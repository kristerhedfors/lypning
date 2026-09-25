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
(odd length, a non-hex digit, bad padding) refuses before the program runs. An
`except binascii.Error` no exception reaches is never evaluated, by CPython or
by the core, so a direct run answers it; one that an exception reaches refuses
there (`ANSWERED_UNREACHED`).
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
    B + 'print(binascii.b2a_base64(b"hi", newline=0), binascii.b2a_base64(b"hi", newline=1))',
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
    B + "try:\n    print(binascii.unhexlify(b'0'))\nexcept binascii.Error as e:\n    print('E', e)",
    B + "try:\n    print(binascii.unhexlify(b'0'))\nexcept (ValueError, binascii.Error):\n    print('E')",
    B + "try:\n    int('x')\nexcept binascii.Error:\n    print('E')\nexcept ValueError:\n    print('V')",
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
    ("binascii", "try:\n    binascii.unhexlify(b'0')\nexcept binascii.Error:\n    pass"),
]

#: An `except binascii.Error` no exception reaches: CPython never evaluates
#: the clause, and neither does the core, so a direct run answers as both do.
#: The ROUTE still goes past the rungs (`ROUTED_PAST_LYPNING_L`).
ANSWERED_UNREACHED = [
    B + "try:\n    print(binascii.unhexlify(b'01'))\nexcept binascii.Error:\n    pass",
    B + "try:\n    print(binascii.unhexlify(b'01'))\nexcept (ValueError, binascii.Error):\n    print('E')",
    BARRIER + "try:\n    print(1)\nexcept binascii.Error:\n    pass",
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
    B + "try:\n    print(binascii.unhexlify(b'0'))\nexcept binascii.Error:\n    pass",
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
    # ONE sign, before the prefix only: the parser stripped one and then let
    # `from_str_radix` read a second, so `int('--12')` was 12 and `int('0x-1',
    # 16)` was -1, both at exit 0 (round 2, 2026-09-24).
    ("print(int('--12'))", "", 1,
     "ValueError: invalid literal for int() with base 10: '--12'"),
    ("print(int(b'0x-1',16))", "", 1,
     "ValueError: invalid literal for int() with base 16: b'0x-1'"),
    ("print(int('-+12'))", "", 1,
     "ValueError: invalid literal for int() with base 10: '-+12'"),
    ("print(int(b'++1',16))", "", 1,
     "ValueError: invalid literal for int() with base 16: b'++1'"),
    ("print(int(b'0x+1',0))", "", 1,
     "ValueError: invalid literal for int() with base 0: b'0x+1'"),
    ("print(int(b'+-0x1f',0))", "", 1,
     "ValueError: invalid literal for int() with base 0: b'+-0x1f'"),
    ("print(int('-0x-1',16))", "", 1,
     "ValueError: invalid literal for int() with base 16: '-0x-1'"),
    ("print(int('-0x1f',16), int(' +12 '), int('-0b1_0', 0), int('+0o7', 8))",
     "-31 12 -2 7\n", 0, ""),
    ("print(int(base=16))", "", 1, "TypeError: int() missing string argument"),
    # float(bytes) is ASCII text, never decoded, and the message names the bytes.
    ("print(float(b' 1.5 '), float(b'1_5'), float(b'-inf'), float(b'1e5'))",
     "1.5 15.0 -inf 100000.0\n", 0, ""),
    ("print(float(b'\\xff1'))", "", 1,
     "ValueError: could not convert string to float: b'\\xff1'"),
    ("print(float(b''))", "", 1, "ValueError: could not convert string to float: b''"),
    # The UTF-8 decode error names CPython's reason and byte range.
    ("print(b'\\xc3'.decode())", "", 1,
     "UnicodeDecodeError: 'utf-8' codec can't decode byte 0xc3 in position 0: "
     "unexpected end of data"),
    ("print(b'\\xe2\\x82'.decode())", "", 1,
     "UnicodeDecodeError: 'utf-8' codec can't decode bytes in position 0-1: "
     "unexpected end of data"),
    ("print(b'\\xc3\\x28'.decode())", "", 1,
     "UnicodeDecodeError: 'utf-8' codec can't decode byte 0xc3 in position 0: "
     "invalid continuation byte"),
    ("print(b'ab\\xe2\\x82\\x28'.decode())", "", 1,
     "UnicodeDecodeError: 'utf-8' codec can't decode bytes in position 2-3: "
     "invalid continuation byte"),
    ("print(b'\\xed\\xa0\\x80'.decode())", "", 1,
     "UnicodeDecodeError: 'utf-8' codec can't decode byte 0xed in position 0: "
     "invalid continuation byte"),
    ("print(b'\\xf0\\x9f\\x98'.decode())", "", 1,
     "UnicodeDecodeError: 'utf-8' codec can't decode bytes in position 0-2: "
     "unexpected end of data"),
    ("print(b'a\\x80'.decode())", "", 1,
     "UnicodeDecodeError: 'utf-8' codec can't decode byte 0x80 in position 1: "
     "invalid start byte"),
    # ASCII is not UTF-8: this answered 'é' at exit 0.
    ("print(b'\\xc3\\xa9'.decode('ascii'))", "", 1,
     "UnicodeDecodeError: 'ascii' codec can't decode byte 0xc3 in position 0: "
     "ordinal not in range(128)"),
    # bytes + anything else is `sq_concat`'s message, not the generic one.
    ("print(b'a' + 'x')", "", 1, "TypeError: can't concat str to bytes"),
    ("print(b'a' + None)", "", 1, "TypeError: can't concat NoneType to bytes"),
    # Keywords and `**` mappings are gathered, and evaluated, in source order.
    ("f = lambda **k: k\nprint(f(x=1, **{'y':2}, z=3))",
     "{'x': 1, 'y': 2, 'z': 3}\n", 0, ""),
    ("f = lambda **k: k\nprint(f(**{'y':2}, x=1, **{'w':0}, z=3))",
     "{'y': 2, 'x': 1, 'w': 0, 'z': 3}\n", 0, ""),
    ("def g(n):\n    print(n)\n    return n\nf = lambda **k: k\n"
     "print(f(a=g(1), **g({'b':2}), c=g(3)))",
     "1\n{'b': 2}\n3\n{'a': 1, 'b': 2, 'c': 3}\n", 0, ""),
    # `keyword argument repeated` is a compile error: nothing before it runs.
    ("print(1)\nprint(sorted([2,1], reverse=True, reverse=False))", "", 1,
     "SyntaxError: keyword argument repeated: reverse"),
]
EXACT_L = [
    (B + "print(int(binascii.hexlify(b'\\x01\\x02'), 16))", "258\n", 0, ""),
    (B + "print(int(binascii.b2a_hex(b'\\xff\\x00'), 16))", "65280\n", 0, ""),
    (B + "print(1)\nprint(binascii.b2a_base64(b'a', newline=False, newline=True))", "", 1,
     "SyntaxError: keyword argument repeated: newline"),
    (B + "print(int(binascii.unhexlify('2d2d31')))", "", 1,
     "ValueError: invalid literal for int() with base 10: b'--1'"),
    (B + "print(float(binascii.hexlify(b'\\x12')))", "12.0\n", 0, ""),
    (B + "print(float(binascii.hexlify(b'abc')))", "616263.0\n", 0, ""),
    (B + "print(binascii.unhexlify('c3').decode())", "", 1,
     "UnicodeDecodeError: 'utf-8' codec can't decode byte 0xc3 in position 0: "
     "unexpected end of data"),
    (B + "h = binascii.hexlify(b'abc')\nprint(h + 'x')", "", 1,
     "TypeError: can't concat str to bytes"),
    (B + "f = lambda **k: k\nprint(f(x=1, **{'y':2}, z=3))",
     "{'x': 1, 'y': 2, 'z': 3}\n", 0, ""),
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


#: `newline` is read by truth from CPython 3.12; before that it was a C `int`,
#: so `None` (and a `str`, a `bytes`) is a TypeError the engine does not word
#: and refuses, statically and at runtime (`binascii::truth`).
NEWLINE_BY_TRUTH = [
    B + 'print(binascii.b2a_base64(b"hi", newline=None))',
    B + 'x = None\nprint(binascii.b2a_base64(b"hi", newline=x))',
    B + 'x = ""\nprint(binascii.b2a_base64(b"hi", newline=x), binascii.b2a_base64(b"hi", newline=b"1"))',
    B + 'print(binascii.b2a_base64(b"hi", newline=2**40))',
]


@needs_l
@pytest.mark.parametrize("program", NEWLINE_BY_TRUTH, ids=range(len(NEWLINE_BY_TRUTH)))
def test_newline_follows_the_references_conversion(program: str) -> None:
    got = _run([str(BINARY)], program)
    ref = _run([sys.executable], program)
    if sys.version_info >= (3, 12):
        # Served where the walk reads the literal; a value it cannot read
        # (row 89 above) may still refuse, and a refusal is never wrong.
        if program != NEWLINE_BY_TRUTH[0] and _refusal_problem(got) is None:
            return
        assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (program, got.stderr)
    else:
        assert ref.returncode == 1, (program, ref.stdout)
        assert _refusal_problem(got) is None, (program, got.returncode, got.stderr)


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


@needs_l
@pytest.mark.parametrize("program", ANSWERED_UNREACHED, ids=range(len(ANSWERED_UNREACHED)))
def test_an_except_clause_no_exception_reaches_is_answered(program: str) -> None:
    with tempfile.TemporaryDirectory() as d:
        got = subprocess.run([str(BINARY), "-c", program], capture_output=True,
                             text=True, cwd=d, timeout=60)
    with tempfile.TemporaryDirectory() as d:
        ref = subprocess.run([sys.executable, "-c", program], capture_output=True,
                             text=True, cwd=d, timeout=60)
    assert (got.returncode, got.stdout) == (ref.returncode, ref.stdout), (program, got.stderr)
    assert got.returncode == 0


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


# A handler that is not an exception CLASS is a TypeError in CPython the moment
# an exception reaches it. The engines match handlers by NAME, so it used to
# be skipped as a non-match; and once a capability import routed the program
# into lypning-l, the core's `exception` blocker was dropped with it (round 2,
# 2026-09-24: `import csv` then `except int:` answered through the chain).
NOT_A_CLASS = [
    B + "try:\n    1/0\nexcept len:\n    print('p')\nexcept ZeroDivisionError:\n    print('z')",
    B + "try:\n    1/0\nexcept (int, ZeroDivisionError):\n    print('p')",
    B + "h = binascii.hexlify\ntry:\n    1/0\nexcept h:\n    print('p')",
    B + "E = (binascii.hexlify,)\ntry:\n    1/0\nexcept E:\n    print('p')",
    "from binascii import hexlify\ntry:\n    1/0\nexcept hexlify:\n    print('p')",
    B + "def h():\n    pass\ntry:\n    1/0\nexcept h:\n    print('p')",
    "import math, binascii\ntry:\n    1/0\nexcept math.sqrt:\n    print('p')",
    "import csv\ntry:\n    1/0\nexcept int:\n    print('p')",
    "import base64\ntry:\n    1/0\nexcept print:\n    print('p')",
]


@needs_core
@pytest.mark.parametrize("program", NOT_A_CLASS, ids=range(len(NOT_A_CLASS)))
def test_a_handler_that_is_not_a_class_routes_to_cpython_and_refuses(program: str) -> None:
    route = engines.route(program, binary=CORE)
    assert route.engine == engines.CPYTHON, (route.engine, route.kind, route.detail)
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got)
    assert problem is None, "%s\n  program: %r" % (problem, program)
    assert ": unsupported: exception: except " in got.stderr, got.stderr


