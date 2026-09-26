"""A call whose arguments do not bind refuses, in every variant.

CPython's binding ``TypeError`` is worded by version, and no wording this
engine could build is right on every minor:

* the function is named by its QUALIFIED name from 3.10 — ``f.<locals>.g()``,
  ``o.<locals>.g()``, ``<genexpr>.<lambda>()`` — and by the bare one on 3.9;
* every missing argument is counted at once (``missing 3 required positional
  arguments: 'alpha', 'beta', and 'gamma'``), on every minor;
* a positional-only name passed by keyword is ``got some positional-only
  arguments passed as keyword arguments``, on every minor;
* an unexpected keyword gains ``. Did you mean 'alpha'?`` on 3.14;
* ``json.loads()`` names its parameter (``'s'``), and ``f(*1)`` says
  ``__main__.f() argument after * must be an iterable, not int``.

The binder said ``g() missing 1 required positional argument: 'alpha'`` for
all of them — a MISMATCH on every minor, visible only through ``except
TypeError as e: print(e)``, which is why the battery never graded it. So each
row here must leave both binaries by the refusal contract (exit 90, one line on
stderr naming the variant, NOTHING on stdout), and ``lypning run`` — the chain
— must end with CPython's own answer. An absent variant is a hole (skipped),
never a pass.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines

ROWS = [
    ("nested_def_arity",
     "def f():\n    def g(a): pass\n    try:\n        g()\n    except TypeError as e:\n"
     "        print(e)\nf()"),
    ("multi_missing",
     "def k(alpha, beta, gamma): pass\ntry:\n    k()\nexcept TypeError as e:\n    print(e)"),
    ("posonly_kw",
     "def f(a, /): pass\ntry:\n    f(a=1)\nexcept TypeError as e:\n    print(e)"),
    ("genexpr_lambda",
     "try:\n    list((lambda a: a)() for _ in [0])\nexcept TypeError as e:\n    print(e)"),
    ("kw_did_you_mean",
     "def k(alpha): pass\ntry:\n    k(alpah=1)\nexcept TypeError as e:\n    print(e)"),
    ("too_many_nested",
     "def o():\n    def g(a): pass\n    return g\ntry:\n    o()(1, 2)\nexcept TypeError as e:\n"
     "    print(e)"),
    ("nested_unexpected_kw",
     "def o():\n    def g(a): pass\n    return g\ntry:\n    o()(b=2)\nexcept TypeError as e:\n"
     "    print(e)"),
    ("multiple_values",
     "def f(a): pass\ntry:\n    f(1, a=2)\nexcept TypeError as e:\n    print(e)"),
    ("star_non_iterable",
     "def f(*a): pass\ntry:\n    f(*1)\nexcept TypeError as e:\n    print(e)"),
    ("json_loads_missing",
     "import json\ntry:\n    json.loads()\nexcept TypeError as e:\n    print(e)"),
    ("json_dumps_missing",
     "import json\ntry:\n    json.dumps()\nexcept TypeError as e:\n    print(e)"),
    ("json_dump_missing",
     "import json\ntry:\n    json.dump()\nexcept TypeError as e:\n    print(e)"),
    # Uncaught, after output: the output is taken back with the refusal.
    ("uncaught_after_print", "print(1)\ndef k(a, b): pass\nk()"),
]

#: Calls that bind: the refusal is on the error path only.
BINDS = [
    "def m(a, b=1): return a + b\nprint(m(1), m(1, 2), m(b=3, a=1))",
    "def f(a, /, b, *c, **d): return a, b, c, d\nprint(f(1, 2, 3, x=4), f(1, b=2))",
    "def o():\n    g = lambda a, b=2: a * b\n    return g\nprint(o()(3), o()(3, b=4))",
    "import json\nprint(json.loads('[1]'), json.dumps({'a': 1}))",
]

VARIANTS = [(engines.LYPNING, engines.find(engines.LYPNING)),
            (engines.LYPNING_L, engines.find(engines.LYPNING_L))]


def _run(argv: list[str], program: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """One program in a temp cwd of its own — invariant 4."""
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True, text=True,
                              cwd=d, timeout=120, env=env)


@pytest.mark.parametrize("engine,binary", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("name,program", ROWS, ids=[r[0] for r in ROWS])
def test_a_binding_error_refuses_cleanly(engine: str, binary: str | None,
                                         name: str, program: str) -> None:
    if binary is None:
        pytest.skip("%s is not built: a hole, not a pass" % engine)
    got = _run([str(binary)], program)
    assert (got.returncode, got.stdout) == (engines.UNSUPPORTED_EXIT, ""), (
        name, got.returncode, got.stdout, got.stderr)
    line = got.stderr.strip()
    assert "\n" not in line and line.startswith("%s: unsupported: call: " % engine), line


@pytest.mark.parametrize("engine,binary", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("program", BINDS, ids=range(len(BINDS)))
def test_a_call_that_binds_still_answers(engine: str, binary: str | None, program: str) -> None:
    if binary is None:
        pytest.skip("%s is not built: a hole, not a pass" % engine)
    got, ref = _run([str(binary)], program), _run([sys.executable], program)
    assert (got.returncode, got.stdout) == (ref.returncode, ref.stdout) == (0, ref.stdout), (
        got.stderr, ref.stderr)


@pytest.mark.parametrize("name,program", ROWS, ids=[r[0] for r in ROWS])
def test_the_chain_ends_with_cpythons_answer(name: str, program: str) -> None:
    if VARIANTS[0][1] is None:
        pytest.skip("the core is not built: the chain would be CPython alone")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["LYPNING_CPYTHON"] = sys.executable
    got = _run([sys.executable, "-m", "lypning", "run"], program, env=env)
    ref = _run([sys.executable], program)
    assert (got.returncode, got.stdout) == (ref.returncode, ref.stdout), (
        name, got.stdout, got.stderr[-300:], ref.stdout)
    if ref.returncode:
        assert got.stderr.strip().splitlines()[-1] == ref.stderr.strip().splitlines()[-1]
