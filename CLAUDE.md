# Mellow — LLM-assisted handwriting notebook for math

**The product spec is [SPEC.md](SPEC.md).** Read it before any feature work. It covers
the vision, the GoodNotes feature set, the assistant, reading handwriting,
storage/sync, the LLM rules, the stack, and the build order (P1–P6).
Design decisions and their reasons are in [DECISIONS.md](DECISIONS.md).

## In one paragraph
A lightweight Lean prover that lives on the tablet (DECISIONS #25, #27): a GoodNotes-style
notebook (web/PWA, iPad + Pencil as the reference device) for university students doing
intro proofs. Students write a problem and its proof by hand; when they select it and press
**Check with Lean**, the LLM translates their confirmed lines into Lean and Lean 4 + Mathlib
checks the proof line by line on a dedicated server. The notebook was stripped to this one
feature on 2026-10-20 (SymPy checks, hints, answers, animations and widgets are gone; see
git history before that date). The learner does the generative work; the board responds to
their reasoning.

## Environment & constraints
- Cloud-hosted Linux machine (Codespaces), VS Code. No Mac, no iPad on hand.
- Tested in a browser via port forwarding, mostly with a mouse; pen device or a
  borrowed iPad for real handwriting tests.
- tldraw counts any HTTPS non-localhost URL, including the forwarded Codespaces URL,
  as production. It needs `VITE_TLDRAW_LICENSE_KEY` (hobby or trial key) there.

## NON-NEGOTIABLES (do not simplify these away, even in minimal mode)
Validation + retry, caching, logging, machine verification (Lean checks every verdict; an
LLM alone never judges a step: this replaced "SymPy verification" when Lean took over,
DECISIONS #27), API keys server-side only (LLM and handwriting-recognizer vendors alike),
and confirm-before-judge: no verdict on a reading, or a Lean statement, the student hasn't
confirmed. Model-written Lean only ever runs sandboxed.

## Working style
- Keep code simple. The user knows PyTorch and web basics, but is new to canvas
  animation and tldraw.
- Log non-obvious decisions in DECISIONS.md (one paragraph each: what and why).
- Mark deliberate shortcuts with a `shortcut:` comment naming the upgrade path.
- Stop after each phase and show how to test it.
- Use the webapp-testing skill to verify UI behaviour with screenshots.

## Repo layout
- `frontend/`: Vite + React + TypeScript + tldraw, dev server on port 5173.
  `index.html` + `src/notebook/`: the notebook; `CheckUI.tsx` is the Lean flow.
- `backend/`: FastAPI on port 8000, proxied as `/api` by Vite. `app/leancheck.py` checks a
  proof with Lean (`app/lean.py` runs Lean sandboxed); `app/recognize.py` reads handwriting.
- `backend/leaneval/`: the Lean experiments (L0, L0b), run against the app's own code.
- `lean/`: the Lean 4 + Mathlib project. On Codespaces, run it from a copy on local disk
  (`MELLOW_LEAN_DIR`, see README).
- `.claude/skills/`: `scene-json` and `math-verify` describe the removed SymPy/scene code;
  they're kept for history and don't apply to the current app.
- See README.md for how to run.
