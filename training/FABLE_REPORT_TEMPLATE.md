# Fable round report — fill actual identities, not placeholders

## Outcome and decision requested

Date, round ID, status (not-started / blocked / failed / completed), concise
outcome, and what Codex should decide. State explicitly whether a GPU run occurred.

## Reproduction and authority

Repository commit; report version; approved device/time/cost/storage ceiling;
exact model revision; tokenizer/template and decoding config; Python build;
engine hash; candidate-image ID; verifier/harness identity; package lock;
bundle/review/probe/adapter digests; selected checkpoint (including step zero).
List exact commands, seeds, private artifact locations/access, exclusions and
missing artifacts. Do not put secrets or private raw data in the writeup.

## Data and hypothesis

Question to answer and predicted outcome; task/population/capability mixture;
independent family/source component counts; oracle/rights review; teacher and
repair conditions; split assignment; ordinary versus conditioned prompts;
first drafts versus repaired answers; rejected/quarantined counts with reasons.
Confirm that no held-out task influenced repairs or iterative recipe choice.

## Measurements

Base and candidate results on identical frozen conditions: first-draft task
correctness, correct-native rate, native rate conditional on correctness,
already-native retention, fallback controls and capability/family breakdowns.
Use group-level paired uncertainty; report sample sizes and denominators.
Keep pass@k and feedback-assisted results separate from first-draft results.
Include truncations, requests/retries/failures, input/output/reasoning tokens,
supervised tokens, optimizer updates, GPU time, wall time and observed costs.
Aggregate interpreter timing is separate; no per-script speed admission gate.

## Failures, reflections and next experiment

Native mismatches, unstable oracles, infrastructure/serving drift, rejected
checkpoints, incomplete artifacts and resource stops. Preserve failed attempts.
What worked? What did not? Which claim is directly supported versus inferred?
What alternative explanations remain? Propose one bounded discriminating next
experiment, required inputs, owner, expected benefit, cost and stop rule.

## Codex review handoff

Link the exact artifacts Codex can inspect. List unresolved questions and
decisions needed. Do not fill in Codex's independent assessment on its behalf.
