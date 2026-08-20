# lypning-mp frozen shim: base64 over binascii. See micropython/lib/README.md.
from binascii import a2b_base64 as _a2b, b2a_base64 as _b2a


def b64encode(s, altchars=None):
    r = _b2a(s)
    if r[-1:] == b"\n":  # CPython's binascii appends a newline, MicroPython may too
        r = r[:-1]
    if altchars:
        r = r.replace(b"+", altchars[0:1]).replace(b"/", altchars[1:2])
    return r


def b64decode(s, altchars=None, validate=False):
    if isinstance(s, str):
        s = s.encode()
    if altchars:
        s = s.replace(altchars[0:1], b"+").replace(altchars[1:2], b"/")
    return _a2b(s)


def urlsafe_b64encode(s):
    return b64encode(s, b"-_")


def urlsafe_b64decode(s):
    return b64decode(s, b"-_")


standard_b64encode = b64encode
standard_b64decode = b64decode
