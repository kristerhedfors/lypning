# Qwen3.8 training for lypning-l

Use [NEXT_ROUND.md](NEXT_ROUND.md) for the manually started next round: current
admission gates, commands, stop conditions and handback format. No training is
started by this repository change.

Decision, 2026-09-14: **verified supervised warm start, then execution-verified
on-policy RL; keep SFT-only and base-model controls.** Target the real
`Qwen/Qwen3.8-27B` checkpoint, with the `lypning-l` execution surface first.
LoRA is the parameterisation, not the learning objective: both stages use it.
No particular algorithm is established as best for this interpreter domain.
This document supersedes the proposed reward in `LADDER.md`, not its recorded
measurements or the existing frozen rewrite benchmark.

## Why this recipe

| Approach | Fit to this problem | Decision |
|---|---|---|
| Continued pretraining on source/documentation | Teaches vocabulary and patterns, but has no direct correctness signal; the model already knows Python | Not the first intervention |
| Supervised fine-tuning on verified solutions | Efficient way to demonstrate supported idioms; risks memorising answers and inheriting a teacher's style | Small, diverse warm start and standalone baseline |
| DPO/ORPO on offline pairs | Cheap comparative learning, but stale pairs and correctness/style confounds are serious here | Optional ablation, not a required middle stage |
| On-policy RL with executable rewards | Directly measures the objective and trains on the current model's mistakes | Main candidate after the verifier and rollout signal pass their gates |
| Multi-turn repair training | Appropriate for a deployment that actually returns interpreter feedback | Separate experiment; do not mistake repair gains for first-draft gains |

