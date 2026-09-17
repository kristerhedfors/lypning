"""itertools.accumulate, pairwise and groupby, as lists.

The engines refuse `import itertools` (module: import itertools) and they
refuse `yield`, so none of these can be the lazy iterators CPython ships.
They are **materialising replacements**: each reads its input to exhaustion
and returns the whole `list` before returning anything at all.

That is a real semantic difference a caller must understand:

  * Correct only over FINITE inputs.  `accumulate` over an endless iterable is
    a perfectly ordinary thing to do with the real one -- a running total you
    stop consuming when you like -- and it hangs here.
  * Memory is the whole result, not one item at a time.
  * You get a `list`, so `len()`, indexing and repeated passes work, where the
    real iterators are spent after one pass.

`groupby` diverges further, and in the caller's favour, so it needs saying
plainly.  CPython's `groupby` yields `(key, grouper)` where the grouper is a
LAZY iterator sharing one underlying cursor: advancing to the next group
silently empties the previous grouper.  This is why
`[(k, list(g)) for k, g in groupby('AAB')]` works but
`[(k, list(g)) for k, g in [(k, g) for k, g in groupby('AAB')]]` gives
`[('A', []), ('B', [])]` in CPython.  Here each group is a real `list`, built
eagerly, and it stays valid forever.  Code written against the lazy version
still works; code that RELIED on a grouper going empty does not.  A case below
pins the difference by listing the groups twice.

`groupby` groups CONSECUTIVE runs, not equal values anywhere in the input --
which is why it is almost always fed a sorted input, and why the cases sort
first and then show what an unsorted input does instead.  Keys are compared
with `==`, so `1.0`, `1` and `True` land in one run, and the key kept is the
FIRST of the run.

Edge cases, all checked against the live module on 2026-09-16:

  * `accumulate(it)` with no `func` is a running sum; `func` is
    positional-or-keyword and `None` means "add", so `accumulate(xs, None)` is
    the running sum too.
  * `initial=` is keyword-only, is PREPENDED to the output, and makes the
    result one longer than the input: `accumulate([], initial=5)` is `[5]`
    while `accumulate([])` is `[]`.
  * `initial=None` means "no initial value", not "prepend None" -- so `None`
    is a sentinel here and cannot be used as a real initial value.
  * `func` is never called on the first element: `accumulate([3], max)` is
    `[3]` with no call at all.
  * `pairwise` of a 0- or 1-element input is `[]`.
  * `groupby` of an empty input is `[]`.

DIVERGENCE (one, deliberate): an unexpected keyword argument to `accumulate`
raises `ValueError` here, where CPython raises `TypeError`.  The subset has no
class statement and most builtin exception names are not bound, so ValueError
is the only shape available.  That case prints the exception's TYPE and
nothing else, so the divergence shows up as exactly one differing line --
`accumulate([1, 2], bogus=1)` -- against the real module, and every other line
matches byte for byte.  Catching only `ValueError` would let CPython's
`TypeError` escape and leave every case below it compared against nothing,
which is why the `except` names both.

The TYPE and not the message, because the message is not API and moved inside
the window this corpus targets: CPython 3.9 through 3.12 say `'bogus' is an
invalid keyword argument for accumulate()` and 3.13 and 3.14 say `accumulate()
got an unexpected keyword argument 'bogus'`, the Argument Clinic wording.
Measured 2026-09-17 on CPython 3.9.23, 3.10.18, 3.11.15, 3.12.11, 3.13.7 and
3.14.0rc2.  A case that printed the text would have been right on four of those
six releases; a unit is inlined and run on whichever one the reader has.  The
message this port raises is its own and is deliberately not a copy of either
CPython wording -- copying one would be picking a release.

Not covered: `itertools.chain`, `islice`, `zip_longest`, `product`,
`combinations` and `permutations`, which have their own units in this corpus.
"""
# fills: itertools.accumulate, itertools.pairwise, itertools.groupby
# reference: itertools


def accumulate(iterable, func=None, **kwargs):
    """Return the running totals of `iterable`, as a list.

    Equivalent to `list(itertools.accumulate(iterable, func, initial=...))`.
    """
    for key in kwargs:
        if key != "initial":
            # Not a copy of CPython's text: CPython has two wordings for
            # this in 3.9-3.14 and no case prints either.  See the module
            # docstring, DIVERGENCE.
            raise ValueError(
                "accumulate() does not take the keyword argument " + repr(key)
            )
    initial = None
    if "initial" in kwargs:
        initial = kwargs["initial"]

    out = []
    total = None
    # `initial is None` is CPython's own sentinel test, not a bug: the real
    # accumulate cannot prepend a literal None either.
    started = initial is not None
    if started:
        total = initial
        out.append(total)

    for item in iterable:
        if not started:
            # The first element is adopted whole; func is never called on it.
            total = item
            started = True
        elif func is None:
            total = total + item
        else:
            total = func(total, item)
        out.append(total)
    return out


def pairwise(iterable):
    """Return successive overlapping pairs of `iterable`, as a list of tuples.

    Equivalent to `list(itertools.pairwise(iterable))`.
    """
    out = []
    previous = None
    have_previous = False
    for item in iterable:
        if have_previous:
            out.append((previous, item))
        previous = item
        have_previous = True
    return out


def groupby(iterable, key=None):
    """Return consecutive runs of `iterable` as a list of (key, list) pairs.

    Equivalent to `[(k, list(g)) for k, g in itertools.groupby(iterable, key)]`
    -- except that the lists here stay valid, where CPython's groupers do not.
    """
    groups = []
    run_key = None
    run = []
    started = False
    for item in iterable:
        if key is None:
            item_key = item
        else:
            item_key = key(item)
        if not started:
            started = True
            run_key = item_key
            run = [item]
        elif item_key == run_key:
            run.append(item)
        else:
            groups.append((run_key, run))
            # The key kept for a run is the FIRST one seen in it.
            run_key = item_key
            run = [item]
    if started:
        groups.append((run_key, run))
    return groups


