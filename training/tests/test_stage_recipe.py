"""The optimizer stages' recipe, pinned without a GPU (S4 preparation, 2026-09-22).

Each test here pins one decision taken before arm A's first seed, because
`code_sha256` covers `training/gpu` and a change between seeds would split one
arm into two: the SFT schedule draws within a family WITHOUT replacement, SFT
logs its pre-clip gradient norm, GRPO steps four prompts of eight draws with an
explicit clip and writes its log history, and the GRPO dataset can be cut to the
probe's informative cases.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from pipeline.curriculum import starter_cases
from pipeline.training import Score
from pipeline.training_contract import decoding
from pipeline.training_types import TrainingError


def stages():
    path = Path(__file__).resolve().parents[1] / "gpu" / "verified_stages.py"
    spec = importlib.util.spec_from_file_location("stage_recipe_runtime", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_family_is_drawn_without_replacement_until_it_is_exhausted():
    """`rng.choice` saw about 63% of a family's cases per pass; a queue sees all."""
    module = stages()
    cases = [{"family": "big"} for _ in range(10)] + [{"family": "small"} for _ in range(3)]
    examples = [{"i": i, "labels": [-100, 1]} for i in range(13)]
    batches = module.sft_batches(cases, examples, steps=10, batch_size=2, seed=1111)
    drawn = [example["i"] for batch in batches for example in batch]
    big = [i for i in drawn if i < 10]
    small = [i for i in drawn if i >= 10]
    assert sorted(big) == list(range(10)), "ten draws from a family of ten see every case once"
    # Three per pass: the first three and the next three are each a permutation.
    assert sorted(small[:3]) == [10, 11, 12] and sorted(small[3:6]) == [10, 11, 12]
    # Deterministic, and seed-dependent.
    assert batches == module.sft_batches(cases, examples, steps=10, batch_size=2, seed=1111)
    assert batches != module.sft_batches(cases, examples, steps=10, batch_size=2, seed=2222)


def test_the_schedule_depends_on_families_not_on_what_it_carries():
    """`supervised_plan` plans with cases where `run()` trains on examples."""
    module = stages()
    cases = [{"family": f} for f in "aabbbc"]
    by_index = module.sft_batches(cases, list(range(6)), steps=5, batch_size=3, seed=7)
    by_value = module.sft_batches(cases, [{"x": i} for i in range(6)], steps=5, batch_size=3, seed=7)
    assert [[v["x"] for v in b] for b in by_value] == by_index


def _rollouts(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_the_grpo_dataset_can_be_cut_to_the_probes_informative_cases(tmp_path):
    """0 < p < 1 by `probe_report`'s own rule: variation among non-truncated draws."""
    module = stages()
    cases = [{"case_id": c} for c in ("mixed", "all-pass", "all-fail", "truncated-only", "absent")]
    path = _rollouts(tmp_path / "probe-rollouts.jsonl", [
        {"case_id": "mixed", "reward": 1, "truncated": False},
        {"case_id": "mixed", "reward": 0, "truncated": False},
        {"case_id": "all-pass", "reward": 1, "truncated": False},
        {"case_id": "all-pass", "reward": 1, "truncated": False},
        {"case_id": "all-fail", "reward": 0, "truncated": False},
        {"case_id": "all-fail", "reward": 0, "truncated": False},
        # A truncated draw is masked from the loss, so its zero is no variation.
        {"case_id": "truncated-only", "reward": 1, "truncated": False},
        {"case_id": "truncated-only", "reward": 0, "truncated": True},
    ])
    assert [c["case_id"] for c in module.informative_cases(path, cases)] == ["mixed"]
    none = _rollouts(tmp_path / "none.jsonl", [{"case_id": "all-pass", "reward": 1, "truncated": False}])
    with pytest.raises(TrainingError, match="no informative"):
        module.informative_cases(none, cases)


class _Tensor:
    def __init__(self, value):
        self.value = value

    def __mul__(self, other):
        return _Tensor(self.value * other)

    __rmul__ = __mul__

    def __truediv__(self, other):
        return _Tensor(self.value / other)

    def backward(self):
        pass

    def detach(self):
        return self

    def __float__(self):
        return float(self.value)


def test_sft_steps_the_declared_optimizer_and_logs_its_gradient_norm(tmp_path):
    """The pre-clip norm was computed every step and thrown away."""
    module = stages()
    seen = {}

    class AdamW:
        def __init__(self, params, **kwargs):
            seen["optimizer"] = kwargs
            self.param_groups = [{}]

        def zero_grad(self, set_to_none=True):
            pass

        def step(self):
            pass

    def clip(params, norm, **kwargs):
        seen["clip"] = (norm, kwargs)
        return _Tensor(0.25)

    torch = SimpleNamespace(optim=SimpleNamespace(AdamW=AdamW), isfinite=lambda t: True,
                            nn=SimpleNamespace(utils=SimpleNamespace(clip_grad_norm_=clip)))
    lora_b = SimpleNamespace(requires_grad=True, grad=SimpleNamespace(abs=lambda: SimpleNamespace(sum=lambda: 1)))

    class Model:
        device = "cpu"

        def parameters(self):
            return [lora_b]

        def named_parameters(self):
            return [("layer.q_proj.lora_B.default.weight", lora_b)]

        def __call__(self, **inputs):
            return SimpleNamespace(loss=_Tensor(2.0))

    core = SimpleNamespace(set_train_mode=lambda m: None, collate=lambda b, pad, dev: {})
    example = {"labels": [-100, 5, 6]}
    args = SimpleNamespace(output=tmp_path, batch_size=1, seed=1, warmup_ratio=0.0)
    module.train_sft(Model(), SimpleNamespace(pad_token_id=0), args, [{"family": "f"}], [example],
                     core, torch, {"steps": 1, "eval_every": 1, "learning_rate": 1e-4},
                     lambda step: None)
    assert seen["optimizer"] == dict(module.SFT_OPTIMIZER, lr=1e-4)
    assert seen["clip"] == (1.0, {"error_if_nonfinite": True})
    row = json.loads((tmp_path / "loss.jsonl").read_text())
    assert row["grad_norm"] == 0.25 and row["step"] == 1


def _grpo(tmp_path, monkeypatch, callback_steps, smoke=True):
    module = stages()
    observed = {}

    class CapturingReward(module.Reward):
        def __init__(self, *a, **kwargs):
            observed["reward"] = kwargs
            super().__init__(*a, **kwargs)
    monkeypatch.setattr(module, "Reward", CapturingReward)

    class Trainer:
        def __init__(self, **kwargs):
            observed.update(kwargs)
            self.chat_template = "template"
            self.chat_template_kwargs = kwargs["args"].chat_template_kwargs

        def train(self):
            callback = observed["callbacks"][0]
            control = SimpleNamespace()
            callback.on_pre_optimizer_step(None, SimpleNamespace(global_step=1), control)
            for step, logs in callback_steps:
                callback.on_log(None, SimpleNamespace(global_step=step), control, logs=logs)

    def clip(params, norm, **kwargs):
        observed["clip"] = (norm, kwargs)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(nn=SimpleNamespace(
        utils=SimpleNamespace(clip_grad_norm_=clip))))
    monkeypatch.setitem(sys.modules, "datasets", SimpleNamespace(Dataset=SimpleNamespace(from_list=lambda x: x)))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(TrainerCallback=object))
    monkeypatch.setitem(sys.modules, "trl", SimpleNamespace(GRPOConfig=SimpleNamespace, GRPOTrainer=Trainer))
    case = dict(starter_cases()[0], split="train")
    args = SimpleNamespace(output=tmp_path, seed=42, generations=8, grpo_prompts=4, warmup_ratio=.1,
                           smoke=smoke, max_no_signal=20, score_workers=2)
    model = SimpleNamespace(device=SimpleNamespace(type="cpu"), parameters=lambda: [])
    tok = SimpleNamespace(eos_token_id=99, apply_chat_template=lambda msgs, **kwargs: msgs[-1]["content"])
    verifier = SimpleNamespace(score=lambda c, p: Score(1, "correct-native", 3, 3))
    module.train_grpo(model, tok, args, {"cases": [case]}, [case], verifier,
        {"steps": 2, "eval_every": 50, "max_tokens": 32, "learning_rate": 1e-5}, decoding(32),
        lambda step: None)
    return observed


