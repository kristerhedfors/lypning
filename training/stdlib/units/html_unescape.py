"""html.unescape and html.escape, without re and without the html5 table.

Covers CPython's ``html.escape`` in full, and ``html.unescape`` restricted
to the five XML entities -- ``amp lt gt quot apos`` -- plus every numeric
character reference, ``&#NN;`` and ``&#xNN;``.

The scan is a hand-written port of CPython's

    _charref = re.compile(r'&(#[0-9]+;?|#[xX][0-9a-fA-F]+;?'
                          r'|[^\\t\\n\\f <&#;]{1,32};?)')

so the three details a from-memory version always gets wrong are kept:

  * The trailing ``;`` is OPTIONAL.  ``&amp``, ``&#65`` and ``&#x41`` all
    decode; ``&#`` and ``&#x;`` do not, because the numeric branches need
    at least one digit, and the named branch cannot start with ``#``.
  * A named reference that is not in the table is retried against its own
    PREFIXES, longest first, down to length 2.  That is why ``&ampersand``
    is ``'&ersand'`` and ``&ampx`` is ``'&x'`` -- not literal text.
  * The named run is at most 32 characters and stops at tab, newline,
    form feed, space, ``<``, ``&``, ``#`` and ``;`` -- but NOT at ``\\r``,
    so ``&a\\rb;`` is one failed reference and stays whole.

Numeric references are handled exactly as the HTML 5 parser specifies and
CPython implements: the 34 Windows-1252 rewrites in ``_invalid_charrefs``
(``&#147;`` is a curly quote, ``&#0;`` is U+FFFD, ``&#13;`` is CR), a
surrogate or a value above U+10FFFF becomes U+FFFD, and a codepoint in
``_invalid_codepoints`` decodes to the EMPTY string.  That last set is
written here as an exact range predicate rather than a 122-element literal;
``_is_invalid_codepoint`` was checked against ``html._invalid_codepoints``
over all of range(0x110000) on 2026-09-16 with zero mismatches.

DIVERGENCE, deliberate: the named table holds only the five XML entities,
in the casings the HTML 5 list actually defines -- ``amp AMP lt LT gt GT
quot QUOT`` with and without the semicolon, and ``apos`` ONLY with it
(``&apos`` is not a legacy reference, so CPython leaves it literal too).
The other 2,214 html5 names are not here, so ``unescape('&nbsp;')`` is
``'&nbsp;'`` where CPython gives ``'\\xa0'``, and ``unescape('&ltrie;')``
is ``'<rie;'`` where CPython gives ``'\\u22b4'``.  Every case below stays
inside the covered set, so the differential against the real ``html`` is
byte-identical.

Not covered: a numeric reference with more than eighteen digits leaves
signed 64-bit and needs the wider engine; none is used below.
"""
# fills: html.unescape, html.escape
# reference: html

_DIGITS = "0123456789"
_HEXDIGITS = "0123456789abcdefABCDEF"
# The complement of CPython's [^\t\n\f <&#;] character class.
_NAME_STOP = "\t\n\x0c <&#;"

# CPython's html._invalid_charrefs: the Windows-1252 rewrites the HTML 5
# numeric-character-reference-end state mandates, plus 0x00 and 0x0d.
_INVALID_CHARREFS = {
    0x00: "�",
    0x0D: "\r",
    0x80: "€",
    0x81: "\x81",
    0x82: "‚",
    0x83: "ƒ",
    0x84: "„",
    0x85: "…",
    0x86: "†",
    0x87: "‡",
    0x88: "ˆ",
    0x89: "‰",
    0x8A: "Š",
    0x8B: "‹",
    0x8C: "Œ",
    0x8D: "\x8d",
    0x8E: "Ž",
    0x8F: "\x8f",
    0x90: "\x90",
    0x91: "‘",
    0x92: "’",
    0x93: "“",
    0x94: "”",
    0x95: "•",
    0x96: "–",
    0x97: "—",
    0x98: "˜",
    0x99: "™",
    0x9A: "š",
    0x9B: "›",
    0x9C: "œ",
    0x9D: "\x9d",
    0x9E: "ž",
    0x9F: "Ÿ",
}

