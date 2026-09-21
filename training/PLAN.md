# The plan — after seed 1111 on bank v3

**This is the live plan.** A session told to "follow the plan" starts here,
takes the first step whose state is `open`, does only that step, updates its
state below, and records the outcome in `ORCHESTRATION.md`'s ledger. It does
not skip ahead: each step's *decision* chooses the shape of the next one.
`STATUS.md` §10 still owns whether a round runs at all; this owns what the
next round is and in what order it is earned.

The evidence this plan rests on is
[`reports/2026-09-21-fable-round02-seed1111-read.md`](reports/2026-09-21-fable-round02-seed1111-read.md).
Read it before changing a step; every number below is quoted from it.

## What seed 1111 established (2026-09-20/21, job `6ab01cbb51992417dfccd64c`)

1. **The checkpoint selector cannot see the effect being trained for.**
   `CheckpointGate.observe` demands no correctness regression on nine
   ~180-draw sub-metrics, then ranks on correctness before nativeness. Simulated
   on the round's own dev baseline: a checkpoint with **+10pp correct-and-native
   and correctness unchanged is admitted 1.7% of the time — pure noise is
   admitted 2.1%**. "Selected step 0", twice, is therefore not evidence about
   the training. `tests/test_gate_admission.py` pins this.
2. **The SFT objective carried almost no signal and was under-powered for it.**
   Loss 0.1436 → 0.0426 in 75 steps: the base already emits the reference
   programs at ≈1.15 perplexity, and rows are `prompt + reference`. LR 2e-5 is a
   full-fine-tuning number; the LoRA optimum is ~10× that (~15× under 100 steps).
3. **GRPO took about four informative steps.** 20 optimizer steps, one prompt
   each, 21% of train groups informative (286 of 1,355), lr 1e-6.
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
| 0 | Read what was paid for | $0 | did dev native move inside the selector's blind spot? | open |
| 1 | Fix the instrument | $0 | nothing else is readable until it is | open |
| 2 | Positive control (S1 / stage 0b) | ~$5 | distillation route or contrastive route | open |
| 3 | Build contrastive targets | tokens | is there enough pair supply for a preference arm? | open |
| 4 | S4, re-specified, three seeds | ~$60–90 | the first result the instrument can read | open |

### Step 0 — Read what was paid for ($0, CI reads, aggregates only)

The adapters at SFT steps 25/50/75 and GRPO 5/10/15/20 are on the Hub under
`round-02/6ab01cbb51992417dfccd64c/`, and `sft/evaluations.jsonl` holds the
per-draw dev records at every eval step. Three summaries, each printing only
statistics over draws — never a case, program or expected stdout, because the
follower streams into a public log (`s0_inventory.py`'s rule):

- **Dev metric per step** from `sft/evaluations.jsonl` and `grpo/evaluations.jsonl`:
  correct and correct-native macro at steps 0/25/50/75 (and 0/5/10/15/20).
  This is the stage-1a proxy `ASSESSMENT.md` §3.1 asked for (S0c).
- **Probe rollouts by native status** (`probe/rollouts.jsonl`, 5,420 draws on
  train prompts) against the base dev draw: the other half of S0c.
- **Eval-2 bundle family sizes** (`eval2/bundle.json`, counts only): how many
  families under five cases enter the primary macro on the real benchmark.

**Decision.** If dev correct-native at any saved step is ≥ +2pp over step 0 with
correctness within −2pp: the treatment worked and the selector is the bug →
after Step 1, re-select offline and run **one** eval-2 pass of that adapter
against base (~$25; `base-eval2` never completed). No training needed.
Otherwise proceed to Step 1 regardless — it is required either way.

### Step 1 — Fix the instrument ($0, code; each item is a PR with a test)

1. **Selector.** Select on the primary metric (correct-and-native family macro)
   with correctness as a *tolerance* gate — the preregistered gate A, −2pp on
   the macro — not nine hard floors and a lexicographic key. The pinning test
   flips: null admitted ≤ 10%, +10pp native admitted ≥ 80%. Step 0 stays
   selectable.
2. **Stopping is not selection.** A registered dose trains to completion;
   every checkpoint is saved (already true); selection is post hoc. No
   patience-based stop inside an S4 stage.
3. **Macro fragility.** Preregister a minimum family size to enter the macro
   (≥ 5 cases, i.e. ≥ 80 draws at k = 16) or case-weight within family with
   clustering kept for the bootstrap. Simulate on the eval-2 bundle first; this
   is an `EVAL2.md` amendment and precedes any k = 16 read.
4. **Learning rates for LoRA.** SFT 1e-4 (2e-4 under 100 steps); GRPO 5e-6;
   small effective batch. `--eval-every 50`.
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
draws (`ASSESSMENT.md` §6). Count before building — dev suggests 2–3% of bare
draws are correct-but-refused, so ~150 natural pairs from the probe alone.

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
low on this population: the correct-but-refused mass on bank v3's dev is 2.4%
of draws, and the engine lever — which takes those kinds directly — has the
better record per dollar. Run S0b (the by-kind refusal vector) with Step 0 so
that call is made on numbers.

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
