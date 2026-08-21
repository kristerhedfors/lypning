# The hillclimb ledger

Append-only, newest first. One entry per iteration of the loop in
`.claude/skills/hillclimb/SKILL.md` — the step that was taken, the four numbers
it moved, and, when it moved none of them, that too.

Entries where a change **did not work** are kept, and they are the most useful
rows in the file: they are what stops the same idea being re-proposed next
month with the same reasoning that failed the first time.

> **Every number here belongs to the run and the machine that produced it.**
> Do not quote one as a fact about yours. Each entry names its date, its host,
> the corpus size the tool printed, and the commit — re-run and quote your own
> (CLAUDE.md invariant 3). The corpus grows every session, so two entries are
> comparable only over the programs both runs timed, which `corpus-time`
> prints rather than assumes.

The four numbers, in the order an entry states them:

| | instrument | what it can see |
|---|---|---|
| **bytes** | `lypning build --rust` | the cost of everything else |
| **correctness** | `lypning conformance --engine lypning` | MATCH / UNSUPPORTED / **MISMATCH 0** |
| **speed** | `lypning perf` | the interpreter, startup subtracted |
| **corpus** | `lypning corpus-time --baseline` | a regression; **not** a compute win — see the skill §3 |

<!-- lypning-hillclimb: newest entry is inserted directly below this line -->

---

## 2026-08-21 · iteration 5 — `opt-level`, measured rather than argued (kept at `"s"`)

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 842 loaded, 763 timed

The release profile is compiled for size, and the comment justifying it is about
*startup*: 96% of a one-liner's cost is the OS spawning the process, so codegen
quality cannot buy what a smaller image can. That is an argument about spawn
cost, not about throughput, and this loop is aimed at throughput — so it was
worth an hour to check whether the argument still holds when the question
changes. It does.

Three builds, one line apart, everything else identical (`lto = true`,
`codegen-units = 1`, `panic = "abort"`, `strip = true`):

| `opt-level` | bytes | **device blocks** | perf TOTAL | startup | corpus-time |
|---|---:|---:|---:|---:|---:|
| **`"s"`** (kept) | **1,045,176** | **8** | **3.65x** | 0.63 ms | 543.6 ms |
| `2` | 1,172,152 | 9 | 3.70x | 0.60 ms | — |
| `3` | 1,184,440 | **10** | 3.51x | 0.64 ms | 537.3 ms |

`opt-level = 2` buys **nothing** — 3.70 against 3.65 is inside the ±3% band
three consecutive runs of one binary showed — and costs 126,976 bytes and a
device block.

`opt-level = 3` buys a real **4%** of compute, and costs 139,264 bytes and **two**
device blocks. A block is the unit a cold read streams in on the device this
project is sized for, so that is two extra blocks on every cold start to make the
interpreter 4% faster at the tenth of a corpus run that is not the spawn. The
corpus agrees it is not worth it: 537.3 ms against 543.6, which is 1.2% and
inside the deadband.

**Kept at `"s"`. Do not re-propose either without a new reason** — not "the
interpreter is a big match statement so surely inlining helps", which is the
reason this was tried. The measurement is above; take a new one on a different
machine if the machine is the new reason.

conformance was 500 / 263 / **0** on all three, as it must be — an optimisation
level that changed an answer would be a compiler bug and is worth knowing about.

---

## 2026-08-21 · iteration 4 — a MISMATCH found by chasing a speed row, and a speed change that bought nothing

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 842 loaded, 763 timed

Two results, and the negative one is the longer entry.

### The change that did not work

`str-repr` sat at the top of the queue (4.3x, 88% of corpus programs), so the
builtin call path was opened. `eval::lookup` reaches `builtins::builtin` on every
`print`, `len` and `range` — a builtin name is by construction a miss in every
scope — and that function linear-scanned 39 BUILTINS and then 24 EXCEPTIONS;
`call_builtin` then asked `is_exception_name` for a **third** pass before it
could dispatch. Three linear passes over ~70 static strings per call looks
exactly like a finding.

Converted all three to `binary_search`. Measured, on this container, min of 7:

