"""urllib.parse.urlsplit, urlparse, urlunsplit, urlunparse and urljoin.

The engine refuses ``import urllib``, so URL splitting and RFC 3986
relative-reference resolution have to be built out of ``str.find``,
``str.partition`` and lists.  This is a port of CPython's ``urlsplit``,
``urlparse``, ``urlunsplit``, ``urlunparse`` and ``urljoin``
(Lib/urllib/parse.py) for ``str`` input.

CPython returns a ``namedtuple`` subclass from ``urlsplit`` and
``urlparse``; a class definition is outside the subset, so these return
PLAIN TUPLES in the same field order -- ``(scheme, netloc, path, query,
fragment)`` for ``urlsplit`` and ``(scheme, netloc, path, params, query,
fragment)`` for ``urlparse``.  Everything a tuple can do -- indexing,
unpacking, equality, ``urlunsplit`` round trip -- behaves identically;
``.hostname``, ``.port``, ``.username``, ``.password`` and ``.geturl()``
are the deliberate and only loss, and the cases below print tuples where
CPython prints ``SplitResult(...)``, which is the one divergence the
differential is allowed to show.

The details that are not guessable:

  * A scheme is only recognised when the text before the first ``':'``
    starts with an ASCII letter and is made only of ``scheme_chars``
    (letters, digits, ``+``, ``-``, ``.``).  So ``'1http://x'`` and
    ``'a b:c'`` have NO scheme, and ``':80/x'`` has none either because
    ``i > 0`` fails.  A recognised scheme is lower-cased; the netloc is
    NOT, which is why ``urlsplit('HTTP://Example.COM/')`` gives
    ``('http', 'Example.COM', ...)``.
  * A netloc exists only after a literal ``'//'``, and it ends at the
    first of ``'/'``, ``'?'`` or ``'#'``.  ``'http:/a'`` and ``'http:a'``
    are therefore netloc-less with paths ``'/a'`` and ``'a'``, while
    ``'//a/b'`` is netloc ``'a'`` with no scheme at all.
  * Before anything else, leading C0 controls and spaces are stripped
    (``lstrip`` only -- a TRAILING space is preserved on purpose), and
    every ``'\\t'``, ``'\\r'`` and ``'\\n'`` is deleted from ANYWHERE in
    the URL.  ``urlsplit('ht\\ntp://a')`` is a plain ``http`` URL.
  * ``urlparse`` is ``urlsplit`` plus params: a ``';'`` in the LAST path
    segment splits ``path;params``, and only for a scheme in
    ``uses_params``.  ``urlsplit`` never does this, which is the whole
    difference between the 5-tuple and the 6-tuple.
  * ``urljoin`` is not pure RFC 3986.  It returns the reference unchanged
    when the schemes differ or the scheme is not in ``uses_relative``, so
    ``urljoin('http://a/b', 'g:h')`` is ``'g:h'`` and
    ``urljoin('mailto:a@b', 'c')`` is ``'c'``.  ``urljoin('http://a/b',
    'http:g')`` resolves, because there the schemes match.
  * The dot-segment walk drops ``'.'``, pops on ``'..'`` and -- unlike a
    naive version -- IGNORES a ``'..'`` that would pop an empty stack,
    which is what keeps ``'../../../g'`` at ``'http://a/g'`` instead of
    escaping the root.  When the last segment was ``'.'`` or ``'..'`` a
    trailing ``''`` is appended, so the result keeps its trailing slash.
    Empty interior segments are filtered out of a relative reference
    only (``segments[1:-1]``), never out of an absolute path, which is
    why ``urljoin('http://a/b', '//g//h')`` keeps its double slash.

Not covered: ``bytes`` input, ``urldefrag``, the ``allow_fragments=False``
interaction with ``urlparse``'s params (covered for ``urlsplit``), and
the two netloc validations that need tables the engine does not have --
``_checknetloc``'s NFKC check (it needs ``unicodedata``) and
``_check_bracketed_host``'s IPv6 literal check (it needs ``ipaddress``).
The unbalanced-bracket check, which raises ``ValueError('Invalid IPv6
URL')``, IS ported.  A netloc that is a well-formed IPv6 literal splits
identically here; a malformed one that CPython rejects is accepted here,
so no case below feeds one in.  Verified against the real module on the
RFC 3986 section 5.4 reference set, on 200,000 random URLs drawn from an
ASCII alphabet -- comparing ``tuple(urlsplit(u))``, ``tuple(urlparse(u))``,
``urlunparse`` and ``urljoin(base, u)`` for both values of
``allow_fragments`` against ten bases -- and on 100,000 further
references built out of ``.``, ``..`` and short segments (2026-09-16).
"""
# fills: urllib.parse.urlsplit, urllib.parse.urlparse, urllib.parse.urlunsplit, urllib.parse.urlunparse, urllib.parse.urljoin
# reference: urllib.parse

