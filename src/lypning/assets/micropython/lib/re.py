# lypning-mp frozen shim: CPython-shaped `re` over MicroPython's re1.5 engine
# (aliased as `_re` by the variant). Adds findall/finditer/split/escape,
# named groups, the I/M/S/X/A flags, CPython sub() semantics, and rewrites the
# constructs re1.5 would silently mis-compile. See micropython/lib/README.md.
import ure as _re

NOFLAG = 0
T = TEMPLATE = 1
I = IGNORECASE = 2
L = LOCALE = 4
M = MULTILINE = 8
S = DOTALL = 16
U = UNICODE = 32
X = VERBOSE = 64
A = ASCII = 256
DEBUG = 128


class error(Exception):
    pass


def _unsupported(tok):
    raise NotImplementedError("lypning-mp: unsupported: argument: re(" + tok + ")")


# The flags this shim actually implements. U/UNICODE is CPython 3's default and
# a no-op here, so accepting it is honest rather than lenient. X and A are done
# as pattern rewrites (_strip_verbose and the \w branches of _prep), which is
# why they are here and L/DEBUG/TEMPLATE are not: nothing in this file could
# honour those, so they are named back to the caller instead.
_FLAGS_OK = I | M | S | U | X | A
_FLAG_NAMES = ((L, "LOCALE"), (DEBUG, "DEBUG"), (T, "TEMPLATE"))


def _check_flags(flags):
    """Refuse a flag we do not implement, instead of ignoring it.

    Declaring a constant and then not honouring it is the worst failure this
    project has: re.VERBOSE used to return [] where CPython returned ['1','22'],
    at exit 0, with nothing on stderr. An agent cannot notice that. Exit 90 with
    a named flag is recoverable; a confidently wrong answer is not.
    """
    rest = flags & ~_FLAGS_OK
    if rest:
        for bit, name in _FLAG_NAMES:
            if rest & bit:
                _unsupported(name)
        _unsupported("flags=" + str(rest))


def _swapcase(c):
    return c.upper() if c.islower() else c.lower()


def _fold_class(cls):
    # [a-z] -> [A-Za-z]; also correct for negated classes. The extra members
    # go straight after the '[' (or '[^') so they can never form a range with
    # what follows.
    head = 2 if cls[1:2] == "^" else 1
    body = cls[head:-1]
    add = []
    k = 0
    n = len(body)
    while k < n:
        c = body[k]
        if c == "\\":
            k += 2
            continue
        if body[k + 1 : k + 2] == "-" and k + 2 < n:
            e = body[k + 2]
            if c.isalpha() and e.isalpha():
                add.append(_swapcase(c) + "-" + _swapcase(e))
            k += 3
            continue
        if c.isalpha():
            add.append(_swapcase(c))
        k += 1
    return cls[:head] + "".join(add) + body + "]"


def _reps(spec):
    # "3" -> (3, 3); "2,4" -> (2, 4); "2," -> (2, None); else None
    if "," in spec:
        a, _, b = spec.partition(",")
    else:
        a, b = spec, spec
    if not a.isdigit() or (b != "" and not b.isdigit()):
        return None
    return (int(a), int(b) if b else None)


# sre_parse.WHITESPACE, verbatim -- these are the characters re.X drops.
_WS = " \t\n\r\v\f"


