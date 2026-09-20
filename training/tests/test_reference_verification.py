"""The reference check, and the half of it that is about WHERE it runs.

`training-prepare` refuses a case whose reference does not reproduce its own
stdout -- but it refuses it on a metered GPU job, after the bank download and
the weights. One such case ended a round at `last_stage: prepare` on
2026-09-20.

The cheap version of that question has a trap in it. `bank_publish.py
--verify-references` ran on `ubuntu-latest` while the verifier runs in
`python:3.12-slim`; `ubuntu-latest` ships locales the slim image does not, so
the `locale.setlocale` case that killed the round reproduced on the runner and
was passed through. A check that runs somewhere other than the thing it
predicts is a check of the wrong machine, and nothing in the bank recorded
which machine it had been.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".github" / "scripts"


def load(name):
    """Import a `.github/scripts` module by path; they are not a package."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "training"))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(ROOT / "training"))
    return module


def union(tmp_path, rows):
    path = tmp_path / "union.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


GOOD = {"case_id": "good-1", "family": "str-upper",
        "reference": "print(input().upper())",
        "tests": [{"stdin": "ab\n", "stdout": "AB\n"}, {"stdin": "zz\n", "stdout": "ZZ\n"}]}
WRONG = {"case_id": "bad-stdout", "family": "str-upper",
         "reference": "print(input().lower())",
         "tests": [{"stdin": "AB\n", "stdout": "AB\n"}]}
RAISES = {"case_id": "bad-raises", "family": "math-div", "reference": "print(1/0)",
          "tests": [{"stdin": "", "stdout": "x\n"}]}


def test_the_checker_separates_references_that_reproduce_from_those_that_do_not(tmp_path):
    """End to end through the real script, because its value is that it runs.

    Both failure shapes matter and they are found differently: a reference that
    prints the wrong thing runs clean and is caught by comparing stdout, while
    one that raises never produces stdout at all.
    """
    out = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "verify_references.py"),
         str(union(tmp_path, [GOOD, WRONG, RAISES])), "--out", str(out), "--image", "a-test-image"],
        capture_output=True, text=True, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stderr
    report = json.loads(out.read_text(encoding="utf-8"))

    assert set(report["bad"]) == {"bad-stdout", "bad-raises"}
    assert report["bad"]["bad-stdout"][1] == "stdout differs"
    assert report["bad"]["bad-stdout"][0] == "str-upper", "the family travels with the case"
    assert "good-1" not in report["bad"]
    assert report["cases"] == 3

    # The instrument is part of the answer: `sandbox.run_python` spawns
    # `sys.executable`, so which interpreter ran is what the report is ABOUT.
    assert report["image"] == "a-test-image"
    assert report["python"] == ".".join(str(n) for n in sys.version_info[:3])


def test_a_finding_is_not_a_failure(tmp_path):
    """Exit 0 with bad cases found, or the workflow stops before it can act.

    The publisher decides what to do with the report. A non-zero exit here
    would end the step and the bank would never be cut at all -- turning "some
    cases were dropped", the normal outcome, into a broken pipeline.
    """
    out = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "verify_references.py"),
         str(union(tmp_path, [WRONG])), "--out", str(out)],
        capture_output=True, text=True, cwd=str(ROOT))
    assert proc.returncode == 0
    assert json.loads(out.read_text(encoding="utf-8"))["bad"]
    assert proc.stdout, "a finding is reported, not swallowed"


def test_the_publisher_refuses_a_report_cut_against_a_different_union(tmp_path, capsys):
    """A report is about one union; another union's answer is not an answer.

    Without this, re-running the carve over more batches while reusing an older
    report would silently drop the wrong case ids and keep the right ones --
    and the manifest would still claim the references were verified.
    """
    publish = load("bank_publish")
    report = tmp_path / "r.json"
    report.write_text(json.dumps({"image": "img", "python": "3.12.0", "cases": 99, "bad": {}}),
                      encoding="utf-8")
    argv = [str(union(tmp_path, [GOOD, WRONG])), "--name", "v9", "--out", str(tmp_path / "b"),
            "--reference-report", str(report)]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", ["bank_publish.py"] + argv)
        assert publish.main() == 2
    assert "not this union's 2" in capsys.readouterr().err


def test_the_publisher_refuses_both_sources_of_the_same_answer(tmp_path, capsys):
    """Two answers to one question is a question about which machine ran it."""
    publish = load("bank_publish")
    report = tmp_path / "r.json"
    report.write_text(json.dumps({"image": "img", "cases": 2, "bad": {}}), encoding="utf-8")
    argv = [str(union(tmp_path, [GOOD, WRONG])), "--name", "v9", "--out", str(tmp_path / "b"),
            "--reference-report", str(report), "--verify-references"]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", ["bank_publish.py"] + argv)
        assert publish.main() == 2
    assert "verifier's image" in capsys.readouterr().err


def test_the_workflow_runs_the_check_in_the_pinned_image_and_publishes_that_report():
    """The gap was environmental, so the fix has to be read in the workflow.

    Three things together are the fix, and any one alone is not: the check runs
    in `launch.BASE_IMAGE`, the image is passed in so the report can name it,
    and the publisher is handed the REPORT rather than asked to redo the check
    on the runner it happens to be standing on.
    """
    text = (ROOT / ".github/workflows/bank-publish.yml").read_text(encoding="utf-8")
    assert "docker run" in text, "the check must run in a container, not on the runner"
    assert "from hf.launch import BASE_IMAGE" in text, (
        "the image must be the verifier's pinned one, read from the source of truth "
        "rather than copied into YAML where it would drift")
    assert "VERIFY_IMAGE" in text, "the report has to be able to name where it ran"
    assert "--reference-report" in text
    assert "$args --verify-references" not in text, (
        "the runner-side check must not be what the published bank is cut on")


def test_the_manifest_records_where_the_check_ran_not_merely_that_it_did():
    """`references_verified: true` was true of the check that missed the case.

    A boolean cannot distinguish the answer that predicts the verifier from the
    answer that does not, and the bank is the only place a later reader can
    find out which one they have.
    """
    source = (SCRIPTS / "bank_publish.py").read_text(encoding="utf-8")
    assert '"references_verified_in": verified_in' in source
    assert 'verified_in = "the publishing runner, NOT the verifier image"' in source, (
        "the runner-side path must say so in the bank, not quietly claim verification")
