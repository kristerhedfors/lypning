# Runtime and training priorities for the next L experiment

Assessment, 2026-09-15. These are engineering priorities to validate, **not
measured Qwen coverage gains**. Historical refusal counts come from different
policies, engines and prompts. The recorded mismatch witnesses were inspected
as text; this refactor did not execute or resolve them. Do not rank new work
using those counts as if they described today's model and L binary.

## Build the verification boundary first

The largest missing capability for trustworthy training is not another Python
module: it is candidate execution that cannot read or modify the trainer,
test registry, evaluator or credentials. The current local subprocess runner
is not a security boundary. Implement an isolated execution backend with
immutable per-request inputs, filesystem/egress restrictions, bounded raw-byte
outputs and explicit infrastructure-failure responses. Prove those restrictions
with adversarial fixtures before admitting generated code.

Then extend the verifier's observable contract to cover bounded output-file
contents, creations/deletions and raw stdout/stderr bytes. File effects must be
captured independently for each oracle/native run; include staged-write rollback
on refusal. A stdout-only reward cannot safely certify repository-editing,
binary-serialization or file-transformation tasks. Do not silently replace
byte comparison with lossy UTF-8 decoding.

## L capability candidates, in proposed order

| Candidate | Why it may help | Required evidence before training |
| --- | --- | --- |
| Resolve relevant semantic mismatches | Wrong native output is a runtime bug, never an idiom the model should learn to avoid. | Minimized, safely replayed witnesses; differential fixes; zero mismatches on the admitted surface. |
| Iterator composability around CSV | L already reads CSV, but direct sequence support is not arbitrary lazy/shared iterator support. Common pipelines compose readers and transforms. | Shared cursor, mutation, exhaustion, exception timing and refusal rollback tests. |
| CSV writers plus in-memory text streams | Round-trip table transformations should not need hand-written quoting to stay native. | Quoting/newline/dialect edge cases, exact writes, aliases and context-manager behavior; verify file effects before admitting disk-writing tasks. |
| Complete useful named-regex operations | Recent named captures enable extraction; metadata and replacement/backreference support can make that surface more coherent. | CPython differential tests for group metadata, unmatched groups, replacement escaping and adversarial patterns; retain safe refusal where incomplete. |
| Broader iterator/generator and simple record abstractions | Can reduce pressure to rewrite idiomatic Python into ad hoc lists/dictionaries. | First establish prevalence among correct fresh train draws; scope generator suspension/exception/close semantics or class identity/method dispatch separately. |
| Remaining numeric bridges | Exact conversions are useful, but arbitrary mixed wide-integer/float arithmetic remains a separate semantic project. | Rounding, overflow, signed-zero, NaN and large-integer differential grids; measure native gain and binary cost. |

Current boundaries are grounded in [L-COVERAGE](../docs/L-COVERAGE.md) and
the checked-in implementation: CSV writers and general iterator sources remain
outside the added CSV slice; the regex engine refuses named backreferences and
some metadata; the parser refuses class definitions and function-body yield;
the module table exposes `io.open`, not a general `io.StringIO` implementation.
These are scoped hypotheses, not an instruction to implement an entire stdlib.

Keep all additions in L. Do not widen the frozen core, target block budgets or
capability declarations to make a training curve look better. Every capability
needs positive native-answer tests, refusal/rollback tests, cross-feature tests,
both-dispatcher agreement and the existing platform size gates.

## Measure priority from correct programs

After the execution boundary exists, sample the pinned base and selected SFT
adapter on TRAIN families. Gate each complete program on all CPython tests
before using its native refusal as coverage evidence. The new rollout logs
include per-input refusal diagnostics, task capabilities, completion tokens
and truncation; none of this becomes prompt feedback.

Rank a proposed feature by affected independent families, correctness-gated
native gain, deployment frequency, semantic risk and incremental binary blocks.
Do not count repeated draws or cloned templates as independent demand.
Keep already-native retention tasks and genuinely unsupported fallback controls.
After each runtime change, rebuild, regrade references and freeze a NEW bundle;
compare training recipes only within a fixed runtime identity.

## Training experiments after the first admitted pilot

1. Base versus verified SFT-only versus SFT→RL, with a separately budgeted
   RL-from-base control. Match data, decode budgets and total work; report actual
   supervised/generated tokens and GPU time, not just optimizer steps.
2. Iterative rejection-sampled SFT: add diverse verified successes from train
   groups only. Keep a held-out provenance review and avoid overrepresenting
   easy tasks or one teacher's style. This can be preferable to RL when reward
   variance is poor; a completed RL run is not a success criterion.
3. Non-thinking versus thinking at matched deployment cost. The current runner
   intentionally supports code-only, non-thinking training. Before a thinking
   experiment, implement explicit trace/answer boundaries, verified trace-data
   handling and a separate frozen configuration; include reasoning tokens in
   the budget. Compare first-draft correctness/native gain and latency.
4. Rank/LR and objective ablations only after data quality is established.
   Test capacity or normalization hypotheses one at a time. A true SFT-anchored
   KL experiment requires an immutable SFT reference model/adapter, not merely
   disabling the trainable adapter.
5. Multi-turn repair only after deployment supplies feedback; evaluate success
   per fixed total attempts/tokens alongside, never in place of, first-draft
   scores. Test core compatibility later as its own dispatch/cost slice.

Release only when the locked test comparison meets preregistered correctness,
retention and native-gain criteria with uncertainty reported. Keep base or SFT
when additional training does not improve that trade-off.
