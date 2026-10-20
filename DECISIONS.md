# Decisions

One paragraph per non-obvious decision: what and why. Newest at the bottom.

## 1. Web instead of native iOS
Web instead of native iOS: no Mac, no iPad, idea not yet validated; scene JSON
schema carries over to a native renderer later.

## 2. Step function has f(0) = ½
M1 asks for a step function with a jump of 1 at x = 0 where "no δ fits when
ε < 0.5". With the ε-band centered on f(a), that threshold only comes out to ½
if f(a) sits at the midpoint of the jump, so f = 0 for x<0, ½ at 0, 1 for x>0.
(With f(0) = 1 the threshold would be ε ≤ 1, outside the slider range.) Because
the definition uses strict inequalities, ε = 0.5 exactly also has no δ; δ
exists iff ε > ½.

## 3. Show the *largest* working δ
For x² at a = 1 the board draws δ = √(1+ε) − 1, the largest δ that works, not
the textbook δ = min(1, ε/3). The largest δ makes the band touch the curve
exactly at the ε-band edge, which is the picture that explains *why* it works.
When no δ works, the board cycles a red candidate δ through 0.8, 0.4, … 0.025
with the escaping part of the graph in red, so "no δ fits" is visible rather
than just stated.

## 4. One forwarded port; Vite proxies the backend
The browser only talks to the Vite dev server (5173); `/api/*` is proxied to
FastAPI on 8000. One port to forward, no CORS setup, and it mirrors production,
where one origin serves the app and the API. Vite's `allowedHosts` includes
`.app.github.dev` because Codespaces port forwarding sends that Host header.

## 5. Magnifier inset instead of zooming the main view
At ε = 0.05, δ ≈ 0.025 is only a few pixels wide in the main view. Rather than
auto-zooming the camera (which would move the ε handle under the mouse mid-drag),
a fixed inset in the corner shows a magnified view around (a, f(a)), scaled to ε.
It hides itself when it wouldn't magnify by at least ~2×.

## 6. Freeform handwriting notebook, not a cell document
The product is a GoodNotes-style notebook where students write on pages and select
their work to get help, not a Jupyter-style list of cells. Cells were simpler to build
and easier for an LLM to generate. But the user's intent is students working in their
own handwriting on a blank page, where the board responds to whatever they write.
Spec v2 (SPEC.md) replaces the v1 milestones.

## 7. Build the notebook on the tldraw SDK
GoodNotes-level features (pressure ink, shapes, text, images, lasso, undo, zoom, and
later sync) would take months to write ourselves. tldraw provides them and uses
perfect-freehand for ink. Costs: it's an infinite canvas (paper pages are emulated with
locked page shapes, as in its PDF-editor example), it has no built-in lasso (we add
one), and production use needs a license key (free hobby key or trial for testing; a
paid license before charging anyone). To keep a later switch possible, our data
(stroke log, readings, scenes) lives in our own format, not only inside tldraw's records.

## 8. Hybrid handwriting reading: stroke recognizer first, LLM fallback
The device gives us strokes, not just pictures. Stroke-based math recognizers (Mathpix
Strokes, MyScript iink) are faster, more accurate per symbol, and cheaper than vision
LLMs on math expressions. They are fast enough to read live as the student writes.
Vision LLMs are better at mixed prose and math, multi-row layout, and using context
("the line above uses q, so this is q, not 9"). So the recognizer reads every line and
an LLM corrects only prose or low-confidence lines. The vendor and LLM are picked in a
test that replays the same recorded strokes to every candidate.

## 9. Accounts and server storage (reverses "accounts out of scope")
The user wants a lasting record of notes and progress, plus cross-device sync. That
needs sign-in and server storage; browser-only storage loses everything when site
data is cleared. The sync model is one student on several devices (versioned saves;
edits made on two devices while offline are both kept), not live collaboration.
Accounts arrive in P4, after the first user test.

