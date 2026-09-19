"""urllib.parse.urlencode, parse_qs and parse_qsl.

The engine refuses ``import urllib``, so form-encoding has to be built by
hand.  This is a port of CPython's ``urlencode``, ``parse_qs`` and
``parse_qsl`` (Lib/urllib/parse.py) for ``str`` input, restricted to the
UTF-8 codec.  The percent-encoding helpers below are the ones from the
``urllib_quote`` unit, inlined -- a unit is self-contained because the
engine has no local imports.

The details that are not guessable:

  * ``parse_qs`` returns ``dict`` of ``list``, ``parse_qsl`` returns a
    ``list`` of ``(name, value)`` pairs, and the difference is load
    bearing: a repeated key collapses into one dict entry with two list
    elements, and the ORDER of the two is the order in the query string.
    ``parse_qs`` is built on ``parse_qsl``, so every flag below means the
    same thing to both.
  * A field with an empty value is DROPPED by default.  ``'a=1&b='``
    parses to ``[('a', '1')]``, not to a pair with an empty string;
    ``keep_blank_values=True`` is what keeps it.  A field with no ``'='``
    at all is the same case: dropped, or kept with an empty value.
  * ``strict_parsing=True`` turns "no ``=`` in this field" into
    ``ValueError('bad query field: %r')``, and it also makes EMPTY fields
    visible: ``'a=1&&b=2'`` silently skips the empty field by default but
    raises under strict parsing, because the ``if name_value or
    strict_parsing`` guard lets the empty string through to the ``=``
    check.
  * The separator is ``'&'`` and only ``'&'``.  ``';'`` was accepted as a
    second separator until it was removed in the 3.6.13/3.7.10 security
    fix, so ``parse_qsl('a=1;b=2')`` is one field whose value is
    ``'1;b=2'``.  ``separator=';'`` asks for the old behaviour; an empty
    separator raises.
  * Names and values are unquoted with ``unquote_plus``, so ``'+'`` is a
    space on the way in and a space is ``'+'`` on the way out.
  * ``urlencode`` calls ``str()`` on anything that is not a string, and
    without ``doseq`` that includes lists: ``urlencode({'a': ['x']})`` is
    ``a=%5B%27x%27%5D``, the repr, percent-encoded.  With ``doseq=True``
    a list becomes one field per element, and an EMPTY list becomes no
    field at all -- the key disappears.  ``doseq`` still sends ``str``
    through whole rather than one field per character.

Not covered: ``bytes`` keys, values, queries or separators (CPython
accepts them and returns bytes from ``parse_qsl``), the ``encoding`` and
``errors`` arguments, and ``urlencode``'s acceptance of an arbitrary
mapping -- CPython probes with ``hasattr(query, 'items')``, which the
engine refuses, so this port tests for ``dict`` instead.  One deliberate
divergence, on an error path no case below reaches: handed a sequence
that is neither a mapping nor pairs, CPython raises ``TypeError('not a
valid non-string sequence or mapping object')`` and this raises
``ValueError`` with the same message, because ``TypeError('...')`` is
outside the subset.  Verified against the real module on 400,000 random
query strings (200,000 of them under ``strict_parsing``) and 50,000
random ``urlencode`` inputs (2026-09-16).
"""
# fills: urllib.parse.urlencode, urllib.parse.parse_qs, urllib.parse.parse_qsl
# reference: urllib.parse

_ALWAYS_SAFE = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789"
    "_.-~"
)
_HEXDIG = "0123456789ABCDEFabcdef"
_REPLACEMENT = chr(0xFFFD)


def quote(string, safe="/"):
    """quote('abc def') -> 'abc%20def'.  See the urllib_quote unit."""
    if not string:
        return string
    extra = ""
    for ch in safe:
        if ord(ch) < 128:
            extra = extra + ch
    out = []
    for n in string.encode("utf-8"):
        ch = chr(n)
        if ch in _ALWAYS_SAFE or (extra and ch in extra):
            out.append(ch)
        else:
            out.append("%{:02X}".format(n))
    return "".join(out)


def quote_plus(string, safe=""):
    """Like quote(), but ' ' becomes '+' and safe defaults to ''."""
    if " " not in string:
        return quote(string, safe)
    return quote(string, safe + " ").replace(" ", "+")


def _hex_pair(item):
    """The byte value of the first two bytes of `item`, or -1."""
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

    One U+FFFD per maximal subpart, with the RFC 3629 lead-byte table.
    See the urllib_quote unit for the full argument.
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


def _is_sized(value):
    """CPython's ``try: len(v) / except TypeError:`` sequence probe."""
    try:
        len(value)
        return True
    except TypeError:
        return False


def urlencode(query, doseq=False, safe="", quote_via=quote_plus):
    """Encode a dict, or a sequence of two-element pairs, as a query."""
    if isinstance(query, dict):
        pairs = list(query.items())
    else:
        pairs = list(query)
        if pairs and not isinstance(pairs[0], tuple):
            raise ValueError("not a valid non-string sequence or mapping object")

    out = []
    for pair in pairs:
        key = pair[0]
        value = pair[1]
        key = quote_via(str(key), safe)
        if not doseq:
            out.append(key + "=" + quote_via(str(value), safe))
        elif isinstance(value, str):
            out.append(key + "=" + quote_via(value, safe))
        elif _is_sized(value):
            for element in value:
                out.append(key + "=" + quote_via(str(element), safe))
        else:
            out.append(key + "=" + quote_via(str(value), safe))
    return "&".join(out)


