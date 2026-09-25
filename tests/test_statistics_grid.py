"""`statistics`, as a grid: every program on the binary and on CPython.

`tests/test_base64_grid.py` is the shape this follows. Every row runs from a
FRESH temp cwd (invariant 4) and must end one of exactly two ways:

  * byte-identical stdout AND the same exit code as CPython 3.x, or
  * a clean refusal — exit 90, nothing on stdout, one
    ``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

**What is served, and why it is exact.** ``median``, ``median_low`` and
``median_high`` are ``sorted(data)`` and an index — and for an even-length
``median``, ``(data[i-1] + data[i]) / 2`` with the ORDINARY operators. So the
capability calls the engine's own sort (NaN refuses as ``nan-order``, as
``sorted`` does) and its own ``+`` and ``/``: result types, float overflow to
``inf``, the ``str / int`` TypeError and the wide-int refusal are inherited, not
reimplemented. ``mean`` is EXACT, as CPython's is: over ints and bools an int
when the total divides, otherwise the correctly rounded ``int / int``; with a
float anywhere, ``float(Fraction(total, n))`` from an exact fixed-point sum
rounded once — ``mean([0.1, 0.2, 0.3])`` is ``0.2``, where a float sum gives
``0.20000000000000004`` and even ``fsum/n`` rounds twice. A non-numeric element
raises ``_exact_ratio``'s own TypeError, and a median whose first pair (timsort
compares ``data[1] < data[0]`` first) has no order raises ``<``'s own.

**What is refused.** Empty data (a ``StatisticsError`` this engine has no class
for), a set holding a non-numeric item, a float next to an int past 64 bits,
keywords and a wrong argument count. Every other name on the module refuses as
``module-attr`` out of ``route::MODULE_ATTRS``, in the CORE's walk.

A runtime refusal is not free: past ``io::COMMIT_THRESHOLD`` of output it
cannot be routed onward and the run ends at exit 1 where CPython answers.
``test_a_float_mean_after_a_flush_answers`` is that program.

``test_the_fuzz_agrees_with_cpython`` is the large differential run: seeded
random lists through every served function, each call run on its own.
"""

from __future__ import annotations

import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

S = "import statistics\n"

