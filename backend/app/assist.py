"""/assist: Hint, Answer and Explain on a selection (SPEC §4, DECISIONS #19-20).

A problem is solved once: the reasoning model writes the proof as steps, SymPy checks every algebra claim
(a counterexample goes back to the model through structured_call's retry), and the checked proof is cached
server-side, keyed by the problem. Hint, Answer and Explain all build on that one proof. The proof only
reaches the browser when the student asks for Answer or Explain.
"""
import hashlib
import threading
from typing import Literal

from pydantic import BaseModel, Field

from . import config, geometry, llm
from .check import CheckResponse, VERDICT_WORDS, assignment
from .scene import proof_scene
from .verify import check_step


class AssistRequest(BaseModel):
    action: Literal["hint", "answer", "explain"]
    kind: Literal["problem", "steps", "concept"]  # what the selection is; the student confirmed it
    selection: str = Field(min_length=1, max_length=4000)  # confirmed readings of the selected lines
    problem: str | None = Field(None, max_length=4000)  # for steps: the confirmed problem they work on
    work: list[str] = Field(default_factory=list, max_length=30)  # confirmed lines between problem and selection
    definitions: list[str] = Field(default_factory=list, max_length=10)  # definitions written earlier on the page
    style: Literal["steps", "picture"] = "steps"  # Explain on a problem or concept: equations, or a picture
    confirmed: Literal[True]  # confirm-before-judge
    reasoning_model: str | None = None


# ---- solve + verify ----

class ProofStep(BaseModel):
    text: str = Field(description="the step as written for a student, math as $...$")
    claim: str = Field("", description="LaTeX of the one equation or expression this step states, no words, no $; "
                                       "empty if none")
    algebra: Literal["none", "identity", "derived"] = Field(
        "none", description="identity: true for all values; derived: follows from earlier claims by algebra alone")
    uses: list[int] = Field(default_factory=list, description="derived only: the earlier step numbers it follows from")
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
  - "derived": the claim follows from earlier claims by algebra alone (rearranging, or substituting an
    earlier claim of the form "x = ..."), like $4k^2 = 2q^2$ from $p^2 = 2q^2$ and $p = 2k$. Put the step
    numbers it follows from in `uses` (here the steps stating $p^2 = 2q^2$ and $p = 2k$). In a proof by
    cases, cite only steps from the same case.
  - "none": anything else: new definitions (like $p = 2k$ from "p is even"), assumptions, inequalities, and
    anything that needs a theorem, parity, divisibility or a limit argument.
