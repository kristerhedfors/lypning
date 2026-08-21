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

## 2026-08-21 · iteration 13 — five live MISMATCHes that `conformance` was never going to see

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1037 loaded, 861 timed

`type_name()` of every exception instance is the literal string `"Exception"`
(`value.rs`), and `isinstance` compared its second argument against that string.
So, all at **exit 0, with no refusal and no error**:

| program | lypning | CPython |
|---|---|---|
| `isinstance(ValueError('b'), ValueError)` | **False** | True |
| `isinstance(FileNotFoundError('x'), OSError)` | **False** | True |
| `isinstance(SystemExit(), Exception)` | **True** | False |
| `except Exception as e: isinstance(e, ValueError)` | **False** | True |
| `isinstance(type(3), type)` | **False** | True |

Five silent wrong answers, in the runtime whose entire argument is that it never
gives one. `conformance` was reporting **0 MISMATCH** the whole time and was not
wrong to: no corpus program calls `isinstance` on an exception yet.

The fix routes an `Exc` through **`exc_matches`** — the same table an `except`
clause uses — so the two can never disagree about the hierarchy. `SystemExit`
falls out correctly because that table already knows `Exception` does not catch
it, which is the point of having one table instead of two.

`isinstance(x, type)` is now **refused** rather than answered. It asks whether
`x` is a class, and lypning's `Value::Builtin` is both `int` and `print`, so
answering means guessing which builtins are types. A refusal costs one spawn and
CPython answers — invariant 1's trade, taken deliberately.

| | before | after |
|---|---|---|
| bytes | 1,045,176 (8 blocks) | 1,045,176 (8 blocks) |
| conformance | 524 / 337 / **0** | 524 / 337 / **0** |
| live MISMATCHes outside the corpus | **5** | **0** |

Fifteen shapes verified against the real CPython — the hierarchy in both
directions, `IOError`/`OSError` aliasing, tuple-of-classes, `bool` under `int`,
and the negatives — and pinned in `tests/test_semantics.py`.

### What this says about the gates

**`conformance` measures the corpus, not the language.** A construct nobody has
typed yet is a construct it cannot grade, and MISMATCH 0 means "no disagreement
*among the programs we have*". That is still the right gate — it is the only one
that grades against real usage — but it is not a proof, and the ledger should
stop reading it as one.

Both bugs found today came from **reading a hot path closely**: the arity
message from the call-binding code in iteration 11, and this from a survey of
what `type()` refuses. Neither came from a gate. `docs/COOKBOOK.md`-style
enumeration of a construct's shapes, run differentially against CPython, finds
things the corpus has not reached — and it is cheap: fifteen programs and a
shell loop.

---

## 2026-08-21 · iteration 12 — `for line in sys.stdin` was quadratic

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1037 loaded, 861 timed

`stdin_line()` called `stdin_all()`, and `stdin_all()` ends in `.clone()` of the
**entire captured input**. Once per line. `modules.rs` calls
`stdin → transform → stdout` the corpus's largest single cluster, so this was
the hottest real path in the runtime and it was O(n²).

Measured before and after, `for line in sys.stdin: n += len(line)`, ~22-byte
lines, min of 3, against CPython 3.11 with each arm's startup subtracted:

| lines | before | after | CPython |
|---:|---:|---:|---:|
| 1,000 | 2.96 ms | **0.49 ms** | 0.75 ms |
| 4,000 | 182.75 ms | **2.18 ms** | 0.73 ms |
| 16,000 | 2,299.99 ms | **8.41 ms** | 2.40 ms |
| 64,000 | *not run* | **34.68 ms** | 11.49 ms |

Four times the cost per doubling became two. At sixteen thousand lines that is
**273x**, and lypning goes from 678x *slower* than the interpreter it exists to
preempt, to 3.5x. It stays linear at sixty-four thousand.

The fix is small: a private `stdin_fill()` holding the read half, and
`stdin_line`/`stdin_rest` doing their scan and their one small slice inside the
buffer's borrow. `stdin_all()` keeps its copy — `sys.stdin.read()` and the
dispatcher's `stdin_consumed()` replay both need to own the bytes and both pay
for it once, and shrinking that would reach into the exit-90 fall-through, where
a truncated stream is a wrong answer rather than a slow one.

