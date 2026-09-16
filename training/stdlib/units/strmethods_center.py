"""str.center and str.expandtabs, which both refuse on the engines.

Measured 2026-09-16::

    $ lypning-l -c 'print("x".center(5))'
    lypning-l: unsupported: str-method: str.center()
    $ lypning-l -c 'print("a\\tb".expandtabs(4))'
    lypning-l: unsupported: str-method: str.expandtabs()

Both are language-level holes rather than a missing module, so there is no
``# reference:`` module to import: the differential runs the real METHODS
under CPython on the same inputs.

``center`` is a transcription of CPython's ``unicode_center`` in
Objects/unicodeobject.c, including the one line everybody writes from
memory and gets wrong::

    marg = width - len(s)
    left = marg // 2 + (marg & width & 1)

That ``& width & 1`` is what makes the padding lean LEFT for an odd margin
on an odd width and RIGHT otherwise, so ``'ab'.center(5)`` is ``'  ab '``
and ``'abc'.center(6)`` is ``' abc  '``.  A plain ``marg // 2`` gets the
first of those wrong.  A margin of zero or less returns the string
unchanged, so a negative `width` is not an error.

``expandtabs`` is CPython's ``unicode_expandtabs``.  It tracks a COLUMN,
not an index: a tab advances to the next multiple of `tabsize`, and the
column resets to zero after ``'\\n'`` or ``'\\r'`` -- but after nothing
else, so ``'\\x0b'`` and ``'\\x0c'`` are ordinary one-column characters
here even though ``str.split()`` treats them as whitespace.  A `tabsize`
of zero or less deletes each tab and advances nothing.  Every character
counts as exactly one column, so a combining mark widens the line.

Both raise the same exceptions CPython raises, with the same messages:
``center`` with a fill that is not exactly one character is a
``TypeError``, which the engines do run.

str.zfill is deliberately NOT here.  It is already served on both engines,
negatives and all -- ``lypning -c 'print("-5".zfill(5))'`` prints
``-0005`` and exits 0, measured 2026-09-16 -- so a reimplementation would
be dead weight.  str.ljust and str.rjust are served too.

Not covered: a non-integer `width` or `tabsize` (CPython's TypeError comes
from argument conversion, not from this code), and the OverflowError
CPython raises when an expanded line would exceed the maximum string size.
"""
# fills: str.center, str.expandtabs
# reference: -


def center(s, width, fillchar=" "):
    """``s.center(width, fillchar)``."""
    if len(fillchar) != 1:
        raise TypeError("The fill character must be exactly one character long")
    marg = width - len(s)
    if marg <= 0:
        return s
    # CPython: left = marg / 2 + (marg & width & 1).  Both operands are
    # non-negative here, so C truncation and Python flooring agree.
    left = marg // 2 + (marg & width & 1)
    return fillchar * left + s + fillchar * (marg - left)


def expandtabs(s, tabsize=8):
    """``s.expandtabs(tabsize)``."""
    out = []
    col = 0
    for ch in s:
        if ch == "\t":
            if tabsize > 0:
                incr = tabsize - col % tabsize
                col = col + incr
                out.append(" " * incr)
        else:
            col = col + 1
            out.append(ch)
            if ch == "\n" or ch == "\r":
                col = 0
    return "".join(out)


def _center_error(s, width, fillchar):
    """The exception type and message ``center`` raises, as a string."""
    try:
        center(s, width, fillchar)
    except TypeError as exc:
        return "TypeError: " + str(exc)
    return "no error"


# --- cases ---
# The margin split, at every width, on an even- and an odd-length string.
print([center("ab", w) for w in range(0, 9)])
print([center("abc", w) for w in range(0, 9)])
print([center("", w, "-") for w in range(0, 6)])
print([center("x", w, "*") for w in range(0, 6)])

# A margin of zero or less returns the string unchanged.
print(repr(center("hello", 5)))
print(repr(center("hello", 4)))
print(repr(center("hello", 0)))
print(repr(center("hello", -3)))
print(repr(center("", 0)))
print(repr(center("", -1, "=")))

# A non-space fill, and a fill that is itself whitespace.
print(repr(center("ab", 7, "0")))
print(repr(center("ab", 7, "\t")))
print(repr(center("ab", 7, "·")))
print(repr(center("é", 5, "·")))

# The fill must be exactly one character.
print(_center_error("x", 5, ""))
print(_center_error("x", 5, "ab"))
print(_center_error("x", 5, "  "))
print(_center_error("x", 1, ""))

# expandtabs: the classic column table.
print(repr(expandtabs("01\t012\t0123\t01234")))
print(repr(expandtabs("01\t012\t0123\t01234", 4)))
print(repr(expandtabs("01\t012\t0123\t01234", 8)))
print(repr(expandtabs("a\tb", 1)))
print(repr(expandtabs("a\tb", 2)))
print(repr(expandtabs("a\tb", 3)))

# A tab at column 0, and consecutive tabs.
print(repr(expandtabs("\t")))
print(repr(expandtabs("\tx", 4)))
print(repr(expandtabs("ab\t\tc", 4)))
print(repr(expandtabs("\t\t\t", 2)))

# tabsize <= 0 deletes the tab and advances nothing.
print(repr(expandtabs("a\tb", 0)))
print(repr(expandtabs("a\tb", -2)))
print(repr(expandtabs("\t\t", 0)))

# The column resets after '\n' and after '\r', and after nothing else.
print(repr(expandtabs("a\tb\nc\td", 4)))
print(repr(expandtabs("a\tb\rc\td", 4)))
print(repr(expandtabs("a\tb\x0bc\td", 4)))
print(repr(expandtabs("a\tb\x0cc\td", 4)))
print(repr(expandtabs("\r\n\t", 4)))
print(repr(expandtabs("abc\n\t", 4)))
print(repr(expandtabs("abcd\n\tx", 4)))

# Strings with no tab at all come back unchanged.
print(repr(expandtabs("")))
print(repr(expandtabs("plain")))
print(repr(expandtabs("line one\nline two")))

# Every character is one column, combining marks and wide glyphs included.
print(repr(expandtabs("café\tx", 4)))
print([ord(ch) for ch in expandtabs("é\tx", 4)])
print(repr(expandtabs("中文\tx", 4)))

# center and expandtabs compose the way the real methods do.
print(repr(center(expandtabs("a\tb", 4), 11, ".")))
print(repr(expandtabs(center("x", 5, "\t"), 4)))
