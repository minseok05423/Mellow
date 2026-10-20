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
5. Lean checks a claim from everything above it, which is more than a grader allows. So a line that gives a reason
   ("since g is injective", "so", "hence") must also follow from that reason alone (step 7), or it is "wrong reason".
6. When a claim (or a line from its reason) can't be proved, the model may propose values instead; we build "values
   exist where every hypothesis holds and the claim fails" from Lean's own printout of the context, and Lean must
   prove it: the line is "false", with values the student can try.
7. The reason check clears everything but the cited facts (`clear * -`), and gets the same model proof or
   counterexample as a claim. Citing one link of a chain cites the student's whole line.
8. "Suppose X" is always an assumption. If the goal doesn't offer it there (assuming the conclusion, the next case of
   an induction, a variable before the choice it depends on), the line is "not given": the student's mistake, never
   sent back to the model, which would "fix" it.
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
- reasons: for have/close, when the line gives a reason ("since ...", "because ...", "by ...") or draws a conclusion
  ("so", "hence", "thus", "therefore"), the names of every fact that reason consists of, and nothing else: the theorem's
  hypotheses, names introduced earlier (by intro or obtain), earlier claims. A reason includes what it is applied to:
  "since g is injective, f(x) = f(y)" right after "let x, y with g(f(x)) = g(f(y))" -> ["hg", "h"]. "So", "hence",
  "thus", "therefore" draw on the line(s) just before. [] when the line gives neither ("Then ...", a bare claim): it is
  then checked from everything above;
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
- "Suppose X" or "assume X" (except "suppose, for contradiction") is an assumption, never a claim: `intro (h : X)`, in
  the order the student writes it, also in an induction step and also when the goal offers no such hypothesis. Lean
  rejects an assumption that isn't given at that point (the conclusion itself, the next case of an induction, a
  variable before the choice it should come after): that is the mistake.
- Never repair the student's reasoning. "Since f is injective, f(x) = f(y)" has reasons ["hf", "h"] even when hg would
  be the right reason; "so" after a line that doesn't imply this one still cites that line. List the facts the
  student's words point to, never the ones that would make the line work: a wrong assumption, a wrong reason or a false
  claim must reach Lean exactly as written.
- Introduce the student's variables and witnesses as they do: `obtain ⟨k, hk⟩ := ha` for "a = 2k for some k", `use k + m`
  for "k + m is an integer, so ... is even", `intro ε hε` for "let ε > 0". When the goal has several parts (∃ δ > 0, ...
  is `δ > 0 ∧ ...` after `use`), split it with constructor and "·" blocks.
- A close's proposition must be exactly the goal at that point."""

RETRY = """Lean rejected the structure of your previous translation:
{errors}
Fix only how the lines are translated: tactics, syntax, names, splitting and closing goals. Keep every claim saying
exactly what the student wrote; whether a claim is true is not your concern."""

PROVE_SYSTEM = """You prove single steps inside a Lean 4 + Mathlib proof. You get the proof so far and, for each step to
prove, its index, its claim, Lean's context at that step (the variables and hypotheses in scope) and Lean's message.
Return JSON {"proofs": [...]}, each {"index": the step's index, "proof": the tactic lines that prove that claim from the
hypotheses in scope, one tactic per line}. Never use sorry, admit, axioms, native_decide, #eval, or search tactics
(exact?, apply?, simp?). Never change a claim. Prefer named Mathlib lemmas and robust tactics: obtain, rcases,
simp [lemmas], rw, ring, omega, linarith [facts], nlinarith [facts], positivity, gcongr, abs_lt, abs_sub_lt_iff,
Finset.sum_range_succ, Int.even_or_odd, ...
If the claim is false, meaning it can fail while every hypothesis in the context holds, return "" as its proof and a
counterexample instead: "counterexample": [{"name", "value"}], a value (one Lean term) for every variable in the
context, not for hypotheses, chosen so that every hypothesis holds and the claim fails; and "check": tactic lines
proving that for those values. We build `∃ <variables>, <hypotheses> ∧ ¬ (<claim>)` from Lean's context ourselves and
start the proof with `refine ⟨<your values>, ?_⟩`, so "check" proves the rest, usually `norm_num` (add lemmas such as
Int.even_iff, Nat.odd_iff as needed). Prefer small nonzero values, and avoid division by zero and natural-number
subtraction below zero, unless no other values work. If the claim may be true but you can't prove it, return "" and
no counterexample."""


