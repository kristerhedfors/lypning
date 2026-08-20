"""Wiring lypning into a Claude Code session without breaking the session.

Three things have to be true before the capture harness produces anything: the
agent can load the skill, the hooks fire on the right events, and the shim is on
``$PATH``. All three live in files a user already owns and has opinions about —
``.claude/settings.json`` most of all — so this module's real subject is not
installation, it is **merging**.

The invariants:

**Nothing this module writes may cost the user something they had.** The merge
reads the existing JSON, keeps every unrelated key, keeps every unrelated hook,
keeps the order they were already in, and appends ours only when no entry with
the same command is there. The file is copied to ``settings.json.lypning-backup``
before the first modification and the backup is never overwritten afterwards —
the pristine original is the thing worth keeping, not the last state.

**Uninstall is the exact inverse.** It drops the entries whose command mentions
lypning and nothing else; an event array we empty is removed, the file never is,
and the capture log is never touched.

**``--dry-run`` is real.** :func:`plan_install` opens files and writes none;
:func:`render_plan` prints every action that would happen, with the settings
merge shown as a unified diff of the JSON. A user gets to read the change to
their config before it is a change to their config.

The MicroPython tier is allowed to be missing throughout — an engine that is not
built is a status line, never an error.
"""

from __future__ import annotations

import copy
import difflib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import paths, shim


# `shim` is a keyword argument on plan_install/install, so the module needs a
# second name that a caller cannot shadow.
shim_module = shim

SETTINGS_BACKUP_SUFFIX = ".lypning-backup"

# The one string that decides what uninstall may delete. Every command we write
# contains it — either as the script name or as the CLI verb — and no unrelated
# hook plausibly does, which is what makes "remove exactly ours" decidable from
# the settings file alone, with no state of our own to keep in sync.
OUR_MARK = "lypning"


@dataclass(frozen=True)
class HookSpec:
    """One hook we want present, and the two ways it can be spelled."""

    event: str
    matcher: Optional[str]
    scripts: Tuple[str, ...]
    fallback: str


HOOKS: Tuple[HookSpec, ...] = (
    # SessionStart re-installs the shim: these containers are ephemeral and a
    # shim that was installed in a previous session is not on this one's PATH.
    HookSpec("SessionStart", None,
             ("lypning-session-start.sh", "lypning-install.sh", "lypning-shim.sh"),
             "lypning shim install"),
    # PreToolUse/Bash catches the command string — heredoc bodies, `uv run`
    # wrappers, write-then-run — which the shim never sees as argv.
    HookSpec("PreToolUse", "Bash",
             ("lypning-capture.sh",),
             "lypning hook pre-tool-use"),
    # Stop folds the session's log into tests/corpus/sightings before teardown
    # takes the container and the log with it.
    HookSpec("Stop", None,
             ("lypning-harvest.sh",),
             "lypning hook stop"),
)


@dataclass
class Action:
    """One thing that will happen, or did. ``kind`` drives the renderer."""

    kind: str  # "write" | "merge" | "skip" | "backup" | "remove"
    path: Path
    note: str = ""
    component: str = ""  # "skill" | "hook" | "settings" | "shim"
    source: Optional[Path] = None

    def as_dict(self) -> dict:
        return {"kind": self.kind, "path": str(self.path), "note": self.note,
                "component": self.component}


@dataclass
class Plan:
    """Everything :func:`apply` would do, computed without touching the disk."""

    actions: List[Action]
    project: Path
    scope: str = "project"
    settings_path: Optional[Path] = None
    diff: List[str] = field(default_factory=list)

    @property
    def changes(self) -> List[Action]:
        return [a for a in self.actions if a.kind != "skip"]

    def as_dict(self) -> dict:
        return {
            "project": str(self.project),
            "scope": self.scope,
            "settings": str(self.settings_path) if self.settings_path else None,
            "actions": [a.as_dict() for a in self.actions],
            "diff": self.diff,
        }


# --- locations ---------------------------------------------------------------


