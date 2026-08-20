"""The front door: does every command still parse, and does anything traceback.

Nothing here measures behaviour that lives in a sibling module — the CLI's job
is to resolve arguments, render what those modules return, and map outcomes onto
exit codes. What is worth pinning is that the parser is intact for every
subcommand (a typo in one ``add_argument`` is invisible until someone types that
command), that ``--json`` is machine-readable, and that a user never sees a
traceback.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from lypning import cli

SRC = str(Path(__file__).resolve().parents[1] / "src")


@pytest.mark.parametrize("command", cli.COMMANDS)
def test_every_subcommand_help_parses(command, capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main([command, "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: lypning %s" % command)


@pytest.mark.parametrize("flag", ["--help", "-h", "--version", "-V"])
def test_the_top_level_flags_belong_to_us(flag, capsys):
    # Every OTHER dash-flag is the interpreter's and is passed through, which is
    # what makes `lypning -u script.py` work.
    with pytest.raises(SystemExit) as excinfo:
        cli.main([flag])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip()


def test_no_arguments_prints_help_and_succeeds(capsys):
    assert cli.main([]) == 0
    assert "usage: lypning" in capsys.readouterr().out


def test_status_json_is_valid_json(capsys):
    assert cli.main(["status", "--json"]) == 0
    obj = json.loads(capsys.readouterr().out)
    assert obj["version"]
    assert set(obj["engines"]) == set(("lypning", "lypning-mp", "cpython"))
    for name, e in obj["engines"].items():
        assert isinstance(e["built"], bool)
        assert (e["path"] is None) == (not e["built"])
    assert obj["corpus"]["entries"] and obj["corpus"]["entries"] > 0


def test_status_reports_an_unbuilt_engine_as_not_built(capsys, no_micropython):
    assert cli.main(["status", "--json"]) == 0
    obj = json.loads(capsys.readouterr().out)
    assert obj["engines"]["lypning-mp"] == {"path": None, "built": False,
                                            "bytes": 0, "blocks": 0}
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    assert "lypning-mp: not built" in capsys.readouterr().out


def test_corpus_stats_render(capsys):
    assert cli.main(["corpus", "--stats"]) == 0
    assert "entries" in capsys.readouterr().out


def _cli(*args, **kw):
    """One real process, because "no traceback" is a claim about stderr.

    The environment is built from nothing rather than inherited: a subprocess
    that kept the ambient $HOME and no $CLAUDE_PROJECT_DIR would resolve the
    project to whatever checkout the suite is running from.
    """
    home = kw.pop("home")
    env = {"PYTHONPATH": SRC, "PATH": "/usr/bin:/bin", "HOME": home,
           "LYPNING_HOME": home + "/state", "CLAUDE_PROJECT_DIR": home}
    env.update(kw.pop("env", {}))
    return subprocess.run([sys.executable, "-m", "lypning"] + list(args),
                          capture_output=True, text=True, env=env, cwd=home, timeout=120)


def test_a_bad_subcommand_exits_2_without_a_traceback(tmp_path):
    p = _cli("nosuchcommand", home=str(tmp_path))
    assert p.returncode == 2
    assert "invalid choice" in p.stderr
    assert "Traceback" not in p.stderr
    assert p.stdout == ""


def test_a_malformed_option_exits_2_without_a_traceback(tmp_path):
    p = _cli("conformance", "--limit", "not-a-number", home=str(tmp_path))
    assert p.returncode == 2
    assert "Traceback" not in p.stderr


def test_a_command_with_nothing_to_run_exits_2_without_a_traceback(tmp_path):
    # `Usage` is one line and exit 2 — the fix is an argument, not a stack.
    p = _cli("run", home=str(tmp_path))
    assert p.returncode == 2
    assert "Traceback" not in p.stderr
    assert p.stderr.startswith("lypning: run: ")


def test_route_names_a_tier_and_says_why(capsys, lypning_bin):
    assert cli.main(["route", "-c", "import ctypes"]) == 0
    out = capsys.readouterr().out
    assert out.split("\t")[0] in ("lypning", "lypning-mp", "cpython")


def test_run_passes_a_program_s_own_exit_code_through(capsys, lypning_bin):
    # 90 included: a caller that dispatches on 90 is the reason 90 exists.
    assert cli.main(["run", "-c", "import sys; sys.exit(3)"]) == 3
    assert cli.main(["run", "-c", "print('hi')"]) == 0
    assert capsys.readouterr().out == "hi\n"


def test_corpus_time_json_is_a_record_a_later_run_can_read(capsys, tmp_path, lypning_bin):
    rec = tmp_path / "before.json"
    assert cli.main(["corpus-time", "--limit", "2", "--repeat", "1",
                     "--record", str(rec), "--json"]) == 0
    obj = json.loads(capsys.readouterr().out)
    assert obj["engine"] == "lypning"
    assert obj["corpus_loaded"] >= obj["timed"] >= 1
    # The file is the same record, which is what --baseline reads back.
    assert json.loads(rec.read_text(encoding="utf-8"))["entries"] == obj["entries"]
    assert cli.main(["corpus-time", "--limit", "2", "--repeat", "1",
                     "--baseline", str(rec), "--json"]) == 0
    again = json.loads(capsys.readouterr().out)
    assert again["diff"]["shared"] >= 1


@pytest.mark.parametrize("content,expect", [
    ("not json at all", "is not JSON"),
    ('{"hello": 1}', "not a corpus-time record"),
    ('{"schema": "who-knows", "entries": {}}', "re-record it"),
])
def test_a_corrupt_baseline_is_one_line_naming_the_fix(content, expect, tmp_path, capsys):
    """And it costs a second, not the whole measurement.

    ``--baseline`` is read BEFORE the corpus is timed on purpose: discovering
    the comparison is impossible after ten minutes of measurement throws the
    measurement away. This asserts the message, and the absence of a traceback
    — a stack trace here reads as a bug in lypning rather than a bad file.
    """
    bad = tmp_path / "baseline.json"
    bad.write_text(content, encoding="utf-8")
    assert cli.main(["corpus-time", "--limit", "1", "--baseline", str(bad)]) == 1
    err = capsys.readouterr().err
    assert expect in err and "Traceback" not in err
    assert err.count("\n") == 1


def test_a_missing_baseline_names_the_command_that_writes_one(tmp_path, capsys):
    assert cli.main(["corpus-time", "--limit", "1",
                     "--baseline", str(tmp_path / "nope.json")]) == 1
    err = capsys.readouterr().err
    assert "corpus-time --record" in err and "Traceback" not in err


def test_an_unwritable_record_target_is_refused_before_the_run(tmp_path, capsys, monkeypatch):
    """The destination is checked up front for the same reason the baseline is.

    A run that timed the whole corpus and then could not write the file has
    thrown the expensive half away, and the only evidence left is an
    ``OSError`` the caller did not ask for.
    """
    from lypning import bench

    def explode(*_a, **_k):
        raise AssertionError("the corpus was timed before the target was checked")

    monkeypatch.setattr(bench, "corpus_time_one", explode)
    # An ancestor that exists and is not a directory: unwritable for any user,
    # including the root this suite often runs as, where a mode bit would not be
    # enforced and the test would pass by accident.
    blocker = tmp_path / "a-file"
    blocker.write_text("", encoding="utf-8")
    assert cli.main(["corpus-time", "--limit", "1",
                     "--record", str(blocker / "sub" / "r.json")]) == 1
    err = capsys.readouterr().err
    assert "--record" in err and "Traceback" not in err


def test_a_record_target_that_is_a_directory_says_so(tmp_path, capsys, monkeypatch):
    from lypning import bench

    monkeypatch.setattr(bench, "corpus_time_one",
                        lambda *a, **k: pytest.fail("checked too late"))
    assert cli.main(["corpus-time", "--limit", "1", "--record", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "is a directory" in err and "Traceback" not in err


def test_corpus_time_on_an_unbuilt_engine_exits_2_without_a_traceback(no_micropython, capsys):
    # Exit 2: nothing ran and the fix is a command, which is what 2 is for here.
    assert cli.main(["corpus-time", "--engine", "lypning-mp", "--limit", "1"]) == 2
    err = capsys.readouterr().err
    assert "not built" in err and "Traceback" not in err


def test_bench_micropython_exits_2_when_the_control_is_absent(no_micropython, monkeypatch, capsys):
    from lypning import build

    monkeypatch.setattr(build, "stock_binary", lambda: None)
    assert cli.main(["bench", "--micropython"]) == 2
    err = capsys.readouterr().err
    assert "lypning build --stock" in err and "Traceback" not in err


def test_build_stock_dry_run_builds_nothing_and_says_what_it_would_run(capsys):
    assert cli.main(["build", "--stock", "--dry-run", "--json"]) == 0
    obj = json.loads(capsys.readouterr().out)
    assert obj["dry_run"] is True
    # Only the control: --stock is not a tier, and asking for it must not
    # silently rebuild the two engines.
    assert [r["engine"] for r in obj["results"]] == ["micropython-stock"]
    assert obj["installed"] == []