@needs_core
def test_a_handler_the_exception_never_reaches_is_not_refused() -> None:
    """CPython validates a clause only when it is tried: a matching clause
    ABOVE the bad one answers."""
    body = "try:\n    1/0\nexcept ZeroDivisionError:\n    print('z')\nexcept int:\n    print('p')"
    for binary, program in ((CORE, body), (BINARY, B + body)):
        got = _run([str(binary)], program)
        assert (got.stdout, got.returncode) == ("z\n", 0), (binary, got)


# Round 3 (2026-09-25): shared-code bugs a served `import binascii` made
# reachable from lypning-l, closed at the root so both variants hold them.
# (program, stdout, exit, last stderr line), CPython 3.14.5's exact bytes.
EXACT_ROUND3 = [
    # `except E as n` ends in `del n`: the name is unbound after the handler.
    ("try:\n    1/0\nexcept ZeroDivisionError as e:\n    pass\nprint(e)", "", 1,
     "NameError: name 'e' is not defined"),
    ("def f():\n    try:\n        1/0\n    except Exception as e:\n        pass\n    print(e)\nf()",
     "", 1,
     "UnboundLocalError: cannot access local variable 'e' where it is not associated with a value"),
    ("try:\n    1/0\nexcept ZeroDivisionError as e:\n    print(e)\ne = 5\nprint(e)",
     "division by zero\n5\n", 0, ""),
    # bytes.upper/lower take nothing; bytes.hex takes at most two.
    ("print(b'a'.upper(1))", "", 1, "TypeError: bytes.upper() takes no arguments (1 given)"),
    ("print(b'a'.lower(1, 2))", "", 1, "TypeError: bytes.lower() takes no arguments (2 given)"),
    ("print(b'a'.hex(1, 2, 3))", "", 1, "TypeError: hex() takes at most 2 arguments (3 given)"),
    ("print([].append())", "", 1,
     "TypeError: list.append() takes exactly one argument (0 given)"),
    ("print([1].sort(1))", "", 1, "TypeError: sort() takes no positional arguments"),
    # int() and float() of a str read every Unicode decimal digit.
    ("print(int('\u0663'), int('\u0661', 16), int('\u0661_2'), float('\u0663.5'))",
     "3 1 12 3.5\n", 0, ""),
    ("print(int('\u3000\u0663\u00a0'), int('-\u0663'), int('\uff11\uff12'), float(' \u0967e2 '))",
     "3 -3 12 100.0\n", 0, ""),
    ("print(int('\u0663x'))", "", 1,
     "ValueError: invalid literal for int() with base 10: '\u0663x'"),
    # The ascii codec's message names the character or the run, and an
    # `except ValueError` catches it.
    ("print('\u00e9'.encode('ascii'))", "", 1,
     "UnicodeEncodeError: 'ascii' codec can't encode character '\\xe9' in position 0: "
     "ordinal not in range(128)"),
    ("try:\n    'a\u00e9\u00fc\u20ac'.encode('ascii')\nexcept ValueError as e:\n    print(e)",
     "'ascii' codec can't encode characters in position 1-3: ordinal not in range(128)\n", 0, ""),
    ("try:\n    'a\U0001f600'.encode('ascii')\nexcept ValueError as e:\n    print(e)",
     "'ascii' codec can't encode character '\\U0001f600' in position 1: "
     "ordinal not in range(128)\n", 0, ""),
    ("try:\n    'a\u20acb'.encode('ascii')\nexcept ValueError as e:\n    print(e)",
     "'ascii' codec can't encode character '\\u20ac' in position 1: "
     "ordinal not in range(128)\n", 0, ""),
    # str/list/tuple concatenation and sequence repetition in CPython's words.
    ("print('a' + b'ab')", "", 1, 'TypeError: can only concatenate str (not "bytes") to str'),
    ("print([1] + b'ab')", "", 1, 'TypeError: can only concatenate list (not "bytes") to list'),
    ("print((1,) + b'ab')", "", 1,
     'TypeError: can only concatenate tuple (not "bytes") to tuple'),
    ("print(b'a' * '3')", "", 1, "TypeError: can't multiply sequence by non-int of type 'str'"),
    ("print(b'a' * 1.5)", "", 1,
     "TypeError: can't multiply sequence by non-int of type 'float'"),
    ("print(1.5 * [1])", "", 1,
     "TypeError: can't multiply sequence by non-int of type 'float'"),
    ("print([1] * 'a')", "", 1, "TypeError: can't multiply sequence by non-int of type 'str'"),
    ("print('ab' * True, [1] * 2, 3 * (1,), b'x' * 0)", "ab [1, 1] (1, 1, 1) b''\n", 0, ""),
    # A non-ASCII character in a bytes literal is a compile error.
    ("print(1)\nprint(b'\u0663' if 0 else 1)", "", 1,
     "SyntaxError: bytes can only contain ASCII literal characters"),
    ("print(1)\nprint(br'\u00e9')", "", 1,
     "SyntaxError: bytes can only contain ASCII literal characters"),
]
EXACT_L_ROUND3 = [
    (B + "try:\n    1/0\nexcept ZeroDivisionError as e:\n    pass\nprint(e)", "", 1,
     "NameError: name 'e' is not defined"),
    (B + "x = binascii.hexlify(b'\\x01')\nprint(x.upper(1))", "", 1,
     "TypeError: bytes.upper() takes no arguments (1 given)"),
    (B + "print(int(binascii.unhexlify('d9a3').decode()))", "3\n", 0, ""),
    (B + "print('\u00e9'.encode('ascii'))", "", 1,
     "UnicodeEncodeError: 'ascii' codec can't encode character '\\xe9' in position 0: "
     "ordinal not in range(128)"),
    (B + "print('a' + binascii.hexlify(b'a'))", "", 1,
     'TypeError: can only concatenate str (not "bytes") to str'),
    (B + "print(b'\u0663' if 0 else 1)", "", 1,
     "SyntaxError: bytes can only contain ASCII literal characters"),
]
# Refused on both variants: a shadowed exception name in a handler (CPython's
# TypeError), a codec error's five-argument repr/args, and a `**` that is not
# a dict (CPython's message names the callee's qualname).
REFUSED_ROUND3 = [
    ("ValueError = len\ntry:\n    int('z')\nexcept ValueError:\n    print('c')", "exception"),
    ("def g():\n    ZeroDivisionError = len\n    try:\n        1/0\n"
     "    except ZeroDivisionError:\n        print('c')\ng()", "exception"),
    ("print('a')\nValueError = KeyError\ntry:\n    {}['k']\nexcept ValueError:\n    print('c')",
     "exception"),
    ("try:\n    b'\\xc3'.decode()\nexcept ValueError as e:\n    print(repr(e))", "exception"),
    ("try:\n    b'\\xc3'.decode()\nexcept ValueError as e:\n    print(e.args)", "exception"),
    ("try:\n    b'\\xc3'.decode()\nexcept ValueError as e:\n    print(e.start)", "exception"),
    ("def f(**k):\n    print(k)\nf(x=1, **None)", "call"),
    ("print(**[1])", "call"),
]


