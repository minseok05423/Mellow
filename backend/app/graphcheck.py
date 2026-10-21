"""Check a student's proof as a graph of steps, each step on its own (DECISIONS #32).

1. `make_graph`: the model turns the confirmed lines into nodes (the problem's givens, then each claim, assumption or
   choice the student writes) with premises (the earlier nodes a step rests on), the variables in the order they are
   introduced, and the order of the goal's quantifiers. The student confirms the graph before anything is judged.
   shortcut: one model call stands in for "rules + a small classifier". Upgrade path: train the classifier on the
   graphs students confirmed.
2. Rules on the confirmed graph find two mistakes without a model: a name that stands for two objects in one step
   (reusing k for a second witness), and a choice that depends on a variable the goal quantifies after it (δ chosen
   from x when the goal picks δ before x).
3. Every other step is checked alone, by the cheapest method that works:
   - Tested: the model writes the step's equations, with its premises as substitutions; SymPy evaluates them at random
     values. A failing value is a counterexample (✗); all passing is ✓ Tested.
   - Proved: the model writes the step as a Lean statement whose hypotheses are its premises, and our fixed automation
     must prove it. A failure isn't evidence the step is wrong, so the step moves on.
   - Judged: three judges mark the step alone against a marking scheme; ✓ only when all three accept.
Each verdict carries its tier, so the student knows how far to trust it: only Proved is certain.
"""
from __future__ import annotations

import cmath
import keyword
import random
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

import sympy
from pydantic import BaseModel
from sympy.parsing.sympy_parser import convert_xor, parse_expr, standard_transformations

from . import config, lean, llm, store
from .leancheck import AUTO, _id

JUDGES = 3
POINTS = 40  # random values per tested step
MIN_POINTS = 8  # values that must evaluate (inside the domain) for a pass to count
RESULT_VERSION = 3  # part of /check's cache key: bump when what the student sees changes meaning

GRAPH_SYSTEM = """You turn a student's handwritten proof into a graph of steps, so each step can be checked on its own.
You never judge or repair the proof: record what the student wrote, mistakes included.
Return JSON with:
- goal: what the problem asks to show, in words, math in $...$;
- goal_order: the goal's quantified variables in the order the goal quantifies them, each {"name", "quantifier":
  "forall" | "exists"}, using the student's names for them ("for every ε > 0 there is δ > 0 such that for all x ..." ->
  ε forall, δ exists, x forall). [] when the goal has no quantifiers worth tracking;
- nodes, numbered from 0, in order:
  - first, one "given" node per hypothesis of the problem (line null, premises []);
  - then, in the student's order, one node per claim, assumption or choice the student writes, with the index of the
    student line it comes from. Every student line gets at least one node. A line with several claims ("so x ∉ A and
    x ∉ B, so x ∈ Aᶜ ∩ Bᶜ") gives several nodes; a chain "c = bm = (ak)m = a(km)" is one node.
    kind "assume": "suppose", "assume", "let x ∈ S", "let ε > 0", a case ("if x < y"), an induction hypothesis, "induction on n";
    kind "choose": the student picks a value ("let δ = min(1, ε/3)", "take k = m + 1");
    kind "claim": everything the student asserts.
  - text: the node's statement as the student wrote it, math in $...$, without the reason ("Since a is even, a = 2k for
    some integer k" -> "$a = 2k$ for some integer $k$");
  - premises: the earlier nodes the student's step rests on: the reason they give ("since a is even" -> the node saying
    a is even), the line before a "so", "then", "hence" or "thus", and earlier facts the step plainly uses. Only earlier
    nodes. A conclusion that discharges assumptions ("so g∘f is injective" after "let x, y with ...") rests on those
    assumptions and what was derived from them. Point to the facts the student's words point to, never the ones that
    would make the step work.
- variables: every variable, in the order introduced: "given" (fixed by the problem, like the a and b of "if a and b
  are even", introduced_at null), "any" (let x be arbitrary, let ε > 0: a variable the goal quantifies is introduced by
  the student's step, not given), "chosen" (the student sets its value: δ = ...), "obtained" ("for some integer k",
  "there is"). introduced_at: the node that introduces it. List each introduction once. depends_on: for a chosen value, the variables in its formula; for an
  obtained one, the variables of the fact it comes from; [] otherwise. If the student reuses a name for a new object
  ("c = bk for some integer k" when k is already in use), list it again: never merge two introductions;
- concludes: the node whose statement is the goal, or null if no node states it."""

