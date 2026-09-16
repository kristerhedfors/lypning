"""binascii.hexlify and binascii.unhexlify, separators and all.

The engine refuses ``import binascii``, and it refuses ``bytes.fromhex``
and ``bytearray`` too, so hex round-tripping has to be built out of
``bytes``, ``list`` and integer arithmetic.  This is a faithful port of
CPython's ``binascii.hexlify`` / ``binascii.unhexlify``
(Modules/binascii.c, plus the shared hex writer in Python/pystrhex.c)
together with their historical aliases ``b2a_hex`` / ``a2b_hex``, which
are literally the same functions.

The four details worth having in front of you:

  * ``hexlify`` returns **bytes**, not str.  ``bytes.hex()`` is the str
    twin of the same writer and takes the same ``sep`` arguments; the
    unit prints both so the pair is visible.
  * ``bytes_per_sep`` counts groups from the **right** when it is
    positive and from the **left** when it is negative, so the short
    group lands at the front for ``2`` and at the back for ``-2``.  A
    count of ``0``, or one at least as large as the data, produces no
    separator at all.
  * ``unhexlify`` checks the **length first**: a string of odd length is
    ``Odd-length string`` even when it is also full of non-hex bytes.
    Only ``0-9 a-f A-F`` are digits -- unlike ``int(s, 16)``, which
    happily accepts ``' +1f '``, so the strict digit table below is not
    an optimisation, it is the specification.
  * ``unhexlify`` accepts str and bytes alike, and a bytes input with a
    byte above 0x7F is a non-hexadecimal digit, not a decode error.

Errors: CPython raises ``binascii.Error``, which IS a subclass of
``ValueError``; the subset has no custom exceptions, so this port raises
``ValueError`` with the identical message and ``except ValueError``
catches both.  ``sep must be length 1.`` is a plain ``ValueError`` in
CPython too.

One divergence, measured on CPython 3.11 on 2026-09-16: ``sep=None`` is
this signature's DEFAULT sentinel, meaning "no separator", and
``hexlify(b"abcd")`` is right here and in CPython alike.  But passing it
explicitly beside a count -- ``binascii.hexlify(b"abcd", None, 2)`` --
raises ``TypeError: object of type 'NoneType' has no len()`` in CPython,
because the C writer takes ``len(sep)`` before it looks at the count.
This port returns the unseparated ``b'61626364'`` instead.  No case
exercises it: the subset cannot raise ``TypeError``, so a case there
could only pin the divergence as if it were CPython's behaviour.

Not covered: separator characters whose ordinal is above 255 (CPython's
writer takes a latin-1 view of a str ``sep``; this port refuses above 255
rather than guess), and the base64/uu/quoted-printable ``a2b_*`` family,
which is a different surface.
"""
# fills: binascii.hexlify, binascii.unhexlify, binascii.b2a_hex, binascii.a2b_hex
# reference: binascii

_HEX_LOWER = "0123456789abcdef"
_HEX_UPPER = "0123456789ABCDEF"


def _hex_digit_value(ch):
    """0-15 for one hex digit character, or -1 for anything else."""
    index = _HEX_LOWER.find(ch)
    if index >= 0:
        return index
    return _HEX_UPPER.find(ch)


def _sep_ordinal(sep):
    """Validate a separator and return its byte value."""
    if len(sep) != 1:
        raise ValueError("sep must be length 1.")
    if isinstance(sep, str):
        value = ord(sep)
    else:
        value = sep[0]
    if value > 255:
        raise ValueError("sep must be a single character below U+0100")
    return value


def _sep_before(index, total, group):
    """True when a separator is written just before byte `index`."""
    if index == 0 or group == 0:
        return False
    if group > 0:
        # Positive: groups counted from the right, the short one first.
        return (total - index) % group == 0
    # Negative: groups counted from the left, the short one last.
    return index % (-group) == 0


def hexlify(data, sep=None, bytes_per_sep=1):
    """Hexadecimal representation of binary data, as bytes."""
    if sep is None:
        group = 0
        sep_byte = 0
    else:
        sep_byte = _sep_ordinal(sep)
        group = bytes_per_sep
    total = len(data)
    out = []
    for index in range(total):
        if _sep_before(index, total, group):
            out.append(sep_byte)
        byte = data[index]
        out.append(ord(_HEX_LOWER[byte >> 4]))
        out.append(ord(_HEX_LOWER[byte & 0x0F]))
    return bytes(out)


def b2a_hex(data, sep=None, bytes_per_sep=1):
    """The older name for hexlify; the same function."""
    return hexlify(data, sep, bytes_per_sep)


def hex_str(data, sep=None, bytes_per_sep=1):
    """bytes.hex(): the str twin of hexlify, same grouping rules."""
    return hexlify(data, sep, bytes_per_sep).decode()


