# lypning-mp — the Python subset

*Spec date: 2026-08-13. Status: IMPLEMENTED — on 2026-08-13 `assets/micropython/build/lypning-mp`
passed 189 of the 202 corpus entries then in the battery, with 0 mismatches and
13 unsupported. The battery has grown since: `lypning conformance` loads
`seed-corpus.jsonl` plus the harvested `corpus.jsonl` and dedups them to 375
entries as of 2026-08-15, so run `lypning conformance` for the current
pass/unsupported split — it prints the corpus size it loaded, which has grown
again since.*

**lypning-mp** is a minimal Python interpreter for the in-browser Linux
sandbox (CheerpX, x86-32). It exists because the real
thing is unaffordable there: `docs/SANDBOX-PERFORMANCE.md` §1 measures
`python3 --version` at **8573 ms cold / 87 ms warm**, and `src/prompts.js`
already tells the agent that "awk/sed/grep beat starting python3 for simple
text work". lypning-mp's job is to make the python the agent *does* reach for cost
what a shell builtin costs.

It is not a Python implementation. It is the subset that the agent loop
actually invokes, and the contract for what happens when a program leaves that
subset.

## 1. The evidence base

Everything below traces to `assets/corpus/seed-corpus.jsonl` — 161 programs,
each with the stdout that the system CPython 3.11.15 actually produced (154
entries; 7 are deliberately blank because their output is a wall clock, a PRNG
stream, or a module we will never have).

| provenance | entries | meaning |
|---|---|---|
| `repo` | 18 | the invocation, or the exact idiom, appears in this repository |
| `experience` | 143 | recurring agentic-CLI idioms, written to the shapes the repo's sandbox prompts describe |

**How to run the corpus.** 17 entries write files. Their recorded
`expect_stdout` was produced with each program in its **own empty working
directory**, `stdin` on the pipe, `argv_tail` after `-c`, and a minimal
environment (`PATH`, `HOME`, `LC_ALL=C.UTF-8`). Entries that list a directory
(`os-listdir-sorted`, `glob-pattern`, `os-walk-count`) look only at a subdir
they create themselves, so they are cwd-independent; the rest still leave files
behind, so a runner that does not use a scratch cwd will litter the repo.

**What the repo actually contains** (the whole observed set — python is rare
here, which is itself a finding).

> Every path in the right-hand column is a file in the **upstream**
> `deepresearch.se` repository this package was extracted from (`README.md` §8),
> not in this one. They are citations for where each invocation was observed;
> none of them resolve here and none are meant to.

| invocation | where |
|---|---|
| `python3 -c 'print(1+1)'` | `tests/e2e/sandbox-perf.spec.js:126` |
| `python3 -c 'import json;print(json.dumps({"a":1}))'` | `tests/e2e/sandbox-perf.spec.js:127` |
| `python3 -c "import PIL"` | `scripts/build-exec-image.sh:138`, `docs/EXECUTION-ENVIRONMENTS.md:741` |
| `python3 -m json.tool` (piped from curl) | `skills-disabled/publish-research/SKILL.md:110` |
| `python3 -m http.server 8123` | `docs/TESTING.md:1226`, `docs/FOREVERAGENT-GAP-ANALYSIS.md:215` |
| `python3 -m pip install --break-system-packages pillow` | `.github/workflows/ci.yml:114` |
| `python3 --version` | `tests/e2e/sandbox-perf.spec.js:125`, `docs/SANDBOX-PERFORMANCE.md` §1 |
| `python3 analyze.py /workspace/data.csv` | `docs/SANDBOX-HOST-COMMANDS.md:341,638`, `public/js/drc-research.js:382` |
| `python3 make_fixtures.py` (a 300-line stdlib script) | `tests/package.json`, `tests/make_fixtures.py` |
| "standard math via python3 or bc" | `src/prompts.js:403,412,423` |
| sha256 of a file, computed in the VM | `docs/test-batches/sandbox.json:19` |

