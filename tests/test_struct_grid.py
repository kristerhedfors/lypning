"""`struct`, as a grid: every program on the binary and on CPython.

`tests/test_binascii_grid.py` is the shape this follows. Every row runs from a
FRESH temp cwd (invariant 4) and must end one of exactly two ways:

  * byte-identical stdout AND the same exit code as CPython, or
  * a clean refusal — exit 90, nothing on stdout, one
    ``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

`cap-binascii` serves `struct.pack`, `unpack`, `unpack_from` and `calcsize`
over the codes ``x c b B ? h H i I l L q Q s d`` (`pystruct.rs`). Native layout
(``@`` or no order character) is served on a 64-bit little-endian Unix host
only, with CPython's alignment: before each item, even at a count of 0, and
never after the last.

**Every error is a refusal, not a raise.** `struct.error` does not exist in
this engine and its texts changed in 3.12 and 3.14, so a value out of range, a
wrong item count, a buffer of the wrong size, a bad or unserved format
character, whitespace or a non-ASCII character in a format, a `bytes` format,
a wrong arity and any keyword but `unpack_from`'s ``offset=`` all refuse — at
RUN time, never in a walk (`struct` has no `route::MODULE_ATTRS` row, which
would cost the frozen core bytes), and inside a run `import struct` has held
reversible, so a refusal cannot be caught by `except Exception` and leaves
stdout empty. A NaN refuses to pack; unpacking one is exact.

**Routing.** The core refuses `import struct` and routes it to lypning-l
(`route::CAPS`'s `cap-binascii` row). A program naming an unserved attribute
(`struct.error`, `Struct`) is routed there too and refuses at run time — a
spawn, never an answer (`ROUTED_LATE`).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

S = "import struct\n"

#: The round trip over every standard code and every standard byte order: the
#: edges of each integer width, the signed zero, the infinities, the smallest
#: subnormal and the largest subnormal and normal doubles, and every `?` / `c`.
ROUND_TRIP = S + (
    "vals = {'b': [-128, -1, 0, 127], 'B': [0, 1, 255], 'h': [-32768, -1, 0, 32767],\n"
    "        'H': [0, 65535], 'i': [-2**31, -1, 0, 2**31-1], 'I': [0, 2**32-1],\n"
    "        'l': [-2**31, 2**31-1], 'L': [0, 2**32-1], 'q': [-2**63, -1, 0, 2**63-1],\n"
    "        'Q': [0, 2**63-1, 2**63, 2**64-1], '?': [True, False], 'c': [b'a', b'\\x00', b'\\xff'],\n"
    "        'd': [0.0, -0.0, 1.5, -2.25, 1e308, -1.7976931348623157e308, 5e-324, -5e-324,\n"
    "              2.2250738585072014e-308, 2.225073858507201e-308, float('inf'), float('-inf')]}\n"
    "for o in '<>!=':\n"
    "    for c, vs in vals.items():\n"
    "        for v in vs:\n"
    "            p = struct.pack(o + c, v)\n"
    "            print(o, c, v, p.hex(), struct.unpack(o + c, p), struct.calcsize(o + c))\n"
)
NATIVE_ROUND_TRIP = S + (
    "vals = {'b': [-128, 127], 'B': [255], 'h': [-32768, 32767], 'H': [65535],\n"
    "        'i': [-2**31, 2**31-1], 'I': [2**32-1], 'q': [-2**63, 2**63-1],\n"
    "        'Q': [2**64-1], '?': [True, False], 'c': [b'z'], 'd': [-0.0, 5e-324, float('inf')]}\n"
    "for o in ['', '@']:\n"
    "    for c, vs in vals.items():\n"
    "        for v in vs:\n"
    "            f = o + 'c' + c + 'b' + c\n"
    "            p = struct.pack(f, b'x', v, 1, v)\n"
    "            print(repr(f), p.hex(), struct.unpack(f, p), struct.calcsize(f))\n"
)

#: CPython answers every one of these, and lypning-l must answer it too.
SERVED = [S + x for x in [
    'print(struct.pack(">I", 258), struct.unpack(">HH", b"\\x00\\x01\\x00\\x02"))',
    "print(struct.unpack('<d', struct.pack('<Q', 0x3ff0000000000000))[0], "
    "struct.unpack('<Q', struct.pack('<d', -0.0))[0])",
    "print(struct.unpack('<d', struct.pack('<Q', 1))[0], "
    "struct.unpack('<d', struct.pack('<Q', 0xfff0000000000000))[0])",
    "print(struct.unpack('<d', struct.pack('<Q', 0xfff8000000000001)))",
    "print(struct.calcsize('<8I'), struct.calcsize('@Id'), struct.calcsize('Id'), "
    "struct.calcsize('<Id'), struct.pack('@Id',1,2.5).hex())",
    "print(struct.unpack_from('<I', b'\\x00\\x01\\x02\\x03\\x04\\x05', 2), "
    "struct.unpack_from('<I', b'\\x00\\x01\\x02\\x03\\x04\\x05', offset=1))",
    "print(struct.pack('<3s', b'ab'), struct.pack('>bB', -1, 255), struct.unpack('>bB', b'\\xff\\xff'), "
    "struct.pack('<?', 0), struct.unpack('<?', b'\\x02'), struct.pack('<2x'), struct.pack('<xH', 1))",
    "print(struct.unpack('<q', b'\\xff'*8), struct.pack('<Q', 2**64-1), struct.pack('<I', True), "
    "struct.unpack('<0I', b''))",
    # Counts, padding and the pad byte.
    "print(struct.pack('<3h2I', 1, -2, 3, 4, 5), struct.unpack('>4H', bytes(range(8))))",
    "print(struct.unpack('<xBx2xH', b'\\x00\\x07\\x00\\x00\\x00\\x01\\x02'))",
    "print(struct.pack('!IH', 1, 2), struct.unpack('!q', b'\\x80' + b'\\x00' * 7))",
    "print(struct.calcsize(''), struct.pack(''), struct.unpack('', b''), struct.pack('<'), struct.calcsize('!'))",
    "print(len(struct.pack('<1000x')), struct.calcsize('<2147483647x'), len(struct.unpack('<300B', bytes(300))))",
    # `s`: truncated or NUL-padded to its count, one argument whatever the count.
    "print(struct.pack('<5s', b'abcdefg'), struct.pack('<0s', b'abc'), struct.unpack('<0s', b''), "
    "struct.pack('>s', b''), struct.unpack('<3s', b'a\\x00c'), struct.pack('<2s3s', b'xy', b'z'))",
    # `?`: the truth of the argument, and any nonzero byte is True (standard).
    "print(struct.pack('<5?', 2, None, 0.0, 'a', b''), struct.unpack('<3?', b'\\x00\\x01\\xff'), "
    "struct.unpack('>?', b'\\x80'))",
    # A bool is an int; an int is a double when it fits a machine word.
    "print(struct.pack('<bHq', True, False, True), struct.pack('<d', True).hex())",
    "print(struct.pack('<d', 3).hex(), struct.pack('>d', -2**53-1).hex(), struct.pack('<d', 2**63-1).hex())",
    # `c`: one byte in, one byte out.
    "print(struct.pack('<3c', b'a', b'b', b'\\x00'), struct.unpack('<2c', b'\\xffz'))",
    # Native: aligned before each item even at count 0, never padded at the end.
    "print(struct.calcsize('@dI'), struct.calcsize('@hq?'), struct.calcsize('@c0q'), "
    "struct.calcsize('@0d'), struct.calcsize('@bh'), struct.calcsize('@3sI'), "
    "struct.calcsize('bhiq'), struct.calcsize('?d'), struct.calcsize('@x0H'))",
    "print(struct.pack('@hq?', 1, 2, True).hex(), struct.unpack('@hq?', struct.pack('@hq?', -1, -2, False)), "
    "struct.pack('@c0q', b'x'), struct.pack('@bih', 1, 2, 3).hex())",
    "print(struct.unpack('?', b'\\x01'), struct.unpack('@2?', b'\\x00\\x01'), struct.pack('=?', 7))",
    "print(struct.unpack('=d', struct.pack('=d', 0.1)), struct.pack('=lL', -1, 1).hex())",
    # `unpack_from`: the default offset, positional and keyword, and the tail.
    "print(struct.unpack_from('<H', b'\\x01\\x02\\x03'), struct.unpack_from('<H', b'\\x01\\x02\\x03', 1), "
    "struct.unpack_from('<0I', b'', 0), struct.unpack_from('<B', b'ab', offset=0))",
    # The edges of a double: subnormals, the largest finite, the infinities,
    # a signed zero, and the NaN bit patterns an unpack keeps.
    "for b in [0, 1, 0x000fffffffffffff, 0x0010000000000000, 0x7fefffffffffffff, 0x7ff0000000000000, "
    "0x8000000000000000, 0x8000000000000001, 0xfff0000000000000]:\n"
    "    print(struct.unpack('>d', struct.pack('>Q', b))[0])",
    "print(struct.unpack('<d', b'\\x00' * 6 + b'\\xf8\\x7f'), struct.unpack('>d', b'\\xff' * 8))",
    "print(struct.unpack('<Q', struct.pack('<d', 5e-324)), struct.unpack('>Q', struct.pack('>d', -0.0)))",
    # What an unpacked value is, downstream.
    "x = struct.unpack('<d', bytes(8))[0]\nprint(x, type(x).__name__, struct.unpack('<Q', b'\\xff' * 8)[0] + 1)",
    "t = struct.unpack('<2H', b'\\x01\\x00\\x02\\x00')\na, b = t\nprint(a + b, len(t), t == (1, 2), type(t).__name__)",
    "vals = [1, 2, 3]\nprint(struct.pack('<3B', *vals), struct.pack(*['<H', 9]))",
    "f = '<' + 'I' * 2\nprint(struct.pack(f, 1, 2), struct.calcsize(f))",
    # The spellings.
    "from struct import pack, unpack, unpack_from, calcsize\n"
    "print(unpack('<I', pack('<I', 7)), unpack_from('<B', b'ab', 1), calcsize('<Q'))",
    "import struct as S\nprint(S.pack('<H', 513))",
    "from struct import pack as p\nprint(p('>i', -2))",
    "p = struct.pack\nprint(p('<h', -1), struct.pack is struct.pack)",
    "def f(b):\n    return struct.unpack('<I', b)[0]\nprint(f(b'\\x01\\x00\\x00\\x00'), f(bytes(4)))",
    # The corpus idiom: a 64-bit draw reinterpreted as a double and back.
    "import random\nrandom.seed(7)\nfor _ in range(6):\n    b = random.getrandbits(64)\n"
    "    x = struct.unpack('<d', struct.pack('<Q', b))[0]\n"
    "    print(repr(x), struct.unpack('<Q', struct.pack('<d', x))[0] == b if x == x else 'nan')",
    "print(struct.unpack('<Q', struct.pack('<d', 1.0))[0], struct.unpack('<q', struct.pack('<d', -1.0))[0])",
    # A binary header read back from a file.
    "open('h.bin', 'wb').write(struct.pack('<8I', *range(8)))\n"
    "print(struct.unpack('<8I', open('h.bin', 'rb').read()))",
    "open('h.bin', 'wb').write(b'MAGC' + struct.pack('<QH', 2**40, 7))\n"
    "d = open('h.bin', 'rb').read()\nprint(d[:4], struct.unpack_from('<QH', d, 4), struct.unpack_from('<H', d, offset=12))",
    # A refusal is not an exception: nothing below needed one.
    "try:\n    print(struct.pack('<B', 255))\nexcept Exception as e:\n    print('E', e)",
]] + [ROUND_TRIP, NATIVE_ROUND_TRIP]

#: Everything outside the slice, and every error CPython raises. Exit 90, an
#: EMPTY stdout (the run is held from `import struct`), one refusal line.
REFUSED = [S + x for x in [
    # struct.error: range, item count, buffer size — worded by version.
    "print(struct.pack('<Q', 2**64))",
    "print(struct.pack('<B', 256))",
    "print(struct.pack('<b', -129))",
    "print(struct.pack('<h', 32768))",
    "print(struct.pack('<H', -1))",
    "print(struct.pack('<i', 2**31))",
    "print(struct.pack('<I', -1))",
    "print(struct.pack('<l', 2**31))",
    "print(struct.pack('<L', 2**32))",
    "print(struct.pack('<q', 2**63))",
    "print(struct.pack('<q', -2**63 - 1))",
    "print(struct.pack('<Q', -1))",
    "print(struct.pack('<Q', 2**100))",
    "print(struct.pack('<I', 1.0))",
    "print(struct.pack('<B', 'a'))",
    "print(struct.pack('<B', None))",
    "print(struct.pack('<2I', 1))",
    "print(struct.pack('<I', 1, 2))",
    "print(struct.pack('<2s', b'a', b'b'))",
    "print(struct.unpack('<I', b'abc'))",
    "print(struct.unpack('<I', b'abcde'))",
    "print(struct.unpack_from('<I', b'ab', 1))",
    "print(struct.unpack_from('<B', b'ab', 2))",
    "print(struct.unpack_from('<B', b'ab', 99))",
    "print(struct.pack('<c', b'ab'))",
    "print(struct.pack('<c', 'a'))",
    "print(struct.pack('<c', 97))",
    # A NaN's bits are the hardware's; `d` refuses every one, and `f`/`e` too.
    "print(struct.pack('>d', float('nan')))",
    "x = float('inf')\nprint(struct.pack('<d', x - x))",
    "print(struct.pack('<d', -float('nan')))",
    "print(struct.pack('<2d', 1.0, float('nan')))",
    "x = struct.unpack('<d', struct.pack('<Q', 0x7ff0000000000001))[0]\nprint(struct.pack('<d', x))",
    "print(struct.pack('<d', 'x'))",
    "print(struct.pack('<d', None))",
    "print(struct.pack('<d', 2**64))",
    "print(struct.pack('<d', 2**1024))",
    "print(struct.pack('<f', 1.0))",
    "print(struct.pack('<e', 1.0))",
    "print(struct.unpack('<f', b'\\x00' * 4))",
    # Formats: whitespace, non-ASCII, bytes, unserved codes, a bare count.
    "print(struct.calcsize('< I'))",
    "print(struct.calcsize('<\\tI'))",
    "print(struct.calcsize('I '))",
    "print(struct.calcsize('<\\u00b2i'))",
    "print(struct.calcsize('<\\u0663i'))",
    "print(struct.pack(b'<I', 1))",
    "print(struct.calcsize(b'<I'))",
    "print(struct.calcsize(3))",
    "print(struct.calcsize(None))",
    "print(struct.pack('@l', 1))",
    "print(struct.calcsize('l'))",
    "print(struct.calcsize('@L'))",
    "print(struct.calcsize('<n'))",
    "print(struct.calcsize('N'))",
    "print(struct.calcsize('P'))",
    "print(struct.calcsize('<p'))",
    "print(struct.calcsize('<3'))",
    "print(struct.calcsize('<z'))",
    "print(struct.calcsize('<<I'))",
    "print(struct.calcsize('<4294967296x'))",
    "print(struct.calcsize('<2147483647x2147483647x'))",
    # A native `?` of a byte other than 0 or 1 is a C _Bool, read by version.
    "print(struct.unpack('?', b'\\x02'))",
    "print(struct.unpack('@?', b'\\xff'))",
    # Arity and keywords: CPython's TypeErrors, which it words.
    "print(struct.unpack('<I'))",
    "print(struct.calcsize())",
    "print(struct.calcsize('<I', 1))",
    "print(struct.unpack('<I', b'abcd', 0))",
    "print(struct.pack())",
    "print(struct.unpack_from('<I'))",
    "print(struct.unpack_from('<I', b'abcd', 0, 0))",
    "print(struct.pack('<I', v=1))",
    "print(struct.pack(fmt='<I'))",
    "print(struct.calcsize(format='<I'))",
    "print(struct.unpack('<I', buffer=b'abcd'))",
    "print(struct.unpack_from('<I', buffer=b'abcd'))",
    "print(struct.unpack_from(format='<I', buffer=b'abcd'))",
    "print(struct.unpack_from('<I', b'abcd', 0, offset=0))",
    "print(struct.unpack_from('<I', b'abcd', offset=0, foo=1))",
    # Buffers and items this engine has no type for, or CPython rejects.
    "print(struct.unpack('<2s', 'ab'))",
    "print(struct.pack('<2s', 'ab'))",
    "print(struct.unpack('<I', bytearray(4)))",
    "print(struct.unpack('<I', memoryview(b'abcd')))",
    "print(struct.unpack_from('<I', b'abcdef', -4))",
    "print(struct.unpack_from('<B', b'ab', True))",
    "print(struct.unpack_from('<B', b'ab', 1.0))",
    "print(struct.pack('<?', []))",
    # The names that are not served.
    "print(struct.error)",
    "print(struct.Struct('<I'))",
    "print(struct.pack_into)",
    "print(struct.iter_unpack('<B', b'ab'))",
    "print(struct.__name__)",
    "from struct import error\nprint(1)",
    "from struct import Struct\nprint(1)",
    "try:\n    struct.pack('<B', 256)\nexcept struct.error as e:\n    print('E', e)",
    # A refusal is not catchable, and what printed before it is held.
    "print(struct.calcsize('<I'))\nprint(struct.error)",
    "print(1)\ntry:\n    struct.pack('<B', 256)\nexcept Exception as e:\n    print(type(e).__name__)",
    "for v in [1, 2, 300]:\n    print(struct.pack('<B', v))",
]] + [
    # py-8fdcf30a2470: NaN packs, from the corpus.
    "import struct, math\ni=float('inf')\n"
    "for v in [float('nan'), -float('nan'), i-i, i*0, math.nan, -math.nan, abs(-float('nan')), float('-nan')]:\n"
    "    print(struct.pack('>d', v).hex())\n"
    "print(struct.unpack('<d', struct.pack('<Q', 0xfff8000000000001)))\n",
    # py-da5554b153e4: whitespace and non-ASCII digits, inside `except Exception`.
    "import struct\nfor f in ['<\u00b2i', '<\u0663i', '<\u00a0i', '<\u3000i', '<\u2007i']:\n"
    "    try:\n        print(repr(f), struct.calcsize(f))\n"
    "    except Exception as e:\n        print(repr(f), type(e).__name__, e)\n",
]

#: Routed by the CORE: into lypning-l on the import alone.
ROUTED_TO_LYPNING_L = [
    S + "print(struct.pack('<I', 1))",
    "from struct import unpack\nprint(unpack('<H', b'ab'))",
    "import struct as s\nprint(s.calcsize('<Q'))",
    "import random, struct\nrandom.seed(1)\nprint(struct.pack('<Q', random.getrandbits(64)))",
    "import binascii, struct\nprint(binascii.hexlify(struct.pack('>H', 258)))",
]
#: Also routed into lypning-l — the core routes on its FIRST blocker, and
#: `struct` has no `route::MODULE_ATTRS` row — where they refuse at run time.
#: A spawn, never an answer; lypning-l's own walk sends them to CPython.
ROUTED_LATE = [
    S + "print(struct.error)",
    "from struct import Struct\nprint(1)",
    S + "print(struct.iter_unpack('<B', b'a'))",
]

#: The core answers these before `import struct` is reached, and lypning-l
#: must answer them identically (invariant 10): no pre-run stop anywhere.
BEFORE_THE_IMPORT = [
    "open('/nonexistent/lypning-struct')\nimport struct",
    "print(1)\nundefined_name\nimport struct\nprint(struct.error)",
    "import sys\nprint('a')\nsys.exit(3)\nimport struct",
    "if 0:\n    import struct\n    struct.error\nprint(2)",
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


def _current(engine: str) -> Path | None:
    """A built ``engine`` that carries THIS tree's capability table — one that
    routes `struct` — so an older binary cannot turn every row into a skip."""
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
        if any(row.get("cap") == "cap-binascii" and "struct" in row.get("modules", ())
               for row in table.get("caps", [])):
            return cand
    return None


BINARY = _current(engines.LYPNING_L)
CORE = _current(engines.LYPNING)

needs_l = pytest.mark.skipif(
    BINARY is None,
    reason="no lypning-l serving struct is built (lypning build --rust)",
)
needs_core = pytest.mark.skipif(
    CORE is None or BINARY is None,
    reason="routing needs the CORE that routes and the lypning-l it routes to",
)


def _run(argv: list[str], program: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True, text=True,
                              cwd=d, timeout=60)


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


@needs_l
@pytest.mark.parametrize("program", SERVED, ids=range(len(SERVED)))
def test_the_served_rows_answer_exactly_what_cpython_answers(program: str) -> None:
    """Served means ANSWERED: a refusal here is a row that stopped measuring."""
    got = _run([str(BINARY)], program)
    ref = _run([sys.executable], program)
    assert got.returncode != engines.UNSUPPORTED_EXIT, (
        "a served row refused\n  program: %r\n  stderr: %r" % (program, got.stderr.strip()))
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "lypning-l disagrees with CPython.\n  program:  %r\n"
        "  lypning-l: %r exit %d %s\n  cpython:   %r exit %d %s"
        % (program, got.stdout[:600], got.returncode, got.stderr.strip()[-200:],
           ref.stdout[:600], ref.returncode, ref.stderr.strip()[-200:]))


@needs_l
@pytest.mark.parametrize("program", REFUSED, ids=range(len(REFUSED)))
def test_the_surface_outside_the_slice_refuses_rather_than_guesses(program: str) -> None:
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got, engines.LYPNING_L)
    assert problem is None, "this program must refuse, not answer: %s\n  program: %r" % (
        problem, program)


@needs_core
@pytest.mark.parametrize("program", REFUSED[:8], ids=range(8))
def test_the_core_refuses_the_import_itself(program: str) -> None:
    got = _run([str(CORE)], program)
    assert _refusal_problem(got, engines.LYPNING) is None, (program, got.stderr)
    assert ": unsupported: module: import struct" in got.stderr, got.stderr


@needs_l
def test_a_refusal_after_a_barrier_leaves_the_cwd_untouched() -> None:
    program = S + "import os\nos.mkdir('NEWD')\nprint(struct.pack('<B', 256))"
    with tempfile.TemporaryDirectory() as d:
        got = subprocess.run([str(BINARY), "-c", program], capture_output=True,
                             text=True, cwd=d, timeout=60)
        after = sorted(os.listdir(d))
    assert _refusal_problem(got, engines.LYPNING_L) is None, got.stderr
    assert after == [], "the refusal landed after the barrier: %r" % after


@needs_core
@pytest.mark.parametrize("program", ROUTED_TO_LYPNING_L, ids=range(len(ROUTED_TO_LYPNING_L)))
def test_the_core_routes_struct_into_lypning_l(program: str) -> None:
    for binary in (CORE, BINARY):
        route = engines.route(program, binary=binary)
        assert route.engine == engines.LYPNING_L, (binary, route.engine, route.kind, route.detail)


@needs_core
@pytest.mark.parametrize("program", ROUTED_LATE, ids=range(len(ROUTED_LATE)))
def test_an_unserved_name_is_a_late_refusal_never_an_answer(program: str) -> None:
    assert engines.route(program, binary=CORE).engine == engines.LYPNING_L
    assert engines.route(program, binary=BINARY).engine == engines.CPYTHON
    got = _run([str(BINARY)], program)
    assert _refusal_problem(got, engines.LYPNING_L) is None, got.stderr
    assert ": unsupported: module-attr: struct." in got.stderr, got.stderr


@needs_core
@pytest.mark.parametrize("program", BEFORE_THE_IMPORT, ids=range(len(BEFORE_THE_IMPORT)))
def test_what_the_core_answers_before_the_import_lypning_l_answers_the_same(program: str) -> None:
    core = _run([str(CORE)], program)
    assert core.returncode != engines.UNSUPPORTED_EXIT, core.stderr
    larger = _run([str(BINARY)], program)
    assert (larger.returncode, larger.stdout, larger.stderr) == \
        (core.returncode, core.stdout, core.stderr), (program, core, larger)
