"""The frozen shim stdlib, differentially tested against live CPython.

The invariant: **every shim in ``assets/micropython/lib`` produces byte-identical
output to the CPython module it replaces, or the divergence is written down.**

The shims are pure Python, so they run under CPython directly. That is the whole
point of this file: every case below is executed TWICE in the same interpreter —
once against CPython's own stdlib and once against the shim tree — and the two
outputs must match byte for byte. Reasoning about what CPython does is exactly
how silent semantic divergence gets shipped (``micropython/lib/README.md``,
"Divergences"), so nothing here is asserted from memory: CPython is the oracle,
live, on every run.

The shim run does not get CPython's modules underneath it either. :data:`_DRIVER`
installs RESTRICTED stand-ins for the MicroPython C modules the shims import
(``ure``, ``uos``, ``ujson``, ``uhashlib``, ``ucollections``), each cut back to
the surface MicroPython actually ships — ``uos.stat`` returns a bare 10-tuple,
``uhashlib`` has no ``hexdigest()``, ``ure`` has no findall/split/flags/named
groups and models re1.5's quirks (``.`` matches ``\\n``, ``$`` is end-of-string
only, ``\\d\\w\\s`` are ASCII). So a shim that leaned on a CPython convenience
fails here rather than in the sandbox, which is the only place the failure would
otherwise show up — and there it prints a plausible wrong answer and exits 0.

**No engine binary is needed**, which is why this suite is cheap enough to run
everywhere; ``lypning conformance`` is the binary-level check and needs a
network-built tier. The two runs are spawned as fresh interpreters because
module-level import shadowing cannot be undone in-process: once ``os`` is
imported, every module that did ``import os`` holds the object, and popping
``sys.modules`` moves nothing.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pytest

from lypning import engines, paths

# --- the driver ---------------------------------------------------------------
#
# Written to the scratch dir and run twice, once per mode. It is a raw string
# because it is Python source, not a Python value: every backslash in it belongs
# to the program being built, not to this file.

_DRIVER = r'''
import sys, io, json, os as _real_os

MODE = sys.argv[1]
LIB = sys.argv[2]
CASES = json.loads(sys.stdin.read())

if MODE == "shim":
    import re as _re, os as _os, hashlib as _hashlib, json as _json
    import collections as _collections, binascii as _binascii

    # --- ure: MicroPython's re1.5, modelled honestly -----------------------
    # Only compile/match/search exist; match objects expose group(int),
    # groups(), start/end/span(int). No flags, no named groups, no findall.
    # Engine quirks reproduced: '.' matches \n (Any), '$' is end-of-string
    # only (Eol), \d\w\s are ASCII.
    class _MPMatch:
        # _shift maps an offset in whatever slice the engine was pointed at
        # back to a character index in the whole subject, which is what the C
        # match object reports because its caps point into the original buffer.
        def __init__(self, m, _shift=None):
            self._m = m
            self._shift = _shift or (lambda i: i)
        def group(self, n):
            if not isinstance(n, int):
                raise TypeError("MicroPython match.group takes an int")
            return self._m.group(n)
        def groups(self):
            return self._m.groups()
        def start(self, n=0):
            i = self._m.start(n)
            return i if i < 0 else self._shift(i)
        def end(self, n=0):
            i = self._m.end(n)
            return i if i < 0 else self._shift(i)
        def span(self, n=0):
            return (self.start(n), self.end(n))

    def _reject(pat):
        for bad in ("(?P<", "(?=", "(?!", "(?<"):
            if bad in pat:
                raise AssertionError("re1.5 cannot compile " + bad + " in " + repr(pat))
        i = 0
        while i < len(pat):
            if pat[i] == "\\":
                if pat[i + 1:i + 2] in ("b", "B", "A", "Z"):
                    raise AssertionError("re1.5 has no " + pat[i:i + 2])
                i += 2
                continue
            if pat[i] == "{" and _re.match(r"\{\d+(,\d*)?\}", pat[i:]):
                raise AssertionError("re1.5 has no {n,m} in " + repr(pat))
            i += 1

    def _eol(pat):
        # re1.5's Eol matches only at the very end of the subject.
        out = []
        i = 0
        while i < len(pat):
            c = pat[i]
            if c == "\\":
                out.append(pat[i:i + 2]); i += 2; continue
            if c == "[":
                j = i + 1
                if pat[j:j + 1] == "^": j += 1
                if pat[j:j + 1] == "]": j += 1
                while j < len(pat) and pat[j] != "]":
                    j += 2 if pat[j] == "\\" else 1
                out.append(pat[i:j + 1]); i = j + 1; continue
            out.append("\\Z" if c == "$" else c)
            i += 1
        return "".join(out)

    class _MPPattern:
        def __init__(self, pat):
            _reject(pat)
            self._r = _re.compile(_eol(pat), _re.DOTALL | _re.ASCII)
        def search(self, s, pos=None, endpos=None):
            if pos is None and endpos is None:
                m = self._r.search(s)
                return _MPMatch(m) if m else None
            # pos and endpos are BYTE offsets, and .span() answers in
            # CHARACTERS. That asymmetry is not a quirk of this stub: in
            # extmod/modre.c, re_exec_helper advances subj.begin by the raw
            # integer while match_span_helper converts the result back with
            # utf8_ptr_to_index. Modelling pos as a character index here would
            # make the shim's ASCII gate look like belt-and-braces, and the
            # test would stop covering the reason it exists.
            b = s.encode()
            start = min(max(pos or 0, 0), len(b))
            if endpos is not None:
                b = b[:min(max(endpos, start), len(b))]
            m = self._r.search(b[start:].decode("utf-8", "replace"))
            if not m:
                return None
            return _MPMatch(m, _shift=lambda i: len(
                b[:start + len(m.string[:i].encode())].decode("utf-8", "replace")))
        def match(self, s):
            m = self._r.match(s)
            return _MPMatch(m) if m else None
        def sub(self, repl, s, count=0):
            # re_sub_helper's own loop, divergences included — this is the fast
            # path micropython/lib/re.py delegates to, and the point of the stub
            # is that a wrong delegation FAILS here rather than in production.
            out, at, n = [], 0, 0
            while True:
                m = self._r.search(s, at)
                if not m or m.start() == m.end():
                    break            # an empty match ENDS the native loop
                out.append(s[at:m.start()])
                i, t = 0, repl
                while i < len(t):
                    if t[i] != "\\":
                        out.append(t[i]); i += 1; continue
                    i += 1
                    if t[i:i + 2] == "g<":
                        i += 2       # \g<number> only; \g<name> is not parsed
                    if i < len(t) and t[i].isdigit():
                        g = ""
                        while i < len(t) and t[i].isdigit():
                            g += t[i]; i += 1
                        if i < len(t) and t[i] == ">":
                            i += 1
                        out.append(m.group(int(g)) or "")
                    elif t[i:i + 1] == "\\":
                        out.append("\\"); i += 1
                    # anything else: the backslash is simply dropped, so \n
                    # comes out as the letter n
                at = m.end()
                n += 1
                if count > 0 and n >= count:
                    break
            out.append(s[at:])
            return "".join(out)

    ure = type(sys)("ure")
    ure.compile = lambda pat, flags=0: _MPPattern(pat)
    ure.search = lambda pat, s: _MPPattern(pat).search(s)
    ure.match = lambda pat, s: _MPPattern(pat).match(s)
    ure.sub = lambda pat, repl, s, count=0: _MPPattern(pat).sub(repl, s, count)

    # --- uos: only the names MicroPython's C os module has -----------------
    uos = type(sys)("uos")
    for _n in ("getcwd", "chdir", "listdir", "mkdir", "remove", "rename",
               "rmdir", "unlink", "statvfs", "urandom"):
        setattr(uos, _n, getattr(_os, _n))
    uos.stat = lambda p: tuple(_os.stat(p))     # a bare 10-tuple, no attributes
    uos.getenv = lambda k: _os.environ.get(k)   # returns None, not KeyError
    uos.putenv = lambda k, v: _os.environ.__setitem__(k, v)
    uos.sep = "/"

    # --- ujson / uhashlib / ucollections -----------------------------------
    ujson = type(sys)("ujson")
    ujson.loads = _json.loads
    ujson.load = _json.load

    class _MPHash:
        def __init__(self, h):
            self._h = h
        def update(self, b):
            self._h.update(b)
        def digest(self):
            return self._h.digest()        # deliberately no hexdigest()

    uhashlib = type(sys)("uhashlib")
    for _n in ("sha256", "sha1", "md5"):
        def _mk(name=_n):
            return lambda data=b"": _MPHash(getattr(_hashlib, name)(data))
        setattr(uhashlib, _n, _mk())

    ucollections = type(sys)("ucollections")
    ucollections.deque = _collections.deque
    ucollections.namedtuple = _collections.namedtuple
    ucollections.OrderedDict = _collections.OrderedDict

    # --- binascii: MicroPython's, which has no Error ------------------------
    #
    # Not a u-prefixed name and not shimmed in this directory — the shims import
    # `binascii` and get the C module. Modelled here because the ONE thing that
    # matters about it is an absence: MicroPython's binascii defines no `Error`,
    # so `a2b_base64` raises a bare ValueError where CPython raises
    # `binascii.Error`. Without this stand-in the shim run imported CPython's
    # binascii, `base64.py` got the real `Error` for free, and a divergence that
    # is live in the sandbox could not fail here. It shipped that way until a
    # harvested corpus entry printed `type(e).__name__` and the routing gate
    # caught it as an UNSAFE route.
    _B64_ALPHABET = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

    def _mp_a2b(data):
        """A transcription of `extmod/modbinascii.c`, not a wrapper around CPython's.

        It used to be the wrapper, lower-casing CPython's message on the way out.
        That model was wrong in a way only the real binary could show, and it was
        wrong in the direction that hides work: CPython's decoder REACHES A
        DIFFERENT VERDICT from MicroPython's on the same bytes. `b64decode(b"a")`
        raises `Invalid base64-encoded string: number of data characters (1)…`
        there and a flat `incorrect padding` here, so a lower-cased CPython
        message modelled a divergence that does not exist and hid one that does.
        Verified against a built lypning-mp: this function and the C agree.

        The loop is the C loop. `hadpad`, the two `nbits` tests and the trailing
        `if nbits` are what decide every padding message the shim has to
        translate, so they are transcribed rather than approximated.
        """
        shift = 0
        nbits = 0
        hadpad = False
        out = bytearray()
        for ch in bytes(data):
            if ch == 0x3D:  # b"="
                if nbits == 2 or (nbits == 4 and hadpad):
                    nbits = 0
                    break
                hadpad = True
            sextet = _B64_ALPHABET.find(bytes([ch]))
            if sextet < 0:
                continue
            hadpad = False
            shift = (shift << 6) | sextet
            nbits += 6
            if nbits >= 8:
                nbits -= 8
                out.append((shift >> nbits) & 0xFF)
        if nbits:
            # No `Error` to raise, and the message is MicroPython's own.
            raise ValueError("incorrect padding")
        return bytes(out)

    def _mp_b2a(data, newline=True):
        # MicroPython's takes anything with a buffer AND a str; CPython's rejects
        # the str. Modelled as the absence of a check, because the check now
        # lives in the shim and a model that kept CPython's would test nothing.
        if isinstance(data, str):
            data = data.encode()
        return _binascii.b2a_base64(bytes(data), newline=newline)

    binascii = type(sys)("binascii")
    binascii.a2b_base64 = _mp_a2b
    binascii.b2a_base64 = _mp_b2a
    binascii.hexlify = _binascii.hexlify
    binascii.unhexlify = _binascii.unhexlify
    binascii.crc32 = _binascii.crc32
    # and deliberately NO `binascii.Error`.

    for _mod in (ure, uos, ujson, uhashlib, ucollections, binascii):
        sys.modules[_mod.__name__] = _mod

    # Drop the CPython modules the shims replace, then put the shim tree first.
    for _n in ("re", "os", "os.path", "json", "hashlib", "collections",
               "base64", "glob", "textwrap", "datetime", "csv", "urllib",
               "urllib.parse", "statistics", "shutil", "tempfile", "pathlib",
               "contextlib", "posixpath", "unicodedata"):
        sys.modules.pop(_n, None)
    sys.path.insert(0, LIB)

    # sys.path alone is NOT enough, and believing it was is how nine os/os.path
    # cases can pass while comparing CPython with itself. CPython 3.11 deep-
    # freezes `os` and `posixpath`, and sys.meta_path runs FrozenImporter BEFORE
    # PathFinder — so `import os` after the pop above resolves to the frozen
    # module no matter what sys.path[0] says, and os.__file__ still points at
    # /usr/lib, which makes the deception look like success. A meta_path entry
    # ahead of the frozen one is also the faithful model: MicroPython registers
    # these as EXTENSIBLE built-ins, where the frozen table is searched first and
    # the C module is only reached under its u-prefixed alias.
    import importlib.machinery as _mach

    _TOP = set()
    for _e in _real_os.listdir(LIB):
        _p = _real_os.path.join(LIB, _e)
        if _e.endswith(".py"):
            _TOP.add(_e[:-3])
        elif _real_os.path.isdir(_p) and "__init__.py" in _real_os.listdir(_p):
            _TOP.add(_e)

    class _ShimFinder:
        # Submodules are routed too, not just top-level names. `import os.path`
        # arrives here with the parent's __path__ already pointing into the shim
        # tree, but handing it back to the default chain does not help: the
        # frozen table carries `os.path` as an ALIAS for posixpath, and
        # FrozenImporter ignores the path argument entirely. So os.path would
        # come out of /usr/lib while os came out of LIB — a mixed run, and every
        # shim that calls os.path.join would be testing CPython's.
        @staticmethod
        def find_spec(name, path=None, target=None):
            if name.split(".")[0] not in _TOP:
                return None
            return _mach.PathFinder.find_spec(
                name, [LIB] if path is None else list(path), target)

    sys.meta_path.insert(0, _ShimFinder)

out = {}
for case in CASES:
    cid, code = case["id"], case["code"]
    d = _real_os.path.join(case["cwd"], cid)
    _real_os.makedirs(d, exist_ok=True)
    _real_os.chdir(d)
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        exec(compile(code, cid, "exec"), {"__name__": "__main__"})
        status = "ok"
    except BaseException as e:
        status = type(e).__name__ + ": " + str(e)
    finally:
        sys.stdout = saved
    out[cid] = [status, buf.getvalue()]

sys.stdout.write(json.dumps(out))
sys.stdout.flush()
_real_os._exit(0)
'''

# --- the cases ----------------------------------------------------------------
#
# ``(module, id, code)``. ``code`` is a whole program and its stdout is what gets
# compared; the module is the shim it exercises, which is what a failure has to
# name for the report to be actionable. Anything that touches the filesystem runs
# in its own scratch directory, so a case can create files without seeing
# another case's — and, more to the point, without seeing the *other run's*.

CASES: Tuple[Tuple[str, str, str], ...] = (
    # ---- base64 --------------------------------------------------------------
    ("base64", "base64-roundtrip", r'''import base64
b = base64.b64encode(b"hello world")
print(b, base64.b64decode(b), base64.b64decode(b.decode()))'''),
    ("base64", "base64-urlsafe", r'''import base64
raw = bytes([251, 255, 190])
print(base64.urlsafe_b64encode(raw), base64.urlsafe_b64decode(base64.urlsafe_b64encode(raw)))'''),
    ("base64", "base64-padding", r'''import base64
for n in range(1, 6):
    print(n, base64.b64encode(b"x" * n), base64.b64decode(base64.b64encode(b"x" * n)))'''),
    ("base64", "base64-empty", r'''import base64
print(base64.b64encode(b""), base64.b64decode(b""))'''),
    ("base64", "base64-altchars", r'''import base64
print(base64.b64encode(bytes([251, 255, 190]), b"@$"))'''),
    # The exception's TYPE, not its message. CPython's base64 raises
    # binascii.Error; MicroPython's binascii has no Error and raises a bare
    # ValueError. A harvested corpus entry prints exactly this and it surfaced
    # as an UNSAFE route (README divergence 17).
    ("base64", "base64-bad-padding-exception-name", r'''import base64
for c in [b"aGk", b"a", b"aGkxaGk", b"aGk=aGk=", b"====", b"aa=="]:
    try:
        print(c, repr(base64.b64decode(c)))
    except Exception as e:
        print(c, "RAISE", type(e).__name__)'''),
    # The subclassing half: whatever it is called, `except ValueError` must
    # still catch it, or the fix would break every program that already worked.
    # The QUALIFIED name, which is what a program printing an exception gets from
    # repr(), traceback, or `%s.%s % (type(e).__module__, type(e).__name__)`.
    # Defining `Error` in base64.py made it `base64.Error`; CPython's lives in
    # binascii. A corpus entry prints exactly this.
    ("base64", "base64-error-qualified-name", r'''import base64
for c in [b"aGk", b"a"]:
    try:
        base64.b64decode(c)
    except Exception as e:
        t = type(e)
        print(c, "%s.%s" % (t.__module__, t.__name__))'''),
    ("base64", "base64-error-is-a-valueerror", r'''import base64
try:
    base64.b64decode(b"aGk")
except ValueError as e:
    print("caught as ValueError:", type(e).__name__)
try:
    base64.b64decode(b"aGk")
except Exception as e:
    print("isinstance ValueError:", isinstance(e, ValueError))'''),
    # The message TEXT, which is the half divergence 17 had left open. Two
    # different verdicts, not two spellings of one: MicroPython's decoder says
    # `incorrect padding` for every unfinished quad, and CPython separates out
    # the one-more-than-a-multiple-of-4 case with a character count.
    ("base64", "base64-padding-message-text", r'''import base64
for c in [b"aGk", b"a", b"aGkxx", b"aGkxaGk", b"=aGk", b"aGkxaGkxa"]:
    try:
        print(c, repr(base64.b64decode(c)))
    except Exception as e:
        print(c, "RAISE", e)'''),
    # `validate=True` was accepted and IGNORED, so this returned b'hi' where
    # CPython raises. Not a message difference — the tier answering "is this
    # valid base64" with the wrong answer. The four strict messages are
    # CPython 3.11+; see the note in lib/base64.py about older references.
    ("base64", "base64-validate-rejects", r'''import base64
for c in [b"a!Gk=", b"aG k=", b"\n\naGk=", b"====", b"=aGk", b"aGk==", b"aGk=aGk=",
          b"a=a", b"aGk=\n", b"-_--", b"aGk=", b"aGkx", b"", b"aa=="]:
    try:
        print(c, repr(base64.b64decode(c, validate=True)))
    except Exception as e:
        print(c, "RAISE", e)'''),
    # b64encode took a str and encoded it; CPython raises. Encoding text without
    # being told the codec is a guess, and the tier must not guess.
    # Labelled rather than repr()'d: a memoryview's repr carries its address,
    # which differs between two runs of the SAME interpreter and would make this
    # case fail forever for a reason that is not about base64.
    ("base64", "base64-encode-rejects-non-bytes", r'''import base64
for label, a in [("str", "hi"), ("int", 123), ("None", None), ("bytes", b"hi"),
                 ("bytearray", bytearray(b"hi")), ("memoryview", memoryview(b"hi"))]:
    try:
        print(label, repr(base64.b64encode(a)))
    except Exception as e:
        print(label, "RAISE", type(e).__name__, e)'''),

    # ---- os.path -------------------------------------------------------------
    ("os.path", "ospath-join", r'''import os.path as p
print(p.join("a", "b", "c.txt"), p.join("/a", "b"), p.join("a", "/b"), p.join("a/", "b"), p.join("", "b"))'''),
    ("os.path", "ospath-split", r'''import os.path as p
for s in ["a/b/c.txt", "c.txt", "/a", "/", "a/", "", "//a//b"]:
    print(repr(s), p.split(s), repr(p.dirname(s)), repr(p.basename(s)))'''),
    ("os.path", "ospath-splitext", r'''import os.path as p
for s in ["a/b/c.txt", ".bashrc", "a.tar.gz", "noext", "a/.x", "x.", "a.b/c"]:
    print(repr(s), p.splitext(s))'''),
    ("os.path", "ospath-normpath", r'''import os.path as p
for s in ["a//b/../c", "./a", "../a", "/../a", "//a/b", "///a", "", ".", "a/b/../.."]:
    print(repr(s), repr(p.normpath(s)))'''),
    ("os.path", "ospath-stat", r'''import os, os.path as p
open("f.txt", "w").write("12345")
os.makedirs("d/e", exist_ok=True)
os.makedirs("d/e", exist_ok=True)
print(p.exists("f.txt"), p.isfile("f.txt"), p.isdir("f.txt"))
print(p.exists("d/e"), p.isfile("d/e"), p.isdir("d/e"))
print(p.exists("nope"), p.isfile("nope"), p.isdir("nope"))
print(p.getsize("f.txt"), os.stat("f.txt").st_size, os.stat("f.txt")[6])'''),
    ("os.path", "ospath-abspath", r'''import os, os.path as p
print(p.abspath("x") == p.join(os.getcwd(), "x"), p.abspath("/a/../b"), p.isabs("/a"), p.isabs("a"))'''),

    # ---- os ------------------------------------------------------------------
    ("os", "os-walk", r'''import os
os.makedirs("w/sub", exist_ok=True)
open("w/one.txt", "w").write("")
open("w/sub/two.txt", "w").write("")
n = 0
for root, dirs, files in os.walk("w"):
    n += len(files)
print(n)
print(sorted(os.listdir("w")))'''),
    ("os", "os-environ", r'''import os
print(os.environ.get("LYPNING_NOPE", "default"))
print("PATH" in os.environ, "LYPNING_NOPE" in os.environ)
print(os.getenv("LYPNING_NOPE", "d2"))'''),
    ("os", "os-remove-rename", r'''import os, os.path as p
open("t1", "w").write("x")
os.rename("t1", "t2")
print(p.exists("t1"), p.exists("t2"))
os.remove("t2")
print(p.exists("t2"))'''),

    # ---- re ------------------------------------------------------------------
    ("re", "re-findall", r'''import re
print(re.findall(r"\d+", "a1 bb22 c333"))
print(re.findall(r"a*", "abc"))
print(re.findall(r"(\w)=(\d)", "a=1 b=2"))
print(re.findall(r"(a)|(b)", "ab"))
print(re.findall(r"zzz", "abc"))'''),
    ("re", "re-finditer", r'''import re
for m in re.finditer(r"\d+", "a1 bb22 c333"):
    print(m.group(0), m.start(), m.end(), m.span())'''),
    ("re", "re-split", r'''import re
print(re.split(r"[,;]\s*", "a, b;c ,d"))
print(re.split(r",", "a,b,c", 1))
print(re.split(r"(,)", "a,b"))
print(re.split(r"x*", "abc"))'''),
    ("re", "re-sub", r'''import re
print(re.sub(r"foo+", "BAR", "foo fooo food"))
print(re.sub(r"(\w+)=(\w+)", r"\2:\1", "a=1 b=2"))
print(re.sub(r"a", "X", "aaaa", count=2))
print(re.sub(r"\d+", lambda m: str(int(m.group()) + 1), "a1 b9"))
print(re.subn(r"a", "X", "banana"))
print(re.sub(r"a", "-\n-", "za"))'''),
    ("re", "re-match-vs-search", r'''import re
print(bool(re.match(r"bar", "foobar")), bool(re.search(r"bar", "foobar")))
print(re.search(r"zzz", "abc") is None)
m = re.search(r"b+", "abbbc")
print(m.group(0), m.start(), m.end(), m.span())'''),
    ("re", "re-named-groups", r'''import re
m = re.search(r"(?P<k>\w+)=(?P<v>\d+)", "port=8080")
print(m.group("k"), m.group("v"), m.groups(), m.group(1, 2))
print(sorted(m.groupdict().items()))
print(sorted(re.compile(r"(?P<a>x)(?P<b>y)").groupindex.items()))'''),
    ("re", "re-flags", r'''import re
print(re.findall(r"^\w+", "one two\nthree four", re.M))
rx = re.compile(r"error", re.I)
print(len(rx.findall("Error ERROR error")), rx.findall("Error ERROR error"))
print(re.findall(r"[a-f]+", "ABCxyz", re.I))
print(re.sub(r"a", "-", "AaA", flags=re.I))'''),
    ("re", "re-dot-newline", r'''import re
print(re.findall(r"a.c", "a\nc abc"))
print(re.findall(r"a.c", "a\nc abc", re.S))
print(re.sub(r"#.*", "", "x #c\ny"))'''),
    ("re", "re-anchors", r'''import re
print(re.findall(r"^a", "aaa"))
print(re.findall(r"^a", "a\na\na", re.M))
print(re.search(r"c$", "abc") is not None, re.search(r"c$", "abc\n") is not None)
print(re.findall(r"\w+$", "one two"))'''),
    ("re", "re-escape", r'''import re
print(re.escape("a.b*c"))
print(re.escape("a b+c[d]{e}|f^g$h#i&j~k"))
print(re.findall(re.escape("a.c"), "a.c abc"))'''),
    ("re", "re-groups-unmatched", r'''import re
m = re.search(r"(a)(b)?", "a")
print(m.groups(), m.groups("-"), m.group(2), m.span(2))'''),
    ("re", "re-braces", r'''import re
print(re.findall(r"\d{3}", "12 345 6789"))
print(re.findall(r"a{2,3}", "a aa aaa aaaa"))
print(re.findall(r"x{2,}", "x xx xxxx"))
print(re.findall(r"[ab]{2}", "ab ba a"))'''),
    ("re", "re-compile-methods", r'''import re
rx = re.compile(r"(\d+)")
print(rx.findall("a1b22"), rx.split("a1b22"), rx.sub("#", "a1b22"), rx.pattern)
print(rx.search("a1b22").group(1), rx.match("1ab") is not None, rx.match("a1") is None)
print(re.fullmatch(r"\d+", "123") is not None, re.fullmatch(r"\d+", "123a") is None)'''),

    # ---- collections ---------------------------------------------------------
    ("collections", "counter-basic", r'''from collections import Counter
c = Counter("mississippi")
print(c.most_common(2), c["s"], c["zzz"], sum(c.values()))
print(c.most_common())
print(sorted(c.items()))'''),
    ("collections", "counter-ties", r'''from collections import Counter
c = Counter()
for w in "d c b a d c b d c d".split():
    c[w] = c.get(w, 0) + 1
print(c.most_common())
print(c.most_common(2))'''),
    ("collections", "counter-from-iterable", r'''from collections import Counter
c = Counter(w for line in ["a b a\n", "c a b\n"] for w in line.split())
print(c.most_common(3), len(c), "a" in c)'''),
    ("collections", "counter-update", r'''from collections import Counter
c = Counter("abc")
c.update("bcd")
print(sorted(c.items()), c.total())'''),  # Counter.total() is 3.10+
    ("collections", "defaultdict-int", r'''from collections import defaultdict
d = defaultdict(int)
for w in "a b a c a".split():
    d[w] += 1
print(dict(sorted(d.items())), len(d), d["nope"], sorted(d.items()))'''),
    ("collections", "defaultdict-list", r'''from collections import defaultdict
d = defaultdict(list)
d["a"].append(1)
d["a"].append(2)
print(sorted(d.items()))'''),
    ("collections", "defaultdict-none", r'''from collections import defaultdict
d = defaultdict(None)
try:
    d["x"]
except KeyError as e:
    print("KeyError", e)'''),

    # ---- json ----------------------------------------------------------------
    ("json", "json-dumps-basic", r'''import json
print(json.dumps({"a": 1}))
print(json.dumps({"b": 2, "a": [1, {"z": None}]}, sort_keys=True, separators=(",", ":")))
print(json.dumps([1, "a", True, None, 1.5]))
print(json.dumps({}), json.dumps([]), json.dumps("x"), json.dumps(3))'''),
    ("json", "json-dumps-unicode", r'''import json
print(json.dumps({"s": "åäö"}, ensure_ascii=False))
print(json.dumps({"s": "åäö"}))
print(json.dumps("tab\there\nnl\"q\\b"))
print(json.dumps("\u0001\u007f\U0001f600"))'''),
    ("json", "json-dumps-indent", r'''import json
print(json.dumps([1, "a", True, None], indent=2))
print(json.dumps({"b": [1, 2], "a": {"c": 3}}, indent=2, sort_keys=True))
print(json.dumps({"a": [], "b": {}}, indent=2, sort_keys=True))
print(json.dumps({"a": 1}, indent="\t"))'''),
    ("json", "json-loads", r'''import json
d = json.loads('{"items":[{"id":1,"tags":["x"]},{"id":2,"tags":[]}]}')
print([i["id"] for i in d["items"] if i["tags"]])
print(json.loads("[1, 2.5, true, null]"))'''),
    ("json", "json-bad-input", r'''import json
try:
    json.loads("{not json")
except ValueError:
    print("invalid ValueError")
try:
    json.loads("{not json")
except json.JSONDecodeError:
    print("invalid JSONDecodeError")'''),
    ("json", "json-roundtrip-file", r'''import json
with open("d.json", "w") as f:
    json.dump({"k": [1, 2, 3]}, f)
with open("d.json") as f:
    print(json.load(f)["k"][1])
print(open("d.json").read())'''),

    # ---- hashlib -------------------------------------------------------------
    ("hashlib", "hashlib-hexdigest", r'''import hashlib
print(hashlib.sha256(b"hello").hexdigest())
print(hashlib.sha1(b"abcd").hexdigest())
print(hashlib.md5(b"abc" * 10).hexdigest())'''),
    ("hashlib", "hashlib-update", r'''import hashlib
h = hashlib.sha1()
for chunk in [b"ab", b"cd"]:
    h.update(chunk)
print(h.hexdigest(), h.hexdigest())
print(hashlib.sha256(b"").hexdigest())
print(hashlib.new("sha256", b"hello").hexdigest())'''),

    # ---- glob ----------------------------------------------------------------
    ("glob", "glob-pattern", r'''import glob, os
os.makedirs("g", exist_ok=True)
for n in ["a.py", "b.py", "c.txt", ".hidden.py"]:
    open(os.path.join("g", n), "w").write("")
print(sorted(glob.glob("g/*.py")))
print(sorted(glob.glob("g/?.py")))
print(sorted(glob.glob("g/[ab].py")))
print(sorted(glob.glob("g/*")))
print(glob.glob("g/a.py"), glob.glob("g/nope.py"), glob.glob("nodir/*.py"))'''),

    # ---- textwrap ------------------------------------------------------------
    ("textwrap", "textwrap-fill-dedent", r'''import textwrap
print(textwrap.fill("one two three four five six", width=12))
print(textwrap.dedent("    a\n    b\n"), end="")
print(repr(textwrap.dedent("\n    heredoc program\n")))
print(repr(textwrap.dedent("  a\n    b\n")))
print(repr(textwrap.dedent("a\n  b\n")))'''),
    ("textwrap", "textwrap-shorten", r'''import textwrap
print(textwrap.shorten("a very long sentence indeed here", width=20))
print(textwrap.shorten("short", width=20))
print(repr(textwrap.wrap("a bb ccc dddd", width=6)))'''),

    # ---- datetime ------------------------------------------------------------
    ("datetime", "datetime-format", r'''import datetime
d = datetime.datetime(2024, 5, 1, 14, 32, 0)
print(d.strftime("%Y-%m-%d %H:%M:%S"), d.date().isoformat(), d.year)
print(d.isoformat(), str(d), d.strftime("%d/%b/%Y %I:%M %p %a %B"))'''),
    ("datetime", "datetime-arith", r'''import datetime
d = datetime.date(2024, 1, 31) + datetime.timedelta(days=1)
print(d, (datetime.date(2024, 3, 1) - datetime.date(2024, 1, 1)).days)
print(datetime.date(2023, 3, 1) - datetime.date(2023, 1, 1))
print(datetime.date(2024, 2, 29) + datetime.timedelta(days=366))
print(datetime.date(2024, 5, 1).weekday(), datetime.date(2024, 5, 1).isoweekday())'''),
    ("datetime", "datetime-iso", r'''import datetime
print(datetime.datetime.fromisoformat("2024-05-01T14:32:00").hour)
print(datetime.datetime.fromisoformat("2024-05-01 14:32:05").isoformat())
print(datetime.date.fromisoformat("2024-05-01"))
print(datetime.date(2026, 8, 13).isoformat())'''),
    ("datetime", "datetime-ordinal", r'''import datetime
for y, m, d in [(1, 1, 1), (1970, 1, 1), (2000, 3, 1), (2024, 12, 31), (9999, 12, 31)]:
    o = datetime.date(y, m, d).toordinal()
    print(o, datetime.date.fromordinal(o))'''),
    ("datetime", "timedelta-str", r'''import datetime
print(datetime.timedelta(days=60), datetime.timedelta(seconds=90))
print(datetime.timedelta(days=1, hours=2, minutes=3, seconds=4))
print(datetime.timedelta(days=1).days, datetime.timedelta(hours=25).days)
print(datetime.timedelta(seconds=1) < datetime.timedelta(seconds=2))'''),

    # ---- csv -----------------------------------------------------------------
    ("csv", "csv-writer", r'''import sys, csv
w = csv.writer(sys.stdout)
w.writerow(["a", "b,c", 'say "hi"'])
w.writerow([1, None, "line\nbreak"])
w.writerows([["x"], ["y", "z"]])'''),
    ("csv", "csv-reader", r'''import csv
rows = list(csv.reader(["a,b,c\n", "1,2,3\n", '"q,q",2,"say ""hi"""\n', "\n", "last"]))
for r in rows:
    print(r)'''),
    ("csv", "csv-dictreader", r'''import csv
