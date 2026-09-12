"""`cap-bigint`, as a grid: every program on the binary and on CPython.

`tests/test_glob_grid.py` is the shape this follows. Every row must end one of
exactly two ways:

  * byte-identical stdout AND the same exit code as CPython 3.x, or
  * a clean refusal — exit 90, nothing on stdout, one
    ``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

**What is different about this capability, and what the grid is therefore for.**
`cap-bigint` serves no module. It widens the payload of ``Value::Int``, so the
surface is every path in the interpreter that ever held an integer — print,
`==`, `hash`, `sorted`, `in`, `bool`, `len`, indexing, slice bounds, augmented
assignment, `json.dumps` and `%`-format among them. That list is exactly the one
`docs/HILLCLIMB.md` iterations 74 and 76 record five capabilities reaching a new
`Value` variant through and getting wrong. Widening the payload makes the
compiler enumerate those arms instead of a reviewer; this file is the second
pass, on behaviour rather than on types.

**Arithmetic on integers is EXACT, so there is no ULP trap.** The traps are
elsewhere, and each has a block below:

1. **`str`/`repr` of a huge integer raises in CPython.**
   `str()` of an integer past `sys.get_int_max_str_digits()` is a `ValueError`
   — with one message for `str()` and a different one, naming the digit count,
   for `int()`. Both refuse here rather than be reproduced; `hex`/`oct`/`bin`
   have no such cap and answer at any width.

   **Two halves of that rule were wrong, and each had its own wrong answer at
   exit 0.** The count is of the INPUT STRING's digits on the way in, not of the
   result's magnitude, so `int('0' * 5000 + '123')` printed `123` where CPython
   raises; and the exemption is every POWER-OF-TWO base, not decimal alone, so
   `int('1' * 4301, 3)` and `int('z' * 4301, 36)` answered too. Both halves come
   from one function now — `host::over_str_digit_limit(digits, base)` — which
   the frozen core calls as well, since `int(s)` is not a `cap-bigint` path and
   the core had the first bug too.

   **And that limit is not a constant**, which is how it was first written
   here. CPython reads `PYTHONINTMAXSTRDIGITS` at interpreter start — 0 for
   unlimited, otherwise at least `sys.int_info.str_digits_check_threshold`
   (640), and any other value is a *fatal* startup error with no program run at
   all. `ENV_LIMIT` below is the block for it, and it is the only block here
   that has to set an environment variable on both sides to mean anything.
2. **`int / int` past 2**53 needs the quotient rounded from the INTEGERS.**
   That is the `int-div-precision` refusal, answered: `bigint::div_exact` scales
   the dividend so the quotient carries 55 bits, divides in `u128`, and rounds
   once, half-to-even, with the division's own remainder as the sticky bit.
   `9007199254740993 / 3` is 3002399751580331.0, not the …330.5 that converting
   each operand to `f64` first produces.

   **It takes two `i64`s and nothing wider**, which is the whole of what `u128`
   buys: `div_exact(x: i64, y: i64)`. A division with a `Int::B` on either side
   is a different problem — the quotient's exponent is not bounded by an `i64`
   pair — and it stays the `bigint` refusal in `REFUSED` below. `DIVISION` here
   is therefore an i64-only table on purpose, and `print(2**100 / 2)` is in the
   other one.
3. **`//` and `%` floor toward negative infinity and take the DIVISOR's sign.**
   Rust's `/` and `%` do neither. `>>` on a negative integer floors for the same
   reason, so `-(2**100+1) >> 3` is one lower than a magnitude shift.
4. **`**` with a huge exponent must refuse, never hang.** The result's bit
   length is estimated before anything is allocated, exactly as `re.rs` caps
   backtracking steps.
5. **`hash()` of a wide integer is CPython's `x mod 2**61-1`** — and matching it
   would not be enough, because `2**100 == 2.0**100` is True there and the two
   are ONE dict key. EQUALITY decides the collapse, not the hash, so a wide
   integer refuses as a key rather than make two.
6. **A wide integer mixed with a float** — `/`, `float()`, `==`, `<`, `%f` — is
   the same question and refuses for the same reason.

**`REFUSED` is the half that matters.** Every row there is something CPython
answers; any answer here would be a wrong one at exit 0 or an exit code the
chain never retries.

**`AFTER_A_BARRIER` is the class `docs/HILLCLIMB.md` iteration 77 named.**
`bigint` is a value-dependent kind by nature — `r *= i` in a loop overflows on an
iteration no walk can pick out — so it keeps the runtime backstop, and a runtime
refusal reached after `os.mkdir` has committed the write barrier is exit 1 with
the directory on disk and no answer (issue #51). What this capability changes is
the DIRECTION: it turns the overwhelming majority of those runtime refusals into
answers, and the rows below pin the shapes that are still refusals — each run as
``os.mkdir('NEWD'); <the call>``, each asserted four ways, the fourth by a
listing of the cwd before and after rather than by reading the message.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

# ---- the grid --------------------------------------------------------------

#: One wide value per shape worth getting wrong: the two i64 boundaries, a
#: power, a factorial, a negative, and one built by parsing rather than by
#: arithmetic.
WIDE = (
    "2**100", "-2**100", "2**63", "-(2**63)-1", "2**64", "10**20", "10**25",
    "-(10**25)", "(2**100+1)", "3**77", "-(3**77)", "(1<<70)", "(-2)**101",
    "int('1'*25)", "int('-' + '9'*22)", "9223372036854775808",
    "-9223372036854775809", "0xFFFFFFFFFFFFFFFF", "0o777777777777777777777777",
    "0b1" + "0"*70,
)

#: Trap 3, and the arithmetic every corpus program in the `bigint` row types.
ARITHMETIC = [
    "print(2 ** 100, 10 ** 20 + 1)",
    "print(10**20)",
    "print(2**62, 2**63)",
    "result = 1\nfor i in range(1, 31):\n    result *= i\nprint(result)",
    "r = 1\nfor i in range(2, 31):\n    r *= i\nprint(r)",
    "acc = 1\nfor n in range(1, 31):\n    acc = acc * n\nprint(acc)",
    "print(0xFFFF_FFFF_FFFF_FFFF >> 1, 1<<62, (1<<62)+1, 0x1_0000_0000_0000)",
    "print(9223372036854775807 + 1, -9223372036854775808 - 1)",
    "print(-(-9223372036854775808), abs(-9223372036854775808))",
    "print((2**63) // -1, (2**63) % -1, -(2**63) // -1)",
    "print(2**100 * 0, 0 * 2**100, 2**100 - 2**100, type(2**100 - 2**100))",
    "print(2**100 // 2**100, 2**100 % 2**100)",
    "print(pow(2, 100), pow(10, 25))",
    "print(sum([2**100, 1]), sum([1, 2**100]), sum([2**100], 5))",
    "x = 2**100\nx += 1\nx *= 2\nx //= 3\nx -= 7\nx %= 10**9\nprint(x)",
    "print((2**100) ** 2)",
    "print(2 ** 100 ** 1)",
    "print(0 ** 100, 1 ** 100, (-1) ** 101)",
    "print(2**200 // (2**100), 2**200 % (2**100 + 1))",
    "print((2**100) >> 100, (2**100) >> 200, (2**100) << 3)",
    "print(-(2**100+1) >> 3, (-(2**100)) >> 100, (-(2**100)) >> 101)",
    "print(1 << 100, 1 << 63, 1 << 64, 1 << 0)",
    # A shift COUNT past 64 is not a machine word that is required, it is a
    # wide RESULT — `1 << 64` refused while `2 ** 64`, the same integer,
    # answered, and `0 << 64` refused for an answer of 0.
    "print(1 << 64, 1 << 65, 2 << 63, 3 << 70, -5 << 70)",
    "print(0 << 64, 0 << 10000, True << 64, (1 << 100) >> 100)",
    "print(1 << 64 == 2 ** 64, (1 << 200) == 2 ** 200)",
]

#: Trap 3 in full: the sign cross-product of `//`, `%` and `divmod`, which Rust
#: gets wrong in both directions.
SIGNS = [
    "print(%s(%s, %s))" % (f, a, b)
    for f in ("divmod",)
    for a in ("2**100", "-2**100", "2**100+1", "-(2**100+1)")
    for b in ("3", "-3", "7", "-7", "2**64", "-(2**64)", "10**11", "-(10**11)")
] + [
    "print((%s) %s (%s))" % (a, op, b)
    for op in ("//", "%")
    for a in ("2**100", "-2**100", "10**25", "-(10**25)")
    for b in ("3", "-3", "10**9", "-(10**9)", "2**64", "-(2**64)")
]

#: Trap 1, and the radix forms that have no cap of their own.
RENDERING = [
    "print(%s(%s))" % (f, v)
    for f in ("str", "repr", "hex", "oct", "bin", "abs", "bool", "int")
    for v in WIDE
] + [
    "print('%%s %%d %%r' %% (%s, %s, %s))" % (v, v, v) for v in WIDE[:6]
] + [
    "print('{} {:d} {!r} {:,} {:x} {:X} {:o} {:b}'.format(*[%s]*8))" % v
    for v in WIDE[:6]
] + [
    "print(f'{%s} {%s:d} {%s:+d} {%s:020d} {%s:#x}')" % ((v,) * 5) for v in WIDE[:6]
] + [
    "print(len(str(2**1000)), len(hex(2**4000)), len(bin(10**300)))",
    "print(str(10**4299)[:3], len(str(10**4299)))",
    "print(int(str(2**100)) == 2**100)",
    "print(int('0x' + 'f'*30, 16), int('f'*30, 16), int('7'*30, 8), int('1'*70, 2))",
    "print(int(' 1' + '0'*25 + ' '), int('+' + '1'*22), int('-' + '1'*22))",
    "print(int('1_000_000_000_000_000_000_000_000'))",
    "print(1_000_000_000_000_000_000_000)",
    "import json\nprint(json.dumps({'k': 2**100, 'l': [-(2**100)]}))",
    "import json\nprint(json.loads('123456789012345678901234567890') == 10**29 // 8 * 8 or True)",
    "print(str(2**100).count('0'), str(2**100)[:4], str(2**100)[-4:])",
    # `int(s, base)` whose literal outgrows an i64 in FEWER THAN 19 characters,
    # which only a base above 10 can do. The bignum fallback was screened on a
    # decimal digit count, so each of these raised a spurious ValueError at
    # exit 1 — the program's own exit, which the chain never retries.
    "print(int('ffffffffffffffff', 16), int('f'*17, 16), int('1' + '0'*16, 16))",
    "print(int('z'*13, 36), int('z'*12, 36), int('g'*13, 17), int('zz', 36))",
    "print(int('-ffffffffffffffff', 16), int('0xffffffffffffffff', 16))",
    "print(int('ff_ff_ffffffffffff', 16), int('0b' + '1'*70, 2), int('0o' + '7'*30, 8))",
    "print(int('9223372036854775808'), int('-9223372036854775808'), int('1'*100, 2))",
    # …and the invalid literals that must still be a ValueError, since the
    # screen those shared is the one that was removed.
    "int('', 16)",
    "int('g', 16)",
    "int('_', 16)",
    "int('12', 1)",
    # A `%`-format PRECISION is a minimum digit count, so a wide integer
    # satisfies it several times over. Asking `Int::get()` for a machine word
    # first made `'%.3d' % (2**100,)` refuse where `'%d' % (2**100,)` answers.
    "print('%.3d' % (2**100,), '%.3x' % (2**100,), '%.3X' % (2**100,))",
    "print('%.31d' % (2**100,), '%.3d' % (-(2**100),), '%08.3d' % (2**100,))",
    "print('%.3o' % (2**100,), '%.25o' % (2**100,), '%.3d' % (10**25,))",
    # `'s'` on a number is a ValueError in CPython, not `str()` of it. This
    # padded the digits instead — an answer at exit 0, on the frozen core too.
    "format(2**100, 's')",
    "format(5, 's')",
    "print(f'{5:s}')",
    "format(1.5, 's')",
    "format(True, 's')",
    "print(format('a', 's'), format(5, 'd'), format(5, ''), f'{2**100}')",
    # …and the spellings that must keep ANSWERING, since the guard those share
    # is a type test and a type test is easy to draw one variant too wide.
    "print(format(None, ''), format([1], ''), '{}'.format(b'a'), f'{None}')",
    "print('%10s|%s' % (None, [1]), '%r' % (b'a',), str(None) + str([1]))",
    "print(f'{2**100:>40}'[-4:], format(2**100, '020d')[:4], format(1.5, '.3f'))",
    # Trap 1's two boundaries, from the side that must still ANSWER.
    #
    # CPython counts the digits of the INPUT STRING and exempts every
    # POWER-OF-TWO base; the engine counted the RESULT's magnitude and exempted
    # decimal alone. Both halves are one function now, so both directions of
    # each need a row: one digit under the limit in a screened base, and any
    # width at all in an exempt one. Leading zeros count toward the limit, and a
    # sign, surrounding whitespace and underscores do not — so these four sit
    # right up against it and must not refuse.
    "print(int('0' * 4299 + '1'))",
    "print(int('  ' + '1' * 4300 + '  ') % 1000)",
    "print(int('_'.join(['1' * 100] * 43)) % 1000)",
    "print(int('-' + '1' * 4300) % 1000)",
    "print(int('1' * 4300, 3) % 1000, int('z' * 4300, 36) % 1000)",
    "print(int('1' * 4301, 16) % 1000, int('1' * 100000, 2) % 1000)",
    "print(int('3' * 100000, 4) % 1000, int('7' * 100000, 8) % 1000)",
    "print(int('f' * 100000, 16) % 1000, int('v' * 100000, 32) % 1000)",
    "print(int('0x' + 'f' * 100000, 0) % 1000, int('0b' + '1' * 100000, 0) % 1000)",
    "print(len(format(10**5000, '_x')), len(hex(10**5000)))",
    # …and the screen sits AFTER the well-formedness test, which is CPython's
    # order: a 5,000-character run of `x` is "invalid literal", not "Exceeds the
    # limit", so it must not be refused before it is rejected.
    "int('x' * 5000)",
    "int('!' * 5000, 3)",
    "int('1' * 5000 + 'x')",
]

#: Every place the widened payload flows through a container, a comparison or a
#: builtin — the wiring list from `docs/LYPNING.md` §11 step 5, as programs.
WIRING = [
    "print([%s])" % v for v in WIDE
] + [
    "print((%s,), {'k': %s}, len([%s]))" % ((v,) * 3) for v in WIDE[:6]
] + [
    "print(%s == %s, %s != %s, %s < %s + 1, %s > %s - 1)" % ((v,) * 8) for v in WIDE[:8]
] + [
    "print(%s == 1, %s == True, %s == None, %s == 'a', %s == [1])" % ((v,) * 5)
    for v in WIDE[:6]
] + [
    "print(sorted([%s, 1, -1, 0]), max(1, %s), min(1, %s))" % ((v,) * 3) for v in WIDE[:8]
] + [
    "print(%s in [1, 2, %s], [1, %s].count(%s), [%s].index(%s))" % ((v,) * 6)
    for v in WIDE[:6]
] + [
    "print(bool(%s), not %s, type(%s), isinstance(%s, int))" % ((v,) * 4) for v in WIDE[:6]
] + [
    "print([%s] == [%s], (%s,) == (%s,), {'a': %s} == {'a': %s})" % ((v,) * 6)
    for v in WIDE[:6]
] + [
    "l = [1, %s, 3]\nl.remove(%s)\nl.sort()\nprint(l, list(reversed(l)))" % (v, v)
    for v in WIDE[:4]
] + [
    "print(list(map(str, [%s])), list(filter(None, [0, %s])))" % (v, v) for v in WIDE[:4]
] + [
    "print([i for i in [%s, 0] if i], any([0, %s]), all([%s, 1]))" % ((v,) * 3)
    for v in WIDE[:4]
] + [
    "print(list(enumerate([%s])), dict(a=%s))" % (v, v) for v in WIDE[:4]
] + [
    "a = %s\nb = %s\nprint(a is a, a == b)" % (v, v) for v in WIDE[:4]
] + [
    "print(2**100 == 2**100 == 2**100, -(2**100) < 0 < 2**100)",
    "print(sorted([2**100, 2**99, -(2**100), 0, 1]))",
    "print(max([2**100, 2**99]), min([2**100, 2**99]))",
    "print(sum([i * 10**20 for i in range(5)]))",
    "print(sum(range(10)) + 2**100)",
    "x = 2**100\nprint(x if x else 'no', 'y' if x > 0 else 'n')",
    "import sys\nsys.stdout.write(str(2**100) + '\\n')",
    "print(2**100, end='')\nprint()",
    "print(*[2**100, 2**99], sep='|')",
    "def f(n):\n    return n * n\nprint(f(2**50), f(2**100))",
    "print((lambda n: n + 1)(2**100))",
    "print(len(str(2**100)), str(2**100).startswith('12'))",
]

#: The regression half: the widening touched 135 sites that read an `i64`, and
#: every one of them still has to answer for an ordinary integer.
SMALL = [
    "print([1,2,3][%s])" % n for n in ("0", "1", "-1", "2")
] + [
    "print('abcd'[%s], b'abcd'[%s])" % (n, n) for n in ("0", "-1")
] + [
    "print(list(range(%s)), 'ab' * %s, [0] * %s)" % ((n,) * 3) for n in ("0", "1", "3")
] + [
    "print(chr(65), ord('A'), round(1.23456, 2), round(12345, -2), round(2.5), round(3.5))",
    "print(int('42'), int('0x1f', 16), int(3.9), int(-3.9), int(True), float(1))",
    "print(hex(255), oct(8), bin(5), hex(-255), abs(-3), abs(-3.5))",
    "print('%d %s %x %o' % (5, 5, 255, 8))",
    "print(f'{255:x} {255:#x} {255:,} {-5:05d} {5:+d} {5:<5}|')",
    "print(sum([1,2,3]), sum([1.5,2]), sum([], 5), sum([True, 1]))",
    "print({1: 'a', 1.0: 'b', True: 'c'}, len({1, 1.0, True}))",
    "print(2 in range(5), 1.0 in range(5), True in range(5), 9 in range(5))",
    "print(1 in b'\\x01', 255 in b'\\xff', b'abc'.find(97), b'abc'.find(False))",
    "print(range(5).start, range(5).stop, range(5).step, list(range(10))[2:8:3])",
    "print(divmod(17, 5), 17 // 5, 17 % 5, -17 // 5, -17 % 5, 17 // -5, 17 % -5)",
    "import json\nprint(json.dumps({'a': [1, 2.5, True, None]}))",
    "import sys\nprint(sys.maxsize)",
    "import random\nrandom.seed(1)\nprint(random.randint(1, 10), random.getrandbits(32))",
    "print(9007199254740992 / 3, 4503599627370496 / 3, 1 / 3, 256 / 8)",
    "print(2**53 / 1, (2**53 - 1) / 1, -(2**53) / 2)",
]

#: Trap 2: `int / int` where the quotient needs the exact rounding, which is the
#: whole of the `int-div-precision` blocker.
DIVISION = [
    "print(9007199254740993/3)",
    "print((2**60+1)/3)",
    "print(543804029693342780/509)",
    "print(9007199254740993/3); print(float(9007199254740993)); "
    "print(9007199254740992/3); print(1.0*9007199254740993)",
] + [
    "print(%d / %d)" % (a, b)
    for a in (9007199254740993, 9007199254740995, 2**60 + 1, 2**62 - 1,
              543804029693342780, -9007199254740993, 2**63 - 1, -(2**63))
    for b in (1, 2, 3, 7, 509, 1000003, -3, 2**53 + 1, 2**62)
] + [
    "print(%d / %d)" % (a, b)
    for a in (0, 1, -1, 2**53, 2**53 - 1)
    for b in (2**53 + 1, 2**62, -(2**62), 3)
]

#: `methods::INT_METHODS`, over the width where this capability is the reason
#: the program runs at all.
#:
#: `bit_length` and the two base-256 conversions are served by BOTH variants —
#: they are `int` methods, not a capability — but `cap-bigint` is what decides
#: whether the ANSWER exists here or is a refusal: `(2**100).bit_length()` is
#: 101 on this binary and `unsupported: bigint` on the core, and
#: `int.from_bytes` of nine bytes is the same split read the other way. The
#: rows below are therefore graded against CPython on `lypning-l` and are the
#: half of the method tables this file is the right place for; the arity,
#: byteorder and `signed=` shapes that need no wide integer are enumerated in
#: the shell against CPython instead (CHANGELOG, this date).
NUMERIC = [
    "print((2**100).bit_length())",
    "print((2**100 - 1).bit_length(), (-(2**100)).bit_length())",
    "print((0).bit_length(), (-5).bit_length(), (5).bit_length())",
    "print(True.bit_length(), False.bit_length())",
    "print(int.bit_length(5))",
    "print((255).to_bytes(2, 'big'))",
    "print(int.to_bytes(255, 2, 'big'))",
    "print((-1).to_bytes(4, 'little', signed=True))",
    "print(int.from_bytes(b'\\x01' * 16, 'big'))",
    "print(int.from_bytes(b'\\xff' * 8, 'big'), int.from_bytes(b'\\xff' * 8, 'big', signed=True))",
    "print(int.from_bytes(b'\\x01' + b'\\x00' * 12, 'big') == 2**96)",
    "print((0.1).as_integer_ratio(), (2.5).as_integer_ratio())",
    "print((-0.0).as_integer_ratio(), (0.0).as_integer_ratio())",
    "print((1.5).is_integer(), (2.0).is_integer(), float('nan').is_integer())",
    "print((2**100).as_integer_ratio())",
    "print((5).as_integer_ratio(), (-5).as_integer_ratio(), True.as_integer_ratio())",
]

GRID = ARITHMETIC + SIGNS + RENDERING + WIRING + SMALL + DIVISION + NUMERIC

# ---- the refusals ----------------------------------------------------------

#: Programs CPython answers and this engine must decline rather than guess. A
#: row that ANSWERED here would be a wrong answer at exit 0 or an exit code the
#: chain never retries — the two outcomes `CLAUDE.md` invariant 1 exists for.
REFUSED = [
    # Trap 6: a wide integer mixed with a float, in every spelling.
    "print(2**100 / 2)",
    "print(2**100 / 2**100)",
    "print(float(2**100))",
    "print(2**100 + 1.5)",
    "print(2**100 * 2.0)",
    "print(2**100 == 2.0**100)",
    "print(2**100 > 1.5)",
    "print(2**100 < 1.5)",
    "print(sorted([2**100, 2.5]))",
    "print(min(2**100, 1.5))",
    "print(sum([2**100, 1.5]))",
    "print(1.5 + 2**100)",
    "print('%f' % (2**100,))",
    "print('{:f}'.format(2**100))",
    "print('{:e}'.format(2**100))",
    "print('{:.2%}'.format(2**100))",
    "print((2**100) ** (-1))",
    # Trap 5: a wide integer as a dict key or set member, and `hash`.
    "print({2**100: 1})",
    "print({2**100})",
    "d = {}\nd[2**100] = 1\nprint(d)",
    "print(2**100 in {1: 2})",
    "print(2**100 in {1, 2})",
    # Trap 1: past CPython's own `int_max_str_digits`, where it raises.
    "print(str(10**4300)[:3])",
    "print(len(str(2**20000)))",
    "print(int('1' * 4301))",
    "print(1" + "0" * 4301 + ")",
    # Trap 1, the INPUT side. CPython counts the digits of the STRING and not of
    # the value it converts to, so a long run of leading zeros is over the limit
    # however small the result: `int('0' * 5000 + '123')` is a ValueError there
    # and printed `123` here — on BOTH variants, because the only screen sat on
    # the arm `from_str_radix` reaches by OVERFLOWING, and a literal that fits an
    # i64 never gets there however long it is.
    "print(int('0' * 5000 + '123'))",
    "print(int('0' * 4300 + '1'))",
    "print(int('  ' + '1' * 4301 + '  '))",
    "print(int('-' + '1' * 4301))",
    "print(int(b'1' * 4301))",
    # Trap 1, the BASE. CPython exempts the power-of-two bases — 2, 4, 8, 16 and
    # 32 convert in linear time — and applies the limit to every other base in
    # 2..=36. Only base 10 was screened, so 3, 5, 6, 7, 9, 11..15, 17..31 and
    # 33..36 answered a program CPython refuses.
    "print(int('1' * 4301, 3))",
    "print(int('1' * 4301, 5))",
    "print(int('z' * 4301, 36))",
    "print(int('1' * 4301, 7))",
    "print(int('1' * 4301, 33))",
    # Trap 4: the budgets. A hang is worse than either answer.
    "print(2 ** (10**9))",
    "print(1 << (10**9))",
    "print((10**100) ** (10**7))",
    "print(2 ** (2**100))",
    "print(1 << (2**100))",
    # A machine word is required and there is not one.
    "print([1, 2, 3][2**100])",
    "print(range(2**100))",
    "print('a' * (2**100))",
    "print([0] * (2**100))",
    "print(chr(2**100))",
    "print(bytes(2**100))",
    "print(round(2**100, -2))",
    "print('x'.rjust(2**100))",
    "print(list(range(10))[2**100:])",
    "import sys\nsys.exit(2**100)",
    "import random\nrandom.seed(1)\nprint(random.randrange(2**100))",
    "import random\nrandom.seed(1)\nprint(random.getrandbits(2**100))",
    # Bitwise operators over an infinite two's-complement sign extension.
    "print((2**100) & 1)",
    "print((2**100) | 1)",
    "print((2**100) ^ 1)",
    "print(~(2**100))",
    "print(1 & (2**100))",
    # int methods this engine STILL does not implement — an AttributeError here
    # would be exit 1, which the chain never retries. `cap-bigint` is what makes
    # these programs reachable at all, so the table is part of the capability.
    #
    # `bit_length`, `to_bytes` and `from_bytes` left this list when
    # `INT_METHODS` was added; they are in `NUMERIC` below, graded against
    # CPython rather than asserted to refuse. What is left is the version
    # question and the properties: `bit_count` is 3.10 and `is_integer` is
    # 3.12, and answering either would be a wrong answer on an older reference
    # interpreter, which is exactly the trade `FLOAT_MISSING`'s `from_number`
    # row states.
    "print((5).bit_count())",
    "print((5).numerator, (5).denominator)",
    "print((5).real, (5).imag, (5).conjugate())",
    "print((5).is_integer())",
    # `int.from_bytes` read off the TYPE OBJECT is a CLASSMETHOD, and the
    # classmethod's result is `cls` — so `bool.from_bytes` is a `bool` where
    # this engine's table would answer an `int`. That one stays a refusal
    # whatever `int` serves.
    "print(int.from_bytes)",
    "print(bool.from_bytes(b'\\x01', 'big'))",
    # …and the OTHER half of the same rule, which a differential sweep over
    # `format_inner` turned up while the `'s'` row above was being written:
    # only `str`, `int`, `bool` and `float` have a `__format__` of their own,
    # so every other type inherits `object.__format__` and CPython raises
    # TypeError for any non-empty spec. These padded instead — an answer at
    # exit 0, on both variants, and the same shape `Path` already had a guard
    # for two lines above it.
    "print(format(None, '5s'))",
    "print(format([1], '>8s'))",
    "print(format(b'a', 's'))",
    "print(f'{None:>8}')",
    "print(f'{(1, 2):>8}')",
    "print(format(range(3), '5s'))",
    # A Counter count past the machine word, which CPython orders and this
    # engine will not.
    "import collections\nc = collections.Counter()\nc['a'] = 2**100\n"
    "print(c.most_common())",
]

#: One row per refusal KIND a program can reach at RUNTIME with a wide value
#: already in hand, each asked through a committed write barrier.
#:
#: `bigint` cannot be hoisted into the walk the way `glob-order` was: the value
#: that overflows is COMPUTED, and `r *= i` in a loop crosses the boundary on an
#: iteration no static analysis can name. So these rows do NOT assert that the
#: cwd is untouched — they assert the opposite of what `glob` asserted, and they
#: say so out loud: this is the residue issue #51 covers, it is the same residue
#: the engine had BEFORE `cap-bigint` (every one of these programs refused then
#: too, one operation earlier), and what the capability changes is how few of
#: them are left.
RUNTIME_BACKSTOP = [
    ("bigint", "print(2**100 / 2)"),
    ("bigint", "print(float(2**100))"),
    ("bigint", "print(2**100 == 2.0**100)"),
    ("bigint", "print(sorted([2**100, 2.5]))"),
    ("bigint", "print({2**100: 1})"),
    ("bigint", "print((2**100) & 1)"),
    ("bigint", "print([1, 2, 3][2**100])"),
    ("bigint", "print(2 ** (10**9))"),
    ("bigint", "print(str(10**4300)[:3])"),
    ("bigint", "n = 1\nfor i in range(1, 40):\n    n *= i\nprint(n / 3)"),
    # `bit_length` was this row until `INT_METHODS` served it. The kind still
    # has a runtime backstop and it is still value-dependent: `bit_count` is a
    # name `int` does not have here (it is CPython 3.10's, and answering it
    # would be a wrong answer on an older reference interpreter), and only
    # `cap-bigint` makes a program that reaches it on a wide value run far
    # enough to ask.
    ("int-method", "print((2**100).bit_count())"),
    # `to_bytes` of a wide integer needs a machine word this engine does not
    # have for it — served for an `i64` receiver, refused past one, never
    # truncated.
    ("bigint", "print((2**100).to_bytes(16, 'big'))"),
]

#: The refusals a walk CAN see, because the digits are in the source. A literal
#: past `int_max_str_digits` is decided by the LEXER, so the program never
#: starts and the barrier is never committed — this is the one row of this
#: capability that is static, and it is asserted the way `glob`'s were: by a
#: listing of the cwd before and after.
#: `hex`/`oct`/`bin` literals have no digit cap in CPython either, so only the
#: DECIMAL row belongs here; a 4301-digit hex literal is an ordinary value.
AFTER_A_BARRIER = [
    ("bigint", "print(1" + "0" * 4301 + ")"),
    ("bigint", "x = 1" + "0" * 5000 + "\nprint('never')"),
]

#: `PYTHONINTMAXSTRDIGITS`, both sides. Every row runs on the binary and on
#: CPython under the SAME value, and the pair must end the same way — or the
#: binary must refuse, which is the only other outcome invariant 2 allows.
#:
#: The values are not decoration. Each was read off CPython 3.14.5 first and
#: each pins a different rule: an accepted limit BELOW the compiled-in 4300
#: (which is the whole bug — `str(10**700)` answered 701 there); a limit that
#: is not a limit, where CPython will not start at all; `strtol`'s exact
#: acceptance, which is not `str::parse`'s (` 640` yes, `640 ` no, `0x280` no,
#: `+640` and `0640` yes); the C `int` ceiling; and 0, which means unlimited in
#: CPython and does NOT here — this engine only ever LOWERS the limit, so 0 is
#: a refusal and not an answer, which invariant 1 says is never a bug.
ENV_LIMIT = [
    ("640", "print(len(str(10**700)))"),
    ("640", "print(str(10**700)[:3])"),
    ("640", "print(int('1' * 700))"),
    ("640", "print(len(str(10**600)))"),
    ("640", "print(len(hex(10**700)))"),
    ("640", "print(int('1' * 700, 3) % 1000)"),
    ("640", "print(int('1' * 600, 3) % 1000)"),
    ("640", "print(int('1' * 700, 2) % 1000)"),
    ("640", "print(int('0' * 700 + '1'))"),
    ("700", "print(len(str(10**700)))"),
    ("4300", "print(len(str(10**700)))"),
    ("0", "print(len(str(10**700)))"),
    ("", "print(len(str(10**700)))"),
    (" 640", "print(1)"),
    ("+640", "print(1)"),
    ("0640", "print(1)"),
    ("-0", "print(1)"),
    ("2147483647", "print(1)"),
    ("640 ", "print(1)"),
    ("0x280", "print(1)"),
    ("640abc", "print(1)"),
    ("  ", "print(1)"),
    ("-1", "print(1)"),
    ("1", "print(1)"),
    ("639", "print(1)"),
    ("abc", "print(1)"),
    ("4294967296", "print(1)"),
    ("99999999999999999999999", "print(1)"),
]

# ---- machinery -------------------------------------------------------------


def _spectrum(binary: Path) -> dict | None:
    """What ``binary`` says it is, or ``None`` if it will not say."""
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
    """A built ``engine`` that carries THIS tree's capability table.

    An installed binary from before this capability landed answers every grid
    row with a refusal, which would turn the whole file into green skips
    measuring nothing. Skipping loudly is the honest failure.
    """
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


BINARY = _current(engines.LYPNING_L, "cap-bigint")
CORE = _current(engines.LYPNING, "cap-bigint")

needs_l = pytest.mark.skipif(
    BINARY is None,
    reason="no lypning-l carrying cap-bigint is built (lypning build --rust)",
)


def _run(argv: list[str], program: str,
         env: dict | None = None) -> subprocess.CompletedProcess:
    """One program, in a temp cwd of its own — invariant 4."""
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True, text=True,
                              cwd=d, timeout=120, env=env)


def _refusal_problem(got: subprocess.CompletedProcess) -> str | None:
    """``None`` if this is a clean exit-90 refusal, else what is wrong with it."""
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
@pytest.mark.parametrize("program", GRID, ids=range(len(GRID)))
def test_the_bigint_grid_agrees_with_cpython(program: str) -> None:
    got = _run([str(BINARY)], program)
    if got.returncode == engines.UNSUPPORTED_EXIT:
        # A refusal is always allowed and is never a bug — but it must be a
        # CLEAN one, and it must be reported, because a row that started
        # refusing is a row that stopped measuring anything.
        problem = _refusal_problem(got)
        assert problem is None, "%s\n  program: %r" % (problem, program)
        pytest.skip("lypning-l refuses this row: %s" % got.stderr.strip()[:160])
    ref = _run([sys.executable], program)
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "lypning-l disagrees with CPython.\n"
        "  program:  %r\n"
        "  lypning-l: %r exit %d %s\n"
        "  cpython:   %r exit %d %s"
        % (program, got.stdout, got.returncode, got.stderr.strip()[-200:],
           ref.stdout, ref.returncode, ref.stderr.strip()[-200:])
    )


@needs_l
@pytest.mark.parametrize("program", REFUSED, ids=range(len(REFUSED)))
def test_the_surface_outside_the_subset_refuses_rather_than_guesses(program: str) -> None:
    """The capability, stated as its boundary.

    CPython answers every one of these. An answer here would be a wrong one at
    exit 0 (a truncated `as i64`, a dict key that collapsed with a float) or an
    `AttributeError` at exit 1, which the dispatcher returns unchanged and the
    caller has no second chance at."""
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got)
    assert problem is None, (
        "this program must refuse, not answer: %s\n  program: %r\n  stderr: %r"
        % (problem, program, got.stderr.strip()[:200])
    )


#: The barrier every row below is asked through. `os.mkdir` is the cheapest
#: thing that commits it: the directory is created immediately, so a refusal
#: that lands afterwards is exit 1 with `NEWD` on disk, and one that lands
#: before it is exit 90 with the cwd untouched. The test reads the cwd, not the
#: message.
BARRIER = "import os\nos.mkdir('NEWD')\n"


def _barriered(call: str) -> str:
    if call.startswith(("import ", "from ")):
        head, tail = call.split("\n", 1)
        return head + "\n" + BARRIER + tail
    return BARRIER + call


def _run_snapshot(program: str) -> tuple[subprocess.CompletedProcess, list[str], list[str]]:
    """One program, with the temp cwd listed before and after it ran."""
    with tempfile.TemporaryDirectory() as d:
        before = sorted(os.listdir(d))
        got = subprocess.run([str(BINARY), "-c", program], capture_output=True,
                             text=True, cwd=d, timeout=120)
        return got, before, sorted(os.listdir(d))


@needs_l
@pytest.mark.parametrize("kind,call", AFTER_A_BARRIER, ids=range(len(AFTER_A_BARRIER)))
def test_a_literal_the_lexer_can_see_refuses_before_the_barrier(kind: str, call: str) -> None:
    """The one refusal of this capability a walk CAN decide.

    The digits are in the source, so the LEXER answers and the program never
    starts. Four assertions, and the fourth is the one that cannot be faked: the
    cwd is listed before and after, so "no side effect" is measured rather than
    read out of the refusal line."""
    program = _barriered(call)
    got, before, after = _run_snapshot(program)
    assert _refusal_problem(got) is None, (
        "%s\n  program: %r\n  stderr: %r"
        % (_refusal_problem(got), program, got.stderr.strip()[:200]))
    assert ": %s: " % kind in got.stderr, (got.stderr.strip()[:200], program)
    assert after == before, (
        "the refusal landed AFTER os.mkdir committed the barrier: %r -> %r\n"
        "  program: %r" % (before, after, program))


@needs_l
@pytest.mark.parametrize("kind,call", RUNTIME_BACKSTOP, ids=range(len(RUNTIME_BACKSTOP)))
def test_what_stays_a_runtime_refusal_still_refuses(kind: str, call: str) -> None:
    """The residue, pinned so it stays a residue — and measured, not assumed.

    These refusals are value-dependent by nature: the integer that overflows is
    COMPUTED. So they keep the runtime backstop, and past a committed barrier
    they are exit 1 with the side effect on disk — issue #51, and the price of
    admitting the shape at all. Every one of them refused BEFORE `cap-bigint`
    too, one operation earlier, so the class is not new; what the capability
    changes is how few programs are left in it.

    What is asserted is only that the backstop is STILL THERE. If a later change
    makes one of these answer instead, the answer would be a wrong one at exit
    0, which is strictly worse."""
    plain = _run([str(BINARY)], call)
    assert _refusal_problem(plain) is None, (
        "without a barrier this must be a clean exit-90 refusal: %s\n  program: %r"
        % (_refusal_problem(plain), call))
    assert ": %s: " % kind in plain.stderr, (plain.stderr.strip()[:200], call)
    got, _, _ = _run_snapshot(_barriered(call))
    assert got.returncode != 0 and got.stdout == "", (
        "this must still refuse — the value is computed, so the walk cannot see it\n"
        "  program: %r\n  stdout: %r" % (call, got.stdout[:200]))


@needs_l
def test_the_capability_is_on_the_larger_variant_only() -> None:
    """The gate this whole file sits behind: the core must still REFUSE a
    bignum, and must route it to the sibling that answers one.

    A capability that leaked into the frozen variant would pass every grid row
    above — it is the same code — so the byte budget is defended here, by asking
    each binary what it is."""
    if CORE is None:
        pytest.skip("no core carrying this tree's capability table is built")
    for program, kind in (("print(2**100)", "bigint"),
                          ("print(9007199254740993/3)", "int-div-precision")):
        refused = _run([str(CORE)], program)
        assert refused.returncode == engines.UNSUPPORTED_EXIT, (program, refused)
        assert refused.stdout == "", (program, refused.stdout)
        assert ": %s: " % kind in refused.stderr, (program, refused.stderr)
        # …and the core's ROUTER knows which sibling answers it, which is the
        # half that makes the refusal cost one spawn instead of a CPython one.
        import json
        route = subprocess.run([str(CORE), "route", "--next", kind],
                               capture_output=True, text=True, timeout=60)
        assert route.returncode == 0, route.stderr
        chain = json.loads(route.stdout.strip())
        assert chain[:1] == [engines.LYPNING_L], (
            "a %s refusal from the core must reach lypning-l, which answers it: %r"
            % (kind, route.stdout))
        # The Python dispatcher's copy of the rule is held to this one by
        # `tests/test_routing.py`'s cross-product, which supplies the static
        # verdicts `chain_after_refusal` needs and this call cannot.


@needs_l
def test_the_python_copy_of_the_capability_table_is_the_binarys_own() -> None:
    """`engines.VARIANT_CAPS` is a copy of `route::SPECTRUM`'s caps column, and
    a copy is honest only while something checks it. `cap-bigint` is the first
    row whose claim is a RUNTIME KIND rather than a module, so the kinds column
    is checked here as well."""
    import json
    out = subprocess.run([str(BINARY), "route", "--spectrum"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    table = json.loads(out.stdout.strip().splitlines()[-1])
    assert table["self"] == engines.LYPNING_L
    assert "cap-bigint" in table["self_caps"]
    assert {r["name"]: tuple(r["caps"]) for r in table["spectrum"]} == engines.VARIANT_CAPS
    row = {r["cap"]: r for r in table["caps"]}["cap-bigint"]
    assert row["modules"] == [], "cap-bigint serves no module; it widens Value::Int"
    assert sorted(row["kinds"]) == ["bigint", "int-div-precision"], row


@needs_l
def test_int_div_precision_left_the_cpython_only_table_in_both_copies() -> None:
    """The kind used to mean "no reimplementation may answer this".

    `bigint.div_exact` answers it exactly, from the integers, so leaving it in
    `route::ONLY_CPYTHON_KINDS` would send every one of those programs past the
    variant that has the answer. Both copies of the table have to agree, which
    is what `tests/test_routing.py` holds in general and what this pins for the
    one row this capability moved."""
    assert "int-div-precision" not in engines.ONLY_CPYTHON_REFUSALS
    out = subprocess.run([str(BINARY), "route", "--next", "int-div-precision"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    # From lypning-l itself there is no larger rung, so the chain is CPython —
    # what matters is the CORE's answer, which the test above pins.
    import json
    assert json.loads(out.stdout.strip()) == [engines.CPYTHON], out.stdout


# ---- PYTHONINTMAXSTRDIGITS -------------------------------------------------


@needs_l
@pytest.mark.parametrize("limit,program", ENV_LIMIT, ids=range(len(ENV_LIMIT)))
def test_the_digit_limit_is_read_from_the_environment(limit: str, program: str) -> None:
    """`sys.get_int_max_str_digits()` is configuration, not a constant.

    It was compiled in as 4300, so under ``PYTHONINTMAXSTRDIGITS=640`` this
    printed ``701`` for ``len(str(10**700))`` where CPython raises ValueError —
    an answer at exit 0 to a program CPython declines, which is the one outcome
    invariant 1 exists for.

    Both sides run under the SAME value, which is what makes the row mean
    anything: the reference is CPython's behaviour with that variable set, not
    CPython's default."""
    env = {**os.environ, "PYTHONINTMAXSTRDIGITS": limit}
    got = _run([str(BINARY)], program, env=env)
    if got.returncode == engines.UNSUPPORTED_EXIT:
        problem = _refusal_problem(got)
        assert problem is None, "%s\n  %s=%r program: %r" % (
            problem, "PYTHONINTMAXSTRDIGITS", limit, program)
        return
    ref = _run([sys.executable], program, env=env)
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "lypning-l disagrees with CPython under PYTHONINTMAXSTRDIGITS=%r.\n"
        "  program:  %r\n"
        "  lypning-l: %r exit %d %s\n"
        "  cpython:   %r exit %d %s"
        % (limit, program, got.stdout, got.returncode, got.stderr.strip()[-200:],
           ref.stdout, ref.returncode, ref.stderr.strip()[-200:]))


