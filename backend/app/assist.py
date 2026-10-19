"""/assist: Hint, Answer and Explain on a selection (SPEC §4, DECISIONS #19-20).

A problem is solved once: the reasoning model writes the proof as steps, SymPy checks every algebra claim
(a counterexample goes back to the model through structured_call's retry), and the checked proof is cached
server-side, keyed by the problem. Hint, Answer and Explain all build on that one proof. The proof only
reaches the browser when the student asks for Answer or Explain.
"""
import hashlib
from typing import Literal

from pydantic import BaseModel, Field

from . import config, llm
from .check import CheckResponse, VERDICT_WORDS, assignment
from .verify import check_step


class AssistRequest(BaseModel):
    action: Literal["hint", "answer", "explain"]
    kind: Literal["problem", "steps", "concept"]  # what the selection is; the student confirmed it
    selection: str = Field(min_length=1, max_length=4000)  # confirmed readings of the selected lines
    problem: str | None = Field(None, max_length=4000)  # for steps: the confirmed problem they work on
    work: list[str] = Field(default_factory=list, max_length=30)  # confirmed lines between problem and selection
    definitions: list[str] = Field(default_factory=list, max_length=10)  # definitions written earlier on the page
    confirmed: Literal[True]  # confirm-before-judge
    reasoning_model: str | None = None


# ---- solve + verify ----

class ProofStep(BaseModel):
    text: str = Field(description="the step as written for a student, math as $...$")
    claim: str = Field("", description="LaTeX of the one equation or expression this step states, no words, no $; "
                                       "empty if none")
    algebra: Literal["none", "identity", "derived"] = Field(
        "none", description="identity: true for all values; derived: follows from earlier claims by algebra alone")
    why: str = Field(description="one sentence: why this step holds")
    checked: bool = Field(False, description="always false; the server sets it")


class Proof(BaseModel):
    steps: list[ProofStep] = Field(min_length=1, max_length=15)


SOLVE_SYSTEM = """You write model proofs for a first-year university proofs course. Prove the problem you
are given, as a list of short steps in order (at most 12). For each step:
- text: the step as you would write it for a student. Write math as $...$.
- claim: if the step states one equation or expression, its LaTeX (no words, no $, one "=" at most); else "".
- algebra, one of:
  - "identity": the claim is an equation true for all values of its variables (expanding, factoring,
    simplifying), like $(a+b)^2 = a^2+2ab+b^2$.
  - "derived": the claim follows from the earlier claims by algebra alone (rearranging, or substituting an
    earlier claim of the form "x = ..."), like $4k^2 = 2q^2$ from $p^2 = 2q^2$ and $p = 2k$.
  - "none": anything else: new definitions (like $p = 2k$ from "p is even"), assumptions, inequalities, and
    anything that needs a theorem, parity, divisibility or a limit argument.
- why: one sentence on why the step holds.
- checked: always false.
An algebra checker verifies every identity and derived claim; a wrong one is sent back to you.
Use results a first-year course would know, and name each theorem you use and check its hypotheses."""


def _verify(p: Proof) -> list[str]:
    """SymPy checks each identity on its own and each derived claim against the claims before it.
    Sets `checked`; a refuted claim is an error, which goes back to the model."""
    errors, claims = [], []
    for i, s in enumerate(p.steps, 1):
        s.checked = False
        if s.claim and s.algebra == "identity" and s.claim.count("=") == 1:
            lhs, rhs = s.claim.split("=")
            r = check_step("=" + rhs, [lhs])  # "= R" continues "L": SymPy compares the two sides for all values
            if r.verdict == "refuted":
                errors.append(f"step {i}: ${s.claim}$ isn't true for all values (at ${assignment(r.counterexample)}$ "
                              f"the left side is ${r.values['before']}$, the right side ${r.values['after']}$); "
                              "fix it, or mark it derived if it follows from earlier claims")
            s.checked = r.verdict == "verified"
        elif s.claim and s.algebra == "derived" and claims:
            r = check_step(s.claim, claims)
            if r.verdict == "refuted":
                errors.append(f"step {i}: ${s.claim}$ does not follow from ${r.compared_with}$ "
                              f"(at ${assignment(r.counterexample)}$ they differ); fix the algebra")
            # trivial_after: the claim holds for all values on its own, so it's true whatever came before
            s.checked = r.verdict == "verified" or r.reason == "trivial_after"
        if s.claim:
            claims.append(s.claim)
    return errors


def solve(problem: str, definitions: list[str], user: str, model: str) -> Proof:
    text = f"Problem:\n{problem}"
    if definitions:
        text += "\n\nDefinitions the student wrote earlier:\n" + "\n".join(definitions)
    return llm.structured_call(
        endpoint="solve", user=user, cost_class="reason", model=model, system=SOLVE_SYSTEM,
        content=[{"type": "text", "text": text}], schema=Proof, semantic_check=_verify,
        cache_input={"problem": problem, "definitions": definitions, "prompt": _id(SOLVE_SYSTEM)},
        effort="high", max_tokens=8192,
    )


def format_proof(p: Proof, *, why: bool, footnote: bool = True) -> str:
    out = [f"{i}. {s.text}{' ✓' if s.checked else ''}" + (f"\n    Why: {s.why}" if why else "")
           for i, s in enumerate(p.steps, 1)]
    if footnote:
        out.append("✓ = algebra checked by SymPy. Other steps aren't machine-checked." if any(s.checked for s in p.steps)
                   else "No step here could be machine-checked.")
    return "\n".join(out)


# ---- the tutor reply for everything that isn't a solved proof ----

