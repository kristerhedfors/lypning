"""Strict numeric coverage: these rows must answer, never become green skips.

The engine and CPython run in independent temporary directories. Boundary
failures are caught inside the program so both exception class and message are
compared without depending on traceback paths. The interpreter running pytest
is the reference and must match the engine's build calibration.
"""
from __future__ import annotations

import sys

import pytest

from test_bigint_grid import BINARY, CORE, _refusal_problem, _run, _run_snapshot, needs_l


def agrees(program: str) -> None:
    got = _run([str(BINARY)], program)
    ref = _run([sys.executable], program)
    assert got.returncode != 90, got.stderr
    assert (got.returncode, got.stdout, got.stderr) == (
        ref.returncode, ref.stdout, ref.stderr
    ), (program, got, ref)


@needs_l
@pytest.mark.parametrize("length", [0, 1, 7, 8, 9, 16, 32, 128, 4096])
@pytest.mark.parametrize("order", ["big", "little"])
@pytest.mark.parametrize("signed", [False, True])
def test_to_bytes_boundary_grid(length: int, order: str, signed: bool) -> None:
    bits = length * 8
    # Include a definitely wide receiver even for zero/short output requests.
    agrees(
        "values = [0, 1, -1, 2**100, -(2**100), "
        "2**%d - 1, 2**%d, 2**%d + 1, 2**%d - 1, 2**%d, 2**%d + 1, "
        "-(2**%d) - 1, -(2**%d), -(2**%d) + 1]\n"
        "for v in values:\n"
        "    try:\n"
        "        print(v.to_bytes(%d, %r, signed=%r))\n"
        "    except OverflowError as exc:\n"
        "        print('OverflowError', str(exc))\n"
        % (max(bits - 1, 0), max(bits - 1, 0), max(bits - 1, 0), bits, bits, bits,
           max(bits - 1, 0),
           max(bits - 1, 0), max(bits - 1, 0), length, order, signed)
    )


@needs_l
@pytest.mark.parametrize("order", ["big", "little"])
def test_wide_to_from_roundtrip_and_unbound_method(order: str) -> None:
    agrees(
        "for v in [2**63, 2**64-1, -(2**64), 2**255-1, -(2**255), 3**100]:\n"
        "    b = int.to_bytes(v, 64, %r, signed=True)\n"
        "    print(b, int.from_bytes(b, %r, signed=True) == v)\n" % (order, order)
    )


@needs_l
@pytest.mark.parametrize("value", [
    "0.0", "-0.0", "0.1", "-0.1", "0.75", "1.0", "-1.0",
    "5e-324", "-5e-324", "1e-323", "2.225073858507201e-308",
    "2.2250738585072014e-308", "2.225073858507202e-308",
    "1e-300", "-1e-300", "1e300", "-1e300",
    "1.7976931348623157e308", "-1.7976931348623157e308",
    "float('nan')", "float('inf')", "float('-inf')",
])
def test_float_ratio_extremes(value: str) -> None:
    agrees(
        "try:\n"
        "    print((%s).as_integer_ratio())\n"
        "except ValueError as exc:\n"
        "    print('ValueError', str(exc))\n"
        "except OverflowError as exc:\n"
        "    print('OverflowError', str(exc))\n" % value
    )


@needs_l
def test_float_ratio_exponent_grid() -> None:
    # Generate literals outside the interpreter: exercises every binary64
    # exponent without relying on the engine's float power implementation.
    import math
    values = []
    for exponent in range(-1074, 1024):
        value = math.ldexp(1.0, exponent)
        values.extend([repr(value), repr(-value)])
    for start in range(0, len(values), 128):
        agrees("for v in [%s]:\n    print(v.as_integer_ratio())\n"
               % ",".join(values[start:start + 128]))


@needs_l
def test_float_ratio_seeded_mantissas() -> None:
    import math
    import random
    import struct
    rng = random.Random(20260915)
    values = []
    while len(values) < 512:
        value = struct.unpack(">d", rng.getrandbits(64).to_bytes(8, "big"))[0]
        if math.isfinite(value):
            values.append(repr(value))
    for start in range(0, len(values), 128):
        agrees("for v in [%s]:\n    print(v.as_integer_ratio())\n"
               % ",".join(values[start:start + 128]))


@needs_l
@pytest.mark.parametrize("call", [
    "(2**100).to_bytes(4097, 'big')",
    "(-(2**100)).to_bytes(4097, 'little', signed=True)",
])
def test_conversion_budget_refuses_without_stdout(call: str) -> None:
    got = _run([str(BINARY)], "print('buffered')\nprint(%s)" % call)
    assert _refusal_problem(got) is None, got
    assert ": int-method: " in got.stderr


@needs_l
@pytest.mark.parametrize("call", [
    "(2**100).to_bytes(16, 'big')", "(1e300).as_integer_ratio()",
    "(5e-324).as_integer_ratio()",
])
def test_new_numeric_answers_survive_a_committed_barrier(call: str) -> None:
    program = "import os\nos.mkdir('NEWD')\nprint(%s)" % call
    got, before, after = _run_snapshot(program)
    ref = _run([sys.executable], program)
    assert (got.returncode, got.stdout, got.stderr) == (
        ref.returncode, ref.stdout, ref.stderr
    )
    assert before == [] and after == ["NEWD"]


@pytest.mark.skipif(CORE is None, reason="frozen core not built")
@pytest.mark.parametrize("call", [
    "(2**100).to_bytes(16, 'big')", "(1e300).as_integer_ratio()",
    "(5e-324).as_integer_ratio()",
])
def test_frozen_core_still_refuses_wide_results(call: str) -> None:
    got = _run([str(CORE)], "print(%s)" % call)
    assert got.returncode == 90 and got.stdout == ""
    assert got.stderr.startswith("lypning: unsupported: bigint: ")
    assert len(got.stderr.splitlines()) == 1
