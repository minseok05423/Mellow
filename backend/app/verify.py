"""SymPy verification of one student step against the lines above it (SPEC §4, math-verify skill).

Three outcomes, and only SymPy may produce the first two:
- verified:   the step provably follows.
- refuted:    a concrete counterexample shows it doesn't (returned for the board to animate).
- unverified: anything SymPy can't settle (prose, parity, inequalities, parse failures, timeouts).
              These get question-style feedback, never a verdict.

Runs in a child process with a hard timeout, because simplify/solve can hang.
"""
from __future__ import annotations

import itertools
import multiprocessing as mp
import re
from dataclasses import asdict, dataclass, field

mp.set_forkserver_preload(["sympy", "sympy.parsing.latex", "app.verify"])

TIMEOUT_S = 3.0
SMALL_VALUES = (1, 2, 3, 0, -1, -2, -3)  # small positives first: friendlier counterexamples


@dataclass
class CheckResult:
    verdict: str  # "verified" | "refuted" | "unverified"
    reason: str  # short machine-readable why, e.g. "rewrite_equal", "parse_failed"
    counterexample: dict[str, str] = field(default_factory=dict)  # symbol -> value
    compared_with: str | None = None  # the context line the step was checked against
    values: dict[str, str] = field(default_factory=dict)  # e.g. {"before": "4", "after": "2"} at the counterexample


# Joining words that don't make a line prose: "so x = 2" is still algebra.
CONNECTORS = {"so", "thus", "hence", "then", "therefore", "and", "i.e.", "ie", "which", "gives", "implies"}


def looks_like_words(latex: str) -> bool:
    """MyScript's math mode reads handwritten words as runs of single-letter variables ("l e t f").
    shortcut: any run of 3+ plain letters counts as words, so a product like `abc` is noted rather than
    checked. Upgrade path: MyScript "Raw Content" mode, which separates text from math."""
    bare = re.sub(r"\\[a-zA-Z]+|[\s{}]", "", latex)
    return bool(re.search(r"[a-zA-Z]{3,}", bare))


def is_prose(latex: str) -> bool:
    if looks_like_words(re.sub(r"\\text\s*\{[^}]*\}", " ", latex)):
        return True
    words = " ".join(re.findall(r"\\text\s*\{([^}]*)\}", latex)).lower().replace(",", " ").split()
    return any(w.strip(".:;") not in CONNECTORS for w in words)



def _normalize(latex: str) -> str:
    s = latex.strip().rstrip(".,;")
    s = re.sub(r"\\text\s*\{[^}]*\}", " ", s)  # connector words ("so", "hence"); prose lines never reach here
    s = re.sub(r"\\(left|right)\b", "", s)
    s = re.sub(r"\\[,;:! ]", " ", s)
    s = s.replace(r"\dfrac", r"\frac").replace(r"\tfrac", r"\frac")
    return s.strip()


FUNCTION_NAMES = {"f", "g", "h", "F", "G", "H"}  # letters students use for functions: f(x) stays a function


def _implicit_products(e):
    """parse_latex reads n(n+1) as a function n applied to n+1; a student means the product.
    shortcut: only f, g, h (and capitals) are functions, so a student's own p(x) reads as p*x. Upgrade
    path: take function names from the page's definitions ("let p be a polynomial")."""
    from sympy import Symbol
    from sympy.core.function import AppliedUndef

    return e.replace(
        lambda x: isinstance(x, AppliedUndef) and x.func.__name__ not in FUNCTION_NAMES and len(x.args) == 1,
        lambda x: Symbol(x.func.__name__) * x.args[0])


def _parse(latex: str):
    from sympy.parsing.latex import parse_latex

    s = _normalize(latex)
    if s.count("=") > 1:
        raise ValueError("chained equalities")
    return _implicit_products(parse_latex(s))


def _is_eq(e) -> bool:
    from sympy import Eq

    return isinstance(e, Eq)


def _zero_form(e):
    return e.lhs - e.rhs if _is_eq(e) else e


def _fmt(v) -> str:
    from sympy import latex, nsimplify

    try:
        return latex(nsimplify(v))
    except Exception:
        return str(v)


def _search_counterexample(a, b, syms):
    """Small-integer assignment where a != b, or None."""
    from sympy import simplify

    for combo in itertools.product(SMALL_VALUES, repeat=len(syms)):
        env = dict(zip(syms, combo))
        try:
            va, vb = a.subs(env), b.subs(env)
            if not (va.is_finite and vb.is_finite):
                continue
            if simplify(va - vb) != 0:
                return env, va, vb
        except (ZeroDivisionError, TypeError):
            continue
    return None


def _check_expressions(before, after, context_line) -> CheckResult:
    from sympy import simplify

    if simplify(before - after) == 0:
        return CheckResult("verified", "rewrite_equal", compared_with=context_line)
    syms = sorted(before.free_symbols | after.free_symbols, key=str)
    found = _search_counterexample(before, after, syms)
    if found:
        env, va, vb = found
        return CheckResult(
            "refuted", "rewrite_differs", {str(k): _fmt(v) for k, v in env.items()},
            context_line, {"before": _fmt(va), "after": _fmt(vb)},
        )
    return CheckResult("unverified", "rewrite_inconclusive", compared_with=context_line)


