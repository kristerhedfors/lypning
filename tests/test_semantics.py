"""Where lypning has to agree with CPython, pinned one program at a time.

Every case here is a divergence `lypning fuzz` (or a sweep around one of its
counterexamples) actually found: a program both interpreters ran to exit 0 while
printing different answers. That is the failure mode this project exists to
prevent — a refusal is loud and one spawn later CPython answers, but a wrong
answer at exit 0 is read as the truth — so each one gets a line here rather than
only a fuzzer seed, because a seed is a lottery ticket and this is a test.

The assertion is differential, not literal: the program runs on the Rust core
and on the real CPython and the two outputs must be equal. Nothing here encodes
what the answer *is*, so a case cannot rot into pinning our own bug.
"""

from __future__ import annotations

import pytest

from lypning import UNSUPPORTED_EXIT, engines

#: ``(id, program)``. Each prints everything it compares, so a failure names the
#: value rather than just the case.
CASES = [
    (
        # The "too many positional arguments" message varies three ways and all
        # three were wrong: the count reported was where the binder stopped
        # rather than how many were given; a function with defaults must say
        # `from R to N`; and `argument`/`was` are singular in different cases
        # (`takes 1 positional argument`, but `takes from 0 to 1 positional
        # arguments`). It reaches stdout only through `print(e)`, which is why
        # the conformance corpus never caught it.
        "arity-typeerror-wording",
        "def f0():\n    pass\n"
        "def f1(a):\n    pass\n"
        "def f2(a, b):\n    pass\n"
        "def d1(a=1):\n    pass\n"
        "def d2(a, b=1):\n    pass\n"
        "def d3(a=1, b=2, c=3):\n    pass\n"
        "cases = [(f0, [1]), (f1, [1, 2]), (f1, [1, 2, 3]), (f2, [1, 2, 3]),\n"
        "         (d1, [1, 2]), (d2, [1, 2, 3]), (d2, [1, 2, 3, 4]), (d3, [1, 2, 3, 4])]\n"
        "for fn, a in cases:\n"
        "    try:\n"
        "        fn(*a)\n"
        "    except TypeError as e:\n"
        "        print(repr(str(e)))\n",
    ),
    (
        # Arguments live in the caller's stack frame up to `args::INLINE` and
        # spill to a Vec past it (`assets/rust/src/args.rs`). That boundary is
        # invisible from Python and must stay invisible: this walks every arity
        # across it, on a plain function, on `*args`, on keywords, and on
        # builtins and methods, which take three different paths to the same
        # argument list.
        "argument-count-across-the-inline-boundary",
        "def f(*a, **k):\n"
        "    return (len(a), a, sorted(k.items()))\n"
        "for n in range(0, 10):\n"
        "    print(n, f(*list(range(n))))\n"
        "def g(a=0, b=1, c=2, d=3, e=4, h=5, i=6, j=7, k=8):\n"
        "    return (a, b, c, d, e, h, i, j, k)\n"
        "print(g())\n"
        "print(g(9))\n"
        "print(g(9, 8, 7, 6, 5))\n"
        "print(g(9, 8, 7, 6, 5, 4, 3, 2, 1))\n"
        "print(g(a=1, k=2))\n"
        "print(g(*[1, 2, 3, 4, 5, 6], **{'i': 7}))\n"
        "print(max(1, 2, 3, 4, 5, 6, 7), min(9, 8, 7, 6, 5, 4, 3))\n"
        "print('a,b,c,d,e,f,g'.split(',', 5))\n"
        "print('%s-%s-%s-%s-%s-%s' % (1, 2, 3, 4, 5, 6))\n",
    ),
    (
        # `IOError` is not a subclass of `OSError` in CPython, it is the same
        # class under a second name — so it has to match in both directions.
        # lypning had it only as a CLAUSE that catches an OSError, not as a KIND
        # an `except OSError` catches, and the asymmetry is why it read as
        # working: raising OSError and catching IOError was fine, and the
        # reverse escaped the handler and exited 1 with a traceback.
        "ioerror-is-oserror-in-both-directions",
        "for raiser, catcher in [('IOError', 'OSError'), ('OSError', 'IOError'),\n"
        "                        ('IOError', 'IOError'), ('OSError', 'OSError'),\n"
        "                        ('IOError', 'Exception'), ('FileNotFoundError', 'IOError')]:\n"
        "    print(raiser, catcher)\n"
        "try:\n"
        "    raise IOError('boom')\n"
        "except OSError as e:\n"
        "    print('OSError caught', e)\n"
        "try:\n"
        "    raise OSError('boom')\n"
        "except IOError as e:\n"
        "    print('IOError caught', e)\n"
        "try:\n"
        "    raise IOError('boom')\n"
        "except Exception as e:\n"
        "    print('Exception caught', e)\n"
        "try:\n"
        "    raise FileNotFoundError('boom')\n"
        "except IOError as e:\n"
        "    print('IOError caught FNF', e)\n",
    ),
    (
        "partition-empty-separator",
        "def t(f):\n"
        "    try:\n"
        "        print(f())\n"
        "    except ValueError as e:\n"
        "        print('ValueError:', e)\n"
        "t(lambda: ''.partition(''))\n"
        "t(lambda: 'abc'.rpartition(''))\n",
    ),
    (
        "round-keeps-the-sign-of-zero",
        "print(round(-0.5, 0), round(0.5, 0), round(-0.4, 0), round(-1.5, 0))\n",
    ),
    (
        "nonfinite-gets-a-sign-slot",
        "inf = float('inf')\n"
        "for s in ['+', ' ', '010', '+010', '+f', '+e', '+g']:\n"
        "    print(s, format(inf, s), format(-inf, s), format(float('nan'), s))\n",
    ),
    (
        "numbers-right-align-with-no-presentation-type",
        "print(repr(format(7, '10')), repr(format(1.5, '10')), repr(format('x', '10')))\n"
        "print(repr(f'{1.5:10}{7:6}'))\n",
    ),
    (
        "grouping-stops-at-the-exponent",
        "for v in [1e17, 1e-7, 123456.0, 12345678.5]:\n"
        "    print(repr(format(v, '_')), repr(format(v, ',')))\n",
    ),
    (
        "case-predicates-test-cased-characters",
        "for s in ['\\u65e5\\u672c', 'abc', 'ABC', '\\u65e5\\u672ca', '\\u01c5', '\\u01c5abc', '123']:\n"
        "    print(s.islower(), s.isupper(), s.isalpha())\n",
    ),
]


