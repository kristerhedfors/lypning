r"""The `re` SURFACE on lypning-l, as a grid: every program on the binary and on CPython.

lypning-l serves the module, the flag constants as a value with CPython's repr,
`re.escape`, `re.purge` — and a MATCHER: `search`, `match`, `fullmatch`,
`findall`, `finditer`, `compile`, `sub`, `subn` and `split`, on the module and
on `re.Pattern`, over the slice of the pattern language the corpus uses.
`tests/test_pathlib_grid.py` is the shape this follows: the defect is PER
PROGRAM, not per function, and it is the one three capabilities in a row
shipped — a new value reached through a path with no arm for it, answering exit
1 where CPython answers, which the chain never retries.

This file is the instrument `lypning conformance` cannot be: conformance grades
the CORPUS, and 174 of the 213 corpus programs the matcher unlocks die
identically on both engines — on a repo-relative file that is not there in the
sandbox's temp cwd — before they reach their first regex call. Everything below
grades the LANGUAGE instead, one program per row, against the reference CPython.

Named groups, backreferences, lookaround, `\\z`, atomic groups, possessive
quantifiers, bytes patterns and every `re.error` message are outside this slice
and are in `MATCHER_REFUSED`, which asserts they refuse rather than approximate.

Every row must end one of exactly two ways: byte-identical stdout AND the same
exit code as CPython 3.x, or a clean refusal — exit 90, nothing on stdout, one
``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

The traps, each measured on CPython 3.11.15, 3.12.13, 3.13.13 and 3.14.5 before
the code was written, each with rows below:

1. **An `IntFlag` is an int.** `re.I == 2`, `re.I + 1`, `{re.I: 1}[2]`,
   `sorted([re.M, re.I])`, `'ab' * re.I`, `[0, 1, 2][re.I]` and
   `isinstance(re.I, int)` all answer as for the int, and every arithmetic
   operator returns a PLAIN int (`-re.I` is `-2`). Only `| & ^` stay flags.
2. **`str()` of a flag is its repr.** `print(re.I)`, `f'{re.I}'`, `'%s' % re.I`,
   `'%5s' % re.I` and `format(re.I)` all print `re.IGNORECASE`, on every
   CPython. A non-empty format spec MOVED — `int.__format__` from 3.11 on, the
   padded repr on 3.9 and 3.10 — so `format()`, f-string specs and the numeric
   `%` conversions are refused here; `%s`/`%r` with a width are `str()`/`repr()`
   then padding, never `__format__`, and answer.
3. **The repr's member order is DECLARATION order.** `re.I | re.A` prints
   `re.ASCII|re.IGNORECASE`; residue bits print as one `0x…` after the names,
   and a value with no named member prints as `re.RegexFlag(512)`.
4. **The TEMPLATE bit** (`re.I | 1`, `re.I | True`, `re.T`, `re.TEMPLATE`, and
   `~re.I` which sets it) is spelled differently by 3.12 and 3.13+: refused.
5. **`Flag` is a container** — `len(re.I)`, `list(re.I | re.M)`,
   `re.I in re.I | re.M`, `re.I.name`, `re.I.value` are answered by CPython
   and refused here rather than raised at exit 1.

6. **The Unicode tables do not ship, so `\w \d \s \b \B` and
   `re.IGNORECASE` REFUSE on a non-ASCII pattern or subject** — while literals,
   ranges and negated classes are exact on any text and must ANSWER. `re.A`
   makes the table classes ASCII-only and exact everywhere.
7. **The step budget refuses; it never answers `None`.** CPython is exponential
   on `(a+)+$` too, so a budget that answered "no match" would be a wrong
   answer at exit 0. `LINEAR` holds the other edge: every shape `_sre` answers
   in milliseconds over 100,000 characters must answer here too.

And the rule the whole file exists for: a construct outside the slice must
refuse CLEANLY. `print('hi')` before it is the row that holds the commit
barrier.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

R = "import re\n"

#: The import in every spelling, a module that is only ever imported, and the
#: 174-shape: a program that fails identically on both engines BEFORE its first
#: regex call. The `open()` row is that shape — FileNotFoundError at exit 1 on
#: both, from the fresh temp cwd — and it must MATCH, not refuse.
IMPORTS = [
    R + "print('ok')",
    "import re as regex\nprint('ok')",
    "from re import I\nprint(I)",
    "from re import escape, purge\nprint(escape('a.b'), purge())",
    "import re, sys\nprint(len(sys.argv))",
    R + "x = re\nprint(x is re, re is re)",
    "import re\nimport re\nprint('twice')",
    R + "s = open('src/eval.rs').read()\nprint(len(re.findall(r'fn ', s)))",
    R + "import sys\nprint(sys.argv[3])",
    R + "print(1)\n",
]

#: Traps 1, 2 and 3: every constant by both names, the flag algebra, the int
#: half, and the flag in every container and formatting path that prints it.
FLAGS = [R + "print(%s)" % n for n in (
    "re.I", "re.IGNORECASE", "re.M", "re.MULTILINE", "re.S", "re.DOTALL", "re.X", "re.VERBOSE",
    "re.A", "re.ASCII", "re.U", "re.UNICODE", "re.L", "re.LOCALE", "re.DEBUG", "re.NOFLAG",
)] + [R + x for x in [
    "print(re.I|re.M, re.I|8, 8|re.I, re.I|re.M|re.S|re.X|re.A, re.I|512, re.I|re.M|1024, "
    "re.I|re.L, re.I|re.DEBUG, re.I|re.U)",
    "print(re.I&re.M, re.I&3, re.I&re.I, (re.I|re.M)&re.M, re.I&0, re.NOFLAG|re.NOFLAG, "
    "re.I|0, 0|re.I)",
    "print(re.I^re.M, (re.I|re.M)^re.M, re.I^2)",
    "print(int(re.I), float(re.M), bool(re.NOFLAG), bool(re.I), abs(re.I), -re.I, +re.I, "
    "round(re.I))",
    "print(re.I==2, re.I!=2, re.I==re.I, re.I==2.0, re.NOFLAG==0, re.NOFLAG==False, re.I==True, "
    "re.I<re.M, re.M>re.I, re.I<=2, re.I>=3, re.I==re.IGNORECASE)",
    "print(re.I+1, re.I+re.M, re.I*2, re.I-1, re.I/2, re.I//2, re.I%3, re.I**2, re.I<<1, "
    "re.M>>1)",
    "print(sorted([re.M, re.I, re.S]), max(re.I, re.M), min(re.I, re.M), sum([re.I, re.M]))",
    "print(str(re.I), repr(re.I), f'{re.I}', f'{re.I!r}', f'{re.I!s}', '%s' % re.I, "
    "'%r' % re.I, format(re.I))",
    # `%s`/`%r` with a width or precision are `str()`/`repr()` then padding —
    # never `__format__` — so they answer, exactly, on every CPython.
    "print('%5s' % re.I, '%-20s|' % re.I, '%20r' % re.I, '%.3s' % re.I, '%s=%s' % (re.I, re.M))",
    "print({re.I: 1}[2], {2: 1}[re.I], re.I in (2,), 2 in {re.I}, re.I in {2}, re.I in [re.I], "
    "{re.I: 1}, len({re.I, 2}), [re.I, re.M], (re.I,), {'f': re.M})",
    "d = {re.I: 'a', 2: 'b'}\nprint(d, len(d))",
    "print(re.I is re.IGNORECASE, re.I is 2, re.I is None, re.I is not None, "
    "(re.I|re.M) is (re.I|re.M))",
    "print(isinstance(re.I, int), isinstance(re.I, (str, int)), isinstance(re.I, str), "
    "isinstance(re.I, bool))",
    "f = re.I\nf |= re.M\nprint(f)\nf &= re.M\nprint(f)\ng = 0\ng |= re.S\nprint(g)",
    # `pow` is not a lypning builtin, so this row refuses on it (a skip); the
    # row after it is the same builtins without `pow`, so they are measured.
    "print(range(re.I), 'ab'*re.I, [0,1,2][re.I], chr(re.I+63), hex(re.I), bin(re.M), "
    "oct(re.X), divmod(re.M, 3), pow(re.I, 2), bytes(re.I))",
    "print(range(re.I), 'ab'*re.I, [0,1,2][re.I], chr(re.I+63), hex(re.I), bin(re.M), "
    "oct(re.X), divmod(re.M, 3), bytes(re.I), re.I*'ab', [0]*re.I, 'abc'[re.I:], "
    "round(re.I, -1), abs(re.NOFLAG))",
    "import json\nprint(json.dumps(re.I), json.dumps([re.I, {'f': re.M}]), "
    "json.dumps({re.I: 1}))",
    "print(not re.NOFLAG, re.I and 1, re.NOFLAG or 'z', re.I if re.I else 0)",
    "flags = re.M | re.S\nprint(flags, int(flags), flags & re.M, flags & re.I)",
    "print([re.I, re.M].index(8), [re.I, re.M].count(2), (re.I, re.M) == (2, 8))",
    "print(re.I | re.M | re.S | re.X | re.A | re.U | re.DEBUG | re.L, re.I & True, "
    "re.I | False, re.NOFLAG | 2**31)",
]]

#: `re.escape`, whose escaped set is exactly CPython 3.7+'s: the printable-ASCII
#: row is the trap-grid row verbatim, and non-ASCII copies through.
ESCAPE = [R + x for x in [
    "print([re.escape(chr(i)) for i in range(32, 127)])",
    "print(re.escape('a1_\\u00e9-'), re.escape(''), repr(re.escape('\\x00\\x07\\x7f\\x80\\xa0')), "
    "re.escape('h\\u00e9llo'), re.escape('a.b*c?'))",
    "print(repr(re.escape('\\t\\n\\r\\v\\f')), repr(re.escape('\\\\')), re.escape('a b'), "
    "len(re.escape('()')), re.escape('x' * 100).count('x'))",
    "print(re.escape(str(1)), re.escape('\\u65e5\\u672c\\u8a9e \\u6f22'), "
    "re.escape('!\\\"%\\',/:;<=>@_`'))",
    "print(re.escape('a') + re.escape('.'), re.escape('.').encode(), 'x' in re.escape('x.y'))",
    # The row above with `\x80` in it refuses on the engine's own `repr-unicode`
    # (U+0080's printability is CPython's table, not ours); this one measures the
    # same code points without asking for their repr.
    "print(repr(re.escape('\\x00\\x07\\x7f')), re.escape('\\x80\\xa0') == '\\x80\\xa0', "
    "len(re.escape('\\x80\\xa0')), re.escape('\\x80\\xa0').encode())",
]]

PURGE = [
    R + "print(re.purge())",
    R + "re.purge()\nprint('done')",
    R + "print(re.purge() is None)",
]

#: The flag as the int partner of things that are not flags, each measured on
#: CPython 3.11–3.14 and each once an exit 1 here: a BOOL on the left of `| & ^`
#: gives a plain int (`bool.__or__` runs first, `RegexFlag` is no subclass of
#: `bool`), a flag on the left of a bool stays a flag; `in range(…)` and
#: `in b"…"` see the int (`re.A in b"x"` is the same ValueError as 256's); and
#: `json.dumps(indent=…)` is `' ' * indent` for anything that is not a str, so
#: `indent=re.I` is two spaces and `indent=True` one.
INT_PARTNERS = [R + x for x in [
    "print(False | re.I, True & re.I, False ^ re.I, True | re.M, True ^ re.I, False & re.I)",
    "print(re.I | False, re.M & True, re.I ^ False, type(False | re.I) is int)",
    "print(re.I in range(5), re.I in range(2), re.M in range(0, 100, 8), re.NOFLAG in range(1), "
    "re.I in range(3, 1, -1))",
    "print(re.I in b'\\x02', re.I in b'x', re.NOFLAG in b'\\x00a', re.M in b'', "
    "re.I not in b'\\x02')",
    "print(re.A in b'x')",
    "print(re.A in bytes([1, 0]))",
    "import json\nprint(json.dumps([re.I], indent=re.I))",
    "import json\nprint(json.dumps({'a': [1, 2]}, indent=True))",
    "import json\nprint(json.dumps([1], indent=False), json.dumps([1], indent=re.NOFLAG))",
    "import json\nprint(json.dumps({'k': re.M, 'i': [re.I]}, indent=re.I, sort_keys=True))",
    "import json\nprint(json.dumps([1], indent=1.5))",
]]


#: The matcher's read-only half: `search`, `match`, `fullmatch`, `findall`,
#: `finditer`, `compile`, and the same five as `Pattern` methods. Every row
#: prints spans and groups rather than the match OBJECT where the object is not
#: the point, because a span that is one off is a wrong answer and a repr that
#: is one character off is a different bug.
MATCHER = [R + x for x in [
    "print(re.search('b','ab').span(), re.match('b','ab'), re.fullmatch('ab','ab').span(), "
    "re.findall('a','aba'), [m.span() for m in re.finditer('a','aba')])",
    "print(re.search(r'\\d+','a12b345').group(), re.findall(r'\\d+','a12b345'), "
    "re.findall(r'\\w+','ab-cd ef_gh 12ij'), re.findall(r'\\s+','a \\t\\nb'), re.findall(r'\\S+','a b'))",
    "m=re.search(r'(\\w+)=(\\d+)','k=12'); print(m.group(), m.group(0), m.group(1), m.group(2), "
    "m.groups(), m.span(), m.span(1), m.start(2), m.end(2), m[0], m[1])",
    "print(bool(re.search('a','a')), bool(re.search('z','a')), not re.search('z','a'), "
    "re.search('a','a') and 1, re.search('z','a') or 2, [bool(x) for x in [re.match('a','a')]])",
    "print(re.findall('ab','abab'), re.findall(r'a(b)?','a ab'), re.findall(r'(?:a)(b)','ab'), "
    "re.findall(r'(a)(b)?','a ab'), re.findall(r'((a)b)','ab'), re.findall(r'()','ab'), "
    "re.findall(r'(a)()','a'))",
    "p=re.compile(r'(\\w+)\\s*=\\s*(\\S+)'); print(p.findall('a = 1\\nb=2\\n c =3'), "
    "[m.group(1,2) for m in p.finditer('a = 1\\nb=2')], p.match('a=1').groups(), "
    "p.search('  a=1').span(), p.fullmatch('a=1') is not None, p.groups, p.pattern)",
    "print(list(re.finditer('x','')), [(m.start(), m.end()) for m in re.finditer('','ab')], "
    "sum(1 for _ in re.finditer('x','xxx')), len(list(re.finditer(r'\\d','a1b2'))))",
    "print([m.group() for m in re.finditer(r'\\d+','a1b22c333')], "
    "[(m.start(), m.group()) for m in re.finditer('o','foo boo')], "
    "[m.groups() for m in re.finditer(r'(\\w)(\\d)','a1 b2')])",
    "print(next(re.finditer('a','a')).span(), list(re.finditer('a','aa'))[1].span())",
    "print(re.match('a','ba'), re.search('a','ba').span(), re.match('^a','ba'), "
    "re.search(r'\\Aa','ba'), re.match('.*a','ba').group(), bool(re.match('bar','foobar')), "
    "bool(re.search('bar','foobar')))",
    "print(re.match(r'a|ab','ab').group(), re.fullmatch(r'a|ab','ab').group(), "
    "re.findall(r'a|ab','ab'), re.findall(r'ab|a','ab'), re.match(r'a*?','aaa').group(), "
    "re.fullmatch(r'a*?','aaa').group(), re.fullmatch(r'(a|ab)(c|bcd)(d*)','abcd').groups(), "
    "re.match(r'(?:a|ab)c','abc').group(), re.fullmatch(r'a{1,2}','aaa'), "
    "re.fullmatch(r'(?:a+)+$','aaa').group(), re.fullmatch(r'a$','a\\n'))",
]]

#: The empty-match rules, which are the whole of `_sre`'s scan loop: an empty
#: match right after a NON-empty one at the same position is produced, two empty
#: matches at one position are not, and the attempt after an empty one starts a
#: character later. Pre-3.7 rules give `-b-c-` where 3.7+ gives `-b--c-`.
EMPTY = [R + x for x in [
    "print(re.findall(r'x*','axxb'), re.findall(r'a*','baaac'), "
    "[m.span() for m in re.finditer(r'a*','baaac')], re.findall(r'\\b','ab cd'), "
    "re.findall(r'a*?','aa'), re.findall(r'.*?','ab'), re.findall(r'.*','ab\\ncd'))",
    "print(re.sub(r'x*','-','abxd'), re.sub(r'a*','-','baaac'), re.sub('','-','abc'), "
    "re.subn(r'a*','X','baaac'), re.sub(r'a|','X','ab'), re.sub(r'|a','X','ab'), "
    "re.sub(r'a|b?','X','ab'), re.sub(r'\\b','|','ab cd'), re.sub(r'\\b|$','|','ab'), "
    "re.sub(r'x*','y',''))",
    "print(re.split(r'x*','axbc'), re.split(r'\\b','a b'), re.split(r'','abc'), "
    "re.split(r'\\s*','a b'), re.split(r'\\W*','a, b'), re.split(r'x',''), re.split(r'',''), "
    "re.split(r'(x)','x'))",
    "print(re.sub('x*','-','abxd',count=2), re.sub('x*','-','abxd',count=1), "
    "re.subn('','X','ab',count=2), re.sub(r'','X','ab',count=3), re.sub(r'','X','ab',count=4), "
    "re.subn(r'x*','X','ab',count=5), re.split('x*','axbxc',maxsplit=2), "
    "re.split(r'','ab',maxsplit=1), re.split(r'','ab',maxsplit=3), re.split(r'x*','axb',maxsplit=3))",
]]

#: A group that did not participate is `None` from `group()`, the DEFAULT from
#: `groups()`, `''` from `findall` and from a `sub` template, `None` kept in
#: `split`, and `(-1, -1)` from `span()`. Five renderings of one fact, and an
#: `Option<String>` that stored `''` for it would be wrong in four of them.
GROUPS = [R + x for x in [
    "m=re.match(r'(a)(b)?(c*)','ac'); print(m.group(2), repr(m.group(3)), m.groups(), "
    "m.group(0,1,2,3), m.groups('DEF'), m.span(2), m.start(2), m.end(2), m.span(3))",
    "print(re.match(r'(a)(b)','ab').groups(), re.match('a','a').groups(), "
    "re.match(r'(a)','a').group(0,0), re.match('a','a').group(0,), "
    "re.match(r'(a)','a').group(1,1,0), re.match(r'(a)','a').group(True), "
    "re.match(r'(a)','a')[True], re.match(r'(a)','a')[0])",
    "print(re.split(r'(,)','a,b,c'), re.split(r'(,)|(;)','a,b;c'), re.split(r'(x)?,','a,b'), "
    "re.split(r'(a)|b','xaxbx'), re.split(r'(a)(b)?','a'), re.split(r'((,))','a,b'), "
    "re.split(r'(a)?(b)?','xaby'), re.split(r'(\\W+)','a, b;;c'))",
    "print(re.findall(r'(a)|(b)','ab'), re.findall(r'(a)|b','ab'), re.sub(r'(a)(b)?',r'[\\2]','a'))",
    "print(re.match(r'(a|b)*','ab').groups(), re.match(r'(?:(a)|(b))+','ab').groups(), "
    "re.match(r'(?:(a)|(b))+','aba').groups(), re.match(r'(a)?(?:ab)','ab').groups(), "
    "re.match(r'(?:(a)b|ac)','ac').groups(), re.match(r'((a)|(b))*','ab').groups(), "
    "re.match(r'((a)|(b))*','ba').groups(), re.match(r'((a)|(b))+?','ab').groups(), "
    "re.match(r'(a*)*(b)','aab').groups(), re.match(r'((a)*)*','aa').groups())",
    "print(re.match(r'(a*)*','aa').groups(), re.match(r'(a*)+','b').groups(), "
    "re.match(r'(a|)+','aa').groups(), re.match(r'(a?){3}','aa').groups(), "
    "re.match(r'(a?)*?b','ab').groups(), re.match(r'(a*)+?b','aab').groups(), "
    "re.match(r'(?:a|)*b','aab').group(), re.findall(r'(a*)*','aa'), re.findall(r'(?:a*)*','aa'), "
    "re.match(r'(a*?)*','aa').groups(), re.match(r'(a??)*','a').groups(), "
    "re.match(r'()*','a').groups(), re.match(r'(a|b?)*','ab').groups())",
    "print(re.match(r'(?:)','a').span(), re.match(r'a|','b').span(), re.match(r'|a','a').span(), "
    "re.match(r'(|a)+','aa').span(), re.match(r'(a|)*b','aab').span(), re.match(r'(?:)*','a').span(), "
    "re.match(r'(?:|a)*b','aab').group(), re.match(r'(?:a|b?)*c','abc').group())",
]]

#: `$` matches before ONE trailing newline and `\Z` does not; `^` and `\A` test
#: index 0 of the REAL string while `$` and `\Z` see `endpos` as the end; `.`
#: excludes `\n` and nothing else; `\b` looks at the character before `pos`.
ANCHORS = [R + x for x in [
    "print(re.search(r'abc$','abc\\n'), re.search(r'abc\\Z','abc\\n'), re.search(r'abc$','abc\\n\\n'), "
    "re.fullmatch(r'abc','abc\\n'), re.fullmatch(r'abc$','abc\\n'))",
    "print(re.search(r'\\d+$','x12\\n').group(), re.match(r'\\d+$','12\\n').span(), "
    "re.search(r'\\d+\\Z','x12\\n'), re.fullmatch(r'\\d+','12\\n'), re.search('$','a\\n').span())",
    "print(re.findall(r'$','a\\n'), re.findall(r'^','a\\nb',re.M), re.findall(r'$','a\\nb\\n',re.M), "
    "re.findall(r'(?m)^','a\\n\\n'), re.findall(r'^$',''), re.findall(r'\\A\\Z',''))",
    "print(re.sub('$','X','a\\n'), re.sub('^','X','a\\nb',flags=re.M), re.sub('$','X','a\\nb\\n',flags=re.M), "
    "re.sub(r'\\Z','X','a\\n'), re.sub(r'\\A','X','ab'), re.sub(r'(?m)^$','E','\\n\\n'), "
    "re.sub(r'\\n$','','a\\n\\n'))",
    "print(re.findall(r'\\w+$','ab\\ncd\\n'), re.findall(r'(?m)\\w+$','ab\\ncd\\n'), "
    "re.findall(r'\\w+\\Z','ab\\ncd\\n'), re.split(r'(?m)^','a\\nb\\n'), re.match(r'a\\Zb','a\\nb'), "
    "re.match(r'(?m)a$\\nb','a\\nb').group())",
    "print(re.findall(r'a.c','a\\nc abc a\\rc a\\x85c'), re.findall(r'.','a\\nb',re.S), "
    "re.findall(r'a[^b]c','a\\nc'), re.findall(r'a\\Dc','a\\nc'), re.findall(r'.+','ab\\ncd'))",
    "print(re.match(r'a.*b','axxb\\nb').group(), re.match(r'(?s)a.*b','axxb\\nb').group(), "
    "re.match(r'a.*?b','axxbb').group(), re.match(r'a(.*)b','aabab').group(1), "
    "re.match(r'a(.*?)b','aabab').group(1), re.findall(r'^.*$','ab\\ncd',re.M), "
    "re.findall(r'^.*$','ab\\ncd'))",
    "print(re.findall(r'\\bfoo\\b','foo foobar bar_foo foo.bar foo9 9foo'), "
    "re.findall(r'\\bfoo\\b','foo_foo'), re.findall(r'\\b\\d+\\b','a1 22 b3'), "
    "re.findall(r'\\b','a'), re.findall(r'\\b','  '), re.findall(r'\\b',''), "
    "re.findall(r'\\B','ab c'), re.findall(r'\\Bfoo\\B','afooa foo'))",
]]

#: `]` first is a literal, `-` at either end is a literal, `\b` inside a class
#: is a backspace, and `[a-c-e]` is three members. `[[a]` and `[a&&b]` compile
#: (CPython warns on stderr, which is not graded) and match literally.
CLASSES = [R + x for x in [
    "print(re.findall(r'[]a]',']a'), re.findall(r'[^]a]',']ab'), re.findall(r'[a-]','a-'), "
    "re.findall(r'[-a]','a-'), re.findall(r'[\\w-]','a-_'), re.findall(r'[.]','.a'), "
    "re.findall(r'[\\\\]','\\\\'), re.findall(r'[\\]]',']'), re.findall(r'[\\b]','\\x08b'), "
    "re.findall(r'[a-c-e]','a-de'), re.findall(r'[\\]-a]',']^a'))",
    "print(re.findall(r'[\\x41-\\x43]','ABCD'), re.findall(r'[\\n]','\\n'), re.findall(r'[\\s\\S]','a\\n'), "
    "re.findall(r'[^\\d\\s]','1 a'), re.findall(r'[a-z0-9_]','aZ_9'), re.findall(r'[^^]','^a'), "
    "re.findall(r'[\\^]','^'), re.findall(r'[$]','$'), re.findall(r'[|]','|'), re.findall(r'[(]','('))",
    "print(re.findall(r'[[a]','[a'), re.findall(r'[a&&b]','a&b'), re.findall(r'[a||b]','a|b'), "
    "re.findall(r'[a~~b]','a~b'))",
    "print(re.findall(r'\\0','\\x00'), re.findall(r'\\00','\\x00'), re.findall(r'\\000','\\x00'), "
    "re.findall(r'\\012','\\n'), re.findall(r'\\07','\\x07'), re.findall(r'\\101','A'), "
    "re.findall(r'\\x41','A'), re.findall(r'\\U00000041','A'), re.findall(r'\\a\\f\\v\\t\\n\\r','\\a\\f\\v\\t\\n\\r'))",
    "print(re.findall(r'\\-\\.\\ \\#\\\"','-. #\"'), re.findall(r'\\/\\@\\%\\!','/@%!'), "
    "re.findall(r'\\_','_'), re.findall(r'\\<\\>','<>'), re.compile(r'\\_').pattern)",
    "print(re.compile('a{'), re.compile('a{,'), re.compile('a{1'), re.compile('a{1,2'), "
    "re.compile('a{,3}'), re.compile('{'), re.compile('a{}'), re.compile('a{x}'), "
    "re.compile('a{ 1}'), re.compile(r'a{,}').pattern, re.compile('a{1000000}').pattern)",
    "print(re.findall(r'a{,2}','aaa'), re.findall(r'a{2}','aaaaa'), re.findall(r'a{2,}','aaaaa a'), "
    "re.findall(r'a{1,2}?','aaa'), re.findall(r'a{','a{'), re.findall(r'a{1,x}','a{1,x}'), "
    "re.match(r'a{,}','aaaa').span(), re.match(r'a{,}b','a{,}b'), re.match(r'a{0}','a').span(), "
    "re.match(r'(a){0}','a').groups(), re.match(r'(a){2,3}','aaaa').span(), "
    "re.match(r'a{2,3}?','aaaa').span(), re.match(r'a{2,}?','aaaa').span())",
]]

#: `pos` and `endpos` are clamped into `[0, len]` and are never Python's
#: negative indices; `endpos < pos` is not raised to `pos` either — the search
#: loop simply never runs, while an anchored `match` at `pos` still does.
POSENDPOS = [R + x for x in [
    "print(re.compile(r'a').search('abc',-5).span(), re.compile(r'c').search('abc',0,100).span(), "
    "re.compile(r'a').search('abc',0,-1), re.compile(r'c').search('abc',5), "
    "re.compile(r'').search('abc',5), re.compile(r'').search('abc',3).span(), "
    "re.compile(r'').search('abc',2,1), re.compile(r'b').search('abc',2,1), "
    "re.compile('a').search('abc',True), re.compile('').match('abc',2,1).span())",
    "print(re.compile('a').search('abc',pos=1,endpos=2), re.compile('a').search('abc',endpos=2).span(), "
    "re.compile('').match('abc',endpos=0).span())",
    "print(re.compile(r'^b').search('ab',1), re.compile(r'\\bb').search('ab',1), "
    "re.compile(r'\\Ab').search('ab',1), re.compile(r'b$').search('abc',0,2).span(), "
    "re.compile(r'a\\b').search('ab',0,1).span(), re.compile(r'\\Ba').search('ba',1).span(), "
    "re.compile(r'\\ba').search('ba',1))",
    "print(re.compile(r'\\Z').findall('ab',0,1), re.compile('$').findall('ab',0,1), "
    "re.compile('^a').findall('aa',1), re.compile('(?m)^a').findall('a\\na',1), "
    "re.compile(r'a').match('ba',1).span(), re.compile(r'a').fullmatch('bab',1,2), "
    "re.compile(r'a').findall('aXa',1), re.compile(r'a').findall('aXa',0,1), "
    "[m.span() for m in re.compile(r'a').finditer('aXa',1)])",
    "m=re.compile(r'(a)(b)').search('xab',1); print(m.pos, m.endpos, m.span(1), m.start(2), "
    "m.span(0), m.string, m.re.pattern, m.groups(), m.group(1,2))",
]]

#: Literals, ranges and negated classes compare CODE POINTS and are exact on any
#: text, so these must ANSWER — a blanket "refuse non-ASCII" would lose them —
#: and every offset is a code-point offset, which a byte-offset engine gets
#: wrong at exit 0.
NONASCII = [R + x for x in [
    "print(re.search('b','\\u00e9b').span(), re.compile('b').search('\\u00e9b',1).span(), "
    "[m.start() for m in re.finditer('.','a\\u00e9\\U0001F600b')], "
    "re.search('\\U0001F600','a\\u00e9\\U0001F600b').span(), re.match('a.b','a\\U0001F600b').end(), "
    "re.compile('b').search('a\\U0001F600b',2).span(), re.compile('.').findall('a\\u00e9\\U0001F600b',1,3))",
    "print(re.split('\\u00e9','a\\u00e9b\\u00e9c'), re.sub('.','-','a\\u00e9\\U0001F600'), "
    "re.findall('..','a\\u00e9\\U0001F600b'), re.sub('l','L','h\\u00e9llo'), re.split('l','h\\u00e9llo'), "
    "re.search('\\u00e9','h\\u00e9llo').start(), re.escape('h\\u00e9llo'))",
    "print(re.match(r'[\\u00e9]','\\u00e9').span(), re.match(r'[\\u00e9-\\u00ea]','\\u00ea').span(), "
    "re.match(r'[^\\u00e9]','e').span(), re.match(r'[^\\u00e9]','\\u00e9'), "
    "re.match(r'\\u00e9+','\\u00e9\\u00e9\\u00e9').end(), re.match(r'(?:\\u00e9|e)*','\\u00e9e\\u00e9').end(), "
    "re.match(r'[\\U0001F600-\\U0001F602]','\\U0001F601').span(), re.match(r'[^ ]+','h\\u00e9llo w\\u00f6rld').group())",
    # `re.A` makes the table classes ASCII-only, which is exact on ANY subject.
    "print(re.findall(r'\\d','1\\u0663',re.A), re.findall(r'\\w+','h\\u00e9llo w\\u00f6rld',re.A), "
    "re.findall(r'(?a)\\b\\u00e9','a\\u00e9'), re.findall(r'(?ai)K','k'), re.findall(r'(?ai)k','kK'))",
]]

#: `sub`, `subn`, `split`: the templates (which are NOT the pattern's escape
#: grammar), the callable repl, `count=`/`maxsplit=`, and the trap that the
#: fourth positional of `sub` is `count` and not `flags`.
SUBSPLIT = [R + x for x in [
    "print(re.sub(r'(a)',r'\\1\\1','xa'), re.sub(r'(a)',r'\\g<1>0','a'), re.sub(r'a',r'\\g<0>\\g<0>','a'), "
    "re.sub(r'(a)(b)',r'\\2\\1','ab'), re.sub('(a)',r'\\g<01>','a'), "
    "re.sub(r'(\\w+) (\\w+)',r'\\2 \\1','hello world foo bar'))",
    "print(repr(re.sub(r'a',r'\\n\\t\\\\','a')), re.sub(r'a','\\\\n','a').encode(), re.sub(r'a',r'\\\\n','a'), "
    "re.sub(r'(a)','\\\\1\\\\1','a'), re.sub(r'a',r'\\\\\\\\','a'))",
    "print(re.sub(r'a',r'\\.','a'), re.sub(r'a',r'\\-','a'), re.sub(r'a',r'\\ ','a'), "
    "re.sub(r'a',r'\\_','a'), re.sub(r'a',r'\\a\\f\\v\\b\\r','a').encode(), "
    "re.sub(r'a',r'\\00\\0\\07\\08','a').encode(), re.sub(r'(a)',r'\\100','a').encode(), "
    "re.sub(r'(a)(b)?',r'[\\2]','a'))",
    "print(re.sub(r'(a)(b)(c)(d)(e)(f)(g)(h)(i)(j)(k)',r'\\11\\1','abcdefghijk'), "
    "re.match(r'(a)(b)(c)(d)(e)(f)(g)(h)(i)(j)(k)','abcdefghijk').group(11), "
    "re.sub(r'(a)|(b)',r'[\\1|\\2]','ab'))",
    "print(re.sub(r'a',lambda m: m.group(0).upper()+'\\\\1','a'), re.sub(r'a',lambda m: None,'aXa'), "
    "re.sub(r'a',lambda m: 'X','aaa',count=2), re.subn(r'a',lambda m: m.group().upper(),'aba'), "
    "re.sub(r'a',lambda m: str(m.span()),'bab'), "
    "re.sub(r'(\\d+)',lambda m: str(int(m.group(1))*2),'a1b22'))",
    "print(re.sub(r'a','X','aaa',count=2), re.subn(r'a','X','aaa',count=1), "
    "re.sub(r'a','X','aAa',flags=re.I), re.sub(r'a','X','aaa',count=0), re.sub(r'a','X','aaa',count=10), "
    "re.subn(r'x','y','abc'), re.sub(r'a','b','aXa',count=1))",
    # The DeprecationWarning CPython 3.13+ prints for a positional count is on
    # stderr, which is not graded; the VALUE is, and it is a count.
    "print(re.sub(r'a','X','aAa',re.I), re.sub(r'X','y','axbxc',re.I), re.sub(r'a','X','aAa',0,re.I), "
    "re.sub(r'a','X','aaa',-1), re.split(r'a','bAbab',re.I), re.split(r'X','axbxc',0,re.I), "
    "re.split(r'a','bab',-1), re.split(r'a','bab',0), re.split(r'x','axbxc',1,0), "
    "re.findall(r'a','aA',re.I), re.findall(r'a','aA',flags=re.I))",
    "print(re.split(r',','a,b,c',maxsplit=1), re.split(r',',',a,'), re.split(r',','a,,b'), "
    "re.split(r',+','a,,b,'), re.split(r'\\s+',' a b '), re.split(r'(\\s)','a b'), "
    "re.split(r'\\s+','',maxsplit=1), re.split(r',','a,b,c,d',maxsplit=5), "
    "re.split(r'(,)','a,b,c',maxsplit=1))",
    "p=re.compile('a',re.I); print(p.sub('X','aA',count=1), p.sub('X','aA',1), p.subn('X','aA'), "
    "p.split('bAb'), p.split('bAbAb',1), p.findall('aA'), p.fullmatch('A').span(), p.flags, p, p.pattern)",
    "print(re.match(r'a','a',0), re.match(r'a','a',re.NOFLAG).span(), re.search(r'a','a',re.I).span(), "
    "re.fullmatch(r'a','A',re.I).span(), [m.span() for m in re.finditer(r'a','A',re.I)], "
    "re.split(r'a','bAb',flags=re.I), re.subn(r'a','x','A',flags=re.I), re.compile(r'a',flags=re.I), "
    "re.compile(pattern=r'a',flags=re.I), re.findall(pattern='a',string='a',flags=0), "
    "re.sub(pattern='a',repl='b',string='a',count=1,flags=0), "
    "re.split(pattern='a',string='bab',maxsplit=1,flags=0))",
]]

#: The flags that are served, as arguments and as a leading `(?imsxau)`, plus
#: VERBOSE's tokenizer rules — where whitespace is skipped and where it is not.
FLAGS2 = [R + x for x in [
    "print(re.findall(r'(?i)[k]','K'), re.findall(r'(?i)[a-z]+','AbC'), re.findall(r'(?i)[^a-z]+','AbC1'), "
    "re.findall(r'(?i)\\w','A'), re.match(r'(?i)k+','kK').group(), re.sub(r'(?i)k','-','kK'), "
    "re.split(r'(?i)k','aKb'), re.findall(r'(?i)K+','kK'), re.findall(r'(?i)[Kx]','k'), "
    "re.match(r'(?i)[e-l]','K').span(), re.match(r'(?i)[m-z]','K'), re.match(r'(?i)[^k]','K'))",
    "print(re.findall(r'a b','ab',re.X), re.findall(r'a\\ b','a b',re.X), re.findall(r'[ ]',' ',re.X), "
    "re.findall(r'a#c\\nb','ab',re.X), re.findall(r'a\\#b','a#b',re.X), re.findall(r'[#]','#',re.X), "
    "re.findall('(?x) a b','ab'), re.findall(r'(?x)a b#c\\n c','ac'), re.findall(r'(?x)#a\\n','a'), "
    "re.findall(r'(?x)a(?#comment)b','ab'), re.findall(r'a(?# c)b','ab'))",
    "print(re.findall(r'(?x)a \\d {2}','a12'), re.findall(r'(?x)a \\d { 2 }','a12'), "
    "re.findall(r'(?x)a \\d{ 2 }','a{ 2 }'), re.findall(r'(?x)a \\d{2 }','a1{2 }'), "
    "re.findall(r'(?x)a {2}','aa'), re.findall(r'(?x)a{ 2}','a{ 2}'), "
    "re.findall(r'(?x)a { 1,2 }','a{1,2}'), re.findall('(?x)a\\tb','ab'), "
    "re.findall('(?x)a\\fb\\vb','abb'), re.findall('(?x)a\\x1cb','a\\x1cb'))",
    "print(re.compile('a').flags, re.compile('a',re.I).flags, re.compile('a',re.I|re.M).flags, "
    "re.compile(r'(?a)\\w').flags, re.compile('a').groups, re.compile(r'(a)(?:b)').groups, "
    "re.compile('a',re.I).flags == re.I|re.U, re.compile('a',re.I|re.M).flags & re.I, "
    "re.compile('a').flags & re.I)",
    "print(re.compile('a',re.I), re.compile('a',re.I|re.M), re.compile('a',re.I|re.M|re.S|re.X|re.A), "
    "re.compile('a',re.U), re.compile('a',re.U|re.I), re.compile('a',re.A|re.X|re.M), "
    "re.compile('a',re.NOFLAG), re.compile('a',2|8), re.compile(r'(?i)a'), re.compile('(?s)a'), "
    "re.compile(r'(?i)a',re.M), re.compile(r'(?u)a'), re.compile(r'(?a)a'), re.compile('(?i)(?m)a'))",
]]

#: The two new values in every generic path a value can take. `is`, `==`, the
#: hash, the truncating reprs, and the compile cache that makes
#: `re.compile('a') is re.compile('a')` True.
OBJECTS = [R + x for x in [
    "print(re.compile('a') is re.compile('a'), re.compile('a') == re.compile('a'), "
    "re.compile('a') == re.compile('a',re.I), re.compile('a',re.U) is re.compile('a'), "
    "re.compile('a',re.U) == re.compile('a'), re.compile('a',2) is re.compile('a',re.I), "
    "re.compile(re.compile('a')) is re.compile('a'))",
    "print({re.compile('a'): 1}, len({re.compile('a'), re.compile('a')}), re.compile('a') == 'a', "
    "re.compile('a') != 'a', hash(re.compile('a')) == hash(re.compile('a')))",
    "re.purge(); p=re.compile('a'); re.purge(); print(p == re.compile('a'), p is re.compile('a'))",
    "m=re.match('a','a'); print(m is None, m == None, m != None, bool(m), not m, m is m, m == m, "
    "m.re is re.compile('a'), m.string, re.match('a','a') == re.match('a','a'), "
    "re.match('a','a') is re.match('a','a'))",
    "p=re.compile('a'); print(p.search('ba',1).re is p, p.search('a').string, p.search('a').pos, "
    "p.search('a').endpos, p.match('a').re.pattern)",
    # The truncating reprs: 50 characters of the match's repr and 200 of the
    # pattern's, closing quote and all — which is why 50 a's print 49.
    "print(re.match('.*','a'*48), re.match('.*','a'*49), re.match('.*','a'*50), "
    "re.match('.*','a'*51), re.match('.*','a'*48+chr(39)), re.match('(?s).*','\\t'*30))",
    "print(re.compile('a'*199), re.compile('a'*200), re.compile('a'*201), re.compile('a'*198+chr(39)))",
    "print(re.compile(r'a\\.b'), re.compile('a\\nb'), re.compile(\"it's\"), re.compile('a\"b\\'c'), "
    "repr(re.compile('a')), str(re.compile('a')), '%s' % re.compile('a'), '%s' % re.match('a','a'))",
    "m=re.match('ab','ab'); print(str(m), repr(m), f'{m}', f'{m!r}', format(m))",
    "print(len(re.compile('a')))",
    "print(-re.compile('a'))",
    "print(isinstance(re.match('a','a'), str), isinstance(re.compile('a'), int))",
]]

#: The step budget must never fire on a shape `_sre` answers in milliseconds:
#: an iterative machine with a counted one-character repeat is linear on all of
#: these, and a frame per character would be a stack overflow instead.
LINEAR = [R + x for x in [
    "print(re.match(r'(?:a|b)*c','ab'*200000+'c').end(), len(re.findall(r'(?:a|b)','ab'*200000)), "
    "re.match(r'(?:a*)*c','a'*100000+'c').end(), len(re.sub(r'(?:x|y)+','','xy'*200000)), "
    "re.match(r'(a+)+b','a'*100000+'b').end(), re.match(r'(.*)*b','a'*100000+'b').end(), "
    "re.match(r'(a|a)*b','a'*100000+'b').end(), re.match(r'(?:x+x+)+y','x'*100000+'y').end(), "
    "re.match(r'^(\\w+\\s?)*$','a '*50000).end(), re.match(r'(a*)*b','a'*100000+'b').end())",
    "s='x'*100000; print(len(re.sub('x','yy',s)), len(re.findall('x',s)), len(re.split('x',s)), "
    "re.match('x*$',s).end(), re.match('(x)*$',s).end(), re.match('(x|y)*$',s).end(), "
    "re.match('.*$',s).end(), re.match('(?:x?)*$',s).end(), re.match('(?:x+)*$',s).end(), "
    "re.match('(?:x+?)+$',s).end())",
    "s='ab'*100000; print(re.match(r'(?:a|b)*?$',s).end(), re.match(r'(?:ab)+?$',s).end(), "
    "re.match(r'(?:(a)|(b))*$',s).groups(), re.fullmatch(r'(?:a|b)*',s).end(), "
    "len(re.findall(r'(a)(b)',s)), re.search(r'b$',s).span())",
]]

#: What the corpus actually types. The mine of 2026-09-04 over 248 blocked
#: programs: `search` in 55, `findall` in 47 (a quarter of them with one group),
#: `compile` in 27, `sub` in 14, `finditer` in 14, and `.group(n)` in 31.
IDIOMS = [R + x for x in [
    "t='def foo(a, b):\\n    return a+b\\n'; print(re.findall(r'def (\\w+)\\(',t), "
    "re.sub(r'\\s+',' ',t), re.split(r'\\n',t), [m.group(1) for m in re.finditer(r'(\\w+)',t)][:5])",
    "print(re.sub(r'([A-Z])',lambda m: '_'+m.group(1).lower(),'camelCaseName'), "
    "re.sub(r'<[^>]+>','','<b>hi</b>'), re.sub(r'\\s+',' ','a   b\\tc').strip(), "
    "re.findall(r'`([A-Za-z0-9_./-]+)`','see `src/x.rs` and `y`'))",
    "print([l for l in re.split(r'\\r?\\n','a\\r\\nb\\nc') if l], "
    "re.match(r'^(\\d+)\\s+(.*)$','12  hello').groups(), "
    "re.search(r'opt-level = (\\\"s\\\"|2|3)','opt-level = 3').group(1))",
    "print(re.findall(r'(?m)^\\s*#\\s*(.*)$','# a\\nx\\n  # b'), re.sub(r'(?m)^','> ','a\\nb'), "
    "re.findall(r'<script.*?</script>','<script>a</script><script>b</script>',re.S))",
    "print([m.group() for m in re.finditer(r'\\d+','a1b22c333')], "
    "re.search(r'\"([^\"]+)\"','say \"hi\" now').group(1), "
    "re.findall(r'(?:home|tmp|root)','/tmp/x /home/y'))",
]]

#: **The quantifier cross-product**, and the wrong answer at exit 0 it was
#: written for. A capturing group whose body ends in a LAZY quantifier, nested
#: in a bounded counted repeat with `min >= 1`, captured the WRONG ITERATION:
#: `re.search(r'(a*?){1,2}b', 'ab').group(1)` was `''` where CPython says `'a'`.
#: The overall SPAN was right every time, which is why nothing above caught it
#: — only `group(n)`, `span(n)`, `groups()` and everything rendered from them
#: (`findall`, `split`, a `sub` template, a `finditer` group read) disagreed.
#:
#: The cause was one write. `sre`'s MAX_UNTIL arms the zero-width guard
#: (`last_ptr`) only in the branch that pushes and pops it — "we may have
#: enough matches, but if we can match another item, do so" — and leaves it
#: alone in the `count < min` branch above. This engine armed it in BOTH, so the
#: next `Until` at the same position read `ptr == last`, skipped the
#: try-one-more-iteration case, and reached the tail carrying a different
#: iteration's marks. `{0,n}`, `*` and `?` never reach that branch, which is
#: exactly the bisect the adversary reported.
#:
#: So every quantifier is nested inside every quantifier here, greedy and lazy,
#: with a capturing group and without, and every rendering of the marks is
#: printed. One program per row rather than one per pattern: 23 x 23 x 9 is
#: 4,761 lines of stdout compared byte for byte, in 0.02 s on either engine.
_Q = ("'', '*', '*?', '+', '+?', '?', '??', '{2}', '{2}?', '{0,2}', '{0,2}?', "
      "'{1,2}', '{1,2}?', '{1,3}', '{1,3}?', '{2,3}', '{2,3}?', '{2,4}', '{2,4}?', "
      "'{1,}', '{1,}?', '{2,}', '{2,}?'")
_S = "'', 'a', 'b', 'aa', 'ab', 'aab', 'aaa', 'ba', 'abab'"


def _cross(build: str, show: str, subjects: str = _S) -> str:
    """One program that walks the inner x outer x subject cube and prints
    `show` for each cell. `build` names the pattern in terms of `i` and `o`."""
    return (R + "Q = [%s]\nS = [%s]\n"
            "for i in Q:\n"
            "    for o in Q:\n"
            "        p = %s\n"
            "        for s in S:\n"
            "            print(p, repr(s), %s)\n" % (_Q, subjects, build, show))


QUANTIFIERS = [
    # The defect itself, spelled out: the three renderings from the report.
    R + "print(re.search('(a*?){1,2}b','ab').group(1), re.findall('(.*?){1,2}','a.b'), "
        "re.split('(a*?){1,2}b','xaby'))",
    R + "print(re.search('(a*?){1,2}b','ab').span(1), re.search('(a*?){1,2}b','ab').groups(), "
        "re.sub('(a*?){1,2}b',r'[\\1]','ab'), [m.group(1) for m in re.finditer('(a*?){1,3}b','abab')])",
    # A CAPTURING group, every quantifier inside every quantifier, and every
    # path the marks are read through.
    _cross("'(a' + i + ')' + o + 'b'",
           "None if re.search(p, s) is None else (re.search(p, s).span(), re.search(p, s).groups())"),
    _cross("'(a' + i + ')' + o + 'b'", "re.findall(p, s), re.split(p, s)"),
    _cross("'(a' + i + ')' + o", "re.findall(p, s), re.split(p, s)"),
    _cross("'(a' + i + ')' + o + 'b'", "re.sub(p, '<\\1>', s), re.subn(p, '-', s)"),
    _cross("'(a' + i + ')' + o", "[(m.span(), m.span(1), m.groups()) for m in re.finditer(p, s)]"),
    _cross("'(a' + i + ')' + o + '$'",
           "None if re.match(p, s) is None else (re.match(p, s).span(), re.match(p, s).groups())"),
    _cross("'(a' + i + ')' + o",
           "None if re.fullmatch(p, s) is None else (re.fullmatch(p, s).span(), re.fullmatch(p, s).groups())"),
    # …and WITHOUT one, where only the span can disagree.
    _cross("'(?:a' + i + ')' + o + 'b'",
           "None if re.search(p, s) is None else re.search(p, s).span(), re.findall(p, s)"),
    # Bodies that are not a bare atom: the `.` of the report's `findall` row, a
    # class, an alternation, and an alternation with an empty branch.
    _cross("'(.' + i + ')' + o", "re.findall(p, s), re.split(p, s)"),
    _cross("'([ab]' + i + ')' + o + 'b'", "re.findall(p, s), re.split(p, s)"),
    _cross("'((?:a|b)' + i + ')' + o", "re.findall(p, s)"),
    _cross("'(a|' + i + ')' + o + 'b'", "re.findall(p, s), re.split(p, s)"),
    # Three deep, with a group at each level, so an inner repeat's marks are
    # read through an outer one's.
    _cross("'((a' + i + ')' + o + ')'",
           "None if re.search(p, s) is None else (re.search(p, s).span(), re.search(p, s).groups())"),
    _cross("'(?:(a' + i + ')(b' + o + '))+'",
           "None if re.search(p, s) is None else (re.search(p, s).span(), re.search(p, s).groups())"),
]

#: Everything the matcher must REFUSE rather than approximate: the constructs of
#: a later slice, every `re.error` path (whose message text and position moved
#: across 3.11–3.14), the Unicode tables, and the step budget.
MATCHER_REFUSED = [R + x for x in [
    # Later slices, named one construct at a time so `--plan` can rank them.
    "print(re.search(r'(?P<a>x)','x'))", "print(re.search(r'(?P<n>a)(?P=n)','aa'))",
    "print(re.match(r'(a)\\1','aa'))", "print(re.search(r'(?=a)','a'))",
    "print(re.search(r'(?!b)a','a'))", "print(re.search(r'(?<=a)b','ab'))",
    "print(re.search(r'(?<!a)b','cb'))", "print(re.findall(r'(?i:a)b','Ab'))",
    "print(re.match(r'(?>a|ab)c','abc'))", "print(re.match(r'a*+a','aaa'))",
    "print(re.match(r'(x)?(?(1)a|b)','b'))", "print(re.findall(r'\\N{BULLET}','\\u2022'))",
    "print(re.search(r'a\\z','a'))",
    # Patterns CPython rejects: its message and its position are its own.
    "print(re.compile('('))", "print(re.compile('a**'))", "print(re.compile('*'))",
    "print(re.compile('^*'))", "print(re.compile(r'\\b*'))", "print(re.compile('a{2,1}'))",
    "print(re.compile('[b-a]'))", "print(re.compile('[a'))", "print(re.compile('[]'))",
    "print(re.compile(r'[\\d-z]'))", "print(re.compile(r'[a--b]'))", "print(re.compile('\\\\'))",
    "print(re.compile(r'\\p'))", "print(re.compile(r'\\e'))", "print(re.compile(r'\\x4'))",
    "print(re.compile(r'\\400'))", "print(re.compile(r'(a)\\2'))", "print(re.compile(r'\\1(a)'))",
    "print(re.compile('a{4294967295}'))", "print(re.compile('a(?i)b'))",
    "print(re.compile('a{1,2}{2}'))", "print(re.compile('a{1}??'))", "print(re.compile('a*+?'))",
    "print(re.compile(r'(?<n>a)'))", "print(re.compile(r'(?P<1>a)'))",
    "print(re.compile(r'(?P<n>a)(?P<n>b)'))", "print(re.compile('(?au)a'))",
    "print(re.compile('(?-i)a'))", "print(re.compile('(?i-i:a)'))",
    # The Unicode tables, in and out of a class, and case folding beyond ASCII.
    "print(re.findall(r'\\d','1\\u0663'))", "print(re.findall(r'\\w+','\\u0928\\u092e'))",
    "print(re.findall(r'\\s','a\\xa0b'))", "print(re.findall(r'\\b\\u00e9\\b','x \\u00e9 y'))",
    "print(re.findall(r'(?i)\\u00e9','e\\u00c9E'))", "print(re.findall(r'(?i)k','k\\u212a'))",
    "print(re.findall(r'[\\w]','\\u00e9'))",
    # 3.12 says no match, 3.14 says a match; neither is safe to pick.
    "print(re.search(r'\\B',''))", "print(re.findall(r'\\B',''))",
    # The step budget: exit 90, and CPython then takes its own exponential time.
    "print(re.match(r'(a+)+$','a'*30+'b'))", "print(re.match(r'(a*)*$','a'*28+'b'))",
    "print(re.match(r'(x+x+)+y','x'*28))", "print(re.match(r'(a|aa)*$','a'*40+'b'))",
    # Flags and argument shapes CPython answers with a ValueError or TypeError.
    "print(re.compile('a',re.L))", "print(re.compile('a',re.A|re.U))",
    "print(re.compile('a',re.DEBUG))", "print(re.compile('a',512))", "print(re.compile('a',3))",
    "print(re.sub(re.compile('a'),'X','a',flags=re.I))", "print(re.match('a','a',re.I,re.M))",
    "print(re.sub('a','b',1))", "print(re.compile('a').search('abc',1.5))",
    "print(re.sub('a','b','a',1.0))", "print(re.compile(None))",
    "print(re.findall('a','a',flag=1))",
    # Bytes.
    "print(re.match(b'(a)',b'ab'))", "print(re.sub('a','b',b'a'))", "print(re.compile(b'a'))",
    # Names of a later slice, and the group lookups CPython answers with an
    # IndexError.
    "print(re.match('a','a').groupdict())", "print(re.match('a','a').lastindex)",
    "print(re.match('a','a').regs)", "print(re.match('a','a').expand('x'))",
    "print(re.compile('a').groupindex)", "print(re.compile('a').scanner('a'))",
    "print(re.match('a','a').group(1))", "print(re.match(r'(a)','a').group(-1))",
    "print(re.match('a','a')['x'])", "print(re.match('a','a').start(1))",
    # The two new values in every generic path: never a panic, never a plausible
    # answer, never an AttributeError at exit 1.
    "print(list(re.match('a','a')))", "print(re.compile('a') < re.compile('b'))",
    "print(re.match('a','a')[0:1])", "print(re.match('a','a') + 1)",
    "print(int(re.match('a','a')))", "print(float(re.compile('a')))",
    "print(bytes(re.match('a','a')))", "print(reversed(re.compile('a')))",
    "print(1 in re.match('a','a'))", "print(re.compile('a') * 2)",
    "print(f'{re.match(chr(97),chr(97)):>5}')", "print(sorted([re.compile('a'), re.compile('b')]))",
    "print({re.match('a','a'): 1})", "print(type(re.compile('a')))",
    "import json\nprint(json.dumps(re.compile('a')))",
    # A refusal leaves NOTHING on stdout even when the program printed first.
    "print('hi')\nprint(re.search(r'(?P<a>x)','x'))",
    "print('hi')\nm = re.search(r'\\d','\\u0663')\nprint(m)",
]]



#: Everything that must refuse rather than answer: every module attribute
#: outside the surface; the flag's attributes, `~`, format specs, container
#: protocol and non-int partners; the bad `escape`/`purge` calls; the refusals
#: the engine already had (`type`, `dir`, `hash`, `sys.modules`); and
#: `MATCHER_REFUSED`, which is the engine's own list.
REFUSED = [R + x for x in [
    "print(re.error)", "print(re.PatternError)", "print(re.T)", "print(re.TEMPLATE)",
    "print(re.RegexFlag)", "print(re.Pattern)", "print(re.Match)", "print(re.Scanner)",
    "print(re.template('a'))", "print(re.__version__)", "print(re.nosuchthing)",
    "print(re.I.name)", "print(re.I.value)", "print(re.I.bit_length())", "print(re.I.__class__)",
    "print(re.I.real)",
    "print(~re.I)", "print(~re.NOFLAG)",
    "print(f'{re.I:>20}')", "print(format(re.I, 'd'))", "print('%d' % re.I)", "print('%x' % re.I)",
    "print('%c' % re.I)",
    "print(len(re.I))", "print(list(re.I|re.M))", "for f in re.I: print(f)",
    "print(re.I in re.I|re.M)", "print(sorted(re.I))",
    "print(re.I|1)", "print(re.I|True)", "print(re.I|'a')", "print(re.I|2.0)", "print(re.I&None)",
    "print(re.I|-1)", "print(re.I | 2**32)",
    "print(re.escape(b'a.'))", "print(re.escape(1))", "print(re.escape())",
    "print(re.escape('a', 'b'))", "print(re.escape(pattern='a'))", "print(re.purge(1))",
    "print(re.purge(x=1))",
    "print(type(re.I))", "print(type(re.I).__name__)", "print(dir(re))", "print(hash(re.I))",
    "try:\n    re.compile('(')\nexcept re.error as e:\n    print(e)",
    "import sys\nprint(sys.modules['re'])",
]] + MATCHER_REFUSED + [
    "from re import search\nprint(search(r'(?P<a>x)', 'x'))",
    "import re as r\nprint(r.findall(r'(?<=a)b', 'ab'))",
    "from re import error\nprint(error)",
    "from re import T\nprint(T)",
]

#: The half of the surface that used to refuse and now answers, in every
#: spelling of the import the walker resolves. These are GRID rows, not
#: `REFUSED` ones, and the move is the whole of this capability.
SPELLINGS = [
    R + "print(re.search('a', 'a').span(), re.match('a', 'a').span(), re.fullmatch('a','a').span())",
    R + "print(re.findall('a', 'aa'), [m.span() for m in re.finditer('a', 'aa')])",
    R + "print(re.sub('a', 'b', 'a'), re.subn('a', 'b', 'a'), re.split('a', 'bab'))",
    R + "p = re.compile\nprint(p('a').pattern)",
    R + "print(re.search('a', 'A', re.I).span(), re.findall(pattern='a', string='aa'))",
    "from re import search\nprint(search('a', 'a').span())",
    "import re as r\nprint(r.findall(r'\\d+', 'a12'))",
    "from re import compile as c\nprint(c(r'\\w+').findall('ab cd'))",
    "import re\ns = 'x'\nprint(len(s))\nm = re.search('x', s)\nprint(m.span())",
    "import re\npat = '(?:' + r'\\d+' + ')'\nprint(re.findall(pat, 'a12 b3'))",
]

GRID = (IMPORTS + FLAGS + ESCAPE + PURGE + INT_PARTNERS + MATCHER + EMPTY
        + GROUPS + QUANTIFIERS + ANCHORS + CLASSES + POSENDPOS + NONASCII + SUBSPLIT
        + FLAGS2 + OBJECTS + LINEAR + IDIOMS + SPELLINGS)


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


def _current(engine: str) -> Path | None:
    """A built ``engine`` that carries THIS tree's capability table.

    A candidate is taken only if it names itself ``engine``, its compiled
    `route::CAPS` knows `cap-re`, and the capability set it was BUILT with
    is this tree's. Those last two are the point: an installed binary from
    before this capability landed answers every grid row with a refusal, which
    would turn the whole file into green skips measuring nothing. Skipping
    loudly is the honest failure; passing quietly is not.
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
        if any(row.get("cap") == "cap-re" for row in table.get("caps", [])):
            return cand
    return None


