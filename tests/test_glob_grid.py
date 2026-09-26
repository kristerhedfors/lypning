"""`glob`, as a grid: every program on the binary and on CPython.

`tests/test_pathlib_grid.py` is the shape this follows and the reason it
exists: the defect is PER PROGRAM, not per function, and a handful of examples
is exactly what would miss it.

Every row runs twice from a FRESH temp cwd holding the same small tree
(invariant 4 — and these rows really do read and write files, so the temp cwd is
load-bearing rather than ceremonial), and must end one of exactly two ways:

  * byte-identical stdout AND the same exit code as CPython 3.x, or
  * a clean refusal — exit 90, nothing on stdout, one
    ``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

**The one thing this file is really for.** `glob.glob()` returns a list whose
ORDER is the filesystem's, because CPython walks with `os.scandir` and does not
sort. lypning-l computes that order the way CPython does — the same `readdir`
stream, the same `_iglob` yield order — so an eager `glob.glob()` is served in
ANY position, and `ORDER_FAITHFUL` grades it UNSORTED against CPython in the
same directory, over a tree created in shuffled order so the filesystem's
order is visibly not alphabetical. What `route.rs` still stops statically, as
`glob-order` at exit 90 with an untouched disk, is `ORDER_SHOWN`: a lazy
`glob.iglob()` outside an order-blind wrapper (CPython reads each directory
only after the loop body before it has run) and a listing function used as a
value. `ORDER_BLIND` is one row per position where `iglob` is served; a row
that answered in `ORDER_SHOWN` would be a guess at exit 0. And lypning-l
refuses `glob-order` at RUNTIME where an order shows over a directory the run
has changed (`STAGED_ORDER_SHOWN`).

**Two rules the first version of that list got wrong, one row each below.**
`set()` was a served position: its ANSWER is order-blind, but the set it hands
back is not, and this engine's `Value::Set` is insertion-ordered, so
`print(set(glob.glob(p)))` was a RUNTIME `set-order` refusal — exit 1 after
`os.mkdir` had committed the barrier, where the core refused at 90 and the chain
got the answer from CPython. And `glob.iglob` answers a GENERATOR: every
position that CONSUMES its argument cannot tell one from a list, but `len()`
raises on a generator and `bool()` is True for every generator, so
`bool(glob.iglob('nope*'))` printed False against CPython's True — at exit 0, on
the case an empty directory makes normal.

The matching traps, each measured against CPython 3.14.5 before the code was
written:

1. **A leading `.` is not matched by `*`, `?` or `[...]`.** `_glob1` drops
   hidden names unless the PATTERN's own basename starts with a dot, so
   `glob('*')` never sees `.hidden` and `glob('.*')` sees only hidden names —
   while `glob('.hidden')`, which has no magic at all, finds it, because that
   path is an `lexists` test and not a listing. `HIDDEN` is a block of it.
2. **A pattern with no magic never lists anything.** It is an `lexists` test, so
   a BROKEN SYMLINK matches where an `exists` test would not; a pattern ending
   in `/` matches only a directory and keeps the slash.
3. **`recursive=True` and `**`.** `**` matches the empty path first, so
   `glob('d/**', recursive=True)` yields `'d/'` before anything under it, and
   `'**/*.py'` matches a top-level `a.py` as well as `d/a.py`. `**` is special
   only as a WHOLE component and only with the keyword; `'**x'`, or `'**'`
   without it, is an ordinary `*`.
4. **The bracket expression is `fnmatch`'s, not a regex's.** A `!` and a `]`
   immediately after the `[` are part of the body (`[]]` matches `]`, `[!]]`
   matches anything else); `^`, `&`, `|` and `\\` are literals; an unterminated
   `[` is a literal `[`. A REVERSED range is where CPython's translation stops
   being a character class and starts merging chunks, so `[z-a]` refuses.

**The class the position rule left open, and the table that closes it.**
A blessed call is SERVED, so the program starts — and a refusal it reaches
afterwards lands past the commit barrier, which is exit 1 with the side effect
on disk, no answer, and a chain that never retries. That is not a smaller
version of a clean 90; it is the regression `docs/HILLCLIMB.md` iteration 76
rejected the first `cap-glob` attempt for, and three separate triggers reached
it through blessed positions: a `root_dir=` keyword, a `[z-a]` pattern literal,
and `glob.translate`, which the core could not even grade because
`modules::MODULES` has no `glob` row in it. So every refusal reachable from an
admitted glob call is now decided in the WALK wherever the walk can see it, and
`AFTER_A_BARRIER` is one row per refusal KIND, each run as
``os.mkdir('NEWD'); <the call>`` and each asserted four ways: exit 90, one
refusal line, an EMPTY stdout, and a cwd whose listing is unchanged — checked by
a before/after snapshot rather than by reading the message, because the message
is the half that was already right.

**The commit barrier is merged into the listing.** `io.rs` stages writes until
the run ends; `open()` and `os.path.exists()` are asked about one path and merge
it, and a LISTING has to work out which staged spelling names an entry of which
directory. `STAGED` is that block: a file this run wrote is an entry of its
directory even though it is not on disk, a file it removed is not an entry even
though it is, and `./d/x` and `d/x` are one path.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

G = "import glob\n"

#: The tree every row is run against, built by the RUNNER rather than by the
#: program, so the rows below measure the filesystem and not the staging area.
#: `STAGED` is where the staging area is measured, on purpose and separately.
FILES = (
    "a.py", "b.py", "c.txt", "ab.py", "aa.py", "A.PY", "10.py", "2.py",
    ".hidden", ".dot.py", "x-y.py", "x_y.py", "z]q", "w[q", "q*r", "t?u",
    "-lead", "end-", "sp ace.py", "é.py", "a.b.c", "no_ext",
    "d/a.py", "d/b.txt", "d/.h", "d/e/a.py", "d/e/c.txt", ".hd/a.py", ".hd/.h",
)
DIRS = ("d", "d/e", "d/e/f", ".hd", "empty")
LINKS = (("broken", "nowhere"), ("dlink", "d"))


def _tree(root: str) -> None:
    # In a SHUFFLED but fixed order, so a filesystem that lists in creation
    # order (tmpfs, btrfs) shows an order that is visibly not alphabetical —
    # and every tree this file builds is still built the same way.
    import random
    rng = random.Random(20260926)
    for x in rng.sample(DIRS, len(DIRS)):
        os.makedirs(os.path.join(root, x), exist_ok=True)
    for f in rng.sample(FILES, len(FILES)):
        p = os.path.join(root, f)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").close()
    for name, target in LINKS:
        os.symlink(target, os.path.join(root, name))


#: Trap 1, 2, 3 and 4 in one list: every pattern shape worth getting wrong.
#: Wrapped in `sorted()` because that is the only way a multi-match result is
#: allowed to be looked at — which is the capability, stated as a test fixture.
PATTERNS = (
    # plain wildcards, and the hidden-name rule (trap 1)
    "*", "*.py", "*.PY", "?.py", "??.py", "*.*", "a*", "*a*", "*[.]py", "*/",
    ".*", ".*.py", ".hidden", ".hd/*", ".hd/.h", "d/.h",
    # no magic at all: an lexists test, not a listing (trap 2)
    "a.py", "nope", "nope/*", "d", "d/", "d/e/", "empty/", "no_ext",
    "broken", "brok*", "dlink", "dlink/", "dlink/*", "", "/", "//", "./*.py",
    # directories, and a magic component in the DIRNAME
    "d/*", "d/*.py", "*/*", "*/*/*", "*/a.py", "d//e/*", "d/e/../a.py",
    # `**` without the keyword is an ordinary `*` (trap 3)
    "**", "**/*", "**/*.py", "d/**", "d/**/*", "**x", "**.py", "**/**",
    # bracket expressions (trap 4)
    "[ab].py", "[!ab].py", "[a-c]*", "[!a-c]*", "[0-9].py", "[!0-9]*.py",
    "[]", "[]]", "[!]]", "[abc", "[[]q", "z[]]q", "q[*]r", "t[?]u",
    "[-a]*", "[a-]*", "[^a]*", "[&a]*", "[|a]*", "[.a]*", "a[b]*",
)

MATCHING = [G + "print(sorted(glob.glob(%r)))" % p for p in PATTERNS] + [
    G + "print(sorted(glob.glob(%r, recursive=True)))" % p for p in PATTERNS
] + [
    # `recursive=` by every spelling a program actually types.
    G + "print(sorted(glob.glob('**/*.py', recursive=1)))",
    G + "print(sorted(glob.glob('**/*.py', recursive=0)))",
    G + "print(sorted(glob.glob('**/*.py', recursive=True)))",
    G + "r=True\nprint(sorted(glob.glob('**/*.py', recursive=r)))",
    # trap 3: `**` yields the empty path first, and `iglob` drops it only when
    # the PATTERN starts with `**`.
    G + "print(sorted(glob.glob('d/**', recursive=True)))",
    G + "print(sorted(glob.glob('**', recursive=True)))",
    G + "print(len(glob.glob('**', recursive=True)))",
    # the alias spellings
    "import glob as g\nprint(sorted(g.glob('*.py')))",
    "from glob import glob\nprint(sorted(glob('*.py')))",
    "from glob import glob as gg\nprint(sorted(gg('*.py')))",
    "from glob import iglob\nprint(sorted(iglob('*.py')))",
    "import glob, os\nprint(sorted(glob.glob(os.path.join('d', '*.py'))))",
    "import glob\nP='*.py'\nprint(sorted(glob.glob(P)))",
]

#: Trap 1, on its own, because it is the rule an implementation drops.
HIDDEN = [G + x for x in [
    "print(sorted(glob.glob('*')))",
    "print(sorted(glob.glob('.*')))",
    "print(sorted(glob.glob('.???*')))",
    "print(sorted(glob.glob('*hidden*')))",
    "print(sorted(glob.glob('.hidden')))",
    "print(sorted(glob.glob('d/*')))",
    "print(sorted(glob.glob('d/.h')))",
    "print(sorted(glob.glob('d/.*')))",
    "print(sorted(glob.glob('**/*', recursive=True)))",
    "print(sorted(glob.glob('.hd/**', recursive=True)))",
    "print(len(glob.glob('**', recursive=True)) == len(glob.glob('**')))",
]]

#: `escape` and `has_magic`: pure string algebra, answered in ANY position
#: because a string carries no order.
STRING_ALGEBRA = [G + x for x in [
    "print(repr(glob.escape('a*b?c[d]')))",
    "print(repr(glob.escape('')), repr(glob.escape('plain')))",
    "print(repr(glob.escape('/a/*.py')), repr(glob.escape('[]')))",
    "print(repr(glob.escape('**')), repr(glob.escape('?')))",
    "print(repr(glob.escape('\\u00e9*')))",
    "print(glob.has_magic('a*'), glob.has_magic('a'), glob.has_magic(''))",
    "print(glob.has_magic('['), glob.has_magic('?'), glob.has_magic(']'))",
    "print(sorted(glob.glob(glob.escape('q*r'))))",
    "print(sorted(glob.glob(glob.escape('w[q'))))",
    "print(sorted(glob.glob(glob.escape('t?u'))))",
    "e = glob.escape('z]q')\nprint(e, sorted(glob.glob(e)))",
    "print([glob.escape(x) for x in ['a', 'b*']])",
    "print(glob.escape('a*') + glob.escape('b?'))",
]]

#: Every position the router SERVES, one row each. These are the whole
#: capability: if one of them started refusing, the capability would be inert.
ORDER_BLIND = [G + x for x in [
    "print(sorted(glob.glob('*.py')))",
    "print(sorted(glob.glob('*.py'), reverse=True))",
    "print(len(glob.glob('*.py')))",
    "print(bool(glob.glob('*.py')), bool(glob.glob('nope*')))",
    "print(sum(glob.glob('*.py')))",
    "print(min(glob.glob('*.py')), max(glob.glob('*.py')))",
    "print(min(glob.glob('nope*'), default='-'))",
    "print(any(glob.glob('*.py')), all(glob.glob('*.py')))",
    "print(any(glob.glob('nope*')), all(glob.glob('nope*')))",
    "print('a.py' in glob.glob('*.py'))",
    "print('zz.py' not in glob.glob('*.py'))",
    "for p in sorted(glob.glob('*.py')): print(p)",
    "for p in sorted(glob.glob('d/*')): print(p)",
    "print(sorted(glob.glob('*.py'))[0], sorted(glob.glob('*.py'))[-1])",
    "print(sorted(glob.glob('*.py'))[:2])",
    "print(len(sorted(glob.glob('*.py'))))",
    "n = len(glob.glob('*.py'))\nprint(n * 2)",
    "print([p.upper() for p in sorted(glob.glob('*.py'))])",
    # `iglob` answers a generator, so only the positions that CONSUME their
    # argument take one — decided per name in `route.rs`'s `ORDER_BLIND`.
    "print(sorted(glob.iglob('*.py')))",
    "print('a.py' in glob.iglob('*.py'))",
    "print(min(glob.iglob('*.py')), max(glob.iglob('*.py')))",
    "print(any(glob.iglob('nope*')), all(glob.iglob('nope*')))",
    "print(sum(glob.iglob('*.py')))",
    # An empty match set is the NORMAL answer for a glob, and `min`/`max` are
    # served over it — so CPython's ValueError text is reachable from an
    # advertised position — and CPython does not word it the same on every
    # version this package supports: 3.9, 3.10 and 3.11 say "min() arg is an
    # empty sequence", 3.12 and 3.13 "min() iterable argument is empty"
    # (measured 2026-09-12). The engine words it for the host, so these two rows
    # grade against whichever one the reference here says.
    "try:\n    print(min(glob.glob('nope*')))\nexcept ValueError as e:\n    print(e)",
    "try:\n    print(max(glob.iglob('nope*')))\nexcept ValueError as e:\n    print(e)",
    # nested: the argument of an order-blind wrapper is order-blind whatever
    # the wrapper's own caller does with the answer.
    "print(str(sorted(glob.glob('*.py'))))",
    "print(sorted(sorted(glob.glob('*.py'))))",
    "print({'n': len(glob.glob('*.py'))})",
    "if bool(glob.glob('*.py')) and len(glob.glob('*.py')) > 1: print('yes')",
    # A wrapper name is given up PER NAME. `defaultdict(set)` passes the
    # builtin `set` around without rebinding anything, and giving up `sorted`
    # for it refused a corpus program whose glob call is squarely inside
    # `sorted()` (py-ad25b33c55b7).
    "import collections\nd = collections.defaultdict(set)\n"
    "print(sorted(glob.glob('*.py')), len(d))",
    "print(sorted(glob.glob('*.py')), list(map(len, ['ab'])))",
    "print(sorted(glob.glob('*.py')), list(map(str, [1])))",
]]

#: Every position an eager `glob.glob()` could be in, graded UNSORTED against
#: CPython in the SAME directory: the order is computed, not hidden.
ORDER_FAITHFUL = [G + x for x in [
    "print(glob.glob('*'))",
    "print(glob.glob('*.py'))",
    "print(glob.glob('nope*'))",
    "for p in glob.glob('*.py'): print(p)",
    "for p in glob.glob('d/*/*.py'): print(p)",
    "for p in glob.glob('*/'): print(p)",
    "print(glob.glob('**', recursive=True))",
    "print(glob.glob('**/*.py', recursive=True))",
    "print(glob.glob('d/**', recursive=True))",
    "print(glob.glob('**/', recursive=True))",
    "print(glob.glob('**/**', recursive=True))",
    "print(glob.glob('**/*'))",
    "print(glob.glob('*/*'))",
    "print(glob.glob('.*'))",
    "print(glob.glob('*/'))",
    "print(glob.glob('dlink/*'))",
    "print(glob.glob('[ab]*'))",
    "x = glob.glob('*.py')\nprint(sorted(x), x)",
    "f = glob.glob('*.py')\np = f[0]\nprint(p, len(f))",
    "print(glob.glob('*.py')[0], glob.glob('*.py')[-1])",
    "print(glob.glob('*.py')[:1])",
    "print(list(glob.glob('*.py')))",
    "print(tuple(glob.glob('*.py')))",
    "print(list(reversed(glob.glob('*.py'))))",
    "print(glob.glob('a*') + glob.glob('b*'))",
    "print(glob.glob('*.py') + glob.glob('*.txt'))",
    "print(sorted(glob.glob('*.py') + glob.glob('*.txt')))",
    "print(sorted([glob.glob('*.py')]))",
    "print(sorted(glob.glob('*'), key=len))",
    "print(max(glob.glob('*.py'), key=len))",
    "print(min(glob.glob('*.py'), key=len))",
    "print(sorted(glob.glob('*.py'), key=str, reverse=True))",
    "print([p.upper() for p in glob.glob('*')])",
    "print([p for p in glob.glob('*.py')])",
    "print(glob.glob('*.py') == [])",
    "print(len(glob.glob('*.py')) if glob.glob('*.py') else 0)",
    "print(sorted(map(str, glob.glob('*.py'))))",
    "print(sorted(glob.glob('*.py')) == glob.glob('*.py'))",
    "print(sorted(set(glob.glob('*.py'))))",
    "print(len(set(glob.glob('*.py'))))",
    "print(sorted(set(glob.glob('*.py')) | set(glob.glob('*.txt'))))",
    "print('a.py' in glob.glob('*.py') in [True])",
    "if glob.glob('*.py'): print('yes')",
    "print(bool(glob.glob('*.py')))",
    "print('hi')\nprint(glob.glob('*.py'))",
    # the wrapper is no longer what decides: a rebound `sorted` shows the order
    # and the order is CPython's
    "sorted = lambda x: x\nprint(sorted(glob.glob('*.py')))",
    "def len(x): return 0\nprint(len(glob.glob('*.py')))",
    "def f(sorted): return sorted\nprint(sorted(glob.glob('*.py')))",
    "print(list(map(len, ['ab'])), len(glob.glob('*.py')))",
    "print(sorted(glob.glob('*.py'), *[], **{}))",
    # every served spelling
    "from glob import glob\nprint(glob('*.py'))",
    "from glob import glob as g\nprint(g('d/*'))",
    "import glob as G\nprint(G.glob('d/*'))",
    "import glob as g\nprint(g.glob('*.py'))",
    # the corpus's own shape: a report loop that opens what it globbed
    "for f in glob.glob('d/**/*.py', recursive=True):\n"
    "    print(f, len(open(f).read()))",
]]

#: What still stops STATICALLY: a lazy `iglob` outside an order-blind wrapper,
#: and a listing function used as a value. A row here that answered would be an
#: order this engine did not compute the way CPython does — at exit 0.
ORDER_SHOWN = [G + x for x in [
    "print(glob.iglob('*.py'))",
    "for p in glob.iglob('*.py'): print(p)",
    "print(list(glob.iglob('*.py')))",
    "print(set(glob.iglob('nope*')))",
    "f = glob.glob\nprint(sorted(f('*.py')))",
    "f = glob.glob\nprint(f('*'))",
    "print(list(map(glob.glob, ['*'])))",
    # `iglob` answers a GENERATOR, and the two order-blind names that ask about
    # the CONTAINER rather than consume it read one differently from a list.
    # `len()` raises `TypeError`; `bool()` is True for EVERY generator, so the
    # empty case — the normal one for a glob — printed False against CPython's
    # True, at exit 0. Decided per name in `route.rs`, not by a `len` test.
    "print(len(glob.iglob('*.py')))",
    "print(len(glob.iglob('nope*')))",
    "print(bool(glob.iglob('*.py')))",
    "print(bool(glob.iglob('nope*')))",
    "print(bool(glob.iglob('*.py')), bool(glob.glob('*.py')))",
    "from glob import iglob\nprint(bool(iglob('nope*')))",
    "import glob as g\nprint(bool(g.iglob('nope*')))",
    "import glob as g\nprint(g.iglob('*.py'))",
    # `from glob import glob` and then the function used as a value
    "from glob import glob\nf = glob\nprint(sorted(f('*.py')))",
    "from glob import glob\nh = glob\nprint(h('*'))",
    # a refusal leaves NOTHING on stdout even when the program printed first —
    # and here it never got to print at all, because the block is static
    "print('hi')\nfor p in glob.iglob('*.py'): print(p)",
    "import os\nos.makedirs('made')\nfor p in glob.iglob('*.py'): print(p)",
]]

#: Served, but refused for a reason that is not glob's: `set()` hands back a
#: set, and this engine's `Value::Set` is insertion-ordered where CPython's is
#: hash-ordered, so a set of two or more paths is a RUNTIME `set-order`
#: refusal. `frozenset` is not a builtin here. Both must be clean refusals, and
#: `os.mkdir` before one must be rewound (issue #51).
SERVED_THEN_REFUSED = [G + x for x in [
    "print(set(glob.glob('*.py')))",
    "import os\nos.mkdir('made_set')\nprint(set(glob.glob('*.py')))",
    "print(frozenset(glob.glob('*.py')))",
]]

#: Rows that write, so each engine gets its own (identically created) tree.
FAITHFUL_WRITES = [G + x for x in [
    "import os\nos.makedirs('made')\nprint(glob.glob('*.py'), glob.glob('*/'))",
    "for sorted in [1]: pass\nprint(sorted(glob.glob('*.py')))",
]]

#: Every error path, and every keyword this engine will not approximate.
REFUSED = [G + x for x in [
    "print(sorted(glob.glob('*.py', root_dir='d')))",
    "print(sorted(glob.glob('*.py', dir_fd=3)))",
    "print(sorted(glob.glob('*', include_hidden=True)))",
    "print(sorted(glob.glob('*.py', bogus=1)))",
    "print(sorted(glob.glob('*.py', True)))",
    "print(sorted(glob.glob()))",
    "print(sorted(glob.glob(b'*.py')))",
    "print(sorted(glob.glob(1)))",
    "print(sorted(glob.glob(None)))",
    "print(sorted(glob.glob(['*.py'])))",
    "print(glob.escape(1))",
    "print(glob.has_magic(b'a'))",
    "print(glob.escape('a', 'b'))",
    "print(glob.has_magic('a*', x=1))",
    # a reversed range is where fnmatch stops being a character class
    "print(sorted(glob.glob('[z-a]*')))",
    "print(sorted(glob.glob('[9-0]*')))",
    # names this module does not serve
    "print(glob.translate('*'))",
    "print(glob.glob0('.', 'a.py'))",
    "print(glob.glob1('.', '*.py'))",
    "print(glob.magic_check)",
    "print(glob.nosuchthing)",
    "from glob import translate\nprint(translate('*'))",
    "print(glob)",
]]

#: Every refusal an ADMITTED glob call can raise, one row per shape, as
#: ``(kind, the call)``. The kind is asserted too: a refusal moved from the run
#: into the walk has to keep the line it would have printed a run later, or the
#: agent reading it learns something different depending on which binary
#: answered.
#:
#: The list is the whole of `glob.rs`'s error surface minus what no walk can
#: read — see `RUNTIME_BACKSTOP` below. "Read" is wider than one spelling and
#: the rows say so: a CONSTANT f-string is a literal, a DISPLAY behind a `*` or
#: a `**` is a literal, and a literal bound to a name is read out of the binding
#: table in force AT the call — which means in source order AND in scope, so a
#: `def f(P)`, a `lambda P:` or a comprehension over `P` no longer gives up a
#: module-level `P` for a call that never entered them.
AFTER_A_BARRIER = [
    # the keyword arguments: literal at the call site, whatever their value
    ("glob", "print(sorted(glob.glob('*.py', root_dir='d')))"),
    ("glob", "print(sorted(glob.glob('*.py', dir_fd=3)))"),
    ("glob", "print(sorted(glob.glob('*', include_hidden=True)))"),
    ("glob", "print(len(glob.glob('*.py', include_hidden=False)))"),
    ("glob", "print(sorted(glob.glob('*.py', bogus=1)))"),
    ("glob", "print(sorted(glob.iglob('*.py', root_dir='d')))"),
    ("glob", "print(glob.escape('a', x=1))"),
    ("glob", "print(glob.has_magic('a*', x=1))"),
    # `recursive=` is served by glob/iglob and by neither of the other two
    ("glob", "print(glob.escape('a', recursive=True))"),
    # the argument count
    ("glob", "print(sorted(glob.glob()))"),
    ("glob", "print(sorted(glob.iglob()))"),
    ("glob", "print(glob.escape())"),
    ("glob", "print(glob.has_magic())"),
    ("glob", "print(sorted(glob.glob('*.py', True)))"),
    ("glob", "print(glob.escape('a', 'b'))"),
    # the pattern's TYPE, for every literal a walk can name
    ("glob", "print(sorted(glob.glob(b'*.py')))"),
    ("glob", "print(sorted(glob.glob(1)))"),
    ("glob", "print(sorted(glob.glob(1.5)))"),
    ("glob", "print(sorted(glob.glob(None)))"),
    ("glob", "print(sorted(glob.glob(True)))"),
    ("glob", "print(sorted(glob.glob(['*.py'])))"),
    ("glob", "print(sorted(glob.glob(('*.py',))))"),
    ("glob", "print(sorted(glob.glob({'*.py'})))"),
    ("glob", "print(sorted(glob.glob({'a': 1})))"),
    ("glob", "print(glob.escape(1))"),
    ("glob", "print(glob.has_magic(b'a'))"),
    # …and a literal bound to a NAME above the call, which is the same
    # binding table `re.sub(P, …)` reads
    ("glob", "P = 5\nprint(sorted(glob.glob(P)))"),
    ("glob", "P = b'*.py'\nprint(sorted(glob.glob(P)))"),
    # the pattern itself, parsed in the walk by the same scan the run uses
    ("glob", "print(sorted(glob.glob('[z-a]')))"),
    ("glob", "print(sorted(glob.glob('[z-a]*')))"),
    ("glob", "print(sorted(glob.glob('[9-0]*')))"),
    ("glob", "print(sorted(glob.glob('[!z-a]')))"),
    ("glob", "print(sorted(glob.glob('d/[z-a]')))"),
    ("glob", "print(sorted(glob.glob('*/[z-a]/x')))"),
    ("glob", "print(sorted(glob.glob('[z-a]', recursive=True)))"),
    ("glob", "print(sorted(glob.iglob('[z-a]')))"),
    ("glob", "print(len(glob.glob('[z-a]')))"),
    ("glob", "print('x' in glob.glob('[z-a]'))"),
    ("glob", "P = '[z-a]'\nprint(sorted(glob.glob(P)))"),
    # …and a CONSTANT f-string, which is a literal with a prefix on it.
    # `Expr::FString` answered the TYPE `str` and never the text, so the type
    # gate passed, `glob_pattern_block` never ran, and the pattern was refused
    # by the run instead — past the barrier, exit 1.
    ("glob", 'print(sorted(glob.glob(f"[z-a]")))'),
    ("glob", 'print(sorted(glob.glob(f"[z" "-a]")))'),
    ("glob", 'P = f"[z-a]"\nprint(sorted(glob.glob(P)))'),
    ("glob", 'print(sorted(glob.iglob(f"[z-a]")))'),
    ("glob", 'print(sorted(glob.glob(f"", root_dir="d")))'),
    # …and the binding that is still LIVE at the call. The table is read in
    # source order, and scope is the other half of that rule: a name bound
    # inside a `def`, a `lambda` or a comprehension is a name of that scope
    # alone. Every row here gave the module-level literal up to a spelling that
    # never touched it, and reached the runtime refusal past the barrier.
    ("glob", "P = '[z-a]'\ndef f(P): pass\nprint(sorted(glob.glob(P)))"),
    ("glob", "P = '[z-a]'\nf = lambda P: P\nprint(sorted(glob.glob(P)))"),
    ("glob", "P = '[z-a]'\nq = [P for P in [1]]\nprint(sorted(glob.glob(P)))"),
    ("glob", "P = '[z-a]'\nq = {P: 1 for P in [1]}\nprint(sorted(glob.glob(P)))"),
    ("glob", "P = '[z-a]'\nfor P in sorted(glob.glob(P)): pass"),
    # …and the same leak in the other direction: a literal bound INSIDE a
    # function answered for the module-level name it shadows, so the call was
    # decided against a pattern that was never in force at it.
    ("glob", "P = '[z-a]'\ndef f():\n    P = '*.py'\nprint(sorted(glob.glob(P)))"),
    ("glob", "P = '[z-a]'\nf = lambda: 0\nprint(sorted(glob.glob(P)))"),
    # the argument list, when the `*`/`**` holds a DISPLAY — which is spelled
    # out in the source, so the walk can count it and read its keys
    ("glob", "print(sorted(glob.glob(*['[z-a]'])))"),
    ("glob", "print(sorted(glob.glob(*('[z-a]',))))"),
    ("glob", "print(sorted(glob.glob(*['*.py', 'x'])))"),
    ("glob", "print(sorted(glob.glob(*[])))"),
    ("glob", "print(sorted(glob.glob('*.py', **{'root_dir': 'd'})))"),
    ("glob", "print(sorted(glob.glob(*['*.py'], **{'dir_fd': 3})))"),
    ("glob", "print(sorted(glob.iglob('*.py', **{'include_hidden': True})))"),
    ("glob", "print(glob.escape('a', **{'x': 1}))"),
    ("glob", "print(sorted(glob.glob(*[b'*.py'])))"),
    # the attributes nothing on the spectrum serves
    ("module-attr", "print(glob.translate('*.py'))"),
    ("module-attr", "print(glob.glob0('.', 'a.py'))"),
    ("module-attr", "print(glob.glob1('.', '*.py'))"),
    ("module-attr", "print(glob.magic_check)"),
    ("module-attr", "print(glob.nosuchthing)"),
    ("module-attr", "print(sorted(glob.translate('*')))"),
    # and the position rule itself, through the same barrier
    ("glob-order", "print(glob.iglob('*.py'))"),
    ("glob-order", "print(bool(glob.iglob('nope*')))"),
    ("glob-order", "for p in glob.iglob('*.py'): print(p)"),
    ("glob-order", "f = glob.glob\nprint(f('*.py'))"),
]

#: The same class through every spelling of the import, because the walk finds
#: the function by NAME and an alias is where a name test goes wrong.
AFTER_A_BARRIER_ALIASED = [
    ("module-attr", "from glob import translate\nprint(translate('*'))"),
    ("module-attr", "from glob import glob0\nprint(glob0('.', 'a'))"),
    ("module-attr", "import glob as g\nprint(g.translate('*'))"),
    ("glob", "from glob import escape\nprint(escape(1))"),
    ("glob", "from glob import glob\nprint(sorted(glob('[z-a]')))"),
    ("glob", "from glob import glob as gg\nprint(sorted(gg('*', root_dir='d')))"),
    ("glob", "from glob import iglob as ig\nprint(sorted(ig('*', dir_fd=1)))"),
    ("glob", "import glob as g\nprint(sorted(g.glob('[z-a]')))"),
    ("glob", "import glob as g\nprint(sorted(g.iglob('*', include_hidden=True)))"),
]

#: What is deliberately NOT static, and the reason each one cannot be.
#:
#: A refusal here still lands after the barrier — that is the cost of admitting
#: the shape at all — but it is reachable only from a program whose PATTERN or
#: ARGUMENT LIST is a value the walk could not read, which is a far narrower
#: door than one a string literal walks through. Of the glob calls in the corpus
#: mined 2026-09-06, 45 pass a literal and 40 a computed pattern; none is
#: spelled with `*args` at all.
#:
#: **Landing after the barrier is not a glob property**, which is why these rows
#: assert a refusal and not a clean 90. `os.mkdir`/`os.makedirs` commit the
#: barrier the moment they run, so from there on every refusal this engine
#: raises is exit 1 — `builtin: eval`, `builtin: complex` and `set-order` with
#: no glob in the program behave identically. What the static half buys is that
#: an admitted glob call stops REACHING that door; it does not close it.
#:
#: The rows are asserted to still REFUSE (not answer), because the backstop is
#: the only thing standing behind them — and the last row is the filesystem's
#: own, which no walk could have hoisted at all.
RUNTIME_BACKSTOP = [
    # a pattern built at runtime: the value is not in the source
    "p = ''.join(['[z', '-a]'])\nprint(sorted(glob.glob(p)))",
    "p = len('ab')\nprint(sorted(glob.glob(p)))",
    # …including an f-string with an interpolation in it, which is a `str`
    # whose TEXT only the run knows. The constant one above is a literal and is
    # static; this one is the same computed-pattern residue by another spelling.
    "x = 'a'\nprint(sorted(glob.glob(f'[z-{x}]')))",
    # an unpacked argument list whose value is a NAME. The DISPLAY spellings
    # above are static — a display is written out in the source — but the table
    # that reads a name bound to a literal records a list or a dict as its TYPE
    # and never its contents, so there is nothing here for a walk to read.
    "a = ['[z-a]']\nprint(sorted(glob.glob(*a)))",
    "k = {'root_dir': 'd'}\nprint(sorted(glob.glob('*', **k)))",
    "print(sorted(glob.glob(*list(['[z-a]']))))",
    "print(sorted(glob.glob('*', **dict(root_dir='d'))))",
    "print(sorted(glob.glob(*[x for x in ['[z-a]']])))",
    "d = {'root_dir': 'x'}\nprint(sorted(glob.glob('*', **{**d})))",
    # the filesystem's own, and no walk could ever have hoisted it: how deep
    # the tree turns out to be. (The other one, a directory entry whose name is
    # not valid UTF-8, has no portable way to be created on this host — APFS
    # rejects the name — so it is documented in `glob.rs` and not pinned here.)
    "os.makedirs('/'.join(['deep'] * 140))\n"
    "print(len(glob.glob('**', recursive=True)))",
]

#: `**` under a dirname that is NOT a directory — missing, a file, a broken
#: link, or the `''` a magic dirname such as `**/**` yields first. The one place
#: CPython's unsorted answer differs by minor (measured 2026-09-26 on 3.9.6,
#: 3.10.20, 3.11.15, 3.12.13, 3.13.13 and 3.14.5): 3.9 and 3.10 yield `''` for
#: the dirname regardless, so `glob('nope/**')` is `['nope/']`, where 3.11+
#: answer `[]`; and 3.9 alone keeps a leading `''` in `**/**`. The engine
#: answers the 3.11+ shape, so a build whose reference is older refuses it.
STARSTAR_NOT_A_DIR = [G + x for x in [
    "print(glob.glob('nope/**', recursive=True))",
    "print(glob.glob('no_ext/**', recursive=True))",
    "print(glob.glob('broken/**', recursive=True))",
    "print(glob.glob('nope/**/**', recursive=True))",
    "print(sorted(glob.glob('nope/**', recursive=True)))",
    "print(len(glob.glob('no_ext/**', recursive=True)))",
    "print(sorted(glob.glob('**/**', recursive=True)))",
    "print(len(glob.glob('**/**', recursive=True)))",
    "print(len(glob.glob('**/**/**', recursive=True)))",
]]

#: The commit barrier, merged into the listing. Each row WRITES and then LISTS,
#: which is the shape the corpus actually types (`glob-pattern`).
STAGED = [G + x for x in [
    "import os\nos.makedirs('nd', exist_ok=True)\n"
    "open('nd/x.py','w').write('')\nopen('nd/y.py','w').write('')\n"
    "print(sorted(glob.glob('nd/*.py')))",
    "open('./d/new.py','w').write('')\nprint(sorted(glob.glob('d/*.py')))",
    "open('d/new.py','w').write('')\nprint(sorted(glob.glob('./d/*.py')))",
    "open('fresh.py','w').write('')\nprint(sorted(glob.glob('*.py')))",
    "open('fresh.py','w').write('')\nprint(len(glob.glob('fresh.py')))",
    "import os\nos.remove('a.py')\nprint(sorted(glob.glob('*.py')))",
    "import os\nos.remove('a.py')\nprint(sorted(glob.glob('a.py')))",
    "import os\nos.rename('a.py','moved.py')\nprint(sorted(glob.glob('*.py')))",
    "open('d/e/new.py','w').write('')\n"
    "print(sorted(glob.glob('**/*.py', recursive=True)))",
    "import os\nos.makedirs('nd2/sub', exist_ok=True)\n"
    "open('nd2/sub/z.py','w').write('')\n"
    "print(sorted(glob.glob('nd2/**/*.py', recursive=True)))",
    "open('later.py','w').write('')\nprint('a.py' in glob.glob('*.py'), "
    "'later.py' in glob.glob('*.py'))",
    "import os\nos.remove('a.py')\nprint(len(glob.glob('*.py')))",
]]

#: The staging area against an ORDER the program can see. A staged write is
#: merged by APPENDING its name, where CPython sees the file wherever the
#: filesystem put it — and on overlayfs a write anywhere below a lower-layer
#: directory copies up every directory above it, which moves them within their
#: parents' listings. So a listing whose order shows refuses `glob-order` when
#: this run has written, appended to, renamed or removed anything at or below
#: the listed directory. Each row must refuse at 90 with an empty stdout and
#: leave the tree exactly as it found it.
STAGED_ORDER_SHOWN = [G + x for x in [
    "open('d/new.py','w').close()\nprint(glob.glob('d/*'))",
    "import os\nos.remove('d/a.py')\nprint(glob.glob('d/*'))",
    "import os\nopen('d/n.py','w').close()\nos.rename('d/n.py','d/zz.py')\n"
    "print(glob.glob('d/*'))",
    "open('d/a.py','a').write('x')\nprint(glob.glob('d/*'))",
    "open('d/a.py','w').write('x')\nprint(glob.glob('d/*'))",
    # a nested level of a `**` walk, and — the overlay copy-up — a write BELOW
    # the listed directory rather than in it
    "open('d/e/n.py','w').close()\nprint(glob.glob('d/**', recursive=True))",
    "open('d/e/n','w').close()\nprint(glob.glob('d/*'))",
    "open('d/e/n','w').close()\nprint(glob.glob('*'))",
    "open('n.py','w').close()\nfor p in glob.glob('*.py'): print(p)",
    # `key=` reads the input order for ties, so it is not blessed
    "open('d/n','w').close()\nprint(sorted(glob.glob('d/*'), key=len))",
    # a module escape: the walk cannot see which call this is, so every
    # listing's order counts as shown
    "m = glob\nopen('d/n','w').close()\nprint(sorted(m.glob('d/*')))",
    "def f(): return G\nimport glob as G\nopen('d/n','w').close()\n"
    "print(sorted(f().glob('d/*')))",
]]

#: …and the same writes where the order does NOT show, or the listing is of
#: a directory nothing was written at or below: answered, and graded.
STAGED_ORDER_BLIND = [G + x for x in [
    "open('d/new.py','w').close()\nprint(sorted(glob.glob('d/*')))",
    "open('d/new.py','w').close()\nprint(len(glob.glob('d/*')))",
    "open('d/e/n.py','w').close()\nprint(sorted(glob.glob('d/*')))",
    "open('empty/new','w').close()\nprint(glob.glob('d/*'))",
    "open('d/e/n','w').close()\nprint(glob.glob('d/e/f/*'), glob.glob('.hd/*'))",
    # writes AFTER the listing: the loop's own call listed before any of them
    "for p in glob.glob('d/*.py'): open(p,'a').write('#')\n"
    "print(sorted(glob.glob('d/*.py')))",
]]

GRID = (MATCHING + HIDDEN + STRING_ALGEBRA + ORDER_BLIND + STAGED + STAGED_ORDER_BLIND
        + FAITHFUL_WRITES)


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

    A candidate is taken only if it names itself ``engine``, its compiled
    `route::CAPS` knows ``cap``, and the capability set it was BUILT with is
    this tree's. An installed binary from before this capability landed answers
    every grid row with a refusal, which would turn the whole file into green
    skips measuring nothing. Skipping loudly is the honest failure.
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


BINARY = _current(engines.LYPNING_L, "cap-glob")
CORE = _current(engines.LYPNING, "cap-glob")

needs_l = pytest.mark.skipif(
    BINARY is None,
    reason="no lypning-l carrying cap-glob is built (cargo build --release "
           "--no-default-features --features variant-l --target-dir target/variant-l)",
)


def _run(argv: list[str], program: str) -> subprocess.CompletedProcess:
    """One program, in a temp cwd of its own holding the same tree — invariant
    4, and here the rows really do read and write files, so this is what keeps
    the repository out of their way."""
    with tempfile.TemporaryDirectory() as d:
        _tree(d)
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
def test_the_glob_grid_agrees_with_cpython(program: str) -> None:
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
@pytest.mark.parametrize("program", ORDER_FAITHFUL, ids=range(len(ORDER_FAITHFUL)))
def test_an_eager_glob_lists_in_cpythons_order(program: str) -> None:
    """The order, graded: both interpreters run in ONE directory, whose tree
    was created in shuffled order, and must print the same bytes unsorted."""
    with tempfile.TemporaryDirectory() as d:
        _tree(d)
        got = subprocess.run([str(BINARY), "-c", program], capture_output=True,
                             text=True, cwd=d, timeout=60)
        ref = subprocess.run([sys.executable, "-c", program], capture_output=True,
                             text=True, cwd=d, timeout=60)
    assert got.returncode != engines.UNSUPPORTED_EXIT, (
        "an eager glob must be served here\n  program: %r\n  stderr: %r"
        % (program, got.stderr.strip()[:200]))
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "lypning-l lists in a different order from CPython.\n"
        "  program:  %r\n  lypning-l: %r exit %d\n  cpython:   %r exit %d"
        % (program, got.stdout, got.returncode, ref.stdout, ref.returncode))


@needs_l
@pytest.mark.parametrize("program", SERVED_THEN_REFUSED, ids=range(len(SERVED_THEN_REFUSED)))
def test_a_served_call_handed_to_what_this_engine_refuses_rewinds(program: str) -> None:
    got, before, after = _run_snapshot(program)
    assert _refusal_problem(got) is None, (program, got.stderr)
    assert ": glob-order: " not in got.stderr, got.stderr
    assert after == before, (program, before, after)


@needs_l
@pytest.mark.parametrize("program", ORDER_SHOWN, ids=range(len(ORDER_SHOWN)))
def test_a_position_that_would_show_the_match_order_refuses(program: str) -> None:
    """What stays static, stated as an assertion.

    CPython answers every one of these; an answer here would be an order this
    engine did not compute the way CPython does — an eager listing for a lazy
    generator, or a call the walk never saw — at exit 0. And the refusal must
    be exit 90 with an EMPTY stdout: a refusal that landed after the program
    had printed would be exit 1, which the chain never retries. That is why
    the block is in the walk and not in `glob.rs`."""
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got)
    assert problem is None, (
        "this program must refuse, not answer: %s\n  program: %r\n  stderr: %r"
        % (problem, program, got.stderr.strip()[:200])
    )
    assert ": glob-order: " in got.stderr, got.stderr


@needs_l
@pytest.mark.parametrize("program", REFUSED, ids=range(len(REFUSED)))
def test_the_surface_outside_the_subset_refuses_rather_than_guesses(program: str) -> None:
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got)
    assert problem is None, (
        "this program must refuse, not answer — CPython answers it or raises a "
        "message that is not this engine's to write: %s\n  program: %r\n  stderr: %r"
        % (problem, program, got.stderr.strip()[:200])
    )


#: The barrier every row of `AFTER_A_BARRIER` is asked through. `os.mkdir` is
#: the cheapest thing that commits it: the directory is created immediately (a
#: directory has no content to stage), so a refusal that lands afterwards is
#: exit 1 with `NEWD` on disk, and one that lands before it is exit 90 with the
#: cwd untouched. The test reads the cwd, not the message.
BARRIER = "import os\nos.mkdir('NEWD')\n"


def _barriered(call: str) -> str:
    """``call``, with the barrier committed before it and its imports before
    that. A row either brings its own import line or gets the plain one."""
    if call.startswith(("import ", "from ")):
        head, tail = call.split("\n", 1)
        return head + "\n" + BARRIER + tail
    return "import glob\n" + BARRIER + call


def _run_snapshot(program: str) -> tuple[subprocess.CompletedProcess, list[str], list[str]]:
    """One program, with the temp cwd listed before and after it ran."""
    with tempfile.TemporaryDirectory() as d:
        _tree(d)
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
def test_every_refusal_a_glob_call_can_raise_lands_before_the_barrier(
    kind: str, call: str,
) -> None:
    """The class, as one assertion per shape.

    A blessed position SERVES the call, so the program runs — and a refusal it
    reaches after `os.mkdir` has committed the write barrier is exit 1 with the
    directory on disk and nothing on stdout, which the chain never retries and
    the agent that typed the one-liner reads as a failure. The binary WITHOUT
    `cap-glob` refused that program cleanly at 90 and the chain got the answer
    from CPython, so the capability made the program worse.

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
@pytest.mark.parametrize("call", RUNTIME_BACKSTOP, ids=range(len(RUNTIME_BACKSTOP)))
def test_what_stays_a_runtime_refusal_still_refuses(call: str) -> None:
    """The residue, pinned so it stays a residue.

    These are the refusals a walk genuinely cannot decide: a pattern whose value
    is computed, an argument list that is unpacked, and the filesystem's own. A
    refusal here still lands after the barrier — that is the price of admitting
    the shape at all — so what is asserted is only that the backstop is STILL
    THERE. If a later change makes one of these answer instead of refusing, the
    answer would be a wrong one at exit 0, which is strictly worse."""
    program = _barriered(call)
    got, _, _ = _run_snapshot(program)
    assert got.returncode != 0, (
        "this must still refuse — the static half deliberately cannot see it\n"
        "  program: %r\n  stdout: %r" % (program, got.stdout[:200]))
    assert "unsupported: " in got.stderr, got.stderr[:200]