#: Programs CPython answers and this capability must ANSWER, byte for byte —
#: `test_no_row_of_answers_quietly_became_a_refusal` holds them to that.
SERVED = [
    # The scout grid's served rows, with CPython 3.14.5's answers in the
    # comments (re-probed 2026-09-24).
    S + "xs=[1,2,3,4,10]; print(statistics.mean(xs), statistics.median(xs))",      # 4 3
    S + "print(statistics.mean([1,2]), statistics.mean([1,2,3]), statistics.mean([1,2,4]))",
    S + "print(statistics.mean([-7,-2]), statistics.mean([2**62,2**62,1]), "
        "statistics.mean(range(5)), statistics.mean(iter([3,4])), statistics.mean({5:0,7:0}))",
    S + "print(statistics.mean([True,True]), statistics.mean([True,False,True]))",  # 1 0.666…
    S + "print(statistics.mean([1,2,4])*3, statistics.mean([10**15+1, 10**15+2]))",
    S + "print('%.0f %.2f' % (statistics.mean([230,231,229,240]), "
        "statistics.median([0.5,0.25,0.75,1.0])))",
    S + "print(f\"{statistics.median([3,1,2]):.1f}\", repr(statistics.mean([1,2,3,4])))",
    "import statistics as st; print(st.median([1,3]), st.median([1,2,3]), "
    "st.median([3,1,2,4]), st.median([1.5,2.5]), st.median([1,2.0]))",
    "from statistics import median; print(median([True,False]), median('cab'), "
    "median({3:0,1:0,2:0}), median(x for x in [5,1]))",
    S + "print(statistics.median([1e308,1e308]), statistics.median([2**53+1,2**53+2]), "
        "statistics.median([-0.0,0.0]), statistics.median([1.0,1,True]))",
    S + "print(statistics.median_low([1,2,3,4]), statistics.median_high([1,2,3,4]), "
        "statistics.median_low([5]), statistics.median_high(['a','b']))",
    # The type of mean's answer is the trap: an int when the total divides.
    S + "for xs in ([4], [0], [-3,-3], [1,2,3], [2,2,3,3], [7,8], [-1,0], [0,0,1]):\n"
        "    m = statistics.mean(xs); print(repr(m), type(m).__name__)",
    S + "print(repr(statistics.mean([True])), type(statistics.mean([True, True])).__name__)",
    S + "print(statistics.mean([2**62, 2**62]), statistics.mean([-2**63, -2**63]))",
    S + "print(statistics.mean([2**63-1, 2**63-1, 2**63-1]))",
    S + "print(statistics.mean(range(1, 1001)), statistics.mean(range(0, 1000, 7)))",
    S + "print(statistics.mean({1, 2, 3, 4}), statistics.median({4, 1, 3}))",
    S + "print(statistics.mean((5, 6)), statistics.mean(x*x for x in range(4)))",
    S + "print(statistics.mean([1,2]) == 1.5, statistics.mean([3,3]) == 3, "
        "statistics.median([1,2]) < 2)",
    # median: the odd case returns the ELEMENT, the even case divides
    S + "print(repr(statistics.median([1.5])), repr(statistics.median([2])), "
        "repr(statistics.median([2, 4])), repr(statistics.median([2, 3])))",
    S + "print(statistics.median(['b','a','c']), statistics.median([(2,1),(1,2),(3,0)]))",
    S + "print(statistics.median(b'abc'), statistics.median(range(10)), statistics.median(range(9)))",
    S + "print(statistics.median([-5, 5]), statistics.median([-1, -2]), statistics.median([1, -0.0]))",
    S + "print(statistics.median([2**61, 2**61]), statistics.median([10**18, 10**18+1, 3]))",
    S + "print(statistics.median([float('inf'), 1]), statistics.median([float('-inf'), float('inf'), 0]))",
    S + "print(statistics.median_low([3, 1]), statistics.median_high([3, 1]), "
        "statistics.median_low('dcba'), statistics.median_high('dcba'))",
    S + "print(statistics.median_low([1.0, 1]), statistics.median_high([True, 1, 1.0]))",
    S + "print(statistics.median_low(x for x in [9, 3, 5, 7]), statistics.median_high({2: 'a', 8: 'b'}))",
    # Aliases and bound names
    "from statistics import mean as m, median as md\nprint(m([1, 5]), md([4, 1, 2]))",
    "from statistics import median_low, median_high\nprint(median_low([1, 2]), median_high([1, 2]))",
    "import statistics as s\nf = s.median\nprint(f([3, 1, 2]), list(map(s.mean, [[1, 2], [3]])))",
    S + "fs = [statistics.mean, statistics.median, statistics.median_low]\n"
        "print([f([1, 2]) for f in fs])",
    # The corpus shapes: JSON ints, len() values, Counter values, sorted input
    "import json, statistics\nrows=[json.loads(l) for l in ['{\"t\": 12}', '{\"t\": 30}', '{\"t\": 7}']]\n"
    "ct=[r.get('t') or 0 for r in rows]\n"
    "print('mean completion %.0f tokens' % statistics.mean(ct), statistics.median(ct))",
    "import statistics\nls=[len(l) for l in 'a\\nbbb\\ncc\\ndddd'.splitlines()]\n"
    "print('median', statistics.median(ls), 'mean', statistics.mean(ls))",
    "import statistics\nfrom collections import Counter\nc = Counter('abracadabra')\n"
    "print(statistics.median(c.values()), statistics.mean(c.values()))",
    "import statistics\nlens=[5, 3, 9, 1]\nlens.sort()\n"
    "print('median prog len', statistics.median(lens), 'min', min(lens))",
    S + "b=[3, 10, 4]\nprint('program bytes: mean %.0f median %.0f' % (sum(b)/len(b), statistics.median(b)))",
    S + "xs=[1,2,3]\nprint(round(statistics.mean(xs), 2), int(statistics.median([1, 4])))",
    # Imported and never used — six corpus programs are this.
    S + "print(sorted([3, 1, 2]))",
    # A TypeError CPython raises and this engine raises identically: exit 1,
    # and stdout up to that point.
    S + "print('a')\nprint(statistics.median(['a', 'b']))",
    # mean over a range is `(first + last) / 2`, never a loop; over anything
    # else it streams (one running total, as CPython's `_sum` does)
    S + "print(statistics.mean(range(10**6, -5, -3)), statistics.mean(range(1, 2)), "
        "statistics.mean(range(0, 10, 3)), statistics.mean(range(-2**63, 2**63-1, 2**62)))",
    S + "print(statistics.mean(range(2**62, 2**63-1, 2**61)), statistics.mean(range(9, 0, -1)))",
    S + "print(statistics.mean(x for x in range(10**5)), statistics.mean(iter(range(7))))",
    # a total order the merge sort and timsort agree on: one kind per column
    S + "print(statistics.median([(1,'a'),(1,'b'),(0,'z')]), "
        "statistics.median_low([[1,2],[1],[0,5,6]]), statistics.median([1,2.5,True]))",
    S + "print(statistics.median([None]), statistics.median_low([2**53, 2.0**53, 1.5]))",
]

