"""`csv.reader` and `csv.DictReader`, as a grid: every program on the binary
and on CPython.

`tests/test_pathlib_grid.py` is the shape this follows, and the reason it exists
is the same: the defect is PER PROGRAM, not per function. Conformance grades the
corpus, which for `csv` is 23 programs of a handful of shapes; the grid is what
covers the rest of the dialect.

Every row is one program, run twice from a FRESH temp cwd (invariant 4 — and
these rows really do write files), and must end one of exactly two ways:

  * byte-identical stdout AND the same exit code as CPython 3.x, or
  * a clean refusal — exit 90, nothing on stdout, one
    ``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

Nothing else passes. A row is NOT allowed to be "close": a reader that swallows
an embedded newline prints a plausible list at exit 0, which is the failure
invariant 1 exists for.

The traps this was written against, each measured against CPython 3.14.5:

1. **The writers do not exist here, and must refuse from the WALK.** The first
   attempt at this capability (`docs/HILLCLIMB.md` iteration 74) added a
   `Value::CsvWriter` and paid five findings for it, all from one root cause: a
   new variant reaches `==`, `is`, hash and `list.remove` through arms nobody
   remembered to wire. `csv.writer` and `csv.DictWriter` are absent from
   `modules::get_attr`, so they are a `module-attr` refusal — `REFUSED` and
   `test_the_writers_refuse_from_the_walk` hold that.

2. **A reader is an ITERATOR, not a list.** Nine of the fifteen corpus readers
   call `next(r)` to skip a header, and a plain list would raise
   `TypeError: 'list' object is not an iterator` at exit 1. `PROTOCOL` is the
   block that holds it, together with the arms a value reaches that the ledger
   has been bitten by: `==`, `is`, `in`, `count`, `remove`, `sorted`, `bool`,
   `len`, `json.dumps`, `%`-format and slice assignment.

3. **The dialect is a character machine, not a `split`.** A doubled quote is one
   quote, a newline inside quotes does not end the record, an empty line is an
   empty LIST (not a one-empty-field row), a file with no trailing newline still
   yields its last record, and an empty file yields nothing. `DIALECT` crosses
   those contents with `delimiter=`, `quotechar=`, `escapechar=`,
   `skipinitialspace=`, `doublequote=` and every `QUOTE_*`.

4. **`open(newline=…)` is visible to a reader.** `newline=''` keeps `\\r\\n`
   verbatim and `newline=None` translates it, and the two differ exactly when a
   `\\r` falls inside a quoted field. `NEWLINES` is that block.

5. **The eager reader leaves the stream at EOF; CPython's lazy one does not.**
   `next(r)` then `f.read()` returns the rest under CPython. That refuses here
   rather than answering `''` at exit 0 — `REFUSED` holds it.

6. **Every error path is CPython's to word.** A bad dialect, a stray quote under
   `strict=True`, a `NUL`, a field over the limit, `csv.Sniffer`,
   `csv.field_size_limit`, `csv.QUOTE_STRINGS`: all refusals, and `REFUSED`
   asserts they are refusals rather than wrong answers.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

C = "import csv\n"


def _prog(content: str, body: str, newline: str = ", newline=''") -> str:
    """One row: write `content` to `d.csv`, open it, then run `body`."""
    return (C
            + "open('d.csv','w').write(%r)\n" % content
            + "f = open('d.csv'%s)\n" % newline
            + body + "\n")


#: Every content shape a reader has to get right, and the ones an implementation
#: gets wrong. The comment on each is what it is FOR.
CONTENTS = (
    "a,b\n1,2\n",              # the ordinary case
    "a,b,c\n1,2,3\n4,5,6\n",   # more than one data row
    "",                        # an empty file yields no rows at all
    "\n",                      # one empty line -> one EMPTY LIST
    "\n\n",                    # two of them
    "a\n\nb\n",                # an empty line between two records
    "a,b",                     # no trailing newline: the last record still comes
    "a,b\n",                   # a trailing newline adds no record
    "a,b\r\nc,d\r\n",          # CRLF
    "a,b\rc,d\r",              # bare CR is a line ending too
    '"a,b",c\n',               # the delimiter inside quotes
    '"a""b",c\n',              # a DOUBLED quote is one quote
    '"a\nb",c\n',              # an embedded newline inside quotes
    '"a\r\nb",c\n',            # an embedded CRLF inside quotes
    '"a\rb",c\n',              # an embedded CR inside quotes
    '"q,q",2,"say ""hi"""\n',  # all three at once
    '"a\n',                    # an unterminated quoted field at EOF
    '"a"x,b\n',                # a stray character after a closing quote
    '"",""\n',                 # two empty quoted fields
    '"" \n',                   # a quoted field then a space
    "a,,b\n",                  # an empty field in the middle
    ",\n",                     # two empty fields
    ",a\n",                    # a leading empty field
    "a,\n",                    # a trailing empty field
    " \n",                     # a field of one space
    "  \t \n",                 # a field of nothing but whitespace
    "  a , b\n",               # whitespace around the fields
    "a, b, \"c\"\n",           # a space before a quote (skipinitialspace)
    'a\\,b,c\n',               # a backslash, which is NOT special by default
    "a\\\\,b\n",               # two backslashes
    "a\\",                     # a trailing backslash at EOF
    "a!,b,c\n",                # a bang, for escapechar='!'
    "a;b\n",                   # a semicolon, for delimiter=';'
    "a\tb\n",                  # a tab
    "a|b\n",                   # a pipe
    "a'b',c\n",                # an apostrophe, for quotechar="'"
    "'a','b,b'\n",             # single-quoted fields
    "1,2\n",                   # digits, for QUOTE_NONNUMERIC
    "1,2\n3.5,x\n",            # a row that converts and one that does not
    '"1","2"\n',               # quoted digits are NOT converted
    "-3.5e2,x\n",              # an exponent
    "1,,2\n",                  # an empty field under QUOTE_NONNUMERIC
    "\ufeffa,b\n",             # a BOM is data, not a marker
    "é,ü\n",                   # non-ASCII
    "a,b\n\n\nc,d\n",          # two empty lines in the middle
)

