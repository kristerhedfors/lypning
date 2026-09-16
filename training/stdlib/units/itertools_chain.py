"""itertools.chain, chain.from_iterable, islice and zip_longest, as lists.

The engines refuse `import itertools` (module: import itertools) and they
refuse `yield`, so none of these can be the lazy iterators CPython ships.
They are **materialising replacements**: each builds and returns the whole
`list` before it returns anything at all.

That is a real semantic difference a caller must understand:

  * Correct only over FINITE inputs.  `chain`, `chain_from_iterable` and
    `zip_longest` here read every input to exhaustion, so an endless iterable
    that the real lazy version handles happily will hang.  `islice` is the one
    exception: with an integer `stop` it stops pulling, so it stays usable on
    an endless input; with `stop=None` it does not.
  * Memory is the whole result, not one item at a time.
  * You get a `list`, so `len()`, indexing and repeated passes work, where the
    real iterators are spent after one pass.
  * How far a shared input ITERATOR is advanced is not guaranteed to match
    CPython's.  `list(islice(it, 3, 1))` is `[]` both here and there, but
    CPython leaves `it` three items in and this leaves it untouched.  Use the
    return value; do not read the iterator's position afterwards.

`chain.from_iterable` is spelled `chain_from_iterable` here.  The subset
refuses attribute assignment on a function (setattr: assignment to .x on a
function) and refuses `class`, so there is nowhere to hang the real name.
It is the same function under a flat name.

Edge cases, all checked against the live module on 2026-09-16:

  * `chain()` with no arguments is `[]`; `chain('ab')` is `['a', 'b']`, so a
    string argument is chained CHARACTER BY CHARACTER, not appended whole.
  * `chain_from_iterable('ab')` is likewise `['a', 'b']`: the outer iterable's
    items are themselves iterated.
  * `islice(it, stop)` is the one-argument form; `islice(it, start, stop)` and
    `islice(it, start, stop, step)` are the others.  `None` means "no bound"
    for `start` and `stop` and "1" for `step`.  `stop` smaller than `start`
    gives `[]`.  `step` must be >= 1: `islice` has no reverse and no 0.
  * `zip_longest()` with no iterables is `[]`, not `[()]`.  It stops at the
    LONGEST input and pads the rest with `fillvalue` (default `None`), which
    is the whole difference from `zip`.

DIVERGENCES (two, deliberate, both documented rather than hidden):

  1. `islice` with the wrong number of positional arguments raises
     `ValueError` here carrying CPython's exact message
     (`islice expected at least 2 arguments, got 1`), where CPython raises
     `TypeError`.  Same for an unexpected keyword to `zip_longest`, whose
     message CPython leaves keyless: `zip_longest() got an unexpected keyword
     argument`, with no mention of which one.  The
     subset has no class statement and most builtin exception names are not
     bound, so ValueError with a non-empty message is the only shape
     available.  The cases print the message, never the type, so the
     differential against the real module is byte-identical.
  2. CPython's islice also rejects an index above `sys.maxsize`.  That is not
     reproduced: an index that large is a bigint, which the core engine
     refuses outright, so the check could never fire where it matters.  The
     `x >= 0` half, which does fire, is reproduced exactly, message and all.

Not covered: `itertools.accumulate`, `pairwise` and `groupby`, which have
their own unit, as do `product`, `combinations` and `permutations`.
"""
# fills: itertools.chain, itertools.chain.from_iterable, itertools.islice, itertools.zip_longest
# reference: itertools


def chain(*iterables):
    """Return every item of every argument, in order, as one flat list.

    Equivalent to `list(itertools.chain(*iterables))`.
    """
    out = []
    for iterable in iterables:
        for item in iterable:
            out.append(item)
    return out


def chain_from_iterable(iterables):
    """`itertools.chain.from_iterable`, under a flat name.

    Equivalent to `list(itertools.chain.from_iterable(iterables))`.
    """
    out = []
    for iterable in iterables:
        for item in iterable:
            out.append(item)
    return out