#: CPython answers each of these (or raises a class this engine cannot name),
#: and the capability declines: exit 90, empty stdout, one stderr line.
REFUSED = [
    # empty data is a StatisticsError
    S + "try: statistics.median([])\nexcept statistics.StatisticsError as e: print('SE', e)",
    S + "try: statistics.mean([])\nexcept ValueError as e: print(type(e).__name__, e)",
    S + "print(statistics.median([]))",
    S + "print(statistics.median_low(x for x in []))",
    S + "print(statistics.median_high({}))",
    S + "print(statistics.mean(range(0)))",
    # a set holding a non-numeric item: WHICH one raises first is set order's
    S + "print(statistics.mean({1, 'a'}))",
    # a float next to an int past 64 bits
    S + "print(statistics.mean([1.5, 2**64]))",
    # NaN in a sort: the result is timsort's, and the engine's sort refuses
    S + "print(statistics.median([float('nan'),1,2]))",
    # a wide total whose division is not exact
    S + "print(statistics.mean([2**64, 1]))",
    S + "print(statistics.median([2**63, 2**63 + 1]))",
    S + "print(statistics.median([2**62, 2**62]))",
    # keywords and argument counts
    S + "print(statistics.mean(data=[1, 2]))",
    S + "print(statistics.median([1, 2], 3))",
    S + "print(statistics.median())",
    # the rest of the module
    S + "print(statistics.pstdev([1,2,3,4]), statistics.fmean([1,2,3]))",
    S + "print(statistics.stdev([1, 2, 3]))",
    S + "print(statistics.mode([1, 1, 2]))",
    "from statistics import fmean\nprint(fmean([1, 2]))",
    "from statistics import StatisticsError\nprint(1)",
    "import statistics as st\nprint(st.quantiles([1, 2, 3, 4]))",
]

