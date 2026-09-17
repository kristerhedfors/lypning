"""struct.unpack and struct.unpack_from for the integer codes.

The engine refuses ``import struct``, so decoding a wire format has to be
built out of indexing and arithmetic.  This is a port of CPython's
``struct.unpack`` / ``struct.unpack_from`` (Modules/_struct.c) restricted
to the ten integer codes ``b B h H i I l L q Q`` under the four
**standard-size** byte-order prefixes ``<`` ``>`` ``!`` ``=``.  Under
those prefixes there is no alignment padding and ``l``/``L`` are four
bytes, not the native eight.  ``struct_pack.py`` is the other half of the
same surface.

The six details worth having in front of you:

  * ``unpack`` demands the buffer be **exactly** the right length -- too
    long is as much an error as too short, and the message says
    ``unpack requires a buffer of 4 bytes`` either way.  ``unpack_from``
    is the one that tolerates trailing data.
  * A signed value is decoded by sign-extending the FIRST byte and
    folding the rest in with ``value * 256 + byte``, not by building the
    unsigned value and subtracting ``2**(8*size)``.  That is not a
    flourish: the unsigned form of a signed 64-bit value overflows the
    core engine's integers, while this form never leaves them, so ``q``
    decodes on the core engine and only ``Q`` above ``2**63 - 1`` needs
    the wider one.  Sign extension is linear, so the multiply-add is
    exactly the two's-complement answer at every step.
  * The fold is ``* 256 +`` and not ``<< 8 |`` for a second reason,
    measured on 2026-09-16: the wider engine carries bigints but refuses
    a BITWISE operator on an integer past 64 bits
    (``bigint: a bitwise operator on an integer past 64 bits``), so the
    shift-and-or spelling of this same loop runs on neither engine once
    ``Q`` crosses ``2**63``.  Arithmetic runs on both.
  * ``unpack_from``'s offset arithmetic has three separate failures, in
    this order: a negative offset with too little room behind it is
    ``not enough data to unpack 8 bytes at offset -4``; a negative offset
    past the start of the buffer is ``offset -10 out of range for 8-byte
    buffer``; and only a NON-negative offset can reach the long
    ``unpack_from requires a buffer of at least ...`` message.  Once the
    first two have passed, a negative offset always has room, so the
    third can never see one.
  * ``unpack`` returns a tuple even for one value, and a count of ``0``
    contributes nothing, so ``unpack('<0i', b'')`` is ``()``.
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
    ``struct_pack.py``: two inlined copies, one answer.

Errors: CPython raises ``struct.error``, which is a subclass of
``Exception`` but NOT of ``ValueError``; the subset has no custom
exceptions, so this port raises ``ValueError`` carrying the identical
message.  That exception TYPE is the one deliberate divergence here.

``unpack_all`` is ``struct.iter_unpack`` with the iterator materialised
into a list, because the subset has no generators; it keeps CPython's two
error messages exactly.  Callers that only loop over the result cannot
tell the difference; callers that expect laziness can.

Not covered: the native/aligned ``@`` mode (no prefix), which pads
between members and makes ``l``/``L`` eight bytes; the non-integer codes
``x c s p f d e ?``, which raise an explicit "not covered" ValueError
here rather than being silently mis-decoded; and ``Struct`` objects,
which would need a class.

Bigints: an unsigned 64-bit value above ``2**63 - 1`` is a bigint, so the
full-width ``Q`` cases below put this unit on the wider engine.  That is
the point of having them: they are where the 64-bit boundary of the core
engine actually falls on this surface, and everything else here --
including all of ``q`` -- stays below it.
"""
# fills: struct.unpack, struct.unpack_from, struct.iter_unpack, struct.calcsize
# reference: struct

_STD_SIZE = {"b": 1, "B": 1, "h": 2, "H": 2, "i": 4,
             "I": 4, "l": 4, "L": 4, "q": 8, "Q": 8}
_SIGNED = {"b": True, "B": False, "h": True, "H": False, "i": True,
           "I": False, "l": True, "L": False, "q": True, "Q": False}