open("data.csv", "w").write("name,qty\na,2\nb,40\n")
with open("data.csv") as f:
    rows = list(csv.DictReader(f))
print(sum(int(r["qty"]) for r in rows))
print([sorted(r.items()) for r in rows])'''),

    # ---- urllib.parse --------------------------------------------------------
    ("urllib.parse", "urllib-quote", r'''from urllib.parse import quote, unquote, quote_plus, unquote_plus
print(quote("a b/c?"), quote("a b/c?", safe=""))
print(unquote("a%20b"), unquote("no-escapes"), unquote("%C3%A5%C3%A4%C3%B6"))
print(quote("åäö"), quote_plus("x y&z"), unquote_plus("x+y"))'''),
    ("urllib.parse", "urllib-encode", r'''from urllib.parse import urlencode
print(urlencode([("q", "x y"), ("n", 2)]))
print(urlencode([("a", "b/c")]))'''),
    ("urllib.parse", "urllib-parse", r'''from urllib.parse import urlparse, parse_qs, parse_qsl
u = urlparse("https://example.invalid/api/pub?slug=a&n=2")
print(u.netloc, u.path, parse_qs(u.query)["slug"], u.scheme, u.query, repr(u.fragment))
print(parse_qsl("a=1&b=2&a=3"), sorted(parse_qs("a=1&b=2&a=3").items()))
print(tuple(urlparse("/just/a/path")))
print(urlparse("https://h:8080/p#f").port, urlparse("https://h:8080/p#f").hostname)'''),

    # ---- statistics ----------------------------------------------------------
    ("statistics", "statistics", r'''import statistics