#: Exact CPython 3.14.5 answers (stdout, exit, last stderr line), measured
#: 2026-09-25, for what used to refuse at runtime: a float `mean`, a non-numeric
#: item in `mean`, and a median whose FIRST timsort pair has no order.
EXACT = {
    S + "print(statistics.mean([0.1,0.2,0.3]), statistics.mean([1e308,1e308]), "
        "statistics.mean([1e50,1,-1e50]))": ("0.2 1e+308 0.3333333333333333\n", 0, ""),
    S + "print(statistics.mean([-0.0]), statistics.mean([1.0,3.0]), statistics.mean([1,2.0,3]))":
        ("0.0 2.0 2.0\n", 0, ""),
    S + "print(statistics.mean([float('inf'),1]), statistics.mean([float('inf'),float('-inf')]), "
        "statistics.mean([float('nan'),2]))": ("inf nan nan\n", 0, ""),
    S + "print('x')\nprint(statistics.mean([1, 2, 3.5]))": ("x\n2.1666666666666665\n", 0, ""),
    S + "print(repr(statistics.mean([-5e-324, 0])), repr(statistics.mean([5e-324, 0])), "
        "repr(statistics.mean([5e-324, 5e-324, 5e-324, 0])))": ("-0.0 0.0 5e-324\n", 0, ""),
    S + "print(statistics.mean([2**62, 2**62, 0.5]), statistics.mean([True, 0.5]), "
        "statistics.mean({1.5: 0, 2.25: 1}), statistics.mean({0.1, 0.2, 0.3}))":
        ("3.0744573456182584e+18 0.75 1.875 0.2\n", 0, ""),
    S + "print(statistics.mean(x / 3 for x in range(10)), "
        "statistics.mean([1.7976931348623157e308] * 3), statistics.mean([2.5e-308, -1e-320]))":
        ("1.5 1.7976931348623157e+308 1.2499999999994996e-308\n", 0, ""),
    S + "print(statistics.mean([1,'a']))":
        ("", 1, "TypeError: can't convert type 'str' to numerator/denominator"),
    S + "print('a')\nprint(statistics.mean('abc'))":
        ("a\n", 1, "TypeError: can't convert type 'str' to numerator/denominator"),
    S + "print(statistics.mean([1.5, None]))":
        ("", 1, "TypeError: can't convert type 'NoneType' to numerator/denominator"),
    S + "try: statistics.mean([[1], [2]])\nexcept TypeError as e: print(e)":
        ("can't convert type 'list' to numerator/denominator\n", 0, ""),
    S + "print(statistics.median([1, 'a']))":
        ("", 1, "TypeError: '<' not supported between instances of 'str' and 'int'"),
    S + "print(statistics.median_low(['a', 1.5]))":
        ("", 1, "TypeError: '<' not supported between instances of 'float' and 'str'"),
    S + "try: statistics.median_high([None, None])\nexcept TypeError as e: print(e)":
        ("'<' not supported between instances of 'NoneType' and 'NoneType'\n", 0, ""),
    S + "try: statistics.median([(1,), [1], 3])\nexcept TypeError as e: print(e)":
        ("'<' not supported between instances of 'list' and 'tuple'\n", 0, ""),
    S + "print(statistics.median(x for x in [b'a', 'a']))":
        ("", 1, "TypeError: '<' not supported between instances of 'str' and 'bytes'"),
}


#: The medians over items `<` does not TOTALLY order refuse (`Shape` in
#: statistics.rs): the engine's merge sort asks its comparisons in a different
#: order from timsort, so a TypeError names a different pair; a NaN at depth
#: two or more makes `<` no order at all; and the core compares an int past
#: 2**53 with a float through f64. Each row is pinned to CPython 3.14.5's exact
#: stdout and exit code (measured 2026-09-24), and each was a wrong answer or
#: a wrong TypeError on lypning-l before the check.
UNORDERED_MEDIANS = {
    S + "print(repr(statistics.median_low([9007199254740993, 9007199254740992.0])))":
        ("9007199254740992.0\n", 0),
    S + "print(repr(statistics.median_high([9007199254740993, 9007199254740992.0])))":
        ("9007199254740993\n", 0),
    S + "print(repr(statistics.median_low([9.223372036854776e18, 2**63-1])))":
        ("9223372036854775807\n", 0),
    S + "print(repr(statistics.median([2**53+1, 2**53+1.0, 2**53])))":
        ("9007199254740992\n", 0),
    S + "print(repr(statistics.median_low([(9007199254740993,), (9007199254740992.0,)])))":
        ("(9007199254740992.0,)\n", 0),
    S + "print(repr(statistics.median_low([10**16+1, 2**53-1, 1e16, 1e16])))":
        ("1e+16\n", 0),
    S + "n=float('nan')\nprint(statistics.median_low([[[3]],[[1]],[[n]],[[2]]]))":
        ("[[nan]]\n", 0),
    S + "n=float('nan')\nprint(statistics.median_high([(0,(3,)),(0,(1,)),(0,(n,)),(0,(2,))]))":
        ("(0, (2,))\n", 0),
    S + "n=float('nan')\nprint(statistics.median([[[3]],[[1]],[[n]],[[2]],[[0]]]))":
        ("[[nan]]\n", 0),
    S + "try: statistics.median([3, 1, None, 2])\nexcept TypeError as e: print('E', e)":
        ("E '<' not supported between instances of 'NoneType' and 'int'\n", 0),
    S + "try: statistics.median_low({1, None})\nexcept TypeError as e: print('E', e)":
        ("E '<' not supported between instances of 'int' and 'NoneType'\n", 0),
    S + "print(statistics.median({(1,2),(1,None)}))": ("", 1),
    S + "print(statistics.median([2, 1, 'a']))": ("", 1),
}
REFUSED += list(UNORDERED_MEDIANS)


