"""L0b Lean experiment: does line-by-line checking pass correct student proofs and catch planted mistakes?

    LLM_TIMEOUT_S=120 MELLOW_LEAN_DIR=/tmp/mellow-lean .venv/bin/python -m leaneval.steps [--models claude-sonnet-5-5] [--only id,id]

(LLM_TIMEOUT_S: Claude at high effort can think silently for longer than the default 20 s between streamed chunks.)

For each proof in steps.json (a correct one and one with a planted mistake), app/leancheck.py checks it the way the
notebook does:
1. The model *translates* the student's lines into Lean: structural tactics (intro, obtain, induction, use, ...) and the
   claims each line makes, plus the hypotheses a line cites as its reason. It writes no proofs.
2. Lean checks every claim with our fixed automation (AUTO), from the lines before it. One run checks the whole proof: a
   failed step is reported and Lean carries on, and a marker after each item shows Lean really got there.
3. If Lean rejects the *translation* (a tactic, a claim's syntax, closing the goal), the model retries with those errors
   only. It never hears which claims failed, so it can't learn to "fix" the student's mistakes.
4. A claim AUTO can't prove gets one more chance: the model writes a proof of that claim alone, which Lean checks. A
   false claim has no proof, so this can't add a false ✓; the line is marked "skipped step" (true, but a step is missing).
5. A line that cites a reason ("since g is injective") must also follow with the statement's other hypotheses cleared,
   or it is marked "wrong reason".
Measured: lines of correct proofs proved (each miss is a false "?"), planted mistakes caught (each miss is a false ✓),
and seconds. Writes leaneval/results/steps-<timestamp>/{report.md,results.json}.
"""
from __future__ import annotations

import argparse
import json
import time

from app import config
from app.leancheck import check as _check
from leaneval.run import HERE, USER, cost


def check(statement: str, lines: list[str], model: str) -> dict:
    return _check(statement, lines, model, USER)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="steps.json", help="steps.json (L0b) or subtle.json (L0d)")
    ap.add_argument("--models", default="claude-sonnet-5-5")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    config.CAPS["lean"] = 10_000  # an experiment, not a student: the daily cap doesn't apply
    statements = {p["id"]: p["lean"] for p in json.loads((HERE / "problems.json").read_text())}
    cases = json.loads((HERE / args.cases).read_text())
    if args.only:
        cases = [c for c in cases if c["id"] in args.only.split(",")]
    models = [m for m in args.models.split(",") if config.llm_configured(m)]

    results, since = [], time.time()
    for model in models:
        for c in cases:
            statement = statements[c.get("problem", c["id"])]
            r = {"id": c["id"], "model": model, "wrong_line": c["wrong_line"], "mistake": c["mistake"],
                 "correct": check(statement, c["correct"], model), "wrong": check(statement, c["wrong"], model)}
            r["caught"] = r["wrong"]["lines"][c["wrong_line"]] in ("not proved", "wrong reason", "false")
            r["false_tick"] = r["wrong"]["lines"][c["wrong_line"]] in ("proved", "skipped step")
            results.append(r)
            ok = r["correct"]["lines"]
            print(f"{model:20} {c['id']:18} correct: {sum(v in ('proved', 'skipped step') for v in ok)}/{len(ok)} ✓ {ok}"
                  f"{' (unfinished)' if r['correct'].get('unfinished') else ''}  mistake "
                  f"{'caught' if r['caught'] else 'FALSE ✓' if r['false_tick'] else 'unchecked'}: {r['wrong']['lines']}",
                  flush=True)

    out = HERE / "results" / time.strftime("steps-%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    (out / "results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))

    def median(xs):
        xs = sorted(xs)
        return xs[len(xs) // 2] if xs else "-"

    lines = ["# Lean L0b results: line-by-line checking", "",
             "| model | correct lines ✓ (skipped step) | automation only (after retries) | correct proofs fully ✓ "
             "| false alarms on correct lines | planted mistakes caught (with a counterexample) | false ✓ "
             "| median s per proof | cost (USD) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for m in models:
        rs = [r for r in results if r["model"] == m]
        ls = [v for r in rs for v in r["correct"]["lines"]]
        first = [v for r in rs for v in r["correct"].get("first_pass", r["correct"]["lines"])]
        good = ("proved", "skipped step")
        full = sum(all(v in good for v in r["correct"]["lines"]) and not r["correct"].get("unfinished") for r in rs)
        lines.append(f"| {m} | {sum(v in good for v in ls)}/{len(ls)} ({ls.count('skipped step')}) "
                     f"| {first.count('proved')}/{len(first)} | {full}/{len(rs)} | {ls.count('wrong reason') + ls.count('false')} "
                     f"| {sum(r['caught'] for r in rs)}/{len(rs)} "
                     f"({sum(r['wrong']['lines'][r['wrong_line']] == 'false' for r in rs)}) | {sum(r['false_tick'] for r in rs)} "
                     f"| {median(x['seconds'] for r in rs for x in (r['correct'], r['wrong']))} | {cost(m, since)} |")
    lines += ["", "\"unchecked\" means Lean never saw the line (a parse error, a translation that ended the proof, or no "
              "translation), so a planted mistake that is unchecked is neither caught nor a false ✓. \"skipped step\": "
              "the automation couldn't prove the line, the model proved it as a single claim and Lean checked that proof.",
              "", "## Per proof", ""]
    for r in results:
        lines += [f"### {r['id']} ({r['model']})", f"- correct: {r['correct']['lines']}"
                  f"{' (unfinished)' if r['correct'].get('unfinished') else ''}"
                  f" (retries {r['correct'].get('retries')}, model proofs {r['correct'].get('model_proofs')})",
                  f"- with mistake on line {r['wrong_line']} ({r['mistake']}): {r['wrong']['lines']}"
                  f" (retries {r['wrong'].get('retries')}, model proofs {r['wrong'].get('model_proofs')})", ""]
        for kind in ["correct", "wrong"]:
            if r[kind].get("lean"):
                lines += [f"<details><summary>{kind}: Lean and errors</summary>", "", "```lean", r[kind]["lean"], "```", "",
                          "```", json.dumps({"errors": r[kind]["errors"], "other": r[kind]["other_errors"]},
                                            indent=1, ensure_ascii=False)[:4000], "```", "</details>", ""]
            else:
                lines += [f"- {kind}: translation failed", ""]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    print(f"\nwrote {out / 'report.md'}")


if __name__ == "__main__":
    main()
