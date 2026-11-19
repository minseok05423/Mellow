"""The graph check (app/graphcheck.py) without a model or Lean: rules, SymPy tests, unanimous judges, the cascade."""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from app import graphcheck, lean, llm, store
from app.graphcheck import (Equation, Form, Graph, Mark, Node, Variable, check, graph_problems, parse, rule_verdicts,
                            try_values, unanimous)
from app.llm import Completion
from leaneval.agentq import claim


ROOT = [{"id": 0, "kind": "proof", "parent": None}]


def blocks(*inner):
    """Block 0, the whole proof, then a block per (kind, parent)."""
    return ROOT + [{"id": i + 1, "kind": k, "parent": p} for i, (k, p) in enumerate(inner)]


def node(i, kind, text, premises=(), line=None, block=0):
    return Node(id=i, kind=kind, line=None if kind == "given" else (i if line is None else line), text=text,
                premises=list(premises), block=block)


def var(name, kind, at=None, deps=()):
    return Variable(name=name, kind=kind, introduced_at=at, depends_on=list(deps))


def form(i, eqs, subs=(), names=("a", "b", "k", "m"), domain="integer", given=()):
    return Form(node=i, variables=[{"name": n, "domain": domain} for n in names],
                substitute=[Equation(lhs=l, rhs=r) for l, r in subs],
                given=[{"lhs": l, "rel": rel, "rhs": r} for l, rel, r in given],
                equations=[{"lhs": e[0], "rel": "=" if len(e) == 2 else e[1], "rhs": e[-1]} for e in eqs], lean="")


def test_parse_lets_through_only_arithmetic_on_declared_names():
    assert str(parse("2*k + 2*m", ["k", "m"])) == "2*k + 2*m"
    assert str(parse("sqrt(x)^2", ["x"])) == "x"
    for bad in ["__import__('os')", "k.real", "os", "lambda: 1", "k; 1", "[k]"]:
        with pytest.raises(ValueError):
            parse(bad, ["k"])
    # Keywords declared as variable names could build generators or conditionals inside eval.
    for bad, names in [("(k for k in m)", ["k", "m", "for", "in"]), ("k if m else k", ["k", "m", "if", "else"])]:
        with pytest.raises(ValueError, match="keywords"):
            parse(bad, names)


def test_a_wrong_equation_gets_a_small_counterexample():
    v = try_values(form(2, [("a + b", "2*k + 2*m"), ("2*k + 2*m", "2*(k*m)")], [("a", "2*k"), ("b", "2*m")]))
    assert (v.status, v.tier) == ("wrong", "tested")
    assert v.message.startswith("False as written for $k = ") and "2 k m" in v.message


def test_a_right_equation_passes_and_values_outside_the_domain_are_skipped():
    v = try_values(form(2, [("a + b", "2*(k + m)")], [("a", "2*k"), ("b", "2*m")]))
    assert (v.status, v.tier) == ("ok", "tested") and "random values" in v.detail
    assert try_values(form(0, [("x/x", "1")], names=["x"], domain="real")).status == "ok"
    assert try_values(form(0, [("2*0", "0*1")], names=[])).status == "ok"  # no variables: one exact evaluation


def test_substitutions_chain():
    # L1g's false ✗ on dvd_trans: c = bm with b = ak, applied at once, left b free in bm.
    names, subs = ["a", "b", "c", "k", "m"], [("b", "a*k"), ("c", "b*m")]
    chain = [("c", "b*m"), ("b*m", "(a*k)*m")]
    assert try_values(form(4, chain + [("(a*k)*m", "a*(k*m)")], subs, names)).status == "ok"
    assert try_values(form(4, chain + [("(a*k)*m", "a*(k + m)")], subs, names)).status == "wrong"


def test_values_that_break_a_premise_are_not_counterexamples():
    # L1g's false ✗ on odd_sq: n^2 = (2k + 1)^2 can't be a substitution, so random n and k broke the premise.
    step = [("n^2", "2*(2*k^2 + 2*k) + 1")]
    assert try_values(form(3, step, names=["n", "k"], given=[("n^2", "=", "(2*k + 1)^2")])) is None
    assert try_values(form(0, [("sqrt(x^2)", "x")], names=["x"], domain="real", given=[("x", ">=", "0")])).status == "ok"
    assert try_values(form(0, [("sqrt(x^2)", "x")], names=["x"], domain="real")).status == "wrong"
    # A substitution cycle can't resolve, but its equations must still hold: a^2 = ab is true whenever a = b.
    v = try_values(form(0, [("a^2", "a*b")], [("a", "b"), ("b", "a")], ["a", "b"]))
    assert v is None or v.status == "ok"


