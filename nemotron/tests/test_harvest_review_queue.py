"""Inert review/repair routing tests; no generated program is executed."""
from __future__ import annotations

import hashlib

import pytest

from harvesting.review_queue import grade, validate_assignments
from pipeline.training_types import Score, TrainingError, VerificationBlocked


@pytest.fixture
def data(tmp_path):
    raw = b"print(42)\n"
    digest = hashlib.sha256(raw).hexdigest()
    (tmp_path / "blobs").mkdir()
    (tmp_path / "blobs" / digest).write_bytes(raw)
    item = {"archive": str(tmp_path), "sources": [{"sha256": digest, "redacted": False,
                                                  "evidence_ids": ["source-event"]}]}
    case = {"case_id": "case", "task": "A complete ordinary task", "family": "family",
            "split": "train", "population": "coverage", "review": {"evidence_ids": ["source-event"]}}
    bundle = {"purpose": "pilot", "execution": {"kind": "docker"}, "cases": [case]}
    assignments = [{"source_sha256": digest, "case_id": "case"}]
    return item, case, bundle, assignments


@pytest.mark.parametrize("mutation", ["test", "dev", "unlinked", "redacted", "local", "smoke", "duplicate", "unknown"])
def test_repair_assignments_require_train_reviewed_source_and_container(data, mutation):
    item, case, bundle, assignments = data
    if mutation in ("test", "dev"):
        case["split"] = mutation
    elif mutation == "unlinked":
        case["review"]["evidence_ids"] = ["other"]
    elif mutation == "redacted":
        item["sources"][0]["redacted"] = True
    elif mutation == "local":
        bundle["execution"]["kind"] = "local-reviewed-smoke"
    elif mutation == "smoke":
        bundle["purpose"] = "smoke"
    elif mutation == "duplicate":
        assignments *= 2
    else:
        assignments[0]["source_sha256"] = "unknown"
    with pytest.raises(TrainingError):
        validate_assignments(item, assignments, bundle)


@pytest.mark.parametrize("score,action", [
    (Score(1, "correct-native", 3, 3), "verified-success-candidate"),
    (Score(.25, "correct-fallback", 0, 3), "teacher-compatibility-repair"),
    (Score(0, "incorrect", 0, 3), "teacher-correctness-repair"),
])
def test_grade_routes_verified_outcomes_without_admitting(data, score, action):
    item, case, bundle, assignments = data
    class Verifier:
        def score(self, got_case, program):
            assert got_case is case and program == "print(42)\n"
            return score
    results = list(grade(item, validate_assignments(item, assignments, bundle), Verifier()))
    assert results[0]["action"] == action and results[0]["trainable"] is False
    if action.startswith("teacher"):
        assert results[0]["teacher_request"]["task"] == case["task"]
        assert "WITHOUT repair prompt" in results[0]["teacher_request"]["training_view"]


def test_control_not_forced_native_and_mismatch_stops(data):
    item, case, bundle, assignments = data
    case["population"] = "fallback-control"
    class Verifier:
        def score(self, *args):
            return Score(1, "correct-control", 0, 3)
    selected = validate_assignments(item, assignments, bundle)
    assert list(grade(item, selected, Verifier()))[0]["action"] == "retain-control"
    class Broken:
        def score(self, *args):
            raise VerificationBlocked("engine mismatch: fixture")
    results = grade(item, selected, Broken())
    assert next(results)["action"] == "blocked-verification"
    with pytest.raises(VerificationBlocked):
        next(results)


def test_changed_source_is_not_executed(data):
    from pathlib import Path
    item, case, bundle, assignments = data
    selected = validate_assignments(item, assignments, bundle)
    (Path(item["archive"]) / "blobs" / selected[0][0]).write_bytes(b"modified")
    with pytest.raises(TrainingError, match="changed"):
        list(grade(item, selected, None))
