"""The library tier: the refusal contract, isolation between runs, and survival.

Three questions, and they are not the same question:

  * **Does it answer what the binary answers?** The interpreter is shared, so
    the risk is not the language — it is the second implementation of the exit
    path (``embed.rs``) drifting from the first (``main.rs``). Invariant 2 says
    that contract has only ever broken silently, so it is asserted, not assumed.
  * **Does run two see run one?** All the runtime's state is thread_local and
    the binary never had to clear it, because it exited. A library does, and a
    leftover byte or a latched ``COMMITTED`` flag would surface as an answer
    that is wrong only on the second call.
  * **Can it take the host down?** A refusal costs a spawn. A SIGSEGV costs the
    application, and no host can catch one. Every case here that ends in a
    refusal used to end in a crash.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from typing import List

from lypning import embed, engines, paths
from conftest import _INSTALLED_LIBRARY


def _INSTALLED_LIBRARY_OR_SKIP() -> embed.Library:
    if _INSTALLED_LIBRARY is None:
        pytest.skip("the C ABI is not built (`lypning build --lib`)")
    return embed.Library(_INSTALLED_LIBRARY)


# --- the contract ------------------------------------------------------------


def test_refusal_contract_holds(lypning_lib):
    """Exit 90, the exact line, nothing on stdout, and a request to route on."""
    ok, why = embed.check_refusal_contract(lypning_lib.path)
    assert ok, why


def test_refusal_is_not_an_error(lypning_lib):
    out = lypning_lib.run("import subprocess")
    assert out.status == embed.UNSUPPORTED
    assert out.exit_code == 90
    assert out.stdout == b""
    assert out.stderr == embed.REFUSAL_LINE
    assert out.kind == "module" and out.detail == "import subprocess"
    assert out.fall_onward is True
    assert out.committed is False


def test_program_error_is_the_programs_own(lypning_lib):
    """An uncaught exception is not a refusal: re-running it elsewhere would
    repeat whatever it did before raising."""
    out = lypning_lib.run("raise ValueError('boom')")
    assert out.status == embed.ERROR
    assert out.exit_code == 1
    assert b"ValueError: boom" in out.stderr
    assert out.fall_onward is False


def test_sys_exit_is_the_programs_own_code(lypning_lib):
    out = lypning_lib.run("import sys; sys.exit(3)")
    assert out.status == embed.OK
    assert out.exit_code == 3
    assert out.fall_onward is False


def test_abi_and_runtime_versions(lypning_lib, lypning_bin):
    assert lypning_lib.version
    reported = subprocess.run([str(lypning_bin), "--version"], capture_output=True,
                              text=True, check=False).stdout.split()[-1]
    assert lypning_lib.version == reported


# --- the library must answer what the binary answers -------------------------

#: One program per shape the exit path can take: a value, a stream, argv, a
#: refusal, an exception, an explicit exit.
AGREEMENT_PROGRAMS = [
    "print(2**8)",
    "print(sorted({'b': 2, 'a': 1}))",
    "import sys; print(sys.argv)",
    "print('x' * 3, end='')",
    "import subprocess",
    "raise SystemExit(2)",
    "raise KeyError('k')",
    "print(1/0)",
    "import json; print(json.dumps({'a': [1, 2]}))",
]


@pytest.mark.parametrize("program", AGREEMENT_PROGRAMS)
def test_library_agrees_with_the_binary(lypning_lib, lypning_bin, program, tmp_path):
    """The drift detector. Two implementations of one contract, same answer."""
    spawned = subprocess.run([str(lypning_bin), "-c", program], capture_output=True,
                             cwd=str(tmp_path), check=False)
    linked = lypning_lib.run(program)
    assert linked.stdout == spawned.stdout
    assert linked.exit_code == spawned.returncode
    if spawned.returncode == 90:
        # The refusal line is the one piece of stderr that must match to the
        # byte: it is what a dispatcher keys on.
        assert linked.stderr == spawned.stderr


# --- injection ---------------------------------------------------------------


def test_stdin_is_the_hosts_bytes_not_the_processs(lypning_lib):
    out = lypning_lib.run("import sys; print(sys.stdin.read().upper(), end='')",
                          stdin=b"hello\n")
    assert out.stdout == b"HELLO\n"


def test_stdin_defaults_to_empty_never_the_hosts_fd_zero(lypning_lib):
    """A library call that blocked on a terminal nobody wrote to would be
    indistinguishable from a hang."""
    out = lypning_lib.run("import sys; print(repr(sys.stdin.read()))")
    assert out.stdout == b"''\n"


def test_argv_takes_cpythons_dash_c_shape(lypning_lib):
    out = lypning_lib.run("import sys; print(sys.argv)", args=["a", "b c"])
    assert out.stdout == b"['-c', 'a', 'b c']\n"


def test_argv_takes_the_file_shape_when_named(lypning_lib):
    out = lypning_lib.run("import sys; print(sys.argv)", filename="run.py", args=["x"])
    assert out.stdout == b"['run.py', 'x']\n"


# --- isolation between runs --------------------------------------------------


def test_output_does_not_leak_into_the_next_run(lypning_lib):
    assert lypning_lib.run("print('first')").stdout == b"first\n"
    assert lypning_lib.run("print('second')").stdout == b"second\n"


def test_stdin_does_not_leak_into_the_next_run(lypning_lib):
    lypning_lib.run("import sys; sys.stdin.read()", stdin=b"consumed")
    out = lypning_lib.run("import sys; print(repr(sys.stdin.read()))")
    assert out.stdout == b"''\n"


def test_a_committed_run_does_not_poison_the_next_refusal(lypning_lib, tmp_path, monkeypatch):
    """The exact shape of a bug the binary could never have: ``COMMITTED`` is
    set on a large flush and was never cleared, so the SECOND run's refusal
    would arrive as a non-retryable error instead of exit 90."""
    monkeypatch.chdir(tmp_path)
    big = lypning_lib.run("print('x' * (9 << 20))")
    assert big.status == embed.OK
    after = lypning_lib.run("import subprocess")
    assert after.status == embed.UNSUPPORTED
    assert after.exit_code == 90
    assert after.stdout == b""


def test_a_refusal_writes_no_file(lypning_lib, tmp_path, monkeypatch):
    """The commit barrier is what makes falling onward safe: the retry on
    CPython must not find half of the program's work already done."""
    monkeypatch.chdir(tmp_path)
    out = lypning_lib.run("open('out.txt', 'w').write('half')\nimport subprocess")
    assert out.status == embed.UNSUPPORTED
    assert out.committed is False
    assert not (tmp_path / "out.txt").exists()


