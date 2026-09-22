from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from pipeline.macro_sensitivity import simulate
from pipeline.training_metrics import family_eligibility, paired_comparison, summarize
from pipeline.training_types import TrainingError


def rows(family, n, *, correct=True, native=True, group=None, population="coverage"):
    return [dict(case_id="%s-%d" % (family, i), draw=d, family=family,
                 population=population, correct=correct, native=native,
                 split_group=group or family, seed=1111)
            for i in range(n) for d in range(16)]


def test_floor_counts_cases_and_preserves_every_population_slice():
    data = rows("large", 5) + rows("tiny", 1, native=False)
    result = summarize(data, min_family_cases=5)
    assert result["correct_native"] == 1
    assert result["macro_rule"]["excluded_cases"] == 1
    assert result["by_family"]["tiny"]["draws"] == 16
    assert result["by_population"]["coverage"]["correct_native"] == .5
    assert result["by_population"]["coverage"]["case_weighted_native"] == 5 / 6
    assert paired_comparison(data, data, resamples=100, min_family_cases=5)["families"] == 1
    with pytest.raises(TrainingError, match="no families"):
        summarize(rows("tiny", 1), min_family_cases=5)
    with pytest.raises(TrainingError, match="case IDs"):
        family_eligibility([dict(family="anonymous")], 5)


def test_excluded_family_still_links_independent_units_and_pairing_is_checked():
    data = rows("a", 5, group="left") + rows("b", 5, group="right")
    bridge = rows("bridge", 2)
    for r in bridge:
        r["split_group"] = "left" if r["case_id"].endswith("0") else "right"
    data += bridge
    candidate = copy.deepcopy(data)
    for r in candidate:
        if r["family"] == "bridge":
            r["native"] = False
    result = paired_comparison(data, candidate, min_family_cases=5, resamples=100)
    assert result["metrics"]["native"]["delta"] == 0
    assert result["independent_clusters"] == 1
    candidate[-1]["seed"] = 2222
    with pytest.raises(TrainingError, match="metadata"):
        paired_comparison(data, candidate, min_family_cases=5, resamples=100)


def test_saved_bundle_size_stress_simulation_distinguishes_primary_and_slices():
    path = Path(__file__).parents[1] / "reports/2026-09-21-codex-step0-aggregates.json"
    data = json.loads(path.read_text())
    primary = simulate(data["eval2_family_sizes"])
    assert primary["excluded_cases"] == 0
    assert primary["simulated_sd_pp"]["family_macro"] == primary["simulated_sd_pp"]["floor_macro"]
    control = simulate(data["eval2_family_sizes"]["by_population"]["fallback-control"])
    assert control["excluded_cases"] == 3
    assert control["max_single_case_weight"]["family_macro"] == .2
    assert control["max_single_case_weight"]["case_weighted"] == 1 / 113
    assert control["simulated_sd_pp"]["case_weighted"] < control["simulated_sd_pp"]["family_macro"]


def test_comparison_rejects_mixed_metric_policies(tmp_path):
    from pipeline.training_report import compare
    manifest = dict(stage="eval", base_model="test", revision="a" * 40,
                    bundle_digest="bundle", smoke=False, decoding={}, enable_thinking=False,
                    tokenizer_sha256="tok", model_config_sha256="config", code_sha256={},
                    versions={}, hardware={}, args=dict(seed=1111, eval_split="all", eval_draws=16, greedy=False))
    a, b = tmp_path / "base", tmp_path / "candidate"
    for path in (a, b):
        path.mkdir()
        (path / "experiment.json").write_text(json.dumps(manifest))
        (path / "evaluations.jsonl").write_text("\n".join(map(json.dumps, rows("a", 5) + rows("tiny", 1, native=False))))
    assert compare(a, b)["base"]["correct_native"] == .5
    manifest["metric_policy"] = {"min_family_cases": 5}
    (b / "experiment.json").write_text(json.dumps(manifest))
    with pytest.raises(TrainingError, match="metric_policy"):
        compare(a, b)
    (a / "experiment.json").write_text(json.dumps(manifest))
    assert compare(a, b)["base"]["correct_native"] == 1
