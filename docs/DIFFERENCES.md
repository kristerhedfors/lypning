# How lypning and lypning-l differ from Python

*The delta, engine by engine: what each one resolves, what it refuses, and which
refusals skip the larger engine entirely. Every difference here is **exact or
exit 90** — never an approximation — and the rule that makes that safe to rely
on is `docs/SUBSET.md` §7. This document is the map of the exit-90 side; the
`docs/SUBSET.md` §6 table is the map of the other one.*

## 1. Three outcomes, and the third is a bug

Run a program under `lypning` or `lypning-l` and exactly one of three things
happens.

1. **CPython's answer**, byte for byte on stdout and stderr, with CPython's exit
   code — including when the program's own answer is a traceback at exit 1.
2. **A refusal**: exit `90`, one `<engine>: unsupported: <kind>: <detail>` line
   on stderr, nothing on stdout, and nothing the program staged committed
   (`docs/LYPNING.md` §6). The dispatcher then runs the program on the next
   engine that can answer it — `lypning-l`, or CPython — so the caller still
   gets the answer, one spawn later.
3. **A different answer at exit 0.** That is the failure the whole project
   exists to prevent; `lypning conformance` counts it as MISMATCH, and MISMATCH
   must be 0.

Everything below is therefore a list of **spawns, not of limitations**: each
entry costs a process and none of them costs an answer. What a caller must
handle is the exit code, not the gap — `README.md` §5.

## 2. It is not a Python implementation

The engines are one Rust crate that parses and evaluates a subset chosen from
what agents type (`docs/LYPNING.md` §1). Almost everything CPython does *around*
executing a program is simply absent, and the absences are the architecture:

| CPython | the engines | what a program sees |
|---|---|---|
| a bytecode compiler, an eval loop and a `.pyc` cache | a recursive-descent parser and a tree-walking evaluator (`parse.rs`, `eval.rs`) | nothing is compiled to disk and nothing is cached; startup is the binary's own `execve` |
| an import system searching `sys.path` | a module table compiled into the binary (`modules.rs:MODULES`) | `sys.path`, `sys.modules` and `__import__` do not exist; every other import is `unsupported: module: import <name>` |
| classes, metaclasses, descriptors, the `__dunder__` protocols | functions, closures and `lambda`, and no way to define a type | `class` is `unsupported: class` at parse time, so every custom exception, `dataclass` and `namedtuple` is CPython's work |
| `eval`, `exec`, `compile`, `__import__` | nothing | `unsupported: builtin: <name>`. They are absent by decision, not by omission: each turns every static guarantee in `route.rs` into a runtime question (`docs/SUBSET.md` §5) |
| introspection: `dir`, `vars`, `globals`, `locals`, `getattr`, `setattr`, `hasattr`, `id`, `hash` | nothing | `unsupported: builtin: <name>` |
| reference counting, a cycle collector, `__del__`, `weakref` | `Rc` (`value.rs`) | no finalizers and no `gc`; a file is closed by `with`, by `.close()`, or at exit |
| threads, signals, sockets, subprocesses | none | single-threaded and offline by construction (`docs/SUBSET.md` §5) |
| a 1000-frame recursion limit and `sys.setrecursionlimit` | a native stack: 180 call frames and 600 nested expression evaluations (`eval.rs:MAX_DEPTH`, `MAX_EXPR_DEPTH`) | `unsupported: recursion`, never a `RecursionError` — the binaries are built `panic = "abort"` (`Cargo.toml`), so a real stack overflow would be exit 134, which is nobody's answer |
| a REPL, `-i`, `-m`, `site`, `PYTHONSTARTUP` | the three one-liner shapes and nothing else (`docs/SUBSET.md` §2) | `-m` is `unsupported: cli: option -m`; there is no interactive mode to enter |
| `__file__`, `__spec__`, `__package__` bound per module | `__name__`, which is `__main__` | `__file__` is `unsupported: dunder-missing` rather than a `NameError`, because CPython binds it for a script and leaves it unbound under `-c`, and only one of those can be faked (`err.rs:name_err`) |

