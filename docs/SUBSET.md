# The subset and the refusal contract

*What every Rust variant must execute exactly, and what happens when a program
leaves that set. The engines are `engines.ENGINE_ORDER`; `lypning-mp`, the
oracle — measured, never routed to — is graded against these rows when named.*

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

### 3.1 Syntax

Literals, operators with CPython's precedence, chained comparison, slicing
with a step, calls with `*args`/`**kwargs`, assignment and star-unpacking,
`if`/`for`/`while`, `def` with defaults and closures, `lambda`, imports,
`with`, `try`/`except`/`finally`, `raise`, `assert`, comprehensions, generator
expressions, f-strings with format specs (`parse.rs`). `class`, decorators,
`yield` and `async` are parse-time refusals and a route to `cpython`; `match`
is a SyntaxError on every variant, exit 1 (`docs/VERIFICATION.md` §C5).

### 3.2 Builtins

`builtins.rs:BUILTINS` is the table, read by the router; `input()` is in it. A
name CPython has and the table lacks is `unsupported: builtin: <name>`; a name
neither has keeps CPython's `NameError` and exit 1 (`err.rs:CPYTHON_BUILTINS`).

### 3.3 Stdlib

`modules.rs:MODULES` is one table per variant; `route.rs:CAPS` maps each
`cap-*` feature to the module it serves.

| variant | modules | source |
|---|---|---|
| `lypning` | `sys`, `os`, `os.path`, `posixpath`, `io`, `json`, `random` (seeded-integer subset, MT19937 exactly) | `modules.rs`, `json.rs`, `random.rs` |
| `lypning-l` | the above plus `collections` (`Counter`, `defaultdict`) and `pathlib` (`Path`) | `collections.rs`, `pathlib.rs` |
| `cpython` | everything else, one spawn later | — |

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
| exception messages | verbatim: `invalid literal for int() with base 10: 'abc'`, `KeyError` printing as `'missing'` | `except-generic-message`, `json-keyerror-guard` | exact, or exit 90 where the text drifts across CPython versions |
| exception hierarchy | `JSONDecodeError` ⊂ `ValueError`; `FileNotFoundError` ⊂ `OSError` with `.errno == 2` | `json-bad-input-exit`, `file-missing-raises` | exact |
| `json.dumps` defaults | `ensure_ascii=True` (so `"åäö"` → `å…`), separators `", "` / `": "`, `indent` emits no trailing spaces | `json-dumps-unicode`, `stdin-json-pretty` | exact (`json.rs`) |
| `csv.writer` | terminates rows with `\r\n` and doubles embedded quotes | `csv-writer-quoting` | exit 90 `module: import csv` → cpython |
| text I/O | universal newlines on read; `\n` written as `\n` (no translation); `sys.stdout.write` adds nothing | `stdin-grep-substring` | exact |
| `re` | leftmost, non-POSIX-longest, backtracking; `match` anchors at 0 while `search` does not; `\d \w \s` are **unicode-aware** by default | `re-match-vs-search`, `stdin-regex-extract` | exit 90 `module: import re` → cpython |
| exit codes | clean end → 0; `sys.exit(n)` → n; uncaught exception → 1 with a traceback on stderr and nothing extra on stdout | `stdin-exit-code-nonzero`, `uncaught-exception-traceback` | exact |
| iteration over a file/stdin | yields lines *with* their `\n`, lazily (so `break` after 2 lines does not read the rest) | `stdin-head-n` | exact |
| `random` | a seeded stream is CPython's MT19937 bit for bit; an unseeded draw exits 90 (`random.rs`) | `random-seeded` | exact or refuse |
| `set` order | order-independent operations work; anything exposing an order exits 90 (`value.rs:set_order_refused`) | `setcomp-ops` | exact or refuse |
| NaN identity | `n in [n]` is True by identity; two NaNs in one comparison exit 90 (`nan-identity`) | — | refuse → cpython |

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