Two absences carry weight. **`subprocess`, `os.system` and `multiprocessing`
do not occur anywhere in this repository** (grep over `*.md`, `*.py`, `*.sh`,
`*.js`, `*.mjs`, excluding generated artifacts) — see §5. And the session
transcripts under `~/.claude/projects/` contain **no python invocations at
all** beyond `python3 --version`: this session's agents were building lypning-mp,
not using python. That evidence lane produced nothing and is not padded here.

`tests/make_fixtures.py` is the only real python file in the tree and it is
the single richest evidence item: `os.path.join`, `os.makedirs(exist_ok=True)`,
`open(..., "wb")`, `struct.pack`, `zlib.crc32/compress`, `zipfile`,
f-strings with format specs (`f"{off:010d}"`), `enumerate(objects, start=1)`,
tuple unpacking in a `for` header, `try/except ImportError`, `assert`, raw
strings, `b"".join(genexp)`, and `isinstance`.

## 2. The one-liner shapes

The agent's shell is a bash-lite loop (`public/js/bash-core.js`), so a python
call is nearly always one line of a pipeline: **stdin → transform → stdout**,
plus an exit code. 20 of 139 corpus entries read `sys.stdin`; that is the
largest single cluster.

| shape | support | notes |
|---|---|---|
| `lypning-mp -c '<src>' [args…]` | **yes, Tier 0** | `sys.argv[0]` is `"-c"`, the rest follows — matches CPython |
| `lypning-mp script.py [args…]` | **yes, Tier 0** | the `analyze.py /workspace/data.csv` shape; `sys.argv[0]` is the path |
| `cat x.py \| lypning-mp -` | **yes, Tier 0** | program on stdin; then `sys.stdin` is already consumed, same as CPython |
| heredoc → file → run (`cat > x.py << 'EOF'` … `EOF`; `lypning-mp x.py`) | **yes** | `bash-core.js` keeps a heredoc whole as one command (`heredocDelimiters`), so this is the multi-line path |
| `lypning-mp -m json.tool` | **yes**, as a builtin alias (§8) | not a real module system |
| `lypning-mp -m <anything else>` | **no** → exit 90 | `http.server`, `pip`, `venv`, `unittest` |
| `lypning-mp --version`, `-V` | **yes** | prints `lypning-mp X.Y (python subset)` — it must not claim a CPython version |
| `lypning-mp` with no args (REPL), `-i` | **no** | the loop is non-interactive by construction (`src/prompts.js:492`) |
| `#!/usr/bin/env lypning-mp` shebang | **yes**, free once script mode works | |

## 3. Tier 0 — must work day one

Everything here is required by at least one corpus entry. Nothing in Tier 0 is
speculative.

### 3.1 Syntax

**Literals** — `int` (arbitrary precision, `bigint-arith`), `float`, `str`
with `'`/`"`/`"""`, raw `r"…"` (`re-*`), bytes `b"…"` with `\xNN`
(`file-binary-roundtrip`), f-strings (`fstring-*`), `True`/`False`/`None`,
list/tuple/dict/set displays, numeric underscores (`time-time-monotonic`).

**Operators** — `+ - * / // % **`, unary `-`, comparison `== != < <= > >=`,
**chained comparison** (`0.0 <= x < 1.0`, `random-unseeded`), `and or not`,
`in` / `not in`, `is` / `is not`, the conditional expression
(`argv-default-when-missing`), augmented assignment `+= -= *=`, subscription,
slicing with an optional step including negative (`str-slicing`), attribute
access, calls with positional, keyword, `*args` and `**kwargs`
(`def-varargs`, `print-sep-end`).

Precedence follows CPython exactly. The two that actually bite: `**` binds
tighter than unary minus, and `//` on negatives floors toward −∞
(`arith-mixed` prints `-4` for `-7 // 2`).

**Statements** — expression statements, assignment (including
`a, b = b, a` and `first, *rest = […]`, `tuple-unpack-swap`), `if/elif/else`,
`for … in` with tuple targets (`for i, (n, v) in enumerate(zip(…))`,
`zip-enumerate`), `while`, `break`, `continue`, `def` with defaults and
keyword-only usage, `return`, `lambda`, `import` / `from … import` /
`import a, b` / `a.b.c` attribute imports, `with … as` (single context
manager, `file-write-read-text`), `try` / `except E` / `except E as e` /
`except Exception as e` / `else`-less `finally` / bare `raise E(msg)`, `pass`.

