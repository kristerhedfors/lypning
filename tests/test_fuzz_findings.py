"""Counterexamples `lypning fuzz` found, pinned so they cannot come back.

Every case here was a **wrong answer at exit 0**, or a traceback at exit 1 for a
program CPython runs — the two shapes the refusal contract exists to prevent,
and the two the corpus could not have caught. The corpus is a sample of what
agents happened to type; the fuzzer samples the subset lypning *claims*, which
is why it reaches these at all.

Each case asserts against **live CPython**, never against a remembered string:
a pinned literal would itself be a claim about CPython that could rot.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from lypning import engines

CASES = [
    # seed 1223909964. Exactly -699300699300699.25; one ulp is 0.125, so both
    # …699.2 and …699.3 are 17 digits and both round-trip. CPython resolves the
    # tie to EVEN, Rust's `{:e}` rounded away, and the answer was silently wrong.
    ("float-repr-tie", 'print((float((-143 >> 0)) ** -1) * 1e17)'),
    ("float-repr-tie-direct", 'print(repr(-699300699300699.25))'),
    # seed 1295253061. `bool` is a subclass of `int`, so this searches for byte
    # 0 and answers -1. Matching only Value::Int raised TypeError at exit 1 —
    # and exit 1 is the program's own, so the dispatcher never retried.
    ("bytes-find-bool", 'print(b"a b  c".find(False))'),
    ("bytes-find-bool-hit", r'print(b"a\x00c".find(False))'),
    ("bytes-find-true", 'print(b"abc".find(True))'),
    # The sign of a zero survives the half-even correction.
    ("round-negative-zero", 'print(round(-0.5))'),
    # An infinity gets a sign slot, and zero fill goes between sign and digits.
    ("format-inf-plus", 'print(format(float("inf"), "+"))'),
    ("format-inf-zerofill", 'print(format(float("-inf"), "010"))'),
    # Grouping a body already in exponent form put a separator in the exponent.
    ("format-grouped-exponent", 'print(format(1e17, "_"))'),
    # Cased, not alphabetic: 日 is alphabetic and has no case at all.
    ("islower-uncased", 'print("日本".islower())'),
    ("isupper-uncased", 'print("日本".isupper())'),
]

needs_engine = pytest.mark.skipif(
    engines.find_lypning() is None, reason="the Rust core is not built"
)


@needs_engine
@pytest.mark.parametrize("name,program", CASES, ids=[c[0] for c in CASES])
def test_matches_cpython(name: str, program: str) -> None:
    ref = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=30
    )
    got = engines.run(engines.LYPNING, program, timeout=30)
    if got.unsupported:
        pytest.fail(
            "%s now REFUSES (exit 90) where it used to answer. A refusal is safe, "
            "but this case was pinned as one lypning gets RIGHT — if the feature "
            "was deliberately dropped, delete the case; do not leave it passing "
            "by accident." % name
        )
    assert got.returncode == ref.returncode, (
        "%s: exit %d, CPython gave %d\nlypning stderr: %s"
        % (name, got.returncode, ref.returncode, got.stderr[:300])
    )
    assert got.stdout == ref.stdout, (
        "%s: lypning printed %r, CPython printed %r" % (name, got.stdout, ref.stdout)
    )