class Item(BaseModel):
    line: int = Field(ge=0)
    kind: Literal["tactic", "have", "close"]
    text: str = Field(min_length=1, max_length=600)
    name: str = Field("", max_length=40)
    reasons: list[str] = Field(default_factory=list, max_length=10)
    indent: int = Field(0, ge=0, le=6)
    contradiction: bool = False


class Translation(BaseModel):
    items: list[Item] = Field(min_length=1, max_length=80)


class Value(BaseModel):
    name: str = Field(max_length=40)
    value: str = Field(max_length=200)


class StepProof(BaseModel):
    index: int
    proof: str = Field("", max_length=4000)
    counterexample: list[Value] = Field(default_factory=list, max_length=20)
    check: str = Field("", max_length=2000)


class StepProofs(BaseModel):
    proofs: list[StepProof] = Field(max_length=40)


def problems_check(t: Translation, n_lines: int, hyps: list[str]) -> list[str]:
    """Why a translation can't be used (empty if it can): every line covered, structure only, no proofs, no cheats."""
    errs = []
    if missing := sorted(set(range(n_lines)) - {i.line for i in t.items}):
        errs.append(f"student lines with no item: {missing}")
    known = set(hyps)  # names a reason may cite: in scope before the item
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
        if unknown := [r for r in i.reasons if r not in known]:
            errs.append(f"item {k}: reasons must name facts in scope before it ({', '.join(sorted(known))}), "
                        f"not {unknown}")
        known |= set(NAME.findall(i.text)) if i.kind == "tactic" else {i.name}
    return errs


def proofs_check(p: StepProofs, todo: set[int]) -> list[str]:
    errs = [f"step {s.index} wasn't asked for" for s in p.proofs if s.index not in todo]
    for s in p.proofs:
        text = "\n".join([s.proof, s.check, *(v.value for v in s.counterexample)])
        if bad := lean.FORBIDDEN.search(text) or SPOOF.search(text):
            errs.append(f"step {s.index}: not allowed: {bad.group(0)!r}")
        # A value sits inside `refine ⟨...⟩`: one term, nothing that could close the bracket or start a proof.
        if any(re.search(r"[\n⟨⟩;]|:=", v.value) for v in s.counterexample):
            errs.append(f"step {s.index}: each counterexample value must be a single Lean term")
    return errs


def context_entries(msg: str) -> list[tuple[list[str], str]] | None:
    """Lean's printed context ("a b : ℤ", "hk : a = k + k", up to "⊢") as (names, type) pairs; None when it can't be
    typed back in exactly (an inaccessible name a✝, a `let` value, an elided term ⋯). Our own `_ctx`/`_reason` blocks
    (`: True`) are left out."""
    rows: list[str] = []
    for line in msg.split("\n")[1:]:
        if line.startswith("⊢"):
            break
        if line[:1].isspace() and rows:
            rows[-1] += " " + line.strip()  # a long hypothesis wraps onto indented lines
        else:
            rows.append(line)
    out = []
    for row in rows:
        names, sep, typ = row.partition(" : ")
        names, typ = names.split(), typ.strip()
        if not sep or not names:
            return None
        if typ == "True" and all(n.startswith("_") for n in names):
            continue
        if any("✝" in n for n in names) or ":=" in typ or "⋯" in typ:
            return None
        out.append((names, typ))
    return out


def refutation_statement(claim: str, entries: list[tuple[list[str], str]], values: dict[str, str]
                         ) -> tuple[str, list[str]] | None:
    """`∃ vars, hyps ∧ ¬ claim`: values exist where every hypothesis in scope holds and the claim fails, so the claim
    doesn't follow from them. Built from Lean's own context, never from the model. Entries the model gave values for
    are bound; the rest must hold as conditions. (A variable left without a value becomes a nonsense condition, so
    Lean rejects the statement: nothing unsound gets through.)"""
    binders, conds, vals = [], [], []
    for names, typ in entries:
        if all(n in values for n in names):
            binders.append(f"({' '.join(names)} : {typ})")
            vals += [values[n] for n in names]
        elif any(n in values for n in names):
            return None
        else:
            conds.append(f"({typ})")
    body = " ∧ ".join([*conds, f"¬ ({claim})"])
    return (f"∃ {' '.join(binders)}, {body}" if binders else body), vals


