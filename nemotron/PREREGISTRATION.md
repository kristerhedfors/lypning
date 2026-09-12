# Pre-registration: does a LoRA make Qwen3.8-27B write lypning-supported python?

Written **2026-09-12, before any fine-tuned model exists** — no adapter has been
trained, no training data has been sampled, and no post-fine-tune number has been
seen by anyone. Engine at `0f61407` (2026-09-12T12:46:26Z). Every number below is
reproducible by the command printed beside it; none is remembered (invariant 3).

The point of writing this first is narrow and specific: three of the decisions
here — which rule is primary, which cases count, and what the comparison is
measured against — all move the answer, and all of them would be unfalsifiable
if taken after the result was visible.

## 1. The claim under test

Rank-16 LoRA on Qwen3.8-27B, trained only on programs the model itself wrote and
that the engine verified, makes it produce more python that lypning can run
**without making it produce wrong python**. The second half is not decoration:
a model that learns to emit `print(1)` for everything would win the first half.

## 2. The instrument, and the three things wrong with it today

**The held-out split is frozen** at 74 cases,
`holdout.lock.json`, manifest `80b2fc52…733ce588`, pinned by
`tests/test_recorded.py::HOLDOUT_MANIFEST`. It does not move.

**(a) The baseline is stale and must be re-graded.** `runs/qwen38-baseline-k16`
was graded this morning against an engine that has since gained `math`, `type()`
of any class, int/float methods and `%`-precision. Every `lypning`-kind
acceptance test checks that lypning ACCEPTS the program, so the same programs
score differently now. **The baseline is re-graded against the engine the
fine-tuned model will be evaluated on, from the recorded completions, with no
new inference.** Comparing a fine-tune against the un-regraded 0.4037 would
credit it with a day of engine work.

**(b) Four held-out cases are now degenerate.** `nt refusals --held-out` says the
engine now RUNS the original program of `ntx-1eb18c506d05`, `ntx-3eb388881928`,
`ntx-4f5b30dcdfe5`, `ntx-9533782459ac` — `import math` twice, `type()` once,
`%.2d` once, which are precisely the refusals closed today. On those a model can
return the input unchanged and pass. Two of them scored **0/16** this morning.

> Decided now: the delta is reported over **all 74** and over the **70
> non-degenerate** cases, and the 70-case figure is primary. The split itself is
> not re-frozen — the lock is the integrity guarantee and it stays — and the
> exclusion criterion is mechanical (`refusals --held-out` names them) rather
> than chosen by looking at scores.

**(c) 5% of draws are truncated.** 59 of 1,184 attempts stopped at
`max_tokens: 2048` with `finish_reason: length`, across 19 cases, and **every one
of them fails** (0/59). `max_tokens` stays at 2048 for both arms: raising it for
the fine-tuned arm alone is a confound, and a model that learns to write shorter
subset-conforming programs should be allowed to show that as a win.

**(d) The held-out set was leaking into train, and ids did not show it.** Added
2026-09-12, still before any adapter exists. `nt leaks` measures three ways a
train case can be the same question as a held-out one:

| test | held-out cases affected |
|---|---|
| prompt similarity >= 0.85 to a train case | **27 of 74** |
| >= 0.95 | 11 |
| expected stdout byte-identical to a train case's | 11 |
| negative program byte-identical | 1 |

Disjoint ids buy nothing here. The corpus is capture-derived, so one agent
hitting the same wall twice in a session produces two entries differing in a
variable name, and `freeze` puts one in each split. Three of the 154 rows in the
first SFT set were verified solutions to two HELD-OUT cases.

> Decided now: **58 of the 175 train cases are excluded from sampling**, by the
> mechanical union of the three tests at a ceiling of 0.85 fixed in
> `split.SIMILARITY_CEILING`. 117 remain. The held-out set is NOT re-cut — the
> lock stays — so the cost falls entirely on the training side, which is the
> right side for it: a train case wrongly dropped costs a few examples, a train
> case wrongly kept costs the defensibility of the number.
>
> `sample.train_cases()` now excludes them by default. Its previous assertion
> was tautological — it filtered by held id, then asserted no held id had
> survived the filter.