#: The dialect arguments this engine serves, plus the bare default.
DIALECTS = (
    "",
    "delimiter=';'",
    "delimiter='\\t'",
    "delimiter='|'",
    "quotechar=\"'\"",
    "escapechar='\\\\'",
    "escapechar='!'",
    "skipinitialspace=True",
    "skipinitialspace=False",
    "doublequote=False",
    "doublequote=False, escapechar='\\\\'",
    "quoting=csv.QUOTE_MINIMAL",
    "quoting=csv.QUOTE_ALL",
    "quoting=csv.QUOTE_NONNUMERIC",
    "quoting=csv.QUOTE_NONE",
    "quoting=csv.QUOTE_NONE, escapechar='\\\\'",
    "strict=True",
    "strict=False",
    "delimiter=';', quotechar=\"'\", escapechar='!'",
    "skipinitialspace=True, quoting=csv.QUOTE_NONNUMERIC",
)

DIALECT = [
    _prog(c, "print(list(csv.reader(f%s)))" % ((", " + d) if d else ""))
    for c in CONTENTS
    for d in DIALECTS
]

#: `open(newline=…)` is the one place the STREAM, not the parser, decides where
#: a record ends — and the three modes differ only when a `\r` is in play.
NEWLINES = [
    _prog(c, "print(list(csv.reader(f)))", nl)
    for c in ("a,b\r\nc,d\r\n", "a,b\rc,d\r", '"a\r\nb",c\n', '"a\rb",c\n',
              "a,b\nc,d\n", "a,b\r\n")
    for nl in ("", ", newline=''", ", newline='\\n'")
]

