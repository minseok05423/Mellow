"""SymPy checking. The 5 known-wrong steps (SPEC testing) must be refuted with a counterexample;
correct steps verified; anything SymPy can't settle must stay unverified (never a verdict)."""
import pytest

from app.verify import check_step

WRONG = [  # (step, context, symbols that must appear in the counterexample)
    ("a^2+b^2", ["(a+b)^2"], {"a", "b"}),
    ("x = 2", ["x^2 = 4"], {"x"}),
    ("x = 5", ["2x + 3 = 7"], {"x"}),
    (r"\frac{a+c}{b+d}", [r"\frac{a}{b}+\frac{c}{d}"], {"a", "b", "c", "d"}),
    ("a+b", [r"\sqrt{a^2+b^2}"], {"a", "b"}),
]

RIGHT = [
    ("a^2+2ab+b^2", ["(a+b)^2"]),
    ("= a^2+2ab+b^2", ["(a+b)^2"]),          # continuation line
    ("x = 2", ["2x + 3 = 7"]),
    ("2q^2=4k^2", ["2q^2=p^2", "p=2k"]),     # substitution from an earlier line
    ("q^2=2k^2", ["2q^2=p^2", "p=2k", "2q^2=4k^2"]),
    (r"\frac{x^2-1}{x-1}", ["x+1"]),
]

UNSETTLED = [
    ("(a+b)^2 = a^2+2ab+b^2", ["(a+b)^2"]),  # review fix 1: equation vs expression
    (r"p \text{ is even}", ["2q^2=p^2"]),     # prose
    ("x^2 > 4", ["x > 2"]),                   # inequality
    ("x = 1", []),                            # nothing above
    ("a = b = c", ["a"]),                     # chained equalities
]


@pytest.mark.parametrize("step,context,syms", WRONG)
def test_wrong_steps_are_refuted_with_counterexample(step, context, syms):
    r = check_step(step, context)
    assert r.verdict == "refuted", r
    assert syms <= set(r.counterexample), r.counterexample


@pytest.mark.parametrize("step,context", RIGHT)
def test_correct_steps_are_verified(step, context):
    assert check_step(step, context).verdict == "verified"


@pytest.mark.parametrize("step,context", UNSETTLED)
def test_unsettled_steps_get_no_verdict(step, context):
    assert check_step(step, context).verdict == "unverified"


def test_counterexample_is_concrete_and_small():
    r = check_step("a^2+b^2", ["(a+b)^2"])
    assert r.counterexample == {"a": "1", "b": "1"} and r.values == {"before": "4", "after": "2"}
    r = check_step("x = 2", ["x^2 = 4"])
    assert r.counterexample == {"x": "-2"}


def test_timeout_is_unverified():
    r = check_step("a^2+b^2", ["(a+b)^2"], timeout=0.0001)
    assert r.verdict == "unverified" and r.reason == "timeout"
