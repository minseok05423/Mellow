"""/structure: label a page's lines with roles and group them into described segments (SPEC §4, DECISIONS #21).

The labels let an action find its context by lookup (the problem above a proof step) instead of guessing
from "the lines above". A cheap model does it; the result is cached by the page's text.
"""
import hashlib
from typing import Literal

from pydantic import BaseModel, Field

from . import llm

Role = Literal["problem", "definition", "claim", "proof_step", "algebra", "concept", "note"]


class StructureRequest(BaseModel):
    lines: list[str] = Field(min_length=1, max_length=80)  # the page's readings, top to bottom
    reader_model: str | None = None  # the student's cheap model (Models menu); the route validates it


class LineLabel(BaseModel):
    role: Role
    segment: int


class Structure(BaseModel):
    lines: list[LineLabel]
    segments: list[str]  # one short description per segment, in order


SYSTEM = """You label a page of a student's handwritten math notebook (a first-year proofs course).
You get its lines top to bottom, as LaTeX read from handwriting (it may contain reading mistakes).

For each line, in order, give:
- role, one of:
  - problem: a statement to prove or a question to answer ("Show that...", "Prove...", "Let f... Show...").
    Every line of a problem written over several lines is role problem.
  - definition: defines a term or notation.
  - claim: a statement the student sets out to prove inside a proof (a lemma, "Claim: ...").
  - proof_step: a step of reasoning in words.
  - algebra: a calculation line.
  - concept: a concept name or topic the student wants to study ("uniform continuity").
  - note: anything else.
- segment: lines that belong together share a segment number; a problem and the lines working on it are
  one segment. Number segments 0, 1, 2, ... in order; a line's segment is never lower than the line above's.

Also give segments: one description per segment, under 80 characters, e.g. "Problem: prove sqrt 2 is irrational"."""

PROMPT_ID = hashlib.sha256(SYSTEM.encode()).hexdigest()[:12]


def _checker(n: int):
    def check(s: Structure) -> list[str]:
        if len(s.lines) != n:
            return [f"give exactly {n} line labels, one per input line, in order (got {len(s.lines)})"]
        segs = [label.segment for label in s.lines]
        if segs[0] != 0 or any(b not in (a, a + 1) for a, b in zip(segs, segs[1:])):
            return ["segments must start at 0 and go up by at most 1 from one line to the next"]
        if len(s.segments) != segs[-1] + 1:
            return [f"give exactly {segs[-1] + 1} segment descriptions"]
        if any(not d.strip() or len(d) > 120 for d in s.segments):
            return ["each segment description must be 1-120 characters"]
        return []
    return check


def label(req: StructureRequest, user: str, model: str) -> Structure:
    text = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(req.lines))
    return llm.structured_call(
        endpoint="structure", user=user, cost_class="structure", model=model, system=SYSTEM,
        content=[{"type": "text", "text": f"Lines, top to bottom:\n{text}"}], schema=Structure,
        semantic_check=_checker(len(req.lines)), cache_input={"lines": req.lines, "prompt": PROMPT_ID},
        effort="low", max_tokens=4096,
    )