@needs_l
def test_a_limit_cpython_will_not_start_with_refuses_before_the_barrier() -> None:
    """The one env-var refusal that is decided before the program exists.

    ``PYTHONINTMAXSTRDIGITS=abc`` is ``Fatal Python error:
    config_init_int_max_str_digits`` — CPython exits 1 having run nothing and
    printed nothing. So this refuses ahead of the parse, and the proof is the
    same one `glob` used: the cwd is listed before and after, so "no side
    effect" is measured rather than read out of the refusal line."""
    program = _barriered("print('never')")
    env = {**os.environ, "PYTHONINTMAXSTRDIGITS": "abc"}
    with tempfile.TemporaryDirectory() as d:
        before = sorted(os.listdir(d))
        got = subprocess.run([str(BINARY), "-c", program], capture_output=True,
                             text=True, cwd=d, timeout=120, env=env)
        after = sorted(os.listdir(d))
    assert _refusal_problem(got) is None, (_refusal_problem(got), got.stderr[:200])
    assert ": env: " in got.stderr, got.stderr.strip()[:200]
    assert after == before, (
        "the refusal landed AFTER os.mkdir committed the barrier: %r -> %r" % (before, after))
    # …and CPython, one spawn later, is the one that gets to say why.
    ref = subprocess.run([sys.executable, "-c", program], capture_output=True,
                         text=True, timeout=120, env=env)
    assert ref.returncode != 0 and ref.stdout == "", (ref.returncode, ref.stdout)


@needs_l
def test_the_frozen_core_reads_the_same_variable() -> None:
    """The check is on the run path, not inside `cap-bigint`.

    An invalid limit stops CPython from starting whatever the program is, so a
    variant with no bignum at all still has to decline `print(1)` — the core
    has no `bigint.rs` to put the rule in, which is why it lives in `host.rs`
    and is called from `main.rs`."""
    if CORE is None:
        pytest.skip("no core carrying this tree's capability table is built")
    env = {**os.environ, "PYTHONINTMAXSTRDIGITS": "639"}
    got = _run([str(CORE)], "print(1)", env=env)
    assert got.returncode == engines.UNSUPPORTED_EXIT, (got.returncode, got.stderr[:200])
    assert got.stdout == "", got.stdout
    assert ": env: " in got.stderr, got.stderr.strip()[:200]
    ok = _run([str(CORE)], "print(1)", env={**os.environ, "PYTHONINTMAXSTRDIGITS": "640"})
    assert (ok.returncode, ok.stdout) == (0, "1\n"), (ok.returncode, ok.stdout)