FORMS_SYSTEM = """You prepare single steps of a student's proof for mechanical checks. For each step you get its claim and
its premises (the earlier facts it rests on). Return {"forms": [...]}, one per step:
- node: the step's number;
- variables: every variable in your substitute and equations, each {"name", "domain": "integer" | "natural" | "real"}
  ("real" unless the problem makes it an integer or a natural number);
- substitute: equations from the step's premises that give a variable's value, {"lhs": the variable, "rhs": its
  expression}: premise "a = 2k for some integer k" -> {"lhs": "a", "rhs": "2*k"}; "n is odd" -> {"lhs": "n", "rhs":
  "2*j + 1"} with j a new integer variable. Only from this step's premises;
- given: every other equation or inequality the step's premises state about its variables, {"lhs", "rel": "=" | "!=" |
  "<" | "<=" | ">" | ">=", "rhs"}: premise "n^2 = (2k + 1)^2" -> {"lhs": "n^2", "rel": "=", "rhs": "(2*k + 1)^2"};
  "x > 0" -> {"lhs": "x", "rel": ">", "rhs": "0"}. Random values are only tried where every given holds;
- equations: when the claim is one or more equations, each {"lhs", "rhs"} exactly as the student wrote it; a chain
  "c = bm = (ak)m" is consecutive pairs (c, b*m), (b*m, (a*k)*m). [] when the claim isn't equations (inequalities,
  divisibility, parity, sets, logic, sums with "...", "for some k"), and [] when the equations rely on a premise that
  substitute and given can't express (p is prime, x ∈ A, f is injective);
  The equations must be the whole claim: when the claim says more ("n^2 = 4k^2 + 4k + 1, so n^2 is odd"), [];
- lean: the step on its own as a Lean 4 + Mathlib statement, written as it would follow `theorem name`: binders for the
  variables, one hypothesis per premise saying exactly what that premise says, then `:` and the whole claim. Use ℝ
  whenever the step divides, and ℤ rather than ℕ whenever it subtracts: ℤ division rounds and ℕ subtraction stops at
  0, which can make a false step true. "" when the step can't be stated simply.
Expressions in substitute, given and equations: ASCII, explicit * for multiplication, ^ for powers, sqrt(), Abs(),
factorial(), binomial(); the student's variable names, Greek letters spelled out (delta, eps).
Be faithful: write exactly what the student claims, even if it is false. Never fix a mistake, never add a hypothesis
that isn't a premise, never strengthen or weaken a claim. A false step must stay false: that is how mistakes are found."""

JUDGE_SYSTEM = """You mark one step of a student's proof, on its own, for a first-year university proofs course. You see
the goal, the variables so far and how each was introduced, the facts the step rests on (its premises), and the step.
Marking scheme: the step earns the mark only if every point that applies holds.
1. A claim follows from its premises, with the algebra, arithmetic and definitions a first-year course takes for
   granted, by a step a grader would accept without asking for more. A true claim reached by an inference that doesn't
   work earns nothing; neither does a premise used in a way it doesn't support. A claim that concludes a "for all" or
   an "if ... then" rests on the assumptions it discharges, listed under assumptions in force: it earns the mark when
   its premises derive, from those assumptions, what the conclusion needs.
2. An assumption ("suppose", "let x be", a case, an induction hypothesis) is one the proof is entitled to here: an
   arbitrary element or the hypothesis of what is being shown, one case of a split that covers every possibility, the
   induction hypothesis for n (not for n + 1), or the negation of the goal for a contradiction. Assuming what is to be
   shown, or something stronger, earns nothing. "Let x" without naming its set is fine when the goal makes clear
   what x ranges over.
3. A choice ("let δ = ...", "take k = ...") depends only on variables introduced before it that the goal lets it
   depend on, and is a legal value (δ > 0, an integer where an integer is needed).
4. Each name means one thing: a name already in use isn't reused for a new object.
Be strict: when you're unsure a grader would accept the step, it doesn't earn the mark. Judge only this step; the
premises are taken as true.
Return {"ok": true if the step earns the mark, "reason": ""} or {"ok": false, "reason": one sentence the student can act
on, saying what is wrong without giving the fix}."""


