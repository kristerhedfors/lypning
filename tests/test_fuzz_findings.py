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
    # Python's whitespace is Unicode White_Space PLUS U+001C-U+001F, which Rust's
    # `char::is_whitespace` excludes. Four characters, and every one of them was
    # a wrong answer at exit 0 in `split`, `strip`, `lstrip`, `rstrip` and
    # `isspace` at once. Pinned per-method rather than once, because the fix
    # routes five separate call sites through one predicate and any of them
    # could be reverted alone.
    ("split-c0-separator", r"print('a\x1cb'.split())"),
    ("split-c0-unit", r"print('a\x1fb'.split())"),
    ("split-c0-maxsplit", r"print('\x1d a b'.split(None, 1))"),
    ("strip-c0-separator", r"print(repr('\x1ca\x1c'.strip()))"),
    ("lstrip-c0-separator", r"print(repr('\x1ea'.lstrip()))"),
    ("rstrip-c0-separator", r"print(repr('a\x1e'.rstrip()))"),
    ("isspace-c0-separator", r"print('\x1c'.isspace(), '\x1f'.isspace())"),
    # …and `bytes` does NOT follow it: `b'\x1c'.isspace()` is False in CPython.
    # Pinned so that "fix the str set" never gets copied across to the byte one.
    ("bytes-split-c0-is-not-space", r"print(len(b'a\x1cb'.split()))"),
    ("bytes-strip-c0-is-not-space", r"print(b'\x1ca\x1c'.strip())"),
    # …nor does `int()`/`float()`, whose strip is White_Space only.
    ("int-c0-separator-is-not-space", r"print(int('\x0b5'))"),
    # `splitlines` splits on ELEVEN boundaries. This split on three, so seven
    # characters — three of them multi-byte — silently produced one line where
    # CPython produces two. Not the same set as the whitespace one above: a tab
    # is whitespace and not a boundary, `\x1f` is whitespace and not a boundary
    # either, and U+2028 is both.
    ("splitlines-vertical-tab", r"print('a\x0bb'.splitlines())"),
    ("splitlines-form-feed", r"print('a\x0cb'.splitlines())"),
    ("splitlines-file-separator", r"print('a\x1cb'.splitlines())"),
    ("splitlines-group-separator", r"print('a\x1db'.splitlines())"),
    ("splitlines-record-separator", r"print('a\x1eb'.splitlines())"),
    ("splitlines-unit-separator-is-not-a-break", r"print(len('a\x1fb'.splitlines()))"),
    ("splitlines-nel", r"print(len('a\x85b'.splitlines()))"),
    ("splitlines-line-separator", r"print(len('a\u2028b'.splitlines()))"),
    ("splitlines-para-separator", r"print(len('a\u2029b'.splitlines()))"),
    ("splitlines-keepends-multibyte", r"print(len('a\u2028b'.splitlines(True)))"),
    # The two-character boundary has to be consumed as one, and the trailing one
    # must not start an empty last line.
    ("splitlines-crlf", r"print('a\r\nb'.splitlines())"),
    ("splitlines-trailing", r"print('a\nb\n'.splitlines())"),
    ("splitlines-empty", r"print(''.splitlines(), '\n'.splitlines())"),
    # `str.count` took `start` and `end` and ignored both. Not an edge case:
    # `line.count(',', 1)` is ordinary input, and it answered the count over the
    # whole string at exit 0.
    ("count-start", "print('Hello'.count('l', 3))"),
    ("count-start-end", "print('Hello'.count('l', 0, 3))"),
    ("count-empty-needle-bounded", "print('abc'.count('', 1, 2))"),
    ("count-start-past-end", "print('Hello'.count('l', 99))"),
    # `start` is folded-and-floored but NOT capped at len, while `end` is capped
    # at both ends; then `end < start` is no-match and `end == start` is a real
    # empty slice. Capping `start` too collapsed "past the end" onto "empty slice
    # at the end", so the empty needle was found there — six methods, exit 0.
    ("find-start-past-end", "print('Hello'.find('', 99))"),
    ("rfind-start-past-end", "print('Hello'.rfind('', 99))"),
    ("startswith-start-past-end", "print('Hello'.startswith('', 99))"),
    ("endswith-start-past-end", "print('Hello'.endswith('', 99))"),
    ("index-start-past-end", "print('Hello'.find('', 6), 'Hello'.find('', 5))"),
    # The pair no hand-picked list was going to contain: `end` folding to BEFORE
    # `start` is no-match, folding to exactly `start` is the empty slice.
    ("find-end-before-start", "print('a'.find('', 1, -99))"),
    ("find-end-equals-start", "print('a'.find('', 0, -99))"),
    ("find-end-lt-start", "print('Hello'.find('', 3, 2), 'Hello'.find('', 3, 3))"),
    # Character offsets, not byte offsets, once the receiver is not ASCII.
    ("find-non-ascii-offset", "print('héllo é'.find('é', 2))"),
    ("count-non-ascii-bounded", "print('日本語日'.count('日', 1))"),
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