**An absent module and an absent package are the same refusal to the engine and
different answers to the caller.** `import numpy` and `import itertools` both
exit 90; the *chain* then runs the program on CPython, where one raises
`ModuleNotFoundError` at exit 1 and the other works. The engine cannot tell them
apart — it has no `sys.path` to look on — and does not have to, because the
distinction is the dispatcher's (`docs/SUBSET.md` §7 rule 4).

## 3. Values: the same objects, four narrower guarantees

The value model is CPython's where it is anything at all: integer arithmetic is
exact or refused and never wrapped, `/`
is always a float, `//` and `%` round toward negative infinity, `float` repr is
shortest round-trip, `dict` is insertion-ordered, `sorted` is stable, and the
exception messages are CPython's own words for the CPython this binary was
built against (`docs/SUBSET.md` §6, §6a). Four guarantees are narrower, and each
is a refusal rather than an approximation — `docs/LYPNING.md` §3 is why:

| | `lypning` | `lypning-l` |
|---|---|---|
| **integer width** | i64. Every operation is checked, and a result past 64 bits is `unsupported: bigint`, never a wrap | exact at any width (`cap-bigint`, `bigint.rs`). What still refuses there: a wide integer mixed with a `float`, and a wide integer as a `dict` key |
| **set order** | order-independent operations answer; anything exposing an order is `unsupported: set-order` | the same |
| **non-ASCII `repr`** | answered for the printable blocks a whitelist names, `unsupported: repr-unicode` otherwise | the same |
| **NaN in a container** | `unsupported: nan-identity` when both sides of one element comparison are NaN, because CPython decides that by object identity | the same |

Integer width is the **only** semantic difference between the two engines. Every
other row of this document is a difference of *surface* — which names resolve —
and `route.rs` holds the rule that makes that safe: a program `lypning` answers,
`lypning-l` answers identically (`docs/VERIFICATION.md` §C4).

## 4. The name surface

### 4.1 Builtins

These resolve, on both engines (`builtins.rs:BUILTINS`):

```
abs all any bin bool bytes chr dict divmod enumerate filter float format hex
input int isinstance iter len list map max min next oct open ord print range
repr reversed round set sorted str sum tuple type zip
```

and these exception names (`builtins.rs:EXCEPTIONS`):

```
ArithmeticError AssertionError AttributeError BaseException Exception
FileExistsError FileNotFoundError IOError IndexError KeyError LookupError
NameError NotImplementedError OSError OverflowError PermissionError
RuntimeError StopIteration SystemExit TypeError UnboundLocalError
UnicodeDecodeError ValueError ZeroDivisionError
```

Everything else CPython puts in `builtins` — `frozenset`, `bytearray`,
`memoryview`, `object`, `pow`, `callable`, `ascii`, `issubclass`, `slice`,
`super`, `property`, `classmethod`, `staticmethod`, and every exception name not
listed above — is `unsupported: builtin: <name>` at exit 90. A name **neither**
has is the program's own bug and keeps CPython's `NameError` at exit 1; the
split is `err.rs:CPYTHON_BUILTINS`, and without it a typo'd name and a missing
capability would be indistinguishable to the dispatcher.

The methods on the types that do exist are a larger surface with the same rule:
a method CPython has and the engine lacks refuses as `<type>-method` or
`module-attr` rather than raising `AttributeError`. `methods.rs` is the table;
`tests/test_method_tables.py` pins it.

### 4.2 Modules

Both engines serve these and nothing else from the standard library
(`modules.rs:MODULES`):

```
sys os os.path posixpath io json math random
```

