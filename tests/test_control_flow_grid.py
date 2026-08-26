"""Control flow, as a grid: which clause runs, on which way out of the body.

Two defects lived here, and both are the kind that runs code CPython does not.

**`try/except/else` ran the else clause on `break` and on `continue`.** The
clause runs only when the body finished by FALLING OFF THE END — `break`,
`continue` and `return` all leave the statement without reaching it, since "no
exception was raised" and "the body ran to completion" are different facts. Any
flow at all used to run it, so

    while True:
        try: break
        else: print("else")

printed `else` before leaving the loop, and a `continue` ran it once per
iteration. Side effects are the ordinary reason to write an else clause, so this
could execute arbitrarily much code. `finally` is different and was already
right: it runs on every path, including these.

**A bare `raise` inside a handler did not re-raise.** It answered
`RuntimeError: No active exception to reraise` — correct outside a handler, and
wrong inside one, where

    except ValueError:
        log(...)
        raise

is the standard idiom for "record it and let it propagate". The interpreter now
keeps a STACK of the exceptions its enclosing handlers are handling, so a
try/except nested inside a handler does not lose the outer one when it finishes.

Every case here is run on both interpreters and the whole output is compared, so
this is a test of the rules rather than of anyone's reading of them. The
well-formed paths are in the grid too — a fix that simply stopped running the
else clause would pass a test that only checked `break`.
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
        r = "EXC:" + str(e)[:20]
    log.append("%s=%r" % (name, r))

# try/else with each exit path from the body
def a1():
    o = []
    for _ in [1, 2]:
        try:
            o.append("t")
            break
        except Exception:
            o.append("x")
        else:
            o.append("e")
    return o
def a2():
    o = []
    for i in [1, 2]:
        try:
            o.append("t")
            continue
        except Exception:
            o.append("x")
        else:
            o.append("e")
    return o
def a3():
    o = []
    def f():
        try:
            o.append("t")
            return "r"
        except Exception:
            o.append("x")
        else:
            o.append("e")
    o.append(f())
    return o
def a4():
    o = []
    try:
        o.append("t")
    except Exception:
        o.append("x")
    else:
        o.append("e")
    return o
def a5():
    o = []
    try:
        o.append("t")
        raise ValueError("v")
    except ValueError:
        o.append("x")
    else:
        o.append("e")
    return o
# else that itself breaks
def a6():
    o = []
    for _ in [1, 2]:
        try:
            o.append("t")
        except Exception:
            o.append("x")
        else:
            o.append("e")
            break
    return o
# finally on every exit path
def a7():
    o = []
    for _ in [1]:
        try:
            break
        finally:
            o.append("f")
    return o
def a8():
    o = []
    def f():
        try:
            return "r"
        finally:
            o.append("f")
    o.append(f())
    return o
def a9():
    o = []
    try:
        try:
            raise ValueError("v")
        finally:
            o.append("f")
    except ValueError:
        o.append("c")
    return o
# finally overriding a return
def a10():
    def f():
        try:
            return "a"
        finally:
            return "b"
    return f()
# loop-else (a different construct entirely)
def a11():
    o = []
    for i in [1, 2]:
        o.append(i)
    else:
        o.append("loop-else")
    return o
def a12():
    o = []
    for i in [1, 2]:
        o.append(i)
        break
    else:
        o.append("loop-else")
    return o
def a13():
    o = []
    n = 0
    while n < 2:
        n += 1
    else:
        o.append("while-else")
    return o
def a14():
    o = []
    n = 0
    while n < 2:
        n += 1
        break
    else:
        o.append("while-else")
    return o
# nested: else inside a handler, break out of the outer loop
def a15():
    o = []
    for _ in [1, 2]:
        try:
            raise KeyError("k")
        except KeyError:
            try:
                o.append("in")
                break
            except Exception:
                o.append("x")
            else:
                o.append("e")
        else:
            o.append("outer-e")
    return o
# bare raise re-raises
def a16():
    o = []
    try:
        try:
            raise ValueError("v")
        except ValueError:
            o.append("first")
            raise
    except ValueError as e:
        o.append("second:" + str(e))
    return o
# exception in the else clause is not caught by the same handler
def a17():
    o = []
    try:
        try:
            o.append("t")
        except ValueError:
            o.append("x")
        else:
            raise ValueError("from-else")
    except ValueError as e:
        o.append("outer:" + str(e))
    return o
# finally with continue
def a18():
    o = []
    for i in [1, 2]:
        try:
            continue
        finally:
            o.append("f%d" % i)
    return o
for i, f in enumerate([a1, a2, a3, a4, a5, a6, a7, a8, a9, a10,
                       a11, a12, a13, a14, a15, a16, a17, a18]):
    t("a%d" % (i + 1), f)
print(len(log))
print("|".join(log))
"""


@needs_engine
def test_the_control_flow_grid_agrees_with_cpython() -> None:
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
    bad = [(x, y) for x, y in zip(a, b) if x != y]
    assert not bad, "%d of %s control-flow cases disagree with CPython: %s" % (
        len(bad),
        mine[0],
        ["lypning=%s cpython=%s" % (x, y) for x, y in bad[:6]],
    )
