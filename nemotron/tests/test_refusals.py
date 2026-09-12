"""The census may go stale in one direction only: fewer refusals, never more kinds.

What this pins is not the counts — those move every time the engine gains a
construct, and moving is the point. It pins the *partition*: which kinds the
census calls closed. That set is
:data:`lypning.engines.ONLY_CPYTHON_REFUSALS`, and its own docstring says
removing an entry "is a claim that a wrong answer was acceptable". If a future
change quietly grows or shrinks it, the corpus's build order silently changes
size and nothing else in this repository would notice.
"""

from __future__ import annotations

import pytest

from pipeline import refusals


def test_closed_kinds_come_from_the_engine_not_a_copy():
    """One list, imported. A second copy is the one that goes stale."""
    from lypning.engines import ONLY_CPYTHON_REFUSALS

    assert refusals.closed_kinds() == frozenset(ONLY_CPYTHON_REFUSALS)


def test_the_closed_kinds_the_corpus_actually_hits():
    """The subset of the declared list that this corpus exercises.

    Recorded 2026-09-12 against the corpus of 249 cases. A kind LEAVING this set
    means either the corpus lost a case or — the case that matters — someone
    decided a reimplementation may answer it after all. Either is a commit that
    should say so.
    """
    seen_in_corpus = {
        "set-order", "glob-order", "nan-identity", "dict-view", "percent-format",
    }
    assert seen_in_corpus <= refusals.closed_kinds()


def test_census_marks_closed_kinds_and_counts_cases():
    closed = sorted(refusals.closed_kinds())[0]
    cases = [
        {"id": "a", "negatives": [{"program": "p1"}]},
        {"id": "b", "negatives": [{"program": "p2"}]},
        {"id": "c", "negatives": []},
    ]
    answers = {"p1": {"kind": closed, "detail": "d1"},
               "p2": {"kind": "module", "detail": "import math"}}
    # Patch the probe rather than build a binary: this test is about the
    # bookkeeping, and the probe has the engine's contract to itself.
    original = refusals.probe
    try:
        refusals.probe = lambda program, engine, timeout_s=10.0: answers.get(program)
        result = refusals.census(cases, "fake-engine")
    finally:
        refusals.probe = original

    assert result["no_negative"] == 1
    assert result["refused"] == 2
    assert result["open_cases"] == 1
    assert result["closed_cases"] == 1
    kinds = {r["kind"]: r for r in result["kinds"]}
    assert kinds[closed]["closed"] is True
    assert kinds["module"]["closed"] is False
    # Open kinds sort first: the report's whole job is to name the build order.
    assert result["kinds"][0]["kind"] == "module"


def test_a_program_the_engine_accepts_retires_its_case():
    cases = [{"id": "a", "negatives": [{"program": "print(1)"}]}]
    original = refusals.probe
    try:
        refusals.probe = lambda program, engine, timeout_s=10.0: None
        result = refusals.census(cases, "fake-engine")
    finally:
        refusals.probe = original
    assert result["accepted"] == ["a"]
    assert result["refused"] == 0


def test_on_policy_tallies_and_reports_mismatches_first():
    """The population `conformance` cannot see, and the line that matters in it.

    Two live MISMATCHes were found this way on 2026-09-12 — a `try` truncated by
    a token cap and an `is` over a NaN written to route around an existing
    refusal — with every gate green. So this pins that a MISMATCH is counted,
    named with its case and sample, and reported above the refusal tally rather
    than buried under it.
    """
    attempts = [
        {"case_id": "a", "sample": 0, "passed": True, "program": "p-match"},
        {"case_id": "b", "sample": 3, "passed": False, "program": "p-refused"},
        {"case_id": "c", "sample": 1, "passed": False, "program": "p-wrong"},
        {"case_id": "d", "sample": 0, "passed": False, "program": ""},
    ]
    graded = {
        "p-match": {"verdict": "MATCH", "detail": ""},
        "p-refused": {"verdict": "UNSUPPORTED", "detail": "module"},
        "p-wrong": {"verdict": "MISMATCH", "detail": "exit 1 vs 0"},
    }
    original = refusals.grade_against_engine
    try:
        refusals.grade_against_engine = lambda program, engine, timeout_s=10.0: graded[program]
        result = refusals.on_policy(attempts, "fake-engine")
    finally:
        refusals.grade_against_engine = original

    # The attempt with no program is not a verdict about the engine.
    assert result["programs"] == 3
    assert result["tally"] == {"MATCH": 1, "UNSUPPORTED": 1, "MISMATCH": 1}
    assert result["details"] == {"module": 1}

    text = refusals.on_policy_report(result)
    assert "c sample 1" in text, "a MISMATCH must name the attempt that produced it"
    assert text.index("MISMATCH is never traded") < text.index("what it refused"), \
        "the refusal tally must not be printed above the wrong answers"


def test_on_policy_with_nothing_wrong_says_so_without_the_mismatch_block():
    attempts = [{"case_id": "a", "sample": 0, "passed": True, "program": "p"}]
    original = refusals.grade_against_engine
    try:
        refusals.grade_against_engine = lambda program, engine, timeout_s=10.0: {
            "verdict": "MATCH", "detail": ""}
        result = refusals.on_policy(attempts, "fake-engine")
    finally:
        refusals.grade_against_engine = original
    assert "MISMATCH" not in refusals.on_policy_report(result)
