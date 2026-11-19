from fastapi.testclient import TestClient

from app import config, graphcheck, store
from app.main import app

client = TestClient(app)
H = {"X-Mellow-User": "test-user-1"}
GRAPH = {"goal": "$a + b$ is even", "goal_order": [], "reaches_goal": True,
         "blocks": [{"id": 0, "kind": "proof", "parent": None}],
         "nodes": [{"id": 0, "kind": "given", "line": None, "text": "$a$, $b$ even", "premises": [], "block": 0},
                   {"id": 1, "kind": "claim", "line": 0, "text": "$a + b$ is even", "premises": [0], "block": 0}],
         "variables": [{"name": "a", "kind": "given", "introduced_at": None, "depends_on": []}]}
BODY = {"graph": GRAPH, "confirmed": True}
RESULT = {"nodes": [{"node": 1, "status": "ok", "tier": "judged", "message": "", "detail": "3 of 3 judges accept", "comments": []}],
          "complete": True, "reaches_goal": True, "message": "Every step checks out: 1 judged.", "seconds": 3}


def model_on(monkeypatch, result=RESULT):
    """A configured model with `graphcheck.check` faked; returns the list of calls it got."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls = []
    monkeypatch.setattr(graphcheck, "check", lambda *a: calls.append(a) or result)
    return calls


def test_health_reports_configuration():
    d = client.get("/health").json()
    assert d["ok"] is True and set(d) == {"ok", "recognizer", "reader_llm", "check_llm", "lean"}


def test_access_code_guards_everything_but_health(monkeypatch):
    monkeypatch.setattr(config, "ACCESS_CODE", "s3cret")
    assert client.get("/health").status_code == 200
    assert client.post("/check", headers=H, json=BODY).status_code == 401
    assert client.post("/check", headers={**H, "X-Mellow-Key": "wrong"}, json=BODY).status_code == 401
    assert client.post("/check", headers={**H, "X-Mellow-Key": "s3cret"}, json=BODY).status_code == 503


def test_check_requires_a_user_and_a_confirmed_graph():
    assert client.post("/check", headers=H, json={**BODY, "confirmed": False}).status_code == 422
    assert client.post("/check", json=BODY).status_code == 400


def test_check_without_a_model_says_so():
    assert client.post("/check", headers=H, json=BODY).status_code == 503


def test_an_agent_queue_counts_as_a_model(monkeypatch):
    monkeypatch.setattr(config, "AGENT_QUEUE", "/nowhere")
    assert config.llm_configured(config.CHECK_MODEL)


def test_check_gives_each_step_a_verdict_and_caches_it(monkeypatch):
    calls = model_on(monkeypatch)
    assert client.post("/check", headers=H, json=BODY).json() == RESULT
    assert calls[0][0] == graphcheck.Graph.model_validate(GRAPH)
    client.post("/check", headers=H, json=BODY)
    assert len(calls) == 1  # the second check came from the cache


def test_a_failed_judge_is_not_cached(monkeypatch):
    failed = {**RESULT, "nodes": [{**RESULT["nodes"][0], "status": "unsure", "detail": "a judge failed"}]}
    calls = model_on(monkeypatch, failed)
    client.post("/check", headers=H, json=BODY)
    client.post("/check", headers=H, json=BODY)
    assert len(calls) == 2


def test_a_malformed_or_huge_graph_is_refused(monkeypatch):
    model_on(monkeypatch)
    forward = {**GRAPH, "nodes": [GRAPH["nodes"][0], {**GRAPH["nodes"][1], "premises": [1]}]}
    assert client.post("/check", headers=H, json={**BODY, "graph": forward}).status_code == 400
    loop = {**GRAPH, "blocks": GRAPH["blocks"] + [{"id": 1, "kind": "let", "parent": 1}]}  # a block inside itself
    assert client.post("/check", headers=H, json={**BODY, "graph": loop}).status_code == 400
    huge = {**GRAPH, "nodes": [GRAPH["nodes"][0], {**GRAPH["nodes"][1], "text": "x" * 501}]}
    assert client.post("/check", headers=H, json={**BODY, "graph": huge}).status_code == 413


def test_graph_returns_the_models_graph(monkeypatch):
    model_on(monkeypatch)
    seen = []
    monkeypatch.setattr(graphcheck, "make_graph", lambda problem, lines, user: seen.append((problem, lines)) or GRAPH)
    d = client.post("/graph", headers=H, json={"problem": " sum of evens ", "lines": ["a+b even "], "confirmed": True})
    assert d.json()["goal"] == GRAPH["goal"] and seen == [("sum of evens", ["a+b even"])]


def test_events_are_logged_and_bounded():
    r = client.post("/event", headers=H, json={"event": "next_step", "check_id": "c1", "data": {"answer": "fix it"}})
    assert r.status_code == 204
    with store.connect() as c:
        assert c.execute("SELECT user, check_id, event, data FROM events").fetchall() == \
            [("test-user-1", "c1", "next_step", '{"answer": "fix it"}')]
    assert client.post("/event", headers=H, json={"event": "anything"}).status_code == 422
    assert client.post("/event", headers=H, json={"event": "feedback", "data": {"text": "x" * 5000}}).status_code == 413
