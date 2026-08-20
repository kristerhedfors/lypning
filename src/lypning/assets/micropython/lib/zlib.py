# lypning-mp frozen shim: zlib — crc32 over binascii, compress/decompress over
# MicroPython's `deflate`. compress() additionally needs the variant to build
# with MICROPY_PY_DEFLATE_COMPRESS. See micropython/lib/README.md.
import binascii as _b
import deflate as _d
import io as _io

MAX_WBITS = 15
DEFLATED = 8
Z_DEFAULT_COMPRESSION = -1


class error(Exception):
    pass


def crc32(data, value=0):
    return _b.crc32(data, value)


def _fmt(wbits):
    if wbits < 0:
        return _d.RAW, -wbits
    if wbits > 15:
        return _d.GZIP, wbits - 16
    return _d.ZLIB, wbits


def compress(data, level=-1, wbits=MAX_WBITS):
    fmt, w = _fmt(wbits)
    buf = _io.BytesIO()
    s = _d.DeflateIO(buf, fmt, w)
    s.write(data)
    s.close()
    return buf.getvalue()


def decompress(data, wbits=MAX_WBITS):
    fmt, w = _fmt(wbits)
    return _d.DeflateIO(_io.BytesIO(data), fmt, w).read()
