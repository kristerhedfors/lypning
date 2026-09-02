# Prompting an agent into the subset

**The question.** lypning is affordable because it is small, and it is small
because roughly two-thirds of what a coding agent types already fits. The other
third is the expensive part. So: can the agent simply be *asked* to stay inside
the subset — and if so, what does it cost to ask, how much does it buy, and
where does asking stop working?

This document is the measurement, not the opinion. Everything below is derived
by `study/score.py` from 884 programs that nine differently-prompted Claude Code
agents actually wrote, each one routed by lypning's own parser, executed on the
Rust core, executed on CPython, and graded in the same MATCH / UNSUPPORTED /
MISMATCH vocabulary `lypning conformance` uses.

---

## 1. Measurement

Run on **2026-08-23**, on this container — 4 CPUs, Linux 6.18.44-fc-v21, the
Rust core built and lypning-mp absent. **34 cells** (nine prompt treatments ×
three or four independent replicate agents), **26 tasks** per cell, **884
programs**, 375 of them distinct.

> **Re-verified after the hillclimb work landed.** The engine changed under this
> study between its generation pass and its merge — six correctness defects
> fixed, `for line in sys.stdin` de-quadratified, four characters moved from a
> refusal to a `SyntaxError`. All 884 programs were re-scored against the merged
> engine and **not one row moved**: same route, same verdict, same
> `tier1_win`, on every one. The tier-1 surface the treatments describe did not
> move either — `study/gen_brief.py` regenerates `capability-brief.md`
> byte-identically. The table below is therefore a measurement of both engines.
>
> **2026-09-01:** the tier-1 surface moved for the first time since — the
> seeded-integer subset of `random` landed (`random.rs`), and
> `capability-brief.md` regenerated with one module added and one paragraph
> saying what of it is in reach. Every number in the table below was measured
> against the brief **before** that paragraph and describes the engine before
> `random`; the treatments have not been re-run. Nothing in the 26 tasks
> imports `random`, so the change is to the prompt's length, not its truth.

| id | treatment | n | routes tier 1 | **runs on tier 1** | of the 23 feasible | correct | MISMATCH |
|---|---|---:|---:|---:|---:|---:|---:|
| T0 | control | 104 | 62.5% | **66.3%** | 75.0% | 100.0% | 0 |
| T1 | nudge | 104 | 75.0% | **76.9%** | 87.0% | 100.0% | 0 |
| T2 | runtime-aware | 104 | 96.2% | **88.5%** | 100.0% | 100.0% | 0 |
| T3 | skill | 104 | 77.9% | **81.7%** | 92.4% | 100.0% | 0 |
| T4 | capability-brief | 104 | 93.3% | **89.4%** | 100.0% | 100.0% | 0 |
| T5 | cookbook | 104 | 91.3% | **88.5%** | 100.0% | 100.0% | 0 |
| T6 | brief+cookbook | 104 | 93.3% | **88.5%** | 100.0% | 100.0% | 0 |
| T7 | verify-once | 78 | 97.4% | **89.7%** | 100.0% | 100.0% | 0 |
| T8 | verify-loop | 78 | 94.9% | **88.5%** | 100.0% | 100.0% | 0 |

*"routes tier 1" is the classifier's static answer. "runs on tier 1" is the one
that matters: the program executed on the Rust core, agreed with CPython, and
answered the question that was asked. The two differ in both directions and §6
is about why.*

Read it in this order:

- **Prompting works, and it is worth about 22 points.** 66.3% of what an
  unprompted agent writes runs on the cheapest tier, against 88.5% under the
  best prompts — and that number is not a coincidence, it is the ceiling. Three
  of the 26 tasks cannot be done inside the subset by any natural solution, and
  23/26 is 88.5%. **Six of the nine treatments answer 100% of the 23 tasks that
  are feasible at all.** T4 and T7 read a fraction of a point higher only
  because one replicate each went *through* the ceiling by hand; §8.
