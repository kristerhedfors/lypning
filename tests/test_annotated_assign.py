"""Annotated assignment (`x: T`, `x: T = v`, `obj.a: T`, `d[k]: T`) on both engines.

A shared-parser fix, so every row runs on the frozen core AND on lypning-l, and
again on lypning-l under a served `from __future__` head — the path that made
these reachable where a head used to send the program to CPython. The bytes are
CPython 3.14.5's, pinned: stdout, exit code and the last stderr line.

What CPython does and this parser used to drop:

  * a bare `x: int` in a def makes `x` LOCAL without binding it, so a read is
    UnboundLocalError, not the global of the same name;
  * `obj.a: T` / `d[k]: T` with no value still evaluates the object and the
    subscript;
  * a tuple, list, starred or call target is a SyntaxError;
  * `del x` in a def also makes `x` local (UnboundLocalError), unless `global`.

What refuses instead (exit 90, one stderr line, empty stdout): an annotated
name that is also `global` (CPython's wording depends on uses this parser does
not track), a nested scope beside a bare annotated local (this evaluator
resolves the free name through the global), an annotated slice with no value,
and `*a, 2` as a VALUE, which the evaluator never spliced.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile

import pytest

from lypning import engines

HEAD = "from __future__ import division\n"

#: (program, stdout, exit, last stderr line) — CPython 3.14.5, pinned.
SERVED = [
    ('x = 5\ndef f():\n    x: int\n    return x\nprint(f())',
     '', 1, "UnboundLocalError: cannot access local variable 'x' where it is not associated with a value"),
    ('x = 9\ndef f():\n    if False:\n        x: int\n    return x\ntry:\n    print(f())\nexcept NameError as e:\n    print(type(e).__name__)',
     'UnboundLocalError\n', 0, ''),
    ("def f():\n    x: int\n    try:\n        return x\n    except UnboundLocalError:\n        return 'UB'\nprint(f())",
     'UB\n', 0, ''),
    ('x = 9\ndef f():\n    x: int\n    return [x for _ in [1]]\nprint(f())',
     '', 1, "UnboundLocalError: cannot access local variable 'x' where it is not associated with a value"),
    ('x = 9\ndef f():\n    x: int\n    del x\nf()',
     '', 1, "UnboundLocalError: cannot access local variable 'x' where it is not associated with a value"),
    ('x = 9\ndef f():\n    (x): int\n    return x\nprint(f())',
     '9\n', 0, ''),
    ('def f(x):\n    x: int\n    return x\nprint(f(3))',
     '3\n', 0, ''),
    ('def f():\n    x: int\n    x = 4\n    return x\nprint(f())',
     '4\n', 0, ''),
    ('x: int\nprint(2)',
     '2\n', 0, ''),
    ("d = {}\nd[print('tgt') or 0]: int\nprint(d)",
     'tgt\n{}\n', 0, ''),
    ("print('a').x: int\nprint(1)",
     'a\n1\n', 0, ''),
    ('undefined_obj.attr: int\nprint(1)',
     '', 1, "NameError: name 'undefined_obj' is not defined"),
    ('l = [1]\nl[1/0]: int\nprint(1)',
     '', 1, 'ZeroDivisionError: division by zero'),
    ('d = {}\nd[1, print(2)]: int\nprint(d)',
     '2\n{}\n', 0, ''),
    ("d = {}\nd['k']: int = 5\nprint(d)",
     "{'k': 5}\n", 0, ''),
    ('(a, b): int = 1, 2\nprint(1)',
     '', 1, 'SyntaxError: only single target (not tuple) can be annotated'),
    ('a, b: int = 1, 2\nprint(1)',
     '', 1, 'SyntaxError: only single target (not tuple) can be annotated'),
    ('[a]: int = [1]\nprint(1)',
     '', 1, 'SyntaxError: only single target (not list) can be annotated'),
    ('*a: int = [1]\nprint(1)',
     '', 1, 'SyntaxError: invalid syntax'),
    ('f(): int = 1\nprint(1)',
     '', 1, 'SyntaxError: illegal target for annotation'),
    ('def f():\n    global x\n    (x): int = 3\nf()\nprint(x)',
     '3\n', 0, ''),
    ('def f():\n    del x\nf()',
     '', 1, "UnboundLocalError: cannot access local variable 'x' where it is not associated with a value"),
    ('x = 1\ndef f():\n    print(x)\n    del x\nf()',
     '', 1, "UnboundLocalError: cannot access local variable 'x' where it is not associated with a value"),
    ("x = 1\ndef f():\n    global x\n    del x\nf()\ntry:\n    print(x)\nexcept NameError:\n    print('gone')",
     'gone\n', 0, ''),
    ('def f():\n    global x\n    del x\nf()',
     '', 1, "NameError: name 'x' is not defined"),
]
REFUSED = [
    ('def f():\n    global y\n    y: int\nprint(1)',
     '', 1, "SyntaxError: annotated name 'y' can't be global"),
    ('def f():\n    x: int = 1\n    global x\nprint(1)',
     '', 1, "SyntaxError: annotated name 'x' can't be global"),
    ('x: int = 1\nglobal x\nprint(1)',
     '', 1, "SyntaxError: annotated name 'x' can't be global"),
    ('x = 9\ndef f():\n    x: int\n    return (lambda: x)()\nprint(f())',
     '', 1, "NameError: cannot access free variable 'x' where it is not associated with a value in enclosing scope"),
    ('x = 9\ndef f():\n    x: int\n    def g():\n        return x\n    return g()\nprint(f())',
     '', 1, "NameError: cannot access free variable 'x' where it is not associated with a value in enclosing scope"),
    ("x = 9\ndef f():\n    x: int\n    return f'{(lambda: x)()}'\nprint(f())",
     '', 1, "NameError: cannot access free variable 'x' where it is not associated with a value in enclosing scope"),
    ("l = [1, 2, 3]\nl[print('a'):print('b')]: int\nprint(1)",
     'a\nb\n1\n', 0, ''),
    ('a = [1]\nx: list = *a, 2\nprint(x)',
     '(1, 2)\n', 0, ''),
    ('a = [1]\nx = *a, 2\nprint(x)',
     '(1, 2)\n', 0, ''),
    ('def f():\n    return *[1], 2\nprint(f())',
     '(1, 2)\n', 0, ''),
    ('for v in *[1], 2:\n    print(v)',
     '1\n2\n', 0, ''),
    ('x = (1,)\nx += *[2], 3\nprint(x)',
     '(1, 2, 3)\n', 0, ''),
]


def _binary(engine: str) -> str:
    found = engines.find(engine)
    if not found:
        # An absent variant is a hole, reported, never a pass.
        pytest.skip("%s is not built (lypning build --rust)" % engine)
    return str(found)


def _run(argv: list[str], program: str) -> subprocess.CompletedProcess:
    """One program in a temp cwd of its own — invariant 4."""
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True, text=True,
                              cwd=d, timeout=60)


def _last(stderr: str) -> str:
    lines = stderr.strip().splitlines()
    return lines[-1] if lines else ""


#: (engine, head) — the core has no `cap-future`, so it runs the bare rows only.
ARMS = [(engines.LYPNING, ""), (engines.LYPNING_L, ""), (engines.LYPNING_L, HEAD)]
ARM_IDS = ["core", "l", "l-head"]


@pytest.mark.parametrize("arm", ARMS, ids=ARM_IDS)
@pytest.mark.parametrize("row", SERVED, ids=range(len(SERVED)))
def test_an_annotated_assignment_matches_cpython(arm, row) -> None:
    engine, head = arm
    program, stdout, code, last = row
    got = _run([_binary(engine)], head + program)
    # Under a head the program is lypning-l's only through `cap-future`, and an
    # uncaught NameError there refuses (`route::hint_held`): CPython may end it
    # with a suggestion this engine does not compute.
    if head and last.startswith("NameError") and got.returncode == engines.UNSUPPORTED_EXIT:
        line = got.stderr.strip()
        assert got.stdout == "" and "\n" not in line, got.stderr
        assert line.startswith("%s: unsupported: name-hint: " % engine), line
        return
    assert (got.stdout, got.returncode, _last(got.stderr)) == (stdout, code, last), (
        head + program, got.stdout, got.returncode, got.stderr[-300:])


@pytest.mark.parametrize("arm", ARMS, ids=ARM_IDS)
@pytest.mark.parametrize("row", REFUSED, ids=range(len(REFUSED)))
def test_what_the_engine_cannot_answer_exactly_refuses(arm, row) -> None:
    engine, head = arm
    program = head + row[0]
    got = _run([_binary(engine)], program)
    assert got.returncode == engines.UNSUPPORTED_EXIT, (program, got.stdout, got.stderr)
    assert got.stdout == ""
    line = got.stderr.strip()
    assert line.startswith("%s: unsupported: " % engine) and "\n" not in line, line
    route = engines.route(program, binary=_binary(engine))
    assert route.engine == engines.CPYTHON, (program, route)


@pytest.mark.skipif(sys.version_info[:2] != (3, 14), reason="pinned bytes are CPython 3.14.5's")
@pytest.mark.parametrize("row", SERVED + REFUSED, ids=range(len(SERVED + REFUSED)))
def test_the_pinned_bytes_are_what_cpython_says(row) -> None:
    program, stdout, code, last = row
    for head in ("", HEAD):
        ref = _run([sys.executable], head + program)
        assert (ref.stdout, ref.returncode, _last(ref.stderr)) == (stdout, code, last), head
