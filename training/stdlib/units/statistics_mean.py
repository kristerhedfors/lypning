"""statistics.mean and statistics.fmean without importing statistics.

The engines refuse `import statistics` (module: import statistics), `import
fractions` and `math.fsum` (module-attr: math.fsum), so the exact arithmetic
CPython's mean() depends on is written out here from integer parts.

mean() IS NOT sum(data) / len(data), and it is not fmean() either. CPython's
mean() sums the data as exact rationals (Lib/statistics.py `_sum`, over
Fraction) and rounds once, at the end; fmean() converts to float and uses
math.fsum, which is an exactly rounded sum of the floats but then divides a
float by the count; sum()/len() rounds at every step. One input separates all
three:

    data = [0.1, 0.2, 0.3]
    mean(data)                      -> 0.2
    fmean(data)                     -> 0.19999999999999998
    _running_sum(data) / len(data)  -> 0.20000000000000004

mean([0.1] * 10) is 0.1 where the running sum gives 0.09999999999999999, and
mean([1e50, 1, -1e50] * 3) is 0.3333333333333333 where the running sum gives
0.0. The third way is spelled out as a loop rather than as sum(), on purpose:
the engines refuse sum() over floats (float-sum) because CPython 3.12 added
Neumaier compensation to it, so sum([0.1, 0.2, 0.3]) is 0.6000000000000001 on
3.11 and 0.6 on 3.12+. _running_sum is the 3.11 answer, unconditionally.
This unit implements the EXACT rule — mean() here is CPython's mean(), not the
cheap one — and the cases print all three side by side so the divergence is
visible rather than described.

mean() also keeps CPython's return type: with every value an int (bool counts
as int) the result is an int when the exact mean is a whole number, so
mean([1, 2, 3]) is the int 2 while fmean([1, 2, 3]) is 2.0, and mean([1, 2]) is
1.5. repr() in the cases keeps those apart.

Helpers:
  _gcd                  Euclid, because math.gcd refuses integers past 64 bits.
  _reduce / _ratio_add  exact rational arithmetic on (numerator, denominator)
                        integer pairs, standing in for fractions.Fraction. The
                        intermediates go past 64 bits for ordinary float data
                        (0.1 alone is 3602879701896397 / 2**55), which is why
                        this unit needs the bigint engine.
  _frac_to_float        the rational -> float rounding that Fraction.__float__
                        does: round half to even on a 53-bit significand.
  _running_sum          plain left-to-right float accumulation, the naive way.
  fsum                  math.fsum's exactly rounded sum, computed from the
                        exact rational total. It reproduces math.fsum's special
                        cases too: +inf together with -inf raises ValueError
                        ("-inf + inf in fsum"), while mean() of the same data
                        returns nan, because statistics does its own summation.

Deliberately not covered: Decimal and Fraction inputs (mean() supports both and
returns the same type); iterators for fmean's data/weights arguments, where
CPython counts as it goes — here both must be sequences; geometric_mean and
harmonic_mean; and any result outside the normal double range, since
_frac_to_float scales by 2.0 ** shift and does not build subnormals or handle
the OverflowError math.fsum raises on finite intermediate overflow.

StatisticsError is a subclass of ValueError and the subset has no class
statement, so the empty-data and bad-weights errors are raised as ValueError
with CPython's own message text.
"""
# fills: statistics.mean, statistics.fmean
# reference: statistics
import math


