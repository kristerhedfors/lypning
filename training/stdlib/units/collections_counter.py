"""collections.Counter's refused half, and defaultdict-shaped grouping.

The core engine refuses ``import collections`` outright.  The wider
variant serves the module, but only part of ``Counter``: construction
from an iterable or a mapping, ``c[k]``, ``k in c``, ``len``, ``del``,
``.items()``, ``.keys()``, ``.values()``, ``.get()``, ``.copy()``,
``.update()``, ``.most_common()`` and ``repr``.  It refuses
``Counter.elements``, ``Counter.total``, ``Counter.subtract`` and every
multiset operator -- ``c1 + c2``, ``c1 - c2``, ``c1 | c2``, ``c1 & c2``,
``+c``, ``-c`` -- and it refuses ``defaultdict(lambda: ...)``.

So this unit is written the way Counter code has to be written to run
here: **the refused constructs never appear**.  Not one of the helpers
below calls ``.elements()``, ``.total()`` or ``.subtract()``, adds two
Counters, or hands ``defaultdict`` a lambda.  Each of those behaviours
is instead built out of the primitives the engine does serve, ported
from CPython 3.11's ``Lib/collections/__init__.py``:

  * ``most_common(c, n)`` -- CPython uses ``sorted(..., reverse=True)``
    when ``n`` is None and ``heapq.nlargest`` otherwise.  There is no
    ``heapq`` here, and none is needed: ``nlargest`` is documented as
    equivalent to ``sorted(iterable, key=key, reverse=True)[:n]`` and is
    stable in the same way, so one stable sort covers both.  Ties keep
    **first-insertion order**, which is the detail worth pinning, and
    ``n <= 0`` gives the empty list rather than a slice from the end.
  * ``elements(c)`` -- each key repeated by its count, in insertion
    order, skipping counts that are zero or negative.
  * ``total(c)`` -- the sum of the counts, negatives included.
  * ``subtract(c, other)`` -- in place, and unlike ``-`` it is allowed
    to drive counts to zero and below.
  * ``add`` / ``sub`` / ``union`` / ``intersection`` / ``positive`` /
    ``negative`` -- the multiset operators.  Their one shared rule is
    that the **output keeps only positive counts**, and their ordering
    rule is elements in the order first seen in the left operand, then
    in the right.  Both are ported exactly, including ``sub``'s odd but
    correct second loop, which lifts a *negative* count in the right
    operand into a positive count in the result.
  * ``group`` / ``group_by`` / ``tally_by`` -- ``defaultdict(list)`` and
    ``defaultdict(int)``.  A bare type as the factory is served; only a
    lambda factory is not, and nothing here needs one.

Two things about Counter that surprise people, both pinned below:

  * Reading a missing key returns ``0`` and **does not insert it**;
    augmenting one (``c[k] += 1``) does insert it.
  * A count reduced to zero stays in the counter.  It is still a key,
    it still shows up in ``most_common`` and ``repr``, and only
    ``elements`` and the operators drop it.

One deliberate divergence, forced by the subset and documented rather
than hidden: ``Counter.elements()`` returns an *iterator*; there are no
generators here, so ``elements`` returns a materialised list.  Every
case below compares it against ``list(real.elements())``, which is the
same sequence.  ``Counter.fromkeys`` is not covered: CPython defines it
only to raise ``NotImplementedError``, and the subset cannot raise that.
"""
# fills: collections.Counter.most_common, collections.Counter.elements, collections.Counter.total, collections.Counter.subtract, collections.Counter.__add__, collections.Counter.__sub__, collections.Counter.__or__, collections.Counter.__and__, collections.defaultdict
# reference: collections

import collections


def count(iterable):
    """Counter(iterable) -- tally the items of any iterable."""
    return collections.Counter(iterable)


def count_map(mapping):
    """Counter(mapping) -- take the counts straight from a dict."""
    return collections.Counter(mapping)


def most_common(counter, n=None):
    """The n most common (element, count) pairs, most common first.

    Stable: elements with equal counts keep the order they were first
    inserted in.  ``n is None`` lists everything; ``n <= 0`` is empty.
    """
    ordered = sorted(counter.items(), key=lambda kv: kv[1], reverse=True)
    if n is None:
        return ordered
    if n <= 0:
        return []
    return ordered[:n]


