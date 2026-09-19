"""statistics.median, median_low and median_high without importing statistics.

The engines refuse `import statistics` (module: import statistics), so the three
positional medians are written out here. They are pure ordering: sort a copy of
the data, then index. CPython's own implementation (Lib/statistics.py) is
exactly this, so there is nothing approximate about these helpers.

Covered, matching CPython 3.11 value for value:
  - median(data): the middle value when len(data) is odd, and
    (data[i-1] + data[i]) / 2 when it is even. The even case goes through true
    division, so median([1, 2, 3, 4]) is the float 2.5 and median([2, 2]) is
    2.0, never the int 2 — the odd case returns the element untouched, so
    median([1, 3, 5]) is the int 3. repr() in the cases keeps the two apart.
  - median_low(data): data[n // 2 - 1] on even input, so no arithmetic happens
    and the element type survives. Works on anything sortable, strings included.
  - median_high(data): data[n // 2], the same on odd input, the upper of the
    two middles on even input.

Empty data: CPython raises StatisticsError("no median for empty data").
StatisticsError is a subclass of ValueError and the subset has no class
statement, so these raise ValueError with CPython's own message text; `except
ValueError` catches both, and the cases print the message to show it.

Deliberately not covered: median_grouped (it interpolates inside a class
interval and takes an `interval` argument), Decimal and Fraction inputs, and
the TypeError CPython raises for median() over unorderable or non-numeric data.
"""
# fills: statistics.median, statistics.median_low, statistics.median_high
# reference: statistics


def median(data):
    """Return the middle value, averaging the two middles on even-length data."""
    values = sorted(data)
    n = len(values)
    if n == 0:
        raise ValueError("no median for empty data")
    if n % 2 == 1:
        return values[n // 2]
    i = n // 2
    return (values[i - 1] + values[i]) / 2


def median_low(data):
    """Return the low median: the smaller of the two middles on even data."""
    values = sorted(data)
    n = len(values)
    if n == 0:
        raise ValueError("no median for empty data")
    if n % 2 == 1:
        return values[n // 2]
    return values[n // 2 - 1]


def median_high(data):
    """Return the high median: the larger of the two middles on even data."""
    values = sorted(data)
    n = len(values)
    if n == 0:
        raise ValueError("no median for empty data")
    return values[n // 2]


def _median_leaves_input_alone(data):
    """Return (median, whether data still holds its original order)."""
    before = list(data)
    result = median(data)
    return (result, data == before)


def _error_of(fn, data):
    """Return the message of the ValueError fn(data) raises, or '' if none."""
    try:
        fn(data)
    except ValueError as exc:
        return str(exc)
    return ""


# --- cases ---
print(repr(median([1, 3, 5])))
print(repr(median([1, 3, 5, 7])))
print(repr(median([5, 3, 1])))
print(repr(median([2, 2])))
print(repr(median([1, 2, 3, 4])))
print(repr(median([7])))
print(repr(median([1.0, 2.0])))
print(repr(median([-11, 5.5, -3.4, 7.7, 2.3])))
print(repr(median([1, 2, 3.5, 4])))
print(repr(median([-3, -1])))
print(repr(median([0.1, 0.2, 0.3, 0.4])))
print(repr(median([1, 1, 1, 1, 1, 9])))

print(repr(median_low([1, 3, 5])))
print(repr(median_low([1, 3, 5, 7])))
print(repr(median_low([2, 2])))
print(repr(median_low([1.5, 2.5])))
print(repr(median_low(["d", "b", "a", "c"])))
print(repr(median_low(["b", "a", "c"])))
print(repr(median_low([True, False])))

print(repr(median_high([1, 3, 5])))
print(repr(median_high([1, 3, 5, 7])))
print(repr(median_high([2, 2])))
print(repr(median_high([1.5, 2.5])))
print(repr(median_high(["d", "b", "a", "c"])))
print(repr(median_high(["b", "a", "c"])))
print(repr(median_high([True, False])))

print(repr(_error_of(median, [])))
print(repr(_error_of(median_low, [])))
print(repr(_error_of(median_high, [])))

print(_median_leaves_input_alone([3, 1, 2]))
print(repr(median((4, 2, 6, 8))))
print(repr(median_low(range(10))), repr(median_high(range(10))))
