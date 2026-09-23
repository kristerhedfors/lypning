"""Reuse a completed same-run evaluation only with sealed policy equivalence.

Step zero in GRPO is its SFT parent, which need not be the unadapted base.
A checkpoint number alone is never evidence of equivalence.

Two reuses share one proof. `reuse_evaluation`: a standalone eval of a sealed
step-0 adapter reads its parent's eval (the eval-2 arms). `reuse_step_zero`:
an SFT stage whose freshly attached LoRA is a verified no-op records step 0
from the unadapted base-dev evaluation its own job just ran, instead of
generating the same draws again. Either way the source must be a complete
evaluation under the same runtime contract, arguments and cases, and the
provenance is written beside the copied draws.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

from .jsonio import write_json
from .training_metrics import summarize
from .training_types import TrainingError

CONTRACT_KEYS = ("base_model", "revision", "bundle_digest", "smoke", "decoding",
                 "enable_thinking", "tokenizer_sha256", "model_config_sha256", "code_sha256",
                 "versions", "hardware", "kernels", "contract_version", "metric_policy")
ARGUMENT_KEYS = ("seed", "eval_split", "eval_draws", "greedy", "eval_sequences", "score_workers")
#: What SFT step-0 reuse checks beyond the eval-2 reuse's keys. The stages
#: differ (eval vs sft), so what the model CALLS, when it stops and which bundle
#: purpose it read are compared outright, and `job_id` makes "the same job"
#: a recorded fact rather than an assumption about how the script was run.
STEP0_CONTRACT_KEYS = CONTRACT_KEYS + ("purpose", "kernel_binding", "eos_token_id",
                                       "presence_penalty", "job_id")
STEP0_ARGUMENT_KEYS = ARGUMENT_KEYS + ("engine", "bundle", "max_new_tokens")


def fresh_lora_is_noop(model):
    """Prove the freshly attached SFT adapter leaves the base unchanged."""
    parameters = list(model.named_parameters())
    tensors = [(n, p) for n, p in parameters if ".lora_" in n]
    return bool(tensors and any(".lora_B." in n for n, _ in tensors)
                and all(not p.requires_grad or ".lora_" in n for n, p in parameters)
                and all((".lora_A." in n or ".lora_B." in n)
                        and bool(p.isfinite().all())
                        and (".lora_B." not in n or not bool(p.detach().count_nonzero()))
                        for n, p in tensors))


def reuse_evaluation(source, output, manifest, cases):
    """Return False for trained/legacy adapters; mismatched evidence fails closed.

    The caller has loaded the target adapter and recorded the actual runtime
    manifest. Equivalence is written into a sealed checkpoint at creation, after
    checking zero finite LoRA tensors (SFT) or identical parent files (GRPO).
    """
    adapter = manifest.get("adapter")
    checkpoint = adapter.get("experiment", {}) if adapter else {}
    proof = checkpoint.get("policy_equivalence")
    if checkpoint.get("checkpoint_step") != 0 or not isinstance(proof, dict):
        return False
    if set(proof) != {"adapter_sha256", "basis"} or proof["basis"] not in (
            "finite-zero-lora-b", "identical-parent-files"):
        raise TrainingError("unknown checkpoint equivalence proof")
    source, output = Path(source), Path(output)
    prior = json.loads((source / "experiment.json").read_text())
    source_adapter = prior.get("adapter")
    if proof["adapter_sha256"] != (source_adapter["sha256"] if source_adapter else None):
        raise TrainingError("evaluation reuse needs the checkpoint's equivalent parent policy")
    if prior.get("stage") != "eval" or manifest.get("stage") != "eval":
        raise TrainingError("evaluation reuse needs standalone evaluation logs")
    metrics = _complete_evaluation(source, prior, manifest, cases, CONTRACT_KEYS, ARGUMENT_KEYS)
    _copy_with_provenance(source, output, proof)
    write_json(output / "metrics.json", metrics)
    return True


def reuse_step_zero(source, output, manifest, cases, noop):
    """SFT step 0 from this job's base-dev evaluation; None means measure it afresh.

    `noop` is `fresh_lora_is_noop(model)`, taken on the stage's model before
    any optimizer step: without it there is no proof and step 0 is generated
    (None). With it, the source must be the UNADAPTED standalone dev
    evaluation of the same job under the same runtime contract, draws,
    chunking, seed, bundle and engine; any mismatch raises, never falls back
    silently. Returns the step-0 metrics, recomputed from the copied draws and
    equal to the source's saved `metrics.json`, which is exactly what
    `measure(0)` returns: the CheckpointGate baseline and `best.json` follow.
    """
    if manifest.get("stage") != "sft" or manifest.get("adapter") is not None:
        raise TrainingError("step-0 reuse is for an SFT stage starting from the pinned base")
    if not noop:
        return None
    source, output = Path(source), Path(output)
    prior = json.loads((source / "experiment.json").read_text())
    if prior.get("stage") != "eval" or prior.get("adapter") is not None:
        raise TrainingError("step-0 reuse needs the unadapted base-dev evaluation")
    if not manifest.get("job_id"):
        raise TrainingError("step-0 reuse needs a job id: an absent one cannot show the same job")
    metrics = _complete_evaluation(source, prior, manifest, cases,
                                   STEP0_CONTRACT_KEYS, STEP0_ARGUMENT_KEYS)
    records = [json.loads(line) for line in (source / "evaluations.jsonl").read_text().splitlines()]
    if any(row.get("step") != 0 for row in records):
        raise TrainingError("step-0 reuse needs step-0 draws only")
    _copy_with_provenance(source, output, {"adapter_sha256": None, "basis": "finite-zero-lora-b"},
                          step=0)
    return metrics


def _complete_evaluation(source, prior, manifest, cases, contract_keys, argument_keys):
    """The saved metrics of a complete, contract-identical evaluation; raises otherwise."""
    for key in contract_keys:
        if key not in prior or key not in manifest or prior[key] != manifest[key]:
            raise TrainingError("evaluation reuse contract mismatch: " + key)
    for key in argument_keys:
        if prior["args"].get(key) != manifest["args"].get(key):
            raise TrainingError("evaluation reuse argument mismatch: " + key)
    # metrics.json is the completion marker; partial evaluations are not reusable.
    saved = json.loads((source / "metrics.json").read_text())
    records = [json.loads(line) for line in (source / "evaluations.jsonl").read_text().splitlines()]
    metrics = summarize(records, **manifest["metric_policy"])
    if metrics != saved:
        raise TrainingError("evaluation reuse metrics do not match saved draws")
    draws = 1 if manifest["args"]["greedy"] else manifest["args"]["eval_draws"]
    expected = {(c["case_id"], draw): c for c in cases for draw in range(draws)}
    if {(r["case_id"], r["draw"]) for r in records} != set(expected):
        raise TrainingError("evaluation reuse requires every requested case/draw")
    for row in records:
        case = expected[row["case_id"], row["draw"]]
        for key in ("family", "population", "capabilities", "split_group"):
            want = case.get(key, case["family"] if key == "split_group" else [])
            if row.get(key, []) != want:
                raise TrainingError("evaluation reuse case metadata mismatch")
    return metrics


def _copy_with_provenance(source, output, proof, **extra):
    """Copy the draws and write `reuse.json`: where they came from, hashed."""
    evidence = dict({"source": str(source), "basis": proof,
                     "source_sha256": {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                                       for name in ("experiment.json", "evaluations.jsonl", "metrics.json")},
                     "independent_draws": False}, **extra)
    shutil.copyfile(source / "evaluations.jsonl", output / "evaluations.jsonl")
    write_json(output / "reuse.json", evidence)