@pytest.mark.parametrize("case_id,program", CASES, ids=[c[0] for c in CASES])
def test_lypning_agrees_with_cpython(case_id, program, lypning_bin):
    ours = engines.run(engines.LYPNING, program, binary=lypning_bin)
    if ours.refused:
        pytest.skip("outside the subset on this build: %s" % ours.stderr.strip())
    theirs = engines.run(engines.CPYTHON, program)
    if theirs.returncode == 127:
        pytest.skip("no reference CPython")
    assert ours.returncode == theirs.returncode
    assert ours.stdout == theirs.stdout


# --- the other half of agreement: refuse, rather than answer differently -----

#: A method CPython's type HAS and lypning does not. Every one of these routed
#: to the Rust core — `route.rs` asks only whether the name is a method of ANY
#: type it knows — and died with `AttributeError` at exit 1, which is the
#: program's own failure and is passed through unchanged. The caller got a
#: traceback where CPython prints an answer, with no second tier to recover on.
MISSING_METHODS = [
    ("bytes-count", 'print(b"abc".count(b"a"))'),
    ("bytes-rpartition", 'print(b"x".rpartition(b"A"))'),
    ("str-istitle", 'print("Hello World".istitle())'),
    ("str-center", 'print("x".center(5, "-"))'),
    ("set-isdisjoint", "print({1, 2}.isdisjoint({3}))"),
    ("dict-fromkeys", 'print(dict.fromkeys("ab"))'),
]


@pytest.mark.parametrize("case_id,program", MISSING_METHODS,
                         ids=[c[0] for c in MISSING_METHODS])
def test_a_method_cpython_has_refuses_instead_of_raising(case_id, program, lypning_bin):
    r = engines.run(engines.LYPNING, program, binary=lypning_bin)
    assert r.returncode == UNSUPPORTED_EXIT, "a method CPython has must leave by exit 90"
    assert r.stdout == "", "the commit barrier let output escape before a refusal"
    assert "-method:" in r.stderr, r.stderr


def test_an_attribute_neither_has_keeps_cpythons_error(lypning_bin):
    """The other side of the split, and why it is not just "refuse on any miss".

    `'x'.nosuch()` is the program's own bug. CPython raises AttributeError at
    exit 1 and so must lypning — refusing it would send a broken program down
    the whole chain to be told the same thing three times.
    """
    r = engines.run(engines.LYPNING, 'print("x".nosuch())', binary=lypning_bin)
    assert r.returncode == 1
    assert "AttributeError" in r.stderr