| | before | after |
|---|---|---|
| `repr(i)` in a loop | 0.644 µs/call | 0.671 µs/call |
| `len(t)` in a loop | 0.298 µs/call | 0.303 µs/call |

**No gain**, at or below noise. The scan was never the cost: 39 short-string
compares that mostly differ in the first byte or in length are a few tens of
nanoseconds against a call that costs 650. The rest is elsewhere — the argument
`Vec`, the `String` → `Rc<str>` conversion on the result, the `Nest` guard, the
`match name` dispatch itself.

So it was **reverted**, and the ordering constraint it would have imposed on both
tables forever went with it. A change that buys nothing and costs a rule is worse
than no change. `tests/test_method_tables.py` still guards `methods.rs`, where
binary search did pay (37 entries, on every `.foo()`), and now carries a comment
saying why `builtins.rs` is not in it.

**Do not re-propose binary-searching BUILTINS or EXCEPTIONS.** The reasoning is
sound and the measurement says it does not matter.

### The MISMATCH it turned up on the way

Sorting `EXCEPTIONS` for that search needed `IOError` moved away from `OSError`,
which raised the question of how aliasing was handled at all. It was not, in one
direction:

```
raise OSError(…) / except IOError  →  caught          (agreed with CPython)
raise IOError(…) / except OSError  →  traceback, exit 1  (CPython: caught)
```

`IOError` and `EnvironmentError` are not *subclasses* of `OSError` in CPython —
they are the same class under three names. `exc_matches` had them on the clause
side only, so an `except OSError` did not catch a kind named `IOError`. That is a
wrong answer at a wrong exit code, which is what invariant 1 is about, and the
asymmetry is why it read as working from the direction anyone would test first.

Not in the corpus, so `conformance` never saw it. Fixed, and pinned in
`tests/test_semantics.py` — differentially, against the real CPython, like every
case in that file.

| | before | after |
|---|---|---|
| bytes | 1,045,176 (8 blocks) | 1,045,176 (8 blocks) |
| conformance | 500 / 263 / **0** | 500 / 263 / **0** |
| perf TOTAL | 3.78x | 3.65x (the reverted change is not in this) |
| corpus-time | 552.2 ms | 543.6 ms |
| a MISMATCH nobody had | present | **fixed** |

The `perf` and `corpus-time` rows moved less than the noise band between them —
nothing in the shipped diff is a speed change. They are recorded because a
regression gate that only gets read when it is green teaches nobody anything.

**The transferable lesson:** the speed queue is also a correctness search. Reading
a hot path closely enough to optimise it is reading it closely enough to find
what is wrong with it, and on this iteration the second thing was worth more than
the first.

---

## 2026-08-21 · iteration 3 — method dispatch, and the hasher underneath everything

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 842 loaded, 763 timed

Two changes to the same layer, both from the top of the weighted queue.

**Method dispatch.** `x.foo()` evaluated the attribute first, and `get_attr`
built a `Value::Bound` — an `Rc<Value>` heap-allocated for the receiver — purely
to hand it to `call_method` a few lines later and drop it. `Expr::Call` now
recognises an `Expr::Attr` callee whose base really has the method and calls it
directly. Module attributes and unbound methods (`str.upper`) still go through
`get_attr`, because both mean something different and that is where the
difference is decided. And `method_name` binary-searches its tables instead of
scanning them — `STR_METHODS` is 37 entries and the scan ran on every attribute.

**The hasher.** Every scope is a `HashMap<Rc<str>, Value>` and every name read
hashes a short identifier. std's default is SipHash-1-3 behind an OS-seeded
`RandomState`: a keyed MAC chosen to survive attacker-chosen keys arriving over
a network, doing setup and finalisation for a six-byte identifier in a process
with a step limit that exits in under a millisecond. Replaced with twenty lines
of FNV-1a (`hash.rs`, no dependency — invariant 6), used by scopes, the module
table, `Dict`'s index, `Set`'s index and the pending-file map.

This cannot change an answer: a `Dict` keeps insertion order in a `Vec` and uses
the map only as an index into it, and `Set` order is refused wherever it would
be observable.

