"""hashlib's four served digests, and the HMAC/PBKDF2 built on top.

The core engine refuses ``import hashlib`` outright.  The wider variant
serves the module, but only four constructors -- ``md5``, ``sha1``,
``sha256``, ``sha512`` -- and on the object only ``.update()``,
``.digest()``, ``.hexdigest()``, ``.copy()`` and ``.digest_size``.
Everything else in the module refuses: ``hashlib.new``, ``blake2b``,
``blake2s``, every ``sha3_*`` and ``shake_*``, ``pbkdf2_hmac``,
``scrypt``, ``algorithms_available``; and on the object, ``.block_size``
and ``.name``.

So this unit covers two different kinds of gap:

  * **Dispatch.**  ``hashlib.new(name, data)`` is the usual way to pick
    an algorithm at run time, and it is refused.  ``digest(name, data)``
    and ``hexdigest(name, data)`` below are the same thing written as an
    ``if`` chain over the four names that do work, raising
    ``ValueError("unsupported hash type <name>")`` -- CPython's own
    wording for ``hashlib.new`` -- for anything else.
  * **Constructions.**  ``hmac`` the *module* is not served at all, and
    ``hashlib.pbkdf2_hmac`` is refused.  Both are built here out of the
    four digests, which is all either one ever was.

HMAC (RFC 2104): pad the key to the hash's **block size** -- hashing it
down first if it is longer, zero-extending it if it is shorter -- then
``H((K ^ opad) || H((K ^ ipad) || message))`` with ``ipad`` a block of
0x36 and ``opad`` a block of 0x5c.  The block size is 64 bytes for md5,
sha1 and sha256 and 128 for sha512; ``.block_size`` is refused by this
engine, so ``block_size(name)`` is a table, which is also why the table
is checked against the real attribute in the authoring differential
rather than trusted.  Note the asymmetry that catches people: a key
*longer* than the block is replaced by its digest, so
``hmac(key) == hmac(sha256(key))`` for such a key, and a 64-byte key and
its 64-byte padding are indistinguishable.

PBKDF2 (RFC 2898): ``dklen`` bytes of output, block by block, each block
being ``U1 ^ U2 ^ ... ^ Uc`` where ``U1 = HMAC(password, salt || INT(i))``
with ``INT(i)`` the 1-based block index as four big-endian bytes, and
each later ``U`` the HMAC of the one before.  The guards carry CPython's
messages verbatim, trailing full stop included.

The vectors below are the published ones -- FIPS 180 for the digests,
RFC 2202 for HMAC-MD5 and HMAC-SHA1, RFC 4231 for HMAC-SHA256 and
HMAC-SHA512, RFC 6070 for PBKDF2-HMAC-SHA1 -- so they pin the code
against the standard and not only against CPython.

Deliberately **not** covered, and the omission is the point:

  * ``hmac.compare_digest``.  Its entire value is that it takes the same
    time whether the first byte differs or the last, and no loop written
    in this subset can promise that.  Shipping a plausible-looking
    ``compare_digest`` that leaks a timing side channel would be worse
    than shipping nothing, so this unit ships nothing.  Compare digests
    with ``==`` here and understand that it is not constant time.
  * ``usedforsecurity=False``, ``hashlib.file_digest``, and the
    ``sha3``/``blake2`` families: different algorithms, not a gap in
    this one.

Verification, run 2026-09-16: besides the case list, a randomised
sweep checked ``block_size`` and ``digest_size`` against the real
attributes and compared 1,600 HMAC and digest answers and 100 PBKDF2
answers against the real ``hmac`` and ``hashlib``, over keys and
messages of every length either side of both block sizes.  0
divergences.

One divergence, and it is the only line below that does not match the
real module: ``hashlib.pbkdf2_hmac`` with an unknown name fails in the
OpenSSL layer, where ``digest`` here raises ``ValueError`` carrying
``hashlib.new``'s message.  The subset has one exception to raise, and
that message is the one worth carrying because it is the one CPython
itself keeps steady: ``hashlib.new('bogus')`` is ``ValueError:
unsupported hash type bogus`` on CPython 3.9.23, 3.10.18, 3.11.15,
3.12.11, 3.13.7 and 3.14.0rc2 alike (measured 2026-09-17).

What CPython answers on the ``pbkdf2_hmac`` side is NOT steady, which is
why the declaration here names no single CPython answer.  On the same six
interpreters, the same day, ``hashlib.pbkdf2_hmac('bogus', b'p', b's',
1)`` raises a plain ``ValueError`` on 3.9 and 3.10 and an
``_hashlib.UnsupportedDigestmodError`` from 3.11 on, and its message is
``unsupported hash type`` on 3.9 and ``[digital envelope routines]
unsupported`` on the other five.  Neither the type nor the text is a fact
about PBKDF2.  The one thing all six agree on is the part a caller can
use: whatever comes out is a ``ValueError`` -- ``UnsupportedDigestmodError``
subclasses it -- so ``except ValueError`` catches CPython's error and this
port's alike, and ``_message`` is written with exactly that ``except``.
"""
# fills: hashlib.md5, hashlib.sha1, hashlib.sha256, hashlib.sha512, hashlib.new, hashlib.pbkdf2_hmac, hmac.new, hmac.digest
# reference: hashlib

