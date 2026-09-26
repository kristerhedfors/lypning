"""Rebuild `subset-spec.md`, the stage 0b system paragraph, from the sources it describes.

The spec is what `nt eval --system-file training/prompts/subset-spec.md` appends
to the system prompt (`LADDER.md` 0b: is the boundary elicitable from a
description?). Its list of refusal kinds is read off the crate — every
``unsupported("<kind>"`` call site, every ``block("<kind>"`` in the router —
every ``stop("<kind>"`` beside it — and the two tables that decide where a refusal goes
(`engines.ONLY_CPYTHON_REFUSALS`, `route::CPYTHON_ONLY_KINDS`). The builtins
and modules come from `builtins.rs` and `modules.rs`. Only the prose and the
one-line recipe per kind are written here, and a kind that appears in the
source without a recipe, or a recipe whose kind the source no longer spells,
is an error rather than a silent gap: the committed file is held equal to
this script's output by `tests/test_eval2_prompt.py`.

The text never names the interpreter. It is "the interpreter that will run
your program", so that the paragraph describes a boundary and not a product.

    python3 training/prompts/gen_subset_spec.py          # rewrite the file
    python3 training/prompts/gen_subset_spec.py --check  # exit 1 if it drifted
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
RUST = ROOT / "src" / "lypning" / "assets" / "rust" / "src"
SPEC = Path(__file__).resolve().parent / "subset-spec.md"

_UNSUPPORTED_RE = re.compile(r'unsupported\(\s*"([a-z0-9-]+)"')
_STRUCT_RE = re.compile(r'Unsupported\s*\{\s*kind:\s*"([a-z0-9-]+)"')
# `.block(` and not a bare `block(`: `binascii::block(name, …)` is a free
# function whose FIRST argument is a module attribute (`block("hexlify", …)`
# in its tests), not a refusal kind.
_BLOCK_RE = re.compile(r'\.block\(\s*"([a-z0-9-]+)"')
_STOP_RE = re.compile(r'\bstop(?:_only|_base64)?\(\s*"([a-z0-9-]+)"')
_STRING_RE = re.compile(r'"([^"]*)"')
_MODULES_RE = re.compile(r"pub const MODULES: &\[&str\] =\s*&\[(?P<body>.*?)\];", re.S)
_BUILTINS_RE = re.compile(r"pub const BUILTINS: &\[&str\] = &\[(?P<body>.*?)\];", re.S)
_ONLY_CPYTHON_RE = re.compile(
    r"pub const ONLY_CPYTHON_KINDS: &\[&str\] = &\[(?P<body>.*?)\];", re.S)
_CPYTHON_ONLY_RE = re.compile(
    r"const CPYTHON_ONLY_KINDS: &\[&str\] = &\[(?P<body>.*?)\];", re.S)
_MODULE_ATTRS_RE = re.compile(
    r"pub const MODULE_ATTRS: &\[\(&str, &\[&str\]\)\] = &\[(?P<body>.*?)\];", re.S)
_ATTR_ROW_RE = re.compile(
    r'\(\s*"([a-z0-9]+)"\s*,\s*(?:&\[([^\]]*)\]|([A-Z0-9_]+))\s*,?\s*\)', re.S)
_SERVED_CONST_RE = r"pub const %s: &\[&str\] =\s*&\[(?P<body>.*?)\];"


def _strip_comments(text: str) -> str:
    return re.sub(r"//[^\n]*", "", text)


def source_kinds(rust: Path = RUST) -> List[str]:
    """Every refusal kind the crate spells, sorted. Empty if the crate is absent."""
    kinds: set = set()
    for path in sorted(rust.glob("*.rs")):
        text = _strip_comments(path.read_text(encoding="utf-8"))
        for rx in (_UNSUPPORTED_RE, _STRUCT_RE, _BLOCK_RE, _STOP_RE):
            kinds.update(rx.findall(text))
    return sorted(kinds)


def _table(rx: "re.Pattern[str]", text: str) -> List[str]:
    m = rx.search(text)
    return _STRING_RE.findall(_strip_comments(m.group("body"))) if m else []


def served_modules(rust: Path = RUST) -> List[str]:
    """The union of every `MODULES` table in modules.rs: what the largest build serves."""
    text = (rust / "modules.rs").read_text(encoding="utf-8")
    out: List[str] = []
    for m in _MODULES_RE.finditer(text):
        for name in _STRING_RE.findall(_strip_comments(m.group("body"))):
            if name not in out:
                out.append(name)
    return out


def served_builtins(rust: Path = RUST) -> List[str]:
    return sorted(_table(_BUILTINS_RE, (rust / "builtins.rs").read_text(encoding="utf-8")))


def served_attrs(rust: Path = RUST) -> Dict[str, List[str]]:
    """`route.rs:MODULE_ATTRS` plus `GLOB_SERVED`: the partially served modules."""
    text = (rust / "route.rs").read_text(encoding="utf-8")
    out: Dict[str, List[str]] = {}
    m = _MODULE_ATTRS_RE.search(text)
    if m:
        for module, literal, const in _ATTR_ROW_RE.findall(_strip_comments(m.group("body"))):
            if const:
                cm = re.search(_SERVED_CONST_RE % const, text, re.S)
                names = _STRING_RE.findall(cm.group("body")) if cm else []
            else:
                names = _STRING_RE.findall(literal)
            out[module] = sorted(names)
    gm = re.search(_SERVED_CONST_RE % "GLOB_SERVED", text, re.S)
    if gm:
        out["glob"] = sorted(_STRING_RE.findall(gm.group("body")))
    return out


def only_cpython_kinds(rust: Path = RUST) -> List[str]:
    """`engines.ONLY_CPYTHON_REFUSALS` when the package imports, else the crate's table."""
    try:
        from lypning.engines import ONLY_CPYTHON_REFUSALS  # the one copy
        return sorted(ONLY_CPYTHON_REFUSALS)
    except ImportError:
        pass
    return sorted(_table(_ONLY_CPYTHON_RE, (rust / "route.rs").read_text(encoding="utf-8")))


