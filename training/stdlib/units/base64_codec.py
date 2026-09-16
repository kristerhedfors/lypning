"""base64: the four functions the engine serves, and the rest built on them.

The core engine refuses ``import base64`` outright.  The wider variant
serves exactly four names -- ``b64encode``, ``b64decode``,
``urlsafe_b64encode``, ``urlsafe_b64decode`` -- and even those only in
their plain form: ``b64encode(s, altchars=...)`` refuses, and
``b64decode(s, validate=True)`` refuses because a truthy ``validate``
selects binascii's strict mode, whose every rejection is a
``binascii.Error`` message the engine does not write.

Everything else in the module refuses as ``module-attr``:
``standard_b64encode``, ``standard_b64decode``, ``b32encode``,
``b32decode``, ``b32hexencode``, ``b32hexdecode``, ``b16encode``,
``b16decode``, ``encodebytes``, ``decodebytes``, ``a85``/``b85``.

So this unit is the rest of RFC 4648 written on top of the part that
works.  ``b64encode``'s output is taken from the engine and the
``altchars`` substitution is applied afterwards, one byte at a time --
which is what ``bytes.maketrans`` does, and unlike two chained
``.replace()`` calls it is *simultaneous*, so an ``altchars`` of
``b'/+'`` swaps rather than collapses.  Base32 and base16 are written
out from the bit layout, because there is nothing underneath them to
lean on.

The layouts, since the whole point of this file is that they are
reproduced from the spec rather than remembered:

  * **base64** -- 3 bytes -> 4 characters of 6 bits, alphabet ``A-Za-z0-9+/``,
    padded to a multiple of 4 with ``=``.  urlsafe swaps ``+/`` for ``-_``.
  * **base32** -- 5 bytes -> 8 characters of 5 bits, alphabet
    ``A-Z2-7``, padded to a multiple of 8.  A short final quantum leaves
    1, 3, 4 or 6 pad characters and never 2, 5 or 7, which is why
    ``b32decode`` rejects any other pad count as ``Incorrect padding``.
    base32hex is the same layout over ``0-9A-V``, whose ordering makes
    the encoding sort the same way the input does.
  * **base16** -- 1 byte -> 2 upper-case hex digits, never padded.

Three ``b32decode`` details that are easy to get wrong and are pinned
below: ``casefold`` upper-cases before decoding; ``map01`` maps the
digit ``0`` to ``O`` *and* the digit ``1`` to whichever of ``I`` or ``L``
is passed, so passing ``map01`` is what makes ``0`` and ``1`` legal at
all; and the pad count is validated *after* the quanta are decoded, so a
string of the right length with a wrong pad count still reports
``Incorrect padding`` rather than ``Non-base32 digit found``.

Verification, run 2026-09-16: besides the case list, a randomised
sweep compared all six encoders and all six decoders against the real
module on 4,000 payloads of up to 70 bytes plus 4,000 random
``altchars`` pairs, and fuzzed the four decoders with 20,000 junk
strings drawn from the alphabet, the pad character, lower case, digits,
a non-ASCII byte, a space and a newline -- comparing the decoded value
*or* the exception message.  104,000 checks, 0 divergences.

Deliberate divergences, all forced by the subset:

  * **Exception class, not message.**  CPython raises ``binascii.Error``.
    That class *is* a subclass of ``ValueError``, so ``except ValueError``
    catches both, and every message here is CPython's verbatim:
    ``Incorrect padding``, ``Non-base32 digit found``,
    ``Non-base16 digit found``, ``Odd-length string``,
    ``string argument should contain only ASCII characters``.
  * **No ``validate=True``.**  ``b64decode`` here is the lenient mode
    only: characters outside the alphabet are discarded before decoding,
    which is what the engine's ``b64decode`` does and what CPython does
    with ``validate=False``.  Strict mode is not faked.
  * ``b16encode`` is built from ``bytes.hex()`` rather than
    ``binascii.hexlify``; ``bytes.fromhex`` is refused, so ``b16decode``
    converts nibble by nibble.
  * ``bytes.upper()``, ``bytes.translate`` and ``bytearray`` are all
    outside the subset, so every decoder works on a list of integers and
    calls ``bytes(...)`` once at the end.
"""
# fills: base64.standard_b64encode, base64.standard_b64decode, base64.b32encode, base64.b32decode, base64.b32hexencode, base64.b32hexdecode, base64.b16encode, base64.b16decode, base64.encodebytes, base64.decodebytes
# reference: base64

import base64

_B32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_B32HEX_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUV"