def _project(project: Path | str | None) -> Path:
    return Path(project).expanduser().resolve() if project else paths.project_dir()


def claude_dir(project: Path | str | None = None, scope: str = "project") -> Path:
    """``~/.claude`` for user scope, ``<project>/.claude`` otherwise."""
    if scope == "user":
        return Path(os.path.expanduser("~")) / ".claude"
    if scope != "project":
        raise ValueError("scope must be 'project' or 'user', not %r" % scope)
    return _project(project) / ".claude"


def _hook_command(scope: str, script: Optional[str], fallback: str) -> str:
    """A copied script when we have one, else the CLI entry point.

    Both spellings mention lypning, so uninstall removes either without knowing
    which one this install chose — and the choice can differ between a source
    checkout (which ships the .sh) and a wheel (which may not).
    """
    if not script:
        return fallback
    base = "$CLAUDE_PROJECT_DIR" if scope == "project" else "$HOME"
    return 'sh "%s/.claude/hooks/%s"' % (base, script)


def _available_scripts(*dirs: Path) -> List[str]:
    names: List[str] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.sh")):
            if p.name not in names:
                names.append(p.name)
    return names


def hook_entries(scope: str, scripts: Sequence[str]) -> List[Tuple[str, Optional[str], str]]:
    """The ``(event, matcher, command)`` triples this install wants present."""
    out: List[Tuple[str, Optional[str], str]] = []
    for spec in HOOKS:
        chosen = next((n for n in spec.scripts if n in scripts), None)
        out.append((spec.event, spec.matcher, _hook_command(scope, chosen, spec.fallback)))
    return out


# --- the merge (pure; no I/O, so it is testable and diffable) -----------------


def load_settings(path: Path) -> Tuple[Dict[str, Any], Optional[str]]:
    """``(settings, error)``. A parse error is data, not an exception.

    A settings.json we cannot parse is a settings.json we must not write: the
    caller turns the error into a skip action and leaves the file exactly as it
    found it.
    """
    if not path.is_file():
        return {}, None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return {}, "cannot read %s: %s" % (path, e)
    if not text.strip():
        return {}, None
    try:
        data = json.loads(text)
    except ValueError as e:
        return {}, "%s is not valid JSON (%s)" % (path.name, e)
    if not isinstance(data, dict):
        return {}, "%s does not contain a JSON object" % path.name
    return data, None


def _matcher_of(group: Any) -> Optional[str]:
    if not isinstance(group, dict):
        return None
    m = group.get("matcher")
    return m if isinstance(m, str) and m else None


def _commands_of(group: Any) -> List[str]:
    if not isinstance(group, dict):
        return []
    hooks = group.get("hooks")
    if not isinstance(hooks, list):
        return []
    return [h.get("command", "") for h in hooks if isinstance(h, dict)]


def merge_hooks(
    settings: Dict[str, Any],
    entries: Sequence[Tuple[str, Optional[str], str]],
) -> Tuple[Dict[str, Any], List[str]]:
    """Add our entries to a deep copy. Returns ``(new_settings, added_commands)``.

    Append-only, by construction: an existing group is extended at the end and a
    new group goes at the end of the event's list, so nothing that was already
    in the file changes index. Insertion order of a dict is preserved by the
    copy, which is what keeps unrelated keys where the user put them.
    """
    out = copy.deepcopy(settings)
    added: List[str] = []
    hooks = out.get("hooks")
    if hooks is None:
        hooks = {}
        out["hooks"] = hooks
    elif not isinstance(hooks, dict):
        # Some other tool's shape. Never rewrite what we do not understand.
        return out, added
    for event, matcher, command in entries:
        groups = hooks.get(event)
        if not isinstance(groups, list):
            if groups is not None:
                continue  # someone else's shape; leave it strictly alone
            groups = []
            hooks[event] = groups
        if any(command in _commands_of(g) for g in groups):
            continue  # already there — this is what makes re-running a no-op
        entry = {"type": "command", "command": command}
        target = next((g for g in groups
                       if isinstance(g, dict) and _matcher_of(g) == matcher
                       and isinstance(g.get("hooks"), list)), None)
        if target is not None:
            target["hooks"].append(entry)
        else:
            group: Dict[str, Any] = {}
            if matcher:
                group["matcher"] = matcher
            group["hooks"] = [entry]
            groups.append(group)
        added.append(command)
    return out, added


