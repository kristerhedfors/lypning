# lypning-mp frozen shim: contextlib.contextmanager, suppress, closing, nullcontext.
# See micropython/lib/README.md.


class _GeneratorCM:
    def __init__(self, gen):
        self.gen = gen

    def __enter__(self):
        return next(self.gen)

    def __exit__(self, typ, value, tb):
        if typ is None:
            try:
                next(self.gen)
            except StopIteration:
                return False
            raise RuntimeError("generator didn't stop")
        if value is None:
            value = typ()
        try:
            self.gen.throw(value)
        except StopIteration as e:
            return e is not value
        except BaseException as e:
            if e is value:
                return False
            raise
        raise RuntimeError("generator didn't stop after throw()")


def contextmanager(func):
    def helper(*args, **kwargs):
        return _GeneratorCM(func(*args, **kwargs))

    return helper


class suppress:
    def __init__(self, *exceptions):
        self._exc = exceptions

    def __enter__(self):
        return self

    def __exit__(self, typ, value, tb):
        return typ is not None and issubclass(typ, self._exc)


class closing:
    def __init__(self, thing):
        self.thing = thing

    def __enter__(self):
        return self.thing

    def __exit__(self, *a):
        self.thing.close()
        return False


class nullcontext:
    def __init__(self, enter_result=None):
        self.enter_result = enter_result

    def __enter__(self):
        return self.enter_result

    def __exit__(self, *a):
        return False
