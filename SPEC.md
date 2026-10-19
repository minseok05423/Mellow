# Mellow — Product Spec v2 (DRAFT, for review)

Status: draft. Replaces the product sections of CLAUDE.md once approved.
Items marked **[default]** are my proposals where you haven't decided yet. Change them freely.

## 1. Product in one paragraph
A GoodNotes-style handwriting notebook for university students doing intro proofs.
Students write their own work on blank pages. When they **select** part of it, an
assistant can check the step, explain it with a Manim-style animation, give a hint,
or drop in an interactive visualization, drawn on the page next to their work.
Notes and a learning log are saved to their account and synced across devices.

Core principle (unchanged): the student does the generative work; the board
responds to *their* reasoning. The assistant never takes over the page.

## 2. Users and scope
- **Learner:** university students, first-year proofs and analysis.
- **Session start:** blank page, the student's own work (homework, lecture notes, practice).
- **Math the assistant must handle:**

  | Area | Checking | Typical feedback |
  |---|---|---|
  | Algebraic proof steps (√2, induction, identities) | SymPy verdicts | verified / counterexample |
  | Calculus computation (derivatives, integrals) | SymPy verdicts | verified / counterexample |
  | Sets, logic, quantifiers | partly SymPy (logic module) | verdict where provable, else questions |
  | Limits, ε–δ, continuity | rarely provable | questions + widgets (M1 ε–δ) |

- **Reference device:** iPad + Apple Pencil in Safari (installed as a PWA).
  Daily development uses any pen device or a mouse; an iPad is borrowed for user tests.

## 3. Notebook features (GoodNotes parity, MVP set)
**Core:**
- notebook library (create, rename, delete)
- pages in a vertical stack, with blank, lined or grid paper
- pen and highlighter with colours and widths, plus an eraser
- lasso to select, move, resize and recolour
- undo/redo, pinch zoom and pan, PDF export

**Also in the MVP:**
- PDF import & annotate (lecture slides, problem sheets)
- shapes (auto-straightened), text boxes, images
- handwriting search
- cross-device sync (needs accounts, §6)

**Out of the MVP:** real-time collaboration or sharing, audio recording, flashcards,
presentation mode, templates beyond blank/lined/grid, folders beyond one level,
a native iOS app, payments, teacher dashboards.

## 4. Assistant
**Trigger:** only when the student selects part of their work (lasso or tap on a
line) and picks an action. The assistant does nothing on its own. [default] A later
option is to offer to check automatically after a pause; off in the MVP.

**Selection menu:**
1. **Check this step.** Verifies the selection against the line above.
2. **Explain / animate.** An animated explanation of why it holds.
3. **Hint.** A question or partial step, never the full answer.
4. **Visualize.** Inserts a widget (e.g. ε–δ) when the selection names a concept
   we have one for. Starting widget library: ε–δ continuity (exists as M1).

**Reading handwriting (hybrid, continuous):**
- **Primary: a stroke-based math recognizer** (Mathpix Strokes or MyScript iink; chosen
  in the bake-off, §7). It reads the raw strokes, not an image, and runs *while the
  student writes*. The reading appears faintly under each line, with uncertain symbols
  marked (e.g. "q or 9?"). So by the time the student selects something, it has
  usually been read already.
- **Fallback: a vision LLM** for:
  - lines containing prose ("hence p is even"),
  - lines laid out over several rows,
  - any reading below the confidence threshold.
  It receives the recognizer's reading with per-symbol confidence, an image of the
  line, and the confirmed readings of the lines above. It returns corrected LaTeX.
  Context settles ambiguity: the line above uses q, so this is q, not 9.
- Lines are grouped from strokes by position and timing. Each line keeps its latest
  reading, stored with the notebook, so readings are never redone just to answer an
  action.
- **Why hybrid:** stroke recognizers are faster and more accurate per symbol on math
  expressions, and cheaper per line. LLMs are better at mixed prose, layout and
  context. Transcription is the main source of grading errors, so reading is where
  quality matters most.

**Pipeline for every action:**
1. Take the selection's current readings (stroke recognizer, plus the LLM fallback
   where needed), and the confirmed readings of the lines just above it as context.
2. **Confirm chip:** "I read: 2q² = p²  ✓ ✎". The student confirms or fixes the
   reading before anything is judged. Not optional: a misread must never become a
   false "wrong". Live readings make this a glance rather than a wait.
