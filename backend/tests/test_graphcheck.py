"""The graph check (app/graphcheck.py) without a model or Lean: rules, SymPy tests, unanimous judges, the cascade."""
import json
import threading

import pytest

from app import graphcheck, lean, llm, store
from app.graphcheck import (Equation, Form, Graph, Mark, Node, Variable, check, graph_problems, parse, rule_verdicts,
                            try_values, unanimous)
from app.llm import Completion
from leaneval.agentq import claim


def node(i, kind, text, premises=(), line=None):
    return Node(id=i, kind=kind, line=None if kind == "given" else (i if line is None else line), text=text,
                premises=list(premises))


def var(name, kind, at=None, deps=()):
    return Variable(name=name, kind=kind, introduced_at=at, depends_on=list(deps))


def form(i, eqs, subs=(), names=("a", "b", "k", "m"), domain="integer", given=()):
    return Form(node=i, variables=[{"name": n, "domain": domain} for n in names],
                substitute=[Equation(lhs=l, rhs=r) for l, r in subs],
                given=[{"lhs": l, "rel": rel, "rhs": r} for l, rel, r in given],
                equations=[Equation(lhs=l, rhs=r) for l, r in eqs], lean="")


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


DVD_SAME_K = Graph(
    goal="$a \\mid c$", goal_order=[],
    nodes=[node(0, "given", "$a \\mid b$"), node(1, "given", "$b \\mid c$"),
           node(2, "claim", "$b = ak$ for some integer $k$", [0], 0),
           node(3, "claim", "$c = bk$ for some integer $k$", [1], 1),
           node(4, "claim", "$c = (ak)k = ak^2$", [2, 3], 2), node(5, "claim", "$a \\mid c$", [4], 3)],
    variables=[var("a", "given"), var("b", "given"), var("c", "given"), var("k", "obtained", 2, ["a", "b"]),
               var("k", "obtained", 3, ["b", "c"])], concludes=5)


def test_a_name_reused_for_a_second_object_is_caught_where_both_meet():
    v = rule_verdicts(DVD_SAME_K)
    assert list(v) == [4] and v[4].status == "wrong" and "step 2 and step 3" in v[4].message


def test_the_same_name_in_separate_directions_is_fine():
    g = Graph(goal="sets are equal", goal_order=[],
              nodes=[node(0, "assume", "$x \\in (A \\cup B)^c$"), node(1, "claim", "$x \\in A^c \\cap B^c$", [0]),
                     node(2, "assume", "$x \\in A^c \\cap B^c$"), node(3, "claim", "$x \\in (A \\cup B)^c$", [2]),
                     node(4, "claim", "the sets are equal", [1, 3])],
              variables=[var("A", "given"), var("B", "given"), var("x", "any", 0), var("x", "any", 2)], concludes=4)
    assert rule_verdicts(g) == {}


def test_a_choice_that_depends_on_a_later_variable_is_caught():
    g = Graph(goal="continuity at 1",
              goal_order=[{"name": "ε", "quantifier": "forall"}, {"name": "δ", "quantifier": "exists"},
                          {"name": "x", "quantifier": "forall"}],
              nodes=[node(0, "assume", "$\\varepsilon > 0$, $x$ real"), node(1, "choose", "$\\delta = \\varepsilon/(|x+1|+1)$")],
              variables=[var("ε", "any", 0), var("x", "any", 0), var("δ", "chosen", 1, ["ε", "x"])], concludes=None)
    v = rule_verdicts(g)
    assert list(v) == [1] and "$δ$ depends on $x$" in v[1].message


def test_graph_problems_finds_forward_premises_and_missing_lines():
    g = DVD_SAME_K.model_copy(deep=True)
    g.nodes[2].premises = [4]
    errs = graph_problems(g, 5)
    assert any("earlier" in e for e in errs) and any("missing lines [4]" in e for e in errs)
    assert graph_problems(DVD_SAME_K, 4) == [] and graph_problems(DVD_SAME_K, None) == []


def test_judges_must_agree_unanimously():
    yes, no = Mark(ok=True, reason=""), Mark(ok=False, reason="That doesn't follow.")
    assert unanimous(1, [yes, yes, yes]).status == "ok"
    assert unanimous(1, [yes, no, yes]).status == "unsure" and "doesn't follow" in unanimous(1, [yes, no, yes]).message
    assert unanimous(1, [no, no, no]).status == "wrong"
    assert unanimous(1, [yes, None, yes]).status == "unsure"


