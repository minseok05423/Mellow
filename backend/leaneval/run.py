"""L0 Lean experiment: can a model prove intro-proof problems in Lean 4 + Mathlib, and state them faithfully?

    .venv/bin/python -m leaneval.run [--models claude-sonnet-5-5,deepseek-v4-pro] [--only id,id] [--attempts 4]

Two separate measurements per problem and model:
- prove: given our hand-written statement, write a proof Lean accepts (compiler errors go back to the model);
- formalize: given the problem in words, write a statement that typechecks; saved for review against ours.
Writes leaneval/results/<timestamp>/{report.md,results.json}. Every model call goes through structured_call
(cached, validated, logged), with the Lean check as its semantic check.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from pydantic import BaseModel, Field

from app import config, lean, llm, store
from app.leancheck import FORMALIZE_SYSTEM, LeanStatement, _id
from bakeoff.run import PER_MTOK

HERE = Path(__file__).resolve().parent
USER = "lean-eval"


def summarize(out: Path, lines: list[str]) -> None:
    """Write the run's report.md and append its headline (all before the first ## section) to results/SUMMARY.md."""
    (out / "report.md").write_text("\n".join(lines) + "\n")
    head = next((i for i, x in enumerate(lines) if x.startswith("## ")), len(lines))
    with open(HERE / "results" / "SUMMARY.md", "a") as f:
        f.write(f"\n## {out.name}\n\n" + "\n".join("##" + x if x.startswith("# ") else x for x in lines[:head]).strip()
                + f"\n\nFull report: {out.name}/report.md\n")
    print(f"\nwrote {out / 'report.md'}, summary appended to {HERE / 'results' / 'SUMMARY.md'}")

PROVE_SYSTEM = """You write Lean 4 proofs with Mathlib (a recent version). You are given a theorem's statement;
write the tactic proof that comes after `:= by`.
Return JSON with:
- proof: the tactic lines only (no `theorem`, no `:= by`), one tactic per line, indented consistently.
- helpers: optional extra lemmas with complete proofs, placed before the theorem; usually "".
Never use sorry, admit, axioms, native_decide, #eval, or search tactics like exact?, apply?, simp?.
Prefer robust tactics: intro, obtain, use, constructor, induction, simp, ring, omega, linarith, nlinarith, positivity,
norm_num, field_simp, gcongr, and named Mathlib lemmas. If the checker reports errors, fix them."""


class LeanProof(BaseModel):
    proof: str = Field(min_length=1, max_length=8000)
    helpers: str = Field("", max_length=8000)


