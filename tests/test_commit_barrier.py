"""The commit barrier, and the one tier that does not have it.

Routing onward is only sound if a tier that refuses left nothing behind.
lypning's Rust core stages stdout and discards it on exit 90, so a refusal is
observably a no-op. lypning-mp is MicroPython and streams, so a program that
prints before reaching an unsupported construct has already committed those
bytes when it refuses.

Both halves are pinned here. The first is a guarantee and must not regress. The
second is a KNOWN DEFECT, and it is asserted rather than skipped so that the day
someone gives lypning-mp a barrier, this test fails and says so — a defect that
quietly heals is a defect nobody updates the documentation for.

See docs/LYPNING.md §6.
"""

from __future__ import annotations

import pytest

from lypning import UNSUPPORTED_EXIT, engines

# Prints, then refuses. The print must be reached before the refusal for either
# assertion below to mean anything.
PRINT_THEN_REFUSE_RUST = 'print("BEFORE")\nimport subprocess\n'
PRINT_THEN_REFUSE_MP = (
    'print("BEFORE")\n'
    "import unicodedata as u\n"
    "print(u.decomposition(chr(0xC0)))\n"
)

# The `lypning_bin` / `micropython_bin` fixtures rather than a module-level
# `skipif`: a mark is evaluated at IMPORT time, before the autouse fixture in
# conftest has moved `$LYPNING_HOME`, so in a wheel install — where the binary
# lives only under that dir — the mark saw a built engine and the test then ran
# against an unbuilt one and failed on exit 127. The fixtures resolve at call
# time, which is the only time the answer is true.


def test_rust_core_refuses_with_stdout_untouched(lypning_bin) -> None:
    r = engines.run(engines.LYPNING, PRINT_THEN_REFUSE_RUST)
    assert r.returncode == UNSUPPORTED_EXIT
    assert r.stdout == "", "the commit barrier let output escape before a refusal"
    assert r.stderr.startswith("lypning: unsupported: ")
    assert r.stderr.count("\n") == 1, "a refusal is exactly one line on stderr"


def test_micropython_tier_has_no_barrier(micropython_bin) -> None:
    """KNOWN DEFECT. If this fails, lypning-mp gained a barrier — update
    docs/LYPNING.md §6, the README, and conformance's `contract` verdict."""
    r = engines.run(engines.MICROPYTHON, PRINT_THEN_REFUSE_MP)
    assert r.returncode == UNSUPPORTED_EXIT
    assert r.stdout == "BEFORE\n", (
        "lypning-mp no longer leaks stdout before refusing — this is good news, "
        "but the documented defect and the conformance contract check are now stale"
    )


# ---- the directory half of the barrier (issue #51) -------------------------
#
# A directory is the one side effect that cannot be staged: `os.mkdir` has no
# content to hold back. It used to COMMIT the run, and from that line on every
# refusal — `builtin: eval`, `builtin: complex`, `bigint`, any of them — was
# reported as `lypning: error: … reached after output was already flushed` at
# exit 1, with the directory on disk and no answer, where CPython answers at
# exit 0. Three capability rounds each met it through a different refusal kind,
# which is why the programs below name three.
#
# The fix is an UNDO log rather than a second model of a directory tree: the
# directory is made for real, so `os.path.isdir`, `glob` and every write into it
# see what CPython would, and `io::rewind` removes it if the run has to fall
# onward. That it is removable is not luck — every file the program writes is
# staged, so the run's own directories are empty when the refusal arrives.

#: Three unrelated refusals, one side effect. The `bigint` one matters most: its
#: refusals depend on a VALUE, so no static walk can hoist them out of the run
#: the way `route.base64_static_check` hoists base64's — which is why moving the
#: barrier was the only fix that could close this.
MKDIR_THEN_REFUSE = [
    ('import os; os.mkdir("D"); print(eval("1"))', "1\n"),
    ('import os; os.mkdir("D"); print(complex(1))', "(1+0j)\n"),
    ('import os; os.mkdir("D"); print(2**100 / 3)', "4.2255020007607644e+29\n"),
]


def _tree(root) -> list:
    return sorted(str(q.relative_to(root)) for q in root.rglob("*"))


def _fresh(parent, name):
    """A cwd of its own per engine, so the second cannot read back what the
    first wrote — the same rule the corpus battery runs under."""
    d = parent / name
    d.mkdir()
    return d


