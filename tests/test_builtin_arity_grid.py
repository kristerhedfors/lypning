"""Builtin CALL SHAPES, as a grid: every program on every built variant and on CPython.

`tests/test_repr_grid.py` is the shape this follows and the rule is the same —
each row ends either byte-identical to the reference or as a clean exit-90
refusal, and nothing else counts. What differs is the question. That file asks
what a value PRINTS; this one asks what a call DOES when the arguments are
wrong, which is the half a subset runtime gets wrong silently: an argument it
does not understand is an argument it can drop, and a dropped argument is an
answer at exit 0 for a program CPython rejects.

**The four shapes this file was written for**, all measured on CPython 3.14.5
on 2026-09-07:

===========================  ==========================  =====================
program                      CPython                     lypning, before
===========================  ==========================  =====================
`list(zip())`                `[]`                        **never returned**
`next(zip())`                `StopIteration`             `()` at exit 0
`bytes(-1)`                  `ValueError: negative …`    `b''` at exit 0
`filter(bool, [1], 0)`       `TypeError: filter …`       filtered, at exit 0
===========================  ==========================  =====================

The first is the worst outcome this project has: a HANG returns no exit code at
all, so the dispatcher never learns to retry and the caller waits for nothing.
`Iter::Zip` built its tuple by looping over its iterables, so with none of them
the loop body never ran, nothing ever reported exhaustion, and it yielded `()`
for as long as anyone asked. Every row of `ZERO_ITERABLES` is therefore run
under a timeout that FAILS rather than waits, and the two shapes that consume
exactly one element — `next(zip())` and a `for` with a `break` — are in the
same table because they are the same defect surviving to exit 0 with an answer.

**Arity is a table, not a family resemblance.** CPython words the same
`(1, 1)` three different ways depending on how the function is defined in C:
`len() takes exactly one argument (2 given)` spells its count as a WORD,
`sorted expected 1 argument, got 2` has no parentheses at all, and
`enumerate() takes at most 2 arguments (3 given)` has both. A table that gets
the count right and the wording wrong still disagrees with the reference on
stderr, which is graded. `ARITY` is every builtin the engine serves crossed
with 0..4 arguments, and the fillers are TYPE-CORRECT so that what is measured
is the arity rule and not a type error standing in front of it.

**The over-correction controls are half the file.** `WORKS` is every call shape
that must keep working, because the cheap way to pass `ARITY` is to refuse more
than CPython does, and the cheap way to pass `ZERO_ITERABLES` is to make `zip`
empty more often than CPython does.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

# ---------------------------------------------------------------- the spec ---

#: A row must finish inside this many seconds. It is not a performance budget —
#: every program here is a one-liner — it is the only way a HANG can be a test
#: FAILURE rather than a suite that never ends.
TIMEOUT = 20.0

#: Every shape that reaches `zip()` or `map(f)` with ZERO iterables. The first
#: group hangs a runtime that treats "no iterables" as "nothing said stop"; the
#: second consumes exactly one element of that infinite stream and prints it.
#: `zip(*[])` is here because the splat is a second way in, and a fix at the
#: `zip()` CALL would have missed it.
ZERO_ITERABLES = [
    "print(list(zip()))",
    "print(tuple(zip()))",
    "print(set(zip()))",
    "print(sorted(zip()))",
    "print(dict(zip()))",
    "print(len(list(zip())))",
    "print(any(zip()))",
    "print(all(zip()))",
    "print([t for t in zip()])",
    "print({t for t in zip()})",
    "print(sum(1 for _ in zip()))",
    "print(list(zip(*[])))",
    "print(list(zip(*())))",
    "xs = []\nprint(list(zip(*xs)))",
    "print(list(enumerate(zip())))",
    "print(list(map(str, zip())))",
    "print(list(filter(None, zip())))",
    # …and the two that answered instead of hanging, which is the same defect
    # one element in.
    "print(next(zip()))",
    "print(next(zip(), 'gone'))",
    "for t in zip():\n    print(t)\n    break\nprint('done')",
    "n = 0\nfor t in zip():\n    n += 1\n    if n > 3:\n        break\nprint(n)",
    "it = iter(zip())\nprint(next(it, 'gone'))",
    # `map` reaches the same state through a missing argument rather than a
    # missing iterable, and CPython rejects it at the CALL.
    "m = map(abs)\nprint('ok')",
    "print(list(map(abs)))",
    "print(next(map(abs)))",
    "m = map(abs, *[])\nprint('ok')",
]

#: `bytes(n)` for a negative `n`. `.max(0)` clamped it, so a count that is not a
#: count produced the empty object at exit 0 and the program carried on holding
#: something it never asked for.
NEGATIVE_COUNT = [
    "print(bytes(-1))",
    "print(bytes(-2))",
    "print(bytes(-100))",
    "n = -1\nprint(bytes(n))",
    "print(len(bytes(-1)))",
    "print(bytes(-1) == b'')",
    "b = bytes(-3)\nprint('ok')",
    "print(bytes(0))",
    "print(bytes(3))",
]

#: Type-correct fillers for every builtin in `builtins::BUILTINS`, longest valid
#: prefix first: row `k` of `ARITY` calls the name with the first `k` of these,
#: padded with `0` once they run out. Type-correct is the whole point — the
#: first derivation of the engine's own table scored `format(1, 1)`'s
#: "argument 2 must be str" as an arity limit and came back one short.
FILLERS = {
    "print": ["1"],
    "open": ["'/dev/null'", "'r'"],
    "len": ["[1]"],
    "isinstance": ["1", "int"],
    "repr": ["1"],
    "sorted": ["[2, 1]"],
    "range": ["1", "5", "2"],
    "str": ["1"],
    "int": ["'10'", "16"],
    "dict": ["{'a': 1}"],
    "set": ["[1]"],
    "list": ["[1]"],
    "sum": ["[1]", "0"],
    "type": ["1"],
    "min": ["[1, 2]"],
    "chr": ["65"],
    "enumerate": ["[1, 2]", "0"],
    "any": ["[1]"],
    "bool": ["1"],
    "float": ["'1.5'"],
    "zip": ["[1]", "[2]"],
    "round": ["1.5", "1"],
    "max": ["[1, 2]"],
    "divmod": ["7", "2"],
    "bytes": ["2"],
    "hex": ["255"],
    "iter": ["[1, 2]"],
    "next": ["iter([1])", "0"],
    "tuple": ["[1]"],
    "format": ["1", "'d'"],
    "ord": ["'a'"],
    "abs": ["-1"],
    "all": ["[1]"],
    "bin": ["5"],
    "map": ["abs", "[1]"],
    "filter": ["bool", "[1]"],
    "reversed": ["[1, 2]"],
    "oct": ["8"],
    "input": [],
}

#: `builtins::BUILTINS`, in the crate's order. Held here so that a name added to
#: the engine without a filler above fails
#: `test_every_builtin_the_engine_serves_is_in_this_grid` rather than being
#: audited by omission.
AUDITED = tuple(FILLERS)

#: Assigned and never used, so that a builtin which builds a LAZY object shows
#: its verdict at the call rather than at the first `next`. `map(abs)` ran to
#: completion at exit 0 in exactly this shape, and consuming the result would
#: have hidden it behind `abs()`'s own TypeError.
UNCONSUMED = "_x = %s\nprint('ok')"

#: Every builtin crossed with 0..4 positional arguments.
ARITY = [
    (name, k)
    for name in AUDITED
    for k in range(5)
]

#: The over-correction control for `ARITY`: call shapes CPython ACCEPTS, one per
#: name the arity work touched, which must still answer.
WORKS = [
    "print(list(zip([1, 2], 'ab')))",
    "print(list(zip([1], [2], [3])))",
    "print(list(zip([1])))",
    "print(list(map(abs, [-1, 2])))",
    "print(list(map(max, [1, 2], [3, 0])))",
    "print(list(filter(None, [0, 1, 2])))",
    "print(list(filter(bool, [0, 1])))",
    "print(list(reversed([1, 2, 3])))",
    "print(list(enumerate('ab')))",
    "print(list(enumerate('ab', 1)))",
    "print(list(enumerate('ab', start=1)))",
    "print(dict())",
    "print(dict({'a': 1}))",
    "print(dict(a=1, b=2))",
    "print(dict([('a', 1)]))",
    "print(dict({'a': 1}, b=2))",
    "print(type(1), type('a'), type([]))",
    "print(str(), str(1), str(b'hi', 'utf-8'))",
    "print(bytes(), bytes(2), bytes([1, 2]), bytes('a', 'utf-8'))",
    "print(sum([1, 2]), sum([1, 2], 3))",
    "print(round(1.5), round(1.55, 1))",
    "print(iter([1, 2]) is not None)",
    "print(next(iter([1])), next(iter([]), 'd'))",
    "print(b'hi'.decode(), b'hi'.decode('utf-8'))",
    "print(sorted([2, 1]), sorted([2, 1], reverse=True))",
]

# ------------------------------------------------------------- the harness ---


def _candidates() -> list:
    """``(engine, binary)`` for every spectrum variant that is built.

    No capability probe, unlike `test_repr_grid.py`: none of this is a `cap-*`
    and none of it is optional, so a binary that does not hold these fixes must
    FAIL the grid rather than skip it. A skip here would be the file reporting
    green about a hang."""
    targets = {
        engines.LYPNING: paths.RUST_DIR / "target" / "release" / "lypning",
        engines.LYPNING_L: paths.RUST_DIR / "target" / "variant-l" / "release" / "lypning",
    }
    out = []
    for engine in engines.SPECTRUM:
        found = engines.find(engine)
        for cand in ([Path(found)] if found else []) + [targets[engine]]:
            if cand.is_file():
                out.append((engine, cand))
                break
    return out


BINARIES = _candidates()
IDS = [e for e, _ in BINARIES]

needs_engine = pytest.mark.skipif(
    not BINARIES, reason="no built Rust variant (lypning build --rust)",
)

on_each = pytest.mark.parametrize("engine,binary", BINARIES, ids=IDS or ["none"])

_REF: dict = {}


def _run(argv: list, program: str) -> subprocess.CompletedProcess:
    """One program, in a temp cwd of its own — invariant 4 — with stdin closed
    and a timeout that turns a hang into a failed row.

    `stdin=DEVNULL` because `input` is one of the names audited here and a row
    that waits on a terminal is a hang wearing another hat."""
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True, text=True,
                              cwd=d, timeout=TIMEOUT, stdin=subprocess.DEVNULL)


def _reference(program: str) -> subprocess.CompletedProcess:
    if program not in _REF:
        _REF[program] = _run([sys.executable], program)
    return _REF[program]


def _last_stderr_line(text: str) -> str:
    """The exception line — the half of a traceback both sides claim.

    CPython's body names ``File "<string>", line 1``, which no second
    implementation writes or could write; the exception line is the claim, and
    it is compared byte for byte."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _refusal_problem(engine: str, got: subprocess.CompletedProcess):
    """``None`` if this is a clean exit-90 refusal, else what is wrong with it."""
    if got.returncode != engines.UNSUPPORTED_EXIT:
        return "exit %d, not %d" % (got.returncode, engines.UNSUPPORTED_EXIT)
    if got.stdout != "":
        return "stdout was not empty: %r" % got.stdout[:120]
    head = "%s: unsupported: " % engine
    line = got.stderr.strip()
    if not line.startswith(head) or "\n" in line:
        return "stderr was %r, expected one %r line" % (line[:160], head)
    return None


