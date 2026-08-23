# lypning-mp frozen shim: base64 over binascii. See micropython/lib/README.md.
from binascii import a2b_base64 as _a2b, b2a_base64 as _b2a

# CPython's `base64` raises `binascii.Error` on malformed input; MicroPython's
# `binascii` raises a plain `ValueError` and defines no `Error` at all. That is
# not an implementation-defined difference the way a message is (README
# divergence 19): the exception's TYPE is part of what `base64` documents, and a
# program doing the ordinary thing with a caught exception —
# `except Exception as e: print(type(e).__name__)` — prints `Error` on CPython
# and printed `ValueError` here. A harvested corpus entry does exactly that, and
# it showed up as an UNSAFE route rather than as a MISMATCH, because the
# classifier had sent it to this tier.
#
# `Error` subclasses `ValueError`, so every `except ValueError` that worked
# before still catches it. Imported from `binascii` when there is one to import,
# so that under CPython this shim raises CPython's own class and not a look-alike
# — two classes with the same name is how `except binascii.Error` starts missing.
try:
    from binascii import Error
except ImportError:
    class Error(ValueError):
        # `binascii`, not `base64`. A program that prints a caught exception gets
        # the QUALIFIED name — from `repr()`, from a traceback, or from
        # `'%s.%s' % (type(e).__module__, type(e).__name__)` — and defining the
        # class here makes that `base64.Error` where CPython says
        # `binascii.Error`.
        #
        # In the class BODY, never as `Error.__module__ = ...` afterwards.
        # MicroPython locks a class once it is built, so the assignment raised at
        # import time and took `import base64` down with it — every base64
        # program on that tier went from a stdout difference at exit 0 to exit 1.
        # A name set in the body is part of the class dict and cannot fail that
        # way; if MicroPython ignores it the qualified name is simply wrong
        # again, which is where this started and is not a crash.
        __module__ = "binascii"


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
    try:
        return _a2b(s)
    except Error:
        raise
    except ValueError as e:
        raise Error(str(e))


def urlsafe_b64encode(s):
    return b64encode(s, b"-_")


def urlsafe_b64decode(s):
    return b64decode(s, b"-_")


standard_b64encode = b64encode
standard_b64decode = b64decode
