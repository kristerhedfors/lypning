---
name: lypning
description: Load when working on lypning — the Rust Python subset and the "Mixture of Pythons" router that picks between lypning, lypning-mp and CPython per one-liner. Covers what lypning implements and why exactly that (chosen from the harvested corpus, not the language reference), the three refusals that keep a subset honest (i64 integers, set iteration order, repr of non-ASCII), the commit barrier that makes falling back to another interpreter safe, the classifier and its asymmetric score (UNSAFE vs WASTED vs LATE), the dispatcher's in-process fast path, the four-arm benchmark and the two things it will mislead you about, and the traps already paid for. Also load for "make python faster in the sandbox", "route this one-liner", "why did lypning refuse", "add a builtin/method/module to lypning", or any change to lypning/, scripts/lypning-*, tests/.
---

# lypning — Mixture of Pythons

Full reference: `docs/LYPNING.md`. Sibling runtime: `docs/MICROPYTHON.md` and the
**lypning-mp** skill, whose cost model this project inherits wholesale.

## 1. The shape of the thing

Three interpreters, cheapest first — **lypning** (Rust subset), **lypning-mp**
(MicroPython variant), **CPython** — plus a classifier that picks one per
program, and a dispatcher that recovers when the pick was wrong.

The claim, measured over 472 harvested one-liners (2026-08-16):

```
              ran  refused   whole-corpus total   vs cpython
cpython       472        0          6987.9 ms       1.000x
lypning-mp        444       28          1153.1 ms       0.165x
lypning          324      148           673.3 ms       0.096x
mixture       472        0          1860.8 ms       0.266x
```

**Never quote a remembered corpus size.** The capture harness grows it every
session: this project measured 420 programs on the day it was written and the
merge that afternoon brought 472. Both tools print the count they loaded.

**lypning is the fastest engine on the work it accepts; the mixture is the only arm
that answers everything.** The other two are cheap because they refuse.

## 2. The three gates — run all three, they answer different questions

```bash
bash scripts/build-rust.sh                  # shape: static, 0 opens, contract smoke checks
node lypning conformance             # correctness: 3 engines + the mixture + routing
node lypning bench                 # cost: four arms, interleaved, min-of-repeats
node lypning conformance --plan      # what to build next
node --test tests/test_routing.py     # the unit half (in `npm test`)
```

Verdicts per engine are lypning-mp's three, verbatim: **MATCH**, **UNSUPPORTED**
(exit 90 + one line on stderr — this is coverage and the build order, not a
failure), **MISMATCH** (always a failure, must be zero).

**CI gates on lypning and on the mixture, not on lypning-mp.** lypning-mp's own divergences
belong to lypning-mp's runner; reporting them here makes a component failure look
like a failure of the mixture.

## 3. The routing score is asymmetric, and that is the point

| verdict | cost | budget |
|---|---|---|
| IDEAL | none | maximise |
| LATE | ran on a pricier engine than needed | tune down |
| WASTED | engine refused; one extra spawn | acceptable |
| **UNSAFE** | routed to an engine that MISMATCHES | **the thing that must not happen** |
| NO-ENGINE | nothing matched | not the router's fault |

Current: 93.0% ideal, 96.6% correct-on-first-try, 1 UNSAFE (which the dispatcher
recovers from). A wrong route costs a spawn; a wrong answer costs the user.

**The routing score grades the FIRST guess. The mixture arm grades what the
caller actually gets.** Both are reported; they disagree exactly where
`main.rs::fall_onward` earns its keep.

## 4. Adding capability to lypning

`--plan` ranks every blocker by how many corpus programs it unblocks. That is
the build order, and the counts shift after each addition because an entry is
only ever blocked by the first thing it hits — so re-run it.

- A **builtin** goes in `builtins.rs` AND in the `BUILTINS` table, because
  `route.rs` reads that table to decide statically whether lypning could run a
  program.
- A **method** goes in `methods.rs` AND its type's table, for the same reason.
- A **module attribute** goes in `modules.rs::get_attr` + `call_module_method`
  + the `interned` name list — three places, and missing the third gives
  `unsupported: module-attr` for something that is implemented.
- After ANY of these, re-run conformance. Coverage going up is the point;
  MISMATCH going above zero cancels the change.

**Do not implement `re`.** It is the largest single gap (66 programs, 14.0%) and
it is deliberate: lypning-mp has a regex engine, so `import re` is a routing
decision. If it is ever revisited, the shape is a small backtracking engine that
exits 90 on any syntax outside a measured subset — the mixture pattern one level
down.

## 5. The refusals are the design, not gaps to close

Three places lypning refuses rather than approximates. Each one, if "fixed" by
guessing, produces a *plausible wrong answer* — the one outcome that makes a
subset runtime worse than nothing:

- **i64 integers.** Python's are arbitrary precision. Every op is checked;
  overflow is `unsupported: bigint`. The dispatcher then hands the program to
  lypning-mp, which HAS bignums.