def test_absolute_values_are_tried_with_mixed_signs():
    v = try_values(form(0, [("Abs(x + y)", "Abs(x) + Abs(y)")], names=["x", "y"]))
    assert v.status == "wrong"


def test_roots_of_negatives_are_outside_the_domain():
    # L1g's false ✗ on am_gm_converse: at a = -1/2, b = -1, (√a − √b)² is the real number -3/2 + √2.
    assert try_values(form(2, [("(sqrt(a) - sqrt(b))^2", ">=", "0")], names=["a", "b"], domain="real")) is None
    assert try_values(form(0, [("sqrt(a)*sqrt(b)", "sqrt(a*b)")], names=["a", "b"], domain="real")).status == "ok"


def test_inequalities_are_refuted_but_never_passed():
    # L1g's eps_delta_sq: with δ = min(1, ε/2), 3δ ≤ ε fails at ε = 1; only the judges had caught it.
    names = ["x", "eps", "delta"]
    step = [("Abs(x^2 - 1)", "<", "3*delta"), ("3*delta", "<=", "eps")]
    prem = [("Abs(x - 1)", "<", "delta"), ("Abs(x + 1)", "<", "3")]
    v = try_values(form(8, step, [("delta", "Min(1, eps/2)")], names, "real", prem))
    assert (v.status, v.tier) == ("wrong", "tested") and "SymPy: Abs(x^2 - 1) < 3*delta; 3*delta <= eps" == v.detail
    # True inequalities: random values can't prove them, so they move on to Lean and the judges.
    assert try_values(form(8, step, [("delta", "Min(1, eps/3)")], names, "real", prem + [("eps", ">", "0")])) is None
    assert try_values(form(0, [("2*x*y", "<=", "x^2 + y^2")], names=["x", "y"], domain="real")) is None


def test_assuming_the_conclusion_is_caught_by_a_rule():
    # L1g's am_gm_converse: "suppose √ab ≤ (a + b)/2 ... so √ab ≤ (a + b)/2".
    g = Graph(goal="$\\sqrt{ab} \\le (a + b)/2$", goal_order=[], variables=[], blocks=ROOT, reaches_goal=True,
              nodes=[node(0, "given", "$a, b \\ge 0$"), node(1, "assume", "$\\sqrt{ab} \\le (a + b)/2$"),
                     node(2, "claim", "$ab \\le (a + b)^2/4$", [1]),
                     node(3, "claim", "$\\sqrt{ab}  \\le (a+b)/2$", [1, 2])])
    v = rule_verdicts(g)
    assert list(v) == [1] and v[1].status == "wrong" and v[1].detail == "rule: no assuming the conclusion"
    g.nodes[1].text = "$\\sqrt{ab} > (a + b)/2$"  # assuming the negation, for a contradiction, is fine
    assert rule_verdicts(g) == {}


DVD_SAME_K = Graph(
    goal="$a \\mid c$", goal_order=[],
    nodes=[node(0, "given", "$a \\mid b$"), node(1, "given", "$b \\mid c$"),
           node(2, "claim", "$b = ak$ for some integer $k$", [0], 0),
           node(3, "claim", "$c = bk$ for some integer $k$", [1], 1),
           node(4, "claim", "$c = (ak)k = ak^2$", [2, 3], 2), node(5, "claim", "$a \\mid c$", [4], 3)],
    variables=[var("a", "given"), var("b", "given"), var("c", "given"), var("k", "obtained", 2, ["a", "b"]),
               var("k", "obtained", 3, ["b", "c"])], blocks=ROOT, reaches_goal=True)


def test_a_name_reused_for_a_second_object_is_caught_where_both_meet():
    v = rule_verdicts(DVD_SAME_K)
    assert list(v) == [4] and v[4].status == "wrong" and "step 2 and step 3" in v[4].message


DE_MORGAN = [node(0, "assume", "$x \\in (A \\cup B)^c$", block=1), node(1, "claim", "$x \\in A^c \\cap B^c$", [0], block=1),
             node(2, "assume", "$x \\in A^c \\cap B^c$", block=2), node(3, "claim", "$x \\in (A \\cup B)^c$", [2], block=2),
             node(4, "claim", "so for every $x$ the two sets are equal", [1, 3])]


def de_morgan(nodes, bl=blocks(("let", 0), ("let", 0))):
    return Graph(goal="$(A \\cup B)^c = A^c \\cap B^c$", goal_order=[], blocks=bl, nodes=nodes, reaches_goal=True,
                 variables=[var("A", "given"), var("B", "given"), var("x", "any", 0), var("x", "any", 2)])


