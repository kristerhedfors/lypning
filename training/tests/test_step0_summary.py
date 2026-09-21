"""Saved-evidence reads must be complete, comparable and safe to publish."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("step0_summary", ROOT / ".github/scripts/step0_summary.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
PRIVATE = "PRIVATE_CASE_PROGRAM_EXPECTED_STDOUT"


def cases(n=4, split="dev"):
    return [{"case_id": PRIVATE + split + str(i), "family": PRIVATE + str(i // 2),
             "split_group": PRIVATE + str(i // 2), "split": split,
             "population": "coverage", "task": PRIVATE, "reference": PRIVATE,
             "tests": [{"stdout": PRIVATE}]} for i in range(n)]


def rows(cs, step=0):
    return [dict(c, step=step, draw=d, seed=1111, status="correct-native",
                 correct=True, native=True, truncated=False, completion_tokens=100,
                 completion=PRIVATE, capabilities=[PRIVATE], refusals=[])
            for c in cs for d in range(4)]


def fallback(row, kind="module"):
    row.update(status="correct-fallback", native=False,
               refusals=[[i, "lypning-l: unsupported: %s: %s" % (kind, PRIVATE)] for i in range(3)])


def evidence():
    dev, train = cases(), cases(split="train")
    return {"pilot/bundle.json": {"cases": dev + train},
            "eval2/bundle.json": {"cases": cases(10)},
            "base-dev/evaluations.jsonl": rows(dev),
            "probe/probe-rollouts.jsonl": rows(train),
            **{stage + "/evaluations.jsonl": [r for step in steps for r in rows(dev, step)]
               for stage, steps in m.STEPS.items()}}


def test_macro_is_family_weighted_and_population_slices_keep_small_families():
    cs = cases(10)
    cs[0]["family"] = "singleton"
    rs = rows(cs)
    for r in rs[:4]:
        fallback(r)
    stats = m.metrics(rs)
    assert stats["correct_native"] == pytest.approx(5 / 6)
    assert stats["draw_weighted_native"] == .9
    sizes = m.family_sizes(cs)
    assert sizes["families"] == 6
    assert sizes["size_histogram"] == {1: 2, 2: 4}
    assert sizes["families_under_five"] == 6


def test_native_gain_with_flat_correctness_meets_step0_rule():
    cs = cases()
    base = rows(cs)
    for r in base[:4]:
        fallback(r)
    result = m.checkpoints(base + rows(cs, 25), cs, (0, 25), base)
    assert result[1]["native_delta_pp"] == 25
    assert result[1]["correct_delta_pp"] == 0
    assert result[1]["meets_step0_rule"]
    assert not result[0]["meets_step0_rule"]
    worse = rows(cs, 25)
    worse[0].update(status="incorrect", correct=False, native=False)
    assert not m.checkpoints(base + worse, cs, (0, 25), base)[1]["meets_step0_rule"]


@pytest.mark.parametrize("fault", ["empty", "missing_draw", "duplicate", "whole_case",
                                  "metadata", "seed", "status", "boolean", "missing_step",
                                  "baseline", "step_type"])
def test_missing_or_inconsistent_evidence_never_becomes_a_negative(fault):
    data = evidence()
    rs = data["sft/evaluations.jsonl"]
    if fault == "empty":
        rs.clear()
    elif fault == "missing_draw":
        rs.pop()
    elif fault == "duplicate":
        rs[-1] = deepcopy(rs[-2])
    elif fault == "whole_case":
        data["sft/evaluations.jsonl"] = [r for r in rs if r["case_id"] != rs[0]["case_id"]]
    elif fault == "metadata":
        rs[-1]["family"] = "changed"
    elif fault == "seed":
        rs[-1]["seed"] = 2222
    elif fault == "status":
        rs[-1]["status"] = PRIVATE
    elif fault == "boolean":
        rs[-1]["native"] = PRIVATE
    elif fault == "missing_step":
        data["sft/evaluations.jsonl"] = [r for r in rs if r["step"] != 75]
    elif fault == "baseline":
        fallback(rs[0])
    elif fault == "step_type":
        rs[-1]["step"] = True
    with pytest.raises(ValueError):
        m.summarise(data)


def test_probe_is_unpaired_and_refusals_count_draws_not_test_inputs():
    data = evidence()
    probe = data["probe/probe-rollouts.jsonl"]
    fallback(probe[0])
    fallback(probe[1], kind="private-kind")
    result = m.summarise(data)
    summary = result["probe_vs_base_dev"]
    assert summary["matched_cases"] == 0
    assert summary["probe_only_cases"] == summary["base_only_cases"] == 4
    assert summary["train_cases_by_native_draw_count"] == {2: 1, 4: 3}
    assert summary["probe_refusals"] == {"fallback_draws": 2, "without_refusal": 0,
                                         "draws_by_kind": {"module": 1, "other-kind": 1}}
    encoded = json.dumps(result)
    assert PRIVATE not in encoded
    assert "private-kind" not in encoded


def test_missing_refusal_is_reported_not_invented():
    rs = rows(cases())
    fallback(rs[0])
    rs[0]["refusals"] = []
    assert m.refusal_vector(rs)["without_refusal"] == 1


def test_private_parse_failure_prints_no_exception_payload(monkeypatch, tmp_path, capsys):
    path = tmp_path / "private.json"
    path.write_text(PRIVATE)
    def download(*args, **kwargs):
        assert kwargs["revision"] == "a" * 40
        return path
    fake = SimpleNamespace(HfApi=lambda **kw: SimpleNamespace(
        repo_info=lambda *a, **k: SimpleNamespace(private=True, sha="a" * 40)),
        hf_hub_download=download)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake)
    monkeypatch.setenv("HF_TOKEN", PRIVATE)
    assert m.main() == 1
    output = capsys.readouterr()
    assert not output.out
    assert PRIVATE not in output.err
    assert "pilot/bundle.json" in output.err
