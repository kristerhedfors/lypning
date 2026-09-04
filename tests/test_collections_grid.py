"""`collections.Counter` and `defaultdict`, as a grid: every program on both.

`tests/test_keyword_grid.py` is the shape this follows and the reason it exists:
the defect this file guards against is PER PROGRAM, not per function, and a
handful of examples is exactly what would miss it. What is different here is
that the surface is a dict SUBCLASS, so most of it is inherited behaviour that
is already right — and the few places a subclass differs are each one silent
wrong answer waiting to happen. Every one of those places has rows below.

The grid is one program per row, run twice from a FRESH temp cwd (invariant 4),
and every row must end one of exactly two ways:

  * byte-identical stdout AND the same exit code as CPython 3.x, or
  * a clean refusal — exit 90, nothing on stdout, one
    ``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

Nothing else passes. In particular a row is NOT allowed to be "close": a
`Counter` whose `most_common()` breaks ties differently from CPython prints a
plausible list at exit 0, which is the failure invariant 1 exists for.

The five traps this was written against, each measured against CPython before
the code was written, and each with rows below:

1. **Tie order.** `most_common()` is `sorted(items, key=count, reverse=True)`
   and CPython's sort is STABLE, so equal counts keep INSERTION order.
   `Counter('abracadabra')` is `a b r c d`. This is the single most likely
   defect and `TIES` below is a whole block of it.
2. **repr.** `Counter({'a': 2, 'b': 1})` in most_common order (not insertion
   order), and `defaultdict(<class 'list'>, {})` with the real class repr.
3. **bool is an int subclass.** `Counter([True, 1])` is one key with count 2,
   and the key that survives is the FIRST one seen.
4. **Missing keys.** `c['nope']` is `0` and inserts NOTHING; `d[k]` on a
   defaultdict DOES insert; `in` inserts for neither; `del c['nope']` is a
   no-op on a Counter and a `KeyError` on a defaultdict.
5. **Everything whose message this engine cannot pin** — `Counter(1)`, a
   non-callable `default_factory`, `.elements()`, `.subtract()`, multiset
   arithmetic — is a refusal, and `REFUSED` below asserts it is a refusal
   rather than a wrong answer.

**One divergence is deliberately not a row**, because it is older than this
capability and belongs to plain dicts: `d.keys() == {'a'}` answers `False` here
and `True` in CPython, for a `dict` as much as for a `Counter` (verified on the
untouched core binary). Set ALGEBRA over a view is already refused
(`unsupported: dict-view`), which is what makes the shape safe; view EQUALITY
against a set is not, and fixing it is a change to `value::eq` that the frozen
core would pay for. It is named here so that the omission is a decision on the
record and not an oversight.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

#: Construction, both spellings, and everything that is inherited dict
#: behaviour and must stay inherited.
CONSTRUCTION = [
    'from collections import Counter\nprint(Counter("abracadabra"))',
    'from collections import Counter\nprint(repr(Counter("aab")))',
    'from collections import Counter\nprint(repr(Counter()))',
    'import collections\nprint(collections.Counter("hello world"))',
    'import collections as c\nprint(c.Counter([1,2,2,3,3,3]))',
    'from collections import Counter\nprint(Counter(["a","b","a"]))',
    'from collections import Counter\nprint(Counter(("a","b","a")))',
    'from collections import Counter\nprint(Counter(range(3)))',
    'from collections import Counter\nprint(Counter({"a": 3, "b": 1}))',
    'from collections import Counter\nprint(Counter(Counter("aab")))',
    'from collections import Counter\nprint(Counter(x for x in "aab"))',
    'from collections import Counter\nprint(Counter(b"aab"))',
    'from collections import Counter\nprint(Counter({"a":1}.keys()))',
    'from collections import Counter\nprint(Counter("a\'b\'a"))',
    'from collections import Counter\nprint(Counter([(1,2),(1,2),(3,4)]))',
    'from collections import Counter\nprint(Counter(""), Counter([]) == Counter())',
]

#: Trap 1. Ties, and only ties: every one of these has at least two counts
#: equal, so any sort that is not stable-descending prints a different list at
#: exit 0.
TIES = [
    'from collections import Counter\nprint(Counter("abracadabra").most_common())',
    'from collections import Counter\nprint(Counter("abracadabra").most_common(3))',
    'from collections import Counter\nprint(Counter("abcdefg").most_common())',
    'from collections import Counter\nprint(Counter("gfedcba").most_common())',
    'from collections import Counter\nprint(Counter("zyxwvutsrq").most_common(4))',
    'from collections import Counter\nprint(Counter("aabbccddeeffgg").most_common())',
    'from collections import Counter\nprint(Counter("mississippi").most_common())',
    'from collections import Counter\n'
    'print(Counter("the quick brown fox jumps over the lazy dog").most_common())',
    'from collections import Counter\nprint(Counter("aaabbbcccdddeeefff").most_common(2))',
    'from collections import Counter\nprint(Counter([9,8,7,6,5,4,3,2,1]).most_common())',
    'from collections import Counter\nprint(Counter("aabb").most_common(1))',
    'from collections import Counter\nprint(Counter("ba").most_common())',
    'from collections import Counter\nc=Counter("xyzzy")\nprint([k for k,v in c.most_common()])',
    'from collections import Counter\nprint(dict(Counter("aabbc").most_common()))',
    # n's edges, which are heapq.nlargest's and not a slice's: None means all,
    # 0 and any negative n mean none, and an n past the end is the whole list.
    'from collections import Counter\nprint(Counter("aab").most_common(0))',
    'from collections import Counter\nprint(Counter("aab").most_common(-1))',
    'from collections import Counter\nprint(Counter("aab").most_common(None))',
    'from collections import Counter\nprint(Counter("aab").most_common(99))',
    'from collections import Counter\nprint(Counter("aab").most_common(1))',
    'from collections import Counter\nprint(Counter("aab").most_common(True))',
    'from collections import Counter\nprint(Counter("aab").most_common(2**62))',
    'from collections import Counter\nprint(Counter().most_common(), Counter().most_common(3))',
]

#: Trap 3 and trap 4 — key collapse across types, and what a missing key does.
KEYS = [
    'from collections import Counter\nprint(Counter([True, 1]))',
    'from collections import Counter\nprint(Counter([1, True]))',
    'from collections import Counter\nprint(Counter([1, 1.0, True]))',
    'from collections import Counter\nprint(Counter([True, 1, 1.0]))',
    'from collections import Counter\nprint(Counter([0, False]))',
    'from collections import Counter\nc=Counter([1,1.0,True])\nprint(list(c.keys()), list(c.values()))',
    'from collections import Counter\nprint(Counter({1: 5})[True])',
    'from collections import Counter\nprint(Counter({1: 2, 1.0: 3}))',
    'from collections import Counter\nprint(Counter({True: 1, 1: 2}))',
    'from collections import Counter\nc=Counter("aab")\nprint(c["zz"], len(c), "zz" in c, c)',
    'from collections import Counter\nc=Counter()\nprint(c["x"], len(c))',
    'from collections import Counter\nc=Counter("aab")\nprint(c.get("zz"), c.get("zz", 7), c.get("a"))',
    'from collections import Counter\nc=Counter()\nc["a"] += 1\nc["a"] += 1\nc["b"] += 1\nprint(c)',
    'from collections import Counter\nc=Counter("aab")\ndel c["zz"]\nprint(c)',
    'from collections import Counter\nc=Counter("aab")\ndel c["a"]\nprint(c)',
    'from collections import Counter\nc=Counter("aab")\nc["c"]=5\nprint(c, c.most_common())',
    # An unhashable key names its own type in the TypeError, and the key that
    # is the mapping itself is the case where reading the type back needs care.
    'from collections import Counter\nc=Counter()\nc[c]=1\nprint(c)',
    'from collections import Counter\nc=Counter()\nprint(c[[1]])',
    'from collections import Counter\nprint({Counter(): 1})',
]

#: `Counter.update` ADDS counts where `dict.update` replaces them, and the
#: empty-self case copies verbatim — CPython's own two branches.
UPDATE = [
    'from collections import Counter\nc=Counter("aab")\nc.update("abc")\nprint(c)',
    'from collections import Counter\nc=Counter()\nc.update({"a": 4})\nprint(c)',
    'from collections import Counter\nc=Counter("a")\nc.update({"a": 4})\nprint(c)',
    'from collections import Counter\nc=Counter("ab")\nc.update(Counter("bc"))\nprint(c, c.most_common())',
    'from collections import Counter\nc=Counter("ab")\nc.update()\nprint(c)',
    'from collections import Counter\nc=Counter("ab")\nc.update([1,2,1])\nprint(c)',
    'from collections import Counter\nc=Counter("aab")\nd=c.copy()\nd["a"]=9\nprint(c, d)',
    'from collections import Counter\nc=Counter("aab")\nc.clear()\nprint(c, len(c))',
    'from collections import Counter\nc=Counter("aab")\nprint(c.pop("a"), c)',
    'from collections import Counter\nc=Counter("aab")\nprint(c.setdefault("z", 4), c)',
    'from collections import Counter\nc=Counter("aab")\nprint(c.popitem(), c)',
]

#: Everything a dict already does, which a subclass must not quietly change.
INHERITED = [
    'from collections import Counter\nprint(dict(Counter("aab")))',
    'from collections import Counter\nprint({**Counter("aab")})',
    'from collections import Counter\nprint(Counter("aab") == {"a": 2, "b": 1})',
    'from collections import Counter\nprint(Counter("aab") == Counter("aba"))',
    'from collections import Counter\nprint(Counter("aab") != {"a": 2})',
    'from collections import Counter\nprint(isinstance(Counter(), dict), isinstance({}, Counter))',
    'from collections import Counter\nprint(isinstance(Counter("a"), Counter))',
    'from collections import Counter\nprint(len(Counter("aab")), bool(Counter()), bool(Counter("a")))',
    'from collections import Counter\nc=Counter("a")\nprint(not c, not Counter())',
    'from collections import Counter\nprint(sorted(Counter("cab")), list(Counter("cab")))',
    'from collections import Counter\nprint(sum(Counter("aab").values()))',
    'from collections import Counter\nprint(sorted(Counter("aab").items()))',
    'from collections import Counter\n'
    'print(Counter("aab").keys(), Counter("aab").values(), Counter("aab").items())',
    'from collections import Counter\nimport json\nprint(json.dumps(Counter("aab")))',
    'from collections import Counter\nfor k, v in Counter("aab").items(): print(k, v)',
    'from collections import Counter\nprint(max(Counter("aabbb"), key=Counter("aabbb").get))',
    'from collections import Counter\nprint("%s" % (Counter("aab"),))',
    'from collections import Counter\nprint(f"{Counter(\'aab\')}")',
    'from collections import Counter\nc = Counter("aab")\nprint(f"{c!r}")',
    'from collections import Counter\nprint(str(Counter("aab")), repr(Counter("aab")))',
    'from collections import Counter\nprint([Counter("aa")], {"c": Counter("aa")})',
    'from collections import Counter\nc=Counter("aab")\nc2=c\nc2["a"]=9\nprint(c)',
    'from collections import Counter\nprint(list(reversed(list(Counter("abc")))))',
    'from collections import Counter\n'
    'print(sorted(Counter("aab").items(), key=lambda kv: (-kv[1], kv[0])))',
]

#: Trap 2's other half, and trap 4's: a defaultdict prints its factory, and its
#: missing key INSERTS where a Counter's does not.
DEFAULTDICT = [
    'from collections import defaultdict\nprint(repr(defaultdict(list)))',
    'from collections import defaultdict\nprint(repr(defaultdict(int)))',
    'from collections import defaultdict\nprint(repr(defaultdict(set)))',
    'from collections import defaultdict\nprint(repr(defaultdict(str)))',
    'from collections import defaultdict\nprint(repr(defaultdict(dict)))',
    'from collections import defaultdict\nprint(repr(defaultdict(tuple)))',
    'from collections import defaultdict\nprint(repr(defaultdict(float)))',
    'from collections import defaultdict\nprint(repr(defaultdict(bool)))',
    'from collections import defaultdict\nprint(repr(defaultdict()))',
    'from collections import defaultdict\nprint(repr(defaultdict(None)))',
    'import collections\nprint(repr(collections.defaultdict(list)))',
    'from collections import defaultdict\n'
    'd=defaultdict(list)\nd["a"].append(1)\nd["a"].append(2)\nd["b"].append(3)\nprint(d)',
    'from collections import defaultdict\nd=defaultdict(int)\nfor c in "aab": d[c] += 1\nprint(d)',
    'from collections import defaultdict\nd=defaultdict(int)\nprint("x" in d, d["x"], "x" in d, len(d), d)',
    'from collections import defaultdict\nd=defaultdict(list)\nprint(d.get("x"), "x" in d, len(d))',
    'from collections import defaultdict\nd=defaultdict(str)\nd["a"] += "z"\nprint(d)',
    'from collections import defaultdict\nd=defaultdict(list)\nd["a"]=[1]\nprint(d, dict(d), len(d))',
    'from collections import defaultdict\nd=defaultdict(int)\nd["a"]=1\ndel d["a"]\nprint(d)',
    'from collections import defaultdict\nd=defaultdict(int)\nd["a"]=1\nprint(d.copy(), d.pop("a"), d)',
    'from collections import defaultdict\nd=defaultdict(int)\nd["a"]=1\nd.clear()\nprint(d)',
    'from collections import defaultdict\nd=defaultdict(list)\nd.update({"a": [1]})\nprint(d)',
    'from collections import defaultdict\nprint(isinstance(defaultdict(list), dict))',
    'from collections import defaultdict\nprint(defaultdict(int) == {}, defaultdict(int) == dict())',
    'from collections import defaultdict\nd=defaultdict(int)\nd["a"]+=1\nprint(sorted(d.items()), dict(d))',
    'from collections import defaultdict\nimport json\nd=defaultdict(int)\nd["a"]=1\nprint(json.dumps(d))',
    'from collections import defaultdict, Counter\nd=defaultdict(list)\nd["k"].append(Counter("aa"))\nprint(d)',
    'from collections import defaultdict\nd=defaultdict(list)\nprint(len(d["a"]), d)',
    'from collections import defaultdict\nd=defaultdict(set)\nd["a"].add(1)\nprint(len(d["a"]), list(d.keys()))',
    'from collections import defaultdict\nd=defaultdict(tuple)\nprint(d["a"], d)',
    'from collections import defaultdict\nd=defaultdict(bool)\nprint(d["a"], d)',
    'from collections import defaultdict\nd=defaultdict(dict)\nd["a"]["b"]=1\nprint(d)',
    'from collections import defaultdict\nd=defaultdict(int)\nd["b"]=1\nd["a"]=2\nprint(list(d), list(d.items()))',
    'from collections import defaultdict\n'
    'd=defaultdict(list)\nfor w in "the cat the hat".split(): d[len(w)].append(w)\nprint(d)',
]

#: Trap 5. These must REFUSE — exit 90, empty stdout — and the test asserts the
#: refusal rather than merely tolerating it, because every one of them is a
#: program CPython answers and a wrong answer here would be silent. A row that
#: started matching CPython would be a coverage win and would fail this list on
#: purpose: the list is a claim about what the engine says it cannot do.
REFUSED = [
    'from collections import Counter\nprint(Counter("ab") + Counter("bc"))',
    'from collections import Counter\nprint(Counter("ab") - Counter("bc"))',
    'from collections import Counter\nprint(Counter("ab") & Counter("bc"))',
    'from collections import Counter\nprint(Counter("ab") | Counter("bc"))',
    'from collections import Counter\nprint(Counter("ab") | {"z": 1})',
    'from collections import Counter\nprint({"z": 1} | Counter("ab"))',
    'from collections import Counter\nprint(-Counter("ab"))',
    'from collections import Counter\nprint(+Counter("ab"))',
    'from collections import Counter\nprint(Counter("a") < Counter("ab"))',
    'from collections import Counter\nprint(sorted(Counter("aab").elements()))',
    'from collections import Counter\nc=Counter("aab")\nc.subtract("a")\nprint(c)',
    'from collections import Counter\nprint(Counter("aab").total())',
    'from collections import Counter\nc=Counter()\nc[1]=2\nprint(c.fromkeys("ab"))',
    'from collections import Counter\nprint(Counter(1))',
    'from collections import Counter\nprint(Counter(a=1))',
    'from collections import Counter\nprint(Counter("ab", "cd"))',
    'from collections import Counter\nprint(Counter("aab").most_common("2"))',
    'from collections import Counter\nprint(Counter("aab").most_common(1.5))',
    'from collections import Counter\nprint(Counter({1,2}))',
    'from collections import Counter\nprint(type(Counter()))',
    'from collections import Counter\nprint(Counter({"a": "x"}))',
    'from collections import Counter\nprint(Counter({"a": 1.5}).most_common())',
    'from collections import Counter\nc=Counter()\nc["a"]=9223372036854775807\nc.update("a")\nprint(c)',
    'from collections import defaultdict\nprint(defaultdict(lambda: 0))',
    'from collections import defaultdict\nprint(defaultdict(3))',
    'from collections import defaultdict, Counter\nprint(defaultdict(Counter))',
    'from collections import defaultdict\nd=defaultdict(list)\nprint(d.default_factory)',
    'from collections import defaultdict\nprint(defaultdict(list, {"a": [1]}))',
    'from collections import OrderedDict\nprint(OrderedDict())',
    'from collections import deque\nprint(deque())',
    'import collections\nprint(collections.namedtuple)',
    # A refusal leaves NOTHING on stdout even when the program printed first —
    # the half of invariant 2 that only ever broke silently.
    'print("hi")\nfrom collections import Counter\nprint(Counter("a") + Counter("b"))',
]

GRID = CONSTRUCTION + TIES + KEYS + UPDATE + INHERITED + DEFAULTDICT


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


def _current(engine: str) -> Path | None:
    """A built ``engine`` that carries THIS tree's capability table.

    Two candidates, in this order: whatever is installed, then the crate's own
    target dir — where the file is called ``lypning`` for both variants (that
    is the cargo bin name; the ENGINE name is what the binary says).

    A candidate is taken only if it names itself ``engine`` **and** its
    compiled `route::CAPS` knows `cap-collections`. That second condition is
    the point: an installed binary from before this capability landed answers
    every grid row with a refusal, which would turn the whole file into a
    hundred green skips measuring nothing. Skipping loudly is the honest
    failure; passing quietly is not.
    """
    target = {engines.LYPNING: paths.RUST_DIR / "target" / "release" / "lypning",
              engines.LYPNING_L: paths.RUST_DIR / "target" / "variant-l" / "release" / "lypning"}
    found = engines.find(engine)
    for cand in ([Path(found)] if found else []) + [target[engine]]:
        if not cand.is_file():
            continue
        table = _spectrum(cand)
        if table is None or table.get("self") != engine:
            continue
        if any(row.get("cap") == "cap-collections" for row in table.get("caps", [])):
            return cand
    return None


BINARY = _current(engines.LYPNING_L)
CORE = _current(engines.LYPNING)

needs_l = pytest.mark.skipif(
    BINARY is None,
    reason="no lypning-l carrying cap-collections is built (cargo build --release "
           "--no-default-features --features variant-l --target-dir target/variant-l)",
)


def _run(argv: list[str], program: str) -> subprocess.CompletedProcess:
    """One program, in a temp cwd of its own — invariant 4, and these programs
    are harmless, but the rule is the rule and a grid is exactly where a
    borrowed cwd would go unnoticed."""
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True, text=True,
                              cwd=d, timeout=60)


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


@needs_l
@pytest.mark.parametrize("program", GRID, ids=range(len(GRID)))
def test_the_collections_grid_agrees_with_cpython(program: str) -> None:
    got = _run([str(BINARY)], program)
    if got.returncode == engines.UNSUPPORTED_EXIT:
        # A refusal is always allowed and is never a bug — but it must be a
        # CLEAN one, and it must be reported, because a row that started
        # refusing is a row that stopped measuring anything.
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
@pytest.mark.parametrize("program", REFUSED, ids=range(len(REFUSED)))
def test_the_surface_outside_the_subset_refuses_rather_than_guesses(program: str) -> None:
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got)
    assert problem is None, (
        "this program must refuse, not answer — CPython answers it and any "
        "answer here would be a silent divergence: %s\n  program: %r\n  stderr: %r"
        % (problem, program, got.stderr.strip()[:200])
    )


@needs_l
def test_the_capability_is_on_the_larger_variant_only() -> None:
    """The gate this whole file sits behind: the core must still REFUSE
    `collections`, and must route it to the sibling that serves it.

    A capability that leaked into the frozen variant would still pass every
    grid row above — it is the same code — so the byte budget is defended
    here, by asking each binary what it is."""
    core = CORE
    if core is None:
        pytest.skip("no core carrying this tree's capability table is built")
    refused = _run([str(core)], "import collections")
    assert refused.returncode == engines.UNSUPPORTED_EXIT and refused.stdout == ""
    assert refused.stderr.strip() == engines.refusal_line(
        engines.LYPNING, "module", "import collections")

    # …and the core's ROUTER knows which sibling does serve it, which is the
    # half that makes the refusal cost one spawn instead of a CPython one.
    route = subprocess.run([str(core), "route", "-c",
                            'from collections import Counter\nprint(Counter("aab"))'],
                           capture_output=True, text=True, timeout=60)
    assert route.stdout.split("\t")[0].strip() == engines.LYPNING_L, route.stdout


@needs_l
def test_the_python_copy_of_the_capability_table_is_the_binarys_own() -> None:
    """`engines.VARIANT_CAPS` is a copy of `route::SPECTRUM`'s caps column, and
    a copy is honest only while something checks it."""
    import json
    out = subprocess.run([str(BINARY), "route", "--spectrum"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    table = json.loads(out.stdout.strip().splitlines()[-1])
    assert table["self"] == engines.LYPNING_L
    assert table["self_caps"] == ["cap-collections"]
    assert {r["name"]: tuple(r["caps"]) for r in table["spectrum"]} == engines.VARIANT_CAPS
    assert {r["cap"]: r["modules"] for r in table["caps"]} == {"cap-collections": ["collections"]}
