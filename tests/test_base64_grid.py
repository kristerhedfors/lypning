"""`base64`, as a grid: every program on the binary and on CPython.

`tests/test_glob_grid.py` is the shape this follows and the reason it exists:
the defect is PER PROGRAM, not per function, and a handful of examples is
exactly what would miss it.

Every row runs from a FRESH temp cwd (invariant 4) and must end one of exactly
two ways:

  * byte-identical stdout AND the same exit code as CPython 3.x, or
  * a clean refusal — exit 90, nothing on stdout, one
    ``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

**The one thing this file is really for.** `b64decode` in its default
`validate=False` mode **DISCARDS** every byte outside the 64-character
alphabet, and the discarding is what decides the padding. A naive decoder
raises on ``b"a!Gk="`` and on ``b"aG k="``; CPython answers ``b'hi'`` for both.
Worse, a per-quad decoder ANSWERS where CPython raises: ``b"aGk=aGk="`` looks
like two padded quads and is really the six data characters ``aGkaGk`` with no
trailing pad at all, which is a `binascii.Error`. The rule, derived from 150,000
differential rows against CPython 3.14.5 on 2026-09-06 and stated once in
`base64.rs`:

  1. `data` is the subsequence of input bytes in ``A-Za-z0-9+/``. ``=`` is not
     in it, and neither is whitespace, punctuation, NUL or any byte above 127.
  2. ``len(data) % 4 == 0`` always decodes — ``b"===="`` and ``b"-_--"`` are
     both ``b''``, because neither holds one alphabet byte.
  3. ``% 4 == 1`` always raises: six bits cannot make a byte.
  4. ``% 4`` of 2 or 3 decodes only if at least ``4 - rem`` ``=`` bytes follow
     the LAST alphabet byte — counted over the whole input, never per quad.

`PADDING` below is that rule as a table, and every row of it was run under
`python3 -c` before this file was written.

**The MicroPython half of the trap.** Three corpus programs (py-16c1663c6170,
py-2d9c1f2c80f4, py-5313beb3de72, mined 2026-09-06) are differential harnesses
an agent typed against `extmod/modbinascii.c`, whose `a2b_base64` counts pads
PER QUAD and stops at the first complete one. That is the wrong answer in both
directions and it is the answer a reimplementation reaches for. `.github/
known-mismatches.json` has no base64 family because those harnesses were how the
question got asked; the rows here are what they were asking.

**Every `binascii.Error` is a refusal, not a raise.** The class does not exist
here and the message text is CPython's, which moves between releases — so a
decode this engine cannot perform refuses, and CPython answers it one spawn
later with its own wording. Same for the `TypeError` a `str` handed to an
encoder raises, and for the `ValueError` a non-ASCII `str` handed to a decoder
raises.

**No new `Value` variant.** `bytes` in, `bytes` out — which is what CPython's
own functions return, so the exact shape is also the free one. `RESULT_USES`
is the check that this is true rather than intended: it runs the result through
print, `==`, `<`, `in`, `len`, indexing, slicing, iteration, a dict key, a list
element that is then reassigned, `%`-formatting, concatenation and
`json.dumps`, because five capabilities in a row shipped a variant that reached
one of those through an arm nobody remembered (`docs/HILLCLIMB.md` iterations
74, 76 and 77).

**The barrier class.** Serving the module means the program STARTS here, and a
refusal reached after `os.mkdir` has committed the write barrier (`io.rs`) is
exit 1 with the directory on disk and no answer, which the chain never retries —
issue #51. So every refusal a served call can raise that the SOURCE spells is
decided in the walk, by `route::base64_call_block`, and asserted here:
`AFTER_A_BARRIER` runs each shape as ``os.mkdir('NEWD'); <the call>`` and
asserts four ways — exit 90, one refusal line, an EMPTY stdout, and a cwd whose
listing is unchanged, measured by a before/after snapshot rather than read out
of the message. `RUNTIME_BACKSTOP` is the residue that stays dynamic, one row
per shape, with the reason each one cannot be hoisted.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

B = "import base64\n"

#: The decode rule, one row per shape, every one of them run under `python3 -c`
#: first. The comment on a row names which of the four clauses it pins.
PADDING = [
    b"", b"aGk=", b"aGk", b"a", b"aa==", b"ab==", b"aaa=", b"aaa==", b"aGk==",
    b"aGkx", b"abcd", b"ab", b"abc", b"A", b"AB", b"ABC", b"ABCD",
    # `=` is not an alphabet byte, so a run of them is an EMPTY input
    b"=", b"==", b"===", b"====", b"aGk=====",
    # the per-quad decoder's two wrong answers, in both directions
    b"aGk=aGk=", b"AA==AA==", b"AB==CD==", b"AA==AA", b"AAA=A", b"AB=CD",
    b"ABC=D", b"AA==A", b"AA==AAA", b"AA==AAAA", b"ABC=DE", b"ABC=DEF",
    b"ABC=DEFG", b"AB=C", b"A=A=", b"A==A", b"=A=A", b"AAA=AB", b"AAA=ABC",
    # non-alphabet bytes are DISCARDED under the default validate=False
    b"a!Gk=", b"aG k=", b"aGk=\n", b"\n\naGk=", b"a b c d", b"\x00\x01aGk=",
    b"\xff\xfe", b"  ", b"aa=x", b"a=b=", b"aa=b=", b"aa==b", b"aa=\n=",
    b"aa\n==", b"aGk= =", b"AA=\x00=", b"aGk=x", b"=aGk", b"a===",
    # `-` and `_` are not in the STANDARD alphabet: b64decode drops them
    b"-_--", b"+/++", b"/+/+", b"++==", b"a-_b",
    # one more than a multiple of four, at several lengths
    b"aGkxx", b"aGkxaGk", b"aaaaa=", b"aaaaaa=", b"aaaaaaa=", b"aaaa=",
]

#: The same inputs through both decoders, and through the `str` spelling that
#: `json.load(...)['content']` hands an agent's one-liner — which is the way
#: py-0b4f04c077ec reaches this function.
DECODE = (
    [B + "print(repr(base64.b64decode(%r)))" % c for c in PADDING]
    + [B + "print(repr(base64.urlsafe_b64decode(%r)))" % c for c in PADDING]
    + [B + "print(repr(base64.b64decode(%r)))" % c.decode("latin-1")
       for c in PADDING if c.isascii()]
    + [B + "print(repr(base64.urlsafe_b64decode(%r)))" % c.decode("latin-1")
       for c in PADDING if c.isascii()]
    # the same value COMPUTED, which is the runtime path rather than the walk
    + [B + "x = %r + b''\nprint(repr(base64.b64decode(x)))" % c for c in PADDING]
)

#: Every byte length across two quad boundaries, both alphabets, plus the bytes
#: that separate them: `+`/`/` become `-`/`_` and nothing else moves.
ENCODE = [
    B + "print(base64.b64encode(bytes(range(%d))))" % n for n in range(0, 20)
] + [
    B + "print(base64.urlsafe_b64encode(bytes(range(%d))))" % n for n in range(0, 20)
] + [
    B + "print(base64.b64encode(b''))",
    B + "print(base64.urlsafe_b64encode(b''))",
    B + "print(base64.b64encode(b'hello world'))",
    B + "print(base64.urlsafe_b64encode(bytes([251, 255, 190])))",
    B + "print(base64.b64encode(bytes([251, 255, 190])))",
    B + "print(base64.b64encode(bytes([255] * 9)))",
    B + "print(base64.urlsafe_b64encode(bytes([255] * 9)))",
    B + "print(base64.b64encode(b'\\x00' * 7))",
    B + "print(base64.b64encode(bytes(range(250, 256))))",
    B + "print(base64.urlsafe_b64encode(bytes(range(250, 256))))",
]

#: Round trips, which is what the corpus actually types.
ROUNDTRIP = [
    B + "b = base64.b64encode(b'hello world')\n"
        "print(b.decode(), base64.b64decode(b).decode())",
    B + "r = bytes(range(256))\n"
        "print(base64.b64decode(base64.b64encode(r)) == r)",
    B + "r = bytes(range(256))\n"
        "print(base64.urlsafe_b64decode(base64.urlsafe_b64encode(r)) == r)",
    B + "for n in range(40):\n"
        "    r = bytes(range(n))\n"
        "    assert base64.b64decode(base64.b64encode(r)) == r\n"
        "print('ok')",
    B + "print(base64.b64decode(base64.b64encode(b'hi').decode()))",
]

#: The served keyword values — the defaults, spelled out, which a one-liner
#: does. Everything else is in `REFUSED`.
KEYWORDS = [
    B + "print(base64.b64encode(b'hi', altchars=None))",
    B + "print(base64.b64decode(b'aGk=', altchars=None))",
    B + "print(base64.b64decode(b'aGk=', validate=False))",
    B + "print(base64.b64decode(b'aGk=', validate=0))",
    B + "print(base64.b64decode(b'aGk=', validate=None))",
    B + "print(base64.b64decode(b'aGk=', altchars=None, validate=False))",
    B + "print(base64.b64decode(b'aGk=', **{'validate': False}))",
    B + "print(base64.b64encode(*[b'hi']))",
    # `altchars=None` is the ONLY served altchars, and it is served on both
    # standard functions and in either keyword order.
    B + "print(base64.b64decode(b'aGk=', validate=False, altchars=None))",
    B + "print(base64.b64decode(b'aGk=', **{'altchars': None}))",
    # every falsy `validate`, because that predicate is the one that stays
    B + "print(base64.b64decode(b'a!G k=', validate=0))",
    B + "print(base64.b64decode('aGk=', validate=None))",
]

#: **Hole 1, both directions.** One `falsy` predicate was answering two
#: different questions — "was the argument omitted" and "is the argument the
#: default" — and `altchars=0` and `altchars=False` are falsy without being
#: either. Measured against CPython 3.14.5 on 2026-09-06, one row per spelling:
#: `None` is the default and ANSWERS; `0` and `False` reach
#: `_bytes_from_decode_data` (or `len()`) and raise `TypeError`; `b""` and `""`
#: reach `assert len(altchars) == 2` and raise `AssertionError`. The engine
#: answered the middle two at exit 0, which is the outcome the whole refusal
#: contract exists to prevent — `validate=` keeps the falsy predicate because
#: its converter really does call `PyObject_IsTrue`.
#:
#: The pairs are `(program, does CPython answer it)`, so the same table drives
#: the "must refuse" assertion and the "CPython would have raised" one.
ALTCHARS = [
    (B + "print(base64.b64decode(b'aGk=', altchars=None))", True),
    (B + "print(base64.b64encode(b'hi', altchars=None))", True),
    (B + "print(base64.b64decode(b'aGk=', altchars=0))", False),
    (B + "print(base64.b64encode(b'hi', altchars=0))", False),
    (B + "print(base64.b64decode(b'aGk=', altchars=False))", False),
    (B + "print(base64.b64encode(b'hi', altchars=False))", False),
    (B + "print(base64.b64decode(b'aGk=', altchars=b''))", False),
    (B + "print(base64.b64encode(b'hi', altchars=b''))", False),
    (B + "print(base64.b64decode(b'aGk=', altchars=''))", False),
    (B + "print(base64.b64encode(b'hi', altchars=''))", False),
    (B + "print(base64.b64decode(b'aGk=', altchars=b'-_'))", True),
    (B + "print(base64.b64encode(b'hi', altchars=b'-_'))", True),
    # the same values through `**`, which the walk flattens
    (B + "print(base64.b64decode(b'aGk=', **{'altchars': 0}))", False),
    (B + "print(base64.b64encode(b'hi', **{'altchars': False}))", False),
    # and through an alias and a `from` import, where a name test goes wrong
    ("import base64 as b\nprint(b.b64decode(b'aGk=', altchars=0))", False),
    ("from base64 import b64encode as e\nprint(e(b'hi', altchars=False))", False),
]

#: Every spelling of the import that binds a served name, because the walk finds
#: the function by NAME and an alias is where a name test goes wrong.
SPELLINGS = [
    "import base64 as b\nprint(b.b64encode(b'hi'))",
    "import base64 as b\nprint(b.urlsafe_b64decode(b'-_--'))",
    "from base64 import b64encode\nprint(b64encode(b'hi'))",
    "from base64 import b64decode as d\nprint(d(b'aGk='))",
    "from base64 import b64encode, b64decode\nprint(b64decode(b64encode(b'hi')))",
    "from base64 import urlsafe_b64encode as u\nprint(u(bytes([251, 255, 190])))",
    B + "f = base64.b64encode\nprint(f(b'hi'))",
    B + "print(list(map(base64.b64encode, [b'a', b'b'])))",
    # a name that is not the module, in a program that imports it
    B + "base64 = 3\nprint(base64)",
    "base64 = 3\nprint(base64 + 1)",
    "def f(base64):\n    return base64\nprint(f(2))",
    # the import alone, which is two corpus programs
    "import base64, os.path, re, json\n",
    "import json, re, base64, os, sys\nprint(1)",
]

#: A literal bound to a NAME above the call, read out of the same binding table
#: `re.sub(P, …)` and `glob.glob(P)` read — in force AT the call, in source
#: order, and given up by any spelling that rebinds it.
BOUND = [
    B + "P = b'aGk='\nprint(base64.b64decode(P))",
    B + "P = 'aGk='\nprint(base64.b64decode(P))",
    B + "P = b'hi'\nprint(base64.b64encode(P))",
    B + "P = 'aGk='\nP = 'YQ=='\nprint(base64.b64decode(P))",
    B + "def f(P):\n    return P\nP = b'aGk='\nprint(base64.b64decode(P))",
    B + "print(base64.b64decode(f'aGk='))",
    B + "x = '='\nprint(base64.b64decode(f'aGk{x}'))",
]

#: **The variant check.** The result is a plain `bytes`, so it must survive
#: every path that materialises, compares, formats, hashes, indexes or mutates
#: a value. A capability that added a `Value` variant would answer one of these
#: wrongly at exit 0 or die at exit 1, and neither is visible from a row that
#: only prints.
RESULT_USES = [
    B + "print(base64.b64encode(b'hi') == b'aGk=')",
    B + "print(base64.b64encode(b'hi') != b'zz')",
    B + "print(base64.b64encode(b'hi') < b'z', base64.b64encode(b'hi') > b'A')",
    B + "print(len(base64.b64encode(b'hello')))",
    B + "print(base64.b64encode(b'hi')[0], base64.b64encode(b'hi')[-1])",
    B + "print(base64.b64encode(b'hello')[1:4])",
    B + "print(b'aGk' in base64.b64encode(b'hi'))",
    B + "print(bool(base64.b64encode(b'')), bool(base64.b64encode(b'x')))",
    B + "print([c for c in base64.b64encode(b'hi')])",
    B + "print(sorted([base64.b64encode(b'b'), base64.b64encode(b'a')]))",
    B + "print({base64.b64encode(b'hi'): 1})",
    B + "print(base64.b64encode(b'hi') in {b'aGk=': 1})",
    B + "print({base64.b64encode(b'hi'), b'aGk='})",
    B + "x = [base64.b64encode(b'hi')]\nx[0] = b'z'\nprint(x)",
    B + "x = [base64.b64encode(b'hi')]\ndel x[0]\nprint(x)",
    B + "x = [base64.b64encode(b'hi')]\nx += [b'z']\nprint(x)",
    B + "x = [b'a']\nx[0:1] = [base64.b64encode(b'hi')]\nprint(x)",
    B + "print('%s|%r' % (base64.b64encode(b'hi'), base64.b64encode(b'hi')))",
    B + "print(base64.b64encode(b'hi') + b'!')",
    B + "print(base64.b64encode(b'hi') * 2)",
    B + "print(base64.b64encode(b'hi').decode().upper())",
    B + "print(base64.b64encode(b'hi').split(b'G'))",
    B + "print(base64.b64encode(b'hi').startswith(b'aG'))",
    B + "import json\nprint(json.dumps(base64.b64encode(b'hi').decode()))",
    B + "print(str(base64.b64encode(b'hi')), repr(base64.b64decode(b'/w==')))",
    B + "print(list(base64.b64decode(b'/w==')))",
    B + "b = base64.b64encode(b'hi')\nb += b'!'\nprint(b)",
    B + "print(base64.b64decode(b'aGk=') == b'hi', base64.b64decode(b'aGk=') is None)",
]

#: The corpus, as rows. Every entry the mine of 2026-09-06 found whose first
#: blocker on `lypning` names `base64` and whose program the battery grades.
CORPUS = [
    B + "b = base64.b64encode(b'hello world')\n"
        "print(b.decode(), base64.b64decode(b).decode())",
    B + "print(base64.urlsafe_b64encode(bytes([251, 255, 190])).decode())",
    "import base64, os.path, re, json\n",
    "import json, re, base64, os, sys\nprint(1)",
]

GRID = DECODE + ENCODE + ROUNDTRIP + KEYWORDS + SPELLINGS + BOUND + RESULT_USES + CORPUS

#: What must REFUSE rather than answer, because CPython answers it with a
#: message this engine does not write, or with an alphabet it does not have.
#: Exit 90 and an EMPTY stdout, both.
REFUSED = [
    # `altchars=` is a second alphabet
    B + "print(base64.b64encode(b'\\xfb\\xff\\xbe', altchars=b'-_'))",
    B + "print(base64.b64decode(b'-_--', altchars=b'-_'))",
    B + "print(base64.b64encode(b'hi', b'-_'))",
    B + "print(base64.b64decode(b'aGk=', b'-_'))",
    # …and a PRESENT falsy `altchars` is not the default, it is a value
    # CPython rejects: `TypeError` for 0 and False, `AssertionError` for
    # b'' and ''. Answering these was hole 1.
    B + "print(base64.b64decode(b'aGk=', altchars=0))",
    B + "print(base64.b64decode(b'aGk=', altchars=False))",
    B + "print(base64.b64decode(b'aGk=', altchars=b''))",
    B + "print(base64.b64decode(b'aGk=', altchars=''))",
    B + "print(base64.b64encode(b'hi', altchars=0))",
    B + "print(base64.b64encode(b'hi', altchars=False))",
    B + "print(base64.b64encode(b'hi', altchars=b''))",
    B + "print(base64.b64encode(b'hi', altchars=''))",
    # `validate=True` selects strict mode, whose rejections are binascii's text
    B + "print(base64.b64decode(b'aGk=', validate=True))",
    B + "print(base64.b64decode(b'a!Gk=', validate=True))",
    B + "print(base64.b64decode(b'aGk=', validate=1))",
    B + "print(base64.b64decode(b'aGk=', **{'validate': True}))",
    # `validate=` and `altchars=` on the urlsafe pair are CPython TypeErrors
    B + "print(base64.urlsafe_b64decode(b'aGk=', validate=False))",
    B + "print(base64.urlsafe_b64encode(b'hi', altchars=None))",
    # every decode CPython raises a binascii.Error for
    B + "print(base64.b64decode(b'aGk'))",
    B + "print(base64.b64decode(b'a'))",
    B + "print(base64.b64decode(b'aGk=aGk='))",
    B + "print(base64.b64decode(b'aGkxx'))",
    B + "print(base64.b64decode('aGk'))",
    B + "print(base64.urlsafe_b64decode(b'_-'))",
    # a str handed to an encoder: CPython's TypeError
    B + "print(base64.b64encode('hi'))",
    B + "print(base64.urlsafe_b64encode('hi'))",
    B + "print(base64.b64encode(123))",
    B + "print(base64.b64decode(1.5))",
    # a non-ASCII str handed to a decoder: CPython's ValueError
    B + "print(base64.b64decode('\\u00e9'))",
    # the module's other half, which nothing on the spectrum serves
    B + "print(base64.b16encode(b'hi'))",
    B + "print(base64.b32encode(b'hi'))",
    B + "print(base64.b85encode(b'hi'))",
    B + "print(base64.a85encode(b'hi'))",
    B + "print(base64.encodebytes(b'hi'))",
    B + "print(base64.decodebytes(b'aGk=\\n'))",
    B + "print(base64.standard_b64encode(b'hi'))",
    B + "print(base64.standard_b64decode(b'aGk='))",
    "from base64 import b32encode\nprint(b32encode(b'hi'))",
    "import binascii\nprint(binascii.a2b_base64(b'aGk='))",
    # the argument count
    B + "print(base64.b64encode())",
    B + "print(base64.b64decode())",
    B + "print(base64.urlsafe_b64encode())",
    B + "print(base64.b64encode(b'hi', b'-_', True))",
    B + "print(base64.b64encode(b'hi', bogus=1))",
]

#: The conservatism, pinned rather than only described. The walk decides the
#: WHOLE program, so a call the RUN would never reach is decided too — and such
#: a program refuses where CPython answers. That is a coverage loss and never a
#: wrong answer: the chain hands it to CPython for one spawn. `glob_static_check`
#: has the identical property, and the alternative — deciding only what the run
#: reaches — is the runtime refusal past a committed barrier that
#: `AFTER_A_BARRIER` exists to rule out.
CONSERVATIVE = [
    B + "if False:\n    base64.b64decode(b'aGk')\nprint('hi')",
    B + "print(base64.b64decode(b'aGk=') if True else base64.b64decode(b'aGk'))",
    B + "def never():\n    return base64.b64encode('hi')\nprint('hi')",
    B + "for x in []:\n    base64.b64decode(b'a')\nprint('hi')",
]

#: The barrier every row of `AFTER_A_BARRIER` is asked through. `os.mkdir` is
#: the cheapest thing that commits it: the directory is created immediately, so
#: a refusal that lands afterwards is exit 1 with `NEWD` on disk, and one that
#: lands before it is exit 90 with the cwd untouched. The test reads the cwd,
#: not the message.
BARRIER = "import os\nos.mkdir('NEWD')\n"

#: One row per refusal SHAPE the source spells, each of which must be decided by
#: the walk. A row that reached `base64.rs` instead would be exit 1 here.
AFTER_A_BARRIER = [
    ("base64", "print(base64.b64decode(b'aGk'))"),
    ("base64", "print(base64.b64decode(b'a'))"),
    ("base64", "print(base64.b64decode(b'aGk=aGk='))"),
    ("base64", "print(base64.b64decode(b'aa='))"),
    ("base64", "print(base64.b64decode('aGk'))"),
    ("base64", "print(base64.urlsafe_b64decode(b'_-'))"),
    ("base64", "print(base64.b64decode('\\u00e9'))"),
    ("base64", "print(base64.b64encode('hi'))"),
    ("base64", "print(base64.urlsafe_b64encode('hi'))"),
    ("base64", "print(base64.b64encode(123))"),
    ("base64", "print(base64.b64encode(None))"),
    ("base64", "print(base64.b64encode([1]))"),
    ("base64", "print(base64.b64encode({'a': 1}))"),
    ("base64", "print(base64.b64decode(1.5))"),
    ("base64", "print(base64.b64decode(True))"),
    ("base64", "print(base64.b64encode(b'x', altchars=b'-_'))"),
    ("base64", "print(base64.b64decode(b'aGk=', altchars=0))"),
    ("base64", "print(base64.b64decode(b'aGk=', altchars=False))"),
    ("base64", "print(base64.b64decode(b'aGk=', altchars=b''))"),
    ("base64", "print(base64.b64decode(b'aGk=', altchars=''))"),
    ("base64", "print(base64.b64encode(b'hi', altchars=0))"),
    ("base64", "print(base64.b64encode(b'hi', altchars=False))"),
    ("base64", "print(base64.b64encode(b'hi', **{'altchars': 0}))"),
    ("base64", "print(base64.b64decode(b'aGk=', altchars=b'-_'))"),
    ("base64", "print(base64.b64decode(b'aGk=', validate=True))"),
    ("base64", "print(base64.b64decode(b'aGk=', validate=1))"),
    ("base64", "print(base64.urlsafe_b64decode(b'aGk=', validate=False))"),
    ("base64", "print(base64.urlsafe_b64encode(b'hi', altchars=None))"),
    ("base64", "print(base64.b64encode(b'hi', bogus=1))"),
    ("base64", "print(base64.b64encode())"),
    ("base64", "print(base64.b64decode())"),
    ("base64", "print(base64.b64encode(b'hi', b'-_'))"),
    ("base64", "print(base64.b64decode(b'aGk=', b'-_', True))"),
    # …and a literal bound to a NAME above the call
    ("base64", "P = 'aGk'\nprint(base64.b64decode(P))"),
    ("base64", "P = 5\nprint(base64.b64decode(P))"),
    ("base64", "P = 'hi'\nprint(base64.b64encode(P))"),
    # …and a CONSTANT f-string, which is a literal with a prefix on it
    ("base64", "print(base64.b64decode(f'aGk'))"),
    ("base64", "print(base64.b64decode(f'aG' 'k'))"),
    ("base64", "P = f'aGk'\nprint(base64.b64decode(P))"),
    # …and a `*`/`**` holding a DISPLAY, which the source spells out
    ("base64", "print(base64.b64decode(*[b'aGk']))"),
    ("base64", "print(base64.b64encode(*[b'hi', b'-_']))"),
    ("base64", "print(base64.b64encode(*[]))"),
    ("base64", "print(base64.b64decode(*(b'aGk',)))"),
    ("base64", "print(base64.b64decode(b'aGk=', **{'validate': True}))"),
    ("base64", "print(base64.b64encode(b'hi', **{'bogus': 1}))"),
    # the attributes nothing on the spectrum serves
    ("module-attr", "print(base64.b16encode(b'hi'))"),
    ("module-attr", "print(base64.b32encode(b'hi'))"),
    ("module-attr", "print(base64.b85encode(b'hi'))"),
    ("module-attr", "print(base64.a85encode(b'hi'))"),
    ("module-attr", "print(base64.encodebytes(b'hi'))"),
    ("module-attr", "print(base64.standard_b64encode(b'hi'))"),
    ("module-attr", "print(base64.nosuchthing)"),
    ("module-attr", "print(base64.MAXBINSIZE)"),
]

#: The same classes through every spelling of the import: the walk finds the
#: function by NAME, and an alias is where a name test goes wrong.
AFTER_A_BARRIER_ALIASED = [
    ("base64", "import base64 as b\nprint(b.b64decode(b'aGk'))"),
    ("base64", "import base64 as b\nprint(b.b64encode('hi'))"),
    ("base64", "from base64 import b64decode\nprint(b64decode(b'aGk'))"),
    ("base64", "from base64 import b64decode as d\nprint(d(b'aGk'))"),
    ("base64", "from base64 import b64encode as e\nprint(e('hi'))"),
    ("base64", "from base64 import urlsafe_b64decode as u\nprint(u(b'_-'))"),
    ("module-attr", "import base64 as b\nprint(b.b32encode(b'hi'))"),
    ("module-attr", "from base64 import b32encode\nprint(b32encode(b'hi'))"),
    ("module-attr", "from base64 import encodebytes as e\nprint(e(b'hi'))"),
]

#: What is deliberately NOT static, and the reason each one cannot be.
#:
#: One shape: an argument whose VALUE the source does not spell. A refusal here
#: still lands after the barrier — that is the cost of admitting the call at all
#: — but it is reachable only from a program whose argument the walk could not
#: read, which is a far narrower door than one a literal walks through.
#: **Landing after the barrier is not a base64 property**: `builtin: eval`,
#: `builtin: complex` and `set-order` behave identically with no base64 in the
#: program, which is issue #51 and not this capability's to close.
#:
#: The rows assert a REFUSAL and not a clean 90, because the backstop in
#: `base64::call` is the only thing standing behind them.
RUNTIME_BACKSTOP = [
    # a value built at runtime: the source has nothing to read
    "s = b'aG' + b'k'\nprint(base64.b64decode(s))",
    "s = ''.join(['a', 'G', 'k'])\nprint(base64.b64decode(s))",
    "print(base64.b64decode(b'aGk=aGk='[:7]))",
    "print(base64.b64encode('h' + 'i'))",
    "x = 'k'\nprint(base64.b64decode(f'aG{x}'))",
    # a `bytes` literal bound to a NAME: the core's binding table records the
    # TYPE and never the content, so there is nothing here for a walk to read
    "P = b'aGk'\nprint(base64.b64decode(P))",
    # an unpacked argument list whose value is a NAME rather than a display
    "a = [b'aGk']\nprint(base64.b64decode(*a))",
    "k = {'validate': True}\nprint(base64.b64decode(b'aGk=', **k))",
    "print(base64.b64decode(*list([b'aGk'])))",
    "print(base64.b64decode(b'aGk=', **dict(validate=True)))",
]


#: **Hole 2, and it is a ROUTE and not a run.** Serving `import base64` moved
#: the deciding blocker. The walk keeps the FIRST one, the import is it, and
#: `verdicts()` re-checks only the IMPORTS against a larger rung — so a blocker
#: recorded LATER was dropped, and `import base64` routed
#: `(255).to_bytes(2, 'big')` into lypning-l, which has no `int.to_bytes`
#: either and raises `AttributeError` at exit 1. That is the program's own exit,
#: which the chain never retries, and the same program refused cleanly at 90
#: before the module was served. `route::method_wide_stop` is the narrowing:
#: `method` is the one kind whose meaning differs between variants, so a
#: `method:` blocker stops the whole spectrum unless the program imports
#: something from `METHOD_BEARING`.
#:
#: Each row is asserted twice — it must ROUTE to CPython, and lypning-l must
#: still fail it, because a row lypning-l learned to answer would be a coverage
#: loss quietly asserted as a fix.
ROUTED_PAST_LYPNING_L = [
    B + "print(base64.b64encode((255).to_bytes(2, 'big')))",
    B + "print(int.from_bytes(base64.b64decode(b'AAAB'), 'big'))",
    B + "print(base64.b64encode(b'hi').nosuchmethod())",
    B + "x = 1.5\nprint(base64.b64encode(str(x.is_integer()).encode()))",
    B + "print(base64.b64encode(str((7).bit_length()).encode()))",
    "from base64 import b64encode\nprint(b64encode((255).to_bytes(2, 'big')))",
]

#: The other half of the same rule, and the reason this table is as long as the
#: one above: the narrowing is one line away from refusing every base64 program
#: in the corpus. It was, for one build — the walk had been recording
#: `method: .b64decode()` for the SERVED module attribute all along, harmlessly,
#: because nothing read a blocker after the first. These rows are what caught
#: it, and the last three are the `METHOD_BEARING` guard: a program that imports
#: `collections`, `pathlib` or `re` must stay optimistic, because only the
#: variant that HAS the capability knows whether the method is one of its own.
ROUTED_TO_LYPNING_L = [
    B + "print(base64.b64encode(b'hello world'))",
    B + "print(base64.b64decode(b'aGk='))",
    "import base64 as b\nprint(b.urlsafe_b64encode(bytes([251, 255, 190])))",
    "from base64 import b64decode\nprint(b64decode(b'aGk='))",
    "import json, re, base64, os, sys\nprint(1)",
    B + "import collections\nc = collections.Counter(base64.b64encode(b'hello'))\n"
        "print(c.most_common(1))",
    B + "import re\nprint(re.sub(r'=+$', '', base64.b64encode(b'hi').decode()))",
    B + "import glob\nprint([base64.b64encode(p.encode()) for p in sorted(glob.glob('*'))])",
]

#: The MicroPython trap, as four literal values rather than a diff against
#: CPython — because the whole point is that a reimplementation's answer is
#: *plausible*, and a table that only says "agree with CPython" reads the same
#: whether the rule was derived or guessed. `extmod/modbinascii.c` counts pads
#: PER QUAD and stops at the first complete one, which is wrong in BOTH
#: directions: it answers `b'hihi'` for `b"aGk=aGk="`, where CPython raises,
#: and it answers two bytes for `b"AA==AA=="`, where CPython gives three.
PER_QUAD = [
    # four data characters, two pad runs, THREE bytes out — never two
    (B + "print(base64.b64decode(b'AA==AA=='))", "b'\\x00\\x00\\x00'\n"),
    # the pad run is reset by a later alphabet byte
    (B + "print(base64.b64decode(b'aa=\\n='))", "b'i'\n"),
    # non-alphabet bytes are gone before anything is counted
    (B + "print(base64.b64decode(b'a!G k='))", "b'hi'\n"),
    # `-` and `_` are not in the STANDARD alphabet
    (B + "print(base64.b64decode(b'-_--'))", "b''\n"),
    # two pad runs mid-input, four data characters, three bytes out — the
    # per-quad decoder reads two padded quads here and answers TWO bytes
    (B + "print(base64.b64decode(b'AB==CD=='))", "b'\\x00\\x10\\x83'\n"),
    (B + "print(base64.b64decode(b'ABC=D'))", "b'\\x00\\x10\\x83'\n"),
    (B + "print(base64.b64decode(b'AAA=A'))", "b'\\x00\\x00\\x00'\n"),
]

#: …and the direction the per-quad decoder ANSWERS where CPython raises. Six
#: data characters and ONE trailing pad where a quad boundary needs two.
PER_QUAD_REFUSED = [
    B + "print(base64.b64decode(b'aGk=aGk='))",
    B + "print(base64.b64decode(b'aGk=aGk'))",
    B + "print(base64.b64decode(b'AA==A'))",
    B + "print(base64.b64decode(b'AB=C'))",
    B + "print(base64.b64decode(b'AAA=AB'))",
]


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


def _current(engine: str, cap: str) -> Path | None:
    """A built ``engine`` that carries THIS tree's capability table.

    An installed binary from before this capability landed answers every grid
    row with a refusal, which would turn the whole file into green skips
    measuring nothing. Skipping loudly is the honest failure.
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
        if table.get("self_caps") != list(engines.VARIANT_CAPS.get(engine, ())):
            continue
        if any(row.get("cap") == cap for row in table.get("caps", [])):
            return cand
    return None