def test_a_name_can_be_reused_in_the_next_block():
    # L1g's de_morgan_or: the ⊇ half reuses x. Each half is a block of its own, so its x is out of scope at the end.
    assert rule_verdicts(de_morgan(DE_MORGAN)) == {}
    flat = [n.model_copy(update={"block": 0}) for n in DE_MORGAN]
    assert list(rule_verdicts(de_morgan(flat, ROOT))) == [4]  # without the blocks, both x's meet at step 4


def test_a_step_from_inside_a_block_that_has_ended_is_out_of_scope():
    using = DE_MORGAN[:3] + [node(3, "claim", "$x \\in (A \\cup B)^c$", [2, 0], block=2), DE_MORGAN[4]]
    v = rule_verdicts(de_morgan(using))
    assert list(v) == [3] and v[3].status == "wrong" and "step 0, from inside the part from step 0" in v[3].message
    # What a block shows (its last step) can be used after it ends.
    shown = DE_MORGAN[:3] + [node(3, "claim", "$x \\in (A \\cup B)^c$", [2, 1], block=2), DE_MORGAN[4]]
    assert rule_verdicts(de_morgan(shown)) == {}


def test_the_step_right_after_a_block_may_rest_on_what_it_assumed():
    # L1g advanced, top_hausdorff_unique_limit: "so p = q" rests on the assumption p ≠ q it discharges.
    def g(nodes):
        return Graph(goal="$p = q$", goal_order=[], blocks=blocks(("contradiction", 0)), nodes=nodes, variables=[],
                     reaches_goal=True)
    body = [node(0, "assume", "$p \\neq q$", block=1), node(1, "claim", "$x_n \\in U \\cap V = \\emptyset$", [0], block=1)]
    assert rule_verdicts(g(body + [node(2, "claim", "$p = q$", [0, 1])])) == {}
    # Any later step still can't use it: the assumption no longer holds there.
    later = body + [node(2, "claim", "$p = q$", [1]), node(3, "claim", "$x_n \\in U$", [0])]
    assert list(rule_verdicts(g(later))) == [3]


def test_a_choice_that_depends_on_a_later_variable_is_caught():
    g = Graph(goal="continuity at 1",
              goal_order=[{"name": "ε", "quantifier": "forall"}, {"name": "δ", "quantifier": "exists"},
                          {"name": "x", "quantifier": "forall"}],
              nodes=[node(0, "assume", "$\\varepsilon > 0$, $x$ real"), node(1, "choose", "$\\delta = \\varepsilon/(|x+1|+1)$")],
              variables=[var("ε", "any", 0), var("x", "any", 0), var("δ", "chosen", 1, ["ε", "x"])], blocks=ROOT, reaches_goal=False)
    v = rule_verdicts(g)
    assert list(v) == [1] and "$δ$ depends on $x$" in v[1].message


def test_each_block_is_checked_by_its_shape():
    shape = lambda nodes, bl, goal=True: graphcheck.structure_problems(
        Graph(goal="the sum formula", goal_order=[], blocks=bl, nodes=nodes, variables=[], reaches_goal=goal))
    induction = blocks(("induction", 0), ("base", 1), ("step", 1))
    gauss = [node(0, "assume", "induction on $n$", block=1), node(1, "claim", "$n = 0$: both sides are $0$", [0], block=2),
             node(2, "assume", "$2(0 + \\dots + n) = n(n + 1)$", [0], block=3),
             node(3, "claim", "$2(0 + \\dots + (n + 1)) = (n + 1)(n + 2)$", [2], block=3)]
    assert shape(gauss, induction) == []
    assert shape(gauss[:3] + [gauss[3].model_copy(update={"premises": [0]})], induction) == [
        "the inductive step doesn't rest on the induction hypothesis"]
    assert shape(gauss[:2] + [gauss[2].model_copy(update={"kind": "claim"}), gauss[3]], induction) == [
        "the inductive step doesn't start with the induction hypothesis"]
    no_base = [gauss[0]] + [n.model_copy(update={"id": n.id - 1, "block": 2, "premises": [p - 1 for p in n.premises if p]})
                            for n in gauss[2:]]
    assert shape(no_base, blocks(("induction", 0), ("step", 1))) == ["the induction has no base case"]
    assert shape(gauss, induction, goal=False) == ["no step states the goal: the sum formula"]

    chain = [node(0, "assume", "Let $x$", block=1),
             node(1, "claim", "$x \\in f^{-1}(A \\cap B)$ iff $f(x) \\in A \\cap B$", [0], block=2),
             node(2, "claim", "iff $f(x) \\in A$ and $f(x) \\in B$", [1], block=2), node(3, "claim", "iff ...", [2], block=2)]
    in_let = blocks(("let", 0), ("chain", 1))
    assert shape(chain, in_let) == []
    assert shape(chain[:3] + [chain[3].model_copy(update={"premises": [0]})], in_let) == [
        "the chain from step 1 breaks at step 3: it doesn't follow from step 2"]

    split = blocks(("cases", 0), ("case", 1), ("case", 1))
    cases = [node(0, "assume", "$n$ even", block=2), node(1, "claim", "$n^2 + n$ even", [0], block=2),
             node(2, "assume", "$n$ odd", block=3), node(3, "claim", "$n^2 + n$ even", [2], block=3)]
    assert shape(cases, split) == [] and graphcheck.results(Graph(
        goal="", goal_order=[], blocks=split, nodes=cases, variables=[], reaches_goal=True), 0) == [1, 3]
    assert shape(cases[:3] + [cases[3].model_copy(update={"premises": [1]})], split) == [
        "case 2 doesn't rest on its case's assumption"]
    assert shape(cases[:2], blocks(("cases", 0), ("case", 1))) == ["a proof by cases needs at least two cases"]


