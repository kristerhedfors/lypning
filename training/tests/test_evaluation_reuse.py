from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from pipeline.evaluation_reuse import (ARGUMENT_KEYS, CONTRACT_KEYS, STEP0_ARGUMENT_KEYS,
                                       STEP0_CONTRACT_KEYS, reuse_evaluation, reuse_step_zero)
from pipeline.jsonio import write_json, write_jsonl
from pipeline.training import Score
from pipeline.training_contract import decoding
from pipeline.training_metrics import CheckpointGate, summarize
from pipeline.training_types import TrainingError


def fixture(tmp_path, parent=None):
    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir(); output.mkdir()
    manifest = {key: {} for key in CONTRACT_KEYS}
    manifest.update(stage="eval", metric_policy={"min_family_cases": 1},
                    adapter={"sha256": parent} if parent else None,
                    args={key: 1 for key in ARGUMENT_KEYS})
    manifest["args"]["greedy"] = False
    cases = [dict(case_id="c", family="f", population="coverage", capabilities=[], split_group="g")]
    rows = [dict(cases[0], draw=0, correct=True, native=True)]
    write_json(source / "experiment.json", manifest)
    write_jsonl(source / "evaluations.jsonl", rows)
    write_json(source / "metrics.json", summarize(rows))
    target = copy.deepcopy(manifest)
    target["adapter"] = {"sha256": "candidate", "experiment": {
        "checkpoint_step": 0, "policy_equivalence": {"adapter_sha256": parent,
        "basis": "identical-parent-files" if parent else "finite-zero-lora-b"}}}
    return source, output, target, cases


@pytest.mark.parametrize("parent", [None, "trained-sft"])
def test_step_zero_reuses_its_actual_parent_and_records_provenance(tmp_path, parent):
    source, output, manifest, cases = fixture(tmp_path, parent)
    assert reuse_evaluation(source, output, manifest, cases)
    assert (output / "evaluations.jsonl").read_bytes() == (source / "evaluations.jsonl").read_bytes()
    import json
    proof = json.loads((output / "reuse.json").read_text())
    assert proof["independent_draws"] is False
    assert proof["basis"]["adapter_sha256"] == parent
    assert set(proof["source_sha256"]) == {"experiment.json", "evaluations.jsonl", "metrics.json"}


def test_grpo_step_zero_is_not_necessarily_base(tmp_path):
    source, output, manifest, cases = fixture(tmp_path)
    manifest["adapter"]["experiment"]["policy_equivalence"]["adapter_sha256"] = "trained-sft"
    with pytest.raises(TrainingError, match="parent policy"):
        reuse_evaluation(source, output, manifest, cases)
    assert not list(output.iterdir())


@pytest.mark.parametrize("kind", ["trained", "legacy"])
def test_no_equivalence_proof_means_real_evaluation(tmp_path, kind):
    source, output, manifest, cases = fixture(tmp_path)
    if kind == "trained":
        manifest["adapter"]["experiment"]["checkpoint_step"] = 50
    else:
        del manifest["adapter"]["experiment"]["policy_equivalence"]
    assert not reuse_evaluation(source, output, manifest, cases)
    assert not list(output.iterdir())


@pytest.mark.parametrize("key", CONTRACT_KEYS)
def test_runtime_contract_drift_refuses_reuse(tmp_path, key):
    source, output, manifest, cases = fixture(tmp_path)
    manifest[key] = "different"
    with pytest.raises(TrainingError, match="contract mismatch"):
        reuse_evaluation(source, output, manifest, cases)


@pytest.mark.parametrize("key", ARGUMENT_KEYS)
def test_evaluation_chunking_or_sampling_drift_refuses_reuse(tmp_path, key):
    source, output, manifest, cases = fixture(tmp_path)
    manifest["args"][key] = "different"
    with pytest.raises(TrainingError, match="argument mismatch"):
        reuse_evaluation(source, output, manifest, cases)


def test_incomplete_or_tampered_evidence_is_not_reused(tmp_path):
    source, output, manifest, cases = fixture(tmp_path)
    with pytest.raises(TrainingError, match="every requested"):
        reuse_evaluation(source, output, manifest, cases + [dict(cases[0], case_id="missing")])
    write_json(source / "metrics.json", {})
    with pytest.raises(TrainingError, match="saved draws"):
        reuse_evaluation(source, output, manifest, cases)


def test_noop_proof_rejects_nonzero_nonfinite_or_extra_trainable_parameters():
    from types import SimpleNamespace
    from pipeline.evaluation_reuse import fresh_lora_is_noop
    class Tensor:
        requires_grad = True
        def __init__(self, nonzero=0, finite=True):
            self.nonzero, self.finite = nonzero, finite
        def isfinite(self):
            return SimpleNamespace(all=lambda: self.finite)
        def detach(self):
            return self
        def count_nonzero(self):
            return self.nonzero
    a, b = Tensor(1), Tensor()
    params = [("x.lora_A.default.weight", a), ("x.lora_B.default.weight", b)]
    model = SimpleNamespace(named_parameters=lambda: iter(params))
    assert fresh_lora_is_noop(model)
    b.nonzero = 1
    assert not fresh_lora_is_noop(model)
    b.nonzero = 0
    a.finite = False
    assert not fresh_lora_is_noop(model)
    a.finite = True
    params.append(("x.bias", Tensor()))
    assert not fresh_lora_is_noop(model)


