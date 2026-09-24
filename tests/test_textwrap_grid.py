"""`textwrap`, as a grid and as a seeded differential fuzz: lypning-l against
the CPython running this file.

`tests/test_base64_grid.py` is the shape this follows. Every row runs from a
FRESH temp cwd (invariant 4) and must end one of exactly two ways:

  * byte-identical stdout AND the same exit code as CPython, or
  * a clean refusal: exit 90, nothing on stdout, and one
    ``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

**The part a handful of examples would miss is `break_on_hyphens=True`, the
default.** CPython splits with `TextWrapper.wordsep_re`, a regex with lookbehind
and three Unicode classes. `textwrap.rs` ports it as a hand-written scanner. It
refuses when it would have to classify a non-ASCII character beside a hyphen,
and nowhere else. `FUZZ_SAMPLE` holds a seeded sample of the differential fuzz
that gated the capability. The full run is ``python tests/test_textwrap_grid.py
--fuzz 20000``, which compares CPython and lypning-l in-process on every case,
with no spawn per case.

**Every refusal a SPELLED call can raise is decided in the walk**
(`route::textwrap_call_block`). That covers a keyword outside the served set, a
`*`/`**` splice, a wrong positional count and a literal of the wrong type.
`AFTER_A_BARRIER` asserts that each one lands before `os.mkdir` commits
anything.

**`dedent` is 3.14's.** 3.14 rewrote it. The margin is now the common prefix of
the `min()` and `max()` of the non-blank lines, and a line where `isspace()` is
true becomes `''` under Unicode rules. Rows that only 3.14 answers this way are
skipped on an older interpreter rather than graded against the wrong one.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from lypning import engines, paths

T = "import textwrap\n"

#: Does THIS interpreter's `dedent` use the 3.14 rule? The engine does, so the
#: rows that tell the two apart are graded only where they agree.
DEDENT_314 = textwrap.dedent(" \x0c\n a") == "\na"

#: Answers pinned from CPython 3.14.5 on 2026-09-24, one row per shape.
GRID = [
    T + "print(textwrap.fill('one two three four five six', width=12))\n"
        "print(textwrap.dedent('    a\\n    b\\n'), end='')",
    T + "print(textwrap.shorten('a very long sentence indeed here', width=20))",
    T + "print(textwrap.wrap('a well-known self-contained thing', 10))\n"
        "print(textwrap.wrap('a well-known self-contained thing', 10, break_on_hyphens=False))",
    T + "print(textwrap.wrap('Hello there -- you goof-ball, use the -b option!', 10))",
    T + "print(textwrap.wrap('2024-09-24 x_y-zz a-b ab-cd abc-d foo_-bar', 6))",
    T + "print(textwrap.wrap('supercalifragilistic', 6), "
        "textwrap.wrap('supercalifragilistic', 6, break_long_words=False))",
    T + "print(textwrap.wrap('ab-cdefghij', 5), textwrap.wrap('---abcdef', 4), "
        "textwrap.wrap('pre-x-y-z-long-word-here', 7))",
    T + "print(textwrap.wrap('  lead  and\\ttab  trail  ', 8), textwrap.wrap('a\\tb\\tc  d\\r\\ne', 12))",
    T + "print(textwrap.fill('hello world foo', 11, initial_indent='* ', subsequent_indent='  '))\n"
        "print(textwrap.fill('hello world', 3, initial_indent='    '))",
    T + "s = '- ' + 'word ' * 30\n"
        "print(textwrap.fill(s, 20, initial_indent='    ', subsequent_indent='    '))",
    T + "print(textwrap.fill('\u00e9migr\u00e9 na\u00efve caf\u00e9 \u00f1and\u00fa', 8))\n"
        "print(repr(textwrap.fill('', 5)), repr(textwrap.fill('   ', 5)), textwrap.wrap('', 5))",
    T + "for w in (11, 12, 5):\n    print(repr(textwrap.shorten('Hello  world!', width=w)))\n"
        "print(repr(textwrap.shorten('  Hello   there,\\n\\tworld  ', 13)), "
        "repr(textwrap.shorten('abcdefghijklmnop qr', width=10)))",
    T + "try:\n    textwrap.fill('x', 0)\nexcept ValueError as e:\n    print('VE', e)\n"
        "try:\n    textwrap.shorten('hello world', 3)\nexcept ValueError as e:\n    print('VE', e)\n"
        "try:\n    textwrap.wrap('x', -4)\nexcept ValueError as e:\n    print('VE', e)",
    T + "print(textwrap.fill('para ' * 40, width=78, break_long_words=False, break_on_hyphens=False))",
    T + "t = 'Engine strings are `engines.ENGINE_ORDER`, pinned by `engines.env_var_for`; "
        "`lypning-mp` is the oracle \u2014 measured, never routed to.'\n"
        "for w in textwrap.wrap(t, 30): print(w)\n"
        "print(textwrap.fill(t, 25, break_long_words=False, break_on_hyphens=False))",
    "from textwrap import dedent, fill\nprint(fill(dedent('''\n    alpha beta\n    gamma delta\n    ''').strip(), 11))",
    "import textwrap as tw\nprint(tw.fill('x ' * 20, 9))",
    "from textwrap import wrap as w\nprint(w('abc def ghi', 4))",
    T + "print(repr(textwrap.dedent('\\t  a\\n\\t  b\\n\\t\\n   \\n')))",
    T + "print(repr(textwrap.dedent('  a\\n\\tb\\n')), repr(textwrap.dedent('  a\\r\\n  b\\r\\n')))",
    T + "print(repr(textwrap.dedent('')), repr(textwrap.dedent('\\n\\n')), repr(textwrap.dedent('x')))",
    T + "print(repr(textwrap.indent('a\\nb\\n\\n  \\nc', '> ')))",
    "import json, textwrap\nprint(json.dumps(textwrap.indent('a\\r\\nb\\rc\\x0bd\\x1ce\\u2028f\\x85g', '+')))",
    T + "print(repr(textwrap.indent('', 'x')), repr(textwrap.indent('   ', 'x')), "
        "repr(textwrap.indent('a', '')))",
    T + "print(textwrap.shorten('The quick brown fox jumps', 15, placeholder='...'))\n"
        "print(textwrap.shorten('The quick brown fox jumps', width=15, placeholder=' \u2026'))",
    T + "print(textwrap.shorten('abc', 3, placeholder=''))",
    T + "print(textwrap.wrap('a--b c--d e-- f --g', 3), textwrap.wrap('x.--y', 2))",
    T + "print(textwrap.wrap('\u2014 dash \u2014 and -- more', 6))",
    T + "print(len(textwrap.wrap('word ' * 500, 37)))",
    # the result is a plain `str` / `list`, so everything after it is the core's
    T + "r = textwrap.wrap('a b c d e f', 3)\nr[0] = r[0].upper()\n"
        "print(r, len(r), 'c d' in r, r == ['A b', 'c d', 'e f'], {x: 1 for x in r})",
    T + "print('%s|%d' % (textwrap.fill('a b', 1), 5), textwrap.fill('a b', 1).split())",
]

#: Rows only 3.14's `dedent` answers the engine's way.
GRID_314 = [
    T + "print(repr(textwrap.dedent('  \\x0c x\\n  \\x0c y')))",
    T + "print(repr(textwrap.dedent('   \\n  x\\n    y\\n')), repr(textwrap.dedent('  x\\n \\xa0\\n  y')))",
]

#: What must REFUSE rather than answer. Exit 90 and an EMPTY stdout.
REFUSED = [
    T + "print(textwrap.dedent(None))",
    T + "print(textwrap.TextWrapper(width=10).wrap('a b'))",
    T + "print(textwrap.__file__)",
    T + "print(textwrap.fill('a b', 5, max_lines=1))",
    T + "print(textwrap.fill('a b', 5, placeholder='..'))",
    T + "print(textwrap.wrap('a\\tb', 5, expand_tabs=False))",
    T + "print(textwrap.wrap('a b', 5, drop_whitespace=False))",
    T + "print(textwrap.wrap('a b', 5, tabsize=4))",
    T + "print(textwrap.wrap('a b', 5, replace_whitespace=False))",
    T + "print(textwrap.wrap('a b', 5, fix_sentence_endings=True))",
    T + "print(textwrap.indent('a', '>', predicate=None))",
    T + "print(textwrap.indent('a', '>', lambda l: True))",
    T + "print(textwrap.shorten('a b', 5, initial_indent='x'))",
    T + "print(textwrap.shorten('a b'))",
    T + "print(textwrap.fill('a b', 5.0))",
    T + "print(textwrap.fill('a b', True))",
    T + "print(textwrap.fill(123, 5))",
    T + "print(textwrap.fill('a b', 5, break_on_hyphens=1))",
    T + "print(textwrap.fill('a b', 5, *[]))",
    T + "print(textwrap.fill('a b', **{'width': 5}))",
    # the hyphen regex over a non-ASCII letter: CPython's Unicode classes
    T + "print(textwrap.wrap('caf\u00e9-bar na\u00efve-x', 6))",
    T + "s = ''.join(['caf', '\u00e9-', 'bar'])\nprint(textwrap.fill(s, 4))",
    # CPython 3.14.5 never returns from this one: no line is left for the
    # leading space once the indent has taken the width
    T + "print(textwrap.wrap(' ab', 2, initial_indent='>>> '))",
]

#: The barrier every `AFTER_A_BARRIER` row is asked through; see
#: `test_base64_grid.py`, which measured why.
BARRIER = "import os\nos.mkdir('NEWD')\n"

AFTER_A_BARRIER = [
    ("textwrap", "print(textwrap.fill('a b', 5, max_lines=2))"),
    ("textwrap", "print(textwrap.fill('a b', 5, bogus=1))"),
    ("textwrap", "print(textwrap.fill('a b', 5, 6))"),
    ("textwrap", "print(textwrap.fill())"),
    ("textwrap", "print(textwrap.fill('a b', 5, width=4))"),
    ("textwrap", "print(textwrap.fill('a b', '5'))"),
    ("textwrap", "print(textwrap.fill('a b', width=5.5))"),
    ("textwrap", "print(textwrap.fill('a b', 5, break_long_words=None))"),
    ("textwrap", "print(textwrap.fill('a b', 5, initial_indent=3))"),
    ("textwrap", "print(textwrap.fill(None, 5))"),
    ("textwrap", "print(textwrap.dedent('a', 'b'))"),
    ("textwrap", "print(textwrap.dedent(3))"),
    ("textwrap", "print(textwrap.indent('a'))"),
    ("textwrap", "print(textwrap.indent('a', '>', predicate=str.strip))"),
    ("textwrap", "print(textwrap.shorten('a b c', width=3, placeholder='.', max_lines=1))"),
    ("textwrap", "print(textwrap.shorten('a b c'))"),
    ("textwrap", "print(textwrap.wrap(*['a b', 5]))"),
    ("textwrap", "print(textwrap.wrap('a b', **{'width': 5}))"),
    ("module-attr", "print(textwrap.TextWrapper)"),
    ("module-attr", "print(textwrap.__file__)"),
    ("module-attr", "print(textwrap._whitespace)"),
    ("textwrap", "import textwrap as t\nprint(t.fill('a b', 5, max_lines=1))"),
    ("textwrap", "from textwrap import fill\nprint(fill('a b', 5, max_lines=1))"),
    ("textwrap", "from textwrap import fill as f\nprint(f('a b', 5, 6))"),
    ("module-attr", "import textwrap as t\nprint(t.TextWrapper(width=4))"),
    ("module-attr", "from textwrap import TextWrapper\nprint(TextWrapper(width=4))"),
]

#: Routing, asked of the CORE — the binary that routes. The served surface goes
#: to lypning-l; what nothing on the spectrum serves goes past it.
ROUTED_TO_LYPNING_L = [
    T + "print(textwrap.fill('a b c', 3))",
    T + "print(textwrap.dedent('  a'))",
    "from textwrap import dedent, fill, indent, shorten, wrap\nprint(wrap('a b', 1))",
    "import textwrap as tw\nprint(tw.shorten('a b c', 3, placeholder=''))",
]
ROUTED_TO_CPYTHON = [
    T + "print(textwrap.TextWrapper(width=5).fill('a b'))",
    T + "print(textwrap.__file__)",
    "from textwrap import TextWrapper\nprint(TextWrapper)",
]


# ---- the fuzz ------------------------------------------------------------

#: ASCII punctuation the hyphen regex cares about, and some it does not.
_PUNCT = list("!\"'&.,?;:()`*/_")
#: Non-ASCII that is common in prose (dashes, quotes, arrows, which the scanner
#: classifies) and that is hostile (a letter, NBSP, a superscript digit, an
#: Arabic-Indic digit, the ideographic space, a C0 separator).
_UNI = ["\u00e9", "\u00a0", "\u2014", "\u2013", "\u201c", "\u2192", "\u00b2",
        "\u0663", "\u3000", "\x1c", "\u00fc", "\u2026"]
_WS = [" ", " ", " ", "  ", "\t", "\n", "\r\n", "\x0b", "\x0c", "   "]
_INDENTS = ["", "", " ", "  ", "* ", "    ", ">>> ", "\t"]
_PLACEHOLDERS = [" [...]", "...", " \u2026", "", "  ", "-"]


def _word(rng: random.Random, uni: bool) -> str:
    r = rng.random()
    letters = "abcdefghijklmnopqrstuvwxyzABC"
    if r < 0.08:
        return "-" * rng.randint(1, 4)
    if r < 0.14:
        return rng.choice(_PUNCT) * rng.randint(1, 2)
    if r < 0.2:
        return str(rng.randint(0, 99999))
    n = rng.choice([1, 2, 3, 4, 5, 6, 7, 9, 12]) if rng.random() < 0.9 else rng.randint(15, 55)
    w = "".join(rng.choice(letters) for _ in range(n))
    if rng.random() < 0.25:
        # a hyphenated or em-dashed compound, with digits and `_` in play
        w += rng.choice(["-", "-", "--", "_-", "-_", "-1"]) + "".join(
            rng.choice(letters + "_1") for _ in range(rng.randint(0, 6)))
    if rng.random() < 0.2:
        w += rng.choice(_PUNCT)
    if uni and rng.random() < 0.3:
        i = rng.randint(0, len(w))
        w = w[:i] + rng.choice(_UNI) + w[i:]
    return w


def _text(rng: random.Random) -> str:
    uni = rng.random() < 0.2
    parts = []
    for _ in range(rng.randint(0, 14)):
        parts.append(_word(rng, uni))
        parts.append(rng.choice(_WS))
    if rng.random() < 0.3 and parts:
        parts.pop()
    lead = rng.choice(["", "", "  ", "\t", "\n"])
    return lead + "".join(parts)


def _dedent_text(rng: random.Random) -> str:
    lines = []
    base = rng.choice(["", " ", "  ", "    ", "\t", "\t ", " \t"])
    for _ in range(rng.randint(0, 6)):
        r = rng.random()
        if r < 0.15:
            lines.append("")
        elif r < 0.3:
            lines.append(rng.choice([" ", "  ", "\t", " \x0c", " \xa0", "   ", "\r"]))
        else:
            extra = rng.choice(["", "", " ", "  ", "\t", "\x0c", "\xa0"])
            body = rng.choice(["a", "bc", "x y", "- item", "\u00e9", "q\r", "z  "])
            lines.append(base + extra + body)
    return "\n".join(lines)


def cases(seed: int, n: int) -> list:
    """``n`` seeded cases as ``(fn, text, width, a, b, blw, boh)``: `fn` 0 is
    `wrap`, 1 `fill`, 2 `shorten`, 3 `shorten` with a placeholder, 4 `dedent`,
    5 `indent`."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        fn = rng.choices(range(6), weights=[40, 20, 10, 10, 12, 8])[0]
        text = _dedent_text(rng) if fn in (4, 5) and rng.random() < 0.7 else _text(rng)
        width = rng.randint(1, 40)
        a = rng.choice(_PLACEHOLDERS if fn == 3 else _INDENTS)
        b = rng.choice(_INDENTS)
        case = (fn, text, width, a, b, rng.random() < 0.7, rng.random() < 0.7)
        if not _cpython_loops(case):
            out.append(case)
    return out