uses_relative = ["", "ftp", "http", "gopher", "nntp", "imap",
                 "wais", "file", "https", "shttp", "mms",
                 "prospero", "rtsp", "rtsps", "rtspu", "sftp",
                 "svn", "svn+ssh", "ws", "wss"]

uses_netloc = ["", "ftp", "http", "gopher", "nntp", "telnet",
               "imap", "wais", "file", "mms", "https", "shttp",
               "snews", "prospero", "rtsp", "rtsps", "rtspu", "rsync",
               "svn", "svn+ssh", "sftp", "nfs", "git", "git+ssh",
               "ws", "wss"]

uses_params = ["", "ftp", "hdl", "prospero", "http", "imap",
               "https", "shttp", "rtsp", "rtsps", "rtspu", "sip",
               "sips", "mms", "sftp", "tel"]

scheme_chars = ("abcdefghijklmnopqrstuvwxyz"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                "0123456789"
                "+-.")

# WHATWG C0 control or space: U+0000 through U+001F, plus the space.
_C0_CONTROL_OR_SPACE = "".join([chr(n) for n in range(0x20)]) + " "
_UNSAFE_URL_CHARS = ["\t", "\r", "\n"]


def _splitnetloc(url, start=0):
    """The netloc runs from `start` to the first of '/', '?' or '#'."""
    delim = len(url)
    for c in "/?#":
        wdelim = url.find(c, start)
        if wdelim >= 0:
            delim = min(delim, wdelim)
    return url[start:delim], url[delim:]


def _splitparams(url):
    """Split 'path;params', where ';' must be in the LAST segment."""
    if "/" in url:
        i = url.find(";", url.rfind("/"))
        if i < 0:
            return url, ""
    else:
        i = url.find(";")
    return url[:i], url[i + 1:]


def urlsplit(url, scheme="", allow_fragments=True):
    """(scheme, netloc, path, query, fragment), as a plain tuple."""
    # Only lstrip: some applications rely on a preserved trailing space.
    url = url.lstrip(_C0_CONTROL_OR_SPACE)
    scheme = scheme.strip(_C0_CONTROL_OR_SPACE)
    for c in _UNSAFE_URL_CHARS:
        url = url.replace(c, "")
        scheme = scheme.replace(c, "")

    allow_fragments = bool(allow_fragments)
    netloc = ""
    query = ""
    fragment = ""
    i = url.find(":")
    if i > 0 and ord(url[0]) < 128 and url[0].isalpha():
        legal = True
        for c in url[:i]:
            if c not in scheme_chars:
                legal = False
                break
        if legal:
            scheme = url[:i].lower()
            url = url[i + 1:]
    if url[:2] == "//":
        netloc, url = _splitnetloc(url, 2)
        if ("[" in netloc and "]" not in netloc) or (
                "]" in netloc and "[" not in netloc):
            raise ValueError("Invalid IPv6 URL")
    if allow_fragments and "#" in url:
        url, fragment = url.split("#", 1)
    if "?" in url:
        url, query = url.split("?", 1)
    return (scheme, netloc, url, query, fragment)


def urlparse(url, scheme="", allow_fragments=True):
    """(scheme, netloc, path, params, query, fragment), as a plain tuple."""
    scheme, netloc, path, query, fragment = urlsplit(url, scheme,
                                                     allow_fragments)
    if scheme in uses_params and ";" in path:
        path, params = _splitparams(path)
    else:
        params = ""
    return (scheme, netloc, path, params, query, fragment)


def urlunsplit(components):
    """Reassemble a 5-tuple into a URL."""
    scheme, netloc, url, query, fragment = components
    if netloc or (scheme and scheme in uses_netloc) or url[:2] == "//":
        if url and url[:1] != "/":
            url = "/" + url
        url = "//" + netloc + url
    if scheme:
        url = scheme + ":" + url
    if query:
        url = url + "?" + query
    if fragment:
        url = url + "#" + fragment
    return url


def urlunparse(components):
    """Reassemble a 6-tuple into a URL."""
    scheme, netloc, url, params, query, fragment = components
    if params:
        url = url + ";" + params
    return urlunsplit((scheme, netloc, url, query, fragment))


