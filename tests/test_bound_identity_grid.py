"""`json.dumps`, `x.append`, `str.upper`: one `Value::Bound`, five questions.

A module-level function read as a VALUE is `Value::Bound(receiver, name)`, built
fresh on every attribute access — and `value::eq`, `value::is_same` and the
hash/`HKey` path had no arm for it at all. So every one of these was wrong at
exit 0 or dead at exit 1 on the tree before this file:

    import json; json.dumps is json.dumps      True in CPython -> False
    import csv;  csv.reader == csv.reader      True in CPython -> False
    import os;   os.getcwd == os.getcwd        True in CPython -> False
    x = [1];     x.append == x.append          True in CPython -> False
    import csv;  len({csv.reader})             1 in CPython -> TypeError, exit 1

They were mostly unreachable while the modules concerned were unserved, which is
why three separate capabilities landing in one round found the same hole.

**The rule, measured on CPython 3.14.5 on 2026-09-06 rather than read from the
manual.** CPython has two types here and they agree on every question below:
`builtin_function_or_method` (`meth_richcompare`, `meth_hash`) and `method`
(`method_richcompare`, `method_hash`).

  * `==` is the FUNCTION and the receiver's IDENTITY. `x.append == x.append` is
    True, `x.append == y.append` is False for two EQUAL lists, and
    `x.append == x.pop` is False.
  * `hash` is built from the same pair, so all three answers survive into a
    dict key and a set element.
  * `is` is False between two accesses — a bound method is BUILT on access —
    EXCEPT where the attribute is one that is stored and handed back: a module
    function in the module's `__dict__` (`json.dumps is json.dumps`) and a
    `method_descriptor` in a type's (`str.upper is str.upper`).

The three receivers that look like the exception and are not are rows here,
because each is spelled `Value::Module` in the engine and none of them is a
module: `sys.stdout` is a `TextIOWrapper` INSTANCE (`sys.stdout.write is
sys.stdout.write` is False, `==` is True) and `Path.cwd` is a CLASSMETHOD, which
builds a bound method exactly as an instance does.

Every row runs on BOTH variants, not just `lypning-l`. `value.rs` is the frozen
core and is not capability-gated, so an answer that differs between the two
would be invariant 10's monotonicity broken by this very fix; `cap-pathlib`,
`cap-re` and `cap-csv` rows simply refuse on the core, which is allowed and is
what the skip below records.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths


def _binary(engine: str) -> "Path | None":
    """A built `engine` — THIS TREE's cargo output first, then whatever is installed.

    That order is the opposite of the `lypning_bin` fixture's and it is
    deliberate. These rows pin a change to `value.rs`, so the only binary that
    can answer them is one built from this checkout; `$LYPNING_HOME` is shared
    between sessions and the binary installed there may be another branch's,
    which would fail every row below for a reason that has nothing to do with
    the tree under test. The install dir stays as the fallback because a wheel
    has no target dir at all and is the shape a `pip` user actually runs.
    """
    target = {engines.LYPNING: paths.RUST_DIR / "target" / "release" / "lypning",
              engines.LYPNING_L: paths.RUST_DIR / "target" / "variant-l" / "release" / "lypning"}
    found = engines.find(engine)
    for cand in [target[engine]] + ([Path(found)] if found else []):
        if cand.is_file():
            return cand
    return None


BINARIES = [(e, _binary(e)) for e in (engines.LYPNING, engines.LYPNING_L)]
IDS = [e for e, _ in BINARIES]


#: `(id, program)`. Each row prints every answer it compares, so a failure names
#: the question rather than only the row.
GRID = [
    # ---- a module function: the attribute CPython STORES ------------------
    ("module-function-is-itself",
     "import json\nprint(json.dumps is json.dumps, json.dumps == json.dumps)"),
    ("module-function-through-a-name",
     "import json\nf = json.dumps\nprint(f is f, f == f, f is json.dumps, f == json.dumps)"),
    ("module-function-two-different-ones",
     "import json\nprint(json.dumps is json.loads, json.dumps == json.loads)"),
    ("module-function-across-two-modules",
     "import json, os\nprint(json.dumps == os.getcwd, json.dumps is os.getcwd)"),
    ("os-and-os-path-are-two-modules",
     "import os\nprint(os.getcwd is os.getcwd, os.path.join is os.path.join,\n"
     "      os.getcwd is os.path.join, os.getcwd == os.path.join)"),
    ("sys-exit-is-a-module-function",
     "import sys\nprint(sys.exit is sys.exit, sys.exit == sys.exit)"),
    ("random-is-a-module-function-too",
     "import random\nprint(random.randint is random.randint, random.random == random.random)"),
    ("re-module-function", "import re\nprint(re.search is re.search, re.search == re.search)"),
    ("csv-module-function", "import csv\nprint(csv.reader == csv.reader, csv.reader is csv.reader)"),
    ("glob-module-function",
     "import glob\nprint(glob.escape is glob.escape, glob.escape == glob.escape)"),

    # ---- a type object: the `method_descriptor` a type's dict stores -------
    ("type-object-unbound-method",
     "print(str.upper is str.upper, str.upper == str.upper)"),
    ("type-object-two-different-methods",
     "print(str.upper is str.lower, str.upper == str.lower)"),
    ("type-object-across-two-types",
     "print(list.append == list.append, list.append == dict.get, str.upper == bytes.hex)"),

    # ---- an instance: the attribute CPython BUILDS -------------------------
    ("list-instance-method", "x = [1]\nprint(x.append is x.append, x.append == x.append)"),
    ("list-instance-two-equal-receivers",
     "x = [1]\ny = [1]\nprint(x.append is y.append, x.append == y.append, x == y)"),
    ("list-instance-two-methods", "x = [1]\nprint(x.append == x.pop, x.append is x.pop)"),
    ("instance-method-through-a-name",
     "x = [1]\nm = x.append\nprint(m is m, m == m, m == x.append, m is x.append)"),
    ("dict-instance-method",
     "d = {}\ne = {}\nprint(d.get == d.get, d.get == e.get, d.get is d.get, d.get == d.keys)"),
    ("set-instance-method", "s = {1}\nprint(s.add == s.add, s.add is s.add)"),
    ("file-instance-method",
     "f = open('f.txt', 'w')\nprint(f.write == f.write, f.write is f.write, f.write == f.close)"),
    ("a-counter-is-a-dict-instance",
     "from collections import Counter\nc = Counter('ab')\n"
     "print(c.most_common == c.most_common, c.most_common is c.most_common)"),

    # ---- the three receivers that look like modules and are not ------------
    ("sys-stdout-is-an-instance-not-a-module",
     "import sys\nprint(sys.stdout.write is sys.stdout.write,\n"
     "      sys.stdout.write == sys.stdout.write,\n"
     "      sys.stdout.write == sys.stderr.write)"),
    ("sys-stdin-is-an-instance-not-a-module",
     "import sys\nprint(sys.stdin.read is sys.stdin.read, sys.stdin.read == sys.stdin.read)"),
    ("path-cwd-is-a-classmethod",
     "from pathlib import Path\nprint(Path.cwd is Path.cwd, Path.cwd == Path.cwd)"),

    # ---- the capability receivers -----------------------------------------
    ("path-instance-method",
     "from pathlib import Path\np = Path('a')\nq = Path('a')\n"
     "print(p.exists is p.exists, p.exists == p.exists, p.exists == q.exists, p == q)"),
    ("path-instance-two-methods",
     "from pathlib import Path\np = Path('a')\nprint(p.exists == p.is_dir)"),
    ("path-parent-is-a-new-object",
     "from pathlib import Path\np = Path('a/b')\nprint(p.parent.exists == p.parent.exists)"),
    ("pattern-instance-method",
     "import re\np = re.compile('a')\nq = re.compile('a')\n"
     "print(p.search == p.search, p.search == q.search, p.search is p.search)"),
    ("match-instance-method",
     "import re\nm = re.match('a', 'a')\nprint(m.group == m.group, m.group is m.group)"),

    # ---- the hash, which must agree with `==` on every one of those --------
    ("module-function-as-a-dict-key",
     "import os\nprint({os.getcwd: 1}[os.getcwd], os.getcwd in {os.getcwd: 1})"),
    ("module-function-as-a-set-element",
     "import csv\nprint(len({csv.reader}), len({csv.reader, csv.reader}),\n"
     "      len({csv.reader, csv.DictReader}))"),
    ("two-module-functions-are-two-keys",
     "import json\nd = {json.dumps: 1, json.loads: 2}\nprint(len(d), d[json.dumps], d[json.loads])"),
    ("instance-method-as-a-set-element",
     "x = [1]\ny = [1]\nprint(len({x.append, x.append}), len({x.append, x.pop}),\n"
     "      len({x.append, y.append}))"),
    ("instance-method-as-a-dict-key",
     "x = [1]\nd = {x.append: 'a'}\nprint(d[x.append], x.append in d, x.pop in d)"),
    ("type-object-method-as-a-set-element",
     "print(len({str.upper, str.upper, str.lower}), len({list.append, dict.get}))"),
    ("a-bound-method-is-not-the-tuple-that-spells-it",
     "import json\nprint(len({json.dumps, ('json', 'dumps')}))"),
    ("path-method-as-a-set-element",
     "from pathlib import Path\np = Path('a')\nq = Path('a')\n"
     "print(len({p.exists, p.exists}), len({p.exists, q.exists}))"),
    ("pattern-and-match-methods-as-set-elements",
     "import re\np = re.compile('a')\nm = p.match('a')\n"
     "print(len({p.search, p.search}), len({m.group, m.group}), len({p.search, m.group}))"),
    ("a-dict-of-module-functions-round-trips",
     "import json, os\nd = {json.dumps: 'j', os.getcwd: 'o', str.upper: 's'}\n"
     "print(d[json.dumps], d[os.getcwd], d[str.upper], len(d))"),

    # ---- everything `==` reaches through: `in`, count, index, remove -------
    ("in-and-count-over-a-list-of-bound-methods",
     "x = [1]\nprint(x.append in [x.append], [x.append].count(x.append),\n"
     "      x.pop in [x.append], [x.append, x.pop].index(x.pop))"),
    ("remove-a-bound-method-from-a-list",
     "x = [1]\nl = [x.append, x.pop]\nl.remove(x.append)\nprint(len(l), l[0] == x.pop)"),
    ("bound-methods-nested-in-containers",
     "import json\nprint([json.dumps] == [json.dumps], (json.dumps,) == (json.dumps,),\n"
     "      {'f': json.dumps} == {'f': json.dumps}, json.dumps != json.dumps)"),
    ("in-over-a-set-of-bound-methods",
     "import json\nprint(json.dumps in {json.dumps}, json.loads in {json.dumps})"),

    # ---- the neighbours the checklist names, unchanged and pinned ----------
    ("bool-of-a-bound-method", "import json\nprint(bool(json.dumps), not json.dumps)"),
    ("ordering-a-bound-method-is-a-typeerror",
     "import json\ntry:\n    json.dumps < json.dumps\nexcept TypeError:\n    print('TypeError')"),
    ("len-of-a-bound-method-is-a-typeerror",
     "import json\ntry:\n    len(json.dumps)\nexcept TypeError:\n    print('TypeError')"),
    ("iterating-a-bound-method-is-a-typeerror",
     "import json\ntry:\n    list(json.dumps)\nexcept TypeError:\n    print('TypeError')"),
    ("json-dumps-of-a-bound-method-is-a-typeerror",
     "import json\ntry:\n    json.dumps(json.dumps)\nexcept TypeError:\n    print('TypeError')"),
]


def _run(argv, program: str) -> subprocess.CompletedProcess:
    """One row, in a temp cwd of its own — invariant 4; some rows open files."""
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True,
                              text=True, cwd=d, timeout=60)


def _refusal_problem(got: subprocess.CompletedProcess, engine: str) -> "str | None":
    """`None` if this is a clean exit-90 refusal, else what is wrong with it."""
    if got.stdout != "":
        return "stdout was not empty: %r" % got.stdout[:120]
    head = "%s: unsupported: " % engine
    line = got.stderr.strip()
    if not line.startswith(head) or "\n" in line:
        return "stderr was %r, expected one %r line" % (line[:160], head)
    return None


@pytest.mark.parametrize("engine,binary", BINARIES, ids=IDS)
@pytest.mark.parametrize("program", [p for _, p in GRID], ids=[i for i, _ in GRID])
def test_the_bound_identity_grid_agrees_with_cpython(engine, binary, program):
    if binary is None:
        pytest.skip("%s is not built (`lypning build --rust`)" % engine)
    got = _run([str(binary)], program)
    if got.returncode == engines.UNSUPPORTED_EXIT:
        # Always allowed and never a bug (invariant 1) — but it must be a CLEAN
        # refusal, and it must be reported, because a row that started refusing
        # is a row that has stopped measuring anything.
        problem = _refusal_problem(got, engine)
        assert problem is None, "%s\n  program: %r" % (problem, program)
        pytest.skip("%s refuses this row: %s" % (engine, got.stderr.strip()[:160]))
    ref = _run([sys.executable], program)
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "%s disagrees with CPython.\n  program:  %r\n  %s: %r exit %d %s\n  cpython:   %r exit %d"
        % (program, engine, got.stdout, got.returncode, got.stderr.strip()[-200:],
           ref.stdout, ref.returncode)
    )


#: The receivers whose identity is CPython's INTERNING and not a fact about the
#: program: `'abc'.upper == 'abc'.upper` is True because the compiler folds the
#: two constants into one object, and the same comparison over a string built at
#: run time is False. Which one a program wrote is not visible from inside the
#: engine — `ops::identity` refuses `is` between equal immutables for exactly
#: this reason — so `==` between bound methods OF them, and using one as a dict
#: key, refuse too. A refusal is coverage; a guess would be wrong for one of the
#: two halves at exit 0.
INTERNED = [
    ("str-literal-receivers", "print('abc'.upper == 'abc'.upper)"),
    ("str-runtime-built-receiver",
     "s = 'abc'\nt = ''.join(['a', 'b', 'c'])\nprint(s.upper == t.upper)"),
    ("bytes-receivers", "print(b'ab'.hex == b'ab'.hex)"),
    ("tuple-receivers", "print((1, 2).count == (1, 2).count)"),
    ("str-receiver-as-a-set-element", "print(len({'abc'.upper}))"),
    ("tuple-receiver-as-a-dict-key", "print({(1, 2).count: 1})"),
]


@pytest.mark.parametrize("engine,binary", BINARIES, ids=IDS)
@pytest.mark.parametrize("program", [p for _, p in INTERNED], ids=[i for i, _ in INTERNED])
def test_an_interned_receiver_refuses_rather_than_guesses(engine, binary, program):
    if binary is None:
        pytest.skip("%s is not built (`lypning build --rust`)" % engine)
    got = _run([str(binary)], program)
    assert got.returncode == engines.UNSUPPORTED_EXIT, (
        "%s answered %r (exit %d) where the receiver's identity is interning's:\n  %r"
        % (engine, got.stdout, got.returncode, program))
    problem = _refusal_problem(got, engine)
    assert problem is None, "%s\n  program: %r" % (problem, program)
    assert ": identity: " in got.stderr, (
        "the refusal kind must be `identity` — `engines.ONLY_CPYTHON_REFUSALS` "
        "sends it straight to CPython rather than one tier down. Got: %r" % got.stderr.strip())


#: The one receiver kind whose two accesses ARE the same object and whose `==`
#: is still False, because the SAME receiver is what makes them equal.
def test_a_receiver_that_is_equal_is_not_a_receiver_that_is_the_same():
    """`x.append == y.append` is False for two lists that compare equal.

    The trap this pins is the obvious wrong implementation: comparing the
    receivers with `==` instead of `is`. It answers True here and False in
    CPython, at exit 0, on a program that reads as though it should be True —
    which is the whole failure mode invariant 1 exists for.
    """
    core = _binary(engines.LYPNING)
    if core is None:
        pytest.skip("the Rust core is not built (`lypning build --rust`)")
    program = ("x = [1, 2]\ny = [1, 2]\n"
               "print(x == y, x.append == y.append, len({x.append, y.append}))\n")
    got = _run([str(core)], program)
    ref = _run([sys.executable], program)
    assert got.stdout == ref.stdout == "True False 2\n", (got.stdout, ref.stdout)


def test_the_two_variants_answer_a_bound_method_alike():
    """Invariant 10, on the value this commit touched.

    `value.rs` is the frozen core and none of it is capability-gated, so the
    core and `lypning-l` must give the same answer to every row that neither
    refuses. A row only `lypning-l` can run (`pathlib`, `re`, `csv`) refuses on
    the core, which is the allowed direction; the reverse — the core answering
    where the larger variant refuses — is the monotonicity break.
    """
    core, large = _binary(engines.LYPNING), _binary(engines.LYPNING_L)
    if core is None or large is None:
        pytest.skip("both variants must be built (`lypning build --rust`)")
    for name, program in GRID + INTERNED:
        a, b = _run([str(core)], program), _run([str(large)], program)
        if a.returncode == engines.UNSUPPORTED_EXIT:
            continue  # the core refuses more; that is the spectrum working
        assert b.returncode != engines.UNSUPPORTED_EXIT, (
            "%s: the core answered %r and lypning-l refused — a router that sent "
            "this program to the larger variant would get a refusal for a program "
            "the smaller one runs.\n  %s" % (name, a.stdout, b.stderr.strip()[:200]))
        assert (a.stdout, a.returncode) == (b.stdout, b.returncode), (
            "%s: the core said %r exit %d, lypning-l said %r exit %d"
            % (name, a.stdout, a.returncode, b.stdout, b.returncode))
