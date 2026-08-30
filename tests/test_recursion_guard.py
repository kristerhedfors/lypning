"""The depth bound, which is the only thing standing between a deep value and
the host's SIGSEGV.

Three descents are driven by data the program supplies — `repr` over nested
containers, `hkey` over nested tuples, and comparison over nested lists — and
every one was measured overflowing the stack before `err::Nest` bounded them.
In the binary that is a crashed one-liner the dispatcher cannot route onward
(139 is not 90). Embedded it is worse: the segfault belongs to the HOST, and a
stack overflow is not an unwind, so `catch_unwind` cannot see it.

This is here rather than in `test_fuzz_findings.py` because those cases assert
lypning AGREES with CPython, and the whole point of these is that lypning
**refuses** where CPython answers. That is the contract working, not failing.

The bound is asserted as a shape, not as a number: shallow answers, deep
refuses, and nothing ever exits with anything else. `MAX_NEST` may move; a test
that pinned 500 would just have to be edited every time it did.
"""

from __future__ import annotations

import pytest

from lypning import engines

needs_engine = pytest.mark.skipif(
    engines.find_lypning() is None, reason="the Rust core is not built"
)

#: Built iteratively rather than with a literal: a 20,000-deep source literal
#: would test the PARSER's depth bound instead of the evaluator's.
_LISTS = "a = []\nfor i in range(%d):\n    a = [a]\nb = []\nfor i in range(%d):\n    b = [b]\n"
_TUPLES = "a = ()\nfor i in range(%d):\n    a = (a,)\n"

#: Two tuples that differ only at the LEAF, so every descent runs the whole way
#: down before it can answer. Unequal on purpose: `is` between two EQUAL
#: immutables refuses on its own terms (interning is CPython's to know, not
#: ours), and this table needs a case the shallow half can still answer.
_TUPLE_PAIR = (
    "a = (1,)\nfor i in range(%d):\n    a = (a,)\n"
    "b = (2,)\nfor i in range(%d):\n    b = (b,)\n"
)

DESCENTS = [
    ("eq", lambda n: (_LISTS % (n, n)) + "print(a == b)"),
    ("order", lambda n: (_LISTS % (n, n)) + "print(sorted([a, b]) is not None)"),
    # A COMPARISON OPERATOR IS NOT THE SORT PATH, and it stopped being one when
    # `a < b` was rewritten as CPython's list_richcompare so the operator's own
    # name could reach the TypeError. `sorted` above still exercises
    # `order`/`seq_order`; this descends through `order_cmp`, which recurses
    # separately and would otherwise be covered by nothing.
    ("compare-op", lambda n: (_LISTS % (n, n)) + "print(a < b)"),
    # `is` between two immutables that are not the same object has to ask
    # whether they are EQUAL before it can answer, so it descends too — a
    # descent `is` did not have until it stopped guessing at interning.
    ("identity", lambda n: (_TUPLE_PAIR % (n, n)) + "print(a is b)"),
    ("membership", lambda n: (_LISTS % (n, n)) + "print(a in [b])"),
    ("hkey", lambda n: (_TUPLES % n) + "d = {}\nd[a] = 1\nprint(len(d))"),
    ("repr", lambda n: (_LISTS % (n, 1)) + "print(len(repr(a)))"),
]


@needs_engine
@pytest.mark.parametrize("name,build", DESCENTS, ids=[d[0] for d in DESCENTS])
@pytest.mark.parametrize("depth", [10, 400])
def test_shallow_is_answered(name, build, depth) -> None:
    got = engines.run(engines.LYPNING, build(depth), timeout=60)
    assert got.returncode == 0, (
        "%s at depth %d should be answered, not refused or crashed: exit %d, %s"
        % (name, depth, got.returncode, got.stderr.strip()[-200:])
    )


@needs_engine
@pytest.mark.parametrize("name,build", DESCENTS, ids=[d[0] for d in DESCENTS])
@pytest.mark.parametrize("depth", [2000, 20000])
def test_deep_refuses_and_never_crashes(name, build, depth) -> None:
    got = engines.run(engines.LYPNING, build(depth), timeout=60)
    assert got.refused, (
        "%s at depth %d exited %d. Anything but 90 here is the failure this "
        "guard exists to prevent — 139 is a SIGSEGV the dispatcher cannot route "
        "onward, and embedded it takes the host down with it. stderr: %s"
        % (name, depth, got.returncode, got.stderr.strip()[-200:])
    )
    assert got.stdout == "", (
        "%s wrote to stdout before refusing (%r) — the commit barrier is what "
        "makes the retry on the next tier safe" % (name, got.stdout)
    )
