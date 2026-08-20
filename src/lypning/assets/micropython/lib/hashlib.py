# lypning-mp frozen shim: hashlib. MicroPython's C hashlib (aliased as `_hashlib`
# by the variant) has update()/digest() but no hexdigest(), which every corpus
# program uses. See micropython/lib/README.md.
import uhashlib as _hashlib
from binascii import hexlify as _hexlify


class _Hash:
    def __init__(self, h, name):
        self._h = h
        self._d = None
        self.name = name

    def update(self, data):
        self._h.update(data)
        self._d = None

    def digest(self):
        if self._d is None:
            self._d = self._h.digest()
        return self._d

    def hexdigest(self):
        return _hexlify(self.digest()).decode()


def _make(fn, name, data):
    return _Hash(fn(data) if data else fn(), name)


def sha256(data=b""):
    return _make(_hashlib.sha256, "sha256", data)


def sha1(data=b""):
    return _make(_hashlib.sha1, "sha1", data)


def md5(data=b""):
    return _make(_hashlib.md5, "md5", data)


def sha224(data=b""):
    return _make(_hashlib.sha224, "sha224", data)


def sha384(data=b""):
    return _make(_hashlib.sha384, "sha384", data)


def sha512(data=b""):
    return _make(_hashlib.sha512, "sha512", data)


# hashlib.algorithms_guaranteed. A digest in here that this build does not carry
# is lypning-mp being small, and must exit 90 by the same route hashlib.md5 already
# takes (the C attribute check, which getattr's default silently bypasses).
# Anything NOT in here is unavailable in CPython too, so it keeps CPython's
# ValueError and its exact message.
_GUARANTEED = ("md5 sha1 sha224 sha256 sha384 sha512 blake2b blake2s "
               "sha3_224 sha3_256 sha3_384 sha3_512 shake_128 shake_256")


def new(name, data=b""):
    fn = getattr(_hashlib, name, None)
    if fn is None:
        if name in _GUARANTEED.split():
            raise NotImplementedError(
                "lypning-mp: unsupported: attribute: hashlib." + name)
        raise ValueError("unsupported hash type " + name)
    return _make(fn, name, data)
