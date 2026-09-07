"""`repr()` of a CLASS, as a grid: every program on every built variant and on CPython.

`tests/test_glob_grid.py` is the shape this follows and the reason it exists:
the defect is PER PROGRAM, not per function, and a handful of examples is
exactly what would miss it.

Every row runs from a FRESH temp cwd (invariant 4) and must end one of exactly
two ways:

  * byte-identical stdout, the same exit code, and the same final stderr line as
    the reference CPython, or
  * a clean refusal — exit 90, nothing on stdout, one
    ``<engine>: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

Stderr is compared as its LAST line rather than whole. CPython's traceback body
names ``File "<string>", line 1`` and, for anything raised inside a stdlib
module, the absolute path of that module in the reference installation — text
that is a property of the interpreter's own filesystem and that no second
implementation could write even if it wanted to. The exception line is the half
both are claiming, so it is the half compared.

**The one thing this file is really for.** CPython's repr of a callable is six
different texts, and only one of them can be reproduced by a second process:

===========================  ====================================================
`repr(int)`                  ``<class 'int'>``
`repr(collections.Counter)`  ``<class 'collections.Counter'>`` — MODULE-QUALIFIED
`repr(len)`                  ``<built-in function len>``
`repr(str.upper)`            ``<method 'upper' of 'str' objects>``
`repr(json.dumps)`           ``<function dumps at 0x7f…>`` — **an address**
`repr([].append)`            ``<built-in method append of list object at 0x…>``
`repr(json)`                 ``<module 'json' from '/…/json/__init__.py'>`` — **a path**
===========================  ====================================================

An address is this process's heap and a path is this installation's filesystem;
neither can ever equal another interpreter's, so serving either would be a wrong
answer at exit 0 on a program whose author would not look twice. `REFUSED`
below is one row per shape that must never be answered, and it is the table that
matters — the served half is only a grid.

**The table is not `tp_name`, and that is the trap inside the trap.**
`type.__repr__` prints ``<class '{__module__}.{__qualname__}'>`` and elides the
module only for `builtins`, while `tp_name` — which `builtins::class_name`
answers, and which every `AttributeError` in this engine prints — is bare for
`Counter` and dotted only for `collections.defaultdict`. Reusing one table for
both would print ``<class 'Counter'>`` at exit 0. `MODULE_QUALIFIED` is that
difference asserted against the reference rather than recalled.

**Two names refuse because this crate collapses them onto one value.**
`modules.rs` answers ``Value::Builtin("Path")`` for both `pathlib.Path` and
`pathlib.PosixPath`, and ``Value::Builtin("ValueError")`` for both `ValueError`
and `json.JSONDecodeError`. The collapse is right where it was made — neither
`isinstance` nor `except` can tell the members of a pair apart — and wrong here,
because `repr` can. `ALIASED` runs the reference to show the two spellings
really do differ, and then asserts the engine refuses rather than picking one.
If a future CPython makes a pair's spellings equal, that test is what says the
refusal could be lifted.

**No new `Value` variant.** A class object was already `Value::Builtin`; this
round added a repr for it and nothing else. `RESULT_USES` is the check that the
value is reached correctly through every other arm — print, `==`, `in`, `bool`,
`len`, `iter`, `.index`, `sorted`, augmented assignment, `json.dumps` and
`%`-formatting — because five capabilities in a row shipped a variant that
reached one of those through an arm nobody remembered (`docs/HILLCLIMB.md`
iterations 74, 76 and 77).

**The barrier class.** `io.rs` stages writes until the run ends, so a refusal
discards them — which is what makes a runtime refusal still a clean 90 with the
disk untouched. `AFTER_A_BARRIER` measures that rather than assuming it: each
refusal shape is run as ``os.mkdir('NEWD'); <call>`` and the cwd is listed
before and after, because a refusal that landed after a committed write would be
exit 1 with the side effect on disk and no answer, which the chain never
retries.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

# ---------------------------------------------------------------- the spec ---

#: CPython 3.14.5's builtin TYPE objects, read off `builtins` on 2026-09-07 by
#: asking `type(getattr(builtins, n)).__name__` rather than by reading a manual.
#: Each reprs as `<class 'NAME'>`, and this list is the engine's whole builtin
#: class namespace on the type side.
TYPE_OBJECTS = (
    "bool", "bytes", "dict", "enumerate", "filter", "float", "int", "list",
    "map", "range", "reversed", "set", "str", "tuple", "type", "zip",
)

#: The exception classes the engine can name. `IOError` is in the list on
#: purpose and is NOT an alias problem: `IOError is OSError` is one class under
#: two names, so CPython's own repr of it is `<class 'OSError'>` — which is what
#: makes it the control for `ALIASED` below.
EXCEPTION_NAMES = (
    "ArithmeticError", "AssertionError", "AttributeError", "BaseException",
    "Exception", "FileExistsError", "FileNotFoundError", "IndexError",
    "KeyError", "LookupError", "NameError", "NotImplementedError",
    "OverflowError", "OSError", "IOError", "PermissionError", "RuntimeError",
    "StopIteration", "SystemExit", "TypeError", "UnboundLocalError",
    "UnicodeDecodeError", "ValueError", "ZeroDivisionError",
)

#: `print(<class>)` for every name above. `ValueError` is here rather than held
#: out: the grid's rule admits a clean refusal on any row, and `ALIASED` is
#: where that particular refusal is pinned as deliberate.
NAMED_CLASSES = ["print(%s)" % n for n in TYPE_OBJECTS + EXCEPTION_NAMES]

#: Reached through `type()` instead of by name — the shape all fifteen corpus
#: programs actually type. One row per concrete value `type()` serves.
THROUGH_TYPE = ["print(type(%s))" % v for v in (
    "1", "-1", "0", "1.5", "True", "False", "'a'", "''", "b'x'", "b''",
    "[]", "[1, 2]", "{}", "{'a': 1}", "()", "(1,)", "{1}", "{1, 2}",
)]

#: The mined corpus shape, in full: fifteen programs load JSON and print the
#: type of what came back. `type(d)` is `dict` for an object, `list` for an
#: array and `str` for a member — the three spellings the mine measured on
#: 2026-09-07, and between them they are the whole of what this row buys.
CORPUS_SHAPE = [
    "import json\nd = json.loads('{\"a\": 1}')\nprint(type(d), len(d))",
    "import json\nd = json.loads('[1, 2, 3]')\nprint(type(d), len(d))",
    "import json\nd = json.loads('{\"a\": \"x\"}')\nprint(type(d['a']))",
    "import json\nd = json.loads('{\"a\": [1]}')\nprint(type(d), type(d['a']))",
    "import json\nd = json.loads('{}')\nprint(type(d), list(d)[:8])",
    "import json\nd = json.loads('{\"a\": 1}')\n"
    "for k, v in d.items():\n    print(k, type(v))",
    "import json\nd = json.loads('{\"a\": 1, \"b\": [2]}')\n"
    "for k, v in d.items():\n    print(k, type(v), (len(v) if isinstance(v, (list, dict)) else v))",
    "import json\nd = json.loads('{\"a\": 1}')\nprint(type(d), len(d))\n"
    "print(json.dumps(d)[:500])",
    "import json\nd = json.loads('[{\"a\": 1}]')\nprint(type(d), len(d))\n"
    "print(sorted(d[0].keys()))",
    "import json\nd = json.loads('{\"a\": 1}')\n"
    "print(type(d), list(d)[:20] if isinstance(d, dict) else len(d))",
]

#: Every coercion that reaches `repr` by another name. `type.__str__` is
#: inherited from `object`, so `str(int)` IS `repr(int)` — but the engine
#: reaches it through `fmt::to_str`, `fmt::to_rc` and `fmt::repr_rc`, which are
#: three different functions and were three chances to miss the new arm.
COERCIONS = [
    "print(repr(int))",
    "print(str(int))",
    "print(f'{int}')",
    "print(f'{int!r}')",
    "print(f'{int!s}')",
    "print('%s' % int)",
    "print('%r' % int)",
    "print('%s and %s' % (int, str))",
    "print('{}'.format(int))",
    "print('{!r}'.format(int))",
    "print(format(int))",
    # `%` is NOT `format()`, and the split is the reason the two sit in
    # different tables. `'%30s' % cls` pads the STRING — `str()` first, width
    # after — so CPython answers it and so does this engine. `f'{cls:>30}'`
    # hands the spec to `object.__format__`, which raises for any non-empty
    # spec whatever the object is. Both measured on the reference, because
    # guessing which way round they went is exactly how a wrong answer ships.
    "print('%10s' % int)",
    "print('%30s' % int)",
    "print('%-30s|' % int)",
    "print('%.5s' % int)",
    "print('%30r' % int)",
    "print(int.__class__ if False else str(dict))",
    "print(''.join([str(int), str(str)]))",
    "print(len(str(int)))",
    "print(str(int).startswith('<class'))",
]

#: A class inside a container, which is where a repr that raised would take the
#: whole structure with it.
COMPOSED = [
    "print([int, str, dict])",
    "print((int, str))",
    "print({'t': int})",
    "print({'a': [int], 'b': (str,)})",
    "print([[int], [[str]]])",
    "print([type(1), type('a'), type([]), type({})])",
    "import json\nprint([type(x) for x in json.loads('[1, \"a\", [], {}]')])",
    "print(list(map(str, [int, str])))",
    "print(sorted(str(t) for t in [str, int, dict]))",
    "print(', '.join(str(t) for t in (int, str)))",
    "print({'types': [str(int), str(list)]})",
]

#: `collections` — the one place `tp_name` and the repr spelling disagree, and
#: therefore the one place a single table would have printed a wrong answer.
COLLECTIONS = [
    "from collections import Counter\nprint(Counter)",
    "import collections\nprint(collections.Counter)",
    "from collections import defaultdict\nprint(defaultdict)",
    "import collections\nprint(collections.defaultdict)",
    "import collections\nprint([collections.Counter, dict])",
    "import collections\nprint(collections.defaultdict(list))",
    "import collections\nprint(collections.defaultdict(int))",
    "import collections\nprint(str(collections.Counter))",
]

GRID = NAMED_CLASSES + THROUGH_TYPE + CORPUS_SHAPE + COERCIONS + COMPOSED + COLLECTIONS

# --------------------------------------------------------- what must refuse ---

#: One row per CPython callable-repr shape that is NOT a plain class, each with
#: the reason it can never be served. Every one of these is a program CPython
#: answers, so an answer here would be a wrong one at exit 0 — which is the
#: whole reason this capability is a table and not a `format!`.
REFUSED = [
    # …because the text carries a heap ADDRESS.
    ("import json\nprint(json.dumps)", "<function dumps at 0x…>"),
    ("import json\nprint(json.loads)", "<function loads at 0x…>"),
    ("print([].append)", "<built-in method append of list object at 0x…>"),
    ("print('a'.upper)", "<built-in method upper of str object at 0x…>"),
    ("print({}.get)", "<built-in method get of dict object at 0x…>"),
    ("print((1).bit_length)", "<built-in method bit_length of int object at 0x…>"),
    # …because the text carries a filesystem PATH, or a marker that depends on
    # how the reference interpreter was built.
    ("import json\nprint(json)", "<module 'json' from '/…'>"),
    ("import os\nprint(os)", "<module 'os' (frozen)>"),
    ("import sys\nprint(sys)", "<module 'sys' (built-in)>"),
    # …because it is a different shape from `<class …>` and out of this slice:
    # reproducible, but not built, so it refuses rather than guessing.
    ("print(len)", "<built-in function len>"),
    ("print(print)", "<built-in function print>"),
    ("print(open)", "<built-in function open>"),
    ("print(str.upper)", "<method 'upper' of 'str' objects>"),
    ("print(list.append)", "<method 'append' of 'list' objects>"),
    # …because a format SPEC on a class is CPython's TypeError to word, not
    # this engine's — `object.__format__` raises for any non-empty spec.
    ("print(f'{int:>30}')", "TypeError from object.__format__"),
    ("print(format(int, '>30'))", "TypeError from object.__format__"),
]

#: The alias pairs. Each is TWO CPython classes that this crate answers with ONE
#: `Value::Builtin`, so nothing in the value says which spelling to print.
#: ``(program, left, right)`` — `test_the_aliased_names_really_are_two_classes`
#: runs the reference to prove `repr(left) != repr(right)` before asserting the
#: engine refuses, so the refusal is justified by measurement and not by memory.
ALIASED = [
    ("print(ValueError)", "ValueError", "__import__('json').JSONDecodeError"),
    ("import json\nprint(json.JSONDecodeError)", "ValueError",
     "__import__('json').JSONDecodeError"),
    ("import pathlib\nprint(pathlib.Path)", "__import__('pathlib').Path",
     "__import__('pathlib').PosixPath"),
    ("import pathlib\nprint(pathlib.PosixPath)", "__import__('pathlib').Path",
     "__import__('pathlib').PosixPath"),
    ("from pathlib import Path\nprint(Path)", "__import__('pathlib').Path",
     "__import__('pathlib').PosixPath"),
]

#: The control for `ALIASED`: `IOError` is aliased too, and it is SERVED,
#: because the two names are one class and CPython's own repr proves it.
NOT_ALIASED = ("IOError", "OSError")

#: **The third instance of the `ALIASED` rule, and the one that arrives from the
#: other direction.** `ALIASED` is one engine value standing for two CPython
#: CLASSES; this is one engine value standing for a CPython class the engine has
#: no name for at all. `modules.rs` materialises `os.environ` as a `Value::Dict`,
#: so `type()` read `dict` off it and — once `repr` learned to serve class
#: objects — printed ``<class 'dict'>`` at exit 0 where CPython prints
#: ``<class 'os._Environ'>``. The parent commit refused it only because no
#: `Value::Builtin` had a repr yet; the refusal has to be deliberate now.
#:
#: ``(program, what_cpython_prints_instead)``. Every row must refuse: the engine
#: cannot spell `os._Environ`, and every one of these would otherwise print a
#: dict's answer for something that is not a dict.
NOT_A_DICT = [
    ("import os\nprint(type(os.environ))", "<class 'os._Environ'>"),
    ("import os\nprint(repr(type(os.environ)))", "<class 'os._Environ'>"),
    ("import os\ne = os.environ\nprint(type(e))", "<class 'os._Environ'>"),
    ("import os\nprint(str(type(os.environ)))", "<class 'os._Environ'>"),
    ("import os\nprint(f'{type(os.environ)}')", "<class 'os._Environ'>"),
    ("import os\nprint('%s' % type(os.environ))", "<class 'os._Environ'>"),
    ("import os\nprint([type(os.environ)])", "[<class 'os._Environ'>]"),
    # The mapping ITSELF, not its class: `_Environ.__repr__` writes
    # `environ({…})` around the braces, so the dict repr is wrong here too.
    ("import os\nprint(os.environ)", "environ({…})"),
    ("import os\nprint(repr(os.environ))", "environ({…})"),
    ("import os\nprint(str(os.environ))", "environ({…})"),
    ("import os\nprint('%s' % os.environ)", "environ({…})"),
    ("import os\nprint({'e': os.environ})", "{'e': environ({…})}"),
    # And its views, which name the mapping inside the wrapper.
    ("import os\nprint(os.environ.keys())", "KeysView(environ({…}))"),
    ("import os\nprint(os.environ.items())", "ItemsView(environ({…}))"),
    ("import os\nprint(os.environ.values())", "ValuesView(environ({…}))"),
]

#: **A pre-existing gap this round found and did NOT close, pinned so it cannot
#: get quietly worse.** `modules::get_attr` rebuilds `os.environ` from
#: `std::env::vars()` on every attribute access, so a write through one access
#: is not visible through the next: `os.environ['X'] = 'v'` then
#: `os.environ.get('X')` answers `None` at exit 0 where CPython answers `'v'`.
#: Bound to a name first — `e = os.environ; e['X'] = 'v'` — it works, because
#: then there is one dict; that row is in `STILL_A_DICT` above.
#:
#: It is not this round's and is not caused by it: naming the mapping changed
#: what `type()`, `repr` and `isinstance` say about it and nothing about how it
#: is BUILT, and this failed the same way before the tag existed. Closing it
#: needs the mapping to be materialised once and to reach `putenv`, since
#: CPython's `os.environ[k] = v` is visible to a child process. What is asserted
#: is only that the engine still DISAGREES here — so the day someone fixes it,
#: this test fails and says to move the row up into `STILL_A_DICT`.
WRITES_DO_NOT_STICK_TODAY = [
    "import os\nos.environ['LYP_X'] = 'v'\nprint(os.environ.get('LYP_X'))",
    "import os\nos.environ['LYP_X'] = 'v'\nprint('LYP_X' in os.environ)",
    "import os\nos.environ.setdefault('LYP_X', 'v')\nprint(os.environ.get('LYP_X'))",
]

#: The control for `NOT_A_DICT`, and the half that must NOT have been
#: over-corrected: a plain dict is still a dict, a copy of `os.environ` is a
#: plain dict in CPython too, and reading the mapping was never the problem.
STILL_A_DICT = [
    ("print(type({}))", "<class 'dict'>\n"),
    ("print(type({'a': 1}))", "<class 'dict'>\n"),
    ("import os\nprint(type(dict(os.environ)))", "<class 'dict'>\n"),
    ("import os\nprint(type(os.environ.copy()))", "<class 'dict'>\n"),
    ("import os\nprint(type(os.environ.get('PATH', '')))", "<class 'str'>\n"),
    ("import os\ne = os.environ\ne['LYP_X'] = 'v'\nprint(e['LYP_X'])", "v\n"),
    ("import os\nprint(len(os.environ) > 0)", "True\n"),
    ("import os\nprint(sorted(os.environ) == sorted(dict(os.environ)))", "True\n"),
    ("import os\nprint(os.environ == dict(os.environ))", "True\n"),
    ("import os\nprint(isinstance(os.environ, dict))", "False\n"),
]

#: The `tp_name` / repr split, as programs. `Counter`'s `tp_name` is bare and
#: its repr is module-qualified; `defaultdict`'s are both dotted; every builtin
#: is bare in both. One table for the two would have printed `<class 'Counter'>`.
MODULE_QUALIFIED = (
    ("from collections import Counter\nprint(Counter)", "collections.Counter"),
    ("from collections import defaultdict\nprint(defaultdict)",
     "collections.defaultdict"),
    ("print(int)", "int"),
    ("print(ZeroDivisionError)", "ZeroDivisionError"),
)

#: Every other arm a class value can be reached through. Not repr — that is the
#: point: a capability that shipped one of these wrong would be a wrong answer
#: nobody was looking at, which is the defect five rounds in a row shipped.
RESULT_USES = [
    "print(int == int, int == str, int != str, int is int)",
    "print(int in (int, str), dict in [list, dict], float in (int,))",
    "print(bool(int), bool(dict))",
    "print([int, str].index(str))",
    "print([int, str].count(int))",
    "print(len([int, str]))",
    "print(list((int, str)) == [int, str])",
    "t = int\nt = str\nprint(t)",
    "d = {'a': int}\nd['b'] = str\nprint(d)",
    "xs = [int]\nxs.append(str)\nxs += [dict]\nprint(xs)",
    "print(len(int))",                     # TypeError, byte-identical message
    "print(list(int))",                    # TypeError, not iterable
    "for x in int:\n    pass",             # TypeError, not iterable
    "print(sorted([int, str]))",           # TypeError, '<' unsupported
    "print(int < str)",                    # TypeError, '<' unsupported
    "print(int + 1)",                      # TypeError, unsupported operand
    "print(abs(int))",                     # TypeError, bad operand
    "import json\nprint(json.dumps(int))",  # TypeError, not JSON serializable
    "print(int.nope)",                     # AttributeError names the class
    "print(isinstance(1, int), isinstance('a', int))",
    "print(isinstance(1, (int, str)))",
]

#: `hash()` of a class is `id(cls) // 16` in CPython — an ADDRESS wearing an
#: integer's clothes. It must never be answered, whatever else this round
#: served, and the engine has no `hash` builtin at all, so it refuses.
HASHED = ["print(hash(int))", "print(hash(str) == hash(str))"]

#: **A pre-existing gap this round did NOT close, pinned so it cannot get
#: quietly worse.** A class is not hashable here, so `{int: 1}` is a `TypeError`
#: at exit 1 where CPython answers `{<class 'int'>: 1}` at exit 0 — a program
#: reported as broken that CPython runs, and the dispatcher cannot fall through
#: an exit 1. It is not this round's to fix and it is not caused by it: the
#: failure is in the HASH, before `repr` is ever called, and it failed the same
#: way before this arm existed. It also cannot be fixed by making
#: `Value::Builtin` hashable while `Path` and `ValueError` are aliases —
#: `{pathlib.Path: 1, pathlib.PosixPath: 2}` would then collapse two keys into
#: one and print a wrong answer at exit 0, which is strictly worse than exit 1.
#: What is asserted is the only thing that is safe today: no answer on stdout.
UNHASHABLE_TODAY = [
    "print({int: 1})",
    "d = {int: 1}\nprint(len(d))",
    "print({int, str})",
    "print(set([int]))",
]

#: The barrier every row of `AFTER_A_BARRIER` is asked through. `os.mkdir` is
#: the cheapest thing that can commit it, and the cwd listing is what the test
#: reads — never the refusal message, which is the half that was already right.
BARRIER = "import os\nos.mkdir('NEWD')\n"

#: One row per refusal KIND a class-repr program can still raise, each run past
#: a committed write. These are the shapes that stay refused, so each of them is
#: a program that must end at exit 90 with the directory NOT created.
AFTER_A_BARRIER = [
    ("repr", "print(len)"),
    ("repr", "print(str.upper)"),
    ("repr", "print([].append)"),
    ("repr", "print(ValueError)"),
    ("repr", "import json\nprint(json.dumps)"),
    ("repr", "import json\nprint(json)"),
    ("repr", "import json\nprint(json.JSONDecodeError)"),
    ("repr", "import sys\nprint(sys)"),
    ("format", "print(f'{int:>30}')"),
    ("format", "print(format(int, '<8'))"),
]

# ------------------------------------------------------------- the harness ---


def _serves(binary: Path) -> bool:
    """Does this binary carry THIS tree's class repr?

    Probed rather than read off a capability table, because this row is not a
    `cap-*`: it is ~300 B in `fmt.rs` that every variant compiles, so there is
    no feature name to ask for. An installed binary from before it landed
    answers every grid row with a refusal, which would turn the file into green
    skips measuring nothing — so a binary that does not serve `print(int)` is
    not a candidate, and if none is, the whole file skips loudly.
    """
    try:
        out = subprocess.run([str(binary), "-c", "print(int)"], capture_output=True,
                             text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and out.stdout == "<class 'int'>\n"


def _candidates() -> list:
    """``(engine, binary)`` for every spectrum variant that serves this."""
    targets = {
        engines.LYPNING: paths.RUST_DIR / "target" / "release" / "lypning",
        engines.LYPNING_L: paths.RUST_DIR / "target" / "variant-l" / "release" / "lypning",
    }
    out = []
    for engine in engines.SPECTRUM:
        found = engines.find(engine)
        for cand in ([Path(found)] if found else []) + [targets[engine]]:
            if cand.is_file() and _serves(cand):
                out.append((engine, cand))
                break
    return out


BINARIES = _candidates()
IDS = [e for e, _ in BINARIES]

needs_engine = pytest.mark.skipif(
    not BINARIES,
    reason="no built variant carries this tree's class repr (lypning build --rust)",
)

on_each = pytest.mark.parametrize("engine,binary", BINARIES, ids=IDS or ["none"])

#: The reference's answer for a program, memoised: every row is asked of every
#: variant, and CPython's answer does not depend on which one asked.
_REF: dict = {}


def _run(argv: list, program: str) -> subprocess.CompletedProcess:
    """One program, in a temp cwd of its own — invariant 4. These rows do not
    write, but the barrier rows do, and one runner for both is one fewer place
    for a row to escape into the repository."""
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True,
                              text=True, cwd=d, timeout=60)


def _reference(program: str) -> subprocess.CompletedProcess:
    if program not in _REF:
        _REF[program] = _run([sys.executable], program)
    return _REF[program]


def _last_stderr_line(text: str) -> str:
    """The exception line, which is the half of a traceback both sides claim.

    CPython's body names ``File "<string>", line 1`` and, for a raise inside a
    stdlib module, that module's absolute path in the reference installation.
    Neither is a claim this engine makes or could make."""
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


# ----------------------------------------------------------------- the grid ---


@needs_engine
@on_each
@pytest.mark.parametrize("program", GRID, ids=range(len(GRID)))
def test_the_class_repr_grid_agrees_with_cpython(
    engine: str, binary: Path, program: str,
) -> None:
    got = _run([str(binary)], program)
    if got.returncode == engines.UNSUPPORTED_EXIT:
        # A refusal is always allowed and is never a bug — but it must be a
        # CLEAN one, and it must be reported, because a row that started
        # refusing is a row that stopped measuring anything.
        problem = _refusal_problem(engine, got)
        assert problem is None, "%s\n  program: %r" % (problem, program)
        pytest.skip("%s refuses this row: %s" % (engine, got.stderr.strip()[:160]))
    ref = _reference(program)
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "%s disagrees with CPython.\n"
        "  program: %r\n  %s: %r exit %d\n  cpython:   %r exit %d"
        % (engine, program, engine, got.stdout, got.returncode,
           ref.stdout, ref.returncode))
    assert _last_stderr_line(got.stderr) == _last_stderr_line(ref.stderr), (
        "%s raised a different exception line.\n  program: %r\n  %s: %r\n  cpython:   %r"
        % (engine, program, engine, _last_stderr_line(got.stderr),
           _last_stderr_line(ref.stderr)))


@needs_engine
@on_each
@pytest.mark.parametrize("program", RESULT_USES, ids=range(len(RESULT_USES)))
def test_a_class_value_is_reached_correctly_through_every_other_arm(
    engine: str, binary: Path, program: str,
) -> None:
    """The check that this round added a repr and not a wrong answer somewhere
    else. Same rule as the grid; a separate test so a failure names the arm."""
    got = _run([str(binary)], program)
    if got.returncode == engines.UNSUPPORTED_EXIT:
        assert _refusal_problem(engine, got) is None, program
        pytest.skip("%s refuses this arm: %s" % (engine, got.stderr.strip()[:160]))
    ref = _reference(program)
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "%s disagrees with CPython on a non-repr arm.\n"
        "  program: %r\n  %s: %r exit %d\n  cpython:   %r exit %d"
        % (engine, program, engine, got.stdout, got.returncode,
           ref.stdout, ref.returncode))
    assert _last_stderr_line(got.stderr) == _last_stderr_line(ref.stderr), (
        "  program: %r\n  %s: %r\n  cpython: %r"
        % (program, engine, _last_stderr_line(got.stderr),
           _last_stderr_line(ref.stderr)))


# ------------------------------------------------------------ what refuses ---


@needs_engine
@on_each
@pytest.mark.parametrize("program,cpython_says", REFUSED,
                         ids=range(len(REFUSED)))
def test_a_shape_this_engine_cannot_spell_refuses_rather_than_guesses(
    engine: str, binary: Path, program: str, cpython_says: str,
) -> None:
    """The capability, stated as its complement.

    CPython answers every one of these. An answer here would be an address or a
    path from another process printed at exit 0 — text the agent that typed the
    one-liner would never check — or a shape this engine simply has not built.
    Exit 90 with an EMPTY stdout is the contract, and `cpython_says` is in the
    table so a reader can see what was given up."""
    got = _run([str(binary)], program)
    problem = _refusal_problem(engine, got)
    assert problem is None, (
        "this program must refuse, not answer: %s\n"
        "  program: %r\n  cpython says: %s\n  stderr: %r"
        % (problem, program, cpython_says, got.stderr.strip()[:200]))


@needs_engine
@pytest.mark.parametrize("program,left,right", ALIASED, ids=range(len(ALIASED)))
def test_the_aliased_names_really_are_two_classes_so_the_engine_refuses(
    program: str, left: str, right: str,
) -> None:
    """The refusal, justified by the reference instead of by memory.

    `modules.rs` answers ONE `Value::Builtin` for each of these pairs, so a repr
    would have to pick a spelling — and this test runs CPython to show the two
    spellings differ, which is the fact that makes picking one a wrong answer.
    Asserted in this order on purpose: if a future CPython makes a pair equal,
    the first assertion fails and says the refusal could be lifted, rather than
    the second one quietly keeping a refusal nobody can justify any more."""
    probe = "print(repr(%s)); print(repr(%s))" % (left, right)
    ref = _run([sys.executable], probe)
    assert ref.returncode == 0, ref.stderr
    one, two = ref.stdout.splitlines()
    assert one != two, (
        "CPython now spells these two classes the same way (%r) — the reason "
        "this engine refuses %r has gone, and the refusal can be lifted"
        % (one, program))
    for engine, binary in BINARIES:
        got = _run([str(binary)], program)
        assert _refusal_problem(engine, got) is None, (
            "%s must refuse: one value stands for both %s and %s, which CPython "
            "spells %s and %s\n  program: %r\n  stderr: %r"
            % (engine, left, right, one, two, program, got.stderr.strip()[:200]))


@needs_engine
@on_each
def test_the_alias_that_is_really_one_class_is_served(engine: str, binary: Path) -> None:
    """`IOError` is the control, and it is why `ALIASED` is a measured rule
    rather than "refuse anything with two names". `IOError is OSError` is one
    class, so CPython's repr of it names `OSError` — and the engine, which
    answers one value for both, is right rather than lucky."""
    name, spelled = NOT_ALIASED
    ref = _run([sys.executable], "print(%s is %s, repr(%s))" % (name, spelled, name))
    assert ref.stdout == "True <class '%s'>\n" % spelled, ref.stdout
    got = _run([str(binary)], "print(%s)" % name)
    assert (got.returncode, got.stdout) == (0, "<class '%s'>\n" % spelled), got


@needs_engine
@on_each
@pytest.mark.parametrize("program,qualname", MODULE_QUALIFIED,
                         ids=range(len(MODULE_QUALIFIED)))
def test_the_repr_spelling_is_qualname_and_not_tp_name(
    engine: str, binary: Path, program: str, qualname: str,
) -> None:
    """`type.__repr__` is `{__module__}.{__qualname__}` with `builtins` elided,
    and `tp_name` is not — the two disagree for exactly `Counter`, which is the
    entry a single shared table would have printed as `<class 'Counter'>`.
    Both halves come from the reference at test time."""
    ref = _run([sys.executable], program)
    if ref.returncode != 0:
        pytest.skip("the reference cannot run this row: %s" % ref.stderr.strip()[:120])
    assert ref.stdout == "<class '%s'>\n" % qualname, (
        "the spelling in this table is not what the reference prints: %r"
        % ref.stdout)
    got = _run([str(binary)], program)
    if got.returncode == engines.UNSUPPORTED_EXIT:
        assert _refusal_problem(engine, got) is None
        pytest.skip("%s refuses this row" % engine)
    assert got.stdout == ref.stdout, (engine, program, got.stdout, ref.stdout)


@needs_engine
@on_each
def test_nothing_served_carries_an_address_or_a_path(engine: str, binary: Path) -> None:
    """The trap, as one assertion over every served row.

    An address and a path are the two things a second process cannot reproduce,
    and they are what makes five of CPython's six callable reprs unservable. So
    rather than trusting the table, every class this engine agrees to spell is
    printed and the text is searched for both."""
    for program in NAMED_CLASSES + COLLECTIONS:
        got = _run([str(binary)], program)
        if got.returncode == engines.UNSUPPORTED_EXIT:
            continue
        assert got.returncode == 0, (program, got.stderr[:200])
        assert re.search(r"0x[0-9a-fA-F]+", got.stdout) is None, (
            "a served repr carries a heap address: %r from %r"
            % (got.stdout, program))
        assert "/" not in got.stdout, (
            "a served repr carries a filesystem path: %r from %r"
            % (got.stdout, program))


@needs_engine
@on_each
@pytest.mark.parametrize("program", HASHED, ids=range(len(HASHED)))
def test_the_hash_of_a_class_is_never_answered(
    engine: str, binary: Path, program: str,
) -> None:
    """`hash(cls)` is `id(cls) // 16`: an address with the `0x` filed off, and
    the one number about a class that changes between two runs of the SAME
    interpreter. Serving a repr must not have opened a door to it."""
    got = _run([str(binary)], program)
    assert got.stdout == "", "a class hash reached stdout: %r" % got.stdout
    assert got.returncode != 0, got


@needs_engine
@on_each
@pytest.mark.parametrize("program", UNHASHABLE_TODAY,
                         ids=range(len(UNHASHABLE_TODAY)))
def test_a_class_used_as_a_key_gives_no_answer_at_all(
    engine: str, binary: Path, program: str,
) -> None:
    """See `UNHASHABLE_TODAY`: this is exit 1 where CPython exits 0, it is not
    this round's doing, and the only safe thing to assert is that no wrong
    answer reaches stdout. Pinned so that a future change to `hash.rs` has to
    come past this docstring."""
    got = _run([str(binary)], program)
    assert got.stdout == "", (
        "this is the row that must not start answering while `Path` and "
        "`ValueError` are aliases: %r" % got.stdout)
    assert got.returncode != 0, got


# ------------------------------------------------------------- the barrier ---


def _barriered(call: str) -> str:
    """``call``, with the barrier committed before it and its imports first."""
    if call.startswith(("import ", "from ")):
        head, tail = call.split("\n", 1)
        return head + "\n" + BARRIER + tail
    return BARRIER + call


def _run_snapshot(binary: Path, program: str):
    """One program, with the temp cwd listed before and after it ran."""
    with tempfile.TemporaryDirectory() as d:
        before = sorted(os.listdir(d))
        got = subprocess.run([str(binary), "-c", program], capture_output=True,
                             text=True, cwd=d, timeout=120)
        return got, before, sorted(os.listdir(d))


@needs_engine
@on_each
@pytest.mark.parametrize("kind,call", AFTER_A_BARRIER,
                         ids=range(len(AFTER_A_BARRIER)))
def test_every_refusal_a_class_repr_can_raise_lands_before_the_barrier(
    engine: str, binary: Path, kind: str, call: str,
) -> None:
    """The class, as one assertion per shape.

    These refusals are RAISED AT RUN TIME — there is no walk that can see them,
    because whether an expression is a class is a fact about a value and not
    about the source. What makes them clean anyway is `io.rs`: writes are staged
    until the run ends, so a refusal discards them. That is a claim about the
    commit barrier, not about this capability, and it is measured here rather
    than assumed — the cwd is listed before and after, so "no side effect" comes
    from the filesystem and not from reading the refusal line.

    A row that failed this would be exit 1 with `NEWD` on disk, no answer, and a
    chain that never retries — strictly worse than the refusal the binary
    without this arm already gave."""
    program = _barriered(call)
    got, before, after = _run_snapshot(binary, program)
    assert _refusal_problem(engine, got) is None, (
        "%s\n  program: %r\n  stderr: %r"
        % (_refusal_problem(engine, got), program, got.stderr.strip()[:200]))
    assert ": %s: " % kind in got.stderr, (
        "the refusal kind changed\n  program: %r\n  stderr: %r"
        % (program, got.stderr.strip()[:200]))
    assert after == before, (
        "the refusal landed AFTER os.mkdir committed the barrier: %r -> %r\n"
        "  program: %r" % (before, after, program))


@needs_engine
@on_each
def test_a_served_class_repr_does_commit_the_barrier(engine: str, binary: Path) -> None:
    """The other half, without which the test above proves nothing: the same
    barrier in a program that is SERVED really does reach the disk. A staging
    layer that dropped every write would pass `AFTER_A_BARRIER` for the wrong
    reason."""
    got, before, after = _run_snapshot(binary, _barriered("print(type({}))"))
    assert (got.returncode, got.stdout) == (0, "<class 'dict'>\n"), got
    assert after == sorted(before + ["NEWD"]), (before, after)


# ----------------------------------------------------------- the two arms ---


@needs_engine
def test_both_variants_serve_it_because_it_is_not_a_capability() -> None:
    """This row is ~300 B in `fmt.rs`, not a `cap-*` feature, and the difference
    is worth an assertion.

    A `cap-*` would need a `route::CAPS` row naming the runtime KINDS it
    answers, and the only kind available is `repr` — which also covers
    `repr() of a module`, `repr() of a generator` and the four other shapes
    `REFUSED` pins, none of which any variant answers. That row would route
    `print(json)` from the core to a sibling that refuses it identically: one
    wasted spawn per program, bought with bytes. Unconditional costs neither, so
    both arms must serve it and the core must ROUTE the corpus shape to itself
    rather than to a sibling or to CPython."""
    assert len(BINARIES) >= 1
    for engine, binary in BINARIES:
        got = _run([str(binary)], "print(type({}), type([]), type('a'))")
        assert got.stdout == "<class 'dict'> <class 'list'> <class 'str'>\n", (
            engine, got.stdout, got.stderr[:200])

    core = dict(BINARIES).get(engines.LYPNING)
    if core is None:
        pytest.skip("the core is not built")
    program = "import json\nd = json.loads('{\"a\": 1}')\nprint(type(d), len(d))"
    route = subprocess.run([str(core), "route", "-c", program],
                           capture_output=True, text=True, timeout=60)
    assert route.stdout.split("\t")[0].strip() == engines.LYPNING, route.stdout


@needs_engine
def test_every_variant_answers_the_grid_identically() -> None:
    """The spectrum's monotonicity, on the rows this round touched.

    A larger variant must never do WORSE than a smaller one, and for a row that
    is in neither's capability set the two must agree exactly — including on
    which refusal they give, because the core's refusal is what the router uses
    to pick the next rung."""
    if len(BINARIES) < 2:
        pytest.skip("only one variant is built")
    (_, first), (_, second) = BINARIES[0], BINARIES[1]
    for program in NAMED_CLASSES + THROUGH_TYPE + COERCIONS:
        a, b = _run([str(first)], program), _run([str(second)], program)
        assert (a.stdout, a.returncode) == (b.stdout, b.returncode), (
            "the two variants disagree on %r: %r/%d vs %r/%d"
            % (program, a.stdout, a.returncode, b.stdout, b.returncode))


@needs_engine
@on_each
@pytest.mark.parametrize("program,cpython_says", NOT_A_DICT,
                         ids=range(len(NOT_A_DICT)))
def test_os_environ_is_not_a_dict_and_is_never_spelled_as_one(
    engine: str, binary: Path, program: str, cpython_says: str,
) -> None:
    """`os.environ` is `ALIASED`'s rule met from the other side.

    There the engine holds one value for two CPython classes; here it holds a
    `Value::Dict` for a CPython class that is not `dict` at all. Both make a
    repr a guess, and a guess printed at exit 0 is the failure invariant 1
    exists to prevent — so both refuse. The reference is run rather than
    recalled, because the whole claim is that CPython says something else."""
    ref = _run([sys.executable], program)
    assert ref.returncode == 0, (
        "the reference cannot run this row, so it proves nothing: %r"
        % ref.stderr.strip()[:200])
    assert "dict" not in ref.stdout.split("(")[0], (
        "this row is in the wrong table: CPython answers %r, which a dict "
        "answer would equal" % ref.stdout)
    got = _run([str(binary)], program)
    problem = _refusal_problem(engine, got)
    assert problem is None, (
        "this program must refuse, not answer: %s\n"
        "  program: %r\n  cpython says: %s\n  got: %r"
        % (problem, program, cpython_says, got.stdout[:200]))


@needs_engine
@on_each
@pytest.mark.parametrize("program,want", STILL_A_DICT, ids=range(len(STILL_A_DICT)))
def test_naming_os_environ_did_not_cost_a_plain_dict_its_own_name(
    engine: str, binary: Path, program: str, want: str,
) -> None:
    """The over-correction check, and the reason `NOT_A_DICT` is a tag on one
    mapping rather than a refusal on `type()` of any dict.

    Fourteen corpus programs print `type(d)` for a `d` that came out of
    `json.loads`, and every one of them must still be answered. `dict(os.environ)`
    and `os.environ.copy()` are here too: CPython returns a plain dict for both,
    so the tag must not travel with the data."""
    ref = _run([sys.executable], program)
    assert ref.returncode == 0 and ref.stdout == want, (
        "this table's expectation is not what the reference prints: %r vs %r"
        % (ref.stdout, want))
    got = _run([str(binary)], program)
    if got.returncode == engines.UNSUPPORTED_EXIT:
        assert _refusal_problem(engine, got) is None, program
        pytest.skip("%s refuses this row: %s" % (engine, got.stderr.strip()[:160]))
    assert (got.stdout, got.returncode) == (ref.stdout, 0), (
        "%s no longer answers a plain dict.\n  program: %r\n  %s: %r exit %d"
        % (engine, program, engine, got.stdout, got.returncode))


@needs_engine
@on_each
@pytest.mark.parametrize("program", WRITES_DO_NOT_STICK_TODAY,
                         ids=range(len(WRITES_DO_NOT_STICK_TODAY)))
def test_a_write_to_os_environ_is_still_not_visible_through_the_next_read(
    engine: str, binary: Path, program: str,
) -> None:
    """The gap above, pinned as a disagreement rather than as an answer.

    Asserting the engine's current output would be asserting that a wrong answer
    is right; what is asserted instead is that it is still WRONG, and in the
    direction measured. A failure here means someone materialised `os.environ`
    once — which is the fix — and the row belongs in `STILL_A_DICT` now."""
    ref = _run([sys.executable], program)
    assert ref.returncode == 0 and ref.stdout in ("v\n", "True\n"), (
        "the reference no longer runs this row, so it pins nothing: %r %r"
        % (ref.stdout, ref.stderr.strip()[:160]))
    got = _run([str(binary)], program)
    if got.returncode == engines.UNSUPPORTED_EXIT:
        assert _refusal_problem(engine, got) is None, program
        pytest.skip("%s refuses this row, which closes the gap safely" % engine)
    assert got.stdout != ref.stdout, (
        "%s now agrees with CPython on a write to os.environ — the gap this "
        "row pins is CLOSED. Move it into STILL_A_DICT.\n  program: %r"
        % (engine, program))
