"""The warm CPython backstop: it must be indistinguishable from a cold spawn.

The pool exists to make the chain's last tier cheap, and the only way that is
allowed to be worth anything is if a caller cannot tell the difference. So the
tests that matter here are not "does it run a program" but "does it produce
byte-for-byte what `python3 -c` produces, including the parts nobody looks at
until they are wrong": the traceback text, a non-integer `SystemExit`, the
exit status of a signalled child, and the isolation between one program and the
next.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time

import pytest

from lypning import pool

pytestmark = pytest.mark.skipif(not hasattr(os, "fork"),
                                reason="the pool forks; there is no Windows story")

DEVNULL = subprocess.DEVNULL


@pytest.fixture()
def served(tmp_path):
    """A running pool, torn down with the test."""
    server = pool.Server(tmp_path / "t.sock")
    server.warm()
    server.open()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for _ in range(200):                          # the socket exists; wait for accept
        try:
            pool.Client(server.path).ping()
            break
        except pool.PoolError:
            time.sleep(0.01)
    yield pool.Client(server.path)
    try:
        pool.Client(server.path).shutdown()
    except pool.PoolError:
        pass
    server.close()


def cpython(program, cwd, stdin=None):
    return subprocess.run([sys.executable, "-c", program], capture_output=True, text=True,
                          cwd=str(cwd), input=stdin,
                          stdin=None if stdin is not None else DEVNULL)


# --- the contract: identical to a cold spawn ---------------------------------

IDENTICAL = [
    "print(1 + 1)",
    "import sys; sys.exit(7)",
    "import sys; sys.exit(0)",
    "import sys; sys.exit('a message')",          # non-int SystemExit goes to stderr
    "raise ValueError('boom')",                   # traceback must not name pool.py
    "def f():\n    1/0\nf()",                     # multi-frame traceback
    "assert False, 'nope'",
    "{}['missing']",
    "import json; print(json.dumps({'a': 1}))",
    "print('unicode: \\u00e9\\u00fc')",
    "import sys; print('to stderr', file=sys.stderr)",
    "print('no newline', end='')",
]


@pytest.mark.parametrize("program", IDENTICAL)
def test_matches_cpython_byte_for_byte(served, tmp_path, program):
    cwd = tmp_path / "run"
    cwd.mkdir()
    ref = cpython(program, cwd)
    got = served.run(program, cwd=cwd)
    assert got["returncode"] == ref.returncode, program
    assert got["stdout"] == ref.stdout, program
    assert got["stderr"] == ref.stderr, program


def test_traceback_does_not_name_the_pool(served, tmp_path):
    """The frame that calls exec() is ours; CPython's -c has no such frame."""
    got = served.run("raise ValueError('boom')", cwd=tmp_path)
    assert "pool.py" not in got["stderr"]
    assert got["stderr"].startswith("Traceback (most recent call last):")
    assert '"<string>", line 1' in got["stderr"]


def test_stdin_is_delivered(served, tmp_path):
    program = "import sys; print(sys.stdin.read().strip().upper())"
    got = served.run(program, cwd=tmp_path, stdin="hello")
    assert got["stdout"] == "HELLO\n"


def test_stdin_absent_is_eof_not_a_hang(served, tmp_path):
    got = served.run("import sys; print(repr(sys.stdin.read()))", cwd=tmp_path)
    assert got["stdout"] == "''\n"


def test_argv_tail_reaches_the_program(served, tmp_path):
    got = served.run("import sys; print(sys.argv[1:])", cwd=tmp_path,
                     argv_tail=["a", "b"])
    assert got["stdout"] == "['a', 'b']\n"


# --- isolation ---------------------------------------------------------------


def test_cwd_is_the_callers(served, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    assert served.run("import os; print(os.getcwd())", cwd=a)["stdout"].strip() == str(a)
    assert served.run("import os; print(os.getcwd())", cwd=b)["stdout"].strip() == str(b)


def test_state_does_not_leak_between_programs(served, tmp_path):
    """The reason this forks instead of reusing one namespace."""
    served.run("import builtins; builtins.LEAKED = 1", cwd=tmp_path)
    got = served.run("import builtins; print(hasattr(builtins, 'LEAKED'))", cwd=tmp_path)
    assert got["stdout"] == "False\n"

    served.run("import sys; sys.setrecursionlimit(120)", cwd=tmp_path)
    got = served.run("import sys; print(sys.getrecursionlimit() == 120)", cwd=tmp_path)
    assert got["stdout"] == "False\n"


def test_a_program_writing_files_does_not_see_anothers(served, tmp_path):
    a, b = tmp_path / "wa", tmp_path / "wb"
    a.mkdir(); b.mkdir()
    served.run("open('x.txt', 'w').write('hi')", cwd=a)
    got = served.run("import os; print(os.path.exists('x.txt'))", cwd=b)
    assert got["stdout"] == "False\n"


# --- the pool survives what the program does ---------------------------------


def test_hard_exit_costs_a_child_not_the_pool(served, tmp_path):
    got = served.run("import os; os._exit(3)", cwd=tmp_path)
    assert got["returncode"] == 3
    assert served.ping()["ok"] is True


def test_signalled_child_reports_negative_status(served, tmp_path):
    got = served.run("import os, signal; os.kill(os.getpid(), signal.SIGKILL)", cwd=tmp_path)
    assert got["returncode"] < 0
    assert served.ping()["ok"] is True


def test_pool_keeps_serving_after_a_failure(served, tmp_path):
    served.run("raise SystemError('x')", cwd=tmp_path)
    got = served.run("print('still here')", cwd=tmp_path)
    assert got["stdout"] == "still here\n"


def test_large_output_survives_the_pipe(served, tmp_path):
    """Bigger than a pipe buffer: the parent must drain while the child writes."""
    got = served.run("print('x' * 400000)", cwd=tmp_path)
    assert len(got["stdout"]) == 400001


# --- degradation --------------------------------------------------------------


def test_a_missing_pool_raises_poolerror_not_something_exotic(tmp_path):
    with pytest.raises(pool.PoolError):
        pool.Client(tmp_path / "absent.sock").ping()


def test_socket_is_not_world_readable(tmp_path):
    server = pool.Server(tmp_path / "perm.sock")
    server.open()
    try:
        assert (server.path.stat().st_mode & 0o077) == 0
    finally:
        server.close()


def test_shutdown_of_an_absent_pool_is_quiet(tmp_path):
    pool.Client(tmp_path / "gone.sock").shutdown()          # must not raise