BINARY = _current(engines.LYPNING_L, "cap-base64")

#: The binary that ROUTES is the cheapest one, and it is a different binary
#: from the one that answers — which is the whole of hole 2. `_current` accepts
#: it on the same terms: its own name, its own (empty) capability list, and a
#: `caps` table that has the row.
CORE = _current(engines.LYPNING, "cap-base64")

needs_l = pytest.mark.skipif(
    BINARY is None,
    reason="no lypning-l carrying cap-base64 is built (cargo build --release "
           "--no-default-features --features variant-l --target-dir target/variant-l)",
)


def _run(argv: list[str], program: str) -> subprocess.CompletedProcess:
    """One program, in a temp cwd of its own — invariant 4. Nothing here needs
    a tree, but the barrier rows WRITE, and a row that wrote into the checkout
    is the failure mode the net exists for."""
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
def test_the_base64_grid_agrees_with_cpython(program: str) -> None:
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
    """CPython answers each of these, or raises a message that is not this
    engine's to write — a `binascii.Error`, a `TypeError`, a `ValueError`. An
    answer here would be a wrong one at exit 0 or a wrong exit code, and the
    chain never retries either."""
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got)
    assert problem is None, (
        "this program must refuse, not answer: %s\n  program: %r\n  stderr: %r"
        % (problem, program, got.stderr.strip()[:200])
    )


