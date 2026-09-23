"""Wiring into a Claude Code project — and the merge that must cost nothing.

``.claude/settings.json`` is a file the user already owns and has opinions
about. The tests here are all one assertion in different clothes: everything
that was in the file before is still in it afterwards, in the same order, and
uninstall removes exactly what install added.
"""

from __future__ import annotations

import json

import pytest

from lypning import install, paths

FOREIGN_SETTINGS = {
    "model": "opusmagnum",
    "permissions": {"allow": ["Bash(ls:*)"]},
    "hooks": {
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "sh ./audit.sh"}]},
        ],
        "Notification": [
            {"hooks": [{"type": "command", "command": "say hi"}]},
        ],
    },
}


@pytest.fixture
def settings_path(project):
    p = project / ".claude" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _write(path, obj):
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def _commands(settings, event):
    out = []
    for group in (settings.get("hooks") or {}).get(event, []):
        out.extend(h.get("command", "") for h in group.get("hooks", []))
    return out


def test_plan_writes_nothing(project, settings_path):
    _write(settings_path, FOREIGN_SETTINGS)
    before = settings_path.read_bytes()
    plan = install.plan_install(project, shim=False)
    assert plan.changes
    assert plan.diff and any(line.startswith("+") for line in plan.diff)
    assert settings_path.read_bytes() == before
    assert not (project / ".claude" / "skills").exists()


def test_install_uninstall_round_trip(project, settings_path):
    _write(settings_path, FOREIGN_SETTINGS)
    original = json.loads(settings_path.read_text(encoding="utf-8"))

    install.install(project, shim=False)
    root = project / ".claude"
    assert (root / "skills" / "lypning" / "SKILL.md").is_file()
    # The installed skill is the shipped one, file for file — nothing more (a
    # stale copy of a deleted document would be read by every agent session).
    assert sorted(p.name for p in (root / "skills" / "lypning").iterdir()) == \
        sorted(p.name for p in paths.SKILL_SRC.iterdir())
    assert sorted(p.name for p in (root / "hooks").glob("*.sh"))
    after = json.loads(settings_path.read_text(encoding="utf-8"))
    assert any("lypning" in c for c in _commands(after, "PreToolUse"))

    install.uninstall(project)
    assert not (root / "skills" / "lypning").exists()
    assert list((root / "hooks").glob("lypning*.sh")) == []
    restored = json.loads(settings_path.read_text(encoding="utf-8"))
    assert restored == original


def test_the_merge_preserves_unrelated_keys_and_hooks(project, settings_path):
    _write(settings_path, FOREIGN_SETTINGS)
    install.install(project, shim=False)
    after = json.loads(settings_path.read_text(encoding="utf-8"))

    assert after["model"] == "opusmagnum"
    assert after["permissions"] == {"allow": ["Bash(ls:*)"]}
    assert _commands(after, "Notification") == ["say hi"]
    # Append-only: the user's entry keeps its index, ours goes after it.
    assert _commands(after, "PreToolUse")[0] == "sh ./audit.sh"
    assert len(_commands(after, "PreToolUse")) == 2
    # Top-level insertion order is where the user put it.
    assert list(after.keys())[:3] == ["model", "permissions", "hooks"]


def test_the_original_settings_are_backed_up_once(project, settings_path):
    _write(settings_path, FOREIGN_SETTINGS)
    original = settings_path.read_bytes()
    backup = settings_path.with_name(settings_path.name + install.SETTINGS_BACKUP_SUFFIX)

    install.install(project, shim=False)
    assert backup.read_bytes() == original

    settings_path.write_text(json.dumps({"model": "later"}) + "\n", encoding="utf-8")
    install.install(project, shim=False)
    # The pristine original is the thing worth keeping, not the last state.
    assert backup.read_bytes() == original


