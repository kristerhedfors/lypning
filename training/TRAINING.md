# Qwen3.8 training for lypning-l

Design decision, 2026-09-15: **verified, diverse SFT is the first candidate;
execution-verified on-policy RL is conditional on measured signal and benefit.**
Keep the untouched model and SFT-only controls. No algorithm has been shown to
be universally best for Qwen3.8 on this interpreter's first-draft workload.

[Next round](NEXT_ROUND.md) is the manual launch checklist.
[L capability priorities](L-TRAINING-ROADMAP.md) separates runtime investments
from training changes. This refactor launches no training and establishes no
model-quality improvement. Historical datasets and measurements remain intact.

## Start with the problem, not an RL algorithm

The target is an ordinary task's **first correct program that executes natively
on L**, while retaining correctness when CPython fallback is necessary. It is
not rewrite compliance, fewer imports, shortest source, or native execution of
an incorrect program. Core compatibility and feedback-assisted repair are
separate evaluation slices, not extra requirements on the first L experiment.

| Method | Role in this experiment |
| --- | --- |
| Continued pretraining | Low priority: this post-trained model already knows Python; raw documentation does not verify semantics. |
| Verified SFT / rejection-sampled fine-tuning | First intervention and standalone control. Teach diverse correct supported idioms, with retention and legitimate fallback examples. Regenerate successes only from train families. |
| Execution-reward RL | Candidate after a train-only probe finds useful within-prompt reward variation. It optimizes current-policy behavior, but can exploit weak tests or forget correctness. |
| Offline preferences (DPO/ORPO) | Optional matched ablation; not an obligatory stage. Pairs must isolate native correctness, not style or teacher preference. |
| Multi-turn repair | Only for a deployment that actually supplies runtime feedback. Do not present repair/pass@k gains as first-draft gains. |