def _barriered(call: str) -> str:
    """``call``, with the barrier committed before it and its imports before
    that. A row either brings its own import line or gets the plain one."""
    if call.startswith(("import ", "from ")):
        head, tail = call.split("\n", 1)
        return head + "\n" + BARRIER + tail
    return B + BARRIER + call


def _run_snapshot(program: str) -> tuple[subprocess.CompletedProcess, list[str], list[str]]:
    """One program, with the temp cwd listed before and after it ran."""
    with tempfile.TemporaryDirectory() as d:
        before = sorted(os.listdir(d))
        got = subprocess.run([str(BINARY), "-c", program], capture_output=True,
                             text=True, cwd=d, timeout=120)
        return got, before, sorted(os.listdir(d))


@needs_l
@pytest.mark.parametrize(
    "kind,call",
    AFTER_A_BARRIER + AFTER_A_BARRIER_ALIASED,
    ids=range(len(AFTER_A_BARRIER) + len(AFTER_A_BARRIER_ALIASED)),
)
def test_every_refusal_a_base64_call_can_raise_lands_before_the_barrier(
    kind: str, call: str,
) -> None:
    """The class, as one assertion per shape.

    Serving the module means the program RUNS here — and a refusal it reaches
    after `os.mkdir` has committed the write barrier is exit 1 with the
    directory on disk and nothing on stdout, which the chain never retries and
    the agent that typed the one-liner reads as a failure. The binary WITHOUT
    `cap-base64` refused that program cleanly at 90 and the chain got the answer
    from CPython, so the capability would have made the program worse.

    Four assertions, and the fourth is the one that cannot be faked: the cwd is
    listed before and after, so "no side effect" is measured rather than read
    out of the refusal line."""
    program = _barriered(call)
    got, before, after = _run_snapshot(program)
    problem = _refusal_problem(got)
    assert problem is None, (
        "a refusal reached after the barrier is exit 1, not 90: %s\n"
        "  program: %r\n  stderr: %r" % (problem, program, got.stderr.strip()[:200])
    )
    assert ": %s: " % kind in got.stderr, (
        "expected a %r refusal\n  program: %r\n  stderr: %r"
        % (kind, program, got.stderr.strip()[:200])
    )
    assert before == after, (
        "the refusal landed AFTER the barrier: cwd went from %r to %r\n  program: %r"
        % (before, after, program)
    )