**Comprehensions** — list (`listcomp-filter`), dict (`dictcomp`), set
(`setcomp-ops`), generator expressions as a call argument
(`sum(1 for _ in sys.stdin)`, `stdin-lines-count`), with multiple `for`
clauses and `if` filters (`nested-comprehension`).

**f-string format specs** — `:010d`, `:.2f`, `:x`, `:.0%`, `:>5`, `:<5`,
`:.1%`, and an arbitrary expression inside the braces
(`f"{prefix}-{name}-{n}"`). `fstring-format-spec` comes straight from
`make_fixtures.py`'s PDF xref writer.

### 3.2 Builtins

Counted as "entries whose program calls it".

| builtin | entries | anchor |
|---|---|---|
| `print` (with `sep=`, `end=`, `file=`, `flush=`) | 132 | `print-sep-end`, `print-to-stderr` |
| `open` (text + `rb`/`wb`/`a`, `encoding=`) | 16 | `file-*` |
| `len` | 13 | `stdin-empty-guard` |
| `sorted` (`key=`, `reverse=`) | 10 | `sorted-key-lambda` |
| `int`, `str`, `float`, `bool` | 11 | `stdin-sum-column`, `repr-vs-str` |
| `sum`, `min`, `max`, `any`, `all` | 9 | `sum-min-max-any-all` |
| `range`, `enumerate` (with `start`), `zip` | 8 | `zip-enumerate`, `file-readlines-enumerate` |
| `list`, `dict`, `set`, `tuple`, `bytes` | 7 | `setcomp-ops`, `collections-defaultdict` |
| `round`, `abs` | 3 | `arith-mixed` |
| `isinstance`, `type(x).__name__`, `repr` | 3 | `except-generic-message` |
| `next`, `map`, `filter` | 3 | `genexp-lazy`, `map-filter-lambda` |
| `sorted(d, key=d.get)` — bound methods as first-class values | 1 | `sorted-dict-by-value` |

Also Tier 0 because their absence breaks the above: `str` methods
(`strip/rstrip/lstrip`, `split` with `maxsplit`, `splitlines`, `join`,
`replace`, `upper`, `lower`, `startswith`, `endswith`, `count`, `find`,
`partition`, `zfill`, `ljust`, `rjust`, `encode`, `format`, `%`),
`list` methods (`append`, `insert`, `remove`, `pop`, `index`, `extend`),
`dict` methods (`get`, `items`, `keys`, `values`, `update`, `setdefault`,
`in`), `set` methods and `& | -`, `bytes.decode` / `.hex()`.

### 3.3 Stdlib

| module | entries | required surface |
|---|---|---|
| `sys` | 31 | `argv`, `stdin` (iteration, `.read`, `.readlines`, `.buffer.read`), `stdout` (`.write`, `.flush`), `stderr`, `exit`, `version_info` |
| `re` | 13 | `search`, `match`, `findall`, `sub` (incl. `count=` and a callable repl), `split`, `compile`, `escape`, match objects (`group`, `group(n)`, named groups, `groups`), flags `I`, `M` |
| `json` | 12 | `loads`, `dumps` (`indent`, `sort_keys`, `separators`, `ensure_ascii`), `load`, `dump`; `JSONDecodeError` subclassing `ValueError` |
| `os` | 9 | `path.join/basename/dirname/splitext/exists/isdir/getsize`, `environ` (`.get`, `in`), `listdir`, `makedirs(exist_ok=)`, `remove`, `rename`, `stat().st_size`, `walk` |
| `hashlib` | 4 | `sha256`, `sha1`, `md5`, `.update`, `.hexdigest` |
| `collections` | 3 | `Counter` (`most_common`, indexing, `values`), `defaultdict` |
| `base64` | 2 | `b64encode`, `b64decode`, `urlsafe_b64encode` |
| `math` | 1 | `sqrt`, `floor`, `ceil`, `pi`, `log10` — the "standard math via python3" line in `src/prompts.js` |

