from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("recipe_gpu", ROOT / "gpu/train_verified.py")
gpu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gpu)


def args(stage, *extra):
    return gpu.parser().parse_args([stage, "--bundle", "/bundle", "--engine", "/engine",
        "--output", "/new", "--revision", "a" * 40] + list(extra))


@pytest.mark.parametrize("stage,steps,want", [("sft", 99, 2e-4), ("sft", 100, 1e-4),
    ("sft", 300, 1e-4), ("grpo", 20, 5e-6), ("grpo", 500, 5e-6)])
def test_registered_lora_recipe_and_override(stage, steps, want):
    parsed = args(stage, "--steps", str(steps))
    assert gpu.schedule(parsed)["learning_rate"] == want
    assert gpu.schedule(parsed)["eval_every"] == 50
    parsed.lr = 3e-5
    assert gpu.schedule(parsed)["learning_rate"] == 3e-5


def test_smoke_uses_its_effective_dose_and_evaluates_every_step():
    parsed = args("sft", "--smoke", "--steps", "300")
    schedule = gpu.schedule(parsed)
    assert schedule == dict(steps=2, eval_every=1, max_tokens=32, learning_rate=2e-4)


def test_pilot_uses_shared_cadence_and_keeps_small_effective_batch():
    import re
    text = (ROOT / "hf/round02_pilot.sh").read_text()
    for stage in ("SFT", "GRPO"):
        flags = re.search(stage + r"_TRAIN=\(([^\n]+)\)", text).group(1)
        assert "--eval-every 50" in flags
        assert "--lr" not in flags  # one recipe, resolved by the trainer
    assert '--batch-size 4' in text
