"""`from __future__ import …`, as a grid: every program on the binary and on CPython.

`tests/test_base64_grid.py` is the shape this follows. Every row runs from a
FRESH temp cwd (invariant 4) and must end one of exactly two ways:

  * byte-identical stdout AND the same exit code as CPython 3.x, or
  * a clean refusal: exit 90, nothing on stdout, one
    ``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

**What `cap-future` is.** A future import is a COMPILER directive, not a module
import: CPython decides it before a single statement runs, which is why every
misuse of one is a `SyntaxError` and not an `ImportError`. So `future.rs` is a
pass over the parse, hooked once at the end of `parse::parse()`, and both the
walk and the run see the program it leaves. It strips the leading run of
`from __future__ import` statements (after at most one plain-`str` docstring)
when every name is one whose effect in Python 3 is nothing at all, or is
`annotations`, whose effect is that annotations are never evaluated, which the
pass makes true by clearing every `def`'s annotation list. Anything else
REFUSES the whole program, statically, before anything runs:

  * a name off that list (`braces`, `barry_as_FLUFL`, a misspelling), or an
    alias: CPython's answer is a `SyntaxError`, or a grammar this parser lacks;
  * a `__future__` import anywhere but the top: a `SyntaxError` in CPython even
    inside a `def` that never runs, which a runtime import could never see;
  * the feature's own name, or `__annotations__`, anywhere in the program:
    the import binds a `_Feature` object, and annotations become strings, and
    neither exists here.

The `*args: T` / `**kw: T` annotation, which this parser did not read and
answered as a `SyntaxError` at exit 1 on a program CPython runs, refuses by name
instead, gated to the same capability so the frozen core is untouched.

`test_the_core_routes_*` holds the router to the capability: the CORE's walk
stops on `module: from __future__ import …`, and `route::CAPS` must send that to
lypning-l, while `route::MODULE_ATTRS` keeps the names nothing serves on CPython.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

F = "from __future__ import annotations\n"

#: Rows CPython answers and this capability serves. Each must match CPython
#: byte for byte, stdout and exit code; a refusal skips loudly.
SERVED = [
    F + "print(1)",
    '"""doc"""\n' + F + "print(2)",
    '"""doc"""; from __future__ import annotations\nprint(9)',
    "# comment\n\nfrom __future__ import annotations\nfrom __future__ import division\nprint(6)",
    "from __future__ import (annotations,\n    division)\nprint(4)",
    "from __future__ import (annotations, division,)\nprint(5)",
    "from __future__ import division, print_function, absolute_import, "
    "unicode_literals, generators, nested_scopes, with_statement, "
    "generator_stop, annotations\nprint(1/2)",
    "from __future__ import print_function\nprint('a', 'b', sep='-')",
    "from __future__ import division\nprint(7 / 2, 7 // 2)",
    "from __future__ import unicode_literals\nprint(type('x').__name__)",
    "'''doc'''\nfrom __future__ import generator_stop\nprint(3)",
    "r'''raw doc'''\nfrom __future__ import annotations\nprint(3)",
    # H1: the annotations are NEVER evaluated under the import
    F + "def f(a: Undef) -> Undef2: return a\nprint(f(3))",
    F + 'def f(x: print("hi")) -> print("ret"):\n    return x\nprint(f(2))',
    F + "def f(x: int = 5) -> int:\n    return x\nprint(f(), f(7))",
    # a default is NOT an annotation: it still runs, and in order
    F + "def f(x: print('A') = print('B')):\n    return x\nprint(f())",
    F + "x: Undef = 1\nprint(x)",
    F + "x: list[int] = [1, 2]\ny: dict[str, Undef]\nprint(x)",
    F + "def outer():\n    def inner(x: Nope) -> Nope:\n        return x\n"
        "    return inner(5)\nprint(outer())",
    F + "if True:\n    def f(a: Nope) -> Nope:\n        return a + 1\nprint(f(1))",
    F + "for i in range(2):\n    def f(a: Nope) -> Nope:\n        return a * i\nprint(f(3))",
    F + "while True:\n    def f(a: Nope): return a\n    break\nprint(f(4))",
    F + "try:\n    def f(a: Nope): return a\nexcept Exception:\n    pass\n"
        "else:\n    def g(b: Nope): return b\nfinally:\n    def h(c: Nope): return c\n"
        "print(f(1), g(2), h(3))",
    F + "try:\n    raise ValueError('x')\nexcept ValueError:\n"
        "    def f(a: Nope): return a\nprint(f(8))",
    F + "import sys\n\ndef main(argv: list[str]) -> int:\n    print(len(argv))\n"
        "    return 0\n\nsys.exit(main([]))",
    F + "import json\ndef load(s: str) -> dict[str, Any]:\n    return json.loads(s)\n"
        "print(load('{\"a\": 1}'))",
    F + "import os\ndef f(p: os.PathLike | None = None) -> Optional[str]:\n"
        "    return p\nprint(f())",
    # a string AFTER the imports is an ordinary expression statement
    F + '"""not a docstring"""\nprint(3)',
    # the program's own error, after the pass, is the program's
    F + "print(undefined_name)",
    F + "print(1)\nraise SystemExit(3)",
    F + "f = lambda *a, **k: (a, k)\nprint(f(1, x=2))",
    F + "def f(x: int = 5, *a, **k) -> None:\n    print(x, a, k)\nf()",
    # a name that CONTAINS a feature name is not the name
    F + "annotations_seen = 1\nprint(annotations_seen)",
    F + "my_division = 2\nprint(my_division)",
    # H6: `*a: T` / `**k: T`. Refused by name on lnext/future; the parser has
    # read them since lnext/correctness, so they are answers now
    F + "def g(x: int = 5, *a: int, **k: str) -> None:\n    print(x, a, k)\ng()",
    "def g(*a: int):\n    return a\nprint(g(1))",
    "def g(**k: str):\n    return k\nprint(g(a='1'))",
]

#: Rows the capability declines. CPython answers some of them and raises a
#: SyntaxError on the rest; either way the answer is CPython's to give, and the
#: binary must refuse cleanly rather than guess.
REFUSED = [
    # H3: placement is decided at compile time
    "import os\nfrom __future__ import annotations\nprint(3)",
    "x = 1\nfrom __future__ import annotations\nprint(x)",
    "def f():\n    from __future__ import annotations\nprint(7)",
    "if True:\n    from __future__ import annotations\nprint(1)",
    "try:\n    from __future__ import annotations\nexcept ImportError:\n    pass\nprint(1)",
    "print(8)\nfrom __future__ import nosuch",
    '"""a"""\n"""b"""\nfrom __future__ import annotations\nprint(1)',
    'b"x"\nfrom __future__ import annotations\nprint(1)',
    'f"x"\nfrom __future__ import annotations\nprint(1)',
    F + "print(1)\nfrom __future__ import division",
    # H7: names off the no-op list
    "from __future__ import annotations, nosuch\nprint(1)",
    "from __future__ import braces",
    "from __future__ import barry_as_FLUFL\nprint(1 <> 2)",
    "from __future__ import barry_as_FLUFL\nprint(1)",
    "from __future__ import *\nprint(1)",
    # H4: the import binds a `_Feature`, which does not exist here
    F + "print(annotations)",
    "from __future__ import annotations as ann\nprint(ann)",
    "from __future__ import division\nprint(division)",
    F + "print(f'{annotations}')",
    F + "def f(annotations=1):\n    return annotations\nprint(f())",
    # H5: annotations become strings, and nothing here holds them
    F + "x: int = 1\nprint(__annotations__)",
    F + "def f(a: int): pass\nprint(f.__annotations__)",
    # `import __future__` is a real module CPython imports
    "import __future__\nprint(1)",
    "import __future__\nprint(__future__.annotations)",
    F + "import __future__\nprint(1)",
]

#: The CORE routes these to lypning-l: its walk stops on
#: `module: from __future__ import …`, which `route::CAPS` gives to cap-future.
ROUTED_TO_LYPNING_L = [
    F + "print(1)",
    '"""doc"""\n' + F + "print(2)",
    "from __future__ import division, print_function, absolute_import, "
    "unicode_literals, generators, nested_scopes, with_statement, "
    "generator_stop, annotations\nprint(1/2)",
    F + "def f(a: Undef) -> Undef2: return a\nprint(f(3))",
]

#: And these to CPython: a name `route::MODULE_ATTRS` does not list, or a
#: later import nothing on the spectrum serves.
ROUTED_TO_CPYTHON = [
    "from __future__ import braces",
    "from __future__ import nosuch\nprint(1)",
    "from __future__ import barry_as_FLUFL\nprint(1)",
    "from __future__ import annotations, nosuch\nprint(1)",
    F + "import subprocess\nprint(1)",
]


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


def _current(engine: str, cap: str) -> Path | None:
    """A built ``engine`` that carries THIS tree's capability table; an older
    binary would answer every row with a refusal and measure nothing."""
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
        if any(row.get("cap") == cap for row in table.get("caps", [])):
            return cand
    return None


BINARY = _current(engines.LYPNING_L, "cap-future")
CORE = _current(engines.LYPNING, "cap-future")

needs_l = pytest.mark.skipif(
    BINARY is None,
    reason="no lypning-l carrying cap-future is built (lypning build --rust)",
)
needs_core = pytest.mark.skipif(
    CORE is None or BINARY is None,
    reason="routing needs the CORE that routes and the lypning-l it routes to",
)


def _run(argv: list[str], program: str) -> subprocess.CompletedProcess:
    """One program, in a temp cwd of its own — invariant 4."""
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
@pytest.mark.parametrize("program", SERVED, ids=range(len(SERVED)))
def test_the_future_grid_agrees_with_cpython(program: str) -> None:
    got = _run([str(BINARY)], program)
    if got.returncode == engines.UNSUPPORTED_EXIT:
        # A refusal is always allowed and never a bug, but it must be CLEAN and
        # it is reported: a row that started refusing stopped measuring.
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
def test_what_the_pass_declines_refuses_rather_than_guesses(program: str) -> None:
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got)
    assert problem is None, (
        "this program must refuse, not answer: %s\n  program: %r\n  stderr: %r"
        % (problem, program, got.stderr.strip()[:200])
    )


@needs_l
@pytest.mark.parametrize("program", REFUSED, ids=range(len(REFUSED)))
def test_the_variant_walk_sends_every_declined_row_to_cpython(program: str) -> None:
    """lypning-l's own router agrees with its run: a row it refuses is never a
    row it would have routed to itself."""
    route = engines.route(program, binary=BINARY)
    assert route.engine == engines.CPYTHON, (
        "lypning-l routes %r to %r, and then refuses it" % (program, route.engine))


@needs_core
@pytest.mark.parametrize("program", ROUTED_TO_LYPNING_L, ids=range(len(ROUTED_TO_LYPNING_L)))
def test_the_core_routes_a_served_future_import_to_lypning_l(program: str) -> None:
    route = engines.route(program, binary=CORE)
    assert route.engine == engines.LYPNING_L, (
        "routed to %r, not lypning-l\n  program: %r\n  blocker: %s: %s"
        % (route.engine, program, route.kind, route.detail)
    )
    assert _run([str(BINARY)], program).returncode == 0


@needs_core
@pytest.mark.parametrize("program", ROUTED_TO_CPYTHON, ids=range(len(ROUTED_TO_CPYTHON)))
def test_the_core_routes_an_unserved_future_import_to_cpython(program: str) -> None:
    route = engines.route(program, binary=CORE)
    assert route.engine == engines.CPYTHON, (
        "routed to %r, which cannot run it\n  program: %r\n  blocker: %s: %s"
        % (route.engine, program, route.kind, route.detail)
    )


#: PEP 649: from 3.14 CPython never evaluates a `def`'s annotations, with or
#: without `from __future__ import annotations`, so EVERY served head must
#: leave them unevaluated (verifier round 1: `division` et al. raised
#: `NameError` at exit 1, or printed an annotation's side effect). The bytes
#: are CPython 3.14.5's, pinned; a binary built against an older or an
#: unmeasured reference must refuse these rather than answer them.
_NOT_ANNOTATIONS = ["absolute_import", "division", "generator_stop", "generators",
                    "nested_scopes", "print_function", "unicode_literals",
                    "with_statement"]
PEP649 = [("from __future__ import %s\ndef g(a: X) -> Y: return a\nprint(g(2))" % f, "2\n")
          for f in _NOT_ANNOTATIONS] + [
    ('from __future__ import print_function\ndef g(a: print("ann")): return a\nprint(g(3))',
     "3\n"),
    ('from __future__ import division\ndef f(x: print("A") = print("B")):\n    return x\n'
     "if 1:\n    def h(a: Nope) -> Nope: return a\nprint(f(), h(4))", "B\nNone 4\n"),
]


@needs_l
@pytest.mark.parametrize("program,stdout", PEP649, ids=range(len(PEP649)))
def test_no_served_head_evaluates_an_annotation_on_314(program: str, stdout: str) -> None:
    got = _run([str(BINARY)], program)
    if got.returncode == engines.UNSUPPORTED_EXIT:
        problem = _refusal_problem(got)
        assert problem is None, "%s\n  program: %r" % (problem, program)
        if sys.version_info >= (3, 14):
            pytest.fail("lypning-l refuses a PEP 649 row on a 3.14 reference: %s"
                        % got.stderr.strip()[:160])
        pytest.skip("reference predates 3.14: %s" % got.stderr.strip()[:160])
    if sys.version_info < (3, 14):
        pytest.skip("CPython 3.14.5 bytes; this reference is older")
    assert (got.stdout, got.returncode) == (stdout, 0), (program, got.stdout, got.stderr)
    ref = _run([sys.executable], program)
    assert (ref.stdout, ref.returncode) == (stdout, 0)


#: CPython NFKC-folds identifiers, so a fullwidth spelling of a feature name IS
#: the feature name and uses the `_Feature` binding (verifier round 2:
#: lypning-l answered `no` / NameError at exit 1). This lexer does not fold, so
#: under a head any non-ASCII identifier refuses. CPython 3.14.5 bytes, pinned.
NFKC = [
    ("from __future__ import division\ntry:\n    \uff44ivision\nexcept NameError:\n"
     "    print('no')\nelse:\n    print('yes')", "yes\n"),
    ("from __future__ import division\nprint(\uff44ivision)",
     "_Feature((2, 2, 0, 'alpha', 2), (3, 0, 0, 'alpha', 0), 131072)\n"),
    ("from __future__ import annotations\nprint(\uff41nnotations)",
     "_Feature((3, 7, 0, 'beta', 1), None, 16777216)\n"),
    ("from __future__ import print_function\nprint(\uff50rint_function)",
     "_Feature((2, 6, 0, 'alpha', 2), (3, 0, 0, 'alpha', 0), 1048576)\n"),
    ("from __future__ import division\nprint(f'{\uff44ivision}')",
     "_Feature((2, 2, 0, 'alpha', 2), (3, 0, 0, 'alpha', 0), 131072)\n"),
]


@needs_l
@pytest.mark.parametrize("program,stdout", NFKC, ids=range(len(NFKC)))
def test_a_non_ascii_identifier_under_a_head_refuses(program: str, stdout: str) -> None:
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got)
    assert problem is None, "%s\n  program: %r\n  stdout: %r" % (problem, program, got.stdout)
    assert engines.route(program, binary=BINARY).engine == engines.CPYTHON
    if sys.version_info[:2] == (3, 14):
        ref = _run([sys.executable], program)
        assert (ref.stdout, ref.returncode) == (stdout, 0)


@needs_core
@pytest.mark.parametrize("program,stdout", PEP649[:1], ids=[0])
def test_the_core_routes_a_pep649_row_to_lypning_l(program: str, stdout: str) -> None:
    assert engines.route(program, binary=CORE).engine == engines.LYPNING_L


def test_the_grid_rows_are_what_cpython_says_they_are() -> None:
    """The SERVED rows must be programs CPython actually compiles: a row that is
    a SyntaxError on the reference would pin a refusal as an answer."""
    for program in SERVED:
        compile(program, "<grid>", "exec")