`random` is the seeded-integer subset only — a seeded stream is CPython's
MT19937 bit for bit, and an unseeded draw is `unsupported: random`, because a
stream seeded from the OS is not reproducible (`random.rs`). `math` serves
`ceil`, `copysign`, `fabs`, `factorial`, `floor`, `fmod`, `gcd`, `isfinite`,
`isinf`, `isnan`, `isqrt`, `sqrt` and `trunc` plus the constants; the
transcendentals are refused rather than approximated, which is why `math` needs
no capability feature — a larger engine would answer every `math` program
exactly as the smaller one does (`math.rs`).

Any other import — `itertools`, `functools`, `datetime`, `textwrap`,
`string`, `struct`, `argparse`, `subprocess`, a third-party package, a module of
the user's own — is `unsupported: module: import <name>` on both engines, and
`lypning-l` adds the modules in §5.

### 4.3 Modules served in part

Five modules on `lypning-l` are served as a named list of attributes rather than
whole, so the walk in the *smaller* engine can decide statically whether the
larger one would answer. Everything not listed is `unsupported: module-attr:
<module>.<name>` — including under `from <module> import <name>`:

| module | served | source |
|---|---|---|
| `base64` | `b64decode` `b64encode` `urlsafe_b64decode` `urlsafe_b64encode` | `route.rs:BASE64_SERVED` |
| `csv` | `DictReader` `QUOTE_ALL` `QUOTE_MINIMAL` `QUOTE_NONE` `QUOTE_NONNUMERIC` `reader` | `route.rs:MODULE_ATTRS` — the writers are CPython's |
| `glob` | `escape` `glob` `has_magic` `iglob` | `route.rs:GLOB_SERVED` |
| `hashlib` | `md5` `sha1` `sha256` `sha512` | `hashlib.rs:SERVED` — `new`, the SHA-3 family and the KDFs are CPython's |
| `time` | `gmtime` `monotonic` `monotonic_ns` `perf_counter` `perf_counter_ns` `sleep` `strftime` `time` `time_ns` | `time.rs:SERVED` — everything local-time is CPython's |

`collections` serves `Counter` and `defaultdict`; `pathlib` serves `Path`. Both
are whole-module claims in `route.rs:CAPS`, so an attribute neither serves —
`collections.deque`, `pathlib.PurePath` — refuses at runtime on `lypning-l`
rather than in the smaller engine's walk, which costs one spawn and no answer.

## 5. What `lypning-l` adds

`lypning-l` is the same crate built with nine `cap-*` features
(`engines.VARIANT_CAPS`, `route.rs:CAPS`, and `lypning route --spectrum` from
either binary):

| capability | what it adds | source |
|---|---|---|
| `cap-base64` | the `base64` module, four functions of it | `base64.rs` |
| `cap-bigint` | no module: integers past 64 bits, exact | `bigint.rs` |
| `cap-collections` | the `collections` module — `Counter`, `defaultdict` | `collections.rs` |
| `cap-csv` | the `csv` module — the two readers | `csv.rs` |
| `cap-glob` | the `glob` module | `glob.rs` |
| `cap-hashlib` | the `hashlib` module — four constructors | `hashlib.rs` |
| `cap-pathlib` | the `pathlib` module — `Path` | `pathlib.rs` |
| `cap-re` | the `re` module and its matcher | `re.rs` |
| `cap-time` | the `time` module — the clocks, a bounded `sleep`, one UTC stamp | `time.rs` |

`cap-re` serves a **slice** of the pattern language, and the rest of it is
refusals rather than a best effort: non-ASCII group names, backreferences, lookaround,
bytes patterns, Unicode `\w`/`\d`/`\s`, Unicode case folding and a backtracking
step budget all refuse (`docs/LYPNING.md` §3).

The cost of the eight is the binary: `lypning` 1,147,088 B in 9 blocks and
`lypning-l` 1,323,216 B in 11 blocks, both x86\_64-unknown-linux-musl, measured
2026-09-14 by `lypning build --rust` on this tree. `lypning gate` holds each
against its own budget (`gate.VARIANT_BLOCK_BUDGET`), and `docs/LYPNING.md` §11
is what adding a ninth capability costs.

