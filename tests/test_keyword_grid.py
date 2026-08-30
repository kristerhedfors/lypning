"""Keyword arguments, as a grid: which parameters may be NAMED, and what happens.

Almost none of CPython's builtins and container methods accept keyword
arguments. `str.strip`, `str.ljust`, `dict.get`, `bool`, `len` and their
neighbours are C functions with positional-only parameters, so naming one is a
`TypeError`. lypning **silently ignored** the keyword and answered without it:

    'xax'.strip(chars='x')   ->  'xax'     (CPython: TypeError)
    'a'.ljust(width=5)       ->  'a'       (CPython: TypeError)
    {'a': 1}.get('b', default=2) -> None   (CPython: TypeError)
    bool(x=1)                ->  False     (CPython: TypeError)

and the handful that DO take keywords were half-wired, which is worse because it
looks finished: `str.split` read `maxsplit=` and not `sep=`, so
`'a,b'.split(sep=',')` split on whitespace and returned `['a,b']` at exit 0.
`sum(xs, start=10)` ignored the start and summed from zero.

This is a grid because the defect is per-parameter, not per-function: knowing
that `split` handles one keyword says nothing about the other, and a list of
examples is exactly what missed this for the project's whole history. The
allow-lists in `methods.rs` and `builtins.rs` were built by asking CPython 3.11
which names it accepts, and this test is the same question asked of both
interpreters at once.

Two constructs the subset refuses outright — `str.center` and `str.expandtabs` —
are deliberately absent. A refusal is exit 90 and correct, but it stops the
program, and a grid that stops measures nothing after that point.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from lypning import engines

needs_engine = pytest.mark.skipif(
    engines.find_lypning() is None, reason="the Rust core is not built"
)

#: Every case is a thunk so one raising does not stop the sweep, and the message
#: is compared as well as the value — "takes no keyword arguments" is the whole
#: point, and a bare TypeError would pass whatever raised it.
GRID = r"""
CASES = [
    ("str.strip", lambda: " a ".strip(chars=None)),
    ("str.strip-arg", lambda: "xax".strip(chars="x")),
    ("str.ljust", lambda: "a".ljust(width=5, fillchar=".")),
    ("str.zfill", lambda: "7".zfill(width=3)),
    ("str.replace", lambda: "a-b".replace("-", "+", count=1)),
    ("str.index", lambda: "abc".index("b", start=0)),
    ("str.find", lambda: "abc".find(sub="b")),
    ("str.count", lambda: "abc".count(sub="b")),
    ("str.startswith", lambda: "abc".startswith(prefix="a")),
    ("str.endswith", lambda: "abc".endswith(suffix="c")),
    ("str.partition", lambda: "a-b".partition(sep="-")),
    ("str.join", lambda: ",".join(iterable=["x"])),
    ("str.split-sep", lambda: "a,b".split(sep=",")),
    ("str.split-both", lambda: "a,b,c".split(sep=",", maxsplit=1)),
    ("str.rsplit-sep", lambda: "a,b,c".rsplit(sep=",", maxsplit=1)),
    ("str.splitlines", lambda: "a\nb".splitlines(keepends=True)),
    ("str.encode", lambda: "ab".encode(encoding="utf-8")),
    ("bytes.decode", lambda: b"ab".decode(encoding="utf-8")),
    ("bytes.split", lambda: b"a,b".split(sep=b",")),
    ("bytes.strip", lambda: b" a ".strip(bytes=None)),
    ("list.index", lambda: [1, 2].index(value=2)),
    ("list.count", lambda: [1].count(value=1)),
    ("list.append", lambda: [1].append(object=2)),
    ("list.pop", lambda: [1].pop(index=0)),
    ("dict.get", lambda: {"a": 1}.get("b", default=2)),
    ("dict.pop", lambda: {"a": 1}.pop("b", default=2)),
    ("dict.setdefault", lambda: {}.setdefault("a", default=1)),
    ("set.add", lambda: {1}.add(elem=2)),
    ("set.discard", lambda: {1}.discard(elem=1)),
    ("bool", lambda: bool(x=1)),
    ("len", lambda: len(obj=[1])),
    ("abs", lambda: abs(x=-1)),
    ("chr", lambda: chr(i=65)),
    ("ord", lambda: ord(c="A")),
    ("hex", lambda: hex(number=255)),
    ("repr", lambda: repr(obj=1)),
    ("list", lambda: list(iterable=[1])),
    ("tuple", lambda: tuple(iterable=[1])),
    ("set", lambda: set(iterable=[1])),
    ("divmod", lambda: divmod(x=7, y=2)),
    ("isinstance", lambda: isinstance(obj=1, class_or_tuple=int)),
    ("all", lambda: all(iterable=[1])),
    ("any", lambda: any(iterable=[1])),
    ("float", lambda: float(x=1)),
    ("range", lambda: range(start=0, stop=2)),
    ("int-x", lambda: int(x="5")),
    ("int-base", lambda: int("ff", base=16)),
    ("int-base0", lambda: int("0x1f", 0)),
    ("int-badbase", lambda: int("5", 1)),
    ("round-ndigits", lambda: round(2.675, ndigits=2)),
    ("round-none", lambda: round(2.5, None)),
    ("round-number", lambda: round(number=2.5)),
    ("sum-start", lambda: sum([1, 2], start=10)),
    ("sum-start-str", lambda: sum([], start="")),
    ("sorted-key-none", lambda: sorted([3, 1, 2], key=None)),
    ("sorted-reverse-none", lambda: sorted([3, 1, 2], reverse=None)),
    ("min-key-none", lambda: min([3, 1], key=None)),
    ("print-sep", lambda: "a-b"),
    ("dict.update-kw", lambda: {"x": 0}),
    ("enumerate-start", lambda: list(enumerate("ab", start=1))),
]
rows = []
for label, f in CASES:
    try:
        rows.append("%s=%r" % (label, f()))
    except Exception as e:
        rows.append("%s=!%s" % (label, e))
