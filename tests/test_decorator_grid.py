"""Decorators, as a grid: every program on the binary and on CPython.

`tests/test_binascii_grid.py` is the shape this follows. Every row runs from a
FRESH temp cwd (invariant 4) and must end one of exactly two ways:

  * byte-identical stdout AND the same exit code as CPython, or
  * a clean refusal — exit 90, nothing on stdout, one
    ``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

lypning-l serves ``@<expr>`` lines before a ``def`` under ``cap-future``: the
decorator expressions run top to bottom BEFORE the defaults, the function is
built, the decorators are applied bottom to top, and the name is bound once.
The core's parser refuses ``decorator`` at the first ``@``, so every such
program is HELD from its first statement (``route::arm_hold``): its uncaught
errors refuse as ``name-hint``, a binding ``TypeError`` refuses as ``call``
(its message names the function by a qualified name that differs by minor),
and a function used as a set element or dict key refuses as
``iterator-identity``.
Anything but a ``def`` after the decorators, and any compile-time error after
the first ``@``, refuses as ``decorator`` — never the engine's own
``SyntaxError``, because the core stopped at the ``@`` and exits 90 there.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

D = "def d(f):\n    return f\n"

#: CPython answers every one of these, and lypning-l must answer it too.
SERVED = [
    # Evaluation order: the decorator expression runs BEFORE the default.
    "def say(m, v):\n    print(m)\n    return v\n@say('deco', lambda f: f)\n"
    "def g(a=say('default', 1)):\n    return a\nprint(g())",
    # A factory, stacked: evaluated top to bottom, applied bottom to top.
    "def deco(t):\n    def w(f):\n        def g(*a): return t + str(f(*a))\n        return g\n"
    "    return w\n@deco('<')\n@deco('>')\ndef h(x): return x*2\nprint(h(3))",
    "def deco(t):\n    print('eval', t)\n    def w(f):\n        print('apply', t)\n        return f\n"
    "    return w\n@deco(1)\n@deco(2)\n@deco(3)\ndef h(): return 'h'\nprint(h())",
    # The name is bound ONCE, after the last decorator: a decorator reads the old one.
    "g = 1\ndef d(f):\n    print('sees', g)\n    return f\n@d\ndef g(): pass\nprint(g())",
    "def d(f):\n    return 3\n@d\ndef f(): pass\nprint(f + 1)",
    # Blank and comment lines between decorator and def; a comment on the line.
    D + "@d\n\n# c\ndef f(): return 1\nprint(f())",
    D + "@d  # a comment\ndef h(): return 2\nprint(h())",
    D + "@d\n@d\n\n\n@d\ndef f(): return 'x'\nprint(f())",
    # PEP 614: any expression.
    "@(lambda f: 5)\ndef h(): pass\nprint(h)",
    "@lambda f: 4\ndef h(): pass\nprint(h)",
    "d = [lambda f: 7]\n@d[0]\ndef h(): pass\nprint(h)",
    "x = [1]\n@x.append\ndef f(): pass\nprint(len(x), f)",
    "t = {'k': lambda f: f}\n@t['k']\ndef f(): return 'k'\nprint(f())",
    # Inside a block and inside a function.
    D + "if True:\n    @d\n    def f(): return 9\nprint(f())",
    D + "def outer():\n    @d\n    def inner(x): return x + 1\n    return inner(1)\nprint(outer())",
    # A `*a, **k` wrapper passes a keyword through.
    "def twice(f):\n    def inner(*a, **k):\n        f(*a, **k)\n        return f(*a, **k)\n"
    "    return inner\n@twice\ndef hi(n, sep='!'):\n    print('hi', n, sep=sep)\nhi(1, sep='?')",
    # A registry keyed by a string, a counter in a closure.
    "REG = {}\ndef reg(name):\n    def w(f):\n        REG[name] = f\n        return f\n    return w\n"
    "@reg('a')\ndef a(): return 1\n@reg('b')\ndef b(): return 2\n"
    "print(sorted(REG), REG['a']() + REG['b']())",
    "def count(f):\n    n = [0]\n    def w(*a):\n        n[0] += 1\n        return f(*a), n[0]\n    return w\n"
    "@count\ndef sq(x): return x * x\nprint(sq(2), sq(3))",
    # A decorator that raises leaves the name unbound — caught, then checked.
    "def bad(f):\n    raise ValueError('x')\ntry:\n    @bad\n    def k(): pass\nexcept ValueError as e:\n"
    "    print('caught', e)\ntry:\n    k\nexcept NameError:\n    print('unbound')",
    # ... or at its OLD value.
    "k = 'old'\ndef bad(f):\n    raise ValueError('x')\ntry:\n    @bad\n    def k(): pass\n"
    "except ValueError:\n    pass\nprint(k)",
    # `from __future__ import annotations` clears a decorated def's annotations.
    "from __future__ import annotations\n" + D + "@d\ndef f(a: undefined) -> nope: pass\nprint('ok')",
    # A served import in a decorated program.
    "import itertools\n" + D + "@d\ndef f(): return list(itertools.product([1], [2]))\nprint(f())",
    "import textwrap\n" + D + "@d\ndef f(): return textwrap.dedent('  a')\nprint(f())",
    # The matrix operator and `@=` are still expressions.
    "x = 3\ntry:\n    x @ 4\nexcept TypeError as e:\n    print(e)",
    "x = 3\ntry:\n    x @= 4\nexcept TypeError as e:\n    print(e)",
    # A decorator called with the function returns anything.
    "def d(f):\n    return [f, f]\n@d\ndef f(): return 1\nprint(len(f), f[0]() + f[1]())",
]

#: A decorator reading the def's own local name inside a function, CPython's
#: UnboundLocalError; and an earlier read of the name. Same exit, same stdout.
SERVED += [
    "def outer():\n    @d\n    def d(): pass\ntry:\n    outer()\nexcept UnboundLocalError:\n    print('ule')",
    "g = 1\n" + D + "def outer():\n    try:\n        print(g)\n    except UnboundLocalError:\n"
    "        print('ule')\n    @d\n    def g(): pass\nouter()",
]

#: Exit 90 and an EMPTY stdout: not a def after the decorators, a decorator not
#: ended by a newline, a compile-time error after the first `@`, and every
#: runtime shape a held run refuses.
REFUSED = [
    "@d def f(): pass",
    "@d\nx = 1",
    "@d\nclass C: pass",
    "@a, b\ndef h(): pass",
    "@\ndef f(): pass",
    "@d\n",
    D + "@d;\ndef f(): pass",
    D + "@d\nasync def f(): pass",
    D + "@d\n    def f(): pass",
    "@x := (lambda f: 3)\ndef h(): pass\nprint(h)",
    # a compile-time error after the `@`: CPython's SyntaxError, the core's 90
    "print('x')\n" + D + "@d\ndef f(a, a): pass",
    D + "@d\ndef f(): return 1\nprint(1 +)",
    D + "def outer():\n    @d\n    def g(): pass\n    global g\nouter()",
    D + "def outer():\n    @d\n    def g(): pass\n    global d\nouter()",
    D + "def outer():\n    @d\n    def g(): pass\n    nonlocal d\nouter()",
    # held: a binding TypeError, caught or not
    D + "@d\ndef f(a): return a\ntry:\n    f(1, 2)\nexcept TypeError as e:\n    print(e)",
    D + "@d\ndef f(a): return a\ntry:\n    f()\nexcept TypeError as e:\n    print(e)",
    D + "@d\ndef f(a): return a\ntry:\n    f(b=1)\nexcept TypeError as e:\n    print(e)",
    D + "@d\ndef f(a): return a\ntry:\n    f(1, a=1)\nexcept TypeError as e:\n    print(e)",
    D + "@d\ndef f(a, /): return a\ntry:\n    f(a=1)\nexcept TypeError as e:\n    print(e)",
    D + "def o():\n    @d\n    def f(a): return a\n    return f\ntry:\n    o()(1, 2)\nexcept TypeError as e:\n    print(e)",
    "def twice(f):\n    def inner(*a, **k):\n        return f(*a, **k)\n    return inner\n"
    "@twice\ndef hi(n): return n\ntry:\n    hi(1, x=2)\nexcept TypeError as e:\n    print(e)",
    # held: `*` over a non-iterable names the callee in CPython
    D + "@d\ndef f(): return 1\ntry:\n    f(*1)\nexcept TypeError as e:\n    print(e)",
    # held: a function is hashable, by identity, in CPython
    D + "@d\ndef f(): return 1\nprint(f in {f})",
    "REG = {}\ndef reg(f):\n    REG[f] = 1\n    return f\n@reg\ndef a(): pass\nprint(len(REG))",
    "seen = set()\ndef reg(f):\n    seen.add(f)\n    return f\n@reg\ndef a(): pass\nprint(len(seen))",
    D + "@d\ndef f(): pass\nprint(hash(f) == hash(f))",
    # held: an uncaught error CPython may end with a suggestion
    D + "@d\ndef f(): pass\nundefined_name",
    "def bad(f):\n    raise ValueError('x')\ntry:\n    @bad\n    def k(): pass\nexcept ValueError:\n"
    "    pass\nprint(k)",
    # runtime decorators this engine does not have
    "@staticmethod\ndef f(): pass\nprint(1)",
    "@classmethod\ndef f(): pass\nprint(1)",
    "@property\ndef f(): pass\nprint(1)",
    "@print\ndef f(): pass\nprint(f)",
    D + "@d\ndef f(): pass\nprint(f.__name__)",
    # a generator under a decorator is still a generator
    D + "@d\ndef g():\n    yield 1\nprint(list(g()))",
]

#: A held program that writes and then refuses leaves the cwd as it found it.
AFTER_A_WRITE = [
    "import os\nos.mkdir('NEWD')\nopen('NEWD/f', 'w').write('x')\n" + D
    + "@d\ndef f(a): return a\ntry:\n    f(1, 2)\nexcept TypeError:\n    pass",
    "open('F', 'w').write('x')\n" + D + "@d\ndef f(): pass\nprint({f})",
]

#: Routed by the CORE into lypning-l: its parser stopped at `@`, and every
#: import is one lypning-l serves.
ROUTED_TO_LYPNING_L = [
    D + "@d\ndef g(): return 1\nprint(g())",
    "import textwrap\n" + D + "@d\ndef g(): return 1\nprint(g())",
    "import os, json\n" + D + "@d\ndef g(): return json.dumps(os.sep)\nprint(g())",
    # LATE: a decorated class, which the core never reads past the `@`, and a
    # runtime decorator lypning-l does not have. Each refuses there, cleanly.
    D + "@d\nclass C: pass",
    "@staticmethod\ndef f(): pass",
]
#: Routed past lypning-l: an import nobody on the spectrum serves.
ROUTED_PAST_LYPNING_L = [
    "import functools\n@functools.lru_cache\ndef f(x): return x\nprint(f(1))",
    "import contextlib\n@contextlib.contextmanager\ndef tag(n):\n    yield\nwith tag('a'):\n    print('b')",
    "import subprocess\n" + D + "@d\ndef g(): return 1",
]


def _spectrum(binary: Path) -> dict | None:
    try:
        out = subprocess.run([str(binary), "route", "--spectrum"],
                             capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError):
        return None


def _current(engine: str) -> Path | None:
    """A built ``engine`` whose table has ``cap-future`` answer ``decorator`` —
    an older binary would turn every row into a green skip measuring nothing."""
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
        if any(r.get("cap") == "cap-future" and "decorator" in r.get("kinds", [])
               for r in table.get("caps", [])):
            return cand
    return None


BINARY = _current(engines.LYPNING_L)
CORE = _current(engines.LYPNING)

needs_l = pytest.mark.skipif(
    BINARY is None, reason="no lypning-l serving decorators is built (lypning build --rust)")
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


@needs_l
@pytest.mark.parametrize("program", SERVED, ids=range(len(SERVED)))
def test_the_served_rows_answer_exactly_what_cpython_answers(program: str) -> None:
    """Served means ANSWERED: a refusal here is a row that stopped measuring."""
    got = _run([str(BINARY)], program)
    ref = _run([sys.executable], program)
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "lypning-l disagrees with CPython.\n  program:  %r\n"
        "  lypning-l: %r exit %d %s\n  cpython:   %r exit %d %s"
        % (program, got.stdout, got.returncode, got.stderr.strip()[-200:],
           ref.stdout, ref.returncode, ref.stderr.strip()[-200:]))


@needs_l
@pytest.mark.parametrize("program", REFUSED, ids=range(len(REFUSED)))
def test_what_is_outside_the_slice_refuses_cleanly(program: str) -> None:
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got)
    assert problem is None, "this program must refuse, not answer: %s\n  program: %r" % (
        problem, program)


@needs_l
@pytest.mark.parametrize("program", AFTER_A_WRITE, ids=range(len(AFTER_A_WRITE)))
def test_a_held_refusal_takes_the_run_back(program: str) -> None:
    with tempfile.TemporaryDirectory() as d:
        got = subprocess.run([str(BINARY), "-c", program], capture_output=True,
                             text=True, cwd=d, timeout=60)
        left = sorted(os.listdir(d))
    problem = _refusal_problem(got)
    assert problem is None, "%s\n  program: %r" % (problem, program)
    assert left == [], "the refusal left the run's writes behind: %r" % left


@needs_core
@pytest.mark.parametrize("program", ROUTED_TO_LYPNING_L, ids=range(len(ROUTED_TO_LYPNING_L)))
def test_the_core_routes_a_decorated_def_into_lypning_l(program: str) -> None:
    route = engines.route(program, binary=CORE)
    assert (route.engine, route.kind) == (engines.LYPNING_L, "decorator"), (
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
