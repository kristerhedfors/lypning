"""Classes, as a grid: every program on the binary and on CPython.

`tests/test_pathlib_grid.py` is the shape this follows. It exists for the
reason that one does — the defect is PER PROGRAM, not per feature — and for one
more that is specific to a LANGUAGE capability: `lypning conformance` grades the
corpus, and the corpus contains two programs whose first blocker is a class.
Nothing in the battery can tell whether `sorted()`, `in`, `%`, `json.dumps` and
a dict key do the right thing when they meet an instance. This file is that
instrument.

Every row must end one of exactly two ways:

  * byte-identical stdout AND the same exit code as CPython 3.x, or
  * a clean refusal — exit 90, nothing on stdout, one
    ``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

Nothing else passes, and "close" is the failure invariant 1 exists for.

The traps this was written against, each measured against CPython before the
code was written and each with a block of rows below:

1. **The default repr contains an address.** `repr(C())` is
   `<__main__.C object at 0x104a2f350>` — unreproducible, and different between
   two runs of CPython itself. So an instance whose class defines neither
   `__repr__` nor `__str__` REFUSES wherever text is asked of it. That is the
   whole reason this capability refuses as much as it answers, and `REFUSED`
   below is mostly this trap seen from a different call site each time.
2. **`repr()` of an instance is the string `__repr__` returned, not a repr of
   it.** Rendering the instance into a `Value::Str` and then letting the builtin
   run gave `'R(7)'` — quotes and all — for a program CPython prints `R(7)` for,
   at exit 0. `REPR` holds it, including the `%r`, `!r` and `[obj]` spellings.
3. **`object` gives an instance two dozen attributes for free.** `__dict__`,
   `__module__`, `__doc__`, `__hash__` are answered by CPython and were an
   `AttributeError` here — exit 1, the program's own exit, which the chain never
   retries. Every dunder this engine does not implement refuses; a plain missing
   name stays an `AttributeError`, which is CPython's answer too.
4. **An instance is hashable in CPython** (`{obj: 1}` works, keyed by `id()`),
   so `unhashable type` would have been exit 1 where CPython answers. It is a
   refusal instead — keying by the pointer is right only while the object is
   alive, and a freed address is handed to the next instance.
5. **A class body is not a closure.** `class C: n = 5` then a method whose body
   reads a bare `n` is a `NameError` in CPython. Building methods with the class
   scope in their environment would have answered 5. `SCOPING` holds it.
6. **`type(obj)` is printable and the instance is not.** `<class '__main__.C'>`
   is exactly reproducible for a class defined at module level with `-c`; a
   class defined inside a function has `__qualname__` `f.<locals>.C` and is
   refused rather than printed wrong.

One shape is deliberately answered rather than refused, and it is worth naming:
the TypeErrors that fall out of the interpreter's generic paths — `len(C())`,
`C() < C()`, `C() + 1`, `json.dumps(C())` — are CPython's own messages word for
word once the type is named for the class, so they are grid rows and not
refusals. They are in `TYPE_ERRORS`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

#: The class every block below builds on: an `__init__`, a plain method, an
#: instance attribute and a default argument.
C = ("class C:\n"
     "    def __init__(self, n=1):\n"
     "        self.n = n\n"
     "    def bump(self):\n"
     "        self.n += 1\n"
     "        return self.n\n")

#: …and the same with a `__repr__`, which is what makes it printable at all.
R = ("class R:\n"
     "    def __init__(self, v=1):\n"
     "        self.v = v\n"
     "    def __repr__(self):\n"
     "        return 'R(%d)' % self.v\n")

#: `__str__` alone, and both together — `str()` falls back to `__repr__` and
#: `repr()` never falls back to `__str__`, which is the asymmetry to hold.
S = "class S:\n    def __str__(self):\n        return 'S!'\n"
B = ("class B:\n"
     "    def __repr__(self):\n        return 'B<>'\n"
     "    def __str__(self):\n        return 'Bs'\n")

#: Construction, `__init__`, methods, attributes.
BASIC = [
    C + "print(C().n, C(7).n, C(n=7).n)",
    C + "c = C(3)\nprint(c.n, c.bump(), c.n)",
    C + "c = C()\nc.x = 5\nprint(c.x, c.n)",
    C + "c = C()\nc.n += 4\nprint(c.n)",
    C + "print(C().bump() + C(10).bump())",
    C + "c = C()\nf = c.bump\nprint(f(), f(), c.n)",
    C + "print(len([C(), C()]), bool(C()), not C())",
    C + "print(C().nosuch)",
    C + "print(C.nosuch)",
    C + "print(C(1, 2))",
    "class D: pass\nprint(D() is not None)",
    "class D: pass\nprint(D(1))",
    "class D:\n    'a docstring'\n    pass\nprint(D() is not None)",
    "class D(object):\n    def __init__(self):\n        self.v = 1\nprint(D().v)",
    "class D:\n    def __init__(self, a, b, c=3, *d, **e):\n        self.t = (a, b, c, d, e)\n"
    "print(D(1, 2, 3, 4, x=5).t)",
    "class D:\n    def f(self, a, b=2):\n        return a + b\n"
    "print(D().f(1), D().f(1, 5), D().f(1, b=9))",
    "class D:\n    def f(self, a):\n        return a\nprint(D().f(1, 2))",
    "class D:\n    def f(self, a):\n        return a\nprint(D().f())",
    "class D:\n    def m(self):\n        return 1\nprint(D.m(D()))",
    "class D:\n    def f(self):\n        return self.g()\n    def g(self):\n        return 7\n"
    "print(D().f())",
    "class D:\n    def f(self, n):\n        if n <= 0:\n            return 0\n"
    "        return n + self.f(n - 1)\nprint(D().f(4))",
    "class D:\n    def __init__(self):\n        self.xs = []\n"
    "    def add(self, v):\n        self.xs.append(v)\n        return self\n"
    "print(D().add(1).add(2).xs)",
    "class D:\n    def __init__(self):\n        self.d = {}\n"
    "d = D()\nd.d['a'] = 1\nprint(d.d, len(d.d))",
    "class D:\n    def __init__(self):\n        self.s = set()\n"
    "    def add(self, x):\n        self.s.add(x)\n        return len(self.s)\n"
    "d = D()\nprint(d.add(1), d.add(1), d.add(2))",
    # Two classes, and the same name twice.
    "class D: pass\nclass E: pass\nprint(D is E, D == E, isinstance(D(), E))",
    "class D: pass\nclass D: pass\nprint(D().__class__.__name__)",
    # A class statement inside a module-level loop runs once per iteration.
    "for i in range(2):\n    class D: pass\n    print(D().__class__.__name__)",
]

#: Trap 5, and the class body's own scope.
SCOPING = [
    "class D:\n    x = 5\n    y = x + 1\n    def get(self):\n        return self.x\n"
    "print(D.x, D.y, D().get(), D().y)",
    "class D:\n    n = 5\n    def m(self):\n        return n\nprint(D().m())",
    "n = 9\nclass D:\n    n = 5\n    def m(self):\n        return n\nprint(D().m())",
    "G = 1\nclass D:\n    def f(self):\n        return G\nprint(D().f())",
    "class D:\n    def f(self):\n        return g()\ndef g():\n    return 8\nprint(D().f())",
    "class D:\n    def f(self):\n        global G\n        G = 3\n        return G\n"
    "print(D().f(), G)",
    "class D:\n    n = 0\n    def get(self):\n        return D.n\nprint(D().get())",
    "class D:\n    xs = []\n    def add(self, v):\n        self.xs.append(v)\n        return D.xs\n"
    "print(D().add(1), D.xs)",
]

#: Trap 1 and trap 2. Every path that turns an object into text.
REPR = [
    R + "print(R())",
    R + "print(repr(R()))",
    R + "print(str(R()))",
    R + "print(R(), R(2))",
    R + "print([R(), R(2)])",
    R + "print((R(),), {1: R()}, [[R()]])",
    R + "print(repr([R()]), str([R()]))",
    R + "print(f'{R()}', f'{R()!r}', f'{R()!s}')",
    R + "print(f'{R()!r:>10}|')",
    R + "print(f'{R()!s:>10}|')",
    R + "w = 8\nprint(f'{R()!r:>{w}}|')",
    R + "print('%s|%r' % (R(), R()))",
    R + "print('%10s|' % R())",
    R + "print('{}'.format(R()), '{!r}'.format(R()))",
    R + "print('{0} {0}'.format(R()))",
    R + "print(format(R()), format(R(), ''))",
    # `str(x, enc)` is a DECODE, not a render: rendering it answered the
    # instance's text at exit 0 where CPython raises. Both spellings.
    R + "print(str(R(), 'utf-8'))",
    R + "print(str(R(), errors='replace'))",
    R + "print(str(R(),))",
    R + "print(R(), R(2), sep='-')",
    R + "print(R(), end='!\n')",
    R + "print(print(R()))",
    R + "print('%s %s' % (R(), R(2)))",
    R + "print('%r' % (R(),))",
    R + "print('%-6s|' % R())",
    R + "print('%(a)s' % {'a': R()})",
    R + "print('{a}'.format(a=R()))",
    R + "print('{0!r}'.format(R()))",
    R + "print('{!s}'.format(R()))",
    R + "print('{}{}'.format(R(), 1))",
    R + "class W:\n    def __init__(self):\n        self.r = R(5)\n"
        "    def __repr__(self):\n        return 'W(%r)' % self.r\nprint(W(), W().r, W().r.v)",
    R + "class W:\n    def __init__(self):\n        self.rs = [R(1), R(2)]\n"
        "print(W().rs[0], len(W().rs))",
    R + "print(', '.join([repr(R()), repr(R(2))]))",
    R + "print([repr(x) for x in [R(1), R(2)]])",
    R + "print(sorted([repr(R(2)), repr(R(1))]))",
    R + "import json\nprint(json.dumps(repr(R())))",
    S + "print(S(), str(S()), f'{S()}', '%s' % S())",
    B + "print(B(), str(B()), repr(B()), f'{B()}', f'{B()!r}')",
    B + "print([B()], '%r' % B(), '%s' % B())",
    # `__repr__` that is not a str, and one that raises: both CPython's own.
    "class D:\n    def __repr__(self):\n        return 1\nprint(repr(D()))",
    "class D:\n    def __str__(self):\n        return 1\nprint(D())",
    "class D:\n    def __repr__(self):\n        raise ValueError('boom')\nprint(D())",
    # …and one that reads the instance it is printing.
    R + "r = R(3)\nr.v = 9\nprint(r)",
]

#: Trap 6, plus `isinstance` — the two builtins that expose the class object.
TYPES = [
    C + "print(type(C()))",
    C + "print(C)",
    C + "print(str(C), repr(C), f'{C}')",
    C + "print(C.__name__, type(C()).__name__)",
    C + "print(type(C()) is C, type(C()) == C, type(C()) is type(C()))",
    C + "print(isinstance(C(), C), isinstance(1, C), isinstance(C(), int))",
    C + "print(isinstance(C(), (int, str)))",
    C + "print(C() .__class__ is C)",
    "class D: pass\nclass E: pass\nprint(isinstance(D(), E), isinstance(D(), D))",
    "class D: pass\nprint([D])",
    "class D: pass\nprint(D == D, D is D, D != D)",
    "class D: pass\nprint(type(D))",
    "class D: pass\nprint(isinstance(D(), object))",
]

#: The recurring defect's own list: every builtin and operator a NEW value
#: variant is reached by. Each of these is either CPython's answer or a refusal;
#: none may be a wrong answer and none may be exit 1 where CPython answers 0.
REACH = [
    R + "a = R()\nprint(a == a, a == R(), a != R(), a is a, a is R())",
    R + "print(R() == 1, 1 == R(), R() == None, R() != 'x')",
    R + "a = R()\nprint([a] == [a], (a,) == (a,), [R()] == [R()])",
    R + "a = R()\nprint(a in [a], a in (a,), a in [1, 2], R() in [R()])",
    R + "a = R()\nprint([a].count(a), [a].index(a), [1, a].count(a))",
    R + "print(len([R(), R()]), len({'a': R()}))",
    R + "print(sorted([r.v for r in [R(2), R(1)]]))",
    R + "print(sorted([R(2), R(1)], key=lambda r: r.v)[0].v)",
    R + "xs = [R(2), R(1)]\nxs.sort(key=lambda r: r.v)\nprint(xs[0].v)",
    R + "print(max([R(1), R(2)], key=lambda r: r.v).v, min([R(1)], key=lambda r: r.v).v)",
    R + "print(any([R()]), all([R()]), bool(R()))",
    R + "print(list(reversed([R(1), R(2)]))[0].v)",
    R + "print(list(map(lambda r: r.v, [R(3)])), [r.v for r in [R(1)]])",
    R + "print(sum(r.v for r in [R(1), R(2)]))",
    R + "d = {}\nd['k'] = R(4)\nprint(d['k'].v, len(d), 'k' in d)",
    R + "print(len([x for x in [R(), R()] if x]))",
    R + "print(next(iter([R(5)])).v)",
    R + "print(list(enumerate([R(1)]))[0][0])",
    R + "print(list(zip([R(1)], [2]))[0][1])",
    R + "print(tuple([R(1)])[0].v, list([R(1)])[0].v)",
    "class D:\n    def f(self, x):\n        return x * 2\nprint(list(map(D().f, [1, 2])))",
    "class D:\n    def f(self, x):\n        return x > 1\nprint(list(filter(D().f, [1, 2, 3])))",
    "class D:\n    def k(self, x):\n        return -x\nprint(sorted([1, 3, 2], key=D().k))",
    "class D:\n    def f(self):\n        return 1\nd = D()\ng = d.f\nprint(g())",
    # An attribute name a class defines is admitted for EVERY receiver; these
    # hold that the receiver that is not an instance still answers correctly.
    "class D:\n    def upper(self):\n        return 1\nprint('abc'.upper())",
    "class D:\n    def n(self):\n        return 1\nd = {'n': 2}\nprint(d['n'])",
    # A 50-deep instance chain, for the iterative teardown in `value::dismantle`.
    "class N:\n    def __init__(self):\n        self.next = None\n"
    "n = N()\nfor i in range(50):\n    m = N()\n    m.next = n\n    n = m\nprint('ok')",
    R + "print([R(1)][0].v, [R(1)][0:1][0].v, [R(1), R(2)][-1].v)",
]

#: The generic TypeErrors — CPython's messages word for word, so rows and not
#: refusals. Both sides exit 1 with nothing on stdout.
TYPE_ERRORS = [
    R + "print(len(R()))",
    R + "print(R()[0])",
    R + "print(R()[0:1])",
    R + "print(list(R()))",
    R + "print(R() + 1)",
    R + "print(R() + R())",
    R + "print('a' + R())",
    R + "print(R() * 2)",
    R + "print(-R())",
    R + "print(R() < R())",
    R + "print(sorted([R(1), R(2)]))",
    R + "print(max([R(1), R(2)]))",
    R + "import json\nprint(json.dumps(R()))",
    R + "import json\nprint(json.dumps({'a': R()}))",
    R + "print(int(R()))",
    R + "print(float(R()))",
    R + "print(', '.join([R()]))",
    C + "c = C()\nprint('made')\nprint(c.nosuch)",
    "class D: pass\ntry:\n    raise D()\nexcept TypeError:\n    print('te')",
]

#: The commit barrier and the other capabilities, because a class is a value
#: those paths now carry.
MIXED = [
    "import collections\nclass D:\n    def __init__(self):\n        "
    "self.c = collections.Counter('aab')\nprint(D().c['a'], D().c.most_common(1))",
    "from pathlib import Path\nclass D:\n    def __init__(self, p):\n        self.p = Path(p)\n"
    "print(D('a/b').p.name, D('a/b').p.parent)",
    "import re\nclass D: pass\nprint(re.escape('a.b'), D() is not None)",
    "import os\nclass D:\n    def __init__(self):\n        self.p = os.path.join('a', 'b')\n"
    "print(D().p)",
    "import json\nclass D:\n    def __init__(self):\n        self.d = {'a': 1}\n"
    "print(json.dumps(D().d))",
    "import random\nrandom.seed(7)\nprint(random.random())\nclass D: pass",
    "class D:\n    def save(self, t):\n        open('f.txt', 'w').write(t)\n"
    "        return open('f.txt').read()\nprint(D().save('hi'))",
    "class D:\n    def f(self):\n        with open('f.txt', 'w') as h:\n            h.write('x')\n"
    "        return open('f.txt').read()\nprint(D().f())",
    "class D:\n    def f(self):\n        raise ValueError('x')\n"
    "try:\n    D().f()\nexcept ValueError as e:\n    print(e)",
    "class D: pass\ntry:\n    D().nope\nexcept AttributeError:\n    print('ok')",
    "class D: pass\nassert D()\nprint('yes')",
    "class D:\n    def __init__(self, n):\n        self.n = n\n"
    "    def __repr__(self):\n        return 'D(%d)' % self.n\n"
    "print([repr(D(i)) for i in range(3)])",
]

GRID = BASIC + SCOPING + REPR + TYPES + REACH + TYPE_ERRORS + MIXED

#: Every shape CPython answers and this engine must NOT, because any answer
#: would be a guess — an address, an order, or a `type` the engine does not
#: model. A row here is a promise that the program leaves NOTHING on stdout and
#: reaches CPython one spawn later.
REFUSED = [
    # Trap 1, from every call site that can reach an instance's text.
    C + "print(C())",
    C + "print(str(C()))",
    C + "print(repr(C()))",
    C + "print(f'{C()}')",
    C + "print('%s' % C())",
    C + "print('{}'.format(C()))",
    C + "print(format(C()))",
    C + "print([C()])",
    C + "print({'a': C()})",
    C + "print((C(),))",
    C + "c = C()\nprint('written first')\nprint(c)",
    # `__str__` alone still cannot answer `repr`, and a spec still cannot
    # reach `object.__format__`.
    S + "print(repr(S()))",
    S + "print([S()])",
    R + "print(f'{R():>10}')",
    R + "print(format(R(), '>4'))",
    R + "print(format(R(), 'd'))",
    R + "print(f'{R()=}')",
    R + "print('{:>10}'.format(R()))",
    R + "print(format(R(), '>8'))",
    R + "print(f'{R()!a}')",
    R + "print([R()])",
    R + "print(repr(R()) if False else [R()])",
    R + "print('%s' % R())",
    R + "print('{}'.format(R()))",
    # Trap 3: the attributes `object` and `type` answer for free.
    C + "print(C().__dict__)",
    C + "print(C.__dict__)",
    C + "print(C.__module__)",
    C + "print(C().__doc__)",
    C + "print(C.__qualname__)",
    C + "print(C().__hash__)",
    C + "print(C().bump.__name__)",
    C + "print(C().bump)",
    # Trap 4: hashing by identity.
    C + "print({C(): 1})",
    C + "print({C()})",
    C + "print(hash(C()))",
    C + "print(C() in {1: 2})",
    C + "print(C() in {1, 2})",
    # Trap 6 and every class shape outside the subset — all of them at PARSE
    # time, so `lypning route` sees them without a spawn.
    "class D(Exception): pass\nprint(1)",
    "class D(ValueError):\n    pass\nprint(1)",
    "class D(dict): pass\nprint(1)",
    "class D(str): pass\nprint(1)",
    "class D(int): pass\nprint(1)",
    "class D(A, B): pass\nprint(1)",
    "class D(object, dict): pass\nprint(1)",
    "class D(metaclass=type): pass\nprint(1)",
    "import collections\nclass D(collections.OrderedDict): pass\nprint(1)",
    "class D:\n    __slots__ = ('a',)\nprint(1)",
    "class D:\n    __hash__ = None\nprint(1)",
    "class D:\n    def __eq__(self, o):\n        return True\nprint(1)",
    "class D:\n    def __hash__(self):\n        return 1\nprint(1)",
    "class D:\n    def __len__(self):\n        return 3\nprint(1)",
    "class D:\n    def __iter__(self):\n        return iter([])\nprint(1)",
    "class D:\n    def __call__(self):\n        return 1\nprint(1)",
    "class D:\n    def __contains__(self, x):\n        return True\nprint(1)",
    "class D:\n    def __getattr__(self, n):\n        return 1\nprint(1)",
    "class D:\n    def __setattr__(self, n, v):\n        pass\nprint(1)",
    "class D:\n    def __lt__(self, o):\n        return True\nprint(1)",
    "class D:\n    def __add__(self, o):\n        return 1\nprint(1)",
    "class D:\n    def __enter__(self):\n        return self\nprint(1)",
    "class D:\n    def __bool__(self):\n        return False\nprint(1)",
    "class D:\n    def __format__(self, s):\n        return 'x'\nprint(1)",
    "class D:\n    @property\n    def x(self):\n        return 1\nprint(1)",
    "class D:\n    @staticmethod\n    def x():\n        return 1\nprint(1)",
    "class D:\n    @classmethod\n    def x(cls):\n        return 1\nprint(1)",
    "import dataclasses\n@dataclasses.dataclass\nclass D:\n    a: int\nprint(1)",
    "class D:\n    def x():\n        return 1\nprint(1)",
    "class D:\n    class E: pass\nprint(1)",
    "class D:\n    if 1:\n        x = 1\nprint(1)",
    "class D:\n    for i in range(2):\n        pass\nprint(1)",
    "class D:\n    print('side effect')\nprint(1)",
    "class D:\n    a, b = 1, 2\nprint(1)",
    "class D:\n    x = 1\n    x.y = 2\nprint(1)",
    # Runtime refusals — a walk cannot see either of these.
    "def f():\n    class D: pass\n    return D\nprint(f())",
    "class D:\n    n = 1\nD.n = 2\nprint(D.n)",
    "class D:\n    def __init__(self):\n        super().__init__()\nprint(D() is not None)",
    # `getattr`/`hasattr`/`vars`/`dir` are not builtins here at all, and refuse
    # as such — asserted here because a class is what makes a program reach for
    # them.
    C + "print(getattr(C(), 'n'))",
    C + "print(hasattr(C(), 'n'))",
    C + "print(vars(C()))",
    C + "print(dir(C()))",
    C + "print(callable(C()))",
    C + "c = C()\nsetattr(c, 'q', 1)\nprint(c.q)",
    C + "c = C()\ndel c.n\nprint(1)",
]


def _spectrum(binary: Path) -> dict | None:
    """What ``route --spectrum`` says this binary is, or None."""
    try:
        out = subprocess.run([str(binary), "route", "--spectrum"],
                             capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def _current(engine: str) -> Path | None:
    """A built ``engine`` that carries THIS tree's capability table.

    The same gate `tests/test_pathlib_grid.py` uses and for the same reason: an
    installed binary from before this capability landed refuses every row above,
    which would turn the file into green skips measuring nothing.
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
        if any(row.get("cap") == "cap-class" for row in table.get("caps", [])):
            return cand
    return None


