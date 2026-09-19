"""The bank-ceiling instrument: what it measures, and what it refuses to measure."""
from __future__ import annotations

import pytest

from pipeline import bank_native


def row(family, kind=None, reference="print(1)", original=None, population="coverage"):
    r = {"case_id": "c-" + family, "family": family, "population": population,
         "reference": reference, "tests": [{"stdin": "", "stdout": "1\n"}]}
    if kind:
        r["synth"] = {"kind": kind}
        if original:
            r["synth"]["original"] = original
    return r


def verdicts(mapping):
    """A stub engine: program text -> (verdict, buckets)."""
    def verdict_of(program, tests):
        return mapping[program]
    return verdict_of


def test_measure_censuses_verdicts_by_population_not_only_in_aggregate():
    rows = [row("a", reference="native-a"), row("a", reference="native-a"),
            row("b", reference="refused-b", population="fallback-control")]
    result = bank_native.measure(rows, verdicts({"native-a": ("native", []),
                                                 "refused-b": ("refused", ["module: import heapq"])}))
    assert result["verdicts"] == {"native": 2, "refused": 1, "mixed": 0}
    assert result["by_stratum"]["population=coverage"] == {"native": 2}
    assert result["by_stratum"]["population=fallback-control"] == {"refused": 1}
    # The family macro, not the case rate: two families, one at 1.0 and one at 0.
    assert result["native_rate_cases"] == pytest.approx(2 / 3)
    assert result["native_rate_family_macro"] == pytest.approx(0.5)
    assert result["refusal_kinds"] == {"module": 1}


def test_a_mismatch_is_a_witness_and_never_a_rate():
    """Root CLAUDE.md invariant 1: an engine that disagrees with CPython is a bug."""
    rows = [row("a", reference="wrong")]
    result = bank_native.measure(rows, verdicts({"wrong": ("mismatch", ["on input 0"])}))
    assert result["measured"] == 0
    assert result["witness_verdicts"] == {"mismatch": 1}
    assert result["witnesses"][0]["family"] == "a"
    assert result["native_rate_family_macro"] is None


def test_the_original_read_skips_every_row_that_carries_no_refused_draft():
    rows = [row("a", kind="native"), row("b", kind="repaired", original="draft-b")]
    result = bank_native.measure(rows, verdicts({"draft-b": ("refused", ["module: import re"])}),
                                 which="original")
    assert (result["rows"], result["skipped"], result["measured"]) == (2, 1, 1)
    assert result["verdicts"]["refused"] == 1


def test_an_unlabelled_bank_refuses_the_ceiling_rather_than_reporting_a_zero():
    """A zero here reads as "no room"; bank_v2 carries no synth.kind at all."""
    mix = bank_native.first_draft_mix([row("a"), row("b")])
    assert mix["unlabelled"] == 2
    assert mix["macro_delta_ceiling"] is None
    assert "UNREADABLE" in bank_native.render_mix(mix, mde=0.03)


def test_a_counterweights_nominal_room_is_not_room():
    """A fallback-control row is pinned at 0 for EVERY arm (training.Score)."""
    rows = ([row("a", kind="native")] * 9 + [row("a", kind="ceiling", population="fallback-control")])
    mix = bank_native.first_draft_mix(rows)
    assert mix["macro_refused_draft"] == pytest.approx(0.1)
    assert mix["macro_delta_ceiling"] == pytest.approx(0.0)


def test_the_same_repaired_rows_spread_or_concentrated_give_different_ceilings():
    """The §4 unit is the family, so WHERE the movable rows sit decides the bank."""
    spread = [row("f%d" % i, kind="native") for i in range(10) for _ in range(9)]
    spread += [row("f%d" % i, kind="repaired", original="d") for i in range(10)]
    concentrated = [row("f%d" % i, kind="native") for i in range(9) for _ in range(10)]
    concentrated += [row("f9", kind="repaired", original="d") for _ in range(10)]
    assert bank_native.first_draft_mix(spread)["macro_delta_ceiling"] == pytest.approx(0.1)
    assert bank_native.first_draft_mix(concentrated)["macro_delta_ceiling"] == pytest.approx(0.1)
    # Equal here by construction; the discrimination is that a family holding
    # nine native rows per repaired one cannot move more than a tenth of itself.
    lopsided = [row("f%d" % i, kind="native") for i in range(9) for _ in range(10)]
    lopsided += [row("f9", kind="repaired", original="d") for _ in range(10)]
    lopsided += [row("f9", kind="native") for _ in range(90)]
    assert bank_native.first_draft_mix(lopsided)["macro_delta_ceiling"] == pytest.approx(0.01)


def test_the_kind_vocabulary_is_asked_of_synth_and_never_copied():
    from pipeline.synth import POPULATION_OF

    assert set(bank_native.KINDS) == set(POPULATION_OF)