def test_installing_twice_changes_nothing_the_second_time(project, settings_path):
    _write(settings_path, FOREIGN_SETTINGS)
    install.install(project, shim=False)
    body = settings_path.read_bytes()
    actions = install.install(project, shim=False)
    assert settings_path.read_bytes() == body
    assert any(a.component == "settings" and a.kind == "skip" for a in actions)


def test_unparseable_settings_are_left_exactly_as_found(project, settings_path):
    settings_path.write_text("{ this is not json", encoding="utf-8")
    actions = install.install(project, shim=False)
    assert settings_path.read_text(encoding="utf-8") == "{ this is not json"
    assert any(a.component == "settings" and a.kind == "skip" and "not valid JSON" in a.note
               for a in actions)


def test_uninstall_removes_only_our_entries(project, settings_path):
    _write(settings_path, FOREIGN_SETTINGS)
    install.install(project, shim=False)
    install.uninstall(project)
    after = json.loads(settings_path.read_text(encoding="utf-8"))
    assert _commands(after, "PreToolUse") == ["sh ./audit.sh"]
    assert _commands(after, "Notification") == ["say hi"]
    assert after["model"] == "opusmagnum"


def test_uninstall_drops_an_event_array_it_emptied_but_never_the_file(project, settings_path):
    _write(settings_path, {"model": "x"})
    install.install(project, shim=False)
    assert "Stop" in json.loads(settings_path.read_text(encoding="utf-8"))["hooks"]
    install.uninstall(project)
    after = json.loads(settings_path.read_text(encoding="utf-8"))
    assert after["hooks"] == {}
    assert after["model"] == "x"
    assert settings_path.is_file()


# --- the merge as a pure function -------------------------------------------

ENTRIES = [("PreToolUse", "Bash", "lypning hook pre-tool-use"),
           ("Stop", None, "lypning hook stop")]


def test_merge_hooks_is_idempotent():
    once, added = install.merge_hooks({}, ENTRIES)
    assert added == [e[2] for e in ENTRIES]
    twice, added_again = install.merge_hooks(once, ENTRIES)
    assert added_again == []
    assert twice == once


def test_merge_hooks_does_not_mutate_the_input():
    settings = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}}
    snapshot = json.dumps(settings, sort_keys=True)
    install.merge_hooks(settings, ENTRIES)
    assert json.dumps(settings, sort_keys=True) == snapshot


def test_merge_hooks_leaves_a_shape_it_does_not_understand_strictly_alone():
    # Some other tool's spelling of `hooks`. Never rewrite what we cannot read.
    settings = {"hooks": "enabled"}
    out, added = install.merge_hooks(settings, ENTRIES)
    assert out == settings and added == []

    settings = {"hooks": {"Stop": "yes"}}
    out, added = install.merge_hooks(settings, ENTRIES)
    assert out["hooks"]["Stop"] == "yes"
    assert "lypning hook stop" not in added


def test_strip_hooks_keeps_a_group_that_was_already_empty():
    settings = {"hooks": {"Stop": [{"hooks": []},
                                   {"hooks": [{"type": "command", "command": "lypning hook stop"}]}]}}
    out, removed = install.strip_hooks(settings)
    assert removed == ["lypning hook stop"]
    assert out["hooks"]["Stop"] == [{"hooks": []}]


# --- the plan's summary line --------------------------------------------------


def test_a_refusal_is_not_summarised_as_already_in_place(project, settings_path):
    """"N changes, M already in place" is the line a reader takes as "fine".

    A settings file that does not parse is a skip, and counting it there says
    the hooks are wired when they are not.
    """
    settings_path.write_text("{ this is not json", encoding="utf-8")
    plan = install.plan_install(project, shim=False)
    assert plan.already == []
    assert len(plan.notes) == 1
    text = install.render_plan(plan)
    assert "0 already in place" in text
    assert "1 warning" in text


