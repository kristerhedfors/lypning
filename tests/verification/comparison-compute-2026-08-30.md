# Sustained compute — six workloads, six arms

`study/monty/perf_matrix.py` · 2026-08-30 · upstream container · pydantic-monty
0.0.21 · `lypning` core built that day · `lypning-mp` is the oracle — measured,
never routed to. Each workload validated to byte-identical stdout on every
engine, then timed as the median of 5 interleaved rounds; ratios are against
CPython's wall clock. Quoted by `docs/COMPARISON.md`; not reproducible from
this tree unedited (issue #45).

| workload | CPython | `lypning` | liblypning | `lypning-mp` (oracle) | Monty (warm feed) | Monty (CLI) |
|---|---:|---:|---:|---:|---:|---:|
| int loop (3M) | 218 ms | 1.85× | 2.08× | **0.78×** | 1.91× | 1.98× |
| float loop (2M) | 135 ms | 2.31× | 2.51× | 1.36× | 2.36× | 2.33× |
| str methods (120k) | 83 ms | 2.82× | 3.41× | 5.88× | **2.13×** | 2.61× |
| list churn (400k) | 60 ms | 2.92× | 3.24× | **1.00×** | 2.12× | 2.13× |
| dict churn (600k) | 122 ms | **2.12×** | 2.48× | 23.41× | 4.42× | 4.52× |
| recursive calls (fib) | 29 ms | 4.51× | 5.15× | **1.05×** | 2.31× | 2.37× |