- **The cheapest prompt that saturates is a paragraph of motive with no feature
  list at all.** T2 tells the agent that a fast subset runtime tries every
  program first, that a refusal costs a wasted spawn, that correctness outranks
  everything — and *explicitly declines to say which features are in*. It
  reaches 100% of the feasible set, matching the generated capability tables
  (T4), the rewrite cookbook (T5), both together (T6), and both plus the engine
  in the loop (T7, T8).
- **Nothing above that paragraph pays for itself.** T4 through T8 add 3.3 KB to
  26 KB of prompt, and two of them add a tool loop with real latency, for a
  difference inside the replicate noise (§3, Table 2).
- **No prompt ever bought a wrong answer.** 884 programs, **0 MISMATCH and 0
  incorrect results under CPython** — including the treatments that push hardest
  against the subset's edges. Every prompt in `study/prompts/` says correctness
  outranks the tier, and the data says the agents believed it.
- **The shipped skill is the second-weakest prompt in the study.** §4.

---

## 2. Method

**Nine treatments**, a ladder from no information to the engine answering for
itself. Each is a file in `study/prompts/`, and each generator agent was told to
read exactly the files its treatment names and nothing else.

| id | treatment | what the agent was given |
|---|---|---|
| T0 | control | nothing at all — the tasks only |
| T1 | nudge | one sentence: *prefer plain, simple Python* |
| T2 | runtime-aware | the motive and the cost, and an explicit refusal to list features |
| T3 | skill | `SKILL.md` as `lypning install` writes it, verbatim |
| T4 | capability-brief | the exact modules, builtins, methods and refusals, generated from the engine's own tables |
| T5 | cookbook | rewrite rules only — *instead of X, write Y* |
| T6 | brief+cookbook | T4 and T5 together: the maximal static prompt |
| T7 | verify-once | T6, plus `lypning route -c` and **one** revision per task |
| T8 | verify-loop | T6, plus `route` and the engine itself, iterated until the program runs |

`study/prompts/capability-brief.md` is **generated** by `study/gen_brief.py`
from `builtins.rs`, `methods.rs`, `modules.rs` and `route.rs`. A hand-written
list of what the subset supports goes stale on the next commit, and a treatment
prompt that lies about the engine measures the lie rather than the prompt.

**Twenty-six tasks**, in `study/tasks.jsonl`, written to the shape the corpus
already has — stdin transforms, JSON, small file work, text munging, path
arithmetic. Fourteen of them *tempt* a specific import the subset does not have
(`re`, `collections.Counter`, `csv`, `math.isqrt`, `pathlib`, `datetime`,
`hashlib`), and are nevertheless solvable without it. Those are where a prompt
either works or does not. Every task is deterministic: fixed stdin, fixed argv,
fixed fixture files, no clock and no randomness.

**Three axes, measured separately**, by `study/harness.py`:

* `correct` — CPython ran the program and it printed the task's expected stdout.
  Measured on CPython so that a subset refusal can never be misread as a wrong
  answer.
* `route` — what `lypning route --json` predicts. One parse, no execution.
* `verdict` — what actually happened on the Rust core: MATCH, UNSUPPORTED (exit
  90, checked to have written nothing to stdout), or MISMATCH.

The headline number, `tier1_win`, is the conjunction: MATCH **and** correct.

**Expected outputs are derived, not typed.** `study/bless.py` runs each task's
reference solution under CPython and records what it printed; it then runs the
same reference on the Rust core, and a task is `tier1_feasible` only if *that*
matched. The 88.5% ceiling in §1 is that measurement, not an assumption.

**The battery runs behind the net.** These are agent-written programs asked to
open, write and list files. Every run gets its own temp cwd with the task's
fixtures, a separate one per engine so the second cannot read back what the
first wrote; a program naming an absolute path is skipped rather than run; and
the whole pass is bracketed by a `git status` snapshot. CLAUDE.md invariant 4
applies here exactly as it applies to `conformance` and `bench`, and §6 has the
incident where it earned its keep.

---

## 3. Spread across treatments

