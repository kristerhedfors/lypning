"""Latent MISMATCHes in shared code, as a grid on EVERY variant.

These rows came from reading the engine rather than from the corpus. None of
them was in the conformance battery, so every gate was green while they were
wrong. Each row runs on both `lypning` and `lypning-l`, from a fresh temp cwd
(invariant 4). It must end in one of two ways:

  * ``ANSWERED``: the same stdout and exit code as CPython. The expected bytes
    are pinned below as CPython 3.14.5 printed them on 2026-09-24, and the row
    is also run against the interpreter running this test. A refusal FAILS
    these rows, because each one is a fix, and a fix that quietly turned into a
    refusal is a regression.
  * ``REFUSED``: a clean refusal. Exit 90, nothing on stdout, one ``<engine>:
    unsupported: …`` line on stderr (invariant 2). Each of these rows exited 1
    before, or answered wrongly at exit 0.

What was wrong, one line each:

  * PEP 479. A StopIteration escaping a generator expression's body was read as
    exhaustion. CPython raises ``RuntimeError('generator raised
    StopIteration')``.
  * A genexp that advances itself answered with exhaustion instead of
    ``ValueError('generator already executing')``. The stand-in that fills the
    cell is marked ``done``, and ``done`` was checked first.
  * StopIteration raised by the function ``map()`` or ``filter()`` calls ENDS
    that iterator in CPython. Here it propagated as an exception.
  * A bare exception (``raise ValueError``, ``next()`` on an exhausted
    iterator) had ``args == ('',)``. CPython has ``()``. ``StopIteration.value``
    was an AttributeError at exit 1. The empty message now means "no
    arguments", so ``ValueError('')`` refuses, and so does an ``assert`` whose
    message is not a non-empty str, but only where a handler could catch it.
  * A comprehension target rebound a name the enclosing function declared
    ``global``.
  * A generator expression resolved names against the frame that CONSUMED it
    rather than the one that created it, which gave an UnboundLocalError.
  * ``float('-nan')`` lost its sign bit.
  * ``def f(*a: int, **k: str)`` was a SyntaxError at exit 1.

Not fixed, and recorded here so that nobody fixes it by accident: on 3.14,
annotations are evaluated lazily (PEP 649). This engine still evaluates them
when the ``def`` runs, so an annotation with a side effect or a NameError
disagrees with a 3.14 reference. Whether to follow the reference's minor
version is a separate decision.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

#: (program, CPython 3.14.5 stdout, CPython exit)
ANSWERED = [
    # --- PEP 479 -----------------------------------------------------------
    ("try:\n    print(list(next(iter([])) for x in [1]))\n"
     "except RuntimeError as e:\n    print('RT', e)\nexcept StopIteration:\n    print('SI')",
     "RT generator raised StopIteration\n", 0),
    ("try:\n    print(sum(next(iter([])) for x in [1]))\n"
     "except RuntimeError as e:\n    print('RT', e)\nexcept StopIteration:\n    print('SI')",
     "RT generator raised StopIteration\n", 0),
    ("g=(next(iter([])) for x in [1])\ntry:\n    next(g)\nexcept RuntimeError as e:\n"
     "    print('RT', e)\ntry:\n    next(g)\nexcept StopIteration as e:\n    print('SI2', e.args)",
     "RT generator raised StopIteration\nSI2 ()\n", 0),
    ("try:\n    print(list(x for x in (next(iter([])) for y in [1])))\n"
     "except RuntimeError as e:\n    print('RT', e)",
     "RT generator raised StopIteration\n", 0),
    ("g=(next(iter([])) for x in [1,2])\ntry:\n    list(g)\nexcept RuntimeError as e:\n"
     "    print('RT')\nprint(list(g))",
     "RT\n[]\n", 0),
    ("def f(x):\n    raise StopIteration('boom')\ntry:\n    print(list(f(x) for x in [1]))\n"
     "except RuntimeError as e:\n    print('RT', e)",
     "RT generator raised StopIteration\n", 0),
    ("try:\n    print(any(next(iter([])) for x in [1]))\nexcept RuntimeError as e:\n    print('RT', e)",
     "RT generator raised StopIteration\n", 0),
    ("print(list(next(iter([])) for x in [1]))", "", 1),
    # A default to next() is not a StopIteration at all.
    ("g=(next(iter([]), 1) for x in [1])\nprint(list(g))", "[1]\n", 0),
    # --- a generator that advances itself ------------------------------------
    ("def h(g):\n    return next(g)\ng=None\ng=(h(g) for x in [1,2])\ntry:\n"
     "    print(list(g))\nexcept ValueError as e:\n    print('VE', e)",
     "VE generator already executing\n", 0),
    # --- StopIteration inside map()/filter() is the end of the iterator ------
    ("it=iter([1])\ntry:\n    print(list(map(lambda x: next(it), [1,2,3])))\n"
     "except StopIteration:\n    print('SI')",
     "[1]\n", 0),
    ("it=iter([1])\nfor v in map(lambda x: next(it), [1,2,3]):\n    print(v)\nprint('end')",
     "1\nend\n", 0),
    ("it=iter([])\nprint(list(filter(lambda x: next(it), [1,2])))", "[]\n", 0),
    ("it=iter([])\ntry:\n    next(map(lambda x: next(it), [1]))\n"
     "except StopIteration as e:\n    print('SI', e.args)",
     "SI ()\n", 0),
    ("it=iter([1])\nprint(list(zip(map(lambda x: next(it), [1,2,3]), 'abc')))",
     "[(1, 'a')]\n", 0),
    ("it=iter([])\ntry:\n    sorted([1,2], key=lambda x: next(it))\nexcept StopIteration:\n    print('SI')",
     "SI\n", 0),
    # --- a bare exception's args ---------------------------------------------
    ("try:\n    raise ValueError\nexcept ValueError as e:\n    print(e.args, repr(e), str(e)=='')",
     "() ValueError() True\n", 0),
    ("try:\n    next(iter([]))\nexcept StopIteration as e:\n    print(e.args, e.value, repr(e))",
     "() None StopIteration()\n", 0),
    ("try:\n    raise StopIteration('v')\nexcept StopIteration as e:\n    print(e.value, e.args)",
     "v ('v',)\n", 0),
    ("try:\n    raise ValueError\nexcept ValueError as e:\n    print('[%s]' % e, len(e.args))",
     "[] 0\n", 0),
    ("try:\n    assert False\nexcept AssertionError as e:\n    print(e.args, repr(e))",
     "() AssertionError()\n", 0),
    ("try:\n    assert False, 'm'\nexcept AssertionError as e:\n    print(e.args, repr(e))",
     "('m',) AssertionError('m')\n", 0),
    # Uncaught, only `str()` of the message is shown, so a non-str one answers.
    ("print(1)\nassert 1 == 2, 5", "1\n", 1),
    ("def f():\n    assert 0, (1, 2)\nf()", "", 1),
    ("try:\n    pass\nexcept Exception:\n    pass\nassert 0, ''", "", 1),
    ("try:\n    raise ValueError('x')\nexcept ValueError as e:\n    print(e.args, repr(e))",
     "('x',) ValueError('x')\n", 0),
    ("try:\n    try:\n        raise KeyError\n    except KeyError:\n        raise\n"
     "except KeyError as e:\n    print(repr(e), str(e)=='')",
     "KeyError() True\n", 0),
    # --- comprehension targets and `global` ----------------------------------
    ("v='OUT'\ndef h():\n    global v\n    [v for v in [1]]\nh()\nprint(v)", "OUT\n", 0),
    ("v='OUT'\ndef h():\n    global v\n    s={v for v in [1]}\n    d={v:1 for v in [2]}\n"
     "    g=list(v for v in [3])\n    return s,d,g\nprint(h())\nprint(v)",
     "({1}, {2: 1}, [3])\nOUT\n", 0),
    ("a='A'\nb='B'\ndef h():\n    global a\n    return [(a,b) for a,b in [(1,2)]]\nprint(h(), a, b)",
     "[(1, 2)] A B\n", 0),
    ("v='OUT'\ndef h():\n    global v\n    return list(v for v in [1,2])\nprint(h(), v)",
     "[1, 2] OUT\n", 0),
    ("v='OUT'\ng=(v for v in [1,2])\ndef h():\n    global v\n    return list(g)\nprint(h(), v)",
     "[1, 2] OUT\n", 0),
    ("k=10\ndef h():\n    global k\n    k=11\n    return [k+i for i in [1]]\nprint(h(), k)",
     "[12] 11\n", 0),
    ("v='OUT'\n[v for v in [1]]\nprint(v)", "OUT\n", 0),
    # --- a generator resolves names in the frame that made it ----------------
    ("x=5\ng=(x for _ in [1])\ndef f():\n    r=list(g)\n    x=1\n    return r\nprint(f())",
     "[5]\n", 0),
    ("def mk():\n    y=7\n    return (y+i for i in [1,2])\ndef use(g):\n    y=0\n"
     "    return list(g)\nprint(use(mk()))",
     "[8, 9]\n", 0),
    ("y=3\ndef h():\n    global y\n    g=(y for _ in [1])\n    y=4\n    return list(g)\nprint(h(), y)",
     "[4] 4\n", 0),
    # --- float('-nan') -------------------------------------------------------
    ("import math\nprint(math.copysign(1.0, float('-nan')))\n"
     "print(math.copysign(1.0, float('nan')))\nprint(float('-nan'))",
     "-1.0\n1.0\nnan\n", 0),
    ("import math\nprint(math.copysign(1.0, float('-NaN')), math.copysign(1.0, float('  -nan  ')))",
     "-1.0 -1.0\n", 0),
    ("x=float('-nan')\nprint(x, repr(x), x!=x, f'{x}', '%f' % x)", "nan nan True nan nan\n", 0),
    # --- star-parameter annotations ------------------------------------------
    ("def f(*a: int, **k: str):\n    return a, k\nprint(f(1,2,x='y'))",
     "((1, 2), {'x': 'y'})\n", 0),
    ("def f(a: int, *b: str, **c: float) -> None:\n    print(a, b, c)\nf(1, 'x', k=2.0)",
     "1 ('x',) {'k': 2.0}\n", 0),
    ("print((lambda *a, **k: (a, k))(1, z=2))", "((1,), {'z': 2})\n", 0),
]

#: Valid Python that exited 1 (or answered wrongly) and must now refuse.
REFUSED = [
    # genexp attributes: AttributeError at exit 1 where CPython answers
    "g=(x for x in [1])\nprint(g.close())\nprint(list(g))",
    "g=(x for x in [1])\nprint(g.send(None))",
    "g=(x for x in [1])\nprint(g.gi_running)",
    "g=(x for x in [1])\ng.throw(ValueError)",
    # starred items after the first in a display, and in a bare tuple
    "a=[1,2]\nprint([0,*a])",
    "a=[1,2]\nprint((0,*a))",
    "a=[1,2]\nprint({0,*a})",
    "la=[1,2]\nprint('%s %s %s' % (0,*la))",
    "a=[1,2]\nb=0,*a\nprint(b)",
    "def f(a):\n    return 0, *a\nprint(f([1]))",
    "a=[1]\nfor x in 0, *a:\n    print(x)",
    "[a, *b] = [1,2,3]\nprint(a, b)",
    # an exception whose `args` the flat (kind, message) value cannot carry
    "try:\n    raise ValueError('')\nexcept ValueError as e:\n    print(e.args, repr(e))",
    "s=''\ntry:\n    raise ValueError(s)\nexcept ValueError as e:\n    print(repr(e))",
    "try:\n    assert False, ''\nexcept AssertionError as e:\n    print(e.args, repr(e))",
    "try:\n    assert False, 5\nexcept AssertionError as e:\n    print(e.args, repr(e))",
    # caught through a call: the `try` is dynamic, not lexical
    "def f():\n    assert 0, 5\ntry:\n    f()\nexcept AssertionError as e:\n    print(e.args)",
    # StopIteration WITH an argument inside map(): `next()` would re-raise it
    "def f(x):\n    raise StopIteration('m')\nprint(list(map(f, [1])))",
    # `from __future__` inside a def: CPython's own SyntaxError, never exit 1 here
    "def f():\n    from __future__ import annotations\n    return 1\nprint(f())",
]

#: Rows the CORE's static walk must send to CPython. The walk can see them, so
#: a program must not start on a variant and die there.
ROUTED_TO_CPYTHON = [
    "a=[1,2]\nb=0,*a\nprint(b)",
    "def f(a):\n    return 0, *a\nprint(f([1]))",
    "a=[1,2]\nprint([0,*a])",
    "la=[1,2]\nprint('%s %s' % (0,*la))",
]


def _find(engine: str) -> Path | None:
    target = {engines.LYPNING: paths.RUST_DIR / "target" / "release" / "lypning",
              engines.LYPNING_L: paths.RUST_DIR / "target" / "variant-l" / "release" / "lypning"}
    found = engines.find(engine)
    for cand in ([Path(found)] if found else []) + [target[engine]]:
        if cand.is_file():
            return cand
    return None


VARIANTS = [(e, _find(e)) for e in (engines.LYPNING, engines.LYPNING_L)]
BUILT = [pytest.param(e, b, id=e) for e, b in VARIANTS if b is not None]
CORE = dict(VARIANTS).get(engines.LYPNING)


def _run(argv: list[str], program: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True, text=True,
                              cwd=d, timeout=60)


def _refusal_problem(engine: str, got: subprocess.CompletedProcess) -> str | None:
    if got.returncode != engines.UNSUPPORTED_EXIT:
        return "exit %d, not %d" % (got.returncode, engines.UNSUPPORTED_EXIT)
    if got.stdout != "":
        return "stdout was not empty: %r" % got.stdout[:120]
    line = got.stderr.strip()
    head = "%s: unsupported: " % engine
    if not line.startswith(head) or "\n" in line:
        return "stderr was %r, expected one %r line" % (line[:160], head)
    return None


def test_both_variants_are_measured() -> None:
    """A variant that is not built is a hole, never a pass (CLAUDE.md, §C12)."""
    missing = [e for e, b in VARIANTS if b is None]
    if missing:
        pytest.skip("not built: %s" % ", ".join(missing))


@pytest.mark.parametrize("engine,binary", BUILT)
@pytest.mark.parametrize("program,stdout,code", ANSWERED, ids=range(len(ANSWERED)))
def test_the_fixed_rows_answer_what_cpython_answers(
        engine: str, binary: Path, program: str, stdout: str, code: int) -> None:
    got = _run([str(binary)], program)
    assert (got.stdout, got.returncode) == (stdout, code), (
        "%s disagrees with the pinned CPython bytes.\n  program: %r\n"
        "  got:     %r exit %d %s\n  pinned:  %r exit %d"
        % (engine, program, got.stdout, got.returncode, got.stderr.strip()[-200:],
           stdout, code)
    )


@pytest.mark.parametrize("program,stdout,code", ANSWERED, ids=range(len(ANSWERED)))
def test_the_pinned_bytes_are_this_cpythons_bytes(program: str, stdout: str, code: int) -> None:
    ref = _run([sys.executable], program)
    assert (ref.stdout, ref.returncode) == (stdout, code), (
        "CPython %s prints something else for this row: %r exit %d\n  program: %r"
        % (sys.version.split()[0], ref.stdout, ref.returncode, program)
    )


@pytest.mark.parametrize("engine,binary", BUILT)
@pytest.mark.parametrize("program", REFUSED, ids=range(len(REFUSED)))
def test_the_unserved_rows_refuse_cleanly(engine: str, binary: Path, program: str) -> None:
    got = _run([str(binary)], program)
    problem = _refusal_problem(engine, got)
    assert problem is None, "%s\n  program: %r" % (problem, program)


@pytest.mark.skipif(CORE is None, reason="the core that routes is not built")
@pytest.mark.parametrize("program", ROUTED_TO_CPYTHON, ids=range(len(ROUTED_TO_CPYTHON)))
def test_the_walk_sends_a_starred_display_to_cpython(program: str) -> None:
    route = engines.route(program, binary=CORE)
    assert route.engine == engines.CPYTHON, (
        "routed to %r\n  program: %r\n  blocker: %s: %s"
        % (route.engine, program, route.kind, route.detail)
    )
