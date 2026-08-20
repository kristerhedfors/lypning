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


def record_bash_command(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One log record for a python-ish Bash command, or None.

    Only ``tool_name == "Bash"`` produces a record: every other tool's input is
    someone else's data, and capturing it would put file contents the session
    never ran into a log that gets published.
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
    command = _str(tool_input.get("command"))
    if not command or not looks_pythonish(command):
        return None
    return {
        "kind": "bash_command",
        "ts": _now(),
        "session": (
            _str(event.get("session_id"))
            or _str(event.get("sessionId"))
            or _str(os.environ.get("CLAUDE_CODE_SESSION_ID"))
        ),
        "cwd": _str(event.get("cwd")) or os.getcwd(),
        "tool": tool,
        "command": command,
        "description": _str(tool_input.get("description")),
        "transcript": _str(event.get("transcript_path")) or _str(event.get("transcriptPath")),
    }


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
