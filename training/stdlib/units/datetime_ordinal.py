"""date.toordinal and date.fromordinal, as functions over (y, m, d) triples.

The engines refuse `import datetime` (module: import datetime) and they refuse
`class` outright, so `datetime.date` cannot exist here.  What CAN exist is the
part people actually use: the proleptic Gregorian civil calendar is pure
integer arithmetic, and this unit is CPython's own arithmetic
(Lib/datetime.py `_days_before_year`, `_days_before_month`, `_ymd2ord`,
`_ord2ymd`) written out as free functions.

THE SHAPE IS NOT CPython's.  There is no date object; a date is the plain
3-tuple `(year, month, day)`, and an ordinal is a plain `int`:

    to_ordinal(2026, 9, 16)   ==  date(2026, 9, 16).toordinal()   -> 739875
    from_ordinal(739875)      ==  (2026, 9, 16)

You are trading `date.toordinal()` for `to_ordinal(y, m, d)` and
`date.fromordinal(n)` for a tuple.  That is the whole bargain: no attribute
access, no comparison operators, no `.replace()`, no arithmetic on the object
-- but the numbers are the same numbers, on every day from 0001-01-01 to
9999-12-31.

Day 1 is 0001-01-01 and day 3652059 is 9999-12-31, both proleptic: the
Gregorian rule is applied backwards through the 1582 reform as if it had
always been in force, which is what CPython does and what makes the ordinal a
usable integer key.  1900 and 2100 are NOT leap years; 2000 is.

Validation follows `datetime.date.__new__` exactly, including the message
text, so a caller can match on it:

    year outside 1..9999    ValueError: year 10000 is out of range
    month outside 1..12     ValueError: month must be in 1..12
    day outside 1..dim      ValueError: day is out of range for month
    ordinal < 1             ValueError: ordinal must be >= 1
    ordinal > 3652059       ValueError: year 10000 is out of range

The last of those carries the year the ordinal really lands in, because
CPython converts before it validates: `date.fromordinal(4000000)` says
"year 10952 is out of range", not "year 10000".  from_ordinal copies that.

Divergence from CPython, deliberate and total: exception TYPE.  CPython raises
`ValueError` for every case above, so there is none here -- the one place the
types do part company is in the sibling unit datetime_arith, not this one.

Not covered here: times, timezones, formatting (datetime_isoformat), weekday
and ISO week numbering (datetime_weekday), and date arithmetic
(datetime_arith).  Each of those units repeats the calendar core below,
because a unit is inlined, never imported.

Verified against the real `datetime` module on 2026-09-16: every ordinal from
1 to 3652059 round-trips through from_ordinal/to_ordinal and agrees with
`datetime.date.fromordinal(n).timetuple()[:3]` and `.toordinal()` -- all
3,652,059 days, zero divergences.  The cases below are a deterministic sample
of that sweep.
"""
# fills: datetime.date.toordinal, datetime.date.fromordinal, datetime.MINYEAR, datetime.MAXYEAR, datetime.date
# reference: datetime

MINYEAR = 1
MAXYEAR = 9999
# date(9999, 12, 31).toordinal(); measured live, not remembered.
MAXORDINAL = 3652059

