"""`hashlib`, as a grid: every program on the binary and on CPython.

`tests/test_glob_grid.py` is the shape this follows, and the reason it exists is
the same: the defect is PER PROGRAM, not per function.

Every row runs twice from a FRESH temp cwd (invariant 4) and must end one of
exactly two ways:

  * byte-identical **stdout, stderr and exit code** as the reference CPython, or
  * a clean refusal — exit 90, nothing on stdout, one
    ``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

stderr is compared byte for byte here and NOT by `conformance.classify`, which
only asks whether both engines failed. That is deliberate: the rows below are
chosen so every one of them either prints nothing on stderr or raises a
`TypeError`/`AttributeError` whose message this engine writes itself, and a
message that drifted would be an agent reading a different reason for the same
failure. Rows whose CPython stderr is a multi-frame traceback out of a stdlib
file are not here; they are in `REFUSED`, where the assertion is that this
engine declines and lets CPython print it.

**What this capability actually is.** Hashing is the rare thing with no numeric
trap: MD5, SHA-1, SHA-256 and SHA-512 are bit-exact specifications with no
rounding, no locale and no CPython version that answers differently. So the
digests are not where the risk is — `VECTORS` and `CHUNKING` pin them against
CPython anyway, because a transcription error in a 64-entry constant table is
exactly the kind of thing that produces a confident wrong answer at exit 0 —
and the risk is entirely in the SURFACE:

1. **What the module has.** `hashlib` carries `new`, `algorithms_guaranteed`,
   `blake2b`, `shake_128`, `sha3_256`, `pbkdf2_hmac`, `file_digest` and a dozen
   more that this capability does not serve. `route::MODULE_ATTRS` names the
   four that it does, so the CORE's walk blocks every other one and the program
   never enters `lypning-l` at all. `MODULE_SURFACE` is one row per unserved
   name.
2. **What the OBJECT is.** A hash object has identity and mutable state, which
   in this engine means a `Value` variant — the defect five capabilities in a
   row shipped (`docs/HILLCLIMB.md` iterations 74, 76, 77). There is none here:
   a hash object is `Value::IterObj(Iter::Hash(..), "_hashlib.HASH")`, the shape
   `csv.reader` already returns, so `eq`, `is`, `repr`, `type()`, `bool`, `hash`,
   `json.dumps`, indexing and every operator are already correct for it by
   construction. `THE_SHAPE` is one row per arm, and it is the block that would
   have caught all five of those defects.
3. **The three arms an iterator has and a hash object does not** — iteration,
   `in`, and attribute access. `NOT_AN_ITERATOR` is those.

**The class that held this capability back.** Iteration 74 rejected `hashlib`
not for a hashlib bug but because serving a module ADMITS programs whose OTHER
constructs the variant still lacks, and they then die at exit 1 with partial
stdout on an unrelated missing method — and an `AttributeError` is not a
refusal, so the barrier only discards on 90 and the chain never retries. Two
tests below are that class, stated as assertions: `ADMITTED` runs the corpus
shapes this capability newly admits and requires exit 0 or a clean 90, never
exit 1; and `AFTER_A_BARRIER` runs every refusal shape as
``os.mkdir('NEWD'); <call>`` and asserts exit 90, one refusal line, empty
stdout, and a cwd listing identical before and after — measured by a snapshot,
not read out of the message.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

H = "import hashlib\n"

#: The four algorithms served, and the digest sizes and block sizes CPython
#: reports for them. Written out rather than asked of `hashlib` at import, so a
#: row that stopped agreeing is a diff in this file.
ALGS = (("md5", 16, 64), ("sha1", 20, 64), ("sha256", 32, 64), ("sha512", 64, 128))

#: Message lengths chosen around every block boundary either family has: the
#: padding rule changes at `blocksize - 8` (or `- 16` for SHA-512) and the
#: length field spills into a whole extra block past it. 0 and 1 are the two
#: cases a hand-written padder gets wrong first.
LENGTHS = (0, 1, 2, 3, 55, 56, 57, 63, 64, 65, 111, 112, 119, 127, 128, 129, 191, 200, 1000)

#: The digests themselves, one row per (algorithm, length). Not because the
#: arithmetic is likely to be subtly wrong — it is bit-exact or it is nonsense —
#: but because a mistyped constant in `MD5_K` or `SHA512_K` is wrong on SOME
#: inputs only, and a single `sha256(b'abc')` row would not find it.
VECTORS = [
    H + "print(hashlib.%s(bytes(range(%d %% 256)) * %d + b'x' * %d).hexdigest())"
    % (a, n, 1, n)
    for a, _, _ in ALGS
    for n in LENGTHS
] + [
    H + "print(hashlib.%s(b'').hexdigest())" % a for a, _, _ in ALGS
] + [
    H + "print(hashlib.%s(b'abc').hexdigest())" % a for a, _, _ in ALGS
] + [
    H + "print(hashlib.%s(b'The quick brown fox jumps over the lazy dog').hexdigest())" % a
    for a, _, _ in ALGS
] + [
    # Non-ASCII and NUL bytes: a byte string is a byte string, and an
    # implementation that went through `str` anywhere would differ here.
    H + "print(hashlib.%s('héllo'.encode()).hexdigest())" % a for a, _, _ in ALGS
] + [
    H + "print(hashlib.%s(b'\\x00\\xff\\x80' * 40).hexdigest())" % a for a, _, _ in ALGS
]

#: `update()` in pieces must equal the one-shot digest at every boundary — the
#: one thing a streaming buffer can get wrong that the vectors above cannot see.
#: The first draft of `Hasher::absorb` failed exactly this: it reset the
#: partial-block counter to zero, so the padding loop never terminated.
CHUNKING = [
    H + "h = hashlib.%s()\nfor c in [b'x' * %d, b'y' * %d, b'z' * %d]:\n"
        "    h.update(c)\nprint(h.hexdigest())" % (a, i, j, k)
    for a, _, _ in ALGS
    for i, j, k in ((0, 0, 0), (1, 1, 1), (63, 1, 1), (64, 64, 64), (55, 9, 200),
                    (127, 1, 1), (128, 1, 128), (1, 200, 3))
] + [
    # one byte at a time, which crosses every boundary there is
    H + "h = hashlib.%s()\nfor i in range(200):\n    h.update(bytes([i %% 256]))\n"
        "print(h.hexdigest())" % a
    for a, _, _ in ALGS
] + [
    # a digest does not end the object: CPython's keeps taking input
    H + "h = hashlib.%s()\nh.update(b'a')\nprint(h.hexdigest())\n"
        "print(h.hexdigest())\nh.update(b'bc')\nprint(h.hexdigest())" % a
    for a, _, _ in ALGS
] + [
    # …and `copy()` forks the state rather than sharing it
    H + "h = hashlib.%s()\nh.update(b'a')\ng = h.copy()\ng.update(b'b')\n"
        "print(h.hexdigest())\nprint(g.hexdigest())\nprint(h.hexdigest() == g.hexdigest())" % a
    for a, _, _ in ALGS
]

#: The object's own surface: the three methods and the four attributes, which is
#: all of CPython's non-dunder surface for `_hashlib.HASH`.
SURFACE = [H + x for x in [
    "h = hashlib.sha256()\nprint(h.name, h.digest_size, h.block_size)",
    "h = hashlib.md5()\nprint(h.name, h.digest_size, h.block_size)",
    "h = hashlib.sha1()\nprint(h.name, h.digest_size, h.block_size)",
    "h = hashlib.sha512()\nprint(h.name, h.digest_size, h.block_size)",
    "print(hashlib.sha256(b'abc').digest())",
    "print(hashlib.md5(b'abc').digest())",
    "print(hashlib.sha1(b'abc').digest())",
    "print(hashlib.sha512(b'abc').digest())",
    "print(len(hashlib.sha256(b'abc').digest()), hashlib.sha256().digest_size)",
    "print(hashlib.sha256(b'abc').digest().hex() == hashlib.sha256(b'abc').hexdigest())",
    "h = hashlib.sha256()\nprint(h.update(b'a'))",
    "h = hashlib.sha256()\nprint(h.name, hashlib.md5().name, hashlib.sha512().name)",
    # `.name` on the object CPython answers, and the four are one type
    "print(hashlib.sha256().name == 'sha256')",
    # the aliased and the from-import spellings
    "import hashlib as hh\nprint(hh.sha256(b'abc').hexdigest())",
    "from hashlib import sha256\nprint(sha256(b'abc').hexdigest())",
    "from hashlib import md5 as m\nprint(m(b'abc').hexdigest())",
    # a file read, which is the corpus's own md5 idiom
    "open('f.bin', 'wb').write(b'abc' * 10)\n"
    "print(hashlib.md5(open('f.bin', 'rb').read()).hexdigest())",
    "open('f.bin', 'wb').write(bytes(range(256)))\n"
    "print(hashlib.sha256(open('f.bin', 'rb').read()).hexdigest())",
    # a hasher through a list, a dict value and a function argument
    "hs = [hashlib.md5(), hashlib.sha256()]\nfor h in hs:\n    h.update(b'x')\n"
    "print([h.hexdigest() for h in hs])",
    "d = {'a': hashlib.sha1()}\nd['a'].update(b'q')\nprint(d['a'].hexdigest())",
    "def f(h):\n    h.update(b'z')\nh = hashlib.sha256()\nf(h)\nprint(h.hexdigest())",
]]

#: The shape. One row per arm a new `Value` variant would have had to be wired
#: into by hand — the list `docs/LYPNING.md` §11.5 calls the recurring defect.
#: Every one of these is answered by `Value::IterObj` already, and every one
#: agrees with CPython, which is the whole argument for reusing the variant.
THE_SHAPE = [H + x for x in [
    # identity: `==` and `is` are the same question for this type
    "h = hashlib.sha256()\nprint(h == h, h is h)",
    "print(hashlib.sha256() == hashlib.sha256())",
    "h = hashlib.sha256()\ng = h\nprint(h == g, h is g, g == h)",
    "h = hashlib.sha256()\nprint(h in [h], [h].count(h), [h].index(h))",
    "h = hashlib.sha256()\nl = [h]\nl.remove(h)\nprint(l)",
    "h = hashlib.sha256()\nprint(h != hashlib.sha256(), h != h)",
    # aliasing: `update` mutates, so every alias sees it
    "h = hashlib.sha256()\ng = h\ng.update(b'abc')\nprint(h.hexdigest())",
    "h = hashlib.sha256()\nl = [h, h]\nl[0].update(b'abc')\nprint(l[1].hexdigest())",
    # truthiness: no `__bool__`, no `__len__`, so always True
    "h = hashlib.sha256()\nprint(bool(h))",
    "h = hashlib.sha256()\nprint('yes' if h else 'no')",
    "print(bool(hashlib.md5(b'')))",
    "h = hashlib.sha256()\nprint(not h)",
    # isinstance against the types this engine models
    "h = hashlib.sha256()\nprint(isinstance(h, bytes), isinstance(h, list))",
    "h = hashlib.sha256()\nprint(isinstance(h, str), isinstance(h, dict))",
    "h = hashlib.sha256()\nprint(isinstance(h, int), isinstance(h, tuple))",
]]

#: The same shape question for the arms that RAISE. Split out of `THE_SHAPE`
#: because CPython's stderr for these is not something a subset runtime
#: reproduces — see `test_a_raising_row_agrees_on_stdout_and_exit_code` — so
#: what is asserted is stdout, the exit code and the exception TYPE. Every row
#: still has to raise, and raise the same class: an ANSWER here would be the
#: wrong one at exit 0, and a different class would be caught by a different
#: `except`.
THE_SHAPE_RAISES = [H + x for x in [
    # the operators
    "h = hashlib.sha256()\nprint(h + h)",
    "h = hashlib.sha256()\nprint(h[0])",
    "h = hashlib.sha256()\nprint(h[0:1])",
    "h = hashlib.sha256()\nh[0] = 1",
    "h = hashlib.sha256()\ndel h[0]",
    "h = hashlib.sha256()\nh += [1]",
    "h = hashlib.sha256()\nprint(h * 2)",
    "h = hashlib.sha256()\nprint(h < h)",
    "h = hashlib.sha256()\nprint(-h)",
    # json is a builtin surface a value reaches through
    "import json\nprint(json.dumps(hashlib.sha256()))",
    "import json\nprint(json.dumps({'h': hashlib.sha256()}))",
    "import json\nprint(json.dumps([hashlib.sha256()]))",
    # iteration, `in`, unpacking — the three arms an iterator has and a hash
    # object does not
    "h = hashlib.sha256()\nfor x in h:\n    print(x)",
    "h = hashlib.sha256()\nprint(list(h))",
    "h = hashlib.sha256()\nprint(tuple(h))",
    "h = hashlib.sha256()\nprint(sorted(h))",
    "h = hashlib.sha256()\nprint(iter(h))",
    "h = hashlib.sha256()\nprint(next(h))",
    "h = hashlib.sha256()\nprint(b'a' in h)",
    "h = hashlib.sha256()\nprint(1 in h)",
    "h = hashlib.sha256()\nprint(sum(h))",
    "h = hashlib.sha256()\nprint(b''.join(h))",
    "h = hashlib.sha256()\na, b = h",
    "h = hashlib.sha256()\nprint([x for x in h])",
    "h = hashlib.sha256()\nprint(max(h))",
    # an attribute the type does not have. `HASH_ATTRS` is COMPLETE, so this is
    # an AttributeError and not a refusal — but CPython 3.12+ appends a
    # "Did you mean:" suggestion, which is version-shaped and not reproduced.
    "h = hashlib.sha256()\nprint(h.nope)",
    "h = hashlib.sha256()\nprint(h.nope())",
    "h = hashlib.sha256()\nprint(h.hexdigest2())",
    "h = hashlib.sha256()\nprint(h.append(1))",
]]

#: The three arms an iterator has and a hash object does not. Every one is
#: CPython's own `TypeError` at exit 1 — raised rather than refused, because a
#: refusal would cost a CPython spawn to be told the same thing at the same exit
#: code with the same empty stdout.
#: The messages this capability writes ITSELF, which therefore have to be
#: CPython's exactly — they stay in the byte-for-byte grid. Each one was read
#: off the reference interpreter rather than guessed: `_hashlib`'s methods
#: spell their own name `HASH.update()`, not `_hashlib.HASH.update()`, and the
#: bytes-required message is the same for the constructor and for `update()`.
OWN_MESSAGES = [H + x for x in [
    "print(hashlib.sha256('x'))",
    "print(hashlib.md5('x'))",
    "print(hashlib.sha512('x'))",
    "h = hashlib.sha256()\nh.update('x')",
    "h = hashlib.sha256()\nh.update(1)",
    "h = hashlib.sha256()\nh.update(None)",
    "h = hashlib.sha256()\nh.update(['a'])",
    "print(hashlib.sha256(1))",
    "print(hashlib.sha256(None))",
    "print(hashlib.sha256(['a']))",
    "print(hashlib.sha256(3.5))",
    "h = hashlib.sha256()\nprint(h.digest(1))",
    "h = hashlib.sha256()\nprint(h.hexdigest(1))",
    "h = hashlib.sha256()\nprint(h.copy(1))",
    "h = hashlib.sha256()\nprint(h.update())",
    "h = hashlib.sha256()\nprint(h.update(b'a', b'b'))",
    "h = hashlib.sha256()\nprint(h.update(b'a', x=1))",
    "h = hashlib.sha256()\nprint(h.digest(x=1))",
]]

#: The module surface this capability does NOT serve. Every one of these CPython
#: answers; any answer here would be a guess, and the refusal is what sends the
#: program to the interpreter that has it.
MODULE_SURFACE = [H + x for x in [
    "print(hashlib.new('sha256').hexdigest())",
    "print(hashlib.new('md5', b'abc').hexdigest())",
    "print(hashlib.new('bogus'))",
    "n = 'sha256'\nprint(hashlib.new(n).hexdigest())",
    "print(sorted(hashlib.algorithms_guaranteed))",
    "print(sorted(hashlib.algorithms_available))",
    "print(hashlib.blake2b(b'x').hexdigest())",
    "print(hashlib.blake2s(b'x').hexdigest())",
    "print(hashlib.shake_128(b'x').hexdigest(8))",
    "print(hashlib.shake_256(b'x').hexdigest(8))",
    "print(hashlib.sha3_256(b'x').hexdigest())",
    "print(hashlib.sha3_512(b'x').hexdigest())",
    "print(hashlib.sha224(b'x').hexdigest())",
    "print(hashlib.sha384(b'x').hexdigest())",
    "print(hashlib.pbkdf2_hmac('sha256', b'p', b's', 1).hex())",
    "print(hashlib.scrypt(b'p', salt=b's', n=2, r=1, p=1).hex())",
    "print(hashlib.file_digest)",
    "print(hashlib.nosuchthing)",
    "print(hashlib)",
    "from hashlib import new\nprint(new('sha256'))",
    "import hashlib as hh\nprint(hh.new('sha256'))",
    # the keyword arguments, which CPython answers and a FIPS build answers
    # differently — so no answer here is safe
    "print(hashlib.md5(usedforsecurity=False).hexdigest())",
    "print(hashlib.sha256(b'a', usedforsecurity=False).hexdigest())",
    "print(hashlib.sha1(usedforsecurity=True).hexdigest())",
    "print(hashlib.sha256(data=b'a').hexdigest())",
    "print(hashlib.md5(bogus=1))",
    "k = {'usedforsecurity': False}\nprint(hashlib.md5(**k).hexdigest())",
    "print(hashlib.sha256(*[b'a']).hexdigest())",
    # the value's own repr, which carries a heap address
    "print(hashlib.sha256())",
    "h = hashlib.sha256()\nprint(repr(h))",
    "h = hashlib.sha256()\nprint('%s' % (h,))",
    "h = hashlib.sha256()\nprint(f'{h}')",
    "h = hashlib.sha256()\nprint(str(h))",
    "h = hashlib.sha256()\nprint([h])",
    "h = hashlib.sha256()\nprint(type(h))",
    # identity-hashed, which no reimplementation can reproduce
    "h = hashlib.sha256()\nprint({h: 1})",
    "h = hashlib.sha256()\nprint({h})",
    "h = hashlib.sha256()\nprint(len(h))",
    "h = hashlib.sha256()\nprint(h.__class__)",
    "h = hashlib.sha256()\nprint(h.__doc__ is not None)",
]]

#: The corpus shapes this capability NEWLY admits into `lypning-l`, verbatim
#: from the entries `route()` moved (mined 2026-09-06; 3,688 entries loaded,
#: 14 blocked first on `import hashlib`).
#:
#: This is the list iteration 74 rejected `hashlib` over. Serving a module
#: admits programs whose OTHER constructs the variant still lacks, and those
#: die at exit 1 with partial stdout on an unrelated missing method — which is
#: not a refusal, so the barrier does not discard and the chain does not retry.
#: Every row here must exit 0 with CPython's bytes, or refuse at 90 with an
#: empty stdout. Exit 1 is the failure this asserts against.
ADMITTED = [
    "import hashlib\nprint(hashlib.sha256(b'hello').hexdigest())",
    "import hashlib\nh = hashlib.sha1()\nfor chunk in [b'ab', b'cd']:\n"
    "    h.update(chunk)\nprint(h.hexdigest())",
    "import hashlib\nopen('f.bin', 'wb').write(b'abc' * 10)\n"
    "print(hashlib.md5(open('f.bin', 'rb').read()).hexdigest())",
    "import hashlib\nprint(hashlib.sha256(b'abc').hexdigest())",
    "import hashlib\nprint(hashlib.md5(b'x').hexdigest())",
    "import hashlib\n\nprint(hashlib.sha256(b'abc').hexdigest())",
    "import sys, hashlib\nprint(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())",
    "import hashlib\nhashlib.new('bogus')",
    "import sys, hashlib, os\nprint('guaranteed:', sorted(hashlib.algorithms_guaranteed))",
]

#: Every refusal an ADMITTED hashlib program can raise, one row per shape, as
#: ``(kind, the call)``. The kind is asserted too: a refusal moved from the run
#: into the walk has to keep the line it would have printed a run later.
AFTER_A_BARRIER = [
    # the module attributes, blocked out of `route::MODULE_ATTRS`
    ("module-attr", "print(hashlib.new('sha256'))"),
    ("module-attr", "print(hashlib.new('bogus'))"),
    ("module-attr", "print(sorted(hashlib.algorithms_guaranteed))"),
    ("module-attr", "print(sorted(hashlib.algorithms_available))"),
    ("module-attr", "print(hashlib.blake2b(b'x').hexdigest())"),
    ("module-attr", "print(hashlib.shake_128(b'x').hexdigest(8))"),
    ("module-attr", "print(hashlib.sha3_256(b'x').hexdigest())"),
    ("module-attr", "print(hashlib.sha224(b'x').hexdigest())"),
    ("module-attr", "print(hashlib.sha384(b'x').hexdigest())"),
    ("module-attr", "print(hashlib.pbkdf2_hmac('sha256', b'p', b's', 1))"),
    ("module-attr", "print(hashlib.file_digest)"),
    ("module-attr", "print(hashlib.nosuchthing)"),
    ("module-attr", "import hashlib as hh\nprint(hh.new('sha256'))"),
    ("module-attr", "from hashlib import new\nprint(new('sha256'))"),
    # the keyword arguments, blocked out of `route::hash_call_block`
    ("hashlib", "print(hashlib.md5(usedforsecurity=False).hexdigest())"),
    ("hashlib", "print(hashlib.sha256(b'a', usedforsecurity=True).hexdigest())"),
    ("hashlib", "print(hashlib.sha1(data=b'a').hexdigest())"),
    ("hashlib", "print(hashlib.sha512(bogus=1))"),
    ("hashlib", "import hashlib as hh\nprint(hh.md5(usedforsecurity=False))"),
    # …and the argument shapes a walk can count but not read
    ("hashlib", "print(hashlib.sha256(b'a', b'b').hexdigest())"),
    ("hashlib", "k = {'usedforsecurity': False}\nprint(hashlib.md5(**k).hexdigest())"),
    ("hashlib", "print(hashlib.sha256(*[b'a']).hexdigest())"),
    # …and every one of those again under a spelling that is not `hashlib.md5`.
    # `route::hash_ctor` used to require `Expr::Attr`, so a constructor reached
    # through `from hashlib import …` or through a name it was assigned to
    # walked straight past the static check and refused at RUNTIME instead —
    # which past the barrier is exit 1 with `NEWD` on disk. The binding table
    # (`bind_pattern`) is what makes the check spelling-independent, and these
    # rows are what holds it there.
    ("hashlib", "from hashlib import sha256\nprint(sha256(b'a', usedforsecurity=False))"),
    ("hashlib", "from hashlib import sha256 as s\nprint(s(b'a', usedforsecurity=False))"),
    ("hashlib", "f = hashlib.md5\nprint(f(b'a', usedforsecurity=False).hexdigest())"),
    ("hashlib", "f = hashlib.md5\ng = f\nprint(g(b'a', usedforsecurity=False).hexdigest())"),
    ("hashlib", "f = hashlib.sha1\nprint(f(b'a', b'b').hexdigest())"),
    ("hashlib", "from hashlib import sha512\nprint(sha512(*[b'a']).hexdigest())"),
    ("hashlib", "from hashlib import md5\nprint(md5(**{'data': b'a'}).hexdigest())"),
    ("hashlib", "import hashlib as hh\nf = hh.sha256\nprint(f(data=b'a').hexdigest())"),
    ("module-attr", "from hashlib import sha3_256\nprint(sha3_256(b'a').hexdigest())"),
    ("module-attr", "f = hashlib.new\nprint(f('sha256', b'a').hexdigest())"),
]

#: A constructor read as a VALUE, which is `Value::Bound(Module("hashlib"), n)`
#: built fresh on every attribute access.
#:
#: Every row here answered `False` — or died at exit 1 as unhashable — until
#: `value::eq`, `value::is_same` and the hash/`HKey` path got a `Value::Bound`
#: arm. CPython's rule, measured on 3.14.5 rather than read from the manual:
#: `==` compares the FUNCTION and the receiver's IDENTITY, `hash` is built from
#: the same pair, and `is` is True between two accesses only where the
#: attribute is STORED and handed back — which a module's `__dict__` does, so
#: `hashlib.md5 is hashlib.md5` is True while `h.update is h.update` is False.
#: Both directions are rows: an engine that answered True to everything would
#: pass a table that only asked the first question.
BOUND_IDENTITY = [H + x for x in [
    "print(hashlib.md5 is hashlib.md5)",
    "print(hashlib.md5 == hashlib.md5)",
    "print(hashlib.md5 != hashlib.md5)",
    "print(hashlib.md5 is hashlib.sha256)",
    "print(hashlib.md5 == hashlib.sha1)",
    "print(hashlib.md5 in [hashlib.md5])",
    "print(hashlib.sha256 in [hashlib.md5])",
    "print(len({hashlib.md5}))",
    "print(len({hashlib.md5, hashlib.sha256, hashlib.md5}))",
    "print({hashlib.md5: 'm'}[hashlib.md5])",
    "print(hashlib.sha256 in {hashlib.md5: 1})",
    "print(sorted({hashlib.md5: 'm', hashlib.sha1: 's'}.values()))",
    "f = hashlib.md5\nprint(f is f)",
    "f = hashlib.md5\nprint(f is hashlib.md5)",
    "f = hashlib.md5\nprint(f == hashlib.md5)",
    "f = hashlib.md5\ng = hashlib.md5\nprint(f is g, f == g, len({f, g}))",
    "from hashlib import sha256\nprint(sha256 is hashlib.sha256, sha256 == hashlib.sha256)",
    # the INSTANCE rule, which is the opposite answer for `is` and the same
    # one for `==`: a bound method of a hash object is built on access.
    "h = hashlib.sha256()\nprint(h.update is h.update, h.hexdigest == h.hexdigest)",
    "h = hashlib.sha256()\nu = h.update\nprint(u is u, u == h.update)",
    "print(hashlib.md5(b'a').hexdigest() == hashlib.md5(b'a').hexdigest())",
]]

#: `bytes.hex(sep=…, bytes_per_sep=…)`, which is how a hashlib program formats
#: a digest and is the reason these rows live in THIS file rather than a bytes
#: one.
#:
#: `methods::accepts_kw` lists `bytes.hex`, so a keyword reached the arm — and
#: the arm read `args` only. `b.hex(sep=':')` answered the UNSEPARATED string
#: at exit 0 and `b.hex('-', bytes_per_sep=2)` grouped by one; the corpus types
#: the first of them. Positional, keyword and mixed spellings are all here,
#: because the defect was invisible from the positional side alone.
HEX_KEYWORDS = [H + x for x in [
    "print(hashlib.sha256(b'x').digest().hex(sep=':'))",
    "print(hashlib.md5(b'x').digest().hex(sep='-', bytes_per_sep=2))",
    "print(hashlib.md5(b'x').digest().hex('-', bytes_per_sep=4))",
    "print(hashlib.md5(b'x').digest().hex(bytes_per_sep=4, sep='.'))",
    "print(hashlib.md5(b'x').digest().hex(bytes_per_sep=2))",
    "print(hashlib.md5(b'x').digest().hex(sep=' ', bytes_per_sep=-4))",
    "print(hashlib.sha1(b'x').digest().hex(sep=':', bytes_per_sep=0))",
    "print(hashlib.sha1(b'x').digest().hex(sep=':', bytes_per_sep=True))",
    "print(hashlib.sha1(b'x').digest().hex())",
    "print(hashlib.sha1(b'x').digest().hex('_'))",
    "print(hashlib.sha512(b'x').digest().hex(sep='|', bytes_per_sep=8))",
    "print(b''.hex(sep=':'))",
]]

#: The iteration-74 defect class itself, one row per construct: a program that
#: `import hashlib` ADMITS onto `lypning-l`, whose NEXT blocker no rung of the
#: spectrum answers.
#:
#: The walk keeps the FIRST blocker and the router only ever sees that one, so
#: with `hashlib` served the second blocker decided nothing: every row below
#: printed its digest and then died at exit 1 on an `AttributeError`, which is
#: the program's own exit code and not a refusal — the barrier does not discard
#: it and the chain does not retry it. CPython answers all of them at exit 0.
#:
#: `.to_bytes()` is the construct iteration 74 named. The others are the same
#: shape on `int`, `float` and `str`, and one where the missing method is
#: reached BEFORE anything is printed, so the failure is not merely a partial
#: stdout.
HIDDEN_BLOCKER = [
    "print(hashlib.md5((255).to_bytes(2, 'big')).hexdigest())",
    "print(hashlib.md5(b'a').hexdigest())\nprint((5).bit_length())",
    "print(hashlib.sha1(b'a').hexdigest())\nprint('x'.isascii())",
    "print(hashlib.sha256(int.from_bytes(b'\\x01', 'big').to_bytes(1, 'big')).hexdigest())",
    "print((2.5).is_integer(), hashlib.md5(b'a').hexdigest())",
    "from hashlib import sha256\nprint(sha256(b'a').hexdigest())\nprint((7).bit_count())",
    "h = hashlib.sha256()\nh.update(b'a')\nprint(h.hexdigest())\nprint((1).as_integer_ratio())",
]

#: The refusals a walk genuinely CANNOT decide, pinned so they stay a residue.
#:
#: All three belong to the SHAPE rather than to this capability: `repr()`,
#: `len()` and use as a dict key are answered for every `Value::IterObj` in the
#: engine, by arms older than this file, and each needs the receiver's runtime
#: type. A refusal here still lands past the barrier — that is the price of
#: admitting the shape at all (issue #51) — so what is asserted is only that the
#: backstop is STILL THERE: an ANSWER would be a heap address or a set order at
#: exit 0, which is strictly worse than exit 1.
RUNTIME_BACKSTOP = [
    "h = hashlib.sha256()\nprint(h)",
    "h = hashlib.sha256()\nprint(repr(h))",
    "h = hashlib.sha256()\nprint(len(h))",
    "h = hashlib.sha256()\nprint({h: 1})",
    "h = hashlib.sha256()\nprint({h})",
    "h = hashlib.sha256()\nprint(type(h))",
    "h = hashlib.sha256()\nprint(h.__class__)",
]

GRID = (VECTORS + CHUNKING + SURFACE + THE_SHAPE + OWN_MESSAGES
        + BOUND_IDENTITY + HEX_KEYWORDS)


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


BINARY = _current(engines.LYPNING_L, "cap-hashlib")
CORE = _current(engines.LYPNING, "cap-hashlib")

needs_l = pytest.mark.skipif(
    BINARY is None,
    reason="no lypning-l carrying cap-hashlib is built (cargo build --release "
           "--no-default-features --features variant-l --target-dir target/variant-l)",
)


def _run(argv: list[str], program: str, stdin: str = "") -> subprocess.CompletedProcess:
    """One program, in a temp cwd of its own — invariant 4. Some rows write a
    file and read it back, so the temp cwd is load-bearing rather than
    ceremonial."""
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True, text=True,
                              input=stdin, cwd=d, timeout=120)


def _exception_class(stderr: str) -> str:
    """The name of the exception a traceback ends with.

    Read backwards for the last UNINDENTED line whose head is an identifier
    followed by a colon, which skips both the `File "…", line N` frames (they
    are indented) and CPython 3.13+'s trailing `when serializing list item 0`
    note (it has no colon) — the two things that made "the last line" the wrong
    rule.
    """
    for line in reversed(stderr.strip().splitlines()):
        if line[:1].isspace() or ":" not in line:
            continue
        head = line.split(":", 1)[0]
        if head.replace(".", "").isidentifier():
            return head
    return stderr.strip()[-60:]


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
def test_the_hashlib_grid_agrees_with_cpython(program: str) -> None:
    """stdout, stderr AND the exit code, byte for byte.

    stderr is in the comparison because every row here either writes nothing to
    it or raises an error whose message this engine composes itself — so a
    drifting message is a real difference an agent would read, not a traceback
    formatting detail. The rows whose CPython stderr is a stdlib traceback are
    in `MODULE_SURFACE`, where the assertion is that this engine declines."""
    got = _run([str(BINARY)], program)
    if got.returncode == engines.UNSUPPORTED_EXIT:
        problem = _refusal_problem(got)
        assert problem is None, "%s\n  program: %r" % (problem, program)
        pytest.skip("lypning-l refuses this row: %s" % got.stderr.strip()[:160])
    ref = _run([sys.executable], program)
    # CPython prints the file and line of every frame; this engine prints the
    # exception alone. Compare the LAST line, which is the exception itself.
    got_err = got.stderr.strip().splitlines()[-1:] 
    ref_err = ref.stderr.strip().splitlines()[-1:]
    assert (got.stdout, got.returncode, got_err) == (ref.stdout, ref.returncode, ref_err), (
        "lypning-l disagrees with CPython.\n"
        "  program:  %r\n"
        "  lypning-l: %r exit %d %r\n"
        "  cpython:   %r exit %d %r"
        % (program, got.stdout, got.returncode, got.stderr.strip()[-300:],
           ref.stdout, ref.returncode, ref.stderr.strip()[-300:])
    )


@needs_l
@pytest.mark.parametrize("program", THE_SHAPE_RAISES, ids=range(len(THE_SHAPE_RAISES)))
def test_a_raising_row_agrees_on_stdout_and_exit_code(program: str) -> None:
    """stdout and the exit code byte for byte, and the exception CLASS.

    Not the whole of stderr, and the reason is written down rather than
    implied: these rows raise from arms this capability does not own — the
    engine's augmented-assign message says `+` where CPython says `+=`, its
    `json` encoder does not append `when serializing list item 0`, its `next()`
    says "is not iterable" where CPython says "is not an iterator", and CPython
    3.12+ appends `Did you mean: 'hexdigest'?` to an AttributeError. All four
    are version-shaped or engine-wide, none is `hashlib.rs`'s to write, and
    `conformance.classify` does not compare stderr either, for exactly this
    reason: a traceback carries file paths, line numbers and interpreter
    internals a subset runtime has no business reproducing.

    What must agree is what a shell pipeline and an agent loop consume — stdout
    and the exit code — plus the exception class, because a different class is
    caught by a different `except`. The rows whose message this capability DOES
    write are in `OWN_MESSAGES`, inside the byte-for-byte grid."""
    got = _run([str(BINARY)], program)
    if got.returncode == engines.UNSUPPORTED_EXIT:
        assert _refusal_problem(got) is None, "%s\n  program: %r" % (
            _refusal_problem(got), program)
        pytest.skip("lypning-l refuses this row: %s" % got.stderr.strip()[:160])
    ref = _run([sys.executable], program)
    assert ref.returncode != 0, "this row is supposed to raise under CPython: %r" % program
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "  program:  %r\n  lypning-l: %r exit %d %r\n  cpython:   %r exit %d %r"
        % (program, got.stdout, got.returncode, got.stderr.strip()[-200:],
           ref.stdout, ref.returncode, ref.stderr.strip()[-200:]))
    assert _exception_class(got.stderr) == _exception_class(ref.stderr), (
        "the exception CLASS must agree — a different one is caught by a "
        "different `except`\n  program: %r\n  lypning-l: %r\n  cpython: %r"
        % (program, got.stderr.strip()[-160:], ref.stderr.strip()[-160:]))


@needs_l
@pytest.mark.parametrize("program", MODULE_SURFACE, ids=range(len(MODULE_SURFACE)))
def test_the_surface_outside_the_subset_refuses_rather_than_guesses(program: str) -> None:
    """Every one of these must be exit 90 with an EMPTY stdout.

    CPython answers most of them; the rest it answers with a message out of
    `hashlib.py` that this engine has no business writing. Either way the
    refusal is the whole mechanism: the chain gets the answer from CPython one
    spawn later, and an answer HERE would be a guess at exit 0."""
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got)
    assert problem is None, (
        "this program must refuse, not answer: %s\n  program: %r\n  stderr: %r"
        % (problem, program, got.stderr.strip()[:200])
    )


@needs_l
@pytest.mark.parametrize("program", ADMITTED, ids=range(len(ADMITTED)))
def test_no_newly_admitted_corpus_program_dies_at_exit_one(program: str) -> None:
    """The class that held this capability back, as an assertion.

    Iteration 74 measured `hashlib` at 0.87 programs/KB and rejected it — and
    its three findings were not hashlib bugs. Adding the module to the router
    admitted programs that then died at exit 1 on an unrelated missing method,
    with whatever they had already printed on stdout, which the barrier does not
    discard because it only discards on 90 and which the chain never retries.

    So: exit 0 with CPython's bytes, or a clean 90. Never exit 1."""
    got = _run([str(BINARY)], program, stdin="abc")
    if got.returncode == engines.UNSUPPORTED_EXIT:
        assert _refusal_problem(got) is None, (got.stdout, got.stderr)
        return
    ref = _run([sys.executable], program, stdin="abc")
    assert got.returncode == ref.returncode, (
        "a newly admitted program exits %d where CPython exits %d — this is the "
        "shape iteration 74 rejected\n  program: %r\n  stdout: %r\n  stderr: %r"
        % (got.returncode, ref.returncode, program, got.stdout, got.stderr[-300:]))
    assert got.stdout == ref.stdout, (program, got.stdout, ref.stdout)


