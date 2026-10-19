"""Hybrid handwriting reading (SPEC §4, DECISIONS #8).

1. A stroke recognizer (Mathpix by default, or MyScript) reads the line's raw strokes.
2. If there's no recognizer, its confidence is low, or the line contains prose, a vision LLM re-reads
   an image of the line, given the recognizer's guess and the confirmed lines above.
3. Whatever comes back is a *reading*: the student confirms it before anything is judged.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time

import httpx2 as httpx
from pydantic import BaseModel, Field

from .verify import looks_like_words
from . import config, llm, store


class Stroke(BaseModel):
    t0: float = 0
    pts: list[list[float]]  # [x, y, pressure, ms since stroke start]


class RecognizeRequest(BaseModel):
    strokes: list[Stroke] = Field(min_length=1, max_length=400)
    image: str | None = None  # PNG data URL of the line, for the LLM fallback
    context: list[str] = Field(default_factory=list, max_length=8)  # confirmed lines above, oldest first
    # The student's choices (Models menu). None = server default. Checked against the catalog by the route.
    recognizer: str | None = None
    reader_model: str | None = None


class Reading(BaseModel):
    latex: str
    confidence: float | None = None
    source: str  # "mathpix" | "myscript" | "llm" | "none"
    has_prose: bool = False


# ---------- stroke recognizers ----------

def _mathpix_text_to_latex(text: str) -> str:
    """Mathpix mixed output 'hence \\( p \\) is even' -> 'hence p is even' as LaTeX with \\text{}."""
    parts = re.split(r"\\\(|\\\)|\\\[|\\\]", text)
    out = []
    for i, part in enumerate(parts):
        part = part.strip()
        if part:
            out.append(part if i % 2 else rf"\text{{{part}}}")
    return r"\ ".join(out)


def read_mathpix(strokes: list[Stroke]) -> Reading:
    body = {"strokes": {"strokes": {
        "x": [[p[0] for p in s.pts] for s in strokes],
        "y": [[p[1] for p in s.pts] for s in strokes],
    }}}
    r = httpx.post("https://api.mathpix.com/v3/strokes", json=body, timeout=config.VENDOR_TIMEOUT_S,
                   headers={"app_id": config.MATHPIX_APP_ID, "app_key": config.MATHPIX_APP_KEY})
    r.raise_for_status()
    d = r.json()
    if "error" in d:
        raise RuntimeError(f"mathpix: {d['error']}")
    if d.get("latex_styled"):
        return Reading(latex=d["latex_styled"], confidence=d.get("confidence"), source="mathpix")
    text = d.get("text", "")
    return Reading(latex=_mathpix_text_to_latex(text), confidence=d.get("confidence"), source="mathpix",
                   has_prose=bool(re.sub(r"\\\(.*?\\\)", "", text).strip()))


def read_myscript(strokes: list[Stroke]) -> Reading:
    # REST, server-side: the keys never reach the browser. Request shape follows MyScript's own iinkTS
    # client, HTTPClientV2 (V1's /iink/batch is deprecated there).
    # shortcut: not yet verified against the live API. Upgrade path: confirm with real keys in the
    # bake-off. Math mode only, so prose lines rely on the LLM fallback.
    body = {
        "configuration": {
            "lang": "en_US",
            # MyScript's math recognizer *solves* equations by default. Mellow must never hand out answers.
            "math": {"solver": {"enable": False}},
            "export": {},
        },
        "scaleX": 0.265, "scaleY": 0.265,  # mm per pixel at 96 dpi, as iinkTS sends
        "contentType": "Math",
        "strokes": [{
            "id": f"s{i}", "pointerType": "pen",
            "x": [p[0] for p in s.pts], "y": [p[1] for p in s.pts],
            "t": [int(s.t0 + p[3]) for p in s.pts], "p": [p[2] for p in s.pts],
        } for i, s in enumerate(strokes)],
    }
    payload = json.dumps(body)
    sig = hmac.new((config.MYSCRIPT_APP_KEY + config.MYSCRIPT_HMAC_KEY).encode(), payload.encode(),
                   hashlib.sha512).hexdigest()
    r = httpx.post("https://cloud.myscript.com/api/v4.0/iink/recognize", content=payload,
                   timeout=config.VENDOR_TIMEOUT_S,
                   headers={"Accept": "application/x-latex", "Content-Type": "application/json",
                            "applicationKey": config.MYSCRIPT_APP_KEY, "hmac": sig})
    r.raise_for_status()
    return Reading(latex=r.text.strip(), confidence=None, source="myscript")


RECOGNIZERS = {"mathpix": read_mathpix, "myscript": read_myscript}


def recognizer_configured(name: str | None = None) -> bool:
    name = name or config.RECOGNIZER
    if name == "mathpix":
        return bool(config.MATHPIX_APP_ID and config.MATHPIX_APP_KEY)
    if name == "myscript":
        return bool(config.MYSCRIPT_APP_KEY and config.MYSCRIPT_HMAC_KEY)
    return False


# ---------- LLM fallback ----------

FALLBACK_SYSTEM = """You transcribe ONE line of a university student's handwritten math notebook into LaTeX.

