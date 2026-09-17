"""itertools.product as a materialised list, without importing itertools.

The engines refuse `import itertools` (module: import itertools) and they
refuse `yield`, so this cannot be the lazy iterator CPython ships.  It is a
**materialising replacement**: `product(...)` builds and returns the whole
`list` of result tuples before it returns anything at all.

That is a real semantic difference a caller must understand:

  * It is correct only over FINITE inputs.  `itertools.product` over a pool
    that never ends is fine as long as you stop consuming it; this one never
    returns.  (CPython's own product is finite-only too -- it materialises
    every pool up front -- but the *result* is lazy there and is not here.)
  * Memory is the whole cross product, not one tuple.  The product of ten
    ten-element pools is 10**10 tuples here and one tuple at a time there.
  * You get a `list`, so `len()`, indexing and a second pass all work, where
    the real iterator is exhausted after one pass.  Code that relies on
    exhaustion will behave differently.
  * Each input iterable is consumed exactly once, up front, exactly as
    CPython's product does, so passing the same iterator twice gives the
    same answer both here and there.

ORDER is the specification and it is not obvious: the RIGHTMOST pool advances
fastest, like an odometer, so `product('ab', [1, 2])` is
`[('a', 1), ('a', 2), ('b', 1), ('b', 2)]`.  This unit is an explicit odometer
over one index per pool -- an iterative loop with no recursion anywhere --
because a recursive-descent version recurses once per pool and refuses
(recursion: call depth beyond 180) on any call with more than ~180 pools,
which is exactly the call that looks safest: 200 one-element pools produce a
single row.  The cases include that call.

Edge cases, all checked against the live module on 2026-09-16:

  * `product()` -- no pools at all -- is `[()]`, not `[]`.  The empty product
    has one element, the empty tuple.
  * Any pool that is empty makes the whole product empty: `product([1], [])`
    is `[]`.
  * `repeat=0` discards the pools entirely and gives `[()]`, even when a pool
    is empty: `product([], repeat=0)` is `[()]`.
  * `repeat=n` concatenates n copies of the SAME materialised pools, so a
    one-shot iterator argument is still only consumed once.
  * A negative `repeat` raises ValueError("repeat argument cannot be
    negative"), the same message CPython uses.

DIVERGENCE (one, deliberate): an unexpected keyword argument raises
`ValueError` here, carrying CPython's exact message
(`'bogus' is an invalid keyword argument for product()`), where CPython raises
`TypeError`.  The subset has no class statement and most builtin exception
names are not bound, so ValueError with a non-empty message is the only shape
available.  The cases print the exception's TYPE as well as its message, so
this shows up as exactly one differing line -- `product([1], bogus=1)` --
against the real module, and every other line matches byte for byte.  The
message alone would hide the one thing that differs, and catching only
`ValueError` would let CPython's `TypeError` escape and leave every case
below it compared against nothing.

Not covered: nothing else in itertools -- see the sibling units.
"""
# fills: itertools.product
# reference: itertools


def _repeat_of(kwargs, funcname):
    """Read and validate the only keyword `product` accepts."""
    for key in kwargs:
        if key != "repeat":
            raise ValueError(
                repr(key) + " is an invalid keyword argument for " + funcname + "()"
            )
    repeat = 1
    if "repeat" in kwargs:
        repeat = kwargs["repeat"]
    if repeat < 0:
        raise ValueError("repeat argument cannot be negative")
    return repeat


def product(*iterables, **kwargs):
    """Return the cartesian product of the pools, as a list of tuples.

    Equivalent to `list(itertools.product(*iterables, repeat=...))`.
    """
    repeat = _repeat_of(kwargs, "product")

    # Materialise every pool exactly once, then repeat the materialised
    # copies -- this is what CPython does, so a one-shot iterator passed with
    # repeat=3 is still read only once.
    once = []
    for iterable in iterables:
        once.append(tuple(iterable))

    pools = []
    round_no = 0
    while round_no < repeat:
        for pool in once:
            pools.append(pool)
        round_no = round_no + 1

    width = len(pools)
    for pool in pools:
        if not pool:
            # One empty pool empties the whole product.  With repeat=0 the
            # pools were dropped above, so this loop never sees it.
            return []

    # The odometer: digits[j] indexes pools[j]; the last digit rolls fastest.
    digits = [0] * width
    rows = []
    while True:
        row = []
        j = 0
        while j < width:
            row.append(pools[j][digits[j]])
            j = j + 1
        rows.append(tuple(row))

        j = width - 1
        while j >= 0:
            digits[j] = digits[j] + 1
            if digits[j] < len(pools[j]):
                break
            digits[j] = 0
            j = j - 1
        if j < 0:
            # Every digit rolled over: the odometer is back at zero.
            return rows


