# micropython/lib — the frozen shim stdlib

Pure-Python modules compiled into the lypning-mp binary by `../variant/manifest.py`,
which freezes this directory by glob. Nothing here is imported from disk at
runtime: `sys.path` is pinned to `['.frozen']`, so these modules cost zero file
opens, which is the whole reason lypning-mp exists (`docs/RESEARCH.md` §2.4).

They close the gap between what MicroPython ships and what
`docs/SUBSET.md` §3.3 says Tier 0 needs, plus the Tier 1 modules the
harvested corpus turned out to want. Each one is verified against **real CPython
3.11** by `tests/test_shims.py`, which runs every case twice — once
against CPython's stdlib, once against these files over restricted stand-ins for
the MicroPython C modules — and requires byte-identical output. CPython is the
oracle on every run; nothing below is asserted from memory.

## How a shim reaches the C module it is built on

This is the one piece of the mechanism that is not obvious, and getting it wrong
produces a module that imports but does nothing.

MicroPython registers `re`, `os`, `json`, `collections`, `hashlib` and friends
as **extensible** built-ins (`MP_REGISTER_EXTENSIBLE_MODULE`). Extensible means
the filesystem — here, the frozen table — is searched **first**, and the C module
is only reached if nothing is found. So freezing `re.py` does not *extend* the C
`re`; it **replaces** it, and the C engine becomes unreachable under that name.

The way back in is the `u`-prefixed aliases the build still registers:

| shim | imports | why |
|---|---|---|
| `re.py` | `ure` | the re1.5 engine (`compile`/`match`/`search`) |
| `os/__init__.py`, `os/path.py` | `uos` | `listdir`/`stat`/`mkdir`/`getenv`/… |
| `json.py` | `ujson` | the C parser (`loads`/`load`) |
| `hashlib.py` | `uhashlib` | `sha256`/`sha1`/`md5` |
| `collections.py` | `ucollections` | `deque`/`namedtuple`/`OrderedDict` (optional, in a `try`) |
| `zlib.py` | `deflate` | not shadowed, imported by its own name |
| `base64.py`, `csv.py`, `glob.py`, … | `binascii`, `io`, `time`, `os`, `re` | not shadowed, or shadowed by a shim in this directory |

**If a future variant drops the `u`-prefixed aliases, every shim in the left
column breaks at import.** The fix is one line per module in the variant
(`MP_REGISTER_MODULE(MP_QSTR__re, mp_module_re);`) plus the import name here —
not a rewrite.

`os` is a **package** (`os/__init__.py` + `os/path.py`) because `import os.path`
needs the parent to be one. A frozen `os/` directory with no `__init__.py` would
resolve `import os` to an empty namespace package and silently lose `listdir`.

## What each module covers

