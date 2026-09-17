"""statistics.variance, pvariance, stdev and pstdev without importing statistics.

The engines refuse `import statistics` (module: import statistics) and `import
fractions`, so CPython's exact algorithm (Lib/statistics.py `_ss`) is written
out here over integer numerator/denominator pairs.

The algorithm matters. CPython does NOT accumulate float sums: it converts every
value to an exact rational, computes

    ssd = (count * sum(x*x) - sum(x)**2) / count

in exact arithmetic, and rounds once at the end. The formula is the textbook
one-pass formula, which is catastrophic in float and exact in rationals. On
[1e9+4, 1e9+7, 1e9+13, 1e9+16] the exact answer is 30.0, a float one-pass sum
of squares gives -170.66666666666666 — a NEGATIVE variance — and the two-pass
loop gives 30.0 but only by luck of the data. The cases print all three.

Covered:
  variance(data, xbar=None)   sample variance, ssd / (count - 1)
  pvariance(data, mu=None)    population variance, ssd / count
  stdev(data, xbar=None)      sqrt of the sample variance, correctly rounded
  pstdev(data, mu=None)       sqrt of the population variance

Two behaviours that are easy to lose:

  - the return type. With every value an int (bool counts as int) the result is
    an int when the exact answer is whole: variance([2, 2, 2]) is the int 0 and
    pvariance([1, 3]) is the int 1, while pvariance([1, 2, 3]) is
    0.6666666666666666. stdev always returns a float, so stdev([2, 2, 2]) is
    0.0, never 0. The cases print repr().

  - the second argument is used, never checked. Passing xbar/mu switches to the
    deviation form, sum((x - c) * (x - c)), where the subtraction and the
    squaring happen in ordinary machine arithmetic before the exact sum. With a
    value that is not the mean the answer is simply a different number, not an
    error: variance([1, 2, 3], 10) is 97. CPython documents this as "use with
    care" and this unit reproduces it exactly, including the int result when
    both the data and the centre are ints.

stdev is not sqrt(variance(data)) computed in float: CPython takes the square
root of the exact rational with _float_sqrt_of_frac, which is correctly rounded
via round-to-odd on an integer square root. That is reproduced here, including
_isqrt, written out because math.isqrt refuses an integer past 64 bits on these
engines, exactly where this unit needs it.

Deliberately not covered: Decimal and Fraction inputs; NaN and infinity in the
data (CPython carries the special value through _ss; here the exact-ratio path
would not); results outside the normal double range, since _frac_to_float scales
by 2.0 ** shift and builds no subnormals; and correlation/covariance/linear
regression, which live on the same machinery but are separate surfaces.

StatisticsError subclasses ValueError and the subset has no class statement, so
the too-little-data errors are raised as a plain ValueError here.  The cases
print that TYPE and never the message, which is the same rule the rest of this
corpus follows: the type is API and the wording is not.  It is not a hypothetical
here.  Before 3.11 stdev and pstdev delegated to variance and pvariance and let
the inner message through unrelabelled, so `statistics.stdev([1])` says
'variance requires at least two data points' on CPython 3.9 and 3.10 and
'stdev requires at least two data points' from 3.11 on, and `statistics.pstdev([])`
says 'pvariance requires at least one data point' and then 'pstdev requires at
least one data point' the same way.  Measured 2026-09-17 on CPython 3.9.23,
3.10.18, 3.11.15, 3.12.11, 3.13.7 and 3.14.0rc2.  A case that printed either
wording would be right on four of those six releases and wrong on the other two,
and a unit is inlined and run on whichever one the reader has.  What every
release does agree on is that all five failures are a ValueError, so that is what
the cases print -- and the boolean shape survives too: which inputs raise and
which return is identical on all six.
"""
# fills: statistics.variance, statistics.pvariance, statistics.stdev, statistics.pstdev
# reference: statistics
import math


def _gcd(a, b):
    """Return the greatest common divisor of two non-negative ints.

    Written out because math.gcd needs machine words: the engine refuses it on
    an integer past 64 bits, which is where exact rational sums live.
    """
    while b != 0:
        r = a % b
        a = b
        b = r
    return a


def _reduce(n, d):
    """Return n/d in lowest terms with a positive denominator."""
    if d < 0:
        n = -n
        d = -d
    g = _gcd(abs(n), d)
    if g > 1:
        n = n // g
        d = d // g
    return (n, d)


