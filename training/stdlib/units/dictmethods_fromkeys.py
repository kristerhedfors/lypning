"""dict.fromkeys, which refuses on the engines, plus a sorted-list helper.

Measured 2026-09-16::

    $ lypning-l -c 'print(dict.fromkeys(["a"],1))'
    lypning-l: unsupported: dict-method: dict.fromkeys()

That is a language-level hole, not a missing module, so ``# reference:``
is ``-`` and the differential runs the real method under CPython on the
same inputs.

``fromkeys`` is a three-line loop, and every interesting thing about it is
a consequence of being exactly that loop:

  * The value is stored, not copied, so ONE mutable object ends up shared
    by every key.  ``fromkeys(['a', 'b'], [])`` then ``d['a'].append(1)``
    leaves ``d['b']`` as ``[1]`` too.  This is the bug the method is most
    often used to write, and it is faithful behaviour, not a divergence.
  * Re-assigning a key that is already present keeps its ORIGINAL
    position, so the result is the input in first-seen order with
    duplicates removed -- which is why ``list(dict.fromkeys(seq))`` is the
    order-preserving ``unique`` every codebase eventually needs.
  * Keys are matched by hash and equality, so ``1``, ``1.0`` and ``True``
    are one key, and the FIRST of them is the one that survives.
  * An unhashable key is the dict's own TypeError, raised on insertion.

The second helper, ``sorted_disjoint``, is deliberately NOT a set method.
``set.isdisjoint`` is one of the refusals no reimplementation short of
CPython gets right, and nothing here tries: ``sorted_disjoint`` takes two
SORTED LISTS, merges them with ``<`` the way ``list.sort`` compares, and
returns whether they share an element.  Its answer never depends on set
construction, set iteration order or hashing -- unhashable-but-orderable
elements work fine -- and it is undefined on unsorted input, which a set
would not be.  Read it as "do these two sorted sequences overlap", never
as a substitute for ``set.isdisjoint``.

Not covered: ``fromkeys`` called on a dict SUBCLASS (classes refuse), and
any set operation at all.
"""
# fills: dict.fromkeys
# reference: -


def fromkeys(iterable, value=None):
    """``dict.fromkeys(iterable, value)`` -- every key mapped to ONE value."""
    out = {}
    for key in iterable:
        out[key] = value
    return out


def sorted_disjoint(a, b):
    """True when the SORTED LISTS `a` and `b` have no element in common.

    A list helper, NOT ``set.isdisjoint``: it compares elements with ``<``
    only, in the order they already lie in, so nothing about the answer
    depends on hashing or on set iteration order.  `a` and `b` must each
    be sorted ascending; duplicates inside either list are harmless.
    """
    i = 0
    j = 0
    while i < len(a) and j < len(b):
        if a[i] < b[j]:
            i = i + 1
        elif b[j] < a[i]:
            j = j + 1
        else:
            return False
    return True


def _err(label, ok):
    """Run `ok`, and report the exception type and message if it raises."""
    try:
        return label + ": " + repr(ok())
    except TypeError as exc:
        return label + ": TypeError: " + str(exc)


def _one_object(keys):
    """Whether every key of one fromkeys() result reaches the SAME object."""
    made = fromkeys(keys, [])
    first = made[keys[0]]
    for key in keys:
        if made[key] is not first:
            return False
    return True


def _share(keys, value):
    """Mutate the value reached through the FIRST key, then show the dict.

    The point of the case: `value` is stored once, so the mutation is
    visible through every key.
    """
    made = fromkeys(keys, value)
    made[keys[0]].append(1)
    return made


# --- cases ---
# The default value is None, and the keys keep their first-seen order.
print(fromkeys("abc"))
print(fromkeys(["a", "b"]))
print(fromkeys([]))
print(fromkeys(""))
print(fromkeys(range(3)))
print(fromkeys(range(3), 0))
print(fromkeys([], 5))

# Duplicates collapse to the FIRST occurrence, position included.
print(fromkeys([1, 2, 1]))
print(fromkeys("hello"))
print(fromkeys([3, 1, 3, 2, 1]))
print(fromkeys("abracadabra"))
print(list(fromkeys("abracadabra")))
print(list(fromkeys([3, 1, 3, 2, 1])))
print(list(fromkeys(["b", "a", "b", "c", "a"])))

# 1, 1.0 and True are one key, and the first of them wins.
print(fromkeys([1, 1.0, True]))
print(fromkeys([True, 1, 1.0]))
print(fromkeys([1.0, 1]))
print(repr(list(fromkeys([1, 1.0]))[0]))
print(repr(list(fromkeys([1.0, 1]))[0]))

# Any value, and any hashable key type.
print(fromkeys("ab", 0))
print(fromkeys("ab", "x"))
print(fromkeys("ab", (1, 2)))
print(fromkeys([(1, 2), (1, 2), (3, 4)]))
print(fromkeys([None, False, ()], "v"))
print(fromkeys(["k"], None))

# Any iterable, including one consumed lazily.
print(fromkeys(x for x in "ab"))
print(fromkeys((c for c in "aab"), 7))
print(fromkeys(tuple("ab"), 1))
print(fromkeys([w.strip() for w in " a , b , a ".split(",")]))

# ONE value object, shared by every key.
print(_share(["a", "b"], []))
print(_share(["a", "b", "c"], []))
print(_one_object(["a", "b", "c"]))

# An unhashable key is the dict's own error.
print(_err("listkey", lambda: fromkeys([[]])))
print(_err("dictkey", lambda: fromkeys([{}])))
print(_err("late", lambda: fromkeys(["ok", []])))

# sorted_disjoint over sorted lists -- a list helper, not a set method.
print(sorted_disjoint([1, 3, 5], [2, 4, 6]))
print(sorted_disjoint([1, 3, 5], [2, 3, 4]))
print(sorted_disjoint([], []))
print(sorted_disjoint([], [1, 2]))
print(sorted_disjoint([1, 2], []))
print(sorted_disjoint([1], [1]))
print(sorted_disjoint([1], [2]))
print(sorted_disjoint([2], [1]))
print(sorted_disjoint([1, 1, 1], [1]))
print(sorted_disjoint([1, 1, 2], [3, 3]))
print(sorted_disjoint([1, 2, 3], [3]))
print(sorted_disjoint([1, 2, 3], [0]))
print(sorted_disjoint([1, 2, 3], [4]))
print(sorted_disjoint(["ant", "bee"], ["cow", "dog"]))
print(sorted_disjoint(["ant", "bee"], ["bee"]))
print(sorted_disjoint([1.0, 2.5], [2.5]))
print(sorted_disjoint([0, 1], [1.0]))
print(sorted_disjoint(sorted("hello"), sorted("word")))
print(sorted_disjoint(sorted("abc"), sorted("xyz")))
print([sorted_disjoint([2, 4, 6], [n]) for n in range(8)])

# It only ever uses '<', so unhashable-but-orderable elements work.
print(sorted_disjoint([[1], [2]], [[3]]))
print(sorted_disjoint([[1], [2]], [[2]]))