def test_a_second_install_reports_the_files_as_already_in_place(project, settings_path):
    _write(settings_path, FOREIGN_SETTINGS)
    install.install(project, shim=False)
    plan = install.plan_install(project, shim=False)
    assert plan.changes == []
    assert plan.already and plan.notes == []
    assert "already in place" in install.render_plan(plan)
    assert "warning" not in install.render_plan(plan)


# --- user scope: capture everywhere, and nothing else anywhere ---------------


@pytest.fixture
def user_settings(tmp_path):
    """``~/.claude/settings.json`` under the test's ``$HOME`` (conftest)."""
    p = install.claude_dir(None, "user") / "settings.json"
    assert str(p).startswith(str(tmp_path)), "user scope must resolve under the test HOME"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def reachable(monkeypatch):
    """A user-scope hook that some arm reaches — without spawning python3."""
    monkeypatch.setattr(install, "dispatch_arms", lambda pythonpath=None: ["stub arm"])


def _events(settings):
    return sorted((settings.get("hooks") or {}).keys())


def test_user_scope_registers_the_bash_capture_hook_and_nothing_else(
        project, user_settings, reachable):
    """A user-scope hook fires in every repository the user opens.

    Only PreToolUse(Bash) is harmless there — it appends to our own log,
    outside every repository. Stop would write sightings into whatever
    repository the session is in; SessionStart would write a shim and inject
    lypning's engine state into every unrelated session.
    """
    install.install(project, scope="user", shim=False, skill=False)
    after = json.loads(user_settings.read_text(encoding="utf-8"))
    assert _events(after) == ["PreToolUse"]
    [group] = after["hooks"]["PreToolUse"]
    assert group["matcher"] == "Bash"
    assert [h["command"] for h in group["hooks"]] == [
        'sh "$HOME/.claude/hooks/lypning-capture.sh"']
    # Only the script the entries name is copied: an unregistered Stop script
    # in ~/.claude/hooks is an invitation to wire it by hand.
    assert sorted(p.name for p in (user_settings.parent / "hooks").glob("*.sh")) == [
        "lypning-capture.sh"]
    # And the project was not touched at all.
    assert not (project / ".claude").exists()


def test_project_scope_still_registers_all_three(project, settings_path):
    install.install(project, shim=False)
    after = json.loads(settings_path.read_text(encoding="utf-8"))
    assert _events(after) == ["PreToolUse", "SessionStart", "Stop"]


def test_the_scopes_field_is_what_decides(monkeypatch):
    entries = install.hook_entries("user", ["lypning-capture.sh", "lypning-harvest.sh",
                                            "lypning-session-start.sh"])
    assert [(e, m) for e, m, _c in entries] == [("PreToolUse", "Bash")]
    assert [spec.event for spec in install.HOOKS if "user" in spec.scopes] == ["PreToolUse"]


def test_user_scope_round_trip_is_exact_and_backs_up_once(project, user_settings, reachable):
    _write(user_settings, FOREIGN_SETTINGS)
    original_bytes = user_settings.read_bytes()
    original = json.loads(user_settings.read_text(encoding="utf-8"))
    backup = user_settings.with_name(user_settings.name + install.SETTINGS_BACKUP_SUFFIX)

    plan = install.plan_install(project, scope="user", shim=False, skill=False)
    assert user_settings.read_bytes() == original_bytes, "a plan writes nothing"
    assert not backup.exists()
    assert any(line.startswith("+") and "lypning-capture.sh" in line for line in plan.diff)

    install.install(project, scope="user", shim=False, skill=False)
    assert backup.read_bytes() == original_bytes
    after = json.loads(user_settings.read_text(encoding="utf-8"))
    assert _commands(after, "PreToolUse")[0] == "sh ./audit.sh"
    assert after["model"] == "opusmagnum"

    again = install.plan_install(project, scope="user", shim=False, skill=False)
    assert again.changes == []
    assert "the hook entry is already present" in install.render_plan(again)

    install.uninstall(project, scope="user")
    assert json.loads(user_settings.read_text(encoding="utf-8")) == original
    assert backup.read_bytes() == original_bytes, "uninstall never touches the backup"
    assert list((user_settings.parent / "hooks").glob("lypning*.sh")) == []


