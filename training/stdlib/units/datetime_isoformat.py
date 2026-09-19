"""date.isoformat, date.fromisoformat, and strftime over %Y %m %d %H %M %S.

The engines refuse `import datetime` (module: import datetime) and `date` is
a class besides.  Formatting and parsing a civil date is string work over
integers, so all of it can be written out; what cannot come with it is the
object.

THE SHAPE IS NOT CPython's.  A date is the plain 3-tuple
`(year, month, day)`, a time is three more ints, and there is nothing to call
a method on:

    isoformat(2026, 9, 16)          ==  date(2026, 9, 16).isoformat()
                                    ->  '2026-09-16'
    fromisoformat('2026-W38-3')     ==  a (y, m, d) tuple, not a date
                                    ->  (2026, 9, 16)
    strftime('%Y-%m-%d %H:%M:%S', 2026, 9, 16, 13, 4, 5)
                                    ->  '2026-09-16 13:04:05'

You are trading `d.isoformat()` for `isoformat(y, m, d)` and
`date.fromisoformat(s)` for a tuple.  Every string these produce and accept
is the string CPython 3.11 produces and accepts.

fromisoformat is the FULL 3.11 grammar, not just YYYY-MM-DD.  3.11 widened it
and a from-memory version will be years out of date:

    '2026-09-16'   extended calendar date        -> (2026, 9, 16)
    '20260916'     basic calendar date           -> (2026, 9, 16)
    '2026-W38-3'   extended ISO week date        -> (2026, 9, 16)
    '2026W383'     basic ISO week date           -> (2026, 9, 16)
    '2026-W38'     week only; the day defaults to 1, Monday -> (2026, 9, 14)
    '2026W38'      the same, basic               -> (2026, 9, 14)

and nothing else.  The length must be exactly 7, 8 or 10 UTF-8 BYTES -- not
characters, which is visible the moment a non-ASCII character is in the
string; the dash separator
must be used consistently or not at all ('2026W38-3' is rejected); the digits
must be ASCII, so '2026-0\u0669-16' is rejected even though str.isdigit() is
true of that character; and there is no leading sign and no surrounding
whitespace.

The error messages are CPython's, and which one you get is worth knowing:

    structural problem      ValueError: Invalid isoformat string: '2026-9-16'
    week 0, 54, or a 53 that
    the year does not have  ValueError: Invalid isoformat string: '2025-W53-1'
    weekday outside 1..7    ValueError: Invalid isoformat string: '2026-W38-0'
    year 0000, calendar form ValueError: year 0 is out of range
    month outside 1..12     ValueError: month must be in 1..12
    day past the month end  ValueError: day is out of range for month
    week date past 9999     ValueError: year 10000 is out of range

Two of those are C-parser artifacts rather than anything a specification
would say, and both are reproduced because CPython is the oracle, not the
specification:

  * every validation failure inside the ISO-WEEK branch is re-reported as
    "Invalid isoformat string", so a bad week number and a bad weekday are
    indistinguishable from a malformed string, while the same failures in the
    calendar branch keep their own message; and
  * year 0000 in the WEEK branch says "month must be in 1..12", where year
    0000 in the calendar branch says "year 0 is out of range".

A third artifact is not a message but an acceptance: the parser reads
fixed-width fields at fixed offsets and never checks that it reached the end
of the string, so a BASIC (dash-free) form of length 10 silently IGNORES its
last two characters.  '20260916xx', '2026091612', '20260916--' and
'2026W383--' all parse to 2026-09-16, and '0232304809' is read as year 0232,
month 30 and rejected for the month rather than for the junk.  Because the
length gate counts bytes, '20260916' followed by ONE two-byte character is
ten bytes, takes that same path, and parses to 2026-09-16 as well.  The extended
forms escape this only because their separator check happens to land on the
offending character.  This unit reproduces it, tail and all.

All three were found by fuzzing this unit against the real parser; the third
one falsified a first, stricter version of this unit that rejected strings
CPython accepts.  Measured live on CPython 3.11.15 on 2026-09-16, not
reasoned about.

ONE DELIBERATE DIVERGENCE, in strftime, and it is the only one: %Y is always
four digits, zero-padded, so strftime('%Y', 1, 1, 1) is '0001'.  CPython
hands %Y to the platform's strftime, and on glibc that yields '1' for year 1,
'9' for year 9 and '999' for year 999 -- while on other platforms it pads,
and on some it refuses years below 1000 outright.  A platform-dependent
answer is not a usable oracle for a corpus, and the padded form is the one
that agrees with isoformat() and with ISO 8601, so it is the one written
here.  Years from 1000 on are byte-identical to CPython either way.

The directive set is exactly %Y %m %d %H %M %S and %% -- and anything else is
REJECTED with a ValueError rather than passed through.  Passing it through is
what CPython+glibc does with an unknown directive ('%q' comes back as '%q'),
but a %j or a %B that silently arrived as literal text would be a wrong
answer that looks like a right one, which is the failure mode this corpus
exists to avoid.  A stray '%' at the end of the format is rejected for the
same reason.

Not covered: strptime, time zones, fractional seconds, datetime.isoformat's
separator argument, and the locale-dependent directives (%a %A %b %B %c %p
%x %X %Z).  The calendar core below is repeated in the sibling units --
datetime_ordinal, datetime_weekday, datetime_arith -- because a unit is
inlined, never imported.

Verified against the real `datetime` module on 2026-09-16, zero divergences
outside the documented %Y one: isoformat agrees with `date.isoformat()` on
all 3,652,059 days from 0001-01-01 to 9999-12-31; fromisoformat agrees with
`date.fromisoformat`, value and error message alike, on the four
day-addressing spellings of all 3,652,059 days (14,608,236 strings), on both
week-only spellings of all 1,043,446 (ISO year, week) pairs from 0001 to
9999, and on 400,000 seeded-random strings of length 0 to 12 over the
alphabet '0123456789-W w+:T/.' widened with one two-byte, one three-byte and
one four-byte character; and strftime agrees with
`datetime.strftime` on 100,000 seeded-random (date, time, format) triples
drawn from the supported directives with the year at 1000 or above.
"""
# fills: datetime.date.isoformat, datetime.date.fromisoformat, datetime.date.strftime, datetime.datetime.strftime, datetime.datetime.isoformat
# reference: datetime

