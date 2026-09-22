---
name: lypning
description: Route Python one-liners through lypning instead of python3, and understand what it refuses. Load when about to run python from Bash in a repo where lypning is installed (`lypning -c`, `lypning run -c`, `lypning route -c`), when a command exited 90 or printed "unsupported:", when python startup cost matters, or when asked why a program went to CPython rather than the subset. Also load when working ON this package — the Rust subset, the classifier, the conformance battery, the differential fuzzer, the benchmarks (`lypning bench`, `lypning corpus-time`), the capture harness that grows the corpus, or anything under src/lypning/ or assets/rust/.
---

# lypning — the Coding Harness Interpreter Optimizer (a mixture of Pythons)

Reference: this file is the only one that ships with the skill. The rest —
`docs/LYPNING.md` (design), `docs/SUBSET.md` (the subset and the refusal
contract), `docs/COOKBOOK.md` (unsupported
Python, rewritten) — live in the **lypning repository**, not in the project
this skill was installed into. Do not go looking for them here unless this
*is* that repository.

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

The chain, cheapest first: **lypning** (the Rust core), **lypning-l** (the same
crate built with `collections` and `pathlib`), **cpython** — plus a classifier
that picks one per program and a dispatcher that recovers when the pick was
wrong.

**A refusal is exit `90`, one `<engine>: unsupported: <kind>: <detail>` line on
stderr, and nothing on stdout.** That is not a failure; it means "outside my
subset", and `lypning run` answers it by spawning the next tier. Any *other*
non-zero exit is the program's own and is returned unchanged — a dispatcher
that retried on exit 1 would run a half-completed program twice.

If `lypning status` says `not built`, everything routes to cpython and the
numbers below do not apply. `lypning build --rust` takes seconds and needs only
cargo. Missing optional variants are reported as `not built`.

**Never quote a remembered corpus size.** Capture grows it every session. Every
tool prints the count it loaded — quote that number, from that run.

## 1a. Writing python that stays on lypning

This section exists because it was measured to be missing. `docs/PROMPTING.md`
(the study of 2026-08-23) put 884 agent-written programs through nine prompt
treatments; this file, handed over verbatim, scored **81.7%** against **88.5%**
for prompts that said the following, and the gap was entirely programs that
reached for an import the subset does not have. What follows is the part of
that gap this file can close.

**The motive, which is most of the win.** The Rust core runs the program in the
dispatcher's own process — no second spawn, and most of a one-liner's cost is
the spawn. A program that leaves the subset does not cost a little more, it
costs everything: a wasted classification plus the full CPython price. So the
question to ask while typing is not "is this valid Python" but "does this need a
module". Over that study's battery of 26 realistic tasks (2026-08-23, measured
on the chain of that date) the framing moved the mixture from 0.470x of CPython
to 0.178x.

**Correctness outranks the route, always, and it is not close.** Never
approximate an answer to stay inside the subset, and never reimplement a
standard algorithm to avoid an import — the study has an agent that wrote 54
lines of SHA-256 by hand rather than `import hashlib`, to save about eleven
milliseconds. **The subset is a routing decision, not a challenge.** A fall-back
is free and always safe; a wrong answer is the one thing this project exists to
prevent.

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

If you must import: `collections` and `pathlib` land on **lypning-l**, the
larger variant of the same crate — one forked spawn, no CPython. Every other
module the core does not serve (`re`, `math`, `csv`, `hashlib`, `datetime`,
`struct`, `base64`, `textwrap`, `glob`, `statistics`, `time`, `urllib.parse`,
`zlib`, `subprocess`, `itertools`, `functools`, `argparse`, `unicodedata`) goes
straight to CPython. `lypning route -c '<program>'` says which, with the
construct that decided it; `os.path.<fn>()` calls stay on the core.

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
  exactly the surface nobody has typed at. `str.partition("")`,
  `round(-0.5, 0)`, `format(7, "10")` and `"日本".islower()` were all found this
  way, and all four were already "covered" by a green conformance run.

**Do not implement `re`.** It is the largest single gap and it is deliberate:
`import re` is a routing decision — it goes to cpython, and `conformance
--plan` ranks what that costs. If it is ever revisited, the shape is a small
backtracking engine that exits 90 on any syntax outside a measured subset —
the mixture pattern one level down.

## 5. The refusals are the design, not gaps to close

Three places lypning refuses rather than approximates. Each one, if "fixed" by
guessing, produces a *plausible wrong answer* — the one outcome that makes a
subset runtime worse than nothing:

- **i64 integers.** Python's are arbitrary precision. Every op is checked;
  overflow is `unsupported: bigint`. The chain then tries `lypning-l`, which
  refuses it the same way, and lands on cpython.
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

## 8. Honest scope

Roughly two-thirds of the corpus runs on the Rust subset and the mixture answers
all of it; both numbers move every session and both tools print the corpus size
they loaded. All of it is measured on a normal Linux filesystem.