# Mellow
A handwriting notebook that checks your proofs step by step: write a proof by hand, select it, and each step is checked
on its own, by Lean 4 + Mathlib, SymPy or three AI judges, with the verdict saying which. Overview: [PROJECT.md](PROJECT.md). Spec: [SPEC.md](SPEC.md). Decisions: [DECISIONS.md](DECISIONS.md).

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
Keys go in `backend/.env` (copy `backend/.env.example`; it's git-ignored). Checking needs
`ANTHROPIC_API_KEY`, or `MELLOW_AGENT_QUEUE=<folder>` to have Claude Code agents answer the model calls instead
(`backend/leaneval/agentq.py`). Without Lean set up, steps are still Tested or Judged, never Proved. Without a reader key, handwriting readings come back empty and you type them in the
confirm box. Backend tests: `cd backend && .venv/bin/python -m pytest -q`.

**Lean** (Lean 4 + Mathlib, the `lean/` project; the toolchain comes from elan). On Codespaces, Mathlib must sit on
the local disk, not `/workspaces` (a check there takes over 5 minutes instead of ~5 s, DECISIONS #26):
```bash
cp -a lean /tmp/mellow-lean        # again after every Codespace restart: /tmp is wiped
# backend/.env: MELLOW_LEAN_DIR=/tmp/mellow-lean
curl localhost:8000/health         # "lean": true when the checker finds Mathlib
```
Lean runs sandboxed with bwrap (`sudo apt install bubblewrap` if it's missing).

**Experiments**: `backend/leaneval/` (L0: can a model prove the problems; L0b/L0d: does line-by-line Lean checking
catch planted and subtle mistakes; L1g, `graph.py`: the same proofs through the graph check) and `backend/bakeoff/` (handwriting recognizers). Each file's docstring says how to run it. Every run appends its headline to
`backend/leaneval/results/SUMMARY.md`, one file for all results; the full report stays in the run's own folder.

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

### Putting it online for testers (EC2)
Testers use the web app on their own iPad; there is no native app to install (DECISIONS #29). Scripts in `deploy/`:
1. Log in the AWS CLI once: `aws configure` (an access key of an IAM user allowed to use EC2 and SSM).
2. Put a tldraw key in `frontend/.env.local` (see above); the server counts as production.
3. `deploy/launch.sh`, once: starts a t3.xlarge (16 GB, about $4/day while running) with a fixed IP and saves its
   details and the testers' access code in `deploy/.instance` (git-ignored).
4. `deploy/deploy.sh`, after every change: builds the frontend, copies the app and `backend/.env`'s keys to the
   server, and sets up Lean, Mathlib, Python, HTTPS (Caddy + an sslip.io name) and the service. It prints the link
   for testers: `https://<ip>.sslip.io/?key=<code>`. They open it in Safari, then Share → Add to Home Screen.

Without the code in the link, the API answers 401, so a leaked bare URL can't spend the LLM budget. Also set a monthly
spend limit in the Anthropic console. Pause billing with
`aws ec2 stop-instances --instance-ids <id from deploy/.instance>` (and `start-instances`); the disk and IP cost
about $7/month while it's stopped.

## What's here
- **Notebook**: `http://localhost:5173/`. A library of notebooks, and lined, grid or blank A4 pages. Pen, eraser,
  lasso, select, hand; undo; zoom. Saves automatically in the browser. Every pen stroke also stores its raw points
  with timestamps, for handwriting recognition.
- **Images**: the **Image** button (or paste) puts a screenshot or photo on the page; it's read like a line.
- **Reading**: nothing is read while you write; Check my proof reads the lines you selected (MyScript, then
  Claude Haiku when MyScript's reading isn't usable).
- **Check my proof**: lasso a problem and its proof → **Check my proof** → confirm or fix the readings and which line
  is the problem → your proof as a graph of steps and arrows, which you can fix by dragging, then confirm → each step is
  checked on its own → a card next to the proof: ✓, ✗ or ? per step, with how it was checked: *Proved by Lean*
  (certain), *Tested* (SymPy at random values; a ✗ comes with values where it fails), or *Judged by AI* (✓ only when
  three judges agree). Checking the same proof again reuses the confirmed steps (DECISIONS #32).
- **Measuring the friends test**: the app logs a few events (`events` table: check started and result, steps
  rejected, leaving during a wait, the answer to "What will you do now?") and a Feedback button in the top bar.
- **Tests**: backend `pytest`; frontend see `frontend/tests/README.md`.