def cpython_only_constructs(rust: Path = RUST) -> List[str]:
    return sorted(_table(_CPYTHON_ONLY_RE, (rust / "route.rs").read_text(encoding="utf-8")))


# ---------------------------------------------------------------- the recipes

SYNTAX = "syntax"
NAMES = "names"
VALUES = "values"
HOST = "host"

SECTIONS: Tuple[Tuple[str, str], ...] = (
    (SYNTAX, "Syntax, refused before the program starts"),
    (NAMES, "Modules, attributes and builtins"),
    (VALUES, "Operations on values"),
    (HOST, "Files, environment and limits"),
)

#: kind -> (section, what fires it, how to stay inside). One line each in the
#: spec. Kinds in `ONLY_CPYTHON_REFUSALS` are listed in their own section
#: whatever this says, because for them the fall-back is the design.
RECIPES: Dict[str, Tuple[str, str, str]] = {
    "alloc": (HOST, "a sequence past this runtime's allocation ceiling",
              "keep lists and strings small; a huge repetition needs full Python"),
    "argument": (NAMES, "a keyword a builtin does not take here (e.g. `zip(strict=…)`)",
                 "call builtins with their plain positional forms"),
    "async": (SYNTAX, "`async def`, `await`, `async for`", "write synchronous code"),
    "augassign": (SYNTAX, "augmented assignment to a slice (`xs[1:3] += …`)",
                  "assign the slice with `=` or rebuild the list"),
    "base64": (NAMES, "a base64 call whose argument or keyword the router could not read",
               "`b64encode(bytes)` / `b64decode(bytes)` with no keywords"),
    "bigint": (VALUES, "an integer shape this build cannot carry exactly (most arithmetic past 64 bits is served)",
               "keep integers within 64 bits where the task allows"),
    "builtin": (NAMES, "a builtin outside the served set, `iter(callable, sentinel)`, `open()` of a descriptor",
                "use the served builtins listed above"),
    "bytes-method": (VALUES, "a bytes method outside `decode hex lower upper find replace join`",
                     "decode to `str` and use the str methods"),
    "class": (SYNTAX, "`class` statements", "use functions with dicts and tuples"),
    "class-subscript": (SYNTAX, "a class subscript such as `list[int]`",
                        "no type parameters at runtime"),
    "class-union": (VALUES, "a `|` union of classes (`int | None`)", "no runtime type unions"),
    "collections": (NAMES, "an operator over a `Counter`, a non-int count, a non-builtin `default_factory`, `deque`",
                    "`Counter(iterable)`, `.most_common(n)`, `.update(iterable)`, `defaultdict(int|list|set|dict|str)`; a list instead of `deque`"),
    "complex": (SYNTAX, "complex literals such as `1j`", "no complex numbers"),
    "csv": (NAMES, "a csv shape the readers do not serve: dialects, unserved keywords, `csv.Error` text",
            "`csv.reader(f)` / `csv.DictReader(f)` over a file opened with `newline=''`; write CSV with `','.join`"),
    "decorator": (SYNTAX, "`@decorator`", "call the wrapping function explicitly"),
    "del": (SYNTAX, "`del` of a slice or attribute (`del xs[1:3]`, `del o.x`)",
            "`del` a name, a subscript or a tuple of them, or rebuild the list"),
    "dict-method": (VALUES, "a dict method outside `get keys values items setdefault pop popitem update copy clear`",
                    "use those"),
    "dict-view": (VALUES, "set algebra, comparison or `is` over `.keys()`/`.values()`/`.items()` views",
                  "wrap the view in `list()` or `set()` first"),
    "dunder-attr": (VALUES, "reading a data-model dunder such as `.__dict__` or `.__class__`",
                    "do not introspect objects"),
    "dunder-missing": (VALUES, "`getattr(x, '__doc__', default)`-style reads of a dunder the type lacks",
                       "do not introspect objects"),
    "ellipsis": (SYNTAX, "`...`", "use `pass`"),
    "encoding": (VALUES, "a codec other than UTF-8 on `encode`, `decode` or `open`",
                 "stay with UTF-8, the default"),
    "env": (HOST, "an interpreter environment variable this build cannot honour",
            "nothing in the program; the host decides"),
    "environ": (HOST, "`os.environ` as a whole under a coerced C locale",
                "read one variable with `os.environ.get('NAME')`; never print the mapping"),
    "escape": (SYNTAX, "`\\N{…}` named escapes and `\\u` escapes naming a lone surrogate",
               "write the character itself or its `\\uXXXX` code"),
    "except-star": (SYNTAX, "`except*`", "use `except`"),
    "exception": (VALUES, "an exception the engine cannot carry: an unknown class, `SystemExit` with several arguments, `KeyError.args`, `sys.exit(kw=…)`",
                  "raise and catch the builtin classes with one string argument; `sys.exit(int)`"),
    "exception-chaining": (VALUES, "`.__context__`, `.__cause__`, `raise … from …`",
                           "handle exceptions without chaining"),
    "file-method": (HOST, "a file method outside `read readline readlines write writelines close tell seek` and iteration",
                    "read the file whole with `.read()` or iterate its lines"),
    "file-read": (HOST, "`read(n)` across a `\\r` under universal newlines",
                  "read whole files or iterate lines"),
    "file-seek": (HOST, "`seek()` with a `whence` other than 0", "read the file whole instead of seeking"),
    "file-tell": (HOST, "`tell()` after iterating, or beside a bare `\\r`", "do not use `tell()`"),
    "float-sum": (VALUES, "`sum()` over floats, which CPython versions round differently",
                  "accumulate with a loop: `total = 0.0; for x in xs: total += x`"),
    "format": (VALUES, "a format spec outside the common ones: `'n'`, a spec on a type CPython rejects, an int code on a float or the reverse",
               "`{:d} {:.2f} {:>8} {:<8} {:^8} {:,} {:x} {:b} {:e} {:%}` and friends on the matching type"),
    "fstring": (SYNTAX, "`!a` conversions and self-documenting `{x=}` fields",
                "`{x!r}`, `{x!s}`, or format the value explicitly"),
    "generator": (SYNTAX, "`yield`, and so every generator function",
                  "build and return a list; generator expressions are fine"),
    "glob": (NAMES, "a glob shape outside `glob(pattern, recursive=…)` / `iglob` / `escape` / `has_magic`",
             "`sorted(glob.glob(pattern))`, with at most `recursive=True`"),
    "glob-order": (HOST, "a `glob()` result whose order the output would show",
                   "always `sorted(glob.glob(…))`"),
    "hashlib": (NAMES, "a hashlib shape outside `md5 sha1 sha256 sha512` over bytes: `new()`, sha3, blake2, KDFs, a str argument",
                "`hashlib.sha256(text.encode()).hexdigest()`"),
    "identity": (VALUES, "`is` between two equal immutables that may or may not be one object",
                 "compare with `==`; use `is` only against `None`, `True`, `False`"),
    "import": (SYNTAX, "relative and star imports", "`import m` or `from m import name`"),
    "indent": (SYNTAX, "an indented first line", "start the program in column 0"),
    "int-div-precision": (VALUES, "`int / int` past 2**53 in a build that cannot round it exactly",
                          "use `//` and `%` for exact integer work"),
    "int-method": (VALUES, "`to_bytes`/`from_bytes` outside `(length, 'big'|'little', signed=bool)` within 8 bytes, `bool.from_bytes`",
                   "`n.to_bytes(length, 'big')` with an explicit byteorder"),
    "isinstance": (VALUES, "`isinstance(x, type)`",
                   "`isinstance` against `int str float bool list dict tuple set bytes`"),
    "iterator-identity": (VALUES, "an iterator, function or file as a dict or set key",
                          "key on strings, numbers and tuples"),
    "iterator-type-name": (VALUES, "an error message that would name an iterator type (`list_iterator`, …)",
                           "iterate with `for`; do not raise over iterator objects"),
    "json": (NAMES, "`json.dumps`/`loads` with hooks, `default=`, or a keyword outside `indent sort_keys ensure_ascii separators`; a lone surrogate",
             "`json.loads(s)`, `json.dumps(obj, indent=2, sort_keys=True)`"),
    "kwonly": (SYNTAX, "keyword-only parameters, `def f(*, a)`", "make every parameter positional-or-keyword"),
    "list-method": (VALUES, "a list method outside `append extend insert pop remove index count reverse clear copy sort`",
                    "use those"),
    "math": (NAMES, "a math domain error, a non-number, a wrong count, and every function outside `ceil copysign fabs factorial floor fmod gcd isfinite isinf isnan isqrt sqrt trunc` and the constants",
             "use those; `log`, `exp` and the trigonometry need full Python"),
    "method": (VALUES, "a method the value's type does not have, or one called on the wrong type",
               "call the methods that belong to the value's type"),
    "mkdir": (HOST, "`os.mkdir`/`makedirs` with `mode=`, or over a path this run removed",
              "`os.makedirs(path, exist_ok=True)` with no mode"),
    "module": (NAMES, "`import` of any module outside the served list (`itertools`, `functools`, `string`, `textwrap`, `datetime`, `time`, `argparse`, `subprocess`, …)",
               "stay with the served modules and write the small helper by hand"),
    "module-attr": (NAMES, "a name of a partially served module outside its served names",
                    "use only the served names listed above"),
    "nan-identity": (VALUES, "two NaNs in one comparison or containment test", "test NaN with `math.isnan`"),
    "nan-order": (VALUES, "sorting, `min` or `max` over a NaN", "filter NaNs out before ordering"),
    "nonlocal": (SYNTAX, "`nonlocal`", "return the value, or keep state in a dict or list"),
    "open-mode": (HOST, "an `open()` mode outside `r`, `w`, `a` and their `b` forms",
                  "`open(path)`, `open(path, 'w')`, `'a'`, `'rb'`, `'wb'`"),
    "open-newline": (HOST, "`open(newline=…)` that translates line endings",
                     "`open(path)` or `open(path, newline='')`"),
    "open-special": (HOST, "reading something that is not a regular file: a device, a FIFO, a directory",
                     "read regular files; standard input is `sys.stdin`"),
    "os-listdir": (HOST, "`os.listdir()`, whose order is the filesystem's",
                   "`sorted(glob.glob(os.path.join(path, '*')))`"),
    "output": (HOST, "captured output past the host's byte limit",
               "print less; a program printing megabytes needs full Python"),
    "pathlib": (NAMES, "a `Path` shape not served: `PurePath`, `.stat()`, `.iterdir()` order, a `ValueError` text",
                "`Path(p).read_text()`, `.write_text()`, `.exists()`, `.name`, `.suffix`, `.stem`, `.parent`, `/` joins"),
    "percent-format": (VALUES, "`%` formatting with the `0` flag, grouping, or their interaction with `-`",
                       "f-strings or `str.format`"),
    "print-file": (HOST, "`print(file=…)` to anything but `sys.stdout`/`sys.stderr`",
                   "print to stdout, or `.write()` to the file"),
    "random": (NAMES, "an unseeded draw, `randrange` with a step, a non-int seed, keywords, `choice` over a non-sequence, `getrandbits` past 63",
               "`random.seed(<int>)` first, then `random()`, `randint`, `randrange(a, b)`, `choice(list)`; unseeded randomness needs full Python"),
    "re": (NAMES, "a regex construct outside the served slice: lookaround, backreferences, bytes patterns, Unicode `\\w \\d \\s`, non-ASCII group names, case folding, a step budget",
           "ASCII patterns with classes, groups, alternation, quantifiers and anchors, through `search match findall sub split compile`"),
    "recursion": (HOST, "nesting or call depth past the engine's limits, or a very long operator chain",
                  "iterate instead of recursing deeply; split long expressions"),
    "repr": (VALUES, "`repr()` of a function, module, file, generator or `os.environ`, whose text carries an address",
             "print values, not objects"),
    "repr-unicode": (VALUES, "`repr()` of a string holding characters outside the printable set the engine knows",
                     "print non-ASCII text directly, not inside a list or dict and not through `repr`"),
    "round": (VALUES, "`round()` outside `round(x)` / `round(x, n)` on ordinary floats",
              "`round(x, n)` on floats within 2**53"),
    "sandbox": (HOST, "a filesystem access the host denied", "nothing in the program; read stdin and print stdout"),
    "set-method": (VALUES, "a set method outside `add discard remove clear copy update`",
                   "use those and the operators `| & - ^`"),
    "set-order": (VALUES, "anything that exposes a set's iteration order: printing a set of more than one element, iterating it into output",
                  "`sorted(s)` before printing or iterating a set"),
    "setattr": (VALUES, "assignment to an attribute of an object", "keep state in dicts, lists and locals"),
    "slice-assign": (SYNTAX, "extended slice assignment, `xs[::2] = …`", "assign with a loop or rebuild the list"),
    "slice-key": (VALUES, "a slice as a dict key", "key on tuples"),
    "steps": (HOST, "still running after the step limit", "keep the work bounded; a long computation needs full Python"),
    "str-method": (VALUES, "a str method outside the served set, or one over a code point it cannot fold",
                   "`split join strip replace upper lower startswith endswith find count format isdigit zfill center ljust rjust partition splitlines encode`"),
    "subscript": (SYNTAX, "a tuple subscript containing a slice, `x[0:1, 2]`", "index with one value or one slice"),
    "token": (SYNTAX, "a byte the lexer cannot read: non-UTF-8 source, a stray character",
              "valid UTF-8 source using only Python's own punctuation"),
    "tuple-method": (VALUES, "a tuple method outside `index` and `count`", "convert with `list()`"),
    "type": (VALUES, "`type()` with three arguments, or `type()` of a value the engine cannot name",
             "`isinstance` for checks; never build classes with `type()`"),
    "unpack": (SYNTAX, "`*` inside a list, set or parenthesised display", "concatenate with `+` or `extend`"),
    "walrus": (SYNTAX, "`:=`", "assign on its own line"),
    "with": (HOST, "`with` over a value that is not a file", "use `with` only around `open()`"),
    "annotation": (SYNTAX, "an annotation the engine cannot carry: a module-level annotation before Python 3.14 (evaluated there), `__annotations__`, an annotated name also declared `global`",
                  "drop the annotation: write `x = value`"),
    "ast": (NAMES, "an `ast` shape outside `ast.literal_eval(str)`: `ast.parse`, `walk`, `dump`, the node classes, or text `literal_eval` would reject",
           "`ast.literal_eval(s)` on a plain literal such as a repr of a dict or list"),
    "aug-assign": (VALUES, "`d |= x` where `d` is a dict and `x` is not one",
                  "`d.update(x)`"),
    "binascii": (NAMES, "a `binascii` call outside `hexlify unhexlify b2a_hex a2b_hex a2b_base64 b2a_base64`, input that would raise, or `bytes.fromhex` of anything but one well-formed str",
                "those functions on well-formed input; `bytes.fromhex(s)` and `b.hex()`"),
    "call": (VALUES, "a keyword given twice through `**`, or `**` over something that is not a dict",
            "pass each keyword once, and `**` only a dict"),
    "except": (SYNTAX, "an `except` clause other than a name or a flat parenthesised tuple of names (`except ((A, B), C)`, `except A, B`)",
              "`except (A, B, C):`"),
    "exception-note": (VALUES, "an error CPython annotates with a note, such as a dict update element that is not a pair",
                      "pass pairs to `dict()` and `.update()`"),
    "future": (SYNTAX, "a `from __future__` import outside the served ones, an alias, or `__future__` anywhere but the top",
              "put `from __future__ import annotations` first, or leave it out"),
    "global": (SYNTAX, "a function made inside a nested function that declares `global`",
              "declare `global` only in top-level functions"),
    "itertools": (NAMES, "an `itertools` shape the engine does not serve",
                 "`chain`, `islice`, `product`, `permutations`, `combinations`, `groupby`, `accumulate` on lists"),
    "name-error": (VALUES, "a `NameError` in a nested scope, whose message depends on assignments elsewhere",
                  "define names before use; do not rely on the text of a NameError"),
    "name-hint": (VALUES, "an uncaught error where CPython may add a `Did you mean` suggestion",
                 "catch the errors you expect; do not rely on traceback text"),
    "remove": (HOST, "`os.remove()` of a directory",
              "`os.rmdir()` for an empty directory"),
    "rename": (HOST, "`os.rename()`/`os.replace()` of a directory, a link, or a file that existed before the program ran",
              "rename files the program itself wrote, or write the new file and remove the old one"),
    "rmdir": (HOST, "`os.rmdir()` of a directory that still holds a file the program wrote or removed",
             "remove the files first, then the directory, or leave the directory in place"),
    "statistics": (NAMES, "a `statistics` shape the engine does not serve",
                  "`mean`, `median`, `mode`, `stdev`, `pstdev`, `variance` on lists of numbers"),
    "textwrap": (NAMES, "a `textwrap` shape the engine does not serve, such as `TextWrapper`",
                "`textwrap.wrap`, `fill`, `dedent`, `indent`, `shorten` with plain arguments"),
    "time": (NAMES, "a `time` shape the engine does not serve: `gmtime`, `strftime`, a `sleep` in a loop or longer than a second, a time function used as a value",
            "call `time.time()` or `time.perf_counter()` directly"),
    "type-attr": (VALUES, "an attribute of a type object such as `int.mro`",
                 "do not introspect types"),
}

