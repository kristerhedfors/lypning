# The subset your program should stay in

The interpreter that will run your program executes a subset of Python 3 in-process, fast. Anything outside the subset it refuses, with one line `unsupported: <kind>: <detail>`, and the program is then re-run unchanged by full Python, which costs a process spawn. Both give the same output. Correctness outranks the tier. A fall-back to full Python is free and legitimate when the task needs it. Stay inside when you can do so with an ordinary, correct program; never bend the answer to stay inside.

## What it runs

- Syntax: literals, operators with CPython precedence, chained comparison, slicing with a step, calls with `*args`/`**kwargs`, assignment and star unpacking, slice assignment, `global`, augmented assignment, `if`/`for`/`while`, `def` with defaults and closures, `lambda`, imports, `with open(...)`, `try`/`except`/`finally`, `raise`, `assert`, comprehensions, generator expressions, f-strings with format specs.
- Builtins: `abs` `all` `any` `bin` `bool` `bytes` `chr` `dict` `divmod` `enumerate` `filter` `float` `format` `hex` `input` `int` `isinstance` `iter` `len` `list` `map` `max` `min` `next` `oct` `open` `ord` `print` `range` `repr` `reversed` `round` `set` `sorted` `str` `sum` `tuple` `type` `zip`.
- Modules: `sys`, `os`, `os.path`, `io`, `json`, `math` (the functions named under `math` below, plus the constants), `posixpath`, `random` (seeded only: `seed(int)` first), `cap-collections`, `collections` (`Counter` and `defaultdict`), `cap-pathlib`, `pathlib` (`Path`), `cap-re`, `re` (a slice of the pattern language, named under `re` below), `cap-csv`, `csv` (`DictReader` `QUOTE_ALL` `QUOTE_MINIMAL` `QUOTE_NONE` `QUOTE_NONNUMERIC` `reader`), `cap-glob`, `glob` (`escape` `glob` `has_magic` `iglob`), `cap-base64`, `base64` (`b64decode` `b64encode` `urlsafe_b64decode` `urlsafe_b64encode`), `cap-hashlib`, `hashlib` (`md5` `sha1` `sha256` `sha512`), `cap-statistics`, `statistics` (`mean` `median` `median_high` `median_low`), `cap-itertools`, `itertools` (`combinations` `product`), `cap-difflib`, `difflib` (), `cap-textwrap`, `textwrap` (`dedent` `fill` `indent` `shorten` `wrap`), `cap-time`, `time` (`gmtime` `monotonic` `monotonic_ns` `perf_counter` `perf_counter_ns` `sleep` `strftime` `time` `time_ns`), `cap-binascii`, `binascii` (`a2b_base64` `a2b_hex` `b2a_base64` `b2a_hex` `hexlify` `unhexlify`), `cap-ast`, `ast` (`literal_eval`), `struct`, `unicodedata`.
- Values: `int` exact past 64 bits, `float` with CPython's repr, `str`, `bytes`, `list`, `tuple`, `dict` (insertion-ordered), `set` (never show its order), `None`, `bool`; CPython's exception classes and messages; text files, `sys.stdin`, `sys.argv`, `sys.exit`.

## What it refuses, and how to stay inside

One line per refusal kind: what fires it, then the way to stay inside.

### Syntax, refused before the program starts

