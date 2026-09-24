"""`time`, as a grid: every program on the binary and on CPython.

`tests/test_base64_grid.py` is the shape this follows. Every row runs from a
FRESH temp cwd (invariant 4) and must end one of exactly two ways:

  * the same stdout AND the same exit code as CPython, or
  * a clean refusal — exit 90, nothing on stdout, one
    ``lypning-l: unsupported: <kind>: <detail>`` line on stderr (invariant 2).

**Why the rows look the way they do.** A clock is the one thing two runs never
agree on, and `conformance.is_run_specific` knows it: a corpus program that
prints `time.time()` is graded on its exit code and exception type alone. So
the battery barely sees a wrong clock, and this grid is where the capability's
correctness actually lives. Every served row prints a DETERMINISTIC predicate
of the clock — a type, an order, a length, a separator — never the value.
`test_the_utc_stamp_is_the_clock` is the one row that checks the VALUE, against
the reference interpreter's own `gmtime()` read a moment apart.

**The policy this file pins** (`time.rs` states it; `route.rs` decides it):

  * everything local-time (`localtime`, `ctime`, `asctime`, `mktime`,
    one-argument `strftime`, `timezone`, `tzname`) refuses STATICALLY;
  * `gmtime` and `strftime` are served in ONE shape only —
    ``time.strftime(<str literal>, time.gmtime())`` with directives from
    ``%Y %m %d %H %M %S %%`` — because there is no `struct_time` value here;
  * a served function used as anything but a callee (``f = time.time``,
    ``print(time.time)``) and the module itself as a value refuse;
  * `time.sleep` is served only where it can cost at most ONE extra second if
    the run refuses later and CPython runs the program again: its argument is
    a literal of at most one second, and the call is not inside a loop, a
    `def`, a `lambda` or a comprehension. A refusal re-runs the whole program
    on CPython, so a served sleep is a sleep the program may pay twice.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from lypning import engines, paths

T = "import time\n"

#: Programs CPython answers and this engine must answer too, byte for byte.
SERVED = [
    T + "t = time.time()\nprint(type(t).__name__, t > 1.6e9)",
    T + "print(type(time.time_ns()).__name__, type(time.perf_counter_ns()).__name__, "
        "type(time.monotonic()).__name__, type(time.monotonic_ns()).__name__, "
        "type(time.perf_counter()).__name__)",
    T + "print(type(time.time_ns()) is int, time.time_ns() // 10**9 > 1600000000)",
    T + "t = time.time(); ns = time.time_ns()\nprint(abs(ns / 1e9 - t) < 1.0)",
    T + "a = time.perf_counter(); time.sleep(0.05); b = time.perf_counter()\n"
        "print(b - a >= 0.05, b - a < 5)",
    T + "a = time.monotonic(); b = time.monotonic()\nprint(b >= a)",
    T + "a = time.monotonic_ns(); b = time.monotonic_ns()\nprint(b >= a, a > 0)",
    T + "a = time.perf_counter_ns(); b = time.perf_counter_ns()\nprint(b >= a)",
    T + "print(abs(time.perf_counter() - time.monotonic()) < 1.0)",
    T + "print(abs(time.perf_counter_ns() / 1e9 - time.perf_counter()) < 1.0)",
    T + "print(time.sleep(0))",
    T + "time.sleep(True); print('ok')",
    T + "time.sleep(False); print('ok')",
    T + "time.sleep(0.0); time.sleep(-0.0); print('ok')",
    T + "time.sleep(1e-3); print('ok')",
    T + "if True:\n    time.sleep(0.01)\nprint('done')",
    T + "try:\n    time.sleep(0.01)\nfinally:\n    print('finally')",
    "import time as t\nprint(round(t.time()) > 0)",
    "from time import sleep, time\nsleep(0)\nprint(time() > 0)",
    "from time import perf_counter as pc\na = pc()\nprint(pc() - a >= 0)",
    "from time import time_ns, monotonic\nprint(time_ns() > 0, monotonic() > 0)",
    "import time, json\nprint(json.dumps(time.time() > 0))",
    T + "x = time.time()\nprint(isinstance(x, float), isinstance(time.time_ns(), int))",
    T + "print(int(time.time()) > 0, str(time.time_ns()).isdigit())",
    T + "d = time.perf_counter() - time.perf_counter()\nprint(d <= 0)",
    T + "print(f'{time.time() - time.time():.0f}' in ('0', '-0'))",
    T + "print('%.1f' % (time.monotonic() * 0))",
    T + "print(time.time_ns() % 1 == 0, time.time() == time.time() or True)",
    T + "start = time.time()\ntotal = sum(range(1000))\n"
        "print(total, time.time() - start >= 0)",
    # The fused UTC stamp.
    T + "print(len(time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())))",
    T + "s = time.strftime('%Y-%m-%d', time.gmtime())\nprint(len(s), s[4], s[7])",
    T + "s = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())\n"
        "print(s[4], s[7], s[10], s[13], s[16], s[19], s[:2])",
    T + "print(time.strftime('%%Y', time.gmtime()))",
    T + "print(time.strftime('abc %% %Y', time.gmtime())[:6])",
    T + "print(time.strftime('', time.gmtime()) == '')",
    T + "print(time.strftime('no directives', time.gmtime()))",
    T + "s = time.strftime('%H%M%S', time.gmtime())\n"
        "print(len(s), s.isdigit(), int(s[:2]) < 24, int(s[2:4]) < 60, int(s[4:]) < 62)",
    T + "s = time.strftime('%m %d', time.gmtime())\n"
        "m, d = s.split()\nprint(1 <= int(m) <= 12, 1 <= int(d) <= 31, len(m), len(d))",
    "import time as tm\nprint(len(tm.strftime('%Y', tm.gmtime())))",
    # The stamp, checked against the clock INSIDE the program: the civil date
    # of `time.time()` by integer arithmetic this engine already serves, read
    # on both sides of the stamp so a second boundary cannot flake it.
    T + "def civil(t):\n"
        "    z = t // 86400 + 719468\n"
        "    era = z // 146097\n"
        "    doe = z - era * 146097\n"
        "    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365\n"
        "    y = yoe + era * 400\n"
        "    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)\n"
        "    mp = (5 * doy + 2) // 153\n"
        "    d = doy - (153 * mp + 2) // 5 + 1\n"
        "    m = mp + 3 if mp < 10 else mp - 9\n"
        "    s = t % 86400\n"
        "    return '%04d-%02d-%02d %02d:%02d:%02d' % (y + (m <= 2), m, d, s // 3600, s // 60 % 60, s % 60)\n"
        "a = int(time.time())\n"
        "s = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())\n"
        "b = int(time.time())\n"
        "print(s in (civil(a), civil(b)))",
    # The fused stamp inside a `def` written ABOVE the import: the walk learns
    # `time` before it judges any call (`route::time_prescan`).
    "def f():\n    return time.strftime('%Y', time.gmtime())\nimport time\nprint(len(f()))",
    # A module is hashable by identity (`value.rs`), one key per module.
    T + "import os\nprint(len({os, os}), os in {os: 1})",
    # The errors a literal argument raises, which the served shape keeps.
    T + "time.sleep(-1)",
    T + "try:\n    time.sleep(-0.5)\nexcept ValueError as e:\n    print('ValueError', e)",
    T + "try:\n    time.sleep(-1)\nexcept ValueError as e:\n    print(e)",
    T + "try:\n    time.sleep(-1e-10)\nexcept ValueError as e:\n    print(e)",
    T + "try:\n    time.sleep(None)\nexcept TypeError as e:\n    print(e)",
    T + "try:\n    time.sleep('1')\nexcept TypeError as e:\n    print(e)",
    T + "try:\n    time.sleep(b'1')\nexcept TypeError as e:\n    print(e)",
    T + "time.sleep(None)",
    T + "time.sleep('1')",
    T + "print('before')\ntime.sleep(-2)\nprint('after')",
    # A program that dies before the clock is ever read is still the same
    # program on both engines.
    T + "t0 = time.time()\ndata = open('missing.json').read()\nprint(time.time() - t0)",
]

#: Programs this engine must REFUSE, cleanly. CPython answers most of them; the
#: rest raise a message that is not this engine's to write.
REFUSED = [
    # a served name that is not called
    T + "print(time.time)",
    T + "f = time.time\nprint(f() > 0)",
    T + "print(type(time.time).__name__)",
    T + "print(callable(time.sleep))",
    T + "fs = [time.time, time.monotonic]\nprint(len(fs))",
    "import time as t\nprint(t.perf_counter)",
    "from time import sleep\ns = sleep\ns(0)",
    "from time import time\nprint(time)",
    # the module as a value
    T + "print(time)",
    T + "print(getattr(time, 'time')() > 0)",
    T + "m = time\nprint(m.time() > 0)",
    # everything local-time, and the rest of the module
    T + "print(time.localtime().tm_year > 2000)",
    T + "print(len(time.ctime()))",
    T + "print(len(time.asctime()))",
    T + "print(time.mktime(time.gmtime()) > 0)",
    T + "print(time.timezone)",
    T + "print(time.tzname)",
    T + "print(time.altzone, time.daylight)",
    T + "print(time.process_time() >= 0)",
    T + "print(time.thread_time() >= 0)",
    T + "print(time.get_clock_info('time').adjustable)",
    T + "print(time.struct_time)",
    T + "print(time.strptime('2020', '%Y').tm_year)",
    T + "print(time.CLOCK_MONOTONIC)",
    "from time import localtime\nprint(localtime().tm_year > 2000)",
    "from time import *\nprint(time() > 0)",
    # strftime and gmtime outside the one fused shape
    T + "print(len(time.strftime('%Y')))",
    T + "print(time.strftime('%Y-%m-%d', time.gmtime(0)))",
    T + "print(time.strftime('%Y', time.localtime()))",
    T + "print(len(time.strftime('%a %b', time.gmtime())))",
    T + "print(len(time.strftime('%y%j', time.gmtime())))",
    T + "print(len(time.strftime('%Z', time.gmtime())))",
    T + "print(len(time.strftime('%', time.gmtime())))",
    T + "print(len(time.strftime('\u00e9%Y', time.gmtime())))",
    T + "f = '%Y'\nprint(len(time.strftime(f, time.gmtime())))",
    T + "g = time.gmtime()\nprint(len(time.strftime('%Y', g)))",
    T + "print(time.gmtime().tm_year > 2000)",
    T + "print(len(time.gmtime()))",
    T + "print(len(time.strftime(format='%Y', t=time.gmtime())))",
    T + "print(len(time.strftime('%Y', time.gmtime(), 1)))",
    T + "print(len(time.strftime(*['%Y'], time.gmtime())))",
    "from time import strftime, gmtime\nprint(len(strftime('%Y', gmtime())))",
    "from time import gmtime\nprint(gmtime().tm_year > 2000)",
    # arity the walk can count
    T + "print(time.time(1))",
    T + "print(time.monotonic(x=1))",
    T + "print(time.perf_counter(*[]))",
    T + "time.sleep()",
    T + "time.sleep(0, 0)",
    T + "time.sleep(secs=0)",
    T + "time.sleep(*[0])",
    # a sleep that could be paid twice, or for longer than a second
    T + "for i in range(2):\n    time.sleep(0)\nprint(i)",
    T + "i = 0\nwhile i < 2:\n    time.sleep(0)\n    i += 1\nprint(i)",
    T + "def f():\n    time.sleep(0)\nf()\nprint('ok')",
    T + "print([time.sleep(0) for _ in range(2)])",
    T + "(lambda: time.sleep(0))()\nprint('ok')",
    T + "time.sleep(1.5)\nprint('ok')",
    T + "time.sleep(2)\nprint('ok')",
    T + "x = 0.01\ntime.sleep(x)\nprint('ok')",
    T + "time.sleep(0.01 * 2)\nprint('ok')",
    T + "time.sleep(float('nan'))",
    T + "time.sleep(10**30)",
    "from time import sleep\nfor _ in range(3):\n    sleep(0)\nprint('ok')",
    "from time import sleep as z\nwhile False:\n    z(0)\nprint('ok')",
    # Text order is not run order: every `time.X` below is reached with the
    # module bound, though the walk reads the import after it. Each printed a
    # bare 9-tuple where CPython prints a `struct_time`, or slept in a loop.
    "def f():\n    return time.gmtime()\nimport time\nprint(f())",
    "def f():\n    return time.gmtime()\nimport time\nprint(type(f()).__name__)",
    "def f():\n    return time.gmtime()\nimport time\nprint(f().tm_year > 2000)",
    "f = lambda: time.gmtime()\nimport time\nprint(f())",
    "def g():\n    return time.gmtime()\ndef f():\n    global time\n    import time\nf()\nprint(g())",
    "i = 0\nwhile i < 2:\n    if i == 1:\n        print(time.gmtime())\n    else:\n        import time\n    i += 1",
    "def f():\n    return time\nimport time\nm = f()\nprint(m.gmtime())",
    "def f():\n    return time\nimport time\nh = f().gmtime\nprint(h())",
    "def f():\n    return time\nimport time\nx = [f()]\nprint(x[0].gmtime())",
    "def f():\n    return time\nimport time\nprint(list(map(lambda m: m.gmtime(), [f()])))",
    "def f():\n    return time\nimport time\nprint(len({f()}))",
    "def f():\n    for _ in range(3):\n        time.sleep(0.2)\n    return 'slept'\nimport time\nprint(f())",
    "def f():\n    time.sleep(5)\nimport time\nf()\nprint('ok')",
    "if False:\n    from time import sleep\nfor _ in range(2):\n    sleep(0)\nprint('ok')",
]

BARRIER = "import os\nos.mkdir('NEWD')\n"

#: Every static refusal SHAPE, after a committed side effect: exit 90, and the
#: directory the program made first is NOT on disk, because nothing ran.
AFTER_A_BARRIER = [
    ("module-attr", T + BARRIER + "print(time.localtime())"),
    ("module-attr", "from time import localtime\n" + BARRIER + "print(localtime())"),
    ("time", T + BARRIER + "print(time.time)"),
    ("time", T + BARRIER + "print(time)"),
    ("time", T + BARRIER + "print(time.strftime('%Y'))"),
    ("time", T + BARRIER + "print(time.strftime('%a', time.gmtime()))"),
    ("time", T + BARRIER + "print(time.gmtime())"),
    ("time", T + BARRIER + "time.sleep(5)"),
    ("time", T + BARRIER + "for _ in range(9):\n    time.sleep(0.1)"),
    ("time", T + BARRIER + "print(time.time(0))"),
    ("time", "from time import strftime\n" + BARRIER + "print(strftime('%Y'))"),
]

#: Routing, asked of the CORE — the binary that routes. It has no `time`
#: bytes, only the table rows every variant carries.
ROUTED_TO_LYPNING_L = [
    T + "print(time.time() > 0)",
    T + "time.sleep(0)\nprint(time.perf_counter() >= 0)",
    T + "print(len(time.strftime('%Y', time.gmtime())))",
    "from time import perf_counter\nprint(perf_counter() >= 0)",
]
ROUTED_TO_CPYTHON = [
    T + "print(time.localtime())",
    T + "print(time.ctime())",
    "from time import localtime\nprint(localtime())",
    T + "print(time.tzname)",
]


def _spectrum(binary: Path) -> dict | None:
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
    """A built ``engine`` that carries THIS tree's capability table, or None —
    an installed binary from before `cap-time` would refuse every row and turn
    the file into green skips."""
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


BINARY = _current(engines.LYPNING_L, "cap-time")
CORE = _current(engines.LYPNING, "cap-time")

needs_l = pytest.mark.skipif(
    BINARY is None,
    reason="no lypning-l carrying cap-time is built (lypning build --rust)",
)
needs_core = pytest.mark.skipif(
    CORE is None or BINARY is None,
    reason="routing needs the CORE that routes and the lypning-l it routes to",
)


def _run(argv: list[str], program: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run(argv + ["-c", program], capture_output=True, text=True,
                              cwd=d, timeout=60)


def _refusal_problem(got: subprocess.CompletedProcess) -> str | None:
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
@pytest.mark.parametrize("program", SERVED, ids=range(len(SERVED)))
def test_the_time_grid_agrees_with_cpython(program: str) -> None:
    """Served, and the same answer: a refusal here is a FAILURE, not a skip,
    because every row is a shape the capability claims."""
    got = _run([str(BINARY)], program)
    assert got.returncode != engines.UNSUPPORTED_EXIT, (
        "a served row refused: %s\n  program: %r" % (got.stderr.strip()[:200], program))
    ref = _run([sys.executable], program)
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "lypning-l disagrees with CPython.\n"
        "  program:  %r\n"
        "  lypning-l: %r exit %d %s\n"
        "  cpython:   %r exit %d %s"
        % (program, got.stdout, got.returncode, got.stderr.strip()[-200:],
           ref.stdout, ref.returncode, ref.stderr.strip()[-200:])
    )
    if got.returncode != 0:
        # The same exception TYPE, which is what conformance grades an error on.
        last = lambda s: (s.strip().splitlines() or [""])[-1].split(":")[0]
        assert last(got.stderr) == last(ref.stderr), (got.stderr, ref.stderr)


@needs_l
@pytest.mark.parametrize("program", REFUSED, ids=range(len(REFUSED)))
def test_the_surface_outside_the_subset_refuses_rather_than_guesses(program: str) -> None:
    got = _run([str(BINARY)], program)
    problem = _refusal_problem(got)
    assert problem is None, (
        "this program must refuse, not answer: %s\n  program: %r\n  stdout: %r\n  stderr: %r"
        % (problem, program, got.stdout[:200], got.stderr.strip()[:200])
    )


@needs_l
@pytest.mark.parametrize("kind,program", AFTER_A_BARRIER, ids=range(len(AFTER_A_BARRIER)))
def test_every_static_refusal_lands_before_the_first_statement(kind: str, program: str) -> None:
    with tempfile.TemporaryDirectory() as d:
        got = subprocess.run([str(BINARY), "-c", program], capture_output=True,
                             text=True, cwd=d, timeout=60)
        after = sorted(os.listdir(d))
    problem = _refusal_problem(got)
    assert problem is None, "%s\n  program: %r" % (problem, program)
    assert ": %s: " % kind in got.stderr, (kind, got.stderr)
    assert after == [], "the refusal landed after the program ran: %r" % after


@needs_core
@pytest.mark.parametrize("program", ROUTED_TO_LYPNING_L, ids=range(len(ROUTED_TO_LYPNING_L)))
def test_the_core_routes_the_served_surface_to_lypning_l(program: str) -> None:
    route = engines.route(program, binary=CORE)
    assert route.engine == engines.LYPNING_L, (route.engine, route.kind, route.detail)


@needs_core
@pytest.mark.parametrize("program", ROUTED_TO_CPYTHON, ids=range(len(ROUTED_TO_CPYTHON)))
def test_the_core_routes_local_time_straight_to_cpython(program: str) -> None:
    """Out of `route::MODULE_ATTRS`, in the core's own walk: the program never
    enters lypning-l to be refused there."""
    route = engines.route(program, binary=CORE)
    assert route.engine == engines.CPYTHON, (route.engine, route.kind, route.detail)


@needs_l
def test_the_utc_stamp_is_the_clock() -> None:
    """The VALUE, once: lypning-l's stamp lies between two reads of the
    reference interpreter's own `gmtime()`."""
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    before = time.strftime(fmt, time.gmtime())
    got = _run([str(BINARY)], T + "print(time.strftime(%r, time.gmtime()))" % fmt)
    after = time.strftime(fmt, time.gmtime())
    assert got.returncode == 0, got.stderr
    assert before <= got.stdout.strip() <= after, (before, got.stdout, after)


