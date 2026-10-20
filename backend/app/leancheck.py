"""Check a student's proof with Lean, line by line (SPEC §4, DECISIONS #25, #27).

1. The model *translates* the student's confirmed lines into Lean: structural tactics (intro, obtain, induction, use,
   ...) and the claims each line makes, plus the hypotheses a line cites as its reason. It writes no proofs.
2. Lean checks every claim with our fixed automation (AUTO), from the lines before it. One run checks the whole proof: a
   failed step is reported and Lean carries on, and a marker after each item shows Lean really got there.
3. If Lean rejects the *translation* (a tactic, a claim's syntax, closing the goal), the model retries with those errors
   only. It never hears which claims failed, so it can't learn to "fix" the student's mistakes.
4. A claim AUTO can't prove gets one more chance: the model writes a proof of that claim alone, which Lean checks. A
   false claim has no proof, so this can't add a false ✓; the line is marked "skipped step" (true, but a step is
   missing). Not after "suppose, for contradiction": there anything follows, so only AUTO's local reasoning counts.
5. A line that cites a reason ("since g is injective") must also follow with the statement's other hypotheses cleared,
   or it is marked "wrong reason".
The problem's statement comes from `state`: the model writes it in Lean (Lean must accept it), a second, cheaper model
says in plain words what that Lean says, and the student confirms those words before anything is checked.
Measured on planted mistakes in leaneval/steps.py (L0b).
"""
from __future__ import annotations

import hashlib
import re
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field

from . import config, lean, llm

def _id(s: str) -> str:
    """A prompt's short hash, part of cache keys so editing a prompt never serves old answers."""
    return hashlib.sha256(s.encode()).hexdigest()[:12]


# Each tactic must close the goal by itself, or the next one is tried.
AUTO = "first | " + " | ".join(f"({t}; done)" for t in
                               ["omega", "ring_nf", "linarith", "nlinarith", "positivity", "norm_num", "simp_all", "aesop"])
HEAD_LINES = 5  # import, blank, set_option, blank, theorem: the proof starts on line 6
RETRIES = 2  # translations Lean rejected
PROOF_ROUNDS = 2  # rounds of single-claim proofs (the second sees Lean's errors from the first)

# Structural tactics only: they introduce, split or name things but can't prove a claim for the student.
STRUCTURAL = {"intro", "intros", "rintro", "obtain", "rcases", "cases", "induction", "by_contra", "by_cases", "push_neg",
              "use", "exists", "constructor", "left", "right", "ext", "funext", "unfold", "rw", "case", "next", "|", "·",
              "exfalso", "contrapose", "contrapose!", "by_contra!", "specialize", "set", "subst"}
CLOSING = re.compile(r"\b(by|exact|exacts|apply|simp|simp_all|omega|linarith|nlinarith|ring|ring_nf|norm_num|positivity|"
                     r"aesop|decide|tauto|trivial|assumption|rfl|calc|refine|first|all_goals|any_goals|try|repeat)\b|;|<;>")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_']*")
# Model text must not print our markers, which vouch that Lean reached a line.
SPOOF = re.compile(r"\btrace|mellow")

