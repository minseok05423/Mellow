"""Check (AI judgement on words), Hint / Answer / Explain on a solved and SymPy-checked proof, and page labels."""
import pytest
from fastapi.testclient import TestClient

from app import config, llm
from app.assist import Proof, ProofStep, Reply
from app.check import StepJudgement
from app.main import app
from app.structure import LineLabel, Structure

client = TestClient(app)
H = {"X-Mellow-User": "test-user-1"}
PROBLEM = r"\text{Show that } (a+b)^2 = a^2+2ab+b^2"
STEPS = r"\text{Expand: } (a+b)(a+b)"


class Fake:
    """Answers each call from a queue per output schema, and records what it was sent."""
    name = "fake"

    def __init__(self, **outputs):
        self.outputs, self.calls = {k: list(v) for k, v in outputs.items()}, []

    def complete(self, *, model, system, messages, schema, effort, max_tokens, on_token=None):
        self.calls.append((schema.__name__, system, messages))
        out = self.outputs[schema.__name__].pop(0)
        # A fresh copy: the server sets `checked` on the proof it receives.
        return llm.Completion(text=out.model_dump_json(), parsed=schema.model_validate(out.model_dump()))

    def sent(self, schema):
        return [m[-1]["content"] if isinstance(m[-1]["content"], str) else m[0]["content"][0]["text"]
                for name, _, m in self.calls if name == schema]


def proof(*claims):
    """A proof whose steps state these claims; the first is a starting point, the rest are derived from it."""
    return Proof(steps=[ProofStep(text=f"so ${c}$", claim=c, algebra="derived" if i else "none", why="algebra")
                        for i, c in enumerate(claims)])


GOOD = proof("(a+b)^2", "(a+b)(a+b)", "a^2+2ab+b^2")


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")


def assist(action, kind="steps", selection=STEPS, **kw):
    return client.post("/assist", headers=H, json={"action": action, "kind": kind, "selection": selection,
                                                    "confirmed": True, **kw})


# ---- Check: words get the AI's holds / gap / error, labelled ----

def test_check_judges_steps_in_words_with_the_problem_as_context(key):
    fake = Fake(StepJudgement=[StepJudgement(verdict="gap", reason="Which rule gives the expansion?")])
    llm.set_provider(fake)
    d = client.post("/check", headers=H, json={"current": STEPS, "context": [], "confirmed": True,
                                               "problem": PROBLEM}).json()
    assert (d["verdict"], d["reason"]) == ("ai_check", "judge_gap") and d["message"].startswith("Gap:")
    assert PROBLEM in fake.sent("StepJudgement")[0]


def test_check_without_a_reasoning_model_notes_words():
    assert client.post("/check", headers=H, json={"current": STEPS, "context": [], "confirmed": True}).json()["verdict"] == "noted"


def test_unknown_reasoning_model_is_rejected():
    assert assist("hint", reasoning_model="gpt-9").status_code == 400
    assert client.post("/check", headers=H, json={"current": STEPS, "context": [], "confirmed": True,
                                                  "reasoning_model": "gpt-9"}).status_code == 400


# ---- Solve once, check with SymPy, reuse ----

def test_hint_is_built_on_a_checked_proof_that_stays_on_the_server(key):
    fake = Fake(Proof=[GOOD], Reply=[Reply(text="What do you get when you multiply out $(a+b)(a+b)$?"),
                                     Reply(text="Which terms can you collect?")])
    llm.set_provider(fake)
    d = assist("hint", problem=PROBLEM).json()
    assert d["verdict"] == "hint" and d["message"].endswith("?") and "a^2+2ab+b^2" not in d["message"]
    assert "Reference proof" in fake.sent("Reply")[0] and "a^2+2ab+b^2" in fake.sent("Reply")[0]
    # A later hint on the same problem reuses the stored proof: no second solve.
    assist("hint", selection=r"\text{so } a^2+ab+ba+b^2", problem=PROBLEM)
    assert [c[0] for c in fake.calls] == ["Proof", "Reply", "Reply"]