| id | treatment | replicates | tier-1 rate per replicate | mean | spread |
|---|---|---:|---|---:|---:|
| T0 | control | 4 | 65.4% · 65.4% · 65.4% · 69.2% | 66.3% | 3.8 pp |
| T1 | nudge | 4 | 73.1% · 76.9% · 84.6% · 73.1% | 76.9% | 11.5 pp |
| T2 | runtime-aware | 4 | 88.5% · 88.5% · 88.5% · 88.5% | 88.5% | 0.0 pp |
| T3 | skill | 4 | 84.6% · 80.8% · 80.8% · 80.8% | 81.7% | 3.8 pp |
| T4 | capability-brief | 4 | 88.5% · 92.3% · 88.5% · 88.5% | 89.4% | 3.8 pp |
| T5 | cookbook | 4 | 88.5% · 88.5% · 88.5% · 88.5% | 88.5% | 0.0 pp |
| T6 | brief+cookbook | 4 | 88.5% · 88.5% · 88.5% · 88.5% | 88.5% | 0.0 pp |
| T7 | verify-once | 3 | 88.5% · 92.3% · 88.5% | 89.7% | 3.8 pp |
| T8 | verify-loop | 3 | 88.5% · 88.5% · 88.5% | 88.5% | 0.0 pp |

*The two tool-in-the-loop treatments had three replicate agents each rather than
four; every rate listed is one independent agent's own 26 programs.*

**The one-sentence nudge is the least reproducible prompt in the study** —
11.5 percentage points between its best and worst replicate, three times the
spread of anything else. "Prefer plain, simple Python" leaves the agent to
invent a subset, and four agents invent four different ones. Every treatment
that saturates has a spread of 0.0 pp across four independent agents: once the
prompt says enough, the outcome stops depending on which agent read it.

---

## 4. Causes of failure

| id | treatment | the three commonest blockers |
|---|---|---|
| T0 | control | `run: module` ×31, `run: os-listdir` ×4 |
| T1 | nudge | `run: module` ×18, `run: os-listdir` ×4, `run: bigint` ×2 |
| T2 | runtime-aware | `run: bigint` ×4, `run: os-listdir` ×4, `run: module` ×4 |
| T3 | skill | `run: module` ×12, `run: os-listdir` ×4, `run: dict-method` ×3 |
| T4 | capability-brief | `run: os-listdir` ×4, `run: module` ×4, `run: bigint` ×3 |
| T5 | cookbook | `run: bigint` ×4, `run: os-listdir` ×4, `run: module` ×4 |
| T6 | brief+cookbook | `run: bigint` ×4, `run: os-listdir` ×4, `run: module` ×4 |
| T7 | verify-once | `run: bigint` ×3, `run: os-listdir` ×3, `run: module` ×2 |
| T8 | verify-loop | `run: module` ×4, `run: bigint` ×3, `run: os-listdir` ×2 |

At the ceiling the residue is identical and irreducible: four `bigint`, four
`os-listdir`, four `module` — one per replicate on each of the three infeasible
tasks. That is the floor, not a failure.

**The shipped skill is the interesting row.** T3 hands the agent `SKILL.md`
exactly as `lypning install` writes it, and it reaches 81.7% — behind the
motive-only paragraph by seven points and behind the generated brief by eight.
Its residual failures are informative: 4× `isqrt` reaching for `math`, 3×
`unique-sorted` reaching for `dict.fromkeys`. The skill is a document about
**working on lypning** — the gates, the routing score, the traps already paid
for — and it is very good at that. It is not a document about *writing a program
that stays in the subset*, and this study is the first thing that has measured
the difference. The cheapest fix is not a longer skill: it is the T2 paragraph,
which is 744 bytes.

> **The skill has since been changed in response.** `SKILL.md` now carries a
> §1a — the motive, the correctness rule, the ten rewrites that account for
> nearly all of the gap, and the three run-time refusals no import can dodge.
> **T3's 81.7% describes the text as it was when it was measured**, and that text
> is kept verbatim at `study/prompts/skill.md` so the row stays reproducible. The
> new section has not been measured; re-running T3 against it is the obvious next
> experiment, and it is one path in `study/treatments.json`.

**Per task**, control against the cheapest static prompt that solves it:

