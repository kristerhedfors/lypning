"""The Codex CLI feed: shell calls read back out of its rollout files, offline.

Codex has no hook this package can register, so nothing captures its python as
it runs. What it does keep is a rollout: every session is one append-only JSONL
file under ``~/.codex/sessions/YYYY/MM/DD/``, holding each model response item
in order. The shell calls are in there, and so is the model that made them.
This module reads those files — **read-only, never written, never moved** — and
turns the python-ish calls into the same record shape the hooks write, tagged
``host="codex"``. It is the same posture :func:`harvest.scan_transcripts` has
toward Claude Code's transcripts: a backwards-reaching feed, best-effort, that
a harvest must not care about when the directory is absent.

Where a command lives in a rollout, as observed in this machine's rollouts on
2026-09-22 (read in aggregate; no command text was read to write this):

* ``response_item`` / ``function_call`` named ``exec_command`` — ``arguments``
  is a JSON *string* whose ``cmd`` is the shell command;
* ``response_item`` / ``custom_tool_call`` named ``exec`` — ``input`` is a
  JavaScript program that calls ``tools.exec_command({cmd: "…"})``, one or
  more times. The command is a JS string LITERAL inside that source, escapes
  and all, so it is decoded (:func:`js_string_literals`) before the extractor
  sees it; read raw, every ``\\n`` in a heredoc is two characters and nothing
  after the first line parses;
* ``function_call`` named ``shell`` and ``local_shell_call`` — an argv list,
  from older Codex versions; ``bash -lc SCRIPT`` is unwrapped to ``SCRIPT``.

The model is the ``model`` of the most recent ``turn_context`` before the call,
in file order: a turn context opens every turn and names the model serving it.

**The key is ``call_id``.** A forked or resumed Codex session copies its
parent's history into a NEW rollout file, so one call can appear in several
files; keyed by file and line it would count once per copy. Keyed by
``call_id`` — the id Codex itself pairs a call with its output by — a rescan
and a fork both collapse onto the first sighting, in path order, which is the
chronological original.

**Never mixed into Claude attribution.** A record here carries ``run`` (the
``call_id``), ``model`` and ``host``; it never carries ``tool_use_id`` or
``transcript``, the two fields the Claude model join reads, so no GPT-served
call can be resolved against a Claude transcript or counted as a Claude model.

Redaction is :func:`harvest.redact`, the Claude feed's, applied to every
command and every program before it leaves this module; a program whose
redaction leaves a credential-shaped residue is dropped, as the Claude feed
drops it (:func:`harvest.is_safe`).

Invariant 8: nothing here prints. :func:`collect` returns data; wiring it into
``lypning harvest`` belongs to :mod:`lypning.harvest` and :mod:`lypning.cli`.
"""

from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from .capture import looks_pythonish

HOST = "codex"
#: The ``source`` a harvested Codex program carries — its own feed, so a
#: sightings reader can tell it from the Claude ``hook`` and ``transcript``
#: feeds without reading ``host``.
SOURCE = "codex"

#: ``function_call`` names whose ``arguments`` carry a shell command.
_FUNCTION_CMD = ("exec_command",)
_FUNCTION_ARGV = ("shell", "container.exec")
#: ``custom_tool_call`` names whose ``input`` is a JS program calling exec.
_CUSTOM_JS = ("exec",)
_SHELLS = frozenset(["bash", "sh", "zsh", "dash"])


def sessions_root() -> Path:
    """``$LYPNING_CODEX_SESSIONS``, else Codex's own ``~/.codex/sessions``.

    ``$CODEX_HOME`` is honoured the way Codex honours it, so a user who moved
    Codex's state is read where it actually is.
    """
    env = os.environ.get("LYPNING_CODEX_SESSIONS", "").strip()
    if env:
        return Path(env).expanduser()
    home = os.environ.get("CODEX_HOME", "").strip()
    base = Path(home).expanduser() if home else Path(os.path.expanduser("~")) / ".codex"
    return base / "sessions"


# --- decoding a JS string literal --------------------------------------------

_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
                   "v": "\v", "0": "\0"}
#: What a backslash may precede to continue a JS string onto the next line.
_LINE_BREAKS = "\r\n" + chr(0x2028) + chr(0x2029)