def _times(a, b):
    return a * b


def _is_odd(n):
    return n % 2 == 1


def _accumulate_error(iterable, kwargs):
    """Return the TYPE name of whatever accumulate(...) raises, or "".

    The tuple in the `except` is load-bearing: CPython answers an unexpected
    keyword with TypeError, and a helper that caught only ValueError would let
    it escape -- leaving every case printed below compared against nothing at
    all.  The type is reported rather than swallowed, which is what makes the
    divergence above one visible line instead of a silence.

    The type and not the message.  CPython reworded this one between 3.12 and
    3.13 and the text is not a fact about `accumulate`; the type is.  See the
    module docstring, DIVERGENCE.
    """
    try:
        accumulate(iterable, **kwargs)
    except (ValueError, TypeError) as exc:
        return type(exc).__name__
    return ""


# --- cases ---
# accumulate: the running sum is the default.
print(accumulate([1, 2, 3, 4]))
print(accumulate([1, 2, 3, 4], None))
print(accumulate(range(5)))
print(accumulate([3]))
print(accumulate([]))
print(accumulate(()))
print(accumulate([1.5, 2.5, -1.0]))
print(accumulate([-1, -2, -3]))

# func= is positional-or-keyword.
print(accumulate([1, 2, 3], _times))
print(accumulate([1, 2, 3], func=_times))
print(accumulate([1, 2, 3, 4, 5], max))
print(accumulate([2, 1, 4, 3], max))
print(accumulate([2, 1, 4, 3], min))
print(accumulate("abc", lambda a, b: a + b))
print(accumulate(["x", "y"], lambda a, b: b + a))
print(accumulate([1, 2, 3], lambda a, b: a - b))

# func is never called on the first element.
print(accumulate([3], max))
print(accumulate(["only"], lambda a, b: a + b))

# initial= is prepended and makes the result one longer.
print(accumulate([1, 2, 3], initial=100))
print(accumulate([1, 2, 3], None, initial=0))
print(accumulate([], initial=10))
print(accumulate([], max, initial=5))
print(accumulate([1, 2, 3], _times, initial=10))
print(accumulate("bc", lambda a, b: a + b, initial="a"))
print(accumulate([[1], [2]], initial=[]))
print(len(accumulate([1, 2, 3])), len(accumulate([1, 2, 3], initial=0)))

# initial=None means "no initial value", not "prepend None".
print(accumulate([1, 2, 3], initial=None))
print(accumulate([], initial=None))

# Types come through unchanged; repr keeps int and bool apart.
print(repr(accumulate([True, True, True])))
print(repr(accumulate([1, 2], initial=True)))
print(repr(accumulate([0.0, 1])))

# pairwise.
print(pairwise("ABCD"))
print(pairwise([1, 2, 3]))
print(pairwise([1, 2]))
print(pairwise([1]))
print(pairwise([]))
print(pairwise(""))
print(pairwise(range(4)))
print(pairwise((n * n for n in range(5))))
print(repr(pairwise([1, "1", True])))
print([b - a for a, b in pairwise([1, 4, 9, 16])])

# groupby over a SORTED input -- the normal use, and the one that gives one
# group per distinct value.
print(groupby(sorted("mississippi")))
print(groupby(sorted([3, 1, 2, 1, 3, 3])))
print(groupby(sorted(["pear", "apple", "plum", "fig"], key=len), key=len))
print(groupby("AAAABBBCCD"))

# The same values UNSORTED fragment into several runs -- the classic trap.
print(groupby("AAAABBBCCDAABBB"))
print(groupby([3, 1, 2, 1, 3, 3]))
print(groupby([1, 1, 2, 1]))

# Empty and single-element inputs.
print(groupby([]))
print(groupby(""))
print(groupby([7]))
print(groupby("a", None))

# A key function; the key stored is the FIRST of each run.
print(groupby([1, 3, 5, 2, 4, 7], key=_is_odd))
print(groupby([1, 2, 3], lambda x: x > 1))
print(groupby(range(10), lambda n: n // 3))
print(repr(groupby([1.0, 1, True, 0, False])))
print(repr(groupby(["a", "A", "b"], key=str.lower)))

# The groups here are real lists and stay valid after the whole result is
# built.  CPython's lazy groupers would be empty on this second pass -- see
# the docstring.  Both sides of the differential go through this same code.
held = groupby("AAB")
print([(k, list(g)) for k, g in held])
print([(k, list(g)) for k, g in held])
print(held[0][1] == ["A", "A"], held[1][1] == ["B"])

# Errors.  The TYPE alone -- CPython reworded the message in 3.13 and the
# type is the part that is API; see DIVERGENCE above.
print(repr(_accumulate_error([1, 2], {"bogus": 1})))
print(repr(_accumulate_error([1, 2], {"initial": 0})))

# Length: a long input, handled by flat loops well clear of the 180-frame
# recursion ceiling.  Every value stays inside signed 64-bit.
running = accumulate(range(1000))
print(len(running), running[0], running[1], running[999])
print(accumulate(range(1, 20), _times)[18])
print(len(pairwise(range(1000))), pairwise(range(1000))[998])
runs = groupby([n // 100 for n in range(1000)])
print(len(runs), runs[0][0], len(runs[0][1]), runs[9][0], runs[9][1][99])
print(len(groupby(range(500))))
