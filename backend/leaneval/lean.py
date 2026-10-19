"""Check Lean proofs against Mathlib for the L0 experiment (leaneval/run.py).

Lean runs inside bwrap: no network, no environment, and only the system libraries, the Lean toolchain, this Mathlib
project (read-only) and a scratch folder are visible, so model-written code (Lean can run code while elaborating)
can't reach keys, the backend or the internet. The text filter below only stops cheating.
The checker builds the file itself: `import Mathlib`, the model's helper lemmas, then `theorem target <statement> := by
<proof>` with the statement we chose, so a model can't change what gets proved. Text that runs code or cheats is rejected
before Lean sees it, and `#print axioms` confirms only Lean's three standard axioms were used.
shortcut: one `lake env lean` process per check, which reloads Mathlib every time. Upgrade to the Lean REPL (Mathlib
loaded once) if checks are too slow for the app.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
import time
from pathlib import Path

LEAN_DIR = Path(__file__).resolve().parents[2] / "lean"
TIMEOUT_S = 300
STANDARD_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
# Model text must not run code, add axioms, skip proofs or redefine syntax. (`?` tactics search and print; banned so a
# proof is a proof, not a search request.)
FORBIDDEN = re.compile(r"\b(sorry|admit|axiom|native_decide|unsafe|implemented_by|extern|run_cmd|run_tac|run_elab|"
                       r"macro|macro_rules|elab|elab_rules|syntax|declare_syntax_cat|initialize|import|IO)\b"
                       r"|#eval|#exit|#print|#check|\b(exact|apply|rw|simp)\?")


# The project's toolchain, called directly: elan would try to go online, which the sandbox blocks.
TOOLCHAIN = Path.home() / ".elan" / "toolchains" / (
    (LEAN_DIR / "lean-toolchain").read_text().strip().replace("/", "--").replace(":", "---"))


def _run(src: str) -> tuple[str, float]:
    bin_ = TOOLCHAIN / "bin"
    with tempfile.TemporaryDirectory() as work:
        f = Path(work) / "check.lean"
        f.write_text(src)
        cmd = ["bwrap", "--ro-bind", "/usr", "/usr", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
               "--symlink", "usr/bin", "/bin", "--ro-bind", str(TOOLCHAIN), str(TOOLCHAIN),
               "--ro-bind", str(LEAN_DIR), str(LEAN_DIR), "--proc", "/proc", "--dev", "/dev",
               "--tmpfs", "/tmp", "--bind", work, work,  # /tmp first: the scratch folder lives under it
               "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
               "--setenv", "PATH", f"{bin_}:/usr/bin", "--setenv", "HOME", work, "--chdir", str(LEAN_DIR),
               str(bin_ / "lake"), "env", str(bin_ / "lean"), str(f)]
        t = time.time()
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
            return r.stdout + r.stderr, time.time() - t
        except subprocess.TimeoutExpired:
            return f"error: Lean took longer than {TIMEOUT_S}s", time.time() - t


def _errors(out: str, offset: int) -> list[str]:
    """Lean's error messages, with line numbers counted from the start of the model's text."""
    errs = []
    for m in re.finditer(r"^\S*?:(\d+):(\d+): error: (.*?)(?=^\S*?:\d+:\d+: |\Z)", out, re.M | re.S):
        errs.append(f"line {int(m.group(1)) - offset}, col {m.group(2)}: {m.group(3).strip()}")
    if not errs and "error" in out.lower():
        errs.append(out.strip()[:1500])
    return errs


def check_proof(statement: str, proof: str, helpers: str = "") -> dict:
    """Does `proof` (tactics after `:= by`) prove `statement`? {"ok", "errors", "seconds"}."""
    bad = FORBIDDEN.search(helpers + "\n" + proof)
    if bad:
        return {"ok": False, "errors": [f"not allowed: {bad.group(0)!r}"], "seconds": 0.0}
    head = f"import Mathlib\n\n{helpers.strip()}\n\ntheorem target {statement} := by\n"
    body = "\n".join("  " + line for line in proof.strip().splitlines())
    out, secs = _run(f"{head}{body}\n\n#print axioms target\n")
    errors = _errors(out, offset=0)
    axioms = re.search(r"depends on axioms: \[(.*?)\]", out, re.S)
    used = {a.strip() for a in axioms.group(1).split(",")} if axioms else set()
    if not errors and not used <= STANDARD_AXIOMS:
        errors.append(f"uses non-standard axioms: {sorted(used - STANDARD_AXIOMS)}")
    return {"ok": not errors, "errors": errors, "seconds": round(secs, 1)}


def check_statement(statement: str) -> dict:
    """Does a statement (binders and type) typecheck? Proved with our own `sorry`, so only the statement is judged."""
    bad = FORBIDDEN.search(statement)
    if bad:
        return {"ok": False, "errors": [f"not allowed: {bad.group(0)!r}"], "seconds": 0.0}
    out, secs = _run(f"import Mathlib\n\ntheorem target {statement} := by\n  sorry\n")
    errors = _errors(out, offset=0)
    return {"ok": not errors, "errors": errors, "seconds": round(secs, 1)}
