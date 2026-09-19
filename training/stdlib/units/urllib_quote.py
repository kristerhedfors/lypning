"""urllib.parse.quote, quote_plus, unquote and unquote_plus.

The engine refuses ``import urllib``, so percent-encoding has to be built
out of ``str.encode``, ``bytes.decode`` and integer arithmetic.  This is a
port of CPython's ``quote``/``quote_from_bytes``/``quote_plus`` and
``unquote_to_bytes``/``unquote``/``unquote_plus`` (Lib/urllib/parse.py)
for ``str`` input, restricted to the UTF-8 codec.

The four details a from-memory version always gets wrong:

  * ``quote`` defaults to ``safe='/'`` and ``quote_plus`` to ``safe=''``.
    The always-safe set is ``A-Z a-z 0-9 _ . - ~`` -- RFC 3986's
    unreserved set, so ``~`` is NOT escaped (it was, before Python 3.7).
    Everything else, including every byte of a multi-byte UTF-8
    character, becomes ``%XX`` with UPPERCASE hex digits.
  * ``safe`` is normalised with ``safe.encode('ascii', 'ignore')``, so a
    non-ASCII character in ``safe`` is silently DROPPED rather than
    making its UTF-8 bytes safe: ``quote('ä', safe='ä')`` is ``'%C3%A4'``.
  * ``quote_plus`` only reaches its ``'+'`` path when the input actually
    contains a space; otherwise it delegates straight to ``quote``.  A
    literal ``'+'`` in the input is escaped to ``%2B`` either way, which
    is what makes the transform reversible.
  * ``unquote`` decodes per ASCII RUN, not per ``%XX``.  CPython splits on
    ``([\\x00-\\x7f]+)``: every maximal ASCII run is percent-decoded to
    bytes and then decoded as UTF-8 with ``errors='replace'``, while
    non-ASCII characters are passed through untouched.  So the bytes of
    ``%C3%A9`` are joined BEFORE the UTF-8 decode and give one ``é``, and
    a stray ``%C3`` gives one U+FFFD.  A ``%`` that is not followed by two
    hex digits is left alone as a literal ``%``; ``%2`` and ``%zz`` are
    literals, and hex digits may be either case.

``errors='replace'`` is itself refused by both engines
(``encoding: decode(errors='replace')``), so ``_utf8_replace`` implements
the replacement policy directly: the Unicode "maximal subpart" rule, one
U+FFFD per maximal prefix of a well-formed sequence, with the RFC 3629
lead-byte table (no overlongs, no surrogates, nothing above U+10FFFF).
Valid sequences are handed to ``bytes.decode()`` so the codepoint
arithmetic is the engine's, not ours.  Verified byte-identical against
``bytes.decode('utf-8', 'replace')`` on all 65,536 two-byte strings and
on 400,000 random strings of length 1-8 (half of them drawn from a
lead-byte-biased pool); ``quote``, ``quote_plus``, ``unquote``,
``unquote_plus`` and ``unquote_to_bytes`` against the real module on
350,000 further random inputs (2026-09-16).

Not covered: ``bytes`` input (``quote``/``unquote`` also accept it and
return the matching type), non-UTF-8 ``encoding``/``errors`` arguments,
``quote_from_bytes``'s ``bytearray`` input, and surrogate input to
``quote`` -- ``str.encode('utf-8')`` raises there, as it does in CPython.
"""
# fills: urllib.parse.quote, urllib.parse.quote_plus, urllib.parse.unquote, urllib.parse.unquote_plus, urllib.parse.quote_from_bytes, urllib.parse.unquote_to_bytes
# reference: urllib.parse

_ALWAYS_SAFE = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789"
    "_.-~"
)
_HEXDIG = "0123456789ABCDEFabcdef"
_REPLACEMENT = chr(0xFFFD)


def _ascii_safe(safe):
    """CPython's ``safe.encode('ascii', 'ignore')``: drop non-ASCII."""
    out = ""
    for ch in safe:
        if ord(ch) < 128:
            out = out + ch
    return out


