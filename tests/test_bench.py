"""The benchmark's selection rules — not its numbers.

There is deliberately no timing assertion anywhere in this file. A wall-clock
threshold on a shared CI runner measures the runner, and a suite that goes red
because a neighbouring job got noisy teaches people to ignore it. What CI can
hold is the arithmetic around the numbers: min-of-N, and which entries are
allowed into the only comparison that is fair.
"""

from __future__ import annotations

import pytest

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


def test_a_battery_running_program_is_taken_out_of_the_timing():
    # Timing a program that runs the whole battery would fork-bomb the host and
    # measure the fork bomb. Same net as the absolute-path skip.
    entries = [corpus.Entry(id="py-a", program="print(1)"),
               corpus.Entry(id="py-fork", program="from lypning import conformance as conf\nconf.run()")]
    assert bench.skip_reason(entries[0]) == ""
    assert bench.skip_reason(entries[1])
    report = bench.corpus_time(entries, arms=[])
    assert report.corpus_size == 1
    assert list(report.skipped) == ["py-fork"]


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


# --- corpus-time: one binary, the whole corpus -------------------------------
#
# Still no timing assertion. What CI can hold is which entries went into the
# total, whether a refusal was counted as a run, and whether two runs are being
# compared over the same programs — the arithmetic, not the clock.


def test_an_unbuilt_binary_is_none_rather_than_an_empty_table(no_micropython):
    # "Not built" is a status line everywhere in this package. An empty table
    # would read as a binary that timed nothing, which is a different fact.
    entries = [corpus.Entry(id="py-a", program="print(1)")]
    assert bench.corpus_time_one("lypning-mp", entries=entries) is None


def test_a_path_is_an_arm_and_a_missing_path_is_not(tmp_path, lypning_bin):
    # The acceptance question is usually asked about a candidate that is not
    # installed yet, so a path is accepted — resolved absolute, because every
    # entry runs in its own temp cwd and a relative path would vanish there.
    arm = bench.arm_for(str(lypning_bin))
    assert arm is not None and arm.binary.is_absolute()
    assert bench.arm_for(str(tmp_path / "nope" / "lypning")) is None


def test_an_explicit_arm_passes_through_resolution(tmp_path):
    # How a binary that is deliberately NOT an engine gets into a table without
    # becoming something engines.find could route a program to.
    arm = bench.Arm("stock", tmp_path / "micropython-stock")
    assert bench.resolve_arms([arm]) == [arm]


def test_a_refusal_is_timed_and_counted_apart(lypning_bin):
    # Exit 90 costs a spawn and a parse, which is real time the agent waited
    # for — so it is in the total. It is counted separately because a change
    # that moves an entry in or out of the subset changes what is being timed.
    entries = [corpus.Entry(id="py-ok", program="print(1)"),
               corpus.Entry(id="py-refused", program="import ctypes")]
    timing = bench.corpus_time_one("lypning", entries=entries, repeat=1)
    assert timing.timed == 2 and timing.refused == 1
    assert timing.total_ms > 0
    assert timing.loaded == 2
    assert "2 programs loaded" in bench.render_corpus_time(timing)


def test_a_programs_own_failure_is_not_a_refusal(lypning_bin):
    timing = bench.corpus_time_one(
        "lypning", entries=[corpus.Entry(id="py-raise", program="raise SystemExit(3)")], repeat=1)
    assert timing.refused == 0
    assert timing.failed + timing.refused == 1


def test_a_record_round_trips(tmp_path, lypning_bin):
    timing = bench.corpus_time_one(
        "lypning", entries=[corpus.Entry(id="py-a", program="print(1)")], repeat=1)
    path = bench.write_record(timing, tmp_path / "before.json")
    record = bench.load_record(path)
    assert record["schema"] == bench.RECORD_SCHEMA
    assert set(record["entries"]) == {"py-a"}


def test_a_loaded_host_disqualifies_the_timing_and_says_so():
    # Every timing tool here is spawn-bound: on an oversubscribed host the
    # reading is the scheduler's queue. One session quoted a 28x "regression"
    # that was a load average of 340. The header carries the load, and a
    # baseline verdict on a loaded host is printed but refuses the word.
    h = bench.host_info({})
    assert "load" in h and "loaded" in h
    assert bench.host_load_line({"cpu_count": 10, "load": 1.2, "loaded": False}) == ", load 1.2"
    warn = bench.host_load_line({"cpu_count": 10, "load": 340.0, "loaded": True})
    assert "host loaded" in warn and "do not quote" in warn
    assert bench.host_load_line({"cpu_count": 10}) == ""   # no getloadavg: silent, not wrong
    baseline = bench.timing_record(_timing({"py-a": bench.EntryTime(10.0, RAN, 0)}))
    now = _timing({"py-a": bench.EntryTime(8.0, RAN, 0)})
    now.host = dict(now.host or {}, cpu_count=10, load=340.0, loaded=True)
    text = bench.render_corpus_time(now, bench.diff_record(baseline, now))
    assert "FASTER — UNRELIABLE, host loaded" in text
    assert "! host loaded" in text


