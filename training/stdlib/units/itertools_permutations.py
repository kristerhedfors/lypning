"""itertools.permutations as a materialised list, without importing itertools.

The engines refuse `import itertools` (module: import itertools) and they
refuse `yield`, so this cannot be the lazy iterator CPython ships.  It is a
**materialising replacement**: the whole `list` of result tuples is built
before anything is returned.

That is a real semantic difference a caller must understand:

  * Correct only over FINITE inputs, and in practice over SMALL ones.  CPython
    materialises the pool up front too, so an endless iterable hangs there as
    well -- but the *result* is lazy there and is not here.
    `permutations(range(20))` is 2.4 quintillion tuples: the real one starts
    instantly, this one never returns.  A caller who only wanted the first few
    permutations of a large pool must not use this.
  * Memory is the whole result, not one tuple at a time.
  * You get a `list`, so `len()`, indexing and repeated passes work, where the
    real iterator is spent after one pass.
  * The input iterable is consumed exactly once, up front, as CPython's is.

ORDER is the specification.  Tuples come out in the lexicographic order of the
INPUT POSITIONS, not of the values, so the output is sorted only when the
input is sorted: `permutations('BA')` is `[('B', 'A'), ('A', 'B')]`.  Elements
are treated as unique by position, never by value, so `permutations([1, 1, 2])`
has six rows with repeats among them, not three distinct ones.

The implementation is the index/cycle odometer from CPython's own
documentation -- an iterative loop with no recursion anywhere.  A
recursive-descent version recurses once per pool element (or once per chosen
position, depending on the shape) and refuses with `recursion: call depth
beyond 180` on a pool of more than ~180 elements.  The cases pass a
200-element pool, where a position-recursive version refuses while the output
stays at 200 rows.

An honest limit on that test: a version whose depth follows `r` rather than
the pool size cannot be caught by any case at all, because the output count
P(n, r) is at least 180! once r exceeds 180 and n >= r, and is `[]` when
r > n.  There is no call with r > 180 and a printable result.  The code above
the separator is simply free of recursive calls; read it, do not infer it from
a case.

Edge cases, all checked against the live module on 2026-09-16:

  * `r=None` (the default) means `r = len(pool)`, the full-length
    permutations.
  * `r == 0` gives `[()]` -- one result, the empty tuple -- for any pool,
    including the empty pool.  `permutations('', None)` is also `[()]`.
  * `r > len(pool)` gives `[]`, NOT an error.
  * A negative `r` raises ValueError("r must be non-negative"), the message
    and the type CPython uses.

Not covered: `itertools.combinations` and `itertools.product`, which have
their own units in this corpus.
"""
# fills: itertools.permutations
# reference: itertools


def _pick(pool, indices):
    """Return the tuple of pool elements named by `indices`, in order."""
    out = []
    for i in indices:
        out.append(pool[i])
    return tuple(out)


def permutations(iterable, r=None):
    """Return all r-length orderings of `iterable`, as a list of tuples.

    Equivalent to `list(itertools.permutations(iterable, r))`.
    """
    pool = tuple(iterable)
    n = len(pool)
    if r is None:
        r = n
    if r < 0:
        raise ValueError("r must be non-negative")
    if r > n:
        return []

    # indices holds a permutation of the pool positions; cycles[i] counts down
    # how many choices are left for position i.  This is the odometer from
    # CPython's itertools documentation, iterative by construction.
    indices = list(range(n))
    cycles = list(range(n, n - r, -1))
    rows = [_pick(pool, indices[:r])]
    while n:
        i = r - 1
        while i >= 0:
            cycles[i] = cycles[i] - 1
            if cycles[i] == 0:
                # Position i is exhausted: rotate its element to the end and
                # recharge the counter, then carry to position i - 1.
                indices[i:] = indices[i + 1:] + indices[i:i + 1]
                cycles[i] = n - i
                i = i - 1
            else:
                j = cycles[i]
                indices[i], indices[-j] = indices[-j], indices[i]
                rows.append(_pick(pool, indices[:r]))
                break
        if i < 0:
            # The carry ran off the left end: every ordering is out.
            return rows
    return rows


def _message_of(iterable, r):
    """Return the message of the ValueError permutations(iterable, r) raises."""
    try:
        permutations(iterable, r)
    except ValueError as exc:
        return str(exc)
    return ""


# --- cases ---
# The ordinary shape: r defaults to the whole pool.
print(permutations("ABC"))
print(permutations("ABC", None))
print(permutations("ABC", 3))
print(permutations("ABC", 2))
print(permutations("ABCD", 2))
print(permutations(range(3)))
print(permutations([10, 20], 1))

# Order follows INPUT POSITION, not value.
print(permutations("BA"))
print(permutations([3, 1, 2]))
print(permutations("CBA", 2))

# Elements are unique by position, never by value: repeats give repeat rows.
print(permutations([1, 1, 2]))
print(permutations("AA"))
print(permutations([0, 0, 0], 2))

# r == 0 is exactly one result, the empty tuple.
print(permutations("ABC", 0))
print(permutations("", 0))
print(permutations("", None))
print(permutations(""))
print(permutations([], 0))
print(len(permutations("ABCDE", 0)))

# r > len(pool) is empty, not an error.
print(permutations("AB", 3))
print(permutations("AB", 5))
print(permutations("", 1))
print(permutations([7], 2))
print(permutations([], 3))

# A single-element pool.
print(permutations("A"))
print(permutations([9], 1))
print(permutations([9], 0))

# Mixed element types survive; repr keeps 1, True and '1' apart.
print(repr(permutations([1, "1"])))
print(repr(permutations([True, 0], 2)))
print(permutations([None, (1, 2)]))

# The pool is read once, up front.
pool = [1, 2]
rows = permutations(pool)
pool.append(3)
print(rows)

# A generator expression is a fine pool.
print(permutations((c for c in "abc"), 2))
print(permutations((n for n in range(3))))

# Counts line up with n! / (n - r)!.
print(len(permutations(range(5))), len(permutations(range(5), 2)))
print(len(permutations(range(6), 3)), len(permutations(range(6), 6)))
eight = permutations(range(8))
print(len(eight))
print(eight[0], eight[40319])

# The full-length result is the sorted pool's lexicographic ordering when the
# pool itself is sorted -- and only then.
print(permutations(sorted("CAB")) == sorted(permutations("CAB")))
print(permutations("CAB") == sorted(permutations("CAB")))

# Errors.
print(repr(_message_of("ABC", -1)))
print(repr(_message_of("", -1)))
print(repr(_message_of("ABC", 2)))

# Depth: a 200-element pool.  A version that recurses once per pool element
# refuses beyond 180 frames; the odometer above does not.  r is kept small so
# the result stays printable -- see the docstring on why r itself cannot be
# pushed past 180.
deep = permutations(range(200), 1)
print(len(deep), len(deep[0]))
print(deep[0], deep[1], deep[199])

pairs = permutations(range(200), 2)
print(len(pairs))
print(pairs[0], pairs[1], pairs[199], pairs[39799])

empty_r = permutations(range(200), 0)
print(empty_r, len(empty_r))
print(permutations(range(190), 1)[189])