def _agree(engine: str, binary: Path, program: str, *, may_refuse: bool = True) -> None:
    """The rule every row in this file is held to.

    Byte-identical stdout, the same exit code and the same exception line as the
    reference — or a clean refusal, which is never a bug (invariant 1) but is
    reported as a skip so that a row which stopped measuring cannot pass as one
    that agreed."""
    try:
        got = _run([str(binary)], program)
    except subprocess.TimeoutExpired:
        raise AssertionError(
            "%s HUNG on this program — no exit code, so the dispatcher never "
            "retries and the caller waits for nothing.\n  program: %r"
            % (engine, program))
    if got.returncode == engines.UNSUPPORTED_EXIT:
        problem = _refusal_problem(engine, got)
        assert problem is None, "%s\n  program: %r" % (problem, program)
        assert may_refuse, (
            "%s refuses a program it used to answer: %r\n  program: %r"
            % (engine, got.stderr.strip()[:160], program))
        pytest.skip("%s refuses this row: %s" % (engine, got.stderr.strip()[:160]))
    ref = _reference(program)
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "%s disagrees with CPython.\n"
        "  program: %r\n  %s: %r exit %d\n  cpython:   %r exit %d"
        % (engine, program, engine, got.stdout, got.returncode,
           ref.stdout, ref.returncode))
    assert _last_stderr_line(got.stderr) == _last_stderr_line(ref.stderr), (
        "%s raised a different exception line.\n  program: %r\n  %s: %r\n"
        "  cpython:   %r"
        % (engine, program, engine, _last_stderr_line(got.stderr),
           _last_stderr_line(ref.stderr)))


