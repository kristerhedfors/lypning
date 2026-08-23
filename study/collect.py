"""Fold the generation workflows' returns into one flat record per program.

Every generated program is kept verbatim, including the ones that turn out to
be wrong, refused, or identical to another cell's. A study that stored only the
programs that worked would be reporting on its own filter.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as H  # noqa: E402

OUT = H.STUDY / "data" / "programs.jsonl"


def records_from(path: Path):
    blob = json.loads(path.read_text(encoding="utf-8"))
    cells = blob["result"] if isinstance(blob, dict) and "result" in blob else blob
    for cell in cells or []:
        if not cell:
            continue
        for p in cell.get("programs") or []:
            if not isinstance(p, dict) or not p.get("program"):
                continue
            yield {
                "treatment": cell["treatment"],
                "label": cell["label"],
                "replicate": cell["replicate"],
                "task": p.get("task", ""),
                "program": p["program"],
                "attempts": p.get("attempts"),
                "note": p.get("note", ""),
            }


def main(argv) -> int:
    if not argv:
        raise SystemExit("usage: collect.py <workflow-output.json> [...]")
    known = {t["id"] for t in H.load_tasks()}
    out, dropped = [], 0
    for a in argv:
        for rec in records_from(Path(a)):
            if rec["task"] not in known:
                dropped += 1
                continue
            out.append(rec)
    out.sort(key=lambda r: (r["treatment"], r["replicate"], r["task"]))
    OUT.write_text("".join(json.dumps(r) + "\n" for r in out), encoding="utf-8")
    cells = {(r["treatment"], r["replicate"]) for r in out}
    print("%s: %d programs, %d cells, %d dropped for an unknown task id"
          % (OUT.relative_to(H.ROOT), len(out), len(cells), dropped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