def _gcd(a, b):
    """Return the greatest common divisor of two non-negative ints.

    Written out because math.gcd needs machine words: the engine refuses it on
    an integer past 64 bits, which is exactly where this unit lives.
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


def _exact_total(values):
    """Return (n, d, all_int, has_special, special) for the exact sum."""
    n = 0
    d = 1
    special = 0.0
    has_special = False
    all_int = True
    for x in values:
        if isinstance(x, float):
            all_int = False
            if not math.isfinite(x):
                has_special = True
                special = special + x
                continue
        xn, xd = x.as_integer_ratio()
        n, d = _ratio_add(n, d, xn, xd)
    return (n, d, all_int, has_special, special)


def mean(data):
    """Return the arithmetic mean, summed exactly and rounded once."""
    values = list(data)
    count = len(values)
    if count < 1:
        raise ValueError("mean requires at least one data point")
    n, d, all_int, has_special, special = _exact_total(values)
    if has_special:
        return special / count
    n, d = _reduce(n, d * count)
    if all_int and d == 1:
        return n
    return _frac_to_float(n, d)


def fsum(data):
    """Return the exactly rounded sum of the data as floats, like math.fsum."""
    n = 0
    d = 1
    saw_pos_inf = False
    saw_neg_inf = False
    saw_nan = False
    for item in data:
        x = float(item)
        if math.isnan(x):
            saw_nan = True
            continue
        if math.isinf(x):
            if x > 0.0:
                saw_pos_inf = True
            else:
                saw_neg_inf = True
            continue
        xn, xd = x.as_integer_ratio()
        n, d = _ratio_add(n, d, xn, xd)
    if saw_pos_inf and saw_neg_inf:
        raise ValueError("-inf + inf in fsum")
    if saw_nan:
        return float("nan")
    if saw_pos_inf:
        return float("inf")
    if saw_neg_inf:
        return float("-inf")
    return _frac_to_float(n, d)


def fmean(data, weights=None):
    """Return the mean as a float: fsum(data) / n, or the weighted mean."""
    values = list(data)
    count = len(values)
    if weights is None:
        if count < 1:
            raise ValueError("fmean requires at least one data point")
        return fsum(values) / count
    w = list(weights)
    if count != len(w):
        raise ValueError("data and weights must be the same length")
    products = []
    i = 0
    while i < count:
        products.append(float(values[i]) * float(w[i]))
        i = i + 1
    num = fsum(products)
    den = fsum(w)
    if den == 0.0:
        raise ValueError("sum of weights must be non-zero")
    return num / den


def _error_of(fn, *args):
    """Return the message of the ValueError fn(*args) raises, or '' if none."""
    try:
        fn(*args)
    except ValueError as exc:
        return str(exc)
    return ""


def _running_sum(data):
    """Return the plain left-to-right float sum, rounding at every step."""
    total = 0.0
    for x in data:
        total = total + x
    return total


def _three_ways(data):
    """Return (mean, fmean, running sum / len) for one input, for comparison."""
    return (mean(data), fmean(data), _running_sum(data) / len(data))


# --- cases ---
print(repr(mean([1, 2, 3])))
print(repr(mean([1, 2])))
print(repr(mean([1, 2, 3, 4, 4])))
print(repr(mean([-1, 0, 1])))
print(repr(mean([3])))
print(repr(mean([2.5])))
print(repr(mean([2, 2, 2])))
print(repr(mean([1, 2.0])))
print(repr(mean(range(6))))
print(repr(mean((4, 5, 6))))
print(repr(mean([True, True])))
print(repr(mean([True, False, True])))
print(repr(mean([10 ** 17, 10 ** 17 + 1])))
print(repr(mean([-0.0, 0.0])))

print(_three_ways([0.1, 0.2, 0.3]))
print(_three_ways([0.1] * 10))
print(_three_ways([0.1, 0.1, 0.1]))
print(_three_ways([1e50, 1, -1e50] * 3))
print(_three_ways([1, 2, 3]))
print(_three_ways([2.75, 1.75, 1.25, 0.25, 0.5, 1.25, 3.5]))
print(_three_ways([1e16, 1.0, 1.0, 1.0]))

print(repr(fsum([0.1, 0.2, 0.3])), repr(_running_sum([0.1, 0.2, 0.3])))
print(repr(fsum([])), repr(fsum([1, 2, 3])))
print(repr(fsum([1e100, 1.0, -1e100, 1.0])), repr(_running_sum([1e100, 1.0, -1e100, 1.0])))

print(repr(fmean([3.5, 4.0, 5.25])))
print(repr(fmean([1, 2, 3])))
print(repr(fmean([3.5, 4.0, 5.25], [0.25, 0.5, 0.25])))
print(repr(fmean([1, 2, 3], [3, 1, 0])))
print(repr(fmean([2, 3, 4], [1, 1, 1])))
print(repr(fmean([2.5])))

print(repr(mean([float("inf"), 1.0])))
print(repr(mean([float("-inf"), 2, 3])))
print(repr(mean([float("inf"), float("-inf")])))
print(repr(mean([float("nan"), 1.0])))
print(repr(fmean([float("inf"), 1.0])))
print(repr(fmean([float("nan"), 1.0])))

print(repr(_error_of(mean, [])))
print(repr(_error_of(fmean, [])))
print(repr(_error_of(fmean, [1, 2], [1])))
print(repr(_error_of(fmean, [1, 2], [0, 0])))
print(repr(_error_of(fsum, [float("inf"), float("-inf")])))
print(repr(_error_of(fmean, [float("inf"), float("-inf")])))