xs = [1, 2, 3, 4, 10]
print(statistics.mean(xs), statistics.median(xs))
print(statistics.mean([1, 2]), statistics.median([1, 2, 3, 4]))
print(statistics.mean([1.5, 2.5]), statistics.median_low(xs), statistics.median_high(xs))'''),

    # ---- shutil / tempfile / pathlib -----------------------------------------
    ("shutil", "shutil-copy", r'''import shutil, os
open("src.txt", "w").write("hi")
shutil.copy("src.txt", "dst.txt")
print(open("dst.txt").read(), os.path.exists("dst.txt"))
os.makedirs("sub", exist_ok=True)
shutil.copy("src.txt", "sub")
print(open("sub/src.txt").read())'''),
    ("tempfile", "tempfile", r'''import tempfile, os
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
    f.write("scratch")
    path = f.name
print(os.path.exists(path), open(path).read(), path.endswith(".txt"))
os.remove(path)'''),
    ("pathlib", "pathlib", r'''from pathlib import Path
Path("p.txt").write_text("hello\n")
print(Path("p.txt").read_text().strip(), Path("p.txt").suffix, Path("p.txt").exists())
p = Path("a") / "b" / "c.tar.gz"
print(str(p), p.name, p.stem, p.suffix, str(p.parent), p.parts)
print(str(Path("x.txt").with_suffix(".md")), Path("nope").exists(), Path("p.txt").is_file())'''),

    # ---- unicodedata ---------------------------------------------------------
    # Not upstream's; added because the shim carries ONE bounded repertoire and a
    # bounded claim is the kind a differential can settle completely. The sweep
    # is the whole of it — U+00C0..U+017F, every codepoint, both directions —
    # so "161 decompose" is measured here rather than quoted from the README.
    ("unicodedata", "unicodedata-sweep", r'''import unicodedata as u