@needs_l
@pytest.mark.parametrize("program", CONSERVATIVE, ids=range(len(CONSERVATIVE)))
def test_the_walk_decides_the_whole_program_even_where_the_run_would_not(
    program: str,
) -> None:
    """CPython answers every one of these; this engine refuses, on purpose.

    The refusal must still be CLEAN — exit 90, empty stdout — because that is
    what makes it a spawn rather than a failure. `print('hi')` running first
    and THEN refusing would be the shape the commit barrier exists to prevent,
    and it is what an implementation that checked at the call site would do."""
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got)
    assert problem is None, (
        "the conservative refusal must be clean: %s\n  program: %r\n  stderr: %r"
        % (problem, program, got.stderr.strip()[:200])
    )
    ref = _run([sys.executable], program)
    assert ref.returncode == 0, "this row is only interesting if CPython answers it"


@needs_l
@pytest.mark.parametrize("call", RUNTIME_BACKSTOP, ids=range(len(RUNTIME_BACKSTOP)))
def test_the_runtime_backstop_still_refuses_what_no_walk_could_hoist(call: str) -> None:
    """The residue, asserted to still REFUSE rather than answer.

    These reach `base64::call` because their argument is a value the source does
    not spell, so the refusal lands past the barrier — exit 1 rather than 90.
    That is issue #51 and not this capability's to close; what IS this
    capability's is that the refusal happens at all, because the alternative is
    a wrong answer at exit 0."""
    got = _run([str(BINARY)], B + call)
    assert got.returncode != 0, (
        "this program must not answer: %r\n  stdout: %r" % (call, got.stdout[:200])
    )
    assert "unsupported: base64: " in got.stderr, (
        "expected a base64 refusal\n  program: %r\n  stderr: %r" % (call, got.stderr[:200])
    )


