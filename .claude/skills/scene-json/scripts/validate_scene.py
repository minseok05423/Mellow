"""Reference validator for Mellow scene JSON (schema_version 1).

Usage: python validate_scene.py scene.json
Exit 0 if valid, 1 otherwise (one error per line on stdout).
Stdlib only, so it doubles as a spec for the backend Pydantic model.
"""
import json
import sys

SUPPORTED_VERSIONS = {1}
FIELDS = {
    "write": ({"id", "op", "latex", "pos"}, {"say"}),
    "fade": ({"id", "op", "target"}, {"say"}),
    "transform": ({"id", "op", "from", "latex"}, {"pos", "say"}),
    "highlight": ({"id", "op", "target"}, {"part", "say"}),
}


def validate(scene) -> list[str]:
    errors = []
    if not isinstance(scene, dict):
        return ["scene must be an object"]
    if scene.get("schema_version") not in SUPPORTED_VERSIONS:
        errors.append(f"schema_version must be one of {sorted(SUPPORTED_VERSIONS)}")
    steps = scene.get("steps")
    if not isinstance(steps, list) or not steps:
        return errors + ["steps must be a non-empty array"]

    alive: dict[str, str] = {}  # object id -> current latex
    seen: set[str] = set()
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"step #{i}: must be an object")
            continue
        sid = step.get("id")
        where = f"step {sid}" if isinstance(sid, str) else f"step #{i}"
        op = step.get("op")
        if op not in FIELDS:
            errors.append(f"{where}: op {op!r} is not one of {sorted(FIELDS)}")
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
