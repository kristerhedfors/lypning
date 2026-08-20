# lypning-mp frozen shim: pathlib.Path (posix only, no PurePath split, no
# symlink or owner surface). See micropython/lib/README.md.
import os


class Path:
    def __init__(self, *parts):
        p = ""
        for x in parts:
            p = os.path.join(p, str(x)) if p else str(x)
        self._p = p or "."

    def __str__(self):
        return self._p

    def __repr__(self):
        return "PosixPath(" + repr(self._p) + ")"

    def __eq__(self, o):
        return isinstance(o, Path) and self._p == o._p

    def __truediv__(self, o):
        return Path(self._p, str(o))

    def joinpath(self, *o):
        return Path(self._p, *o)

    @property
    def name(self):
        return os.path.basename(self._p)

    @property
    def parent(self):
        return Path(os.path.dirname(self._p) or ".")

    @property
    def suffix(self):
        return os.path.splitext(self.name)[1]

    @property
    def stem(self):
        return os.path.splitext(self.name)[0]

    @property
    def parts(self):
        return tuple(x for x in self._p.split("/") if x)

    def with_suffix(self, suffix):
        return Path(os.path.splitext(self._p)[0] + suffix)

    def with_name(self, name):
        return Path(os.path.dirname(self._p), name)

    def exists(self):
        return os.path.exists(self._p)

    def is_file(self):
        return os.path.isfile(self._p)

    def is_dir(self):
        return os.path.isdir(self._p)

    def is_absolute(self):
        return self._p[:1] == "/"

    def stat(self):
        return os.stat(self._p)

    def absolute(self):
        return Path(os.path.abspath(self._p))

    resolve = absolute

    def read_text(self, encoding=None):
        with open(self._p) as f:
            return f.read()

    def read_bytes(self):
        with open(self._p, "rb") as f:
            return f.read()

    def write_text(self, data, encoding=None):
        with open(self._p, "w") as f:
            return f.write(data)

    def write_bytes(self, data):
        with open(self._p, "wb") as f:
            return f.write(data)

    def open(self, mode="r", **kw):
        return open(self._p, mode)

    def mkdir(self, mode=0o777, parents=False, exist_ok=False):
        if parents:
            os.makedirs(self._p, mode, exist_ok)
        else:
            try:
                os.mkdir(self._p)
            except OSError:
                if not (exist_ok and os.path.isdir(self._p)):
                    raise

    def unlink(self, missing_ok=False):
        try:
            os.remove(self._p)
        except OSError:
            if not missing_ok:
                raise

    def rmdir(self):
        os.rmdir(self._p)

    def rename(self, target):
        os.rename(self._p, str(target))
        return Path(str(target))

    def iterdir(self):
        for n in os.listdir(self._p):
            yield Path(self._p, n)

    def glob(self, pattern):
        import glob as _glob

        for p in _glob.glob(os.path.join(self._p, pattern)):
            yield Path(p)

    @classmethod
    def cwd(cls):
        return cls(os.getcwd())

    @classmethod
    def home(cls):
        return cls(os.environ.get("HOME", "/root"))


PurePath = Path
PosixPath = Path
PurePosixPath = Path