@pytest.mark.skipif(sys.version_info[:3] != (3, 14, 5), reason="pinned to CPython 3.14.5")
@pytest.mark.parametrize("program", list(UNORDERED_MEDIANS), ids=range(len(UNORDERED_MEDIANS)))
def test_the_unordered_median_rows_are_pinned_to_cpython(program: str) -> None:
    ref = _run([sys.executable], program)
    assert (ref.stdout, ref.returncode) == UNORDERED_MEDIANS[program]


#: A median over sets. CPython orders sets by SUBSET, a partial order the
#: engine's sort does not implement, so the medians refuse any set-like element
#: (set, frozenset, dict view, or one inside a tuple or list) rather than raise
#: a TypeError CPython never raises. Each row is pinned to CPython 3.14.5's
#: exact stdout and exit code, measured 2026-09-24.
SET_MEDIANS = {
    S + "print(statistics.median_low([{2},{1,2}]))": ("{2}\n", 0),
    S + "print(statistics.median_low([{1},{2}]), statistics.median_high([{1},{2}]))":
        ("{1} {2}\n", 0),
    S + "print(statistics.median([{2},{1}]))": ("", 1),
    S + "print(statistics.median_high([({2},),({1,2},)]))": ("({1, 2},)\n", 0),
    S + "print(statistics.median_low([frozenset({2}),frozenset({1,2})]))":
        ("frozenset({2})\n", 0),
    S + "print(statistics.median_low([{1:0}.keys(), {1:0,2:0}.keys()]))":
        ("dict_keys([1])\n", 0),
    S + "print('x')\nprint(statistics.median([[{1}], [{2}]]))": ("x\n", 1),
}
REFUSED += list(SET_MEDIANS)


@pytest.mark.skipif(sys.version_info[:3] != (3, 14, 5), reason="pinned to CPython 3.14.5")
@pytest.mark.parametrize("program", list(SET_MEDIANS), ids=range(len(SET_MEDIANS)))
def test_the_set_median_rows_are_pinned_to_cpython(program: str) -> None:
    ref = _run([sys.executable], program)
    assert (ref.stdout, ref.returncode) == SET_MEDIANS[program]


#: The CORE routes each of these to CPython: its first blocker is `module:
#: import statistics`, which lypning-l answers, but the attribute is one no rung
#: serves, and `route::MODULE_ATTRS` says so in the core's own walk.
ROUTED_PAST_LYPNING_L = [
    S + "print(statistics.stdev([1, 2]))",
    "from statistics import fmean\nprint(fmean([1, 2]))",
    "import statistics as st\nprint(st.pvariance([1, 2]))",
    "from statistics import StatisticsError\nprint(1)",
]
# `except statistics.StatisticsError:` is NOT among them: the handler's type is
# evaluated only when an exception reaches it, in CPython and here alike, so
# the program runs on lypning-l and a handler that is reached refuses there
# (`module-attr`, at runtime) — exit 90, never an answer.

