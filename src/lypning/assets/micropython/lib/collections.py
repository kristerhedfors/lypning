# lypning-mp frozen shim: collections.Counter and collections.defaultdict.
# Re-exports MicroPython's C collections (deque/namedtuple/OrderedDict) when
# the variant aliases it as `_collections`. See micropython/lib/README.md.
try:
    from ucollections import *
except ImportError:
    pass


def _drepr(d):
    if not d:
        return "{}"
    return "{" + ", ".join(repr(k) + ": " + repr(d[k]) for k in d) + "}"


class _Missing:
    pass


_MISSING = _Missing()


class defaultdict(dict):
    def __init__(self, default_factory=None, *args):
        super().__init__()
        self.default_factory = default_factory
        for a in args:
            self.update(a)

    def __getitem__(self, key):
        v = self.get(key, _MISSING)
        if v is _MISSING:
            return self.__missing__(key)
        return v

    def __missing__(self, key):
        if self.default_factory is None:
            raise KeyError(key)
        v = self.default_factory()
        self[key] = v
        return v

    def __repr__(self):
        return "defaultdict(" + repr(self.default_factory) + ", " + _drepr(self) + ")"


class Counter(dict):
    def __init__(self, iterable=None, **kwargs):
        super().__init__()
        if iterable is not None:
            self.update(iterable)
        if kwargs:
            self.update(kwargs)

    def __getitem__(self, key):
        return self.get(key, 0)

    def __missing__(self, key):
        return 0

    def update(self, other=None, **kwargs):
        if other is not None:
            if isinstance(other, dict):
                for k in other:
                    self[k] = self.get(k, 0) + other[k]
            else:
                for k in other:
                    self[k] = self.get(k, 0) + 1
        for k in kwargs:
            self[k] = self.get(k, 0) + kwargs[k]

    def subtract(self, other):
        for k in other:
            n = other[k] if isinstance(other, dict) else 1
            self[k] = self.get(k, 0) - n

    def most_common(self, n=None):
        items = list(self.items())
        order = list(range(len(items)))
        # Sort by count descending, ties by first-seen order — matches both
        # CPython branches (sorted(reverse=True) and heapq.nlargest) without
        # relying on the runtime's sort being stable.
        order.sort(key=lambda i: (-items[i][1], i))
        out = [items[i] for i in order]
        return out if n is None else out[:n]

    def elements(self):
        for k in self:
            for _ in range(self[k]):
                yield k

    def total(self):
        return sum(self.values())

    def __repr__(self):
        if not self:
            return "Counter()"
        return (
            "Counter({"
            + ", ".join(repr(k) + ": " + repr(v) for k, v in self.most_common())
            + "})"
        )