### 3.4 Frequency table (build order)

Entries whose program contains the construct, counted mechanically over
`seed-corpus.jsonl` (139 programs). The regexes are conservative: multi-clause
comprehensions and conditional expressions are undercounted, never
overcounted. Build top-down.

| construct | entries | tier |
|---|---|---|
| `print()` | 132 | 0 |
| `import` / `from … import` | 93 | 0 |
| assignment (incl. multiple targets) | 54 | 0 |
| `for … in` | 31 | 0 |
| keyword arguments at a call site | 29 | 0 |
| `in` / `not in` | 29 | 0 |
| `str` literals + methods | 28 | 0 |
| `if` / `elif` / `else` | 11 | 0 |
| `with … as` | 8 | 0 |
| `def` … `return` | 7 | 0 |
| tuple / star unpacking | 7 | 0 |
| f-strings | 6 | 0 |
| `try` / `except` / `finally` | 5 | 0 |
| `lambda` | 5 | 0 |
| augmented assignment | 4 | 0 |
| generator expression as a call argument | 4 | 0 |
| slicing | 4 | 0 |
| list comprehension | 3 | 0 |
| dict / set comprehension | 3 | 0 |
| conditional expression (`a if c else b`) | 3 | 0 |
| decorator `@f` | 3 | 2 |
| `*args` / `**kwargs` | 2 | 0 |
| `while` / `break` / `continue` | 2 | 0 |
| `raise` | 2 | 0 |
| `class` | 2 | 1 |
| `yield` | 2 | 1 |
| `%` formatting | 2 | 0 |
| chained comparison | 2 | 0 |
| `.format()` | 1 | 0 |
| `is` / `is not` | 1 | 0 |

| module | entries | tier |
|---|---|---|
| `sys` | 31 | 0 |
| `re` | 13 | 0 |
| `json` | 12 | 0 |
| `os` | 9 | 0 |
| `hashlib` | 4 | 0 |
| `datetime` | 4 | 1 |
| `collections` | 3 | 0 |
| `csv` | 3 | 1 |
| `base64` | 2 | 0 |
| `textwrap`, `urllib.parse`, `random` | 2 each | 1 |
| `glob`, `shutil`, `tempfile`, `pathlib`, `binascii`, `time`, `math`, `statistics`, `struct` | 1 each | `math` is 0; the rest 1 |
| `dataclasses`, `contextlib`, `zlib` | 1 each | 2 |
| `subprocess`, `argparse`, `threading`, `http.server`, `socketserver`, `PIL` | 1 each | out (§5) |

46 of the 139 programs import nothing at all — pure syntax and builtins — and
21 more import only `sys`. §3.1 + §3.2 + `sys` covers **67/139 (48%)** before
any other module exists; that is the first milestone. All of Tier 0 (`sys`,
`re`, `json`, `os`, `hashlib`, `collections`, `base64`, `math`) covers
**110/139 (79%)**.

## 4. Tier 1 and Tier 2

**Tier 1 — the second build wave.** Each has real corpus pull but none is on
the critical path of a stdin filter.

| item | entries | why not Tier 0 |
|---|---|---|
| `csv` (`reader`, `writer`, `DictReader`) | 3 | the `analyze.py data.csv` shape is prompted (`docs/SANDBOX-HOST-COMMANDS.md:638`) but no repo command runs it yet |
| `datetime` (`datetime`, `date`, `timedelta`, `strftime`, `fromisoformat`) | 4 | needed for log work; a large surface for a small win |
| `urllib.parse` (`quote`, `unquote`, `urlencode`, `urlparse`, `parse_qs`) | 2 | the sandbox is offline; URLs are only *built* here for a later step |
| `textwrap` (`fill`, `dedent`, `shorten`) | 2 | output shaping only |
| `glob`, `shutil.copy`, `tempfile.NamedTemporaryFile` | 3 | the shell does these more cheaply |
| `pathlib.Path` (`read_text`, `write_text`, `exists`, `suffix`) | 1 | pure sugar over Tier 0 `os`/`open` |
| `struct` (`pack`, `unpack`), `binascii.hexlify`, `int.from_bytes` | 2 | binary work; `make_fixtures.py` needs it, agents rarely do |
| `statistics.mean/median`, `time.time` | 2 | trivially implementable, rarely load-bearing |
| classes: `class`, `__init__`, methods, `self` | 2 | 2/139 entries, and both are script-shaped, not one-liner-shaped |
| generator functions (`yield`) | 2 | `genexp` + `next` already covers the observed need |
| `random` (`random`, `randint`, `choice`, `shuffle`) | 2 | see the exactness carve-out in §6 |

