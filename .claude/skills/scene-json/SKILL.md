---
name: scene-json
description: Schema and validation rules for Mellow's blackboard scene JSON (schema_version, steps, the ops write/fade/transform/highlight, normalized pos; v2 adds a figure with draw/move for geometric pictures). Use this whenever you write, edit, validate, or render a scene; touch the /scene, /explain or picture (geometry.py) code, their Pydantic models or system prompts; add scene fixtures; build the frontend scene renderer; or consider adding a new op or field — even if the user just says "the √2 proof", "a proof scene", or "the steps JSON".
---

# Scene JSON

A scene is a list of steps the board plays one at a time (advance on click).
The same JSON drives the browser renderer today and possibly a native renderer
later, so keep it renderer-agnostic: describe *what* appears, never pixels or
timing curves.

## Top level

```json
{"schema_version": 1, "steps": [ ... ]}
```

- `schema_version` (int, required). Currently `1`. Bump it on any breaking
  change; it is part of the LLM cache key, so a bump invalidates cached scenes.
- `steps` (array, required, non-empty). Order = playback order.

## Step fields

Every step has:
- `id` (string, required, unique within the scene). Convention: `s1`, `s2`, …
- `op` (required): one of `write`, `fade`, `transform`, `highlight`.
- `say` (string, optional): narration shown as a caption while the step plays.

Per-op fields:

| op          | required fields          | optional | meaning |
|-------------|--------------------------|----------|---------|
| `write`     | `latex`, `pos`           | `say`    | Draw new LaTeX at `pos`. Creates an object named by this step's `id`. |
| `fade`      | `target`                 | `say`    | Fade out the object created by step `target`. |
| `transform` | `from`, `latex`          | `pos`, `say` | Morph object `from` into new LaTeX (in place unless `pos` given). The result is addressable by this step's `id`; `from` is consumed. |
| `highlight` | `target`                 | `part`, `say` | Emphasize object `target`; `part` is a LaTeX substring to highlight, else the whole thing. |

`pos` is `[x, y]` normalized to `[0, 1]`, origin top-left, measured to the
object's top-left corner. Normalized so the scene works at any canvas size.

## Example (√2 is irrational, opening)

```json
{"schema_version":1,"steps":[
 {"id":"s1","op":"write","latex":"\\sqrt2=\\frac{p}{q},\\ \\gcd(p,q)=1","pos":[0.1,0.1]},
 {"id":"s2","op":"write","latex":"2q^2=p^2","pos":[0.1,0.25],"say":"Square both sides"},
 {"id":"s3","op":"highlight","target":"s2","part":"p^2","say":"So p² is even, hence p is even"},
 {"id":"s4","op":"transform","from":"s2","latex":"2q^2=4k^2","say":"Write p = 2k"}
]}
```

## Validation rules

Reject a scene (never render it) if any of these fail. On the backend this is
the Pydantic model plus a semantic pass; on LLM output, a failure triggers one
retry with the error message appended, then a clean error.

1. **Shape**: `schema_version` is a supported int; `steps` non-empty; each step
   has exactly the fields its op allows (extra keys are errors — they usually
   mean the LLM invented a feature).
2. **Unique ids**: no two steps share an `id`.
3. **pos normalized**: both coordinates are numbers in `[0, 1]`.
4. **References resolve backwards**: every `target` / `from` names the `id` of an
   *earlier* step. Forward references and self-references are errors.
5. **Referenced object is alive**: the referenced object must still be on the
   board at that point — not already faded, and not already consumed as the
   `from` of a transform. (Highlighting `s2` after `s4` transformed it is an
   error; highlight `s4` instead.)
6. **highlight.part** (if given) is a substring of the target's current LaTeX.
   Without this, highlighting silently does nothing.
7. **latex non-empty** for `write` and `transform`. (Parseability is checked by
   KaTeX in the browser; the backend may additionally try a KaTeX render.)
8. **Only the 4 ops.** Don't add a fifth op, or a new field, without first
   writing a DECISIONS.md entry explaining why the existing ops can't express
   it. A new op touches the schema, the prompts, the validator, every renderer,
   and the cache key, so it should be a deliberate choice.

Error messages should name the step id and the rule, e.g.
`step s3: target "s9" does not reference an earlier step`, because they are fed
back to the LLM on retry and it needs to know exactly what to fix.

## Reference validator

`scripts/validate_scene.py <file.json>` implements the rules above with the
standard library only. Use it to check fixtures or hand-written scenes, and as
the reference when writing the Pydantic version in `backend/`:

```bash
python .claude/skills/scene-json/scripts/validate_scene.py path/to/scene.json
```

Exit code 0 = valid; otherwise it prints one error per line.

## Version 2: figures (DECISIONS #24)

`schema_version: 2` adds a `figure` and two ops for geometric explanations. v1 scenes still play.

```json
{"schema_version": 2,
 "figure": {"points": {"A": [0, 0], "B": [3, 0]}, "view": [-0.4, -0.4, 3.4, 3.4], "box": [0.02, 0.04, 0.56, 0.92]},
 "steps": [{"id": "s1", "op": "draw", "shape": {"kind": "segment", "points": ["A", "B"], "label": "a+b"}, "say": "..."},
           {"id": "s2", "op": "move", "target": "s1", "to": ["B", "A"]}]}
```

- `figure.points`: name -> `[x, y]` in math coordinates (y up). `view`: the region shown,
  `[xmin, ymin, xmax, ymax]`. `box`: where on the stage, normalized `[x, y, w, h]`. The renderer
  maps view into box keeping the aspect ratio.
- `draw` {id, op, shape, say?}: shape `{kind, points, label?, pts?}`, kind one of point (1 point),
  segment (2), polygon (3+), circle (centre, a point on it), angle (3, the vertex in the middle;
  a right angle draws as a little square), curve (`pts`: sampled `[x, y]` list; `points` may be empty).
- `move` {id, op, target, to, say?}: a polygon or segment slides/turns to the points `to` (same
  count, same order). The result is addressable by this step's id; `target` is consumed.
- `highlight` and `fade` work on shapes too (`part` only applies to equations).

Scenes like this are compiled by `backend/app/geometry.py` from the model's figure after SymPy
checks it; the model never writes v2 scene JSON directly. The reference validator checks v2 too.

## Streaming

`/scene` streams JSON Lines: first line is `{"schema_version":1}`, then one step
object per line. Validate each step as it arrives (rules 1–7 only need earlier
steps), and stop rendering at the first invalid step.