#: …and each of these to lypning-l.
ROUTED_TO_LYPNING_L = [
    S + "print(statistics.median([1, 2]))",
    "from statistics import mean, median\nprint(mean([1, 2]), median([3]))",
    "import statistics as st\nprint(st.median_low([1, 2]), st.median_high([1, 2]))",
    S + "print(1)",
]


def _spectrum(binary: Path) -> dict | None:
    """What ``binary`` says it is, or ``None`` if it will not say."""
    try:
        out = subprocess.run([str(binary), "route", "--spectrum"],
                             capture_output=True, text=True, timeout=60)
    except OSError:
        return None
    try:
        import json
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def _current(engine: str, cap: str) -> Path | None:
    """A built ``engine`` that carries THIS tree's capability table — an older
    binary would turn every row into a green skip measuring nothing."""
    target = {engines.LYPNING: paths.RUST_DIR / "target" / "release" / "lypning",
              engines.LYPNING_L: paths.RUST_DIR / "target" / "variant-l" / "release" / "lypning"}
    found = engines.find(engine)
    for cand in ([Path(found)] if found else []) + [target[engine]]:
        if not cand.is_file():
            continue
        table = _spectrum(cand)
        if table is None or table.get("self") != engine:
            continue
        if table.get("self_caps") != list(engines.VARIANT_CAPS.get(engine, ())):
            continue
        if any(row.get("cap") == cap for row in table.get("caps", [])):
            return cand
    return None


BINARY = _current(engines.LYPNING_L, "cap-statistics")
CORE = _current(engines.LYPNING, "cap-statistics")

needs_l = pytest.mark.skipif(
    BINARY is None,
    reason="no lypning-l carrying cap-statistics is built (lypning build --rust)",
)
needs_core = pytest.mark.skipif(
    CORE is None or BINARY is None,
    reason="routing needs the CORE that routes and the lypning-l it routes to",
)


def _run(argv: list[str], program: str) -> subprocess.CompletedProcess:
    """One program, in a temp cwd of its own — invariant 4."""
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True, text=True,
                              cwd=d, timeout=120)


def _refusal_problem(got: subprocess.CompletedProcess) -> str | None:
    """``None`` if this is a clean exit-90 refusal, else what is wrong with it."""
    if got.returncode != engines.UNSUPPORTED_EXIT:
        return "exit %d, not %d" % (got.returncode, engines.UNSUPPORTED_EXIT)
    if got.stdout != "":
        return "stdout was not empty: %r" % got.stdout[:120]
    head = "%s: unsupported: " % engines.LYPNING_L
    line = got.stderr.strip()
    if not line.startswith(head) or "\n" in line:
        return "stderr was %r, expected one %r line" % (line[:160], head)
    return None


def _agrees(program: str) -> None:
    got = _run([str(BINARY)], program)
    if got.returncode == engines.UNSUPPORTED_EXIT:
        problem = _refusal_problem(got)
        assert problem is None, "%s\n  program: %r" % (problem, program)
        pytest.skip("lypning-l refuses this row: %s" % got.stderr.strip()[:160])
    ref = _run([sys.executable], program)
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "lypning-l disagrees with CPython.\n"
        "  program:  %r\n"
        "  lypning-l: %r exit %d %s\n"
        "  cpython:   %r exit %d %s"
        % (program, got.stdout, got.returncode, got.stderr.strip()[-200:],
           ref.stdout, ref.returncode, ref.stderr.strip()[-200:])
    )


@needs_l
@pytest.mark.parametrize("program", SERVED, ids=range(len(SERVED)))
def test_the_statistics_grid_agrees_with_cpython(program: str) -> None:
    _agrees(program)


@needs_l
@pytest.mark.parametrize("program", REFUSED, ids=range(len(REFUSED)))
def test_the_surface_outside_the_slice_refuses_rather_than_guesses(program: str) -> None:
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got)
    assert problem is None, (
        "this program must refuse, not answer: %s\n  program: %r\n  stdout: %r\n  stderr: %r"
        % (problem, program, got.stdout[:200], got.stderr.strip()[:200])
    )


