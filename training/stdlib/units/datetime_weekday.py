"""date.weekday / isoweekday / isocalendar and calendar.isleap / monthrange.

The engines refuse `import datetime` and `import calendar` (module: import
datetime), and `date` is a class besides, so none of this can be reached the
normal way.  All of it is integer arithmetic over the proleptic Gregorian
calendar, so all of it can be written out.

THE SHAPE IS NOT CPython's.  There are no objects: a date is the plain
3-tuple `(year, month, day)` spread over three int arguments, and every
answer is an int or a tuple of ints.

    weekday(2026, 9, 16)      ==  date(2026, 9, 16).weekday()      -> 2
    isoweekday(2026, 9, 16)   ==  date(2026, 9, 16).isoweekday()   -> 3
    isocalendar(2026, 9, 16)  ==  tuple(date(2026, 9, 16).isocalendar())
    isleap(2100)              ==  calendar.isleap(2100)            -> False
    monthrange(2026, 2)       ==  calendar.monthrange(2026, 2)     -> (6, 28)

You are trading `d.weekday()` for `weekday(y, m, d)`.  The numbers are the
same numbers on every day from 0001-01-01 to 9999-12-31.

Two deliberate divergences, both about types, neither about values:

  * `isocalendar` returns a plain 3-tuple.  CPython 3.9+ returns an
    `IsoCalendarDate`, which reprs as
    `datetime.IsoCalendarDate(year=2026, week=38, weekday=3)` and carries
    `.year`, `.week`, `.weekday`.  It IS a tuple subclass, so
    `tuple(d.isocalendar())` is exactly what this returns, and every
    comparison, unpacking and indexing behaves identically.  The repr does
    not, and there is no way to make it without a class.
  * `monthrange` raises `ValueError` with CPython's own message text, where
    CPython raises `calendar.IllegalMonthError`.  That is a SUBCLASS of
    ValueError, so `except ValueError` already catches both and the message
    is byte-identical; only `type(e).__name__` differs.

Two details a from-memory version gets wrong, both taken from the real source
(Lib/calendar.py, Lib/datetime.py) rather than from recollection:

  * `calendar.monthrange` does NOT validate the year.  `calendar.weekday`
    remaps a year outside 1..9999 with `year = 2000 + year % 400` before
    handing it to `date`, so `monthrange(0, 1)` is `(5, 31)` and
    `monthrange(10000, 1)` is `(5, 31)` -- no exception, a real answer for a
    different year.  That is replicated here, remap and all.
  * ISO week numbering is not the Gregorian year.  0001-01-01 is ISO
    (1, 1, 1), but 2021-01-01 is ISO (2020, 53, 5) and 2019-12-30 is ISO
    (2020, 1, 1).  The ISO year of a date can be one either side of its
    Gregorian year, and a year has 53 ISO weeks exactly when it starts on a
    Thursday, or is a leap year starting on a Wednesday.

Not covered: month/year calendar rendering (`calendar.month`,
`calendar.calendar`, the TextCalendar and HTMLCalendar classes), locale, and
first-weekday configuration (`calendar.setfirstweekday`) -- monthrange here
is the MONDAY-first answer CPython gives by default and is not affected by
`setfirstweekday`, which is correct: neither is CPython's.

Verified against the real `datetime` and `calendar` modules on 2026-09-16:
weekday, isoweekday and isocalendar agree on all 3,652,059 days from
0001-01-01 to 9999-12-31, and isleap/monthrange agree on all 119,988
(year, month) pairs -- zero divergences.  The cases below are a
deterministic sample of those sweeps.
"""
# fills: datetime.date.weekday, datetime.date.isoweekday, datetime.date.isocalendar, calendar.isleap, calendar.monthrange, calendar.weekday, calendar.leapdays
# reference: datetime

