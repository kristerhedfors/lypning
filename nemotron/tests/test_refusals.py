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
