"""struct.pack and struct.calcsize for the integer codes, standard sizes.

The engine refuses ``import struct``, so binary framing has to be built
out of arithmetic.  This is a port of CPython's ``struct.pack`` and
``struct.calcsize`` (Modules/_struct.c) restricted to the ten integer
codes ``b B h H i I l L q Q`` under the four **standard-size** byte-order
prefixes ``<`` ``>`` ``!`` ``=``.  Under those prefixes there is no
alignment padding and ``l``/``L`` are four bytes, not the native eight --
which is exactly why wire formats spell the prefix out.

The five details worth having in front of you:

  * The range-check messages are **not uniform**, and CPython's own
    inconsistency is the specification.  Two things drive it, and neither
    is guessable:

      - The message depends on the BYTE ORDER for ``h H q Q``, because
        ``<``/``=`` run CPython's little-endian handler table and
        ``>``/``!`` its big-endian one, and the two were written by
        different hands.  ``pack('<h', 40000)`` is ``short format
        requires ...``; ``pack('>h', 40000)`` is ``'h' format requires
        ...``.  ``pack('<q', 2**63)`` is ``argument out of range``;
        ``pack('>q', 2**63)`` is ``int too large to convert``.
      - Every handler first converts the value to a C ``long`` or
        ``unsigned long`` and only THEN checks the code's own range, so a
        value outside the C type never reaches the pretty message.
        ``pack('<b', 2**63 - 1)`` is ``byte format requires -128 <=
        number <= 127`` but ``pack('<b', 2**63)`` is ``argument out of
        range``.  Which C type it is differs per code: ``b B h i l`` and
        little-endian ``H`` go through signed ``long``, so a negative
        value reaches the range check; ``I L``, big-endian ``H`` and
        ``Q`` go through ``unsigned long``, so a negative value does not.
        That is why ``pack('<I', -1)`` and ``pack('<I', 2**32)`` raise
        two different messages, and why ``pack('<H', -1)`` and
        ``pack('>H', -1)`` do too.

    The whole grid is printed in the cases.
  * A value is encoded by taking ``value % 256`` and dividing by 256,
    ``size`` times.  For a negative value Python's floor division and
    remainder already produce two's complement, so signed and unsigned
    share one loop and no ``value + 2**(8*size)`` correction is needed.
    The pair is deliberately arithmetic and not ``value & 0xFF`` /
    ``value >> 8``: they compute the same bytes, but the bitwise pair
    refuses on a bigint and the arithmetic one does not, and
    ``pack('<Q', v)`` for ``v > 2**63 - 1`` hands this loop a bigint.
  * The format is ASCII, not Unicode, and bounded.  CPython encodes the
    format to ASCII before it parses it, so a character above U+007F is
    a UnicodeEncodeError naming its position (a run of them is reported
    as one run) and an embedded NUL is ``embedded null character`` --
    both ahead of every other check, including the prefix.  Only the
    ASCII digits are digits and only the six ASCII spaces (space, tab,
    newline, carriage return, U+000B, U+000C) are spaces, which is
    narrower than ``str.isdigit()`` and ``str.isspace()``: those take
    U+00B2 for a digit and U+00A0 for a space and CPython takes
    neither.  A total past 2**63 - 1 is ``total struct size too long``,
    counted as the parser walks, so an over-long count is that error
    even with no code after it.  That grammar is shared verbatim with
    ``struct_unpack.py``: two inlined copies, one answer.
  * Whitespace is ignored where a format character is expected, so
    ``'< i i'`` is fine -- but a repeat count must be followed
    IMMEDIATELY by its code, so ``'<1 i'`` is ``bad char in struct
    format`` and a trailing count is ``repeat count given without format
    specifier``.  A count of ``0`` consumes no values and emits nothing.
  * ``bool`` is an ``int``, so ``pack('<i', True)`` is ``01000000``.

Errors: CPython raises ``struct.error``, which is a subclass of
``Exception`` but NOT of ``ValueError``; the subset has no custom
exceptions, so this port raises ``ValueError`` carrying the identical
message.  That exception TYPE is the one deliberate divergence in this
unit, and it is the only one -- every message below is CPython's own,
character for character.  Two things follow, and both are load-bearing.
``_error_message`` catches ``Exception`` and not ``ValueError``: the
narrow spelling looks right here, where nothing else is ever raised, and
stops catching the moment these same cases are run against the real
``struct``, which is when it matters.  And the type difference is
PRINTED, by ``_error_type`` at the end of the cases, rather than being
left to show itself by ending the run -- an abort compares one line and
silences every line under it.  CPython's own choice of type is not
uniform either: a bad format character is ``struct.error`` but a format
that will not encode to ASCII is ``UnicodeEncodeError``, and this port
answers ``ValueError`` to both.

Bigints: ``pack('<Q', v)`` for ``v > 2**63 - 1`` takes a bigint as
INPUT, so this unit needs the wider engine, and the cases below stand on
that boundary on purpose -- 2**63, 2**64 - 1, and the two different
out-of-range messages at 2**64.  Until 2026-09-17 they did not, and the
docstring's claim that the helper handled the region was false in both
engines: the encoder masked with ``& 0xFF`` and the range test shifted
with ``>> 64``, and the wider engine refuses ``bigint: a bitwise
operator on an integer past 64 bits`` (measured 2026-09-16).  Both are
arithmetic now, which the wider engine does serve, and the cases are
there so that no later reader has to take the claim on trust.
``struct_unpack.py`` is the mirror: there the bigint comes out as a
result instead of going in.  Everything else here, all of ``q``
included, stays inside signed 64 bits.  The helpers were checked against
the real module over 300,000 random (prefix, code, value) draws on
2026-09-16, covering the whole range on both sides of every boundary,
and the arithmetic fold that replaced the mask was re-checked the same
way on 2026-09-17.

Not covered: the native/aligned ``@`` mode (no prefix), which pads
between members and makes ``l``/``L`` eight bytes; the non-integer codes
``x c s p f d e ?``, which raise an explicit "not covered" ValueError
here rather than being silently mis-encoded; and ``pack_into``, which
needs a writable buffer and the subset has no ``bytearray``.
"""
# fills: struct.pack, struct.calcsize
# reference: struct