- why: one sentence on why the step holds.
- checked: always false.
An algebra checker verifies every identity and derived claim; a wrong one is sent back to you.
Use results a first-year course would know, and name each theorem you use and check its hypotheses."""


def _verify(p: Proof) -> list[str]:
    """SymPy checks each identity on its own and each derived claim against the claims before it.
    Sets `checked`; a refuted claim is an error, which goes back to the model."""
    errors, claims = [], {}  # step number -> its claim
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
        elif s.claim and s.algebra == "derived" and (cited := [claims[j] for j in sorted(set(s.uses))
                                                              if j in claims and j < i]):
            # Only the cited steps: in a proof by cases, the nearest earlier claim can be from another case.
            r = check_step(s.claim, cited)
            if r.verdict == "refuted":
                errors.append(f"step {i}: ${s.claim}$ does not follow from ${r.compared_with}$ "
                              f"(at ${assignment(r.counterexample)}$ they differ); fix the algebra, cite the "
                              "right steps in `uses`, or mark it none if it needs more than algebra")
            # trivial_after: the claim holds for all values on its own, so it's true whatever came before
            s.checked = r.verdict == "verified" or r.reason == "trivial_after"
        if s.claim:
            claims[i] = s.claim
    return errors


# One solve per problem at a time: a Hint pressed while the background solve runs waits for it, then
# reads the cache, instead of paying for a second solve.
# shortcut: in-process locks, so this holds for one server process. Upgrade path: a lock row in the DB.
_solving: dict[tuple, threading.Lock] = {}
_solving_guard = threading.Lock()


def solve(problem: str, user: str, model: str, cost_class: str = "reason") -> Proof:
    """The problem alone is the key (not the page's definitions), so the background solve and every
    action on that problem share one cached proof."""
    with _solving_guard:
        lock = _solving.setdefault((problem, model), threading.Lock())
    with lock:
        return llm.structured_call(
            endpoint="solve", user=user, cost_class=cost_class, model=model, system=SOLVE_SYSTEM,
            content=[{"type": "text", "text": f"Problem:\n{problem}"}], schema=Proof, semantic_check=_verify,
            # DeepSeek counts thinking against max_tokens; a 12-step proof by cases hit 8192 and was cut off.
            cache_input={"problem": problem, "prompt": _id(SOLVE_SYSTEM)}, effort="high", max_tokens=16384,
        )


class PresolveRequest(BaseModel):
    problem: str = Field(min_length=1, max_length=4000)  # a problem the page labels found (not yet confirmed)
    reasoning_model: str | None = None


def presolve(req: PresolveRequest, user: str) -> None:
    """Solve in the background so the first Hint / Answer / Explain on a problem is fast. Nothing is shown
    to the student, so an unconfirmed reading is fine; a different confirmed text just solves again."""
    model = req.reasoning_model or config.REASONING_MODEL
    if not config.llm_configured(model):
        return
    try:
        solve(req.problem, user, model, cost_class="presolve")
    except (llm.LLMError, llm.QuotaExceeded):
        pass  # the action solves (or falls back) when pressed; the attempt is in the request log


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


PICTURE_SYSTEM = r"""You explain proofs and concepts for a first-year university proofs course with a geometric,
intuitive picture, played step by step as an animation on a dark board (like 3Blue1Brown). The picture
should carry the argument: show WHY it is true, not only what. Prefer classic visual arguments when they
fit: cutting and rearranging areas, similar triangles, symmetry, a square or rectangle split into pieces,
dots or squares in a pattern, a graph of a function (secants, tangents, bands around a value).

Return JSON with params, points and steps.
- params: up to 6 one-letter lowercase names (not x) with a positive value used to draw, e.g. a=2, b=1.
- points: named points (A, B, P_1, A'...) with coordinates as LaTeX in the params, e.g. "a+b", "\frac{a}{2}",
  "0". Axes are the usual math axes, y up. Writing coordinates in the params keeps the picture true for
  every value; plain numbers are fine when the picture is a specific example.
- steps, played in order (ids s1, s2, ...; 5 to 14 steps):
  - draw: add a shape. shape is one of point [P], segment [A,B], polygon [A,B,C,...] (filled),
    circle [centre, a point on it], angle [A,B,C] (marks the angle at B), curve (the graph y = f, with f in
    LaTeX in x and the params, and domain [from, to]). Optional label: short LaTeX shown on the shape
    (no $ signs; keep labels few and short, the picture should not be crowded).
  - move: slide or rotate an earlier polygon or segment (target) to new points (points: same number, same
    order). Pieces only move, they never stretch: the moved shape must be congruent to the original.
  - write: a short equation shown beside the picture (latex, no $ signs). transform: rewrite an earlier
    write (target) into new latex.
  - highlight / fade: target an earlier step id still on the board.
  - say: the caption for the step: what to look at and why it matters, one or two sentences, math as $...$.
  - claims: every fact the step's picture asserts that can be measured, as {"left": Q, "right": Q} with Q
    one of {"length": ["A","B"]}, {"angle": ["A","B","C"]} (degrees, vertex in the middle),
    {"area": ["A","B","C",...]}, {"value": "LaTeX in the params"}. A checker computes every claim from your
    coordinates; a false claim, a stretched move or an unknown point is sent back to you.
If no honest picture of the whole proof exists, picture the key idea (a graph, a number line, an example)
and say in the captions what the picture shows and what it doesn't. Never draw a figure that misleads.

Example, for "Show (a+b)^2 = a^2 + 2ab + b^2":
{"params": [{"name": "a", "value": 2}, {"name": "b", "value": 1}],
 "points": [{"name": "A", "x": "0", "y": "0"}, {"name": "B", "x": "a+b", "y": "0"}, {"name": "C", "x": "a+b", "y": "a+b"},
  {"name": "D", "x": "0", "y": "a+b"}, {"name": "P", "x": "a", "y": "0"}, {"name": "Q", "x": "a+b", "y": "a"},
  {"name": "R", "x": "a", "y": "a+b"}, {"name": "S", "x": "0", "y": "a"}, {"name": "M", "x": "a", "y": "a"}],
 "steps": [
  {"id": "s1", "op": "draw", "shape": "polygon", "points": ["A", "B", "C", "D"], "label": "(a+b)^2",
   "say": "A square with side $a+b$ has area $(a+b)^2$.",
   "claims": [{"left": {"area": ["A", "B", "C", "D"]}, "right": {"value": "(a+b)^2"}}]},
  {"id": "s2", "op": "draw", "shape": "polygon", "points": ["A", "P", "M", "S"], "label": "a^2",
   "say": "Cut off a square of side $a$ in one corner...",
   "claims": [{"left": {"area": ["A", "P", "M", "S"]}, "right": {"value": "a^2"}}]},
  {"id": "s3", "op": "draw", "shape": "polygon", "points": ["M", "Q", "C", "R"], "label": "b^2",
   "say": "...and a square of side $b$ in the opposite corner.",
   "claims": [{"left": {"area": ["M", "Q", "C", "R"]}, "right": {"value": "b^2"}}]},
  {"id": "s4", "op": "draw", "shape": "polygon", "points": ["P", "B", "Q", "M"], "label": "ab",
   "say": "What's left is two rectangles, each $a$ by $b$.",
   "claims": [{"left": {"area": ["P", "B", "Q", "M"]}, "right": {"value": "ab"}}]},
  {"id": "s5", "op": "draw", "shape": "polygon", "points": ["S", "M", "R", "D"], "label": "ab", "say": "",
   "claims": [{"left": {"area": ["S", "M", "R", "D"]}, "right": {"value": "ab"}}]},
  {"id": "s6", "op": "write", "latex": "(a+b)^2 = a^2 + 2ab + b^2",
   "say": "The four pieces fill the square exactly, so their areas add up to the square's.",
   "claims": [{"left": {"value": "(a+b)^2"}, "right": {"value": "a^2+2ab+b^2"}}]}]}"""

NO_MODEL = "No reasoning model is set up, so I can't help with this yet."
NO_PICTURE = "I couldn't draw a picture that passes the checker. Try Explain step by step."
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
            proof = solve(problem, user, model)
        except llm.LLMError:
            proof = None  # Hint falls back to a question with no answer behind it; Answer/Explain say so

    if req.action == "explain" and req.style == "picture" and req.kind in ("problem", "concept"):
        return picture(req, problem, proof, user, model)
    if req.kind == "problem" and req.action != "hint":
        if proof is None:
            return CheckResponse(verdict="noted", reason="no_checked_answer", message=NO_PROOF)
        explain = req.action == "explain"
        return CheckResponse(verdict=req.action, reason=f"{req.action}_problem",
                             message=format_proof(proof, why=explain), scene=proof_scene(proof) if explain else None)

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


def picture(req: AssistRequest, problem: str | None, proof: Proof | None, user: str, model: str) -> CheckResponse:
    """Explain with a picture (DECISIONS #24): the model draws, SymPy checks the drawing, code lays it out."""
    parts = [f"Problem:\n{problem}" if req.kind == "problem" else f"Concept:\n{req.selection}"]
    if proof:
        parts.append("A checked proof, for reference (your picture may argue differently):\n"
                     + format_proof(proof, why=False, footnote=False))
    out = llm.structured_call(
        endpoint="picture", user=user, cost_class="reason", model=model, system=PICTURE_SYSTEM,
        content=[{"type": "text", "text": "\n\n".join(parts)}], schema=geometry.Picture,
        semantic_check=geometry.picture_check, cache_input={"parts": parts, "prompt": _id(PICTURE_SYSTEM)},
        effort="high", max_tokens=16384,  # thinking counts against max_tokens on DeepSeek
    )
    render = geometry.analyse(out.model_dump())  # compiled fresh, so layout fixes reach cached figures too
    if render["errors"]:  # only if the checker changed since this figure was cached
        return CheckResponse(verdict="noted", reason="picture_failed", message=NO_PICTURE)
    return CheckResponse(verdict="explain", reason=f"explain_picture_{req.kind}", scene=render["scene"],
                         message=render["text"] + "\nAI picture: claims marked ✓ were checked by SymPy.")
