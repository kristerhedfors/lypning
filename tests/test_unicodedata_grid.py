"""`unicodedata` on lypning-l, from the reference CPython's own tables.

`build.rs` dumps the reference's tables (`ucd_dump.py`) into lypning-l, so a
served answer is exact for that reference whatever its Unicode version; the
algorithm (UAX #15) is `ucd.rs`. Served: `unidata_version`, `category`,
`combining`, `decomposition`, `normalize`, `is_normalized`. Every argument
error, every other attribute, a surrogate from `chr()`, and — while the module
is imported — a Unicode-property question about non-ASCII text whose Rust
tables are not the reference's, refuse by the contract (exit 90, one line,
empty stdout). A program that never imports the module runs as the core runs
it (invariant 10).

The differential rows run the whole plane (properties) and every canonical
pair, pairs with a mark between, starter+starter, Hangul L+V / LV+T / L+V+T,
and pseudo-random sequences (the four forms and `is_normalized`) as ONE
program on each side and compare a digest. They were also run on 3.9 and 3.11
reference builds when this landed (see CHANGELOG); here they compare with the
interpreter the binary was built against, and skip on any other.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines

CORE = engines.find(engines.LYPNING)
LARGER = engines.find(engines.LYPNING_L)
needs_l = pytest.mark.skipif(LARGER is None, reason="lypning-l is not built: a hole, not a pass")
needs_both = pytest.mark.skipif(CORE is None or LARGER is None,
                                reason="needs the core and lypning-l (lypning build --rust)")

PROPERTIES = """\
import unicodedata as u
import hashlib
parts = []
for cp in range(0x110000):
    if 0xD800 <= cp <= 0xDFFF:
        continue
    c = chr(cp)
    parts.append('%s|%d|%s' % (u.category(c), u.combining(c), u.decomposition(c)))
print(u.unidata_version, len(parts), hashlib.sha256('\\n'.join(parts).encode()).hexdigest())
"""

NORMALIZATION = """\
import unicodedata as u
import hashlib
FORMS = ('NFC', 'NFD', 'NFKC', 'NFKD')
cps = [cp for cp in range(0x110000) if not 0xD800 <= cp <= 0xDFFF and (
    u.decomposition(chr(cp)) or u.combining(chr(cp)) or 0x1100 <= cp < 0x1200
    or 0xAC00 <= cp < 0xD7A4 or u.category(chr(cp)) in ('Mn', 'Mc'))]
pairs = []
for cp in cps:
    d = u.decomposition(chr(cp))
    if d and not d.startswith('<') and len(d.split()) == 2:
        pairs.append([int(x, 16) for x in d.split()])
texts = []
for cp in cps:
    c = chr(cp)
    texts += [c, c + '\\u0301', '\\u0301' + c, 'a' + c + '\\u0323\\u0302']
for a, b in pairs:
    for mid in ('', '\\u0323', '\\u0302', '\\u0f71', '\\u0b47'):
        texts.append(chr(a) + mid + chr(b))
texts += ['\\u0b47\\u0b3e', '\\u0b47\\u0301\\u0b3e']
for l in range(0x1100, 0x1113, 3):
    for v in range(0x1161, 0x1176, 4):
        texts.append(chr(l) + chr(v))
        for t in range(0x11a7, 0x11c3, 5):
            texts.append(chr(l) + chr(v) + chr(t))
            texts.append(chr(0xAC00 + ((l - 0x1100) * 21 + (v - 0x1161)) * 28) + chr(t))
seed = 12345
for _ in range(20000):
    s = ''
    for _ in range(seed % 6 + 1):
        seed = (seed * 1103515245 + 12345) % 2147483648
        s += chr(cps[seed % len(cps)])
    texts.append(s)
out = []
for t in texts:
    for f in FORMS:
        out.append(u.normalize(f, t))
        out.append('1' if u.is_normalized(f, t) else '0')
