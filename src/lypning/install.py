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
their config before it is a change to their config."""

from __future__ import annotations

import copy
import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
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


SCOPES = ("project", "user")


@dataclass(frozen=True)
class HookSpec:
    """One hook we want present, the two ways it can be spelled, and where.

    ``scopes`` is which installs register it. A user-scope hook fires in EVERY
    repository the user opens, so it earns its place only by being harmless in
    a repository that is not ours — which only the capture hook is.
    """

    event: str
    matcher: Optional[str]
    scripts: Tuple[str, ...]
    fallback: str
    scopes: Tuple[str, ...] = SCOPES


HOOKS: Tuple[HookSpec, ...] = (
    # SessionStart re-installs the shim: these containers are ephemeral and a
    # shim that was installed in a previous session is not on this one's PATH.
    # Project only: at user scope it would write a shim into ~/.lypning/bin
    # and inject lypning's engine state into every unrelated session.
    HookSpec("SessionStart", None,
             ("lypning-session-start.sh", "lypning-install.sh", "lypning-shim.sh"),
             "lypning shim install", scopes=("project",)),
    # PreToolUse/Bash catches the command string — heredoc bodies, `uv run`
    # wrappers, write-then-run — which the shim never sees as argv. The one
    # hook that belongs at user scope: it only appends to our own log, outside
    # every repository, so it can run anywhere at all times.
    HookSpec("PreToolUse", "Bash",
             ("lypning-capture.sh",),
             "lypning hook pre-tool-use"),
    # Stop folds the session's log into tests/corpus/sightings before teardown
    # takes the container and the log with it. Project only: that directory is
    # a lypning checkout's, and at user scope this would write it into every
    # repository the user opens. `capture.hook_stop` refuses outside a checkout
    # as well, which is what protects an install made before this field.
    HookSpec("Stop", None,
             ("lypning-harvest.sh",),
             "lypning hook stop", scopes=("project",)),
)


@dataclass
class Action:
    """One thing that will happen, or did. ``kind`` drives the renderer."""

    kind: str  # "write" | "merge" | "skip" | "backup" | "remove"
    path: Path
    note: str = ""
    component: str = ""  # "skill" | "hook" | "settings" | "shim"
    source: Optional[Path] = None
    #: Only for ``skip``: this thing is ALREADY as the install wants it. The
    #: other skips are refusals and warnings, which must never be summarised as
    #: "already in place" — that is the one summary a reader takes as "fine".
    present: bool = False

    def as_dict(self) -> dict:
        return {"kind": self.kind, "path": str(self.path), "note": self.note,
                "component": self.component, "present": self.present}


@dataclass
class Plan:
    """Everything :func:`apply` would do, computed without touching the disk."""

    actions: List[Action]
    project: Path
    scope: str = "project"
    settings_path: Optional[Path] = None
    diff: List[str] = field(default_factory=list)
    #: A source tree pinned onto every hook command as ``LYPNING_PYTHONPATH``
    #: (:func:`plan_install`). Carried on the plan because :func:`apply`
    #: re-derives the entries rather than replaying the planned text.
    pythonpath: Optional[str] = None

    @property
    def changes(self) -> List[Action]:
        return [a for a in self.actions if a.kind != "skip"]

    @property
    def already(self) -> List[Action]:
        return [a for a in self.actions if a.kind == "skip" and a.present]

    @property
    def notes(self) -> List[Action]:
        """Skips that are NOT "already fine": refusals, warnings, failures."""
        return [a for a in self.actions if a.kind == "skip" and not a.present]

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


def _is_git_worktree(p: Path) -> bool:
    """Did the project root come from a repository, or from a bare ``cd``?

    ``paths.project_dir`` falls back to the current directory when there is no
    git toplevel, which is right — a ``.claude`` directory is useful in a plain
    directory too — but a plan that writes into whatever directory the user
    happened to be standing in should say so rather than look deliberate.
    """
    return (p / ".git").exists()


def claude_dir(project: Path | str | None = None, scope: str = "project") -> Path:
    """``~/.claude`` for user scope, ``<project>/.claude`` otherwise."""
    if scope == "user":
        return Path(os.path.expanduser("~")) / ".claude"
    if scope != "project":
        raise ValueError("scope must be 'project' or 'user', not %r" % scope)
    return _project(project) / ".claude"


def _hook_command(scope: str, script: Optional[str], fallback: str,
                  pythonpath: Optional[str] = None) -> str:
    """A copied script when we have one, else the CLI entry point.

    Both spellings mention lypning, so uninstall removes either without knowing
    which one this install chose — and the choice can differ between a source
    checkout (which ships the .sh) and a wheel (which may not).

    ``pythonpath`` prefixes the command with ``LYPNING_PYTHONPATH=<dir>``, the
    arm the scripts read when the package is neither installed nor the
    session's own checkout. Only on a script: the CLI fallback needs ``lypning``
    on PATH, and a pinned tree cannot help a command that never reaches python.
    """
    if not script:
        return fallback
    base = "$CLAUDE_PROJECT_DIR" if scope == "project" else "$HOME"
    command = 'sh "%s/.claude/hooks/%s"' % (base, script)
    if pythonpath:
        command = "LYPNING_PYTHONPATH=%s %s" % (shlex.quote(pythonpath), command)
    return command


def _available_scripts(*dirs: Path) -> List[str]:
    names: List[str] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.sh")):
            if p.name not in names:
                names.append(p.name)
    return names


def hook_entries(scope: str, scripts: Sequence[str],
                 pythonpath: Optional[str] = None) -> List[Tuple[str, Optional[str], str]]:
    """The ``(event, matcher, command)`` triples this install wants present.

    Only the specs registered at ``scope`` (:attr:`HookSpec.scopes`).
    """
    out: List[Tuple[str, Optional[str], str]] = []
    for spec in HOOKS:
        if scope not in spec.scopes:
            continue
        chosen = next((n for n in spec.scripts if n in scripts), None)
        out.append((spec.event, spec.matcher,
                    _hook_command(scope, chosen, spec.fallback, pythonpath)))
    return out


def _scripts_for(scope: str, available: Sequence[str]) -> List[str]:
    """The hook scripts an install at ``scope`` copies.

    A project install copies every shipped script, as it always has. A user
    install copies only the ones its own entries name: a Stop script sitting
    unregistered in ``~/.claude/hooks`` is an invitation to wire it by hand,
    and wired by hand it is the one this scope exists not to have.
    """
    if scope != "user":
        return list(available)
    wanted = {n for spec in HOOKS if scope in spec.scopes for n in spec.scripts}
    return [n for n in available if n in wanted]


def _stale_scope_entries(settings: Dict[str, Any], scope: str) -> List[str]:
    """Events holding one of our commands that ``scope`` no longer registers.

    What an install from before :attr:`HookSpec.scopes` left at user scope. It
    is reported, never removed: an install is append-only, and deleting an
    entry is uninstall's job, which is exact about it.
    """
    allowed = {spec.event for spec in HOOKS if scope in spec.scopes}
    ours = {spec.event for spec in HOOKS}
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return []
    stale = []
    for event, groups in hooks.items():
        if event in allowed or event not in ours or not isinstance(groups, list):
            continue
        if any(isinstance(c, str) and OUR_MARK in c.lower()
               for g in groups for c in _commands_of(g)):
            stale.append(event)
    return stale


# --- can a user-scope hook reach the package at all? --------------------------
#
# lypning-capture.sh finds the package one of four ways: the `lypning` console
# script, the session's own checkout (`$CLAUDE_PROJECT_DIR/src`), a tree pinned
# by `$LYPNING_PYTHONPATH`, or a bare `python3 -m lypning`. At project scope in
# a checkout the second always works. At user scope, in somebody else's
# repository, it never does — and when none of the other three does either, the
# hook answers the protocol line, exits 0, and records nothing, forever, by
# invariant 5. That is a successful-looking install of nothing, the same
# failure `_path_warning` exists to name for a shim that is not on PATH.


def _is_console_script(path: str) -> bool:
    """A ``#!`` text file, as a console script is — not the Rust core.

    The name ``lypning`` can resolve to the engine binary (``~/.lypning/bin``),
    which reads ``hook`` as a script path and fails; the script's first arm
    then falls through, so that resolution is not an arm that works.
    """
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"#!"
    except OSError:
        return False


def _imports_lypning(python: str) -> bool:
    """Can this interpreter ``import lypning`` in a hook's environment?

    Spawned from a neutral directory, because ``python3 -m`` puts the working
    directory on ``sys.path`` and a probe run from a checkout's ``src`` would
    find the package the hook, running from another repository, never will.
    ``LYPNING_CAPTURE=0`` so that a ``python3`` which is our own shim does not
    log the probe as a program somebody ran.
    """
    env = dict(os.environ)
    env["LYPNING_CAPTURE"] = "0"
    try:
        proc = subprocess.run(
            # Not the working directory's entry (a stray `lypning/` dir in the
            # temp dir is no installation), and not a namespace package: a
            # spec with no origin is a directory, not the package.
            [python, "-c", "import importlib.util, sys; "
                           "sys.path[:] = [p for p in sys.path if p not in ('', '.')]; "
                           "s = importlib.util.find_spec('lypning'); "
                           "sys.exit(0 if s is not None and s.origin else 1)"],
            cwd=tempfile.gettempdir(), env=env, capture_output=True, timeout=10,
            check=False)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return proc.returncode == 0


def dispatch_arms(pythonpath: Optional[str] = None) -> List[str]:
    """The arms of lypning-capture.sh a hook fired OUTSIDE a checkout can reach.

    Read-only: it looks at PATH and at files, and spawns ``python3`` once with
    capture off. The answer is about THIS environment, which is the nearest
    thing available to the one the agent will launch hooks from — a shell that
    starts the agent with a different PATH gets a different answer, which is why
    the warning names what was checked. Empty means inert.
    """
    arms: List[str] = []
    exe = shutil.which("lypning")
    if exe and _is_console_script(exe):
        arms.append("lypning on PATH (%s)" % exe)
    pin = pythonpath or os.environ.get("LYPNING_PYTHONPATH", "").strip()
    if pin and (Path(pin).expanduser() / "lypning" / "__init__.py").is_file():
        arms.append("LYPNING_PYTHONPATH (%s)" % pin)
    python = shutil.which("python3")
    if python and _imports_lypning(python):
        arms.append("python3 -m lypning (%s)" % python)
    return arms


INERT_FIX = ("`uv tool install <lypning checkout>` to put `lypning` on PATH, or "
             "export LYPNING_PYTHONPATH=<checkout>/src in the shell that starts "
             "the agent")


def _inert_warning(hooks_dir: Path, pythonpath: Optional[str]) -> Optional[Action]:
    """A user-scope capture hook that no dispatch arm can reach, as a warning."""
    if dispatch_arms(pythonpath):
        return None
    return Action(
        "skip", hooks_dir,
        "WARNING: INERT — outside a lypning checkout this hook reaches no copy of "
        "the package (no `lypning` console script on PATH, no LYPNING_PYTHONPATH, "
        "`python3 -c 'import lypning'` fails) and will record nothing — fix: %s"
        % INERT_FIX, "hook")


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


#: The ``LYPNING_PYTHONPATH=<dir> `` prefix :func:`_hook_command` may put on a
#: command, in any quoting :func:`shlex.quote` can produce.
_PIN_PREFIX = re.compile(r"""^LYPNING_PYTHONPATH=(?:'[^']*'|"[^"]*"|[^\s'"]+)+\s+""")


