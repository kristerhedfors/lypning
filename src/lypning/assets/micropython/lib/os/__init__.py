# lypning-mp frozen shim: os. Re-exports MicroPython's C os module (aliased as
# `_os` by the variant) and adds the CPython surface it lacks: os.path,
# os.environ, os.makedirs, os.walk and a stat result with named fields.
# See micropython/lib/README.md.
from uos import *
import uos as _os

import os.path as path

sep = "/"
linesep = "\n"
curdir = "."
pardir = ".."

_FIELDS = (
    "st_mode",
    "st_ino",
    "st_dev",
    "st_nlink",
    "st_uid",
    "st_gid",
    "st_size",
    "st_atime",
    "st_mtime",
    "st_ctime",
)


class _StatResult:
    def __init__(self, t):
        self.t = t

    def __getitem__(self, i):
        return self.t[i]

    def __len__(self):
        return len(self.t)

    def __iter__(self):
        return iter(self.t)

    def __getattr__(self, name):
        try:
            return self.t[_FIELDS.index(name)]
        except ValueError:
            raise AttributeError(name)

    def __repr__(self):
        return "os.stat_result(" + repr(self.t) + ")"


def stat(p):
    return _StatResult(_os.stat(p))


class _Environ:
    def get(self, key, default=None):
        v = _os.getenv(key)
        return default if v is None else v

    def __getitem__(self, key):
        v = _os.getenv(key)
        if v is None:
            raise KeyError(key)
        return v

    def __setitem__(self, key, value):
        _os.putenv(key, value)

    def __contains__(self, key):
        return _os.getenv(key) is not None


environ = _Environ()


def getenv(key, default=None):
    v = _os.getenv(key)
    return default if v is None else v


def makedirs(name, mode=0o777, exist_ok=False):
    head = name.rstrip("/") or name
    i = head.rfind("/")
    if i > 0:
        parent = head[:i]
        if not path.isdir(parent):
            makedirs(parent, mode, True)
    try:
        _os.mkdir(head)
    except OSError:
        if not (exist_ok and path.isdir(head)):
            raise


def walk(top, topdown=True):
    dirs = []
    files = []
    try:
        names = _os.listdir(top)
    except OSError:
        return
    for n in names:
        if path.isdir(path.join(top, n)):
            dirs.append(n)
        else:
            files.append(n)
    if topdown:
        yield top, dirs, files
    for d in dirs:
        for x in walk(path.join(top, d), topdown):
            yield x
    if not topdown:
        yield top, dirs, files