MODULE_NOTES: Dict[str, str] = {
    "math": "the functions named under `math` below, plus the constants",
    "random": "seeded only: `seed(int)` first",
    "collections": "`Counter` and `defaultdict`",
    "pathlib": "`Path`",
    "re": "a slice of the pattern language, named under `re` below",
}


def _module_line(name: str, attrs: Dict[str, List[str]]) -> str:
    if name in attrs:
        return "`%s` (%s)" % (name, " ".join("`%s`" % a for a in attrs[name]))
    if name in MODULE_NOTES:
        return "`%s` (%s)" % (name, MODULE_NOTES[name])
    return "`%s`" % name


def check(kinds: Sequence[str]) -> List[str]:
    """Every source kind has a recipe and every recipe names a source kind."""
    problems = ["no recipe for refusal kind `%s` (spelled in the crate)" % k
                for k in kinds if k not in RECIPES]
    problems += ["recipe for `%s`, which the crate no longer spells" % k
                 for k in sorted(RECIPES) if k not in kinds]
    return problems


def generate(rust: Path = RUST) -> str:
    """The spec text. Raises ValueError when the recipes and the crate disagree."""
    kinds = source_kinds(rust)
    problems = check(kinds)
    if problems:
        raise ValueError("\n".join(problems))
    only = set(only_cpython_kinds(rust))
    absent = set(cpython_only_constructs(rust))
    attrs = served_attrs(rust)
    modules = served_modules(rust)
    builtins = served_builtins(rust)

    out: List[str] = []
    w = out.append
    w("# The subset your program should stay in")
    w("")
    w("The interpreter that will run your program executes a subset of Python 3 "
      "in-process, fast. Anything outside the subset it refuses, with one line "
      "`unsupported: <kind>: <detail>`, and the program is then re-run unchanged "
      "by full Python, which costs a process spawn. Both give the same output. "
      "Correctness outranks the tier. A fall-back to full Python is free and "
      "legitimate when the task needs it. Stay inside when you can do so with an "
      "ordinary, correct program; never bend the answer to stay inside.")
    w("")
    w("## What it runs")
    w("")
    w("- Syntax: literals, operators with CPython precedence, chained comparison, "
      "slicing with a step, calls with `*args`/`**kwargs`, assignment and star "
      "unpacking, slice assignment, `global`, augmented assignment, "
      "`if`/`for`/`while`, `def` with defaults and closures, `lambda`, imports, "
      "`with open(...)`, `try`/`except`/`finally`, `raise`, `assert`, "
      "comprehensions, generator expressions, f-strings with format specs.")
    w("- Builtins: " + " ".join("`%s`" % b for b in builtins) + ".")
    w("- Modules: " + ", ".join(_module_line(m, attrs) for m in modules) + ".")
    w("- Values: `int` exact past 64 bits, `float` with CPython's repr, `str`, "
      "`bytes`, `list`, `tuple`, `dict` (insertion-ordered), `set` (never show "
      "its order), `None`, `bool`; CPython's exception classes and messages; "
      "text files, `sys.stdin`, `sys.argv`, `sys.exit`.")
    w("")
    w("## What it refuses, and how to stay inside")
    w("")
    w("One line per refusal kind: what fires it, then the way to stay inside.")
    for key, title in SECTIONS:
        rows = [k for k in kinds if k not in only and RECIPES[k][0] == key]
        if not rows:
            continue
        w("")
        w("### " + title)
        w("")
        for k in rows:
            _, what, recipe = RECIPES[k]
            note = " No build of this interpreter has it." if k in absent else ""
            w("- `%s` — %s. Stay inside: %s.%s" % (k, what, recipe, note))
    w("")
    w("### Behaviours only full Python gets right")
    w("")
    w("These go straight to full Python. A rewrite is the only way to stay "
      "inside, and only when the task allows it.")
    w("")
    for k in kinds:
        if k in only:
            _, what, recipe = RECIPES[k]
            w("- `%s` — %s. Stay inside: %s." % (k, what, recipe))
    w("")
    return "\n".join(out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if str(ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "src"))
    try:
        text = generate()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if "--check" in args:
        current = SPEC.read_text(encoding="utf-8") if SPEC.exists() else ""
        if current != text:
            print("%s is stale; run %s" % (SPEC, Path(__file__).name), file=sys.stderr)
            return 1
        return 0
    SPEC.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
