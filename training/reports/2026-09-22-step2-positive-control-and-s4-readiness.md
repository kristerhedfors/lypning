# Step 2 positive control and S4 readiness — 2026-09-22

## Verdict

**Prepare S4 rejection-sampling SFT for independent review; do not submit a GPU
job yet.** The first bounded Cerebras control measured a clear native-policy
effect, but the conditioned arm also lost correctness. The raw conditioned arm
therefore failed the preregistered distillation rule. Execution-verified
rejection sampling removes that correctness failure from the training set: only
correct-native coverage programs and correct non-native retention programs are
admitted, then trained behind the bare prompt.

No GPU job was submitted in this work.

## Immutable evidence

The first generation run was GitHub Actions
[`35751938025`](https://github.com/kristerhedfors/lypning/actions/runs/35751938025),
source `57f63b7e68c4e341fe90de1aa6f7f76a2241e072`, private run
`smoke-57f63b7e68c4e341fe90de1aa6f7f76a2241e072-35751938025`.
It admitted 64 train cases over 31 families, checked all references in the
pinned runtime, completed 512 of 512 Cerebras requests with zero retries, and
charged or reserved **$1.20715678** against a $5 hard ceiling.

The first grading attempt failed before executing a candidate because it used
the GitHub controller's CPython build string when checking the rebuilt pinned
container. Engine bytes matched. PR
[#108](https://github.com/kristerhedfors/lypning/pull/108) now reuses the exact
container oracle recorded by paid-run admission while independently rehashing
the engine. The successful no-provider, no-GPU grade is Actions
[`35759939928`](https://github.com/kristerhedfors/lypning/actions/runs/35759939928).
It scored all 512 completions in isolated networkless containers and stored
private rows and targets under the same private run.

## Measured signal

| metric | bare | conditioned | paired delta, conditioned − bare |
|---|---:|---:|---:|
| correct-native | 54.03% | 68.82% | **+14.78pp**, 95% cluster CI **+3.89 to +26.81pp** |
| correct | 85.08% | 76.08% | **−9.01pp**, 95% cluster CI **−17.34 to −2.02pp** |
| mean completion tokens | 162.38 | 129.42 | −32.96 |
| truncation | 0% | 0% | 0pp |

The native effect is measurable and its clustered interval excludes zero. The
correctness loss is also measurable and violates the registered `>= -2pp`
tolerance, so both `distillation_route` and `confirmatory_signal` are false.
This is evidence for a usable rejection oracle, not evidence that the subset
spec should become the serving prompt.

## Private SFT target gate

The smoke grade admitted 157 deduplicated examples across 54 cases and 29
families: 149 correct-native coverage programs and eight correct-control
retention programs. Training messages contain the bare task prompt; the subset
spec and private expected outputs are absent. Target selection is capped at
four programs per coverage case and one per retained control case.

PR [#109](https://github.com/kristerhedfors/lypning/pull/109) added a standalone
free workflow with no GPU submission path. Actions
[`35762924601`](https://github.com/kristerhedfors/lypning/actions/runs/35762924601)
resolved Qwen to immutable tokenizer revision
`1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`, validated target digest
`788b98d40cbd7d7f2bed13ec8f14aa3b08e37436265709882da7fe42c9adf636`,
found no over-length row, and counted **156,691 supervised tokens** in the exact
300-step, effective-batch-four seed-1111 schedule against the 50,000 minimum.

## Remaining gates before GPU submission

1. Finish and grade the bounded case-expansion run, then rerun the exact target
   token/shape gate against its private lineage.
2. Have the stronger independent reviewer assess this report, the aggregate
   grade, target policy and launch manifest.
3. Run the explicitly pending 256-sequence hardware smoke only after that
   review authorizes GPU use. This report does not authorize the smoke or the
   training job.
4. Obtain a concrete GPU ceiling for the reviewed seed-1111 SFT job. Seeds 2222
   and 3333 remain separate decisions; one seed is not a complete S4 result.

The prepared first training recipe is Qwen3.8-27B, LoRA rank 16 / alpha 32,
SFT LR `1e-4`, 300 fixed steps, effective batch four, evaluations every 50
steps plus final, no early stop, and post-hoc selection under the amended
family-macro rule. The GPU launcher requires the private target run, exact
target digest and admitted engine lineage in its manifest.