| | before | after |
|---|---|---|
| bytes | 1,045,176 (8 blocks) | 1,045,176 (8 blocks) |
| conformance | 524 / 337 / **0** | 524 / 337 / **0** |
| corpus-time | — | unchanged, and it cannot see this |

**Why `bench` and `corpus-time` are flat, and why that is not a defence of
them.** Of the 1037 corpus programs loaded, only 19 carry a captured stdin
sample: 38 bytes at most, six lines at most, twelve bytes and three lines at the
median. That is not evidence that real inputs are small — it is what the capture
harness can record. The shim inherits the pipe untouched rather than reading it
(`assets/shim/python-shim`), so the samples are an artefact of the instrument
and the quadratic path was fully live in real use.

**So the corpus has a blind spot, and it is shaped like its own capture
mechanism.** A cost that only appears at scale cannot be found by any of the
four gates here; this one was found by reading the code, and confirmed with a
scaling ladder written for the purpose. When a change is about *complexity*
rather than constant factors, the evidence is a ladder across input sizes, and
`perf` and `corpus-time` are expected to say nothing.

Pinned in `tests/test_semantics.py` — seven cases, differential against the real
CPython. They pin the **cursor**, not the speed: `readline` then `read`, `read`
then `readlines`, iteration then `read`, a last line with no newline, empty
input. That shared cursor is what this change could plausibly have broken, and a
timing assertion on a shared runner would only have measured the runner.

---

## 2026-08-21 · iteration 11 — two allocations out of every Python call

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1037 loaded, 861 timed

`call-recursive` stayed at the top of the queue after iteration 10, so
`call_func_inner` was read for allocations rather than for logic. It made four
per call: the scope's `Rc`, the scope map's table, a `Vec<bool>` of
"was this parameter bound", and a fresh `Vec<Scope>` for the frame's scope
chain. The last two are removable and nothing else in the function changes.

* **`used` is a `u64` bitmask.** Sixty-four covers every function anyone types
  — CPython's own hard limit on positional parameters is 255 — and past 64 it
  falls back to the vector, so behaviour is *identical* rather than merely
  unlikely to differ.
* **The scope-chain vector is recycled.** Spent chains go back to a pool capped
  at 64, cleared at the point the frame ends so scopes still drop on time. A
  recursion now pays for its depth once instead of once per frame per call.

| against iteration 10's binary | ratio |
|---|---:|
| `fib(21)` | **0.71x** |
| a closure called in a loop | **0.79x** |
| `def f(a)` … `f(1)` | **0.84x** |
| `def f(a,b,c)` … `f(1,2,3)` | 0.86x |
| `def f()` … `f()` | 0.88x |
| `len(t)` — the control, no `def` involved | 0.98x |

| | before | after |
|---|---|---|
| bytes | 1,045,176 (8 blocks) | 1,045,176 (8 blocks) |
| conformance | 524 / 337 / **0** | 524 / 337 / **0** |
| perf `call-recursive` | 20.19x | **17.56x** |
| perf TOTAL | 3.55x | 3.60x — inside the band, see below |
| corpus-time, min of 3 | 1.10 s | 1.11 s — flat |

**On reading the aggregates here.** perf TOTAL moved 3.55 → 3.60 and
corpus-time 1.10 → 1.11 s, and neither is a regression: both are inside the ~3%
spread iteration 10 measured, and the TOTAL is a sum of absolute milliseconds,
so it is dominated by the slowest cases and moves when they breathe. The claim
this entry makes is the per-case table above — six samples per cell, one
mechanism changed, and a control (`len(t)`) that does not go through
`call_func_inner` and did not move. **When a targeted change is real and the
aggregate is flat, say which one you are claiming and why.**

---

## 2026-08-21 · iteration 10 — paying iteration 8's debt, and sweeping `INLINE`

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1037 loaded, 861 timed

The re-ranked queue on the grown corpus put `call-recursive` first — Python
`def` calls, which is exactly what iteration 8 made 2–21% slower while making
builtin and method calls 14–19% faster. Two changes, both aimed there.

