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
