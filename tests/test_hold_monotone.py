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
import os
import subprocess
import sys
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
    # The final verification of 979b527: an import that never runs, and a
    # construct the parser let through or refused by the name before `global`.
    "if 0:\n    import binascii\nprint(1)\nimport os\nglobal os",
    "if 0:\n    import binascii\nprint(1)\nf = lambda x: 0\nglobal x",
    "if 0:\n    import binascii\nprint([x for x in []])\nglobal x",
    "if 0:\n    import binascii\nprint(1)\ndef g(x): pass\nglobal x",
    "if 0:\n    import binascii\nprint(1)\nprint(dict(y=1))\nglobal y",
    "if 0:\n    import binascii\nprint(0777)",
    "def g():\n    import binascii\nprint(1)\nbreak",
    "if 0:\n    import binascii\ndef f(a, a): pass\nprint(1)",
    "if 0:\n    import binascii\nprint(1)\nprint(x=1, 2)",
    "if 0:\n    import binascii\nx = 1\nglobal x\nprint(x)",
    "if 0:\n    import binascii\nprint(1)\nreturn 1",
]

#: What CPython's compiler rejects before anything runs, which the parser used
#: to let through (the core ran it; lypning-l, behind a capability, emulated
#: CPython's SyntaxError). It is the PARSER's SyntaxError now, in every
#: variant, with or without a head: exit 1, nothing on stdout, routed to
#: CPython as `syntax`.
REJECTED = [
    "def f(a, a): pass",
    "f = lambda a, a: 1",
    "def f(a, *a): pass",
    "def f(a, **a): pass",
    "def f(x=1, y): pass",
    "def f():\n    x = 1\n    global x",
    "def g(x):\n    global x",
    "x = 2\ndef f():\n    print(x)\n    global x",
    "x = 1\nglobal x",
    "print(1)\ncontinue",
    "print(1)\nbreak",
    "print(1)\nreturn 5",
    "print(0777)",
    "print(1__0)",
    "print(1_)",
    "print(1_e5)",
    "print(1)\ntry:\n    pass\nexcept:\n    pass\nexcept ValueError:\n    pass",
    "print(sum(x for x in [1], 2))",
    "print(f'{1!x}')",
    "print(1)\na, *b, *c = [1, 2, 3]",
    "print(1)\n*a = [1]\nprint(a)",
    "for *x in []: pass",
    "print([x for *x in []])",
    "print(1)\ndef f(/, a): pass",
    "print(1)\ndef f(a, /, /): pass",
    "print(1)\nif 1:\n\tx = 1\n        y = 2",
    "print(1)\na, b += 1",
]

#: The heads: none (the core's own program), a capability that runs, one that
#: never runs, and a served `__future__` head.
REJECTED_HEADS = [
    "",
    "import statistics\n",
    "import itertools\n",
    "import random\nr = random.Random(1)\n",
    "import sys\nv = sys.version_info[0]\n",
    "from __future__ import annotations\n",
    "if False:\n    import time\n",
]

#: Characters CPython rejects in or as an identifier or whitespace, and one it
#: accepts: all refuse (`token`), so CPython answers each.
NON_ASCII = ["print(1)\n\u20ac = 1", "print(1)\n\xa0x = 1", "print(1)\nx\u200b = 1",
             "print(1)\n\u0661 = 1", "print(1)\n\u3000x = 1", "\u03c0 = 1\nprint(\u03c0)"]


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


REJECTED_PROGRAMS = [h + b for h in REJECTED_HEADS for b in REJECTED]


@needs_both
@pytest.mark.parametrize("program", REJECTED_PROGRAMS, ids=range(len(REJECTED_PROGRAMS)))
def test_a_compile_time_error_is_the_parsers_in_both(program: str) -> None:
    with pytest.raises(SyntaxError):
        compile(program, "<string>", "exec")
    core, larger = _run(CORE, program), _run(LARGER, program)
    assert (larger.returncode, larger.stdout, larger.stderr) == \
        (core.returncode, core.stdout, core.stderr), program
    assert (core.returncode, core.stdout) == (1, b""), (program, core.returncode, core.stdout, core.stderr)
    assert core.stderr.decode().strip().splitlines()[-1].startswith("SyntaxError: "), core.stderr
    for binary in (CORE, LARGER):
        assert engines.route(program, binary=binary).kind == "syntax", program


