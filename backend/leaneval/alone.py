"""L0c experiment: an LLM grading proofs alone (what students do with ChatGPT), on L0b's proofs.

    LLM_TIMEOUT_S=120 .venv/bin/python -m leaneval.alone [--models claude-sonnet-5-5,claude-haiku-5-5] [--runs 3]

Each proof in steps.json (a correct one and one with a planted mistake) goes to the model as text, with the problem,
the way a student would ask: "is my proof correct?". Measured per model, over several runs: wrong proofs passed (the
false ✓ Lean is there to prevent), correct proofs rejected, and wrong proofs flagged at the right line. Text, not a
photo, and a structured answer: both favour the model over a real ChatGPT chat. Compare with L0b (PROJECT.md §9).
"""
from __future__ import annotations

import argparse
import json
import time
from typing import Literal

from pydantic import BaseModel

from app import config, llm
from leaneval.run import HERE, USER, cost

SYSTEM = "You are a careful university math tutor. A student in an intro-to-proofs course shows you their proof."


class Grade(BaseModel):
    verdict: Literal["correct", "incorrect"]
    first_wrong_line: int | None  # 0-based; null when correct
    reason: str


def grade(problem: str, lines: list[str], model: str, run: int) -> Grade | None:
    content = (f"Problem: {problem}\n\nMy proof, line by line:\n" + "\n".join(f"{k}: {s}" for k, s in enumerate(lines)) +
               "\n\nIs my proof correct? If not, give the number of the first wrong line.")
    try:
        return llm.structured_call(endpoint="lean_alone", user=USER, cost_class="reason", model=model, system=SYSTEM,
                                   content=[{"type": "text", "text": content}], schema=Grade,
                                   cache_input={"content": content, "run": run, "prompt": SYSTEM}, effort="medium")
    except llm.LLMError as e:
        print(f"  {model} failed: {e}")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="steps.json", help="steps.json (L0b) or subtle.json (L0d)")
    ap.add_argument("--models", default="claude-sonnet-5-5,claude-haiku-5-5")
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()
    config.CAPS["reason"] = 10_000  # an experiment, not a student
    problems = {p["id"]: p["text"] for p in json.loads((HERE / "problems.json").read_text())}
    cases = json.loads((HERE / args.cases).read_text())
    models = [m for m in args.models.split(",") if config.llm_configured(m)]

    rows, since = [], time.time()
    for model in models:
        passed = rejected = at_line = n = 0
        for c in cases:
            problem = problems[c.get("problem", c["id"])]
            for run in range(args.runs):
                ok, bad = grade(problem, c["correct"], model, run), grade(problem, c["wrong"], model, run)
                if ok is None or bad is None:
                    continue
                n += 1
                rejected += ok.verdict == "incorrect"
                passed += bad.verdict == "correct"
                at_line += bad.verdict == "incorrect" and bad.first_wrong_line == c["wrong_line"]
                print(f"{model:20} {c['id']:18} run {run}: correct proof -> {ok.verdict}, wrong proof -> "
                      f"{bad.verdict} (line {bad.first_wrong_line}, planted {c['wrong_line']})", flush=True)
        rows.append(f"| {model} | {passed}/{n} | {rejected}/{n} | {at_line}/{n} | {cost(model, since)} |")

    out = HERE / "results" / time.strftime("alone-%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    report = ["# L0c: an LLM grading alone, on L0b's proofs", "",
              f"{len(cases)} problems, {args.runs} runs each; text and a structured answer (favours the model).", "",
              "| model | wrong proofs passed (false ✓) | correct proofs rejected | mistake found at the right line "
              "| cost (USD) |", "|---|---|---|---|---|", *rows]
    (out / "report.md").write_text("\n".join(report) + "\n")
    print("\n".join(report))


if __name__ == "__main__":
    main()