def _unpinned(command: Any) -> Any:
    """``command`` without a leading ``LYPNING_PYTHONPATH=…`` assignment.

    Two spellings of one hook — pinned and not, or pinned to two trees — are
    the SAME hook for "is it already registered": registered twice, one Bash
    call fires the capture script twice and logs every program twice.
    """
    return _PIN_PREFIX.sub("", command, count=1) if isinstance(command, str) else command


def _pin_of(command: Any) -> Optional[str]:
    """The directory a registered command pins as ``LYPNING_PYTHONPATH``."""
    if not isinstance(command, str):
        return None
    m = _PIN_PREFIX.match(command)
    if not m:
        return None
    try:
        words = shlex.split(m.group(0))
    except ValueError:
        return None
    return words[0].split("=", 1)[1] if words and "=" in words[0] else None


def _registered_pin(settings: Dict[str, Any]) -> Optional[str]:
    """The pin on our registered PreToolUse command, if it carries one.

    What a hook fired from that entry actually reaches: a pinned install is
    NOT inert just because the shell asking has no ``$LYPNING_PYTHONPATH``.
    """
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict) or not isinstance(hooks.get("PreToolUse"), list):
        return None
    for g in hooks["PreToolUse"]:
        for c in _commands_of(g):
            if isinstance(c, str) and OUR_MARK in c.lower() and _pin_of(c):
                return _pin_of(c)
    return None


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
        bare = _unpinned(command)
        if any(bare == _unpinned(c) for g in groups for c in _commands_of(g)):
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
                return Action("skip", dest, "identical", component, src, present=True)
        except OSError:
            pass
        return Action("write", dest, "overwrite", component, src)
    return Action("write", dest, "new", component, src)


