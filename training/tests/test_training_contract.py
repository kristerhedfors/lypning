"""Scientific-contract regressions; no model downloads, GPU imports or rollouts."""
from __future__ import annotations

import json
from pathlib import Path
import re
from dataclasses import asdict

import pytest

from pipeline.curriculum import starter_cases
from pipeline.jsonio import write_json, write_jsonl
from pipeline.training import Reward, Score, TrainingError
from pipeline.training_contract import (BASE_MODEL, CONTRACT_VERSION, adapter_identity,
    complete, decoding, draw_seed, learning_rate, model_config_identity, probe_report, seal_adapter, validate_probe)
from pipeline import training_contract as contract_module
from pipeline.training_data import split_cases, validate_cases, validate_pilot, validate_reference_scores
from pipeline.training_metrics import CheckpointGate, paired_comparison, summarize


def test_source_and_ast_components_do_not_cross_splits():
    cases = starter_cases()
    cases[0]["source_group"] = cases[1]["source_group"]
    cases[2]["reference"] = "# different formatting\n" + cases[1]["reference"]
    split = split_cases(cases)
    assert len({c["split"] for c in split[:3]}) == 1
    assert {c["case_id"]: c["split"] for c in split} == {
        c["case_id"]: c["split"] for c in split_cases(list(reversed(cases)))}
    for c in cases:
        c["source_group"] = "one source"
    with pytest.raises(TrainingError, match="independent"):
        split_cases(cases)


def test_pilot_stratification_has_independent_controls():
    # Pure split fixture, not a claimed executable or independent dataset.
    cases = [dict(case_id=str(i), family=str(i), source_group=str(i), capabilities=["fixture"],
                  reference="print(%d)" % i,
                  population="coverage" if i < 9 else "fallback-control") for i in range(18)]
    split = split_cases(cases)
    validate_pilot(split)
    for part in ("train", "dev", "test"):
        for pop in ("coverage", "fallback-control"):
            assert len({c["family"] for c in split if c["split"] == part and c["population"] == pop}) >= 2


@pytest.mark.parametrize("path", ["a/../b", "/a", "./a", "a//b", "a\\b", "solution.py/x", "a\0b"])
def test_unsafe_input_paths_fail_admission(path):
    cases = starter_cases()
    cases[0]["tests"][0]["files"] = {path: "hello"}
    with pytest.raises(TrainingError):
        validate_cases(cases)


def test_malformed_labels_and_path_collisions_are_clean_errors():
    cases = starter_cases()
    cases[0]["capabilities"] = [{}]
    with pytest.raises(TrainingError):
        validate_cases(cases)
    cases[0]["capabilities"] = ["a"]
    cases[0]["tests"][0]["files"] = {"a": "file", "a/b": "nested"}
    with pytest.raises(TrainingError, match="collision"):
        validate_cases(cases)


def test_population_labels_require_native_demonstrations_and_real_controls():
    case = starter_cases()[0]
    scores = {case["case_id"]: asdict(Score(.25, "correct-fallback", 0, 3))}
    with pytest.raises(TrainingError, match="population admission"):
        validate_reference_scores([case], scores)
    scores[case["case_id"]] = asdict(Score(1, "correct-native", 3, 3))
    validate_reference_scores([case], scores)
    case["population"] = "fallback-control"
    with pytest.raises(TrainingError, match="label is stale"):
        validate_reference_scores([case], scores)


def test_decoding_and_seed_are_step_independent():
    assert decoding(100)["temperature"] == .7
    assert decoding(100)["top_k"] == 20
    assert not decoding(100, greedy=True)["do_sample"]
    assert draw_seed(42, "case", 0) == draw_seed(42, "case", 0)
    assert draw_seed(42, "case", 0) != draw_seed(42, "case", 1)
    assert complete([1, 99], [98, 99])
    assert not complete([], 99) and not complete([1, 2], 99)


def test_adapter_seal_pins_weights_config_and_manifest(tmp_path):
    manifest = dict(base_model=BASE_MODEL, revision="a" * 40, contract_version=CONTRACT_VERSION)
    write_json(tmp_path / "experiment.json", manifest)
    write_json(tmp_path / "adapter_config.json", {"r": 16})
    (tmp_path / "adapter_model.safetensors").write_bytes(b"test fixture, not weights")
    write_json(tmp_path / "seal.json", seal_adapter(tmp_path))
    assert adapter_identity(tmp_path, "a" * 40)["experiment"] == manifest
    with pytest.raises(TrainingError, match="identity mismatch"):
        adapter_identity(tmp_path, "b" * 40)
    (tmp_path / "adapter_model.safetensors").write_bytes(b"changed")
    with pytest.raises(TrainingError, match="integrity"):
        adapter_identity(tmp_path, "a" * 40)


