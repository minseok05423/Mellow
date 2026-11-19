"""Check a student's proof as a graph of steps, each step on its own (DECISIONS #32).

1. `make_graph` (DECISIONS #35): a cheap model splits the confirmed lines into numbered nodes (the problem's givens,
   then each claim, assumption or choice the student writes); a strong model links them: premises (the earlier nodes a
   step rests on), the variables in the order they are introduced, and the order of the goal's quantifiers. Chunking
   mistakes show on the student's own words, so the student catches them; linking mistakes don't, so the strong model
   makes those. The student confirms the graph before anything is judged.
   shortcut: two model calls stand in for "rules + a small classifier". Upgrade path: train the classifier on the
   graphs students confirmed.
2. Rules on the confirmed graph find two mistakes without a model: a name that stands for two objects in one step
   (reusing k for a second witness), and a choice that depends on a variable the goal quantifies after it (δ chosen
   from x when the goal picks δ before x). The cheap model also groups the steps into nested blocks (a subproof, a
   proof by contradiction, cases, an induction, a chain; DECISIONS #37): a block's steps stay inside it, so a name
   can be reused in the next block and a fact from inside a block that has ended is out of scope, and each block's
   shape is checked from the links (each case rests on its own assumption, the inductive step on its hypothesis).
3. Every other step is checked alone, by the cheapest method that works:
   - Tested: the model writes the step's equations or inequalities, with its premises as substitutions; SymPy
     evaluates them at random values. A failing value is a counterexample (✗); all passing is ✓ Tested, for equations
     only: an inequality can fail in a region random values miss. A step that introduces its own variables ("for some
     integer k") is never Tested, by SymPy or plausible: it says they exist, which values can't settle.
   - Proved: the model writes the step as a Lean statement whose hypotheses are its premises, and our fixed automation
     must prove it. In the same run Lean's `plausible` looks for values where the premises hold and the claim fails
     (✗ Tested). Neither finding anything isn't evidence either way, so the step moves on.
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
RESULT_VERSION = 9  # part of /check's cache key: bump when what the student sees changes meaning

CHUNK_SYSTEM = """You split a student's handwritten proof into numbered steps, so each step can be checked on its own,
and group the steps into the blocks of the proof's structure.
You never judge or repair the proof: record what the student wrote, mistakes included.
Return {"blocks": [...], "nodes": [...]}.
blocks, numbered from 0 in the order they start: block 0 is the whole proof, {"id": 0, "kind": "proof", "parent":
null}. Every other block sits inside an earlier one (its parent) and holds consecutive steps:
  "let": the student takes arbitrary elements or assumes a hypothesis to prove something about them ("let x ∈ A ...
    so x ∈ B", one direction of an "iff" or of a set equality), starting with that assumption;
  "contradiction": "suppose, for contradiction, ...", starting with that assumption, up to the contradiction;
  "cases": a split into cases, holding its "case" blocks (and a line that announces the split); each "case" starts
    with its case's assumption ("if n is even");
  "induction": holding its "base" block and its "step" block (and "induction on n"); the "step" starts with the
    induction hypothesis;
  "chain": one chain of =, ≤, <, "iff" or "implies" written over several lines.
  A "case" sits directly inside a "cases" block, a "base" or "step" directly inside an "induction". Record the blocks
  the student wrote, even when a part is missing (an induction with no base case has its "step" block alone).
nodes, numbered from 0, in order:
- first, one "given" node per hypothesis of the problem (line null);
- then, in the student's order, one node per claim, assumption or choice the student writes, with the index of the
  student line it comes from. Every student line gets at least one node. A line with several claims ("so x ∉ A and
  x ∉ B, so x ∈ Aᶜ ∩ Bᶜ") gives several nodes; a chain "c = bm = (ak)m = a(km)" is one node.
  kind "assume": "suppose", "assume", "let x ∈ S", "let ε > 0", a case ("if x < y"), an induction hypothesis, "induction on n";
  kind "choose": the student picks a value ("let δ = min(1, ε/3)", "take k = m + 1");
  kind "claim": everything the student asserts.
- text: the node's statement as the student wrote it, math in $...$, without the reason ("Since a is even, a = 2k for
  some integer k" -> "$a = 2k$ for some integer $k$");
- block: the innermost block the node belongs to; the givens are in block 0."""

LINK_SYSTEM = """You turn a student's handwritten proof, already split into numbered steps grouped into blocks, into a
graph, so each step can be checked on its own from the steps it rests on. Keep the steps and blocks as they are. You
never judge or repair the proof: record what the student wrote, mistakes included.
Return JSON with:
- goal: what the problem asks to show, in words, math in $...$;
- goal_order: the goal's quantified variables in the order the goal quantifies them, each {"name", "quantifier":
  "forall" | "exists"}, using the student's names for them ("for every ε > 0 there is δ > 0 such that for all x ..." ->
  ε forall, δ exists, x forall). [] when the goal has no quantifiers worth tracking;