- `annotation` — an annotation the engine cannot carry: a module-level annotation before Python 3.14 (evaluated there), `__annotations__`, an annotated name also declared `global`. Stay inside: drop the annotation: write `x = value`.
- `async` — `async def`, `await`, `async for`. Stay inside: write synchronous code. No build of this interpreter has it.
- `augassign` — augmented assignment to a slice (`xs[1:3] += …`). Stay inside: assign the slice with `=` or rebuild the list.
- `class` — `class` statements. Stay inside: use functions with dicts and tuples.
- `class-subscript` — a class subscript such as `list[int]`. Stay inside: no type parameters at runtime.
- `complex` — complex literals such as `1j`. Stay inside: no complex numbers.
- `decorator` — `@decorator` on a `class`, on `async def` or before anything but a `def` (a decorated `def` is served). Stay inside: decorate plain `def`s only.
- `ellipsis` — `...`. Stay inside: use `pass`.
- `escape` — `\N{…}` named escapes and `\u` escapes naming a lone surrogate. Stay inside: write the character itself or its `\uXXXX` code.
- `except` — an `except` clause other than a name or a flat parenthesised tuple of names (`except ((A, B), C)`, `except A, B`). Stay inside: `except (A, B, C):`.
- `except-star` — `except*`. Stay inside: use `except`.
- `fstring` — `!a` conversions and self-documenting `{x=}` fields. Stay inside: `{x!r}`, `{x!s}`, or format the value explicitly.
- `future` — a `from __future__` import outside the served ones, an alias, or `__future__` anywhere but the top. Stay inside: put `from __future__ import annotations` first, or leave it out.
- `generator` — `yield`, and so every generator function. Stay inside: build and return a list; generator expressions are fine.
- `global` — a function made inside a nested function that declares `global`. Stay inside: declare `global` only in top-level functions.
- `import` — relative and star imports. Stay inside: `import m` or `from m import name`.
- `indent` — an indented first line. Stay inside: start the program in column 0.
- `kwonly` — a keyword-only parameter list CPython rejects (`def f(*)`, `def f(*, a, /)`); `def f(*, a)` itself is served. Stay inside: write keyword-only parameters after one `*`, each a name.
- `nonlocal` — `nonlocal`. Stay inside: return the value, or keep state in a dict or list.
- `slice-assign` — extended slice assignment, `xs[::2] = …`. Stay inside: assign with a loop or rebuild the list.
- `subscript` — a tuple subscript containing a slice, `x[0:1, 2]`. Stay inside: index with one value or one slice.
- `token` — a byte the lexer cannot read: non-UTF-8 source, a stray character. Stay inside: valid UTF-8 source using only Python's own punctuation.
- `unpack` — `*` inside a list, set or parenthesised display. Stay inside: concatenate with `+` or `extend`.
- `walrus` — `:=`. Stay inside: assign on its own line.

### Modules, attributes and builtins

