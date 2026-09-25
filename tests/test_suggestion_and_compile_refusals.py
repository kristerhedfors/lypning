"""Uncaught errors CPython ends with a suggestion, and compile-time
SyntaxErrors, in programs lypning-l serves only through a capability the core
lacks.

Every program here went to CPython before its capability landed — the core
refuses the import (or the `__future__` head) STATICALLY — so CPython's own
bytes were the answer. lypning-l does not compute the `Did you mean` search
(`err::forgot_import`), so a suggestion row in which the capability has RUN
(`io::hold`) must end in a clean refusal (exit 90, nothing on stdout, one
``lypning-l: unsupported: name-hint: <detail>`` line on stderr) and the chain
must then print exactly what CPython 3.14.5 prints — pinned below.

An error raised before the capability runs, or under an import that never
runs, is the core's program up to that point: lypning-l answers it as the
core answers it (`BEFORE_IT_RUNS`), however it was reached — no dispatcher
tells a rung it was routed.

A compile-time SyntaxError is the PARSER's, in every variant (`SYNTAX`):
exit 1 with empty stdout run directly, and routed to CPython as `syntax`.
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

#: (program, CPython 3.14.5 stdout, CPython 3.14.5 last stderr line); exit 1.
HINTED = [
    ('import statistics\nprint(statistics.mean([1,2]))\nlenn([1])',
     '1.5\n', "NameError: name 'lenn' is not defined. Did you mean: 'len'?"),
    ('import statistics\nprint(prnt)',
     '', "NameError: name 'prnt' is not defined. Did you mean: 'print'?"),
    ('import statistics\ndata=[1,2]\nprint(statistics.mean(dat))',
     '', "NameError: name 'dat' is not defined. Did you mean: 'data'?"),
    ('import statistics\n"abc".uper()',
     '', "AttributeError: 'str' object has no attribute 'uper'. Did you mean: 'upper'?"),
    ('import statistics\nx={"a":1}\nx.item()',
     '', "AttributeError: 'dict' object has no attribute 'item'. Did you mean: 'items'?"),
    ('import statistics\nm=statistics.mean([1,2])\nm.is_integr()',
     '', "AttributeError: 'float' object has no attribute 'is_integr'. Did you mean: 'is_integer'?"),
    ('import random\ncount = random.Random(5).randint(1, 3)\nprint(cont)',
     '', "NameError: name 'cont' is not defined. Did you mean: 'count'?"),
    ('import random\nx = random.Random(5).sample([1, 2, 3], 2)\nprint(x)\nclass_name = 1\nprint(class_nme)',
     '[3, 2]\n', "NameError: name 'class_nme' is not defined. Did you mean: 'class_name'?"),
    ('import random\nprint(random.sample(range(5), 2) if random.seed(1) is None else 0)\nprnt',
     '[1, 0]\n', "NameError: name 'prnt' is not defined. Did you mean: 'print'?"),
    ('import random\nl = [3, 1, 2]\nrandom.seed(0)\nrandom.shuffle(l)\nprint(l)\nprnt',
     '[3, 2, 1]\n', "NameError: name 'prnt' is not defined. Did you mean: 'print'?"),
    ('import random\nr = random.Random(1)\ndef g(alpha): pass\ng(alpah=r.randint(1, 2))',
     '', "TypeError: g() got an unexpected keyword argument 'alpah'. Did you mean 'alpha'?"),
    ('import sys\nif sys.version_info >= (3, 8):\n    valuee = 1\nprint(value)',
     '', "NameError: name 'value' is not defined. Did you mean: 'valuee'?"),
    ('import sys\ndef g(alpha): pass\ng(alpah=sys.version_info[0])',
     '', "TypeError: g() got an unexpected keyword argument 'alpah'. Did you mean 'alpha'?"),
    ("import textwrap\nprint(textwrap.dedent(' a'))\nprnt(1)",
     'a\n', "NameError: name 'prnt' is not defined. Did you mean: 'print'?"),
    ('import binascii\nprnt(1)',
     '', "NameError: name 'prnt' is not defined. Did you mean: 'print'?"),
    ('from __future__ import annotations\nprnt(1)',
     '', "NameError: name 'prnt' is not defined. Did you mean: 'print'?"),
]

#: Before the capability runs, or under an import that never runs: the core's
#: answer, from lypning-l too — the exception CPython raises, without its
#: suggestion (as the core, like CPython 3.9, prints it).
BEFORE_IT_RUNS = [
    'prnt(1)\nimport time',
    'if False:\n    import time\nprnt(1)',
    'import os\ndef f():\n    import time\nprnt(1)',
    'def g(alpha): pass\ng(alpah=1)\nimport time',
    'if False:\n    import time\ndef g(alpha): pass\ng(alpah=1)',
]

#: SyntaxErrors CPython's compiler raises, which the parser raises too, with a
#: head or without; empty stdout, exit 1.
SYNTAX = [
    ('from __future__ import annotations\ndef f(x: int = 1, y) -> int:\n    return x',
     '', 'SyntaxError: parameter without a default follows parameter with a default'),
    ('from __future__ import annotations\ndef f(x: int, x: int): pass',
     '', "SyntaxError: duplicate argument 'x' in function definition"),
    ('from __future__ import annotations\nf = lambda x, x: 1',
     '', "SyntaxError: duplicate argument 'x' in function definition"),
    ('from __future__ import annotations\nf = lambda x=1, y: 1',
     '', 'SyntaxError: parameter without a default follows parameter with a default'),
    ('from __future__ import annotations\ndef f():\n    break',
     '', "SyntaxError: 'break' outside loop"),
    ('from __future__ import annotations\ndef f():\n    try:\n        pass\n    finally:\n        continue',
     '', "SyntaxError: 'continue' not properly in loop"),
    ('from __future__ import annotations\nreturn 1',
     '', "SyntaxError: 'return' outside function"),
    ('from __future__ import annotations\nbreak',
     '', "SyntaxError: 'break' outside loop"),
    ('from __future__ import annotations\ncontinue',
     '', "SyntaxError: 'continue' not properly in loop"),
    ('from __future__ import annotations\ndef f(a):\n    global a',
     '', "SyntaxError: name 'a' is parameter and global"),
    ('from __future__ import annotations\ndef f():\n    x = 1\n    global x',
     '', "SyntaxError: name 'x' is assigned to before global declaration"),
    ('from __future__ import annotations\ndef f():\n    print(x)\n    global x',
     '', "SyntaxError: name 'x' is used prior to global declaration"),
    ('from __future__ import annotations\ndef f(*a): pass\nf(x for x in range(2), 1)',
     '', 'SyntaxError: Generator expression must be parenthesized'),
    ('from __future__ import annotations\nprint(x=1, 2)',
     '', 'SyntaxError: positional argument follows keyword argument'),
    ('from __future__ import annotations\nprint(**{}, *[])',
     '', 'SyntaxError: iterable argument unpacking follows keyword argument unpacking'),
    ('from __future__ import annotations\n__debug__ = 1',
     '', 'SyntaxError: cannot assign to __debug__'),
    ('from __future__ import annotations\ndef f(__debug__): pass',
     '', 'SyntaxError: cannot assign to __debug__'),
]

#: Neighbours of the rows above that must still be SERVED, byte for byte.
SERVED = [
    "import statistics\nprint(statistics.mean([1,2]))",
    "import time\nprint(type(time.time()).__name__)",
    "from __future__ import annotations\ndef f(x: int = 1) -> int:\n    return x\nprint(f())",
    "from __future__ import annotations\ndef f(a, b=1, *c, **d): pass\nprint(sum(x for x in range(3)))",
    "from __future__ import annotations\nfor i in range(3):\n    if i:\n        break\n    continue\nprint(i)",
    "from __future__ import annotations\ndef f():\n    global g\n    g = 1\nf()\nprint(g)",
    "from __future__ import annotations\nprint(1, sep='', *[2])",
    "import random\nprint(random.Random(5).randint(1, 3))",
    "import sys\nprint(sys.version_info[0])",
]


def _spectrum(binary: Path) -> dict | None:
    try:
        out = subprocess.run([str(binary), "route", "--spectrum"],
                             capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError):
        return None


def _current(engine: str = engines.LYPNING_L) -> Path | None:
    """A built ``engine`` carrying THIS tree's capability table, or None."""
    found = engines.find(engine)
    target = paths.RUST_DIR / "target" / ("variant-l/release" if engine == engines.LYPNING_L
                                          else "release") / "lypning"
    for cand in ([Path(found)] if found else []) + [target]:
        if not cand.is_file():
            continue
        table = _spectrum(cand)
        if table and table.get("self") == engine and \
                table.get("self_caps") == list(engines.VARIANT_CAPS.get(engine, ())):
            return cand
    return None