| task | tempts | tier-1 feasible | control | cheapest static prompt that gets it | engine in the loop |
|---|---|---|---:|---:|---:|
| `word-freq-top3` | collections.Counter | yes | 0.0% | T1 (100%) | 100.0% |
| `csv-column-sum` | csv | yes | 0.0% | T2 (100%) | 100.0% |
| `csv-group-max` | csv, collections | yes | 0.0% | T2 (100%) | 100.0% |
| `extract-ints` | re | yes | 0.0% | T2 (100%) | 100.0% |
| `char-histogram` | collections.Counter | yes | 0.0% | T1 (100%) | 100.0% |
| `isqrt` | math.isqrt | yes | 25.0% | T2 (100%) | 100.0% |
| `listdir-filter` | glob, pathlib | **no** | 0.0% | — | 0.0% |
| `big-factorial` | — | **no** | 0.0% | T4 (25%) | 0.0% |
| `sha256-abc` | hashlib | **no** | 0.0% | — | 16.7% |
| *the other 17* | — | yes | 100.0% | T1 (100%) | 100.0% |

Six tasks carry the entire effect. Every one of them is a case where the
idiomatic Python is an import and the subset-clean Python is four more lines —
`Counter` becomes `d.get(k, 0) + 1`, `csv.reader` becomes `line.split(",")`,
`re.findall(r"\d+", …)` becomes a digit-run loop, `math.isqrt` becomes a binary
search. Seventeen tasks are unaffected because the natural answer was already
inside the subset, which is the same reason the unprompted baseline is 66% and
not 20%.

The full table, all 26 rows, is `study/data/tables.md`.

---

## 5. Cost, in milliseconds and in lines

Coverage is a proxy. What a user gets is a cheaper session, and the two are not
the same number: a program that stays in the subset runs **in the dispatcher's
own process** with no second spawn, while one that leaves it pays a wasted
classification plus the full CPython price. `study/timing.py` times each cell's
26 programs through the mixture and through CPython, min of 3, arms interleaved
per program:

| id | treatment | mixture | cpython | vs cpython |
|---|---|---:|---:|---:|
| T0 | control | 169.4 ms | 360.5 ms | 0.470x |
| T1 | nudge | 141.8 ms | 360.7 ms | 0.393x |
| T2 | runtime-aware | 60.6 ms | 340.2 ms | **0.178x** |
| T3 | skill | 98.9 ms | 329.9 ms | 0.300x |
| T4 | capability-brief | 63.9 ms | 330.4 ms | 0.193x |
| T5 | cookbook | 71.8 ms | 330.2 ms | 0.218x |
| T6 | brief+cookbook | 67.8 ms | 336.5 ms | 0.202x |
| T7 | verify-once | 57.4 ms | 338.5 ms | **0.169x** |
| T8 | verify-loop | 62.6 ms | 344.0 ms | 0.182x |

**The cost effect is larger than the coverage effect.** Coverage rises by a
factor of 1.33 (66.3% → 88.5%); the mixture's cost falls by a factor of 2.8
(0.470x → 0.169x). That is the fall-through's arithmetic: the 34% of programs
the unprompted agent sends onward do not cost a little more, they cost
everything — a wasted classification plus a 10.9 ms CPython spawn — so removing
two-thirds of them removes most of the bill. Wall-clock on a shared box: quote
the ratio, re-measure the milliseconds, and keep it out of CI for the same
reason `lypning bench` is kept out.

**And the price the agent pays** — mean program length, in lines:

| id | T0 | T1 | T2 | T3 | T4 | T5 | T6 | T7 | T8 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| mean lines | 3.5 | 6.7 | 5.8 | 4.3 | 4.9 | 5.5 | 4.7 | 5.1 | 4.6 |
| mean bytes | 104 | 134 | 124 | 111 | 116 | 121 | 111 | 133 | 108 |

Staying in the subset costs about **1.4 lines per program** at the saturating
prompt — `Counter` unrolled into a dict loop, `csv.reader` into a `split`. It is
a real cost and it is small. T1 is the longest not because it is the most
constrained but because it is the vaguest: an agent told only to "prefer simple
Python" writes out loops it did not need to.

---

## 6. Findings in the machinery rather than the prompts

### 6a. Every one of the classifier's false negatives was `os.path`

