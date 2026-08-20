"""The syntax-gap scanner — the one part of lypning-mp that guesses.

The invariant this file exists to hold: **a program CPython runs leaves
lypning-mp by the exit-90 contract naming the construct, and a program CPython
rejects keeps its own exit 1.**

MicroPython's parser refuses several constructs CPython accepts — ``match`` /
``case``, ``except*``, ``{**d}`` in a display, a positional-only ``/``,
parenthesized with-items. Every one of them arrives as the same
``SyntaxError: invalid syntax`` a typo does, so ``lypning_missing_syntax()`` in
``assets/micropython/variant/lypning_compat.h`` runs only AFTER a parse has
already failed and asks a different question: does the source CONTAIN a form
this parser is known not to read? Nothing is scanned on the happy path, so a
program that compiles cannot reach a false positive at all.

The two ways of being wrong cost different things, and the asymmetry is why the
battery below is larger on the negative side:

* a **miss** — a construct that should refuse reports a plain syntax error —
  tells an agent its own correct program is broken, and it edits it. That is
  the MISMATCH the scanner exists to prevent, and it is unrecoverable because
  nothing downstream ever learns the program was fine.
* a **false positive** — a genuine syntax error mis-scanned into a refusal —
  sends a broken program on to CPython, which rejects it too. One wasted spawn,
  and the accurate SyntaxError arrives one tier later. Bounded, self-correcting,
  and still worth not doing.

Two levels, because neither covers the other:

* **as C, on the host.** The scanner is pure C over a ``char *`` with no
  MicroPython types in it, so the header's pure-C half is sliced out at the two
  markers below and compiled with ``cc -Wall -Werror``. That needs no i386
  toolchain, no network and no built tier, so the fine-grained battery runs
  wherever a compiler does — and ``-Werror`` is as much the point as the cases
  are: this header compiles into a build that takes minutes, so a warning caught
  here is a build cycle not spent.
* **through the binary, on all three source routes.** ``-c`` hands the scanner a
  string, a script file hands it a path re-read on the error path, and ``-``
  hands it a buffered read; ``-c`` and a file leave through
  ``shared/runtime/pyexec.c`` while stdin leaves through ``ports/unix/main.c``,
  so one route working proves nothing about the others.
  ``assets/scripts/build-micropython.sh`` pins the same three at build time and
  names this suite for the rest of the story.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Tuple

import pytest

from lypning import engines, paths

HEADER = paths.ASSETS / "micropython" / "variant" / "lypning_compat.h"

#: The descriptions the scanner returns, spelled out rather than imported —
#: they are the ``detail`` half of a contract line an agent reads, so a test
#: that computed them from the header could not notice one changing.
MATCH_STMT = "match statement"
CASE_CLAUSE = "match statement (case clause)"
EXCEPT_STAR = "except* (exception groups)"
POSONLY = "positional-only parameter (def f(a, /, b))"
DICT_UNPACK = "dict unpacking in a literal ({**d})"
WITH_PARENS = "parenthesized with-items"
NONE = "(none)"

#: The two anchors the pure-C slice is cut on. Anchored on text rather than on
#: line numbers because the header grows, and a test that silently compiled half
#: a scanner would pass while testing nothing.
_SLICE_START = "#define LYPNING_SYNTAX_DEPTH"
_SLICE_END = "// Set by ports/unix/main.c"


# --- the scanner, compiled on the host ---------------------------------------


def _extract_scanner(header_text: str) -> str:
    """The pure-C half of the header: the scanner and nothing that needs a VM."""
    start = header_text.find(_SLICE_START)
    end = header_text.find(_SLICE_END, start + 1)
    if start < 0:
        pytest.fail("%s is missing from %s" % (_SLICE_START, HEADER))
    if end <= start:
        pytest.fail("the runtime-hook marker moved; re-anchor this extraction")
    body = header_text[start:end]
    # Drop the doc comment that introduces the runtime hooks — it belongs to the
    # half that needs py/obj.h.
    tail = body.rfind("/*")
    if tail > 0:
        body = body[:tail]
    if "lypning_missing_syntax" not in body:
        pytest.fail("the slice no longer contains the scanner; re-anchor it")
    return body


@pytest.fixture(scope="module")
def scan():
    """``scan(src)`` → the description, or ``"(none)"``.

    Module-scoped: one compile serves the whole battery, and it deliberately
    does not take ``tmp_path`` — the autouse isolation fixture in ``conftest``
    is per-test, and a C compile cares about none of what it moves.
    """
    cc = shutil.which("cc") or shutil.which("gcc")
    if cc is None:
        pytest.skip("no C compiler — the scanner cannot be built on the host")
    work = Path(tempfile.mkdtemp(prefix="lypning-scan-"))
    src = work / "scan.c"
    binary = work / "scan"
    src.write_text("\n".join([
        "#include <stdio.h>",
        "#include <string.h>",
        "#include <ctype.h>",
        "#include <stdbool.h>",
        _extract_scanner(HEADER.read_text(encoding="utf-8")),
        "int main(int argc, char **argv) {",
        "    (void)argc;",
        "    const char *r = lypning_missing_syntax(argv[1]);",
        '    printf("%s\\n", r ? r : "(none)");',
        "    return 0;",
        "}",
    ]), encoding="utf-8")
    # Bounded like every other spawn in this file: a `cc` that is a wrapper
    # waiting on a lock (ccache, distcc) would otherwise hang the whole suite
    # with no output at all, from a module-scoped fixture every test waits on.
    built = subprocess.run([cc, "-Wall", "-Werror", "-O1", "-o", str(binary), str(src)],
                           capture_output=True, text=True, timeout=120, check=False)
    if built.returncode != 0:
        pytest.fail("the scanner does not compile cleanly:\n%s" % built.stderr)

    def _scan(source: str) -> str:
        done = subprocess.run([str(binary), source], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=30, check=False)
        assert done.returncode == 0, "the scanner crashed on:\n%s" % source
        return done.stdout.strip()

    try:
        yield _scan
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --- the positive side: constructs CPython runs and this parser cannot read ---
#
# Every entry was run under CPython 3.11 and under the lypning-mp binary in this
# tree: CPython executes it, the parser rejects it. That is what makes it a
# divergence rather than a language-reference exercise, and
# `test_the_positives_are_things_cpython_accepts` re-establishes half of it on
# every run rather than trusting this paragraph.

#: ``(id, source, expected description)``.
_DETECTED: List[Tuple[str, str, str]] = [
    ("match-stmt", 'x = 1\nmatch x:\n    case 1: print("one")', MATCH_STMT),
    ("match-tuple", "match (a, b):\n    case _: pass", MATCH_STMT),
    ("match-call", "match command.split():\n    case [x]: pass", MATCH_STMT),
    # A fragment, and the reason the case clause has a rule of its own: when the
    # `match` header is split across lines it does not end in a colon, and the
    # `case` is then the only line that still names the construct.
    ("case-clause", "    case 1:\n        pass", CASE_CLAUSE),
    ("except-star", "try:\n    pass\nexcept* ValueError:\n    pass", EXCEPT_STAR),
    ("except-star-tight", "try:\n    pass\nexcept*ValueError:\n    pass", EXCEPT_STAR),
    ("posonly", "def f(a, /, b): return a + b", POSONLY),
    ("posonly-trailing", "def f(a, /): return a", POSONLY),
    ("posonly-unspaced", "def f(a,/,b): return a", POSONLY),
    ("dict-unpack-first", 'print({**a, "b": 1})', DICT_UNPACK),
    ("dict-unpack-last", 'print({"a": 1, **d})', DICT_UNPACK),
    ("dict-unpack-merge", "merged = {**defaults, **overrides}", DICT_UNPACK),
    ("with-parens-single", 'with (open("a") as f,): pass', WITH_PARENS),
    ("with-parens-pair", 'with (open("a") as f, open("b") as g):\n    pass', WITH_PARENS),
    # The form the syntax exists FOR — the `as` is on a later line than the `(`,
    # which a line-bounded scan misses entirely. It did, and only the toy
    # single-line case was ever caught.
    ("with-parens-wrapped",
     'with (\n    open("a") as f,\n    open("b") as g,\n):\n    pass', WITH_PARENS),
    ("with-parens-wrapped-one", 'with (\n    open("a") as f,\n):\n    pass', WITH_PARENS),
]

#: Entries that are source the scanner can be HANDED but not a program CPython
#: will compile on its own — an indented clause with no statement above it. The
#: scanner is fed whatever the failing process was given, which for a file is a
#: whole module and for `-c` may be any fragment an agent typed, so a fragment
#: is a legitimate input; it just cannot take part in the divergence check.
_FRAGMENTS = {"case-clause"}


@pytest.mark.parametrize("source,want", [(s, w) for _, s, w in _DETECTED],
                         ids=[i for i, _, _ in _DETECTED])
def test_detects_what_micropython_cannot_parse(scan, source, want):
    assert scan(source) == want, "scanning:\n%s" % source


def test_the_positives_are_things_cpython_accepts():
    """The table is a divergence table, checked against a live CPython.

    A construct CPython rejects too is not a divergence — it is a syntax error,
    and refusing it would turn a plain exit 1 into a 90 for no gain. This is why
    ``type X = int`` and ``def f[T]()`` are deliberately absent from the header:
    the reference interpreter does not take them either.
    """
    cpython = engines.find_cpython()
    if cpython is None:
        pytest.skip("no reference CPython")
    probe = subprocess.run([str(cpython), "-c", "import sys; print(sys.version_info[1])"],
                           capture_output=True, text=True, timeout=30, check=False)
    if probe.returncode != 0 or int(probe.stdout.strip()) < 11:
        # `match` is 3.10 and `except*` is 3.11. On an older reference these are
        # not divergences yet, and asserting they compile would fail for the one
        # reason that is not a defect.
        pytest.skip("the reference CPython predates match/except* (needs 3.11+)")
    sources = [s for i, s, _ in _DETECTED if i not in _FRAGMENTS]
    driver = (
        "import sys, json\n"
        "out = []\n"
        "for s in json.load(sys.stdin):\n"
        "    try:\n"
        "        compile(s, '<probe>', 'exec')\n"
        "        out.append('')\n"
        "    except SyntaxError as e:\n"
        "        out.append(str(e))\n"
        "json.dump(out, sys.stdout)\n"
    )
    done = subprocess.run([str(cpython), "-c", driver], input=json.dumps(sources),
                          capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    for source, err in zip(sources, json.loads(done.stdout)):
        assert err == "", "CPython rejects this too, so it is not a divergence:\n%s\n%s" % (
            source, err)


# --- the negative side, which is the one that matters ------------------------

_CLEAN = [
    # ordinary programs, several of which the tier runs today
    'print("fine")',
    "print(6 / 2)",
    "print(sum([1, 2]) / 2)",
    "def f(a, b): return a / b",
    "def ratio(num, den):\n    return num/den",
    "def f(**k): return k\nprint(f(**{'a': 1}))",
    "print(max(*[1, 2]))",
    "print(2 ** 3)",
    "print({1, 2})",
    "print({'a': 1})",
    "print(1 == 1, 1 != 2, 1 <= 2, 1 >= 0)",
    "try:\n    pass\nexcept ValueError:\n    pass",
    "class A(B): pass",
    "class A:\n    x = 1",
    'with open("a") as f: pass',
    'with open("a") as f, open("b") as g: pass',
    # `with (expr) as f:` is ordinary and parses fine — the missing form is an
    # `as` INSIDE the parentheses, not the parentheses themselves.
    'with (lambda: 1)() as f: pass',
    'with (open("a")) as f: pass',
    'with (\n    open("a")\n) as f: pass',
    'x = (\n    1,\n)\nwith open("a") as f: pass',
    "",
]


@pytest.mark.parametrize("source", _CLEAN)
def test_does_not_fire_on_programs_this_tier_can_run(scan, source):
    assert scan(source) == NONE, "false positive on:\n%s" % source


#: ``match`` and ``case`` are SOFT keywords: CPython still lets them be ordinary
#: names, and they are common ones — ``m = re.match(...)`` is in this repo's own
#: corpus. Flagging those would blame the match statement for every program that
#: failed to parse for some other reason and happened to call ``re.match``.
_SOFT_KEYWORDS = [
    "match = 1\nprint(match)",
    "m = re.match(p, s)\nprint(m)",
    'd = {"match": 1}\nprint(d["match"])',
    "case = 2\nprint(case)",
    "for case in cases:\n    print(case)",
    "matches = []\nprint(matches)",
    "print(matching)",
    "def match(a, b):\n    return a == b",
]


@pytest.mark.parametrize("source", _SOFT_KEYWORDS)
def test_match_and_case_stay_soft_keywords(scan, source):
    assert scan(source) == NONE, "false positive on a soft keyword:\n%s" % source


#: Everything the scanner looks for can appear inside a string or a comment, and
#: the corpus is full of programs that manipulate Python source as data.
_LITERALS = [
    'print("match x:")',
    "# match x:\nprint(1)",
    'def f():\n    """match x:\n    case 1: pass"""\n    return 1',
    "src = '''\nmatch x:\n    case 1: pass\n'''\nprint(len(src))",
    'import re\nprint(re.findall(r"a{**}", "a"))',
    'print("def f(a, /, b)")',
    "print('except* Error')",
    'print("{**d}")',
    'print("with (a as b,):")',
    'esc = "he said \\"match x:\\""\nprint(esc)',
]


@pytest.mark.parametrize("source", _LITERALS)
def test_skips_comments_and_string_literals(scan, source):
    assert scan(source) == NONE, "false positive inside a literal:\n%s" % source


@pytest.mark.parametrize("source,want", [
    # `f(**kw)` is supported and extremely common; `{**d}` is not. The two differ
    # only by which bracket is open, which is the whole reason the scanner keeps
    # a bracket stack instead of matching on `**`.
    ("def f(**kw): pass\nf(**{'a': 1})", NONE),
    ("print(dict(**a))", NONE),
    ("print([*a, *b])", NONE),
    ("print({'k': f(**a)})", NONE),
    ("print({**a})", DICT_UNPACK),
    ("print(f({**a}))", DICT_UNPACK),
])
def test_tells_dict_unpacking_from_a_keyword_splat(scan, source, want):
    assert scan(source) == want, "scanning:\n%s" % source


@pytest.mark.parametrize("source,want", [
    # A division inside a call is indistinguishable from a positional-only
    # marker unless the separators either side are checked — and division in a
    # call is far more common than a `/` in a signature.
    ("print(total / count)", NONE),
    ("f(a / b, c)", NONE),
    ("f(a, b / c)", NONE),
    ("print(len(x) / 2, y / 3)", NONE),
    ("def f(a, /, b): pass", POSONLY),
])
def test_tells_a_positional_only_marker_from_division(scan, source, want):
    assert scan(source) == want, "scanning:\n%s" % source


@pytest.mark.parametrize("source,want", [
    # Deep nesting must not run the fixed 24-slot bracket stack off its end. The
    # scanner runs while the process is ALREADY failing, so a crash here trades a
    # wrong exit code for no output at all — strictly worse than the bug it is
    # fixing. `scan` asserts the exit status, so a segfault fails these too.
    ("(" * 200 + ")" * 200, NONE),
    ("{" * 200, NONE),
    (")" * 200, NONE),
    ('"' + "unterminated", NONE),
    ("'''never closed", NONE),
    ("#" + "x" * 5000, NONE),
    ("\n" * 1000 + "match x:\n    case 1: pass", MATCH_STMT),
    # a brace nest deeper than the stack must not report a phantom {**d}
    ("f(" + "[" * 60 + "**", NONE),
])
def test_survives_pathological_input(scan, source, want):
    assert scan(source) == want


# --- known misses ------------------------------------------------------------
#
# Found by this suite, reproduced against the binary in this tree, and left
# FAILING on purpose rather than papered over: each of these is a program
# CPython runs (exit 0) that lypning-mp reports as `SyntaxError: invalid syntax`
# at exit 1 — the miss direction, the one that tells an agent its correct
# program is broken. They are a coverage number and a build order, in the same
# sense `conformance --plan` is; widening the scanner to catch them is a change
# to lypning_compat.h and an i386 rebuild, which is why it is not done here.
#
# `strict=True`: the day the scanner learns one of these, this suite goes red
# and the entry has to be deleted, rather than quietly passing while claiming
# the gap is open.

_KNOWN_MISSES = [
    # `match` split across lines: the header line does not end in `:`, so the
    # statement rule misses it, and an inline `case _: pass` body does not end in
    # `:` either, so the clause rule misses it too. Both rules are line-shaped
    # and this construct is not.
    ("match-head-wrapped-inline-body", "match (\n    1,\n    2,\n):\n    case _: pass"),
    # The positional-only marker outside a paren: a lambda has no brackets at
    # all, so `depth` is 0 and the `/` rule never looks.
    ("posonly-in-a-lambda", "f = lambda a, /, b: a + b\nprint(f(1, 2))"),
    # A signature broken across lines with the `/` on its own: the scan back for
    # the preceding separator steps over spaces and tabs but stops at a newline,
    # so the `,` on the line above is never reached.
    ("posonly-wrapped-signature", "def f(\n    a,\n    /,\n    b,\n): return a + b"),
]


@pytest.mark.xfail(strict=True, reason="known scanner miss — see _KNOWN_MISSES")
@pytest.mark.parametrize("source", [s for _, s in _KNOWN_MISSES],
                         ids=[i for i, _ in _KNOWN_MISSES])
def test_known_misses(scan, source):
    assert scan(source) != NONE, "CPython runs this; the scanner does not name it:\n%s" % source


# --- the binary, on all three source routes ----------------------------------

_ROUTES = ("-c", "file", "stdin")


def _run_route(binary: Path, route: str, source: str, work: Path) -> engines.Result:
    """Run ``source`` on the tier by one of the three routes.

    The argv is built here rather than going through :func:`engines.run`, the
    way :func:`engines.route` builds its own: ``run()`` has two argv shapes,
    ``-c PROG`` and a script path, and there is no third for ``<engine> -``.
    Since the whole point of this section is that the source reaches the scanner
    differently in each route, the route is exactly the thing that may not be
    abstracted away. The answer is still wrapped in :class:`engines.Result` so
    ``.refused`` and ``.refusal`` — the properties a dispatcher actually keys on
    — are what the assertions read.
    """
    if route == "-c":
        cmd, stdin = [str(binary), "-c", source], None
    elif route == "file":
        script = work / "prog.py"
        script.write_text(source, encoding="utf-8")
        cmd, stdin = [str(binary), str(script)], None
    else:
        cmd, stdin = [str(binary), "-"], source
    done = subprocess.run(cmd, input=stdin, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=30,
                          env=dict(os.environ, LYPNING_CAPTURE="0"), check=False)
    return engines.Result(engines.MICROPYTHON, str(binary), done.returncode,
                          done.stdout, done.stderr, 0)


#: One runnable program per construct — the fragments above cannot be executed.
_PROGRAMS = [
    ("match", 'x = 1\nmatch x:\n    case 1: print("one")\n', MATCH_STMT),
    ("case", "    case 1:\n        pass\n", CASE_CLAUSE),
    ("except-star", "try:\n    pass\nexcept* ValueError:\n    pass\n", EXCEPT_STAR),
    ("posonly", "def f(a, /, b): return a + b\nprint(f(1, 2))\n", POSONLY),
    ("dict-unpack", 'print({**{"a": 1}, "b": 2})\n', DICT_UNPACK),
    ("with-parens",
     'with (\n    open("a") as f,\n    open("b") as g,\n):\n    pass\n', WITH_PARENS),
]


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("source,detail", [(s, d) for _, s, d in _PROGRAMS],
                         ids=[i for i, _, _ in _PROGRAMS])
def test_the_tier_refuses_by_contract_on_every_route(micropython_bin, tmp_path,
                                                     route, source, detail):
    res = _run_route(micropython_bin, route, source, tmp_path)
    assert res.refused, "route %s: exit %d, stderr %r" % (route, res.returncode, res.stderr)
    assert res.refusal == ("syntax", detail)
    # Exactly one line, and nothing else on it: a dispatcher reads this line.
    assert res.stderr.strip().splitlines() == [
        "lypning-mp: unsupported: syntax: %s" % detail]
    # Nothing on stdout. Free here in a way it is not elsewhere: the parse fails
    # before a single statement runs, so there are no bytes to have committed —
    # which is exactly what makes the streaming-stdout defect on the RUNTIME
    # refusal path (README §5) a separate problem from this one.
    assert res.stdout == ""


#: The other direction. Every one of these is genuinely malformed, and each
#: carries the text of a construct the scanner looks for — inside a string, a
#: comment or a docstring — so a scanner that stopped skipping literals turns
#: a plain exit 1 into a 90 and sends the program to CPython to fail again.
_REAL_SYNTAX_ERRORS = [
    ("unbalanced-paren", "print(1\n"),
    ("match-in-a-string", 'print("match x:"\n'),
    ("re-match-nearby", "m = re.match(p, s\nprint(m)\n"),
    ("match-in-a-comment", "# match x:\nprint(1\n"),
    ("dict-unpack-in-a-docstring", 'def f():\n    """{**d}"""\n    return 1\nprint(f()\n'),
    ("division-typo", "print(total / count\n"),
    ("not-an-operator", "x === 1\n"),
]


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("source", [s for _, s in _REAL_SYNTAX_ERRORS],
                         ids=[i for i, _ in _REAL_SYNTAX_ERRORS])
def test_a_real_syntax_error_keeps_its_exit_1(micropython_bin, tmp_path, route, source):
    res = _run_route(micropython_bin, route, source, tmp_path)
    assert res.returncode == 1, "route %s: exit %d, stderr %r" % (
        route, res.returncode, res.stderr)
    assert not res.unsupported
    assert "unsupported" not in res.stderr
    assert "SyntaxError" in res.stderr or "IndentationError" in res.stderr


@pytest.mark.parametrize("route", _ROUTES)
def test_a_source_larger_than_the_static_buffer_still_reaches_the_scanner(
        micropython_bin, tmp_path, route):
    """The file route reads the source back on the error path, into an 8 KiB
    static buffer — and past it into a bounded malloc. A construct beyond the
    buffer is where that second branch is the only thing standing between a
    refusal and a bare SyntaxError, and it is reachable from a real program: the
    corpus's median entry is 6 lines but its longest is 600.
    """
    padding = "\n".join("x%d = %d" % (i, i) for i in range(1200))
    source = padding + "\nmatch x0:\n    case 1: pass\n"
    assert len(source) > 8192, "the padding no longer clears the static buffer"
    res = _run_route(micropython_bin, route, source, tmp_path)
    assert res.refused, "route %s: exit %d, stderr %r" % (route, res.returncode, res.stderr)
    assert res.refusal == ("syntax", MATCH_STMT)


@pytest.mark.parametrize("route", _ROUTES)
def test_the_documented_imprecision_costs_one_spawn(micropython_bin, tmp_path, route):
    """A program with BOTH a typo and a `match` reports the match.

    Stated in ``lypning_compat.h`` rather than hidden, and pinned here so it
    cannot drift silently in either direction. It exits 90 where CPython exits
    1, which costs one retry and then produces the accurate SyntaxError from
    CPython — the bounded, self-correcting direction. Reversing the priority to
    "report the typo" would mean guessing which of the two the parser tripped
    on, and guessing wrong there is the unrecoverable direction.
    """
    res = _run_route(micropython_bin, route, "match x:\n    case 1: pass\nprint(1\n", tmp_path)
    assert res.refused
    assert res.refusal == ("syntax", MATCH_STMT)


def test_a_program_with_none_of_this_still_runs(micropython_bin, tmp_path):
    """The floor: the scanner is not on the happy path and cannot touch it."""
    for route in _ROUTES:
        res = _run_route(micropython_bin, route, "print(41 + 1)\n", tmp_path)
        assert (res.returncode, res.stdout.strip()) == (0, "42"), route


def test_an_unbuilt_tier_is_a_status_line_not_a_crash(no_micropython):
    """Absent is the usual case — the tier needs a 32-bit toolchain and a network.

    Checked by pointing the finder at nothing rather than by reasoning about it,
    and it must degrade the same way every other path here does: a Result
    carrying 127 and one line, never an exception.
    """
    assert engines.find_micropython() is None
    res = engines.run(engines.MICROPYTHON, "match x:\n    case 1: pass\n")
    assert res.returncode == 127
    assert not res.refused
    assert "not built" in res.stderr
