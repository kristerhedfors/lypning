"""`difflib`, stage A: the import is served and NOTHING on it is.

`route::MODULE_ATTRS` carries an EMPTY `difflib` row — present, because an
absent row claims the module's whole surface — so every `difflib.<name>` is a
static `module-attr` block in the CORE's walk, and only a program that imports
the module without touching it is routed to `lypning-l`. The corpus mined on
2026-09-24 has such programs (scout census: 4 of the 24 blocked first on
`import difflib`); `SequenceMatcher.ratio` is stage B and is not served.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile

import pytest

from lypning import engines

from test_itertools_grid import CORE, _current, _refusal_problem, _spectrum

BINARY = _current(engines.LYPNING_L, "cap-difflib")

needs_l = pytest.mark.skipif(BINARY is None,
                             reason="no lypning-l carrying cap-difflib is built")
needs_core = pytest.mark.skipif(BINARY is None or CORE is None,
                                reason="routing needs both binaries")

SERVED = [
    "import difflib\nprint(1)",
    "import difflib as d, json\nprint(json.dumps([1]))",
    "import os, difflib\nprint(os.path.join('a', 'b'))",
    "import difflib\nimport sys\nprint(len(sys.argv))",
]

REFUSED = [
    "import difflib\nprint(difflib.SequenceMatcher(None, 'a', 'b').ratio())",
    "import difflib\nprint(difflib.unified_diff)",
    "import difflib\nprint(list(difflib.ndiff(['a'], ['b'])))",
    "import difflib\nprint(difflib.get_close_matches('a', ['a']))",
    "from difflib import SequenceMatcher\nprint(1)",
    "import difflib\nprint(difflib)",
]


def _run(argv, program):
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True, text=True,
                              cwd=d, timeout=60)


@needs_l
@pytest.mark.parametrize("program", SERVED, ids=range(len(SERVED)))
def test_a_program_that_only_imports_difflib_runs_and_agrees(program):
    got = _run([str(BINARY)], program)
    ref = _run([sys.executable], program)
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        program, got.stdout, got.returncode, got.stderr[-200:])


@needs_l
@pytest.mark.parametrize("program", REFUSED, ids=range(len(REFUSED)))
def test_every_name_on_difflib_refuses(program):
    got = _run([str(BINARY)], program)
    assert _refusal_problem(got) is None, (_refusal_problem(got), program)


@needs_core
@pytest.mark.parametrize("program", SERVED + REFUSED, ids=range(len(SERVED + REFUSED)))
def test_the_core_routes_only_the_bare_import_to_the_larger_variant(program):
    route = engines.route(program, binary=CORE)
    want = engines.LYPNING_L if program in SERVED else engines.CPYTHON
    # `print(difflib)` is a module repr, refused at runtime by the engine and not
    # by the walk; it is the one REFUSED row the core may route to lypning-l.
    if program.endswith("print(difflib)"):
        want = route.engine
    assert route.engine == want, (program, route.engine, route.kind, route.detail)


@needs_l
def test_the_row_is_present_and_empty():
    table = _spectrum(BINARY)
    assert {r["cap"]: r["modules"] for r in table["caps"]}["cap-difflib"] == ["difflib"]