35 of the 884 programs — 4.0% — were sent past tier 1 by the classifier and then
ran on tier 1 correctly anyway. **All 35 were two-level module attribute calls**:
`.getsize()` ×16, `.splitext()` ×15, `.basename()` ×4, and nothing else.

`route.rs`'s `Expr::Attr` arm resolves a module attribute only when the base is a
bare name, so `os.getenv` is decided correctly and `os.path.basename` — whose
base is itself an `Expr::Attr` — falls through to the method table, misses, and
is blocked as `method: .basename()`. The engine implements all three; the
classifier cannot see that it does. This is a LATE route, which is a budget
rather than a gate, and it is inside the 22 LATE that `lypning conformance`
already reports.

It is worth fixing for a reason this study can measure directly: **it degrades
the programs agents write.** T7 and T8 agents were told to trust `lypning route`,
and did — one recorded *"first used os.path.getsize → cpython; re-read the file
instead"*, and another replaced `os.path.splitext` with a hand-rolled `rfind`.
Both rewrites are worse code, and both were made to satisfy a tier the original
already met. A classifier that under-reports its own engine does not merely cost
a spawn once it is in a prompt loop; it teaches.

The change is local to `walk_expr` in `route.rs` — resolve a dotted base against
`modules::MODULES` before falling through to the method table. It is deliberately
**not made here**: it would invalidate this study's own measurements halfway
through, and the evidence that it is safe (35 for 35 MATCH on tier 1) is exactly
what a maintainer needs to make it with `lypning conformance` in front of them.

### 6b. Six percent of refusals are invisible to any prompt

56 programs — 6.3% — routed to tier 1 and then refused at run time: 33
`os-listdir`, 23 `bigint`. No parser can see an integer overflow coming and no
prompt can prevent one, because the refusal is a property of the *data*, not of
the program text. This is the gap between the two leftmost data columns of §1's
table, and it is why the study grades on execution rather than on routing. A
study that measured only `lypning route` would have reported T2 at 96.2% instead
of 88.5%.

### 6c. Using lypning as a library is invisible to lypning's capture

Both capture feeds watch for a **process**: the shim on `$PATH` catches an exec
of `python3`, and the `PreToolUse` hook catches a Bash command that mentions one.
A host that links `liblypning` and calls `lypning_run()` spawns nothing. Five
hosts can run ten thousand programs and the corpus will not grow by one — and
the Python host is the worst case, because it *is* a python process, so the shim
logs the driver script and none of the hundreds of programs it ran. One sighting
where there should be hundreds reads as a working feed.

§7 is what this study did about it. The durable fix belongs in the C ABI, where
all five hosts would inherit it at once.

---

## 7. The five hosts, and the capture loop

The study's programs were then run through **every host the C ABI has** — C,
C++, Rust, Node and Python — over one shared program set, with each host given
its own copy of the set and each program run with its own entry directory as the
working directory. Drivers are in `study/hosts/`; none of them falls onward to
CPython, because the question they answer is what the subset itself takes.

```
host          programs   ran   refused   other
c-embed            393   341        52       0
cpp-embed          393   341        52       0
rust-embed         393   341        52       0
node-embed         393   341        52       0
python-embed       393   341        52       0
```

**Five bindings, one ABI, byte-identical answers** — including on the refusal
path, which is the half that has only ever broken silently. That agreement is
the point of having one ABI and four conveniences over it, and it is asserted
here rather than assumed.

Each driver appends the shim's own record shape to `$LYPNING_LOG`, with `shim`
naming the host instead of an interpreter, which is what makes `lypning harvest`
merge it (see `study/hosts/capture.h` for why that is the host's job today and
where it should live tomorrow). Together with one pass of all 884 programs
through the installed `python3` shim — the feed that needs no new code at all —
the run produced:

```
python3        884     the capture shim: install it, run programs, they are logged
c-embed        393     ┐
cpp-embed      393     │ liblypning, in-process, one record per run, written by
rust-embed     393     │ the host because no feed can see an in-process call
node-embed     393     │
python-embed   393     ┘
               ----
               2849    records in /root/.lypning/invocations.jsonl
```