# The five XML entities, in exactly the casings html.entities.html5 defines.
# 'apos' has no semicolon-less form; 'APOS' and 'APOS;' do not exist at all.
_XML_ENTITIES = {
    "amp": "&",
    "amp;": "&",
    "AMP": "&",
    "AMP;": "&",
    "lt": "<",
    "lt;": "<",
    "LT": "<",
    "LT;": "<",
    "gt": ">",
    "gt;": ">",
    "GT": ">",
    "GT;": ">",
    "quot": '"',
    "quot;": '"',
    "QUOT": '"',
    "QUOT;": '"',
    "apos;": "'",
}


def _is_invalid_codepoint(num):
    """CPython's ``num in html._invalid_codepoints``, as ranges.

    0x01-0x08, 0x0b, 0x0e-0x1f, 0x7f-0x9f, 0xfdd0-0xfdef, and every
    noncharacter pair 0xNfffe / 0xNffff up to plane 16.
    """
    if 0x01 <= num <= 0x08:
        return True
    if num == 0x0B:
        return True
    if 0x0E <= num <= 0x1F:
        return True
    if 0x7F <= num <= 0x9F:
        return True
    if 0xFDD0 <= num <= 0xFDEF:
        return True
    if num & 0xFFFE == 0xFFFE:
        return True
    return False


def _match_charref(s, i):
    """The regex group for a reference starting at ``s[i] == '&'``, or None.

    The returned text never includes the leading ``&``, so the caller
    advances by ``1 + len(group)`` -- the same span ``re.sub`` consumes.
    """
    n = len(s)
    j = i + 1
    if j < n and s[j] == "#":
        k = j + 1
        if k < n and (s[k] == "x" or s[k] == "X"):
            m = k + 1
            while m < n and s[m] in _HEXDIGITS:
                m = m + 1
            if m > k + 1:
                if m < n and s[m] == ";":
                    m = m + 1
                return s[j:m]
        m = j + 1
        while m < n and s[m] in _DIGITS:
            m = m + 1
        if m > j + 1:
            if m < n and s[m] == ";":
                m = m + 1
            return s[j:m]
        return None
    m = j
    while m < n and m - j < 32 and s[m] not in _NAME_STOP:
        m = m + 1
    if m == j:
        return None
    if m < n and s[m] == ";":
        m = m + 1
    return s[j:m]


def _replace_charref(g):
    """CPython's ``html._replace_charref``, over the restricted name table."""
    if g[0] == "#":
        if g[1] == "x" or g[1] == "X":
            num = int(g[2:].rstrip(";"), 16)
        else:
            num = int(g[1:].rstrip(";"))
        if num in _INVALID_CHARREFS:
            return _INVALID_CHARREFS[num]
        if 0xD800 <= num <= 0xDFFF or num > 0x10FFFF:
            return "�"
        if _is_invalid_codepoint(num):
            return ""
        return chr(num)
    if g in _XML_ENTITIES:
        return _XML_ENTITIES[g]
    # "find the longest matching name (as defined by the standard)":
    # prefixes of length len(g)-1 down to 2, never shorter.
    x = len(g) - 1
    while x > 1:
        if g[:x] in _XML_ENTITIES:
            return _XML_ENTITIES[g[:x]] + g[x:]
        x = x - 1
    return "&" + g


def unescape(s):
    """Convert named and numeric character references in `s` to characters."""
    if "&" not in s:
        return s
    out = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == "&":
            group = _match_charref(s, i)
            if group is not None:
                out.append(_replace_charref(group))
                i = i + 1 + len(group)
                continue
        out.append(s[i])
        i = i + 1
    return "".join(out)


