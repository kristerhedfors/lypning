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

## 2026-08-25 · iteration 43 — the row the suite said it could not see

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1551 loaded, 1305 timed

**bytes** 987,336 → 987,336 · **conformance** 906 / 399 / **0 MISMATCH** ·
**perf** `file-write-read` **1.98x → 2.40x**, weight 0.41 → **0.59** — and the
TOTAL renumbered

`lypning perf` had started printing its own warning on this row:

> ! too small to trust — the reference arm spent under 2 ms of work on
> file-write-read, so its ratio is mostly the startup subtraction. Grow the case.

`file-write-read` is typed by **42% of the corpus** — the second-highest
prevalence in the suite — and the instrument was saying out loud that it could
not measure it. So this iteration grew the case rather than optimising anything.

**It was not merely imprecise, it was hiding a gap.** The net ratio *grows* with
the size:

```
  lines     lypning net   cpython net   ratio
  20,000       8.7 ms        4.8 ms     1.82x
  50,000      20.5           9.7        2.12x
 100,000      40.1          17.0        2.36x
 200,000      81.3          33.2        2.45x
```

A per-byte cost higher than CPython's does not show at a size where both arms
are mostly startup. The case is 100,000 lines now — the reference arm does 17 ms
of work, comfortably clear of the 2 ms floor — and the row reads **2.40x**
instead of 1.98x. Resizing renumbers the row and the TOTAL; entries above this
one are not comparable across it.

Unlike iteration 31's `str-methods` bias, there is no trophy-case problem here:
nothing was optimised in this iteration, so the resize cannot flatter anything I
did.

### What the case now shows, for whoever takes it

With a row that can see I/O, callgrind and `strace` say the gap is **not** I/O:

* **Syscalls are already right.** The whole file is read in **2 `read` calls**;
  there is no per-line syscall to remove.
* `core::str::converts::from_utf8` is 3.90% — each line revalidated as UTF-8.
* The interpreter loop is **22%**: `exec_block` 4.73, `eval` 4.00, `eval'2` 3.86,
  `lookup` 3.09, `assign` 2.45, and `memcmp` 6.79 on top of it.

So `file-write-read` at 2.40x is mostly 100,000 iterations of a Python loop, not
100,000 lines of file handling. That is the same answer the last three profiles
gave, and it is the finding this iteration ends on — see below.

### The gradient under the current dial is flat

Iterations 41–43: one win (`call-method` −14%, real), **two reverted negative
results** (binary-searched name tables, ASCII byte-scan split), and a suite fix.
Every profile taken since iteration 41 — `call-method`, `str-split`,
`file-write-read` — puts the same thing on top: `memcmp` from name resolution,
and then `eval`/`exec_block`/`lookup`/`assign`, which together are 18–22% of
every run.

The leaf operations are done. What is left is the **tree-walking evaluator
itself**, and making that cheaper is not an iteration — it is the
slot-resolved-frames branch the skill already names: resolve local names to
indices at definition time, so `lookup` stops hashing a string and `builtin()`
stops being reached at all on a miss. `assigned_names` already computes the set
that branch needs.

Recording it here rather than starting it: it is a multi-step change to the
calling convention, and the skill's rule is to name a branch and take a smaller
step meanwhile.


## 2026-08-25 · iteration 42 — the same negative result, re-run with the reason for doubting it

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **REVERTED, nothing committed to the engine**

`memcmp` is the **largest single entry** in the profile of both rows at the top
of the queue — 7.55% of `call-method`, 6.62% of `str-split` — and a good part of
it is name resolution scanning tables:

* `builtin()` walked `BUILTINS` with `.iter().find()`. `len` sits **19 compares**
  in, and a name that is *not* a builtin — the common case on a scope miss —
  walked all 39 and then all 24 exceptions before answering `None`.
* `is_exception_name()` is asked by `call_builtin` before **every** builtin call
  and scanned 24 unsorted entries to do it.

Iteration 4 already tried binary search here and measured zero. The reason to
re-run it was specific and, I thought, good: **that reading was taken when a
quarter to a half of the instruction stream was inside musl's allocator.**
Iterations 18 and 27 removed the allocator. The surroundings had changed
completely, so the result might have too.

**It had not.** `EXCEPTIONS` sorted, all three readers switched to
`binary_search`, three interleaved rounds against the unchanged binary:

```
            TOTAL     str-split  name-lookup  call-method
  scan     1172.8       35.1        20.1         21.9      (minima)
  bsearch  1195.8       35.6        20.3         22.0
```

Every individual row is flat, and the **TOTAL is 2% WORSE with
non-overlapping bands**. Reverted in full — the engine is unchanged.

The mechanism is the one iteration 4 named and it survives the allocator's
removal intact: a linear walk of a small, sorted, cache-resident table is
perfectly branch-predicted and its `memcmp`s are two or three bytes long, while
a binary search over 39 entries is five or six *unpredictable* branches. High Ir
at high IPC beats low Ir at low IPC. **Ir is not time**, and this is the second
time this table has proved it.

What this actually rules out is broader than the change: `memcmp` sitting at the
top of a callgrind profile is not, by itself, a reason to do anything. The next
person to see 7.55% there now has two measurements saying so, taken under
opposite conditions.


## 2026-08-25 · iteration 41 — a two-way searcher to look for one byte

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1551 loaded, 1305 timed

**bytes** 987,336 → 987,336 (**8 blocks**, unchanged) ·
**conformance** 906 / 399 / **0 MISMATCH** ·
**perf** `call-method` **25.7 → 22.0 ms** (−14%, min of 3 interleaved) ·
**corpus** no change outside noise ·
**fuzz** seed 20260824 × 3000, 0 counterexamples

`call-method` is 1.90x CPython and typed by **88% of the corpus** — the
highest-weighted row there is. Callgrind on `t.count('a')` over a six-character
string said where it goes:

```
  memcmp                       3.77M   7.55%
  TwoWaySearcher::next         2.66M   5.33%
  eval + eval'2 + exec_block   8.77M  17.6%
  StrSearcher::new             0.98M   1.96%
```

`str`'s substring routines build a **two-way searcher** — a Boyer-Moore-class
algorithm with a setup phase — for any `&str` pattern, however short. Together
that is **3.64M of 49.9M instructions retired, 7.3%, to look for one byte.**

A single ASCII byte cannot occur as a UTF-8 continuation byte, so counting it in
the bytes is exactly counting it in the characters. `count` scans bytes;
`find`/`rfind`/`index`/`rindex` hand std a `char` instead of a `&str`, which
takes its own single-character path rather than the searcher.

The guard is **`< 0x80`, not `len() == 1`**, and that is the whole correctness
argument: a one-*byte* needle is not a one-*character* needle, `'é'` is two
bytes, and a needle that is one non-ASCII character must keep the general path.

### What it is worth, and where

`call-method` 25.7 → 22.0 ms, three interleaved rounds each, **bands do not
overlap** (25.7 / 26.0 / 26.9 against 22.0 / 22.2 / 23.3). The suite TOTAL is
flat — 1184.3 against 1180.9 on the minimum, overlapping — because this row is
26 ms of 1,200. `corpus-time` is likewise flat: 1477 / 1628 / 1563 against
1504 / 1494 / 1576, interleaved, overlapping.

That is the expected shape and not a disappointment. `.count(`, `.find(` and
`.startswith(` with a one-character needle are what `line.count(',')` and
`s.find('=')` are, and 88% of the corpus makes a method call — the win is spread
across programs the aggregate instruments cannot resolve, which is precisely what
skill §3 says about them.

975 cells against CPython — 13 haystacks (ASCII, multi-byte, embedded NUL and
tab, 50 characters long) × 15 needles (absent, one-byte, two-byte, one non-ASCII
character, two non-ASCII characters, empty) × the five methods — **0 differing**.
11 pinned.

### The baseline lied again, and the fix is the same one

`corpus-time --baseline` reported **1.413x SLOWER**. The baseline was recorded
this morning on a freshly booted box; the machine has since run six subagents,
a dozen cargo builds and three benchmark passes. Interleaved, the two binaries
are indistinguishable. That is the fourth entry in two days to record a
`--baseline` reading against a stale machine state, and the fourth to be resolved
by measuring both arms in the same minute.


## 2026-08-25 · iteration 40 — the routing table, and what "importable" does not mean

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1551 loaded, 1305 timed

**bytes** 987,336 → 987,336 (**8 blocks**, unchanged) ·
**conformance** (lypning arm) 906 / 399 / **0 MISMATCH** ·
**routing** IDEAL 1188 → **1190**, LATE 85 → **83**, WASTED 28 → 28,
**UNSAFE 4 → 4** ·
**mixture wall clock** not resolvable — see below

Not an interpreter change. `tests/test_routing.py` warns about modules the tier
can import that `route.rs`'s table omits, because each one sends its programs to
a CPython spawn they do not need. It reported two: `argparse` and `unicodedata`.

**`argparse` is in.** Two programs move from CPython to lypning-mp — IDEAL +2,
LATE −2, WASTED and UNSAFE unchanged, reproduced 3/3 interleaved on each binary.

### `unicodedata` is out, and that is the entry

It imports. It does not *work*. `unicodedata.decomposition` is absent from the
tier, so `py-876af0f0a956` — which prints a version banner and then calls it —
gets the banner onto stdout **before** the refusal, and lypning-mp streams, so
those bytes are already committed (§6). Adding the module moved routing safety's
fatal count from **UNSAFE 4 to 5**.

That is the whole meaning of the sentence the test prints, demonstrated rather
than quoted: *importable is not the same as complete*. `import unicodedata`
exits 0 on the tier; the table is not about imports, it is about answers. The
check that matters is whether the corpus programs using a module AGREE on the
tier — and the ones that cannot must **refuse** cleanly, not print first.

`route.rs`'s doc now carries both halves, so the next person who reads that
warning finds the measurement rather than repeating it.

### Two numbers I got wrong on the way, both by measuring once

**"+7 IDEAL, −7 LATE."** One conformance run said 1195/78 and I nearly wrote it
down. Three interleaved runs of each binary say **1190/83 against 1188/85** —
+2, not +7. Routing looked like a deterministic instrument and is not quite: the
*ideal* engine is the first on the ladder that MATCHED, and a handful of corpus
entries are environment-dependent enough that the match itself can flip. The
prediction histogram agreed with the smaller number all along (cpython 134 →
132, exactly two programs); I had two readings that disagreed and quoted the
flattering one.

**The wall-clock win.** Two programs × the spawn difference (~18 ms CPython
against ~1.8 ms on the tier) is about **32 ms** on a mixture total near 8,300 —
0.4%. Interleaved, three rounds each: 8187.8 / 8435.4 / 8947.5 before against
8259.7 / 8370.3 / 8704.4 after. Overlapping, and the *minimum* is worse after.
The benchmark cannot resolve this change and the entry does not claim it did.

So this is accepted on the routing report, which counts programs exactly, and
explicitly not on the benchmark, which is spawn-bound and noisy at this scale.
A 0.4% routing gain that costs no bytes and no safety is worth having; a 0.4%
gain claimed as a measured speedup would not be.

### The scouts could not run

The subagent fan-out for this iteration was blocked — every tool call rejected
before execution by a broken permission handler, the same failure that killed the
differential sweep on 2026-08-24. All six agents reported it and refused to
invent findings, which is the right behaviour and worth recording: the report
that says "I measured nothing" is the one that does not cost the next iteration
a wasted slot.


