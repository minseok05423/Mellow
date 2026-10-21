"""Answer the checker's model calls with Claude Code agents instead of the API (when API credit runs out).

The runner (`leaneval.steps --agent-queue DIR`) uses the real pipeline unchanged; only the provider differs: each
model call becomes DIR/<id>.request.json and waits for DIR/<id>.answer.json. An agent answers in a loop:

    .venv/bin/python -m leaneval.agentq next DIR [prep|K/N]   # claims an unanswered request, prints it (or DONE)
    .venv/bin/python -m leaneval.agentq answer DIR ID FILE

`next` claims what it prints (DIR/<id>.claimed), so several agents can share a queue. Lanes keep the graph check's
judges isolated: `prep` takes every call but the judges' (the graph and forms calls, which see the whole proof), and
K/N takes only judge calls whose `copy` is K mod N, so agents on 0/3, 1/3 and 2/3 answer one step's three identical
judge requests independently, never having read the proof. A claimed request whose agent stopped is freed by deleting its .claimed file.

Lean runs from all runners take turns (a file lock), so several queues can run at once without loading Mathlib in
parallel. An agent is a stand-in for the API model, not the same thing: results are labelled with the queue's model
name, and the agent must answer from the request alone (it sees exactly what the API model would).
"""
from __future__ import annotations

import fcntl
import json
import sys
import time
from pathlib import Path

from app import lean
from app.llm import QueueProvider  # noqa: F401 (the runners' import)


def serialize_lean(lock_path: Path) -> None:
    """Make every Lean run in this process wait its turn behind a lock shared by all runners."""
    run = lean._run

    def locked(src: str):
        with open(lock_path, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            return run(src)

    lean._run = locked


def pending(folder: Path) -> list[Path]:
    return sorted((p for p in folder.glob("*.request.json")
                   if not (folder / p.name.replace(".request.", ".answer.")).exists()
                   and not (folder / p.name.replace(".request.", ".claimed.")).exists()),
                  key=lambda p: int(p.name.split(".")[0]))


def claim(folder: Path, lane: str | None) -> tuple[dict, int] | None:
    """The first pending request on this lane, claimed atomically so no two agents answer it."""
    todo = pending(folder)
    for p in todo:
        r = json.loads(p.read_text())
        judge = r.get("schema", {}).get("title") == "Mark"
        if lane == "prep" and judge or lane and lane != "prep" and (
                not judge or r.get("copy", 0) % int(lane.split("/")[1]) != int(lane.split("/")[0])):
            continue
        try:
            (folder / p.name.replace(".request.", ".claimed.")).open("x").close()
            return r, len(todo)
        except FileExistsError:
            continue
    return None


def main():
    cmd, folder = sys.argv[1], Path(sys.argv[2])
    if cmd == "next":
        t = time.time()
        while not (got := claim(folder, sys.argv[3] if len(sys.argv) > 3 else None)):
            if (folder / "DONE").exists():
                print("DONE: the run is finished, no more requests.")
                return
            if time.time() - t > 900:
                print("WAITING: no request for 15 minutes; run `next` again.")
                return
            time.sleep(2)
        r, waiting = got
        print(f"REQUEST {r['id']}  ({waiting} waiting)\n\n=== SYSTEM ===\n{r['system']}\n\n=== MESSAGES ===")
        for m in r["messages"]:
            content = m["content"] if isinstance(m["content"], str) else "\n".join(
                b.get("text", "") for b in m["content"])
            print(f"--- {m['role']} ---\n{content}")
        print(f"\n=== ANSWER: JSON matching this schema ===\n{json.dumps(r['schema'], ensure_ascii=False)}")
    elif cmd == "answer":
        n, src = sys.argv[3], Path(sys.argv[4])
        text = src.read_text()
        json.loads(text)  # fail here, not in the runner, on broken JSON
        (folder / f"{n}.answer.json").write_text(text)
        print(f"answered {n}")


if __name__ == "__main__":
    main()