@needs_l
def test_time_is_the_wall_clock_and_monotonic_is_the_reference_clock() -> None:
    """`time.time()` is CLOCK_REALTIME and `time.monotonic()` is the clock
    CPython reads for it on this platform — so both land between two reads of
    the reference's own."""
    prog = T + "print(repr(time.time()), repr(time.monotonic()), time.time_ns(), time.monotonic_ns())"
    t0, m0 = time.time(), time.monotonic()
    got = _run([str(BINARY)], prog)
    t1, m1 = time.time(), time.monotonic()
    assert got.returncode == 0, got.stderr
    t, m, tns, mns = got.stdout.split()
    assert t0 <= float(t) <= t1
    assert m0 <= float(m) <= m1
    assert t0 <= int(tns) / 1e9 <= t1 + 1e-6
    assert m0 <= int(mns) / 1e9 <= m1 + 1e-6


@needs_l
def test_a_served_sleep_sleeps() -> None:
    start = time.monotonic()
    got = _run([str(BINARY)], T + "a = time.monotonic()\ntime.sleep(0.2)\nprint(time.monotonic() - a >= 0.2)")
    assert got.returncode == 0 and got.stdout == "True\n", got
    assert time.monotonic() - start >= 0.2


@needs_l
@pytest.mark.parametrize("arg,exc", [("-1", "ValueError"), ("-0.001", "ValueError"),
                                     ("None", "TypeError"), ("'x'", "TypeError"),
                                     ("[]", "TypeError")])