class Quantified(BaseModel):
    name: str
    quantifier: Literal["forall", "exists"]


class Node(BaseModel):
    id: int
    kind: Literal["given", "assume", "choose", "claim"]
    line: int | None  # the student's line; None for the problem's givens
    text: str
    premises: list[int]


class Variable(BaseModel):
    name: str
    kind: Literal["given", "any", "chosen", "obtained"]
    introduced_at: int | None  # the node that introduces it; None for the problem's own
    depends_on: list[str]


class Graph(BaseModel):
    goal: str
    goal_order: list[Quantified]
    nodes: list[Node]
    variables: list[Variable]
    concludes: int | None


class Equation(BaseModel):
    lhs: str
    rhs: str


class Relation(BaseModel):
    lhs: str
    rel: Literal["=", "!=", "<", "<=", ">", ">="]
    rhs: str


class Domain(BaseModel):
    name: str
    domain: Literal["integer", "natural", "real"]


class Form(BaseModel):
    node: int
    variables: list[Domain]
    substitute: list[Equation]
    given: list[Relation]  # the premises' other facts: a random value counts only where they all hold
    equations: list[Equation]
    lean: str


class Forms(BaseModel):
    forms: list[Form]


class Mark(BaseModel):
    ok: bool
    reason: str


class NodeVerdict(BaseModel):
    node: int
    status: Literal["ok", "wrong", "unsure"]
    tier: Literal["proved", "tested", "judged"]
    message: str
    detail: str  # what decided it: the Lean statement, the values tried, or the judges' count


def graph_problems(g: Graph, n_lines: int | None) -> list[str]:
    """Structural errors in a graph (the model's, for a retry; or a client's, rejected). None: don't check coverage."""
    errs = []
    if [n.id for n in g.nodes] != list(range(len(g.nodes))):
        errs.append("node ids must be 0, 1, 2, ... in order")
    for i, n in enumerate(g.nodes):
        if n.kind == "given" and (n.line is not None or n.premises):
            errs.append(f"node {n.id}: a given has line null and no premises")
        if n.kind != "given" and (n.line is None or n.line < 0 or (n_lines is not None and n.line >= n_lines)):
            errs.append(f"node {n.id}: line must be one of the student's lines")
        if any(not 0 <= p < i for p in n.premises):
            errs.append(f"node {n.id}: premises must be earlier nodes")
    if n_lines is not None and (missing := set(range(n_lines)) - {n.line for n in g.nodes}):
        errs.append(f"every student line needs a node; missing lines {sorted(missing)}")
    if g.concludes is not None and not 0 <= g.concludes < len(g.nodes):
        errs.append("concludes must be a node id or null")
    if any(v.introduced_at is not None and not 0 <= v.introduced_at < len(g.nodes) for v in g.variables):
        errs.append("introduced_at must be a node id or null")
    return errs


def make_graph(problem: str, lines: list[str], user: str) -> Graph:
    text = f"Problem:\n{problem}\n\nThe student's proof, line by line:\n" + "\n".join(f"{i}: {s}" for i, s in enumerate(lines))
    return llm.structured_call(
        endpoint="graph", user=user, cost_class="check", model=config.CHECK_MODEL, system=GRAPH_SYSTEM,
        content=[{"type": "text", "text": text}], schema=Graph,
        cache_input={"problem": problem, "lines": lines, "prompt": _id(GRAPH_SYSTEM)},
        semantic_check=lambda g: graph_problems(g, len(lines)), max_tokens=8000)


def verdict(node: int, status: str, tier: str, message: str = "", detail: str = "") -> NodeVerdict:
    return NodeVerdict(node=node, status=status, tier=tier, message=message, detail=detail)


# ---- rules -------------------------------------------------------------------------------------------------------

WORD = re.compile(r"[^\W\d_]+")


def mentions(text: str, name: str, letters: set[str]) -> bool:
    """Does the text use this variable? Math runs letters together ("ak" is a times k), so a run of one-letter
    variable names counts too."""
    text = re.sub(r"\\[A-Za-z]+", " ", text)  # LaTeX commands aren't variables
    if not WORD.fullmatch(name):
        return name in text
    return any(run == name or (name in run and set(run) <= letters) for run in WORD.findall(text))


