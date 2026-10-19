"""Animated explanations (SPEC §4 board objects): a checked proof becomes scene JSON (scene-json skill).

The scene is built by code from the proof SymPy checked, not by another LLM call, so the board only shows
claims the checker has seen, it costs nothing, and layout is ours (the LLM never picks positions).
- a claim is written on the next row; a claim that rewrites the previous one (same left side) transforms
  it in place, so an equation visibly evolves;
- the step's words and why it holds are the caption, with "✓ checked by SymPy" on checked steps;
- steps with nothing to draw join the next caption; a closing one highlights the last line;
- when the rows run out, the board fades and starts again at the top.
"""
import re

ROWS = 7
X, TOP, ROW = 0.05, 0.07, 0.125  # normalized: row r sits at y = TOP + r * ROW
MAX_INLINE = 70  # longest inline math (from a step's words) worth writing on the board
RELATION = re.compile(r"[=<>]|\\(le|ge|leq|geq|ne|neq|in|notin|subset|subseteq|to|mid|equiv|approx)\b")


def _caption(step) -> str:
    why = f"Why: {step.why}" if step.why else ""
    if step.checked:
        why += " ✓ checked by SymPy"
    return "\n".join(p for p in (step.text, why.strip()) if p)


def _drawable(step) -> str:
    """The LaTeX a step puts on the board: its claim, else the math in its words if short."""
    if step.claim.strip():
        return step.claim.strip()
    text = re.sub(r"\\[()\[\]]", "$", step.text)
    # Only math that states something ("\sqrt2 = a/b", "c \in [0,1]"), not a lone "$n$" in a sentence.
    math = [m.strip() for m in text.split("$")[1::2] if RELATION.search(m)]
    joined = r" \quad ".join(math)
    return joined if 0 < len(joined) <= MAX_INLINE else ""


def _lhs(latex: str) -> str | None:
    return latex.split("=", 1)[0].replace(" ", "") if latex.count("=") == 1 else None


def proof_scene(proof) -> dict | None:
    """Scene JSON for a proof, or None if it has nothing to draw."""
    steps: list[dict] = []
    rows: list[str] = []  # object ids on the board, top to bottom
    last: tuple[str, str] | None = None  # (object id, latex) drawn by the previous proof step
    pending: list[str] = []  # captions of steps with nothing to draw
    for n, s in enumerate(proof.steps, 1):
        latex = _drawable(s)
        if not latex:
            if _caption(s):
                pending.append(_caption(s))
            last = None
            continue
        sid, say = f"s{len(steps) + 1}", "\n\n".join(pending + [_caption(s)])
        pending = []
        if last and last[0] in rows and _lhs(latex) and _lhs(latex) == _lhs(last[1]):
            steps.append({"id": sid, "op": "transform", "from": last[0], "latex": latex, "say": say})
            rows[rows.index(last[0])] = sid
        else:
            if len(rows) == ROWS:  # board full: clear it (no captions, so these play straight through)
                for target in rows:
                    steps.append({"id": f"s{len(steps) + 1}", "op": "fade", "target": target})
                rows = []
                sid = f"s{len(steps) + 1}"
            steps.append({"id": sid, "op": "write", "latex": latex, "pos": [X, round(TOP + len(rows) * ROW, 3)], "say": say})
            rows.append(sid)
        last = (sid, latex)
    if not steps:
        return None
    if pending:  # the conclusion in words: point at the last line while it's read
        steps.append({"id": f"s{len(steps) + 1}", "op": "highlight", "target": rows[-1], "say": "\n\n".join(pending)})
    return {"schema_version": 1, "steps": steps}