def test_the_sleep_errors_are_cpythons_types_and_cost_no_sleep(arg: str, exc: str) -> None:
    prog = T + "try:\n    time.sleep(%s)\nexcept Exception as e:\n    print(type(e).__name__, e)" % arg
    got = _run([str(BINARY)], prog)
    if arg == "[]":
        # a list display is not a literal the sleep policy reads
        assert _refusal_problem(got) is None
        return
    ref = _run([sys.executable], prog)
    assert got.stdout.split()[0] == exc
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode)


def test_the_capability_is_on_the_larger_variant_only() -> None:
    assert "cap-time" in engines.VARIANT_CAPS[engines.LYPNING_L]
    assert "cap-time" not in engines.VARIANT_CAPS[engines.LYPNING]


@needs_l
def test_no_served_row_quietly_became_a_refusal() -> None:
    refused = [p for p in SERVED
               if _run([str(BINARY)], p).returncode == engines.UNSUPPORTED_EXIT]
    assert refused == [], refused[:4]


#: Programs that end in an uncaught `NameError` on the name `time`. CPython 3.14
#: ends that traceback with an import hint whose wording rests on a suggestion
#: search this engine does not run, so lypning-l refuses at the exit path
#: (`err::forgot_import`, kind `name-hint`) and CPython answers. Before the fix,
#: the first row printed `AttributeError: 'ValueError' object has no attribute
#: 'time'`: an `except … as time` never unbound the name.
FORGOT_IMPORT = [
    T + "try:\n    raise ValueError('x')\nexcept ValueError as time:\n    pass\nprint(time.time())",
    "def f():\n    import time\nf()\nprint(time.time())",
    T + "del time\nprint(time.time())",
    "def f():\n    import time\n    return time.time() > 0\nprint(f())\nprint(time.time())",
]

