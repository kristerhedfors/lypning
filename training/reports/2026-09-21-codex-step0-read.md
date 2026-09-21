# Step 0: the saved checkpoints do not earn an eval-2 rescue

Read **2026-09-21**, HF job `6ab01cbb51992417dfccd64c`, GitHub Actions
[run 35575454075](https://github.com/kristerhedfors/lypning/actions/runs/35575454075),
reader commit `2127954`. Every measured number below comes from that run.
The complete public aggregates and six input SHA-256 digests are in
[`2026-09-21-codex-step0-aggregates.json`](2026-09-21-codex-step0-aggregates.json).
Private repository `headforce/lypning-round02-artifacts` was pinned to revision
`ca9d17fb4f04d2d2f2ba6005e2e213d1b81b7b29` for every download and the adapter
inventory. No weights were downloaded, programs executed, artifacts changed,
provider calls made, or GPU jobs launched.

**Decision: proceed to PLAN.md Step 1.** No saved checkpoint has a dev
correct-and-native family-macro gain of at least +2pp with correctness within
−2pp of step 0. The conditional offline re-selection and paid eval-2 rescue
are not earned. This is an exploratory k=4 dev read, not a confirmatory
eval-2 result or evidence that a properly dosed treatment cannot work.

## 1. Complete dev evaluations, by saved step

The reader uses `pipeline.training_metrics.summarize`, the production family
macro over **all** dev families, including fallback controls. Each checkpoint
has 1,224 draws: all 306 declared dev cases, four distinct draws apiece, seven
families. Case/draw keys, family, population, split group, capability labels and
decoding seeds match base-dev. Both stage-0 score/status/token records match
base-dev. Missing cases, duplicate draws, metadata drift or missing evaluations
for a saved adapter stop the read.

| stage | saved step | correct macro | correct-native macro | correct Δpp | native Δpp | Step 0 rule |
|---|---:|---:|---:|---:|---:|---|
| SFT | 0 | 91.8452% | 67.7183% | 0 | 0 | baseline |
| SFT | 25 | 87.1032% | 65.9127% | −4.7421 | −1.8056 | fails |
| SFT | 50 | 88.4127% | 68.0159% | −3.4325 | +0.2976 | fails |
| SFT | 75 | 88.5714% | 68.0159% | −3.2738 | +0.2976 | fails |
| GRPO | 0 | 91.8452% | 67.7183% | 0 | 0 | baseline |
| GRPO | 5 | 91.8452% | 67.7183% | 0 | 0 | fails |
| GRPO | 10 | 91.8056% | 67.8373% | −0.0397 | +0.1190 | fails |
| GRPO | 15 | 91.9444% | 67.8175% | +0.0992 | +0.0992 | fails |

**There is no saved GRPO step 20.** Both the Hub weight-file inventory and the
evaluation step set stop at 15. The earlier Fable read describes the planned
20 as completed and PLAN.md assumed its adapter existed. Neither assertion is
supported by the saved evidence. The current GRPO callback can stop at three
rejected checks, consistent with stopping at 15; this read does not inspect
optimizer logs or claim to establish an exact final optimizer count.

Population slices expose SFT's retention loss:

| stage / step | coverage correct | coverage native | control correct |
|---|---:|---:|---:|
| baseline | 92.1139% | 75.4533% | 91.5128% |
| SFT 25 | 88.6600% | 76.2790% | 81.2145% |
| SFT 50 | 92.3321% | 76.0256% | 75.5682% |
| SFT 75 | 88.0525% | 75.8303% | 77.4503% |

Even coverage-only native improves by at most +0.8257pp. Thus the outcome of
the Step 0 decision does not hinge on choosing the all-family rather than
coverage-only macro. The selector's known sensitivity defect remains, but it
did not conceal a checkpoint meeting this plan's rescue threshold.

## 2. Train probe versus base dev: two populations, not paired arms

The real probe path is `probe/probe-rollouts.jsonl`, not `probe/rollouts.jsonl`.
It contains 5,420 draws over all 1,355 declared train cases, four per case.
There are **zero shared case IDs** with the 306 dev cases. Aggregate differences
therefore describe population composition, not a treatment effect.

| status | train probe draws | base-dev draws |
|---|---:|---:|
| correct-native | 3,403 | 818 |
| correct-fallback | 635 | 29 |
| correct-control | 775 | 276 |
| incorrect | 596 | 99 |
| no-code | 11 | 2 |
| total | 5,420 | 1,224 |

There are 4,813 correct train draws (88.8007%) and three truncated train draws.
The native **boolean** includes correct-control draws that happen to run
natively: 3,459 train draws and 819 dev draws. It must not be confused with the
`correct-native` **status** count. Draw-weighted native is 63.8192% on train and
66.9118% on dev; family-macro native is 64.2750% and 67.7183%, respectively.

Per-train-case native draw counts, with no case IDs published:

| native draws out of four | train cases |
|---:|---:|
| 0 | 376 |
| 1 | 71 |
| 2 | 74 |
| 3 | 96 |
| 4 | 738 |

The train correct-fallback mass is **635 / 5,420 = 11.7159%**, versus
**29 / 1,224 = 2.3693%** on dev. PLAN.md's extrapolation of roughly 150
negatives from dev was too low. These are negative **draws**, not usable
same-prompt pairs or independent tasks. Step 3 still must count train prompts
that also have a correct-native positive before deciding its preference arm.

## 3. S0b-style by-kind vector, from recorded refusals

This is a descriptive census of this job's correct-fallback draws. It neither
replays a changed engine nor replaces the separately frozen 171-draw historical
S0b assignment. Each kind counts at most once per draw, regardless of how many
test inputs refused; a draw may contribute to multiple kinds. No refusal
details or source programs are printed. Every fallback draw has a recorded
refusal; there are no unknown-kind buckets in this read.

| kind | train probe | base dev |
|---|---:|---:|
| builtin | 4 | 0 |
| bytes-method | 3 | 0 |
| class-subscript | 1 | 0 |
| class-union | 6 | 0 |
| decorator | 1 | 0 |
| int-method | 13 | 4 |
| module | 575 | 15 |
| module-attr | 12 | 8 |
| set-order | 14 | 0 |
| str-method | 7 | 2 |

The module kind dominates the train negatives. This does not say which module
to implement or how much mass is repairable; no held-out-driven build order or
engine/model addressability claim is made from the vector.

## 4. Eval-2 family sizes: the primary macro has no tiny families

The saved benchmark contains **803 cases in 19 families**, not the 893 cases
and 21 families quoted in the Fable read. Its all-population family-size
histogram is 17 × 45, 1 × 22, 1 × 16. **Zero families below five cases enter
the primary macro.** A five-case floor on that macro would exclude nothing.

Population slices differ:

| slice | cases | families | family-size histogram | families under five |
|---|---:|---:|---|---:|
| eval-2 coverage | 690 | 19 | 6×2, 13×1, 16×1, 22×1, 43×1, 44×1, 45×12 | 0 |
| eval-2 control | 113 | 5 | 1×1, 2×1, 32×1, 39×2 | 2 |
| dev, all | 306 | 7 | 36×1, 45×6 | 0 |
| dev coverage | 230 | 7 | 1×1, 13×1, 36×1, 45×4 | 1 |
| dev control | 76 | 2 | 32×1, 44×1 | 0 |

Histogram notation is cases-per-family × number-of-families. The dev coverage
singleton is real, but it is a population slice of a larger family, not a
singleton in the all-population primary macro. Step 1.3 must specify which
aggregation its size rule applies to and handle the two tiny eval-2 control
slices explicitly. This read does not amend EVAL2.md or filter any metric.

## 5. Next action and verification

Step 0 is complete; Step 1 stays open. Fix the selector, stopping and other
instrument items with their tests. Do not launch the conditional rescue,
remaining old-configuration seeds, positive control or preference preparation
as part of this step. The independent Codex ruling is
[`../reviews/2026-09-21-codex-seed1111-step0-assessment.md`](../reviews/2026-09-21-codex-seed1111-step0-assessment.md).

The successful Actions read ran all 17 reader tests first. They cover family
weighting, the decision rule, incomplete/duplicate draws, baseline and metadata
drift, the saved-versus-planned distinction, unpaired probe counts, refusal
deduplication, and suppression of private labels and parse-error payloads.
The two earlier read attempts failed closed before publishing any row; their
validation discrepancy led to the inventory check and the step-20 correction.

Local verification on 2026-09-21: the full training suite passed **1,266
passed, 11 skipped** outside the macOS sandbox, after its process-monitor and
local-server restrictions caused the sandboxed run to fail. Reader plus
documentation checks passed **144 passed, 3 skipped, 20 xpassed**; the xpasses
are existing non-strict documentation expectations. No engine code changed.