def _path_warning() -> Optional[Action]:
    """The one failure a shim install cannot detect by looking at its own files.

    A shim that is written but never reached reads as a successful install for
    as long as it takes somebody to wonder why the corpus is empty (shim.py).
    `status` and `doctor` say so; so must the command that just installed it,
    which is the moment the user is actually looking.
    """
    problem = shim_module.path_problem()
    if not problem:
        return None
    return Action("skip", paths.bin_dir(),
                  "WARNING: %s — fix: export PATH=\"%s:$PATH\""
                  % (problem, paths.bin_dir()), "shim")


def plan_install(
    project: Path | str | None = None,
    *,
    scope: str = "project",
    shim: bool = True,
    hooks: bool = True,
    skill: bool = True,
    pythonpath: Optional[str] = None,
) -> Plan:
    """Compute the whole install. **Writes nothing.**

    ``pythonpath`` pins a source tree (a directory holding ``lypning/``) onto
    every hook command, for a user-scope install with no ``lypning`` on PATH.
    A directory that holds no package is refused with a warning rather than
    pinned: a pin that points nowhere is an inert hook that looks configured.
    """
    want_shim, want_hooks, want_skill = shim, hooks, skill
    proj = _project(project)
    root = claude_dir(proj, scope)
    actions: List[Action] = []
    settings_path = root / "settings.json"
    diff: List[str] = []
    if pythonpath:
        pythonpath = str(Path(pythonpath).expanduser().resolve())
        if not (Path(pythonpath) / "lypning" / "__init__.py").is_file():
            actions.append(Action(
                "skip", Path(pythonpath),
                "WARNING: no lypning/__init__.py here — not pinned", "hook"))
            pythonpath = None

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
        scripts = _scripts_for(scope, _available_scripts(hooks_src))
        if not scripts:
            # A wheel without the shell hooks is a supported shape: the CLI
            # entry points (`lypning hook …`) do the same work, one exec later.
            actions.append(Action(
                "skip", dest_root,
                "no hook scripts in %s — wiring the `lypning hook` CLI entry points instead"
                % hooks_src, "hook"))
        for name in scripts:
            actions.append(_file_action(hooks_src / name, dest_root / name, "hook"))

        entries = hook_entries(scope, scripts, pythonpath)
        probe_pin = pythonpath
        before, err = load_settings(settings_path)
        if err:
            actions.append(Action("skip", settings_path, err + " — refusing to touch it", "settings"))
        else:
            stale = _stale_scope_entries(before, scope)
            if stale:
                actions.append(Action(
                    "skip", settings_path,
                    "WARNING: %s scope still holds lypning %s entr%s from an older "
                    "install, which this scope no longer registers — `lypning "
                    "uninstall%s` and install again to drop %s"
                    % (scope, "/".join(stale), "y" if len(stale) == 1 else "ies",
                       " --user" if scope == "user" else "",
                       "it" if len(stale) == 1 else "them"), "settings"))
            after, added = merge_hooks(before, entries)
            registered = _registered_pin(before)
            if pythonpath and not added and registered != pythonpath:
                actions.append(Action(
                    "skip", settings_path,
                    "WARNING: the capture hook is already registered %s — not pinned "
                    "again (two entries would log every call twice); `lypning "
                    "uninstall%s` and install again to change it"
                    % ("pinned to %s" % registered if registered else "without a pin",
                       " --user" if scope == "user" else ""), "settings"))
            if not pythonpath and not added:
                # What the existing entry reaches is what the INERT check asks.
                probe_pin = registered
            if not added:
                actions.append(Action("skip", settings_path,
                                      "all %d hook entries already present" % len(entries)
                                      if len(entries) != 1 else
                                      "the hook entry is already present",
                                      "settings", present=True))
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

        if scope == "user":
            warning = _inert_warning(dest_root, probe_pin)
            if warning is not None:
                actions.append(warning)

    if want_shim:
        actions.extend(shim_actions())

    return Plan(actions, proj, scope, settings_path, diff, pythonpath)