def escape(s, quote=True):
    """Replace ``&``, ``<``, ``>`` -- and with `quote`, ``"`` and ``'``."""
    s = s.replace("&", "&amp;")  # Must be done first!
    s = s.replace("<", "&lt;")
    s = s.replace(">", "&gt;")
    if quote:
        s = s.replace('"', "&quot;")
        s = s.replace("'", "&#x27;")
    return s


# --- cases ---
# escape: the ampersand goes first, or the other replacements get eaten.
print(repr(escape("<a href='x'>T&C</a>")))
print(repr(escape("<a href='x'>T&C</a>", False)))
print(repr(escape("")))
print(repr(escape("&amp;")))
print(repr(escape('" \' & < >')))
print(repr(escape('" \' & < >', quote=False)))
print(repr(escape("no markup here")))
print(repr(escape("caf\xe9 — na\xefve")))

# unescape: the five XML entities, both casings, with and without ';'.
print(repr(unescape("&amp;&lt;&gt;&quot;&apos;")))
print(repr(unescape("&AMP;&LT;&GT;&QUOT;")))
print(repr(unescape("&amp&lt&gt&quot")))
print(repr(unescape("&apos")))
print(repr(unescape("&APOS;")))

# The prefix retry: a name that is not in the table is cut back.
print(repr(unescape("&ampersand")))
print(repr(unescape("&ampx")))
print(repr(unescape("&ltx;")))
print(repr(unescape("&quotient")))

# Names with no match anywhere stay literal.
print(repr(unescape("&foo;")))
print(repr(unescape("&a;")))
print(repr(unescape("&zzz")))
print(repr(unescape("&frobnicate;")))
print(repr(unescape("&;")))
print(repr(unescape("& amp;")))
print(repr(unescape("&&amp;")))
print(repr(unescape("&a\rb;")))

# A run of 32 name characters matches; the 33rd starts a new scan.
print(repr(unescape("&" + "x" * 32 + ";")))
print(repr(unescape("&" + "x" * 33 + ";")))

# Numeric references, decimal and hex, with and without the semicolon.
print(repr(unescape("&#65;&#x42;&#X43;")))
print(repr(unescape("&#65&#x42")))
print(repr(unescape("&#0000065;")))
print(repr(unescape("&#x0041;")))
print(repr(unescape("&#65;;")))
print(repr(unescape("&#65;x")))
print(repr(unescape("&#8364;")))
print([ord(ch) for ch in unescape("&#x1F600;")])

# Numeric references that are NOT a plain chr().  These decode to
# replacement, control and private-use characters, so the cases print
# CODEPOINTS: repr() of an unprintable character needs CPython's own
# unicode tables and is a refusal on both engines.
print([ord(ch) for ch in unescape("&#0;")])
print(repr(unescape("&#13;")))
print([ord(ch) for ch in unescape("&#147;&#148;")])
print([ord(ch) for ch in unescape("&#128;")])
print([ord(ch) for ch in unescape("&#xD800;")])
print([ord(ch) for ch in unescape("&#xDFFF;")])
print([ord(ch) for ch in unescape("&#1114112;")])
print(repr(unescape("&#1;")))
print(repr(unescape("&#11;")))
print(repr(unescape("&#127;")))
print(repr(unescape("&#xFDD0;")))
print(repr(unescape("&#xFFFE;")))
print(repr(unescape("&#xFFFF;")))
print(repr(unescape("&#x10FFFF;")))
print([ord(ch) for ch in unescape("&#x10FFFD;")])

# Malformed numeric references stay literal.
print(repr(unescape("&#")))
print(repr(unescape("&#;")))
print(repr(unescape("&#x;")))
print(repr(unescape("&#abc;")))
print([ord(ch) for ch in unescape("&#0x41;")])

# Whole strings, and the fast path for text with no '&' at all.
print(repr(unescape("")))
print(repr(unescape("plain text")))
print(repr(unescape("1 &lt; 2 &amp;&amp; 3 &gt; 2")))
print(repr(unescape("&lt;p&gt;caf&#233;&lt;/p&gt;")))
print(unescape(escape("<b>a & b</b>")))
print(repr(unescape(escape('"quoted" & \'single\''))))