BINARY = _current(engines.LYPNING_L)
CORE = _current(engines.LYPNING)

needs_l = pytest.mark.skipif(
    BINARY is None,
    reason="no lypning-l carrying cap-class is built (cargo build --release "
           "--no-default-features --features variant-l --target-dir target/variant-l)",
)


def _run(argv: list[str], program: str) -> subprocess.CompletedProcess:
    """One program, in a temp cwd of its own — invariant 4."""
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
def test_the_class_grid_agrees_with_cpython(program: str) -> None:
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
    # A row that exits 0 must be silent on both sides too: a warning here would
    # be this engine inventing one.
    if got.returncode == 0:
        assert (got.stderr, ref.stderr) == ("", ""), program


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
@pytest.mark.parametrize("program", REFUSED, ids=range(len(REFUSED)))
def test_cpython_really_does_answer_every_refused_row(program: str) -> None:
    """The other half of the table above, and the reason it is worth having.

    A row in `REFUSED` is only interesting if CPython ANSWERS it — otherwise it
    proves nothing about a divergence, and it would keep passing after the shape
    it names stopped being a shape at all. `class D(A, B)` is the case in point:
    it is refused for multiple inheritance, and CPython raises `NameError`, so
    the assertion is that CPython gets *further* than a refusal — it runs the
    program and produces its own exit — not that it exits 0.
    """
    ref = _run([sys.executable], program)
    assert ref.returncode != engines.UNSUPPORTED_EXIT, program
    assert (ref.stdout != "" or ref.returncode != 0), (
        "this row prints nothing on CPython either, so refusing it proves "
        "nothing: %r" % program)


