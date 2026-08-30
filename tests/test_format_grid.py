"""The format mini-language, as a grid over the whole spec cross-product.

Run one program per SPEC rather than one for everything, because a refusal is
exit 90 and stops the program — and 720 of the 1,746 specs here are refusals,
which is the contract working, not a defect. Of the 1,026 that tier 1 does
answer, **300 disagreed with CPython** when this grid was first run. Six
families, and each needed its own fix:

  * `format(5, '<04')` was `'5   '`, not `'5000'`. The `0` flag sets the FILL
    whatever the alignment is; it supplies the alignment only when none was
    given. Setting both together meant an explicit alignment silently lost the
    zero fill. An explicit fill still wins: `format(5, '*<04')` is `'5***'`.
  * `format(5, '09,')` was `'000000005'`, not `'0,000,005'` — the pad zeros are
    part of the number and take separators with them. Only on the `=` path,
    which is why `format(5, '<09,')` really is `'500000000'`. The digit count is
    the smallest whose GROUPED length reaches the space available, which is why
    `format(5, '012,')` is thirteen characters: nine digits group to eleven and
    ten group to thirteen, and there is no way to land on twelve without a
    leading separator.
  * `,` and `_` were ignored for the `g` and `%` presentation types.
  * A precision with an EMPTY presentation type was ignored, so
    `format(123456.789, '.4')` answered the whole repr where CPython answers
    `'1.235e+05'`. The rule is `g`, except that fixed notation always keeps a
    digit past the point — and **that digit costs a significant place**, which
    is what decides the notation: `format(12.0, '.2')` is `'1.2e+01'` because
    `'12.0'` needs three significant digits, and `format(12.0, '.3')` is `'12.0'`
    because three were allowed.
  * A precision on an INTEGER presentation type is a ValueError in CPython and
    was ignored here. It is checked on the value as well as the type, because
    `format(0.0, '.2d')` is "Unknown format code 'd'" — a different complaint
    that comes first.
  * `#` with a zero precision and a grouping character put the point after the
    leading digit: `format(1234.0, '#,.0f')` was `'1.,234'`. A separator is part
    of the significand, and the scan for the end of the number stopped at the
    first one.

The `%` operator shares every rule here EXCEPT the last-but-one: `'%.2d' % 5` is
`'05'`, because `%` reads a precision on an integer as a minimum digit count,
which the mini-language has no spelling for. That is why `format_value` and
`format_value_pct` are two entry points and not one.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from lypning import engines

needs_engine = pytest.mark.skipif(
    engines.find_lypning() is None, reason="the Rust core is not built"
)

SPECS = []
for _fill in ["", "*", "0"]:
    for _align in ["", "<", ">", "^", "="]:
        if _fill and not _align:
            continue
        for _zero in ["", "0"]:
            for _width in ["", "1", "4", "9"]:
                for _group in ["", ",", "_"]:
                    SPECS.append(_fill + _align + _zero + _width + _group)
for _sign in ["", "+", " "]:
    for _alt in ["", "#"]:
        for _width in ["", "9"]:
            for _group in ["", ",", "_"]:
                for _prec in ["", ".0", ".2", ".6"]:
                    for _t in ["", "d", "f", "e", "g", "%", "x", "o", "b", "n"]:
                        SPECS.append(_sign + _alt + _width + _group + _prec + _t)
SPECS = list(dict.fromkeys(SPECS))

VALUES = "[0, 5, -5, 1234, -1234, 1234567, 0.0, -0.0, 1.5, -1.5, 1234.5, -1234.5, 0.0001234, 12.5]"

PROGRAM = (
    "SPEC = %r\n"
    "for v in " + VALUES + ":\n"
    "    try:\n"
    "        print(repr(format(v, SPEC)))\n"
    "    except Exception as e:\n"
    "        print('!ERR', e)\n"
)


@needs_engine
def test_every_format_spec_agrees_with_cpython() -> None:
    ran = refused = 0
    bad = []
    for spec in SPECS:
        program = PROGRAM % spec
        got = engines.run(engines.LYPNING, program, timeout=30)
        if got.refused:
            # Exit 90 is the contract working: the dispatcher hands the program
            # to CPython. Counted, not failed.
            refused += 1
            continue
        ref = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True, timeout=30
        )
        ran += 1
        if (got.stdout, got.returncode) != (ref.stdout, ref.returncode):
            mine, theirs = got.stdout.splitlines(), ref.stdout.splitlines()
            first = next(
                ("lypning=%s cpython=%s" % (x, y) for x, y in zip(mine, theirs) if x != y),
                "output lengths differ",
            )
            bad.append("%r: %s" % (spec, first))
    assert ran > 500, (
        "only %d of %d specs ran (%d refused) — the subset shrank and this grid "
        "is measuring far less than it did" % (ran, len(SPECS), refused)
    )
    assert not bad, "%d of %d runnable specs disagree with CPython; first: %s" % (
        len(bad),
        ran,
        bad[:6],
    )