def proved_decls(out: str) -> set[str]:
    """Theorems that `#print axioms` shows were proved with Lean's standard axioms only (no sorry)."""
    done = set()
    for m in re.finditer(r"'([\w.]+)' (?:depends on axioms: \[(.*?)\]|does not depend on any axioms)", out, re.S):
        if {a.strip() for a in (m.group(2) or "").split(",") if a.strip()} <= lean.STANDARD_AXIOMS:
            done.add(m.group(1))
    return done


def hypotheses(statement: str) -> list[str]:
    """Names bound in the statement's binders, before its top-level colon: `(a b : ℤ) (ha : Even a)` -> a, b, ha."""
    depth = 0
    for k, ch in enumerate(statement):
        depth += (ch in "([{⟨") - (ch in ")]}⟩")
        if ch == ":" and depth == 0:
            statement = statement[:k]
            break
    return [n for group in re.findall(r"[({\[]\s*([^(){}\[\]:]+?)\s*:", statement) for n in group.split() if n.isidentifier()]


def assemble(statement: str, items: list[Item], proofs: dict[int, str] | None = None,
             refutations: dict[int, dict] | None = None, trivial: bool = False,
             reason_proofs: dict[int, str] | None = None, reason_refutations: dict[int, dict] | None = None
             ) -> tuple[str, dict[int, tuple[int, str, int]], dict[int, list[int]]]:
    """The Lean file; for each proof line (1-based, as Lean counts) its (item, part, column where the claim's proof
    starts); and which student lines each `trace "mellow k"` marker vouches for. A failing tactic can make Lean skip the
    rest of the proof without an error on those lines, so a line only counts once Lean has printed the markers after it.
    `proofs` replaces AUTO for single claims (step 4 above). Before every claim, Lean prints its context (step 6).
    `refutations` adds counterexamples (step 6) and `trivial` asks whether AUTO proves the whole theorem alone: both
    come after the theorem, as theorems of their own. `reason_proofs` and `reason_refutations` do the same for the
    check that a line follows from its reason (step 7)."""
    proofs, refutations = proofs or {}, refutations or {}
    reason_proofs, reason_refutations = reason_proofs or {}, reason_refutations or {}
    body, where, markers, pending = [], {}, {}, set()
    chains = chain_links(items)
    defined: set[str] = set()  # claims already in scope

    def emit(indent: int, text: str, idx: int, part: str, by_col: int = 0):
        where[HEAD_LINES + len(body) + 1] = (idx, part, by_col)
        body.append("  " * (indent + 1) + text)

    def faith(indent: int, entries: list, idx: int, part: str):
        # Each hypothesis as we re-typed it from Lean's printout must be the real one, or a counterexample might
        # satisfy a weaker version of it.
        emit(indent, "have _faith : True := by", idx, part)
        for names, typ in entries:
            for n in names:
                emit(indent + 1, f"have _ : {typ} := {n}", idx, part)
        emit(indent + 1, "trivial", idx, part)

    for idx, i in enumerate(items):
        pad = len("  " * (i.indent + 1))
        if i.kind == "tactic":
            emit(i.indent, i.text, idx, "tactic")
        else:
            # An error message is how Lean prints the context at a line; inside `True`, so nothing is left behind.
            emit(i.indent, f'have _ctx{idx} : True := by fail "mellow ctx"', idx, "ctx")
            if idx in refutations:
                faith(i.indent, refutations[idx]["entries"], idx, "faith")
            if i.reasons:
                # The line must follow from the facts the student gives as its reason alone: everything else is
                # cleared. Inside a `True` block, so neither its result nor its failure leaves anything behind.
                # Like a claim, it gets the model's proof or counterexample when the automation fails (step 7).
                # Citing one link of a chain ("c = bm = (ak)m = a(km)") cites the chain up to it.
                cited = list(dict.fromkeys(n for r in i.reasons for n in chains.get(r, [r]) if n == r or n in defined))
                emit(i.indent, f"have _reason{idx} : True := by", idx, "reason")
                emit(i.indent + 1, f"have _r : {i.text} := by", idx, "reason")
                emit(i.indent + 2, f"clear * - {' '.join(cited)}", idx, "reason")
                emit(i.indent + 2, 'have _rctx : True := by fail "mellow ctx"', idx, "rctx")
                if idx in reason_refutations:
                    faith(i.indent + 2, reason_refutations[idx]["entries"], idx, "rfaith")
                for line in (textwrap.dedent(reason_proofs[idx].strip("\n")).splitlines() if idx in reason_proofs
                             else [AUTO]):
                    emit(i.indent + 2, line.rstrip(), idx, "reason")
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
            defined.add(i.name)
        pending.add(i.line)
        text = i.text.rstrip()
        if text.endswith("with"):
            continue  # `induction n with` must be followed by its cases; the next marker vouches for it
        k = len(markers)
        markers[k] = sorted(pending)
        # A block opener's marker goes inside the block, as its first tactic.
        emit(i.indent + (text.endswith("=>") or text == "·"), f'trace "mellow {k}"', idx, "marker")
        pending = set()
    # `#print axioms` vouches that each of these was really proved, without sorry.
    for prefix, refs in (("cex", refutations), ("rcex", reason_refutations)):
        for idx, r in refs.items():
            if (st := refutation_statement(items[idx].text, r["entries"], r["values"])) is None:
                continue
            claim, values = st
            emit(-1, f"theorem {prefix}_{idx} : {claim} := by", idx, prefix)
            if values:
                emit(0, "refine ⟨" + ", ".join(f"({v})" for v in values) + ", ?_⟩", idx, prefix)
            for line in textwrap.dedent(r["check"].strip("\n")).splitlines():
                emit(0, line.rstrip(), idx, prefix)
            emit(-1, f"#print axioms {prefix}_{idx}", idx, prefix)
    if trivial:
        emit(-1, f"theorem trivial_check {statement} := by {AUTO}", -1, "trivial")
        emit(-1, "#print axioms trivial_check", -1, "trivial")
    head =f"import Mathlib\n\nset_option maxHeartbeats 1000000\n\ntheorem target {statement} := by\n"
    return head + "\n".join(body) + "\n", where, markers


