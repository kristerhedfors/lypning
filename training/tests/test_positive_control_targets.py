import importlib.util
import json
from pathlib import Path

import pytest

from pipeline.positive_control_targets import build_targets
from pipeline.training_types import TrainingError

ROOT = Path(__file__).parents[1]


def _gpu():
    spec = importlib.util.spec_from_file_location("targets_gpu", ROOT / "gpu/train_verified.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _case(case_id, population):
    return {
        "case_id": case_id, "family": "fam-" + case_id, "task": "rewrite " + case_id,
        "population": population, "split": "train", "tests": [],
    }


def _rows(cases, samples=2, passing=("subset-spec",), program=None):
    completions, grades = [], []
    for draw in range(samples):
        for case in cases:
            for arm in ("bare", "subset-spec"):
                text = (program(case, draw, arm) if program else
                        "print(%r)" % (case["case_id"] + str(draw) + arm))
                key = {"case_id": case["case_id"], "draw": draw, "arm": arm}
                completions.append(dict(key, completion="```python\n%s\n```" % text))
                want = "correct-native" if case["population"] == "coverage" else "correct-control"
                grades.append(dict(key, status=want if arm in passing else "incorrect",
                                   native=case["population"] == "coverage" and arm in passing))
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
    # The reviewed policy stays the default, and is now written down.
    assert report["arms"] == ["subset-spec"]
    assert report["length_policy"]["measure"] == "utf8-bytes"


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


def test_bare_arm_harvests_the_models_own_draws_and_records_it():
    cases = [_case("a", "coverage"), _case("b", "fallback-control")]
    completions, grades = _rows(cases, passing=("bare",))
    rows, report = build_targets(cases, completions, grades, samples=2, arms=("bare",))
    assert [(r["case_id"], r["source"]["arm"]) for r in rows] == [
        ("a", "bare"), ("a", "bare"), ("b", "bare")]
    assert report["arms"] == ["bare"] and report["arm_rows"] == {"bare": 3}
    assert report["prompt_policy"] == "bare training prompt, as drawn"
    # The same evidence under the default harvests nothing: the conditioned
    # draws all failed, and the bare ones are not a declared source.
    none, default = build_targets(cases, completions, grades, samples=2)
    assert none == [] and default["arms"] == ["subset-spec"]


def test_both_arms_are_read_draw_major_in_canonical_order():
    cases = [_case("a", "coverage")]
    completions, grades = _rows(cases, samples=2, passing=("bare", "subset-spec"))
    rows, report = build_targets(cases, completions, grades, samples=2,
                                 arms=["subset-spec", "bare"], coverage_keep=3)
    assert [(r["source"]["draw"], r["source"]["arm"]) for r in rows] == [
        (0, "bare"), (0, "subset-spec"), (1, "bare")]
    assert report["arms"] == ["bare", "subset-spec"]
    assert report["rejected"] == {"over-retention-cap": 1}


@pytest.mark.parametrize("arms", [(), "bare", ("bare", "bare"), ("tuned",)])
def test_arm_set_must_be_named_distinct_known_arms(arms):
    cases = [_case("a", "coverage")]
    completions, grades = _rows(cases)
    with pytest.raises(TrainingError, match="target arms"):
        build_targets(cases, completions, grades, samples=2, arms=arms)


def test_normalised_ast_dedup_runs_before_the_cap():
    # Draws 0-2 are one program up to formatting and comments; draw 3 differs.
    variants = ["x = 1\nprint(x)", "x = (1)  # one\n\nprint( x )", "x=1\nprint(x)\n",
                "print(2)"]
    cases = [_case("a", "coverage")]
    completions, grades = _rows(cases, samples=4,
                                program=lambda case, draw, arm: variants[draw])
    rows, report = build_targets(cases, completions, grades, samples=4, coverage_keep=2)
    assert [r["source"]["draw"] for r in rows] == [0, 3]
    assert report["rejected"] == {"duplicate-ast": 2}
    assert report["eligible_before_cap"] == {"coverage": 2}


def test_exact_duplicates_are_counted_apart_from_ast_duplicates():
    cases = [_case("a", "coverage")]
    completions, grades = _rows(cases, samples=3,
                                program=lambda case, draw, arm: "print(1)")
    rows, report = build_targets(cases, completions, grades, samples=3)
    assert len(rows) == 1 and report["rejected"] == {"duplicate-program": 2}


def test_targets_longer_than_the_generation_budget_are_dropped_by_tokens():
    long = "print(%r)" % ("y" * 400)
    cases = [_case("a", "coverage")]
    completions, grades = _rows(cases, samples=2, program=lambda case, draw, arm:
                                long if draw == 0 else "print(1)")
    counted = []
    def count(text):
        counted.append(text)
        return len(text) // 4
    rows, report = build_targets(cases, completions, grades, samples=2, max_new_tokens=100,
                                 token_count=count, tokenizer="repo@" + "a" * 40)
    assert [r["source"]["draw"] for r in rows] == [1]
    assert report["rejected"] == {"over-max-new-tokens": 1}
    assert report["length_policy"] == {"max_new_tokens": 100, "measure": "tokens",
                                       "tokenizer": "repo@" + "a" * 40,
                                       "segment": "assistant turn plus <|im_end|>"}
    # Counted as the trainer supervises it: fenced turn plus the terminator.
    assert counted[0] == "```python\n" + long + "\n```<|im_end|>"


def test_without_a_tokenizer_the_byte_bound_never_admits_an_over_long_target():
    cases = [_case("a", "coverage")]
    # 1,001 bytes of program: far under 1,024 tokens, over 1,024 bytes fenced.
    long = "#" + "z" * 1000
    completions, grades = _rows(cases, samples=2, program=lambda case, draw, arm:
                                long if draw == 0 else "print(1)")
    rows, report = build_targets(cases, completions, grades, samples=2)
    assert [r["source"]["draw"] for r in rows] == [1]
    assert report["rejected"] == {"over-max-new-tokens": 1}
    assert report["length_policy"]["measure"] == "utf8-bytes"
    assert report["length_policy"]["tokenizer"] is None
    with pytest.raises(TrainingError, match="name its tokenizer"):
        build_targets(cases, completions, grades, samples=2, token_count=len)


def _write_targets(tmp_path, rows_arms, report_arms):
    from pipeline.jsonio import sha256_of
    from pipeline.training import messages
    import hashlib
    cases = [
        {"case_id": "c", "family": "coverage-family", "task": "coverage task",
         "population": "coverage", "split": "train", "reference": "print(1)"},
        {"case_id": "r", "family": "retention-family", "task": "retention task",
         "population": "fallback-control", "split": "train", "reference": "print(2)"},
    ]
    run_id = "targets-" + "a" * 40 + "-1"
    rows = []
    for case, arm in zip(cases, rows_arms):
        program = case["reference"]
        rows.append({"case_id": case["case_id"], "family": case["family"],
                     "population": case["population"],
                     "messages": messages(case) + [{"role": "assistant",
                         "content": "```python\n%s\n```" % program}],
                     "source": {"run_id": run_id, "arm": arm, "draw": 0,
                         "program_sha256": hashlib.sha256(program.encode()).hexdigest()}})
    path = tmp_path / "sft.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    report = {"schema": 1, "run_id": run_id, "rows": len(rows), "sft_sha256": sha256_of(rows),
              "lineage": {"engine_sha256": "e" * 64}}
    if report_arms is not None:
        report["arms"] = report_arms
    (tmp_path / "sft-report.json").write_text(json.dumps(report))
    return path, {"identity": {"sha256": "e" * 64}, "cases": cases}