BINARY = _current()
needs_l = pytest.mark.skipif(BINARY is None, reason="no current lypning-l is built (lypning build --rust)")
exact = sys.version_info[:3] == (3, 14, 5)


def _run(argv: list[str], program: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True, text=True,
                              cwd=d, timeout=60)


def _refused(got: subprocess.CompletedProcess, kind: str) -> None:
    assert got.returncode == engines.UNSUPPORTED_EXIT, (got.returncode, got.stdout, got.stderr)
    assert got.stdout == "", got.stdout
    line = got.stderr.strip()
    assert "\n" not in line and line.startswith("%s: unsupported: %s: " % (engines.LYPNING_L, kind)), line


@needs_l
@pytest.mark.parametrize("program,out,line", HINTED, ids=range(len(HINTED)))
def test_a_suggestion_cpython_prints_is_a_refusal(program: str, out: str, line: str) -> None:
    _refused(_run([str(BINARY)], program), "name-hint")
    if exact:
        ref = _run([sys.executable], program)
        assert (ref.returncode, ref.stdout, ref.stderr.strip().splitlines()[-1]) == (1, out, line)


@needs_l
@pytest.mark.parametrize("program,out,line", SYNTAX, ids=range(len(SYNTAX)))
def test_a_compile_time_syntax_error_is_the_parsers(program: str, out: str, line: str) -> None:
    direct = _run([str(BINARY)], program)
    if "__debug__" in program:  # a constant this engine has no value for
        _refused(direct, "builtin")
        return
    assert engines.route(program, binary=BINARY).kind == "syntax"
    assert (direct.returncode, direct.stdout) == (1, ""), (direct.returncode, direct.stdout, direct.stderr)
    assert direct.stderr.strip().splitlines()[-1].startswith("SyntaxError: "), direct.stderr
    if exact:
        ref = _run([sys.executable], program)
        assert (ref.returncode, ref.stdout, ref.stderr.strip().splitlines()[-1]) == (1, out, line)


