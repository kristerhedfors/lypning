"""The 2026-09-24 engine-mismatch policy, shared by the grade, evaluation and GRPO.

The seed-1111 arm-A pilot (HF job 6ab52a686b030d633f68e503) finished SFT and
then aborted in its base test arm on one base-model draw that reached a
lypning-l bug. Such a draw is now a counted, zero-scored draw with a private
witness, and a run fails only past 1% of its draws. The evaluation and GRPO
wiring is pinned in `test_verified_evaluation.py` and `test_stage_recipe.py`;
this file pins the shared pieces and what the metrics make of the status.
"""
from __future__ import annotations

import json

import pytest

from pipeline import mismatch_policy as policy
from pipeline import positive_control_grade as grade
from pipeline.sandbox import RunResult
from pipeline.training import Verifier
from pipeline.training_metrics import CheckpointGate, summarize
from pipeline.training_types import ENGINE_MISMATCH, Score, TrainingError, VerificationBlocked


def result(stdout="", **kwargs):
    return RunResult(**dict({"exit_code": 0, "stdout": stdout, "stderr": "", "duration_s": 0.01}, **kwargs))


CASE = {"case_id": "c", "family": "f", "population": "coverage", "split": "train", "task": "t",
        "tests": [{"stdout": "ok\n"}]}


def blocked(native):
    runs = iter([result("ok\n"), result("ok\n"), native])
    with pytest.raises(VerificationBlocked) as caught:
        Verifier("/engine", runner=lambda *a, **k: next(runs)).score(CASE, "print('ok')")
    return caught.value


def test_one_bound_one_status_one_file_for_every_scorer():
    """The Step 2 grade re-exports the policy; it does not keep a copy."""
    for name in ("ENGINE_MISMATCH_STATUS", "ENGINE_MISMATCH_FILE", "ENGINE_MISMATCH_BOUND_PERCENT",
                 "EngineMismatchBound", "check_mismatch_bound", "over_mismatch_bound",
                 "engine_mismatches", "mismatch_score"):
        assert getattr(grade, name) is getattr(policy, name), name
    assert issubclass(policy.EngineMismatchBound, VerificationBlocked)
    with pytest.raises(policy.EngineMismatchBound) as caught:
        policy.check_mismatch_bound(2, 100)
    assert str(caught.value) == "engine-mismatch draws 2 of 100 exceed the 1% bound"
    assert caught.value.witness is None and caught.value.digest is None
    policy.check_mismatch_bound(1, 100)


def test_a_wrong_native_answer_is_counted_and_a_native_timeout_is_not():
    """Read against the REAL verifier's witness, so the timeout index cannot drift."""
    wrong = blocked(result("ko\n"))
    crash = blocked(result("", exit_code=1, stderr="SyntaxError\n"))
    timeout = blocked(result("", exit_code=None, timed_out=True))
    for exc in (wrong, crash, timeout):
        assert exc.kind == ENGINE_MISMATCH
    assert policy.counted_on_gpu(wrong) and policy.counted_on_gpu(crash)
    assert policy.native_timeout(timeout) and not policy.counted_on_gpu(timeout)
    assert not policy.counted_on_gpu(VerificationBlocked("harness", {"harness_error": "x"}))
    assert not policy.counted_on_gpu(VerificationBlocked("engine/oracle/verifier drift during run"))
    score = policy.mismatch_score(CASE, wrong)
    assert score == Score(0.0, "engine-mismatch", 0, 1, (), 0)
    assert not score.correct and not score.native and score.reward == 0


def test_the_wrapper_counts_writes_privately_and_passes_everything_else(tmp_path):
    exc = blocked(result("ko\n"))
    path = tmp_path / policy.ENGINE_MISMATCH_FILE

    class Raising:
        def __init__(self, error):
            self.error = error

        def score(self, case, program):
            if self.error:
                raise self.error
            return Score(1, "correct-native", 1, 1)

    scoring = policy.MismatchScoring(Raising(exc), path, planned=200)
    assert scoring.score(CASE, "p1").status == "engine-mismatch"
    assert scoring.score(CASE, "p2").status == "engine-mismatch"
    assert scoring.count == 2
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [(r["program"], r["mismatch"], r["digest"]) for r in rows] == [("p1", 1, exc.digest),
                                                                          ("p2", 2, exc.digest)]
    assert rows[0]["witness"]["expected_stdout"] == "ok\n" and rows[0]["tests"] == CASE["tests"]
    assert rows[0]["source"] == "rollout"
    with pytest.raises(policy.EngineMismatchBound, match="draws 3 of 200"):
        scoring.score(CASE, "p3")
    assert len(path.read_text().splitlines()) == 3, "the witness that crossed the bound is kept"
    assert policy.MismatchScoring(Raising(None), path, planned=1).score(CASE, "x").status == "correct-native"
    for other in (VerificationBlocked("harness"), blocked(result("", timed_out=True, exit_code=None))):
        with pytest.raises(VerificationBlocked) as caught:
            policy.MismatchScoring(Raising(other), tmp_path / "none.jsonl", planned=10 ** 6).score(CASE, "x")
        assert caught.value is other
    assert not (tmp_path / "none.jsonl").exists()
    with pytest.raises(TrainingError):
        policy.MismatchScoring(Raising(None), path, planned=0)
    with pytest.raises(TrainingError, match="private witness file"):
        policy.MismatchScoring(Raising(exc), None, planned=200)