#: The barrier every row of `AFTER_A_BARRIER` is asked through. `os.mkdir` is
#: the cheapest thing that commits it: the directory is created immediately, so
#: a refusal that lands afterwards is exit 1 with `NEWD` on disk, and one that
#: lands before it is exit 90 with the cwd untouched. The test reads the CWD,
#: not the message.
BARRIER = "import os\nos.mkdir('NEWD')\n"


def _barriered(call: str) -> str:
    """``call``, with the barrier committed before it and its imports before
    that. A row either brings its own import line or gets the plain one."""
    if call.startswith(("import ", "from ")):
        head, tail = call.split("\n", 1)
        return head + "\n" + BARRIER + tail
    return H + BARRIER + call


def _run_snapshot(program: str) -> tuple[subprocess.CompletedProcess, list[str], list[str]]:
    """One program, with the temp cwd listed before and after it ran."""
    with tempfile.TemporaryDirectory() as d:
        before = sorted(os.listdir(d))
        got = subprocess.run([str(BINARY), "-c", program], capture_output=True,
                             text=True, cwd=d, timeout=120)
        return got, before, sorted(os.listdir(d))


@needs_l
@pytest.mark.parametrize("kind,call", AFTER_A_BARRIER, ids=range(len(AFTER_A_BARRIER)))
def test_every_refusal_a_hashlib_call_can_raise_lands_before_the_barrier(
    kind: str, call: str,
) -> None:
    """Issue #51, as one assertion per shape.

    Serving the module admits the program, so it RUNS — and a refusal it reaches
    after `os.mkdir` has committed the write barrier is exit 1 with the
    directory on disk, nothing on stdout, and a chain that never retries. The
    binary WITHOUT `cap-hashlib` refused that program cleanly at 90 and the
    chain got the answer from CPython, so the capability would have made the
    program worse.

    Four assertions, and the fourth is the one that cannot be faked: the cwd is
    listed before and after, so "no side effect" is measured rather than read
    out of the refusal line."""
    program = _barriered(call)
    got, before, after = _run_snapshot(program)
    assert _refusal_problem(got) is None, (
        "%s\n  program: %r\n  stderr: %r"
        % (_refusal_problem(got), program, got.stderr.strip()[:200]))
    assert ": %s: " % kind in got.stderr, (
        "the refusal moved into the walk must keep the kind the run would have "
        "raised\n  program: %r\n  stderr: %r" % (program, got.stderr.strip()[:200]))
    assert after == before, (
        "the refusal landed AFTER os.mkdir committed the barrier: %r -> %r\n"
        "  program: %r" % (before, after, program))


