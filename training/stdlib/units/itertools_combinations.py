"""itertools.combinations and combinations_with_replacement, as lists.

The engines refuse `import itertools` (module: import itertools) and they
refuse `yield`, so these cannot be the lazy iterators CPython ships.  They are
**materialising replacements**: the whole `list` of result tuples is built
before anything is returned.

That is a real semantic difference a caller must understand:

  * Correct only over FINITE inputs.  CPython's combinations materialises its
    pool up front too, so an endless iterable hangs there as well -- but the
    *result* is lazy there and is not here.  `combinations(range(50), 25)` is
    126 trillion tuples: the real one starts instantly and this one never
    finishes.
  * Memory is the whole result, not one tuple at a time.
  * You get a `list`, so `len()`, indexing and repeated passes work, where the
    real iterator is spent after one pass.
  * Each input iterable is consumed exactly once, up front, as CPython's is.

ORDER is the specification and it is not obvious.  Both functions emit tuples
in the lexicographic order of the INPUT POSITIONS, not of the values -- so the
output is sorted only when the input is sorted.  `combinations('DCBA', 2)`
gives `('D', 'C')` first.  Elements are treated as unique by position, never
by value, so a repeated element yields repeated tuples: `combinations('AA', 2)`
is `[('A', 'A')]` and `combinations([1, 1, 2], 2)` has two `(1, 2)` rows.

Both walk an explicit index vector with an iterative odometer -- no recursion
anywhere.  A recursive-descent version recurses once per chosen element and
refuses (recursion: call depth beyond 180) at r > ~180, which is exactly the
call whose output is smallest: `combinations(range(200), 199)` is 200 rows.
The cases include it.

Edge cases, all checked against the live module on 2026-09-16:

  * `r == 0` gives `[()]` for both -- one result, the empty tuple -- for any
    pool, including the empty pool.
  * `combinations(pool, r)` with `r > len(pool)` is `[]`.
  * `combinations_with_replacement(pool, r)` with `r > len(pool)` is NOT empty
    -- repetition is allowed -- but it IS empty when the pool is empty and
    `r > 0`.
  * A negative `r` raises ValueError("r must be non-negative") in both, which
    is the message and the type CPython uses.

Not covered: `itertools.permutations` and `itertools.product`, which have
their own units in this corpus.
"""
# fills: itertools.combinations, itertools.combinations_with_replacement
# reference: itertools


def _pick(pool, indices):
    """Return the tuple of pool elements named by `indices`, in order."""
    out = []
    for i in indices:
        out.append(pool[i])
    return tuple(out)


def combinations(iterable, r):
    """Return all r-length subsequences of `iterable`, as a list of tuples.

    Equivalent to `list(itertools.combinations(iterable, r))`.
    """
    if r < 0:
        raise ValueError("r must be non-negative")
    pool = tuple(iterable)
    n = len(pool)
    if r > n:
        return []

    # indices is strictly increasing; indices[i] is capped at i + n - r.
    indices = list(range(r))
    rows = [_pick(pool, indices)]
    while True:
        i = r - 1
        while i >= 0:
            if indices[i] != i + n - r:
                break
            i = i - 1
        if i < 0:
            # Every position is at its ceiling: the last subsequence is out.
            return rows
        indices[i] = indices[i] + 1
        j = i + 1
        while j < r:
            indices[j] = indices[j - 1] + 1
            j = j + 1
        rows.append(_pick(pool, indices))


def combinations_with_replacement(iterable, r):
    """Return all r-length subsequences allowing repeated elements.

    Equivalent to `list(itertools.combinations_with_replacement(iterable, r))`.
    """
    if r < 0:
        raise ValueError("r must be non-negative")
    pool = tuple(iterable)
    n = len(pool)
    if n == 0 and r > 0:
        # Nothing to repeat.  r == 0 still gives [()], handled below.
        return []

    # indices is non-decreasing; every position is capped at n - 1.
    indices = [0] * r
    rows = [_pick(pool, indices)]
    while True:
        i = r - 1
        while i >= 0:
            if indices[i] != n - 1:
                break
            i = i - 1
        if i < 0:
            return rows
        # Everything from i rightwards restarts at the newly bumped value.
        indices[i:] = [indices[i] + 1] * (r - i)
        rows.append(_pick(pool, indices))


