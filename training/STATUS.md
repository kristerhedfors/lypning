# Training programme status — 2026-09-16

One page for the operator and the reviewing session: where the numbers stand,
what is built, what is next, how much the needle can be expected to move, and
whether the instrument can see it move. Every number carries its run and date
(root `CLAUDE.md`, invariant 3); nothing here is a new measurement. Sources are
the pre-registration, `LADDER.md`, `REVIEW.md`, `AUDIT.md`, the run summaries
under `runs/`, the reviews under `reviews/` and the round-02 smoke report.

## 1. The goal, in one sentence and one number

Adapt `Qwen/Qwen3.8-27B` so that an agent with **no knowledge that lypning
exists** writes ordinary first-draft programs that `lypning-l` runs natively at
a rate materially above the base model, **without getting more answers wrong**,
without dropping imports the engine serves, and without writing longer programs
(`LADDER.md` §target state; `TRAINING.md` §problem).

The number that would show it: the paired, cluster-bootstrapped change in the
**correct-and-native first-draft rate** on an unconditioned task benchmark
(the ladder's eval-2), reported beside the subset-legality rate (SLR), with
gates A (correctness not more than 2pp below base), B (supported-import
retention at least 0.80× base) and C (mean completion tokens not more than 20%
longer), engine fingerprint and serving stack quoted. Minimum detectable effect
as pre-registered: the 95% interval's **lower bound above +3pp**
(`PREREGISTRATION.md` §7b, 2026-09-14).

The programme's metric has changed generation three times (correctness pass@1
on the rewrite corpus; SLR; correct-and-native on task families). That drift is
itself a finding, addressed in §6.

## 2. Scoreboard: every number that exists, with its date

All eval numbers below are on the frozen 74-case rewrite hold-out (manifest
`80b2fc52…`, `prompt_sha cbb7be44937a6b41`), which by the project's own
admission measures **compliance with a rewrite instruction, not the deployment
prior** (`REVIEW.md` §6, 2026-09-14). They are the numbers we have, not the
number we want.