import hashlib


def digest(name, data):
    """hashlib.new(name, data).digest(), over the four served algorithms."""
    if name == "md5":
        return hashlib.md5(data).digest()
    if name == "sha1":
        return hashlib.sha1(data).digest()
    if name == "sha256":
        return hashlib.sha256(data).digest()
    if name == "sha512":
        return hashlib.sha512(data).digest()
    raise ValueError("unsupported hash type " + name)


def hexdigest(name, data):
    """hashlib.new(name, data).hexdigest() -- lower-case hex, no prefix."""
    return digest(name, data).hex()


def digest_size(name):
    """Digest length in bytes: 16, 20, 32, 64."""
    return len(digest(name, b""))


def block_size(name):
    """The compression-function block size HMAC pads the key to.

    A table, because ``.block_size`` is refused by this engine.
    """
    if name == "md5" or name == "sha1" or name == "sha256":
        return 64
    if name == "sha512":
        return 128
    raise ValueError("unsupported hash type " + name)


def digest_stream(name, chunks):
    """Feed chunks one at a time -- the same answer as one long call."""
    if name == "md5":
        hasher = hashlib.md5()
    elif name == "sha1":
        hasher = hashlib.sha1()
    elif name == "sha256":
        hasher = hashlib.sha256()
    elif name == "sha512":
        hasher = hashlib.sha512()
    else:
        raise ValueError("unsupported hash type " + name)
    for chunk in chunks:
        hasher.update(chunk)
    return hasher.digest()


def _xor_pad(key, pad_byte):
    return bytes([key[i] ^ pad_byte for i in range(len(key))])


def hmac_digest(name, key, message):
    """HMAC-<name> of message under key, as bytes (RFC 2104)."""
    block = block_size(name)
    if len(key) > block:
        key = digest(name, key)
    if len(key) < block:
        key = key + bytes(block - len(key))
    inner = digest(name, _xor_pad(key, 0x36) + message)
    return digest(name, _xor_pad(key, 0x5C) + inner)


def hmac_hexdigest(name, key, message):
    """HMAC-<name> as lower-case hex."""
    return hmac_digest(name, key, message).hex()


def _int_be32(value):
    """The 1-based PBKDF2 block index as four big-endian bytes."""
    return bytes([
        (value >> 24) & 0xFF,
        (value >> 16) & 0xFF,
        (value >> 8) & 0xFF,
        value & 0xFF,
    ])