**`Args` travels by `&mut`.** It was moved by value through `call` →
`call_func` → `call_func_inner`, three copies of the struct per call. Now the
top frame owns it and the rest borrow. Worth a little on its own (`f()` 8.8 →
8.4, `fib(21)` 25.1 → 24.8) and not the main thing.

**`INLINE` is 2, and it was swept rather than chosen.** The array is initialised
on every call whether or not it is used, so a wide one taxes the zero- and
one-argument calls that dominate — `len(x)`, `str(x)`, `open(p)`, `x.split(s)`,
`f(x)` — to spare three-argument ones that are rarer. Against the whole `perf`
suite, which weights every case by corpus prevalence:

| | perf TOTAL |
|---|---:|
| `Vec` (before iteration 8) | 3.72x |
| **`INLINE = 2`** | **3.55x** |
| `INLINE = 3` | 3.67x |
| `INLINE = 4` | 3.63x |

Two costs `print(a, b, c)` about 2% and buys 24% on `len(t)`, 26% on
`t.count(c)` and 11% on `fib`. **The zero-argument regression iteration 8 booked
is gone**: `f()` is 7.7 against the `Vec` build's 7.6.

| | before (iteration 8) | after |
|---|---|---|
| bytes | 1,045,176 (8 blocks) | 1,045,176 (8 blocks) |
| conformance | 524 / 337 / **0** | 524 / 337 / **0** |
| perf TOTAL | 3.72x (`Vec` baseline) | **3.55x** |
| perf `call-method` (86%) | 2.38x | **2.18x** |
| corpus-time, min of 3 | 1.12 s | **1.10 s** |

### The instrument correction

`corpus-time` was read once and said **+1.4%, SLOWER**. Three runs of each
binary said otherwise:

```
new   1.14  1.11  1.10 s     ->  min 1.10
prev  1.12  1.12  1.12 s     ->  min 1.12
```

The single comparison landed inside the instrument's own spread. **`corpus-time`
has a ~3% noise band on this container, not the ±1% the skill claimed**, and one
run of it is not a reading. Both corrections are in the skill now: take the
minimum of at least three runs, and treat anything inside ±3% as flat.

Had that not been checked, this iteration would have been reverted for a
regression it does not have.

---

## 2026-08-21 · iteration 9 — the corpus grew, and it grew toward us

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64

`lypning harvest --transcripts` at the end of the session's work. The
`PreToolUse` hook is wired into `.claude/settings.json`, but hooks are read at
session start, so the session that wired them is not a session they capture; the
transcript scan reaches backwards and does not care.

| | before | after |
|---|---:|---:|
| corpus | 842 | **1037** |
| runnable (rest name an absolute path) | 763 | 861 |
| conformance | 500 / 263 / **0** | 524 / 337 / **0** |
| coverage | 65.5% | 60.9% |

**The coverage number fell and nothing regressed.** 195 new programs arrived,
24 of them inside the subset and 74 outside it, so the denominator grew faster
than the numerator. That is what invariant 1 means by a rising UNSUPPORTED count
being a coverage number and a build order rather than a regression — and it is
the shape to expect from every harvest, because a program already inside the
subset is one nobody had to write down.

### The part worth reading twice

The build order moved, and not evenly:

| blocker | before | after |
|---|---:|---:|
| `import re` | 97 | 112 |
| **`import pathlib`** | **2** | **41** |
| `import subprocess` | 9 | 15 |
| `import collections` | 11 | 15 |

`pathlib` went from nearly-nobody to the **second largest single blocker** in one
session — because *this loop* edits files with `pathlib` one-liners, and this
loop's transcript is now 20.7% of the corpus (`lypning corpus --stats`).

This is not a bug and the harvest should not stop: the corpus is real usage and
these were real sessions doing real work. But it is a bias with a direction, and
the direction is *toward whoever is reading the build order*. An optimiser that
harvests itself, then optimises for what it harvested, is measuring its own
habits. Recorded here, and in the skill, so the next reading of `--plan` is made
with the source split in view.

No engine change in this entry. The bytes, the binary and the `perf` suite are
untouched; the numbers every later entry is compared against have a new
denominator, which is why each entry states the corpus size it loaded.

---

