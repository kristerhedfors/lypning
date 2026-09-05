"""The `re` SURFACE on lypning-l, as a grid: every program on the binary and on CPython.

There is no matcher in this capability. What lypning-l serves is the module, the
flag constants as a value with CPython's repr, `re.escape`, `re.purge`, and the
NAMES of the nine matcher-backed functions — which refuse at call time, cleanly,
so the chain hands the program to CPython. `tests/test_pathlib_grid.py` is the
shape this follows: the defect is PER PROGRAM, not per function, and it is the
one three capabilities in a row shipped — a new value reached through a path
with no arm for it, answering exit 1 where CPython answers, which the chain
never retries.

The measurement this is built on (corpus mine of 2026-09-04, 3,525 entries
loaded): 248 programs were blocked on `module: import re` for lypning-l, 213 of
them with no other static blocker, and of those 174 fail identically on both
engines BEFORE their first regex call, 94 never call a matcher at all, and 25
ever reach one. Admitting the names is what the 174 need. Quote the counts a
conformance run prints, with its date, not these.

Every row must end one of exactly two ways: byte-identical stdout AND the same
exit code as CPython 3.x, or a clean refusal — exit 90, nothing on stdout, one
``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

The traps, each measured on CPython 3.11.15, 3.12.13, 3.13.13 and 3.14.5 before
the code was written, each with rows below:

1. **An `IntFlag` is an int.** `re.I == 2`, `re.I + 1`, `{re.I: 1}[2]`,
   `sorted([re.M, re.I])`, `'ab' * re.I`, `[0, 1, 2][re.I]` and
   `isinstance(re.I, int)` all answer as for the int, and every arithmetic
   operator returns a PLAIN int (`-re.I` is `-2`). Only `| & ^` stay flags.
2. **`str()` of a flag is its repr.** `print(re.I)`, `f'{re.I}'`, `'%s' % re.I`,
   `'%5s' % re.I` and `format(re.I)` all print `re.IGNORECASE`, on every
   CPython. A non-empty format spec MOVED — `int.__format__` from 3.11 on, the
   padded repr on 3.9 and 3.10 — so `format()`, f-string specs and the numeric
   `%` conversions are refused here; `%s`/`%r` with a width are `str()`/`repr()`
   then padding, never `__format__`, and answer.
3. **The repr's member order is DECLARATION order.** `re.I | re.A` prints
   `re.ASCII|re.IGNORECASE`; residue bits print as one `0x…` after the names,
   and a value with no named member prints as `re.RegexFlag(512)`.
4. **The TEMPLATE bit** (`re.I | 1`, `re.I | True`, `re.T`, `re.TEMPLATE`, and
   `~re.I` which sets it) is spelled differently by 3.12 and 3.13+: refused.
5. **`Flag` is a container** — `len(re.I)`, `list(re.I | re.M)`,
   `re.I in re.I | re.M`, `re.I.name`, `re.I.value` are answered by CPython
   and refused here rather than raised at exit 1.

And the rule the whole file exists for: every matcher call — `re.search`,
`re.compile`, `re.sub`, through any spelling of the import — must refuse
CLEANLY. `print('hi')` before the call is the row that holds the commit barrier.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

R = "import re\n"

#: The import in every spelling, a module that is only ever imported, and the
#: 174-shape: a program that fails identically on both engines BEFORE its first
#: regex call. The `open()` row is that shape — FileNotFoundError at exit 1 on
#: both, from the fresh temp cwd — and it must MATCH, not refuse.
IMPORTS = [
    R + "print('ok')",
    "import re as regex\nprint('ok')",
    "from re import I\nprint(I)",
    "from re import escape, purge\nprint(escape('a.b'), purge())",
    "import re, sys\nprint(len(sys.argv))",
    R + "x = re\nprint(x is re, re is re)",
    "import re\nimport re\nprint('twice')",
    R + "s = open('src/eval.rs').read()\nprint(len(re.findall(r'fn ', s)))",
    R + "import sys\nprint(sys.argv[3])",
    R + "print(1)\n",
]

#: Traps 1, 2 and 3: every constant by both names, the flag algebra, the int
#: half, and the flag in every container and formatting path that prints it.
FLAGS = [R + "print(%s)" % n for n in (
    "re.I", "re.IGNORECASE", "re.M", "re.MULTILINE", "re.S", "re.DOTALL", "re.X", "re.VERBOSE",
    "re.A", "re.ASCII", "re.U", "re.UNICODE", "re.L", "re.LOCALE", "re.DEBUG", "re.NOFLAG",
)] + [R + x for x in [
    "print(re.I|re.M, re.I|8, 8|re.I, re.I|re.M|re.S|re.X|re.A, re.I|512, re.I|re.M|1024, "
    "re.I|re.L, re.I|re.DEBUG, re.I|re.U)",
    "print(re.I&re.M, re.I&3, re.I&re.I, (re.I|re.M)&re.M, re.I&0, re.NOFLAG|re.NOFLAG, "
    "re.I|0, 0|re.I)",
    "print(re.I^re.M, (re.I|re.M)^re.M, re.I^2)",
    "print(int(re.I), float(re.M), bool(re.NOFLAG), bool(re.I), abs(re.I), -re.I, +re.I, "
    "round(re.I))",
    "print(re.I==2, re.I!=2, re.I==re.I, re.I==2.0, re.NOFLAG==0, re.NOFLAG==False, re.I==True, "
    "re.I<re.M, re.M>re.I, re.I<=2, re.I>=3, re.I==re.IGNORECASE)",
    "print(re.I+1, re.I+re.M, re.I*2, re.I-1, re.I/2, re.I//2, re.I%3, re.I**2, re.I<<1, "
    "re.M>>1)",
    "print(sorted([re.M, re.I, re.S]), max(re.I, re.M), min(re.I, re.M), sum([re.I, re.M]))",
    "print(str(re.I), repr(re.I), f'{re.I}', f'{re.I!r}', f'{re.I!s}', '%s' % re.I, "
    "'%r' % re.I, format(re.I))",
    # `%s`/`%r` with a width or precision are `str()`/`repr()` then padding —
    # never `__format__` — so they answer, exactly, on every CPython.
    "print('%5s' % re.I, '%-20s|' % re.I, '%20r' % re.I, '%.3s' % re.I, '%s=%s' % (re.I, re.M))",
    "print({re.I: 1}[2], {2: 1}[re.I], re.I in (2,), 2 in {re.I}, re.I in {2}, re.I in [re.I], "
    "{re.I: 1}, len({re.I, 2}), [re.I, re.M], (re.I,), {'f': re.M})",
    "d = {re.I: 'a', 2: 'b'}\nprint(d, len(d))",
    "print(re.I is re.IGNORECASE, re.I is 2, re.I is None, re.I is not None, "
    "(re.I|re.M) is (re.I|re.M))",
    "print(isinstance(re.I, int), isinstance(re.I, (str, int)), isinstance(re.I, str), "
    "isinstance(re.I, bool))",
    "f = re.I\nf |= re.M\nprint(f)\nf &= re.M\nprint(f)\ng = 0\ng |= re.S\nprint(g)",
    # `pow` is not a lypning builtin, so this row refuses on it (a skip); the
    # row after it is the same builtins without `pow`, so they are measured.
    "print(range(re.I), 'ab'*re.I, [0,1,2][re.I], chr(re.I+63), hex(re.I), bin(re.M), "
    "oct(re.X), divmod(re.M, 3), pow(re.I, 2), bytes(re.I))",
    "print(range(re.I), 'ab'*re.I, [0,1,2][re.I], chr(re.I+63), hex(re.I), bin(re.M), "
    "oct(re.X), divmod(re.M, 3), bytes(re.I), re.I*'ab', [0]*re.I, 'abc'[re.I:], "
    "round(re.I, -1), abs(re.NOFLAG))",
    "import json\nprint(json.dumps(re.I), json.dumps([re.I, {'f': re.M}]), "
    "json.dumps({re.I: 1}))",
    "print(not re.NOFLAG, re.I and 1, re.NOFLAG or 'z', re.I if re.I else 0)",
    "flags = re.M | re.S\nprint(flags, int(flags), flags & re.M, flags & re.I)",
    "print([re.I, re.M].index(8), [re.I, re.M].count(2), (re.I, re.M) == (2, 8))",
    "print(re.I | re.M | re.S | re.X | re.A | re.U | re.DEBUG | re.L, re.I & True, "
    "re.I | False, re.NOFLAG | 2**31)",
]]

#: `re.escape`, whose escaped set is exactly CPython 3.7+'s: the printable-ASCII
#: row is the trap-grid row verbatim, and non-ASCII copies through.
ESCAPE = [R + x for x in [
    "print([re.escape(chr(i)) for i in range(32, 127)])",
    "print(re.escape('a1_\\u00e9-'), re.escape(''), repr(re.escape('\\x00\\x07\\x7f\\x80\\xa0')), "
    "re.escape('h\\u00e9llo'), re.escape('a.b*c?'))",
    "print(repr(re.escape('\\t\\n\\r\\v\\f')), repr(re.escape('\\\\')), re.escape('a b'), "
    "len(re.escape('()')), re.escape('x' * 100).count('x'))",
    "print(re.escape(str(1)), re.escape('\\u65e5\\u672c\\u8a9e \\u6f22'), "
    "re.escape('!\\\"%\\',/:;<=>@_`'))",
    "print(re.escape('a') + re.escape('.'), re.escape('.').encode(), 'x' in re.escape('x.y'))",
    # The row above with `\x80` in it refuses on the engine's own `repr-unicode`
    # (U+0080's printability is CPython's table, not ours); this one measures the
    # same code points without asking for their repr.
    "print(repr(re.escape('\\x00\\x07\\x7f')), re.escape('\\x80\\xa0') == '\\x80\\xa0', "
    "len(re.escape('\\x80\\xa0')), re.escape('\\x80\\xa0').encode())",
]]

PURGE = [
    R + "print(re.purge())",
    R + "re.purge()\nprint('done')",
    R + "print(re.purge() is None)",
]

GRID = IMPORTS + FLAGS + ESCAPE + PURGE

#: Everything that must refuse rather than answer: the nine matcher-backed
#: functions in every spelling; every module attribute outside the surface;
#: the flag's attributes, `~`, format specs, container protocol and non-int
#: partners; the bad `escape`/`purge` calls; and the refusals the engine
#: already had (`type`, `dir`, `hash`, `sys.modules`).
REFUSED = [R + x for x in [
    "print(re.search('a', 'a'))", "print(re.match('a', 'a'))", "print(re.fullmatch('a', 'a'))",
    "print(re.findall('a', 'a'))", "print(list(re.finditer('a', 'a')))", "print(re.compile('a'))",
    "print(re.sub('a', 'b', 'a'))", "print(re.subn('a', 'b', 'a'))", "print(re.split('a', 'bab'))",
    "p = re.compile\nprint(p('a'))", "print(re.search('a', 'a', re.I))",
    "print(re.findall(pattern='a', string='a'))",
    # The commit barrier: a print BEFORE the refusing call leaves nothing on stdout.
    "print('hi')\nprint(re.search('a', 'a'))",
    "s = 'x'\nprint(len(s))\nm = re.search('x', s)\nprint(m)",
    "print(re.error)", "print(re.PatternError)", "print(re.T)", "print(re.TEMPLATE)",
    "print(re.RegexFlag)", "print(re.Pattern)", "print(re.Match)", "print(re.Scanner)",
    "print(re.template('a'))", "print(re.__version__)", "print(re.nosuchthing)",
    "print(re.I.name)", "print(re.I.value)", "print(re.I.bit_length())", "print(re.I.__class__)",
    "print(re.I.real)",
    "print(~re.I)", "print(~re.NOFLAG)",
    "print(f'{re.I:>20}')", "print(format(re.I, 'd'))", "print('%d' % re.I)", "print('%x' % re.I)",
    "print('%c' % re.I)",
    "print(len(re.I))", "print(list(re.I|re.M))", "for f in re.I: print(f)",
    "print(re.I in re.I|re.M)", "print(sorted(re.I))",
    "print(re.I|1)", "print(re.I|True)", "print(re.I|'a')", "print(re.I|2.0)", "print(re.I&None)",
    "print(re.I|-1)", "print(re.I | 2**32)",
    "print(re.escape(b'a.'))", "print(re.escape(1))", "print(re.escape())",
    "print(re.escape('a', 'b'))", "print(re.escape(pattern='a'))", "print(re.purge(1))",
    "print(re.purge(x=1))",
    "print(type(re.I))", "print(type(re.I).__name__)", "print(dir(re))", "print(hash(re.I))",
    "try:\n    re.compile('(')\nexcept re.error as e:\n    print(e)",
    "import sys\nprint(sys.modules['re'])",
]] + [
    "from re import search\nprint(search('a', 'a'))",
    "import re as r\nprint(r.findall('a', 'a'))",
    "from re import error\nprint(error)",
    "from re import T\nprint(T)",
]


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
    `route::CAPS` knows `cap-re`, and the capability set it was BUILT with
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
        if any(row.get("cap") == "cap-re" for row in table.get("caps", [])):
            return cand
    return None


BINARY = _current(engines.LYPNING_L)
CORE = _current(engines.LYPNING)

needs_l = pytest.mark.skipif(
    BINARY is None,
    reason="no lypning-l carrying cap-re is built (cargo build --release "
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
def test_the_re_grid_agrees_with_cpython(program: str) -> None:
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
    """The gate this whole file sits behind: the core must still REFUSE `re`,
    and must route it to the sibling that serves it.

    A capability that leaked into the frozen variant would still pass every grid
    row above — it is the same code — so the byte budget is defended here, by
    asking each binary what it is."""
    core = CORE
    if core is None:
        pytest.skip("no core carrying this tree's capability table is built")
    refused = _run([str(core)], "import re")
    assert refused.returncode == engines.UNSUPPORTED_EXIT and refused.stdout == ""
    assert refused.stderr.strip() == engines.refusal_line(
        engines.LYPNING, "module", "import re")

    # …and the core's ROUTER knows which sibling does serve it, which is the
    # half that makes the refusal cost one spawn instead of a CPython one.
    route = subprocess.run([str(core), "route", "-c",
                            'import re\nprint(re.escape("a."))'],
                           capture_output=True, text=True, timeout=60)
    assert route.stdout.split("\t")[0].strip() == engines.LYPNING_L, route.stdout


@needs_l
def test_a_runtime_refusal_after_reading_stdin_replays_it_to_the_next_rung() -> None:
    """The corpus's largest cluster is `stdin -> transform -> stdout`, and with
    `re` admitted its regex half now routes to lypning-l, reads the pipe, and
    refuses at the matcher. The core's `run` forks that rung; before this was
    pinned it forked it with the INHERITED pipe, so CPython was then handed an
    exhausted stream and answered about nothing at exit 0 — the mixture-rust
    arm's `stdin-regex-extract` and `stdin-replace-sed` MISMATCHes of
    2026-09-05. The Python dispatcher reads a piped stdin once and replays it
    to every rung; this holds the Rust one to the same rule."""
    core = CORE
    if core is None:
        pytest.skip("no core carrying this tree's capability table is built")
    env = dict(os.environ, LYPNING_L_BIN=str(BINARY), LYPNING_CPYTHON=sys.executable)
    program = "import sys, re\nfor line in sys.stdin:\n    m = re.search(r'id=(\\d+)', line)\n    if m:\n        print(m.group(1))"
    got = subprocess.run([str(core), "run", "-c", program], input="x id=41 y\nnope\nz id=7\n",
                         capture_output=True, text=True, timeout=60, env=env)
    assert (got.stdout, got.returncode) == ("41\n7\n", 0), (got.stdout, got.stderr[-300:])
    program = "import sys, re\nsys.stdout.write(re.sub(r'foo+', 'BAR', sys.stdin.read()))"
    got = subprocess.run([str(core), "run", "-c", program], input="foo fooo food\n",
                         capture_output=True, text=True, timeout=60, env=env)
    assert (got.stdout, got.returncode) == ("BAR BAR BARd\n", 0), (got.stdout, got.stderr[-300:])


@needs_l
def test_a_re_method_name_is_admitted_only_for_a_program_that_imports_re() -> None:
    """`.group`, `.span`, `.start` are ordinary names on other objects.

    Admitting `known_method("group")` for every receiver would take a program
    this engine sends to CPython today — which answers it — and run it here
    instead, where it stops at an `AttributeError`: exit 1, the program's own
    exit, which the chain never retries. The import is what makes the router's
    optimism honest, and this is the assertion that says so."""
    without = subprocess.run([str(BINARY), "route", "-c", "x=1\nprint(x.group())"],
                             capture_output=True, text=True, timeout=60)
    assert without.stdout.split("\t")[0].strip() == engines.CPYTHON, without.stdout
    with_import = subprocess.run(
        [str(BINARY), "route", "-c", "import re\nx=1\nprint(x.group())"],
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
    assert "cap-re" in table["self_caps"]
    assert {r["name"]: tuple(r["caps"]) for r in table["spectrum"]} == engines.VARIANT_CAPS
    assert {r["cap"]: r["modules"] for r in table["caps"]}["cap-re"] == ["re"]
