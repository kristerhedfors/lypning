"""str.translate and str.maketrans, which both refuse on the engines.

Measured 2026-09-16::

    $ lypning-l -c 'print("abc".translate({97:98}))'
    lypning-l: unsupported: str-method: str.translate()
    $ lypning-l -c 'print(str.maketrans("a","b"))'
    lypning-l: unsupported: str-method: str.maketrans()

These are language-level holes, not a missing module, so ``# reference:``
is ``-`` and the differential runs the real METHODS under CPython on the
same inputs.

``maketrans`` builds a plain dict keyed by ORDINAL:

  * One argument, a dict.  A one-character string key becomes its
    ordinal; an int key is kept as is; anything else is a TypeError, and
    a string key that is not exactly one character is a ValueError.
    VALUES are copied through untouched and are not validated here --
    ``str.maketrans`` does not check them, ``str.translate`` does.
  * Two arguments, equal-length strings: ``{ord(x[i]): ord(y[i])}``.  The
    values are ORDINALS, not one-character strings.
  * A third string maps each of its characters to ``None``, i.e. delete,
    and is applied AFTER the pair, so ``maketrans('ab', 'xy', 'ab')``
    deletes both rather than substituting them.

``translate`` walks the string once and looks each ordinal up:

  * A missing key keeps the character.  CPython gets that from catching
    LookupError around ``table[ord(ch)]``; with a dict it is the same
    thing as ``ord(ch) not in table``.
  * ``None`` deletes the character.
  * An int becomes ``chr(value)``, and is range-checked -- including
    ``True``, which is an int and translates to ``'\\x01'``.
  * A str is appended whole, so one character can expand to many, or to
    the empty string.
  * Anything else is a TypeError.

Not covered: a table that is not a dict.  CPython accepts any object with
``__getitem__`` -- a list indexes by ordinal, and a class with a
``__missing__`` is the documented way to map unknown characters -- but
classes refuse on the engines, and a non-dict table would need
``getattr``/``hasattr``, which refuse too.  Also not covered: the message
CPython raises when ``maketrans`` is handed a non-string second or third
argument, because that message names the offending type and
``type(y).__name__`` is a refused dunder attribute; pass strings.
"""
# fills: str.translate, str.maketrans
# reference: -

MAXUNICODE = 0x10FFFF


def maketrans(x, y=None, z=None):
    """``str.maketrans(x)``, ``str.maketrans(x, y)``, ``str.maketrans(x, y, z)``."""
    if y is None:
        table = {}
        for key in x:
            value = x[key]
            if isinstance(key, str):
                if len(key) != 1:
                    raise ValueError(
                        "string keys in translate table must be of length 1"
                    )
                table[ord(key)] = value
            elif isinstance(key, int):
                table[key] = value
            else:
                raise TypeError("keys in translate table must be strings or integers")
        return table

    if len(x) != len(y):
        raise ValueError("the first two maketrans arguments must have equal length")
    table = {}
    i = 0
    while i < len(x):
        table[ord(x[i])] = ord(y[i])
        i = i + 1
    if z is not None:
        for ch in z:
            table[ord(ch)] = None
    return table


def translate(s, table):
    """``s.translate(table)`` for a dict `table` keyed by ordinal."""
    out = []
    for ch in s:
        code = ord(ch)
        if code not in table:
            out.append(ch)
            continue
        value = table[code]
        if value is None:
            continue
        if isinstance(value, str):
            out.append(value)
            continue
        if isinstance(value, int):
            if value < 0 or value > MAXUNICODE:
                raise ValueError("character mapping must be in range(0x110000)")
            out.append(chr(value))
            continue
        raise TypeError("character mapping must return integer, None or str")
    return "".join(out)


def _err(label, ok):
    """Run `ok`, and report the exception type and message if it raises."""
    try:
        return label + ": " + repr(ok())
    except ValueError as exc:
        return label + ": ValueError: " + str(exc)
    except TypeError as exc:
        return label + ": TypeError: " + str(exc)


# --- cases ---
# maketrans, two arguments: the values are ORDINALS.
print(maketrans("ab", "xy"))
print(maketrans("", ""))
print(maketrans("a", "é"))
print(maketrans("aa", "xy"))

# maketrans, three arguments: the third maps to None, and wins.
print(maketrans("ab", "xy", "cd"))
print(maketrans("ab", "xy", "ab"))
print(maketrans("ab", "xy", "cdab"))
print(maketrans("ab", "xy", ""))

# maketrans, one dict argument: keys normalise, values pass through.
print(maketrans({"a": "XY", 98: None, 99: 100}))
print(maketrans({}))
print(maketrans({"a": 98}))
print(maketrans({0: "zero", 1114111: "last"}))
print(maketrans({"é": "e"}))
print(maketrans({97: 1.5}))

# maketrans rejects a bad key or an unequal pair.
print(_err("len2key", lambda: maketrans({"ab": "x"})))
print(_err("emptykey", lambda: maketrans({"": "x"})))
print(_err("floatkey", lambda: maketrans({1.5: "x"})))
print(_err("unequal", lambda: maketrans("ab", "xyz")))
print(_err("unequal2", lambda: maketrans("abc", "")))

# translate: substitute, delete, expand, and keep what is not in the table.
print(repr(translate("abc", {})))
print(repr(translate("", {97: "x"})))
print(repr(translate("aaa", maketrans("a", "b"))))
print(repr(translate("hello", maketrans("el", "ip"))))
print(repr(translate("hello", maketrans("el", "ip", "h"))))
print(repr(translate("abcd", {97: "XY", 98: None, 99: 100})))
print(repr(translate("abc", {97: ""})))
print(repr(translate("abc", {97: "long string"})))
print(repr(translate("banana", maketrans("an", "AN"))))
print(repr(translate("banana", maketrans("", "", "an"))))

# An int value is chr(), and bool is an int.
print(repr(translate("abc", {97: 65})))
print(repr(translate("abc", {97: 0})))
print(repr(translate("abc", {97: True})))
print(repr(translate("abc", {97: False})))
print([ord(ch) for ch in translate("abc", {97: 1114111})])

# The table is keyed by ordinal, so a character key never matches.
print(repr(translate("abc", {"a": "X"})))
print(repr(translate("abc", maketrans({"a": "X"}))))

# translate rejects an out-of-range int and a value of the wrong type.
print(_err("toobig", lambda: translate("abc", {97: 1114112})))
print(_err("negative", lambda: translate("abc", {97: -1})))
print(_err("float", lambda: translate("abc", {97: 1.5})))
print(_err("list", lambda: translate("abc", {97: []})))
print(_err("nested", lambda: translate("abc", {97: {}})))

# Non-ASCII on both sides of the table.
print(repr(translate("café", maketrans("é", "e"))))
print(repr(translate("naïve", maketrans("ï", "i"))))
print(repr(translate("abc", {97: "å", 98: "ä", 99: "ö"})))
print(repr(translate("åäö", maketrans("åäö", "aao"))))

# The idioms this method exists for: strip, rot-13 by table, and mask.
print(repr(translate("a1b2c3", maketrans("", "", "0123456789"))))
print(repr(translate("hello world", maketrans("abcdefghijklmnopqrstuvwxyz",
                                              "nopqrstuvwxyzabcdefghijklm"))))
print(repr(translate("secret", maketrans("secret", "******"))))
print(repr(translate("a.b.c", maketrans(".", "/"))))
print(repr(translate("  pad  ", maketrans(" ", "_"))))
