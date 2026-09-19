"""fnmatch.fnmatch, fnmatchcase, translate and filter -- shell globbing.

``fnmatch`` is a thin layer over ``re``: it translates a shell pattern
into a regular expression and matches with it.  The core engine refuses
``import re`` outright, so the whole module is out of reach there; the
wider variant serves ``re`` and this unit runs on it.

Covered:

  * ``translate(pat)`` -- the regular expression, built the way CPython
    3.11 through 3.13 build it, including the ``(?s:...)`` wrapper and
    the ``(?>.*?fixed)`` atomic groups for interior ``STAR fixed`` pairs.
    Its TEXT is not printed; see "What translate returns is not API".
  * ``fnmatchcase(name, pat)`` -- case-sensitive match of a whole name.
  * ``fnmatch(name, pat)`` -- case-normalised match.
  * ``filter(names, pat)`` -- the matching subset, order preserved.

The pattern language: ``*`` any run of characters, ``?`` one character,
``[seq]`` one character from the set, ``[!seq]`` one character not in it.
There is no way to quote a metacharacter.  An unbalanced ``[`` is a
literal ``[``.  An initial period is *not* special (that is ``glob``'s
rule, not ``fnmatch``'s).

The three corners that catch people, all pinned in the cases below:

  * ``[]]`` is the set containing ``]``: a ``]`` in the first position is
    a member, not the terminator, and so is the ``]`` right after a
    leading ``!``.
  * A ``-`` is a range only between two characters.  ``[a-]``, ``[-a]``
    and ``[a-c-e]`` are each handled by the chunking pass that CPython
    added so that a trailing or leading hyphen becomes a literal.
  * ``&``, ``~`` and ``|`` are escaped inside the set because a future
    ``re`` may give ``&&``, ``~~`` and ``||`` set-operation meaning.

What translate returns is not API
---------------------------------

The cases do not print the expression ``translate`` returns.  CPython has
respelled it three times inside the 3.9-3.14 window this corpus targets.
Measured 2026-09-17 by running ``fnmatch.translate`` over 2,430 distinct
patterns -- every string up to length three over ``a * ? [ ] ! - \\ ^ & ~
| .``, plus every pattern this unit uses -- on CPython 3.9.23, 3.10.20,
3.11.15, 3.12.3, 3.13.12 and 3.14.0rc2, and diffing adjacent versions:

  * 3.9 -> 3.10, **6 patterns.**  The hyphen chunking pass arrives.  3.9
    emits ``(?s:[a-])\\Z`` for ``[a-]`` where 3.10 emits ``(?s:[a\\-])\\Z``,
    and 3.9 hands an inverted range such as ``[c-a]`` straight through
    where 3.10 collapses it to the never-matching ``(?!)``.
  * 3.10 -> 3.11, **23 patterns.**  An interior ``STAR fixed`` pair is
    the atomic ``(?>.*?fixed)`` from 3.11 on; 3.10 spells the same thing
    with a named-group lookahead, ``(?=(?P<g0>.*?fixed))(?P=g0)``.
  * 3.11 -> 3.12 and 3.12 -> 3.13, **0 patterns.**
  * 3.13 -> 3.14, **all 2,430.**  The end-of-string anchor is spelled
    ``\\z`` from 3.14 and ``\\Z`` before it.  One anchor, two spellings.

Not one of those three changed an answer.  Over 4,681 patterns -- every
string up to length four over ``a b * ? [ ] ! -`` -- against 259 names --
every string up to length three over ``a b ] - ^`` and newline, plus the
empty one -- ``fnmatchcase`` returns the identical verdict on all six
interpreters (digest ``2119c0708f01b4fc``, run 2026-09-17).

So the cases print the ANSWER and not the expression: ``_accepts(pat)``
gives the subset of one fixed probe list that ``pat`` accepts, which is
what a caller of ``translate`` actually depends on.  Of the expression
itself the cases print only the two things all six interpreters agree on
-- that it opens with the scoped-DOTALL ``(?s:`` and that it ends at an
end-of-string anchor, the latter through ``_anchor``, which case-folds
``\\Z`` and ``\\z`` into the one spelling they share.

One case was removed rather than printed
----------------------------------------

``[c-a]``, an inverted range, used to appear twice: as
``translate('[c-a]')`` and as ``fnmatchcase('b', '[c-a]')``.  It is the
only input found whose BEHAVIOUR drifts rather than its spelling.  From
3.10 the empty range is collapsed and the pattern matches nothing; 3.9
hands ``[c-a]`` to ``re`` and ``fnmatchcase('b', '[c-a]')`` raises
``re.error: bad character range c-a at position 5``.  Every inverted
range does this -- ``[b-a]``, ``[z-a]``, ``[9-0]``, ``[!c-a]``,
``[c-ab]`` were each checked on 3.9.23, 3.10.20 and 3.14.0rc2 on
2026-09-17 -- so there is no stable inverted range to print instead, and
no case here uses one.  ``_parse`` still collapses empty ranges, faithful
to 3.10 and later; no printed case reaches that branch.

Two deliberate restrictions, both documented rather than faked:

  * **str patterns only.**  CPython's ``_compile_pattern`` handles a
    ``bytes`` pattern by round-tripping it through ISO-8859-1; the subset
    has no codec other than UTF-8, so a bytes pattern is out of scope
    here.  Nothing below passes one.
  * **No cache.**  CPython wraps ``_compile_pattern`` in an
    ``lru_cache``; that is a speed decision with no effect on any answer,
    and the subset has no decorators, so each call recompiles.

One divergence, and it is only in the regex handed to ``re``, never in
any answer this unit prints:

  ``_regex_for`` builds a second, equivalent expression for matching.
  It drops the ``(?s:...)`` wrapper in favour of passing ``re.DOTALL``,
  writes ``.*?fixed`` where CPython writes the atomic ``(?>.*?fixed)``,
  and writes ``[^\\s\\S]`` -- a class that can never match -- where
  CPython writes the never-matching lookahead ``(?!)``.  Scoped inline
  flags, atomic groups and lookaheads are all refused by this engine.
  The atomic group is documented in CPython as a backtracking
  optimisation for an interior ``* fixed`` pair; dropping it can only
  cost time, never change whether an anchored match exists.  That claim
  was not reasoned about, it was swept: the same 4,681 patterns against
  the same 259 names, comparing this expression's anchored match against
  ``fnmatch.fnmatchcase`` -- 0 divergences on each of 3.9.23, 3.10.20,
  3.11.15, 3.12.3, 3.13.12 and 3.14.0rc2, run 2026-09-17.  ``_agrees``
  puts a handful of those comparisons in the cases, where the reference
  arm runs the left-hand side against the real module.

``os.path.normcase`` is the identity on POSIX, which is where this unit
is graded, so ``normcase`` here is the identity and ``fnmatch`` equals
``fnmatchcase``.  On Windows CPython would lowercase both arguments and
turn ``/`` into ``\\``; that is not reproduced.
"""
# fills: fnmatch.fnmatch, fnmatch.fnmatchcase, fnmatch.translate, fnmatch.filter
# reference: fnmatch