def ancestors(g: Graph, i: int) -> set[int]:
    seen, todo = set(), [i]
    while todo:
        if (n := todo.pop()) not in seen:
            seen.add(n)
            todo += g.nodes[n].premises
    return seen


def rule_verdicts(g: Graph) -> dict[int, NodeVerdict]:
    out: dict[int, NodeVerdict] = {}
    letters = {v.name for v in g.variables if len(v.name) == 1}
    names = sorted({v.name for v in g.variables})
    for n in g.nodes:
        up = ancestors(g, n.id)
        for name in names:
            intro = sorted({-1 if v.introduced_at is None else v.introduced_at for v in g.variables
                            if v.name == name and (v.introduced_at is None or v.introduced_at in up)})
            if len(intro) > 1 and mentions(n.text, name, letters):
                where = " and ".join("the problem" if i < 0 else f"step {i}" for i in intro)
                out.setdefault(n.id, verdict(n.id, "wrong", "tested",
                                             f"${name}$ stands for two different things here ({where}). "
                                             "A new object needs a new name.", "rule: one name, one object"))
                letters.discard(name)  # report each clash once, where it first matters
                names.remove(name)
                break
    order = [q.name for q in g.goal_order]
    for v in g.variables:
        if v.kind == "chosen" and v.name in order and v.introduced_at is not None:
            late = [d for d in v.depends_on if d in order and order.index(d) > order.index(v.name)]
            if late:
                out.setdefault(v.introduced_at, verdict(
                    v.introduced_at, "wrong", "tested",
                    f"${v.name}$ depends on ${late[0]}$, but the goal picks ${v.name}$ before ${late[0]}$, "
                    f"so ${v.name}$ can't depend on it.", "rule: the goal's quantifier order"))
    return out


# ---- Tested: SymPy at random values --------------------------------------------------------------------------------

FUNCS = {"sqrt": sympy.sqrt, "Abs": sympy.Abs, "factorial": sympy.factorial, "binomial": sympy.binomial,
         "Min": sympy.Min, "Max": sympy.Max, "floor": sympy.floor, "ceiling": sympy.ceiling}
SAFE = re.compile(r"[A-Za-z0-9_\s+\-*/^(),]*")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def parse(s: str, names: list[str]) -> sympy.Expr:
    """parse_expr calls eval, so only numbers, + - * / ^ ( ) , the declared names and FUNCS get through: no dots,
    quotes, brackets or colons, no Python keywords (for, in, lambda, ...), and no builtins in eval's globals."""
    if not SAFE.fullmatch(s) or "__" in s:
        raise ValueError(f"{s!r}: use only numbers, + - * / ^ ( ) , and names")
    if bad := {w for w in IDENT.findall(s) if keyword.iskeyword(w)} | {n for n in names if keyword.iskeyword(n)}:
        raise ValueError(f"{s!r}: Python keywords can't be names: {sorted(bad)}")
    if unknown := set(IDENT.findall(s)) - set(names) - set(FUNCS):
        raise ValueError(f"{s!r}: names not in variables: {sorted(unknown)}")
    try:
        return parse_expr(s, local_dict={**FUNCS, **{n: sympy.Symbol(n) for n in names}},
                          global_dict={"__builtins__": {}, "Integer": sympy.Integer, "Float": sympy.Float,
                                       "Rational": sympy.Rational, "Symbol": sympy.Symbol},
                          transformations=standard_transformations + (convert_xor,))
    except Exception as e:
        raise ValueError(f"{s!r}: {type(e).__name__}") from None


def forms_problems(f: Forms, todo: set[int]) -> list[str]:
    errs = []
    for form in f.forms:
        names = [v.name for v in form.variables]
        if form.node not in todo:
            errs.append(f"node {form.node} isn't one of the steps asked for")
        if bad := [n for n in names if not IDENT.fullmatch(n) or n in FUNCS]:
            errs.append(f"node {form.node}: variable names must be ASCII identifiers: {bad}")
            continue
        for e in form.substitute:
            if e.lhs not in names:
                errs.append(f"node {form.node}: substitute lhs {e.lhs!r} must be one of its variables")
        for e in form.substitute + form.given + form.equations:
            for side in (e.lhs, e.rhs):
                try:
                    parse(side, names)
                except ValueError as err:
                    errs.append(f"node {form.node}: {err}")
    return errs


