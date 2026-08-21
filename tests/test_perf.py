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


# --- corpus prevalence, and the queue it reorders ----------------------------


class _E:
    def __init__(self, program):
        self.program = program


def test_prevalence_is_the_fraction_of_programs_the_probe_matches():
    entries = [_E("print(a.split())"), _E("print(1)"), _E("x = b.split(',')"), _E("pass")]
    w, n = perf.prevalence(entries)
    assert n == 4
    assert w["str-split"] == pytest.approx(0.5)


def test_a_case_with_no_probe_is_absent_rather_than_zero():
    # "we did not ask" and "nobody types this" are different answers, and a zero
    # in the weight column would silently retire a case nobody meant to retire.
    named = {c.name for c in perf.SUITE if c.probe}
    w, _ = perf.prevalence([_E("print(1)")])
    assert set(w) == named
    for c in perf.SUITE:
        if not c.probe:
            assert c.name not in w


def test_an_unloadable_corpus_is_a_hole_and_not_a_crash():
    # A diagnostic that refuses to run without its denominator is a diagnostic
    # nobody runs.
    assert perf.prevalence([]) == ({}, 0)


def test_the_weight_is_how_far_behind_times_how_often_it_is_typed():
    c = _case("a", {"cpython": 1.0, "lypning": 5.0})   # 5x
    r = _report([c])
    r.prevalence = {"a": 0.5}
    assert r.weight(c) == pytest.approx((5.0 - 1.0) * 0.5)


def test_a_case_that_beats_cpython_weighs_zero_rather_than_negative():
    # Being ahead somewhere does not buy time back somewhere else.
    c = _case("a", {"cpython": 4.0, "lypning": 1.0})
    r = _report([c])
    r.prevalence = {"a": 0.5}
    assert r.weight(c) == 0.0


def test_the_queue_is_ordered_by_weight_and_not_by_ratio():
    # The row this project actually learned it from: `s += x` in a loop measured
    # 43x CPython — the worst ratio in the suite — against a corpus in which it
    # appears in one program out of 842.
    rare = _case("rare", {"cpython": 1.0, "lypning": 44.0})     # 44x, 0.1%
    common = _case("common", {"cpython": 1.0, "lypning": 6.0})  # 6x, 50%
    r = _report([rare, common])
    r.prevalence = {"rare": 0.001, "common": 0.5}
    r.corpus_size = 842
    text = perf.render(r)
    queue = text[text.index("THE QUEUE"):]
    assert queue.index("common") < queue.index("rare")
    # …while the table above it still leads with the worst ratio.
    assert text.index("rare") < text.index("THE QUEUE")


def test_a_case_too_small_to_trust_is_named():
    r = _report([_case("thin", {"cpython": 0.4, "lypning": 30.0})])
    assert "too small to trust" in perf.render(r) and "thin" in perf.render(r)


def test_every_probe_compiles():
    import re as _re
    for c in perf.SUITE:
        if c.probe:
            _re.compile(c.probe)


def test_every_case_has_a_probe():
    # A case with no probe cannot be ranked, and an unrankable case in a suite
    # whose whole point is the ranking is a case that quietly stops mattering.
    assert [c.name for c in perf.SUITE if not c.probe] == []


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
