# Next training round — manual agent handoff

Prepared 2026-09-15. **No training, GPU rental, automation or weight upload is
started by this change.** The next coding agent launches manually, after the
operator approves the worker and an enforced cost/time cap. Read `TRAINING.md`
for the objective; this file is the executable handoff and stop checklist.

**Other-device entry point:** read [START_NEXT_ROUND.md](START_NEXT_ROUND.md)
first for private evidence/review, candidate-image construction and the portable
`pipeline.round_plan` command. [DATA_PRODUCTION.md](DATA_PRODUCTION.md) owns the
complete observation-to-next-dataset loop and interface/privacy limits.

## Assignment and evidence

Pilot **Qwen/Qwen3.8-27B → lypning-l**: matched base control, verified SFT, then
execution-verified GRPO from the selected SFT checkpoint. Keep model revision,
engine, task-family split, template and evaluation fixed. First-draft correctness
comes before correct-native execution. Correct fallback is legitimate; core
`lypning` compatibility and feedback-repair training are separate later studies.

The regime now separates admission, verification, optimizer stages and evaluation.
Schema 3 groups sources/families/solution ASTs, verifies population labels, seals
adapters, gates RL on train-only probes and preserves RNG during matched-seed
evaluation. Per-capability correctness gates complement population checks. CPU/native CI
does **not** exercise the exact Qwen/TRL GPU stack or demonstrate model quality.

## Admission gates — stop if any is unmet

1. Start from merged `main`, inspect its CI, and record `git rev-parse HEAD`.
2. Review `data/engine-mismatches.jsonl` and `LADDER.md`. The recorded Rust
   semantic discrepancies are **not claimed fixed by this training refactor**.
   Review witnesses statically first; replay only behind the execution boundary
   in gate 6, then fix or
   explicitly quarantine affected families, recording counts and provenance.
   Quarantine is an experimental limitation, not a runtime fix. If it removes
   important target coverage, resolve the runtime before training. Any newly
   observed native mismatch aborts; it must never become a learning target.
3. Author independent multi-input pilot tasks. The starter is only a smoke
   fixture, not an independent benchmark. Review project/source/template-family
   overlap and near-duplicates before freezing; cloning templates does not add
   independent families. Include supported-module retention and fallback
   controls, empty/boundary/Unicode inputs, varied argv and input files.
4. Schema-3 pilot admission enforces at least 18 families, reviewed source and
   capability labels, and two families plus two independent components per
   population in each train/dev/test split. This is an admission floor, not adequate statistical power
   by itself. Use substantially broader coverage for a quality claim. Three
   distinct test inputs and two different expected outputs is likewise a floor.
5. The verifier currently covers deterministic UTF-8 stdout, empty stderr and
   exit zero. Do not admit file-editing tasks, binary-output tasks or arbitrary
   checkers without implementing their observable contracts.
6. Build/test the pinned candidate boundary using `START_NEXT_ROUND.md`: the
   Docker image on a disposable worker, or the pooled Hugging Face sandbox
   contract (`--execution-kind hf-sandbox-pool`) chosen on 2026-09-15, which
   fails closed on the Space's Hub commit and on the identity every sandbox
   response carries, because a Space name is not an immutable image.
   Pilot preparation requires both `--review` and `--execution-image`;
   generated code (even smoke) requires an isolated execution bundle.
   `--isolated-worker` remains an additional operator attestation, not a jail.
   The local subprocess helper is allowed only for reviewed CPU smoke fixtures.
   Candidate containers have no host mounts/network/GPU/credentials; identity
   checks and real protocol fixtures must pass before model loading. If this
   boundary or an approved disposable worker is unavailable, stop.
   The corpus replay now safety-skips recognised package installs and model
   downloads before either arm runs; this is not a general containment layer.
   CI exposed a captured installer mutating the comparison's shared interpreter.
   Never admit dependency installers or model-download tasks into this pilot.

## Freeze the worker, model and inputs

Use one Linux GPU/process; distributed `WORLD_SIZE > 1` is rejected. Resolve one
Python 3.12 interpreter and use it for builds, preparation and the GPU script.
The bundle pins the complete CPython version, so do not prepare with a different
system interpreter. GPU dependencies are pinned in the script, not the package.

