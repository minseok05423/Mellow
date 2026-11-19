"""L1g experiment: the graph check (app/graphcheck.py, DECISIONS #32) on the L0b and L0d proofs.

    MELLOW_LEAN_DIR=/tmp/mellow-lean .venv/bin/python -m leaneval.graph [--cases steps.json,subtle.json] [--only id,id]
        [--agent-queue DIR]

For each case (a correct proof and one with a planted mistake) the proof goes through the app's own pipeline: a cheap
model splits it into steps and a strong one links them (DECISIONS #35), the strong model revises the graph where the
student would confirm or fix it (`revise`, no student here), then each step is checked on its own: rules, SymPy at
random values, Lean with the fixed automation, three unanimous judges.
Measured: correct proofs with every step ✓ and complete (each ✗ or ? on a correct step is a false alarm); planted
mistakes caught (a step from the planted line, or from a case's accept_lines, is ✗ or ?), and with which tier; false ✓ (every step from the planted line
✓); where the ✓s come from (Proved / Tested / Judged).
With --agent-queue every model call is answered by Claude Code agents (leaneval/agentq.py): run a lane per schema title
(Chunks, Links, Graph, Form; each on the model the request names) and the three judge lanes. Cases on the same problem run one after another so identical judge requests are never in flight
together (which would mix up the judges' lanes); different problems run in parallel.
Writes leaneval/results/graph-<timestamp>/{report.md,results.json}.
"""
from __future__ import annotations

import argparse
import json
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app import config, graphcheck, llm
from leaneval.run import HERE, USER, summarize


REVISE_SYSTEM = """You stand in for the student who wrote this proof, at the step where they confirm how the checker
read it. The checker turned their lines into a graph: numbered nodes (the problem's givens, then each claim, assumption
or choice), each with the earlier nodes it rests on (premises), the variables in the order introduced, the goal and its
quantifier order, the blocks the steps are grouped in (a subproof, a proof by contradiction, cases, an induction, a
chain), and whether the proof reaches the goal. You know what you meant, and you wrote exactly these lines: return the
graph corrected so it records the reasoning on the page, mistakes included.
Fix: a line split or merged wrongly, a node's text that doesn't say what the line says, a wrong kind, a premise the
student's words point to (or the step plainly uses) that is missing, a premise they don't point to, a variable
introduced at the wrong node or with the wrong dependencies, a step in the wrong block, a block of the wrong kind, a
wrong goal, quantifier order or reaches_goal. Keep everything that is right as it is; if you split or merge nodes or
blocks, renumber 0, 1, 2, ... and update every reference. Never make the proof better than it is: don't add a premise
just because the step would need it, don't fix a false claim, don't add steps the student didn't write. If the graph
is right, return it unchanged."""


def revise(problem: str, lines: list[str], g: graphcheck.Graph) -> graphcheck.Graph:
    """The student's confirm step, played by the strong model (testing only; in the app the student confirms)."""
    text = (f"Problem:\n{problem}\n\nYour proof, line by line:\n" + "\n".join(f"{i}: {s}" for i, s in enumerate(lines))
            + f"\n\nThe checker's graph:\n{g.model_dump_json(indent=1)}")
    return llm.structured_call(
        endpoint="revise", user=USER, cost_class="check", model=config.GRAPH_MODEL, system=REVISE_SYSTEM,
        content=[{"type": "text", "text": text}], schema=graphcheck.Graph,
        cache_input={"text": text, "prompt": graphcheck._id(REVISE_SYSTEM)},
        semantic_check=lambda x: graphcheck.graph_problems(x, len(lines)), max_tokens=8000)


def run_proof(problem: str, lines: list[str]) -> dict:
    t = time.time()
    try:
        made = graphcheck.make_graph(problem, lines, USER)
        g = revise(problem, lines, made)
        res = graphcheck.check(g, USER)
    except Exception as e:  # one broken proof shouldn't end the run
        return {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:], "seconds": int(time.time() - t)}
    line_of = {n.id: n.line for n in g.nodes}
    return {"graph": g.model_dump(), "revised": g != made, "made": made.model_dump(), **res,
            "lines": [line_of[v["node"]] for v in res["nodes"]], "seconds": int(time.time() - t)}


def on_line(r: dict, line: int) -> list[dict]:
    return [v for v, ln in zip(r.get("nodes", []), r.get("lines", [])) if ln == line]