RELATION = re.compile(r"\s(=|≤|<|≥|>)\s")


def chain_links(items: list[Item]) -> dict[str, list[str]]:
    """For each claim, the chain it ends: claims continued from the claim before (its left side is the previous right
    side: `n ^ 2 = (2 * k + 1) ^ 2`, then `(2 * k + 1) ^ 2 = ...`, even on the student's next line). A student who cites
    the end of a chain means all of it. Not every claim on the same line: in "x ∉ A ∪ B, so x ∉ A or x ∉ B", a later
    "so" draws on the second claim only."""
    def sides(text: str) -> tuple[str, str]:
        parts = RELATION.split(text, maxsplit=1)
        return (parts[0], parts[2]) if len(parts) == 3 else ("", "")

    chains, prev = {}, None
    for i in items:
        if i.kind == "tactic":
            continue
        continued = prev is not None and sides(i.text)[0] and \
            " ".join(sides(prev.text)[1].split()) == " ".join(sides(i.text)[0].split())
        chains[i.name] = (chains[prev.name] if continued else []) + [i.name]
        prev = i
    return chains


def intro_names(text: str) -> list[str]:
    """Names an intro introduces: `intro x y (h : x = y)` -> x, y, h."""
    if not re.match(r"(intro|intros|rintro)\b", text):
        return []
    rest = text.split(None, 1)[1] if " " in text else ""
    names = re.findall(r"\(\s*([^\s():]+)\s*:", rest)
    names += [w for w in re.sub(r"\([^()]*\)", " ", rest).split() if NAME.fullmatch(w)]
    return names