`lypning harvest --export` published 393 distinct programs to
`tests/corpus/sightings/lypning-prompting-study.jsonl`; `lypning harvest` folded
them in. **The corpus went from 1037 programs to 1430.** Re-running the export
on the merged tree wrote `0 new, 393 total, unchanged`, which is the purity
`harvest.py` promises, checked rather than trusted.

> **The corpus is now partly this study's own output, and that must not be
> quoted as a field number.** On the merged tree, `lypning conformance` read
> **61.4%** tier-1 coverage over the corpus before the fold and **69.9%** after
> it, over 1237 programs instead of 861. Nothing about the engine changed between
> those two runs. The 8.5 points are 393 programs written by agents under nine
> prompt treatments, six of which were designed to produce subset-clean Python —
> so folding them in raises the number the corpus exists to report honestly. The
> sightings are one file and can be excluded by name. Anyone quoting corpus
> coverage as evidence about what agents type in the field should exclude
> `tests/corpus/sightings/lypning-prompting-study.jsonl` first, and
> `study/baseline.py` says so at the top for the same reason.

---

## 8. The ceiling, and the two programs that exceeded it

Three tasks are outside the subset for any natural solution: `listdir-filter`
(`os.listdir` order is the filesystem's, and is refused), `big-factorial` (30!
leaves the signed 64-bit range) and `sha256-abc` (no hash primitive). 23 of 26 is
the 88.5% every saturating treatment reached.

Two agents went through it anyway, and both are worth reading before anyone
writes a prompt like these.

Given the capability brief and nothing else, one agent computed 30! by
implementing **decimal bignum multiplication**:

```python
d = [1]
for i in range(2, 31):
    c = 0
    for j in range(len(d)):
        v = d[j] * i + c
        d[j] = v % 10
        c = v // 10
    while c:
        d.append(c % 10)
        c //= 10
print("".join(str(x) for x in reversed(d)))
```

And with the engine in the loop, one agent answered `sha256-abc` by
**implementing SHA-256 from scratch** — 54 lines of round constants, message
schedule and 32-bit rotations masked into i64 — rather than `import hashlib`.

Both are correct. Both MATCH. Both are, as engineering, a bad trade: a one-line
`hashlib` call became 54 lines of hand-rolled cryptography to avoid a fall-back
that would have cost about eleven milliseconds. **The subset is a routing
decision, not a challenge**, and a prompt that does not say so will occasionally
be read as one. The treatment prompts in `study/prompts/` all put correctness
above the tier and none of them produced a wrong answer; none of them says *do
not reimplement a standard algorithm to stay in the subset*, and they should.

---

## 9. Limitations

* **The control cell is contaminated, and is anchored rather than trusted.**
  Every generator agent ran inside this repository, whose `CLAUDE.md` announces
  that the project is about a Python subset. Agents were told to ignore it; that
  is a request, not a guarantee. So T0 is checked against something the
  contamination cannot reach: the corpus itself, captured from real agent
  sessions doing unrelated work — and the anchor is **two** numbers, not one,
  because the corpus grew between them:

  | corpus, routed before this study's fold | programs | to tier 1 |
  |---|---:|---:|
  | as it stood on 2026-08-23 morning | 842 | **62.7%** |
  | after the hillclimb work landed | 1037 | **55.6%** |

  T0 routed 62.5% and ran 66.3%, so it sits on the first anchor and seven points
  above the second. The move is not noise and it is not the agents: the 195
  programs the hillclimb session added are largely `transcript`-sourced (the
  corpus went from 20 transcript entries to 215), and a session doing *engine
  work* types longer, heavier Python than a session doing data wrangling. That is
  worth knowing on its own — **"what agents type" is not one distribution, it is
  a function of what the session is doing** — and it means the control cell is
  bracketed by the field rather than confirmed by it. It remains the best
  available evidence that the `CLAUDE.md` leak did not do much, and it is weaker
  evidence than one number would have looked.
* **Twenty-six tasks are not the distribution.** They were written to the
  corpus's shape and deliberately loaded with fourteen import temptations, which
  is a harder battery than a real session. A corpus one-liner is a median of six
  lines and is often a `sed`-shaped transform that was never in danger.