SYSTEM = """You translate a student's handwritten proof, line by line, into Lean 4 + Mathlib, so Lean can check each line.
You are given the theorem's statement (fixed, after `theorem target`) and the student's lines, numbered from 0.
Return JSON {"items": [...]}, in proof order. Each item:
- line: the student line it comes from (every student line needs at least one item);
- kind: "tactic" for structure: intro, obtain, rcases, cases, induction, by_contra, by_cases, push_neg, use, constructor,
  left, right, ext, unfold, rw, case blocks ("| zero =>", "| succ n ih =>"), or a focusing dot "·";
  "have" for a claim the line makes: text is the Lean proposition only, it becomes `have <name> : <text> := by <automation>`;
  "close" for a claim that is the current goal: also proved by automation, then used to close the goal;
- text: the tactic, or the proposition;
- name: for have/close, the hypothesis name (later items may refer to it);
- reasons: for have/close, the names of the theorem's hypotheses the student explicitly gives as the reason for this
  line ("since g is injective" -> ["hg"]); [] when the line cites none or only earlier lines;
- indent: nesting depth inside the proof (0 = top level; one more inside a case block or after "·");
- contradiction: true on the item that introduces the student's "suppose, for contradiction, ..." assumption (by_contra,
  or the intro of a negated goal such as Irrational x or ¬P); false otherwise.
Rules:
- Be faithful. Translate exactly what the student claims, even if it is false or does not follow. Never correct a
  mistake, never add a fact or step the student didn't write, never strengthen or weaken a claim. A false line must stay
  false in Lean: this is how mistakes are found.
- A line with several claims ("a + b = 2k + 2m = 2(k + m)") gets one have per claim, chained as the student wrote it.
- Write no proofs: tactic items may not contain by, exact, apply, simp, omega, linarith, ring, calc, refine, `;`. The
  automation proves every have and close from the earlier hypotheses.
- When the student introduces or assumes something, give it the type the student states, so Lean checks it against the
  goal: "let x, y with g(f(x)) = g(f(y))" -> `intro x y (h : g (f x) = g (f y))`; "let x, y with x = y" ->
  `intro x y (h : x = y)`, even when the goal gives a different hypothesis (Lean then rejects it: that is the mistake).
- Never repair the student's reasoning. "Since f is injective, f(x) = f(y)" has reasons ["hf"] even when hg would be
  the right reason; a wrong assumption, a wrong reason or a false claim must reach Lean exactly as written.
- Introduce the student's variables and witnesses as they do: `obtain ⟨k, hk⟩ := ha` for "a = 2k for some k", `use k + m`
  for "k + m is an integer, so ... is even", `intro ε hε` for "let ε > 0". When the goal has several parts (∃ δ > 0, ...
  is `δ > 0 ∧ ...` after `use`), split it with constructor and "·" blocks.
- A close's proposition must be exactly the goal at that point."""

RETRY = """Lean rejected the structure of your previous translation:
{errors}
Fix only how the lines are translated: tactics, syntax, names, splitting and closing goals. Keep every claim saying
exactly what the student wrote; whether a claim is true is not your concern."""

PROVE_SYSTEM = """You prove single steps inside a Lean 4 + Mathlib proof. You get the proof so far and, for each step to
prove, its index, its claim and Lean's goal state at that step (the hypotheses in scope). Return JSON {"proofs": [...]},
each {"index": the step's index, "proof": the tactic lines that prove that claim from the hypotheses in scope, one tactic
per line}. Never use sorry, admit, axioms, native_decide, #eval, or search tactics (exact?, apply?, simp?). Never change
a claim. If you believe a claim is false or does not follow from the hypotheses in scope, return "" as its proof.
Prefer named Mathlib lemmas and robust tactics: obtain, rcases, simp [lemmas], rw, ring, omega, linarith [facts],
nlinarith [facts], positivity, gcongr, abs_lt, abs_sub_lt_iff, Finset.sum_range_succ, Int.even_or_odd, ..."""


class Item(BaseModel):
    line: int = Field(ge=0)
    kind: Literal["tactic", "have", "close"]
    text: str = Field(min_length=1, max_length=600)
    name: str = Field("", max_length=40)
    reasons: list[str] = Field(default_factory=list, max_length=6)
    indent: int = Field(0, ge=0, le=6)
    contradiction: bool = False


class Translation(BaseModel):
    items: list[Item] = Field(min_length=1, max_length=80)


class StepProof(BaseModel):
    index: int
    proof: str = Field("", max_length=4000)


class StepProofs(BaseModel):
    proofs: list[StepProof] = Field(max_length=40)