@needs_l
@pytest.mark.parametrize("program,answered", ALTCHARS, ids=range(len(ALTCHARS)))
def test_an_absent_altchars_is_the_default_and_a_present_falsy_one_is_a_value(
    program: str, answered: bool,
) -> None:
    """Hole 1, as one assertion per spelling.

    `altchars=None` is the default because CPython's test is `if altchars is
    not None`, so it must ANSWER. Every other value must refuse — an
    alternative alphabet this engine does not implement, or one of the two
    raises CPython words itself. The `answered` column is what makes this a
    real check and not a tautology: for the rows CPython answers, this file
    also asserts the engine agrees byte for byte."""
    got = _run([str(BINARY)], program)
    if not answered:
        problem = _refusal_problem(got)
        assert problem is None, (
            "a present falsy altchars must refuse, not answer: %s\n"
            "  program: %r\n  stdout: %r" % (problem, program, got.stdout[:200])
        )
        ref = _run([sys.executable], program)
        assert ref.returncode != 0, (
            "this row is only interesting if CPython raises: %r" % program
        )
        return
    if got.returncode == engines.UNSUPPORTED_EXIT:
        assert _refusal_problem(got) is None
        pytest.skip("refused, which is allowed: %s" % got.stderr.strip()[:120])
    ref = _run([sys.executable], program)
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "altchars=None is the served default and must answer exactly\n"
        "  program: %r\n  lypning-l: %r exit %d\n  cpython:   %r exit %d"
        % (program, got.stdout, got.returncode, ref.stdout, ref.returncode)
    )