def _strip_verbose(pat):
    """re.VERBOSE, done as CPython does it: in the tokenizer, before parsing.

    CPython's tokenizer hands `_parse` one token at a time, where a backslash
    escape is ONE token, and `_parse` skips a token that is whitespace or
    starts a `#` comment. So the rules fall out exactly:

      * `\\ ` and `\\#` are escapes, never whitespace or a comment start;
      * a character class is opaque -- `[a b]` and `[#]` keep both;
      * `#` runs to the next newline, or to the end of the pattern.

    The one case that is not a plain deletion is `{`. CPython's repeat parser
    reads the RAW text after `{`, so `a{2, 3}` fails to parse as a repeat and
    the brace becomes a LITERAL -- and then the space inside it is skipped like
    any other. Deleting the space first would turn it into a valid `{2,3}` and
    silently repeat the atom. So a brace whose raw body is not a repeat spec is
    escaped here, which is how the decision the raw text made survives the
    rewrite. `_reps` is the same test `_prep` applies, so the two agree.
    """
    out = []
    i = 0
    n = len(pat)
    incls = False
    gap = False  # something was dropped since the last character kept
    while i < n:
        c = pat[i]
        if c == "\\":
            out.append(pat[i : i + 2])
            i += 2
            gap = False
            continue
        if incls:
            if c == "]":
                incls = False
            out.append(c)
            i += 1
            continue
        if c == "[":
            # A leading '^' and then a leading ']' are part of the class head,
            # not a terminator -- same scan as _prep's.
            incls = True
            j = i + 1
            if pat[j : j + 1] == "^":
                j += 1
            if pat[j : j + 1] == "]":
                j += 1
            out.append(pat[i:j])
            i = j
            gap = False
            continue
        if c in _WS:
            i += 1
            gap = True
            continue
        if c == "#":
            # A comment runs on ESCAPE TOKENS too, not raw characters: CPython
            # compares each token to "\n", and `\\` + newline is one token, so
            # an escaped newline does NOT end the comment. Same reason a lone
            # trailing backslash is an error there rather than a dropped
            # character -- and dropping it here would be the silent kind.
            while i < n and pat[i] != "\n":
                i += 2 if pat[i] == "\\" else 1
            if i > n:
                raise error("bad escape (end of pattern)")
            gap = True
            continue
        if c == "{":
            j = pat.find("}", i)
            if j < 0 or _reps(pat[i + 1 : j]) is None:
                out.append("\\{")
                i += 1
            else:
                out.append(pat[i : j + 1])
                i = j + 1
            gap = False
            continue
        if gap and (c == "?" or c == "+") and out and (
            out[-1] in ("*", "+", "?") or (out[-1][:1] == "{" and out[-1][-1:] == "}")
        ):
            # `*?` and `*+` are one token pair in CPython and are matched with
            # no whitespace skip between them, so `a* # c\n ?` is "multiple
            # repeat" there, not a lazy quantifier. Closing the gap here would
            # invent a pattern CPython refuses to compile -- found by fuzzing
            # 68,928 verbose patterns against CPython, and the only divergence
            # that survived it.
            raise error("multiple repeat")
        out.append(c)
        i += 1
        gap = False
    return "".join(out)


# re.ASCII narrows \w to [a-zA-Z0-9_]. It has to be done here and it can only
# be done for the POSITIVE form:
#
#   * \d and \s need nothing -- re1.5's named classes are already exactly
#     [0-9] and [ \t\n\v\f\r], which is what re.A asks for. Their negations
#     \D and \S are likewise unchanged by the flag on this engine.
#   * \w is the one the variant patched to be unicode-aware
#     (lib/re1.5/charclass.c: every byte >= 0x80 is a word constituent), so
#     re.A is the exact opposite and needs the explicit class back.
#   * \W -- and \w inside a NEGATED class, which is the same thing -- cannot be
#     done at all. re1.5 matches BYTES, so [^a-zA-Z0-9_] matches one byte of a
#     UTF-8 character where CPython matches the whole character: measured on
#     this build, searching it in "a\xe5b" raises UnicodeError from the
#     mid-codepoint span. Refused by name rather than answered approximately.
_ASCII_W = "a-zA-Z0-9_"


