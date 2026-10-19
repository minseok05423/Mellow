---
name: math-verify
description: How Mellow checks a student's (or the LLM's) math step with SymPy, and how to word feedback when SymPy can't decide. Use this whenever you work on the /check endpoint, wrong-step / counterexample feedback, predict-then-reveal answer checking, verifying LLM-proposed algebra in /scene or /explain, the check fixtures, or any code that parses student LaTeX into SymPy — even if the user only says "is this step right", "grade the student's step", or "catch wrong steps".
---

# Math verification

The LLM proposes; SymPy verifies. A student should never see "correct" or
"wrong" unless SymPy actually proved it, because a confident wrong verdict
from the board destroys trust faster than anything else. Everything SymPy
can't settle gets *question-style* feedback that pushes the student to justify
the step themselves.

## The three outcomes

Every check returns exactly one of:

| verdict      | when | student sees |
|--------------|------|--------------|
| `verified`   | SymPy proved the step follows (difference simplifies to 0, or implication holds) | affirmation + the board animates why |
| `refuted`    | SymPy found a concrete counterexample (numbers where the step's claim fails) | the counterexample, animated: "try p = 3: …" |
| `unverified` | parse failed, timeout, non-algebraic step (parity, "hence", ∀/∃, inequalities SymPy can't settle) | a question: "Does this follow from the line above? What would you need to know about p?" |

Never upgrade `unverified` to a verdict because the LLM is confident. The LLM
may *word* the question, but the verdict comes from SymPy alone.

## Pipeline

1. **Parse** student LaTeX with `sympy.parsing.latex.parse_latex` (needs
   `antlr4-python3-runtime`; pin the version SymPy asks for). Wrap in
   try/except → `unverified` on failure. Normalize first: strip `\left`/`\right`,
   `\,`, `\ `, and trailing punctuation.
2. **Declare symbols** with assumptions from context (`p, q = symbols('p q',
   integer=True, positive=True)`), and substitute parsed symbols by name so
   assumptions apply. Assumptions change what `simplify` can prove.
3. **Check** using one of the patterns below, under a hard time limit (~2 s,
   run in a subprocess or thread with timeout — `simplify` can hang).
4. **Counterexample search** when the symbolic check is inconclusive or false:
   evaluate both sides at a handful of random/small integer points. A failing
   point is a `refuted` verdict *with* that point as evidence. Passing points
   prove nothing — they leave it `unverified` (or support `verified` only if
   the symbolic check already succeeded).

## Patterns

```python
from sympy import symbols, simplify, expand, Eq, solve, factor
p, q, k = symbols('p q k', integer=True)

# A. Expression rewrite: is new_expr == old_expr?
def same_expr(old, new):
    return simplify(old - new) == 0

# B. Equation step: does new equation follow from old by algebra?
#    Compare lhs - rhs up to a nonzero constant factor.
def equation_equivalent(old: Eq, new: Eq):
    a = expand(old.lhs - old.rhs)
    b = expand(new.lhs - new.rhs)
    if b == 0:
        return a == 0
    ratio = simplify(a / b)
    return ratio.is_constant() and ratio != 0

# C. Substitution step (√2 proof: "write p = 2k" turns 2q²=p² into 2q²=4k²)
old = Eq(2*q**2, p**2)
new = Eq(2*q**2, 4*k**2)
sub = {p: 2*k}
ok = simplify((old.lhs - old.rhs).subs(sub) - (new.lhs - new.rhs)) == 0
# equivalently the spec's check: simplify((2*k)**2 - 4*k**2) == 0

# D. Counterexample search for a claimed identity lhs == rhs
import itertools
def find_counterexample(lhs, rhs, syms, values=(1, 2, 3, 0, -1, -2, -3)):  # small positives first: friendlier counterexamples
    for combo in itertools.product(values, repeat=len(syms)):
        env = dict(zip(syms, combo))
        try:
            l, r = lhs.subs(env), rhs.subs(env)
        except ZeroDivisionError:
            continue
        if l.is_finite and r.is_finite and simplify(l - r) != 0:
            return env  # e.g. {a: 1, b: 1} refutes (a+b)^2 = a^2 + b^2
    return None
```

`scripts/check_step.py` bundles these into a CLI for quick manual checks:

```bash
python .claude/skills/math-verify/scripts/check_step.py "2q^2=p^2" "2q^2=4k^2" --sub "p=2k"
python .claude/skills/math-verify/scripts/check_step.py "(a+b)^2" "a^2+b^2"
```

## What counts as non-algebraic (→ `unverified`, question feedback)

- Parity / divisibility reasoning: "p² even ⇒ p even", "gcd(p,q)=1".
- Logical connectives and quantifiers: "hence", "for all ε there exists δ".
- Inequalities that need case analysis or the triangle inequality.
- Anything where the student's step is prose.

For these, write a question that names the specific gap, not a generic
"are you sure?". Good: "You concluded p is even from p² being even. Why can't
an odd number have an even square?" Bad: "Is this correct?"

## Fixtures

The test suite needs 5 deliberately wrong steps that `/check` must `refute`
(each with an expected counterexample), plus correct steps that must `verify`.
Wrong steps that are only catchable by reasoning (not algebra) belong in the
`unverified` set — don't count them toward the 5.
