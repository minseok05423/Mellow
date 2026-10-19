from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
H = {"X-Mellow-User": "test-user-1"}


def test_check_refutes_with_counterexample_message():
    r = client.post("/check", headers=H, json={"current": "a^2+b^2", "context": ["(a+b)^2"], "confirmed": True})
    assert r.status_code == 200
    d = r.json()
    assert d["verdict"] == "refuted" and d["counterexample"] == {"a": "1", "b": "1"}
    assert "a = 1, b = 1" in d["message"]


def test_check_requires_confirmed_reading_and_user():
    assert client.post("/check", headers=H, json={"current": "x", "context": [], "confirmed": False}).status_code == 422
    assert client.post("/check", json={"current": "x", "context": [], "confirmed": True}).status_code == 400


def test_unverified_gets_a_question_not_a_verdict():
    d = client.post("/check", headers=H, json={"current": r"x \in \mathbb{Z}", "context": ["2q^2=p^2"],
                                               "confirmed": True}).json()
    assert d["verdict"] == "unverified" and d["message"].endswith("?")


def test_a_statement_in_words_is_noted_not_questioned():
    stmt = r"\text{Let } f:[0,1]\to[0,1] \text{ be continuous. Show that there exists } c \text{ s.t. } f(c)=c"
    d = client.post("/check", headers=H, json={"current": stmt, "context": [], "confirmed": True}).json()
    assert (d["verdict"], d["reason"]) == ("noted", "prose")


def test_connector_words_still_get_checked():
    d = client.post("/check", headers=H, json={"current": r"\text{so } a^2+2ab+b^2", "context": ["(a+b)^2"],
                                               "confirmed": True}).json()
    assert d["verdict"] == "verified"


def test_health_reports_configuration():
    d = client.get("/health").json()
    assert d["ok"] is True and set(d) == {"ok", "recognizer", "reader_llm", "reasoning_llm"}


def test_prose_lines_are_never_the_line_checked_against():
    ctx = ["(a+b)^2", r"\text{there exists } c \text{ s.t. } c=1"]
    d = client.post("/check", headers=H, json={"current": "a^2+b^2", "context": ctx, "confirmed": True}).json()
    assert d["verdict"] == "refuted" and d["compared_with"] == "(a+b)^2"


def test_words_read_letter_by_letter_are_noted_never_refuted():
    # Real MyScript readings of "continuous. show" and "that ∃c∈[0,1] s.t." (it reads words as letters).
    lines = [r"C o n t i n u o u s . s h o w", r"t h a t \exists c \in \left[ 0 , 1 \right] s . t ."]
    for i, line in enumerate(lines):
        d = client.post("/check", headers=H, json={"current": line, "context": lines[:i], "confirmed": True}).json()
        assert d["verdict"] == "noted"
    d = client.post("/check", headers=H, json={"current": r"f \left( c \right) = c", "context": lines,
                                               "confirmed": True}).json()
    assert (d["verdict"], d["reason"]) == ("unverified", "no_previous_line")
