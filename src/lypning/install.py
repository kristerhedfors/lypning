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

**A collect-only install is a first-class shape.** The engine tiers need a built
binary, a shim on ``$PATH`` and a skill; *collection* needs none of that — two
hooks and a directory to publish into. A repository that merely wants to
contribute the python its agents type gets exactly that: no shim shadowing its
``python3``, no skill spending its context window, no cargo. That cheapness is
not a convenience, it is the precondition for collecting from other repositories
at all, because a repo which does not use lypning will only run a collector it
does not have to adopt. Because it is a shape and not a subset, switching *into*
it removes the engine wiring a previous install left behind
(:func:`prune_hooks`) — a merge that only ever added would leave the shim being
installed every session under a plan that says "no shim".

**A publish directory is evidence, not a preference.** ``--sightings DIR`` is
carried in the hook command itself, which makes the settings file the only place
it is recorded — so an install that omits the flag READS IT BACK rather than
resetting it (:func:`configured_sightings`). Every other collision here is
resolved by "the last install wins", and this one is not: what loses is a
directory of already-published session files, which no later harvest would ever
look in again. Moving it stays possible, by saying so.

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
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

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

# The environment variable the hook commands carry their publish directory in.
# Named once because it is written into the command, parsed back out of it, and
# relied on to contain OUR_MARK so uninstall still matches a prefixed command.
SIGHTINGS_VAR = "LYPNING_SIGHTINGS"


@dataclass(frozen=True)
class HookSpec:
    """One hook we want present, and the two ways it can be spelled."""

    event: str
    matcher: Optional[str]
    scripts: Tuple[str, ...]
    fallback: str
    #: Does this hook serve COLLECTION, or engine wiring? The two are separable
    #: and a collect-only install keeps only the collecting ones — see the module
    #: docstring for why that separation is what makes collection portable.
    collect: bool = True


