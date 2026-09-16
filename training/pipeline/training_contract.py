"""Frozen decoding, artifact provenance and train-only RL admission (no GPU imports)."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path

from .jsonio import sha256_of
from .training_types import TrainingError

BASE_MODEL = "Qwen/Qwen3.8-27B"
CONTRACT_VERSION = 1
GPU_VERSIONS = {"torch": "2.9.1", "transformers": "5.17.0", "peft": "0.20.0",
                "accelerate": "1.15.0", "huggingface-hub": "1.31.0", "safetensors": "0.8.0",
                "trl": "1.13.0", "datasets": "4.7.0"}


def runtime_versions():
    try:
        actual = {name: importlib.metadata.version(name) for name in GPU_VERSIONS}
    except importlib.metadata.PackageNotFoundError as exc:
        raise TrainingError("missing GPU dependency; run with the pinned uv script environment") from exc
    # CUDA wheel build tags do not change the pinned public torch version.
    if any(actual[k].split("+", 1)[0] != v for k, v in GPU_VERSIONS.items()):
        raise TrainingError("GPU dependency versions differ from the pinned experiment: " + str(actual))
    return actual


def decoding(max_tokens, *, greedy=False):
    # Non-thinking first-draft experiment. Presence penalty is explicitly zero:
    # Transformers does not implement the serving API's presence_penalty=1.5.
    return dict(do_sample=not greedy, temperature=0.7, top_p=0.8, top_k=20,
                min_p=0.0, repetition_penalty=1.0, max_new_tokens=max_tokens,
                num_beams=1, num_return_sequences=1)


def draw_seed(seed, case_id, draw):
    return int(sha256_of([seed, case_id, draw])[:8], 16) % (2 ** 31)


def complete(token_ids, eos):
    stops = eos if isinstance(eos, (list, tuple, set)) else [eos]
    return bool(len(token_ids)) and int(token_ids[-1]) in stops


def source_identity(root):
    root = Path(root)
    paths = list((root / "pipeline").glob("*.py")) + list((root / "gpu").glob("*.py"))
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)}


def model_config_identity(config):
    """Architecture identity must not depend on a worker cache path/cache mode."""
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k not in ("_name_or_path", "use_cache")}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    return sha256_of(clean(config))


def adapter_files(path):
    path = Path(path)
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in path.iterdir()
             if p.suffix in (".json", ".safetensors") and p.name not in ("experiment.json", "seal.json")}
    if "adapter_model.safetensors" not in files or "adapter_config.json" not in files:
        raise TrainingError("adapter weights/config missing")
    return files


def seal_adapter(path):
    path = Path(path)
    return {"files": adapter_files(path),
            "manifest_sha256": hashlib.sha256((path / "experiment.json").read_bytes()).hexdigest()}


def adapter_identity(path, revision):
    path = Path(path).resolve(strict=True)
    try:
        manifest = json.loads((path / "experiment.json").read_text())
        seal = json.loads((path / "seal.json").read_text())
    except FileNotFoundError as exc:
        raise TrainingError("adapter needs experiment.json and seal.json; legacy adapters need a reviewed migration") from exc
    if seal != seal_adapter(path):
        raise TrainingError("adapter artifact integrity check failed")
    if manifest.get("base_model") != BASE_MODEL or manifest.get("revision") != revision:
        raise TrainingError("adapter/base-model identity mismatch")
    if manifest.get("contract_version") != CONTRACT_VERSION:
        raise TrainingError("adapter experiment contract changed")
    return {"sha256": sha256_of(seal), "experiment": manifest}


def probe_contract(bundle, revision, adapter, policy, seed, generations, smoke):
    return {"version": CONTRACT_VERSION, "bundle_digest": bundle["digest"],
            "base_model": BASE_MODEL, "revision": revision,
            "adapter_sha256": adapter["sha256"] if adapter else None,
            "decoding": policy, "enable_thinking": False, "seed": seed,
            "generations": generations, "smoke": smoke,
            "code_sha256": source_identity(Path(__file__).resolve().parents[1])}


def probe_report(records, contract):
    groups = {}
    for row in records:
        groups.setdefault(row["case_id"], []).append(row)
    if not groups or any(len(g) != contract["generations"] for g in groups.values()):
        raise TrainingError("probe requires a complete generation group for every train case")
    if any({r["draw"] for r in g} != set(range(contract["generations"])) for g in groups.values()):
        raise TrainingError("probe has duplicated or missing draw IDs")
    # Truncated samples are masked from the RL loss: they cannot supply the
    # variation that admits a run, even if their nominal zero reward differs.
    informative = sum(len({r["reward"] for r in group if not r["truncated"]}) > 1
                      for group in groups.values())
    correct = sum(r["correct"] for r in records)
    report = {"contract": contract, "groups": len(groups), "informative_groups": informative,
              "correct_draws": correct, "draws": len(records),
              "truncated_draws": sum(r["truncated"] for r in records),
              "completion_tokens": sum(r["completion_tokens"] for r in records),
              "records_sha256": sha256_of(records),
              "admitted": informative >= 2 and correct > 0}
    report["digest"] = sha256_of(report)
    return report


def validate_probe(path, contract, train_ids):
    path = Path(path)
    report = json.loads(path.read_text())
    digest = report.pop("digest", None)
    if digest != sha256_of(report) or report.get("contract") != contract:
        raise TrainingError("probe integrity/experiment mismatch; re-probe the exact starting policy")
    records = [json.loads(line) for line in path.with_name("probe-rollouts.jsonl").read_text().splitlines()]
    if {r["case_id"] for r in records} != set(train_ids):
        raise TrainingError("probe must contain exactly the train cases")
    expected = probe_report(records, contract)
    if expected["digest"] != digest or not expected["admitted"]:
        raise TrainingError("probe has insufficient non-truncated reward variation; improve SFT/data before RL")
    return digest


def learning_rate(step, steps, peak, warmup_ratio):
    """Linear warmup then linear decay, indexed by the step about to execute."""
    warmup = min(steps, max(0, int(steps * warmup_ratio)))
    if warmup and step <= warmup:
        return peak * step / warmup
    return peak * (steps - step + 1) / max(1, steps - warmup)
