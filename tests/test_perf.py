"""The hot-loop diagnostic's arithmetic and its refusals — not its numbers.

Same rule as ``test_bench.py``: there is no timing assertion in this file. A
wall-clock threshold on a shared runner measures the runner. What CI can hold is
the arithmetic around the numbers — that startup is subtracted, that a case the
arms disagree about is fatal rather than fast, and that a diff spans only the
cases both runs measured.
"""

from __future__ import annotations

import json

import pytest

from lypning import engines, perf


def _case(name, net, verdict=perf.OK):
    c = perf.CaseResult(name=name, group="g", verdict=verdict)
    c.net = dict(net)
    ref = c.net.get(perf.REFERENCE)
    if ref:
        c.ratio = {k: v / ref for k, v in c.net.items()}
    return c


def _report(cases, arms=("cpython", "lypning"), startup=None):
    return perf.PerfReport(
        arms=list(arms),
        startup=dict(startup or {"cpython": 11.0, "lypning": 0.6}),
        cases=list(cases),
        seconds=1.0,
        repeat=5,
        host={"cpu_count": 4, "kernel": "Linux", "ci": False},
    )


# --- the suite is a claim about the subset -----------------------------------


def test_every_case_has_a_unique_name():
    names = [c.name for c in perf.SUITE]
    assert len(names) == len(set(names))


def test_every_case_prints_something():
    # The printed value is the checksum the arms are held to. A case with no
    # output cannot be compared, and an interpreter is free to skip work whose
    # result is never observed.
    for c in perf.SUITE:
        assert "print(" in c.program, c.name


def test_no_case_names_an_absolute_path():
    # Two cases write files. They run in a temp cwd, which contains every
    # relative path and nothing else; an absolute one would escape it.
    for c in perf.SUITE:
        assert "/etc" not in c.program and "/home" not in c.program, c.name


# --- startup subtraction -----------------------------------------------------


def test_startup_is_subtracted_from_the_reading():
    assert perf._net(12.0, 11.0) == pytest.approx(1.0)


def test_a_case_that_is_all_startup_does_not_divide_by_zero():
    # Floored rather than zeroed: a zero denominator reads as an infinite
    # speedup, which is the one number this table must never print.
    assert perf._net(11.0, 11.0) > 0


def test_a_missing_reading_stays_missing():
    assert perf._net(None, 11.0) is None


# --- the verdicts ------------------------------------------------------------


def test_a_report_with_only_ok_cases_is_ok():
    assert _report([_case("a", {"cpython": 1.0, "lypning": 2.0})]).ok


@pytest.mark.parametrize("verdict", [perf.DIFFER, perf.REFUSED_V, perf.FAILED])
def test_a_bad_case_makes_the_whole_run_not_ok(verdict):
    # Each of these is a bug in the tree, not a slow row — invariant 1 on the
    # suite's own terms.
    r = _report([_case("a", {"cpython": 1.0, "lypning": 2.0}),
                 _case("b", {}, verdict)])
    assert not r.ok
    assert [c.name for c in r.bad] == ["b"]


def test_an_unmeasured_case_is_not_a_failure():
    # Nothing was built, so nothing was claimed. A hole, not a red run.
    r = _report([_case("a", {}, perf.UNMEASURED)])
    assert r.ok
    assert r.bad == []


# --- the shared subset -------------------------------------------------------


def test_the_total_spans_only_cases_every_arm_measured():
    # A total over different case sets is not a comparison — the same rule
    # `bench` applies to the corpus.
    both = _case("both", {"cpython": 2.0, "lypning": 8.0})
    one = _case("one", {"cpython": 5.0})
    r = _report([both, one])
    assert r.shared() == 1
    assert r.totals() == {"cpython": 2.0, "lypning": 8.0}


def test_a_case_that_failed_is_out_of_the_total():
    bad = _case("bad", {"cpython": 2.0, "lypning": 8.0}, perf.DIFFER)
    r = _report([bad])
    assert r.shared() == 0
    assert r.totals() == {"cpython": 0.0, "lypning": 0.0}


# --- the record and the diff -------------------------------------------------


def test_a_record_round_trips(tmp_path):
    r = _report([_case("a", {"cpython": 2.0, "lypning": 6.0})])
    p = perf.write_record(r, tmp_path / "rec.json")
    back = perf.load_record(p)
    assert back["cases"][0]["net"]["lypning"] == 6.0


def test_a_foreign_json_file_is_refused(tmp_path):
    p = tmp_path / "other.json"
    p.write_text(json.dumps({"tool": "something else"}))
    with pytest.raises(ValueError):
        perf.load_record(p)


def test_the_diff_spans_only_cases_both_runs_measured():
    baseline = perf.record(_report([
        _case("kept", {"cpython": 2.0, "lypning": 10.0}),
        _case("gone", {"cpython": 2.0, "lypning": 4.0}),
    ]))
    now = _report([
        _case("kept", {"cpython": 2.0, "lypning": 6.0}),
        _case("added", {"cpython": 2.0, "lypning": 3.0}),
    ])
    d = perf.diff(baseline, now, arm="lypning")
    assert [r[0] for r in d.rows] == ["kept"]
    assert d.gone == ["gone"] and d.added == ["added"]
    assert d.totals == (10.0, 6.0)


def test_the_diff_puts_the_biggest_improvement_first():
    baseline = perf.record(_report([
        _case("small", {"cpython": 1.0, "lypning": 5.0}),
        _case("big", {"cpython": 1.0, "lypning": 50.0}),
    ]))
    now = _report([
        _case("small", {"cpython": 1.0, "lypning": 4.0}),
        _case("big", {"cpython": 1.0, "lypning": 10.0}),
    ])
    d = perf.diff(baseline, now, arm="lypning")
    assert [r[0] for r in d.rows] == ["big", "small"]


# --- rendering ---------------------------------------------------------------


def test_the_table_leads_with_the_worst_ratio():
    r = _report([_case("fast", {"cpython": 4.0, "lypning": 2.0}),
                 _case("slow", {"cpython": 1.0, "lypning": 40.0})])
    text = perf.render(r)
    assert text.index("slow") < text.index("fast")


def test_the_table_says_it_is_not_an_acceptance_gate():
    # The one sentence that keeps this instrument from being misused. It is
    # asserted because removing it is the whole failure mode.
    assert "NOT an acceptance gate" in perf.render(_report([]))


def test_a_bad_case_is_named_rather_than_averaged_over():
    r = _report([_case("broken", {}, perf.DIFFER)])
    r.cases[0].note = "arms printed different answers"
    text = perf.render(r)
    assert "NOT MEASURED" in text and "broken" in text


def test_render_survives_an_empty_run():
    assert perf.render(_report([]))


# --- the live suite, when there is something to run it on --------------------


def test_the_whole_suite_is_inside_the_subset(lypning_bin):
    """Every case runs on lypning and agrees with CPython.

    This is the expensive test in the file and it is the point of the file:
    a suite entry that drifts out of the subset, or that the two arms answer
    differently, is exactly what the diagnostic must not average over.
    """
    if engines.find_cpython() is None:
        pytest.skip("no CPython to compare against")
    report = perf.run(repeat=1)
    assert [c.name for c in report.bad] == []
    assert report.ok