def test_blocks_must_nest_and_hold_consecutive_steps():
    g = de_morgan(DE_MORGAN)
    assert graph_problems(g, None) == []
    bad = lambda **kw: graph_problems(Graph(**{**g.model_dump(), **kw}), None)
    assert "consecutive" in bad(nodes=[DE_MORGAN[0], DE_MORGAN[2].model_copy(update={"id": 1}),
                                       DE_MORGAN[1].model_copy(update={"id": 2, "premises": [0]}), *DE_MORGAN[3:]])[0]
    assert "a case sits directly inside cases" in bad(blocks=blocks(("case", 0), ("let", 0)))[0]
    assert "a case sits directly inside cases" in bad(blocks=blocks(("cases", 0), ("let", 1)))[0]
    assert "block 0 must be" in bad(blocks=[{"id": 0, "kind": "let", "parent": None}])[0]
    assert "has no nodes" in bad(blocks=blocks(("let", 0), ("let", 0), ("let", 0)))[0]


def test_graph_problems_finds_forward_premises_and_missing_lines():
    g = DVD_SAME_K.model_copy(deep=True)
    g.nodes[2].premises = [4]
    errs = graph_problems(g, 5)
    assert any("earlier" in e for e in errs) and any("missing lines [4]" in e for e in errs)
    assert graph_problems(DVD_SAME_K, 4) == [] and graph_problems(DVD_SAME_K, None) == []


def test_judges_must_agree_unanimously():
    yes, no = Mark(verdict="ok", reason=""), Mark(verdict="wrong", reason="That doesn't follow.")
    maybe = Mark(verdict="unsure", reason="Needs the lemma that p² even makes p even.")
    assert unanimous(1, [yes, yes, yes]).status == "ok"
    split = unanimous(1, [yes, no, yes])
    assert split.status == "unsure" and split.comments == ["Wrong: That doesn't follow."]
    assert unanimous(1, [no, no, no]).status == "wrong" and unanimous(1, [no, no, no]).comments == []
    # One unsure judge is enough for a ?, even when the others call it wrong; the student sees why.
    one = unanimous(1, [no, maybe, no])
    assert one.status == "unsure" and "Unsure: Needs the lemma that p² even makes p even." in one.comments and one.detail == "0 of 3 judges accept, 1 unsure"
    assert unanimous(1, [yes, maybe, yes]).status == "unsure"
    assert unanimous(1, [yes, None, yes]).status == "unsure"


class Fake:
    """Answers by schema: each step's Form from `forms` (a blank one when it has none), each judge from `marks` (by step
    id, a list of oks per judge)."""
    name = "fake"

    def __init__(self, forms, marks):
        self.forms, self.marks, self.calls, self.lock = forms, marks, [], threading.Lock()

    def complete(self, *, model, system, messages, schema, effort, max_tokens, on_token=None):
        text = messages[0]["content"][0]["text"]
        if schema is graphcheck.Form:
            step = int(text.split("\nStep ")[1].split(":")[0])
            with self.lock:
                self.calls.append(f"Form {step}")
            obj = next((f.model_dump() for f in self.forms if f.node == step), form(step, [], names=[]).model_dump())
        else:
            step = int(text.split("The step [")[1].split("]")[0])
            with self.lock:  # judges run in threads: the k-th judge of a step gets marks[step][k]
                self.calls.append(f"Mark {step}")
                ok = self.marks[step][self.calls.count(f"Mark {step}") - 1]
            obj = {"verdict": "ok" if ok else "wrong", "reason": "" if ok else "no"}
        return Completion(text=json.dumps(obj), parsed=schema.model_validate(obj))