HOOKS: Tuple[HookSpec, ...] = (
    # SessionStart re-installs the shim: these containers are ephemeral and a
    # shim that was installed in a previous session is not on this one's PATH.
    # That is engine wiring, not collection — it is the one spec a collect-only
    # install drops, and the reason the field exists.
    HookSpec("SessionStart", None,
             ("lypning-session-start.sh", "lypning-install.sh", "lypning-shim.sh"),
             "lypning shim install", collect=False),
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
    #: The two collection choices travel WITH the plan, because :func:`apply`
    #: re-derives the hook entries from it rather than replaying the planned
    #: text. A plan that forgot them would dry-run as collect-only and then
    #: install the full set.
    collect_only: bool = False
    sightings: Optional[str] = None
    #: ``sightings`` was not asked for on this run — it was read back out of the
    #: hook commands already in settings.json. See :func:`plan_install` for why
    #: an omitted flag inherits rather than resets.
    sightings_inherited: bool = False
    #: The publish directory the hooks used BEFORE this run, when this run moves
    #: them somewhere else. A directory of already-published evidence is about to
    #: stop being read; the plan says so out loud.
    sightings_previous: Optional[str] = None

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
            "collect_only": self.collect_only,
            "sightings": self.sightings,
            "sightings_inherited": self.sightings_inherited,
            "sightings_previous": self.sightings_previous,
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


def sh_quote(value: str) -> str:
    """POSIX single-quoting for a value that then goes inside a JSON string.

    Two layers of escaping are in play and only one of them is done for us: the
    json module handles the JSON, nothing handles the shell. A sightings
    directory is a path the user chose, so it may contain a space — which would
    silently turn one hook command into a command plus an argument nobody asked
    for — and it may contain a quote. Single quotes protect everything except a
    single quote, which has to be closed, escaped and reopened; that is what the
    ``'"'"'`` is. Public because it is the kind of thing that is only ever wrong
    in the case nobody thought to try by hand.
    """
    return "'" + value.replace("'", "'\"'\"'") + "'"


def sh_unquote(text: str) -> str:
    """Read one shell word off the front of ``text``. The inverse of :func:`sh_quote`.

    Only ever applied to a word :func:`sh_quote` wrote, so the grammar it has to
    cover is small: a run of single-quoted chunks, double-quoted chunks (that is
    what the ``'"'"'`` escape leaves behind) and bare characters, ending at the
    first unquoted space. An unbalanced quote returns the empty string rather
    than a guess — a command we cannot parse is a command whose publish directory
    we must not claim to know, and the caller treats "" as "no prefix here".
    """
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "'\"":
            j = text.find(ch, i + 1)
            if j < 0:
                return ""
            out.append(text[i + 1:j])
            i = j + 1
        elif ch == " ":
            break
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def configured_sightings(settings: Dict[str, Any]) -> Optional[str]:
    """The publish directory the hooks in this settings file ALREADY use.

    Read back out of the command string because that is the only place it was
    ever written — a collector whose install is one merge into a file the repo
    already has does not get to keep a config file of its own (see
    :func:`hook_entries`). Which means the settings file is the state, and this
    is how a later install finds out what the earlier one chose.
    """
    for command in _our_commands(settings):
        head, sep, rest = command.partition("=")
        if sep and head.strip() == SIGHTINGS_VAR:
            value = sh_unquote(rest).strip()
            if value:
                return value
    return None


def _hook_id(command: str) -> Optional[str]:
    """Which of our hooks a command IS, whatever spelling it arrived in.

    A hook has several correct spellings — the copied script or the CLI entry
    point, with or without an ``LYPNING_SIGHTINGS=`` prefix — and the merge has
    to recognise all of them as the same hook. Matching on the command string
    alone would let a second spelling of a hook that is already installed land
    beside the first (see :func:`merge_hooks`). The event is a sufficient id:
    each spec owns one, and the merge only ever compares within one event.
    """
    low = command.lower()
    for spec in HOOKS:
        if spec.fallback in low or any(name in low for name in spec.scripts):
            return spec.event
    return None


def hook_entries(
    scope: str,
    scripts: Sequence[str],
    *,
    collect_only: bool = False,
    sightings: Optional[str] = None,
) -> List[Tuple[str, Optional[str], str]]:
    """The ``(event, matcher, command)`` triples this install wants present.

    ``collect_only`` keeps only the collecting specs; ``sightings`` names the
    directory the Stop hook publishes into.
    """
    where = (sightings or "").strip()
    out: List[Tuple[str, Optional[str], str]] = []
    for spec in HOOKS:
        if collect_only and not spec.collect:
            continue
        chosen = next((n for n in spec.scripts if n in scripts), None)
        command = _hook_command(scope, chosen, spec.fallback)
        if where:
            # The collecting repository chooses where published files land, and
            # says so in the hook command itself rather than in a config file of
            # its own — a collector whose install is one merge into a file that
            # already exists is one a foreign repo will actually accept.
            #
            # The prefix goes on every entry we write, not only the collecting
            # ones: the variable is inert where nothing reads it, and a rule with
            # an exception is a rule the next reader has to remember.
            #
            # Uninstall keeps working for free: `LYPNING_SIGHTINGS` contains
            # OUR_MARK, so a prefixed command matches strip_hooks by both halves
            # rather than by neither.
            command = "%s=%s %s" % (SIGHTINGS_VAR, sh_quote(where), command)
        out.append((spec.event, spec.matcher, command))
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


def _our_commands(settings: Dict[str, Any]) -> List[str]:
    """Every hook command in this settings object that is one of ours."""
    out: List[str] = []
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return out
    for groups in hooks.values():
        if not isinstance(groups, list):
            continue
        for g in groups:
            out.extend(c for c in _commands_of(g)
                       if isinstance(c, str) and OUR_MARK in c.lower())
    return out


def _find_ours(groups: Sequence[Any], ident: Optional[str]) -> Optional[Dict[str, Any]]:
    """The existing hook dict that is already our ``ident`` hook, if any."""
    if ident is None:
        return None
    for g in groups:
        if not isinstance(g, dict) or not isinstance(g.get("hooks"), list):
            continue
        for h in g["hooks"]:
            if not isinstance(h, dict):
                continue
            cmd = h.get("command")
            if isinstance(cmd, str) and OUR_MARK in cmd.lower() and _hook_id(cmd) == ident:
                return h
    return None


def merge_hooks(
    settings: Dict[str, Any],
    entries: Sequence[Tuple[str, Optional[str], str]],
) -> Tuple[Dict[str, Any], List[str]]:
    """Add our entries to a deep copy. Returns ``(new_settings, added_commands)``.

    Append-only for everything that is not already ours: an existing group is
    extended at the end and a new group goes at the end of the event's list, so
    nothing that was already in the file changes index. Insertion order of a
    dict is preserved by the copy, which is what keeps unrelated keys where the
    user put them.

    The one thing that is *replaced* rather than appended is an entry that is
    already ours, spelled differently — see below. That is not a cost to the
    user: the entry it overwrites is ours and the command overwriting it is the
    one they just asked for.
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
        mine = _find_ours(groups, _hook_id(command))
        if mine is not None:
            # Same hook, different spelling: a --collect-only install after a
            # plain one (the command gains an LYPNING_SIGHTINGS prefix), or a
            # wheel's CLI spelling after a checkout's script. Rewrite it in
            # place instead of appending. Appending would leave two Stop hooks
            # that BOTH harvest, publishing to two different directories on
            # every turn boundary, and nothing downstream could tell the user
            # which one their evidence went to — `status` would list both and
            # uninstall would remove both, so the duplication is silent for
            # exactly as long as it takes someone to wonder why. The last
            # install wins, and there is always exactly one of each of ours.
            mine["command"] = command
            added.append(command)
            continue
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
    """Remove every hook entry whose command mentions lypning. Nothing else.

    A collect-only install is removed by the same rule and needs no help: the
    ``LYPNING_SIGHTINGS='…'`` prefix carries OUR_MARK itself, so a prefixed
    command matches here twice over rather than falling off the end of uninstall.
    """
    return _drop_hooks(settings, lambda cmd: True)


def unwanted_events(collect_only: bool) -> Tuple[str, ...]:
    """The events whose hook this install shape does NOT want wired.

    A collect-only install is defined by what it leaves out, so the specs it
    leaves out are the specs it has to be able to take back out — see
    :func:`prune_hooks`.
    """
    if not collect_only:
        return ()
    return tuple(spec.event for spec in HOOKS if not spec.collect)


def prune_hooks(
    settings: Dict[str, Any],
    events: Sequence[str],
) -> Tuple[Dict[str, Any], List[str]]:
    """Remove OUR entries for hooks this install shape does not want.

    This is what stops ``--collect-only`` after a full install from being a lie.
    :func:`merge_hooks` only adds and rewrites, so without this the SessionStart
    entry survives — and that entry runs ``lypning shim install`` every session,
    which is a python3 shim on the PATH of a repository whose operator just
    asked, in as many words, for no shim. A plan that then printed "no shim, no
    skill" would be summarising the opposite of what the settings file does.

    Removal, not a warning, because the alternative costs the user more: leaving
    it means hand-editing someone else's JSON, and hand-editing a foreign repo's
    config is exactly the friction a collector cannot afford (module docstring).
    What removal costs is one ``lypning install`` to put it back, which is a
    command, and it is shown as a planned change with a diff before it happens.

    The hook *script* in ``.claude/hooks`` is left where it is. It is inert —
    nothing execs it once no command names it — and deleting a file on the way
    past is a cost, where removing the entry only undoes wiring we put there.
    ``uninstall`` is still the thing that removes files.
    """
    if not events:
        return copy.deepcopy(settings), []
    wanted = set(events)
    # By hook identity rather than by which event array the entry sits in: the
    # id survives every spelling (script or CLI, prefixed or not), and an entry
    # filed under the wrong event is still the hook we are dropping. A command
    # of ours we cannot identify is kept — never delete on a guess.
    return _drop_hooks(settings, lambda cmd: _hook_id(cmd) in wanted)


def _drop_hooks(
    settings: Dict[str, Any],
    drop: Callable[[str], bool],
) -> Tuple[Dict[str, Any], List[str]]:
    """Remove our hook entries for which ``drop(command)`` is true.

    Shared by :func:`strip_hooks` and :func:`prune_hooks` so the delicate part —
    which group survives an emptying, and which event array is deleted — has one
    implementation and cannot drift between uninstall and a shape change. Only
    commands carrying OUR_MARK are ever offered to ``drop``.
    """
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
                if isinstance(cmd, str) and OUR_MARK in cmd.lower() and drop(cmd):
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
    collect_only: bool = False,
    sightings: Optional[str] = None,
) -> Plan:
    """Compute the whole install. **Writes nothing.**"""
    want_shim, want_hooks, want_skill = shim, hooks, skill
    if collect_only:
        # Not "shim and skill happen to be off" — off is what collect-only MEANS,
        # so it is decided here rather than left to the caller to pass three
        # flags consistently. Nothing below this line needs an engine, a
        # toolchain or a context window.
        want_shim = want_skill = False
    proj = _project(project)
    root = claude_dir(proj, scope)
    actions: List[Action] = []
    settings_path = root / "settings.json"
    diff: List[str] = []

    # Read before deciding anything, because two of the decisions below depend on
    # what a previous install already wired.
    before, settings_err = load_settings(settings_path)
    already_publishing = None if settings_err else configured_sightings(before)

    asked = (sightings or "").strip() or None
    where = asked
    inherited = False
    if not want_hooks:
        # The publish directory is carried in the hook command and recorded
        # nowhere else, so a run that writes no hook entry cannot choose one and
        # cannot move one. A plan that resolved it anyway would print a `publish:`
        # line, and — over an install that already publishes elsewhere — a move
        # warning for a move that never happens: apply() leaves settings.json
        # byte-identical, the old directory keeps receiving, and the operator has
        # been told the opposite in the one report they were given to read first.
        # A flag that did nothing is said out loud instead, because a user who
        # typed --sightings and got silence reads the silence as agreement.
        if asked:
            actions.append(Action(
                "skip", settings_path,
                "WARNING: --sightings %s does nothing here — the publish directory "
                "rides on the hook command and --no-hooks writes none, so %s"
                % (asked,
                   ("the hooks already wired keep publishing to %s" % already_publishing)
                   if already_publishing else
                   "nothing publishes until an install wires the hooks"),
                "settings"))
        where = None
    elif where is None and already_publishing:
        # An OMITTED --sightings is not a request to move the evidence. The
        # merge's "last install wins" rule is right for a spelling — a script
        # path, a CLI entry point — and wrong here, because the losing side is a
        # directory of published files: reset the prefix and the Stop hook starts
        # writing to tests/corpus/sightings (creating a `tests/` in a repo that
        # deliberately has none), while every session file already published
        # under the old directory becomes invisible to every later harvest.
        # Nobody typed a flag to cause that. So the configured location wins over
        # the default, and only an explicit --sightings moves it.
        where = already_publishing
        inherited = True
    moved_from = already_publishing if (
        want_hooks and asked and already_publishing
        and asked != already_publishing) else None
    if moved_from:
        # An explicit --sightings IS a request to move, so it is honoured — but
        # the files under the old directory do not move with it and nothing reads
        # them afterwards, which is the kind of thing an operator wants to hear
        # before the write rather than after.
        actions.append(Action(
            "skip", settings_path,
            "WARNING: the hooks publish to %s today; this moves them to %s. "
            "Files already published under %s stay there and no later harvest "
            "reads them — move them across, or re-run with --sightings %s"
            % (moved_from, asked, moved_from, moved_from), "settings"))

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

        entries = hook_entries(scope, scripts, collect_only=collect_only, sightings=where)
        copied = scripts
        if collect_only:
            # Copy only the scripts a wired entry actually names. A full install
            # keeps copying everything in the source directory, because a helper
            # script that no command names may still be sourced by one that is —
            # but a collect-only install must not leave the session-start script
            # in a tree where nothing runs it, which would read as a shim install
            # that failed rather than one that was never asked for.
            copied = [n for n in scripts if any(n in cmd for _, _, cmd in entries)]
        for name in copied:
            actions.append(_file_action(hooks_src / name, dest_root / name, "hook"))

        if settings_err:
            actions.append(Action("skip", settings_path,
                                  settings_err + " — refusing to touch it", "settings"))
        else:
            # Prune first, then merge onto the pruned copy: the entries this
            # shape does not want have to be gone before we count what is
            # "already present", or a collect-only install over a full one plans
            # zero changes while leaving the shim wiring in place.
            pruned, dropped = prune_hooks(before, unwanted_events(collect_only))
            after, added = merge_hooks(pruned, entries)
            if not added and not dropped:
                actions.append(Action("skip", settings_path,
                                      "all %d hook entries already present" % len(entries),
                                      "settings", present=True))
            else:
                backup = settings_path.with_name(settings_path.name + SETTINGS_BACKUP_SUFFIX)
                if settings_path.is_file() and not backup.exists():
                    actions.append(Action("backup", backup, "copy of the current settings.json",
                                          "settings", settings_path))
                # Appended and rewritten entries are both changes to the file but
                # they are not the same news, and a reader who has just switched
                # a repo between install shapes needs to see which happened.
                # Counted against `pruned`, not `before`: a removal is its own
                # news and must not be netted off against an addition.
                fresh = len(_our_commands(after)) - len(_our_commands(pruned))
                rewritten = len(added) - fresh
                parts = []
                if fresh:
                    parts.append("add %d hook entr%s" % (fresh, "y" if fresh == 1 else "ies"))
                if rewritten:
                    parts.append("rewrite %d of ours in place" % rewritten)
                if dropped:
                    parts.append("remove %d engine-wiring entr%s this shape does "
                                 "not use (%s)" % (len(dropped),
                                                   "y" if len(dropped) == 1 else "ies",
                                                   ", ".join(sorted(set(
                                                       _hook_id(c) or "?" for c in dropped)))))
                note = ", ".join(parts) or "update %d hook entries" % len(added)
                if not settings_path.is_file():
                    note += " (creating the file)"
                actions.append(Action("merge", settings_path, note, "settings"))
                diff = settings_diff(before, after, settings_path)

    if want_shim:
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

    return Plan(actions, proj, scope, settings_path, diff,
                collect_only=collect_only, sightings=where,
                sightings_inherited=inherited, sightings_previous=moved_from)



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
                # Same order as the plan — prune, then merge onto the pruned
                # copy — because `plan.sightings` is already the RESOLVED
                # directory and `plan.collect_only` is the shape, so this
                # re-derivation reaches the same file the diff showed.
                pruned, dropped = prune_hooks(before, unwanted_events(plan.collect_only))
                after, added = merge_hooks(pruned, hook_entries(
                    plan.scope, scripts,
                    collect_only=plan.collect_only, sightings=plan.sightings))
                if not added and not dropped:
                    done.append(Action("skip", a.path, "already present", "settings"))
                    continue
                paths.ensure_dir(a.path.parent)
                a.path.write_text(dumps_settings(after), encoding="utf-8")
                # "wrote", not "added": since the merge supersedes an entry that
                # is already ours, some of these may have replaced a differently
                # spelled one rather than arrived new. A removal is reported
                # separately — an operator who reads only this line has to learn
                # that something left the file, not just that something arrived.
                note = "wrote %d hook entr%s" % (
                    len(added), "y" if len(added) == 1 else "ies")
                if dropped:
                    note += ", removed %d not used by this shape (%s)" % (
                        len(dropped), ", ".join(sorted(set(
                            _hook_id(c) or "?" for c in dropped))))
                done.append(Action("merge", a.path, note, "settings"))
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
        "project : %s%s" % (plan.project, "" if _is_git_worktree(plan.project)
                            else "  (not a git work tree — this is just the "
                                 "current directory; --project names another)"),
        "scope   : %s (%s)" % (plan.scope, claude_dir(plan.project, plan.scope)),
    ]
    # An absent shim and an absent skill are the POINT of a collect-only plan,
    # and a plan is read by someone deciding whether to run it. Two lines here
    # are the difference between "this install is deliberately small" and "this
    # install looks like it is missing half of itself".
    #
    # This line is a claim about the settings file, so prune_hooks is what makes
    # it true: whatever a previous install wired, the plan below removes the
    # engine-wiring entries, and the removal is one of the actions printed.
    if plan.collect_only:
        out.append("mode    : collect-only — capture and publish; "
                   "no shim, no skill, no engine needed")
    if plan.sightings:
        # Where the evidence goes is the one setting that is expensive to get
        # wrong quietly, so the line says whether this run CHOSE it or merely
        # kept it, and names the flag that changes it either way.
        if plan.sightings_inherited:
            source = "already wired in settings.json; kept — --sightings DIR moves it"
        elif plan.sightings_previous:
            source = "MOVED from %s — see the warning below" % plan.sightings_previous
        else:
            source = "%s, set on each hook command" % SIGHTINGS_VAR
        out.append("publish : %s  (%s)" % (plan.sightings, source))
    out.append("")
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