class Reply(BaseModel):
    text: str


BASE = """You tutor a first-year university proofs course. A student selected part of their handwritten work
in a notebook and asked for help. Their lines are LaTeX they have confirmed. Write math as $...$ (LaTeX)."""

HINT_RULES = """
Reply with ONE short question (at most two sentences, under 60 words). Never say whether the work is correct,
wrong or complete, and never give the answer or a proof."""

PROMPTS = {
    # A hint with a checked reference proof behind it (DECISIONS #20).
    "hint_ref": BASE + HINT_RULES + """
You also get a reference proof the student must not see. Use it only to know where a proof can go:
- Ask about the student's next step. If nothing is written beyond the problem, ask how to start.
- Follow the student's own route. If their approach differs from the reference but can work, hint along it.
- Don't name a construction, function or theorem from the reference that the student hasn't written.""",
    # No reference proof (no problem found, or it couldn't be solved and checked): the P2 question.
    "hint_blind": BASE + HINT_RULES + """
Ask about the most important thing: for a problem statement, what the goal says precisely or what kind of
argument could reach it; for proof steps, the weakest justification, a hidden assumption, a missing case or
a quantifier. Don't introduce a construction, function or theorem the student hasn't written.""",
    "hint_concept": BASE + HINT_RULES + """
The selection is a concept. Ask one question that tests whether they understand it, e.g. ask for an example,
a non-example, or what the definition says in a specific case. Don't define it.""",
    "answer_steps": BASE + """
Say plainly in the first sentence whether the selected reasoning holds. If it doesn't, name the exact error
or gap and show how to fix it. If it does, name the key justification. Under 120 words.
Judge only what is written: a theorem used without naming it or checking its hypotheses is a gap.
A reference proof may be given; the student's route can differ from it and still be right.""",
    "answer_concept": BASE + """
The selection is a concept. Give its precise definition as a first-year course states it, then one sentence
of intuition. Under 80 words.""",
    "explain_steps": BASE + """
Explain what each selected step does and why it is needed, in plain words, step by step. If a step doesn't
hold, say so plainly and why. Under 150 words.""",
    "explain_concept": BASE + """
The selection is a concept. Explain it for a first-year student: the definition, the intuition, and one
worked example (and a non-example if it helps). Under 200 words.""",
}


def _id(prompt: str) -> str:  # a prompt edit never serves old answers
    return hashlib.sha256(prompt.encode()).hexdigest()[:12]


def _hint_check(r: Reply) -> list[str]:
    errors = []
    if "?" not in r.text:
        errors.append("the hint must be a question (contain '?')")
    if len(r.text) > 400:
        errors.append("the hint must be under 400 characters")
    if VERDICT_WORDS.search(r.text):
        errors.append("no verdicts: don't say correct, wrong or well done; only ask")
    return errors


def _length_check(r: Reply) -> list[str]:
    return [] if 0 < len(r.text) <= 2000 else ["reply must be 1-2000 characters"]


NO_MODEL = "No reasoning model is set up, so I can't help with this yet."
NO_PROOF = "I couldn't write an answer that passes the algebra checker. Try again, or ask for a hint."
NOT_CHECKED = "\nAI, not machine-checked."


def assist(req: AssistRequest, user: str) -> CheckResponse:
    model = req.reasoning_model or config.REASONING_MODEL
    if not config.llm_configured(model):
        return CheckResponse(verdict="noted", reason="no_model", message=NO_MODEL)
    problem = req.selection if req.kind == "problem" else req.problem
    proof = None
    if problem and req.kind != "concept":
        try:
            proof = solve(problem, req.definitions, user, model)
        except llm.LLMError:
            proof = None  # Hint falls back to a question with no answer behind it; Answer/Explain say so

    if req.kind == "problem" and req.action != "hint":
        if proof is None:
            return CheckResponse(verdict="noted", reason="no_checked_answer", message=NO_PROOF)
        return CheckResponse(verdict=req.action, reason=f"{req.action}_problem",
                             message=format_proof(proof, why=req.action == "explain"))

    if req.action == "hint":
        prompt = "hint_concept" if req.kind == "concept" else "hint_ref" if proof else "hint_blind"
    else:
        prompt = f"{req.action}_{'concept' if req.kind == 'concept' else 'steps'}"
    parts = []
    if problem and req.kind != "problem":
        parts.append(f"Problem:\n{problem}")
    if req.definitions:
        parts.append("Definitions written earlier on the page:\n" + "\n".join(req.definitions))
    if req.work:
        parts.append("The student's lines before the selection, oldest first:\n" + "\n".join(req.work))
    parts.append(f"Selected ({'problem statement' if req.kind == 'problem' else req.kind}):\n{req.selection}")
    if proof:
        parts.append("Reference proof (never show it to the student):\n" + format_proof(proof, why=False, footnote=False))
    hint = req.action == "hint"
    out = llm.structured_call(
        endpoint=req.action, user=user, cost_class="reason", model=model, system=PROMPTS[prompt],
        content=[{"type": "text", "text": "\n\n".join(parts)}], schema=Reply,
        semantic_check=_hint_check if hint else _length_check,
        cache_input={"parts": parts, "prompt": _id(PROMPTS[prompt])},
        # Answers are stated as fact to the student, so the model thinks harder before giving one.
        effort="high" if req.action == "answer" else "medium", max_tokens=8192,
    )
    return CheckResponse(verdict=req.action, reason=f"{req.action}_{req.kind}",
                         message=out.text if hint else out.text + NOT_CHECKED)