# --- SFT step 0 from this job's base-dev ------------------------------------------

TESTS = Path(__file__).resolve().parent


def fake_runtime(monkeypatch):
    """`test_verified_evaluation`'s fake model, tokenizer and torch, and the real `evaluate`."""
    spec = importlib.util.spec_from_file_location("reuse_eval_fakes", TESTS / "test_verified_evaluation.py")
    fakes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fakes)
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(GenerationConfig=SimpleNamespace))
    return fakes, fakes.load_evaluation()


#: Dev cases over both populations the gate reads, several families each.
DEV = [dict(case_id="d%d" % i, family="f%d" % (i % 4), task="task %d" % i, split="dev",
            population="coverage" if i % 4 < 2 else "fallback-control", split_group="g%d" % (i % 4))
       for i in range(8)]


def score(case, program):
    """Deterministic and uneven, so the gate's baseline is not a constant."""
    n = int(case["case_id"][1:])
    if n % 3 == 0:
        return Score(0, "wrong-output")
    return Score(1, "correct-native" if n % 2 else "correct-fallback", 3, 3)


def manifest_of(stage, adapter=None, **over):
    manifest = {key: "same" for key in STEP0_CONTRACT_KEYS}
    manifest.update(stage=stage, adapter=adapter, metric_policy={"min_family_cases": 1},
                    job_id="6ab4582d6b030d633f68c90e",
                    args={key: "same" for key in STEP0_ARGUMENT_KEYS})
    manifest["args"].update(seed=1111, eval_split="dev", eval_draws=2, greedy=False,
                            eval_sequences=4, score_workers=2)
    manifest.update(over)
    return manifest


def measure(ev, fakes, path, step=0):
    """One dev evaluation exactly as `train_verified.measure` runs it."""
    torch = fakes.FakeTorch()
    verifier = SimpleNamespace(score=score)
    return ev.evaluate(fakes.Model(torch, [1, 2, 99]), fakes.Tokenizer(), DEV, verifier, decoding(10),
                       path, step, torch, seed=1111, draws=2, sequences_per_call=4, score_workers=2,
                       min_family_cases=1)


def base_dev(tmp_path, ev, fakes, **over):
    source = tmp_path / "base-dev"
    source.mkdir(parents=True)
    metrics = measure(ev, fakes, source / "evaluations.jsonl")
    write_json(source / "metrics.json", metrics)
    write_json(source / "experiment.json", manifest_of("eval", **over))
    return source


def test_a_reused_step_zero_is_the_step_zero_a_fresh_evaluation_would_record(tmp_path, monkeypatch):
    """Same draws, same metrics, same CheckpointGate baseline and best.json.

    The fake policy is deterministic, as a no-op LoRA over the base is up to
    kernel nondeterminism: regenerating step 0 in the SFT stage and reading
    base-dev's draws must give the gate the same incumbent.
    """
    fakes, ev = fake_runtime(monkeypatch)
    source = base_dev(tmp_path, ev, fakes)
    fresh, reused = tmp_path / "sft-fresh", tmp_path / "sft-reused"
    fresh.mkdir(); reused.mkdir()
    regenerated = measure(ev, fakes, fresh / "evaluations.jsonl")
    baseline = reuse_step_zero(source, reused, manifest_of("sft"), DEV, noop=True)
    assert baseline == regenerated
    assert json.loads(json.dumps(baseline)) == json.loads((source / "metrics.json").read_text())
    rows = [json.loads(line) for line in (reused / "evaluations.jsonl").read_text().splitlines()]
    again = [json.loads(line) for line in (fresh / "evaluations.jsonl").read_text().splitlines()]
    assert rows == again and {r["step"] for r in rows} == {0}
    assert CheckpointGate(baseline).report() == CheckpointGate(regenerated).report()
    # A later checkpoint is judged against the same incumbent either way.
    later = measure(ev, fakes, tmp_path / "later.jsonl", step=350)
    a, b = CheckpointGate(baseline), CheckpointGate(regenerated)
    a.observe(350, later); b.observe(350, later)
    assert a.report() == b.report()
    proof = json.loads((reused / "reuse.json").read_text())
    assert proof["basis"] == {"adapter_sha256": None, "basis": "finite-zero-lora-b"}
    assert proof["step"] == 0 and proof["independent_draws"] is False
    assert set(proof["source_sha256"]) == {"experiment.json", "evaluations.jsonl", "metrics.json"}
    assert not (reused / "metrics.json").exists(), "the SFT stage's metrics are its checkpoints'"


