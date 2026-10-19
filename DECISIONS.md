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
often doesn't compile, and running it needs a sandboxed service.

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