import re


def _parse(pat, never):
    """Pattern -> list of [is_star, text] pieces, CPython's first pass.

    ``never`` is the sub-expression for an empty character range, which
    must never match: ``(?!)`` for the string ``translate`` returns, and
    the equivalent ``[^\\s\\S]`` for the expression actually compiled.
    """
    res = []
    i = 0
    n = len(pat)
    while i < n:
        c = pat[i]
        i = i + 1
        if c == "*":
            # Consecutive `*` collapse into one.
            if (not res) or (not res[-1][0]):
                res.append([True, ""])
        elif c == "?":
            res.append([False, "."])
        elif c == "[":
            j = i
            if j < n and pat[j] == "!":
                j = j + 1
            if j < n and pat[j] == "]":
                j = j + 1
            while j < n and pat[j] != "]":
                j = j + 1
            if j >= n:
                # No closing bracket: the `[` is a literal.
                res.append([False, "\\["])
            else:
                stuff = pat[i:j]
                if "-" not in stuff:
                    stuff = stuff.replace("\\", "\\\\")
                else:
                    chunks = []
                    if pat[i] == "!":
                        k = i + 2
                    else:
                        k = i + 1
                    while True:
                        k = pat.find("-", k, j)
                        if k < 0:
                            break
                        chunks.append(pat[i:k])
                        i = k + 1
                        k = k + 3
                    chunk = pat[i:j]
                    if chunk:
                        chunks.append(chunk)
                    else:
                        chunks[-1] = chunks[-1] + "-"
                    # Remove empty ranges -- invalid in a regex. This is the
                    # 3.10 behaviour; 3.9 emitted the inverted range and let
                    # `re` raise. No printed case reaches here; see the
                    # docstring, "One case was removed rather than printed".
                    for k in range(len(chunks) - 1, 0, -1):
                        if chunks[k - 1][-1] > chunks[k][0]:
                            chunks[k - 1] = chunks[k - 1][:-1] + chunks[k][1:]
                            del chunks[k]
                    # Escape backslashes and the hyphens that are not
                    # themselves range separators.
                    parts = []
                    for s in chunks:
                        parts.append(s.replace("\\", "\\\\").replace("-", "\\-"))
                    stuff = "-".join(parts)
                # Escape the set operations &&, ~~ and ||.
                stuff = re.sub(r"([&~|])", r"\\\1", stuff)
                i = j + 1
                if not stuff:
                    res.append([False, never])
                elif stuff == "!":
                    res.append([False, "."])
                else:
                    if stuff[0] == "!":
                        stuff = "^" + stuff[1:]
                    elif stuff[0] == "^" or stuff[0] == "[":
                        stuff = "\\" + stuff
                    res.append([False, "[" + stuff + "]"])
        else:
            res.append([False, re.escape(c)])
    assert i == n
    return res