@needs_l
def test_no_row_of_answers_quietly_became_a_refusal() -> None:
    """A file of green skips measures nothing: every SERVED row must RUN."""
    refused = [p for p in SERVED
               if _run([str(BINARY)], p).returncode == engines.UNSUPPORTED_EXIT]
    assert refused == [], "%d rows CPython answers are refused here: %r" % (
        len(refused), refused[:4])


@needs_core
@pytest.mark.parametrize("program", ROUTED_PAST_LYPNING_L,
                         ids=range(len(ROUTED_PAST_LYPNING_L)))
def test_an_unserved_name_routes_to_cpython_from_the_core(program: str) -> None:
    route = engines.route(program, binary=CORE)
    assert route.engine == engines.CPYTHON, (
        "routed to %r, which cannot run it\n  program: %r\n  first blocker: %s: %s"
        % (route.engine, program, route.kind, route.detail)
    )


@needs_core
@pytest.mark.parametrize("program", ROUTED_TO_LYPNING_L,
                         ids=range(len(ROUTED_TO_LYPNING_L)))
def test_a_served_name_routes_to_lypning_l(program: str) -> None:
    route = engines.route(program, binary=CORE)
    assert route.engine == engines.LYPNING_L, (
        "routed to %r, not lypning-l\n  program: %r\n  blocker: %s: %s"
        % (route.engine, program, route.kind, route.detail)
    )


def _fuzz_lists(rng: random.Random, count: int) -> list[str]:
    """Literal data a program can spell: ints (small, near 2**53, near the i64
    edge), bools, floats including signed zeros and infinities, and strings.
    Half the lists are ints and bools only, which is what `mean` serves."""
    def num(floats: bool) -> str:
        r = rng.random() if floats else rng.random() * 0.7
        if r < 0.45:
            return str(rng.randint(-50, 50))
        if r < 0.55:
            return rng.choice(["True", "False"])
        if r < 0.62:
            return str(rng.randint(-10**15, 10**15))
        if r < 0.66:
            return str(rng.choice([1, -1]) * (2**53 + rng.randint(-3, 3)))
        if r < 0.70:
            return str(rng.choice([1, -1]) * (2**62 + rng.randint(-3, 3)))
        if r < 0.90:
            return repr(rng.uniform(-100, 100))
        return rng.choice(["0.0", "-0.0", "0.1", "1e308", "-1e308", "float('inf')",
                           "5e-324", "0.5", "2.5"])
    out = []
    for _ in range(count):
        n = rng.randint(1, 9)
        kind = rng.random()
        if kind < 0.1:
            out.append(repr("".join(rng.choice("abcde") for _ in range(n))))
        else:
            floats = kind >= 0.55
            out.append("[" + ", ".join(num(floats) for _ in range(n)) + "]")
    return out


FUNCS = ("mean", "median", "median_low", "median_high")

_TRY = ("try:\n    r = %s\n    print(repr(r), type(r).__name__)\n"
        "except (TypeError, ValueError, OverflowError) as e:\n"
        "    print(type(e).__name__)\n")


@needs_l
@pytest.mark.parametrize("seed", range(4))
def test_the_fuzz_agrees_with_cpython(seed: int) -> None:
    """Each datum through each served function: one CPython run answers the
    whole batch, one line per call, and each call runs on lypning-l on its own,
    so one refused float mean cannot hide a disagreement next to it. An answer
    must agree; a refusal must be clean."""
    rng = random.Random(seed)
    calls = ["statistics.%s(%s)" % (f, d) for d in _fuzz_lists(rng, 150) for f in FUNCS]
    ref = _run([sys.executable], S + "".join(_TRY % c for c in calls))
    assert ref.returncode == 0, ref.stderr[-400:]
    want = ref.stdout.splitlines()
    assert len(want) == len(calls)
    served = 0
    for call, line in zip(calls, want):
        got = _run([str(BINARY)], S + _TRY % call)
        if got.returncode == engines.UNSUPPORTED_EXIT:
            problem = _refusal_problem(got)
            assert problem is None, "%s\n  call: %r" % (problem, call)
            continue
        served += 1
        assert (got.stdout, got.returncode) == (line + "\n", 0), (
            "lypning-l disagrees with CPython on %s: %r vs %r" % (call, got.stdout, line))
    assert served >= len(calls) // 2, "only %d of %d fuzz calls were served" % (served, len(calls))


