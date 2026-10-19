# Mellow: everything in one place

This file is a single overview of the project as of 2026-10-19: what Mellow is, what has been
built, how it works, how to run and test it, and what's next. The detailed sources stay where
they are: product spec in [SPEC.md](SPEC.md), design decisions in [DECISIONS.md](DECISIONS.md),
working rules in [CLAUDE.md](CLAUDE.md), run instructions in [README.md](README.md).

---

## 1. What Mellow is

A GoodNotes-style handwriting notebook for university students doing intro proofs (first-year
proofs and analysis). It runs in the browser as a web app / PWA, with iPad + Apple Pencil as the
reference device.

Students write their own work on blank, lined or grid pages. When they **select** part of it,
they pick **Check**, **Hint**, **Answer** or **Explain with AI**, and the reply appears on the
page next to their work:

- **Algebra** is checked by SymPy, which gives a proven verdict: "Follows" or a counterexample.
- **Proof steps in words** go to an LLM, the reasoning model, labelled as the AI's.
- **Hint, Answer and Explain** on a problem build on one proof the model writes once, with its
  algebra checked by SymPy, kept on the server.

Later phases add animated explanations, interactive widgets, accounts and sync, PDFs, and
handwriting search.

**Core principle:** the student does the generative work; the board responds to *their*
reasoning. The assistant never acts on its own, and never judges a reading the student hasn't
confirmed.

### Non-negotiables

These are kept even in "minimal" mode:

- validation + retry of every LLM output
- caching
- logging
- SymPy verification for algebra
- API keys server-side only (LLM and handwriting vendors alike; the browser never calls a vendor)
- confirm-before-judge: no verdict on a reading the student hasn't confirmed

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

---

## 3. What works today

### Notebook (P1), at `http://localhost:5173/`
- **Notebook library:** create, rename, delete.
- **Pages:** A4 pages stacked vertically; blank, lined or grid paper; "+ Page" adds one at the end.
- **Tools:**
  - pen, highlighter, eraser
  - lasso: selects strokes that are at least 75% inside the loop
  - shapes, text, images
  - undo/redo, zoom
- **Autosave** in the browser (IndexedDB, one store per notebook).
- **Raw ink:** every pen stroke also stores its full-rate points with timestamps
  (`[dx, dy, pressure, dt]`) in the shape's `meta.ink`. It's captured from pointer events, and
  tldraw alone would drop the timing that recognizers need.
- **Bake-off samples:** lasso a line → *Save sample* → type the expected LaTeX. *Samples (n) ⤓*
  downloads them as JSON for the recognizer test.

### Read + Check (P2)
- **Line grouping:** strokes are grouped into lines by vertical overlap. Stacked pieces, like a
  fraction, are merged into one line.
- **Live reading:** about 1 s after you stop writing, each new line is read and shown faintly
  under it. Readings can be toggled with *Readings on/off*.
- **Four actions:** lasso one or more lines, then press **Check ✓**, **Hint**, **Answer** or
  **Explain with AI** (P3a).
  - The **confirm box** lists every selected line's reading for you to confirm or fix (✎ opens a
    LaTeX field with a live preview), plus **what the selection is** (proof steps, a problem or
    a concept) and, for steps, **the problem they work on**. Both come from the page's labels
    and can be changed. Check also shows the line above.
  - Only confirmed readings are ever judged.
- **Page labels:** after you pause writing, a cheap model labels every line on the page
  (problem, definition, claim, proof step, algebra, concept, note) and groups lines into
  described segments. They're kept with the notebook until the page's readings change. Any
  problem found starts solving in the background, so Hint/Answer/Explain on it are usually
  instant. A step on a new page finds its problem on the page before.
- **Check gives one card.**
  - On a problem or a concept: "nothing to check", with no request.
  - If the selection contains words, all its lines are treated as one block, and the reasoning
    model says holds / gap / error ("Check · AI, not verified"). It never gives the fix.
  - If it's all algebra, each line is checked by SymPy as a step. The results are combined:
    the most serious verdict, with each line's problem listed ("Line 2: …").
