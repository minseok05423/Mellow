# Mellow — LLM-assisted handwriting notebook for math

**The product spec is [SPEC.md](SPEC.md).** Read it before any feature work. It covers
the vision, the GoodNotes feature set, the assistant, reading handwriting,
storage/sync, the LLM rules, the stack, and the build order (P1–P6).
Design decisions and their reasons are in [DECISIONS.md](DECISIONS.md).

## In one paragraph
A proof checker that lives on the tablet (DECISIONS #32): a GoodNotes-style notebook (web/PWA,
iPad + Pencil as the reference device) for university students doing intro proofs. Students
write a problem and its proof by hand; when they select it and press **Check my proof**, their
confirmed lines become a graph of steps and premises, which they confirm, and each step is
checked on its own: SymPy at random values (Tested), Lean 4 + Mathlib (Proved), or three
unanimous LLM judges (Judged). Every verdict shows its tier. The notebook was stripped to this one
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
Validation + retry, caching, logging, honest verification (every verdict states how it was
checked: Proved by Lean, Tested by SymPy, or Judged by AI; a Judged ✓ needs three unanimous
judges, and only Proved is ever called certain; DECISIONS #32 replaced "Lean checks every
verdict"), API keys server-side only (LLM and handwriting-recognizer vendors alike), and
confirm-before-judge: no verdict on a reading, or a proof graph, the student hasn't confirmed.
Model-written Lean only ever runs sandboxed, and model-written math reaches SymPy only through
the whitelist parser.

## Working style
- Keep code simple. The user knows PyTorch and web basics, but is new to canvas
  animation and tldraw.
- Log non-obvious decisions in DECISIONS.md (one paragraph each: what and why).
- Mark deliberate shortcuts with a `shortcut:` comment naming the upgrade path.
- Stop after each phase and show how to test it.
- Use the webapp-testing skill to verify UI behaviour with screenshots.

## Repo layout
- `frontend/`: Vite + React + TypeScript + tldraw, dev server on port 5173.
  `index.html` + `src/notebook/`: the notebook; `CheckUI.tsx` is the check flow.
- `backend/`: FastAPI on port 8000, proxied as `/api` by Vite. `app/graphcheck.py` checks a
  proof as a graph of steps (`app/lean.py` runs Lean sandboxed; `app/leancheck.py` is the
  earlier line-by-line Lean checker, kept for the experiments); `app/recognize.py` reads handwriting.
- `backend/leaneval/`: the experiments (L0, L0b, L0d, L1g = `graph.py`), run against the app's
  own code; `agentq.py` lets Claude Code agents answer the model calls instead of the API.
- `lean/`: the Lean 4 + Mathlib project. On Codespaces, run it from a copy on local disk
  (`MELLOW_LEAN_DIR`, see README).
- `.claude/skills/`: `scene-json` and `math-verify` describe the removed SymPy/scene code;
  they're kept for history and don't apply to the current app.
- See README.md for how to run.