@needs_l
@pytest.mark.parametrize("program", BEFORE_IT_RUNS, ids=range(len(BEFORE_IT_RUNS)))
def test_an_error_before_the_capability_runs_is_the_cores(program: str) -> None:
    core = _current(engines.LYPNING)
    if core is None:
        pytest.skip("no current core is built (lypning build --rust)")
    got, want = _run([str(BINARY)], program), _run([str(core)], program)
    assert (got.returncode, got.stdout, got.stderr) == (want.returncode, want.stdout, want.stderr)
    assert got.returncode == 1
    ref = _run([sys.executable], program)
    assert ref.returncode == 1 and ref.stdout == got.stdout
    assert ref.stderr.strip().splitlines()[-1].split(":")[0] == got.stderr.strip().splitlines()[-1].split(":")[0]


@needs_l
@pytest.mark.parametrize("program", SERVED, ids=range(len(SERVED)))
def test_the_neighbours_are_still_served(program: str) -> None:
    got = _run([str(BINARY)], program)
    ref = _run([sys.executable], program)
    assert (got.returncode, got.stdout) == (ref.returncode, ref.stdout), got.stderr
    assert got.returncode == 0


@needs_l
@pytest.mark.parametrize("program,out,line", (HINTED + SYNTAX)[::5])
def test_the_chain_prints_cpythons_bytes(program: str, out: str, line: str) -> None:
    if not exact:
        pytest.skip("the pinned bytes are CPython 3.14.5's")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    with tempfile.TemporaryDirectory() as d:
        got = subprocess.run([sys.executable, "-m", "lypning", "run", "-c", program],
                             capture_output=True, text=True, cwd=d, timeout=120, env=env)
    assert (got.returncode, got.stdout, got.stderr.strip().splitlines()[-1]) == (1, out, line)
