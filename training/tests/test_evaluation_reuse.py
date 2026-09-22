from __future__ import annotations

import copy
from pathlib import Path

import pytest

from pipeline.evaluation_reuse import ARGUMENT_KEYS, CONTRACT_KEYS, reuse_evaluation
from pipeline.jsonio import write_json, write_jsonl
from pipeline.training_metrics import summarize
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
