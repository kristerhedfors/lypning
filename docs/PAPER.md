# Agent Python is not Python: profiling what coding agents actually execute, and building an interpreter chain for it

**Draft, 2026-08-31.** Every number in this paper is dated and names the run that
printed it, per this project's third invariant: a remembered number is a wrong
number. The corpus grows with every session, so a reader reproducing this work
will get different counts and should quote their own. Harnesses for every
measurement are in `study/paper/` and `study/monty/`.

**Status.** This is a systems report written to submission structure, not a
submitted paper. The literature positioning began with a specific defect: the
survey passes that produced its citations turned out to have no network access,
so every attribution was model recall — and one author list was fabricated. The
twelve numbered references in §10 have since been checked against primary sources
(2026-08-31); the remaining recalled attributions are still marked `[unverified]`
inline and must be checked before submission anywhere.

---

## Abstract

Coding agents execute Python constantly, but they do not execute the Python that
Python implementations are optimized for. We instrumented a coding harness at the
point of interpreter invocation and captured **2,906 programs** (loaded
2026-08-30) at the moment they were handed to `python3`. The resulting profile is
extreme in a way that determines implementation strategy: the median program is
**384 bytes and 74 AST nodes**, 0.3% define a class, `match`, walrus and `async`
are absent entirely — and the median program spends **0.019 ms executing** against
**16.83 ms of process spawn, interpreter startup and its own imports** — of which
10.66 ms is bare interpreter startup. Execution is 17.3% of aggregate wall time
and essentially none of the median program's.