**Tier 2 — only on evidence of use.**

| item | entries | reason to defer |
|---|---|---|
| decorators (`@f`) | 3 | two of the three are `@dataclass` / `@contextmanager`, which are Tier 2 for other reasons; only `decorator-plain` is a bare user decorator, and it needs closures + nested `def` |
| `dataclasses` | 1 | needs decorators + annotations + generated `__repr__` |
| `contextlib.contextmanager` | 1 | needs generators *and* the context-manager protocol |
| `zlib`, `zipfile` | 1 | real compression is a large, testable-only-by-bytes surface |
| user-defined context managers, operator overloading, inheritance, properties | 0 | no corpus entry |
| `collections.deque/OrderedDict/namedtuple`, `itertools`, `functools` | 0 | add the specific function when a corpus entry demands it |

## 5. Explicitly out of scope

Each decision is tied to corpus frequency and to the sandbox cost model.

| feature | corpus | decision and reason |
|---|---|---|
| `subprocess`, `os.system`, `multiprocessing` | 1 + 1 + 0 entries, **0 repo occurrences** | **Out.** The caller already *is* a shell: a python program that shells out pays the interpreter's cost and then the 6.5 ms-per-spawn plus cold-ELF cost (`docs/SANDBOX-PERFORMANCE.md` §3) for something the agent could have written as the next line of its own bash block. lypning-mp exits **90** with `lypning-mp: unsupported: module: subprocess`, which is a clear instruction to the agent to hoist the command into the shell. It does **not** silently shell out — a fake `subprocess` that works would keep the expensive pattern alive. |
| `argparse` | 1 | **Out.** Class-heavy, and `sys.argv` covers every observed need (`argv-*`). Exit 90 `module: argparse`. |
| `threading`, `asyncio`, `async`/`await` | 1, 0 | **Out.** lypning-mp is single-threaded on one WASM CPU; concurrency buys nothing and no entry needs it. |
| `http.server`, `socket`, any network | 1 (repo!) | **Out.** `python3 -m http.server 8123` is real in `docs/TESTING.md`, but that runs on a developer's laptop, not in the VM — and the VM has no usable network (`src/prompts.js:423`: "treat the sandbox as OFFLINE"). Exit 90 `module: http.server`. |
| `pip`, third-party imports (`PIL`, `numpy`, …) | 2 (both repo) | **Out.** No package installation, no C extensions. `import PIL` must fail with CPython's own `ModuleNotFoundError`, not exit 90 — see the carve-out in §7. |
| `eval`, `exec`, `compile`, `globals()`, `__import__` | 0 | **Out.** No corpus pull, and they turn every subset guarantee into a runtime question. |
| metaclasses, `__slots__`, descriptors, MRO games | 0 | **Out.** |
| `input()` | 0 | **Out.** The loop is non-interactive; `sys.stdin.read()` is the observed idiom. |
| REPL / `-i` | 0 | **Out.** |

## 6. Semantics that must be exact

The one outcome we cannot tolerate is **silent semantic divergence from
CPython**: a program that runs, prints something plausible, exits 0, and is
wrong. Everything in this table has a corpus entry that would catch a
divergence.

