"""The offline Codex feed: rollouts in, redacted host-tagged programs out.

Everything here is a fixture rollout under ``tmp_path`` — the suite never
reads a real ``~/.codex`` (conftest points ``$HOME`` at a temp dir, and these
tests pass their roots explicitly on top of that). What is pinned is the three
things that fail silently if they drift: the JS decode (read raw, a heredoc is
one line of ``\\n`` escapes and extracts nothing), the ``call_id`` key (a fork
copies history, so a file-and-line key counts a call once per copy), and the
attribution (``turn_context.model``, tagged ``codex``, never a Claude field).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lypning import codex


def _line(kind, payload, ts="2026-09-20T10:00:00.000Z"):
    return json.dumps({"timestamp": ts, "type": kind, "payload": payload})


def _meta(session="sess-codex-1", cwd="/work/proj"):
    return _line("session_meta", {"id": session, "cwd": cwd, "originator": "codex_cli_rs"})


def _turn(model, cwd="/work/proj"):
    return _line("turn_context", {"turn_id": "t", "cwd": cwd, "model": model})


def _exec_command(call_id, cmd, workdir=None):
    args = {"cmd": cmd}
    if workdir:
        args["workdir"] = workdir
    return _line("response_item", {"type": "function_call", "name": "exec_command",
                                   "arguments": json.dumps(args), "call_id": call_id})


def _exec_js(call_id, js):
    return _line("response_item", {"type": "custom_tool_call", "name": "exec",
                                   "status": "completed", "call_id": call_id, "input": js})


# A heredoc exactly as Codex's `exec` tool carries it: a JS string literal whose
# newlines are the two characters backslash-n, inside a JS program.
JS_HEREDOC = ('const r = await tools.exec_command({\n'
              '  cmd: "python3 - <<\'PY\'\\nimport sys\\nprint(\\"hi\\", sys.argv)\\nPY",\n'
              '  workdir: "/work/proj",\n'
              '});\ntext(r);')


def _write(path: Path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def sessions(tmp_path):
    root = tmp_path / "codex" / "sessions"
    _write(root / "2026" / "09" / "20" / "rollout-2026-09-20T10-00-00-a.jsonl", [
        _meta(),
        _turn("gpt-5.4"),
        _exec_command("call_1", "python3 -c 'print(40 + 2)'"),
        _exec_command("call_2", "ls -la"),  # not python: never a record
        _exec_js("call_3", JS_HEREDOC),
        _turn("gpt-6-astra"),
        _exec_command("call_4", "cd /x && python3 -c 'import json; print(json.dumps(1))'",
                      workdir="/work/other"),
        '{"torn line, Codex still writing',
    ])
    return root


def test_the_js_escaped_heredoc_is_decoded_before_extraction(sessions):
    progs = {p.call_id: p for p in codex.collect(sessions)}
    assert progs["call_3"].program == 'import sys\nprint("hi", sys.argv)'
    assert progs["call_1"].program == "print(40 + 2)"
    assert "call_2" not in progs


def test_each_program_carries_its_turns_model_and_the_codex_host(sessions):
    progs = {p.call_id: p for p in codex.collect(sessions)}
    assert progs["call_1"].model == "gpt-5.4"
    assert progs["call_3"].model == "gpt-5.4"
    # The model is the most recent turn_context's, not the session's first.
    assert progs["call_4"].model == "gpt-6-astra"
    assert {p.host for p in progs.values()} == {"codex"}
    assert {p.source for p in progs.values()} == {"codex"}
    assert {p.session for p in progs.values()} == {"sess-codex-1"}
    assert progs["call_4"].cwd == "/work/other"
    assert progs["call_1"].cwd == "/work/proj"
    assert progs["call_1"].ts == "2026-09-20T10:00:00.000Z"


def test_a_rescan_and_a_forked_copy_count_once(sessions):
    """A forked session copies its parent's history into a new rollout."""
    first = [p.key for p in codex.collect(sessions)]
    assert first and len(first) == len(set(first))
    assert [p.key for p in codex.collect(sessions)] == first

    original = next(sessions.rglob("*.jsonl")).read_text(encoding="utf-8")
    _write(sessions / "2026" / "09" / "21" / "rollout-2026-09-21T09-00-00-fork.jsonl",
           [_meta("sess-fork"), _turn("gpt-5.5")] + original.splitlines()[1:])
    again = codex.collect(sessions)
    assert [p.key for p in again] == first
    # The first sighting in path order — the original — keeps its attribution.
    assert {p.session for p in again} == {"sess-codex-1"}
    assert all(p.key.startswith("codex:call_") for p in again)


def test_a_record_names_no_claude_join_field(sessions):
    """No GPT-served call may be resolved against a Claude transcript."""
    records = [c.record() for c in codex.iter_calls(sessions)]
    assert records
    for rec in records:
        assert rec["host"] == "codex" and rec["kind"] == "bash_command"
        assert rec["model"] and rec["run"].startswith("call_")
        assert "tool_use_id" not in rec
        assert rec["transcript"] is None