def test_check_runs_rules_then_sympy_then_judges(monkeypatch):
    monkeypatch.setattr(lean, "ready", lambda: False)
    g = DVD_SAME_K.model_copy(deep=True)
    fake = Fake([], {2: [True] * 3, 3: [True] * 3, 5: [True, False, True]})
    llm.set_provider(fake)
    r = check(g, "u1")
    by = {n["node"]: (n["status"], n["tier"]) for n in r["nodes"]}
    assert by == {2: ("ok", "judged"), 3: ("ok", "judged"), 4: ("wrong", "tested"), 5: ("unsure", "judged")}
    assert r["complete"] is False and r["message"] == "Look again at steps 4, 5."
    # The rule settled step 4, so only 2, 3 and 5 reach the judges.
    assert sorted(c for c in fake.calls if c.startswith("Form")) == ["Form 2", "Form 3", "Form 5"]
    assert sum(c.startswith("Mark") for c in fake.calls) == 9


def test_every_step_ok_is_complete_and_says_how_sure(monkeypatch):
    monkeypatch.setattr(lean, "ready", lambda: False)
    g = Graph(goal="$a + b$ is even", goal_order=[],
              nodes=[node(0, "given", "$a = 2k$"), node(1, "given", "$b = 2m$"),
                     node(2, "claim", "$a + b = 2(k + m)$", [0, 1], 0), node(3, "claim", "$a + b$ is even", [2], 1)],
              variables=[var("a", "given"), var("b", "given"), var("k", "given"), var("m", "given")], blocks=ROOT, reaches_goal=True)
    llm.set_provider(Fake([form(2, [("a + b", "2*(k + m)")], [("a", "2*k"), ("b", "2*m")])], {3: [True] * 3}))
    r = check(g, "u1")
    assert [(n["status"], n["tier"]) for n in r["nodes"]] == [("ok", "tested"), ("ok", "judged")]
    assert r["complete"] is True and "1 tested, 1 judged" in r["message"] and "Only Proved is certain" in r["message"]


def test_lean_proves_from_axioms_only(monkeypatch):
    out = ("'node2' depends on axioms: [propext, Classical.choice, Quot.sound]\n'node3' depends on axioms: [sorryAx]\n"
           "'node4' does not depend on any axioms\n")
    monkeypatch.setattr(lean, "_run", lambda src: (out, 1.0))
    assert graphcheck.lean_run({2: "(x : ℕ) : x = x", 3: "(x : ℕ) : x = 1", 4: ": True"}) == ({2, 4}, {}, False)
    with store.connect() as c:  # every run is logged, so a failed one is visible
        assert json.loads(c.execute("SELECT response FROM request_log WHERE endpoint = 'lean'").fetchone()[0]) == \
            {"proved": [2, 4], "refuted": {}, "vacuous": [], "timed_out": False, "seconds": 1.0}


# What Lean 4's `plausible` printed for L1g's coprime_assert step, and for a statement it can't test.
PLAUSIBLE = """/tmp/x/check.lean:{line}:2: error: \n===================
Found a counter-example!
a := 2
b := 4
c := -4
guard: ⋯
guard: ⋯
issue: ⋯ does not hold
(1 shrinks)
-------------------
/tmp/x/check.lean:{other}:2: error: Failed to create a `testable` instance for `∀ (x : ℝ), x = x`.
"""


def test_plausible_counterexamples_refute_a_step(monkeypatch):
    seen = []

    def run(src):
        seen.append(src)
        lines = src.split("\n")
        at = [k + 1 for k, ln in enumerate(lines) if ln == "  plausible"]  # Lean counts lines from 1
        return PLAUSIBLE.format(line=at[0], other=at[1]), 1.0
    monkeypatch.setattr(lean, "_run", run)
    proved, refuted, _ = graphcheck.lean_run({5: "(a b c : ℤ) (h3 : a ∣ c) (h4 : b ∣ c) : a * b ∣ c",
                                              6: "(x : ℝ) : x = x"})
    assert proved == set() and refuted == {5: [("a", "2"), ("b", "4"), ("c", "-4")]}
    assert "theorem refute5 (a b c : ℤ) (h3 : a ∣ c) (h4 : b ∣ c) : a * b ∣ c := by\n  plausible" in seen[0]


