"""Reference validator for Mellow scene JSON (schema_version 1, and 2 which adds a figure: draw and move).

Usage: python validate_scene.py scene.json
Exit 0 if valid, 1 otherwise (one error per line on stdout).
Stdlib only, so it doubles as a spec for the backend Pydantic model.
"""
import json
import sys

SUPPORTED_VERSIONS = {1, 2}
FIELDS = {
    "write": ({"id", "op", "latex", "pos"}, {"say"}),
    "fade": ({"id", "op", "target"}, {"say"}),
    "transform": ({"id", "op", "from", "latex"}, {"pos", "say"}),
    "highlight": ({"id", "op", "target"}, {"part", "say"}),
    "draw": ({"id", "op", "shape"}, {"say"}),  # v2
    "move": ({"id", "op", "target", "to"}, {"say"}),  # v2
}
SHAPE_POINTS = {"point": 1, "segment": 2, "circle": 2, "angle": 3}  # polygon: 3+; curve: any, plus pts
SHAPES = set(SHAPE_POINTS) | {"polygon", "curve"}


def _numbers(v, n):
    return isinstance(v, list) and len(v) == n and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)


def _figure_errors(scene) -> list[str]:
    fig = scene.get("figure")
    if not isinstance(fig, dict):
        return ["figure must be an object with points, view and box"]
    errors = []
    pts = fig.get("points")
    if not isinstance(pts, dict) or not all(isinstance(k, str) and _numbers(v, 2) for k, v in pts.items()):
        errors.append("figure.points must map point names to [x, y]")
    view = fig.get("view")
    if not (_numbers(view, 4) and view[0] < view[2] and view[1] < view[3]):
        errors.append("figure.view must be [xmin, ymin, xmax, ymax] with min < max")
    box = fig.get("box")
    if not (_numbers(box, 4) and all(0 <= v <= 1 for v in box)):
        errors.append("figure.box must be [x, y, w, h] with all in [0, 1]")
    return errors


def validate(scene) -> list[str]:
    errors = []
    if not isinstance(scene, dict):
        return ["scene must be an object"]
    if scene.get("schema_version") not in SUPPORTED_VERSIONS:
        errors.append(f"schema_version must be one of {sorted(SUPPORTED_VERSIONS)}")
    steps = scene.get("steps")
    if not isinstance(steps, list) or not steps:
        return errors + ["steps must be a non-empty array"]
    version = scene.get("schema_version")
    if version == 2 or "figure" in scene:
        errors += _figure_errors(scene)
    names = set((scene.get("figure") or {}).get("points") or {}) if isinstance(scene.get("figure"), dict) else set()

    alive: dict[str, str] = {}  # object id -> current latex (equations)
    shapes: dict[str, tuple] = {}  # object id -> (kind, number of points) (v2 figure shapes)
    seen: set[str] = set()
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"step #{i}: must be an object")
            continue
        sid = step.get("id")
        where = f"step {sid}" if isinstance(sid, str) else f"step #{i}"
        op = step.get("op")
        if op not in FIELDS or (op in ("draw", "move") and version != 2):
            errors.append(f"{where}: op {op!r} is not allowed in schema_version {version}")
            continue
        required, optional = FIELDS[op]
        keys = set(step)
        for k in sorted(required - keys):
            errors.append(f"{where}: missing field {k!r} for op {op}")
        for k in sorted(keys - required - optional):
            errors.append(f"{where}: field {k!r} not allowed for op {op}")
        if not isinstance(sid, str) or not sid:
            errors.append(f"{where}: id must be a non-empty string")
            continue
        if sid in seen:
            errors.append(f"{where}: duplicate id")
        seen.add(sid)

        if "pos" in step:
            pos = step["pos"]
            ok = (isinstance(pos, list) and len(pos) == 2
                  and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                          and 0 <= v <= 1 for v in pos))
            if not ok:
                errors.append(f"{where}: pos must be [x, y] with both in [0, 1]")
        if op in ("write", "transform"):
            if not isinstance(step.get("latex"), str) or not step["latex"].strip():
                errors.append(f"{where}: latex must be a non-empty string")

        if op == "draw":
            shape = step.get("shape")
            kind = shape.get("kind") if isinstance(shape, dict) else None
            spts = shape.get("points") if isinstance(shape, dict) else None
            if kind not in SHAPES:
                errors.append(f"{where}: shape kind must be one of {sorted(SHAPES)}")
            elif not isinstance(spts, list) or any(n not in names for n in spts):
                errors.append(f"{where}: shape points must be names in figure.points")
            elif (kind in SHAPE_POINTS and len(spts) != SHAPE_POINTS[kind]) or (kind == "polygon" and len(spts) < 3):
                errors.append(f"{where}: wrong number of points for a {kind}")
            elif kind == "curve" and not (isinstance(shape.get("pts"), list) and len(shape["pts"]) >= 2
                                          and all(_numbers(q, 2) for q in shape["pts"])):
                errors.append(f"{where}: a curve needs pts, a list of [x, y]")
            else:
                shapes[sid] = (kind, len(spts))
            continue
        if op == "move":
            target, to = step.get("target"), step.get("to")
            if target not in shapes or shapes[target][0] not in ("polygon", "segment"):
                errors.append(f"{where}: target {target!r} is not a polygon or segment still on the board")
            elif not isinstance(to, list) or len(to) != shapes[target][1] or any(n not in names for n in to):
                errors.append(f"{where}: to must name {shapes[target][1]} points in figure.points")
            else:
                shapes[sid] = shapes.pop(target)
            continue
        if op in ("fade", "highlight") and step.get("target") in shapes:  # figure shapes
            if "part" in step:
                errors.append(f"{where}: part only applies to equations")
            if op == "fade":
                shapes.pop(step["target"])
            continue

        ref_key = "from" if op == "transform" else "target" if op in ("fade", "highlight") else None
        if ref_key:
            ref = step.get(ref_key)
            if not isinstance(ref, str):
                errors.append(f"{where}: {ref_key} must be a step id string, got {ref!r}")
            elif ref not in seen - {sid}:
                errors.append(f"{where}: {ref_key} {ref!r} does not reference an earlier step")
            elif ref not in alive:
                errors.append(f"{where}: {ref_key} {ref!r} is no longer on the board (faded or transformed)")
            elif op == "highlight" and "part" in step and not isinstance(step["part"], str):
                errors.append(f"{where}: part must be a string, got {step['part']!r}")
            elif op == "highlight" and "part" in step and step["part"] not in alive[ref]:
                errors.append(f"{where}: part {step['part']!r} is not a substring of {ref}'s latex")

        # Update board state.
        if op == "write" and isinstance(step.get("latex"), str):
            alive[sid] = step["latex"]
        elif op == "transform" and isinstance(step.get("from"), str) and step["from"] in alive:
            del alive[step["from"]]
            alive[sid] = step.get("latex", "")
        elif op == "fade" and isinstance(step.get("target"), str):
            alive.pop(step["target"], None)
    return errors


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    with open(sys.argv[1]) as f:
        errs = validate(json.load(f))
    print("\n".join(errs) if errs else "valid")
    sys.exit(1 if errs else 0)