def _ratio_add(an, ad, bn, bd):
    """Return an/ad + bn/bd exactly, in lowest terms."""
    g = _gcd(ad, bd)
    d = ad // g * bd
    n = an * (bd // g) + bn * (ad // g)
    return _reduce(n, d)


def _frac_to_float(n, d):
    """Return the rational n/d as the nearest float, ties to even."""
    if n == 0:
        return 0.0
    negative = n < 0
    if negative:
        n = -n
    shift = n.bit_length() - d.bit_length() - 53
    if shift >= 0:
        num = n
        den = d << shift
    else:
        num = n << -shift
        den = d
    q = num // den
    if q.bit_length() > 53:
        shift = shift + 1
        den = den << 1
        q = num // den
    rem = num - q * den
    twice = rem * 2
    if twice > den or (twice == den and q % 2 == 1):
        q = q + 1
        if q.bit_length() > 53:
            q = q // 2
            shift = shift + 1
    value = q * (2.0 ** shift)
    if negative:
        return -value
    return value


def _isqrt(n):
    """Return the integer square root of n >= 0, by Newton's method."""
    if n < 0:
        raise ValueError("isqrt of a negative number")
    if n < 2:
        return n
    x = 1 << ((n.bit_length() + 1) // 2)
    while True:
        y = (x + n // x) // 2
        if y >= x:
            return x
        x = y


def _isqrt_of_frac_rto(n, m):
    """Return the integer square root of n/m, rounded to odd."""
    a = _isqrt(n // m)
    if a * a * m != n:
        a = a | 1
    return a


def _float_sqrt_of_frac(n, m):
    """Return the square root of n/m as a correctly rounded float."""
    q = (n.bit_length() - m.bit_length() - 109) // 2
    if q >= 0:
        numerator = _isqrt_of_frac_rto(n, m << (2 * q)) << q
        denominator = 1
    else:
        numerator = _isqrt_of_frac_rto(n << (-2 * q), m)
        denominator = 1 << -q
    return _frac_to_float(numerator, denominator)


def _ss(data, c=None):
    """Return (all_int, ssd_n, ssd_d, count): the exact sum of square deviations."""
    values = list(data)
    count = 0
    all_int = True
    if c is not None:
        n = 0
        d = 1
        for x in values:
            deviation = x - c
            square = deviation * deviation
            if isinstance(square, float):
                all_int = False
            sn, sd = square.as_integer_ratio()
            n, d = _ratio_add(n, d, sn, sd)
            count = count + 1
        return (all_int, n, d, count)
    sxn = 0
    sxd = 1
    sxxn = 0
    sxxd = 1
    for x in values:
        if isinstance(x, float):
            all_int = False
        xn, xd = x.as_integer_ratio()
        sxn, sxd = _ratio_add(sxn, sxd, xn, xd)
        sxxn, sxxd = _ratio_add(sxxn, sxxd, xn * xn, xd * xd)
        count = count + 1
    if count == 0:
        return (all_int, 0, 1, 0)
    an, ad = _reduce(count * sxxn, sxxd)
    bn, bd = _reduce(sxn * sxn, sxd * sxd)
    n, d = _ratio_add(an, ad, -bn, bd)
    n, d = _reduce(n, d * count)
    return (all_int, n, d, count)


def _convert(n, d, all_int):
    """Return n/d as an int when the data were ints and it divides, else float."""
    if all_int and d == 1:
        return n
    return _frac_to_float(n, d)


def variance(data, xbar=None):
    """Return the sample variance, ssd / (count - 1)."""
    all_int, n, d, count = _ss(data, xbar)
    if count < 2:
        raise ValueError("variance requires at least two data points")
    n, d = _reduce(n, d * (count - 1))
    return _convert(n, d, all_int)


def pvariance(data, mu=None):
    """Return the population variance, ssd / count."""
    all_int, n, d, count = _ss(data, mu)
    if count < 1:
        raise ValueError("pvariance requires at least one data point")
    n, d = _reduce(n, d * count)
    return _convert(n, d, all_int)


def stdev(data, xbar=None):
    """Return the square root of the sample variance, correctly rounded."""
    all_int, n, d, count = _ss(data, xbar)
    if count < 2:
        raise ValueError("stdev requires at least two data points")
    n, d = _reduce(n, d * (count - 1))
    return _float_sqrt_of_frac(n, d)


def pstdev(data, mu=None):
    """Return the square root of the population variance, correctly rounded."""
    all_int, n, d, count = _ss(data, mu)
    if count < 1:
        raise ValueError("pstdev requires at least one data point")
    n, d = _reduce(n, d * count)
    return _float_sqrt_of_frac(n, d)


def _naive_two_pass(data):
    """Sample variance the usual float way: mean first, then squared deviations."""
    count = len(data)
    total = 0.0
    for x in data:
        total = total + x
    centre = total / count
    ss = 0.0
    for x in data:
        ss = ss + (x - centre) * (x - centre)
    return ss / (count - 1)


def _naive_one_pass(data):
    """Sample variance from running sums of x and x*x: the unstable formula."""
    count = len(data)
    total = 0.0
    squares = 0.0
    for x in data:
        total = total + x
        squares = squares + x * x
    return (squares - total * total / count) / (count - 1)


SAMPLE = [2.75, 1.75, 1.25, 0.25, 0.5, 1.25, 3.5]
POPULATION = [0.0, 0.25, 0.25, 1.25, 1.5, 1.75, 2.75, 3.25]
SPREAD = [1.5, 2.5, 2.5, 2.75, 3.25, 4.75]
UNSTABLE = [1e9 + 4, 1e9 + 7, 1e9 + 13, 1e9 + 16]
CLOSE = [1e8, 1e8 + 1, 1e8 + 2]
TENTHS = [0.1, 0.2, 0.3]


def _error_kind(fn, *args):
    """Return 'ValueError' when fn(*args) raises one, or '' when it does not.

    The TYPE, and deliberately not the message.  CPython's StatisticsError is
    a ValueError subclass, so `except ValueError` catches the real module's
    error and this port's alike and the answer is the same on both sides --
    while the text under it is not the same on every release: 3.9 and 3.10
    report `stdev`'s failure in `variance`'s words.  See the module docstring.
    """
    try:
        fn(*args)
    except ValueError:
        return "ValueError"
    return ""


# --- cases ---
print(repr(variance(SAMPLE)))
print(repr(pvariance(POPULATION)))
print(repr(stdev(SPREAD)))
print(repr(pstdev(SPREAD)))

print(repr(variance([1, 2, 3, 4])))
print(repr(pvariance([1, 2, 3, 4])))
print(repr(variance([2, 2, 2])))
print(repr(pvariance([1, 2, 3])))
print(repr(pvariance([1, 3])))
print(repr(pvariance([4])))
print(repr(variance([1, 2])))
print(repr(variance([-3, -1, 1, 3])))
print(repr(variance([True, False, True, True])))
print(repr(variance(range(10))))
print(repr(pvariance((1, 2, 3, 4, 5))))

print(repr(stdev([1, 2])))
print(repr(stdev([1, 2, 3, 4])))
print(repr(pstdev([1, 2, 3, 4])))
print(repr(stdev([2, 2, 2])))
print(repr(pstdev([7])))
print(repr(stdev([0.1, 0.2, 0.3])))
print(repr(pstdev([0.1, 0.2, 0.3])))
print(repr(stdev(range(10))))

print(repr(variance(SAMPLE, 1.6071428571428572)))
print(repr(variance([1, 2, 3], 2)))
print(repr(variance([1, 2, 3], 10)))
print(repr(variance([1, 2, 3], 2.0)))
print(repr(pvariance([1, 2, 3], 2)))
print(repr(stdev([1, 2, 3], 2)))
print(repr(pstdev([1, 2, 3], 10)))

print(repr(variance(UNSTABLE)), repr(_naive_two_pass(UNSTABLE)), repr(_naive_one_pass(UNSTABLE)))
print(repr(variance(CLOSE)), repr(_naive_two_pass(CLOSE)), repr(_naive_one_pass(CLOSE)))
print(repr(variance(SAMPLE)), repr(_naive_two_pass(SAMPLE)), repr(_naive_one_pass(SAMPLE)))
print(repr(variance(TENTHS)), repr(_naive_two_pass(TENTHS)), repr(_naive_one_pass(TENTHS)))

# Too little data is a ValueError, and the TYPE is all that is printed:
# StatisticsError is a ValueError subclass, and the wording under it moved
# in 3.11 for stdev and pstdev.  See the module docstring.
print(repr(_error_kind(variance, [1])))
print(repr(_error_kind(variance, [])))
print(repr(_error_kind(pvariance, [])))
print(repr(_error_kind(stdev, [1])))
print(repr(_error_kind(pstdev, [])))
# And the boundary itself: one more point and each of them returns.
print(repr(_error_kind(variance, [1, 2])))
print(repr(_error_kind(pvariance, [1])))
print(repr(_error_kind(stdev, [1, 2])))
print(repr(_error_kind(pstdev, [1])))