#: The iterator protocol, and every arm a value reaches. Nine of the fifteen
#: corpus readers open with `next(r)`; the rest of this block is the wiring list
#: from `docs/LYPNING.md` §11 step 5, which is what iteration 74 was written up
#: for.
PROTOCOL = [
    _prog(c, b)
    for c in ("a,b\n1,2\n", "", "\n", "a\n", "a,b,c\n1,2,3\n4,5,6\n")
    for b in (
        "r=csv.reader(f)\nprint(next(r))\nprint(list(r))",
        "r=csv.reader(f)\nprint(next(r, 'DEF'))\nprint(next(r, 'DEF'))\nprint(next(r, 'DEF'))",
        "r=csv.reader(f)\nfor row in r:\n    print(row)",
        "r=csv.reader(f)\nprint(list(r))\nprint(list(r))",
        "r=csv.reader(f)\nprint(type(r).__name__)",
        "r=csv.reader(f)\nprint(iter(r) is r)",
        "r=csv.reader(f)\nprint(r == r)",
        "r=csv.reader(f)\nprint(r != r)",
        "r=csv.reader(f)\nprint(r == csv.reader(open('d.csv', newline='')))",
        "r=csv.reader(f)\nprint(r in [r])",
        "r=csv.reader(f)\nprint([r].count(r))",
        "r=csv.reader(f)\nprint([r].index(r))",
        "r=csv.reader(f)\nx=[r]\nx.remove(r)\nprint(x)",
        "r=csv.reader(f)\nprint(sorted([r]) == [r])",
        "r=csv.reader(f)\nprint(bool(r), not r)",
        "r=csv.reader(f)\nprint(len(list(r)))",
        "r=csv.reader(f)\nprint(sum(1 for _ in r))",
        "r=csv.reader(f)\nprint(tuple(r))",
        "r=csv.reader(f)\nprint([*r])",
        "r=csv.reader(f)\nprint(list(enumerate(r)))",
        "r=csv.reader(f)\nprint([','.join(x) for x in r])",
        "r=csv.reader(f)\nprint(json.dumps(list(r)))".replace("print", "import json\nprint", 1),
        "r=csv.reader(f)\nnext(r, None)\nprint(sum(int(x[0]) for x in r if x and x[0].isdigit()))",
        "r=csv.reader(f)\nrows=list(r)\nprint(rows[1:] if len(rows) > 1 else rows)",
        "r=csv.reader(f)\nprint(list(r))\nf.close()\nprint('closed')",
        "d=csv.DictReader(f)\nprint(list(d))",
        "d=csv.DictReader(f)\nprint(next(d, None))",
        "d=csv.DictReader(f)\nprint(type(d).__name__)",
        "d=csv.DictReader(f)\nprint([type(x).__name__ for x in d])",
        "d=csv.DictReader(f)\nfor row in d:\n    print(sorted(row.items(), key=str))",
    )
]

#: `DictReader`'s own rules: the header, a SKIPPED blank row, a short row filled
#: from `restval`, a long row whose tail goes under `restkey` (which is `None`
#: by default, so the dict grows a `None` key).
DICT_CONTENTS = (
    "a,b\n1,2\n",
    "a,b\n1,2,3\n",
    "a,b,c\n1\n",
    "a,b\n\n1,2\n",
    "",
    "a,b\n",
    "\n",
    'a,b\n"x,y",2\n',
    "a;b\n1;2\n",
    "a,b\n1,2\n3,4\n",
    "a,a\n1,2\n",
)
DICT_KWS = ("", "restkey='R'", "restval='V'", "restkey='R', restval='V'",
            "fieldnames=['x','y']", "fieldnames=('x','y')", "delimiter=';'",
            "quoting=csv.QUOTE_NONNUMERIC", "skipinitialspace=True")
DICTREADER = [
    _prog(c, "print([sorted(r.items(), key=str) for r in csv.DictReader(f%s)])"
             % ((", " + k) if k else ""))
    for c in DICT_CONTENTS
    for k in DICT_KWS
]

#: The module surface: the four constants are plain ints and behave as ints, and
#: the two readers are reachable under every spelling of the import.
SURFACE = [
    C + "print(csv.QUOTE_MINIMAL, csv.QUOTE_ALL, csv.QUOTE_NONNUMERIC, csv.QUOTE_NONE)",
    C + "print(csv.QUOTE_ALL + 1, csv.QUOTE_NONE * 2, csv.QUOTE_MINIMAL == 0)",
    C + "print(type(csv.QUOTE_ALL).__name__)",
    C + "print([csv.QUOTE_MINIMAL, csv.QUOTE_ALL] == [0, 1])",
    _prog("a,b\n1,2\n", "from csv import reader\nprint(list(reader(f)))"),
    _prog("a,b\n1,2\n", "from csv import DictReader\nprint(list(DictReader(f)))"),
    _prog("a,b\n1,2\n", "import csv as c\nprint(list(c.reader(f)))"),
    _prog("a,b\n1,2\n", "print(list(csv.reader(f, delimiter=',')))"),
    _prog("a,b\n1,2\n", "rows = list(csv.reader(f))\nprint(rows)"),
    _prog("a,b\n1,2\n", "with open('d.csv', newline='') as g:\n"
                       "    print(list(csv.reader(g)))"),
]

GRID = DIALECT + NEWLINES + PROTOCOL + DICTREADER + SURFACE