needs_core = pytest.mark.skipif(
    CORE is None or BINARY is None,
    reason="routing needs the CORE that routes and the lypning-l it routes to",
)


@needs_core
@pytest.mark.parametrize("program", ROUTED_PAST_LYPNING_L,
                         ids=range(len(ROUTED_PAST_LYPNING_L)))
def test_a_later_blocker_no_variant_answers_still_routes_to_cpython(program: str) -> None:
    """Hole 2. Two assertions, and the second is what keeps the first honest.

    The route must be CPython: the program's first blocker is `module: import
    base64`, which lypning-l answers, and its second is a method NOTHING on the
    spectrum has. And lypning-l must still fail the program — a row it quietly
    learned to answer would turn this test into a coverage loss asserted as a
    fix."""
    route = engines.route(program, binary=CORE)
    assert route.engine == engines.CPYTHON, (
        "routed to %r, which cannot run it\n  program: %r\n  first blocker: %s: %s"
        % (route.engine, program, route.kind, route.detail)
    )
    got = _run([str(BINARY)], program)
    assert got.returncode != 0, (
        "lypning-l answers this now, so the row no longer pins the narrowing: %r"
        % program
    )


@needs_core
@pytest.mark.parametrize("program", ROUTED_TO_LYPNING_L,
                         ids=range(len(ROUTED_TO_LYPNING_L)))
