from __future__ import annotations

from pipeline.training_metrics import CheckpointGate, summarize


def test_family_macro_not_number_of_clones():
    records = [dict(family="a", population="coverage", correct=True, native=True)] * 9
    records += [dict(family="b", population="coverage", correct=False, native=False)]
    assert summarize(records)["correct"] == 0.5


def test_fallback_regression_cannot_be_hidden_by_coverage_gain():
    baseline = {"correct": 0.5, "correct_native": 0.2,
                "by_family": {"a": {"correct_native": 0.2, "draws": 400},
                              "b": {"correct_native": 0.2, "draws": 400}},
                "by_population": {"coverage": {"correct": 0.2, "draws": 400},
                                  "fallback-control": {"correct": 1.0, "draws": 400}}}
    gate = CheckpointGate(baseline)
    bad = dict(baseline, correct=0.7, correct_native=0.7,
               by_population={"coverage": {"correct": 0.9, "draws": 400},
                              "fallback-control": {"correct": 0.8, "draws": 400}})
    assert gate.observe(10, bad) is None, "the gate selects; it never stops"
    assert gate.best_step == 0
    assert gate.report()["observed"][0]["rejected_for"] == ["retention"]
    # The same aggregate gain, with the control population held: admitted.
    good = dict(bad, by_population=baseline["by_population"])
    gate.observe(20, good)
    assert gate.report()["step"] == 20


def test_a_control_slice_may_wobble_within_its_own_noise():
    """The retention rule is three standard errors, so it does not veto on
    sampling noise -- the failure mode of the per-slice floors it replaces."""
    baseline = {"correct": 0.9, "correct_native": 0.5,
                "by_family": {"a": {"correct_native": 0.5, "draws": 400}},
                "by_population": {"fallback-control": {"correct": 0.9, "draws": 400}}}
    gate = CheckpointGate(baseline)
    # 3 standard errors of a 0.9 rate over 400 draws is about 4.5pp.
    wobble = dict(baseline, correct_native=0.6,
                  by_population={"fallback-control": {"correct": 0.87, "draws": 400}})
    gate.observe(10, wobble)
    assert gate.best_step == 10
    collapse = dict(wobble, by_population={"fallback-control": {"correct": 0.80, "draws": 400}})
    gate = CheckpointGate(baseline)
    gate.observe(10, collapse)
    assert gate.best_step == 0
