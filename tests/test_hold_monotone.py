"""What the core answers, lypning-l answers identically — the hold included.

`io::hold` (keep the run reversible to its end) and `err::forgot_import`'s
`name-hint` refusal exist for programs lypning-l serves only through a
capability the core lacks: those went to CPython before, and CPython's answer
carries a `Did you mean` this engine does not compute. `route::hint_held`
ARMS a run from the walk, as the spectrum router's own verdict: when the core's
static walk blocks on such a capability, so the router would not pick the core.
The hold itself starts where the capability RUNS (`io::hold`) — the point at
which the core, running the same program, refuses — so a capability under an
import that never runs holds nothing (`ARMED`).

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

#: Controls: programs only a capability admits, whose capability RUNS. The
#: core routes them past itself and refuses where the capability runs, so the
#: hold applies and the uncaught error refuses.
HELD = [
    "import itertools\nprint(1)\nprnt(1)",
    "def f():\n    pass\nimport time\nundefined_name",
    "import random\nprint(random.Random(5).randint(1, 3))\nundefined_name",
    "import sys\nprint(sys.version_info[0] >= 3)\nundefined_name",
    "from __future__ import annotations\nundefined_name",
]


#: Programs the core routes past itself whose capability never RUNS, and
#: programs that only look like a capability: the core answers each (it never
#: reaches its refusal), so lypning-l must answer it byte for byte the same —
#: the run is armed by the walk, and held only where the capability runs.
ARMED = [
    "if False:\n    import itertools\nprint(undefined_name)",
    "if False:\n    import time\nundefined_name",
    "def f():\n    import time\nprint('ok')\nundefined",
    "import random\nif False:\n    random.shuffle([])\nprint('ok')\nundefined",
    "while False:\n    import textwrap\nx = 1\nx.foo",
    "if False:\n    import statistics\ndef f(a):\n    return a\nf(b=1)",
    "if False:\n    import textwrap\nprint('a' * 9000000)",
    "barry_as_FLUFL = 1\nprint(barry_as_FLUFL)",
    "def barry_as_FLUFL(): return 2\nprint(barry_as_FLUFL())",
    "print(dict(barry_as_FLUFL=1))",
    "__future__ = 1\nprint(__future__)",
    "for __future__ in range(2): print(__future__)",
    "import os as __future__\nprint(1)",
    "def __future__(): return 5\nprint(__future__())",
    "try:\n    __future__\nexcept NameError as e:\n    print(e)",
    "binascii = 'x'\nprint(binascii.upper())",
    "binascii = [3]\nprint(binascii.count(3))",
    "def f(binascii): return binascii.upper()\nprint(f('a'))",
    "try:\n    print(1)\nexcept glob.X:\n    pass",
    "try:\n    print(1)\nexcept hashlib.X:\n    pass",
    "try:\n    print(1)\nexcept time.error:\n    pass",
    "try:\n    print(1)\nexcept textwrap.X:\n    pass",
    "try:\n    print(1)\nexcept binascii.Error:\n    pass",
    "time = 1\ntry:\n    print(1)\nexcept time.error:\n    pass",
    "try:\n    print(1)\nexcept ValueError:\n    pass\nexcept (glob.X, time.Y):\n    pass",
]

#: What the parser lets through and CPython's compiler rejects. Behind a
#: capability the core lacks, each went to CPython before, and lypning-l now
#: answers CPython's answer: exit 1, nothing on stdout, a SyntaxError.
LAX = [
    "def f(a, a): pass",
    "f = lambda a, a: 1",
    "def f(a, *a): pass",
    "def f(a, **a): pass",
    "def f(x=1, y): pass",
    "def f():\n    x = 1\n    global x",
    "def g(x):\n    global x",
    "x = 2\ndef f():\n    print(x)\n    global x",
    "print(1)\ncontinue",
    "print(1)\nbreak",
    "print(1)\nreturn 5",
    "print(0777)",
    "print(1__0)",
    "print(1_)",
    "print(1)\ntry:\n    pass\nexcept:\n    pass\nexcept ValueError:\n    pass",
    "print(sum(x for x in [1], 2))",
    "print(f'{1!x}')",
    "print(1)\na, *b, *c = [1, 2, 3]",
]

#: A prefix that makes each of those a program only a capability admits.
LAX_HEADS = [
    "import statistics\n",
    "import itertools\n",
    "import random\nr = random.Random(1)\n",
    "import sys\nv = sys.version_info[0]\n",
    "from __future__ import annotations\n",
    "if False:\n    import time\n",
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


@needs_both
@pytest.mark.parametrize("program", ARMED, ids=range(len(ARMED)))
def test_a_capability_that_never_runs_holds_nothing(program: str) -> None:
    """The core runs these to the end without meeting its refusal; so does lypning-l."""
    core = _run(CORE, program)
    assert core.returncode != engines.UNSUPPORTED_EXIT, core.stderr[-300:]
    larger = _run(LARGER, program)
    assert (larger.returncode, larger.stdout, larger.stderr) == \
        (core.returncode, core.stdout, core.stderr), (
        "lypning-l disagrees with the core on a program the core answers\n"
        "  program: %r\n  core: %d %r\n  lypning-l: %d %r"
        % (program, core.returncode, core.stderr[-200:], larger.returncode, larger.stderr[-200:]))


LAX_PROGRAMS = [h + b for h in LAX_HEADS for b in LAX]


@needs_both
@pytest.mark.parametrize("program", LAX_PROGRAMS, ids=range(len(LAX_PROGRAMS)))
def test_a_syntax_error_behind_a_capability_is_cpythons(program: str) -> None:
    with pytest.raises(SyntaxError):
        compile(program, "<string>", "exec")
    got = _run(LARGER, program)
    assert (got.returncode, got.stdout) == (1, b""), (program, got.returncode, got.stdout, got.stderr)
    assert got.stderr.decode().strip().splitlines()[-1].startswith("SyntaxError: "), got.stderr


def test_no_child_inherits_the_routed_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a dispatcher says a rung was routed; a battery run from inside a chain does not."""
    monkeypatch.setenv(engines.ROUTED_ENV, "1")
    assert engines.ROUTED_ENV not in engines.child_env()
    assert engines.child_env({engines.ROUTED_ENV: "1"})[engines.ROUTED_ENV] == "1"


#: Held only when ROUTED: the chain went past the core, and CPython's hint is
#: its answer; run directly, lypning-l answered what the core answers above.
ROUTED_HELD = "if False:\n    import time\nprnt(1)"


@needs_both
def test_both_dispatchers_route_the_hold() -> None:
    import os
    import sys
    direct = _run(LARGER, ROUTED_HELD)
    assert direct.returncode == 1, direct.stderr
    env = dict(os.environ, **{engines.ROUTED_ENV: "1"})
    with tempfile.TemporaryDirectory() as d:
        routed = subprocess.run([str(LARGER), "-c", ROUTED_HELD], capture_output=True,
                                cwd=d, timeout=120, env=env)
    assert routed.returncode == engines.UNSUPPORTED_EXIT, routed.stderr
    assert routed.stderr.decode().startswith("%s: unsupported: name-hint: " % engines.LYPNING_L)
    if sys.version_info[:2] < (3, 10):
        return
    for d in (engines.dispatch(ROUTED_HELD, ledger=False).result,
              engines.run(engines.LYPNING, ROUTED_HELD, binary=CORE, prefix=("run",))):
        assert d.returncode == 1, d.stderr
        assert "Did you mean" in d.stderr.strip().splitlines()[-1], d.stderr