def _value(domain: str, rng: random.Random):
    if domain == "natural":
        return sympy.Integer(rng.randint(0, 9))
    if domain == "integer":
        return sympy.Integer(rng.randint(-6, 6))
    return sympy.Rational(rng.randint(-12, 12), rng.randint(1, 3))


def _number(x) -> complex | None:
    try:
        z = complex(sympy.N(x))
    except Exception:  # zoo, nan, a symbol left over
        return None
    return z if cmath.isfinite(z) and abs(z.imag) < 1e-9 else None


def _close(a: complex, b: complex) -> bool:
    return abs(a - b) <= 1e-9 * (1 + abs(a) + abs(b))


HOLDS = {"=": _close, "!=": lambda a, b: not _close(a, b), "<": lambda a, b: a.real < b.real and not _close(a, b),
         "<=": lambda a, b: a.real <= b.real or _close(a, b), ">": lambda a, b: a.real > b.real and not _close(a, b),
         ">=": lambda a, b: a.real >= b.real or _close(a, b)}


def try_values(form: Form) -> NodeVerdict | None:
    """✗ Tested at the first value that breaks an equation (small values first), ✓ Tested when enough values pass,
    None when it can't be tested. Only values where every premise holds count: a value that breaks a premise isn't a
    counterexample to the step."""
    names = [v.name for v in form.variables]
    subs = {sympy.Symbol(e.lhs): parse(e.rhs, names) for e in form.substitute}
    for _ in form.substitute:  # chain them: c = b*m with b = a*k makes c = a*k*m
        subs = {s: e.subs(subs) for s, e in subs.items()}
    eqs = [(parse(e.lhs, names), parse(e.rhs, names)) for e in form.equations]
    # The substitutions are premises too, so one left unresolved (a = b, b = a) still has to hold.
    given = [(parse(g.lhs, names), g.rel, parse(g.rhs, names)) for g in form.given] + [
        (parse(e.lhs, names), "=", parse(e.rhs, names)) for e in form.substitute]
    free = sorted({s for e in eqs + [(l, r) for l, _, r in given] for side in e for s in side.subs(subs).free_symbols},
                  key=str)
    domain = {v.name: v.domain for v in form.variables}
    rng = random.Random(form.node)
    points = sorted(({s: _value(domain.get(str(s), "real"), rng) for s in free} for _ in range(POINTS)),
                    key=lambda p: sum(abs(x) for x in p.values()))
    passed = 0
    for p in points:
        at = lambda e: _number(e.subs(subs).subs(p))
        values = [(at(lhs), at(rhs)) for lhs, rhs in eqs]
        held = [(at(lhs), rel, at(rhs)) for lhs, rel, rhs in given]
        if any(a is None or b is None for a, *_, b in values + held):
            continue  # outside the domain (division by zero, sqrt of a negative)
        if not all(HOLDS[rel](a, b) for a, rel, b in held):
            continue  # a premise fails here
        for (lhs, rhs), (a, b) in zip(eqs, values):
            if not _close(a, b):
                at = ", ".join(f"${s} = {sympy.latex(x)}$" for s, x in p.items())
                show = lambda e: sympy.latex(sympy.nsimplify(e.subs(subs).subs(p)))
                return verdict(form.node, "wrong", "tested",
                               f"False as written{' for ' + at if at else ''}: ${sympy.latex(lhs)}$ is ${show(lhs)}$ "
                               f"but ${sympy.latex(rhs)}$ is ${show(rhs)}$.",
                               "SymPy: " + "; ".join(f"{e.lhs} = {e.rhs}" for e in form.equations))
        passed += 1
    if passed >= (MIN_POINTS if free else 1):
        return verdict(form.node, "ok", "tested", "", f"SymPy: holds at {passed} random values")
    return None


# ---- Proved: Lean with the fixed automation ------------------------------------------------------------------------

BAD_STATEMENT = re.compile(r"sorry|:=|--|/-|\n|\bnode\d|\bvacuous\d")