## 2026-08-21 · iteration 8 — the argument list stops allocating

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 842 loaded, 763 timed

Iteration 6 said allocation count is the lever. This is the biggest single
allocation the interpreter makes: every call built a `Vec<Value>` for its
arguments and dropped it again. The cost was measured before anything was
written, by varying only the argument count:

| `def f(...)`: return 1 | µs/call | delta vs 0 args |
|---|---:|---:|
| `f()` | 0.258 | — |
| `f(1)` | 0.446 | **+0.188** |
| `f(1, 2)` | 0.461 | +0.203 |
| `f(1, 2, 3)` | 0.494 | +0.236 |

The step is at the **first** argument — 0.188 µs — and then 0.024 µs each after
it. That shape is an allocation, not work, and it costs more than an entire
zero-argument call.

`args::Args` keeps up to four arguments in the caller's stack frame and spills
to a `Vec` past that. No `unsafe`: vacated slots hold `Value::None`, which is a
discriminant write, so the array stays `[Value; 4]` and `Deref` still hands out
a real `&[Value]` — which is why `args.first()`, `args.get(i)`, `args.len()` and
`args.iter()` did not have to change at any of their call sites. Binding a
function's parameters uses `Args::take(i)` rather than `into_iter`, because
consuming it as an iterator would build the `Vec` again.

| against the previous commit's binary | ratio |
|---|---:|
| `len(t)` in a loop | **0.86x** |
| `t.count('a')` in a loop | **0.81x** |
| `print(1, 2, 3)` in a loop | **0.80x** |
| `fib(21)` | 0.95x |
| `def f(a)` … `f(1)` | 1.05x |
| `def f()` … `f()` | 1.21x |

perf `call-method` — the top of the weighted queue at 83% of corpus programs —
went **3.61x → 2.39x**. TOTAL 3.73x → 3.70x. corpus-time 552.0 → 551.5 ms, flat,
as a compute change is there.

**The trade, stated plainly:** builtin and method calls got 14–19% faster and
Python `def` calls got 2–6% slower, worst at 21% for a call with no arguments at
all, which pays the array's initialisation and buys nothing. That is a good
trade *for this corpus* — a `.foo()` is in 83% of its programs and a `def` in 8%
— and it would be a bad one for a workload of deep zero-argument calls. The next
step for it is passing `Args` by `&mut` instead of moving 152 bytes through
`call` → `call_func` → `call_func_inner`.

### The cliff, which is worth more than the change

The first version spilled by growing: start inline, and on the fifth argument
move the four across into a fresh `Vec` and push. That version was **eight times
slower than the `Vec` it replaced — for six arguments, and only six**:

| args | previous | INLINE=2 | INLINE=4 | INLINE=8 |
|---:|---:|---:|---:|---:|
| 5 | 21.7 | 30.2 | 29.0 | 21.7 |
| **6** | **28.2** | **227.4** | **221.7** | **27.2** |
| 7 | 32.4 | 37.3 | 32.9 | 26.8 |

Seven arguments were fine. Eight were fine. Six were catastrophic, at two
different INLINE values with two different spill capacities — and not at all
when six arguments stayed inline. This is the **same musl mallocng size-class
resonance iteration 1 recorded for `str-split`** and iteration 3 dissolved by
accident, and it is the third time this allocator has produced a cliff that
looks like an algorithmic bug and is not one.

The fix was to stop growing into the spill: the caller knows the argument count
before it starts, so `Args::with_capacity` allocates the final size once. That
needed an explicit `spilled` flag rather than `spill.is_empty()`, because a
pre-allocated empty `Vec` is not the same thing as no `Vec`.

**The rule to carry:** on this target, a two-phase allocation — allocate small,
then move and grow — is a trap. Allocate once at the final size wherever the
size is known. And when a benchmark is bad at exactly one input size, suspect
the allocator before the algorithm; sweep the neighbours first, because one
point is not a curve.

| | before | after |
|---|---|---|
| bytes | 1,045,176 (8 blocks) | 1,045,176 (8 blocks) |
| conformance | 500 / 263 / **0** | 500 / 263 / **0** |
| perf TOTAL | 3.73x | 3.70x |
| perf `call-method` (83%) | 3.61x | **2.39x** |
| corpus-time | 552.0 ms | 551.5 ms |