def _cpython_loops(case: tuple) -> bool:
    """The inputs CPython's `_wrap_chunks` never returns from: the first line's
    width is below 1 and the text opens with whitespace, which
    `_handle_long_word` shaves to an empty chunk that is dropped from the line
    and never from the stack. `textwrap.rs` refuses them (`REFUSED`)."""
    fn, t, w, a, _b, blw, _boh = case
    return fn in (0, 1) and blw and len(a) >= w and t[:1] in ("\t", "\n", "\x0b", "\x0c", "\r", " ")


def reference(case: tuple):
    """What CPython answers for one case — the same program body, in-process."""
    fn, t, w, a, b, blw, boh = case
    try:
        if fn == 0:
            return textwrap.wrap(t, w, initial_indent=a, subsequent_indent=b,
                                 break_long_words=blw, break_on_hyphens=boh)
        if fn == 1:
            return textwrap.fill(t, width=w, initial_indent=a, subsequent_indent=b,
                                 break_long_words=blw, break_on_hyphens=boh)
        if fn == 2:
            return textwrap.shorten(t, w)
        if fn == 3:
            return textwrap.shorten(t, width=w, placeholder=a)
        if fn == 4:
            return textwrap.dedent(t)
        return textwrap.indent(t, a)
    except ValueError as e:
        return "VE " + str(e)


