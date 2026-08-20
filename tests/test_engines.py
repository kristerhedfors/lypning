"""Refusals, the chain, and the two ways an engine can fail to answer.

The refusal line is the whole interface between the tiers: exit 90 plus
``<engine>: unsupported: <kind>: <detail>`` is what makes a wrong route cost one
spawn instead of a wrong answer, so it is pinned twice — once as a parse, once
against the binary that has to emit it.
"""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest

from lypning import engines

#: Verbatim stderr from `lypning -c 'import ctypes'`. Kept as a literal so the
#: parser is pinned even in a container where nothing is built.
REAL_REFUSAL = "lypning: unsupported: module: import ctypes\n"


def _result(rc: int, stderr: str = "", engine: str = engines.LYPNING) -> engines.Result:
    return engines.Result(engine, "/nowhere/" + engine, rc, "", stderr, 0)


def test_refusal_parses_kind_and_detail():
    r = _result(90, REAL_REFUSAL)
    assert r.unsupported
    assert r.refusal == ("module", "import ctypes")


def test_refusal_is_empty_unless_the_exit_code_says_so():
    # A program that prints the refusal shape and exits 1 is a program, not a
    # refusal; only exit 90 may move the dispatcher to the next tier.
    assert _result(1, REAL_REFUSAL).refusal == ("", "")
    assert not _result(1, REAL_REFUSAL).unsupported


def test_refusal_falls_back_to_the_whole_stderr_when_the_line_is_missing():
    r = _result(90, "lypning: something went sideways\n")
    assert r.refusal == ("", "lypning: something went sideways")


def test_refusal_survives_a_noisy_stderr():
    r = _result(90, "warning: ignore me\n" + REAL_REFUSAL)
    assert r.refusal == ("module", "import ctypes")


def test_live_engine_emits_the_refusal_contract(lypning_bin):
    r = engines.run(engines.LYPNING, "import ctypes", timeout=30)
    assert r.returncode == engines.UNSUPPORTED_EXIT
    assert r.unsupported and r.refusal[0]
    # The contract is stderr-only: a refusal on stdout poisons every pipeline
    # that the caller was going to fall through to CPython for.
    assert r.stdout == ""
    assert r.stderr.strip() == REAL_REFUSAL.strip()


def test_chain_from_each_tier():
    assert engines.chain_from(engines.LYPNING) == ["lypning", "lypning-mp", "cpython"]
    assert engines.chain_from(engines.MICROPYTHON) == ["lypning-mp", "cpython"]
    assert engines.chain_from(engines.CPYTHON) == ["cpython"]


def test_chain_from_an_unknown_engine_ends_at_cpython():
    # A classifier that names something that is not a tier must still produce a
    # runnable chain; CPython runs everything.
    assert engines.chain_from("nonesuch") == ["cpython"]


def _write_exec(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_find_cpython_walks_past_the_capture_shim(tmp_path, monkeypatch):
    shim_dir = tmp_path / "shimbin"
    real_dir = tmp_path / "realbin"
    shim_dir.mkdir()
    real_dir.mkdir()
    _write_exec(shim_dir / "python3", "#!/bin/sh\n# %s v1\nexec /bin/true\n" % engines.SHIM_MARKER)
    real = _write_exec(real_dir / "python3", "#!/bin/sh\nexec /bin/true\n")

    monkeypatch.setenv("PATH", os.pathsep.join([str(shim_dir), str(real_dir)]))
    found = engines.find_cpython()
    assert found == real.resolve()


def test_find_cpython_prefers_an_explicit_override(tmp_path, monkeypatch):
    override = _write_exec(tmp_path / "mypython", "#!/bin/sh\nexec /bin/true\n")
    monkeypatch.setenv("LYPNING_CPYTHON", str(override))
    assert engines.find_cpython() == override.resolve()


def test_run_reports_a_timeout_rather_than_raising():
    started = time.perf_counter()
    r = engines.run(engines.CPYTHON, "import time; time.sleep(30)", timeout=0.5)
    assert r.timed_out
    assert r.returncode == 124
    assert not r.unsupported  # a hang is not a refusal
    assert time.perf_counter() - started < 10


def test_run_on_an_absent_engine_is_a_result_not_an_exception(monkeypatch):
    monkeypatch.setattr(engines, "find", lambda engine: None)
    r = engines.run(engines.MICROPYTHON, "print(1)")
    assert r.returncode == 127
    assert r.binary == ""
    assert "not built" in r.stderr
    assert not r.unsupported  # "absent" must never be mistaken for "refused"


def test_run_on_a_binary_that_cannot_be_executed_is_a_result(tmp_path):
    missing = tmp_path / "nope"
    r = engines.run(engines.LYPNING, "print(1)", binary=missing)
    assert r.returncode == 127
    assert "cannot exec" in r.stderr


# --- discovery: what may and may not stand in for an engine -------------------


def _exe(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def nothing_built(tmp_path, monkeypatch):
    """No state bin dir and no cargo target, so only $PATH is left to search.

    In a checkout ``build_dir()`` is the asset tree, and the binary a developer
    just built there would answer every discovery question before $PATH was
    ever reached.
    """
    from lypning import paths
    empty = tmp_path / "no-build"
    empty.mkdir()
    monkeypatch.setenv("LYPNING_HOME", str(tmp_path / "empty-state"))
    monkeypatch.setattr(paths, "build_dir", lambda: empty)
    return empty


def test_a_console_script_named_lypning_is_not_the_engine(tmp_path, monkeypatch, nothing_built):
    """`pip install lypning` puts a console script of that name on $PATH.

    Exec into it and the process re-enters interpreter mode and execs itself
    again, forever — a hang with no output. Discovery must walk past it and
    report "not built", which is what every degradation path is written for.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _exe(bindir / engines.LYPNING,
         "#!/usr/bin/env python3\nfrom lypning.cli import main\nmain()\n")
    monkeypatch.setenv("PATH", str(bindir))
    assert engines.find_lypning() is None


def test_a_compiled_binary_on_path_is_the_engine(tmp_path, monkeypatch, nothing_built):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    real = bindir / engines.LYPNING
    real.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 32)
    real.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    assert engines.find_lypning() == real.resolve()


def test_an_override_that_points_at_nothing_is_an_error_not_a_fallback(monkeypatch):
    # Silently falling through would measure a binary the caller did not name
    # and report the number as if it had.
    monkeypatch.setenv("LYPNING_BIN", "/nowhere/at/all/lypning")
    with pytest.raises(engines.EngineError) as e:
        engines.find_lypning()
    assert "LYPNING_BIN" in str(e.value)


def test_an_override_pointing_at_a_script_is_an_error(tmp_path, monkeypatch):
    script = _exe(tmp_path / "lypning", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("LYPNING_MP_BIN", str(script))
    with pytest.raises(engines.EngineError):
        engines.find_micropython()


def test_a_cpython_override_that_points_at_nothing_is_an_error(monkeypatch):
    monkeypatch.setenv("LYPNING_CPYTHON", "/nowhere/at/all/python3")
    with pytest.raises(engines.EngineError) as e:
        engines.find_cpython()
    assert "LYPNING_CPYTHON" in str(e.value)
