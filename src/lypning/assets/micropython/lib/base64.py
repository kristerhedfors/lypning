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
        # class here would otherwise make that `base64.Error` where CPython says
        # `binascii.Error`.
        #
        # In the class body because that is where it belongs, and for no
        # stronger reason: checked on MicroPython 1.22.1, a later
        # `Error.__module__ = ...` is accepted too. An earlier version of this
        # comment claimed MicroPython locks a class and that the assignment was
        # crashing `import base64`; that was wrong, and the crash it was invented
        # to explain is the one described in `README.md` divergence 17 — builtin
        # types have no `__module__` there at all.
        __module__ = "binascii"


_ALPHABET = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def _scan(s, strict):
    """CPython's `a2b_base64` error detection, and only the detection.

    A transcription of `binascii_a2b_base64_impl`, returning the message CPython
    would raise or ``None``. It exists because MicroPython's C decoder
    (`extmod/modbinascii.c`) reaches a DIFFERENT verdict on the same bytes, in
    two ways this file has to close:

    * it says `incorrect padding` for every unfinished quad, where CPython
      separates the one-more-than-a-multiple-of-4 case out with its own message
      and a character count;
    * it has no strict mode at all, so `validate=True` was accepted and ignored
      — `b64decode(b"a!Gk=", validate=True)` returned `b'hi'` here and raises on
      CPython. That is not a message difference. It is the tier answering a
      question about whether data is valid base64 with the wrong answer, which
      is the one outcome a subset runtime may never produce.

    Detection only, deliberately: the DECODE stays on the C function, which
    already agrees with CPython on every value both accept. This walks the input
    a second time, so callers only pay for it when the C decoder has already
    failed, or when `validate=True` makes it the only way to be right.

    The short-circuit in C's `quad_pos >= 2 && quad_pos + ++pads >= 4` is load-
    bearing: `pads` does not advance while `quad_pos` is below 2, which is why
    `b"===="` decodes to `b""` rather than raising.

    **The four strict messages are CPython 3.11's.** `validate=True` only began
    reaching `a2b_base64(strict_mode=True)` in 3.11; before it, `base64` ran its
    own regex and said `Non-base64 digit found` for every one of the cases this
    separates. Against a 3.9 or 3.10 oracle this still REJECTS the same inputs —
    which is the part that keeps a caller safe — and words it differently. That
    is why `base64-validate-rejects` declares `(3, 11)` in `_CASE_MIN_PYTHON`.

    Checked by brute force rather than by reading: every string up to length 6
    over `aG=!\\n-`, plus 60,000 random ones, decoded both ways and compared.
    The `b"a=a"` case — `Discontinuous padding not allowed`, which is NOT the
    same message as `Excess data after padding` — came out of that and out of
    nothing else.
    """
    n = len(s)
    if strict and n and s[0] == 0x3D:  # b"="
        return "Leading padding not allowed"
    quad_pos = 0
    pads = 0
    padding_started = False
    count = 0
    for i in range(n):
        ch = s[i]
        if ch == 0x3D:
            padding_started = True
            if quad_pos >= 2:
                pads += 1
                if quad_pos + pads >= 4:
                    if strict and i + 1 < n:
                        return "Excess data after padding"
                    return None
            continue
        if ch not in _ALPHABET:
            if strict:
                return "Only base64 data is allowed"
            continue
        if strict and padding_started:
            # NOT "Excess data after padding", which is the message for padding
            # that COMPLETED a quad and still had bytes after it. This is
            # padding that did not complete one — `b"a=a"` — and CPython names
            # the two separately. Found by brute-forcing short strings over
            # `aG=!\n-` against CPython, not by reading the C.
            return "Discontinuous padding not allowed"
        pads = 0
        quad_pos = (quad_pos + 1) % 4
        count += 1
    if quad_pos:
        if quad_pos == 1:
            # The count is of DATA characters, which is what CPython's
            # `(bin_len / 3) * 4 + quad_pos` works out to.
            return ("Invalid base64-encoded string: number of data characters "
                    "(%d) cannot be 1 more than a multiple of 4" % count)
        return "Incorrect padding"
    return None


def b64encode(s, altchars=None):
    # MicroPython's `b2a_base64` takes anything with a buffer and, unlike
    # CPython's, takes a `str` as well — so `b64encode("hi")` returned
    # `b'aGk='` here and raises TypeError there. Encoding text without being
    # told which codec is a guess, and this file does not guess.
    if not isinstance(s, (bytes, bytearray, memoryview)):
        raise TypeError("a bytes-like object is required, not '%s'" % type(s).__name__)
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
    if validate:
        # Before the C call, not after: the C decoder does not reject anything
        # strict mode is about, so there is no failure to interpret afterwards.
        why = _scan(s, True)
        if why is not None:
            raise Error(why)
    try:
        return _a2b(s)
    except Error:
        raise
    except ValueError as e:
        # The C decoder refused. Ask what CPython would have said about these
        # same bytes; fall back to its own message, capitalised, if the two
        # disagree about there being a problem at all — a message that is merely
        # differently worded beats one that is silently absent.
        why = _scan(s, False)
        if why is None:
            why = str(e)[:1].upper() + str(e)[1:]
        raise Error(why)


def urlsafe_b64encode(s):
    return b64encode(s, b"-_")


def urlsafe_b64decode(s):
    return b64decode(s, b"-_")


standard_b64encode = b64encode
standard_b64decode = b64decode