def _read_literal(src: str, i: int) -> Tuple[Optional[str], int]:
    """Decode the JS string literal whose opening quote is ``src[i]``.

    Returns ``(text, index after the closing quote)``, or ``(None, …)`` when it
    is not a literal this can decode statically: unterminated, or a template
    literal with a ``${…}`` substitution, whose value exists only at run time.
    Not ``json.loads``: JS accepts ``'…'``, backticks, ``\\x41``, ``\\u{1F600}``
    and a backslash-newline continuation, none of which JSON does.
    """
    quote = src[i]
    out: List[str] = []
    j = i + 1
    n = len(src)
    while j < n:
        c = src[j]
        if c == quote:
            return "".join(out), j + 1
        if c == "$" and quote == "`" and j + 1 < n and src[j + 1] == "{":
            return None, j
        if c != "\\":
            out.append(c)
            j += 1
            continue
        if j + 1 >= n:
            break
        e = src[j + 1]
        j += 2
        if e in _SIMPLE_ESCAPES and not (e == "0" and j < n and src[j].isdigit()):
            out.append(_SIMPLE_ESCAPES[e])
        elif e == "x" and j + 2 <= n:
            try:
                out.append(chr(int(src[j:j + 2], 16)))
                j += 2
            except ValueError:
                out.append("x")
        elif e == "u" and j < n and src[j] == "{":
            end = src.find("}", j)
            try:
                out.append(chr(int(src[j + 1:end], 16)))
                j = end + 1
            except (ValueError, OverflowError):
                out.append("u")
        elif e == "u":
            try:
                code = int(src[j:j + 4], 16)
                j += 4
                # A surrogate pair is two \uXXXX escapes; join them, or a
                # non-BMP character arrives as two lone surrogates that no
                # encoder downstream will write.
                if 0xD800 <= code < 0xDC00 and src[j:j + 2] == "\\u":
                    low = int(src[j + 2:j + 6], 16)
                    if 0xDC00 <= low < 0xE000:
                        code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                        j += 6
                out.append(chr(code))
            except ValueError:
                out.append("u")
        elif e in _LINE_BREAKS:
            if e == "\r" and j < n and src[j] == "\n":
                j += 1  # a CRLF continuation is one line break
        else:
            out.append(e)  # \\ \" \' \` and any identity escape
    return None, n


def js_string_literals(src: str, prop: str = "cmd") -> List[str]:
    """Every string literal assigned to ``prop:`` in a JS source, decoded.

    ``tools.exec_command({cmd: "…"})`` puts the command in exactly that shape,
    and a ``Promise.all([...])`` puts several. A value that is not a literal —
    a variable, a concatenation, a template with a substitution — is skipped:
    what it holds is decided at run time, and guessing at it would put a
    program into the corpus that nobody ran.
    """
    found: List[str] = []
    if not isinstance(src, str) or prop not in src:
        return found
    n = len(src)
    i = 0
    while True:
        k = src.find(prop, i)
        if k < 0:
            return found
        i = k + len(prop)
        before = src[k - 1] if k > 0 else ""
        # `cmd:`, `"cmd":` and `'cmd':` — not `mycmd:` nor `x.cmd:`.
        if before in "\"'":
            if i < n and src[i] == before:
                i += 1
            before = src[k - 2] if k > 1 else ""
        if before and (before.isalnum() or before in "_$."):
            continue
        j = i
        while j < n and src[j] in " \t\r\n":
            j += 1
        if j >= n or src[j] != ":":
            continue
        j += 1
        while j < n and src[j] in " \t\r\n":
            j += 1
        if j < n and src[j] in "\"'`":
            text, i = _read_literal(src, j)
            if text is not None:
                found.append(text)


def _argv_command(argv: Any) -> Optional[str]:
    """An argv list as the shell command it runs: ``bash -lc S`` is ``S``."""
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return None
    if (len(argv) >= 3 and os.path.basename(argv[0]) in _SHELLS
            and argv[-2] in ("-c", "-lc", "-cl")):
        return argv[-1]
    return " ".join(shlex.quote(a) for a in argv)


