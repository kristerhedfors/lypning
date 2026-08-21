---
name: hillclimb
description: >-
  The lypning improvement loop: one small, measured, committed step at a time up
  three curves that pull against each other — raw speed on what the Rust core
  already runs, coverage of what it refuses, and the bytes and startup that
  paying for either costs. Load when asked to make lypning faster, to support
  more programs, to shrink the binary, to work through `conformance --plan`, to
  grow the corpus from this session's own python invocations, or to "keep
  going" / "hillclimb" / "run another iteration" on any of those. Carries the
  focus dial, the four gates and what each can and cannot see, the ledger, and
  the traps this loop has already paid for — including the one that matters
  most: the corpus benchmark is spawn-bound and CANNOT see a compute win, so a
  change accepted on it alone is a change accepted on noise.
---

# hillclimb — one measured step at a time

> **FOCUS: raw performance of functionality the Rust core already supports.**
>
> That is the dial's current setting. Coverage is taken only when it is nearly
> free; size is a constraint, not a target. §1 says how to re-aim it, and
> nothing else in this file changes when you do.

Read `CLAUDE.md` first — its nine invariants are binding and this loop does not
repeat them. Read `README.md` §4 for what each command is. This file is only
the loop: what to measure, what to change, what makes a step acceptable, and
what to write down.

The shape of every iteration is the same five moves. **Do not skip 1 or 5.**

```
1  RECORD the baseline    ← before touching anything. There is no "after"
                            without a "before", and rebuilding one costs a
                            stash and two minutes you will not want to spend.
2  PICK from the gradient  ← the instruments rank the work; you do not guess
3  CHANGE one thing        ← one mechanism, smallest version of it
4  GATE it                 ← all four, in order, on the binary you just built
5  RECORD the result       ← docs/HILLCLIMB.md, then commit
```

---

## 1. The three curves, and which instrument sees which

They are not independent. Coverage costs bytes; bytes cost startup; startup is
most of what a one-liner costs. A step that improves one and is silent on the
others is a good step. A step that improves one and quietly spends another is
how this project would get slowly worse while every number looked fine.

| curve | measured by | acceptance | what it CANNOT see |
|---|---|---|---|
| **speed** (the focus) | `lypning perf` | worst-ratio rows falling | anything about the corpus |
| **coverage** | `lypning conformance` | MATCH up, **MISMATCH 0** | whether it cost bytes |
| **cost** | `lypning gate`, the build's own byte count | bytes flat or down | whether it cost correctness |
| **the whole thing** | `lypning corpus-time --baseline` | **not slower** | a compute win — see §3 |

**To re-aim the loop**, change the FOCUS block at the top of this file to name
a different curve and say in one line why. Everything below stays true; only
§4's ordering of candidates changes. The three settings that make sense:

- **raw performance** *(current)* — work §4a. Take coverage only when a
  refusal is one `match` arm away.
- **coverage** — work `conformance --plan` top-down (§4b), and hold the byte
  budget: state the bytes each feature cost in the ledger entry.
- **cost** — work §4c. The honest opening move is measuring `opt-level`, and
  the honest second one is deleting something.

---

## 2. The five moves, as commands

Everything runs from the repo root. `PYTHONPATH=src` on purpose — in a checkout
that runs the tree you are editing rather than whatever wheel is installed.

```bash
export L="PYTHONPATH=src python3 -m lypning"
export B=.hillclimb            # baselines; gitignored, throwaway
mkdir -p $B
```

### Move 1 — record the baseline, BEFORE you edit

```bash
PYTHONPATH=src python3 -m lypning build --rust          # note the byte count it prints
PYTHONPATH=src python3 -m lypning perf --record $B/perf.json
PYTHONPATH=src python3 -m lypning corpus-time --repeat 3 --record $B/corpus.json
PYTHONPATH=src python3 -m lypning conformance --engine lypning   # the MATCH/UNSUPPORTED split
```

If you have already edited and have no baseline, build one rather than guessing:

```bash
git stash push src/lypning/assets/rust
(cd src/lypning/assets/rust && cargo build --release --target x86_64-unknown-linux-musl)
cp src/lypning/assets/rust/target/x86_64-unknown-linux-musl/release/lypning $B/lypning-base
git stash pop
PYTHONPATH=src python3 -m lypning corpus-time --engine $B/lypning-base --repeat 3 --record $B/corpus.json
```

`corpus-time` will warn that the baseline is a different *arm*. That warning is
correct in general and is the intended usage here — the arm is the same engine
one commit earlier, which is exactly what a speed change should be read against.

### Move 2 — pick from the gradient