3. The action endpoint (`/check`, `/explain`, `/hint`, `/visualize`) runs.
   - SymPy verifies algebra.
   - Steps SymPy can't verify get question-style feedback, never verdicts (**Hints**, the default).
     A student who picks **Answers** gets the reasoning model's direct answer instead, labelled
     "AI, not verified" (DECISIONS #18).
4. The result is drawn as a **board object** on the page, placed in free space next
   to the selection by deterministic layout code (the LLM never picks coordinates).
   Board objects are ordinary page objects: the student can move or delete them,
   and they are saved and synced with the notes.

**Board objects:**
- **Animations:** play scene JSON (the 4 ops; see the scene-json skill). `pos` is
  relative to the object's box. They play on tap, can be replayed, and advance on click.
- **Widgets:** an interactive object with parameters, e.g.
  `{"widget":"epsdelta","f":"x^2","a":1}`.
- **Feedback notes:** a short verdict or question, plus a counterexample animation
  when a step is refuted.

## 5. Progress record
- **Notes:** autosaved continuously, with a version history (restore any earlier
  version of a page). [default] A snapshot every 5 minutes of active editing,
  kept for 30 days.
- **Learning log:** one entry per assistant action: who, which notebook and page,
  when, the action, the confirmed LaTeX, the verdict or feedback, and whether the
  student then changed their work.
- **Progress view:** per notebook, steps checked, mistakes found (with links back
  to the page), and hints used.
- **Assistant context:** each request includes a short summary of the student's
  recent relevant log entries (e.g. "has confused ⇒ and ⇔ twice this week").

## 6. Accounts, storage, sync
- **Accounts:** simple sign-in. [default] Use a managed auth provider (Google
  sign-in + email link) rather than writing our own. *Reverses "accounts: out of
  scope" from spec v1.*
- **Sync model:** one person on several devices, **not** live collaboration.
  - Local-first: the open notebook lives in the browser (IndexedDB), so writing
    works offline. Changes sync to the server when online.
  - Conflicts (edited on two devices while offline): keep both versions and
    flag them. Never silently overwrite.
  - [default] We don't use tldraw's real-time sync server; a simple save/load of
    versioned documents covers this model.
- **Server storage:** [default] Postgres for users, notebooks, versions and the
  learning log; file storage for PDFs and images (local disk in dev, S3-compatible
  in prod). The LLM request log lives in the same database.
- **Our own format:** notebooks are stored in a Mellow JSON format, converted to
  and from tldraw's format at the edge, so we are not locked into tldraw. It holds:
  - strokes as vector points `[x, y, pressure, t]`, at full precision
  - page objects, scene JSON, and line readings
- **Raw stroke log:** tldraw's pen shapes store only x, y and pressure, without
  timestamps and at reduced precision. Stroke recognizers and handwriting datasets
  (e.g. MathWriting) depend on timing. So we record every stroke ourselves from the
  pen events, alongside tldraw, and link it to the tldraw shape by id.

## 7. LLM handling (v1 rules kept, extended)
- **Endpoints:** `/recognize`, `/check`, `/explain`, `/hint`, `/visualize`, `/scene`.
  Each has its own system prompt and schema.
- **`/recognize` is hybrid:**
  - The backend calls the stroke recognizer with the line's strokes; the vendor
    key stays on the server, like the LLM key.
  - It calls the LLM fallback only for the cases in §4.
  - It returns LaTeX, per-symbol confidence, and which reader produced it.
  - [default] Live reading during writing goes through the backend too, with
    updates batched when the pen lifts. If that's too slow, the fallback is the
    vendor's live session mode with short-lived tokens from our backend. No
    permanent vendor key ever reaches the browser.
- **Validation:** structured output, then Pydantic validation including semantic
  checks. Retry once with the error appended; a second failure becomes a clean
  error. Never render invalid output.
- **Cache:** on hash(endpoint + input + model + schema_version). For `/recognize`,
  the input is a hash of the line's strokes, plus the hash of the context lines
  when the LLM fallback runs.
- **Timeout:** ~20 s. Live reading has its own target (§10).
- **Per-user daily caps,** split by cost class: [default]
  - stroke recognitions: generous, since they're cheap and frequent
  - LLM fallback reads: 200
  - reasoning calls (check/explain/hint/visualize): 50
- **Log:** every request, response, validation result and retry count, including
  which reader produced each reading.
- **Bake-off before the assistant is built:**
  - Record 30–50 real handwritten intro-proof lines **once, as strokes with
    timestamps**: math-only, mixed prose, and messy lines.
  - Replay the same strokes to every candidate: Mathpix Strokes, MyScript iink, and
    2–3 vision LLMs (given a rendered image of the line).
  - Pick the stroke recognizer and the fallback LLM by reading accuracy, then
    latency, then cost.
  - Measure how often the fallback is needed. If the stroke recognizer alone is
    already good enough on mixed lines, drop the fallback.
  - The backend hides each vendor behind one interface, so either can be swapped.
- **Model and vendor choices (decided 2026-10-19):**

  | Job | Candidates in the test | Used until results are in |
  |---|---|---|
  | Math ink (stroke recognizer) | Mathpix Strokes, MyScript iink | Mathpix |
  | Prose and mixed lines | Recognizer's own text output; vision LLM only when confidence is low | (same) |
  | Vision fallback LLM | DeepSeek Flash; Claude Haiku/Sonnet/Opus 5.5 if keys are added | **DeepSeek Flash** (`deepseek-flash`, vision marked experimental) |
  | Reasoning (check feedback, hint, explain, visualize, scene) | DeepSeek V4 Pro; Claude Opus/Sonnet 5.5 if keys are added | **DeepSeek V4 Pro** (`deepseek-v4-pro`), thinking depth set per action |

  *Updated 2026-10-19: DeepSeek for all LLM calls by default (DECISIONS #15). The student can
  change this in the notebook's **Models** menu (DECISIONS #16).*
- **The student chooses models** (Models menu): handwriting reader (MyScript, Mathpix, none),
  image reader (vision models only), and reasoning model. Prices are shown with each option.
  The backend publishes the catalog (`GET /models`), offers only models whose keys are
  configured, and rejects any other choice. Keys and daily caps stay on the server. The
  choice is saved per browser; it moves to the account in P4.

  - **The reasoning test:** 10–20 real student mistakes, each turned into a hint, an
    explanation and a scene by every candidate. Scored on: no wrong math, doesn't
    give the answer away, scene JSON valid first try, latency, cost.
  - **DeepSeek runs on DeepSeek's native API** (user's choice, 2026-10-19; this replaces
    the earlier US/EU-host plan). Student work sent to it goes to servers in China;
    check the target university's policy before real students use it.
- **Streaming:** every LLM call streams. The backend validates the complete output
  before anything is rendered or cached. Endpoints that show text as it arrives (from
  P3) forward tokens, plus a "reset" event before a retry so a failed attempt is
  cleared from the screen.
- **Handwriting search** needs a reading for every page. Most come free from live
  reading. [default] Lines never read live (e.g. written offline) are read in the
  background once the page has been idle, using the stroke recognizer.

**NON-NEGOTIABLES (unchanged):** validation + retry, caching, logging,
SymPy verification, API key server-side only. **Added:** confirm-before-judge.

## 8. Stack
- **Frontend:** React + TypeScript + Vite + **tldraw SDK**.
  - Pages are fixed-size frames in one canvas; the camera is constrained to the page column.
  - Board objects and widgets are custom tldraw shapes. Each renders our own canvas
    animation engine (`anim.ts` from M1), plus KaTeX.
  - pdf.js for importing PDFs; PDFs are exported by assembling per-page renders.
  - A stroke recorder runs alongside tldraw, writing the raw stroke log (§6).
- **Recognition vendor:** Mathpix or MyScript, chosen in the bake-off. A third-party
  dependency like tldraw: get pricing from both before the bake-off.
- **tldraw license:**
  - Development on `localhost` is free.
  - Any HTTPS non-localhost URL, **including the forwarded Codespaces URL**, needs a
    key. Use the free hobby key (non-commercial, watermark) or the 100-day trial
    during build and testing.
  - A commercial license (price on request) is needed before charging anyone.
- **Backend:** Python FastAPI, Pydantic, SymPy, SQLAlchemy over Postgres.

## 9. Build order (each phase ends with something usable)
1. **P1 Notebook basics.**
   - tldraw with the page stack and paper templates, pen, highlighter, eraser,
     lasso, shapes, text and images.
   - Autosave in the browser only. No accounts, no assistant.
   - The stroke recorder (§6), plus an export of recorded lines for the bake-off.
   - *Test:* write a page of math on an iPad through the forwarded URL, and record
     the bake-off lines on it.
2. **P2 Read + Check.**
   - The bake-off, using the recorded strokes.
   - Hybrid `/recognize` with live readings under each line.
   - Confirm chip, then `/check` with SymPy.
   - The backend gets validation, retry, cache and logging.
   - Feedback is drawn as a board object.
   - *Test:* the 5 known-wrong steps are caught, with zero false "wrong" verdicts.
3. **P3 Explain / Hint / Visualize.**
   - The scene renderer (old M2) as a custom shape; ε–δ ported as the first widget.
4. **User test #1:** 3–5 students on iPads, each working one proof with the
   assistant. Decide what to change *before* building P4–P6.
5. **P4 Accounts + sync + learning log + progress view.**
6. **P5 PDF import/annotate + PDF export.**
7. **P6 Handwriting search.**

The current M1 code carries over: `anim.ts` and `plot.ts` become the engine inside
board shapes, and the ε–δ demo becomes the first widget. The three open review
findings (check_step false verdict, validator crash, test location) get fixed in P2.

## 10. MVP success criteria [default]
- Recognition: ≥ 90% of bake-off lines read correctly without edits, measured
  separately for math-only and mixed-prose lines. Every misread can be fixed in the
  confirm chip in under 10 s.
- Live reading: a line's reading appears within about 1 s of the pen lifting.
- Check: zero false "wrong" verdicts on the fixture set, and all 5 known-wrong
  steps caught.
- Latency: half of assistant replies arrive within 4 s, and 9 in 10 within 8 s.
- Writing on iPad feels close enough to GoodNotes that test students don't
  complain about the pen.
- User test: at least 3 of 5 students use an assistant action unprompted, and say
  it helped them find or understand a mistake.

## 11. Open questions
1. Is this meant to become commercial? This decides when we need a paid tldraw
   license, and whether the hobby key is enough for now.
2. Sign-in: Google + email link OK, or does your target university use something else?
3. Hosting for the backend and database (decide before P4; not needed earlier).
4. Recognition vendor pricing. MyScript's isn't public, and Mathpix's digital-ink
   pricing needs confirming. Ask both before the bake-off; a quote that's too high
   rules a vendor out before testing.