_STD_SIZE = {"b": 1, "B": 1, "h": 2, "H": 2, "i": 4,
             "I": 4, "l": 4, "L": 4, "q": 8, "Q": 8}
_OTHER_CODES = "xcspfde?"

# Written as a subtraction because the literal -9223372036854775808 is
# parsed as a negated 9223372036854775808, which is already a bigint.
_MIN_I64 = -9223372036854775807 - 1
_MAX_I64 = 9223372036854775807


# ---------------------------------------------------------------------
# The format grammar below is shared VERBATIM with the other struct unit
# in this corpus.  Units are inlined, never imported, so the two copies
# are two programs; a caller may take either, and they must answer the
# same.  Change one, change the other, byte for byte.
# ---------------------------------------------------------------------

# CPython's PY_SSIZE_T_MAX: the largest total a format may describe.
_MAX_SSIZE = 9223372036854775807
# Py_ISDIGIT and Py_ISSPACE are ASCII-only.  str.isdigit() and
# str.isspace() are not: they would take U+00B2 for a digit and U+00A0
# for a space, and CPython takes neither.
_ASCII_DIGITS = "0123456789"
_ASCII_SPACE = " \t\n\r\x0b\x0c"
_HEX_DIGITS = "0123456789abcdef"


def _escape_point(point):
    """One code point in the backslash spelling CPython's message uses."""
    if point < 256:
        marker = "x"
        width = 2
    elif point < 65536:
        marker = "u"
        width = 4
    else:
        marker = "U"
        width = 8
    out = ""
    place = width
    while place > 0:
        out = out + _HEX_DIGITS[(point // (16 ** (place - 1))) % 16]
        place = place - 1
    return "\\" + marker + out


def _require_ascii(fmt):
    """Reject what CPython's encode-to-ASCII rejects, before any parsing.

    CPython encodes the format to ASCII first, so this runs ahead of
    every other check -- ahead of the prefix, the codes and the counts.
    A single offending character is reported alone, a run of them as a
    run, and only then is an embedded NUL noticed.
    """
    index = 0
    total = len(fmt)
    while index < total:
        if ord(fmt[index]) > 127:
            end = index
            while end < total and ord(fmt[end]) > 127:
                end = end + 1
            if end - index == 1:
                raise ValueError(
                    "'ascii' codec can't encode character '"
                    + _escape_point(ord(fmt[index])) + "' in position "
                    + str(index) + ": ordinal not in range(128)")
            raise ValueError(
                "'ascii' codec can't encode characters in position "
                + str(index) + "-" + str(end - 1)
                + ": ordinal not in range(128)")
        index = index + 1
    if chr(0) in fmt:
        raise ValueError("embedded null character")


def _parse_format(fmt):
    """Return (little_endian, [(code, count), ...]) for a std-size format."""
    _require_ascii(fmt)
    if len(fmt) == 0 or (fmt[0] != "<" and fmt[0] != ">"
                         and fmt[0] != "!" and fmt[0] != "="):
        raise ValueError(
            "this port needs an explicit '<', '>', '!' or '=' prefix")
    # '=' is native byte order; every platform this corpus runs on is
    # little-endian, and the engine refuses sys.byteorder, so it is fixed.
    little = fmt[0] == "<" or fmt[0] == "="
    items = []
    size = 0
    index = 1
    total = len(fmt)
    while index < total:
        ch = fmt[index]
        if ch in _ASCII_SPACE:
            index = index + 1
            continue
        count = 1
        if ch in _ASCII_DIGITS:
            digits = ""
            while index < total and fmt[index] in _ASCII_DIGITS:
                digits = digits + fmt[index]
                index = index + 1
            count = int(digits)
            # CPython bounds the count while it is still reading the
            # digits, so a count past PY_SSIZE_T_MAX is a size error
            # even when no code follows it at all.
            if count > _MAX_SSIZE:
                raise ValueError("total struct size too long")
            if index >= total:
                raise ValueError("repeat count given without format specifier")
            ch = fmt[index]
        if ch not in _STD_SIZE:
            if ch in _OTHER_CODES:
                raise ValueError(
                    "this port covers only the integer codes"
                    " b B h H i I l L q Q, not '" + ch + "'")
            raise ValueError("bad char in struct format")
        # The running total is checked here, item by item, and not once
        # at the end, because that is where CPython checks it: '<'
        # followed by an over-long count, a 'b' and a 'z' is the size
        # error, not the bad-char one.
        size = size + _STD_SIZE[ch] * count
        if size > _MAX_SSIZE:
            raise ValueError("total struct size too long")
        items.append((ch, count))
        index = index + 1
    return little, items


def _items_size(items):
    """Bytes the parsed items occupy, standard sizes, no padding.

    No bound is repeated here: _parse_format has already refused any
    format whose running total passes PY_SSIZE_T_MAX.
    """
    total = 0
    for item in items:
        total = total + _STD_SIZE[item[0]] * item[1]
    return total


def calcsize(fmt):
    """Size in bytes of the struct described by `fmt`."""
    little, items = _parse_format(fmt)
    return _items_size(items)


def _fits_signed_long(value):
    """True when `value` survives CPython's PyLong_AsLong (C long)."""
    return value >= _MIN_I64 and value <= _MAX_I64


def _fits_unsigned_long(value):
    """True when `value` survives PyLong_AsUnsignedLong (C unsigned long).

    The upper bound is 2**64 - 1, and the test divides rather than
    shifting -- `(value >> 64) == 0` says the same thing but is a
    BITWISE operator, which the wider engine refuses once `value` is
    past 64 bits, which is exactly the case this test exists for.
    Dividing out the low 32 bits leaves at most 32 more.
    """
    return value >= 0 and value // 4294967296 <= 4294967295


def _range_message(code, value, little):
    """CPython's out-of-range message for `value` under `code`, or ''.

    Two gates, in CPython's order: the C conversion first, the code's own
    range second.  The wording of the second depends on the byte order.
    """
    if code == "b":
        if not _fits_signed_long(value):
            return "argument out of range"
        if value < -128 or value > 127:
            return "byte format requires -128 <= number <= 127"
    elif code == "B":
        if not _fits_signed_long(value):
            return "argument out of range"
        if value < 0 or value > 255:
            return "ubyte format requires 0 <= number <= 255"
    elif code == "h":
        if not _fits_signed_long(value):
            return "argument out of range"
        if value < -32768 or value > 32767:
            if little:
                return "short format requires -32768 <= number <= 32767"
            return "'h' format requires -32768 <= number <= 32767"
    elif code == "H":
        if little:
            # np_ushort converts through a SIGNED long, so -1 reaches
            # the range check and gets the wordy message.
            if not _fits_signed_long(value):
                return "argument out of range"
            if value < 0 or value > 65535:
                return "ushort format requires 0 <= number <= 65535"
        else:
            if not _fits_unsigned_long(value):
                return "argument out of range"
            if value > 65535:
                return "'H' format requires 0 <= number <= 65535"
    elif code == "i" or code == "l":
        if not _fits_signed_long(value):
            return "argument out of range"
        if value < -2147483648 or value > 2147483647:
            return ("'" + code
                    + "' format requires -2147483648 <= number <= 2147483647")
    elif code == "I" or code == "L":
        if not _fits_unsigned_long(value):
            return "argument out of range"
        if value > 4294967295:
            return "'" + code + "' format requires 0 <= number <= 4294967295"
    elif code == "q":
        if not _fits_signed_long(value):
            if little:
                return "argument out of range"
            return "int too large to convert"
    elif code == "Q":
        if not _fits_unsigned_long(value):
            if little:
                return "argument out of range"
            return "int too large to convert"
    return ""


def _append_int(out, code, value, little):
    """Append one packed integer to the list of byte values `out`."""
    if not isinstance(value, int):
        raise ValueError("required argument is not an integer")
    message = _range_message(code, value, little)
    if message:
        raise ValueError(message)
    size = _STD_SIZE[code]
    # `% 256` and `// 256` are `& 0xFF` and `>> 8` for every int,
    # negative ones included, because Python floors both -- so one loop
    # still serves the signed and the unsigned codes and no
    # `value + 2**(8*size)` correction is needed.  Arithmetic and not
    # bitwise because `pack('<Q', v)` for v > 2**63 - 1 hands this loop
    # a bigint, and the wider engine refuses a bitwise operator on one.
    chunk = []
    remaining = value
    for _step in range(size):
        chunk.append(remaining % 256)
        remaining = remaining // 256
    if little:
        for byte in chunk:
            out.append(byte)
    else:
        index = size - 1
        while index >= 0:
            out.append(chunk[index])
            index = index - 1


def pack(fmt, *values):
    """Pack `values` into bytes according to `fmt`."""
    little, items = _parse_format(fmt)
    expected = 0
    for item in items:
        expected = expected + item[1]
    if len(values) != expected:
        raise ValueError("pack expected %d items for packing (got %d)"
                         % (expected, len(values)))
    out = []
    taken = 0
    for item in items:
        for _repeat in range(item[1]):
            _append_int(out, item[0], values[taken], little)
            taken = taken + 1
    return bytes(out)


def pack_hex(fmt, *values):
    """pack() rendered as lower-case hex -- easier to read in a case."""
    return pack(fmt, *values).hex()


def _error_message(fn, *args):
    """The message of the exception fn(*args) raises, or '' if none.

    ``except Exception``, and the width is the whole point.  This port
    raises ``ValueError`` because the subset has no ``class``, but the
    module it stands in for raises ``struct.error``, which is a subclass
    of ``Exception`` and NOT of ``ValueError``.  A narrower catch reads
    the same in this file and stops catching the moment these cases are
    run against the real ``struct``: the first failure escapes, the run
    ends there, and every case below it is compared against nothing.
    The width hides no type difference, because ``_error_type`` below
    prints exactly that.
    """
    try:
        fn(*args)
    except Exception as exc:
        return str(exc)
    return ""


def _error_type(fn, *args):
    """The NAME of the exception type fn(*args) raises, or '' if none.

    The deliberate divergence, printed instead of raised.  Every failure
    in this port is a ``ValueError``; the module it stands in for uses
    two different types for the same failures -- ``struct.error``, whose
    ``__name__`` is ``error``, and ``UnicodeEncodeError`` for a format
    that will not encode -- and the port flattens both into one.  That
    is a fact about the port, so the cases print it rather than letting
    it end the run.
    """
    try:
        fn(*args)
    except Exception as exc:
        return type(exc).__name__
    return ""


# --- cases ---
# Standard sizes: no padding, and l/L are four bytes, not eight.
print(calcsize("<b"), calcsize("<B"), calcsize("<h"), calcsize("<H"))
print(calcsize("<i"), calcsize("<I"), calcsize("<l"), calcsize("<L"))
print(calcsize("<q"), calcsize("<Q"))
print(calcsize(">i"), calcsize("!i"), calcsize("=i"))
print(calcsize("<"), calcsize("<0i"), calcsize("<3i"), calcsize("<2q"))
print(calcsize("<bi"), calcsize("<ib"), calcsize("<10b"), calcsize("<bqb"))
print(calcsize("< i i"), calcsize("<i i"), calcsize("<ii "), calcsize("< 1i"))
print(calcsize("<\ti"), calcsize("<i\n"), calcsize("< "))

# One byte, signed and unsigned, at both ends.
print(pack_hex("<b", 0), pack_hex("<b", 1), pack_hex("<b", -1))
print(pack_hex("<b", 127), pack_hex("<b", -128))
print(pack_hex("<B", 0), pack_hex("<B", 255), pack_hex("<B", 128))
print(pack_hex(">b", -128), pack_hex("!B", 255))

# Two bytes: the endian prefixes start to matter.
print(pack_hex("<h", 1), pack_hex(">h", 1), pack_hex("!h", 1), pack_hex("=h", 1))
print(pack_hex("<h", -1), pack_hex(">h", -1))
print(pack_hex("<h", 32767), pack_hex("<h", -32768))
print(pack_hex(">h", 32767), pack_hex(">h", -32768))
print(pack_hex("<H", 0), pack_hex("<H", 65535), pack_hex(">H", 65535))
print(pack_hex("<h", 258), pack_hex(">h", 258))

# Four bytes: i and l are the same under a standard-size prefix.
print(pack_hex("<i", 1), pack_hex(">i", 1), pack_hex("=i", 1), pack_hex("!i", 1))
print(pack_hex("<i", -1), pack_hex(">i", -1))
print(pack_hex("<i", 2147483647), pack_hex("<i", -2147483648))
print(pack_hex(">i", 2147483647), pack_hex(">i", -2147483648))
print(pack_hex("<l", 305419896), pack_hex(">l", 305419896))
print(pack_hex("<i", 305419896) == pack_hex("<l", 305419896))
print(pack_hex("<I", 0), pack_hex("<I", 4294967295), pack_hex(">I", 4294967295))
print(pack_hex("<L", 3735928559), pack_hex(">L", 3735928559))
print(pack_hex("<I", 3735928559) == pack_hex("<L", 3735928559))

# Eight bytes.
print(pack_hex("<q", 0), pack_hex("<q", 1), pack_hex(">q", 1))
print(pack_hex("<q", -1), pack_hex(">q", -1))
print(pack_hex("<q", 9223372036854775807), pack_hex(">q", 9223372036854775807))
print(pack_hex("<q", -9223372036854775807 - 1))
print(pack_hex(">q", -9223372036854775807 - 1))
print(pack_hex("<Q", 0), pack_hex("<Q", 9223372036854775807))
print(pack_hex(">Q", 9223372036854775807))
print(pack_hex("<Q", 1311768467463790320), pack_hex(">Q", 1311768467463790320))
print(pack_hex("<q", -2), pack_hex(">q", -2))
print(pack_hex("<q", -4294967296), pack_hex(">q", -4294967296))

# The full unsigned width: above 2**63 - 1 the VALUE is a bigint on the
# way in, which is the boundary this unit exists to stand on.
print(pack_hex("<Q", 9223372036854775808), pack_hex(">Q", 9223372036854775808))
print(pack_hex("<Q", 18446744073709551615))
print(pack_hex(">Q", 18446744073709551615))
print(pack_hex("<Q", 18446744073709551614), pack_hex(">Q", 18446744073709551614))
print(pack_hex("<Q", 9223372036854775807 + 1)
      == pack_hex("<Q", 9223372036854775808))
print(pack_hex("<Q", 12297829382473034410))
print(pack_hex(">Q", 12297829382473034410))
# One past the top, and one past the bottom: two messages each way.
print(repr(_error_message(pack, "<Q", 18446744073709551616)))
print(repr(_error_message(pack, ">Q", 18446744073709551616)))
print(repr(_error_message(pack, "<Q", -9223372036854775809)))
print(repr(_error_message(pack, ">Q", -9223372036854775809)))
print(repr(_error_message(pack, "<q", 9223372036854775808)))
print(repr(_error_message(pack, ">q", 9223372036854775808)))
print(repr(_error_message(pack, "<Q", 18446744073709551615)))
for _code in ["b", "B", "h", "H", "i", "I", "l", "L", "q", "Q"]:
    print(_code,
          repr(_error_message(pack, "<" + _code, 18446744073709551615)),
          repr(_error_message(pack, ">" + _code, 18446744073709551615)))
for _code in ["b", "B", "h", "H", "i", "I", "l", "L", "q", "Q"]:
    print(_code,
          repr(_error_message(pack, "<" + _code, 18446744073709551616)),
          repr(_error_message(pack, ">" + _code, 18446744073709551616)))

# Repeat counts, several codes, and the empty format.
print(pack_hex("<3i", 1, 2, 3))
print(pack_hex(">3i", 1, 2, 3))
print(pack_hex("<2h2B", 1, -1, 0, 255))
print(pack_hex("<bhiq", -1, -1, -1, -1))
print(pack_hex(">bhiq", 1, 1, 1, 1))
print(repr(pack("<")), repr(pack("<0i")), repr(pack("<0i0b")))
print(pack_hex("< i i", 1, 2))
print(pack_hex("<10b", 0, 1, 2, 3, 4, 5, 6, 7, 8, 9))

# bool is an int, so it packs as 0 or 1.
print(pack_hex("<i", True), pack_hex("<i", False))
print(pack_hex("<B", True), pack_hex("<B", False))

# Range errors: four different message shapes, all from CPython.
for _prefix in ["<", ">", "!", "="]:
    print(_prefix, repr(_error_message(pack, _prefix + "b", 128)),
          repr(_error_message(pack, _prefix + "b", -129)))
for _prefix in ["<", ">", "!", "="]:
    print(_prefix, repr(_error_message(pack, _prefix + "B", 256)),
          repr(_error_message(pack, _prefix + "B", -1)))
for _prefix in ["<", ">", "!", "="]:
    print(_prefix, repr(_error_message(pack, _prefix + "h", 32768)))
    print(_prefix, repr(_error_message(pack, _prefix + "h", -32769)))
for _prefix in ["<", ">", "!", "="]:
    print(_prefix, repr(_error_message(pack, _prefix + "H", 65536)))
    print(_prefix, repr(_error_message(pack, _prefix + "H", -1)))
for _prefix in ["<", ">", "!", "="]:
    print(_prefix, repr(_error_message(pack, _prefix + "i", 2147483648)))
    print(_prefix, repr(_error_message(pack, _prefix + "i", -2147483649)))
    print(_prefix, repr(_error_message(pack, _prefix + "l", 2147483648)))
    print(_prefix, repr(_error_message(pack, _prefix + "l", -2147483649)))
for _prefix in ["<", ">", "!", "="]:
    print(_prefix, repr(_error_message(pack, _prefix + "I", 4294967296)))
    print(_prefix, repr(_error_message(pack, _prefix + "I", -1)))
    print(_prefix, repr(_error_message(pack, _prefix + "L", 4294967296)))
    print(_prefix, repr(_error_message(pack, _prefix + "L", -1)))
for _prefix in ["<", ">", "!", "="]:
    print(_prefix, repr(_error_message(pack, _prefix + "q", -1)))
    print(_prefix, repr(_error_message(pack, _prefix + "Q", -1)))
    print(_prefix, repr(_error_message(pack, _prefix + "Q", -4294967296)))
print(repr(_error_message(pack, "<q", 9223372036854775807)))
print(repr(_error_message(pack, "<Q", 9223372036854775807)))
print(repr(_error_message(pack, "<b", 127)))

# The C conversion runs BEFORE the code's own range check, so a value
# outside the C type never reaches the pretty message.
for _code in ["b", "B", "h", "H", "i", "I", "l", "L", "q", "Q"]:
    print(_code,
          repr(_error_message(pack, "<" + _code, 9223372036854775807)),
          repr(_error_message(pack, ">" + _code, 9223372036854775807)))
for _code in ["b", "B", "h", "H", "i", "I", "l", "L", "q", "Q"]:
    print(_code,
          repr(_error_message(pack, "<" + _code, -9223372036854775807 - 1)),
          repr(_error_message(pack, ">" + _code, -9223372036854775807 - 1)))
for _code in ["b", "B", "h", "H", "i", "I", "l", "L", "q", "Q"]:
    print(_code,
          repr(_error_message(pack, "<" + _code, -1099511627776)),
          repr(_error_message(pack, ">" + _code, -1099511627776)))

# Argument-count and argument-type errors.
print(repr(_error_message(pack, "<i")))
print(repr(_error_message(pack, "<i", 1, 2)))
print(repr(_error_message(pack, "<3i", 1, 2)))
print(repr(_error_message(pack, "<", 1)))
print(repr(_error_message(pack, "<0i", 1)))
print(repr(_error_message(pack, "<i", 1.5)))
print(repr(_error_message(pack, "<i", "1")))
print(repr(_error_message(pack, "<i", None)))

# Format errors.
print(repr(_error_message(calcsize, "<z")))
print(repr(_error_message(calcsize, "<b<")))
print(repr(_error_message(calcsize, "<n")))
print(repr(_error_message(calcsize, "<N")))
print(repr(_error_message(calcsize, "<P")))
print(repr(_error_message(calcsize, "<3")))
print(repr(_error_message(calcsize, "<i3")))
print(repr(_error_message(calcsize, "<1 i")))

# The format is ASCII and CPython encodes it before it parses it, so a
# character above U+007F loses to the encoder -- ahead of the prefix,
# the codes and the counts -- and a run of them is reported as one run.
print(repr(_error_message(calcsize, "<" + chr(178) + "i")))
print(repr(_error_message(calcsize, "<" + chr(178) + chr(179) + "i")))
print(repr(_error_message(calcsize, "<" + chr(128) + chr(128) + chr(128))))
print(repr(_error_message(calcsize, "<" + chr(178) + "i" + chr(179))))
print(repr(_error_message(calcsize, "<i" + chr(133))))
print(repr(_error_message(calcsize, chr(178) + "i")))
print(repr(_error_message(calcsize, "<" + chr(4660) + "i")))
print(repr(_error_message(calcsize, "<" + chr(55295) + "i")))
print(repr(_error_message(calcsize, "<" + chr(1114111) + "i")))
print(repr(_error_message(calcsize, "<" + chr(0) + "i")))
print(repr(_error_message(calcsize, "<i" + chr(0))))
print(repr(_error_message(calcsize, "<" + chr(0) + chr(178))))

# str.isdigit() and str.isspace() are Unicode-wide; this grammar is not.
print(repr(_error_message(calcsize, "<" + chr(1635) + "i")))
print(repr(_error_message(calcsize, "<i" + chr(160) + "i")))
print(repr(_error_message(calcsize, "<i" + chr(8199) + "i")))
print(calcsize("<i" + chr(11) + "i"), calcsize("<i" + chr(12) + "i"))
print(repr(_error_message(calcsize, "<i" + chr(28) + "i")))
print(repr(_error_message(calcsize, "<" + chr(127) + "i")))

# The total is bounded by PY_SSIZE_T_MAX, counted as the parser walks.
print(calcsize("<9223372036854775807b"), calcsize("<9223372036854775806b1b"))
print(calcsize("<1152921504606846975q"), calcsize("<4611686018427387903h"))
print(calcsize("<00000000000000000000000000000001b"))
print(calcsize("<09223372036854775807b"))
print(repr(_error_message(calcsize, "<9223372036854775808b")))
print(repr(_error_message(calcsize, "<9223372036854775806b2b")))
print(repr(_error_message(calcsize, "<1152921504606846976q")))
print(repr(_error_message(calcsize, "<9223372036854775807q")))
print(repr(_error_message(calcsize, "<4611686018427387904h")))
print(repr(_error_message(calcsize, "<" + "9" * 30 + "b")))
# An over-long count outruns both the missing-specifier check and the
# bad-char one; a count that fits does not.
print(repr(_error_message(calcsize, "<9223372036854775808")))
print(repr(_error_message(calcsize, "<9223372036854775807")))
print(repr(_error_message(calcsize, "<9223372036854775808bz")))
print(repr(_error_message(calcsize, "<9223372036854775807bz")))

# Every code, every prefix, one value: the whole grid in one line each.
for _code in ["b", "B", "h", "H", "i", "I", "l", "L", "q", "Q"]:
    print(_code, calcsize("<" + _code),
          pack_hex("<" + _code, 1), pack_hex(">" + _code, 1),
          pack_hex("!" + _code, 1), pack_hex("=" + _code, 1))

# The one deliberate divergence, printed rather than raised.  Every line
# above compares a MESSAGE with the real `struct`; these compare the
# TYPE, which is the single thing this port cannot match -- `class
# E(Exception)` is refused on both engines, so every failure here is a
# ValueError.  CPython uses two types for the same failures, `error`
# (that is `struct.error`) and `UnicodeEncodeError`, and this port
# flattens both into one.  Printing it is what keeps the divergence
# demonstrated instead of ending the run at the first range error.
print("pack <Q 2**64", _error_type(pack, "<Q", 18446744073709551616))
print("pack >Q 2**64", _error_type(pack, ">Q", 18446744073709551616))
print("pack <b 128", _error_type(pack, "<b", 128))
print("pack <i 1.5", _error_type(pack, "<i", 1.5))
print("pack <i (no value)", _error_type(pack, "<i"))
print("calcsize <z", _error_type(calcsize, "<z"))
print("calcsize <NUL i", _error_type(calcsize, "<" + chr(0) + "i"))
print("calcsize <U+00B2 i", _error_type(calcsize, "<" + chr(178) + "i"))
print("pack <i 1", _error_type(pack, "<i", 1))