def problems_check(t: Translation, n_lines: int, hyps: list[str]) -> list[str]:
    """Why a translation can't be used (empty if it can): every line covered, structure only, no proofs, no cheats."""
    errs = []
    if missing := sorted(set(range(n_lines)) - {i.line for i in t.items}):
        errs.append(f"student lines with no item: {missing}")
    for k, i in enumerate(t.items):
        if i.line >= n_lines:
            errs.append(f"item {k}: line {i.line} doesn't exist")
        if bad := lean.FORBIDDEN.search(i.text) or SPOOF.search(i.text):
            errs.append(f"item {k}: not allowed: {bad.group(0)!r}")
        if i.kind == "tactic":
            first = i.text.split()[0] if i.text.split() else ""
            if first not in STRUCTURAL:
                errs.append(f"item {k}: {first!r} is not a structural tactic")
            if bad := CLOSING.search(i.text):
                errs.append(f"item {k}: tactic items may not prove anything ({bad.group(0)!r})")
        else:
            if not NAME.fullmatch(i.name):
                errs.append(f"item {k}: {i.kind} needs a hypothesis name")
            if re.search(r"\bby\b|:=", i.text):
                errs.append(f"item {k}: a claim is a proposition only, no proof")
        if unknown := [r for r in i.reasons if r not in hyps]:
            errs.append(f"item {k}: reasons must be the theorem's hypothesis names ({', '.join(hyps)}), not {unknown}")
    return errs


def proofs_check(p: StepProofs, todo: set[int]) -> list[str]:
    errs = [f"step {s.index} wasn't asked for" for s in p.proofs if s.index not in todo]
    errs += [f"step {s.index}: not allowed: {bad.group(0)!r}" for s in p.proofs
             if (bad := lean.FORBIDDEN.search(s.proof) or SPOOF.search(s.proof))]
    return errs


def hypotheses(statement: str) -> list[str]:
    """Names bound in the statement's binders, before its top-level colon: `(a b : ℤ) (ha : Even a)` -> a, b, ha."""
    depth = 0
    for k, ch in enumerate(statement):
        depth += (ch in "([{⟨") - (ch in ")]}⟩")
        if ch == ":" and depth == 0:
            statement = statement[:k]
            break
    return [n for group in re.findall(r"[({\[]\s*([^(){}\[\]:]+?)\s*:", statement) for n in group.split() if n.isidentifier()]


def assemble(statement: str, items: list[Item], proofs: dict[int, str] | None = None
             ) -> tuple[str, dict[int, tuple[int, str, int]], dict[int, list[int]]]:
    """The Lean file; for each proof line (1-based, as Lean counts) its (item, part, column where the claim's proof
    starts); and which student lines each `trace "mellow k"` marker vouches for. A failing tactic can make Lean skip the
    rest of the proof without an error on those lines, so a line only counts once Lean has printed the markers after it.
    `proofs` replaces AUTO for single claims (step 4 above)."""
    proofs = proofs or {}
    hyps = hypotheses(statement)
    body, where, markers, pending = [], {}, {}, set()

    def emit(indent: int, text: str, idx: int, part: str, by_col: int = 0):
        where[HEAD_LINES + len(body) + 1] = (idx, part, by_col)
        body.append("  " * (indent + 1) + text)

    for idx, i in enumerate(items):
        pad = len("  " * (i.indent + 1))
        if i.kind == "tactic":
            emit(i.indent, i.text, idx, "tactic")
        else:
            if i.reasons and idx not in proofs:
                # Inside a `True` block, so neither its result nor its failure leaves the claim behind for later lines.
                clear = "".join(f"(try clear {h}); " for h in hyps if h not in i.reasons)
                emit(i.indent, f"have _reason{idx} : True := by", idx, "reason")
                emit(i.indent + 1, f"have _r : {i.text} := by {clear}{AUTO}", idx, "reason")
                emit(i.indent + 1, "trivial", idx, "reason")
            head = f"have {i.name} : {i.text} := by"
            if idx in proofs:
                emit(i.indent, head, idx, "claim", pad + len(head) - 2)
                # Keep the proof's own indentation: its bullets and case blocks depend on it.
                for line in textwrap.dedent(proofs[idx].strip("\n")).splitlines():
                    emit(i.indent + 1, line.rstrip(), idx, "proof")
            else:
                emit(i.indent, f"{head} {AUTO}", idx, "claim", pad + len(head) - 2)
            if i.kind == "close":
                emit(i.indent, f"exact {i.name}", idx, "exact")
        pending.add(i.line)
        text = i.text.rstrip()
        if text.endswith("with"):
            continue  # `induction n with` must be followed by its cases; the next marker vouches for it
        k = len(markers)
        markers[k] = sorted(pending)
        # A block opener's marker goes inside the block, as its first tactic.
        emit(i.indent + (text.endswith("=>") or text == "·"), f'trace "mellow {k}"', idx, "marker")
        pending = set()
    head = f"import Mathlib\n\nset_option maxHeartbeats 1000000\n\ntheorem target {statement} := by\n"
    return head + "\n".join(body) + "\n", where, markers