We then benchmark five implementations — CPython 3.11, PyPy 7.3.20, MicroPython,
Monty 0.0.21, and our own tiered engine — on this corpus with one instrument,
measuring cold start, parse, execute, process overhead, memory, compatibility
rate, and end-to-end wall clock. Two results stand out. **PyPy — widely reported as the
fastest Python on conventional benchmarks, though we ran no such sweep
ourselves — is the slowest engine on this workload**
(3.0–3.1× CPython's wall over 1,990 programs, across two sweeps) and
produces **39 silent divergences** from CPython, its largest family (10 of 39)
being the one the profile predicts: without refcounting, `open(f,"w").write(...)` is not promptly
flushed — and 45.8% of these programs call `open()`, **96.0% of them via the
bare, refcount-dependent idiom**. PyPy's
per-program penalty is **majority fixed startup, not unamortized warmup** — it
pays +16.22 ms over CPython on `print(1)`, before any JIT can warm. And the honest competitor to a new engine
is not another interpreter but a **warm pool** — a pre-warmed CPython forking per
program — which we measure rather than dismiss, and which **beats our own deployed
chain** (2.04× vs 1.50× over cold CPython). We then *build* the composition
those numbers point at — tier 1 with the pool as its backstop — and measure it
at **1.77× with 745 of 745 programs correct**, the best arm on both axes, while
reporting that our own arithmetic had projected ≈3× and was wrong.

Our own contribution is narrower than "a faster Python" and we state it as such:
a characterization of a workload we are not aware of being measured at this
granularity before, and the design point that
workload forces — **tiers that are separate processes cannot deoptimize**, so
tier selection must be a static admission test, and the refusal channel becomes
the system's principal interface rather than an internal signal. The measurement that beat us pointed past us, so we followed it: the
pool-backstopped chain is built (§5.6) and is the fastest correct arm we have.
Building it also refuted a claim we had published — a warm pool is *not*
correct by construction, because it freezes the environment at start and a fork
cannot re-seed hash randomization.

---

## 1. Introduction

An agent editing a repository runs Python the way a shell user runs `grep`: in
short, disposable bursts, dozens of times per task. Recent measurement of agent
behaviour bounds this from the outside — an analysis of 7,745 agent traces from
SWE-bench submissions reports an average of 8.8 test runs per task
([arXiv 2606.26978](https://arxiv.org/abs/2606.26978)) — but says nothing about
what those invocations contain, because it observes the loop, not the programs.

This matters because Python implementations are optimized against a workload that
is nearly the opposite of this one. `pyperformance` and its predecessors measure
programs that run for seconds; PyPy's tracing JIT, CPython's specializing
interpreter, and every tiered runtime we are aware of assume a program lives long
enough to repay the cost of observing it. If the programs an agent actually runs
finish in microseconds, that assumption is not merely weak — it inverts, and the
optimization becomes a regression. We show that it does.

The paper makes five claims, in decreasing order of confidence:

1. **An empirical characterization** of Python emitted by a coding agent at
   *execution* granularity — captured as the interpreter receives it, not as
   source committed to a repository. We are not aware of a prior published
   characterization at this granularity. (§3)
2. **A five-implementation benchmark** on that corpus under one instrument,
   decomposing cold start, parse, execute, process overhead, memory,
   compatibility and end-to-end wall clock — including a warm-pool baseline,
   because a warm interpreter is the real alternative to a new one. (§5)
3. **A design point**: when tiers are separate processes and programs live for
   microseconds, on-stack replacement, deoptimization and profile-guided tier-up
   are all unavailable, so admission must be a static test decided before
   execution, and the refusal signal must become a machine-checked external
   contract. (§6)
4. **Two negative results worth the space**: on five of six sustained-compute
   workloads nothing beats CPython, and the one engine that wins one loses the
   next by 23×; our deployed configuration is **1.50–1.77×
   faster end to end, not an order of magnitude**; and **a pre-warmed CPython
   fork pool beats it** (2.04×) while being correct by construction. We report
   the modest number and the loss because they are what a user would find. (§5.4)
5. **The composition, built rather than projected**: a chain whose backstop is
   the warm pool measures 1.77× with 745/745 correct — best on both axes — and
   the arithmetic estimate that motivated it (≈3×) was too high by 40%. (§5.6)

None of the underlying mechanisms are new, and §7 concedes the prior art before
positioning against it.

---

## 2. Capture method and sampling frame

Capture is a `PreToolUse` hook plus a `python3` shim on `$PATH`. When the agent
runs Python, the program text is recorded to a session-local JSONL file before
the interpreter sees it; `lypning harvest` then redacts, deduplicates and
content-addresses the entries into a corpus. The hook never blocks and never
fails a session: every path prints `{"continue":true}` and exits 0, including its
own failures, so a capture defect loses an entry rather than an agent turn.

This records the exact string that was about to be executed, which is a stronger
observation than scraping committed source — the programs here include the ones
that failed, the ones that were immediately rewritten, and the throwaway probes
that never reach a repository.

**The frame, stated as a machine spec.** One capture harness (Claude Code), one
model family, one user's task mix, captured across 20 distinct dates ending
2026-08-30, on one machine. **2,906 entries** at the load that produced this
paper's tables. This is not "what coding agents generate." It is what this
harness emitted under this workload, and every claim below is scoped to that.

**The corpus is running code, and it is run behind a net.** These are real
programs from real sessions, so the corpus is full of one-liners that edit `src/`
and `docs/`. Every entry runs in its own temp cwd, entries naming an absolute
path are skipped rather than run, and each run is bracketed by a `git status`
snapshot that restores and reports anything that changed anyway. This is a net,
not a sandbox: it cannot undo a write outside the repository, it only makes the
next occurrence loud. It exists because the first measurement runs rewrote 34
tracked files.

---

## 3. The profile

### 3.1 Static shape

Parsing all 2,906 entries (2,869 parse; 37 are syntax errors — themselves a
finding, since an agent does emit invalid Python):

| | median | p90 | max |
|---|---:|---:|---:|
| bytes | **384** | 2,371 | 21,834 |
| lines | **10** | 47 | — |
| AST nodes | **74** | 272 | 4,328 |

Feature presence, as a percentage of the 2,869 parsed programs:

| feature | % | feature | % |
|---|---:|---|---:|
| loop (`for`/`while`) | **48.6** | `with` | 2.5 |
| comprehension | 23.8 | decorator | 0.4 |
| function definition | 10.3 | **class definition** | **0.3** |
| `try`/`except` | 6.9 | `yield` | 0.2 |
| `lambda` | 5.7 | `global`/`nonlocal` | 0.1 |
| f-string | 5.2 | `match`, walrus, `async` | **0** (exact) |

The zeros are exact, not rounded: a direct AST count over all 2,906 entries
(2026-08-31) finds no `match` statement, no walrus operator, and no `async`
construct anywhere in the corpus.

The distribution is the design document. Half of these programs loop, but only
one in ten defines a function and three in a thousand define a class. An engine
that implements loops, comprehensions, and the builtin data types — and refuses
classes, generators and `async` — is not a crippled Python; on this population it
is most of one.

Top imports (occurrence counts): `json` 711, `sys` 680, `re` 415, `lypning` 242,
`subprocess` 234, `os` 148, `pathlib` 136, `io` 131, `collections` 129. These
count import *statements*, not programs, and the two differ: `lypning` occurs 242
times across 235 distinct programs (§8). We do not substitute one for the other
anywhere below.

Top call sites, counted statically — these are **call sites, not dynamic call
counts**, which we did not measure: `print` 5,126, `open` 2,041, `len` 1,388,
`isinstance` 760, `repr` 436, `sorted` 407, `range` 372. Put differently,
**1,314 of the 2,869 parsed programs (45.8%) call `open()`.** (An earlier draft
said 49.0%, from a substring search for `open(`; that over-counts `.open(` and
`reopen(`. The AST-counted figure is the correct one and we report it in place of
our own first number.) Agent Python is I/O code with a little computation
attached — and §5.2 shows this single fact predicts where a competing
implementation breaks.

One refinement matters more than the headline count. Of those 1,314 programs,
only **61 (4.6%) use `with open(...)`**; **1,262 (96.0%) contain at least one
bare `open()`** whose file object is closed by refcounting rather than by a
context manager. That idiom is the one CPython makes safe and no other
implementation is obliged to.

### 3.2 Where the time goes

For every program CPython runs cleanly, we decompose one `python3 prog.py` by
timing `compile()` and `exec()` *inside the child* and subtracting from the
parent's spawn-to-reap wall. Over **765 programs** (2026-08-30):

| component | median per program | share of summed wall |
|---|---:|---:|
| process spawn + interpreter startup + the program's own imports | **16.83 ms** | **78.9%** |
| parse (`compile()`) | 0.773 ms | 3.8% |
| **execute (`exec()`)** | **0.019 ms** | 17.3% |
| total wall | 17.84 ms | 100% |

Read the two columns together, and note they disagree on purpose. The **median**
program executes for 19 microseconds — startup outweighs its computation by
roughly 900×. The **aggregate** execute share is 17.3% because a small heavy tail
of genuinely compute-bound programs dominates the sum. Both are true and the
paper needs both: the median says optimizing the interpreter loop is pointless
for a typical program, the aggregate says a minority of programs is where all
the actual computation lives. **676 of 765 (88.4%) execute in under 1 ms; 96.3%
in under 10 ms.**

*What that first row is not.* It is spawn-to-reap minus in-child `compile()` and
`exec()`, so it carries process spawn **and each program's own imports** — not
bare interpreter startup, which §5.3 measures directly at 10.663 ms for
`python3 -c 'print(1)'`. The ~6.2 ms difference is per-program import cost, which
is precisely what §5.4's warm pool later amortizes away. Where this paper says
"startup" against 16.83 ms it means the whole pre-execution cost; where it needs
bare interpreter startup it uses §5.3's number.

*The median column does not sum, and is not meant to.* 16.83 + 0.773 + 0.019 =
17.62 against a reported total of 17.84: each cell is an independent median over
the same 765 programs, and the total row is the median of the summed wall, not
the sum of the medians.

*Denominator note.* This subset is 765, while the sweep in §5 uses 745. The
difference is the invocation shape: here programs run as a file (`prog.py`), so
`__file__` exists; in §5 they run via `-c`, and a handful of programs that
inspect `__file__` succeed in one shape and not the other. Both subsets are
drawn from the same 1,990 graded candidates (2,906 loaded, minus 755 naming
absolute paths, minus 161 nondeterministic).

---

## 4. Design

Three tiers, in separate processes: a Rust subset interpreter, MicroPython, then
CPython as the always-correct backstop. A tier that cannot faithfully run a
program **refuses**: exit code 90, exactly one
`<engine>: unsupported: <kind>: <detail>` line on stderr, and nothing at all on
stdout. The dispatcher then hands the same program text to the next tier. Any
other non-zero exit is the *program's own* and is passed through untouched and
never retried — a dispatcher that retried on a generic failure would re-run a
program that had already half-completed.

Two properties do the load-bearing work.

**The commit barrier.** Because a refusal means re-execution from the top, an
abandoned attempt must leave no trace. Tier 1 stages this process's stdout and
file writes and discards them on refusal, so a refused run is a no-op *with
respect to what the barrier covers*. It covers this process's writes and standard
output. It does **not** unwind a child process, a network call, or a signal — and
`import subprocess` occurs 234 times in the corpus, so the gap is not
hypothetical — though note that tier 1 refuses `subprocess` at admission, so
those programs are not themselves the exposure. The exposure is an uncoverable
effect reachable from what tier 1 *does* admit, and we have not measured how
often a refusal follows one. The
barrier makes refusal safe, not omnipotent.

**The contract is asserted, not assumed.** A change that turns a refusal into a
traceback still compiles, still links, still passes `--version`, and still answers
correctly for every program the tier admits. This property has only ever broken
silently, so it is checked against the freshly built binary before a build may
report success.

We should be precise about one thing the contract does not give us. Exit 90 is not
reserved by POSIX, and a corpus program could in principle exit 90 and write a
matching line to stderr. The conjunction (exit 90 ∧ exactly one matching stderr
line ∧ empty stdout) makes collision unlikely, not impossible. It is a
low-collision encoding — and the rate is now measured rather than waved at:
across all 1,990 graded programs run under plain CPython (2026-08-31), **zero**
exited 90 at all, and zero produced the full refusal signature. On this corpus
the collision rate is 0/1,990; the encoding remains fallible in principle.

---

## 5. Evaluation

All measurements on one container, 2026-08-30, CPython 3.11 as reference with
`PYTHONHASHSEED=0` and `LC_ALL=C.UTF-8`, each program in a fresh temp cwd.
Engines: **CPython 3.11**, **PyPy 7.3.20** (Python 3.11.13), **MicroPython**
(32-bit), **Monty 0.0.21** (in-process, its native shape), and **lypning** in two
configurations — tier 1 alone, and the full chain, which is what a user runs.

Two disclosures about the reference arm, since we beat it. The CPython arms
invoke the interpreter binary directly (`sys.executable`), **not** through the
`python3` shim of §2, so the baseline carries none of our overhead; each arm's
exact command line is in `study/paper/`. And we did **not** ablate CPython's
startup — no `-S`, `-I` or `-X importtime` breakdown of the 10.663 ms — and a
`-S` baseline would move our headline ratio. CPython 3.11 is also several releases behind on
precisely the axis we measure.

### 5.1 Compatibility

One sweep, one reference run per program, all engines graded against it. Of 1,990
graded programs, **745 run cleanly** under CPython in the sandbox; the other 1,245
fail for both reference and engine (they need a real repository, real arguments,
or the network). We grade BOTH-FAIL on the coarse criterion that both sides
failed — we do **not** require that they failed the same way, which is a
limitation of the instrument. For calibration: an engine that unconditionally
exited 1 would score 1,245 BOTH-FAIL and zero divergences here. The BOTH-FAIL
column measures what the grader cannot see, not agreement.

Correctness counts are identical in both sweeps; wall clock is given for each.

| engine | MATCH | BOTH-FAIL | UNSUPPORTED | LOUD-ERROR | **SILENT-DIFF** | wall 08-30 / 08-31 |
|---|---:|---:|---:|---:|---:|---:|
| CPython 3.11 | 745 | 1,245 | 0 | 0 | 0 | 26.8 / 30.8 s |
| PyPy 7.3.20 | 706 | 1,244 | 0 | 0 | **39** | 79.3 / 96.2 s |
| MicroPython | 611 | 1,240 | 64 | 6 | **64** | 2.9 / 3.3 s |
| Monty 0.0.21 | 275 | 1,245 | 0 | 447 | **23** | 6.9 / 8.4 s |
| lypning tier 1 | 480 | 1,245 | 264 | 0 | **1** | 2.7 / 3.4 s |
| **lypning chain** | **744** | 1,245 | 0 | 0 | **1** | 15.2 / 17.4 s |

(PyPy also has 1 REF-FAIL-ENGINE-OK and MicroPython 5 — programs where the engine
succeeds and CPython does not. We count these separately rather than as matches.)

On the 745-program clean subset: the chain answers 744 identically to CPython;
tier 1 alone answers 480 (64.4%) and refuses 264 (35.4%).

**Calibration: CPython disagrees with itself.** To scale the divergence counts,
we ran CPython 3.13.7 as an *engine* against the 3.11 reference over the same
1,990 programs (2026-08-31): **736 MATCH, 9 SILENT-DIFF**, 0 loud errors, 1
case succeeding where 3.11 fails. Two minor versions of the reference
implementation itself diverge silently on 9 programs — mostly changed error
messages and formatting details. On this instrument, lypning tier 1 (1
divergence) tracks CPython 3.11 more closely than CPython 3.13 does; PyPy's 39
is ~4× the same-language version drift, MicroPython's 64 ~7×. This is the
fairest yardstick we have for what "compatible" can even mean across
implementations.

**Reproducibility.** This sweep was run twice on the same machine with the same
instrument, on 2026-08-30 and 2026-08-31, the second after a harness change —
that is repetition, not independence that gives every child an explicit
EOF on stdin (without it, corpus programs calling `sys.stdin.read()` block on the
harness's inherited stdin — a real irreproducibility we found and fixed). **Every
correctness count above was identical across both runs.** No graded outcome moved
across that fix — the affected programs were already failing on both sides — so
it removed a class of harness hang and wall-clock noise, not a grading error. The wall-clock column
was not: CPython's sweep moved from 26.8 s to 30.8 s under different machine
load. We therefore report timing as ratios, which were stable — the chain came
out at 1.76× and 1.77× CPython on the two runs — and treat absolute walls as
conditioned on the run that printed them.

**The one silent divergence is ours and we do not round it away.** Tier 1 and the
chain each produce one: `1.7976931348623157e308 ** 0.5` yields
`1.3407807929942597e+154` against CPython's `…596e+154`, a musl-libm `pow`
last-ULP difference, ledgered by identity. This falsifies, at n=1, any claim that
the admission test is conservative in one direction only. The accurate statement
is: admission is one-directional *by construction over the syntactic and
import-level subset it tests*, with residual floating-point library divergence
tracked separately in a ledger. A guarantee with a ledger attached is weaker than
a guarantee, and we would rather say so than define the exception away.

### 5.2 Where the competition breaks — and why the profile predicted it

**PyPy's 39 divergences, classified by hand (2026-08-31), revise our own first
framing down.** An early draft called one root cause "dominant"; the full
classification does not support that word. The split: **10** file-finalization
(including four near-duplicate program texts), **7** set iteration order, **4**
call-signature differences (keyword arguments and arity PyPy accepts where
CPython rejects), **4** error-message detail, **4** singletons (NaN identity
across distinct literals, iterator type names, a dict-view operation, stdlib
source introspection), and **10** large differential probe grids whose first
divergence sits beyond our 200-character capture window — most themselves
signature-, order- or error-text probes. File finalization is the *largest*
family at 26%, and the only one that corrupts **data** rather than presentation
or introspection detail; "dominant" it is not. A caveat that cuts both ways:
many probe-grid programs are this project's own conformance probes, exquisitely
sensitive to any implementation difference — a corpus without them would show
fewer divergences in every family.

The file-finalization mechanism itself stands exactly as documented: PyPy does
not refcount, so
a file object is not finalized at the moment its last reference drops. The idiom
`open(f,"w").write(...)` — which CPython flushes and closes immediately — leaves
data unwritten under PyPy, and a subsequent read in the same program sees an
empty or stale file. Observed, verbatim:

| program shape | CPython | PyPy 7.3.20 |
|---|---|---|
| write a CSV, reopen it, sum a column | `42` | `0` |
| write `x`, append `y`, read lines back | `['x', 'y']` | `['y']` |
| `hashlib.md5(open("f.bin","rb").read())` | hash of the content | hash of an **empty file** |

This is not an obscure corner, and the profile predicted the exact shape of it.
§3.1 measured that 45.8% of parsed programs call `open()` and that **96.0% of
those use the bare idiom** rather than `with open(...)`. PyPy's documentation
states the consequence plainly: because its collector is not refcounting, "files
(and sockets, etc) are not promptly closed", and for files opened for writing
"data can be left sitting in their output buffers for a while, making the on-disk
file appear empty or truncated" [10]. The workload is 96% composed of the idiom
that triggers it.

The minimal reproduction is four lines, and the repair is the line an agent
would have to already know to write (2026-08-31):

```python
open("data.csv", "w").write("name,qty\na,2\nb,40\n")     # CPython: sum: 42
with open("data.csv") as f: rows = [l.strip().split(",") for l in f][1:]
print("sum:", sum(int(r[1]) for r in rows if len(r) > 1))  # PyPy:    sum: 0
```

Rewriting only the first line as `with open("data.csv","w") as f: f.write(...)`
makes PyPy agree. So this is not a PyPy defect to be fixed but a documented
design consequence — and the finding is that the population of programs coding
agents emit is almost entirely composed of the idiom it punishes. The remaining PyPy divergences are set and
dict-view iteration order, and PyPy accepting keyword arguments CPython rejects
(`" a ".strip(chars=None)`).

MicroPython's 64 divergences are a different family — `bool` not a subclass of
`int`, set ordering, `random` seeding, sort stability, `int('1_')` accepted — and
are why the chain's classifier routes around it rather than trusting it.
Monty's 23 include `round(2.675, 2)` → `2.68` and `9007199254740993/3` losing
precision; its 447 LOUD-ERRORs are unimplemented constructs, which is a refusal
in effect if not in form.

### 5.3 Cold start, memory, and sustained compute

Startup, spawning `print(1)`; and peak RSS, which is dominated by the ~8.5 MB
measurement floor of the spawning parent and therefore separates nothing:

| engine | spawn `print(1)`, median (min) | on-disk runtime | peak RSS |
|---|---:|---:|---:|
| MicroPython | **0.362 ms** (0.289) | — | at floor |
| lypning (tier 1) | **0.553 ms** (0.385) | 1,024,208 B | at floor |
| CPython 3.11 | 10.663 ms (10.080) | — | at floor |
| **PyPy 7.3.20** | **26.882 ms** (25.627) | ~148 MB installed | — |
| Monty (warm in-process) | 0.04 ms | 22,064,856 B | floor + ~1 MB |
| lypning (in-process lib) | 0.05 ms | — | — |

40 spawns each, 2026-08-31. Ablations run after review (same day, 40 spawns
each): `python3 -S` starts in **8.67 ms** — the one-flag baseline recovers ~25%
of CPython's startup, and every ratio in this paper shrinks accordingly against
a user willing to forgo site-packages; we did not sweep the corpus under `-S`,
where programs needing site-packages would fail. And the "newer CPython is
faster" worry inverts on this container: the system **CPython 3.13.12 starts in
13.8 ms — slower than 3.11's 11.6 ms** measured back-to-back (a standalone
3.13.7 build: 13.1 ms), and neither 3.13 build has the experimental JIT compiled
in (`PY_ENABLE_EXPERIMENTAL_JIT` unset). Our 3.11 reference is the *faster*
CPython available on this machine.

This table also carries the paper's cleanest causal result: **PyPy costs +16.22 ms more than CPython to run `print(1)`** — a program
with no loop to trace and nothing to compile. That is fixed interpreter startup,
and it is present before any JIT question arises. PyPy's per-program penalty depends on which
arm you take it from, and we give the range rather than the flattering end:
32.9 ms from the 2026-08-31 sweep, 26.4 ms from 2026-08-30, and **20.9 ms** from
§5.4's clean-subset table (38.03 − 17.10). The fixed 16.22 ms is therefore
between **49% and 78%** of the penalty depending on the arm. "Roughly half" —
which an earlier draft wrote — is the single most favourable reading available,
and it is the one that leaves the most room for a warmup story. On the
clean-subset arm, which is the one where every program actually runs, fixed
startup is closer to four fifths of it.

On **sustained compute**, over six workloads first validated to produce
byte-identical output on every engine (medians of 5 interleaved rounds,
2026-08-30, reported in full in `docs/COMPARISON.md`), nothing beats CPython on
five of the six: lypning 1.9–4.5× slower, Monty 1.9–4.5× slower. MicroPython is
the exception and is bimodal — 1.28× *faster* on integer loops, 23× slower on
dict churn.
Callgrind instruction counts partially reorder the wall ranks — Monty executes
0.84× CPython's instructions on an integer loop and still loses on wall — so
these costs are dispatch- and memory-bound. **None of these engines is a compute
accelerator.** They are startup plays, which is why §3.2 is the paper's load-
bearing measurement.

### 5.4 End to end, and the warm-pool baseline

The obvious objection to building an engine is that the cost we are attacking is
interpreter *startup*, and the standard answer to startup cost is to not pay it —
keep an interpreter warm and fork per program, as every notebook-kernel agent
already does. That baseline is the real competition, so we measure it rather than
argue with it: a pre-warmed CPython that forks per program, each child chdir'd
into a fresh temp cwd, compiling and executing the program text it is handed.

Best of 2 rounds over the 745 clean programs, 2026-08-31, all arms interleaved
in one process on one machine:

| configuration | total | per program | vs cold CPython | answers |
|---|---:|---:|---:|---|
| lypning tier 1, cold spawn | 1,993 ms | **2.67 ms** | **6.39×** | 64.4%; refuses 35.4%, 1 wrong |
| **CPython warm fork pool** | 6,248 ms | **8.39 ms** | **2.04×** | 100% — it *is* CPython |
| lypning chain, cold spawn | 8,476 ms | 11.38 ms | 1.50× | 100%, 744/745 identical |
| CPython, cold spawn | 12,740 ms | 17.10 ms | 1.00× | reference |
| PyPy 7.3.20, cold spawn | 28,333 ms | 38.03 ms | **0.45×** | 706/745; 39 silently wrong |

**The warm pool beats our deployed chain, and we are not going to bury that.**
A pre-warmed CPython that forks per program serves this corpus at 8.39 ms against
the chain's 11.38 ms, and it answers every program correctly *because it is
CPython*. On both axes a reader should care about, it wins. Part of its advantage
is structural and worth naming: the forked child inherits the parent's
`sys.modules`, so a program that does `import json` pays nothing for it while
every cold-spawn arm pays the import each time. (`json` appears in 711 import
statements corpus-wide and `re` in 415 — occurrence counts over all 2,906
entries, not program counts over the 745 measured here.)

**Where the chain loses, program by program (2026-08-31).** A mean win can hide
regressions a user feels one at a time, so we paired each program's chain wall
against its cold-CPython wall in the same sweep: the chain is *slower* on **216
of 745 programs (29.0%)** — median paired delta **−10.56 ms** (a win), p90
**+1.70 ms**, worst single regression **+173 ms**. The shape is the expected
one: admitted programs win big, refused programs lose small — but a user whose
workload happens to be refusal-heavy will feel the 29%, and this table is the
honest price list.

What the pool costs is not visible in this table. It is a resident daemon: memory
held between invocations, a lifecycle to supervise, crash recovery, and a harness
change to route through it. Our chain is a drop-in binary that requires no daemon
and no harness modification, and it works in settings a pool does not — where
each invocation is independent, in a different container, or under a different
user. That is a real engineering trade, not a performance win, and we present it
as one.

The measurement also points somewhere better than either arm, and an earlier
draft of this paragraph got the arithmetic wrong in our own favour. Two
corrections, both from the table above.

First, **2.67 ms is not the cost of serving a program.** It is 1,993 ms / 745,
an average over 480 answers *and* 264 cheap refusals, so it understates the cost
of an answered program and cannot be spliced against the pool's 8.39 ms.

Second, the chain's fallback is not a single cold CPython spawn. If it were,
the chain would cost 1,993 + 264 × 17.10 = **6,507 ms**; it measured **8,476 ms**.
The true per-refused-program cost is (8,476 − 1,993)/264 = **24.6 ms**, because a
refused program pays the tier-1 spawn it already paid, *then* the tier-2
MicroPython spawn, *then* CPython — three spawns, not one. §4 declares three
tiers and this is where the third one shows up in the wall clock.

**The tier-2 ablation a reviewer asked for, run (2026-08-31).** With the
MicroPython binary moved aside — the degrade-to-not-built path the chain must
survive anyway — the chain over the same 745 programs answers **exactly as
correctly: 744 matches, 1 ledgered divergence**, at 13.46 ms/program in a single
round against 11.38 best-of-2 with tier 2 present. On this subset tier 2
contributes speed only (it serves many refusals at a 0.36 ms spawn instead of
CPython's 17 ms), and none of its own 64 known divergences reach the chain's
output — the classifier routes around them, which this ablation now demonstrates
rather than asserts. The wall comparison is single-round and should be read as
"same order, tier 2 pays for itself", not to three figures.

With those corrected, the composition estimate is still favourable and now
honest: a chain whose backstop is the warm pool would pay the tier-1 pass on
every program plus the pool on the refused ones,
(1,993 + 264 × 8.39)/745 ≈ **5.65 ms**, or 3.03× cold CPython — better than the
pool's 2.04× and the chain's 1.50×, and worse than the 3.6× the naive splice
implied. We have not built it, so this is arithmetic on measured arms and not a
measurement; the experiment is the obvious next one.

**Invocation weighting, computed after a reviewer demanded it.** The
distinct-program sweeps under-state what a session pays, and §8's dedup analysis
says in which direction: sessions re-run the simple programs. Weighting each
graded program's measured wall (08-31 sweep) by its capture-log invocation count
— 6,171 invocations over the 1,990 graded entries — moves every arm:

| engine | distinct-weighted | invocation-weighted |
|---|---:|---:|
| **lypning chain** | 1.77× | **2.35×** |
| lypning tier 1 | 8.95× | 11.74× |
| Monty 0.0.21 | 3.65× | 6.90× |
| PyPy 7.3.20 | 0.32× | 0.32× |

"The number an agent session pays" is the right-hand column, and it is *better*
for us than the number we had been quoting: the programs agents repeat are
disproportionately the ones tier 1 serves. For once the omission ran against us.

The number we measured on what we actually ship is otherwise the **chain: 1.50×
on the clean subset, 1.76–1.77× distinct-weighted across the two full sweeps** —
in-sample, on a subset selected for still running with its repository removed
(§8), which is the direction that flatters us — with 744 of 745 clean
programs answered identically to CPython. Tier 1's 6.39× is a configuration
nobody deploys alone, and quoting it as the headline would be dishonest.

---

### 5.5 The Monty stack, warm shape for warm shape

A review of an earlier draft flagged one asymmetry: we had measured Monty in its
warm in-process shape but lypning only as a cold-spawned process, although a
warm lypning arm — the `liblypning` C ABI — exists and had been timed two tables
up. The parity run closes it (2026-08-31, `study/paper/warm_parity.py`): four
deployment shapes over the same 745 clean programs, each program in a fresh temp
cwd, graded and timed by one instrument, best of two interleaved rounds.

| deployment shape | per program | vs cold CPython | match | silent-diff | loud/refused |
|---|---:|---:|---:|---:|---|
| **liblypning, warm in-process** | **2.23 ms** | **8.05×** | 480 | 1 | 262 refused, 2 errored |
| Monty 0.0.21, warm pool | 9.41 ms | 1.91× | 275 | 23 | 447 errored |
| warm chain (liblypning → cold CPython on refusal) | 12.60 ms | 1.43× | **742** | 1 | 2 errored |
| CPython, cold spawn | 17.96 ms | 1.00× | 745 | 0 | — |

Read the table with its asymmetries named, because the arms do different work.
Substrate against substrate, liblypning completes the sweep in 2.23 ms/program
against the Monty pool's 9.41 — but lypning's average is earned partly by
*declining* 262 programs cheaply while Monty attempts everything, so the honest
per-answered-program bound is ≈3.5 ms against ≈25 ms (attributing each arm's
entire wall to its answers). On correctness the gap needs no qualification:
480 matches with 1 silent divergence against 275 with 23. And the earlier
concession does **not** fully reverse: the only warm lypning shape that answers
*every* program — the warm chain, 12.60 ms — is still slower on wall than
Monty's pool (9.41 ms). What the extra 3.2 ms per program buys is 742 correct
answers instead of 275 correct plus 447 errors and 23 silent wrongs; whether
that trade wins depends on what an error costs the caller. One further shape
caveat: "warm pool" for Monty means its client/worker checkout-and-feed, which
carries dispatch cost the C-ABI call does not, so part of the substrate gap is
harness shape rather than interpreter speed.

The instrument also caught a defect of ours, which we report rather than
absorb: the library arm turns **2 programs** into loud errors that the spawned
tier-1 binary correctly *refuses* (its spawn-shape verdict on the same subset is
480/264/0/1). An in-process embedding that disagrees with its own binary on 2 of
745 programs has a bug on one side or the other, and until it is root-caused the
library arm's correctness must be quoted as 480/262/**2**/1, not the binary's
cleaner line. Those 2 errors also propagate into the warm chain (742 rather than
744 matches), because an error — unlike a refusal — does not fall through.

What the table cannot show is the two stacks' failure economics, and here we
must be careful about what is measured and what is not. Measured: the error
*count* each substrate hands its caller on this population — 447 for Monty,
0 for the cold chain, 2 for the warm chain. Not measured: what an error costs
the caller. In the CodeAct deployment an error returns to the language model,
and a model turn is a modeled cost (we did not drive ADK-Rust with a live
model); in the lypning deployment a refusal is answered inside the stack in
tens of milliseconds. That advantage also belongs to the *chain architecture*
rather than to the interpreter — nothing prevents a Monty deployment from
adding its own CPython fallback on error, at which point the comparison
collapses to the substrate rows above. The two systems also remain different products:
Monty's sandbox, resource limits and snapshot/resume are capabilities lypning
does not have and does not claim; `docs/COMPARISON.md` treats that side fully.

### 5.6 The pool-backstopped chain, built and measured

Two sections of this paper projected a composition and declined to claim it:
tier 1 serves its share far below the warm pool's per-program cost, so a chain
whose *backstop* is the pool rather than a cold CPython spawn should beat both.
It is now built (`src/lypning/pool.py`, `lypning pool serve`, opt-in via
`LYPNING_POOL`) and measured rather than estimated (2026-08-31,
`study/paper/pool_chain.py`, best of 3 interleaved rounds over the same 745
clean programs, each in a fresh temp cwd, graded against a per-program CPython
reference):

| arm | per program | vs cold CPython | correct |
|---|---:|---:|---:|
| **pool-backstopped chain** | **14.85 ms** | **1.77×** | **745 / 745** |
| cold-spawned chain (what we ship) | 17.41 ms | 1.51× | 744 / 745 |
| warm CPython fork pool alone | 18.76 ms | 1.40× | 745 / 745 |
| CPython, cold spawn | 26.27 ms | 1.00× | reference |

The composition is the best arm on both axes in the same run: fastest, and the
only fast arm that answers every program. Of its 745 answers, 481 come from
tier 1 in-process and 264 from the pool. It has no silent divergence at all —
not even the ledgered musl `pow` ULP, because tier 1 here is the host-linked
library rather than the musl-static binary, so the one difference the shipped
cold chain carries is absent by construction from this shape.

**The arithmetic estimate was too optimistic, and by a lot.** §5.4 projected
≈3× from (1,993 + 264 × 8.39)/745; the measurement says 1.77×. Three things the
arithmetic ignored: the tier-1 pass is paid on *all* 745 programs including the
264 it refuses, the pool leg now also pays socket round-trip and environment
forwarding, and this run's machine was more loaded than §5.4's (its cold-CPython
baseline is 26.27 ms/program against 17.10 there — which is exactly why this
paper reports ratios and quotes absolute walls only with their run). The
qualitative claim survives; the magnitude does not, and an estimate that
survives contact with a measurement at 60% of its projected value should be
reported as the miss it is.

**What building it cost in fidelity, and what that taught.** A warm pool is not
correct by construction, which an earlier draft of `docs/EXECUTIVE-SUMMARY.md`
asserted and this measurement refuted. Differential testing against the corpus
found three classes of divergence in our own pool before any of them reached a
number we published:

* *Streams.* The forked child must rebind `sys.stdout`/`stderr`/`stdin` onto
  its dup'd descriptors with CPython's own settings (`surrogateescape`,
  `write_through`); inheriting the parent's objects silently drops all output
  whenever the host has replaced them, and `errors="replace"` corrupts bytes a
  program meant to round-trip.
* *Environment.* A pool freezes the environment at start. Forwarding the
  caller's `os.environ` per request fixes everything read at run time and took
  the divergence count from 6 to 3.
* *Hash seed.* The residual 3 are set and dict-view orderings, and a fork
  cannot fix them: `PYTHONHASHSEED` is consumed at interpreter start. Starting
  the pool under the caller's seed took 3 to 0. **This is a deployment rule,
  not a bug**, and it is the sharpest practical finding of the whole exercise —
  a warm pool inherits the identity of the interpreter that started it, so it
  must be started as the interpreter its callers think they are getting.

None of these were visible in a hand-written smoke test; all three came from
running the corpus differentially, which is the method this paper argues for.

## 6. The design point: tiers that cannot deoptimize

Every tiered execution system we are aware of makes its tiering decision the same
way: start the program in a cheap tier, watch it run, promote what is hot, and
bail out when a speculation fails. HotSpot's uncommon traps, V8's bailouts (in
Crankshaft historically; TurboFan, Sparkplug and Maglev since 2017), Truffle's
`transferToInterpreter`, and PyPy's guard failures into its blackhole interpreter
are variations on one arrangement `[unverified]`. They share three enabling
conditions: the tiers run in one address space over one heap with a *maintained
mapping* between frame layouts (they are not identical — deoptimization exists
precisely because they differ), a profile accumulates as the program runs, and
the program runs long enough to repay collecting it.

Tiers that are separate operating-system processes have none of the three. There
is no shared heap, no shared address space, and no frame mapping to maintain. In
principle state could be serialized across the boundary; in practice the
quantitative argument settles it without appeal to impossibility — a cross-process
transfer costs on the order of the 16.83 ms spawn it would be avoiding, to
preserve 0.019 ms of work. Deoptimization therefore degenerates into
re-execution from the top, which is sound only if the abandoned attempt left no
trace, which is what the commit barrier provides and bounds (§4).

Re-execution constrains what the admission decision can be. A tier that cannot be
abandoned partway with its effects kept must be abandoned before any effect
occurs, so the decision is made before execution begins; no profile exists at that
point, so it is taken from the program text alone. Given process separation, that
is not one option among several — it is the only tiering scheme available. We
stress the conditional: *process separation itself is our design choice*, not
something the workload forced. What the workload forces is the conclusion that
in-process tiering does not pay here, and that argument is quantitative. A
profile-guided mechanism has, at the median, 19 microseconds in which to observe,
decide, compile and profit.

**PyPy is the empirical test of that claim, and it is a negative result about a
mature system rather than an argument from our own design.** Over the same 1,990
programs, PyPy takes 3.0–3.1× CPython's wall in two independent sweeps. But the
wall clock alone does not license the causal story, and an earlier draft of this
section overreached by attributing the whole gap to unamortized warmup. §5.3
separates the components directly: PyPy pays **+16.22 ms over CPython on
`print(1)`**, a program with nothing to trace. Roughly half the per-program
penalty is therefore fixed interpreter startup that no amount of program length
would amortize away, and only the remainder is attributable to warmup and
short-program execution. The design conclusion survives the correction and is
arguably strengthened — a runtime whose *constant* cost is 26.9 ms cannot serve a
population whose median program computes for 0.019 ms, regardless of its JIT. We
have not swept program duration, so we cannot say where the crossover lies, only
that this workload sits far below it.

We also acknowledge what we did not measure. GraalPy is in the family we
position against and absent from our sweep for no reason better than container
constraints; a complete evaluation would include it. CPython 3.13, by contrast,
is now measured (§5.1, §5.3): it starts *slower* than 3.11 on this container,
neither available build carries the experimental JIT, and as an engine against
the 3.11 reference it produces 9 silent divergences of its own — which
recalibrates what cross-implementation "compatibility" can even mean.

Once tiering reduces to a static admission test with re-execution as its only
recovery, the refusal signal stops being an internal control-flow event and
becomes the system's principal interface. An in-process deoptimization is a jump
that nothing outside the runtime observes (which is not to say it is easy — deopt
correctness is a notorious bug class in every system named above; it is simply
not an IPC problem). Here the signal must cross a process boundary and be
distinguishable from every possible failure of an arbitrary program written by
someone else. That is a systems-integration problem, not a compiler one, and it
is the part of this work that is real engineering and looks like nothing in a
diagram.

---

## 7. Related work

Every load-bearing idea in this system has been published before, and in most
cases published better. We state that first, because the contribution is not any
of these ideas but the corner of the design space in which the workload forces
them to combine.

**Refusing rather than guessing.** The closest precedent is in our own ecosystem.
Numba compiles a Python subset and *raises* rather than silently degrading: as of
**0.59.0 (31 January 2024)**, object-mode fallback was removed from the jit family
entirely and `nopython` became the default, so code that would previously have
fallen back now fails with a `TypingError` [9]. The stated rationale is ours: a
silent slow path is worse than a visible refusal because nothing downstream can
tell it happened. Our exit-90 contract is that idea moved across a process
boundary.

**Fail-stop as the correctness criterion.** "Never silently wrong" is not new.
Schlichting and Schneider define a fail-stop processor as one that "automatically
halts in response to any internal failure and does so *before the effects of that
failure become visible*" [5] — which is the pairing of our refusal contract
with the commit barrier, *over the effects the barrier covers*. A halt that
leaves a spawned child, a sent packet or a delivered signal visible is not
fail-stop under that definition, so we claim the property over this process's
stdout and file writes, not in general (§4). Our design is an
instance of a class named in 1983, and saying so costs a novelty claim and buys a
correctness vocabulary.

**Speculation with bail-out.** HotSpot's uncommon traps [8], Truffle's
`transferToInterpreter` `[unverified]`, and PyPy's guard failures into its
blackhole interpreter `[unverified]` all deoptimize from a fast path to a
conservative one; V8 historically bailed out of compilation entirely for
unsupported constructs, though that specific mechanism (Crankshaft) was retired in
2017 in favour of TurboFan, Sparkplug and Maglev `[unverified]`. The conditions
under which such a system is observationally equivalent to its reference have been
formalized and mechanized in Coq by Flückiger, Scherer, Yee, Goel, Ahmed and
Vitek [4]. Our `MISMATCH 0` gate is an empirical, differential-testing
approximation of a property they proved. §6 argues that what distinguishes our
setting is not the bail-out but the *absence of the enabling conditions* for
deoptimization once tiers are processes.

**Profiling the real workload instead of the benchmark suite.** This is the exact
methodological structure of Richards, Lebresne, Burg and Vitek [3], who found that
"current JavaScript benchmarks are poor representatives of real JavaScript
programs"; of JSMeter [6], which concluded that "the benchmarks are not
representative of many real web sites and that conclusions reached from measuring
the benchmarks may be misleading"; and of DaCapo [7], built to replace suites
whose behaviour had stopped resembling deployed Java. We follow that method
exactly and claim only the subject as new. We are not aware of a prior published
characterization of Python emitted by a coding agent at *execution* granularity —
captured as the interpreter receives it — and we write "not aware of" rather than
"there is none".

**Benchmarking methodology.** Two results shape §5 and §8. Barrett, Bolz-Tereick,
Mount, Killick and Tratt [2] show that VM warmup does not reliably reach a steady
state at all, which is why we do not report a "steady-state" number for PyPy and
instead report whole-sweep wall clock in the shape a user pays. Mytkowicz, Diwan,
Hauswirth and Sweeney [1] show that measurement bias from incidental setup —
environment size, link order — can be large enough to invert a conclusion about
`-O2` versus `-O3`; since startup is 78.9% of our summed wall time, that hazard
lands directly on our headline quantity, and §8 concedes we did not control it.

**Alternative Python implementations, as measured rather than cited.** PyPy,
MicroPython and Monty are our baselines, and the useful result is semantic. Every
one of PyPy's three divergence families that we observed is **documented by PyPy
itself** [10]: that its non-refcounting collector means "files (and sockets, etc)
are not promptly closed", so that for files opened for writing "data can be left
sitting in their output buffers for a while, making the on-disk file appear empty
or truncated"; that PyPy's sets are ordered where CPython's are not; and that
PyPy's builtins accept keyword arguments CPython's reject. Our contribution here
is not the discovery but the **blast radius**: on agent-emitted Python, where
45.8% of parsed programs call `open()` and 96.0% of those use the bare,
refcount-dependent idiom, these documented differences silently change the answer
for 39 of 745 runnable programs (5.2%).

**Code execution inside agent loops.** Two recent studies bound the behaviour from
outside the interpreter. Analysis of 7,745 agent traces from SWE-bench submissions
reports an average of 8.8 test runs per task [11] — direct evidence that
*execution frequency*, not duration, is the quantity a runtime for this setting
should be designed against. A study of 300 agent-generated projects finds only
68.3% execute out-of-the-box (89.2% for Python) [12], which measures environment
and dependency drift rather than interpreter semantics, and motivates our
zero-dependency constraint. Neither instruments the interpreter invocation itself.

**Still unverified.** Claims marked `[unverified]` above — Truffle's
`transferToInterpreter`, PyPy's blackhole interpreter, V8's tier history, and the
RPython translator's subset — are from model recall and were not checked against primary sources
before this draft. They are attributions we believe correct in substance, and
each must be verified before submission. Everything in the numbered references
below was checked against a primary source on 2026-08-31.

## 8. Threats to validity

**Training on the test set — the most serious.** The capability tables that decide
admission were derived from this corpus, and the coverage rate is reported on it.
There is no clean pre-registered holdout in this data, because the tables were
revised while the corpus grew. The direction flatters us, and it flatters
coverage specifically.

We must also retract a defence that does not survive scrutiny. It is tempting to
say that corpus-fitting can raise coverage but cannot manufacture *agreement*,
since a wrongly admitted program shows up as a divergence rather than a match.
That is false in the direction that matters: the development loop resolves an
observed divergence by *narrowing* a table, converting a SILENT-DIFF into an
UNSUPPORTED. Our reported silent-divergence count of 1 is therefore as
corpus-fitted as the coverage number. The correct claim is narrow: fitting cannot
manufacture within-sample agreement for a table entry that this corpus exercises,
and **the out-of-sample silent-divergence rate is exactly what is unmeasured.**
A prospective holdout — freeze the tables, grade only entries captured
afterwards — is the right experiment, and it is now registered rather than
promised: `study/paper/holdout_registration.json` pins the freeze commit, the
date (2026-08-31), and the identity of every in-sample entry, with the grading
procedure spelled out. Any entry captured after that commit is out-of-sample by
construction. The out-of-sample silent-divergence rate remains a number this
draft does not have; the registration is what makes the eventual number honest.

**The sampling frame.** n=1 in every dimension: one harness, one model family,
one user's task mix, 20 capture dates ending 2026-08-30. The population is
non-stationary — a model update changes what gets typed — so the *build order*
(which missing features block the most programs) is more transferable than the
coverage percentage attached to it.

**Exclusion bias, and the two exclusions differ.** Of 2,906 entries, 755 were
skipped for naming an absolute path and 161 as nondeterministic. These do not
behave alike. The 161 *cannot* be graded differentially — CPython does not agree
with itself on them — so removing them is an instrument requirement, not a bias.
The 755 are a real bias, it flatters us, and rather than assert its direction we
measured it. Profiling the skipped set against the retained set (2026-08-31;
752 and 1,956 of them parse):

| | skipped (absolute path) | retained | ratio |
|---|---:|---:|---:|
| `open()` call sites per program | 0.856 | 0.680 | 1.26× |
| contains a loop | **75.1%** | 38.2% | 1.97× |
| contains a comprehension | **46.7%** | 14.6% | 3.20× |
| defines a function | **20.3%** | 5.6% | 3.63× |
| has `try`/`except` | 10.0% | 5.3% | 1.89× |

The excluded programs are substantially more complex than the ones we evaluate on
— nearly twice as likely to loop, three times as likely to use a comprehension,
and 3.6 times as likely to define a function. This is the reviewer's objection
confirmed with a number rather than talked around: **the exclusion removes the
harder half of the workload, and our coverage rate is correspondingly optimistic.**
We cannot say by how much without running them, which needs a real sandbox we did
not build. Note the one bound available: the retained set is far from I/O-free —
PyPy's file-finalization divergences — the largest of its six families, per the
hand classification shipped at `study/paper/data/pypy_divergences_families.json`
(§5.2) — were all found *inside* it.

**A larger selection effect than the exclusions.** Only 745 of the 1,990 graded
candidates run cleanly enough to be graded on output — 25.6% of the 2,906 entries
loaded. The other 1,245 candidates fail because the temp-cwd net strips the repository state they were
written against. The evaluated set is therefore selected on "still works with its
context removed," which plausibly skews toward shorter, more self-contained, and
faster programs — precisely the ones a subset engine handles. This is the
sampling threat we consider most serious after the first, and it is not fully
addressable without a real sandbox that can reproduce repository state.

**Deduplication removes the weighting a user actually pays — measured.** The
corpus content-addresses entries, so every count above is over distinct program
*texts*. What an agent's wall clock is weighted by is *invocations*. The capture
log carries both: the 2,906 distinct entries were seen **7,406 times**, a mean of
2.55 and a maximum of 45, with 1,845 (63.5%) seen exactly once. Re-weighting the
profile by invocation count moves it, and moves it *away* from complexity
(2026-08-31):

| feature | by distinct program | by invocation |
|---|---:|---:|
| contains a loop | 48.6% | 44.7% |
| contains a comprehension | 23.8% | **15.7%** |
| defines a class | 0.3% | 0.4% |

The programs agents re-run are simpler than the ones they run once, so the
workload an interpreter actually faces is *easier* than §3.1 suggests. This runs
in our favour and we would rather report it than let a reviewer find it: our
coverage numbers are computed on the harder, distinct-text population.

**Measurement bias in a spawn-dominated regime.** Startup is 78.9% of summed wall
time, and that is exactly the quantity most perturbed by environment size and
link order — the hazard Mytkowicz et al. describe [1],
and it lands on our headline quantity rather than adjacent to it. Our hygiene
(`PYTHONHASHSEED=0`, `LC_ALL=C.UTF-8`, fresh temp cwd, one instrument, interleaved
rounds, best-of-N) controls semantic determinism; it does **not** control machine load — CPython's
own sweep wall moved 26.8 → 30.8 s between our two dates (§5.1) — and it does
**not** control layout. Interleaving and best-of-N expose load rather than
remove it, which is why we report ratios. We did not randomize layout, and a reader who suspects layout
artefacts should weigh §5.4's warm-pool comparison, which is a different shape of
measurement, more heavily than the cold-spawn ratios.

**Self-hosting — larger than it first appears, but it lands on the profile, not
the evaluation.** 235 of 2,906 programs (8.1%) import `lypning` itself, and
**1,029 (35.4%) mention it at all**, many invoking the CLI through `subprocess`.
A third of this corpus was captured from sessions developing the system under
evaluation, so §3's profile is contaminated by one project's idioms and should be
read that way.

The evaluation is not, and we checked rather than assumed. Re-running the tier-1
grading with every `lypning`-importing entry removed changes nothing at all —
480 MATCH, 264 UNSUPPORTED, 1 SILENT-DIFF either way (2026-08-31) — because
**none of the 745 clean programs import `lypning`**. All 129 such programs among
the 1,990 graded candidates fail in the sandbox for other reasons and land in
BOTH-FAIL. The sensitivity analysis is a null result, which is the outcome we
wanted and not one we could have predicted.

---

## 9. Conclusion

Coding agents have created a Python workload with no benchmark suite we are
aware of, and properties that invert the assumptions of mainstream
implementation work: 384-byte programs, no classes, and 19 microseconds of
computation behind 16.83 ms of spawn, interpreter startup and imports. Measured
against that workload, the implementation usually reported as the fastest is
the slowest, and it is silently wrong on a class of program that touches almost
half the corpus by static call site: writing a file and reading it back.

Our own engine's honest headline is 1.50× on the clean subset and 1.76–1.77×
across two full sweeps, with 744 of 745 programs answered identically to CPython.
A pre-warmed CPython fork pool beats it — 2.04× and always correct — at the cost
of a resident daemon, and the measurement points at the design that should beat
both: a chain whose backstop tier is a warm pool rather than a cold spawn. We
report that we lose to the obvious baseline because a paper that hid it would be
worth less than the measurement it suppressed.

The durable contributions are the corpus and the observation that
process-separated tiers cannot deoptimize, which turns tier selection into a
static admission test and the refusal channel into an interface that has to be
specified and machine-checked like any other.

**Artifacts.** Corpus, capture harness, all five-engine measurement scripts, and
the conformance battery are in the repository; `study/paper/` reproduces every
table here. Every tool prints the corpus count it loaded — quote that number,
from that run, with its date.

---

## References

Checked against primary sources on 2026-08-31.

1. T. Mytkowicz, A. Diwan, M. Hauswirth, P. F. Sweeney. *Producing Wrong Data
   Without Doing Anything Obviously Wrong!* ASPLOS 2009.
   [doi:10.1145/1508284.1508275](https://dl.acm.org/doi/10.1145/1508284.1508275)
2. E. Barrett, C. F. Bolz-Tereick, S. Mount, R. Killick, L. Tratt. *Virtual
   Machine Warmup Blows Hot and Cold.* PACMPL 1(OOPSLA), October 2017.
   [doi:10.1145/3133876](https://dl.acm.org/doi/pdf/10.1145/3133876) ·
   [arXiv:1602.00602](https://arxiv.org/abs/1602.00602)
3. G. Richards, S. Lebresne, B. Burg, J. Vitek. *An Analysis of the Dynamic
   Behavior of JavaScript Programs.* PLDI 2010, pp. 1–12.
   [doi:10.1145/1806596.1806598](https://dl.acm.org/doi/10.1145/1806596.1806598)
4. O. Flückiger, G. Scherer, M.-H. Yee, A. Goel, A. Ahmed, J. Vitek.
   *Correctness of Speculative Optimizations with Dynamic Deoptimization.*
   POPL 2018 / PACMPL.
   [doi:10.1145/3158137](https://dl.acm.org/doi/10.1145/3158137) ·
   [arXiv:1711.03050](https://arxiv.org/pdf/1711.03050)
5. R. D. Schlichting, F. B. Schneider. *Fail-Stop Processors: An Approach to
   Designing Fault-Tolerant Computing Systems.* ACM TOCS 1(3):222–238, 1983.
   [doi:10.1145/357369.357371](https://dl.acm.org/doi/10.1145/357369.357371)
6. P. Ratanaworabhan, B. Livshits, B. G. Zorn. *JSMeter: Comparing the Behavior
   of JavaScript Benchmarks with Real Web Applications.* USENIX WebApps 2010.
   [usenix.org](https://www.usenix.org/conference/webapps-10/jsmeter-comparing-behavior-javascript-benchmarks-real-web-applications)
7. S. M. Blackburn et al. *The DaCapo Benchmarks: Java Benchmarking Development
   and Analysis.* OOPSLA 2006.
   [doi:10.1145/1167515.1167488](https://dl.acm.org/doi/10.1145/1167515.1167488)
8. M. Paleczny, C. Vick, C. Click. *The Java HotSpot Server Compiler.* USENIX
   JVM Research and Technology Symposium, April 2001.
   [usenix.org](https://www.usenix.org/legacy/events/jvm01/full_papers/paleczny/paleczny.pdf)
9. Numba 0.59.0 release notes, 31 January 2024 — object-mode fallback removed,
   `nopython` default.
   [numba.readthedocs.io](https://numba.readthedocs.io/en/stable/release/0.59.0-notes.html)
10. PyPy documentation, *Differences between PyPy and CPython* — non-refcounting
    collection and prompt file closing, set ordering, keyword arguments.
    [doc.pypy.org](https://doc.pypy.org/en/latest/cpython_differences.html)
11. *To Run or Not to Run: Analyzing the Cost-Effectiveness of Code Execution in
    LLM-Based Program Repair.* [arXiv:2606.26978](https://arxiv.org/abs/2606.26978)
12. *AI-Generated Code Is Not Reproducible (Yet): An Empirical Study of Dependency
    Gaps in LLM-Based Coding Agents.*
    [arXiv:2512.22387](https://arxiv.org/abs/2512.22387)