**(e) Five held-out cases are unsatisfiable.** `nt usable` runs each case's own
negative — correct python by the corpus's definition — and asks whether it
reproduces the expected output. Five do not: a frozen `datetime.now()`,
a benchmark's timings, a set's iteration order captured under another hash seed,
a `TypeError` worded by another CPython, and an `os.listdir()` count of a
directory nothing seeds. Nothing passes them: not the model, not a human, not
CPython. They sit in the "never passes" column looking exactly like hard cases.

> **65 of 74 cases can measure a model** — 74 less 4 degenerate less 5
> unsatisfiable, with no overlap. `nt usable` names all nine.

**(f) The spend cap was decorative.** `ChatBackend.cost()` multiplies by
`NTX_PRICE_IN`/`NTX_PRICE_OUT`, which are unset by default, so every draw of the
first SFT set recorded `cost_usd: 0.0` and `--max-spend` could never trip. Both
must be exported before any sampling run.

## 3. The rules, fixed now

Run `nt power qwen38-baseline-k16` — 74 cases, k=16, 34 never pass, 16 always
pass, 24 movable. **Corrected 2026-09-12, still before any adapter exists:** the
curve first simulated only the bootstrap leg, so the headline was the power of
half a rule the pre-registration defines as a conjunction. Both legs now:

| true lift | unpaired | bootstrap leg | McNemar leg | **PRIMARY (both)** |
|---|---|---|---|---|
| +2pp | 0% | 45% | 62% | 42% |
| +4pp | 0% | 92% | 97% | **90%** |
| +8pp | 1% | 100% | 100% | 100% |
| +12pp | 43% | 100% | 100% | 100% |
| +15pp | 97% | 100% | 100% | 100% |

**AND A DISCRETE FLOOR THE PERCENTAGE HIDES.** Exact two-sided McNemar over `b`
gained and `c` lost is `2/2**b` when nothing is lost: p = 0.0625 at five cases
and 0.03125 at six. **A fine-tune that flips fewer than six cases and loses none
cannot fire this rule at any effect size.** The +4pp above is the power of a
uniform smear across many cases, which is the most favourable shape an
improvement can take; a fine-tune that completely solves three previously
hopeless cases is +4.3pp on this denominator and fires nothing. The rule asks
how many CASES moved, not how far the mean did, and both numbers are reported.

**The standing unpaired rule reaches 80% power only at +15pp.** Moving a 74-case
mean that far means taking about eleven cases from never-passing to
always-passing — a third of the 34 that never pass. An experiment whose rule
cannot see a plausible effect reports "no win" whatever happens, and the money is
spent either way.

- **PRIMARY:** the paired test. The two arms run on the *same* frozen cases, so
  they are paired by construction and the unpaired rule pays for the shared
  per-case difficulty twice. A win requires **both** the bootstrap 95% CI lower
  bound of the per-case delta to be above 0 **and** exact two-sided McNemar
  p < 0.05 on the case-level flips. Both are already implemented
  (`stats.paired_delta`, `stats._mcnemar`); neither was written for this.
- **SECONDARY, always reported:** the unpaired rule, `stats.beats`, unchanged.
  It was fixed before any run happened and it stays in the table. **Where the two
  disagree, the disagreement is the finding and is reported as one** — not
  resolved in favour of whichever is kinder.
- **The safety gate, which is not a statistic.** `conformance` MISMATCH must be 0
  and routing UNSAFE must be 0 on the engine used for both arms, and
  `nt refusals --run <eval>` must report MISMATCH 0 over the fine-tuned model's
  own output. A pass-rate gain bought with a silent wrong answer is a loss.

## 3b. The rule, run against the engine drift it was written to survive

Recorded 2026-09-12 after re-grading the frozen baseline completions at the
current engine — no new inference, and still before any adapter exists. This is
the **null** for this experiment: the same model, the same completions, a newer
engine. If the rule fired here it would fire on engine work alone.

| denominator | before | after | paired delta | 95% CI | McNemar | rule fires |
|---|---|---|---|---|---|---|
| all 74 | 0.4037 | 0.4382 | +3.45pp | [+0.8, +6.6] | p = 0.0312 | **yes** |
| **70 non-degenerate (PRIMARY)** | 0.4116 | 0.4286 | **+1.70pp** | [0.0000, +4.37] | **p = 0.25** | **no** |

