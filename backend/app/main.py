"""Mellow backend. Holds every API key; the browser never calls an LLM or recognition vendor directly."""
import re
import time

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from . import catalog, config, llm, recognize, store, structure
from .assist import AssistRequest, assist
from .check import CheckRequest, CheckResponse, check

app = FastAPI(title="Mellow")


def user_id(x_mellow_user: str | None) -> str:
    # shortcut: an anonymous id the browser generates. Upgrade path: real accounts in P4 (SPEC §6).
    if not x_mellow_user or not re.fullmatch(r"[\w-]{6,64}", x_mellow_user):
        raise HTTPException(400, "missing or malformed X-Mellow-User header")
    return x_mellow_user


@app.get("/health")
def health():
    return {
        "ok": True,
        "recognizer": config.RECOGNIZER if recognize.recognizer_configured() else None,
        "reader_llm": config.FALLBACK_READER_MODEL if config.llm_configured(config.FALLBACK_READER_MODEL) else None,
        "reasoning_llm": config.REASONING_MODEL if config.llm_configured(config.REASONING_MODEL) else None,
    }


@app.get("/models")
def models():
    return catalog.catalog()


@app.post("/recognize", response_model=recognize.Reading)
def recognize_route(req: recognize.RecognizeRequest, x_mellow_user: str | None = Header(None)):
    user = user_id(x_mellow_user)
    if req.recognizer and req.recognizer != "none" and not recognize.recognizer_configured(req.recognizer):
        raise HTTPException(400, f"recognizer {req.recognizer!r} isn't available")
    if req.reader_model and not catalog.model_available(req.reader_model, vision=True):
        raise HTTPException(400, f"reader model {req.reader_model!r} isn't available (needs a key and image input)")
    try:
        return recognize.recognize(req, user)
    except llm.QuotaExceeded as e:
        raise HTTPException(429, str(e))
    except llm.LLMError as e:
        raise HTTPException(502, str(e))


@app.post("/check", response_model=CheckResponse)
def check_route(req: CheckRequest, x_mellow_user: str | None = Header(None)):
    user = user_id(x_mellow_user)
    if req.reasoning_model and not catalog.model_available(req.reasoning_model):
        raise HTTPException(400, f"reasoning model {req.reasoning_model!r} isn't available")
    t = time.time()
    try:
        res = check(req, user)
    except llm.QuotaExceeded as e:
        raise HTTPException(429, str(e))
    except llm.LLMError as e:
        raise HTTPException(502, str(e))
    # Logged like LLM calls: this is the start of the learning log (SPEC §5).
    by_llm = res.reason.startswith("judge_")  # the LLM call itself is logged by structured_call too
    store.log(user=user, endpoint="check", provider="llm" if by_llm else "sympy",
              model=(req.reasoning_model or config.REASONING_MODEL) if by_llm else "sympy", request=req.model_dump(),
              response=res.model_dump(), valid=1, retries=0, cached=0, latency_ms=int((time.time() - t) * 1000))
    return res


@app.post("/structure", response_model=structure.Structure)
def structure_route(req: structure.StructureRequest, x_mellow_user: str | None = Header(None)):
    user = user_id(x_mellow_user)
    model = req.reader_model or config.FALLBACK_READER_MODEL  # labelling is a cheap model's job
    if not catalog.model_available(model):
        raise HTTPException(503, f"no model available for labelling ({model})")
    try:
        return structure.label(req, user, model)
    except llm.QuotaExceeded as e:
        raise HTTPException(429, str(e))
    except llm.LLMError as e:
        raise HTTPException(502, str(e))


@app.post("/assist", response_model=CheckResponse)
def assist_route(req: AssistRequest, x_mellow_user: str | None = Header(None)):
    """Hint, Answer and Explain (SPEC §4). Every LLM call inside is logged by structured_call."""
    user = user_id(x_mellow_user)
    if req.reasoning_model and not catalog.model_available(req.reasoning_model):
        raise HTTPException(400, f"reasoning model {req.reasoning_model!r} isn't available")
    t = time.time()
    try:
        res = assist(req, user)
    except llm.QuotaExceeded as e:
        raise HTTPException(429, str(e))
    except llm.LLMError as e:
        raise HTTPException(502, str(e))
    store.log(user=user, endpoint="assist", provider="llm", model=req.reasoning_model or config.REASONING_MODEL,
              request=req.model_dump(), response=res.model_dump(), valid=1, retries=0, cached=0,
              latency_ms=int((time.time() - t) * 1000))
    return res


class SceneRequest(BaseModel):
    topic: str


@app.post("/scene")
def scene(req: SceneRequest):
    raise HTTPException(501, "not implemented (P3b)")
