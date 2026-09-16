from __future__ import annotations

from pipeline.training_metrics import CheckpointGate, summarize


def test_family_macro_not_number_of_clones():
    records = [dict(family="a", population="coverage", correct=True, native=True)] * 9
    records += [dict(family="b", population="coverage", correct=False, native=False)]
    assert summarize(records)["correct"] == 0.5


def test_fallback_regression_cannot_be_hidden_by_coverage_gain():
    baseline = {"correct": 0.5, "correct_native": 0.2,
                "by_population": {"coverage": {"correct": 0.2}, "fallback-control": {"correct": 1.0}}}
    gate = CheckpointGate(baseline, patience=2)
    bad = {"correct": 0.7, "correct_native": 0.7,
           "by_population": {"coverage": {"correct": 0.9}, "fallback-control": {"correct": 0.8}}}
    assert not gate.observe(10, bad)
    assert gate.best_step == 0
    assert gate.observe(20, bad)
    good = dict(baseline, correct_native=0.4)
    assert not gate.observe(30, good)
    assert gate.report()["step"] == 30 and gate.stale == 0