def test_commands_and_programs_are_redacted(tmp_path, monkeypatch):
    secret = "sk-ant-api03-" + "Q" * 40
    root = tmp_path / "s"
    _write(root / "r.jsonl", [
        _meta(), _turn("gpt-5.4"),
        _exec_command("call_s", "python3 -c 'password = \"hunter2hunter2\"; print(1)'"),
        _exec_command("call_t", "API_KEY=%s python3 -c 'print(2)'" % secret),
    ])
    progs = codex.collect(root)
    assert progs and all("hunter2hunter2" not in p.program for p in progs)
    for call in codex.iter_calls(root):
        assert "hunter2hunter2" not in call.command and secret not in call.command
        assert call.redactions


def test_an_absent_root_is_empty_and_nothing_is_printed(tmp_path, capsys):
    assert codex.collect(tmp_path / "nope") == []
    assert list(codex.iter_calls(tmp_path / "nope")) == []
    assert capsys.readouterr() == ("", "")


def test_the_default_root_follows_codex_home(tmp_path, monkeypatch):
    monkeypatch.delenv("LYPNING_CODEX_SESSIONS", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ch"))
    assert codex.sessions_root() == tmp_path / "ch" / "sessions"
    monkeypatch.setenv("LYPNING_CODEX_SESSIONS", str(tmp_path / "pinned"))
    assert codex.sessions_root() == tmp_path / "pinned"


def test_the_rollouts_are_only_ever_read(sessions, monkeypatch):
    files = sorted(sessions.rglob("*.jsonl"))
    listing = sorted(sessions.rglob("*"))
    before = [(f.read_bytes(), os.stat(str(f)).st_mtime_ns) for f in files]
    real_open = open

    def guarded(file, mode="r", *a, **k):
        assert not any(c in mode for c in "wax+"), "opened for writing: %s" % file
        return real_open(file, mode, *a, **k)

    with monkeypatch.context() as m:
        m.setattr("builtins.open", guarded)
        codex.collect(sessions)
        list(codex.iter_calls(sessions))
    assert [(f.read_bytes(), os.stat(str(f)).st_mtime_ns) for f in files] == before
    assert sorted(sessions.rglob("*")) == listing


def test_older_argv_shaped_calls_are_unwrapped(tmp_path):
    root = tmp_path / "s"
    _write(root / "r.jsonl", [
        _meta(), _turn("gpt-5.2-codex"),
        _line("response_item", {"type": "local_shell_call", "call_id": "call_l",
                                "action": {"type": "exec", "command":
                                           ["bash", "-lc", "python3 -c 'print(3)'"]}}),
        _line("response_item", {"type": "function_call", "name": "shell", "call_id": "call_m",
                                "arguments": json.dumps({"command":
                                                         ["python3", "-c", "print(4)"]})}),
    ])
    assert sorted(p.program for p in codex.collect(root)) == ["print(3)", "print(4)"]


# --- the JS literal decoder --------------------------------------------------


@pytest.mark.parametrize("src,expected", [
    ('tools.exec_command({cmd: "a\\nb"})', ["a\nb"]),
    ("tools.exec_command({cmd: 'it\\'s'})", ["it's"]),
    ('tools.exec_command({"cmd": "q"})', ["q"]),
    ("x({cmd: `tmpl\\tok`})", ["tmpl\tok"]),
    ('x({cmd: "\\x41\\u00e9\\u{1F600}\\ud83d\\ude00"})', ["Aé\U0001F600\U0001F600"]),
    ('x({cmd: "one \\\nline"})', ["one line"]),
    ('Promise.all([t({cmd: "a"}), t({cmd: "b"})])', ["a", "b"]),
    # Decided at run time, so never guessed at.
    ("x({cmd: `echo ${dir}`})", []),
    ("x({cmd: c})", []),
    ('x({mycmd: "no"}); y.cmd = "no"', []),
    ('x({cmd: "unterminated', []),
])
def test_js_string_literals(src, expected):
    assert codex.js_string_literals(src) == expected


def test_model_counts_is_an_aggregate(sessions):
    counts = codex.model_counts(codex.collect(sessions))
    assert counts == {"gpt-5.4": 2, "gpt-6-astra": 1}


def test_harvest_reads_the_feed_end_to_end(sessions):
    """The two halves were written apart; this joins the real modules."""
    from lypning import harvest
    sightings = harvest.parse_codex(sessions_dir=sessions)
    programs = {s.program for s in sightings}
    assert "print(40 + 2)" in programs
    assert {s.source for s in sightings} == {"codex"}
    again = harvest.parse_codex(sessions_dir=sessions)
    assert sorted(s.key for s in again) == sorted(s.key for s in sightings)