n = 0
for cp in range(0x00C0, 0x0180):
    s = chr(cp)
    d = u.normalize("NFD", s)
    if d != s:
        n += 1
        print(hex(cp), repr(d), u.normalize("NFC", d) == s)
print("decomposed", n)'''),
    ("unicodedata", "unicodedata-names", r'''import unicodedata as u
for s in ["Dalén", "Åkesson", "Öberg", "smörgås", "plain"]:
    d = u.normalize("NFD", s)
    print(repr(s), repr(d), u.normalize("NFC", d) == s, len(s), len(d))'''),

    # ---- contextlib ----------------------------------------------------------
    ("contextlib", "contextlib", r'''import contextlib
@contextlib.contextmanager
def tag(name):
    print("<" + name + ">")
    yield name
    print("</" + name + ">")
with tag("a") as t:
    print("body", t)
with contextlib.suppress(ValueError):
    raise ValueError("swallowed")
print("after")'''),
)

# --- deliberate divergences ---------------------------------------------------
#
# Cases where the shim CANNOT match CPython and the difference is a documented
# limit, not a bug. They are asserted as differences so that the day a shim
# starts agreeing — or diverges in a NEW way — the suite says so, instead of
# ``micropython/lib/README.md`` quietly going stale. ``note`` is the reason and
# belongs in that README too; ``same`` is what the pair must do.

DIVERGENCES: Tuple[Tuple[str, str, str, bool, str], ...] = (
    (
        "re",
        "re-ascii-classes",
        r'''import re
