"""The opencode adapter: one file we own, and nothing else touched.

Two kinds of test here, and the second kind is the interesting one. The Python
half — plan, apply, uninstall — is testable directly. The JavaScript half runs
inside Bun, where pytest cannot reach it, and its failure modes are all silent:
a second export makes the loader throw, a reassigned hook output is dropped with
no error at all, and a hook name that is declared but never dispatched simply
never fires. Those are pinned by grepping the shipped asset, which is the only
instrument available.
"""

from __future__ import annotations

import json
import re

import pytest

from lypning import install, paths
from lypning.harness import opencode


def _plugin_text():
    return (paths.OPENCODE_ASSETS / "lypning.js").read_text(encoding="utf-8")


def _plugin_code():
    """The plugin with its comments removed.

    The header documents the mutation rule by quoting the wrong forms
    (`output.args = {...}`), which is exactly what the greps below look for. A
    test that could not tell prose from code would forbid the file from
    explaining itself.
    """
    out = []
    for line in _plugin_text().splitlines():
        stripped = line.lstrip()
        if stripped.startswith("//"):
            continue
        out.append(line.split("  //")[0])
    return "\n".join(out)


def test_plan_writes_nothing(project):
    plan = opencode.plan(project, shim=False)
    assert [a for a in plan.actions if a.kind == "write"]
    assert not opencode.config_root(project).exists()


def test_install_uninstall_round_trip(project):
    before = sorted(p for p in project.rglob("*") if p.is_file())
    install.apply(opencode.plan(project, shim=False))
    dest = opencode.plugin_path(project)
    assert dest.is_file() and opencode.is_ours(dest)

    opencode.uninstall(project)
    assert not dest.exists()
    after = sorted(p for p in project.rglob("*") if p.is_file())
    assert before == after


def test_a_foreign_lypning_js_is_refused_without_force(project):
    dest = opencode.plugin_path(project)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("// somebody else's plugin\n", encoding="utf-8")
    original = dest.read_bytes()

    plan = opencode.plan(project, shim=False)
    refusals = [a for a in plan.notes if a.path == dest]
    assert refusals, "a foreign file at our name must be refused, loudly"
    assert "force" in refusals[0].note
    # And it must NOT read as "already in place" — that is the one summary a
    # reader takes as "fine".
    assert dest not in [a.path for a in plan.already]
    assert dest.read_bytes() == original


