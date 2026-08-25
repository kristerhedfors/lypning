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
    # `swapcase` took `.next()` of the case mapping, which TRUNCATES every
    # multi-character one, and it uppercased anything that was not uppercase —
    # including titlecase, which CPython leaves alone.
    ("swapcase-sharp-s", "print('ß'.swapcase())"),
    ("swapcase-dotted-i", "print('İ'.swapcase())"),
    ("swapcase-j-caron", "print('ǰ'.swapcase())"),
    ("swapcase-titlecase-is-left-alone", "print('ǅ'.swapcase())"),
    ("swapcase-ascii-unchanged", "print('aB1 c'.swapcase())"),
    # `join` has its own message for a non-iterable, and the fix for it briefly
    # wrapped the whole drain instead of just `make_iter` — which turned every
    # exception the sequence raised WHILE being drained into that message.
    # Turning one exception into a different one is the same class of defect as
    # answering the wrong number, so both halves are pinned.
    ("join-non-iterable-message", "print(','.join(5))"),
    ("join-non-iterable-none", "print(','.join(None))"),
    ("join-generator-raises-through", "print(','.join(str(1//x) for x in [1,0]))"),
    ("join-generator-value-error", "print(','.join(str(int(x)) for x in ['1','z']))"),
    ("join-bad-item-index", "print(','.join(x for x in ['a', 2]))"),
    ("join-empty-and-single", "print(repr(','.join([])), repr(','.join(['a'])))"),
    # A frame's scope map is recycled between calls (ledger, iteration 29), and
    # a scope that ESCAPED the frame must never be. These are the three ways it
    # escapes — a nested `def`, a `lambda`, a generator expression — each with
    # enough intervening calls to have cycled the pool many times over. A
    # recycled captured scope is a cleared map handed to a closure: a wrong
    # answer, not a refusal.
    ("scope-nested-def-escapes", "def outer():\n    x = 1\n    def inner():\n        return x\n    return inner\nprint(outer()())"),
    ("scope-lambda-escapes", "def mk(n):\n    return lambda k: k + n\nprint(mk(10)(1), mk(20)(1))"),
    ("scope-genexpr-outlives-frame", "def gen(n):\n    return (i * n for i in range(3))\nprint(list(gen(5)))"),
    ("scope-closures-survive-recycling", "def mk(n):\n    return lambda: n\ndef noise(k):\n    a=k;b=k;c=k;d=k;e=k;f=k\n    return a+b+c+d+e+f\nfs=[mk(i) for i in range(10)]\nfor i in range(300):\n    noise(i)\nprint([f() for f in fs])"),
    ("scope-genexpr-survives-recycling", "def gen(n):\n    return (i + n for i in range(3))\ndef noise(k):\n    a=k;b=k;c=k;d=k\n    return a+b+c+d\ng = gen(100)\nfor i in range(200):\n    noise(i)\nprint(list(g))"),
    ("scope-two-closures-one-frame", "def mk(n):\n    return (lambda: n), (lambda: n * 2)\na, b = mk(7)\nprint(a(), b())"),
    ("scope-closure-made-in-recursion", "def r(n):\n    if n == 0:\n        return []\n    f = lambda: n\n    return [f] + r(n-1)\nprint([g() for g in r(6)])"),
    # The presized map must hold every name the body binds, not just the params.
    ("scope-varying-local-counts", "def one(): x=1; return x\ndef four(): a=1;b=2;c=3;d=4; return a+b+c+d\ndef eight(): a=1;b=2;c=3;d=4;e=5;f=6;g=7;h=8; return a+b+c+d+e+f+g+h\nprint(sum(one()+four()+eight() for _ in range(50)))"),
    ("scope-kwargs-eight", "def f(a,b,c,d,e,f_,g,h):\n    return a+b+c+d+e+f_+g+h\nprint(f(1,2,3,4,5,6,7,8), f(h=8,g=7,f_=6,e=5,d=4,c=3,b=2,a=1))"),
    # `print`'s `sep` and `end` must be str or None — CPython checks the TYPE
    # rather than converting. Converting them printed `a2` for `end=2` at exit
    # 0, and `sep=1` was accepted silently because with one argument the
    # separator is never used: the bad keyword only showed if a second argument
    # ever appeared.
    ("print-sep-int-is-a-type-error", "print('a', sep=1)"),
    ("print-end-int-is-a-type-error", "print('a', end=2)"),
    ("print-sep-bytes-is-a-type-error", "print('a', 'b', sep=b'x')"),
    ("print-end-list-is-a-type-error", "print('a', end=[1])"),
    ("print-sep-end-none-are-the-defaults", "print('a', 'b', sep=None, end=None)"),
    ("print-sep-end-strings", "print('a', 'b', sep='|', end='#')"),
    ("print-no-args", "print()"),
    ("print-many-args", "print(1, 'a', None, [1, 2], (3,), {'k': 1})"),
    # The recursion guard moved off the scalar paths of `eq`, `order` and `hkey`
    # (ledger, iteration 33). These are the shallow structures it must still get
    # right — the DEEP ones cannot live here because they refuse, and `CASES`
    # asserts a match; `tests/test_recursion_guard.py` covers those.
    ("eq-nested-lists", "print([[1, [2]]] == [[1, [2]]], [[1, [2]]] == [[1, [3]]])"),
    ("eq-numeric-tower", "print(1 == 1.0, 1 == True, 0 == False, 1.5 == 1.5)"),
    ("order-nested", "print(sorted([[2, 1], [1, 9], [1, 2]]))"),
    ("hkey-tuple-nested", "d = {(1, (2, 3)): 'a'}\nprint(d[(1, (2, 3))])"),
    ("hkey-collapses-numeric", "d = {1: 'a'}\nd[1.0] = 'b'\nd[True] = 'c'\nprint(len(d), d[1])"),
    ("membership-mixed", "print(1 in [1.0], 'a' in ['a'], (1,) in [(1,)], None in [None])"),
    # The tuple fast path must not change any of these. (The MESSAGE cases for
    # unpacking are in `STDERR_CASES` below, not here: `CASES` compares stdout
    # and the exit code, so a message case put here passes on the very binary
    # that has the bug — which is how the first draft of these was written.)
    ("unpack-wrong-length-few", "a, b, c = (1, 2)"),
    ("unpack-wrong-length-many", "a, b = (1, 2, 3)"),
    ("unpack-nested", "a, (b, (c, d)) = 1, (2, (3, 4))\nprint(a, b, c, d)"),
    ("unpack-star-mid", "a, *b, c = (1, 2, 3, 4, 5)\nprint(a, b, c)"),
    ("unpack-into-subscripts", "l = [0, 0, 0]\nl[0], (l[1], l[2]) = 1, (2, 3)\nprint(l)"),
    ("unpack-swap", "a, b = 1, 2\na, b = b, a\nprint(a, b)"),
    ("unpack-list-is-snapshotted", "src = [1, 2]\ndef f():\n    src.append(9)\n    return 0\nl = [0, 0]\nl[f()], l[1] = src\nprint(l, src)"),
    # `%`-formatting translates each conversion into a `format()` spec, and the
    # spec was being built in the wrong order with the wrong default alignment.
    # 2,618 cells of the conversion grid.
    #
    # The default alignment for `%s` is RIGHT and `format()`'s default for a
    # string is LEFT, so the translation has to say `>` out loud. Without it
    # every `%` one-liner that lines a column up came out flush the wrong way.
    ("pct-str-width-right-aligns", "print(repr('%5s' % 'ab'))"),
    ("pct-str-width-left-with-dash", "print(repr('%-5s' % 'ab'))"),
    ("pct-repr-width-right-aligns", "print(repr('%5r' % 'ab'))"),
    ("pct-str-zero-flag-is-ignored", "print(repr('%05s' % 'a'))"),
    ("pct-str-width-and-precision", "print(repr('%5.2s' % 'abc'))"),
    # The pieces of a format spec are not commutative: `+0f` is valid and `0+f`
    # is a ValueError, `#05x` is `0x0ff` and `0#5x` is a ValueError. The zero-pad
    # flag was emitted first, as if it were an alignment.
    ("pct-sign-then-zero-float", "print(repr('%+0f' % 0))"),
    ("pct-space-then-zero-float", "print(repr('% 0e' % 0))"),
    ("pct-zero-width-int", "print(repr('%05d' % 42), repr('%05d' % -42))"),
    ("pct-plus-zero-width-int", "print(repr('%+05d' % 42))"),
    # A `-` beats a `0`, and neither applies to a string conversion.
    ("pct-dash-beats-zero", "print(repr('%-05d' % 255), repr('%-05.1f' % 1.5))"),
    ("pct-alt-form-hex-width", "print(repr('%-#5x' % 255), repr('%#5x' % 255))"),
    ("pct-sign-and-dash", "print(repr('%+-5d' % 255), repr('% -5d' % 255))"),
    # The `0x` prefix belongs in the slot that precedes zero fill, exactly as a
    # sign does: prepending it to the body made `format(255, '#010x')` come out
    # `'00000000xff'` instead of `'0x000000ff'`. Reachable with no `%` anywhere.
    ("format-alt-hex-zero-fill", "print(repr(format(255, '#010x')))"),
    ("format-alt-hex-negative", "print(repr(format(-255, '#010x')))"),
    ("format-alt-oct-bin-zero-fill", "print(repr(format(8, '#010o')), repr(format(5, '#010b')))"),
    ("format-alt-hex-sign", "print(repr(format(255, '+#010x')), repr(format(255, '#10x')))"),
    # `#` on a float keeps the decimal point when the precision left no digits
    # after it, and the point goes after the significand — the same place for
    # `f`, and not the same place for `e` or `%`.
    ("format-alt-float-keeps-point", "print(repr(format(0.0, '#.0f')), repr(format(-2.0, '#.0f')))"),
    ("format-alt-exp-keeps-point", "print(repr(format(1234.0, '#.0e')), repr(format(0.0, '#.0e')))"),
    ("format-alt-general-keeps-point", "print(repr(format(0.0, '#.0g')), repr(format(1.0, '#.0g')))"),
    ("format-alt-percent-keeps-point", "print(repr(format(0.5, '#.0%')))"),
    ("format-alt-is-a-noop-above-zero", "print(repr(format(1.0, '#.2f')), repr(format(0.0, '#g')))"),
    ("pct-alt-float-keeps-point", "print(repr('%#.0f' % 0.0), repr('%#.0e' % 1234.0))"),
    # `%c` takes an int OR a one-character string, aligns right by default, and
    # raises OverflowError — not ValueError — out of range. `format()`'s `c`
    # shares the alignment and the exception and NOT the one-character string.
    ("pct-c-single-char-string", "print(repr('%c' % 'a'), repr('%c%c' % (72, 'i')))"),
    ("pct-c-bad-string-is-a-type-error", "print('%c' % 'ab')"),
    ("pct-c-float-is-a-type-error", "print('%c' % 1.5)"),
    ("pct-c-width-right-aligns", "print(repr('%5c' % 65), repr('%-5c' % 65), repr('%05c' % 65))"),
    ("pct-c-out-of-range-overflows", "print('%c' % -1)"),
    ("format-c-out-of-range-overflows", "print(format(1114112, 'c'))"),
    ("format-c-width-right-aligns", "print(repr(format(65, '5c')), repr(format(65, '<5c')))"),
    # A precision on an integer conversion is minimum DIGITS. lypning refuses the
    # cases where that adds digits (see the ledger) and must keep answering the
    # ones where it does not — deciding that needs the VALUE, so these pin the
    # boundary rather than the refusal.
    ("pct-int-precision-already-satisfied", "print(repr('%.2d' % 42), repr('%.2d' % -42), repr('%.2x' % 255))"),
    ("pct-int-precision-zero", "print(repr('%.0d' % 1), repr('%.d' % 1))"),
    ("pct-precision-on-c-is-ignored", "print(repr('%.2c' % 65))"),
    ("pct-precision-on-str-truncates", "print(repr('%.2s' % 'abc'), repr('%5.2s' % 'abc'))"),
    # `count`/`find`/`rfind`/`index`/`rindex` take a byte-scan fast path for a
    # one-BYTE ASCII needle (ledger, iteration 41). The guard is `< 0x80`, not
    # `len() == 1`: a one-byte needle is not a one-character needle, and the
    # non-ASCII cases below are what tells the two apart.
    ("bytefind-one-char-ascii", "print('banana'.count('a'), 'banana'.find('a'), 'banana'.rfind('a'))"),
    ("bytefind-absent-needle", "print('abcdef'.count('z'), 'abcdef'.find('z'), 'abcdef'.rfind('z'))"),
    ("bytefind-multibyte-needle", "print('héllo é'.count('é'), 'héllo é'.find('é'), 'héllo é'.rfind('é'))"),
    ("bytefind-multibyte-haystack", "print('日本語日'.count('日'), '日本語日'.find('日'), '日本語日'.rfind('日'))"),
    ("bytefind-ascii-needle-in-multibyte", "print('aé日b'.count('a'), 'aé日b'.find('b'), 'aé日b'.rfind('b'))"),
    ("bytefind-two-char-needle", "print('banana'.count('an'), 'banana'.find('an'), 'banana'.rfind('an'))"),
    ("bytefind-overlapping", "print('aaaa'.count('aa'), 'aaaa'.count('a'))"),
    ("bytefind-nul-and-tab", r"print('a b	c'.count(' '), 'a b	c'.find('	'))"),
    ("bytefind-empty-haystack", "print(''.count('a'), ''.find('a'), ''.rfind('a'))"),
    ("bytefind-index-raises", "print('abc'.index('z'))"),
    ("bytefind-with-bounds", "print('banana'.count('a', 2), 'banana'.find('a', 2), 'banana'.rfind('a', 0, 3))"),
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