| | before | after |
|---|---|---|
| bytes | 1,045,176 (8 blocks) | 1,045,176 (8 blocks) |
| conformance | 500 / 263 / **0** | 500 / 263 / **0** |
| perf TOTAL | 4.87x | **3.78x** |
| corpus-time | 552.2 ms | **533.1 ms (−3.5%)** |
| startup | 0.63 ms | 0.63 ms |

Rows that moved, `lypning perf`, min of 5, startup subtracted:

| case | corpus | before | after |
|---|---|---|---|
| `str-split` | 10% | 50.95x | **9.72x** |
| `call-recursive` | 8% | 34.21x | **21.96x** |
| `str-methods` | 38% | 6.66x | **5.12x** |
| `call-method` | 83% | 4.70x | **3.82x** |
| `loop-range` | 5% | 2.42x | **1.61x** |
| `list-index` | 20% | 2.64x | **1.85x** |
| `name-lookup` | 27% | 2.85x | **2.26x** |
| `dict-get` | 12% | 4.94x | **3.94x** |

**Two things worth knowing next time.**

*The size accounting is a step function and it nearly bit.* The method-dispatch
change alone added 4,096 bytes — 696 of them past the 1,048,576 B mark — which
took the binary from 8 device blocks to **9**. A block is the unit a cold read
streams in, so that is a real cost for a duplicated dispatch arm. The hasher
change gave the 4,096 back exactly (SipHash and `RandomState` leave the image
entirely), and the pair lands on the original byte count. Had it not, the
dispatch change would have been the one to drop.

*`str-split` at 47x → 9.7x came from the HASHER, not from anything in split.*
Nothing in `.split()` hashes. Iteration 1 recorded that splitting a short string
was bimodal — 8 tokens of one character cost 8.7 µs, 8 tokens of three
characters 2.3 µs — and concluded it was a musl mallocng size-class effect
rather than an interpreter one. Removing `RandomState` changed the size of every
scope map and moved the whole allocation pattern out of the slow mode. That
conclusion was right and the fix for it was three files away from the symptom.
**The lesson is not about split; it is that on musl a "this row is slow" reading
can be an allocation-shape reading, and the code the row names may be innocent.**

Run-to-run variance was checked before believing any of this: three consecutive
`perf` runs of the same binary agreed within 3%.

---

## 2026-08-21 · iteration 2 — the queue is ratio TIMES prevalence, not ratio

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 842 programs loaded

No engine change. The instrument was ranking the wrong list.

`lypning perf` sorted by how badly lypning loses to CPython. Its worst row was
`str-concat` at 43x — a genuine quadratic, since `Value::Str(Rc<str>)` cannot
grow and every `s += x` copies the whole string. The fix is a different string
representation: `Rc<String>` with an in-place append when the refcount is one.
That is an afternoon, it touches every string site in the crate, and it adds a
second pointer hop to *every* string read.

Before starting it, the corpus was asked how often agents actually type it:

| construct | corpus programs (of 842) |
|---|---|
| `open(` | 51.9% |
| slice `[a:b]` | 17.2% |
| `json.` | 15.4% |
| `.join(` | 8.6% |
| `.split(` | 8.1% |
| `def ` | 8.0% |
| f-string | 7.5% |
| `'%s' % x` | 2.3% |
| **`s += x` inside a loop** | **0.1% — one program** |

**So the change was not made**, and the instrument was fixed instead. Every case
now carries a regex, the corpus is scanned on each run, and a second ordering is
printed under the table: **how far behind, times how much of the corpus types
it**. `str-concat` drops off the queue at 0% prevalence and stays in the table
as a row, which is the right place for a real defect nobody is paying for.

The queue that ordering produces, and the actual work list from here:

| case | vs CPython | corpus | weight |
|---|---|---|---|
| `str-split` | 50.9x | 10% | 4.86 |
| `file-write-read` | 7.3x | 52% | 3.27 |
| `str-repr` | 4.7x | 88% | 3.26 |
| `call-method` | 4.7x | 83% | 3.09 |
| `call-recursive` | 34.2x | 8% | 2.64 |
| `str-methods` | 6.7x | 38% | 2.15 |

