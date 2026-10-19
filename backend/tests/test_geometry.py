"""Explaining with a picture: SymPy checks every claim and move in the model's figure, then code lays it out."""
import copy
import importlib.util
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, llm
from app.assist import PICTURE_SYSTEM, Proof, ProofStep
from app.geometry import Picture, analyse, picture_check
from app.main import app

_spec = importlib.util.spec_from_file_location(
    "validate_scene", Path(__file__).resolve().parents[2] / ".claude/skills/scene-json/scripts/validate_scene.py")
reference = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reference)

# The worked example the prompt gives the model: (a+b)^2 as a square cut into pieces.
SQUARE = json.loads(re.search(r'Example, for .*?:\n(\{.*\})', PICTURE_SYSTEM, re.S).group(1))


def pic(**changes):
    p = copy.deepcopy(SQUARE)
    p.update(changes)
    return p


def test_the_prompts_own_example_passes_and_compiles_to_a_valid_scene():
    r = analyse(SQUARE)
    assert r["errors"] == [] and reference.validate(r["scene"]) == []
    scene = r["scene"]
    assert scene["schema_version"] == 2 and scene["figure"]["points"]["C"] == [3.0, 3.0]  # a=2, b=1
    v = scene["figure"]["view"]
    assert v[0] < 0 < 3 < v[2]  # the whole square fits, with a margin
    write = next(s for s in scene["steps"] if s["op"] == "write")
    assert write["pos"][0] > 0.5  # equations go in the column beside the figure, placed by code
    says = {s["id"]: s.get("say") for s in scene["steps"]}
    assert all(says[i].endswith("\n✓ checked by SymPy") for i in ("s1", "s2", "s3", "s4", "s6"))
    assert says["s5"] is None  # no caption: it plays straight on (its claim was still checked)


def test_a_claim_true_only_at_the_drawn_values_says_so():
    p = pic(steps=[{"id": "s1", "op": "draw", "shape": "segment", "points": ["A", "B"], "say": "This side is 3 long.",
                    "claims": [{"left": {"length": ["A", "B"]}, "right": {"value": "3"}}]},
                   {"id": "s2", "op": "draw", "shape": "angle", "points": ["B", "A", "D"], "say": "A right angle.",
                    "claims": [{"left": {"angle": ["B", "A", "D"]}, "right": {"value": "90"}}]}])
    steps = analyse(p)["scene"]["steps"]
    assert steps[0]["say"].endswith("✓ true in this picture (checked by SymPy)")  # |AB| = a+b, which is 3 only here
    assert steps[1]["say"].endswith("✓ checked by SymPy")  # a right angle for every a, b


def test_a_length_equal_to_a_difference_is_only_true_in_the_picture():
    # |PB| = b holds for all a, b; |AP| = a - b holds only while a >= b, so it can't claim "all values".
    p = pic(steps=[{"id": "s1", "op": "draw", "shape": "segment", "points": ["P", "B"], "say": "b long.",
                    "claims": [{"left": {"length": ["P", "B"]}, "right": {"value": "b"}}]},
                   {"id": "s2", "op": "draw", "shape": "segment", "points": ["S", "A"], "say": "a - b?",
                    "claims": [{"left": {"length": ["S", "M"]}, "right": {"value": "2a-b-b"}}]}])
    steps = analyse(p)["scene"]["steps"]
    assert steps[0]["say"].endswith("✓ checked by SymPy")
    assert steps[1]["say"].endswith("✓ true in this picture (checked by SymPy)")


def test_a_false_claim_goes_back_with_the_numbers():
    p = pic()
    p["steps"][1]["claims"][0]["right"]["value"] = "a^2+b"
    errors = analyse(p)["errors"]
    assert len(errors) == 1 and errors[0].startswith("step s2: the claim area(APMS) = $a^2+b$ is false")
    assert "= 4 but $a^2+b$ = 5" in errors[0]


def test_pieces_only_move_they_never_stretch():
    tri = {"params": [{"name": "a", "value": 2}, {"name": "b", "value": 1}],
           "points": [{"name": n, "x": x, "y": y} for n, x, y in
                      [("A", "0", "0"), ("B", "a", "0"), ("C", "0", "b"), ("B'", "0", "a"), ("C'", "-b", "0"), ("D", "2a", "0")]],
           "steps": [{"id": "s1", "op": "draw", "shape": "polygon", "points": ["A", "B", "C"], "say": "A right triangle."},
                     {"id": "s2", "op": "move", "target": "s1", "points": ["A", "B'", "C'"], "say": "Turn it a quarter turn."}]}
    assert analyse(tri)["errors"] == []  # a rotation keeps every side
    bad = copy.deepcopy(tri)
    bad["steps"][1]["points"] = ["A", "D", "C"]
    assert analyse(bad)["errors"] == ["step s2: pieces only move, but AB would change length (2 to 4)"]


def test_a_sheared_piece_is_not_a_move():
    # Review finding: a square "moved" to a rhombus with the same four sides changed shape.
    sq = {"params": [], "points": [{"name": n, "x": x, "y": y} for n, x, y in
          [("A", "0", "0"), ("B", "1", "0"), ("C", "1", "1"), ("D", "0", "1"),
           ("P", "3", "0"), ("Q", "4", "0"), ("R", "\\frac{9}{2}", "\\frac{\\sqrt{3}}{2}"), ("S", "\\frac{7}{2}", "\\frac{\\sqrt{3}}{2}")]],
          "steps": [{"id": "s1", "op": "draw", "shape": "polygon", "points": ["A", "B", "C", "D"], "say": "A square."},
                    {"id": "s2", "op": "move", "target": "s1", "points": ["P", "Q", "R", "S"], "say": "Moved?"}]}
    assert analyse(sq)["errors"] == ["step s2: pieces only move, but AC would change length (1.414 to 1.732)"]