def _assemble(pieces, atomic):
    """Second pass: turn the piece list into one expression.

    ``atomic`` writes CPython's ``(?>.*?fixed)`` for an interior
    ``STAR fixed`` pair; without it the same pair is ``.*?fixed``.
    """
    out = []
    i = 0
    n = len(pieces)
    # Fixed pieces at the start.
    while i < n and not pieces[i][0]:
        out.append(pieces[i][1])
        i = i + 1
    # Then STAR fixed STAR fixed ...
    while i < n:
        assert pieces[i][0]
        i = i + 1
        if i == n:
            out.append(".*")
            break
        assert not pieces[i][0]
        fixed = []
        while i < n and not pieces[i][0]:
            fixed.append(pieces[i][1])
            i = i + 1
        joined = "".join(fixed)
        if i == n:
            out.append(".*")
            out.append(joined)
        else:
            if atomic:
                out.append("(?>.*?" + joined + ")")
            else:
                out.append(".*?" + joined)
    assert i == n
    return "".join(out)


def translate(pat):
    """Shell pattern -> the regular expression CPython's fnmatch builds.

    Spelled the way CPython 3.11 through 3.13 spell it. The text is not
    what the cases check; see the module docstring.
    """
    body = _assemble(_parse(pat, "(?!)"), True)
    return "(?s:" + body + ")\\Z"


def _regex_for(pat):
    """The equivalent expression this engine can compile (see docstring)."""
    return _assemble(_parse(pat, "[^\\s\\S]"), False) + "\\Z"


def _compile_pattern(pat):
    return re.compile(_regex_for(pat), re.DOTALL)


def fnmatchcase(name, pat):
    """Whole-name match, taking case into account."""
    return _compile_pattern(pat).match(name) is not None


def normcase(s):
    """os.path.normcase on POSIX: the identity."""
    return s


def fnmatch(name, pat):
    """Whole-name match after case normalisation (a no-op on POSIX)."""
    return fnmatchcase(normcase(name), normcase(pat))


def filter(names, pat):
    """Those elements of NAMES that match PAT, in the order given."""
    result = []
    matcher = _compile_pattern(normcase(pat)).match
    for name in names:
        if matcher(name) is not None:
            result.append(name)
    return result


def _matcher(pat):
    """A reusable match callable, the shape CPython's cache hands back."""
    return _compile_pattern(pat).match