## 2026-08-25 · iteration 39 — the numbers, re-measured with the third tier built

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1551 loaded, 1305 timed

Not a change to the interpreter. A measurement pass, because every figure in
`README.md` and `docs/LYPNING.md` still described the 2026-08-21 tree — a
different binary, a corpus two-fifths smaller, and iterations 18–38 ago.

**lypning-mp was built for the first time in this tree**, which needed
`gcc-multilib` and a working archive. That matters more than it sounds: without
it the mixture arm falls straight through to CPython on every refusal, and the
first run of the day reported **0.541x** where the real figure is **0.302x**.
A benchmark with a hole where a tier should be is not a slower result, it is a
different question.

```
startup, min of 15    cpython 11.57   lypning 0.66   lypning-mp 0.61   mixture 0.60
shared subset (904)   cpython 1.000x  lypning 0.089x lypning-mp 0.102x mixture 0.131x
whole corpus (1305)   cpython 1.000x  lypning 0.069x lypning-mp 0.098x mixture 0.302x
```

**The mixture saves 69.8%** — 16,658 ms across 1305 programs, nothing
unanswered. That is up from the 66.0% recorded on 2026-08-21, on a corpus that
has since grown by 709 programs.

**lypning is ahead of lypning-mp on the shared subset again**, 0.089x against
0.102x. That ordering is now on its third reading: upstream had lypning ahead,
this tree reversed it twice, and the allocator work has put it back. The docs say
plainly that this is not evidence the question is settled — the shared subset is
by construction the programs lypning accepts, where both engines sit near their
startup floor, and a capture that adds harder shared programs can move it again.

### Building the tier turned two gates red, and none of it is this session's

`lypning conformance` with all three arms: **12 MISMATCH** — 11 on lypning-mp,
1 on the mixture — and routing safety **4 UNSAFE**. The lypning arm stays at
906 / 399 / **0**.

Four of the eleven are the commit-barrier defect §6 already describes. **Six of
the other seven arrived with the corpus, not with the tier**, and they are the
most interesting thing in this entry: iterations 24–26 wrote differential probes
to find defects in the *Rust core* — grids over `str.find` bounds, over
`json.loads` control characters, over `int()` whitespace — the capture harness
harvested them from the transcript, and they now find **the same defect families
in lypning-mp**. `'Hello'.find('', 6)` answers 5 there; `json.loads('"a\tb"')`
returns a string. Both were always true of the tier. Nothing could see them until
a corpus entry looked.

The mixture's single mismatch is the one shape §5 exists to prevent: lypning-mp
answers `py-9b16a7261b96` at exit 0 with the wrong output, so the chain never
falls onward. Three of the four UNSAFE routes the dispatcher recovered; that one
it cannot.

CI is unaffected — it does not build the tier, and the comment on the `core`
job's conformance line already said why the mixture arm is clean only in that
configuration.

### Pruning the accept-list, and a limit in the scorer

`.github/known-mismatches.json` gained 7 entries and lost 3, and the scorer now
exits 0: every observed mismatch is named and every named one reproduces.

The three removed were flagged **GOOD NEWS — no longer reproduces**. Two of them
genuinely are: `py-a17250cecb37` and `py-ed8fafe6cdb2` now exit **90** on the
tier, which is a refusal and the correct outcome. The third, `py-d72e3ff2ddbe`,
**still differs when run by hand** — mp exits 1 on `from lypning import engines`
where CPython exits 0 — and the battery simply does not surface it, because the
entry is graded on its exit code alone.

So the scorer cannot tell *fixed* from *no longer measured*, and its message
asserts the first. That is a real gap and it is written into the ledger file
rather than worked around, because the next person to read a GOOD NEWS line
deserves to know it might mean neither.


## 2026-08-25 · iteration 38 — the conversion grid goes to zero

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1551 loaded, 1305 timed

**bytes** 983,240 → 987,336 (+4,096; **8 blocks**) ·
**conformance** 906 / 399 / **0 MISMATCH** ·
**perf** flat (interleaved, below) ·
**fuzz** seed 20260824 × 3000, 0 counterexamples ·
**the grid** 26,280 checked, **0 DIFFERING**

Iteration 37 left three defects from the conversion grid. All three are closed,
and one of them turned out to be four.

**The `0x` prefix belongs in the slot that precedes zero fill**, exactly as a
sign does. Prepending it to the body made `format(255, '#010x')` come out
`'00000000xff'` where CPython gives `'0x000000ff'` — and `format(-255, '#010x')`
is `'-0x00000ff'`, which is the same rule twice over. Reachable with no `%`
anywhere.

**`#` on a float keeps the decimal point** when the precision left no digits
after it: `'%#.0f' % 0.0` is `'0.'`, `format(1234.0, '#.0e')` is `'1.e+03'`,
`format(0.5, '#.0%')` is `'50.%'`. The point goes after the significand, which
is the same place for `f` and *not* the same place for `e` or `%` — so
`keep_point` finds the first non-digit rather than appending.

**`%c` was four things at once.** It raised `ValueError` where CPython raises
**`OverflowError`** (and `format(x, 'c')` shares that path); it aligned left
where CPython aligns right, for both `%5c` and `format(65, '5c')`; the `0` flag
applied where CPython ignores it; and it **refused a one-character string**,
which CPython accepts — `'%c' % 'a'` is `'a'`. That last one is the only
conversion that cannot be handed to `format()` wholesale, because `format('a',
'c')` is a ValueError there.

### The precision defect, and where the decision goes

`%.Nd` is minimum **digits**, which `format()` has no spelling for. It is
**refused** rather than implemented: it is expressible — the body is
`format(v, "0{P + 1 if signed}d")` and the outer width composes on top,
collapsing to one call of width `max(P + signlen, W)` when the `0` flag is set —
but that is three composition rules to get exactly right on a construct the
corpus barely contains, and this session has already shipped one bug by being
clever on an error path (iteration 28).

The first cut refused it in `read_spec` and gave away **2,016 cells**, including
ones that were already correct: `'%.2d' % 42` is `'42'` either way. Only a value
knows how many digits it has, so the decision moved to `percent_one`, where the
refusal covers exactly the cells where the precision adds something — 1,152, down
from 2,016, and agreement went up 850 cells with no other change.

### Three-way, and it is zero now

```
  17112  both agree with CPython
   2664  FIXED
      0  BROKEN
      0  both wrong
   3840  refused by one or both
```

Every non-refused cell of 23,616 agrees. The wider 35,400-cell sweep reports
**26,280 checked, 0 DIFFERING**, and the 400-spec `format()` alternate-form grid
and 23 `%c` shapes are clean too.

### A regression caught by measuring the thing that changed

Moving the prefix into the sign slot was written as
`pad_signed(&format!("{signch}{alt_prefix}"), …)` — a `String` on **every**
numeric format, for a branch only `#x` ever takes. `str-fmt-pct` went 78.1 → 85.7
ms, non-overlapping over three interleaved rounds, so it was real. Concatenating
only when there is a prefix put it back: 78.1 / 81.0 / 80.0 before against
78.8 / 81.7 / 93.5 after — overlapping on the minimum, which is the reading the
skill says to take.

21 cases pinned; 14 fail on the iteration-37 binary.


## 2026-08-25 · iteration 37 — the format spec was built in the wrong order, and `%5s` leaned the wrong way

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1551 loaded, 1305 timed

**bytes** 983,240 → 983,240 (**8 blocks**, unchanged) ·
**conformance** 906 / 399 / **0 MISMATCH** ·
**perf** flat (interleaved, below) ·
**fuzz** seed 20260824 × 3000, 0 counterexamples

`%`-formatting works by translating each conversion into a `format()` spec.
Iteration 36 measured that translation and left the disagreements it found for
this one: **8,346 differing cells of 29,100** in the conversion grid. Sorted by
cause, they were twenty families and four defects. Two are fixed here.

**`%5s` leans the wrong way.** The default alignment for a `%s` conversion is
**right**; `format()`'s default for a string is **left**. So `'%5s' % 'ab'`
produced `'ab   '` where CPython produces `'   ab'` — which is every `%`
one-liner that lines a column up, and about as ordinary as input gets.

**The pieces of a format spec are not commutative.** The order is
`[align][sign][#][0][width][.prec][type]`, and this emitted the zero-pad flag
*first, as if it were an alignment*:

```
format(1.5, '+0f')   '+1.500000'        format(255, '#05x')   '0x0ff'
format(1.5, '0+f')   ValueError         format(255, '0#5x')   ValueError
```

So every conversion combining `0` with a sign or with `#` raised ValueError where
CPython formats. Two smaller rules came with it, both checked against CPython
rather than assumed: a `-` beats a `0` (`'%-05d' % 255` is `'255  '`), and a `0`
never applies to a string conversion (`'%05s' % 'a'` is `'    a'`).

### The gate for a correctness change is three-way, not two

"Agrees with CPython more often" is not enough — the question is whether anything
that agreed **stopped**. So the grid was run on three binaries at once, 23,616
cells:

```
  14614  both agree with CPython
   2618  FIXED
      0  BROKEN
   2920  both wrong, unchanged
    776  both wrong, but changed
   2688  refused by one or both
```

**Zero broken.** The 448 cells inside that "refused" row that are *newly* refused
are an improvement too, and worth reading carefully: they were a **wrong
ValueError** before and are an exit-90 refusal now, so the dispatcher hands them
to CPython and the caller gets the real answer. `'%+0d' % 1.5` is `'+1'` in
CPython; lypning said ValueError, and now refuses — because `%d` on a float was
always a refusal and the bogus ValueError had been masking it.

The 776 "both wrong, but changed" are the same story one step short: `'%+0.2d' %
1` went from ValueError to `'+1'`, where CPython says `'+01'`. Closer, still
wrong, and the reason is the next defect.

### Still open, from the same grid

* **`%.Nd` is minimum DIGITS, not a `format()` precision.** `'%.2d' % 1` is
  `'01'` and `'%.7d' % -42` is `'-0000042'`; lypning passes `.N` straight through
  to a precision, which for an integer is meaningless, so it is dropped.
  `format(-1, '03d')` is `'-01'`, so the shape is expressible — it needs the
  precision turned into a width around the sign, composed with the *outer* width.
* **`#` on a float conversion.** `'%#.0f' % 0.0` is `'0.'` — the alternate form
  keeps the decimal point — and lypning gives `'0'`. This one is not a
  translation bug: `format(0.0, '#.0f')` is wrong in lypning's own `format()`.
* **`format(255, '#05x')` is `'0x0ff'` and lypning says `'00xff'`.** Also
  `format()`'s own, reachable with no `%` anywhere: the `0x` prefix has to come
  before the zero fill.

### On the numbers

The machine restarted mid-iteration and the `--baseline` files predate it, so the
absolute readings are worthless — the suite reported +489 ms and `pytest` had
gone from 12.6 s to 21.7 s on unchanged code. Measured the only way that works,
three interleaved rounds of both binaries in the same minute:

```
  TOTAL      before 1351.9  1403.7  1358.6      after 1338.9  1364.3  1372.6
  str-fmt-pct       79.3    79.5    80.3              77.4    77.9    81.5
```

Overlapping on both. A correctness fix that adds three `push`es to a `String`
costs nothing measurable, which is what it should cost.

11 cases pinned; 7 fail on the iteration-36 binary.


## 2026-08-25 · iteration 36 — twelve allocations for seven bytes

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1551 loaded, 1305 timed

