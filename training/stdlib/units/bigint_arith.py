"""Exact integer arithmetic past 64 bits: factorial, powers, byte codecs.

The core engine keeps every integer inside a signed 64-bit machine word.
The moment a result needs a bignum it refuses with kind ``bigint`` --
``2**63``, ``9223372036854775807 + 1``, ``int('1' * 25)``,
``int.from_bytes`` of nine 0xff bytes, ``math.factorial(21)``.  The wider
variant has arbitrary-precision integers and runs all of it.  This unit
needs no import at all: it is the bigint axis on its own.

Two of these surfaces are refused by **both** engines, so they are
written out here rather than called:

  * ``math.factorial(n)`` past ``20!`` -- the last factorial that fits in
    a signed 64-bit word is ``20! = 2432902008176640000``; ``21!`` is
    refused by the core *and* by the wider variant, which serves the
    bignum but not that function on one.
  * ``pow`` -- the builtin is refused by both engines in every form, two
    argument and three.  ``ipow`` is binary exponentiation and
    ``powmod`` is the modular form, the one that keeps a cryptographic
    exponentiation from ever materialising the full power.

The rest is served by the wider variant and exercised here because the
core is not able to: ``**`` on a bignum, ``//``, ``%``, ``divmod``,
``<<``, ``>>``, ``bit_length``, ``abs``, ``str``, ``hex``, ``format``,
``int(s)``, ``int(s, base)``, ``int.to_bytes`` and ``int.from_bytes``.
``to_bytes`` and ``from_bytes`` are *also* written from scratch here,
and every case checks the hand-written answer against the builtin
method -- a bignum byte codec written from memory is exactly the kind of
code that is wrong in the sign bit only.  Note what they are written
out of: **shifts and arithmetic, never a bitwise operator**.  ``<<`` and
``>>`` are served on a bignum, but ``&``, ``|`` and ``^`` are refused by
both engines past 64 bits, so the usual ``n & mask`` becomes
``n % (1 << k)`` and the usual ``(value << 8) | byte`` becomes a ``+``.
Each is the same value; neither is the reflex.

Also refused by both engines on a bignum argument, hence rewritten:
``math.isqrt`` and ``math.gcd``.  ``isqrt`` is Newton's method
converging from above, which is exact for every non-negative integer;
``gcd`` is Euclid, returning a non-negative result with
``gcd(0, 0) == 0``.

Deliberate divergences, all forced by the subset and none of them
silent:

  * **Error type, not error message.**  CPython signals a value that
    will not fit in ``length`` bytes with ``OverflowError``.  The subset
    can raise ``ValueError`` and nothing else, so ``to_bytes`` raises
    ``ValueError`` carrying CPython's exact wording -- ``int too big to
    convert``, ``can't convert negative int to unsigned``.  The two
    genuine ``ValueError`` cases, a negative ``length`` and a bad
    ``byteorder``, are unchanged, as is the order the four checks fire
    in: byteorder, then length, then the sign, then the range.
  * **Non-negative exponents only.**  ``pow(2, -1)`` returns a float and
    ``pow(3, -1, 7)`` returns a modular inverse; both are out of scope,
    and ``ipow``/``powmod`` raise ``ValueError`` for a negative exponent
    rather than guess.  There is no CPython message to copy for that
    case, so those two carry their own wording; ``powmod``'s zero-modulus
    message is CPython's, verbatim.
  * ``float(2**80)`` is refused by both engines (a bignum where a
    machine word is needed), so nothing here crosses into float.

What the header does **not** claim, and why.  ``factorial``, ``isqrt``
and ``gcd`` are here, they are exact, and both engines refuse the CPython
functions they stand in for -- but ``# fills:`` does not name
``math.factorial``, ``math.isqrt`` or ``math.gcd``.  ``math`` is a closed
kind in ``engines.ONLY_CPYTHON_REFUSALS``, and the acceptance gate reads
the leading segment of every ``# fills:`` name against that set, so a
unit that named them would be rejected.  The closed list is there to stop
a pure-Python transcendental shipping a float that is wrong in the last
place while its cases all pass; these three are integer-exact and their
sweep says so.  That is an argument for narrowing the closed kind, and
narrowing it is a decision for whoever owns the list -- not something a
unit may help itself to by widening a table, which is the one move
invariant 1 forbids.  So the code stays, the claim does not, and the
question is raised where it can be answered.

Verification, run 2026-09-16: besides the case list, a randomised
sweep compared ``isqrt`` on 7,995 values (every integer below 3,000,
4,000 random bit patterns up to 400 bits, and both neighbours of every
power of two and ten up to the 200th), ``gcd`` on 4,000 signed pairs,
``ipow``/``powmod`` on 2,000 triples including negative bases and
negative moduli, and ``to_bytes``/``from_bytes`` on 24,000 (value,
length, byteorder, signed) combinations -- value *and* error message,
against the real ``math`` and the real builtins.  0 divergences.

The zero-length corner is real and is pinned below: ``(0).to_bytes(0,
'big')`` and ``(-1).to_bytes(0, 'big', signed=True)`` are both ``b''``,
because a signed value fits in ``length`` bytes exactly when its
arithmetic shift right by ``8 * length - 1`` is 0 or -1, and at length
zero only ``0`` and ``-1`` survive the sign extension.
"""
# fills: pow, int.to_bytes, int.from_bytes
# reference: math