@needs_both
@pytest.mark.parametrize("program", NON_ASCII, ids=range(len(NON_ASCII)))
def test_a_non_ascii_identifier_refuses_in_both(program: str) -> None:
    for binary in (CORE, LARGER):
        got = _run(binary, program)
        assert got.returncode == engines.UNSUPPORTED_EXIT and got.stdout == b"", got
        assert b": unsupported: token: " in got.stderr


#: No dispatcher adds, removes or overwrites a variable in what a program, or
#: anything it spawns, sees — whichever rung answers.
ENV_PROBES = [
    "import itertools, os\nprint(os.environ.get('LYPNING_ROUTED'), os.getenv('LYPNING_ROUTED'))",
    "import os\nprint(os.environ.get('LYPNING_ROUTED'))",
    "import subprocess\nimport os\nprint(os.environ.get('LYPNING_ROUTED'))",
]


@needs_both
@pytest.mark.parametrize("value", [None, "0", "1"])
@pytest.mark.parametrize("program", ENV_PROBES, ids=range(len(ENV_PROBES)))
def test_the_environment_is_the_callers(program: str, value: str | None) -> None:
    import os
    env = {k: v for k, v in os.environ.items() if k != "LYPNING_ROUTED"}
    if value is not None:
        env["LYPNING_ROUTED"] = value
    want = "%s\n" % value if "getenv" not in program else "%s %s\n" % (value, value)
    with tempfile.TemporaryDirectory() as d:
        chain = subprocess.run([str(CORE), "run", "-c", program], capture_output=True,
                               text=True, cwd=d, timeout=120,
                               env=dict(env, LYPNING_L_BIN=str(LARGER)))
    assert (chain.returncode, chain.stdout) == (0, want), chain.stderr
    saved = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(env)
        got = engines.dispatch(program, ledger=False).result
    finally:
        os.environ.clear()
        os.environ.update(saved)
    assert (got.returncode, got.stdout) == (0, want), got.stderr


#: `os.rename`/`os.replace` of a directory, or onto one: the kernel moves a
#: tree the barrier cannot stage, so every variant refuses and CPython answers.
RENAME = [
    "import os\nos.mkdir('pre')\nos.rename('pre', 'pre2')\nprint(os.path.isdir('pre2'))",
    "import os\nos.mkdir('pre')\nos.replace('pre', 'pre3')\nprint(os.path.isdir('pre3'))",
    "import itertools, os\nos.mkdir('a')\nos.mkdir('b')\nos.rename('a', 'b')\nprint(os.path.isdir('b'))",
    "import os\nos.mkdir('a')\nos.mkdir('b')\nopen('b/f', 'w').close()\nos.replace('a', 'b')",
    "import os\nopen('f', 'w').close()\nos.mkdir('d')\nos.rename('f', 'd')",
    "import os\nos.mkdir('d')\nopen('f', 'w').close()\nos.rename('d', 'f')",
]


@needs_both
@pytest.mark.parametrize("program", RENAME, ids=range(len(RENAME)))
def test_renaming_a_directory_refuses_and_the_chain_answers(program: str) -> None:
    for binary in (CORE, LARGER):
        with tempfile.TemporaryDirectory() as d:
            got = subprocess.run([str(binary), "-c", program], capture_output=True, cwd=d, timeout=120)
            assert got.returncode == engines.UNSUPPORTED_EXIT and got.stdout == b"", got
            if binary == LARGER or "itertools" not in program:
                assert b": unsupported: rename: " in got.stderr
            assert os.listdir(d) == [], "the refusal left the run's directories behind"
    with tempfile.TemporaryDirectory() as d:
        ref = subprocess.run([sys.executable, "-c", program], capture_output=True, cwd=d, timeout=120)
    with tempfile.TemporaryDirectory() as d:
        chain = subprocess.run([str(CORE), "run", "-c", program], capture_output=True, cwd=d,
                               timeout=120, env=dict(os.environ, LYPNING_L_BIN=str(LARGER),
                                                     LYPNING_CPYTHON=sys.executable))
    assert (chain.returncode, chain.stdout) == (ref.returncode, ref.stdout), chain.stderr