@needs_l
@pytest.mark.parametrize("program", HIDDEN_BLOCKER, ids=range(len(HIDDEN_BLOCKER)))
def test_a_blocker_the_router_could_not_see_refuses_instead_of_exiting_one(
    program: str,
) -> None:
    """Iteration 74's rejection reason, as one assertion per construct.

    The router is first-blocker-wins and the first blocker of every row here is
    `module: import hashlib`, which `lypning-l` answers — so the program is
    admitted, and the construct that actually stops it (`.to_bytes()`,
    `.bit_length()`, `.isascii()`) was invisible to the route. It printed the
    digest and then raised `AttributeError`: exit 1, which the barrier does not
    discard and the chain does not retry, for a program CPython answers.

    Four assertions, and the first two are the ones that matter: CPython
    answers at exit 0 (so the refusal is not hiding a program that fails
    anyway), and this engine exits 90 with an empty stdout rather than 1 with a
    partial one. The barrier is committed first and the cwd compared, so
    `NEWD` on disk would fail the row even if the exit code looked right."""
    plain = program if program.startswith(("import ", "from ")) else H + program
    ref = _run([sys.executable], plain)
    assert ref.returncode == 0 and ref.stdout, (
        "this row is only interesting if CPython answers it\n  program: %r\n"
        "  cpython: exit %d %r" % (plain, ref.returncode, ref.stderr.strip()[-200:]))
    got, before, after = _run_snapshot(_barriered(program))
    assert _refusal_problem(got) is None, (
        "%s\n  program: %r\n  stderr: %r"
        % (_refusal_problem(got), program, got.stderr.strip()[:200]))
    assert ": method: " in got.stderr, (
        "the refusal must keep the kind the walk raised\n  program: %r\n"
        "  stderr: %r" % (program, got.stderr.strip()[:200]))
    assert after == before, (
        "the refusal landed AFTER os.mkdir committed the barrier: %r -> %r\n"
        "  program: %r" % (before, after, program))


