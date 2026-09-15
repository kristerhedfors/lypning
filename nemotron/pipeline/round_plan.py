"""Persist a cross-device manual run plan. Never execute its commands.

Paths in the JSON config are relative to the repository root. Keep this plan,
the data review, bundle, stage artifacts and operator decisions together. The
plan reports missing prerequisites; it does not manufacture approvals or data.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path

from .jsonio import sha256_of, write_json
from .training_contract import BASE_MODEL, adapter_identity, source_identity
from .training_types import TrainingError

ROOT = Path(__file__).resolve().parents[2]


def relative(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise TrainingError("round paths must be nonempty repository-relative paths")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value or value == ".":
        raise TrainingError("round paths must remain inside the repository")
    return path


def selected(root, directory, revision):
    best = root / directory / "best.json"
    if not best.exists():
        return None
    report = json.loads(best.read_text(encoding="utf-8"))
    step = report.get("step")
    if type(step) is not int or step < 0:
        raise TrainingError("best.json has no nonnegative selected step")
    path = directory / ("adapter-%d" % step)
    adapter_identity(root / path, revision)
    return path.as_posix()


def plan(config, root=ROOT):
    if set(config) != {"schema", "revision", "engine", "round_dir", "seed", "steps", "eval_every", "rank", "generations", "eval_draws", "max_new_tokens"} or config["schema"] != 1:
        raise TrainingError("unknown or missing round configuration fields")
    revision = config["revision"]
    if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise TrainingError("replace revision with the approved immutable Qwen Hub commit")
    for key in ("seed", "steps", "eval_every", "rank", "generations", "eval_draws", "max_new_tokens"):
        if type(config[key]) is not int or config[key] <= 0:
            raise TrainingError(key + " must be a positive integer")
    if config["generations"] < 2:
        raise TrainingError("probe/GRPO need at least two draws")
    directory, engine = relative(config["round_dir"]), relative(config["engine"])
    root = Path(root)
    adapter_sft = selected(root, directory / "sft", revision)
    adapter_grpo = selected(root, directory / "grpo", revision)
    common = ["--engine", engine.as_posix(), "--revision", revision, "--isolated-worker",
              "--seed", str(config["seed"]), "--rank", str(config["rank"]),
              "--generations", str(config["generations"]), "--eval-draws", str(config["eval_draws"]),
              "--max-new-tokens", str(config["max_new_tokens"])]
    actions = []
    def action(name, stage, extra=(), prerequisites=(), smoke=False):
        bundle = directory / ("smoke" if smoke else "pilot") / "bundle.json"
        output = directory / name
        required = [engine.as_posix(), bundle.as_posix()] + list(prerequisites)
        missing = [p for p in required if not (root / p).exists()]
        cmd = ["uv", "run", "--python", "3.12", "nemotron/gpu/train_verified.py", stage,
               "--bundle", bundle.as_posix(), "--output", output.as_posix()] + common + list(extra)
        if smoke:
            cmd.append("--smoke")
        actions.append({"name": name, "stage": stage, "argv": cmd,
                        "shell": "PYTHONPATH=src:nemotron LYPNING_CAPTURE=0 LYPNING_HARVEST=0 " + shlex.join(cmd),
                        "missing": missing, "output_exists": (root / output).exists(),
                        "operator_gate": "review prerequisites, isolation, budget and previous stage before manual launch"})
    action("sft-smoke", "sft", smoke=True)
    action("grpo-smoke", "grpo", ["--from-base"], smoke=True)
    smoke_done = [(directory / name / "best.json").as_posix() for name in ("sft-smoke", "grpo-smoke")]
    action("base-dev", "eval", prerequisites=smoke_done)
    steps = ["--steps", str(config["steps"]), "--eval-every", str(config["eval_every"]), "--patience", "3"]
    action("sft", "sft", steps, [(directory / "base-dev/metrics.json").as_posix()])
    if adapter_sft:
        action("sft-dev-reload", "eval", ["--adapter", adapter_sft])
        action("probe", "probe", ["--adapter", adapter_sft], [(directory / "sft-dev-reload/metrics.json").as_posix()])
        action("grpo", "grpo", steps + ["--adapter", adapter_sft, "--probe", (directory / "probe/probe.json").as_posix()],
               [(directory / "probe/probe.json").as_posix()])
    # Test commands appear only after dev has selected both sealed adapters.
    # Their presence is still NOT approval to open the holdout repeatedly.
    if adapter_sft and adapter_grpo:
        action("base-test", "eval", ["--eval-split", "test"])
        action("sft-test", "eval", ["--eval-split", "test", "--adapter", adapter_sft])
        action("grpo-test", "eval", ["--eval-split", "test", "--adapter", adapter_grpo])
    result = {"schema": 1, "base_model": BASE_MODEL, "config": config,
              "config_sha256": sha256_of(config), "code_sha256": source_identity(root / "nemotron"),
              "training_started": False, "quality_claim": None,
              "actions": actions, "pending_selection": [n for n, a in (("sft", adapter_sft), ("grpo", adapter_grpo)) if not a],
              "policy": "correct compatible first drafts; no per-program speed gate"}
    result["digest"] = sha256_of(result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="new plan JSON; otherwise stdout only")
    args = parser.parse_args(argv)
    try:
        if args.output and args.output.exists():
            raise TrainingError("plan output exists; keep prior evidence and choose a new file")
        result = plan(json.loads(args.config.read_text(encoding="utf-8")))
        if args.output:
            write_json(args.output, result)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print("round plan blocked: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
