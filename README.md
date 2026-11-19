# Mellow

Mellow is a handwriting notebook that checks proofs one step at a time. You write a proof by hand
(iPad + Pencil is the reference device), lasso it, and press **Check my proof**. Each step comes
back with ✓, ✗ or ?, and with how it was checked:

- **Proved**: Lean 4 + Mathlib proved the step from its premises alone. This is the only verdict
  that is called certain.
- **Tested**: SymPy evaluated the step at 40 random values. A ✗ comes with the values where it fails.
- **Judged**: three LLM judges marked the step against a marking scheme. It gets ✓ only if all
  three accept and ✗ only if all three reject; anything else is ?, and you see their comments.

It's aimed at university students writing their first ε–δ and induction proofs. The idea is that
the student does the reasoning and the notebook only responds to what they wrote: nothing is
judged until the student has confirmed both the handwriting reading and the structure of the proof.

## How a check works

1. The selected lines are read only when you press the button (Mathpix by default, or MyScript;
   a vision model re-reads lines the recognizer isn't sure about). You confirm or fix each reading.
2. A cheap model splits the proof into numbered steps and nested blocks (cases, contradiction,
   induction, ...), and a stronger model links each step to the earlier steps it rests on. This
   shows up as a graph you can fix by dragging before confirming.
3. Two mistakes are caught by rules with no model at all: reusing a name for a new object, and
   choosing something (δ) that depends on a variable the goal quantifies later (x).
4. Every remaining step is checked alone with the cheapest method that works: SymPy, then Lean
   (with `plausible` looking for counterexamples in the same run), then the three judges.

The details and the reasons behind them are in [DECISIONS.md](DECISIONS.md) (42 entries so far).
[SPEC.md](SPEC.md) is the product spec and [PROJECT.md](PROJECT.md) is a single-page overview.

## Running it

Frontend (Vite + React + tldraw), on port 5173:

```bash
cd frontend
npm install
npm run dev
```

Backend (FastAPI), on port 8000. Vite proxies `/api/*` to it, so 5173 is the only port you need
to forward.

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then fill in the keys
.venv/bin/uvicorn app.main:app --reload --port 8000
curl localhost:8000/health
```

Every model call goes to Anthropic, so checking needs `ANTHROPIC_API_KEY`. The models are set in
`backend/app/config.py` (Haiku splits the proof, Opus links it, Sonnet writes the Lean/SymPy
forms and judges). Setting `MELLOW_AGENT_QUEUE=<folder>` makes Claude Code agents answer the
model calls instead of the API (`backend/leaneval/agentq.py`). Without a recognizer key the
readings come back empty and you type them in.

Lean is optional. Without it a step can be Tested or Judged, but never Proved. The `lean/`
folder is a Lean 4 + Mathlib project (toolchain via elan), and Lean always runs inside `bwrap`
with no network (`sudo apt install bubblewrap`). On Codespaces, keep Mathlib on the local disk.
From `/workspaces` a single check takes over 5 minutes instead of about 5 seconds:

```bash
cp -a lean /tmp/mellow-lean          # redo after every Codespace restart
# in backend/.env: MELLOW_LEAN_DIR=/tmp/mellow-lean
curl localhost:8000/health           # "lean": true once Mathlib is found
```

Tests: `cd backend && .venv/bin/python -m pytest -q`. For the browser tests, see
[frontend/tests/README.md](frontend/tests/README.md).

### On an iPad

tldraw treats any HTTPS address other than localhost as production, including a forwarded
Codespaces URL, so it needs a license key there (a free hobby key from https://tldraw.dev/pricing
works). Put it in `frontend/.env.local` (copy `frontend/.env.example`) and restart `npm run dev`.
Then make port 5173 public in VS Code's Ports tab and open the URL in Safari. Share → Add to Home
Screen gives a full-screen app.

### Deploying for testers

`deploy/` holds scripts for a single EC2 instance (t3.xlarge, roughly $4 a day while it runs):

- `deploy/launch.sh` runs once. It starts the instance with a fixed IP and writes its id and an
  access code to `deploy/.instance`.
- `deploy/deploy.sh` runs after each change. It builds the frontend, copies the app and the keys
  from `backend/.env`, sets up Lean, Mathlib, Caddy (HTTPS via sslip.io) and the service, and
  prints the link to send: `https://<ip>.sslip.io/?key=<code>`.

Requests without the access code get a 401, so a leaked URL can't spend the API budget. Set a
monthly limit in the Anthropic console anyway. `aws ec2 stop-instances` pauses billing (about
$7 a month for the disk and IP while stopped).

## Layout

```
frontend/src/notebook/   the notebook; CheckUI.tsx is the check flow, GraphEditor.tsx the graph
backend/app/             FastAPI app: graphcheck.py (the step-by-step check), lean.py (sandboxed
                         Lean), recognize.py (handwriting), llm.py (validated, cached model calls)
backend/leaneval/        experiments: can a model prove the problems, does checking catch planted
                         and subtle mistakes, the graph check on the same proofs
backend/bakeoff/         handwriting recognizer comparison
lean/                    the Lean 4 + Mathlib project
deploy/                  EC2 scripts
```

Each experiment's docstring says how to run it. Runs append a line to
`backend/leaneval/results/SUMMARY.md`.

## Status

This is an early prototype being tried with a few friends. There are no accounts yet. Each
browser gets an anonymous id, and notebooks are saved in the browser through tldraw's IndexedDB
persistence. On 2026-10-20 the
notebook was cut down to this one feature, so the earlier animated ε–δ demos, hints and widgets
only exist in the git history.