@pytest.mark.parametrize("program, _answer", MKDIR_THEN_REFUSE,
                         ids=["eval", "complex", "bigint"])
def test_a_refusal_after_mkdir_is_a_refusal(lypning_bin, tmp_path, program, _answer) -> None:
    """Exit 90, and a cwd the next tier can run in."""
    cwd = tmp_path / "run"
    cwd.mkdir()
    r = engines.run(engines.LYPNING, program, cwd=cwd)
    assert r.returncode == UNSUPPORTED_EXIT, r.stderr
    assert r.stdout == ""
    assert r.stderr.startswith("lypning: unsupported: ")
    assert _tree(cwd) == [], "the barrier left a directory behind"


@pytest.mark.parametrize("program, answer", MKDIR_THEN_REFUSE,
                         ids=["eval", "complex", "bigint"])
def test_the_chain_makes_the_directory_exactly_once(tmp_path, program, answer) -> None:
    """The safety property, and it is self-checking.

    `os.mkdir` raises `FileExistsError` the second time, so a chain that ran the
    side effect twice could not exit 0 — CPython would fail on the retry. Exit
    0 with the right answer IS the proof that the retry saw a clean directory,
    and the attempt list proves a retry happened at all rather than the test
    passing because lypning answered on its own.
    """
    cwd = tmp_path / "chain"
    cwd.mkdir()
    d = engines.dispatch(program, cwd=cwd)
    assert [a.refused for a in d.attempts] == [True] * len(d.attempts)
    assert d.attempts, "nothing refused, so this program never exercised the barrier"
    assert d.result.returncode == 0, d.result.stderr
    assert d.result.stdout == answer
    assert _tree(cwd) == ["D"]

    # …and against the interpreter itself, which is what "exactly once" means.
    alone = tmp_path / "alone"
    alone.mkdir()
    ref = engines.run(engines.CPYTHON, program, cwd=alone)
    assert (ref.returncode, ref.stdout) == (d.result.returncode, d.result.stdout)
    assert _tree(alone) == _tree(cwd)


def test_a_run_that_succeeds_makes_it_exactly_once(lypning_bin, tmp_path) -> None:
    """The other half. A barrier that removed too much would show up here."""
    cwd = tmp_path / "ok"
    cwd.mkdir()
    d = engines.dispatch('import os; os.makedirs("a/b/c"); print("done")', cwd=cwd)
    assert d.attempts == [], "no tier refused, so nothing was ever rewound"
    assert d.result.returncode == 0, d.result.stderr
    assert d.result.stdout == "done\n"
    assert _tree(cwd) == ["a", "a/b", "a/b/c"]


def test_only_what_the_run_made_is_taken_back(lypning_bin, tmp_path) -> None:
    """A rewind is not `rm -rf`: it removes the run's own directories, in
    reverse order, and stops at the prefix that was already there."""
    cwd = tmp_path / "mixed"
    (cwd / "keep").mkdir(parents=True)
    (cwd / "a").mkdir()
    r = engines.run(engines.LYPNING,
                    'import os; os.makedirs("a/b/c"); os.mkdir("D"); print(eval("1"))',
                    cwd=cwd)
    assert r.returncode == UNSUPPORTED_EXIT, r.stderr
    assert _tree(cwd) == ["a", "keep"]


def test_a_file_staged_under_a_new_directory_goes_with_it(lypning_bin, tmp_path) -> None:
    """Why the rewind can always succeed from inside one run: the file is
    staged, so the directory is still empty when the refusal arrives."""
    cwd = tmp_path / "staged"
    cwd.mkdir()
    r = engines.run(
        engines.LYPNING,
        'import os; os.mkdir("D"); open("D/f","w").write("x"); print(eval("1"))',
        cwd=cwd,
    )
    assert r.returncode == UNSUPPORTED_EXIT, r.stderr
    assert _tree(cwd) == []