| semantic | required behaviour | anchor entry |
|---|---|---|
| `int` | arbitrary precision; `2 ** 100` prints in full | `bigint-arith` |
| `/` vs `//` vs `%` | `/` always float; `//` floors toward −∞ (`-7 // 2 == -4`); `%` takes the divisor's sign | `arith-mixed` |
| `float` repr | CPython's shortest round-trip: `0.1 + 0.2` → `0.30000000000000004`, `1e22` → `1e+22`, `100.0/4` → `25.0` | `float-repr` |
| `round` | banker's rounding, and `round(2.675, 2)` → `2.67` (the float, not the decimal, answer) | `arith-mixed` |
| `str` | unicode; `len("åäö") == 3`, `len("åäö".encode()) == 6` | `file-encoding-utf8`, `str-translate-encode` |
| `bytes` repr | `b'caf\xc3\xa9'` — printable ASCII literal, `\xNN` otherwise | `str-translate-encode`, `struct-pack` |
| `dict` | insertion-ordered (**implemented, with an asymptotic cost — see below**); repr `{'a': 1, 'b': 2}` with the exact spacing | `dictcomp`, `dict-items-loop` |
| `sorted` | **stable** | `sorted-stability` |
| container repr | `print(list)` shows element `repr`s: `[1, 'b']`, tuples as `('k', '=', 'v=w')`, `None`/`True` bare | `repr-vs-str`, `str-partition-splitlines` |
| exception messages | verbatim: `invalid literal for int() with base 10: 'abc'`, `KeyError` printing as `'missing'` | `except-generic-message`, `json-keyerror-guard` |
| exception hierarchy | `JSONDecodeError` ⊂ `ValueError`; `FileNotFoundError` ⊂ `OSError` with `.errno == 2` | `json-bad-input-exit`, `file-missing-raises` |
| `json.dumps` defaults | `ensure_ascii=True` (so `"åäö"` → `å…`), separators `", "` / `": "`, `indent` emits no trailing spaces | `json-dumps-unicode`, `stdin-json-pretty` |
| `csv.writer` | terminates rows with `\r\n` and doubles embedded quotes | `csv-writer-quoting` |
| text I/O | universal newlines on read; `\n` written as `\n` (no translation); `sys.stdout.write` adds nothing | `stdin-grep-substring` |
| `re` | leftmost, non-POSIX-longest, backtracking; `match` anchors at 0 while `search` does not; `\d \w \s` are **unicode-aware** by default | `re-match-vs-search`, `stdin-regex-extract` |
| exit codes | clean end → 0; `sys.exit(n)` → n; uncaught exception → 1 with a traceback on stderr and nothing extra on stdout | `stdin-exit-code-nonzero`, `uncaught-exception-traceback` |
| iteration over a file/stdin | yields lines *with* their `\n`, lazily (so `break` after 2 lines does not read the rest) | `stdin-head-n` |

### Dict ordering: exactness and cost

CPython has guaranteed `dict` insertion order since 3.7. MicroPython's dict is
an open-addressing hash map that does not preserve it, so `print(d)`,
`d.items()`, `json.dumps(d)` and every `Counter`/`defaultdict` display came out
reordered — plausible, different, exit 0.

**Shipped as exact** (owner decision, 2026-08-13): one line in
`mp_obj_new_dict()` points the Python-level dict at the ordered map that was
already compiled in for `collections.OrderedDict`. Zero bytes, and it took the
conformance MISMATCH count to 0.

The cost is asymptotic, because that ordered map is a linear array. Measured on
the shipped binary, native host, inserting N distinct string keys: 1,000 →
11 ms, 5,000 → 193 ms, 10,000 → 768 ms, 20,000 → 3,005 ms — clean quadratic,
and 40,000 raises `MemoryError` before it can get worse. Two consequences worth
holding: the heap cap makes a pathological dict fail LOUDLY (traceback, exit 1)
rather than hang into the 30 s ceiling that destroys the VM; and these are host
numbers, so the guest is nearer that ceiling than the table suggests. A
large-dict case belongs in the live `tests/e2e/sandbox-perf.spec.js` run that
§5 of `docs/MICROPYTHON.md` already stages before `PKGS_COMMON` changes.

The permanent fix is CPython's compact-dict layout — ordered array plus hash
index, O(1) *and* ordered — which is a VM change, not a variant change.

### Permitted approximations, and their risk