def lean_trap(statement: str) -> bool:
    """ℤ or ℕ division rounds and ℕ subtraction stops at 0, so a false step could be proved: those go to the judges.
    shortcut: by the text alone, so `(a : ℤ) (x : ℝ) : a / 2 = x` gets through. Upgrade path: ask Lean the type of
    every `/` and `-`."""
    return "/" in statement and not re.search("ℝ|ℚ", statement) or "ℕ" in statement and "-" in statement


def split_statement(statement: str) -> tuple[str, str] | None:
    """`(a b : ℤ) (h : b = a * k) : claim` -> (binders, claim): the first `:` outside brackets. None if there is none."""
    depth = 0
    for i, ch in enumerate(statement):
        depth += (ch in "([{⦃") - (ch in ")]}⦄")
        if ch == ":" and depth == 0:
            return statement[:i].strip(), statement[i + 1:].strip()
    return None


def _axiom_free(out: str, prefix: str) -> set[int]:
    """The theorems `#print axioms` shows use Lean's standard axioms alone (a theorem with an error adds sorryAx)."""
    ids = {int(i) for i in re.findall(rf"'{prefix}(\d+)' does not depend on any axioms", out)}
    for i, axioms in re.findall(rf"'{prefix}(\d+)' depends on axioms: \[(.*?)\]", out, re.S):
        if {a.strip() for a in axioms.split(",")} <= lean.STANDARD_AXIOMS:
            ids.add(int(i))
    return ids


def lean_proved(statements: dict[int, str], user: str = "") -> tuple[set[int], bool]:
    """The steps our automation proves in one Lean run, and whether the run timed out. From contradictory premises Lean
    proves anything, which isn't the student's reasoning, so a step whose hypotheses alone prove False counts only when
    False is its claim. Every run is logged: a failed run otherwise just sends its steps to the judges, unseen."""
    parts = {i: p for i, s in statements.items() if (p := split_statement(s))}
    src = "import Mathlib\n\n" + "".join(
        f"theorem node{i} {statements[i]} := by\n  {AUTO}\n#print axioms node{i}\n"
        f"theorem vacuous{i} {binders} : False := by\n  {AUTO}\n#print axioms vacuous{i}\n"
        for i, (binders, _) in parts.items())
    out, secs = lean._run(src)
    vacuous = {i for i in _axiom_free(out, "vacuous") if parts[i][1] != "False"}
    proved = (_axiom_free(out, "node") - vacuous) & set(parts)
    timed_out = out.startswith("error: Lean took longer")
    store.log(user=user, endpoint="lean", provider="lean", model="lean", request={"statements": statements},
              response={"proved": sorted(proved), "vacuous": sorted(vacuous), "timed_out": timed_out,
                        "seconds": round(secs, 1)},
              valid=int(not timed_out), retries=0, cached=0, latency_ms=int(secs * 1000))
    return proved, timed_out


# ---- Judged: three judges, unanimous --------------------------------------------------------------------------------

KIND = {"claim": "a claim", "assume": "an assumption", "choose": "a choice of value"}


def describe(g: Graph, n: Node) -> str:
    q = ", ".join(f"{'for all' if x.quantifier == 'forall' else 'there is'} {x.name}" for x in g.goal_order)
    vs = "; ".join(f"{v.name} ({v.kind}" + ("" if v.introduced_at is None else f", step {v.introduced_at}")
                   + (f", depends on {', '.join(v.depends_on)}" if v.depends_on else "") + ")"
                   for v in g.variables if v.introduced_at is None or v.introduced_at <= n.id)
    prem = "\n".join(f"[{p}] {g.nodes[p].text}" + (" (given)" if g.nodes[p].kind == "given" else "")
                     for p in n.premises) or "(none)"
    # A conclusion like "so g∘f is injective" discharges the "let x, y with ..." behind its premises: show it.
    held = [a for a in sorted(ancestors(g, n.id) - {n.id} - set(n.premises)) if g.nodes[a].kind in ("assume", "choose")]
    force = "".join(f"\n[{a}] {g.nodes[a].text}" for a in held)
    return (f"Goal: {g.goal}\nThe goal's order: {q or '(no quantifiers)'}\nVariables so far: {vs or '(none)'}\n"
            f"Premises:\n{prem}\n" + (f"Assumptions in force (through its premises):{force}\n" if held else "")
            + f"\nThe step [{n.id}], {KIND[n.kind]}: {n.text}")


