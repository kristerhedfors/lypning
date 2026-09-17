"""Exercise GRPO wiring with protocol doubles; the real GPU smoke remains required."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

from pipeline.curriculum import starter_cases
from pipeline.training import Score
from pipeline.training_contract import decoding


def test_sft_schedule_covers_each_family_before_repeating_and_is_deterministic():
    path = Path(__file__).resolve().parents[1] / "gpu" / "verified_stages.py"
    spec = importlib.util.spec_from_file_location("test_sft_schedule_runtime", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = [{"family": family} for family in ("a", "a", "b", "c")]
    examples = [{"name": str(i), "labels": [-100, i, i]} for i in range(4)]
    first = module.sft_batches(cases, examples, steps=2, batch_size=2, seed=7)
    second = module.sft_batches(cases, examples, steps=2, batch_size=2, seed=7)
    assert first == second
    family_of = {id(example): case["family"] for case, example in zip(cases, examples)}
    scheduled = [family_of[id(example)] for batch in first for example in batch]
    assert set(scheduled[:3]) == {"a", "b", "c"}, "one complete family cycle precedes repeats"
    assert module.supervised_tokens(first) == 8


def test_grpo_stage_uses_frozen_policy_and_callbacks(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "gpu" / "verified_stages.py"
    spec = importlib.util.spec_from_file_location("test_stages_runtime", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    observed = {}
    class Trainer:
        def __init__(self, **kwargs):
            observed.update(kwargs)
            self.chat_template = "template"
            self.chat_template_kwargs = kwargs["args"].chat_template_kwargs

        def train(self):
            callback = observed["callbacks"][0]
            state, control = SimpleNamespace(global_step=1), SimpleNamespace()
            callback.on_pre_optimizer_step(None, state, control)
            callback.on_step_end(None, state, control)
            assert not control.should_training_stop
            cid = observed["train_dataset"][0]["case_id"]
            rewards = observed["reward_funcs"](["```python\npass\n```", "bad"], [cid, cid],
                                                completion_ids=[[1, 99], [99]])
            assert rewards == [1, 0]
    def clip(params, norm, **kwargs):
        assert kwargs["error_if_nonfinite"] is True
        observed["finite_checked"] = True
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(nn=SimpleNamespace(
        utils=SimpleNamespace(clip_grad_norm_=clip))))
    monkeypatch.setitem(sys.modules, "datasets", SimpleNamespace(Dataset=SimpleNamespace(from_list=lambda x: x)))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(TrainerCallback=object))
    monkeypatch.setitem(sys.modules, "trl", SimpleNamespace(GRPOConfig=SimpleNamespace, GRPOTrainer=Trainer))
    case = dict(starter_cases()[0], split="train")
    args = SimpleNamespace(output=tmp_path, seed=42, generations=2, warmup_ratio=.1, smoke=True, max_no_signal=20,
                           score_workers=2)
    model = SimpleNamespace(device=SimpleNamespace(type="cpu"), parameters=lambda: [])
    tok = SimpleNamespace(eos_token_id=99, apply_chat_template=lambda msgs, **kwargs: msgs[-1]["content"])
    verifier = SimpleNamespace(score=lambda c, p: Score(1, "correct-native", 3, 3) if p else Score(0, "no-code"))
    steps = []
    module.train_grpo(model, tok, args, {"cases": [case]}, [case], verifier,
        {"steps": 2, "eval_every": 1, "max_tokens": 32, "learning_rate": 1e-6}, decoding(32),
        lambda step: steps.append(step) or False)
    config = observed["args"]
    assert config.num_generations == config.gradient_accumulation_steps == 2
    assert (config.temperature, config.top_p, config.top_k) == (.7, .8, 20)
    assert config.loss_type == "dr_grpo" and config.scale_rewards == "none" and config.beta == 0
    assert config.generation_kwargs["eos_token_id"] == 99
    assert observed["finite_checked"] and steps == [1]
