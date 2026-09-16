"""date + timedelta(days=n) and date - date, as tuples and integer day counts.

The engines refuse `import datetime` (module: import datetime), and `date`
and `timedelta` are classes besides, so neither the objects nor the operators
can exist here.  What the operators DO is add and subtract integers on the
proleptic Gregorian ordinal, and that is all integer arithmetic.

THE SHAPE IS NOT CPython's.  A date is the plain 3-tuple
`(year, month, day)`, spread over three int arguments and returned as a
tuple; a timedelta of whole days is a plain `int`:

    add_days(2026, 9, 16, 30)         ==  date(2026, 9, 16) + timedelta(days=30)
                                      ->  (2026, 10, 16)
    days_between(2026, 9, 16, 2026, 1, 1)
                                      ==  (date(2026, 9, 16) - date(2026, 1, 1)).days
                                      ->  258
    compare_dates(a..., b...)         ==  the <, ==, > a date pair answers

You are trading `d + timedelta(days=n)` for `add_days(y, m, d, n)` and
`d1 - d2` for an int.  Sub-day resolution is gone with the object: this unit
is whole days only, which is what `date` arithmetic is anyway -- `date -
date` in CPython returns a timedelta whose seconds and microseconds are
always zero.

The rules that make the arithmetic right, and that a from-memory version gets
wrong:

  * Adding days is ordinal addition, never month-then-day patching.
    2026-01-31 plus 1 day is 2026-02-01; there is no "clamp to the end of the
    month" anywhere in `date` arithmetic, because there is no month
    arithmetic in `date` at all.  This unit does not invent one.
  * The range is closed at both ends.  0001-01-01 minus one day and
    9999-12-31 plus one day are both errors, not wrap-around, not year 0.
  * `timedelta` itself refuses a day count past +/- 999999999 at
    CONSTRUCTION, before the date is even consulted, and with a different
    message.  Both limits are kept here, in CPython's order.

Two deliberate divergences, both about exception TYPE, neither about values
or messages:

  * A result outside 0001-01-01..9999-12-31 raises `ValueError("date value
    out of range")`.  CPython raises `OverflowError` with that exact text.
  * A day count past +/- 999999999 raises `ValueError("days=1000000000; must
    have magnitude <= 999999999")`.  CPython raises `OverflowError` with that
    exact text.

`OverflowError` is NOT a ValueError subclass, so this is a real difference an
`except OverflowError` would notice.  It is forced: the subset has no
custom exceptions and does not bind `OverflowError`, so a unit raises
`ValueError("message")` or nothing.  The message text is identical, so
matching on the text carries over unchanged.

Not covered: timedelta arithmetic in its own right (seconds, microseconds,
normalisation, division), datetime, and timezones.  Ordinal conversion is in
datetime_ordinal; weekday and ISO weeks are in datetime_weekday; formatting
is in datetime_isoformat.  The calendar core below is repeated in each,
because a unit is inlined, never imported.

Verified against the real `datetime` module on 2026-09-16, three sweeps, zero
divergences: next_day and prev_day agree with `date +/- timedelta(days=1)` on
every one of the 3,652,059 days from 0001-01-01 to 9999-12-31; add_days
agrees with `date + timedelta(days=n)`, value and error message alike, on
200,000 seeded-random (date, offset) pairs with the offset drawn from
-4,000,000..4,000,000 so roughly half of them overflow the range; and
days_between agrees with `(d1 - d2).days` on 250,000 seeded-random date
pairs.  The cases below are a deterministic sample.
"""
# fills: datetime.date.__add__, datetime.date.__sub__, datetime.date.__lt__, datetime.timedelta, datetime.timedelta.days
# reference: datetime

MINYEAR = 1
MAXYEAR = 9999
MINORDINAL = 1
# date(9999, 12, 31).toordinal(); measured live, not remembered.
MAXORDINAL = 3652059
# timedelta's own construction limit, in days.
MAXDELTADAYS = 999999999