def test_an_older_user_install_is_reported_not_rewritten(project, user_settings, reachable):
    """Stop/SessionStart left at user scope by an install older than the
    scopes field: named in the plan, never removed by an install (append-only)
    — uninstall is the exact tool for that."""
    old = {"hooks": {"Stop": [{"hooks": [{"type": "command",
                                          "command": 'sh "$HOME/.claude/hooks/lypning-harvest.sh"'}]}]}}
    _write(user_settings, old)
    plan = install.plan_install(project, scope="user", shim=False, skill=False)
    stale = [a for a in plan.notes if "older install" in a.note]
    assert len(stale) == 1 and "Stop" in stale[0].note and "--user" in stale[0].note
    install.apply(plan)
    after = json.loads(user_settings.read_text(encoding="utf-8"))
    assert _events(after) == ["PreToolUse", "Stop"]


# --- INERT: a user-scope hook no arm can reach --------------------------------


def test_a_user_hook_no_arm_reaches_is_reported_inert(project, user_settings, monkeypatch):
    """The failure invariant 5 makes silent: the hook answers, exits 0, and
    records nothing, in every repository, forever. The plan must say so, the
    same way it says a shim is not on PATH."""
    monkeypatch.setattr(install, "dispatch_arms", lambda pythonpath=None: [])
    plan = install.plan_install(project, scope="user", shim=False, skill=False)
    inert = [a for a in plan.notes if "INERT" in a.note]
    assert len(inert) == 1
    assert "uv tool install" in inert[0].note and "LYPNING_PYTHONPATH" in inert[0].note
    assert "1 warning" in install.render_plan(plan)

    install.apply(plan)
    st = install.status(project)
    assert st["scopes"]["user"]["inert"] is True
    assert st["scopes"]["project"]["inert"] is False
    assert "reach    : INERT" in install.render_status(st)


def test_a_reachable_user_hook_is_not_reported_inert(project, user_settings, reachable):
    plan = install.plan_install(project, scope="user", shim=False, skill=False)
    assert not [a for a in plan.actions if "INERT" in a.note]
    install.apply(plan)
    st = install.status(project)
    assert st["scopes"]["user"]["inert"] is False
    assert st["scopes"]["user"]["reach"] == ["stub arm"]


def test_project_scope_never_asks_whether_it_is_inert(project, monkeypatch):
    def boom(pythonpath=None):
        raise AssertionError("a project install must not probe the user-scope arms")
    monkeypatch.setattr(install, "dispatch_arms", boom)
    install.plan_install(project, shim=False)


def test_dispatch_arms_tells_the_console_script_from_the_rust_core(tmp_path, monkeypatch):
    """The name ``lypning`` can resolve to the engine binary, which the
    script's first arm cannot use — only a ``#!`` console script counts."""
    monkeypatch.setattr(install, "_imports_lypning", lambda python: False)
    monkeypatch.delenv("LYPNING_PYTHONPATH", raising=False)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "lypning"
    exe.write_bytes(b"\xcf\xfa\xed\xfe not a script")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    assert install.dispatch_arms() == []
    exe.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    assert install.dispatch_arms() == ["lypning on PATH (%s)" % exe]

    src = tmp_path / "src"
    (src / "lypning").mkdir(parents=True)
    (src / "lypning" / "__init__.py").write_text("", encoding="utf-8")
    exe.unlink()
    assert install.dispatch_arms(str(src)) == ["LYPNING_PYTHONPATH (%s)" % src]
    monkeypatch.setenv("LYPNING_PYTHONPATH", str(src))
    assert install.dispatch_arms() == ["LYPNING_PYTHONPATH (%s)" % src]


# --- pinning a source tree -------------------------------------------------------