#: Programs CPython answers and this engine must REFUSE rather than answer.
#: Every one is exit 90, empty stdout, one refusal line — anything else here is
#: a silent divergence, which is the only outcome the mixture cannot survive.
REFUSED = [
    # The writers. Absent from `modules::get_attr`, so the WALK stops on them.
    _prog("", "w = csv.writer(f)"),
    _prog("", "print(csv.writer)"),
    _prog("", "print(csv.DictWriter)"),
    _prog("", "from csv import writer\nprint(writer)"),
    _prog("", "from csv import DictWriter\nprint(DictWriter)"),
    _prog("", "import csv as c\nprint(c.writer)"),
    C + "import sys\ncsv.writer(sys.stdout).writerow(['a','b'])",
    # Everything else under the module that CPython answers and this does not.
    C + "print(csv.Sniffer)",
    C + "print(csv.field_size_limit())",
    C + "print(csv.Error)",
    C + "print(csv.excel)",
    C + "print(csv.unix_dialect)",
    C + "print(csv.list_dialects())",
    C + "print(csv.register_dialect)",
    C + "print(csv.get_dialect('excel'))",
    C + "print(csv.QUOTE_STRINGS)",
    C + "print(csv.QUOTE_NOTNULL)",
    # A named dialect is a registry CPython owns.
    _prog("a,b\n", "print(list(csv.reader(f, 'excel')))"),
    _prog("a,b\n", "print(list(csv.reader(f, dialect='excel')))"),
    # The reader ignores `lineterminator` but VALIDATES it; refused rather than
    # reproduce a validation whose only effect is a message.
    _prog("a,b\n", "print(list(csv.reader(f, lineterminator='\\n')))"),
    _prog("a,b\n", "print(list(csv.reader(f, nosuch=1)))"),
    # Dialect validation: every one of these is a TypeError whose wording has
    # moved between CPython versions.
    _prog("a,b\n", "print(list(csv.reader(f, delimiter=';;')))"),
    _prog("a,b\n", "print(list(csv.reader(f, delimiter='')))"),
    _prog("a,b\n", "print(list(csv.reader(f, delimiter=5)))"),
    _prog("a,b\n", "print(list(csv.reader(f, quotechar='ab')))"),
    _prog("a,b\n", "print(list(csv.reader(f, escapechar='ab')))"),
    _prog("a,b\n", "print(list(csv.reader(f, quoting=9)))"),
    _prog("a,b\n", "print(list(csv.reader(f, quoting=-1)))"),
    _prog("a,b\n", "print(list(csv.reader(f, quoting='x')))"),
    _prog("a,b\n", "print(list(csv.reader(f, quotechar=None, quoting=csv.QUOTE_ALL)))"),
    # A csv.Error is CPython's to word.
    _prog('"a"x,b\n', "print(list(csv.reader(f, strict=True)))"),
    _prog('"a\n', "print(list(csv.reader(f, strict=True)))"),
    # A `\r` in the middle of an unquoted field, reached through a stream that
    # does not split on it: `new-line character seen in unquoted field`.
    _prog("a\rb\n", "print(list(csv.reader(f)))", ", newline='\\n'"),
    # The input shapes the mine does not show, refused rather than guessed at.
    _prog("", "print(list(csv.reader(['a,b'])))"),
    _prog("", "print(list(csv.reader('a,b')))"),
    _prog("", "print(list(csv.reader(iter(['a,b\\n']))))"),
    _prog("", "print(list(csv.reader(42)))"),
    _prog("", "print(list(csv.reader(None)))"),
    _prog("a,b\n", "g = open('d.csv','rb')\nprint(list(csv.reader(g)))"),
    _prog("a,b\n", "g = open('o.csv','w')\nprint(list(csv.reader(g)))"),
    _prog("a,b\n", "print(list(csv.reader()))"),
    _prog("a,b\n", "print(list(csv.DictReader()))"),
    _prog("a,b\n", "print(list(csv.DictReader(f, fieldnames='ab')))"),
    _prog("a,b\n", "print(list(csv.DictReader(f, nosuch=1)))"),
    # A reader's own attributes. CPython answers all four; an AttributeError
    # here would be exit 1, which the chain never retries.
    _prog("a,b\n", "r = csv.reader(f)\nprint(r.line_num)"),
    _prog("a,b\n", "r = csv.reader(f)\nprint(r.dialect)"),
    _prog("a,b\n1,2\n", "d = csv.DictReader(f)\nprint(d.fieldnames)"),
    _prog("a,b\n1,2\n", "d = csv.DictReader(f)\nprint(d.line_num)"),
    # The eager reader leaves the stream at EOF where CPython's lazy one does
    # not, so every later read of it refuses instead of inventing a position.
    _prog("a,b\n1,2\n", "r = csv.reader(f)\nnext(r)\nprint(f.read())"),
    _prog("a,b\n1,2\n", "r = csv.reader(f)\nprint(f.readline())"),
    _prog("a,b\n1,2\n", "r = csv.reader(f)\nprint(f.readlines())"),
    _prog("a,b\n1,2\n", "r = csv.reader(f)\nprint(f.tell())"),
    _prog("a,b\n1,2\n", "r = csv.reader(f)\nf.seek(0)\nprint(f.read())"),
    _prog("a,b\n1,2\n", "r = csv.reader(f)\nprint([l for l in f])"),
    # A SECOND reader over the same stream: CPython's lazy first one left the
    # file at the start, so its rows all go to the second.
    _prog("a,b\n1,2\n", "r1 = csv.reader(f)\nr2 = csv.reader(f)\nprint(list(r2))"),
    # A field past `csv.field_size_limit()`, whose message quotes a limit that
    # is CPython's to set.
    _prog("x" * 200000 + "\n", "print(list(csv.reader(f)))"),
    # Hashed by object identity in CPython, which is an address.
    _prog("a,b\n", "r = csv.reader(f)\nprint({r: 1}[r])"),
    _prog("a,b\n", "r = csv.reader(f)\nprint(len({r, r}))"),
    _prog("a,b\n", "r = csv.reader(f)\nprint(hash(r) == hash(r))"),
    # A repr with a heap address in it.
    _prog("a,b\n", "r = csv.reader(f)\nprint(r)"),
    _prog("a,b\n", "r = csv.reader(f)\nprint(repr(r))"),
    _prog("a,b\n", "r = csv.reader(f)\nprint('%s' % (r,))"),
    _prog("a,b\n", "d = csv.DictReader(f)\nprint(d)"),
]


