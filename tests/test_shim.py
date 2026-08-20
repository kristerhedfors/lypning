"""Installing the shim, and the one case where it must refuse.

The copy is three lines; the refusal is the module. A file at ``<bin>/python3``
that is not one of ours is presumed to be somebody's interpreter, and clobbering
it fails later, somewhere else, as a different bug every time. So: never without
``--force``, never one name without the other, and always restorable.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from lypning import paths, shim

FOREIGN = "#!/bin/sh\n# somebody's venv stub\nexec /usr/bin/python3 \"$@\"\n"


@pytest.fixture
def bin_dir(tmp_path):
    return tmp_path / "bin"


def _plant(path, body=FOREIGN):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_install_uninstall_round_trip(bin_dir):
    lines = shim.install(bin_dir)
    assert len(lines) == 2
    for name in shim.SHIM_NAMES:
        target = bin_dir / name
        assert target.is_file()
        assert shim.is_shim(target)
        assert os.access(str(target), os.X_OK)
    assert [s.state for s in shim.status(bin_dir)] == ["current", "current"]

    out = shim.uninstall(bin_dir)
    assert [s.state for s in shim.status(bin_dir)] == ["absent", "absent"]
    assert not any((bin_dir / n).exists() for n in shim.SHIM_NAMES)
    # The captured programs outlive the harness that captured them.
    assert any("was NOT deleted" in line for line in out)


def test_installing_twice_is_a_no_op_that_says_so(bin_dir):
    shim.install(bin_dir)
    lines = shim.install(bin_dir)
    assert all("unchanged" in line for line in lines)
    assert [s.state for s in shim.status(bin_dir)] == ["current", "current"]


def test_a_stale_shim_is_refreshed_rather_than_refused(bin_dir):
    # A SessionStart hook runs the installer every session; an older copy of our
    # own shim is ours to replace.
    _plant(bin_dir / "python3", "#!/bin/sh\n# %s v0 — older\nexit 0\n" % "LYPNING_SHIM_MARKER")
    assert [s.state for s in shim.status(bin_dir)] == ["absent", "stale"]
    lines = shim.install(bin_dir)
    assert any("refreshed" in line for line in lines)
    assert [s.state for s in shim.status(bin_dir)] == ["current", "current"]


def test_refuses_to_clobber_a_foreign_python_without_force(bin_dir):
    target = _plant(bin_dir / "python3")
    with pytest.raises(shim.ShimError) as excinfo:
        shim.install(bin_dir)
    assert "REFUSING" in str(excinfo.value)
    assert "--force" in str(excinfo.value)
    assert target.read_text(encoding="utf-8") == FOREIGN
    # All names or none: a run that installed `python` and then refused
    # `python3` leaves one source line resolving two interpreters.
    assert not (bin_dir / "python").exists()


def test_force_moves_the_foreign_file_aside_and_uninstall_puts_it_back(bin_dir):
    target = _plant(bin_dir / "python3")
    lines = shim.install(bin_dir, force=True)
    backup = bin_dir / ("python3" + shim.BACKUP_SUFFIX)
    assert any("backed up" in line for line in lines)
    assert backup.read_text(encoding="utf-8") == FOREIGN
    assert shim.is_shim(target)  # moved, never copied: only one file claims the name

    shim.uninstall(bin_dir)
    assert target.read_text(encoding="utf-8") == FOREIGN
    assert not backup.exists()
    assert not (bin_dir / "python").exists()


def test_uninstall_leaves_a_foreign_file_alone(bin_dir):
    target = _plant(bin_dir / "python3")
    lines = shim.uninstall(bin_dir)
    assert target.read_text(encoding="utf-8") == FOREIGN
    assert any("left alone" in line for line in lines)


def test_is_shim_says_no_to_a_binary_and_to_nothing(tmp_path):
    assert not shim.is_shim(tmp_path / "absent")
    _plant(tmp_path / "elf", "\x7fELF" + "\x00" * 64)
    assert not shim.is_shim(tmp_path / "elf")


def test_path_problem_is_loud_when_the_bin_dir_is_not_on_path(bin_dir, monkeypatch):
    shim.install(bin_dir)
    monkeypatch.setenv("PATH", "/usr/bin")
    assert "NOT on PATH" in (shim.path_problem(bin_dir) or "")


def test_path_problem_is_loud_when_a_real_interpreter_shadows_the_shim(bin_dir, tmp_path,
                                                                       monkeypatch):
    shim.install(bin_dir)
    ahead = tmp_path / "ahead"
    _plant(ahead / "python3")
    monkeypatch.setenv("PATH", os.pathsep.join([str(ahead), str(bin_dir)]))
    problem = shim.path_problem(bin_dir) or ""
    # An installed shim that never runs reads as a working install for as long
    # as it takes somebody to wonder why the corpus is empty.
    assert "BEHIND" in problem and str(ahead / "python3") in problem


def test_path_problem_is_none_when_the_shim_wins(bin_dir, monkeypatch):
    shim.install(bin_dir)
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), "/usr/bin"]))
    assert shim.path_problem(bin_dir) is None


def test_the_shim_defaults_to_the_same_log_paths_resolves(bin_dir, tmp_path, monkeypatch):
    """With only ``$LYPNING_HOME`` set, both feeds must land in the same file.

    The regression this guards: the shim once read ``$LYPNING_LOG`` and then
    ``$HOME/.lypning``, skipping ``$LYPNING_HOME`` entirely. Nothing failed —
    the shim wrote one file, :func:`paths.log_path` and ``lypning-harvest.sh``
    read another, and the shim feed simply never reached a sighting.
    """
    shim.install(bin_dir)
    state = tmp_path / "elsewhere"
    monkeypatch.setenv("LYPNING_HOME", str(state))
    monkeypatch.delenv("LYPNING_LOG", raising=False)

    real = shutil.which("python3", path="/usr/bin:/bin")
    if not real:
        pytest.skip("no system python3 to exec into")
    env = dict(os.environ, PATH=os.pathsep.join([str(bin_dir), os.path.dirname(real)]))
    out = subprocess.run([str(bin_dir / "python3"), "-c", "print('shimmed')"],
                         capture_output=True, text=True, env=env, check=False)
    assert out.returncode == 0 and out.stdout == "shimmed\n"

    log = paths.log_path()
    assert log == state / "invocations.jsonl"
    assert log.is_file(), "the shim wrote somewhere paths.log_path() does not read"
    assert json.loads(log.read_text(encoding="utf-8").splitlines()[-1])["program"] == "print('shimmed')"
