"""`glob`, as a grid: every program on the binary and on CPython.

`tests/test_pathlib_grid.py` is the shape this follows and the reason it
exists: conformance grades the corpus, not the language, so the defect that
only shows on a shape nobody has typed yet is exactly the one it cannot see.

Every row runs in a fresh temp cwd holding the same fixture tree (invariant 4),
built by the test process so that both engines glob over identical files, and
ends one of exactly two ways:

  * byte-identical stdout AND the same exit code as CPython 3.x, or
  * a clean refusal — exit 90, nothing on stdout, one
    ``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

**The trap this file is written against is the order.** `glob.glob()` returns
the order `os.scandir` gave, which no implementation can reproduce — the same
fact that makes `os.listdir()` a refusal (`modules.rs`) and `Path.glob()` one
(`pathlib.rs`). So the rows come in two families, and the split between them is
the capability's whole claim:

  * A result of **nought or one path** has no order, so it is an ordinary list
    and every row using one is in `GRID` — including `print(glob.glob(...))`,
    `[0]` and a bare `for`.
  * A result of **two or more paths** answers only the order-blind
    questions — `sorted`, `len`, `in`, `bool`, `min`/`max`, `set`, `+` — which
    are `MULTI` below, and refuses everything that would show the order, which
    is the `glob-order` block of `REFUSED`.

The three exactness traps, each measured against CPython 3.14.5 before the code
was written, each with a block of rows:

1. **Hidden files.** `*` and `?` do not match a leading dot, but a pattern
   whose own basename starts with one does, and a basename with no magic is an
   existence test that does not filter at all. `**` drops hidden names at every
   level of its walk.
2. **A pattern with no magic never lists a directory.** It is `lexists`, so a
   BROKEN SYMLINK matches where `os.path.exists` would say no; and a pattern
   ending in `/` matches only directories and keeps the slash.
3. **`recursive=True` and `**`.** `**` matches the empty path first, so
   `glob('d/**', recursive=True)` yields `'d/'` before anything under it. `**`
   is special only as a whole component and only with the keyword — `'**x'`,
   and `'**'` without it, are an ordinary `*`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

#: The tree every row globs over, built by the TEST PROCESS rather than by the
#: program, for two reasons that are really one. The engine's commit barrier
#: stages writes until a run ends, so a program that makes its own files and
#: then globs them is refused (`glob.rs`) — every row would be a green skip
#: measuring nothing. And a fixture built here is built identically for both
#: engines, which is what makes a byte-for-byte comparison mean anything.
#:
#: A hidden file, a hidden DIRECTORY, two levels of nesting, a name holding a
#: bracket, and a broken symlink — which exists for `lexists` and not for
#: `exists`, and is trap 2.
TREE = ("a.py", "b.py", "c.txt", ".hidden", "d/x.py", "d/y.txt", "d/sub/z.py",
        ".dot/e.py", "we[i]rd")


def _tree(root: Path) -> None:
    for name in TREE:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
    (root / "broken").symlink_to("nowhere-at-all")


def _p(body: str) -> str:
    return "import glob, json, os\n" + body + "\n"


#: A pattern whose match count is nought or one: an ordinary list, so every
#: shape is answered, including the ones the multi-path family refuses.
SINGLE = [_p(x) for x in [
    "print(glob.glob('a.py'))",
    "print(glob.glob('c.txt'))",
    "print(glob.glob('nosuch'))",
    "print(glob.glob('nosuch*'))",
    "print(glob.glob('*.txt'))",
    "print(glob.glob('d/x.py'))",
    "print(glob.glob('*.txt')[0])",
    "print(len(glob.glob('*.txt')), len(glob.glob('zz*')))",
    "for f in glob.glob('*.txt'): print(f)",
    "for f in glob.glob('zz*'): print(f)",
    "print(list(glob.glob('*.txt')), tuple(glob.glob('zz*')))",
    "print(glob.glob('*.txt') == ['c.txt'], glob.glob('zz*') == [])",
    "print(json.dumps(glob.glob('*.txt')), json.dumps(glob.glob('zz*')))",
    "print(glob.glob('*.txt') + glob.glob('zz*'))",
    "g = glob.glob('*.txt')\ng.sort()\ng.append('x')\nprint(g)",
    "print(','.join(glob.glob('*.txt')))",
    "print('%s' % (glob.glob('*.txt'),))",
    "print(repr(glob.glob('zz*')), str(glob.glob('*.txt')))",
    "print(sorted(glob.glob('*.txt')), bool(glob.glob('zz*')))",
]]

#: Trap 2 — a pattern with no magic is an existence test, not a listing.
NOMAGIC = [_p(x) for x in [
    "print(glob.glob('d'))",
    "print(glob.glob('d/'))",
    "print(glob.glob('nosuch/'))",
    "print(glob.glob('c.txt/'))",
    "print(glob.glob(''))",
    "print(glob.glob('.'))",
    "print(glob.glob('./a.py'))",
    "print(glob.glob('d/sub/z.py'))",
    "print(glob.glob('/'))",
    "print(glob.glob('/nonexistent-root-xyz/a'))",
    "print(glob.glob('.hidden'))",
    "print(glob.glob('.dot/e.py'))",
]]

#: Trap 1 — the hidden-file rules, which are three different rules.
HIDDEN = [_p(x) for x in [
    "print(sorted(glob.glob('.*')))",
    "print(sorted(glob.glob('.h*')))",
    "print(glob.glob('*hidden'))",
    "print(sorted(glob.glob('*')))",
    "print(sorted(glob.glob('.dot/*')))",
    "print(sorted(glob.glob('*/*.py')))",
    "print(sorted(glob.glob('**/*.py', recursive=True)))",
    "print(sorted(glob.glob('.*/*.py')))",
    "print(len(glob.glob('?.py')), sorted(glob.glob('?.py')))",
]]

#: Trap 3 — `**`, with and without the keyword that makes it special.
RECURSIVE = [_p(x) for x in [
    "print(sorted(glob.glob('**', recursive=True)))",
    "print(sorted(glob.glob('**')))",
    "print(sorted(glob.glob('**x')))",
    "print(sorted(glob.glob('d/**', recursive=True)))",
    "print(sorted(glob.glob('d/**')))",
    "print(sorted(glob.glob('**/', recursive=True)))",
    "print(sorted(glob.glob('d/**/', recursive=True)))",
    "print(sorted(glob.glob('**/*.py', recursive=True)))",
    "print(sorted(glob.glob('**/*.txt', recursive=True)))",
    "print(sorted(glob.glob('d/**/*.py', recursive=True)))",
    "print(sorted(glob.glob('**/**', recursive=True)))",
    "print(sorted(glob.glob('*/**', recursive=True)))",
    "print(sorted(glob.glob('d/sub/**', recursive=True)))",
    "print(len(glob.glob('**/*', recursive=True)))",
    "print(sorted(glob.glob('**/*.py', recursive=False)))",
    "print(sorted(glob.glob('**/*.py', recursive=0)))",
    "print(sorted(glob.glob('**/*.py', recursive=1)))",
]]

#: The bracket expression, whose translation in `fnmatch` is the least obvious
#: part of the pattern language: `]` first is a literal, `!` first negates, a
#: `[` with no closing `]` is a literal `[`, and a `-` at either end is one too.
BRACKETS = [_p(x) for x in [
    "print(sorted(glob.glob('[ab].py')))",
    "print(sorted(glob.glob('[!a].py')))",
    "print(sorted(glob.glob('[a-c].py')))",
    "print(sorted(glob.glob('[!a-b].py')))",
    "print(sorted(glob.glob('[]a].py')))",
    "print(sorted(glob.glob('[a.py')))",
    "print(sorted(glob.glob('a[]].py')))",
    "print(sorted(glob.glob('*[.]py')))",
    "print(sorted(glob.glob('*[-.]py')))",
    "print(sorted(glob.glob('we[[]i]rd')))",
    "print(sorted(glob.glob(glob.escape('we[i]rd'))))",
    "print(sorted(glob.glob('we[i]rd')))",
    "print(sorted(glob.glob('[abc]*')))",
    "print(sorted(glob.glob('*.p[xy]')))",
    "print(sorted(glob.glob('[!]a].py')))",
]]

#: The two pure-string names, which have no filesystem in them at all.
STRINGS = [_p(x) for x in [
    "print(glob.escape('a*b?c[d]e'))",
    "print(glob.escape(''))",
    "print(glob.escape('/a/b*'))",
    "print(glob.escape('***'))",
    "print(glob.has_magic('a*'), glob.has_magic('a'), glob.has_magic('['))",
    "print(glob.has_magic('?'), glob.has_magic(''), glob.has_magic('a]b'))",
]]

#: A result of two or more paths, asked only what does not depend on its order.
#: This block is the capability's claim: every one of these is exact, and the
#: `glob-order` block of `REFUSED` is everything else.
MULTI = [_p(x) for x in [
    "print(sorted(glob.glob('*.py')))",
    "print(len(glob.glob('*.py')))",
    "print(bool(glob.glob('*.py')))",
    "print('a.py' in glob.glob('*.py'), 'zz' in glob.glob('*.py'))",
    "print(min(glob.glob('*.py')), max(glob.glob('*.py')))",
    "print(sorted(set(glob.glob('*.py'))))",
    "print(any(glob.glob('*.py')), all(glob.glob('*.py')))",
    "print(sorted(glob.glob('*.py') + glob.glob('d/*.py')))",
    "print(len(glob.glob('*.py') + glob.glob('d/*.py')))",
    "print(sorted(glob.glob('*.py') + ['zz']))",
    "print(sorted(['zz'] + glob.glob('*.py')))",
    "print(sorted(glob.glob('*.py'), reverse=True))",
    "print(sorted(glob.glob('*.py'), key=str))",
    "print(sorted(set(glob.glob('*.py')) | set(glob.glob('d/*.py'))))",
    "print(isinstance(glob.glob('*.py'), list))",
    "print(len(sorted(glob.glob('*.py'))), sorted(glob.glob('*.py'))[0])",
    "for f in sorted(glob.glob('*.py')): print(f, open(f).read())",
    "print(sum(len(p) for p in sorted(glob.glob('*.py'))))",
    "print(sorted(glob.glob('*.py'), key=lambda p: p[::-1]))",
]]

#: The spellings a router has to resolve, and the aliases with them.
SPELLINGS = [_p(x) for x in [
    "print(sorted(glob.glob('*.py')))",
    "print(sorted(glob.glob(os.path.join('d', '*.py'))))",
    "print(sorted(glob.glob('d' + os.sep + '*.py')))",
]] + [
    "import glob as g\nprint(sorted(g.glob('*.py')), g.escape('a*'))\n",
    "from glob import glob\nprint(sorted(glob('*.py')))\n",
    "from glob import glob, escape\nprint(sorted(glob('*.py')), escape('a*'))\n",
    "from glob import escape as e\nprint(e('a*b'))\n",
    "import glob\nf = glob.glob\nprint(sorted(f('*.py')))\n",
]

GRID = SINGLE + NOMAGIC + HIDDEN + RECURSIVE + BRACKETS + STRINGS + MULTI + SPELLINGS

#: Every shape CPython answers and this engine must NOT, because any answer
#: would be a guess. The first block is the order — the reason this capability
#: has a rule at all — and the rest is the surface CPython owns.
REFUSED = [_p(x) for x in [
    # The order, reached every way a list can be read by position.
    "print(glob.glob('*.py'))",
    "print(repr(glob.glob('*.py')))",
    "print(str(glob.glob('*.py')))",
    "for f in glob.glob('*.py'): print(f)",
    "print([x for x in glob.glob('*.py')])",
    "print(glob.glob('*.py')[0])",
    "print(glob.glob('*.py')[-1])",
    "print(glob.glob('*.py')[:1])",
    "print(list(glob.glob('*.py')))",
    "print(tuple(glob.glob('*.py')))",
    "print(reversed(glob.glob('*.py')))",
    "print(','.join(glob.glob('*.py')))",
    "print(glob.glob('*.py') == ['a.py', 'b.py'])",
    "print(glob.glob('*.py') != [])",
    "print(glob.glob('*.py') < glob.glob('*'))",
    "print(sorted([glob.glob('*.py'), glob.glob('*')]))",
    "print(json.dumps(glob.glob('*.py')))",
    "print(json.dumps({'k': glob.glob('*.py')}))",
    "print(json.dumps(glob.glob('*.py'), indent=2))",
    "print('%s' % (glob.glob('*.py'),))",
    "print('%r' % (glob.glob('*.py'),))",
    "print('{}'.format(glob.glob('*.py')))",
    "print(f'{glob.glob(chr(42) + str())}')",
    "print(format(glob.glob('*.py'), '>5'))",
    "g = glob.glob('*.py')\ng.sort()\nprint(g)",
    "print(glob.glob('*.py').count('a.py'))",
    "print(glob.glob('*.py').index('a.py'))",
    "print(glob.glob('*.py').pop())",
    "print(glob.glob('*.py').copy())",
    "print(glob.glob('*.py') * 2)",
    "print(glob.glob('*.py') + ('x',))",
    "a, b = glob.glob('*.py')\nprint(a, b)",
    "print([*glob.glob('*.py')])",
    "print(dict.fromkeys(glob.glob('*.py')))",
    "print(next(iter(glob.glob('*.py'))))",
    "print(list(enumerate(glob.glob('*.py'))))",
    "print(list(map(len, glob.glob('*.py'))))",
    "print(list(zip(glob.glob('*.py'), glob.glob('*.py'))))",
    "print(sorted(glob.glob('*.py'), key=len))",
    "print(max(glob.glob('*.py'), key=len))",
    "print(min(glob.glob('*.py'), key=lambda p: 0))",
    "print(bytes(glob.glob('*.py')))",
    # A refusal leaves NOTHING on stdout even when the program printed first —
    # the half of invariant 2 that only ever broke silently.
    "print('hi')\nprint(glob.glob('*.py'))",
    # The names no Rust variant serves. A generator whose repr holds an
    # address, and three functions CPython owns; every one a `module-attr`
    # refusal the router blocks on statically.
    "print(glob.iglob('*.py'))",
    "print(list(glob.iglob('*.py')))",
    "print(glob.translate('*.py'))",
    "print(glob.glob0('.', 'a.py'))",
    "print(glob.glob1('.', '*.py'))",
    "print(glob.magic_check)",
    "print(glob.__all__)",
    # Keyword arguments this engine does not serve, refused rather than dropped
    # — dropping `root_dir` would glob the wrong directory at exit 0.
    "print(glob.glob('*.py', root_dir='d'))",
    "print(glob.glob('*.py', include_hidden=True))",
    "print(glob.glob('*.py', dir_fd=3))",
    "print(glob.glob('*.py', nosuchkw=1))",
    "print(glob.escape('a*', nosuchkw=1))",
    # Every error path: the exception CLASS is right in CPython and the message
    # is not this engine's to write, because it moves between releases.
    "print(glob.glob())",
    "print(glob.glob('a', 'b'))",
    "print(glob.glob(1))",
    "print(glob.glob(None))",
    "print(glob.glob(b'*.py'))",
    "print(glob.has_magic(1))",
    "print(glob.escape(1))",
    # A reversed range is where CPython stops translating the class literally.
    "print(glob.glob('[c-a].py'))",
]]


def _spectrum(binary: Path) -> dict | None:
    """What ``binary`` says it is, or ``None`` if it will not say."""
    try:
        out = subprocess.run([str(binary), "route", "--spectrum"],
                             capture_output=True, text=True, timeout=60)
    except OSError:
        return None
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def _current(engine: str) -> Path | None:
    """A built ``engine`` that carries THIS tree's capability table.

    An installed binary from before this capability landed would answer every
    grid row with a refusal and turn the whole file into green skips measuring
    nothing, so a candidate is taken only if its compiled `route::CAPS` knows
    `cap-glob` and the set it was BUILT with is this tree's.
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
        if any(row.get("cap") == "cap-glob" for row in table.get("caps", [])):
            return cand
    return None