def test_removing_a_directory_the_run_did_not_make_still_commits(lypning_bin, tmp_path) -> None:
    """`os.rmdir` is the half that stays irreversible, and it must: `create_dir`
    cannot give back a mode, a timestamp or an owner. So the refusal after it is
    this program's own error and the chain never retries it — which is the
    behaviour `os.mkdir` used to share and no longer does."""
    cwd = tmp_path / "rm"
    (cwd / "gone").mkdir(parents=True)
    r = engines.run(engines.LYPNING, 'import os; os.rmdir("gone"); print(eval("1"))', cwd=cwd)
    assert r.returncode == 1
    assert "cannot be routed onward" in r.stderr
    assert _tree(cwd) == []


def test_dispatcher_contains_the_leak(micropython_bin) -> None:
    """The leak is invisible through `lypning run`: each tier's stdout is
    captured in the parent and dropped on exit 90, so the caller sees exactly
    one tier's output. This is what keeps the mixture arm clean, and it is a
    weaker guarantee than the engine holding the barrier itself — it holds only
    while the dispatcher is the one running the program."""
    d = engines.dispatch(PRINT_THEN_REFUSE_MP)
    assert d.result.returncode == 0
    assert d.result.stdout.count("BEFORE") == 1, "a refused tier's output was replayed"
    assert all(a.unsupported for a in d.attempts)


# ---- the holes an adversary found in the #51 barrier -----------------------
#
# The undo log itself held: nothing below reopens double execution. What the
# fix had not covered was the arm where the undo FAILS, the reads it made
# reachable, and the two halves of `os.mkdir` that were never CPython's.


