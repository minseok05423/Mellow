# Mellow
An LLM-assisted online blackboard with 3Blue1Brown-style animations.
Project spec: [CLAUDE.md](CLAUDE.md). Design decisions: [DECISIONS.md](DECISIONS.md).

## Run it

**Frontend** (Vite + TypeScript), the only port you need to forward is **5173**:
```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

**Backend** (FastAPI), runs on 8000. You do *not* need to forward it: the Vite dev
server proxies `/api/*` → `http://localhost:8000/*`.
```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8000
# check: curl localhost:8000/health   or, via the frontend: curl localhost:5173/api/health
```
Keys go in `backend/.env` (copy `backend/.env.example`; it's git-ignored). With no keys,
everything still works: handwriting readings come back empty, and you type them in the
confirm chip. Backend tests: `cd backend && .venv/bin/python -m pytest -q`.

**Recognizer bake-off**: record samples in the notebook (*Save sample*), download
them, then `cd backend && .venv/bin/python -m bakeoff.run ~/Downloads/mellow-samples-*.json`.
The report lands in `backend/bakeoff/results/`.

### Port forwarding (Codespaces / VS Code)
Open the **Ports** tab, forward 5173 (VS Code usually does this automatically when
`npm run dev` starts), and click the globe icon to open it in your browser.

### Testing on an iPad (or any device other than this machine)
tldraw treats any HTTPS address that isn't localhost as production, including the
forwarded Codespaces URL. Get a free hobby key or a trial key at
https://tldraw.dev/pricing, then:
```bash
cp frontend/.env.example frontend/.env.local   # paste the key into it
```
Restart `npm run dev`. In VS Code's **Ports** tab, set port 5173 to *Public* if the
iPad isn't signed in to GitHub, and open the forwarded URL in Safari. For a
full-screen app, use Share → Add to Home Screen.

## What's here
- **Notebook (P1)**: `http://localhost:5173/`. A library of notebooks, and lined, grid or
  blank A4 pages. Pen, highlighter, eraser, lasso, shapes, text and images; undo; zoom.
  Saves automatically in the browser. Every pen stroke also stores its raw points with
  timestamps, for handwriting recognition later.
  - **Bake-off samples**: lasso one handwritten line → *Save sample* → type the expected
    LaTeX → *Save*. *Samples (n) ⤓* downloads them all as JSON for the recognizer test.
- **Read + Check (P2)**: lines are read live, shown faintly under each line (*Readings
  on/off*). Lasso a line → **Check ✓ / Hint / Answer / Explain with AI** → confirm or fix what was read (and what it is, and its problem) → SymPy or the reasoning model answers
  against the line above → a feedback card appears next to the line: follows,
  counterexample, or a question when it can't be settled algebraically.
- **Models menu**: pick the handwriting reader, the image reader and the reasoning model.
  Options without a configured key are disabled. `GET /api/models` shows what's available.
- **M1 ε–δ demo**: `http://localhost:5173/epsdelta.html`. Drag ε and watch δ respond.
- **Tests**: see `frontend/tests/README.md`.
- **Spec**: `SPEC.md`. Build order is P1–P6 in §9.