BINARY = _current(engines.LYPNING_L)
CORE = _current(engines.LYPNING)

needs_l = pytest.mark.skipif(
    BINARY is None,
    reason="no lypning-l carrying cap-glob is built (cargo build --release "
           "--no-default-features --features variant-l --target-dir target/variant-l)",
)


def _run(argv: list[str], program: str) -> subprocess.CompletedProcess:
    """One program, over a fresh copy of `TREE` in a temp cwd of its own —
    invariant 4, and the fixture is what the row is actually about."""
    with tempfile.TemporaryDirectory() as d:
        _tree(Path(d))
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
def test_the_glob_grid_agrees_with_cpython(program: str) -> None:
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
        "this program must refuse, not answer — any answer here would be a "
        "silent divergence: %s\n  program: %r\n  stderr: %r"
        % (problem, program, got.stderr.strip()[:200])
    )


@needs_l
def test_a_listing_refuses_while_the_commit_barrier_holds_a_write() -> None:
    """The barrier is invisible to a program asking about ONE path and cannot
    be to one asking for a LISTING.

    `open(p, 'w')` stages until the run ends, so the directory on disk is not
    the directory the program sees. Answering from the disk would print `[]` at
    exit 0 where CPython prints the file the program just made — so it refuses,
    and it refuses BEFORE the write is committed, which is what keeps the
    refusal free."""
    wrote = _run([str(BINARY)], "import glob\nopen('new.py', 'w').write('')\n"
                                "print(sorted(glob.glob('*.py')))")
    assert _refusal_problem(wrote) is None, wrote
    assert "commit barrier" in wrote.stderr, wrote.stderr
    ref = _run([sys.executable], "import glob\nopen('new.py', 'w').write('')\n"
                                 "print(sorted(glob.glob('*.py')))")
    assert ref.returncode == 0 and "new.py" in ref.stdout, ref
    # …and a run that only READS is unaffected, which is what makes the guard a
    # narrow one rather than a switch that turns the capability off.
    read = _run([str(BINARY)], "import glob\nopen('a.py').read()\n"
                               "print(sorted(glob.glob('*.py')))")
    assert (read.returncode, read.stdout) == (0, "['a.py', 'b.py']\n"), read


