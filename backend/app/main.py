"""Mellow backend. Holds every API key; the browser never calls an LLM or recognition vendor directly."""
import hmac
import re
import time
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import config, lean, leancheck, llm, recognize, store

app = FastAPI(title="Mellow")


@app.middleware("http")
async def access_code(request: Request, call_next):
    # shortcut: one shared code for invited testers. Upgrade path: real accounts in P4 (SPEC §6).
    key = request.headers.get("x-mellow-key", "")
    if config.ACCESS_CODE and request.url.path != "/health" and not hmac.compare_digest(key, config.ACCESS_CODE):
        return JSONResponse({"detail": "This link is missing its access code. Open the link you were sent."}, 401)
    return await call_next(request)


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
        "lean_llm": config.LEAN_MODEL if config.llm_configured(config.LEAN_MODEL) else None,
        "lean": lean.ready(),
    }


@app.post("/recognize", response_model=recognize.Reading)
def recognize_route(req: recognize.RecognizeRequest, x_mellow_user: str | None = Header(None)):
    user = user_id(x_mellow_user)
    try:
        return recognize.recognize(req, user)
    except llm.QuotaExceeded as e:
        raise HTTPException(429, str(e))
    except llm.LLMError as e:
        raise HTTPException(502, str(e))


def _lean_available():
    if not config.llm_configured(config.LEAN_MODEL):
        raise HTTPException(503, f"no key for the Lean model ({config.LEAN_MODEL})")
    if not lean.ready():
        raise HTTPException(503, f"Lean isn't set up at {lean.LEAN_DIR} (see README: Lean)")


class StatementRequest(BaseModel):
    problem: str = Field(min_length=1, max_length=2000)
    confirmed: Literal[True]  # confirm-before-judge: the problem's reading is the student's


class StatementResponse(BaseModel):
    statement: str
    in_words: str


@app.post("/lean/statement", response_model=StatementResponse)
def statement_route(req: StatementRequest, x_mellow_user: str | None = Header(None)):
    """The problem as a Lean statement, and what that statement says in words, for the student to confirm."""
    user = user_id(x_mellow_user)
    _lean_available()
    try:
        return leancheck.state(req.problem, user)
    except llm.QuotaExceeded as e:
        raise HTTPException(429, str(e))
    except llm.LLMError as e:
        raise HTTPException(502, f"Couldn't state this problem in Lean: {e}")


class LeanCheckRequest(BaseModel):
    statement: str = Field(min_length=1, max_length=2000)
    lines: list[str] = Field(min_length=1, max_length=30)
    # Confirm-before-judge: the readings, and the statement (via its words), are confirmed by the student.
    confirmed: Literal[True]


class LineVerdict(BaseModel):
    verdict: Literal["proved", "skipped step", "wrong reason", "not proved", "unchecked"]
    message: str
    lean: list[str]  # what the line became in Lean


class LeanCheckResponse(BaseModel):
    lines: list[LineVerdict]
    complete: bool
    message: str
    seconds: int


@app.post("/lean/check", response_model=LeanCheckResponse)
def lean_check_route(req: LeanCheckRequest, x_mellow_user: str | None = Header(None)):
    """Check the confirmed lines of a proof against the confirmed statement, line by line, with Lean."""
    user = user_id(x_mellow_user)
    _lean_available()
    lines = [s.strip()[:500] for s in req.lines]
    # The statement goes into a Lean file: the same filter as model-written Lean.
    # A statement is binders and a type: `:=` or a new line could smuggle in a proof of its own.
    if lean.FORBIDDEN.search(req.statement) or leancheck.SPOOF.search(req.statement) or re.search(r":=|\n", req.statement):
        raise HTTPException(400, "the statement contains something Lean isn't allowed to run")
    key = llm.cache_key("lean_check", {"statement": req.statement, "lines": lines}, config.LEAN_MODEL)
    if (hit := store.cache_get(key)) is not None:
        return hit
    t = time.time()
    try:
        result = leancheck.check(req.statement, lines, config.LEAN_MODEL, user)
    except llm.QuotaExceeded as e:
        raise HTTPException(429, str(e))
    res = leancheck.summary(result)
    # Logged like LLM calls (each model call inside is logged by structured_call too): the start of the learning log.
    store.log(user=user, endpoint="lean_check", provider="lean", model=config.LEAN_MODEL, cache_key=key,
              request=req.model_dump(), response=res, valid=1, retries=0, cached=0,
              latency_ms=int((time.time() - t) * 1000))
    if result["translated"]:  # a failed translation (say, a timeout) is tried again next time
        store.cache_put(key, res)
    return res