_OTHER_CODES = "xcspfde?"


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


def _read_int(data, start, size, little, signed):
    """Decode one integer of `size` bytes starting at `start`.

    The first byte read is sign-extended and the rest are folded in with
    `value * 256 + byte`, so no intermediate ever exceeds the width of
    the result.  Building the unsigned value first and subtracting
    2**(8*size) would push a signed 64-bit decode through a 65-bit
    intermediate; spelling the fold `(value << 8) | byte` would hit the
    wider engine's refusal of bitwise operators past 64 bits.
    """
    if little:
        index = start + size - 1
        step = -1
    else:
        index = start
        step = 1
    byte = data[index]
    if signed and byte >= 128:
        value = byte - 256
    else:
        value = byte
    for _step in range(size - 1):
        index = index + step
        value = value * 256 + data[index]
    return value


def _read_items(items, data, offset, little):
    """Decode every item, returning a tuple in format order."""
    out = []
    position = offset
    for item in items:
        code = item[0]
        size = _STD_SIZE[code]
        signed = _SIGNED[code]
        for _repeat in range(item[1]):
            out.append(_read_int(data, position, size, little, signed))
            position = position + size
    return tuple(out)


def unpack(fmt, data):
    """Decode `data`, which must be exactly the size `fmt` describes."""
    little, items = _parse_format(fmt)
    size = _items_size(items)
    if len(data) != size:
        raise ValueError("unpack requires a buffer of %d bytes" % size)
    return _read_items(items, data, 0, little)


def unpack_from(fmt, buffer, offset=0):
    """Decode at `offset` in `buffer`; trailing bytes are allowed."""
    little, items = _parse_format(fmt)
    size = _items_size(items)
    if offset < 0:
        # CPython's three gates, in CPython's order.
        if offset + size > 0:
            raise ValueError("not enough data to unpack %d bytes at offset %d"
                             % (size, offset))
        if offset + len(buffer) < 0:
            raise ValueError("offset %d out of range for %d-byte buffer"
                             % (offset, len(buffer)))
        offset = offset + len(buffer)
    if len(buffer) - offset < size:
        raise ValueError(
            "unpack_from requires a buffer of at least %d bytes for"
            " unpacking %d bytes at offset %d (actual buffer size is %d)"
            % (size + offset, size, offset, len(buffer)))
    return _read_items(items, buffer, offset, little)


def unpack_all(fmt, data):
    """struct.iter_unpack, materialised: a list of tuples, not a generator."""
    little, items = _parse_format(fmt)
    size = _items_size(items)
    if size == 0:
        raise ValueError("cannot iteratively unpack with a struct of length 0")
    if len(data) % size != 0:
        raise ValueError(
            "iterative unpacking requires a buffer of a multiple of %d bytes"
            % size)
    out = []
    offset = 0
    while offset < len(data):
        out.append(_read_items(items, data, offset, little))
        offset = offset + size
    return out


def _error_message(fn, *args):
    """The message of the ValueError fn(*args) raises, or '' if none."""
    try:
        fn(*args)
    except ValueError as exc:
        return str(exc)
    return ""


# --- cases ---
# One byte, signed and unsigned, across the sign boundary.
print(unpack("<b", bytes([0])), unpack("<b", bytes([1])), unpack("<b", bytes([127])))
print(unpack("<b", bytes([128])), unpack("<b", bytes([255])))
print(unpack("<B", bytes([0])), unpack("<B", bytes([128])), unpack("<B", bytes([255])))
print(unpack(">b", bytes([255])), unpack("!B", bytes([255])))

# Two bytes: the prefix decides which end is which.
print(unpack("<h", bytes([1, 0])), unpack(">h", bytes([1, 0])))
print(unpack("!h", bytes([1, 0])), unpack("=h", bytes([1, 0])))
print(unpack("<h", bytes([255, 255])), unpack(">h", bytes([255, 255])))
print(unpack("<h", bytes([255, 127])), unpack("<h", bytes([0, 128])))
print(unpack(">h", bytes([127, 255])), unpack(">h", bytes([128, 0])))
print(unpack("<H", bytes([255, 255])), unpack(">H", bytes([0, 1])))
print(unpack("<h", bytes([254, 255])), unpack(">h", bytes([255, 254])))