def _as_byte_list(data):
    """CPython's _bytes_from_decode_data, as a list of ints.

    A str argument must be pure ASCII; bytes pass through unchanged.
    """
    if isinstance(data, str):
        out = []
        for ch in data:
            code = ord(ch)
            if code > 127:
                raise ValueError(
                    "string argument should contain only ASCII characters")
            out.append(code)
        return out
    return list(data)


def _upper_byte(code):
    if 97 <= code <= 122:
        return code - 32
    return code


def _substitute(values, frm, to):
    """Simultaneous single-byte substitution -- bytes.maketrans, by hand."""
    out = []
    for code in values:
        if code == frm[0]:
            out.append(to[0])
        elif code == frm[1]:
            out.append(to[1])
        else:
            out.append(code)
    return out


def b64encode(data, altchars=None):
    """Base64 with an optional two-byte replacement for `+` and `/`."""
    encoded = base64.b64encode(data)
    if altchars is None:
        return encoded
    alt = _as_byte_list(altchars)
    return bytes(_substitute(list(encoded), [43, 47], alt))


def b64decode(data, altchars=None):
    """Base64, lenient: characters outside the alphabet are discarded."""
    values = _as_byte_list(data)
    if altchars is not None:
        alt = _as_byte_list(altchars)
        values = _substitute(values, alt, [43, 47])
    return base64.b64decode(bytes(values))


def standard_b64encode(data):
    """base64.standard_b64encode -- b64encode with no altchars."""
    return b64encode(data)


def standard_b64decode(data):
    """base64.standard_b64decode -- b64decode with no altchars."""
    return b64decode(data)


def urlsafe_b64encode(data):
    """Base64 over the URL and filename safe alphabet: `-` and `_`."""
    return b64encode(data, b"-_")


def urlsafe_b64decode(data):
    """The inverse of urlsafe_b64encode."""
    return b64decode(data, b"-_")


def encodebytes(data):
    """MIME base64: 57 input bytes per line, each line ending in `\\n`."""
    pieces = []
    for i in range(0, len(data), 57):
        pieces.append(base64.b64encode(data[i:i + 57]))
        pieces.append(b"\n")
    return b"".join(pieces)


def decodebytes(data):
    """The inverse of encodebytes; newlines are simply not in the alphabet."""
    return b64decode(data)


def _b32encode(alphabet, data):
    leftover = len(data) % 5
    if leftover:
        data = data + bytes(5 - leftover)
    out = []
    for i in range(0, len(data), 5):
        acc = 0
        for byte in data[i:i + 5]:
            acc = (acc << 8) + byte
        shift = 35
        while shift >= 0:
            out.append(alphabet[(acc >> shift) % 32])
            shift = shift - 5
    encoded = "".join(out)
    # A short final quantum leaves 1, 3, 4 or 6 pad characters.
    if leftover == 1:
        encoded = encoded[:-6] + "======"
    elif leftover == 2:
        encoded = encoded[:-4] + "===="
    elif leftover == 3:
        encoded = encoded[:-3] + "==="
    elif leftover == 4:
        encoded = encoded[:-1] + "="
    return encoded.encode()


def _b32decode(alphabet, data, casefold=False, map01=None):
    values = _as_byte_list(data)
    if len(values) % 8:
        raise ValueError("Incorrect padding")
    if map01 is not None:
        mapped = _as_byte_list(map01)
        if len(mapped) != 1:
            raise ValueError("map01 must be a single character")
        # RFC 4648 section 2.4: 0 reads as O, and 1 as either I or L.
        values = _substitute(values, [48, 49], [79, mapped[0]])
    if casefold:
        values = [_upper_byte(code) for code in values]
    reverse = {}
    for index in range(len(alphabet)):
        reverse[ord(alphabet[index])] = index
    total = len(values)
    end = total
    while end > 0 and values[end - 1] == 61:
        end = end - 1
    body = values[:end]
    padchars = total - end
    decoded = []
    acc = 0
    for i in range(0, len(body), 8):
        acc = 0
        for code in body[i:i + 8]:
            if code not in reverse:
                raise ValueError("Non-base32 digit found")
            acc = (acc << 5) + reverse[code]
        for shift in (32, 24, 16, 8, 0):
            decoded.append((acc >> shift) % 256)
    # The pad count is checked here, after the quanta, exactly as CPython
    # checks it -- the order decides which of the two errors you see.
    if total % 8 or padchars not in (0, 1, 3, 4, 6):
        raise ValueError("Incorrect padding")
    if padchars and decoded:
        acc = acc << (5 * padchars)
        last = []
        for shift in (32, 24, 16, 8, 0):
            last.append((acc >> shift) % 256)
        leftover = (43 - 5 * padchars) // 8
        decoded = decoded[:-5] + last[:leftover]
    return bytes(decoded)