@pytest.mark.parametrize("rows_arms,report_arms", [
    (("bare", "bare"), ["bare"]),
    (("bare", "subset-spec"), ["bare", "subset-spec"]),
    (("subset-spec", "subset-spec"), ["subset-spec"]),
    # A report written before arms were recorded was conditioned-only.
    (("subset-spec", "subset-spec"), None),
])
def test_loader_admits_any_arm_set_the_report_declares(tmp_path, rows_arms, report_arms):
    path, bundle = _write_targets(tmp_path, rows_arms, report_arms)
    cases, rows, _ = _gpu().load_sft_targets(path, bundle)
    assert [row["source"]["arm"] for row in rows] == list(rows_arms)
    assert [case["case_id"] for case in cases] == ["c", "r"]


@pytest.mark.parametrize("rows_arms,report_arms,match", [
    (("bare", "subset-spec"), ["subset-spec"], "declared arm"),
    (("bare", "bare"), None, "declared arm"),
    (("subset-spec", "subset-spec"), ["bare"], "declared arm"),
    (("subset-spec", "subset-spec"), [], "source arm set"),
    (("subset-spec", "subset-spec"), ["tuned"], "source arm set"),
    (("subset-spec", "subset-spec"), "subset-spec", "source arm set"),
])
def test_loader_refuses_a_row_from_an_undeclared_arm(tmp_path, rows_arms, report_arms, match):
    path, bundle = _write_targets(tmp_path, rows_arms, report_arms)
    with pytest.raises(TrainingError, match=match):
        _gpu().load_sft_targets(path, bundle)