def elements(counter):
    """Each element repeated by its count; zero and negative are skipped.

    CPython returns an iterator; this returns the same sequence as a
    list, because the subset has no generators.
    """
    out = []
    for elem, cnt in counter.items():
        i = 0
        while i < cnt:
            out.append(elem)
            i = i + 1
    return out


def total(counter):
    """Sum of the counts, negatives included."""
    return sum(counter.values())


def update(counter, other):
    """Counter.update: add counts in, in place.  Accepts a mapping or an
    iterable.  Both inputs and outputs may hold zero and negative counts.
    """
    if isinstance(other, dict):
        for elem in other:
            counter[elem] = counter.get(elem, 0) + other[elem]
    else:
        for elem in other:
            counter[elem] = counter.get(elem, 0) + 1
    return counter


def subtract(counter, other):
    """Counter.subtract: take counts away, in place, below zero if need be."""
    if isinstance(other, dict):
        for elem in other:
            counter[elem] = counter.get(elem, 0) - other[elem]
    else:
        for elem in other:
            counter[elem] = counter.get(elem, 0) - 1
    return counter


def add(a, b):
    """a + b -- counts summed, only the positive results kept."""
    result = collections.Counter()
    for elem, cnt in a.items():
        newcount = cnt + b.get(elem, 0)
        if newcount > 0:
            result[elem] = newcount
    for elem, cnt in b.items():
        if elem not in a and cnt > 0:
            result[elem] = cnt
    return result


def sub(a, b):
    """a - b -- counts differenced, only the positive results kept.

    The second loop is not a typo: an element present only in ``b`` with
    a *negative* count contributes its magnitude to the result.
    """
    result = collections.Counter()
    for elem, cnt in a.items():
        newcount = cnt - b.get(elem, 0)
        if newcount > 0:
            result[elem] = newcount
    for elem, cnt in b.items():
        if elem not in a and cnt < 0:
            result[elem] = 0 - cnt
    return result


def union(a, b):
    """a | b -- the larger of the two counts, only the positive ones kept."""
    result = collections.Counter()
    for elem, cnt in a.items():
        other_count = b.get(elem, 0)
        if cnt < other_count:
            newcount = other_count
        else:
            newcount = cnt
        if newcount > 0:
            result[elem] = newcount
    for elem, cnt in b.items():
        if elem not in a and cnt > 0:
            result[elem] = cnt
    return result


def intersection(a, b):
    """a & b -- the smaller of the two counts, only the positive ones kept.

    There is no second loop: an element missing from ``a`` has count 0
    there, so the minimum can never be positive.
    """
    result = collections.Counter()
    for elem, cnt in a.items():
        other_count = b.get(elem, 0)
        if cnt < other_count:
            newcount = cnt
        else:
            newcount = other_count
        if newcount > 0:
            result[elem] = newcount
    return result


def positive(counter):
    """+c -- drop the zero and negative counts."""
    result = collections.Counter()
    for elem, cnt in counter.items():
        if cnt > 0:
            result[elem] = cnt
    return result


def negative(counter):
    """-c -- keep only the negative counts, with the sign flipped."""
    result = collections.Counter()
    for elem, cnt in counter.items():
        if cnt < 0:
            result[elem] = 0 - cnt
    return result


def group(pairs):
    """defaultdict(list): collect the values of (key, value) pairs by key."""
    groups = collections.defaultdict(list)
    for key, value in pairs:
        groups[key].append(value)
    return groups


def group_by(items, keyfunc):
    """defaultdict(list): bucket items by ``keyfunc(item)``, order kept."""
    groups = collections.defaultdict(list)
    for item in items:
        groups[keyfunc(item)].append(item)
    return groups


def tally_by(items, keyfunc):
    """defaultdict(int): count items by ``keyfunc(item)``."""
    tally = collections.defaultdict(int)
    for item in items:
        tally[keyfunc(item)] += 1
    return tally


# --- cases ---
_C = count("abracadabra")
print(repr(_C))
print(list(_C.items()))
print(_C["a"], _C["b"], _C["z"])
print(len(_C))