def shim_actions() -> List[Action]:
    """The shim half of an install, which is the same for every harness.

    Extracted so the harness modules call it rather than each restating what a
    stale shim, an absent one and a foreign one respectively mean. The shim is
    the one piece of wiring that was already harness-agnostic: it is a file on
    ``$PATH``, and nothing about it knows who asked.
    """
    actions: List[Action] = []
    for st in shim_module.status():
        if st.state == "current":
            actions.append(Action("skip", st.path, "shim already current", "shim", present=True))
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
    warning = _path_warning()
    if warning is not None:
        actions.append(warning)
    return actions



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
                scripts = _scripts_for(plan.scope, _available_scripts(
                    paths.HOOKS_SRC, a.path.parent / "hooks"))
                after, added = merge_hooks(before, hook_entries(plan.scope, scripts,
                                                                plan.pythonpath))
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
        warning = _path_warning()
        if warning is not None:
            done.append(warning)
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


def harness_status(project: Path | str | None = None) -> Dict[str, Any]:
    """Every harness's wiring, one entry per name in :data:`harness.NAMES`.

    Each module is called behind its own try/except: a harness module that
    raises is a status line that says so, never a `lypning status` that fails.
    """
    from . import harness

    out: Dict[str, Any] = {}
    for name in harness.NAMES:
        try:
            out[name] = harness.load(name).status(project)
        except Exception as e:  # a broken module must not take down status
            out[name] = {"name": name, "error": str(e)}
    return out


