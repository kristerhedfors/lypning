"""A capture-tier export: exact programs from the raw capture log, attributed.

WHY A NEW ARTIFACT. Everything downstream of capture used to read the corpus
(``assets/corpus/corpus.jsonl``) or the published sightings, and both are the
wrong input for training lineage: their program text is normalised and
redacted, so its SHA-256 is not the SHA-256 of what the agent typed, and
``eval2_bank`` links a case to its evidence through exactly that hash. The
corpus is also a deliberate fold that last ran on 2026-09-13, so it holds none
of what the strongest writer has produced since. This module reads the raw log
itself — READ-ONLY, it never writes there — runs ``harvest.extract_with_tails``
over each command, and writes one row per extracted program occurrence with the
exact bytes.

WHAT A ROW CARRIES, AND WHERE EACH FIELD COMES FROM.

``program``, ``argv_tail``  exact, from ``extract_with_tails`` on the command
``source_sha256``           ``sha256(program)`` — ``lypning.evidence``'s identity
``parent_event_id``         the id ``evidence.snapshot`` gives that log LINE
                            under the same ``origin``: the join back to the raw
                            archive, computed rather than looked up
``session``/``host``/``ts`` the log record's own fields, absent stays absent
``model``                   :func:`attribute`; ``"unknown"`` when nothing knows
``ok``                      True / False / None — the tool call's outcome when a
                            journal or transcript records one, never inferred
``quality``                 ``capture_quality.classify``; ``""`` is tier A
``contaminated``            :func:`contamination`; ``""`` is clean, and
                            ``elsewhere:<rule>`` when another occurrence of the
                            same bytes tripped it — taint is per program

MODEL ATTRIBUTION, IN ORDER, EXACT JOINS ONLY.

1. ``$LYPNING_HOME/attribution.jsonl`` — the append-only journal harvest keeps
   so that attribution survives the transcript it was read from (transcripts
   expire; the default cleanup window is 30 days). Keyed by ``tool_use_id``.
2. The transcript the record names, and its subagents: ``tool_use`` block id to
   the ``message.model`` of the record that holds it — the same exact join
   ``harvest`` uses. For a record written before the id was captured, the Bash
   block in that transcript whose ``command`` is byte-identical, and only when
   every such block names the same model.
3. ``"unknown"``. Never the model that happened to be speaking at that time:
   a time join is a guess, and ``DATA_PRODUCTION.md`` forbids promoting one to
   an attribution.

WHAT IT NEVER IS. Not an input to bank v3, and never passed through
``training_data.split_cases``: adding components re-ranks every existing one,
which would move sealed dev/test cases, and would merge this tier (differential
oracle, via ``eval2_bank``) with v3's self-consistency tier. It is the source
of a SEPARATE capture-tier bank for the round after S4, and it is not an arm-A
input — a program Claude wrote is not a draw the target model made.

Library code: returns data, prints nothing (invariant 8). Stdlib only, and the
``lypning`` package is imported lazily, as in ``eval2_select``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from . import capture_quality

#: The writer whose programs are preferred when a draw has to choose. A
#: preference, not a filter: other Claude models stay eligible, ranked after.
PREFERRED_MODELS = ("claude-opus-5-5",)

#: Hosts whose records are selectable by default. A missing ``host`` is Claude:
#: every record written before the field existed is one (``harvest._joinable``).
DEFAULT_HOSTS = ("claude",)

#: A model string from another vendor's agent. Excluded by default whatever
#: host the record claims, so a mis-tagged record cannot slip one through:
#: whether such programs may train the target at all is an operator decision
#: that has not been taken.
_OTHER_VENDOR = re.compile(r"(?i)^(?:gpt|codex|o\d)(?:[-._]|$)")

UNKNOWN = "unknown"

#: A command that touches evaluation or training material. The programs in it
#: may BE that material, or be derived from it — a leak into a new bank that
#: no similarity check is guaranteed to see. Over-matching costs a candidate;
#: under-matching costs a contaminated benchmark.
CONTAMINATION = (
    ("eval2", re.compile(r"(?i)eval[-_ ]?2")),
    ("bank", re.compile(r"(?i)bank")),
    ("positive-control", re.compile(r"(?i)positive[-_ ]?control")),
    ("completions", re.compile(r"(?i)completions")),
    ("capture-log", re.compile(r"(?i)invocations\.jsonl")),
)

SCHEMA = 1


# --- identities ------------------------------------------------------------------


def source_sha256(program: str) -> str:
    return hashlib.sha256(program.encode("utf-8")).hexdigest()


def event_id(origin: str, line: int, raw_line: bytes) -> str:
    """The id ``lypning.evidence.snapshot`` gives line ``line`` under ``origin``.

    Computed with evidence's own ``digest``/``encoded``, so it cannot drift from
    what a snapshot of the same log writes; the pinning test snapshots a fixture
    log and compares.
    """
    from lypning import evidence  # noqa: WPS433 - lazy, like eval2_select
    return evidence.digest(evidence.encoded([origin, line, evidence.digest(raw_line)]))


def _log_lines(path: Path) -> Iterator[Tuple[int, bytes, Optional[Dict[str, Any]]]]:
    """``(line number, exact bytes, record or None)`` — every physical line.

    Numbered exactly as ``evidence.snapshot`` numbers them (blank and malformed
    lines count), because the number is half of ``parent_event_id``. A final
    line without its newline is still being written: it is skipped, as the
    snapshot would quarantine it.
    """
    with open(str(path), "rb") as fh:
        for n, raw in enumerate(fh, start=1):
            if not raw.endswith(b"\n"):
                return
            try:
                rec = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError):
                yield n, raw, None
                continue
            yield n, raw, rec if isinstance(rec, dict) else None


# --- attribution -----------------------------------------------------------------


def journal_path() -> Path:
    from lypning import paths  # noqa: WPS433
    return paths.state_dir() / "attribution.jsonl"


def read_journal(path: Optional[Path]) -> Dict[str, Dict[str, Any]]:
    """``tool_use_id -> {"model", "ok"}`` from the attribution journal.

    Absent file, unreadable line, missing field: each is "the journal does not
    know", never an error — the journal is written by ``lypning harvest`` and
    does not exist on a machine whose harvest predates it. Later lines win, which is what an
    append-only journal means by a correction.
    """
    out: Dict[str, Dict[str, Any]] = {}
    if path is None or not Path(path).is_file():
        return out
    with open(str(path), "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            tid = rec.get("tool_use_id")
            if not isinstance(tid, str) or not tid:
                continue
            model = rec.get("model")
            entry = out.setdefault(tid, {"model": None, "ok": None})
            if isinstance(model, str) and model and not model.startswith("<"):
                entry["model"] = model
            flags = [rec.get(k) for k in ("is_error", "interrupted")]
            if any(isinstance(f, bool) for f in flags):
                entry["ok"] = not any(f is True for f in flags)
    return out


class TranscriptIndex:
    """What one session's transcripts say: ids to models and outcomes, commands
    to models. Built once per transcript path, never cached to disk — this
    module writes nothing but its own output."""

    def __init__(self) -> None:
        self.model_by_id: Dict[str, str] = {}
        self.error_by_id: Dict[str, bool] = {}
        self.models_by_command: Dict[str, Set[str]] = {}

    def feed(self, text: str) -> None:
        for line in text.split("\n"):
            if '"tool_use"' not in line and '"tool_result"' not in line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            message = ev.get("message") if isinstance(ev, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            model = message.get("model")
            model = model if isinstance(model, str) and model and not model.startswith("<") else None
            for block in content:
                if not isinstance(block, dict):
                    continue
                kind = block.get("type")
                if kind == "tool_use" and model:
                    bid = block.get("id")
                    if isinstance(bid, str) and bid:
                        self.model_by_id[bid] = model
                    inp = block.get("input")
                    cmd = inp.get("command") if isinstance(inp, dict) else None
                    if block.get("name") == "Bash" and isinstance(cmd, str):
                        self.models_by_command.setdefault(cmd, set()).add(model)
                elif kind == "tool_result":
                    tid = block.get("tool_use_id")
                    if isinstance(tid, str) and tid:
                        self.error_by_id[tid] = block.get("is_error") is True

    def model_for_command(self, command: str) -> Optional[str]:
        models = self.models_by_command.get(command) or set()
        return next(iter(models)) if len(models) == 1 else None


def _session_files(transcript: str) -> List[Path]:
    main = Path(transcript)
    files = [main] if main.is_file() else []
    sub = main.parent / main.stem / "subagents"
    if sub.is_dir():
        files.extend(sorted(p for p in sub.rglob("*.jsonl") if p.is_file()))
    return files


def _load_index(transcript: str) -> TranscriptIndex:
    index = TranscriptIndex()
    for f in _session_files(transcript):
        try:
            with open(str(f), "r", encoding="utf-8", errors="replace") as fh:
                index.feed(fh.read())
        except OSError:
            continue
    return index


def attribute(rec: Dict[str, Any], journal: Dict[str, Dict[str, Any]],
              indexes: Dict[str, TranscriptIndex], *,
              transcripts: bool = True) -> Tuple[str, str, Optional[bool]]:
    """``(model, basis, ok)`` for one log record. See the module docstring."""
    tid = rec.get("tool_use_id") if isinstance(rec.get("tool_use_id"), str) else None
    model: Optional[str] = None
    basis = UNKNOWN
    ok: Optional[bool] = None
    if tid and tid in journal:
        model = journal[tid].get("model")
        ok = journal[tid].get("ok")
        if model:
            basis = "journal"
    transcript = rec.get("transcript")
    host = rec.get("host")
    joinable = not (isinstance(host, str) and host and host != "claude")
    if transcripts and joinable and isinstance(transcript, str) and transcript \
            and (model is None or ok is None):
        index = indexes.get(transcript)
        if index is None:
            index = indexes[transcript] = _load_index(transcript)
        if tid:
            if model is None and tid in index.model_by_id:
                model, basis = index.model_by_id[tid], "transcript-id"
            if ok is None and tid in index.error_by_id:
                ok = not index.error_by_id[tid]
        elif model is None and isinstance(rec.get("command"), str):
            found = index.model_for_command(rec["command"])
            if found:
                model, basis = found, "transcript-command"
    return (model or UNKNOWN), basis, ok


# --- screens -----------------------------------------------------------------------


def contamination(command: str, program: str = "") -> str:
    """The first contamination rule the command (or program) trips, or ``""``."""
    for name, rx in CONTAMINATION:
        if rx.search(command or "") or rx.search(program or ""):
            return name
    return ""


def other_vendor(model: str) -> bool:
    return bool(_OTHER_VENDOR.match(model or ""))


# --- the export ----------------------------------------------------------------------


def export(log: Path, *, origin: str, attribution: Optional[Path] = None,
           transcripts: bool = True, local: Optional[Iterable[str]] = None,
           keep: str = "tier-a") -> Dict[str, Any]:
    """Rows and counts for every program the log's commands hold. Writes nothing.

    ``keep`` decides which rows come back (the counts always cover all of them):
    ``"tier-a"`` — tier A and uncontaminated, the default and the only rows an
    author should see; ``"all"`` — every row, with the program text withheld
    (``program`` None) on any row the ``privacy`` rule rejected, so an audit
    file can never be the place a credential is copied to.
    """
    if keep not in ("tier-a", "all"):
        raise ValueError("keep must be 'tier-a' or 'all'")
    if not isinstance(origin, str) or not origin.strip():
        raise ValueError("origin must name the log generation, as for lypning.evidence")
    from lypning import harvest  # noqa: WPS433

    local_set = frozenset(local) if local is not None else capture_quality.repo_modules()
    journal = read_journal(attribution)
    indexes: Dict[str, TranscriptIndex] = {}
    rows: List[Dict[str, Any]] = []
    counts: Dict[str, Any] = {
        "lines": 0, "records": 0, "commands": 0, "with_program": 0, "programs": 0,
        "quality": {r: 0 for r in ("",) + capture_quality.RULES},
        "contaminated": {name: 0 for name, _ in CONTAMINATION},
        "model_basis": {}, "models": {}, "ok": {"true": 0, "false": 0, "unknown": 0},
    }
    verdicts: Dict[str, str] = {}
    tainted: Dict[str, str] = {}
    for n, raw, rec in _log_lines(Path(log)):
        counts["lines"] += 1
        if rec is None:
            continue
        counts["records"] += 1
        command = rec.get("command")
        if rec.get("kind") != "bash_command" or not isinstance(command, str):
            continue
        counts["commands"] += 1
        extracted = harvest.extract_with_tails(command)
        if not extracted:
            continue
        counts["with_program"] += 1
        model, basis, ok = attribute(rec, journal, indexes, transcripts=transcripts)
        parent = event_id(origin, n, raw)
        dirty_command = contamination(command)
        for idx, (program, tail) in enumerate(extracted):
            counts["programs"] += 1
            sha = source_sha256(program)
            if sha not in verdicts:
                verdicts[sha] = capture_quality.classify(program, local=local_set)
            quality = verdicts[sha]
            dirty = dirty_command or contamination("", program)
            counts["quality"][quality] += 1
            if dirty:
                counts["contaminated"][dirty] += 1
            counts["model_basis"][basis] = counts["model_basis"].get(basis, 0) + 1
            counts["models"][model] = counts["models"].get(model, 0) + 1
            counts["ok"]["unknown" if ok is None else str(ok).lower()] += 1
            if dirty:
                tainted.setdefault(sha, dirty)
            row = {
                "schema": SCHEMA,
                "tier": "capture",
                "program": None if quality == "privacy" else program,
                "argv_tail": [str(a) for a in tail],
                "source_sha256": sha,
                "parent_event_id": parent,
                "parent_line": n,
                "index": idx,
                "session": rec.get("session"),
                "host": rec.get("host") if isinstance(rec.get("host"), str) else "claude",
                "ts": rec.get("ts"),
                "tool_use_id": rec.get("tool_use_id"),
                "agent_type": rec.get("agent_type"),
                "model": model,
                "model_basis": basis,
                "ok": ok,
                "quality": quality,
                "contaminated": dirty,
            }
            rows.append(row)
    # Contamination is a property of the PROGRAM, not of one occurrence: bytes
    # that were ever typed next to eval-2 or a bank may be derived from it, and
    # the clean-looking occurrence elsewhere does not launder them. Every
    # occurrence of a tainted program is marked, then filtered.
    for row in rows:
        if not row["contaminated"] and row["source_sha256"] in tainted:
            row["contaminated"] = "elsewhere:" + tainted[row["source_sha256"]]
    if keep == "tier-a":
        rows = [r for r in rows if not r["quality"] and not r["contaminated"]]
    counts["distinct"] = len(verdicts)
    counts["distinct_tier_a"] = sum(1 for v in verdicts.values() if not v)
    counts["distinct_tainted"] = len(tainted)
    counts["distinct_tier_a_clean"] = sum(1 for sha, v in verdicts.items()
                                          if not v and sha not in tainted)
    return {"origin": origin, "rows": rows, "counts": counts,
            "journal_entries": len(journal), "transcripts_read": len(indexes)}


# --- selection -----------------------------------------------------------------------


def select_rows(rows: Iterable[Dict[str, Any]], *,
                models: Optional[Sequence[str]] = None,
                prefer: Sequence[str] = PREFERRED_MODELS,
                hosts: Sequence[str] = DEFAULT_HOSTS,
                allow_other_vendors: bool = False) -> List[Dict[str, Any]]:
    """One record per distinct program, tier A and clean, in preference order.

    Occurrences of the same bytes fold into one record carrying every
    ``parent_event_id`` and a ``models`` histogram; its ``model`` is the most
    preferred writer that typed it, else the most frequent (ties by name), so
    a program Opus 5.5 typed once and another model typed twice still counts as
    Opus 5.5's. ``models``, when given, keeps only programs some listed model
    typed. Other hosts and other vendors' models are dropped unless asked for,
    and stay host-tagged when they are.
    """
    prefer = list(prefer)
    by_sha: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if row.get("quality") or row.get("contaminated") or not isinstance(row.get("program"), str):
            continue
        host = row.get("host") or "claude"
        model = row.get("model") or UNKNOWN
        if host not in hosts:
            continue
        if other_vendor(model) and not allow_other_vendors:
            continue
        sha = row["source_sha256"]
        rec = by_sha.get(sha)
        if rec is None:
            rec = by_sha[sha] = {
                "program": row["program"], "argv_tail": list(row.get("argv_tail") or []),
                "source_sha256": sha, "parent_event_ids": [], "sessions": [],
                "models": {}, "hosts": [], "ok": {"true": 0, "false": 0, "unknown": 0},
                "first_ts": row.get("ts"),
            }
        if row.get("parent_event_id") not in rec["parent_event_ids"]:
            rec["parent_event_ids"].append(row.get("parent_event_id"))
        if row.get("session") and row["session"] not in rec["sessions"]:
            rec["sessions"].append(row["session"])
        if host not in rec["hosts"]:
            rec["hosts"].append(host)
        rec["models"][model] = rec["models"].get(model, 0) + 1
        ok = row.get("ok")
        rec["ok"]["unknown" if ok is None else str(bool(ok)).lower()] += 1
        ts = row.get("ts")
        if isinstance(ts, str) and (not rec["first_ts"] or ts < rec["first_ts"]):
            rec["first_ts"] = ts

    def rank(model: str) -> int:
        return prefer.index(model) if model in prefer else len(prefer)

    out: List[Dict[str, Any]] = []
    for rec in by_sha.values():
        if models and not any(m in rec["models"] for m in models):
            continue
        typed = sorted(rec["models"].items(), key=lambda kv: (rank(kv[0]), kv[0] == UNKNOWN,
                                                              -kv[1], kv[0]))
        rec["model"] = typed[0][0]
        rec["preferred"] = rank(rec["model"]) < len(prefer)
        out.append(rec)
    out.sort(key=lambda r: (rank(r["model"]), r["model"] == UNKNOWN, r["source_sha256"]))
    return out


def write_rows(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    """Owner-only, like every other private capture artifact (``DATA_PRODUCTION.md``)."""
    path = Path(path)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def snapshot(rows_path: Path, evidence_dir: Path, origin: str) -> Dict[str, Any]:
    """Freeze the export with ``lypning.evidence`` so ``eval2_bank`` can link it.

    Every row carries ``program``, so every event gets ``source_sha256`` equal to
    the row's own — the link ``eval2_bank.load_evidence`` indexes. The origin is
    namespaced so this snapshot can never be mistaken for one of the raw log.
    """
    from lypning import evidence  # noqa: WPS433
    return evidence.snapshot(rows_path, evidence_dir, "capture-export:" + origin)


def render(result: Dict[str, Any]) -> str:
    """The counts as text, for the CLI. Aggregates only: no program, no command."""
    c = result["counts"]
    lines = ["capture export  origin %s" % result["origin"],
             "  log lines %d   records %d   bash commands %d   with a program %d"
             % (c["lines"], c["records"], c["commands"], c["with_program"]),
             "  program occurrences %d   distinct %d   distinct tier A %d   "
             "tier A and clean %d   (tainted %d)"
             % (c["programs"], c["distinct"], c["distinct_tier_a"],
                c["distinct_tier_a_clean"], c["distinct_tainted"]),
             "  journal entries %d   transcripts read %d"
             % (result["journal_entries"], result["transcripts_read"]),
             "  quality (occurrences, first rejecting rule):"]
    for rule in capture_quality.RULES:
        lines.append("    %-14s %6d" % (rule, c["quality"].get(rule, 0)))
    lines.append("    %-14s %6d" % ("tier A", c["quality"].get("", 0)))
    lines.append("  contaminated (occurrences):")
    for name, _ in CONTAMINATION:
        lines.append("    %-16s %6d" % (name, c["contaminated"].get(name, 0)))
    lines.append("  model basis: " + ", ".join(
        "%s %d" % kv for kv in sorted(c["model_basis"].items())))
    lines.append("  models: " + ", ".join(
        "%s %d" % kv for kv in sorted(c["models"].items(), key=lambda kv: (-kv[1], kv[0]))))
    lines.append("  outcome: true %(true)d  false %(false)d  unknown %(unknown)d" % c["ok"])
    lines.append("  rows written %d" % len(result["rows"]))
    return "\n".join(lines)