| cheat | risk |
|---|---|
| `random` need not reproduce CPython's MT19937 stream | A seeded program prints different numbers. **Contained:** `random-seeded` deliberately carries a blank `expect_stdout`, and the conformance runner must treat `random` output as non-deterministic. If an agent ever seeds and compares, this becomes a MISMATCH we chose. |
| `set` iteration/repr order may differ | `print({1,2,3})` diverges. Corpus practice already sorts before printing (`setcomp-ops`). Risk is a program that prints a set directly — real, and cheap to hit. |
| Traceback body: emit `Traceback (most recent call last):`, one frame line, then the exact final `Type: message` line | A script grepping intermediate frames or line numbers breaks. Low: agents grep the last line. |
| `id()`, default `object.__repr__` addresses, `sys.version` string | Anything printing an address diverges. Acceptable; `--version` must say lypning-mp anyway. |
| Recursion limit and `RecursionError` wording | `sys.setrecursionlimit` unsupported; deep recursion may fail differently. `recursion-small` (fact(10)) is the observed depth. |
| Float formatting past 17 significant digits, `float.hex()`, `decimal`/`fractions` | Numerics beyond the corpus. Anyone doing real numerics should be told to use the server-side container. |
| Locale: assume `C.UTF-8` always | `locale`-sensitive `strftime`/sorting differs. No corpus entry sets a locale. |
| `os.walk` ordering | Sort before printing, as `os-listdir-sorted` does. |

## 7. Failure modes: the unsupported contract

When a program uses something lypning-mp does not implement:

```
exit code 90
stderr, one line, nothing else:

lypning-mp: unsupported: <kind>: <detail>
```

`<kind>` is exactly one of `syntax`, `builtin`, `module`, `attribute`,
`argument`. `<detail>` names the precise thing:

```
lypning-mp: unsupported: module: subprocess
lypning-mp: unsupported: builtin: input
lypning-mp: unsupported: syntax: decorator
lypning-mp: unsupported: attribute: str.casefold
lypning-mp: unsupported: argument: keyword strict
```

90 is clear of 0/1/2 (CPython's own), of 126/127 (shell "cannot execute" /
"not found"), and of 128+n (signals), so a caller can branch on it
unambiguously and retry with real `python3` — or rewrite the line as awk.

Rules that make the code branchable:

1. **Never exit 90 for a program lypning-mp can run.** A supported program's exit
   code is CPython's.
2. **Never exit 1 for a program lypning-mp merely does not support.** Exit 1 means
   "this program ran and raised", nothing else.
3. **Detect early where possible.** Parse the whole program first; if an
   unsupported *syntactic* form appears anywhere, exit 90 before executing a
   statement, so the program has no partial side effects. Unsupported
   attributes and modules can only be caught at their use site — partial
   stdout before the 90 is then expected and allowed.
4. **`ImportError` vs 90.** A module CPython itself would not find in this
   image (`PIL`, `numpy`) raises `ModuleNotFoundError` and exits **1**, exactly
   as CPython does — that is a program that ran correctly. Exit 90 is reserved
   for modules that *exist in CPython* but not in lypning-mp (`subprocess`,
   `argparse`, `zlib`).
5. **Nothing else on stderr in the 90 case.** No traceback, no banner, no
   suggestion text. One greppable line.

The conformance runner (`lypning conformance`) diffs lypning-mp against
real CPython and recognises exactly three outcomes:

| outcome | definition | verdict |
|---|---|---|
| **MATCH** | stdout and exit code identical to CPython | pass |
| **UNSUPPORTED** | exit 90 with the one-line stderr form | coverage not yet reached — **not a failure** |
| **MISMATCH** | anything else | hard failure |

stderr is deliberately **not** compared byte-for-byte. CPython's tracebacks
carry file paths, line numbers and interpreter internals that a subset runtime
has no business reproducing, and requiring them would fail every error case for
no benefit — what a pipeline and an agent loop actually consume is stdout and
the exit code. The one stderr rule that is enforced: if CPython reported an
error and lypning-mp was silent, that is a MISMATCH. A program that fails under
CPython must fail under lypning-mp.

