"""/check: is what the student wrote sound? (SPEC §4, DECISIONS #19)

Algebra goes to SymPy, whose verdicts are proven. Proof steps in words go to the reasoning model, whose
holds / gap / error is labelled "AI, not verified". Check never gives the fix or the next step.
shortcut: feedback on SymPy's `unverified` algebra comes from templates. Upgrade path: the reasoning
model words those questions too.
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
    problem: str | None = Field(None, max_length=4000)  # the confirmed problem these steps work on, if known


class CheckResponse(BaseModel):
    # ai_check / hint / answer / explain come from the reasoning model, never from SymPy
    verdict: Literal["verified", "refuted", "unverified", "noted", "ai_check", "hint", "answer", "explain"]
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


JUDGE_SYSTEM = """You tutor a first-year university proofs course. A student selected steps of their handwritten
proof in a notebook and asked you to check them. Their lines are LaTeX they have confirmed.

Judge only whether what is written holds. Never give the next step, the fix or a proof.
- verdict: "holds" if every selected step follows; "gap" if a justification is missing (a theorem not named,
  its hypotheses not checked, a missing case); "error" if a step is false or doesn't follow.
- reason: one or two sentences, under 60 words. For a gap or an error, point at the exact step and what is
  wrong, without fixing it. For holds, name the key justification.
Write math as $...$ (LaTeX)."""

PROMPT_ID = hashlib.sha256(JUDGE_SYSTEM.encode()).hexdigest()[:12]  # a prompt edit never serves old answers
VERDICT_WORDS = re.compile(r"\b(correct|incorrect|wrong|well done|good job|great job)\b", re.I)


class StepJudgement(BaseModel):
    verdict: Literal["holds", "gap", "error"]
    reason: str


def _judge_check(j: StepJudgement) -> list[str]:
    return [] if 0 < len(j.reason) <= 600 else ["reason must be 1-600 characters"]


JUDGE_LABEL = {"holds": "Holds.", "gap": "Gap:", "error": "Error:"}


def judge_steps(req: CheckRequest, user: str, model: str) -> CheckResponse:
    parts = [f"Problem:\n{req.problem}"] if req.problem else []
    parts.append("Confirmed lines above the selection, oldest first:\n" + ("\n".join(req.context) or "(none)"))
    parts.append(f"Selected:\n{req.current}")
    out = llm.structured_call(
        endpoint="check", user=user, cost_class="reason", model=model, system=JUDGE_SYSTEM,
        content=[{"type": "text", "text": "\n\n".join(parts)}], schema=StepJudgement, semantic_check=_judge_check,
        cache_input={"parts": parts, "prompt": PROMPT_ID},
        # A verdict is stated to the student, so the model thinks harder before giving one.
        effort="high", max_tokens=8192,
    )
    # Not a SymPy verdict: the card labels it "AI, not verified" and never reuses SymPy's card types (DECISIONS #19).
    return CheckResponse(verdict="ai_check", reason=f"judge_{out.verdict}",
                         message=f"{JUDGE_LABEL[out.verdict]} {out.reason}")


NOTED = "This is written in words, so there's nothing to calculate. I've noted it; the lines below can build on it."


def assignment(env: dict[str, str]) -> str:
    return ", ".join(f"{k} = {v}" for k, v in env.items())


def check(req: CheckRequest, user: str) -> CheckResponse:
    if is_prose(req.current):
        # Words ("Let f be continuous...", "hence p is even") aren't algebra SymPy can judge.
        model = req.reasoning_model or config.REASONING_MODEL
        if config.llm_configured(model):
            return judge_steps(req, user, model)
        return CheckResponse(verdict="noted", reason="prose", message=NOTED)  # no reasoning model available
    r = check_step(req.current, req.context)
    if r.verdict == "verified" and r.reason == "substitution_equal":
        msg = f"This follows by substituting ${r.compared_with}$."
    elif r.verdict == "verified":
        msg = "This follows from the line above."
    elif r.verdict == "refuted" and r.reason == "rewrite_differs":
        msg = (f"Try ${assignment(r.counterexample)}$: the line above gives ${r.values['before']}$, "
               f"but yours gives ${r.values['after']}$.")
    elif r.verdict == "refuted":
        msg = f"${assignment(r.counterexample)}$ satisfies ${r.compared_with}$, but not your line."
    else:
        msg = QUESTIONS.get(r.reason, DEFAULT_QUESTION)
    return CheckResponse(verdict=r.verdict, reason=r.reason, message=msg, counterexample=r.counterexample,
                         values=r.values, compared_with=r.compared_with)