- `argument` — a keyword a builtin does not take here (e.g. `zip(strict=…)`). Stay inside: call builtins with their plain positional forms.
- `ast` — an `ast` shape outside `ast.literal_eval(str)`: `ast.parse`, `walk`, `dump`, the node classes, or text `literal_eval` would reject. Stay inside: `ast.literal_eval(s)` on a plain literal such as a repr of a dict or list.
- `base64` — a base64 call whose argument or keyword the router could not read. Stay inside: `b64encode(bytes)` / `b64decode(bytes)` with no keywords.
- `binascii` — a `binascii` call outside `hexlify unhexlify b2a_hex a2b_hex a2b_base64 b2a_base64`, input that would raise, or `bytes.fromhex` of anything but one well-formed str. Stay inside: those functions on well-formed input; `bytes.fromhex(s)` and `b.hex()`.
- `builtin` — a builtin outside the served set, `iter(callable, sentinel)`, `open()` of a descriptor. Stay inside: use the served builtins listed above.
- `collections` — an operator over a `Counter`, a non-int count, a non-builtin `default_factory`, `deque`. Stay inside: `Counter(iterable)`, `.most_common(n)`, `.update(iterable)`, `defaultdict(int|list|set|dict|str)`; a list instead of `deque`.
- `csv` — a csv shape the readers do not serve: dialects, unserved keywords, `csv.Error` text. Stay inside: `csv.reader(f)` / `csv.DictReader(f)` over a file opened with `newline=''`; write CSV with `','.join`.
- `glob` — a glob shape outside `glob(pattern, recursive=…)` / `iglob` / `escape` / `has_magic`. Stay inside: `sorted(glob.glob(pattern))`, with at most `recursive=True`.
- `hashlib` — a hashlib shape outside `md5 sha1 sha256 sha512` over bytes: `new()`, sha3, blake2, KDFs, a str argument. Stay inside: `hashlib.sha256(text.encode()).hexdigest()`.
- `itertools` — an `itertools` shape the engine does not serve. Stay inside: `chain`, `islice`, `product`, `permutations`, `combinations`, `groupby`, `accumulate` on lists.
- `module` — `import` of any module outside the served list (`itertools`, `functools`, `string`, `textwrap`, `datetime`, `time`, `argparse`, `subprocess`, …). Stay inside: stay with the served modules and write the small helper by hand.
- `module-attr` — a name of a partially served module outside its served names. Stay inside: use only the served names listed above.
- `pathlib` — a `Path` shape not served: `PurePath`, `.stat()`, `.iterdir()` order, a `ValueError` text. Stay inside: `Path(p).read_text()`, `.write_text()`, `.exists()`, `.name`, `.suffix`, `.stem`, `.parent`, `/` joins.
- `re` — a regex construct outside the served slice: lookaround, backreferences, bytes patterns, Unicode `\w \d \s`, non-ASCII group names, case folding, a step budget. Stay inside: ASCII patterns with classes, groups, alternation, quantifiers and anchors, through `search match findall sub split compile`.
- `statistics` — a `statistics` shape the engine does not serve. Stay inside: `mean`, `median`, `mode`, `stdev`, `pstdev`, `variance` on lists of numbers.
- `struct` — a `struct` call outside `pack unpack unpack_from calcsize`, a code outside `x c b B ? h H i I l L q Q s d`, or input that would raise `struct.error`. Stay inside: an explicit byte order (`'<'`, `'>'`) and in-range values.
- `textwrap` — a `textwrap` shape the engine does not serve, such as `TextWrapper`. Stay inside: `textwrap.wrap`, `fill`, `dedent`, `indent`, `shorten` with plain arguments.
- `time` — a `time` shape the engine does not serve: `gmtime`, `strftime`, a `sleep` in a loop or longer than a second, a time function used as a value. Stay inside: call `time.time()` or `time.perf_counter()` directly.
- `unicodedata` — a `unicodedata` call outside `category combining decomposition normalize is_normalized` of one plain str (and the form), a lone surrogate from `chr()`, or a Unicode property of non-ASCII text in a program that imports it. Stay inside: call `unicodedata.normalize`, `category`, `combining` or `decomposition` with positional str arguments.

### Operations on values