def test_a_refuted_step_is_wrong_and_skips_the_judges(monkeypatch):
    monkeypatch.setattr(lean, "ready", lambda: True)
    monkeypatch.setattr(graphcheck, "lean_run", lambda st, user="": (set(), {1: [("a", "2"), ("c", "-4")]}, False))
    g = Graph(goal="$ab \\mid c$", goal_order=[], variables=[], blocks=ROOT, reaches_goal=True,
              nodes=[node(0, "given", "$a \\mid c$, $b \\mid c$"), node(1, "claim", "$ab \\mid c$", [0], 0)])
    f = form(1, [], names=[])
    f.lean = "(a b c : ℤ) (h : a ∣ c ∧ b ∣ c) : a * b ∣ c"
    fake = Fake([f], {})
    llm.set_provider(fake)
    r = check(g, "u1")
    assert (r["nodes"][0]["status"], r["nodes"][0]["tier"]) == ("wrong", "tested")
    assert r["nodes"][0]["message"] == "Fails for $a = 2$, $c = -4$, where the steps it rests on hold."
    assert not any(c.startswith("Mark") for c in fake.calls)


def test_a_step_that_introduces_its_variables_is_never_tested(monkeypatch):
    # "√2 = b/a with a, b ∈ ℤ" says such a and b exist, so a = b = 1 breaking it is no counterexample (a correct proof
    # got ✗ Tested for it on 2026-10-23). Step 1's a and b are recorded by the linker; step 2's k isn't, but its text
    # says "for some". Neither SymPy nor plausible may decide them: they go to the judges.
    monkeypatch.setattr(lean, "ready", lambda: True)
    monkeypatch.setattr(graphcheck, "lean_run", lambda st, user="": (set(), {2: [("b", "1"), ("k", "0")]}, False))
    g = Graph(goal="$\\sqrt{2}$ is irrational", goal_order=[], blocks=ROOT, reaches_goal=True,
              nodes=[node(0, "assume", "$\\sqrt{2}$ is rational"),
                     node(1, "claim", "$\\sqrt{2} = \\frac{b}{a}$ with $a, b \\in \\mathbb{Z}$", [0]),
                     node(2, "claim", "$b = 2k$ for some integer $k$", [1])],
              variables=[var("a", "obtained", 1), var("b", "obtained", 1)])
    f1 = form(1, [("sqrt(2)", "b/a")], names=("a", "b"))
    f2 = form(2, [("b", "2*k")], names=("b", "k"))
    f2.lean = "(b k : ℤ) : b = 2 * k"
    fake = Fake([f1, f2], {0: [True] * 3, 1: [True] * 3, 2: [True] * 3})
    llm.set_provider(fake)
    r = check(g, "u1")
    assert [(n["status"], n["tier"]) for n in r["nodes"]] == [("ok", "judged")] * 3


def test_the_rest_of_an_introduction_line_is_never_tested(monkeypatch):
    # "p, q ∈ ℤ, q ≠ 0, √2 = p/q" split into a choice and a claim: the claim is what p and q were chosen to satisfy,
    # not something every p and q must (a correct proof got ✗ at p = -1, q = 1 on 2026-10-23).
    monkeypatch.setattr(lean, "ready", lambda: False)
    g = Graph(goal="$\\sqrt{2}$ is irrational", goal_order=[], blocks=ROOT, reaches_goal=True,
              nodes=[node(0, "assume", "$\\sqrt{2}$ is rational"),
                     node(1, "choose", "$p, q \\in \\mathbb{Z}$, $q \\neq 0$", [0], line=1),
                     node(2, "claim", "$\\sqrt{2} = \\frac{p}{q}$", [0, 1], line=1)],
              variables=[var("p", "obtained", 1), var("q", "obtained", 1)])
    llm.set_provider(Fake([form(2, [("sqrt(2)", "p/q")], names=("p", "q"))], {i: [True] * 3 for i in range(3)}))
    assert check(g, "u1")["nodes"][2]["tier"] == "judged"