Two entries in the corpus (`datetime-now`, `random-seeded`) are tagged
`nondeterministic` / `seeded` and compared on exit code alone — a wall clock
and a PRNG stream differ between two runs of the *same* interpreter, so
demanding a stdout match would fail them permanently and bury the real signal.

Silent semantic divergence — a program that runs to completion and prints the
wrong thing — is the failure this whole contract exists to make impossible to
miss.

## 8. Corpus entries a builtin subcommand covers outright

Some common invocations are not really "run this Python"; they are a named
transformation. Implementing them as lypning-mp subcommands covers those entries
with zero language surface, and they should be built **first** — they are the
cheapest coverage in the whole plan.

| subcommand | replaces | corpus entries covered |
|---|---|---|
| `lypning-mp json.tool` (also reachable as `-m json.tool`) | `python3 -m json.tool` — pretty-print stdin JSON, `--sort-keys`, `--compact` | `stdin-json-pretty`, and the repo's own `curl … \| python3 -m json.tool` |
| `lypning-mp sha256 [file]` / `md5` / `sha1` | `hashlib` one-liners over stdin or a file | `hashlib-sha256-str`, `hashlib-md5-file`, `stdin-bytes-buffer` |
| `lypning-mp b64 [-d]` | `base64` encode/decode over stdin | `base64-encode-decode`, `base64-urlsafe` |
| `lypning-mp --version` | `python3 --version` | `shape-version` |

Two caveats. These are **conveniences, not the contract**: the equivalent
Python program must still work once its Tier is built, because an agent that
writes `import hashlib` is not going to be told to use a subcommand. And the
digest subcommands duplicate `sha256sum`/`md5sum`, which already exist in the
VM — their value is only that lypning-mp is warm when coreutils' binary is cold.

## 9. Performance rules the implementation must respect

From `docs/SANDBOX-PERFORMANCE.md` §1–§4, and non-negotiable in this
environment:

- **Startup is the product.** If lypning-mp's cold start is not far below
  CPython's 8573 ms, it has no reason to exist. That means one small artifact,
  no directory walking at import time, no `.so`, no `site-packages` scan.
- **No process spawns.** 6.5 ms floor each, ~29 ms for a substantial binary.
  §5's `subprocess` decision follows from this, not from taste.
- **Printing megabytes is a trap regardless of correctness.** Output crosses
  the VM→JS boundary at ~1.1 MB/s (2 MB costs ~1.9 s). A program that dumps a
  whole file back is the expensive shape even when it is right — the corpus
  reflects this: `stdin-head-n`, `wc`-style counting, `most_common(3)`.
  lypning-mp should not buffer unbounded output; write through, line by line.
- **The 30 s ceiling destroys the VM.** `execInSandbox` resets CheerpX on
  timeout, ending the agent's turn. An interpreter loop that is 100× slower
  than CPython on a large input converts a working command into a lost VM;
  prefer streaming line iteration over whole-input reads wherever the
  semantics allow.
- **Batching beats round-trips.** Nothing lypning-mp does should encourage one
  invocation per line of data.

## 10. Open questions for the implementation

1. Does the parser reject unsupported syntax up front (rule 3) cheaply enough
   to keep startup flat? If not, syntax 90s become late 90s and rule 3 weakens
   to "before executing that statement".
2. `re` is the largest single module in Tier 0 (13 entries) and there is no way
   to borrow one. lypning-mp is a native ELF running *inside* the guest, so the
   browser's `RegExp` is not reachable from it, and linking an external engine
   (PCRE, Oniguruma) reintroduces exactly the shared-object streaming the whole
   design exists to avoid. So: a small backtracking engine compiled in, scoped
   to the constructs the corpus actually uses. The divergence risks to test
   are `\d`/`\w` unicode semantics, named groups, and `re.M`/`re.S`, each of
   which has a corpus entry (`re-multiline-anchors`, `re-groups-named`).
3. Float→string: shortest round-trip printing must be implemented, not
   approximated. `float-repr` is the test.
4. `int`: which bignum representation, given that `2 ** 100` must print
   exactly.
5. Is script mode (`analyze.py`) or `-c` mode first? The corpus says `-c` and
   stdin filters dominate; the repo's only real python file is a script.
