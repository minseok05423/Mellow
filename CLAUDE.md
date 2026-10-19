# Mellow — LLM-assisted handwriting notebook for math

**The product spec is [SPEC.md](SPEC.md).** Read it before any feature work. It covers
the vision, the GoodNotes feature set, the assistant, reading handwriting,
storage/sync, the LLM rules, the stack, and the build order (P1–P6).
Design decisions and their reasons are in [DECISIONS.md](DECISIONS.md).

## In one paragraph
A GoodNotes-style notebook (web/PWA, iPad + Pencil as the reference device) for
university students doing intro proofs. Students write on blank pages; when they
select part of their work, an assistant checks it (SymPy), explains it with
Manim-style animations, hints, or inserts a widget, drawn on the page. The learner
does the generative work; the board responds to their reasoning.

## Environment & constraints
- Cloud-hosted Linux machine (Codespaces), VS Code. No Mac, no iPad on hand.
- Tested in a browser via port forwarding, mostly with a mouse; pen device or a
  borrowed iPad for real handwriting tests.
- Do NOT use Manim at runtime. A small Manim-style animation engine lives in the
  browser (`frontend/src/anim.ts`).
- tldraw counts any HTTPS non-localhost URL, including the forwarded Codespaces URL,
  as production. It needs `VITE_TLDRAW_LICENSE_KEY` (hobby or trial key) there.

## NON-NEGOTIABLES (do not simplify these away, even in minimal mode)
Validation + retry, caching, logging, SymPy verification, API keys server-side only
(LLM and handwriting-recognizer vendors alike), and confirm-before-judge: no verdict
on a reading the student hasn't confirmed.

## Working style
- Keep code simple. The user knows PyTorch and web basics, but is new to canvas
  animation and tldraw.
- Log non-obvious decisions in DECISIONS.md (one paragraph each: what and why).
- Mark deliberate shortcuts with a `shortcut:` comment naming the upgrade path.
- Stop after each phase and show how to test it.
- Use the webapp-testing skill to verify UI behaviour with screenshots.

## Repo layout
- `frontend/`: Vite + React + TypeScript + tldraw, dev server on port 5173.
  - `index.html` + `src/notebook/`: the notebook app (P1 onward).
  - `epsdelta.html` + `src/epsdelta-main.ts`: the M1 ε–δ demo. Its engine (`anim.ts`,
    `plot.ts`, `epsdelta.ts`) becomes the first board widget in P3.
- `backend/`: FastAPI on port 8000, proxied as `/api` by Vite. Endpoints are stubs until P2.
- `.claude/skills/`: `scene-json` (scene format) and `math-verify` (SymPy checking).
- See README.md for how to run.