def prove(p: dict, model: str, attempts: int) -> dict:
    """Up to `attempts` proofs: each structured_call makes two (its own retry), errors carried into the next call."""
    t0, history, last = time.time(), [], {}

    def check(out: LeanProof) -> list[str]:
        r = lean.check_proof(p["lean"], out.proof, out.helpers)
        last.update(proof=out.proof, helpers=out.helpers, **r)
        return [f"Lean rejected the proof:\n" + "\n".join(r["errors"])[:3000]] if r["errors"] else []

    for round_ in range(max(1, attempts // 2)):
        text = f"Theorem statement (after the name):\n{p['lean']}\n\nThe problem in words: {p['text']}"
        if history:
            text += "\n\nYour previous proof failed:\n" + history[-1]
        try:
            out = llm.structured_call(endpoint="lean_prove", user=USER, cost_class="reason", model=model, system=PROVE_SYSTEM,
                                content=[{"type": "text", "text": text}], schema=LeanProof, semantic_check=check,
                                cache_input={"id": p["id"], "lean": p["lean"], "history": history, "prompt": _id(PROVE_SYSTEM)},
                                effort="high", max_tokens=16384)
            # From the call's result, not `last`: on a cache hit the check doesn't run again.
            return {"ok": True, "rounds": round_ + 1, "proof": out.proof, "helpers": out.helpers,
                    "seconds": round(time.time() - t0)}
        except llm.LLMError:
            history.append(f"{last.get('helpers', '')}\n{last.get('proof', '')}\n\nLean said:\n" + "\n".join(last.get("errors", []))[:3000])
    return {"ok": False, "rounds": max(1, attempts // 2), "proof": last.get("proof"), "errors": last.get("errors", []),
            "seconds": round(time.time() - t0)}


def formalize(p: dict, model: str) -> dict:
    last = {}

    def check(out: LeanStatement) -> list[str]:
        r = lean.check_statement(out.statement)
        last.update(statement=out.statement, **r)
        return [f"Lean rejected the statement:\n" + "\n".join(r["errors"])[:3000]] if r["errors"] else []

    try:
        out = llm.structured_call(endpoint="lean_formalize", user=USER, cost_class="reason", model=model, system=FORMALIZE_SYSTEM,
                            content=[{"type": "text", "text": p["text"]}], schema=LeanStatement, semantic_check=check,
                            cache_input={"id": p["id"], "text": p["text"], "prompt": _id(FORMALIZE_SYSTEM)},
                            effort="high", max_tokens=8192)
        return {"ok": True, "statement": out.statement}
    except llm.LLMError:
        return {"ok": False, "statement": last.get("statement"), "errors": last.get("errors", [])}


def cost(model: str, since: float) -> float | None:
    if model not in PER_MTOK:
        return None
    with store.connect() as c:
        i, o = c.execute("SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) FROM request_log "
                         "WHERE user = ? AND model = ? AND ts >= ?", (USER, model, since)).fetchone()
    pin, pout = PER_MTOK[model]
    return round((i * pin + o * pout) / 1e6, 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="claude-sonnet-5-5")  # DeepSeek V4 Pro proved 1 of 4 at up to 10 min each
    ap.add_argument("--only", default="")
    ap.add_argument("--attempts", type=int, default=4)
    args = ap.parse_args()
    config.CAPS["reason"] = 10_000  # an experiment, not a student: the daily cap doesn't apply
    problems = json.loads((HERE / "problems.json").read_text())
    if args.only:
        problems = [p for p in problems if p["id"] in args.only.split(",")]
    models = [m for m in args.models.split(",") if config.llm_configured(m)]

    # Our reference statements must typecheck, or the experiment measures our mistakes.
    bad = {p["id"]: r["errors"] for p in problems if not (r := lean.check_statement(p["lean"]))["ok"]}
    if bad:
        raise SystemExit(f"reference statements don't typecheck: {json.dumps(bad, indent=1)[:3000]}")

    results, since = [], time.time()
    for model in models:
        for p in problems:
            r = {"id": p["id"], "topic": p["topic"], "model": model,
                 "prove": prove(p, model, args.attempts), "formalize": formalize(p, model)}
            results.append(r)
            print(f"{model:20} {p['id']:18} prove={'ok' if r['prove']['ok'] else 'FAIL'} "
                  f"({r['prove']['rounds']} rounds, {r['prove']['seconds']}s)  formalize={'ok' if r['formalize']['ok'] else 'FAIL'}",
                  flush=True)

    out = HERE / "results" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    (out / "results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))
    lines = ["# Lean L0 results", "", "| model | proved | typechecking statements | median time to proof | cost (USD) |",
             "|---|---|---|---|---|"]
    for m in models:
        rs = [r for r in results if r["model"] == m]
        times = sorted(r["prove"]["seconds"] for r in rs if r["prove"]["ok"])
        lines.append(f"| {m} | {sum(r['prove']['ok'] for r in rs)}/{len(rs)} | {sum(r['formalize']['ok'] for r in rs)}/{len(rs)} "
                     f"| {times[len(times) // 2] if times else '-'}s | {cost(m, since)} |")
    lines += ["", "## Per problem", "", "| problem | topic | " + " | ".join(models) + " |", "|---|---|" + "---|" * len(models)]
    for p in problems:
        cells = [("✓" if r["prove"]["ok"] else "✗") + f" r{r['prove']['rounds']}" for m in models
                 for r in results if r["model"] == m and r["id"] == p["id"]]
        lines.append(f"| {p['id']} | {p['topic']} | " + " | ".join(cells) + " |")
    lines += ["", "## Statements to review (model's formalization vs ours)", ""]
    for r in results:
        p = next(q for q in problems if q["id"] == r["id"])
        lines += [f"- **{r['id']}** ({r['model']}, {'typechecks' if r['formalize']['ok'] else 'does not typecheck'})",
                  f"  - ours: `{p['lean']}`", f"  - model: `{r['formalize'].get('statement')}`"]
    summarize(out, lines)


if __name__ == "__main__":
    main()