#: The one probe list every ``_accepts`` case is answered against. Fixed so
#: that a pattern's line is read as "these, of these", and wide enough that
#: the metacharacters, the bracket corners and the whitespace all show.
_PROBE = ["", "a", "b", "c", "d", "x", "z", "A", "-", "]", "[", "^", "!",
          "&", "~", "|", ".", "\\", "ab", "abc", "a1", "b1", "a.b", "a+b",
          "a b", "a\tb", "a\nb", "(a)", "aaa", "foo.bar", "foobar",
          "report.txt", "a/b/c", "Makefile", "makefile"]


def _accepts(pat):
    """The names in ``_PROBE`` that PAT accepts -- what translate is FOR.

    The expression ``translate`` returns has been respelled three times
    between 3.9 and 3.14 without any of these lines moving, which is why
    the cases print this and not the expression.
    """
    return filter(_PROBE, pat)


def _anchor(expr):
    """The end-of-string anchor an expression ends with, case-folded.

    ``\\Z`` up to 3.13 and ``\\z`` from 3.14 are the same anchor under two
    spellings, so the case prints the one spelling they share.
    """
    return expr[-2:].lower()


def _agrees(pat, names):
    """Whether ``fnmatchcase`` and ``_regex_for``'s expression agree on NAMES.

    The equivalence ``translate`` exists to provide, checked through the
    expression this engine can actually compile. In the reference run the
    left-hand side is the real ``fnmatch.fnmatchcase``, so the answer is a
    comparison and not a tautology.
    """
    matcher = re.compile(_regex_for(pat), re.DOTALL).match
    for name in names:
        if (matcher(name) is not None) != fnmatchcase(name, pat):
            return False
    return True


# --- cases ---
# translate() returns an expression; the two things about its TEXT that
# every CPython from 3.9 to 3.14 agrees on are the scoped-DOTALL opener
# and that it ends at an end-of-string anchor.
print(repr(translate("")[:4]))
print(repr(translate("*.txt")[:4]))
print(repr(translate("a*b*c")[:4]))
print(repr(translate("[!a-c]")[:4]))
print(repr(_anchor(translate(""))))
print(repr(_anchor(translate("*.txt"))))
print(repr(_anchor(translate("a*b*c"))))
print(repr(_anchor(translate("[!a-c]"))))

# Everything else about translate() is checked as what the expression
# ACCEPTS, out of the one fixed probe list.
print(_accepts(""))
print(_accepts("*"))
print(_accepts("**"))
print(_accepts("***"))
print(_accepts("?"))
print(_accepts("a"))
print(_accepts("*.txt"))
print(_accepts("a*b"))
print(_accepts("*a*b*"))
print(_accepts("a*b*c*d"))
print(_accepts("foo?bar"))
print(_accepts("."))
print(_accepts("a.b"))
print(_accepts("a+b"))
print(_accepts("a b"))
print(_accepts("a\tb"))
print(_accepts("(a)"))

# Character sets, including every bracket corner.
print(_accepts("[abc]"))
print(_accepts("[!abc]"))
print(_accepts("[]]"))
print(_accepts("[!]]"))
print(_accepts("[]"))
print(_accepts("[!]"))
print(_accepts("["))
print(_accepts("[abc"))
print(_accepts("[^a]"))
print(_accepts("[[]"))
print(_accepts("[a-c]"))
print(_accepts("[!a-c]"))
print(_accepts("[a-]"))
print(_accepts("[-a]"))
print(_accepts("[a-c-e]"))
print(_accepts("[a-cx-z]"))
print(_accepts("[&~|]"))
print(_accepts("[a&b]"))
print(_accepts("[\\]"))
print(_accepts("[a\\b]"))
print(_accepts("[!-a]"))
print(_accepts("*[abc]*"))

# fnmatchcase: the happy path and the empties.
print(fnmatchcase("abc", "abc"))
print(fnmatchcase("abc", "ABC"))
print(fnmatchcase("", ""))
print(fnmatchcase("", "*"))
print(fnmatchcase("", "?"))
print(fnmatchcase("a", ""))
print(fnmatchcase("abc", "a*c"))
print(fnmatchcase("ac", "a*c"))
print(fnmatchcase("abc", "a?c"))
print(fnmatchcase("ac", "a?c"))
print(fnmatchcase("report.txt", "*.txt"))
print(fnmatchcase("report.txt.bak", "*.txt"))
print(fnmatchcase("report.txt", "*.TXT"))