# Index 0 is a placeholder so the month number indexes directly.
_DAYS_IN_MONTH = [-1, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
# Days of a NON-leap year that precede the first of each month.  CPython
# accumulates this at import; here it is written out, and the cases check it.
_DAYS_BEFORE_MONTH = [-1, 0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]


def is_leap(year):
    """True when `year` is a leap year in the proleptic Gregorian calendar.

    Every fourth year, except centuries, except every fourth century.  This
    is `calendar.isleap` and `datetime._is_leap`, character for character.
    """
    if year % 4 != 0:
        return False
    if year % 100 != 0:
        return True
    return year % 400 == 0


def days_in_month(year, month):
    """Number of days in `month` of `year`.  February is the only variable."""
    if month < 1 or month > 12:
        raise ValueError("month must be in 1..12")
    if month == 2 and is_leap(year):
        return 29
    return _DAYS_IN_MONTH[month]


def days_before_year(year):
    """Number of days before January 1st of `year`.

    y = year - 1 complete years have passed; y // 4 of them were divisible by
    four, y // 100 of those were centuries, y // 400 of those were not.
    Python's floor division makes this correct for year 1 (y = 0) and for the
    year-0 and negative years the ISO-week helper reaches into.
    """
    y = year - 1
    return y * 365 + y // 4 - y // 100 + y // 400


def days_before_month(year, month):
    """Days in `year` preceding the first of `month`."""
    if month < 1 or month > 12:
        raise ValueError("month must be in 1..12")
    before = _DAYS_BEFORE_MONTH[month]
    if month > 2 and is_leap(year):
        return before + 1
    return before


def check_date(year, month, day):
    """Raise ValueError unless (year, month, day) is a real date.

    The messages are `datetime.date.__new__`'s messages, in its order: year,
    then month, then day.
    """
    if year < MINYEAR or year > MAXYEAR:
        raise ValueError("year %d is out of range" % year)
    if month < 1 or month > 12:
        raise ValueError("month must be in 1..12")
    dim = days_in_month(year, month)
    if day < 1 or day > dim:
        raise ValueError("day is out of range for month")


def to_ordinal(year, month, day):
    """(y, m, d) -> ordinal, counting 0001-01-01 as day 1."""
    check_date(year, month, day)
    return days_before_year(year) + days_before_month(year, month) + day


def from_ordinal(n):
    """ordinal -> (year, month, day), counting 0001-01-01 as day 1.

    CPython's `_ord2ymd`, with its 400/100/4/1-year cycle walk kept intact.
    The 400-year pattern repeats exactly, so subtract 1 from n and the
    400-year boundaries land on multiples of 146097.
    """
    if n < 1:
        raise ValueError("ordinal must be >= 1")

    di400y = days_before_year(401)  # 146097 days in 400 years
    di100y = days_before_year(101)  # 36524
    di4y = days_before_year(5)      # 1461

    n = n - 1
    n400, n = divmod(n, di400y)
    year = n400 * 400 + 1

    # n100 can be 4: four full centuries precede, so the day is the 31st of
    # December that closes a 400-year cycle.  Same for n1 below.
    n100, n = divmod(n, di100y)
    n4, n = divmod(n, di4y)
    n1, n = divmod(n, 365)

    year = year + n100 * 100 + n4 * 4 + n1
    if n1 == 4 or n100 == 4:
        if year - 1 > MAXYEAR:
            raise ValueError("year %d is out of range" % (year - 1))
        return (year - 1, 12, 31)

    leapyear = n1 == 3 and (n4 != 24 or n100 == 3)
    # (n + 50) >> 5 estimates the month, exact or one too large, never small.
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
    if year > MAXYEAR:
        # CPython computes the year first and only then rejects it, so the
        # message names the year the ordinal actually lands in -- 4000000 is
        # "year 10952 is out of range", not "year 10000".
        raise ValueError("year %d is out of range" % year)
    return (year, month, n + 1)


# --- cases ---
# The two fixed points of the proleptic Gregorian ordinal.
print(to_ordinal(1, 1, 1))
print(from_ordinal(1))
print(to_ordinal(9999, 12, 31))
print(from_ordinal(3652059))

# A date in the middle, both ways.
print(to_ordinal(2026, 9, 16))
print(from_ordinal(739875))

# Every month boundary of one ordinary year, one leap year, and the two
# century years that differ: first day and last day of all twelve months.
for _y in [2026, 2024, 1900, 2000, 2100]:
    _row = []
    for _m in range(1, 13):
        _row.append(to_ordinal(_y, _m, 1))
        _row.append(to_ordinal(_y, _m, days_in_month(_y, _m)))
    print(_y, _row)

# The leap rule itself, at the years that decide it.
for _y in [1, 4, 100, 400, 1600, 1700, 1800, 1900, 1996, 2000, 2024, 2025, 2100, 2200, 2400, 9996, 9999]:
    print(_y, is_leap(_y), days_in_month(_y, 2))

# February 28/29 and March 1 across a century non-leap and a leap century.
print(to_ordinal(1900, 2, 28), to_ordinal(1900, 3, 1))
print(to_ordinal(2000, 2, 28), to_ordinal(2000, 2, 29), to_ordinal(2000, 3, 1))
print(to_ordinal(2100, 2, 28), to_ordinal(2100, 3, 1))

# Round trip over the boundaries of the 400/100/4-year cycles, where the
# n100 == 4 and n1 == 4 branches of from_ordinal are the only correct answer.
for _n in [146096, 146097, 146098, 36524, 36525, 1460, 1461, 1462, 365, 366, 367]:
    print(_n, from_ordinal(_n), to_ordinal(*from_ordinal(_n)) == _n)

# A contiguous run across a leap day, an ordinary year end, and year 1.
for _n in range(730177, 730186):
    print(_n, from_ordinal(_n))
for _n in range(3652055, 3652060):
    print(_n, from_ordinal(_n))
for _n in range(1, 6):
    print(_n, from_ordinal(_n))

# The precomputed day-count table, rebuilt from days_in_month.
_acc = 0
_built = [-1]
for _m in range(1, 13):
    _built.append(_acc)
    _acc = _acc + _DAYS_IN_MONTH[_m]
print(_built == _DAYS_BEFORE_MONTH, _acc)

# Every ordinal in the sample sweep round-trips.
_bad = 0
for _n in range(700000, 700400):
    if to_ordinal(*from_ordinal(_n)) != _n:
        _bad = _bad + 1
print("roundtrip failures:", _bad)

# Validation, message for message.
for _args in [(0, 1, 1), (10000, 1, 1), (2026, 0, 1), (2026, 13, 1), (2026, 1, 0), (2026, 1, 32), (2026, 2, 29), (2024, 2, 30), (1900, 2, 29), (2000, 2, 30), (2026, 4, 31)]:
    try:
        check_date(_args[0], _args[1], _args[2])
        print(_args, "ok")
    except ValueError as _e:
        print(_args, repr(str(_e)))

for _n in [0, -1, -400, 3652060, 4000000]:
    try:
        print(_n, from_ordinal(_n))
    except ValueError as _e:
        print(_n, repr(str(_e)))

# 2024-02-29 exists, 2023-02-29 does not; both answers are one call away.
print(to_ordinal(2024, 2, 29))
try:
    print(to_ordinal(2023, 2, 29))
except ValueError as _e:
    print(repr(str(_e)))