def test_model_identity_ignores_paths_not_architecture():
    a = {"_name_or_path": "/worker/a", "text_config": {"use_cache": True, "hidden_size": 5120}}
    b = {"_name_or_path": "/worker/b", "text_config": {"use_cache": False, "hidden_size": 5120}}
    assert model_config_identity(a) == model_config_identity(b)
    b["text_config"]["hidden_size"] = 512
    assert model_config_identity(a) != model_config_identity(b)


def test_script_pins_and_runtime_check_agree(monkeypatch):
    script = (Path(__file__).resolve().parents[1] / "gpu" / "train_verified.py").read_text()
    pins = dict(re.findall(r'"([a-z-]+)==([0-9.]+)"', script.split('# ///', 2)[1]))
    assert pins == contract_module.GPU_VERSIONS
    monkeypatch.setattr(contract_module.importlib.metadata, "version", lambda name: pins[name])
    assert contract_module.runtime_versions() == pins
    monkeypatch.setattr(contract_module.importlib.metadata, "version", lambda name: "0.0.0")
    with pytest.raises(TrainingError, match="dependency versions"):
        contract_module.runtime_versions()


def probe_rows():
    return [dict(case_id=cid, draw=d, reward=float(d), truncated=False, correct=bool(d),
                 completion_tokens=10) for cid in ("one", "two") for d in range(2)]


def test_probe_gate_exact_identity_and_nontruncated_variation(tmp_path):
    rows, contract = probe_rows(), {"generations": 2, "revision": "test"}
    report = probe_report(rows, contract)
    assert report["admitted"] and report["informative_groups"] == 2
    write_json(tmp_path / "probe.json", report)
    write_jsonl(tmp_path / "probe-rollouts.jsonl", rows)
    assert validate_probe(tmp_path / "probe.json", contract, ["one", "two"]) == report["digest"]
    with pytest.raises(TrainingError, match="exactly the train"):
        validate_probe(tmp_path / "probe.json", contract, ["one", "holdout"])
    with pytest.raises(TrainingError, match="mismatch"):
        validate_probe(tmp_path / "probe.json", dict(contract, revision="changed"), ["one", "two"])
    for r in rows:
        r["truncated"] = not r["correct"]
    assert not probe_report(rows, contract)["admitted"]
    rows[1]["draw"] = 0
    with pytest.raises(TrainingError, match="draw IDs"):
        probe_report(rows, contract)


def test_zero_signal_grpo_is_logged_as_a_fraction_never_aborted_or_shaped():
    """A no-spread group carries no gradient; it is counted, not fatal.

    The consecutive-group abort this replaces would fire by chance inside an
    arm-C dose (about 0.79**20 per starting point at seed 1111's informative
    rate). Many more than the old `max_no_signal` in a row must now pass,
    rewards must stay unshaped zeros, and the fraction must be logged.
    """
    from types import SimpleNamespace
    case = dict(starter_cases()[0], split="train")
    verifier = SimpleNamespace(score=lambda *a: Score(0, "incorrect", 0, 3))
    reward = Reward([case], verifier, eos_token_id=[98, 99], generations=2, max_no_signal=2)
    logged = []
    kwargs = dict(completions=["bad", "bad"], case_id=[case["case_id"]] * 2, completion_ids=[[99], [98]],
                  log_metric=lambda name, value: logged.append((name, value)))
    assert reward.no_signal_fraction is None
    for _ in range(25):
        assert reward(**kwargs) == [0, 0]
    assert (reward.groups, reward.no_signal_groups, reward.no_signal_fraction) == (25, 25, 1.0)
    assert ("verified/frac_no_signal_groups", 1.0) in logged
    assert ("verified/no_signal_fraction", 1.0) in logged

    # An informative group moves the cumulative fraction and logs its batch as 0.
    # Scores are keyed on the PROGRAM, never on call order: Reward scores a
    # group concurrently, so an iterator of scores races between its threads.
    def by_program(case, program):
        if program is None:  # a truncated draw is scored as no program at all
            return Score(0.5, "no-code", 0, 3)
        return Score(1.0, "correct-native", 3, 3) if program == "good" else Score(0, "incorrect", 0, 3)
    reward.verifier = SimpleNamespace(score=by_program)
    mixed = dict(kwargs, completions=["```python\nbad\n```", "```python\ngood\n```"])
    logged.clear()
    assert reward(**mixed) == [0, 1.0]
    assert (reward.groups, reward.no_signal_groups) == (26, 25)
    assert ("verified/frac_no_signal_groups", 0.0) in logged
    assert ("verified/no_signal_fraction", 25 / 26) in logged

    # A truncated draw is masked, so its distinct reward cannot make the group
    # informative: the rewards differ (0 vs 0.5) and the group still counts.
    assert reward(**dict(mixed, completion_ids=[[99], [7]])) == [0, 0.5]
    assert (reward.groups, reward.no_signal_groups) == (27, 26)


