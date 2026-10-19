"""Recognizer bake-off (SPEC §7, DECISIONS #12): replay recorded handwriting to every candidate.

    .venv/bin/python -m bakeoff.run mellow-samples-YYYY-MM-DD.json [--candidates mathpix,myscript,llm:claude-haiku-5-5]

Stroke recognizers get the raw strokes; vision LLMs get the rendered PNG (no recognizer guess, no
context, so each is judged on its own). Writes bakeoff/results/<timestamp>/{report.md,results.csv}.
Candidates whose keys aren't configured are skipped and listed in the report.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import time
from collections import defaultdict
from pathlib import Path

from app import config, llm, recognize
from app.recognize import RecognizeRequest, Stroke

DEFAULT_CANDIDATES = ["mathpix", "myscript", "llm:deepseek-flash",
                      "llm:claude-haiku-5-5", "llm:claude-sonnet-5-5", "llm:claude-opus-5-5"]

# USD. Vendor prices are list prices to confirm with the vendors (SPEC §11); None = unknown.
PER_REQUEST = {"mathpix": 0.005, "myscript": None}
# DeepSeek: peak-hour cache-miss rates (off-peak is half), so cost estimates err high.
PER_MTOK = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0),
            "deepseek-flash": (0.30, 1.20), "deepseek-v4-pro": (1.32, 3.96)}


def normalize(latex: str) -> str:
    """Compare transcriptions modulo formatting: spacing, \\left/\\right, braces around one token, \\cdot."""
    s = re.sub(r"\\(left|right)\b|\\[,;:! ]|\s+", "", latex)
    s = s.replace(r"\cdot", "*").replace(r"\times", "*").replace(r"\dfrac", r"\frac")
    s = re.sub(r"([\^_])\{(\w)\}", r"\1\2", s)
    s = re.sub(r"\\text\{([^}]*)\}", lambda m: m.group(1).lower(), s)
    return s


def equivalent(a: str, b: str) -> bool:
    """Same math even if written differently (e.g. 2q^2=p^2 vs p^2=2q^2). Prose/unparseable -> False."""
    try:
        from sympy import Eq, simplify
        from sympy.parsing.latex import parse_latex

        x, y = parse_latex(a), parse_latex(b)
        if isinstance(x, Eq) != isinstance(y, Eq):
            return False
        if isinstance(x, Eq):
            dx, dy = x.lhs - x.rhs, y.lhs - y.rhs
            return simplify(dx - dy) == 0 or simplify(dx + dy) == 0
        return simplify(x - y) == 0
    except Exception:
        return False


class CandidateUnavailable(Exception):
    pass


def run_candidate(name: str, sample: dict) -> tuple[str, float | None, float | None]:
    """Returns (latex, confidence, cost_usd)."""
    strokes = [Stroke(**s) for s in sample["strokes"]]
    if name in recognize.RECOGNIZERS:
        keys = {"mathpix": (config.MATHPIX_APP_ID, config.MATHPIX_APP_KEY),
                "myscript": (config.MYSCRIPT_APP_KEY, config.MYSCRIPT_HMAC_KEY)}[name]
        if not all(keys):
            raise CandidateUnavailable(f"{name}: keys not set in backend/.env")
        r = recognize.RECOGNIZERS[name](strokes)
        return r.latex, r.confidence, PER_REQUEST[name]
    if name.startswith("llm:"):
        model = name[4:]
        if not config.llm_configured(model):
            raise CandidateUnavailable(f"{name}: no API key for {model}")
        before = _token_totals()
        r = recognize.read_with_llm(RecognizeRequest(strokes=strokes, image=sample["image"]), "bakeoff", None, model)
        tin, tout = (a - b for a, b in zip(_token_totals(), before))
        pin, pout = PER_MTOK.get(model, (None, None))
        cost = (tin * pin + tout * pout) / 1e6 if pin is not None else None
        return r.latex, r.confidence, cost
    raise ValueError(f"unknown candidate {name}")


def _token_totals() -> tuple[int, int]:
    from app import store

    with store.connect() as c:
        row = c.execute("SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) "
                        "FROM request_log WHERE user = 'bakeoff' AND cached = 0").fetchone()
    return row[0], row[1]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("samples")
    ap.add_argument("--candidates", default=",".join(DEFAULT_CANDIDATES))
    args = ap.parse_args(argv)
    samples = json.loads(Path(args.samples).read_text())["samples"]
    config.CAPS["read_llm"] = config.CAPS["stroke"] = 10**6  # the bake-off isn't a student
    out_dir = Path(__file__).parent / "results" / time.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True)

    rows, skipped = [], {}
    for cand in args.candidates.split(","):
        for s in samples:
            t = time.time()
            try:
                latex, conf, cost = run_candidate(cand, s)
                err = ""
            except CandidateUnavailable as e:
                skipped[cand] = str(e)
                break
            except Exception as e:  # one bad sample shouldn't stop the run
                latex, conf, cost, err = "", None, None, f"{type(e).__name__}: {e}"
            rows.append({
                "candidate": cand, "sample": s["id"], "kind": s["kind"], "truth": s["truth"], "read": latex,
                "exact": normalize(latex) == normalize(s["truth"]),
                "equivalent": normalize(latex) == normalize(s["truth"]) or equivalent(latex, s["truth"]),
                "confidence": conf, "latency_ms": int((time.time() - t) * 1000), "cost_usd": cost, "error": err,
            })
            print(f"{cand:24} {s['kind']:6} {'OK ' if rows[-1]['exact'] else 'MISS'} {s['truth']!r} -> {latex!r}")

    with open(out_dir / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["candidate"])
        w.writeheader()
        w.writerows(rows)
    (out_dir / "report.md").write_text(report(rows, skipped, len(samples)))
    print(f"\nReport: {out_dir / 'report.md'}")


def report(rows: list[dict], skipped: dict, n: int) -> str:
    by = defaultdict(list)
    for r in rows:
        by[r["candidate"]].append(r)
    kinds = ["math", "mixed", "messy"]
    lines = [f"# Recognizer bake-off ({n} samples)", "",
             "Exact = same transcription modulo formatting. Equiv = same math (SymPy), e.g. sides swapped.", "",
             "| Candidate | " + " | ".join(f"{k} exact" for k in kinds) + " | All exact | All equiv | p50 latency | Cost/line | Errors |",
             "|---|" + "---|" * (len(kinds) + 5)]
    for cand, rs in by.items():
        def pct(sel, key):
            sel = [r for r in rs if sel(r)]
            return f"{100 * sum(r[key] for r in sel) / len(sel):.0f}% ({len(sel)})" if sel else "–"
        costs = [r["cost_usd"] for r in rs if r["cost_usd"] is not None]
        lines.append(
            f"| {cand} | " + " | ".join(pct(lambda r, k=k: r["kind"] == k, "exact") for k in kinds)
            + f" | {pct(lambda r: True, 'exact')} | {pct(lambda r: True, 'equivalent')}"
            + f" | {statistics.median(r['latency_ms'] for r in rs)} ms"
            + f" | {'$%.5f' % statistics.mean(costs) if costs else 'unknown'}"
            + f" | {sum(bool(r['error']) for r in rs)} |")
    if skipped:
        lines += ["", "Skipped:"] + [f"- {v}" for v in skipped.values()]
    misses = [r for r in rows if not r["exact"]]
    if misses:
        lines += ["", "## Misses", "", "| Candidate | Kind | Truth | Read |", "|---|---|---|---|"]
        lines += [f"| {r['candidate']} | {r['kind']} | `{r['truth']}` | `{r['read'] or r['error']}` |" for r in misses]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
