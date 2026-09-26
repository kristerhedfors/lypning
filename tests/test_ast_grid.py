"""`ast`, as a grid: every program on the binary and on CPython.

`tests/test_binascii_grid.py` is the shape this follows. Every row runs from a
FRESH temp cwd (invariant 4) and must end one of exactly two ways:

  * byte-identical stdout AND the same exit code as CPython, or
  * a clean refusal — exit 90, nothing on stdout, one
    ``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

`cap-ast` serves `import ast` and ONE name, `ast.literal_eval` over a `str`
(`src/pyast.rs`), by a token-level recursive descent of its own behind a screen
that refuses every text `lex.rs` and CPython's tokenizer could read differently.
**Every `ValueError`, `SyntaxError` and `TypeError` it would raise is a
refusal**, `try`/`except` included: the messages are the repr of an AST node
(changed in 3.14) or the tokenizer's.

`ast.parse` is NOT served in any form: `parse.rs` accepts programs CPython's
`ast.parse` rejects, so it — and `walk`, `dump`, `unparse`, the node classes —
is a `module-attr` block in the CORE's walk, routed straight to CPython.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

A = "import ast\n"


def le(s: str) -> str:
    """A program printing ``ast.literal_eval(s)``."""
    return A + "print(ast.literal_eval(%r))" % s


#: CPython answers every one of these, and lypning-l must answer it too.
SERVED = [
    A + "print('only imported')",
    "import ast as A\nprint(1)",
    le("[1, 'a', (2.5, None), {'k': True}, b'x', ()]"),
    A + "print(ast.literal_eval('{1: 2, 1: 3}'), ast.literal_eval(\"'a' 'b'\"), "
        "ast.literal_eval('-0.0'), ast.literal_eval('1e400'), ast.literal_eval('0x10'), "
        "ast.literal_eval('1_0'))",
    A + "print(ast.literal_eval('(1)'), ast.literal_eval('(1,)'), ast.literal_eval('- 1'), "
        "ast.literal_eval('+2.5'))",
    A + "set = list\nprint(ast.literal_eval('set()'), ast.literal_eval('set ( )'))",
    le("{1, True}"),
    le("{1: 'a', True: 'b', 1.0: 'c'}"),
    le("[[], {}, (), set()]"),
    le("  # a comment\n[\n  1,  # one\n  2,\n]\n\n"),
    le("1, 2"),
    le("1,"),
    le("()"),
    le("(((1),),)"),
    le("{'a': [1, 2], 'b': None}"),
    le('{"a": [1, 2], "b": None, "c": {"d": (True, False)}}'),
    le("0x_1f"), le("0o17"), le("0b1_01"), le("1_000.5"), le(".5"), le("1."), le("1E+3"), le("1.e-3"),
    le("00"), le("0_0"), le("09.5"), le("1e-400"), le("-1e400"),
    le("123456789012345678901234567890"),
    le("-9223372036854775808"),
    le("-0x8000000000000000"),
    le("'\\x41\\101\\n\\t\\u00e9\\U0001F600'"),
    le("'\\0' '\\7' '\\377'"),
    le("b'\\x00\\xff' b'ab'"),
    le("r'\\d+' R'\\n'"),
    le("rb'\\x' Br'\\y'"),
    le("u'x' U'y'"),
    le("'''a\nb'''"),
    le("'a\\\nb'"),
    le('"it\'s"'),
    le("[1,\n 2]"),
    le("{\n'k'\n:\n1\n}"),
    le("(1, 'two', [3.0, {'four': 4}], None, True)"),
    le("{(1, 2): 'pair', 'x': -1, 2.5: b'q'}"),
    # repr-shaped strings the way real programs make them
    A + "d = {'name': 'x', 'n': [1, 2.5, -3], 'ok': True, 'none': None, 't': (1,)}\n"
        "print(ast.literal_eval(repr(d)) == d, ast.literal_eval(repr(d)))",
    A + "rows = [('a', 1), ('b', -2)]\nprint(ast.literal_eval(str(rows)))",
    A + "print(ast.literal_eval(repr('quote\\'s \"and\" \\\\ back \\n')))",
    A + "print(ast.literal_eval(repr(b'\\x00\\x7f\\x80\\xff')))",
    A + "print(list(map(ast.literal_eval, ['1', '[2]', \"'x'\", 'None'])))",
    A + "d = ast.literal_eval(\"{'b': 1, 'a': 2}\")\nfor k, v in d.items():\n    print(k, v)",
    A + "x = ast.literal_eval('[1]')\nx.append(2)\nprint(x, type(x).__name__)",
    A + "try:\n    v = ast.literal_eval('[1]')\nexcept ValueError:\n    v = None\nprint(v)",
    A + "print(len(ast.literal_eval('{1, 2, 3, 2}')), 2 in ast.literal_eval('{1, 2}'))",
    A + "print(sorted(ast.literal_eval(\"{'b', 'a'}\")))",
    "from ast import literal_eval\nprint(literal_eval('[1, -2.5]'))",
    "from ast import literal_eval as L\nprint(L('(None,)'))",
    "import ast as A\nprint(A.literal_eval('{\"a\": [1, 2]}'))",
    A + "f = ast.literal_eval\nprint(f('3'))",
]

#: Served on a 3.10+ reference, where `literal_eval` strips leading spaces and
#: tabs; an IndentationError on 3.9. The binary may refuse either way.
SERVED_OR_REFUSED = [
    le(" 1"),
    le("\t[1]"),
]

#: CPython raises, warns, or answers something this engine does not model.
#: Exit 90 and an EMPTY stdout, both.
REFUSED = [
    le("{'a', 'b'}"),                        # set-order
    le("--1"), le("-True"), le("+None"), le("-(1)"), le("+-1"),
    le("1+2j"), le("1j"), le("..."), le("b'a' 'b'"), le("'a' b'b'"),
    le("1;2"), le("[1]*2"), le("1 2"), le(""), le("x"), le("(set)()"), le("set((1,))"),
    le("{**{}}"), le("[1][0]"), le("1\n2"), le("f'{1}'"), le("ur'a'"),
    le("'\\N{EM DASH}'"),                     # named escape
    le("'\\x+1'"), le("'\\u+041'"), le("'\\x4'"), le("'\\u00'"),   # bad hex escapes
    le("'\\d'"), le("b'\\u0041'"), le("'\\8'"),                    # warning escapes
    le("'\\777'"), le("b'\\400'"),                                  # octal past \377
    le("1\x00"), le("1 # \x00"), le("'a\x00'"),                    # NUL
    le("1\\\n"), le("1\n\\\n"), le("[1,\\\n2]"),                    # backslash continuation
    le("1\n "), le("1\n\t"), le("1\r"), le("1\r\n"), le("\x0c1"),
    le("1if 1 else 2"), le("0x1for"), le("1or 2"), le("1_"), le("1__0"), le("0777"),
    le("1e"), le("0x"), le("0b2"), le("1..real"), le("1.real"),
    le("# -*- coding: latin-1 -*-\n1"),
    le("'\\ud800'"),
    le("[" * 60 + "]" * 60),
    A + "print(ast.literal_eval(b'1'))",
    A + "print(ast.literal_eval(1))",
    A + "print(ast.literal_eval(node_or_string='1'))",
    A + "print(ast.literal_eval('1', 2))",
    A + "print(ast.literal_eval())",
    A + "try:\n    ast.literal_eval('x')\nexcept ValueError as e:\n    print(e)",
    A + "try:\n    ast.literal_eval('{[1]: 2}')\nexcept TypeError as e:\n    print(e)",
    A + "try:\n    ast.literal_eval('{(1, [2])}')\nexcept TypeError as e:\n    print(e)",
    A + "try:\n    ast.literal_eval('(')\nexcept SyntaxError as e:\n    print(e.msg)",
    A + "print(ast)",
    A + "print(ast.literal_eval)",
]

#: Every name but `literal_eval`, `ast.parse` above all: routed by the CORE's
#: walk straight to CPython, and refused by lypning-l where it is evaluated.
UNSERVED_NAMES = [
    A + "ast.parse('x = 1\\n')\nprint('ok')",
    A + "for s in ['a = 1', 'def f(n):\\n    return n + 1\\n']:\n"
        "    ast.parse(s, filename='f.py')\n    print('ok')",
    A + "[ast.parse(s, 'f.py') for s in ('a=1', 'b=2')]\nprint('parse ok')",
    A + "ast.parse('x=1', feature_version=(3, 9))\nprint(1)",
    A + "ast.parse('return 1')\nprint('ok')",
    A + "ast.parse('if 1: pass print(2)')\nprint('ok')",
    A + "ast.parse('x = 1\\\\\\n')\nprint('ok')",
    A + "ast.parse('x = 1\\x00')\nprint('ok')",
    A + "ast.parse(\"f'{x #}'\")\nprint('ok')",
    A + "ast.parse(\"f'{x!rr}'\")\nprint('ok')",
    # the feature_version guard: no rung may start answering these
    A + "[ast.parse(s) for s in ('match x:\\n case 1: pass', 'x=(y:=1)', "
        "'try:\\n pass\\nexcept A, B:\\n pass')]\nprint(1)",
    A + "try:\n    ast.parse('x = (')\nexcept SyntaxError as e:\n    print(e.msg, e.lineno, e.offset)",
    A + "print(ast.parse('x=1'))",
    A + "t = ast.parse('x=1')\nprint(1)",
    A + "print(ast.dump(ast.parse('x')))",
    A + "for n in ast.walk(ast.parse('x')):\n    print(type(n).__name__)",
    A + "print(ast.unparse(ast.parse('x')))",
    A + "print(ast.walk)",
    A + "print(isinstance(1, ast.AST))",
    A + "class V(ast.NodeVisitor):\n    pass",
    "import ast as a\na.parse('1')\nprint(1)",
    "from ast import parse\nparse('x')\nprint(1)",
    "from ast import literal_eval, dump\nprint(literal_eval('1'))",
    A + "try:\n    1/0\nexcept ast.AST:\n    pass",
]

#: Each must be a clean 90 with the cwd untouched: every refusal lands at
#: runtime, in a run `import ast` holds reversible (`io::hold`).
BARRIER = "import os\nos.mkdir('NEWD')\n"
AFTER_A_BARRIER = [
    ("module-attr", "ast.parse('x')"),
    ("module-attr", "print(ast.dump)"),
    ("ast", "print(ast.literal_eval('--1'))"),
    ("ast", "s = '-' + '-1'\nprint(ast.literal_eval(s))"),
    ("ast", "print(ast.literal_eval(\"'\\\\x+1'\"))"),
]

#: Routed by the CORE: into lypning-l on the import.
ROUTED_TO_LYPNING_L = [
    A + "print(ast.literal_eval('[1]'))",
    "from ast import literal_eval\nprint(literal_eval('1'))",
    "import ast as A\nprint(A.literal_eval('1'))",
    A + "print(1)",
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


BINARY = _current(engines.LYPNING_L, "cap-ast")
CORE = _current(engines.LYPNING, "cap-ast")

needs_l = pytest.mark.skipif(
    BINARY is None,
    reason="no lypning-l carrying cap-ast is built (lypning build --rust)",
)
needs_core = pytest.mark.skipif(
    CORE is None or BINARY is None,
    reason="routing needs the CORE that routes and the lypning-l it routes to",
)


def _run(argv: list[str], program: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True, text=True,
                              cwd=d, timeout=60, input=stdin)


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


def _agree(program: str, stdin: str | None = None) -> None:
    got = _run([str(BINARY)], program, stdin)
    ref = _run([sys.executable], program, stdin)
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "lypning-l disagrees with CPython.\n  program:  %r\n"
        "  lypning-l: %r exit %d %s\n  cpython:   %r exit %d %s"
        % (program, got.stdout, got.returncode, got.stderr.strip()[-200:],
           ref.stdout, ref.returncode, ref.stderr.strip()[-200:]))


@needs_l
@pytest.mark.parametrize("program", SERVED, ids=range(len(SERVED)))
def test_the_served_rows_answer_exactly_what_cpython_answers(program: str) -> None:
    """Served means ANSWERED: a refusal here is a row that stopped measuring."""
    _agree(program)


@needs_l
def test_a_dict_read_from_stdin() -> None:
    program = ("import sys, ast\nd = ast.literal_eval(sys.stdin.read().strip())\n"
               "for k, v in d.items():\n    print(k, v)")
    _agree(program, '{"a": [1, 2], "b": None}\n')


@needs_l
@pytest.mark.parametrize("program", SERVED_OR_REFUSED, ids=range(len(SERVED_OR_REFUSED)))
def test_leading_whitespace_is_served_or_refused_never_guessed(program: str) -> None:
    got = _run([str(BINARY)], program)
    if _refusal_problem(got) is None:
        return
    ref = _run([sys.executable], program)
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (program, got.stderr)


@needs_l
@pytest.mark.parametrize("program", REFUSED + UNSERVED_NAMES,
                         ids=range(len(REFUSED) + len(UNSERVED_NAMES)))
def test_the_surface_outside_the_slice_refuses_rather_than_guesses(program: str) -> None:
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got)
    assert problem is None, "this program must refuse, not answer: %s\n  program: %r" % (
        problem, program)


@needs_l
@pytest.mark.parametrize("program", UNSERVED_NAMES, ids=range(len(UNSERVED_NAMES)))
def test_an_unserved_name_refuses_even_after_output(program: str) -> None:
    """Not a pre-run stop (an `ast.parse` that never runs is the core's answer):
    the refusal lands where the name is evaluated, and the output staged before
    it is discarded, because `import ast` held the run reversible."""
    got = _run([str(BINARY)], "print('first')\n" + program)
    assert _refusal_problem(got) is None, (program, got.returncode, got.stdout, got.stderr)
    # The attribute, an `except` naming one, or a parse-time refusal (`class`).
    assert any(": unsupported: %s" % k in got.stderr
               for k in ("module-attr: ast.", "exception: ", "class: ")), got.stderr


@needs_l
@pytest.mark.parametrize("kind,call", AFTER_A_BARRIER, ids=range(len(AFTER_A_BARRIER)))
def test_every_refusal_leaves_the_disk_as_it_was(kind: str, call: str) -> None:
    program = A + BARRIER + call
    with tempfile.TemporaryDirectory() as d:
        before = sorted(os.listdir(d))
        got = subprocess.run([str(BINARY), "-c", program], capture_output=True,
                             text=True, cwd=d, timeout=60)
        after = sorted(os.listdir(d))
    problem = _refusal_problem(got)
    assert problem is None, "%s\n  program: %r" % (problem, program)
    assert ": %s: " % kind in got.stderr, got.stderr
    assert before == after, "the refusal landed AFTER the barrier: %r -> %r" % (before, after)


@needs_core
@pytest.mark.parametrize("program", ROUTED_TO_LYPNING_L, ids=range(len(ROUTED_TO_LYPNING_L)))
def test_the_core_routes_a_served_call_into_lypning_l(program: str) -> None:
    route = engines.route(program, binary=CORE)
    assert route.engine == engines.LYPNING_L, (route.engine, route.kind, route.detail)


@needs_core
@pytest.mark.parametrize("program", UNSERVED_NAMES, ids=range(len(UNSERVED_NAMES)))
def test_the_core_routes_what_no_rung_serves_to_cpython(program: str) -> None:
    route = engines.route(program, binary=CORE)
    assert route.engine == engines.CPYTHON, (route.engine, route.kind, route.detail)
    assert _run([str(BINARY)], program).returncode == engines.UNSUPPORTED_EXIT


@needs_core
def test_the_core_itself_refuses_the_import() -> None:
    got = _run([str(CORE)], A + "print(ast.literal_eval('1'))")
    assert got.returncode == engines.UNSUPPORTED_EXIT and got.stdout == "", got
    assert "unsupported: module: import ast" in got.stderr, got.stderr


def test_the_capability_is_lypning_l_s_only() -> None:
    assert "cap-ast" in engines.VARIANT_CAPS[engines.LYPNING_L]
    assert "cap-ast" not in engines.VARIANT_CAPS[engines.LYPNING]
