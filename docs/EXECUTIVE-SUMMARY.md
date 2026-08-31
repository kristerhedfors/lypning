# Executive summary — does lypning improve anything, and where?

**2026-08-31.** This document answers one question as objectively as the
measurements allow: *if you route an agent's Python through lypning instead of
plain CPython, what gets better, what gets worse, and what stays the same?*
Every number is from a dated run with a committed harness (`study/paper/`,
`study/monty/`); full methodology and threats are in `docs/PAPER.md`, and this
document was itself reviewed adversarially for bias in both directions before
publication. None of these numbers should be quoted without re-running them.

The one-line answer: **on the measured workload, lypning improves end-to-end
latency (1.50× distinct-weighted, 2.35× weighted by real invocation counts)
while diverging from CPython on one ledgered program — fewer than CPython 3.13
scores against 3.11 on the same instrument. A pre-warmed CPython fork pool
beats every shipped lypning configuration on the distinct-weighted measure
(2.04× vs 1.50×) and is correct by definition; it was not measured
invocation-weighted, and the two designs compose rather than compete.**

---

## Where lypning improves — measured

**1. Whole-workload latency against what harnesses do today (cold `python3`).**
Over the 745 corpus programs that run cleanly in the sandbox, the cold-spawned
chain completes the sweep at 1.50× cold CPython; the warm chain measured 1.43×
the same day — warming the first tier did not help the chain, because its cost
is dominated by fallback spawns. Over all 1,990 graded programs: 1.76–1.77×
across two sweeps. Weighted by how often each program was *actually invoked*
(6,171 invocations, capture log): **2.35×**, because agents disproportionately
re-run the simple programs tier 1 serves.