def _commands_of(payload: Dict[str, Any]) -> Tuple[str, List[str], Optional[str]]:
    """``(tool, commands, workdir)`` for one ``response_item`` payload."""
    kind = payload.get("type")
    name = payload.get("name") if isinstance(payload.get("name"), str) else ""
    if kind == "function_call" and (name in _FUNCTION_CMD or name in _FUNCTION_ARGV):
        raw = payload.get("arguments")
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            return name, [], None
        if not isinstance(args, dict):
            return name, [], None
        workdir = args.get("workdir") if isinstance(args.get("workdir"), str) else None
        if name in _FUNCTION_CMD:
            cmd = args.get("cmd")
            if isinstance(cmd, list):
                cmd = _argv_command(cmd)
            return name, [cmd] if isinstance(cmd, str) and cmd else [], workdir
        cmd = _argv_command(args.get("command"))
        return name, [cmd] if cmd else [], workdir
    if kind == "custom_tool_call" and name in _CUSTOM_JS:
        return name, [c for c in js_string_literals(payload.get("input") or "") if c], None
    if kind == "local_shell_call":
        action = payload.get("action")
        if isinstance(action, dict):
            cmd = _argv_command(action.get("command"))
            wd = action.get("working_directory")
            return "local_shell_call", [cmd] if cmd else [], wd if isinstance(wd, str) else None
    return name, [], None


# --- the rollout scan ---------------------------------------------------------


@dataclass(frozen=True)
class CodexCall:
    """One python-ish shell command from a rollout, with what it is known by.

    ``command`` is RAW here, straight from the rollout; :func:`iter_calls`
    hands out a redacted copy and :func:`collect` extracts from the raw one and
    redacts what it extracted, so the extractor never tokenises a marker.
    """

    call_id: str
    index: int  # which command of this call — an `exec` program may run several
    command: str
    tool: str
    session: Optional[str]
    cwd: Optional[str]
    ts: str
    model: Optional[str]
    rollout: str
    redactions: Tuple[str, ...] = ()

    def record(self) -> Dict[str, Any]:
        """The capture-log record the hooks would have written for this call.

        ``run`` is the per-call id in a namespace that is not Claude's, the
        slot :func:`capture.record_command` keeps for exactly that; ``model``
        is written because here, unlike under the Claude hook, it is known.
        """
        rec: Dict[str, Any] = {
            "kind": "bash_command",
            "ts": self.ts,
            "session": self.session,
            "cwd": self.cwd,
            "tool": self.tool,
            "command": self.command,
            "description": None,
            "transcript": None,
            "host": HOST,
            "run": self.call_id if self.index == 0 else "%s#%d" % (self.call_id, self.index),
            "model": self.model,
        }
        if self.redactions:
            rec["redactions"] = list(self.redactions)
        return rec


def log_records(sessions_dir: Any = None) -> Iterator[Dict[str, Any]]:
    """The feed :func:`lypning.harvest.parse_codex` consumes: one capture-log
    record per python-ish call, as :meth:`CodexCall.record` shapes it.

    The command is the REDACTED one :func:`iter_calls` hands out, and a call
    with a credential-shaped residue is never yielded. ``call_id`` carries the
    per-command id (``run``) so harvest keys a rescan or a forked rollout onto
    the same occurrence. Harvest extracts the programs itself, exactly as it
    does from the Claude log, so both feeds go through one extractor.
    """
    for call in iter_calls(sessions_dir):
        rec = call.record()
        rec["call_id"] = rec["run"]
        yield rec


def rollout_files(roots: Any = None) -> List[Path]:
    """Every ``*.jsonl`` under the roots, sorted by path. Never raises.

    Sorted by path is sorted by time — Codex names a rollout
    ``YYYY/MM/DD/rollout-<ISO start>-<id>.jsonl`` — which is what makes the
    FIRST sighting of a forked call the original rather than a copy.
    """
    if roots is None:
        roots = [sessions_root()]
    elif isinstance(roots, (str, Path)):
        roots = [roots]
    found = set()
    try:
        for root in roots:
            root = Path(root)
            try:
                if root.is_file():
                    found.add(root)
                    continue
                for dirpath, dirnames, filenames in os.walk(str(root)):
                    dirnames.sort()
                    for name in filenames:
                        if name.endswith(".jsonl"):
                            found.add(Path(dirpath) / name)
            except (OSError, ValueError):
                continue
    except TypeError:
        return []
    return sorted(found)


