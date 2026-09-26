# The subset and the refusal contract

*What every Rust variant must execute exactly, and what happens when a program
leaves that set. The engines are `engines.ENGINE_ORDER`; the CPython reference
is used to grade every variant against these rows.*

It is not a Python implementation. It is the subset that the agent loop actually
invokes, and the contract for what happens when a program leaves that subset.

## 1. The evidence base

`src/lypning/assets/corpus/seed-corpus.jsonl` holds the anchor entries,
`corpus.jsonl` beside it the harvested ones. One entry per line: `id`,
`program`, `stdin`, `argv_tail`, `expect_stdout`, `provenance`, `tags`, `note`.
Every `expect_stdout` was recorded in an empty working directory with `stdin`
on the pipe, `argv_tail` after `-c` and a minimal environment (`PATH`, `HOME`,
`LC_ALL=C.UTF-8`); a blank one marks a clock, a PRNG stream or an absent module.

## 2. The one-liner shapes

Each variant accepts `<engine> -c '<src>' [args…]` (`sys.argv[0]` is `"-c"`),
`<engine> script.py [args…]` (`sys.argv[0]` is the path) and `<engine> -` with
the program on stdin, consumed as under CPython (`main.rs:run`). `--version`
prints the variant's own name; every `-m` is `cli: option -m`; there is no REPL.

## 3. What the engines implement

The surface — the syntax each engine parses, the builtins and exception names
that resolve, the modules each one serves and the four it serves only in part —
is `docs/DIFFERENCES.md`, and it is there rather than here because it is a copy
of tables in the crate and a copy needs a test: every list on that page is held
to `builtins.rs`, `modules.rs` and `route.rs` by `tests/test_differences.py`.
This table was the second copy, and it named `collections` and `pathlib` as the
whole of what the larger engine adds for as long as it took six more
capabilities to land.

What stays here is the part that is not a list of names: the semantics those
names must have exactly (§6), what CPython does not agree with itself about
(§6a), and the contract for leaving the subset (§7).

## 4. Build order

`lypning conformance --plan` and `lypning routes --plan` are the build order.

## 5. Explicitly out of scope

| feature | decision |
|---|---|
| `subprocess`, `multiprocessing` (`module`); `os.system` (`module-attr`) | **Out.** The caller already *is* a shell: hoist the command into the next line of the bash block. It does **not** silently shell out — a fake `subprocess` that works would keep the expensive pattern alive. |
| `threading`, `asyncio`, `async`/`await`; `http.server`, `socket`, any network | **Out.** Exit 90; single-threaded and offline by construction. |
| `pip`, third-party imports (`PIL`, `numpy`) | **Out**, and not a refusal: `ModuleNotFoundError`, exit 1, as CPython (§7 rule 4). |
| `eval`, `exec`, `compile`, `globals()`, `__import__`; metaclasses, descriptors; REPL, `-i`, `-m` | **Out.** They turn every subset guarantee into a runtime question; `-m` is `cli: option -m`. |

## 6. Semantics that must be exact

The one outcome we cannot tolerate is **silent semantic divergence from
CPython**: a program that runs, prints something plausible, exits 0, and is
wrong. Everything in this table has a corpus entry that would catch a
divergence, and every row is **exact, or exit 90** — never approximate.