| What | Value | Run, date, identity |
|---|---|---|
| Stock Qwen, first-draft pass@1, k=16, thinking off | 0.4257 (95% CI 0.3269–0.5270); pass@16 0.5676 | `qwen38-regrade-20260913b`, 2026-09-13, engine `9d412a3131dc6a8a`, provider novita, seed 1234; the promoted baseline of record |
| Same completions at three earlier engines | 0.4037 → 0.4375 → 0.4375 | `qwen38-baseline-k16` (2026-09-12) and two regrades; zero new inference, so the spread is engine drift, not model movement |
| Verified rejection-sampled LoRA, run of record (v1) | correctness 41.16% → 45.62%, +4.46pp, CI [+1.52, +7.86]; McNemar p = 1.0000 (gained 2, lost 2). **ΔSLR −1.00pp**, CI [−3.36, +1.51]. Verdict: no win. $28.70 | `qwen38-base-arm-v3` vs `qwen38-lora-r16-v3`, 2026-09-14, one H200, `fla-0.5.2`, engine `9d412a3131dc6a8a`, seed 1111, one training seed |
| Context-distilled LoRA (v2 run 1, hinted sampling → unhinted training) | correctness 40.71% → 44.55%, +3.84pp, CI [+0.80, +7.50]; McNemar p = 1.0000. **ΔSLR −0.21pp**, CI [−3.19, +2.67]; gates A/B/C pass. Verdict: no win. $23.08 | `qwen38-base-torchref` vs `qwen38-hinted-v4`, 2026-09-14, one H200, torch-reference kernel, engine `83bc54b6dcd43f46`, seed 1111 |
| Noise floor, same weights through two serving stacks | ΔSLR −0.22pp, CI [−2.46, +2.03] | `qwen38-baseline-k16` vs `qwen38-base-arm-v3`, 2026-09-14 |
| Noise floor, same weights through two kernels | **ΔSLR +1.57pp**, CI [−0.51, +3.89]; correctness −0.14pp | `qwen38-base-arm-v3` vs `qwen38-base-torchref`, 2026-09-14. The kernel moved legality more than either adapter did |
| RL reachability on the refusing tail, k=16 | 84.62% of 52 tier-1 hold-out cases reachable, 42.31% rewardable; train pool 82.38% / 34.72% over 193 | `LADDER.md` stage 0a, 2026-09-14, engine `23684d6c40738fcf`; train-pool row provisional (53 mismatches over 14 cases found in the replay) |
| Round-02 pipeline on Hugging Face, pooled sandboxes | plumbing complete: 16/16 starter references verified, SFT and GRPO smoke stages sealed, planner ran. **No model-quality number** | job `6aa9c4c35527934177ee6c46`, 2026-09-15, tiny random Qwen config, never the 27B weights (`reports/2026-09-15-fable-round02-smoke.md`) |
| Round-02 pilot on the 27B weights, task-first path | SFT 20 steps (loss 0.83 → 0.54), step 5 selected; probe 172/200 correct, GRPO admitted, GRPO flat (step 0 kept); dev 7 cases: base 78.6% / 75.0% correct / correct-and-native, SFT 82.1% / 75.0%; test 7 cases: base 85.7% / 57.1%, SFT 82.1% / 57.1%, paired native delta 0.0pp [−10.7, +10.7]. **Eval-2: base arm blocked at 384/1,200 draws by an engine timeout on a CPU-bound candidate under 16-way concurrent scoring (partial base 87.4% / 69.8%); SFT and GRPO arms never ran** | job `6aaa87465527934177ee9f34`, 2026-09-16, engine `2e079e786a655ab6`, policy v3, k = 4 (`reports/2026-09-16-fable-round02-run.md`) |
| Eval-2 base rate on the training bank (legacy tree), full pilot draw | 87.9% correct, 68.7% correct-and-native, family macro over 64 cases; power at N = 300: concentrated +5pp 97%, uniform +10pp 76%, uniform ≤ +8pp ≤ 20% | run `eval-20260916-063539`, 2026-09-16, k = 16, engine `2e079e786a655ab6` (`EVAL2.md` §7, §11) |

Data side, same date:

| Asset | Count | Status |
|---|---|---|
| Admitted training cases on the task-first path | **64** (train v1, 49 families, 13 fallback-control), reviewed by authoring and solving agents, not by Codex | frozen 2026-09-16, `EVAL2.md` §11; the first eval-2 bank of 300 cases froze the same day |
| Starter smoke curriculum | 16 families | smoke fixture by declaration; the pilot floor is 18 and `validate_pilot` rejects the starter by name |
| Reviewed project task catalog | 12 tasks | collection fixture, not a split |
| Question proposals from the 2026-09-15 pilots | 63 structurally valid of 120 requested (`none` arm); 0 of 120 (`medium` arm, all length-truncated) | unreviewed; four sampled proposals already found contradictory (`reviews/2026-09-15-question-pilots.md`) |
| Legacy rewrite-corpus SFT rows | 154 / 117 / 318 / 326 | historical regime; not reused on the task-first path |
| Rewrite-material ceiling | 259 candidate cases, 178 already in the corpus | `AUDIT.md`, 2026-09-13: "not enough refusal material left to fine-tune against" |

## 3. What is done

- **Two complete, matched, pre-registered training rounds** with their controls,
  both null on the endpoint that matters and both documented to the last dollar.
- **An instrument that catches its own errors**: engine fingerprints on every
  legality number, serving stack as part of arm identity, the amended McNemar
  rule that closed a false positive from engine work alone, the kernel null
  that would otherwise have manufactured a +1.2pp win of the wrong sign.
