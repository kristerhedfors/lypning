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
    # `str.rsplit(None, maxsplit)` REFUSED and its bytes twin answered. The
    # whitespace-split rule lived in two implementations — and the bytes copy's
    # own comment said "this is the only place the rule lives". One rule, two
    # copies, and the str one was missing a case.
    ("ws-rsplit-maxsplit", "print('a b  c'.rsplit(None, 2))"),
    ("ws-rsplit-zero", "print(' a '.rsplit(None, 0))"),
    ("ws-rsplit-keeps-leading", "print('  a  b  '.rsplit(None, 1))"),
    ("ws-rsplit-long", "print('one two three four'.rsplit(None, 2))"),
    # A NON-SPACE MULTI-BYTE CHARACTER. Unifying the rule introduced a SIGABRT
    # here and a 602-program grid missed it, because every subject in that grid
    # was ASCII or whitespace: the scan advanced one BYTE at a time and sliced
    # into the middle of a character. `'café'.split()` aborted the process.
    ("ws-multibyte-nonspace", "print('caf\u00e9'.split())"),
    ("ws-multibyte-fields", "print('h\u00e9llo w\u00f6rld'.split())"),
    ("ws-multibyte-cjk", "print('\u65e5\u672c \u8a9e'.split())"),
    ("ws-multibyte-maxsplit", "print('caf\u00e9 au lait'.split(None, 1))"),
    ("ws-multibyte-rsplit", "print('\u65e5\u672c\u8a9e'.rsplit(None, 1))"),
    ("ws-astral", "print('\U0001f600 x'.split())"),
    # The two whitespace SETS still differ, which is why the rule is shared and
    # the predicate is not: str splits on these, bytes does not.
    ("ws-unicode-space-str", "print('a\xa0b'.split(), 'a\u2000b'.split(), 'a\x85b'.split())"),
    ("ws-unicode-space-bytes",
     "print(bytes([97,194,160,98]).split(), bytes([97,194,133,98]).split())"),
    # A TRAILING COMMA AFTER ONE NAME MAKES A ONE-ELEMENT TUPLE TARGET, and
    # dropping it dropped the unpacking with it — along with the ARITY CHECK.
    # `[v for v, in [(1, 2)]]` is a ValueError in CPython and answered
    # `[(1, 2)]` here, at exit 0: a program CPython stops ran to completion and
    # printed plausible wrong data. The parenthesized spelling `(v,)` and two
    # names `a, b,` were always right, which is what kept it quiet. It reached
    # statement for-loops and all four comprehension forms.
    ("target-one-tuple-list", "print([v for v, in [(1,)]])"),
    ("target-one-tuple-arity", "try:\n    [v for v, in [(1, 2)]]\nexcept ValueError as e:\n    print(e)"),
    ("target-one-tuple-stmt", "for v, in [(5,)]:\n    print(v)"),
    ("target-one-tuple-dict", "print({v: 1 for v, in [(8,)]})"),
    ("target-one-tuple-gen", "print(list(v for v, in [(4,)]))"),
    ("target-parenthesised-still-ok", "print([v for (v,) in [(9,)]], [a for a, b in [(1, 2)]])"),
    ("target-two-names-trailing-comma", "for a, b, in [(1, 2)]:\n    print(a, b)"),
    # A RANGE HOLDS INTEGERS, BUT `in` ASKS ABOUT VALUES. `1.0 == 1` and
    # `True == 1`, so both are in range(5); matching only Value::Int answered
    # False to both at exit 0. A non-integral float is still False.
    ("range-in-float", "print(1.0 in range(5), 1.5 in range(5))"),
    ("range-in-bool", "print(True in range(5), False in range(5))"),
    ("range-in-str", "print('a' in range(5))"),
    # TWO RANGES ARE EQUAL WHEN THEY DESCRIBE THE SAME SEQUENCE, not when their
    # three fields match: two empty ranges are equal whatever their bounds, and
    # a one-element range's step is not observable.
    ("range-eq-empty", "print(range(0) == range(1, 1))"),
    ("range-eq-single", "print(range(1) == range(0, 1, 2))"),
    ("range-eq-normalised", "print(range(0, 10, 2) == range(0, 9, 2), range(3) == range(4))"),
    # ...and a range is HASHABLE, keyed on that same normalised form so equal
    # ranges collapse. This was `TypeError: unhashable type: 'range'` at exit 1.
    ("range-hashable", "print({range(2)}, len({range(0), range(1, 1)}), len({range(2), range(3)}))"),
    ("range-dict-key", "d = {range(2): 'a'}\nprint(d[range(0, 2)])"),
    # `.start`, `.stop` and `.step` are ordinary attributes CPython exposes and
    # this raised AttributeError for them, at exit 1 — the program's own exit,
    # which the chain does not retry.
    ("range-attrs", "print(range(5).start, range(5).stop, range(5).step)"),
    ("range-attrs-stepped", "r = range(2, 9, 3)\nprint(r.start, r.stop, r.step)"),
    # An attribute a range really does not have still matches CPython.
    ("range-attr-absent", "print(range(5).nosuch)"),
    # The other side of the line, which docs/HILLCLIMB.md iteration 14 drew on
    # purpose: a SyntaxError is TERMINAL, so syntax that cannot be a Python
    # program keeps exiting 1 rather than spending a spawn to be told by CPython
    # what lypning already knew.
    ("syntax-really-invalid", "print(1 +"),
    ("syntax-impossible-byte", "print($p)"),
    # ...and `**` in a dict display is dict MERGING, which works and must not be
    # swept up with the `*` set-unpacking form beside it.
    ("dict-merge-display", "d = {'a': 1}\nprint({**d, 'b': 2})"),
    # The bytes methods were written as a second copy of the str ones, and the
    # copy drifted in five places. A 1,938-program grid over six subjects, 17
    # argument shapes and 17 methods found all five; the str originals are
    # right in every one of them.
    #
    # 1. A message suffix nothing in CPython prints. 162 divergences: every
    #    bytes TypeError read "a bytes-like object is required, not 'str' (in
    #    bytes.split())", and str(e) is what a program prints.
    ("bytes-msg-split", "try:\n    b'a'.split(1)\nexcept TypeError as e:\n    print(e)"),
    ("bytes-msg-strip", "try:\n    b'a'.strip('x')\nexcept TypeError as e:\n    print(e)"),
    ("bytes-msg-replace", "try:\n    b'a'.replace(1, b'x')\nexcept TypeError as e:\n    print(e)"),
    ("bytes-msg-contains", "try:\n    'a' in b'abc'\nexcept TypeError as e:\n    print(e)"),
    # 2. find/rfind/index/rindex/count have their OWN wording, because those
    #    five also accept a single integer byte value.
    ("bytes-msg-find", "try:\n    b'a'.find('a')\nexcept TypeError as e:\n    print(e)"),
    ("bytes-msg-find-none", "try:\n    b'a'.find(None)\nexcept TypeError as e:\n    print(e)"),
    # `bytes.count`, `.index`, `.partition` and friends are not implemented and
    # REFUSE, which is never a defect — a coverage number, and the reason this
    # grid reports 816 refusals against 1,938 programs.
    # 3. startswith/endswith take A TUPLE OF PREFIXES — the point of the method,
    #    and not accepted at all. str.startswith has taken one since it was
    #    written. Their first-argument message is CPython's own, with the type
    #    name UNQUOTED; a bad tuple ELEMENT falls back to the shared message.
    ("bytes-startswith-tuple", "print(b'abc'.startswith((b'x', b'a')), b'abc'.endswith((b'c',)))"),
    ("bytes-startswith-empty-tuple", "print(b'abc'.startswith(()))"),
    ("bytes-msg-startswith", "try:\n    b'a'.startswith(1)\nexcept TypeError as e:\n    print(e)"),
    ("bytes-msg-startswith-elem", "try:\n    b'a'.startswith((97,))\nexcept TypeError as e:\n    print(e)"),
    # 4. join names the item's INDEX and type, and a non-iterable argument is
    #    "can only join an iterable" — which str.join says right beside it.
    ("bytes-msg-join-item", "try:\n    b''.join([b'a', 1])\nexcept TypeError as e:\n    print(e)"),
    ("bytes-msg-join-noniter", "try:\n    b''.join(1)\nexcept TypeError as e:\n    print(e)"),
    # 5. Two answers, not messages. An empty separator is a ValueError, not a
    #    one-element list; and `in` truncated with `as u8`, so `300 in b'abc'`
    #    tested byte 44 and answered False where CPython raises.
    ("bytes-split-empty-sep", "try:\n    b'abc'.split(b'')\nexcept ValueError as e:\n    print(e)"),
    ("bytes-in-out-of-range", "try:\n    300 in b'abc'\nexcept ValueError as e:\n    print(e)"),
    ("bytes-in-negative", "try:\n    -1 in b'abc'\nexcept ValueError as e:\n    print(e)"),
    ("bytes-in-ok", "print(1 in b'abc', 97 in b'abc', True in b'abc')"),
    # A KEYWORD SILENTLY REFILLED A PARAMETER THE POSITIONALS HAD FILLED. The
    # binder looked the name up and inserted over the top, so `f(1, a=2)` ran
    # with a=2 where CPython raises, and `f(1, 2, a=9)` ran with a=9 AND b=2 —
    # a function executing on data the caller never passed together, at exit 0.
    ("dup-arg-single", "def f(a):\n    return a\ntry:\n    print(f(1, a=2))\nexcept TypeError as e:\n    print(e)"),
    ("dup-arg-two", "def f(a, b):\n    return (a, b)\ntry:\n    print(f(1, 2, a=9))\nexcept TypeError as e:\n    print(e)"),
    ("dup-arg-default", "def f(a, b=5):\n    return (a, b)\ntry:\n    print(f(1, 2, b=9))\nexcept TypeError as e:\n    print(e)"),
    ("dup-arg-star", "def f(a, *b):\n    return (a, b)\ntry:\n    print(f(1, 2, a=9))\nexcept TypeError as e:\n    print(e)"),
    # ...and every legal way of filling the same parameters still works, because
    # the check is on the USED bit and not on the name appearing twice.
    ("bind-ok-defaults", "def f(a, b=2):\n    return (a, b)\nprint(f(1), f(1, 3), f(a=1), f(1, b=9), f(b=9, a=1))"),
    ("bind-ok-varargs", "def f(a, *b, **k):\n    return (a, b, k)\nprint(f(1, 2, 3, x=4))"),
    ("bind-ok-unpacked", "def f(a):\n    return a\nprint(f(*[5]), f(**{'a': 6}))"),
    # REVERSING AN ITERATOR IS NOT A THING. CPython needs __reversed__, or
    # __len__ and __getitem__ together, so a sequence reverses and a one-pass
    # iterator raises — the rarer divergence, where the engine SUCCEEDS and
    # CPython refuses. A set is not reversible either, and refusing it as a
    # set-order exposure was a spawn spent on nothing: CPython never gets far
    # enough to iterate, so this message is exact.
    ("reversed-set", "try:\n    list(reversed({1, 2}))\nexcept TypeError as e:\n    print(e)"),
    ("reversed-int", "try:\n    list(reversed(1))\nexcept TypeError as e:\n    print(e)"),
    ("reversed-seqs", "print(list(reversed([1,2])), list(reversed((1,2))), list(reversed('ab')))"),
    ("reversed-bytes", "print(list(reversed(b'ab')), list(reversed(range(3))))"),
    ("reversed-dict", "print(list(reversed({'a':1})), list(reversed({'a':1}.items())))"),
    # A 5,460-program grid over every ordering operator across 26 operand
    # values: 1,611 divergences in three shapes, all at exit 0.
    #
    # 1,461 of them were THE OPERATOR'S OWN NAME in the TypeError. Every
    # ordering comparison derived its answer from one `Ordering`, so every one
    # of them reported `'<'`. CPython names the operator you wrote, at every
    # depth — a sequence compares element-wise and hands the ORIGINAL operator
    # to the first differing pair — and `str(e)` is printed by a great many
    # corpus programs.
    ("cmp-typeerror-le", "try:\n    0 <= ''\nexcept TypeError as e:\n    print(e)"),
    ("cmp-typeerror-ge", "try:\n    0 >= ''\nexcept TypeError as e:\n    print(e)"),
    ("cmp-typeerror-gt", "try:\n    0 > ''\nexcept TypeError as e:\n    print(e)"),
    ("cmp-typeerror-nested-le", "try:\n    [1] <= ['a']\nexcept TypeError as e:\n    print(e)"),
    ("cmp-typeerror-nested-gt", "try:\n    (1,) > ('a',)\nexcept TypeError as e:\n    print(e)"),
    # ...and sort still says `'<'`, because that is the comparison sort makes.
    ("sort-typeerror-stays-lt", "try:\n    sorted([1, 'a'])\nexcept TypeError as e:\n    print(e)"),
    # 120 more were a NaN SHORT-CIRCUIT THAT RAN BEFORE THE TYPE CHECK. An
    # ordering over a NaN is False because IEEE 754 says the relation does not
    # hold — but only between values that have an ordering to fail. A str and a
    # float do not, and CPython raises there.
    ("nan-order-number", "print(float('nan') < 1, float('nan') >= float('nan'))"),
    ("nan-order-typeerror", "try:\n    '' < float('nan')\nexcept TypeError as e:\n    print(e)"),
    ("nan-order-nested", "print([float('nan')] < [1.0])"),
    # The same rule reached sort, min and max, where raising "cannot order NaN"
    # turned three values CPython computes into exceptions. A NaN compares False
    # to everything, and sort only ever asks `b < a`, so the answer is "do not
    # reorder".
    # `min` and `max` stay here and are genuinely correct: they are linear scans
    # asking one question per element, so "neither less nor greater" gives
    # CPython's answer exactly. `sorted` does NOT — see nan-order below.
    ("range-huge-len", "try:\n    len(range(-2**62, 2**62))\nexcept OverflowError as e:\n    print(e)"),
    ("range-ordinary", "print(range(10)[2:8:3], list(range(10)[::3]), len(range(10)), range(10)[::-1])"),
    ("nan-max", "print(max(float('nan'), 1.0))"),
    ("nan-min", "print(min(float('nan'), 1.0))"),
    ("nan-max-mid", "print(max([3, 1, float('nan'), 2]))"),
    ("nan-min-mid", "print(min([3, 1, float('nan'), 2]))"),
    # A 390-program grid over the float floor-division OVERFLOW neighbourhood:
    # 98 divergences, every one of them this, every one at exit 0. `//` guarded
    # on `(x / y).is_finite()` and answered nan when it was not — right for
    # `inf // 2.5`, which IS nan, and wrong for a finite pair whose quotient
    # merely overflows, which CPython answers with an infinity. CPython never
    # looks at the quotient: it takes `fmod` first, and the two cases separate
    # there, because `fmod(inf, y)` is nan and `fmod(7.0, 1e-308)` is an
    # ordinary small number. The guard could not have been found by testing the
    # infinity it was written for.
    ("floordiv-overflow", "print(7.0 // 1e-308)"),
    ("floordiv-overflow-neg", "print(7.0 // -1e-308)"),
    ("floordiv-overflow-subnormal", "print(7.0 // 5e-324)"),
    ("floordiv-overflow-divmod", "print(divmod(7.0, 1e-308))"),
    ("floordiv-overflow-wide", "print(1e308 // 1e-308)"),
    # The cases the guard existed for, which must still hold: an INFINITE
    # DIVIDEND has no floor and stays nan, and a finite one over an infinity
    # floors to 0.0 or -1.0 by sign.
    ("floordiv-inf-dividend", "print(float('inf') // 2.5)"),
    ("floordiv-inf-dividend-neg", "print(float('-inf') // 2.5)"),
    ("floordiv-inf-divisor", "print(2.5 // float('inf'))"),
    ("floordiv-inf-divisor-neg", "print(-2.5 // float('inf'))"),
    ("floordiv-inf-both", "print(float('inf') // float('inf'))"),
    # The two corrections this expression already carried, re-pinned beside the
    # new one because all three share one line of code: the exact quotient
    # derived from fmod, and the sign of a zero result.
    ("floordiv-exact-quotient", "print(1e16 // -3.0)"),
    ("floordiv-half-correction", "print(9.0 // 0.7)"),
    ("floordiv-negative-zero", "print(repr(-0.0 // 1.0), repr(0.0 // -1.0))"),
    # An attribute CPython does not have either: still AttributeError, still
    # exit 1, still agreeing with CPython. The dunder refusal must not swallow
    # this one — a refusal here would be a spawn spent on nothing.
    ("attr-really-absent", "print((2).nosuchthing)"),
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
    ("bytefind-nul-and-tab", "print('a\\x00b\\tc'.count('\\x00'), 'a\\x00b\\tc'.find('\\t'))"),
    ("bytefind-empty-haystack", "print(''.count('a'), ''.find('a'), ''.rfind('a'))"),
    ("bytefind-index-raises", "print('abc'.index('z'))"),
    ("bytefind-with-bounds", "print('banana'.count('a', 2), 'banana'.find('a', 2), 'banana'.rfind('a', 0, 3))"),
    # `reverse=True` was a post-pass `idx.reverse()` over a finished stable sort,
    # which reverses the ties along with everything else. Python's sort is stable
    # descending too, so `sorted(counts, key=counts.get, reverse=True)` — the most
    # ordinary "top N by frequency" line an agent writes — silently answered with
    # tied keys in the wrong order, at exit 0. CPython reverses the input, sorts
    # ascending, and reverses again.
    ("sort-reverse-ties-dict", 'c = {"b": 2, "a": 2, "c": 1}\nprint(sorted(c, key=lambda k: c[k], reverse=True))'),
    ("sort-reverse-ties-pairs", 'print(sorted([("a", 1), ("b", 1), ("c", 2)], key=lambda t: t[1], reverse=True))'),
    ("sort-reverse-ties-len", 'print(sorted(["bb", "aa", "c"], key=len, reverse=True))'),
    ("sort-reverse-ties-inplace", 'L = [("a", 1), ("b", 1), ("c", 1), ("d", 0)]\nL.sort(key=lambda t: t[1], reverse=True)\nprint(L)'),
    ("sort-reverse-no-ties", "print(sorted([5, 3, 1, 4], reverse=True))"),
    ("sort-reverse-degenerate", "print(sorted([], reverse=True), sorted([1], reverse=True))"),
    # `key=None` is the DEFAULT, not a callable. It is also how an optional key
    # gets spelled — `sorted(xs, key=chooser)` where `chooser` may be None — and
    # reading it as a value to call raised TypeError at exit 1. Exit 1 is the
    # program's own, so the dispatcher does not fall through and the caller got
    # that error instead of the answer, for valid Python.
    ("sort-key-none", "print(sorted([3, 1, 2], key=None))"),
    ("sort-key-none-inplace", "x = [3, 1, 2]\nx.sort(key=None)\nprint(x)"),
    ("sort-key-none-reverse", "print(sorted([3, 1, 2], key=None, reverse=True))"),
    ("minmax-key-none", "print(min([3, 1, 2], key=None), max([3, 1, 2], key=None))"),
    ("minmax-key-none-default", "print(min([1, 2], key=None, default=9))"),
    # The other direction, and the worse one: `reverse=` goes through __index__
    # in CPython, so a non-integer is a TypeError. Read for truthiness it was an
    # ascending sort at exit 0 — a wrong answer where an error was owed.
    ("sort-reverse-int", "print(sorted([3, 1, 2], reverse=1), sorted([3, 1, 2], reverse=0))"),
    # `bytes.rsplit` was `split` wearing a different name: `from_right` reached
    # the splitter and was discarded.
    ("bytes-rsplit-direction", 'print(b"path/to/file.txt".rsplit(b"/", 1))'),
    ("bytes-rsplit-ws", 'print(b"x y  z".rsplit(None, 1))'),
    ("bytes-rsplit-unbounded", 'print(b"a/b/c".rsplit(b"/"))'),
    # Rust's ASCII whitespace omits \x0b, which Python counts. One byte value,
    # and it changed the answer of four methods at exit 0.
    ("bytes-vertical-tab-strip", r'print(b"\x0b\x0chi\x0b".strip())'),
    ("bytes-vertical-tab-split", r'print(b"a\x0bb".split())'),
    # Whitespace splitting hands back the REMAINDER once maxsplit is spent, and
    # never emits a leading empty field.
    ("bytes-split-maxsplit-remainder", 'print(b"x y  z".split(None, 1))'),
    ("bytes-split-maxsplit-zero", 'print(b" a ".split(None, 0))'),
    # start/end were accepted and thrown away by three bytes methods.
    ("bytes-find-start", 'print(b"abcabc".find(b"a", 1))'),
    ("bytes-find-end", 'print(b"abcabc".find(b"c", 0, 2))'),
    ("bytes-startswith-start", 'print(b"abcabc".startswith(b"b", 1))'),
    ("bytes-endswith-bounds", 'print(b"abcabc".endswith(b"b", 0, 2))'),
    ("bytes-hex-sep", 'print(b"abcd".hex("-"), b"abcd".hex("-", 2), b"abcd".hex("-", -2))'),
    # `(x - mod) / y` is exact in real arithmetic and not in floating point, so
    # CPython corrects the floor when the discarded fraction was over a half.
    # This answered 11.0.
    ("float-floordiv-correction", "print(9.0 // 0.7)"),
    ("float-floordiv-exact", "print(1e16 // -3.0, -0.0 // 1.0, -7.0 // 2.0)"),
    # bool overrides the three bitwise operators to return bool — and ONLY when
    # both operands are bool. The shifts are not overridden.
    ("bool-bitwise", "print(True | False, True & True, True ^ True)"),
    ("bool-bitwise-mixed-stays-int", "print(True | 1, 1 | True, False | 0)"),
    ("bool-shift-stays-int", "print(True << 1, True >> 1, ~True)"),
    # list.index / tuple.index took start and stop and ignored them.
    ("list-index-start", "print([1, 1, 1].index(1, 1))"),
    ("list-index-negative-start", "print([1, 2, 1].index(1, -2))"),
    ("tuple-index-start", "print((1, 2, 1).index(1, 1))"),
    # The other side of the set-order guard: no tie, so there IS one answer and
    # it must still be given. A blanket refusal would pass the REFUSES pins and
    # fail here.
    ("set-key-no-tie-still-answers", 'print(max({1, 2, 3}, key=lambda v: v), sorted({"a", "bb"}, key=len))'),
    ("list-key-ties-are-fine", "print(max([-1, 1], key=abs))"),
    # `bool` is a subclass of `int`, so `True in b"ab"` is the same byte-value
    # test as `1 in b"ab"`. Matching only `Value::Int` raised "a bytes-like
    # object is required" — the same subclass slip already pinned for
    # `bytes.find(False)`, fixed there and not in `in`.
    ("bool-in-bytes", 'print(True in b"ab", False in b"ab", 97 in b"ab")'),
    # CPython converts an unhashable set to a frozenset for the membership test
    # rather than raising: `{1} in {1}` is False, not a TypeError.
    ("set-in-set", "print({1} in {1}, {1} in {2}, 1 in {1})"),
    # The leftover-argument check is skipped for anything CPython calls a
    # mapping — dict, list and bytes all subscript — and fires for int, str and
    # tuple. Only dict was exempt here.
    ("percent-list-operand", "print(repr('ab' % [1]), repr('ab' % b'ab'), repr('ab' % {}))"),
    ("percent-leftover-still-raises", "print(repr('a%sb' % [1]))"),
    # Mutating a dict while iterating it raises RuntimeError. The guard existed
    # for a bare dict and the three VIEWS never reached it — they snapshotted
    # into a plain vector and threw the dict away, so `for k in d.keys(): del
    # d[k]` walked a frozen copy, emptied the dict and answered normally.
    ("dict-mutate-during-keys",
     'g = {"a": 1, "b": 2}\ntry:\n    for k in g.keys():\n        del g[k]\n    print("no-error")\nexcept RuntimeError:\n    print("RuntimeError")'),
    ("dict-mutate-during-values",
     'g = {"a": 1}\ntry:\n    for v in g.values():\n        g["c"] = 3\n    print("no-error")\nexcept RuntimeError:\n    print("RuntimeError")'),
    ("dict-mutate-during-items",
     'g = {"a": 1, "b": 2}\ntry:\n    for k, v in g.items():\n        del g[k]\n    print("no-error")\nexcept RuntimeError:\n    print("RuntimeError")'),
    ("dict-mutate-bare", 'g = {"a": 1, "b": 2}\ntry:\n    for k in g:\n        del g[k]\n    print("no-error")\nexcept RuntimeError:\n    print("RuntimeError")'),
    # …and the guard must not fire on the ordinary uses.
    ("dict-views-still-iterate", 'g = {"a": 1, "b": 2}\nprint([k for k in g.keys()], list(g.values()), sorted(g.items()))'),
    ("dict-mutate-over-a-copy-is-fine", 'g = {"a": 1}\nfor k in list(g.keys()):\n    del g[k]\nprint(g)'),
    # An exception instance reports ITS OWN class, not the base. Every message
    # naming the type named "Exception" for all twenty-four of them.
    ("exception-type-name-in-attr-error",
     'try:\n    raise ValueError("v")\nexcept ValueError as e:\n    print(e.nosuch)'),
    ("exception-type-name-in-operand-error",
     'try:\n    raise KeyError("k")\nexcept KeyError as e:\n    print(e + 1)'),
    ("exception-args-still-work", 'try:\n    raise ValueError("v")\nexcept ValueError as e:\n    print(e.args, str(e))'),
    # `str(KeyError('f'))` shows the REPR of the key, so a missing `''` is
    # distinguishable from a missing `' '`. The constructor stored the plain
    # string while every real lookup stored `repr(key)`, so the two disagreed —
    # and `repr()` then quoted the lookup form a second time.
    ("keyerror-str-quotes", 'print(KeyError("f"), repr(KeyError("f")))'),
    ("keyerror-int-key", "print(str(KeyError(1)), repr(KeyError(1)))"),
    ("keyerror-from-lookup", 'try:\n    {}["k"]\nexcept KeyError as e:\n    print(str(e), repr(e))'),
    ("keyerror-empty", "print(repr(KeyError()), repr(ValueError()))"),
    ("other-exceptions-unquoted", 'print(str(ValueError("v")), repr(ValueError("v")))'),
    # A bare `raise` in a handler re-raises what that handler caught, and a
    # nested try/except inside the handler must not lose it.
    ("bare-raise-nested-keeps-outer",
     'try:\n    try:\n        raise KeyError("k")\n    except KeyError:\n        try:\n            raise TypeError("t")\n        except TypeError:\n            pass\n        raise\nexcept KeyError as e:\n    print("outer kept", e)'),
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
    # `range.index` and `range.count` are real CPython methods this engine does
    # not implement. They answered AttributeError at exit 1 — unroutable —
    # where every other unimplemented method refuses.
    ("range-method-index", "print(range(5).index(1))"),
    ("range-method-count", "print(range(5).count(1))"),
    # VALID PYTHON THE PARSER DOES NOT KNOW IS A CAPABILITY GAP, NOT A SYNTAX
    # ERROR, and the difference is the exit code. These four exited 1 with a
    # SyntaxError — the PROGRAM's own exit, which the chain does not retry — so
    # a program CPython runs fine simply died. `async`, `kwonly` and `nonlocal`
    # were already refused by name; these were not.
    ("syntax-walrus", "print((n := 1))"),
    ("syntax-walrus-if", "n = 0\nif (n := 5) > 3:\n    print(n)"),
    ("syntax-list-unpack", "print([*[1, 2], 3])"),
    ("syntax-set-unpack", "print({*{1, 2}, 3})"),
    ("syntax-tuple-slice-subscript",
     "x = [1]\ntry:\n    x[0:1, 2]\nexcept TypeError as e:\n    print(e)"),
    # A CORRECTION, made the same day. Earlier this session `order` was changed
    # to treat a NaN as "neither less nor greater" instead of raising, and
    # `sorted([nan, 1.0])` was pinned as matching CPython. It did — for TWO
    # elements, which is one comparison, where any consistent comparator agrees.
    # It does not in general:
    #
    #     sorted([3, 1, float('nan'), 2])
    #     CPython [1, 2, 3, nan]        this merge sort [1, 3, nan, 2]
    #
    # Every comparison against a NaN is false, so "not less" holds in BOTH
    # directions and the comparator stops being an order. Which element moves
    # then depends entirely on the sequence of questions the algorithm asks, and
    # CPython's answer is timsort's. No fix to the comparison can close that, so
    # the sort refuses. Answering wrongly at exit 0 is worse than the TypeError
    # it used to raise, which is why this could not be left.
    ("nan-order-sorted", "print(sorted([3, 1, float('nan'), 2]))"),
    ("nan-order-two", "print(sorted([float('nan'), 1.0]))"),
    ("nan-order-method", "x = [3, 1, float('nan')]\nx.sort()\nprint(x)"),
    # A RANGE CAN BE LONGER THAN i64 CAN COUNT, and the length was computed in
    # i64: `range(-2**62, 2**62)` has 2**63 elements, the subtraction wrapped to
    # i64::MIN, and `slice_span`'s `clamp(0, n)` PANICKED on min > max. Exit 134,
    # a SIGABRT — the one outcome the dispatcher cannot route onward, and one
    # that aborts an embedding host's process outright.
    ("range-huge-slice", "print(range(-2**62, 2**62)[:1])"),
    ("range-huge-index", "print(range(-2**62, 2**62)[0])"),
    ("range-huge-reversed", "print(range(-2**62, 2**62)[::-1][:1])"),
    # ...and the arithmetic that builds the sliced range overflows too: this is
    # `range(0, 4, 9223372036854775808)` in CPython, and `st * step` wrapped to
    # a NEGATIVE step, so `list(...)` answered [] where CPython answers [0].
    ("range-step-overflow", "print(list(range(0, 4, 2)[::2**62]))"),
    # `def f(a, *c, d)` is the same feature as `def f(a, *, d)`, which is
    # refused. This spelling fell through and recorded `d` as an ordinary
    # positional parameter, which the binder cannot represent: it computes the
    # positional count as `names.len() - star - dstar` and then slices
    # `names[..npos]` from the FRONT, which is only right while `*args` and
    # `**kw` come last. `f(1, 2, d=3)` said "unexpected keyword 'd'" and
    # `f(1, 2, 3)` raised UnboundLocalError — neither a refusal, so neither
    # could be answered one spawn later.
    ("kwonly-after-star", "def f(a, *c, d):\n    return (a, c, d)\nprint(f(1, 2, d=3))"),
    ("kwonly-after-star-full", "def f(a, b=2, *c, d, e=5, **k):\n    return (a, b, c, d, e, k)\nprint(f(1, 2, 3, d=4))"),
    # An iterator's type name is one CPython spells from a family this engine
    # does not distinguish (`list_iterator`, `tuple_iterator`, and
    # `str_ascii_iterator`, which is `str_iterator` for a non-ASCII string).
    # Any message that would name one refuses, for the same reason `repr()` of
    # an iterator refuses: the answer contains something not reproducible.
    ("reversed-iterator",
     "try:\n    list(reversed(iter([1, 2])))\nexcept TypeError as e:\n    print(e)"),
    ("len-iterator",
     "try:\n    len(iter([1, 2]))\nexcept TypeError as e:\n    print(e)"),
    # The last 30 of that grid, and the one that cannot be fixed by computing
    # harder. `is` is object identity, and for an immutable value CPython
    # answers it from INTERNING: `0 is 0` and `'ab' is 'ab'` are True because
    # the compiler folded the two constants into one object, while
    # `int('1000') is 1000` is False for the same values. The answer depends on
    # where the value came from, which nothing in a value can tell you — so
    # answering either way is wrong for the other half.
    ("is-equal-ints", "print(0 is 0)"),
    ("is-equal-strs", "print('ab' is 'ab')"),
    ("is-equal-tuples", "print((1,) is (1,))"),
    ("is-not-equal-floats", "print(2.5 is not 2.5)"),
    # A DUNDER IS PART OF THE DATA MODEL, so `AttributeError` for one is not a
    # fact about this program — it is a claim about Python, and a false one.
    # `type(2).__name__` is `int` in CPython and was an AttributeError here.
    # Three measured MISMATCHes on the tier-1 arm, and worse than the count:
    # AttributeError is exit 1, the PROGRAM's own exit, which the chain does not
    # retry. Unlike a refusal, none of them could be answered one spawn later.
    # The rule is a wildcard over `__x__` rather than a list of the dunders
    # CPython has, because a list is incomplete the moment someone uses the next
    # one — and incomplete here means a silent wrong answer, where over-broad
    # means a spawn.
    ("dunder-name", "print(type(2).__name__)"),
    ("dunder-class", "try:\n    1/0\nexcept Exception as e:\n    print(e.__class__.__name__)"),
    ("dunder-doc", "print(len.__doc__ is not None)"),
    # `(2).__dict__` is deliberately NOT here: an int has no `__dict__` in
    # CPython either, so refusing it is the over-broad half of the wildcard —
    # one spawn, after which CPython raises the same AttributeError the program
    # would have got anyway. That is the cost side of the trade, and it is the
    # side worth paying.
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
    # A set has no first element, so "ties keep the first" has nothing to keep.
    # `max({-1, 1}, key=abs)` answered -1 where CPython answers 1 — same set,
    # same key, different iteration order. Refused only when a tie ACTUALLY
    # occurs: `max(s, key=len)` over distinct lengths still answers, and is
    # pinned in CASES above so this cannot quietly widen into a blanket refusal.
    ("set-order-max-key-ties", "print(max({-1, 1}, key=abs))"),
    ("set-order-min-key-ties", "print(min({-1, 1}, key=abs))"),
    ("set-order-sorted-key-ties", "print(sorted({-1, 1}, key=abs))"),
    # `strict=` needs a length check the lazy zip does not make. A refusal the
    # dispatcher routes onward beats an approximation (invariant 1); what it
    # must never do is what it used to — accept the flag and drop the guard.
    # Equal lengths on purpose: this table requires that CPython ANSWERS the
    # program, so the refusal is shown to be about the keyword rather than about
    # the mismatch it guards. The unequal case is a ValueError on both tiers.
    ("zip-strict", "print(list(zip([1, 2], [3, 4], strict=True)))"),
    # `bytes % args` is real Python (PEP 461) and is not implemented here.
    # Falling through to the generic binary-op arm made it a TypeError — the
    # program's own exit, which the dispatcher does not treat as a refusal, so
    # `b"%d" % 5` died at exit 1 with nothing to rescue it.
    ("bytes-percent-format", 'print(b"%d" % 5)'),
    # `__context__`, `__cause__` and `__traceback__` really do exist on a
    # CPython exception, so answering "no such attribute" is a claim about
    # Python rather than about the program. `Value::Exc` is a flat
    # `(kind, message)` pair with nowhere to hold a chained exception, and
    # AttributeError is exit 1 — the program's own — so a handler that inspects
    # the context died here instead of being answered one spawn later.
    ("exception-context", 'try:\n    raise ValueError("v")\nexcept ValueError as e:\n    print(e.__context__)'),
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
    # `reverse=` is read through __index__, so the message names an integer and
    # not a bool. Pinned as text because the stdout pin above cannot tell this
    # error from any other TypeError.
    ("sort-reverse-none-message", "print(sorted([3, 1, 2], reverse=None))",
     "'NoneType' object cannot be interpreted as an integer"),
    # `list.index` with a range that excludes the element must RAISE, not find
    # it anyway. Pinned as text because the stdout pin cannot tell this
    # ValueError from a different one.
    ("list-index-stop-excludes", "print([1, 2, 3].index(3, 0, 2))", "3 is not in list"),
    # `enumerate` is exempt from the no-keywords table because `start` is real,
    # and the exemption used to mean no validation at all.
    ("enumerate-bad-keyword", "print(list(enumerate([1], strict=True)))",
     "'strict' is an invalid keyword argument"),
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