@needs_l
def test_the_order_refusal_is_the_one_os_listdir_already_gives() -> None:
    """The whole rule in one assertion: the SAME directory order that makes
    `os.listdir()` a refusal makes a multi-path glob result one, and it is
    spelled as its own kind so `conformance --plan` ranks one row for it."""
    got = _run([str(BINARY)], _p("print(glob.glob('*.py'))"))
    assert got.returncode == engines.UNSUPPORTED_EXIT and got.stdout == ""
    assert got.stderr.strip().startswith(
        "%s: unsupported: glob-order: " % engines.LYPNING_L), got.stderr
    assert "filesystem-defined and not reproducible" in got.stderr
    listdir = _run([str(BINARY)], "import os\nprint(os.listdir('.'))")
    assert listdir.stderr.strip().startswith(
        "%s: unsupported: os-listdir: " % engines.LYPNING_L), listdir.stderr
    assert "filesystem-defined and not reproducible" in listdir.stderr


@needs_l
def test_one_path_is_a_plain_list_and_two_are_not() -> None:
    """The line the rule is drawn at, asserted from both sides in one tree:
    the same program, the same directory, one pattern matching once and one
    matching twice."""
    one = _run([str(BINARY)], _p("print(glob.glob('*.txt'))"))
    assert (one.returncode, one.stdout) == (0, "['c.txt']\n"), one
    two = _run([str(BINARY)], _p("print(glob.glob('*.py'))"))
    assert two.returncode == engines.UNSUPPORTED_EXIT and two.stdout == "", two