**bytes** 987,336 → **983,240** (−4,096; 8 blocks) ·
**conformance** 906 / 399 / **0 MISMATCH** ·
**perf** `str-fmt-pct` **85.16 → 63.36 ms** (−26%); TOTAL +0.9%, see below ·
**corpus** 938.2 → 932.2 ms, 0.994x ·
**fuzz** seed 20260824 × 3000, 0 counterexamples

`'%d-%s' % (i, 'a')` produces seven bytes and allocated about a dozen times to
do it. Three of those are gone:

* **The format string was collected into a `Vec<char>` on every call** — four
  bytes per character of a string that is almost always ASCII. The scan walks
  the bytes now and copies each literal run with one `push_str`. `%` is ASCII
  and cannot occur inside a multi-byte sequence, so every slice boundary is a
  character boundary by construction.
* **The translated spec was a fresh `format!` per conversion.** One buffer,
  cleared and refilled, for the whole call.
* The output starts at the format string's length rather than at zero.

`read_spec` now writes into that buffer and returns only the index; its pieces
are byte slices of the format string, so none of them allocates either. The one
place a non-ASCII character can appear is the conversion letter, and that is an
error path — decoded there so the message can still name it.

### How a refactor gets accepted

Nothing about this is supposed to change an answer, so the gate is not
"agrees with CPython" — it is **"agrees with the binary before it"**, which is a
stronger claim and a cheaper one to check. The whole conversion grid, run on
both binaries: conversion × flags × width × precision × value.

**29,100 cells identical, 0 differing.** Plus a second pass over 4,536
spec/value pairs comparing exit code, stdout *and stderr* — including the
conversions that refuse and the ones that raise — **0 differing**. The refusal
set is unchanged, which is the half a value-only comparison would have missed.

The `perf` TOTAL reads +0.9%, spread as +0.3 to +0.5 across a dozen rows with no
causal path to `ops.rs`, while the row this touches moved −21.81 ms. That is the
optimiser redistributing inlining, the shape iteration 15 documented; the byte
count moving *down* 4,096 on a change that only deletes work is the same effect
from the other side.

### What the grid found on the way, and did not fix

Run against **CPython** rather than against the previous binary, the same grid
reports **8,346 differing cells of 29,100**, and they reproduce on the
iteration-35 binary — pre-existing, not this change's. The first family is
precision on an integer conversion, which in C and Python means *minimum
digits*:

```
'%.2d' % 1     lypning '1'     CPython '01'
'%.7d' % -42   lypning '-42'   CPython '-0000042'
```

lypning translates `.N` straight through to a `format()` precision, and
`format(1, '.2d')` is not a thing — so the precision is silently dropped. 8,346
cells is a multiplier over flags and widths rather than 8,346 defects, and
sorting them into distinct causes is the next iteration, not this one.


## 2026-08-24 · iteration 35 — two allocations per generator element, and a reading that was the machine

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1551 loaded, 1305 timed

**bytes** 983,240 → 987,336 (+4,096; **8 blocks**, headroom 61,240) ·
**conformance** 906 / 399 / **0 MISMATCH** ·
**perf** `genexpr` **22.22 → 16.92 ms** (−24%, min of 3 interleaved) ·
**corpus** no change outside noise ·
**fuzz** seed 20260824 × 3000, 0 counterexamples

`gen_next` swaps the real `GenState` out of its `RefCell` so `eval` can re-enter,
and filled the hole with `GenState::placeholder()` — which built
`Rc::new(Vec::new())` and `Rc::new(Expr::None)`. **Two heap allocations and two
frees per element yielded**: an `RcBox` around a 24-byte `Vec` header, and one
sized to the largest `Expr` variant, both dropped a few lines later when the real
state goes back.

The stand-in borrows the real state's two `Rc`s instead. Two refcount increments
do the same job, and neither field is ever read through it — the placeholder is
marked `running` and `done`, and both are checked before anything else.

29 generator programs against CPython — laziness (`any(1//x for x in [1, 0])` is
`True` because the second element is never asked for), a generator that raises
partway, closures captured by one, a generator outliving its frame, `next` with
and without a default, partial consumption then `list()`, nested and interleaved
generators, and 200,000 elements — 0 mismatches.

### The reading that was the machine, not the code

The first `--baseline` run said `genexpr` **35.25 → 16.83** and the TOTAL
1428.74 → 901.32, a 37% improvement across **every row** — `tuple-unpack`,
`loop-while`, `call-method`, `list-append`, all down 13–15%, none of which this
change touches. `corpus-time` said 0.617x.

None of it was real. The build had also dropped from 19 s to 12 s and `pytest`
from 17.5 s to 11.3 s: the box had got quieter, because the ten subagents of a
background sweep had stopped competing for four CPUs. A `--baseline` file
recorded under load and compared against a run without it measures the load.

Three interleaved rounds of both binaries, same minute:

```
                 TOTAL                    genexpr
  before   892.14  900.60  858.34    22.22  25.18  27.21
  after    882.03  863.34  852.91    17.68  16.92  18.91
```

`genexpr`'s bands do not overlap — −24%, real. The TOTAL's overlap completely —
flat, and the 37% was a fiction. `perf` interleaves its two ARMS for exactly this
reason, so the ratio inside one run is trustworthy; a recorded baseline from an
earlier moment is not, and this is the second entry in two days to say so.


## 2026-08-24 · iteration 34 — unpacking copied the tuple it was taking apart

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1551 loaded, 1305 timed

**bytes** 983,240 → 983,240 (**8 blocks**, unchanged) ·
**conformance** 906 / 399 / **0 MISMATCH** ·
**perf** `tuple-unpack` **27.59 → 23.73 ms** (−14%), `enumerate-zip` 32.30 →
28.06 (−13%) ·
**corpus** 1.58 → 1.57 s, 0.993x ·
**fuzz** seed 20260824 × 3000, 0 counterexamples

`a, b = pair` went through `iter_collect`, which for a tuple is `(**t).clone()`
— a fresh `Vec<Value>`, every element cloned into it, handed over one at a time,
and the vector dropped. The shape is everywhere: `for k, v in d.items()`,
`a, b = b, a`, `for i, x in enumerate(…)`.

A tuple of exactly the right length with no star target now assigns straight
from the source.

**A list deliberately does not**, and that is the interesting half. CPython's
`UNPACK_SEQUENCE` reads every element before assigning any, so a target that
mutates the list must not be visible to the elements after it — the snapshot is
required, and the snapshot is exactly the copy `iter_collect` already makes.
There is nothing to win there. A tuple is immutable, so reading it in place and
snapshotting it are the same thing. (`l[f()], l[1] = src` where `f` appends to
`src` is pinned.)

### The same message defect as `join`, in the same shape

The 36-program sweep found `a, b = 5` reporting `'int' object is not iterable`
where CPython says **`cannot unpack non-iterable int object`**. Unpacking has its
own message, exactly as `join` does. Pre-existing on the iteration-33 binary.

And the same trap: the remap goes on `make_iter` **alone**, with the drain
written out, or every exception the sequence raises while being drained becomes
it — `a, b = (1//x for x in [1, 0])` would report a TypeError where CPython
raises ZeroDivisionError. That is the third time this exact shape has come up
(`sum`, `join`, here), which is starting to look like a pattern rather than three
accidents.

### The pins that did not pin anything

The message cases went into `CASES` first and **passed on the binary that had
the bug** — because `CASES` compares stdout and the exit code, and every one of
them exits 1 with empty stdout whether the message is right or wrong. A test
that cannot fail is worse than no test, because it reads as coverage.

They are in a new `STDERR_CASES` now, with a test that asserts the message and
checks the oracle first, so a CPython that changed its wording fails loudly
instead of quietly turning the assertion into a tautology. Three of them fail on
the iteration-33 binary; the behavioural pins (nested, star, subscript targets,
swap, the list snapshot) stay in `CASES`, where comparing stdout is exactly
right.

---

## 2026-08-24 · iteration 33 — `1 == 2` paid for a recursion guard

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1551 loaded, 1305 timed

**bytes** 983,240 → 983,240 (**8 blocks**, unchanged) ·
**conformance** 906 / 399 / **0 MISMATCH** ·
**perf** `membership` **127.43 → 99.52 ms** (−22%), `list-sort` 278.65 → 254.35
(−8.7%); TOTAL 1428.74 → 1391.39 ·
**corpus** 1.58 → 1.58 s, 1.002x ·
**fuzz** three seeds × 2500, 0 counterexamples

`err::Nest` bounds the three descents a *program* chooses the depth of — `repr`,
`hkey`, and comparison — because each was measured overflowing the stack, and a
stack overflow embedded is the **host's** SIGSEGV rather than a refusal it can
route onward. It is the right guard.

It was taken at the top of `eq`, `order` and `hkey`, before any dispatch. So
`1 == 2` paid for it. So did `'a' in ['b', 'c']`, and every dict get, every dict
insert, and every `in` over a dict or set — because each of those hashes a key
through `hkey`. A thread_local read-modify-write in, an `R<Nest>` built, and
another read-modify-write out, on operations that **cannot recurse at all**.

The guard now sits on the arms that descend: the composite arms of `eq`, the
`List`/`Tuple` arms of `order`, and the one `Tuple` arm of `hkey`. Scalars,
numbers, strings and bytes go straight through.

### The measurement that matters is that nothing changed

A speed change to a safety guard is only worth having if the guard still works,
and "still works" here means a specific shape: shallow answers, deep **refuses**,
and nothing ever exits any other way — 139 is a SIGSEGV the dispatcher cannot
route onward. Both binaries, five descents (`eq`, `order`, `in`, tuple-as-dict-key,
`repr`), depths 10 / 100 / 400 / 490 / 600 / 2,000 / 20,000:

**Every cell identical**, including the transition — 490 answers, 600 refuses,
on both. No crash at 20,000 deep on either.

`tests/test_recursion_guard.py` is that shape as a permanent test, and it
**passes on both binaries**, which is the point and is worth saying plainly: it
catches nothing today. It is a characterisation test for the next change to this
code, not evidence for this one. It asserts the shape rather than the number,
because `MAX_NEST` may move and a test pinning 500 would only ever be edited.

It also could not live in `test_fuzz_findings.py`: those cases assert lypning
**agrees** with CPython, and the whole point of these is that lypning refuses
where CPython answers. That is the contract working.

---

## 2026-08-24 · iteration 32 — four allocations to write one line

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1551 loaded, 1305 timed

**bytes** 983,240 → 983,240 (**8 blocks**, unchanged) ·
**conformance** 906 / 399 / **0 MISMATCH** ·
**perf** `print-lines` **14.42 → 12.24 ms** (−15%) ·
**corpus** 1.58 → 1.53 s, 0.966x ·
**fuzz** seed 20260824 × 3000, 0 counterexamples

`print(` is the one construct nearly every corpus program executes, and the arm
allocated four times to write one line: a `String` for a separator it never uses
when there is one argument, a `String` for a newline, a `String` to accumulate
into, and one more from `to_str`. The defaults are `&'static str` now, and the
single-argument case — which is almost every `print` — takes the string `to_str`
already built instead of copying it into a second one.

`print-lines` was **0.27x** CPython before this, so the row was never the reason
to do it. The reason is the one the instruments cannot show: `corpus-time` is
spawn-bound and cannot see a per-call allocation at all, and this is the call
every program makes.

### And the sweep found two more silent wrong answers