def test_a_successful_run_does_write_its_file(lypning_lib, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = lypning_lib.run("open('out.txt', 'w').write('done')")
    assert out.status == embed.OK
    assert (tmp_path / "out.txt").read_text() == "done"


# --- the policies ------------------------------------------------------------


def test_denying_the_filesystem_refuses_rather_than_lying(lypning_lib, tmp_path, monkeypatch):
    """Telling the program the file is missing would be a wrong answer at exit
    0 — the one outcome the refusal contract exists to prevent."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "present.txt").write_text("here")
    out = lypning_lib.run("print(open('present.txt').read())", filesystem=False)
    assert out.status == embed.UNSUPPORTED
    assert out.kind == "sandbox"
    assert out.fall_onward is True


def test_denying_the_filesystem_still_runs_ordinary_programs(lypning_lib):
    assert lypning_lib.run("print(sum(range(10)))", filesystem=False).stdout == b"45\n"


def test_a_step_limit_bounds_a_program_that_will_not_stop(lypning_lib):
    """There is no process to kill, so this is the only bound an embedded run
    can be given — and it has to be a refusal, so the program still gets its
    answer from CPython."""
    out = lypning_lib.run("while True: pass", step_limit=100_000)
    assert out.status == embed.UNSUPPORTED
    assert out.kind == "steps"
    assert out.fall_onward is True


def test_the_step_limit_also_counts_inside_a_builtin(lypning_lib):
    """``sum(range(10**12))`` is one statement. A bound that only counted
    statements would let it run forever inside a host that asked for a bound."""
    out = lypning_lib.run("print(sum(range(10**12)))", step_limit=100_000)
    assert out.status == embed.UNSUPPORTED
    assert out.kind == "steps"


def test_a_step_limit_does_not_disturb_an_ordinary_program(lypning_lib):
    assert lypning_lib.run("print(sum(range(100)))", step_limit=100_000).stdout == b"4950\n"


def test_an_output_limit_refuses_rather_than_filling_the_host(lypning_lib):
    out = lypning_lib.run("print('x' * 100000)", output_limit=1000)
    assert out.status == embed.UNSUPPORTED
    assert out.kind == "output"
    assert out.stdout == b""


# --- what used to take the process down --------------------------------------


_DEEP = "x = []\ny = []\nfor i in range(20000):\n    x = [x]\n    y = [y]\n"


@pytest.mark.parametrize("program,kind", [
    ("x = " + "(" * 5000 + "1" + ")" * 5000, "recursion"),
    ("x = [1]\nfor i in range(2000): x = [x]\nprint(repr(x))", "recursion"),
    ("k = (1,)\nfor i in range(2000): k = (k,)\nd = {k: 1}", "recursion"),
    ('import json; json.loads("[" * 5000 + "]" * 5000)', "recursion"),
    # Every descent a program can drive, not only the three that were guarded
    # first: comparison reaches `in`, `sorted` and dict/set equality too.
    (_DEEP + "print(x == y)", "recursion"),
    (_DEEP + "print(sorted([x, y]) == [x, y])", "recursion"),
    (_DEEP + "print(x in [y])", "recursion"),
    ("import json\n" + _DEEP + "print(len(json.dumps(x)))", "recursion"),
    # A flat chain does not NEST — it is one node per term down a left-leaning
    # spine, which both the evaluator and the AST's own drop walk one frame at
    # a time.
    ("print(" + "+".join(["1"] * 100000) + ")", "recursion"),
    # An allocation Rust cannot fail: its handler aborts, which is not an
    # unwind and cannot be caught at the boundary.
    ("x = 'a' * (10 ** 14)\nprint(len(x))", "alloc"),
    ("x = [0] * (10 ** 12)\nprint(len(x))", "alloc"),
    # The lexer reads a zero byte as end-of-input, so this ran half a program
    # and reported success — the wrong-answer failure the contract exists for.
    ("print(1)\0print(2)", "source"),
])
def test_unbounded_recursion_refuses_instead_of_segfaulting(lypning_lib, program, kind):
    """Each of these overflowed the stack and killed the process. A stack
    overflow is not an unwind, so the ABI's guard cannot catch one — the depth
    has to be bounded before the stack is."""
    out = lypning_lib.run(program)
    assert out.status == embed.UNSUPPORTED
    assert out.kind == kind
    assert out.fall_onward is True


def test_tearing_down_a_deep_structure_does_not_recurse(lypning_lib):
    """The binary never noticed this: it exits, and the kernel reclaims what the
    program built. A library has to actually take the value apart, and doing it
    the derived way is one stack frame per level."""
    out = lypning_lib.run("x = [1]\nfor i in range(200000): x = [x]\nprint('built')")
    assert out.status == embed.OK
    assert out.stdout == b"built\n"
    # The point of the test is that the process is still here to run this.
    assert lypning_lib.run("print(1 + 1)").stdout == b"2\n"


def test_deep_programs_stay_refusals_on_a_small_host_stack():
    """The limits are sized for a HOST thread, not for a main one.

    A pthread default under musl, and a Node worker, hand the runtime a fraction
    of the 8 MB the main thread gets. A guard tuned on the main thread's stack
    is a guard that holds only where nobody embeds.
    """
    lib = _INSTALLED_LIBRARY_OR_SKIP()
    results: List[str] = []

    def work() -> None:
        for program, _kind in (
            ("print(" + "+".join(["1"] * 100000) + ")", "recursion"),
            (_DEEP + "print(x == y)", "recursion"),
            ("def f(n):\n    return 0 if n == 0 else 1 + f(n - 1)\nprint(f(179))", ""),
        ):
            results.append(lib.run(program).status_name)

    previous = threading.stack_size(1 << 20)
    try:
        t = threading.Thread(target=work)
        t.start()
        t.join()
    finally:
        threading.stack_size(previous)
    # The last one is a legal recursion at the depth `MAX_DEPTH` allows: the
    # bound must be tight enough for a 1 MB stack and loose enough to keep it.
    assert results == ["unsupported", "unsupported", "ok"]


def test_a_format_spec_too_wide_is_a_value_error_not_an_abort(lypning_lib):
    """`.parse().unwrap()` on a width field aborted the binary outright — exit
    134 for a program CPython answers with an ordinary exception."""
    out = lypning_lib.run("print(format(1, '9' * 30))")
    assert out.status == embed.ERROR
    assert b"ValueError" in out.stderr


def test_a_directory_made_is_a_commit(lypning_lib, tmp_path, monkeypatch):
    """`os.mkdir` cannot be staged — there is no content to hold back — so the
    run stops being reversible there. Reported as committed, or the host re-runs
    it on CPython and the second mkdir raises for a program that works."""
    monkeypatch.chdir(tmp_path)
    out = lypning_lib.run("import os\nos.mkdir('d')\nimport subprocess")
    assert (tmp_path / "d").is_dir()
    assert out.committed is True
    assert out.fall_onward is False


def test_a_refusal_in_finally_is_not_swallowed_by_a_break(lypning_lib):
    """A `break` in `finally` discards an in-flight EXCEPTION — CPython's rule,
    kept. It must not discard a refusal: that is a wrong answer at exit 0."""
    out = lypning_lib.run(
        "for i in range(1):\n"
        "    try:\n"
        "        import subprocess\n"
        "    finally:\n"
        "        break\n"
        "print('reached')")
    assert out.status == embed.UNSUPPORTED
    assert out.stdout == b""


def test_busy_and_a_clean_panic_are_routable(lypning_lib):
    """Three statuses mean "lypning did not answer": a refusal, a Busy that
    executed nothing, and a Panic that reached no commit. A host that only
    routed the first would report the other two to a user as the program's
    own failure."""
    out = lypning_lib.run("import subprocess")
    assert out.fall_onward is True
    assert lypning_lib.run("print(1)").fall_onward is False
    assert lypning_lib.run("raise ValueError('x')").fall_onward is False


# --- threading ---------------------------------------------------------------


def test_two_threads_run_two_programs_without_mixing(lypning_lib):
    """The state is thread_local, which is what makes this legal — and what
    makes a shared-state regression show up here first."""
    results: dict[int, bytes] = {}

    def work(n: int) -> None:
        results[n] = lypning_lib.run("import sys; print(sys.argv[1] * 3)",
                                     args=[str(n)]).stdout

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == {i: ("%d%d%d\n" % (i, i, i)).encode() for i in range(8)}


# --- the dispatcher's predicate ----------------------------------------------


@pytest.mark.parametrize("code,stderr,expected", [
    (90, b"lypning: unsupported: module: import re", True),
    (0, b"", False),
    (1, b"Traceback (most recent call last):\nValueError", False),
    (1, b"MemoryError", True),
    (0, b"Traceback (most recent call last):", True),
])
def test_fall_onward_matches_the_dispatchers_rule(lypning_lib, code, stderr, expected):
    assert lypning_lib.fall_onward(code, stderr) is expected


# --- routing -----------------------------------------------------------------


def test_route_answers_without_running_anything(lypning_lib, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = lypning_lib.route("import re\nopen('written.txt', 'w').write('x')")
    assert r.engine == "lypning-mp"
    assert r.kind == "module" and "re" in r.detail
    assert r.imports == ["re"]
    assert not (tmp_path / "written.txt").exists()


def test_route_agrees_with_the_binary(lypning_lib, lypning_bin):
    for program in ("print(1)", "import re", "async def f(): pass", "import subprocess"):
        spawned = subprocess.run([str(lypning_bin), "route", "-c", program],
                                 capture_output=True, text=True, check=False)
        assert lypning_lib.route(program).engine == spawned.stdout.split()[0]


# --- the header is the contract ----------------------------------------------


def test_every_exported_symbol_is_in_the_header(lypning_lib):
    """The header is hand-written, so nothing but a test keeps it honest. A
    symbol the library exports and the header omits is a capability no host can
    reach; one the header promises and the library lacks is a link error in
    somebody else's build."""
    nm = subprocess.run(["nm", "-D", "--defined-only", str(lypning_lib.path)],
                        capture_output=True, text=True, check=False)
    if nm.returncode != 0:
        pytest.skip("nm is not available")
    exported = {line.split()[-1] for line in nm.stdout.splitlines()
                if " lypning_" in line}
    header = (paths.INCLUDE_DIR / "lypning.h").read_text(encoding="utf-8")
    declared = {name for name in exported if name + "(" in header}
    assert exported == declared, "not declared in lypning.h: %s" % sorted(exported - declared)
    for line in header.splitlines():
        if line.startswith(("int ", "void ", "const ", "uint", "size_t", "lypning_")) and "(" in line:
            name = line.split("(")[0].split()[-1].lstrip("*")
            if name.startswith("lypning_"):
                assert name in exported, "%s is declared in lypning.h but not exported" % name


# --- discovery ---------------------------------------------------------------


def test_a_named_library_that_is_missing_is_a_bad_override_not_an_absence(monkeypatch, tmp_path):
    """"Not built" would send the caller to run a build they already ran, and
    it would be wrong every time — the same rule the engine overrides hold."""
    monkeypatch.setenv("LYPNING_LIB", str(tmp_path / "nope.so"))
    with pytest.raises(embed.LibraryError) as e:
        embed.find_library()
    assert "$LYPNING_LIB" in str(e.value)
    with pytest.raises(embed.LibraryError):
        embed.Library()


def test_a_truncated_library_is_reported_not_dlopened(monkeypatch, tmp_path, lypning_lib):
    """A link killed halfway through leaves one of these in a build tree, and
    `dlopen` maps it: the first call then reads a page past the end of the file
    and takes SIGBUS, which no `except` can see."""
    half = tmp_path / "liblypning.so"
    half.write_bytes(lypning_lib.path.read_bytes()[:200_000])
    monkeypatch.setenv("LYPNING_LIB", str(half))
    usable, why = engines.library_ready()
    assert usable is False
    assert "truncated" in why


def test_a_foreign_library_is_named_as_such(monkeypatch, tmp_path):
    other = Path("/lib/x86_64-linux-gnu/libm.so.6")
    if not other.is_file():
        pytest.skip("no other shared library to point at")
    monkeypatch.setenv("LYPNING_LIB", str(other))
    usable, why = engines.library_ready()
    assert usable is False
    assert "lypning" in why


def test_engines_reports_an_unusable_library_rather_than_failing_runs(monkeypatch, tmp_path):
    fake = tmp_path / "liblypning.so"
    fake.write_bytes(b"not an elf file")
    monkeypatch.setenv("LYPNING_LIB", str(fake))
    usable, why = engines.library_ready()
    assert usable is False
    assert why