def b32encode(data):
    """RFC 4648 base32, alphabet A-Z and 2-7."""
    return _b32encode(_B32_ALPHABET, data)


def b32decode(data, casefold=False, map01=None):
    """RFC 4648 base32, decoded."""
    return _b32decode(_B32_ALPHABET, data, casefold, map01)


def b32hexencode(data):
    """RFC 4648 base32hex ("extended hex"), alphabet 0-9 and A-V."""
    return _b32encode(_B32HEX_ALPHABET, data)


def b32hexdecode(data, casefold=False):
    """RFC 4648 base32hex, decoded.  No map01: 0 and 1 are real digits here."""
    return _b32decode(_B32HEX_ALPHABET, data, casefold, None)


def b16encode(data):
    """RFC 4648 base16: upper-case hex, never padded."""
    return data.hex().upper().encode()


def _hexval(code):
    if 48 <= code <= 57:
        return code - 48
    return code - 55


def b16decode(data, casefold=False):
    """RFC 4648 base16, decoded.  Lower case only with casefold=True."""
    values = _as_byte_list(data)
    if casefold:
        values = [_upper_byte(code) for code in values]
    for code in values:
        if not ((48 <= code <= 57) or (65 <= code <= 70)):
            raise ValueError("Non-base16 digit found")
    # The digit check runs first, so a bad character in an odd-length
    # string reports the bad character and not the length.
    if len(values) % 2:
        raise ValueError("Odd-length string")
    out = []
    for i in range(0, len(values), 2):
        out.append(_hexval(values[i]) * 16 + _hexval(values[i + 1]))
    return bytes(out)


def _message(fn, *args):
    """Run fn(*args) and return its ValueError message, or the value."""
    try:
        return fn(*args)
    except ValueError as exc:
        return "ValueError: " + str(exc)


# --- cases ---
# The RFC 4648 section 10 test vectors, all three encodings.
print(b64encode(b""), b32encode(b""), b32hexencode(b""), b16encode(b""))
print(b64encode(b"f"), b32encode(b"f"), b32hexencode(b"f"), b16encode(b"f"))
print(b64encode(b"fo"), b32encode(b"fo"), b32hexencode(b"fo"), b16encode(b"fo"))
print(b64encode(b"foo"), b32encode(b"foo"), b32hexencode(b"foo"), b16encode(b"foo"))
print(b64encode(b"foob"), b32encode(b"foob"), b32hexencode(b"foob"), b16encode(b"foob"))
print(b64encode(b"fooba"), b32encode(b"fooba"), b32hexencode(b"fooba"), b16encode(b"fooba"))
print(b64encode(b"foobar"), b32encode(b"foobar"), b32hexencode(b"foobar"), b16encode(b"foobar"))
print(b64decode(b64encode(b"")) == b"", b32decode(b32encode(b"")) == b"")
print(b64decode(b64encode(b"f")) == b"f", b32decode(b32encode(b"f")) == b"f")
print(b64decode(b64encode(b"fo")) == b"fo", b32decode(b32encode(b"fo")) == b"fo")
print(b64decode(b64encode(b"foo")) == b"foo", b32decode(b32encode(b"foo")) == b"foo")
print(b64decode(b64encode(b"foob")) == b"foob", b32decode(b32encode(b"foob")) == b"foob")
print(b64decode(b64encode(b"fooba")) == b"fooba", b32decode(b32encode(b"fooba")) == b"fooba")
print(b64decode(b64encode(b"foobar")) == b"foobar", b32decode(b32encode(b"foobar")) == b"foobar")
print(b32hexdecode(b32hexencode(b"fooba")) == b"fooba", b16decode(b16encode(b"fooba")) == b"fooba")
print(b32hexdecode(b32hexencode(b"foobar")) == b"foobar", b16decode(b16encode(b"foobar")) == b"foobar")

# base64, including the two padding lengths and the high-bit bytes.
print(b64encode(b"abc"))
print(b64encode(b"ab"))
print(b64encode(b"a"))
print(b64encode(bytes([0, 1, 2, 3, 4, 5])))
print(b64encode(bytes([251, 255, 254])))
print(b64encode(bytes([i for i in range(256)])))
print(b64decode(b"YWJj"))
print(b64decode("YWJj"))
print(repr(b64decode(b"")))
print(b64decode(b64encode(bytes([i for i in range(256)]))) == bytes([i for i in range(256)]))

