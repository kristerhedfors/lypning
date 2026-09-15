# Codex assessment: Fable's Stage 0a audit

Reviewed 2026-09-15 against merged main `4522c10d64495200a31ecf49e07e4c52ead0fa8c`.
Evidence: [Fable PR #73](https://github.com/kristerhedfors/lypning/pull/73), its
merged changes, and `LADDER.md`. This review did not replay captured code or
inspect the other device's private `work/round-02/`. PR-reported test results are
Fable's evidence, not newly reproduced results in this review.

## What the report supports

Fable reports concrete differential/refusal fixes and correctly states that no
GPU, image or training work occurred on that worker. Resolving native mismatches
before training is the right priority. Keeping safe refusals for incomplete
semantics is preferable to falsely claiming compatibility. The report also
distinguishes the remaining grid replay and launch prerequisites from completed
one-line witness work. This is runtime progress, not a demonstrated model gain.

## Additional assessment beyond Fable's reflections

1. **Do not inherit the historical reachability conclusion as RL admission.**
   `LADDER.md` measured old draws on an earlier engine and serving configuration.
   Legal reachability is not correctness-gated reward variation, and a nonzero
   historical pass@k does not validate the next SFT checkpoint's GRPO signal.
   Run the current exact-policy TRAIN probe after SFT and runtime regrading.
2. **Zero successes in a finite sample is not proof of impossibility.** The
   ladder's zero-draw feature strata prioritize teacher/engine investigation;
   they do not prove that no RL/generalization or different sampling budget
   could help. Conversely, native legality alone is not proof that RL will help.
3. **A flat prompting experiment does not rule out specification distillation.**
   The historical instruction to skip distillation entirely after flat prompt
   SLR is too categorical. Teacher quality, prompt design, verified repairs and
   data diversity are confounded. Test verified SFT on ordinary tasks; a single
   flat prompt arm is evidence about that arm, not all distillation recipes.
4. **Treat the reported corpus timeout and sandbox test failure as unresolved
   verification context.** Do not change thresholds or award native labels to
   make them disappear. Reproduce the relevant contract on the pinned Linux
   candidate boundary; preserve the original failure. Shared-runner timing is
   not a correctness oracle or a training reward.
5. **Close the gap between project captures and task correctness.** A whole
   OpenCode project cannot be certified from a passing self-test or from running
   one .py file. Review an independently testable task projection, preserving
   original project lineage, or extend the verifier's file/module contract.

## Decision: continue data preparation; hold any new training launch claim

Fable can continue its already authorized work. Before claiming a new pilot is
ready, provide the current operator status, remaining safe witness replay,
reviewed data and candidate-image identities, hardware approval/smoke and a
report following `FABLE_REPORT_TEMPLATE.md`. Codex's next work is question and
oracle review plus verified repair candidates in a separate bundle. After an
actual base/SFT report arrives, assess paired task correctness and correct-native
gain before proposing DPO/GRPO. Do not change Fable's frozen artifacts in place.