- **Card types:**
  - ✓ **Follows**: SymPy proved the step follows from the line above.
  - ✗ **Counterexample**: SymPy found values where the step fails, e.g. "Try a = 1, b = 1: the
    line above gives 4, but yours gives 2."
  - ? **Question**: algebra SymPy can't settle (template question).
  - ◇ **Check · AI, not verified**: the model's holds / gap / error on steps in words.
  - ? **Hint**: one question toward the next step.
  - ✦ **Answer · AI**: the proof (steps whose algebra SymPy checked are marked ✓), whether the
    selected steps hold and how to fix them, or a definition.
  - ❋ **Explanation · AI**: on a problem, an **animated board** that plays the checked proof one
    click at a time: equations draw in, rewrite themselves in place (shared symbols slide to their
    new spots), and the conclusion lights up; captions say what each step does, why, and whether
    SymPy checked it. Replay starts over; Copy gives the text. On steps or a concept, a text card.
  - • **Noted**: nothing to check, no reasoning model available, or no checked proof.
- **Cards** are ordinary page objects. Code places them in free space next to the selection
  (the LLM never picks positions). You can move or delete them, and they're saved with the
  notebook.
- **Solve once (Hint / Answer / Explain on a problem):** the model writes the proof as steps,
  each algebra claim tagged identity or derived and checked by SymPy; a counterexample goes back
  to the model for one retry. The checked proof is cached on the server, keyed by the problem, so
  only the first solve is slow (20–70 s with DeepSeek V4 Pro at high effort), and it usually
  runs in the background before any button is pressed. Hints use it as a
  hidden reference and follow the student's own route; it reaches the browser only through
  Answer or Explain. If no proof passes, Hint asks without one, and Answer/Explain say so.
- **Copy and paste:**
  - each card has a **Copy** button (text, with math as LaTeX)
  - card text can be highlighted with the mouse and copied with Ctrl/Cmd+C; math pastes as LaTeX
  - readings under lines can be highlighted with the select tool
  - text in the confirm box can be selected
  - copying a whole card (selected as a shape) gives its text

### Models menu
The student picks:
- the **handwriting reader**: MyScript, Mathpix, or none
- the **image reader**: vision models only
- the **reasoning model**

Prices are shown. Options whose keys aren't configured are disabled. The backend publishes the
catalog at `GET /api/models` and rejects any other choice. Choices are saved per browser.

### M1 ε–δ demo, at `http://localhost:5173/epsdelta.html`
Drag ε and watch δ respond. It becomes the first board widget in P3.

---

## 4. How it works

### Reading handwriting (`POST /recognize`)
1. The browser sends a line's strokes (with timing), a PNG of the line, and the confirmed lines
   above it as context.
2. The **stroke recognizer** reads it first. The default is MyScript's REST API
   (`/api/v4.0/iink/recognize`, HMAC-signed, solver turned off so it never hands out answers).
   Mathpix Strokes is the alternative.
3. The **LLM fallback** reads the line image instead (default DeepSeek Flash) when:
   - there's no reading,
   - confidence is low (< 0.8),
   - the line has prose, or
   - the reading looks like words spelled as single letters ("l e t f"). MyScript's math mode
     does that with words. Any run of 3+ letters counts as words.

   The fallback is told to transcribe exactly, mistakes included, and to write prose as
   `\text{…}`.
4. Results are cached by strokes + reader. Older letter-by-letter readings are re-read once when
   the notebook opens.

### Checking (`POST /check`)
**Request:** the confirmed line, the confirmed lines above, the confirmed problem (if known),
`confirmed: true` (required), and the reasoning model.

**A line counts as words** if it contains words other than joining words (so, thus, hence,
then, therefore, and, i.e., which, gives, implies), or a run of 3+ letters.
- Words go to the reasoning model (`structured_call`, below) at high thinking effort, which
  returns holds / gap / error with a reason.
