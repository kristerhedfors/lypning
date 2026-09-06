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


#: EVERY presentation type and flag combination CPython rejects for an INT, plus
#: the neighbours that make each rejection's boundary visible.
#:
#: Enumerated by RUNNING CPython 3.14.5 over the cross-product — every ASCII
#: character as a presentation type, times sign, `z`, `#`, `0`, grouping and
#: precision — and not by reading its grammar. Three families were answering at
#: exit 0 where it raises, all of them on `c`: `format(1234, '_c')`,
#: `format(1234, '+c')` and `format(1234, '#c')` each padded a character and
#: printed it. Two more were errors of the wrong sentence: the grouping table
#: was four types wide instead of the whole set, and the `z` flag was not in the
#: grammar at all, so `format(1234, 'zd')` was "Invalid format specifier".
#:
#: The ORDER of the checks is as much of the rule as the checks are, and these
#: rows pin it: grouping is decided against the type before the object is looked
#: at (`',a'` names the comma, not the unknown code), then the code against the
#: object (`'zs'` on an int names the `s`, not the `z`), then precision, then
#: `z`, then `c`'s sign and `c`'s alternate form (`'+#c'` names the sign).
INT_REJECTIONS = [
    ",b", ",o", ",x", ",X", ",c", ",n", ",s", ",a", ",z", ",d", ",e", ",%", ",g",
    "_c", "_n", "_s", "_a", "_z", "_b", "_o", "_x", "_X", "_d", "_g", "_e", "_%",
    "a", "r", "q", "S", "D", "0z", "#z", "zz", "0a", "9a",
    "z", "zd", "zb", "zo", "zx", "zX", "zc", "zs", "ze", "zf", "zg", "z%",
    "+z", " z", "-z", "z.3", "z.3f", "z0", "z#", "z8.2f", "+z08.2f", "z,.2f",
    ".3", ".3d", ".3x", ".3o", ".3b", ".3c", "z.3c", "+.3c", "#.3c",
    "+c", "-c", " c", "#c", "+#c", "0c", "<c", ">c", "^c", "=c", "5c", "c", "9c",
    ".", ".d", "#+c", "z+.2f", "#z.0f", "z^8.2f",
]

#: PEP 682's `z`, which the parser above did not know existed — so it fell into
#: the type slot and `format(1.0, 'zf')` was "Invalid format specifier 'zf'", an
#: error at exit 1 where CPython answers `'1.000000'`.
#:
#: What it coerces is a negative zero, and whether a value IS one is a question
#: about the RENDERED digits and not about the value: `format(-0.0001, 'z.2f')`
#: is `'0.00'` and `format(-0.0001, 'z.6f')` is `'-0.000100'`. `-1e-30` keeps its
#: sign at every precision here, and `z^8.2f` is not this flag at all — `z` is
#: the fill when an alignment follows it.
Z_FLAG = [
    "z", "zf", "z.2f", "z.6f", "z.0f", "ze", "z.2e", "zg", "z.3g", "z%",
    "z.2%", "+z.2f", " z.2f", "z08.2f", "z8.2f", "z,.2f", "z_.2f", "z.20f", "z.0e",
]

INT_VALUES = "[1234, -1234, 0, 1, True, False, 65, -65]"
Z_VALUES = "[1.5, -1.5, 0.0, -0.0, -0.0001, -0.4, -0.5, -0.6, -1e-30, -0.004, 1e300, -1e-7]"


def _one_spec(engine: str, spec: str, values: str) -> str | None:
    """``None`` when this spec agrees with CPython for every value, else why."""
    program = PROGRAM.replace(VALUES, values) % spec
    got = engines.run(engine, program, timeout=30)
    if got.refused:
        # Allowed by invariant 1 — but say so, because a row that started
        # refusing is a row that stopped measuring anything.
        return "refused: %s" % got.stderr.strip()[:120]
    ref = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=30
    )
    if (got.stdout, got.returncode) == (ref.stdout, ref.returncode):
        return None
    return next(
        ("lypning=%s cpython=%s" % (x, y)
         for x, y in zip(got.stdout.splitlines(), ref.stdout.splitlines()) if x != y),
        "output lengths differ",
    )


@needs_engine
@pytest.mark.parametrize("engine", [engines.LYPNING, engines.LYPNING_L])
@pytest.mark.parametrize("spec", INT_REJECTIONS, ids=INT_REJECTIONS)
def test_the_int_rejection_family_agrees_with_cpython(engine: str, spec: str) -> None:
    if engines.find(engine) is None:
        pytest.skip("%s is not built" % engine)
    problem = _one_spec(engine, spec, INT_VALUES)
    assert problem is None, "%s: format(v, %r): %s" % (engine, spec, problem)


@needs_engine
@pytest.mark.parametrize("engine", [engines.LYPNING, engines.LYPNING_L])
@pytest.mark.parametrize("spec", Z_FLAG, ids=Z_FLAG)
def test_the_z_flag_agrees_with_cpython(engine: str, spec: str) -> None:
    if engines.find(engine) is None:
        pytest.skip("%s is not built" % engine)
    problem = _one_spec(engine, spec, Z_VALUES)
    assert problem is None, "%s: format(v, %r): %s" % (engine, spec, problem)
