"""What the core answers, lypning-l answers identically — the hold included.

`io::hold` (keep the run reversible to its end) and `err::forgot_import`'s
`name-hint` refusal exist for programs lypning-l serves only through a
capability the core lacks: those went to CPython before, and CPython's answer
carries a `Did you mean` this engine does not compute. `route::hint_held`
decides the hold from the walk, as the spectrum router's own verdict: a run is
held exactly when the core's static walk blocks on such a capability, so the
router would not pick the core.

It used to be a text match — `itertools`, `difflib` or `__future__` anywhere in
the source, `sample`/`shuffle`/`Random`/`version_info` as a word — so a comment,
a string or a variable name held a program the core serves, and lypning-l then
refused (exit 90) what its own subset answered: an uncaught error, more than
8 MiB of output. Invariant 10 forbids it, and the conformance monotone gate
counts it. Every probe below spells those words without using a capability;
each must come out of `lypning -c` and `lypning-l -c` byte for byte the same.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

#: Spellings of a capability's words that use no capability.
MENTIONS = [
    "# itertools is not needed here\n",
    "# compare with difflib later\n",
    "print('itertools')\n",
    's = "difflib"\n',
    '"""uses __future__ semantics"""\n',
    "# __future__\n",
    'print(f"see __future__")\n',
    "x = 'barry_as_FLUFL'\n",
    "import random\nsample = [1, 2]\nprint(sample)\n",
    "import random\nrandom.seed(1)\nprint('sample', 'shuffle', 'Random')\n",
    "import random\nshuffle = Random = 0\n",
    "import sys\nversion_info = 3\n",
    "import sys\nprint('version_info')\n",
    "# time statistics textwrap binascii\n",
]

#: What a held run refuses and the core answers.
TAILS = [
    "undefined_name\n",
    "x = 1\nx.foo\n",
    "d = {}\nd.nosuch()\n",
    "def f(a):\n    return a\nf(b=1)\n",
    "print('ok')\n",
]

#: More than 8 MiB of output, which a held run refuses rather than flush.
BIG = "for i in range(800000):\n    print('xxxxxxxxxx')\n"

PROBES = [m + t for m in MENTIONS for t in TAILS] + [m + BIG for m in MENTIONS[::4]]

#: Controls: programs only a capability admits. The core routes them past
#: itself, so the hold applies and the uncaught error refuses.
HELD = [
    "import itertools\nprint(1)\nprnt(1)",
    "if False:\n    import time\nundefined_name",
    "import random\nprint(random.Random(5).randint(1, 3))\nundefined_name",
    "import sys\nprint(sys.version_info[0] >= 3)\nundefined_name",
    "from __future__ import annotations\nundefined_name",
]


def _spectrum(binary: Path) -> dict | None:
    try:
        out = subprocess.run([str(binary), "route", "--spectrum"],
                             capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError):
        return None


def _current(engine: str) -> Path | None:
    """A built ``engine`` carrying THIS tree's capability table, or None."""
    target = {engines.LYPNING: paths.RUST_DIR / "target" / "release" / "lypning",
              engines.LYPNING_L: paths.RUST_DIR / "target" / "variant-l" / "release" / "lypning"}
    found = engines.find(engine)
    for cand in ([Path(found)] if found else []) + [target[engine]]:
        if not cand.is_file():
            continue
        table = _spectrum(cand)
        if table and table.get("self") == engine and \
                table.get("self_caps") == list(engines.VARIANT_CAPS.get(engine, ())):
            return cand
    return None


CORE = _current(engines.LYPNING)
LARGER = _current(engines.LYPNING_L)
needs_both = pytest.mark.skipif(
    CORE is None or LARGER is None,
    reason="needs the core and lypning-l of this tree (lypning build --rust); "
           "an absent variant is a hole, not a pass")


def _run(binary: Path, program: str) -> subprocess.CompletedProcess:
    """One program in a temp cwd of its own — invariant 4."""
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run([str(binary), "-c", program], capture_output=True,
                              cwd=d, timeout=120)


@needs_both
@pytest.mark.parametrize("program", PROBES, ids=range(len(PROBES)))
def test_what_the_core_answers_lypning_l_answers_byte_for_byte(program: str) -> None:
    route = engines.route(program, binary=CORE)
    # The core never routes one of these to a capability: none uses one.
    assert route.engine in (engines.LYPNING, engines.CPYTHON), (route.engine, route.kind)
    core = _run(CORE, program)
    assert core.returncode != engines.UNSUPPORTED_EXIT, core.stderr[-300:]
    larger = _run(LARGER, program)
    assert (larger.returncode, larger.stdout, larger.stderr) == \
        (core.returncode, core.stdout, core.stderr), (
        "lypning-l disagrees with the core on a program no capability admits\n"
        "  program: %r\n  core: %d %r\n  lypning-l: %d %r"
        % (program, core.returncode, core.stderr[-200:], larger.returncode, larger.stderr[-200:]))


@needs_both
def test_the_probes_mostly_route_to_the_core() -> None:
    """Not vacuous: the probe set is the core's own programs, routed to it."""
    to_core = [p for p in PROBES if engines.route(p, binary=CORE).engine == engines.LYPNING]
    assert len(to_core) * 2 > len(PROBES), len(to_core)


@needs_both
@pytest.mark.parametrize("program", HELD, ids=range(len(HELD)))
def test_a_program_only_a_capability_admits_is_still_held(program: str) -> None:
    assert engines.route(program, binary=CORE).engine != engines.LYPNING
    got = _run(LARGER, program)
    assert got.returncode == engines.UNSUPPORTED_EXIT, (got.returncode, got.stderr)
    assert got.stdout == b""
    assert got.stderr.decode().startswith("%s: unsupported: name-hint: " % engines.LYPNING_L)
