"""Mandatory-answer differential guards for version-shaped runtime behavior.

The reference is the running Python. Build each engine for that interpreter;
neither refusals nor matching exception classes alone satisfy these checks.
"""

from __future__ import annotations

import sys

import pytest

from lypning import engines


CASES = [
    (
        "unpack-exact-versus-iterable",
        "for xs in ([1,2,3], (1,2,3), {1:0,2:0,3:0}, 'abc', range(3), iter([1,2,3])):\n"
        "    try:\n"
        "        a, = xs\n"
        "    except ValueError as e:\n"
        "        print(type(e).__name__, e)\n",
    ),
    (
        "unpack-consumes-only-one-extra",
        "it = iter([1,2,3,4])\n"
        "try:\n"
        "    a, = it\n"
        "except ValueError as e:\n"
        "    print(e)\n"
        "print(list(it))\n"
        "try:\n"
        "    a, = (1 // x for x in [1,1,0])\n"
        "except Exception as e:\n"
        "    print(type(e).__name__, e)\n",
    ),
    (
        "unpack-short-starred-and-nested",
        "for xs in ([], [1], [1,2], [1,2,3]):\n"
        "    try:\n"
        "        a, b = xs\n"
        "        print(a,b)\n"
        "    except ValueError as e:\n"
        "        print(e)\n"
        "    try:\n"
        "        a, *b, c = xs\n"
        "        print(a,b,c)\n"
        "    except ValueError as e:\n"
        "        print(e)\n"
        "try:\n"
        "    (a,), = [(1,2,3)]\n"
        "except ValueError as e:\n"
        "    print(e)\n",
    ),
    (
        "list-index-error-and-neighbors",
        "for xs, x in (([1,2,3],3), (['a','b'],'b'), ([],None)):\n"
        "    for start,stop in ((0,0),(0,2),(9,99),(-99,-1)):\n"
        "        try:\n"
        "            print(xs.index(x,start,stop))\n"
        "        except ValueError as e:\n"
        "            print(e)\n"
        "try:\n"
        "    (1,2).index(3)\n"
        "except ValueError as e:\n"
        "    print(e)\n",
    ),
    (
        "zero-width-signed-byte-conversion",
        "for n in (-2,-1,0,1,2):\n"
        "    for length in (0,1,8):\n"
        "        for signed in (True,False):\n"
        "            for order in ('big','little'):\n"
        "                try:\n"
        "                    print(n,length,signed,order,n.to_bytes(length,order,signed=signed))\n"
        "                except (OverflowError,ValueError) as e:\n"
        "                    print(type(e).__name__, e)\n",
    ),
    (
        "division-errors",
        "for a in (1,-7,1.0,-7.0):\n"
        "    for b in (0,0.0,-0.0):\n"
        "        for f in (lambda: a/b,lambda: a//b,lambda: a%b):\n"
        "            try:\n"
        "                print(f())\n"
        "            except ZeroDivisionError as e:\n"
        "                print(e)\n",
    ),
]


@pytest.mark.parametrize("engine", [engines.LYPNING, engines.LYPNING_L])
@pytest.mark.parametrize("name,program", CASES, ids=[c[0] for c in CASES])
def test_version_shaped_semantics_must_answer(engine, name, program, monkeypatch):
    monkeypatch.setenv("LYPNING_CPYTHON", sys.executable)
    binary = engines.find(engine)
    if binary is None:
        pytest.skip("%s is not built" % engine)
    got = engines.run(engine, program, binary=binary)
    reference = engines.run(engines.CPYTHON, program)
    assert reference.returncode == 0, reference.stderr
    assert (got.returncode, got.stdout, got.stderr) == (
        0, reference.stdout, reference.stderr
    ), (name, got.stdout, got.stderr, reference.stdout)


def test_dict_subclasses_keep_the_generic_unpack_error(monkeypatch):
    monkeypatch.setenv("LYPNING_CPYTHON", sys.executable)
    binary = engines.find(engines.LYPNING_L)
    if binary is None:
        pytest.skip("lypning-l is not built")
    program = (
        "from collections import Counter, defaultdict\n"
        "d = defaultdict(int)\n"
        "d.update({'a':1,'b':2,'c':3})\n"
        "for xs in (Counter('abc'),d):\n"
        "    try:\n"
        "        a, = xs\n"
        "    except ValueError as e:\n"
        "        print(e)\n"
    )
    got = engines.run(engines.LYPNING_L, program, binary=binary)
    reference = engines.run(engines.CPYTHON, program)
    assert (got.returncode, got.stdout, got.stderr) == (
        0, reference.stdout, reference.stderr
    )
