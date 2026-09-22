import pytest

from pipeline.positive_control_targets import build_targets
from pipeline.training_types import TrainingError


def _case(case_id, population):
    return {
        "case_id": case_id, "family": "fam-" + case_id, "task": "rewrite " + case_id,
        "population": population, "split": "train", "tests": [],
    }


def _rows(cases, samples=2):
    completions, grades = [], []
    for draw in range(samples):
        for case in cases:
            for arm in ("bare", "subset-spec"):
                program = "print(%r)" % (case["case_id"] + str(draw))
                key = {"case_id": case["case_id"], "draw": draw, "arm": arm}
                completions.append(dict(key, completion="```python\n%s\n```" % program))
                want = "correct-native" if case["population"] == "coverage" else "correct-control"
                grades.append(dict(key, status=want if arm == "subset-spec" else "incorrect",
                                   native=case["population"] == "coverage" and arm == "subset-spec"))
    return completions, grades


def test_builds_bare_prompt_targets_with_population_retention():
    cases = [_case("a", "coverage"), _case("b", "fallback-control")]
    completions, grades = _rows(cases)
    rows, report = build_targets(cases, completions, grades, samples=2,
                                 coverage_keep=2, control_keep=1, run_id="paid-1")
    assert [row["case_id"] for row in rows] == ["a", "a", "b"]
    assert all("subset specification" not in row["messages"][0]["content"] for row in rows)
    assert all(row["source"]["arm"] == "subset-spec" for row in rows)
    assert report["populations"] == {"coverage": 2, "fallback-control": 1}
    assert report["rejected"]["over-retention-cap"] == 1


def test_refuses_incomplete_evidence():
    cases = [_case("a", "coverage")]
    completions, grades = _rows(cases)
    with pytest.raises(TrainingError, match="complete, exactly paired"):
        build_targets(cases, completions[:-1], grades, samples=2)


def test_retention_control_must_remain_non_native():
    cases = [_case("b", "fallback-control")]
    completions, grades = _rows(cases)
    grades[1]["native"] = True
    rows, report = build_targets(cases, completions, grades, samples=2)
    assert len(rows) == 1
    assert report["rejected"]["control-became-native"] == 1
