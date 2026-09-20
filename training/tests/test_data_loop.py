from __future__ import annotations

import json
from pathlib import Path

import pytest

from lypning.evidence import snapshot, load_snapshot
from pipeline.curriculum import starter_cases
from pipeline.data_loop import REVIEW_FIELDS, review, load_review
from pipeline.jsonio import write_json
from pipeline.round_plan import plan
from pipeline.training_types import TrainingError
from pipeline import training
from pipeline.jsonio import sha256_of


def authored_cases():
    return [dict(c, review=dict({k: "reviewed fixture" for k in REVIEW_FIELDS},
                               origin="authored", evidence_ids=[])) for c in starter_cases()]


def test_review_is_not_execution_and_binds_cases_and_split(tmp_path):
    cases = authored_cases()
    result = review(cases, purpose="smoke")
    assert not result["correctness_verified"] and result["performance_gate"] is None
    path = tmp_path / "review.json"
    write_json(path, result)
    assert load_review(path, cases, 1111, "smoke") == result
    cases[0]["task"] += " changed"
    with pytest.raises(TrainingError, match="integrity"):
        load_review(path, cases, 1111, "smoke")


def test_review_missing_intent_and_unknown_evidence_fail():
    cases = authored_cases()
    cases[0]["review"]["intent_basis"] = ""
    with pytest.raises(TrainingError, match="explicit review"):
        review(cases, purpose="smoke")
    cases = authored_cases()
    cases[0]["review"].update(origin="captured", evidence_ids=["unknown"])
    with pytest.raises(TrainingError, match="observation link"):
        review(cases, purpose="smoke")


def test_observation_sources_cannot_be_independent_components(tmp_path):
    log = tmp_path / "log"
    log.write_text(json.dumps({"program": "print('source')"}) + "\n")
    snapshot(log, tmp_path / "snapshot", "host/session")
    _, events = load_snapshot(tmp_path / "snapshot")
    cases = authored_cases()
    for c in cases[:2]:
        c["review"].update(origin="captured", evidence_ids=[events[0]["event_id"]])
    with pytest.raises(TrainingError, match="evidence leakage"):
        review(cases, [tmp_path / "snapshot"], purpose="smoke")
    cases[1]["source_group"] = cases[0]["source_group"]
    result = review(cases, [tmp_path / "snapshot"], purpose="smoke")
    assert result["linked_observations"] == 1


def config():
    path = Path(__file__).resolve().parents[1] / "round-02.example.json"
    return dict(json.loads(path.read_text()), revision="a" * 40)


def test_manual_plan_never_launches_or_invents_selected_adapters(tmp_path):
    result = plan(config(), tmp_path)
    assert result["training_started"] is False
    assert result["pending_selection"] == ["sft", "grpo"]
    assert {a["name"] for a in result["actions"]} == {"sft-smoke", "grpo-smoke", "base-dev", "sft"}
    assert all(a["missing"] for a in result["actions"])
    assert all(a["argv"][:2] == ["uv", "run"] for a in result["actions"])
    assert not list(tmp_path.iterdir())
    for changes in ({"engine": "../escape"}, {"revision": "main"}, {"steps": True}, {"generations": 1}):
        with pytest.raises(TrainingError):
            plan(dict(config(), **changes), tmp_path)


def test_plan_uses_selected_step_zero_and_requires_seal(tmp_path, monkeypatch):
    from pipeline import round_plan
    directory = tmp_path / "work/round-02/sft"
    directory.mkdir(parents=True)
    write_json(directory / "best.json", {"step": 0})
    with pytest.raises((TrainingError, OSError)):
        plan(config(), tmp_path)
    seen = []
    monkeypatch.setattr(round_plan, "adapter_identity", lambda p, r: seen.append(p))
    result = plan(config(), tmp_path)
    assert seen == [directory / "adapter-0"]
    probe = next(a for a in result["actions"] if a["name"] == "probe")
    assert probe["argv"][probe["argv"].index("--adapter") + 1].endswith("sft/adapter-0")
    assert not any(a["name"].endswith("-test") for a in result["actions"])


def test_review_and_execution_contract_survive_bundle_reload(tmp_path, monkeypatch):
    # Schema-only fixture: synthetic labels, mocked execution, NOT pilot data.
    cases = []
    for i in range(18):
        case = dict(authored_cases()[0], case_id=str(i), task="task %d" % i,
                    family=str(i), source_group=str(i), reference="print(%d)" % i,
                    population="coverage" if i < 9 else "fallback-control")
        cases.append(case)
    reviewed = review(cases)
    review_path = tmp_path / "review.json"
    write_json(review_path, reviewed)
    monkeypatch.setattr(training, "engine_identity", lambda b: {"fixture": True})
    monkeypatch.setattr(training, "execution_runner", lambda *a, **kw: None)
    monkeypatch.setattr(training.Verifier, "score", lambda self, c, p: training.Score(
        1.0, "correct-native" if c["population"] == "coverage" else "correct-control",
        3 if c["population"] == "coverage" else 0, 3))
    with pytest.raises(TrainingError, match="--review"):
        training.prepare(cases, "engine", tmp_path / "missing-review", purpose="pilot")
    with pytest.raises(TrainingError, match="--execution-image"):
        training.prepare(cases, "engine", tmp_path / "missing-image", purpose="pilot", review_path=review_path)
    image = "sha256:" + "a" * 64
    payload = training.prepare(cases, "engine", tmp_path / "bundle", purpose="pilot",
                               review_path=review_path, execution_image=image)
    path = tmp_path / "bundle/bundle.json"
    assert training.load_bundle(path, "engine") == payload
    payload["data_review"]["cases_sha256"] = "changed"
    payload["digest"] = sha256_of({k: v for k, v in payload.items() if k != "digest"})
    write_json(path, payload)
    with pytest.raises(TrainingError, match="lineage changed"):
        training.load_bundle(path, "engine")
