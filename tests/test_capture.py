"""The hook that must never fail the tool call it is only watching.

Two things: the pythonish screen, and the protocol. The screen is deliberately
broader than precise — a false positive costs one log line, a miss costs a
corpus entry that can never be recovered — so both halves of the table are
pinned. The protocol is one line of stdout and exit 0 on EVERY path, including
the paths where the hook's own work failed; a hook that raised on a malformed
payload would break a Bash call that had nothing to do with python.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lypning import capture, paths

MATCHES = [
    "python3 -c 'print(1)'",
    "python -c 'x'",
    "python3.11 -m json.tool",
    "cd /srv && python3 script.py",
    "cat f | python3 -",
    "PYTHONPATH=src python3 -c 'import x'",
    "py -c 'print(1)'",
    "uv run script.py",
    "poetry run pytest",
    "python3 <<'PY'\nprint(1)\nPY",
    # Deliberately over-broad: a quoted `python` in a commit message reads the
    # same as a quoted invocation, and one wasted parse is the cheaper error.
    "git commit -m 'add python support'",
    "cat <<PYTHON > f\nx\nPYTHON",
    # The write half of write-then-run: a heredoc redirected into a .py file,
    # whatever its delimiter. The later `python x.py` shows only a path.
    "cat > /tmp/a.py <<'EOF'\nprint(1)\nEOF",
    "cat <<EOF > a.py\nx = 1\nEOF",
    "cat >> \"dir/b.py\" << \"END\"\nx\nEND",
    "cat <<'EOF' | tee -a s.py\nx\nEOF",
    "mkdir -p t && cat > t/test_x.py <<-EOF\n\tx\n\tEOF",
]

MISSES = [
    "",
    "ls -la",
    "npm run build",           # the reason the runners are listed by name
    "cargo build --release",
    "grep -r pythonic src",    # `python` must be a whole word
    "echo mypython3",
    "ls /usr/lib/python3.11/site-packages",  # a path, not an invocation
    "cat <<'EOF'\nhello\nEOF",
    # Narrower than the shell screen's `*.py*`, on purpose: not exactly `.py`,
    # not the heredoc's own line, or no heredoc at all.
    "cat > stub.pyi <<EOF\nx\nEOF",
    "cat <<EOF > a.py.bak\nx\nEOF",
    "cat > notes.txt <<EOF\nsee a.py\nEOF",
    "cat a.py",
    "echo x > a.py",
]


@pytest.mark.parametrize("command", MATCHES)
def test_pythonish_matches(command):
    assert capture.looks_pythonish(command)


@pytest.mark.parametrize("command", MISSES)
def test_pythonish_misses(command):
    assert not capture.looks_pythonish(command)


def test_pythonish_survives_a_non_string():
    assert not capture.looks_pythonish(None)


def _fire(hook, payload, stdin_text=None):
    out = io.StringIO()
    text = stdin_text if stdin_text is not None else json.dumps(payload)
    rc = hook(io.StringIO(text), out)
    return rc, out.getvalue()


BASH_EVENT = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "python3 -c 'print(1)'"},
    "session_id": "sess-1",
}

NOISE = [
    ("garbage", "not json {{{"),
    ("empty", ""),
    ("whitespace", "   \n"),
    ("a bare array", "[1, 2, 3]"),
    ("a non-Bash tool", json.dumps({"tool_name": "Write",
                                    "tool_input": {"command": "python3 -c 'x'"}})),
    ("a Bash call with no python", json.dumps({"tool_name": "Bash",
                                               "tool_input": {"command": "ls -la"}})),
    ("a tool_input that is not an object", json.dumps({"tool_name": "Bash",
                                                       "tool_input": "python3 -c 'x'"})),
]


@pytest.mark.parametrize("hook", [capture.hook_pre_tool_use, capture.hook_stop],
                         ids=["pre-tool-use", "stop"])
@pytest.mark.parametrize("label,text", NOISE, ids=[n for n, _ in NOISE])
def test_hook_answers_the_protocol_and_exits_zero(hook, label, text):
    rc, out = _fire(hook, None, text)
    assert rc == 0
    assert out == capture.OK_RESPONSE + "\n"
    # No permissionDecision: answering `allow` here would bypass the permission
    # prompt for every Bash command in the session.
    assert "permissionDecision" not in out
    assert not paths.log_path().exists()


def test_hook_logs_a_python_bash_command():
    rc, out = _fire(capture.hook_pre_tool_use, BASH_EVENT)
    assert rc == 0 and out == capture.OK_RESPONSE + "\n"
    lines = paths.log_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["kind"] == "bash_command"
    assert rec["command"] == "python3 -c 'print(1)'"
    assert rec["session"] == "sess-1"
    assert rec["ts"].endswith("Z")


# The PreToolUse payload the CLI actually sends for a SUBAGENT's Bash call:
# the same shape plus `agent_id`/`agent_type`, and a `tool_use_id` whose block
# lives in the subagent's own transcript rather than the session's.
SUBAGENT_EVENT = dict(BASH_EVENT, tool_use_id="toolu_01abc",
                      agent_id="a5228b5e71af7dc5f", agent_type="general-purpose")


def test_the_hook_records_the_tool_use_id_that_names_the_model():
    """The id is the only join key back to the model that typed the command.

    Nothing in the payload names a model and no CLAUDE_* variable exposes one,
    so if this id is not written down here the attribution cannot be recovered
    later at all — the command string alone does not identify a turn. The join
    itself is deliberately NOT done here: this runs before every Bash call in
    the session.
    """
    rc, out = _fire(capture.hook_pre_tool_use, SUBAGENT_EVENT)
    assert rc == 0 and out == capture.OK_RESPONSE + "\n"
    assert "permissionDecision" not in out
    rec = json.loads(paths.log_path().read_text(encoding="utf-8").splitlines()[0])
    assert rec["tool_use_id"] == "toolu_01abc"
    assert rec["agent_type"] == "general-purpose"
    assert rec["agent_id"] == "a5228b5e71af7dc5f"


def test_a_main_loop_call_carries_no_agent_keys():
    # `agent_id` is absent from the payload for a main-loop call, and its
    # presence is what says "this was a subagent" — so a null would be a claim
    # the payload never made.
    rec = capture.record_bash_command(BASH_EVENT)
    assert rec["tool_use_id"] is None
    assert "agent_id" not in rec and "agent_type" not in rec


def test_only_the_claude_mapper_writes_a_join_key_no_other_harness_has():
    """`tool_use_id` belongs to `from_claude_event`, not to `record_command`.

    It is a key into a Claude Code transcript and nothing else has one. Set in
    the shared builder it would put a permanent `"tool_use_id": null` into every
    opencode and OpenHands record, naming a join those harnesses do not have —
    and opencode's `run` (its `callID`) is the same concept in another
    namespace, deliberately not unified with it for the same reason.
    """
    neutral = capture.record_command("python3 -c 1", host="opencode", tool="bash",
                                     run="call_1")
    assert "tool_use_id" not in neutral
    assert neutral["run"] == "call_1"
    for mapper, event in (
        (capture.from_opencode_event,
         {"tool": "bash", "args": {"command": "python3 -c 1"}, "callID": "call_1"}),
        (capture.from_openhands_event,
         {"tool_name": "terminal", "tool_input": {"command": "python3 -c 1"},
          "session_id": "s", "working_dir": "/w"}),
    ):
        rec = mapper(event)
        assert rec is not None
        assert "tool_use_id" not in rec
        assert "agent_id" not in rec and "agent_type" not in rec
    # And the Claude mapper still writes it, always, possibly null.
    assert "tool_use_id" in capture.from_claude_event(BASH_EVENT)


def test_capture_disabled_still_answers_but_writes_nothing(monkeypatch):
    monkeypatch.setenv("LYPNING_CAPTURE", "0")
    assert not capture.capture_enabled()
    rc, out = _fire(capture.hook_pre_tool_use, BASH_EVENT)
    assert rc == 0
    assert out == capture.OK_RESPONSE + "\n"
    assert not paths.log_path().exists()


def test_stop_hook_does_not_harvest_when_capture_is_disabled(monkeypatch, project):
    monkeypatch.setenv("LYPNING_CAPTURE", "0")
    paths.ensure_dir(paths.log_path().parent)
    paths.log_path().write_text(
        json.dumps({"kind": "python_invocation", "program": "print('x')",
                    "session": "sess-1", "ts": "2026-01-01T00:00:00.000Z"}) + "\n",
        encoding="utf-8")
    rc, out = _fire(capture.hook_stop, {"cwd": str(project)})
    assert rc == 0 and out == capture.OK_RESPONSE + "\n"
    assert not paths.sightings_dir(project).exists()


def test_an_unwritable_log_does_not_reach_the_tool_call(monkeypatch, tmp_path):
    # append_record falls back to a per-uid tmp path and then gives up silently.
    monkeypatch.setenv("LYPNING_LOG", str(tmp_path / "nope" / "x" / "log.jsonl"))
    monkeypatch.setattr(capture, "_fallback_log", lambda: tmp_path / "also" / "gone" / "l.jsonl")
    monkeypatch.setattr(capture.Path, "mkdir",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))
    rc, out = _fire(capture.hook_pre_tool_use, BASH_EVENT)
    assert rc == 0
    assert out == capture.OK_RESPONSE + "\n"


def test_every_hook_can_reach_the_package_from_a_source_checkout():
    """The arm three hooks have now lost, one at a time, for the same reason.

    A hook finds the package one of three ways: the ``lypning`` console script,
    the source tree via ``$CLAUDE_PROJECT_DIR/src``, or a bare
    ``python3 -m lypning``. In a *checkout of lypning itself* — a session with
    the package neither installed nor on PATH — only the middle arm works, and
    that is precisely the session most worth capturing, because it is the one
    editing the engine.

    Both other arms fail SILENTLY there, because invariant 5 says a hook never
    fails a session: it prints ``{"continue":true}`` and exits 0 on every path,
    including its own failures. So a hook missing this arm does not break, it
    goes quiet — capture ran inert for a full session before anyone noticed, and
    the session-start hook went on *reporting* that capture was inert while the
    capture hook's own third arm had it running.

    Checked by grep rather than by running the hooks, because what fails here is
    a path that only exists in an environment the suite cannot conjure: the
    thing to assert is that the arm is present in the file at all.
    """
    hooks = sorted(paths.HOOKS_SRC.glob("lypning-*.sh"))
    assert hooks, "no hook scripts found in %s" % paths.HOOKS_SRC
    missing = []
    for h in hooks:
        text = h.read_text(encoding="utf-8")
        if "$CLAUDE_PROJECT_DIR/src/lypning/__init__.py" not in text:
            missing.append(h.name)
        elif 'PYTHONPATH="$CLAUDE_PROJECT_DIR/src' not in text:
            missing.append(h.name + " (guards on the source tree but never adds it)")
    assert not missing, (
        "these hooks cannot reach the package from a checkout of lypning itself, "
        "and will go quiet rather than fail: %s" % missing)


def test_the_committed_hooks_match_the_ones_the_installer_ships():
    """``.claude/hooks/`` is a copy, and a copy drifts.

    The tree carries the hooks twice: ``assets/claude/hooks/`` is what
    ``lypning install`` writes into someone else's project, and ``.claude/hooks/``
    is this repository running its own harness on itself. Dogfooding is the
    point — it is how the inert-capture bug was found — but it only works while
    the two are the same file. A fix applied to one and not the other means this
    session is testing something no user gets, or shipping something nobody ran.
    """
    shipped = paths.HOOKS_SRC
    local = Path(__file__).resolve().parents[1] / ".claude" / "hooks"
    if not local.is_dir():
        pytest.skip("no .claude/hooks in this install shape")
    drifted = []
    for h in sorted(shipped.glob("lypning-*.sh")):
        mine = local / h.name
        if not mine.is_file():
            drifted.append(h.name + " (not installed here)")
        elif mine.read_text(encoding="utf-8") != h.read_text(encoding="utf-8"):
            drifted.append(h.name + " (differs)")
    assert not drifted, "the committed hooks have drifted from the shipped ones: %s" % drifted


# --- the harness mappers -----------------------------------------------------
#
# One record builder, three field mappings. What is pinned here is the literals:
# every one of these tool names was verified against a real install, and each of
# them fails the same silent way if it drifts — the hook runs, matches nothing,
# logs nothing, and reports success.

HOST_EVENTS = [
    ("claude", capture.from_claude_event,
     {"tool_name": "Bash", "tool_input": {"command": "python3 -c 'print(1)'"}},
     "python3 -c 'print(1)'"),
    ("claude rejects another tool", capture.from_claude_event,
     {"tool_name": "Write", "tool_input": {"command": "python3 -c 'x'"}}, None),
    ("openhands", capture.from_openhands_event,
     {"tool_name": "terminal", "tool_input": {"command": "python3 -c 'print(1)'"},
      "tool_response": {"exit_code": 0}, "session_id": "s", "working_dir": "/w"},
     "python3 -c 'print(1)'"),
    ("openhands rejects another tool", capture.from_openhands_event,
     {"tool_name": "file_editor", "tool_input": {"command": "python3 -c 'x'"}}, None),
    ("opencode", capture.from_opencode_event,
     {"tool": "bash", "args": {"command": "python3 -c 'print(1)'"},
      "sessionID": "s", "callID": "c"},
     "python3 -c 'print(1)'"),
    ("opencode rejects another tool", capture.from_opencode_event,
     {"tool": "read", "args": {"command": "python3 -c 'x'"}}, None),
]


@pytest.mark.parametrize("label,mapper,event,expected", HOST_EVENTS,
                         ids=[n for n, _m, _e, _x in HOST_EVENTS])
def test_each_mapper_reads_its_own_harness(label, mapper, event, expected):
    rec = mapper(event)
    if expected is None:
        assert rec is None
    else:
        assert rec is not None and rec["command"] == expected


def test_the_tool_name_literals_are_the_verified_ones():
    """The specific trap, pinned.

    ``terminal`` is the registry key the OpenHands SDK derives; ``TerminalTool``
    is the class name, is stale in the SDK's own docstring examples, and matches
    nothing. ``bash`` is the tool id opencode exposes and pins for plugin
    compatibility; a mapper written against ``shell`` alone would observe
    nothing, forever, and say nothing about it.
    """
    ok = {"tool_input": {"command": "python3 -c 1"}}
    assert capture.from_openhands_event(dict(ok, tool_name="terminal")) is not None
    assert capture.from_openhands_event(dict(ok, tool_name="TerminalTool")) is None

    args = {"args": {"command": "python3 -c 1"}}
    assert capture.from_opencode_event(dict(args, tool="bash")) is not None
    # `shell` is a forward alias only: it can over-match, never under-match.
    assert capture.from_opencode_event(dict(args, tool="shell")) is not None
    assert capture.from_opencode_event(dict(args, tool="read")) is None


@pytest.mark.parametrize("host", capture.HOSTS)
def test_a_record_carries_its_host(host):
    rec = capture.record_command("python3 -c 1", host=host, tool="t")
    assert rec is not None and rec["host"] == host


NEW_HOOKS = [
    ("openhands-post-tool-use", capture.hook_openhands_post_tool_use),
    ("openhands-session-end", capture.hook_openhands_session_end),
]


@pytest.mark.parametrize("name,hook", NEW_HOOKS, ids=[n for n, _ in NEW_HOOKS])
@pytest.mark.parametrize("label,text", NOISE, ids=[n for n, _ in NOISE])
def test_the_new_hooks_answer_the_protocol_and_exit_zero(name, hook, label, text):
    rc, out = _fire(hook, None, text)
    assert rc == 0
    assert out == capture.OK_RESPONSE + "\n"
    assert "permissionDecision" not in out
    # OpenHands HONOURS a `decision` key. That is exactly why we never send one.
    assert '"decision"' not in out
    assert not paths.log_path().exists()


def test_no_hook_entry_point_can_return_two():
    """In OpenHands, 2 is *block the agent*.

    Every entry point in the table, driven with every kind of noise, must come
    back 0 — including the paths where the hook's own work failed.
    """
    from lypning import cli

    for name, attr in cli._HOOK_EVENTS.items():
        hook = getattr(capture, attr)
        for _label, text in NOISE:
            rc, _out = _fire(hook, None, text)
            assert rc == 0, "%s returned %r" % (name, rc)


def test_the_openhands_hook_records_the_exit_code_it_was_given():
    event = {"tool_name": "terminal",
             "tool_input": {"command": "python3 -c 'print(1)'"},
             "tool_response": {"output": "1\n", "exit_code": 0},
             "session_id": "sess-1", "working_dir": "/w"}
    rc, out = _fire(capture.hook_openhands_post_tool_use, event)
    assert rc == 0 and out == capture.OK_RESPONSE + "\n"
    rec = json.loads(paths.log_path().read_text().strip())
    assert rec["host"] == "openhands"
    assert rec["session"] == "sess-1"
    assert rec["exit_code"] == 0
    assert rec["kind"] == "bash_command"


def test_the_routing_prompt_ships_and_says_the_two_load_bearing_things():
    text = capture.routing_prompt()
    assert text, "the routing paragraph did not ship"
    # Without this clause an agent reads the subset as a challenge — one wrote
    # 54 lines of SHA-256 to avoid importing hashlib (docs/PROMPTING.md).
    assert "Correctness comes first" in text
    # And without this one it treats a refusal as a failure to work around.
    assert "90" in text


# --- the shell screen, run for real -------------------------------------------

HOOK_SH = paths.HOOKS_SRC / "lypning-capture.sh"


def _run_capture_sh(tmp_path, command, env_extra=None, path_dirs=()):
    """Drive lypning-capture.sh with a Bash event and a stub ``lypning``.

    The stub is the first arm; it touches a marker and exits 0, so the marker
    existing means the screen let the command through to a spawn.
    """
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "lypning"
    stub.write_text('#!/bin/sh\ncat >/dev/null\n: > "$STUB_MARK"\nexit 0\n', encoding="utf-8")
    stub.chmod(0o755)
    mark = tmp_path / "reached"
    if mark.exists():
        mark.unlink()
    env = {"PATH": os.pathsep.join([str(stub_dir)] + list(path_dirs) + ["/usr/bin", "/bin"]),
           "HOME": str(tmp_path), "STUB_MARK": str(mark)}
    env.update(env_extra or {})
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command},
                          "session_id": "s", "cwd": str(tmp_path), "tool_use_id": "t"})
    proc = subprocess.run(["sh", str(HOOK_SH)], input=payload, capture_output=True,
                          text=True, env=env, timeout=60)
    return proc, mark.exists()


@pytest.mark.parametrize("command", MATCHES)
def test_the_shell_screen_is_broader_than_the_regexes(tmp_path, command):
    """Invariant 5's cost rule, held on the real script rather than by reading it.

    Every command the precise filter accepts must get PAST the fork-free
    ``case`` screen, or it never reaches the filter at all: a screen narrower
    than ``PYTHONISH`` loses a corpus entry with nothing anywhere saying so.
    """
    assert capture.looks_pythonish(command)
    proc, reached = _run_capture_sh(tmp_path, command)
    assert proc.returncode == 0
    assert proc.stdout == capture.OK_RESPONSE + "\n"
    assert reached, "the shell screen dropped a command PYTHONISH accepts: %r" % command


@pytest.mark.parametrize("command", ["ls -la", "cat <<'EOF'\nhello\nEOF", "npm run build"])
def test_the_screen_answers_a_plain_command_without_spawning(tmp_path, command):
    proc, reached = _run_capture_sh(tmp_path, command)
    assert proc.returncode == 0 and proc.stdout == capture.OK_RESPONSE + "\n"
    assert not reached


def test_a_pinned_source_tree_is_an_arm_outside_any_checkout(tmp_path):
    """``$LYPNING_PYTHONPATH``: the arm a user-scope hook has when nothing is
    installed and the session is not in a checkout of lypning.

    The stub ``lypning`` is absent here and ``$CLAUDE_PROJECT_DIR`` is not a
    checkout, so the record in the log can only have come from the pin.
    """
    src = Path(capture.__file__).resolve().parents[1]
    assert (src / "lypning" / "__init__.py").is_file()
    py_dir = tmp_path / "py-bin"
    py_dir.mkdir()
    # The BASE interpreter, not a virtualenv's: the environment running this
    # suite may have lypning installed, which would make the bare arm log too
    # and leave the pin unproven.
    (py_dir / "python3").symlink_to(getattr(sys, "_base_executable", None) or sys.executable)
    log = tmp_path / "log.jsonl"
    elsewhere = tmp_path / "someone-elses-repo"
    elsewhere.mkdir()
    payload = json.dumps({"tool_name": "Bash",
                          "tool_input": {"command": "python3 -c 'print(1)'"},
                          "session_id": "s", "cwd": str(elsewhere), "tool_use_id": "t"})
    env = {"PATH": os.pathsep.join([str(py_dir), "/usr/bin", "/bin"]), "HOME": str(tmp_path),
           "LYPNING_LOG": str(log), "LYPNING_PYTHONPATH": str(src),
           "CLAUDE_PROJECT_DIR": str(elsewhere)}
    proc = subprocess.run(["sh", str(HOOK_SH)], input=payload, capture_output=True,
                          text=True, env=env, timeout=120)
    assert proc.returncode == 0 and proc.stdout == capture.OK_RESPONSE + "\n"
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1, "exactly one arm logs"
    assert records[0]["tool_use_id"] == "t"

    # And the same call without the pin is inert, unless that interpreter can
    # import the package on its own — in which case the bare arm is why.
    log.unlink()
    del env["LYPNING_PYTHONPATH"]
    probe = subprocess.run([str(py_dir / "python3"), "-c", "import lypning"],
                           capture_output=True, env=env, cwd=str(elsewhere), timeout=60)
    proc = subprocess.run(["sh", str(HOOK_SH)], input=payload, capture_output=True,
                          text=True, env=env, timeout=120)
    assert proc.returncode == 0 and proc.stdout == capture.OK_RESPONSE + "\n"
    if probe.returncode != 0:
        assert not log.exists()


def test_every_hook_carries_the_pinned_tree_arm():
    """The same arm in all three scripts, guarded on the package file."""
    missing = [h.name for h in sorted(paths.HOOKS_SRC.glob("lypning-*.sh"))
               if '[ -f "$LYPNING_PYTHONPATH/lypning/__init__.py" ]'
               not in h.read_text(encoding="utf-8")
               or 'PYTHONPATH="$LYPNING_PYTHONPATH' not in h.read_text(encoding="utf-8")]
    assert not missing, missing


# --- the Stop roll-up belongs to a checkout of lypning -----------------------


def _seed_log():
    paths.ensure_dir(paths.log_path().parent)
    paths.log_path().write_text(
        json.dumps({"kind": "python_invocation",
                    "program": "print('stop-guard-probe-4d1c', 6 * 7)",
                    "session": "sess-guard", "ts": "2026-09-22T00:00:00.000Z"}) + "\n",
        encoding="utf-8")


@pytest.mark.parametrize("hook,key", [(capture.hook_stop, "cwd"),
                                      (capture.hook_openhands_session_end, "working_dir")],
                         ids=["stop", "openhands-session-end"])
def test_the_roll_up_writes_nothing_into_someone_elses_repository(project, hook, key):
    """A user-scope Stop hook fires in every repository the user opens.

    Invariant 7: exporting there would add ``tests/corpus/sightings`` files to
    a repository that never asked for them. An install older than the scoped
    ``install.HOOKS`` may still have Stop registered at user scope, so the
    guard is in the entry point, not only in the installer.
    """
    (project / ".git").mkdir()  # a repository, just not ours
    _seed_log()
    before = sorted(p.relative_to(project) for p in project.rglob("*"))
    rc, out = _fire(hook, {key: str(project)})
    assert rc == 0 and out == capture.OK_RESPONSE + "\n"
    assert not paths.sightings_dir(project).exists()
    assert sorted(p.relative_to(project) for p in project.rglob("*")) == before


def test_the_roll_up_still_exports_in_a_checkout_of_lypning(project):
    (project / "src" / "lypning").mkdir(parents=True)
    (project / "src" / "lypning" / "__init__.py").write_text("", encoding="utf-8")
    assert capture.is_lypning_checkout(project)
    _seed_log()
    rc, out = _fire(capture.hook_stop, {"cwd": str(project)})
    assert rc == 0 and out == capture.OK_RESPONSE + "\n"
    files = list(paths.sightings_dir(project).glob("*.jsonl"))
    assert files and "stop-guard-probe-4d1c" in files[0].read_text(encoding="utf-8")


def test_is_lypning_checkout_needs_the_package_file(tmp_path):
    assert not capture.is_lypning_checkout(None)
    assert not capture.is_lypning_checkout(tmp_path)
    (tmp_path / "src" / "lypning").mkdir(parents=True)
    assert not capture.is_lypning_checkout(tmp_path)


# --- rule 5 runs inside a blocking hook: it must stay linear ------------------


@pytest.mark.parametrize("command", [
    # A long line of `>x.py` fragments after a heredoc, no heredoc on that line:
    # as one regex, every fragment rescanned the rest of the line (23 s at
    # 190 KB before the rule became two per-line searches).
    "cat <<EOF\n" + ">a.py " * 64000,
    # Many heredoc operators on one line, and no target on it.
    "<<a " * 64000,
    # Both on the line, but never a target ending in exactly `.py`.
    "cat <<EOF " + "<<a >b.pyc " * 32000,
    # Whitespace after `tee`, which `tee\s+…\s*` backtracked over quadratically.
    "cat <<EOF | tee" + " " * 200000 + "x.pyc",
], ids=["targets-after-heredoc", "many-heredocs", "both-no-exact-target", "tee-whitespace"])
def test_rule_five_is_linear_on_adversarial_lines(command):
    import time

    t0 = time.perf_counter()
    assert not capture.looks_pythonish(command)
    # Generous: linear is milliseconds here; the quadratic form was tens of
    # seconds, so this cannot flake on a loaded runner and still catches it.
    assert time.perf_counter() - t0 < 2.0


def test_the_opencode_screen_carries_rule_five():
    """The plugin's screens are a port of PYTHONISH; a write-then-run heredoc
    it drops is lost under opencode exactly as it was under Claude Code."""
    js = (paths.OPENCODE_ASSETS / "lypning.js").read_text(encoding="utf-8")
    assert "const PY_TARGET = /" in js and "const HEREDOC_OP = /" in js
    assert "HEREDOC_OP.test(l) && PY_TARGET.test(l)" in js
