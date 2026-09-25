"""Latent MISMATCHes in shared code, as a grid on EVERY variant.

These rows came from reading the engine rather than from the corpus. None of
them was in the conformance battery, so every gate was green while they were
wrong. Each row runs on both `lypning` and `lypning-l`, from a fresh temp cwd
(invariant 4). It must end in one of two ways:

  * ``ANSWERED``: the same stdout and exit code as the REFERENCE CPython —
    the interpreter running this test, which is the one the engine was built
    against (`build.reference_python_env`). The bytes written next to each
    row are what CPython 3.14.5 printed on 2026-09-24; they document the fix
    and are checked only on a 3.14 interpreter, because several rows (PEP 649,
    the free-variable and unbound-local wordings) are worded differently by
    older ones and the engine follows its reference. A refusal FAILS these
    rows, because each one is a fix, and a fix that quietly turned into a
    refusal is a regression.
  * ``REFUSED``: a clean refusal. Exit 90, nothing on stdout, one ``<engine>:
    unsupported: …`` line on stderr (invariant 2). Each of these rows exited 1
    before, or answered wrongly at exit 0.

What was wrong, one line each:

  * PEP 479. A StopIteration escaping a generator expression's body was read as
    exhaustion. CPython raises ``RuntimeError('generator raised
    StopIteration')``.
  * A genexp that advances itself answered with exhaustion instead of
    ``ValueError('generator already executing')``. The stand-in that fills the
    cell is marked ``done``, and ``done`` was checked first.
  * StopIteration raised by the function ``map()`` or ``filter()`` calls ENDS
    that iterator in CPython. Here it propagated as an exception.
  * A bare exception (``raise ValueError``, ``next()`` on an exhausted
    iterator) had ``args == ('',)``. CPython has ``()``. ``StopIteration.value``
    was an AttributeError at exit 1. The empty message now means "no
    arguments", so ``ValueError('')`` refuses, and so does an ``assert`` whose
    message is not a non-empty str, but only where a handler could catch it.
  * A comprehension target rebound a name the enclosing function declared
    ``global``.
  * A generator expression resolved names against the frame that CONSUMED it
    rather than the one that created it, which gave an UnboundLocalError.
  * ``float('-nan')`` lost its sign bit.
  * ``def f(*a: int, **k: str)`` was a SyntaxError at exit 1.

Round 2 of the same track, from the verifier's probes:

  * ``comp_assign`` set the WHOLE ``global`` table aside for a comprehension
    target, so ``global d; [0 for d['k'] in [1]]`` read ``d`` as an unbound
    local. Only the names the target binds leave the table now.
  * ``def f(**k, x)`` and ``def f(*a, *b)`` parsed; ``def f(*a=1)`` had the
    wrong SyntaxError. All three are CPython's SyntaxError now.
  * A genexp evaluated its first iterable at the first ``next()``. It is
    evaluated, and ``iter()`` taken, where the genexp is created.
  * An engine-raised OSError had ``args == (message,)``; it is ``(errno,
    strerror)`` and ``repr`` follows. ``UnicodeDecodeError('x')`` is CPython's
    arity TypeError.
  * ``float(b'1')`` was a TypeError.
  * On a 3.14 reference annotations are lazy (PEP 649) and are no longer
    evaluated when the ``def`` runs.
  * A genexp reading a name its creating function binds later raises CPython's
    free-variable NameError, not UnboundLocalError.

  * ``global x`` in a function nested in another read the ENCLOSING
    function's ``x``. A declared name now skips every scope below the frame's
    own comprehensions.

And refused: a ``def``/``lambda`` made inside a nested function that declares
``global``; ordering sets in
``sorted``/``min``/``max``/a sequence; ``math.copysign`` of a NaN (its sign
depends on how CPython's compiler ordered the operands that made it); a
Unicode*Error's constructor arguments; ``OSError.filename2``; ``t[*a]``; a
NameError two scopes deep, whose message depends on enclosing assignments.
``sys.platform`` is the host's (``darwin``/``linux``), not always ``linux``.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

#: (program, CPython 3.14.5 stdout, CPython 3.14.5 exit)
ANSWERED = [
    # --- PEP 479 -----------------------------------------------------------
    ("try:\n    print(list(next(iter([])) for x in [1]))\n"
     "except RuntimeError as e:\n    print('RT', e)\nexcept StopIteration:\n    print('SI')",
     "RT generator raised StopIteration\n", 0),
    ("try:\n    print(sum(next(iter([])) for x in [1]))\n"
     "except RuntimeError as e:\n    print('RT', e)\nexcept StopIteration:\n    print('SI')",
     "RT generator raised StopIteration\n", 0),
    ("g=(next(iter([])) for x in [1])\ntry:\n    next(g)\nexcept RuntimeError as e:\n"
     "    print('RT', e)\ntry:\n    next(g)\nexcept StopIteration as e:\n    print('SI2', e.args)",
     "RT generator raised StopIteration\nSI2 ()\n", 0),
    ("try:\n    print(list(x for x in (next(iter([])) for y in [1])))\n"
     "except RuntimeError as e:\n    print('RT', e)",
     "RT generator raised StopIteration\n", 0),
    ("g=(next(iter([])) for x in [1,2])\ntry:\n    list(g)\nexcept RuntimeError as e:\n"
     "    print('RT')\nprint(list(g))",
     "RT\n[]\n", 0),
    ("def f(x):\n    raise StopIteration('boom')\ntry:\n    print(list(f(x) for x in [1]))\n"
     "except RuntimeError as e:\n    print('RT', e)",
     "RT generator raised StopIteration\n", 0),
    ("try:\n    print(any(next(iter([])) for x in [1]))\nexcept RuntimeError as e:\n    print('RT', e)",
     "RT generator raised StopIteration\n", 0),
    ("print(list(next(iter([])) for x in [1]))", "", 1),
    # A default to next() is not a StopIteration at all.
    ("g=(next(iter([]), 1) for x in [1])\nprint(list(g))", "[1]\n", 0),
    # --- a generator that advances itself ------------------------------------
    ("def h(g):\n    return next(g)\ng=None\ng=(h(g) for x in [1,2])\ntry:\n"
     "    print(list(g))\nexcept ValueError as e:\n    print('VE', e)",
     "VE generator already executing\n", 0),
    # --- StopIteration inside map()/filter() is the end of the iterator ------
    ("it=iter([1])\ntry:\n    print(list(map(lambda x: next(it), [1,2,3])))\n"
     "except StopIteration:\n    print('SI')",
     "[1]\n", 0),
    ("it=iter([1])\nfor v in map(lambda x: next(it), [1,2,3]):\n    print(v)\nprint('end')",
     "1\nend\n", 0),
    ("it=iter([])\nprint(list(filter(lambda x: next(it), [1,2])))", "[]\n", 0),
    ("it=iter([])\ntry:\n    next(map(lambda x: next(it), [1]))\n"
     "except StopIteration as e:\n    print('SI', e.args)",
     "SI ()\n", 0),
    ("it=iter([1])\nprint(list(zip(map(lambda x: next(it), [1,2,3]), 'abc')))",
     "[(1, 'a')]\n", 0),
    ("it=iter([])\ntry:\n    sorted([1,2], key=lambda x: next(it))\nexcept StopIteration:\n    print('SI')",
     "SI\n", 0),
    # --- a bare exception's args ---------------------------------------------
    ("try:\n    raise ValueError\nexcept ValueError as e:\n    print(e.args, repr(e), str(e)=='')",
     "() ValueError() True\n", 0),
    ("try:\n    next(iter([]))\nexcept StopIteration as e:\n    print(e.args, e.value, repr(e))",
     "() None StopIteration()\n", 0),
    ("try:\n    raise StopIteration('v')\nexcept StopIteration as e:\n    print(e.value, e.args)",
     "v ('v',)\n", 0),
    ("try:\n    raise ValueError\nexcept ValueError as e:\n    print('[%s]' % e, len(e.args))",
     "[] 0\n", 0),
    ("try:\n    assert False\nexcept AssertionError as e:\n    print(e.args, repr(e))",
     "() AssertionError()\n", 0),
    ("try:\n    assert False, 'm'\nexcept AssertionError as e:\n    print(e.args, repr(e))",
     "('m',) AssertionError('m')\n", 0),
    # Uncaught, only `str()` of the message is shown, so a non-str one answers.
    ("print(1)\nassert 1 == 2, 5", "1\n", 1),
    ("def f():\n    assert 0, (1, 2)\nf()", "", 1),
    ("try:\n    pass\nexcept Exception:\n    pass\nassert 0, ''", "", 1),
    ("try:\n    raise ValueError('x')\nexcept ValueError as e:\n    print(e.args, repr(e))",
     "('x',) ValueError('x')\n", 0),
    ("try:\n    try:\n        raise KeyError\n    except KeyError:\n        raise\n"
     "except KeyError as e:\n    print(repr(e), str(e)=='')",
     "KeyError() True\n", 0),
    # --- comprehension targets and `global` ----------------------------------
    ("v='OUT'\ndef h():\n    global v\n    [v for v in [1]]\nh()\nprint(v)", "OUT\n", 0),
    ("v='OUT'\ndef h():\n    global v\n    s={v for v in [1]}\n    d={v:1 for v in [2]}\n"
     "    g=list(v for v in [3])\n    return s,d,g\nprint(h())\nprint(v)",
     "({1}, {2: 1}, [3])\nOUT\n", 0),
    ("a='A'\nb='B'\ndef h():\n    global a\n    return [(a,b) for a,b in [(1,2)]]\nprint(h(), a, b)",
     "[(1, 2)] A B\n", 0),
    ("v='OUT'\ndef h():\n    global v\n    return list(v for v in [1,2])\nprint(h(), v)",
     "[1, 2] OUT\n", 0),
    ("v='OUT'\ng=(v for v in [1,2])\ndef h():\n    global v\n    return list(g)\nprint(h(), v)",
     "[1, 2] OUT\n", 0),
    ("k=10\ndef h():\n    global k\n    k=11\n    return [k+i for i in [1]]\nprint(h(), k)",
     "[12] 11\n", 0),
    ("v='OUT'\n[v for v in [1]]\nprint(v)", "OUT\n", 0),
    # --- a generator resolves names in the frame that made it ----------------
    ("x=5\ng=(x for _ in [1])\ndef f():\n    r=list(g)\n    x=1\n    return r\nprint(f())",
     "[5]\n", 0),
    ("def mk():\n    y=7\n    return (y+i for i in [1,2])\ndef use(g):\n    y=0\n"
     "    return list(g)\nprint(use(mk()))",
     "[8, 9]\n", 0),
    ("y=3\ndef h():\n    global y\n    g=(y for _ in [1])\n    y=4\n    return list(g)\nprint(h(), y)",
     "[4] 4\n", 0),
    # --- float('-nan') -------------------------------------------------------
    ("x=float('-nan')\nprint(x, repr(x), x!=x, f'{x}', '%f' % x)", "nan nan True nan nan\n", 0),
    # --- star-parameter annotations ------------------------------------------
    ("def f(*a: int, **k: str):\n    return a, k\nprint(f(1,2,x='y'))",
     "((1, 2), {'x': 'y'})\n", 0),
    ("def f(a: int, *b: str, **c: float) -> None:\n    print(a, b, c)\nf(1, 'x', k=2.0)",
     "1 ('x',) {'k': 2.0}\n", 0),
    ("print((lambda *a, **k: (a, k))(1, z=2))", "((1,), {'z': 2})\n", 0),
    # --- round 2: a comprehension target that READS a global -----------------
    ("d = {}\ndef f():\n    global d\n    d = {}\n    [0 for d['k'] in [1]]\n    return d\nprint(f())",
     "{'k': 1}\n", 0),
    ("d = {}\ndef f():\n    global d\n    d = {}\n    print(list(0 for d['k'] in [1,2]))\n"
     "    return d\nprint(f())",
     "[0, 0]\n{'k': 2}\n", 0),
    ("def f():\n    global i\n    i = 0\n    d = [0,0]\n    [0 for d[i] in [5]]\n    return d\nprint(f())",
     "[5, 0]\n", 0),
    ("d = {}\ndef f():\n    global d\n    d = {}\n    print([d.get('k') for d['k'] in [1,2]])\n"
     "    return d\nprint(f())",
     "[1, 2]\n{'k': 2}\n", 0),
    ("d = {}\ndef f():\n    global d\n    d = {}\n    [0 for a, d['k'] in [(1, 2)]]\n    return d\nprint(f())",
     "{'k': 2}\n", 0),
    # --- round 2: `global` in a nested function skips the enclosing local ---
    ("x = 0\ndef f():\n    x = 1\n    def g():\n        global x\n        return x\n    return g()\nprint(f())",
     "0\n", 0),
    ("x = 0\ndef f():\n    x = 1\n    def g():\n        global x\n"
     "        return [x for _ in [1]], list(x for _ in [1]), [x for x in [7]], x\n    return g()\nprint(f(), x)",
     "([0], [0], [7], 0) 0\n", 0),
    ("x = 5\ndef f():\n    x = 1\n    def g():\n        global x\n        x += 1\n        return x\n"
     "    return g(), x\nprint(f(), x)",
     "(6, 1) 6\n", 0),
    # --- round 2: the genexp's first iterable is evaluated at creation --------
    ("def f():\n    print('f called')\n    return [1, 2]\ng = (x for x in f())\nprint('created')\n"
     "print(list(g))",
     "f called\ncreated\n[1, 2]\n", 0),
    ("try:\n    g = (x for x in 5)\n    print('created')\nexcept TypeError as e:\n    print('TE', e)",
     "TE 'int' object is not iterable\n", 0),
    ("def outer():\n    items=[1,2]\n    g=(i for i in items)\n    items=[3]\n    return list(g)\nprint(outer())",
     "[1, 2]\n", 0),
    ("def f():\n    g = (x for _ in [1])\n    return list(g)\n    x = 1\ntry:\n    print(f())\n"
     "except NameError as e:\n    print(type(e).__name__, e)",
     "NameError cannot access free variable 'x' where it is not associated with a value in enclosing scope\n", 0),
    # --- round 2: OS errors, Unicode errors ----------------------------------
    ("try:\n    open('/nonexistent/zz')\nexcept OSError as e:\n    print(e.args, repr(e), [e])",
     "(2, 'No such file or directory') FileNotFoundError(2, 'No such file or directory') "
     "[FileNotFoundError(2, 'No such file or directory')]\n", 0),
    ("try:\n    UnicodeDecodeError('x')\nexcept TypeError as e:\n    print(e)",
     "function takes exactly 5 arguments (1 given)\n", 0),
    ("try:\n    raise UnicodeDecodeError\nexcept TypeError as e:\n    print(e)",
     "function takes exactly 5 arguments (0 given)\n", 0),
    # --- round 2: float(bytes) ----------------------------------------------
    ("print(float(b'1'), float(b' 2.5 '), float(b'-inf'), float(b'1_0'))", "1.0 2.5 -inf 10.0\n", 0),
    ("try:\n    float(b'\\xff')\nexcept ValueError as e:\n    print(e)",
     "could not convert string to float: b'\\xff'\n", 0),
    # --- round 2: annotations are lazy on 3.14 -------------------------------
    ("def f(a: print('ann'), *b: print('b'), **k: Undefined) -> print('r'):\n    pass\nprint('ok')",
     "ok\n", 0),
    # --- review of the merged branch (CPython 3.14.5, 2026-09-25) ------------
    # `except E as x` in a closure unbinds the CLOSURE's `x`: the enclosing
    # function's `x` is not the one read
    ("def outer():\n    x = 'outer'\n    def inner():\n        try:\n            int('q')\n"
     "        except ValueError as x:\n            pass\n        return x\n    return inner()\n"
     "try:\n    print(outer())\nexcept UnboundLocalError as e:\n    print(type(e).__name__, e)",
     "UnboundLocalError cannot access local variable 'x' where it is not associated with a value\n", 0),
    ("def outer():\n    y = 1\n    def inner():\n        print(y)\n        y = 2\n    inner()\n"
     "try:\n    outer()\nexcept UnboundLocalError as e:\n    print(type(e).__name__, e)",
     "UnboundLocalError cannot access local variable 'y' where it is not associated with a value\n", 0),
    # a NameError two scopes deep on a name NO function assigns is the plain
    # one, caught or not
    ("def f():\n    def g():\n        try:\n            return zz\n        except NameError:\n"
     "            return 0\n    return g()\nprint(f())",
     "0\n", 0),
    ("def f(xs):\n    try:\n        return [undefined for x in xs]\n    except NameError as e:\n"
     "        return str(e)\nprint(f([1]))",
     "name 'undefined' is not defined\n", 0),
    # an assert's non-str message, caught and read through str(): exact
    ("def check(v):\n    assert v > 0, v\ntry:\n    check(-1)\nexcept AssertionError as e:\n"
     "    print('AE', e)",
     "AE -1\n", 0),
    # a bare annotated local no nested scope reads
    ("def main():\n    count: int\n    count = 3\n    fn = lambda s: s.upper()\n"
     "    print(count, fn('x'), sep='|')\nmain()",
     "3|X\n", 0),
]

#: Programs CPython rejects at compile time, and exit 1. The LAST stderr line
#: written here is CPython 3.14.5's (2026-09-24); the engine is held to the
#: reference interpreter's own last line, which 3.9 words "invalid syntax".
SYNTAX = [
    ("def f(**k, x):\n    pass", "SyntaxError: arguments cannot follow var-keyword argument"),
    ("def f(**k: int, x):\n    pass", "SyntaxError: arguments cannot follow var-keyword argument"),
    ("def f(*a, **k, *b):\n    pass", "SyntaxError: arguments cannot follow var-keyword argument"),
    ("f = lambda **k, x: 0", "SyntaxError: arguments cannot follow var-keyword argument"),
    ("def f(*a, *b):\n    pass", "SyntaxError: * argument may appear only once"),
    ("def f(*a: int = 1):\n    pass", "SyntaxError: var-positional argument cannot have default value"),
    ("def f(**a = 1):\n    pass", "SyntaxError: var-keyword argument cannot have default value"),
]

#: Valid Python that exited 1 (or answered wrongly) and must now refuse.
REFUSED = [
    # genexp attributes: AttributeError at exit 1 where CPython answers
    "g=(x for x in [1])\nprint(g.close())\nprint(list(g))",
    "g=(x for x in [1])\nprint(g.send(None))",
    "g=(x for x in [1])\nprint(g.gi_running)",
    "g=(x for x in [1])\ng.throw(ValueError)",
    # starred items after the first in a display, and in a bare tuple
    "a=[1,2]\nprint([0,*a])",
    "a=[1,2]\nprint((0,*a))",
    "a=[1,2]\nprint({0,*a})",
    "la=[1,2]\nprint('%s %s %s' % (0,*la))",
    "a=[1,2]\nb=0,*a\nprint(b)",
    "def f(a):\n    return 0, *a\nprint(f([1]))",
    "a=[1]\nfor x in 0, *a:\n    print(x)",
    # a leading star then a trailing comma, closed by `;`, `)` or `]`: the
    # starred branch of `expr_list` stopped only at a newline or `=`, so this
    # was a SyntaxError at exit 1
    "a=[1,2];t=*a,;print(t)",
    # CPython's TabError and IndentationError: subclasses whose names are the
    # last stderr line, which the engine's SyntaxError printed instead
    "if 1:\n\tx=1\n        y=2\nprint(1)",
    "if 1:\n    x=1\n  y=2\nprint(1)",
    # an except clause that is not a name or a flat tuple of names: valid
    # Python the parser called a SyntaxError (py-c4b2d35e4022), and 3.14's
    # unparenthesised `except A, B` (PEP 758)
    "try:\n    raise KeyError(1)\nexcept ((KeyError, ValueError), TypeError):\n    print('c')",
    "try:\n    raise ValueError('x')\nexcept (ValueError, (KeyError,)):\n    print(1)",
    # an attribute a flat exception value does not keep: AttributeError at the
    # program's own exit 1 where CPython answers (py-b24ca4b953ce)
    "import json\ntry:\n    json.loads('x')\nexcept json.JSONDecodeError as e:\n    print('json', e.msg, e.pos)",
    "try:\n    raise ValueError('a')\nexcept ValueError as e:\n    print(e.with_traceback(None) is e)",
    "try:\n    open(\"no/x: 'y\")\nexcept OSError as e:\n    print(e.filename)",
    # an f-string field that reuses the f-string's own quote: 3.12+ (PEP 701)
    "d = {'k': 1}\nprint(f\"{d[\"k\"]}\")",
    "a=[1,2]\nprint([*a,])",
    "[a, *b] = [1,2,3]\nprint(a, b)",
    # an exception whose `args` the flat (kind, message) value cannot carry
    "try:\n    raise ValueError('')\nexcept ValueError as e:\n    print(e.args, repr(e))",
    "s=''\ntry:\n    raise ValueError(s)\nexcept ValueError as e:\n    print(repr(e))",
    "try:\n    assert False, ''\nexcept AssertionError as e:\n    print(e.args, repr(e))",
    "try:\n    assert False, 5\nexcept AssertionError as e:\n    print(e.args, repr(e))",
    # caught through a call: the `try` is dynamic, not lexical
    "def f():\n    assert 0, 5\ntry:\n    f()\nexcept AssertionError as e:\n    print(e.args)",
    # StopIteration WITH an argument inside map(): `next()` would re-raise it
    "def f(x):\n    raise StopIteration('m')\nprint(list(map(f, [1])))",
    # `from __future__` inside a def: CPython's own SyntaxError, never exit 1 here
    "def f():\n    from __future__ import annotations\n    return 1\nprint(f())",
    # --- round 2 ------------------------------------------------------------
    # a closure made in a nested function that declares `global`: its free
    # variable resolves through the declaration, which the chain does not carry
    "x = 0\ndef f():\n    x = 1\n    def g():\n        global x\n        return (lambda: x)()\n    return g()\nprint(f())",
    # sets are partially ordered: CPython's answer is timsort's comparison order
    "print(sorted([{1,2},{3},{1}]))",
    "print(min([{1,2},{3},{1}]))",
    "print(max([{1},{1,2}]))",
    "print([{1}] < [{2}])",
    # the sign of a NaN depends on how CPython made it
    "import math\nx = float('-nan')\nprint(math.copysign(1, x ** 1))",
    "import math\nn = float('nan')\nprint(math.copysign(1, -n + n))",
    "import math\nprint(math.copysign(1.0, float('-nan')))",
    # a Unicode*Error keeps only its message, not its five arguments
    "try:\n    b'\\xff'.decode()\nexcept UnicodeDecodeError as e:\n    print(e.args)",
    "try:\n    b'\\xff'.decode()\nexcept UnicodeDecodeError as e:\n    print(repr(e))",
    "try:\n    b'\\xff'.decode()\nexcept UnicodeDecodeError as e:\n    print(e.start, e.reason)",
    "try:\n    'é'.encode('ascii')\nexcept UnicodeEncodeError as e:\n    print(e.args)",
    "try:\n    open('/nonexistent/zz')\nexcept OSError as e:\n    print(e.filename2)",
    # a message spelled like an engine OS error would be read back as one
    "e = FileNotFoundError('[Errno 2] x')\nprint(e.errno)",
    # a starred subscript
    "t = {(1, 2): 'x'}\na = [1, 2]\nprint(t[*a])",
    "t = {(0, 1, 2): 'x'}\na = [1, 2]\nprint(t[0, *a])",
    # a free variable of a nested function, read before the enclosing binds it
    "def f():\n    g = lambda: x\n    return g()\n    x = 1\ntry:\n    print(f())\n"
    "except NameError as e:\n    print(e)",
]

#: Rows the CORE's static walk must send to CPython. The walk can see them, so
#: a program must not start on a variant and die there.
ROUTED_TO_CPYTHON = [
    "a=[1,2]\nb=0,*a\nprint(b)",
    "def f(a):\n    return 0, *a\nprint(f([1]))",
    "a=[1,2]\nprint([0,*a])",
    "la=[1,2]\nprint('%s %s' % (0,*la))",
    "t = {(1, 2): 'x'}\na = [1, 2]\nprint(t[*a])",
]


def _find(engine: str) -> Path | None:
    target = {engines.LYPNING: paths.RUST_DIR / "target" / "release" / "lypning",
              engines.LYPNING_L: paths.RUST_DIR / "target" / "variant-l" / "release" / "lypning"}
    found = engines.find(engine)
    for cand in ([Path(found)] if found else []) + [target[engine]]:
        if cand.is_file():
            return cand
    return None


VARIANTS = [(e, _find(e)) for e in (engines.LYPNING, engines.LYPNING_L)]
BUILT = [pytest.param(e, b, id=e) for e, b in VARIANTS if b is not None]
CORE = dict(VARIANTS).get(engines.LYPNING)


#: The written-down bytes are one version's; every other version is compared
#: live, engine against the interpreter running the suite.
PINNED_314 = pytest.mark.skipif(sys.version_info[:2] != (3, 14),
                                reason="bytes pinned to CPython 3.14.5")


def _run(argv: list[str], program: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True, text=True,
                              cwd=d, timeout=60)


def _refusal_problem(engine: str, got: subprocess.CompletedProcess) -> str | None:
    if got.returncode != engines.UNSUPPORTED_EXIT:
        return "exit %d, not %d" % (got.returncode, engines.UNSUPPORTED_EXIT)
    if got.stdout != "":
        return "stdout was not empty: %r" % got.stdout[:120]
    line = got.stderr.strip()
    head = "%s: unsupported: " % engine
    if not line.startswith(head) or "\n" in line:
        return "stderr was %r, expected one %r line" % (line[:160], head)
    return None


def test_both_variants_are_measured() -> None:
    """A variant that is not built is a hole, never a pass (CLAUDE.md, §C12)."""
    missing = [e for e, b in VARIANTS if b is None]
    if missing:
        pytest.skip("not built: %s" % ", ".join(missing))


@pytest.mark.parametrize("engine,binary", BUILT)
@pytest.mark.parametrize("program,stdout,code", ANSWERED, ids=range(len(ANSWERED)))
def test_the_fixed_rows_answer_what_cpython_answers(
        engine: str, binary: Path, program: str, stdout: str, code: int) -> None:
    got = _run([str(binary)], program)
    ref = _run([sys.executable], program)
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "%s disagrees with the reference CPython %s.\n  program: %r\n"
        "  got:       %r exit %d %s\n  reference: %r exit %d"
        % (engine, sys.version.split()[0], program, got.stdout, got.returncode,
           got.stderr.strip()[-200:], ref.stdout, ref.returncode)
    )


@PINNED_314
@pytest.mark.parametrize("program,stdout,code", ANSWERED, ids=range(len(ANSWERED)))
def test_the_pinned_bytes_are_this_cpythons_bytes(program: str, stdout: str, code: int) -> None:
    ref = _run([sys.executable], program)
    assert (ref.stdout, ref.returncode) == (stdout, code), (
        "CPython %s prints something else for this row: %r exit %d\n  program: %r"
        % (sys.version.split()[0], ref.stdout, ref.returncode, program)
    )


@pytest.mark.parametrize("engine,binary", BUILT)
@pytest.mark.parametrize("program", REFUSED, ids=range(len(REFUSED)))
def test_the_unserved_rows_refuse_cleanly(engine: str, binary: Path, program: str) -> None:
    got = _run([str(binary)], program)
    problem = _refusal_problem(engine, got)
    assert problem is None, "%s\n  program: %r" % (problem, program)


@pytest.mark.skipif(CORE is None, reason="the core that routes is not built")
@pytest.mark.parametrize("program", ROUTED_TO_CPYTHON, ids=range(len(ROUTED_TO_CPYTHON)))
def test_the_walk_sends_a_starred_display_to_cpython(program: str) -> None:
    route = engines.route(program, binary=CORE)
    assert route.engine == engines.CPYTHON, (
        "routed to %r\n  program: %r\n  blocker: %s: %s"
        % (route.engine, program, route.kind, route.detail)
    )


@pytest.mark.parametrize("engine,binary", BUILT)
@pytest.mark.parametrize("program,last", SYNTAX, ids=range(len(SYNTAX)))
def test_the_syntax_errors_are_cpythons(engine: str, binary: Path, program: str, last: str) -> None:
    got = _run([str(binary)], program)
    ref = _run([sys.executable], program)
    want = ("", 1, ref.stderr.strip().splitlines()[-1:])
    assert ref.returncode == 1, (ref.returncode, ref.stderr, program)
    assert (got.stdout, got.returncode, got.stderr.strip().splitlines()[-1:]) == want, (
        "%s: %r exit %d %r\n  program: %r" % (engine, got.stdout, got.returncode, got.stderr, program))


@PINNED_314
@pytest.mark.parametrize("program,last", SYNTAX, ids=range(len(SYNTAX)))
def test_the_pinned_syntax_errors_are_this_cpythons(program: str, last: str) -> None:
    ref = _run([sys.executable], program)
    assert (ref.returncode, ref.stderr.strip().splitlines()[-1:]) == (1, [last])


@pytest.mark.parametrize("engine,binary", BUILT)
def test_sys_platform_is_the_hosts(engine: str, binary: Path) -> None:
    """`sys.platform` was `linux` everywhere; on macOS CPython says `darwin`."""
    if sys.platform not in ("darwin", "linux"):
        pytest.skip("sys.platform refuses on %s" % sys.platform)
    got = _run([str(binary)], "import sys\nprint(sys.platform)")
    assert (got.stdout, got.returncode) == (sys.platform + "\n", 0), got.stderr