print(re.findall(r"\w+", "åäö abc"))''',
        False,
        "re1.5's \\w/\\d/\\s are ASCII bytes; CPython's are unicode (README §1)",
    ),
    (
        "collections",
        "counter-repr",
        r'''from collections import Counter
print(repr(Counter("aab")))''',
        True,
        "Counter.__repr__ is reimplemented and must still agree; only the native "
        "subclass's dict repr spacing differs under MicroPython (README §22)",
    ),
    (
        "unicodedata",
        "unicodedata-outside-repertoire",
        r'''import unicodedata as u
print(repr(u.normalize("NFD", "ẛ")))''',
        False,
        "CPython carries the whole UCD; the shim carries 161 codepoints and "
        "REFUSES the rest by exit-90 shape rather than returning the string "
        "unnormalised — silence here would be a comparison that quietly fails",
    ),
)

#: Shim modules with no case here, and why. A new shim landing without cases is
#: a promise this file cannot keep, so the set is pinned rather than derived —
#: see :func:`test_every_shim_module_is_covered_or_named`.
UNCOVERED: Dict[str, str] = {
    # `deflate` is a C module with no pure-Python stand-in, and compress()
    # additionally needs MICROPY_PY_DEFLATE_COMPRESS in the variant. Covered by
    # conformance against the built binary instead.
    "zlib": "needs the `deflate` C module; checked by `lypning conformance`",
    # Diverges from CPython BY DESIGN — it exits 2 with usage text it writes
    # itself — so a byte-identical differential would be pinning CPython's
    # message wording, which is not a promise this shim makes.
    "argparse": "exit-2 message text is CPython's, not this file's, to reproduce",
}


# --- the two runs -------------------------------------------------------------


def _all_cases() -> Tuple[Tuple[str, str, str], ...]:
    return CASES + tuple((m, cid, code) for m, cid, code, _, _ in DIVERGENCES)


class Differential:
    """Both runs, or the reason there is only one.

    Holds ``error`` rather than raising in the fixture on purpose: a driver that
    will not start is one finding, and reporting it once — with the interpreter's
    own stderr attached — beats erroring every parametrised case with a stack
    that names pytest's fixture machinery instead of the driver.
    """

    def __init__(
        self,
        cpython: Optional[Dict[str, List[str]]],
        shim: Optional[Dict[str, List[str]]],
        error: Optional[str],
        oracle_version: "Optional[tuple]" = None,
    ) -> None:
        self.cpython = cpython or {}
        self.shim = shim or {}
        self.error = error
        self.oracle_version = oracle_version or (0, 0)

    def require(self) -> None:
        if self.error:
            pytest.fail(self.error)

    def agree(self, case_id: str) -> bool:
        return self.cpython.get(case_id) == self.shim.get(case_id)

    def report(self, module: str, case_id: str) -> str:
        """One failure, with both outputs. Rendered, never printed."""
        cpy = self.cpython.get(case_id, ["<missing>", ""])
        shm = self.shim.get(case_id, ["<missing>", ""])
        lines = ["%s / %s" % (module, case_id)]
        for label, (status, out) in (("cpython", cpy), ("shim", shm)):
            lines.append("  %-7s outcome: %s" % (label, status))
            lines.append("  %-7s stdout:  %s" % (label, _oneline(out)))
        return "\n".join(lines)


def _oneline(text: str) -> str:
    """``repr`` of the whole output. A diff of two escaped one-liners is readable;
    a diff of two multi-line blocks in a pytest assertion message is not."""
    return repr(text) if len(text) <= 2000 else repr(text[:2000]) + " …(truncated)"


def _run(
    python: Path,
    mode: str,
    cases: Sequence[Tuple[str, str, str]],
    scratch: Path,
    lib: Path,
) -> Dict[str, List[str]]:
    driver = scratch / "driver.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    payload = json.dumps([
        {"id": cid, "code": code, "cwd": str(scratch / mode)} for _, cid, code in cases
    ])
    # ``-B``: the shim tree is an ASSET, read-only in a wheel and reviewed in a
    # diff in a checkout. Importing it would otherwise drop a __pycache__ into
    # it — bytes the user did not have before a test ran (CLAUDE.md §7), and one
    # more thing for the wheel shape to fail silently on.
    proc = subprocess.run(
        [str(python), "-B", str(driver), mode, str(lib)],
        input=payload, capture_output=True, text=True, timeout=300, check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(
            "the %s driver failed: exit %d\n%s" % (mode, proc.returncode, proc.stderr)
        )
    return json.loads(proc.stdout)


def _oracle_and_lib() -> Tuple[Path, Path]:
    """The interpreter that plays oracle and the shim tree it is measured against.

    ``find_cpython`` rather than ``sys.executable``: capture puts a shim named
    ``python3`` first on ``$PATH`` and a differential measured through a shell
    script is measuring the script. Both halves degrade to a skip — a tree with
    no ``assets/micropython/lib`` is a wheel built without it, not a failure.
    """
    lib = paths.MICROPYTHON_LIB
    if not lib.is_dir():
        pytest.skip("the shim stdlib is not present at %s" % lib)
    python = engines.find_cpython()
    if python is None:  # pragma: no cover - a machine with no python3 cannot run pytest
        pytest.skip("no real CPython found to be the oracle")
    return python, lib


def _oracle_version(python: "Path") -> tuple:
    """``(major, minor)`` of the interpreter acting as the oracle.

    Asked of the binary rather than read from ``sys.version_info``: the oracle
    is whichever CPython ``engines.find_cpython()`` resolved, which need not be
    the one running pytest.
    """
    out = subprocess.run(
        [str(python), "-c", "import sys;print('%d %d' % sys.version_info[:2])"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    try:
        return tuple(int(n) for n in out.stdout.split())
    except ValueError:  # pragma: no cover
        return (0, 0)


@pytest.fixture(scope="session")
def differential(tmp_path_factory) -> Differential:
    """Both runs, once per session.

    Session-scoped because the two spawns are the whole cost of this file and
    they answer the same question for every test in it. It takes
    ``tmp_path_factory`` rather than ``tmp_path`` for that reason, and does its
    own isolation: each case gets ``<scratch>/<mode>/<id>``, a separate tree per
    mode, so the shim run cannot read back what the CPython run wrote.
    """
    python, lib = _oracle_and_lib()
    scratch = tmp_path_factory.mktemp("shims")
    cases = _all_cases()
    try:
        cpython = _run(python, "cpython", cases, scratch, lib)
        shim = _run(python, "shim", cases, scratch, lib)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        return Differential(None, None, str(exc))
    return Differential(cpython, shim, None, _oracle_version(python))


# --- the tests ----------------------------------------------------------------

MODULES: Tuple[str, ...] = tuple(dict.fromkeys(m for m, _, _ in CASES))


def test_both_differential_drivers_run(differential: Differential) -> None:
    """The suite's own precondition: two runs, every case answered by both.

    A driver that dies halfway leaves a short dict, and a short dict would make
    every missing case look like agreement if the comparison were keyed on what
    is present. So the count is checked before anything is compared.
    """
    differential.require()
    expected = len(_all_cases())
    assert len(differential.cpython) == expected
    assert len(differential.shim) == expected


def test_the_shim_run_really_imports_the_shim_tree(tmp_path) -> None:
    """The one failure mode that makes every other test in this file a lie.

    A differential where the "shim" run quietly resolved CPython's module passes
    everything, forever, and says nothing. That is not hypothetical: CPython 3.11
    deep-freezes ``os`` and ``posixpath`` and carries ``os.path`` as a frozen
    ALIAS, and ``sys.meta_path`` runs FrozenImporter ahead of PathFinder — so the
    obvious ``sys.path.insert(0, LIB)`` leaves nine os/os.path cases comparing
    CPython with itself, with ``os.__file__`` still pointing at /usr/lib to make
    it look right. So provenance is measured, once, from inside the shim run.
    """
    python, lib = _oracle_and_lib()
    probe = (
        "import %s\n"
        "import sys\n"
        "for name in sorted(n for n in sys.modules if n in (%s)):\n"
        "    print(name, getattr(sys.modules[name], '__file__', None))\n"
        % (", ".join(MODULES), ", ".join(repr(m) for m in MODULES))
    )
    out = _run(python, "shim", (("probe", "provenance", probe),), tmp_path, lib)
    status, text = out["provenance"]
    assert status == "ok", text
    lines = [ln.split(" ", 1) for ln in text.splitlines()]
    assert {n for n, _ in lines} == set(MODULES), text
    outside = [ln for ln in lines if not ln[1].startswith(str(lib))]
    assert not outside, (
        "these modules came from CPython, not the shim tree — the differential "
        "is comparing CPython with itself for them: %s" % outside
    )


# Cases whose ORACLE has to be new enough to express them.
#
# The shims are written against a current CPython, so a shim can be AHEAD of an
# old one: `Counter.total()` arrived in 3.10, and under a 3.9 oracle the
# differential reports the shim answering where CPython raises AttributeError.
# That is the oracle being old, not the shim being wrong. Failing on it would
# leave the 3.9 matrix job green only if the case were deleted — so the case
# stays, and is compared on every interpreter that can express it.
#
# The oracle is NOT necessarily the interpreter running pytest: conftest strips
# $LYPNING_CPYTHON, so it is resolved off $PATH.
_CASE_MIN_PYTHON = {
    "counter-update": (3, 10),
    # `validate=True` reached `binascii.a2b_base64(strict_mode=True)` in CPython
    # 3.11. Before that `base64` did its own regex check and raised
    # `Non-base64 digit found` for everything this case distinguishes — so on a
    # 3.9 or 3.10 oracle the shim REJECTS the same inputs and words it
    # differently, and grading the text against that oracle would be grading it
    # against a CPython this message was never copied from.
    "base64-validate-rejects": (3, 11),
}


@pytest.mark.parametrize("module", MODULES)
def test_shim_matches_cpython(module: str, differential: Differential) -> None:
    """Every case for one shim module, byte for byte against the live oracle."""
    differential.require()
    oracle = differential.oracle_version
    graded = [
        (mod, cid) for mod, cid, _ in CASES
        if mod == module and _CASE_MIN_PYTHON.get(cid, (0, 0)) <= oracle
    ]
    failures = [
        differential.report(mod, cid) for mod, cid in graded
        if not differential.agree(cid)
    ]
    assert not failures, "%s: %d of %d cases diverge from CPython\n\n%s" % (
        module,
        len(failures),
        len(graded),
        "\n\n".join(failures),
    )


@pytest.mark.parametrize(
    "module, case_id, same, note",
    [(m, cid, same, note) for m, cid, _, same, note in DIVERGENCES],
    ids=[cid for _, cid, _, _, _ in DIVERGENCES],
)
def test_documented_divergence(
    module: str, case_id: str, same: bool, note: str, differential: Differential
) -> None:
    """A written-down limit is a limit; an unwritten one is a bug nobody notices.

    Both directions fail here. The shim agreeing where the README says it cannot
    is as much a stale document as the shim breaking where the README says it
    works, and the fix for either is a line in the README, not a line here.
    """
    differential.require()
    agreed = differential.agree(case_id)
    if same:
        assert agreed, "%s: expected agreement — %s\n\n%s" % (
            case_id, note, differential.report(module, case_id),
        )
    else:
        assert not agreed, (
            "%s: expected a divergence and the two AGREED — the limit closed, so "
            "update micropython/lib/README.md and move this case into CASES.\n%s"
            % (case_id, note)
        )


def test_every_shim_module_is_covered_or_named() -> None:
    """The README promises this file checks the shims. Keep the promise total.

    Derived from the directory, not from a list, because the freeze in
    ``variant/manifest.py`` is a glob: a module added there ships in the binary
    whether or not anyone remembered it here. Landing one with no cases and no
    entry in :data:`UNCOVERED` fails, which costs one line to fix and is the only
    moment anybody will think about it.
    """
    lib = paths.MICROPYTHON_LIB
    if not lib.is_dir():
        pytest.skip("the shim stdlib is not present at %s" % lib)
    shipped = {p.stem for p in lib.glob("*.py")}
    shipped |= {p.name for p in lib.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    # A case names the module it exercises (`os.path`, `urllib.parse`); the
    # package is what ships.
    covered = {m.split(".")[0] for m, _, _ in CASES}
    missing = sorted(shipped - covered - set(UNCOVERED))
    assert not missing, (
        "shim modules with neither a case nor a reason in UNCOVERED: %s" % ", ".join(missing)
    )
    stale = sorted(set(UNCOVERED) & covered)
    assert not stale, "UNCOVERED names modules that ARE covered: %s" % ", ".join(stale)


#: Asked of the stand-ins themselves, inside the shim run. See
#: :func:`test_the_stand_ins_are_restricted_at_run_time`.
_RESTRICTIONS = r'''import ure, uos, ujson, uhashlib
def surface(o):
    return sorted(n for n in dir(o) if not n.startswith("_"))