@needs_l
def test_a_parse_time_refusal_is_what_the_router_reads() -> None:
    """Every shape outside the subset refuses at PARSE time, where a walk sees it.

    A runtime refusal would be correct and still cost a spawn to be told no —
    and after an irreversible side effect it is not even correct, because the
    barrier has committed and exit 90 becomes exit 1. So the classifier must
    name the class refusal without running anything.
    """
    for program, detail in [
        ("class D(Exception): pass\nprint(1)", "a base other than"),
        ("class D(A, B): pass\nprint(1)", "multiple bases"),
        ("class D(metaclass=type): pass\nprint(1)", "metaclass or a computed base"),
        ("class D:\n    __slots__ = ()\nprint(1)", "dunder attribute"),
        ("class D:\n    def __eq__(self, o):\n        return True\nprint(1)", "dunder method"),
        ("class D:\n    class E: pass\nprint(1)", "not a method"),
    ]:
        out = subprocess.run([str(BINARY), "route", "-c", program],
                             capture_output=True, text=True, timeout=60)
        engine, _, why = out.stdout.strip().partition("\t")
        assert engine == engines.CPYTHON, (program, out.stdout)
        assert why.startswith("class: ") and detail in why, (program, why)


@needs_l
def test_the_capability_is_on_the_larger_variant_only() -> None:
    """The core must still REFUSE a class, and must route it to the sibling.

    A capability that leaked into the frozen variant would still pass every grid
    row above — it is the same code — so the byte budget is defended here, by
    asking each binary what it is.
    """
    core = CORE
    if core is None:
        pytest.skip("no core carrying this tree's capability table is built")
    refused = _run([str(core)], "class D: pass\nprint(1)")
    assert refused.returncode == engines.UNSUPPORTED_EXIT and refused.stdout == ""
    assert refused.stderr.strip() == engines.refusal_line(
        engines.LYPNING, "class", "class definition")

    # …and the core's ROUTER knows which sibling does serve it, which is the
    # half that makes the refusal cost one spawn instead of a CPython one. It
    # is also the half `cap-class` needed a `kinds` column for: a class is a
    # PARSE-time blocker, so the core reports it before it has seen an import
    # and no other fact could send the program to the sibling.
    route = subprocess.run([str(core), "route", "-c",
                            "class D:\n    def __init__(self):\n        self.n = 1\n"
                            "print(D().n)"],
                           capture_output=True, text=True, timeout=60)
    assert route.stdout.split("\t")[0].strip() == engines.LYPNING_L, route.stdout

    # But an import the sibling cannot serve still wins, even though the parse
    # stopped before the walker saw it: `route::scan_imports` is what reads it,
    # and without that every class program in a corpus full of them would cost
    # a spawn to be told no.
    away = subprocess.run([str(core), "route", "-c",
                           "import subprocess\nclass D: pass\nprint(1)"],
                          capture_output=True, text=True, timeout=60)
    assert away.stdout.split("\t")[0].strip() == engines.CPYTHON, away.stdout


