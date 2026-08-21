"""Wiring into a Claude Code project — and the merge that must cost nothing.

``.claude/settings.json`` is a file the user already owns and has opinions
about. The tests here are all one assertion in different clothes: everything
that was in the file before is still in it afterwards, in the same order, and
uninstall removes exactly what install added.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from lypning import install

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


# --- collect-only: the install a repository that is not this one will accept ---
#
# Everything below is one claim in three clothes: a collect-only install wires
# the two capture hooks and NOTHING else, the directory it publishes into
# survives the trip through a shell, and uninstall still finds it. That last one
# is the regression worth the most: the LYPNING_SIGHTINGS prefix changes the
# command string, and the command string is the only thing uninstall keys on.

#: A space and a single quote, which is what a real user's directory looks like
#: on the one machine nobody tested on. Never written to — only quoted.
WEIRD_SIGHTINGS = "/var/it's a repo/published programs"


def _ours(settings, event):
    return [c for c in _commands(settings, event) if "lypning" in c.lower()]


def test_hook_entries_collect_only_keeps_only_the_collecting_specs():
    every_script = [name for spec in install.HOOKS for name in spec.scripts]
    full = install.hook_entries("project", every_script)
    only = install.hook_entries("project", every_script, collect_only=True)

    assert [event for event, _, _ in full] == [spec.event for spec in install.HOOKS]
    # PreToolUse records, Stop publishes. SessionStart installs the shim, which
    # is engine wiring — the one spec collect-only drops, and the reason the
    # field is on HookSpec rather than a name check here.
    assert [event for event, _, _ in only] == ["PreToolUse", "Stop"]
    assert [spec.event for spec in install.HOOKS if not spec.collect] == ["SessionStart"]


def test_collect_only_plans_no_shim_and_no_skill(project, settings_path):
    _write(settings_path, FOREIGN_SETTINGS)
    plan = install.plan_install(project, collect_only=True)
    # shim=True and skill=True are the defaults and are passed here on purpose:
    # off is what collect-only MEANS, not something the caller has to remember.
    assert [a for a in plan.actions if a.component == "shim"] == []
    assert [a for a in plan.actions if a.component == "skill"] == []
    assert plan.collect_only is True
    assert plan.changes


def test_collect_only_wires_the_two_capture_hooks_and_leaves_no_shim_behind(
        project, settings_path):
    _write(settings_path, FOREIGN_SETTINGS)
    install.install(project, collect_only=True)
    after = json.loads(settings_path.read_text(encoding="utf-8"))

    assert len(_ours(after, "PreToolUse")) == 1
    assert len(_ours(after, "Stop")) == 1
    assert "SessionStart" not in after["hooks"]
    assert not (project / ".claude" / "skills").exists()
    # A session-start script sitting in a tree where no hook command names it
    # reads as a shim install that failed rather than one never asked for.
    assert sorted(p.name for p in (project / ".claude" / "hooks").glob("*.sh")) == [
        "lypning-capture.sh", "lypning-harvest.sh"]


def test_the_sightings_prefix_survives_a_space_and_a_single_quote():
    """Two layers of escaping, and only the JSON one is done for us.

    The value goes into a hook command that a shell will parse, so a directory
    with a space in it would otherwise become a command plus an argument nobody
    asked for. Asserted through a real ``sh``, because shell quoting is only
    ever wrong in the case nobody tried by hand.
    """
    prefix = "LYPNING_SIGHTINGS=%s " % install.sh_quote(WEIRD_SIGHTINGS)
    for _, _, command in install.hook_entries("project", [], sightings=WEIRD_SIGHTINGS):
        assert command.startswith(prefix)
        assert command[len(prefix):].strip()  # the hook itself is still there

    # The prefix in front of a CHILD, which is the shape a hook command has:
    # an assignment before a command exports to that command's environment, and
    # the hook is `sh "…/lypning-harvest.sh"`.
    echoed = subprocess.run(
        ["sh", "-c", prefix + '''sh -c 'printf %s "$LYPNING_SIGHTINGS"' '''],
        capture_output=True, text=True)
    assert echoed.returncode == 0
    assert echoed.stdout == WEIRD_SIGHTINGS


def test_no_sightings_argument_leaves_the_command_exactly_as_it_was():
    """The default has to be byte-identical, or every install churns the file."""
    plain = install.hook_entries("project", [])
    for blank in (None, "", "   "):
        assert install.hook_entries("project", [], sightings=blank) == plain


def test_the_prefixed_command_is_what_lands_in_settings_json(project, settings_path):
    _write(settings_path, FOREIGN_SETTINGS)
    install.install(project, collect_only=True, sightings=WEIRD_SIGHTINGS)
    after = json.loads(settings_path.read_text(encoding="utf-8"))
    stop = _ours(after, "Stop")
    assert len(stop) == 1
    assert stop[0].startswith("LYPNING_SIGHTINGS=%s " % install.sh_quote(WEIRD_SIGHTINGS))


def test_uninstall_removes_a_collect_only_install_completely(project, settings_path):
    """The prefix changes the string uninstall keys on. It must still key.

    `LYPNING_SIGHTINGS` contains the mark itself, so a prefixed command matches
    twice over rather than falling off the end of uninstall — but that is a
    property of the spelling, and the spelling is exactly the kind of thing a
    later change breaks without breaking anything that fails loudly.
    """
    _write(settings_path, FOREIGN_SETTINGS)
    original = json.loads(settings_path.read_text(encoding="utf-8"))

    install.install(project, collect_only=True, sightings=WEIRD_SIGHTINGS)
    wired = json.loads(settings_path.read_text(encoding="utf-8"))
    assert _ours(wired, "Stop") and _ours(wired, "PreToolUse")

    install.uninstall(project)
    restored = json.loads(settings_path.read_text(encoding="utf-8"))
    assert restored == original
    assert "lypning" not in json.dumps(restored).lower()
    assert list((project / ".claude" / "hooks").glob("lypning*.sh")) == []


def test_switching_install_shape_never_leaves_two_hooks_that_both_harvest(
        project, settings_path):
    """One Stop hook, whichever order the two shapes were installed in.

    Two would both publish, to two different directories, on every turn
    boundary — and nothing downstream could say which one the evidence went to:
    `status` lists both, uninstall removes both, so the duplication is silent
    for exactly as long as it takes someone to wonder where their programs are.
    """
    _write(settings_path, FOREIGN_SETTINGS)
    install.install(project, shim=False)
    install.install(project, collect_only=True, sightings=WEIRD_SIGHTINGS)
    after = json.loads(settings_path.read_text(encoding="utf-8"))

    assert len(_ours(after, "Stop")) == 1, "two Stop hooks would both harvest"
    assert len(_ours(after, "PreToolUse")) == 1
    assert _ours(after, "Stop")[0].startswith("LYPNING_SIGHTINGS=")

    # And back the other way: the last install wins, in both directions.
    install.install(project, shim=False)
    back = json.loads(settings_path.read_text(encoding="utf-8"))
    assert len(_ours(back, "Stop")) == 1
    assert len(_ours(back, "PreToolUse")) == 1
    assert "LYPNING_SIGHTINGS" not in _ours(back, "Stop")[0]

    install.uninstall(project)
    assert "lypning" not in settings_path.read_text(encoding="utf-8").lower()


def test_a_collect_only_merge_still_costs_the_user_nothing(project, settings_path):
    _write(settings_path, FOREIGN_SETTINGS)
    install.install(project, collect_only=True, sightings=WEIRD_SIGHTINGS)
    after = json.loads(settings_path.read_text(encoding="utf-8"))

    assert after["model"] == "opusmagnum"
    assert after["permissions"] == {"allow": ["Bash(ls:*)"]}
    assert _commands(after, "Notification") == ["say hi"]
    # Append-only: the user's entry keeps its index, ours goes after it.
    assert _commands(after, "PreToolUse")[0] == "sh ./audit.sh"
    assert len(_commands(after, "PreToolUse")) == 2
    assert list(after.keys())[:3] == ["model", "permissions", "hooks"]


def test_installing_collect_only_twice_changes_nothing_the_second_time(
        project, settings_path):
    _write(settings_path, FOREIGN_SETTINGS)
    install.install(project, collect_only=True, sightings=WEIRD_SIGHTINGS)
    body = settings_path.read_bytes()
    actions = install.install(project, collect_only=True, sightings=WEIRD_SIGHTINGS)
    assert settings_path.read_bytes() == body
    assert any(a.component == "settings" and a.kind == "skip" for a in actions)


def test_the_plan_says_the_install_is_deliberately_small(project, settings_path):
    """A plan is read by someone deciding whether to run it.

    An absent shim and an absent skill are the POINT here, and a report that
    only omitted them would read as an install missing half of itself.
    """
    _write(settings_path, FOREIGN_SETTINGS)
    text = install.render_plan(install.plan_install(
        project, collect_only=True, sightings=WEIRD_SIGHTINGS))
    assert "collect-only" in text
    assert WEIRD_SIGHTINGS in text
    assert "LYPNING_SIGHTINGS" in text
