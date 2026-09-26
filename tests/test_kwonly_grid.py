"""Keyword-only parameters, as a grid: every program on the binary and on CPython.

`tests/test_binascii_grid.py` is the shape this follows. Every row runs from a
FRESH temp cwd (invariant 4) and must end one of exactly two ways:

  * byte-identical stdout AND the same exit code as CPython, or
  * a clean refusal — exit 90, nothing on stdout, one
    ``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

lypning-l serves ``def f(a, *, b, c=1)``, ``def f(*args, k, **kw)`` and the
same forms in ``lambda`` under ``cap-future``: a keyword fills a keyword-only
name, a missing one takes its default, and neither star's own name is ever
filled by a keyword. The core's parser refuses ``kwonly``, so every such
program is HELD from its first statement (``route::arm_hold``) — wherever the
parameter list sits, an f-string field included — and every binding
``TypeError`` in it refuses as ``call``: CPython names the function by a
qualified name that differs by minor, counts every missing argument, and
adds ``Did you mean`` from 3.13. Every compile-time error on the keyword-only
path refuses as ``kwonly``: 3.9 says ``invalid syntax`` where 3.14 says
``/ must be ahead of *``.

`tests/test_param_grid.py`'s whole shape-by-call grid runs here on lypning-l
too, in both spellings: its two keyword-only shapes are refusals on the core
and answers here.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

from test_param_grid import CASES, _programs

#: CPython answers every one of these, and lypning-l must answer it too.
SERVED = [
    "def g(a, *, b, c=1): return a, b, c\nprint(g(1, b=2), g(1, c=3, b=4))",
    "def f(*, a=1, b): return a, b\nprint(f(b=2))",
    "def m(a, *c, d, **e): return a, c, d, e\nprint(m(1, 2, 3, d=4, z=5))",
    "def h(*args, **kw): return args, kw\nprint(h(args=1))",
    "def h(*args, k, **kw): return args, k, kw\nprint(h(1, k=2, args=3, kw=4))",
    "def f(*a, b=1, c): return a, b, c\nprint(f(1, c=2))",
    "print((lambda *, k=5: k)(), (lambda *a, k: (a, k))(1, 2, k=3))",
    'print(f"{(lambda *, a: a)(a=1)}")',
    "def f(*, a=[]):\n    a.append(1)\n    return a\nprint(f(), f(), f(a=[9]))",
    "def f(x, *, key=None, reverse=False):\n    return sorted(x, key=key, reverse=reverse)\n"
    "print(f([3, 1, 2], reverse=True), f(['b', 'A'], key=str.lower))",
    "def f(a, /, b, *, c): return a, b, c\nprint(f(1, 2, c=3), f(1, b=2, c=3))",
    "def f(*, a): return a\nprint(f(**{'a': 1}))",
    "def f(*, a, **k): return a, k\nprint(f(a=1, b=2), f(**{'a': 3, 'z': 0}))",
    "def decide(a, *, exclude=None, **kw): return exclude, kw\nprint(decide(1, exclude=2, q=3))",
    "x = [lambda *, k=i: k for i in range(3)]\nprint([g() for g in x])",
    "def outer():\n    def f(*, a): return a\n    return f(a=5)\nprint(outer())",
    # Defaults: positional first, then keyword-only, both in source order.
    "def f(*, k=print('kd')): return k\nprint(f())",
    "def f(a=print('pd'), *, k=print('kd')): pass",
    "def f(a=print('pd'), *b, k=print('kd'), j=print('jd')): pass\nprint('end')",
    # With a decorator whose wrapper passes the keyword through.
    "def dec(f):\n    def w(*a, **k): return f(*a, **k)\n    return w\n"
    "@dec\ndef g(x, *, y=2): return x + y\nprint(g(1), g(1, y=5))",
    # The corpus fragments (py-324a964ef811, py-538bf93c4ed8): only defs.
    "def report(cmp, *, before: str, after: str, limit: int = 5) -> None:\n    pass\nprint('ok')",
    "def decide(a, b=None, *, exclude=None, **kw):\n    return a\nprint(decide(1, exclude=[2]))",
]

#: Exit 90 and an EMPTY stdout.
REFUSED = [
    # every SyntaxError on the keyword-only path
    "def f(*): pass",
    "f = lambda *: 0",
    "def f(*, **k): pass",
    "def f(*, a, /): pass",
    "def f(*, *, a): pass",
    "def f(*, a, *b): pass",
    "def f(a=1, b, *, c): pass",
    "def f(*,): pass",
    "def f(*, a, a): pass",
    "def f(a, *, a): pass",
    "def f(*, a, **k, b): pass",
    "print(1)\ndef f(*a, b, *c): pass",
    # held: every binding TypeError, caught or not
    "def g(a, *, b): pass\ntry:\n    g(1, 2)\nexcept TypeError as e:\n    print(e)",
    "def g(a, *, b): pass\ntry:\n    g(1)\nexcept TypeError as e:\n    print(e)",
    "def g(a, *, b): pass\ng(1)",
    "def g(*, b): pass\ntry:\n    g(c=1)\nexcept TypeError as e:\n    print(e)",
    "def f(a, /, b, *, c): return a\ntry:\n    f(a=1, b=2, c=3)\nexcept TypeError as e:\n    print(e)",
    "def f(*, a): return a\nprint(f(a=1))\ndef o():\n    def f(a): return a\n    return f\n"
    "try:\n    o()(1, 2)\nexcept TypeError as e:\n    print(e)",
    # held: an uncaught error CPython may end with a suggestion
    "def f(*, a):\n    return a\nprint(f(a=1))\nundefined_name",
    "x = (lambda *, k=0: k)()\nx.foo",
]

#: Routed by the CORE into lypning-l: its parser stopped on `kwonly`.
ROUTED_TO_LYPNING_L = [
    "def g(a, *, b): return a + b\nprint(g(1, b=2))",
    "def g(*a, k): return a, k\nprint(g(1, k=2))",
    'print(f"{(lambda *, a: a)(a=1)}")',
    "import textwrap\ndef g(s, *, w=4): return textwrap.dedent(s)\nprint(g(' a'))",
]
#: Routed past lypning-l: an import nobody on the spectrum serves.
ROUTED_PAST_LYPNING_L = [
    "from typing import Dict, Any\ndef report(cmp: Dict[str, Any], *, before: str) -> None: pass",
    "import subprocess\ndef f(*, a): return a",
]


def _spectrum(binary: Path) -> dict | None:
    try:
        out = subprocess.run([str(binary), "route", "--spectrum"],
                             capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError):
        return None


def _current(engine: str) -> Path | None:
    """A built ``engine`` whose table has ``cap-future`` answer ``kwonly``."""
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
        if any(r.get("cap") == "cap-future" and "kwonly" in r.get("kinds", [])
               for r in table.get("caps", [])):
            return cand
    return None


BINARY = _current(engines.LYPNING_L)
CORE = _current(engines.LYPNING)

needs_l = pytest.mark.skipif(
    BINARY is None, reason="no lypning-l serving keyword-only parameters is built")
needs_core = pytest.mark.skipif(
    CORE is None or BINARY is None,
    reason="routing needs the CORE that routes and the lypning-l it routes to")


def _run(argv: list[str], program: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True, text=True,
                              cwd=d, timeout=60)


def _refusal_problem(got: subprocess.CompletedProcess) -> str | None:
    if got.returncode != engines.UNSUPPORTED_EXIT:
        return "exit %d, not %d" % (got.returncode, engines.UNSUPPORTED_EXIT)
    if got.stdout != "":
        return "stdout was not empty: %r" % got.stdout[:120]
    head = "%s: unsupported: " % engines.LYPNING_L
    line = got.stderr.strip()
    if not line.startswith(head) or "\n" in line:
        return "stderr was %r, expected one %r line" % (line[:160], head)
    return None


def _agrees(program: str) -> None:
    got = _run([str(BINARY)], program)
    ref = _run([sys.executable], program)
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "lypning-l disagrees with CPython.\n  program:  %r\n"
        "  lypning-l: %r exit %d %s\n  cpython:   %r exit %d %s"
        % (program, got.stdout, got.returncode, got.stderr.strip()[-200:],
           ref.stdout, ref.returncode, ref.stderr.strip()[-200:]))


@needs_l
@pytest.mark.parametrize("program", SERVED, ids=range(len(SERVED)))
def test_the_served_rows_answer_exactly_what_cpython_answers(program: str) -> None:
    """Served means ANSWERED: a refusal here is a row that stopped measuring."""
    _agrees(program)


@needs_l
@pytest.mark.parametrize("program", REFUSED, ids=range(len(REFUSED)))
def test_what_is_outside_the_slice_refuses_cleanly(program: str) -> None:
    problem = _refusal_problem(_run([str(BINARY)], program))
    assert problem is None, "this program must refuse, not answer: %s\n  program: %r" % (
        problem, program)


@needs_l
@pytest.mark.parametrize("case_id,params,body,call", CASES, ids=[c[0] for c in CASES])
def test_the_parameter_grid_answers_or_refuses_on_lypning_l(
        case_id: str, params: str, body: str, call: str) -> None:
    """Both spellings; a keyword-only shape CPython binds is ANSWERED."""
    outcomes = []
    for program in _programs(params, body, call):
        got = _run([str(BINARY)], program)
        ref = _run([sys.executable], program)
        if got.returncode == engines.UNSUPPORTED_EXIT:
            assert _refusal_problem(got) is None, (program, got.stderr)
            assert not (case_id.startswith("kwonly") and ref.returncode == 0), (
                "a keyword-only binding CPython makes was refused: %r" % program)
            outcomes.append("REFUSED")
            continue
        assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
            program, got.stderr[-200:], ref.stderr[-200:])
        outcomes.append((got.stdout, got.returncode))
    assert outcomes[0] == outcomes[1], "a lambda and a def disagree: %r" % (outcomes,)


@needs_core
@pytest.mark.parametrize("program", ROUTED_TO_LYPNING_L, ids=range(len(ROUTED_TO_LYPNING_L)))
def test_the_core_routes_a_keyword_only_signature_into_lypning_l(program: str) -> None:
    route = engines.route(program, binary=CORE)
    assert (route.engine, route.kind) == (engines.LYPNING_L, "kwonly"), (
        route.engine, route.kind, route.detail)
    core = _run([str(CORE)], program)
    assert core.returncode == engines.UNSUPPORTED_EXIT and core.stdout == "", core


@needs_core
@pytest.mark.parametrize("program", ROUTED_PAST_LYPNING_L,
                         ids=range(len(ROUTED_PAST_LYPNING_L)))
def test_the_core_routes_what_no_rung_serves_to_cpython(program: str) -> None:
    route = engines.route(program, binary=CORE)
    assert route.engine == engines.CPYTHON, (route.engine, route.kind, route.detail)
    assert _refusal_problem(_run([str(BINARY)], program)) is None
