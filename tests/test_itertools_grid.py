"""`itertools`, as a grid: every program on the binary and on CPython.

`tests/test_hashlib_grid.py` is the shape this follows. Every row runs from a
FRESH temp cwd (invariant 4) and must end one of exactly two ways:

  * the same stdout, exit code and LAST stderr line (the exception itself —
    CPython prints frames this engine does not) as the reference CPython, or
  * a clean refusal — exit 90, nothing on stdout, one
    ``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

**What is served:** `itertools.product` and `itertools.combinations`, and
nothing else on the module (`route::MODULE_ATTRS`, `itertools.rs:SERVED`).

**The two properties a naive port gets wrong, pinned in `DRAINED` and
`LAZY`.** The inputs are drained at CONSTRUCTION, as CPython's
`PySequence_Tuple` does — a generator handed to `product` is empty afterwards,
and a list appended to after the call changes nothing. The output is LAZY, an
index counter — `next(product(range(10**6), repeat=3))` answers at once.

**The object is `Value::IterObj`, not a new `Value` variant** (the defect
`docs/HILLCLIMB.md` iterations 74, 76 and 77 paid for). `THE_SHAPE` runs it
through the arms a variant would have had to remember: `bool`, `==`, `is`,
`iter()`, `next()` with a default, `in`, `sorted`, `max`, `zip`, `enumerate`,
`map`, `set`, `json.dumps` of what it yields, indexing, `+` and attribute
access — the last three raising CPython's own message naming
`'itertools.product'`.

**What refuses, and must keep refusing** (`REFUSED`): the object's `repr`
(a heap address), `len()` and `type()` of it (an iterator-type name), `hash()`,
an integer past 64 bits (CPython's `OverflowError` names a C type), a set as an
input (`set-order`), a keyword `combinations` does not bind, `mro()` of the
class, an uncaught NameError (CPython's last line carries a suggestion), and
every name on the module outside the two.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

I = "import itertools\n"

BASICS = [
    I + "print(list(itertools.product('ab', range(2))))",
    I + "print(list(itertools.product()), list(itertools.product([], [1])))",
    I + "print(list(itertools.product([1, 2], repeat=2)))",
    I + "print(list(itertools.product('ab', repeat=0)), list(itertools.product(repeat=2)))",
    I + "print(len(list(itertools.product(range(3), range(4), repeat=2))))",
    I + "print(list(itertools.product('ab', repeat=True)), list(itertools.product('ab', repeat=False)))",
    I + "print(list(itertools.product(*[])), list(itertools.product(*['ab', 'cd'])))",
    I + "print(list(itertools.product(**{'repeat': 2})))",
    I + "print(len(list(itertools.product(repeat=1, *['a']))))",
    I + "print(list(itertools.product({'a': 1, 'b': 2})))",
    I + "print(list(itertools.product([[1], [2]], repeat=2)))",
    I + "print([''.join(p) for p in itertools.product('01', repeat=3)])",
    I + "print(len(list(itertools.product(range(10), repeat=5))))",
    I + "print(list(itertools.product(iter([1, 2]), iter('ab'))))",
    I + "print(list(itertools.product(range(3), [])), list(itertools.product([], repeat=0)))",
    # `repeat=0` is no pools: CPython never touches an argument — a
    # non-iterable is not an error and a generator is not consumed.
    I + "print(list(itertools.product(5, repeat=0)))",
    I + "g = (x for x in [1, 2, 3])\nprint(list(itertools.product(g, repeat=0)), list(g))",
    I + "g = (print('side') or x for x in [1])\nprint(list(itertools.product(g, 'ab', None, repeat=0)), list(g))",
    I + "print(list(itertools.combinations(range(4), 2)))",
    I + "print(list(itertools.combinations('ab', 0)), list(itertools.combinations('ab', 5)))",
    I + "print(list(itertools.combinations(iterable='abc', r=2)))",
    I + "print(list(itertools.combinations('abc', r=2)))",
    I + "print(list(itertools.combinations('abcd', 3)), list(itertools.combinations([], 0)))",
    I + "print(list(itertools.combinations(range(3), r=True)))",
    I + "print(list(itertools.combinations(range(5), 5)), list(itertools.combinations(range(5), 4)))",
    I + "print(list(itertools.combinations([1, 1, 2], 2)))",
    I + "print(len(list(itertools.combinations(range(20), 10))))",
    I + "for i, (a, b) in enumerate(itertools.combinations(range(1, 6), 2)):\n    print(i, a, b, a * b)",
    "from itertools import product, combinations as C\n"
    "print(list(product('ab', 'c')), list(C('abc', 2)))",
    "import itertools as it\nprint(list(it.product([0, 1], repeat=3)))",
    I + "f = itertools.product\nprint(list(f('ab')))",
    I + "print(itertools.product is itertools.product)",
]

#: Inputs are read ONCE, when the object is built — and not after.
DRAINED = [
    I + "g = (i for i in range(3))\np = itertools.product(g, 'x')\nprint(list(g))\nprint(list(p))",
    I + "x = [1, 2]\np = itertools.product(x, x)\nx.append(3)\nprint(len(list(p)))",
    I + "x = [1, 2, 3]\nc = itertools.combinations(x, 2)\nx.clear()\nprint(list(c))",
    I + "it = iter([1, 2, 3])\nprint(list(itertools.product(it, it)))",
    I + "it = iter(range(5))\nnext(it)\nc = itertools.combinations(it, 2)\nprint(list(it), list(c))",
    I + "d = {'a': 1}\np = itertools.product(d)\nd['b'] = 2\nprint(list(p))",
]

#: Results are produced one index step at a time.
LAZY = [
    I + "p = itertools.product(range(10**6), repeat=3)\nprint(next(p), next(p))",
    I + "c = itertools.combinations(range(10**5), 3)\nprint(next(c), next(c), next(c))",
    I + "p = itertools.combinations([1, 2, 3], 2)\nprint(next(p)); print(next(p)); print(next(p))\n"
        "print(next(p, 'done'), next(p, 'done'))",
    I + "p = itertools.product('ab')\nprint(iter(p) is p, next(p), list(p), list(p))",
    # 10**6, not 10**9: both engines DRAIN the range first (CPython's tuple()),
    # so 10**9 measured the host's memory, and timed out under suite load
    I + "for t in itertools.product(range(10**6), repeat=2):\n    print(t)\n    if t[1] == 2:\n        break",
]

THE_SHAPE = [
    I + "print(bool(itertools.product('a')), itertools.product('a') == itertools.product('a'))",
    I + "p = itertools.product('a')\nprint(p == p, p is p, p != p)",
    I + "print('x' in itertools.product('x'), ('x',) in itertools.product('x'))",
    I + "print(sorted(itertools.product([2, 1], [1])), max(itertools.combinations([3, 1, 2], 2)))",
    I + "print(sum(1 for _ in itertools.product(range(3), repeat=3)))",
    I + "print(list(zip(itertools.product('ab'), 'xyz')))",
    I + "print(list(enumerate(itertools.combinations('abc', 2))))",
    I + "print(list(map(sum, itertools.combinations([1, 2, 3, 4], 2))))",
    I + "print([a + b for a, b in itertools.product('ab', 'cd')])",
    I + "print(sorted(set(itertools.combinations([1, 2, 3], 2))))",
    I + "print(set(itertools.product([1], [2])))",
    I + "import json\nprint(json.dumps(list(itertools.combinations([1, 2, 3], 2))))",
    I + "print(tuple(itertools.product('ab')), any(itertools.product('a')), all(itertools.product([])))",
    I + "print([c for c in itertools.combinations(range(4), 2) if sum(c) > 2])",
    I + "print(dict(itertools.product('ab', [1])))",
    I + "a, b = itertools.combinations('xyz', 2)[0] if False else next(itertools.combinations('xyz', 2))\nprint(a, b)",
    # CPython's own messages, caught and printed: the type name is its tp_name
    I + "p = itertools.product('a')\n"
        "for f in [lambda: p[0], lambda: p.foo, lambda: p + 1,\n"
        "          lambda: itertools.combinations('a', 1) + 1,\n"
        "          lambda: itertools.combinations('a', 1).foo, lambda: itertools.product.foo]:\n"
        "    try:\n        f()\n    except (TypeError, AttributeError) as e:\n"
        "        print(type(e).__name__, e)",
]

#: The argument checks, in CPython's order: every one of these is CPython's own
#: text, caught and printed, so a drifting message is a stdout difference.
ERRORS = [
    I + "try:\n    itertools.product(repeat=%s)\nexcept (TypeError, ValueError) as e:\n"
        "    print(type(e).__name__, e)" % r
    for r in ("1.5", "None", "'a'", "-1", "-2")
] + [
    I + "try:\n    itertools.product('a', repeat=2, foo=1)\nexcept TypeError as e:\n    print(e)",
    I + "try:\n    itertools.product('a', None)\nexcept TypeError as e:\n    print(e)",
    I + "try:\n    itertools.product(1)\nexcept TypeError as e:\n    print(e)",
    # the repeat is checked BEFORE any iterable is drained
    I + "g = iter([1, 2])\ntry:\n    itertools.product(g, repeat=-1)\nexcept ValueError as e:\n"
        "    print(e)\nprint(list(g))",
] + [
    I + "try:\n    itertools.combinations(%s)\nexcept (TypeError, ValueError) as e:\n"
        "    print(type(e).__name__, e)" % a
    for a in ("'abc'", "", "r=2", "'abc', -1", "'abc', 1.0", "'abc', None", "'abc', 2, 3",
              "'abc', r=2, x=1", "'abc', 2, r=2", "1, 2", "1, -1", "1, 1.0")
] + [
    # r is CONVERTED before the iterable is drained, and its SIGN checked after
    I + "g = iter([1, 2])\ntry:\n    itertools.combinations(g, 'x')\nexcept TypeError as e:\n"
        "    print(e)\nprint(list(g))",
    I + "g = iter([1, 2])\ntry:\n    itertools.combinations(g, -1)\nexcept ValueError as e:\n"
        "    print(e)\nprint(list(g))",
]

#: Uncaught: the exit code and the exception line must agree.
RAISES = [
    I + "print(list(itertools.product('a', repeat=-1)))",
    I + "print(list(itertools.product(1)))",
    I + "print(list(itertools.combinations('abc')))",
    I + "print(list(itertools.combinations('abc', -1)))",
    I + "print(next(itertools.product([])))",
    I + "print(itertools.product('a')[0])",
]

GRID = BASICS + DRAINED + LAZY + THE_SHAPE + ERRORS + RAISES

#: CPython answers each of these (or raises a message that is not this engine's
#: to write); this engine must decline rather than guess.
REFUSED = [
    I + "print(itertools.product('a'))",
    I + "print(repr(itertools.combinations('a', 1)))",
    I + "print(str(itertools.product('a')))",
    I + "print(itertools.product)",
    I + "print(len(itertools.product('a')))",
    I + "print(type(itertools.product('a')).__name__)",
    I + "print(hash(itertools.product('a')) is not None)",
    I + "print({itertools.product('a'): 1})",
    I + "print(itertools.product(repeat=2**70))",
    I + "print(itertools.combinations('abc', 2**70))",
    I + "print(itertools.combinations('abc', -2**70))",
    # CPython mallocs r indices before comparing r with n: MemoryError at
    # these sizes, a host-dependent answer below them (verifier, round 1)
    I + "print(list(itertools.combinations('abc', 2**63-1)))",
    I + "print(list(itertools.combinations([], 2**62)))",
    I + "print(list(itertools.combinations('', 2**20 + 1)))",
    I + "print(list(itertools.product('ab', repeat=10**9)))",
    I + "print(list(itertools.product({1, 2})))",
    I + "print(list(itertools.combinations({'a', 'b'}, 1)))",
    I + "print(list(itertools.combinations(x='abc', r=2)))",
    I + "print(itertools.product.__name__)",
    # every type object has `mro` (round 2); nothing here builds one
    I + "print(len(itertools.product.mro()))",
    I + "itertools.combinations.mro",
    I + "x = itertools.product\nprint(x.mro()[-1])",
    # CPython 3.14 annotates an error inside a json container with a note,
    # which is its traceback's last line
    I + "import json\njson.dumps({'k': itertools.product('a')})",
    I + "import json\njson.dumps([1, itertools.combinations('a', 1)])",
    # an uncaught NameError: CPython ends it with a suggestion
    I + "del itertools\nprint(itertools)",
    I + "print(json)",
    I + "print(itertool)",
    "from itertools import product\nprint(prodcut('a'))",
    "import difflib\nprint(difflib2)",
] + [
    I + "print(itertools.%s)" % n
    for n in ("chain", "islice", "permutations", "count", "groupby", "accumulate", "cycle",
              "repeat", "zip_longest", "starmap", "tee", "pairwise", "batched", "compress",
              "dropwhile", "takewhile", "filterfalse", "combinations_with_replacement")
] + [
    "from itertools import chain\nprint(1)",
    "from itertools import product, islice\nprint(1)",
    I + "print(itertools.chain.from_iterable([[1]]))",
]

#: A refusal reached after `os.mkdir` must still be a clean 90 with the disk
#: untouched — the barrier (`io.rs`) takes the directory back.
BARRIER = "import os\nos.mkdir('NEWD')\n"
AFTER_A_BARRIER = [
    I + BARRIER + "print(itertools.product('a'))",
    I + BARRIER + "print(len(itertools.combinations('a', 1)))",
    I + BARRIER + "print(list(itertools.product({1, 2})))",
    I + BARRIER + "print(itertools.product(repeat=2**70))",
]

#: What the CORE routes, from its own walk.
ROUTED_TO_LYPNING_L = [
    I + "print(1)",
    I + "print(list(itertools.product('ab', repeat=2)))",
    "import itertools as it\nprint(list(it.combinations('abc', 2)))",
    "from itertools import product\nprint(list(product('ab')))",
    "import re, itertools\nprint(re.escape('a'), list(itertools.product('a')))",
]
ROUTED_TO_CPYTHON = [
    I + "print(list(itertools.chain('ab', 'c')))",
    I + "print(list(itertools.islice('ab', 1)))",
    I + "def f():\n    return itertools.groupby([1])\nprint(1)",
    "from itertools import product, chain\nprint(list(product('ab')))",
    # the first blocker is ANOTHER capability's import: the unserved attribute
    # is recorded as the spectrum's stop rather than dropped
    "import re, itertools\nprint(re.escape('a'), list(itertools.count()))",
    "import csv, itertools, os\nos.mkdir('x')\nprint(itertools.permutations('ab'))",
]


def _spectrum(binary: Path) -> dict | None:
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


BINARY = _current(engines.LYPNING_L, "cap-itertools")
CORE = _current(engines.LYPNING, "cap-itertools")

needs_l = pytest.mark.skipif(
    BINARY is None,
    reason="no lypning-l carrying cap-itertools is built (lypning build --rust)",
)
needs_core = pytest.mark.skipif(
    CORE is None or BINARY is None,
    reason="routing needs the CORE that routes and the lypning-l it routes to",
)


def _run(argv: list[str], program: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True, text=True,
                              cwd=d, timeout=120)


def _refusal_problem(got: subprocess.CompletedProcess) -> str | None:
    if got.returncode != engines.UNSUPPORTED_EXIT:
        return "exit %d, not %d" % (got.returncode, engines.UNSUPPORTED_EXIT)
    if got.stdout != "":
        return "stdout was not empty: %r" % got.stdout[:120]
    head = "%s: unsupported: " % engines.LYPNING_L
    line = got.stderr.strip()
    if not line.startswith(head) or "\n" in line:
        return "stderr was %r, expected one %r line" % (line[:160], head)
    return None


@needs_l
@pytest.mark.parametrize("program", GRID, ids=range(len(GRID)))
def test_the_itertools_grid_agrees_with_cpython(program: str) -> None:
    got = _run([str(BINARY)], program)
    assert got.returncode != engines.UNSUPPORTED_EXIT, (
        "every GRID row is served; this one refused: %s\n  program: %r"
        % (got.stderr.strip()[:200], program))
    ref = _run([sys.executable], program)
    got_err = got.stderr.strip().splitlines()[-1:]
    ref_err = ref.stderr.strip().splitlines()[-1:]
    assert (got.stdout, got.returncode, got_err) == (ref.stdout, ref.returncode, ref_err), (
        "lypning-l disagrees with CPython.\n  program:  %r\n"
        "  lypning-l: %r exit %d %r\n  cpython:   %r exit %d %r"
        % (program, got.stdout, got.returncode, got.stderr.strip()[-300:],
           ref.stdout, ref.returncode, ref.stderr.strip()[-300:]))


@needs_l
@pytest.mark.parametrize("program", REFUSED, ids=range(len(REFUSED)))
def test_the_surface_outside_the_subset_refuses_rather_than_guesses(program: str) -> None:
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got)
    assert problem is None, "this program must refuse, not answer: %s\n  program: %r" % (
        problem, program)


@needs_l
@pytest.mark.parametrize("program", AFTER_A_BARRIER, ids=range(len(AFTER_A_BARRIER)))
def test_a_refusal_after_a_side_effect_is_still_clean(program: str) -> None:
    with tempfile.TemporaryDirectory() as d:
        before = sorted(os.listdir(d))
        got = subprocess.run([str(BINARY), "-c", program], capture_output=True, text=True,
                             cwd=d, timeout=120)
        after = sorted(os.listdir(d))
    assert _refusal_problem(got) is None, (_refusal_problem(got), program)
    assert before == after, "the refusal left %r behind" % sorted(set(after) - set(before))


@needs_core
@pytest.mark.parametrize("program", ROUTED_TO_LYPNING_L, ids=range(len(ROUTED_TO_LYPNING_L)))
def test_the_core_routes_a_served_program_to_the_larger_variant(program: str) -> None:
    route = engines.route(program, binary=CORE)
    assert route.engine == engines.LYPNING_L, (program, route.engine, route.kind, route.detail)


@needs_core
@pytest.mark.parametrize("program", ROUTED_TO_CPYTHON, ids=range(len(ROUTED_TO_CPYTHON)))
def test_the_core_routes_an_unserved_name_straight_to_cpython(program: str) -> None:
    route = engines.route(program, binary=CORE)
    assert route.engine == engines.CPYTHON, (program, route.engine, route.kind, route.detail)
    mine = engines.route(program, binary=BINARY)
    assert mine.engine == engines.CPYTHON, (program, mine.engine, mine.kind, mine.detail)


@needs_core
def test_the_capability_is_on_the_larger_variant_only() -> None:
    refused = _run([str(CORE)], "import itertools")
    assert refused.returncode == engines.UNSUPPORTED_EXIT and refused.stdout == ""
    assert refused.stderr.strip() == engines.refusal_line(
        engines.LYPNING, "module", "import itertools")
    table = _spectrum(BINARY)
    assert "cap-itertools" in table["self_caps"]
    assert {r["cap"]: r["modules"] for r in table["caps"]}["cap-itertools"] == ["itertools"]


#: Round-1 verifier findings, pinned to CPython 3.14.5's exact bytes
#: (2026-09-24) rather than to whatever interpreter runs the suite:
#: `product` with no iterables is ONE empty tuple at any `repeat` (it hung,
#: spinning `repeat` times over nothing); an all-keyword `combinations` call
#: says "keyword arguments"; `combinations` with a small `r > n` is `[]`.
PINNED = [
    (I + "print(list(itertools.product(repeat=2**62)))", "[()]\n", 0, None),
    (I + "print(list(itertools.product(repeat=2**63-1)), list(itertools.product(repeat=0)))",
     "[()] [()]\n", 0, None),
    (I + "print(list(itertools.combinations('ab', 2**20)))", "[]\n", 0, None),
    (I + "itertools.combinations(r=1, iterable='ab', x=3)", "", 1,
     "TypeError: combinations() takes at most 2 keyword arguments (3 given)"),
    (I + "itertools.combinations(x=3, y=4, z=5)", "", 1,
     "TypeError: combinations() takes at most 2 keyword arguments (3 given)"),
    (I + "itertools.combinations(iterable='ab', r=1, x=3, y=4)", "", 1,
     "TypeError: combinations() takes at most 2 keyword arguments (4 given)"),
    (I + "itertools.combinations(r=2, iterable='b', **{'a': 1})", "", 1,
     "TypeError: combinations() takes at most 2 keyword arguments (3 given)"),
    (I + "itertools.combinations('ab', r=1, x=3)", "", 1,
     "TypeError: combinations() takes at most 2 arguments (3 given)"),
    # a module is hashable by identity, and `posixpath` IS `os.path`
    ("import itertools, math\nd = {itertools: 1, math: 2}\n"
     "print(d[itertools], d[math], len({math, math, itertools}), math in {itertools})",
     "1 2 2 False\n", 0, None),
]


@needs_l
@pytest.mark.parametrize("case", PINNED, ids=range(len(PINNED)))
def test_the_round_one_findings_match_cpython_bytes(case) -> None:
    program, out, code, err = case
    got = _run([str(BINARY)], program)
    last = (got.stderr.strip().splitlines() or [None])[-1]
    assert (got.stdout, got.returncode, last) == (out, code, err), (program, got.stderr)


#: Shared (core) code: a module as a dict/set key, and `posixpath` as the
#: same object as `os.path`. Exact CPython 3.14.5 bytes.
CORE_PINNED = [
    ("import math\nprint({math: 1}[math], len({math, math}), math in {math: 0})",
     "1 1 True\n"),
    ("import os, posixpath\nfrom os import path\n"
     "print(os.path is posixpath, os.path == posixpath, path is posixpath)\n"
     "print({os.path: 1}.get(posixpath), len({os, os.path, posixpath}))",
     "True True True\n1 2\n"),
    ("import sys, json\nprint({sys: 'a', json: 'b'}[json], {sys: 1}.get(json))",
     "b None\n"),
]


@needs_core
@pytest.mark.parametrize("case", CORE_PINNED, ids=range(len(CORE_PINNED)))
def test_a_module_is_hashable_by_identity_on_every_variant(case) -> None:
    program, out = case
    for binary in (CORE, BINARY):
        got = _run([str(binary)], program)
        assert (got.stdout, got.returncode) == (out, 0), (binary, program, got.stderr)


#: Round-2 verifier findings, pinned to CPython 3.14.5's exact bytes
#: (2026-09-25): `isinstance` against the classes is ANSWERED (and a tuple is
#: tried left to right); json names `type(o).__name__`, not the dotted
#: tp_name; the operand messages are the left operand's own.
PINNED_2 = [
    (I + "print(isinstance(itertools.product([1]), itertools.product),"
         " isinstance(itertools.product([1]), itertools.combinations),"
         " isinstance(1, (int, itertools.combinations)),"
         " isinstance(itertools.combinations('a', 1), (str, itertools.combinations)))",
     "True False True True\n", 0, None),
    (I + "import json\njson.dumps(itertools.product('ab'))", "", 1,
     "TypeError: Object of type product is not JSON serializable"),
    (I + "import json\njson.dumps(itertools.combinations('a', 1))", "", 1,
     "TypeError: Object of type combinations is not JSON serializable"),
    (I + "'x' + itertools.product('a')", "", 1,
     'TypeError: can only concatenate str (not "itertools.product") to str'),
    (I + "dict(itertools.product('ab'))", "", 1,
     "ValueError: dictionary update sequence element #0 has length 1; 2 is required"),
    (I + "isinstance(1, itertools.product('a'))", "", 1,
     "TypeError: isinstance() arg 2 must be a type, a tuple of types, or a union"),
    (I + "'abc'.startswith(itertools.product('a'))", "", 1,
     "TypeError: startswith first arg must be str or a tuple of str, not itertools.product"),
    (I + "[1, 2][itertools.product('a')]", "", 1,
     "TypeError: list indices must be integers or slices, not itertools.product"),
    (I + "1 in itertools.product", "", 1,
     "TypeError: argument of type 'type' is not a container or iterable"),
    (I + "try:\n    print(itertool)\nexcept NameError as e:\n    print(e)",
     "name 'itertool' is not defined\n", 0, None),
]


@needs_l
@pytest.mark.parametrize("case", PINNED_2, ids=range(len(PINNED_2)))
def test_the_round_two_findings_match_cpython_bytes(case) -> None:
    program, out, code, err = case
    got = _run([str(BINARY)], program)
    last = (got.stderr.strip().splitlines() or [None])[-1]
    assert (got.stdout, got.returncode, last) == (out, code, err), (program, got.stderr)


#: Shared (core) code reached through the round-2 findings: each message is
#: CPython 3.14.5's, caught and printed or uncaught, on EVERY variant.
CORE_PINNED_2 = [
    ("'x' + 1", "", 1, 'TypeError: can only concatenate str (not "int") to str'),
    ("[1] + (2,)", "", 1, 'TypeError: can only concatenate list (not "tuple") to list'),
    ("(1,) + [2]", "", 1, 'TypeError: can only concatenate tuple (not "list") to tuple'),
    ("s = 'x'\ns += None", "", 1,
     'TypeError: can only concatenate str (not "NoneType") to str'),
    ("dict([(1,)])", "", 1,
     "ValueError: dictionary update sequence element #0 has length 1; 2 is required"),
    ("dict(['ab', (1, 2, 3)])", "", 1,
     "ValueError: dictionary update sequence element #1 has length 3; 2 is required"),
    ("d = {}\nd.update([(1, 2), 'c'])", "", 1,
     "ValueError: dictionary update sequence element #1 has length 1; 2 is required"),
    ("print(dict(['ab', (1, 2)]))", "{'a': 'b', 1: 2}\n", 0, None),
    ("isinstance(1, 3)", "", 1,
     "TypeError: isinstance() arg 2 must be a type, a tuple of types, or a union"),
    ("isinstance(1, (str, 3))", "", 1,
     "TypeError: isinstance() arg 2 must be a type, a tuple of types, or a union"),
    ("print(isinstance(1, (int, 3)), isinstance('a', (int, str)))", "True True\n", 0, None),
    ("'abc'.startswith(1)", "", 1,
     "TypeError: startswith first arg must be str or a tuple of str, not int"),
    ("'abc'.endswith([1])", "", 1,
     "TypeError: endswith first arg must be str or a tuple of str, not list"),
    ("print('abc'.startswith(('a', 1)), 'abc'.endswith(('c', 'x')))", "True True\n", 0, None),
    ("'abc'.startswith((1, 'a'))", "", 1,
     "TypeError: tuple for startswith must only contain str, not int"),
    ("'abc'.startswith(('x', 1), 10)", "", 1,
     "TypeError: tuple for startswith must only contain str, not int"),
    ("'abc'.find('a', 'x')", "", 1,
     "TypeError: slice indices must be integers or None or have an __index__ method"),
    ("b'abc'.find(b'a', 1.5)", "", 1,
     "TypeError: slice indices must be integers or None or have an __index__ method"),
    ("x = 'a'\n[1, 2][x]", "", 1, "TypeError: list indices must be integers or slices, not str"),
    ("x = 'a'\n(1, 2)[x]", "", 1, "TypeError: tuple indices must be integers or slices, not str"),
    ("x = 'a'\n'ab'[x]", "", 1, "TypeError: string indices must be integers, not 'str'"),
    ("x = None\nb'ab'[x]", "", 1,
     "TypeError: byte indices must be integers or slices, not NoneType"),
    ("x = 1.0\nrange(3)[x]", "", 1,
     "TypeError: range indices must be integers or slices, not float"),
    ("x = [1]\nk = 'a'\nx[k] = 2", "", 1,
     "TypeError: list indices must be integers or slices, not str"),
    ("1 in 5", "", 1, "TypeError: argument of type 'int' is not a container or iterable"),
    ("import json\njson.dumps(int)", "", 1,
     "TypeError: Object of type type is not JSON serializable"),
    ("import json\nprint(json.dumps([1, (2, {'a': None})]))", '[1, [2, {"a": null}]]\n', 0, None),
    ("print(int.mro())", None, 90, None),
    # a NameError on the core keeps the bare line it always printed (main's
    # behaviour; the hint is a known core gap, not this unit's)
    ("try:\n    print(nope)\nexcept NameError as e:\n    print(e)",
     "name 'nope' is not defined\n", 0, None),
]


@needs_core
@pytest.mark.parametrize("case", CORE_PINNED_2, ids=range(len(CORE_PINNED_2)))
def test_the_shared_messages_match_cpython_bytes_on_every_variant(case) -> None:
    program, out, code, err = case
    for binary in (CORE, BINARY):
        got = _run([str(binary)], program)
        if code == engines.UNSUPPORTED_EXIT:
            assert got.returncode == code and got.stdout == "", (binary, program, got.stderr)
            assert len(got.stderr.strip().splitlines()) == 1, (binary, program, got.stderr)
            continue
        last = (got.stderr.strip().splitlines() or [None])[-1]
        assert (got.stdout, got.returncode, last) == (out, code, err), (
            binary, program, got.stderr)


#: A json error INSIDE a container carries a 3.14 note; the core refuses it
#: cleanly rather than print a last line CPython does not.
@needs_core
def test_a_json_error_inside_a_container_refuses_on_every_variant() -> None:
    for binary in (CORE, BINARY):
        got = _run([str(binary)], "import json\njson.dumps({'k': [1, {2}]})")
        assert got.returncode == engines.UNSUPPORTED_EXIT and got.stdout == "", (
            binary, got.stderr)


#: Round-3 verifier findings. An uncaught AttributeError in a program that
#: names itertools or difflib refuses (`attr-hint`): CPython 3.10+ ends it with
#: `Did you mean: 'append'?`, out of `dir(x)`, which this engine does not
#: compute. The flag is read from the SOURCE, so an error raised BEFORE the
#: import refuses too (for NameError as well).
REFUSED_3 = [
    I + "x = [1]; x.apend(2)",
    I + "d = {}; d.iterms()",
    I + "'a'.strp()",
    I + "(1).bit_lenght()",
    I + "itertools.product('a').__next",
    # more than 8 MiB of output refuses rather than flushing: a refusal later
    # in the run would otherwise be an exit 1 with half the output written
    I + "print('x' * (9 << 20)); print('ok')",
    I + "print('x' * (9 << 20)); print(itertools.product.mro()[0] is itertools.product)",
]


#: An error before the import runs: the core answers it, and so does
#: lypning-l, however it was reached (`route::arm_hold`).
BEFORE_THE_IMPORT_3 = [
    "x = [1]; x.apend(2)\nimport itertools",
    "print(nope)\nimport difflib",
]


@needs_core
@pytest.mark.parametrize("program", BEFORE_THE_IMPORT_3, ids=range(len(BEFORE_THE_IMPORT_3)))
def test_an_error_before_the_import_is_the_cores(program: str) -> None:
    core, larger = _run([str(CORE)], program), _run([str(BINARY)], program)
    assert (larger.returncode, larger.stdout, larger.stderr) == (core.returncode, core.stdout, core.stderr)


@needs_l
@pytest.mark.parametrize("program", REFUSED_3, ids=range(len(REFUSED_3)))
def test_the_round_three_refusals_are_clean(program: str) -> None:
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got)
    assert problem is None, "this program must refuse, not answer: %s\n  program: %r" % (
        problem, program)


#: ...and the CHAIN answers each of them with CPython's exact bytes (3.14.5,
#: 2026-09-25): the flush-past-8-MiB programs were an exit 1 with the prefix
#: already on stdout.
BIG = "x" * (9 << 20) + "\n"
CHAIN_3 = [
    (I + "print('x' * (9 << 20)); print(list(itertools.product([1], repeat=70000))[:0])",
     BIG + "[]\n", 0, None),
    (I + "print('x' * (9 << 20)); print(repr(itertools.product([1]))[:5])",
     BIG + "<iter\n", 0, None),
    (I + "print('x' * (9 << 20)); print(len(list(itertools.combinations(range(3), 2**30))))",
     BIG + "0\n", 0, None),
    (I + "print('x' * (9 << 20)); x = getattr(itertools, 'perm' + 'utations'); print(1)",
     BIG + "1\n", 0, None),
    (I + "print('x' * (9 << 20)); print(list(itertools.product(repeat=2**70)))",
     BIG, 1, "OverflowError: Python int too large to convert to C ssize_t"),
    (I + "x = [1]; x.apend(2)", "", 1,
     "AttributeError: 'list' object has no attribute 'apend'. Did you mean: 'append'?"),
    (I + "itertools.product('a').__next", "", 1,
     "AttributeError: 'itertools.product' object has no attribute '__next'."
     " Did you mean: '__ne__'?"),
]


@needs_core
@pytest.mark.skipif(sys.version_info[:2] != (3, 14), reason="bytes pinned to CPython 3.14")
@pytest.mark.parametrize("case", CHAIN_3, ids=range(len(CHAIN_3)))
def test_the_chain_answers_the_round_three_findings_with_cpython_bytes(case) -> None:
    program, out, code, err = case
    env = dict(os.environ, LYPNING_CPYTHON=sys.executable, LYPNING_L_BIN=str(BINARY))
    with tempfile.TemporaryDirectory() as d:
        got = subprocess.run([str(CORE), "run", "-c", program], capture_output=True,
                             text=True, cwd=d, timeout=300, env=env)
    last = (got.stderr.strip().splitlines() or [None])[-1]
    assert (got.stdout == out, got.returncode, last) == (True, code, err), (
        program, got.stdout[-80:], got.stderr[-300:])


#: Sequence repetition by a non-int names the COUNT, left operand first, as
#: `PyNumber_Multiply` does: `'x' * itertools.product('a')` (round 3) said
#: "cannot be interpreted as an integer". Shared code; CPython 3.14.5 bytes.
CORE_PINNED_3 = [
    ("'x' * 1.5", "TypeError: can't multiply sequence by non-int of type 'float'"),
    ("1.5 * 'x'", "TypeError: can't multiply sequence by non-int of type 'float'"),
    ("'x' * None", "TypeError: can't multiply sequence by non-int of type 'NoneType'"),
    ("'x' * 'y'", "TypeError: can't multiply sequence by non-int of type 'str'"),
    ("[1] * 'x'", "TypeError: can't multiply sequence by non-int of type 'str'"),
    ("'x' * [1]", "TypeError: can't multiply sequence by non-int of type 'list'"),
    ("{} * 'x'", "TypeError: can't multiply sequence by non-int of type 'dict'"),
    ("(1,) * {}", "TypeError: can't multiply sequence by non-int of type 'dict'"),
    ("b'a' * []", "TypeError: can't multiply sequence by non-int of type 'list'"),
    ("'x' * zip()", "TypeError: can't multiply sequence by non-int of type 'zip'"),
    ("range(3) * 'x'", "TypeError: can't multiply sequence by non-int of type 'range'"),
    ("x = [1]\nx *= 'a'", "TypeError: can't multiply sequence by non-int of type 'str'"),
    ("range(3) * 2", "TypeError: unsupported operand type(s) for *: 'range' and 'int'"),
]


@needs_core
@pytest.mark.parametrize("case", CORE_PINNED_3, ids=range(len(CORE_PINNED_3)))
def test_sequence_repetition_names_the_count_on_every_variant(case) -> None:
    program, err = case
    for binary in (CORE, BINARY):
        got = _run([str(binary)], program)
        last = (got.stderr.strip().splitlines() or [None])[-1]
        assert (got.stdout, got.returncode, last) == ("", 1, err), (binary, program, got.stderr)
    got = _run([str(BINARY)], I + "x = 'x' * itertools.product('a')")
    assert got.stderr.strip().splitlines()[-1] == (
        "TypeError: can't multiply sequence by non-int of type 'itertools.product'")
    ok = _run([str(CORE)], "print('ab' * True, [0] * 2, 2 * (1,), b'a' * False)")
    assert ok.stdout == "ab [0, 0] (1, 1) b''\n", ok.stderr
