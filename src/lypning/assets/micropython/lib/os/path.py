# lypning-mp frozen shim: os.path (posixpath subset). See micropython/lib/README.md.
import uos as _os

sep = "/"
extsep = "."
curdir = "."
pardir = ".."


def normcase(s):
    return s


def isabs(s):
    return s[:1] == "/"


def join(a, *p):
    path = a
    for b in p:
        if b[:1] == "/":
            path = b
        elif path == "" or path[-1:] == "/":
            path += b
        else:
            path += "/" + b
    return path


def split(p):
    i = p.rfind("/") + 1
    head, tail = p[:i], p[i:]
    if head and head.strip("/"):
        head = head.rstrip("/")
    return head, tail


def dirname(p):
    return split(p)[0]


def basename(p):
    return split(p)[1]


def splitext(p):
    si = p.rfind("/")
    di = p.rfind(".")
    if di > si:
        fi = si + 1
        while fi < di:
            if p[fi] != ".":
                return p[:di], p[di:]
            fi += 1
    return p, ""


def normpath(p):
    if p == "":
        return "."
    lead = 0
    if p[:1] == "/":
        lead = 2 if (p[:2] == "//" and p[:3] != "///") else 1
    comps = []
    for c in p.split("/"):
        if c == "" or c == ".":
            continue
        if c != ".." or (not lead and not comps) or (comps and comps[-1] == ".."):
            comps.append(c)
        elif comps:
            comps.pop()
    r = "/".join(comps)
    if lead:
        r = "/" * lead + r
    return r or "."


def abspath(p):
    if p[:1] != "/":
        p = join(_os.getcwd(), p)
    return normpath(p)


def exists(p):
    try:
        _os.stat(p)
        return True
    except OSError:
        return False


lexists = exists


def isdir(p):
    try:
        return _os.stat(p)[0] & 0o170000 == 0o040000
    except OSError:
        return False


def isfile(p):
    try:
        return _os.stat(p)[0] & 0o170000 == 0o100000
    except OSError:
        return False


def getsize(p):
    return _os.stat(p)[6]


def getmtime(p):
    return _os.stat(p)[8]


def expanduser(s):
    if s == "~" or s[:2] == "~/":
        h = _os.getenv("HOME")
        return s if h is None else h + s[1:]
    return s