**2. Latency on the programs the fast tier admits.** Tier 1 answers 64.4% of
the clean subset. Cold-spawned, that arm averages 2.67 ms per program against
17.10 ms for cold CPython (6.4×); warm in-process it averages 2.23 ms per
program over the whole sweep (8.05× against that run's 17.96 ms baseline).
Both averages include the 262–264 refusals, which cost little; attributing the
entire arm wall to answered programs alone still bounds the warm cost at
≈3.5 ms per answered program. The gain on admitted programs is 5–8× depending
on shape and denominator, not an order of magnitude.

**3. The silent-wrong-answer rate.** On one instrument over the same programs,
silent divergences from the CPython 3.11 reference: **lypning 1** (a ledgered
musl-libm `pow` last-ULP case) — against PyPy **39**, MicroPython **64**, Monty
**23**, and CPython 3.13 itself **9**. The comparison needs its denominator
stated: tier 1 posts its 1 divergence *on the 64% of programs it answers*,
with tables tuned in-sample to refuse what it cannot match, while the others
answer everything they can and diverge in the open. Within that framing the
result stands: the cold-spawned chain answered 744 of 745 with the one ledgered
exception and zero errors CPython would not also produce. The warm chain
currently produces **2** such errors, from an unresolved defect in which the
in-process library arm errs on 2 programs the binary correctly refuses.

**4. Against the Monty substrate, both warm.** Over the same 745 programs with
identical per-program temp-cwd churn, the liblypning arm completes the sweep at
2.23 ms/program against the Monty pool's 9.41 — but the arms do different work:
lypning *declines* 262 programs cheaply while Monty attempts everything, so
per answered program the honest bound is ≈3.5 ms against ≈25 ms. lypning
matches CPython on 480 programs with 1 silent divergence; Monty matches on 275
with 23. The shape that answers *everything* — the warm chain at 12.60
ms/program — is **slower on wall than Monty's pool** (9.41): what the extra
3.2 ms buys is 742 correct answers instead of 275 correct plus 447 errors and
23 silent wrongs. Whether that trade is worth it depends entirely on what an
error costs the caller, which is the next point.

**5. Failure economics in an agent loop — plausible, not measured end-to-end.**
In the CodeAct deployment a Monty error returns to the language model; in the
lypning deployment a refusal is answered inside the stack and the model never
sees it. We did not drive the CodeAct loop with a live model, so the per-error
cost (a model turn) is a modeled quantity, not a measurement — and the
comparison is architecturally confounded: nothing prevents a Monty deployment
from adding its own CPython fallback on error, at which point this advantage
belongs to whoever builds the chain, not to either interpreter. What is
measured is only the error *count* each substrate hands its caller on this
population: 447 against 0 (cold chain) / 2 (warm chain).

**6. Adaptability — a property, not a measurement.** The capability tables are
derived from a captured corpus by committed tooling, the corpus grows passively
during normal work, and a prospective holdout is registered
(`study/paper/holdout_registration.json`). No competing system in this
comparison retunes itself to a user's workload. This is a design fact; its
value is workload-dependent, and the same in-sample tuning is the evaluation's
biggest threat (paper §8).

## Where lypning does not improve

**1. Against a warm CPython pool, per distinct program.** A pre-warmed CPython
forking per program serves the same 745 at 8.39 ms each — 2.04× cold CPython
against the shipped chain's 1.50×, correct by construction. The pool was not
measured invocation-weighted (the weighting that lifts the chain to 2.35×
would plausibly lift the pool too). The pool's costs are operational — a
resident daemon, memory, lifecycle, a harness change — and it is slower than
warm tier 1 on admitted programs (8.39 vs ≈3.5 ms), which is why the
measurements point at composing the two: tier 1 with the pool as backstop,
arithmetic estimate ≈3× (unbuilt, and sensitive to which baseline is used).
The fair statement: **where a resident daemon is acceptable, the pool is the
stronger baseline today and lypning's remaining edge is the admitted-program
path; where it is not, lypning's shipped chain is the best measured option.**

**2. Sustained compute.** On six compute-bound workloads lypning runs 1.9–4.5×
*slower* than CPython (2026-08-30, `docs/COMPARISON.md`). The entire speedup is
startup and dispatch; long-running programs get only overhead.

**3. Per-program worst cases.** The chain is slower than cold CPython on 216 of
745 programs (29.0%) — p90 penalty +1.70 ms, worst measured +173 ms
(2026-08-31). A refusal-heavy workload pays the chain's overhead without its
payoff.

**4. Moving parts (observation, not measurement).** lypning adds a binary, a
dispatcher, hooks, a shim and a battery to what was one interpreter. The 2
programs on which our own library arm disagrees with our own binary — found
2026-08-31, unresolved — illustrate that the added machinery is a place for
bugs to live.

## Where the comparison is not apples-to-apples

- **Monty / ADK-Rust CodeAct** is a sandboxed, snapshottable, in-process
  tool-calling substrate; lypning is a drop-in process-level accelerator with
  no sandbox of its own. They answer different questions and can coexist. The
  numbers above compare only the axis they share: fidelity and cost on real
  agent programs. (pydantic-monty 0.0.21, confirmed the current PyPI release
  2026-08-31.)
- **PyPy** is built for long-running programs; this corpus is its worst case,
  and its 39 divergences are documented behavior, not bugs. The fair reading is
  "wrong tool for this workload", not "broken tool".

## Confidence, stated plainly

These results are **in-sample** (the capability tables were tuned on the same
growing corpus they are evaluated on; the registered holdout will produce the
first out-of-sample number), from **one machine**, one harness, one model
family, one user's task mix, with wall-clock ratios from two sweeps
(correctness counts reproduced exactly; walls moved with load, so ratios are
the stable quantity — and two cold-CPython baselines appear above, 17.10 and
17.96 ms/program, from different days' runs; each ratio uses its own run's
baseline). The evaluated subset is the quarter of the corpus that survives
having its repository context removed, and the excluded programs are measurably
more complex — the direction that flatters lypning's coverage. The corpus
contains this project's own development sessions (35.4% of entries mention
lypning), which contaminates the *profile*; the *evaluation* subset contains
none of them (checked). All quantified in `docs/PAPER.md` §8.

## The verdict table

| axis | measurement | verdict |
|---|---|---|
| whole-workload wall vs cold CPython | chain 1.50× distinct / 2.35× as-invoked | **improves** |
| same, vs warm CPython pool | pool 2.04× distinct beats chain 1.50×; pool unmeasured as-invoked | **loses per distinct program; open as-invoked** |
| admitted-program latency | 5–8× depending on shape and denominator | **improves** |
| silent-wrong-answer rate | 1 (ledgered) vs 9–64 for every other engine, on the 64% answered | **improves, with the denominator stated** |
| vs Monty substrate, both warm | ≈3.5 vs ≈25 ms per answered program; 480/1 vs 275/23 correctness; but the all-answering warm chain is slower on wall than Monty's pool (12.60 vs 9.41 ms) | **improves on correctness-per-cost; wall depends on what an error costs** |
| failure economics in an agent loop | error counts measured (447 vs 0–2); per-error cost modeled only | **plausible, unmeasured** |
| sustained compute | 1.9–4.5× slower | **regresses** |
| operational simplicity | more moving parts; 1 live cross-arm defect | **regresses** |

If the question is "should a coding harness route `python3` through lypning
rather than doing nothing" — on this evidence, for this workload shape: yes.
If the question is "is lypning the best possible use of this engineering
effort" — the warm-pool result says a resident kernel pool is the stronger
baseline where a daemon is acceptable, and the strongest measured configuration
is likely the unbuilt composition of the two.
