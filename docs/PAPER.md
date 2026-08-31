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
**16.83 ms of interpreter startup**. Execution is 17.3% of aggregate wall time and
essentially none of the median program's.

We then benchmark five implementations — CPython 3.11, PyPy 7.3.20, MicroPython,
Monty 0.0.21, and our own tiered engine — on this corpus with one instrument,
measuring cold start, parse, execute, process overhead, memory, compatibility
rate, and end-to-end wall clock. Two results stand out. **PyPy, the fastest
Python by conventional benchmarks, is the slowest engine on this workload**
(3.0–3.1× CPython's wall over 1,990 programs, across two independent sweeps) and
produces **39 silent divergences** from CPython, dominated by one cause the
profile predicts: without refcounting, `open(f,"w").write(...)` is not promptly
flushed, and 49.0% of corpus programs contain an `open(` call site. PyPy's
per-program penalty is **majority fixed startup, not unamortized warmup** — it
pays +16.22 ms over CPython on `print(1)`, before any JIT can warm. And the honest competitor to a new engine
is not another interpreter but a **warm pool** — a pre-warmed CPython forking per
program — which we measure rather than dismiss, and which **beats our own deployed
chain** (2.04× vs 1.50× over cold CPython) while being correct by construction.

Our own contribution is narrower than "a faster Python" and we state it as such:
a characterization of an under-measured workload, and the design point that
workload forces — **tiers that are separate processes cannot deoptimize**, so
tier selection must be a static admission test, and the refusal channel becomes
the system's principal interface rather than an internal signal. The measurement
that beat us also points past us: tier 1 serves its share at 2.67 ms against the
pool's 8.39 ms, so a chain whose *backstop* is a warm pool should beat both.

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

The paper makes four claims, in decreasing order of confidence:

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
4. **Two negative results worth the space**: on this workload nothing beats
   CPython at sustained compute; our deployed configuration is **1.50–1.77×
   faster end to end, not an order of magnitude**; and **a pre-warmed CPython
   fork pool beats it** (2.04×) while being correct by construction. We report
   the modest number and the loss because they are what a user would find. (§5.4)

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
| f-string | 5.2 | `match`, walrus, `async` | **0.0** |

The distribution is the design document. Half of these programs loop, but only
one in ten defines a function and three in a thousand define a class. An engine
that implements loops, comprehensions, and the builtin data types — and refuses
classes, generators and `async` — is not a crippled Python; on this population it
is most of one.

Top imports (occurrence counts): `json` 711, `sys` 680, `re` 415, `lypning` 242,
`subprocess` 234, `os` 148, `pathlib` 136, `io` 131, `collections` 129.

Top call sites, counted statically — these are **call sites, not dynamic call
counts**, which we did not measure: `print` 5,126, `open` 2,041, `len` 1,388,
`isinstance` 760, `repr` 436, `sorted` 407, `range` 372. Put differently,
**1,423 of 2,906 programs (49.0%) contain at least one `open(` call site.** Agent
Python is I/O code with a little computation attached, and §5.2 shows that this
single fact predicts where a competing implementation breaks.

### 3.2 Where the time goes

For every program CPython runs cleanly, we decompose one `python3 prog.py` by
timing `compile()` and `exec()` *inside the child* and subtracting from the
parent's spawn-to-reap wall. Over **765 programs** (2026-08-30):

| component | median per program | share of summed wall |
|---|---:|---:|
| interpreter startup + spawn | **16.83 ms** | **78.9%** |
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
234 corpus programs import `subprocess`, so that gap is not hypothetical. The
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
low-collision encoding, and we have not measured a false-refusal rate.

---

## 5. Evaluation

All measurements on one container, 2026-08-30, CPython 3.11 as reference with
`PYTHONHASHSEED=0` and `LC_ALL=C.UTF-8`, each program in a fresh temp cwd.
Engines: **CPython 3.11**, **PyPy 7.3.20** (Python 3.11.13), **MicroPython**
(32-bit), **Monty 0.0.21** (in-process, its native shape), and **lypning** in two
configurations — tier 1 alone, and the full chain, which is what a user runs.

### 5.1 Compatibility

One sweep, one reference run per program, all engines graded against it. Of 1,990
graded programs, **745 run cleanly** under CPython in the sandbox; the other 1,245
fail for both reference and engine (they need a real repository, real arguments,
or the network). We grade BOTH-FAIL on the coarse criterion that both sides
failed — we do **not** require that they failed the same way, which is a
limitation of the instrument.

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

**Reproducibility.** This sweep was run twice, independently, on 2026-08-30 and
2026-08-31, the second after a harness change that gives every child an explicit
EOF on stdin (without it, corpus programs calling `sys.stdin.read()` block on the
harness's inherited stdin — a real irreproducibility we found and fixed). **Every
correctness count above was identical across both runs.** The wall-clock column
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

**PyPy's 39 divergences have one dominant root cause.** PyPy does not refcount, so
a file object is not finalized at the moment its last reference drops. The idiom
`open(f,"w").write(...)` — which CPython flushes and closes immediately — leaves
data unwritten under PyPy, and a subsequent read in the same program sees an
empty or stale file. Observed, verbatim:

| program shape | CPython | PyPy 7.3.20 |
|---|---|---|
| write a CSV, reopen it, sum a column | `42` | `0` |
| write `x`, append `y`, read lines back | `['x', 'y']` | `['y']` |
| `hashlib.md5(open("f.bin","rb").read())` | hash of the content | hash of an **empty file** |

This is not an obscure corner. §3.1 measured that 49.0% of corpus programs contain
an `open(` call site; the profile said this class of program dominates, and the
divergence lands exactly there. The remaining PyPy divergences are set and
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

40 spawns each, 2026-08-31. This table carries the paper's cleanest causal
result: **PyPy costs +16.22 ms more than CPython to run `print(1)`** — a program
with no loop to trace and nothing to compile. That is fixed interpreter startup,
and it is present before any JIT question arises. PyPy's per-program penalty over
the sweep is ≈33 ms, so roughly half is this constant and the remainder is
warmup and slower short-program execution. We report the split rather than
attributing the whole gap to warmup, which is what the wall-clock numbers alone
would have let us claim.

On **sustained compute**, over six workloads first validated to produce
byte-identical output on every engine (medians of 5 interleaved rounds), nothing
here beats CPython: lypning runs 1.9–4.5× slower, Monty 1.9–4.5× slower,
MicroPython bimodal (0.78× on integer loops, 23× slower on dict churn).
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
| lypning tier 1, cold spawn | 1,993 ms | **2.67 ms** | **6.39×** | 64.4%; refuses the rest |
| **CPython warm fork pool** | 6,248 ms | **8.39 ms** | **2.04×** | 100% — it *is* CPython |
| lypning chain, cold spawn | 8,476 ms | 11.38 ms | 1.50× | 100%, 744/745 identical |
| CPython, cold spawn | 12,740 ms | 17.10 ms | 1.00× | reference |
| PyPy 7.3.20, cold spawn | 28,333 ms | 38.03 ms | **0.45×** | 706/745; 39 silently wrong |

**The warm pool beats our deployed chain, and we are not going to bury that.**
A pre-warmed CPython that forks per program serves this corpus at 8.39 ms against
the chain's 11.38 ms, and it answers every program correctly *because it is
CPython*. On both axes a reader should care about, it wins. Part of its advantage
is structural and worth naming: the forked child inherits the parent's
`sys.modules`, so a program that does `import json` — 711 corpus programs import
`json`, 415 `re` — pays nothing for it, while every cold-spawn arm pays the import
each time.

What the pool costs is not visible in this table. It is a resident daemon: memory
held between invocations, a lifecycle to supervise, crash recovery, and a harness
change to route through it. Our chain is a drop-in binary that requires no daemon
and no harness modification, and it works in settings a pool does not — where
each invocation is independent, in a different container, or under a different
user. That is a real engineering trade, not a performance win, and we present it
as one.

The measurement also points somewhere better than either arm, and this is the
most useful thing in the paper for a practitioner. Tier 1 alone serves its 64.4%
at **2.67 ms**, three times faster than the warm pool — but its fallback is a
cold CPython spawn at 17.10 ms, which is what drags the chain to 11.38 ms.
**The two designs compose: a chain whose backstop tier is a warm CPython pool
rather than a cold spawn should beat both**, since it pays 2.67 ms on the
programs tier 1 admits and ~8.39 ms on the rest. We have not built it, so we do
not report a number for it; the arithmetic is visible in the table and the
experiment is the obvious next one.

The number a user gets from what we actually ship is the **chain: 1.50× on this
subset, 1.76–1.77× across the two full-sweep runs**, with 744 of 745 clean
programs answered identically to CPython. Tier 1's 6.39× is a configuration
nobody deploys alone, and quoting it as the headline would be dishonest.

---

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

We also acknowledge what we did not measure. GraalPy and CPython 3.13's tier-2
interpreter are in the family we position against and are absent from our sweep;
a complete evaluation would include them.

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
failure become visible*" [5] — which is exactly the pairing of our refusal
contract with the commit barrier that discards staged effects. Our design is an
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
49.0% of programs contain an `open(` call site, these documented differences
silently change the answer for 39 of 745 runnable programs (5.2%).

**Code execution inside agent loops.** Two recent studies bound the behaviour from
outside the interpreter. Analysis of 7,745 agent traces from SWE-bench submissions
reports an average of 8.8 test runs per task [11] — direct evidence that
*execution frequency*, not duration, is the quantity a runtime for this setting
should be designed against. A study of 300 agent-generated projects finds only
68.3% execute out-of-the-box (89.2% for Python) [12], which measures environment
and dependency drift rather than interpreter semantics, and motivates our
zero-dependency constraint. Neither instruments the interpreter invocation itself.

**Still unverified.** Claims marked `[unverified]` above — Truffle's
`transferToInterpreter`, PyPy's blackhole interpreter, V8's tier history, the
RPython translator's subset, JavaScriptCore's tier count, and Meta's Cinder and
Skybison — are from model recall and were not checked against primary sources
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
afterwards — is the right experiment and is a standing one, not a result we can
report today.

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
| `open(` sites per program | 0.856 | 0.680 | 1.26× |
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
PyPy's 39 file-finalization divergences were all found *inside* it.

**A larger selection effect than the exclusions.** Only 745 of 2,906 entries
(25.6%) run cleanly enough to be graded on output. The other 1,245 graded
candidates fail because the temp-cwd net strips the repository state they were
written against. The evaluated set is therefore selected on "still works with its
context removed," which plausibly skews toward shorter, more self-contained, and
faster programs — precisely the ones a subset engine handles. This is the
sampling threat we consider most serious after the first, and it is not fully
addressable without a real sandbox that can reproduce repository state.

**Measurement bias in a spawn-dominated regime.** Startup is 78.9% of summed wall
time, and that is exactly the quantity most perturbed by environment size and
link order — the hazard Mytkowicz et al. describe `[unverified: ASPLOS 2009]`,
and it lands on our headline quantity rather than adjacent to it. Our hygiene
(`PYTHONHASHSEED=0`, `LC_ALL=C.UTF-8`, fresh temp cwd, one instrument, interleaved
rounds, best-of-N) controls semantic determinism and machine load; it does **not**
control layout. We did not randomize layout, and a reader who suspects layout
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

Coding agents have created a Python workload with no established benchmark and
properties that invert the assumptions of mainstream implementation work: 384-byte
programs, no classes, and 19 microseconds of computation behind 16.83 ms of
interpreter startup. Measured against that workload, the fastest Python
implementation is the slowest, and it is silently wrong on the single most common
thing these programs do — write a file and read it back.

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
