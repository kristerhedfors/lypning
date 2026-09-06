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

5. **The reader is LAZY, so the stream is where CPython leaves it.** `next(r)`
   then `f.read()` returns the rest, `f.readline()` returns the next line, a
   second reader over an untouched first one gets every row, and `f.tell()` is
   the offset one record in. Seven of these were refusals while the reader was
   eager; `LAZY` is the block that holds them as ANSWERS, which is the only
   thing that can tell laziness from a well-guarded eagerness.

6. **Every error path is CPython's to word.** A bad dialect, a stray quote under
   `strict=True`, a `NUL`, a field over the limit, `csv.Sniffer`,
   `csv.field_size_limit`, `csv.QUOTE_STRINGS`: all refusals, and `REFUSED`
   asserts they are refusals rather than wrong answers.

7. **A dialect is checked against ITSELF.** `_csv` compares the three dialect
   characters to each other before it parses a byte — `delimiter=quotechar`,
   `escapechar=delimiter`, `escapechar=quotechar` — and rejects `\r` or `\n` as
   any of them. Validating each one in isolation passed all six and then parsed
   with a dialect CPython refuses to build. `SAME_CHAR` is that block, and every
   row of it is a refusal.

8. **The MOMENT a row is read is observable, and it is the file's.** Closing
   the file makes the next row a `ValueError`, and a `QUOTE_NONNUMERIC` field
   that `float()` cannot take raises the program's OWN `ValueError` from the
   iteration that reaches it — after the earlier rows have printed, which is
   what a `try:` around the loop is written to catch. Both fall out of a reader
   that holds the file rather than a copy of it. What does NOT fall out is a
   write to the path under an open handle: `FileObj::data` is the bytes `open()`
   read and CPython's is a descriptor, so `MOMENTS` keeps those rows and they
   are refusals.

9. **`open(newline=…)` is a promise to the STREAM, not only to the parser.**
   `newline=''` means a line ends at `\r\n`, `\n` OR a bare `\r`; `newline=None`
   means the same three ends AND that each arrives as `\n`. So `readline`,
   `readlines`, `for line in f`, `read()` and `seek(0)` all have to split and
   translate there too — and the reader has no splitter of its own, it reads
   these lines. `STREAM` is that block, it deliberately uses no `csv` at all,
   and it is run BYTE for byte: `text=True` universal-newline-translates a
   captured stream, so a harness that decodes cannot see this class of
   difference at all (`engines.run_engine` still cannot — see the commit).

10. **`sys.stdin` is not `open(p)`, and it has ONE cursor.** CPython opens it
    with `newline="\n"`, not with the `newline=None` a file gets, so a `\r`
    reaches the parser verbatim. And a reader, `input()`, `sys.stdin.read()`,
    `.readline()` and a `for` loop already in flight all advance the same
    position — the one stream a program cannot reopen, so an invented position
    there is unrecoverable. `STDIN_ROWS` interleaves all five.

11. **A `module` claim is not an attribute claim.** The binary that ROUTES is
    the core, which has none of this compiled in; `route::MODULE_ATTRS` is what
    lets it send `csv.writer` straight to CPython instead of into lypning-l, to
    be refused there after a side effect has already committed.
    `test_a_writer_after_a_side_effect_is_routed_away_not_refused_late` holds
    it end to end, through the real dispatcher.
