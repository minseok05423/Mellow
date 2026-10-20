"""Lean checking (app/leancheck.py) without Lean or a model: assembling the file, mapping errors back to lines,
rejecting proofs."""
from app.leancheck import (Item, StepProof, StepProofs, Translation, assemble, hypotheses, problems_check,
                            proofs_check, under_contradiction, verdicts)

STATEMENT = "(a b : ℤ) (ha : Even a) (hb : Even b) : Even (a + b)"
ITEMS = [Item(line=0, kind="tactic", text="obtain ⟨k, hk⟩ := ha"),
         Item(line=1, kind="have", text="a + b = 2 * k", name="h1"),
         Item(line=2, kind="close", text="Even (a + b)", name="h2")]


def where_of(src: str, text: str) -> int:
    """1-based line number of the first proof line containing `text`."""
    return next(n for n, line in enumerate(src.splitlines(), 1) if text in line and n > 5)


def test_hypotheses_are_the_binders_before_the_colon():
    assert hypotheses(STATEMENT) == ["a", "b", "ha", "hb"]
    assert hypotheses("{α β : Type*} (f : α → β) (hf : Function.Injective f) : Function.Injective (f ∘ id)") == \
        ["α", "β", "f", "hf"]
    assert hypotheses(": ∀ ε > 0, ∃ δ > 0, (2 : ℝ) < 3") == []


def test_assemble_maps_lines_and_puts_a_marker_after_each_item():
    src, where, markers = assemble(STATEMENT, ITEMS)
    lines = src.splitlines()
    assert lines[4].startswith("theorem target")
    assert lines[5:7] == ["  obtain ⟨k, hk⟩ := ha", '  trace "mellow 0"'] and where[6] == (0, "tactic", 0)
    have = lines[7]
    assert have.startswith("  have h1 : a + b = 2 * k := by first | (omega; done)")
    assert where[8] == (1, "claim", have.index("by first"))
    assert lines[9:12] == [lines[9], "  exact h2", '  trace "mellow 2"'] and where[11] == (2, "exact", 0)
    assert markers == {0: [0], 1: [1], 2: [2]}


def test_block_openers_get_their_marker_inside_the_block():
    items = [Item(line=0, kind="tactic", text="induction n with"),
             Item(line=1, kind="tactic", text="| zero =>"),
             Item(line=1, kind="close", text="P 0", name="h0", indent=1)]
    src, _, markers = assemble("(n : ℕ) : P n", items)
    assert src.splitlines()[5:8] == ["  induction n with", "  | zero =>", '    trace "mellow 0"']
    assert markers[0] == [0, 1]  # the `with` line is vouched for by the next marker


def test_reason_check_clears_uncited_hypotheses_inside_a_true_block():
    items = [Item(line=0, kind="have", text="Even a", name="h0", reasons=["ha"])]
    src, where, _ = assemble(STATEMENT, items)
    lines = src.splitlines()
    assert lines[5] == "  have _reason0 : True := by"
    assert lines[6].startswith("    have _r : Even a := by (try clear a); (try clear b); (try clear hb); first |")
    assert lines[7] == "    trivial" and lines[8].startswith("  have h0 : Even a := by first")
    assert {where[n][1] for n in (6, 7, 8)} == {"reason"}


def test_model_proofs_replace_the_automation_for_that_claim():
    src, where, _ = assemble(STATEMENT, ITEMS, {1: "obtain ⟨m, hm⟩ := hb\nomega"})
    n = where_of(src, "have h1")
    assert src.splitlines()[n - 1] == "  have h1 : a + b = 2 * k := by"
    assert src.splitlines()[n:n + 2] == ["    obtain ⟨m, hm⟩ := hb", "    omega"]
    assert where[n + 1] == (1, "proof", 0)


def test_model_proofs_keep_their_own_indentation():
    proof = "rcases h with h | h\n· exact Or.inl h\n· right\n  exact h"
    src, _, _ = assemble(STATEMENT, ITEMS, {1: proof})
    n = where_of(src, "have h1")
    assert src.splitlines()[n:n + 4] == ["    rcases h with h | h", "    · exact Or.inl h", "    · right", "      exact h"]


