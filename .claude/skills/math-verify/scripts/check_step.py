"""Quick manual check: does NEW follow from OLD algebraically?

Usage:
  python check_step.py OLD NEW [--sub "p=2k"]
OLD/NEW are LaTeX expressions or equations. Prints verified / refuted (with a
counterexample) / unverified. Plain-math fallback parser is used if
sympy's LaTeX parser (needs antlr4-python3-runtime) is unavailable.
"""
import argparse
import itertools

from sympy import Eq, simplify, sympify
from sympy.parsing.sympy_parser import (
    convert_xor, implicit_multiplication_application, parse_expr,
    standard_transformations)


def parse(s: str):
    s = s.strip().rstrip(".,")
    try:
        from sympy.parsing.latex import parse_latex
        return parse_latex(s)
    except Exception:
        tf = standard_transformations + (implicit_multiplication_application, convert_xor)
        if "=" in s:
            lhs, rhs = s.split("=", 1)
            return Eq(parse_expr(lhs, transformations=tf), parse_expr(rhs, transformations=tf), evaluate=False)
        return parse_expr(s, transformations=tf)


def as_zero_form(e):
    return e.lhs - e.rhs if isinstance(e, Eq) else e


def check(old, new, sub=None):
    if isinstance(old, Eq) != isinstance(new, Eq):
        return "unverified", None  # an equation vs a plain expression: never guess a verdict
    a, b = as_zero_form(old), as_zero_form(new)
    if sub:
        a = a.subs(sub)
    is_eq = isinstance(old, Eq)
    if is_eq:
        if simplify(b) == 0:
            return ("verified", None) if simplify(a) == 0 else ("unverified", None)
        ratio = simplify(a / b)
        if ratio.is_constant() and ratio != 0:
            return "verified", None
    elif simplify(a - b) == 0:
        return "verified", None
    # Counterexample search: only meaningful for expression rewrites
    # (for equations, the solution sets can differ legitimately).
    if not is_eq:
        syms = sorted(a.free_symbols | b.free_symbols, key=str)
        for combo in itertools.product((1, 2, 3, 0, -1, -2, -3), repeat=len(syms)):
            env = dict(zip(syms, combo))
            try:
                d = simplify(a.subs(env) - b.subs(env))
            except ZeroDivisionError:
                continue
            if d.is_finite and d != 0:
                return "refuted", env
    return "unverified", None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--sub", help='substitution like "p=2k"')
    args = ap.parse_args()
    sub = None
    if args.sub:
        k, v = args.sub.split("=", 1)
        sub = {sympify(k): parse(v)}
    verdict, ce = check(parse(args.old), parse(args.new), sub)
    print(verdict, ce or "")