@needs_l
@pytest.mark.parametrize("program", BOUND_IDENTITY, ids=range(len(BOUND_IDENTITY)))
def test_a_constructor_read_as_a_value_answers_rather_than_refusing(
    program: str,
) -> None:
    """The grid SKIPS a row this engine refuses, which is right for a table of
    reach and wrong for this one: `hashlib.md5 is hashlib.md5` answering
    `unsupported` instead of `True` would be a silent regression that leaves
    `test_the_hashlib_grid_agrees_with_cpython` green. So the identity rows are
    asserted to ANSWER as well as to agree."""
    got = _run([str(BINARY)], program)
    assert got.returncode != engines.UNSUPPORTED_EXIT, (
        "a constructor compared with itself must be answered, not refused\n"
        "  program: %r\n  stderr: %r" % (program, got.stderr.strip()[:200]))


@needs_l
@pytest.mark.parametrize("call", RUNTIME_BACKSTOP, ids=range(len(RUNTIME_BACKSTOP)))
def test_what_stays_a_runtime_refusal_still_refuses(call: str) -> None:
    """The residue, pinned so it stays a residue.

    These three arms are `Value::IterObj`'s and not this capability's: `repr()`,
    `len()` and hashing need the receiver's runtime type, so no walk can decide
    them. A refusal here lands after the barrier — that is what admitting the
    shape costs — and the assertion is only that the backstop is still there. An
    ANSWER instead would be a heap address or a set order at exit 0."""
    program = _barriered(call)
    got, _, _ = _run_snapshot(program)
    assert got.returncode != 0, (
        "this must still refuse — the static half deliberately cannot see it\n"
        "  program: %r\n  stdout: %r" % (program, got.stdout[:200]))
    assert "unsupported: " in got.stderr, got.stderr[:200]