def _ascii_class(cls):
    out = []
    neg = cls[1:2] == "^"
    i = 0
    n = len(cls)
    while i < n:
        c = cls[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        nx = cls[i + 1 : i + 2]
        if nx == "W":
            _unsupported("\\W with re.ASCII")
        if nx == "w" and neg:
            _unsupported("[^\\w] with re.ASCII")
        out.append(_ASCII_W if nx == "w" else c + nx)
        i += 2
    return "".join(out)


def _prep(pat, flags):
    """Rewrite a CPython pattern into one re1.5 compiles with the same meaning.

    Returns (native_pattern, name->group index, starts_with_^, ends_with_$).
    """
    ic = flags & I
    asc = flags & A
    out = []
    names = {}
    gi = 0
    i = 0
    n = len(pat)
    while i < n:
        c = pat[i]
        if c == "\\":
            nx = pat[i + 1 : i + 2]
            if nx in ("b", "B", "A", "Z", "G"):
                _unsupported("\\" + nx)  # re1.5 would match these as literals
            if nx.isdigit():
                _unsupported("\\" + nx)  # backreferences are not supported
            if asc and (nx == "w" or nx == "W"):
                if nx == "W":
                    _unsupported("\\W with re.ASCII")
                out.append("[" + _ASCII_W + "]")
                i += 2
                continue
            out.append(c + nx)
            i += 2
            continue
        if c == "[":
            j = i + 1
            if pat[j : j + 1] == "^":
                j += 1
            if pat[j : j + 1] == "]":
                j += 1
            while j < n and pat[j] != "]":
                j += 2 if pat[j] == "\\" else 1
            if j >= n:
                raise error("unterminated character set")
            cls = pat[i : j + 1]
            # A `]` as the FIRST member of a class is a literal `]`, not the
            # terminator — the scan above already knows that, but re1.5 does
            # not, so `[]]` reached it as an empty class followed by a stray
            # `]` and `re.findall('[]]+', ']] a')` returned [] where CPython
            # returns [']]']. Escaping it says the same thing in a form the
            # engine reads the same way.
            if cls[:2] == "[]":
                cls = "[\\]" + cls[2:]
            elif cls[:3] == "[^]":
                cls = "[^\\]" + cls[3:]
            if asc:
                cls = _ascii_class(cls)
            out.append(_fold_class(cls) if ic else cls)
            i = j + 1
            continue
        if c == "(":
            if pat[i : i + 4] == "(?P<":
                j = pat.find(">", i)
                if j < 0:
                    raise error("unterminated group name")
                gi += 1
                names[pat[i + 4 : j]] = gi
                out.append("(")
                i = j + 1
                continue
            if pat[i : i + 3] == "(?:":
                out.append("(?:")
                i += 3
                continue
            if pat[i : i + 2] == "(?":
                _unsupported(pat[i : i + 3])
            gi += 1
            out.append("(")
            i += 1
            continue
        if c == ".":
            # re1.5's Any matches \n too, CPython's . does not unless DOTALL.
            out.append("." if flags & S else "[^\n]")
            i += 1
            continue
        if c == "{" and out:
            j = pat.find("}", i)
            r = _reps(pat[i + 1 : j]) if j > 0 else None
            if r is not None:
                atom = out[-1]
                if atom[:1] == "(":
                    _unsupported("{n,m} on a group")
                lo, hi = r
                if hi is None:
                    if lo == 0:
                        rep = [atom + "*"]
                    else:
                        rep = [atom] * (lo - 1) + [atom + "+"]
                else:
                    rep = [atom] * lo + [atom + "?"] * (hi - lo)
                out[-1:] = rep
                i = j + 1
                continue
        if ic and c.isalpha():
            out.append("[" + c.lower() + c.upper() + "]")
            i += 1
            continue
        out.append(c)
        i += 1
    return ("".join(out), names, out[:1] == ["^"], out[-1:] == ["$"])


def _slice_guard(fn):
    """Run `fn`, turning a mid-codepoint slice into the exit-90 refusal.

    re1.5 matches BYTES. The variant patches its charclass so that a byte >=
    0x80 is a word constituent, which makes `\w+` match a whole non-ASCII word
    — but an UNQUANTIFIED `\w` still matches exactly one byte, so on
    "raksmorgas" with combining characters it lands mid-character and slicing
    the span raises UnicodeError.

    That is lypning-mp being unable to produce CPython's answer, not the program
    being wrong, so it belongs to the 90 rather than to a traceback at exit 1.
    Turning it into a refusal is all that is available here: matching a whole
    UTF-8 sequence would need an alternation, which cannot appear inside a
    character class, so the fix is in the engine or nowhere.
    """
    try:
        return fn()
    except UnicodeError:
        _unsupported("a match that splits a multi-byte character (an unquantified \\w matches one byte)")


class Match:
    def __init__(self, m, names, off, string):
        self._m = m
        self._names = names
        self._off = off
        self.string = string

    def _idx(self, g):
        if isinstance(g, str):
            if g not in self._names:
                raise IndexError("no such group")
            return self._names[g]
        return g

    def group(self, *a):
        if len(a) == 0:
            return _slice_guard(lambda: self._m.group(0))
        if len(a) == 1:
            return _slice_guard(lambda: self._m.group(self._idx(a[0])))
        return tuple(_slice_guard(lambda i=x: self._m.group(self._idx(i))) for x in a)

    def groups(self, default=None):
        g = _slice_guard(lambda: self._m.groups())
        if default is None:
            return g
        return tuple(default if x is None else x for x in g)

    def groupdict(self, default=None):
        d = {}
        for k in self._names:
            v = _slice_guard(lambda kk=k: self._m.group(self._names[kk]))
            d[k] = default if v is None else v
        return d

    def start(self, g=0):
        v = _slice_guard(lambda: self._m.start(self._idx(g)))
        return v if v < 0 else v + self._off

    def end(self, g=0):
        v = _slice_guard(lambda: self._m.end(self._idx(g)))
        return v if v < 0 else v + self._off

    def span(self, g=0):
        return (self.start(g), self.end(g))

    def __repr__(self):
        return "<re.Match object; span=" + repr(self.span()) + ">"


class Pattern:
    def __init__(self, pattern, flags=0, _notrim=False):
        _check_flags(flags)
        # `pattern` stays the source the caller passed, as CPython's does;
        # `_src` is what every rewrite downstream works from.
        self._src = _strip_verbose(pattern) if flags & X else pattern
        native, names, anchored, dollar = _prep(self._src, flags)
        self.pattern = pattern
        self.flags = flags
        self.groupindex = names
        self._r = _re.compile(native)
        self._names = names
        self._anchored = anchored
        self._dollar = dollar and not _notrim
        self._full = None

    def fullmatch(self, string):
        if self._full is None:
            # `(?:…)$` is non-capturing, so group numbering is unchanged; the
            # trailing-newline trim is suppressed because CPython's fullmatch
            # has to consume the newline too. The wrap is built from the
            # already-stripped source, with X cleared: appending `)$` to a
            # verbose pattern that ends in a `#` comment would put the wrap
            # INSIDE the comment and lose it.
            self._full = Pattern("(?:" + self._src + ")$", self.flags & ~X, True)
            self._full._names = self._names
        return self._full.match(string)

    def search(self, string):
        for m in _find(self, string):
            return m
        return None

    def match(self, string):
        m = self._r.match(_trim(self, string))
        return Match(m, self._names, 0, string) if m else None

    def findall(self, string):
        return _findall(self, string)

    def finditer(self, string):
        return _find(self, string)

    def split(self, string, maxsplit=0):
        return _split(self, string, maxsplit)

    def sub(self, repl, string, count=0):
        if _can_use_native_sub(self, repl):
            return self._r.sub(repl, string, count)
        return _subn(self, repl, string, count)[0]

    def subn(self, repl, string, count=0):
        return _subn(self, repl, string, count)


def _can_use_native_sub(p, repl):
    # The C engine has its own substitution loop (`re_sub_helper` in
    # extmod/modre.c), and it is 15x faster than doing the same work here: it
    # appends into one vstr with pointer arithmetic, while _subn below builds a
    # Python list of slices, and `s[last:st]` walks the string from the start on
    # every match. Measured on this build, 2,000 substitutions over a 2,000-
    # character line: 22 ms native against 340 ms through the shim.
    #
    # It is NOT a drop-in replacement, which is why this gate exists rather than
    # a straight delegation. Four differences, each of which would be a silent
    # wrong answer at exit 0 — the one failure mode docs/MICROPYTHON.md treats as
    # worse than being slow:
    #
    #   1. An EMPTY match ends the native loop (`caps[0] == caps[1]` breaks), so
    #      re.sub(r"", "-", "abc") returns "abc" where CPython returns "-a-b-c-".
    #      Any pattern that can match empty is refused. The test is whether it
    #      matches the empty string: this engine has no lookaround and no word
    #      boundary, so the only context-sensitive instructions are Bol and Eol,
    #      and both are satisfied at position 0 of "". A pattern that can consume
    #      zero characters anywhere can therefore do it on "" too.
    #   2. A BACKSLASH in the template means something different. The native
    #      loop knows \1 and \g<1> but not \g<name>, and it drops the backslash
    #      from \n rather than turning it into a newline, which _expand does.
    #      Only a backslash-free template is delegated.
    #   3. MULTILINE is done here, by splitting the subject into lines and
    #      matching each (see _segments). The native loop sees one string, so ^
    #      and $ would only match at its two ends.
    #   4. A trailing `$` needs the newline trim in _trim, which the native loop
    #      does not do.
    #
    # A callable replacement is technically supported by the native loop, but it
    # would be handed the RAW match object rather than the wrapper this module
    # returns, so the callback would see a different API. Refused too.
    if not isinstance(repl, str) or "\\" in repl:
        return False
    if (p.flags & M) or p._dollar:
        return False
    return p._r.match("") is None


def _trim(p, s):
    # CPython's `$` also matches just before a trailing newline; re1.5's Eol
    # only matches at the very end. Drop that newline before matching.
    if p._dollar and not (p.flags & M) and s[-1:] == "\n":
        return s[:-1]
    return s


def _segments(p, s):
    # MULTILINE is implemented by splitting the subject into lines, because
    # re1.5's Bol/Eol only match at the two ends of the string it is given.
    #
    # SPLIT ONLY WHEN THE PATTERN ACTUALLY USES `^` OR `$`. MULTILINE changes
    # nothing else in CPython — it is defined purely as "^ also matches after a
    # newline, $ also matches before one" — so splitting a pattern that has
    # neither anchor cannot help and actively breaks it: no pattern could match
    # ACROSS a newline. `re.findall(r"\s\w+", "ab\ncd", re.M)` is ['\ncd'] in
    # CPython and was [] here, and there is no `^` or `$` anywhere in it. A
    # harvested corpus entry caught it on line 2098 of a 3,900-case sweep.
    if (p.flags & M) and (p._anchored or p._dollar):
        # The residual limit, refused rather than answered wrongly: with the
        # subject split, a pattern that is anchored AND can consume a newline
        # can no longer match one, and unlike the case above there is nothing
        # to fall back to. The test is deliberately conservative — anything
        # that could put a newline inside the match.
        if _may_match_newline(p._src):
            _unsupported("re.M with a pattern that can match a newline")
        off = 0
        for line in s.split("\n"):
            yield line, off
            off += len(line) + 1
    else:
        yield _trim(p, s), 0


def _may_match_newline(pat):
    """Could this pattern consume a newline?

    Only asked for an anchored MULTILINE pattern, where a wrong answer cannot
    be recovered. Over-reporting costs a refusal and a retry on real python3;
    under-reporting is a silent wrong answer, so the doubt goes one way.
    """
    i = 0
    n = len(pat)
    while i < n:
        c = pat[i]
        if c == "\\":
            nx = pat[i + 1 : i + 2]
            if nx in ("n", "s", "S", "D", "W"):
                return True
            i += 2
            continue
        if c == "[":
            # A negated class matches a newline unless it excludes one, and
            # working out which is not worth the risk here.
            j = pat.find("]", i + 1)
            cls = pat[i : j + 1] if j > 0 else pat[i:]
            if cls[:2] == "[^" or "\\n" in cls or "\\s" in cls or "\\S" in cls:
                return True
            i = (j + 1) if j > 0 else n
            continue
        if c == "\n":
            return True
        i += 1
    return False


def _find(p, s):
    # Every scanning operation in this module — findall, finditer, split, sub —
    # is this loop. It used to advance by RE-SLICING the subject at each match
    # (`search(seg[pos:])`), which copies the entire remainder of the string per
    # match and makes a single re.sub over a 2,000-character line quadratic:
    # 10.4x stock MicroPython in docs/BENCH-LEDGER.md, against 0.57x for
    # the same substitution done by the C engine directly. The cost was the
    # slicing, not the engine.
    #
    # The engine already takes a start position — `re_exec_helper` in
    # extmod/modre.c does `subj.begin += startpos` for a compiled pattern — so
    # the scan can advance a cursor instead of copying.
    #
    # THE CATCH, and why there are two loops below. That `startpos` is a BYTE
    # offset, while `match.span()` answers in CHARACTERS (match_span_helper runs
    # utf8_ptr_to_index over the result). Feeding a character index back in as a
    # byte offset lands mid-codepoint on any non-ASCII subject: measured on this
    # build, `search("räksmörgås abc", 12)` reports a span starting at 9. The
    # two units coincide exactly when the subject is ASCII, and only then.
    #
    # So the cursor path is taken on an ASCII subject and the old slicing path
    # on any other, with the test being one encode per call — against one slice
    # per match, which is what it replaces. Non-ASCII input keeps today's cost;
    # closing that needs the engine to hand back a byte-space resume point,
    # which is a port-patch hunk rather than a shim change (docs/MICROPYTHON.md §8c).
    #
    # One deliberate behaviour change rides along, in the ASCII path only: with
    # slicing, the engine saw each remainder as a fresh string, so `^` matched at
    # every restart. With a cursor it sees the true line start, which is what
    # CPython's pos does. That is CPython's semantics, not a divergence from it —
    # `re-caret-midpattern` in the seed corpus pins it.
    for seg, base in _segments(p, s):
        ln = len(seg)
        if len(seg.encode()) == ln:
            pos = 0
            while pos <= ln:
                m = p._r.search(seg, pos)
                if m is None:
                    break
                st, en = m.span()
                yield Match(m, p._names, base, s)
                if p._anchored:
                    break
                pos = en if en > st else st + 1
        else:
            pos = 0
            while pos <= ln:
                m = p._r.search(seg[pos:] if pos else seg)
                if m is None:
                    break
                st, en = m.span()
                yield Match(m, p._names, base + pos, s)
                if p._anchored:
                    break
                pos += en if en > st else st + 1


def _findall(p, s):
    # Guarded at the boundary rather than at each span access: the fast
    # paths below reach the native match object directly, so a slice that
    # lands mid-character can surface from several places.
    return _slice_guard(lambda: _findall_raw(p, s))


def _findall_raw(p, s):
    out = []
    for m in _find(p, s):
        g = m._m.groups()
        if not g:
            out.append(m._m.group(0))
        elif len(g) == 1:
            out.append("" if g[0] is None else g[0])
        else:
            out.append(tuple("" if x is None else x for x in g))
    return out


def _split(p, s, maxsplit):
    # Guarded at the boundary rather than at each span access: the fast
    # paths below reach the native match object directly, so a slice that
    # lands mid-character can surface from several places.
    return _slice_guard(lambda: _split_raw(p, s, maxsplit))


def _split_raw(p, s, maxsplit):
    out = []
    last = 0
    k = 0
    for m in _find(p, s):
        if maxsplit and k >= maxsplit:
            break
        st, en = m.span()
        out.append(s[last:st])
        for g in m._m.groups():
            out.append(g)
        last = en
        k += 1
    out.append(s[last:])
    return out


_TEMPLATE_ESC = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "f": "\f",
    "v": "\v",
    "a": "\a",
    "b": "\b",
    "0": "\0",
    "\\": "\\",
}