MINYEAR = 1
MAXYEAR = 9999

_DIGITS = "0123456789"
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


def _to_ordinal(year, month, day):
    """Unvalidated (y, m, d) -> ordinal; the ISO-week helper needs year 0."""
    return _days_before_year(year) + _days_before_month(year, month) + day


def _from_ordinal(n):
    """ordinal -> (y, m, d).  CPython's `_ord2ymd`, unvalidated on purpose."""
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


def check_date(year, month, day):
    """Raise ValueError unless (year, month, day) is a real date."""
    if year < MINYEAR or year > MAXYEAR:
        raise ValueError("year %d is out of range" % year)
    if month < 1 or month > 12:
        raise ValueError("month must be in 1..12")
    if day < 1 or day > days_in_month(year, month):
        raise ValueError("day is out of range for month")


def check_time(hour, minute, second):
    """Raise ValueError unless (hour, minute, second) is a real wall time."""
    if hour < 0 or hour > 23:
        raise ValueError("hour must be in 0..23")
    if minute < 0 or minute > 59:
        raise ValueError("minute must be in 0..59")
    if second < 0 or second > 59:
        raise ValueError("second must be in 0..59")


def isoformat(year, month, day):
    """(y, m, d) -> 'YYYY-MM-DD'.  This is `date.isoformat()` and `str(date)`.

    The year is always four digits: 0001-01-01, never 1-01-01.
    """
    check_date(year, month, day)
    return "%04d-%02d-%02d" % (year, month, day)


def datetime_isoformat(year, month, day, hour=0, minute=0, second=0, sep="T"):
    """(y, m, d, H, M, S) -> 'YYYY-MM-DDTHH:MM:SS'.

    This is `datetime.isoformat(sep)` for a whole-second, naive datetime;
    CPython omits the microsecond field when it is zero, and it is always
    zero here.
    """
    check_date(year, month, day)
    check_time(hour, minute, second)
    return "%04d-%02d-%02d%s%02d:%02d:%02d" % (
        year, month, day, sep, hour, minute, second)


def _uint(text, start, width):
    """`width` ASCII digits at `start` as an int, or -1.

    Not int(): int() accepts surrounding whitespace, a sign, underscores and
    every non-ASCII decimal digit in Unicode.  The C parser accepts none of
    those, so neither does this.
    """
    if start < 0 or start + width > len(text):
        return -1
    value = 0
    i = start
    while i < start + width:
        pos = _DIGITS.find(text[i])
        if pos < 0:
            return -1
        value = value * 10 + pos
        i = i + 1
    return value