`sep` and `end` must be `str` or `None`, and CPython checks the **type** rather
than converting. This converted:

```
print('a', end=2)    printed `a2`   CPython raises TypeError
print('a', sep=1)    printed `a`    CPython raises TypeError
```

The second is the more interesting one. With a single argument the separator is
never used, so a bad `sep=` was accepted **silently** and would only have shown
up the day someone added a second argument. Both reproduce on the iteration-31
binary; both now raise CPython's exact message, including the type name.

35 print shapes swept against CPython — zero, one and many arguments, every
`sep`/`end` combination including `None`, `file=` to stdout and stderr, an
invalid keyword, `*` unpacking, non-ASCII, and a 100,000-byte line — 0
mismatches. Eight pinned; four fail on the previous binary.

---

## 2026-08-24 · iteration 31 — two negative results, and a win the suite cannot see

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1551 loaded, 1305 timed

**bytes** 983,240 → 983,240 (**8 blocks**, unchanged) ·
**conformance** 906 / 399 / **0 MISMATCH** ·
**perf** TOTAL 1450.20 → 1443.29 (min of 3 interleaved) — **flat** ·
**corpus** 1.58 → 1.57 s, 0.994x

`str-methods` was the top of the queue (3.18x, 36% of the corpus). Three things
were tried. **Two of them made it slower and are the useful half of this entry.**

### Negative result 1: hand-rolling the ASCII case map is 18x slower

The plan was an ASCII fast path for `lower`/`upper`, on the theory that
`to_lowercase()` pays for Unicode machinery an ASCII string does not need.
`std` already has that fast path, and it is **vectorised**. 200,000 iterations
over a 960-byte ASCII string:

```
s.to_lowercase()                                  17.3 ms
push(b.to_ascii_lowercase() as char) in a loop   306.6 ms
s.to_ascii_lowercase()                            15.3 ms
```

Pushing one char at a time is exactly what the fast path exists to avoid. The
comment in `methods.rs` now carries those three numbers so the idea does not come
back.

### Negative result 2: counting matches to presize `replace` costs more than it saves

`std`'s `str::replace` starts from `String::new()` and grows, so presizing looks
free. It is not: knowing the size needs a **second pass over the receiver**, and
measured that way `str-methods` came out **29% slower** (34.57 → 44.71 ms). At
the sizes strings actually have on this path, a pass costs more than the
reallocations it removes.

### What survived: two early exits that allocate nothing

* `strip`/`lstrip`/`rstrip` when nothing was trimmed — `trim_matches` returns a
  subslice, so equal length is equal content, and the answer is the receiver.
* `replace` when the needle is absent — found with **`find`**, which stops at the
  first match, not with a count. The case that pays for the scan is the case
  that skips an allocation entirely.

Both also match CPython more closely than before: `s.replace('x', 'y') is s` is
True in CPython when nothing matched, and now here too.

### The suite cannot see the win, and the reason is a bias worth naming

`str-methods` is flat, because its receiver is `'  Hello World  '` — a string
where **every** method changes something. That is not what the corpus looks like:
a line that has just come out of `splitlines()` is already stripped, and a
`text.replace(old, new)` needle is often absent. Measured directly, min of 7:

```
                       before    after   cpython
strip-clean-lines       21.34    20.25     19.77   −5.1%
replace-absent           2.36     1.94     16.80  −18.0%
mixed strip+replace     28.60    26.19     20.03   −8.5%
strip-dirty-lines       21.71    21.45     21.18   −1.2%
replace-present         28.33    28.08     24.55   −0.9%
```

The last two are the shapes the suite *does* measure, and they are flat — so
this is not a trade, it is a win on half the distribution and nothing on the
other half.

**No case was added for it, deliberately.** The skill's rule is that a win moving
no row means the suite has a hole — but the hole here is that `str-methods` uses
an unrepresentative receiver, and adding `str-methods-noop` immediately after
optimising the noop path is how a gradient turns into a trophy case. The honest
fix is to decide whether `str-methods` should have a representative receiver,
which renumbers the row and breaks comparability with every entry above. That is
a decision for an iteration that is not also the one that benefits from it.

### And a reading that was noise

A single `--baseline` comparison said the change cost **+30.90 ms**, concentrated
in `dict-set` (+7.10), `membership` (+2.76) and `str-split` (+2.40) — three rows
with no causal path to a change in `methods.rs`. Three interleaved rounds of both
binaries: before 1450.20 / 1483.72 / 1450.47, after 1446.80 / 1456.41 / 1443.29.
Overlapping, and the minimum went the other way. One comparison is not a reading;
this is the third time that sentence has earned its place in this file.

1,875 replace/strip combinations against CPython — receiver, needle, replacement,
count, and the three strip variants with and without an argument — 0 mismatches.

---

## 2026-08-24 · iteration 30 — three passes over the receiver to answer an O(1) question

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1551 loaded, 1305 timed

**bytes** 983,240 → 983,240 (**8 blocks**, unchanged) ·
**conformance** 906 / 399 / **0 MISMATCH** ·
**perf** new row `str-scan` **101.43 → 23.66 ms, 7.79x → 1.81x**; the suite is
32 cases now and **the TOTAL renumbered** ·
**corpus** 1.58 → 1.60 s, 1.015x, inside the deadband

`t.startswith('#')` passes no bounds, and `slice_str` walked the receiver
**three times** before looking at the needle: `chars().count()` to find `n`, then
two `char_indices().nth()` walks to turn 0 and `n` back into byte offsets it
already had. On a 4,000-byte haystack that is ~69,000 instructions to answer a
question about the first character. `line.startswith('#')` over a long line is
an ordinary agent one-liner.

The fix is four lines: no bounds, no work.

### The reason this is worth an entry is the case, not the fix

**No perf case reached this function.** The suite's only method row is
`call-method`, which is `.count('a')` on a six-character string, and `count` did
not go through `slice_str` until iteration 24 put it there. Two scouts found the
defect independently by reading, and neither could show it moving anything —
which the skill has a rule for: *a measured win that moves no row means the suite
has a hole, and the case goes in the same iteration.*

So `str-scan` is new: `startswith` and `find` over a **1,000-byte** haystack,
40,000 times. The length is the point — the defect is linear in the receiver, so
the six-character strings elsewhere in the suite would have hidden it exactly as
they did. Measured on the iteration-29 binary it reads **7.79x**, which would
have made it the second-worst row in the table; on this one it is **1.81x**.

The suite is 32 cases and its TOTAL is not comparable with earlier entries.
Re-recorded: **1470.00 ms against CPython's 783.71, 1.88x**.

This is also a scan removal, which the ledger's iteration-4 negative result says
usually buys nothing. That result was about shortening a **fixed** 39-entry table
scan that was cache-resident SIMD `memcmp`; this deletes three passes whose
length is the caller's data. The distinction is the whole reason the earlier
result did not rule this out.

### And the grid became a test

`tests/test_str_bounds_grid.py` runs the 44,352-cell cross-product from
iteration 24 as a permanent gate — the same program on both interpreters, one
process each, 0.17 s. It is a grid rather than a list of examples because a list
of examples is exactly what failed to find the original bug: the first fix
passed all fourteen chosen cases and still left 609 cells wrong.

On the iteration-23 binary it reports **3,409 of 44,352 cells disagree**, which
is what a regression gate for this function should look like.

---

## 2026-08-24 · iteration 29 — a call built an empty hash table and then grew it

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1551 loaded, 1305 timed

**bytes** 983,240 → 983,240 (**8 blocks**, unchanged) ·
**conformance** 906 / 399 / **0 MISMATCH** ·
**perf** `call-recursive` 62.55 → 55.80, `call-func` 44.44 → 39.52 (both ≈ −11%) ·
**corpus** 1.58 → 1.57 s, 0.994x — inside the deadband, as expected ·
**fuzz** four seeds × 2500

Every call to a Python function allocated a fresh `Rc<RefCell<Map>>`, inserted
its parameters and locals into a table that started **empty**, and dropped the
whole thing at the end. Measured per call on this container, 200,000 iterations,
empty-loop floor subtracted:

```
def f():          178 ns
f(a)              261
f(a, b)           296
f(a, b, c, d)     564
f(a … h)        1,109
```

The 2→4 step is +268 ns and 4→8 is +545 — those are hashbrown capacity steps on
a table whose final size was known before the first insert. That is the
two-phase-allocation trap CLAUDE.md and the skill both name, and it was on the
hottest call in the interpreter.

Two halves, one mechanism:

* **Presize.** `assigned` is already every name the body binds, parameters
  included, computed at definition time. A lambda's `assigned` is deliberately
  empty, so the parameter count is the floor rather than the answer.
* **Pool.** Spent scopes go back to `Interp::scope_pool` with their tables
  intact, beside the chain-vector pool that was already there. `clear()` keeps
  capacity, so a function called in a loop grows its table once for the whole
  run instead of once per call. Capped at 64, like the chain pool, so a deep
  recursion cannot leave the pool holding its depth.

`call-method` did **not** move, which is the right answer and worth stating: it
allocates no scope, so a change here that moved it would have been the optimiser
rather than the code.

### The way this goes wrong is silent

A scope that escaped its frame must never be recycled — a closure handed a
cleared map is a wrong answer, not a refusal. The guard is
`Rc::strong_count == 1` on the frame's own scope, and it is exact for a specific
reason: the chain is cloned at exactly three sites (a nested `def`, a `lambda`,
a generator expression), each cloning every `Rc` in it, and **the crate creates
no `Weak` anywhere**, so the strong count is the whole story. Checked by grep,
not by memory.

`try_borrow_mut` rather than `borrow_mut`: a live borrow here would mean a `Ref`
outlived the frame, and under `panic = "abort"` finding that out by aborting is
not a diagnosis. Declining to recycle is.

Eighteen programs cover the three escape routes, including two that create
closures and then make **300 intervening calls** — cycling the pool many times
over — before reading them back. All 18 agree with CPython (one refuses on call
depth, which is a refusal and not a disagreement). Ten are pinned.

**One fuzz seed is red and it is the known one.** `4242` finds
`1.7976931348623157e308 ** 0.5` off by one ULP — the musl-against-glibc `pow`
difference recorded in iteration 20 — and it reproduces identically on the
iteration-28 binary. Three other seeds × 2500 are clean.

---

## 2026-08-24 · iteration 28 — `join` copied the list it was only reading

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1551 loaded, 1305 timed

**bytes** 983,240 → 983,240 (**8 blocks**, unchanged) ·
**conformance** 906 / 399 / **0 MISMATCH** ·
**perf** `str-join` **76.63 → 57.87 ms** (6.31x → ~4.8x); TOTAL 1514.87 → 1461.01 ·
**corpus** 1.58 → 1.53 s, 0.969x ·
**fuzz** seed 20260824 × 3000, 0 counterexamples

Two wastes, and the larger one was invisible in the code. `iter_collect` on a
list **copies the whole list**, so `''.join(['ab'] * 600000)` moved 24 MB of
`Value` to produce 1.2 MB of answer — a copy nothing reads twice. A list and a
tuple are borrowed in place now; anything else still materialises, because a
generator has to be drained before it can be measured.

The second: `String::new()` doubled its way to the result. `join_parts` sums the
byte lengths in the pass that already has to type-check every element, then
fills a `String` sized exactly. The type check staying in that **first** pass is
not incidental — CPython reports `sequence item {i}` for the first non-str and
produces no output, so finding it before anything is written is what keeps the
error identical.

