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
    twin of the same writer and takes the same ``sep`` arguments, plus
    one rule of its own: the separator must be **ASCII**.  A separator
    whose ordinal is above 0x7F is written through by
    ``binascii.hexlify`` as that raw byte and rejected by ``bytes.hex``
    with ``ValueError: sep must be ASCII.`` -- after the length check,
    which runs first in both.  ``hex_str`` enforces that rule rather
    than delegating blind, so it is exact for every separator; the unit
    prints both so the pair is visible.
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
exercises it, and no HELPER reaches it either: the subset cannot raise
``TypeError``, so a case there could only pin the divergence as if it
were CPython's behaviour.  The second half of that is not free --
``hex_str`` used to hand its own default ``None`` down to ``hexlify``
as an explicit argument, which reached the divergence from inside the
unit, so it now calls ``hexlify(data)`` with no ``sep`` the way
``bytes.hex()`` does.  ``b2a_hex`` still delegates with all three
arguments and may: its caller has already chosen a separator or not,
and the default it would forward is its own.

The separator is covered over its whole domain.  Measured on CPython
3.11 on 2026-09-17 with::

    python3.11 -c 'import binascii
    def last(f):
        hi = -1
        for n in range(0x1100):
            try:
                f(chr(n))
                hi = n
            except ValueError:
                pass
        return hex(hi)
    print(last(lambda s: binascii.hexlify(b"abcd", s, 2)),
          last(lambda s: b"abcd".hex(s, 2)))'
    # -> 0xff 0x7f   (the last separator ordinal each one accepts)

``binascii.hexlify`` takes a latin-1 view and accepts 0x00..0xFF as str
or bytes, writing the raw byte, and raises ``ValueError("sep must be
ASCII.")`` from U+0100 up; ``bytes.hex`` raises that same message from
0x80 up, as str or bytes alike.  Both check the length before the
ordinal, so ``""`` and ``"--"`` are ``sep must be length 1.`` either
way, and this port keeps that order.

Not covered: the base64/uu/quoted-printable ``a2b_*`` family, which is
a different surface.
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
    """Validate a hexlify separator and return its byte value.

    Length first, then the ordinal: that is CPython's order, and the
    latin-1 view means everything below U+0100 is a separator byte.
    """
    if len(sep) != 1:
        raise ValueError("sep must be length 1.")
    if isinstance(sep, str):
        value = ord(sep)
    else:
        value = sep[0]
    if value > 255:
        raise ValueError("sep must be ASCII.")
    return value


def _sep_ascii_ordinal(sep):
    """The same, under ``bytes.hex()``'s stricter rule: ASCII only.

    ``hexlify`` would write 0x80..0xFF through as a raw byte; ``bytes.hex``
    refuses them, so ``hex_str`` checks before it delegates rather than
    letting the ``.decode()`` below fail with a different exception.
    """
    value = _sep_ordinal(sep)
    if value > 127:
        raise ValueError("sep must be ASCII.")
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
    """bytes.hex(): the str twin of hexlify, same grouping rules.

    Same grouping, one rule more: the separator must be ASCII.

    With no separator it calls ``hexlify(data)`` and passes no ``sep``
    at all, which is what CPython's ``bytes.hex()`` does.  Handing the
    default ``None`` down as an explicit argument would reach the one
    divergence the docstring declares -- ``hexlify(data, None, 1)`` is
    a ``TypeError`` in CPython and an unseparated result here -- from
    inside the unit, where nothing prints it.
    """
    if sep is None:
        return hexlify(data).decode()
    _sep_ascii_ordinal(sep)
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


def _hex_error_message(data, sep, bytes_per_sep):
    """The message the REAL ``bytes.hex`` raises, or '' if it raises none.

    The ASCII cases below pin ``hex_str`` against this, not against a
    remembered string, so a divergence shows up as a False in the output.
    """
    try:
        data.hex(sep, bytes_per_sep)
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
# separator at all".  An explicit sep=None is a TypeError in CPython, so
# it is left to the docstring rather than pinned by a case -- and no
# helper here passes one either, which is why `hex_str` calls
# `hexlify(data)` rather than forwarding its own default.
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

# ...but only bytes.hex insists the separator be ASCII.  hexlify writes
# 0x80..0xFF through as a raw byte; hex_str refuses it, with CPython's
# own message, before the decode that would otherwise fail differently.
# The passing cases print ordinals rather than repr(): repr() of an
# unprintable character needs CPython's unicode tables and is a refusal.
print([_b for _b in hexlify(b"abcd", "\x80", 2)])
print([_b for _b in hexlify(b"abcd", bytes([0xFF]), 2)])
print([ord(_c) for _c in hex_str(b"abcd", "\x7f", 2)])
print(hex_str(b"abcd", "\x7f", 2) == b"abcd".hex("\x7f", 2))
print(repr(_error_message(hex_str, b"abcd", "\x80", 2)))
print(repr(_error_message(hex_str, b"abcd", "\xff", 2)))
print(repr(_error_message(hex_str, b"abcd", bytes([0x80]), 2)))
print(repr(_error_message(hex_str, b"abcd", "\u0100", 2)))
print(repr(_error_message(hexlify, b"abcd", "\u0100", 2)))
print(repr(_error_message(hex_str, b"abcd", "--", 2)))

# Pinned against the real bytes.hex, message and check order alike: the
# length is tested before the ordinal, so "--" is a length error even
# though it is also not ASCII.
print(_error_message(hex_str, b"abcd", "\x80", 2) == _hex_error_message(b"abcd", "\x80", 2))
print(_error_message(hex_str, b"abcd", "\xff", 2) == _hex_error_message(b"abcd", "\xff", 2))
print(_error_message(hex_str, b"abcd", "\u0100", 2) == _hex_error_message(b"abcd", "\u0100", 2))
print(_error_message(hex_str, b"abcd", "--", 2) == _hex_error_message(b"abcd", "--", 2))
print(_error_message(hex_str, b"abcd", "\u0100\u0100", 2) == _hex_error_message(b"abcd", "\u0100\u0100", 2))

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
