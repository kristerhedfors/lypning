"""Synthetic data fixtures, never execution of generated source."""
from __future__ import annotations

import hashlib
import json

import pytest

from harvesting.questions import blob_bytes, delivery_errors, inspect_records, parse_bank


def proposal(index=0):
    return {"id": str(index), "proposed_family": "fixture", "prompt": "task " + str(index),
            "input_contract": "stdin text", "output_contract": "stdout text", "difficulty": "easy",
            "novelty_basis": "inert fixture", "edge_cases": ["empty input"], "capability_targets": ["text"]}


def put(tmp_path, data, collection="project", path="project/questions.jsonl"):
    (tmp_path / "blobs").mkdir(exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    (tmp_path / "blobs" / digest).write_bytes(data)
    return {"sha256": digest, "collection": collection, "path": path}


def test_valid_schema_keeps_unknown_fields_and_exact_line_identity():
    row = dict(proposal(), extra_context={"source": "fixture"})
    raw = json.dumps(row).encode() + b"\n"
    result = parse_bank(raw, "file-id")[0]
    assert result["proposal"] == row and result["errors"] == []
    assert result["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["offset"] == 0 and result["size"] == len(raw)
    assert result["trainable"] is False


@pytest.mark.parametrize("raw", [b"not-json", b"[]", b"{}", b"\xff", b'{"a":1,"a":2}',
                                  b'{"a":NaN}', b" " * 65537])
def test_bad_lines_are_quarantined_not_discarded(raw):
    result = parse_bank(raw, "file-id")
    assert len(result) == 1 and result[0]["errors"] and result[0]["trainable"] is False
    assert result[0]["raw_sha256"] == hashlib.sha256(raw).hexdigest()


def test_duplicate_ids_prompts_and_row_limits():
    raw = json.dumps(proposal()).encode()
    rows = parse_bank(raw + b"\n" + raw, "file-id")
    assert not rows[0]["errors"] and rows[1]["errors"] == ["duplicate-id-or-lexical-prompt"]
    limited = parse_bank(b"\n".join([raw] * 300), "file-id")
    assert len(limited) == 257 and "row-cap" in limited[-1]["errors"][0]


def test_numeric_legacy_ids_are_preserved_without_counting_booleans_as_ids():
    row = dict(proposal(), id=1)
    result = parse_bank(json.dumps(row).encode(), "file")[0]
    assert not result["errors"] and result["proposal"]["id"] == 1
    row["id"] = True
    assert "id" in parse_bank(json.dumps(row).encode(), "file")[0]["errors"]


def test_hash_and_symlink_checks(tmp_path):
    record = put(tmp_path, b"original")
    path = tmp_path / "blobs" / record["sha256"]
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="integrity"):
        blob_bytes(tmp_path, record["sha256"])
    with pytest.raises(ValueError, match="identity"):
        blob_bytes(tmp_path, "../escape")
    link_digest = "a" * 64
    (tmp_path / "blobs" / link_digest).symlink_to(path)
    with pytest.raises(ValueError, match="Unsafe"):
        blob_bytes(tmp_path, link_digest)


def test_missing_file_and_truncated_response_fail_delivery(tmp_path):
    ledger = put(tmp_path, json.dumps({"kind": "response", "status": "completed",
        "response": {"choices": [{"finish_reason": "length"}]}}).encode(), "proxy", "capture/proxy.jsonl")
    assert delivery_errors([ledger], tmp_path, {"catalog": "questions"}) == [
        "provider_completion_truncated", "question_deliverable_missing_or_invalid"]
    assert delivery_errors([], tmp_path, {}, smoke=True) == []
    assert "python_deliverable_missing" in delivery_errors([], tmp_path, {})
    assert "provider_completion_missing" in delivery_errors([], tmp_path, {})


def test_question_count_redaction_and_completion_contract(tmp_path):
    data = b"\n".join(json.dumps(proposal(i)).encode() for i in range(20))
    file = put(tmp_path, data)
    ledger = put(tmp_path, b'{"kind":"response","status":"completed","response":{"choices":[{"finish_reason":"stop"}]}}',
                 "proxy", "capture/proxy.jsonl")
    result = inspect_records([file, ledger], tmp_path)
    assert result["schema_valid"] == 20 and result["semantic_novelty"] == "unreviewed"
    assert delivery_errors([file, ledger], tmp_path, {"catalog": "questions"}) == []
    assert delivery_errors([file, ledger], tmp_path, {"catalog": "questions", "requested_questions": 5})
    file["redacted"] = True
    assert inspect_records([file], tmp_path)["schema_valid"] == 0
