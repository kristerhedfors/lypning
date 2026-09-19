"""The repair preludes, differential-tested against the library they replace.

WHY THIS FILE EXISTS. `pipeline.synth` accepts a rewrite only when it reproduces
the expected output on the four inputs a queue row carries. Four inputs cannot see
that 1900 is not a leap year while 2000 is, or that `monthrange` returns a
weekday nobody in the current queue reads. So an edit to `_dt_cfd` or
`_cal_weekday` that breaks a century boundary would pass the bank in silence and
land in training data as a plausible wrong answer.

The verification numbers quoted in `repair_rules.py`'s docstrings were measured
once by hand and are not re-runnable from the tree — which is the same defect as a
number with no command behind it. These tests are that command.

They compare against CPython's own `calendar` and `datetime`, which is the
point: the prelude exists to be indistinguishable from them inside the served
subset, so the library is the oracle and any disagreement is the prelude's bug.
"""
from __future__ import annotations

import calendar
import datetime as _dt

import pytest

from pipeline import repair_rules as module


def _prelude_namespace(*keys):
    """Exec the named PRELUDE entries and hand back what they defined.

    The preludes are source strings, not importable objects, because they are
    pasted into a candidate program. Running them here is the only way to test
    the thing that actually ships.
    """
    ns: dict = {}
    for key in keys:
        exec(module.PRELUDE[key], ns)          # noqa: S102 - the unit under test
    return ns, module


def test_no_prelude_defines_a_class():
    """The engine refuses `class` outright, so a class shim can never be native.

    This is not style. `lypning-l -c "class C: pass"` exits 90 with
    `class: class definition`, which is how the `fractions` rule died after
    firing 13 times for 0 accepts. A prelude that grows a class is a rule that
    will never be accepted, and the failure shows up as an unexplained zero.
    """
    _, module = _prelude_namespace()
    for name, source in module.PRELUDE.items():
        assert "class " not in source, "%s defines a class; it can never run native" % name


@pytest.mark.parametrize("year", [1, 4, 100, 400, 1899, 1900, 1996, 2000, 2023, 2024,
                                  2100, 2400, 9999])
def test_isleap_matches_cpython_on_the_century_rule(year):
    """1900 is not a leap year and 2000 is; `% 4` alone gets both wrong."""
    ns, _ = _prelude_namespace("cal_isleap")
    assert ns["_cal_isleap"](year) == calendar.isleap(year)


def test_monthrange_matches_cpython_including_the_weekday_half():
    """Both elements, over every month of four centuries.

    No program in the queue reads the weekday half, so verification cannot see
    it wrong. A rule that returns a plausible first element and a wrong second
    is exactly the silent failure the differential oracle exists to prevent.
    """
    ns, _ = _prelude_namespace("cal_isleap", "cal_monthrange")
    for year in range(1700, 2101):
        for month in range(1, 13):
            assert ns["_cal_monthrange"](year, month) == calendar.monthrange(year, month), \
                "monthrange(%d, %d)" % (year, month)


def test_month_name_keeps_the_empty_first_element():
    """`calendar.month_name[1]` is January; index 0 is "". Off by one is silent."""
    ns, _ = _prelude_namespace("cal_month_name")
    assert list(ns["_CAL_MONTH_NAME"]) == list(calendar.month_name)
    assert ns["_CAL_MONTH_NAME"][0] == ""
    assert ns["_CAL_MONTH_NAME"][1] == "January"


def test_civil_day_roundtrip_matches_toordinal_across_four_centuries():
    """days-from-civil against CPython's ordinal, every day, both directions."""
    ns, _ = _prelude_namespace("dt_civil")
    day = _dt.date(1800, 1, 1)
    end = _dt.date(2200, 1, 1)
    base = _dt.date(1970, 1, 1).toordinal()
    step = _dt.timedelta(days=1)
    while day < end:
        got = ns["_dt_dfc"](day.year, day.month, day.day)
        assert got == day.toordinal() - base, day
        assert ns["_dt_cfd"](got) == (day.year, day.month, day.day), day
        day += step


@pytest.mark.parametrize("y,m,d", [(2024, 1, 1), (2024, 2, 29), (2024, 12, 31),
                                   (1900, 3, 1), (2000, 2, 29), (2023, 12, 31)])
def test_day_of_year_matches_cpython(y, m, d):
    ns, _ = _prelude_namespace("dt_civil", "dt_fields")
    days = ns["_dt_dfc"](y, m, d)
    assert ns["_dt_yday"](days) == _dt.date(y, m, d).timetuple().tm_yday


def test_date_construction_rejects_what_cpython_rejects():
    """Validity, not just arithmetic: 2023-02-29 must raise, 2024-02-29 must not."""
    ns, _ = _prelude_namespace("dt_civil", "dt_date")
    for y, m, d in [(2023, 2, 29), (2023, 13, 1), (2023, 0, 1), (2023, 1, 32), (2023, 4, 31)]:
        with pytest.raises(ValueError):
            ns["_dt_date"](y, m, d)
        with pytest.raises(ValueError):
            _dt.date(y, m, d)
    for y, m, d in [(2024, 2, 29), (2000, 2, 29), (1900, 2, 28), (2023, 12, 31)]:
        assert ns["_dt_cfd"](ns["_dt_date"](y, m, d)) == (y, m, d)


