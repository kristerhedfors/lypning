"""Inspect question proposals as bounded data, never code or correctness labels.

Keep raw archive blobs and per-line identities even when schema checks fail.
Structural validity and lexical uniqueness do not establish semantic novelty.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re


MAX_FILE = 16 * 1024 * 1024
MAX_LINE = 65536
MAX_ROWS = 256
TEXT_FIELDS = ("proposed_family", "prompt", "input_contract", "output_contract",
               "difficulty", "novelty_basis")
LIST_FIELDS = ("edge_cases", "capability_targets")


def blob_bytes(root, digest):
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid blob identity")
    path = root / "blobs" / digest
    if path.is_symlink() or not path.is_file():
        raise ValueError("Unsafe blob")
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE + 1)
    if len(data) > MAX_FILE or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Blob size/integrity mismatch")
    return data


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("nonfinite JSON")


def parse_bank(data, file_sha256):
    rows, seen_ids, seen_prompts = [], set(), set()
    offset = 0
    for index, raw in enumerate(data.splitlines(keepends=True)):
        if index >= MAX_ROWS:
            rows.append({"line": index + 1, "offset": offset, "file_sha256": file_sha256,
                         "errors": ["row-cap; remaining bytes retained in original blob"], "trainable": False})
            break
        row = {"line": index + 1, "offset": offset, "size": len(raw),
               "raw_sha256": hashlib.sha256(raw).hexdigest(), "file_sha256": file_sha256,
               "errors": [], "trainable": False}
        offset += len(raw)
        try:
            if len(raw) > MAX_LINE:
                raise ValueError("oversized line")
            proposal = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant)
            if not isinstance(proposal, dict):
                raise ValueError("question must be an object")
            row["proposal"] = proposal
            row["errors"] = [key for key in TEXT_FIELDS
                             if not isinstance(proposal.get(key), str) or not proposal[key].strip()]
            identifier = proposal.get("id")
            if not ((isinstance(identifier, str) and identifier.strip()) or
                    (type(identifier) is int and identifier >= 0)):
                row["errors"].append("id")
            row["errors"] += [key for key in LIST_FIELDS
                              if not isinstance(proposal.get(key), list) or not proposal[key]
                              or any(not isinstance(v, str) or not v.strip() for v in proposal[key])]
            if not row["errors"]:
                key = " ".join(proposal["prompt"].casefold().split())
                if str(identifier) in seen_ids or key in seen_prompts:
                    row["errors"].append("duplicate-id-or-lexical-prompt")
                seen_ids.add(str(identifier))
                seen_prompts.add(key)
                row["prompt_sha256"] = hashlib.sha256(key.encode()).hexdigest()
        except (ValueError, UnicodeError, RecursionError):
            row["errors"] = ["invalid-json-or-line-bound"]
        rows.append(row)
    return rows


def inspect_records(records, root):
    files = []
    for record in records:
        if (record["collection"] != "project" or
                PurePosixPath(record["path"]).name != "questions.jsonl" or not record.get("sha256")):
            continue
        rows = parse_bank(blob_bytes(root, record["sha256"]), record["sha256"])
        if record.get("redacted"):
            for row in rows:
                row["errors"].append("redacted-derivative-needs-review")
        files.append({"path": record["path"], "sha256": record["sha256"], "rows": rows,
                      "schema_valid": sum(not r["errors"] for r in rows),
                      "invalid": sum(bool(r["errors"]) for r in rows)})
    return {"files": files, "schema_valid": sum(f["schema_valid"] for f in files),
            "invalid": sum(f["invalid"] for f in files), "trainable": False,
            "semantic_novelty": "unreviewed", "correctness": "unknown"}


def delivery_errors(records, root, task, *, smoke=False):
    if smoke:
        return []  # The separate check_smoke contract grades its authored fixture.
    errors = []
    if task.get("catalog") == "questions":
        proposals = inspect_records(records, root)
        expected = task.get("requested_questions", 20)
        if (len(proposals["files"]) != 1 or proposals["schema_valid"] != expected or proposals["invalid"]):
            errors.append("question_deliverable_missing_or_invalid")
    elif not any(r["collection"] == "project" and r["path"].endswith(".py") and r.get("sha256")
                 for r in records):
        errors.append("python_deliverable_missing")
    ledgers = [r for r in records if r["collection"] == "proxy"
               and r["path"].endswith("/proxy.jsonl") and r.get("sha256")]
    if not ledgers:
        errors.append("provider_ledger_missing")
    completed = 0
    for record in ledgers:
        for raw in blob_bytes(root, record["sha256"]).splitlines():
            event = json.loads(raw)
            if event.get("kind") != "response" or event.get("status") != "completed":
                continue
            choices = event.get("response", {}).get("choices", [])
            if not isinstance(choices, list) or not choices:
                errors.append("provider_completion_invalid")
                continue
            completed += 1
            if any(c.get("finish_reason") == "length" for c in choices):
                errors.append("provider_completion_truncated")
            if any(c.get("finish_reason") not in ("stop", "tool_calls", "length") for c in choices):
                errors.append("provider_completion_incomplete")
    if not completed:
        errors.append("provider_completion_missing")
    return sorted(set(errors))


def main(argv=None):
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("archives", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, help="New private JSON report; never overwrite")
    args = parser.parse_args(argv)
    from .review_queue import inventory
    seen, results = set(), []
    for archive in args.archives:
        item = inventory(archive)  # Bind metadata and hashes before parsing outputs.
        manifest = json.loads((archive / "manifest.json").read_text())
        bank = inspect_records(manifest["records"], archive)
        bank.update(task=item["task"]["id"], run_id=item["task"]["run_id"],
                    snapshot_digest=item["snapshot_digest"], archive=str(archive.resolve()))
        for file in bank["files"]:
            for row in file["rows"]:
                key = row.get("prompt_sha256")
                row["duplicate_prompt_across_inputs"] = bool(key and key in seen)
                if key:
                    seen.add(key)
        results.append(bank)
    encoded = json.dumps(results, indent=2, ensure_ascii=True)
    if args.output:
        os.umask(0o077)
        args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with args.output.open("x", encoding="utf-8") as output:
            output.write(encoded + "\n")
    else:
        print(encoded)


if __name__ == "__main__":
    main()
