---
name: lypning
description: Route Python one-liners through lypning instead of python3, and understand what it refuses. Load when about to run python from Bash in a repo where lypning is installed (`lypning -c`, `lypning run -c`, `lypning route -c`), when a command exited 90 or printed "unsupported:", when python startup cost matters, or when asked why a program went to CPython rather than the subset. Also load when working ON this package — the Rust subset, the MicroPython tier, the classifier, the conformance battery, the differential fuzzer, the benchmarks (`lypning bench`, `lypning corpus-time`), the capture harness that grows the corpus, or anything under src/lypning/ or assets/rust/.
---

# lypning — Mixture of Pythons

Reference: `MICROPYTHON.md` next to this file (the second tier's charter) is the
only one that ships with the skill. The rest — `docs/LYPNING.md` (design),
`docs/MICROPYTHON.md` (the cost model the second tier is built against),
`docs/COOKBOOK.md` (unsupported Python, rewritten) — live in the **lypning
repository**, not in the project this skill was installed into. Do not go
looking for them here unless this *is* that repository.

## 1. Using it

`lypning` IS an interpreter — interpreter mode is decided before argument
parsing, so anything that calls `python3` can call this instead.

```bash
lypning -c 'print(2**8)'          # exec the Rust core directly; exit 90 if it refuses
lypning run -c 'import re; ...'   # route, run, and fall through on exit 90 ONLY
lypning route -c 'import os'      # which tier would take it, and why — no execution
lypning status                    # what is built, wired and captured
lypning doctor                    # the same with an opinion; non-zero on any FAIL
```

Three interpreters, cheapest first — **lypning** (Rust subset), **lypning-mp**
(MicroPython variant), **cpython** — plus a classifier that picks one per
program and a dispatcher that recovers when the pick was wrong.

**A refusal is exit `90`, one `<engine>: unsupported: <kind>: <detail>` line on
stderr, and nothing on stdout.** That is not a failure; it means "outside my
subset", and `lypning run` answers it by spawning the next tier. Any *other*
non-zero exit is the program's own and is returned unchanged — a dispatcher
that retried on exit 1 would run a half-completed program twice.

If `lypning status` says `not built`, everything routes to cpython and the
numbers below do not apply. `lypning build --rust` takes seconds and needs only
cargo. The lypning-mp tier needs a 32-bit C toolchain and a network and is
**absent by default**; that is a status line, never an error.

**Never quote a remembered corpus size.** Capture grows it every session. Every
tool prints the count it loaded — quote that number, from that run.

## 1a. Writing python that stays on tier 1

This section exists because it was measured to be missing. `docs/PROMPTING.md`
put 884 agent-written programs through nine prompt treatments; this file, handed
over verbatim, scored **81.7%** against **88.5%** for prompts that said the
following, and the gap was entirely programs that reached for an import the
subset does not have. What follows is the part of that gap this file can close.

**The motive, which is most of the win.** The fastest tier runs the program in
the dispatcher's own process — no second spawn, and about 96% of a one-liner's
cost is the spawn. A program that leaves the subset does not cost a little more,
it costs everything: a wasted classification plus the full CPython price. So the
question to ask while typing is not "is this valid Python" but "does this need a
module". Over a battery of 26 realistic tasks that one framing moved the mixture
from 0.470x of CPython to 0.178x.

**Correctness outranks the tier, always, and it is not close.** Never approximate
an answer to stay inside the subset, and never reimplement a standard algorithm
to avoid an import — the study has an agent that wrote 54 lines of SHA-256 by
hand rather than `import hashlib`, to save about eleven milliseconds. **The
subset is a routing decision, not a challenge.** A fall-back is free and always
safe; a wrong answer is the one thing this project exists to prevent.

**The rewrites that account for nearly all of it.** Each is an exact
substitution, not an approximation:

| instead of | write |
|---|---|
| `collections.Counter(xs)` | `d = {}` then `d[x] = d.get(x, 0) + 1` |
| `Counter(...).most_common(k)` | `sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:k]` |
| `defaultdict(list)` | `d.setdefault(k, []).append(v)` |
| `re.sub(r"\s+", " ", s)` | `" ".join(s.split())` |
| `re.findall(r"\d+", s)` | accumulate digit runs in a `for` over the characters |
| `import csv`, for simple rows | `line.split(",")` over `f.read().splitlines()` |
| `import pathlib` | `os.path.join` / `basename` / `splitext`, and `open` |
| `math.sqrt(n)` / `math.isqrt(n)` | `n ** 0.5` / an integer binary search |
| `import datetime` for durations | `divmod` on integer seconds |
| `dict.fromkeys(xs)` to dedupe | a `for` loop with a membership test |

And the three that no rewrite of an import can dodge, because they are decided
while the program runs and no parser can see them coming: **64-bit integers**
(anything past the signed range refuses — let it fall back), **set iteration
order** (`set(...)` and `len(set(...))` are fine; printing or iterating one is
refused, so `sorted(set(...))`), and **`os.listdir`**, whose order the filesystem
defines rather than Python.

If you must import, the cheap ones still land on lypning-mp rather than CPython:
`re`, `collections`, `math`, `csv`, `hashlib`, `datetime`, `random`, `struct`,
`base64`, `pathlib`, `textwrap`, `glob`, `statistics`, `time`, `urllib.parse`,
`zlib`. `subprocess`, `itertools`, `functools`, `argparse` and `unicodedata` go
straight to CPython.

**One caveat about `lypning route`, until it is fixed.** It reports every
`os.path.<fn>()` call as `cpython`, and the engine runs all of them on tier 1
anyway (`docs/LYPNING.md` §4). Do not rewrite `os.path` code to satisfy it.

## 2. The gates — they answer different questions, so run the ones that apply

```bash
lypning build --rust          # shape: static, 0 opens, and the refusal contract asserted
lypning conformance           # correctness: every tier + the mixture, graded against CPython
lypning conformance --plan    # what to build next: which refusal blocks the most programs
lypning fuzz                  # correctness on programs NOBODY typed — generated from the subset
lypning bench                 # cost, arm vs arm: what the mixture costs against CPython
lypning corpus-time           # cost, run vs run: did MY change speed the corpus up
lypning gate --compare        # bytes and file opens, against the real CPython
python3 -m pytest             # the unit half (`make test`)
```

Verdicts per engine: **MATCH**, **UNSUPPORTED** (exit 90 + one line on stderr —
this is coverage and the build order, not a failure), **MISMATCH** (always a
failure, must be zero).

**`conformance` grades the programs agents happened to type; `fuzz` generates
them.** The corpus is a sample, not a specification, so MISMATCH 0 over it is
evidence about lypning's surface only in proportion to how much of that surface
it touches. `lypning fuzz` draws from lypning's OWN builtin and method tables,
runs each program under CPython and under the engine, shrinks every
counterexample to a minimal program, and prints the seed that replays the run
whether or not anything failed. Exit 90 means the generator wandered outside the
subset: a refusal, never a finding.

**`bench` compares arms; `corpus-time` compares runs of ONE binary.** Reach for
`corpus-time` when you changed an engine and want to know whether the corpus got
faster — `--record before.json`, then `--baseline before.json`, and the diff is
taken over the entries both runs timed (the corpus grows, so that intersection
is printed rather than assumed). It disagrees with a microbenchmark routinely
and by an order of magnitude, because a corpus entry runs once and exits: its
cost is startup, parse and compile, not steady-state dispatch.

**A subset runtime that silently disagrees with CPython is worse than no
runtime at all**, because the agent that typed the one-liner will not notice. It
will notice a refusal. Never "fix" a MISMATCH by widening a capability table:
the table describes what the engine does, and editing it to describe what you
wish it did converts a loud failure into a silent one.

## 3. The routing score is asymmetric, and that is the point

| verdict | cost | budget |
|---|---|---|
| IDEAL | none | maximise |
| LATE | ran on a pricier engine than needed | tune down |
| WASTED | engine refused; one extra spawn | acceptable |
| **UNSAFE** | routed to an engine that MISMATCHES | **the thing that must not happen** |
| NO-ENGINE | nothing matched | not the router's fault |

A wrong route costs a spawn; a wrong answer costs the user.

**The routing score grades the FIRST guess. The mixture arm grades what the
caller actually gets.** Both are reported; they disagree exactly where the
dispatcher's fall-through earns its keep.

## 4. Adding capability to lypning

`--plan` ranks every blocker by how many corpus programs it unblocks. That is
the build order, and the counts shift after each addition because an entry is
only ever blocked by the first thing it hits — so re-run it.

- A **builtin** goes in `assets/rust/src/builtins.rs` AND in the `BUILTINS`
  table, because `route.rs` reads that table to decide statically whether
  lypning could run a program.
- A **method** goes in `methods.rs` AND its type's table, for the same reason.
- A **module attribute** goes in `modules.rs::get_attr` + `call_module_method`
  + the `interned` name list — three places, and missing the third gives
  `unsupported: module-attr` for something that is implemented.
- After ANY of these, re-run `lypning conformance`. Coverage going up is the
  point; MISMATCH going above zero cancels the change. Then run `lypning fuzz`:
  the corpus only exercises what someone typed, and a new builtin or method is
  exactly the surface nobody has typed at yet. `str.partition("")`,
  `round(-0.5, 0)`, `format(7, "10")` and `"日本".islower()` were all found this
  way, and all four were already "covered" by a green conformance run.

**Do not implement `re`.** It is the largest single gap and it is deliberate:
lypning-mp has a regex engine, so `import re` is a routing decision. If it is
ever revisited, the shape is a small backtracking engine that exits 90 on any
syntax outside a measured subset — the mixture pattern one level down.

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

Falling back is only safe if a refused run left nothing behind. So a lypning run
is transactional: stdout/stderr buffered, file writes and deletes staged, all of
it flushed on success and **discarded on exit 90**.

Two rules that are easy to break:

- **The barrier must be invisible to the program.** A read consults the staged
  writes first, so `open(p,'w').write(x)` then `open(p).read()` behaves as in
  CPython. `os.path.exists/getsize/isfile`, `os.remove` and `os.rename` all see
  the overlay. This was NOT true in the first version and eight corpus programs
  caught it.
- **A consumed pipe cannot be rewound.** If lypning read stdin before refusing,
  the dispatcher forks and replays the captured bytes instead of exec'ing.

## 7. Traps already paid for

- **Static musl is a precondition, not a preference.** glibc-dynamic: 1.33 ms
  startup, 5 file opens. musl-static: 0.24 ms, 0 opens. The dynamic loader's
  five opens are the ENTIRE gap, and measuring the wrong binary understates
  lypning by more than everything else the benchmark varies. `lypning build
  --rust` defaults to musl for exactly this reason; `--target host` builds the
  dynamically linked control and **installs it under the same name**, so put
  the musl build back before quoting anything.
- **`Command::output()` defaults stdin to /dev/null.** The forked lypning-mp
  tier silently answered about an empty stream, and every `stdin → transform →
  stdout` one-liner — the corpus's largest cluster — got the wrong answer at
  exit 0. Only the end-to-end mixture arm could see it; per-engine conformance
  could not.
- **`sys.argv` is not the process argv.** `python -c PROG a b` gives
  `['-c','a','b']`; `python f.py a b` gives `['f.py','a','b']`; and under
  `lypning run` the dispatcher's own subcommand must not appear. Three separate
  bugs, all in the same six lines.
- **An engine's `MemoryError` is not the program's answer.** lypning-mp's heap
  is a fraction of CPython's, so `json.load` on a 4 MB file dies there and
  succeeds under python3 — with a *non-zero* exit and a traceback, which looks
  exactly like a program that legitimately raised. The chain treats MemoryError
  as a refusal; it deliberately does NOT treat an ordinary non-zero traceback
  that way, because re-running would execute the program's side effects twice.
- **`opt-level = "z"` saves 57,344 B and zero CheerpX blocks.** Cold cost is a
  step function in 131,072 B device blocks (`MICROPYTHON.md` §2d), so a saving
  that crosses no boundary streams the same number of fetches. Check the block
  count, not the byte count.
- **RUNNING THE CORPUS CAN REWRITE THIS REPOSITORY.** The corpus is harvested
  from real agent sessions, so it is full of programs that edit `src/`, `docs/`
  and the skills. Every entry gets its own temp cwd, entries naming an absolute
  path are skipped rather than run, and both `lypning conformance` and `lypning
  bench` bracket the run with a `git status` snapshot that restores and reports
  anything that changed anyway — and fails the run when it did. That is a
  **net, not a sandbox**: it cannot undo a write outside the repository. The
  first measurement runs of this project rewrote 34 tracked files, and the
  failure looked like "my change broke the suite" for a while before it looked
  like what it was. **`git status` before and after any corpus run, and never
  trust a suite result taken across one without checking.**
- **A benchmark total over different program sets is not a comparison.** An arm
  that refuses work looks faster the less it can do. `lypning bench` reports the
  SHARED subset (what every arm ran) and the whole corpus separately, and the
  second is the one that answers "what does a session cost".

## 8. Honest scope

Roughly two-thirds of the corpus runs on the Rust subset and the mixture answers
all of it; both numbers move every session and both tools print the corpus size
they loaded. All of it is measured on a normal Linux filesystem.

**The sandbox arm HAS been run** (2026-08-19, `docs/LYPNING.md` §8a), and it
does not say what the filesystem numbers imply. Two corrections came out of it.
Every published lypning figure had been measured with the **x86_64** binary,
which CheerpX — 32-bit x86 only — cannot load at all; `lypning build --rust
--target i686` is the one that runs there. And in the VM the two subsets are
within noise of each other on every probe, because a 50–85 ms exec round-trip
floor sits under both, while lypning costs **1.67x lypning-mp's bytes on first
touch** (1,280 KB vs 768 KB — 8 device blocks against 3). What the measurement
DOES support is the case for a subset at all: `python3 -c 'import json; …'`
takes **13.3 s** cold in that image against lypning-mp's 61 ms and lypning's
23 ms, and the exec ceiling that destroys the VM is 30 s. Do not argue lypning
over lypning-mp on sandbox speed; the evidence is not there. And cold VM boot
still dominates a sandbox turn regardless: 24.4 s of boot against 290 ms of
commands. This is a real but **secondary** term, and no user-facing copy should
say the sandbox is fast because of it.
