"""Read-only, hash-checked harvest inventory; never run or admit generated code."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from lypning.evidence import load_snapshot


def inspect(path):
    manifest = json.loads((path / "manifest.json").read_text())
    load_snapshot(path / "evidence")
    counts = {"provider_requests": 0, "provider_completed": 0, "provider_failed": 0,
              "prompt_tokens": 0, "completion_tokens": 0, "truncated_completions": 0,
              "omitted_files": 0}
    sources = set()
    returned_models = set()
    for row in manifest["records"]:
        digest = row.get("sha256")
        if not digest:
            counts["omitted_files"] += 1
            continue
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid blob identity")
        blob = path / "blobs" / digest
        if blob.is_symlink() or blob.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Unsafe or oversized blob")
        data = blob.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Blob integrity mismatch")
        if row["collection"] == "project" and row["path"].endswith(".py"):
            sources.add(digest)
        if row["collection"] != "proxy" or not row["path"].endswith("/proxy.jsonl"):
            continue
        for line in data.splitlines():
            event = json.loads(line)
            if event.get("kind") == "request":
                counts["provider_requests"] += 1
            if event.get("kind") != "response":
                continue
            if event.get("status") != "completed":
                counts["provider_failed"] += 1
                continue
            counts["provider_completed"] += 1
            response = event.get("response", {})
            if isinstance(response.get("model"), str):
                returned_models.add(response["model"])
            for field in ("prompt_tokens", "completion_tokens"):
                value = response.get("usage", {}).get(field)
                if type(value) is int and value >= 0:
                    counts[field] += value
            counts["truncated_completions"] += sum(
                choice.get("finish_reason") == "length" for choice in response.get("choices", []))
    return {"task": manifest["task"]["id"], "run_id": manifest["task"]["run_id"],
            "round": manifest["task"]["round"], "state": manifest["state"],
            "errors": manifest["errors"], "unique_python_sources": len(sources),
            "returned_models": sorted(returned_models), **counts,
            "correctness": "unknown", "native_compatibility": "unmeasured", "trainable": False}


def main(argv=None):
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("archives", type=Path, nargs="+")
    args = parser.parse_args(argv)
    print(json.dumps([inspect(path) for path in args.archives], indent=2))


if __name__ == "__main__":
    main()
