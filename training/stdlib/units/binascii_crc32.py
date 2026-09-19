"""binascii.crc32 and zlib.crc32 -- the same CRC-32, built from scratch.

The engine serves neither ``binascii`` nor ``zlib``, so the checksum has
to be computed here.  This is the CRC-32 of IEEE 802.3 / PKZIP that both
CPython functions compute: the reflected algorithm with polynomial
0xEDB88320 (the bit-reversal of 0x04C11DB7), initial register all ones,
and a final ones complement.

``binascii.crc32`` and ``zlib.crc32`` are the same function with the same
signature and the same answers; ``binascii.crc32`` is documented as an
alias in everything but name.  Both are covered here.

The three details worth having in front of you:

  * The result is **unsigned**.  Since Python 3.0 both functions return a
    value in ``0 .. 0xFFFFFFFF``; the old signed result is gone.  Every
    intermediate here is kept inside 32 bits with ``& 0xFFFFFFFF``, which
    is also what keeps the unit inside the core engine's signed 64-bit
    integers.
  * The ``value`` argument is the **running CRC**, not a seed for the
    register: it is masked to 32 bits and then complemented back into the
    register, so ``crc32(b, crc32(a)) == crc32(a + b)``.  That is what
    makes streaming work, and it is why ``crc32(data, -1)`` equals
    ``crc32(data, 0xFFFFFFFF)`` and ``crc32(data, 2**32 + 5)`` equals
    ``crc32(data, 5)``.
  * The 256-entry table is a precomputation, not the definition.  It is
    built here by a loop at first use rather than pasted in as a literal,
    and ``_crc32_bitwise`` recomputes the same answer eight bits at a
    time so the two can be checked against each other.

Errors: CPython raises ``TypeError`` for a str argument; the subset has
no ``TypeError`` name to raise, so this port simply documents that
``data`` must be bytes.  Nothing else about this surface raises.

Not covered: ``zlib.adler32`` (a different checksum), and
``zlib.crc32_combine`` (3.13+, not present in the 3.11 oracle).
"""
# fills: binascii.crc32, zlib.crc32
# reference: binascii

_CRC32_POLY = 0xEDB88320
_MASK32 = 0xFFFFFFFF

# A one-element list is this subset's memo cell: no classes, no nonlocal.
_TABLE_CACHE = []


def _crc_table():
    """The 256-entry CRC-32 table, built once by a loop, then cached."""
    if _TABLE_CACHE:
        return _TABLE_CACHE[0]
    table = []
    for index in range(256):
        value = index
        for _bit in range(8):
            if value & 1:
                value = _CRC32_POLY ^ (value >> 1)
            else:
                value = value >> 1
        table.append(value & _MASK32)
    _TABLE_CACHE.append(table)
    return table


def crc32(data, value=0):
    """CRC-32 of `data`, continuing the running checksum `value`."""
    table = _crc_table()
    crc = (value & _MASK32) ^ _MASK32
    for byte in data:
        crc = table[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return (crc ^ _MASK32) & _MASK32


def _crc32_bitwise(data, value=0):
    """The same CRC-32 without the table -- the definition itself."""
    crc = (value & _MASK32) ^ _MASK32
    for byte in data:
        crc = crc ^ byte
        for _bit in range(8):
            if crc & 1:
                crc = _CRC32_POLY ^ (crc >> 1)
            else:
                crc = crc >> 1
    return (crc ^ _MASK32) & _MASK32


def crc32_hex(data, value=0):
    """The checksum as the eight lower-case hex digits people quote."""
    return "%08x" % crc32(data, value)


def crc32_bytes(data, value=0):
    """The checksum as four big-endian bytes, the order archives store."""
    crc = crc32(data, value)
    return bytes([(crc >> 24) & 0xFF, (crc >> 16) & 0xFF,
                  (crc >> 8) & 0xFF, crc & 0xFF])


def _chunked_crc32(data, chunk):
    """Stream `data` through crc32 in `chunk`-sized pieces."""
    crc = 0
    start = 0
    while start < len(data):
        crc = crc32(data[start:start + chunk], crc)
        start = start + chunk
    return crc


# --- cases ---
print(crc32(b""))
print(crc32(b"a"))
print(crc32(b"abc"))
print(crc32(b"123456789"))
print(crc32(b"The quick brown fox jumps over the lazy dog"))
print(crc32(bytes([0, 0, 0, 0, 0, 0, 0, 0, 0, 0])))
print(crc32(bytes([255, 255, 255, 255])))
print(crc32(bytes([_i for _i in range(256)])))

# The standard check value for this CRC is 0xCBF43926 over "123456789".
print(crc32_hex(b"123456789"))
print(crc32(b"123456789") == 0xCBF43926)
print(repr(crc32_bytes(b"123456789")))
print(repr(crc32_bytes(b"")))
print(crc32_hex(b""))
print(crc32_hex(b"a"))

# The table is a precomputation; the bitwise form is the definition.
print(_crc32_bitwise(b"") == crc32(b""))
print(_crc32_bitwise(b"123456789") == crc32(b"123456789"))
print(_crc32_bitwise(bytes([_i for _i in range(256)])) == crc32(bytes([_i for _i in range(256)])))
print(_crc32_bitwise(b"abc", 12345) == crc32(b"abc", 12345))

# A few table entries, so the table itself is pinned.
print(_crc_table()[0])
print(_crc_table()[1])
print(_crc_table()[128])
print(_crc_table()[255])
print(len(_crc_table()))
print(sum(_crc_table()) % 1000000007)

# `value` is a running CRC, so chaining equals concatenation.
print(crc32(b"def", crc32(b"abc")))
print(crc32(b"abcdef"))
print(crc32(b"def", crc32(b"abc")) == crc32(b"abcdef"))
print(_chunked_crc32(bytes([_i for _i in range(256)]), 7) == crc32(bytes([_i for _i in range(256)])))
print(_chunked_crc32(b"123456789", 1) == crc32(b"123456789"))
print(_chunked_crc32(b"", 4) == crc32(b""))

# The running value is masked to 32 bits before it is used.
print(crc32(b"abc", 0))
print(crc32(b"abc", 0xFFFFFFFF))
print(crc32(b"abc", -1))
print(crc32(b"abc", -1) == crc32(b"abc", 0xFFFFFFFF))
print(crc32(b"abc", 0x1FFFFFFFF) == crc32(b"abc", 0xFFFFFFFF))
print(crc32(b"", 0x1FFFFFFFF))
print(crc32(b"", 5))
print(crc32(b"", -1))
print(crc32(b"", 0))

# Single-byte inputs pin the low end of the table.
print([crc32(bytes([_i])) for _i in [0, 1, 2, 127, 128, 254, 255]])

# The result never leaves the unsigned 32-bit range.
print(max([crc32(bytes([_i, _i])) for _i in range(256)]) <= 0xFFFFFFFF)
print(min([crc32(bytes([_i, _i])) for _i in range(256)]) >= 0)
print(len(set([crc32(bytes([_i])) for _i in range(256)])))