def _expand(m, t):
    if "\\" not in t:
        return t
    out = []
    i = 0
    n = len(t)
    while i < n:
        c = t[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        i += 1
        c = t[i : i + 1]
        if c == "g" and t[i + 1 : i + 2] == "<":
            j = t.find(">", i)
            if j < 0:
                raise error("unterminated group name")
            k = t[i + 2 : j]
            v = m.group(int(k) if k.isdigit() else k)
            out.append("" if v is None else v)
            i = j + 1
        elif c and c in "123456789":
            j = i
            while j < n and t[j].isdigit() and j - i < 2:
                j += 1
            v = m.group(int(t[i:j]))
            out.append("" if v is None else v)
            i = j
        else:
            out.append(_TEMPLATE_ESC.get(c, c))
            i += 1
    return "".join(out)


def _subn(p, repl, s, count):
    # Guarded at the boundary rather than at each span access: the fast
    # paths below reach the native match object directly, so a slice that
    # lands mid-character can surface from several places.
    return _slice_guard(lambda: _subn_raw(p, repl, s, count))


def _subn_raw(p, repl, s, count):
    # Every match _find yields is substituted, INCLUDING an empty one that
    # abuts the previous match. This looks like a bug and is the rule: CPython
    # skipped that match up to 3.6 and stopped skipping it in 3.7 (bpo-32308),
    # so re.sub(r"b*", "-", "abc") is "-a--c-" and not "-a-c-" — the empty match
    # at position 2, immediately after the "b", produces its own replacement.
    # This module carried the 3.6 rule and answered "-a-c-" at exit 0, silently,
    # for every quantifier that can match empty; `re-sub-empty-match` in the seed
    # corpus is the entry that caught it. split() and findall() never had the
    # skip and were already right, which is why the divergence was sub-only.
    fn = repl if callable(repl) else None
    out = []
    last = 0
    n = 0
    for m in _find(p, s):
        st, en = m.span()
        out.append(s[last:st])
        out.append(fn(m) if fn else _expand(m, repl))
        last = en
        n += 1
        if count and n >= count:
            break
    out.append(s[last:])
    return ("".join(out), n)


_cache = {}


def compile(pattern, flags=0):
    if isinstance(pattern, Pattern):
        return pattern
    key = pattern + "\x00" + str(flags)
    p = _cache.get(key)
    if p is None:
        if len(_cache) > 24:
            _cache.clear()
        p = Pattern(pattern, flags)
        _cache[key] = p
    return p


def search(pattern, string, flags=0):
    return compile(pattern, flags).search(string)


def match(pattern, string, flags=0):
    return compile(pattern, flags).match(string)


def fullmatch(pattern, string, flags=0):
    return compile(pattern, flags).fullmatch(string)


def findall(pattern, string, flags=0):
    return _findall(compile(pattern, flags), string)


def finditer(pattern, string, flags=0):
    return _find(compile(pattern, flags), string)


def split(pattern, string, maxsplit=0, flags=0):
    return _split(compile(pattern, flags), string, maxsplit)


def sub(pattern, repl, string, count=0, flags=0):
    return compile(pattern, flags).sub(repl, string, count)


def subn(pattern, repl, string, count=0, flags=0):
    return _subn(compile(pattern, flags), repl, string, count)


_SPECIAL = "()[]{}?*+-|^$\\.&~# \t\n\r\v\f"


def escape(pattern):
    out = []
    for c in pattern:
        if c in _SPECIAL:
            out.append("\\")
        out.append(c)
    return "".join(out)


def purge():
    _cache.clear()
