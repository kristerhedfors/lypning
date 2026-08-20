# lypning-mp frozen shim: tempfile. Names come from os.urandom, not mkstemp(3):
# there is one process in this VM and no other writer to race with.
# See micropython/lib/README.md.
import os

_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"


def gettempdir():
    return os.environ.get("TMPDIR") or "/tmp"


def _name(prefix, suffix, dir):
    r = os.urandom(6)
    tail = "".join(_CHARS[b % len(_CHARS)] for b in r)
    return os.path.join(dir or gettempdir(), prefix + tail + suffix)


def mktemp(suffix="", prefix="tmp", dir=None):
    return _name(prefix, suffix, dir)


def mkstemp(suffix="", prefix="tmp", dir=None, text=False):
    p = _name(prefix, suffix, dir)
    f = open(p, "w" if text else "wb")
    return f, p


def mkdtemp(suffix="", prefix="tmp", dir=None):
    p = _name(prefix, suffix, dir)
    os.mkdir(p)
    return p


class _Temp:
    def __init__(self, f, name, delete):
        self.file = f
        self.name = name
        self._delete = delete

    def __getattr__(self, k):
        return getattr(self.file, k)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False

    def close(self):
        self.file.close()
        if self._delete:
            try:
                os.remove(self.name)
            except OSError:
                pass


def NamedTemporaryFile(
    mode="w+b", buffering=-1, encoding=None, newline=None,
    suffix="", prefix="tmp", dir=None, delete=True
):
    p = _name(prefix, suffix, dir)
    return _Temp(open(p, mode), p, delete)


TemporaryFile = NamedTemporaryFile


class TemporaryDirectory:
    def __init__(self, suffix="", prefix="tmp", dir=None):
        self.name = mkdtemp(suffix, prefix, dir)

    def __enter__(self):
        return self.name

    def __exit__(self, *a):
        self.cleanup()
        return False

    def cleanup(self):
        import shutil

        shutil.rmtree(self.name, True)
