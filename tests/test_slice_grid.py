"""Slicing and indexing, as a grid: every sequence type against every bound.

The highest-traffic surface in the subset after `print`, and the one where a
wrong answer is least likely to look wrong. Ten sequence receivers — str, empty
str, one-char str, non-ASCII str, bytes, empty bytes, list, empty list, tuple,
range — against every combination of start, stop and step drawn from negative,
zero, small positive, exactly len, past len, and omitted, with steps of both
signs including ones larger than the sequence.

**The result of the first run is worth recording: of 10,990 cells, ZERO were
silent wrong answers.** Every divergence was one of two other kinds, and both are
now closed:

  * `range` could not be SLICED at all. Indexing one worked; slicing fell into
    the "not subscriptable" arm and raised a TypeError — exit 1, which is the
    program's own exit, so the dispatcher does not treat it as a refusal and
    nothing rescued a construct CPython answers. Slicing a range yields a range,
    and both endpoints map through the parent's own start and step. Deriving the
    stop from a COUNT instead gives the same ELEMENTS and a different repr
    (`range(4)[::3]` as `range(0, 6, 3)` rather than `range(0, 4, 3)`), and a
    range's repr is observable, so same-elements is not good enough.
  * Two index-error messages named the wrong type: `bytes` said "bytearray",
    which is not a type this subset has at all, and `range` said "range" where
    CPython says "range object".

The grid stays because the clean result is the point. A rule with 10,990 cells
behind it is a rule that can be changed with confidence; the same rule defended
by fourteen examples is one nobody dares touch.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from lypning import engines

needs_engine = pytest.mark.skipif(
    engines.find_lypning() is None, reason="the Rust core is not built"
)

GRID = r"""
IDX = [None, 0, 1, 2, -1, -2, -5, 3, 4, 99, -99]
STEPS = [None, 1, 2, 3, -1, -2, -3, 99, -99]
SEQS = [("s", "abcd"), ("s0", ""), ("s1", "a"), ("u", "héllo"),
        ("b", b"abcd"), ("b0", b""), ("l", [1, 2, 3, 4]), ("l0", []),
        ("t", (1, 2, 3, 4)), ("r", range(4))]
rows = []
for name, seq in SEQS:
    for a in IDX:
        for b in IDX:
            for st in STEPS:
                try:
                    r = repr(seq[a:b:st])
                except Exception as e:
                    r = "!" + str(e)[:24]
                rows.append("%s[%r:%r:%r]=%s" % (name, a, b, st, r))
    for a in IDX:
        if a is None:
            continue
        try:
            r = repr(seq[a])
        except Exception as e:
            r = "!" + str(e)[:24]
        rows.append("%s[%r]=%s" % (name, a, r))
print(len(rows))
print("|".join(rows))
"""


@needs_engine
def test_the_whole_slice_grid_agrees_with_cpython() -> None:
    ref = subprocess.run(
        [sys.executable, "-c", GRID], capture_output=True, text=True, timeout=120
    )
    assert ref.returncode == 0, "the oracle did not run: %s" % ref.stderr[-400:]
    got = engines.run(engines.LYPNING, GRID, timeout=120)
    if got.refused:
        pytest.fail(
            "lypning REFUSES the grid program — a construct it uses left the "
            "subset, so this is measuring nothing: %s" % got.stderr.strip()[:200]
        )
    assert got.returncode == 0, "lypning exited %d: %s" % (
        got.returncode,
        got.stderr.strip()[-400:],
    )

    mine, theirs = got.stdout.splitlines(), ref.stdout.splitlines()
    assert mine[0] == theirs[0], "the grids are different sizes — the program moved"
    a, b = mine[1].split("|"), theirs[1].split("|")
    bad = [(x, y) for x, y in zip(a, b) if x != y]
    # Reported as a count plus the first few. The count says how broken it is and
    # the examples say how; a diff of 10,990 cells says neither.
    assert not bad, "%d of %s cells disagree with CPython; first: %s" % (
        len(bad),
        mine[0],
        ["lypning=%s cpython=%s" % (x, y) for x, y in bad[:6]],
    )