def verdicts(out: str, where: dict, markers: dict[int, list[int]], items: list[Item], n_lines: int,
             proved_by_model: set[int] = frozenset()) -> dict:
    """Per student line: proved / skipped step / wrong reason / not proved / unchecked (Lean never got there: a parse
    error, or an earlier failing tactic that ended the proof). Also the translation's structural errors (for a retry)
    and the claims whose proof failed (for single-claim proofs)."""
    errs: dict[int, dict[str, list[str]]] = {}
    parse_from, unfinished, other = n_lines, False, []
    for e in lean._errors(out, offset=0):
        m = re.match(r"line (-?\d+), col (\d+): (.*)", e, re.S)
        if not m or int(m.group(1)) not in where:
            unfinished = unfinished or "unsolved goals" in e  # Lean reports an unfinished proof on the theorem line
            other.append(e[:600])
            continue
        idx, part, by_col = where[int(m.group(1))]
        if part == "claim" and int(m.group(2)) >= by_col:
            part = "proof"
        errs.setdefault(idx, {}).setdefault(part, []).append(m.group(3)[:800])
        if re.match(r"(unexpected|expected) ", m.group(3)):
            parse_from = min(parse_from, items[idx].line)
    seen = {int(k) for k in re.findall(r"^mellow (\d+)$", out, re.M)}
    reached = {s: any(s in ls for ls in markers.values()) and all(k in seen for k, ls in markers.items() if s in ls)
               for s in range(n_lines)}
    # A typed assumption that doesn't match the goal is the student's mistake, not the translation's: never sent back
    # for a retry, where the model would "fix" it.
    structural = [f"line {items[idx].line}, {items[idx].kind} `{items[idx].text}`: {msg}"
                  for idx, parts in errs.items() for part in ("tactic", "claim", "exact") for msg in parts.get(part, [])
                  if "provided type annotation" not in msg]

    def verdict(s: int) -> str:
        mine = [idx for idx, i in enumerate(items) if i.line == s]
        parts = {p for idx in mine for p in errs.get(idx, {})}
        if s >= parse_from:
            return "unchecked"
        if parts & {"tactic", "claim", "exact", "proof"}:
            return "not proved"
        if not reached[s]:
            return "unchecked"
        if "reason" in parts:
            return "wrong reason"
        return "skipped step" if proved_by_model & set(mine) else "proved"

    return {"lines": [verdict(s) for s in range(n_lines)], "structural": structural,
            "failed_claims": sorted(idx for idx, parts in errs.items() if "proof" in parts and "claim" not in parts),
            "errors": {str(idx): parts for idx, parts in errs.items()}, "unfinished": unfinished or bool(other),
            "other_errors": other}