print(len(rows))
print("|".join(rows))
"""


@needs_engine
def test_the_keyword_grid_agrees_with_cpython() -> None:
    ref = subprocess.run(
        [sys.executable, "-c", GRID], capture_output=True, text=True, timeout=120
    )
    assert ref.returncode == 0, "the oracle did not run: %s" % ref.stderr[-400:]
    got = engines.run(engines.LYPNING, GRID, timeout=120)
    if got.refused:
        # Two different faults land here and the message has to admit both,
        # because the second is the one this file exists for. Either a construct
        # the grid uses left the subset — in which case the grid is measuring
        # nothing and should be trimmed — or a keyword stopped being read, and
        # the refusal is downstream of that: dropping `sep=` turns
        # `rsplit(sep=',', maxsplit=1)` into `rsplit(None, 1)`, which the subset
        # genuinely does refuse. That is exactly the regression this pins, and it
        # arrives disguised as a coverage problem.
        pytest.fail(
            "lypning REFUSES the grid program. Either a construct left the "
            "subset, or a keyword argument stopped being read and the refusal "
            "is a consequence of the wrong positional value: %s"
            % got.stderr.strip()[:200]
        )
    assert got.returncode == 0, "lypning exited %d: %s" % (
        got.returncode,
        got.stderr.strip()[-400:],
    )

    mine, theirs = got.stdout.splitlines(), ref.stdout.splitlines()
    assert mine[0] == theirs[0], "the grids are different sizes — the program moved"
    a, b = mine[1].split("|"), theirs[1].split("|")
    bad = [(x, y) for x, y in zip(a, b) if x != y]
    assert not bad, "%d of %s cells disagree with CPython; first: %s" % (
        len(bad),
        mine[0],
        ["lypning=%s cpython=%s" % (x, y) for x, y in bad[:6]],
    )