def verdicts(out: str, where: dict, markers: dict[int, list[int]], items: list[Item], n_lines: int,
             proved_by_model: set[int] = frozenset(), given: set[str] = frozenset()) -> dict:
    """Per student line: proved / skipped step / wrong reason / false (a counterexample Lean checked) / not proved /
    unchecked (Lean never got there: a parse error, or an earlier failing tactic that ended the proof). Also the
    translation's structural errors (for a retry), the claims whose proof failed (for single-claim proofs), each
    claim's context as Lean printed it, and whether the theorem is trivial for AUTO. "not given": the student assumes
    or introduces something the goal doesn't offer there (step 8); `given` are the statement's own names, whose
    re-introduction is the translation's slip, not the student's."""
    errs: dict[int, dict[str, list[str]]] = {}
    contexts: dict[int, str] = {}
    reason_contexts: dict[int, str] = {}
    unfaithful: set[int] = set()
    unfaithful_reason: set[int] = set()
    not_given: set[int] = set()
    parse_from, unfinished, other = n_lines, False, []
    for e in lean._errors(out, offset=0):
        m = re.match(r"line (-?\d+), col (\d+): (.*)", e, re.S)
        if not m or int(m.group(1)) not in where:
            unfinished = unfinished or "unsolved goals" in e  # Lean reports an unfinished proof on the theorem line
            other.append(e[:600])
            continue
        idx, part, by_col = where[int(m.group(1))]
        if part == "ctx":
            if m.group(3).startswith("mellow ctx"):
                contexts[idx] = m.group(3)
                continue
            part = "tactic"  # e.g. "no goals": the claim comes after the proof was closed, a structural error
        if part == "rctx":
            if m.group(3).startswith("mellow ctx"):
                reason_contexts[idx] = m.group(3)
                continue
            part = "reason"
        if part in ("faith", "cex", "trivial"):  # judged by `#print axioms` below, never the student's lines
            unfaithful.add(idx)
            continue
        if part in ("rfaith", "rcex"):
            unfaithful_reason.add(idx)
            continue
        if part == "claim" and int(m.group(2)) >= by_col:
            part = "proof"
        # An assumption that doesn't match the goal, or nothing to introduce there: the student's mistake, unless the
        # translation re-introduced the statement's own variables. Never sent back for a retry, where it would be "fixed".
        if part == "tactic" and re.search(r"provided type annotation|no additional binders", m.group(3)) and \
                not set(intro_names(items[idx].text)) <= set(given):
            not_given.add(idx)
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
                  if idx not in not_given]
    decls = proved_decls(out)
    refuted = {int(d[4:]) for d in decls if d.startswith("cex_")} - unfaithful
    reason_refuted = {int(d[5:]) for d in decls if d.startswith("rcex_")} - unfaithful_reason

    def verdict(s: int) -> str:
        mine = [idx for idx, i in enumerate(items) if i.line == s]
        parts = {p for idx in mine for p in errs.get(idx, {})}
        if s >= parse_from:
            return "unchecked"
        if not_given & set(mine):
            return "not given"
        if parts & {"tactic", "claim", "exact", "proof"}:
            return "false" if refuted & set(mine) else "not proved"
        if not reached[s]:
            return "unchecked"
        if "reason" in parts:
            return "wrong reason"
        return "skipped step" if proved_by_model & set(mine) else "proved"

    return {"lines": [verdict(s) for s in range(n_lines)], "structural": structural,
            "failed_claims": sorted(idx for idx, parts in errs.items() if "proof" in parts and "claim" not in parts),
            "failed_reasons": sorted(idx for idx, parts in errs.items() if "reason" in parts),
            "errors": {str(idx): parts for idx, parts in errs.items()}, "unfinished": unfinished or bool(other),
            "other_errors": other, "contexts": contexts, "refuted": sorted(refuted),
            "reason_contexts": reason_contexts, "reason_refuted": sorted(reason_refuted),
            "trivial": "trivial_check" in decls}


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