def judge(text: str, k: int, user: str) -> Mark | None:
    try:
        return llm.structured_call(
            endpoint="judge", user=user, cost_class="check", model=config.CHECK_MODEL, system=JUDGE_SYSTEM,
            content=[{"type": "text", "text": text}], schema=Mark,
            cache_input={"step": text, "judge": k, "prompt": _id(JUDGE_SYSTEM)}, max_tokens=2000)
    except llm.LLMError:
        return None


def unanimous(node: int, marks: list[Mark | None]) -> NodeVerdict:
    if any(m is None for m in marks):
        return verdict(node, "unsure", "judged", "Couldn't be checked this time.", "a judge failed")
    yes, no = [m for m in marks if m.ok], [m for m in marks if not m.ok]
    detail = f"{len(yes)} of {len(marks)} judges accept"
    if not no:
        return verdict(node, "ok", "judged", "", detail)
    if not yes:
        return verdict(node, "wrong", "judged", no[0].reason, detail)
    return verdict(node, "unsure", "judged", f"Not every judge accepts this: {no[0].reason}", detail)


# ---- the check ----------------------------------------------------------------------------------------------------

def formalize(g: Graph, claims: list[Node], user: str) -> dict[int, Form]:
    text = f"Goal: {g.goal}\n\n" + "\n\n".join(
        f"Step {n.id}: {n.text}\nPremises:\n" + ("\n".join(f"  [{p}] {g.nodes[p].text}" for p in n.premises) or "  (none)")
        for n in claims)
    try:
        out = llm.structured_call(
            endpoint="forms", user=user, cost_class="check", model=config.CHECK_MODEL, system=FORMS_SYSTEM,
            content=[{"type": "text", "text": text}], schema=Forms,
            cache_input={"steps": text, "prompt": _id(FORMS_SYSTEM)},
            semantic_check=lambda f: forms_problems(f, {n.id for n in claims}), max_tokens=8000)
    except llm.LLMError:
        return {}  # every step goes to the judges
    return {f.node: f for f in out.forms}


def check(g: Graph, user: str) -> dict:
    t = time.time()
    v = rule_verdicts(g)
    claims = [n for n in g.nodes if n.kind == "claim" and n.id not in v]
    forms = formalize(g, claims, user) if claims else {}
    statements = {}
    for n in claims:
        f = forms.get(n.id)
        if f and f.equations and (r := try_values(f)):
            v[n.id] = r
        elif (f and f.lean.strip() and not BAD_STATEMENT.search(f.lean) and not lean.FORBIDDEN.search(f.lean)
              and not lean_trap(f.lean)):
            statements[n.id] = f.lean.strip()
    timed_out = False
    if statements and lean.ready():
        proved, timed_out = lean_proved(statements, user)
        for i in proved:
            v[i] = verdict(i, "ok", "proved", "", f"Lean: {statements[i]}")
    rest = [n for n in g.nodes if n.kind != "given" and n.id not in v]
    jobs = [(describe(g, n), k) for n in rest for k in range(JUDGES)]
    with ThreadPoolExecutor(max_workers=9) as ex:
        marks = list(ex.map(lambda job: judge(job[0], job[1], user), jobs))
    for j, n in enumerate(rest):
        v[n.id] = unanimous(n.id, marks[j * JUDGES:(j + 1) * JUDGES])
        if timed_out and n.id in statements:  # Lean might have proved it: the report can count these
            v[n.id].detail += " (Lean timed out)"

    nodes = [v[n.id] for n in g.nodes if n.kind != "given"]
    bad = [x.node for x in nodes if x.status != "ok"]
    if bad:
        message = f"Look again at step{'s' if len(bad) > 1 else ''} {', '.join(map(str, bad))}."
    elif g.concludes is None:
        message = f"Every step checks out, but no step states the goal: {g.goal}"
    else:
        n = Counter(x.tier for x in nodes)
        message = ("Every step checks out: " + ", ".join(f"{n[t]} {t}" for t in ("proved", "tested", "judged") if n[t])
                   + ". Only Proved is certain: Tested passed SymPy at random values, Judged was accepted by "
                   f"{JUDGES} of {JUDGES} judges.")
    return {"nodes": [x.model_dump() for x in nodes], "complete": not bad and g.concludes is not None,
            "message": message, "seconds": int(time.time() - t)}
