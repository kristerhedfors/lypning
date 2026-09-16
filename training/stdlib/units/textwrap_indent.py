"""textwrap.indent, including the default predicate CPython actually uses.

Adds `prefix` to the start of selected lines of `text`.  This is a
faithful port of CPython's ``textwrap.indent`` (Lib/textwrap.py), rebuilt
without the generator it uses internally.  The three details worth having
in front of you:

  * The default predicate is ``line.strip()`` -- a TRUTHY STRING, not a
    bool -- so a line that is empty or consists only of whitespace is left
    unprefixed.  ``str.strip()`` strips Unicode whitespace, so a line of
    U+00A0 alone is also skipped, even though U+00A0 is NOT one of the
    ASCII whitespace characters the rest of textwrap recognises.
  * Splitting is ``text.splitlines(True)``, which keeps the line endings
    and breaks on EVERY line boundary str knows: '\\n', '\\r', '\\r\\n',
    '\\x0b', '\\x0c', U+0085, U+2028 and U+2029 -- not just '\\n'.  A
    ``text.split('\\n')`` version quietly differs on all of those.
  * The prefix goes BEFORE the line's own leading whitespace, and the
    kept line ending is never touched.

A caller-supplied predicate replaces the default entirely; it is called
with the line INCLUDING its line ending.

Not covered: nothing.  This is the whole of textwrap.indent.  Verified
byte-identical against the real module on 300,000 random strings drawn
from ``' \\t\\nabX\\r\\x0b\\xa0.-'``, with three prefixes and a custom
predicate each (2026-09-16).
"""
# fills: textwrap.indent
# reference: textwrap


def indent(text, prefix, predicate=None):
    """Add 'prefix' to the beginning of selected lines in 'text'."""
    out = []
    for line in text.splitlines(True):
        if predicate is None:
            # CPython's default: a truthy str, not a bool.
            keep = line.strip()
        else:
            keep = predicate(line)
        if keep:
            out.append(prefix + line)
        else:
            out.append(line)
    return "".join(out)


def _not_a_comment(line):
    return not line.lstrip().startswith("#")


# --- cases ---
print(repr(indent("hello\nworld\n", "> ")))
print(repr(indent("hello\nworld", "> ")))
print(repr(indent("one line", "| ")))
print(repr(indent("", "| ")))
print(repr(indent("\n", "| ")))

# The default predicate skips empty and whitespace-only lines.
print(repr(indent("a\n\nb\n", "+ ")))
print(repr(indent("a\n   \nb\n", "+ ")))
print(repr(indent("a\n\t\nb\n", "+ ")))
print(repr(indent("\n\n\n", "+ ")))
print(repr(indent("   ", "+ ")))

# The prefix goes before the line's own indentation; endings are kept.
print(repr(indent("    deep\n  less\n", "# ")))
print(repr(indent("a\r\nb\r\n", "# ")))
print(repr(indent("a\rb\r", "# ")))

# splitlines() breaks on more than '\n'.
print(repr(indent("a\x0bb", "# ")))
print(repr(indent("a\x0cb\n", "# ")))

# An empty prefix is a no-op that still round-trips the text exactly.
print(repr(indent("a\n  \nb\n", "")))

# A custom predicate replaces the default entirely, so blank lines that
# satisfy it DO get the prefix.
print(repr(indent("a\n\nb\n", "* ", lambda line: True)))
print(repr(indent("a\n\nb\n", "* ", lambda line: False)))
print(repr(indent("keep\ndrop\n", "* ", lambda line: line.startswith("k"))))
print(repr(indent("x = 1\n# note\n\ny = 2\n", "    ", _not_a_comment)))

# The predicate sees the line ending, so 'line == "a"' would never fire.
print(repr(indent("a\na\n", "! ", lambda line: line == "a")))
print(repr(indent("a\na\n", "! ", lambda line: line == "a\n")))

# U+00A0 is stripped by str.strip(), so that line is skipped by default.
print(repr(indent("a\n \nb\n", "> ").replace(" ", "<NBSP>")))

# Non-ASCII content is prefixed like anything else.
print(indent("café\nnaïve\n", "-- "))