def _events(path: Path) -> Iterator[Dict[str, Any]]:
    """The JSON objects of one rollout, line by line. Never raises.

    Opened for reading only, and streamed: a long session's rollout runs to
    tens of megabytes, and a torn last line — Codex still writing — is skipped
    like any other line that does not parse.
    """
    try:
        with open(str(path), "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    yield ev
    except (OSError, ValueError):
        return


def _raw_calls(roots: Any = None) -> Iterator[CodexCall]:
    """Every python-ish shell command, first sighting per ``call_id``, raw."""
    seen = set()
    for path in rollout_files(roots):
        session: Optional[str] = None
        model: Optional[str] = None
        turn_cwd: Optional[str] = None
        for ev in _events(path):
            payload = ev.get("payload")
            if not isinstance(payload, dict):
                continue
            kind = ev.get("type")
            if kind == "session_meta":
                sid = payload.get("id") or payload.get("session_id")
                session = sid if isinstance(sid, str) and sid else session
                cwd = payload.get("cwd")
                turn_cwd = cwd if isinstance(cwd, str) and cwd else turn_cwd
                continue
            if kind == "turn_context":
                m = payload.get("model")
                model = m if isinstance(m, str) and m else None
                cwd = payload.get("cwd")
                turn_cwd = cwd if isinstance(cwd, str) and cwd else turn_cwd
                continue
            if kind != "response_item":
                continue
            call_id = payload.get("call_id")
            if not isinstance(call_id, str) or not call_id or call_id in seen:
                continue
            tool, commands, workdir = _commands_of(payload)
            if not commands:
                continue
            seen.add(call_id)
            ts = ev.get("timestamp") if isinstance(ev.get("timestamp"), str) else ""
            for idx, command in enumerate(commands):
                if not looks_pythonish(command):
                    continue
                yield CodexCall(call_id=call_id, index=idx, command=command, tool=tool,
                                session=session, cwd=workdir or turn_cwd, ts=ts,
                                model=model, rollout=str(path))


def iter_calls(roots: Any = None) -> Iterator[CodexCall]:
    """Every python-ish Codex shell call, REDACTED, one per ``(call_id, index)``.

    ``roots`` is a directory, a file, or an iterable of either; default
    :func:`sessions_root`. A call whose redacted command still carries a
    credential-shaped residue is dropped, not handed out.
    """
    from . import harvest

    for call in _raw_calls(roots):
        text, hits = harvest.redact(call.command)
        if not harvest.is_safe(hits):
            continue
        yield CodexCall(call.call_id, call.index, text, call.tool, call.session,
                        call.cwd, call.ts, call.model, call.rollout, tuple(hits))


@dataclass(frozen=True)
class CodexProgram:
    """One program extracted from one Codex call — the harvest's unit.

    ``key`` is ``codex:<call_id>#<command index>#<program index>``: stable
    across rescans and across forked copies of the rollout, and in a namespace
    no Claude key can collide with.
    """

    key: str
    program: str
    argv_tail: Tuple[str, ...]
    session: Optional[str]
    ts: str
    model: Optional[str]
    cwd: Optional[str]
    call_id: str
    host: str = HOST
    source: str = SOURCE


def collect(roots: Any = None) -> List[CodexProgram]:
    """Every program in every python-ish Codex call, redacted, in path order.

    THE ENTRY POINT FOR :mod:`lypning.harvest`. Read-only over ``roots``
    (default :func:`sessions_root`); an absent directory is ``[]``. Extraction
    is :func:`harvest.extract_with_tails` on the DECODED command, so a Codex
    heredoc yields exactly what the same heredoc typed into Claude Code would.
    Each program and its argv tail are redacted as the Claude feed redacts
    them, and a program left with a residue is dropped.
    """
    from . import harvest

    out: List[CodexProgram] = []
    for call in _raw_calls(roots):
        for pidx, (program, tail) in enumerate(harvest.extract_with_tails(call.command)):
            text, hits = harvest.redact(program)
            argv, argv_hits = harvest.redact_argv(tail)
            if not text.strip() or not harvest.is_safe(hits + argv_hits):
                continue
            out.append(CodexProgram(
                key="codex:%s#%d#%d" % (call.call_id, call.index, pidx),
                program=text, argv_tail=tuple(argv), session=call.session,
                ts=call.ts, model=call.model, cwd=call.cwd, call_id=call.call_id))
    return out


def model_counts(programs: Iterable[CodexProgram]) -> Dict[str, int]:
    """Programs per model — the aggregate a report may print; bodies never."""
    counts: Dict[str, int] = {}
    for p in programs:
        name = p.model or "unknown"
        counts[name] = counts.get(name, 0) + 1
    return counts


__all__ = ("HOST", "SOURCE", "CodexCall", "CodexProgram", "collect",
                          "iter_calls", "js_string_literals", "model_counts",
                          "rollout_files", "sessions_root")