class Fake:
    """Answers by schema: Forms from `forms`, each judge from `marks` (by step id, a list of oks per judge)."""
    name = "fake"

    def __init__(self, forms, marks):
        self.forms, self.marks, self.calls, self.lock = forms, marks, [], threading.Lock()

    def complete(self, *, model, system, messages, schema, effort, max_tokens, on_token=None):
        text = messages[0]["content"][0]["text"]
        if schema is graphcheck.Forms:
            self.calls.append("Forms")
            obj = {"forms": [f.model_dump() for f in self.forms]}
        else:
            step = int(text.split("The step [")[1].split("]")[0])
            with self.lock:  # judges run in threads: the k-th judge of a step gets marks[step][k]
                self.calls.append(f"Mark {step}")
                ok = self.marks[step][self.calls.count(f"Mark {step}") - 1]
            obj = {"ok": ok, "reason": "" if ok else "no"}
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
    assert fake.calls.count("Forms") == 1 and sum(c.startswith("Mark") for c in fake.calls) == 9


def test_every_step_ok_is_complete_and_says_how_sure(monkeypatch):
    monkeypatch.setattr(lean, "ready", lambda: False)
    g = Graph(goal="$a + b$ is even", goal_order=[],
              nodes=[node(0, "given", "$a = 2k$"), node(1, "given", "$b = 2m$"),
                     node(2, "claim", "$a + b = 2(k + m)$", [0, 1], 0), node(3, "claim", "$a + b$ is even", [2], 1)],
              variables=[var("a", "given"), var("b", "given"), var("k", "given"), var("m", "given")], concludes=3)
    llm.set_provider(Fake([form(2, [("a + b", "2*(k + m)")], [("a", "2*k"), ("b", "2*m")])], {3: [True] * 3}))
    r = check(g, "u1")
    assert [(n["status"], n["tier"]) for n in r["nodes"]] == [("ok", "tested"), ("ok", "judged")]
    assert r["complete"] is True and "1 tested, 1 judged" in r["message"] and "Only Proved is certain" in r["message"]


def test_lean_proves_from_axioms_only(monkeypatch):
    out = ("'node2' depends on axioms: [propext, Classical.choice, Quot.sound]\n'node3' depends on axioms: [sorryAx]\n"
           "'node4' does not depend on any axioms\n")
    monkeypatch.setattr(lean, "_run", lambda src: (out, 1.0))
    assert graphcheck.lean_proved({2: "(x : ℕ) : x = x", 3: "(x : ℕ) : x = 1", 4: ": True"}) == ({2, 4}, False)
    with store.connect() as c:  # every run is logged, so a failed one is visible
        assert json.loads(c.execute("SELECT response FROM request_log WHERE endpoint = 'lean'").fetchone()[0]) == \
            {"proved": [2, 4], "vacuous": [], "timed_out": False, "seconds": 1.0}


def test_a_lean_timeout_is_logged_and_marked_on_the_judged_steps(monkeypatch):
    monkeypatch.setattr(lean, "ready", lambda: True)
    monkeypatch.setattr(lean, "_run", lambda src: ("error: Lean took longer than 300s", 300.0))
    g = Graph(goal="$a + b$ is even", goal_order=[],
              nodes=[node(0, "given", "$a$, $b$ even"), node(1, "claim", "$a + b$ is even", [0], 0)],
              variables=[], concludes=1)
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
    assert graphcheck.lean_proved({1: "(n : ℤ) (h : Even n ∧ Odd n) : n = 5",
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
    for i, copy, title in [(1, 0, "Graph"), (2, 1, "Mark"), (3, 2, "Mark"), (4, 0, "Mark"), (5, 0, "Forms")]:
        (tmp_path / f"{i}.request.json").write_text(json.dumps({"id": i, "copy": copy, "schema": {"title": title}}))
    assert claim(tmp_path, "1/3")[0]["id"] == 2
    assert claim(tmp_path, "1/3") is None  # 2 is claimed now
    assert claim(tmp_path, "0/3")[0]["id"] == 4  # judges only: never the graph call, which sees the whole proof
    assert claim(tmp_path, "prep")[0]["id"] == 1
    assert claim(tmp_path, "prep")[0]["id"] == 5
    assert claim(tmp_path, "prep") is None
    assert claim(tmp_path, None)[0]["id"] == 3


def test_judges_see_the_assumptions_a_conclusion_discharges():
    g = Graph(goal="$g \\circ f$ is injective", goal_order=[],
              nodes=[node(0, "given", "$g$ is injective"), node(1, "assume", "Let $x, y$ with $g(f(x)) = g(f(y))$"),
                     node(2, "claim", "$f(x) = f(y)$", [0, 1]), node(3, "claim", "$x = y$", [2]),
                     node(4, "claim", "$g \\circ f$ is injective", [3])],
              variables=[], concludes=4)
    text = graphcheck.describe(g, g.nodes[4])
    assert "Assumptions in force (through its premises):\n[1] Let $x, y$" in text
    assert "Assumptions in force" not in graphcheck.describe(g, g.nodes[2])  # [1] is a direct premise there
