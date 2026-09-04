"""The two roots, and the environment variables that move them.

Only the overrides are pinned here. They are the whole contract between this
module, the POSIX-sh shim and the hooks: all three resolve the same log from the
same two variables, and a change to either default silently splits one capture
feed into two.
"""

from __future__ import annotations

from lypning import paths

from conftest import requires_git


def test_state_dir_and_bin_dir_follow_lypning_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LYPNING_HOME", str(tmp_path / "elsewhere"))
    assert paths.state_dir() == tmp_path / "elsewhere"
    assert paths.bin_dir() == tmp_path / "elsewhere" / "bin"


def test_state_dir_defaults_under_home(tmp_path, monkeypatch):
    monkeypatch.delenv("LYPNING_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    assert paths.state_dir() == tmp_path / "h" / ".lypning"


def test_blank_lypning_home_is_not_a_path(tmp_path, monkeypatch):
    # An exported-but-empty variable is the shell's way of saying "unset"; taking
    # it literally would put the state dir at the filesystem root.
    monkeypatch.setenv("LYPNING_HOME", "   ")
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    assert paths.state_dir() == tmp_path / "h" / ".lypning"


def test_log_path_follows_lypning_log(tmp_path, monkeypatch):
    monkeypatch.setenv("LYPNING_LOG", str(tmp_path / "custom.jsonl"))
    assert paths.log_path() == tmp_path / "custom.jsonl"


def test_log_path_defaults_into_the_state_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("LYPNING_LOG", raising=False)
    monkeypatch.setenv("LYPNING_HOME", str(tmp_path / "state"))
    assert paths.log_path() == tmp_path / "state" / "invocations.jsonl"


def test_project_dir_prefers_claude_project_dir(tmp_path, monkeypatch):
    d = tmp_path / "somewhere"
    d.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(d))
    assert paths.project_dir() == d.resolve()


def test_project_dir_ignores_a_claude_project_dir_that_is_not_a_directory(tmp_path, monkeypatch):
    # Claude Code exports it unconditionally; a stale value must not win over a
    # real work tree, or every sighting lands somewhere that does not exist.
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "gone"))
    assert paths.project_dir(tmp_path) == tmp_path.resolve()


@requires_git
def test_project_dir_falls_back_to_the_git_toplevel(git_repo, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    deep = git_repo / "a" / "b"
    deep.mkdir(parents=True)
    assert paths.project_dir(deep) == git_repo.resolve()


def test_project_dir_falls_back_to_the_start_dir_outside_a_work_tree(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    assert paths.project_dir(lonely) == lonely.resolve()


def test_routes_dir_is_state_and_moves_with_lypning_home(tmp_path, monkeypatch):
    # State, never assets: the route ledger is what THIS machine's runs learned,
    # so it must not be shippable and must not survive a `rm -rf ~/.lypning`.
    monkeypatch.setenv("LYPNING_HOME", str(tmp_path / "elsewhere"))
    assert paths.routes_dir() == tmp_path / "elsewhere" / "routes"
    assert paths.ASSETS not in paths.routes_dir().parents


def test_sightings_dir_is_one_directory_under_the_project(tmp_path):
    assert paths.sightings_dir(tmp_path) == tmp_path / "tests" / "corpus" / "sightings"