# most_common: all of it, a prefix, and the degenerate n.
print(most_common(_C))
print(most_common(_C, 1))
print(most_common(_C, 3))
print(most_common(_C, 99))
print(most_common(_C, 0))
print(most_common(_C, -1))
print(most_common(count("")))

# ...and it agrees with the method the engine does serve.
print(most_common(_C) == _C.most_common())
print(most_common(_C, 3) == _C.most_common(3))
print(most_common(_C, 0) == _C.most_common(0))
print(most_common(_C, 99) == _C.most_common(99))

# Ties keep first-insertion order, not alphabetical order.
_T = count("zyxzyx")
print(list(_T.items()))
print(most_common(_T))
print(most_common(_T, 2))
print(most_common(_T) == _T.most_common())
print(most_common(_T, 2) == _T.most_common(2))

# elements: insertion order, repetitions, zero and negative skipped.
print(elements(count("ABCABC")))
print("".join(elements(count("abracadabra"))))
print(elements(count("")))
print(elements(count_map({"a": 2, "b": 0, "c": -3, "d": 1})))
print(elements(count_map({2: 2, 3: 3, 17: 1})))

# total sums the counts, negatives included.
print(total(_C))
print(total(count("")))
print(total(count_map({"a": 5, "b": -2})))
print(total(_C) == sum(_C.values()))

# A missing key reads as 0 and is NOT inserted; augmenting inserts it.
_M = count("aab")
print(_M["zz"], len(_M), "zz" in _M)
_M["zz"] += 1
print(_M["zz"], len(_M), "zz" in _M)
print(list(_M.items()))

# A count driven to zero stays a key.
_Z = count("aaabbc")
_Z["b"] -= 2
print(list(_Z.items()))
print(most_common(_Z))
print(elements(_Z))
print(repr(_Z))
del _Z["b"]
print(list(_Z.items()))

# del of a missing key does not raise (Counter.__delitem__ swallows it).
_D = count("ab")
del _D["nope"]
print(list(_D.items()))

# update: mapping and iterable, in place, negatives allowed.
_U = count("which")
print(list(update(_U, "witch").items()))
print(list(update(_U, count("watch")).items()))
print(_U["h"])
print(list(update(count(""), {"a": -4}).items()))

# subtract: below zero, unlike the `-` operator.
_S = count("which")
subtract(_S, "witch")
subtract(_S, count("watch"))
print(list(_S.items()))
print(_S["h"], _S["w"])
print(list(subtract(count_map({"a": 2}), {"a": 1, "b": 3}).items()))
print(list(subtract(count(""), "aab").items()))

# The multiset operators, in CPython's documented ordering.
_A = count("abbb")
_B = count("bcc")
print(repr(add(_A, _B)))
print(list(add(_A, _B).items()))
print(repr(sub(count("abbbc"), count("bccd"))))
print(repr(union(_A, _B)))
print(repr(intersection(_A, _B)))
print(list(intersection(_A, _B).items()))

# Only positive counts survive, on every operator.
_P = count_map({"a": 3, "b": 0, "c": -2})
_Q = count_map({"a": 3, "c": -5, "d": -1})
print(list(add(_P, _Q).items()))
print(list(sub(_P, _Q).items()))
print(list(union(_P, _Q).items()))
print(list(intersection(_P, _Q).items()))
print(list(positive(_P).items()))
print(list(negative(_P).items()))
print(list(negative(_Q).items()))
print(list(add(count(""), count("")).items()))
print(list(sub(count("ab"), count("ab")).items()))

# Grouping with defaultdict, order preserved, no lambda factory.
_G = group([("fruit", "apple"), ("veg", "leek"), ("fruit", "pear")])
print(list(_G.items()))
print(dict(_G))
print(len(_G), _G["fruit"])
print(sorted(_G.keys()))

_WORDS = ["apple", "avocado", "beet", "cherry", "chard", "corn"]
print(list(group_by(_WORDS, lambda w: w[0]).items()))
print(list(tally_by(_WORDS, lambda w: w[0]).items()))
print(list(tally_by(_WORDS, len).items()))
print(list(group_by([], len).items()))

# Reading a defaultdict key inserts it -- that is the whole point of it.
_E = group([])
print(len(_E))
print(_E["new"])
print(len(_E), list(_E.items()))
