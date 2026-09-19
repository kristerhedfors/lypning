"""bisect.bisect_left / bisect_right / insort_left / insort_right.

A direct port of CPython's Lib/bisect.py, which is also what the C
accelerator ``_bisect`` computes: the midpoint is ``(lo + hi) // 2`` and
the comparison is always ``<``, never ``<=`` or ``>``, so the result
matches ``list.sort`` and ``heapq`` on any type that only defines
``__lt__``.  The two halves differ in exactly one place:

  * ``bisect_left`` tests ``a[mid] < x`` -- it lands BEFORE an equal run,
    so ``a[:i]`` is everything strictly less than `x`.
  * ``bisect_right`` tests ``x < a[mid]`` -- it lands AFTER an equal run,
    so ``a[:i]`` is everything less than or equal to `x`.

On a list with no element equal to `x` the two agree; the difference is
only ever visible on duplicates, which is why every duplicate case below
prints both.

``lo`` is validated (``ValueError('lo must be non-negative')``) and is NOT
clamped to ``len(a)``: ``bisect_right([1, 2, 3], 2, 5)`` is 5, because the
loop never runs.  ``hi`` defaults to ``len(a)``.

DIVERGENCE, deliberate, in the SIGNATURE only: CPython declares
``key`` keyword-only (``def bisect_left(a, x, lo=0, hi=None, *, key=None)``)
and a keyword-only parameter is a refusal on both engines, so `key` is an
ordinary fifth parameter here.  Every call CPython accepts, these accept,
with the same answer; these additionally accept `key` positionally, which
CPython rejects.  No case below passes `key` positionally.

WHICH CPython that `key` comes from: 3.10 and later.  `key` was added to
all four functions in CPython 3.10 (gh-bpo-4356); on 3.9 there is no such
parameter at all and ``bisect.bisect_left([0, -1, 2], 2, 0, None,
key=abs)`` is ``TypeError: bisect_left() takes at most 4 arguments (5
given)``.  Measured 2026-09-17 on CPython 3.9.23, 3.10.18, 3.11.15 and
3.14.0rc2.  The `key` cases below therefore have no counterpart to check
against on 3.9 -- they are not WRONG there, the surface is absent -- and
running this unit's cases against the real `bisect` on 3.9 stops at the
first of them.  Everything above the `key` block is the 3.9 surface and
answers identically on every release.

Not covered, and not exercised: ``hi`` outside ``0 <= hi <= len(a)``.  The
documented contract does not allow it, and CPython's C accelerator answers
differently from its own pure-Python source there -- ``hi=-1`` is an
argument-clinic sentinel meaning ``len(a)`` in the C version and an empty
range in the Python one.  A unit must not pin a quirk its reference
implementation disagrees with itself about.
"""
# fills: bisect.bisect_left, bisect.bisect_right, bisect.insort_left, bisect.insort_right, bisect.bisect, bisect.insort
# reference: bisect


def bisect_left(a, x, lo=0, hi=None, key=None):
    """Index where `x` goes in sorted `a`, before any equal element."""
    if lo < 0:
        raise ValueError("lo must be non-negative")
    if hi is None:
        hi = len(a)
    if key is None:
        while lo < hi:
            mid = (lo + hi) // 2
            if a[mid] < x:
                lo = mid + 1
            else:
                hi = mid
    else:
        while lo < hi:
            mid = (lo + hi) // 2
            if key(a[mid]) < x:
                lo = mid + 1
            else:
                hi = mid
    return lo


def bisect_right(a, x, lo=0, hi=None, key=None):
    """Index where `x` goes in sorted `a`, after any equal element."""
    if lo < 0:
        raise ValueError("lo must be non-negative")
    if hi is None:
        hi = len(a)
    if key is None:
        while lo < hi:
            mid = (lo + hi) // 2
            if x < a[mid]:
                hi = mid
            else:
                lo = mid + 1
    else:
        while lo < hi:
            mid = (lo + hi) // 2
            if x < key(a[mid]):
                hi = mid
            else:
                lo = mid + 1
    return lo


def insort_left(a, x, lo=0, hi=None, key=None):
    """Insert `x` into sorted `a`, before any element equal to it."""
    if key is None:
        at = bisect_left(a, x, lo, hi)
    else:
        at = bisect_left(a, key(x), lo, hi, key)
    a.insert(at, x)


