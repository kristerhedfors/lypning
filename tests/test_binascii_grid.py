"""`binascii`, as a grid: every program on the binary and on CPython.

`tests/test_base64_grid.py` is the shape this follows. Every row runs from a
FRESH temp cwd (invariant 4) and must end one of exactly two ways:

  * byte-identical stdout AND the same exit code as CPython, or
  * a clean refusal — exit 90, nothing on stdout, one
    ``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

`cap-binascii` serves six names — `hexlify`/`b2a_hex`, `unhexlify`/`a2b_hex`,
`a2b_base64` and `b2a_base64` — bytes in and bytes out. The base64 pair calls
`base64.rs`'s decoder, so the 3.11/3.12 versus 3.13+ decode split it refuses is
refused here too, from the same function.

**Every `binascii.Error` is a refusal, not a raise.** The class does not exist
in this engine, so `binascii.Error` — `except binascii.Error` included — is a
`module-attr` block in the CORE's walk, and every input that would raise one
(odd length, a non-hex digit, bad padding) refuses before the program runs.
`crc32` has no corpus demand (mined 2026-09-24) and is not served.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

B = "import binascii\n"

#: CPython answers every one of these, and lypning-l must answer it too.
SERVED = [
    B + 'print(binascii.hexlify(b"\\x01\\xff").decode(), int.from_bytes(b"\\x01\\x00", "big"))',
    B + 'print(binascii.hexlify(b""), binascii.b2a_hex(b"\\x00\\x10"), '
        'binascii.unhexlify(b"01FF"), binascii.a2b_hex("0a"))',
    B + 'print(binascii.unhexlify(b"0A0b"), binascii.a2b_hex(b""), binascii.hexlify(b"\\x00" * 3))',
    B + 'for s in [b"aGk=!!!", b"aGk=====", b"aGk=\\n"]:\n'
        '    print(repr(s), repr(binascii.a2b_base64(s)))',
    B + 'print(binascii.a2b_base64("aGk="), binascii.a2b_base64(b""), binascii.a2b_base64(b"===="))',
    B + 'print(binascii.b2a_base64(b"hi"), binascii.b2a_base64(b"hi", newline=False), '
        'binascii.b2a_base64(b""), binascii.b2a_base64(b"abc", newline=True))',
    B + 'print(binascii.b2a_base64(b"hi", newline=None), binascii.b2a_base64(b"hi", newline=0), '
        'binascii.b2a_base64(b"hi", newline=1))',
    B + "d = bytes(range(256))\n"
        "print(binascii.unhexlify(binascii.hexlify(d)) == d, "
        "binascii.a2b_base64(binascii.b2a_base64(d)) == d, binascii.hexlify(d).decode() == d.hex())",
    "from binascii import hexlify as h, unhexlify\nprint(h(b'ab'), unhexlify('6162'))",
    "import binascii as B\nx = B.hexlify(b'\\xde\\xad')\n"
    "print(x, len(x), x[0], x[1:], x + b'!', x == b'dead', {x: 1}, '%s' % x, type(x).__name__)",
    B + "f = binascii.hexlify\nprint(f(b'z'))",
    B + "def g(b):\n    return binascii.hexlify(b)\nprint(g(b'\\x07'), g(b''))",
    # a computed argument that the RUN decides and serves
    B + "s = ''.join(['0', '1'])\nprint(binascii.unhexlify(s))",
    B + "print(binascii.a2b_base64('YWJj' if True else 'x'))",
    B + "print(binascii.hexlify(b'a').hex(), binascii.unhexlify(b'ff')[0])",
    # the corpus rows (mined 2026-09-24): first blocker `import binascii`
    B + "for s in [b'aGk=!!!', b'aGk=====', b'aGk=\\n']:\n"
        "    print(repr(s), repr(binascii.a2b_base64(s)))\n",
    "import json, binascii\nprint(json.dumps(binascii.hexlify(b'\\x01').decode()))",
]

#: CPython answers these with a message this engine does not write, or with a
#: surface it does not have. Exit 90 and an EMPTY stdout, both — decided in the
#: WALK, so the refusal lands before anything runs.
REFUSED = [
    B + 'print(binascii.hexlify("ab"))',                          # TypeError
    B + 'print(binascii.hexlify(b"\\x01\\xff\\x02", b":"))',       # sep
    B + 'print(binascii.hexlify(b"\\x01\\xff\\x02", "-", 2))',     # sep, bytes_per_sep
    B + 'print(binascii.hexlify(data=b"a"))',                     # keyword
    B + 'print(binascii.unhexlify("0"))',                         # Odd-length string
    B + 'print(binascii.unhexlify(" 00"))',                       # Odd-length string
    B + 'print(binascii.unhexlify("0g"))',                        # Non-hexadecimal digit
    B + 'print(binascii.unhexlify("\\u00e90"))',                  # ValueError, non-ASCII
    B + 'print(binascii.unhexlify(5))',                           # TypeError
    B + 'print(binascii.a2b_base64(b"aGk"))',                     # Incorrect padding
    B + 'print(binascii.a2b_base64(b"a"))',                       # 1 more than a multiple of 4
    B + 'print(binascii.a2b_base64(b"AA==AA=="))',                # the version split
    B + 'print(binascii.a2b_base64(b"aGk=", strict_mode=False))', # 3.9/3.10: TypeError
    B + 'print(binascii.b2a_base64("hi"))',                       # TypeError
    B + 'print(binascii.b2a_base64(b"hi", False))',               # TypeError
    B + 'x = 3\nprint(binascii.b2a_base64(b"hi", newline=x))',     # a value the walk cannot read
    B + 'print(binascii.crc32(b"hello"))',
    "from binascii import crc32\nprint(crc32(b'a'))",
    B + "E = binascii.Error\nprint(1)",
    B + "try:\n    print(binascii.unhexlify(b'01'))\nexcept binascii.Error as e:\n    print('E', e)",
    B + "try:\n    print(binascii.unhexlify(b'01'))\nexcept (ValueError, binascii.Error):\n    print('E')",
    B + "def g(b):\n    return binascii.hexlify(b)\nprint(g('x'))",
]

#: The residue no walk could read — an argument the source does not spell. It
#: still REFUSES, from `binascii::call`, which asks the same `block` the walk
#: asks.
RUNTIME_BACKSTOP = [
    B + "s = ''.join(['0', '1', 'f'])\nprint(binascii.unhexlify(s))",
    B + "s = ''.join(['0', 'g'])\nprint(binascii.unhexlify(s.encode()))",
    B + "s = 'aG' + 'k'\nprint(binascii.a2b_base64(s))",
    B + "print(binascii.hexlify('a' + 'b'))",
    B + "print(binascii.b2a_base64(b'hi', newline=[]))",
]

#: The barrier class: each shape after ``os.mkdir`` must still be a clean 90
#: with the cwd untouched.
BARRIER = "import os\nos.mkdir('NEWD')\n"
AFTER_A_BARRIER = [
    ("binascii", 'print(binascii.unhexlify("0"))'),
    ("binascii", 'print(binascii.hexlify("ab"))'),
    ("binascii", 'print(binascii.a2b_base64(b"aGk"))'),
    ("module-attr", 'print(binascii.crc32(b""))'),
    ("module-attr", "try:\n    pass\nexcept binascii.Error:\n    pass"),
]

#: Routed by the CORE: into lypning-l on the import, and past it when the
#: program names what no rung serves.
ROUTED_TO_LYPNING_L = [
    B + "print(binascii.hexlify(b'a'))",
    "from binascii import a2b_base64\nprint(a2b_base64(b'aGk='))",
    "import base64, binascii\nprint(base64.b64encode(binascii.unhexlify('00ff')))",
]
ROUTED_PAST_LYPNING_L = [
    B + "print(binascii.crc32(b'a'))",
    B + "print(binascii.b2a_uu(b'a'))",
    "from binascii import Error\nprint(1)",
    B + "try:\n    print(binascii.unhexlify(b'01'))\nexcept binascii.Error:\n    pass",
    # A served FUNCTION in an `except` is not a class: CPython raises
    # TypeError the moment an exception reaches the handler, and the handler
    # used to be admitted and silently skipped. The alias spelling reached
    # neither the resolution nor the binascii escalation.
    B + "try:\n    1/0\nexcept binascii.hexlify:\n    print('b')\nexcept ZeroDivisionError:\n    print('z')",
    "import binascii as b\ntry:\n    1/0\nexcept b.crc32:\n    print('b')\nexcept ZeroDivisionError:\n    print('z')",
    "import binascii as b\ntry:\n    1/0\nexcept b.hexlify:\n    print('b')\nexcept ZeroDivisionError:\n    print('z')",
]

# Reached through this unit: `import binascii` used to route to CPython, so
# these shared-code bugs were unreachable from a binascii program until the
# module was served. Every row is (program, stdout, exit, last stderr line),
# the exact bytes CPython 3.14.5 printed on 2026-09-24, and each must hold on
# BOTH variants — the core shares `builtins.rs`.
_LONG = "invalid literal for int() with base 10: b'" + "x" * 198
EXACT = [
    # int(bytes, base) dropped a POSITIONAL base: `int(b'ff', 16)` raised a
    # base-10 ValueError and the hexlify idiom printed 102.
    ("print(int(b'ff',16))", "255\n", 0, ""),
    ("print(int(b'101',2))", "5\n", 0, ""),
    ("print(int(b' 0x1f ',0))", "31\n", 0, ""),
    ("print(int(b'-0b11',0), int(b'1_0'), int(b'\\x0b12\\x0c'), int(b'z', 36))",
     "-3 10 12 35\n", 0, ""),
    ("print(int(b'zz',16))", "", 1,
     "ValueError: invalid literal for int() with base 16: b'zz'"),
    ("print(int(b\"a'b\", 16))", "", 1,
     "ValueError: invalid literal for int() with base 16: b\"a'b\""),
    ("print(int(b'010',0))", "", 1,
     "ValueError: invalid literal for int() with base 0: b'010'"),
    # Never decoded: an Arabic-Indic digit in UTF-8 is not a digit to bytes.
    ("print(int(b'\\xd9\\xa1'))", "", 1,
     "ValueError: invalid literal for int() with base 10: b'\\xd9\\xa1'"),
    ("print(int(b'x'*205))", "", 1, "ValueError: " + _LONG),
    ("print(int(b'12', 1))", "", 1,
     "ValueError: int() base must be >= 2 and <= 36, or 0"),
    # `keyword argument repeated` is a compile error: nothing before it runs.
    ("print(1)\nprint(sorted([2,1], reverse=True, reverse=False))", "", 1,
     "SyntaxError: keyword argument repeated: reverse"),
]
EXACT_L = [
    (B + "print(int(binascii.hexlify(b'\\x01\\x02'), 16))", "258\n", 0, ""),
    (B + "print(int(binascii.b2a_hex(b'\\xff\\x00'), 16))", "65280\n", 0, ""),
    (B + "print(1)\nprint(binascii.b2a_base64(b'a', newline=False, newline=True))", "", 1,
     "SyntaxError: keyword argument repeated: newline"),
]
# A keyword given twice through `**` is a TypeError whose message names the
# callee; every callee here took the LAST value and answered at exit 0.
DUPLICATE_KW = [
    B + "print(binascii.b2a_base64(b'a', newline=True, **{'newline': False}))",
    B + "print(binascii.b2a_base64(b'a', **{'newline': False}, **{'newline': True}))",
    B + "kw = {'newline': 0}\nprint(binascii.b2a_base64(b'a', newline=1, **kw))",
]
DUPLICATE_KW_CORE = [
    "print(sorted([3,1], reverse=True, **{'reverse': False}))",
    "def f(**k):\n    return k\nprint(f(a=1, **{'a': 2}))",
]


def _spectrum(binary: Path) -> dict | None:
    try:
        out = subprocess.run([str(binary), "route", "--spectrum"],
                             capture_output=True, text=True, timeout=60)
    except OSError:
        return None
    try:
        import json
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def _current(engine: str, cap: str) -> Path | None:
    """A built ``engine`` that carries THIS tree's capability table — an older
    binary would turn every row into a green skip measuring nothing."""
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
        if any(row.get("cap") == cap for row in table.get("caps", [])):
            return cand
    return None


BINARY = _current(engines.LYPNING_L, "cap-binascii")
CORE = _current(engines.LYPNING, "cap-binascii")

needs_l = pytest.mark.skipif(
    BINARY is None,
    reason="no lypning-l carrying cap-binascii is built (lypning build --rust)",
)
needs_core = pytest.mark.skipif(
    CORE is None or BINARY is None,
    reason="routing needs the CORE that routes and the lypning-l it routes to",
)


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
@pytest.mark.parametrize("program", REFUSED + RUNTIME_BACKSTOP,
                         ids=range(len(REFUSED) + len(RUNTIME_BACKSTOP)))
def test_the_surface_outside_the_slice_refuses_rather_than_guesses(program: str) -> None:
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got)
    assert problem is None, "this program must refuse, not answer: %s\n  program: %r" % (
        problem, program)


@needs_l
@pytest.mark.parametrize("kind,call", AFTER_A_BARRIER, ids=range(len(AFTER_A_BARRIER)))
def test_every_static_refusal_lands_before_the_barrier(kind: str, call: str) -> None:
    program = B + BARRIER + call
    with tempfile.TemporaryDirectory() as d:
        before = sorted(os.listdir(d))
        got = subprocess.run([str(BINARY), "-c", program], capture_output=True,
                             text=True, cwd=d, timeout=60)
        after = sorted(os.listdir(d))
    problem = _refusal_problem(got)
    assert problem is None, "%s\n  program: %r" % (problem, program)
    assert ": %s: " % kind in got.stderr, got.stderr
    assert before == after, "the refusal landed AFTER the barrier: %r -> %r" % (before, after)


@needs_core
@pytest.mark.parametrize("program", ROUTED_TO_LYPNING_L, ids=range(len(ROUTED_TO_LYPNING_L)))
def test_the_core_routes_a_served_call_into_lypning_l(program: str) -> None:
    route = engines.route(program, binary=CORE)
    assert route.engine == engines.LYPNING_L, (route.engine, route.kind, route.detail)


@needs_core
@pytest.mark.parametrize("program", ROUTED_PAST_LYPNING_L,
                         ids=range(len(ROUTED_PAST_LYPNING_L)))
def test_the_core_routes_what_no_rung_serves_to_cpython(program: str) -> None:
    route = engines.route(program, binary=CORE)
    assert route.engine == engines.CPYTHON, (route.engine, route.kind, route.detail)
    assert _run([str(BINARY)], program).returncode == engines.UNSUPPORTED_EXIT


def _exact_problem(got: subprocess.CompletedProcess, out: str, code: int, last: str) -> str | None:
    tail = got.stderr.strip().splitlines()[-1:] or [""]
    if (got.stdout, got.returncode, tail[0]) != (out, code, last):
        return "got %r exit %d %r; want %r exit %d %r" % (
            got.stdout, got.returncode, tail[0][:260], out, code, last[:260])
    return None


@needs_core
@pytest.mark.parametrize("row", EXACT, ids=range(len(EXACT)))
def test_shared_code_reached_through_binascii_answers_cpythons_bytes(row) -> None:
    program, out, code, last = row
    for binary in (CORE, BINARY):
        problem = _exact_problem(_run([str(binary)], program), out, code, last)
        assert problem is None, "%s: %s\n  program: %r" % (binary, problem, program)


@needs_l
@pytest.mark.parametrize("row", EXACT_L, ids=range(len(EXACT_L)))
def test_binascii_programs_answer_cpythons_bytes(row) -> None:
    program, out, code, last = row
    problem = _exact_problem(_run([str(BINARY)], program), out, code, last)
    assert problem is None, "%s\n  program: %r" % (problem, program)


@needs_core
@pytest.mark.parametrize("program", DUPLICATE_KW + DUPLICATE_KW_CORE,
                         ids=range(len(DUPLICATE_KW) + len(DUPLICATE_KW_CORE)))
def test_a_keyword_given_twice_through_dstar_refuses(program: str) -> None:
    binaries = [BINARY] if program in DUPLICATE_KW else [CORE, BINARY]
    for binary in binaries:
        got = _run([str(binary)], program)
        assert got.returncode == engines.UNSUPPORTED_EXIT and got.stdout == "", (
            binary, got.returncode, got.stdout, got.stderr)
        assert got.stderr.count("\n") == 1 and ": unsupported: call: " in got.stderr, got.stderr


@needs_core
def test_a_served_function_in_an_except_clause_routes_to_cpython() -> None:
    program = ("import math\ntry:\n    1/0\nexcept math.sqrt:\n    print('m')\n"
               "except ZeroDivisionError:\n    print('z')")
    route = engines.route(program, binary=CORE)
    assert route.engine == engines.CPYTHON, (route.engine, route.kind, route.detail)
    assert route.kind == "exception", (route.kind, route.detail)
