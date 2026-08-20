# lypning-mp frozen shim: statistics (mean/median family only, exact-int result
# where CPython gives one). See micropython/lib/README.md.


class StatisticsError(ValueError):
    pass


def _data(xs):
    d = list(xs)
    if not d:
        raise StatisticsError("no data points")
    return d


def mean(xs):
    d = _data(xs)
    total = sum(d)
    n = len(d)
    if isinstance(total, int) and total % n == 0:
        return total // n
    return total / n


def fmean(xs):
    d = _data(xs)
    return sum(d) / len(d)


def median(xs):
    d = sorted(_data(xs))
    n = len(d)
    if n % 2:
        return d[n // 2]
    return (d[n // 2 - 1] + d[n // 2]) / 2


def median_low(xs):
    d = sorted(_data(xs))
    return d[(len(d) - 1) // 2]


def median_high(xs):
    d = sorted(_data(xs))
    return d[len(d) // 2]


def mode(xs):
    d = _data(xs)
    counts = {}
    for x in d:
        counts[x] = counts.get(x, 0) + 1
    best = d[0]
    for x in d:
        if counts[x] > counts[best]:
            best = x
    return best


def pvariance(xs, mu=None):
    d = _data(xs)
    m = mean(d) if mu is None else mu
    return sum((x - m) ** 2 for x in d) / len(d)


def variance(xs, mu=None):
    d = _data(xs)
    if len(d) < 2:
        raise StatisticsError("variance requires at least two data points")
    m = mean(d) if mu is None else mu
    return sum((x - m) ** 2 for x in d) / (len(d) - 1)


def stdev(xs, mu=None):
    return variance(xs, mu) ** 0.5


def pstdev(xs, mu=None):
    return pvariance(xs, mu) ** 0.5