def _islice_bounds(args):
    """Validate the positional tail of islice() and return (start, stop, step).

    CPython checks the arguments in this order -- stop, then start, then step
    -- and a case below pins it: islice(s, -1, -1) complains about the stop.
    """
    count = len(args)
    if count < 1:
        raise ValueError("islice expected at least 2 arguments, got 1")
    if count > 3:
        raise ValueError(
            "islice expected at most 4 arguments, got " + str(count + 1)
        )

    if count == 1:
        start = 0
        stop = args[0]
        step = 1
    else:
        start = args[0]
        stop = args[1]
        step = 1
        if count == 3:
            step = args[2]

    if stop is None:
        stop = -1
    elif stop < 0:
        raise ValueError(
            "Stop argument for islice() must be None or an integer: "
            "0 <= x <= sys.maxsize."
        )

    if start is None:
        start = 0
    elif start < 0:
        raise ValueError(
            "Indices for islice() must be None or an integer: "
            "0 <= x <= sys.maxsize."
        )

    if step is None:
        step = 1
    elif step < 1:
        raise ValueError(
            "Step for islice() must be a positive integer or None."
        )

    # stop == -1 is the internal spelling of "unbounded"; no caller can reach
    # it, because a negative stop was rejected above.
    return (start, stop, step)


def islice(iterable, *args):
    """Return the selected slice of `iterable`, as a list.

    Equivalent to `list(itertools.islice(iterable, *args))`.
    """
    bounds = _islice_bounds(args)
    start = bounds[0]
    stop = bounds[1]
    step = bounds[2]

    if stop >= 0 and stop <= start:
        return []

    out = []
    wanted = start
    index = -1
    for item in iterable:
        index = index + 1
        if index == wanted:
            out.append(item)
            wanted = wanted + step
        if stop >= 0 and index + 1 >= stop:
            # Pull no further: this is what keeps a bounded islice usable on
            # an input that never ends.
            break
    return out


def zip_longest(*iterables, **kwargs):
    """Zip the iterables, padding the short ones, as a list of tuples.

    Equivalent to `list(itertools.zip_longest(*iterables, fillvalue=...))`.
    """
    for key in kwargs:
        if key != "fillvalue":
            raise ValueError(
                "zip_longest() got an unexpected keyword argument"
            )
    fillvalue = None
    if "fillvalue" in kwargs:
        fillvalue = kwargs["fillvalue"]

    pools = []
    longest = 0
    for iterable in iterables:
        pool = tuple(iterable)
        pools.append(pool)
        if len(pool) > longest:
            longest = len(pool)

    if not pools:
        # No iterables at all is [], not [()].
        return []

    rows = []
    i = 0
    while i < longest:
        row = []
        for pool in pools:
            if i < len(pool):
                row.append(pool[i])
            else:
                row.append(fillvalue)
        rows.append(tuple(row))
        i = i + 1
    return rows


def _islice_message(iterable, args):
    """Return the message of the ValueError islice(iterable, *args) raises."""
    try:
        islice(iterable, *args)
    except ValueError as exc:
        return str(exc)
    return ""


def _zip_message(iterables, kwargs):
    """Return the message of the ValueError zip_longest(...) raises."""
    try:
        zip_longest(*iterables, **kwargs)
    except ValueError as exc:
        return str(exc)
    return ""


# --- cases ---
# chain: flat concatenation, in argument order.
print(chain("ABC", "DEF"))
print(chain([1, 2], [3], [], [4, 5]))
print(chain())
print(chain([]))
print(chain([], [], []))
print(chain("ab"))
print(chain([1, 2]))
print(chain(range(3), "xy", (True, None)))
print(repr(chain([1, "1"], [True])))
print(chain([[1, 2], [3]], [[4]]))

# chain_from_iterable: the outer items are themselves iterated.
print(chain_from_iterable(["ABC", "DEF"]))
print(chain_from_iterable([]))
print(chain_from_iterable([[], []]))
print(chain_from_iterable("ab"))
print(chain_from_iterable([[1, 2], (3, 4), range(5, 7)]))
print(chain_from_iterable((p for p in ["ab", "cd"])))
print(chain_from_iterable([{"k": 1}]))

