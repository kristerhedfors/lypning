"""Shared fixtures. The invariant: **a test may not touch $HOME or this repo.**

Every path the package resolves comes from an environment variable — the state
dir, the capture log, the project — so one autouse fixture that points all three
at ``tmp_path`` is enough to make the whole suite hermetic. Without it a
``lypning install`` test writes into the developer's ``~/.claude/settings.json``
and a conformance test's git net restores files in the checkout it is running
from, which is a test suite that can lose work.

The second job here is the engine tiers. ``lypning`` may or may not be built and
``lypning-mp`` almost never is (it needs a network), so anything that spawns one
takes the matching fixture and is skipped rather than failed when it is absent.
The check is made at call time, not at import time: the autouse fixture moves
``$LYPNING_HOME``, so where the binary resolves from is not known until the test
is running.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lypning import engines  # noqa: E402  (after the path insert, on purpose)

#: Cleared rather than preserved: an engine override in the developer's shell
#: would silently change which binary every test measured.
_ENGINE_OVERRIDES = ("LYPNING_BIN", "LYPNING_MP_BIN", "LYPNING_CPYTHON", "LYPNING_LIB")


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Point every writable path this package knows about at ``tmp_path``."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    for d in (home, project):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("LYPNING_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LYPNING_LOG", str(tmp_path / "state" / "invocations.jsonl"))
    # Set, not unset: with no CLAUDE_PROJECT_DIR the git fallback finds the real
    # checkout, and `conformance.run` would then arm its restore net on it.
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.delenv("LYPNING_CAPTURE", raising=False)
    monkeypatch.delenv("LYPNING_HARVEST", raising=False)
    for name in _ENGINE_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    return project


@pytest.fixture
def project(isolated_env):
    """The throwaway project directory ``$CLAUDE_PROJECT_DIR`` points at."""
    return isolated_env


@pytest.fixture
def lypning_bin():
    """The Rust core, or skip. Resolved now — the autouse fixture moved $LYPNING_HOME."""
    b = engines.find_lypning()
    if b is None:
        pytest.skip("the Rust core is not built (`lypning build --rust`)")
    return b


@pytest.fixture
def lypning_lib():
    """The C ABI library, or skip. Optional exactly like the MicroPython tier.

    Loaded, not merely located: a library built before a symbol was added is
    found by :func:`lypning.embed.find_library` and then fails on the first
    call, and a suite that reported that as thirty failures instead of one skip
    would be reporting the developer's stale build as a broken runtime.
    """
    from lypning import embed
    path = embed.find_library()
    if path is None:
        pytest.skip("the C ABI is not built (`lypning build --lib`)")
    try:
        return embed.Library(path)
    except embed.LibraryError as e:
        pytest.skip("the C ABI at %s is not usable: %s" % (path, e))


@pytest.fixture
def micropython_bin():
    """The MicroPython tier, or skip. Absent in any container without a network."""
    b = engines.find_micropython()
    if b is None:
        pytest.skip("lypning-mp is not built")
    return b


requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture
def git_repo(tmp_path):
    """A one-commit git work tree, with an identity that does not read ``~/.gitconfig``."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    root = tmp_path / "repo"
    root.mkdir()
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    })

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(root)] + list(args),
                       capture_output=True, text=True, check=True, env=env)

    git("init", "-q")
    (root / "tracked.txt").write_text("original\n", encoding="utf-8")
    git("add", "tracked.txt")
    git("commit", "-q", "-m", "seed")
    return root


@pytest.fixture
def no_micropython(monkeypatch):
    """The usual container: ``lypning-mp`` is not built.

    Simulated rather than detected. Building that tier needs a network, so it is
    absent almost everywhere — but not everywhere, and a test of the degradation
    path that silently skips itself on the one machine where the binary happens
    to exist is a test of nothing on that machine.
    """
    available = engines.available
    monkeypatch.setattr(engines, "find_micropython", lambda: None)
    monkeypatch.setattr(
        engines, "available",
        lambda: dict(available(), **{engines.MICROPYTHON: None}),
    )
    return None