[DeepSeek-R1](https://arxiv.org/abs/2501.12948) supports verifiable RL and a
cold-start supervised stage for reasoning/code. [DeepCoder's official
report](https://www.together.ai/blog/deepcoder) demonstrates coding gains from
execution-RL at substantially larger data/compute scale than this project.
Neither establishes a winner for Qwen3.8 + lypning-l. [STaR](https://arxiv.org/abs/2203.14465)
supports iterating over verified successes; [SCoRe](https://arxiv.org/abs/2409.12917)
is evidence that self-correction requires attention to the on-policy,
multi-turn distribution, not just an offline collection of corrections.

Full-parameter tuning is not intrinsically necessary for the first experiment.
Keep BF16 rank-16 LoRA, frozen vision weights, and the existing exact
`Qwen3_5ForConditionalGeneration` class and projection-gradient checks. Change
rank or try full tuning only if the data/objective ablations demonstrate an
adaptation-capacity bottleneck. QLoRA is a memory trade-off, not a claim of better
quality; do not change quantisation, objective and dataset simultaneously.
The checkpoint's architecture is verified by its [official config](https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/config.json).

## What was wrong with the earlier plan

1. Its proposed reward paid `+0.5` for legal-but-wrong code and `+0.1` for a
   correct fallback. This actively favours losing correctness. Import-count and
   reference-length penalties add style proxies that can also punish valid code.
2. Refusal-derived rewrites are not the distribution of ordinary first-draft
   tasks. Excluding already-supported examples also excludes retention training
   for capabilities the large engine already has.
3. One expected stdout is easy to memorise. A single successful perturbation is
   better, but remains weak evidence of generalisation. Split semantic families
   before generating variants or sampling solutions.
4. Runtime mismatches, flaky oracles and harness failures cannot be learning
   targets. Fix or quarantine them with a separately reported denominator;
   never reward the model for avoiding an engine bug. The new path aborts.
5. Zero successes in a finite pass@k sample estimates reachable signal at that
   budget. It does **not** prove a hard learning ceiling: transfer and policy
   changes can expose new successes. All-equal GRPO groups do, however, have no
   relative reward signal for that update.
6. With an SFT adapter loaded, disabling it gives the original base policy,
   not an SFT reference. The new GRPO path continues the same adapter with
   `beta=0`, explicitly; it does not pretend to KL-anchor to SFT.

## New executable path

`pipeline/training.py` owns the task schema, family split, bundle integrity and
verifier. `gpu/train_verified.py` implements completion-only SFT, GRPO and
generation/evaluation using those same task prompts and verification rules.
It reuses the tested masking, exact model class, vision exclusion and gradient
smoke from `gpu/lypning_lora.py`; the legacy standalone runner remains usable
for reproducing historical runs. GPU dependencies remain outside the Python
package's zero-dependency runtime.

| Observed result on **all** test inputs | Coverage task reward | Fallback-control reward |
|---|---:|---:|
| Correct CPython; correct native execution on every input | 1 | 1 |
| Correct CPython; at least one valid native refusal | 0.25 | 1 |
| Wrong, exception, timeout, empty or truncated completion | 0 | 0 |
| Unstable oracle, malformed exit-90, harness failure, native mismatch after correct CPython | Abort, save RL witness | Abort, save RL witness |

No partial-correctness or syntax bonuses. Refusal must be exit 90, one
`lypning-l: unsupported: kind: detail` stderr line and empty stdout. Correct
fallback receives credit; a fallback-control task does not penalise a legitimate
native implementation either. Core `lypning` acceptance is **not** a second
training requirement: teach the broad surface first, measure core compatibility
as a separate later slice.

The initial verifier supports deterministic UTF-8 stdout, empty stderr and exit
zero, with varied stdin/argv/input files. Each case needs at least three distinct
inputs and at least two expected outputs. Expectations are independently
specified, then references are checked twice on CPython and against the engine.
This is finite test evidence, **not a proof of correctness**. It does not check
file postconditions, binary-output equivalence or arbitrary checker scripts.
Those need their own explicit observable contracts before production editing
tasks can enter this reward. Avoid model-written checkers that share a model's
mistake with its solution.

Prompts contain the ordinary task, not expected outputs, references, refusals or
engine hints. The bundle retains tests/references for the trusted verifier;
they are not passed into model inputs. Training samples semantic families with
equal mass. Reference solutions are authored SFT seeds, **not** labelled as
on-policy samples. No frozen legacy dataset or historical grade is rewritten.

The 16-family authored starter is a **smoke curriculum**, not a training corpus
or independent benchmark. It exercises integers/bigints, sorting, deduplication,
Counter, regex, JSON, CSV, Unicode, GCD, hex parsing, bracket validation, hashing,
Decimal fallback, argv and input files. Measured locally on 2026-09-14: all 48
reference test inputs passed correctness; 45 executed natively and the three
Decimal inputs validly refused. The deterministic starter split has 12 train,
2 dev and 2 test families. Do not report its scores as model quality evidence.

## Commands and execution safety

Run from the repository root. Use the same Python minor version as the engine.
The bundle pins binary hash, Python version, verifier/sandbox source hashes,
limits, prompts, references, test cases and family split. Existing output
directories are refused. Build a **new** bundle on the actual Linux worker;
copying a macOS engine-graded bundle there must fail the identity check.

```bash
# Linux worker: use the compiled lypning-l artifact, not a shim or dispatcher.
PYTHONPATH=src:nemotron python -m pipeline.cli training-prepare \
  --starter \
  --engine src/lypning/assets/rust/target/variant-l/release/lypning \
  --output work/training-smoke

# Replace --starter with --cases independently-authored.jsonl for real data.
# For reviewed local macOS smoke fixtures only, explicitly add --memory-mb 0.
```

`--memory-mb 0` is a recorded opt-out for macOS's RLIMIT_AS failure, not a silent
weakening of all runners. A real training/eval run refuses such a bundle.

Set `QWEN_REV` to a verified 40-character Hub commit for this model and
`LYPNING_L_BIN` to the absolute native binary path. Neither is auto-discovered.
These commands do not submit jobs, rent hardware or upload to the Hub:

```bash
# No torch import, checkpoint download, generation or training:
PYTHONPATH=src:nemotron python nemotron/gpu/train_verified.py sft \
  --bundle work/training-smoke/bundle.json --engine "$LYPNING_L_BIN" \
  --revision "$QWEN_REV" --output work/sft-plan --smoke --plan

# On the isolated worker, first exercise tiny-model gradients, tokenisation,
# actual SFT/GRPO trainer steps, generation and verification. Smoke is not a
# meaningful model-quality run; random models can give all-zero RL rewards.
uv run nemotron/gpu/train_verified.py sft --smoke --isolated-worker \
  --bundle work/training-smoke/bundle.json --engine "$LYPNING_L_BIN" \
  --revision "$QWEN_REV" --output work/sft-smoke
uv run nemotron/gpu/train_verified.py grpo --smoke --from-base --isolated-worker \
  --bundle work/training-smoke/bundle.json --engine "$LYPNING_L_BIN" \
  --revision "$QWEN_REV" --output work/grpo-smoke
```

`--isolated-worker` is an operator attestation, **not an isolation mechanism**.
The existing subprocess harness scrubs environment variables, bounds resources
and uses a fresh cwd, but shares the filesystem. Run generated code in a
disposable, externally constrained worker without credentials, private home
directories or host repo mounts. Deny its outbound networking independently;
preload weights. A future remote verifier service should keep test registries
and trainer storage outside the generated program's filesystem namespace.
Do not run model rollouts in this personal development checkout.

Update, 2026-09-15: resource setup now runs in a fresh launcher, not `preexec_fn`.
Linux retains the address-space cap; macOS uses a sampled process-group RSS
watchdog for diagnostics. A host that denies process inspection reports a
harness failure. The pytest-only `--no-memory-limit` flag explicitly skips that
guard on a restricted macOS host; CI runs without it. Schema-2 bundles pin the
launcher/memory policy and distinguish smoke from pilot data. The starter cannot
launch a real job. Checkpoint selection also protects coverage and fallback
correctness separately against the dev baseline. Ordinary failing tracebacks
receive zero reward; only would-be successes are checked for oracle stability.

After the smoke and the real-data admission gates pass: prepare a fresh real
bundle, run SFT without `--smoke`, then GRPO with `--adapter` pointing to the
selected `adapter-N` directory. Both use the same bundle and pinned base. The
saved GRPO adapter includes its SFT warm start and reloads directly on the base;
there is no hidden merged parent. `best.json` identifies the selected checkpoint
(possibly step 0 if nothing improved). Checkpoints are selected by family-macro
dev correctness first, then correct-native rate; plateau patience defaults to
three checks. This small-sample checkpoint heuristic is not a release test.

Run `eval --adapter ... --eval-split test` only after selection is locked. Run
the unadapted `eval` arm using identical bundle, revision and generation settings.
Dev/test IDs cannot enter the RL reward callback. Normal evaluation is greedy
pass@1; it does not silently compare against a provider's sampled pass@k result.

The initial GRPO settings are deliberately plain: four generations per prompt,
learning rate 1e-6, fixed-length-normalised Dr.GRPO objective, no reward standard
deviation scaling, no KL, truncated outputs masked. These are an experiment
starting point, not tuned optima. API semantics are documented by
[TRL](https://huggingface.co/docs/trl/grpo_trainer). SFT starts at 2e-5, completion
tokens only, no silent truncation, with token-weighted gradient accumulation.
Run manifests record package versions, source hashes, adapter lineage and args.
RL writes program/score/family/population rows as well as correctness metrics,
so high reward cannot conceal a changing task mixture.

## Gates before spending on a real run

1. Resolve or explicitly quarantine the signal ladder's existing engine
   mismatches. Never transform a mismatch into a successful sample or claim
   it has been fixed by the training refactor.
2. Author a broader independent task set with source/project/template-family
   grouping, robust edge inputs, supported-module retention and fallback
   controls. Review family labels and near-duplicates manually; a family string
   alone cannot detect semantic leakage. Keep old refusal rewrites as a separate
   diagnostic distribution. Three test inputs is an admission floor, not enough
   validation for arbitrary programs.
3. Run small pinned-base rollouts on **training** families. Measure joint
   correctness/native acceptance, fallback correctness, reward variance and
   truncation by family. For all-zero groups improve tests/data/elicitation or
   add verified SFT successes; do not award incorrect answers partial credit.
4. Execute the exact tiny-model SFT and GRPO paths on the pinned Linux stack.
   Check per-leaf gradients and adapter reload. Then run a short real-model pilot
   under a separately approved resource budget. CPU unit tests are not this gate.
5. Compare base, SFT-only, RL-from-base and SFT→RL under matched compute/token
   budgets and at least two seeds. Checkpoint on dev, report once on test.
   Report family-macro correctness, joint correct-native pass@1, fallback and
   retention slices, length/import changes, measured latency, and mismatch
   counts. Use paired family-level uncertainty intervals. Require correctness
   non-inferiority and a real correct-native improvement before adoption.
6. Only then assess smaller-core routing, rank changes, DPO ablations, execution
   speed rewards, or a separately trained feedback-repair policy.

## Verification status

On 2026-09-14 the focused verifier/masking/gate checks and the explicit native
starter check passed locally. No real-model SFT/GRPO, GPU smoke, benchmark quality
comparison or paid job has been run as part of this refactor. The pinned TRL
integration still needs the Linux hardware smoke above.

The wider pipeline suite also fails on the untouched upstream snapshot
`af5d908` on this macOS/Python 3.14 environment: 50 failures, 235 passes, 2 skips.
Many fail in `preexec_fn` while configuring the pre-existing subprocess memory
cap. This refactor does not claim to fix that legacy harness or the recorded
runtime mismatches.

A matched full-suite run of the refactor had 50 failures, 273 passes and 3 skips;
the JUnit failing-test sets were identical (zero added failures). Further focused
tests cover CLI failure reporting, mid-run engine drift and the real-tokenizer
vocabulary in the tiny-model path. The final focused verifier/masking/gate run
passed **78 tests on 2026-09-15**, including the opt-in native starter check.
The full suite
regenerates two historical summary files with platform-dependent floating-point
rounding; those incidental changes were reverted, preserving the recorded data.