def prove_steps(src: str, items: list[Item], jobs: list[tuple[int, str]], v: dict, model: str, user: str
                ) -> dict[tuple[int, str], tuple[str, dict | None]]:
    """One call per job, in parallel: a short think each, and one hard claim can't time out the others. A job is a
    claim the automation couldn't prove ("claim"), or a line that didn't follow from its reason alone ("reason", with
    everything else cleared). Each gets a proof, or a counterexample (step 6), or neither: {job: (proof, refutation)}."""
    def one(job: tuple[int, str]) -> StepProof | None:
        idx, kind = job
        contexts = v["contexts"] if kind == "claim" else v["reason_contexts"]
        msg = "\n".join(v["errors"].get(str(idx), {}).get("proof" if kind == "claim" else "reason", []))[:2500]
        ctx = "\n".join(contexts.get(idx, "").split("\n")[1:-1])  # without our "mellow ctx" line and "⊢ True"
        step = (f"`have {items[idx].name} : {items[idx].text}`" if kind == "claim" else
                f"`have _r : {items[idx].text}` inside the block `_reason{idx}`, from only the facts left after "
                f"`clear * - {' '.join(items[idx].reasons)}`")
        content = (f"The proof so far:\n```lean\n{src}```\n\nStep to prove:\n\nStep {idx}: {step}\n"
                   f"Lean's context at this step:\n{ctx}\nLean's message:\n{msg}")
        try:
            out = llm.structured_call(endpoint="lean_step_proofs", user=user, cost_class="lean", model=model,
                                      system=PROVE_SYSTEM, content=[{"type": "text", "text": content}],
                                      schema=StepProofs, semantic_check=lambda p: proofs_check(p, {idx}),
                                      cache_input={"content": content, "prompt": _id(PROVE_SYSTEM)},
                                      effort="high", max_tokens=16384)
        except llm.LLMError:
            return None
        return next((s for s in out.proofs if s.index == idx), None)

    with ThreadPoolExecutor(max_workers=4) as pool:
        got = dict(zip(jobs, pool.map(one, jobs)))
    out = {}
    for (idx, kind), s in got.items():
        if not s:
            continue
        if s.proof.strip():
            out[(idx, kind)] = (s.proof, None)
        elif s.counterexample and (entries := context_entries(
                (v["contexts"] if kind == "claim" else v["reason_contexts"]).get(idx, ""))):
            out[(idx, kind)] = ("", {"entries": entries, "values": {x.name: x.value.strip() for x in s.counterexample},
                                     "check": s.check.strip() or "norm_num"})
    return out


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

    proofs, refutations, reason_proofs, reason_refutations = {}, {}, {}, {}

    def run(trivial: bool = False):
        nonlocal lean_s
        src, where, markers = assemble(statement, t.items, proofs, refutations, trivial, reason_proofs,
                                       reason_refutations)
        out, secs = lean._run(src)
        lean_s += secs
        return src, verdicts(out, where, markers, t.items, len(lines), set(proofs), set(hypotheses(statement)))

    src, v = run(trivial=True)
    trivial = v["trivial"]
    while v["structural"] and retries < RETRIES:
        retries += 1
        if (t2 := translate(statement, lines, model, v["structural"], user)) is None:
            break
        t = t2
        src, v = run()
    first_pass = v["lines"]

    gave_up: set[tuple[int, str]] = set()
    for _ in range(PROOF_ROUNDS):
        contra = under_contradiction(t.items)
        jobs = [job for job in [*((idx, "claim") for idx in v["failed_claims"]),
                                *((idx, "reason") for idx in v["failed_reasons"])]
                if job not in gave_up and job[0] not in contra]
        if not jobs:
            break
        got = prove_steps(src, t.items, jobs, v, model, user)
        gave_up |= set(jobs) - {job for job, (proof, _) in got.items() if proof}
        for (idx, kind), (proof, ref) in got.items():
            (proofs if kind == "claim" else reason_proofs).update({idx: proof} if proof else {})
            (refutations if kind == "claim" else reason_refutations).update({idx: ref} if ref else {})
        src, v = run()
    counterexamples = {t.items[idx].line: refutations[idx]["values"] for idx in v["refuted"]}
    reason_counterexamples = {t.items[idx].line: reason_refutations[idx]["values"] for idx in v["reason_refuted"]}
    return {"translated": True, "items": [i.model_dump() for i in t.items], "retries": retries, "first_pass": first_pass, "model_proofs": len(proofs),
            "lean_seconds": round(lean_s, 1), "seconds": round(time.time() - t0), "lean": src, **v,
            "trivial": trivial, "counterexamples": counterexamples, "reason_counterexamples": reason_counterexamples,
            "contradiction_lines": sorted({t.items[idx].line for idx in under_contradiction(t.items)})}


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