### The bug this iteration introduced, and how it was caught

The 25-case sweep turned up one difference that was pre-existing: `','.join(5)`
said `'int' object is not iterable` where CPython says **`can only join an
iterable`**. `join` has its own message. Fixing it by wrapping `iter_collect` in
a `map_err` made every case pass — and **broke the generator path**:

```
','.join(str(1//x) for x in [1, 0])
  lypning   TypeError: can only join an iterable
  CPython   ZeroDivisionError: integer division or modulo by zero
```

Every exception the sequence raised *while being drained* became the
not-iterable message. Turning one exception into a different one is the same
class of defect as answering the wrong number, and the sweep did not catch it
because the sweep had no generator that raises. Trying one by hand did.

The remap is on **`make_iter` alone** now, with the draining loop written out so
it cannot be re-collapsed by accident. That also avoids the alternative fix — a
second list of which `Value` variants are iterable — which would have been a
copy of `make_iter`'s own match, free to drift.

Six cases pinned, including both halves of that pair.

---

## 2026-08-24 · iteration 27 — 32,104 syscalls for one one-liner

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1488 loaded, 1272 timed

**bytes** 979,144 → 983,240 (+4,096; **8 blocks**, headroom 65,336) ·
**conformance** 888 / 384 / **0 MISMATCH** ·
**perf** `str-concat` **231.55 → 17.22 ms**; TOTAL 1670.99 → 1475.23 (−11.7%) ·
**corpus** 1.69 → 1.51 s, **0.896x** ·
**fuzz** seed 20260824 × 3000, 0 counterexamples

`str-concat` has been the worst row in the table since the table existed —
31.09x CPython at the last reading — and the standing explanation was the
quadratic: `Value::Str(Rc<str>)` cannot grow, so `s += 'x'` copies the whole
string every time. The standing answer was a growable string representation, the
largest single change on the §4a list.

**It was not the quadratic. It was syscalls.** `strace -c` on
`s += 'x'` twenty thousand times:

```
16,055  mmap
16,047  munmap
```

musl hands a medium allocation straight to `mmap` and gives it back with
`munmap`, and this loop allocates every length from 1 to 20,000 while freeing
the previous one. 32,104 syscalls, 0.69 s of syscall time, for one one-liner.
The copying was never the expensive part.

So `alloc.rs` grew nine more classes: powers of two from 512 B to 128 KiB, over
`System`, cached on free exactly like the small ones. The whole run of lengths
from 2,049 to 4,096 is now one 4 KiB block handed back and forth, so a syscall
happens once per class transition rather than once per allocation:

```
32,104 syscalls  ->  21
```

`realloc` gained the case that falls out of it: **same class in, same class out
returns the pointer**, so growing from 2,049 to 4,096 bytes needs no allocation
and no copy at all. Sound because `dealloc` is later handed the *new* layout,
which maps to the same class.

The ceiling is as deliberate as the floor. Above 128 KiB an allocation goes to
`System` untouched and `realloc` forwards to `System::realloc`, so a `Vec`
doubling toward 8 MiB still gets `mremap` to move a page table instead of a copy
into a block we cached. Losing that is how this change would have made things
slower.

### What it cost

**Bytes:** +4,096, still 8 blocks.

**Memory: nothing measurable.** Power-of-two classes waste up to 2x by
construction and that is a proof, not a measurement — so it was measured. Peak
RSS over five allocation-churn programs, before → after: 22.0 → 22.2 MB,
31.0 → 31.5 MB, and three unchanged at the 8.0 MB floor. CPython is higher on
every one.

`s += 'x'` × 20,000 end to end went **236.4 ms → 18.9 ms**, which is faster than
CPython's 21.8 ms on the same program.

### This demotes the standing branch

The growable string representation has been the named answer to `str-concat`
through this whole ledger. On this evidence it is worth far less than it looked:
the row is 17 ms now, roughly 2x CPython rather than 31x, and what is left of it
*is* the quadratic copy. That is a real cost and a real branch, but it is no
longer the largest single win available and should stop being described as one.

Verified beyond the four gates, because an allocator change earns it: 165
programs straddling **every** power-of-two class boundary and its neighbours
(255/256/257 … 131,071/131,072/131,073 … 1,000,000) in four shapes each —
`'x'*n`, `[0]*n`, `bytes` of n, and a loop growing across the boundary — 0
mismatches; the 216-program small-boundary sweep from iteration 18, still 0; the
44,352-cell string-bounds grid, still identical; 3,000 fuzz programs, 0
counterexamples.

---

## 2026-08-24 · iteration 26 — the JSON decoder answered malformed documents

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1488 loaded, 1272 timed

**bytes** 979,144 → 979,144 (**8 blocks**, unchanged) ·
**conformance** 888 / 384 / **0 MISMATCH** ·
**corpus** 1.69 → 1.59 s, 0.939x ·
**fuzz** seed 20260824 × 3000, 0 counterexamples

Two defects in `json.loads`, the last of the four the iteration-24 reading pass
turned up.

**A raw control character inside a string was accepted.** `json.loads('"a\tb"')`
returned `'a\tb'` at exit 0 where CPython raises `JSONDecodeError: Invalid
control character at: line 1 column 3 (char 2)`. It is invalid JSON by RFC 8259
and `strict=True` is CPython's default — lypning has no `strict=False` to
justify it either.

That is the worst shape a decoder can have, and worse than a plain wrong answer:
a **malformed document is answered**, so a program whose correct outcome is a
`JSONDecodeError` gets a value instead, and the dispatcher never learns there was
anything to route onward. The run-scan stopped at `"` and `\` and nothing else;
it stops at `< 0x20` now. The bound is exact — DEL (0x7f) is legal in a JSON
string and CPython accepts it.

**"Unterminated string starting at" pointed at the end of the scan.** `'"abc'`
reported `char 4` where CPython reports `char 0`, the opening quote. On the one
class of document where the message *is* the answer, it named the wrong place.

Swept over every control character 0x00–0x20 plus DEL, in three positions, plus
the truncated and nested documents: **45 documents, 0 mismatches** — same message
text, same line, same column, same char offset.

**One difference that is left, and is not this:** lypning's traceback names the
exception `JSONDecodeError` where CPython's shows `json.decoder.JSONDecodeError`.
That is the module path of a class lypning has no module for, it is on stderr,
and conformance grades stdout and the exit code. Faking a module path to match a
traceback would be inventing provenance; recorded rather than fixed.

Nine cases pinned, in two new tests: `CASES` asserts lypning *matches* CPython on
stdout, and these assert on a **message on stderr at exit 1**, which is a
different claim. Both assert the oracle still says what the test thinks it says
before checking lypning against it. Five fail on the iteration-25 binary.

---

## 2026-08-24 · iteration 25 — all six case methods disagreed with CPython

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1488 loaded, 1272 timed

**bytes** 979,144 → 979,144 (**8 blocks**, unchanged) ·
**conformance** 888 / 384 / **0 MISMATCH**, coverage unchanged ·
**corpus** 1.69 → 1.59 s, 0.942x ·
**fuzz** seed 20260824 × 3000, 0 counterexamples

The plan was to fix `casefold` and `title`. A per-codepoint sweep of the whole
family — `upper`, `lower`, `casefold`, `title`, `capitalize`, `swapcase`, every
one of the 1,112,064 codepoints, on both interpreters — said **all six**
disagreed, including the two nobody suspected.

Sorting the disagreements apart is the entire content of this iteration, because
they are three different problems and only two of them are lypning's.

### Three logic bugs, fixed

* **`casefold` was `to_lowercase`.** Wrong for 297 codepoints, including the one
  everyone reaches for: `'ß'.casefold()` is `'ss'` and was `'ß'`, so
  `'ß'.casefold() == 'ss'.casefold()` was **False** — and caseless comparison is
  the entire purpose of the method.
* **`title` and `capitalize` uppercased where CPython titlecases.** 135
  codepoints: `'ǅ'.title()` is `'ǅ'` and was `'Ǆ'`; `'ß'.capitalize()` is `'Ss'`
  and was `'SS'`.
* **`swapcase` truncated every multi-character mapping and mapped titlecase.**
  `.next()` on the case iterator: `'ß'` swapped to `'S'` where CPython gives
  `'SS'`, `'İ'` to `'i'` where CPython gives two codepoints, `'ǰ'` to `'J'`. And
  the `else` arm uppercased anything not uppercase, which is wrong for every
  titlecase character — `Lt` is not `Lu`, and `is_uppercase()` is the Uppercase
  *property*, not "has a lowercase mapping". Both fixed exactly; `swapcase` went
  from 217 differing codepoints to 110, and the 110 are §3 below.

`casefold` and `title`/`capitalize` **refuse** rather than answer, because `std`
has no full case folding and no `char::to_titlecase`. A table here would be a
second source of Unicode truth in a runtime whose first one is whatever the
toolchain shipped, and two of those drift apart silently. `docs/SUBSET.md` §7
rule 4 already says what to do: refuse, and the dispatcher gets the real answer
one spawn later.

### The tables are checked against the oracle, not written from a document

`casefold_differs` and `titlecase_differs` are 41 and 18 ranges, **derived by
asking CPython** for every codepoint where the two mappings differ.
`tests/test_method_tables.py` re-derives them from CPython on every run and
compares — 297 and 135, **no missing, no extra**. The two directions get
different messages on purpose: a missing codepoint is a wrong answer at exit 0,
an extra one is only coverage given away.

That is the same discipline `route.rs`'s capability table is held to, and for the
same reason invariant 1 gives: a table edited to describe what someone wished the
runtime did converts a loud failure into a silent one.

### The third problem is not lypning's, and it is not fixed

After both fixes, **every remaining difference in all six methods is the same 55
codepoints**, and they are a Unicode *version* skew:

```
CPython 3.11.15  ships Unicode 14.0.0
rustc 1.94.1     ships a later one
```

U+1C89, U+A7CB, U+A7CC, U+A7D2, U+A7DC and friends are literally **unassigned**
in this CPython's tables and have case mappings in Rust's; U+019B and U+0264
gained uppercase mappings after 14.0. So lypning maps them and CPython does not.

This is a real MISMATCH by invariant 1's definition and it is deliberately left
open, because the honest fix is not obvious in one mechanism:

* Refusing them means embedding "the delta between rustc's tables and CPython
  3.11's", which is exactly the thing that rots — and it rots in the **unsafe**
  direction when the toolchain moves ahead of the list.
* The set is a property of the *pair* of runtimes, and the CPython the dispatcher
  falls through to is whatever is on the machine.
* No corpus program contains one of these 55; they are in Latin Extended-D and
  Cyrillic Extended-C.

**Proposed branch: pin the Unicode version the subset claims.** Either vendor the
case tables for one version and derive every mapping from them, or make the
refusal set a build-time artefact generated by asking the *local* CPython. Both
are multi-step and both change what "lypning agrees with CPython" means, which is
a decision rather than a fix.

Sweeps: 1,112,064 codepoints × 6 methods before and after; the two tables against
CPython; 19 cases pinned in `tests/test_fuzz_findings.py`, 9 of which fail on the
iteration-24 binary. Five of the nineteen assert a **refusal** rather than a
match, which needed a second test — `CASES` asserts lypning agrees, and "must
refuse" is the opposite claim.

---

## 2026-08-24 · iteration 24 — `'Hello'.count('l', 3)` answered 2

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1488 loaded, 1272 timed

**bytes** 979,144 → 979,144 (**8 blocks**, unchanged) ·
**conformance** 888 / 384 / **0 MISMATCH** ·
**corpus** 1.69 → 1.61 s, 0.955x ·
**fuzz** seed 20260824 × 3000, 0 counterexamples

Two defects in the optional `start`/`end` bounds that seven `str` methods take,
found by reading `methods.rs`. Both silent, both at exit 0.

**`count` took its bounds and ignored them.** The arm read `args[0]` and nothing
else, so `'Hello'.count('l', 3)` answered 2 where CPython answers 1. That is not
an edge case — `line.count(',', 1)` is a thing agents type.

**The other six clamped a start that CPython does not clamp.** CPython's
`ADJUST_INDICES` is deliberately asymmetric: a negative `start` folds and floors
at 0 but a positive one is **never capped at `len`**, while `end` is capped at
both ends; then `end < start` is the no-match answer and `end == start` is a real
empty slice. `clamp_index` capped `start` too, which collapsed "past the end"
onto "the empty slice at the end" — so the empty needle was found there:

```
'Hello'.find('', 99)         5      CPython -1
'Hello'.rfind('', 99)        5      CPython -1
'Hello'.startswith('', 99)   True   CPython False
'Hello'.endswith('', 99)     True   CPython False
'Hello'.index('', 99)        5      CPython ValueError
```

`slice_str` now returns `Option<(&str, i64)>` — the slice *and* the character
offset it starts at — where `None` is "no match" and `Some(("", lo))` is the
empty slice. Making those two different **types** rather than the same empty
string is the point; they were the same value, which is why one line was wrong
in six places. `count` goes through the same function, so there is now one
definition of what the bounds mean instead of two. The `find` arm also stops
walking the receiver a second time to recompute the offset — it comes back from
`slice_str`.

### The grid, and the pair that needed it

The first fix was `start > n ⇒ None`, which made all fourteen hand-picked cases
pass. It was **wrong**, and a 33,957-cell grid over receiver × needle × start ×
end × method said so: 609 cells still differed. `'a'.find('', 1, -99)` is -1 —
`end` folds to 0, which is *before* start — while `'a'.find('', 0, -99)` is 0.
`start > n` is just one corner of `end < start`, and no list of examples anyone
would write by hand contains that pair.

Re-run with non-ASCII receivers and needles: **44,352 cells, all identical.**
Fourteen cases pinned in `tests/test_fuzz_findings.py`; 12 fail on the
iteration-23 binary.

Bytes did not move. `corpus-time` came in at 0.955x, outside the deadband and
faster, which is not attributable here: the `find` arm lost a full second walk of
the receiver, but no corpus program is bounds-heavy enough to explain 77 ms, so
read it as the run and not the change.

### Still outstanding from the same reading pass

Two verified, neither fixed here, each its own iteration:

* **`json.loads` accepts raw control characters inside a string.**
  `json.loads('"a\tb"')` returns `'a\tb'` where CPython raises
  `JSONDecodeError: Invalid control character`. A malformed document is accepted
  and answered, so a program that should have gone to CPython gets a result.
* **`casefold` is aliased to `to_lowercase`, and `title` uses `to_uppercase`.**
  `'ß'.casefold()` is `'ß'` where CPython gives `'ss'` — and caseless comparison
  is the entire purpose of `casefold`. `'ǅ'.title()` gives `'Ǆ'` where CPython
  gives `'ǅ'`.

---

## 2026-08-24 · iteration 23 — spending 4 KB of the 45 that iteration 22 freed

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1488 loaded, 1272 timed

**bytes** 975,048 → 979,144 (+4,096; **8 blocks**, headroom 69,432) ·
**conformance** 888 / 384 / **0 MISMATCH** ·
**perf** TOTAL ~1707 → ~1650 ms (−3.3%, bands below) ·
**corpus** 1.69 → 1.65 s, 0.974x

Iteration 18 set `#[inline(never)]` on the allocator's three `GlobalAlloc`
methods and said in as many words that it was a byte decision to revisit when
the budget allowed. Iteration 22 freed 45,056 B. This spends 4,096 of them.