def _spectrum(binary: Path) -> dict | None:
    """What ``binary`` says it is, or ``None`` if it will not say."""
    try:
        out = subprocess.run([str(binary), "route", "--spectrum"],
                             capture_output=True, text=True, timeout=60)
    except OSError:
        return None
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def _current(engine: str) -> Path | None:
    """A built ``engine`` that carries THIS tree's capability table.

    An installed binary from before this capability landed would answer every
    grid row with a refusal and turn the file into green skips measuring
    nothing, so a candidate is taken only if its compiled `route::CAPS` knows
    `cap-csv` and the set it was BUILT with is this tree's."""
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
        if any(row.get("cap") == "cap-csv" for row in table.get("caps", [])):
            return cand
    return None


BINARY = _current(engines.LYPNING_L)
CORE = _current(engines.LYPNING)

needs_l = pytest.mark.skipif(
    BINARY is None,
    reason="no lypning-l carrying cap-csv is built (cargo build --release "
           "--no-default-features --features variant-l --target-dir target/variant-l)",
)


def _run(argv: list[str], program: str, stdin: str = "") -> subprocess.CompletedProcess:
    """One program, in a temp cwd of its own — invariant 4, and these rows do
    write files, so the temp cwd is load-bearing rather than ceremonial."""
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True, text=True,
                              cwd=d, timeout=60, input=stdin)


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
def test_the_csv_grid_agrees_with_cpython(program: str) -> None:
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


#: The other input the mine DOES show — `csv.reader(sys.stdin)`, two corpus
#: programs — needs its own runner because the row has to be fed.
STDIN_ROWS = [
    (C + "import sys\nfor row in csv.reader(sys.stdin):\n    print(row[0], row[-1])",
     'a,b,c\n1,2,3\n"x\ny",5,6\n'),
    (C + "import sys\nprint(list(csv.reader(sys.stdin)))", "a,b\n1,2\n"),
    (C + "import sys\nprint(list(csv.reader(sys.stdin)))", ""),
    (C + "import sys\nr = csv.reader(sys.stdin)\nprint(next(r))\nprint(list(r))",
     "a,b\n1,2\n3,4\n"),
    (C + "import sys\nprint(list(csv.DictReader(sys.stdin)))", "a,b\n1,2\n"),
    (C + "import sys\nprint(list(csv.reader(sys.stdin, delimiter=';')))", "a;b\n"),
    (C + "import sys\nprint(list(csv.reader(sys.stdin)))", "a,b\r\nc,d\r\n"),
]


