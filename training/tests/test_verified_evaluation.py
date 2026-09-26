"""Exercise evaluation state restoration with a tiny fake runtime, not a GPU."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
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


#: `Verifier._observed` of a native run that timed out after a correct oracle.
NATIVE_TIMEOUT = [None, "", "", True, False, False, False]


def test_a_blocked_evaluation_preserves_the_program_and_still_aborts(tmp_path, monkeypatch):
    """The uncontroversial half of ASSESSMENT.md §6 step 2.

    Round-02's base arm blocked on a native timeout after a correct oracle and
    left only the exception string: the program was nowhere in the artifacts, so
    neither ruling Codex is owed on ledger row T4 — fault, or a scored
    `not-native` with a witness — could be taken from the evidence. The witness
    closes that, and the abort is unchanged: this test pins BOTH halves, because
    a witness that swallowed the raise would be the gate change nobody approved.
    T4 kept the timeout a hard abort (2026-09-17), and the 2026-09-24 policy
    that COUNTS other engine mismatches leaves it one (`mismatch_policy`).
    """
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(GenerationConfig=SimpleNamespace))
    ev, torch = load_evaluation(), FakeTorch()
    model = Model(torch, [1, 99])
    from pipeline.training_types import VerificationBlocked

    raised = []

    def block(case, program):
        exc = VerificationBlocked("engine mismatch", {"program": program, "observed": NATIVE_TIMEOUT})
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
    # The detail is on the private row's witness; the message names only a
    # kind and a digest, because it reaches the public GPU log (2026-09-23).
    assert rows[0]["program"] and rows[0]["program"] not in rows[0]["error"]
    assert rows[0]["kind"] == "engine mismatch"
    assert rows[0]["witness"] == {"program": rows[0]["program"], "observed": NATIVE_TIMEOUT}
    assert not (tmp_path / "eval.jsonl").exists(), "an abort, not a counted draw"
    # The arm still aborts and the trainer's state is still restored.
    assert model.training and model.is_gradient_checkpointing and torch.state == 123


def test_a_blocked_draw_keeps_successful_siblings_but_the_arm_still_aborts(tmp_path, monkeypatch):
    """Chunk durability is not arm completion: keep successes, re-raise the block."""
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(GenerationConfig=SimpleNamespace))
    ev, torch = load_evaluation(), FakeTorch()
    model = Model(torch, [1, 99])
    from pipeline.training_types import VerificationBlocked

    blocked = VerificationBlocked("native timeout after correct oracle")

    def score(case, program):
        if case["case_id"] == "blocked":
            raise blocked
        return Score(1, "correct-native", 3, 3)

    cases = [dict(case_id="blocked", family="f0", task="t0", population="coverage"),
             dict(case_id="kept", family="f1", task="t1", population="coverage")]
    output = tmp_path / "eval.jsonl"
    witness = tmp_path / "witness.jsonl"
    with pytest.raises(VerificationBlocked) as caught:
        ev.evaluate(model, Tokenizer(), cases, SimpleNamespace(score=score), decoding(10),
                    output, 0, torch, draws=1, witness_path=witness, score_workers=2)
    assert caught.value is blocked
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert [row["case_id"] for row in rows] == ["kept"]
    assert json.loads(witness.read_text())["case_id"] == "blocked"


def test_a_witnessless_evaluation_behaves_exactly_as_before(tmp_path, monkeypatch):
    """No witness path, no new file, same raise — the default is unchanged."""
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(GenerationConfig=SimpleNamespace))
    ev, torch = load_evaluation(), FakeTorch()
    model = Model(torch, [1, 99])
    from pipeline.training_types import VerificationBlocked

    def block(case, program):
        raise VerificationBlocked("harness")

    cases = [dict(case_id="c", family="f", task="task", population="coverage")]
    with pytest.raises(VerificationBlocked, match="^harness$"):
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
    # And for the counted engine-mismatch draws' witnesses (2026-09-24).
    assert source.count("mismatch_path=args.output / ENGINE_MISMATCH_FILE") == calls


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


# --- scoring overlapped with the next chunk's generation --------------------------

class SeededModel(Model):
    """A model whose completions depend on the torch RNG state it generates under.

    So a scoring thread that consumed RNG, or a chunk generated under another
    state, would change the programs and hence the bytes compared below.
    `hook(call)` runs at the start of each `generate`; `fail_at` makes that
    call raise, as a CUDA out-of-memory would.
    """

    def __init__(self, torch, fail_at=None, hook=None):
        super().__init__(torch, [])
        self.fail_at, self.hook = fail_at, hook

    def generate(self, **kwargs):
        assert kwargs["generation_config"].eos_token_id == 99
        call = len(self.seeds)
        if self.hook:
            self.hook(call)
        if call == self.fail_at:
            raise RuntimeError("CUDA out of memory")
        self.seeds.append(self.torch.state)
        state = self.torch.state % 1000
        self.torch.state += 1
        n = kwargs["input_ids"].shape[0] * kwargs["generation_config"].num_return_sequences
        return Tokens([[7, 7, 7, state, row, 99] for row in range(n)])


class Scorer:
    """Deterministic scores, an uneven finishing order, an optional block.

    `block` names draws that abort the arm (a harness failure); `mismatch`
    names draws whose native run disagreed with the oracle, which since
    2026-09-24 are counted draws (`pipeline.mismatch_policy`).
    """

    def __init__(self, block=None, enter=None, mismatch=None):
        self.block, self.enter, self.mismatch = block, enter, mismatch
        self.lock = threading.Lock()
        self.seen = []

    def score(self, case, program):
        with self.lock:
            self.seen.append((case["case_id"], program))
        if self.enter:
            self.enter(case, program)
        n = sum(map(ord, program))
        time.sleep((n % 5) * 0.002)          # later draws often finish first
        from pipeline.training_types import ENGINE_MISMATCH, VerificationBlocked
        if self.block and self.block(case, program):
            raise VerificationBlocked("harness", {"program": program})
        if self.mismatch and self.mismatch(case, program):
            raise VerificationBlocked(ENGINE_MISMATCH, {
                "case_id": case["case_id"], "test": 0, "expected_stdout": case["tests"][0]["stdout"],
                "observed": [1, "", "SyntaxError\n", False, False, False, False]})
        if case["population"] == "fallback-control":
            return Score(1, "correct-control", 3, 3)
        return Score(1, "correct-native", 3, 3) if n % 3 else Score(0, "incorrect", 0, 1, (), 0)


#: Five cases at two draws, four sequences per call: chunks of 2, 2 and 1 case.
OVERLAP_CASES = [dict(case_id="c%d" % i, family="f%d" % (i % 2), task="t%d" % i,
                      population="fallback-control" if i == 4 else "coverage",
                      split_group="g%d" % (i % 2), tests=[{"stdout": str(i)}]) for i in range(5)]
#: Fifty cases at two draws: 100 planned draws, so ONE engine mismatch is at
#: the 1% bound and not over it; 25 chunks of two cases.
BOUNDED_CASES = [dict(case_id="c%d" % i, family="f%d" % (i % 3), task="t%d" % i,
                      population="fallback-control" if i % 7 == 6 else "coverage",
                      split_group="g%d" % (i % 3), tests=[{"stdout": str(i)}]) for i in range(50)]


def chunk_of(case):
    return int(case["case_id"][1:]) // 2


def scoring_threads():
    return [t.name for t in threading.enumerate() if t.name.startswith(("eval-score", "eval-stage"))]


def run_evaluation(tmp_path, name, overlapped, *, block=None, fail_at=None, score_workers=3,
                   hook=None, enter=None, mismatch=None, cases=OVERLAP_CASES, keep_mismatches=True):
    ev, torch = load_evaluation(), FakeTorch()
    model = SeededModel(torch, fail_at, hook)
    verifier = Scorer(block, enter, mismatch)
    out = tmp_path / name
    out.mkdir()
    got = {"raised": None, "metrics": None, "records": None}
    try:
        got["metrics"], got["records"] = ev.evaluate(
            model, Tokenizer(), cases, verifier, decoding(10), out / "evaluations.jsonl", 3,
            torch, seed=11, draws=2, return_records=True, witness_path=out / "witness.jsonl",
            mismatch_path=out / "engine-mismatches.jsonl" if keep_mismatches else None,
            sequences_per_call=4, score_workers=score_workers, overlapped=overlapped)
    except Exception as exc:                                     # noqa: BLE001 -- compared below
        got["raised"] = (type(exc).__name__, str(exc), getattr(exc, "witness", None))
    for key in ("evaluations.jsonl", "witness.jsonl", "engine-mismatches.jsonl"):
        got[key] = (out / key).read_bytes() if (out / key).exists() else None
    got.update(seeds=model.seeds, scored=sorted(verifier.seen), threads=scoring_threads(),
               state=torch.state, training=model.training)
    return got


def c2_second_draw(case, program):
    """One draw of the middle chunk blocks; its sibling draws score."""
    return case["case_id"] == "c2" and program.endswith(",1)")


def c20_first_draw(case, program):
    """One engine-mismatch draw, mid-run: chunk 10 of the bounded fixture."""
    return case["case_id"] == "c20" and program.endswith(",0)")


@pytest.mark.parametrize("scenario, block, fail_at, raised, mismatch, cases", [
    ("clean", None, None, None, None, OVERLAP_CASES),
    ("blocked", c2_second_draw, None, "VerificationBlocked", None, OVERLAP_CASES),
    ("blocked-first-chunk", lambda c, p: c["case_id"] == "c0" and p.endswith(",0)"), None,
     "VerificationBlocked", None, OVERLAP_CASES),
    ("generation-fails", None, 2, "RuntimeError", None, OVERLAP_CASES),
    ("blocked-then-generation-fails", c2_second_draw, 2, "VerificationBlocked", None, OVERLAP_CASES),
    ("every-draw-of-a-case-blocks", lambda c, p: c["case_id"] == "c1", None, "VerificationBlocked",
     None, OVERLAP_CASES),
    # Engine mismatches (2026-09-24): one of 100 draws is counted and the arm
    # completes; one of 10 is over the 1% bound and ends it after its chunk.
    ("mismatch-counted", None, None, None, c20_first_draw, BOUNDED_CASES),
    ("mismatch-over-bound", None, None, "EngineMismatchBound", c2_second_draw, OVERLAP_CASES),
    ("mismatch-then-blocked", lambda c, p: c["case_id"] == "c30" and p.endswith(",1)"), None,
     "VerificationBlocked", c20_first_draw, BOUNDED_CASES),
])
def test_overlapped_scoring_writes_the_serial_bytes(tmp_path, monkeypatch, scenario, block, fail_at, raised,
                                                     mismatch, cases):
    """Rows, their order, metrics, witnesses and the exception: the serial loop's, exactly.

    The only thing overlap may change is that, on an abort, one more chunk was
    GENERATED while the failing one was being scored -- and it is never
    scored and never written.
    """
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(GenerationConfig=SimpleNamespace))
    serial = run_evaluation(tmp_path, "serial", False, block=block, fail_at=fail_at,
                            mismatch=mismatch, cases=cases)
    overlapped = run_evaluation(tmp_path, "overlapped", True, block=block, fail_at=fail_at,
                                mismatch=mismatch, cases=cases)
    assert (serial["raised"] or (None,))[0] == raised
    compared = ["raised", "metrics", "records", "evaluations.jsonl", "engine-mismatches.jsonl",
                "scored", "state", "training"]
    if scenario != "every-draw-of-a-case-blocks":
        compared.append("witness.jsonl")          # two witness rows race in either mode
    for key in compared:
        assert overlapped[key] == serial[key], key
    if scenario == "every-draw-of-a-case-blocks":
        def rows(got):
            return sorted(got["witness.jsonl"].decode().splitlines())
        assert rows(overlapped) == rows(serial) and len(rows(serial)) == 2
    assert serial["state"] == 123 and serial["training"], "fork_rng and the trainer's state restored"
    # Generation's RNG sequence is the serial one, chunk for chunk.
    assert overlapped["seeds"][:len(serial["seeds"])] == serial["seeds"]
    if raised is None:
        assert overlapped["seeds"] == serial["seeds"] and len(serial["seeds"]) == (len(cases) + 1) // 2
        assert [(r["case_id"], r["draw"]) for r in serial["records"]] == \
            [("c%d" % i, d) for i in range(len(cases)) for d in range(2)]
    else:
        assert len(overlapped["seeds"]) - len(serial["seeds"]) <= 1, "at most one call later"
    if raised == "VerificationBlocked":
        assert serial["witness.jsonl"], "a blocked draw still leaves its witness"
    if mismatch is None:
        assert serial["engine-mismatches.jsonl"] is None, "no mismatch, no new file"
    else:
        # The mismatch is a row, not an abort: counted, scored zero, witnessed
        # privately, and the aborting-block witness file never sees it.
        hit = [r for r in read_rows(serial) if r["status"] == "engine-mismatch"]
        assert len(hit) == 1 and hit[0]["reward"] == 0 and not hit[0]["correct"] and not hit[0]["native"]
        kept = [json.loads(line) for line in serial["engine-mismatches.jsonl"].decode().splitlines()]
        assert [(w["case_id"], w["draw"], w["step"]) for w in kept] == \
            [(hit[0]["case_id"], hit[0]["draw"], 3)]
        assert kept[0]["witness"]["expected_stdout"] and kept[0]["digest"] and kept[0]["program"]
        assert kept[0]["source"] == "evaluation" and kept[0]["kind"] == "engine mismatch"
        assert "engine mismatch" not in (serial["witness.jsonl"] or b"").decode()
    if scenario == "mismatch-counted":
        assert serial["metrics"]["engine_mismatches"] == 1
        assert serial["metrics"]["statuses"]["engine-mismatch"] == 1
    if scenario == "mismatch-over-bound":
        # Counts only; the chunk that crossed the bound is written whole first.
        assert serial["raised"] == ("EngineMismatchBound",
                                    "engine-mismatch draws 1 of 10 exceed the 1% bound", None)
        assert [r["case_id"] for r in read_rows(serial)] == ["c0", "c0", "c1", "c1", "c2", "c2", "c3", "c3"]
    assert serial["threads"] == overlapped["threads"] == [], "no scoring thread outlives evaluate"


@pytest.mark.parametrize("overlapped", [False, True])
def test_a_mismatch_with_nowhere_to_file_it_still_aborts(tmp_path, monkeypatch, overlapped):
    """No private mismatch file, no counting: a counted draw whose witness is
    dropped would be a hidden engine bug (root CLAUDE.md invariant 1), so an
    evaluation without `mismatch_path` aborts on the mismatch exactly as it
    did before 2026-09-24, its program kept in the abort witness."""
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(GenerationConfig=SimpleNamespace))
    got = run_evaluation(tmp_path, "run", overlapped, mismatch=c20_first_draw, cases=BOUNDED_CASES,
                         keep_mismatches=False)
    assert got["raised"][0] == "VerificationBlocked" and got["raised"][1].startswith("engine mismatch (witness ")
    assert got["engine-mismatches.jsonl"] is None
    kept = [json.loads(line) for line in got["witness.jsonl"].decode().splitlines()]
    assert [(w["case_id"], w["draw"], w["kind"]) for w in kept] == [("c20", 0, "engine mismatch")]
    assert "engine-mismatch" not in got["evaluations.jsonl"].decode()


def read_rows(got):
    return [json.loads(line) for line in (got["evaluations.jsonl"] or b"").decode().splitlines()]


def test_a_chunk_is_scored_while_the_next_generates_and_never_two_at_once(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(GenerationConfig=SimpleNamespace))
    lock, inflight, seen_at_generate, widest = threading.Lock(), {}, {}, []
    entered, generating = threading.Event(), threading.Event()

    def enter(case, program):
        chunk = chunk_of(case)
        with lock:
            inflight[chunk] = inflight.get(chunk, 0) + 1
            widest.append(len(inflight))
        if chunk == 0:
            entered.set()
            # Held until the second call is generating: overlap, not luck.
            assert generating.wait(5), "chunk 0 was not scored while chunk 1 generated"

    class Tracked(Scorer):
        def score(self, case, program):
            try:
                return Scorer.score(self, case, program)
            finally:
                with lock:
                    chunk = chunk_of(case)
                    inflight[chunk] -= 1
                    if not inflight[chunk]:
                        del inflight[chunk]

    def hook(call):
        if call == 1:
            assert entered.wait(5)
        with lock:
            seen_at_generate[call] = set(inflight)
        if call == 1:
            generating.set()          # chunk 0's scorings were held in flight until now

    ev, torch = load_evaluation(), FakeTorch()
    model = SeededModel(torch, hook=hook)
    ev.evaluate(model, Tokenizer(), OVERLAP_CASES, Tracked(enter=enter), decoding(10),
                tmp_path / "eval.jsonl", 0, torch, seed=11, draws=2, sequences_per_call=4, score_workers=4)
    assert seen_at_generate[0] == set() and seen_at_generate[1] == {0}
    assert all(now <= {call - 1} for call, now in seen_at_generate.items())
    assert max(widest) == 1, "one chunk scored at a time: backpressure"
    assert scoring_threads() == []


def test_a_scoring_failure_surfaces_once_the_overlapping_generate_returns(tmp_path, monkeypatch):
    """The abort is raised after the call in flight, and the chunk it made is never scored."""
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(GenerationConfig=SimpleNamespace))
    failed = threading.Event()

    def block(case, program):
        if case["case_id"] == "c0" and program.endswith(",0)"):
            failed.set()
            return True
        return False

    def hook(call):
        if call == 1:
            assert failed.wait(5), "chunk 0's block happens during chunk 1's generation"

    got = run_evaluation(tmp_path, "abort", True, block=block, hook=hook)
    assert got["raised"][0] == "VerificationBlocked"
    assert len(got["seeds"]) == 2, "no third call: the abort is taken before the next generate"
    assert {case for case, _ in got["scored"]} == {"c0", "c1"}, "chunk 1 was generated, never scored"
    assert [json.loads(line)["case_id"] for line in got["evaluations.jsonl"].decode().splitlines()] == \
        ["c0", "c1", "c1"], "the blocked draw's siblings are kept"
    assert json.loads(got["witness.jsonl"])["case_id"] == "c0"
    assert got["threads"] == [] and got["state"] == 123


def test_an_interrupt_cancels_queued_scorings_and_joins_every_thread(tmp_path, monkeypatch):
    """KeyboardInterrupt (or a TERM handler's exit) mid-generate: nothing left running."""
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(GenerationConfig=SimpleNamespace))
    started = threading.Event()

    def enter(case, program):
        started.set()
        time.sleep(0.2)                      # in flight when the interrupt lands

    def hook(call):
        if call == 1:
            assert started.wait(5)
            raise KeyboardInterrupt

    ev, torch = load_evaluation(), FakeTorch()
    model, verifier = SeededModel(torch, hook=hook), Scorer(enter=enter)
    with pytest.raises(KeyboardInterrupt):
        ev.evaluate(model, Tokenizer(), OVERLAP_CASES, verifier, decoding(10), tmp_path / "eval.jsonl",
                    0, torch, seed=11, draws=2, sequences_per_call=4, score_workers=1)
    assert scoring_threads() == [], "every scoring thread joined before the interrupt propagates"
    assert len(verifier.seen) < 4, "queued scorings of the chunk were cancelled, not run"
    # The draw in flight finished and scored, its three siblings were
    # cancelled: writing it alone would leave a chunk with holes and no
    # witness. The serial loop interrupted mid-scoring writes nothing of it.
    assert not (tmp_path / "eval.jsonl").exists(), "no partial chunk after teardown"
    assert torch.state == 123 and model.training


