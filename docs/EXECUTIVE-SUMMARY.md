# Executive summary — does lypning improve anything, and where?

> **Status (2026-09-05):** every number below was measured on 2026-08-31 on
> `lypning → lypning-mp → cpython` (`lypning-mp` is the oracle — measured, never
> routed to — since 2026-09-04, `CHANGELOG.md` #38) with the `lypning` core
> built that day on the upstream container. Nothing has been
> re-run on the chain that ships, `lypning → lypning-l → cpython`
> (`engines.ENGINE_ORDER`); the pool-backstopped arm is the only shape that
> matches what ships, and it has no `lypning-l` leg.

Does routing an agent's `python3` through lypning beat cold CPython, and where
not. Harnesses: `study/paper/`, `study/monty/`. Method and threats:
`docs/PAPER.md` §8. Re-run before quoting (`CLAUDE.md` invariant 3).

## The verdict table

The clean subset is the 745 corpus programs that ran cleanly in the sandbox on
2026-08-31 (`docs/PAPER.md` §5.4); "distinct" weights each program once,
"as-invoked" by the capture log's invocation counts. Every cell names its
harness under `study/paper/` and its date.

| axis | measurement | verdict |
|---|---|---|
| whole-workload wall vs cold CPython | chain 1.50× distinct, 2.35× as-invoked over 6,171 logged invocations (2026-08-31, `measure_all.py`) | **improves** |
| same, vs a warm CPython fork pool | pool 2.04× distinct beats chain 1.50×; pool unmeasured as-invoked (2026-08-31, `warmpool.py`) | **loses per distinct program; open as-invoked** |
| latency on the programs `lypning` admits | 5–8× by shape and denominator: 2.67 vs 17.10 ms cold-spawned, 2.23 vs 17.96 ms in-process, both averages including the 262–264 refusals (2026-08-31, `measure_all.py`, `warm_parity.py`) | **improves** |
| silent-wrong-answer rate | `lypning` 1 — entry `py-ab7286f43b7a`, family `float-pow-last-bit`, `.github/known-mismatches.json` — on the 64.4% of the clean subset it answered; PyPy 39, standalone (upstream) MicroPython 64, Monty 23, CPython 3.13 9, each answering everything (2026-08-31, `measure_all.py`). The oracle's own catalogue is `lypning oracle` (§C12), a different instrument. | **improves, with the denominator stated** |
| vs the Monty substrate, both warm | ≈3.5 vs ≈25 ms per answered program; 480 matches / 1 silent vs 275 / 23; the all-answering warm chain 12.60 ms/program against the Monty pool's 9.41 (2026-08-31, `warm_parity.py`; `docs/COMPARISON.md`) | **improves on correctness-per-cost; wall depends on what an error costs** |
| failure economics in an agent loop | error counts measured, 447 vs 0 cold / 2 warm (2026-08-31); the per-error cost, a model turn, modeled only. nothing prevents a Monty deployment from adding its own CPython fallback on error, at which point this advantage belongs to whoever builds the chain, not to either interpreter. | **plausible, unmeasured** |
| sustained compute | 1.9–4.5× slower on six compute-bound workloads (2026-08-30, `study/monty/perf_matrix.py`). The entire speedup is startup and dispatch; long-running programs get only overhead. | **regresses** |
| per-program worst cases | slower than cold CPython on 216 of 745; p90 +1.70 ms, worst +173 ms (2026-08-31, `measure_all.py`). A refusal-heavy workload pays the chain's overhead without its payoff. The mechanism is `routing.py`'s grades — a LATE costs a CPython spawn, a WASTED an in-process parse (§C5). | **regresses on refusal-heavy work** |
| operational simplicity | a binary, a dispatcher, hooks, a shim, a battery, an optional daemon. The library and the binary drift when built from different trees; `lypning doctor` prints `FAIL core/library agreement` when they disagree (`engines.library_binary_drift`; §C7). The lesson stands even though the bug did not: more artifacts is more that can drift, silently. | **regresses** |
| the composition: `lypning` in-process, pool on refusal | 1.77×, 745/745 correct — against the cold chain 1.51× and the pool alone 1.40× in the same run (2026-08-31, `pool_chain.py`). A fork cannot re-seed hash randomization: 3 of 745 diverged without the caller's `PYTHONHASHSEED`, 0 with it (`pool.py` docstring). The pool is correct when it is started as the interpreter its callers think they are getting — a deployment rule, not a free property. No command reports the seed a running pool was started under. | **improves; built as `lypning pool serve` + `LYPNING_POOL`** |

The verdict of 2026-08-31, for the chain it measured: where a resident daemon
is acceptable, the composition is the best measured configuration; where it is
not, lypning's shipped cold chain is. The second clause names the pre-oracle
chain and must be re-measured on `lypning → lypning-l → cpython` before it can
stand.

## Regenerating it on the chain that ships

- From this tree: `lypning bench` (one arm per engine; prints the count it
  loaded), `lypning conformance --mixture both` (MATCH / UNSUPPORTED /
  MISMATCH per variant; `MISMATCH 0`; `dispatchers agree N/N`) and `lypning
  doctor` (`0 FAIL`) — `docs/VERIFICATION.md` §C3, §C4, §C7. Coverage is read
  per variant from that run, never from the clean-subset figure above.
- `study/paper/*.py` hardcode `/root/.lypning/bin/lypning` and
  `lypning-mp-i386`; porting them to `engines.find()` / `paths.state_dir()` is
  issue #45, and no row above re-runs from this tree before it lands. The
  oracle arm is a hole when `lypning-mp` is not built, never a zero (§C12).
- Entry points: `lypning run -c` dispatches in `engines.dispatch` and is the
  only path that reaches `LYPNING_POOL` (`engines._pool_socket`; an unreachable
  pool falls back to a cold spawn); `lypning -c` and the shim dispatch in
  `main.rs` and never do. The composition row is the `lypning run` shape.

```bash
lypning conformance --mixture both; echo $?  # MISMATCH 0 · dispatchers agree N/N · 0
lypning doctor; echo $?                       # 0 FAIL, core/library agreement ok · 0
```