| semantic | required behaviour | anchor entry | variants |
|---|---|---|---|
| `int` | i64, exact; a result past 64 bits is `unsupported: bigint`, and `int / int` past 2**53 is `int-div-precision` (`ops.rs`) | `bigint-arith` | both refuse → cpython |
| `/` vs `//` vs `%` | `/` always float; `//` floors toward −∞ (`-7 // 2 == -4`); `%` takes the divisor's sign | `arith-mixed` | exact on every variant |
| `float` repr | CPython's shortest round-trip: `0.1 + 0.2` → `0.30000000000000004`, `1e22` → `1e+22`, `100.0/4` → `25.0` | `float-repr` | exact |
| `round` | banker's rounding, and `round(2.675, 2)` → `2.67` (the float, not the decimal, answer) | `arith-mixed` | exact |
| `str` | unicode; `len("åäö") == 3`, `len("åäö".encode()) == 6` | `file-encoding-utf8`, `str-translate-encode` | exact; a non-ASCII `repr` outside the printable whitelist exits 90 (`repr-unicode`) |
| `bytes` repr | `b'caf\xc3\xa9'` — printable ASCII literal, `\xNN` otherwise | `str-translate-encode`, `struct-pack` | exact |
| `dict` | insertion-ordered; repr `{'a': 1, 'b': 2}` with the exact spacing | `dictcomp`, `dict-items-loop` | exact — insertion-ordered natively |
| `sorted` | **stable** | `sorted-stability` | exact |
| container repr | `print(list)` shows element `repr`s: `[1, 'b']`, tuples as `('k', '=', 'v=w')`, `None`/`True` bare | `repr-vs-str`, `str-partition-splitlines` | exact; a set with more than one element exits 90 (`set-order`) |
| exception messages | verbatim: `invalid literal for int() with base 10: 'abc'`, `KeyError` printing as `'missing'`; where CPython's own text differs across 3.9–3.13 it is worded for the **host** (§6a) | `except-generic-message`, `json-keyerror-guard`, `min-max-of-an-empty-iterable-say-what-cpython-says` | exact |
| exception hierarchy | `JSONDecodeError` ⊂ `ValueError`; `FileNotFoundError` ⊂ `OSError` with `.errno == 2` | `json-bad-input-exit`, `file-missing-raises` | exact |
| `json.dumps` defaults | `ensure_ascii=True` (so `"åäö"` → `å…`), separators `", "` / `": "`, `indent` emits no trailing spaces | `json-dumps-unicode`, `stdin-json-pretty` | exact (`json.rs`) |
| `csv.writer` | terminates rows with `\r\n` and doubles embedded quotes | `csv-writer-quoting` | `lypning` exits 90 `module: import csv`; `lypning-l` serves the two readers and exits 90 `module-attr: csv.writer` → cpython |
| text I/O | universal newlines on read; `\n` written as `\n` (no translation); `sys.stdout.write` adds nothing | `stdin-grep-substring` | exact |
| `re` | leftmost, non-POSIX-longest, backtracking; `match` anchors at 0 while `search` does not; `\d \w \s` are **unicode-aware** by default | `re-match-vs-search`, `stdin-regex-extract` | `lypning` exits 90 `module: import re`; `lypning-l` serves the slice of the pattern language `re.rs` names and refuses the rest |
| exit codes | clean end → 0; `sys.exit(n)` → n; uncaught exception → 1 with a traceback on stderr and nothing extra on stdout | `stdin-exit-code-nonzero`, `uncaught-exception-traceback` | exact |
| iteration over a file/stdin | yields lines *with* their `\n`, lazily (so `break` after 2 lines does not read the rest) | `stdin-head-n` | exact |
| `random` | a seeded stream is CPython's MT19937 bit for bit; an unseeded draw exits 90 (`random.rs`) | `random-seeded` | exact or refuse |
| `set` order | order-independent operations work; anything exposing an order exits 90 (`value.rs:set_order_refused`) | `setcomp-ops` | exact or refuse |
| NaN identity | `n in [n]` is True by identity; two NaNs in one comparison exit 90 (`nan-identity`) | — | refuse → cpython |

The `lypning-l` CSV readers also accept direct lists, tuples and strings of
lines. Each element is one line, not an arbitrary byte chunk; a string is an
iterable of single-character lines. Reading stays lazy: list mutations before
the next pull are visible, but a list iterator that has observed EOF never
revives after an append. A quoted field may span several input elements.
An explicit `DictReader(fieldnames=...)` list also remains live when its
caller changes column names or appends/removes columns between rows.
Non-string elements, unsupported dialects, shared iterators and generators
still refuse. In particular, wrapping an arbitrary iterator is not a safe way
to bypass the stale-file check on file-backed readers. Writers remain static
refusals; this extension adds no value-object variant or core capability.

### 6a. Where CPython does not agree with itself