def test_an_interrupt_after_a_chunk_scored_keeps_that_chunk_whole(tmp_path, monkeypatch):
    """Interrupted while chunk 1 generates, chunk 0 already scored: chunk 0's rows, all of them."""
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(GenerationConfig=SimpleNamespace))
    ev, torch = load_evaluation(), FakeTorch()
    written = tmp_path / "eval.jsonl"

    def hook(call):
        if call == 1:
            deadline = time.monotonic() + 5
            while not (written.exists() and len(written.read_text().splitlines()) == 4):
                assert time.monotonic() < deadline, "chunk 0 was never written"
                time.sleep(0.005)
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        ev.evaluate(SeededModel(torch, hook=hook), Tokenizer(), OVERLAP_CASES, Scorer(), decoding(10),
                    written, 0, torch, seed=11, draws=2, sequences_per_call=4, score_workers=2)
    assert [(json.loads(line)["case_id"], json.loads(line)["draw"])
            for line in written.read_text().splitlines()] == [("c0", 0), ("c0", 1), ("c1", 0), ("c1", 1)]
    assert scoring_threads() == []


def test_the_trainer_overlaps_by_default_records_it_and_keeps_it_out_of_every_identity():
    root = Path(__file__).resolve().parents[1]
    source = (root / "gpu" / "train_verified.py").read_text()
    assert source.count("overlapped=not args.serial_scoring") == source.count("evaluate(model, tok,") == 2
    assert '"scoring": "serial" if args.serial_scoring else "overlapped"' in source
    spec = importlib.util.spec_from_file_location("overlap_tv", root / "gpu" / "train_verified.py")
    tv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tv)
    argv = ["eval", "--bundle", "b.json", "--engine", "e", "--output", "o", "--revision", "a" * 40]
    assert tv.parser().parse_args(argv).serial_scoring is False
    assert tv.parser().parse_args(argv + ["--serial-scoring"]).serial_scoring is True
    from pipeline import evaluation_reuse
    for keys in (evaluation_reuse.CONTRACT_KEYS, evaluation_reuse.ARGUMENT_KEYS,
                 evaluation_reuse.STEP0_CONTRACT_KEYS, evaluation_reuse.STEP0_ARGUMENT_KEYS):
        assert "scoring" not in keys and "serial_scoring" not in keys
    spec = importlib.util.spec_from_file_location("overlap_arm", root.parent / ".github" / "scripts"
                                                  / "arm_check.py")
    arm_check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(arm_check)
    assert not {"scoring", "serial_scoring"} & set(arm_check.ARM_FIELDS)