def test_a_wrong_algebra_claim_is_caught_by_sympy_and_retried(key):
    fake = Fake(Proof=[proof("(a+b)^2", "a^2+b^2"), GOOD])
    llm.set_provider(fake)
    d = assist("answer", kind="problem", selection=PROBLEM).json()
    retry = fake.calls[1][2][-1]["content"]
    assert "does not follow" in retry and "a = 1, b = 1" in retry  # the counterexample goes back to the model
    assert d["verdict"] == "answer" and "a^2+2ab+b^2$ ✓" in d["message"] and "checked by SymPy" in d["message"]


def test_new_definitions_are_not_algebra_and_never_refuted(key):
    p = Proof(steps=[ProofStep(text="$p^2 = 2q^2$", claim="p^2=2q^2", why="squaring"),
                     ProofStep(text="$p$ is even, so $p = 2k$", claim="p=2k", why="parity"),
                     ProofStep(text="$4k^2 = 2q^2$", claim="4k^2=2q^2", algebra="derived", why="substitute")])
    llm.set_provider(Fake(Proof=[p]))
    d = assist("explain", kind="problem", selection=r"\text{Prove } \sqrt{2} \text{ is irrational}").json()
    assert d["verdict"] == "explain" and "$p = 2k$\n" in d["message"] and "4k^2 = 2q^2$ ✓" in d["message"]
    assert "Why: substitute" in d["message"]


def test_identities_are_checked_on_their_own_and_a_false_one_goes_back(key):
    def ident(*claims):
        return Proof(steps=[ProofStep(text=f"${c}$", claim=c, algebra="identity", why="expand") for c in claims])
    fake = Fake(Proof=[ident("(a+b)^2=a^2+2ab+b^2", "(a+b)^3=a^3+b^3"),
                       ident("(a+b)^2=a^2+2ab+b^2", "(a+b)^3=a^3+3a^2b+3ab^2+b^3")])
    llm.set_provider(fake)
    d = assist("answer", kind="problem", selection=r"\text{Expand } (a+b)^3").json()
    assert "isn't true for all values" in fake.calls[1][2][-1]["content"]
    assert d["message"].count("✓") == 3  # both steps, plus the footnote


def test_without_a_checked_proof_hint_still_asks_and_answer_says_so(key):
    bad = proof("(a+b)^2", "a^2+b^2")
    fake = Fake(Proof=[bad, bad, bad, bad], Reply=[Reply(text="What does the goal say precisely?")])
    llm.set_provider(fake)
    d = assist("hint", kind="problem", selection=PROBLEM).json()
    assert d["verdict"] == "hint" and "Reference proof" not in fake.sent("Reply")[0]
    d = assist("answer", kind="problem", selection=PROBLEM).json()
    assert (d["verdict"], d["reason"]) == ("noted", "no_checked_answer")


def test_a_hint_with_a_verdict_is_rejected_and_retried(key):
    fake = Fake(Proof=[GOOD], Reply=[Reply(text="This is correct, well done!"), Reply(text="What comes next?")])
    llm.set_provider(fake)
    assert assist("hint", problem=PROBLEM).json()["message"] == "What comes next?"


def test_answers_on_steps_and_concepts_are_labelled_and_concepts_need_no_proof(key):
    fake = Fake(Reply=[Reply(text="Uniformly continuous means one $\\delta$ works for every $x$.")])
    llm.set_provider(fake)
    d = assist("answer", kind="concept", selection=r"\text{uniform continuity}").json()
    assert d["verdict"] == "answer" and d["message"].endswith("AI, not machine-checked.")
    assert [c[0] for c in fake.calls] == ["Reply"]


def test_without_a_reasoning_model_actions_say_so():
    assert assist("hint").json()["reason"] == "no_model"


# ---- Page labels ----

def test_structure_labels_every_line_and_retries_a_bad_labelling(key):
    good = Structure(lines=[LineLabel(role="problem", segment=0), LineLabel(role="algebra", segment=0)],
                     segments=["Problem: expand (a+b)^2"])
    fake = Fake(Structure=[Structure(lines=good.lines[:1], segments=good.segments), good])
    llm.set_provider(fake)
    d = client.post("/structure", headers=H, json={"lines": [PROBLEM, "(a+b)(a+b)"]}).json()
    assert [x["role"] for x in d["lines"]] == ["problem", "algebra"] and len(fake.calls) == 2


def test_structure_needs_a_model():
    assert client.post("/structure", headers=H, json={"lines": [PROBLEM]}).status_code == 503
