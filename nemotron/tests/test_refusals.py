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


# --- power: the rule has to be able to see the effect being paid for ---------


def test_the_power_curve_reports_both_rules_and_is_deterministic():
    """Two runs of the same curve must agree, or the number is not a number.

    The seed is the whole reason: a power analysis that moves between runs is a
    number someone can re-roll until it says what they want, which is the exact
    failure `PREREGISTRATION.md` exists to prevent.
    """
    from pipeline import stats

    # range stops at 15/16 on purpose: a 16/16 here would be an
    # always-passing case and the counts below would be about the
    # arithmetic rather than the function.
    scores = [0.0] * 30 + [1.0] * 10 + [i / 16 for i in range(1, 16)] * 2
    a = stats.power_curve(scores, 16, trials=6, resamples=120)
    b = stats.power_curve(scores, 16, trials=6, resamples=120)
    assert a == b, "the power curve is not deterministic"
    assert [r["lift"] for r in a["rows"]] == list(stats.POWER_GRID)
    assert a["n_cases"] == len(scores)
    assert a["never_passes"] == 30 and a["always_passes"] == 10


def test_the_paired_rule_is_never_weaker_than_the_unpaired_one():
    """The claim `PREREGISTRATION.md` rests on, checked rather than asserted.

    Pairing cancels the per-case difficulty both arms share. If a grid ever
    showed the unpaired rule winning a row, the argument for making the paired
    test primary would be gone and this file should say so first.
    """
    from pipeline import stats

    scores = [0.0] * 34 + [1.0] * 16 + [i / 16 for i in range(2, 15, 3)] * 6
    curve = stats.power_curve(scores[:74], 16, trials=8, resamples=150)
    for row in curve["rows"]:
        assert row["paired_power"] >= row["unpaired_power"], (
            "at a %+.0fpp lift the unpaired rule beat the paired one (%.2f vs %.2f) — "
            "the premise of the primary rule has gone"
            % (100 * row["lift"], row["unpaired_power"], row["paired_power"])
        )


def test_minimum_detectable_reads_the_first_row_that_clears_the_bar():
    from pipeline import stats

    curve = {"rows": [{"lift": 0.02, "paired_power": 0.4, "unpaired_power": 0.0},
                      {"lift": 0.05, "paired_power": 0.9, "unpaired_power": 0.1},
                      {"lift": 0.20, "paired_power": 1.0, "unpaired_power": 0.95}]}
    assert stats.minimum_detectable(curve, "paired") == 0.05
    assert stats.minimum_detectable(curve, "unpaired") == 0.20
    assert stats.minimum_detectable(curve, "unpaired", want=0.99) is None


def test_the_power_curve_simulates_both_legs_of_the_rule_not_one():
    """The defect an adversarial re-check found in this very analysis.

    `PREREGISTRATION.md` makes the primary rule the CONJUNCTION — the paired
    bootstrap CI lower bound above 0 AND exact McNemar p < 0.05 — and the curve
    counted only the bootstrap leg. So `nt power`'s headline was the power of
    half the rule, and the number the pre-registration rests on was measured
    against a rule nobody registered.
    """
    from pipeline import stats

    scores = [0.0] * 20 + [1.0] * 8 + [i / 16 for i in range(2, 15)] * 2
    curve = stats.power_curve(scores, 16, trials=8, resamples=150)
    for row in curve["rows"]:
        assert set(row) >= {"lift", "unpaired_power", "bootstrap_power",
                            "mcnemar_power", "paired_power"}
        # the conjunction can never beat either leg it is made of
        assert row["paired_power"] <= row["bootstrap_power"] + 1e-9
        assert row["paired_power"] <= row["mcnemar_power"] + 1e-9


def test_the_discrete_floor_under_the_rule_is_reported():
    """Exact McNemar over b gained and nothing lost is 2/2**b, so five flipped
    cases give p = 0.0625 and cannot fire the rule at ANY effect size. A
    percentage-point MDE hides that entirely; it is the shape of the effect,
    not its size, that decides."""
    from pipeline import stats

    assert stats._mcnemar(5, 0) == pytest.approx(0.0625)
    assert stats._mcnemar(6, 0) == pytest.approx(0.03125)
    assert stats._min_discordant() == 6
    curve = stats.power_curve([0.0] * 10 + [0.5] * 10, 16, trials=4, resamples=100)
    assert curve["min_gained_if_none_lost"] == 6