@needs_core
@pytest.mark.parametrize("row", EXACT_ROUND3, ids=range(len(EXACT_ROUND3)))
def test_round3_shared_code_answers_cpythons_bytes(row) -> None:
    program, out, code, last = row
    for binary in (CORE, BINARY):
        problem = _exact_problem(_run([str(binary)], program), out, code, last)
        assert problem is None, "%s: %s\n  program: %r" % (binary, problem, program)


@needs_l
@pytest.mark.parametrize("row", EXACT_L_ROUND3, ids=range(len(EXACT_L_ROUND3)))
def test_round3_binascii_programs_answer_cpythons_bytes(row) -> None:
    program, out, code, last = row
    got = _run([str(BINARY)], program)
    # An uncaught NameError in a program only `cap-binascii` admits refuses
    # (`route::hint_held`): CPython may end it with a suggestion.
    if last.startswith("NameError") and got.returncode == engines.UNSUPPORTED_EXIT:
        line = got.stderr.strip()
        assert got.stdout == "" and "\n" not in line, got.stderr
        assert line.startswith("%s: unsupported: name-hint: " % engines.LYPNING_L), line
        return
    problem = _exact_problem(got, out, code, last)
    assert problem is None, "%s\n  program: %r" % (problem, program)


@needs_core
@pytest.mark.parametrize("row", REFUSED_ROUND3, ids=range(len(REFUSED_ROUND3)))
def test_round3_refusals_hold_on_both_variants(row) -> None:
    body, kind = row
    for binary, program in ((CORE, body), (BINARY, B + body)):
        got = _run([str(binary)], program)
        assert got.returncode == engines.UNSUPPORTED_EXIT and got.stdout == "", (
            binary, program, got.returncode, got.stdout, got.stderr)
        assert got.stderr.count("\n") == 1 and ": unsupported: %s: " % kind in got.stderr, (
            got.stderr)


@needs_core
def test_a_rebound_exception_name_routes_to_cpython() -> None:
    """`ValueError = len` stops every rung in the walk: the handler would be
    CPython's TypeError, and matching it by name answered 'c' at exit 0."""
    for program in (B + "ValueError = len\ntry:\n    int('z')\nexcept ValueError:\n    print('c')",
                    "import csv\nKeyError = 1\nprint(csv.QUOTE_ALL)"):
        route = engines.route(program, binary=CORE)
        assert route.engine == engines.CPYTHON, (route.engine, route.kind, route.detail)


@needs_core
def test_a_from_imported_module_exception_still_matches() -> None:
    program = ("import json\nfrom json import JSONDecodeError\ntry:\n    json.loads('x')\n"
               "except JSONDecodeError:\n    print('ok')")
    got = _run([str(CORE)], program)
    assert (got.stdout, got.returncode) == ("ok\n", 0), got