Pinned in `tests/test_semantics.py`: every arity from 0 to 9 across the inline
boundary, on a plain function, `*args`, `**kwargs`, defaults, star-unpacking,
and on builtins and methods — which reach the same argument list by three
different paths.

---

## 2026-08-21 · iteration 6 — callgrind, and the `String` nobody needed

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 842 loaded, 763 timed

### Stop guessing: there is a profiler on this container

`valgrind` is installed, `perf` is not. `callgrind` counts instructions exactly,
which is better than sampling for a program that lives half a millisecond. The
recipe, worth keeping:

```bash
cd src/lypning/assets/rust
cargo build --release --target x86_64-unknown-linux-musl \
      --config 'profile.release.strip=false' --config 'profile.release.debug=1'
valgrind --tool=callgrind --callgrind-out-file=cg.out \
      target/x86_64-unknown-linux-musl/release/lypning -c 'PROGRAM'
callgrind_annotate --threshold=85 cg.out
```

On `n += len(str(i))` × 3000, by share of all instructions:

| | share |
|---|---:|
| musl `malloc` / `free` (`alloc_slot`, `nontrivial_free`, `meta.h`) | **~26%** |
| `builtins::builtin` + `is_exception_name` — the linear table scans | ~12% |
| `eval_inner` / `eval` / `exec_block` | ~10% |
| `memcmp` (shared between the scans and `match name`) | 5.8% |

**A quarter of the instruction stream is the allocator.** That is the lever, and
it is the number to bring to any future argument about this interpreter's speed.

It also settles iteration 4's puzzle from the other side. The table scans really
are ~12% of *instructions* — and replacing them with binary search still bought
no wall clock, because a predictable, cache-resident SIMD `memcmp` retires far
more instructions per cycle than the allocator's pointer chasing does. **Ir is
not time.** Use callgrind to find *where the work is*, and the wall clock to
decide whether removing it helped.

### The change

`fmt::to_str` returns a `String`, because `repr` composes nested values into
one. Every caller that wanted a `Value` then paid `Rc<str>::from(String)` — a
second allocation and a second copy of bytes just written, then a free. Added
`to_rc`, `repr_rc` and `int_rc`: a str is already an `Rc<str>` (clone the
refcount, **zero** allocations where there were two), and an int is written into
a twenty-byte stack buffer (one allocation instead of two). `int_rc` counts down
on the negative side, because `-i64::MIN` overflows and that is reachable from a
corpus program.

`str` and `repr` are kept apart rather than sharing an arm: `str('a')` is `a`
and `repr('a')` is `'a'`, and that is precisely the kind of shared shortcut that
would produce a silent wrong answer.

| microcase, min of 7 | iteration 0 | now | net of drift |
|---|---:|---:|---:|
| `str(i)` | 0.746 µs | 0.589 µs | **−9%** |
| `str(s)` | 0.771 µs | 0.536 µs | **−21%** |
| `repr(i)` | 0.720 µs | 0.573 µs | **−8%** |
| `len(t)` (the control — untouched by this change) | 0.348 µs | 0.302 µs | — |

### And the suite could not see any of it

perf TOTAL stayed at 3.65x. `str-repr` is `repr([i, 'a', 1.5])`, which is a
*composite* repr and falls through to the old path; no case called `str()` or
`repr()` on a scalar at all — a construct that appears in 8% of corpus programs.

So a case was added (`str-of-scalar`), and the general lesson goes in the skill:
**when a change you measured helps and no row moves, the suite has a hole.** Add
the case in the same iteration, while you still know what it should measure.
Adding it renumbers the TOTAL (30 cases now, not 29), which is why `perf --diff`
prints the intersection rather than assuming it.

| | before | after |
|---|---|---|
| bytes | 1,045,176 (8 blocks) | 1,045,176 (8 blocks) |
| conformance | 500 / 263 / **0** | 500 / 263 / **0** |
| perf TOTAL | 3.65x (29 cases) | 3.97x (30 cases — a new, slow case, not a regression) |
| corpus-time | 552.2 ms | 542.8 ms |

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