#: Case mappings lypning cannot reproduce, which must leave by the refusal
#: contract rather than answer. They are here rather than in `CASES` because
#: `CASES` asserts lypning MATCHES CPython, and a refusal is the opposite claim:
#: exit 90, one line on stderr, nothing on stdout, and the dispatcher gets the
#: real answer from CPython one spawn later.
REFUSES = [
    # `'ß'.casefold()` is `'ss'`; aliasing casefold to lowercasing answered `'ß'`
    # at exit 0, so `'ß'.casefold() == 'ss'.casefold()` was False — and caseless
    # comparison is the whole purpose of the method.
    ("casefold-sharp-s", "print('ß'.casefold())"),
    ("casefold-micro", "print('µ'.casefold())"),
    ("casefold-ligature", "print('ﬁ'.casefold())"),
    # Titlecase is not uppercase: `'ǅ'.title()` is `'ǅ'`, and `'ß'.capitalize()`
    # is `'Ss'` rather than `'SS'`.
    ("title-digraph", "print('ǅx'.title())"),
    ("capitalize-sharp-s", "print('ßx'.capitalize())"),
]


@needs_engine
@pytest.mark.parametrize("name,program", REFUSES, ids=[c[0] for c in REFUSES])
def test_refuses_rather_than_answering(name: str, program: str) -> None:
    got = engines.run(engines.LYPNING, program, timeout=30)
    assert got.refused, (
        "%s must REFUSE: lypning cannot reproduce this case mapping, so an "
        "answer here is a wrong answer at exit 0. Got exit %d, stdout %r"
        % (name, got.returncode, got.stdout)
    )
    assert got.stdout == "", (
        "%s wrote to stdout before refusing (%r) — the commit barrier is what "
        "makes the retry on the next tier safe" % (name, got.stdout)
    )
    # And the tier that gets it must actually answer, or the refusal has only
    # moved the problem.
    ref = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=30
    )
    assert ref.returncode == 0, "%s: CPython does not answer it either" % name