#: The last stderr line CPython 3.14.5 writes for every FORGOT_IMPORT row.
FORGOT_IMPORT_LINE = "NameError: name 'time' is not defined. Did you forget to import 'time'?"


@needs_l
@pytest.mark.parametrize("program", FORGOT_IMPORT, ids=range(len(FORGOT_IMPORT)))
def test_a_name_error_on_time_refuses_so_cpython_prints_the_hint(program: str) -> None:
    got = _run([str(BINARY)], program)
    assert _refusal_problem(got) is None, (got.returncode, got.stdout, got.stderr)
    assert got.stderr.startswith("%s: unsupported: name-hint: " % engines.LYPNING_L), got.stderr
    ref = _run([sys.executable], program)
    if sys.version_info[:2] == (3, 14):
        assert ref.stderr.strip().splitlines()[-1] == FORGOT_IMPORT_LINE


@needs_l
@pytest.mark.parametrize("op,line", [
    ("+=", "TypeError: unsupported operand type(s) for +=: 'module' and 'int'"),
    ("-=", "TypeError: unsupported operand type(s) for -=: 'module' and 'int'"),
    ("*=", "TypeError: unsupported operand type(s) for *=: 'module' and 'int'"),
])
def test_an_augmented_assignment_to_the_module_names_the_in_place_operator(op: str, line: str) -> None:
    """The bytes are CPython 3.14.5's. The engine printed `for +:`, dropping the
    `=` CPython writes for an in-place operator."""
    got = _run([str(BINARY)], T + "time %s 1" % op)
    assert got.returncode == 1 and got.stdout == "", got.stderr
    assert got.stderr.strip().splitlines()[-1] == line