# ------------------------------------------------------- zero iterables ------


@needs_engine
@on_each
@pytest.mark.parametrize("program", ZERO_ITERABLES, ids=range(len(ZERO_ITERABLES)))
def test_zip_and_map_with_no_iterables_terminate_and_agree(
    engine: str, binary: Path, program: str,
) -> None:
    """`zip()` is an immediately-empty iterator, not an endless one.

    Every row here either hung or printed `()` before the fix. The timeout in
    `_run` is what makes the first half of that a failure: a hang produces no
    exit code, so it is the one outcome the exit-90 chain cannot route around —
    strictly worse than the wrong answer the same bug gives on the paths that
    only ask once."""
    _agree(engine, binary, program)


@needs_engine
@on_each
def test_an_empty_zip_is_empty_and_a_non_empty_one_is_not(
    engine: str, binary: Path,
) -> None:
    """The over-correction control for the row above, in one program: making
    `Iter::Zip` report exhaustion too eagerly would pass every test in
    `ZERO_ITERABLES` and break every real `zip` in the corpus."""
    _agree(engine, binary,
           "print(list(zip()), list(zip([1, 2], 'ab')), list(zip([1], [])))")


# ------------------------------------------------------- negative counts -----


@needs_engine
@on_each
@pytest.mark.parametrize("program", NEGATIVE_COUNT, ids=range(len(NEGATIVE_COUNT)))
def test_bytes_of_a_negative_count_raises_rather_than_clamping(
    engine: str, binary: Path, program: str,
) -> None:
    """`bytes(-1)` is a `ValueError`, not `b''`.

    The last two rows are the control: `bytes(0)` really is `b''` and `bytes(3)`
    really is three NULs, so the fix has to be the SIGN and not the whole
    integer path."""
    _agree(engine, binary, program)


