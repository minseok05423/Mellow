"""Checked proofs become valid scene JSON (scene-json skill): equations evolve in place, words become captions."""
import importlib.util
from pathlib import Path

from app.assist import Proof, ProofStep
from app.scene import ROWS, proof_scene

_spec = importlib.util.spec_from_file_location(
    "validate_scene", Path(__file__).resolve().parents[2] / ".claude/skills/scene-json/scripts/validate_scene.py")
reference = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reference)


def step(text, claim="", why="", checked=False):
    return ProofStep(text=text, claim=claim, why=why, checked=checked)


# Shaped like DeepSeek's real proof that n^2+n is even.
EVEN = Proof(steps=[
    step("Every integer $n$ is even or odd.", why="parity"),
    step("Case 1: $n = 2k$.", claim="n=2k"),
    step("Substitute.", claim="n^2+n=(2k)^2+2k", checked=True),
    step("Expand.", claim="n^2+n=4k^2+2k", checked=True),
    step("Factor.", claim="n^2+n=2(2k^2+k)", why="take out 2", checked=True),
    step("So $n^2+n$ is even.", why="twice an integer"),
])


def test_a_proof_becomes_a_valid_scene_where_an_equation_evolves_in_place():
    scene = proof_scene(EVEN)
    assert reference.validate(scene) == []
    ops = [(s["op"], s.get("from"), s.get("latex")) for s in scene["steps"]]
    # The prose opener has nothing to draw; "Case 1" writes n = 2k with the opener in its caption.
    assert scene["steps"][0]["op"] == "write" and scene["steps"][0]["say"].startswith("Every integer")
    # n^2+n = ... is written once, then transformed twice in place.
    assert ops[1:4] == [("write", None, "n^2+n=(2k)^2+2k"), ("transform", "s2", "n^2+n=4k^2+2k"),
                        ("transform", "s3", "n^2+n=2(2k^2+k)")]
    assert "✓ checked by SymPy" in scene["steps"][3]["say"] and "Why: take out 2" in scene["steps"][3]["say"]
    # The conclusion in words highlights the final line.
    assert scene["steps"][-1] == {"id": "s5", "op": "highlight", "target": "s4", "say": "So $n^2+n$ is even.\nWhy: twice an integer"}


def test_a_full_board_fades_and_starts_again_at_the_top():
    proof = Proof(steps=[step(f"line {i}", claim=f"x_{i}=y_{i}") for i in range(ROWS + 2)])
    scene = proof_scene(proof)
    assert reference.validate(scene) == []
    fades = [s for s in scene["steps"] if s["op"] == "fade"]
    assert len(fades) == ROWS and all("say" not in f for f in fades)  # they play straight through
    writes = [s for s in scene["steps"] if s["op"] == "write"]
    assert writes[ROWS]["pos"] == writes[0]["pos"]  # back at the top
    assert all(0 <= v <= 1 for w in writes for v in w["pos"])


def test_inline_math_from_words_is_drawn_and_long_words_only_caption():
    proof = Proof(steps=[step(r"Assume \(\sqrt{2} = a/b\) in lowest terms."),
                         step("Square both sides.", claim="2=a^2/b^2", checked=True)])
    scene = proof_scene(proof)
    assert scene["steps"][0]["latex"] == r"\sqrt{2} = a/b" and reference.validate(scene) == []
    assert proof_scene(Proof(steps=[step("Every integer is even or odd.")])) is None