BINARY = _current(engines.LYPNING_L)
CORE = _current(engines.LYPNING)

needs_l = pytest.mark.skipif(
    BINARY is None,
    reason="no lypning-l carrying cap-re is built (cargo build --release "
           "--no-default-features --features variant-l --target-dir target/variant-l)",
)


def _run(argv: list[str], program: str) -> subprocess.CompletedProcess:
    """One program, in a temp cwd of its own — invariant 4, and here the rows
    really do write files, so this is what keeps the tree out of their way."""
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
def test_the_re_grid_agrees_with_cpython(program: str) -> None:
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
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got)
    assert problem is None, (
        "this program must refuse, not answer — CPython answers it and any "
        "answer here would be a silent divergence: %s\n  program: %r\n  stderr: %r"
        % (problem, program, got.stderr.strip()[:200])
    )


@needs_l
def test_the_capability_is_on_the_larger_variant_only() -> None:
    """The gate this whole file sits behind: the core must still REFUSE `re`,
    and must route it to the sibling that serves it.

    A capability that leaked into the frozen variant would still pass every grid
    row above — it is the same code — so the byte budget is defended here, by
    asking each binary what it is."""
    core = CORE
    if core is None:
        pytest.skip("no core carrying this tree's capability table is built")
    refused = _run([str(core)], "import re")
    assert refused.returncode == engines.UNSUPPORTED_EXIT and refused.stdout == ""
    assert refused.stderr.strip() == engines.refusal_line(
        engines.LYPNING, "module", "import re")

    # …and the core's ROUTER knows which sibling does serve it, which is the
    # half that makes the refusal cost one spawn instead of a CPython one.
    route = subprocess.run([str(core), "route", "-c",
                            'import re\nprint(re.escape("a."))'],
                           capture_output=True, text=True, timeout=60)
    assert route.stdout.split("\t")[0].strip() == engines.LYPNING_L, route.stdout


