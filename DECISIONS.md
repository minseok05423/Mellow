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

## 32. The proof is checked as a graph of steps, each on its own; the LLM is the main checker (reverses #25's Lean-first)

The user's call (2026-10-21), after reviewing the research: Lean-first was the wrong foundation for checking student
proofs. Translation errors compound across steps, Mathlib's definitions don't match textbooks, and Lean rejects steps a
grader finds trivial (L0b's "?" on √2). Structured natural-language verification checks one step at a time
(Pseudo-Formalization), which is what this does. The student's confirmed lines become a graph (`app/graphcheck.py`):
the problem's givens, then each claim, assumption or choice, with its premises (the earlier steps it rests on), the
variables in the order they're introduced, and the goal's quantifier order, so "δ depends on x" is visible. The student
confirms the graph before anything is judged; it replaces confirming a Lean statement. Rules catch two mistakes without
a model: a name reused for a new object, and a choice that depends on a variable the goal quantifies later. Every other
step is checked alone by the cheapest method that works: Tested (SymPy evaluates its equations at random values; a
failing value is a counterexample), Proved (its Lean statement, with only its premises as hypotheses, falls to the
fixed automation; a failure is not a verdict), else Judged (three judges, a marking scheme, ✓ only when all three accept,
✗ only when none does, otherwise ?). Each verdict shows its tier, and the card says only Proved is certain. This changes
CLAUDE.md's non-negotiable "Lean checks every verdict" into "every verdict states how it was checked, and an LLM verdict
needs three unanimous judges". The biggest risk is judges being lenient (accepting a flawed step); unanimity, the
marking scheme and seeing one step at a time reduce it but don't remove it. Later, Lean's and SymPy's verdicts become
training labels for our own step verifier, and the confirmed graphs train the classifier that replaces the one model
call that makes the graph. `leancheck.py` stays for the L0 experiments.

## 33. Judges can answer ?, and a ? is the student's call

The user's call (2026-10-22), after L1g: two of its false alarms were one judge wanting "p² even ⇒ p even" proved,
where most graders accept it. A judge now answers ok, unsure or wrong, and says wrong only when the step is entirely
wrong (false, an inference that doesn't work, an assumption that isn't allowed); a gap a strict grader would ask
about is unsure. ✓ still needs all three to accept and ✗ needs all three to say wrong; any unsure, or a split, gives ?.
A ? is then not left as a dead end: before the card goes on the page, the student sees the whole graph with every
verdict and, under each ?, what the doubting judges said, and marks it Acceptable or Not acceptable. The decision is
the student's, so it never turns into a ✓: the card keeps the ? and says "you accepted it", the proof is never called
complete because of it, and the decisions are logged (`review` event), which shows how often students overrule a
doubt. This trades some of the judges' strictness for the student's judgement on exactly the steps where graders
differ, which is also where working out why is the learning. L1g's numbers predate it and need a rerun.

## 34. Structure refutes before the judges: Lean's `plausible`, SymPy on inequalities, and a circular-assumption rule

The user's call (2026-10-22): the graph check leaned too much on the judges, with priorities accuracy, then speed,
then token cost. In L1g the judges made 13 of the 22 catches and Lean never refuted anything: it could only prove.
Re-reading the run, the step statements the model writes for Lean were faithful almost everywhere, so they can carry
refutations too, and three zero-token checks now run before any judge sees a step. (1) In the same Lean file as the
proofs, `plausible` (in Mathlib) searches for values where every hypothesis holds and the claim fails; a find is ✗
Tested with the values. It only works on decidable statements over types it can sample (ℤ, ℕ, not ℝ) and can't test a
premise with ∃, which also keeps it off the one unfaithful pattern the audit found (a premise's "for some x, y" turned
into free variables). (2) SymPy's random values now test inequalities too ("3δ ≤ ε" with δ = min(1, ε/2) fails at
ε = 1), but only to refute: an inequality can fail in a region random values miss, so passing values never give ✓.
(3) A rule: an assumption that states what the proof concludes, and that the conclusion rests on, is ✗ (a converse
dressed as a proof). A refutation is worded "fails for these values, where the steps it rests on hold", because it
is about the step as the confirmed graph links it, which matters when the graph left out a premise. Left for later,
because they need the goal stated in Lean or a model call: the shape of cases and induction (which would also end the
case-split and scope false alarms), and refuting statements over abstract sets and functions (a model picks
concrete ones, Lean checks). Some mistakes only a judge can see, a true claim reached by a wrong reason.

## 35. A cheap model splits the proof into steps, a strong one links them, and each step is translated alone