Do not upgrade the pilot oracle to 3.14 on the strength of the starter smoke.
The 2026-09-15 broader-suite audit exposed tuple-unpack/list-index/modulo
wording, sort reverse conversion and `int.to_bytes` edge cases. They now have
differential fixes tested against fresh 3.12.13, 3.13.13 and 3.14.5 engines,
including build-sensitive behavior probes. This is not full-runtime
certification: keep the pilot's 3.12 pin and perform the admission audit above.
Rebuild and prepare a new bundle after any engine or oracle change; do not
reuse verified labels across a coverage expansion.
For the newly added CSV, named-regex and numeric surfaces, see the
[coverage boundaries and dataset-authoring directions](../docs/L-COVERAGE.md).

```bash
# Repository root on the approved disposable worker.
export PYTHONPATH=src:nemotron
export LYPNING_CAPTURE=0 LYPNING_HARVEST=0
ROUND_PYTHON=$(uv python find 3.12)
export LYPNING_CPYTHON="$ROUND_PYTHON"
export LYPNING_HOME="$PWD/work/round-02/engine-home"
"$ROUND_PYTHON" -m lypning build --rust --target host
export LYPNING_L_BIN="$LYPNING_HOME/bin/lypning-l"
"$LYPNING_L_BIN" --version
git rev-parse HEAD
```

`LYPNING_CPYTHON` pins the build's oracle discovery too; invoking the build with
3.12 alone does not override another `python3` earlier on `PATH`.

Resolve the official model repository's immutable 40-character Hub commit and
record it as `QWEN_REV`; do not use `main`. Preload packages/weights before
candidate execution in the egress-disabled worker. Keep credentials outside the
candidate filesystem. Apply an enforced wall-clock/GPU budget through the worker
scheduler for every run; `--steps` alone is not a monetary limit.

Input JSONL fields: `case_id`, `family`, `source_group`, `capabilities` (nonempty
list of reviewed labels), `task`, `reference`, `provenance`,
`population` (`coverage` or `fallback-control`), and `tests` (each with `stdout`
and optional `stdin`, `argv`, UTF-8 `files`). `pipeline/curriculum.py` illustrates
the schema, not production data quality. Connected source/family/solution groups
are split before sampling. Coverage references must answer natively on every
input; control references must validly refuse on every input. New L capabilities
invalidate old control labels. References and expected outputs do not enter
model prompts. Do not reuse schema-2 bundles or unsealed historical adapters.

```bash
"$ROUND_PYTHON" -m pipeline.cli training-prepare --starter \
  --engine "$LYPNING_L_BIN" --execution-image "$EXECUTION_IMAGE" --output work/round-02/smoke

"$ROUND_PYTHON" -m pipeline.cli training-prepare \
  --cases work/round-02/reviewed/cases.jsonl --review work/round-02/reviewed/review.json \
  --execution-image "$EXECUTION_IMAGE" --purpose pilot --seed 1111 \
  --engine "$LYPNING_L_BIN" --output work/round-02/pilot

# Plan only: no GPU imports, downloads, generation or optimisation.
"$ROUND_PYTHON" nemotron/gpu/train_verified.py sft --plan \
  --bundle work/round-02/pilot/bundle.json --engine "$LYPNING_L_BIN" \
  --revision "$QWEN_REV" --output work/round-02/sft-1111 \
  --steps 20 --eval-every 5 --patience 3 --rank 16 --batch-size 4
```

Archive the bundle digest, model revision, engine hash, repository commit,
package versions, data-review/quarantine decisions and approved budget. Do not
edit a bundle or move the held-out split during an experiment. Output directories
are never overwritten; interrupted preparation publishes no usable manifest.

## Manual launch sequence — next agent only

```bash
# 1. Exact tiny-model SFT/GRPO wiring with the production tokenizer.
uv run --python "$ROUND_PYTHON" nemotron/gpu/train_verified.py sft \
  --smoke --isolated-worker --bundle work/round-02/smoke/bundle.json \
  --engine "$LYPNING_L_BIN" --revision "$QWEN_REV" --output work/round-02/sft-smoke
uv run --python "$ROUND_PYTHON" nemotron/gpu/train_verified.py grpo \
  --smoke --from-base --isolated-worker --bundle work/round-02/smoke/bundle.json \
  --engine "$LYPNING_L_BIN" --revision "$QWEN_REV" --output work/round-02/grpo-smoke

# 2. Unadapted dev control, same sampled first-draft policy as checkpoint selection.
uv run --python "$ROUND_PYTHON" nemotron/gpu/train_verified.py eval \
  --isolated-worker --bundle work/round-02/pilot/bundle.json \
  --engine "$LYPNING_L_BIN" --revision "$QWEN_REV" --output work/round-02/base-dev

# 3. Bounded SFT pilot.
uv run --python "$ROUND_PYTHON" nemotron/gpu/train_verified.py sft \
  --isolated-worker --bundle work/round-02/pilot/bundle.json \
  --engine "$LYPNING_L_BIN" --revision "$QWEN_REV" --output work/round-02/sft-1111 \
  --steps 20 --eval-every 5 --patience 3 --rank 16 --batch-size 4 --seed 1111
```