@needs_l
def test_the_capability_is_on_the_larger_variant_only() -> None:
    """The gate this whole file sits behind: the core must still REFUSE
    `glob`, and must route it to the sibling that serves it.

    A capability that leaked into the frozen variant would still pass every
    grid row above — it is the same code — so the byte budget is defended here,
    by asking each binary what it is."""
    if CORE is None:
        pytest.skip("no core carrying this tree's capability table is built")
    refused = _run([str(CORE)], "import glob")
    assert refused.returncode == engines.UNSUPPORTED_EXIT and refused.stdout == ""
    assert refused.stderr.strip() == engines.refusal_line(
        engines.LYPNING, "module", "import glob")

    # …and the core's ROUTER knows which sibling does serve it, which is the
    # half that makes the refusal cost one spawn instead of a CPython one.
    route = subprocess.run([str(CORE), "route", "-c",
                            "import glob\nprint(len(glob.glob('*.py')))"],
                           capture_output=True, text=True, timeout=60)
    assert route.stdout.split("\t")[0].strip() == engines.LYPNING_L, route.stdout


@needs_l
def test_the_python_copy_of_the_capability_table_is_the_binarys_own() -> None:
    """`engines.VARIANT_CAPS` is a copy of `route::SPECTRUM`'s caps column, and
    a copy is honest only while something checks it."""
    out = subprocess.run([str(BINARY), "route", "--spectrum"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    table = json.loads(out.stdout.strip().splitlines()[-1])
    assert table["self"] == engines.LYPNING_L
    assert "cap-glob" in table["self_caps"]
    assert {r["name"]: tuple(r["caps"]) for r in table["spectrum"]} == engines.VARIANT_CAPS
    assert {r["cap"]: r["modules"] for r in table["caps"]}["cap-glob"] == ["glob"]
