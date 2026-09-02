"""Wiring lypning into the OpenHands agent SDK — a plugin directory, not a merge.

OpenHands loads plugins ambiently: every local conversation calls
``load_available_plugins`` with no config gate, detects the Claude Code plugin
layout, reads ``hooks/hooks.json`` out of it and merges those hooks into the
conversation. So dropping a directory at ``~/.openhands/plugins/lypning/``
installs the capture hooks **with no file the user owns edited at all** —
invariant 7 satisfied by construction rather than by careful merging.

**What is deliberately NOT written, and this is the important part.**
``HookConfig.load()`` is first-match-wins and *not merged*: it reads
``<workspace>/.openhands/hooks.json``, and only if that is absent,
``~/.openhands/hooks.json``. Writing either one would therefore not add our
hooks to the user's — it would **hide** them, or be hidden by them. And the
format carries no per-entry ownership marker, so a later uninstall could not
remove exactly ours. An uninstall that cannot be exact is one that costs the
user something they had, so this module never writes that file. For anyone who
wants it anyway, ``assets/openhands/hooks.fragment.json`` is the paste-it-
yourself copy, in the same role ``assets/claude/settings.fragment.json`` plays.

The three wiring decisions live in the asset and are asserted by tests, because
each fails silently: ``PostToolUse`` and not both hooks (two would double the
sighting count that ``--plan`` steers by), a synchronous ``SessionEnd`` (the
SDK kills outstanding async hooks at session end), and the matcher spelled
``terminal`` (the registry key — ``TerminalTool`` matches nothing).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import install, paths


NAME = "openhands"
TITLE = "OpenHands (OpenHands/software-agent-sdk)"
SCOPES = ("project", "user")

#: The plugin directory we own, under the harness's plugin search roots.
PLUGIN_DIR_NAME = "lypning"

#: Every file the install writes, as (asset name, path under the plugin root).
#: All three are byte-verbatim asset copies, so ``_file_action``'s content
#: compare works and a checkout and a wheel produce identical trees.
FILES = (
    ("plugin.json", Path(".claude-plugin") / "plugin.json"),
    ("hooks.json", Path("hooks") / "hooks.json"),
    ("README.md", Path("README.md")),
)


def config_root(project: Path | str | None = None, scope: str = "project") -> Path:
    """The plugin directory for this scope — the thing we own outright."""
    if scope == "user":
        return Path.home() / ".openhands" / "plugins" / PLUGIN_DIR_NAME
    root = Path(project) if project else paths.project_dir()
    return Path(root) / ".openhands" / "plugins" / PLUGIN_DIR_NAME


def manifest_path(project: Path | str | None = None, scope: str = "project") -> Path:
    return config_root(project, scope) / ".claude-plugin" / "plugin.json"


def is_ours(root: Path) -> bool:
    """Is this plugin directory one we wrote?

    Decided from the manifest on disk — ``"name": "lypning"`` — rather than from
    any record of ours, so a restored backup or a hand-copied tree still answers
    correctly. Never raises.
    """
    try:
        obj = json.loads((root / ".claude-plugin" / "plugin.json")
                         .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(obj, dict) and obj.get("name") == PLUGIN_DIR_NAME


def plan(
    project: Path | str | None = None,
    *,
    scope: str = "project",
    shim: bool = True,
    hooks: bool = True,
    prompt: bool = True,
) -> install.Plan:
    """Compute the whole install. **Writes nothing.**"""
    proj = Path(project) if project else paths.project_dir()
    root = config_root(project, scope)
    actions: List[install.Action] = []

    if hooks:
        if root.is_dir() and not is_ours(root):
            actions.append(install.Action(
                "skip", root,
                "a plugin directory of that name is already there and is not "
                "ours — left alone", "plugin"))
        else:
            for asset, relative in FILES:
                src = paths.OPENHANDS_ASSETS / asset
                if not src.is_file():
                    actions.append(install.Action(
                        "skip", root / relative,
                        "plugin source missing at %s" % src, "plugin"))
                    continue
                actions.append(install._file_action(src, root / relative, "plugin"))

    if shim:
        actions.extend(install.shim_actions())

    return install.Plan(actions, proj, scope, None, [])


def uninstall(project: Path | str | None = None, *,
              scope: str = "project") -> List[install.Action]:
    """Remove the plugin directory, and only if its manifest says it is ours.

    The capture log is never touched: the programs in it outlive the harness
    that recorded them.
    """
    actions: List[install.Action] = []
    root = config_root(project, scope)
    if not root.is_dir():
        return actions
    if not is_ours(root):
        actions.append(install.Action(
            "skip", root, "not ours — left alone", "plugin"))
        return actions
    try:
        shutil.rmtree(str(root))
        actions.append(install.Action("remove", root, "removed", "plugin"))
    except OSError as e:
        actions.append(install.Action("skip", root, "FAILED: %s" % e, "plugin"))
    return actions


def uninstall_preview(project: Path | str | None = None, *,
                      scope: str = "project") -> List[str]:
    root = config_root(project, scope)
    if not root.is_dir():
        return []
    if is_ours(root):
        return ["- %s  (plugin directory)" % root]
    return ["? %s  (not ours — would be left alone)" % root]


def status(project: Path | str | None = None) -> Dict[str, Any]:
    """Read-only, JSON-serialisable. Invariant 8: no printing here."""
    scopes: Dict[str, Any] = {}
    for scope in SCOPES:
        root = config_root(project, scope)
        present = root.is_dir()
        scopes[scope] = {
            "root": str(root),
            "installed": bool(present and is_ours(root)),
            "foreign": bool(present and not is_ours(root)),
        }
    return {"name": NAME, "title": TITLE, "scopes": scopes,
            "cli_on_path": shutil.which("lypning") is not None}


def detect(project: Path | str | None = None) -> List[str]:
    """Scopes whose plugin search root already exists. Reported, never acted on."""
    return [s for s in SCOPES if config_root(project, s).parent.is_dir()]