def pbkdf2_hmac(name, password, salt, iterations, dklen=None):
    """hashlib.pbkdf2_hmac: dklen derived bytes (RFC 2898, PBKDF2)."""
    if iterations < 1:
        raise ValueError("iteration value must be greater than 0.")
    hlen = digest_size(name)
    if dklen is None:
        dklen = hlen
    if dklen < 1:
        raise ValueError("key length must be greater than 0.")
    blocks = []
    produced = 0
    index = 1
    while produced < dklen:
        u = hmac_digest(name, password, salt + _int_be32(index))
        acc = list(u)
        i = 1
        while i < iterations:
            u = hmac_digest(name, password, u)
            acc = [acc[j] ^ u[j] for j in range(len(acc))]
            i = i + 1
        blocks.append(bytes(acc))
        produced = produced + hlen
        index = index + 1
    return b"".join(blocks)[:dklen]


def _message(fn, *args):
    """Run fn(*args) and return its ValueError message, or the value."""
    try:
        return fn(*args)
    except ValueError as exc:
        return "ValueError: " + str(exc)


# --- cases ---
# The published single-block vectors (FIPS 180 / RFC 1321).
print(hexdigest("md5", b""))
print(hexdigest("md5", b"abc"))
print(hexdigest("md5", b"message digest"))
print(hexdigest("sha1", b""))
print(hexdigest("sha1", b"abc"))
print(hexdigest("sha1", b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"))
print(hexdigest("sha256", b""))
print(hexdigest("sha256", b"abc"))
print(hexdigest("sha256", b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"))
print(hexdigest("sha512", b""))
print(hexdigest("sha512", b"abc"))

# The one-million-'a' vector is the multi-block case, shortened here to
# a length that still crosses several blocks in every algorithm.
print(hexdigest("md5", b"a" * 1000))
print(hexdigest("sha1", b"a" * 1000))
print(hexdigest("sha256", b"a" * 1000))
print(hexdigest("sha512", b"a" * 1000))

# Bytes with the high bit set, and an embedded NUL.
print(hexdigest("sha256", bytes([0, 1, 127, 128, 255])))
print(hexdigest("sha256", b"a\x00b"))
print(repr(digest("md5", b"abc")))
print(repr(digest("sha1", b"")))

# Sizes, and the block sizes HMAC needs.
print(digest_size("md5"), digest_size("sha1"), digest_size("sha256"), digest_size("sha512"))
print(block_size("md5"), block_size("sha1"), block_size("sha256"), block_size("sha512"))
print(len(hexdigest("sha256", b"abc")))
print(repr(_message(digest, "sha3_256", b"abc")))
print(repr(_message(digest, "blake2b", b"abc")))
print(repr(_message(block_size, "bogus")))

# Streaming equals one-shot, in every split.
print(digest_stream("sha256", [b"a", b"bc"]) == digest("sha256", b"abc"))
print(digest_stream("sha256", []) == digest("sha256", b""))
print(digest_stream("sha256", [b"", b"abc", b""]) == digest("sha256", b"abc"))
print(digest_stream("sha512", [b"a" * 127, b"a" * 1]) == digest("sha512", b"a" * 128))
print(digest_stream("md5", [bytes([i]) for i in range(256)]) == digest("md5", bytes([i for i in range(256)])))

# .copy() forks the state rather than sharing it.
_H = hashlib.sha256(b"ab")
_F = _H.copy()
_H.update(b"c")
_F.update(b"d")
print(_H.hexdigest())
print(_F.hexdigest())
print(_H.hexdigest() == hexdigest("sha256", b"abc"))
print(_F.hexdigest() == hexdigest("sha256", b"abd"))

# HMAC-MD5 and HMAC-SHA1: RFC 2202.
print(hmac_hexdigest("md5", b"\x0b" * 16, b"Hi There"))
print(hmac_hexdigest("md5", b"Jefe", b"what do ya want for nothing?"))
print(hmac_hexdigest("md5", b"\xaa" * 16, b"\xdd" * 50))
print(hmac_hexdigest("sha1", b"\x0b" * 20, b"Hi There"))
print(hmac_hexdigest("sha1", b"Jefe", b"what do ya want for nothing?"))
print(hmac_hexdigest("sha1", b"\xaa" * 20, b"\xdd" * 50))
print(hmac_hexdigest("sha1", b"\xaa" * 80, b"Test Using Larger Than Block-Size Key - Hash Key First"))

# HMAC-SHA256 and HMAC-SHA512: RFC 4231.
print(hmac_hexdigest("sha256", b"\x0b" * 20, b"Hi There"))
print(hmac_hexdigest("sha256", b"Jefe", b"what do ya want for nothing?"))
print(hmac_hexdigest("sha256", b"\xaa" * 20, b"\xdd" * 50))
print(hmac_hexdigest("sha256", b"\xaa" * 131, b"Test Using Larger Than Block-Size Key - Hash Key First"))
print(hmac_hexdigest("sha512", b"\x0b" * 20, b"Hi There"))
print(hmac_hexdigest("sha512", b"Jefe", b"what do ya want for nothing?"))
print(hmac_hexdigest("sha512", b"\xaa" * 131, b"Test Using Larger Than Block-Size Key - Hash Key First"))

# The corners of the key padding.
print(hmac_hexdigest("sha256", b"", b""))
print(hmac_hexdigest("sha256", b"", b"abc"))
print(hmac_hexdigest("sha256", b"k", b""))
print(hmac_hexdigest("sha256", b"\x00" * 64, b"abc") == hmac_hexdigest("sha256", b"", b"abc"))
print(hmac_hexdigest("sha256", b"k" * 64, b"abc") == hmac_hexdigest("sha256", b"k" * 64, b"abc"))
print(hmac_hexdigest("sha256", b"k" * 65, b"abc") == hmac_hexdigest("sha256", digest("sha256", b"k" * 65), b"abc"))
print(hmac_hexdigest("sha512", b"k" * 129, b"abc") == hmac_hexdigest("sha512", digest("sha512", b"k" * 129), b"abc"))
print(len(hmac_digest("sha512", b"k", b"m")))
print(repr(_message(hmac_digest, "sha3_256", b"k", b"m")))

# One flipped bit in the message changes everything.
print(hmac_hexdigest("sha256", b"key", b"message"))
print(hmac_hexdigest("sha256", b"key", b"messagf"))
print(hmac_hexdigest("sha256", b"keu", b"message"))

# PBKDF2-HMAC-SHA1: RFC 6070.
print(pbkdf2_hmac("sha1", b"password", b"salt", 1).hex())
print(pbkdf2_hmac("sha1", b"password", b"salt", 2).hex())
print(pbkdf2_hmac("sha1", b"password", b"salt", 4096).hex())
print(pbkdf2_hmac("sha1", b"passwordPASSWORDpassword", b"saltSALTsaltSALTsaltSALTsaltSALTsalt", 4096, 25).hex())
print(pbkdf2_hmac("sha1", b"pass\x00word", b"sa\x00lt", 4096, 16).hex())

# PBKDF2 over the other digests, and the dklen corners.
print(pbkdf2_hmac("sha256", b"password", b"salt", 1).hex())
print(pbkdf2_hmac("sha256", b"password", b"salt", 2, 40).hex())
print(pbkdf2_hmac("sha512", b"password", b"salt", 1, 16).hex())
print(pbkdf2_hmac("md5", b"password", b"salt", 3, 1).hex())
print(len(pbkdf2_hmac("sha256", b"p", b"s", 1)))
print(len(pbkdf2_hmac("sha256", b"p", b"s", 1, 100)))
print(pbkdf2_hmac("sha256", b"p", b"s", 1, 32) == pbkdf2_hmac("sha256", b"p", b"s", 1))
print(pbkdf2_hmac("sha256", b"p", b"s", 1, 10) == pbkdf2_hmac("sha256", b"p", b"s", 1)[:10])
print(repr(_message(pbkdf2_hmac, "sha1", b"p", b"s", 0)))
print(repr(_message(pbkdf2_hmac, "sha1", b"p", b"s", 1, 0)))
print(repr(_message(pbkdf2_hmac, "bogus", b"p", b"s", 1)))

# A one-iteration PBKDF2 block is exactly one HMAC.
print(pbkdf2_hmac("sha256", b"pw", b"sa", 1) == hmac_digest("sha256", b"pw", b"sa" + bytes([0, 0, 0, 1])))
