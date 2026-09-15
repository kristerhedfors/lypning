"""Private, lossless evidence snapshots; observations are not training labels.

No execution, transcript discovery, remote upload, or corpus publication occurs
here. Import only logs the operator is authorized to retain. Content identities
are full SHA-256 of exact bytes, not the legacy corpus's normalized short IDs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path

MAX_LINE_BYTES = 4 * 1024 * 1024


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encoded(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _invalid_constant(value):
    raise ValueError("non-JSON numeric constant: " + value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field: " + key)
        result[key] = value
    return result


def _check_metadata(value):
    # A fixed view limit is portable across Python recursion-limit changes.
    # Raw bytes are retained even when the parsed view is too deeply nested.
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 128 or (isinstance(item, float) and not math.isfinite(item)):
            raise ValueError("metadata nesting/nonfinite-number limit")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)


def _write_new(path, raw):
    # Explicit private files, including when the caller has a permissive umask.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)


def snapshot(log, output, origin):
    """Freeze a bounded-memory snapshot without losing duplicate occurrences.

    origin names ONE log generation (host/session/rotation); keep it on repeat
    imports of a growing log, change it when the log is rotated or rewritten.
    Invalid/oversized lines remain in the exact raw copy with byte ranges and
    hashes. They are quarantined, never silently discarded or parsed as code.
    The manifest is written last; interrupted snapshots are not admissible.
    """
    if not isinstance(origin, str) or not origin.strip():
        raise ValueError("origin must identify the host/session/log generation")
    output = Path(output)
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    objects = output / "sources"
    objects.mkdir(mode=0o700)
    event_count = quarantined = 0
    source_keys = set()
    events_hash = hashlib.sha256()
    full = hashlib.sha256()
    offset = 0
    # Copy and hash from the same stream: don't reopen a live log for a second
    # pass. Fix the initial byte budget so a busy producer cannot prolong import.
    event_fd = os.open(str(output / "events.jsonl"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(event_fd, "wb") as event_stream, Path(log).open("rb") as source:
        remaining = os.fstat(source.fileno()).st_size
        fd = os.open(str(output / "raw.jsonl"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as target:
            while remaining:
                start = offset
                line_hash = hashlib.sha256()
                head = bytearray()
                ended = False
                while remaining and not ended:
                    chunk = source.readline(min(65536, remaining))
                    if not chunk:
                        raise ValueError("log shrank during snapshot; retry into a new directory")
                    target.write(chunk)
                    full.update(chunk)
                    line_hash.update(chunk)
                    remaining -= len(chunk)
                    offset += len(chunk)
                    if len(head) <= MAX_LINE_BYTES:
                        head.extend(chunk)
                    ended = chunk.endswith(b"\n")
                record = None
                reason = "oversized" if offset - start > MAX_LINE_BYTES else None
                if not ended:
                    reason = "incomplete-line"
                if reason is None:
                    try:
                        record = json.loads(head.decode("utf-8"), parse_constant=_invalid_constant,
                                            object_pairs_hook=_unique_object)
                        _check_metadata(record)
                        if not isinstance(record, dict):
                            reason = "not-object"
                    except (ValueError, UnicodeError, RecursionError):
                        reason = "invalid-json-or-utf8"
                event = {"schema": 1, "origin": origin, "line": event_count + 1,
                         "offset": start, "bytes": offset - start,
                         "raw_sha256": line_hash.hexdigest(), "quarantine": reason,
                         "trainable": False}
                event["event_id"] = digest(encoded([origin, event["line"], event["raw_sha256"]]))
                if reason is None:
                    # Preserve every unknown producer field; missing context is
                    # unknown, never inferred from a successful process exit.
                    event["record"] = record
                    program = record.get("program")
                    if isinstance(program, str):
                        try:
                            raw = program.encode("utf-8")
                        except UnicodeError:
                            event["quarantine"] = "invalid-source-utf8"
                        else:
                            key = digest(raw)
                            event["source_sha256"] = key
                            source_keys.add(key)
                            if not (objects / key).exists():
                                _write_new(objects / key, raw)
                # JSON escapes may contain unpaired surrogates in producer
                # metadata. Keep the raw evidence, quarantine the parsed view.
                try:
                    event_bytes = encoded(event) + b"\n"
                except UnicodeError:
                    event.pop("record", None)
                    event["quarantine"] = "invalid-record-utf8"
                    event_bytes = encoded(event) + b"\n"
                event_stream.write(event_bytes)
                events_hash.update(event_bytes)
                event_count += 1
                quarantined += event["quarantine"] is not None
    manifest = {"schema": 1, "origin": origin, "raw_sha256": full.hexdigest(),
                "raw_bytes": offset, "events_sha256": events_hash.hexdigest(),
                "events": event_count, "quarantined": quarantined,
                "sources": len(source_keys),
                "privacy": "private-unreviewed", "training_labels": False}
    manifest["digest"] = digest(encoded(manifest))
    _write_new(output / "manifest.json", encoded(manifest) + b"\n")
    return manifest


def load_snapshot(path):
    """Validate the manifest and all source artifacts before a derived view."""
    path = Path(path)
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    claimed = manifest.pop("digest")
    if manifest.get("schema") != 1 or digest(encoded(manifest)) != claimed:
        raise ValueError("evidence manifest integrity failed")
    # Stream the potentially large raw archive; observations remain metadata.
    raw_hash = hashlib.sha256()
    size = 0
    with (path / "raw.jsonl").open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            raw_hash.update(chunk)
            size += len(chunk)
    event_bytes = (path / "events.jsonl").read_bytes()
    if (raw_hash.hexdigest() != manifest["raw_sha256"] or size != manifest["raw_bytes"] or
            digest(event_bytes) != manifest["events_sha256"]):
        raise ValueError("evidence artifact integrity failed")
    events = [json.loads(line) for line in event_bytes.splitlines()]
    if len(events) != manifest["events"]:
        raise ValueError("evidence event count changed")
    for event in events:
        key = event.get("source_sha256")
        if key is not None:
            if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
                raise ValueError("invalid source identity")
            if digest((path / "sources" / key).read_bytes()) != key:
                raise ValueError("source artifact integrity failed")
    manifest["digest"] = claimed
    return manifest, events


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new PRIVATE directory")
    parser.add_argument("--origin", required=True, help="stable host/session/log-generation identifier")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(snapshot(args.log, args.output, args.origin), indent=2))
        return 0
    except (OSError, ValueError) as exc:
        parser.exit(1, "evidence snapshot blocked: %s\n" % exc)


if __name__ == "__main__":
    main()
