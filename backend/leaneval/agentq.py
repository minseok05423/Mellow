"""Answer the checker's model calls with Claude Code agents instead of the API (when API credit runs out).

The runner (`leaneval.steps --agent-queue DIR`) uses the real pipeline unchanged; only the provider differs: each
model call becomes DIR/<id>.request.json and waits for DIR/<id>.answer.json. An agent answers in a loop:

    .venv/bin/python -m leaneval.agentq next DIR      # waits for an unanswered request, prints it (or DONE)
    .venv/bin/python -m leaneval.agentq answer DIR ID FILE

Lean runs from all runners take turns (a file lock), so several queues can run at once without loading Mathlib in
parallel. An agent is a stand-in for the API model, not the same thing: results are labelled with the queue's model
name, and the agent must answer from the request alone (it sees exactly what the API model would).
"""
from __future__ import annotations

import fcntl
import itertools
import json
import sys
import threading
import time
from pathlib import Path

from pydantic import BaseModel, ValidationError

from app import lean
from app.llm import Completion

WAIT_S = 3 * 3600


class QueueProvider:
    name = "agent"

    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.ids = itertools.count(1)
        self.lock = threading.Lock()

    def complete(self, *, model, system, messages, schema: type[BaseModel], effort, max_tokens, on_token=None):
        with self.lock:
            n = next(self.ids)
        req, ans = self.folder / f"{n}.request.json", self.folder / f"{n}.answer.json"
        tmp = req.with_suffix(".tmp")
        tmp.write_text(json.dumps({"id": n, "system": system, "messages": messages,
                                   "schema": schema.model_json_schema()}, ensure_ascii=False, indent=1))
        tmp.rename(req)  # atomic: an agent never reads half a request
        t = time.time()
        while not ans.exists():
            if time.time() - t > WAIT_S:
                return Completion(text="no answer from the agent", parsed=None)
            time.sleep(1)
        time.sleep(0.2)  # let the writer finish
        text = ans.read_text()
        try:
            return Completion(text=text, parsed=schema.model_validate_json(text))
        except ValidationError as e:
            return Completion(text=f"{text}\n\n{e}", parsed=None)


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
                   if not (folder / p.name.replace(".request.", ".answer.")).exists()),
                  key=lambda p: int(p.name.split(".")[0]))


def main():
    cmd, folder = sys.argv[1], Path(sys.argv[2])
    if cmd == "next":
        t = time.time()
        while not (todo := pending(folder)):
            if (folder / "DONE").exists():
                print("DONE: the run is finished, no more requests.")
                return
            if time.time() - t > 900:
                print("WAITING: no request for 15 minutes; run `next` again.")
                return
            time.sleep(2)
        r = json.loads(todo[0].read_text())
        print(f"REQUEST {r['id']}  ({len(todo)} waiting)\n\n=== SYSTEM ===\n{r['system']}\n\n=== MESSAGES ===")
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