- Hints (`/assist`) run at medium effort; a hint containing "correct", "wrong" or "well done"
  fails validation and is retried once.
- Word lines are never used as the line an algebra step is checked against.

**Algebra goes to SymPy:**
- LaTeX is parsed with `parse_latex`.
- **Equal or not:** two expressions are compared directly. If they differ, a counterexample
  search finds values where they disagree.
- **Equations:** the new equation must follow from the earlier one, checked by solving.
- **Special cases:**
  - Continuation lines ("= …") continue the previous expression.
  - Substitutions like "p = 2k" in the lines above are applied first.
  - An equation and a plain expression can't be compared, so the answer is a question,
    never a guess.
- Every check runs in a separate process with a 3 s timeout. The processes start from a
  preloaded server ("forkserver"), so one slow check can't hang the server.

**Every check is logged** (the start of the learning log).

### The LLM layer (`backend/app/llm.py`)
Every LLM call goes through `structured_call`, in this order:
1. Check the cache.
2. Check the daily quota (per user: 3000 stroke readings, 200 LLM readings, 50 reasoning calls,
   500 page labellings, 30 background solves).
3. Call the model, streaming.
4. Validate the output against its Pydantic schema, plus semantic checks.
5. If invalid, retry once, showing the model its error.
6. If it's still invalid, return a clean error.

Every attempt is logged to SQLite. Invalid output is never returned or cached. Prompts are part
of the cache key, so editing a prompt never serves old answers.

**Providers:**
- **DeepSeek** (native API, OpenAI format): JSON mode with the schema in the prompt; thinking
  depth mapped from the effort level; images only for `deepseek-flash`.
- **Claude** (Anthropic SDK): structured outputs; server-side fallback for Opus/Sonnet 5.5.

Models are routed by name: `deepseek-*` goes to DeepSeek, everything else to Claude.

### Default models

| Job | Default | Alternatives in the catalog |
|---|---|---|
| Handwriting (strokes) | MyScript (in your `.env`) | Mathpix, none |
| Image reader (fallback) | `deepseek-flash` | Claude Haiku/Sonnet/Opus 5.5 |
| Reasoning | `deepseek-v4-pro` | Claude Haiku/Sonnet/Opus 5.5 |

Privacy note: DeepSeek's native API runs on servers in China. Check the target university's
policy before real students use it.

### Where things are stored
- **Notebooks:** in the browser (tldraw + IndexedDB).
- **Readings:** in the notebook's document `meta`, written outside undo history.
- **Feedback cards:** shapes in the notebook.
- **Backend:** one SQLite file (`backend/mellow.sqlite3`) with the request log, cache and quota
  usage.
- **User identity:** an anonymous per-browser id, until accounts in P4.

---

## 5. Repository layout

