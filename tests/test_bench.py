"""The benchmark's selection rules — not its numbers.

There is deliberately no timing assertion anywhere in this file. A wall-clock
threshold on a shared CI runner measures the runner, and a suite that goes red
because a neighbouring job got noisy teaches people to ignore it. What CI can
hold is the arithmetic around the numbers: min-of-N, and which entries are
allowed into the only comparison that is fair.
"""

from __future__ import annotations

from lypning import bench, corpus

RAN, REFUSED, ERROR = bench.RAN, bench.REFUSED, bench.ERROR


def _t(ms, outcome=RAN, rc=0):
    return bench.EntryTime(ms, outcome, rc)


def test_min_of_n_keeps_the_fastest_sample():
    slow, fast = _t(5.0), _t(3.0)
    assert bench._keep_best(None, slow) is slow
    assert bench._keep_best(slow, fast) is fast
    assert bench._keep_best(fast, slow) is fast


def test_min_of_n_keeps_the_outcome_of_the_sample_it_kept():
    # The number and the label have to describe the same run: a fast refusal is
    # not a fast execution, and reporting one as the other is how a broken
    # engine posts the best time in the table.
    kept = bench._keep_best(_t(9.0, ERROR, 124), _t(2.0, REFUSED, 90))
    assert (kept.ms, kept.outcome, kept.returncode) == (2.0, REFUSED, 90)


def test_an_unbuilt_arm_is_absent_rather_than_zero(no_micropython):
    # A missing arm is a hole in the table; a zero in it would be a lie that
    # reads as a win.
    assert bench.resolve_arms(["lypning-mp"]) == []


def test_nothing_is_shared_when_there_are_no_arms(no_micropython):
    # With no arms there is nothing shared, which is different from everything
    # shared — and a shared subset of "all of it" would print a comparison
    # between columns that do not exist.
    report = bench.corpus_time([corpus.Entry(id="py-a", program="print(1)")],
                               arms=["lypning-mp"])
    assert report.shared_ids == []
    assert report.corpus_size == 1
    assert report.arms == {}


def test_an_absolute_path_takes_an_entry_out_of_the_corpus_size():
    entries = [corpus.Entry(id="py-a", program="print(1)"),
               corpus.Entry(id="py-b", program="open('/etc/hosts').read()"),
               corpus.Entry(id="py-c", program="print(2)", argv_tail=(">", "/tmp/out"))]
    assert bench.skip_reason(entries[0]) == ""
    assert "/etc/hosts" in bench.skip_reason(entries[1])
    assert "/tmp/out" in bench.skip_reason(entries[2])
    report = bench.corpus_time(entries, arms=[])
    assert report.corpus_size == 1
    assert sorted(report.skipped) == ["py-b", "py-c"]


def test_the_shared_subset_is_what_every_arm_actually_ran(lypning_bin):
    # `import ctypes` is refused by the Rust core and run by the dispatcher, so
    # it is not a program the two arms can be compared on.
    entries = [corpus.Entry(id="py-both", program="print(1)"),
               corpus.Entry(id="py-refused", program="import ctypes")]
    report = bench.corpus_time(entries, arms=["lypning", "mixture"])
    assert set(report.arms) == {"lypning", "mixture"}
    assert report.shared_ids == ["py-both"]
    assert report.arms["lypning"].refused == 1
    assert report.arms["lypning"].ran == 1
    assert report.arms["mixture"].ran == 2
    # Every entry is in the per-arm total; only the shared ones are comparable.
    assert report.arms["lypning"].shared_total_ms <= report.arms["lypning"].total_ms


def test_a_programs_own_failure_is_a_run_not_an_error(lypning_bin):
    report = bench.corpus_time([corpus.Entry(id="py-raise", program="raise SystemExit(3)")],
                               arms=["lypning"])
    arm = report.arms["lypning"]
    assert arm.errors == 0
    # `raise` is an answer, and the wall clock spent reaching it is real.
    assert arm.ran + arm.refused == 1