print("ure", surface(ure))
print("pattern", surface(ure.compile("a")))
print("match", surface(ure.search("(a)", "a")))
print("hexdigest", hasattr(uhashlib.sha256(), "hexdigest"))
print("dumps", hasattr(ujson, "dumps"))
print("stat-type", type(uos.stat(".")).__name__)
print("stat-attrs", hasattr(uos.stat("."), "st_size"))
print("getenv-missing", repr(uos.getenv("LYPNING_NOPE")))
for label, pat in (("named", "(?P<a>x)"), ("lookahead", "(?=x)"),
                   ("braces", "a{2,3}"), ("word-boundary", r"\bx")):
    try:
        ure.compile(pat)
        print(label, "COMPILED")
    except Exception as e:
        print(label, type(e).__name__)
'''


def test_the_stand_ins_are_restricted_at_run_time(tmp_path) -> None:
    """Ask the stand-ins, rather than reading the source that builds them.

    The sibling test below greps :data:`_DRIVER`, and grepping cannot see a line
    ADDED next to the ones it matches: appending ``ure.findall = _re.compile``
    leaves every text assertion in this file true, and the whole suite green,
    while every ``re`` case underneath is quietly answered by CPython's engine.
    So the surface is measured from inside the shim run — the same place the
    shims see it — and that is what makes this file a differential rather than
    two runs of CPython.
    """
    python, lib = _oracle_and_lib()
    out = _run(python, "shim", (("probe", "restrictions", _RESTRICTIONS),), tmp_path, lib)
    status, text = out["restrictions"]
    assert status == "ok", text
    got = dict(ln.split(" ", 1) for ln in text.splitlines())

    # MicroPython's `re` is re1.5 and has exactly these four names. `findall`,
    # `split`, `finditer`, `escape` and `fullmatch` are CPython's, and a shim
    # that reached one would be testing CPython.
    assert got["ure"] == repr(["compile", "match", "search", "sub"]), text
    assert got["pattern"] == repr(["match", "search", "sub"]), text
    assert got["match"] == repr(["end", "group", "groups", "span", "start"]), text
    # What re1.5 cannot compile must raise here rather than be answered by
    # CPython's engine: the shim's own fallbacks are what is under test.
    for label in ("named", "lookahead", "braces", "word-boundary"):
        assert got[label] != "COMPILED", "%s: re1.5 cannot compile this\n%s" % (label, text)
    # `uhashlib` without hexdigest is what forces hashlib.py to hexlify itself;
    # `uos.stat` as a bare tuple is what forces the shim's own `_StatResult`.
    assert got["hexdigest"] == "False", text
    assert got["dumps"] == "False", text
    assert got["stat-type"] == "tuple" and got["stat-attrs"] == "False", text
    assert got["getenv-missing"] == "None", text


def test_the_shim_run_does_not_get_cpythons_modules() -> None:
    """The stand-ins are the point of the file, so assert they are restrictive.

    Read off the driver source rather than executed — the run-time half is the
    test above, and both are wanted: this one names the *reason* each stand-in
    is shaped the way it is, and fails on a rewrite that removes the mechanism
    even where the surface it produced happens to survive. ``uos.stat``
    returning a bare tuple is what forces ``os.stat().st_size`` to come from the
    shim's own ``_StatResult``; ``uhashlib`` without ``hexdigest`` is what
    forces ``hashlib.py`` to hexlify for itself.
    """
    for name in ("ure", "uos", "ujson", "uhashlib", "ucollections"):
        assert '%s = type(sys)("%s")' % (name, name) in _DRIVER
    assert "uos.stat = lambda p: tuple(_os.stat(p))" in _DRIVER
    assert "def digest(self):" in _DRIVER and "def hexdigest" not in _DRIVER
    assert "ujson.dumps" not in _DRIVER  # MicroPython's dumps has no indent/sort_keys
    # The engine models re1.5, so anything re1.5 cannot compile must be refused
    # by the stub rather than silently answered by CPython's engine.
    for rejected in ("(?P<", "(?=", "(?!", "(?<", '("b", "B", "A", "Z")', "{n,m}"):
        assert rejected in _DRIVER
    # And the shim tree has to be ahead of CPython's on the path, or every case
    # above would be comparing CPython with itself and passing.
    assert "sys.path.insert(0, LIB)" in _DRIVER


def test_cases_are_uniquely_named() -> None:
    """Case ids key the result dict in both runs; a duplicate silently drops one."""
    ids = [cid for _, cid, _ in CASES] + [cid for _, cid, _, _, _ in DIVERGENCES]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, "duplicate case ids: %s" % ", ".join(dupes)
    assert len(ids) == len(_all_cases())


def test_no_case_reaches_outside_its_scratch_directory() -> None:
    """Same net as the corpus battery, for the same reason (CLAUDE.md §4).

    These programs write files, and they run under the checkout's own
    interpreter with no sandbox around them. The driver chdirs each case into
    ``<scratch>/<mode>/<id>`` first, which only helps while every path a case
    opens is relative — so the tokens that would escape are named here rather
    than discovered from ``git status`` afterwards. Absolute-looking *strings*
    are fine and common (``p.normpath("/../a")``): they never reach the
    filesystem, so the tokens below are the opening calls, not the strings.
    """
    escapes = ('open("/', "open('/", 'Path("/', "Path('/", 'makedirs("/',
               "os.chdir", "expanduser")
    offenders = [
        "%s: %s" % (cid, token)
        for _, cid, code in _all_cases()
        for token in escapes
        if token in code
    ]
    assert not offenders, "cases that could write outside their scratch dir: %s" % offenders
