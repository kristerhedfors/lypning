# Comparison — lypning against ADK-Rust CodeAct + Monty

> **Status (2026-09-05):** measured on the upstream container on 2026-08-30
> (fidelity, compute, end to end) and 2026-08-31 (warm parity), against
> pydantic-monty 0.0.21 and the `lypning` core built that day, on the chain
> `lypning → lypning-mp → cpython` (`lypning-mp` is the oracle — measured, never
> routed to — since 2026-09-04, `CHANGELOG.md` #38). No table has a `lypning-l`
> column; nothing was re-run on `lypning → lypning-l → cpython`. Monty 0.0.21
> was current on 2026-08-31 — re-check before quoting. The harness does not run
> from this tree unedited (*Reproducing*); the wider sweep is `docs/PAPER.md`.

## The two designs

**[Monty](https://github.com/pydantic/monty)** (Pydantic, MIT) is embedded — no
filesystem, environment or network unless the host grants a function; resource
limits; snapshot and resume — and
**[ADK-Rust](https://github.com/zavora-ai/adk-rust)** (Apache-2.0) builds its
experimental CodeAct runtime on it: the LLM's Python calls the agent's tools as
functions; the security boundary is the interpreter. **lypning** runs the
`python3 -c …` a harness launches as a process — CPython's answer or a refusal
(`docs/VERIFICATION.md` §C1) that falls through the chain (§C4) — and the
boundary is the harness's.

So Monty asks *"how do I run untrusted code safely inside my process?"* and
lypning asks *"how do I run the code my harness already trusts, faster, without
ever being wrong?"* The measurements are about the second question.

## One instrument, one corpus

`study/monty/grade_monty.py` and `grade_lypning.py` grade the corpus as loaded
on 2026-08-30 (2,906) minus 755 naming absolute paths and 161 nondeterministic
— **1,990 graded** — against CPython in a fresh temp cwd per program with
`PYTHONHASHSEED=0`, `LC_ALL=C.UTF-8` (`conformance._env_for`).

| outcome, of 1,990 | `lypning` alone | Monty 0.0.21 |
|---|---:|---:|
| agreed with CPython (stdout, success) | **480** (24.1%) | 275 (13.8%) |
| both failed (program fails in an empty sandbox on both) | 1,245 (62.6%) | 1,245 (62.6%) |
| refused / unsupported, loudly | 264 (13.3%) | 340 (17.1%) |
| errored where CPython succeeds | 0 | 107 (5.4%) |
| **answered wrongly at exit-success** | **1** (0.05%) | **23** (1.2%) |

The identical both-fail counts are the sanity check that one instrument graded
both columns. On the **CPython-clean subset**, the 745 programs that ran
cleanly (2026-08-30):

| | `lypning` alone | Monty 0.0.21 |
|---|---:|---:|
| answers correctly | **480 / 745 = 64.4%** | 275 / 745 = 36.9% |
| fails or refuses loudly | 264 (35.4%) | 447 (60.0%) |
| **silent wrong answer** | **1 (0.13%)** | 23 (3.1%) |

lypning's one divergence is the musl-libm `pow` last-ULP case, ledgered by
identity: engine `lypning`, entry `py-ab7286f43b7a`, family
`float-pow-last-bit`, `.github/known-mismatches.json`. Chain, whole corpus
(`lypning conformance`, 2026-08-30): 2,119 / 2,906 match, 7 ledgered
exceptions — the battery grades failures too, so the counts are not comparable.

### Where Monty's 23 silent divergences live

Four spellings; the CPython column re-verified by hand on three runs
(2026-08-30), the `lypning` / `lypning-l` column pinned for both variants by
`tests/verification/refusal-probes.json` (2026-09-05):

| construct | CPython | Monty 0.0.21 | `lypning` and `lypning-l` |
|---|---|---|---|
| `9007199254740993 / 3` | `3002399751580331.0` | `3002399751580330.5` | refuses `int-div-precision`, chain → CPython |
| `getattr(type(e), "__module__", "MISSING")` | `builtins` | `MISSING` | refuses `builtin: getattr`, chain → CPython |
| `round(2.675, 2)` | `2.67` (rounds the stored value) | `2.68` | answers `2.67` |
| caught-`TypeError` text from a mixed sort | names the innermost pair (`'int' and 'str'`) | names the outer tuples | matches CPython's text |

The `getattr` row is a run-time refusal: `lypning route -c` predicts `lypning`
and both variants exit 90 (2026-09-05, `tests/verification/refusal-probes.json`)
— the case `lypning routes` is for (§C11). Credit where measured: Monty
reproduces CPython's `hash(-1) == -2` sentinel quirk correctly, and one
candidate set-order divergence did **not** reproduce under a pinned hash seed —
both claims were dropped from this table for exactly that reason.

## Compute

Startup does not separate the systems (2026-08-30, 60 runs: warm in-process
0.05 vs 0.04 ms median; spawned 0.64 vs CPython's 10.33 ms). On six compute
workloads (2026-08-30, `study/monty/perf_matrix.py`; the matrix is
`tests/verification/comparison-compute-2026-08-30.md`) lypning and Monty both
run 1.9–4.5× slower than CPython and the oracle is bimodal, 0.78× to 23.41×.
None of these engines is a compute accelerator; they are startup and safety
plays.

End to end over the 745 clean programs (2026-08-30, `perf_endtoend.py`): the
pre-oracle chain (`lypning run`) 11.5 ms/program against CPython's 17.0 and the
Monty pool's 9.1, which hands 6 of 10 programs back to the model as errors;
`lypning -c` alone 2.7 ms over 480 answers and 264 refusals — not a per-answer
cost (`CHANGELOG.md` 2026-08-31 retracts that ratio). Warm for warm
(`docs/PAPER.md` §5.5, 2026-08-31): liblypning 2.23 ms/program against the
pool's 9.41, 480 / 1 silent against 275 / 23; the all-answering warm chain,
12.60, is slower on wall and buys 742 correct answers instead of 275 plus 447
errors. Its 2-program library-arm disagreement was build hygiene (`CHANGELOG.md`
2026-08-31); `lypning doctor` `core/library agreement` guards it (§C7).

## Feature-by-feature

| | lypning (the chain) | ADK-Rust CodeAct + Monty |
|---|---|---|
| wrong-answer discipline | **refuse (exit 90) or match CPython**; divergences ledgered by identity; grid + fuzz + corpus battery enforce it | best-effort subset; divergences surface as differences (23/1,990 on 2026-08-30) |
| on unsupported code | falls through the chain; the caller gets CPython's answer | raises to the host; the LLM sees the error and retries |
| stdlib | `lypning`'s subset; `lypning-l` adds `collections` and `pathlib` (`engines.VARIANT_CAPS`); all of CPython via the chain | curated subset (`json`, `re`, `datetime`, `pathlib`, …) |
| adapts to *your* programs | **yes — the product**: capture → harvest → gate → step (`docs/FORKING.md`) | fixed feature roadmap upstream |

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
  exit 90 (`docs/LYPNING.md` §5).

## Reproducing

```bash
pip install pydantic-monty==0.0.21
python3 study/monty/grade_monty.py    # → corpus loaded: N   abs-path skipped: N   nondeterministic skipped: N   graded: N
python3 study/monty/grade_lypning.py  # → graded N   wall Ns; then one `<class> <count> (<pct>)` line each
python3 study/monty/perf_matrix.py    # compute matrix, 6 workloads × 6 arms
python3 study/monty/perf_endtoend.py  # the clean-subset sweep
```

Both scripts print the counts they graded; quote those, from your run, with
its date. Classes: `MATCH`, `BOTH-FAIL`, `UNSUPPORTED`, `LOUD-ERROR`,
`SILENT-DIFF`, `TIMEOUT`, `REF-TIMEOUT`. Pass: lypning's `SILENT-DIFF` is 0
modulo the identities in `.github/known-mismatches.json`
(`.github/scripts/known-mismatches.py`) — a new one is invariant 1, file it,
never widen a table; Monty's count is a data point; timeouts, nondeterminism
and stderr grade as §C3 says. The scripts pin the upstream container
(`/home/user/lypning/src`, `/root/.lypning/bin/lypning`, `-mp-i386`), read none
of the variables `engines.env_var_for` spells (`LYPNING_BIN`, `LYPNING_L_BIN`,
`LYPNING_MP_BIN`), and `perf_matrix.py` fails without `lypning-mp`: issue #45.
No `lypning-l` column exists; `lypning conformance --engine lypning-l` is its
instrument (§C3). The reference CPython was the container's 3.11.
