"""Exercise evaluation state restoration with a tiny fake runtime, not a GPU."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import importlib.util
import json
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
    """`generate`'s output: rows of prompt + tail, indexed `[row, start:]`."""
    def __init__(self, rows):
        self.rows = rows

    def __getitem__(self, key):
        row, span = key
        return Tokens([self.rows[row][span]])

    def tolist(self):
        return list(self.rows[0])


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
    padding_side = "right"

    def apply_chat_template(self, messages, **kwargs):
        assert kwargs["enable_thinking"] is False
        return "prompt " + messages[-1]["content"]

    def __call__(self, texts, **kwargs):
        assert isinstance(texts, list) and kwargs["padding"] is True and self.padding_side == "left"
        self.batches = getattr(self, "batches", []) + [len(texts)]
        return Batch(input_ids=SimpleNamespace(shape=(len(texts), 3)))

    def decode(self, tail, **kwargs):
        return "```python\nprint(%s)\n```" % ",".join(str(t) for t in tail if t != 99)


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
        n = kwargs["input_ids"].shape[0] * kwargs["generation_config"].num_return_sequences
        return Tokens([[7, 7, 7] + list(self.tail) for _ in range(n)])


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
    # One chunk, one generate call per evaluation; the same seed at step 0 and 10.
    assert len(model.seeds) == 2 and model.seeds[0] == model.seeds[1]
    assert Tokenizer.padding_side == "right", "the trainer's padding side is restored"


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



def test_chunks_pair_across_arms_and_padding_is_not_generation(tmp_path, monkeypatch):
    """Three cases at two draws with room for two sequences per call: three
    chunks, each under its own seed, the same three seeds in the other arm.
    A batched tail ends at its first EOS; the padding behind it is not output."""
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(GenerationConfig=SimpleNamespace))
    ev, torch = load_evaluation(), FakeTorch()
    seen = []
    verifier = SimpleNamespace(score=lambda c, p: seen.append((c["case_id"], p)) or Score(1, "correct-native", 3, 3))
    cases = [dict(case_id=str(i), family="f", task="t%d" % i, population="coverage") for i in range(3)]
    arms = []
    for arm in ("base", "candidate"):
        model, tok = Model(torch, [1, 2, 99, 0, 0]), Tokenizer()
        _, records = ev.evaluate(model, tok, cases, verifier, decoding(10), tmp_path / (arm + ".jsonl"),
                                 0, torch, seed=7, draws=2, return_records=True, sequences_per_call=2, score_workers=3)
        assert tok.batches == [1, 1, 1] and len(model.seeds) == 3 and len(set(model.seeds)) == 3
        arms.append((model.seeds, [(r["case_id"], r["draw"], r["seed"]) for r in records]))
        assert [r["completion_tokens"] for r in records] == [3] * 6, "cut at EOS, padding dropped"
        assert all(r["truncated"] is False and r["correct"] for r in records)
    assert arms[0] == arms[1], "the candidate arm draws the base arm's noise, chunk for chunk"
    assert [c for c, _ in seen] == ["0", "0", "1", "1", "2", "2"] * 2, "scored in (case, draw) order"
    assert ev.chunked(cases, draws=16, sequences_per_call=64) == [cases[:3]] and \
        ev.chunked(cases, draws=100, sequences_per_call=64) == [[c] for c in cases], "at least one case per call"
    assert ev.trim([1, 2], 99, 0) == [1, 2] and ev.trim([1, 0, 0], 99, 0) == [1] and ev.trim([99, 99], 99, 99) == [99]


def test_a_blocked_evaluation_preserves_the_program_and_still_aborts(tmp_path, monkeypatch):
    """The uncontroversial half of ASSESSMENT.md §6 step 2.

    Round-02's base arm blocked on a native timeout after a correct oracle and
    left only the exception string: the program was nowhere in the artifacts, so
    neither ruling Codex is owed on ledger row T4 — fault, or a scored
    `not-native` with a witness — could be taken from the evidence. The witness
    closes that, and the abort is unchanged: this test pins BOTH halves, because
    a witness that swallowed the raise would be the gate change nobody approved.
    """
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(GenerationConfig=SimpleNamespace))
    ev, torch = load_evaluation(), FakeTorch()
    model = Model(torch, [1, 99])
    from pipeline.training_types import VerificationBlocked

    raised = []

    def block(case, program):
        exc = VerificationBlocked("engine mismatch: " + program)
        raised.append(exc)
        raise exc

    cases = [dict(case_id="c", family="f", task="task", population="coverage",
                  split_group="g", tests=[{"stdout": "1"}])]
    witness = tmp_path / "eval-blocked-witnesses.jsonl"
    with pytest.raises(VerificationBlocked, match="engine mismatch") as caught:
        ev.evaluate(model, Tokenizer(), cases, SimpleNamespace(score=block), decoding(10),
                    tmp_path / "eval.jsonl", 7, torch, witness_path=witness)
    # The ORIGINAL exception, not a new one of the same class: a witness that
    # re-raised its own would discard the message the abort exists to carry.
    assert any(caught.value is exc for exc in raised)
    rows = [json.loads(line) for line in witness.read_text().splitlines() if line.strip()]
    assert rows, "the program that blocked the arm must survive the abort"
    assert rows[0]["case_id"] == "c" and rows[0]["step"] == 7
    assert rows[0]["split_group"] == "g" and rows[0]["tests"] == [{"stdout": "1"}]
    assert rows[0]["program"] and rows[0]["program"] in rows[0]["error"]
    # The arm still aborts and the trainer's state is still restored.
    assert model.training and model.is_gradient_checkpointing and torch.state == 123