def test_the_narrowing_did_not_take_the_capability_with_it(program: str) -> None:
    """The other half, and the one that caught the narrowing's first build.

    The walk had always recorded `method: .b64decode()` for the SERVED module
    attribute — harmlessly, because nothing read a blocker after the first.
    Reading them sent every base64 program in the corpus to CPython, refused by
    the name of the very function the module was served to run. These rows are
    what said so."""
    route = engines.route(program, binary=CORE)
    assert route.engine == engines.LYPNING_L, (
        "routed to %r, not lypning-l\n  program: %r\n  blocker: %s: %s"
        % (route.engine, program, route.kind, route.detail)
    )


@needs_l
@pytest.mark.parametrize("program,want", PER_QUAD, ids=range(len(PER_QUAD)))
def test_the_decoder_counts_pads_over_the_input_and_never_per_quad(
    program: str, want: str,
) -> None:
    """The MicroPython trap, pinned as literal bytes.

    `extmod/modbinascii.c` counts pads PER QUAD and stops at the first complete
    one. `b"AA==AA=="` is four data characters and THREE bytes out; a per-quad
    decoder gives two. Asserted against the value rather than against CPython,
    because a table that only says "agree with CPython" reads the same whether
    the rule was derived or guessed."""
    got = _run([str(BINARY)], program)
    assert (got.returncode, got.stdout) == (0, want), (
        "program: %r\n  got: %r exit %d %s\n  want: %r"
        % (program, got.stdout, got.returncode, got.stderr.strip()[:160], want)
    )
    ref = _run([sys.executable], program)
    assert ref.stdout == want, "the reference moved: %r" % ref.stdout