def insort_right(a, x, lo=0, hi=None, key=None):
    """Insert `x` into sorted `a`, after any element equal to it."""
    if key is None:
        at = bisect_right(a, x, lo, hi)
    else:
        at = bisect_right(a, key(x), lo, hi, key)
    a.insert(at, x)


# CPython's own aliases, at the bottom of Lib/bisect.py.
bisect = bisect_right
insort = insort_right

def _insorted_left(seq, x, lo=0, hi=None, key=None):
    """Copy `seq`, insort_left `x` into the copy, return the copy.

    The cases print values, so the mutation needs a return; the copy also
    keeps each case independent of the one before it.
    """
    out = seq[:]
    insort_left(out, x, lo, hi, key=key)
    return out


def _insorted_right(seq, x, lo=0, hi=None, key=None):
    """Copy `seq`, insort_right `x` into the copy, return the copy."""
    out = seq[:]
    insort_right(out, x, lo, hi, key=key)
    return out


def _insort_returns(seq, x):
    """The RETURN VALUE of insort_right, which is always None."""
    return insort_right(seq[:], x)


def _doubles(n):
    """[0, 2, 4, ...] with `n` elements -- a big sorted list for the cases."""
    return [i * 2 for i in range(n)]


def _first_word(pair):
    return pair[0]


# --- cases ---
# No element equals x: the two agree.
print(bisect_left([1, 3, 5, 7, 9], 0), bisect_right([1, 3, 5, 7, 9], 0))
print(bisect_left([1, 3, 5, 7, 9], 4), bisect_right([1, 3, 5, 7, 9], 4))
print(bisect_left([1, 3, 5, 7, 9], 10), bisect_right([1, 3, 5, 7, 9], 10))

# An element equals x: left lands before it, right after it.
print(bisect_left([1, 3, 5, 7, 9], 1), bisect_right([1, 3, 5, 7, 9], 1))
print(bisect_left([1, 3, 5, 7, 9], 5), bisect_right([1, 3, 5, 7, 9], 5))
print(bisect_left([1, 3, 5, 7, 9], 9), bisect_right([1, 3, 5, 7, 9], 9))

# Runs of duplicates -- the only place the two ever disagree.
print(bisect_left([2, 2, 2, 2], 2), bisect_right([2, 2, 2, 2], 2))
print(bisect_left([2, 2, 2, 2], 1), bisect_right([2, 2, 2, 2], 1))
print(bisect_left([2, 2, 2, 2], 3), bisect_right([2, 2, 2, 2], 3))
print(bisect_left([1, 2, 2, 2, 3], 2), bisect_right([1, 2, 2, 2, 3], 2))

# Empty and single-element lists.
print(bisect_left([], 1), bisect_right([], 1))
print(bisect_left([5], 4), bisect_right([5], 4))
print(bisect_left([5], 5), bisect_right([5], 5))
print(bisect_left([5], 6), bisect_right([5], 6))

# Every insertion point of a five-element list, both flavours.
print([bisect_left([1, 3, 5, 7, 9], v) for v in range(0, 11)])
print([bisect_right([1, 3, 5, 7, 9], v) for v in range(0, 11)])

# lo and hi restrict the search window; the answer stays an index into a.
print(bisect_left([1, 3, 5, 7, 9], 5, 0, 2), bisect_right([1, 3, 5, 7, 9], 5, 0, 2))
print(bisect_left([1, 3, 5, 7, 9], 5, 3), bisect_right([1, 3, 5, 7, 9], 5, 3))
print(bisect_left([1, 3, 5, 7, 9], 5, 2, 3), bisect_right([1, 3, 5, 7, 9], 5, 2, 3))
print(bisect_left([1, 3, 5, 7, 9], 5, 2, 2), bisect_right([1, 3, 5, 7, 9], 5, 2, 2))
print(bisect_left([1, 3, 5, 7, 9], 5, 0, 0), bisect_right([1, 3, 5, 7, 9], 5, 0, 0))
print(bisect_left([1, 3, 5, 7, 9], 5, 0, 5), bisect_right([1, 3, 5, 7, 9], 5, 0, 5))

# lo beyond the end is not clamped: the loop never runs, lo comes back out.
print(bisect_left([1, 3, 5, 7, 9], 5, 7), bisect_right([1, 3, 5, 7, 9], 5, 7))

