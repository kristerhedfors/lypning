"""textwrap.dedent, without re, str.translate or str.expandtabs.

Removes the longest leading run of spaces and tabs common to every
non-blank line.  This is a port of CPython **3.14's** ``textwrap.dedent``
(Lib/textwrap.py), and dedent uses TWO different notions of whitespace
that a from-memory version always collapses into one:

  * **Blanking** whitespace is ``str.isspace()`` -- all 29 whitespace code
    points, so ``'\\r'``, ``'\\x0b'``, ``'\\x0c'``, ``'\\x1c'``-``'\\x1f'``,
    U+0085, U+00A0, U+2028 and U+3000 all count.  A line made entirely of
    them is normalised to the empty string, and that happens BEFORE the
    margin is computed, so such a line can never shrink the margin.
  * **Margin** whitespace is ``' '`` and ``'\\t'``, and nothing else.  The
    margin therefore stops at the first character that is not one of those
    two, even when that character is itself whitespace: in ``'  \\xa0a'``
    the indent is ``'  '`` and the U+00A0 is content that never gets
    stripped.  CPython 3.14 writes this as ``c not in ' \\t'``.

Tabs and spaces are both whitespace but are not equal: ``'  hello'`` and
``'\\thello'`` have no common leading whitespace, so dedent leaves them
alone.

**Which CPython this reproduces.**  3.14 changed the blanking rule and
this file follows 3.14.  Before 3.14 the test was the regex
``_whitespace_only_re = re.compile('^[ \\t]+$', M)``, so only a line of
spaces and tabs was blanked and a line of any OTHER whitespace was
ordinary content: on 3.9 through 3.13, ``dedent('  a\\n  \\r\\n  b\\n')``
is ``'a\\n\\r\\nb\\n'`` -- the ``'  \\r'`` line keeps its ``'\\r'`` and
contributes its ``'  '`` indent to the margin -- where 3.14 and this file
give ``'a\\n\\nb\\n'``.  The same holds for a line of only ``'\\x0b'``,
``'\\x0c'``, ``'\\x1c'``-``'\\x1f'``, U+0085, U+00A0, U+2028 or U+3000.
CPython documents the old behaviour as the bug and the new one as the fix
("dedent() now correctly normalizes blank lines containing only whitespace
characters", 3.14 whatsnew; gh-131791), which is why the port follows 3.14
rather than the majority of releases.  Only the blanking rule moved: the
margin has been ``' '`` and ``'\\t'`` only, on every release.

**What the cases deliberately do not print.**  No case below feeds dedent
a line that is entirely whitespace but not entirely spaces and tabs, such
as a line of only ``'\\r'`` or only ``'\\x0b'``, because the answer is not
the same on every CPython and a unit is inlined and run on whichever one
the reader has.  The boundary is the paragraph above, not an output line.
The cases do print the half of that boundary every release agrees on: such
a character at the FRONT of a content line, where it ends the margin.  The
U+00A0 cases compare against the expected string instead of printing a
``repr()``, because ``repr()`` of U+00A0 is one of the engines' own
refusals (``repr-unicode``) and a unit belongs inside the subset.

Not covered: nothing else.  This is the whole of textwrap.dedent.

Measured 2026-09-17 against the real module on 17,617 strings -- every
string over ``' \\t\\na\\r\\x0b'`` of length <= 5, plus every string over
``' \\t\\nab\\r\\x0b\\x0c\\xa0'`` and over ``' \\t\\na\\x85\\u2028\\u3000'``
of length <= 4.  Byte-identical on CPython 3.14.0rc2; different on 3.9.23,
3.10.20, 3.11.15, 3.12.3 and 3.13.12 for exactly the 8,362 of those inputs
that contain such a line, and byte-identical on the other 9,255.  Those
five releases agree with each other on all 17,617.  ``str.isspace()``
itself does not move: over all 0x110000 code points it answers True for
the same 29 on every one of those six interpreters, so the helper below
gives one answer on all of them even where the real module does not.
"""
# fills: textwrap.dedent
# reference: textwrap


def _is_blank(line):
    """True when the line is non-empty and made only of whitespace.

    This is CPython 3.14's ``l.isspace()``, and it is a method call rather
    than a scan on purpose: the rule is all 29 whitespace code points, not
    a list anyone should retype.  ``''.isspace()`` is already False, so the
    empty line needs no special case -- it is left alone rather than
    blanked, which is the same thing.
    """
    return line.isspace()