@needs_l
def test_mean_over_a_huge_range_answers_without_materializing_it() -> None:
    """CPython streams `mean(range(10**10))` through `_sum` for about twelve
    minutes and prints `4999999999.5` (= float(Fraction(10**10 - 1, 2))); the
    capability used to collect 10**10 values and die at exit 137."""
    got = _run([str(BINARY)], S + "print(statistics.mean(range(10**10)))")
    assert (got.stdout, got.returncode) == ("4999999999.5\n", 0)


def _last(err: str) -> str:
    lines = err.strip().splitlines()
    return lines[-1] if lines else ""


@pytest.mark.skipif(sys.version_info[:3] != (3, 14, 5), reason="pinned to CPython 3.14.5")
@pytest.mark.parametrize("program", list(EXACT), ids=range(len(EXACT)))
def test_the_exact_rows_are_pinned_to_cpython(program: str) -> None:
    ref = _run([sys.executable], program)
    assert (ref.stdout, ref.returncode, _last(ref.stderr)) == EXACT[program]


@needs_l
@pytest.mark.parametrize("program", list(EXACT), ids=range(len(EXACT)))
def test_the_exact_rows_answer_byte_for_byte(program: str) -> None:
    got = _run([str(BINARY)], program)
    assert (got.stdout, got.returncode, _last(got.stderr)) == EXACT[program], got.stderr[-300:]


#: Past `io::COMMIT_THRESHOLD` (8 MiB) of stdout a runtime refusal can no
#: longer be routed onward: it ends at exit 1 where CPython answers. These used
#: to be exactly that, through lypning-l and through the chain alike. A
#: `statistics` program is now held reversible (`route::hint_held`, so an
#: uncaught error CPython would end with a suggestion can still refuse), so
#: pinned lypning-l refuses cleanly at the threshold and the CHAIN answers.
FLUSHED = 90_000  # lines of 101 bytes: 9,090,000 B, past the threshold


@needs_l
@pytest.mark.parametrize("tail,want,code,err", [
    ("print(statistics.mean([0.5, 1]))", "0.75\n", 0, ""),
    ("print(statistics.median([1, 'a']))", "", 1,
     "TypeError: '<' not supported between instances of 'str' and 'int'"),
    ("print(statistics.mean([2, 'a']))", "", 1,
     "TypeError: can't convert type 'str' to numerator/denominator"),
])
def test_a_float_mean_after_a_flush_answers(tail: str, want: str, code: int, err: str) -> None:
    program = S + "for i in range(%d): print('x' * 100)\n" % FLUSHED + tail
    head = ("x" * 100 + "\n") * FLUSHED
    src = str(Path(engines.__file__).resolve().parents[1])
    env = dict(os.environ, PYTHONPATH=src)
    for argv in ([str(BINARY)], [sys.executable, "-m", "lypning", "run"]):
        with tempfile.TemporaryDirectory() as d:
            got = subprocess.run(argv + ["-c", program], capture_output=True, text=True,
                                 cwd=d, timeout=300, env=env)
        if argv == [str(BINARY)]:
            line = got.stderr.strip()
            assert (got.returncode, got.stdout) == (engines.UNSUPPORTED_EXIT, ""), got.stderr[-300:]
            assert "\n" not in line and line.startswith("%s: unsupported: name-hint: " % engines.LYPNING_L)
            continue
        assert (got.returncode, _last(got.stderr)) == (code, err), (argv, got.stderr[-300:])
        assert got.stdout == head + want, (argv, len(got.stdout), got.stdout[-60:])