# ---------------------------------------------------------------- arity ------


@needs_engine
@on_each
@pytest.mark.parametrize("name,count", ARITY,
                         ids=["%s-%d" % (n, k) for n, k in ARITY])
def test_the_builtin_arity_grid_agrees_with_cpython(
    engine: str, binary: Path, name: str, count: int,
) -> None:
    """Every builtin the engine serves, called with 0..4 positional arguments.

    The call is assigned and never used, so a name that builds a lazy object is
    judged where CPython judges it — at the call. `map(abs)` is the row that
    makes the point: consumed, it raises `abs()`'s TypeError and looks nearly
    right; unconsumed, it ran to completion at exit 0."""
    base = FILLERS[name]
    args = list(base[:count]) + ["0"] * max(0, count - len(base))
    _agree(engine, binary, UNCONSUMED % ("%s(%s)" % (name, ", ".join(args))))


@needs_engine
@on_each
@pytest.mark.parametrize("program", WORKS, ids=range(len(WORKS)))
def test_the_call_shapes_cpython_accepts_are_still_answered(
    engine: str, binary: Path, program: str,
) -> None:
    """The over-correction control for `ARITY`.

    A table that refuses one argument too many passes every arity row above and
    breaks the corpus; these are the shapes CPython accepts, and a refusal on
    one of them is reported rather than skipped."""
    _agree(engine, binary, program, may_refuse=True)


@needs_engine
def test_every_builtin_the_engine_serves_is_in_this_grid() -> None:
    """The audit, asserted rather than described.

    `AUDITED` is `builtins::BUILTINS` transcribed, and this is what catches a
    name added to the engine that nobody crossed with an argument count. The
    engine is asked for the list it actually has — `NameError` is what a name it
    does not serve raises — so the two cannot drift apart in silence."""
    engine, binary = BINARIES[0]
    missing = []
    for name in AUDITED:
        got = _run([str(binary)], "print(%s is not None)" % name)
        if got.returncode != 0 or got.stdout != "True\n":
            missing.append((name, got.returncode, got.stderr.strip()[:80]))
    assert not missing, (
        "these names are in this file's audit list but %s does not serve them, "
        "so the grid measures nothing for them: %r" % (engine, missing))