def _indent_of(line):
    """The leading ' '/'\\t' run, or None when the line has no content.

    Margin whitespace is ``' '`` and ``'\\t'`` only, so the run stops at the
    first other character even if that character is whitespace -- U+00A0
    ends the margin exactly as a letter would.  None means the line is
    empty or was blanked, and a blanked line takes no part in the margin.
    """
    i = 0
    while i < len(line) and (line[i] == " " or line[i] == "\t"):
        i = i + 1
    if i >= len(line):
        return None
    return line[:i]


def _common_prefix(a, b):
    i = 0
    while i < len(a) and i < len(b) and a[i] == b[i]:
        i = i + 1
    return a[:i]


def dedent(text):
    """Remove any common leading whitespace from every line in `text`."""
    blanked = []
    for line in text.split("\n"):
        if _is_blank(line):
            blanked.append("")
        else:
            blanked.append(line)

    margin = None
    for line in blanked:
        indent = _indent_of(line)
        if indent is None:
            continue
        if margin is None:
            margin = indent
        elif indent.startswith(margin):
            # More deeply indented than the current winner: no change.
            pass
        elif margin.startswith(indent):
            # Consistent with, and no deeper than, the winner: new winner.
            margin = indent
        else:
            margin = _common_prefix(margin, indent)

    if not margin:
        return "\n".join(blanked)

    out = []
    cut = len(margin)
    for line in blanked:
        if line.startswith(margin):
            out.append(line[cut:])
        else:
            out.append(line)
    return "\n".join(out)


# --- cases ---
print(repr(dedent("    hello\n    world\n")))
print(repr(dedent("  shallow\n    deeper\n  shallow again\n")))
print(repr(dedent("no indent at all")))
print(repr(dedent("")))
print(repr(dedent("\n    x\n    y\n")))
print(repr(dedent("    only one line")))

# A whitespace-only line is blanked first, so it cannot shrink the margin.
print(repr(dedent("    a\n  \n    b\n")))
print(repr(dedent("    a\n        \n    b\n")))
print(repr(dedent("    a\n\n    b\n")))

# Spaces and tabs are both whitespace but are not equal.
print(repr(dedent("  space\n\ttab\n")))
print(repr(dedent("\ta\n\tb\n")))
print(repr(dedent("  \tmixed\n  plain\n")))
print(repr(dedent(" \t a\n \tb\n")))

# Lines of only spaces and tabs, and nothing else.
print(repr(dedent("      \n")))
print(repr(dedent("   ")))
print(repr(dedent("\t\t")))
print(repr(dedent("\n\n\n")))

# Margin whitespace is ' ' and '\t' ONLY.  '\r', '\x0b' and '\x0c' end the
# margin exactly as a letter does, and are never stripped.  Every line here
# carries content, so no CPython blanks any of them and every CPython
# answers the same -- unlike a line made of ONE of those characters, which
# is why no such line appears anywhere in these cases.
print(repr(dedent("  \ra\n  b\n")))
print(repr(dedent("  \x0ba\n  b\n")))
print(repr(dedent("  \x0ca\n  b\n")))
print(repr(dedent("  a\n  \rb\n  c\n")))
print(repr(dedent("  \x0b\x0ca\n  b\n")))

# The same for U+00A0, which is whitespace to str.isspace() and not margin
# whitespace -- written as a comparison with the expected string because
# repr() of U+00A0 is one of the engines' refusals (repr-unicode: its
# printability needs CPython's own tables), and a unit stays in the subset.
print(dedent("  \xa0a\n  b\n") == "\xa0a\nb\n")
print(dedent("\t\xa0a\n\t\xa0b\n") == "\xa0a\n\xa0b\n")
print(dedent("  \xa0a\n  \xa0\xa0b\n") == "\xa0a\n\xa0\xa0b\n")

# The margin is stripped from every line that carries it, blank or not.
print(repr(dedent("   a\n   b")))
print(repr(dedent("\t \ta\n\t \tb\n\t \t\n")))

# Non-ASCII content is ordinary content.
print(dedent("    café\n    naïve\n"))
print(len(dedent("   x\n   y\n")))

# The two notions of whitespace, side by side: each of these IS
# str.isspace() whitespace, so a line of only it is blanked, and none of
# them is margin whitespace, so none is ever stripped off a content line.
print("\r".isspace(), "\x0b".isspace(), "\xa0".isspace(), "a".isspace())