@needs_l
@pytest.mark.parametrize("program", PER_QUAD_REFUSED, ids=range(len(PER_QUAD_REFUSED)))
def test_the_per_quad_decoders_other_direction_refuses_rather_than_answers(
    program: str,
) -> None:
    """`b"aGk=aGk="` is the six data characters `aGkaGk` with ONE trailing pad
    where a quad boundary needs two — a `binascii.Error`, which this engine
    does not word and therefore refuses. A per-quad decoder answers `b'hihi'`
    here, at exit 0, which is the wrong answer the chain cannot catch."""
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got)
    assert problem is None, (
        "must refuse, not answer: %s\n  program: %r\n  stdout: %r"
        % (problem, program, got.stdout[:200])
    )
    ref = _run([sys.executable], program)
    assert ref.returncode != 0, "this row is only interesting if CPython raises"


#: The sub-tables that must run COMPLETELY. `DECODE` is not among them and
#: cannot be: `PADDING` is half error shapes on purpose, and an input CPython
#: raises on is one this engine refuses — the same rule seen from the other
#: side, asserted in `REFUSED`. Every other table is programs CPython answers,
#: so a refusal in one is a row that quietly stopped measuring anything.
FULLY_SERVED = (ENCODE + ROUNDTRIP + KEYWORDS + SPELLINGS + BOUND + RESULT_USES
                + CORPUS + [p for p, _ in PER_QUAD])


@needs_l
def test_no_table_of_answers_quietly_became_a_table_of_refusals() -> None:
    """A file of green skips measures nothing, and `pytest.skip` is invisible in
    a summary line. Every row outside `DECODE` is a program CPython answers, so
    every one of them must RUN here; and `DECODE` must be at least half served,
    which is what its `PADDING` table's split of decodable to raising inputs
    makes it."""
    refused = [p for p in FULLY_SERVED
               if _run([str(BINARY)], p).returncode == engines.UNSUPPORTED_EXIT]
    assert refused == [], "%d rows CPython answers are refused here: %r" % (
        len(refused), refused[:4])
    ran = sum(1 for p in DECODE
              if _run([str(BINARY)], p).returncode != engines.UNSUPPORTED_EXIT)
    assert ran >= len(DECODE) // 2, "only %d of %d DECODE rows ran" % (ran, len(DECODE))