`pyproject.toml` says `requires-python = ">=3.9"`, and CPython's own answer is
not the same on all of those versions. **The engine is worded for the host** —
`build.rs` asks the interpreter `engines.find_cpython()` names what version it
is and compiles it in as `err::REF_PY_MINOR`, which `builtins.rs`, `value.rs`,
`ops.rs`, `bigint.rs` and `err.rs` itself branch on. No refusal and no runtime
cost: every branch is against a compile-time constant, so each build carries
one wording and the optimiser drops the others (the crate got 64 bytes of code
*smaller*, 2026-09-12).

Which version a given binary was built for is legible from outside it, because
a binary built for one CPython and graded against another is wrong in the one
shape that reads as an engine defect: `lypning --version` ends `for cpython
3.11`, `lypning doctor`'s `reference cpython` row compares that with the
interpreter the binary falls through to and FAILs when they differ, and
`tests/test_build.py::test_the_suite_and_the_engine_it_grades_speak_the_same_cpython`
fails in one line when the suite grades it with another. `docs/VERIFICATION.md`
§C7.

**A minor version is not a complete behavior profile.** On 2026-09-15,
CPython 3.12.13 exposed a builtin `normpath`, truth-value conversion for sort's
`reverse`, and the short `iter(v, w)` error, unlike the older 3.12 measurements
below. `reference_probe.py` measures these behaviors and the zero-length signed
negative `int.to_bytes` boundary using the exact executable selected by
`engines.find_cpython()`. Cargo compiles the flags into both engines; no Python
is invoked at runtime. Rebuild when the reference executable changes, even
within a minor version: the version/doctor check currently compares only 3.x.
A bare Cargo build without a usable probe warns and uses legacy defaults;
that fallback is not evidence of agreement with a particular interpreter.

**`sys.version` is a build's text, not a version's.** The date and compiler
differ on every CPython build, including a bottle rebuilt at the same micro, so
no table keyed on the minor can serve it. lypning-l (`cap-random`) serves it
only from a bake: a separate probe (`sys_probe.py`), run by `build.rs` on the
interpreter that answered `reference_probe.py` and only when that interpreter
is the named reference (`err::REF_PY_EXACT`), records the text and the
`(realpath, length, mtime)` of the interpreter and of its shared libpython. At
run time lypning-l resolves the CPython a refusal would reach — a
`$LYPNING_CPYTHON` path, or the first non-shim `python3` on `PATH` — and
answers only when both fingerprints match. Anything else refuses as
`module-attr` (a bare-name or `~` pin, a `#!` script, a copy, an upgrade,
another minor, a build with no bake), and that CPython prints its own
version. The core never answers it; the router sends it to lypning-l only
from a build whose probe ran (`route::cap_attr`).