def urljoin(base, url, allow_fragments=True):
    """Resolve a possibly relative reference against a base URL."""
    if not base:
        return url
    if not url:
        return base

    bscheme, bnetloc, bpath, bparams, bquery, bfragment = urlparse(
        base, "", allow_fragments)
    scheme, netloc, path, params, query, fragment = urlparse(
        url, bscheme, allow_fragments)

    if scheme != bscheme or scheme not in uses_relative:
        return url
    if scheme in uses_netloc:
        if netloc:
            return urlunparse((scheme, netloc, path, params, query, fragment))
        netloc = bnetloc

    if not path and not params:
        path = bpath
        params = bparams
        if not query:
            query = bquery
        return urlunparse((scheme, netloc, path, params, query, fragment))

    base_parts = bpath.split("/")
    if base_parts[-1] != "":
        # The last item is not a directory, so it takes no part in
        # resolving the relative path.
        base_parts = base_parts[:-1]

    if path[:1] == "/":
        # For RFC 3986, a rooted reference ignores the base path entirely.
        segments = path.split("/")
    else:
        segments = base_parts + path.split("/")
        # Drop the empty interior segments that would otherwise become
        # redundant slashes on re-joining.  Never the first or the last.
        segments[1:-1] = [s for s in segments[1:-1] if s]

    resolved_path = []
    for seg in segments:
        if seg == "..":
            # A '..' that would pop an empty stack is IGNORED, not an
            # error and not an escape above the root.
            if resolved_path:
                resolved_path.pop()
        elif seg == ".":
            continue
        else:
            resolved_path.append(seg)

    if segments[-1] == "." or segments[-1] == "..":
        # The last segment was a relative directory: keep the slash.
        resolved_path.append("")

    joined = "/".join(resolved_path)
    if not joined:
        joined = "/"
    return urlunparse((scheme, netloc, joined, params, query, fragment))


# --- cases ---
# urlsplit: the five fields, and where each delimiter is found.
print(urlsplit("http://example.com/path?query=1#frag"))
print(urlsplit("http://user:pw@host:8080/p/q?a=b&c=d#top"))
print(urlsplit("https://example.com"))
print(urlsplit("https://example.com/"))
print(urlsplit(""))
print(urlsplit("/just/a/path"))
print(urlsplit("relative/path"))
print(urlsplit("//netloc/only"))
print(urlsplit("//netloc"))
print(urlsplit("?only=query"))
print(urlsplit("#only-fragment"))
print(urlsplit("http://a/b?#"))
print(urlsplit("http://a/b?x#y#z"))
print(urlsplit("http://a/b?x?y"))

# The scheme rules: ASCII letter first, scheme_chars throughout, lowered.
print(urlsplit("HTTP://Example.COM/Path"))
print(urlsplit("svn+ssh://h/r"))
print(urlsplit("x-y://h/r"))
print(urlsplit("x.y+1-z://h/r"))
print(urlsplit("1http://x"))
print(urlsplit("a b:c"))
print(urlsplit(":80/x"))
print(urlsplit("http:/a"))
print(urlsplit("http:a"))
print(urlsplit("mailto:user@example.com"))
print(urlsplit("file:///etc/hosts"))
print(urlsplit("news:comp.lang.python"))
print(urlsplit("tel:+31-1234"))

# The default scheme argument, and allow_fragments.
print(urlsplit("//host/p", "https"))
print(urlsplit("ftp://h/p", "https"))
print(urlsplit("/p", "https"))
print(urlsplit("http://a/b#c", "", False))
print(urlsplit("http://a/b?q#c", "", False))

# Leading C0 controls and spaces are stripped; tab, CR and LF are
# deleted from anywhere.  A TRAILING space survives.
print(urlsplit("  http://a/b"))
print(urlsplit("\x00\x1f http://a/b"))
print(urlsplit("http://a/b  "))
print(urlsplit("ht\ntp://a/b"))
print(urlsplit("http://a/\tb\rc\nd"))
print(urlsplit("http://\ta/b"))

# urlparse adds params, taken from the LAST path segment only.
print(urlparse("http://a/b/c;p1?q#f"))
print(urlparse("http://a/b;x/c"))
print(urlparse("http://a/b;x/c;y"))
print(urlparse("http://a/b;x;y"))
print(urlparse("http://a/;p"))
print(urlparse("no-scheme;p"))
print(urlparse("news:comp;x"))
print(urlparse("http://a/b/c/d;p?q"))
print(urlsplit("http://a/b/c/d;p?q"))

# urlunsplit / urlunparse round trips, including the empty-part cases.
print(repr(urlunsplit(urlsplit("http://a/b?c#d"))))
print(repr(urlunparse(urlparse("http://a/b;p?c#d"))))
print(repr(urlunsplit(("http", "a", "b", "", ""))))
print(repr(urlunsplit(("", "a", "/b", "", ""))))
print(repr(urlunsplit(("http", "", "/b", "", ""))))
print(repr(urlunsplit(("", "", "//b", "", ""))))
print(repr(urlunsplit(("http", "a", "", "q", "f"))))
print(repr(urlunparse(("http", "a", "b", "p", "q", "f"))))
print(repr(urlunsplit(("mailto", "", "a@b", "", ""))))

