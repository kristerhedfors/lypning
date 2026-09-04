"""The gate's arithmetic. No wall clock, no strace, no binary.

Everything measured here is measured elsewhere by running something, which is
exactly what does not belong in CI. What belongs is the two pure functions the
numbers are fed through: the device-block step function that makes one byte cost
131,071 more, and the cold-cost projection — including its refusal to produce a
number when the baseline was never taken, since a plausible-looking estimate
built on nothing is worse than no estimate.
"""

from __future__ import annotations

import pytest

from lypning import gate


@pytest.mark.parametrize("size,blocks", [
    (0, 0),
    (-1, 0),
    (1, 1),
    (gate.DEVICE_BLOCK, 1),
    (gate.DEVICE_BLOCK + 1, 2),
    (541_688, 5),        # the MicroPython prototype
    (5 * gate.DEVICE_BLOCK, 5),
    (5 * gate.DEVICE_BLOCK + 1, 6),  # the byte that costs a whole block
])
def test_device_blocks_rounds_up(size, blocks):
    assert gate.device_blocks(size) == blocks


MEASURED_BASELINE = {"measured": True, "exists": True, "device_blocks": 8,
                     "opens": 22, "cold_ms": 8000}


def test_cold_cost_scales_on_bytes_when_bytes_dominate():
    assert gate.project_cold_ms(2 * gate.DEVICE_BLOCK, 0, MEASURED_BASELINE) == 2000


def test_cold_cost_scales_on_opens_when_opens_dominate():
    # The pessimistic read: cold cost is bytes fetched AND round trips taken,
    # and neither term alone explains it, so the larger share wins.
    assert gate.project_cold_ms(gate.DEVICE_BLOCK, 11, MEASURED_BASELINE) == 4000


@pytest.mark.parametrize("opens,baseline", [
    (None, MEASURED_BASELINE),
    (0, dict(MEASURED_BASELINE, measured=False)),
    (0, dict(MEASURED_BASELINE, exists=False)),
])
def test_cold_cost_is_none_without_a_measurement(opens, baseline):
    assert gate.project_cold_ms(700_000, opens, baseline) is None


def test_size_of_a_binary_that_is_not_there_is_zero(tmp_path):
    # A missing artifact is caught by its own check, not by an exception here.
    assert gate.size_bytes(tmp_path / "absent") == 0


def test_the_rust_core_is_measured_against_its_own_budget():
    # Two runtimes with different jobs: the lypning-mp byte budget is not a
    # verdict on the Rust core, and reporting it as one would invent a number
    # no document argues for.
    over = gate.MAX_BYTES * 3
    # A Rust variant is gated in device blocks against its own budget — this
    # used to pass anything for `lypning`, and a spectrum whose premise is
    # bytes-per-point cannot leave its points ungated. 2.1 MB is 17 blocks.
    assert not gate._size_check("lypning", over).ok
    assert gate._size_check("lypning", 8 * gate.DEVICE_BLOCK).ok
    assert not gate._size_check("lypning", 8 * gate.DEVICE_BLOCK + 1).ok
    assert gate._size_check("lypning", 8 * gate.DEVICE_BLOCK).unit == "blocks"
    assert not gate._size_check("lypning-mp", over).ok
    assert gate._size_check("lypning-mp", gate.MAX_BYTES).ok