- **Set iteration order.** It falls out of CPython's hashing and no independent
  implementation reproduces it. Order-independent operations work; anything that
  would expose an order exits 90. Dicts have no such restriction — Python
  *defines* their order as insertion order, so it is reproducible and is
  reproduced.
- **`repr` of non-ASCII.** Deciding whether to escape needs CPython's Unicode
  category tables. A whitelist of unambiguously-printable blocks is allowed; the
  rest is refused.

## 6. The commit barrier — read this before touching `io.rs`

Falling back is only safe if a refused run left nothing behind. So a lypning run is
transactional: stdout/stderr buffered, file writes and deletes staged, all of it
flushed on success and **discarded on exit 90**.

Two rules that are easy to break:

- **The barrier must be invisible to the program.** A read consults the staged
  writes first, so `open(p,'w').write(x)` then `open(p).read()` behaves as in
  CPython. `os.path.exists/getsize/isfile`, `os.remove` and `os.rename` all see
  the overlay. This was NOT true in the first version and eight corpus programs
  caught it.
- **A consumed pipe cannot be rewound.** If lypning read stdin before refusing, the
  dispatcher forks and replays the captured bytes instead of exec'ing.

## 7. Traps already paid for

- **Static musl is a precondition, not a preference.** glibc-dynamic: 1.33 ms
  startup, 5 file opens. musl-static: 0.24 ms, 0 opens. The dynamic loader's
  five opens are the ENTIRE gap, and measuring the wrong binary understates lypning
  by more than everything else the benchmark varies. `engine_bin()` in both tools
  prefers the musl build for exactly this reason.
- **`Command::output()` defaults stdin to /dev/null.** The forked lypning-mp tier
  silently answered about an empty stream, and every `stdin → transform →
  stdout` one-liner — the corpus's largest cluster — got the wrong answer at
  exit 0. Only the end-to-end mixture arm could see it; per-engine conformance
  could not.
- **`sys.argv` is not the process argv.** `python -c PROG a b` gives
  `['-c','a','b']`; `python f.py a b` gives `['f.py','a','b']`; and under
  `lypning run` the dispatcher's own subcommand must not appear. Three separate
  bugs, all in the same six lines.
- **An engine's `MemoryError` is not the program's answer.** lypning-mp's heap is a
  fraction of CPython's, so `json.load` on a 4 MB file dies there and succeeds
  under python3 — with a *non-zero* exit and a traceback, which looks exactly
  like a program that legitimately raised. The chain treats MemoryError as a
  refusal; it deliberately does NOT treat an ordinary non-zero traceback that
  way, because re-running would execute the program's side effects twice.
- **`opt-level = "z"` saves 57,344 B and zero CheerpX blocks.** Cold cost is a
  step function in 131,072 B device blocks (the lypning-mp skill §2d), so a saving
  that crosses no boundary streams the same number of fetches. Check the block
  count, not the byte count.
- **RUNNING THE CORPUS CAN REWRITE THIS REPOSITORY.** The corpus is harvested
  from real agent sessions, so it is full of programs that edit `src/`, `docs/`
  and the skills. Per-entry temp cwds contain the relative paths; 17 entries
  carry an absolute one and 3 of those can write. The first measurement runs of
  this project rewrote 34 tracked files — a duplicated function landed in
  `src/pipeline-inputs.js` and broke 37 unrelated tests, and the failure looked
  like "my change broke the suite" for a while before it looked like what it
  was. Both tools now bracket the run with `repoDirtyList` / `reportRepoDamage`,
  which reports and restores anything that was not dirty beforehand and fails
  the run. **`git status` before and after any corpus run, and never trust a
  suite result taken across one without checking.**
- **A benchmark total over different program sets is not a comparison.** An arm
  that refuses work looks faster the less it can do. The bench reports the
  SHARED subset (what every arm ran) and the whole corpus separately, and the
  second is the one that answers "what does a session cost".

## 8. Honest scope

lypning answers 68.6% of the corpus and the mixture saves 73.4% of CPython's wall
clock over it. Both numbers are on a normal Linux filesystem.

**The sandbox arm HAS now been run** (2026-08-19, `docs/LYPNING.md` §8a), and it
does not say what the filesystem numbers imply. Two corrections came out of it.
Every published lypning figure had been measured with the **x86_64** binary, which
CheerpX — 32-bit x86 only — cannot load at all; the i686 build is the one that
runs there, and it now ships in the image. And in the VM the two subsets are
within noise of each other on every probe, because a 50–85 ms exec round-trip
floor sits under both, while lypning costs **1.67x lypning-mp's bytes on first touch**
(1,280 KB vs 768 KB — 8 device blocks against 3). What the measurement DOES
support is the case for a subset at all: `python3 -c 'import json; …'` takes
**13.3 s** cold in that image against lypning-mp's 61 ms and lypning's 23 ms, and the
exec ceiling that destroys the VM is 30 s. Do not argue lypning over lypning-mp on
sandbox speed; the evidence is not there. And cold VM boot still dominates a sandbox turn
regardless: 24.4 s of boot against 290 ms of commands. This is a real but
**secondary** term, and no user-facing copy should say the sandbox is fast
because of it.