**The measurement is the entry.** A single A/B said −48 ms, or 2.9% — squarely
inside the range where this profile's inlining decisions move on their own
(iteration 15), and exactly the reading the skill says not to believe. So it was
taken its way: **four builds of the unchanged source**, differing only by a
comment appended to an unrelated file, against **three of the changed** one.

```
perf TOTAL, ms
  inline(never)   1690.46   1703.90   1705.45   1726.79
  inline          1634.44   1653.03   1662.16
```

The baseline's own spread is 36 ms — 2.1%, which is why one comparison could
never have settled this. But the bands **do not overlap**: the worst inlined
build beats the best non-inlined one by 28 ms. Real, and about 3.3%.
`membership` (−14.83), `dict-set` (−9.04) and `str-repr` (−4.08) carry most of
it; twelve rows move and none regresses.

The `inline(never)` comment in `alloc.rs` also said the attribute cost "8,192
bytes for no measurable speed". Both halves were wrong by iteration 23: it is
4,096 bytes on this binary, and the speed is measurable when you measure it
properly. The comment now carries the seven numbers instead of the conclusion.

---

## 2026-08-24 · iteration 22 — the error type was in every return value

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1488 loaded, 1272 timed

**bytes** 1,020,104 → **975,048** (−45,056; 8 blocks, headroom 73,528) ·
**conformance** 888 / 384 / **0 MISMATCH** ·
**perf** TOTAL 1742.66 → 1656.86 ms (−4.9%), clean A/B against a HEAD build ·
**corpus** 1.69 → 1.63 s, **0.962x** ·
**fuzz** seed 20260824 × 3000, 0 counterexamples

`R<T> = Result<T, LypningError>` is the return type of essentially every
function here and roughly 790 `?` operators apply it. `LypningError` was an enum
held **inline**, and its largest variant is two `String`s:

```
inline  ErrKind= 48   R<Value>= 48   R<bool>= 48   R<()>= 48
boxed   Boxed  =  8   R<Value>= 40   R<bool>= 16   R<()>=  8
```

So twenty functions returning `R<()>` were moving 48 bytes to say nothing went
wrong, and `R<Value>` carried a discriminant word beside a `Value` that already
has a spare tag. Boxed, `R<()>` is one register, `R<bool>` is two, and
`R<Value>` niche-encodes into `Value`'s own tag and costs **nothing** over a bare
`Value` — which is the row that matters, because `eval` returns one.

`LypningError` is now a newtype over `Box<ErrKind>`. All ~12 constructor helpers
keep their signatures, so none of the ~301 `Err(...)` sites changed and the
`Box::new` is emitted once per helper rather than once per site. Eleven places
matched a variant directly and were rewritten to `e.kind()`.