#: The program each batch runs: the same dispatch as `reference`, spelled with
#: literal keywords because a `**kwargs` call is refused in the walk.
_BODY = """import json, textwrap
for fn, t, w, a, b, blw, boh in C:
    try:
        if fn == 0:
            r = textwrap.wrap(t, w, initial_indent=a, subsequent_indent=b, break_long_words=blw, break_on_hyphens=boh)
        elif fn == 1:
            r = textwrap.fill(t, width=w, initial_indent=a, subsequent_indent=b, break_long_words=blw, break_on_hyphens=boh)
        elif fn == 2:
            r = textwrap.shorten(t, w)
        elif fn == 3:
            r = textwrap.shorten(t, width=w, placeholder=a)
        elif fn == 4:
            r = textwrap.dedent(t)
        else:
            r = textwrap.indent(t, a)
    except ValueError as e:
        r = 'VE ' + str(e)
    print(json.dumps(r))
"""


def _program(batch: list) -> str:
    return "C = " + repr([list(c) for c in batch]) + "\n" + _BODY


def fuzz(binary: Path, seed: int, n: int, batch: int = 400) -> dict:
    """Run ``n`` cases through ``binary`` in batches. A batch that refuses is
    split in half until the refusing cases are alone, so a refusal costs its
    own case and not its neighbours. Returns counts and the disagreements."""
    todo = [c for c in cases(seed, n) if DEDENT_314 or c[0] != 4]
    stats = {"cases": len(todo), "agree": 0, "refused": 0, "disagree": []}

    def go(chunk: list) -> None:
        with tempfile.TemporaryDirectory() as d:
            got = subprocess.run([str(binary), "-c", _program(chunk)], capture_output=True,
                                 text=True, cwd=d, timeout=600)
        if got.returncode == engines.UNSUPPORTED_EXIT and len(chunk) > 1:
            assert got.stdout == "", "a refusal printed: %r" % got.stdout[:200]
            go(chunk[: len(chunk) // 2])
            go(chunk[len(chunk) // 2:])
            return
        if got.returncode == engines.UNSUPPORTED_EXIT:
            assert got.stdout == "" and got.stderr.count("\n") == 1, got.stderr[:300]
            stats["refused"] += 1
            return
        lines = got.stdout.split("\n")
        for i, c in enumerate(chunk):
            want = json.dumps(reference(c))
            if got.returncode == 0 and i < len(lines) and lines[i] == want:
                stats["agree"] += 1
            else:
                stats["disagree"].append((c, lines[i] if i < len(lines) else None,
                                          want, got.returncode, got.stderr[-300:]))

    for i in range(0, len(todo), batch):
        go(todo[i:i + batch])
    return stats


# ---- the harness -----------------------------------------------------------

def _spectrum(binary: Path) -> dict | None:
    try:
        out = subprocess.run([str(binary), "route", "--spectrum"],
                             capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError):
        return None


def _current(engine: str, cap: str) -> Path | None:
    """A built ``engine`` that carries THIS tree's capability table, or None —
    an older binary would turn every row into a green skip."""
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


BINARY = _current(engines.LYPNING_L, "cap-textwrap")
CORE = _current(engines.LYPNING, "cap-textwrap")

needs_l = pytest.mark.skipif(
    BINARY is None, reason="no lypning-l carrying cap-textwrap is built (lypning build --rust)")
needs_core = pytest.mark.skipif(
    CORE is None or BINARY is None,
    reason="routing needs the CORE that routes and the lypning-l it routes to")


def _run(argv: list, program: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True, text=True,
                              cwd=d, timeout=60)


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


_ROWS = GRID + (GRID_314 if DEDENT_314 else [])


@needs_l
@pytest.mark.parametrize("program", _ROWS, ids=range(len(_ROWS)))
def test_the_textwrap_grid_agrees_with_cpython(program: str) -> None:
    got = _run([str(BINARY)], program)
    ref = _run([sys.executable], program)
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "lypning-l disagrees with CPython (a refusal counts too: every row here is served).\n"
        "  program:  %r\n  lypning-l: %r exit %d %s\n  cpython:   %r exit %d"
        % (program, got.stdout, got.returncode, got.stderr.strip()[-200:],
           ref.stdout, ref.returncode))


@needs_l
@pytest.mark.parametrize("program", REFUSED, ids=range(len(REFUSED)))
def test_the_surface_outside_the_slice_refuses_rather_than_guesses(program: str) -> None:
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got)
    assert problem is None, "must refuse: %s\n  program: %r" % (problem, program)


@needs_l
@pytest.mark.parametrize("kind,call", AFTER_A_BARRIER, ids=range(len(AFTER_A_BARRIER)))
def test_every_spelled_refusal_lands_before_the_barrier(kind: str, call: str) -> None:
    if call.startswith(("import ", "from ")):
        head, tail = call.split("\n", 1)
        program = head + "\n" + BARRIER + tail
    else:
        program = T + BARRIER + call
    with tempfile.TemporaryDirectory() as d:
        before = sorted(os.listdir(d))
        got = subprocess.run([str(BINARY), "-c", program], capture_output=True,
                             text=True, cwd=d, timeout=60)
        after = sorted(os.listdir(d))
    problem = _refusal_problem(got)
    assert problem is None, "%s\n  program: %r" % (problem, program)
    assert ": %s: " % kind in got.stderr, (kind, got.stderr)
    assert before == after, "the refusal landed after the barrier: %r" % program


@needs_core
@pytest.mark.parametrize("program", ROUTED_TO_LYPNING_L, ids=range(len(ROUTED_TO_LYPNING_L)))
def test_the_core_routes_the_served_surface_to_lypning_l(program: str) -> None:
    route = engines.route(program, binary=CORE)
    assert route.engine == engines.LYPNING_L, (route.engine, route.kind, route.detail)


@needs_core
@pytest.mark.parametrize("program", ROUTED_TO_CPYTHON, ids=range(len(ROUTED_TO_CPYTHON)))
def test_the_core_routes_the_unserved_surface_past_lypning_l(program: str) -> None:
    route = engines.route(program, binary=CORE)
    assert route.engine == engines.CPYTHON, (route.engine, route.kind, route.detail)


#: The sampled fuzz: a fixed seed, so a failure reproduces exactly.
FUZZ_SEED = 20260924
FUZZ_SAMPLE = 1500


@needs_l
def test_a_seeded_fuzz_sample_agrees_with_cpython() -> None:
    stats = fuzz(BINARY, FUZZ_SEED, FUZZ_SAMPLE)
    assert not stats["disagree"], "%d disagreements, first: %r" % (
        len(stats["disagree"]), stats["disagree"][0])
    # A fuzz that refused everything would pass the line above and measure
    # nothing; the non-ASCII share of the alphabet is what refuses.
    assert stats["agree"] >= stats["cases"] * 9 // 10, stats


if __name__ == "__main__":
    # The merge gate: `python tests/test_textwrap_grid.py --fuzz 20000 [seed]`.
    n = int(sys.argv[sys.argv.index("--fuzz") + 1]) if "--fuzz" in sys.argv else 20000
    seed = int(sys.argv[-1]) if len(sys.argv) > 3 else 1
    if BINARY is None:
        sys.exit("no lypning-l carrying cap-textwrap is built")
    s = fuzz(BINARY, seed, n)
    print("cases %d agree %d refused %d disagree %d (CPython %s, seed %d)" % (
        s["cases"], s["agree"], s["refused"], len(s["disagree"]),
        sys.version.split()[0], seed))
    for d in s["disagree"][:10]:
        print(repr(d))
    sys.exit(1 if s["disagree"] else 0)