@needs_l
@pytest.mark.parametrize("program,stdin", STDIN_ROWS, ids=range(len(STDIN_ROWS)))
def test_the_csv_grid_over_stdin_agrees_with_cpython(program: str, stdin: str) -> None:
    got = _run([str(BINARY)], program, stdin)
    if got.returncode == engines.UNSUPPORTED_EXIT:
        problem = _refusal_problem(got)
        assert problem is None, "%s\n  program: %r" % (problem, program)
        pytest.skip("lypning-l refuses this row: %s" % got.stderr.strip()[:160])
    ref = _run([sys.executable], program, stdin)
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "lypning-l disagrees with CPython over stdin.\n  program: %r\n  stdin: %r\n"
        "  lypning-l: %r exit %d\n  cpython:   %r exit %d"
        % (program, stdin, got.stdout, got.returncode, ref.stdout, ref.returncode))


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
def test_the_writers_refuse_from_the_walk_and_not_at_runtime() -> None:
    """The whole shape of this capability, asserted.

    `csv.writer` is absent from `modules::get_attr`, so the router's static walk
    resolves it and stops — which is what makes it cost a static route instead
    of a run that gets partway and then refuses. A runtime refusal reached after
    a side effect the commit barrier has let through becomes exit 1, which the
    chain never retries; that is how the first `glob` attempt turned correct
    programs into failures."""
    for program in ("import csv\ncsv.writer(open('o','w')).writerow(['a'])\n",
                    "import csv\nw = csv.DictWriter(open('o','w'), ['a'])\n",
                    "from csv import writer\n"):
        route = subprocess.run([str(BINARY), "route", "-c", program],
                               capture_output=True, text=True, timeout=60)
        engine, _, detail = route.stdout.partition("\t")
        assert engine.strip() == engines.CPYTHON, route.stdout
        assert detail.strip().startswith("module-attr: csv."), route.stdout
    # …and a reader is NOT sent away.
    route = subprocess.run(
        [str(BINARY), "route", "-c", "import csv\nprint(list(csv.reader(open('d.csv'))))\n"],
        capture_output=True, text=True, timeout=60)
    assert route.stdout.split("\t")[0].strip() == engines.LYPNING_L, route.stdout


@needs_l
def test_the_capability_is_on_the_larger_variant_only() -> None:
    """The byte budget, defended by asking each binary what it is.

    A capability that leaked into the frozen variant would pass every grid row
    above — it is the same code — so this is the only place that can see it."""
    core = CORE
    if core is None:
        pytest.skip("no core carrying this tree's capability table is built")
    refused = _run([str(core)], "import csv")
    assert refused.returncode == engines.UNSUPPORTED_EXIT and refused.stdout == ""
    assert refused.stderr.strip() == engines.refusal_line(
        engines.LYPNING, "module", "import csv")
    # `newline=''` is served with the capability and by nothing smaller, and the
    # core still refuses it — with the kind that says so.
    nl = _run([str(core)], "f = open('x', 'w', newline='')")
    assert nl.returncode == engines.UNSUPPORTED_EXIT and nl.stdout == ""
    assert "open-newline" in nl.stderr

    # …and the core's ROUTER knows which sibling serves it, which is what makes
    # the refusal cost one spawn instead of a CPython one.
    route = subprocess.run([str(core), "route", "-c",
                            "import csv\nprint(list(csv.reader(open('d.csv'))))"],
                           capture_output=True, text=True, timeout=60)
    assert route.stdout.split("\t")[0].strip() == engines.LYPNING_L, route.stdout


@needs_l
def test_the_python_copy_of_the_capability_table_is_the_binarys_own() -> None:
    """`engines.VARIANT_CAPS` is a copy of `route::SPECTRUM`'s caps column, and
    a copy is honest only while something checks it."""
    out = subprocess.run([str(BINARY), "route", "--spectrum"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    table = json.loads(out.stdout.strip().splitlines()[-1])
    assert table["self"] == engines.LYPNING_L
    assert "cap-csv" in table["self_caps"]
    assert {r["name"]: tuple(r["caps"]) for r in table["spectrum"]} == engines.VARIANT_CAPS
    assert {r["cap"]: r["modules"] for r in table["caps"]}["cap-csv"] == ["csv"]