def quote_from_bytes(bs, safe="/"):
    """Percent-encode a bytes object; always returns an ASCII str."""
    if not bs:
        return ""
    extra = _ascii_safe(safe)
    out = []
    for n in bs:
        ch = chr(n)
        if ch in _ALWAYS_SAFE or (extra and ch in extra):
            out.append(ch)
        else:
            out.append("%{:02X}".format(n))
    return "".join(out)


def quote(string, safe="/"):
    """quote('abc def') -> 'abc%20def'."""
    if not string:
        return string
    return quote_from_bytes(string.encode("utf-8"), safe)


def quote_plus(string, safe=""):
    """Like quote(), but ' ' becomes '+' and safe defaults to ''."""
    if " " not in string:
        return quote(string, safe)
    return quote(string, safe + " ").replace(" ", "+")


def _hex_pair(item):
    """The byte value of the first two bytes of `item`, or -1.

    `item` is a bytes object; indexing it yields ints.  CPython looks the
    two characters up in a 144-entry table built from
    ``'0123456789ABCDEFabcdef'``, so a short or non-hex pair is a miss.
    """
    if len(item) < 2:
        return -1
    value = 0
    i = 0
    while i < 2:
        pos = _HEXDIG.find(chr(item[i]))
        if pos < 0:
            return -1
        if pos >= 16:
            pos = pos - 6
        value = value * 16 + pos
        i = i + 1
    return value


def unquote_to_bytes(string):
    """unquote_to_bytes('abc%20def') -> b'abc def'."""
    if not string:
        return b""
    data = string.encode("utf-8")
    bits = data.split(b"%")
    if len(bits) == 1:
        return data
    res = [bits[0]]
    for item in bits[1:]:
        value = _hex_pair(item)
        if value < 0:
            res.append(b"%")
            res.append(item)
        else:
            res.append(bytes([value]))
            res.append(item[2:])
    return b"".join(res)


def _utf8_replace(data):
    """``data.decode('utf-8', 'replace')``, spelled out.

    One U+FFFD per maximal subpart: a byte sequence that is a prefix of
    some well-formed sequence but cannot be completed is consumed whole
    and replaced once; any other bad byte is replaced on its own.
    """
    out = []
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b < 0x80:
            out.append(chr(b))
            i = i + 1
            continue
        # RFC 3629: the lead byte fixes the length and the range the
        # SECOND byte may take.  0xC0/0xC1 would be overlong, 0xF5-0xFF
        # would be above U+10FFFF, and a continuation byte cannot lead.
        if 0xC2 <= b <= 0xDF:
            length = 2
            lo = 0x80
            hi = 0xBF
        elif b == 0xE0:
            length = 3
            lo = 0xA0
            hi = 0xBF
        elif 0xE1 <= b <= 0xEC:
            length = 3
            lo = 0x80
            hi = 0xBF
        elif b == 0xED:
            # 0xED 0xA0.. would be a UTF-16 surrogate.
            length = 3
            lo = 0x80
            hi = 0x9F
        elif 0xEE <= b <= 0xEF:
            length = 3
            lo = 0x80
            hi = 0xBF
        elif b == 0xF0:
            length = 4
            lo = 0x90
            hi = 0xBF
        elif 0xF1 <= b <= 0xF3:
            length = 4
            lo = 0x80
            hi = 0xBF
        elif b == 0xF4:
            length = 4
            lo = 0x80
            hi = 0x8F
        else:
            out.append(_REPLACEMENT)
            i = i + 1
            continue
        j = i + 1
        if j >= n or data[j] < lo or data[j] > hi:
            out.append(_REPLACEMENT)
            i = i + 1
            continue
        j = j + 1
        complete = True
        while j < i + length:
            if j >= n or data[j] < 0x80 or data[j] > 0xBF:
                complete = False
                break
            j = j + 1
        if not complete:
            out.append(_REPLACEMENT)
            i = j
            continue
        out.append(data[i:i + length].decode())
        i = i + length
    return "".join(out)


def unquote(string):
    """Replace %xx escapes by their single-character equivalent."""
    if "%" not in string:
        return string
    out = []
    i = 0
    n = len(string)
    while i < n:
        j = i
        if ord(string[i]) < 128:
            while j < n and ord(string[j]) < 128:
                j = j + 1
            out.append(_utf8_replace(unquote_to_bytes(string[i:j])))
        else:
            while j < n and ord(string[j]) >= 128:
                j = j + 1
            out.append(string[i:j])
        i = j
    return "".join(out)