## 10. Raw ink lives in each stroke shape's `meta`
The stroke log (SPEC §6) is stored on the tldraw draw shape itself (`meta.ink`), not
in a separate store. Points are relative to the shape's origin, with timestamps. It is
written in a before-change hook at the moment tldraw marks the stroke complete, so it
joins the same undo step. That way it moves, deletes, undoes, redoes, persists and
(later) syncs together with its stroke, with no second store to keep consistent.
Cost: it ties the raw data to tldraw's records. When we export to our own format we
read it back from `meta`. Shapes resized after writing are not rescaled yet (marked
`shortcut:` in samples.ts).

## 11. Lasso selects strokes that are mostly inside the loop
tldraw's example lasso only selects shapes that are entirely inside the loop.
Handwriting strokes often poke out of a hand-drawn loop (descenders, exponents), so
ours selects a shape when at least 75% of its points are inside, and never selects
the locked paper pages. After lassoing, it switches to the select tool so the
selection can be moved or resized right away.

## 12. Model choices are made by tests on our own data, with defaults meanwhile
Leaderboard math scores don't measure what Mellow needs. Reading has to cope with
*our students'* handwriting. Reasoning has to produce good questions, not give answers
away, and write valid scene JSON fast, while SymPy decides correctness. So each slot
gets a test on our own data (recorded strokes; real student mistakes), and a default
until the test runs: Mathpix for ink, Sonnet 5.5 for the vision fallback, Opus 5.5 for
reasoning. DeepSeek V4 Pro is a reasoning candidate because it's much cheaper and has
open weights. It would run on a US/EU host of those weights, never DeepSeek's own API,
so student data stays out of China.

## 13. DeepSeek via its native API; all LLM calls stream
The user chose DeepSeek's own API over a US/EU host of its open weights. Trade-off:
student context sent to it goes to servers in China. DeepSeek is a reasoning candidate
only: it takes no images, and its JSON mode doesn't enforce a schema, so our Pydantic
validation and one retry do the enforcing. Every LLM call streams, as the user asked;
it also protects against timeouts. Validation still runs on the complete output, and
the streaming callback sends a "reset" before a retry, so a screen showing tokens as
they arrive never keeps an answer that failed validation.

## 14. SymPy checks run in forked processes with a hard timeout
`simplify`/`solve` can hang, so each check runs in a child process that is killed
after 3 s (a timeout becomes "unverified", never a verdict). Children fork from a
forkserver with SymPy preloaded, not from the web server: forking the threaded server
directly can deadlock, and preloading keeps a check around 0.35–0.5 s.

## 15. DeepSeek for every LLM call, for now
The user chose DeepSeek for all LLM calls: `deepseek-flash` reads line images (the
vision fallback) and `deepseek-v4-pro` does the reasoning, both on DeepSeek's native
API. Both default to "thinking" mode. We map our effort levels onto it: thinking off
for live reading, where speed matters, and on with increasing depth for reasoning.
Caveats: DeepSeek labels Flash's image input experimental, and images are reportedly
billed at ≤384 tokens each, which may limit fine handwriting detail. The recognizer
test should tell. Claude stays wired in: setting a `claude-*` model id in `.env`
switches any call back without code changes.

## 16. Students choose their models, from a server-controlled catalog
The user wants students to decide which models the board uses. The backend publishes
a catalog: each recognizer and model, its price, whether it takes images, and whether
its key is configured. Requests may name a recognizer and a reader model, and the
backend rejects anything not in the catalog, unconfigured, or (for reading) unable to
take images. So the choice can't expose a key, pick an unknown model, or bypass the
caps. The cache key already includes the model, so switching never serves another
model's answer. Existing readings aren't redone when the choice changes; only newly
read lines use the new choice.

## 17. Proof steps in words get a question from the reasoning model

