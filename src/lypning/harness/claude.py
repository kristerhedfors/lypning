"""Claude Code, behind the same interface the other harness modules present.

This module adds no behaviour whatsoever. The Claude install is
:mod:`lypning.install` itself — the settings merge, the backup, the diff, the
hook scripts, the skill — and it stays there, because that is where its
invariant-7 regression suite points. All this does is give :mod:`lypning.cli`
one dispatch shape instead of two, so a per-harness branch in the CLI never has
to grow a special case for the harness that came first.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List

from .. import install


NAME = "claude"
TITLE = "Claude Code"
SCOPES = ("project", "user")


def config_root(project: Path | str | None = None, scope: str = "project") -> Path:
    return install.claude_dir(project, scope)


def plan(
    project: Path | str | None = None,
    *,
    scope: str = "project",
    shim: bool = True,
    hooks: bool = True,
    prompt: bool = True,
) -> install.Plan:
    """``prompt`` is the skill here: it is how the agent is told to route."""
    return install.plan_install(project, scope=scope, shim=shim, hooks=hooks,
                                skill=prompt)


def uninstall(project: Path | str | None = None, *,
              scope: str = "project") -> List[install.Action]:
    return install.uninstall(project, scope=scope)


def uninstall_preview(project: Path | str | None = None, *,
                      scope: str = "project") -> List[str]:
    """What is there to remove, asked of the disk.

    There is no plan object for the removal side, and inventing one here would
    put a second definition of "what we installed" next to the real one.
    """
    out: List[str] = []
    root = config_root(project, scope)
    skill = root / "skills" / "lypning"
    if skill.is_dir():
        out.append("- %s  (skill)" % skill)
    hooks_dir = root / "hooks"
    if hooks_dir.is_dir():
        for p in sorted(hooks_dir.glob("*.sh")):
            if p.name.startswith("lypning"):
                out.append("- %s  (hook script)" % p)
    st = install.status(project)
    present = (st.get("scopes", {}).get(scope, {}) or {}).get("hooks") or {}
    for event, cmds in sorted(present.items()):
        out.append("- %s  (%d hook entr%s under %s)"
                   % (root / "settings.json", len(cmds),
                      "y" if len(cmds) == 1 else "ies", event))
    return out


def status(project: Path | str | None = None) -> Dict[str, Any]:
    """A summary of what ``install.status()['scopes']`` already says.

    The duplication is deliberate and documented there: ``st["scopes"]`` is
    frozen as the Claude view because ``cli`` and every ``--json`` consumer
    index it, so the harness dimension is a sibling key rather than a reshape.
    """
    st = install.status(project)
    scopes: Dict[str, Any] = {}
    for scope in SCOPES:
        sc = (st.get("scopes", {}).get(scope, {}) or {})
        hooks = sc.get("hooks") or {}
        scopes[scope] = {
            "root": str(config_root(project, scope)),
            "installed": bool(hooks),
            "events": sorted(hooks),
            "settings_error": sc.get("settings_error"),
        }
    return {"name": NAME, "title": TITLE, "scopes": scopes,
            "cli_on_path": shutil.which("lypning") is not None}


def detect(project: Path | str | None = None) -> List[str]:
    return [s for s in SCOPES if config_root(project, s).is_dir()]