The trade is **one heap allocation per error constructed**, and it is a good
trade here for a specific reason worth writing down: errors are not control flow
in this runtime. Nothing raises per element; the one builtin that raises per call
(`next()`'s `StopIteration`) raises once per call, not once per item. The 790 `?`
sites pay on every evaluation; the allocation pays when a program is about to
stop.

The win is broad rather than deep, which is what a change to the calling
convention should look like: `list-sort` −32.17 ms, `dict-set` −6.59,
`call-recursive` −4.32, `tuple-unpack` −4.07, `str-of-scalar` −4.47,
`list-append` −3.30, `call-method` −2.86, `enumerate-zip` −2.85, `str-slice`
−2.71, `loop-range` −2.50 — twelve rows past 2 ms and nothing worse.

**Measured as a clean A/B**, not against the running baseline: three iterations
had landed since it was recorded, so HEAD was rebuilt to a separate binary and
both were run under `LYPNING_BIN`. Attributing three changes' worth of
improvement to one of them is the easiest number in this file to get wrong.

### The gate that actually matters here

Four of the eleven rewritten sites *are* the exit-90 contract — `main.rs`'s
`finish`, `embed.rs`'s outcome mapping and `route.rs`'s two error arms. That is
invariant 2's known silent-failure mode: a refusal that becomes a traceback
still compiles, still links, still passes `--version`.

`build --rust` asserts the contract and passed. It was also checked by hand, as a
**differential against the HEAD binary** over 21 cases: refusals by module,
builtin and bigint; a refusal reached after output was already staged; a syntax
error; `sys.exit` with 3, 0, a string and nothing; `raise
NotImplementedError`; ZeroDivisionError; NameError; `except` catching a real
exception and *not* catching a refusal or an exit; four `route` outputs; and
`lypning run` falling through to CPython. **All 21 byte-identical on exit code,
stdout and stderr.**

### The byte number changes what is affordable next

Headroom before a 9th device block went from 28,472 B to **73,528 B**. Two
candidates that were priced as "probably does not fit" — `#[inline]` on the
allocator's `GlobalAlloc` methods (8,192 B, ~3% on the perf TOTAL, iteration 18)
and the `percent_format` rewrite — are now affordable. That was not the reason
for this step and it is the most useful thing it produced.

---

## 2026-08-24 · iteration 21 — thirty-six silent wrong answers about whitespace

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1488 loaded, 1272 timed

**bytes** 1,020,104 → 1,020,104 (**8 blocks**, unchanged) ·
**conformance** 888 / 384 / **0 MISMATCH** ·
**perf** TOTAL 1779.09 → 1738.24 ms ·
**corpus** 1.69 → 1.65 s, **0.974x** ·
**fuzz** seed 20260824 × 2500, 0 counterexamples

A reading pass over the string methods — nominally for speed — turned up a
whitespace disagreement. Written out as a shell diff loop over fifteen candidate
characters × seven methods, it was **36 wrong answers, every one at exit 0**:

* **`str` whitespace is Unicode `White_Space` plus U+001C–U+001F.** Rust's
  `char::is_whitespace` is `White_Space` alone, and lypning used it directly in
  five places. `'a\x1cb'.split()` answered `['a\x1cb']` against CPython's
  `['a', 'b']`; `'\x1ca\x1c'.strip()` kept both; `'\x1c'.isspace()` was False.
  Four characters × five methods.
* **`splitlines` splits on eleven boundaries and this split on three.** `\x0b`,
  `\x0c`, `\x1c`, `\x1d`, `\x1e`, U+0085, U+2028 and U+2029 all produced one line
  where CPython produces two. Three of those are multi-byte, which is why the
  byte-wise scan could not have been extended in place — it walks chars now.

The two sets are **not the same set**, and the fix keeps them apart on purpose: a
tab is whitespace and not a boundary, `\x1f` is whitespace and not a boundary
either, and U+2028 is both. Two more places that look like they want the new
predicate and must not have it: **`bytes`**, whose whitespace is ASCII only
(`b'\x1c'.isspace()` is False in CPython), and **`int()`/`float()`**, whose strip
is `White_Space` (`int('\x1c5')` is a ValueError). Both verified against CPython
rather than assumed, and both pinned.

### Verified over the whole codepoint space, not over a list

A hand-picked list of characters is how this bug survived in the first place, so
the check is a program both interpreters run: for every one of the 1,112,064
codepoints (surrogates excluded), collect the ones where `chr(c).isspace()`, then
where `('a'+chr(c)+'b').split()` is two elements, then `strip`, then
`splitlines`, then `splitlines(True)` — and diff the five answers.

**All five agree exactly.** 29 whitespace codepoints, 10 line boundaries, same
sets and same sums on both. That is the strongest form this verification has:
there is no sixteenth character left to have missed.

23 cases went into `tests/test_fuzz_findings.py`, which asserts against live
CPython. 16 of them fail on the iteration-20 binary and 7 pass — the 7 being the
controls that must NOT change, which is the half of a regression test that
usually goes unwritten.

### And it made things faster

Bytes did not move. `perf` TOTAL went down 40.85 ms and `corpus-time`
1.69 → 1.65 s, **0.974x — outside the deadband, in the good direction**, which
was no part of the intent: `s.split(py_space).filter(non-empty)` turns out to
beat `split_whitespace()`, and the `char_indices` line scan beats the byte scan
it replaced. Recorded because it happened, not claimed as a reason.

---

## 2026-08-24 · iteration 20 — `sum()` was mostly copying, and it could not sum strings

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1488 loaded, 1272 timed

**bytes** 1,020,104 → 1,020,104 (**8 blocks**, unchanged) ·
**conformance** 888 / 384 / **0 MISMATCH**, unchanged ·
**perf** `builtin-sum-len` **38.50 → 4.65 ms**, `list-comp` 39.99 → 32.87,
`genexpr` 43.78 → 40.14; TOTAL 1779.09 → 1745.43 ms ·
**corpus** 1.69 → 1.70 s, 1.005x, inside the deadband

Iteration 18 emptied the allocator out of the profile, so callgrind on
`sum(a) + len(a)` now points somewhere new: `iter_collect` at 16.9% inclusive
over 20 calls. `Interp::iter_collect` on a list is `l.borrow().clone()` — a full
`Vec<Value>` copy — and `sum` was calling it before adding anything.
Decomposed: **23.6 ns per element materialising and dropping the copy against 18
ns doing the additions.** `sum` of a generator measured the same as `list()` of
the same generator, because the buffer *was* the job.

Two layers, both in the one `"sum"` arm:

* **Drive the iterator instead of draining it.** `make_iter` on a list is
  already a live view that copies nothing (`Iter::List(Rc, usize)`); only
  `iter_collect` materialises. This is also the more faithful of the two —
  CPython's `sum` pulls one element at a time.
* **An i64 loop for the case the corpus actually types**, `sum()` of a list of
  ints: a borrowed scan with `checked_add`, bailing to the general path on the
  first non-int or the first overflow. Bailing is free because the loop has no
  effects to undo, and the general path then produces the TypeError or the
  `bigint` refusal that was always the right answer.

The row went **8.3x faster** and is now below CPython. `list-comp` and `genexpr`
moved too — both feed a `sum()` — and nothing regressed. Bytes did not move at
all, which is the pleasant surprise: the fast loop is smaller than the
`iter_collect` call and the `Vec` drop glue it replaced.

### The bug this turned up on the way past

`sum(['a', 'b'], '')` printed **`ab` at exit 0**. CPython raises
`TypeError: sum() can't sum strings [use ''.join(seq) instead]`, and checks the
*start* argument for it before it looks at the sequence at all — `sum([1, 2],
'')` is the same TypeError. `sum([b'x'], b'')` was the same defect in bytes.

A wrong answer, not a refusal, and invisible to every gate: no corpus program
sums with a string start, so `conformance` was green over it, and `perf` does
not evaluate semantics. It is the second correctness bug in this ledger found by
reading a hot path for *speed* (iteration 13 was the first), which is the
argument for the speed queue that has nothing to do with speed.

Reproduced on the iteration-19 binary, so it is not this change's. Both types
now raise CPython's message. `sum(['a', 'b'])` needs no case — the default start
is `0` and `0 + 'a'` already raises what CPython raises.

Checked with a 41-program differential sweep of `sum`: empty, start, mixed
int/float, bools, tuple/range/dict-values/generator/map/filter, list and tuple
concatenation, overflow at the head, the tail and through the start, sets of
ints and of floats, and the not-iterable and missing-argument errors. One
disagreement left in it and it is not about `sum`: `zip.__name__` is an
`AttributeError` at exit 1 where CPython answers, which is the shape
`STR_MISSING` exists to catch — an attribute CPython has must **refuse**, not
raise, because exit 1 is terminal and there is no second chance. Logged, not
fixed here.

### The fuzz gate is red, and it was red before this

`lypning fuzz --seed 3 --iterations 2500` finds two counterexamples. Both
reproduce **identically on the iteration-19 binary** (`LYPNING_BIN=… fuzz --seed
3`), so neither belongs to this step, and seed 20260824 at 3000 iterations finds
zero — which is the useful thing to know about a fuzzer's seed.

1. `1.7976931348623157e308 ** 0.5` → `…97e+154` against CPython's `…96e+154`.
   One ULP, and it is a **libm** difference: CPython's `pow` is glibc's, lypning
   links musl's. Not fixable by being more careful; fixable only by shipping a
   `pow` or by refusing non-integral float exponents, which is a design decision
   and not a bug fix.
2. `{}.pop(['x'])` → `TypeError: unhashable type: 'list'` against CPython's
   `KeyError: ['x']`. CPython short-circuits an **empty** dict before it hashes
   the key, so the same call on a non-empty dict is a TypeError on both. A
   genuine quirk to reproduce rather than a principle.

Each is its own iteration; the four gates and the sweep above are what this step
was accepted on.

---

## 2026-08-24 · iteration 19 — buying the device block back, and the flag that could not

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1430 loaded, 1237 timed

**bytes** 1,049,272 → 1,020,104 (**9 → 8 blocks**) ·
**conformance** 865 / 372 / **0 MISMATCH**, unchanged ·
**perf** TOTAL 1802.73 → 1758.77 ms (−2.4%, inside the band) ·
**corpus** 1.45 → 1.47 s, 1.013x, inside the ±3% deadband

Iteration 18 spent a device block and said the next step was `opt-level = "z"`,
which builds at 996,024 B and would buy it back. **It was measured and rejected.**

### The negative result first

`"z"` against `"s"`, same source, same allocator: `perf` TOTAL **1802.73 →
2371.69 ms, +31.6% slower**, and not concentrated anywhere — `tuple-unpack`,
`enumerate-zip`, `str-slice`, `str-fstring`, `json-loads`, `dict-get` and
`print-lines` each gave up a third or more. A third of the interpreter's
throughput is not a price worth one block. `docs/LYPNING.md` §8 said `"z"`
"buys nothing under the cost model that matters", which was a statement about
bytes and read like a statement about the flag; it now carries the speed number
too.

### What worked instead

The musl targets build a **static-PIE** by default, and the relocations that
costs are a section: `.rela.dyn`, 33,864 B of a 1,049,272 B image. `-C
relocation-model=static` removes it. 1,049,272 → **1,020,104 B**, nine blocks
back to eight, and `perf` went *down* 2.4% rather than up — which is inside the
band where this profile's inlining decisions move on their own (iteration 15) and
is not claimed as a win.

**The claim that did not survive its own measurement.** The first draft of the
config file argued this on two grounds, bytes and startup: ~1,400 relative
relocations processed before `main`, in a program whose whole startup is under a
millisecond, ought to show. It does not. `-c 'pass'`, min of 60 interleaved runs:
**0.387 ms as a PIE against 0.388 ms without.** The comment now says so, because
a plausible mechanism stated as a measured one is how a document starts lying.

### Where the flag lives, which is the whole difficulty

Not in `RUSTFLAGS` inside `build.py`: cargo would then produce a different
binary by hand than through our tooling, and — worse than aesthetics — the two
would not share an object cache, so alternating rebuilds the world. It is
`assets/rust/.cargo/config.toml`, read by cargo from the working directory
upward, which works because `build.py` already runs cargo with `cwd=` the crate
directory.

That puts it in **the wheel's** hands, and the wheel is the shape nobody tests by
accident (CLAUDE.md). Tested on purpose: `pip install` of a built wheel into a
venv, then `LYPNING_HOME=<tmp> lypning build --rust` → **1,020,104 B, 8 blocks**,
byte-identical to the checkout, with the config staged into
`~/.lypning/build/rust/.cargo/`.

`tests/test_packaging.py` grew the guard, and it is a different guard from the
one already there. Every other `package-data` entry fails *loudly* when it goes
missing — cargo cannot build without `Cargo.toml`. This one fails silently: the
build succeeds, all four gates pass, and the binary is one device block larger
for no reason anyone would ever look for. It also names a dot directory, which
is exactly the kind of line a tidy-up deletes.

---

## 2026-08-24 · iteration 18 — the allocator was the interpreter

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1430 loaded, 1237 timed

**bytes** 1,045,176 → 1,049,272 (**8 → 9 blocks**) ·
**conformance** 865 MATCH / 372 UNSUPPORTED / **0 MISMATCH**, unchanged ·
**perf** TOTAL 2164.77 → 1802.73 ms, 2.93x → 2.31x ·
**corpus** 1.69 → 1.44 s, **0.850x**, outside the ±3% deadband

The gradient said `str-split`. Callgrind said something else. On
`t.split()` in a loop, **43.9% of every instruction retired was inside musl's
mallocng** — `alloc_slot`, `nontrivial_free` and two `meta.h` helpers — against
2.6% in `eval`. The ledger's standing answer has been "about a quarter"; on this
program it is closer to a half, and it is not a property of `split`. It is what
every row of the table has been measuring.

So the step is `src/alloc.rs`: a size-classed free-list allocator over
bump-allocated 64 KiB chunks, installed as the binary's `#[global_allocator]`.
Sixteen classes of 16 bytes; a free block threads the next pointer through its
own first word, so a live object carries no header and the class is recomputed
from the `Layout` the caller has to hand back. Anything above 256 bytes or
asking for more than 16-byte alignment goes to `System` unchanged — big buffers
want `mremap` on growth, and a bump allocator's inability to return memory is
worst exactly there.

Every row moved, because every row was paying it. `json-loads` went from 4.47x
CPython to **0.68x** — it is now faster than CPython, having been the fourth
worst ratio in the table. `call-recursive` 13.53x → 7.47x, `str-fmt-pct`
7.15x → 4.86x, `str-split` 7.18x → 3.70x. Nothing regressed.

**It cost a device block, and that is the honest headline.** 1,049,272 B is 696
bytes over 8 × 131,072, so cold first-touch in CheerpX now fetches a ninth block
(`docs/LYPNING.md` §8a). The escape measured but deliberately NOT taken here,
because it is a second mechanism: `opt-level = "z"` with this allocator builds
at 996,024 B, back inside 8 blocks with 52 KiB to spare. That is the next
iteration and it has its own speed question to answer.

`#[inline(never)]` on the three `GlobalAlloc` methods is load-bearing for the
byte count: with `#[inline]` the binary is **8,192 bytes larger** and the perf
TOTAL was 1754 ms against 1807 — a 3% difference, which is inside the band where
this profile's inlining decisions move on their own (iteration 15). Smaller won.
Worth revisiting the moment the byte budget has room.

Beyond the four gates, because an allocator is not the kind of thing conformance
can be trusted alone on: `lypning fuzz --seed 20260824 --iterations 3000` — 3000
generated programs, **0 counterexamples**; a hand-written sweep of 216 programs
straddling every class boundary and the 256-byte cutoff (`'x'*n`, `[0]*n`,
`bytes` of n, for n around 0, 16k±1, 256±1, 4096±1) against CPython — **0
mismatches**; and peak RSS on five allocation-churn programs, which is *lower*
than CPython's on every one (22.1 MB against 28.2 MB building 300,000 strings)
and flat at the 8.1 MB floor on the churn cases, so the free lists are recycling
rather than the chunks accumulating.

**What this does not say.** It is one machine and one libc. The win is against
*musl's mallocng specifically*; glibc's allocator is a different program and this
number is not a claim about it. The i686 build that CheerpX actually loads was
not re-measured here.

---

## 2026-08-21 · iteration 17 — the dispatcher was the one giving the wrong answer

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1037 loaded, 861 timed

Two fixes, and the second is the one that matters.

### Text decoding refuses where it cannot agree

Iteration 16 recorded two places where lypning's decoding disagrees with
CPython's and left them. Both now **refuse**:

* **stdin** — CPython decodes it with `surrogateescape` (PEP 383), so
  `for l in sys.stdin` over `b"ok\n\xff\n"` yields `'\udcff\n'` at exit 0
  where lypning raised `UnicodeDecodeError`.
* **a text file read a line at a time** — CPython decodes a buffered block and
  raises before yielding anything, at a file-relative position; lypning yielded
  the first line and then raised at a line-relative one.

`bytes.decode()` and a whole-file `f.read()` agree exactly on both interpreters
and keep raising, unchanged. Checking the barrier first, as iteration 16 said to:
a refusal reached after output has been committed is already turned into a plain
exit-1 error rather than a routable 90, by both `main.rs` and `embed.rs`. So
refusing here is safe, and the fall-through does the right thing — `lypning run`
now prints CPython's `'\udcff\udcfe\n'`.

### The fall-through defect

Verifying that revealed something worse, **pre-existing**, and reproducible on
the iteration-0 binary:

```
$ printf '2\n3\n' | lypning run -c 'import sys
  for l in sys.stdin: print(int(l) * 10**30)'
                     ← nothing. exit 0.
$ printf '2\n3\n' | python3 -c '…'
2000000000000000000000000000000
3000000000000000000000000000000
```

`cli.cmd_run` passed `stdin=None` to `engines.dispatch`, so every tier
**inherited** the caller's pipe. lypning read both lines, overflowed on the
bignum, and exited 90 — and CPython was then handed a stream with nothing left
in it. Empty output, exit 0, no error anywhere.

**The dispatcher itself produced the wrong answer**, which is precisely the
failure the whole fall-through mechanism exists to prevent. The Rust `lypning
run` captures and replays and was always right; only the Python CLI was wrong.

Why it stayed hidden: a refusal the classifier predicts **statically** never
touches stdin — `import ctypes` routes straight to CPython and lypning never
runs. Only a **runtime** refusal after stdin has been read reaches the bug, and
that is exactly the case iteration 16's new `encoding` refusal created.

Fixed by reading a piped stdin once, before dispatching, and handing the same
bytes to every attempt. A terminal is left alone — reading it would block for
input the program may never want. Pinned in `tests/test_cli.py`, both ways: the
fall-through case, and a run that never falls through, so the capture cannot
break the ordinary path.

conformance 529 / 332 / **0**, bytes 1,045,176, both unchanged.

**Two lessons, both about where to look.** A fix that creates a *new refusal
path* is a fix that exercises the fall-through for the first time in that shape
— so test the fall-through, not just the refusal. And "the dispatcher agrees
with CPython" is a claim no gate in this tree makes: `conformance` runs the
`mixture` arm, but only over corpus programs, and none of them refuses at
runtime after reading stdin.

---

## 2026-08-21 · iteration 16 — a line was copied three times

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1037 loaded, 861 timed

`open(` is in **52%** of corpus programs, and every line read from a file in a
`for` loop was copied three times: a `Vec<u8>` sliced out of the buffer, a
`String` from `decode_utf8`, and then the `Rc<str>` a `Value::Str` is actually
made of. `decode_utf8_rc` does the identical `std::str::from_utf8` check and
lands in the `Rc<str>` directly, so it is one slice and one copy. The error
wording moved into a single `utf8_error` so the two decoders cannot drift.

| | before | after |
|---|---:|---:|
| `for line in open(f)`, 50k lines | 25.55 ms | **20.71 ms** |
| the same file in UTF-8, 20k lines | 10.46 ms | **8.86 ms** |
| `.readlines()` | 34.58 ms | **28.08 ms** |
| `for line in sys.stdin`, 50k lines | 24.69 ms | **21.44 ms** |
| `f.read()` | 2.39 ms | **1.82 ms** |
| a plain loop — the control | 16.84 ms | 17.14 ms |

perf `file-write-read` 4.43x → **3.54x**. conformance 529 / 332 / **0** and
1,045,176 bytes, both unchanged. corpus-time flat within its band.

### A divergence found but not fixed here

Verifying the decode path against CPython on **invalid UTF-8** turned up two
disagreements. Both are **pre-existing** — identical on the iteration-0 binary,
so this change did not cause them — and both are real:

* **stdin.** CPython decodes stdin with `surrogateescape` (PEP 383), so
  `for l in sys.stdin` on `b"ok\n\xff\xfe\n"` yields `'\udcff\udcfe\n'` and
  exits 0. lypning raises `UnicodeDecodeError`.
* **A text file, line at a time.** lypning yields `'ok\n'` and *then* raises, at
  a position counted within the line (5). CPython decodes a buffered block, so
  it raises before yielding anything, at a position counted in the file (8).
  Whole-file `f.read()` agrees exactly, because there the slice and the file are
  the same bytes.

Left for its own iteration rather than half-fixed here. The cheap correct answer
is a **refusal** — `unsupported: encoding` — which hands the program to CPython
and its surrogateescape; the thing to check first is whether the commit barrier
is still armed at that point, because a refusal after output has been committed
is not available. That check is the iteration, not a footnote to this one.

Recorded because a divergence nobody has written down is a divergence that gets
rediscovered. Neither is reachable from the corpus today: nothing in it reads
invalid UTF-8 in text mode.

---

## 2026-08-21 · iteration 15 — a generator step stopped rebuilding itself

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1037 loaded, 861 timed

A generator expression appears in **6.8%** of the corpus, and every element it
yielded allocated four times before evaluating anything:

1. `GenState::placeholder()` built a fresh scope — an `Rc<RefCell<HashMap>>` —
   to fill the `RefCell` while the real state was out;
2. `gen_next` cloned the closure environment into a new `Vec<Scope>`;
3. `gen_step` **deep-cloned the clause**, one allocation per AST node;
4. and then **deep-cloned the element expression**, the same way.

Now: the placeholder's scope is an `Option` (never read — a placeholder is
marked `running` and `done`, and both are checked first), the chain vector comes
from the pool `call_func_inner` already uses, and the AST lives behind `Rc`, so
(3) and (4) are refcount bumps. The AST is still deep-copied **once**, when the
generator is created; moving the `Rc` into `Expr::Comp` itself would remove that
too and is a separate change.