def unhexlify(hexstr):
    """Binary data from its hexadecimal representation."""
    total = len(hexstr)
    if total % 2 != 0:
        raise ValueError("Odd-length string")
    text_input = isinstance(hexstr, str)
    out = []
    index = 0
    while index < total:
        if text_input:
            high_ch = hexstr[index]
            low_ch = hexstr[index + 1]
        else:
            high_ch = chr(hexstr[index])
            low_ch = chr(hexstr[index + 1])
        high = _hex_digit_value(high_ch)
        low = _hex_digit_value(low_ch)
        if high < 0 or low < 0:
            raise ValueError("Non-hexadecimal digit found")
        out.append(high * 16 + low)
        index = index + 2
    return bytes(out)


def a2b_hex(hexstr):
    """The older name for unhexlify; the same function."""
    return unhexlify(hexstr)


def _error_message(fn, *args):
    """The message of the ValueError fn(*args) raises, or '' if none."""
    try:
        fn(*args)
    except ValueError as exc:
        return str(exc)
    return ""


# --- cases ---
print(repr(hexlify(b"abc")))
print(repr(hexlify(b"")))
print(repr(hexlify(bytes([0x00, 0xFF, 0x10, 0x0F]))))
print(repr(hexlify(bytes([171]))))
print(repr(hexlify(bytes([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]))))
print(repr(hexlify(bytes([0x80, 0x81, 0xFE, 0xFF]))))

# b2a_hex is the same function under its older name.
print(repr(b2a_hex(b"abc")))
print(repr(b2a_hex(b"abcd", b"-", 2)))

# A positive count groups from the RIGHT, so the short group is first.
print(repr(hexlify(b"abcd", b"-")))
print(repr(hexlify(b"abcd", b"-", 2)))
print(repr(hexlify(b"abcde", b":", 2)))
print(repr(hexlify(b"abcde", b":", 3)))
print(repr(hexlify(b"abcd", b"-", 4)))
print(repr(hexlify(b"abcd", b"-", 10)))

# A negative count groups from the LEFT, so the short group is last.
print(repr(hexlify(b"abcd", b"-", -1)))
print(repr(hexlify(b"abcde", b":", -2)))
print(repr(hexlify(b"abcde", b":", -3)))
print(repr(hexlify(b"abcd", b"-", -4)))
print(repr(hexlify(b"abcd", b"-", -10)))

# A count of zero, or one at least as large as the data, means "no
# separator at all".  An explicit sep=None is a TypeError in CPython and
# is left to the docstring, not to a case.
print(repr(hexlify(b"abcd", b"-", 0)))
print(repr(hexlify(b"", b"-", 2)))
print(repr(hexlify(b"a", b"-", 3)))

# A str separator and a bytes separator behave the same.
print(repr(hexlify(b"abcd", " ")))
print(repr(hexlify(b"abcd", ":", 2)))
print(repr(hexlify(b"abcd", bytes([0]))))
print(repr(hexlify(b"abcd", bytes([0x7F]))))
print(repr(_error_message(hexlify, b"abcd", b"--")))
print(repr(_error_message(hexlify, b"abcd", b"")))

# bytes.hex() is the same writer returning str, not bytes.
print(repr(hex_str(b"abc")))
print(repr(hex_str(b"abcd", "-", 2)))
print(repr(b"abc".hex()))
print(repr(b"abcd".hex("-", 2)))
print(hex_str(b"abcd", "-", 2) == b"abcd".hex("-", 2))

# unhexlify accepts str and bytes, upper and lower case.
print(repr(unhexlify(b"616263")))
print(repr(unhexlify("616263")))
print(repr(unhexlify(b"")))
print(repr(unhexlify(b"ABCDEF")))
print(repr(unhexlify(b"abcdef")))
print(repr(unhexlify("aBcDeF")))
print(repr(unhexlify("0000")))
print(repr(unhexlify("ff00")))
print(repr(a2b_hex(b"4142")))

# Length is checked before the digits are.
print(repr(_error_message(unhexlify, b"6")))
print(repr(_error_message(unhexlify, "61 62")))
print(repr(_error_message(unhexlify, b"6g")))
print(repr(_error_message(unhexlify, b"0G")))
print(repr(_error_message(unhexlify, bytes([0xFF, 0xFF]))))
print(repr(_error_message(unhexlify, bytes([0x00, 0x01]))))
print(repr(_error_message(unhexlify, "+1")))
print(repr(_error_message(unhexlify, " 1")))
print(repr(_error_message(unhexlify, "0x")))

# Round trip over every byte value.
print(unhexlify(hexlify(bytes([_i for _i in range(256)]))) == bytes([_i for _i in range(256)]))
print(len(hexlify(bytes([_i for _i in range(256)]))))
print(repr(hexlify(bytes([_i for _i in range(256)]))[0:8]))
print(repr(hexlify(bytes([_i for _i in range(256)]))[504:512]))
print(unhexlify(hexlify(bytes([_i for _i in range(256)])).decode().upper()) == bytes([_i for _i in range(256)]))