@needs_l
def test_an_attribute_name_is_admitted_only_for_a_program_that_defines_it() -> None:
    """`.label` is an ordinary name on other objects.

    Admitting every attribute name for every receiver would take a program this
    engine sends to CPython today — which answers it — and run it here instead,
    where it stops at an `AttributeError`: exit 1, the program's own exit, which
    the chain never retries. The class body is what makes the router's optimism
    honest, and this is the assertion that says so.
    """
    without = subprocess.run([str(BINARY), "route", "-c", "x = 1\nprint(x.label)"],
                             capture_output=True, text=True, timeout=60)
    assert without.stdout.split("\t")[0].strip() == engines.CPYTHON, without.stdout
    with_class = subprocess.run(
        [str(BINARY), "route", "-c",
         "class D:\n    def label(self):\n        return 'x'\nprint(D().label())"],
        capture_output=True, text=True, timeout=60)
    assert with_class.stdout.split("\t")[0].strip() == engines.LYPNING_L, with_class.stdout


@needs_l
def test_the_python_copy_of_the_capability_table_is_the_binarys_own() -> None:
    """`engines.VARIANT_CAPS` is a copy of `route::SPECTRUM`'s caps column, and
    a copy is honest only while something checks it.

    `cap-class` is also the first row whose `kinds` column is not empty, so the
    shape of that column is pinned here as well.
    """
    table = _spectrum(BINARY)
    assert table is not None and table["self"] == engines.LYPNING_L
    assert "cap-class" in table["self_caps"]
    assert {r["name"]: tuple(r["caps"]) for r in table["spectrum"]} == engines.VARIANT_CAPS
    row = {r["cap"]: r for r in table["caps"]}["cap-class"]
    assert row["modules"] == [] and row["kinds"] == ["class"]
