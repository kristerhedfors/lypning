"""statistics.mode without importing statistics or collections.Counter.

The engines refuse `import statistics` (module: import statistics), and the core
engine also refuses `import collections`, so the tally is a plain dict here.
That is enough, because CPython's mode() is
`Counter(iter(data)).most_common(1)[0][0]` and for a single most-common element
Counter falls back to `max(self.items(), key=itemgetter(1))` — max() returns the
FIRST maximal item in iteration order, and a Counter iterates in first-seen
order. So since Python 3.8 mode() does not raise on a tie: it returns the value
whose first occurrence came earliest among those tied for the highest count.
mode(['red', 'red', 'green', 'blue', 'blue']) is 'red', and mode([1, 2, 3]) is
1. This unit reproduces that rule exactly, including the two places it is easy
to get wrong:

  - the key kept is the first one seen, not the last. dict (like Counter) keeps
    the original key when an equal key is stored again, so mode([1, 1.0, 1.0])
    is the int 1 while mode([1.0, 1, 1]) is the float 1.0, and a leading True
    survives as True rather than 1. The cases print repr() so the difference is
    visible.
  - equal-but-distinct values share a slot: 1, 1.0 and True are one entry with
    a combined count, because that is what hashing by value does.

Data must be hashable, as it must be for Counter. NaN is deliberately absent
from the cases: NaN is never equal to itself, so it never accumulates a count,
and identity-based NaN behaviour is not something a reimplementation should
claim. Empty data raises StatisticsError("no mode for empty data") in CPython;
StatisticsError subclasses ValueError and the subset has no class statement, so
this raises ValueError with the same message. multimode() is not covered.
"""
# fills: statistics.mode
# reference: statistics


def _tally(data):
    """Return {value: count}, keys in first-seen order, like Counter(data)."""
    counts = {}
    for value in data:
        if value in counts:
            counts[value] = counts[value] + 1
        else:
            counts[value] = 1
    return counts


def mode(data):
    """Return the most common value, the earliest-seen one when counts tie."""
    counts = _tally(data)
    best = None
    best_count = 0
    seen = False
    for value in counts:
        count = counts[value]
        if not seen or count > best_count:
            best = value
            best_count = count
            seen = True
    if not seen:
        raise ValueError("no mode for empty data")
    return best


def _error_of(fn, data):
    """Return the message of the ValueError fn(data) raises, or '' if none."""
    try:
        fn(data)
    except ValueError as exc:
        return str(exc)
    return ""


# --- cases ---
print(repr(mode([1, 1, 2, 3, 3, 3, 3, 4])))
print(repr(mode(["red", "blue", "blue", "red", "green", "red", "red"])))
print(repr(mode(["red", "red", "green", "blue", "blue"])))
print(repr(mode([1, 2, 3])))
print(repr(mode([3, 2, 1])))
print(repr(mode([7])))
print(repr(mode("aabbbc")))
print(repr(mode("abcabc")))
print(repr(mode([1, 1.0, 1.0, 2])))
print(repr(mode([1.0, 1, 2])))
print(repr(mode([True, 1, 1, 0])))
print(repr(mode([1, True, True, 0])))
print(repr(mode([0, False, 2])))
print(repr(mode([(1, 2), (1, 2), (3, 4)])))
print(repr(mode([(1, 2), (3, 4)])))
print(repr(mode([None, None, 5])))
print(repr(mode([None, 5, 5])))
print(repr(mode(["b", "b", "a", "a"])))
print(repr(mode(["a", "a", "b", "b"])))
print(repr(mode((2.5, 2.5, -2.5))))
print(repr(mode([-0.0, 0.0, 1.0])))
print(repr(mode([x % 3 for x in range(10)])))
print(repr(mode(range(4))))
print(repr(_error_of(mode, [])))
print(repr(_error_of(mode, "")))
print(_tally("aabbbc"))
print(_tally([1, 1.0, True, 2]))