- **The task-first pipeline** (`pipeline.training`, `gpu/train_verified.py`):
  grouped multi-input verified bundles, sealed adapters, capability and
  population gates, train-only RL admission probe, matched seeds, paired
  family-component reports, portable round plans.
- **Two candidate execution boundaries**: Docker on a disposable worker, and
  since 2026-09-15 pooled Hugging Face sandboxes with one CPython base digest on
  both sides of the identity handshake; the latter smoke-tested on a real GPU
  (PR #79). Codex's review of 2026-09-16 held it for two fixes, now in the PR:
  a Space name is not an immutable image, so the runner fails closed on the
  Space's Hub commit and on the identity every response carries; and the
  artifact repository must be private before anything is submitted.
- **Engine work paid for by the programme**: `math`, `type()`, `%.2d` and the
  hillclimb-83 fixes came from on-policy evidence; stage 0a alone added 14 cases
  to `data/engine-mismatches.jsonl`.
- **A data-production loop with hard budgets** (49,152 output tokens per
  session, no automatic retries) and a review queue, but nothing admitted yet.

## 4. What is next, in order, with what each step decides

| # | Step | Owner | Cost | Decides |
|---|---|---|---|---|
| 1 | Codex decision on gate 6: pooled tier admitted, or dedicated required | Codex | $0 | whether round-02 may run at all on HF |
| 2 | Run the `question-proposals` profile pilot (reasoning off, 12 × 4,096 tokens), then semantic review and lineage review of what it yields | Codex reviews, Fable runs | tokens only | whether the question feed produces independent families at all |
| 3 | **Build eval-2** (`LADDER.md` stage 4): ≥300 unconditioned ordinary-task prompts, no mention of lypning, own lock and `prompt_sha`, both arms baselined before any adapter; pre-registered in [EVAL2.md](EVAL2.md) before any draw is sampled | Fable authors, Codex reviews | ~$40 | the only benchmark on which a positive result is a deployment claim |
| 4 | Stage 0b prompt ceiling: base Qwen with the subset spec in the system prompt, on eval-1 and eval-2 | Fable | ~$5 | whether the boundary is elicitable from a description (≥10pp SLR up) or only installable by a signal |
| 5 | Author and review the pilot dataset to the gate-4 floor and well beyond it (≥18 independent families is a floor; aim for the hundreds), verified through the admitted boundary, new bundle frozen | Codex + operator review, Fable prepares | tokens + review time | closes gates 3 and 4 |
| 6 | Round-02 proper: base-dev control → verified SFT (3 seeds) → train-only probe → GRPO only if the probe is informative → locked test eval per arm | Fable, under an operator cost ceiling | h200 at $5/hr; the ladder's ~$130–160 total | the first real number on eval-2 |
| 7 | Stage 5, live sessions: refusals per 100 programs through opencode against the same script on base | Fable | harness time | the README number |

Steps 2, 3 and 4 do not depend on each other and should run in parallel. Step 6
does not start until 1, 3 and 5 are closed. The `training/` directory rename is
the operator's call and gates nothing.

## 5. Expected movement of the needle

Honest priors, not measurements. Each is conditional on a step above.

- **On eval-1 (the rewrite tail)**, two adapters trained on ~230–320 verified
  rows moved SLR by −1.00pp and −0.21pp against noise floors of −0.22pp and
  +1.57pp (2026-09-14). A third adapter of the same kind on the same data
  should be expected to do the same. Do not run one.
- **The reachability ceiling is high but the rewardable ceiling is not**: at
  k=16, 84.62% of tier-1 hold-out cases can be reached natively but only 42.31%
  natively and correctly (stage 0a, 2026-09-14). RL has something to reinforce;
  more than half of the tail is not rewardable at any policy the base model can
  sample, and the kinds `decorator`, `generator` and `walrus` had no native
  draw in 16. A finite 0/16 is not proof of impossibility under RL; it says
  those samples give RL nothing to reinforce.
- **Stage 0b is the fork.** If the spec in the prompt lifts SLR by ≥10pp, the
  model can hold the boundary when shown the map, and distillation on ordinary
  tasks (not rewrite prompts) is the recipe with a real chance; the v2 run's
  null does not rule this out because its population was the wrong one. If 0b is
  flat, no amount of SFT on descriptions helps and only a per-kind signal
  (preference or RL) installs the boundary.
- **On eval-2 nothing is known**, including the base rate. It is plausible that
  an ordinary-task SLR for stock Qwen is already far above the tail's 42–44%,
  in which case the deployment headroom is smaller than the tail suggests and
  a +3pp lower bound is a demanding target. It is equally plausible the tail
  under-represents kinds that ordinary tasks hit constantly. Measuring the base
  rate is step 3, and it is worth more than any training run.
- **Detectability**: on 74 cases a fine-tune that flips fewer than six cases and
  loses none cannot fire the pre-registered rule at any effect size, and the
  headline verdict cannot fire below roughly +11pp on the whole hold-out
  (`AUDIT.md` §FN, `PREREGISTRATION.md` §3c). A larger benchmark is
  necessary but 300 tasks alone do not guarantee power for +3pp: with a
  paired cluster bootstrap, power depends on the discordance rate, the family
  clustering and the base rate, none of which is known for ordinary tasks.
  Size eval-2 from a design-specific power analysis on its own pilot draws
  before freezing it. That is the second reason eval-2 comes before training.
- **Kill criteria stand** (`LADDER.md` §6): if 0b is flat and the by-kind curves
  do not converge, or if eval-1 converges but eval-2 stays flat across two seeds,
  stop training and put the budget into the engine, which has so far returned
  more per dollar than the adapters.

## 6. Are we measuring properly?

For the narrow question ("does this adapter make the model comply with a
rewrite instruction more often, without getting more wrong?") yes, and the
answer was no, twice. For the deployment question, **not yet**, for reasons
the documents themselves record:

1. **Wrong population.** Every `refused:*` hold-out prompt names the refusal
   (`REVIEW.md` §6, 2026-09-14). SLR on it is compliance, not the prior an
   agent brings. Eval-2 does not exist.
2. **Metric drift.** Correctness pass@1 → SLR → correct-and-native, across three
   document generations. The next round must freeze one primary metric before
   anything runs and report the other two beside it. Recommended primary:
   correct-and-native sampled pass@1 on eval-2, macro over families, with SLR
   and correctness as gated secondaries.
3. **Noise floors exceed effect sizes.** Kernel swap +1.57pp on legality;
   engine regrade +3.38pp on correctness with zero new inference (2026-09-12
   to 2026-09-13). Both are now preconditions of a comparison, which is right,
   but they mean a single-seed, single-stack delta under about +2pp is noise by
   construction.
4. **Contamination in the frozen split.** 27 of 74 hold-out cases have a train
   neighbour at ≥0.85 prompt similarity; 58 of 175 train cases were excluded
   from sampling rather than re-cutting the split (`AUDIT.md`,
   `PREREGISTRATION.md` §1b, 2026-09-12). Eval-2 must be split by family and
   source group, as the schema-3 bundle already does.
5. **One seed.** Both runs of record are one training seed; the
   pre-registration now demands three. No result with fewer is a result.
6. **The corpus is exhausted.** 259 candidate rewrite cases exist in total;
   the sub-tail cannot be widened 25×. Ordinary tasks are the only population
   with supply, and their verifier contract (deterministic UTF-8 stdout, exit
   zero) bounds what can be admitted.
7. **The current pipeline has never trained the 27B weights.** The smoke
   proves protocol and plumbing; the exact Qwen/TRL GPU stack found two
   argument-level breakages on its first real run (2026-09-15). Expect more at
   full scale; budget a failed first attempt.

What is measured correctly and should be kept: matched base arm regenerated in
the same container, engine fingerprint and serving stack as arm identity,
paired cluster bootstrap with a pre-registered MDE, gates that void rather than
discount, sealed adapters, the train-only probe before RL, per-kind refusal
vectors beside the scalar.

## 7. How to train to reach the goal

The recipe below is the one the evidence supports today. It is a proposal for
Codex's review, not an approval.

1. **Freeze the primary metric and the benchmark first.** Eval-2 as in step 3:
   ≥300 ordinary tasks reverse-prompted from real captures, no runtime named,
   family/source/AST-grouped split, own lock. Baseline base Qwen on it at k=16,
   thinking off, one pinned container and kernel, before any adapter exists.
   Report SLR, correctness and correct-and-native with by-kind vectors.
2. **Change the training population to match.** Train on ordinary-task prompts,
   never on rewrite prompts. Supervised targets come from the model's own
   verified successes on train families (rejection sampling at k=32, keep ≤4,
   diverse, ceiling cases keep one), plus retention examples where fallback is
   the correct answer, so the model is not taught to avoid imports the engine
   serves.
3. **Let stage 0b choose the sampler.** If the spec lifts SLR ≥10pp, sample
   with the spec and train on bare prompts (distillation); if flat, sample bare
   and rely on the verifier to select, and plan the preference or RL stage from
   the start.
4. **SFT as the first arm, three seeds, LoRA rank 16 alpha 32 on all text
   projections** (the `out_proj` exclusion was an error inherited from the
   earlier model; fixed 2026-09-13). Assistant-only loss, non-thinking template,
   the fixed decoding contract (temperature 0.7, top-p 0.8, top-k 20, presence
   penalty 0). Select the checkpoint on dev by correctness, tie-break by
   correct-and-native. Step 0 is a valid selection.
5. **Probe before RL.** Four generations per train prompt from the selected
   SFT policy. RL only if at least two train groups are informative (mixed
   rewards); report the informative fraction, all-equal groups and truncation
   by family. Dr. GRPO, beta 0, four generations, reward exactly as the
   contract: correct-and-native 1, correct with valid refusal 0.25 on coverage
   and 1 on controls, everything else 0, mismatch aborts. Never reshape reward
   to pay for wrong code.
6. **Decide on eval-2, not eval-1.** Lock settings on dev, run test once per
   arm, paired cluster bootstrap by family component. Promote only on
   correctness non-inferiority (gate A) plus a correct-and-native lower bound
   above the pre-registered margin. Otherwise keep base or SFT and say why.
7. **Keep feeding the engine.** Every verified-wrong native answer is an engine
   bug before it is a training example; every high-frequency refusal kind on
   eval-2 is a runtime candidate ranked by independent families affected
   (`L-TRAINING-ROADMAP.md`). The two loops share one budget and the engine
   loop has the better record.

## 8. Decisions requested

- Codex: gate 6 (pooled tier) and whether eval-2 construction is authorized
  ahead of the pilot dataset (this document's step 3 before step 5).
- Operator: cost ceiling for eval-2 and for round-02 proper; approval of the
  question-proposals pilot budget; the primary metric freeze in §6 item 2.

## 9. Assessment, 2026-09-16

[ASSESSMENT.md](ASSESSMENT.md) reads this document, the ladder, the
pre-registrations, the reports and the reviews against the goal in §1 and
answers why no run so far could say whether the programme is moving: no
positive control has been run, every adapter was trained at a scale that
installs style rather than a boundary, the training side got the leftover of
the supply, the power table in `EVAL2.md` §7 keys its uniform rows by a
nominal lift the simulation does not realise on a saturated pilot, and the
examples that would carry the signal — verified native rewrites of
correct-but-refused answers — have never been admitted. It replaces §4's
sequence with a signal ladder (S0–S4) whose first three rungs cost nothing,
and an action plan with an owner, a cost and a stop rule per step. Read it
before the next paid step; §4 above stands as history until Codex rules.