def _core_env() -> dict:
    return dict(os.environ, LYPNING_L_BIN=str(BINARY), LYPNING_CPYTHON=sys.executable)


@needs_l
def test_a_runtime_refusal_after_reading_stdin_replays_it_to_the_next_rung() -> None:
    """The corpus's largest cluster is `stdin -> transform -> stdout`, and with
    `re` admitted its regex half routes to lypning-l, reads the pipe, and
    refuses at the matcher. The core's `run` forks that rung; before this was
    pinned it forked it with the INHERITED pipe, so CPython was then handed an
    exhausted stream and answered about nothing at exit 0 — the mixture-rust
    arm's `stdin-regex-extract` and `stdin-replace-sed` MISMATCHes of
    2026-09-05. The Python dispatcher reads a piped stdin once and replays it
    to every rung; this holds the Rust one to the same rule.

    The pattern is BUILT AT RUNTIME here, which is the one shape the walker
    cannot decide: a literal it can compile itself, and a literal it cannot
    compile is a static route to CPython that never touches lypning-l (the test
    after this one), so neither would still measure the replay. The route is
    asserted first, so the row keeps measuring it."""
    core = CORE
    if core is None:
        pytest.skip("no core carrying this tree's capability table is built")
    env = _core_env()
    for program, stdin, want in [
        ("import sys, re\npat = '(?P<n>' + r'id=(\\d+)' + ')'\nfor line in sys.stdin:\n"
         "    m = re.search(pat, line)\n    if m:\n        print(m.group(2))",
         "x id=41 y\nnope\nz id=7\n", "41\n7\n"),
        ("import sys, re\npat = '(?<=f)' + 'oo+'\n"
         "sys.stdout.write(re.sub(pat, 'BAR', sys.stdin.read()))",
         "foo fooo food\n", "fBAR fBAR fBARd\n"),
    ]:
        route = subprocess.run([str(core), "route", "-c", program], capture_output=True, text=True,
                               timeout=60, env=env)
        assert route.stdout.split("\t")[0].strip() == engines.LYPNING_L, route.stdout
        got = subprocess.run([str(core), "run", "-c", program], input=stdin,
                             capture_output=True, text=True, timeout=60, env=env)
        assert (got.stdout, got.returncode) == (want, 0), (got.stdout, got.stderr[-300:])