class Mask:
    def __init__(self, lengths):
        self.lengths = lengths

    def sum(self, dim):
        assert dim == 1
        return SimpleNamespace(tolist=lambda: list(self.lengths))


class LengthTokenizer(Tokenizer):
    """Prompts as long as their task says (`t<len>`), left-padded to the longest."""
    def __call__(self, texts, **kwargs):
        assert isinstance(texts, list) and kwargs["padding"] is True and self.padding_side == "left"
        self.batches = getattr(self, "batches", []) + [len(texts)]
        lengths = [int(t.split("t")[-1]) for t in texts]
        return Batch(input_ids=SimpleNamespace(shape=(len(texts), max(lengths))), attention_mask=Mask(lengths))


def test_prefill_parts_are_the_fewest_contiguous_spans_under_the_budget():
    ev = load_evaluation()
    assert ev.prefill_parts([10, 10, 10, 10], 2, 80) == [[0, 1, 2, 3]], "a chunk that fits is one span"
    assert ev.prefill_parts([10, 40, 10, 10], 2, 160) == [[0, 1], [2, 3]]
    assert ev.prefill_parts([10, 10, 10, 434], 16, 48384) == [[0, 1, 2, 3]]
    assert ev.prefill_parts([100, 100, 100], 4, 300) == [[0], [1], [2]], "an oversize case stands alone"
    assert ev.PREFILL_TOKENS == 189 * 256


