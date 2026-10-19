import json

from bakeoff import run


def test_normalize_ignores_formatting():
    assert run.normalize(r"2 q^{2} = p^{2}") == run.normalize("2q^2=p^2")
    assert run.normalize(r"\left( a \right) \cdot b") == run.normalize("(a)*b")
    assert run.normalize(r"\text{Hence } p") == run.normalize(r"\text{hence} p")


def test_equivalent_accepts_swapped_sides_but_not_different_math():
    assert run.equivalent("2q^2=p^2", "p^2=2q^2")
    assert not run.equivalent("2q^2=p^2", "q^2=p^2")
    assert not run.equivalent(r"\text{hence}", "x")


def test_run_with_stub_candidate_writes_report(tmp_path, monkeypatch):
    samples = {"schema_version": 1, "samples": [
        {"id": "a", "kind": "math", "truth": "2q^2", "strokes": [{"t0": 0, "pts": [[0, 0, 0.5, 0]]}], "image": ""},
        {"id": "b", "kind": "mixed", "truth": r"\text{hence } p", "strokes": [{"t0": 0, "pts": [[0, 0, 0.5, 0]]}], "image": ""},
    ]}
    f = tmp_path / "s.json"
    f.write_text(json.dumps(samples))
    monkeypatch.setattr(run, "run_candidate", lambda c, s: ("2 q^{2}" if s["id"] == "a" else "hence", 0.9, 0.001))
    monkeypatch.setattr(run, "__file__", str(tmp_path / "run.py"))
    run.main([str(f), "--candidates", "stub"])
    report = next(tmp_path.glob("results/*/report.md")).read_text()
    assert "| stub | 100% (1) | 0% (1) | – | 50% (2)" in report
    assert "## Misses" in report
