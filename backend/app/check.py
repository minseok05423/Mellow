"""/check: SymPy decides; the student sees a verdict only when SymPy proved it (SPEC §4).

Algebra goes to SymPy. Proof steps in words go to the reasoning model. By default it may only ask a
question (SPEC §4); a student who chose "Answers" gets its direct answer, labelled unverified.
shortcut: feedback on SymPy's `unverified` algebra comes from templates. Upgrade path: the reasoning
model words those questions too once /hint lands in P3.
"""
import hashlib
import re
from typing import Literal

from pydantic import BaseModel, Field

from . import config, llm
from .verify import check_step, is_prose


class CheckRequest(BaseModel):
    current: str = Field(min_length=1, max_length=2000)  # the student's confirmed reading of the selected line
    context: list[str] = Field(default_factory=list, max_length=8)  # confirmed lines above, oldest first
    # Confirm-before-judge: the client must only send readings the student confirmed.
    confirmed: Literal[True]
    reasoning_model: str | None = None  # the student's choice (Models menu); the route validates it
    # The student's choice for word lines: a guiding question, or a direct (unverified) answer.
    mode: Literal["hint", "answer"] = "hint"


class CheckResponse(BaseModel):
    verdict: Literal["verified", "refuted", "unverified", "noted", "answer"]
    reason: str
    message: str
    counterexample: dict[str, str] = {}
    values: dict[str, str] = {}
    compared_with: str | None = None


QUESTIONS = {
    "parse_failed": "I can't check this one algebraically. Does it follow from the line above? Which rule gets you there?",
    "mixed_kinds": "One of these lines is an equation and the other isn't. What exactly are you claiming is equal to what?",
    "no_previous_line": "There's no calculation above to check this against. What is this line building on?",
    "trivial_after": "This line is always true on its own. What did you want it to show?",
}
DEFAULT_QUESTION = "I couldn't settle this algebraically. Can you justify the step from the line above?"


PROOF_SYSTEM = """You tutor a first-year university proofs course. A student selected part of their
handwritten work in a notebook and asked you to check it. Their lines are LaTeX they have confirmed.

Decide what the selection is: the problem statement they are about to prove, or steps of their proof.

Rules:
- Never say whether the work is correct, wrong or complete. Never give the answer, the next step or
  a proof. An algebra checker gives verdicts; you only ask.
- Don't introduce a construction, function or theorem the student hasn't written; let them find it.
- Reply with ONE short question (at most two sentences, under 60 words) about the most important thing:
  - a problem statement: what the goal says precisely, or what kind of argument could reach it;
  - proof steps: the weakest justification, a hidden assumption, a missing case, or a quantifier.
  If the steps look solid, ask the student to name the justification for the key step.
- Write math as $...$ (LaTeX)."""

ANSWER_SYSTEM = """You tutor a first-year university proofs course. A student selected part of their
handwritten work in a notebook and asked for a direct answer. Their lines are LaTeX they have confirmed.

Decide what the selection is: a problem statement, or steps of their proof.
- A problem statement: give a concise, complete proof (under 150 words).
- Proof steps: say plainly in the first sentence whether the reasoning holds. If it doesn't, name the
  exact error or gap and show how to fix it. If it does, name the key justification. Under 120 words.
Judge only what is written. A step whose justification isn't written (a theorem not named, or its
hypotheses not checked) does not hold yet: call it a gap and say what is missing.
Write math as $...$ (LaTeX)."""

PROMPT_ID = {mode: hashlib.sha256(p.encode()).hexdigest()[:12]  # a prompt edit never serves old answers
             for mode, p in (("hint", PROOF_SYSTEM), ("answer", ANSWER_SYSTEM))}
VERDICT_WORDS = re.compile(r"\b(correct|incorrect|wrong|well done|good job|great job)\b", re.I)


class ProofFeedback(BaseModel):
    kind: Literal["statement", "steps"]
    question: str


def _proof_check(f: ProofFeedback) -> list[str]:
    errors = []
    if "?" not in f.question:
        errors.append("question must be a question (contain '?')")
    if len(f.question) > 400:
        errors.append("question must be under 400 characters")
    if VERDICT_WORDS.search(f.question):
        errors.append("no verdicts: don't say correct, wrong or well done; only ask")
    return errors


class ProofAnswer(BaseModel):
    kind: Literal["statement", "steps"]
    answer: str


def _answer_check(a: ProofAnswer) -> list[str]:
    return [] if 0 < len(a.answer) <= 1500 else ["answer must be 1-1500 characters"]


def proof_feedback(req: CheckRequest, user: str, model: str) -> CheckResponse:
    above = "\n".join(req.context) or "(none)"
    prompt = f"Confirmed lines above the selection, oldest first:\n{above}\n\nSelected:\n{req.current}"
    hint = req.mode == "hint"
    out = llm.structured_call(
        endpoint="check", user=user, cost_class="reason", model=model,
        system=PROOF_SYSTEM if hint else ANSWER_SYSTEM, content=[{"type": "text", "text": prompt}],
        schema=ProofFeedback if hint else ProofAnswer, semantic_check=_proof_check if hint else _answer_check,
        cache_input={"current": req.current, "context": req.context, "prompt": PROMPT_ID[req.mode]},
        # Answers are stated as fact to the student, so the model thinks harder before giving one.
        effort="medium" if hint else "high", max_tokens=8192,
    )
    if hint:
        return CheckResponse(verdict="unverified", reason=f"proof_{out.kind}", message=out.question)
    # Not a SymPy verdict: the card labels it as the AI's unverified answer (DECISIONS #18).
    return CheckResponse(verdict="answer", reason=f"proof_{out.kind}", message=out.answer)


NOTED = "This is written in words, so there's nothing to calculate. I've noted it; the lines below can build on it."
def _assignment(env: dict[str, str]) -> str:
    return ", ".join(f"{k} = {v}" for k, v in env.items())


def check(req: CheckRequest, user: str) -> CheckResponse:
    if is_prose(req.current):
        # Words ("Let f be continuous...", "hence p is even") aren't algebra SymPy can judge.
        model = req.reasoning_model or config.REASONING_MODEL
        if config.llm_configured(model):
            return proof_feedback(req, user, model)
        return CheckResponse(verdict="noted", reason="prose", message=NOTED)  # no reasoning model available
    r = check_step(req.current, req.context)
    if r.verdict == "verified":
        msg = "This follows from the line above."
    elif r.verdict == "refuted" and r.reason == "rewrite_differs":
        msg = (f"Try ${_assignment(r.counterexample)}$: the line above gives ${r.values['before']}$, "
               f"but yours gives ${r.values['after']}$.")
    elif r.verdict == "refuted":
        msg = f"${_assignment(r.counterexample)}$ satisfies ${r.compared_with}$, but not your line."
    else:
        msg = QUESTIONS.get(r.reason, DEFAULT_QUESTION)
    return CheckResponse(verdict=r.verdict, reason=r.reason, message=msg, counterexample=r.counterexample,
                         values=r.values, compared_with=r.compared_with)
