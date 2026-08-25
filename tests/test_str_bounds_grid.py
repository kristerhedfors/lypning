"""The optional `start`/`end` bounds, as a grid rather than a list of examples.

`str.find`, `rfind`, `index`, `rindex`, `count`, `startswith` and `endswith` all
take the same two optional arguments, and CPython's rule for them
(`ADJUST_INDICES`) is deliberately asymmetric: a negative `start` folds and
floors at 0 but a positive one is **never capped at `len`**, while `end` is
capped at both ends; then `end < start` is the no-match answer and
`end == start` is a real empty slice.

Every part of that sentence was wrong here at some point, and the reason this is
a grid is that **a hand-picked list did not find it**. The fix in iteration 24
started as `start > n ⇒ no match`, which made all fourteen chosen examples pass
and still left 609 cells differing — `'a'.find('', 1, -99)` is -1 because `end`
folds to *before* `start`, while `'a'.find('', 0, -99)` is 0. Nobody writes that
pair down on purpose.

So the whole cross-product is enumerated instead: receivers ASCII and not,
needles present, absent, empty and multi-byte, starts and ends negative, zero,
interior, exactly `len`, and past the end. Both interpreters run **the same
program** and the answers are compared as a whole, which is what makes this a
test of the rule rather than of somebody's understanding of it.

It is cheap — one process each, well under a second — because the enumeration
happens inside the program rather than in the harness.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from lypning import engines

needs_engine = pytest.mark.skipif(
    engines.find_lypning() is None, reason="the Rust core is not built"
)

#: Written without `getattr`, which lypning refuses — the program has to run on
#: both interpreters or it is not a differential test.
GRID = r"""
def call(meth, s, needle, start, end):
    try:
        if start is None:
            if meth == 'find': return repr(s.find(needle))
            if meth == 'rfind': return repr(s.rfind(needle))
            if meth == 'count': return repr(s.count(needle))
            if meth == 'startswith': return repr(s.startswith(needle))
            if meth == 'endswith': return repr(s.endswith(needle))
            if meth == 'index': return repr(s.index(needle))
            return repr(s.rindex(needle))
        if end is None:
            if meth == 'find': return repr(s.find(needle, start))
            if meth == 'rfind': return repr(s.rfind(needle, start))
            if meth == 'count': return repr(s.count(needle, start))
            if meth == 'startswith': return repr(s.startswith(needle, start))
            if meth == 'endswith': return repr(s.endswith(needle, start))
            if meth == 'index': return repr(s.index(needle, start))
            return repr(s.rindex(needle, start))
        if meth == 'find': return repr(s.find(needle, start, end))
        if meth == 'rfind': return repr(s.rfind(needle, start, end))
        if meth == 'count': return repr(s.count(needle, start, end))
        if meth == 'startswith': return repr(s.startswith(needle, start, end))
        if meth == 'endswith': return repr(s.endswith(needle, start, end))
        if meth == 'index': return repr(s.index(needle, start, end))
        return repr(s.rindex(needle, start, end))
    except ValueError:
        return 'ValueError'

out = []
for s in ['', 'a', 'Hello', 'aaa', 'abcabc', 'héllo é', '日本語日', 'aé日b']:
    for needle in ['', 'a', 'l', 'ab', 'é', '日', 'lo', '日b']:
        for start in [None, -99, -6, -3, -1, 0, 1, 3, 5, 6, 99]:
            for end in [None, -99, -3, 0, 1, 3, 5, 6, 99]:
                for meth in ['find','rfind','count','startswith','endswith','index','rindex']:
                    out.append(call(meth, s, needle, start, end))
print(len(out))
print('|'.join(out))
"""


@needs_engine
def test_the_whole_bounds_grid_agrees_with_cpython() -> None:
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
    # Reported as a count plus the first few, not as a diff of 44,352 cells: the
    # count says how broken it is and the examples say how.
    bad = [(i, x, y) for i, (x, y) in enumerate(zip(a, b)) if x != y]
    assert not bad, "%d of %s cells disagree with CPython; first: %s" % (
        len(bad),
        mine[0],
        ["#%d lypning=%s cpython=%s" % (i, x, y) for i, x, y in bad[:6]],
    )