* **One model, one day, one machine.** Four replicates per static treatment is
  enough to see that the saturating prompts have zero spread and the vague one
  has 11.5 points; it is not enough to separate 88.5% from 89.4%.
* **Coverage saturated, so the ladder's top is untested.** T4 through T8 all sit
  on the ceiling. A battery with a higher ceiling would be needed to find out
  whether the tool loop is worth its latency, and §5's timing suggests it is
  close.
* **lypning-mp was absent.** Everything above is the Rust core against CPython.
  Programs that fell through would in a complete build land on the middle tier
  rather than on CPython, which changes §5's milliseconds and none of §1's
  coverage.

---

## 10. Reproduction

Everything is committed: the tasks with their derived expectations, all nine
prompt texts, all 884 generated programs, all 884 scored rows, and the tables.

```bash
python3 study/gen_brief.py         # regenerate the capability brief from the engine
python3 study/gen_taskbrief.py     # regenerate the brief the agents were handed
python3 study/bless.py             # re-derive expected stdout + the tier-1 ceiling
python3 study/score.py             # re-score every program; writes study/data/results.jsonl
python3 study/score.py --report    # re-render the tables without re-running anything
python3 study/timing.py --repeat 3 # re-time the mixture against CPython, per treatment
python3 study/baseline.py          # the field baseline — read its docstring first
```

Regenerating the *programs* means re-running nine treatments' worth of agents,
which is not a script here; `study/collect.py` turns whatever they return into
`study/data/programs.jsonl`, and everything downstream is deterministic from
there. The capture half:

```bash
lypning install                                   # hooks, skill and the python3 shim
export LYPNING_LOG=~/.lypning/invocations.jsonl
python3 study/capture_pass.py                     # every program through the shim
python3 study/hosts/prepare.py                    # lay out the shared program set
make -C study/hosts && (cd study/hosts/run_rust && cargo build --release)
sh study/hosts/run_all.sh                         # all five hosts, into the same log
lypning harvest --export && lypning harvest       # sightings, then the corpus
```

`study/hosts/run_all.sh` gives each host its own copy of the program set in a
temp directory. Run `git status` afterwards anyway.

## The paragraph the harness adapters actually inject

`src/lypning/assets/prompt/routing.md` is one asset used by all three harness
adapters (`docs/HARNESSES.md` §5), so the text cannot drift into three variants.
It is T2 — the 744-byte motive-only treatment that saturated this battery — plus
two things T2 did not contain: an instruction to *type* `lypning -c`, and a
sentence naming the exit-90 refusal so the agent does not treat one as a failure
to work around.

**It is unmeasured, and the numbers above do not transfer to it.** T2's 88.5%
was obtained under *automatic* routing, where the agent had to type nothing at
all; this paragraph asks it to type something, which is a different ask and
could plausibly cost coverage. Nothing here says what it costs. Measure it
before quoting a rate for it.

What does carry over from this battery is the shape, and it is why `SKILL.md` is
not what gets injected: handed over verbatim, the skill scored 81.7%, seven
points behind the bare paragraph, because it is a document about working *on*
lypning rather than a motive for using it. The two clauses that must survive any
edit are *correctness outranks the tier* and *a fall-back is free*.

**Files.**

| path | what |
|---|---|
| `study/tasks.jsonl` | the 26 tasks, their fixtures, their reference solutions and derived expectations |
| `study/prompts/` | all nine treatment texts, verbatim as the agents received them |
| `study/treatments.json` | the treatment index: which prompt files, and whether tools were allowed |
| `study/data/programs.jsonl` | every generated program, kept whatever it turned out to be |
| `study/data/results.jsonl` | one scored row per program: route, verdict, correctness, refusal |
| `study/data/tables.md` | the four tables in §1, §3, §4 in full |
| `study/data/timing.json` | §5's milliseconds |
| `study/data/baseline.json` | §9's field baseline |
| `study/harness.py` | running and grading, behind the net |
| `study/hosts/` | the five host drivers and the capture record they write |
| `tests/test_study.py` | that the artifacts above stay consistent with each other and with the engine |
