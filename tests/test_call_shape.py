"""Malformed calls: lypning must never ANSWER where CPython raises.

Extra positional arguments were dropped in silence and missing ones defaulted,
so a call with the wrong shape came back with a plausible result instead of a
`TypeError`:

    'ab'.strip('a', 'b')   ->  'b'      (the second argument won)
    [1].insert(0)          ->  inserts None into the list
    [1].append(1, 2)       ->  appends 1, drops 2
    {}.get()               ->  None
    {}.get('a', 1, 2)      ->  1
    len([1], [2])          ->  1
    abs(1, 2)              ->  1
    chr(65, 66)            ->  'A'
    divmod(1, 2, 3)        ->  (0, 1)

Nineteen of those, every one at exit 0. `[1].insert(0)` is the worst: it does
not merely answer wrongly, it puts a `None` in the list and carries on.

**This asserts the outcome shape, not the message.** Fifty-five of these still
differ from CPython in wording — `str.strip() takes at most 1 argument (2 given)`
against `strip expected at most 1 argument, got 2` — and that is a real but much
smaller gap: the caller gets an error either way and learns the same thing. What
must never differ is whether an error happened at all, so that is what is pinned.
Pinning the text here would freeze fifty-five strings that are allowed to be
wrong, and would fail for a reason nobody should have to act on.

The arity tables in `builtins.rs` and `methods.rs` were derived by calling
CPython 3.11 with 0..6 arguments — and the first derivation was WRONG, because
`format(1, 1)` fails with "argument 2 must be str, not int", a TYPE error whose
text contains the word "argument". An oracle only answers the question you
actually asked it; the second pass used type-correct fillers.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from lypning import engines

needs_engine = pytest.mark.skipif(
    engines.find_lypning() is None, reason="the Rust core is not built"
)

#: Each case records only `ok` or `raised`, never the message. A thunk per case
#: so one raising does not stop the sweep.
GRID = r"""
CASES = [
    ("str.replace-few", lambda: "ab".replace("a")),
    ("str.replace-many", lambda: "ab".replace("a", "b", 1, 1)),
    ("str.split-many", lambda: "ab".split(",", 1, 2)),
    ("str.find-none", lambda: "ab".find()),
    ("str.find-many", lambda: "ab".find("a", 1, 2, 3)),
    ("str.strip-many", lambda: "ab".strip("a", "b")),
    ("str.count-none", lambda: "ab".count()),
    ("str.index-none", lambda: "ab".index()),
    ("str.join-none", lambda: "ab".join()),
    ("str.startswith-none", lambda: "ab".startswith()),
    ("list.append-none", lambda: [1].append()),
    ("list.append-many", lambda: [1].append(1, 2)),
    ("list.insert-few", lambda: [1].insert(0)),
    ("list.pop-many", lambda: [1].pop(0, 1)),
    ("list.index-none", lambda: [1].index()),
    ("dict.get-none", lambda: {}.get()),
    ("dict.get-many", lambda: {}.get("a", 1, 2)),
    ("dict.setdefault-none", lambda: {}.setdefault()),
    ("dict.pop-none", lambda: {}.pop()),
    ("len-none", lambda: len()),
    ("len-many", lambda: len([1], [2])),
    ("abs-none", lambda: abs()),
    ("abs-many", lambda: abs(1, 2)),
    ("chr-none", lambda: chr()),
    ("chr-many", lambda: chr(65, 66)),
    ("ord-none", lambda: ord()),
    ("ord-many", lambda: ord("a", "b")),
    ("hex-none", lambda: hex()),
    ("repr-none", lambda: repr()),
    ("repr-many", lambda: repr(1, 2)),
    ("bool-many", lambda: bool(1, 2)),
    ("round-many", lambda: round(1, 2, 3)),
    ("sum-many", lambda: sum([1], 2, 3)),
    ("sorted-many", lambda: sorted([1], [2])),
    ("divmod-few", lambda: divmod(1)),
    ("divmod-many", lambda: divmod(1, 2, 3)),
    ("int-many", lambda: int("1", "2", "3")),
    ("isinstance-few", lambda: isinstance(1)),
    ("range-none", lambda: range()),
    # …and the well-formed calls beside them, so a check that simply refused
    # everything would fail here instead of looking like a fix.
    ("ok-replace", lambda: "ab".replace("a", "b")),
    ("ok-replace-count", lambda: "ab".replace("a", "b", 1)),
    ("ok-split", lambda: "a,b".split(",")),
    ("ok-split-max", lambda: "a,b,c".split(",", 1)),
    ("ok-strip", lambda: " a ".strip()),
    ("ok-strip-chars", lambda: "xax".strip("x")),
    ("ok-find3", lambda: "abc".find("b", 0, 3)),
    ("ok-insert", lambda: [1].insert(0, 9)),
    ("ok-pop", lambda: [1].pop()),
    ("ok-index3", lambda: [1, 2, 1].index(1, 1)),
    ("ok-get", lambda: {}.get("a")),
    ("ok-get-default", lambda: {}.get("a", 1)),
    ("ok-len", lambda: len([1])),
    ("ok-round1", lambda: round(1.5)),
    ("ok-round2", lambda: round(1.55, 1)),
    ("ok-round-kw", lambda: round(number=2.5)),
    ("ok-sum", lambda: sum([1], 2)),
    ("ok-format2", lambda: format(3.5, ".1f")),
    ("ok-int2", lambda: int("ff", 16)),
    ("ok-range3", lambda: range(0, 4, 2)),
    ("ok-divmod", lambda: divmod(7, 2)),
    ("ok-isinstance", lambda: isinstance(1, int)),
]
rows = []
for label, f in CASES:
    try:
        f()
        rows.append(label + "=ok")
    except Exception:
        rows.append(label + "=raised")
print(len(rows))
print("|".join(rows))
"""


@needs_engine
def test_no_malformed_call_is_answered_that_cpython_rejects() -> None:
    ref = subprocess.run(
        [sys.executable, "-c", GRID], capture_output=True, text=True, timeout=120
    )
    assert ref.returncode == 0, "the oracle did not run: %s" % ref.stderr[-400:]
    got = engines.run(engines.LYPNING, GRID, timeout=120)
    if got.refused:
        pytest.fail(
            "lypning REFUSES the grid program — a construct it uses left the "
            "subset, so this is measuring nothing: %s" % got.stderr.strip()[:200]
        )
    assert got.returncode == 0, "lypning exited %d: %s" % (
        got.returncode,
        got.stderr.strip()[-400:],
    )

    mine, theirs = got.stdout.splitlines(), ref.stdout.splitlines()
    assert mine[0] == theirs[0], "the grids are different sizes — the program moved"
    a, b = mine[1].split("|"), theirs[1].split("|")
    answered = [x for x, y in zip(a, b) if x != y and x.endswith("=ok")]
    refused = [x for x, y in zip(a, b) if x != y and x.endswith("=raised")]
    assert not answered, (
        "lypning ANSWERED %d malformed call(s) CPython rejects — the silent "
        "wrong answers this file exists for: %s" % (len(answered), answered[:8])
    )
    assert not refused, (
        "lypning rejected %d well-formed call(s) CPython accepts — an arity "
        "bound is too tight: %s" % (len(refused), refused[:8])
    )
