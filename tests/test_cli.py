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