#: `json.loads` documents that must FAIL to decode, with CPython's exact message
#: and position. They are separate from `CASES` because both interpreters exit 1
#: with a traceback, so the assertion is on the message rather than on stdout —
#: and lypning names the exception `JSONDecodeError` where CPython's traceback
#: shows `json.decoder.JSONDecodeError`, which is the module path of a class
#: lypning does not have a module for.
JSON_ERRORS = [
    # Raw control characters are invalid inside a JSON string, and the decoder
    # ACCEPTED them: `json.loads('"a\tb"')` returned `'a\tb'` at exit 0. That is
    # the worst shape a decoder can have — a malformed document is answered, so
    # a program that should have been routed onward to get its JSONDecodeError
    # got a result instead. The bound is `< 0x20` exactly, per RFC 8259.
    ("json-tab-in-string", 'chr(9)', "Invalid control character at: line 1 column 3 (char 2)"),
    ("json-newline-in-string", 'chr(10)', "Invalid control character at: line 1 column 3 (char 2)"),
    ("json-nul-in-string", 'chr(0)', "Invalid control character at: line 1 column 3 (char 2)"),
    ("json-unit-sep-in-string", 'chr(31)', "Invalid control character at: line 1 column 3 (char 2)"),
]


@needs_engine
@pytest.mark.parametrize(
    "name,ch,message", JSON_ERRORS, ids=[c[0] for c in JSON_ERRORS]
)
def test_json_rejects_what_cpython_rejects(name: str, ch: str, message: str) -> None:
    program = "import json\nprint(repr(json.loads('\"a' + %s + 'b\"')))" % ch
    ref = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=30
    )
    assert ref.returncode == 1 and message in ref.stderr, (
        "the oracle moved: CPython no longer reports %r for %s" % (message, name)
    )
    got = engines.run(engines.LYPNING, program, timeout=30)
    assert not got.refused, (
        "%s: a refusal is safe but wrong here — CPython raises rather than "
        "answering, so lypning can and should raise too" % name
    )
    assert got.returncode == 1, (
        "%s: exit %d — the document is invalid and must not decode. stdout %r"
        % (name, got.returncode, got.stdout)
    )
    assert message in got.stderr, (
        "%s: lypning said %r, CPython says %r — the POSITION is the answer for "
        "a decode error" % (name, got.stderr.strip()[-120:], message)
    )