def _minor_of(exe: str) -> str | None:
    try:
        out = subprocess.run([exe, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                             capture_output=True, text=True, timeout=30)
    except OSError:
        return None
    return out.stdout.strip() or None


def _interpreters() -> dict[str, str]:
    """Every CPython minor this host has on PATH (and the system 3.9), by minor."""
    import shutil
    found: dict[str, str] = {}
    for exe in [sys.executable, "/usr/bin/python3"] + [
            "python3.%d" % m for m in range(9, 15)]:
        path = exe if os.path.isabs(exe) else shutil.which(exe)
        if path and os.path.exists(path):
            minor = _minor_of(path)
            if minor and minor not in found:
                found[minor] = path
    return found


@needs_l
@pytest.mark.parametrize("program", STARSTAR_NOT_A_DIR, ids=range(len(STARSTAR_NOT_A_DIR)))
def test_a_starstar_over_a_non_directory_answers_only_for_its_own_minor(program: str) -> None:
    """Answered on a 3.11+ reference, byte-equal to that reference; refused
    (`glob`, exit 90, empty stdout) on an older one, whose CPython answers
    differently. Every interpreter this host has is asked too, so the row also
    pins the split it exists for: 3.11+ agree with each other."""
    ref = engines.reference_minor(BINARY)
    got = _run([str(BINARY)], program)
    pythons = _interpreters()
    answers = {m: _run([exe], program) for m, exe in pythons.items()}
    newer = {m: (a.stdout, a.returncode) for m, a in answers.items()
             if int(m.split(".")[1]) >= 11}
    assert len(set(newer.values())) <= 1, (program, newer)
    if ref is None or int(ref.split(".")[1]) < 11:
        assert _refusal_problem(got) is None, (program, got.stderr)
        assert ": glob: " in got.stderr, got.stderr
        return
    if got.returncode == engines.UNSUPPORTED_EXIT:
        assert _refusal_problem(got) is None, (program, got.stderr)
        pytest.skip("lypning-l refuses this row: %s" % got.stderr.strip()[:160])
    want = answers.get(ref) or _run([sys.executable], program)
    assert (got.stdout, got.returncode) == (want.stdout, want.returncode), (
        program, got.stdout, want.stdout)


def _tree_listing(root: str) -> list[str]:
    out = []
    for base, dirs, files in os.walk(root):
        for n in dirs + files:
            p = os.path.join(base, n)
            out.append((os.path.relpath(p, root), os.path.getsize(p) if os.path.isfile(p) else -1))
    return sorted(out)


@needs_l
@pytest.mark.parametrize("program", STAGED_ORDER_SHOWN, ids=range(len(STAGED_ORDER_SHOWN)))
def test_a_visible_order_over_a_directory_this_run_changed_refuses(program: str) -> None:
    with tempfile.TemporaryDirectory() as d:
        _tree(d)
        before = _tree_listing(d)
        got = subprocess.run([str(BINARY), "-c", program], capture_output=True,
                             text=True, cwd=d, timeout=60)
        after = _tree_listing(d)
    assert _refusal_problem(got) is None, (program, got.stdout[:200], got.stderr)
    assert ": glob-order: " in got.stderr, got.stderr
    assert after == before, (program, set(after) ^ set(before))


@needs_l
def test_a_listing_after_an_early_commit_refuses_rather_than_answering() -> None:
    """Past the output threshold `io::commit` flushes the staging area, and a
    later listing would no longer see the run's write as staged — while the
    commit created it in its own order, not at the program's `open()`. The
    refusal lands after the flush, so it is exit 1 and not 90; what matters is
    that no order is printed."""
    program = G + ("open('d/n','w').close()\nprint('x' * (9 << 20))\n"
                   "print(glob.glob('d/*'))")
    with tempfile.TemporaryDirectory() as d:
        _tree(d)
        got = subprocess.run([str(BINARY), "-c", program], capture_output=True,
                             text=True, cwd=d, timeout=120)
    assert got.returncode in (1, engines.UNSUPPORTED_EXIT), got.returncode
    assert "unsupported: glob-order: " in got.stderr, got.stderr[-300:]
    assert "['d/" not in got.stdout


@needs_l
def test_the_core_routes_every_static_glob_refusal_straight_to_cpython() -> None:
    """The other half of making a refusal static: it stops costing a spawn.

    `engines.route()` asks the CORE, and the core's first blocker for every one
    of these is `module: import glob` — which `lypning-l` answers, so the whole
    table used to route there and be refused. The walk now records the refusal
    that stops EVERY rung in a slot of its own and the verdicts carry it, so the
    core names CPython without a glob implementation of its own. `glob.translate`
    is the row that proves the attribute table has to live in `route.rs`:
    `modules::MODULES` has no `glob` row in the core, so nothing else here could
    have graded it."""
    if CORE is None:
        pytest.skip("no core carrying this tree's capability table is built")
    for _kind, call in AFTER_A_BARRIER:
        program = "import glob\n" + call
        out = subprocess.run([str(CORE), "route", "-c", program],
                             capture_output=True, text=True, timeout=60)
        assert out.stdout.split("\t")[0].strip() == engines.CPYTHON, (
            "the core sent a program every Rust rung refuses to a Rust rung\n"
            "  program: %r\n  route: %r" % (program, out.stdout))


@needs_l
def test_the_walk_and_the_run_answer_the_pattern_question_with_one_scan() -> None:
    """`route::glob_pattern_block` is the only place a pattern is graded.

    The walker runs it over a literal; `glob::call` runs it over the pattern it
    was handed, before a single directory is read. Two consequences are asserted
    here. The refusal is the SAME line either way — the second program's pattern
    is built at runtime, so only the run can see it. And the runtime answer no
    longer depends on what is on disk: `[z-a]` used to reach the matcher only
    when some candidate name got far enough into the pattern to test it, so the
    same program answered `[]` in an empty directory and refused in a full one."""
    static = "import glob\nprint(sorted(glob.glob('[z-a]')))"
    dynamic = "import glob\np = ''.join(['[z', '-a]'])\nprint(sorted(glob.glob(p)))"
    with tempfile.TemporaryDirectory() as d:
        one = subprocess.run([str(BINARY), "-c", static], capture_output=True,
                             text=True, cwd=d, timeout=60)
        two = subprocess.run([str(BINARY), "-c", dynamic], capture_output=True,
                             text=True, cwd=d, timeout=60)
    detail = "a [z-a] range in a pattern"
    assert detail in one.stderr and detail in two.stderr, (one.stderr, two.stderr)
    # …and an EMPTY directory refuses it too, which is what "one scan, before
    # the walk" buys: the answer is a property of the pattern.
    assert one.returncode == engines.UNSUPPORTED_EXIT and one.stdout == "", one


@needs_l
def test_the_capability_is_on_the_larger_variant_only() -> None:
    """The gate this whole file sits behind: the core must still REFUSE `glob`,
    and must route it to the sibling that serves it.

    A capability that leaked into the frozen variant would still pass every grid
    row above — it is the same code — so the byte budget is defended here, by
    asking each binary what it is."""
    core = CORE
    if core is None:
        pytest.skip("no core carrying this tree's capability table is built")
    refused = _run([str(core)], "import glob")
    assert refused.returncode == engines.UNSUPPORTED_EXIT and refused.stdout == ""
    assert refused.stderr.strip() == engines.refusal_line(
        engines.LYPNING, "module", "import glob")

    # …and the core's ROUTER knows which sibling does serve it, which is the
    # half that makes the refusal cost one spawn instead of a CPython one.
    route = subprocess.run([str(core), "route", "-c",
                            "import glob\nprint(len(glob.glob('*.py')))"],
                           capture_output=True, text=True, timeout=60)
    assert route.stdout.split("\t")[0].strip() == engines.LYPNING_L, route.stdout


@needs_l
def test_the_order_block_is_static_so_the_router_can_see_it() -> None:
    """What stays in `route.rs` is what makes the refusal free.

    A lazy `iglob` loop is still a static `glob-order`, so the router sends it
    straight to CPython and this binary, run directly, refuses before
    `os.makedirs()` has done anything. An eager `glob.glob` loop is now
    lypning-l's to answer, and the router says so."""
    def _route(program: str) -> str:
        return subprocess.run([str(BINARY), "route", "-c", program],
                              capture_output=True, text=True, timeout=60).stdout

    lazy = _route("import glob\nfor p in glob.iglob('*.py'): print(p)")
    assert lazy.split("\t")[0].strip() == engines.CPYTHON, lazy
    assert "glob-order" in lazy, lazy
    eager = _route("import glob\nfor p in glob.glob('*.py'): print(p)")
    assert eager.split("\t")[0].strip() == engines.LYPNING_L, eager

    with tempfile.TemporaryDirectory() as d:
        _tree(d)
        got = subprocess.run(
            [str(BINARY), "-c",
             "import glob, os\nos.makedirs('committed')\nprint(list(glob.iglob('*.py')))"],
            capture_output=True, text=True, cwd=d, timeout=60)
        assert _refusal_problem(got) is None, got.stderr
        assert ": glob-order: " in got.stderr, got.stderr
        assert not os.path.exists(os.path.join(d, "committed")), (
            "the refusal landed AFTER os.makedirs")

    # `set()` is not an order-blind wrapper: the set it hands back is a RUNTIME
    # `set-order` refusal here, one statement after `os.mkdir` — which the
    # barrier now takes back (#51), so it is still a clean 90 with the
    # directory gone.
    with tempfile.TemporaryDirectory() as d:
        _tree(d)
        got = subprocess.run(
            [str(BINARY), "-c",
             "import glob, os\nos.mkdir('made_set')\n"
             "print(set(glob.glob('*.py')))"],
            capture_output=True, text=True, cwd=d, timeout=60)
        assert _refusal_problem(got) is None, got.stderr
        assert ": set-order: " in got.stderr, got.stderr
        assert not os.path.exists(os.path.join(d, "made_set")), got


@needs_l
def test_the_core_routes_an_order_showing_program_past_the_variant() -> None:
    """The position rule is in EVERY binary, because the core is the one asked.

    `engines.route()` asks the frozen core, and the core reads `cap-glob` off
    `lypning-l`'s row of `route::SPECTRUM`. While the walk that decides the
    position was behind `cfg(feature = "cap-glob")`, the core saw only
    `module: import glob` and answered `lypning-l` for programs `lypning-l`
    refuses — one wasted spawn each. And the eager `glob.glob` stop was
    REMOVED from the walk, not gated: the stop slot overrides every verdict,
    so a core that kept it would send every one of these to CPython.

    Asked of BOTH binaries with the same programs, because "the core computes
    the same verdict" is the claim, and one binary cannot check it."""
    if CORE is None:
        pytest.skip("no core carrying this tree's capability table is built")
    shown = [
        "import glob\nfor p in glob.iglob('*.py'): print(p)",
        "import glob\nprint(bool(glob.iglob('nope*')))",
        "import glob\nf = glob.glob\nprint(sorted(f('*.py')))",
        "from glob import glob\nh = glob\nprint(h('*'))",
        "import glob as g\nprint(g.iglob('*.py'))",
    ]
    served = [
        "import glob\nprint(sorted(glob.glob('*.py')))",
        "import glob\nprint(len(glob.glob('*.py')))",
        "import glob\nprint(bool(glob.glob('nope*')))",
        "import glob\nprint(sorted(glob.iglob('*.py')))",
        "import glob\nprint(glob.glob('*.py'))",
        "import glob\nfor p in glob.glob('*'): print(p)",
        "import glob\nprint(glob.glob('a*') + glob.glob('b*'))",
        "import glob\nprint(glob.glob('*')[0])",
        "import glob\nprint(glob.glob('**', recursive=True)[-1])",
        "from glob import glob\nprint(glob('*.py'))",
        "import glob\nprint(set(glob.glob('*.py')))",
    ]

    def _engine(binary: Path, program: str) -> str:
        out = subprocess.run([str(binary), "route", "-c", program],
                             capture_output=True, text=True, timeout=60)
        return out.stdout.split("\t")[0].strip()

    for program in shown:
        assert _engine(CORE, program) == engines.CPYTHON, program
        assert _engine(BINARY, program) == engines.CPYTHON, program
    for program in served:
        # The core cannot import `glob`, so it names the sibling that can; the
        # sibling names itself. Neither answer is CPython, which is the point.
        assert _engine(CORE, program) == engines.LYPNING_L, program
        assert _engine(BINARY, program) == engines.LYPNING_L, program


@needs_l
def test_a_program_that_dies_before_its_import_dies_the_same_on_both() -> None:
    """Invariant 10 at the edge the change moved: both binaries used to refuse
    this statically; now both run it, and both must die at the `open()` with
    the same traceback and exit code."""
    if CORE is None:
        pytest.skip("no core carrying this tree's capability table is built")
    program = "open('/nonexistent')\nimport glob\nprint(glob.glob('*'))"
    with tempfile.TemporaryDirectory() as d:
        core = subprocess.run([str(CORE), "-c", program], capture_output=True,
                              text=True, cwd=d, timeout=60)
        big = subprocess.run([str(BINARY), "-c", program], capture_output=True,
                             text=True, cwd=d, timeout=60)
    assert core.returncode == big.returncode == 1, (core, big)
    assert (core.stdout, core.stderr) == (big.stdout, big.stderr)


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
    assert "cap-glob" in table["self_caps"]
    assert {r["name"]: tuple(r["caps"]) for r in table["spectrum"]} == engines.VARIANT_CAPS
    assert {r["cap"]: r["modules"] for r in table["caps"]}["cap-glob"] == ["glob"]
