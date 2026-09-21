# Codex assessment of the seed-1111 read and Step 0

**Ruling: revise the factual read; continue to PLAN.md Step 1 only.**
Reviewed the Fable report
[`../reports/2026-09-21-fable-round02-seed1111-read.md`](../reports/2026-09-21-fable-round02-seed1111-read.md)
against the saved evidence through the aggregate-only read of **2026-09-21**,
[Actions run 35575454075](https://github.com/kristerhedfors/lypning/actions/runs/35575454075).
Numbers below come from that run; input revision, hashes, tables and limitations
live in [`../reports/2026-09-21-codex-step0-read.md`](../reports/2026-09-21-codex-step0-read.md).
The earlier report is preserved as authored.

## What stands

- The selector has a structural sensitivity problem: correctness floors on
  each slice and correctness-first ranking can reject useful native gains.
  Its synthetic admission test exercises the real class. The simulated
  probabilities use capability proxies and do not constitute measured
  checkpoint effects; fixing that instrument remains required.
- Selected step 0 alone cannot establish absence of learning. The saved
  per-step evaluations now answer the narrower question the plan actually
  asks: none qualifies for its +2pp native / −2pp correctness rescue.
- The dev coverage macro contains a one-case slice. Reporting the all-family
  macro separately from population-slice macros is essential.
- More old-configuration seeds would preserve the defective instrument;
  remaining seeds stay held. Loss reduction alone does not establish transfer.

## What changes with evidence

- SFT's later native gains are only +0.2976pp at best, with correctness losses
  of 3.2738–4.7421pp across its saved nonzero checkpoints. Fallback-control
  correctness falls sharply. Rejecting these checkpoints is warranted by the
  plan's tolerance even though the existing selector is defective.
- GRPO's largest observed native gain is +0.1190pp at step 10. The saved
  adapter inventory and evaluations end at step 15, not 20. The assertion of
  four informative optimizer steps was an expectation from a probe fraction
  and planned dose, not an observed count; the present read does not measure
  optimizer-step informativeness.
- Train probe negatives are 635 draws, not roughly 150. Dev is not a reliable
  estimator of this train population: there are zero shared cases and very
  different family compositions. This weakens the plan's small-supply premise
  without yet establishing enough same-prompt pairs for preference training.
- The actual saved eval-2 bundle has 803 cases in 19 families, all at least
  16 cases. A five-case primary-macro floor changes nothing here. Fragility
  remains in population slices: two eval-2 control slices have one and two
  cases. Step 1 must amend the intended estimand explicitly rather than apply
  a size threshold whose scope is unspecified.

## Scope of confidence

This is a k=4 dev read with multiple checkpoints inspected, no confidence
interval claim and no completed k=16 benchmark arm. It supports neither a
deployment gain nor a universal null for SFT/GRPO. Native status on correct
controls can differ from the `correct-native` status label; the read uses the
native boolean for the metric. The train/dev comparison is descriptive only.

All six private inputs came from one Hub revision; cases and draws were
checked against the prepared pilot bundle, and stage baselines against base-dev.
The by-kind vector uses recorded refusals, with no new engine replay. The read
does not independently recertify model/tokenizer/kernel identities, the test-arm
byte identity, all optimizer behavior, or hardware isolation claimed in the
Fable report. Those claims retain their original evidence and limits.

## Bounded next action

**Owner:** the next session following the plan. **Inputs:** PLAN.md Step 1,
this assessment and its aggregate read, existing selector/stopping tests and
the saved checkpoint/bundle identities. **Action:** instrument fixes and their
tests, keeping step 0 selectable and correctness retention explicit. Clarify
the population scope of the macro rule before any k=16 comparison.

**Stop criteria:** no paid or GPU step, no new provider sampling, no changed
frozen artifacts, no early advance to Step 2. The Step 0 condition did not earn
offline re-selection plus eval-2 rescue. Step 3 later counts paired supply
from the observed train population, rather than extrapolating it from dev.