def _check_equations(before, after, context_line) -> CheckResult:
    """Does `after` follow from `before`? Same equation up to a constant factor, or true on every solution."""
    from sympy import S, simplify, solve

    a, b = _zero_form(before), _zero_form(after)
    if simplify(b) == 0:
        return CheckResult("unverified", "trivial_after", compared_with=context_line)
    ratio = simplify(a / b)
    if ratio.is_constant() and ratio != 0:
        return CheckResult("verified", "equation_equivalent", compared_with=context_line)

    # Implication: solve `before` for a variable it shares with `after`, then test `after` on each solution.
    shared = sorted(a.free_symbols & b.free_symbols, key=str)
    for v in shared:
        try:
            sols = solve(a, v)
        except Exception:
            continue
        if not sols:
            continue
        failing = [s for s in sols if simplify(b.subs(v, s)) != 0]
        if not failing:
            return CheckResult("verified", "follows_on_all_solutions", compared_with=context_line)
        # Make the failing solution concrete: plug small values into any remaining symbols.
        sol = failing[0]
        rest = sorted(sol.free_symbols | (b.free_symbols - {v}), key=str)
        for combo in itertools.product(SMALL_VALUES, repeat=len(rest)):
            env = dict(zip(rest, combo))
            val = sol.subs(env)
            if not (val.is_real and val.is_finite):
                continue
            env[v] = val
            if simplify(b.subs(env)) != 0:
                return CheckResult(
                    "refuted", "solution_breaks_step", {str(k): _fmt(x) for k, x in env.items()}, context_line,
                    {"before": _fmt(S(0)), "after": _fmt(b.subs(env))},
                )
    return CheckResult("unverified", "implication_inconclusive", compared_with=context_line)


def _check(current: str, context: list[str]) -> CheckResult:
    from sympy import Symbol

    try:
        cont = current.strip().startswith("=")  # continuation line: "= a^2 + 2ab + b^2"
        after = _parse(current.strip().lstrip("=") if cont else current)
    except Exception:
        return CheckResult("unverified", "parse_failed")

    # Lines like "p = 2k" in the context are substitutions; the step is checked against the
    # nearest earlier line that isn't one.
    subs: dict = {}
    sub_line = None  # the nearest substitution line, named in feedback
    target, target_line = None, None
    for line in reversed(context):
        if is_prose(line):
            continue  # "Let f be ..." is never an algebra target, even after its words are stripped
        try:
            e = _parse(line)
        except Exception:
            continue
        if _is_eq(e) and isinstance(e.lhs, Symbol) and e.lhs not in e.rhs.free_symbols and target is None:
            subs.setdefault(e.lhs, e.rhs)
            sub_line = sub_line or line
            continue
        target, target_line = e, line
        break
    if target is None:
        if subs and _is_eq(after) and not cont and after.free_symbols & set(subs):
            # "p = 2k" above, "p^2 = 4k^2" here: true for all values once p is replaced. Only ever a
            # "verified": if it isn't an identity, the step may rest on a hypothesis written in words
            # ("p^2 = 2q^2" stated in prose, then restated), so a counterexample here could be false.
            from sympy import simplify

            if simplify((after.lhs - after.rhs).subs(subs)) == 0:
                return CheckResult("verified", "substitution_equal", compared_with=sub_line)
        return CheckResult("unverified", "no_previous_line")

    before = target.subs(subs) if subs else target
    if cont:
        # "= X" continues the previous expression (or the previous equation's right side).
        prev_value = before.rhs if _is_eq(before) else before
        return _check_expressions(prev_value, after, target_line)
    if _is_eq(before) != _is_eq(after):
        # Review fix: an equation and a plain expression can't be compared. Never guess a verdict.
        return CheckResult("unverified", "mixed_kinds", compared_with=target_line)
    if _is_eq(after):
        return _check_equations(before, after, target_line)
    return _check_expressions(before, after, target_line)


def _worker(current, context, conn):
    try:
        conn.send(asdict(_check(current, context)))
    except Exception as e:  # never let SymPy errors become verdicts
        conn.send(asdict(CheckResult("unverified", f"error:{type(e).__name__}")))


def check_step(current: str, context: list[str], timeout: float = TIMEOUT_S) -> CheckResult:
    """Check `current` (confirmed LaTeX) against `context` (confirmed lines above, oldest first)."""
    # forkserver: checkers fork from a clean single-threaded helper (forking the threaded web server
    # directly can deadlock), and the helper has SymPy preloaded, so each check starts fast.
    ctx = mp.get_context("forkserver")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_worker, args=(current, context, child), daemon=True)
    proc.start()
    try:
        if parent.poll(timeout):
            return CheckResult(**parent.recv())
        return CheckResult("unverified", "timeout")
    finally:
        proc.kill()
        proc.join()