Rules:
- Transcribe exactly what is written, including mistakes. Never correct, simplify or complete the math;
  a wrong step must stay wrong, because the student's reasoning is what gets checked.
- Write prose as \\text{...}, e.g. \\text{hence } p \\text{ is even}.
- Use the confirmed lines above only to settle ambiguous symbols (q vs 9, x vs \\times, 1 vs l).
- A stroke recognizer's guess may be given; it can be wrong. Trust the image.
- confidence: your probability (0-1) that the transcription is exactly right.
- uncertain: the symbols you were unsure about, as written in your LaTeX."""


class FallbackReading(BaseModel):
    latex: str
    confidence: float
    uncertain: list[str]
    has_prose: bool


def _semantic_check(r: FallbackReading) -> list[str]:
    errors = []
    if r.latex.count("{") != r.latex.count("}"):
        errors.append("unbalanced braces in latex")
    if not 0 <= r.confidence <= 1:
        errors.append("confidence must be between 0 and 1")
    return errors


def read_with_llm(req: RecognizeRequest, user: str, guess: Reading | None, model: str) -> Reading:
    m = re.match(r"data:(image/\w+);base64,(.+)", req.image or "", re.S)
    if not m:
        raise ValueError("the LLM fallback needs a PNG data URL image of the line")
    media_type, data = m.groups()
    text = "Confirmed lines above (oldest first):\n" + ("\n".join(req.context) or "(none)")
    if guess and guess.latex:
        text += f"\n\nStroke recognizer's guess ({guess.source}, confidence {guess.confidence}): {guess.latex}"
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
        {"type": "text", "text": text + "\n\nTranscribe the line in the image."},
    ]
    image_hash = hashlib.sha256(data.encode()).hexdigest()
    out = llm.structured_call(
        endpoint="recognize", user=user, cost_class="read_llm", model=model,
        system=FALLBACK_SYSTEM, content=content, schema=FallbackReading, semantic_check=_semantic_check,
        cache_input={"image": image_hash, "context": req.context, "guess": guess.latex if guess else None},
        effort="low", max_tokens=2048,
    )
    return Reading(latex=out.latex, confidence=out.confidence, source="llm", has_prose=out.has_prose)


# ---------- the hybrid ----------

def recognize(req: RecognizeRequest, user: str) -> Reading:
    rec = req.recognizer or config.RECOGNIZER
    reader = req.reader_model or config.FALLBACK_READER_MODEL
    guess = None
    if recognizer_configured(rec):
        key = llm.cache_key("strokes", [s.pts for s in req.strokes], rec)
        if (hit := store.cache_get(key)) is not None:
            guess = Reading.model_validate(hit)
        elif not store.take_quota(user, "stroke"):
            raise llm.QuotaExceeded("Daily limit reached for handwriting reading")
        else:
            t = time.time()
            try:
                guess = RECOGNIZERS[rec](req.strokes)
                store.cache_put(key, guess.model_dump())
                err = None
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
            store.log(user=user, endpoint="recognize", provider=rec, model=rec,
                      cache_key=key, request={"strokes": len(req.strokes)},
                      response=guess.model_dump() if guess else None, valid=int(err is None), error=err,
                      retries=0, cached=0, latency_ms=int((time.time() - t) * 1000))

    good_enough = guess and guess.latex and not guess.has_prose and not looks_like_words(guess.latex) and (
        guess.confidence is None or guess.confidence >= config.CONFIDENCE_MIN)
    if good_enough:
        return guess
    if req.image and config.llm_configured(reader):
        return read_with_llm(req, user, guess, reader)
    # No fallback available: return whatever we have. The confirm chip lets the student type it.
    return guess or Reading(latex="", source="none")