def _utf8_len(text):
    """Length of `text` in UTF-8 BYTES, which is the length CPython gates on.

    `date.fromisoformat` accepts a string whose length is 7, 8 or 10 -- and
    the C parser measures that in bytes, not in characters.  So
    '20260916' plus one two-byte character is ten bytes long, takes the
    length-10 basic path, and parses as 2026-09-16 with its tail ignored,
    while the same string is nine CHARACTERS and would be rejected by a
    len() that counted those.  Counting here rather than encoding keeps the
    unit free of bytes handling.
    """
    total = 0
    for ch in text:
        code = ord(ch)
        if code < 128:
            total = total + 1
        elif code < 2048:
            total = total + 2
        elif code < 65536:
            total = total + 3
        else:
            total = total + 4
    return total


def _isoweek1monday(year):
    """Ordinal of the Monday opening ISO week 1 of `year`."""
    firstday = _to_ordinal(year, 1, 1)
    firstweekday = (firstday + 6) % 7
    week1monday = firstday - firstweekday
    if firstweekday > 3:
        week1monday = week1monday + 7
    return week1monday


def isoweeks_in_year(year):
    """52 or 53.  53 when Jan 1 is a Thursday, or a leap year's Wednesday."""
    first = _to_ordinal(year, 1, 1) % 7
    if first == 4:
        return 53
    if first == 3 and is_leap(year):
        return 53
    return 52


def fromisoformat(text):
    """An ISO 8601 date string -> (year, month, day).

    Accepts exactly what CPython 3.11's `date.fromisoformat` accepts: the
    extended and basic calendar dates, and the extended and basic ISO week
    dates with or without a weekday.
    """
    bad = "Invalid isoformat string: " + repr(text)
    # The gate is the UTF-8 byte length; the field reads below are by
    # character, which agrees because every field a read accepts is ASCII.
    blen = _utf8_len(text)
    if blen != 7 and blen != 8 and blen != 10:
        raise ValueError(bad)
    n = len(text)

    year = _uint(text, 0, 4)
    if year < 0:
        raise ValueError(bad)

    has_sep = text[4] == "-"
    pos = 4
    if has_sep:
        pos = 5

    if pos < n and text[pos] == "W":
        pos = pos + 1
        week = _uint(text, pos, 2)
        if week < 0:
            raise ValueError(bad)
        pos = pos + 2
        weekday = 1
        if n > pos:
            if (text[pos] == "-") != has_sep:
                raise ValueError(bad)
            if has_sep:
                pos = pos + 1
            weekday = _uint(text, pos, 1)
            if weekday < 0:
                raise ValueError(bad)
            pos = pos + 1
        # There is deliberately NO check that the whole string was consumed;
        # CPython's parser does not make one.  See the module docstring.
        # Everything that fails from here on in the week branch is reported
        # as a malformed string, which is what the C parser does.
        if week < 1 or week > 53:
            raise ValueError(bad)
        if week == 53 and isoweeks_in_year(year) != 53:
            raise ValueError(bad)
        if weekday < 1 or weekday > 7:
            raise ValueError(bad)
        ordinal = _isoweek1monday(year) + (week - 1) * 7 + (weekday - 1)
        ymd = _from_ordinal(ordinal)
        if ymd[0] > MAXYEAR:
            raise ValueError("year %d is out of range" % ymd[0])
        if ymd[0] < MINYEAR:
            # Year 0000 with a week date: the C parser reports the month, not
            # the year.  Measured, not reasoned about.
            raise ValueError("month must be in 1..12")
        return ymd

    month = _uint(text, pos, 2)
    if month < 0:
        raise ValueError(bad)
    pos = pos + 2
    if pos >= n:
        raise ValueError(bad)
    if (text[pos] == "-") != has_sep:
        raise ValueError(bad)
    if has_sep:
        pos = pos + 1
    day = _uint(text, pos, 2)
    if day < 0:
        raise ValueError(bad)
    # Again no end-of-string check: '20260916xx' really is 2026-09-16.
    check_date(year, month, day)
    return (year, month, day)


