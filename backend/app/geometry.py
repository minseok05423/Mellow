"""Explaining with a picture (DECISIONS #24): the reasoning model draws a proof; SymPy checks the drawing.

The model writes points (coordinates as LaTeX in a few parameters, so the picture holds for every value),
steps that draw / move / write / highlight / fade, and the claims each step's picture makes: equal lengths,
angles, areas, or an equation. Here, in a child process with a time limit:
- every claim is computed from the coordinates: false -> an error that goes back to the model (with the
  numbers); true for all parameter values -> "general"; true for the values drawn only -> "picture";
- a moved piece must keep its side lengths and area (pieces move, they never stretch);
- the result is compiled into scene JSON v2 for the board: numeric coordinates, the view, curve samples,
  and positions for equations, all chosen by code. Only the model's figure is cached; the scene is compiled
  on every request, so layout fixes reach old answers too.
"""
from __future__ import annotations

import multiprocessing as mp
import signal
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from . import verify  # noqa: F401  (the forkserver preloads it; _parse reads LaTeX)

TIMEOUT_S = 20.0
SYMBOLIC_S = 2.0  # per claim: past this, a claim that holds in the picture stays "picture"
SYMBOLIC_TOTAL_S = 12.0  # all claims together, inside TIMEOUT_S
TOL = 1e-9


# ---- what the model writes ----