def test_strptime_accepts_the_same_strings_cpython_does():
    """%m and %d take one OR two digits, so `s[5:7]` is not the parser."""
    ns, _ = _prelude_namespace("dt_civil", "dt_date", "dt_strptime")
    fn = ns["_dt_strptime_ymd"]
    for good in ("2023-01-05", "2023-1-5", "2023-01-5", "0001-01-01", "9999-12-31",
                 "2024-02-29"):
        assert ns["_dt_cfd"](fn(good)) == (_dt.datetime.strptime(good, "%Y-%m-%d").year,
                                           _dt.datetime.strptime(good, "%Y-%m-%d").month,
                                           _dt.datetime.strptime(good, "%Y-%m-%d").day), good
    for bad in ("2023-01-00", "2023-13-01", "23-01-01", "2023/01/01", "", "2023-02-30",
                "2023-01-01x"):
        with pytest.raises(ValueError):
            fn(bad)
        with pytest.raises(ValueError):
            _dt.datetime.strptime(bad, "%Y-%m-%d")


def test_a_carrier_reaching_a_print_refuses_the_repair():
    """The leak that verification cannot see, pinned.

    A date is carried as an int day-count, so a carrier that reaches any
    stringifying position prints `21358` where the original printed
    `2028-06-23`. The first guard matched only a FIRST argument, so
    `print("far", d)` walked through it; with the leak on a branch the four
    tests never take, the wrong program was ACCEPTED. Every shape below must
    refuse rather than rewrite.
    """
    _, module = _prelude_namespace()
    for leak in ('print("far", d)',
                 'print("%s" % d)',
                 'print(",".join([str(d)]))',
                 'sys.stdout.write(str(d))',
                 'print(f"{d}")'):
        src = ("import datetime\n"
               "d = datetime.date(2023, 1, 1)\n"
               + leak + "\n")
        assert module.rule_datetime(src) is None, leak


def test_a_fractional_timedelta_refuses_the_repair():
    """`date + timedelta(days=1.5)` advances ONE day; `+ 1.5` advances 1.5.

    CPython truncates inside timedelta, so the rewrite is wrong in a way the
    source does not show. An integer literal or a plain name is rewritten; a
    float is refused.
    """
    _, module = _prelude_namespace()
    fractional = ("import datetime\n"
                  "d = datetime.date(2023, 1, 1) + datetime.timedelta(days=1.5)\n"
                  "print(d.year, d.month, d.day)\n")
    assert module.rule_datetime(fractional) is None
    integral = ("import datetime\n"
                "d = datetime.date(2023, 1, 1) + datetime.timedelta(days=3)\n"
                "print(d.year, d.month, d.day)\n")
    assert module.rule_datetime(integral) is not None


def test_a_clock_reader_is_never_repaired():
    """`date.today()` cannot become deterministic, so it must not be rewritten."""
    _, module = _prelude_namespace()
    for clock in ("datetime.date.today()", "datetime.datetime.now()",
                  "datetime.datetime.utcnow()", "datetime.date.fromtimestamp(0)"):
        src = "import datetime\nd = %s\nprint(d.year)\n" % clock
        assert module.rule_datetime(src) is None, clock


# --- the two loops must not disagree ------------------------------------------
#
# A repair rule teaches the model to route around a refusal. That is only
# defensible while the refusal is a stopgap the engine roadmap will remove, and
# it becomes a measured regression the day the engine removes it: gate B
# (`legality._gate_b`) probes the engine and scores every pair whose reference
# imports a served module. These two tests are the seam between the loops.

import os

from pipeline import legality, levers

STDLIB = pytest.mark.skipif(
    levers.stdlib_names() is None,
    reason="no sys.stdlib_module_names: the not-stdlib layer abstains and verdicts are incomplete by design")


@STDLIB
def test_every_rule_rewrites_a_module_with_an_engine_addressable_verdict():
    """A rule is the proof of addressability; the table must say so.

    The rewrite runs natively on the engine or the rule is withdrawn, so its
    module's used surface is expressible in the served subset by construction.
    A module with no verdict is a repair nobody classified; a module declared a
    legitimate fallback is a repair the programme has decided should not exist.
    Both fail here, with the bucket named, rather than in the weights.
    """
    stdlib = levers.stdlib_names()
    verdicts = {name: levers.classify("module", "import " + name, stdlib=stdlib)
                for name in module.MODULES}
    loose = {name: v["bucket"] for name, v in verdicts.items()
             if v["bucket"] != levers.ENGINE_ADDRESSABLE}
    assert not loose, (
        "repair rule(s) rewrite a module with no engine-addressable verdict in levers.DECLARED: "
        "%s. Declare the module (ENGINE_ADDRESSABLE, with why) or withdraw the rule." % loose)


def _built_engine():
    try:
        from lypning import paths
    except ImportError:
        return None
    candidate = paths.bin_dir() / "lypning-l"
    if candidate.is_file() and os.access(str(candidate), os.X_OK):
        return str(candidate)
    return None


def test_no_rule_rewrites_a_module_the_engine_serves():
    """The drift alarm: a rule outliving the refusal it repaired.

    Read against the built engine, never a table, because gate B reads the
    engine. The sentinel is not decoration: `modules_served` returns False for
    everything when the probe cannot run, and a test that then asserted
    "nothing is served" would pass on a broken instrument. `glob` has been
    served since iteration 77 (docs/HILLCLIMB.md), so it must read True first.
    """
    engine = _built_engine()
    if engine is None:
        pytest.skip("no built lypning-l under LYPNING_HOME; the adapt workflow runs this against the real binary")
    sentinel = legality.modules_served(engine, ["glob"])
    assert sentinel.get("glob") is True, (
        "the probe reports glob as refused, but the engine has served it since iteration 77: "
        "the instrument is broken, and every assertion below would pass vacuously")
    served = legality.modules_served(engine, module.MODULES)
    drifted = sorted(name for name, ok in served.items() if ok)
    assert not drifted, (
        "rule(s) %s rewrite module(s) the engine now serves. Delete the rule and retire its pairs "
        "from the bank: gate B probes the engine and scores each such pair as a supported-import "
        "regression." % drifted)
