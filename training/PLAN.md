# The plan — after seed 1111 on bank v3

**This is the live plan.** A session told to "follow the plan" starts here,
takes the first step whose state is `open`, does only that step, updates its
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
| 1 | Fix the instrument | $0 | nothing else is readable until it is | in progress (1.1 and 1.2 done 2026-09-21, PR #97; 1.3 done 2026-09-22, PR #98; 1.4 done 2026-09-22, PR #99; 1.5 in progress; 1.6 open) |
| 2 | Positive control (S1 / stage 0b) | ~$5 | distillation route or contrastive route | open |
| 3 | Build contrastive targets | tokens | is there enough pair supply for a preference arm? | open |
| 4 | S4, re-specified, three seeds | ~$60–90 | the first result the instrument can read | open |

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
   find out why 480m did not fire.
6. **Engine fixes land now** — `data/engine-mismatches.jsonl` (`sys.stdin.read(n)`).
   The arm must change anyway; this is the one window where an engine change
   costs no comparability (`arm_check.py` will refuse to join old seeds).

### Step 2 — Positive control (~$5; S1, `LADDER.md` stage 0b)

The training bank, bare vs `--system-file training/prompts/subset-spec.md`,
k = 16, thinking off, one pinned provider (`nt eval --system-file`, shipped
2026-09-16, never run). The prompting study got +22pp on Claude agents this way
(`docs/PROMPTING.md` T2).

**Decision.** Correct-and-native up ≥ 10pp with correctness flat → the
*distillation* route: sample with the spec, train on bare prompts. Flat → only a
per-kind contrastive signal can install the boundary; Step 3 is mandatory. In
either case the run gives the fixed selector a known-positive to validate on.

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
| **A — rejection-sampling SFT** | train on the model's *own* verified correct-native draws (k = 8–16 per train prompt, keep ≤ 4; retention rows on control families where fallback is right); LR 1e-4; 300–600 steps; no early stop; post-hoc selection | `STATUS.md` §7 item 2's recipe, never run — seed 1111 trained on authored references the base already emits |
| **B — preference** | iterative DPO/ORPO on Step 3 pairs, best-of-n vs worst-of-n with the verifier as the rule reward, 1–2 rounds, SFT warm-up first | targets the exact axis SFT cannot; matches PPO after warm-up in the reference recipe |
| **C — GRPO, dosed** | from the better of A/B: ≥ 300–500 optimizer steps, 8 generations, prompts drawn preferentially from the probe's informative groups, lr 5e-6, Dr.GRPO β = 0 as now | capacity was never the limit (rank 1 suffices for policy gradient); dose and LR were |

Order: A first (closest to the pipeline, cheapest); B only if Step 3's supply
clears; C only if A's probe is informative (seed 1111's was: 21%). Read on
eval-2 at k = 16 under the amended macro rule, three seeds, `arm_check.py`
green before the second seed is billed.

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

## How a session follows this

1. `git pull` on `main`; read this file, then the report it cites.
2. Take the first `open` step. If its decision needs a number, get the number
   first — the reads in Step 0 are free.
3. Do the work in a PR that also edits this table's state and adds a ledger row
   to `ORCHESTRATION.md`. A step whose decision changed a later step edits
   that step here, in the same PR, with the reason.
4. Before any billed job: `round02-preflight` (all six checks) and the cost
   ceiling in `ROUND_READINESS.md`.