@needs_l
def test_a_pattern_literal_outside_the_slice_is_a_static_route_to_cpython() -> None:
    """A pattern the engine cannot compile is decided by the WALKER, before the
    program starts — the same refusal `re.compile` would raise one in-process
    run later, moved to where a walk can see it.

    The move is the point, and it is what the `re` surface's first shape already
    paid for: a runtime refusal that lands after a side effect the commit
    barrier has let through (`os.makedirs` before `re.sub`) cannot fall onward,
    so it becomes exit 1 — the program's own exit, which the chain never
    retries. Here the route is CPython, exec'd with the pipe inherited and
    untouched: nothing between the producer and the answer."""
    program = ("import sys, re\nd = sys.stdin.read()\n"
               "print(re.findall(r'(?P<d>\\d)', d))")
    route = subprocess.run([str(BINARY), "route", "-c", program], capture_output=True, text=True,
                           timeout=60, env=_core_env())
    assert route.stdout.split("\t")[0].strip() == engines.CPYTHON, route.stdout
    assert route.stdout.split("\t")[1].strip().startswith("re: pattern"), route.stdout
    got = subprocess.run([str(BINARY), "run", "-c", program], input="a1b2\n",
                         capture_output=True, text=True, timeout=60, env=_core_env())
    assert (got.stdout, got.returncode) == ("['1', '2']\n", 0), (got.stdout, got.stderr[-300:])
    # …and the barrier case the static route exists for: the side effect has
    # already committed when the refusal would have fired.
    side = ("import re, os\nos.makedirs('d1/d2')\n"
            "print(re.sub(r'(?<=a)b', 'X', 'ab'))")
    r2 = subprocess.run([str(BINARY), "route", "-c", side], capture_output=True, text=True,
                        timeout=60, env=_core_env())
    assert r2.stdout.split("\t")[0].strip() == engines.CPYTHON, r2.stdout
    got2 = _run([str(BINARY), "run"], side)
    assert (got2.stdout, got2.returncode) == ("aX\n", 0), (got2.stdout, got2.stderr[-300:])


