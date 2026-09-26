---
name: round02-plan
description: The live plan for the round-02 training programme — paused on 2026-09-26 while engine coverage continues in a parallel session, resuming on the next engine through `training/ENGINE_BUMP.md` and then the spend ramp in `training/RAMP.md`; the five steps of `training/PLAN.md` in order and how a session advances one. TRIGGER on "follow the plan", "continue the plan", "what's next", "next step", "where were we", "continue toward training", "next round", "resume the training programme", "resume training on the new engine", or any request to act on seed 1111 or its finish. SKIP for reading raw artifacts (`round02-evidence`), dispatching a billed job (`round02-launch`), the free checks before a launch (`round02-preflight`), authoring cases or cutting a bank.
---

# Follow the plan

The plan is **`training/PLAN.md`**. It is the one live home for what comes
next; `STATUS.md` §10 still decides whether a round runs at all. The evidence
is `training/reports/2026-09-21-fable-round02-seed1111-read.md`. Read both
before acting; every number in the plan is quoted from the report.

## What's next, as of 2026-09-26

**Training is paused; resume on the next engine, never on this one.** The user
decided on 2026-09-26 that engine coverage continues first in a parallel
session, and that paid work afterwards climbs one rung at a time, because
almost every paid training and eval step so far has crashed. The resume order,
with the current state, is the top section of
`training/START_NEXT_ROUND.md`; read it first. In short:

1. The seed-1111 finish was dispatched 2026-09-26 (Actions 36239792036, HF job
   6ab7b0b76b030d633f693e48); read its reports first. A redispatch runs only
   while the verifier Space head is still arm A's revision — before any
   non-finish `round02.yml` dispatch or `round02/**` push rebuilds it — and
   only from a main whose `VERIFIER_MODULES` (`training/pipeline/training.py`),
   `QWEN_REV`, seed, draws and pool density still match the pilot's
   (`training/ENGINE_BUMP.md` §5). Land no edit to a verifier module until the
   finish has run or been abandoned. It climbs under `training/RAMP.md` §5's
   salvage conditions, not straight after the OOM.
2. `training/ENGINE_BUMP.md` — regenerate every engine-labelled training
   artifact; the new engine is a new arm.
3. `training/RAMP.md` — the billed rungs, each with its entry condition and
   ceiling, and the ledger of every billed attempt. One rung per explicit user
   go; a crash sends you back a rung until its failure has a free check.
4. `training/PLAN.md` Step 4 on the new engine.

Arm A seed 1111's last billed step, finish HF job `6ab6a20c6b030d633f691a95`
(Actions `36161157776`, 2026-09-25), completed both test arms and died of CUDA
OOM in base-eval2 prefill; no eval-2 arm exists on any seed. The sections
below record the state up to 2026-09-22 and the rules that still hold.

## The one thing to hold in mind

Seed 1111 selected step 0 for SFT and for GRPO, and **that is not a result
about the training**. The selector it ran under demanded no correctness
regression on nine ~180-draw sub-metrics and ranked correctness before
nativeness; simulated on the round's own baseline it admitted a +10pp
correct-and-native checkpoint 1.7% of the time and pure noise 2.1%. Nothing
measured through that selector is readable, and that includes every number
seed 1111 produced.