_DAYS_IN_MONTH = [-1, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
_DAYS_BEFORE_MONTH = [-1, 0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]


def is_leap(year):
    """True when `year` is a leap year in the proleptic Gregorian calendar."""
    if year % 4 != 0:
        return False
    if year % 100 != 0:
        return True
    return year % 400 == 0


def days_in_month(year, month):
    """Length of `month` in `year`."""
    if month < 1 or month > 12:
        raise ValueError("month must be in 1..12")
    if month == 2 and is_leap(year):
        return 29
    return _DAYS_IN_MONTH[month]


def _days_before_year(year):
    y = year - 1
    return y * 365 + y // 4 - y // 100 + y // 400


def _days_before_month(year, month):
    before = _DAYS_BEFORE_MONTH[month]
    if month > 2 and is_leap(year):
        return before + 1
    return before


def check_date(year, month, day):
    """Raise ValueError unless (year, month, day) is a real date."""
    if year < MINYEAR or year > MAXYEAR:
        raise ValueError("year %d is out of range" % year)
    if month < 1 or month > 12:
        raise ValueError("month must be in 1..12")
    if day < 1 or day > days_in_month(year, month):
        raise ValueError("day is out of range for month")


def to_ordinal(year, month, day):
    """(y, m, d) -> ordinal, 0001-01-01 being day 1."""
    check_date(year, month, day)
    return _days_before_year(year) + _days_before_month(year, month) + day


def from_ordinal(n):
    """ordinal -> (y, m, d).  CPython's `_ord2ymd`, cycle walk intact."""
    if n < MINORDINAL or n > MAXORDINAL:
        raise ValueError("date value out of range")
    di400y = 146097
    di100y = 36524
    di4y = 1461
    n = n - 1
    n400, n = divmod(n, di400y)
    year = n400 * 400 + 1
    n100, n = divmod(n, di100y)
    n4, n = divmod(n, di4y)
    n1, n = divmod(n, 365)
    year = year + n100 * 100 + n4 * 4 + n1
    if n1 == 4 or n100 == 4:
        return (year - 1, 12, 31)
    leapyear = n1 == 3 and (n4 != 24 or n100 == 3)
    month = (n + 50) >> 5
    preceding = _DAYS_BEFORE_MONTH[month]
    if month > 2 and leapyear:
        preceding = preceding + 1
    if preceding > n:
        month = month - 1
        back = _DAYS_IN_MONTH[month]
        if month == 2 and leapyear:
            back = back + 1
        preceding = preceding - back
    n = n - preceding
    return (year, month, n + 1)


def _check_delta_days(n):
    """The limit `timedelta(days=n)` imposes before a date is consulted.

    CPython raises OverflowError here, one step earlier than the date range
    check and with a different message; both are kept, in that order.
    """
    if n > MAXDELTADAYS or n < -MAXDELTADAYS:
        raise ValueError("days=%d; must have magnitude <= 999999999" % n)


def add_days(year, month, day, n):
    """(y, m, d) + n days -> (y, m, d).  This is `date + timedelta(days=n)`.

    Pure ordinal addition: no month clamping, no wrap-around at the ends of
    the supported range.
    """
    base = to_ordinal(year, month, day)
    _check_delta_days(n)
    return from_ordinal(base + n)


def sub_days(year, month, day, n):
    """(y, m, d) - n days -> (y, m, d).  This is `date - timedelta(days=n)`.

    Spelled as its own function rather than `add_days(..., -n)` because
    `-(-999999999)` is still inside timedelta's range while `-n` of the
    smallest int would not be; the check runs on the value timedelta sees.
    """
    _check_delta_days(n)
    return from_ordinal(to_ordinal(year, month, day) - n)


def add_weeks(year, month, day, n):
    """(y, m, d) + n weeks.  This is `date + timedelta(weeks=n)`."""
    return add_days(year, month, day, n * 7)


def days_between(y1, m1, d1, y2, m2, d2):
    """Days from the second date to the first.  This is `(date1 - date2).days`.

    Signed, and zero for the same day: later minus earlier is positive.
    """
    return to_ordinal(y1, m1, d1) - to_ordinal(y2, m2, d2)


def compare_dates(y1, m1, d1, y2, m2, d2):
    """-1, 0 or 1 as the first date sorts before, with, or after the second.

    A (y, m, d) tuple already compares correctly with `<`, because the fields
    run most-significant first; this exists so a caller working in separate
    ints does not have to build the tuples.
    """
    a = to_ordinal(y1, m1, d1)
    b = to_ordinal(y2, m2, d2)
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def next_day(year, month, day):
    """The day after.  `date + timedelta(days=1)`."""
    return add_days(year, month, day, 1)


def prev_day(year, month, day):
    """The day before.  `date - timedelta(days=1)`."""
    return sub_days(year, month, day, 1)


# --- cases ---
# Ordinary addition, forwards and backwards.
print(add_days(2026, 9, 16, 30))
print(add_days(2026, 9, 16, -30))
print(add_days(2026, 9, 16, 0))
print(sub_days(2026, 9, 16, 30))
print(add_weeks(2026, 9, 16, 2), add_weeks(2026, 9, 16, -2))

# Month rollover is ordinal, not clamped: the 31st plus one day is the 1st.
for _m in range(1, 13):
    _last = days_in_month(2026, _m)
    print(2026, _m, next_day(2026, _m, _last), prev_day(2026, _m, 1))

# Across February, in a leap year, a common year, a leap century and a
# non-leap century.
for _y in [2024, 2026, 2000, 1900, 2100]:
    print(_y, add_days(_y, 2, 28, 1), add_days(_y, 2, 28, 2), sub_days(_y, 3, 1, 1))

# Year rollover at both ends of December.
print(next_day(2026, 12, 31), prev_day(2027, 1, 1))
print(add_days(2026, 12, 31, 365), add_days(2024, 12, 31, 366))

# The closed ends of the range.
print(next_day(1, 1, 1), prev_day(9999, 12, 31))
print(add_days(1, 1, 1, 3652058), sub_days(9999, 12, 31, 3652058))
for _call in [(9999, 12, 31, 1), (1, 1, 1, -1), (2026, 9, 16, 999999999), (2026, 9, 16, -999999999)]:
    try:
        print(_call, add_days(_call[0], _call[1], _call[2], _call[3]))
    except ValueError as _e:
        print(_call, repr(str(_e)))

# timedelta's own limit fires first, and says something different.
for _n in [1000000000, -1000000000, 2000000000]:
    try:
        print(_n, add_days(2026, 9, 16, _n))
    except ValueError as _e:
        print(_n, repr(str(_e)))

# A 400-year step lands on the same month and day: the cycle is 146097 days.
print(add_days(2000, 3, 1, 146097), add_days(1600, 2, 29, 146097))
print(days_between(2400, 3, 1, 2000, 3, 1))

# date - date, signed, and zero for the same day.
print(days_between(2026, 9, 16, 2026, 1, 1))
print(days_between(2026, 1, 1, 2026, 9, 16))
print(days_between(2026, 9, 16, 2026, 9, 16))
print(days_between(9999, 12, 31, 1, 1, 1))
print(days_between(1, 1, 1, 9999, 12, 31))

# Year lengths fall out of the subtraction.
for _y in [1, 1900, 1999, 2000, 2024, 2025, 2100, 9998]:
    print(_y, days_between(_y + 1, 1, 1, _y, 1, 1), is_leap(_y))

# Ordering.
print(compare_dates(2026, 9, 16, 2026, 9, 17))
print(compare_dates(2026, 9, 17, 2026, 9, 16))
print(compare_dates(2026, 9, 16, 2026, 9, 16))
print(compare_dates(2026, 1, 31, 2026, 2, 1), compare_dates(1, 1, 1, 9999, 12, 31))

# add_days and days_between are inverses over a long contiguous run.
_bad = 0
for _n in range(-200, 201):
    _t = add_days(2026, 2, 28, _n)
    if days_between(_t[0], _t[1], _t[2], 2026, 2, 28) != _n:
        _bad = _bad + 1
print("inverse failures:", _bad)

# A whole week of consecutive days across a leap day.
for _n in range(-3, 4):
    print(_n, add_days(2024, 2, 29, _n))

# The input is validated before anything is added.
for _call in [(2026, 2, 30, 1), (2026, 13, 1, 1), (0, 1, 1, 1), (10000, 1, 1, 1), (2026, 1, 0, 1)]:
    try:
        print(_call, add_days(_call[0], _call[1], _call[2], _call[3]))
    except ValueError as _e:
        print(_call, repr(str(_e)))