```
CLAUDE.md          working rules + non-negotiables
SPEC.md            product spec v2 (vision, features, assistant, storage, LLM rules, P1–P6)
DECISIONS.md       18 design decisions, one paragraph each
README.md          how to run
PROJECT.md         this file
.claude/skills/    scene-json (scene format + validator), math-verify (SymPy step checker)

frontend/          Vite 8 + React 19 + TypeScript + tldraw 5.5 + KaTeX
  index.html                  the notebook app
  epsdelta.html               M1 ε–δ demo (anim.ts, plot.ts, epsdelta.ts)
  src/notebook/
    main.tsx        hash router: library ↔ notebook
    Library.tsx     notebook list; library.ts stores the index
    Notebook.tsx    tldraw setup, toolbar, top bar (pages, paper, readings, Models, samples)
    pages.tsx       A4 page shape, paper styles, page stack, camera limits
    ink.ts          raw stroke recorder (meta.ink)
    lasso.ts        lasso tool (75%-inside rule)
    lines.ts        groups strokes into lines
    readings.ts     live reading, readings store, re-read of letter-by-letter readings
    CheckUI.tsx     readings under lines, the four action buttons, confirm box, combining results
    structure.ts    page labels (roles + segments), background solving, finding an action's context
    scene.tsx       the animated explanation board: plays scene JSON with Manim-style motion
    feedback.tsx    feedback card shape, placement, Copy button
    models.ts       model catalog/choices
    samples.ts      bake-off sample recording/download
    api.ts          calls to the backend (/api proxy)
  tests/            Playwright end-to-end tests (Python): test_notebook, test_check, test_models, test_epsdelta

backend/           FastAPI + Pydantic + SymPy + SQLite
  app/
    main.py         routes: /health, /models, /recognize, /structure, /check, /assist (+ /scene stub → 501)
    config.py       settings from .env, daily caps, which models are configured
    llm.py          structured_call, DeepSeek + Claude providers, streaming
    recognize.py    MyScript, Mathpix, LLM fallback, the hybrid
    verify.py       SymPy checking (process + timeout), word detection
    check.py        /check: SymPy for algebra, the AI's holds / gap / error for words, templates
    assist.py       /assist: solve + SymPy-verify once, then hint / answer / explain
    structure.py    /structure: page labels from a cheap model
    scene.py        a checked proof -> scene JSON for the board
    catalog.py      model catalog with prices
    store.py        SQLite: request log, cache, quotas
  bakeoff/run.py    recognizer bake-off over recorded samples
  tests/            pytest suite (80 tests)
```

---

## 6. Running it

**Frontend** (only port 5173 needs forwarding):
```bash
cd frontend && npm install && npm run dev
```

**Backend** (Vite proxies `/api` to it):
```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8000
```

**Keys** go in `backend/.env` (git-ignored, never in `.env.example`):
- `DEEPSEEK_API_KEY`
- `MYSCRIPT_APP_KEY` / `MYSCRIPT_HMAC_KEY`
- optionally `MATHPIX_APP_ID` / `MATHPIX_APP_KEY` and `ANTHROPIC_API_KEY`

With no keys, everything still runs: readings are empty and you type them in the confirm box,
and word lines get "Noted".

**Opening it:**
- `http://localhost:5173` from VS Code on your machine, or the forwarded Codespaces URL.
- tldraw treats any HTTPS address that isn't localhost as production. The forwarded URL and an
  iPad therefore need `VITE_TLDRAW_LICENSE_KEY` in `frontend/.env.local`.

**Tests:**
```bash
cd backend && .venv/bin/python -m pytest -q                       # 80 pass, never call real APIs
python3 -m venv ~/.venvs/mellow-tests                             # one-time Playwright setup
~/.venvs/mellow-tests/bin/pip install -r frontend/tests/requirements.txt
~/.venvs/mellow-tests/bin/playwright install chromium
~/.venvs/mellow-tests/bin/python frontend/tests/test_check.py /tmp/shots   # with both servers up
```
All four end-to-end suites pass:
- **test_notebook:** P1 features
- **test_check:**
  - readings and the confirm box
  - SymPy cards
  - multi-line statements and one card per Check
  - letter-by-letter words
  - copy and paste
  - the four actions, page labels, problem lookup, Check on a problem
- **test_models:** the Models menu
- **test_epsdelta:** the M1 demo

**Recognizer bake-off:** record samples in the notebook, download them, then run:
```bash
cd backend && .venv/bin/python -m bakeoff.run ~/Downloads/mellow-samples-*.json
```

---

## 7. Design decisions (summary of DECISIONS.md)