#: Where the walk finds the pattern, one row per spelling: what the route must
#: be, and the blocker's prefix when it is a block. Every `cpython` row is a
#: construct that USED to reach the runtime refusal and now does not, and every
#: `lypning-l` row is a pattern a walk cannot read — the backstop's half,
#: which must stay the backstop's.
STATIC_PATTERNS = [
    # A literal in the pattern position: the shape step 2 already decided.
    (R + "print(re.search(r'(?P<a>x)', 'x'))", engines.CPYTHON, "re: pattern"),
    # A literal bound to a NAME above the call. The compiled pattern is the
    # same one; only the walk had to reach further to find it.
    (R + "P = r'(?P<a>x)'\nprint(re.search(P, 'x'))", engines.CPYTHON, "re: pattern"),
    (R + "P = r'a(?=b)'\nprint(re.sub(P, '-', 'ab'))", engines.CPYTHON, "re: pattern"),
    (R + "P = r'(a)\\1'\nprint(re.compile(P).findall('aa'))", engines.CPYTHON, "re: pattern"),
    ("from re import findall\nP = r'(?P<a>x)'\nprint(findall(P, 'x'))",
     engines.CPYTHON, "re: pattern"),
    ("import re as r\nP = r'(?<=a)b'\nprint(r.search(P, 'ab'))", engines.CPYTHON, "re: pattern"),
    # A BYTES pattern: servable by CPython, by nothing here, and refused on
    # every subject — so there is nothing for a run to learn.
    (R + "print(re.finditer(b'a', b'ab'))", engines.CPYTHON, "re: bytes pattern"),
    (R + "print(re.compile(b'a'))", engines.CPYTHON, "re: bytes pattern"),
    (R + "P = rb'\\s'\nprint(re.findall(P, b'a b'))", engines.CPYTHON, "re: bytes pattern"),
    # `pattern=` is the one keyword spelling of the same argument, and this
    # engine and CPython both take it.
    (R + "print(re.search(pattern=r'(?P<a>x)', string='x'))", engines.CPYTHON, "re: pattern"),
    # …and the other half. A pattern a walk cannot read keeps the RUNTIME
    # refusal, because a static block here would be a program sent to CPython
    # that this engine runs — the direction that costs a spawn for nothing.
    (R + "for P in ['a', 'b']:\n    print(re.findall(P, 'ab'))", engines.LYPNING_L, None),
    (R + "P = r'(?P<a>x)'\nP = r'x'\nprint(re.search(P, 'x').span())", engines.LYPNING_L, None),
    (R + "P = r'(?P<a>x)'\ndef f(P):\n    return re.search(P, 'x')\nprint(f('x').span())",
     engines.LYPNING_L, None),
    (R + "print(re.findall('(?P' + '<a>x)', 'x'))", engines.LYPNING_L, None),
    (R + "P = 'x'\nprint(re.findall(P + '+', 'xx'))", engines.LYPNING_L, None),
    (R + "P = r'x'\nprint(re.findall(P, 'xx'))", engines.LYPNING_L, None),
    # A name that means the module and a name that does not: `re.split(',')` on
    # a string someone called `re` is a str method, and no walk may compile it.
    # This routes to lypning-l because a router never routes below itself, not
    # because the walk found a pattern — the `blocker is None` half is the
    # assertion that matters.
    ("re = 'a,b'\nprint(re.split(','))", engines.LYPNING_L, None),
]