def test_a_witnessless_evaluation_behaves_exactly_as_before(tmp_path, monkeypatch):
    """No witness path, no new file, same raise — the default is unchanged."""
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(GenerationConfig=SimpleNamespace))
    ev, torch = load_evaluation(), FakeTorch()
    model = Model(torch, [1, 99])
    from pipeline.training_types import VerificationBlocked

    def block(case, program):
        raise VerificationBlocked("engine mismatch")

    cases = [dict(case_id="c", family="f", task="task", population="coverage")]
    with pytest.raises(VerificationBlocked):
        ev.evaluate(model, Tokenizer(), cases, SimpleNamespace(score=block), decoding(10),
                    tmp_path / "eval.jsonl", 0, torch)
    assert list(tmp_path.glob("*witness*")) == []


def test_a_clean_evaluation_writes_no_witness(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(GenerationConfig=SimpleNamespace))
    ev, torch = load_evaluation(), FakeTorch()
    model = Model(torch, [1, 99])
    verifier = SimpleNamespace(score=lambda c, p: Score(1, "correct-native", 3, 3))
    cases = [dict(case_id="c", family="f", task="task", population="coverage")]
    witness = tmp_path / "eval-blocked-witnesses.jsonl"
    ev.evaluate(model, Tokenizer(), cases, verifier, decoding(10),
                tmp_path / "eval.jsonl", 0, torch, witness_path=witness)
    assert not witness.exists()


def test_a_case_without_tests_does_not_lose_the_abort(tmp_path, monkeypatch):
    """A KeyError in the witness would REPLACE the exception it documents.

    `Verifier.score` can block before it reads `tests` — a runner failure, a
    harness error, engine-identity drift — so a case reaching the witness
    without one is exactly the path the witness was built for. Reading
    `case["tests"]` there turned `engine mismatch: ...` into `KeyError: 'tests'`,
    and `train_verified.main` catches KeyError too, so the run still exited 1
    with the evidence gone.
    """
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(GenerationConfig=SimpleNamespace))
    ev, torch = load_evaluation(), FakeTorch()
    model = Model(torch, [1, 99])
    from pipeline.training_types import VerificationBlocked

    def block(case, program):
        raise VerificationBlocked("engine mismatch: identity drift")

    cases = [dict(case_id="c", family="f", task="task", population="coverage")]
    witness = tmp_path / "eval-blocked-witnesses.jsonl"
    with pytest.raises(VerificationBlocked, match="identity drift"):
        ev.evaluate(model, Tokenizer(), cases, SimpleNamespace(score=block), decoding(10),
                    tmp_path / "eval.jsonl", 0, torch, witness_path=witness)
    rows = [json.loads(line) for line in witness.read_text().splitlines() if line.strip()]
    assert rows and rows[0]["tests"] is None and rows[0]["case_id"] == "c"


def test_the_runner_asks_for_a_witness_at_every_evaluation_call_site():
    """The witness is worthless if the trainer never requests it.

    Deleting both `witness_path=` arguments in `train_verified.py` left the whole
    suite green, so nothing would have noticed the real trainer silently writing
    no witness at all. This reads the script, which is how `test_training.py`
    already pins source-level contracts.
    """
    source = (Path(__file__).resolve().parents[1] / "gpu" / "train_verified.py").read_text()
    calls = source.count("evaluate(model, tok,")
    assert calls == 2, "call sites moved; re-check that each still asks for a witness"
    assert source.count('witness_path=args.output / "eval-blocked-witnesses.jsonl"') == calls


def test_concurrent_witness_rows_stay_parseable(tmp_path):
    """Two writes per row let threads interleave a body and its newline.

    A witness row carries a program and its tests, so it is routinely over the
    8 KiB text buffer — which is exactly when the body and the "\n" became
    separate syscalls and 16 workers could shred the file.
    """
    from concurrent.futures import ThreadPoolExecutor
    from pipeline.jsonio import append_jsonl

    path = tmp_path / "w.jsonl"
    payload = "x" * 20000

    def write(i):
        for _ in range(20):
            append_jsonl(path, {"i": i, "program": payload})

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(write, range(16)))
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    assert len(lines) == 320
    for line in lines:
        json.loads(line)