# A negative lo is the one documented error.
try:
    bisect_left([1, 3, 5], 5, -1)
except ValueError as exc:
    print("bisect_left:", exc)
try:
    bisect_right([1, 3, 5], 5, -1)
except ValueError as exc:
    print("bisect_right:", exc)
try:
    _insorted_left([1, 2], 1, -1)
except ValueError as exc:
    print("insort_left:", exc)
try:
    _insorted_right([1, 2], 1, -1)
except ValueError as exc:
    print("insort_right:", exc)

# Strings, floats and mixed int/float compare the same way.
print(bisect_left(["ant", "bee", "cow", "cow", "dog"], "cow"))
print(bisect_right(["ant", "bee", "cow", "cow", "dog"], "cow"))
print(bisect_left(["ant", "bee", "cow", "cow", "dog"], "cat"))
print(bisect_right(["ant", "bee", "cow", "cow", "dog"], "zebra"))
print(bisect_left(["ant", "bee", "cow", "cow", "dog"], ""))
print(bisect_right(["ant", "bee", "cow", "cow", "dog"], "ant"))
print(bisect_left([1.0, 2.5, 2.5, 4.75], 2.5), bisect_right([1.0, 2.5, 2.5, 4.75], 2.5))
print(bisect_left([1.0, 2.5, 2.5, 4.75], 2), bisect_right([1.0, 2.5, 2.5, 4.75], 3))
print(bisect_left([1, 2, 3], 2.0), bisect_right([1, 2, 3], 2.0))

# insort_* mutate in place and return None.
print(_insort_returns([1, 3, 5], 4))
print(_insorted_left([1, 3, 5], 4))
print(_insorted_right([1, 3, 5], 0))
print(_insorted_right([1, 3, 5], 99))
print(_insorted_left([1, 2, 2, 3], 2))
print(_insorted_right([1, 2, 2, 3], 2))
print(_insorted_left([], "only"))
print(_insorted_right([], 7))

# insort with lo/hi: the window bounds the SEARCH, not the insertion, so an
# unsorted prefix survives and the result need not come out sorted.
print(_insorted_right([9, 1, 3, 5], 4, 1))
print(_insorted_left([1, 3, 5, 9], 7, 0, 2))
print(_insorted_right([1, 2, 3], 9, 5))

# key= applies to the list elements; insort applies it to x as well.
print(bisect_left([("ant", 3), ("bee", 1), ("cow", 4)], "cow", 0, None, key=_first_word))
print(bisect_right([("ant", 3), ("bee", 1), ("cow", 4)], "cow", 0, None, key=_first_word))
print(bisect_left([("ant", 3), ("bee", 1), ("cow", 4)], "cat", 0, None, key=_first_word))
print(bisect_left([("ant", 3), ("bee", 1), ("cow", 4)], "zzz", 0, None, key=_first_word))
print(_insorted_right([("ant", 3), ("cow", 4)], ("bee", 9), 0, None, key=_first_word))
print(_insorted_left([("ant", 3), ("cow", 4)], ("cow", 0), 0, None, key=_first_word))
print(_insorted_right([("ant", 3), ("cow", 4)], ("cow", 0), 0, None, key=_first_word))

# key= over a non-identity projection: a list sorted by abs(), not by value.
print(bisect_left([0, -1, 2, -3, 4], 2, 0, None, key=abs))
print(bisect_right([0, -1, 2, -3, 4], 2, 0, None, key=abs))
print(bisect_left([0, -1, 2, -3, 4], 5, 0, None, key=abs))
print(_insorted_left([0, -1, 2, -3], -2, 0, None, key=abs))
print(_insorted_right([0, -1, 2, -3], -2, 0, None, key=abs))

# The aliases are bisect_right and insort_right.
print(bisect([1, 3, 5, 7, 9], 5), bisect_right([1, 3, 5, 7, 9], 5))
print(bisect([2, 2, 2], 2), insort is insort_right)

# A search on a ten-thousand-element list stays exact at both ends.
print(bisect_left(_doubles(10000), 0), bisect_right(_doubles(10000), 0))
print(bisect_left(_doubles(10000), 19998), bisect_right(_doubles(10000), 19998))
print(bisect_left(_doubles(10000), 9999), bisect_right(_doubles(10000), 9999))
print(bisect_left(_doubles(10000), -1), bisect_right(_doubles(10000), 20000))