The primary denominator holds and the all-74 one does not, which is precisely
what §2(b) was written for: the four degenerate cases carry more than half the
apparent gain, and a model can pass three of them by echoing its input. Measured
on those three, **11 of 23 newly-passing draws still contain the very construct
the prompt asked the model to remove, and 4 draws are verbatim echoes** — the
stock model is already exploiting the degeneracy, so this is not a hypothetical.

Type-I, measured over 400 null trials on the re-graded rates: bootstrap leg 4.0%,
McNemar leg 1.5%, conjunction ~1%. The conjunction is conservative, as intended.

Two of the four degenerate cases share a byte-identical negative program — the 52
rewrite cases hold only 51 distinct ones — so two of the six apparent gains are
one program counted twice.

## 4. What would make a positive result a lie

| confound | the check | status |
|---|---|---|
| engine differs between arms | both graded at `0f61407`; `nt compare` blocks on `holdout_manifest_sha256` + `prompt_sha` | covered |
| degenerate cases | 70-case primary figure, cases named mechanically above | covered |
| model echoes the input | the corpus's negative control: the original program is recorded per case, and an output equal to it is a fail by construction on non-degenerate cases | covered |
| model games the acceptance test | `sample.discriminate()` perturbs the input and requires the candidate to follow the mutation | **sampling path only — NOT wired into eval** |
| prompt drift | `prompt_sha` recorded per run, compared by `stats.comparability` | covered |
| grading nondeterminism | re-grade the same attempts twice and diff | to be run before the comparison |
| training contamination | train split only; `holdout.lock.json` verified before sampling and again before training | to be run |

## 5. Spend

Hugging Face for everything, decided 2026-09-12: training on HF Jobs, weights on
the Hub, evaluation inference on HF. `RUNBOOK.md` has the arithmetic; the whole
run is ~$16.

**SAMPLING** goes through the HF router at the novita price for
`Qwen/Qwen3.8-27B` — $0.42/M in, $3.00/M out, read from `/v1/models` on
2026-09-12 — bounded by `--max-spend`, which needs `NTX_PRICE_IN` and
`NTX_PRICE_OUT` exported or it is a no-op.

**TRAINING AND EVALUATION** go to HF Jobs on **one `h200`** ($5.00/hr, 141 GB),
under a platform-enforced `--timeout`. The checkpoint is 55.6 GB and rank-16 LoRA
adds ~1.5 GB of optimizer state, so it fits on a single card with room to spare
and there is no sharding — `distributed: fsdp2` comes out, and with it the
largest class of config error in the run.

**AND THE ARMS COME OUT OF ONE vLLM INSTANCE.** This amends §2(a). The baseline
re-graded above was GENERATED on novita's serving stack through the router; a
tuned arm served from our own vLLM would differ in kernels, sampling and
tokenizer handling, so a delta between them would be part weights and part
stack with nothing to separate the two. The evaluation is therefore one Job, one
vLLM process, two arms — base `Qwen/Qwen3.8-27B` with `--enable-lora`, the
held-out split generated twice, once without the adapter and once with it, same
seed and same decode budget.

So `qwen38-regrade-20260912` is **not** the reference the fine-tune is measured
against. It keeps its job — it is the engine-drift null test in §3b, where both
arms genuinely are one stack and it is the right instrument. The fine-tune's
reference is the no-adapter arm of the eval Job, which costs nothing extra
because it is the same Job.

**The experiment is abandoned, not rescued, if:** the re-graded baseline leaves
fewer than 15 movable cases; verified on-policy SFT yields fewer than 150
examples; or the safety gate fails on the fine-tuned arm.

Over the 65 usable cases the baseline point is 0.4433 (still graded by this
morning's engine, so it will move again on the re-grade), with 27 that never
pass, 16 that always do, and **22 movable**. Power is unchanged by the
exclusions: paired 80% at +4pp, unpaired 80% at +15pp.

Sampling 117 clean train cases at k=16, keep=2 is projected to yield well under
150 examples. **The sampling run therefore uses k=32 and keep=4**, decided here
rather than after seeing a thin result — roughly 3,700 draws, about $6 at the
novita price, and the abandon threshold above stands.
