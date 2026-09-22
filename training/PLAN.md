# The plan — after seed 1111 on bank v3

**This is the live plan.** A session told to "follow the plan" starts here,
continues an `in progress` step, or takes the first `open` step, does only that
step, updates its
state below, and records the outcome in `ORCHESTRATION.md`'s ledger. It does
not skip ahead: each step's *decision* chooses the shape of the next one.
`STATUS.md` §10 still owns whether a round runs at all; this owns what the
next round is and in what order it is earned.

The evidence this plan rests on is
[`reports/2026-09-21-fable-round02-seed1111-read.md`](reports/2026-09-21-fable-round02-seed1111-read.md).
Read it before changing a step. The completed Step 0 read corrects its saved
GRPO dose, benchmark size and train-negative estimate:
[`reports/2026-09-21-codex-step0-read.md`](reports/2026-09-21-codex-step0-read.md),
Actions run `35575454075`, 2026-09-21. Its separate Codex assessment is
[`reviews/2026-09-21-codex-seed1111-step0-assessment.md`](reviews/2026-09-21-codex-seed1111-step0-assessment.md).

## What seed 1111 established (2026-09-20/21, job `6ab01cbb51992417dfccd64c`)

1. **The checkpoint selector cannot see the effect being trained for.**
   `CheckpointGate.observe` demands no correctness regression on nine
   ~180-draw sub-metrics, then ranks on correctness before nativeness. Simulated
   on the round's own dev baseline: a checkpoint with **+10pp correct-and-native
   and correctness unchanged is admitted 1.7% of the time — pure noise is
   admitted 2.1%**. "Selected step 0", twice, is therefore not evidence about
   the training. `tests/test_gate_admission.py` measured this against the real
   class, and since Step 1.1 (2026-09-21) it measures the replacement on the
   same simulation: 84.85% and 9.17%.
2. **The SFT objective carried almost no signal and was under-powered for it.**
   Loss 0.1436 → 0.0426 in 75 steps: the base already emits the reference
   programs at ≈1.15 perplexity, and rows are `prompt + reference`. LR 2e-5 is a
   full-fine-tuning number; the LoRA optimum is ~10× that (~15× under 100 steps).
3. **GRPO's saved evaluations end at step 15.** The planned dose was 20;
   there is no saved step-20 adapter or evaluation. The probe's 21% informative
   groups (286 of 1,355) is not an observed informative-optimizer-step count.
4. **The "24.55pp headroom" is mostly one case.** The dev coverage macro is
   0.7545 because one 1-case family slice (4 draws, all correct-but-refused)
   contributes 0.0 as a whole family; without it the macro is ~0.88. By status,
   dev draws are 8.1% incorrect and **2.4% correct-but-refused**.
5. **The architecture and pipeline were fine.** LoRA r16/α32 on every text
   projection, verifier, pool, per-stage uploads, seed determinism (three
   identical arms, byte for byte). Wall clock went to evaluation draws, not to
   optimizer steps. The torch-reference kernel is an arm identity, not a fault.

## Steps

State is one of `open`, `in progress`, `done (date, where)`, `skipped (why)`.
Advance a step by editing this table in the same PR as the work.