```bash
PYTHONPATH=src python3 -m lypning perf                       # speed:    worst ratio first
PYTHONPATH=src python3 -m lypning conformance --engine lypning --plan   # coverage: most programs first
```

Neither is advice. `perf` sorts by how much slower lypning is than CPython at
something it *already does*; `--plan` sorts by how many corpus programs one
missing feature blocks. Take the top row you can do in one mechanism.

### Move 3 — change one thing

One mechanism per commit. Not one file, not one line — one *mechanism*, so that
when a gate goes red the thing that turned it red is not a guess. Rebuild with

```bash
PYTHONPATH=src python3 -m lypning build --rust
```

which is also the command that asserts the exit-90 contract on the binary that
was just produced (invariant 2). `cargo build` alone does not, so do not
substitute it for the final build of a step.

### Move 4 — the four gates, in this order

```bash
PYTHONPATH=src python3 -m lypning build --rust      # 1. builds AND asserts the refusal contract
PYTHONPATH=src python3 -m lypning conformance --engine lypning   # 2. MISMATCH must be 0
PYTHONPATH=src python3 -m lypning perf --baseline $B/perf.json   # 3. the focus curve
PYTHONPATH=src python3 -m lypning corpus-time --repeat 3 --baseline $B/corpus.json  # 4. no regression
python3 -m pytest -q                                # the Python side, which the four do not cover
PYTHONPATH=src python3 -m lypning doctor            # 0 FAIL
git status --short                                  # invariant 4: check it yourself
```

The order is not cosmetic. **Correctness before speed**: a `perf` table for a
binary that mismatches is a measurement of the wrong program, and reading it
first is how you spend an hour being pleased about it.

Gate 4 is the one to read carefully, and §3 is about it.

### Move 5 — record, then commit

Append an entry to `docs/HILLCLIMB.md` — newest first, at the marker. It takes
one minute and it is the only reason iteration 40 is not iteration 3 done
again. Then commit, with the four numbers in the message:

- binary bytes, before → after
- conformance MATCH / UNSUPPORTED / MISMATCH
- the `perf` rows that moved, with their ratios
- the `corpus-time` before → after, **and whether that is inside noise**

Quote the corpus size the tool printed on *this* run (invariant 3). Never a
remembered one.

---

## 3. The trap that matters most

**`corpus-time` is spawn-bound and cannot see a compute win.**

Measured on this container: the corpus median is about 0.7 ms per program
against a startup of about 0.64 ms. So roughly nine tenths of what the corpus
benchmark measures is the operating system creating a process, and one tenth is
the interpreter doing anything at all. A change that makes the interpreter 16%
faster moves that total by about 1.6%, which is inside run-to-run noise.

This has three consequences and each of them is a way to be wrong:

1. **Do not accept a speed change on `corpus-time` alone.** It will say
   "unchanged" for a real win and for a change that did nothing, identically.
   Accept on `perf`, which subtracts startup and measures the construct.
2. **Do not dismiss a speed change because `corpus-time` did not move.** Being
   flat there is the *expected* shape of a compute win. What `corpus-time` is
   for is catching the opposite: a change that made the common case slower.
   Treat it as a regression gate with a ±1% deadband, not as the reward.
3. **Never claim a corpus number you did not get.** "16% faster" is a claim
   about `perf`'s suite, not about a session's one-liners. Say which.

And the strategic consequence, which the focus dial should be re-aimed on the
day it matters more than raw speed: **for the corpus total, coverage beats
interpreter speed by an order of magnitude.** Every program that moves from
UNSUPPORTED to MATCH stops a ~11 ms CPython spawn from happening in the mixture.
The refusals left in the corpus are worth seconds; making the interpreter twice
as fast at everything is worth tens of milliseconds. Both are worth doing. They
are not worth the same, and the ledger should not pretend they are.

### The other traps, each already paid for

- **`opt-level = "s"`.** The release profile is compiled for size, and the
  comment justifying it is about startup, not throughput. Changing it is a
  legitimate experiment and it is a *step*, with a ledger entry naming the byte
  cost — not a free win to slip into another change.
- **The binary size is a step function in 131,072 B device blocks.** A change
  that adds 400 bytes usually costs nothing; the one that crosses a block
  boundary costs a whole block of cold read. `lypning build --rust` prints the
  block count. Watch that column, not only the byte column.
- **Never widen a capability table to clear a MISMATCH** (invariant 1). The
  table describes what the engine does. Editing it to describe what you wish it
  did converts a loud failure into a silent one.
- **A partial module is a MISMATCH generator.** Before implementing any of
  `conformance --plan`'s module rows, write down the inputs where a naive
  version would *differ from CPython* instead of refusing — float repr, integer
  division, sort stability, exception message text, encoding edges. If you
  cannot bound that list, the feature is not ready; a refusal is always
  acceptable and a wrong answer never is.