def strip_hooks(settings: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Remove every hook entry whose command mentions lypning. Nothing else."""
    out = copy.deepcopy(settings)
    removed: List[str] = []
    hooks = out.get("hooks")
    if not isinstance(hooks, dict):
        return out, removed
    for event in list(hooks.keys()):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept_groups: List[Any] = []
        for g in groups:
            if not isinstance(g, dict) or not isinstance(g.get("hooks"), list):
                kept_groups.append(g)
                continue
            kept: List[Any] = []
            for h in g["hooks"]:
                cmd = h.get("command", "") if isinstance(h, dict) else ""
                if isinstance(cmd, str) and OUR_MARK in cmd.lower():
                    removed.append(cmd)
                else:
                    kept.append(h)
            if not kept and g["hooks"]:
                continue  # a group WE emptied; an already-empty one is theirs
            g["hooks"] = kept
            kept_groups.append(g)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            # An event array with nothing left in it is noise in the user's
            # config; the file itself is theirs and stays.
            del hooks[event]
    return out, removed


def dumps_settings(settings: Dict[str, Any]) -> str:
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


def settings_diff(before: Dict[str, Any], after: Dict[str, Any], path: Path) -> List[str]:
    return list(difflib.unified_diff(
        dumps_settings(before).splitlines(),
        dumps_settings(after).splitlines(),
        fromfile=str(path) + " (current)",
        tofile=str(path) + " (after install)",
        lineterm="",
    ))


# --- planning ----------------------------------------------------------------


def _file_action(src: Path, dest: Path, component: str) -> Action:
    if dest.is_file():
        try:
            if dest.read_bytes() == src.read_bytes():
                return Action("skip", dest, "identical", component, src)
        except OSError:
            pass
        return Action("write", dest, "overwrite", component, src)
    return Action("write", dest, "new", component, src)


def plan_install(
    project: Path | str | None = None,
    *,
    scope: str = "project",
    shim: bool = True,
    hooks: bool = True,
    skill: bool = True,
) -> Plan:
    """Compute the whole install. **Writes nothing.**"""
    want_shim, want_hooks, want_skill = shim, hooks, skill
    proj = _project(project)
    root = claude_dir(proj, scope)
    actions: List[Action] = []
    settings_path = root / "settings.json"
    diff: List[str] = []

    if want_skill:
        src = paths.SKILL_SRC
        dest_root = root / "skills" / "lypning"
        if not src.is_dir():
            actions.append(Action("skip", dest_root, "skill source missing at %s" % src, "skill"))
        else:
            files = sorted(p for p in src.rglob("*") if p.is_file())
            if not files:
                actions.append(Action("skip", dest_root, "skill source is empty", "skill"))
            for f in files:
                actions.append(_file_action(f, dest_root / f.relative_to(src), "skill"))

    scripts: List[str] = []
    if want_hooks:
        hooks_src = paths.HOOKS_SRC
        dest_root = root / "hooks"
        scripts = _available_scripts(hooks_src)
        if not scripts:
            # A wheel without the shell hooks is a supported shape: the CLI
            # entry points (`lypning hook …`) do the same work, one exec later.
            actions.append(Action(
                "skip", dest_root,
                "no hook scripts in %s — wiring the `lypning hook` CLI entry points instead"
                % hooks_src, "hook"))
        for name in scripts:
            actions.append(_file_action(hooks_src / name, dest_root / name, "hook"))

        entries = hook_entries(scope, scripts)
        before, err = load_settings(settings_path)
        if err:
            actions.append(Action("skip", settings_path, err + " — refusing to touch it", "settings"))
        else:
            after, added = merge_hooks(before, entries)
            if not added:
                actions.append(Action("skip", settings_path,
                                      "all %d hook entries already present" % len(entries),
                                      "settings"))
            else:
                backup = settings_path.with_name(settings_path.name + SETTINGS_BACKUP_SUFFIX)
                if settings_path.is_file() and not backup.exists():
                    actions.append(Action("backup", backup, "copy of the current settings.json",
                                          "settings", settings_path))
                note = "add %d hook entr%s" % (len(added), "y" if len(added) == 1 else "ies")
                if not settings_path.is_file():
                    note += " (creating the file)"
                actions.append(Action("merge", settings_path, note, "settings"))
                diff = settings_diff(before, after, settings_path)

    if want_shim:
        for st in shim_module.status():
            if st.state == "current":
                actions.append(Action("skip", st.path, "shim already current", "shim"))
            elif st.state == "stale":
                actions.append(Action("write", st.path, "refresh stale shim", "shim",
                                      paths.SHIM_SRC))
            elif st.state == "absent":
                actions.append(Action("write", st.path, "install shim", "shim", paths.SHIM_SRC))
            else:
                actions.append(Action(
                    "backup", st.path,
                    "NOT a lypning shim — needs --force, which moves it to %s%s"
                    % (st.path.name, shim_module.BACKUP_SUFFIX), "shim", paths.SHIM_SRC))

    return Plan(actions, proj, scope, settings_path, diff)



# --- applying ----------------------------------------------------------------


def _copy(src: Path, dest: Path, mode: int) -> None:
    paths.ensure_dir(dest.parent)
    tmp = dest.with_name(dest.name + ".lypning-tmp")
    shutil.copyfile(str(src), str(tmp))
    os.chmod(str(tmp), mode)
    os.replace(str(tmp), str(dest))


# shim.install/uninstall report in lines because that is what `lypning shim` has
# to print; the installer wants the same information as actions. The verb is the
# first word and an absolute second word is the path it acted on, which is the
# whole grammar those messages use.
_SHIM_VERBS = {"installed": "write", "refreshed": "write", "backed": "backup",
               "removed": "remove", "restored": "remove", "unchanged": "skip"}


def _shim_action(line: str) -> Action:
    verb, _, rest = line.partition(" ")
    kind = _SHIM_VERBS.get(verb, "skip")
    if verb == "backed" and rest.startswith("up "):
        verb, rest = "backed up", rest[3:]
    word = rest.split(" ", 1)[0] if rest else ""
    if word.startswith("/"):
        return Action(kind, Path(word), rest[len(word):].strip(" ") or verb, "shim")
    return Action(kind, paths.bin_dir(), line, "shim")


def apply(plan: Plan, *, force: bool = False) -> List[Action]:
    """Execute a plan. Returns what actually happened, in order."""
    done: List[Action] = []
    shim_pending = [a for a in plan.actions if a.component == "shim" and a.kind != "skip"]
    for a in plan.actions:
        if a.component == "shim":
            continue  # handled once, below, by shim.install()
        if a.kind == "skip":
            done.append(a)
            continue
        try:
            if a.kind == "write" and a.source is not None:
                _copy(a.source, a.path, 0o755 if a.component == "hook" else 0o644)
                done.append(a)
            elif a.kind == "backup" and a.source is not None:
                paths.ensure_dir(a.path.parent)
                shutil.copyfile(str(a.source), str(a.path))
                done.append(a)
            elif a.kind == "merge":
                # Re-read and re-merge rather than replaying the planned text:
                # the file may have moved under us, and the merge is idempotent,
                # so the fresh one is right whether or not it did.
                before, err = load_settings(a.path)
                if err:
                    done.append(Action("skip", a.path, err, "settings"))
                    continue
                scripts = _available_scripts(paths.HOOKS_SRC, a.path.parent / "hooks")
                after, added = merge_hooks(before, hook_entries(plan.scope, scripts))
                if not added:
                    done.append(Action("skip", a.path, "already present", "settings"))
                    continue
                paths.ensure_dir(a.path.parent)
                a.path.write_text(dumps_settings(after), encoding="utf-8")
                done.append(Action("merge", a.path, "added %d entries" % len(added), "settings"))
            else:
                done.append(Action("skip", a.path, "nothing to do", a.component))
        except OSError as e:
            done.append(Action("skip", a.path, "FAILED: %s" % e, a.component))

    if shim_pending:
        try:
            for line in shim_module.install(force=force):
                done.append(_shim_action(line))
        except shim_module.ShimError as e:
            # A refused shim must not fail the rest of the install: the hooks and
            # the skill are useful on their own, and the refusal is the message.
            done.append(Action("skip", paths.bin_dir(), str(e), "shim"))
    return done


def install(project: Path | str | None = None, **kw: Any) -> List[Action]:
    """Plan, then apply. ``force`` goes to :func:`apply`, the rest to the plan."""
    force = bool(kw.pop("force", False))
    return apply(plan_install(project, **kw), force=force)


def uninstall(project: Path | str | None = None, *, scope: str = "project") -> List[Action]:
    """Remove exactly what :func:`install` added, and nothing adjacent."""
    proj = _project(project)
    root = claude_dir(proj, scope)
    done: List[Action] = []

    skill_dir = root / "skills" / "lypning"
    if skill_dir.is_dir():
        try:
            shutil.rmtree(str(skill_dir))
            done.append(Action("remove", skill_dir, "skill", "skill"))
        except OSError as e:
            done.append(Action("skip", skill_dir, "FAILED: %s" % e, "skill"))

    hooks_dir = root / "hooks"
    if hooks_dir.is_dir():
        ours = set(_available_scripts(paths.HOOKS_SRC))
        for p in sorted(hooks_dir.glob("*.sh")):
            if p.name not in ours and not p.name.startswith("lypning"):
                continue  # somebody else's hook script lives here too
            try:
                p.unlink()
                done.append(Action("remove", p, "hook script", "hook"))
            except OSError as e:
                done.append(Action("skip", p, "FAILED: %s" % e, "hook"))

    settings_path = root / "settings.json"
    if settings_path.is_file():
        before, err = load_settings(settings_path)
        if err:
            done.append(Action("skip", settings_path, err + " — left untouched", "settings"))
        else:
            after, removed = strip_hooks(before)
            if removed:
                try:
                    settings_path.write_text(dumps_settings(after), encoding="utf-8")
                    done.append(Action("remove", settings_path,
                                       "removed %d hook entr%s" % (
                                           len(removed), "y" if len(removed) == 1 else "ies"),
                                       "settings"))
                except OSError as e:
                    done.append(Action("skip", settings_path, "FAILED: %s" % e, "settings"))
            else:
                done.append(Action("skip", settings_path, "no lypning hook entries", "settings"))

    for line in shim_module.uninstall():
        done.append(_shim_action(line))
    return done


# --- status ------------------------------------------------------------------


def status(project: Path | str | None = None) -> dict:
    """Everything a `lypning status` needs, JSON-serialisable, read-only."""
    from . import engines  # imported here: status is the only caller and it is cold

    proj = _project(project)
    out: Dict[str, Any] = {"project": str(proj), "scopes": {}}
    for scope in ("project", "user"):
        root = claude_dir(proj, scope)
        settings_path = root / "settings.json"
        settings, err = load_settings(settings_path)
        present: Dict[str, List[str]] = {}
        for event, groups in (settings.get("hooks") or {}).items():
            if not isinstance(groups, list):
                continue
            mine = [c for g in groups for c in _commands_of(g)
                    if isinstance(c, str) and OUR_MARK in c.lower()]
            if mine:
                present[event] = mine
        skill_dir = root / "skills" / "lypning"
        out["scopes"][scope] = {
            "claude_dir": str(root),
            "settings": str(settings_path),
            "settings_exists": settings_path.is_file(),
            "settings_error": err,
            "backup": str(settings_path) + SETTINGS_BACKUP_SUFFIX
            if (settings_path.with_name(settings_path.name + SETTINGS_BACKUP_SUFFIX)).exists()
            else None,
            "hooks": present,
            "hook_scripts": _available_scripts(root / "hooks"),
            "skill": str(skill_dir) if skill_dir.is_dir() else None,
        }

    log = paths.log_path()
    out["log"] = {"path": str(log), "exists": log.is_file(),
                  "bytes": log.stat().st_size if log.is_file() else 0}
    out["shim"] = {
        "bin_dir": str(paths.bin_dir()),
        "source": str(paths.SHIM_SRC),
        "states": [s.as_dict() for s in shim_module.status()],
        "path_problem": shim_module.path_problem(),
    }
    # An engine that is not built is a status line, never an error — lypning-mp
    # needs a toolchain and a network the install may simply not have.
    out["engines"] = {name: (str(p) if p else None) for name, p in engines.available().items()}
    return out


# --- rendering ---------------------------------------------------------------

_SIGIL = {"write": "+", "merge": "~", "backup": "b", "remove": "-", "skip": "."}


def render_plan(plan: Plan) -> str:
    """The dry-run report: every action, then the settings.json diff."""
    out = [
        "project : %s" % plan.project,
        "scope   : %s (%s)" % (plan.scope, claude_dir(plan.project, plan.scope)),
        "",
    ]
    if not plan.actions:
        out.append("nothing to do")
    for a in plan.actions:
        note = "  — %s" % a.note if a.note else ""
        out.append("%s %-7s %s%s" % (_SIGIL.get(a.kind, "?"), a.kind, a.path, note))
    changes = len(plan.changes)
    out.append("")
    out.append("%d change%s, %d already in place"
               % (changes, "" if changes == 1 else "s", len(plan.actions) - changes))
    if plan.diff:
        # The diff carries its own ---/+++ header naming the file, so nothing is
        # printed above it: a second header would only invite a reader to mistake
        # this for a patch they can apply.
        out.append("")
        out.extend(plan.diff)
    return "\n".join(out)


def render_actions(actions: Sequence[Action]) -> str:
    return "\n".join(
        "%s %-7s %s%s" % (_SIGIL.get(a.kind, "?"), a.kind, a.path,
                          "  — %s" % a.note if a.note else "")
        for a in actions
    ) or "nothing to do"


def render_status(st: dict) -> str:
    """The human report for ``lypning status``."""
    out = ["project : %s" % st.get("project", "?")]
    for scope, s in (st.get("scopes") or {}).items():
        out.append("")
        out.append("%s scope — %s" % (scope, s["claude_dir"]))
        out.append("  skill    : %s" % (s["skill"] or "not installed"))
        scripts = s["hook_scripts"]
        out.append("  scripts  : %s" % (", ".join(scripts) if scripts else "none"))
        if s["settings_error"]:
            out.append("  settings : ERROR — %s" % s["settings_error"])
        elif not s["settings_exists"]:
            out.append("  settings : %s (does not exist)" % s["settings"])
        else:
            hooks = s["hooks"]
            if hooks:
                for event in sorted(hooks):
                    for cmd in hooks[event]:
                        out.append("  hook     : %-13s %s" % (event, cmd))
            else:
                out.append("  hook     : none of ours in %s" % s["settings"])
        if s["backup"]:
            out.append("  backup   : %s" % s["backup"])

    shim_info = st.get("shim") or {}
    out.append("")
    out.append("shim — %s" % shim_info.get("bin_dir", "?"))
    for s in shim_info.get("states", []):
        out.append("  %-8s %s" % (s["name"] + ":", s["state"]))
    problem = shim_info.get("path_problem")
    if problem:
        out.append("  PATH   : WARNING — %s" % problem)
    else:
        out.append("  PATH   : ok")

    log = st.get("log") or {}
    out.append("")
    out.append("log   : %s (%s)" % (log.get("path"),
                                    "%d bytes" % log.get("bytes", 0) if log.get("exists")
                                    else "not created yet"))
    out.append("engines:")
    for name, p in (st.get("engines") or {}).items():
        out.append("  %-11s %s" % (name + ":", p or "not built"))
    return "\n".join(out)
