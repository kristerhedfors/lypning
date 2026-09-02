"""`SystemExit`, as a grid: what an uncaught one does, and which clauses catch it.

`raise SystemExit(4)` used to be an ordinary exception in the Rust core — a
`Traceback` on stderr and exit 1 — where CPython exits 4 in silence. And
`sys.exit(n)` had the opposite defect: it was a private exit signal that no
`except` clause could see, so

    try:
        sys.exit(1)
    except SystemExit:
        cleanup()

never ran its handler. Both are silent disagreements at the process boundary,
which is exactly the kind a caller does not notice.

Now they are one exception, built the same way by both, caught by the same
clauses as in CPython (`BaseException` and a bare `except`, never `Exception`),
run through `finally`, and read back at the end of the run for the exit
status. What the runtime cannot carry — a code whose `str()` reads as a
different code, a non-int non-str code, several arguments — it refuses.

Every case here is a whole program, because the thing under test is the exit
status and the stderr of the process, which a single grid program cannot
observe from the inside. Each row is either byte-identical to CPython on stdout
and exit code, or a clean exit-90 refusal.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from lypning import engines

needs_engine = pytest.mark.skipif(
    engines.find_lypning() is None, reason="the Rust core is not built"
)

PROGRAMS = [
    # uncaught: the code decides the status
    "raise SystemExit(4)",
    "raise SystemExit",
    "raise SystemExit()",
    "raise SystemExit(None)",
    "raise SystemExit(0)",
    "raise SystemExit(True)",
    "raise SystemExit(False)",
    "raise SystemExit(256)",
    "raise SystemExit(-1)",
    'raise SystemExit("boom")',
    'raise SystemExit(f"code {1+1}")',
    'print("before")\nraise SystemExit(3)',
    'import sys\nprint("x", file=sys.stderr)\nraise SystemExit("msg")',
    "import sys\nsys.exit(3)",
    "import sys\nsys.exit()",
    "import sys\nsys.exit(None)",
    "import sys\nsys.exit(False)",
    'import sys\nsys.exit("bye")',
    "import sys\nfor i in range(5):\n    if i == 2:\n        sys.exit(i)\n    print(i)",
    'def f():\n    print("in f")\n    raise SystemExit(6)\nf()\nprint("never")',
    'with open("t.txt", "w") as fh:\n    fh.write("hi")\n    raise SystemExit(8)',
    # finally runs, and the status survives it
    'try:\n    raise SystemExit(2)\nfinally:\n    print("fin")',
    'def f():\n    try:\n        raise SystemExit(6)\n    finally:\n        print("cleanup")\nf()',
    'import sys\ntry:\n    sys.exit("m")\nfinally:\n    print("fin")',
    # which clauses catch it
    'try:\n    raise SystemExit(2)\nexcept SystemExit as e:\n    print("caught", e.code)',
    'try:\n    raise SystemExit(7)\nexcept BaseException as e:\n    print("base", e)',
    'try:\n    raise SystemExit(7)\nexcept Exception as e:\n    print("exc", e)',
    'try:\n    raise SystemExit(7)\nexcept:\n    print("bare")',
    'try:\n    raise SystemExit(7)\nexcept ValueError:\n    print("no")',
    'try:\n    raise SystemExit(3)\nexcept (ValueError, SystemExit) as e:\n    print("tuple", e.code)',
    'import sys\ntry:\n    sys.exit(3)\nexcept SystemExit as e:\n    print("caught", e.code)',
    'import sys\ntry:\n    sys.exit(3)\nexcept Exception:\n    print("no")',
    'import sys\ntry:\n    sys.exit("x")\nexcept:\n    print("bare")\nprint("after")',
    'try:\n    try:\n        raise SystemExit(3)\n    except ValueError:\n        print("inner")\n'
    'except SystemExit as e:\n    print("outer", e.code)',
    # re-raised, and raised from inside a handler
    'try:\n    raise SystemExit(5)\nexcept SystemExit:\n    print("re")\n    raise',
    "try:\n    raise SystemExit(5)\nexcept SystemExit as e:\n    raise e",
    'import sys\ntry:\n    sys.exit(2)\nexcept BaseException:\n    print("base")\n    raise',
    "try:\n    raise SystemExit(3)\nexcept SystemExit as e:\n    raise SystemExit(e.code + 10)",
    'try:\n    1/0\nexcept ZeroDivisionError:\n    raise SystemExit("chained")',
    "try:\n    1/0\nexcept ZeroDivisionError:\n    raise SystemExit(9)",
    # the instance
    "e = SystemExit(3)\nprint(e, repr(e), e.code, e.args)",
    'try:\n    raise SystemExit()\nexcept SystemExit as e:\n    print("caught", e.code, e.args)',
    'try:\n    raise SystemExit("s")\nexcept SystemExit as e:\n    print("caught", e.code, repr(e))',
    "import sys\ntry:\n    sys.exit(True)\nexcept SystemExit as e:\n    print(e.code, e)",
    'print(repr(SystemExit()), repr(SystemExit(None)), repr(SystemExit(True)), repr(SystemExit("x")))',
    "print(isinstance(SystemExit(1), Exception), isinstance(SystemExit(1), BaseException))",
    'x = SystemExit("keep")\nprint("no raise")',
    "raise SystemExit(code=4)",
    # refused: a code the flat (kind, message) pair cannot carry exactly
    'raise SystemExit("4")',
    'raise SystemExit("")',
    'raise SystemExit("None")',
    'raise SystemExit("True")',
    "raise SystemExit(1, 2)",
    "raise SystemExit(4.5)",
    "raise SystemExit([1])",
    "import sys\nsys.exit(2.5)",
    'import sys\nsys.exit("1")',
]


@needs_engine
@pytest.mark.parametrize("program", PROGRAMS)
def test_system_exit_agrees_with_cpython_or_refuses(program: str, tmp_path) -> None:
    ours, theirs = tmp_path / "lypning", tmp_path / "cpython"
    ours.mkdir()
    theirs.mkdir()
    ref = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True,
        cwd=str(theirs), timeout=60,
    )
    got = engines.run(engines.LYPNING, program, cwd=ours, timeout=60)
    if got.returncode == 90:
        lines = got.stderr.splitlines()
        assert got.stdout == "" and len(lines) == 1, "an unclean refusal: %r" % got.stderr
        assert lines[0].startswith("lypning: unsupported: "), got.stderr
        return
    assert (got.stdout, got.returncode) == (ref.stdout, ref.returncode), (
        "lypning exit %d, stderr %r; CPython exit %d, stderr %r"
        % (got.returncode, got.stderr[-300:], ref.returncode, ref.stderr[-300:])
    )
    if not ref.stderr.startswith("Traceback"):
        # No traceback for a SystemExit, ever: stderr is the program's own
        # writes plus, for a non-integer code, that code and a newline — and
        # those must match to the byte. (A traceback's frame lines are
        # CPython's own shape, and are not compared anywhere.)
        assert got.stderr == ref.stderr
