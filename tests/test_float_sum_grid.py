"""`sum()` over floats, as a grid: answer where 3.11 and 3.12+ agree, refuse where not.

CPython 3.12 changed `sum` (`builtin_sum_impl`, bltinmodule.c): the float path
runs Neumaier compensated summation, so `sum([0.1] * 10)` is `1.0` on 3.12+
and `0.9999999999999999` on 3.11 and earlier. lypning was a naive left fold,
which is 3.11's answer — a MISMATCH against a 3.12+ reference, and silently so,
because it is a plausible float printed at exit 0.

This tree targets both versions, so the engine may only answer where they agree.
It computes the naive sum and the correction in one loop and refuses with
`float-sum` when adding the correction changes a bit. The verdict is derived
rather than enumerated: the oracle is the reference interpreter's own `sum`
against a left fold with `operator.add`, which is what 3.11's `sum` is. Where
those two agree, lypning must print exactly what CPython prints; where they
differ, lypning must refuse — and the cases where they differ are the ones
that would have been wrong.

Every case is its own process on each side because a refusal stops a program,
and a grid that stops measures nothing after that point.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from lypning import engines

needs_engine = pytest.mark.skipif(
    engines.find_lypning() is None, reason="the Rust core is not built"
)

#: The expression under test and, in the same program, the oracle: `sum` of the
#: same list against a left fold from the same start. The list is
#: materialised once so a generator case draws its numbers only once.
CASES = [
    "[0.1] * 10",
    "[0.1, 0.2, 0.3]",
    "[1e16, 1.0, -1e16]",
    "[1e16, 1.0, 1.0, -1e16]",
    "[1, 2.5]",
    "[2.5, 1]",
    "[0.5, 0.5]",
    "[1.5] * 4",
    "[1.5, 2.5, 3.5, 4.5]",
    "[float('inf'), 1.0]",
    "[float('nan')]",
    "[1e308, 1e308]",
    "[1e308, 1e308, -1e308]",
    "[-0.0]",
    "[-0.0, 0.0]",
    "[0.1, 1e-20, -0.1]",
    "[True, 1.5]",
    "[1, True, 2.5]",
    "[2.5, True]",
    "[3, 4, 5.5, 6]",
    "[1.0, 1e-16, 1e-16]",
    "[1e-16, 1e-16, 1.0]",
    "[0.1] * 3, 1",
    "[0.1] * 10, 5",
    "[1, 2], 0.5",
    "[], 0.0",
    "[], 1.5",
    "[-0.0], -0.0",
    "[0.1] * 10, start=0.0",
    # A `bool` start never enters the float loop on any version: generic
    # `PyNumber_Add` from the first element, naive on both, so answered.
    "[0.1] * 10, True",
]

#: Long, irregular lists are the shape the corpus actually has: sums of a
#: thousand draws. `random` is not in this subset (its import is a `module`
#: refusal), so the draws come from a fixed multiplicative hash the subset can
#: compute; whether they compensate to the same bits as the fold is decided by
#: the oracle, not guessed here.
for n in (2, 10, 100, 1000):
    CASES.append("[(i * 7919 %% 10007) / 10007 for i in range(%d)]" % n)
    CASES.append("[(i * 104729 %% 1009) / 3.7 for i in range(%d)]" % n)

PROGRAM = "print(sum(%s))\n"

ORACLE = """
import functools, operator
args = (%s)
xs = list(args[0]) if isinstance(args, tuple) else list(args)
start = args[1] if isinstance(args, tuple) else 0
naive = functools.reduce(operator.add, xs, start)
print(repr(sum(xs, start)) == repr(naive))
"""


def _oracle_args(case: str) -> str:
    # `start=` keyword becomes a positional for the tuple the oracle unpacks.
    return case.replace("start=", "")


@needs_engine
@pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="the oracle interpreter must be one that compensates",
)
@pytest.mark.parametrize("case", CASES)
def test_float_sum_answers_only_where_the_versions_agree(case: str) -> None:
    prog = PROGRAM % case
    ref = subprocess.run(
        [sys.executable, "-c", prog], capture_output=True, text=True, timeout=60
    )
    assert ref.returncode == 0, "the reference did not run: %s" % ref.stderr[-300:]
    verdict = subprocess.run(
        [sys.executable, "-c", ORACLE % _oracle_args(case)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert verdict.returncode == 0, "the oracle did not run: %s" % verdict.stderr[-300:]
    agree = verdict.stdout.strip() == "True"

    got = engines.run(engines.LYPNING, prog, timeout=60)
    if agree:
        assert not got.refused, (
            "3.11 and 3.12+ agree on sum(%s) = %s, yet lypning refuses: %s"
            % (case, ref.stdout.strip(), got.stderr.strip()[:200])
        )
        assert (got.returncode, got.stdout) == (0, ref.stdout), (
            "sum(%s): lypning %r, CPython %r" % (case, got.stdout, ref.stdout)
        )
    else:
        # The two versions print different floats here. An answer — either of
        # them — is a MISMATCH for whoever has the other interpreter.
        assert got.refused, "sum(%s) differs between 3.11 and 3.12+ (3.12+: %s), lypning answered %r at exit %d" % (
            case,
            ref.stdout.strip(),
            got.stdout,
            got.returncode,
        )
        assert "unsupported: float-sum:" in got.stderr, got.stderr
        assert got.stdout == ""