@needs_engine
def test_json_unterminated_string_points_at_the_opening_quote() -> None:
    """"starting at" means the quote, not where the scan gave up."""
    program = "import json\nprint(json.loads('\"abc'))"
    want = "Unterminated string starting at: line 1 column 1 (char 0)"
    ref = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=30
    )
    assert want in ref.stderr, "the oracle moved: %s" % ref.stderr.strip()[-160:]
    got = engines.run(engines.LYPNING, program, timeout=30)
    assert want in got.stderr, (
        "lypning reported %r; it used to point at char 4, the end of the scan"
        % got.stderr.strip()[-120:]
    )


#: Cases whose whole content is the message on stderr. `CASES` cannot hold them:
#: it compares stdout and the exit code, and every one of these exits 1 with
#: empty stdout on both interpreters whether the message is right or wrong.
#:
#: Each pair is (name, program, the substring CPython must produce). The oracle
#: is checked first on every run, so a CPython that changed its wording fails
#: loudly here instead of silently turning the test into a tautology.
STDERR_CASES = [
    # Unpacking has its own message for a non-iterable, and the same trap `join`
    # had: the remap must be on `make_iter` alone, or every exception raised
    # while DRAINING the sequence turns into it.
    ("unpack-non-iterable-int", "a, b = 5",
     "cannot unpack non-iterable int object"),
    ("unpack-non-iterable-none", "a, b = None",
     "cannot unpack non-iterable NoneType object"),
    ("unpack-non-iterable-float", "a, b = 1.5",
     "cannot unpack non-iterable float object"),
    ("unpack-generator-raises-through", "a, b = (1//x for x in [1, 0])",
     "ZeroDivisionError"),
    ("unpack-generator-value-error", "a, b = (int(x) for x in ['1', 'z'])",
     "invalid literal for int()"),
    # The same shape in `join`, kept beside it so the pair is visible.
    ("join-non-iterable-message-text", "print(','.join(5))",
     "can only join an iterable"),
    ("join-generator-raises-through-text",
     "print(','.join(str(1//x) for x in [1, 0]))", "ZeroDivisionError"),
]


@needs_engine
@pytest.mark.parametrize(
    "name,program,message", STDERR_CASES, ids=[c[0] for c in STDERR_CASES]
)
def test_the_message_on_stderr_matches_cpython(name, program, message) -> None:
    ref = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=30
    )
    assert ref.returncode == 1 and message in ref.stderr, (
        "the oracle moved: CPython no longer produces %r for %s — got %r"
        % (message, name, ref.stderr.strip()[-200:])
    )
    got = engines.run(engines.LYPNING, program, timeout=30)
    assert not got.refused, (
        "%s: a refusal is safe but wrong here — CPython raises rather than "
        "answering, so lypning can and should raise the same thing" % name
    )
    assert got.returncode == 1, "%s: exit %d, stdout %r" % (
        name, got.returncode, got.stdout)
    assert message in got.stderr, (
        "%s: lypning said %r, CPython says %r" % (
            name, got.stderr.strip()[-160:], message)
    )
