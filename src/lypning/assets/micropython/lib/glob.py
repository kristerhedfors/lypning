# lypning-mp frozen shim: glob over os.listdir. Wildcards are supported in the
# last path component only. See micropython/lib/README.md.
import os
import re as _re

_MAGIC = "*?["


def _translate(pat):
    out = []
    i = 0
    n = len(pat)
    while i < n:
        c = pat[i]
        if c == "*":
            out.append(".*")
        elif c == "?":
            out.append(".")
        elif c == "[":
            j = pat.find("]", i + 1)
            if j < 0:
                out.append("\\[")
            else:
                body = pat[i + 1 : j]
                out.append("[" + ("^" + body[1:] if body[:1] == "!" else body) + "]")
                i = j
        else:
            out.append(_re.escape(c))
        i += 1
    return "".join(out) + "$"


def fnmatch(name, pat):
    return _re.match(_translate(pat), name) is not None


def has_magic(s):
    for c in _MAGIC:
        if c in s:
            return True
    return False


def glob(pathname, recursive=False):
    i = pathname.rfind("/")
    head = pathname[:i] if i >= 0 else ""
    pat = pathname[i + 1 :]
    if not has_magic(pat):
        return [pathname] if os.path.exists(pathname) else []
    try:
        names = os.listdir(head if head else ".")
    except OSError:
        return []
    out = []
    hidden = pat[:1] == "."
    for n in names:
        if n[:1] == "." and not hidden:
            continue
        if fnmatch(n, pat):
            out.append(head + "/" + n if head else n)
    return out


def iglob(pathname, recursive=False):
    return iter(glob(pathname, recursive))


def escape(pathname):
    out = []
    for c in pathname:
        out.append("[" + c + "]" if c in _MAGIC else c)
    return "".join(out)
