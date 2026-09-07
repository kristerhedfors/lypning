"""Parameter lists, as a grid: every shape, in both spellings, on every call.

`def` and `lambda` are the same grammar under two terminators, and this file
exists because they were two readers. The lambda one knew about `*args` and
nothing else — no `**kwargs`, no `/`, no refusal for the keyword-only forms —
so

    f = lambda *a, **k: 1
    print(f(1))

died on `SyntaxError: expected a name, found '**'` at exit 1. That is the
PROGRAM's own exit code, the one a dispatcher must hand back untouched, so the
caller got a failure where CPython answers `1` and where a clean exit-90 refusal
would have bought an answer one spawn later. Fifteen of the forty-eight
lambda cells below disagreed with CPython that way; the rest agreed only by
accident, both sides exiting 1 for unrelated reasons.

The fix was to delete the second reader, not to teach it: `parse::param_list`
now serves both spellings and `call_func_inner` binds what it produces without
ever asking which keyword introduced the parameters. So the grid is run in BOTH
spellings and the two are held to each other as well as to CPython — a shape
that answers as a `def` and refuses as a `lambda` is the defect this file is
named for, and it is invisible to a grid that only writes one of them.

`/` is here for the opposite reason. It was accepted and IGNORED, so

    def f(x, /, y): return (x, y)
    f(x=1, y=2)     ->  (1, 2)      (CPython: TypeError)

answered at exit 0 with a binding CPython refuses to make — a silent wrong
answer, which is the one outcome worse than a refusal, and one that extending
`/` to lambdas would have doubled. `Params::posonly` is what closes it.

Every cell is a whole program, because what is under test is the process's exit
code and stdout, and each is either byte-identical to CPython on both or a clean
exit-90 refusal: one line on stderr, nothing on stdout. `lambda *, x` is that
refusal today (`kwonly`), exactly as `def f(*, x)` has always been.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from lypning import engines

needs_engine = pytest.mark.skipif(
    engines.find_lypning() is None, reason="the Rust core is not built"
)

#: `(id, parameter list, body expression)`. Every shape a parameter list can
#: take, in the order the grammar allows them to combine.
SHAPES = [
    ("bare", "", "1"),
    ("star", "*a", "a"),
    ("dstar", "**k", "k"),
    ("star-dstar", "*a, **k", "(a, k)"),
    ("pos-star", "x, *a", "(x, a)"),
    ("pos-dstar", "x, **k", "(x, k)"),
    ("default-star-dstar", "x=1, *a, **k", "(x, a, k)"),
    ("kwonly-bare", "*, x", "x"),
    ("kwonly-after-star", "*a, x", "(a, x)"),
    ("posonly", "x, /, y", "(x, y)"),
    ("posonly-only", "x, /", "x"),
    ("posonly-dstar", "x, /, **k", "(x, k)"),
    ("posonly-star-dstar", "x, /, *a, **k", "(x, a, k)"),
    ("default-only", "x=1", "x"),
]

#: Too few, too many, by keyword, and the mixtures — the call shapes that tell
#: two parameter lists apart. A cell that answers for every call and a cell that
#: answers for none are both uninformative; these are the ones that split.
CALLS = ["()", "(1)", "(1, 2)", "(x=1)", "(1, x=2)", "(1, 2, y=3, z=4)"]


def _programs(params: str, body: str, call: str):
    """The same cell written as a lambda and as a def."""
    return (
        "f = lambda %s: %s\nprint(repr(f%s))\n" % (params, body, call),
        "def f(%s):\n    return %s\nprint(repr(f%s))\n" % (params, body, call),
    )


CASES = [
    ("%s%s" % (name, call), params, body, call)
    for name, params, body in SHAPES
    for call in CALLS
]


def _outcome(program: str, tmp_path, tag: str):
    """Run one program on both interpreters; return lypning's outcome.

    ``"REFUSED"`` for a clean exit-90, otherwise ``(stdout, returncode)`` — and
    that pair must equal CPython's. Separate cwds per interpreter, for the
    reason invariant 4 gives: the second must not read back what the first
    wrote.
    """
    ours, theirs = tmp_path / (tag + "-l"), tmp_path / (tag + "-c")
    ours.mkdir()
    theirs.mkdir()
    ref = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True,
        cwd=str(theirs), timeout=60,
    )
    got = engines.run(engines.LYPNING, program, cwd=ours, timeout=60)
    if got.unsupported:
        lines = got.stderr.splitlines()
        assert got.stdout == "", (
            "a refusal wrote to stdout, which the next tier will write again: %r"
            % got.stdout
        )
        assert len(lines) == 1, "a refusal is exactly one line: %r" % got.stderr
        assert got.refused, "exit 90 without the contract line: %r" % got.stderr
        return "REFUSED"
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "%s\nlypning exit %d %r; CPython exit %d %r"
        % (program, got.returncode, got.stderr[-300:], ref.returncode, ref.stderr[-300:])
    )
    return (got.stdout, got.returncode)


@needs_engine
@pytest.mark.parametrize(
    "case_id,params,body,call", CASES, ids=[c[0] for c in CASES]
)
def test_a_parameter_list_agrees_with_cpython_in_both_spellings(
    case_id: str, params: str, body: str, call: str, tmp_path
) -> None:
    lam, dfn = _programs(params, body, call)
    got_lam = _outcome(lam, tmp_path, "lam")
    got_def = _outcome(dfn, tmp_path, "def")
    assert got_lam == got_def, (
        "the same parameter list behaves differently as a lambda and as a def, "
        "which is the whole defect: lambda %r, def %r\n%s%s"
        % (got_lam, got_def, lam, dfn)
    )


#: The `/` marker's own rows, which no call above reaches: a positional-only
#: parameter named by keyword lands on `**kwargs` when there is one, and on the
#: unexpected-keyword error when there is not. Both are CPython's rule and
#: neither survived `/` being parsed and dropped.
POSONLY_PROGRAMS = [
    "def f(x, /, y):\n    return (x, y)\nprint(f(x=1, y=2))",
    "def f(x, /, **k):\n    return (x, k)\nprint(f(1, x=2))",
    "def f(a, b, /, c, *d, **e):\n    return (a, b, c, d, e)\nprint(f(1, 2, 3, 4, z=5))",
    "def f(x, /, y=2):\n    return (x, y)\nprint(f(1), f(1, 3), f(1, y=4))",
    "print((lambda x, /, **k: (x, k))(1, x=2))",
    "print((lambda a, /, b, *c, **d: (a, b, c, d))(1, 2, 3, e=4))",
    "print((lambda x, /, y: (x, y))(x=1, y=2))",
]


@needs_engine
@pytest.mark.parametrize("program", POSONLY_PROGRAMS)
def test_a_positional_only_parameter_may_not_be_named(program: str, tmp_path) -> None:
    _outcome(program, tmp_path, "posonly")


#: Sharing one reader gave the lambda the `def` default parser, and with it the
#: conditional expression CPython has always allowed there. The old comment said
#: the `:` made it ambiguous; it does not — `else` takes exactly one expression,
#: so the terminator is never in doubt. A nested lambda in a default is the same
#: question asked the other way round.
DEFAULT_PROGRAMS = [
    "print((lambda x=1 if True else 2: x)())",
    "print((lambda x=1 if False else 2: x)())",
    "g = lambda f=lambda: 7: f()\nprint(g())",
    'print((lambda x={"a": 1}: x)())',
    "print((lambda x=[1, 2][1]: x)())",
    "print((lambda a, b=2, *c, **d: (a, b, c, d))(1, 3, 4, e=5))",
]


@needs_engine
@pytest.mark.parametrize("program", DEFAULT_PROGRAMS)
def test_a_default_value_reads_the_same_expression_as_a_def_does(
    program: str, tmp_path
) -> None:
    _outcome(program, tmp_path, "default")


@needs_engine
def test_the_reported_program_answers_rather_than_failing(tmp_path) -> None:
    """`py-cd598c0d0e44`'s first two lines, which is where this was found.

    Pinned on its own because the corpus entry it came from is skipped by the
    battery — it imports `lypning` — so nothing else in the tree would notice
    this exact program going back to exit 1.
    """
    got = _outcome("f = lambda *a, **k: 1\nprint(f(1))\n", tmp_path, "reported")
    assert got == ("1\n", 0), got