Read `sft-1111/best.json`; set `SFT_ADAPTER` to its selected `adapter-N` directory.
Step 0 is valid: it means SFT did not improve the development criterion. Do not
substitute the final checkpoint because it trained longer. Reload the selected
adapter with `eval --adapter "$SFT_ADAPTER"` and reproduce its dev record first.
Check train-family rollout correctness and reward variation before scaling RL;
random tiny-model all-zero rewards only test plumbing, not learnable signal.

```bash
# 4. Probe the exact selected policy on TRAIN cases only; no optimizer updates.
uv run --python "$ROUND_PYTHON" nemotron/gpu/train_verified.py probe \
  --adapter "$SFT_ADAPTER" --isolated-worker --bundle work/round-02/pilot/bundle.json \
  --engine "$LYPNING_L_BIN" --revision "$QWEN_REV" --output work/round-02/probe-1111 \
  --generations 4 --seed 1111

# Inspect probe.json and per-family probe-rollouts.jsonl before the next command.
# 5. RL requires that exact admitted probe (bundle/base/adapter/decoding/seed/code).
uv run --python "$ROUND_PYTHON" nemotron/gpu/train_verified.py grpo \
  --adapter "$SFT_ADAPTER" --isolated-worker --bundle work/round-02/pilot/bundle.json \
  --engine "$LYPNING_L_BIN" --revision "$QWEN_REV" --output work/round-02/grpo-1111 \
  --steps 20 --eval-every 5 --patience 3 --rank 16 --generations 4 --seed 1111 \
  --probe work/round-02/probe-1111/probe.json
```

Stop on a harness/native mismatch, non-finite loss/gradient, changed prompt
template, missing base weights, dead adapter projection, extreme truncation or
sustained all-equal reward groups. Preserve failed runs and
`blocked-witnesses.jsonl`. Inspect `rollouts.jsonl` by family/population, not just
the aggregate reward. Never award wrong runnable code partial credit.

Adapters are saved, not optimizer/RNG resume state. Loading an adapter starts a
**new** run with recorded lineage; it is not an exact resume. Repeat the design
with seed 2222 and the **same bundle**, using a fresh matching probe. Include an RL-from-base ablation only
within the approved matched budget. Select on dev, then lock settings/checkpoints
before independent `eval --eval-split test` per arm. Keep `--eval-draws`,
`--max-new-tokens`, seed and the full decoding contract identical across compared
arms. Four draws estimate sampled pass@1; do not report them as best-of-four.
Use `--greedy` only for a separately labelled, matched diagnostic.

The default non-thinking policy is explicit, not Qwen's default mode. Do not
toggle thinking during a run or supply code-only SFT as if it contained verified
reasoning. See the controlled thinking-mode ablation in
[L-TRAINING-ROADMAP.md](L-TRAINING-ROADMAP.md).

```bash
# Compare standalone eval directories only; no model loading or program execution.
"$ROUND_PYTHON" -m pipeline.training_report \
  work/round-02/base-test work/round-02/grpo-test
```

Require sealed adapter reloads (`seal.json`) and inspect all population and
capability slices. Probe admission (two informative train groups) is a minimum
wiring/signal gate, not evidence that scaling is worthwhile. Report the
informative fraction, all-zero/all-equal groups and truncation by family before
the operator authorizes a longer run. A failed probe means improve data/SFT or
keep the better baseline; never reshape reward to pay for wrong code.

## Handback

Report identities, selected steps, run paths, actual time/cost, family-macro
correctness and correct-native pass@1, coverage/fallback slices, mismatch,
quarantine and truncation counts, and paired source/family-component uncertainty intervals.
Compare matched base/SFT/GRPO arms, not a local run against a historical
provider's sampled pass@k. Require correctness non-inferiority and meaningful
correct-native improvement before promotion. Otherwise retain base/SFT, explain
the failed gate and propose the smallest next experiment. Completing a trainer
run is not evidence of a model-quality win.