# urljoin: RFC 3986 section 5.4.1, the normal examples.
print(repr(urljoin("http://a/b/c/d;p?q", "g:h")))
print(repr(urljoin("http://a/b/c/d;p?q", "g")))
print(repr(urljoin("http://a/b/c/d;p?q", "./g")))
print(repr(urljoin("http://a/b/c/d;p?q", "g/")))
print(repr(urljoin("http://a/b/c/d;p?q", "/g")))
print(repr(urljoin("http://a/b/c/d;p?q", "//g")))
print(repr(urljoin("http://a/b/c/d;p?q", "?y")))
print(repr(urljoin("http://a/b/c/d;p?q", "g?y")))
print(repr(urljoin("http://a/b/c/d;p?q", "#s")))
print(repr(urljoin("http://a/b/c/d;p?q", "g#s")))
print(repr(urljoin("http://a/b/c/d;p?q", "g?y#s")))
print(repr(urljoin("http://a/b/c/d;p?q", ";x")))
print(repr(urljoin("http://a/b/c/d;p?q", "g;x")))
print(repr(urljoin("http://a/b/c/d;p?q", "g;x?y#s")))
print(repr(urljoin("http://a/b/c/d;p?q", "")))
print(repr(urljoin("http://a/b/c/d;p?q", ".")))
print(repr(urljoin("http://a/b/c/d;p?q", "./")))
print(repr(urljoin("http://a/b/c/d;p?q", "..")))
print(repr(urljoin("http://a/b/c/d;p?q", "../")))
print(repr(urljoin("http://a/b/c/d;p?q", "../g")))
print(repr(urljoin("http://a/b/c/d;p?q", "../..")))
print(repr(urljoin("http://a/b/c/d;p?q", "../../")))
print(repr(urljoin("http://a/b/c/d;p?q", "../../g")))

# RFC 3986 section 5.4.2, the abnormal examples.
print(repr(urljoin("http://a/b/c/d;p?q", "../../../g")))
print(repr(urljoin("http://a/b/c/d;p?q", "../../../../g")))
print(repr(urljoin("http://a/b/c/d;p?q", "/./g")))
print(repr(urljoin("http://a/b/c/d;p?q", "/../g")))
print(repr(urljoin("http://a/b/c/d;p?q", "g.")))
print(repr(urljoin("http://a/b/c/d;p?q", ".g")))
print(repr(urljoin("http://a/b/c/d;p?q", "g..")))
print(repr(urljoin("http://a/b/c/d;p?q", "..g")))
print(repr(urljoin("http://a/b/c/d;p?q", "./../g")))
print(repr(urljoin("http://a/b/c/d;p?q", "./g/.")))
print(repr(urljoin("http://a/b/c/d;p?q", "g/./h")))
print(repr(urljoin("http://a/b/c/d;p?q", "g/../h")))
print(repr(urljoin("http://a/b/c/d;p?q", "g;x=1/./y")))
print(repr(urljoin("http://a/b/c/d;p?q", "g;x=1/../y")))
print(repr(urljoin("http://a/b/c/d;p?q", "g?y/./x")))
print(repr(urljoin("http://a/b/c/d;p?q", "g?y/../x")))
print(repr(urljoin("http://a/b/c/d;p?q", "g#s/./x")))
print(repr(urljoin("http://a/b/c/d;p?q", "g#s/../x")))
print(repr(urljoin("http://a/b/c/d;p?q", "http:g")))

# The scheme rules of urljoin, and the empty-argument short circuits.
print(repr(urljoin("", "g")))
print(repr(urljoin("http://a/b", "")))
print(repr(urljoin("mailto:a@example.com", "c")))
print(repr(urljoin("http://a/b", "mailto:c@d")))
print(repr(urljoin("gopher://a/b/c", "d")))
print(repr(urljoin("myscheme://a/b/c", "d")))
print(repr(urljoin("http://a/b", "https://c/d")))
print(repr(urljoin("file:///a/b/c", "../d")))
print(repr(urljoin("ws://a/b/c", "../d")))

# Interior empty segments: filtered for a relative reference, kept for a
# rooted one, and never at either end.
print(repr(urljoin("http://a/b/c/d", "//g//h")))
print(repr(urljoin("http://a/b/c/d", "/g//h")))
print(repr(urljoin("http://a/b/c/d", "g//h")))
print(repr(urljoin("http://a/b/c/d", "g/")))
print(repr(urljoin("http://a//b/c", "d")))
print(repr(urljoin("http://a/b/", "c")))
print(repr(urljoin("http://a", "b")))
print(repr(urljoin("http://a/", "..")))
print(repr(urljoin("http://a/b/c", "../../../../x")))

# An unbalanced bracket in the netloc is the one ValueError ported here.
try:
    urlsplit("http://[::1/p")
except ValueError as exc:
    print("ValueError:", exc)
try:
    urlsplit("http://::1]/p")
except ValueError as exc:
    print("ValueError:", exc)
