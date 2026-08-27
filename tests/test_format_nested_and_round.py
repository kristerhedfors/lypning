"""Nested replacement fields, and `round` over value x ndigits.

Two small grids that share a file because both were one defect each.

`"{:.{}f}".format(3.14159, 2)` raised `Invalid format specifier`, and
`"{:{}}".format(3.0, 5)` quietly answered `'3e+00'`. A nested field draws from
the same auto-numbering as the field it sits in — outer takes argument 0, inner
takes argument 1 — and the implementation did two things wrong: it expanded the
spec BEFORE the outer field claimed its argument, and it recursed into a fresh
counter that restarted at zero. Explicit numbering (`"{0:.{1}f}"`) never had the
bug, which is why it survived every example anyone wrote down.

`round(5.0, -1)` answered `10.0` where CPython answers `0.0`, and
`round(25.0, -1)` answered `30.0` where CPython answers `20.0`: Rust's `round()`
breaks ties AWAY FROM ZERO and Python breaks them to even. The `ndigits == 0`
branch already carried that correction and the positive branch gets it free from
the formatter; only the negative one was left. `round(int, -n)` used to refuse
outright and is implemented here in integer arithmetic — an int near 2**63 has
more significant digits than a double carries, so scaling through f64 would
answer a rounded number that is not the rounded number.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from lypning import engines

needs_engine = pytest.mark.skipif(
    engines.find_lypning() is None, reason="the Rust core is not built"
)

NESTED = r"""
CASES = [
    ("{:{}}", (3.0, 5)), ("{:.{}f}", (3.14159, 2)), ("{:{}f}", (3.0, 7)),
    ("{0:.{1}f}", (3.14159, 2)), ("{:>{}}", ("a", 4)), ("{:{}.{}f}", (3.14159, 8, 2)),
    ("{}{}", (1, 2)), ("{}{:{}}", (1, 2.0, 6)), ("{:{}}{}", (1.0, 5, "x")),
    ("{1:{0}}", (6, 2.5)), ("{:{}{}}", (5, ">", 4)), ("{:^{}}", ("z", 5)),
    ("{:{}d}", (42, 6)), ("{:{}s}", ("q", 3)), ("{:.{}}", (1.23456, 3)),
    ("{a:{b}}", None), ("{:{}%}", (0.5, 9)), ("{:0{}d}", (7, 4)),
    ("{}-{:{}.{}f}-{}", (0, 1.23456, 9, 3, "e")),
]
rows = []
for f, a in CASES:
    try:
        r = repr(f.format(a=1.5, b=6) if a is None else f.format(*a))
    except Exception as e:
        r = "!" + str(e)[:30]
    rows.append("%s%r=%s" % (f, a, r))
print(len(rows))
print("|".join(rows))
"""

ROUNDING = r"""
VALS = [0.0, -0.0, 0.5, -0.5, 1.5, -1.5, 2.5, -2.5, 5.0, -5.0, 15.0, -15.0,
        25.0, -25.0, 35.0, 45.0, 50.0, 150.0, 250.0, 1234.5678, -1234.5678,
        0.125, 2.675, 1e16, -1e16, 99.5, 100.5, 0.05, 1e-8, 123456789.0]
NDS = [None, 0, 1, 2, 3, -1, -2, -3, -5, 17, 18, -18]
rows = []
for v in VALS:
    for nd in NDS:
        try:
            r = repr(round(v) if nd is None else round(v, nd))
        except Exception as e:
            r = "!" + str(e)[:20]
        rows.append("%r/%r=%s" % (v, nd, r))
INTS = [0, 5, 15, 25, -5, -15, -25, 150, 12345, -12345]
for v in INTS:
    for nd in NDS:
        try:
            r = repr(round(v) if nd is None else round(v, nd))
        except Exception as e:
            r = "!" + str(e)[:20]
        rows.append("i%r/%r=%s" % (v, nd, r))
print(len(rows))
print("|".join(rows))
"""


def _grid(program: str, what: str) -> None:
    ref = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=60
    )
    assert ref.returncode == 0, "the oracle did not run: %s" % ref.stderr[-400:]
    got = engines.run(engines.LYPNING, program, timeout=60)
    if got.refused:
        pytest.fail(
            "lypning REFUSES the %s grid — a construct it uses left the subset, "
            "so this is measuring nothing: %s" % (what, got.stderr.strip()[:200])
        )
    assert got.returncode == 0, "lypning exited %d: %s" % (
        got.returncode,
        got.stderr.strip()[-400:],
    )
    mine, theirs = got.stdout.splitlines(), ref.stdout.splitlines()
    assert mine[0] == theirs[0], "the grids are different sizes — the program moved"
    a, b = mine[1].split("|"), theirs[1].split("|")
    bad = [(x, y) for x, y in zip(a, b) if x != y]
    assert not bad, "%d of %s %s cells disagree with CPython; first: %s" % (
        len(bad),
        mine[0],
        what,
        ["lypning=%s cpython=%s" % (x, y) for x, y in bad[:6]],
    )


@needs_engine
def test_nested_replacement_fields_agree_with_cpython() -> None:
    _grid(NESTED, "nested-field")


@needs_engine
def test_the_round_grid_agrees_with_cpython() -> None:
    _grid(ROUNDING, "round")
