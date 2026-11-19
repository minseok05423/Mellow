"""Mellow backend. Holds every API key; the browser never calls an LLM or recognition vendor directly."""
import hmac
import json
import re
import time
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import config, graphcheck, lean, llm, recognize, store

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
        "check_llm": config.CHECK_MODEL if config.llm_configured(config.CHECK_MODEL) else None,
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


def _llm_available():
    for model in (config.CHUNK_MODEL, config.GRAPH_MODEL, config.CHECK_MODEL):
        if not config.llm_configured(model):
            raise HTTPException(503, f"no key for the checking model ({model})")


class EventRequest(BaseModel):
    # shortcut: check_id isn't on request_log, so a check's model calls are joined to its events by user and time.
    # Upgrade path: send check_id as a header and log it with every call.
    event: Literal["check_started", "graph_rejected", "check_result", "abandoned", "next_step", "feedback"]
    check_id: str = Field("", max_length=64)
    data: dict = Field(default_factory=dict)


@app.post("/event", status_code=204)
def event_route(req: EventRequest, x_mellow_user: str | None = Header(None)):
    """What a student does around a check, for the friends test: logged, never used to judge anything."""
    user = user_id(x_mellow_user)
    if len(json.dumps(req.data)) > 4000:
        raise HTTPException(413, "event data too large")
    store.event(user, req.check_id, req.event, req.data)


class GraphRequest(BaseModel):
    problem: str = Field(min_length=1, max_length=2000)
    lines: list[str] = Field(min_length=1, max_length=30)
    confirmed: Literal[True]  # confirm-before-judge: the readings are the student's


@app.post("/graph", response_model=graphcheck.Graph)
def graph_route(req: GraphRequest, x_mellow_user: str | None = Header(None)):
    """The proof as a graph of steps and premises, for the student to confirm before anything is judged."""
    user = user_id(x_mellow_user)
    _llm_available()
    try:
        return graphcheck.make_graph(req.problem.strip(), [s.strip()[:500] for s in req.lines], user)
    except llm.QuotaExceeded as e:
        raise HTTPException(429, str(e))
    except llm.LLMError as e:
        raise HTTPException(502, f"Couldn't read the proof's steps: {e}")


class CheckRequest(BaseModel):
    graph: graphcheck.Graph
    confirmed: Literal[True]  # confirm-before-judge: the student confirmed this graph


class CheckResponse(BaseModel):
    nodes: list[graphcheck.NodeVerdict]
    complete: bool
    reaches_goal: bool  # the structure gets to the goal, whatever the steps' verdicts
    message: str
    seconds: int


@app.post("/check", response_model=CheckResponse)
def check_route(req: CheckRequest, x_mellow_user: str | None = Header(None)):
    """Check each step of the confirmed graph on its own: SymPy, then Lean, then three judges."""
    user = user_id(x_mellow_user)
    _llm_available()
    g = req.graph
    if (len(g.nodes) > 60 or len(g.blocks) > 60 or len(g.variables) > 60 or any(len(n.text) > 500 for n in g.nodes)
            or len(g.goal) > 2000):
        raise HTTPException(413, "this proof is too long to check")
    if errs := graphcheck.graph_problems(g, None):
        raise HTTPException(400, "; ".join(errs))
    key = llm.cache_key("check", {"graph": g.model_dump(), "v": graphcheck.RESULT_VERSION}, config.CHECK_MODEL)
    if (hit := store.cache_get(key)) is not None:
        return hit
    t = time.time()
    try:
        res = graphcheck.check(g, user)
    except llm.QuotaExceeded as e:
        raise HTTPException(429, str(e))
    # Logged like LLM calls (each model call inside is logged by structured_call too): the start of the learning log.
    store.log(user=user, endpoint="check", provider="graph", model=config.CHECK_MODEL, cache_key=key,
              request=req.model_dump(), response=res, valid=1, retries=0, cached=0,
              latency_ms=int((time.time() - t) * 1000))
    if not any(n["detail"] == "a judge failed" for n in res["nodes"]):  # a failed call is tried again next time
        store.cache_put(key, res)
    return res
