"""`pathlib.Path`, as a grid: every program on the binary and on CPython.

`tests/test_collections_grid.py` is the shape this follows and the reason it
exists: the defect is PER PROGRAM, not per function, and a handful of examples
is exactly what would miss it.

The grid is one program per row, run twice from a FRESH temp cwd (invariant 4 —
and unlike the collections rows these programs really do write files, so the
temp cwd is load-bearing rather than ceremonial), and every row must end one of
exactly two ways:

  * byte-identical stdout AND the same exit code as CPython 3.x, or
  * a clean refusal — exit 90, nothing on stdout, one
    ``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

Nothing else passes. A row is NOT allowed to be "close": a `.parts` that is
plausible but short one component prints a tuple at exit 0, which is the failure
invariant 1 exists for.

The traps this was written against, each measured against CPython 3.14.5 before
the code was written, and each with a block of rows below:

1. **The root is a component of `.parts`.** `Path('/a/b/c.txt').parts` is
   `('/', 'a', 'b', 'c.txt')`. This is the ONE pathlib divergence the oracle
   records — `lypning oracle --full`, family `pathlib-parts-drops-root`: a
   second, independent reimplementation dropped the root and answered at exit 0.
   `PARTS` is a whole block of it, including the `//` root, which CPython
   preserves and a single-slash normaliser would eat.
2. **Normalisation, not resolution.** `a//b` and `a/./b` collapse, a trailing
   slash goes, `''` becomes `'.'` — and `..` is KEPT, because a pure path is
   never resolved against a filesystem. `Path('a/../b')` is `a/../b` and its
   parent is `a/..`.
3. **Ordering compares the SPLIT string, not `.parts`.** CPython 3.12+ order
   `str(p).split('/')`, whose first element is `''` for an absolute path;
   3.11 ordered `.parts`, whose first element is `'/'`. So `Path('/a') <
   Path('!x')` is True on one and False on the other. Two paths that share a
   root order identically on both and are answered; everything else refuses.
4. **`.suffix` changed in 3.14.** `Path('a.').suffix` is `'.'` on 3.14 and `''`
   on 3.12; `Path('..a').suffix` is `''` on 3.14 and `'.a'` on 3.12. Those names
   refuse rather than pick a CPython. Every other name is the same on both and
   is a row here.
5. **Everything a filesystem or a message would have to answer** — `.glob()`,
   `.iterdir()`, `.resolve()`, `.stat()`, `Path.home()`, and every error path
   (`relative_to` on a path that is not below, `with_suffix('x')`, `Path(1)`) —
   is a refusal, and `REFUSED` below asserts it is a refusal rather than a wrong
   answer.

**One shape is deliberately answered rather than refused**, and it is worth
naming: the TypeErrors that fall out of the interpreter's generic operator paths
— `Path('a') / 1`, `Path('a') < 'b'`, `'a' + Path('b')` — are CPython's own
messages word for word once the type is named `PosixPath`, so they are grid rows
and not refusals. They are in `OPERATORS` below.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

P = "from pathlib import Path\n"

#: Every path shape worth normalising, and the ones an implementation gets
#: wrong: the empty path, both roots, a trailing slash, a doubled slash, a `.`
#: component, a `..` that must SURVIVE, a leading-dot name, a name that is
#: nothing but dots, and two that need quoting in a repr.
CASES = (
    "", ".", "..", "/", "//", "///", "a", "a/", "a//b", "a/./b", "a/../b", "./a", "a/.",
    "/a/b/c.txt", "/a//b/", ".hidden", "a.tar.gz", "...", "/..", "//a/b", "a/b/", "a b/c",
    "/.", "./", "a/../..", ".a.b", "it's", 'a"b', "X.TXT",
)

#: Construction from one segment, and the two texts it produces. `repr` is
#: `PosixPath('a')` on POSIX — the quote choice is the ordinary string rule, so
#: the two quoted rows are here to hold it.
CONSTRUCTION = [P + "print(repr(Path(%r)), str(Path(%r)))" % (c, c) for c in CASES] + [
    P + "print(Path('a','b','c'), Path('a','/b','c'), Path(), Path('a', Path('b')))",
    P + "print(Path(Path('a/b')))",
    P + "print(Path(*['a','b','c']))",
    P + "print(repr(Path('\\u00e9')))",
    P + "print(repr(Path('a\\nb')), repr(Path('a\\\\b')))",
    # The dotted spellings, and the one alias that IS the same class on POSIX,
    # repr included. `PurePosixPath` is not — it reprs as `PurePosixPath('a')`
    # — and is in `REFUSED`.
    "import pathlib\nprint(pathlib.Path('a/b'), pathlib.Path('a/b').name)",
    "import pathlib as pl\nprint(pl.Path('/a').parts)",
    "import pathlib\nprint(repr(pathlib.PosixPath('a/b')), pathlib.PosixPath('a').name)",
    "import pathlib\nprint(isinstance(pathlib.Path('a'), pathlib.PosixPath))",
    "import pathlib\nfrom pathlib import Path\nprint(Path('a') == pathlib.Path('a'))",
    "import pathlib\nprint(pathlib.Path.cwd().is_absolute())",
]

#: Trap 1. The root is a component.
PARTS = [P + "print(Path(%r).parts)" % c for c in CASES] + [
    P + "a, b = Path('a/b').parts\nprint(a, b)",
    P + "print('/'.join(Path('/a/b/c').parts))",
    P + "print(sorted(Path('/z/a').parts))",
    P + "print(Path('/a/b').parts[-1], Path('/a/b').parts[0], Path('/a/b').parts[1:])",
    P + "print(Path('a/b').parts == ('a','b'), Path('/a').parts == ('/','a'))",
    P + "for x in Path('/a/b').parts: print(x)",
    P + "print(len(Path('/a/b/c').parts))",
    P + "print(Path('a/../b').parts, Path('a/../b').parent)",
    P + "print(Path('a/b/c').relative_to('a').parts)",
]

#: Trap 4 lives here: the names whose stem and suffix moved between 3.12 and
#: 3.14 are in `CASES` (`'...'`, `'.a.b'`) or in `REFUSED` (`'a.'`, `'..a'`).
NAMES = [
    P + "p=Path(%r)\nprint(repr(p.name), repr(p.stem), repr(p.suffix), p.suffixes)" % c
    for c in CASES
] + [
    P + "print(Path('X.TXT').suffix, Path('X.TXT').stem)",
    P + "print(Path('\\u00e9/\\u4e2d.txt').name, Path('\\u00e9/\\u4e2d.txt').suffix)",
    P + "print(Path('a').stem, Path('a').suffix, Path('a').suffixes)",
    P + "print(Path('.').name == '', Path('/').name == '')",
    P + "print(Path('a/b').name.upper(), Path('a/b.txt').suffix.lstrip('.'))",
    P + "print(Path('..').parent, Path('..').name, Path('..').parts)",
]

#: `.parent` is a path; `.parents` is a sequence of them, shortest LAST, and
#: empty for a path with no components at all.
PARENTS = [
    P + "p=Path(%r)\nprint(p.parent, len(p.parents), [str(x) for x in p.parents])" % c
    for c in CASES
] + [
    P + "print(Path('/a/b/c').parent.parent.parent, Path('a').parent.parent)",
    P + "print(tuple(Path('/a/b').parents))",
    P + "print(Path('/a/b').parents[1], Path('/a/b/c').parents[0], Path('/a/b/c').parents[-1])",
    P + "print(list(Path('/a/b/c').parents))",
    P + "for q in Path('/a/b/c').parents: print(q)",
    P + "i = 0\nfor q in Path('/a/b/c').parents:\n    i += 1\n    if i == 2: break\nprint(i, q)",
    P + "print([len(Path(x).parents) for x in ['.','a','a/b','/','/a']])",
    P + "print(len(Path('a').parents), bool(Path('.').parents), bool(Path('a').parents))",
    P + "print(repr(Path('/a/b').parents))",
    P + "print(Path('/a/b').relative_to('/a').parent)",
]

#: The `/` operator in all three spellings CPython defines, `joinpath`, and the
#: multi-segment constructor — one operation, four ways to write it, and a
#: later segment that starts with `/` replaces everything before it.
_JOIN = [
    ("a", "b"), ("/a", "b"), ("a", "/b"), ("a", ""), ("", "b"), (".", "a"), ("a", "."),
    ("a", ".."), ("/", "a"), ("//", "a"), ("a", "b/c"), ("a", "./b"), ("a/", "b"),
    ("a", "b/"), ("..", "a"), ("a/b", "../c"), ("/", "/"), ("a", "//b"),
]
JOIN = []
for _a, _b in _JOIN:
    JOIN += [
        P + "print(Path(%r) / %r)" % (_a, _b),
        P + "print(%r / Path(%r))" % (_a, _b),
        P + "print(Path(%r) / Path(%r))" % (_a, _b),
        P + "print(Path(%r).joinpath(%r))" % (_a, _b),
        P + "print(Path(%r, %r))" % (_a, _b),
    ]
JOIN += [
    P + "print(Path('a').joinpath('b','c'), Path('a').joinpath())",
    P + "print(Path('a')/'b'/'c'/'d')",
    P + "print(Path('a') / Path('/b'))",
    P + "print(Path('a').joinpath(Path('b')))",
    P + "print(Path('a/b') == Path('a') / 'b')",
    P + "p = Path('d') / 'e' / 'f'\nprint(p, p.parent, p.parts)",
]

#: `with_name` / `with_suffix` / `with_stem`, only where both CPython eras build
#: the same path. The invalid arguments are all in `REFUSED`.
WITH = [
    P + "print(Path(%r).with_name(%r))" % (c, n)
    for c, n in [("a/b.txt", "c.md"), ("a", "z"), ("/a/b", "c"), ("a/b", ".."), ("a/b.txt", ".q")]
] + [
    P + "print(Path(%r).with_suffix(%r))" % (c, s)
    for c, s in [("a/b.txt", ".md"), ("a/b.txt", ""), ("a/b", ".x"), ("a.tar.gz", ".bz2"),
                 ("a/b.txt", ".a.b"), ("/x/.hidden", ".z"), ("/a", ".x"), ("a/b", ".tar.gz")]
] + [
    P + "print(Path(%r).with_stem(%r))" % (c, s)
    for c, s in [("a/b.txt", "q"), ("a", "z"), ("a.tar.gz", "w")]
]

#: `relative_to`, in both spellings of its argument. Not-below is a ValueError
#: whose wording moved between versions, so it is in `REFUSED`.
RELATIVE = []
for _a, _b in [("/a/b/c", "/a"), ("/a/b/c", "/a/b"), ("a/b", "a"), ("a/b", "a/b"), ("/a", "/"),
               ("a/b/c", "."), ("//a/b", "//a"), ("a/../b", "a/..")]:
    RELATIVE += [
        P + "print(Path(%r).relative_to(%r))" % (_a, _b),
        P + "print(Path(%r).relative_to(Path(%r)))" % (_a, _b),
    ]
RELATIVE += [P + "print(Path('a/b').relative_to('a').is_absolute())"]

#: Equality (a path is never equal to the str that spells it), ordering under
#: trap 3, hashing, and every container a path lands in.
COMPARE = [
    P + "print(Path('a')==Path('a'), Path('a')=='a', Path('a')!='a', Path('a')==Path('b'))",
    P + "print(Path('./a')==Path('a'), Path('a/')==Path('a'), Path('/a')==Path('//a'))",
    P + "print(Path('a')==1, Path('a')==None)",
    P + "print(Path('a/b') < Path('a.b'), Path('a') < Path('a/b'), Path('b') < Path('a'))",
    P + "print(Path('a') <= Path('a'), Path('a') >= Path('b'), Path('b') > Path('a'))",
    P + "print(sorted([Path('b'),Path('a/c'),Path('a.b'),Path('a')]))",
    P + "print(sorted([Path('/b'),Path('/a/c'),Path('/a')]))",
    P + "print([str(p) for p in sorted(Path(x) for x in ['b','a/c','a'])])",
    P + "xs = [Path('b'), Path('a')]\nxs.sort()\nprint(xs)",
    P + "print(max(Path('a'),Path('b')), min(Path('a'),Path('b')))",
    P + "d={Path('a'):1}\nd[Path('a')]=2\nd[Path('b')]=3\nprint(d, len(d))",
    P + "d={Path('a'):1,'a':2}\nprint(len(d), d[Path('a')], d['a'])",
    P + "d = {Path(x): i for i, x in enumerate(['a','b','a'])}\nprint(d, len(d))",
    P + "print(Path('a') in [Path('a'), Path('b')], Path('c') in [Path('a')])",
    P + "print(len({Path('a'), Path('a'), Path('b')}))",
    P + "print([Path('a'), Path('/b')])",
    P + "print({'k': Path('a/b')})",
    P + "print((Path('a'),))",
    P + "print(isinstance(Path('a'), Path), isinstance('a', Path), isinstance(Path('a'), str))",
    P + "print(isinstance(Path('a'), (str, Path)))",
    P + "print(bool(Path('.')), bool(Path('a')))",
    P + "print(Path('a') if Path('a') else 'no')",
]

#: `str(p)` is `__fspath__`, which is what makes a path work as an argument to
#: everything that already took a filename — and `%`-formatting, which is
#: `str()` in CPython too and so is NOT the `__format__` refusal.
STRINGS = [
    P + "print(f'{Path(\"a/b\")}', '%s' % Path('a/b'), '%r' % Path('a/b'))",
    P + "print(f'{Path(\"a/b\")!r}', f'{Path(\"a/b\")!s}')",
    P + "print('x' + str(Path('a')), str(Path('a')).upper())",
    P + "print(Path('a/b').as_posix(), repr(Path('/a').as_posix()))",
    P + "print(Path('a').as_posix() + 'x', len(Path('a/b').as_posix()))",
    P + "print(str(Path('a/b')).split('/'), str(Path('a/b')).replace('/', '|'))",
    P + "print(len(str(Path('a/b'))), str(Path('a/b'))[0])",
    P + "print(str(Path('a')).encode())",
    P + "import json\nprint(json.dumps(str(Path('a/b'))))",
    P + "import os\nprint(os.path.basename(str(Path('a/b'))))",
    P + "import os.path\nprint(os.path.join('x', str(Path('a'))))",
] + [P + "p=Path(%r)\nprint(p.is_absolute(), repr(p.as_posix()))" % c for c in CASES]

#: The operator TypeErrors, which are the interpreter's generic messages and are
#: CPython's own word for word once the type is named `PosixPath`. Rows, not
#: refusals — see the module docstring.
OPERATORS = [
    P + "print(Path('a') / 1)",
    P + "print(Path('a') / None)",
    P + "print(1 / Path('a'))",
    P + "print(Path('a') + 'b')",
    P + "print('a' + Path('b'))",
    P + "print(Path('a')[0])",
    P + "print(len(Path('a')))",
    P + "print([c for c in Path('a')])",
    P + "print(Path('a') < 'b')",
    P + "print('a' < Path('b'))",
]

#: The filesystem half, which goes through `io.rs`'s commit barrier because it
#: goes through the same `open()` every other file program here does.
FILESYSTEM = [
    P + "p=Path('x.txt')\nprint(p.exists())\nprint(p.write_text('hi\\n'))\n"
        "print(p.exists(), p.is_file(), p.is_dir())\nprint(repr(p.read_text()))",
    P + "p=Path('x.bin')\nprint(p.write_bytes(b'ab'))\nprint(p.read_bytes())",
    P + "p=Path('x.txt')\np.write_text('a\\nb\\n')\nwith p.open() as f: print(f.read().split())",
    P + "p=Path('x.txt')\np.write_text('a')\nprint(p.read_text(encoding='utf-8'))",
    P + "p = Path('f.txt')\np.write_text('\\u00e9\\u00e9')\nprint(p.read_text(), len(p.read_text()))",
    P + "p = Path('f.txt')\nprint(p.write_text('\\u00e9'))",
    P + "p = Path('f.txt')\np.write_text('a')\nwith open(p, 'a') as f: f.write('b')\nprint(p.read_text())",
    P + "p = Path('f.txt')\np.write_text('one\\ntwo\\n')\nprint([l.rstrip() for l in p.open()])",
    P + "p = Path('f.txt')\np.write_text('x')\nprint(p.read_bytes(), Path('f.txt').read_text())",
    P + "p=Path('d')\np.mkdir()\nprint(p.is_dir(), p.exists())",
    P + "p=Path('d/e')\np.mkdir(parents=True)\nprint(p.is_dir())",
    P + "p=Path('d')\np.mkdir()\np.mkdir(exist_ok=True)\nprint(p.is_dir())",
    P + "p=Path('x.txt')\np.write_text('q')\np.unlink()\nprint(p.exists())",
    P + "p=Path('x.txt')\np.write_text('hi')\nprint(open(str(p)).read())",
    P + "p=Path('x.txt')\np.write_text('hi')\nprint(open(p).read())",
    P + "import os\np=Path('x.txt')\np.write_text('hi')\nprint(os.path.exists(p), os.path.getsize(p))",
    P + "import os\np = Path('f.txt')\np.write_text('xy')\nprint(os.path.getsize(str(p)))",
    P + "print(Path('nope.txt').exists(), Path('nope.txt').is_file(), Path('nope.txt').is_dir())",
    P + "p = Path('f.txt')\nprint(p.read_text())",
    P + "p=Path('sub')/'f.txt'\nPath('sub').mkdir()\np.write_text('z')\nprint(p.read_text(), p.parent.name)",
    P + "p = Path('sub')\np.mkdir()\n(p / 'x.txt').write_text('1')\n"
        "print((p / 'x.txt').read_text(), (p / 'x.txt').exists())",
    P + "p=Path('a.txt')\np.write_text('1')\nprint(sorted([p.name, p.suffix]))",
    # `Path.cwd()` is `os.getcwd()`, and both engines run in the same temp cwd.
    P + "print(Path.cwd().is_absolute(), Path.cwd() == Path.cwd())",
    P + "import os\nprint(str(Path.cwd()) == os.getcwd())",
    P + "print(Path.cwd().name == Path.cwd().name)",
]

GRID = (CONSTRUCTION + PARTS + NAMES + PARENTS + JOIN + WITH + RELATIVE + COMPARE
        + STRINGS + OPERATORS + FILESYSTEM)

#: Every shape CPython answers and this engine must NOT, because any answer
#: would be a guess: a directory order, a resolved path, a version-dependent
#: split, or an exception message that moved between 3.11, 3.12 and 3.14.
REFUSED = [P + x for x in [
    # Directory order is filesystem-defined — the same reason `os.listdir`
    # already refuses (`modules.rs`).
    "print(list(Path('.').glob('*')))",
    "print(list(Path('.').rglob('*')))",
    "print(list(Path('.').iterdir()))",
    "print(Path('a').walk())",
    # State this engine cannot pin, and metadata it will not invent.
    "print(Path('a').resolve())",
    "print(Path('a').absolute())",
    "print(Path('~').expanduser())",
    "print(Path.home())",
    "print(Path('.').stat())",
    "print(Path('a').is_symlink())",
    "print(Path('a').samefile('b'))",
    "print(Path('a').touch())",
    "print(Path('a').as_uri())",
    "print(Path('a').match('*'))",
    # Attributes and classes outside the surface. A refusal, never an
    # AttributeError: CPython answers all of these, and exit 1 is the program's
    # own exit, which the chain never retries.
    "print(Path('a').drive, Path('a').root)",
    "print(Path('a').anchor)",
    "print(Path('a').nosuchthing)",
    "print(type(Path('a')))",
    "print(Path)",
    "import pathlib\nprint(pathlib.PurePath('a'))",
    # `PurePosixPath('a')` reprs as itself, so aliasing it to `Path` would print
    # `PosixPath('a')` at exit 0 — the whole reason it is not aliased.
    "import pathlib\nprint(pathlib.PurePosixPath('a'))",
    "import pathlib\nprint(pathlib.WindowsPath('a'))",
    "import pathlib\nprint(pathlib.PurePath)",
    "import pathlib\nprint(pathlib.Path.home())",
    # Trap 4: the names CPython 3.12 and 3.14 split differently.
    "print(Path('a.').suffix)",
    "print(Path('a.').stem)",
    "print(Path('..a').suffix)",
    "print(Path('a..').suffixes)",
    "print(Path('a.b.').suffixes)",
    # Trap 3: an ordering the two eras disagree about.
    "print(Path('/a') < Path('a'))",
    "print(Path('//a') < Path('/b'))",
    "print(sorted([Path('/a'), Path('b')]))",
    # Every error path. The exception CLASS is right in CPython and the message
    # is not this engine's to write.
    "print(Path('/a').relative_to('/b'))",
    "print(Path('/a/b').relative_to('/x', walk_up=True))",
    "print(Path('/a/b').relative_to('/a', '/b'))",
    "print(Path('a/b').relative_to(1))",
    "print(Path('a').with_name(''))",
    "print(Path('a').with_name('.'))",
    "print(Path('.').with_name('x'))",
    "print(Path('a').with_name('b/c'))",
    "print(Path('a').with_suffix('x'))",
    "print(Path('a').with_suffix('.'))",
    "print(Path('.').with_suffix('.x'))",
    "print(Path(1))",
    "print(Path('a', 1))",
    # Keyword arguments this engine does not serve, refused rather than dropped.
    "print(Path('a').read_text(errors='ignore'))",
    "print(Path('a').read_text(encoding='latin-1'))",
    "print(Path('a').unlink(missing_ok=True))",
    "print(Path('a').mkdir(mode=0o755))",
    "print(Path('a').open('rb+'))",
    "print(Path('a').write_text(1))",
    "print(Path('a').write_bytes('x'))",
    # `object.__format__` raises for a non-empty spec, so padding one here would
    # be a wrong answer at exit 0 on the one spelling that lines paths up.
    "print(f'{Path(\"a\"):>10}')",
    "print(f'{Path(\"a\"):5}')",
    "print(format(Path('a'), '>5'))",
    # The `.parents` view outside len/index/iteration. CPython answers each of
    # these and the value here carries no object identity to answer them with.
    "print(Path('a').parents[5])",
    "print(Path('/a/b').parents[0:2])",
    "print(Path('a') in Path('/a/b').parents)",
    "print(list(reversed(Path('/a/b').parents)))",
    "print(Path('/a/b').parents == Path('/a/b').parents)",
    "print(hash(Path('/a/b').parents))",
    "print({Path('/a/b').parents: 1})",
    "print(Path('a').parents.name)",
    # A refusal leaves NOTHING on stdout even when the program printed first —
    # the half of invariant 2 that only ever broke silently.
    "print('hi')\nprint(Path('a').resolve())",
]]


def _spectrum(binary: Path) -> dict | None:
    """What ``binary`` says it is, or ``None`` if it will not say."""
    try:
        out = subprocess.run([str(binary), "route", "--spectrum"],
                             capture_output=True, text=True, timeout=60)
    except OSError:
        return None
    try:
        import json
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def _current(engine: str) -> Path | None:
    """A built ``engine`` that carries THIS tree's capability table.

    A candidate is taken only if it names itself ``engine``, its compiled
    `route::CAPS` knows `cap-pathlib`, and the capability set it was BUILT with
    is this tree's. Those last two are the point: an installed binary from
    before this capability landed answers every grid row with a refusal, which
    would turn the whole file into green skips measuring nothing. Skipping
    loudly is the honest failure; passing quietly is not.
    """
    target = {engines.LYPNING: paths.RUST_DIR / "target" / "release" / "lypning",
              engines.LYPNING_L: paths.RUST_DIR / "target" / "variant-l" / "release" / "lypning"}
    found = engines.find(engine)
    for cand in ([Path(found)] if found else []) + [target[engine]]:
        if not cand.is_file():
            continue
        table = _spectrum(cand)
        if table is None or table.get("self") != engine:
            continue
        if table.get("self_caps") != list(engines.VARIANT_CAPS.get(engine, ())):
            continue
        if any(row.get("cap") == "cap-pathlib" for row in table.get("caps", [])):
            return cand
    return None


BINARY = _current(engines.LYPNING_L)
CORE = _current(engines.LYPNING)

needs_l = pytest.mark.skipif(
    BINARY is None,
    reason="no lypning-l carrying cap-pathlib is built (cargo build --release "
           "--no-default-features --features variant-l --target-dir target/variant-l)",
)


def _run(argv: list[str], program: str) -> subprocess.CompletedProcess:
    """One program, in a temp cwd of its own — invariant 4, and here the rows
    really do write files, so this is what keeps the tree out of their way."""
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True, text=True,
                              cwd=d, timeout=60)


def _refusal_problem(got: subprocess.CompletedProcess) -> str | None:
    """``None`` if this is a clean exit-90 refusal, else what is wrong with it."""
    if got.returncode != engines.UNSUPPORTED_EXIT:
        return "exit %d, not %d" % (got.returncode, engines.UNSUPPORTED_EXIT)
    if got.stdout != "":
        return "stdout was not empty: %r" % got.stdout[:120]
    head = "%s: unsupported: " % engines.LYPNING_L
    line = got.stderr.strip()
    if not line.startswith(head) or "\n" in line:
        return "stderr was %r, expected one %r line" % (line[:160], head)
    return None


@needs_l
@pytest.mark.parametrize("program", GRID, ids=range(len(GRID)))
def test_the_pathlib_grid_agrees_with_cpython(program: str) -> None:
    got = _run([str(BINARY)], program)
    if got.returncode == engines.UNSUPPORTED_EXIT:
        # A refusal is always allowed and is never a bug — but it must be a
        # CLEAN one, and it must be reported, because a row that started
        # refusing is a row that stopped measuring anything.
        problem = _refusal_problem(got)
        assert problem is None, "%s\n  program: %r" % (problem, program)
        pytest.skip("lypning-l refuses this row: %s" % got.stderr.strip()[:160])
    ref = _run([sys.executable], program)
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "lypning-l disagrees with CPython.\n"
        "  program:  %r\n"
        "  lypning-l: %r exit %d %s\n"
        "  cpython:   %r exit %d %s"
        % (program, got.stdout, got.returncode, got.stderr.strip()[-200:],
           ref.stdout, ref.returncode, ref.stderr.strip()[-200:])
    )


@needs_l
@pytest.mark.parametrize("program", REFUSED, ids=range(len(REFUSED)))
def test_the_surface_outside_the_subset_refuses_rather_than_guesses(program: str) -> None:
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got)
    assert problem is None, (
        "this program must refuse, not answer — CPython answers it and any "
        "answer here would be a silent divergence: %s\n  program: %r\n  stderr: %r"
        % (problem, program, got.stderr.strip()[:200])
    )


@needs_l
def test_the_capability_is_on_the_larger_variant_only() -> None:
    """The gate this whole file sits behind: the core must still REFUSE
    `pathlib`, and must route it to the sibling that serves it.

    A capability that leaked into the frozen variant would still pass every grid
    row above — it is the same code — so the byte budget is defended here, by
    asking each binary what it is."""
    core = CORE
    if core is None:
        pytest.skip("no core carrying this tree's capability table is built")
    refused = _run([str(core)], "import pathlib")
    assert refused.returncode == engines.UNSUPPORTED_EXIT and refused.stdout == ""
    assert refused.stderr.strip() == engines.refusal_line(
        engines.LYPNING, "module", "import pathlib")

    # …and the core's ROUTER knows which sibling does serve it, which is the
    # half that makes the refusal cost one spawn instead of a CPython one.
    route = subprocess.run([str(core), "route", "-c",
                            'from pathlib import Path\nprint(Path("a/b").name)'],
                           capture_output=True, text=True, timeout=60)
    assert route.stdout.split("\t")[0].strip() == engines.LYPNING_L, route.stdout


@needs_l
def test_a_pathlib_method_name_is_admitted_only_for_a_program_that_imports_pathlib() -> None:
    """`.name` is an ordinary attribute on other objects.

    Admitting `known_method("name")` for every receiver would take a program
    this engine sends to CPython today — which answers it — and run it here
    instead, where it stops at an `AttributeError`: exit 1, the program's own
    exit, which the chain never retries. The import is what makes the router's
    optimism honest, and this is the assertion that says so."""
    without = subprocess.run([str(BINARY), "route", "-c", 'f=open("x")\nprint(f.name)'],
                             capture_output=True, text=True, timeout=60)
    assert without.stdout.split("\t")[0].strip() == engines.CPYTHON, without.stdout
    with_import = subprocess.run(
        [str(BINARY), "route", "-c", 'from pathlib import Path\nprint(Path("a").name)'],
        capture_output=True, text=True, timeout=60)
    assert with_import.stdout.split("\t")[0].strip() == engines.LYPNING_L, with_import.stdout


@needs_l
def test_the_python_copy_of_the_capability_table_is_the_binarys_own() -> None:
    """`engines.VARIANT_CAPS` is a copy of `route::SPECTRUM`'s caps column, and
    a copy is honest only while something checks it."""
    import json
    out = subprocess.run([str(BINARY), "route", "--spectrum"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    table = json.loads(out.stdout.strip().splitlines()[-1])
    assert table["self"] == engines.LYPNING_L
    assert "cap-pathlib" in table["self_caps"]
    assert {r["name"]: tuple(r["caps"]) for r in table["spectrum"]} == engines.VARIANT_CAPS
    assert {r["cap"]: r["modules"] for r in table["caps"]}["cap-pathlib"] == ["pathlib"]
