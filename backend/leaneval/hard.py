"""L2 experiment: very hard proofs, the graph check (L1g) against one chatbot call (L0c), on proofs graded by experts.

    MELLOW_LEAN_DIR=/tmp/mellow-lean .venv/bin/python -m leaneval.hard --arm pipeline [--agent-queue DIR]
    .venv/bin/python -m leaneval.hard --arm chat --models claude-opus-5-5 [--agent-queue DIR]

The cases (gradingbench.json) come from Google DeepMind's IMO-GradingBench (CC-BY-4.0,
github.com/google-deepmind/superhuman/tree/main/imobench): four IMO-hard problems (PB-Advanced), each with the shortest
model-written proof that human experts graded 7/7 (correct) and the shortest graded 0/7 (incorrect), one proof line
per non-empty line of the response. Unlike steps.json and subtle.json nobody planted the mistakes or marked where they
are: only the experts' verdict on the whole proof is known. Measured per checker: correct proofs accepted, incorrect
proofs rejected (an incorrect proof accepted is a false ✓). The pipeline accepts a proof when the graph check calls it
complete (every step ✓ and the goal reached); a chatbot when it answers "correct" to alone.py's question, in one call.
With --agent-queue, give each chatbot model its own queue so every request reaches an agent on the model it names.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app import config, llm
from leaneval.alone import ASK, grade
from leaneval.graph import run_proof
from leaneval.run import HERE, summarize


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["pipeline", "chat"], required=True)
    ap.add_argument("--cases", default="gradingbench.json")
    ap.add_argument("--models", default="claude-opus-5-5", help="chat arm: the chatbots, one call per proof each")
    ap.add_argument("--parallel", type=int, default=8)
    ap.add_argument("--agent-queue", default="", help="answer model calls with Claude Code agents (leaneval/agentq.py)")
    args = ap.parse_args()
    config.CAPS["check"] = config.CAPS["reason"] = 100_000  # an experiment, not a student
    cases = json.loads((HERE / args.cases).read_text())
    if args.agent_queue:
        from leaneval.agentq import QueueProvider, serialize_lean
        llm.set_provider(QueueProvider(Path(args.agent_queue)))
        serialize_lean(HERE / "results" / "lean.lock")
    checkers = ["graph check"] if args.arm == "pipeline" else args.models.split(",")

    def one(c: dict, who: str) -> dict:
        t = time.time()
        if who == "graph check":
            r = run_proof(c["problem_text"], c["proof"])
            bad = [v for v in r.get("nodes", []) if v["status"] != "ok"]
            return {"id": c["id"], "label": c["label"], "checker": who, "error": r.get("error"),
                    "accepted": bool(r.get("complete")), "seconds": r["seconds"],
                    "why": r.get("message", "") + "".join(f"\n  [{v['node']}] {v['status']}/{v['tier']}: {v['message']}"
                                                         for v in bad[:5]), "full": r}
        g = grade(c["problem_text"], c["proof"], who, 0, ASK["neutral"])
        return {"id": c["id"], "label": c["label"], "checker": who, "error": None if g else "no answer",
                "accepted": bool(g) and g.verdict == "correct", "seconds": int(time.time() - t),
                "why": g and f"line {g.first_wrong_line}: {g.reason}", "full": g and g.model_dump()}

    since = time.time()
    with ThreadPoolExecutor(args.parallel) as ex:
        results = list(ex.map(lambda cw: one(*cw), [(c, w) for w in checkers for c in cases]))

    out = HERE / "results" / time.strftime(f"hard-{args.arm}-%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    (out / "results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))
    via = " (answered by Claude Code agents)" if args.agent_queue else ""
    lines = [f"# L2: very hard proofs (IMO-GradingBench), {args.arm} arm{via}", "",
             f"{len(cases)} proofs on 4 IMO-hard problems, graded by experts: {sum(c['label'] == 'correct' for c in cases)}"
             f" correct (7/7), {sum(c['label'] == 'incorrect' for c in cases)} incorrect (0/7). {int(time.time() - since)} s.", "",
             "| checker | correct proofs accepted | incorrect proofs rejected | false ✓ (incorrect accepted) | errors "
             "| median s per proof |", "|---|---|---|---|---|---|"]
    for who in checkers:
        rs = [r for r in results if r["checker"] == who and not r["error"]]
        ok, wrong = [r for r in rs if r["label"] == "correct"], [r for r in rs if r["label"] == "incorrect"]
        secs = sorted(r["seconds"] for r in rs) or [0]
        lines.append(f"| {who}{via and ' (agent)'} | {sum(r['accepted'] for r in ok)}/{len(ok)} "
                     f"| {sum(not r['accepted'] for r in wrong)}/{len(wrong)} | {sum(r['accepted'] for r in wrong)} "
                     f"| {sum(1 for r in results if r['checker'] == who and r['error'])} | {secs[len(secs) // 2]} |")
    lines += ["", "## Per proof", ""]
    for r in results:
        verdict = r["error"] or ("accepted" if r["accepted"] else "rejected")
        lines.append(f"- **{r['id']}** ({r['label']}), {r['checker']}: **{verdict}**. {r['why'] or ''}")
    summarize(out, lines)
    if args.agent_queue:
        (Path(args.agent_queue) / "DONE").touch()


if __name__ == "__main__":
    main()