def test_a_step_is_translated_with_how_its_variables_were_chosen(monkeypatch):
    # q ≠ 0 holds wherever q is in scope, so the translator gets the step that chose q even when the step doesn't link
    # to it: "fails for p = q = 0" ignored it on 2026-10-23. An assumption is left out: everything after "suppose p is
    # the largest prime" would rest on a false premise, and refutations inside a contradiction would vanish.
    sent = {}

    def call(**kw):
        text = kw["content"][0]["text"]
        sent[int(text.split("\nStep ")[1].split(":")[0])] = text.split("Premises:\n")[1]
        raise llm.LLMError("no model here")

    monkeypatch.setattr(graphcheck.llm, "structured_call", call)
    g = Graph(goal="", goal_order=[], blocks=ROOT, reaches_goal=True,
              nodes=[node(0, "choose", "$p, q \\in \\mathbb{Z}$, $q \\neq 0$"), node(1, "claim", "$p = 2k$", [0]),
                     node(2, "claim", "$\\gcd(p, q) = 2$", [1]), node(3, "assume", "$r$ is the largest prime"),
                     node(4, "claim", "$r! + 1$ is prime", [])],
              variables=[var("p", "chosen", 0), var("q", "chosen", 0), var("r", "any", 3)])
    graphcheck.formalize(g, [g.nodes[i] for i in (1, 2, 4)], "u1")
    assert sent[1] == "  [0] $p, q \\in \\mathbb{Z}$, $q \\neq 0$"
    assert sent[2] == "  [1] $p = 2k$\n  [0] $p, q \\in \\mathbb{Z}$, $q \\neq 0$"
    assert sent[4] == "  (none)"


def test_judges_give_their_reason_before_their_verdict():
    # Verdict first, three judges wrote "wrong" and then reasoned that the step was right (2026-10-23).
    assert list(Mark.model_json_schema()["properties"]) == ["reason", "verdict"]


def test_a_lean_timeout_is_logged_and_marked_on_the_judged_steps(monkeypatch):
    monkeypatch.setattr(lean, "ready", lambda: True)
    monkeypatch.setattr(lean, "_run", lambda src: ("error: Lean took longer than 300s", 300.0))
    g = Graph(goal="$a + b$ is even", goal_order=[],
              nodes=[node(0, "given", "$a$, $b$ even"), node(1, "claim", "$a + b$ is even", [0], 0)],
              variables=[], blocks=ROOT, reaches_goal=True)
    f = form(1, [], names=[])
    f.lean = "(a b : ℤ) (h : Even a ∧ Even b) : Even (a + b)"
    llm.set_provider(Fake([f], {1: [True] * 3}))
    r = check(g, "u1")
    assert r["nodes"][0]["tier"] == "judged" and r["nodes"][0]["detail"].endswith("(Lean timed out)")
    with store.connect() as c:
        assert c.execute("SELECT valid FROM request_log WHERE endpoint = 'lean'").fetchone()[0] == 0


def test_a_step_from_contradictory_premises_is_not_proved(monkeypatch):
    out = "'node1' does not depend on any axioms\n'vacuous1' does not depend on any axioms\n" \
          "'node2' does not depend on any axioms\n'vacuous2' does not depend on any axioms\n"
    seen = []
    monkeypatch.setattr(lean, "_run", lambda src: (seen.append(src), (out, 1.0))[1])
    assert graphcheck.lean_run({1: "(n : ℤ) (h : Even n ∧ Odd n) : n = 5",
                                   2: "(n : ℤ) (h : Even n ∧ Odd n) : False"})[0] == {2}
    assert "theorem vacuous1 (n : ℤ) (h : Even n ∧ Odd n) : False := by" in seen[0]


def test_statements_split_at_the_colon_outside_brackets():
    assert graphcheck.split_statement("(a : ℤ) (h : (2 : ℝ) = a) : ∀ x : ℝ, x = x") == (
        "(a : ℤ) (h : (2 : ℝ) = a)", "∀ x : ℝ, x = x")
    assert graphcheck.split_statement(": True") == ("", "True")
    assert graphcheck.split_statement("(a : ℤ)") is None


def test_lean_traps_where_a_false_step_could_be_proved():
    assert graphcheck.lean_trap("(k : ℤ) : (2 * k + 1) / 2 = k")
    assert graphcheck.lean_trap("(x y : ℕ) : x - y ≥ 0")
    assert not graphcheck.lean_trap("(p q : ℤ) (h : (2 : ℝ) = (p : ℝ) / (q : ℝ)) : p ^ 2 = 2 * q ^ 2")
    assert not graphcheck.lean_trap("(n k : ℤ) (h : n = 2 * k + 1) : n - 1 = 2 * k")


