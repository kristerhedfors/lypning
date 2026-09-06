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

and the reader that DID handle both spellings took them in the wrong order:
`args.get(i).cloned().or_else(|| kwget(&kw, "name"))` discards the keyword
whenever a positional is also present, so the same parameter given twice
answered at exit 0 with the positional's value —

    b"abcd".hex("-", sep=":")  ->  '61-62-63-64'  (CPython: TypeError)
    round(2.5, number=9)       ->  2              (CPython: TypeError)
    open(p, "r", mode="w")     ->  opened for READ (CPython: TypeError)

— which is what a one-liner looks like after an edit added the keyword and left
the positional behind, answered with the value the edit meant to replace.

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

import re
import subprocess
import sys
from pathlib import Path

import pytest

from lypning import engines

RUST = Path(__file__).resolve().parents[1] / "src" / "lypning" / "assets" / "rust" / "src"

#: The spelling this whole defect class is made of. `Option::or_else` does not
#: run when the first option is `Some`, so reading a positional-or-keyword
#: parameter this way DISCARDS the keyword whenever both are given.
DISCARDS_THE_KEYWORD = re.compile(r"or_else\(\|\|\s*kw(get|val)\(")

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
    # THE SAME PARAMETER TWICE — once by position, once by name. Every reader
    # above was `args.get(i).or_else(|| kwget(kw, name))`, and `or_else` does
    # not run when the positional is there, so the keyword was DROPPED and the
    # call answered at exit 0 with the value the caller had just replaced. That
    # is what an edited one-liner looks like: `hex('-')` grown a `sep=` without
    # the positional coming out. CPython refuses rather than choose, and the
    # message is its own — the bare function name and the 1-based position.
    #
    # Only the one-positional shape is here. CPython checks the TOTAL argument
    # count first, so `split(",", 1, maxsplit=1)` reports `takes at most 2
    # arguments (3 given)` where this reports the duplicate: same exit code,
    # same empty stdout, a sentence `conformance.classify` does not compare and
    # this grid would.
    ("str.split-dup", lambda: "a,b".split(",", sep=",")),
    ("str.rsplit-dup", lambda: "a,b".rsplit(",", sep=",")),
    ("str.encode-dup", lambda: "ab".encode("utf-8", encoding="utf-8")),
    ("bytes.decode-dup", lambda: b"ab".decode("utf-8", encoding="utf-8")),
    ("bytes.split-dup", lambda: b"a,b".split(b",", sep=b",")),
    ("bytes.hex-dup", lambda: b"ab".hex("-", sep="-")),
    ("bytes.hex-per-dup", lambda: b"abcd".hex(sep="-", bytes_per_sep=2)),
    ("round-dup", lambda: round(2.5, number=2.5)),
    # The one duplicate on position TWO that CPython's arity rule lets through,
    # because `bytes` takes three positionals and this call passes three
    # arguments in total.
    ("bytes-encoding-dup", lambda: bytes("ab", "utf-8", encoding="utf-8")),
    # `str.encode(errors=…)`, and the POSITIONAL form, which is what the second
    # argument is. Both were parsed and then discarded, so this arm always
    # encoded strictly and `'héllo'.encode('ascii', 'ignore')` raised
    # UnicodeEncodeError at exit 1 where CPython answers `b'hllo'`. Exit 1 is
    # the program's own number: the dispatcher hands it back and the caller
    # never learns that another engine would have answered.
    ("str.encode-ignore", lambda: "héllo".encode("ascii", "ignore")),
    ("str.encode-ignore-kw", lambda: "héllo".encode("ascii", errors="ignore")),
    ("str.encode-replace", lambda: "héllo".encode("ascii", "replace")),
    ("str.encode-strict", lambda: "abc".encode("ascii", "strict")),
    # UTF-8 encodes every str, so CPython never consults the handler — which is
    # why an unknown handler name is not a LookupError here either.
    ("str.encode-utf8-errors", lambda: "héllo".encode(errors="ignore")),
    ("str.encode-unused-handler", lambda: "abc".encode("ascii", "bogus")),
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


def test_no_arm_reads_a_parameter_by_position_or_else_by_name() -> None:
    """`crate::args::bind` is the only way to read a positional-or-keyword
    parameter, and this is what keeps it that way.

    The grid above can only see the parameters someone thought to add a row
    for, and the defect it pins is not a property of any one method — it is a
    property of an IDIOM that is three characters shorter than the correct one
    and reads as if it were the same thing. Sixteen call sites had it. A test
    over the source is the only thing that notices the seventeenth, because a
    new one arrives with its own new method and no row here."""
    offenders = []
    for path in sorted(RUST.glob("*.rs")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if DISCARDS_THE_KEYWORD.search(line) and not line.lstrip().startswith("///"):
                offenders.append("%s:%d: %s" % (path.name, n, line.strip()))
    assert not offenders, (
        "a parameter is read by position OR ELSE by name, which drops the "
        "keyword when both are given; use `crate::args::bind(args, &kw, pos, "
        "name, func)` instead:\n  " + "\n  ".join(offenders)
    )