def test_a_failed_rewind_costs_the_program_only_its_routing(lypning_bin, tmp_path) -> None:
    """The one arm that must degrade to the OLD behaviour and no further.

    `rewind` used to `discard()` FIRST and try the removals after, so a
    directory it could not take back — only something outside the process can
    arrange that — cost the program its stdout as well: exit 1 with an empty
    stream, where the barrier this replaced reported the same exit 1 with the
    program's own output intact. A fix may not make its unfixable case worse
    than the defect it fixes.

    The outside actor is this test: it waits for `D`, plants a file in it, and
    only then lets the program past the `sys.stdin.read()` that is holding it.
    """
    import subprocess
    import time

    cwd = tmp_path / "stuck"
    cwd.mkdir()
    program = 'import os, sys; os.mkdir("D"); print("BEFORE"); sys.stdin.read(); print(eval("1"))'
    p = subprocess.Popen(
        [str(lypning_bin), "-c", program], cwd=str(cwd),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    for _ in range(500):
        if (cwd / "D").is_dir():
            break
        time.sleep(0.01)
    (cwd / "D" / "planted").write_text("not ours")
    out, err = p.communicate("", timeout=30)

    assert p.returncode == 1, err
    assert out == "BEFORE\n", "a failed undo threw away output the program had already produced"
    assert "cannot be routed onward" in err
    assert _tree(cwd) == ["D", "D/planted"], "nothing is forced, and nothing else is taken"


def test_the_refusal_names_the_cause_it_actually_had(lypning_bin, tmp_path) -> None:
    """`is_committed()` has three producers and the line quotes one of them.

    An `os.rmdir` of a foreign directory was reported as `reached after output
    was already flushed` — a diagnostic naming a mechanism that never ran.
    """
    cwd = tmp_path / "why"
    (cwd / "gone").mkdir(parents=True)
    r = engines.run(engines.LYPNING, 'import os; os.rmdir("gone"); print(eval("1"))', cwd=cwd)
    assert r.returncode == 1
    assert "os.rmdir removed a directory this run did not make" in r.stderr
    assert "flushed" not in r.stderr


#: `open()` reads the path WHOLE and slices the snapshot, so a stream with no
#: end never returns. A hang is the one outcome worse than a wrong answer: it
#: has no exit code, so the dispatcher cannot even reach the next tier.
ENDLESS = [
    (engines.LYPNING, 'print(open("/dev/zero").read(1))'),
    (engines.LYPNING, 'print(open("/dev/random").read(1))'),
    (engines.LYPNING, 'print(open("/dev/null").read())'),
    (engines.LYPNING, 'open("/dev/zero", "a"); print(open("/dev/zero").read(1))'),
    # `Path.read_bytes` and `os.rename` reach a different `fs::read` each, and
    # both used to be unbounded. `pathlib` is a `lypning-l` capability.
    (engines.LYPNING_L, 'import pathlib; print(pathlib.Path("/dev/zero").read_bytes()[:1])'),
    (engines.LYPNING, 'import os; os.rename("/dev/zero", "z"); print("renamed")'),
]


@pytest.mark.parametrize("engine, program", ENDLESS,
                         ids=["read", "random", "null", "append-base", "pathlib", "rename"])
def test_a_stream_with_no_end_refuses_rather_than_hangs(lypning_bin, engine, program) -> None:
    r = engines.run(engine, program, timeout=20)
    assert not r.timed_out, "the read never came back"
    assert r.returncode == UNSUPPORTED_EXIT, r.stderr
    assert "open-special" in r.stderr


def test_a_regular_file_is_untouched_by_the_bound(lypning_bin, tmp_path) -> None:
    (tmp_path / "g").write_text("abc")
    r = engines.run(engines.LYPNING, 'print(open("g").read(2))', cwd=tmp_path)
    assert (r.returncode, r.stdout) == (0, "ab\n")


#: Every one of these was exit 0 with an answer CPython does not give.
#: `exist_ok` is not a `mkdir` keyword at all — CPython raises TypeError — and
#: `mode=` reached `create_dir`, which hands the kernel 0o777 and lets the umask
#: decide, so the directory existed with the wrong permissions.
MKDIR_SIGNATURE = [
    'import os; os.mkdir("D", exist_ok=True); print("ok")',
    'import os; os.makedirs("D", nonsense=1); print("ok")',
    'import os; os.makedirs("D", mode=0o700); print(oct(os.stat("D").st_mode & 0o777))',
    'import os; os.mkdir("D", 0o700); print(oct(os.stat("D").st_mode & 0o777))',
]


@pytest.mark.parametrize("program", MKDIR_SIGNATURE, ids=["mkdir-kw", "unknown-kw", "kw-mode", "positional-mode"])
def test_mkdir_serves_its_signature_exactly_or_refuses_it(lypning_bin, program) -> None:
    r = engines.run(engines.LYPNING, program)
    assert r.returncode == UNSUPPORTED_EXIT, r.stderr
    assert r.stderr.startswith("lypning: unsupported: mkdir: ")


@pytest.mark.parametrize("program", MKDIR_SIGNATURE, ids=["mkdir-kw", "unknown-kw", "kw-mode", "positional-mode"])
def test_the_chain_answers_the_mkdir_signature_as_cpython_does(tmp_path, program) -> None:
    got = engines.dispatch(program, cwd=_fresh(tmp_path, "chain"))
    ref = engines.run(engines.CPYTHON, program, cwd=_fresh(tmp_path, "alone"))
    assert (got.result.returncode, got.result.stdout) == (ref.returncode, ref.stdout)
    assert _tree(tmp_path / "chain") == _tree(tmp_path / "alone")


def test_a_mkdir_over_this_runs_own_delete_refuses(lypning_bin, tmp_path) -> None:
    """The two halves of `make_dir` consult different layers.

    The existence test is `path_exists`, which honours a staged delete; the
    creation is `create_dir`, which can only see the disk the delete has not
    reached yet. So `os.remove('F'); os.mkdir('F')` raised FileExistsError at
    exit 1 where CPython exits 0 with a directory. Neither layer can serve it,
    so it refuses — and the chain then does both for real.
    """
    program = 'import os; os.remove("F"); os.mkdir("F"); print("ok")'
    cwd = _fresh(tmp_path, "engine")
    (cwd / "F").write_text("")
    r = engines.run(engines.LYPNING, program, cwd=cwd)
    assert r.returncode == UNSUPPORTED_EXIT, r.stderr
    assert r.stderr.startswith("lypning: unsupported: mkdir: ")
    assert (cwd / "F").is_file(), "the refusal left the staged delete undone"

    chain, alone = _fresh(tmp_path, "chain"), _fresh(tmp_path, "alone")
    (chain / "F").write_text("")
    (alone / "F").write_text("")
    got = engines.dispatch(program, cwd=chain)
    ref = engines.run(engines.CPYTHON, program, cwd=alone)
    assert (got.result.returncode, got.result.stdout) == (ref.returncode, ref.stdout)
    assert (chain / "F").is_dir() and _tree(chain) == _tree(alone)


def test_a_mkdir_over_a_delete_of_a_file_never_on_disk_refuses(lypning_bin, tmp_path) -> None:
    """The same disagreement the other way round, and the worse half.

    Here `create_dir` SUCCEEDS — the staged file never reached the disk — and
    the staged delete then arrives at the commit to unlink a path that has
    become a directory, so the program printed its answer AND a
    `PermissionError` traceback at exit 1 where CPython exits 0.
    """
    program = 'open("F","w").write("x")\nimport os\nos.remove("F")\nos.mkdir("F")\nprint("ok")\n'
    r = engines.run(engines.LYPNING, program, cwd=_fresh(tmp_path, "engine"))
    assert r.returncode == UNSUPPORTED_EXIT, r.stderr
    assert r.stdout == "", "a refusal must not print"

    chain, alone = _fresh(tmp_path, "chain"), _fresh(tmp_path, "alone")
    got = engines.dispatch(program, cwd=chain)
    ref = engines.run(engines.CPYTHON, program, cwd=alone)
    assert (got.result.returncode, got.result.stdout) == (ref.returncode, ref.stdout), \
        got.result.stderr
    assert _tree(chain) == _tree(alone) == ["F"]


# ---- the safety property, once more, over every shape ----------------------
#
# `mark_committed` exists for one reason: a program must never run its side
# effects twice. Each program below makes a directory and then goes on to do
# something with it, half of them ending in a refusal the chain has to fall
# through. `os.mkdir` raises FileExistsError the second time, so a chain that
# re-ran the side effect could not agree with CPython — which is what makes
# comparing the two the whole proof.

#: `(program, how many `D` the run should leave)`. The count is the half a
#: comparison against CPython cannot make on its own: the two agreeing on an
#: EMPTY tree is also what a barrier that removed too much would produce.
NEVER_TWICE = [
    ('import os; os.mkdir("D"); print(eval("1"))', 1, "mkdir-then-refuse"),
    ('import os; os.mkdir("D"); print("made")', 1, "mkdir-then-succeed"),
    ('import os; os.makedirs("D/x/y"); print(eval("1"))', 1, "nested-makedirs"),
    ('import os; os.makedirs("D/x/y"); print("deep")', 1, "nested-makedirs-ok"),
    ('import os; os.mkdir("D"); open("D/f","w").write("x"); print(eval("1"))', 1,
     "write-then-refuse"),
    ('import os; os.mkdir("D"); open("D/f","w").write("x"); print("wrote")', 1,
     "write-then-succeed"),
    ('import os, os.path; os.mkdir("D"); print(os.path.isdir("D"), eval("1"))', 1,
     "isdir-then-refuse"),
    ('import os, os.path; os.mkdir("D"); print(os.path.isdir("D"))', 1, "isdir"),
    ('import os; os.mkdir("D"); print(sorted(os.listdir(".")))', 1, "listdir-parent"),
    ('import os, glob; os.mkdir("D"); open("D/f","w").write("x"); print(sorted(glob.glob("D/*")))',
     1, "glob-into-it"),
    # The two that end with nothing on disk. Running the side effect twice is
    # still visible: the second `os.mkdir` raises where the first did not.
    ('import os; os.mkdir("D"); os.rmdir("D"); print(eval("1"))', 0, "rmdir-then-refuse"),
    ('import os; os.mkdir("D"); os.rmdir("D"); print("gone")', 0, "rmdir"),
]


@pytest.mark.parametrize("program, made, _id", NEVER_TWICE, ids=[i for _, _n, i in NEVER_TWICE])
def test_the_chain_runs_the_side_effect_exactly_once(tmp_path, program, made, _id) -> None:
    """The whole reason `mark_committed` exists, held over every shape.

    `os.mkdir` raises FileExistsError the second time, so a chain that re-ran
    the program could not agree with CPython on stdout and the exit code — and
    the tree comparison catches the other direction, a barrier that took back
    more than the run made.
    """
    chain, alone = _fresh(tmp_path, "chain"), _fresh(tmp_path, "alone")
    got = engines.dispatch(program, cwd=chain)
    ref = engines.run(engines.CPYTHON, program, cwd=alone)
    assert got.result.stdout == ref.stdout, got.result.stderr
    assert got.result.returncode == ref.returncode, got.result.stderr
    assert _tree(chain) == _tree(alone)
    assert _tree(chain).count("D") == made, "the side effect did not run exactly once"