"""

from __future__ import annotations

import json
import os
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

#: Trap 7. `_csv` builds the dialect before it reads a byte, and refuses these.
#: Each is a `ValueError` whose wording CPython owns, so each is a refusal here
#: — but a refusal at `csv.reader(…)`, where no row exists yet and no output has
#: been written, which is why it can still fall onward. The last four rows are
#: the dialects that must NOT be refused, so that the check cannot pass by
#: refusing everything.
SAME_CHAR = [
    _prog('a,"b""c",d\n', "print(list(csv.reader(f%s)))" % d)
    for d in (", delimiter=',', quotechar=','",
              ", delimiter=',', escapechar=','",
              ", quotechar='\"', escapechar='\"'",
              ", delimiter=' ', quotechar=' '",
              ", delimiter='\\t', quotechar='\\t'",
              ", delimiter='\\n'", ", delimiter='\\r'",
              ", quotechar='\\n'", ", quotechar='\\r'",
              ", escapechar='\\n'", ", escapechar='\\r'",
              # …and the ones CPython builds happily.
              ", delimiter=';', quotechar=\"'\"",
              ", delimiter=';', escapechar='\\\\'",
              ", quotechar=None, quoting=csv.QUOTE_NONE",
              ", delimiter='\\t'")
]

#: Trap 8. The moments a reader's file can be seen from outside it, and —
#: after each — the shape that must still WORK, because the cheapest way to
#: pass this block would be to refuse every reader whose file is ever closed or
#: written.
MOMENTS = [
    # The reader outlives its file: CPython raises, and so must this. It comes
    # out of `Iter::Lines`, which is where a `for line in f` would get it.
    _prog("a,b\nc,d\n", "with open('d.csv') as g:\n    r = csv.reader(g)\nprint(list(r))"),
    _prog("a,b\nc,d\n", "g=open('d.csv')\nr=csv.reader(g)\ng.close()\nprint(next(r))"),
    _prog("a,b\nc,d\n", "with open('d.csv') as g:\n    r=csv.DictReader(g)\n"
                         "print([sorted(x.items(), key=str) for x in r])"),
    _prog("a,b\nc,d\n", "with open('d.csv') as g:\n    r=csv.reader(g)\n    rows=list(r)\n"
                         "print(rows)\ntry:\n    next(r)\nexcept ValueError as e:\n"
                         "    print('VE', e)"),
    _prog("a,b\nc,d\n", "def mk():\n    with open('d.csv') as g:\n        return csv.reader(g)\n"
                         "print(list(mk()))"),
    # …and closed HALFWAY, which only a lazy reader can be: the rows before the
    # close have printed and the one after it raises.
    _prog("a,b\nc,d\ne,f\n", "g=open('d.csv')\nr=csv.reader(g)\nprint(next(r))\n"
                              "g.close()\ntry:\n    print(next(r))\nexcept ValueError as e:\n"
                              "    print('VE', e)"),
    # …and the shapes that close the file AFTER draining, which is every corpus
    # one and must keep answering.
    _prog("a,b\nc,d\n", "with open('d.csv') as g:\n    for row in csv.reader(g):\n"
                         "        print(row)"),
    _prog("a,b\nc,d\n", "g=open('d.csv')\nrows=list(csv.reader(g))\ng.close()\nprint(rows)"),
    # The file is written under the reader. This is the one divergence
    # laziness does NOT close, because it is not the reader's: `FileObj::data`
    # is the bytes `open()` read, where CPython holds a descriptor. Refusals.
    _prog("a\n", "r=csv.reader(f)\ng=open('d.csv','a')\ng.write('b\\n')\ng.close()\n"
                 "print(list(r))"),
    _prog("a\n", "r=csv.reader(f)\nopen('d.csv','w').write('b\\n')\nprint(list(r))"),
    _prog("a\n", "r=csv.DictReader(f)\nopen('d.csv','a').write('b\\n')\nprint(list(r))"),
    # …and the read-then-rewrite shape, where the write is to another path or
    # the reader is drained and dropped, and must therefore go through.
    _prog("a\nb\n", "rows=list(csv.reader(f))\nopen('o.csv','w').write(str(rows))\n"
                     "print(open('o.csv').read())"),
    _prog("a\nb\n", "for row in csv.reader(f):\n    open('log','a').write(str(row))\n"
                     "print(open('log').read())"),
    # …and the write-then-read-back shape, where the file was written BEFORE it
    # was opened, so the handle already has those bytes and is not stale at all.
    _prog("", "open('n.csv','w').write('a,b\\n1,2\\n')\n"
              "print(list(csv.reader(open('n.csv', newline=''))))"),
    # QUOTE_NONNUMERIC: `float()` runs during ITERATION, so a real `try` around
    # the loop catches the program's OWN ValueError and the rows before it have
    # printed. A reader that converted at construction moved that exception to
    # a different statement.
    _prog("1,2\nx,3\n", "r=csv.reader(f, quoting=csv.QUOTE_NONNUMERIC)\ntry:\n"
                         "    for row in r:\n        print(row)\nexcept ValueError:\n"
                         "    print('ve')"),
    _prog("1,2\nx,3\n", "try:\n    r=csv.reader(f, quoting=csv.QUOTE_NONNUMERIC)\n"
                         "    print('made')\n    print(list(r))\nexcept ValueError:\n"
                         "    print('bad')"),
    _prog("1,2\nx,3\n", "r=csv.reader(f, quoting=csv.QUOTE_NONNUMERIC)\nprint(next(r))\n"
                         "print(next(r))"),
    _prog("1,2\n3,4\n", "print(list(csv.reader(f, quoting=csv.QUOTE_NONNUMERIC)))"),
    _prog('"a",2\n', "print(list(csv.reader(f, quoting=csv.QUOTE_NONNUMERIC)))"),
]

#: Trap 5, and the block that tells a lazy reader from a well-guarded eager
#: one. Every row here was a REFUSAL until the reader started pulling its lines
#: from the file object: an eager reader can be stopped from answering wrongly,
#: but it cannot answer these at all.
LAZY = [
    _prog(c, b)
    for c in ("a,b\n1,2\n", "a,b\n1,2\n3,4\n", "a,b\n")
    for b in (
        "r=csv.reader(f)\nnext(r)\nprint(repr(f.read()))",
        "r=csv.reader(f)\nprint(repr(f.readline()))",
        "r=csv.reader(f)\nprint(f.readlines())",
        "r=csv.reader(f)\nprint(f.tell())",
        # `csv.reader(f)` is `iter(f)` plus one `next` per row, so a row taken
        # disables `tell()` exactly as a `for` loop's first line does.
        "r=csv.reader(f)\nnext(r, None)\nf.seek(0)\nprint(f.tell())",
        "r=csv.reader(f)\nnext(r, None)\nprint(repr(f.readline()))",
        "r=csv.reader(f)\nf.seek(0)\nprint(repr(f.read()))",
        "r=csv.reader(f)\nprint([l for l in f])",
        # A second reader over a first one that never advanced: CPython's left
        # the file at the start, so every row goes to the second.
        "r1=csv.reader(f)\nr2=csv.reader(f)\nprint(list(r2))",
        # …and over one that DID advance, where the two share the cursor.
        "r1=csv.reader(f)\nnext(r1, None)\nr2=csv.reader(f)\nprint(list(r2))",
        # Two readers alternating down one stream.
        "r1=csv.reader(f)\nr2=csv.reader(f)\nprint(next(r1, None), next(r2, None))",
        # A DictReader reads its header at the first row, not at construction.
        "d=csv.DictReader(f)\nprint(f.tell())\nprint(next(d, None) is not None)",
        "d=csv.DictReader(f)\nnext(d, None)\nf.seek(0)\nprint(f.tell())",
        "d=csv.DictReader(f)\nnext(d, None)\nprint(repr(f.read()))",
        # The reader stops at the record it was asked for and no further.
        "r=csv.reader(f)\nnext(r, None)\nprint(sum(1 for _ in f))",
    )
]

#: Trap 9. No `csv` here at all: this is the FILE OBJECT under the flag `csv`
#: made reachable, and every one of these answered wrongly at exit 0 while the
#: parser that shares the flag had it right.
STREAM = [
    "open('t','w',newline='').write(%r)\n" % c + b + "\n"
    for c in ("a\rb\rc\r", "a\r\nb\nc\rd", "a\nb\n", "a\r\nb\r\n", "", "a", "\r",
              "\r\n", "a\r\n", "a\rb")
    for b in ("print(open('t', newline='').readlines())",
              "print(sum(1 for _ in open('t', newline='')))",
              "print([repr(x) for x in open('t', newline='')])",
              "print(repr(open('t', newline='').readline()))",
              "f=open('t',newline='')\nprint(repr(f.readline()))\nf.seek(0)\n"
              "print(repr(f.readline()))",
              "print(repr(open('t', newline='').read()))",
              "print(open('t', newline='\\n').readlines())",
              "print(repr(open('t', newline='\\n').read()))",
              # …and the DEFAULT spelling, `newline=None`, which is the only
              # mode that TRANSLATES: every one of the three endings arrives as
              # a `\n`. A stream that split at `\n` alone answered these
              # wrongly at exit 0 and the parser above it went with them.
              "print(open('t').readlines())",
              "print([repr(x) for x in open('t')])",
              "print(repr(open('t').readline()))",
              "print(repr(open('t').read()))",
              "print(sum(1 for _ in open('t')))",
              # `tell()` on a text stream is an opaque COOKIE, and equals the
              # offset only where the decoder holds nothing. Just past a bare
              # `\r` it does not, and CPython answers a 39-digit integer.
              "f=open('t',newline='')\nf.readline()\nprint(f.tell())",
              "f=open('t')\nf.readline()\nprint(f.tell())",
              "f=open('t',newline='\\n')\nf.readline()\nprint(f.tell())",
              # …and `__next__` disables it outright until the iteration ends
              # or the stream is seeked. `readlines` restores it and `readline`
              # does not, which is the half of this rule that is not guessable.
              # (`iter(f)` and not `next(f)`: a file IS its own iterator in
              # CPython and is not one here, which is a divergence of its own
              # and not this one's — see the commit.)
              "f=open('t')\nfor l in f:\n    break\nprint(f.tell())",
              "f=open('t')\nfor l in f:\n    pass\nprint(f.tell())",
              "f=open('t')\nf.readlines()\nprint(f.tell())",
              "f=open('t')\nf.readline()\nprint(f.tell())",
              "f=open('t')\nit=iter(f)\nnext(it, None)\nprint(f.tell())",
              "f=open('t')\nit=iter(f)\nnext(it, None)\nf.seek(0)\nprint(f.tell())",
              "f=open('t')\nit=iter(f)\nnext(it, None)\nf.read()\nprint(f.tell())",
              "f=open('t')\nit=iter(f)\nnext(it, None)\nf.readlines()\nprint(f.tell())",
              "f=open('t','rb')\nit=iter(f)\nnext(it, None)\nprint(f.tell())",
              "f=open('t')\nprint(f.tell())\nprint(f.tell())")
]

#: Trap 9's other half: CPython rejects `newline=` on a BINARY stream, whatever
#: the value, and accepts an explicit `newline=None`.
BINARY_NEWLINE = [
    "open('t','wb').write(b'a\\r\\nb')\nprint(open('t','rb'%s).read())" % nl
    for nl in (", newline=''", ", newline='\\n'", ", newline=None", "")
]

GRID = (DIALECT + NEWLINES + PROTOCOL + DICTREADER + SURFACE
        + SAME_CHAR + MOMENTS + LAZY + STREAM + BINARY_NEWLINE)

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
    # `tell()` on a TEXT stream is an opaque cookie wherever the decoder holds
    # state, which just past a bare `\r` it does — 39 digits, not an offset.
    # (Every other position is exact and answers: see `LAZY` and `STREAM`.)
    "open('t','w',newline='').write('a\\rb\\n')\nf=open('t',newline='')\n"
    "f.readline()\nprint(f.tell())",
    "open('t','w',newline='').write('a\\rb\\n')\nf=open('t')\n"
    "f.readline()\nprint(f.tell())",
    # `read(n)` counts CHARACTERS of the translated stream, and under
    # `newline=None` a `\r\n` is one of them, so a byte count is the wrong
    # count and would leave the position wrong too.
    "open('t','w',newline='').write('a\\r\\nb\\n')\nprint(repr(open('t').read(3)))",
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
    """One program, in a temp cwd of its own, captured as BYTES.

    Two things here are load-bearing and neither is ceremonial. The temp cwd is
    invariant 4 — these rows really do write files. And `text=False` is trap 9:
    `text=True` decodes a captured stream with universal newlines, so a stdout
    of `b'a\\r\\n'` and one of `b'a\\n'` arrive identical and every row about a
    carriage return quietly stops measuring anything. The battery
    (`engines.run_engine`) still captures both arms with `text=True` and so
    still cannot see this class of difference; this file can, which is the only
    reason the rows are worth writing."""
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True,
                              cwd=d, timeout=60, input=stdin.encode())


def _err(got: subprocess.CompletedProcess) -> str:
    """The refusal line, which is text even where stdout is not."""
    return got.stderr.decode("utf-8", "replace")


def _refusal_problem(got: subprocess.CompletedProcess) -> str | None:
    """``None`` if this is a clean exit-90 refusal, else what is wrong with it."""
    if got.returncode != engines.UNSUPPORTED_EXIT:
        return "exit %d, not %d" % (got.returncode, engines.UNSUPPORTED_EXIT)
    if got.stdout != b"":
        return "stdout was not empty: %r" % got.stdout[:120]
    head = "%s: unsupported: " % engines.LYPNING_L
    line = _err(got).strip()
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
        pytest.skip("lypning-l refuses this row: %s" % _err(got).strip()[:160])
    ref = _run([sys.executable], program)
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "lypning-l disagrees with CPython.\n"
        "  program:  %r\n"
        "  lypning-l: %r exit %d %s\n"
        "  cpython:   %r exit %d %s"
        % (program, got.stdout, got.returncode, _err(got).strip()[-200:],
           ref.stdout, ref.returncode, _err(ref).strip()[-200:])
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
    # Trap 10. CPython opens `sys.stdin` with `newline="\n"`, so a `\r` inside a
    # quoted field survives and a bare `\r` between records is a csv.Error —
    # neither of which is true of the `newline=None` a plain `open()` gets.
    (C + "import sys\nprint(list(csv.reader(sys.stdin)))", '"a\rb",c\n'),
    (C + "import sys\nprint(list(csv.reader(sys.stdin)))", "a,b\rc,d\r"),
    (C + "import sys\nprint(list(csv.reader(sys.stdin)))", '"a\r\nb",c\n'),
    # …and the CURSOR, on the one stream that cannot be reopened, which is why
    # an invented position here is unrecoverable. A reader that took the whole
    # stream at construction answered every one of these empty at exit 0, and a
    # flag on the stream closed the first four and not the rest: `input()`
    # reached the bytes through a different function, and a `for` loop already
    # in flight had been handed its iterator before the flag was set. There is
    # no flag now — one cursor, advanced a line at a time by whoever asks next.
    (C + "import sys\nr=csv.reader(sys.stdin)\nprint(repr(sys.stdin.read()))", "a,b\nc,d\n"),
    (C + "import sys\nr=csv.reader(sys.stdin)\nprint(repr(sys.stdin.readline()))", "a,b\nc,d\n"),
    (C + "import sys\nr=csv.reader(sys.stdin)\nprint(sys.stdin.readlines())", "a,b\nc,d\n"),
    (C + "import sys\nr=csv.DictReader(sys.stdin)\nprint(repr(sys.stdin.read()))", "a,b\nc,d\n"),
    (C + "import sys\nr=csv.reader(sys.stdin)\nfor l in sys.stdin:\n    print(l)", "a,b\nc,d\n"),
    (C + "import sys\nr=csv.reader(sys.stdin)\nnext(r)\nprint(repr(sys.stdin.read()))",
     "a,b\nc,d\ne,f\n"),
    (C + "import sys\nr=csv.reader(sys.stdin)\nnext(r)\nprint(repr(sys.stdin.readline()))",
     "a,b\nc,d\ne,f\n"),
    # `input()`, which reaches the stream through neither `sys.stdin` nor the
    # reader and so passed every guard that stood between them.
    (C + "import sys\nr=csv.reader(sys.stdin)\nprint(next(r))\nprint(input())",
     "a,b\n1,2\n3,4\n"),
    (C + "import sys\nprint(input())\nr=csv.reader(sys.stdin)\nprint(list(r))",
     "a,b\n1,2\n3,4\n"),
    (C + "import sys\nr=csv.reader(sys.stdin)\nprint(input())\nprint(next(r))",
     "a,b\n1,2\n3,4\n"),
    # …and an iterator over the stream already IN FLIGHT when the reader is
    # made, which is the case a guard installed once per loop cannot reach.
    (C + "import sys\nit=iter(sys.stdin)\nprint(next(it).strip())\n"
         "r=csv.reader(sys.stdin)\nprint(next(r))\nprint(next(it).strip())",
     "a,b\n1,2\n3,4\n"),
    (C + "import sys\nfor l in sys.stdin:\n    print(list(csv.reader(sys.stdin)))\n    break",
     "a,b\n1,2\n3,4\n"),
    # A DictReader reads its header at the first row, not at construction.
    (C + "import sys\nd=csv.DictReader(sys.stdin)\nprint(repr(sys.stdin.readline()))",
     "a,b\n1,2\n"),
]


@needs_l
@pytest.mark.parametrize("program,stdin", STDIN_ROWS, ids=range(len(STDIN_ROWS)))
def test_the_csv_grid_over_stdin_agrees_with_cpython(program: str, stdin: str) -> None:
    got = _run([str(BINARY)], program, stdin)
    if got.returncode == engines.UNSUPPORTED_EXIT:
        problem = _refusal_problem(got)
        assert problem is None, "%s\n  program: %r" % (problem, program)
        pytest.skip("lypning-l refuses this row: %s" % _err(got).strip()[:160])
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
        % (problem, program, _err(got).strip()[:200])
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
def test_a_writer_after_a_side_effect_is_routed_away_not_refused_late() -> None:
    """Trap 11, end to end through the REAL dispatcher.

    `test_the_writers_refuse_from_the_walk_and_not_at_runtime` asks lypning-l,
    which serves `csv` and therefore resolves `csv.writer` in its own walk. The
    dispatcher does not ask lypning-l — it asks the cheapest binary there is
    (`engines.route` → `find_lypning`), and the core has none of `csv.rs`
    compiled in. With `csv` claimed by the module name alone, the core sent every
    csv program into lypning-l; a program that committed a side effect first and
    then reached `csv.writer` got that refusal at RUNTIME, past the commit
    barrier, and the 90 became exit 1 for a program CPython answers. This asserts
    the fix where it has to hold: the core's own route, and the dispatcher's
    exit code.
    """
    core = CORE
    if core is None:
        pytest.skip("no core carrying this tree's capability table is built")
    program = ("import csv, os\n"
               "os.mkdir('d')\n"
               "w = csv.writer(open('o','w'))\n"
               "w.writerow([1, 2])\n"
               "print('ok')\n")
    route = subprocess.run([str(core), "route", "-c", program],
                           capture_output=True, text=True, timeout=60)
    engine, _, detail = route.stdout.partition("\t")
    assert engine.strip() == engines.CPYTHON, route.stdout
    assert detail.strip() == "module-attr: csv.writer", route.stdout
    # …and the same for every other name `csv.rs` declines, under every spelling
    # of the import, from the binary that cannot see `csv.rs` at all.
    for prog, want in (
            ("import csv\ncsv.DictWriter(open('o','w'), ['a'])\n", "csv.DictWriter"),
            ("import csv\nprint(csv.Sniffer)\n", "csv.Sniffer"),
            ("import csv\nprint(csv.field_size_limit())\n", "csv.field_size_limit"),
            ("import csv as c\nc.writer(open('o','w'))\n", "csv.writer"),
            ("from csv import writer\n", "csv.writer"),
    ):
        r = subprocess.run([str(core), "route", "-c", prog],
                           capture_output=True, text=True, timeout=60)
        assert r.stdout.split("\t")[0].strip() == engines.CPYTHON, (prog, r.stdout)
        assert r.stdout.partition("\t")[2].strip() == "module-attr: " + want, (prog, r.stdout)
    # …while a READER, and the constants, still route INTO lypning-l: the table
    # must not have bought the exit code by sending the capability away.
    for prog in ("import csv\nprint(list(csv.reader(open('d.csv'))))\n",
                 "import csv\nprint(list(csv.DictReader(open('d.csv'))))\n",
                 "import csv\nprint(csv.QUOTE_NONNUMERIC)\n",
                 "from csv import reader\nprint(reader)\n"):
        r = subprocess.run([str(core), "route", "-c", prog],
                           capture_output=True, text=True, timeout=60)
        assert r.stdout.split("\t")[0].strip() == engines.LYPNING_L, (prog, r.stdout)
    # The dispatcher's own answer, which is the number that was wrong: the
    # program runs, on CPython, at exit 0.
    with tempfile.TemporaryDirectory() as d:
        # `python -m lypning` in a subprocess does not inherit this run's
        # import path: under `uv run` the interpreter is an isolated one that
        # has never seen `src/`. Hand it the same sys.path this process has, or
        # the test measures "no module named lypning" and calls it a failure.
        env = dict(os.environ)
        parts = [p for p in sys.path if p]
        if env.get("PYTHONPATH"):
            parts.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(parts)
        got = subprocess.run([sys.executable, "-m", "lypning", "run", "-c", program],
                             capture_output=True, text=True, cwd=d, timeout=120, env=env)
    assert (got.returncode, got.stdout) == (0, "ok\n"), (got.returncode, got.stdout, got.stderr)


@needs_l
def test_the_capability_is_on_the_larger_variant_only() -> None:
    """The byte budget, defended by asking each binary what it is.

    A capability that leaked into the frozen variant would pass every grid row
    above — it is the same code — so this is the only place that can see it."""
    core = CORE
    if core is None:
        pytest.skip("no core carrying this tree's capability table is built")
    refused = _run([str(core)], "import csv")
    assert refused.returncode == engines.UNSUPPORTED_EXIT and refused.stdout == b""
    assert _err(refused).strip() == engines.refusal_line(
        engines.LYPNING, "module", "import csv")
    # `newline=''` is served with the capability and by nothing smaller, and the
    # core still refuses it — with the kind that says so.
    nl = _run([str(core)], "f = open('x', 'w', newline='')")
    assert nl.returncode == engines.UNSUPPORTED_EXIT and nl.stdout == b""
    assert "open-newline" in _err(nl)

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


@needs_l
def test_the_route_attribute_table_is_what_the_module_serves() -> None:
    """`route::MODULE_ATTRS` is a hand-written copy of what `csv.rs` answers,
    read by a binary that does not have `csv.rs` — so the two have to be held
    together from the outside as well as by the crate's own unit test. A name
    in the table the module refuses routes a program into a refusal; a name the
    module serves and the table omits sends a program lypning-l would have run
    to CPython instead."""
    served = ("reader", "DictReader", "QUOTE_ALL", "QUOTE_MINIMAL",
              "QUOTE_NONE", "QUOTE_NONNUMERIC")
    refused = ("writer", "DictWriter", "Sniffer", "field_size_limit", "Error",
               "excel", "unix_dialect", "register_dialect", "list_dialects",
               "get_dialect", "unregister_dialect", "QUOTE_STRINGS", "QUOTE_NOTNULL")
    head = "%s: unsupported: module-attr: " % engines.LYPNING_L
    for name in served:
        # A served name may still refuse for another reason — `print(csv.reader)`
        # is a `repr` of a builtin, which CPython prints with an address. What it
        # may not be is `module-attr`, which is the kind the router reads.
        got = _run([str(BINARY)], "import csv\nprint(csv.%s)" % name)
        assert not _err(got).startswith(head), (name, _err(got))
    for name in refused:
        got = _run([str(BINARY)], "import csv\nprint(csv.%s)" % name)
        assert _refusal_problem(got) is None, (name, got.returncode, _err(got))
        assert _err(got).startswith(head + "csv." + name), (name, _err(got))
