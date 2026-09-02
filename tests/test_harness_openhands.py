"""The OpenHands adapter: a plugin directory, and no file of the user's edited.

The wiring decisions in ``hooks.json`` each fail silently if they drift, and
none of them is observable from Python at run time — the SDK is not a dependency
here and a conversation is not something a unit test starts. So they are pinned
by parsing the shipped asset, which is the instrument that is actually
available, and each test says which silent failure it is standing in front of.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lypning import install, paths
from lypning.harness import openhands


def _hooks(name="hooks.json"):
    return json.loads((paths.OPENHANDS_ASSETS / name).read_text(encoding="utf-8"))


def test_plan_writes_nothing(project):
    plan = openhands.plan(project, shim=False)
    assert [a for a in plan.actions if a.kind == "write"]
    assert not openhands.config_root(project).exists()


def test_install_uninstall_round_trip(project):
    before = sorted(p for p in project.rglob("*") if p.is_file())
    install.apply(openhands.plan(project, shim=False))
    root = openhands.config_root(project)
    assert root.is_dir() and openhands.is_ours(root)

    openhands.uninstall(project)
    assert not root.exists()
    after = sorted(p for p in project.rglob("*") if p.is_file())
    assert before == after


def test_no_user_hooks_json_is_ever_written(project):
    """The one thing this adapter must never do.

    ``HookConfig.load()`` is first-match-wins and NOT merged, so a hooks.json we
    wrote would hide the user's rather than join it — and the format has no
    per-entry marker, so uninstall could not remove only ours.
    """
    install.apply(openhands.plan(project, shim=False))
    assert not (project / ".openhands" / "hooks.json").exists()
    assert not (Path.home() / ".openhands" / "hooks.json").exists()


def test_the_hooks_json_matcher_is_the_registry_key():
    """`terminal` is the key the SDK derives; `TerminalTool` matches nothing."""
    groups = _hooks()["hooks"]["PostToolUse"]
    assert [g["matcher"] for g in groups] == ["terminal"]


def test_session_end_is_not_async():
    """The SDK terminates outstanding async hooks at session end, so an async
    harvest is one that gets killed partway through writing."""
    for group in _hooks()["hooks"]["SessionEnd"]:
        for hook in group["hooks"]:
            assert hook.get("async") is not True


def test_post_tool_use_is_async():
    """Fire-and-forget is the strongest form of "a hook never blocks": it is
    structurally incapable of it, not merely careful."""
    for group in _hooks()["hooks"]["PostToolUse"]:
        for hook in group["hooks"]:
            assert hook.get("async") is True


def test_exactly_one_capture_hook_is_wired_per_tool_call():
    """Two hooks on one tool write two records per call, and the harvester
    counts distinct occurrence keys — so `count` would silently double, and
    `conformance --plan` steers by count."""
    hooks = _hooks()["hooks"]
    assert "PostToolUse" in hooks
    pre = hooks.get("PreToolUse") or []
    assert not [g for g in pre if g.get("matcher") == "terminal"]


def test_every_hook_command_is_path_resolved_or_absolute():
    """There is no plugin-root expansion in the SDK and the executor sets cwd to
    the workspace, so a relative command resolves against the user's repo."""
    for groups in _hooks()["hooks"].values():
        for group in groups:
            for hook in group["hooks"]:
                command = hook["command"]
                assert not command.startswith("./")
                assert not command.startswith("../")


def test_no_hook_claims_a_permission_decision():
    """OpenHands honours `decision`. That is exactly why we never send one."""
    text = (paths.OPENHANDS_ASSETS / "hooks.json").read_text(encoding="utf-8")
    assert "decision" not in text


def test_the_fragment_and_the_installed_hooks_agree():
    """The paste-it-yourself copy cannot drift from the installed one."""
    assert _hooks("hooks.fragment.json")["hooks"] == _hooks("hooks.json")["hooks"]


def test_the_fragment_says_merge_do_not_replace():
    """It is the one file here a user is invited to hand-merge, and the merge is
    the dangerous part — first-match-wins means a copy HIDES what it replaces."""
    text = (paths.OPENHANDS_ASSETS / "hooks.fragment.json").read_text(encoding="utf-8")
    assert "MERGE, DO NOT REPLACE" in text


def test_uninstall_only_removes_a_directory_it_owns(project):
    root = openhands.config_root(project)
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "somebody-else"}), encoding="utf-8")
    openhands.uninstall(project)
    assert root.is_dir(), "a plugin directory that is not ours must survive"


def test_the_manifest_names_us():
    """Ownership is decided from the manifest on disk, so it has to say so."""
    manifest = json.loads((paths.OPENHANDS_ASSETS / "plugin.json")
                          .read_text(encoding="utf-8"))
    assert manifest["name"] == openhands.PLUGIN_DIR_NAME


def test_status_is_json_serialisable(project):
    json.dumps(openhands.status(project))