Step 1.1–1.2 replaced it on 2026-09-21 (PR #97): the same simulation in
`training/tests/test_gate_admission.py` now measures 84.85% and 9.17% against
the real class, and selection no longer stops training. **This does not make
seed 1111 readable** — its checkpoints were produced under the old rule and
its GRPO stopped at step 15 of a registered 20. It makes the *next* round
readable. Step 1.3–1.6 implementation followed on 2026-09-22 in PRs #98–#101:
macro scope, LoRA rates, evaluation reuse/deadline and sized stdin reads.
The stack needs review and a new pinned engine/image before paid work;
`training/reviews/2026-09-22-step1-validation.md` records the inherited host
conformance failures and the required candidate checks. Step 2 is next, subject
to those checks and the existing cost approval.

**State on 2026-09-22 (evening), superseded by the section above: Step 2 was in progress with paid rungs.** Codex
ran the Cerebras smoke (`35751938025`, 64 cases × 2 arms × k=4), graded it
pooled with controls (`35759939928`: native +14.78pp, correct −9.01pp;
neither route earned), and completed the 192-case target rung
(`35767396604`, 1,536/1,536, not yet graded); the approval is not recorded in
this tree. The S4 refactor that followed fixed the split seed at 1111, made
the Step 2 decision coverage-only, put a 1,000-distinct-case floor on the
target curriculum (no existing rung clears it), enforced the torch-reference
kernel and set arm C's GRPO recipe. The review
`training/reviews/2026-09-22-claude-step2-s4-review.md` says **revise
(prepare)**. Next free actions, in order: grade `35767396604` **once**
(coverage-only, `QWEN_REV` tokenizer, `target_arms` chosen first — a second
grade overwrites); `s4-target-preflight` on it (expected to refuse on the case
floor); measure step-0 vs step-N draw coupling in seed 1111's
`sft/evaluations.jsonl`; count byte-identical completions between the smoke
and the rung's first 64 cases (does the provider honour `seed`). Then the
operator decides a full-split target rung and the dev-selection draws
(`launch.py --dev-eval-draws` / `DEV_EVAL_DRAWS`, 4 or 16; default 4). No GPU job before that.

## The steps, and the rule for taking one

| # | step | cost | state lives in |
|---|---|---|---|
| 0 | Read what was paid for — per-step dev metrics, probe rollouts, eval-2 family sizes | $0 | `PLAN.md` table |
| 1 | Fix the instrument — selector, stopping, macro floor, LoRA learning rates, eval cost, engine fixes | $0 | |
| 2 | Positive control — stage 0b, bare vs `prompts/subset-spec.md`; only k = 16 decides | ~$126 at 512 output tokens; ~$225 at allowance (full); rungs so far in `PLAN.md` | |
| 3 | Build contrastive targets — correct-native vs correct-but-refused pairs per prompt | tokens | |
| 4 | S4 re-specified — RFT arm, preference arm if supply, dosed GRPO; three seeds | up to ~$180 (720m ≈ $60 per seed job) | |

**Continue the first `in progress` step; otherwise take the first `open` step.
Do only that step.** Each step's
decision rule chooses the shape of the next one, so skipping ahead builds the
wrong thing. Do the work in a PR that also edits the state cell in `PLAN.md`
and adds a ledger row to `training/ORCHESTRATION.md`. If a decision changes a
later step, edit that step in the same PR and say why.

## Before anything billed

`round02-preflight` — all six checks, including `arm_check.py`, which will
refuse a seed that does not join the completed ones. The rung you are on, its
entry condition and its dollar ceiling are in `training/RAMP.md` (ceilings are
the operator's, recorded in its §7 before dispatch); dispatch nothing billed
that the ramp has not reached.

## Do not

- Run seeds 2222/3333 of seed 1111's configuration — they would replicate a
  blind selector, and `arm_check.py` would let them.
- Push a `round02/**` branch, or dispatch any non-finish `round02.yml` stage,
  before the seed-1111 finish is run or abandoned: bootstrap rebuilds the
  verifier Space from that commit's engine, and a moved head makes the finish
  unfinishable.
- Train or evaluate a new-engine arm on anything arm A produced (targets,
  bundles, Space revision). `train_verified.py` refuses SFT targets graded by
  another engine; `training/ENGINE_BUMP.md` lists what must be regenerated.
- Switch the kernel: it is an arm identity (+1.57pp on identical weights).
- Grade a Step 2 run twice, or lower the curriculum case floor to fit a
  target set.
- Widen `s0_inventory.py`'s `SMALL` tuple to read Step 0. Write a summary that
  prints aggregates in the job, as `loss_summary.py` does — the follower
  streams into a **public** log and a bundle was printed into one on 2026-09-18.
- Read the step-0 selection, or the byte-identical test triplicate, as evidence
  that training does not work here. See the one thing above.