| module | provides | deliberately absent |
|---|---|---|
| `base64` | `b64encode`/`b64decode` (str or bytes input), `urlsafe_*`, `standard_*`, `altchars` | `validate=`, `b32`/`b16`/`a85`, `encodebytes` |
| `os` | everything `uos` has, plus `path`, `environ`, `makedirs`, `walk`, `stat()` with named fields, `getenv(default)`, `linesep` | `fstat`, `system` (§5), `scandir`, `chmod`, `symlink`, `fork` |
| `os.path` | `join`, `split`, `dirname`, `basename`, `splitext`, `normpath`, `abspath`, `isabs`, `exists`, `isfile`, `isdir`, `getsize`, `getmtime`, `expanduser` | `realpath`, `relpath`, `commonpath`, `samefile`, `splitdrive` |
| `re` | `search`, `match`, `fullmatch`, `findall`, `finditer`, `split`, `sub`, `subn`, `escape`, `compile`, `purge`, match objects with named groups + `span`/`groupdict`, flags `I`/`M`/`S` | lookaround, backreferences, `\b`, `re.X`, `re.DEBUG`, `pos`/`endpos` |
| `collections` | `Counter` (`most_common`, `update`, `subtract`, `elements`, `total`), `defaultdict`; re-exports `deque`/`namedtuple`/`OrderedDict` | Counter arithmetic (`+ - & \|`), `ChainMap`, `UserDict` |
| `json` | `loads`, `load`, `dumps`, `dump` with `indent`, `sort_keys`, `separators`, `ensure_ascii`, `default`; `JSONDecodeError` ⊂ `ValueError` | `cls=`, `object_hook`, `parse_float`, `JSONEncoder`/`JSONDecoder` classes |
| `hashlib` | `sha256`, `sha1`, `md5`, `sha224/384/512`, `new`, and the `hexdigest()` MicroPython lacks | `blake2*`, `shake`, `pbkdf2_hmac`, `copy()`, `digest_size` |
| `glob` | `glob`, `iglob`, `escape`, `fnmatch` (`*`, `?`, `[seq]`, `[!seq]`) | `**`/`recursive=`, magic in a directory component, `root_dir=` |
| `textwrap` | `wrap`, `fill`, `dedent`, `shorten`, `indent` | `TextWrapper`, `break_long_words`, `break_on_hyphens`, `initial_indent`, tab expansion |
| `datetime` | `date`, `datetime`, `timedelta`, `strftime`, `isoformat`, `fromisoformat`, `fromordinal`/`toordinal`, `now`, `today`, comparisons | `time` class, `tzinfo`/aware datetimes, `timezone`, `strptime`, ISO week functions |
| `csv` | `reader`, `writer`, `DictReader`, `DictWriter`, excel dialect (`\r\n`, doubled quotes, minimal quoting) | `Sniffer`, `register_dialect`, `quoting=`, `escapechar`, `QUOTE_NONNUMERIC` behaviour |
| `urllib.parse` | `quote`, `quote_plus`, `unquote`, `unquote_plus`, `urlencode`, `urlparse`/`urlsplit`, `urlunparse`, `urljoin`, `parse_qs`, `parse_qsl` | `urllib.request` (the VM is offline), `DefragResult`, `urldefrag` |
| `statistics` | `mean`, `fmean`, `median`, `median_low`, `median_high`, `mode`, `variance`/`stdev` family | `quantiles`, `correlation`, `NormalDist`, exact-Fraction summation |
| `shutil` | `copy`, `copy2`, `copyfile`, `copyfileobj`, `move`, `rmtree`, `which` | archives, `copytree`, metadata/permission copying |
| `tempfile` | `NamedTemporaryFile`, `TemporaryFile`, `TemporaryDirectory`, `mkstemp`, `mkdtemp`, `mktemp`, `gettempdir` | `SpooledTemporaryFile`, `O_EXCL` atomicity |
| `pathlib` | `Path` — `/`, `read_text`/`write_text`/`read_bytes`/`write_bytes`, `name`/`stem`/`suffix`/`parent`/`parts`, `exists`/`is_file`/`is_dir`, `mkdir`, `iterdir`, `glob`, `rename`, `unlink` | `PurePath` separation, Windows flavours, `match`, `rglob`, `resolve()` (aliased to `absolute()`) |
| `contextlib` | `contextmanager`, `suppress`, `closing`, `nullcontext` | `ExitStack`, `redirect_stdout`, async variants |
| `zlib` | `crc32`, `compress`, `decompress` over `deflate` | `compressobj`/`decompressobj`, `gzip` module, `adler32` |

Explicitly **not** shimmed, because `docs/SUBSET.md` §5 puts them out of
scope: `subprocess`, `argparse`, `threading`, `http.server`, `socket`, and
anything that installs packages. They must exit 90, not be faked.

## Divergences from CPython that remain

Every entry was produced by running both, not by reading docs. The ones that can
bite silently — a program that finishes, prints something plausible, and exits 0
— are marked **SILENT**.

### `re` — the engine's own limits

1. **SILENT: `\d`, `\w`, `\s` are ASCII.** re1.5 matches bytes, so
   `re.findall(r"\w+", "åäö abc")` gives `['abc']` where CPython gives
   `['åäö', 'abc']`. This cannot be fixed here: a raw high byte cannot be put
   into a `str` pattern (MicroPython encodes it as UTF-8 before the engine sees
   it). It matters more in this repo than most, because CLAUDE.md invariant 6
   requires Swedish and English to be handled with the same breadth — a `\w+`
   tokeniser will quietly drop every Swedish word containing å/ä/ö. Closing it
   means a change in `lib/re1.5/charclass.c`, not in this directory.
2. `\b`, `\B`, `\A`, `\Z`, `\G` and pattern backreferences (`\1`) raise
   `NotImplementedError("lypning-mp: unsupported: argument: re(...)")`. They are
   rejected rather than passed through because re1.5 compiles `\b` as the
   *literal letter b* — the silent-wrongness case this whole contract exists to
   prevent.
3. Lookaround (`(?=`, `(?!`, `(?<`) and inline flags (`(?i)`) are rejected the
   same way. `(?:` is supported natively.
4. `{n,m}` is expanded into repetition (`\d{3}` → `\d\d\d`, `a{2,4}` →
   `aaa?a?`), because re1.5 treats `{` as a literal. Expansion only works on a
   single non-group atom; `(ab){2}` raises unsupported rather than mis-matching.
   `a{1,2}?` (lazy) is not handled.