def test_verdicts():
    src, where, markers = assemble(STATEMENT, ITEMS)
    h1, ex = where_of(src, "have h1"), where_of(src, "exact h2")
    by_col = where[h1][2]

    def run(out, proved_by_model=frozenset()):
        return verdicts(out, where, markers, ITEMS, 3, proved_by_model)

    # The automation failed on line 1: "not proved", and it is offered to the model; not a structural error.
    v = run(f"mellow 0\nx.lean:{h1}:{by_col}: error: unsolved goals\n⊢ False\nmellow 1\nmellow 2\n")
    assert v["lines"] == ["proved", "not proved", "proved"] and v["failed_claims"] == [1] and v["structural"] == []
    # The claim itself doesn't elaborate: a structural error for a retry, not offered to the model.
    v = run(f"mellow 0\nx.lean:{h1}:20: error(lean.unknownIdentifier): Unknown identifier `kk`\nmellow 1\nmellow 2\n")
    assert v["lines"][1] == "not proved" and v["failed_claims"] == [] and "Unknown identifier" in v["structural"][0]
    # A failing tactic ends the proof: no errors after it, but no markers either.
    v = run(f"mellow 0\nx.lean:{ex}:2: error: Type mismatch\n")
    assert v["lines"] == ["proved", "unchecked", "not proved"] and "Type mismatch" in v["structural"][0]
    assert run("x.lean:5:60: error: unsolved goals\n⊢ Even (a + b)\nmellow 0\nmellow 1\nmellow 2\n")["unfinished"]
    v = run(f"mellow 0\nx.lean:{h1}:3: error: unexpected token ')'; expected term\n")
    assert v["lines"] == ["proved", "unchecked", "unchecked"]
    assert run("mellow 0\nmellow 1\nmellow 2\n", {1})["lines"] == ["proved", "skipped step", "proved"]


def test_wrong_reason_verdict():
    items = [Item(line=0, kind="have", text="Even a", name="h0", reasons=["hb"])]
    src, where, markers = assemble(STATEMENT, items)
    r = where_of(src, "have _r")
    v = verdicts(f"x.lean:{r}:60: error: unsolved goals\n⊢ Even a\nmellow 0\n", where, markers, items, 1)
    assert v["lines"] == ["wrong reason"]


def test_translations_that_prove_skip_or_spoof_are_rejected():
    hyps = hypotheses(STATEMENT)
    assert problems_check(Translation(items=ITEMS), 3, hyps) == []
    assert problems_check(Translation(items=ITEMS[:2]), 3, hyps)  # line 2 missing
    for bad in [Item(line=0, kind="tactic", text="exact ⟨k, by ring⟩"),
                Item(line=0, kind="have", text="P := by sorry", name="h"),
                Item(line=0, kind="have", text="P", name="h", reasons=["hz"]),
                Item(line=0, kind="tactic", text='rw [show "mellow 3"]')]:
        assert problems_check(Translation(items=[bad]), 1, hyps), bad
    assert proofs_check(StepProofs(proofs=[StepProof(index=1, proof="omega")]), {1}) == []
    assert proofs_check(StepProofs(proofs=[StepProof(index=2, proof="omega")]), {1})
    assert proofs_check(StepProofs(proofs=[StepProof(index=1, proof='trace "mellow 3"\nsorry')]), {1})


def test_no_model_proofs_after_suppose_for_contradiction():
    items = [Item(line=0, kind="tactic", text="intro h", contradiction=True),
             Item(line=1, kind="have", text="p ^ 2 = 2 * q ^ 2", name="h1"),
             Item(line=2, kind="tactic", text="· exfalso", indent=1)]
    assert under_contradiction(items) == {1, 2}
    cases = [Item(line=0, kind="tactic", text="rcases h with h | h"),
             Item(line=0, kind="tactic", text="·", indent=0),
             Item(line=1, kind="tactic", text="by_contra hc", indent=1),
             Item(line=1, kind="have", text="False", name="h1", indent=1),
             Item(line=2, kind="have", text="P", name="h2", indent=0)]
    assert under_contradiction(cases) == {3}  # the second case is outside the contradiction's block


def test_a_typed_assumption_that_misses_the_goal_is_not_sent_back_for_a_retry():
    items = [Item(line=0, kind="tactic", text="intro x y (h : x = y)")]
    src, where, markers = assemble("(f : ℕ → ℕ) : Function.Injective f", items)
    out = ("x.lean:6:12: error: Type mismatch: Hypothesis `h` has type\n  f x = f y\nbut is expected to have type\n"
           "  x = y\ndue to the provided type annotation\n")
    v = verdicts(out, where, markers, items, 1)
    assert v["lines"] == ["not proved"] and v["structural"] == []