def _message_of(fn, iterable, r):
    """Return the message of the ValueError fn(iterable, r) raises."""
    try:
        fn(iterable, r)
    except ValueError as exc:
        return str(exc)
    return ""


# --- cases ---
# The ordinary shape.
print(combinations("ABCD", 2))
print(combinations("ABCD", 3))
print(combinations(range(4), 2))
print(combinations([10, 20, 30], 1))
print(combinations("ABCD", 4))

# Order follows INPUT POSITION, not value: an unsorted pool gives unsorted rows.
print(combinations("DCBA", 2))
print(combinations([3, 1, 2], 2))

# Elements are unique by position, never by value.
print(combinations("AA", 2))
print(combinations([1, 1, 2], 2))
print(combinations([1, 1, 1], 2))

# r == 0 is one result, the empty tuple; r > n is none at all.
print(combinations("ABC", 0))
print(combinations("", 0))
print(combinations([], 0))
print(combinations("AB", 3))
print(combinations("", 1))
print(combinations([], 5))
print(len(combinations("ABC", 0)))

# combinations_with_replacement: the ordinary shape.
print(combinations_with_replacement("ABC", 2))
print(combinations_with_replacement("AB", 3))
print(combinations_with_replacement(range(3), 3))
print(combinations_with_replacement("A", 4))

# r > n is fine here -- repetition is allowed -- but an empty pool is not.
print(combinations_with_replacement("AB", 4))
print(combinations_with_replacement("", 0))
print(combinations_with_replacement("", 2))
print(combinations_with_replacement([], 1))
print(combinations_with_replacement("ABC", 0))
print(combinations_with_replacement([5, 5], 2))

# Mixed element types survive; repr keeps 1 and '1' apart.
print(repr(combinations([1, "1", True], 2)))
print(repr(combinations_with_replacement([0, "0"], 2)))
print(combinations([(1, 2), (3, 4), (5, 6)], 2))
print(combinations([None, 1, "x"], 2))

# The pool is read once, up front.
pool = [1, 2, 3]
rows = combinations(pool, 2)
pool.append(4)
print(rows)

# A generator expression is a fine pool.
print(combinations((c for c in "abc"), 2))
print(combinations_with_replacement((n for n in range(3)), 2))

# Counts line up with the binomial coefficients.
print(len(combinations(range(10), 5)), len(combinations(range(10), 0)))
print(len(combinations_with_replacement(range(5), 3)))
print(len(combinations(range(6), 3)), len(combinations(range(6), 4)))

# Errors.
print(repr(_message_of(combinations, "ABC", -1)))
print(repr(_message_of(combinations_with_replacement, "ABC", -1)))
print(repr(_message_of(combinations, "", -3)))
print(repr(_message_of(combinations, "ABC", 2)))

# Depth: r = 199 over a 200-element pool.  A recursive-descent combinations
# recurses once per chosen position and refuses beyond 180 frames; the index
# odometer above does not.  The output is only 200 rows.
deep = combinations(range(200), 199)
print(len(deep))
print(len(deep[0]))
print(deep[0][0], deep[0][198])
print(deep[1][0], deep[1][1], deep[1][198])
print(deep[199][0], deep[199][1], deep[199][198])

# The same depth for combinations_with_replacement: 200 positions, 2 values.
wide = combinations_with_replacement([0, 1], 200)
print(len(wide), len(wide[0]))
print(wide[0][0], wide[0][199])
print(wide[1][0], wide[1][198], wide[1][199])
print(wide[200][0], wide[200][199])
print(sum(wide[7]))
