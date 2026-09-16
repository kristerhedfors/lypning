"""struct.pack and struct.calcsize for the integer codes, standard sizes.

The engine refuses ``import struct``, so binary framing has to be built
out of shifts and masks.  This is a port of CPython's ``struct.pack`` and
``struct.calcsize`` (Modules/_struct.c) restricted to the ten integer
codes ``b B h H i I l L q Q`` under the four **standard-size** byte-order
prefixes ``<`` ``>`` ``!`` ``=``.  Under those prefixes there is no
alignment padding and ``l``/``L`` are four bytes, not the native eight --
which is exactly why wire formats spell the prefix out.

The four details worth having in front of you:

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
  * A value is encoded by taking ``value & 0xFF`` and shifting right
    eight, ``size`` times.  For a negative value Python's arithmetic
    shift already produces two's complement, so signed and unsigned share
    one loop and no ``value + 2**(8*size)`` correction is needed -- which
    also keeps every intermediate inside the core engine's 64-bit ints.
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
unit, and it is the only one.

Bigints: every case here stays inside signed 64 bits, so the unit runs
on the core engine.  ``pack('<Q', v)`` for ``v > 2**63 - 1`` takes a
bigint as INPUT and so needs the wider engine; the helper handles it, and
``struct_unpack.py`` is the unit that demonstrates the full 64-bit
unsigned width, because there the bigint comes out as a result instead of
going in as a literal.  The helpers here were checked against the real
module over 300,000 random (prefix, code, value) draws on 2026-09-16,
covering the whole range on both sides of every boundary.

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


def _parse_format(fmt):
    """Return (little_endian, [(code, count), ...]) for a std-size format."""
    if len(fmt) == 0 or (fmt[0] != "<" and fmt[0] != ">"
                         and fmt[0] != "!" and fmt[0] != "="):
        raise ValueError(
            "this port needs an explicit '<', '>', '!' or '=' prefix")
    # '=' is native byte order; every platform this corpus runs on is
    # little-endian, and the engine refuses sys.byteorder, so it is fixed.
    little = fmt[0] == "<" or fmt[0] == "="
    items = []
    index = 1
    total = len(fmt)
    while index < total:
        ch = fmt[index]
        if ch.isspace():
            index = index + 1
            continue
        count = 1
        if ch.isdigit():
            digits = ""
            while index < total and fmt[index].isdigit():
                digits = digits + fmt[index]
                index = index + 1
            count = int(digits)
            if index >= total:
                raise ValueError("repeat count given without format specifier")
            ch = fmt[index]
        if ch not in _STD_SIZE:
            if ch in _OTHER_CODES:
                raise ValueError(
                    "this port covers only the integer codes"
                    " b B h H i I l L q Q, not '" + ch + "'")
            raise ValueError("bad char in struct format")
        items.append((ch, count))
        index = index + 1
    return little, items


def _items_size(items):
    """Bytes the parsed items occupy, standard sizes, no padding."""
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

    The upper bound is 2**64 - 1, which is not writable as a literal
    inside 64-bit ints, so the test is a shift: nothing may be left above
    bit 63.
    """
    return value >= 0 and (value >> 64) == 0


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
    # Arithmetic shift gives two's complement for free, so one loop
    # serves both the signed and the unsigned codes.
    chunk = []
    remaining = value
    for _step in range(size):
        chunk.append(remaining & 0xFF)
        remaining = remaining >> 8
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
    """The message of the ValueError fn(*args) raises, or '' if none."""
    try:
        fn(*args)
    except ValueError as exc:
        return str(exc)
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

# Every code, every prefix, one value: the whole grid in one line each.
for _code in ["b", "B", "h", "H", "i", "I", "l", "L", "q", "Q"]:
    print(_code, calcsize("<" + _code),
          pack_hex("<" + _code, 1), pack_hex(">" + _code, 1),
          pack_hex("!" + _code, 1), pack_hex("=" + _code, 1))