class Param(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-wyz]$", description="one lowercase letter, not x")
    value: float = Field(gt=0, description="the value used to draw")


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[A-Z][A-Za-z0-9_']{0,3}$")
    x: str = Field(description="LaTeX in the params, e.g. a+b or \\frac{a}{2}")
    y: str


class Quantity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    length: list[str] | None = Field(None, description="two point names")
    angle: list[str] | None = Field(None, description="three point names, the vertex in the middle; in degrees")
    area: list[str] | None = Field(None, description="a polygon's point names, in order")
    value: str | None = Field(None, description="LaTeX in the params (degrees when compared with an angle)")


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    left: Quantity
    right: Quantity


class PicStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^s\d+$")
    op: Literal["draw", "move", "write", "transform", "highlight", "fade"]
    shape: Literal["point", "segment", "polygon", "circle", "angle", "curve"] | None = None
    points: list[str] = Field(default_factory=list, description="draw: the shape's points; move: the new points")
    label: str = Field("", description="draw: LaTeX label for a segment, polygon, circle or curve")
    f: str = Field("", description="curve: y = f(x), LaTeX in x and the params")
    domain: list[str] = Field(default_factory=list, description="curve: [from, to], LaTeX in the params")
    latex: str = Field("", description="write / transform: the equation")
    target: str = Field("", description="move / transform / highlight / fade: an earlier step's id")
    say: str = Field("", description="the caption, math as $...$")
    claims: list[Claim] = Field(default_factory=list)


class Picture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    params: list[Param] = Field(default_factory=list, max_length=6)
    points: list[Point] = Field(min_length=1, max_length=40)
    steps: list[PicStep] = Field(min_length=2, max_length=30)


# ---- checking and compiling (runs in a child process) ----

NEEDS = {"point": 1, "segment": 2, "circle": 2, "angle": 3}


def _tex(s: str) -> str:
    """Models wrap LaTeX in $...$ or \\(...\\) even when asked not to; the board wants it bare."""
    s = (s or "").strip()
    for a, b in (("$$", "$$"), ("$", "$"), ("\\(", "\\)"), ("\\[", "\\]")):
        if s.startswith(a) and s.endswith(b) and len(s) > len(a) + len(b):
            return s[len(a):-len(b)].strip()
    return s
FIGURE_BOX, FULL_BOX = [0.02, 0.04, 0.56, 0.92], [0.04, 0.04, 0.92, 0.92]  # normalized stage regions
TEXT_X, TEXT_TOP, TEXT_ROW, TEXT_ROWS = 0.62, 0.06, 0.115, 8


class _Timeout(Exception):
    pass


def _timed(fn, seconds: float):
    def boom(*_):
        raise _Timeout

    old = signal.signal(signal.SIGALRM, boom)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def _analyse(pic: dict) -> dict:
    from sympy import N, Symbol, acos, cos, nsimplify, pi, simplify, sqrt

    errors: list[str] = []
    deadline = time.monotonic() + SYMBOLIC_TOTAL_S
    params = {p["name"]: Symbol(p["name"], positive=True) for p in pic["params"]}
    values = {params[p["name"]]: nsimplify(p["value"]) for p in pic["params"]}
    x = Symbol("x")

    def expr(latex: str, *extra):
        e = verify._parse(latex)
        e = e.subs({Symbol(n): s for n, s in params.items()})  # the parser makes plain symbols
        unknown = e.free_symbols - set(params.values()) - set(extra)
        if unknown:
            raise ValueError(f"uses {', '.join(sorted(map(str, unknown)))}, which isn't a param")
        return e

    def num(e) -> float:
        v = complex(N(e.subs(values), 30))
        if abs(v.imag) > 1e-9 or v.real != v.real or abs(v.real) == float("inf"):
            raise ValueError("isn't a finite real number at the drawn values")
        return v.real

    P: dict[str, tuple] = {}
    for pt in pic["points"]:
        if pt["name"] in P:
            errors.append(f"point {pt['name']} is defined twice")
            continue
        try:
            P[pt["name"]] = (expr(pt["x"]), expr(pt["y"]))
            num(P[pt["name"]][0]), num(P[pt["name"]][1])
        except Exception as e:
            errors.append(f"point {pt['name']}: can't use its coordinates ({e})")
    if errors:
        return {"errors": errors}
    coords = {n: [round(num(px), 4), round(num(py), 4)] for n, (px, py) in P.items()}

    def pts(names, where):
        missing = [n for n in names if n not in P]
        if missing:
            raise ValueError(f"{where}: unknown point {missing[0]} (define it in points)")
        return [P[n] for n in names]

    def quantity(q: dict, where: str):
        kinds = [k for k in ("length", "angle", "area", "value") if q.get(k) is not None]
        if len(kinds) != 1:
            raise ValueError(f"{where}: a quantity needs exactly one of length, angle, area, value")
        k, v = kinds[0], q[kinds[0]]
        if k == "value":
            return "value", expr(v), f"${v}$"
        ps = pts(v, where)
        name = "".join(v)
        if k == "length":
            if len(ps) != 2:
                raise ValueError(f"{where}: a length takes two points")
            return "length", (ps[0][0] - ps[1][0]) ** 2 + (ps[0][1] - ps[1][1]) ** 2, f"|{name}|"  # squared
        if k == "angle":
            if len(ps) != 3:
                raise ValueError(f"{where}: an angle takes three points, the vertex in the middle")
            (ax, ay), (bx, by), (cx, cy) = ps
            u, w = (ax - bx, ay - by), (cx - bx, cy - by)
            return "angle", (u[0] * w[0] + u[1] * w[1]) / sqrt((u[0] ** 2 + u[1] ** 2) * (w[0] ** 2 + w[1] ** 2)), f"angle {name}"  # its cosine
        if len(ps) < 3:
            raise ValueError(f"{where}: an area takes three or more points")
        s = sum(ps[i][0] * ps[(i + 1) % len(ps)][1] - ps[(i + 1) % len(ps)][0] * ps[i][1] for i in range(len(ps))) / 2
        return "area", s ** 2, f"area({name})"  # squared

    def shown(kind, e) -> float:  # a quantity's actual value, for messages
        if kind == "angle":
            return num(acos(e) * 180 / pi)
        return num(sqrt(e)) if kind in ("length", "area") else num(e)

    def check(claim: dict, where: str) -> str:
        (kl, l, dl), (kr, r, dr) = quantity(claim["left"], where), quantity(claim["right"], where)
        a, b = l, r
        sign_known = True  # squaring a value: "general" only if the value can't be negative for any params
        if "angle" in (kl, kr):
            if {kl, kr} - {"angle", "value"}:
                raise ValueError(f"{where}: compare an angle with an angle or a number of degrees")
            for k, v, d in ((kl, l, dl), (kr, r, dr)):
                if k == "value" and not 0 <= num(v) <= 180:  # cosines would match 320 with 40
                    raise ValueError(f"{where}: an angle is between 0 and 180 degrees, but {d} isn't")
            a = cos(l * pi / 180) if kl == "value" else l
            b = cos(r * pi / 180) if kr == "value" else r
        elif {kl, kr} == {"length", "area"}:
            raise ValueError(f"{where}: compare a length with a length and an area with an area (or with a value)")
        elif kl != kr and "value" in (kl, kr):  # lengths and areas are compared squared
            v = l if kl == "value" else r
            if num(v) < 0:
                raise ValueError(f"{where}: {dl if kl == 'value' else dr} is negative")
            a, b = (l ** 2, r) if kl == "value" else (l, r ** 2)
            sign_known = bool(v.is_nonnegative)  # |AB| = a - b holds only when a >= b
        na, nb = num(a), num(b)
        if abs(na - nb) > TOL * max(abs(na), abs(nb)) + 1e-25:  # relative: tiny figures don't hide errors
            raise ValueError(f"{where}: the claim {dl} = {dr} is false in your figure "
                             f"({dl} = {shown(kl, l):.4g} but {dr} = {shown(kr, r):.4g}); fix the points or the claim")
        budget = min(SYMBOLIC_S, deadline - time.monotonic())  # one budget for all claims
        if not sign_known or budget < 0.05:
            return "picture"
        try:
            return "general" if _timed(lambda: simplify(a - b) == 0, budget) else "picture"
        except _Timeout:
            return "picture"

    scene_steps: list[dict] = []
    text: list[str] = []
    alive: dict[str, tuple] = {}  # step id -> ("tex", latex) | ("shape", kind, point names)
    slots: dict[str, int] = {}  # equation id -> its row in the side column (kept when rewritten, freed on fade)
    samples: list[list[float]] = []
    for st in pic["steps"]:
        sid, op, where = st["id"], st["op"], f"step {st['id']}"
        try:
            if sid in alive or any(s["id"] == sid for s in scene_steps):
                raise ValueError(f"{where}: duplicate id")
            out: dict = {"id": sid, "op": op}
            if op == "draw":
                kind, names = st.get("shape"), st.get("points") or []
                if kind is None:
                    raise ValueError(f"{where}: draw needs a shape")
                if kind == "polygon" and len(names) < 3:
                    raise ValueError(f"{where}: a polygon needs three or more points")
                if kind in NEEDS and len(names) != NEEDS[kind]:
                    raise ValueError(f"{where}: a {kind} takes {NEEDS[kind]} point(s)")
                pts(names, where)
                shape = {"kind": kind, "points": names}
                if _tex(st.get("label", "")):
                    shape["label"] = _tex(st["label"])
                if kind == "curve":
                    if not st.get("f") or len(st.get("domain") or []) != 2:
                        raise ValueError(f"{where}: a curve needs f and a domain [from, to]")
                    f = expr(st["f"], x)
                    lo, hi = num(expr(st["domain"][0])), num(expr(st["domain"][1]))
                    if not lo < hi:
                        raise ValueError(f"{where}: the domain must go from a smaller to a larger number")
                    shape["pts"] = []
                    for i in range(65):
                        xv = lo + (hi - lo) * i / 64
                        try:
                            shape["pts"].append([round(xv, 4), round(num(f.subs(x, xv)), 4)])
                        except (ValueError, TypeError, ZeroDivisionError):
                            continue  # undefined there (e.g. a pole)
                    if len(shape["pts"]) < 2:
                        raise ValueError(f"{where}: f isn't defined on the domain")
                    samples += shape["pts"]
                out["shape"] = shape
                alive[sid] = ("shape", kind, names)
            elif op == "move":
                t = alive.get(st.get("target", ""))
                if not t or t[0] != "shape" or t[1] not in ("polygon", "segment"):
                    raise ValueError(f"{where}: move needs a polygon or segment still on the board as its target")
                old, new = t[2], st.get("points") or []
                if len(new) != len(old):
                    raise ValueError(f"{where}: move needs {len(old)} new points, in the same order")
                o, n = pts(old, where), pts(new, where)
                # Pieces only move: every distance between two vertices stays the same (sides alone would let a
                # rhombus shear), so the piece is congruent to where it was.
                for i in range(len(o)):
                    for j in range(i + 1, len(o)):
                        lo_ = num((o[i][0] - o[j][0]) ** 2 + (o[i][1] - o[j][1]) ** 2)
                        ln = num((n[i][0] - n[j][0]) ** 2 + (n[i][1] - n[j][1]) ** 2)
                        if abs(lo_ - ln) > 1e-7 * max(lo_, ln):
                            raise ValueError(f"{where}: pieces only move, but {old[i]}{old[j]} would change length "
                                             f"({lo_ ** 0.5:.4g} to {ln ** 0.5:.4g})")
                del alive[st["target"]]
                out.update(target=st["target"], to=new)
                alive[sid] = ("shape", t[1], new)
            elif op in ("write", "transform"):
                if not _tex(st.get("latex", "")):
                    raise ValueError(f"{where}: {op} needs latex")
                out["latex"] = _tex(st["latex"])
                if op == "transform":
                    t = alive.get(st.get("target", ""))
                    if not t or t[0] != "tex":
                        raise ValueError(f"{where}: transform needs an equation still on the board as its target")
                    out["from"] = st["target"]
                    del alive[st["target"]]
                    slots[sid] = slots.pop(st["target"])
                else:
                    free = sorted(set(range(TEXT_ROWS)) - set(slots.values()))
                    if not free:  # side column full: clear it
                        for r in slots:
                            scene_steps.append({"id": f"{sid}_clear{len(scene_steps)}", "op": "fade", "target": r})
                            del alive[r]
                        slots, free = {}, [0]
                    slots[sid] = free[0]
                    out["pos"] = [TEXT_X, round(TEXT_TOP + free[0] * TEXT_ROW, 3)]
                alive[sid] = ("tex", out["latex"])
            else:  # highlight, fade
                if st.get("target", "") not in alive:
                    raise ValueError(f"{where}: {op} needs a target still on the board")
                out["target"] = st["target"]
                if op == "fade":
                    del alive[st["target"]]
                    slots.pop(st["target"], None)
            statuses = [check(c, where) for c in st.get("claims") or []]
            say = (st.get("say") or "").strip()
            if statuses and say:  # a step without a caption plays straight on; its claims are still checked
                say += ("\n✓ checked by SymPy" if all(s == "general" for s in statuses)
                        else "\n✓ true in this picture (checked by SymPy)")
            if say.strip():
                out["say"] = say.strip()
                text.append(say.strip())
            scene_steps.append(out)
        except ValueError as e:
            errors.append(str(e))
    if not errors and not any(s["op"] == "draw" for s in scene_steps):
        errors.append("draw at least one shape: this is a picture")
    if errors:
        return {"errors": errors}

    for s in scene_steps:  # a circle's whole disc must be in view, not just its centre and one point
        if s["op"] == "draw" and s["shape"]["kind"] == "circle":
            (cx, cy), (tx, ty) = coords[s["shape"]["points"][0]], coords[s["shape"]["points"][1]]
            r = ((tx - cx) ** 2 + (ty - cy) ** 2) ** 0.5
            samples += [[cx - r, cy - r], [cx + r, cy + r]]
    xs = [c[0] for c in coords.values()] + [p[0] for p in samples]
    ys = [c[1] for c in coords.values()] + [p[1] for p in samples]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-6)
    pad = 0.12 * span
    view = [round(min(xs) - pad, 4), round(min(ys) - pad, 4), round(max(xs) + pad, 4), round(max(ys) + pad, 4)]
    has_text = any(s["op"] == "write" for s in scene_steps)
    scene = {"schema_version": 2, "figure": {"points": coords, "view": view, "box": FIGURE_BOX if has_text else FULL_BOX},
             "steps": scene_steps}
    return {"errors": [], "scene": scene, "text": "\n\n".join(f"{i}. {t}" for i, t in enumerate(text, 1))}


def _worker(pic, conn):
    try:
        conn.send(_analyse(pic))
    except Exception as e:  # never let a SymPy failure pass a figure
        conn.send({"errors": [f"the figure couldn't be checked ({type(e).__name__}: {e})"]})


def analyse(pic: dict, timeout: float = TIMEOUT_S) -> dict:
    """Check a Picture (as a dict) and compile it: {"errors": [...]} or {"errors": [], "scene", "text"}."""
    ctx = mp.get_context("forkserver")  # same isolation as verify.check_step
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_worker, args=(pic, child), daemon=True)
    proc.start()
    child.close()  # so a child that dies (out of memory, crash) reads as EOF at once, not after the timeout
    try:
        if parent.poll(timeout):
            return parent.recv()
        return {"errors": ["checking the figure took too long: use simpler coordinates and fewer claims"]}
    except EOFError:
        return {"errors": ["the figure checker stopped unexpectedly: use simpler coordinates"]}
    finally:
        proc.kill()
        proc.join()


def picture_check(p: Picture) -> list[str]:
    """structured_call's semantic check: SymPy checks the figure (errors go back to the model)."""
    return analyse(p.model_dump())["errors"][:8]
