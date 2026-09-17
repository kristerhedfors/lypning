# Codex review — round-02 and the S-ladder handoff

Date: 2026-09-17. Decision: **continue the $0 S0 reads; hold every paid or GPU
launch.** This is the independent Codex review owed by the decision ledger. It
reviews all four available Fable reports without changing them:

- `reports/2026-09-15-fable-round02-smoke.md`
- `reports/2026-09-16-fable-round02-pilot.md`
- `reports/2026-09-16-fable-round02-run.md`
- `reports/2026-09-16-fable-sladder-s0-device-audit.md`

The review used the committed reports, code and public metadata in this tree.
The private Hub dataset, job logs, weights and raw private rows were not
available on this device and were not reconstructed. Every conclusion below is
bounded by that evidence boundary.

## What the round established

The smoke established useful plumbing, not model quality: a tiny random model
completed the intended stage sequence, candidate execution crossed the pooled
sandbox boundary, and the image/engine/harness/interpreter identity checks were
exercised. Pooled sandboxes still share a kernel within one trust class and can
connect outbound. Those are accepted residual risks of the operator-selected
tier, not properties the smoke disproved. The later Space pause also showed the
image was operationally incomplete: it had no listening health process.

The first pilot established only two external blockers. The H200 job was
cancelled for billing and the provider returned 402 during the pilot draw. Its
15-case prefix was selected by interruption and is neither a completed arm nor
evidence that the remaining population would saturate. It must not be used for
promotion, power or recipe selection.

The ninth round-02 attempt is the only real 27B training evidence. It completed
SFT, the train-only probe and GRPO, but no eval-2 arm completed: the base arm
aborted after 384 of 1,200 k=4 draws, and the SFT and GRPO arms never ran.
Therefore round-02 has no paired eval-2 model result. Its seven-case dev and
test slices show no native-rate gain and intervals much wider than the target;
they are diagnostics only. SFT exposed just 6,251 supervised tokens over 20
steps and one seed, learned terseness quickly, and selected an unstable step on
tiny dev evidence. GRPO retained its SFT starting policy at step 0. The null is
a dosage/data finding, not evidence that verified adaptation cannot work.

Nine H200 attempts also demonstrate that the current workflow was too fragile
to repeat unchanged. The useful fixes from those attempts—transport retries,
stage checkpoints, exact bundle identity, paired batched generation and
per-job pool names—stand. The report's original “about 55 minutes per arm” was
a projection made before the arm failed, not an observation. A conforming
confirmatory arm is k=16, four times the attempted draws, so reducing scoring
workers on one host is not an acceptable capacity plan.

The S-ladder audit correctly reported a no-run. It had none of the private
rows, provider credentials, verifier Space access or GPU prerequisites needed
to execute S0–S4 and did not pretend otherwise. Its sandbox setup/exit-127 fix
is accepted. Because that fix and the worker in this review alter measured
harness identity, the next live verifier must be a new Space commit and every
bundle must be prepared again.

## Decisions on the open questions

1. **Native timeout after a correct oracle remains an abort.** Scoring it as
   `not-native` would make the primary endpoint depend on transient host load.
   The candidate program and successful siblings are now persisted, but an arm
   containing the block is still incomplete and cannot move a gate.
2. **Verifier capacity is four by four.** Sixteen score workers default to four
   sandboxes per `cpu-basic` host with a four-host ceiling. The launcher refuses
   a capacity product below its worker count and records both values.
3. **Held-out ranking is refused.** `nt levers --rank` on draw rows exits 2.
   Fable uses `--vector`, which reports reviewed buckets and family counts
   without a steering score or build order. Train-side capture ranking remains
   available.
4. **The 108 new lever declarations do not stand unchanged.** Thirteen
   deterministic families, covering 20 entries, move from legitimate fallback
   to engine-addressable: computed attribute/introspection protocols, seeded
   random operations, arbitrary-precision arithmetic, deterministic qualname
   attributes, fixed regex parser rejections, Unicode case-folding and the
   strict call shape. The remaining declarations are accepted as the current
   corpus policy under rule 2, not as a timeless statement about Python. The
   reviewed local vector is now 195 self-referential, 242 legitimate fallback,
   206 engine-addressable and 0 other over 643 refusals. S0b must still measure
   the private deployment population.
5. **The training floor becomes executable.** A real adapter stage needs at
   least 1,000 train cases and one of the registered seeds 1111/2222/3333. SFT
   must schedule at least one complete family cycle and at least 50,000 actual
   supervised-token exposures. Family-cyclic scheduling replaces independent
   with-replacement family draws, which could omit a family in a short run.
   All three seeds are required for the S4 aggregate; a single process can
   validate its own seed but cannot certify that the other jobs exist.
6. **The Space gets a content-free health endpoint.** `/` and `/healthz`
   return `ok`; no identity, verifier request or credential is exposed over
   HTTP. Sandbox execution continues to invoke the one-shot stdin protocol
   directly.

## Next bounded action

Fable owns one $0 session on the private-artifact device: run S0a and S0b with
the exact commands in `START_NEXT_ROUND.md`, read S0c from the immutable probe
and base-pilot rows, write a sanitized report with hashes and unmatched IDs,
then stop. No provider call, Space rebuild, dataset mutation or GPU job is
authorized by this review.

Codex then reviews that report. Paid S1 remains held until the three S0 reads
are complete and interpretable. S4 remains blocked on positive S1–S3 evidence,
a reviewed ≥1,000-case train bank, three-seed launch approval, a rebuilt healthy
verifier, new bundles and an explicit cost ceiling. A repeated native timeout,
identity drift, MISMATCH, incomplete arm, missing private artifact or leak-gate
failure stops the relevant rung; none is converted into a model score.
