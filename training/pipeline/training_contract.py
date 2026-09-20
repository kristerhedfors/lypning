"""Frozen decoding, artifact provenance and train-only RL admission (no GPU imports)."""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path

from .jsonio import sha256_of
from .training_types import TrainingError

BASE_MODEL = "Qwen/Qwen3.8-27B"
CONTRACT_VERSION = 1
#: Draws per case for a confirmatory eval-2 arm, pre-registered in `EVAL2.md`
#: §4 and not ours to move: the §4 rule is priced at this k, so an arm drawn at
#: another one is a different instrument whose interval is wider than any effect
#: it is looking for. The runner's own `--eval-draws` default is a smoke
#: setting, which `EVAL2.md` §4 says in as many words and which the round-02
#: pilot spent an arm proving, so the two disagree by construction and the
#: disagreement has to be caught somewhere that costs nothing. `preflight`.
PROTOCOL_EVAL_DRAWS = 16
#: S4's minimum evidence dose, accepted from `ASSESSMENT.md` section 6 on
#: 2026-09-17. These are training gates, not claims about model quality.
MIN_TRAIN_CASES = 1000
MIN_SUPERVISED_TOKENS = 50_000
PROTOCOL_TRAIN_SEEDS = (1111, 2222, 3333)
#: Pinned because a kernel is part of an arm's identity, not a free speedup.
#: `STATUS.md` §2 records a kernel swap on IDENTICAL weights moving dSLR by
#: +1.57pp -- larger than either adapter of 2026-09-14 moved it, and the null
#: that would otherwise have manufactured a win of the wrong sign. So two arms
#: on different kernels are not comparable, and the kernel is pinned here with
#: everything else rather than left to whatever the image happens to have.
#:
#: `flash-linear-attention` serves `chunk_gated_delta_rule` and
#: `fused_recurrent_gated_delta_rule`, which are 48 of this model's layers;
#: without it transformers falls back to reference PyTorch and says so. 0.5.2
#: is the version `STATUS.md` records for the v1 run of record (`fla-0.5.2`).
#:
#: PINNING IT IS NOT ENOUGH, and the round of 2026-09-20 is the proof: with
#: `flash-linear-attention==0.5.2` installed and `runtime_versions` passing,
#: transformers still reported it "not installed" and ran all 48 layers on the
#: reference path. The DISTRIBUTION was present; the MODULE would not import.
#: Metadata is not the question, so `kernel_state` asks the real one and the
#: arm records what was usable rather than what was requested.
#:
#: `causal_conv1d` is DELIBERATELY ABSENT. It covers `causal_conv1d_fn` and
#: `causal_conv1d_update`, but PyPI ships it as an sdist only (1.7.0, checked
#: 2026-09-20), so adding it means an nvcc build on a metered job that can hang
#: or fail at exit 123. Its fallback stays, and it stays visible in the log.
GPU_VERSIONS = {"torch": "2.9.1", "transformers": "5.17.0", "peft": "0.20.0",
                "accelerate": "1.15.0", "huggingface-hub": "1.31.0", "safetensors": "0.8.0",
                "trl": "1.13.0", "datasets": "4.7.0",
                "flash-linear-attention": "0.5.2"}


def runtime_versions():
    try:
        actual = {name: importlib.metadata.version(name) for name in GPU_VERSIONS}
    except importlib.metadata.PackageNotFoundError as exc:
        raise TrainingError("missing GPU dependency; run with the pinned uv script environment") from exc
    # CUDA wheel build tags do not change the pinned public torch version.
    if any(actual[k].split("+", 1)[0] != v for k, v in GPU_VERSIONS.items()):
        raise TrainingError("GPU dependency versions differ from the pinned experiment: " + str(actual))
    return actual


def kernel_state():
    """Which fused kernels are ACTUALLY usable, which is not what is pinned.

    `runtime_versions` reads distribution metadata and a fallback can satisfy
    it: see the 2026-09-20 round above. This asks the question transformers
    asks -- can the module be imported -- so a comparison between arms can be
    made on what ran.

    It matters because the kernel is part of an arm's identity: `STATUS.md` §2
    records a swap on IDENTICAL weights moving dSLR by +1.57pp, larger than
    either adapter of 2026-09-14 moved it. A cross-arm read that straddles this
    line is not a comparison, and nothing else in the record would show it.

    Never raises: this is an observation written into the manifest, and a round
    must not die because a kernel it can run without is missing. `NTX_USE_FLA`
    is reported beside it because `train_verified.run` sets it to "0", so an
    importable kernel can still be deliberately unused -- two different reasons
    for the same reference path, and the manifest should distinguish them.
    """
    state = {"NTX_USE_FLA": os.environ.get("NTX_USE_FLA", "1")}
    for dist, module in (("flash-linear-attention", "fla"),
                         ("causal_conv1d", "causal_conv1d")):
        try:
            importlib.import_module(module)
        except BaseException as exc:                              # noqa: BLE001
            # BaseException: a kernel import can fail on a missing CUDA symbol,
            # which is not always an Exception subclass.
            state[dist] = "unusable: %s" % type(exc).__name__
        else:
            state[dist] = "usable"
    return state


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