| | before | after |
|---|---:|---:|
| `sum(x*x for x in range(100k))` | 61.93 ms | **26.52 ms** |
| `', '.join(str(x) for x in …)` | 21.4 ms | **11.8 ms** |
| `any(x<0 for x in …)` | 52.3 ms | **21.7 ms** |
| nested `for a … for b …` | 66.5 ms | **24.0 ms** |
| a genexpr created inside a loop | 68.5 ms | 51.5 ms |
| `[x*x for x in …]` — the control | 16.9 ms | 16.6 ms |

conformance 529 / 332 / **0**, bytes 1,045,176, both unchanged. Laziness still
holds where it must: `any(1/x for x in [1, 0])` is `True` on both, never
dividing.

### Distinguishing noise from a regression

`fib(23)` came out **7% slower**, on a change that touches nothing a recursive
call executes. Five hypotheses, each built and measured:

| suspected cause | result |
|---|---|
| a new `thread_local` shifting the TLS block | no — 1.077x before and after removing it |
| the extracted `take_chain`/`give_chain` helpers | no — 1.086x with them inlined back |
| `#[inline]` on those helpers | no change either way |
| the `Expr::Comp` arm widening `eval_inner`'s stack frame | **partial** — `#[inline(never)]` recovered ~2% |
| `new_scope` losing a caller and with it an inlining decision | **partial** — `#[inline]` recovered ~2% |

Net: 49.2 ms → 51.2 ms, about 4%, on deep recursion only; one-argument calls and
the plain loop are flat. Shipped, because 2.4x on 6.8% of the corpus against 4%
on deep recursion is not a close call, and `corpus-time` sits inside its band.

**The method is the transferable part.** The way to know a small regression is
real is to *perturb the baseline build* — rebuild the unchanged source three
more times with a comment added to an unrelated file, and measure the spread:

```
baseline source, four builds:   48.85  49.31  49.55  49.18   (1.4%)
changed source, four builds:    51.39  52.81  52.91  53.41   (3.9%)
```

The bands do not overlap, so it is a regression and not luck. Do this **before**
spending an afternoon on a 3% reading. At `opt-level = "s"` with LTO and
`codegen-units = 1`, a change in one part of the crate moves inlining decisions
in another, and the two have no source-level relationship at all.

---

## 2026-08-21 · iteration 14 — five programs of coverage for zero bytes

**host** 4 cpus, Linux 6.18.44-fc-v21, x86_64 · **corpus** 1037 loaded, 861 timed

`$`, `` ` ``, `?` and a bare `!` cannot begin a token in **any** Python 3
program. CPython answers each with a SyntaxError at exit 1 and empty stdout.
lypning was answering `unsupported: token: byte 0x24`, which is exit 90 — and
exit 90 means *"outside my subset, try the next interpreter"*, when there is no
interpreter for which `$p` is a program.

The corpus has four entries of exactly this shape — `$p`, `$1`, and a Rust
`r#"…"#` paste — shell accidents an agent typed and a capture faithfully
recorded. Every one was costing a process spawn to be told by CPython what
lypning already knew.

One line in `lex.rs`, alongside the `!` case that was already there.

| | before | after |
|---|---:|---:|
| bytes | 1,045,176 (8 blocks) | **1,045,176 (8 blocks)** |
| conformance MATCH | 524 | **529** |
| UNSUPPORTED | 337 | **332** |
| coverage | 60.9% | **61.4%** |
| MISMATCH | 0 | 0 |
| `token` blockers in `--plan` | 4 | **0 — the kind is gone** |

**Five programs for zero bytes, which is the best ratio available.** And nothing
is behind it: a SyntaxError is terminal, not a capability gap, so there is no
second blocker waiting once the first is cleared. That is what makes this
different from a module — `import base64` unblocks four programs and one of them
immediately hits `import re`.

**Where the line is drawn, and why at ASCII.** Not extended to non-ASCII bytes:
Python 3 identifiers may be Unicode, so `π = 1` is a valid program and refusing
it is the correct answer. Pinned both ways in `tests/test_semantics.py` — seven
impossible bytes must exit 1, and four programs *containing* those bytes (in a
string, in a comment, as `!=`, and as a Unicode identifier) must still run.

### General shape

A refusal is free only when the answer is genuinely elsewhere. Where CPython's
answer is *"this is not a program"*, a refusal is a spawn spent to learn nothing,
and the classifier is left believing a capability gap exists where there is only
a typo. Worth a sweep: **every `unsupported` kind should be a thing another
interpreter could actually do.** `token` was not one.

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

### Implications for the gates

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

### The principal finding

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

### The size cliff

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

### Profiling on this container

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

### Why the suite could not see it

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

### The change that was rejected

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

### A MISMATCH found in passing

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