def unquote_plus(string):
    """Like unquote(), but '+' becomes ' ' first."""
    return unquote(string.replace("+", " "))


# --- cases ---
# quote: the always-safe set, and the default safe='/'.
print(repr(quote("abc def")))
print(repr(quote("")))
print(repr(quote("ABCabc012_.-~")))
print(repr(quote("/path/to/file")))
print(repr(quote("/path/to/file", "")))
print(repr(quote(":/?#[]@!$&'()*+,;=")))
print(repr(quote(":/?#[]@!$&'()*+,;=", "/:@")))
print(repr(quote("a b", " ")))
print(repr(quote("\x00\x1f\x7f")))
print(repr(quote("%")))
print(repr(quote("100%")))

# Non-ASCII is UTF-8 encoded byte by byte, with uppercase hex.
print(repr(quote("el niño")))
print(repr(quote("€")))
print(repr(quote("\U0001d11e")))
print(repr(quote("ä", "ä")))
print(repr(quote("ä", "ä/")))

# quote_plus: safe defaults to '', and '+' is escaped either way.
print(repr(quote_plus("a b c")))
print(repr(quote_plus("a+b")))
print(repr(quote_plus("a+b c")))
print(repr(quote_plus("/a b", "/")))
print(repr(quote_plus("/a/b")))
print(repr(quote_plus("")))
print(repr(quote_plus("  ")))
print(repr(quote_plus("a b", "+")))
print(repr(quote_plus("el niño")))

# unquote: the happy path, then every way a '%' fails to start an escape.
print(repr(unquote("abc%20def")))
print(repr(unquote("no escapes here")))
print(repr(unquote("")))
print(repr(unquote("%2F%2f")))
print(repr(unquote("%")))
print(repr(unquote("%%")))
print(repr(unquote("%%41")))
print(repr(unquote("%2")))
print(repr(unquote("%zz")))
print(repr(unquote("%2G")))
print(repr(unquote("a%")))
print(repr(unquote("100%25")))
print(repr(unquote("%41%42%43")))
print(repr(unquote("%61%62%63")))

# unquote: an ASCII run's bytes are joined BEFORE the UTF-8 decode.
print(repr(unquote("%C3%A9")))
print(repr(unquote("%c3%a9")))
print(repr(unquote("%E2%82%AC")))
print([ord(c) for c in unquote("caf%C3%A9 nai%CC%88ve")])
print([ord(c) for c in unquote("%F0%9F%92%A9")])

# unquote: malformed UTF-8 lands on the replacement character.
print([ord(c) for c in unquote("%E2%28")])
print([ord(c) for c in unquote("%C3%28")])
print([ord(c) for c in unquote("%FF%FE")])
print([ord(c) for c in unquote("%ED%A0%80")])
print([ord(c) for c in unquote("%E0%80%80")])
print([ord(c) for c in unquote("%F0%82%82%AC")])
print([ord(c) for c in unquote("%F4%90%80%80")])
print([ord(c) for c in unquote("a%C3b%A9c")])
print([ord(c) for c in unquote("%E2%82")])
print([ord(c) for c in unquote("%80")])
print([ord(c) for c in unquote("%C2")])

# unquote: a non-ASCII character in the INPUT is passed through, and can
# never be part of an escape, because CPython splits on ASCII runs.
print(repr(unquote("é%41")))
print(repr(unquote("%41é%42")))
print(repr(unquote("éè%20x")))

# unquote_plus / unquote_to_bytes.
print(repr(unquote_plus("%7e/abc+def")))
print(repr(unquote_plus("a+b+c")))
print(repr(unquote_plus("a%2Bb")))
print(repr(unquote_plus("")))
print(repr(unquote_to_bytes("abc%20def")))
print(repr(unquote_to_bytes("%FF%00")))
print(repr(unquote_to_bytes("")))
print(repr(unquote_to_bytes("plain")))
print(repr(unquote_to_bytes("é")))

# Round trips.
print(unquote(quote("el niño/café")) == "el niño/café")
print(unquote_plus(quote_plus("a b+c&d=e")) == "a b+c&d=e")
print(repr(quote_plus("a b+c&d=e")))