def test_a_baseline_that_is_not_a_record_is_refused_by_name(tmp_path):
    # Refused rather than half-read: a diff over an empty intersection renders
    # as "no change", which is the most expensive possible way to be wrong.
    (tmp_path / "notjson.json").write_text("hello", encoding="utf-8")
    with pytest.raises(ValueError):
        bench.load_record(tmp_path / "notjson.json")
    (tmp_path / "old.json").write_text('{"schema": "lypning-corpus-time/0", "entries": {}}',
                                       encoding="utf-8")
    with pytest.raises(ValueError):
        bench.load_record(tmp_path / "old.json")
    with pytest.raises(ValueError):
        bench.load_record(tmp_path / "absent.json")


def _timing(entries, loaded=None):
    return bench.CorpusTiming(engine="lypning", binary="/x/lypning", entries=entries,
                              loaded=loaded if loaded is not None else len(entries))


def test_a_diff_compares_only_the_entries_both_runs_timed():
    # The capture harness grows the corpus every session, so a baseline covers a
    # different set of programs than a run today — and two totals over different
    # program sets are not a comparison.
    baseline = bench.timing_record(_timing({
        "py-a": bench.EntryTime(10.0, RAN, 0),
        "py-gone": bench.EntryTime(90.0, RAN, 0),
    }))
    now = _timing({
        "py-a": bench.EntryTime(8.0, RAN, 0),
        "py-new": bench.EntryTime(70.0, RAN, 0),
    })
    d = bench.diff_record(baseline, now)
    assert (d.shared, d.added, d.dropped) == (1, 1, 1)
    assert (d.before_ms, d.after_ms) == (10.0, 8.0)
    assert d.ratio == 0.8
    text = bench.render_corpus_time(now, d)
    assert "1 programs both runs timed" in text
    assert "1 new here, 1 only in the baseline" in text


def test_a_status_flip_invalidates_the_comparison_out_loud():
    # An entry that started exiting 90 got cheaper by losing a capability, and
    # its time is no longer a time for the same work.
    baseline = bench.timing_record(_timing({"py-a": bench.EntryTime(10.0, RAN, 0)}))
    now = _timing({"py-a": bench.EntryTime(1.0, REFUSED, 90)})
    d = bench.diff_record(baseline, now)
    assert d.flips == [("py-a", "ran:0", "refused:90")]
    assert "changed EXIT STATUS" in bench.render_corpus_time(now, d)


def test_the_corpus_size_loaded_survives_the_skips(lypning_bin):
    # A subset silently smaller than the file on disk is how a corpus count
    # goes stale in the other direction.
    entries = [corpus.Entry(id="py-a", program="print(1)"),
               corpus.Entry(id="py-abs", program="open('/etc/hosts').read()")]
    timing = bench.corpus_time_one("lypning", entries=entries, repeat=1)
    assert timing.loaded == 2 and timing.timed == 1 and len(timing.skipped) == 1
    assert "2 programs loaded, 1 timed" in bench.render_corpus_time(timing)


# --- lypning-mp against the benchmark control --------------------------------


def test_the_comparison_reports_what_is_missing_rather_than_raising(no_micropython, monkeypatch):
    # The MicroPython tier is absent by default and the control more so, so this
    # is the ordinary answer rather than a failure — and each half names the
    # command that fixes it.
    from lypning import build

    monkeypatch.setattr(build, "stock_binary", lambda: None)
    report = bench.micropython()
    assert report.bench is None and not report.ok
    assert any("--micropython" in m for m in report.missing)
    assert any("--stock" in m for m in report.missing)
    text = bench.render_micropython(report)
    assert "cannot be made" in text
    with pytest.raises(ValueError):
        bench.ledger_entry(report)


def test_the_ledger_entry_goes_directly_below_the_marker(tmp_path):
    # The ledger is append-only and newest-first: order is its index.
    led = tmp_path / "LEDGER.md"
    led.write_text("# head\n\n%s\n\n## 2026-08-15 — older\n\nbody\n" % bench.LEDGER_MARKER,
                   encoding="utf-8")
    bench.record_ledger(led, "## 2026-08-20 — newer\n\nnew body")
    text = led.read_text(encoding="utf-8")
    assert text.index("## 2026-08-20") < text.index("## 2026-08-15")
    assert bench.LEDGER_MARKER in text
    assert "\n\n## 2026-08-15" in text  # the older entry keeps its blank line


def test_a_ledger_without_a_marker_is_refused(tmp_path):
    # An entry appended to the end of a newest-first file reads as its oldest.
    led = tmp_path / "LEDGER.md"
    led.write_text("# no marker here\n", encoding="utf-8")
    with pytest.raises(ValueError):
        bench.record_ledger(led, "## entry")
    assert led.read_text(encoding="utf-8") == "# no marker here\n"


def test_the_ledger_entry_names_both_binaries_and_the_pin(micropython_bin, monkeypatch):
    from lypning import build

    stock = build.stock_binary()
    if stock is None:
        pytest.skip("the benchmark control is not built (`lypning build --stock`)")
    report = bench.micropython(limit=3, startup_repeat=1)
    entry = bench.ledger_entry(report)
    assert "lypning-mp" in entry and "stock" in entry
    assert build.micropython_pin()["tag"] in entry
    for shape in report.shapes:
        assert shape.sha256[:12] in entry
    # Both binaries, by digest, and they are not the same binary: a copy of
    # lypning-mp sitting at the control's path would make every ratio read 1.00.
    assert report.shapes[0].sha256 != report.shapes[1].sha256
    assert "3 corpus programs loaded" in entry