| # | step | cost | decision it makes | state |
|---|---|---|---|---|
| 0 | Read what was paid for | $0 | did dev native move inside the selector's blind spot? | done (2026-09-21, [read](reports/2026-09-21-codex-step0-read.md), GH 35575454075) |
| 1 | Fix the instrument | $0 | nothing else is readable until it is | done (2026-09-22, implementation in PRs #97–#101; validation limits below) |
| 2 | Positive control (S1 / stage 0b) | ~$126 at 512 output tokens/request; ~$225 at allowance (full k=16); rungs so far $5.74 charged or reserved | distillation route, rejection-filtered distillation, or contrastive route | in progress (2026-09-22: smoke generated and graded, pooled; 192-case target rung `35767396604` complete and **not yet graded**; rungs below; [review](reviews/2026-09-22-claude-step2-s4-review.md): revise) |
| 3 | Build contrastive targets | tokens | is there enough pair supply for a preference arm? | open |
| 4 | S4, re-specified, three seeds | ~$60–90 | the first result the instrument can read | open |

**Operator direction, 2026-09-22: free first.** Continue Step 2's free checks
and preparation. Paid inference and GPU training remain held; no paid ceiling
is approved. Finish the free readiness result before requesting a paid scope
and ceiling. `ROUND_READINESS.md` records the active checks and measured costs.

**Later on 2026-09-22.** Codex dispatched four paid Cerebras generation
runs, three of which reached the provider (Step 2 below), under the per-rung
ceilings in `step2-control.yml`; the approval is not recorded in this tree. No GPU job has been submitted. The independent
review of that work is
[`reviews/2026-09-22-claude-step2-s4-review.md`](reviews/2026-09-22-claude-step2-s4-review.md):
**revise (prepare)** — no GPU spend until the coverage-only grade of
`35767396604`, the seed-1111 kernel read, and an operator decision on a
full-split target rung and on evaluation draws.

### Step 0 — Read what was paid for ($0, CI reads, aggregates only)

The adapters at SFT steps 25/50/75 and GRPO 5/10/15 are on the Hub under
`round-02/6ab01cbb51992417dfccd64c/`, and `sft/evaluations.jsonl` holds the
per-draw dev records at every eval step. Three summaries, each printing only
statistics over draws — never a case, program or expected stdout, because the
follower streams into a public log (`s0_inventory.py`'s rule):

- **Dev metric per step** from `sft/evaluations.jsonl` and `grpo/evaluations.jsonl`:
  correct and correct-native macro at steps 0/25/50/75 (and 0/5/10/15).
  This is the stage-1a proxy `ASSESSMENT.md` §3.1 asked for (S0c).
- **Probe rollouts by native status** (`probe/probe-rollouts.jsonl`, 5,420 draws on
  train prompts) against the base dev draw: the other half of S0c.
- **Eval-2 bundle family sizes** (`eval2/bundle.json`, counts only): how many
  families under five cases enter the primary macro on the real benchmark.

**Decision.** If dev correct-native at any saved step is ≥ +2pp over step 0 with
correctness within −2pp: the treatment worked and the selector is the bug →
after Step 1, re-select offline and run **one** eval-2 pass of that adapter
against base (~$25; `base-eval2` never completed). No training needed.
Otherwise proceed to Step 1 regardless — it is required either way.

**Completed decision (2026-09-21, GH `35575454075`): proceed to Step 1;
no rescue eval-2 pass earned.** On the all-family dev macro, SFT's largest
native gain is +0.2976pp with correctness below the −2pp tolerance; GRPO's
largest is +0.1190pp. No saved checkpoint qualifies. GRPO step 20 is absent
from both the adapter inventory and evaluations. Train and dev have no shared
cases, so the probe comparison is descriptive. The train probe contains 635
correct-fallback draws; this replaces Step 3's dev-based estimate, not its
still-unmeasured count of usable pairs. All benchmark families clear five
cases; small population slices, rather than the primary macro, need attention.

### Step 1 — Fix the instrument ($0, code; each item is a PR with a test)

1. **Selector.** ~~Select on the primary metric (correct-and-native family
   macro) with correctness as a *tolerance* gate — the preregistered gate A,
   −2pp on the macro — not nine hard floors and a lexicographic key. The
   pinning test flips: null admitted ≤ 10%, +10pp native admitted ≥ 80%. Step 0
   stays selectable.~~ **Done 2026-09-21, PR #97.** `CheckpointGate` now ranks
   `correct_native` behind gate A and a per-population retention tolerance at
   three standard errors; capability floors are reported, not vetoes.
   `tests/test_gate_admission.py` measures **null 9.17%, +10pp native 84.85%**
   (4,000 trials, seed 7), inverting the two pinned assertions.

   **What this changed, and why the next steps should know it.** Gate A plus an
   argmax is not enough: a pure argmax on a noisy macro admits the null about
   half the time, because the best of several noisy evaluations is biased
   upward. The ≤ 10% half of this step's own acceptance rule therefore forces a
   **selection margin**, and the margin is `1.2816 × SE` of the family macro —
   the 90th percentile, so the constant is the rule rather than a number tuned
   until a test passed. On seed 1111's dev split that is **1.07pp**, which is
   the scale of effect this split can resolve at all; the +10pp arm of the
   simulation realises only ≈ +1.8pp of macro because `redraw` caps native at
   correct and three of five coverage capabilities are saturated. Two
   consequences for Step 4: a dev split of this size cannot select on an effect
   much under a point, and the null rate is **per observation**, so a stage
   evaluating at three steps has three chances to draw it. Step 1.4's
   `--eval-every 50` cuts observations as well as cost.

   **Revised 2026-09-22 (this PR, before arm A's first seed).** The 84.85% held
   only against a noise-free baseline. `CheckpointGate` now selects on the
   **coverage** population's correct-native macro — a control turning native is
   not a gain; controls count only through gate A and retention — against a
   margin of `1.2816 ×` the **paired, case-clustered** standard error of the
   candidate-minus-baseline delta, so the margin is per observation and the
   1.07pp figure above no longer applies. Simulation in
   `tests/test_gate_admission.py` (2026-09-22, seed 7, baseline redrawn,
   within-case correlation bracketed by KAPPA 2 / 20): null 10.53% / 9.23%;
   **+10pp native at k = 4 only 38.0% / 63.5%**; at k = 16, 75.3% / 97.7%. With
   draws fully coupled by the shared evaluation seeds the same +10pp arm is
   admitted 99.9% / 100% at k = 4 (seed 9). Which regime a trained adapter is
   in is unmeasured: read step-0 versus step-N draw agreement in seed 1111's
   `sft/evaluations.jsonl` before paying for `--eval-draws 16` (an operator
   decision with a cost).
2. **Stopping is not selection.** ~~A registered dose trains to completion;
   every checkpoint is saved (already true); selection is post hoc. No
   patience-based stop inside an S4 stage.~~ **Done 2026-09-21, PR #97.**
   `observe` returns nothing, `--patience` is gone from the CLI and from every
   command block, the SFT `break` and the GRPO `should_training_stop` are
   removed, and `best.json` keeps every observation with the reason it was or
   was not selected, so the selection can be re-made offline. This is also what
   cost seed 1111 its GRPO step 20: three rejected checks under the old gate
   ended a registered 20-step dose at 15, and Step 0 could not read an adapter
   that was never saved.
3. **Macro fragility.** Preregister a minimum family size to enter the macro
   (≥ 5 cases, i.e. ≥ 80 draws at k = 16) or case-weight within family with
   clustering kept for the bootstrap. Simulate on the eval-2 bundle first; this
   is an `EVAL2.md` amendment and precedes any k = 16 read.
   Step 0 found **803 cases / 19 families**, all at least 16 cases, in the
   saved eval-2 bundle: a five-case floor on the primary macro excludes none.
   Two control population slices have one and two cases; the dev coverage
   singleton is also a slice. Specify the scope of the rule and simulate its
   effect on those slices before amending it; do not conflate their macro with
   the all-family primary metric.
   **Done 2026-09-22.** `EVAL2.md` §4 preregisters the five-case floor on
   whole primary families, with source links retained before bootstrap filtering.
   The size-only simulation leaves the saved primary macro identical. Population
   slices remain unfiltered descriptive macros with case-weighted companions;
   no small control case disappears. Benchmark manifests record the metric policy.
4. **Learning rates for LoRA.** SFT 1e-4 (2e-4 under 100 steps); GRPO 5e-6;
   small effective batch. `--eval-every 50`. **Done 2026-09-22.** The trainer
   resolves LR from the effective dose and records it; explicit overrides win.
   The launch script, manual commands and example plan use cadence 50 in both
   stages, always evaluate the final step, and keep effective SFT batch 4.
5. **Evaluation cost.** Skip duplicate eval-2 arms when an adapter is step 0
   and record the reuse; raise `--eval-sequences`; `PILOT_TIMEOUT` 720m, and
   find out why 480m did not fire. **Done 2026-09-22, PR #100.** Sealed
   step-zero equivalence permits same-job reuse with recorded provenance; GRPO
   reuses its SFT parent, which need not be base. Matched batches are 256;
   the proposed 720m ceiling is bounded independently inside the container.
   Audit GH `35680032058` confirms the provider stored 28,800 seconds for the
   old job: the request was not lost, and the service did not enforce that
   recorded deadline as expected. Its internal reason is not exposed. See
   `reports/2026-09-22-evaluation-cost.md`; hardware smoke and spend approval
   remain prerequisites for the next paid run.
6. **Engine fixes land now** — `data/engine-mismatches.jsonl` (`sys.stdin.read(n)`).
   The arm must change anyway; this is the one window where an engine change
   costs no comparability (`arm_check.py` will refuse to join old seeds).
   **Done 2026-09-22, PR #101.** Sized stdin reads now advance by Unicode
   characters without draining the remaining stream. The recorded witness
   agrees with CPython on both variants; tests cover cursor sharing, invalid
   arguments, zero-length reads and refusal replay. Frozen witnesses stay intact.

**Step 1 handoff.** Merge the stack in order (#97, #98, #99, #100, #101), then
freeze the new engine/image and run the existing preflight and hardware smoke.
The macOS full-corpus check still finds five inherited mismatching programs,
reproduced unchanged on pre-fix commit `bcefefa`; no new mismatch was introduced.
This is not a green conformance claim. A pinned candidate with MISMATCH 0 and
an approved cost ceiling remain prerequisites for paid execution. Step 2 is
next in the sequence; no paid step was launched. See
`reviews/2026-09-22-step1-validation.md` for the verification record.

### Step 2 — Positive control (bounded paid rungs; S1, `LADDER.md` stage 0b)

The pilot bank’s **training split** at seed 1111, bare vs
`training/prompts/subset-spec.md`,
k = 16, thinking off, one pinned provider. The older `nt eval --system-file`
command reads the legacy held-out corpus, so Step 2 preparation uses
`pipeline.positive_control` to count the actual training population and both
arms before a dedicated bounded runner is admitted. Dev/test cases stay sealed. The prompting study got +22pp on Claude agents this way
(`docs/PROMPTING.md` T2).

**Decision** (read on **coverage** rows; controls are reported separately as
retention, and since this PR the grader computes it that way). Correct-and-native
up ≥ 10pp with correctness flat → the *distillation* route: sample with the
spec, train on bare prompts. **Native up (CI excludes zero) with correctness
down → rejection-filtered context distillation** (Codex's proposal, 2026-09-22):
keep only verifier-passing spec-conditioned draws — correct-native coverage,
correct non-native controls — and train them behind the bare prompt. It is
exploratory: the filter keeps wrong programs out of the loss, not the behaviour
that produced them out of the policy, so the S4 read must hold gate A and
control retention. Flat → only a per-kind contrastive signal can install the
boundary; Step 3 is mandatory. In every case the run gives the fixed selector a
known-positive to validate on.

**Rungs run** (2026-09-22, GitHub Actions, dispatched by Codex; ceilings are
`step2-control.yml`'s per-rung caps; approval not recorded in this tree):

| run | rung | cost, charged or reserved | outcome |
|---|---|---|---|
| `35749197934` | smoke 64 × 4 | — | failed before generation; fixed in #105 |
| `35751938025` | smoke 64 × 4 | $1.20715678 of $5 | 512/512, zero retries |
| `35758545464` | grade smoke | $0 | failed before a candidate ran (oracle identity); fixed in #108 |
| `35759939928` | grade smoke | $0 | **pooled** (controls counted): native +14.78pp [+3.89, +26.81], correct −9.01pp [−17.34, −2.02]; neither route earned; 157 targets over 54 cases / 29 families |
| `35762924601` | S4 target preflight | $0 | 156,691 scheduled supervised tokens vs 50,000 — about 7.6 passes over 157 rows |
| `35763603648` | targets 192 × 4 at 60 rpm | $0.90358492 of $6 | stopped at 327/1,536 on an opaque provider error; not gradeable |
| `35767396604` | targets 192 × 4 at 45 rpm | $3.63061517 of $6 | complete 1,536/1,536; references 192/192; **grade once, after this PR** |

The smoke is exploratory: 64 cases, k = 4, 30 clusters. The rungs are nested —
the 192-case set begins with the smoke's 64 and repeats their requests
seed-for-seed — so the targets rung's grade is a superset, not a replication.
**Next free read:** grade `35767396604` once (a second grade overwrites
`positive-control/<run>/grade`), coverage-only, with the `QWEN_REV` tokenizer
and `target_arms` chosen beforehand; then `s4-target-preflight` on it, which is
expected to refuse on the case floor (Step 4). Only a k = 16 rung can make the
preregistered decision.

### Step 3 — Build contrastive targets (tokens only)

What `prompt + reference` SFT lacks is contrast on nativeness with correctness
held fixed. Per train prompt: positive = a correct-native draw; negative = a
correct-but-refused draw. Sources, in order: the 5,420 probe draws already
banked; Step 2's conditioned draws; S2's verified native rewrites of refused
draws (`ASSESSMENT.md` §6). Count before building — Step 0 measured **635
correct-fallback train draws (11.7159%)**, versus 29 / 1,224 on dev (2.3693%).
Negative draws are not same-prompt pairs: count train prompts with both a
correct-native positive and a correct-fallback negative, retaining the controls
separately. The earlier ~150-pair estimate extrapolated from the wrong
population and is withdrawn.

**Decision.** Fewer than ~300 pairs → the preference arm is under-powered;
carry the signal in RL (Step 4 arm C) instead of a preference arm.

### Step 4 — S4, re-specified (three seeds; ~$60–90 once Step 1.5 holds)

| arm | recipe | why this and not what ran |
|---|---|---|
| **A — rejection-sampling SFT** | verified draws from a Step 2 target run: correct-native coverage (keep ≤ 4 per case after AST dedup, over-length dropped) and correct non-native controls (≤ 1); **target source is the run's `target_arms`, default the spec-conditioned arm** — rejection-filtered context distillation, bare-prompt training; bare-arm draws (the model's own, ReST-EM) are the declared alternative; LR 1e-4; 300–600 steps; no early stop; post-hoc selection; `--grpo-steps 0` | `STATUS.md` §7 item 2's recipe, never run — seed 1111 trained on authored references the base already emits. The pilot path requires a target run (#106) |
| **B — preference** | iterative DPO/ORPO on Step 3 pairs, best-of-n vs worst-of-n with the verifier as the rule reward, 1–2 rounds, SFT warm-up first | targets the exact axis SFT cannot; matches PPO after warm-up in the reference recipe |
| **C — GRPO, dosed** | from the better of A/B: ≥ 300–500 optimizer steps; **4 prompts × 8 generations per step** (≤ 32 sequences); **LoRA LR 1e-5**; Dr.GRPO, β = 0, `scale_rewards="none"` kept; optional `--grpo-informative-only` (probe's 0 < p < 1 cases); no-signal groups logged as a fraction, never an abort | capacity was never the limit (rank 1 suffices for policy gradient); dose and LR were. Amended 2026-09-22: LoRA wants ~10× the full-FT RL rate ([LoRA Without Regret](https://thinkingmachines.ai/blog/lora/); [TRL](https://huggingface.co/docs/trl/main/en/lora_without_regret)); `dr_grpo` divides by `max_completion_length`, shrinking short programs' steps; zero-variance groups carry no gradient ([DAPO](https://arxiv.org/abs/2503.14476), [arXiv 2504.11343](https://arxiv.org/abs/2504.11343)); a 20-group streak abort would end a 500-group dose by chance |

Order: A first (closest to the pipeline, cheapest); B only if Step 3's supply
clears; C only if A's probe is informative (seed 1111's was: 21%). Read on
eval-2 at k = 16 under the amended macro rule, three seeds, `arm_check.py`
green before the second seed is billed.

**What arm A needs before its first seed (2026-09-22, this PR).**

- **Case floor on the curriculum.** The existing `MIN_TRAIN_CASES` (1,000) now
  counts the distinct cases the target rows train on, plus two families per
  population. Arm A needs verified targets on **≥ 1,000 of the 1,355** train
  cases. No existing rung (64, 192 or 300 cases) can reach it; the smoke's 157
  rows over 54 cases cleared the token floor only by repetition. A full-split
  target rung is needed: from `35767396604`'s measured ~$0.0024/request,
  1,355 × 2 arms × k = 4 is ~10,840 requests, ~$26, ~241 min at 45 rpm — over
  the rung's 240-minute window, so sharded; a spec-arm-only draw is fewer
  requests but not half the cost, because the spec dominates input. **Operator
  decision; not implemented; the floor was not lowered.**
- **Split seed protocol note (2026-09-22).** The split seed is fixed at 1111
  for every S4 training seed. Seeds 2222 and 3333 differ from 1111 in LoRA
  initialisation (now reseeded from `--seed` before `attach_lora`) and data
  order only; dev and test are the same sealed cases. Targets graded on the
  1111 train split serve all three seeds. `job-manifest.json` records
  `split_seed`; `arm_check` treats it as an arm field.
- **Kernel enforcement.** The torch-reference gated-delta rule is enforced:
  the `fla` blocker is installed before any probe, the probe runs in a child
  interpreter, the bound implementation is recorded (`kernel_binding`), and a
  run refuses any other binding before the weights are pulled. Seed 1111's
  actual binding is **unverified**: read its `sft/experiment.json` `kernels`
  field (free CI read).
- **Re-prepare bundles.** `verifier_sha256` now also hashes
  `hf_sandbox_runner.py` and `container_worker.py`, and `code_sha256` changed;
  every existing bundle is refused.
- **Launcher.** GRPO dose defaults to 0 in `launch.py`, the job script and
  `round02.yml`, so arm A never bills GRPO; the probe still runs as arm C's
  admission evidence. A banked stage defaults to h200 / 720m. `QWEN_REV` is
  pinned in every round-02 workflow. Arm C must dispatch a group size of 8
  for probe and GRPO alike (the pilot at `b96bbfe` passes 4 to both) and
  budget the job timeout for 16–32 sequences per step.

## Kill criteria (unchanged, `LADDER.md` §6)

If Step 2 is flat **and** Step 3's supply is tiny, the model lever is capped
low on this population. Step 0 measured correct-fallback mass of 2.3693% on
dev but 11.7159% on train; use Step 3's actual pair count rather than treating
dev as a train-supply estimate. The Step 0 by-kind vector is recorded in its
read (575 of 635 train negatives carry the module kind); it is descriptive,
not a claim that every refusal is engine-addressable or model-repairable.

## Do not

- Run seeds 2222/3333 of seed 1111's configuration. `arm_check.py` would admit
  them; they would replicate a blind selector.
- Switch the kernel. It is an arm identity (`STATUS.md` §2: +1.57pp on identical
  weights); changing it means all three seeds again.
- Re-cut the bank for the 2026-09-18 log leak. It was v2-era material; every
  case id in v3 is `v3-`-namespaced, and v2 was retired for saturation anyway.
- Widen `s0_inventory.py`'s `SMALL` tuple to read Step 0. Summarise in the job.
- Lower the curriculum case floor, or regrade a Step 2 run, to make a launch
  fit. A different floor is a dated protocol decision.

## How a session follows this

1. `git pull` on `main`; read this file, then the report it cites.
2. Take the first `open` step. If its decision needs a number, get the number
   first — the reads in Step 0 are free.
3. Do the work in a PR that also edits this table's state and adds a ledger row
   to `ORCHESTRATION.md`. A step whose decision changed a later step edits
   that step here, in the same PR, with the reason.
4. Before any billed job: `round02-preflight` (all six checks) and the cost
   ceiling in `ROUND_READINESS.md`.