def strftime(fmt, year, month, day, hour=0, minute=0, second=0):
    """Render (y, m, d, H, M, S) through a %Y %m %d %H %M %S %% format.

    Any other directive, and a '%' at the end of the format, raise
    ValueError.  See the module docstring: %Y is always four digits.
    """
    check_date(year, month, day)
    check_time(hour, minute, second)
    out = []
    i = 0
    n = len(fmt)
    while i < n:
        ch = fmt[i]
        if ch != "%":
            out.append(ch)
            i = i + 1
            continue
        if i + 1 >= n:
            raise ValueError("stray '%' at the end of the format string")
        code = fmt[i + 1]
        if code == "%":
            out.append("%")
        elif code == "Y":
            out.append("%04d" % year)
        elif code == "m":
            out.append("%02d" % month)
        elif code == "d":
            out.append("%02d" % day)
        elif code == "H":
            out.append("%02d" % hour)
        elif code == "M":
            out.append("%02d" % minute)
        elif code == "S":
            out.append("%02d" % second)
        else:
            raise ValueError("directive not in this subset: %" + code)
        i = i + 2
    return "".join(out)


# --- cases ---
# isoformat pads the year to four digits at both ends of the range.
print(isoformat(1, 1, 1))
print(isoformat(9, 1, 2))
print(isoformat(99, 12, 31))
print(isoformat(999, 6, 30))
print(isoformat(1000, 1, 1))
print(isoformat(2026, 9, 16))
print(isoformat(9999, 12, 31))

# Every month boundary of a leap year and a century non-leap year.
for _y in [2024, 1900]:
    for _m in range(1, 13):
        print(isoformat(_y, _m, 1), isoformat(_y, _m, days_in_month(_y, _m)))

print(datetime_isoformat(2026, 9, 16))
print(datetime_isoformat(2026, 9, 16, 13, 4, 5))
print(datetime_isoformat(1, 1, 1, 0, 0, 0))
print(datetime_isoformat(9999, 12, 31, 23, 59, 59))
print(datetime_isoformat(2026, 9, 16, 13, 4, 5, " "))

# The six spellings CPython 3.11 accepts, all naming the same Wednesday.
for _s in ["2026-09-16", "20260916", "2026-W38-3", "2026W383"]:
    print(repr(_s), fromisoformat(_s))
# Without a weekday the day defaults to Monday.
for _s in ["2026-W38", "2026W38"]:
    print(repr(_s), fromisoformat(_s))

# Both ends of the range, both spellings.
for _s in ["0001-01-01", "00010101", "0001-W01-1", "0001W011", "9999-12-31", "99991231", "9999-W52-5"]:
    print(repr(_s), fromisoformat(_s))

# Round trip: isoformat out, fromisoformat back, over month boundaries,
# leap days and the century years.
_bad = 0
for _y in [1, 1900, 2000, 2024, 2026, 2100, 9999]:
    for _m in range(1, 13):
        for _d in [1, 15, days_in_month(_y, _m)]:
            if fromisoformat(isoformat(_y, _m, _d)) != (_y, _m, _d):
                _bad = _bad + 1
print("round-trip failures:", _bad)

# ISO week dates that are not in the Gregorian year they name.
for _s in ["2021-W01-1", "2020-W53-5", "2021-W53-1", "2026-W53-1", "2026-W53-7", "2025-W52-7", "2015-W53-4"]:
    try:
        print(repr(_s), fromisoformat(_s))
    except ValueError as _e:
        print(repr(_s), repr(str(_e)))

# Structural rejections: length, separator consistency, case, junk.
for _s in ["", "2026", "2026-09", "2026-9-16", "20260916x", " 2026-09-16", "2026-09-16 ", "2026/09/16", "202-09-16", "10000-01-01", "2026-w38-3", "2026W38-3", "2026-W383", "2026-W38-", "2026-W3", "2026W3", "0260916", "+026-09-16", "2026-Wab-1"]:
    try:
        print(repr(_s), fromisoformat(_s))
    except ValueError as _e:
        print(repr(_s), repr(str(_e)))

# Value rejections, each with its own message.
for _s in ["0000-01-01", "00000101", "2026-00-01", "2026-13-01", "2026-02-30", "2026-04-31", "20260231", "9999-W52-6", "0000-W01-1", "0000W011", "0000-W01-7", "0000-W53-1", "0000-W54-1", "0000-W38-0", "2026-W00-1", "2026-W54-1", "2025-W53-1", "2024-W53-1", "2026-W38-0", "2026-W38-8"]:
    try:
        print(repr(_s), fromisoformat(_s))
    except ValueError as _e:
        print(repr(_s), repr(str(_e)))