def rows(statuses, family="f", population="coverage"):
    out = []
    for i, status in enumerate(statuses):
        score = Score(1 if status.startswith("correct") else 0, status, 1 if status == "correct-native" else 0, 1)
        out.append({"case_id": "%s%d" % (family, i), "draw": 0, "family": family, "population": population,
                    "status": status, "correct": score.correct, "native": score.native})
    return out


def test_summarize_counts_the_status_in_every_slice_zero_included():
    clean = summarize(rows(["correct-native", "incorrect"]))
    assert clean["engine_mismatches"] == 0
    assert clean["by_population"]["coverage"]["engine_mismatches"] == 0
    hit = summarize(rows(["correct-native", "engine-mismatch", "incorrect", "correct-fallback"]))
    assert hit["engine_mismatches"] == 1 and hit["statuses"]["engine-mismatch"] == 1
    assert hit["by_family"]["f"]["engine_mismatches"] == 1
    assert hit["by_population"]["coverage"]["engine_mismatches"] == 1
    # Counted against the arm: a draw, and not a correct one.
    assert hit["draws"] == 4 and hit["case_weighted_correct"] == 0.5 and hit["case_weighted_native"] == 0.25


def test_the_checkpoint_gate_reads_a_mismatch_as_a_wrong_draw_and_nothing_else():
    """Selection is unchanged but for the one thing the status means: not correct."""
    def metrics(statuses):
        return summarize(rows(statuses, "a") + rows(statuses, "b", "fallback-control"))

    base = metrics(["correct-native", "incorrect", "correct-fallback", "incorrect"])
    as_incorrect = metrics(["correct-native", "correct-native", "correct-native", "incorrect"])
    as_mismatch = metrics(["correct-native", "correct-native", "correct-native", "engine-mismatch"])
    for key in ("correct", "correct_native"):
        assert as_mismatch[key] == as_incorrect[key]
    one, two = CheckpointGate(base), CheckpointGate(base)
    one.observe(1, as_incorrect)
    two.observe(1, as_mismatch)
    strip = ("statuses", "engine_mismatches")

    def selection(gate):
        return [{k: v for k, v in o.items() if k not in strip} for o in gate.report()["observed"]]
    assert selection(one) == selection(two)


def test_the_paired_report_carries_both_arms_counts_over_every_draw(tmp_path):
    """`training_report.compare` reports each arm's count beside the paired delta.

    Over every draw: family "b" is below the primary macro's case minimum, so
    the summaries' top-level count leaves its mismatch out, and the report's
    count keeps it -- a mismatch counts against the arm that drew it.
    """
    from pipeline.jsonio import write_json, write_jsonl
    from pipeline.training_contract import BASE_MODEL, decoding
    from pipeline.training_report import compare
    manifest = dict(stage="eval", base_model=BASE_MODEL, revision="a" * 40, bundle_digest="bundle",
                    smoke=True, decoding=decoding(32), enable_thinking=False, tokenizer_sha256="tok",
                    model_config_sha256="config", code_sha256={}, versions={}, hardware={},
                    metric_policy={"min_family_cases": 2},
                    args=dict(seed=1111, eval_split="test", eval_draws=1, greedy=False))
    arms = {"base": rows(["correct-native", "incorrect", "correct-fallback", "incorrect"], "a")
            + rows(["engine-mismatch"], "b"),
            "candidate": rows(["correct-native", "engine-mismatch", "engine-mismatch", "incorrect"], "a")
            + rows(["engine-mismatch"], "b")}
    for arm, arm_rows in arms.items():
        (tmp_path / arm).mkdir()
        write_json(tmp_path / arm / "experiment.json", manifest)
        write_jsonl(tmp_path / arm / "evaluations.jsonl", arm_rows)
    report = compare(tmp_path / "base", tmp_path / "candidate")
    assert report["engine_mismatches"] == {"base": 1, "candidate": 3}
    assert report["base"]["engine_mismatches"] == 0 and report["candidate"]["engine_mismatches"] == 2


@pytest.mark.parametrize("script", ["round02_pilot.sh", "round02_eval2.sh"])
def test_the_round_scripts_print_both_counts_beside_the_paired_delta(tmp_path, script):
    import re
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    text = (root / "training" / "hf" / script).read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if 'print("== report"' in line]
    assert lines
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"paired": {"delta": 0.01}, "engine_mismatches": {"base": 2, "candidate": 0}}))
    for line in lines:
        program = re.search(r"python3 -c '([^']*)'", line).group(1)
        done = subprocess.run([sys.executable, "-c", program, str(report)], cwd=tmp_path,
                              env={"PYTHONPATH": str(root / "training")},
                              capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stderr
        assert done.stdout.rstrip().endswith('engine_mismatches {"base": 2, "candidate": 0}')
