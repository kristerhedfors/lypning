# lypning-mp frozen shim: urllib.parse. See micropython/lib/README.md.

_ALWAYS_SAFE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-~"
_HEX = "0123456789ABCDEF"


def quote(s, safe="/", encoding="utf-8", errors=None):
    if isinstance(s, str):
        s = s.encode(encoding)
    if isinstance(safe, bytes):
        safe = safe.decode()
    out = []
    for b in s:
        c = chr(b)
        if c in _ALWAYS_SAFE or c in safe:
            out.append(c)
        else:
            out.append("%" + _HEX[b >> 4] + _HEX[b & 15])
    return "".join(out)


def quote_plus(s, safe="", encoding="utf-8", errors=None):
    if " " in s:
        return quote(s, safe + " ", encoding).replace(" ", "+")
    return quote(s, safe, encoding)


def unquote_to_bytes(s):
    if isinstance(s, str):
        s = s.encode("utf-8")
    parts = s.split(b"%")
    out = bytearray(parts[0])
    for p in parts[1:]:
        try:
            out.append(int(p[:2], 16))
            out.extend(p[2:])
        except ValueError:
            out.extend(b"%")
            out.extend(p)
    return bytes(out)


def unquote(s, encoding="utf-8", errors="replace"):
    if "%" not in s:
        return s
    return unquote_to_bytes(s).decode(encoding)


def unquote_plus(s, encoding="utf-8", errors="replace"):
    return unquote(s.replace("+", " "), encoding)


def urlencode(query, doseq=False, safe="", encoding=None, quote_via=quote_plus):
    items = query.items() if isinstance(query, dict) else query
    out = []
    for k, v in items:
        k = quote_via(k if isinstance(k, str) else str(k), safe)
        if doseq and isinstance(v, (list, tuple)):
            for e in v:
                out.append(k + "=" + quote_via(e if isinstance(e, str) else str(e), safe))
        else:
            out.append(k + "=" + quote_via(v if isinstance(v, str) else str(v), safe))
    return "&".join(out)


class _Split:
    _FIELDS = ("scheme", "netloc", "path", "params", "query", "fragment")

    def __init__(self, scheme, netloc, path, params, query, fragment):
        self.scheme = scheme
        self.netloc = netloc
        self.path = path
        self.params = params
        self.query = query
        self.fragment = fragment

    def __getitem__(self, i):
        return getattr(self, self._FIELDS[i])

    def __len__(self):
        return 6

    def __iter__(self):
        return iter([getattr(self, f) for f in self._FIELDS])

    def geturl(self):
        return urlunparse(self)

    @property
    def hostname(self):
        h = self.netloc.rsplit("@", 1)[-1]
        if h.startswith("["):
            return h[1 : h.find("]")].lower()
        return h.split(":")[0].lower() or None

    @property
    def port(self):
        h = self.netloc.rsplit("@", 1)[-1]
        p = h.rsplit(":", 1)
        return int(p[1]) if len(p) == 2 and p[1].isdigit() else None

    def __repr__(self):
        return (
            "ParseResult(scheme=%r, netloc=%r, path=%r, params=%r, query=%r, fragment=%r)"
            % (
                self.scheme,
                self.netloc,
                self.path,
                self.params,
                self.query,
                self.fragment,
            )
        )


def urlparse(url, scheme="", allow_fragments=True):
    rest = url
    frag = ""
    if allow_fragments and "#" in rest:
        rest, _, frag = rest.partition("#")
    query = ""
    if "?" in rest:
        rest, _, query = rest.partition("?")
    if "://" in rest:
        scheme, _, rest = rest.partition("://")
        scheme = scheme.lower()
        rest = "//" + rest
    elif ":" in rest and rest.split(":", 1)[0].isalpha() and rest[:1].isalpha():
        head, _, tail = rest.partition(":")
        if not head.isdigit():
            scheme = head.lower()
            rest = tail
    netloc = ""
    if rest[:2] == "//":
        rest = rest[2:]
        i = len(rest)
        for c in "/":
            j = rest.find(c)
            if j >= 0 and j < i:
                i = j
        netloc, rest = rest[:i], rest[i:]
    params = ""
    if ";" in rest:
        rest, _, params = rest.partition(";")
    return _Split(scheme, netloc, rest, params, query, frag)


urlsplit = urlparse


def urlunparse(parts):
    scheme, netloc, path, params, query, frag = tuple(parts)
    url = path
    if params:
        url += ";" + params
    if netloc or (scheme and path[:2] != "//"):
        url = "//" + netloc + url
    if scheme:
        url = scheme + ":" + url
    if query:
        url += "?" + query
    if frag:
        url += "#" + frag
    return url


def parse_qsl(qs, keep_blank_values=False, **kw):
    out = []
    for pair in qs.replace(";", "&").split("&"):
        if not pair:
            continue
        k, _, v = pair.partition("=")
        if not v and not keep_blank_values:
            continue
        out.append((unquote_plus(k), unquote_plus(v)))
    return out


def parse_qs(qs, keep_blank_values=False, **kw):
    d = {}
    for k, v in parse_qsl(qs, keep_blank_values):
        if k in d:
            d[k].append(v)
        else:
            d[k] = [v]
    return d


def urljoin(base, url):
    if not base or url[:2] == "//" or "://" in url:
        return url
    b = urlparse(base)
    if url[:1] == "/":
        return urlunparse(_Split(b.scheme, b.netloc, url, "", "", ""))
    head = b.path.rsplit("/", 1)[0]
    return urlunparse(_Split(b.scheme, b.netloc, head + "/" + url, "", "", ""))