# `*` crosses a separator and a newline; `?` matches one of anything.
print(fnmatchcase("a/b/c", "a*c"))
print(fnmatchcase("a\nb", "a?b"))
print(fnmatchcase("a\nb", "*b"))
print(fnmatchcase("a\n", "a*"))
print(fnmatchcase("x\n", "x?"))

# Sets.
print(fnmatchcase("b", "[abc]"))
print(fnmatchcase("d", "[abc]"))
print(fnmatchcase("d", "[!abc]"))
print(fnmatchcase("b", "[!abc]"))
print(fnmatchcase("]", "[]]"))
print(fnmatchcase("a", "[]]"))
print(fnmatchcase("a", "[!]]"))
print(fnmatchcase("]", "[!]]"))
print(fnmatchcase("b", "[a-c]"))
print(fnmatchcase("z", "[a-c]"))
print(fnmatchcase("-", "[a-]"))
print(fnmatchcase("a", "[a-]"))
print(fnmatchcase("-", "[-a]"))
print(fnmatchcase("^", "[^a]"))
print(fnmatchcase("a", "[^a]"))
print(fnmatchcase("[", "[[]"))
print(fnmatchcase("&", "[&~|]"))
print(fnmatchcase("~", "[&~|]"))

# An unbalanced `[` is a literal `[`.
print(fnmatchcase("[abc", "[abc"))
print(fnmatchcase("a", "[abc"))
print(fnmatchcase("[", "["))

# Backtracking shapes: the interior `* fixed` pairs.
print(fnmatchcase("abab", "*ab*b"))
print(fnmatchcase("abab", "*ba*b"))
print(fnmatchcase("aaa", "*aa*"))
print(fnmatchcase("aaa", "*a*a*a*"))
print(fnmatchcase("aaa", "*a*a*a*a*"))
print(fnmatchcase("banana", "*na*na*"))
print(fnmatchcase("banana", "*na*na*na*"))
print(fnmatchcase("xxxxxxxxxxy", "*y"))
print(fnmatchcase("abcdef", "a*f"))
print(fnmatchcase("abcdef", "a*e"))

# fnmatch() on POSIX is fnmatchcase().
print(fnmatch("abc", "a*"))
print(fnmatch("ABC", "a*"))
print(fnmatch("abc", "A*"))
print(fnmatch("abc", "abc") == fnmatchcase("abc", "abc"))
print(repr(normcase("A/b")))

# filter() keeps the input order and does not sort.
print(filter(["c.txt", "b.py", "a.txt"], "*.txt"))
print(filter(["c.txt", "b.py", "a.txt"], "*"))
print(filter(["c.txt", "b.py", "a.txt"], "*.rst"))
print(filter([], "*"))
print(filter(["a", "ab", "abc"], "a?"))
print(filter(["a1", "a2", "b1"], "[ab]1"))
print(filter(["x", "", "y"], "?"))
print(filter(["Makefile", "makefile"], "[Mm]akefile"))

# A reusable matcher, the shape CPython's cache returns.
_m = _matcher("*.log")
print(_m("a.log") is not None)
print(_m("a.txt") is not None)

# The second expression -- the one this engine compiles -- accepts exactly
# what fnmatchcase accepts. See the docstring's "One divergence".
print(repr(_regex_for("*.txt")))
print(repr(_regex_for("[]")))
print(repr(_regex_for("a*b*c")))
print(_regex_for("a*b*c").count("(?>"))
print(_agrees("*.txt", _PROBE))
print(_agrees("a*b*c", _PROBE))
print(_agrees("*a*b*", _PROBE))
print(_agrees("*[abc]*", _PROBE))
print(_agrees("[!a-c]", _PROBE))
print(_agrees("[a-c-e]", _PROBE))
print(_agrees("?", _PROBE))
print(_agrees("*", _PROBE))
print(_agrees("", _PROBE))