def test_no_noop_proof_means_step_zero_is_generated(tmp_path, monkeypatch):
    fakes, ev = fake_runtime(monkeypatch)
    source = base_dev(tmp_path, ev, fakes)
    output = tmp_path / "sft"; output.mkdir()
    assert reuse_step_zero(source, output, manifest_of("sft"), DEV, noop=False) is None
    assert not list(output.iterdir())


@pytest.mark.parametrize("key", STEP0_CONTRACT_KEYS)
def test_step_zero_reuse_refuses_any_runtime_contract_drift(tmp_path, monkeypatch, key):
    fakes, ev = fake_runtime(monkeypatch)
    source = base_dev(tmp_path, ev, fakes)
    output = tmp_path / "sft"; output.mkdir()
    with pytest.raises(TrainingError, match="contract mismatch: " + key):
        reuse_step_zero(source, output, manifest_of("sft", **{key: "different"}), DEV, noop=True)
    assert not list(output.iterdir())


@pytest.mark.parametrize("key", STEP0_ARGUMENT_KEYS)
def test_step_zero_reuse_refuses_other_draws_chunking_seed_bundle_or_engine(tmp_path, monkeypatch, key):
    fakes, ev = fake_runtime(monkeypatch)
    source = base_dev(tmp_path, ev, fakes)
    output = tmp_path / "sft"; output.mkdir()
    manifest = manifest_of("sft")
    manifest["args"][key] = "different"
    with pytest.raises(TrainingError, match="argument mismatch: " + key):
        reuse_step_zero(source, output, manifest, DEV, noop=True)


def test_step_zero_reuse_needs_the_unadapted_base_dev_of_the_same_job(tmp_path, monkeypatch):
    fakes, ev = fake_runtime(monkeypatch)
    source = base_dev(tmp_path, ev, fakes)
    output = tmp_path / "sft"; output.mkdir()
    with pytest.raises(TrainingError, match="job_id"):
        reuse_step_zero(source, output, manifest_of("sft", job_id="6ab01cbb51992417dfccd64c"), DEV, noop=True)
    with pytest.raises(TrainingError, match="job id"):
        reuse_step_zero(source, output, manifest_of("sft", job_id=None), DEV, noop=True)
    with pytest.raises(TrainingError, match="pinned base"):
        reuse_step_zero(source, output, manifest_of("eval"), DEV, noop=True)
    with pytest.raises(TrainingError, match="pinned base"):
        reuse_step_zero(source, output, manifest_of("sft", adapter={"sha256": "x"}), DEV, noop=True)
    adapted = base_dev(tmp_path / "adapted", ev, fakes, adapter={"sha256": "trained"})
    with pytest.raises(TrainingError, match="unadapted base-dev"):
        reuse_step_zero(adapted, output, manifest_of("sft"), DEV, noop=True)
    assert not list(output.iterdir())


def test_step_zero_reuse_refuses_incomplete_or_foreign_draws(tmp_path, monkeypatch):
    fakes, ev = fake_runtime(monkeypatch)
    source = base_dev(tmp_path, ev, fakes)
    output = tmp_path / "sft"; output.mkdir()
    with pytest.raises(TrainingError, match="every requested"):
        reuse_step_zero(source, output, manifest_of("sft"), DEV + [dict(DEV[0], case_id="d99")], noop=True)
    rows = [json.loads(line) for line in (source / "evaluations.jsonl").read_text().splitlines()]
    write_jsonl(source / "evaluations.jsonl", rows + rows)      # every draw twice
    with pytest.raises(TrainingError, match="duplicate"):
        reuse_step_zero(source, output, manifest_of("sft"), DEV, noop=True)
    for row in rows:
        row["step"] = 50
    write_jsonl(source / "evaluations.jsonl", rows)
    write_json(source / "metrics.json", summarize(rows))
    with pytest.raises(TrainingError, match="step-0 draws only"):
        reuse_step_zero(source, output, manifest_of("sft"), DEV, noop=True)
    assert not list(output.iterdir())


def test_the_trainer_reuses_step_zero_only_in_sft_and_only_on_a_noop_proof():
    """Read from the script, as `test_verified_evaluation` pins its call sites."""
    source = (TESTS.parent / "gpu" / "train_verified.py").read_text()
    assert "reuse_step_zero(args.reuse_step0, args.output, manifest, dev_cases,\n" \
           "                                   fresh_lora_is_noop(model))" in source
    assert source.index("reuse_step_zero(args.reuse_step0") < source.index("baseline = measure(0)") \
        < source.index("gate = CheckpointGate(baseline)")
    assert '"job_id": os.environ.get("JOB_ID")' in source
    spec = importlib.util.spec_from_file_location("reuse_tv", TESTS.parent / "gpu" / "train_verified.py")
    tv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tv)
    args = tv.parser().parse_args(["eval", "--bundle", "b.json", "--engine", "e", "--output", "/nonexistent/o",
                                   "--revision", "a" * 40, "--reuse-step0", "base-dev"])
    with pytest.raises(TrainingError, match="only for SFT"):
        tv.preflight(args)