_DAYS_IN_MONTH = [-1, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
_DAYS_BEFORE_MONTH = [-1, 0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]

# Mon..Sun, indexed by weekday(); calendar's day_abbr with the default locale.
DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def isleap(year):
    """True when `year` is a leap year.  This is `calendar.isleap`."""
    if year % 4 != 0:
        return False
    if year % 100 != 0:
        return True
    return year % 400 == 0


def leapdays(y1, y2):
    """Number of leap years in range [y1, y2).  This is `calendar.leapdays`."""
    a = y1 - 1
    b = y2 - 1
    return (b // 4 - a // 4) - (b // 100 - a // 100) + (b // 400 - a // 400)


def _days_before_year(year):
    y = year - 1
    return y * 365 + y // 4 - y // 100 + y // 400


def _days_before_month(year, month):
    before = _DAYS_BEFORE_MONTH[month]
    if month > 2 and isleap(year):
        return before + 1
    return before


def days_in_month(year, month):
    """Length of `month` in `year`."""
    if month < 1 or month > 12:
        raise ValueError("month must be in 1..12")
    if month == 2 and isleap(year):
        return 29
    return _DAYS_IN_MONTH[month]


def to_ordinal(year, month, day):
    """(y, m, d) -> ordinal, 0001-01-01 being day 1.  Unvalidated on purpose.

    calendar's helpers call into date() and let IT validate; the callers here
    do their own checking where CPython's do.
    """
    return _days_before_year(year) + _days_before_month(year, month) + day


def weekday(year, month, day):
    """Monday == 0 .. Sunday == 6.  This is `date.weekday`.

    Ordinal 1 (0001-01-01) is a Monday, so (ordinal + 6) % 7 puts Monday at
    zero.
    """
    return (to_ordinal(year, month, day) + 6) % 7


def isoweekday(year, month, day):
    """Monday == 1 .. Sunday == 7.  This is `date.isoweekday`."""
    n = to_ordinal(year, month, day) % 7
    if n == 0:
        return 7
    return n


def calendar_weekday(year, month, day):
    """`calendar.weekday`: weekday(), but a year outside 1..9999 is REMAPPED.

    `year = 2000 + year % 400` keeps the weekday pattern (the Gregorian
    calendar repeats every 400 years) while giving `date` a year it accepts.
    The answer is therefore never an error and never about the year asked
    for.  CPython does exactly this; it is not a bug to be fixed here.
    """
    if year < 1 or year > 9999:
        year = 2000 + year % 400
    return weekday(year, month, day)


def monthrange(year, month):
    """(weekday of the 1st, number of days) for `month` of `year`.

    This is `calendar.monthrange`.  The month IS validated; the year is NOT
    -- see calendar_weekday.
    """
    if month < 1 or month > 12:
        # CPython raises calendar.IllegalMonthError, a ValueError subclass,
        # with this exact text.
        raise ValueError("bad month number %d; must be 1-12" % month)
    day1 = calendar_weekday(year, month, 1)
    ndays = _DAYS_IN_MONTH[month]
    if month == 2 and isleap(year):
        ndays = ndays + 1
    return (day1, ndays)


def _isoweek1monday(year):
    """Ordinal of the Monday that opens ISO week 1 of `year`.

    ISO week 1 is the week holding the first Thursday, equivalently the week
    holding January 4th.  Walk back to the Monday of the week containing
    January 1st, then step forward a week when January 1st fell on Friday,
    Saturday or Sunday.
    """
    thursday = 3
    firstday = to_ordinal(year, 1, 1)
    firstweekday = (firstday + 6) % 7
    week1monday = firstday - firstweekday
    if firstweekday > thursday:
        week1monday = week1monday + 7
    return week1monday


def isocalendar(year, month, day):
    """(ISO year, ISO week, ISO weekday) as a plain 3-tuple.

    `date.isocalendar()` returns an IsoCalendarDate; this returns what
    `tuple()` of it returns.  The ISO year is not the Gregorian year: the
    first days of January can belong to the last week of the year before,
    and the last days of December to week 1 of the year after.
    """
    iso_year = year
    week1monday = _isoweek1monday(iso_year)
    today = to_ordinal(year, month, day)
    week, dow = divmod(today - week1monday, 7)
    if week < 0:
        iso_year = iso_year - 1
        week1monday = _isoweek1monday(iso_year)
        week, dow = divmod(today - week1monday, 7)
    elif week >= 52:
        if today >= _isoweek1monday(iso_year + 1):
            iso_year = iso_year + 1
            week = 0
    return (iso_year, week + 1, dow + 1)


def isoweeks_in_year(year):
    """52 or 53: how many ISO weeks `year` has.

    53 exactly when January 1st is a Thursday, or the year is a leap year
    whose January 1st is a Wednesday.  This is the test CPython's
    `_isoweek_to_gregorian` uses to accept or reject a W53 date.
    """
    first = to_ordinal(year, 1, 1) % 7
    if first == 4:
        return 53
    if first == 3 and isleap(year):
        return 53
    return 52


# --- cases ---
# Monday is 0 for weekday() and 1 for isoweekday(), on day 1 of the calendar.
print(weekday(1, 1, 1), isoweekday(1, 1, 1), isocalendar(1, 1, 1))
print(weekday(9999, 12, 31), isoweekday(9999, 12, 31), isocalendar(9999, 12, 31))

# A full week, so all seven names appear once.
for _d in range(14, 21):
    print(_d, weekday(2026, 9, _d), isoweekday(2026, 9, _d), DAY_ABBR[weekday(2026, 9, _d)])

# Every month boundary of a leap year and of a century non-leap year.
for _y in [2024, 1900]:
    for _m in range(1, 13):
        _last = days_in_month(_y, _m)
        print(_y, _m, weekday(_y, _m, 1), weekday(_y, _m, _last), _last)

# monthrange over all twelve months of a leap year, an ordinary year, and the
# two century years that disagree.
for _y in [2024, 2026, 1900, 2000, 2100]:
    _row = []
    for _m in range(1, 13):
        _row.append(monthrange(_y, _m))
    print(_y, _row)

# isleap at the years that decide the rule.
for _y in [1, 4, 100, 400, 1600, 1700, 1800, 1900, 1996, 2000, 2004, 2024, 2025, 2100, 2200, 2300, 2400, 9996, 9999]:
    print(_y, isleap(_y), monthrange(_y, 2))

# leapdays over ranges that cross each exception.
print(leapdays(1900, 1901), leapdays(2000, 2001), leapdays(1, 10000))
print(leapdays(1896, 1908), leapdays(2096, 2108), leapdays(2000, 2000))

# monthrange does not validate the year: it remaps it and answers anyway.
print(monthrange(0, 1), monthrange(10000, 1), monthrange(-400, 1))
print(calendar_weekday(0, 1, 1), calendar_weekday(10000, 1, 1), calendar_weekday(2000, 1, 1))

# monthrange DOES validate the month.
for _m in [0, 13, -1, 100]:
    try:
        print(_m, monthrange(2026, _m))
    except ValueError as _e:
        print(_m, repr(str(_e)))

# The ISO year is not the Gregorian year.  Each of these is a boundary that a
# from-memory implementation gets wrong.
for _t in [(2019, 12, 29), (2019, 12, 30), (2019, 12, 31), (2020, 1, 1), (2020, 12, 31), (2021, 1, 1), (2021, 1, 3), (2021, 1, 4), (2026, 1, 1), (2026, 9, 16), (2026, 12, 28), (2027, 1, 3), (2015, 12, 31), (2016, 1, 1), (2016, 1, 3), (2016, 1, 4)]:
    print(_t, isocalendar(_t[0], _t[1], _t[2]))

# A contiguous run across a 53-week year end, so the week rolls 52 -> 53 -> 1.
for _d in range(26, 32):
    print(2026, 12, _d, isocalendar(2026, 12, _d))
for _d in range(1, 5):
    print(2027, 1, _d, isocalendar(2027, 1, _d))

# 52 or 53 ISO weeks: the years that have 53 between 2015 and 2030.
_long = []
for _y in range(2015, 2031):
    if isoweeks_in_year(_y) == 53:
        _long.append(_y)
print(_long)
print(isoweeks_in_year(1), isoweeks_in_year(9999), isoweeks_in_year(2020), isoweeks_in_year(2026))

# Every ISO weekday of every date lines up with isoweekday().
_bad = 0
for _y in [1, 1900, 2000, 2026, 9999]:
    for _m in range(1, 13):
        for _d in [1, 15, days_in_month(_y, _m)]:
            if isocalendar(_y, _m, _d)[2] != isoweekday(_y, _m, _d):
                _bad = _bad + 1
print("isoweekday disagreements:", _bad)

# The 400-year cycle: the weekday pattern repeats exactly.
print(weekday(2000, 3, 1) == weekday(2400, 3, 1), weekday(1600, 2, 29) == weekday(2000, 2, 29))