5. `.` is rewritten to `[^\n]` unless `re.S` is set, because re1.5's `Any`
   matches `\n`. With `re.S` the rewrite is skipped and correctness depends on
   the engine, which is untested — no corpus entry uses `re.S`.
6. `re.M` is emulated by splitting the subject on `\n` and matching each line,
   so **a pattern cannot match across a newline under `re.M`**. `^` and `$`
   behave exactly like CPython's; verified.
7. `$` in a non-`re.M` pattern: CPython also matches just before a single
   trailing newline, re1.5's `Eol` does not. The shim strips that newline when
   the pattern *ends* with `$`. A `$` elsewhere in the pattern (`a$|b`) is not
   handled.
8. `re.I` is pattern rewriting (`a` → `[aA]`, `[a-z]` → `[A-Za-z]`). Non-ASCII
   case folding does not happen, for the same reason as (1).
9. A `^`-anchored pattern is matched at most once per segment, because
   MicroPython's `search` takes no `pos` argument and the shim has to slice the
   subject — which would otherwise re-anchor `^` at every slice. An anchor
   inside a group (`(^a)`) is not detected and *will* over-match.
10. Scanning slices the subject once per match, so `findall`/`finditer`/`sub`
    are O(n·matches) rather than O(n). Fine for one-liners, wrong for megabytes.
11. `re.X`, `re.A`, `re.L`, `re.U`, `re.DEBUG` exist as constants and do
    nothing. `re.error` is defined but a malformed pattern usually surfaces as
    MicroPython's own error from `ure.compile`.
12. `dir(re)` does not match CPython's: MicroPython does not sort `dir()`.

### `os`

13. `os.environ` **cannot be enumerated.** MicroPython has `getenv` but no
    environment listing, so `.get`, `env[k]`, `k in env` and `env[k] = v` work
    and `list(os.environ)`, `.keys()`, `.items()`, `dict(os.environ)` raise.
14. `os.stat()` returns a shim object with the ten named fields plus indexing
    and iteration. It is not `os.stat_result`; `type()` and `repr()` differ.
15. `os.makedirs` on an existing directory raises `OSError(EEXIST)` where
    CPython raises `FileExistsError`. Same for `open()` on a missing file
    (`OSError(ENOENT)` vs `FileNotFoundError`) — that one is the runtime's, not
    this directory's. `except OSError` catches both; `except FileExistsError`
    does not exist yet.
16. `os.walk` takes no `onerror`/`followlinks` and swallows unreadable
    directories. Order follows `listdir`, so sort before printing.

### Everything else

17. `base64.b64decode` does not validate: MicroPython's `a2b_base64` is lenient
    where CPython raises. Both reject bad padding, with different messages.
    `validate=True` is accepted and ignored.

    The exception's **type** used to differ too, and no longer does. CPython
    raises `binascii.Error`; MicroPython's `binascii` defines no `Error` at all
    and raised a bare `ValueError`. **Observed 2026-08-23** by a harvested corpus
    entry (`py-16c1663c6170`) that does the ordinary thing with a caught
    exception and prints its type name — and it surfaced as an UNSAFE *route*
    rather than a MISMATCH, because the classifier had already sent it here.

    Unlike #19 this is not implementation-defined: a message is text Python does
    not specify, but the class `base64` raises is documented. `base64.py` defines
    `Error(ValueError)` when `binascii` has none to import, with
    `__module__ = "binascii"` in the class body so the QUALIFIED name agrees too
    — `repr()`, a traceback and
    `'%s.%s' % (type(e).__module__, type(e).__name__)` all print it. Verified
    directly on MicroPython 1.22.1: the shipped shim raises `binascii.Error`
    there. Pinned by `base64-bad-padding-exception-name`,
    `base64-error-qualified-name` and `base64-error-is-a-valueerror` in
    `tests/test_shims.py`, which can only fail because that suite now models
    MicroPython's `binascii` — an absence, so it had to be modelled deliberately
    or the shim run would keep importing CPython's and getting `Error` for free.

    What is left is the message text — CPython's `Incorrect padding` against
    MicroPython's `incorrect padding` — and that IS #19's kind of difference.

17a. **Builtin types have no `__module__`.** `TypeError.__module__` is
    `'builtins'` on CPython and raises `AttributeError` on MicroPython; a class
    defined in Python has one either way. This is the runtime's, not this
    directory's (as in #15), and it is not fixable from a shim.

    It is recorded here because it is what actually breaks `py-9b16a7261b96`, a
    corpus entry that prints `type(e).__module__` for every exception it catches
    — so the first builtin one ends the program and the entry exits 1 where
    CPython exits 0. **That entry was briefly tagged `implementation-defined` on
    the theory that only the base64 message still differed. The theory was
    wrong** — the tag suppresses the stdout comparison and the exit-code
    difference remains, so it bought nothing and misdescribed the cause. The tag
    is removed; the entry is a MISMATCH on this tier for a concrete missing
    feature, which is the honest thing for it to be.