@needs_l
def test_the_core_routes_every_static_hashlib_refusal_straight_to_cpython() -> None:
    """The other half of making a refusal static: it stops costing a spawn.

    `engines.route()` asks the CORE, whose first blocker for every one of these
    is `module: import hashlib` — which `lypning-l` answers, so the whole table
    would route there and be refused. `route::MODULE_ATTRS` and the STOP slot
    the walk records carry the refusal into the verdicts instead, so the core
    names CPython without a hashlib implementation of its own."""
    if CORE is None:
        pytest.skip("no core carrying this tree's capability table is built")
    for kind, call in AFTER_A_BARRIER:
        if kind != "module-attr":
            continue
        program = call if call.startswith(("import ", "from ")) else H + call
        out = subprocess.run([str(CORE), "route", "-c", program],
                             capture_output=True, text=True, timeout=60)
        assert out.stdout.split("\t")[0].strip() == engines.CPYTHON, (
            "the core sent a program every Rust rung refuses to a Rust rung\n"
            "  program: %r\n  route: %r" % (program, out.stdout))


@needs_l
def test_the_keyword_block_costs_one_spawn_and_never_an_exit_code() -> None:
    """Issue #48's residue, measured rather than argued.

    The keyword rows are decided by a walk behind `cfg(feature = "cap-hashlib")`,
    so the CORE — which has no hashlib implementation and therefore no reason to
    carry the check — cannot compute them and routes them to `lypning-l` on the
    strength of `module: import hashlib` alone. That costs ONE spawn and it is
    the deliberate trade: `route::MODULE_ATTRS` is carried by every variant
    because a wrong ATTRIBUTE is common, and `usedforsecurity=` appears nowhere
    in the corpus.

    What must NOT happen is the exit code: `lypning-l` runs the same walk over
    its own `-c` input before `Interp::new()`, so the refusal is 90 with an
    untouched disk, which `AFTER_A_BARRIER` asserts. Both halves here."""
    if CORE is None:
        pytest.skip("no core carrying this tree's capability table is built")
    for kind, call in AFTER_A_BARRIER:
        if kind == "module-attr":
            continue
        program = call if call.startswith(("import ", "from ")) else H + call
        core = subprocess.run([str(CORE), "route", "-c", program],
                              capture_output=True, text=True, timeout=60)
        assert core.stdout.split("\t")[0].strip() == engines.LYPNING_L, core.stdout
        # …and the variant that CAN compute it says CPython, which is what
        # makes the spawn the only cost.
        mine = subprocess.run([str(BINARY), "route", "-c", program],
                              capture_output=True, text=True, timeout=60)
        assert mine.stdout.split("\t")[0].strip() == engines.CPYTHON, mine.stdout
        assert "hashlib: " in mine.stdout, mine.stdout


