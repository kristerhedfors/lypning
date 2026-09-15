from __future__ import annotations

import ast
import json
import os

import pytest

from lypning import capture, evidence, harvest
from lypning.embed import Outcome, OK


def test_source_identity_preserves_multiline_data():
    left, right = 'value = """a \nb"""', 'value = """a\nb"""'
    assert ast.literal_eval(ast.parse(left).body[0].value) != ast.literal_eval(ast.parse(right).body[0].value)
    assert harvest.sighting_key(left) != harvest.sighting_key(right)
    assert evidence.digest(left.encode()) != evidence.digest(right.encode())
    # Historical summary IDs remain stable for ordinary single-line source.
    assert harvest.sighting_key("\nprint(1)\n") == harvest.sighting_key("print(1)")


def test_snapshot_exact_source_context_occurrences_and_quarantine(tmp_path):
    records = [dict(program='print("unique")\n', argv_tail=[str(i)], stdin_sample=str(i),
                    future_context={"model": "unattributed"}) for i in range(2)]
    raw = b"".join(evidence.encoded(r) + b"\n" for r in records + [records[0]]) + b"bad\xff\n{"
    log = tmp_path / "log"
    log.write_bytes(raw)
    destination = tmp_path / "snapshot"
    manifest = evidence.snapshot(log, destination, "host/session/generation")
    loaded, events = evidence.load_snapshot(destination)
    assert loaded == manifest
    assert (destination / "raw.jsonl").read_bytes() == raw
    assert manifest["events"] == 5 and manifest["sources"] == 1 and manifest["quarantined"] == 2
    assert len({e["event_id"] for e in events}) == 5
    assert events[0]["source_sha256"] == events[1]["source_sha256"]
    assert events[0]["record"] != events[1]["record"]
    assert events[0]["record"]["future_context"] == records[0]["future_context"]
    assert not any(e["trainable"] for e in events)
    assert events[-1]["quarantine"] == "incomplete-line"
    if os.name == "posix":
        assert destination.stat().st_mode & 0o077 == 0
        assert (destination / "raw.jsonl").stat().st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        evidence.snapshot(log, destination, "host/session/generation")


def test_snapshot_stable_prefix_and_no_silent_oversize_loss(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "MAX_LINE_BYTES", 20)
    log = tmp_path / "log"
    log.write_bytes(b'{"program":"x"}\n' + b"x" * 100 + b"\n")
    evidence.snapshot(log, tmp_path / "a", "origin")
    with log.open("ab") as stream:
        stream.write(b"{}\n")
    evidence.snapshot(log, tmp_path / "b", "origin")
    _, a = evidence.load_snapshot(tmp_path / "a")
    _, b = evidence.load_snapshot(tmp_path / "b")
    assert a == b[:2] and a[1]["quarantine"] == "oversized"
    source = tmp_path / "a" / "sources" / a[0]["source_sha256"]
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="source artifact integrity"):
        evidence.load_snapshot(tmp_path / "a")


def test_sighting_keeps_forward_metadata_in_corpus():
    sighting = harvest.Sighting("py-fixture", "print(1)", extra={"lineage": {"review": "unknown"}})
    assert sighting.entry().extra == sighting.extra


def test_embedding_capture_is_default_private_evidence_not_a_label(tmp_path, monkeypatch):
    log = tmp_path / "calls"
    monkeypatch.setenv("LYPNING_LOG", str(log))
    out = Outcome(status=OK, exit_code=0, stdout=b"\xff", stderr=b"", kind="", detail="", committed=False, fall_onward=False)
    args = dict(outcome=out, engine="lypning-l", version="fixture", library="library", stdin=b"\0")
    assert capture.record_embedding("print(1)", **args)
    rec = json.loads(log.read_text())
    assert rec["correctness"] == "unknown" and rec["stdout_base64"] == "/w=="
    assert rec["stdin_base64"] == "AA==" and rec["program"] == "print(1)"
    monkeypatch.setenv("LYPNING_CAPTURE", "0")
    assert not capture.record_embedding("print(2)", **args)
    assert len(log.read_text().splitlines()) == 1


def test_oversized_capture_does_not_append_or_truncate(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "MAX_EVENT_BYTES", 20)
    log = tmp_path / "log"
    assert not capture.append_record({"program": "x" * 100}, log)
    assert not log.exists()


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}\n', b'{"x":NaN}\n', b'{"x":1e999}\n',
                                     b'{"x":"\\ud800"}\n', b'{"x":' + b'[' * 2000 + b']' * 2000 + b'}\n'],
                         ids=["duplicate-key", "nan", "overflow", "surrogate", "nesting"])
def test_ambiguous_or_unrepresentable_metadata_keeps_raw_evidence(tmp_path, raw):
    log = tmp_path / "log"
    log.write_bytes(raw)
    result = evidence.snapshot(log, tmp_path / "snapshot", "origin")
    assert result["quarantined"] == 1
    assert (tmp_path / "snapshot/raw.jsonl").read_bytes() == raw
