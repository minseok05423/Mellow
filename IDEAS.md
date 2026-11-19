# Ideas

Ideas not yet built. Decisions that were made go in [DECISIONS.md](DECISIONS.md).

## 1. Re-check only what a fix touches
When a proof is wrong and the student changes the wrong part, nodes that don't depend on the changed part don't
need to be checked again. Only the changed nodes and the nodes that depend on them (through their premises) are
re-evaluated; every other verdict is kept.

Partly there already: each step's translation and each judge call are cached on that step's own text and premises
(DECISIONS #35). But a judge also sees the variables so far and the assumptions in force, so a fix elsewhere can still
change what it is asked.

## 2. A library of trivial statements
Keep a running library of "trivial" or "obvious" statements and lemmas. The app asks the student whether the
starting nodes (and only the starting nodes) are trivial. If the student accepts, the app saves the statement to the
library.
