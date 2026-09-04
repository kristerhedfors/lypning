"""Wiring lypning into opencode — one file we own, and nothing else touched.

opencode auto-discovers ``{plugin,plugins}/*.{ts,js}`` under its config
directories with no config entry of any kind, which makes this the easiest
install in the package to keep honest: there is no ``opencode.json`` to merge
into, so :mod:`lypning.install`'s hardest machinery — the settings merge that
exists because ``.claude/settings.json`` is a file the user owns — is not
needed here at all, and deliberately is not reached for.

**Invariant 7, three ways.** We own exactly one filename. Ownership is
decidable from that file's own bytes (:data:`PLUGIN_MARKER`, the same trick as
``shim.is_shim``), so there is no state file of ours to drift out of sync with
the disk. And the one cost we cause but cannot undo is stated up front rather
than discovered later: opencode writes a ``.gitignore``, a ``package.json`` and
a ``node_modules/`` into every config directory it scans, on its next start. If
this install is what created ``.opencode/``, the dry-run says so, because
``lypning uninstall`` removes only the plugin file and cannot put that back.

**What is deliberately NOT written:** an ``opencode.json`` ``"plugin"`` array
entry. File plugins are discovered without one, and adding it would mean
parsing and rewriting a user's JSONC — comments and all — which needs a parser
this package is not allowed to acquire (invariant 6).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import install, paths


NAME = "opencode"
TITLE = "opencode (sst/opencode)"
SCOPES = ("project", "user")

#: The line that makes this file ours. Read from the head of the plugin rather
#: than recorded anywhere, so a user who copies the file somewhere else, or a
#: tree restored from a backup, still answers the ownership question correctly.
PLUGIN_MARKER = "lypning-opencode-plugin"

#: How far into the file to look for it. The marker sits in the header comment;
#: a file that carries it further down than this is not one we wrote.
MARKER_LINES = 12

PLUGIN_NAME = "lypning.js"
BACKUP_SUFFIX = ".lypning-backup"


def config_root(project: Path | str | None = None, scope: str = "project") -> Path:
    """Where opencode reads plugins from, for this scope.

    Project scope is ``<project>/.opencode``: opencode walks up from the cwd to
    the work tree looking for it. User scope follows opencode's own resolution
    — ``$OPENCODE_CONFIG_DIR``, else ``$XDG_CONFIG_HOME/opencode``, else
    ``~/.config/opencode``.
    """
    if scope == "user":
        import os

        explicit = os.environ.get("OPENCODE_CONFIG_DIR", "").strip()
        if explicit:
            return Path(explicit).expanduser()
        xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
        if xdg:
            return Path(xdg).expanduser() / "opencode"
        return Path.home() / ".config" / "opencode"
    root = Path(project) if project else paths.project_dir()
    return Path(root) / ".opencode"


def plugin_path(project: Path | str | None = None, scope: str = "project") -> Path:
    return config_root(project, scope) / "plugin" / PLUGIN_NAME


def is_ours(path: Path) -> bool:
    """Does this file carry our marker in its head? Never raises."""
    try:
        with open(str(path), "r", encoding="utf-8", errors="replace") as fh:
            for _ in range(MARKER_LINES):
                line = fh.readline()
                if not line:
                    break
                if PLUGIN_MARKER in line:
                    return True
    except OSError:
        pass
    return False


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
    dest = plugin_path(project, scope)
    actions: List[install.Action] = []

    if hooks:
        src = paths.OPENCODE_ASSETS / PLUGIN_NAME
        if not src.is_file():
            actions.append(install.Action(
                "skip", dest, "plugin source missing at %s" % src, "plugin"))
        elif dest.is_file() and not is_ours(dest):
            # A refusal, not a note that everything is fine: something else owns
            # this name and overwriting it is exactly the cost invariant 7
            # forbids. `install.apply` passes --force to the shim only, so this
            # file is never moved aside by lypning: the note says so rather
            # than promising a backup that would not be made (uninstall restores
            # a `<name>` + BACKUP_SUFFIX file if one exists).
            actions.append(install.Action(
                "skip", dest,
                "NOT a lypning plugin — left alone; move it aside yourself "
                "(--force moves only a foreign python3 shim, not this file)",
                "plugin", src))
        else:
            actions.append(install._file_action(src, dest, "plugin"))
            if not root.exists():
                actions.append(install.Action(
                    "skip", root,
                    "note: opencode writes .gitignore, package.json and "
                    "node_modules/ into every config directory it scans, on its "
                    "next start. `lypning uninstall` removes only %s and cannot "
                    "undo that." % PLUGIN_NAME, "plugin"))

    if shim:
        actions.extend(install.shim_actions())

    return install.Plan(actions, proj, scope, None, [])


def uninstall(project: Path | str | None = None, *,
              scope: str = "project") -> List[install.Action]:
    """Remove exactly the plugin file, and only if it is ours.

    The exact inverse of the install and nothing more: opencode's own
    ``.gitignore``, ``package.json`` and ``node_modules/`` are never touched,
    and neither is the capture log.
    """
    actions: List[install.Action] = []
    dest = plugin_path(project, scope)
    if dest.is_file():
        if is_ours(dest):
            try:
                dest.unlink()
                actions.append(install.Action("remove", dest, "removed", "plugin"))
            except OSError as e:
                actions.append(install.Action(
                    "skip", dest, "FAILED: %s" % e, "plugin"))
        else:
            actions.append(install.Action(
                "skip", dest, "not ours — left alone", "plugin"))
    backup = dest.with_name(dest.name + BACKUP_SUFFIX)
    if backup.is_file():
        try:
            shutil.move(str(backup), str(dest))
            actions.append(install.Action(
                "remove", dest, "restored from %s" % backup.name, "plugin"))
        except OSError as e:
            actions.append(install.Action("skip", backup, "FAILED: %s" % e, "plugin"))
    return actions


def uninstall_preview(project: Path | str | None = None, *,
                      scope: str = "project") -> List[str]:
    """What :func:`uninstall` would remove, asked of the disk rather than of a
    model of it."""
    out: List[str] = []
    dest = plugin_path(project, scope)
    if dest.is_file():
        if is_ours(dest):
            out.append("- %s  (plugin)" % dest)
        else:
            out.append("? %s  (not ours — would be left alone)" % dest)
    backup = dest.with_name(dest.name + BACKUP_SUFFIX)
    if backup.is_file():
        out.append("+ %s  (restored from %s)" % (dest, backup.name))
    return out


def status(project: Path | str | None = None) -> Dict[str, Any]:
    """Read-only, JSON-serialisable. Invariant 8: no printing here."""
    scopes: Dict[str, Any] = {}
    for scope in SCOPES:
        dest = plugin_path(project, scope)
        present = dest.is_file()
        scopes[scope] = {
            "root": str(config_root(project, scope)),
            "plugin": str(dest),
            "installed": bool(present and is_ours(dest)),
            "foreign": bool(present and not is_ours(dest)),
        }
    return {"name": NAME, "title": TITLE, "scopes": scopes,
            "cli_on_path": shutil.which("lypning") is not None}


def detect(project: Path | str | None = None) -> List[str]:
    """Scopes whose config root already exists. Reported, never acted on: an
    install the user did not ask for is the surprise invariant 7 forbids."""
    return [s for s in SCOPES if config_root(project, s).is_dir()]