[DeepSeek-R1](https://arxiv.org/abs/2501.12948) and
[DeepCoder](https://www.together.ai/blog/deepcoder) motivate verified RL in
reasoning/code, not a guaranteed win on this smaller task distribution.
[STaR](https://arxiv.org/abs/2203.14465) motivates iterative verified-success
training; [SCoRe](https://arxiv.org/abs/2409.12917) motivates treating
self-correction as its own training distribution.

LoRA is an adaptation parameterization, not an objective. Keep BF16 rank 16,
alpha 32 and dropout zero initially, then jointly ablate rank and learning rate
if underfitting survives data/objective improvements.
[LoRA Without Regret](https://thinkingmachines.ai/blog/lora/) supports trying
LoRA before full tuning and warns that capacity and optimization settings
matter. It does not establish rank 16 as optimal here. Quantization and full
fine-tuning remain separately budgeted experiments, not concurrent changes.

## Qwen-specific contract

The [official model card](https://huggingface.co/Qwen/Qwen3.8-27B) identifies
`Qwen/Qwen3.8-27B` as post-trained, with Qwen3.5 architecture and thinking
enabled by default. Use the exact `Qwen3_5ForConditionalGeneration` class,
not a guessed text-only architecture. Pin an immutable Hub commit; validate
checkpoint keys, tokenizer, template and every targeted text projection.
Freeze vision, embeddings and output head. Include both gated linear-attention
and full-attention projections plus the text MLPs; retain the gradient smoke.

This first experiment explicitly disables thinking everywhere. Its supervised
targets are code, not verified reasoning traces. A thinking-mode comparison
needs its own templates, parsed answer boundary, trace provenance and token
budget; simply toggling it in this SFT pipeline would change the objective.

Use sampled decoding with temperature 0.7, top-p 0.8, top-k 20, min-p 0,
repetition penalty 1, one beam and the tokenizer's assistant EOS. The official
non-thinking serving recipe also uses presence penalty 1.5; this Transformers
experiment explicitly uses **zero**, not an undocumented approximation.
Probe, RL and primary evaluation share this policy. Greedy is an eval-only
diagnostic. Completion length is part of the experiment, never silently changed.

## Audit: why the previous scaffold was insufficient

| Risk | Refactor |
| --- | --- |
| Family labels missed exact solution/source reuse | Join family, declared source group and normalized solution AST into indivisible split components. |
| Coverage SFT seeds could themselves fall back | Admit coverage references only when every input is native; controls must really refuse on every input. Revalidate registry on bundle load. |
| RL launched without learnable signal | Require a sealed train-only probe for the exact starting policy. Masked truncations cannot supply admission signal. Log the no-signal group fraction; since 2026-09-22 a streak no longer aborts a dose. |
| Greedy dev versus differently sampled RL | One explicit decoding contract; fixed per-case/draw seeds; preserve training RNG across evaluation. |
| Adapter could be replaced or have incompatible provenance | Seal weights, config and manifest; check base, split, mode, tokenizer and model configuration on reload. |
| Partial source hashes and ambiguous batching | Hash all pipeline/GPU sources; separate SFT batch size from RL group size; record completion/supervised token counts. |
| Aggregate gains hid regressions | Family-macro, population and capability metrics; correctness retention gates; paired source/family-component comparison. |
| Monolithic runner mixed scientific policy with optimization | Separate data, verification, contracts, evaluation, optimizer stages and reporting. GPU imports remain execution-only. |

These are reliability improvements, not evidence that the trained policy is
better. The exact pinned GPU stack still requires the manual smoke/reload gate.

## Data admission: schema 3

Every case has `case_id`, `family`, `task`, `reference`, `provenance`,
`population`, and `tests`. Pilot cases additionally require reviewed
`source_group` and nonempty `capabilities` labels. A source group represents
shared project/template/derivation, not a unique ID invented to pass splitting.
Join these groups before sampling or adding derived solutions. AST matching
detects exact structural reuse, **not semantic near-duplicates**; human review
and benchmark contamination review remain mandatory.

Split connected components deterministically within population strata. Pilot
admission needs at least 18 semantic families, two families and two independent
components of each population in each split. Linked families may reduce independence:
inspect connected components, not just the family count. This is a floor,
not enough statistical power by itself. Author substantially broader data.

Use deterministic UTF-8 stdout, empty stderr and exit zero; tests may vary
stdin, argv and UTF-8 input files. Require at least three distinct inputs and
two distinct outputs, including boundary/adversarial inputs. Expectations must
be independently checked; agreeing with one teacher is not an oracle.
Finite tests cannot prove semantic correctness. File effects, binary outputs
and arbitrary checkers are not yet certified by this contract.

References are run twice on CPython, then on the pinned compiled L binary.
Coverage seeds must be correct and fully native; control seeds must be correct
and cleanly refused on every input. A model may subsequently solve a control
natively without penalty. A coverage expansion requires a new bundle and a
fresh review of control labels.

The authored starter remains smoke-only, even with source/capability metadata.
No additional pilot corpus is fabricated by cloning it. Schema-2 bundles and
unsealed legacy adapters do not silently migrate into this experiment:
reprepare data; preserve historical runs; review any adapter migration explicitly.

## Verification and learning

| All-input observation | Coverage reward | Control reward |
| --- | ---: | ---: |
| Correct CPython and correct native on every input | 1 | 1 |
| Correct CPython and valid native refusal on one or more inputs | 0.25 | 1 |
| Incorrect, exception, timeout, no code, missing EOS | 0 | 0 |
| Unstable successful oracle, harness failure, malformed refusal, native mismatch | Abort | Abort |

No legality-only bonus, syntax credit, import penalty or reference-length reward.
Refusal is exit 90, one correctly prefixed stderr line and empty stdout.
Memory-limit failures are not successful execution or legitimate refusals.
Per-draw records include truncation, token count, failing input and refusal
diagnostics; these are for analysis, never prompt feedback.

SFT trains assistant completion tokens plus EOS, rejects silently dropped or
boundary-merged examples, samples family then case, and uses token-weighted
microbatch accumulation (effective batch 4). The preregistered Step 1.4 recipe
starts at peak LR 1e-4, or 2e-4 for fewer than 100 effective optimizer steps,
with linear warmup/decay,
gradient clipping and non-finite loss/gradient checks. Explicit `--lr` overrides
the recipe and is recorded in the effective schedule. Evaluation defaults to
every 50 updates in both stages, with the final step always evaluated; smoke
still evaluates both of its two steps. This cadence changes no registered dose.

RL starts at LR 5e-6, four generations per prompt and one optimizer update per
group, using Dr.GRPO with no reward-standard-deviation scaling and truncated
completions masked. `beta=0` is explicit: disabling the warm-start adapter
would anchor to the original base, not SFT. Do not call this an SFT KL anchor.
The [pinned TRL documentation](https://huggingface.co/docs/trl/v1.13.0/grpo_trainer)
defines these settings. The [Dr.GRPO analysis](https://arxiv.org/abs/2503.20783)
motivates avoiding length/difficulty normalization biases; it does not prove
these settings optimal. [GSPO](https://arxiv.org/abs/2507.18071) is a plausible
later ablation, not a reason to replace a verified baseline without evidence.

The probe covers every train case with the requested group size. At least two
groups need distinct rewards among **non-truncated** draws, and some correct
draws must exist. This deliberately modest gate is not a power calculation:
inspect variation by family and raise sample sizes before scaling.
Default live RL aborts after 20 consecutive uninformative groups. Smoke bypasses
these signal gates because a tiny random model is only a plumbing test.

GRPO uses a cyclic family-balanced dataset; within-family multiplicities can
differ when family sizes differ. Review this mixture and freeze it. Increasing
`--generations` also increases rollouts per update; equal steps are not equal
compute across configurations.

## Evaluation, checkpointing and reproducibility

Primary scores average independent first-draft draws, then cases within families,
then families. They estimate sampled pass@1, **not best-of-k**. Equal draw counts
and unique case/draw IDs are enforced. Dev checkpoint selection ranks the
correct-and-native family macro — the quantity being trained for — behind two
tolerances against that run's starting policy: gate A, −2pp on the all-family
correctness macro, and a per-population retention rule at three standard
errors. A candidate must also clear the starting policy by the macro's own
standard error, because the best of several noisy evaluations is biased
upward. A GRPO warm start's baseline is SFT; final release comparisons must
also include base.

**Selection is post hoc; it never stops training.** A registered dose runs to
completion, every checkpoint is saved and `best.json` records every
observation with the reason it was or was not selected, so the selection can
be re-made offline without re-running the stage. Before 2026-09-21 a patience
counter in the same object ended a stage after three unselected evaluations,
which cost seed 1111's GRPO five of its twenty registered steps and the
adapter that would have been saved at step 20.

Checkpoints retain separate directories, including step zero. `best.json`
selects without deleting evidence. Adapters are restart artifacts, **not exact
optimizer/RNG resumes**. Reload before accepting a checkpoint. Fixed seeds
reduce comparison noise; they do not guarantee bit identity across hardware
or different kernels. Do not mix versions or decode budgets.

After locking choices on dev, evaluate base, SFT-only and SFT→RL on the same
untouched test split. Add RL-from-base only within the approved matched budget.
Repeat at least two training seeds. `python -m pipeline.training_report BASE
CANDIDATE` checks standalone eval contracts and reports paired source/family-component
bootstrap intervals, resampling linked families together. With few independent
components, these intervals are exploratory.
The code prevents test cases from entering reward or checkpoint selection;
an operator must still prevent repeated test inspection and preregister release
margins, minimum native gain and acceptable retention regressions.

## Execution and cost boundary

Follow [NEXT_ROUND.md](NEXT_ROUND.md) for commands. `--plan` checks locally
without importing torch, downloading weights or executing generated programs.
One process/GPU only; real runs require an admitted pilot, Linux/CUDA and active
memory limits. Output directories must be new.

`--isolated-worker` is an attestation, **not a jail**. Pilot preparation now
requires a reviewed-data manifest and an immutable candidate-image ID. All
generated-code runs, including smoke, require the container execution contract
stored in their bundle. Candidates receive no host mounts/network, registry,
expected outputs, credentials or GPU. The full oracle/engine/harness identity is
checked before model loading. Docker still shares a kernel; use a dedicated
disposable worker and the boundary tests in
[START_NEXT_ROUND.md](START_NEXT_ROUND.md). The local subprocess harness remains
available only for reviewed CPU smoke fixtures, not arbitrary generated code.
Preload dependencies and weights after approval. Historical captured programs
are also untrusted; never replay them directly in the development checkout.

Step/completion caps bound work shape, not dollars or elapsed time. Use an
externally enforced scheduler budget, record actual GPU time/cost and token
totals, preserve blocked witnesses, and stop on verifier failures. CPU tests
cannot substitute for the exact Qwen/TRL gradient, generation and reload smoke.