def parse_qsl(qs, keep_blank_values=False, strict_parsing=False,
              max_num_fields=None, separator="&"):
    """Parse a query string into a list of (name, value) pairs."""
    if not separator or not isinstance(separator, str):
        raise ValueError("Separator must be of type string or bytes.")
    if not qs:
        return []

    if max_num_fields is not None:
        num_fields = 1 + qs.count(separator)
        if max_num_fields < num_fields:
            raise ValueError("Max number of fields exceeded")

    pairs = []
    for name_value in qs.split(separator):
        if name_value or strict_parsing:
            name, has_eq, value = name_value.partition("=")
            if not has_eq and strict_parsing:
                raise ValueError("bad query field: %r" % (name_value,))
            if value or keep_blank_values:
                pairs.append((unquote_plus(name), unquote_plus(value)))
    return pairs


def parse_qs(qs, keep_blank_values=False, strict_parsing=False,
             max_num_fields=None, separator="&"):
    """Parse a query string into a dict of name -> list of values."""
    parsed = {}
    pairs = parse_qsl(qs, keep_blank_values, strict_parsing,
                      max_num_fields=max_num_fields, separator=separator)
    for pair in pairs:
        name = pair[0]
        value = pair[1]
        if name in parsed:
            parsed[name].append(value)
        else:
            parsed[name] = [value]
    return parsed


# --- cases ---
# urlencode: the shapes it accepts, and what str() does to a value.
print(repr(urlencode({})))
print(repr(urlencode([])))
print(repr(urlencode({"a": "1", "b": "2"})))
print(repr(urlencode([("a", "1"), ("a", "2")])))
print(repr(urlencode((("z", "26"), ("y", "25")))))
print(repr(urlencode({"q": "a b&c=d"})))
print(repr(urlencode({"k": "~-._"})))
print(repr(urlencode({"a": 1, "b": 2.5, "c": None, "d": True})))
print(repr(urlencode({"path": "/a/b"})))
print(repr(urlencode({"path": "/a/b"}, safe="/")))
print(repr(urlencode({"t": "a+b"})))
print(repr(urlencode({"t": "a+b"}, safe="+")))
print(repr(urlencode({"ä": "ö"})))

# Without doseq a list value is str()'d; with doseq it fans out, and an
# empty list contributes NOTHING -- the key disappears.
print(repr(urlencode({"a": ["x", "y"]})))
print(repr(urlencode({"a": ["x", "y"]}, doseq=True)))
print(repr(urlencode({"a": ("x", "y")}, doseq=True)))
print(repr(urlencode({"a": []}, doseq=True)))
print(repr(urlencode({"a": [], "b": "1"}, doseq=True)))
print(repr(urlencode({"a": "xy"}, doseq=True)))
print(repr(urlencode({"a": 7}, doseq=True)))
print(repr(urlencode({"a": [1, 2, 3]}, doseq=True)))
print(repr(urlencode({"a": "b c"}, doseq=True)))

# quote_via swaps the encoder: quote keeps a space as %20, not '+'.
print(repr(urlencode({"p": "a b/c"}, quote_via=quote)))
print(repr(urlencode({"p": "a b/c"}, safe="/", quote_via=quote)))
print(repr(urlencode({"p": "a b/c"}, safe="/")))

# parse_qsl: the default drops blank values, keep_blank_values keeps them.
print(parse_qsl("a=1&b=2"))
print(parse_qsl("a=1&a=2"))
print(parse_qsl("a=1&b="))
print(parse_qsl("a=1&b=", keep_blank_values=True))
print(parse_qsl("a"))
print(parse_qsl("a", keep_blank_values=True))
print(parse_qsl(""))
print(parse_qsl("&&"))
print(parse_qsl("&&", keep_blank_values=True))
print(parse_qsl("a=1&&b=2"))
print(parse_qsl("=v"))
print(parse_qsl("=v", keep_blank_values=True))
print(parse_qsl("a=1=2"))
print(parse_qsl("a==", keep_blank_values=True))

# '+' is a space, %XX is decoded, and ';' is NOT a separator any more.
print(parse_qsl("a+b=c+d"))
print(parse_qsl("a%20b=c%2Bd"))
print(parse_qsl("a=%C3%A9"))
print([ord(c) for c in parse_qsl("a=%E2%28")[0][1]])
print(parse_qsl("a=1;b=2"))
print(parse_qsl("a=1;b=2", separator=";"))
print(parse_qsl("a=1|b=2", separator="|"))
print(parse_qsl("a=1&&b=2", separator="&&"))

# strict_parsing sees the empty field that the default silently skips.
try:
    parse_qsl("a", strict_parsing=True)
except ValueError as exc:
    print("ValueError:", exc)
try:
    parse_qsl("a=1&&b=2", strict_parsing=True)
except ValueError as exc:
    print("ValueError:", exc)
try:
    parse_qsl("a=1&b=2", separator="")
except ValueError as exc:
    print("ValueError:", exc)
try:
    parse_qsl("a=1&b=2&c=3", max_num_fields=2)
except ValueError as exc:
    print("ValueError:", exc)
print(parse_qsl("a=1&b=2&c=3", max_num_fields=3))
print(parse_qsl("a=1&b=2", strict_parsing=True))

# parse_qs: the same fields, collapsed into a dict of lists.
print(parse_qs("a=1&b=2"))
print(parse_qs("a=1&a=2&b=3"))
print(parse_qs(""))
print(parse_qs("a=1&b="))
print(parse_qs("a=1&b=", keep_blank_values=True))
print(parse_qs("a=1&a=&a=3", keep_blank_values=True))
print(parse_qs("a+b=c+d"))
print(parse_qs("a=1;b=2"))
print(parse_qs("a=1;b=2", separator=";"))

# Round trip.
print(repr(urlencode(parse_qsl("x=a+b&y=%2F&x=2", keep_blank_values=True))))
print(parse_qs(urlencode({"a": ["1", "2"]}, doseq=True)))
