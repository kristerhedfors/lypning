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