def translate(statement: str, lines: list[str], model: str, feedback: list[str], user: str) -> Translation | None:
    content = "Theorem statement (after `theorem target`):\n" + statement + "\n\nThe student's lines:\n" + \
        "\n".join(f"{k}: {s}" for k, s in enumerate(lines))
    if feedback:
        content += "\n\n" + RETRY.format(errors="\n".join(feedback)[:4000])
    try:
        return llm.structured_call(endpoint="lean_steps", user=user, cost_class="lean", model=model, system=SYSTEM,
                                   content=[{"type": "text", "text": content}], schema=Translation,
                                   semantic_check=lambda t: problems_check(t, len(lines), hypotheses(statement)),
                                   cache_input={"statement": statement, "lines": lines, "feedback": feedback,
                                                "prompt": _id(SYSTEM + RETRY + AUTO)},
                                   effort="high", max_tokens=16384)
    except llm.LLMError:
        return None


def prove_steps(src: str, items: list[Item], todo: list[int], errors: dict, model: str, user: str) -> dict[int, str]:
    """One call per claim, in parallel: a short think each, and one hard claim can't time out the others."""
    def one(idx: int) -> str:
        msg = "\n".join(errors.get(str(idx), {}).get("proof", []))[:2500]
        content = (f"The proof so far:\n```lean\n{src}```\n\nStep to prove:\n\nStep {idx}: "
                   f"`have {items[idx].name} : {items[idx].text}`\nLean's message:\n{msg}")
        try:
            out = llm.structured_call(endpoint="lean_step_proofs", user=user, cost_class="lean", model=model,
                                      system=PROVE_SYSTEM, content=[{"type": "text", "text": content}],
                                      schema=StepProofs, semantic_check=lambda p: proofs_check(p, {idx}),
                                      cache_input={"content": content, "prompt": _id(PROVE_SYSTEM)},
                                      effort="high", max_tokens=16384)
        except llm.LLMError:
            return ""
        return next((s.proof for s in out.proofs if s.index == idx), "")

    with ThreadPoolExecutor(max_workers=4) as pool:
        got = dict(zip(todo, pool.map(one, todo)))
    return {idx: p for idx, p in got.items() if p.strip()}


def under_contradiction(items: list[Item]) -> set[int]:
    """Items after a "suppose, for contradiction" assumption, in its block. Their context is false by design, so anything
    follows from it: a model's proof there could prove a false line by finishing the contradiction itself. Only the
    fixed automation, which reasons locally, checks them."""
    out, start = set(), None
    for idx, i in enumerate(items):
        if start is not None and i.indent < items[start].indent:
            start = None
        if start is None and (i.contradiction or (i.kind == "tactic" and i.text.split()[0] in
                                                  {"by_contra", "by_contra!", "exfalso"})):
            start = idx
        elif start is not None:
            out.add(idx)
    return out


def check(statement: str, lines: list[str], model: str, user: str) -> dict:
    t0, lean_s, retries = time.time(), 0.0, 0
    t = translate(statement, lines, model, [], user)
    if t is None:
        return {"translated": False, "lines": ["unchecked"] * len(lines), "seconds": round(time.time() - t0)}

    def run(proofs: dict[int, str]):
        nonlocal lean_s
        src, where, markers = assemble(statement, t.items, proofs)
        out, secs = lean._run(src)
        lean_s += secs
        return src, verdicts(out, where, markers, t.items, len(lines), set(proofs))

    src, v = run({})
    while v["structural"] and retries < RETRIES:
        retries += 1
        if (t2 := translate(statement, lines, model, v["structural"], user)) is None:
            break
        t = t2
        src, v = run({})
    first_pass = v["lines"]

    proofs, gave_up = {}, set()
    for _ in range(PROOF_ROUNDS):
        todo = [idx for idx in v["failed_claims"] if idx not in gave_up and idx not in under_contradiction(t.items)]
        if not todo:
            break
        got = prove_steps(src, t.items, todo, v["errors"], model, user)
        gave_up |= set(todo) - set(got)
        proofs.update(got)
        src, v = run(proofs)
    return {"translated": True, "items": [i.model_dump() for i in t.items], "retries": retries, "first_pass": first_pass, "model_proofs": len(proofs),
            "lean_seconds": round(lean_s, 1), "seconds": round(time.time() - t0), "lean": src, **v}


