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
from lypning import engines as eng

#: ``(id, program)``. Each prints everything it compares, so a failure names the
#: value rather than just the case.
CASES = [
    (
        # `type_name` of every exception instance is the literal string
        # "Exception", and `isinstance` compared against it — so
        # `isinstance(ValueError('b'), ValueError)` was False and
        # `isinstance(SystemExit(), Exception)` was True, both at exit 0, both
        # wrong. Nothing in the corpus did it yet, so `conformance` was clean
        # the whole time. It goes through `exc_matches` now, which is the same
        # table an `except` clause uses, so the two cannot drift apart.
        "isinstance-of-an-exception-follows-the-hierarchy",
        "pairs = [\n"
        "    (ValueError('b'), ValueError), (ValueError('b'), Exception),\n"
        "    (ValueError('b'), TypeError), (SystemExit(), Exception),\n"
        "    (FileNotFoundError('x'), OSError), (IOError('x'), OSError),\n"
        "    (OSError('x'), IOError), (KeyError('k'), LookupError),\n"
        "    (IndexError(), LookupError), (ZeroDivisionError(), ArithmeticError),\n"
        "    (ValueError('b'), BaseException),\n"
        "]\n"
        "for v, c in pairs:\n"
        "    print(isinstance(v, c))\n"
        "print(isinstance(ValueError('b'), (TypeError, ValueError)))\n"
        "print(isinstance(ValueError('b'), (TypeError, KeyError)))\n"
        "try:\n"
        "    raise ValueError('v')\n"
        "except Exception as e:\n"
        "    print(isinstance(e, ValueError), isinstance(e, TypeError), isinstance(e, Exception))\n"
        "print(isinstance(1, int), isinstance(True, int), isinstance(1, bool))\n"
        "print(isinstance(1.5, float), isinstance('a', str), isinstance(b'x', bytes))\n"
        "print(isinstance([1], list), isinstance((1,), tuple), isinstance({1: 2}, dict))\n"
        "print(isinstance(1, (str, float)), isinstance(None, int))\n",
    ),
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
    # Not a missing method — a deliberate refusal. `isinstance(x, type)` asks
    # whether x is a class, and `Value::Builtin` is both `int` and `print`, so
    # lypning would have to guess which builtins are types. It used to answer
    # False for `isinstance(type(3), type)`: a wrong answer at exit 0.
    ("isinstance-against-type", "print(isinstance(type(3), type))"),
]


@pytest.mark.parametrize("case_id,program", MISSING_METHODS,
                         ids=[c[0] for c in MISSING_METHODS])
def test_a_method_cpython_has_refuses_instead_of_raising(case_id, program, lypning_bin):
    r = engines.run(engines.LYPNING, program, binary=lypning_bin)
    assert r.returncode == UNSUPPORTED_EXIT, "a method CPython has must leave by exit 90"
    assert r.stdout == "", "the commit barrier let output escape before a refusal"
    assert ": unsupported: " in r.stderr, r.stderr


#: ``(id, program)`` run with bytes on stdin, differentially, exactly as above.
#:
#: Split out because these need an input and the cases above do not. The reason
#: they exist is `stdin_line`: it used to re-copy the whole captured input for
#: every line, which made `for line in sys.stdin` quadratic — the corpus's
#: largest single cluster, at 2.3 seconds for sixteen thousand lines against
#: CPython's 3.4 milliseconds. The fix moved the slicing inside the buffer's
#: borrow, and the thing it could plausibly have broken is the CURSOR that
#: `readline`, `read`, `readlines` and iteration share. So the cursor is what
#: these pin, in every interleaving, rather than the speed — a timing assertion
#: on a shared runner measures the runner.
STDIN_CASES = [
    (
        "readline-then-read",
        "import sys\nprint(repr(sys.stdin.readline()))\n"
        "print(repr(sys.stdin.readline()))\nprint(repr(sys.stdin.read()))\n",
        "a\nbb\nccc\ndddd\n",
    ),
    (
        "read-consumes-everything-including-later-readlines",
        "import sys\nprint(repr(sys.stdin.read()))\n"
        "print(repr(sys.stdin.readline()), sys.stdin.readlines())\n",
        "a\nbb\nccc\n",
    ),
    (
        "iteration-then-read",
        "import sys\nfor line in sys.stdin:\n    print(repr(line))\n"
        "print('rest', repr(sys.stdin.read()))\n",
        "a\nbb\nccc\ndddd\n",
    ),
    (
        "readline-then-readlines",
        "import sys\nprint(repr(sys.stdin.readline()), sys.stdin.readlines())\n",
        "a\nbb\nccc\n",
    ),
    (
        "last-line-has-no-newline",
        "import sys\nfor line in sys.stdin:\n    print(repr(line))\n",
        "x\ny",
    ),
    (
        "empty-stdin",
        "import sys\nprint(repr(sys.stdin.read()), sys.stdin.readlines())\n",
        "",
    ),
    (
        "many-lines-stay-in-order",
        "import sys\nn = 0\nlast = ''\n"
        "for line in sys.stdin:\n    n += 1\n    last = line\n"
        "print(n, repr(last))\n",
        "".join("line %d\n" % i for i in range(500)),
    ),
]