Two other suite defects fixed while in there: four cases were sized so small
that CPython spent under 2 ms on them, which makes a startup-subtracted ratio
mostly rounding error — `call-recursive` read 22x, 34x and 73x on three
consecutive runs of the same binary. They are bigger now, and the tool prints a
`too small to trust` line if any case drifts under the floor on a faster machine.

| | before | after |
|---|---|---|
| bytes | 1,045,176 | 1,045,176 (no engine change) |
| conformance | 500 / 263 / **0** | 500 / 263 / **0** |
| perf | ranked by ratio | ranked by ratio x prevalence |
| corpus-time | — | not run: no engine change to regress |

---

## 2026-08-21 · iteration 1 — stop copying whole sequences to reach part of one

**commit** `2e0931f` · **host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 ·
**corpus** 842 programs loaded, 763 timed, 79 skipped for naming an absolute path

Focus: raw performance. Four places where the interpreter allocated a copy of a
container to answer a question about a piece of it — `x in xs` cloning the list,
`s[i]` collecting a `Vec<char>`, a `step == 1` slice materialising an index
vector, and `.split()` building every part twice.

| | before | after |
|---|---|---|
| bytes | 1,045,176 | 1,045,176 (unchanged to the byte, 8 blocks) |
| conformance | 500 / 263 / **0** | 500 / 263 / **0** |
| perf TOTAL | 5.04x | **4.20x** |
| corpus-time | 541.8 ms | 544.7 ms (+0.5%, inside noise) |

Rows that moved, `lypning perf`, min of 5, startup subtracted, against
CPython 3.11:

| case | before | after |
|---|---|---|
| `str-slice` | 15.20x | **4.48x** |
| `membership` | 5.03x | **1.59x** |
| `str-split` | 53.56x | 43.51x |

**What did not work, and why it is here.** `.split()` no longer allocates each
part twice, and the row moved 19% — far less than the halving the allocation
count suggested. Splitting a short string is *bimodal* on this build: 8 tokens
of one character each costs 8.7 µs, 8 tokens of three characters costs 2.3 µs,
and 8 of eight characters costs 8.5 µs again. Neither token count nor string
length explains it monotonically, so it is an allocator effect (musl mallocng
size classes), not an interpreter one. **Do not re-propose "allocate less in
split" as the fix for that row** — the remaining cost is not in the code the
patch touched. If the row matters, the next thing to try is not allocating the
parts at all for the `len(...)`-only case, or a different allocation strategy,
and either is its own step.

**The reading on `corpus-time`.** Flat is the expected shape. The corpus median
is 0.7 ms against a 0.64 ms startup, so ~90% of that instrument is the process
spawn and a 16% compute win is worth ~1.6% of it. The entry records it as a
regression gate that stayed green, and the speed claim is made on `perf`, whose
suite is what the claim is about.

---

## 2026-08-21 · iteration 0 — the instrument, and the baseline it took

**commit** `5d65655` · **host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 ·
**corpus** 842 programs loaded, 763 timed

There was no instrument that could say *which construct* is slow. `bench`
compares arms and `corpus-time` compares runs, and both time programs that run
once and exit, so both are spawn-bound. `lypning perf` runs one loop per
construct with startup subtracted and sorts by the ratio.

The baseline it took, which every later entry is a step away from:

| | |
|---|---|
| bytes | 1,045,176 (8 blocks, static musl x86_64) |
| conformance | 500 MATCH / 263 UNSUPPORTED / **0 MISMATCH**, 65.5% coverage |
| perf TOTAL | 5.04x CPython on compute, over 29 cases |
| startup | 0.64 ms against CPython's 10.81 ms |
| corpus (bench, shared 500) | 416.0 ms against CPython's 6269.8 ms |

The worst rows, which are the work queue: `str-split` 53.6x, `str-concat`
41.6x, `call-recursive` 26.5x, `str-slice` 15.2x, `str-fmt-pct` 13.2x,
`str-join` 9.8x. Two rows where lypning already **wins** on compute:
`json-dumps` 0.92x and `print-lines` 0.34x.

Also wired this repository's own `.claude/` capture hooks, so the sessions that
do this work feed the corpus they are graded against.
