"""Name resolution, as a grid: which scope answers a read, and when it refuses to.

`Interp::lookup` walks the scope chain and then, before it looks at the globals,
probes the current function's assigned-name set — the one thing that separates
`UnboundLocalError` from "there is a global of that name". That probe is a hash
and a table walk on every name read that is not a local, and the name it is
asked about most is a global function being called from inside another one,
where the answer is always no. `hash::Names` puts a one-word filter in front of
it so the common no costs no hashing at all.

**A filter is a shortcut, and a shortcut is a way to be silently wrong.** It is
allowed to answer only in one direction — a clear bit means the name was never
inserted — so the risk is not the filter's arithmetic but the SET behind it:
every construct that binds a name into a frame has to reach
`eval::assigned_names`, and a construct that does not would now read as "not
assigned" twice as fast. That was already true of the `UnboundLocalError` this
grid is mostly made of; it is more load-bearing now, and nothing pinned it.

So this walks every binding form `collect_assigned` knows — assignment,
augmented assignment, `for`, `with ... as`, `except ... as`, `import`, `def` —
in the read-before-assign shape where CPython raises and a naive interpreter
finds the global of the same name and carries on. Beside them: `global` in the
forms that make a write escape the frame, closures over an enclosing scope,
comprehensions (which get their OWN scope, pushed onto the same chain), and
lambdas, whose assigned set is deliberately empty so that the filter must not be
allowed to conclude anything from it.

Every case runs on both interpreters and the whole line is compared, so this is
a test of the rules and not of anyone's reading of them. The cases that simply
work are in the grid on purpose: a change that made every name read raise
`UnboundLocalError` would pass a grid that only contained the raising ones.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from lypning import engines

needs_engine = pytest.mark.skipif(
    engines.find_lypning() is None, reason="the Rust core is not built"
)

GRID = r"""
log = []
def t(name, f):
    try:
        r = f()
    except Exception as e:
        r = "EXC:" + str(e)[:56]
    log.append("%s=%r" % (name, r))

x = "global-x"
n = 1

# A real regular file, written before the grid runs: `a4` must fail on the READ
# and never reach the `with`, and a version of it that reached one would then
# fail on the file instead — a different message for the same defect, which is
# how a broken filter once looked like an unsupported `open`.
SCRATCH = "scope-grid-scratch.txt"
open(SCRATCH, "w").write("s")

# --- read-before-assign, once per binding form collect_assigned walks --------
def a1():
    y = x
    x = "local"
    return y
def a2():
    y = n
    n += 1
    return y
def a3():
    y = x
    for x in []:
        pass
    return y
def a4():
    y = x
    with open(SCRATCH) as x:
        pass
    return y
def a5():
    y = x
    try:
        pass
    except ValueError as x:
        pass
    return y
def a6():
    y = x
    import os as x
    return y
def a7():
    y = x
    def x():
        return 1
    return y
def a8():
    y = x
    if False:
        x = "never"
    return y
def a9():
    y = x
    (x, z) = (1, 2)
    return y

# --- the same names, read where they ARE the global -------------------------
def b1():
    return x
def b2():
    return n + 1
def b3():
    return len(x)
def b4():
    return str(n) + x

# --- global -----------------------------------------------------------------
g = 0
def c1():
    global g
    g = 1
    return g
def c2():
    global g
    g += 1
    return g
def c3():
    return g
def c4():
    global fresh
    fresh = "made"
    return fresh
def c5():
    def inner():
        global g
        g = 99
    inner()
    g = "local-not-global"
    return g

# --- closures ---------------------------------------------------------------
def d1():
    v = 10
    def inner():
        return v + 1
    return inner()
def d2():
    v = 10
    def inner():
        return v
    v = 20
    return inner()
def d3():
    def inner():
        return x
    return inner()
def d4(k):
    def inner(m):
        return m * k
    return inner(3)

# --- comprehensions: their own scope, pushed onto the same chain ------------
def e1():
    return [i * 2 for i in range(4)]
def e2():
    m = 3
    return [i * m for i in range(4)]
def e3():
    return {k: k * k for k in range(3)}
def e4():
    return sum(i for i in range(5))
def e5():
    return [[j for j in range(i)] for i in range(3)]
def e6():
    return [i * n for i in range(3)]
def e7():
    q = [1, 2, 3]
    return [w for w in q if w > 1]

# --- lambdas: an EMPTY assigned set, and parameters that must still resolve --
def f1():
    return (lambda a, b=2: a + b)(1)
def f2():
    return (lambda a, b=2: a + b)(1, 5)
def f3():
    k = 4
    return (lambda z: z + k)(1)
def f4():
    return (lambda: x)()
def f5():
    return sorted([(2, "b"), (1, "a")], key=lambda p: p[0])
def f6():
    return (lambda *a, **kw: (a, sorted(kw.items())))(1, 2, u=3)

# --- **kwargs, built only when something lands in it ------------------------
def h1(**kw):
    return sorted(kw.items())
def h2(a, **kw):
    return (a, sorted(kw.items()))
def h3(a, *r, **kw):
    return (a, r, sorted(kw.items()))

# --- names chosen to land near each other in a one-word filter --------------
def k1():
    aa = 1
    return aa + len(x)
def k2():
    a = 1
    return a
def k3():
    nn = 2
    return nn + n
def k4():
    ff = 1
    return ff
def k5():
    return recur(3)
def recur(m):
    return 1 if m <= 1 else m * recur(m - 1)

cases = [a1, a2, a3, a4, a5, a6, a7, a8, a9,
         b1, b2, b3, b4,
         c1, c2, c3, c4, c5,
         d1, d2, d3, lambda: d4(7),
         e1, e2, e3, e4, e5, e6, e7,
         f1, f2, f3, f4, f5, f6,
         lambda: h1(), lambda: h1(z=1, y=2), lambda: h2(1), lambda: h2(1, z=9),
         lambda: h3(1, 2, 3, v=4),
         k1, k2, k3, k4, k5]
for i, fn in enumerate(cases):
    t("c%d" % (i + 1), fn)
print(len(log))
print("|".join(log))
"""


@needs_engine
def test_the_scope_grid_agrees_with_cpython(tmp_path, monkeypatch) -> None:
    # Both arms inherit this cwd and `a4` writes a file into it. `conftest`
    # redirects every path the PACKAGE resolves; the cwd of a program the test
    # spawns is the test's own to move, and a grid that wrote into the checkout
    # would be a test that dirties the tree it is run from.
    monkeypatch.chdir(tmp_path)
    ref = subprocess.run(
        [sys.executable, "-c", GRID], capture_output=True, text=True, timeout=120
    )
    assert ref.returncode == 0, "the oracle did not run: %s" % ref.stderr[-400:]
    got = engines.run(engines.LYPNING, GRID, timeout=120)
    if got.refused:
        pytest.fail(
            "lypning REFUSES the grid program — a construct it uses left the "
            "subset, so this is measuring nothing: %s" % got.stderr.strip()[:200]
        )
    assert got.returncode == 0, "lypning exited %d: %s" % (
        got.returncode,
        got.stderr.strip()[-400:],
    )

    mine, theirs = got.stdout.splitlines(), ref.stdout.splitlines()
    assert mine[0] == theirs[0], "the grids are different sizes — the program moved"
    a, b = mine[1].split("|"), theirs[1].split("|")
    bad = [(p, q) for p, q in zip(a, b) if p != q]
    assert not bad, "%d of %s scope cases disagree with CPython: %s" % (
        len(bad),
        mine[0],
        ["lypning=%s cpython=%s" % (p, q) for p, q in bad[:6]],
    )
