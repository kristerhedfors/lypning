from __future__ import annotations

from pipeline.training_metrics import CheckpointGate, summarize


def test_family_macro_not_number_of_clones():
    records = [dict(family="a", population="coverage", correct=True, native=True)] * 9
    records += [dict(family="b", population="coverage", correct=False, native=False)]
    assert summarize(records)["correct"] == 0.5


def family(name, population, solved, native=None, cases=100, draws=4):
    """`cases` cases of `draws` draws; the first `solved` always correct, the
    first `native` of those always native. Metrics come from `summarize`, so
    the gate reads the case clusters it selects on in production."""
    native = solved if native is None else native
    return [dict(family=name, case_id="%s-%d" % (name, i), draw=d, population=population,
                 correct=i < solved, native=i < native)
            for i in range(cases) for d in range(draws)]


def test_fallback_regression_cannot_be_hidden_by_coverage_gain():
    baseline = summarize(family("a", "coverage", 20) + family("b", "fallback-control", 100, 0))
    gate = CheckpointGate(baseline)
    bad = summarize(family("a", "coverage", 90) + family("b", "fallback-control", 80, 0))
    assert gate.observe(10, bad) is None, "the gate selects; it never stops"
    assert gate.best_step == 0
    assert gate.report()["observed"][0]["rejected_for"] == ["retention"]
    # The same coverage gain, with the control population held: admitted.
    good = summarize(family("a", "coverage", 90) + family("b", "fallback-control", 100, 0))
    gate.observe(20, good)
    assert gate.report()["step"] == 20


def test_a_control_slice_may_wobble_within_its_own_noise():
    """The retention rule is three standard errors, so it does not veto on
    sampling noise -- the failure mode of the per-slice floors it replaces."""
    coverage = lambda native: family("a", "coverage", 100, native)
    baseline = summarize(coverage(50) + family("c", "fallback-control", 90, 0))
    gate = CheckpointGate(baseline)
    # 3 standard errors of a 0.9 rate over 400 draws is about 4.5pp.
    wobble = summarize(coverage(60) + family("c", "fallback-control", 87, 0))
    gate.observe(10, wobble)
    assert gate.best_step == 10
    collapse = summarize(coverage(60) + family("c", "fallback-control", 80, 0))
    gate = CheckpointGate(baseline)
    gate.observe(10, collapse)
    assert gate.best_step == 0
