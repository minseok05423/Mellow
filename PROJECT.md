# Mellow: everything in one place

This file is a single overview of the project as of 2026-10-20: what Mellow is, what has been
built, how it works, how to run and test it, what's next, and the strategy discussions so far
(§10). The detailed sources stay where they are: product spec in [SPEC.md](SPEC.md), design
decisions in [DECISIONS.md](DECISIONS.md), working rules in [CLAUDE.md](CLAUDE.md), run
instructions in [README.md](README.md).

---

## 1. What Mellow is

**A lightweight Lean prover that lives on the tablet** (DECISIONS #25, #27). A GoodNotes-style
handwriting notebook for university students doing intro proofs, in the browser as a web app /
PWA, with iPad + Apple Pencil as the reference device.

Students write a problem and its proof by hand. They lasso it and press **Check with Lean**:
they confirm what was read, confirm what Lean will prove (shown in plain words), and Lean 4 +
Mathlib checks the proof **line by line**. A card on the page says, for each line:

- **✓ Lean checked this step.**
- **✓ True, but it needs a step you didn't write** (Lean checked a proof the model filled in).
- **✗ True, but it doesn't follow from the reason you gave.**
- **? Lean couldn't verify this line**: it may be wrong, or need more steps.
- **– Not checked**: Lean stopped before this line.

The tablet is a thin client; reading, LLM calls and Lean run on the server. On 2026-10-20 the
notebook was stripped to this one feature (DECISIONS #27): the earlier SymPy checks, hints,
answers, animated explanations and widgets are in git history.

**Core principle:** the student does the generative work; the board responds to *their*
reasoning. The assistant never acts on its own, and never judges a reading, or a Lean statement,
the student hasn't confirmed.

### Non-negotiables

These are kept even in "minimal" mode:

- validation + retry of every LLM output
- caching
- logging
- machine verification: Lean gives every verdict; an LLM alone never judges a step
- API keys server-side only (LLM and handwriting vendors alike; the browser never calls a vendor)
- confirm-before-judge: no verdict on a reading or statement the student hasn't confirmed
- model-written Lean runs only in the sandbox

---

## 2. How the project got here

1. **Phase 0 + M1.** The project began as an "LLM-assisted blackboard with 3Blue1Brown-style
   animations":
   - wrote CLAUDE.md and DECISIONS.md
   - created two skills, `scene-json` (the animation scene format) and `math-verify` (SymPy step
     checking)
   - scaffolded the Vite frontend and FastAPI backend
   - built the **M1 ε–δ demo**: drag ε and watch the largest working δ respond, with a magnifier
     inset; a step function with f(0)=½ shows the "no δ fits" case
   - a code review found three issues; all were fixed
2. **The product changed** to a freeform handwriting notebook (SPEC.md v2). We chose:
   - the web instead of native iOS
   - the tldraw SDK as the canvas
   - hybrid handwriting reading: a stroke recognizer first, an LLM fallback when needed
   - accounts and server storage (planned for P4)
   - a build order P1–P6
3. **P1, notebook basics:** built and tested.
4. **P2, read + check:** built and tested.
5. **Model work:**
   - DeepSeek on its native API, with streaming
   - DeepSeek for every LLM call
   - MyScript's REST API as the handwriting reader
   - a Models menu so the student picks the models
6. **Fixes from real use** (this session):
   - multi-line selections
   - words read as single letters
   - one card per Check
   - copy/paste
   - proof feedback from the reasoning model
   - the Hints / Answers switch
7. **P3a, structure + four actions** (2026-10-19): page labels (roles + segments), the
   Check / Hint / Answer / Explain menu replacing the switch, and solve-once proofs checked by
   SymPy. Built and tested, including a real DeepSeek smoke test.
8. **Background solving and safer checking:** pages are labelled after a pause and problems
   solved in the background; derived claims cite their steps (a real proof by cases had been
   "refuted" across cases); `n(n+1)` reads as a product; substitution can only verify.
9. **Animated explanations:** Explain on a problem plays the checked proof on a dark board.
10. **Explain as a picture:** geometric proofs the AI draws and SymPy checks (Pythagoras, odd
    numbers, triangle angles and AM-GM all passed on the first real try).
11. **Pasted images** are read by the image reader and work like handwriting.
12. **Lean experiment (L0) started:** Lean 4 + Mathlib installed, a sandboxed checker and a
    25-problem evaluation written.
13. **Lean becomes the core** (2026-10-20, DECISIONS #25). Lean checks were found to thrash
    `/workspaces` (a loop-mounted disk image): over 300 s per check even on 16 GB. With Mathlib
    copied to the local disk at `/tmp` a check takes ~5 s (DECISIONS #26).
14. **L0 and L0b run** (2026-10-20, §9): Claude Sonnet proves 25/25 problems; line-by-line
    checking catches 11/11 planted mistakes with no false ✓ (DECISIONS #28).
15. **The notebook is stripped to Check with Lean** (2026-10-20, DECISIONS #27), tested in the
    browser with faked endpoints and end to end with the real backend, Lean and Claude.
16. **Ready to put online for testers** (2026-10-20, DECISIONS #29). The MVP builds and every
    test passes, end to end too. No native iPad app: testers open an invite link in Safari and add
    it to the home screen. `deploy/` launches one EC2 t3.xlarge with HTTPS and an access code in
    the link; it waits on AWS credentials and a tldraw key.

---

## 3. What works today

### Notebook, at `http://localhost:5173/`
- **Notebook library:** create, rename, delete.
- **Pages:** A4 pages stacked vertically; blank, lined or grid paper; "+ Page" adds one at the end.
- **Tools:** pen, eraser, lasso (selects strokes at least 75% inside the loop), select, hand;
  undo/redo, zoom.
- **Autosave** in the browser (IndexedDB, one store per notebook).
- **Raw ink:** every pen stroke also stores its full-rate points with timestamps
  (`[dx, dy, pressure, dt]`) in the shape's `meta.ink`, for the handwriting recognizer.
- **Live reading:** about 1 s after you stop writing, each new line is read and shown faintly
  under it (*Readings on/off*). Pasted images are read too and count as one line.

### Check with Lean
1. Lasso the problem and its proof → **Check with Lean**.
2. **Confirm box:** each line's reading, to confirm or fix (✎ opens a LaTeX field with a live
   preview), and whether it's *the problem* or *a proof line* (with several lines, the first starts
   as the problem). If no selected line is the problem, type it.
3. **"Lean will check that your proof shows: …"** The problem as a Lean statement, read back in
   plain words by a second model that sees only the Lean. *Yes, check my proof* or *Not what I
   meant*.
4. **The card**, next to the proof: one row per line with its verdict (§1), and a summary
   ("Lean checked every line, and the proof is complete." / "Look again at line 3.").
- Typical times: stating a problem ~10–20 s, a check 5–60 s (a seconds counter shows).
- Old notebooks still open; old cards and animated boards show as plain cards.

---

## 4. How it works

### Reading handwriting (`POST /recognize`)
1. The browser sends a line's strokes (with timing), a PNG of the line, and the confirmed lines
   above it as context.
2. The **stroke recognizer** reads it first: MyScript's REST API (HMAC-signed, solver turned
   off), or Mathpix Strokes.
3. The **LLM fallback** (default DeepSeek Flash) reads the line image when there's no reading,
   confidence is below 0.8, the line has prose, or the reading looks like words spelled as single
   letters.
4. Results are cached by strokes + reader.

### Stating the problem (`POST /lean/statement`)
- Claude Sonnet writes the Lean statement; Lean must accept it (checked with `sorry` as the
  proof), else the error goes back for one retry.
- Claude Haiku, given only the Lean, says what it asserts in words, for the student to confirm.

### Checking the proof (`POST /lean/check`, `backend/app/leancheck.py`)
1. **Translate:** Claude Sonnet turns each confirmed line into Lean: structural tactics (intro,
   obtain, induction, use, …), the claims the line makes, and the hypotheses it cites as reasons.
   It writes no proofs; tactic lines that could prove something are rejected.
2. **Check:** one Lean file; every claim must be proved by fixed automation (omega, ring_nf,
   linarith, nlinarith, positivity, norm_num, simp_all, aesop) from the lines before it. A failed
   step is reported and Lean carries on; a marker after each item shows Lean reached it.
3. **Retry the translation** if Lean rejects its structure (never told which claims failed).
4. **Single-claim proofs:** a claim the automation can't prove gets a proof from the model, which
   Lean checks → "skipped step". Not after "suppose, for contradiction" (DECISIONS #28).
5. **Reasons:** a line that cites a reason must also follow with the other hypotheses cleared.
6. The result is cached by statement + lines, logged, and returned per line.

### Lean
- `backend/app/lean.py` runs `lake env lean` on a generated file inside **bwrap**: no network, no
  environment, read-only Mathlib, a scratch folder. A text filter rejects `sorry`, axioms and
  code execution; `#print axioms` confirms only the standard axioms.
- Lean 4 + Mathlib live in `lean/`; on Codespaces a copy in `/tmp/mellow-lean`
  (`MELLOW_LEAN_DIR`), because `/workspaces` thrashes (DECISIONS #26).

### The LLM layer (`backend/app/llm.py`)
Every LLM call goes through `structured_call`: cache → daily quota (per user: 3000 stroke
readings, 200 LLM readings, 300 Lean model calls) → streaming call → Pydantic + semantic
validation → one retry with the error → clean error. Every attempt is logged to SQLite; invalid
output is never returned or cached; prompts are part of the cache key.

### Models
| Job | Default |
|---|---|
| Handwriting (strokes) | MyScript |
| Image reader (fallback) | `deepseek-flash` |
| Lean: statements, translation, single-claim proofs | `claude-sonnet-5-5` |
| Lean statement read back in words | `claude-haiku-5-5` |

Set in `backend/.env`. DeepSeek's native API runs on servers in China: check the target
university's policy before real students use it.

### Where things are stored
- **Notebooks:** in the browser (tldraw + IndexedDB). Readings in the document's `meta`; cards
  are shapes.
- **Backend:** one SQLite file (`backend/mellow.sqlite3`) with the request log, cache and quotas.
- **User identity:** an anonymous per-browser id, until accounts in P4.

---

## 5. Repository layout

```
CLAUDE.md          working rules + non-negotiables
SPEC.md            product spec
DECISIONS.md       28 design decisions, one paragraph each
README.md          how to run
PROJECT.md         this file

frontend/          Vite + React + TypeScript + tldraw + KaTeX
  src/notebook/
    main.tsx        hash router: library ↔ notebook
    Library.tsx     notebook list; library.ts stores the index
    Notebook.tsx    tldraw setup, toolbar, top bar (pages, paper, readings)
    pages.tsx       A4 page shape, paper styles, page stack, camera limits
    ink.ts          raw stroke recorder (meta.ink)
    lasso.ts        lasso tool (75%-inside rule)
    lines.ts        groups strokes into lines
    readings.ts     live reading, readings store
    CheckUI.tsx     readings under lines, Check with Lean, the confirm box
    feedback.tsx    the card shape, placement, Copy button
    api.ts          calls to the backend (/api proxy)
  tests/            Playwright (Python): test_notebook, test_lean

backend/           FastAPI + Pydantic + SQLite
  app/
    main.py         /health, /recognize, /lean/statement, /lean/check
    leancheck.py    stating a problem, checking a proof line by line
    lean.py         running Lean sandboxed
    recognize.py    MyScript, Mathpix, LLM fallback, the hybrid
    llm.py          structured_call, DeepSeek + Claude providers, streaming
    config.py, store.py
  leaneval/         L0 (run.py: prove + state 25 problems), L0b (steps.py + steps.json: planted mistakes)
  bakeoff/          handwriting recognizer comparison
  tests/            pytest

lean/              Lean 4 + Mathlib project (its .lake build cache is git-ignored)
```

---

## 6. Running it

See [README.md](README.md). In short: `npm run dev` in `frontend/`; `uvicorn app.main:app
--port 8000` in `backend/`; keys (`ANTHROPIC_API_KEY`, reader keys) and `MELLOW_LEAN_DIR` in
`backend/.env`; on Codespaces `cp -a lean /tmp/mellow-lean` after every restart.

**Tests:**
```bash
cd backend && .venv/bin/python -m pytest -q                                   # never calls real APIs or Lean
~/.venvs/mellow-tests/bin/python frontend/tests/test_notebook.py /tmp/shots    # with both servers up
~/.venvs/mellow-tests/bin/python frontend/tests/test_lean.py /tmp/shots        # Lean endpoints faked
~/.venvs/mellow-tests/bin/python frontend/tests/test_lean.py /tmp/shots --real # real Lean + Claude
cd backend && LLM_TIMEOUT_S=120 MELLOW_LEAN_DIR=/tmp/mellow-lean .venv/bin/python -m leaneval.steps  # L0b
```

---

## 7. Design decisions

The full list is [DECISIONS.md](DECISIONS.md). The ones that shape the current app:
- **#6–#11** the notebook: freeform handwriting, tldraw, hybrid reading, raw ink, the lasso.
- **#13, #15** DeepSeek's native API, streaming (used for reading).
- **#25** Mellow is a lightweight Lean prover on the tablet.
- **#26** Mathlib lives on a local disk.
- **#27** the notebook is stripped to Check with Lean.
- **#28** how a line is judged.

---

## 8. Known shortcuts and limits

Where these live in code, they are marked `shortcut:`.

- **Proofs by contradiction** get only the automation's local reasoning, so steps that need
  Lean plumbing (casts between ℤ and ℝ, `Even p` as `p = r + r`) show "?": √2 is 0/6 in L0b.
  Upgrade path: a stronger local tactic set, or single-claim proofs limited to local facts.
- **"?" doesn't say why** the line failed (false, or a step too big for the automation).
  Upgrade path: try to prove the line's negation to tell "false" from "couldn't verify"; show the
  Lean goal as a hint.
- **Lean reloads Mathlib on every run** (~5 s). Upgrade path: a warm REPL (Kimina) for ~1 s.
- **No progress while checking**, only a seconds counter. Upgrade path: stream each line's
  verdict as Lean reaches it.
- **Which line is the problem** is a dropdown, defaulting to the first selected line.
- **The card is placed next to the strokes**, not the readings under them, so long typed
  readings can run under it.
- **Line grouping** uses geometry only; **words rule**: any 3+ letter run sends a line to the
  LLM reader.
- **Anonymous per-browser id** until accounts (P4).
- **Not checked on real hardware:** real pen input needs an iPad or pen device; the bake-off
  needs 30–50 recorded lines (the recorder was removed with #27; it's in git history).

---

## 9. What's next

### Lean results (2026-10-20, Claude Sonnet 5.5; reports in `backend/leaneval/results/`)
- **L0 (`leaneval/run.py`):** 25/25 problems proved, all first try, ~8 s each including Lean;
  25/25 model-written statements typecheck, and all 25 were reviewed as faithful (differences
  only in names and equivalent forms). DeepSeek V4 Pro proved 1 of its first 4, at up to
  10 min each.
- **L0b (`leaneval/steps.py`, `steps.json`):** 11 cases (10 problems + a wrong-reason case),
  each with a correct student-style proof and one with a planted mistake.

  | | v1 | v2 (current) |
  |---|---|---|
  | Correct lines ✓ | 32/44 | 42/48 (5 "skipped step"; all 6 misses are √2) |
  | Correct proofs fully ✓ | 5/10 | 10/11 |
  | Planted mistakes caught | 9/10 | 11/11 |
  | False ✓ | 0 | 0 |
  | Median per proof | ~10 s | 15 s |

  The planted mistakes were written by us, not by students, and translation faithfulness varies
  between runs: the two v2 runs before #28's last two rules let 1 and 2 mistakes through.

### Next steps
1. **Try it with real handwriting** on an iPad or pen device (needs the tldraw license key for
   the forwarded URL).
2. **Real student mistakes:** collect 20–30 and add them to `steps.json`; rerun L0b on each
   prompt change, several times, since translations vary.
3. **Tell "false" from "couldn't verify"** (§8), and stream verdicts.
4. **A warm Lean server** (Kimina) and an always-on host for user tests.
5. **User test #1:** 3–5 students, each working one proof.

### Future: a skill tree (logged 2026-10-20, not started)
A skill tree of the proof techniques and concepts a student has mastered, like achievements in
games: induction, contradiction, contrapositive, cases, ε–δ, injectivity, divisibility,
parity, … A technique lights up when Lean has checked proofs that use it. Lean makes this
possible, because the translated proof names the structure each student used
(`induction`, `by_contra`, `rcases`, which lemmas), so the tree can be built from checked work
rather than self-reports. It needs accounts and the learning log (P4) to keep progress across
devices.

### Waiting on you
- **A tldraw license key:** for the forwarded URL and iPad tests.
- **The direction questions in §10** (time, access to students, what success looks like).
- **Open questions (SPEC §11):** commercial or not (this decides the tldraw license), sign-in
  method, backend hosting (the Lean server needs an always-on ≥16 GB machine).

---

## 10. Direction and strategy (discussions, 2026-10-19)

### Goal
Research and community work to help students first; a startup if real pull shows up. Decide
the startup question with thresholds set in advance (below), not by feel.

### Market position
- **The gap:** on two axes, *gives answers ↔ builds understanding* and *unverified ↔
  verified*, answer engines (Photomath, Mathway, Wolfram) are verified but give answers;
  chatbot study modes (ChatGPT, Claude, Gemini) teach but are unverified; note apps (Apple Math
  Notes, GoodNotes) are handwriting-first but don't reason about proofs; Lean is rigorous but
  too steep for first-years. Nobody owns **verified + teaching + on your own handwritten page**
  for proofs.
- **Positioning:** for first-year students learning to write proofs, a notebook that responds
  to *your* reasoning: checks the algebra for real, hints instead of answering, shows *why* with
  checked pictures, and never passes a guess off as a verdict.
- **Beachhead:** intro-proofs and first-year analysis courses; later linear algebra, calculus,
  olympiad training.
- **Business model that fits community-first:** free (possibly open source) for students; paid
  for institutions (instructor dashboard from the learning log, enforced hints-only mode, LMS
  integration, privacy-compliant hosting).
- **Threats:** Apple/GoodNotes adding proof tutoring; OpenAI/Google adding handwriting +
  verification; a small niche. **Defensibility** comes from verification people can trust, data
  on which explanations help, instructor relationships, and a public benchmark, not from the UI.
- Competitor details above are from memory; verify with online research.

### Moat, models and RL
- Training a frontier model is out of reach, and a solo fine-tune is overtaken by the next base
  model. What lasts: **data** (which explanations worked), **evals** that define "good math
  teaching", **verifiers** (already built), and **distribution**.
- "Beauty" must be measurable before it can be trained: one key idea over brute force, the
  picture that makes it obvious, short and general, students report an "aha".
- **RL for good explanations:** rewards from most to least trustworthy: verifier gate
  (correctness), learning outcomes, simulated students (reward = the stuck student's next step
  checks out, minus a penalty for giving it away), human preferences (DPO), LLM judge (easy to
  game). Combined: correctness gate × preference score − length − answer leak. Methods: GRPO
  (TRL's GRPOTrainer), DPO; LoRA on a 7–14B open model costs roughly $50–500 per experiment.
- **Path:** reward v0 + calibrate a judge on ~50 ratings → best-of-N in the app (no training) →
  log which explanation students prefer → DPO → GRPO with the simulated-student hint loop.
- **Where you can contribute** (vs labs focused on capability): teaching, product, data and
  trust; a public benchmark for math *teaching*; checked visual proofs as an open format.

### Lean
- **Why:** check the logic, not just the algebra; explanations built from the proof itself
  (which lemma, which hypothesis); hints from the proof state (what's left to prove).
- **Risks and answers:** a mistranslated statement still gets a ✓ → a human-checked problem
  library plus back-translation confirmed in the confirm box; LLM Lean often fails to compile →
  compile-error retry loop; running model-written Lean is code execution → sandbox (bwrap now,
  a locked-down container in production).
- **Prior work to study:** Lean Verbose (Massot), Waterproof, the Natural Number Game,
  DeepSeek-Prover, Goedel-Prover, Kimina.
- **Hosting:** a warm Lean server (Kimina) on an always-on machine with ≥16 GB. Lambda is a poor
  fit (cold starts reload Mathlib). Rough cost: EC2 t3.xlarge ~$0.17/h (~$120/month always on),
  r6i.large ~$0.13/h, spot 60–70% cheaper; a Codespace 4-core ~$0.36/h only while working.
  Check student credits (GitHub Student Pack, AWS Educate). Prices are from memory; verify.

### Validating demand
1. Interview 10–15 students about the last time they were stuck on a proof (*The Mom Test*;
   ask about their problem, don't pitch).
2. Short clips of the picture proofs on r/learnmath, r/math, Discords, Shorts → waitlist
   (~10% of targeted visitors signing up is a good sign).
3. A landing page with one hook and a waitlist.
4. A 2–3 week pilot with 5–10 students: week-2 retention without reminders, what they press,
   "how would you feel if you couldn't use it?" (≥40% "very disappointed"), Mellow vs ChatGPT
   on the same stuck problem.
5. Ask for money or commitment: a founding-member price; an instructor agreeing to a pilot.
- Online research complements this (competitors, student complaints, research on AI hints)
  but can't show whether students would use *this* app.
- Usability first: a first-run sample page ("lasso this → press Hint") and a hosted URL.

### Set up early
- A professor to work with (credibility, **ethics approval** for studying students, a course
  for the pilot, a path to a paper).
- Consent and anonymous data; reconsider DeepSeek (servers in China) for institutions; Claude
  is wired in as the alternative.
- Licensing: AGPL protects against hosted copies if commercializing, MIT is most open;
  tldraw's hobby license is non-commercial.

### Startup signals (decide thresholds now)
≥40% of pilot students still using it in week 2 unprompted; an instructor asking to use it in a
course; students sharing it or asking to pay; strangers signing up from clips.

### The open decision
Too many directions at once (app, demand, benchmark, RL, Lean, images). Pick **one question
for the next 4–6 weeks** and park the rest.

**Settled in part (2026-10-20):** Lean is the core of the product, not a side experiment
(DECISIONS #25). The question for the next weeks becomes: *can Lean check a student's
handwritten proof line by line, reliably and fast enough?* (L0, L0b, then L1 in the app). Getting
it into one intro-proofs course stays the way to learn whether students use it. Still to answer:
1. Hours per week for the next two months?
2. Access to students in an intro-proofs course (as student, TA, or via a professor)?
3. In two months, which would make you proudest: (a) 20 students using it weekly, (b) a
   published benchmark or paper, (c) a model that writes beautiful proofs, (d) Lean checking
   students' proofs?