def test_a_pinned_tree_is_on_the_command_and_uninstall_still_removes_it(
        project, user_settings, tmp_path):
    src = tmp_path / "checkout" / "src"
    (src / "lypning").mkdir(parents=True)
    (src / "lypning" / "__init__.py").write_text("", encoding="utf-8")
    _write(user_settings, {"model": "x"})

    plan = install.plan_install(project, scope="user", shim=False, skill=False,
                                pythonpath=str(src))
    assert not [a for a in plan.actions if "INERT" in a.note], "the pin is an arm"
    install.apply(plan)
    after = json.loads(user_settings.read_text(encoding="utf-8"))
    assert _commands(after, "PreToolUse") == [
        "LYPNING_PYTHONPATH=%s sh \"$HOME/.claude/hooks/lypning-capture.sh\"" % src.resolve()]
    install.uninstall(project, scope="user")
    assert json.loads(user_settings.read_text(encoding="utf-8")) == {"model": "x", "hooks": {}}


def test_a_pin_that_holds_no_package_is_refused(project, user_settings, tmp_path, reachable):
    plan = install.plan_install(project, scope="user", shim=False, skill=False,
                                pythonpath=str(tmp_path / "empty"))
    assert plan.pythonpath is None
    assert any("not pinned" in a.note for a in plan.notes)
    assert not any("LYPNING_PYTHONPATH=" in line for line in plan.diff)


def test_a_repin_never_registers_the_capture_hook_twice(project, user_settings, tmp_path,
                                                        reachable):
    """Pinned and unpinned are one hook. Registered as two, one Bash call fires
    the capture script twice and every program is logged — and counted — twice.
    """
    src = tmp_path / "checkout" / "src"
    (src / "lypning").mkdir(parents=True)
    (src / "lypning" / "__init__.py").write_text("", encoding="utf-8")
    install.install(project, scope="user", shim=False, skill=False)
    first = user_settings.read_bytes()

    plan = install.plan_install(project, scope="user", shim=False, skill=False,
                                pythonpath=str(src))
    assert plan.changes == [] and plan.diff == []
    assert any("already registered without a pin" in a.note for a in plan.notes)
    install.apply(plan)
    assert user_settings.read_bytes() == first
    after = json.loads(first)
    assert len(_commands(after, "PreToolUse")) == 1

    # And the other way round: pinned first, then a plain install.
    install.uninstall(project, scope="user")
    install.install(project, scope="user", shim=False, skill=False, pythonpath=str(src))
    pinned = user_settings.read_bytes()
    install.install(project, scope="user", shim=False, skill=False)
    assert user_settings.read_bytes() == pinned


def test_status_asks_about_the_registered_pin_not_the_shell(project, user_settings, tmp_path,
                                                            monkeypatch):
    """A pinned install reaches the package whatever the asking shell exports;
    reporting it INERT would send the operator to fix a working hook."""
    src = tmp_path / "checkout" / "src"
    (src / "lypning").mkdir(parents=True)
    (src / "lypning" / "__init__.py").write_text("", encoding="utf-8")
    install.install(project, scope="user", shim=False, skill=False, pythonpath=str(src))
    monkeypatch.delenv("LYPNING_PYTHONPATH", raising=False)
    monkeypatch.setattr(install, "_imports_lypning", lambda python: False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    st = install.status(project)
    assert st["scopes"]["user"]["inert"] is False
    assert st["scopes"]["user"]["reach"] == ["LYPNING_PYTHONPATH (%s)" % src.resolve()]
    # The plan asks the same question the same way.
    again = install.plan_install(project, scope="user", shim=False, skill=False)
    assert not [a for a in again.actions if "INERT" in a.note]


def test_a_pin_with_a_quote_in_it_round_trips():
    cmd = install._hook_command("user", "lypning-capture.sh", "x", "/a b/it's/src")
    assert install._pin_of(cmd) == "/a b/it's/src"
    assert install._unpinned(cmd) == install._hook_command("user", "lypning-capture.sh", "x")