The user's design (2026-10-22), for accuracy, then speed, then token cost. The graph used to come from one call. Now
Haiku (`CHUNK_MODEL`) splits the confirmed lines into numbered steps, and Opus (`GRAPH_MODEL`) reads the problem, the
lines and those steps, and writes only the links: premises, variables, the goal and its quantifier order. The split
puts each model where its mistakes get caught: a wrong chunk shows up in the student's own words at the confirm
step, but a missing premise looks right to its author, so the strong model makes the links. The translation call
(`forms`, the slowest stage at 78 s and 5,200 output tokens on DeepSeek) became one call per step, run in parallel.
The wait is now the slowest step, each step's translation is cached on its own text (an edited graph re-asks only the
steps it changed), and a translator that sees one step can't quietly fix it from context. It gets the proof's
variables, so a name keeps one domain. In the experiments Opus also plays the student's confirm step and revises the
graph (`leaneval/graph.py`, `revise`), since there is no student there. A student who can edit the graph in the app
is not built yet.

## 36. The graph says how the proof reaches its goal: one step, a chain, induction, cases or contradiction (its `conclusion` is replaced by blocks, #37)

The user's call (2026-10-22), after the Chunks→Links run: three correct proofs were left incomplete because no single
step states the goal. A chain of "iff"s for an arbitrary x proves a set equality, and an induction ends with "this is
the claim for n + 1", never with "for all n". Loosening "concludes" would have called those complete again, but also
an induction with no base case. Instead the linking model records the structure (`conclusion`: a method and its
steps), the student confirms it with the graph (each step is labelled "base case", "link 2 of the chain to the
goal", ...), and rules check its shape from the links at no token cost: the inductive step rests on a hypothesis the
base case doesn't, each case on an assumption of its own, each link of a chain on the one before, a contradiction on
an assumption. The proof is complete only when every step is ✓ and the shape holds. That the steps say the right
thing (the base case is P(0), the chain's ends are the goal's two sides, the cases cover everything) is still the
model's word confirmed by the student, as with one concluding step before; the judges' rule on assumptions covers
whether the case split and the induction hypothesis are allowed. Judging the structure itself is the upgrade.

## 37. The proof is grouped into nested blocks, which give steps a scope (replaces #36's `conclusion`)

The user's call (2026-10-22): a proof has structure throughout, not only where it ends. Four of L1g's six false alarms
came from a flat graph: no case splits (`strictmono_case`) and no scopes (`de_morgan_or`'s ⊇ half reuses x). Haiku,
which already splits the lines into steps, now also groups them into nested blocks: a subproof ("let x ∈ A … so
x ∈ B"), a proof by contradiction, cases holding a block per case, an induction holding its base case and inductive
step, and a chain. The student's words mark the blocks ("Case 1", "suppose, for contradiction", "base case"), so a
wrong block shows up in their own words at the confirm step, where the blocks are headers with the steps indented
under them; that's #35's reason for giving the split to the cheap model. Opus keeps the links and now says only
whether the proof reaches the goal (`reaches_goal`). Code then reads three things off the blocks, at no token cost.
(1) Scope: a step may rest on steps in its own block or the blocks around it, and on what a closed block shows (its
last step, or each part's last step for cases and induction), but not on a step inside a block that has ended
(✗, a rule); two introductions of a name clash only where both are in scope, so x can be reused in the next block.
(2) Shape, from the links: an induction has a base case and an inductive step that starts with and rests on its
hypothesis, a proof by cases has two or more cases each resting on its own assumption, a proof by contradiction rests
on what it assumes, a chain is unbroken; #36's rules, per block. (3) The judges see the assumptions of the blocks a
step sits in and where it sits ("inside the proof by contradiction"), instead of guessing from its premises alone.
What blocks don't settle: that the cases cover everything, that the base case is P(0), that the last steps state the
goal; those stay the models' word, confirmed by the student, until the structure is judged. A closed block's last step
counts as shown everywhere after it, which lets a case's result be used inside the next case. Lean doesn't use the
blocks yet: each step is still translated alone; building a Lean skeleton (`by_contra`, `rcases`, `induction`) from
the blocks is the next step for formalization. Unmeasured: L1g needs a rerun.

## 38. The step right after a block may rest on the assumption the block opened with (it discharges it)

L1g on the advanced cases (2026-10-23) flagged the closing step of three correct proofs as out of scope: "so $T$ is
injective" resting on "$T(u) = T(v)$" and "$u = v$", "so $p = q$" resting on "$p \neq q$" and the contradiction. That
is a discharge, and the linker is told to record it that way (#37's scope rule and the link prompt disagreed). Now the
step right after a block may also rest on an assumption one of its ended blocks opened with; any later step still may
not, and an intermediate step inside the block is still out of scope. Kept narrow on purpose (accuracy first): a
conclusion that discharges two blocks in a row (both halves of a set equality) still has the earlier one's assumption
flagged. Replayed on every stored graph: 7 proofs lose a scope ✗; on one wrong proof (la_trivial_kernel_injective) that
✗ sat on the planted line, so its catch now has to come from Lean or the judges. Rerun of all 34 cases: still 34/34
caught (that one as ? by the judges), 0 false ✓, and correct proofs complete went from 22 to 28 of 34 counting the
advanced cases' first run.

## 39. Lines are read only when the student checks them, and every model call goes to Anthropic

The user's call (2026-10-23). Live reading read every line about a second after the student stopped writing and showed
it faintly underneath. Now nothing is read while the student writes: Check my proof reads the selected lines that have
no usable reading, all at once, and the confirm box opens when they're read. Reading is needed only to judge, so
reading on demand spends calls only on lines that get checked and keeps the page free of half-right guesses; the cost
is a few seconds' wait before the confirm box, and a line's context is only the readings already stored above it, not
the rest of the selection (read in parallel). The same day the DeepSeek account ran out of credit, and every line
MyScript spelled letter by letter ("s h o w") failed with a 502, since DeepSeek Flash was the fallback reader (#15).
The fallback reader is now Claude Haiku (`claude-haiku-5-5`), so every model call in the app goes to Anthropic; the
DeepSeek provider stays in `llm.py` for a `deepseek-*` model id.

## 40. The student fixes the graph by hand, and adds screenshots with an Image button

The user's call (2026-10-23). The graph used to be a list ("[4] claim from [2], [3]") that the student could only
accept or send back ("Not what I meant"), so a single wrong link meant asking the models again. Now, after the
readings are confirmed, the graph opens full screen as boxes and arrows (React Flow, `GraphEditor.tsx`): the student
drags from one step to another to link them, drags an arrow's end to move it, and taps × to remove it. Linking two
steps always makes the later one rest on the earlier, whichever way the drag went, since premises must be earlier
steps; a given never gets premises. Only the links are editable: the steps and blocks stay the models' (a wrong step
is still "Not what I meant"), and each box names its block ("Case 1") instead of the list's headers. Every link the
student changed is logged with `check_started` (the model's premises → theirs), which is the training data the
linker's upgrade path needs (#35). The editor sits in `document.body`, outside tldraw's canvas, so tldraw's gestures
and shortcuts (Backspace deletes the selected ink) never see it, and removing is a button because an iPad has no
delete key. Screenshots: images on the page were already read as a line (by the fallback reader), but the only way
to add one was pasting, which is awkward on an iPad. An **Image** button opens the photo or file picker and puts the
image on the page selected, so Check my proof is one tap away. An image goes to the reader at up to twice its size
on the page but at most 1568 px on its long side: Claude scales larger images down anyway, and a big PNG can pass
the API's 5 MB image limit.

## 41. A step that introduces its own variables is never Tested

Found in the first real check after #39 (2026-10-23, a correct proof that √2 is irrational): "√2 = b/a with a, b ∈ ℤ"
got ✗ Tested, "false for a = 1, b = 1". The step says such a and b exist; the translator wrote it as an equation anyway
(its prompt says not to, for "for some" claims), and SymPy tested it as if it had to hold for every a and b. That is
the worst kind of false alarm, from the tier students are told to trust. Now code, not the prompt, keeps such a step
from being decided by values: when the linker records a variable introduced at the step, or its text says "for some",
"there is" or ∃, neither SymPy nor `plausible` decides it (values that break it refute nothing, values that pass prove
nothing); Lean may still prove it, which only happens when the statement is the ∃ it should be. It can only remove
verdicts, so it can't add a false ✓. None of the 12 Tested ✗ in the last full run (graph-20261023-050824-q-all) is
affected. Replayed, the √2 step goes to the judges, who answer ? (a ≠ 0, and the lowest terms the splitter had moved
into a step of its own). Also: each box in the graph editor (#40) shows, top right, the steps it rests on.

## 42. Variables keep the conditions they were chosen under, and judges reason before they decide

A friend's first check (2026-10-23, a correct proof that √2 is irrational) got three false ✗, all around new
variables. (1) "p, q ∈ ℤ, q ≠ 0, gcd(p, q) = 1, √2 = p/q", one line, was split into a choice and the claim √2 = p/q,
and the claim was tested at random p, q (✗ at p = -1, q = 1): #41 covered only the step that introduces the
variables. Now a claim on the same line as an introduction it rests on counts as part of it, and values never decide
it. (2) "gcd(p, q) = 2" was refuted at p = q = 0, ignoring the q ≠ 0 the student stated, because only the linked
steps reached the translator. Now the steps that chose or obtained a step's variables go to the translator as premises
too (`setup`): their conditions hold wherever the variables are in scope. Not an assumption's: inside a proof by
contradiction everything would then rest on a false premise, and the refutations there would vanish. (3) All three
judges answered "wrong" on "q² = 2k²" with reasons that ended "so the claim is actually correct": the schema asked
for the verdict before the reason, so they committed first and reasoned after. The reason now comes first, and the
verdict follows from it. Replayed: the friend's proof went from five flags to two (? on "p = 2k" from p² even, and a
correct ✗ on "gcd(p, q) = 2", where only gcd ≥ 2 follows); the user's own from five to three ?s. None of the 12
Tested ✗ in graph-20261023-050824-q-all is skipped by (1). Unmeasured: (2) and (3) change what the translator and
the judges see on every proof, so L1g needs a rerun.
