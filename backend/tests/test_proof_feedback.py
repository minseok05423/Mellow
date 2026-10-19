"""Proof steps in words: the reasoning model asks one question, never gives a verdict."""
from fastapi.testclient import TestClient

from app import config, llm
from app.check import ProofFeedback
from app.main import app

client = TestClient(app)
H = {"X-Mellow-User": "test-user-1"}
STMT = r"\text{Let } f:[0,1]\to[0,1] \text{ be continuous. Show there is } c \text{ with } f(c)=c"


class FakeReasoner:
    name = "fake"

    def __init__(self, *questions):
        self.questions, self.calls = list(questions), []

    def complete(self, *, model, system, messages, schema, effort, max_tokens, on_token=None):
        self.calls.append((model, messages))
        out = ProofFeedback(kind="steps", question=self.questions.pop(0))
        return llm.Completion(text=out.model_dump_json(), parsed=out)


def post(current, context=(), **kw):
    return client.post("/check", headers=H, json={"current": current, "context": list(context), "confirmed": True, **kw})


def test_proof_steps_get_a_question_from_the_reasoning_model(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    fake = FakeReasoner("Why does $g(x) = f(x) - x$ change sign on $[0,1]$?")
    llm.set_provider(fake)
    d = post(r"\text{Let } g(x)=f(x)-x \text{. Then } g(0)\ge 0 \text{ and } g(1)\le 0", [STMT]).json()
    assert (d["verdict"], d["reason"]) == ("unverified", "proof_steps") and d["message"].endswith("?")
    model, messages = fake.calls[0]
    assert model == config.REASONING_MODEL and STMT in messages[0]["content"][0]["text"]


def test_a_verdict_is_rejected_and_retried(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    fake = FakeReasoner("This is correct, well done!", "What makes $g$ continuous?")
    llm.set_provider(fake)
    d = post(r"\text{so } g \text{ has a root}", [STMT]).json()
    assert d["message"] == "What makes $g$ continuous?" and len(fake.calls) == 2


def test_without_a_reasoning_model_prose_is_noted():
    assert post(STMT).json()["verdict"] == "noted"


def test_unknown_reasoning_model_is_rejected():
    assert post(STMT, reasoning_model="gpt-9").status_code == 400


class FakeAnswerer:
    name = "fake"

    def __init__(self):
        self.calls = []

    def complete(self, *, model, system, messages, schema, effort, max_tokens, on_token=None):
        from app.check import ProofAnswer
        self.calls.append((system, effort))
        out = ProofAnswer(kind="steps", answer="It doesn't hold yet: you need $g$ continuous to use the IVT.")
        return llm.Completion(text=out.model_dump_json(), parsed=out)


def test_answer_mode_gives_a_labelled_direct_answer(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    fake = FakeAnswerer()
    llm.set_provider(fake)
    d = post(r"\text{so } g \text{ has a root}", [STMT], mode="answer").json()
    assert d["verdict"] == "answer" and "IVT" in d["message"]
    system, effort = fake.calls[0]
    assert "direct answer" in system and effort == "high"
    # The same lines in hint mode are a different request, not a cached answer.
    llm.set_provider(FakeReasoner("What makes $g$ continuous?"))
    assert post(r"\text{so } g \text{ has a root}", [STMT]).json()["verdict"] == "unverified"