# The length gate counts UTF-8 bytes, so eight digits plus one two-byte
# character is a length-10 basic date whose tail is ignored.  Only the
# parsed tuple is printed: repr of a non-ASCII str is a closed kind.
print("eight digits + one two-byte char:", fromisoformat("20260916\u0669"))
print("eight digits + one three-byte char:", _utf8_len("20260916" + chr(0x4e00)))
try:
    print(fromisoformat("2026091" + chr(0x669)))
except ValueError:
    print("nine bytes rejected on length: True")

# ASCII digits only: str.isdigit() is true of U+0669 ARABIC-INDIC DIGIT NINE
# and of U+00B2 SUPERSCRIPT TWO, and neither is accepted.  Only the verdict
# is printed, never the string: repr of a non-ASCII str is a closed kind.
_rejected = 0
for _s in ["2026-0\u0669-16", "\u0662026-09-16", "2026-09-1\u00b2", "2026-W3\u0669-1"]:
    try:
        fromisoformat(_s)
    except ValueError:
        _rejected = _rejected + 1
print("non-ascii digit strings rejected:", _rejected, "of 4")
print("\u0669".isdigit(), "\u00b2".isdigit())

# strftime over the supported directives, year >= 1000.
print(repr(strftime("%Y-%m-%d", 2026, 9, 16)))
print(repr(strftime("%Y-%m-%d %H:%M:%S", 2026, 9, 16, 13, 4, 5)))
print(repr(strftime("%d/%m/%Y", 2026, 9, 16)))
print(repr(strftime("%Y%m%d", 2026, 9, 16)))
print(repr(strftime("%H%M%S", 2026, 9, 16, 0, 0, 0)))
print(repr(strftime("%Y-%m-%dT%H:%M:%S", 1000, 1, 1, 23, 59, 59)))
print(repr(strftime("%Y-%m-%dT%H:%M:%S", 9999, 12, 31, 23, 59, 59)))
print(repr(strftime("", 2026, 9, 16)))
print(repr(strftime("no directives at all", 2026, 9, 16)))
print(repr(strftime("%%", 2026, 9, 16)))
print(repr(strftime("%%Y", 2026, 9, 16)))
print(repr(strftime("100%% of %Y", 2026, 9, 16)))
print(repr(strftime("%Y%Y%Y", 2026, 9, 16)))

# Midnight and the last second of the day, zero-padded.
for _h, _mi, _s2 in [(0, 0, 0), (1, 2, 3), (9, 59, 59), (12, 0, 0), (23, 59, 59)]:
    print(repr(strftime("%H:%M:%S", 2026, 9, 16, _h, _mi, _s2)))

# Time fields are validated the way datetime validates them.
for _t in [(24, 0, 0), (-1, 0, 0), (0, 60, 0), (0, -1, 0), (0, 0, 60), (0, 0, -1)]:
    try:
        print(_t, repr(strftime("%H:%M:%S", 2026, 9, 16, _t[0], _t[1], _t[2])))
    except ValueError as _e:
        print(_t, repr(str(_e)))

# The date is validated too, before any formatting happens.
for _d2 in [(2026, 2, 30), (2026, 13, 1), (0, 1, 1), (10000, 1, 1)]:
    try:
        print(_d2, repr(strftime("%Y-%m-%d", _d2[0], _d2[1], _d2[2])))
    except ValueError as _e:
        print(_d2, repr(str(_e)))

# THE DOCUMENTED DIVERGENCE: %Y is four digits here; CPython on glibc gives
# '1', '9' and '999' for these three.  isoformat() agrees with the padding.
print(repr(strftime("%Y", 1, 1, 1)), repr(isoformat(1, 1, 1)))
print(repr(strftime("%Y-%m-%d", 9, 1, 2)), repr(isoformat(9, 1, 2)))
print(repr(strftime("%Y-%m-%d", 999, 6, 30)), repr(isoformat(999, 6, 30)))

# THE SUBSET CONTRACT: a directive outside %Y %m %d %H %M %S %% is refused,
# where CPython on glibc would pass an unknown one through and would render a
# known-but-unimplemented one.  These lines assert this unit's own API.
for _f in ["%j", "%A", "%B", "%q", "%y", "%", "ok %Y then %p"]:
    try:
        print(repr(_f), repr(strftime(_f, 2026, 9, 16, 13, 4, 5)))
    except ValueError as _e:
        print(repr(_f), repr(str(_e)))
