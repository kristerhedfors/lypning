"""Exercise evaluation state restoration with a tiny fake runtime, not a GPU."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from pipeline.training import Score
from pipeline.training_contract import decoding


def load_evaluation():
    path = Path(__file__).resolve().parents[1] / "gpu" / "verified_evaluation.py"
    spec = importlib.util.spec_from_file_location("test_eval_runtime", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Batch(dict):
    def to(self, device):
        return self


class Tokens:
    def __init__(self, values):
        self.values = values

    def __getitem__(self, key):
        return self

    def tolist(self):
        return self.values


class FakeTorch:
    def __init__(self):
        self.state = 123
        self.random = SimpleNamespace(fork_rng=self.fork_rng)

    @contextmanager
    def fork_rng(self):
        before = self.state
        try:
            yield
        finally:
            self.state = before

    def manual_seed(self, seed):
        self.state = seed

    def no_grad(self):
        return nullcontext()


class Tokenizer:
    eos_token_id, pad_token_id, bos_token_id = 99, 0, None

    def apply_chat_template(self, messages, **kwargs):
        assert kwargs["enable_thinking"] is False
        return "prompt"

    def __call__(self, *args, **kwargs):
        return Batch(input_ids=SimpleNamespace(shape=(1, 3)))

    def decode(self, *args, **kwargs):
        return "```python\nprint(1)\n```"


class Model:
    def __init__(self, torch, tail):
        self.torch, self.tail = torch, tail
        self.training, self.is_gradient_checkpointing = True, True
        self.config = SimpleNamespace(text_config=SimpleNamespace(use_cache=False))
        self.generation_config = SimpleNamespace(eos_token_id=[98, 99])
        self.device = "cpu"
        self.seeds = []

    def gradient_checkpointing_disable(self):
        self.is_gradient_checkpointing = False

    def gradient_checkpointing_enable(self, **kwargs):
        self.is_gradient_checkpointing = True

    def eval(self):
        self.training = False

    def train(self, flag):
        self.training = flag

    def generate(self, **kwargs):
        assert kwargs["generation_config"].eos_token_id == 99
        self.seeds.append(self.torch.state)
        self.torch.state += 1
        return Tokens(self.tail)


@pytest.mark.parametrize("tail", [[1, 99], [], [1, 2]])
def test_matched_seeds_state_restore_and_truncation(tmp_path, monkeypatch, tail):
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(GenerationConfig=SimpleNamespace))
    ev, torch = load_evaluation(), FakeTorch()
    model = Model(torch, tail)
    verifier = SimpleNamespace(score=lambda c, p: Score(1, "correct-native", 3, 3) if p else Score(0, "no-code"))
    cases = [dict(case_id="c", family="f", task="task", population="coverage")]
    for step in (0, 10):
        metrics = ev.evaluate(model, Tokenizer(), cases, verifier, decoding(10),
                              tmp_path / "eval.jsonl", step, torch, seed=42, draws=2)
        assert model.training and model.is_gradient_checkpointing
        assert model.config.text_config.use_cache is False and torch.state == 123
        assert model.generation_config.eos_token_id == [98, 99]
        assert metrics["correct"] == (1 if tail == [1, 99] else 0)
    assert model.seeds[:2] == model.seeds[2:]


def test_verifier_failure_restores_training_state(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(GenerationConfig=SimpleNamespace))
    ev, torch = load_evaluation(), FakeTorch()
    model = Model(torch, [1, 99])
    def fail(*args):
        raise RuntimeError("oracle failed")
    cases = [dict(case_id="c", family="f", task="task", population="coverage")]
    with pytest.raises(RuntimeError, match="oracle failed"):
        ev.evaluate(model, Tokenizer(), cases, SimpleNamespace(score=fail), decoding(10),
                    tmp_path / "eval.jsonl", 0, torch)
    assert model.training and model.is_gradient_checkpointing and torch.state == 123
    assert model.config.text_config.use_cache is False


def test_whole_bank_evaluation_keeps_the_row_schema_across_splits(tmp_path, monkeypatch):
    # A benchmark bundle is measured whole: one row per case and draw whatever
    # the split, with the same keys, so training_report compares two whole-bank
    # evaluations exactly as it compares two dev evaluations.
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(GenerationConfig=SimpleNamespace))
    ev, torch = load_evaluation(), FakeTorch()
    model = Model(torch, [1, 99])
    verifier = SimpleNamespace(score=lambda c, p: Score(1, "correct-native", 3, 3))
    cases = [dict(case_id=str(i), family="f%d" % i, task="task %d" % i, population="coverage",
                  split=split, split_group="g%d" % i)
             for i, split in enumerate(("train", "dev", "test"))]
    metrics, records = ev.evaluate(model, Tokenizer(), cases, verifier, decoding(10),
                                   tmp_path / "eval.jsonl", 0, torch, seed=42, draws=2, return_records=True)
    assert [r["case_id"] for r in records] == ["0", "0", "1", "1", "2", "2"]
    assert len({tuple(sorted(r)) for r in records}) == 1
    assert {r["split_group"] for r in records} == {"g0", "g1", "g2"}
    assert len((tmp_path / "eval.jsonl").read_text().splitlines()) == 6
    assert metrics["correct"] == 1