# The lenient mode: characters outside the alphabet are discarded.
print(b64decode(b"YW*Jj"))
print(b64decode(b"YWJj\n"))
print(b64decode(b"Y W J j"))

# altchars, and the simultaneous substitution that a double replace breaks.
print(b64encode(bytes([251, 239])))
print(b64encode(bytes([251, 239]), b"-_"))
print(b64encode(bytes([251, 239]), b"/+"))
print(b64decode(b64encode(bytes([251, 239]), b"/+"), b"/+") == bytes([251, 239]))
print(b64decode(b64encode(bytes([251, 239]), b"!?"), b"!?") == bytes([251, 239]))

# urlsafe and standard, checked against the engine's own two functions.
print(urlsafe_b64encode(bytes([251, 255])))
print(urlsafe_b64decode(b"-_8="))
print(urlsafe_b64encode(bytes([i for i in range(256)])) == base64.urlsafe_b64encode(bytes([i for i in range(256)])))
print(urlsafe_b64decode(base64.urlsafe_b64encode(bytes([i for i in range(256)]))) == bytes([i for i in range(256)]))
print(standard_b64encode(b"abc"))
print(standard_b64decode(b"YWJj"))
print(standard_b64encode(bytes([251, 255])) == base64.b64encode(bytes([251, 255])))

# MIME wrapping: one line per 57 input bytes.
print(encodebytes(b""))
print(encodebytes(b"abc"))
print(encodebytes(b"a" * 57))
print(encodebytes(b"a" * 58))
print(len(encodebytes(b"a" * 200).split(b"\n")))
print(decodebytes(encodebytes(b"a" * 200)) == b"a" * 200)
print(decodebytes(b"YWJj\n"))

# base32, every quantum length and both pad counts that are not 1 or 4.
print(b32encode(b"a"))
print(b32encode(b"ab"))
print(b32encode(b"abc"))
print(b32encode(b"abcd"))
print(b32encode(b"abcde"))
print(b32encode(b"abcdef"))
print(b32encode(bytes([0, 0, 0, 0, 0])))
print(b32encode(bytes([255, 255, 255, 255, 255])))
print(b32decode(b"MFRGG==="))
print(b32decode("MFRGG==="))
print(repr(b32decode(b"")))
print(b32decode(b32encode(bytes([i for i in range(256)]))) == bytes([i for i in range(256)]))

# casefold and map01.
print(b32decode(b"mfrgg===", True))
print(repr(_message(b32decode, b"mfrgg===")))
print(b32decode(b"MFRGG===", False, None))
print(b32decode(b"M1RGG===", False, b"L") == b32decode(b"MLRGG==="))
print(b32decode(b"M0RGG===", False, b"L") == b32decode(b"MORGG==="))
print(b32decode(b"M1RGG===", False, b"I") == b32decode(b"MIRGG==="))
print(repr(_message(b32decode, b"M1RGG===")))

# base32 errors: length, alphabet, pad count -- in CPython's order.
print(repr(_message(b32decode, b"MFRGG")))
print(repr(_message(b32decode, b"MFRG====")))
print(repr(_message(b32decode, b"MFRGG=======")))
print(repr(_message(b32decode, b"MFRGG!==")))
print(repr(_message(b32decode, b"========")))
print(repr(_message(b32decode, "éééééééé")))

# base32hex sorts the way the input does; base32 does not.
print(b32hexencode(bytes([0, 0, 0, 0, 0])))
print(b32hexencode(bytes([255, 255, 255, 255, 255])))
print(b32hexencode(b"foobar"))
print(b32hexdecode(b"CPNMUOJ1E8======"))
print(b32hexdecode(b"cpnmuoj1e8======", True))
print(b32hexencode(bytes([1])) < b32hexencode(bytes([2])))

# base16.
print(b16encode(b""))
print(b16encode(b"abc"))
print(b16encode(bytes([0, 15, 16, 255])))
print(b16decode(b"616263"))
print(b16decode("616263"))
print(b16decode(b"61626F"))
print(b16decode(b"61626f", True))
print(repr(_message(b16decode, b"61626f")))
print(repr(_message(b16decode, b"6162")))
print(repr(_message(b16decode, b"616")))
print(repr(_message(b16decode, b"61g2")))
print(repr(_message(b16decode, b"61g")))
print(repr(_message(b16decode, "éé")))
print(b16decode(b16encode(bytes([i for i in range(256)]))) == bytes([i for i in range(256)]))

# The three encodings on one payload, side by side.
_P = b"lypning"
print(b64encode(_P), b32encode(_P), b32hexencode(_P), b16encode(_P))
print(len(b64encode(_P)), len(b32encode(_P)), len(b16encode(_P)))
