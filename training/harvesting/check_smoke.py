"""Assert capture health on a reviewed fixture, not on arbitrary generated code."""
from __future__ import annotations

import json
from pathlib import Path
import sys


def check(path):
    data = json.loads((path / "manifest.json").read_text())
    assert data["smoke"] and not data["errors"], data["errors"]
    rows = data["records"]
    assert any(r.get("program") == "print(42)\n" for r in rows), "Fixture source not collected"
    log = next(r for r in rows if r["path"].endswith("/invocations.jsonl"))
    events = [json.loads(line) for line in (path / "blobs" / log["sha256"]).read_text().splitlines()]
    assert any(r.get("kind") == "tool_before" for r in events), "Before hook missing"
    assert any(r.get("kind") == "tool_after" for r in events), "After hook missing"
    assert any("/ses_" in r["path"] and r["path"].endswith(".json") for r in rows), "Session export missing"
    assert all(r["trainable"] is False for r in rows)
    print("Real OpenCode mock-provider tool execution, capture, source collection and session export passed")


if __name__ == "__main__":
    check(Path(sys.argv[1]))
