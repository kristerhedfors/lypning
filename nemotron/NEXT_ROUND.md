# Next training round — manual agent handoff

Prepared 2026-09-15. **No training, GPU rental, automation or weight upload is
started by this change.** The next coding agent launches manually, after the
operator approves the worker and an enforced cost/time cap. Read `TRAINING.md`
for the objective; this file is the executable handoff and stop checklist.

## Assignment and evidence

Pilot **Qwen/Qwen3.8-27B → lypning-l**: matched base control, verified SFT, then
execution-verified GRPO from the selected SFT checkpoint. Keep model revision,
engine, task-family split, template and evaluation fixed. First-draft correctness
comes before correct-native execution. Correct fallback is legitimate; core
`lypning` compatibility and feedback-repair training are separate later studies.

The code now fixes incorrect-program tracebacks being misclassified as unstable
oracles, threaded-parent `preexec_fn`, stale replay caches, tests rewriting
historical results, starter data accidentally launching a real run, and aggregate
checkpoint improvements concealing fallback-control regressions. CPU/native CI
does **not** exercise the exact Qwen/TRL GPU stack or demonstrate model quality.

## Admission gates — stop if any is unmet

1. Start from merged `main`, inspect its CI, and record `git rev-parse HEAD`.
2. Review `data/engine-mismatches.jsonl` and `LADDER.md`. The recorded Rust
   semantic discrepancies are **not claimed fixed by this training refactor**.
   Freshly replay relevant witnesses with `nt refusals --run`, then fix or
   explicitly quarantine affected families, recording counts and provenance.
   Quarantine is an experimental limitation, not a runtime fix. If it removes
   important target coverage, resolve the runtime before training. Any newly
   observed native mismatch aborts; it must never become a learning target.
3. Author independent multi-input pilot tasks. The starter is only a smoke
   fixture, not an independent benchmark. Review project/source/template-family
   overlap and near-duplicates before freezing; cloning templates does not add
   independent families. Include supported-module retention and fallback
   controls, empty/boundary/Unicode inputs, varied argv and input files.
4. Schema-2 pilot admission enforces at least 18 families and both populations
   in train/dev/test. This is an admission floor, not adequate statistical power
   by itself. Use substantially broader coverage for a quality claim. Three
   distinct test inputs and two different expected outputs is likewise a floor.
5. The verifier currently covers deterministic UTF-8 stdout, empty stderr and
   exit zero. Do not admit file-editing tasks, binary-output tasks or arbitrary
   checkers without implementing their observable contracts.
6. Establish and test an actual execution boundary for generated code.
   `--isolated-worker` is **an attestation, not a jail**. The subprocess helper
   shares the filesystem. Candidate programs must not see the bundle/test
   registry, trainer outputs, credentials or private checkout. Configure an
   external sandbox/remote verifier with those paths excluded and egress blocked.
   A shared writable host mount or environment scrubbing alone is insufficient.
   If that boundary is unavailable, stop before executing any generated code.

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

Input JSONL fields: `case_id`, `family`, `task`, `reference`, `provenance`,
`population` (`coverage` or `fallback-control`), and `tests` (each with `stdout`
and optional `stdin`, `argv`, UTF-8 `files`). `pipeline/curriculum.py` illustrates
the schema, not production data quality. Family splits are assigned before any
sampling. References and expected outputs do not enter model prompts.

```bash
"$ROUND_PYTHON" -m pipeline.cli training-prepare --starter \
  --engine "$LYPNING_L_BIN" --output work/round-02/smoke

"$ROUND_PYTHON" -m pipeline.cli training-prepare \
  --cases work/round-02/reviewed-cases.jsonl --purpose pilot --seed 1111 \
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

# 2. Unadapted dev control, same greedy generation as checkpoint selection.
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
# 4. After reload, useful rollout signal and the approved budget are confirmed.
uv run --python "$ROUND_PYTHON" nemotron/gpu/train_verified.py grpo \
  --adapter "$SFT_ADAPTER" --isolated-worker --bundle work/round-02/pilot/bundle.json \
  --engine "$LYPNING_L_BIN" --revision "$QWEN_REV" --output work/round-02/grpo-1111 \
  --steps 20 --eval-every 5 --patience 3 --rank 16 --batch-size 4 --seed 1111
```

Stop on a harness/native mismatch, non-finite loss/gradient, changed prompt
template, missing base weights, dead adapter projection, extreme truncation or
sustained all-equal reward groups. Preserve failed runs and
`blocked-witnesses.jsonl`. Inspect `rollouts.jsonl` by family/population, not just
the aggregate reward. Never award wrong runnable code partial credit.

Adapters are saved, not optimizer/RNG resume state. Loading an adapter starts a
**new** run with recorded lineage; it is not an exact resume. Repeat the design
with seed 2222 and the **same bundle**. Include an RL-from-base ablation only
within the approved matched budget. Select on dev, then lock settings/checkpoints
before independent `eval --eval-split test` per arm.

## Handback

Report identities, selected steps, run paths, actual time/cost, family-macro
correctness and correct-native pass@1, coverage/fallback slices, mismatch,
quarantine and truncation counts, and paired family-level uncertainty intervals.
Compare matched base/SFT/GRPO arms, not a local run against a historical
provider's sampled pass@k. Require correctness non-inferiority and meaningful
correct-native improvement before promotion. Otherwise retain base/SFT, explain
the failed gate and propose the smallest next experiment. Completing a trainer
run is not evidence of a model-quality win.