print(len(texts), hashlib.sha256('\\x00'.join(out).encode()).hexdigest())
"""

#: Served: stdout equal to the reference's. py-0e996bd950d0, py-d6e33f9a4680
#: and py-fea58362bd64 are corpus programs.
SERVED = [
    "import unicodedata\nprint(unicodedata.unidata_version)",
    "import unicodedata as u\nprint(u.category('a'), u.category('1'), u.category('\\u00c5'))\n"
    "print(u.normalize('NFC', '\\u00e5'))",
    "import unicodedata as u\nprint(u.normalize('NFD', 'Dal\\u00e9n').encode())",
    "import unicodedata as u\nprint(repr(u.decomposition('\\uac00')), repr(u.decomposition('\\uac01')))",
    "import unicodedata as u\nprint(u.normalize('NFKC', '\\ufb01\\u2460'), u.decomposition('\\ufb01'))",
    "import unicodedata as u\nprint([hex(ord(c)) for c in u.normalize('NFC', 'a\\u0302\\u0323')],\n"
    "      len(u.normalize('NFC', '\\u0915\\u093c')), u.normalize('NFC', '\\u1100\\u1161\\u11a8') == '\\uac01',\n"
    "      u.normalize('NFC', '\\u212b') == '\\u00c5', u.is_normalized('NFC', '\\u212b'))",
    "import unicodedata as u\nprint(u.category('\\U00016130'), u.combining('\\u0301'), u.normalize('NFC', 'abc'))",
    "from unicodedata import normalize, category\nprint(normalize('NFD', '\\u00e9') == 'e\\u0301', category(' '))",
    "import unicodedata as u\nprint(sorted({u.category(chr(c)) for c in range(0x300, 0x400)}))",
    PROPERTIES,
    NORMALIZATION,
]

#: Refused wherever they run: exit 90, empty stdout, one line.
REFUSED = [
    "import unicodedata as u\nprint(1)\nu.category('ab')",
    "import unicodedata as u\nprint(1)\nu.category(1)",
    "import unicodedata as u\nprint(1)\nu.combining('')",
    "import unicodedata as u\nprint(1)\nprint(u.normalize('NFX', 'a'))",
    "import unicodedata as u\nprint(1)\nprint(u.normalize(form='NFC', unistr='a'))",
    "import unicodedata as u\nprint(1)\nprint(u.normalize('NFC', b'a'))",
    "import unicodedata as u\nprint(1)\nprint(u.name('a'))",
    "import unicodedata as u\nprint(1)\nprint(u.lookup('LATIN SMALL LETTER A'))",
    "import unicodedata as u\nprint(1)\nprint(u.numeric('1'))",
    "import unicodedata as u\nprint(1)\nprint(u.__file__)",
    "import unicodedata as u\nprint(1)\nprint(getattr(u, 'name', None))",
    "import unicodedata as u\nprint(1)\nprint(hasattr(u, 'name'))",
    "import unicodedata as u\nprint(1)\nprint(u)",
    "import unicodedata as u\nprint(1)\nprint(u.category)",
    "import unicodedata as u\nprint(1)\nprint(u.category(chr(0xD800)))",
    "import unicodedata\nprint(1)\nundefined_name",
]

#: Answered as CPython answers, OR refused: `str`'s own Unicode tables are
#: Rust's, which is not the reference's Unicode on every build, so in a run
#: that imported `unicodedata` a non-ASCII property question refuses when they
#: differ. Never a third outcome.
DRIFT = [
    "import unicodedata\nprint('\\u0c5c'.isalpha())",
    "import unicodedata\nprint('\\U0001e5f1'.isalnum(), '\\u0661'.isdigit())",
    "import unicodedata\nprint(int('\\u0661'), float('\\u0661'))",
    "import unicodedata\nprint('\\u01c5'.upper(), '\\u00df'.casefold(), 'a\\u2009b'.split())",
    "import unicodedata\nprint('ABC'.lower(), 'a b'.split(), ' x '.strip(), int('7'))",
]

#: Without the import, the core's answer — on both binaries, byte for byte.
UNIMPORTED = [
    "print(len(chr(0xD800)))",
    "if False:\n    import unicodedata\nprint('\\u0c5c'.isalpha(), int('\\u0661'))",
    "if False:\n    import unicodedata\nprint(xx)",
]


def _run(argv: list, program: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """One program in a temp cwd of its own — invariant 4."""
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run([str(a) for a in argv] + ["-c", program], capture_output=True,
                              text=True, cwd=d, timeout=300, env=env)


def _reference() -> Path:
    """The interpreter lypning-l was built against, or skip."""
    exe = engines.find_cpython()
    got = _run([exe], "import sys;print('%d.%d' % sys.version_info[:2])").stdout.strip()
    if LARGER is None or engines.reference_minor(Path(LARGER)) != got:
        pytest.skip("lypning-l was built against another CPython than %s" % exe)
    return exe


def _refused(got: subprocess.CompletedProcess, engine: str = engines.LYPNING_L) -> None:
    assert (got.returncode, got.stdout) == (engines.UNSUPPORTED_EXIT, ""), (
        got.returncode, got.stdout, got.stderr)
    line = got.stderr.strip()
    assert "\n" not in line and line.startswith("%s: unsupported: " % engine), line


@needs_l
@pytest.mark.parametrize("program", SERVED, ids=range(len(SERVED)))
def test_a_served_row_answers_exactly_what_the_reference_answers(program: str) -> None:
    ref = _run([_reference()], program)
    got = _run([LARGER], program)
    assert (got.returncode, got.stdout) == (ref.returncode, ref.stdout) == (0, ref.stdout), (
        got.stderr[-300:])


@needs_l
@pytest.mark.parametrize("program", REFUSED, ids=range(len(REFUSED)))
def test_what_is_outside_the_slice_refuses_cleanly(program: str) -> None:
    _refused(_run([LARGER], program))


@needs_l
@pytest.mark.parametrize("program", DRIFT, ids=range(len(DRIFT)))
def test_a_unicode_property_answers_as_the_reference_or_refuses(program: str) -> None:
    got = _run([LARGER], program)
    if got.returncode == engines.UNSUPPORTED_EXIT:
        _refused(got)
        return
    ref = _run([_reference()], program)
    assert (got.returncode, got.stdout) == (ref.returncode, ref.stdout), got.stderr


@needs_both
@pytest.mark.parametrize("program", UNIMPORTED, ids=range(len(UNIMPORTED)))
def test_without_the_import_lypning_l_answers_as_the_core(program: str) -> None:
    core, larger = _run([CORE], program), _run([LARGER], program)
    assert core.returncode != engines.UNSUPPORTED_EXIT, core.stderr
    assert (larger.returncode, larger.stdout, larger.stderr.replace(engines.LYPNING_L, engines.LYPNING)) \
        == (core.returncode, core.stdout, core.stderr)


@needs_both
def test_the_core_routes_the_module_to_lypning_l_and_an_unserved_name_past_it() -> None:
    served = "import unicodedata as u\nprint(u.category('a'))"
    assert engines.route(served, binary=CORE).engine == engines.LYPNING_L
    _refused(_run([CORE], served), engine=engines.LYPNING)
    unserved = "import unicodedata as u\nprint(u.name('a'))"
    assert engines.route(unserved, binary=LARGER).engine == engines.CPYTHON


@needs_both
@pytest.mark.parametrize("program", [SERVED[1], REFUSED[6], REFUSED[14]], ids=range(3))
def test_the_chain_ends_with_the_reference_answer(program: str) -> None:
    exe = _reference()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["LYPNING_CPYTHON"] = str(exe)
    ref = _run([exe], program)
    for argv in ([CORE, "run"], [sys.executable, "-m", "lypning", "run"]):
        got = _run(argv, program, env=env)
        assert (got.returncode, got.stdout) == (ref.returncode, ref.stdout), (argv, got.stderr)