@pytest.mark.parametrize("case_id,program,stdin", STDIN_CASES,
                         ids=[c[0] for c in STDIN_CASES])
def test_stdin_agrees_with_cpython(case_id, program, stdin, lypning_bin):
    cpython = engines.find_cpython()
    if cpython is None:
        pytest.skip("no CPython to compare against")
    ours = engines.run(engines.LYPNING, program, binary=lypning_bin, stdin=stdin)
    theirs = engines.run(engines.CPYTHON, program, binary=cpython, stdin=stdin)
    assert ours.returncode != UNSUPPORTED_EXIT, ours.stderr
    assert (ours.stdout, ours.returncode) == (theirs.stdout, theirs.returncode)


#: Bytes that cannot begin a token in any Python 3 program. CPython answers each
#: with a SyntaxError at exit 1 and empty stdout, and so must lypning: a refusal
#: means "outside my subset, try the next interpreter", and there is no
#: interpreter for which `$p` is a program. Four corpus entries are shell paste
#: accidents of exactly this shape and were each costing a spawn to be told by
#: CPython what lypning already knew.
IMPOSSIBLE_BYTES = ["$p", "$1", "`x`", "?", "!", "x !", 'r#"a"#']

#: The other side of the same line, and the reason it is drawn at ASCII. Each of
#: these CONTAINS one of those bytes and is a perfectly good program.
POSSIBLE_ANYWAY = [
    ('print("$p ? `x`")', "$p ? `x`\n"),
    ("# $ ? `\nprint(1)", "1\n"),
    ("print(1 != 2)", "True\n"),
    # Python 3 identifiers may be Unicode, so a non-ASCII byte is NOT an
    # impossible one and must keep its refusal rather than joining the list.
    ("\u03c0 = 1\nprint(\u03c0)", "1\n"),
]


@pytest.mark.parametrize("program", IMPOSSIBLE_BYTES)
def test_an_impossible_byte_is_a_syntax_error_not_a_refusal(program, lypning_bin):
    r = engines.run(engines.LYPNING, program, binary=lypning_bin)
    assert r.returncode == 1, "CPython's own verdict is SyntaxError at exit 1"
    assert r.stdout == ""
    assert r.stderr != ""
    assert ": unsupported: " not in r.stderr, "a refusal here would send it down the chain"


@pytest.mark.parametrize("program,expected", POSSIBLE_ANYWAY,
                         ids=[p[0][:20] for p in POSSIBLE_ANYWAY])
def test_those_bytes_are_still_fine_inside_a_program(program, expected, lypning_bin):
    r = engines.run(engines.LYPNING, program, binary=lypning_bin)
    assert (r.returncode, r.stdout) == (0, expected), r.stderr


def test_an_attribute_neither_has_keeps_cpythons_error(lypning_bin):
    """The other side of the split, and why it is not just "refuse on any miss".

    `'x'.nosuch()` is the program's own bug. CPython raises AttributeError at
    exit 1 and so must lypning — refusing it would send a broken program down
    the whole chain to be told the same thing three times.
    """
    r = engines.run(engines.LYPNING, 'print("x".nosuch())', binary=lypning_bin)
    assert r.returncode == 1
    assert "AttributeError" in r.stderr


def test_identity_still_answers_where_it_is_a_fact_and_not_an_interning_question(lypning_bin):
    """The other half of the `is` refusal, and the half that bounds its cost.

    Refusing more than necessary is not free — every refusal is a process
    spawn — so the refusal is narrowed to the case that genuinely cannot be
    answered: two values that are EQUAL, IMMUTABLE, and not provably the same
    object. Everything else still answers:

    * the singletons, where identity has one possible answer;
    * two values that are not equal, which are never the same object either;
    * ``x is x`` wherever the value carries an ``Rc`` to compare — every str,
      tuple, list, dict and set;
    * two mutable displays, which CPython never folds: ``[1] is [1]`` is False
      in both.
    """
    def out(program):
        r = eng.run(eng.LYPNING, program)
        assert not r.refused, "refused where it should answer: %s" % r.stderr.strip()
        return r.stdout.strip()

    assert out("x = None\nprint(x is None, x is not None)") == "True False"
    assert out("x = 5\nprint(x is None)") == "False"
    assert out("print(True is True, False is False, True is False)") == "True True False"
    assert out("print([1] is [1], {} is {})") == "False False"
    assert out("x = [1]\nprint(x is x)") == "True"
    assert out("x = 'ab'\nprint(x is x)") == "True"
    assert out("x = (1, 2)\nprint(x is x)") == "True"
    assert out("print(1 is 'a', 1000 is 1001)") == "False False"