def test_equations_keep_their_rows_when_one_fades():
    # Review finding: write, write, write, fade the first, write -> the new one landed on the third.
    w = lambda i: {"id": f"s{i}", "op": "write", "latex": f"x_{i}=1", "say": "."}
    p = pic(steps=[SQUARE["steps"][0], w(2), w(3), w(4), {"id": "s5", "op": "fade", "target": "s2"}, w(6)])
    pos = {s["id"]: s["pos"] for s in analyse(p)["scene"]["steps"] if s["op"] == "write"}
    assert pos["s6"] == pos["s2"] and len({tuple(v) for v in pos.values()}) == 3


def test_tiny_figures_still_catch_false_claims_and_angles_stay_in_range():
    p = pic(params=[{"name": "a", "value": 0.001}, {"name": "b", "value": 0.001}])
    p["steps"][1]["claims"][0]["right"]["value"] = "4a^2"  # area(APMS) is a^2
    assert any("is false" in e for e in analyse(p)["errors"])
    q = pic(steps=[{"id": "s1", "op": "draw", "shape": "angle", "points": ["P", "M", "S"], "say": ".",
                    "claims": [{"left": {"angle": ["P", "M", "S"]}, "right": {"value": "270"}}]}])
    assert any("between 0 and 180" in e for e in analyse(q)["errors"])


def test_a_curve_through_a_pole_skips_it():
    p = {"params": [{"name": "a", "value": 2}], "points": [{"name": "O", "x": "0", "y": "0"}],
         "steps": [{"id": "s1", "op": "draw", "shape": "curve", "f": "\\frac{1}{x}", "domain": ["-a", "a"], "say": "."},
                   {"id": "s2", "op": "draw", "shape": "point", "points": ["O"], "say": "."}]}
    r = analyse(p)
    assert r["errors"] == [] and len(r["scene"]["steps"][0]["shape"]["pts"]) == 64  # x = 0 left out


def test_curves_are_sampled_by_the_server():
    p = {"params": [{"name": "a", "value": 2}], "points": [{"name": "O", "x": "0", "y": "0"}],
         "steps": [{"id": "s1", "op": "draw", "shape": "curve", "f": "x^2", "domain": ["0", "a"], "label": "y=x^2",
                    "say": "The parabola."},
                   {"id": "s2", "op": "draw", "shape": "point", "points": ["O"], "say": "The origin."}]}
    scene = analyse(p)["scene"]
    pts = scene["steps"][0]["shape"]["pts"]
    assert len(pts) == 65 and pts[0] == [0.0, 0.0] and pts[-1] == [2.0, 4.0] and reference.validate(scene) == []
    assert scene["figure"]["box"] == [0.04, 0.04, 0.92, 0.92]  # no equations: the figure gets the whole board


@pytest.mark.parametrize("change, message", [
    (lambda p: p["steps"][0].update(points=["A", "B", "Z"]), "unknown point Z"),
    (lambda p: p["points"][1].update(x="a+c"), "uses c, which isn't a param"),
    (lambda p: p["steps"][3].update(op="move", target="s9"), "move needs a polygon or segment still on the board"),
    (lambda p: p["steps"][0]["claims"][0]["right"].update(length=["A", "B"]), "exactly one of length, angle, area, value"),
])
def test_broken_figures_are_sent_back_naming_the_step(change, message):
    p = pic()
    change(p)
    assert any(message in e for e in analyse(p)["errors"])


def test_invented_fields_are_rejected_and_dollar_signs_stripped():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Picture.model_validate({**SQUARE, "colour": "red"})
    p = pic()
    p["steps"][1]["label"] = "$a^2$"
    p["steps"][5]["latex"] = r"\((a+b)^2 = a^2+2ab+b^2\)"
    assert picture_check(Picture.model_validate(p)) == []
    steps = analyse(p)["scene"]["steps"]
    assert steps[1]["shape"]["label"] == "a^2" and steps[5]["latex"] == "(a+b)^2 = a^2+2ab+b^2"


class Fake:
    name = "fake"

    def __init__(self, *outputs):
        self.outputs, self.calls = list(outputs), []

    def complete(self, *, model, system, messages, schema, effort, max_tokens, on_token=None):
        self.calls.append((schema.__name__, messages))
        out = self.outputs.pop(0)
        return llm.Completion(text=json.dumps(out), parsed=schema.model_validate(out))


def test_explain_as_a_picture_retries_a_false_figure_then_returns_the_scene(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    wrong = pic()
    wrong["steps"][0]["claims"][0]["right"]["value"] = "a^2+b^2"
    proof = Proof(steps=[ProofStep(text="Expand.", claim="(a+b)^2=a^2+2ab+b^2", algebra="identity", why="")])
    fake = Fake(proof.model_dump(), wrong, SQUARE)
    llm.set_provider(fake)
    d = TestClient(app).post("/assist", headers={"X-Mellow-User": "test-user-1"}, json={
        "action": "explain", "kind": "problem", "style": "picture", "confirmed": True,
        "selection": r"\text{Show } (a+b)^2 = a^2+2ab+b^2"}).json()
    assert [c[0] for c in fake.calls] == ["Proof", "Picture", "Picture"]
    assert "is false in your figure" in fake.calls[2][1][-1]["content"]  # the retry names the false claim
    assert d["verdict"] == "explain" and d["scene"]["schema_version"] == 2 and "1. A square" in d["message"]