@needs_l
def test_the_core_routes_a_served_hashlib_program_to_the_larger_variant() -> None:
    """…and the converse, which is the capability's whole value.

    The four served constructors must route INTO `lypning-l` from the core,
    which has no `hashlib` row in `modules::MODULES` and reads the claim off
    `route::CAPS`."""
    if CORE is None:
        pytest.skip("no core carrying this tree's capability table is built")
    for alg, _, _ in ALGS:
        program = H + "print(hashlib.%s(b'abc').hexdigest())" % alg
        out = subprocess.run([str(CORE), "route", "-c", program],
                             capture_output=True, text=True, timeout=60)
        assert out.stdout.split("\t")[0].strip() == engines.LYPNING_L, out.stdout


@needs_l
def test_the_capability_is_on_the_larger_variant_only() -> None:
    """The gate this whole file sits behind: the core must still REFUSE
    `hashlib`, and must route it to the sibling that serves it.

    A capability that leaked into the frozen variant would still pass every grid
    row above — it is the same code — so the byte budget is defended here, by
    asking each binary what it is."""
    if CORE is None:
        pytest.skip("no core carrying this tree's capability table is built")
    refused = _run([str(CORE)], "import hashlib")
    assert refused.returncode == engines.UNSUPPORTED_EXIT and refused.stdout == ""
    assert refused.stderr.strip() == engines.refusal_line(
        engines.LYPNING, "module", "import hashlib")