@needs_l
@pytest.mark.parametrize("program,engine,blocker",
                         STATIC_PATTERNS, ids=range(len(STATIC_PATTERNS)))
def test_where_the_walk_finds_the_pattern(program: str, engine: str, blocker: str | None) -> None:
    """A pattern this engine cannot compile is the program's blocker in the
    WALK, wherever the pattern is spelled — and a pattern the walk cannot
    read keeps the runtime refusal.

    Both halves are the same argument, and it is the one the `re` surface has
    paid for twice: a runtime refusal that lands after a side effect the commit
    barrier has let through cannot fall onward, so it becomes exit 1, the
    program's own exit, which the chain never retries. A static route costs
    nothing and cannot land late. But a static route taken for a pattern the
    program never uses is a program sent to CPython that this engine runs —
    so the walk resolves a name only where a walk can honestly read it, and
    every other spelling stays with the backstop."""
    got = subprocess.run([str(BINARY), "route", "-c", program],
                         capture_output=True, text=True, timeout=60)
    assert got.returncode == 0, got.stderr
    fields = got.stdout.split("\t")
    assert fields[0].strip() == engine, got.stdout
    if blocker is None:
        assert len(fields) == 1 or not fields[1].strip().startswith("re:"), got.stdout
    else:
        assert fields[1].strip().startswith(blocker), got.stdout