def test_a_grpo_step_is_four_prompts_of_eight_with_an_explicit_clip(tmp_path, monkeypatch):
    """Seed 1111 stepped on ONE group of 4; the loss shape itself is unchanged."""
    observed = _grpo(tmp_path, monkeypatch, [])
    config = observed["args"]
    assert config.num_generations == 8
    assert config.per_device_train_batch_size * config.gradient_accumulation_steps == 32
    assert config.max_grad_norm == 1.0
    assert config.learning_rate == 1e-5
    assert config.loss_type == "dr_grpo" and config.scale_rewards == "none" and config.beta == 0
    assert type(observed["callbacks"][0]).__name__ == "CheckpointCallback"
    # The callback no longer clips a second time: a norm taken at +inf changes
    # nothing and still refuses a NaN.
    norm, kwargs = observed["clip"]
    assert norm == float("inf") and kwargs == {"error_if_nonfinite": True}


def test_grpo_writes_its_log_history_to_loss_jsonl(tmp_path, monkeypatch):
    """`save_strategy="no"` left GRPO with no per-step record; numbers only."""
    _grpo(tmp_path, monkeypatch, [
        (1, {"loss": 0.5, "grad_norm": 0.1, "frac_reward_zero_std": 0.25,
             "clip_ratio/region_mean": 0.0, "learning_rate": 1e-5, "note": "case text", "flag": True}),
        (2, {}),
        (2, {"loss": 0.4}),
        # The trainer's end-of-run summary is not a step.
        (2, {"train_loss": 0.45, "train_runtime": 12.0, "epoch": 1.0}),
    ])
    rows = [json.loads(line) for line in (tmp_path / "loss.jsonl").read_text().splitlines()]
    assert rows == [{"clip_ratio/region_mean": 0.0, "frac_reward_zero_std": 0.25, "grad_norm": 0.1,
                     "learning_rate": 1e-5, "loss": 0.5, "step": 1},
                    {"loss": 0.4, "step": 2}]


def test_the_no_signal_abort_still_counts_optimizer_steps(tmp_path, monkeypatch):
    """`Reward` counts consecutive uninformative GROUPS, and a step now has four.

    Seed 1111 stepped one group at a time, so --max-no-signal 20 meant twenty
    zero-advantage steps. Passed through unscaled at four prompts a step it
    would mean five, and on a bank where most probe groups carry no signal
    (`reports/2026-09-21-fable-round02-seed1111-read.md` §4) a run of twenty
    uninformative groups is far likelier than a run of eighty -- an abort that
    ends a paid run after the SFT, the probe and the weight pull on noise.
    """
    observed = _grpo(tmp_path, monkeypatch, [], smoke=False)
    assert observed["reward"]["max_no_signal"] == 20 * 4
    assert observed["reward"]["generations"] == 8
    assert _grpo(tmp_path / "s", monkeypatch, [])["reward"]["max_no_signal"] == 0, "smoke never aborts"
