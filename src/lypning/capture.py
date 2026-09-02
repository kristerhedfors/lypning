"""The Claude Code hooks: observe, and get out of the way.

Two feeds fill the capture log. The ``python-shim`` on ``$PATH`` catches every
invocation that actually reached an interpreter — nested ones, subshells,
pipelines, Makefiles, anything a script spawns. This module catches the other
half: the **command string**, before it runs.

That string is the only place some programs are visible AT ALL. A heredoc body
(``python3 <<'PY' … PY``) never appears in the shim's argv. A ``uv run``
wrapper resolves to an interpreter the shim may not be shadowing. A
Write-then-run pattern puts the program in a file that the shim only ever sees
as a path. And a command that fails before exec never reaches the shim at all.
Both feeds append to the same ``$LYPNING_LOG`` and :mod:`lypning.harvest`
merges them.

The invariant this module exists to hold: **the hook never blocks and never
decides permission.** It prints ``{"continue":true,"suppressOutput":true}`` and
exits 0 on every path, including its own failures. There is deliberately no
``permissionDecision`` field — answering ``allow`` here would bypass the normal
permission prompt for every Bash command in the session, which is a far bigger
change than a capture harness is allowed to make. With no ``hookSpecificOutput``
at all the dispatcher never assigns a permission behaviour and the normal flow
runs untouched.

COST. ``pre-tool-use`` runs before EVERY Bash tool call, and almost none of them
are python, so the no-match path has to be nearly free: the event is parsed, the
command screened, and nothing else is imported or opened. :mod:`lypning.harvest`
— the expensive half — is imported inside :func:`hook_stop` rather than at
module scope for exactly that reason. The pythonish screen is deliberately
BROADER than precise: a false positive costs one extra log line, a miss costs a
corpus entry that can never be recovered.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, TextIO

from . import paths

# The whole protocol response. `continue` keeps the tool call going;
# `suppressOutput` keeps the hook out of the transcript. Nothing else — see the
# module docstring on why there is no permissionDecision.
OK_RESPONSE = '{"continue":true,"suppressOutput":true}'

# Any of these in a Bash command means a python program may be in it. Ported
# from the shell hook unchanged, and kept broad on purpose: an over-match costs
# one wasted parse, a miss loses a corpus entry forever.
#
#   1. a bare `python`/`python3`/`python3.11` word,
#   2. `py -c` (the Windows launcher, which the first pattern would miss),
#   3. the runner wrappers, listed BY NAME rather than screening on " run "
#      alone — which every `npm run …` in a repo would otherwise trip,
#   4. a heredoc whose delimiter contains PY.
PYTHONISH = (
    re.compile(r"(?:^|[\s;&|(){}`$\"'=])python[0-9.]*(?:\s|$)"),
    re.compile(r"(?:^|[\s;&|(){}`$])py\s+-c(?:\s|$)"),
    re.compile(r"(?:^|[\s;&|(){}`$])(?:uv|pipx|poetry|hatch|pdm|rye)\s+run(?:\s|$)"),
    re.compile(r"<<-?\s*['\"]?(?:PY|PYTHON|PYEOF|EOFPY)\b"),
)

# A tool event is a few KiB of JSON. A Bash command can legitimately carry a
# large heredoc, but nothing useful arrives past this, and a hook that reads an
# unbounded stream into memory is a hook that can hang the tool call.
MAX_EVENT_BYTES = 4 * 1024 * 1024

#: The harnesses lypning knows how to be wired into. The value goes into every
#: record as ``host``, which is what makes "how many python one-liners does THIS
#: harness actually type" a number somebody can read off the log rather than a
#: thing we assert. It is a different namespace from the engine strings
#: (invariant 9): an engine is "lypning"/"lypning-mp"/"cpython", a host is who
#: asked.
HOSTS = ("claude", "opencode", "openhands")

#: Read in this order. ``LYPNING_SESSION_ID`` is ours and works under every
#: harness; the rest are the ones a harness sets for us, and are *read*, never
#: defined — the same posture ``$CLAUDE_PROJECT_DIR`` already has in paths.py.
#: The shim spells this same list, so both feeds tag one session identically;
#: they must be changed together or a session's records split into two tags.
SESSION_ENV = ("LYPNING_SESSION_ID", "CLAUDE_CODE_SESSION_ID",
               "CLAUDE_SESSION_ID", "OPENHANDS_SESSION_ID")


def capture_enabled() -> bool:
    """``LYPNING_CAPTURE=0`` disables both feeds. The hook still answers."""
    return os.environ.get("LYPNING_CAPTURE", "1").strip() != "0"


def harvest_enabled() -> bool:
    """``LYPNING_HARVEST=0`` keeps capturing but never harvests automatically."""
    return os.environ.get("LYPNING_HARVEST", "1").strip() != "0"


def _now() -> str:
    """UTC, milliseconds, ``Z`` — the shim's format, so one log has one clock."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def read_event(stream: Optional[TextIO] = None) -> Dict[str, Any]:
    """The hook event JSON from stdin. Never raises; ``{}`` on anything else.

    A hook that dies on a malformed payload fails the tool call it was only
    supposed to watch, so every failure here — no stdin, bytes instead of text,
    truncated JSON, a bare array — degrades to "no event".
    """
    if stream is None:
        stream = getattr(sys, "stdin", None)
    if stream is None:
        return {}
    try:
        raw = stream.read(MAX_EVENT_BYTES)
    except Exception:
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if not raw or not raw.strip():
        return {}
    try:
        obj = json.loads(raw)
    except ValueError:
        return {}
    return obj if isinstance(obj, dict) else {}