# Four bytes: i and l agree under a standard-size prefix.
print(unpack("<i", bytes([1, 0, 0, 0])), unpack(">i", bytes([1, 0, 0, 0])))
print(unpack("<i", bytes([255, 255, 255, 255])))
print(unpack("<i", bytes([255, 255, 255, 127])), unpack("<i", bytes([0, 0, 0, 128])))
print(unpack(">i", bytes([127, 255, 255, 255])), unpack(">i", bytes([128, 0, 0, 0])))
print(unpack("<I", bytes([255, 255, 255, 255])), unpack("<I", bytes([0, 0, 0, 128])))
print(unpack("<l", bytes([120, 86, 52, 18])), unpack(">l", bytes([18, 52, 86, 120])))
print(unpack("<l", bytes([120, 86, 52, 18])) == unpack("<i", bytes([120, 86, 52, 18])))
print(unpack("<L", bytes([239, 190, 173, 222])), unpack(">L", bytes([222, 173, 190, 239])))

# Eight bytes, signed: every intermediate stays inside 64 bits.
print(unpack("<q", bytes([0, 0, 0, 0, 0, 0, 0, 0])))
print(unpack("<q", bytes([1, 0, 0, 0, 0, 0, 0, 0])))
print(unpack("<q", bytes([255, 255, 255, 255, 255, 255, 255, 255])))
print(unpack("<q", bytes([255, 255, 255, 255, 255, 255, 255, 127])))
print(unpack("<q", bytes([0, 0, 0, 0, 0, 0, 0, 128])))
print(unpack(">q", bytes([127, 255, 255, 255, 255, 255, 255, 255])))
print(unpack(">q", bytes([128, 0, 0, 0, 0, 0, 0, 0])))
print(unpack("<q", bytes([254, 255, 255, 255, 255, 255, 255, 255])))
print(unpack("<Q", bytes([0, 0, 0, 0, 0, 0, 0, 0])))
print(unpack("<Q", bytes([255, 255, 255, 255, 255, 255, 255, 127])))

# Eight bytes, unsigned, above 2**63 - 1: this is the bigint boundary.
print(unpack("<Q", bytes([255, 255, 255, 255, 255, 255, 255, 255])))
print(unpack(">Q", bytes([255, 255, 255, 255, 255, 255, 255, 255])))
print(unpack("<Q", bytes([0, 0, 0, 0, 0, 0, 0, 128])))
print(unpack(">Q", bytes([128, 0, 0, 0, 0, 0, 0, 0])))
print(unpack("<Q", bytes([255, 255, 255, 255, 255, 255, 255, 255]))[0]
      - unpack("<Q", bytes([255, 255, 255, 255, 255, 255, 255, 127]))[0])

# Repeat counts, several codes, and the empty format.
print(unpack("<3i", bytes([1, 0, 0, 0, 2, 0, 0, 0, 3, 0, 0, 0])))
print(unpack(">3i", bytes([0, 0, 0, 1, 0, 0, 0, 2, 0, 0, 0, 3])))
print(unpack("<2h2B", bytes([1, 0, 255, 255, 0, 255])))
print(unpack("<bhi", bytes([255, 255, 255, 255, 255, 255, 255])))
print(unpack("<", b""), unpack("<0i", b""), unpack("<0i0b", b""))
print(unpack("< i i", bytes([1, 0, 0, 0, 2, 0, 0, 0])))
print(unpack(">3B", bytes([1, 2, 3])))
print(calcsize("<3i"), calcsize("<2q"), calcsize("<bqb"), calcsize("<"))

# unpack wants an exact fit, in both directions.
print(repr(_error_message(unpack, "<i", b"abc")))
print(repr(_error_message(unpack, "<i", b"abcde")))
print(repr(_error_message(unpack, "<b", b"")))
print(repr(_error_message(unpack, "<", b"a")))
print(repr(_error_message(unpack, "<q", bytes([0, 0, 0, 0]))))