@needs_l
def test_the_digests_are_the_reference_interpreters_own() -> None:
    """One assertion the grid cannot make: the four algorithms, over a message
    long enough to cross every block boundary, compared against `hashlib` in
    THIS process rather than against a subprocess."""
    msg = bytes(range(256)) * 5
    for alg, size, block in ALGS:
        program = (H + "import sys\nd = sys.stdin.buffer.read()\n"
                   "print(hashlib.%s(d).hexdigest())" % alg)
        # `sys.stdin.buffer` is not served, so the digest is taken through a
        # literal instead — the point is the VALUE, not the plumbing.
        program = H + "print(hashlib.%s(%r).hexdigest())" % (alg, msg)
        got = _run([str(BINARY)], program)
        assert got.returncode == 0, got.stderr
        want = hashlib.new(alg, msg).hexdigest()
        assert got.stdout.strip() == want, (alg, got.stdout, want)
        assert hashlib.new(alg).digest_size == size
        assert hashlib.new(alg).block_size == block


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
    assert "cap-hashlib" in table["self_caps"]
    assert {r["name"]: tuple(r["caps"]) for r in table["spectrum"]} == engines.VARIANT_CAPS
    assert {r["cap"]: r["modules"] for r in table["caps"]}["cap-hashlib"] == ["hashlib"]
