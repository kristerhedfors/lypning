# lypning-mp frozen shim: datetime (proleptic Gregorian date/datetime/timedelta,
# naive only — no tzinfo, no %-locale directives). See micropython/lib/README.md.
import time as _time

MINYEAR = 1
MAXYEAR = 9999

_DIM = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_DAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def _isleap(y):
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def _dim(y, m):
    return 29 if (m == 2 and _isleap(y)) else _DIM[m - 1]


def _toord(y, m, d):
    p = y - 1
    n = p * 365 + p // 4 - p // 100 + p // 400
    for i in range(1, m):
        n += _dim(y, i)
    return n + d


def _fromord(n):
    y = (n - 1) // 366 + 1
    while _toord(y + 1, 1, 1) <= n:
        y += 1
    r = n - _toord(y, 1, 1) + 1
    m = 1
    while r > _dim(y, m):
        r -= _dim(y, m)
        m += 1
    return (y, m, r)


class timedelta:
    def __init__(
        self,
        days=0,
        seconds=0,
        microseconds=0,
        milliseconds=0,
        minutes=0,
        hours=0,
        weeks=0,
    ):
        us = int(microseconds + milliseconds * 1000)
        total = (weeks * 7 + days) * 86400 + hours * 3600 + minutes * 60 + seconds
        total += us // 1000000
        self.microseconds = us % 1000000
        self.days = int(total // 86400)
        self.seconds = int(total - self.days * 86400)

    def total_seconds(self):
        return self.days * 86400 + self.seconds + self.microseconds / 1000000

    def _us(self):
        return (self.days * 86400 + self.seconds) * 1000000 + self.microseconds

    def __add__(self, o):
        return timedelta(microseconds=self._us() + o._us())

    def __sub__(self, o):
        return timedelta(microseconds=self._us() - o._us())

    def __neg__(self):
        return timedelta(microseconds=-self._us())

    def __eq__(self, o):
        return isinstance(o, timedelta) and self._us() == o._us()

    def __lt__(self, o):
        return self._us() < o._us()

    def __le__(self, o):
        return self._us() <= o._us()

    def __gt__(self, o):
        return self._us() > o._us()

    def __ge__(self, o):
        return self._us() >= o._us()

    def __str__(self):
        d, s, us = self.days, self.seconds, self.microseconds
        r = "%d:%02d:%02d" % (s // 3600, s // 60 % 60, s % 60)
        if us:
            r += ".%06d" % us
        if d:
            r = "%d day%s, " % (d, "" if d in (1, -1) else "s") + r
        return r

    def __repr__(self):
        return "datetime.timedelta(days=%d, seconds=%d, microseconds=%d)" % (
            self.days,
            self.seconds,
            self.microseconds,
        )


def _fmt(spec, y, mo, d, h, mi, s, us):
    wd = (_toord(y, mo, d) + 6) % 7  # Monday == 0
    yday = _toord(y, mo, d) - _toord(y, 1, 1) + 1
    out = []
    i = 0
    n = len(spec)
    while i < n:
        c = spec[i]
        if c != "%":
            out.append(c)
            i += 1
            continue
        i += 1
        k = spec[i : i + 1]
        i += 1
        if k == "Y":
            out.append("%04d" % y)
        elif k == "y":
            out.append("%02d" % (y % 100))
        elif k == "m":
            out.append("%02d" % mo)
        elif k == "d":
            out.append("%02d" % d)
        elif k == "H":
            out.append("%02d" % h)
        elif k == "M":
            out.append("%02d" % mi)
        elif k == "S":
            out.append("%02d" % s)
        elif k == "f":
            out.append("%06d" % us)
        elif k == "j":
            out.append("%03d" % yday)
        elif k == "p":
            out.append("AM" if h < 12 else "PM")
        elif k == "I":
            out.append("%02d" % (h % 12 or 12))
        elif k == "A":
            out.append(_DAYS[wd])
        elif k == "a":
            out.append(_DAYS[wd][:3])
        elif k == "B":
            out.append(_MONTHS[mo - 1])
        elif k == "b":
            out.append(_MONTHS[mo - 1][:3])
        elif k == "F":
            out.append("%04d-%02d-%02d" % (y, mo, d))
        elif k == "T":
            out.append("%02d:%02d:%02d" % (h, mi, s))
        elif k == "%":
            out.append("%")
        else:
            out.append("%" + k)
    return "".join(out)


class date:
    def __init__(self, year, month, day):
        if not 1 <= month <= 12 or not 1 <= day <= _dim(year, month):
            raise ValueError("day is out of range for month")
        self.year = year
        self.month = month
        self.day = day

    @classmethod
    def fromordinal(cls, n):
        y, m, d = _fromord(n)
        return cls(y, m, d)

    @classmethod
    def fromisoformat(cls, s):
        return cls(int(s[0:4]), int(s[5:7]), int(s[8:10]))

    @classmethod
    def today(cls):
        t = _time.localtime()
        return cls(t[0], t[1], t[2])

    def toordinal(self):
        return _toord(self.year, self.month, self.day)

    def weekday(self):
        return (self.toordinal() + 6) % 7

    def isoweekday(self):
        return self.weekday() + 1

    def isoformat(self):
        return "%04d-%02d-%02d" % (self.year, self.month, self.day)

    def strftime(self, spec):
        return _fmt(spec, self.year, self.month, self.day, 0, 0, 0, 0)

    def replace(self, year=None, month=None, day=None):
        return date(
            self.year if year is None else year,
            self.month if month is None else month,
            self.day if day is None else day,
        )

    def __str__(self):
        return self.isoformat()

    def __repr__(self):
        return "datetime.date(%d, %d, %d)" % (self.year, self.month, self.day)

    def __add__(self, o):
        return date.fromordinal(self.toordinal() + o.days)

    def __sub__(self, o):
        if isinstance(o, timedelta):
            return date.fromordinal(self.toordinal() - o.days)
        return timedelta(days=self.toordinal() - o.toordinal())

    def _key(self):
        return self.toordinal()

    def __eq__(self, o):
        return isinstance(o, date) and self._key() == o._key()

    def __lt__(self, o):
        return self._key() < o._key()

    def __le__(self, o):
        return self._key() <= o._key()

    def __gt__(self, o):
        return self._key() > o._key()

    def __ge__(self, o):
        return self._key() >= o._key()


class datetime(date):
    def __init__(self, year, month, day, hour=0, minute=0, second=0, microsecond=0):
        date.__init__(self, year, month, day)
        self.hour = hour
        self.minute = minute
        self.second = second
        self.microsecond = microsecond

    @classmethod
    def now(cls, tz=None):
        t = _time.time()
        lt = _time.localtime(int(t))
        return cls(lt[0], lt[1], lt[2], lt[3], lt[4], lt[5], int((t % 1) * 1000000))

    utcnow = now

    @classmethod
    def fromtimestamp(cls, ts):
        lt = _time.localtime(int(ts))
        return cls(lt[0], lt[1], lt[2], lt[3], lt[4], lt[5], int((ts % 1) * 1000000))

    @classmethod
    def fromisoformat(cls, s):
        s = s.replace("T", " ")
        d, _, t = s.partition(" ")
        y, mo, dd = int(d[0:4]), int(d[5:7]), int(d[8:10])
        h = mi = sec = us = 0
        if t:
            h = int(t[0:2])
            mi = int(t[3:5])
            if len(t) >= 8:
                sec = int(t[6:8])
            if len(t) > 9 and t[8] == ".":
                us = int((t[9:15] + "000000")[:6])
        return cls(y, mo, dd, h, mi, sec, us)

    @classmethod
    def combine(cls, d, t):
        return cls(d.year, d.month, d.day, t.hour, t.minute, t.second, t.microsecond)

    def date(self):
        return date(self.year, self.month, self.day)

    def timestamp(self):
        return _time.mktime(
            (self.year, self.month, self.day, self.hour, self.minute, self.second, 0, 0)
        ) + self.microsecond / 1000000

    def isoformat(self, sep="T"):
        r = "%04d-%02d-%02d%s%02d:%02d:%02d" % (
            self.year,
            self.month,
            self.day,
            sep,
            self.hour,
            self.minute,
            self.second,
        )
        if self.microsecond:
            r += ".%06d" % self.microsecond
        return r

    def strftime(self, spec):
        return _fmt(
            spec,
            self.year,
            self.month,
            self.day,
            self.hour,
            self.minute,
            self.second,
            self.microsecond,
        )

    def _key(self):
        return (
            (self.toordinal() * 86400 + self.hour * 3600 + self.minute * 60 + self.second)
            * 1000000
            + self.microsecond
        )

    def __str__(self):
        return self.isoformat(" ")

    def __repr__(self):
        return "datetime.datetime(%d, %d, %d, %d, %d, %d)" % (
            self.year,
            self.month,
            self.day,
            self.hour,
            self.minute,
            self.second,
        )

    def __add__(self, o):
        us = self._key() + o._us()
        n, rem = us // 86400000000, us % 86400000000
        y, mo, d = _fromord(n)
        sec, micro = rem // 1000000, rem % 1000000
        return datetime(y, mo, d, sec // 3600, sec // 60 % 60, sec % 60, micro)

    def __sub__(self, o):
        if isinstance(o, timedelta):
            return self.__add__(-o)
        return timedelta(microseconds=self._key() - o._key())
