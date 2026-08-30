"""Sort stability, as a grid — including the descending half, which was wrong.

Python's sort is stable, and the guarantee is part of the language rather than
an implementation detail: elements that compare equal come back in the order
they went in. **`reverse=True` does not suspend that.** It inverts the ordering,
not the arrangement of the ties, so `sorted(counts, key=counts.get,
reverse=True)` keeps `b` ahead of `a` when both count 2.

`sort_values` implemented descending as a finished stable ascending sort
followed by `idx.reverse()`, which reverses the ties along with everything else.
CPython reverses the input, sorts, and reverses again (`listobject.c`,
`reverse_slice` either side of the merge) — the second reversal undoes the first
for equal elements and inverts the rest.

A list of examples is a bad instrument for this, for a specific reason: **the
defect is invisible without ties, and invisible again when the tie happens to
land in the right place.** `sorted([5,3,1,4], reverse=True)` is correct under
both implementations, and so is any sort of distinct keys — which is most of the
sorts anyone writes down when trying to think of a test. So the tie structure is
enumerated instead: key functions with 1, 2, 3 and 5 distinct values over
lengths 0 to 13, which forces run lengths on both sides of the merge width and
guarantees ties at every size.

Both interpreters run **the same program** and the answers are compared whole,
which makes this a test of the rule and not of anyone's reading of it. It is one
process each because the enumeration happens inside the program.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from lypning import engines

needs_engine = pytest.mark.skipif(
    engines.find_lypning() is None, reason="the Rust core is not built"
)

#: A linear congruential generator rather than `random`, which lypning refuses —
#: the program has to run on both interpreters or it is not a differential test.
#: The payload carries the original index, so a reordering of equal keys shows up
#: in the output instead of being invisible.
GRID = r"""
seed = 12345


def nxt(m):
    global seed
    seed = (1103515245 * seed + 12345) % 2147483648
    return seed % m


rows = []
for n in range(0, 14):
    for mod in (1, 2, 3, 5):
        data = [(i, nxt(mod)) for i in range(n)]
        for rev in (False, True):
            a = sorted(data, key=lambda t: t[1], reverse=rev)
            b = list(data)
            b.sort(key=lambda t: t[1], reverse=rev)
            # `sorted` and `.sort` must agree with each other as well as with
            # CPython: they are separate call sites into the same routine.
            agree = "same" if a == b else "SPLIT"
            rows.append("%s;%s" % (agree, ",".join("%d:%d" % (x, y) for x, y in a)))

# Sorting without a key at all, where the ties are whole equal values.
for n in range(0, 14):
    for rev in (False, True):
        rows.append("plain;%s" % (sorted([i % 3 for i in range(n)], reverse=rev),))

print(len(rows))
print("|".join(rows))
"""


@needs_engine
def test_the_whole_sort_grid_agrees_with_cpython() -> None:
    ref = subprocess.run(
        [sys.executable, "-c", GRID], capture_output=True, text=True, timeout=120
    )
    assert ref.returncode == 0, "the oracle did not run: %s" % ref.stderr[-400:]
    got = engines.run(engines.LYPNING, GRID, timeout=120)
    if got.refused:
        pytest.fail(
            "lypning now REFUSES the grid program itself — some construct it "
            "uses left the subset, so this test is measuring nothing: %s"
            % got.stderr.strip()[:200]
        )
    assert got.returncode == 0, "lypning exited %d: %s" % (
        got.returncode,
        got.stderr.strip()[-400:],
    )

    mine, theirs = got.stdout.splitlines(), ref.stdout.splitlines()
    assert mine[0] == theirs[0], "the grids are different sizes — the program moved"
    a, b = mine[1].split("|"), theirs[1].split("|")
    split = [x for x in a if x.startswith("SPLIT")]
    assert not split, "sorted() and list.sort() disagree with each other: %s" % split[:3]
    bad = [(i, x, y) for i, (x, y) in enumerate(zip(a, b)) if x != y]
    assert not bad, "%d of %s cells disagree with CPython; first: %s" % (
        len(bad),
        mine[0],
        ["#%d lypning=%s cpython=%s" % (i, x, y) for i, x, y in bad[:6]],
    )
