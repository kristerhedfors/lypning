"""Reuse a completed same-run evaluation only with sealed policy equivalence.

Step zero in GRPO is its SFT parent, which need not be the unadapted base.
A checkpoint number alone is never evidence of equivalence.
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
    for key in CONTRACT_KEYS:
        if key not in prior or key not in manifest or prior[key] != manifest[key]:
            raise TrainingError("evaluation reuse contract mismatch: " + key)
    for key in ARGUMENT_KEYS:
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
    evidence = {"source": str(source), "basis": proof,
                "source_sha256": {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                                  for name in ("experiment.json", "evaluations.jsonl", "metrics.json")},
                "independent_draws": False}
    shutil.copyfile(source / "evaluations.jsonl", output / "evaluations.jsonl")
    write_json(output / "reuse.json", evidence)
    write_json(output / "metrics.json", metrics)
    return True
