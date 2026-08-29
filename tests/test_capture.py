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