def _error_of(args, kwargs):
    """Return "<type>: <message>" for whatever product(*args, **kwargs) raises.

    The tuple in the `except` is load-bearing.  CPython answers a negative
    `repeat` with ValueError and an unexpected keyword with TypeError, so a
    helper that caught only ValueError would let that TypeError escape -- and
    every case printed below it would then be compared against nothing at all.
    The type is reported rather than swallowed, which is what makes the
    divergence above one visible line instead of a silence.
    """
    try:
        product(*args, **kwargs)
    except (ValueError, TypeError) as exc:
        return type(exc).__name__ + ": " + str(exc)
    return ""


# --- cases ---
# The ordinary shape: the rightmost pool advances fastest.
print(product("ab", [1, 2]))
print(product([1, 2], "ab"))
print(product("ab", "cd", "ef"))
print(product([0, 1], [2, 3], [4, 5]))
print(product(range(3), range(2)))

# A single pool wraps each element in a 1-tuple.
print(product("abc"))
print(product([7]))

# No pools at all: the empty product has exactly one element.
print(product())
print(len(product()))

# An empty pool anywhere empties the whole product.
print(product([]))
print(product([1, 2], []))
print(product([], [1, 2]))
print(product([1], [], [2]))
print(product("", "ab"))

# repeat= concatenates copies of the same pools.
print(product([0, 1], repeat=2))
print(product("ab", repeat=3))
print(product([0, 1], [2, 3], repeat=2))
print(len(product([0, 1], [2, 3], repeat=2)))

# repeat=0 drops the pools entirely -- even the empty ones.
print(product([0, 1], repeat=0))
print(product(repeat=0))
print(product([], repeat=0))
print(product([1, 2], [], repeat=0))

# repeat with no pools is still the empty product.
print(product(repeat=3))
print(product(repeat=1))

# An empty pool survives repetition.
print(product([1, 2], [], repeat=2))

# Mixed element types come through unchanged; repr makes 1 and '1' distinct.
print(product([1, "1"], [True, None]))
print(repr(product(["x"], [0])))
print(product([(1, 2)], ["a", "b"]))

# The input is read once, up front, so a list mutated afterwards is unaffected.
pool = [1, 2]
rows = product(pool, "a")
pool.append(3)
print(rows)

# A generator expression is a fine pool: it is consumed exactly once.
print(product((c for c in "ab"), (n for n in [0, 1])))

# repeat over a one-shot iterator still reads it once.
print(product((n for n in [0, 1]), repeat=2))

# Errors.  The type is printed with the message -- see DIVERGENCE above.
print(repr(_error_of(([1],), {"repeat": -1})))
print(repr(_error_of((), {"repeat": -5})))
print(repr(_error_of(([1],), {"bogus": 1})))
print(repr(_error_of(([1],), {"repeat": 1})))

# Depth: 200 pools.  A recursive-descent product recurses once per pool and
# refuses beyond 180 frames; the odometer above does not.  Output stays tiny
# because 198 of the pools hold a single element.
deep = [(7,)] * 198
deep.append((0, 1))
deep.append((2, 3))
wide = product(*deep)
print(len(wide))
print(len(wide[0]))
print(wide[0][0], wide[0][198], wide[0][199])
print(wide[3][198], wide[3][199])
print(wide[1][199], wide[2][198])

# The same depth reached through repeat= rather than through argument count.
tall = product((5,), repeat=200)
print(len(tall), len(tall[0]), tall[0][0], tall[0][199])

# A 200-deep product whose last pool is empty is still empty, without ever
# building a row.
print(product(*([(7,)] * 199 + [()])))