def test_an_oversize_chunk_splits_the_same_way_in_both_arms_and_others_keep_their_seed(tmp_path, monkeypatch):
    """Chunk 1 holds one long prompt: it is drawn as two parts, each padded to
    its own longest and seeded by its own cases, identically in both arms. The
    chunk under the budget keeps the seed it had before the budget existed."""
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(GenerationConfig=SimpleNamespace))
    ev, torch = load_evaluation(), FakeTorch()
    verifier = SimpleNamespace(score=lambda c, p: Score(1, "correct-native", 3, 3))
    cases = [dict(case_id=str(i), family="f", task="t%d" % n, population="coverage")
             for i, n in enumerate([5, 5, 5, 40])]
    arms = []
    for arm in ("base", "candidate", "unbudgeted"):
        model, tok = Model(torch, [1, 99]), LengthTokenizer()
        budget = None if arm == "unbudgeted" else 100
        _, records = ev.evaluate(model, tok, cases, verifier, decoding(10), tmp_path / arm / "e.jsonl",
                                 0, torch, seed=7, draws=2, return_records=True, sequences_per_call=4,
                                 prefill_tokens=budget)
        arms.append((tok.batches, [(r["case_id"], r["draw"], r["seed"]) for r in records]))
        splits = tmp_path / arm / ev.PREFILL_SPLITS_FILE
        assert splits.exists() == (budget is not None)
    assert arms[0] == arms[1], "both arms split the same chunk the same way"
    assert arms[0][0] == [2, 2, 1, 1] and arms[2][0] == [2, 2], "chunk 1 is tokenized whole, then per part"
    seeds = lambda rows: {c: s for c, _, s in rows}  # noqa: E731
    assert seeds(arms[0][1])["0"] == seeds(arms[2][1])["0"], "an unsplit chunk keeps its seed"
    assert seeds(arms[0][1])["2"] != seeds(arms[0][1])["3"], "each part has its own seed"
    assert [(c, d) for c, d, _ in arms[0][1]] == [(c, d) for c, d, _ in arms[2][1]], "(case, draw) order holds"
    assert json.loads((tmp_path / "base" / ev.PREFILL_SPLITS_FILE).read_text()) == \
        {"step": 0, "splits": [{"chunk_cases": 2, "padded": 40, "parts": [1, 1]}]}


def test_the_chunk_that_ran_the_finish_out_of_memory_now_fits_in_three_parts():
    """HF job 6ab6a20c6b030d633f691a95, eval-2 chunk index 26 (2026-09-25): one
    434-token prompt among 16 cases at 16 draws padded 256 sequences to 111,104
    prefill tokens. Every part must fit the largest prefill already run clean."""
    ev = load_evaluation()
    lengths = [129] * 15 + [434]
    assert 16 * 16 * max(lengths) == 111_104
    spans = ev.prefill_parts(lengths, 16, ev.PREFILL_TOKENS)
    assert [len(s) for s in spans] == [6, 6, 4]
    assert sorted(i for s in spans for i in s) == list(range(16)), "every case, once, in order"
    assert all(len(s) * 16 * max(lengths[i] for i in s) <= ev.PREFILL_TOKENS for s in spans)
    typical = [129] * 16
    assert ev.prefill_parts(typical, 16, ev.PREFILL_TOKENS) == [list(range(16))], "a p50 chunk is untouched"