def test_warmup_decay_bounds():
    values = [learning_rate(s, 20, 1.0, .1) for s in range(1, 21)]
    assert values[:2] == [.5, 1.0]
    assert all(0 < v <= 1 for v in values)
    assert values[-1] < values[3]


def evaluation_rows():
    return [dict(case_id=cid, family=cid, population="coverage", capabilities=["csv"],
                 draw=d, seed=d, correct=False, native=False, truncated=False, completion_tokens=2)
            for cid in ("one", "two") for d in range(2)]


def test_paired_bootstrap_keeps_families_and_seeds_matched():
    base = evaluation_rows()
    candidate = [dict(r, correct=True, native=True) for r in base]
    report = paired_comparison(base, candidate, resamples=100)
    assert report["families"] == 2 and report["metrics"]["correct"]["ci95"] == [1, 1]
    linked_base = [dict(r, split_group="shared source") for r in base]
    linked_candidate = [dict(r, split_group="shared source") for r in candidate]
    assert paired_comparison(linked_base, linked_candidate, resamples=100)["independent_clusters"] == 1
    candidate[0]["seed"] = 100
    with pytest.raises(TrainingError, match="seed mismatch"):
        paired_comparison(base, candidate)
    with pytest.raises(TrainingError, match="equal draw"):
        summarize(base[:-1])


def test_a_family_spanning_split_groups_links_them_into_one_component():
    """eval-2 bank v1: `stdin-digit-run-sum` spans 23 source groups. The family
    is one unit for the macro and its groups one component for the resampling;
    a second family in one of those groups rides in the same component."""
    from pipeline.training_metrics import split_components

    def case(cid, family, group):
        return [dict(case_id=cid, family=family, split_group=group, population="coverage",
                     capabilities=["csv"], draw=d, seed=d, correct=False, native=False,
                     truncated=False, completion_tokens=2) for d in range(2)]
    base = case("one-x", "one", "gA") + case("one-y", "one", "gB") + case("two", "two", "gB") + case("three", "three", "gC")
    candidate = [dict(r, correct=True, native=True) for r in base]
    report = paired_comparison(base, candidate, resamples=100)
    assert report["families"] == 3 and report["independent_clusters"] == 2
    assert report["metrics"]["correct"]["delta"] == 1.0
    assert split_components({("one", "gA"), ("one", "gB"), ("two", "gB"), ("three", "gC")}) == \
        {"one": "one", "two": "one", "three": "three"}


def test_a_capability_regression_is_reported_and_no_longer_a_floor():
    """Nine hard floors on ~180-draw sub-metrics is what made seed 1111's
    selector blind (`PLAN.md` Step 1.1). Gate A and the population retention
    rule carry the correctness constraint now; `by_capability` is evidence in
    `best.json`, read after the fact, not a veto cast on one draw of noise."""
    baseline = summarize(evaluation_rows())
    baseline["by_capability"]["csv"]["correct"] = 1
    gate = CheckpointGate(baseline)
    # Family "one" turns correct and native: the coverage macro selection reads
    # rises by .5, and the csv capability sits at .5, below the 1 set above.
    candidate = summarize([dict(r, correct=True, native=True) if r["family"] == "one" else r
                           for r in evaluation_rows()])
    assert candidate["by_capability"]["csv"]["correct"] == .5
    assert gate.observe(10, candidate) is None, "selection never stops training"
    assert gate.best_step == 10
    assert gate.report()["by_capability"]["csv"]["correct"] == .5


def test_standalone_comparison_rejects_unmatched_contract(tmp_path):
    from pipeline.training_report import compare
    a, b = tmp_path / "base", tmp_path / "candidate"
    a.mkdir()
    b.mkdir()
    manifest = dict(stage="eval", base_model=BASE_MODEL, revision="a" * 40, bundle_digest="bundle",
        smoke=True, decoding=decoding(32), enable_thinking=False, tokenizer_sha256="tok",
        model_config_sha256="config", code_sha256={}, versions={}, hardware={},
        args=dict(seed=1111, eval_split="test", eval_draws=2, greedy=False))
    for p in (a, b):
        write_json(p / "experiment.json", manifest)
        write_jsonl(p / "evaluations.jsonl", evaluation_rows())
    assert compare(a, b)["paired"]["metrics"]["correct"]["delta"] == 0
    manifest["decoding"] = decoding(64)
    write_json(b / "experiment.json", manifest)
    with pytest.raises(TrainingError, match="unmatched evaluation contract"):
        compare(a, b)