def test_uninstall_leaves_opencodes_own_files_alone(project):
    install.apply(opencode.plan(project, shim=False))
    root = opencode.config_root(project)
    (root / "package.json").write_text("{}", encoding="utf-8")
    (root / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    (root / "node_modules").mkdir(exist_ok=True)
    (root / "plugin" / "somebody-else.js").write_text("//\n", encoding="utf-8")

    opencode.uninstall(project)

    assert (root / "package.json").is_file()
    assert (root / ".gitignore").is_file()
    assert (root / "node_modules").is_dir()
    assert (root / "plugin" / "somebody-else.js").is_file()


def test_uninstall_leaves_a_foreign_file_at_our_name_alone(project):
    dest = opencode.plugin_path(project)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("// not ours\n", encoding="utf-8")
    opencode.uninstall(project)
    assert dest.is_file() and dest.read_text() == "// not ours\n"


def test_the_dry_run_says_what_it_cannot_undo(project):
    """opencode fills any config directory it scans. If we created it, say so.

    A cost we cause and cannot reverse belongs in the plan the user reads
    before agreeing to it, not in a surprise afterwards.
    """
    plan = opencode.plan(project, shim=False)
    notes = " ".join(a.note for a in plan.notes)
    assert "node_modules" in notes


def test_status_is_json_serialisable(project):
    json.dumps(opencode.status(project))


# --- the shipped plugin, pinned by grep --------------------------------------


def test_the_plugin_has_exactly_one_export():
    """opencode's loader iterates EVERY export and requires each to be a plugin
    function, throwing on the first that is not. A second export is a plugin
    that does not load — inside Bun, where nothing here can see it."""
    assert len(re.findall(r"^export\b", _plugin_text(), re.M)) == 1


def test_the_plugin_never_reassigns_a_hook_output():
    """Assigning a field propagates; replacing the container is dropped with no
    error and no warning. Only one of those is a bug you would ever find."""
    code = _plugin_code()
    assert not re.search(r"output\.args\s*=[^=]", code)
    assert not re.search(r"output\.env\s*=[^=]", code)


def test_the_plugin_does_not_implement_a_hook_that_is_never_dispatched():
    """`permission.ask` is declared in the plugin type and dispatched nowhere.
    A capture plugin built on it would do nothing, silently, forever."""
    assert "permission.ask" not in _plugin_code()


def test_the_plugin_matches_the_verified_tool_id():
    """The exposed tool id is `bash`; opencode pins it that way for plugin
    compatibility. Matching only `shell` observes nothing."""
    text = _plugin_text()
    assert '"bash"' in text


def test_the_plugin_ships_no_router():
    """Routing is `lypning run`, and it is not shipped here (docs/HARNESSES.md).

    A rewrite of the command would be a second implementation of the exit-90
    fall-through contract, which is the one thing in this package that has only
    ever broken silently.
    """
    assert not re.search(r"output\.args\.command\s*=", _plugin_code())


def test_the_routing_paragraph_has_not_drifted():
    """One measured paragraph, not three variants that drifted apart."""
    baked = _plugin_text()
    shipped = paths.ROUTING_PROMPT.read_text(encoding="utf-8").strip()
    for line in shipped.splitlines():
        line = line.strip()
        if line:
            assert line in baked, "the plugin's copy is missing: %r" % line


def test_the_plugin_writes_the_record_shape_harvest_reads(tmp_path):
    """The grep proves the keys are spelled; feeding a line of that exact shape
    through the harvester proves they are the ones it reads."""
    from lypning import harvest

    text = _plugin_text()
    for key in ("kind", "ts", "session", "cwd", "command", "host"):
        assert '"%s"' % key in text or "%s:" % key in text
    assert "bash_command" in text

    log = tmp_path / "invocations.jsonl"
    log.write_text(json.dumps({
        "kind": "bash_command", "ts": "2026-09-02T00:00:00.000Z",
        "session": "ses_1", "cwd": "/w", "tool": "bash",
        "command": "python3 -c 'print(1)'", "description": None,
        "transcript": None, "host": "opencode", "run": "call_1",
    }) + "\n", encoding="utf-8")
    sightings = harvest.parse_log(log)
    assert len(sightings) == 1
    assert sightings[0].program == "print(1)"


def test_the_plugin_marker_is_near_the_top():
    """Ownership is decided from the file's own head, so the marker has to be
    inside the window `is_ours` reads."""
    head = _plugin_text().splitlines()[:opencode.MARKER_LINES]
    assert any(opencode.PLUGIN_MARKER in line for line in head)


def test_a_plugin_note_reaches_the_python_gates(tmp_path):
    """The only route by which a failure inside Bun becomes visible.

    The plugin's PATH self-assertion writes a `{"kind":"note"}` record when the
    shim is not reached. Nothing in Python can see that happen, so `doctor`
    reads it back out of the log — and must ignore a stale one, or a problem
    fixed a month ago warns forever.
    """
    import datetime

    from lypning import cli

    now = datetime.datetime.now(datetime.timezone.utc)
    fresh = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    stale = (now - datetime.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    log = tmp_path / "log.jsonl"

    log.write_text(json.dumps({"kind": "note", "ts": fresh, "host": "opencode",
                               "detail": "PATH shim not reached"}) + "\n",
                   encoding="utf-8")
    assert "PATH shim not reached" in (cli._recent_capture_note(str(log)) or "")

    log.write_text(json.dumps({"kind": "note", "ts": stale, "host": "opencode",
                               "detail": "PATH shim not reached"}) + "\n",
                   encoding="utf-8")
    assert cli._recent_capture_note(str(log)) is None

    assert cli._recent_capture_note(str(tmp_path / "nope.jsonl")) is None
    assert cli._recent_capture_note(None) is None
