"""What the grading path may and may not charge the model for.

Three rules, and every test here pins one of them. An engine that disagreed with
CPython is our bug and leaves the denominator (CLAUDE.md invariant 1). A refusal
is labelled with the engine's own word for it, not "wrong answer". And a pass is
taken away only by a discriminator that says so in as many words — a false
accusation makes a frozen held-out case unpassable, which is worse than the cheat
it would have prevented.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import evaluate as E
from pipeline import sample as S
from pipeline.acceptance import Verdict
from pipeline.classify import ATTEMPT_CATEGORIES, classify, is_category, stratum
from pipeline.evaluate import (GENUINE, NOT_GENUINE, UNDECIDED, judge_authorship, score_verdict,
                               summarize_run)
from pipeline.jsonio import read_jsonl, write_jsonl
from pipeline.sandbox import RunResult

DATA = Path(__file__).resolve().parents[1] / "data"

# A faithful rewrite of ntx-ca1a768f3164, the frozen held-out case round 1's
# syntactic guard made unpassable: it wraps and dedents the text itself, and the
# only literal it carries is the input the task handed it.
HONEST_REWRITE = (
    'text = "one two three four five six"\n'
    'line = ""\n'
    'out = []\n'
    'for word in text.split():\n'
    '    if line and len(line) + 1 + len(word) > 12:\n'
    '        out.append(line)\n'
    '        line = word\n'
    '    else:\n'
    '        line = word if not line else line + " " + word\n'
    'if line:\n'
    '    out.append(line)\n'
    'print("\\n".join(out))\n'
    'for raw in "    a\\n    b\\n".split("\\n")[:-1]:\n'
    '    print(raw[4:])\n'
)


def _case(case_id):
    for row in read_jsonl(DATA / "corpus.jsonl"):
        if row["id"] == case_id:
            return row
    raise AssertionError("case not in the corpus: %s" % case_id)


def _attempts(tmp_path, rows):
    run = tmp_path / "run"
    run.mkdir()
    write_jsonl(run / "attempts.jsonl", rows)
    return run


# ------------------------------------------- the regression, and the seam


def test_a_faithful_rewrite_is_not_a_cheat_just_because_it_quotes_its_input():
    """The regression two reviewers reproduced, now closed at the source.

    Held-out case ntx-ca1a768f3164 asks for textwrap.fill to be rewritten without
    the import. Every line of the expected output is a substring of the input
    string the prompt handed over, so the SYNTACTIC guard called this honest
    reimplementation a cheat -- six false positives for every real catch, and it
    made a frozen held-out case unpassable by a correct program.

    This test originally pinned the workaround: the guard still fired and the
    seam declined to let it decide the score. The behavioural discriminator no
    longer fires at all, so the assertion moved from "we ignore the wrong answer"
    to "the answer is right", which is what the test's name always claimed.
    """
    case = _case("ntx-ca1a768f3164")
    want = case["test"]["expect_stdout"]
    assert S.looks_like_literal_output(HONEST_REWRITE, want) == ""
    rec = score_verdict(case, HONEST_REWRITE, Verdict(True, "pass", "tier-1 on lypning"))
    assert rec["passed"] is True and rec["failure_category"] == ""
    # UNDECIDED, not GENUINE: this case has no input, so the perturbation cannot
    # run and nothing PROVED the program computed its answer. Doubt is the honest
    # record, and it does not cost the attempt its pass.
    assert rec["authorship"] == UNDECIDED


def test_a_program_that_spells_the_answer_out_is_still_caught():
    """The other half: closing the false positives may not open a false negative."""
    case = _case("ntx-ca1a768f3164")
    want = case["test"]["expect_stdout"]
    cheat = "print(%r)" % want.rstrip("\n")
    assert S.looks_like_literal_output(cheat, want) != ""
    rec = score_verdict(case, cheat, Verdict(True, "pass", "tier-1 on lypning"))
    assert rec["passed"] is False and rec["authorship"] == NOT_GENUINE


@pytest.mark.skipif(__import__("pipeline.engines", fromlist=["x"]).engine_path("lypning") is None,
                    reason="the engines are not built")
def test_the_frozen_holdout_case_is_passable_end_to_end():
    from pipeline.acceptance import run_test
    case = _case("ntx-ca1a768f3164")
    verdict = run_test(case["test"], HONEST_REWRITE)
    assert verdict.passed and verdict.detail.startswith("tier-1")
    assert score_verdict(case, HONEST_REWRITE, verdict)["passed"] is True


def test_a_discriminator_that_names_a_verdict_can_take_the_pass_away(monkeypatch):
    monkeypatch.setattr(S, "authorship_verdict",
                        lambda case, program: ("recited", "it never computes anything"),
                        raising=False)
    rec = score_verdict({"prompt": "p", "test": {"expect_stdout": "x\n"}},
                        "print('x')", Verdict(True, "pass"))
    assert rec["passed"] is False
    assert rec["reason"] == "not-genuine" and rec["failure_category"] == "not-genuine"
    assert rec["detail"] == "it never computes anything"


def test_a_discriminator_that_cannot_tell_leaves_the_score_alone_and_says_so(monkeypatch):
    monkeypatch.setattr(S, "authorship_verdict",
                        lambda case, program: ("cannot-tell", "no evidence either way"),
                        raising=False)
    rec = score_verdict({"prompt": "p", "test": {"expect_stdout": "x\n"}},
                        "print(6*7)", Verdict(True, "pass"))
    assert rec["passed"] is True
    assert rec["authorship"] == UNDECIDED and rec["authorship_detail"] == "no evidence either way"


def test_a_clean_answer_carries_no_doubt(monkeypatch):
    monkeypatch.setattr(S, "authorship_verdict", lambda case, program: "genuine",
                        raising=False)
    rec = score_verdict({"prompt": "p", "test": {"expect_stdout": "x\n"}},
                        "print(6*7)", Verdict(True, "pass"))
    # Recorded explicitly: "looked and found nothing" must be distinguishable
    # from "nothing looked", or the attempt cannot be audited afterwards.
    assert rec["passed"] is True and rec["authorship"] == GENUINE


def test_a_yes_no_answer_is_doubt_and_never_a_verdict(monkeypatch):
    # A two-state guard cannot distinguish "no evidence" from "computed", and it
    # is that missing third answer the false positives came out of.
    monkeypatch.setattr(S, "authorship_verdict", lambda case, program: True, raising=False)
    assert judge_authorship({"test": {}}, "print(1)")[0] == UNDECIDED
    monkeypatch.setattr(S, "authorship_verdict", lambda case, program: False, raising=False)
    assert judge_authorship({"test": {}}, "print(1)")[0] == UNDECIDED


def test_an_answer_in_a_shape_this_seam_cannot_read_is_doubt(monkeypatch):
    monkeypatch.setattr(S, "authorship_verdict",
                        lambda case, program: {"confidence": 0.61}, raising=False)
    assert judge_authorship({"test": {}}, "print(1)")[0] == UNDECIDED
    monkeypatch.setattr(S, "authorship_verdict", lambda case, program: None, raising=False)
    assert judge_authorship({"test": {}}, "print(1)")[0] == UNDECIDED


def test_an_unwired_seam_says_so_instead_of_passing_everything_silently(monkeypatch):
    for name in E._ENTRY_POINTS:
        monkeypatch.delattr(S, name, raising=False)
    status, why = judge_authorship({"test": {}}, "print(1)")
    assert status == UNDECIDED and "no discriminator" in why


def test_a_broken_discriminator_does_not_fail_a_paid_run(monkeypatch):
    def boom(case, program):
        raise RuntimeError("ast blew up")
    monkeypatch.setattr(S, "authorship_verdict", boom, raising=False)
    status, why = judge_authorship({"test": {}}, "print(1)")
    assert status == UNDECIDED and "ast blew up" in why


def test_a_discriminator_is_called_in_whatever_shape_it_declares(monkeypatch):
    seen = {}

    def by_name(program, prompt, expected_stdout):
        seen.update(program=program, prompt=prompt, expected_stdout=expected_stdout)
        return "genuine"

    monkeypatch.setattr(S, "authorship_verdict", by_name, raising=False)
    case = {"prompt": "rewrite it", "test": {"expect_stdout": "x\n"}}
    assert judge_authorship(case, "print('x')") == (GENUINE, "")
    assert seen == {"program": "print('x')", "prompt": "rewrite it", "expected_stdout": "x\n"}


def test_a_discriminator_with_an_unreadable_shape_answers_doubt(monkeypatch):
    monkeypatch.setattr(S, "authorship_verdict",
                        lambda whatever, program, extra: "recited", raising=False)
    assert judge_authorship({"test": {}}, "print(1)")[0] == UNDECIDED


def test_todays_sample_module_cannot_flip_a_single_attempt():
    # The seam is live against whatever pipeline.sample exposes right now. What
    # it exposes today is two-state, so the most it can do is record doubt.
    case = _case("ntx-ca1a768f3164")
    status, _ = judge_authorship(case, HONEST_REWRITE)
    assert status in (GENUINE, UNDECIDED)


def test_the_eval_loop_records_what_the_seam_decided(tmp_path, monkeypatch):
    from pipeline.backends import ChatBackend, Completion
    from pipeline.evaluate import Evaluation
    from pipeline.schema import make_case

    class _Canned(ChatBackend):
        def complete(self, messages, **kw):
            return Completion(text="```python\nprint(2550)\n```", reasoning=None,
                              prompt_tokens=1, completion_tokens=1, latency_s=0.0,
                              finish_reason="stop")

    case = make_case(prompt="sum the evens",
                     test={"kind": "stdout", "expect_stdout": "2550\n"},
                     category="wrong-output")
    ev = Evaluation(_Canned("http://canned/v1", "canned"), [case], tmp_path / "run")

    monkeypatch.setattr(S, "authorship_verdict",
                        lambda case, program: ("not-genuine", "it prints the answer"),
                        raising=False)
    rec = ev._one(case, 0)
    assert rec["passed"] is False and rec["reason"] == "not-genuine"
    assert rec["failure_category"] == "not-genuine"

    monkeypatch.setattr(S, "authorship_verdict", lambda case, program: "unknown",
                        raising=False)
    rec = ev._one(case, 0)
    assert rec["passed"] is True and rec["authorship"] == UNDECIDED


# ----------------------------------------------- invariant 1 at the seam


def test_an_engine_mismatch_is_not_the_models_failure():
    v = Verdict(False, "engine-mismatch", "lypning disagrees with CPython: stdout: want 'x'")
    rec = score_verdict({"test": {}}, "print('x')", v)
    assert rec["reason"] == "engine-mismatch"
    assert rec["failure_category"] == "engine-mismatch"


def test_engine_mismatches_leave_the_denominator_and_are_reported(tmp_path):
    run = _attempts(tmp_path, [
        {"case_id": "a", "sample": 0, "passed": True, "how": "fenced-python"},
        {"case_id": "b", "sample": 0, "passed": False, "how": "fenced-python",
         "reason": "stdout", "failure_category": "wrong-output"},
        {"case_id": "c", "sample": 0, "passed": False, "how": "fenced-python",
         "reason": "engine-mismatch", "detail": "lypning disagrees with CPython: stdout",
         "failure_category": "engine-mismatch"},
    ])
    s = summarize_run(run)
    assert s["engine_mismatches"] == 1 and s["engine_mismatch_cases"] == ["c"]
    assert s["engine_mismatch_detail"] == {"c: lypning disagrees with CPython: stdout": 1}
    assert s["n_attempts"] == 2 and s["n_attempts_billed"] == 3
    assert s["cases_evaluated"] == 2 and s["pass_rate"] == 0.5
    assert s["failures_by_category"] == {"wrong-output": 1}


def test_two_mismatches_on_two_cases_stay_two_lines(tmp_path):
    run = _attempts(tmp_path, [
        {"case_id": "y", "sample": 0, "passed": False, "reason": "engine-mismatch",
         "detail": "lypning disagrees with CPython: exit: want 0, got 1"},
        {"case_id": "z", "sample": 0, "passed": False, "reason": "engine-mismatch",
         "detail": "lypning disagrees with CPython: exit: want 0, got 1"},
        {"case_id": "a", "sample": 0, "passed": True},
    ])
    s = summarize_run(run)
    assert s["engine_mismatch_cases"] == ["y", "z"]
    assert len(s["engine_mismatch_detail"]) == 2


def test_a_case_whose_every_draw_mismatched_is_not_a_zero(tmp_path):
    run = _attempts(tmp_path, [
        {"case_id": "a", "sample": 0, "passed": True},
        {"case_id": "z", "sample": 0, "passed": False, "reason": "engine-mismatch"},
        {"case_id": "z", "sample": 1, "passed": False, "reason": "engine-mismatch"},
    ])
    s = summarize_run(run)
    assert "z" not in s["per_case"] and s["pass_rate"] == 1.0


def test_one_rule_decides_what_is_scored():
    # `nt compare` and `nt slices` read the same predicate summarize_run does, so
    # a mismatch cannot be excluded from one printout and scored a zero in the next.
    rows = [{"case_id": "a", "passed": True},
            {"case_id": "b", "passed": False, "reason": "engine-mismatch"},
            {"case_id": "c", "passed": False, "harness_error": "500"}]
    assert [a["case_id"] for a in E.scored_attempts(rows)] == ["a"]


def test_non_genuine_passes_and_doubt_are_counted_separately(tmp_path):
    run = _attempts(tmp_path, [
        {"case_id": "a", "sample": 0, "passed": True},
        {"case_id": "b", "sample": 0, "passed": True, "authorship": "undecided",
         "authorship_detail": "3 of 3 output lines appear verbatim in the source"},
        {"case_id": "c", "sample": 0, "passed": False, "reason": "not-genuine",
         "failure_category": "not-genuine", "detail": "it never computes anything"},
    ])
    s = summarize_run(run)
    assert s["non_genuine_passes"] == 1 and s["non_genuine_cases"] == ["c"]
    assert s["authorship_undecided"] == 1 and s["authorship_undecided_cases"] == ["b"]
    # A doubted pass is still a pass; a non-genuine one is still in the denominator.
    assert s["n_attempts"] == 3 and s["pass_rate"] == pytest.approx(2 / 3)
    assert s["failures_by_category"] == {"not-genuine": 1}


def test_a_malformed_test_is_the_harness_failing_not_the_model():
    rec = score_verdict({"test": {}}, "print(1)",
                        Verdict(False, "malformed-test", "engine not built: lypning-l"))
    assert rec["passed"] is False and "engine not built" in rec["harness_error"]
    assert "failure_category" not in rec


# ------------------------------------------------------------ the labels


def test_a_refusal_is_labelled_a_refusal_and_not_a_wrong_answer():
    # The CPython reference run attached to a lypning verdict PASSED, so its
    # stderr belongs to a correct program and may not be sniffed for a reason.
    ref = RunResult(exit_code=0, stdout="ok\n",
                    stderr="Traceback (most recent call last):\n", duration_s=0.0)
    v = Verdict(False, "refused", "lypning-l: unsupported: module: import hashlib", run=ref)
    assert classify(v) == "refused:module"
    assert stratum(classify(v)) == "refused"


def test_a_refusal_nobody_can_parse_is_still_a_refusal():
    assert classify(Verdict(False, "refused", "every engine refused")) == "refused:unknown"


def test_every_reason_the_lypning_kind_can_return_has_its_own_label():
    labels = {r: classify(Verdict(False, r, "d")) for r in
              ("refused", "engine-mismatch", "not-genuine", "timeout", "stdout", "exit")}
    assert labels == {"refused": "refused:unknown", "engine-mismatch": "engine-mismatch",
                      "not-genuine": "not-genuine", "timeout": "timeout",
                      "stdout": "wrong-output", "exit": "wrong-output"}


def test_the_terminal_says_why_the_denominator_shrank(tmp_path):
    # An excluded mismatch that nobody prints is a correct number nobody can
    # account for -- the quiet half of the inversion invariant 1 warns about.
    from pipeline.cli import render_summary
    run = _attempts(tmp_path, [
        {"case_id": "a", "sample": 0, "passed": True},
        {"case_id": "b", "sample": 0, "passed": True, "authorship": "undecided",
         "authorship_detail": "output lines appear verbatim"},
        {"case_id": "c", "sample": 0, "passed": False, "reason": "not-genuine",
         "failure_category": "not-genuine"},
        {"case_id": "d", "sample": 0, "passed": False, "reason": "engine-mismatch",
         "detail": "lypning disagrees with CPython: stdout"},
    ])
    text = render_summary(summarize_run(run))
    assert "MISMATCH   1 attempt(s) excluded" in text and "not the model: d" in text
    assert "recited    1 attempt(s)" in text and "not-genuine: c" in text
    assert "undecided  1 pass(es)" in text and "they stand: b" in text


def test_an_attempt_only_label_can_never_become_a_case_category():
    # adapters.py maps a failure_category onto a case category; admitting these
    # would file this repository's own bug as a task to train against.
    assert ATTEMPT_CATEGORIES == ("engine-mismatch", "not-genuine")
    for name in ATTEMPT_CATEGORIES:
        assert not is_category(name)
    assert is_category("wrong-output") and is_category("refused:module")
