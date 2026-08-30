# Comparison — lypning against ADK-Rust CodeAct + Monty

Two Rust-implemented Pythons for agent-typed code, built for different jobs.
This document says what each is for, then puts both on **one instrument over
one corpus** and reports what came out. Every number below was measured on this
container on **2026-08-30**, against **pydantic-monty 0.0.21** and the lypning
binary built the same day (1,024,208 B); the harness is committed at
`study/monty/` so a fork can re-run it. Per CLAUDE.md invariant 3, do not quote
these numbers without re-running them — both projects move.

## The two designs, honestly stated

**[Monty](https://github.com/pydantic/monty)** (Pydantic, MIT) is a minimal
Python interpreter in Rust built to be *embedded*: it cannot touch the
filesystem, environment or network unless the host grants an external function,
it enforces resource limits, and it can snapshot its entire execution state and
resume later. **[ADK-Rust](https://github.com/zavora-ai/adk-rust)** (Apache-2.0)
uses it as the substrate for its experimental **CodeAct** runtime
(`adk-codeact-monty`): the LLM writes Python that calls the agent's tools as
functions, Monty executes it inside the sandbox, and suspend/resume snapshots
carry state across turns. The security boundary *is* the interpreter.

**lypning** is a drop-in interpreter chain for programs an agent launches as
*processes* — the `python3 -c …` a coding harness runs a hundred times a day.
Its contract is different: it must be **indistinguishable from CPython or
refuse** (exit 90, nothing on stdout), and anything refused falls through the
chain until CPython answers, so the caller always gets CPython's answer —
usually cheaper. Side effects are real (files are written), guarded by a commit
barrier that makes a refused run observably a no-op. The security boundary is
the harness's, not the interpreter's.

So Monty asks *"how do I run untrusted code safely inside my process?"* and
lypning asks *"how do I run the code my harness already trusts, faster, without
ever being wrong?"* The measurements below are about the second question —
fidelity to CPython on real agent programs — because that is the axis the two
designs genuinely share.

## One instrument, one corpus

`study/monty/grade_monty.py` and `study/monty/grade_lypning.py` run the same
grading over the same programs: the lypning corpus (2,906 captured programs on
this date), minus 755 naming absolute paths and 161 nondeterministic ones —
**1,990 graded**. The CPython reference runs per-program in a fresh temp cwd
with `PYTHONHASHSEED=0` and `LC_ALL=C.UTF-8`. Monty runs with no OS access
(its default); lypning runs as the process it is.

| outcome, of 1,990 | lypning (tier 1 alone) | Monty 0.0.21 |
|---|---:|---:|
| agreed with CPython (stdout, success) | **480** (24.1%) | 275 (13.8%) |
| both failed (program fails in an empty sandbox on both) | 1,245 (62.6%) | 1,245 (62.6%) |
| refused / unsupported, loudly | 264 (13.3%) | 340 (17.1%) |
| errored where CPython succeeds | 0 | 107 (5.4%) |
| **answered wrongly at exit-success** | **1** (0.05%) | **23** (1.2%) |

The identical both-fail counts are the sanity check that one instrument graded
both columns. On the **CPython-clean subset** (745 programs that run cleanly in
the sandbox):

| | lypning tier 1 | Monty 0.0.21 |
|---|---:|---:|
| answers correctly | **480 / 745 = 64.4%** | 275 / 745 = 36.9% |
| fails or refuses loudly | 264 (35.4%) | 447 (60.0%) |
| **silent wrong answer** | **1 (0.13%)** | 23 (3.1%) |

lypning's one silent divergence is the ledgered musl-libm `pow` last-ULP
difference (`1.7976931348623157e308 ** 0.5`), tracked by identity in
`.github/known-mismatches.json`. And tier 1 is not the product — the **chain**
is: with the fall-through, the full mixture answers **2,119 of 2,906** corpus
programs identically to CPython with 7 ledgered exceptions (`lypning
conformance`, 2026-08-30), because a refusal is answered by the next tier
rather than surfaced to the model. CodeAct has no equivalent — a Monty error
returns to the LLM, which spends a model turn recovering.

### Where Monty's 23 silent divergences live

Four spellings were independently re-verified (each on three CPython runs)
before publication; they are the *same families* lypning refuses or escalates
rather than answers:

| construct | CPython | Monty 0.0.21 | lypning |
|---|---|---|---|
| `9007199254740993 / 3` | `3002399751580331.0` | `3002399751580330.5` | refuses `int-div-precision`, chain → CPython |
| `getattr(type(e), "__module__", "MISSING")` | `builtins` | `MISSING` | refuses `dunder-missing`, chain → CPython |
| `round(2.675, 2)` | `2.67` (rounds the stored value) | `2.68` | answers `2.67` |
| caught-`TypeError` text from a mixed sort | names the innermost pair (`'int' and 'str'`) | names the outer tuples | matches CPython's text |

Credit where measured: Monty reproduces CPython's `hash(-1) == -2` sentinel
quirk correctly, and one candidate set-order divergence did **not** reproduce
under a pinned hash seed — both claims were dropped from this table for exactly
that reason.

## Performance

Four instruments, because no single one can see everything: startup (where the
corpus population actually lives), sustained compute (where none of these
systems was designed to live), the end-to-end corpus wall (the deployment
number), and memory. All measured on this container, 2026-08-30.

### Startup and footprint

Both projects claim fast startup and both are right — *in the same shape*. The
fair comparison is shape-to-shape (60 runs each):

| shape | lypning | Monty 0.0.21 | CPython 3.11 |
|---|---:|---:|---:|
| in-process, warm (`liblypning` C ABI / warm pool checkout+feed) | **0.05 ms** median | **0.04 ms** median | — |
| process spawn, `print(1)` | **0.64 ms** median | ~100 µs execution + runtime start (CLI shape) | 10.33 ms median |
| cold pool / first load | one `dlopen` | 15.6 ms pool construction | — |
| on-disk runtime | 1,024,208 B (one static binary, 8 CheerpX blocks) | 22,064,856 B runtime binary (9.2 MB wheel payload) | — |
| peak RSS (hello / dict-heavy) | at the ~8.6 MB measurement floor | floor +0.2 MB / +1.0 MB | at the floor |

In-process to in-process they are the same order of magnitude; startup does not
separate these systems.

### Sustained compute — where nobody beats CPython

Six compute-bound workloads (integer and float loops, string methods, list and
dict churn, recursive calls), each first validated to produce **byte-identical
stdout on every engine**, then timed as the median of 5 interleaved rounds so
machine load hits all arms alike. Ratios are against CPython's wall clock:

| workload | CPython | lypning | liblypning | lypning-mp | Monty (warm feed) | Monty (CLI) |
|---|---:|---:|---:|---:|---:|---:|
| int loop (3M) | 218 ms | 1.85× | 2.08× | **0.78×** | 1.91× | 1.98× |
| float loop (2M) | 135 ms | 2.31× | 2.51× | 1.36× | 2.36× | 2.33× |
| str methods (120k) | 83 ms | 2.82× | 3.41× | 5.88× | **2.13×** | 2.61× |
| list churn (400k) | 60 ms | 2.92× | 3.24× | **1.00×** | 2.12× | 2.13× |
| dict churn (600k) | 122 ms | **2.12×** | 2.48× | 23.41× | 4.42× | 4.52× |
| recursive calls (fib) | 29 ms | 4.51× | 5.15× | **1.05×** | 2.31× | 2.37× |

The honest headline: **on sustained loops, nothing here beats CPython** —
lypning runs 1.9–4.5× slower, Monty 1.9–4.5× slower (consistent with its own
"5× faster to 5× slower" claim), and MicroPython is bimodal (fastest on
ints and lists, 23× slower on dict churn, which is why the classifier exists).
Instruction counts (callgrind, exact) partially reorder the wall ranks — on
the int loop Monty *executes* 0.84× CPython's instructions and lypning-mp
0.62×, yet both lose or tie on wall — so the wall costs are memory- and
dispatch-bound, not instruction-bound. None of these engines is a compute
accelerator; they are startup and safety plays.

### End to end — the number an agent session pays

The corpus population is one-liners, so the deployment metric is spawn-bound
by construction: total wall to run all **745 CPython-clean corpus programs**,
each in a fresh temp cwd, in each system's natural shape (best of 2 rounds):

| system, its own shape | total | per program | and it answers |
|---|---:|---:|---|
| CPython, spawned per program | 12,687 ms | 17.0 ms | 100% (it is the oracle) |
| **lypning chain** (`lypning run`) | **8,569 ms** | **11.5 ms** | **100%, never silently wrong** (1 ledgered ULP) |
| lypning tier 1 alone | 2,029 ms | 2.7 ms | 64.4% (the chain covers the rest) |
| Monty, warm pool | 6,812 ms | 9.1 ms | 36.9% correct; 60% error back to the LLM |

Read the last column with the middle ones: Monty's pool completes the sweep
faster than the chain, but on this population it hands 6 of 10 programs back
to the model as errors — and a model turn costs three to six orders of
magnitude more time and money than any interpreter in this table. The chain is
32% faster than spawning CPython *while returning CPython's answer for
everything*. And when a workload is dominated by programs tier 1 can take, the
ceiling is the tier-1 row: 6.3× faster than CPython end to end.

## Feature-by-feature

| | lypning (mixture chain) | ADK-Rust CodeAct + Monty |
|---|---|---|
| primary job | make a harness's real `python3` invocations cheaper, never wrong | run LLM-written tool-calling code inside the agent process, safely |
| wrong-answer discipline | **refuse (exit 90) or match CPython**; divergences ledgered by identity; grid+fuzz+corpus battery enforce it | best-effort subset; divergences surface as differences (23/1,990 measured) |
| on unsupported code | falls through the chain; caller still gets CPython's answer | raises to the host; the LLM sees the error and retries |
| side effects | real, transactional (commit barrier discards on refusal) | none unless host grants external functions / `AbstractOS` |
| sandboxing | none of its own — inherits the harness's | **built-in**: no FS/env/network by default, resource limits |
| state across runs | none (process model) | **session state, snapshot/serialize/resume** |
| tool calling | not its job (the harness's tools stay outside) | **the point**: tools exposed as Python functions via `external_lookup` |
| classes, generators, `match`, inheritance | classes/generators refused → chain answers; no gap visible to caller | partial (no inheritance/metaclasses/`match` at 0.0.21) → error to the LLM |
| stdlib | tier 1 subset + tier 2's frozen stdlib + all of CPython via the chain | curated subset (`json`, `re`, `datetime`, `pathlib`, …) |
| adapts to *your* programs | **yes — the product**: capture → harvest → gate → step (`FORKING.md`) | fixed feature roadmap upstream |
| typing | runs code as CPython would | optional ahead-of-time type checking |
| maturity | experimental, measured | experimental (pre-V1; CodeAct crate marked experimental) |
| license | MIT | Monty MIT; ADK-Rust Apache-2.0 |

## When to choose which — and when both

* **Choose ADK-Rust CodeAct + Monty** when the LLM's Python *is the tool call*
  and it must run inside your process with a hard sandbox, resource limits and
  resumable state. That is what it is for, and nothing in lypning does it.
* **Choose lypning** when an agent already shells out to `python3` and you want
  those runs cheaper with a guarantee the answer never silently changes — the
  shim and hooks drop in under an existing harness without touching the agent.
* **Both compose.** They sit at different layers: CodeAct replaces tool-call
  JSON inside the agent loop; lypning replaces the interpreter under the shell
  commands that loop still issues. A fork could even mount Monty as a tier in
  the chain — the contract any tier must honor is one line on stderr and
  exit 90 (`docs/LYPNING.md` §5) — though Monty's error model would need a
  wrapper to qualify.

## Reproducing this

```bash
pip install pydantic-monty            # 0.0.21 at time of measurement
python3 study/monty/grade_monty.py    # fidelity: Monty column   (~40 s)
python3 study/monty/grade_lypning.py  # fidelity: lypning column (~50 s)
python3 study/monty/perf_matrix.py    # compute matrix, 6 workloads x 6 arms
python3 study/monty/perf_endtoend.py  # the 745-program deployment sweep
```

Both scripts print the counts they graded; quote those, from your run, with
its date.

Sources: [pydantic/monty](https://github.com/pydantic/monty) ·
[Pydantic's announcement](https://pydantic.dev/articles/pydantic-monty) ·
[zavora-ai/adk-rust](https://github.com/zavora-ai/adk-rust) ·
[ADK-Rust site](https://adk-rust.com/en)