A line with words beyond joining words ("so", "hence", "thus") is a statement or a proof
step, like "Let f be continuous. Show…" or "hence p is even". SymPy can't judge it, so
/check sends it to the student's reasoning model (DeepSeek V4 Pro by default). The model
may only ask one short question about the weakest point. It never says right or wrong
and never gives the answer: SPEC §4 says steps SymPy can't verify get questions, never
verdicts. A reply that gives a verdict ("correct", "wrong", "well done") fails validation
and is retried once. With no reasoning model available, the card just says "Noted".
Prose lines are never used as the line an algebra step is checked against. Lassoing
several lines gives one card: words anywhere make the selection one block for the model;
pure math lines are each checked by SymPy and combined (the most serious verdict, with
each line's problem listed). MyScript's math mode reads words as single letters
("l e t f"), so a run of 3+ letters counts as words: the line goes to the LLM reader,
and old readings like that are re-read when the notebook opens.

## 18. Students choose hints or answers for proof steps in words (replaced by #19)

The notebook's toolbar has a Hints / Answers switch, saved per browser. Hints is the
default and keeps SPEC §4's rule: one guiding question, never a verdict. Answers was the
user's call (2026-10-19): the reasoning model says plainly whether the written step holds
and how to fix it, or writes a proof for a problem statement. That answer isn't checked
by SymPy and can be wrong, so its card says "Answer · AI, not verified" and never reuses
SymPy's "Follows" / "Counterexample". The model judges only what is written: a theorem
used without naming it or checking its hypotheses counts as a gap. Answers run at higher
thinking effort, and the two modes are cached separately. Algebra lines still get SymPy's
verdicts in both modes, and confirm-before-judge applies to both.

## 19. Four selection actions replace the Hints / Answers switch

Selecting work offers Check, Hint, Answer and Explain with AI (user's call, 2026-10-19).
The student picks the kind of help per selection instead of setting a mode for the whole
notebook, so the toolbar switch from #18 goes. Check judges only what is written: SymPy
for algebra, and for steps in words the reasoning model's holds / gap / error, labelled
"AI, not verified" like #18's answers. Hint keeps #17's rules (one question, no verdict).
Explain starts as a text card and gains an animated scene version in P3b; the user wants
both. What each action does depends on whether the selection is a problem, proof steps or
a concept (SPEC §4 table).

## 20. A problem is solved once, checked, and reused by Hint, Answer and Explain

Hints written without a solution are blind: the model asks about the "weakest point"
without knowing where the proof goes. So the reasoning model first writes the proof as
structured steps (prose plus the LaTeX claim, if any, tagged identity, derived or none).
SymPy checks an identity on its own (left side against right side, for all values) and a
derived claim against the earlier steps it cites (only those: in a proof by cases the
nearest earlier claim can belong to the other case, which gave a real false counterexample); untagged claims (new definitions like p = 2k)
are never checked, since they don't follow by algebra. The first real test showed why the
tag is needed: a proof made only of identities had nothing checked when each claim was
compared with the one before it. A counterexample goes back to the model for one retry, and an
answer that still fails is never used. The checked answer is stored server-side, keyed by
the problem, and only sent to the browser when the student asks for it. All three actions
build on the same answer, so they can't contradict each other and only the first press
pays for the solve. SymPy can't check logic (quantifiers, cases, named theorems), so each
step is marked "✓ checked by SymPy" or "not machine-checked". Lean would check the logic
too, but was left out for now: a mistranslated statement still gets a ✓, LLM-written Lean
often doesn't compile, and running it needs a sandboxed service. *(Reversed 2026-10-20 by #25: Lean is now the core.)*

## 21. Lines get roles and segments; context is found by structure, not RAG

Each line's reading gains a role (problem, definition, claim, proof step, algebra,
concept, note), and lines are grouped into segments with a one-line description. A cheap
model labels the whole page in one call once its lines are read and the student pauses
(and again when an action is pressed, if anything changed); labels are stored per page with the readings they were made from, so
any edit means a relabel (cached server-side by text). The confirm box shows what the
selection is and its problem, and the student can change both. Context lines (work,
definitions) may be unconfirmed readings for Hint and Explain, but an Answer, which is a
verdict, only uses confirmed ones. An action's context is then a
lookup (the segment's problem, its steps, earlier definitions) instead of "the 8 lines
above". RAG is deferred: a notebook's text fits in the prompt, structure finds the right
lines exactly where embedding search is fuzzy, and the model already knows intro proofs.
It pays off for course-specific material (lecture notes, numbered lemmas), which arrives
with PDF import in P5.

## 22. Problems are solved in the background as soon as the page labels find them

The first real solves took 20-70 s (DeepSeek V4 Pro, high effort), and a student who
presses Hint shouldn't wait that long. So once a page is read and the student pauses, it is
labelled, and each run of problem lines is sent to /presolve, which solves it in the
background and caches the checked proof. The solve is keyed by the problem text alone (not
the page's definitions), joined the same way the actions join it, so a later Hint, Answer or
Explain finds it. A press during a running solve waits for it instead of paying for a second
one (an in-process lock per problem). Presolving uses unconfirmed readings, which is fine
because nothing is shown; a corrected problem just solves again. It has its own daily cap
(30) so background work can't use up the student's 50 reasoning calls. The cost is one solve
per problem written, even if no button is pressed: about a cent each at DeepSeek's prices.

## 23. Animated explanations are built by code from the checked proof

Explain on a problem plays the proof on a dark board (3Blue1Brown style), one step per
click, with the step's words and why it holds as the caption. The scene JSON is built by
code from the proof SymPy already checked, not by another LLM call: the board can only show
claims the checker has seen (with "✓ checked by SymPy" where it passed), it is instant and
free, and positions come from our layout, never the model. A claim that rewrites the
previous one (same left side) becomes a `transform`, so an equation visibly evolves in
place; steps with nothing to draw join the next caption; the conclusion `highlight`s the
last line; a full board fades and starts again. The browser player (scene.tsx) does
Manim-style motion with anim.ts's `smooth` easing: `write` draws left to right, `transform`
moves the glyphs two equations share to their new places (matched by symbol, in reading
order) while the rest cross-fade, `highlight` pulses and turns yellow. Reduced-motion
settings skip the motion. LLM-written scenes (the /scene endpoint) can come later; they
would need the scene-json validator and SymPy checks on their algebra.

## 24. Pictures: the model draws a proof, SymPy checks the drawing

Explain on a problem or concept can be "a picture": a geometric, intuitive argument played on
the board (cut-and-rearrange areas, similar triangles, a semicircle for AM-GM, graphs). The
scene format gains a figure and two ops, `draw` (point, segment, polygon, circle, angle mark,
curve) and `move` (a piece slides and turns to new points), because the four text ops can't
show shapes; this is schema_version 2, and v1 scenes play as before. The model writes points
with coordinates in a few parameters (so the picture is true for every value, not one
drawing) and, per step, the claims its picture makes: equal lengths, angles, areas, or an
equation. In a child process with a time limit, SymPy computes every claim from the
coordinates: a false claim goes back to the model with the numbers; a claim that holds for
all parameter values is "✓ checked by SymPy", one that holds only at the drawn values is
"✓ true in this picture". A move must keep every side length (pieces never stretch), so a
rearrangement proof can't cheat. Code compiles the figure into the scene (numeric points,
view, curve samples, equation positions, label nudging); the model never places anything on
the page. Only the model's figure is cached and the scene is compiled per request, so layout
fixes reach old answers. Real tests (Pythagoras, odd numbers, triangle angles, AM-GM) all
passed the checker first time; each picture takes 2-3 minutes at high effort.

## 25. Mellow is a lightweight Lean prover on the tablet (reverses #20's "no Lean for now")

Lean becomes the core of checking, not a side experiment. The tablet stays a thin client: it records ink, shows
readings, the confirm box and cards; reading, LLM calls and Lean all run on a dedicated server, which keeps Mathlib
loaded and runs model-written Lean in a sandbox. The point is to check the student's *logic*, not just their algebra:
SymPy can only mark quantifiers, cases and named theorems "not machine-checked". The shape: the problem becomes a Lean
statement that the student confirms through a plain-words back-translation (or that comes from a human-checked problem
library), so a mistranslation can't earn a ✓; each confirmed line becomes a `have` that Lean's automation (ring,
linarith, omega, simp, ...) must prove from the lines before it, so a failure lands on the student's own line; hints
come from the remaining proof state. Verdicts are three: ✓ Lean proved the step, ✗ false (a counterexample from SymPy
or Lean), and ? couldn't verify, which never means "wrong", since automation or translation may be what failed. SymPy
stays as the fast first check for algebra and the counterexample finder. Two experiments gate the app work: L0 (can a
model prove and state the intro problems?) and L0b (does line-by-line checking pass correct student proofs and fail
planted mistakes, and how fast?).

## 26. Mathlib lives on a local disk, not on the Codespace's /workspaces

On this Codespace `/workspaces` is a loop-mounted disk image, and loading Mathlib (6 GB of build files, memory-mapped)
from it thrashed: one check took over 300 s even with 16 GB of memory, with Lean at ~12% CPU and the disk reading
300-500 MB/s. The same project copied to `/tmp` (a real local disk) takes ~50 s cold and ~5 s warm per check, so the
checker reads `MELLOW_LEAN_DIR` (default: the repo's `lean/`). `/tmp` is wiped when the Codespace stops, so the copy is
redone then. The sandbox now mounts its empty `/tmp` before binding the Lean project, which may live under it. A
production server keeps Mathlib on local SSD; ~5 s per check is the cost of reloading Mathlib each time, and a warm
Lean REPL (Kimina or leanprover-community/repl) removes most of it.

## 27. The notebook is stripped to one feature: Check with Lean

On 2026-10-20 the notebook was cut down to what a Lean check needs (the user's call: "only the lean+llm feature").
Kept: the library, pages, pen, eraser, lasso, select, hand, raw ink, live reading (pasted images too, since a pasted
problem sheet is a problem), the confirm box and cards. Removed: SymPy checking, Hint / Answer / Explain, animated
boards and pictures, page labels and background solving, the Models menu, the bake-off sample recorder, the ε–δ demo,
and shapes, text and arrows in the toolbar; they're in git history before this date. One action replaces the four:
select a problem and its proof, confirm the readings and which line is the problem, confirm what Lean will prove (a
second, cheaper model reads the Lean statement back in words, seeing only the Lean, so a mistranslation shows), then
Lean checks the proof line by line and one card shows each line's verdict. The CLAUDE.md non-negotiable "SymPy
verification" became "machine verification": Lean now gives every verdict, which keeps its point (no step is judged by
an LLM alone) with a stronger checker. Lean runs on Claude Sonnet (L0: 25/25 problems; DeepSeek V4 Pro 1 of 4), with
`LLM_TIMEOUT_S` raised to 120 s because Claude can think silently past 20 s; reading stays on its own models. Old
notebooks still open: old card verdicts stay valid, and old animated boards show as plain cards, since tldraw won't
load a document with an unknown shape type.

## 28. How a line is judged (rules L0b forced)

Each rule fixes a failure seen in L0b. A line counts as proved only once Lean prints a marker after it: a failing
top-level tactic can end the proof with no error on later lines, which first showed up as a false ✓. When the fixed
automation can't prove a claim, the model writes a proof of that claim alone and Lean checks it ("skipped step"):
a false claim has no proof, and this took correct lines from 37/48 to 48/48. But not after "suppose, for
contradiction": there the context is false by design, the model proved the false line "q² = 4r²" by finishing the
contradiction itself, so such lines get only the automation's local reasoning (√2 proofs now show "?" on steps it
can't do, the price of no false ✓). Assumptions keep the student's stated type (`intro x y (h : x = y)`), so Lean
rejects one that doesn't match the goal; the model had silently replaced a student's assumed conclusion with the right
hypothesis. Lean's errors go back to the model for a retry only when they're about the translation's structure, never
which claims failed or a mismatched assumption, so a retry can't "repair" the student. A cited reason ("since g is
injective") must work with the other hypotheses cleared, inside a block that leaves nothing behind. With these, L0b:
11/11 planted mistakes caught, 0 false ✓, 42/48 correct lines proved (all misses in √2).

## 29. Testers get the web app from EC2, not a native iPad app

Asked for an iPad version in Swift, Flutter or Rust that a friend can run, the web app served over HTTPS won: an iPad
can't install an app file handed to it (native needs an Apple Developer account and TestFlight or a registered
device), every native toolchain for iOS needs a Mac, which we don't have, and a native app would mean rebuilding the
tldraw canvas. The PWA is already the reference (SPEC §3, #1); Add to Home Screen gives it a full-screen icon. One EC2
t3.xlarge runs everything (16 GB because a Lean check holds ~5 GB of Mathlib and a check runs up to 4 at once), with
Caddy for HTTPS on an sslip.io name, so no domain is needed, and HTTPS is required anyway (`crypto.randomUUID` only
works on secure pages). A shared access code in the invite link guards the API, because the anonymous per-browser
caps alone let anyone with the URL spend the LLM budget by clearing storage. Revisit native only if Pencil latency in
Safari fails the user test.

## 30. Counterexamples Lean checks, and verdicts that never overclaim

All four reviewers in the 2026-10-20 product discussion (PROJECT.md §11) asked to tell "false" from "couldn't verify".
When the automation and the model both fail on a claim, the model may answer with values instead of a proof. We build
the statement ourselves from Lean's own printout of the context at that line (an error raised on purpose inside a
`True` block, which leaves nothing behind): `∃ variables, every hypothesis ∧ ¬ claim`. Lean must prove it with the
model's values, `#print axioms` must show no sorry, and inside the real proof every hypothesis as we re-typed it must
read back as the real one, so a weaker reprint can't make a true claim look false. The model only proposes; Lean
decides, as with every verdict. Values are shown bare ("fails for k = 1, m = 1": working out why is the learning, the
student said); at 0 the card asks "did you mean to rule that out?", because Lean's 1/0 = 0 and ℕ's 0 − 1 = 0 make
claims false there that a student meant to exclude. Nothing new runs inside a proof by contradiction. With it, the
card stops overclaiming: a step the model filled is a gap (⋯), never ✓ or "complete"; a ✓ resting on an unverified
line says so; and when the automation proves the whole theorem alone, the card says the size of the steps wasn't
judged. The context printout adds one Lean error per claim but no extra run or model call.

## 31. Lean checks the student's reason, not just the claim

L0d (PROJECT.md §11) showed the gap the instructor reviewer predicted: Lean asked "is this line true, given
everything above?", while a grader asks "does it follow from the reason given?". Subtle mistakes have true claims, so
8 of 11 passed (an LLM grading alone passed 0 of 33). Three changes, each keeping Lean as the judge:
(1) A line that gives a reason ("since", "because", "so", "hence") must follow from the facts that reason consists of,
with everything else removed (`clear * -`); the translator lists those facts (including the fact a reason is applied
to, and the line before a "so"), and like a claim, the check gets the model's proof or a counterexample when the
automation fails. Citing the end of a chain ("c = bm = (ak)m = a(km)", even continued on the next line) cites the
chain, found by matching each link's left side to the previous right side, not by line: in "P, so Q", a later "so"
draws on Q alone. (2) "Suppose X" is always an assumption (`intro (h : X)`), never a claim. (3) An intro Lean rejects
because the goal offers nothing there, or a different hypothesis, is the student's mistake ("not given": assuming the
conclusion, the next case of an induction, a variable before the choice that depends on it), unless only the
statement's own variables were re-introduced, which is the translation's slip and goes back for a retry. With (1),
L0d went from 3 to 7 of 11 caught; (2) and (3) are unmeasured (API credit ran out). What Lean still can't see: a
reason that is sufficient but not the student's argument (n³ − n = (n − 1)n(n + 1) does give 3 ∣ n³ − n, whatever the
student said about evenness), and a true claim reached by an invalid step (2xy ≤ x² + y² is true outright): those show
as a gap, "needs a step you didn't write", and the proof is never called complete.