@needs_l
def test_a_static_pattern_block_answers_where_the_runtime_refusal_could_not() -> None:
    """The barrier case, for the constructs this commit moved.

    `os.makedirs` is past the commit barrier, so the refusal `re.finditer`
    would have raised cannot be replayed onward: it becomes exit 1 with the
    directory already made. Routed by the walk instead, the program never
    starts here and CPython answers it."""
    for program, want in [
        ("import re, os\nos.makedirs('d1/d2')\nprint(re.findall(b'a', b'aa'))",
         "[b'a', b'a']\n"),
        ("import re, os\nP = r'(?P<a>x)'\nos.makedirs('d3')\n"
         "print(re.findall(P, 'xx'))", "['x', 'x']\n"),
    ]:
        route = subprocess.run([str(BINARY), "route", "-c", program],
                               capture_output=True, text=True, timeout=60)
        assert route.stdout.split("\t")[0].strip() == engines.CPYTHON, route.stdout
        got = _run([str(BINARY), "run"], program)
        assert (got.stdout, got.returncode) == (want, 0), (got.stdout, got.stderr[-300:])


@needs_l
def test_a_program_that_cannot_read_stdin_does_not_wait_for_a_slow_producer() -> None:
    """The other edge of the replay: buffering stdin means reading it to EOF,
    and EOF is the producer's to give. Done for every forked rung it made
    `(sleep 30; echo hi) | lypning run -c 'import collections; …'` print nothing
    for thirty seconds and `tail -f log | lypning run …` wait for good — for a
    program that never looks at the stream. So the read is conditional on
    `Route::reads_stdin`. The program here routes core -> lypning-l, the forked
    intermediate rung that used to trigger it; the pipe is held open for the
    whole run to prove the run did not wait for it."""
    core = CORE
    if core is None:
        pytest.skip("no core carrying this tree's capability table is built")
    program = "import collections\nprint(collections.Counter('ab'))"
    route = subprocess.run([str(core), "route", "-c", program], capture_output=True, text=True,
                           timeout=60, env=_core_env())
    assert route.stdout.split("\t")[0].strip() == engines.LYPNING_L, route.stdout
    p = subprocess.Popen([str(core), "run", "-c", program], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_core_env())
    try:
        code = p.wait(timeout=60)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        pytest.fail("`lypning run` waited on a stdin the program cannot read")
    finally:
        p.stdin.close()
    assert (code, p.stdout.read()) == (0, "Counter({'a': 1, 'b': 1})\n"), p.stderr.read()


@needs_l
def test_a_re_method_name_is_admitted_only_for_a_program_that_imports_re() -> None:
    """`.group`, `.span`, `.start` are ordinary names on other objects.

    Admitting `known_method("group")` for every receiver would take a program
    this engine sends to CPython today — which answers it — and run it here
    instead, where it stops at an `AttributeError`: exit 1, the program's own
    exit, which the chain never retries. The import is what makes the router's
    optimism honest, and this is the assertion that says so."""
    without = subprocess.run([str(BINARY), "route", "-c", "x=1\nprint(x.group())"],
                             capture_output=True, text=True, timeout=60)
    assert without.stdout.split("\t")[0].strip() == engines.CPYTHON, without.stdout
    with_import = subprocess.run(
        [str(BINARY), "route", "-c", "import re\nx=1\nprint(x.group())"],
        capture_output=True, text=True, timeout=60)
    assert with_import.stdout.split("\t")[0].strip() == engines.LYPNING_L, with_import.stdout


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
    assert "cap-re" in table["self_caps"]
    assert {r["name"]: tuple(r["caps"]) for r in table["spectrum"]} == engines.VARIANT_CAPS
    assert {r["cap"]: r["modules"] for r in table["caps"]}["cap-re"] == ["re"]