## 6. Refusals that skip `lypning-l`

Most refusals are capability gaps, and the dispatcher answers them by trying the
larger engine. These are not gaps. Each names a behaviour CPython has that a
reimplementation gets *wrong*, so a larger build of the same
reimplementation would get it wrong too, and answering at exit 0 is exactly what
must not happen. A program that refuses with one of these goes straight to
CPython (`route.rs:ONLY_CPYTHON_KINDS`, `engines.ONLY_CPYTHON_REFUSALS`):

`del` · `dict-view` · `dunder-missing` · `encoding` · `exception-chaining` ·
`glob-order` · `identity` · `iterator-type-name` · `json` · `math` ·
`nan-identity` · `nan-order` · `percent-format` · `random` · `repr-unicode` ·
`set-method` · `set-order`

Three of them show what the list is for: `identity` fires on `is` between two
equal immutables that are not provably the same object, where an interning
scheme that is not CPython's answers `True` for `int('1000') is 1000`;
`nan-order` fires on a sort containing a NaN, whose result is the sort
*algorithm's* answer rather than Python's; `glob-order` fires on a `glob()`
result in a position that would show the order of two or more matched paths,
which comes from `os.scandir` and cannot be reproduced by anything.

One construct is on the other list — `route.rs:CPYTHON_ONLY_KINDS` — for the
opposite reason: `async` is not something a reimplementation gets wrong, it is
something no Rust variant *has*, because the program needs an event loop. A
larger sibling would refuse it identically, so the chain does not spend the
spawn asking.

## 7. Syntax

Supported, on both engines: literals, the operators with CPython's precedence,
chained comparison, slicing with a step, calls with `*args`/`**kwargs`,
assignment and star-unpacking, slice assignment, `global`, augmented
assignment, `if`/`for`/`while`, `def` with defaults and closures, `lambda`,
imports, `with`, `try`/`except`/`finally`, `raise`, `assert`, comprehensions,
generator expressions, and f-strings with format specs (`parse.rs`). `del`
takes a name, a subscript or a tuple of them; a slice or an attribute target
is `unsupported: del`.

Refused, on both engines, and detected before a statement runs:

| construct | refusal |
|---|---|
| `class` | `unsupported: class` |
| `@decorator` | `unsupported: decorator` |
| `yield`, and so every generator function | `unsupported: generator` |
| `async def`, `await`, `async for` | `unsupported: async` |
| `:=` | `unsupported: walrus` |
| `nonlocal` | `unsupported: nonlocal` |
| `except*` | `unsupported: except-star` |
| keyword-only parameters, `def f(*, a)` | `unsupported: kwonly` |
| `...` | `unsupported: ellipsis` |
| `1j` | `unsupported: complex` |

`match` is the one construct that is **not** a refusal: the grammar predates it,
so every engine answers `SyntaxError` at exit 1. The chain is still right —
`route.rs` sends the program to CPython — and only the bare engine's answer is
wrong (`docs/VERIFICATION.md` §C5).

## 8. Check a row

```bash
lypning -c 'print(-7 // 2)'; echo $?      # → -4, 0 — what python3 -c prints
lypning -c 'import re'; echo $?           # → lypning: unsupported: module: import re on stderr, 90
lypning-l -c 'import re'; echo $?         # → 0: the capability is the difference
lypning route --spectrum                  # → the variants and their cap-* sets, as JSON
lypning conformance --mixture both        # → MISMATCH 0 · UNSAFE 0 · dispatchers agree N/N
```

Every table here is pinned to the engine's own tables by
`tests/test_differences.py`, which fails if a name in this document is not a
name in the crate. What that cannot check is prose, and the standing rule for
prose is `CLAUDE.md`: a number is quoted with the run and the date that produced
it, or it is left out.