# ---- the problem's statement ----

FORMALIZE_SYSTEM = """You translate a first-year university math problem into a Lean 4 + Mathlib theorem statement.
Return JSON with statement: the binders and the type that come after the theorem's name, for example
"(n : ℤ) (h : Odd n) : Odd (n ^ 2)". Be faithful: exactly the problem's hypotheses, nothing weaker or stronger, no
extra assumptions, standard Mathlib definitions (Even, Odd, Nat.Prime, Irrational, Function.Injective, Set, Real.sqrt,
Finset.sum, Filter.Tendsto, ContinuousOn ...). Do not write a proof."""

IN_WORDS_SYSTEM = """You read a Lean 4 + Mathlib theorem statement and say in plain words what it asserts, for a
first-year student: every hypothesis and the conclusion, exactly as the Lean says, nothing added or left out, even if
it looks like a mistake. One or two sentences; math in $...$. Do not prove it or comment on it."""


class LeanStatement(BaseModel):
    statement: str = Field(min_length=1, max_length=2000)


class InWords(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


def state(problem: str, user: str) -> dict:
    """The problem as a Lean statement Lean accepts, and that statement read back in words by a second model, which
    sees only the Lean: the student confirms the words, so a mistranslated problem is caught before any check."""
    def typechecks(out: LeanStatement) -> list[str]:
        if SPOOF.search(out.statement):
            return ["not allowed in a statement"]
        r = lean.check_statement(out.statement)
        return ["Lean rejected the statement:\n" + "\n".join(r["errors"])[:3000]] if r["errors"] else []

    st = llm.structured_call(endpoint="lean_statement", user=user, cost_class="lean", model=config.LEAN_MODEL,
                             system=FORMALIZE_SYSTEM, content=[{"type": "text", "text": problem}], schema=LeanStatement,
                             semantic_check=typechecks, cache_input={"problem": problem, "prompt": _id(FORMALIZE_SYSTEM)},
                             effort="high", max_tokens=8192)
    words = llm.structured_call(endpoint="lean_in_words", user=user, cost_class="lean", model=config.LEAN_WORDS_MODEL,
                                system=IN_WORDS_SYSTEM, content=[{"type": "text", "text": st.statement}], schema=InWords,
                                cache_input={"statement": st.statement, "prompt": _id(IN_WORDS_SYSTEM)},
                                effort="low", max_tokens=2048)
    return {"statement": st.statement, "in_words": words.text}


# ---- what the student sees ----

MESSAGES = {
    "proved": "Lean checked this step.",
    "skipped step": "True, and Lean checked it, but it needs a step you didn't write.",
    "wrong reason": "True, but it doesn't follow from the reason you gave.",
    "not proved": "Lean couldn't verify this line. It may be wrong, or need more steps.",
    "unchecked": "Not checked: Lean stopped before this line.",
}


def summary(result: dict) -> dict:
    """The check's result for the student: each line's verdict and message, the Lean it became, and one sentence."""
    items = result.get("items", [])
    lines = [{"verdict": v, "message": MESSAGES[v],
              "lean": [i["text"] for i in items if i["line"] == k]} for k, v in enumerate(result["lines"])]
    if not result["translated"]:
        overall = "Lean couldn't translate this proof. Try writing each step as one claim per line."
    elif all(v in ("proved", "skipped step") for v in result["lines"]):
        overall = ("Every line holds, but the proof doesn't reach the conclusion yet." if result["unfinished"]
                   else "Lean checked every line, and the proof is complete.")
    else:
        bad = [str(k + 1) for k, v in enumerate(result["lines"]) if v not in ("proved", "skipped step")]
        overall = f"Look again at line{'s' if len(bad) > 1 else ''} {', '.join(bad)}."
    return {"lines": lines, "complete": result["translated"] and not result["unfinished"]
            and all(v in ("proved", "skipped step") for v in result["lines"]), "message": overall,
            "seconds": result.get("seconds", 0)}