- `aug-assign` — `d |= x` where `d` is a dict and `x` is not one. Stay inside: `d.update(x)`.
- `bigint` — an integer shape this build cannot carry exactly (most arithmetic past 64 bits is served). Stay inside: keep integers within 64 bits where the task allows.
- `bytes-method` — a bytes method outside `decode hex lower upper find replace join`. Stay inside: decode to `str` and use the str methods.
- `call` — a keyword given twice through `**`, `**` over something that is not a dict, `*` over a non-iterable, or any call whose arguments do not bind (wrong arity, an unknown or repeated keyword, a positional-only name by keyword). Stay inside: pass each keyword once, `**` only a dict, and call functions with the arguments they take.
- `class-union` — a `|` union of classes (`int | None`). Stay inside: no runtime type unions.
- `dict-method` — a dict method outside `get keys values items setdefault pop popitem update copy clear`. Stay inside: use those.
- `dunder-attr` — reading a data-model dunder such as `.__dict__` or `.__class__`. Stay inside: do not introspect objects.
- `exception` — an exception the engine cannot carry: an unknown class, `SystemExit` with several arguments, `KeyError.args`, `sys.exit(kw=…)`. Stay inside: raise and catch the builtin classes with one string argument; `sys.exit(int)`.
- `exception-note` — an error CPython annotates with a note, such as a dict update element that is not a pair. Stay inside: pass pairs to `dict()` and `.update()`.
- `float-sum` — `sum()` over floats, which CPython versions round differently. Stay inside: accumulate with a loop: `total = 0.0; for x in xs: total += x`.
- `format` — a format spec outside the common ones: `'n'`, a spec on a type CPython rejects, an int code on a float or the reverse. Stay inside: `{:d} {:.2f} {:>8} {:<8} {:^8} {:,} {:x} {:b} {:e} {:%}` and friends on the matching type.
- `int-div-precision` — `int / int` past 2**53 in a build that cannot round it exactly. Stay inside: use `//` and `%` for exact integer work.
- `int-method` — `to_bytes`/`from_bytes` outside `(length, 'big'|'little', signed=bool)` within 8 bytes, `bool.from_bytes`. Stay inside: `n.to_bytes(length, 'big')` with an explicit byteorder.
- `isinstance` — `isinstance(x, type)`. Stay inside: `isinstance` against `int str float bool list dict tuple set bytes`.
- `iterator-identity` — an iterator, function or file as a dict or set key. Stay inside: key on strings, numbers and tuples.
- `list-method` — a list method outside `append extend insert pop remove index count reverse clear copy sort`. Stay inside: use those.
- `method` — a method the value's type does not have, or one called on the wrong type. Stay inside: call the methods that belong to the value's type.
- `name-error` — a `NameError` in a nested scope, whose message depends on assignments elsewhere. Stay inside: define names before use; do not rely on the text of a NameError.
- `repr` — `repr()` of a function, module, file, generator or `os.environ`, whose text carries an address. Stay inside: print values, not objects.
- `round` — `round()` outside `round(x)` / `round(x, n)` on ordinary floats. Stay inside: `round(x, n)` on floats within 2**53.
- `setattr` — assignment to an attribute of an object. Stay inside: keep state in dicts, lists and locals.
- `slice-key` — a slice as a dict key. Stay inside: key on tuples.
- `str-method` — a str method outside the served set, or one over a code point it cannot fold. Stay inside: `split join strip replace upper lower startswith endswith find count format isdigit zfill center ljust rjust partition splitlines encode`.
- `tuple-method` — a tuple method outside `index` and `count`. Stay inside: convert with `list()`.
- `type` — `type()` with three arguments, or `type()` of a value the engine cannot name. Stay inside: `isinstance` for checks; never build classes with `type()`.
- `type-attr` — an attribute of a type object such as `int.mro`. Stay inside: do not introspect types.

### Files, environment and limits

- `alloc` — a sequence past this runtime's allocation ceiling. Stay inside: keep lists and strings small; a huge repetition needs full Python.
- `env` — an interpreter environment variable this build cannot honour. Stay inside: nothing in the program; the host decides.
- `environ` — `os.environ` as a whole under a coerced C locale. Stay inside: read one variable with `os.environ.get('NAME')`; never print the mapping.
- `file-method` — a file method outside `read readline readlines write writelines close tell seek` and iteration. Stay inside: read the file whole with `.read()` or iterate its lines.
- `file-read` — `read(n)` across a `\r` under universal newlines. Stay inside: read whole files or iterate lines.
- `file-seek` — `seek()` with a `whence` other than 0. Stay inside: read the file whole instead of seeking.
- `file-tell` — `tell()` after iterating, or beside a bare `\r`. Stay inside: do not use `tell()`.
- `mkdir` — `os.mkdir`/`makedirs` with `mode=`, or over a path this run removed. Stay inside: `os.makedirs(path, exist_ok=True)` with no mode.
- `open-mode` — an `open()` mode outside `r`, `w`, `a` and their `b` forms. Stay inside: `open(path)`, `open(path, 'w')`, `'a'`, `'rb'`, `'wb'`.
- `open-newline` — `open(newline=…)` that translates line endings. Stay inside: `open(path)` or `open(path, newline='')`.
- `open-special` — reading something that is not a regular file: a device, a FIFO, a directory. Stay inside: read regular files; standard input is `sys.stdin`.
- `os-listdir` — `os.listdir()`, whose order is the filesystem's. Stay inside: `sorted(glob.glob(os.path.join(path, '*')))`.
- `output` — captured output past the host's byte limit. Stay inside: print less; a program printing megabytes needs full Python.
- `print-file` — `print(file=…)` to anything but `sys.stdout`/`sys.stderr`. Stay inside: print to stdout, or `.write()` to the file.
- `recursion` — nesting or call depth past the engine's limits, or a very long operator chain. Stay inside: iterate instead of recursing deeply; split long expressions.
- `remove` — `os.remove()` of a directory. Stay inside: `os.rmdir()` for an empty directory.
- `rename` — `os.rename()`/`os.replace()` of a directory, a link, or a file that existed before the program ran. Stay inside: rename files the program itself wrote, or write the new file and remove the old one.
- `rmdir` — `os.rmdir()` of a directory that still holds a file the program wrote or removed. Stay inside: remove the files first, then the directory, or leave the directory in place.
- `sandbox` — a filesystem access the host denied. Stay inside: nothing in the program; read stdin and print stdout.
- `steps` — still running after the step limit. Stay inside: keep the work bounded; a long computation needs full Python.
- `with` — `with` over a value that is not a file. Stay inside: use `with` only around `open()`.