18. `json.dumps` is pure Python and therefore slower than the C encoder it
    replaces. It has to be: MicroPython's `dumps` has no `indent`, no
    `sort_keys`, and no `ensure_ascii`, and it emits raw UTF-8 where CPython
    escapes to `\uXXXX` by default — `json.dumps({"s": "åäö"})` differing is the
    exact case `json-dumps-unicode` pins. Parsing stays on C.
19. `json.JSONDecodeError` has no `.msg`/`.doc`/`.pos`/`.lineno`/`.colno`, and
    its message is MicroPython's, not CPython's. **Observed 2026-08-14** by a
    harvested corpus entry (`py-428369184d6a`) that does the ordinary thing with
    a caught exception — `except Exception as e: print('not json:', e)` — and so
    put the message on stdout, where conformance compares it: CPython prints
    `Expecting value: line 1 column 1 (char 0)`, lypning-mp prints `syntax error in
    JSON`. Closing it means reproducing CPython's line/column/char arithmetic,
    which needs a position-tracking parser in this file; parsing is deliberately
    on the C `ujson` (#18) precisely so that code does not exist here. The entry
    is tagged `implementation-defined` instead, because Python specifies no
    exception message text. That tag is hand-applied and now SURVIVES a
    re-harvest (`lypning harvest` carries `tags` through the
    merge and the serializer) — before that it would have been erased by the
    next harvest and turned CI red with no diff to explain it.
20. `hashlib`: `update()` after `digest()` does not resume — MicroPython's C
    hash finalises on `digest()`. The shim caches the digest so repeated
    `hexdigest()` is stable, which CPython also gives. `md5` and `sha1` need the
    variant built with `MICROPY_PY_HASHLIB_MD5` / `MICROPY_PY_HASHLIB_SHA1`;
    without them the call reports `attribute: hashlib.md5` and exits 90.
21. `glob` supports wildcards in the last path component only, has no `**`, and
    returns `os.listdir` order (CPython's is also arbitrary — sort before
    printing).
22. `collections`: `Counter` and `defaultdict` are `dict` subclasses with
    reimplemented `__repr__`. `most_common` sorts by count descending with ties
    in first-seen order — matching both CPython branches (`sorted(reverse=True)`
    and `heapq.nlargest`) — and does not rely on the runtime's sort being
    stable. Counter arithmetic operators are absent.
23. `datetime` is naive only. `strftime` supports `%Y %y %m %d %H %M %S %f %j
    %p %I %A %a %B %b %F %T %%` with C-locale names; anything else is emitted
    verbatim rather than raising. There is no `strptime` and no `time` class.
24. `urllib.parse.urlparse` returns a hand-rolled result object — indexable,
    iterable, with `.geturl()`, `.hostname`, `.port` — not a namedtuple, so
    `repr()` and `_replace()` differ. `urlencode` over a dict iterates the
    dict's order, which MicroPython does not guarantee to be insertion order;
    pass a list of pairs when the order matters.
25. `statistics.mean` returns an `int` when the total divides exactly, matching
    CPython for integer input, but it does not use exact `Fraction` arithmetic —
    float input can differ from CPython in the last bit.
26. `tempfile` names come from `os.urandom`, not `O_EXCL`. There is one process
    in this VM and no other writer, so the race CPython guards against cannot
    happen here; do not copy this module anywhere else.
27. `zlib.compress` additionally needs the variant built with
    `MICROPY_PY_DEFLATE_COMPRESS`; without it the call reports
    `attribute: DeflateIO.write` and exits 90. `crc32` works today.
28. `pathlib.Path.resolve()` is `absolute()` — it does not resolve symlinks.

## Rules for adding a module here

- **Run it, do not reason about it.** Add cases to
  `tests/test_shims.py`; they are compared against live CPython. The
  suite's fake `ure` deliberately rejects what re1.5 cannot compile, so a shim
  that leans on CPython's regex fails there rather than in the sandbox.
- **Every byte is frozen into a binary with a 700,000 B gate and streamed over a
  WebSocket on first use.** Write what the corpus needs, not the general case.
- **Derive the next module from `lypning conformance --plan`**, not
  from a list. It ranks by entries unblocked and the ranking moves as things
  land.
- **Any divergence you cannot close goes in the table above**, with the
  observation that produced it. A divergence that is written down is a
  documented limit; one that is not is a bug the agent will never notice.