- links: one per step that isn't a given, {"node", "premises"}: the earlier nodes the student's step rests on: the
  reason they give ("since a is even" -> the node saying a is even), the line before a "so", "then", "hence" or "thus",
  and earlier facts the step plainly uses. Only earlier nodes. A conclusion that discharges assumptions ("so g∘f is
  injective" after "let x, y with ...") rests on those assumptions and what was derived from them. Point to the facts
  the student's words point to, never the ones that would make the step work, even a step inside a block that has
  ended.
- variables: every variable, in the order introduced: "given" (fixed by the problem, like the a and b of "if a and b
  are even", introduced_at null), "any" (let x be arbitrary, let ε > 0: a variable the goal quantifies is introduced by
  the student's step, not given), "chosen" (the student sets its value: δ = ...), "obtained" ("for some integer k",
  "there is"). introduced_at: the node that introduces it. List each introduction once. depends_on: for a chosen value, the variables in its formula; for an
  obtained one, the variables of the fact it comes from; [] otherwise. If the student reuses a name for a new object
  ("c = bk for some integer k" when k is already in use), list it again: never merge two introductions;
- reaches_goal: whether the proof, as its blocks end, gets to the goal: its last step states the goal; when it ends in
  cases, each case's last step does; in an induction, the base case states it for the first value and the inductive
  step for n + 1 ("this is the claim for n + 1"); in a proof by contradiction, the last step is a contradiction; in a
  chain, the first left side and the last right side together state it ("x ∈ f⁻¹(A ∩ B) iff ... iff
  x ∈ f⁻¹(A) ∩ f⁻¹(B)" for an arbitrary x states that the two sets are equal). false when the proof stops short."""

FORM_SYSTEM = """You prepare one step of a student's proof for mechanical checks. You get the goal, the proof's
variables, the step's claim and its premises (the earlier facts it rests on). Return the step's form:
- node: the step's number;
- variables: every variable in your substitute and equations, each {"name", "domain": "integer" | "natural" | "real"}
  ("real" unless the problem or the proof's variables make it an integer or a natural number);
- substitute: equations from the step's premises that give a variable's value, {"lhs": the variable, "rhs": its
  expression}: premise "a = 2k for some integer k" -> {"lhs": "a", "rhs": "2*k"}; "n is odd" -> {"lhs": "n", "rhs":
  "2*j + 1"} with j a new integer variable. Only from this step's premises;
- given: every other equation or inequality the step's premises state about its variables, {"lhs", "rel": "=" | "!=" |
  "<" | "<=" | ">" | ">=", "rhs"}: premise "n^2 = (2k + 1)^2" -> {"lhs": "n^2", "rel": "=", "rhs": "(2*k + 1)^2"};
  "x > 0" -> {"lhs": "x", "rel": ">", "rhs": "0"}. Random values are only tried where every given holds;
- equations: when the claim is one or more equations or inequalities, each {"lhs", "rel", "rhs"} exactly as the
  student wrote it; a chain "c = bm = (ak)m" is consecutive pairs (c, =, b*m), (b*m, =, (a*k)*m), and
  "|x^2 - 1| < 3δ ≤ ε" is (Abs(x^2 - 1), <, 3*delta), (3*delta, <=, eps). [] when the claim isn't equations or
  inequalities (divisibility, parity, sets, logic, sums with "...", "for some k"), and [] when they rely on a premise
  that substitute and given can't express (p is prime, x ∈ A, f is injective);
  The equations must be the whole claim: when the claim says more ("n^2 = 4k^2 + 4k + 1, so n^2 is odd"), [];
- lean: the step on its own as a Lean 4 + Mathlib statement, written as it would follow `theorem name`: binders for the
  variables, one hypothesis per premise saying exactly what that premise says, then `:` and the whole claim. Use ℝ
  whenever the step divides, and ℤ rather than ℕ whenever it subtracts: ℤ division rounds and ℕ subtraction stops at
  0, which can make a false step true. "" when the step can't be stated simply.
Expressions in substitute, given and equations: ASCII, explicit * for multiplication, ^ for powers, sqrt(), Abs(),
Min(), Max(), floor(), ceiling(), factorial(), binomial(); the student's variable names, Greek letters spelled out (delta, eps).
Be faithful: write exactly what the student claims, even if it is false. Never fix a mistake, never add a hypothesis
that isn't a premise, never strengthen or weaken a claim. A false step must stay false: that is how mistakes are found."""

JUDGE_SYSTEM = """You mark one step of a student's proof, on its own, for a first-year university proofs course. You see
the goal, the variables so far and how each was introduced, the facts the step rests on (its premises), where the step
sits (a case, the inductive step, a proof by contradiction), and the step.
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
Judge only this step; the premises are taken as true. Return {"reason", "verdict"}, the reason first and then the
verdict it supports. verdict is one of:
- "ok": the step earns the mark; reason: a few words on why it follows.
- "wrong": the step is entirely wrong: the claim doesn't follow at all (false, or reached by an inference that doesn't
  work), or the assumption or choice is not allowed. Only when you're sure.
- "unsure": anything in between, when you're unsure a grader would accept the step: it leans on a fact the student
  didn't justify, a gap a strict grader would ask about, or you can't tell what the student means.
For "wrong" and "unsure", reason: one sentence the student can act on, saying what is wrong or doubtful without giving
the fix."""


class Quantified(BaseModel):
    name: str
    quantifier: Literal["forall", "exists"]


class Chunk(BaseModel):
    id: int
    kind: Literal["given", "assume", "choose", "claim"]
    line: int | None  # the student's line; None for the problem's givens
    text: str
    block: int  # the innermost block it belongs to


class Block(BaseModel):
    id: int
    kind: Literal["proof", "let", "contradiction", "cases", "case", "induction", "base", "step", "chain"]
    parent: int | None  # None only for block 0, the whole proof


class Chunks(BaseModel):
    blocks: list[Block]
    nodes: list[Chunk]


class Node(Chunk):
    premises: list[int]


class Variable(BaseModel):
    name: str
    kind: Literal["given", "any", "chosen", "obtained"]
    introduced_at: int | None  # the node that introduces it; None for the problem's own
    depends_on: list[str]


class Graph(BaseModel):
    goal: str
    goal_order: list[Quantified]
    blocks: list[Block]
    nodes: list[Node]
    variables: list[Variable]
    reaches_goal: bool


class Link(BaseModel):
    node: int
    premises: list[int]


class Links(BaseModel):
    goal: str
    goal_order: list[Quantified]
    links: list[Link]
    variables: list[Variable]
    reaches_goal: bool


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
    equations: list[Relation]  # the claim: equations, or inequalities (which can only be refuted)
    lean: str


class Mark(BaseModel):
    # The reason first, so the verdict follows from it: verdict first, three judges wrote "wrong" and then reasoned
    # that the step was right (2026-10-23).
    reason: str
    verdict: Literal["ok", "unsure", "wrong"]


class NodeVerdict(BaseModel):
    node: int
    status: Literal["ok", "wrong", "unsure"]
    tier: Literal["proved", "tested", "judged"]
    message: str
    detail: str  # what decided it: the Lean statement, the values tried, or the judges' count
    comments: list[str] = []  # on a judged ?: what each judge who didn't accept the step said, for the student to weigh


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
    if any(v.introduced_at is not None and not 0 <= v.introduced_at < len(g.nodes) for v in g.variables):
        errs.append("introduced_at must be a node id or null")
    return errs + block_problems(g)


PART = {"case": "cases", "base": "induction", "step": "induction"}  # a part sits directly inside its whole


def block_problems(g: Graph) -> list[str]:
    bs = g.blocks
    if not bs or (bs[0].id, bs[0].kind, bs[0].parent) != (0, "proof", None):
        return ['block 0 must be {"id": 0, "kind": "proof", "parent": null}']
    if [b.id for b in bs] != list(range(len(bs))):
        return ["block ids must be 0, 1, 2, ... in order"]
    errs = []
    for b in bs[1:]:
        if b.kind == "proof" or b.parent is None or not 0 <= b.parent < b.id:
            errs.append(f"block {b.id}: parent must be an earlier block, and only block 0 is the proof")
            continue
        whole = bs[b.parent].kind
        if (b.kind in PART or whole in PART.values()) and PART.get(b.kind) != whole:
            errs.append(f"block {b.id}: a case sits directly inside cases, a base or step directly inside an "
                        f"induction, and nothing else does; got {b.kind} inside {whole}")
    if any(not 0 <= n.block < len(bs) for n in g.nodes):
        errs.append("each node's block must be a block id")
    if errs:
        return errs  # the checks below walk the blocks
    errs += [f"node {n.id}: a given belongs in block 0" for n in g.nodes if n.kind == "given" and n.block]
    starts = []
    for b in bs:
        ns = members(g, b.id)
        if not ns:
            errs.append(f"block {b.id} has no nodes")
        elif ns != list(range(ns[0], ns[-1] + 1)):
            errs.append(f"block {b.id}: its nodes must be consecutive, got {ns}")
        starts.append(ns[0] if ns else 0)
    if starts != sorted(starts):
        errs.append("blocks must be numbered in the order they start")
    return errs


def assemble(chunks: Chunks, links: Links) -> Graph:
    premises = {x.node: x.premises for x in links.links}
    return Graph(goal=links.goal, goal_order=links.goal_order, blocks=chunks.blocks, variables=links.variables,
                 reaches_goal=links.reaches_goal,
                 nodes=[Node(**c.model_dump(), premises=premises.get(c.id, [])) for c in chunks.nodes])


def links_problems(chunks: Chunks, links: Links, n_lines: int) -> list[str]:
    steps = {c.id for c in chunks.nodes if c.kind != "given"}
    named = [x.node for x in links.links]
    errs = [f"links must name each step that isn't a given once; got {sorted(named)}, steps {sorted(steps)}"] \
        if sorted(named) != sorted(steps) else []
    return errs + graph_problems(assemble(chunks, links), n_lines)


def make_graph(problem: str, lines: list[str], user: str) -> Graph:
    text = f"Problem:\n{problem}\n\nThe student's proof, line by line:\n" + "\n".join(f"{i}: {s}" for i, s in enumerate(lines))
    chunks = llm.structured_call(
        endpoint="chunk", user=user, cost_class="check", model=config.CHUNK_MODEL, system=CHUNK_SYSTEM,
        content=[{"type": "text", "text": text}], schema=Chunks,
        cache_input={"problem": problem, "lines": lines, "prompt": _id(CHUNK_SYSTEM)},
        semantic_check=lambda c: graph_problems(assemble(c, Links(goal="", goal_order=[], links=[], variables=[],
                                                                  reaches_goal=False)), len(lines)), max_tokens=4000)
    steps = "\n".join(f"[{c.id}] {c.kind}" + ("" if c.line is None else f" (line {c.line})") + f", block {c.block}: "
                      f"{c.text}" for c in chunks.nodes) + "\nIts blocks:\n" + "\n".join(
        f"block {b.id}: {b.kind}" + ("" if b.parent is None else f", inside block {b.parent}") for b in chunks.blocks)
    links = llm.structured_call(
        endpoint="link", user=user, cost_class="check", model=config.GRAPH_MODEL, system=LINK_SYSTEM,
        content=[{"type": "text", "text": f"{text}\n\nIts steps:\n{steps}"}], schema=Links,
        cache_input={"problem": problem, "lines": lines, "steps": steps, "prompt": _id(LINK_SYSTEM)},
        semantic_check=lambda x: links_problems(chunks, x, len(lines)), max_tokens=8000)
    return assemble(chunks, links)


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


# ---- blocks (DECISIONS #37) ---------------------------------------------------------------------------------------

def inside(g: Graph, b: int) -> list[int]:
    """Block b and the blocks around it, innermost first."""
    out = [b]
    while (p := g.blocks[out[-1]].parent) is not None:
        out.append(p)
    return out


def members(g: Graph, b: int) -> list[int]:
    """The nodes in block b and in the blocks inside it."""
    return [n.id for n in g.nodes if b in inside(g, n.block)]


def children(g: Graph, b: int) -> list[int]:
    return [x.id for x in g.blocks if x.parent == b]


def opening(g: Graph, b: int) -> int | None:
    """The assumption or choice a block starts with, if it starts with one of its own."""
    ns = members(g, b)
    return ns[0] if ns and g.nodes[ns[0]].block == b and g.nodes[ns[0]].kind in ("assume", "choose") else None


def results(g: Graph, b: int) -> list[int]:
    """What a block shows: its last step, or, when it ends in cases or an induction, the last step of each part."""
    ns = members(g, b)
    if not ns:
        return []
    last = g.nodes[ns[-1]]
    if last.block == b:
        return [last.id] if last.kind == "claim" else []
    child = next(c for c in inside(g, last.block) if g.blocks[c].parent == b)
    if g.blocks[child].kind in PART.values():
        return [r for c in children(g, child) for r in results(g, c)]
    return results(g, child)


def block_name(g: Graph, b: int) -> str:
    x = g.blocks[b]
    if x.kind == "case":
        return f"case {children(g, x.parent).index(b) + 1}"
    return {"proof": "the proof", "let": f"the part from step {members(g, b)[0]}", "cases": "the proof by cases",
            "contradiction": "the proof by contradiction", "induction": "the induction", "base": "the base case",
            "step": "the inductive step", "chain": f"the chain from step {members(g, b)[0]}"}[x.kind]


# ---- rules -------------------------------------------------------------------------------------------------------

def rule_verdicts(g: Graph) -> dict[int, NodeVerdict]:
    out: dict[int, NodeVerdict] = {}
    # A block's steps stay inside it: once it ends, only what it shows (its results) can be used.
    # shortcut: a result counts as shown everywhere after its block, though it holds under the block's assumption, so a
    # case's result used inside the next case gets through. Upgrade path: a result outside its block is "assumption ⇒
    # result", usable only where that is what the step needs.
    # The step right after a block discharges it ("so T is injective", "so p = q") and rests on the assumptions it
    # opened with, as the linker is told to record. shortcut: only the step right after; a conclusion that discharges
    # two blocks in a row (both halves of a set equality) still gets the earlier one's assumption flagged.
    for n in g.nodes:
        here = inside(g, n.block)
        for p in n.premises:
            ended = [b for b in inside(g, g.nodes[p].block) if b not in here]
            discharged = (g.nodes[p].kind == "assume" and p in {opening(g, b) for b in ended}
                          and n.id == members(g, ended[-1])[-1] + 1) if ended else False
            if ended and p not in results(g, ended[-1]) and not discharged:
                out[n.id] = verdict(n.id, "wrong", "tested",
                                    f"This uses step {p}, from inside {block_name(g, ended[0])}, which has ended: what "
                                    "holds there doesn't hold here.", "rule: a block's steps stay inside it")
                break
    letters = {v.name for v in g.variables if len(v.name) == 1}
    names = sorted({v.name for v in g.variables})
    for n in g.nodes:
        up, here = ancestors(g, n.id), set(inside(g, n.block))
        for name in names:
            # Two introductions clash only where both are in scope: a name can be reused in the next block.
            intro = sorted({-1 if v.introduced_at is None else v.introduced_at for v in g.variables
                            if v.name == name and (v.introduced_at is None or (
                                v.introduced_at in up and g.nodes[v.introduced_at].block in here))})
            if len(intro) > 1 and mentions(n.text, name, letters):
                where = " and ".join("the problem" if i < 0 else f"step {i}" for i in intro)
                out.setdefault(n.id, verdict(n.id, "wrong", "tested",
                                             f"${name}$ stands for two different things here ({where}). "
                                             "A new object needs a new name.", "rule: one name, one object"))
                letters.discard(name)  # report each clash once, where it first matters
                names.remove(name)
                break
    # Assuming what the proof concludes is circular, however the rest goes (a converse dressed as a proof).
    said = lambda t: re.sub(r"[\s$]", "", t)
    for c in results(g, 0):
        for a in sorted(ancestors(g, c) - {c}):
            if g.nodes[a].kind == "assume" and said(g.nodes[a].text) == said(g.nodes[c].text):
                out.setdefault(a, verdict(a, "wrong", "tested", "This assumes what the proof sets out to show, so "
                                          "what follows from it doesn't prove it.", "rule: no assuming the conclusion"))
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


ASSUMES = {"case": "its case's assumption", "step": "the induction hypothesis",
           "contradiction": "the assumption it contradicts", "let": "what it assumes"}


def structure_problems(g: Graph) -> list[str]:
    """What keeps the proof's structure from reaching the goal, as clauses for "every step checks out, but ...".
    The shape of each block, from the links: an induction has a base case and an inductive step resting on its
    hypothesis, each case rests on its own assumption, a contradiction on what it assumes, a chain is unbroken.
    shortcut: that the steps say the right thing (the base case is P(0), the cases cover everything, the proof's last
    steps state the goal) is the models' word, confirmed by the student. Upgrade path: judge the structure."""
    gaps = [] if g.reaches_goal else [f"no step states the goal: {g.goal}"]
    for b in g.blocks:
        name, kids = block_name(g, b.id), [g.blocks[c].kind for c in children(g, b.id)]
        start, ends = opening(g, b.id), results(g, b.id)
        if b.kind == "induction":
            gaps += [f"the induction has no {w}" for k, w in (("base", "base case"), ("step", "inductive step"))
                     if k not in kids]
        elif b.kind == "cases" and kids.count("case") < 2:
            gaps.append("a proof by cases needs at least two cases")
        elif b.kind == "chain":
            ns = members(g, b.id)
            gaps += [f"{name} needs at least two links"] if len(ns) < 2 else [
                f"{name} breaks at step {y}: it doesn't follow from step {x}"
                for x, y in zip(ns, ns[1:]) if x not in g.nodes[y].premises][:1]
        elif b.kind != "proof" and not ends:
            gaps.append(f"{name} doesn't end in a claim")
        elif b.kind in ASSUMES and start is None:
            gaps.append(f"{name} doesn't start with {ASSUMES[b.kind]}")
        elif b.kind in ("case", "step", "contradiction") and not all(start in ancestors(g, r) for r in ends):
            gaps.append(f"{name} doesn't rest on {ASSUMES[b.kind]}")
    return gaps


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


def form_problems(form: Form, node: int) -> list[str]:
    names = [v.name for v in form.variables]
    if form.node != node:
        return [f"node must be {node}, the step asked for"]
    if bad := [n for n in names if not IDENT.fullmatch(n) or n in FUNCS]:
        return [f"variable names must be ASCII identifiers: {bad}"]
    errs = [f"substitute lhs {e.lhs!r} must be one of its variables" for e in form.substitute if e.lhs not in names]
    for e in form.substitute + form.given + form.equations:
        for side in (e.lhs, e.rhs):
            try:
                parse(side, names)
            except ValueError as err:
                errs.append(str(err))
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
    """✗ Tested at the first value that breaks the claim (small values first), ✓ Tested when enough values pass and the
    claim is equations only, None when it can't be decided. Only values where every premise holds count: a value that
    breaks a premise isn't a counterexample to the step."""
    names = [v.name for v in form.variables]
    subs = {sympy.Symbol(e.lhs): parse(e.rhs, names) for e in form.substitute}
    for _ in form.substitute:  # chain them: c = b*m with b = a*k makes c = a*k*m
        subs = {s: e.subs(subs) for s, e in subs.items()}
    eqs = [(parse(e.lhs, names), e.rel, parse(e.rhs, names)) for e in form.equations]
    # The substitutions are premises too, so one left unresolved (a = b, b = a) still has to hold.
    given = [(parse(g.lhs, names), g.rel, parse(g.rhs, names)) for g in form.given] + [
        (parse(e.lhs, names), "=", parse(e.rhs, names)) for e in form.substitute]
    free = sorted({s for lhs, _, rhs in eqs + given for side in (lhs, rhs) for s in side.subs(subs).free_symbols},
                  key=str)
    domain = {v.name: v.domain for v in form.variables}
    # A root of a negative number leaves the reals even when the result comes back real ((√a − √b)² at a, b < 0).
    roots = {b for lhs, _, rhs in eqs + given for side in (lhs, rhs)
             for b, x in (q.as_base_exp() for q in side.subs(subs).atoms(sympy.Pow)) if x.is_Rational and not x.is_integer}
    rng = random.Random(form.node)
    points = sorted(({s: _value(domain.get(str(s), "real"), rng) for s in free} for _ in range(POINTS)),
                    key=lambda p: sum(abs(x) for x in p.values()))
    passed = 0
    for p in points:
        at = lambda e: _number(e.subs(subs).subs(p))
        values = [(at(lhs), rel, at(rhs)) for lhs, rel, rhs in eqs]
        held = [(at(lhs), rel, at(rhs)) for lhs, rel, rhs in given]
        if any(a is None or b is None for a, *_, b in values + held) or any(
                (r := _number(b.subs(p))) is None or r.real < 0 for b in roots):
            continue  # outside the domain (division by zero, sqrt of a negative)
        if not all(HOLDS[rel](a, b) for a, rel, b in held):
            continue  # a premise fails here
        for (lhs, _, rhs), (a, rel, b) in zip(eqs, values):
            if not HOLDS[rel](a, b):
                at = ", ".join(f"${s} = {sympy.latex(x)}$" for s, x in p.items())
                show = lambda e: sympy.latex(sympy.nsimplify(e.subs(subs).subs(p)))
                return verdict(form.node, "wrong", "tested",
                               f"False as written{' for ' + at if at else ''}: ${sympy.latex(lhs)}$ is ${show(lhs)}$ "
                               f"but ${sympy.latex(rhs)}$ is ${show(rhs)}$.",
                               "SymPy: " + "; ".join(f"{e.lhs} {e.rel} {e.rhs}" for e in form.equations))
        passed += 1
    if any(rel != "=" for _, rel, _ in eqs):
        return None  # values only refute an inequality: one can fail in a region random values miss
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


FOUND = re.compile(r"^\S*?:(\d+):\d+: error[^\n]*\n=+\nFound a counter-example!\n(.*?)^-{5,}", re.M | re.S)


def counterexamples(out: str, at_line: dict[int, int]) -> dict[int, list[tuple[str, str]]]:
    """`plausible`'s counterexamples, by step, from the `name := value` lines it prints."""
    return {at_line[int(line)]: re.findall(r"^(\S+) := (.+)$", block, re.M)
            for line, block in FOUND.findall(out) if int(line) in at_line}


def lean_run(statements: dict[int, str], user: str = "") -> tuple[set[int], dict[int, list[tuple[str, str]]], bool]:
    """In one Lean run: the steps our automation proves, the steps `plausible` refutes (values where every hypothesis
    holds and the claim fails), and whether the run timed out. From contradictory premises Lean proves anything, which
    isn't the student's reasoning, so a step whose hypotheses alone prove False counts only when False is its claim.
    `plausible` tests only decidable statements over types it can sample (ℤ, ℕ, finite sets, not ℝ, and not a premise
    with ∃), and says nothing about the rest. Every run is logged: a failed run otherwise just sends its steps to the
    judges, unseen."""
    parts = {i: p for i, s in statements.items() if (p := split_statement(s))}
    lines, at_line = ["import Mathlib", ""], {}
    for i, (binders, _) in parts.items():
        lines += [f"theorem node{i} {statements[i]} := by", f"  {AUTO}", f"#print axioms node{i}",
                  f"theorem vacuous{i} {binders} : False := by", f"  {AUTO}", f"#print axioms vacuous{i}",
                  f"theorem refute{i} {statements[i]} := by"]
        lines.append("  plausible")
        at_line[len(lines)] = i  # Lean reports the counterexample at the tactic's line (1-based)
    out, secs = lean._run("\n".join(lines) + "\n")
    vacuous = {i for i in _axiom_free(out, "vacuous") if parts[i][1] != "False"}
    proved = (_axiom_free(out, "node") - vacuous) & set(parts)
    refuted = {i: v for i, v in counterexamples(out, at_line).items() if i not in proved}
    timed_out = out.startswith("error: Lean took longer")
    store.log(user=user, endpoint="lean", provider="lean", model="lean", request={"statements": statements},
              response={"proved": sorted(proved), "refuted": refuted, "vacuous": sorted(vacuous),
                        "timed_out": timed_out, "seconds": round(secs, 1)},
              valid=int(not timed_out), retries=0, cached=0, latency_ms=int(secs * 1000))
    return proved, refuted, timed_out


# ---- Judged: three judges, unanimous --------------------------------------------------------------------------------

KIND = {"claim": "a claim", "assume": "an assumption", "choose": "a choice of value"}


def describe(g: Graph, n: Node) -> str:
    q = ", ".join(f"{'for all' if x.quantifier == 'forall' else 'there is'} {x.name}" for x in g.goal_order)
    vs = "; ".join(f"{v.name} ({v.kind}" + ("" if v.introduced_at is None else f", step {v.introduced_at}")
                   + (f", depends on {', '.join(v.depends_on)}" if v.depends_on else "") + ")"
                   for v in g.variables if v.introduced_at is None or v.introduced_at <= n.id)
    prem = "\n".join(f"[{p}] {g.nodes[p].text}" + (" (given)" if g.nodes[p].kind == "given" else "")
                     for p in n.premises) or "(none)"
    # A conclusion like "so g∘f is injective" discharges the "let x, y with ..." behind its premises, and a step inside
    # a case or a proof by contradiction stands on the assumption its blocks start with: show both.
    here = inside(g, n.block)
    starts = {s for b in here if (s := opening(g, b)) is not None and s < n.id}
    held = [a for a in sorted((ancestors(g, n.id) | starts) - {n.id} - set(n.premises))
            if g.nodes[a].kind in ("assume", "choose")]
    force = "".join(f"\n[{a}] {g.nodes[a].text}" for a in held)
    where = " › ".join(block_name(g, b) for b in reversed(here[:-1]))
    return (f"Goal: {g.goal}\nThe goal's order: {q or '(no quantifiers)'}\nVariables so far: {vs or '(none)'}\n"
            f"Premises:\n{prem}\n" + (f"Assumptions in force:{force}\n" if held else "")
            + (f"Where it is: inside {where}\n" if where else "")
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
    n = Counter(m.verdict for m in marks)
    detail = f"{n['ok']} of {len(marks)} judges accept" + (f", {n['unsure']} unsure" if n["unsure"] else "")
    if n["ok"] == len(marks):
        return verdict(node, "ok", "judged", "", detail)
    if n["wrong"] == len(marks):
        return verdict(node, "wrong", "judged", marks[0].reason, detail)
    # Any judge unsure, or the judges split: the student decides, with what the doubting judges said.
    v = verdict(node, "unsure", "judged", "The judges aren't sure about this step: decide whether it's acceptable.", detail)
    v.comments = [f"{'Unsure' if m.verdict == 'unsure' else 'Wrong'}: {m.reason}" for m in marks if m.verdict != "ok"]
    return v


# ---- the check ----------------------------------------------------------------------------------------------------

def setup(g: Graph, n: Node) -> list[int]:
    """The steps that chose or obtained the variables this step uses ("p, q ∈ ℤ, q ≠ 0"), when it doesn't link to them:
    their conditions hold wherever the variables are in scope, so a value that breaks them is no counterexample. Not an
    assumption ("suppose p is the largest prime"): inside a proof by contradiction everything would rest on a false
    premise, and the refutations there would vanish."""
    letters = {v.name for v in g.variables if len(v.name) == 1}
    at = {v.name: v.introduced_at for v in g.variables  # in order introduced: a reused name keeps its latest step
          if v.introduced_at is not None and v.introduced_at < n.id and mentions(n.text, v.name, letters)}
    return sorted({i for i in at.values() if g.nodes[i].kind in ("choose", "claim")} - set(n.premises))


def formalize(g: Graph, claims: list[Node], user: str) -> dict[int, Form]:
    """One call per step, all at once: the wait is the slowest step, not every step written in turn, and an edited
    graph only re-asks for the steps it changed (each call is cached on its own text). The variables table keeps a
    name's domain the same across steps."""
    vs = "; ".join(f"{v.name} ({v.kind}" + ("" if v.introduced_at is None else f", step {v.introduced_at}") + ")"
                   for v in g.variables) or "(none)"

    def one(n: Node) -> Form | None:
        text = (f"Goal: {g.goal}\nThe proof's variables: {vs}\n\nStep {n.id}: {n.text}\nPremises:\n"
                + ("\n".join(f"  [{p}] {g.nodes[p].text}" for p in n.premises + setup(g, n)) or "  (none)"))
        try:
            return llm.structured_call(
                endpoint="form", user=user, cost_class="check", model=config.CHECK_MODEL, system=FORM_SYSTEM,
                content=[{"type": "text", "text": text}], schema=Form,
                cache_input={"step": text, "prompt": _id(FORM_SYSTEM)},
                semantic_check=lambda f: form_problems(f, n.id), max_tokens=4000)
        except llm.LLMError:
            return None  # this step goes to the judges

    with ThreadPoolExecutor(max_workers=9) as ex:
        return {n.id: f for n, f in zip(claims, ex.map(one, claims)) if f}


EXISTS = re.compile(r"for some|there (is|are|exists?)|\\exists|∃", re.I)


def check(g: Graph, user: str) -> dict:
    t = time.time()
    v = rule_verdicts(g)
    claims = [n for n in g.nodes if n.kind == "claim" and n.id not in v]
    forms = formalize(g, claims, user) if claims else {}
    # A step that introduces its variables ("√2 = b/a with a, b ∈ ℤ", "a = 2k for some k") says they exist: values that
    # break it refute nothing and values that pass prove nothing, so neither SymPy nor plausible decides it, whatever
    # the translator wrote. The linker records the introduction; the text catches one it missed.
    # The rest of an introduction counts too: "p, q ∈ ℤ, q ≠ 0, √2 = p/q" split into a choice and, on the same line,
    # the claim p and q were chosen to satisfy.
    intro = {x.introduced_at for x in g.variables}
    exists = {n.id for n in claims if EXISTS.search(n.text) or n.id in intro
              or any(p in intro and g.nodes[p].line == n.line for p in n.premises)}
    statements = {}
    for n in claims:
        f = forms.get(n.id)
        if f and f.equations and n.id not in exists and (r := try_values(f)):
            v[n.id] = r
        elif (f and f.lean.strip() and not BAD_STATEMENT.search(f.lean) and not lean.FORBIDDEN.search(f.lean)
              and not lean_trap(f.lean)):
            statements[n.id] = f.lean.strip()
    timed_out = False
    if statements and lean.ready():
        proved, refuted, timed_out = lean_run(statements, user)
        for i in proved:
            v[i] = verdict(i, "ok", "proved", "", f"Lean: {statements[i]}")
        for i, values in refuted.items():
            if i in exists:
                continue
            at = ", ".join(f"${n} = {x}$" for n, x in values)
            v[i] = verdict(i, "wrong", "tested", f"Fails for {at}, where the steps it rests on hold.",
                           f"Lean's plausible: {statements[i]}")
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
    gaps = structure_problems(g)
    if bad:
        message = f"Look again at step{'s' if len(bad) > 1 else ''} {', '.join(map(str, bad))}."
    elif gaps:
        message = f"Every step checks out, but {gaps[0]}."
    else:
        n = Counter(x.tier for x in nodes)
        message = ("Every step checks out: " + ", ".join(f"{n[t]} {t}" for t in ("proved", "tested", "judged") if n[t])
                   + ". Only Proved is certain: Tested passed SymPy at random values, Judged was accepted by "
                   f"{JUDGES} of {JUDGES} judges.")
    return {"nodes": [x.model_dump() for x in nodes], "complete": not bad and not gaps, "reaches_goal": not gaps,
            "message": message, "seconds": int(time.time() - t)}
