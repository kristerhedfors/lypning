"""`cap-random` and `sys.version_info`, as a grid: every program on the binary
and on CPython.

`tests/test_hashlib_grid.py` is the shape this follows. Every row runs from a
FRESH temp cwd (invariant 4) and must end one of exactly two ways:

  * byte-identical **stdout and exit code** (and the last stderr line) as the
    reference CPython, or
  * a clean refusal — exit 90, nothing on stdout, one
    ``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

`SERVED` is stricter: those rows are the capability, so a refusal there is a
failure, not a skip.

**What this capability is.** Three names on `random` — the `Random(int)`
CONSTRUCTOR, `sample` and `shuffle` — and four spellings of `sys.version_info`
(`[0]`/`[1]`, `[:2]`, `.major`/`.minor`, and a comparison against a tuple
literal of at most two elements). The stream is the one `random.rs` already
reproduces bit for bit; what is new is a second STATE (an instance has its own
MT19937, carried as a `Value::IterObj` and never as a new `Value` variant) and
two Python-level algorithms from `random.py` that consume that stream in a
specific order: `sample`'s set-size rule with its two branches, and the 3.11+
`shuffle`. A draw consumed out of order is a plausible wrong list at exit 0,
which is why the grid pins the stream AFTER each call as well (`random()` on
the same line).

`sys.version_info` is grounded only to the MINOR version (`err::REF_PY_MINOR`,
checked by `doctor`), so every spelling that would print the micro version,
the release level or the struct's repr is refused — statically, in the core's
walk, so the router sends it to CPython in one step.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

R = "import random\n"
S = "import sys\n"

#: The capability's own rows: each must ANSWER on lypning-l (never refuse) and
#: agree with CPython byte for byte.
SERVED = [R + x for x in [
    # The instance: its own stream, seeded like the module's.
    "r=random.Random(5)\nprint(r.randint(1,100), r.random(), r.choice('abc'), r.randrange(10))",
    "r=random.Random(3); L=list('abcdefg'); r.shuffle(L); print(L)",
    "a=random.Random(42); random.seed(1); b=random.Random(42); print(a.random()==b.random(), random.random())",
    "r=random.Random(7); print([r.getrandbits(k) for k in (1, 8, 31, 32, 33, 63)])",
    "r=random.Random(-7); print(r.random())",
    "r=random.Random(0); print(r.random())",
    "r=random.Random(True); print(r.random())",
    "r=random.Random(2**40); print(r.randrange(3, 1000), r.randint(-5, 5))",
    "r=random.Random(1); r.seed(2); print(r.random()); random.seed(2); print(random.random())",
    "r=random.Random(9); print(r.sample(range(100), 8), r.sample('abc', 3), r.random())",
    "rs=[random.Random(i) for i in range(3)]; print([x.randint(1, 6) for x in rs])",
    "r=random.Random(5); f=r.randint; print(f(1, 10), f(1, 10))",
    "r=random.Random(5); print(bool(r), r == r, r is r, r != r)",
    "r=random.Random(5); s=random.Random(5); print(r == s, r is s)",
    "random.seed(3)\nr = random.Random(3)\nprint(random.random() == r.random())",
    "print(random.Random(11).random(), random.Random(11).randint(0, 9))",
    "r = random.Random(1111)\nids = list(range(20))\nr.shuffle(ids)\nprint(ids)",
    "rng = random.Random(7)\nprint([rng.randrange(1 << 30) for _ in range(3)])",
    "import sys\nrng = random.Random(int('12'))\nprint(rng.choice([1, 2, 3]), rng.random())",
    "r = random.Random(4)\nx = [1, 2, 3, 4, 5]\nprint(r.shuffle(x), x)",
    # The module-level functions: the hidden instance's stream.
    "random.seed(4); x=[1,2,3,4,5]; random.shuffle(x); print(x, random.random())",
    "random.seed(4); print(random.sample([1,2,3,4], 2))",
    "random.seed(11); print(random.sample(range(100), 8), random.random())",
    "random.seed(1); print(random.sample(list(range(1000)), 6))",
    "random.seed(1); print(random.sample('abcdefghij', 10))",
    "random.seed(2); print(random.sample(range(10**6), 30)[:3], random.random())",
    "random.seed(5); print(random.sample((1, 2, 3), 0), random.sample([], 0), random.random())",
    "random.seed(6); print(random.sample(((1, 'a'), (2, 'b'), (3, 'c')), 2))",
    "random.seed(6); print(random.sample('héllo wörld', 4))",
    "random.seed(6); print(bytes(random.sample(b'-_!?+/~.', 2)))",
    # The set-size boundaries: `n <= setsize` is the pool branch, one more is
    # the rejection-set branch, and the draws differ.
    "random.seed(8); print(random.sample(range(21), 5), random.random())",
    "random.seed(8); print(random.sample(range(22), 5), random.random())",
    "random.seed(8); print(random.sample(range(85), 6), random.random())",
    "random.seed(8); print(random.sample(range(86), 6), random.random())",
    "random.seed(8); print(random.sample(range(277), 22), random.random())",
    "random.seed(8); print(random.sample(range(278), 22), random.random())",
    "random.seed(8); print(random.sample(range(85), 21), random.random())",
    "random.seed(8); print(random.sample(range(86), 21), random.random())",
    "random.seed(9); print(sorted(random.sample(range(50), 50)) == list(range(50)), random.random())",
    "random.seed(0); x=list(range(1000)); random.shuffle(x); print(sum(i*v for i, v in enumerate(x)), random.random())",
    "random.seed(0); x=[]; random.shuffle(x); y=[1]; random.shuffle(y); print(x, y, random.random())",
    "random.seed(0); x=['a', 'b']; random.shuffle(x); print(x, random.random())",
    "import random as rr\nrr.seed(3)\nprint(rr.sample(range(100), 5))",
    "from random import sample, shuffle, seed\nseed(3)\nx = list('abcdef')\nshuffle(x)\nprint(x, sample(x, 2))",
    "random.seed(3)\nfor a in random.sample([10, 20, 30, 40], 3):\n    print(a)",
    "print(random.sample is random.sample, random.shuffle == random.shuffle)",
    # `getrandbits(64)`: two words, low first, and a wide integer at or past
    # 2**63 — the idiom `struct.unpack('<d', struct.pack('<Q', bits))` feeds on.
    "random.seed(7); print(random.getrandbits(64), random.getrandbits(33), random.random())",
    "random.seed(3); print([random.getrandbits(64) for _ in range(8)], random.random())",
    "r=random.Random(7); print([r.getrandbits(64) for _ in range(4)], r.getrandbits(1))",
    "random.seed(5); x = random.getrandbits(64); print(x < 2**64, x >= 0, hex(x), x % 1000, x // 2**32)",
    "random.seed(11); print(sum(random.getrandbits(64) for _ in range(100)))",
    "from random import getrandbits, seed\nseed(2)\nprint(getrandbits(64), getrandbits(64))",
]] + [S + x for x in [
    "print(sys.version_info[0], sys.version_info.major, sys.version_info >= (3, 8))",
    "print(sys.version_info[:2])",
    "print(sys.version_info[1] == sys.version_info.minor)",
    "print(sys.version_info.minor >= 9)",
    "print(sys.version_info < (3,), sys.version_info > (3,), sys.version_info == (3, 14), sys.version_info != (3, 14))",
    "print(sys.version_info >= (3, 14), sys.version_info <= (3, 14), sys.version_info > (3, 14), sys.version_info < (3, 14))",
    "print(sys.version_info >= (3, 13), sys.version_info <= (3, 13), sys.version_info == (3, 13))",
    "print(sys.version_info >= (3, 15), sys.version_info < (4, 0), sys.version_info == ())",
    "print(() < sys.version_info, (3, 9) <= sys.version_info < (4,), (3,) == sys.version_info)",
    "if sys.version_info < (3, 8):\n    print('old')\nelse:\n    print('new')",
    "print('%d.%d' % sys.version_info[:2])",
    "print('py', sys.version_info[:2])",
    "print(sys.version_info[:1], sys.version_info[:0])",
    "import sys as s\nprint(s.version_info[:2], s.version_info.major)",
    "print(\"3.%d\" % sys.version_info[1])",
    "print(\"%d.%d\" % (sys.version_info[0], sys.version_info[1]))",
    "import os,sys\nprint(sys.version_info>=(3,))",
    "print(sys.version_info.major.bit_length())",
]]

#: Rows CPython answers with an error. Served shapes whose error is the
#: program's own; stdout and the exit code must agree, or the row refuses.
RAISES = [S + x for x in [
    "print(sys.version_info >= (3, 'a'))",
]]

#: Rows either answer exactly or refuse cleanly — never a third thing. The
#: `type()` of a bound method is an engine-wide refusal today, and a computed
#: index or a tuple held in a name is a shape the WALK sends to CPython but the
#: runtime can still answer exactly (`randobj::vi_index`, `vi_operand`).
AGREE_OR_REFUSE = [R + x for x in [
    "print(type(random.sample).__name__, type(random.shuffle).__name__)",
    "r = random.Random(1)\nprint(type(r.randint).__name__, type(r.random).__name__, type(r.getrandbits).__name__)",
    "r = random.Random(1)\nprint(type(r.sample).__name__, type(r.shuffle).__name__, type(r.seed).__name__)",
    "r = random.Random(1)\nprint(r.randint == r.randint, r.randint is r.randint)",
    "r = random.Random(1)\nprint(r + 1)",
    "r = random.Random(1)\nprint(r[0])",
    "import json\nprint(json.dumps(random.Random(1)))",
    "print(random.Random(1) == 1, random.Random(1) != None)",
    "random.seed(1); print(random.getrandbits(64) & 0xff, float(random.getrandbits(64)))",
    "random.seed(1); print({random.getrandbits(64): 1})",
]] + [S + x for x in [
    "t = (3, 8)\nprint(sys.version_info >= t)",
    "t = (3, 8, 1)\nprint(sys.version_info >= t)",
    "i = 0\nprint(sys.version_info[i])",
    "i = 2\nprint(sys.version_info[i])",
    "print(sys.version_info[True])",
    "print(sys.version_info >= (3, 8) and sys.version_info[:2] != (2, 7))",
]]

#: Everything outside the slice. Each must REFUSE on lypning-l — exit 90,
#: empty stdout, one refusal line — whatever CPython does with it.
REFUSED = [R + x for x in [
    "print(random.Random().random())",
    "print(random.Random('abc').random())",
    "print(random.Random(1.5).random())",
    "print(random.Random(b'x').random())",
    "print(random.Random(None).random())",
    "print(random.Random(1, 2).random())",
    "print(random.Random(x=1).random())",
    "print(random.Random(5))",
    "print(type(random.Random(5)))",
    "print(repr(random.Random(5)))",
    "r = random.Random(5)\nprint(r.randint(1, 2), r)",
    "print(random.sample({1, 2, 3}, 2))",
    "print(random.sample({1: 2}, 1))",
    "random.seed(1); print(random.sample([1, 2], 3))",
    "random.seed(1); print(random.sample([1, 2], -1))",
    "random.seed(1); print(random.sample([1, 2], k=1))",
    "random.seed(1); print(random.sample([1, 2], 1, counts=[1, 1]))",
    "random.seed(1); print(random.sample([1, 2]))",
    "random.seed(1); print(random.sample([1, 2], 1.0))",
    "random.seed(1); print(random.sample(iter([1, 2]), 1))",
    "random.seed(1); x = (1, 2, 3); random.shuffle(x); print(x)",
    "random.seed(1); random.shuffle('abc')",
    "random.seed(1); x = [1, 2]; random.shuffle(x, 1)",
    "print(random.sample([1, 2, 3], 2))",
    "x = [1, 2]\nrandom.shuffle(x)\nprint(x)",
    "r = random.Random(5); print(r.gauss(0, 1))",
    "r = random.Random(5); print(r.uniform(0, 1))",
    "r = random.Random(5); print(r.choices([1, 2], k=3))",
    "r = random.Random(5); print(r.getstate())",
    "r = random.Random(5); print(r.random(1))",
    "r = random.Random(5); print(r.randint(1, 5, step=1))",
    "r = random.Random(5); print(isinstance(r, random.Random))",
    "print(random.Random)",
    "R = random.Random\nprint(R(5).random())",
    "from random import Random\nprint(Random(5).random())",
    "print(list(random.Random(1)))",
    "for x in random.Random(1):\n    print(x)",
    "print(1 in random.Random(1))",
    "print(len(random.Random(1)))",
    "print({random.Random(1): 1})",
    "print(random.Random(2**64 + 1).random())",
    "print(random.Random(9).randrange(0, 2**63))",
    "print(random.SystemRandom().random())",
    "r = random.Random(3)\nr.seed()\nprint(r.random())",
    "r = random.Random(3)\nr.seed('a')\nprint(r.random())",
    "r = random.Random(3)\nprint(r.choice([]))",
    "random.seed(1); print(random.getrandbits(65))",
    "r = random.Random(1); print(r.getrandbits(128))",
]] + [S + x for x in [
    "print(sys.version_info)",
    "print(repr(sys.version_info))",
    "print(sys.version_info[:3])",
    "print(sys.version_info[2])",
    "print(sys.version_info[-1])",
    "print(sys.version_info.micro)",
    "print(sys.version_info.releaselevel)",
    "print(len(sys.version_info))",
    "print(tuple(sys.version_info))",
    "print(list(sys.version_info))",
    "print(sys.version_info >= (3, 8, 1))",
    "print(sys.version_info == (3, 14, 5, 'final', 0))",
    "print(sys.version_info >= [3, 8])",
    "print(3 in sys.version_info)",
    "print(sys.version_info[0:2])",
    "print(sys.version_info[:2:1])",
    "print(sys.version_info[:-1])",
    "v = sys.version_info\nprint(v[0])",
    "from sys import version_info\nprint(version_info[0])",
    "print(type(sys.version_info))",
    "print(sys.version_info + (1,))",
    "print(sys.version_info is sys.version_info)",
    "print(sys.version_info == sys.version_info)",
    # `sys.version` itself is served now, from a fingerprinted bake:
    # tests/test_sys_version_grid.py. `hexversion` carries the micro and is not.
    "print(sys.hexversion)",
    "print('%d.%d.%d' % sys.version_info[:3])",
    "print(__import__('sys').version_info[:2])",
]]

#: Served rows whose FIRST blocker in the core is the new name, and the rows it
#: must hand to CPython instead because nothing on the spectrum serves them.
ROUTE_TO_L = [
    R + "r=random.Random(5)\nprint(r.randint(1, 100))",
    R + "random.seed(1)\nprint(random.sample(range(10), 3))",
    R + "random.seed(1)\nx = [1, 2, 3]\nrandom.shuffle(x)\nprint(x)",
    R + "random.Random(3).shuffle([1, 2])",
    R + "from random import sample\nprint(sample([1], 1))",
    S + "print(sys.version_info[0])",
    S + "print(sys.version_info[:2])",
    S + "print(sys.version_info.major, sys.version_info.minor)",
    S + "print(sys.version_info >= (3, 9))",
    S + "print('%d.%d' % sys.version_info[:2])",
]
ROUTE_TO_CPYTHON = [
    R + "print(random.Random().random())",
    R + "print(random.Random('abc').random())",
    R + "print(random.Random(1, 2).random())",
    R + "print(random.Random)",
    R + "from random import Random",
    R + "random.seed(1)\nprint(random.sample([1, 2], k=1))",
    R + "random.seed(1)\nprint(random.sample([1, 2], 1, counts=[1, 1]))",
    R + "r = random.Random(5)\nprint(r.gauss(0, 1))",
    R + "r = random.Random(5)\nprint(r.choices([1], k=2))",
    R + "random.seed(1)\nprint(random.sample(range(9), 2), random.choices([1], k=2))",
    R + "print(random.shuffle([1, 2]), random.uniform(0, 1))",
    S + "print(sys.version_info)",
    S + "print(sys.version_info[:3])",
    S + "print(sys.version_info.micro)",
    S + "print(sys.version_info >= (3, 8, 1))",
    S + "v = sys.version_info",
    S + "from sys import version_info",
    S + "print(sys.version_info[0], sys.hexversion)",
]


def _spectrum(binary: Path) -> dict | None:
    try:
        out = subprocess.run([str(binary), "route", "--spectrum"],
                             capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError):
        return None


def _current(engine: str) -> Path | None:
    """A built ``engine`` that carries THIS tree's capability table — an older
    binary refuses every row, which would make the file green skips."""
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
        if any(row.get("cap") == "cap-random" for row in table.get("caps", [])):
            return cand
    return None


BINARY = _current(engines.LYPNING_L)
CORE = _current(engines.LYPNING)

needs_l = pytest.mark.skipif(
    BINARY is None, reason="no lypning-l carrying cap-random is built")
needs_core = pytest.mark.skipif(
    CORE is None, reason="no core carrying this tree's capability table is built")


def _run(argv: list[str], program: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True, text=True,
                              cwd=d, timeout=120)


def _refusal_problem(got: subprocess.CompletedProcess, engine: str) -> str | None:
    if got.returncode != engines.UNSUPPORTED_EXIT:
        return "exit %d, not %d" % (got.returncode, engines.UNSUPPORTED_EXIT)
    if got.stdout != "":
        return "stdout was not empty: %r" % got.stdout[:120]
    head = "%s: unsupported: " % engine
    line = got.stderr.strip()
    if not line.startswith(head) or "\n" in line:
        return "stderr was %r, expected one %r line" % (line[:160], head)
    return None


def _engine_of(binary: Path, program: str) -> str:
    out = subprocess.run([str(binary), "route", "-c", program],
                         capture_output=True, text=True, timeout=60)
    return out.stdout.split("\t")[0].strip()


@needs_l
@pytest.mark.parametrize("program", SERVED, ids=range(len(SERVED)))
def test_the_served_grid_answers_exactly_what_cpython_answers(program: str) -> None:
    got = _run([str(BINARY)], program)
    ref = _run([sys.executable], program)
    assert got.returncode != engines.UNSUPPORTED_EXIT, (
        "a served row refused\n  program: %r\n  stderr: %r" % (program, got.stderr.strip()))
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "lypning-l disagrees with CPython.\n  program:  %r\n  lypning-l: %r exit %d %r\n"
        "  cpython:   %r exit %d %r" % (program, got.stdout, got.returncode,
                                        got.stderr.strip()[-300:], ref.stdout,
                                        ref.returncode, ref.stderr.strip()[-300:]))


@needs_l
@pytest.mark.parametrize("program", RAISES, ids=range(len(RAISES)))
def test_a_raising_row_agrees_or_refuses(program: str) -> None:
    got = _run([str(BINARY)], program)
    if got.returncode == engines.UNSUPPORTED_EXIT:
        assert _refusal_problem(got, engines.LYPNING_L) is None
        return
    ref = _run([sys.executable], program)
    assert ref.returncode != 0
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode)
    assert got.stderr.strip().splitlines()[-1:] == ref.stderr.strip().splitlines()[-1:]


@needs_l
@pytest.mark.parametrize("program", AGREE_OR_REFUSE, ids=range(len(AGREE_OR_REFUSE)))
def test_a_row_answers_exactly_or_refuses_cleanly(program: str) -> None:
    got = _run([str(BINARY)], program)
    if got.returncode == engines.UNSUPPORTED_EXIT:
        assert _refusal_problem(got, engines.LYPNING_L) is None, (program, got.stderr)
        return
    ref = _run([sys.executable], program)
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "  program:  %r\n  lypning-l: %r exit %d %r\n  cpython:   %r exit %d %r"
        % (program, got.stdout, got.returncode, got.stderr.strip()[-200:],
           ref.stdout, ref.returncode, ref.stderr.strip()[-200:]))


@needs_l
@pytest.mark.parametrize("program", REFUSED, ids=range(len(REFUSED)))
def test_the_surface_outside_the_slice_refuses_rather_than_guesses(program: str) -> None:
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got, engines.LYPNING_L)
    assert problem is None, "this row must refuse: %s\n  program: %r\n  stderr: %r" % (
        problem, program, got.stderr.strip()[:200])


@needs_l
@pytest.mark.parametrize("program", REFUSED, ids=range(len(REFUSED)))
def test_the_core_refuses_the_same_rows(program: str) -> None:
    """The core serves none of this and must refuse it too — never answer."""
    if CORE is None:
        pytest.skip("no core carrying this tree's capability table is built")
    got = _run([str(CORE)], program)
    assert _refusal_problem(got, engines.LYPNING) is None, (program, got.stderr)


@needs_core
@pytest.mark.parametrize("program", ROUTE_TO_L, ids=range(len(ROUTE_TO_L)))
def test_the_core_routes_a_served_shape_to_the_larger_variant(program: str) -> None:
    if BINARY is None:
        pytest.skip("no lypning-l carrying cap-random is built")
    assert _engine_of(CORE, program) == engines.LYPNING_L, program
    assert _engine_of(BINARY, program) == engines.LYPNING_L, program


@needs_core
@pytest.mark.parametrize("program", ROUTE_TO_CPYTHON, ids=range(len(ROUTE_TO_CPYTHON)))
def test_the_core_routes_an_unserved_shape_straight_to_cpython(program: str) -> None:
    """A shape the walk can see and no rung serves is decided in the CORE's
    walk, so the program never enters `lypning-l` to be refused there."""
    assert _engine_of(CORE, program) == engines.CPYTHON, program
    if BINARY is not None:
        assert _engine_of(BINARY, program) == engines.CPYTHON, program


@needs_core
def test_the_capability_is_on_the_larger_variant_only() -> None:
    for program in (R + "random.seed(1)\nprint(random.sample([1], 1))",
                    R + "print(random.Random(1).random())",
                    S + "print(sys.version_info[0])"):
        got = _run([str(CORE)], program)
        assert _refusal_problem(got, engines.LYPNING) is None, (program, got.stderr)
        assert ": module-attr: " in got.stderr, got.stderr


@needs_l
def test_the_python_copy_of_the_capability_table_is_the_binarys_own() -> None:
    table = _spectrum(BINARY)
    assert table is not None and table["self"] == engines.LYPNING_L
    assert "cap-random" in table["self_caps"]
    assert {r["name"]: tuple(r["caps"]) for r in table["spectrum"]} == engines.VARIANT_CAPS
    assert {r["cap"]: r["modules"] for r in table["caps"]}["cap-random"] == []