**`unicodedata` is the reference's own tables.** Every minor ships another
Unicode, and 3.13 began answering `decomposition()` for Hangul syllables, so
lypning-l (`cap-re`) carries no table of its own: `build.rs` runs
`ucd_dump.py` on the probed reference, under the same `REF_PY_EXACT`
condition, and `ucd.rs` answers `category`, `combining`, `decomposition`,
`normalize` and `is_normalized` from that dump (UAX #15 for the forms). With
no dump, `import unicodedata` refuses. While the module is imported, `str`'s
own predicates and case mappings, argument-less `split`/`strip` and
`int()`/`float()` of non-ASCII text refuse when the reference's Unicode is not
Rust's, and so does `chr()` of a surrogate.

**A call whose arguments do not bind refuses, in every variant.** CPython's
binding `TypeError` names the function by its qualified name from 3.10, counts
every missing argument at once, and adds `Did you mean` on 3.14, so no wording
is right on every minor: `err::bind_refused` (`call`).

The rule that decides whether a site gets a branch is narrower than "this is
version-dependent": it is **the ANSWER differs across 3.9 … 3.13**, each
boundary measured on all five with `uv run --python X` and written down at the
site. Where all five agree the constant is not consulted and the engine keeps
answering — `sorted([3, 1], strict_mode=True)` is the case that pins the
distinction in the other direction: its text *does* differ (3.13 rewrote it), so
it is worded per host and still exits 1 with CPython's own `TypeError` on every
one of them.

| what | 3.9 | 3.10 | 3.11 | 3.12 | 3.13 |
|---|---|---|---|---|---|
| `min([])` | `min() arg is an empty sequence` | ← | ← | `min() iterable argument is empty` | ← |
| `int('1', 16, 0)` | `int() takes at most 2 arguments (3 given)` | ← | ← | ← | `int expected at most 2 arguments, got 3` |
| `sorted(xs, bogus=1)` | `'bogus' is an invalid keyword argument for sort()` | ← | ← | ← | `sort() got an unexpected keyword argument 'bogus'` |
| `zip([1], bogus=1)` | `zip() takes no keyword arguments` | the invalid-keyword form | ← | ← | the unexpected-keyword form |
| `enumerate()` | `… required argument 'iterable' (pos 1)` | ← | `… required argument 'iterable'` | ← | ← |
| `str(1, 0)` | `str() argument 2 must be str, not int` | `str() argument 'encoding' must be str, not int` | ← | ← | ← |
| `iter([1], 0)` | `iter(v, w): v must be callable` | ← | ← | build-dependent, probed | build-dependent, probed |
| `type(os.path.normpath)` | `function` | ← | ← | build-dependent, probed | build-dependent, probed |
| `type(re.compile('a').match)` | `builtin_function_or_method` | `builtin_method` | ← | ← | ← |
| `1 % 0` | `integer division or modulo by zero` | ← | `integer modulo by zero` | ← | ← |
| `float([])` | `… must be a string or a number` | `… or a real number` | ← | ← | ← |
| `int([])` | `… a string, a bytes-like object or a number` | `… or a real number` | ← | ← | ← |

The last three rows were found after the first nine, and they are why the
paragraph that used to close this section — "four further divergences in the
same range" — no longer claims to be a list of what exists. `1 % 0` is one
operator and not a family: `1 // 0` and `divmod(1, 0)` keep the long sentence on
all five, so `err::int_mod_by_zero` is `%`'s alone and the pinning row in
`tests/test_semantics.py` fails if that branch is ever widened. `int([])` is the
odd one out in the other direction: the engine's text matched NO supported
CPython, having dropped the bytes-like clause both wordings carry, so it was a
plain bug wearing a skew's clothes.

**This table is the list of divergences the engine HANDLES, not the list that
exists.** A differential sweep on 2026-09-12 — 14,808 generated one-liners over
the engine's own argument and operator grid, each run as `try: … except
Exception as e: print(e)` on the engine and on 3.9.23, 3.10.20, 3.11.15, 3.12.3
and 3.13.12 — found 622 programs whose answer is not the same on all five
CPythons, in 202 distinct five-way patterns. The engine refuses 62 of those
patterns (coverage, invariant 1), matches its host on 67, and answers
differently on 73. Against the host alone the same sweep put 1,461 of 11,777
answered programs on a message CPython 3.11 does not write, from 28 of the
crate's message literals. The sweep is not checked in: *measured 2026-09-12 with
a one-off differential sweep; not reproducible from this tree.* What it covers
is one generated cross-product, and what it cannot reach is everything the
generator does not spell — multi-statement programs, the module surfaces beyond
the calls listed in it, and any message whose literal no generated program
raises (98 of 225 were reached).

Some of the residue is **capability**, not wording, and is left alone rather
than approximated: `NameError`'s `Did you mean: …?` suggestion (3.10+, the
engine says the bare 3.9 form and is therefore wrong on four hosts out of five
whenever a near-miss name exists), `str | None` at runtime (PEP 604, 3.10+),
and the `match` statement (3.10+, a
`SyntaxError` here — `route.rs` sends it to CPython, so the chain is right and
only the bare engine arm is not). A suggestion engine guessed at is a MISMATCH
generator. The rest is wording, and the largest pieces of it are named in the
`Unreleased` entry of `CHANGELOG.md` for whoever takes them next: each needs the
same five-way measurement, and none of them may become a refusal, because every
one is an answer CPython gives.

`str.replace(count=…)`, added in
[Python 3.13](https://docs.python.org/3.13/library/stdtypes.html#str.replace),
is served for references from that version onward. Its implementation and
keyword validator already agreed, but a second method-level allow-list still
rejected it. String keyword admission now has one source, `str_kw_allowed`;
the mandatory-answer version tests cover counts, empty patterns and Unicode.

#### The 3.14 boundary

CPython 3.14 changed observable results, not only traceback decoration. Its
[`ceval.c`](https://github.com/python/cpython/blob/v3.14.0/Python/ceval.c)
includes the actual size in excess-unpacking errors for exact lists, tuples
and dicts, but not their iterators or dict subclasses. The engine preserves
that distinction. Ordinary unpacking consumes at most one excess item before
raising; starred unpacking still consumes the entire tail.

The 3.14 [`list.index`](https://github.com/python/cpython/blob/v3.14.0/Objects/listobject.c)
failure no longer renders the missing value. Integer and float division and
modulo errors now use a common sentence, while zero to a negative power keeps
its own wording; see
[`longobject.c`](https://github.com/python/cpython/blob/v3.14.0/Objects/longobject.c)
and [`floatobject.c`](https://github.com/python/cpython/blob/v3.14.0/Objects/floatobject.c).
`err::zero_div` selects those messages without changing the exception class.
Earlier hosts keep their own wordings, including float modulo's separate
3.13 change.

Zero-length signed integer conversion also differs between patch releases:
`(-1).to_bytes(0, 'big', signed=True)` returned empty bytes on 3.12.13 and
raised `OverflowError` on 3.13.13 and 3.14.5, measured 2026-09-15. A minor-version
check cannot represent that history: the reference behavior probe supplies
`REF_ZERO_NEGATIVE_BYTES_OVERFLOW`. Zero itself still fits; other nonzero
integers still overflow, and the unsigned-negative error keeps precedence.

`tests/test_version_semantics.py` requires successful, byte-identical answers
from both core and L against the running interpreter. It checks exact
containers against other iterables/subclasses, excess-iterator consumption,
short/starred/nested unpacking, index bounds, zero-width conversion and numeric
zero-division errors. It never treats refusal as a passing answer.

## 7. Failure modes: the unsupported contract

When a program uses something an engine does not implement: exit code **90**,
one line on stderr, nothing else — `<engine>: unsupported: <kind>: <detail>`,
`<engine>` being the binary's own name (`err.rs:ENGINE`, `engines.refusal_line`)
and `<kind>` any `[\w-]+` (`conformance._UNSUPPORTED_RE`). `grep -ho
'unsupported("[a-z-]*' src/lypning/assets/rust/src/*.rs | sort -u` lists the
kinds spelled at call sites; the rest reach `err.rs:unsupported` through helpers
— `set-order` (`value.rs:set_order_refused`), `repr-unicode` (`fmt.rs`),
`int-div-precision`, `nan-identity` and `type` (`ops.rs`).

90 is clear of 0/1/2 (CPython's own), of 126/127 (shell "cannot execute" /
"not found"), and of 128+n (signals), so a caller can branch on it
unambiguously and retry with real `python3` — or rewrite the line as awk.

1. **Never exit 90 for a program `<engine>` can run.** A supported program's exit
   code is CPython's.
2. **Never exit 1 for a program `<engine>` merely does not support.** Exit 1 means
   "this program ran and raised", nothing else.
3. **Detect early where possible.** A syntactic gap exits 90 before a statement
   runs; a runtime gap fires later and the barrier discards what was staged.
4. **`ImportError` vs 90.** A module CPython itself would not find in this
   image (`PIL`, `numpy`) raises `ModuleNotFoundError` and exits **1**, exactly
   as CPython does — that is a program that ran correctly. Exit 90 is reserved
   for modules that *exist in CPython* but not in `<engine>` (`subprocess`,
   `argparse`, `zlib`).
5. **Nothing else on stderr in the 90 case**, and `sys.exit(90)` is not a
   refusal: no line, the program's own number, returned unchanged.

Silent semantic divergence — a program that runs to completion and prints the
wrong thing — is the failure this whole contract exists to make impossible to
miss. `docs/LYPNING.md` §2 grades it; §6 is the barrier a runtime 90 needs.

## 8. Verify one row

```bash
lypning -c 'print(-7 // 2)'; echo $?             # → -4, 0 — as python3 -c does
lypning -c 'print(2**100)'; echo $?              # stderr: lypning: unsupported: bigint: …; 90 — and nothing on stdout
```

Every row on every engine: `docs/VERIFICATION.md` §C1, §C3 (`MISMATCH 0`).