def looks_pythonish(command: str) -> bool:
    """Could this Bash command contain a python program?

    The precise filter, and the only one: the shell hook's ``case`` screen is a
    cheap pre-filter in front of it, so a loose screen can never put noise in
    the log. :mod:`lypning.harvest` re-applies this to transcript commands.
    """
    if not isinstance(command, str) or not command:
        return False
    return any(rx.search(command) for rx in PYTHONISH)


def _str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def session_env() -> Optional[str]:
    """The session id from the environment, whichever harness supplied it.

    Load-bearing rather than decorative: a sighting key is
    ``hook:<session>#<lineno>#<idx>``, and a line number's scope is one log in
    one container. With no session tag every producer collapses onto the tag
    ``invocations``, keys collide across machines and rotations, ``count``
    stops being stable, and every session writes the same ``unknown.jsonl`` —
    which reintroduces the one-file-per-tree merge conflict that per-session
    files exist to remove.
    """
    for name in SESSION_ENV:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def record_command(
    command: Optional[str],
    *,
    host: str,
    tool: str,
    session: Optional[str] = None,
    cwd: Optional[str] = None,
    description: Optional[str] = None,
    transcript: Optional[str] = None,
    exit_code: Optional[int] = None,
    run: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """One ``bash_command`` record for a python-ish command, or None.

    The single record builder, shared by every harness. Pulling the fields out
    of a harness's event is the caller's job — one small mapper per harness,
    below — which is what stops this function growing a per-harness branch
    every time a harness is added, and stops three copies of the record shape
    drifting apart from each other.

    ``harvest`` reads four fields (``kind``, ``command``, ``session``, ``ts``)
    and ignores the rest, so the extras here cost nothing downstream:
    :func:`harvest.host_counts` reads ``host``, and ``run``/``exit_code`` are
    ground truth a harness volunteered which the corpus path never sees.
    """
    if not isinstance(command, str) or not looks_pythonish(command):
        return None
    record = {
        "kind": "bash_command",
        "ts": _now(),
        "session": _str(session) or session_env(),
        "cwd": _str(cwd) or os.getcwd(),
        "tool": tool,
        "command": command,
        "description": _str(description),
        "transcript": _str(transcript),
        "host": host,
    }
    if isinstance(run, str) and run:
        record["run"] = run
    if isinstance(exit_code, int):
        record["exit_code"] = exit_code
    return record


def from_claude_event(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Claude Code ``PreToolUse``. Only ``tool_name == "Bash"`` records.

    Every other tool's input is someone else's data, and capturing it would put
    file contents the session never ran into a log that gets published.
    """
    if not isinstance(event, dict):
        return None
    tool = _str(event.get("tool_name")) or _str(event.get("toolName")) or ""
    if tool != "Bash":
        return None
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = event.get("toolInput")
    if not isinstance(tool_input, dict):
        return None
    return record_command(
        _str(tool_input.get("command")),
        host="claude",
        tool=tool,
        session=_str(event.get("session_id")) or _str(event.get("sessionId")),
        cwd=_str(event.get("cwd")),
        description=_str(tool_input.get("description")),
        transcript=(_str(event.get("transcript_path"))
                    or _str(event.get("transcriptPath"))),
    )


def from_openhands_event(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """OpenHands ``PostToolUse``. Only the ``terminal`` tool records.

    The tool name is ``terminal`` — the LLM-facing name the SDK derives in
    ``ToolDefinition.__init_subclass__`` and registers as the registry key.
    ``TerminalTool`` is the class name, is stale even in the SDK's own docstring
    examples, and would never match; a matcher spelled that way observes
    nothing, forever, and says nothing about it.

    ``PostToolUse`` rather than ``PreToolUse`` costs the commands that were
    typed and then denied — which the Claude feed deliberately keeps — and buys
    the observation: ``exit_code`` and output from a CPython run the user paid
    for anyway. Wiring both would write two records per call, and since
    ``harvest`` counts distinct occurrence keys, that would silently double the
    ``count`` that ``conformance --plan`` steers by.
    """
    if not isinstance(event, dict):
        return None
    tool = _str(event.get("tool_name")) or _str(event.get("toolName")) or ""
    if tool != "terminal":
        return None
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = event.get("toolInput")
    if not isinstance(tool_input, dict):
        return None
    response = event.get("tool_response")
    if not isinstance(response, dict):
        response = {}
    exit_code = response.get("exit_code")
    return record_command(
        _str(tool_input.get("command")),
        host="openhands",
        tool=tool,
        session=_str(event.get("session_id")) or _str(event.get("sessionId")),
        cwd=_str(event.get("working_dir")) or _str(event.get("workingDir")),
        exit_code=exit_code if isinstance(exit_code, int) else None,
    )


def from_opencode_event(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """An opencode tool event, as the plugin would spell one.

    The per-call record is written by the JavaScript plugin itself: its hooks
    run in-process in Bun, and spawning a python interpreter per bash call is
    exactly the cost that in-process hook exists to avoid. This mapper is here
    so the record shape has ONE definition — the plugin is tested against it,
    and anything that feeds an opencode-shaped event through the CLI lands on
    the same bytes.

    The exposed tool id is ``bash``. opencode's own source pins it that way for
    compatibility with existing plugins and saved permissions; ``shell`` is
    accepted only as a forward alias, and a mapper that matched ``shell`` alone
    would observe nothing.
    """
    if not isinstance(event, dict):
        return None
    tool = _str(event.get("tool")) or _str(event.get("toolID")) or ""
    if tool not in ("bash", "shell"):
        return None
    args = event.get("args")
    if not isinstance(args, dict):
        return None
    exit_code = event.get("exit_code")
    return record_command(
        _str(args.get("command")),
        host="opencode",
        tool=tool,
        session=_str(event.get("session")) or _str(event.get("sessionID")),
        cwd=_str(event.get("cwd")),
        description=_str(args.get("description")),
        exit_code=exit_code if isinstance(exit_code, int) else None,
        run=_str(event.get("callID")) or _str(event.get("run")),
    )


def record_bash_command(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Back-compatible alias for :func:`from_claude_event`.

    Kept because it is the name the Claude hook has always called and the name
    the tests name; the behaviour is unchanged.
    """
    return from_claude_event(event)


def _fallback_log() -> Path:
    """Where the record goes when ``$HOME`` is unwritable — the shim's fallback,
    spelled the same way so both feeds land in the same place."""
    tmp = os.environ.get("TMPDIR") or "/tmp"
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return Path(tmp) / "lypning-mp-{0}".format(uid) / "invocations.jsonl"


def append_record(rec: Dict[str, Any], log: Optional[Path] = None) -> bool:
    """Append one JSON line, best effort. True if it landed anywhere.

    Tries the configured log, then the per-uid tmp fallback, then gives up
    silently: a capture failure must never surface to the tool call. The write
    is a single ``write()`` of one line, which is what makes concurrent appends
    from the shim and the hook interleave as whole records rather than shred
    each other.
    """
    candidates = []
    try:
        candidates.append(Path(log) if log is not None else paths.log_path())
    except Exception:
        pass
    candidates.append(_fallback_log())
    try:
        line = json.dumps(rec, separators=(",", ":"), ensure_ascii=False) + "\n"
    except (TypeError, ValueError):
        return False
    for target in candidates:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(str(target), "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line)
            return True
        except OSError:
            continue  # unwritable — try the next candidate
    return False


def _respond(stdout: Optional[TextIO] = None) -> int:
    """Print the response and return 0. Cannot fail the caller either."""
    if stdout is None:
        stdout = getattr(sys, "stdout", None)
    try:
        if stdout is not None:
            stdout.write(OK_RESPONSE + "\n")
            stdout.flush()
    except Exception:
        pass
    return 0


def hook_pre_tool_use(stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> int:
    """PreToolUse(Bash): log the command string, allow nothing, block nothing.

    Always 0, always the same one line of stdout. Everything between is wrapped
    — a hook that raised would turn a capture bug into a failed tool call.
    """
    try:
        if capture_enabled():
            rec = record_bash_command(read_event(stdin))
            if rec is not None:
                append_record(rec)
    except Exception:
        pass
    return _respond(stdout)


def hook_stop(stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> int:
    """Stop: publish this session's sightings into the tree before teardown.

    The log lives outside the repository and these containers are ephemeral, so
    without this the evidence dies with the container — which is precisely how
    upstream captured, harvested and threw away 17 sessions of python
    (docs/CAPTURE.md). The export is a union by key, so firing on every turn
    boundary is idempotent and a session that ran no python writes nothing.

    Stop fires at every turn boundary, so the no-work path bails before
    importing the harvester: an empty or missing log is the overwhelmingly
    common case in a session that never touched python.
    """
    try:
        event = read_event(stdin)
        if capture_enabled() and harvest_enabled():
            log = paths.log_path()
            if log.is_file() and log.stat().st_size > 0:
                from . import harvest

                cwd = _str(event.get("cwd"))
                project = paths.project_dir(cwd) if cwd else None
                harvest.export_sightings(project, quiet=True)
    except Exception:
        pass
    return _respond(stdout)


# --- OpenHands ---------------------------------------------------------------
#
# OpenHands dispatches the same six event names Claude Code does and reads the
# same `{"continue":true,"suppressOutput":true}` back, so these entry points are
# the Claude ones with a different mapper. Its exit-code semantics are NOT the
# Unix convention, and that is what makes invariant 5 sharper here than
# anywhere else: `0` proceeds, **`2` blocks the agent**, and any other non-zero
# is logged and proceeds. Stdout is additionally parsed as JSON, where
# `{"decision":"deny"}` and `{"continue":false}` each block.
#
# So: every function below returns 0 and only 0 — a bare `return 2` from
# anywhere in this file is a blocked agent — and none of them emits a
# `decision` key. OpenHands would honour `"allow"`, which is exactly why we do
# not send it: it is the same power the Claude hook declines to take, and for
# the same reason.


def hook_openhands_post_tool_use(stdin: Optional[TextIO] = None,
                                 stdout: Optional[TextIO] = None) -> int:
    """PostToolUse(terminal): log the command and what it exited with."""
    try:
        if capture_enabled():
            rec = from_openhands_event(read_event(stdin))
            if rec is not None:
                append_record(rec)
    except Exception:
        pass
    return _respond(stdout)


def hook_openhands_session_start(stdin: Optional[TextIO] = None,
                                 stdout: Optional[TextIO] = None) -> int:
    """SessionStart: hand the agent the routing paragraph and the engine state.

    Capture is automatic; routing is not. The shim logs and then execs real
    CPython, so it delivers no speedup at all — speed needs the agent to type
    ``lypning``, and this is where it is told to.

    The engine line is not decoration. With nothing built, every program falls
    through to CPython, which looks exactly like a working mixture with
    disappointing numbers, and the agent will describe a routing decision that
    never happened.

    ``additionalContext`` is documented for ``PreToolUse`` and read generically
    by the executor, but it has **not** been observed on ``SessionStart``. It
    is harmless if ignored — an unrecognised key beside a ``continue`` that is
    understood — so it ships, with docs/HARNESSES.md saying it is unverified
    and how to prove it.
    """
    try:
        read_event(stdin)
        payload = json.dumps(
            {"continue": True, "additionalContext": agent_context()},
            separators=(",", ":"), ensure_ascii=False,
        )
    except Exception:
        return _respond(stdout)
    if stdout is None:
        stdout = getattr(sys, "stdout", None)
    try:
        if stdout is not None:
            stdout.write(payload + "\n")
            stdout.flush()
    except Exception:
        pass
    return 0


def hook_openhands_session_end(stdin: Optional[TextIO] = None,
                               stdout: Optional[TextIO] = None) -> int:
    """SessionEnd: fold this session's log into the tree before it is gone.

    Must be wired **synchronously**: the SDK terminates outstanding async hook
    processes at session end, so an async harvest is a harvest that gets killed
    partway. It can also fire mid-conversation when hooks are re-merged, so the
    export has to be idempotent — ``export_sightings`` is a union by key and
    already is.

    Best-effort by construction: it runs only if the conversation closes
    cleanly. That is why ``PostToolUse`` appends durably as it goes and this is
    a roll-up rather than the only write.
    """
    try:
        event = read_event(stdin)
        if capture_enabled() and harvest_enabled():
            log = paths.log_path()
            if log.is_file() and log.stat().st_size > 0:
                from . import harvest

                cwd = _str(event.get("working_dir")) or _str(event.get("cwd"))
                project = paths.project_dir(cwd) if cwd else None
                harvest.export_sightings(project, quiet=True)
    except Exception:
        pass
    return _respond(stdout)


# --- opencode ----------------------------------------------------------------


def hook_opencode_context(stdin: Optional[TextIO] = None,
                          stdout: Optional[TextIO] = None) -> int:
    """The routing paragraph and engine state, as plain text on stdout.

    Nothing in opencode reads this hook's stdout as a protocol response — the
    JavaScript plugin reads it as text and appends it to the bash tool's
    description. So this is the one entry point here that does not print
    ``OK_RESPONSE``, and it still cannot fail: an exception yields an empty
    string and the plugin falls back to its own baked-in copy.
    """
    try:
        read_event(stdin)
        text = agent_context()
    except Exception:
        text = ""
    if stdout is None:
        stdout = getattr(sys, "stdout", None)
    try:
        if stdout is not None:
            stdout.write(text + "\n")
            stdout.flush()
    except Exception:
        pass
    return 0


def routing_prompt() -> str:
    """The injectable paragraph, read from the shipped asset.

    One asset, every harness, so the thing that was measured cannot drift into
    three variants. Empty string if the asset did not ship — a missing prompt
    costs coverage, never a session.
    """
    try:
        return paths.ROUTING_PROMPT.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def engine_state_line() -> str:
    """Which engines are actually built, as one sentence.

    The same composition the Claude ``SessionStart`` hook builds inline, in
    Python rather than in a heredoc, so the two new harnesses do not each grow
    their own copy of it. Empty string if it cannot be determined — a session
    is never failed over a status line.
    """
    try:
        from . import engines

        from . import __version__ as version
    except Exception:
        return ""
    try:
        have = engines.available()
        tiers = [e for e in engines.ENGINE_ORDER if e != engines.CPYTHON]
        built = [e for e in tiers if have.get(e)]
        missing = [e for e in tiers if not have.get(e)]
        parts = ["built: " + (", ".join(built) if built else "none")]
        if missing:
            parts.append("not built: " + ", ".join(missing))
        real = have.get(engines.CPYTHON)
        parts.append("cpython: " + (str(real) if real else "not found"))
        line = "lypning %s — %s." % (version, "; ".join(parts))
        if not built:
            line += (" Nothing to route to, so every program falls through to"
                     " CPython and any speed claim is meaningless: run `lypning"
                     " build` first.")
        elif engines.MICROPYTHON in missing:
            line += (" The lypning-mp tier needs a build with network access"
                     " (`lypning build --micropython`) and is absent by"
                     " default; the mixture works without it, one tier"
                     " shallower.")
        return line
    except Exception:
        return ""


def agent_context() -> str:
    """The routing paragraph plus one line of which engines are actually built.

    Invariant 8: this returns the string; the hook entry points and cli.py are
    what write it anywhere.
    """
    parts = [routing_prompt(), engine_state_line()]
    return "\n\n".join(p for p in parts if p)