### Behaviours only full Python gets right

These go straight to full Python. A rewrite is the only way to stay inside, and only when the task allows it.

- `del` — `del` of a slice or attribute (`del xs[1:3]`, `del o.x`). Stay inside: `del` a name, a subscript or a tuple of them, or rebuild the list.
- `dict-view` — set algebra, comparison or `is` over `.keys()`/`.values()`/`.items()` views. Stay inside: wrap the view in `list()` or `set()` first.
- `dunder-missing` — `getattr(x, '__doc__', default)`-style reads of a dunder the type lacks. Stay inside: do not introspect objects.
- `encoding` — a codec other than UTF-8 on `encode`, `decode` or `open`. Stay inside: stay with UTF-8, the default.
- `exception-chaining` — `.__context__`, `.__cause__`, `raise … from …`. Stay inside: handle exceptions without chaining.
- `glob-order` — a `glob()` result whose order the output would show. Stay inside: always `sorted(glob.glob(…))`.
- `identity` — `is` between two equal immutables that may or may not be one object. Stay inside: compare with `==`; use `is` only against `None`, `True`, `False`.
- `iterator-type-name` — an error message that would name an iterator type (`list_iterator`, …). Stay inside: iterate with `for`; do not raise over iterator objects.
- `json` — `json.dumps`/`loads` with hooks, `default=`, or a keyword outside `indent sort_keys ensure_ascii separators`; a lone surrogate. Stay inside: `json.loads(s)`, `json.dumps(obj, indent=2, sort_keys=True)`.
- `math` — a math domain error, a non-number, a wrong count, and every function outside `ceil copysign fabs factorial floor fmod gcd isfinite isinf isnan isqrt sqrt trunc` and the constants. Stay inside: use those; `log`, `exp` and the trigonometry need full Python.
- `name-hint` — an uncaught error where CPython may add a `Did you mean` suggestion. Stay inside: catch the errors you expect; do not rely on traceback text.
- `nan-identity` — two NaNs in one comparison or containment test. Stay inside: test NaN with `math.isnan`.
- `nan-order` — sorting, `min` or `max` over a NaN. Stay inside: filter NaNs out before ordering.
- `percent-format` — `%` formatting with the `0` flag, grouping, or their interaction with `-`. Stay inside: f-strings or `str.format`.
- `random` — an unseeded draw, `randrange` with a step, a non-int seed, keywords, `choice` over a non-sequence, `getrandbits` past 63. Stay inside: `random.seed(<int>)` first, then `random()`, `randint`, `randrange(a, b)`, `choice(list)`; unseeded randomness needs full Python.
- `repr-unicode` — `repr()` of a string holding characters outside the printable set the engine knows. Stay inside: print non-ASCII text directly, not inside a list or dict and not through `repr`.
- `set-method` — a set method outside `add discard remove clear copy update`. Stay inside: use those and the operators `| & - ^`.
- `set-order` — anything that exposes a set's iteration order: printing a set of more than one element, iterating it into output. Stay inside: `sorted(s)` before printing or iterating a set.
