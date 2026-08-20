# lypning-mp frozen shim: shutil (file copy/move/tree removal over os). No
# archives, no permission or metadata copying. See micropython/lib/README.md.
import os

_CHUNK = 65536


def copyfileobj(src, dst, length=_CHUNK):
    while True:
        buf = src.read(length)
        if not buf:
            break
        dst.write(buf)


def copyfile(src, dst):
    with open(src, "rb") as f:
        with open(dst, "wb") as g:
            copyfileobj(f, g)
    return dst


def copy(src, dst):
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))
    return copyfile(src, dst)


copy2 = copy


def move(src, dst):
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))
    try:
        os.rename(src, dst)
    except OSError:
        copyfile(src, dst)
        os.remove(src)
    return dst


def rmtree(path, ignore_errors=False):
    try:
        for name in os.listdir(path):
            p = os.path.join(path, name)
            if os.path.isdir(p):
                rmtree(p, ignore_errors)
            else:
                os.remove(p)
        os.rmdir(path)
    except OSError:
        if not ignore_errors:
            raise


def which(cmd, path=None):
    if "/" in cmd:
        return cmd if os.path.isfile(cmd) else None
    for d in (path or os.environ.get("PATH", "")).split(":"):
        p = os.path.join(d or ".", cmd)
        if os.path.isfile(p):
            return p
    return None