- **A `perf` case that lypning refuses fails the run.** That is deliberate: the
  suite is a claim about what the subset covers. If a change narrows the subset,
  `perf` says so before `conformance` has to.
- **The corpus rewrites repositories** (invariant 4). `conformance`, `bench`,
  `corpus-time` and `perf` all run behind the net, and `git status` after a run
  that crashed mid-way is still your job.

---

## 4. Where the work is

### 4a. Speed — the current focus

Run `lypning perf` and take the top row. As of the run recorded in
`docs/HILLCLIMB.md`, the shape of the table was: string building and formatting
worst, function calls next, containers and the loop itself already within a
small factor. The mechanisms behind those rows, in rough order of payoff per
unit of risk:

- **Copying a whole container to reach part of one.** `Vec<char>` for one
  character, an index vector for a contiguous slice, a `Vec<String>` that is
  then copied again into `Rc<str>`. Look for `.collect()` on a path that only
  needs a range. *(Several of these are done; look for the ones that are not.)*
- **`s += t` in a loop is quadratic.** `Value::Str(Rc<str>)` cannot grow, so
  every append copies the whole string. CPython has an in-place path for a
  string with one reference. Fixing this properly means a growable string
  representation, which is the largest single change on this list and the
  largest single win — treat it as its own multi-step branch, not a step.
- **Per-call allocation.** A bound method allocates an `Rc<Value>` that is
  dropped immediately; a Python call builds a scope, a `Vec<bool>` and two more
  vectors before the body runs.
- **Name lookup.** Every read walks the scope chain hashing a string with
  SipHash, then linear-scans the builtin table. A `match` over `&'static str`
  and a cheap hasher (written here, not depended on — invariant 6) both apply.
- **Guards on scalar paths.** A recursion guard taken and dropped on every
  comparison and every dict-key hash, where only the recursive arms need it.
- **Iteration allocating per element.** Iterating a string collects a
  `Vec<char>` up front and allocates two heap objects per character.

### 4b. Coverage — taken when it is nearly free

`conformance --plan` ranks by programs unblocked. Read it as bytes-per-program
and not as programs alone: `re` unblocks the most and costs the most, and a
handful of small modules unblock two or three each for a few hundred bytes.

Refusals that must **stay** refusals, whatever the plan says: anything needing a
network or another process (`subprocess`, `http.server`, `threading`), anything
third-party (`PIL`), and anything whose output is not reproducible across two
interpreters (`os.listdir` order, set iteration order, addresses, timestamps).
Those are correct behaviour, not gaps — a rising UNSUPPORTED count is a coverage
number and a build order, never a regression.

### 4c. Cost — the constraint

`lypning gate` and the byte count `build --rust` prints. The rule is that a step
does not spend bytes silently: if the binary grew, the ledger entry says by how
much and what was bought.

---

## 5. Growing the corpus from this session

The corpus is the argument for every decision in this project, and it grows from
python invocations that coding agents actually type — including the ones this
loop itself types while measuring.

```bash
PYTHONPATH=src python3 -m lypning harvest --transcripts --dry-run   # what would be added
PYTHONPATH=src python3 -m lypning harvest --transcripts             # add it
PYTHONPATH=src python3 -m lypning corpus --stats                    # the new shape
```

`--transcripts` is the feed that works inside a session already in progress: the
`PreToolUse` hook is wired into `.claude/settings.json`, but hooks are read at
session start, so a session that wired them is not the session they capture.
The transcript scan reaches backwards and does not care.

Harvest **at the end of an iteration, not the start** — the corpus is what the
next conformance run is graded against, and changing the denominator in the
middle of a measurement makes the before and after incomparable. Then say the
new size in the ledger entry, from the run that printed it.

Everything is redacted before it is written and the files are committed. A
program that names an absolute path is kept in the corpus and skipped by the
runners, which is why `conformance` reports 763 of 842 and not 842 — that
difference is expected and is printed every time.

---

## 6. Stop conditions

Stop and say so, rather than taking another step, when:

- a gate is red and the fix is not obvious in one mechanism — report the red
  gate and what you tried, do not widen the change until it passes;
- `conformance` reports any MISMATCH — that is the one number that is never
  traded against anything;
- the next candidate on the gradient is a multi-step branch (the growable
  string representation is the standing example) — say so, propose the branch,
  and take a smaller step meanwhile;
- three consecutive iterations move no gate outside noise — the gradient is
  flat under the current focus, and the dial should be re-aimed (§1).

Otherwise the loop has no natural end: append the ledger entry, commit, and take
the next row.