1. **Web instead of native iOS:** no Mac on hand; tldraw works well in Safari.
2. **ε–δ step function has f(0) = ½:** shows the "no δ fits" case clearly.
3. **Show the largest working δ.**
4. **One forwarded port:** Vite proxies `/api` to the backend.
5. **Magnifier inset** instead of zooming the main ε–δ view.
6. **Freeform handwriting notebook,** not a cell document.
7. **Built on the tldraw SDK.**
8. **Hybrid reading:** stroke recognizer first, LLM fallback when needed.
9. **Accounts and server storage** (reverses spec v1).
10. **Raw ink lives in each stroke's `meta`,** so it shares the stroke's undo step.
11. **Lasso** selects strokes mostly (75%) inside the loop.
12. **Models are chosen by tests on our own data,** with defaults meanwhile.
13. **DeepSeek on its native API;** all LLM calls stream.
14. **SymPy checks** run in separate processes with a hard timeout.
15. **DeepSeek for every LLM call,** for now.
16. **Students choose their models** from a catalog the server controls.
17. **Proof steps in words:** they get a question from the reasoning model, and are never used
    as the line algebra is checked against. Lassoed lines give one card. Words read as single
    letters count as words.
18. **Hints or Answers:** a toolbar switch. Replaced by #19.
19. **Four selection actions** (Check, Hint, Answer, Explain with AI) replace the switch.
20. **A problem is solved once,** its algebra checked by SymPy (identities on their own, derived
    claims against earlier ones), and reused by Hint, Answer and Explain. Lean left out for now.
21. **Lines get roles and segments;** context is found by structure, not RAG (RAG waits for P5).
22. **Problems are solved in the background** once the page labels find them (own cap: 30/day).
23. **Animated explanations are built by code** from the checked proof (no extra LLM call).

---

## 8. Known shortcuts and limits

These are marked `shortcut:` in the code.

**Reading and checking:**
- **Line grouping** uses geometry only. Tall fractions or tightly packed lines can be split or
  merged wrongly. Upgrade path: use stroke timing.
- **Words rule:** any 3+ letter run counts as words. So a product like `abc`, or a plain `lcm`,
  is sent to the reasoning model instead of SymPy. Upgrade path: MyScript "Raw Content" mode,
  which separates words from math.
- **Template questions:** algebra SymPy can't settle still gets them in both modes. Upgrade
  path: the reasoning model words them in P3.
- **Not streamed yet:** proof questions and answers show "Checking…" for about 5–10 s.
- **Unverified logic:** SymPy checks only the algebra in a proof; logic steps (quantifiers,
  cases, named theorems) are marked by having no ✓. The AI's Check and Answer on steps can be
  wrong. Tuning belongs in the planned reasoning test.
- **Solves are slow (20–70 s), hidden by solving in the background** once the page is labelled
  after a pause. Pressing an action right after writing still waits (with a seconds counter).
  Upgrade path: stream progress into the card (P3b).
- **Problem lookup goes back one page.** Older problems: type them in the confirm box.
- **Function names:** only f, g, h (and F, G, H) are read as functions; any other letter before
  brackets, like n(n+1), is a product.

**Accounts and identity:**
- An **anonymous per-browser id** stands in for accounts until P4.
- Model choices are saved per browser.

**Not checked on real hardware or live services:**
- MyScript's request shape was confirmed with real keys in smoke tests. The bake-off hasn't
  been run yet; it needs 30–50 lines recorded on a pen device.
- Real pen input (pressure, Apple Pencil, palm rejection) needs an iPad or pen device.

**Small things:**
- Readings under lines can't be selected while the pen is active.
- The top bar wraps to two rows on narrower screens.

---

## 9. What's next

### Waiting on you
- **Bake-off samples:** record 30–50 handwritten lines on a pen device and run the bake-off.
- **Mathpix keys:** add them if you want Mathpix in the bake-off.
- **A tldraw license key:** for the forwarded URL and iPad tests.
- **Open questions (SPEC §11):**
  - Will Mellow be commercial? This decides the tldraw license.
  - Sign-in method.
  - Backend hosting.
  - Recognizer vendor pricing.

### Build order (SPEC §9)
- **P3b rest** (the animated Explain board is done):
  - the ε–δ widget on the page
  - the reasoning-model test
  - streaming text into cards
- **User test #1:** 3–5 students on iPads, each working one proof.
- **P4:** accounts, sync across devices, the learning log, and a progress view.
- **P5:** PDF import and annotation, PDF export.
- **P6:** handwriting search.