def test_agents_claim_requests_by_lane(tmp_path):
    for i, copy, title in [(1, 0, "Graph"), (2, 1, "Mark"), (3, 2, "Mark"), (4, 0, "Mark"), (5, 0, "Form")]:
        (tmp_path / f"{i}.request.json").write_text(json.dumps({"id": i, "copy": copy, "schema": {"title": title}}))
    assert claim(tmp_path, "1/3")[0]["id"] == 2
    assert claim(tmp_path, "1/3") is None  # 2 is claimed now
    assert claim(tmp_path, "0/3")[0]["id"] == 4  # judges only: never the graph call, which sees the whole proof
    assert claim(tmp_path, "Form")[0]["id"] == 5  # a lane by schema title
    assert claim(tmp_path, "prep")[0]["id"] == 1
    assert claim(tmp_path, "prep") is None
    assert claim(tmp_path, None)[0]["id"] == 3


class Scripted:
    """Answers each schema with the next of its scripted answers, recording which model was asked."""
    name = "fake"

    def __init__(self, answers):
        self.answers, self.models = answers, []

    def complete(self, *, model, system, messages, schema, effort, max_tokens, on_token=None):
        self.models.append((schema.__name__, model))
        obj = self.answers[schema.__name__].pop(0)
        return Completion(text=json.dumps(obj), parsed=schema.model_validate(obj))


def test_the_graph_is_chunked_cheaply_then_linked(monkeypatch):
    monkeypatch.setattr(graphcheck.config, "CHUNK_MODEL", "cheap")
    monkeypatch.setattr(graphcheck.config, "GRAPH_MODEL", "strong")
    chunks = {"blocks": ROOT,
              "nodes": [{"id": 0, "kind": "given", "line": None, "text": "$a$, $b$ even", "block": 0},
                        {"id": 1, "kind": "claim", "line": 0, "text": "$a + b = 2(k + m)$", "block": 0},
                        {"id": 2, "kind": "claim", "line": 1, "text": "$a + b$ is even", "block": 0}]}
    stray = {**chunks, "nodes": chunks["nodes"][:2] + [{**chunks["nodes"][2], "block": 1}]}  # no block 1: retried
    links = {"goal": "$a + b$ is even", "goal_order": [], "variables": [], "reaches_goal": True,
             "links": [{"node": 1, "premises": [0]}, {"node": 2, "premises": [1]}]}
    missing = {**links, "links": links["links"][:1]}  # step 2 left out: retried with the error
    fake = Scripted({"Chunks": [stray, chunks], "Links": [missing, links]})
    llm.set_provider(fake)
    g = graphcheck.make_graph("a, b even => a + b even", ["a+b = 2(k+m)", "so even"], "u1")
    assert [n.premises for n in g.nodes] == [[], [0], [1]] and g.reaches_goal and g.nodes[2].line == 1
    assert [b.kind for b in g.blocks] == ["proof"]
    assert fake.models == [("Chunks", "cheap"), ("Chunks", "cheap"), ("Links", "strong"), ("Links", "strong")]


def test_judges_see_the_assumptions_a_conclusion_discharges():
    g = Graph(goal="$g \\circ f$ is injective", goal_order=[],
              nodes=[node(0, "given", "$g$ is injective"), node(1, "assume", "Let $x, y$ with $g(f(x)) = g(f(y))$"),
                     node(2, "claim", "$f(x) = f(y)$", [0, 1]), node(3, "claim", "$x = y$", [2]),
                     node(4, "claim", "$g \\circ f$ is injective", [3])],
              variables=[], blocks=ROOT, reaches_goal=True)
    text = graphcheck.describe(g, g.nodes[4])
    assert "Assumptions in force:\n[1] Let $x, y$" in text
    assert "Assumptions in force" not in graphcheck.describe(g, g.nodes[2])  # [1] is a direct premise there


def test_judges_see_the_blocks_a_step_sits_in():
    g = Graph(goal="$\\sqrt 2$ is irrational", goal_order=[], blocks=blocks(("contradiction", 0)), reaches_goal=True,
              nodes=[node(0, "assume", "Suppose $\\sqrt 2 = p/q$ in lowest terms", block=1),
                     node(1, "claim", "$p^2 = 2q^2$", block=1), node(2, "claim", "$p$ is even", [1], block=1)],
              variables=[])
    text = graphcheck.describe(g, g.nodes[2])
    assert "Assumptions in force:\n[0] Suppose" in text and "Where it is: inside the proof by contradiction" in text


def test_lean_import_survives_missing_lean_dir(tmp_path):
    """After a Codespace restart /tmp/mellow-lean is gone: the app must still start, with Lean not ready."""
    env = {**os.environ, "MELLOW_LEAN_DIR": str(tmp_path / "gone")}
    out = subprocess.run([sys.executable, "-c", "from app import lean; print(lean.ready())"], env=env,
                         capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"