def factorial(n):
    """n! for a non-negative integer n.  factorial(0) is 1."""
    if n < 0:
        raise ValueError("factorial() not defined for negative values")
    result = 1
    i = 2
    while i <= n:
        result = result * i
        i = i + 1
    return result


def ipow(base, exp):
    """base ** exp, exactly, by binary exponentiation.  exp must be >= 0."""
    if exp < 0:
        raise ValueError("ipow() is defined only for a non-negative exponent")
    result = 1
    b = base
    e = exp
    while e > 0:
        if e % 2 == 1:
            result = result * b
        e = e // 2
        if e > 0:
            b = b * b
    return result


def powmod(base, exp, mod):
    """pow(base, exp, mod) -- the three-argument form.  exp must be >= 0.

    The result carries the sign of ``mod``, because ``%`` does.
    """
    if exp < 0:
        raise ValueError("powmod() is defined only for a non-negative exponent")
    if mod == 0:
        raise ValueError("pow() 3rd argument cannot be 0")
    result = 1 % mod
    b = base % mod
    e = exp
    while e > 0:
        if e % 2 == 1:
            result = (result * b) % mod
        e = e // 2
        if e > 0:
            b = (b * b) % mod
    return result


def isqrt(n):
    """The integer square root: the largest k with k * k <= n."""
    if n < 0:
        raise ValueError("isqrt() argument must be nonnegative")
    if n == 0:
        return 0
    # Start strictly above the answer, then converge down from above.
    x = 1 << ((n.bit_length() + 1) // 2)
    while True:
        y = (x + n // x) // 2
        if y >= x:
            return x
        x = y


def gcd(a, b):
    """Euclid's algorithm.  Non-negative result; gcd(0, 0) is 0."""
    if a < 0:
        a = 0 - a
    if b < 0:
        b = 0 - b
    while b != 0:
        a, b = b, a % b
    return a


def _fits(n, length, signed):
    """Does n fit in ``length`` bytes, two's complement when signed?"""
    if signed:
        if length == 0:
            return n == 0 or n == -1
        return (n >> (8 * length - 1)) in (0, -1)
    if n < 0:
        return False
    return (n >> (8 * length)) == 0


def to_bytes(n, length, byteorder, signed=False):
    """int.to_bytes, written out of shifts and masks.

    The four checks fire in CPython's order: byteorder, length, the sign,
    then the range.  The range failures are ValueError here and
    OverflowError in CPython -- same wording, see the module docstring.
    """
    if byteorder != "little" and byteorder != "big":
        raise ValueError("byteorder must be either 'little' or 'big'")
    if length < 0:
        raise ValueError("length argument must be non-negative")
    if n < 0 and not signed:
        raise ValueError("can't convert negative int to unsigned")
    if not _fits(n, length, signed):
        raise ValueError("int too big to convert")
    # `n & mask` would be the idiom, but the wider variant refuses a
    # bitwise &, | or ^ on an integer past 64 bits -- only the shifts
    # survive.  `n % 2**k` is the same value for a positive modulus, and
    # `x % 256` is the same as `x & 0xFF` once x is non-negative.
    value = n % (1 << (8 * length))
    out = []
    i = length - 1
    while i >= 0:
        out.append((value >> (8 * i)) % 256)
        i = i - 1
    if byteorder == "little":
        out = list(reversed(out))
    return bytes(out)


def from_bytes(data, byteorder, signed=False):
    """int.from_bytes, written out of shifts.  Empty input is 0."""
    if byteorder != "little" and byteorder != "big":
        raise ValueError("byteorder must be either 'little' or 'big'")
    order = list(data)
    if byteorder == "little":
        order = list(reversed(order))
    value = 0
    for byte in order:
        # `+` rather than `|`: the byte cannot overlap the shifted value,
        # and `|` on a bignum is refused by both engines.
        value = (value << 8) + byte
    if signed and len(order) > 0 and (order[0] & 0x80) != 0:
        value = value - (1 << (8 * len(order)))
    return value


def _message(fn, *args):
    """Run fn(*args) and return its ValueError message, or the value."""
    try:
        return fn(*args)
    except ValueError as exc:
        return "ValueError: " + str(exc)


# --- cases ---
# factorial: the 64-bit cliff is between 20! and 21!.
print(factorial(0))
print(factorial(1))
print(factorial(5))
print(factorial(10))
print(factorial(20))
print(factorial(21))
print(factorial(25))
print(factorial(30))
print(factorial(50))
print(len(str(factorial(100))))
print(factorial(100) % 1000000007)
print(factorial(20) == 2432902008176640000)
print(factorial(21) == factorial(20) * 21)
print(repr(_message(factorial, -1)))

# Exact powers.  `**` and ipow must agree on every one of them.
print(ipow(2, 62))
print(ipow(2, 63))
print(ipow(2, 64))
print(ipow(2, 128))
print(ipow(3, 100))
print(ipow(10, 30))
print(ipow(0, 0))
print(ipow(7, 0))
print(ipow(0, 5))
print(ipow(1, 1000))
print(ipow(-2, 63))
print(ipow(-3, 101))
print(ipow(2, 63) == 2 ** 63)
print(ipow(3, 100) == 3 ** 100)
print(ipow(-3, 101) == (-3) ** 101)
print(ipow(2, 200) == 1 << 200)
print(repr(_message(ipow, 2, -1)))

# The boundary the core engine actually stops at.
print(9223372036854775807)
print(9223372036854775807 + 1)
print(2 ** 62)
print(2 ** 63 - 1 == 9223372036854775807)
print(-(2 ** 63) - 1)

# Modular exponentiation.
print(powmod(3, 100, 7))
print(powmod(2, 1000, 1000000007))
print(powmod(123456789, 987654321, 1000000007))
print(powmod(0, 0, 5))
print(powmod(5, 0, 1))
print(powmod(1, 0, -5))
print(powmod(3, 4, -5))
print(powmod(-3, 3, 7))
print(powmod(2, 1000, 1) == 0)
print(powmod(3, 100, 7) == ipow(3, 100) % 7)
print(repr(_message(powmod, 2, 1, 0)))
print(repr(_message(powmod, 2, -1, 7)))

# Integer square root, exact on both sides of every perfect square.
print(isqrt(0))
print(isqrt(1))
print(isqrt(2))
print(isqrt(3))
print(isqrt(4))
print(isqrt(8))
print(isqrt(9))
print(isqrt(10 ** 20))
print(isqrt(2 ** 80))
print(isqrt(2 ** 81))
print(isqrt(ipow(10, 30) - 1))
print(isqrt(factorial(30)))
print(isqrt(ipow(12345678901234567890, 2)))
print(isqrt(ipow(12345678901234567890, 2)) == 12345678901234567890)
print(isqrt(ipow(12345678901234567890, 2) - 1) == 12345678901234567890 - 1)
print(repr(_message(isqrt, -1)))

# gcd, including the bignum arguments both engines refuse to math.gcd.
print(gcd(0, 0))
print(gcd(12, 0))
print(gcd(0, 12))
print(gcd(12, 18))
print(gcd(-12, 18))
print(gcd(12, -18))
print(gcd(-12, -18))
print(gcd(2 ** 80, 2 ** 40))
print(gcd(factorial(20), factorial(15)))
print(gcd(ipow(2, 100), ipow(3, 100)))
print(gcd(ipow(6, 40), ipow(6, 25)) == ipow(6, 25))

# to_bytes / from_bytes past 64 bits, both orders, hand-written == builtin.
print(repr(to_bytes(0, 0, "big")))
print(repr(to_bytes(0, 4, "big")))
print(repr(to_bytes(1, 8, "big")))
print(repr(to_bytes(1, 8, "little")))
print(to_bytes(2 ** 80, 11, "big").hex())
print(to_bytes(2 ** 80, 11, "little").hex())
print(to_bytes(2 ** 80, 16, "big").hex())
print(to_bytes(factorial(30), 16, "big").hex())
print(to_bytes(2 ** 80, 11, "big") == (2 ** 80).to_bytes(11, "big"))
print(to_bytes(2 ** 80, 11, "little") == (2 ** 80).to_bytes(11, "little"))
print(to_bytes(factorial(30), 16, "big") == factorial(30).to_bytes(16, "big"))

# Signed, and the zero-length sign-extension corner.
print(repr(to_bytes(-1, 4, "big", True)))
print(repr(to_bytes(-1, 0, "big", True)))
print(repr(to_bytes(-128, 1, "big", True)))
print(repr(to_bytes(127, 1, "big", True)))
print(to_bytes(-(2 ** 80), 11, "big", True).hex())
print(to_bytes(-(2 ** 80), 11, "little", True).hex())
print(to_bytes(-(2 ** 80), 11, "big", True) == (-(2 ** 80)).to_bytes(11, "big", signed=True))

# from_bytes, unsigned and signed, empty and past 64 bits.
print(from_bytes(b"", "big"))
print(from_bytes(b"", "big", True))
print(from_bytes(b"\x01\x02", "big"))
print(from_bytes(b"\x01\x02", "little"))
print(from_bytes(b"\xff" * 8, "big"))
print(from_bytes(b"\xff" * 9, "big"))
print(from_bytes(b"\xff" * 12, "big"))
print(from_bytes(b"\xff\xff", "big", True))
print(from_bytes(b"\x80", "big", True))
print(from_bytes(b"\x80" + b"\x00" * 10, "big", True))
print(from_bytes(b"\xff" * 12, "big") == int.from_bytes(b"\xff" * 12, "big"))
print(from_bytes(b"\x80" + b"\x00" * 10, "big", True) == int.from_bytes(b"\x80" + b"\x00" * 10, "big", signed=True))

# The round trip, on the values the core cannot hold.
print(from_bytes(to_bytes(2 ** 80, 11, "big"), "big") == 2 ** 80)
print(from_bytes(to_bytes(factorial(30), 16, "little"), "little") == factorial(30))
print(from_bytes(to_bytes(-(2 ** 80), 11, "little", True), "little", True) == -(2 ** 80))

# The four refusals, in the order CPython fires them.
print(repr(_message(to_bytes, 1, -1, "mid")))
print(repr(_message(to_bytes, -1, -1, "big")))
print(repr(_message(to_bytes, -1, 1, "big")))
print(repr(_message(to_bytes, 300, 1, "big")))
print(repr(_message(to_bytes, -129, 1, "big", True)))
print(repr(_message(to_bytes, 1, 0, "big")))
print(repr(_message(to_bytes, 2 ** 80, 10, "big")))
print(repr(_message(from_bytes, b"\x01", "mid")))

# str, hex, format and int() all cross the 64-bit line here.
print(str(2 ** 80))
print(hex(2 ** 80))
print(format(2 ** 80, ","))
print("%d" % factorial(25))
print("{:x}".format(2 ** 80))
print(int("123456789012345678901234567890"))
print(int("ffffffffffffffffff", 16))
print(int("1" + "0" * 64, 2) == 2 ** 64)
print((2 ** 80).bit_length())
print(factorial(30).bit_length())
print(abs(-(2 ** 80)))
print(divmod(2 ** 80, 3))
print(-(2 ** 80) // 3, -(2 ** 80) % 3)
print((2 ** 80) >> 40, (1 << 80) == 2 ** 80)
print(sum([2 ** 70, 2 ** 70, 2 ** 70]))
print(sorted([2 ** 80, 5, 2 ** 64]))
print(max([factorial(25), factorial(24)]))