# islice: the one-argument form is a stop.
print(islice("ABCDEFG", 2))
print(islice("ABCDEFG", 0))
print(islice("ABCDEFG", 100))
print(islice("ABCDEFG", None))
print(islice("", 3))

# start/stop, and start/stop/step.
print(islice("ABCDEFG", 2, 4))
print(islice("ABCDEFG", 2, None))
print(islice("ABCDEFG", 2, None, 2))
print(islice("ABCDEFG", 0, None, 3))
print(islice(range(10), 2, 9, 3))
print(islice(range(10), 1, None, 4))
print(islice("ABCDEFG", None, None, 2))
print(islice("ABCDEFG", None, None, None))
print(islice("ABCDEFG", 1, 2, None))

# stop at or below start, and a start past the end.
print(islice("ABCDE", 3, 1))
print(islice("ABCDE", 2, 2))
print(islice("ABCDE", 10, 20))
print(islice("ABCDE", 10, None))
print(islice("ABCDE", 1, 100))

# A big step takes only the first selected item.
print(islice("ABCDEFG", 0, None, 100))
print(islice("ABCDEFG", 3, None, 100))
print(islice(range(7), 1, 6, 2))

# zip_longest: stops at the LONGEST, pads the rest.
print(zip_longest("ABCD", "xy"))
print(zip_longest("ABCD", "xy", fillvalue="-"))
print(zip_longest("AB", "CDE"))
print(zip_longest("AB", "", fillvalue=0))
print(zip_longest("AB"))
print(zip_longest())
print(zip_longest("", ""))
print(zip_longest([], [], fillvalue=9))
print(zip_longest([1, 2], [3, 4], [5, 6]))
print(zip_longest([1], [2, 3], [4, 5, 6], fillvalue=0))
print(repr(zip_longest([1, 2], ["1"], fillvalue=True)))
print(zip_longest(range(3), (c for c in "ab"), fillvalue=None))

# The inputs are read once, up front.
left = [1, 2]
rows = zip_longest(left, "ab")
left.append(3)
print(rows)

# Errors.  Only the message is printed -- see DIVERGENCES in the docstring.
print(repr(_islice_message("ABCDE", (-1,))))
print(repr(_islice_message("ABCDE", (0, -1))))
print(repr(_islice_message("ABCDE", (-1, -1))))
print(repr(_islice_message("ABCDE", (-1, 3, -1))))
print(repr(_islice_message("ABCDE", (0, -1, -1))))
print(repr(_islice_message("ABCDE", (-1, None))))
print(repr(_islice_message("ABCDE", (None, -1))))
print(repr(_islice_message("ABCDE", (0, 3, -1))))
print(repr(_islice_message("ABCDE", (0, 3, 0))))
print(repr(_islice_message("ABCDE", (None, None, 0))))
print(repr(_islice_message("ABCDE", ())))
print(repr(_islice_message("ABCDE", (0, 1, 1, 1))))
print(repr(_islice_message("ABCDE", (1, 3))))
print(repr(_zip_message(("ab",), {"bogus": 1})))
print(repr(_zip_message(("ab",), {"fillvalue": 1})))

# Width: 300 chained arguments and a 200-wide zip_longest.  Every loop above
# is flat, so neither is near the 180-frame recursion ceiling.
many = [[i] for i in range(300)]
flat = chain(*many)
print(len(flat), flat[0], flat[299], sum(flat))
print(chain_from_iterable(many) == flat)

wide = zip_longest(*[[i, i + 1] for i in range(200)], fillvalue=-1)
print(len(wide), len(wide[0]))
print(wide[0][0], wide[0][199], wide[1][0], wide[1][199])

ragged = zip_longest(*([[0]] * 199 + [[0, 1, 2]]), fillvalue="p")
print(len(ragged), len(ragged[0]))
print(ragged[0][0], ragged[0][199], ragged[2][0], ragged[2][199])

# islice over a long input, and over a lazily built one.
print(islice(range(1000), 995, None))
print(islice((n * n for n in range(1000)), 3, 9, 2))
print(len(islice(range(10000), 4000)))