def run_case(c: dict, problems: dict) -> dict:
    problem = c.get("problem_text") or problems[c.get("problem", c["id"])]["text"]  # held-out cases bring their own
    r = {"id": c["id"], "kind": c.get("kind", "planted"), "wrong_line": c["wrong_line"], "mistake": c["mistake"],
         "correct": run_proof(problem, c["correct"]), "wrong": run_proof(problem, c["wrong"])}
    # accept_lines: where a grader could fairly place the mistake (the line that reuses a name, or the one that uses it)
    planted = [v for ln in c.get("accept_lines", [c["wrong_line"]]) for v in on_line(r["wrong"], ln)]
    r["caught"] = any(v["status"] != "ok" for v in planted)
    r["caught_wrong"] = any(v["status"] == "wrong" for v in planted)
    r["false_tick"] = bool(planted) and all(v["status"] == "ok" for v in planted)
    r["false_alarms"] = [v for v in r["correct"].get("nodes", []) if v["status"] != "ok"]
    show = lambda x: x.get("error") or " ".join(f"{v['node']}:{v['status']}/{v['tier'][0]}" for v in x["nodes"])
    print(f"{c['id']:20} correct: {show(r['correct'])} {'COMPLETE' if r['correct'].get('complete') else ''}\n"
          f"{'':20} wrong (line {c['wrong_line']}): {show(r['wrong'])}  -> "
          f"{'caught ✗' if r['caught_wrong'] else 'caught ?' if r['caught'] else 'FALSE ✓' if r['false_tick'] else 'no step'}",
          flush=True)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="steps.json,subtle.json")
    ap.add_argument("--only", default="")
    ap.add_argument("--parallel", type=int, default=4, help="problems checked at once")
    ap.add_argument("--agent-queue", default="", help="answer model calls with Claude Code agents (leaneval/agentq.py)")
    args = ap.parse_args()
    config.CAPS["check"] = 100_000  # an experiment, not a student: the daily cap doesn't apply
    problems = {p["id"]: p for p in json.loads((HERE / "problems.json").read_text())}
    cases = [c for f in args.cases.split(",") for c in json.loads((HERE / f).read_text())]
    if args.only:
        cases = [c for c in cases if c["id"] in args.only.split(",")]
    model = config.CHECK_MODEL
    if args.agent_queue:
        from leaneval.agentq import QueueProvider, serialize_lean
        llm.set_provider(QueueProvider(Path(args.agent_queue)))
        serialize_lean(HERE / "results" / "lean.lock")
        model = f"agent-{model}"
    elif not config.llm_configured(config.CHECK_MODEL):
        raise SystemExit(f"no key for {config.CHECK_MODEL}; use --agent-queue")

    groups = defaultdict(list)  # same problem -> one after another
    for c in cases:
        groups[c.get("problem", c["id"])].append(c)
    since = time.time()
    with ThreadPoolExecutor(args.parallel) as ex:
        results = [r for rs in ex.map(lambda cs: [run_case(c, problems) for c in cs], groups.values()) for r in rs]
    order = {c["id"]: i for i, c in enumerate(cases)}
    results.sort(key=lambda r: order[r["id"]])

    out = HERE / "results" / (time.strftime("graph-%Y%m%d-%H%M%S")
                              + (f"-{Path(args.agent_queue).name}" if args.agent_queue else ""))
    out.mkdir(parents=True)
    (out / "results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))

    lines = [f"# L1g results: the graph check ({model}, {time.strftime('%Y-%m-%d')}, {int(time.time() - since)} s)", "",
             "| cases | correct proofs complete | false alarms on correct steps (✗ / ?) | planted mistakes caught "
             "(✗ / ? only) | false ✓ | errors | median s per proof |", "|---|---|---|---|---|---|---|"]
    for name, rs in [(k, [r for r in results if r["kind"] == k]) for k in sorted({r["kind"] for r in results})]:
        alarms = [v for r in rs for v in r["false_alarms"]]
        secs = sorted(x["seconds"] for r in rs for x in (r["correct"], r["wrong"]))
        lines.append(f"| {name} ({len(rs)}) | {sum(bool(r['correct'].get('complete')) for r in rs)}/{len(rs)} "
                     f"| {sum(v['status'] == 'wrong' for v in alarms)} / {sum(v['status'] == 'unsure' for v in alarms)} "
                     f"| {sum(r['caught'] for r in rs)}/{len(rs)} ({sum(r['caught_wrong'] for r in rs)} / "
                     f"{sum(r['caught'] and not r['caught_wrong'] for r in rs)}) | {sum(r['false_tick'] for r in rs)} "
                     f"| {sum('error' in r[k] for r in rs for k in ('correct', 'wrong'))} | {secs[len(secs) // 2]} |")
    tiers = Counter(f"{v['status']} {v['tier']}" for r in results for k in ("correct", "wrong") for v in r[k].get("nodes", []))
    timeouts = sum(v["detail"].endswith("(Lean timed out)") for r in results for k in ("correct", "wrong")
                   for v in r[k].get("nodes", []))
    proofs = [r[k] for r in results for k in ("correct", "wrong") if "error" not in r[k]]
    lines += ["", f"Graphs the reviser (standing in for the student) changed: {sum(x['revised'] for x in proofs)} of "
              f"{len(proofs)}.",
              "Verdicts by tier, all proofs: " + ", ".join(f"{k}: {n}" for k, n in sorted(tiers.items())),
              f"Steps sent to the judges because Lean timed out: {timeouts}.",
              "", "A planted mistake counts as caught when a step from the planted line is ✗ or ?; a false ✓ when every "
              "step from it is ✓. \"kind\" planted = steps.json (L0b), subtle = subtle.json (L0d).", "", "## Per proof", ""]
    for r in results:
        lines += [f"### {r['id']} ({r['kind']}): {r['mistake']}"]
        for k in ("correct", "wrong"):
            x = r[k]
            lines.append(f"- **{k}**" + (f" (planted on line {r['wrong_line']})" if k == "wrong" else "")
                         + (" (graph revised)" if x.get("revised") else "") + f": {x.get('message', x.get('error'))}")
            graph = {n["id"]: n for n in x.get("graph", {}).get("nodes", [])}
            for v in x.get("nodes", []):
                n = graph[v["node"]]
                lines.append(f"  - [{v['node']}] line {n['line']} {n['kind']} from {n['premises']}: {n['text']} → "
                             f"**{v['status']} / {v['tier']}** {v['message']} ({v['detail'][:200]})")
        lines.append("")
    summarize(out, lines)
    if args.agent_queue:
        (Path(args.agent_queue) / "DONE").touch()


if __name__ == "__main__":
    main()