def status(project: Path | str | None = None) -> dict:
    """Everything a `lypning status` needs, JSON-serialisable, read-only.

    ``out["scopes"]`` is **frozen as the Claude view** and stays that way. Both
    `cli._uninstall_preview` and `cli._doctor_checks` index
    ``scopes[scope]["hooks"]`` and ``["settings_error"]``, as does every
    `--json` consumer outside this tree, so the harness dimension is a sibling
    key — ``out["harnesses"]`` — rather than a reshape of that one. The Claude
    entry there restates what ``scopes`` already says; the duplication is the
    price of not breaking readers, and is cheaper than the break.
    """
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
        # Whether a user-scope hook can reach the package at all. Asked only
        # when one is registered — it costs a python3 spawn — and never at
        # project scope, where the checkout arm is the answer in the one kind
        # of repository a project install is for.
        reach = (dispatch_arms(_registered_pin(settings))
                 if scope == "user" and present else None)
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
            "reach": reach,
            "inert": reach is not None and not reach,
        }

    log = paths.log_path()
    # "Absent" and "there but unusable" have the same symptom — an empty corpus
    # — and only one of them is the normal state of a fresh install. A log that
    # is a directory, or that this user cannot read, is reported as the failure
    # it is rather than as "not created yet" (shim.py holds the same rule for
    # a shim that is installed but shadowed).
    log_error = None
    if log.exists() and not log.is_file():
        log_error = "%s exists but is not a file — move it aside, or set $LYPNING_LOG" % log
    elif log.is_file() and not os.access(str(log), os.R_OK):
        log_error = "%s is not readable — fix its permissions, or set $LYPNING_LOG" % log
    out["log"] = {"path": str(log), "exists": log.is_file(),
                  "error": log_error,
                  "bytes": log.stat().st_size if log.is_file() else 0}
    out["harnesses"] = harness_status(proj)
    out["shim"] = {
        "bin_dir": str(paths.bin_dir()),
        "source": str(paths.SHIM_SRC),
        "states": [s.as_dict() for s in shim_module.status()],
        "path_problem": shim_module.path_problem(),
    }
    out["engines"] = {name: (str(p) if p else None) for name, p in engines.available().items()}
    return out


# --- rendering ---------------------------------------------------------------

_SIGIL = {"write": "+", "merge": "~", "backup": "b", "remove": "-", "skip": "."}


def render_plan(plan: Plan) -> str:
    """The dry-run report: every action, then the settings.json diff."""
    out = [
        "project : %s%s" % (plan.project, "" if _is_git_worktree(plan.project)
                            else "  (not a git work tree — this is just the "
                                 "current directory; --project names another)"),
        # The config root is the settings file's directory for Claude, and for
        # the harnesses that have no settings file it is not this module's to
        # know — so it is named only when there is one to name. Printing
        # `.claude` under an opencode plan told the reader the install was
        # going somewhere it was not.
        "scope   : %s%s" % (plan.scope,
                            " (%s)" % plan.settings_path.parent
                            if plan.settings_path is not None else ""),
        "",
    ]
    if not plan.actions:
        out.append("nothing to do")
    for a in plan.actions:
        note = "  — %s" % a.note if a.note else ""
        out.append("%s %-7s %s%s" % (_SIGIL.get(a.kind, "?"), a.kind, a.path, note))
    changes = len(plan.changes)
    out.append("")
    summary = "%d change%s, %d already in place" % (
        changes, "" if changes == 1 else "s", len(plan.already))
    notes = len(plan.notes)
    if notes:
        # A warning counted as "already in place" is how a plan that will not
        # do what the user asked reads as a plan that is fine.
        summary += ", %d warning%s (the `.` line%s above)" % (
            notes, "" if notes == 1 else "s", "" if notes == 1 else "s")
    out.append(summary)
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
        if s.get("inert"):
            out.append("  reach    : INERT — outside a lypning checkout no arm reaches "
                       "the package, so these hooks record nothing — fix: %s" % INERT_FIX)
        elif s.get("reach"):
            out.append("  reach    : %s" % "; ".join(s["reach"]))
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
