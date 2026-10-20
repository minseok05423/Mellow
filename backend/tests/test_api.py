from fastapi.testclient import TestClient

from app import config, lean, leancheck, store
from app.main import app

client = TestClient(app)
H = {"X-Mellow-User": "test-user-1"}
STATEMENT = "(a b : ℤ) (ha : Even a) (hb : Even b) : Even (a + b)"
BODY = {"statement": STATEMENT, "lines": ["a = 2k", "b = 2m", "a + b = 2(k + m)"], "confirmed": True}


def lean_on(monkeypatch, result=None):
    """A configured Lean model and Mathlib, with `leancheck.check` faked; returns the list of calls it got."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(lean, "ready", lambda: True)
    calls = []
    result = result or {"translated": True, "lines": ["proved", "skipped step", "not proved"], "unfinished": False,
                        "items": [{"line": 2, "text": "a + b = 2 * (k + m)"}], "seconds": 12}
    monkeypatch.setattr(leancheck, "check", lambda *a: calls.append(a) or result)
    return calls


def test_health_reports_configuration():
    d = client.get("/health").json()
    assert d["ok"] is True and set(d) == {"ok", "recognizer", "reader_llm", "lean_llm", "lean"}


def test_access_code_guards_everything_but_health(monkeypatch):
    monkeypatch.setattr(config, "ACCESS_CODE", "s3cret")
    assert client.get("/health").status_code == 200
    assert client.post("/lean/check", headers=H, json=BODY).status_code == 401
    assert client.post("/lean/check", headers={**H, "X-Mellow-Key": "wrong"}, json=BODY).status_code == 401
    assert client.post("/lean/check", headers={**H, "X-Mellow-Key": "s3cret"}, json=BODY).status_code == 503


def test_lean_check_requires_a_user_and_confirmed_readings():
    assert client.post("/lean/check", headers=H, json={**BODY, "confirmed": False}).status_code == 422
    assert client.post("/lean/check", json=BODY).status_code == 400


def test_lean_check_without_a_key_or_mathlib_says_so():
    assert client.post("/lean/check", headers=H, json=BODY).status_code == 503


def test_lean_check_gives_each_line_a_verdict_and_caches_it(monkeypatch):
    calls = lean_on(monkeypatch)
    d = client.post("/lean/check", headers=H, json=BODY).json()
    assert [x["verdict"] for x in d["lines"]] == ["proved", "skipped step", "not proved"]
    assert d["lines"][2]["lean"] == ["a + b = 2 * (k + m)"] and "Check it, or add the step" in d["lines"][2]["message"]
    assert d["complete"] is False and d["message"] == "Look again at line 3."
    assert calls[0][:3] == (STATEMENT, BODY["lines"], config.LEAN_MODEL)
    client.post("/lean/check", headers=H, json=BODY)
    assert len(calls) == 1  # the second check came from the cache


def test_a_failed_translation_is_not_cached(monkeypatch):
    calls = lean_on(monkeypatch, {"translated": False, "lines": ["unchecked"] * 3, "seconds": 3})
    assert "couldn't translate" in client.post("/lean/check", headers=H, json=BODY).json()["message"]
    client.post("/lean/check", headers=H, json=BODY)
    assert len(calls) == 2


def test_a_statement_that_would_run_code_is_refused(monkeypatch):
    lean_on(monkeypatch)
    for bad in [STATEMENT + " := by sorry", '(x : ℕ) : x = x := by trace "mellow 3"', "(x : ℕ) : x = x := rfl",
                STATEMENT + "\ntheorem other : True"]:
        assert client.post("/lean/check", headers=H, json={**BODY, "statement": bad}).status_code == 400


def test_lean_statement_returns_lean_and_words(monkeypatch):
    lean_on(monkeypatch)
    monkeypatch.setattr(leancheck, "state", lambda problem, user: {"statement": STATEMENT, "in_words": f"words: {problem}"})
    d = client.post("/lean/statement", headers=H, json={"problem": "sum of evens", "confirmed": True}).json()
    assert d == {"statement": STATEMENT, "in_words": "words: sum of evens"}


def test_summary_sentences():
    done = {"translated": True, "lines": ["proved", "proved"], "unfinished": False}
    assert leancheck.summary(done)["complete"] is True and leancheck.summary(done)["lines"][0]["message"] == ""
    assert "doesn't reach the conclusion" in leancheck.summary({**done, "unfinished": True})["message"]
    assert leancheck.summary({**done, "lines": ["wrong reason", "unchecked"]})["message"] == "Look again at lines 1, 2."
    # A step the model filled in is a gap: never "complete".
    gap = leancheck.summary({**done, "lines": ["proved", "skipped step"]})
    assert gap["complete"] is False and gap["message"] == "Every line holds, but line 2 skips a step."
    # A false line shows its counterexample; a ✓ after it says it rests on that line.
    r = leancheck.summary({**done, "lines": ["false", "proved"], "counterexamples": {0: {"k": "(1 : ℤ)", "m": "1"}}})
    assert r["lines"][0]["message"] == "False as written: it fails for k = 1, m = 1."
    assert r["lines"][1]["message"] == "It follows from the lines above, but line 1 isn't verified."
    assert r["message"] == "Line 1 is false as written." and r["complete"] is False
    # At 0, Lean's conventions (1/0 = 0) may be the reason: asked, not asserted.
    r = leancheck.summary({**done, "lines": ["false", "proved"], "counterexamples": {0: {"b": "0"}}})
    assert r["lines"][0]["message"] == "False as written when b = 0. Did you mean to rule that out?"
    assert "can't judge whether your steps" in leancheck.summary({**done, "trivial": True})["message"]
    r = leancheck.summary({**done, "lines": ["proved", "not proved"], "contradiction_lines": [1]})
    assert "inside a proof by contradiction" in r["message"]


def test_events_are_logged_and_bounded():
    r = client.post("/event", headers=H, json={"event": "next_step", "check_id": "c1", "data": {"answer": "fix it"}})
    assert r.status_code == 204
    with store.connect() as c:
        assert c.execute("SELECT user, check_id, event, data FROM events").fetchall() == \
            [("test-user-1", "c1", "next_step", '{"answer": "fix it"}')]
    assert client.post("/event", headers=H, json={"event": "anything"}).status_code == 422
    assert client.post("/event", headers=H, json={"event": "feedback", "data": {"text": "x" * 5000}}).status_code == 413
