---
name: round02-plan
description: The live plan after round-02 seed 1111 on bank v3 (2026-09-21) — what it established, the five steps in order with the decision each makes, and how a session advances one step. TRIGGER on "follow the plan", "continue the plan", "what's next", "next step", "where were we", "continue toward training", "next round", "resume the training programme", or any request to act on the seed-1111 result. SKIP for reading raw artifacts (`round02-evidence`), dispatching a billed job (`round02-launch`), the free checks before a launch (`round02-preflight`), authoring cases or cutting a bank.
---

# Follow the plan

The plan is **`training/PLAN.md`**. It is the one live home for what comes
next; `STATUS.md` §10 still decides whether a round runs at all. The evidence
is `training/reports/2026-09-21-fable-round02-seed1111-read.md`. Read both
before acting; every number in the plan is quoted from the report.

## The one thing to hold in mind

Seed 1111 selected step 0 for SFT and for GRPO, and **that is not a result
about the training**. The selector demands no correctness regression on nine
~180-draw sub-metrics and ranks correctness before nativeness; simulated on the
round's own baseline it admits a +10pp correct-and-native checkpoint 1.7% of
the time and pure noise 2.1%. `training/tests/test_gate_admission.py` pins
this against the real class. Nothing measured through that selector is
readable until Step 1 fixes it.

## The steps, and the rule for taking one

| # | step | cost | state lives in |
|---|---|---|---|
| 0 | Read what was paid for — per-step dev metrics, probe rollouts, eval-2 family sizes | $0 | `PLAN.md` table |
| 1 | Fix the instrument — selector, stopping, macro floor, LoRA learning rates, eval cost, engine fixes | $0 | |
| 2 | Positive control — stage 0b, bare vs `prompts/subset-spec.md`, k = 16 | ~$5 | |
| 3 | Build contrastive targets — correct-native vs correct-but-refused pairs per prompt | tokens | |
| 4 | S4 re-specified — RFT arm, preference arm if supply, dosed GRPO; three seeds | ~$60–90 | |

**Take the first step whose state is `open`. Do only that step.** Each step's
decision rule chooses the shape of the next one, so skipping ahead builds the
wrong thing. Do the work in a PR that also edits the state cell in `PLAN.md`
and adds a ledger row to `training/ORCHESTRATION.md`. If a decision changes a
later step, edit that step in the same PR and say why.

## Before anything billed

`round02-preflight` — all six checks, including `arm_check.py`, which will
refuse a seed that does not join the completed ones. The cost ceiling is the
operator's and lives in `ROUND_READINESS.md`.

## Do not

- Run seeds 2222/3333 of seed 1111's configuration — they would replicate a
  blind selector, and `arm_check.py` would let them.
- Switch the kernel: it is an arm identity (+1.57pp on identical weights).
- Widen `s0_inventory.py`'s `SMALL` tuple to read Step 0. Write a summary that
  prints aggregates in the job, as `loss_summary.py` does — the follower
  streams into a **public** log and a bundle was printed into one on 2026-09-18.
- Read the step-0 selection, or the byte-identical test triplicate, as evidence
  that training does not work here. See the one thing above.