RESULT_VERSION = 4  # part of /lean/check's cache key: bump when what the student sees changes meaning

MESSAGES = {
    "proved": "",
    "skipped step": "Follows, but Lean needed a step you didn't write here. Can you add it?",
    "wrong reason": "Right claim, wrong reason: it doesn't follow from the reason you gave.",
    "wrong reason with values": "Right claim, wrong reason: your reason also allows {values}, where this line fails.",
    "false": "False as written: it fails for {values}.",
    "not proved": "Lean couldn't get from your earlier lines to this one. Check it, or add the step in between.",
    "not given": "You assume or introduce something that isn't given at this point. Is it what you're proving, or does "
                 "it come too early?",
    "unchecked": "Not checked: Lean stopped before this line.",
}
# Lean's conventions (1/0 = 0, 0 - 1 = 0 in ℕ) make a claim "false" at 0 that the student meant to exclude.
EDGE = "False as written when {values}. Did you mean to rule that out?"


def _plain(value: str) -> str:
    """A Lean value as the student would write it: `(1 : ℤ)` -> `1`."""
    return re.sub(r"^\((.*?)\s*:\s*[^()]*\)$", r"\1", value.strip())


def _lines_text(ks: list[int]) -> str:
    return f"line{'s' if len(ks) > 1 else ''} {', '.join(str(k + 1) for k in ks)}"


def summary(result: dict) -> dict:
    """The check's result for the student: each line's verdict and message, the Lean it became, and one sentence.
    Honest about what a ✓ means: a gap the model filled is never "complete", and a ✓ that rests on an unverified line
    says so."""
    items, verdicts_ = result.get("items", []), result["lines"]
    lines, unverified = [], None
    for k, v in enumerate(verdicts_):
        msg = MESSAGES[v]
        if v == "false":
            vals = {n: _plain(x) for n, x in result["counterexamples"].get(k, {}).items()}
            msg = (EDGE if "0" in vals.values() else msg).format(values=", ".join(f"{n} = {x}" for n, x in vals.items()))
        if v == "wrong reason" and (vals := result.get("reason_counterexamples", {}).get(k)):
            msg = MESSAGES["wrong reason with values"].format(
                values=", ".join(f"{n} = {_plain(x)}" for n, x in vals.items()))
        if v in ("proved", "skipped step") and unverified is not None:
            msg = f"{msg} It follows from the lines above, but line {unverified + 1} isn't verified.".strip()
        if v in ("false", "not proved", "not given") and unverified is None:
            unverified = k
        lines.append({"verdict": v, "message": msg, "lean": [i["text"] for i in items if i["line"] == k]})

    bad = [k for k, v in enumerate(verdicts_) if v not in ("proved", "skipped step")]
    gaps = [k for k, v in enumerate(verdicts_) if v == "skipped step"]
    if not result["translated"]:
        overall = "Lean couldn't translate this proof. Try writing each step as one claim per line."
    elif bad:
        false = [k for k in bad if verdicts_[k] == "false"]
        rest = [k for k in bad if verdicts_[k] != "false"]
        overall = " ".join([f"{_lines_text(false).capitalize()} {'is' if len(false) == 1 else 'are'} false as written."
                            if false else "", f"Look again at {_lines_text(rest)}." if rest else ""]).strip()
    elif gaps:
        overall = f"Every line holds, but {_lines_text(gaps)} skip{'s' if len(gaps) == 1 else ''} a step."
    elif result["unfinished"]:
        overall = "Every line holds, but the proof doesn't reach the conclusion yet."
    else:
        overall = "Lean checked every line, and the proof is complete."
    if result.get("trivial") and not bad:
        overall += " Lean proves this statement on its own, so it can't judge whether your steps are detailed enough."
    if any(verdicts_[k] in ("not proved", "unchecked") for k in result.get("contradiction_lines", [])):
        overall += (" Lean checks little inside a proof by contradiction: there, \"?\" means it couldn't verify a "
                    "line, not that the line is wrong.")
    complete = result["translated"] and not result["unfinished"] and not bad and not gaps
    return {"lines": lines, "complete": complete, "message": overall, "seconds": result.get("seconds", 0)}