# unpack_from tolerates trailing bytes and understands a negative offset.
print(unpack_from("<i", bytes([1, 2, 3, 4, 5, 6, 7, 8])))
print(unpack_from("<i", bytes([1, 2, 3, 4, 5, 6, 7, 8]), 0))
print(unpack_from("<i", bytes([1, 2, 3, 4, 5, 6, 7, 8]), 4))
print(unpack_from("<i", bytes([1, 2, 3, 4, 5, 6, 7, 8]), -4))
print(unpack_from("<h", bytes([1, 2, 3, 4, 5, 6, 7, 8]), -2))
print(unpack_from("<q", bytes([1, 2, 3, 4, 5, 6, 7, 8]), -8))
print(unpack_from("<b", bytes([1, 2, 3, 4, 5, 6, 7, 8]), 7))
print(unpack_from("<b", bytes([1, 2, 3, 4, 5, 6, 7, 8]), -1))
print(unpack_from("<2h", bytes([1, 2, 3, 4, 5, 6, 7, 8]), 2))

# Its three failures, in CPython's order.
print(repr(_error_message(unpack_from, "<i", bytes([1, 2, 3, 4, 5, 6, 7, 8]), 5)))
print(repr(_error_message(unpack_from, "<i", bytes([1, 2, 3, 4, 5, 6, 7, 8]), 8)))
print(repr(_error_message(unpack_from, "<q", bytes([1, 2, 3, 4, 5, 6, 7, 8]), 1)))
print(repr(_error_message(unpack_from, "<q", bytes([1, 2, 3, 4, 5, 6, 7, 8]), -4)))
print(repr(_error_message(unpack_from, "<i", bytes([1, 2, 3, 4, 5, 6, 7, 8]), -1)))
print(repr(_error_message(unpack_from, "<i", bytes([1, 2, 3, 4, 5, 6, 7, 8]), -9)))
print(repr(_error_message(unpack_from, "<i", bytes([1, 2, 3, 4, 5, 6, 7, 8]), -10)))
print(repr(_error_message(unpack_from, "<i", b"", 0)))
print(repr(_error_message(unpack_from, "<i", b"", 1)))
print(repr(_error_message(unpack_from, "<i", b"", -1)))

# iter_unpack, materialised.
print(unpack_all("<h", bytes([1, 0, 2, 0])))
print(unpack_all("<2h", bytes([1, 0, 2, 0])))
print(unpack_all("<h", b""))
print(unpack_all(">i", bytes([0, 0, 0, 1, 0, 0, 0, 2])))
print(unpack_all("<B", bytes([0, 127, 128, 255])))
print(repr(_error_message(unpack_all, "<", b"")))
print(repr(_error_message(unpack_all, "<0i", b"")))
print(repr(_error_message(unpack_all, "<i", b"abcde")))
print(repr(_error_message(unpack_all, "<h", bytes([1]))))

# Format errors, the same grammar as pack's.
print(repr(_error_message(calcsize, "<z")))
print(repr(_error_message(calcsize, "<b<")))
print(repr(_error_message(calcsize, "<n")))
print(repr(_error_message(calcsize, "<N")))
print(repr(_error_message(calcsize, "<P")))
print(repr(_error_message(calcsize, "<3")))
print(repr(_error_message(calcsize, "<i3")))
print(repr(_error_message(calcsize, "<1 i")))
print(repr(_error_message(unpack, "<z", b"")))

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

# Every code, every prefix, decoding the same all-ones bytes.
for _code in ["b", "B", "h", "H", "i", "I", "l", "L", "q", "Q"]:
    _size = calcsize("<" + _code)
    _ones = bytes([255 for _k in range(_size)])
    _low = bytes([1] + [0 for _k in range(_size - 1)])
    print(_code, _size,
          unpack("<" + _code, _ones)[0], unpack(">" + _code, _ones)[0],
          unpack("<" + _code, _low)[0], unpack(">" + _code, _low)[0],
          unpack("!" + _code, _low)[0], unpack("=" + _code, _low)[0])
