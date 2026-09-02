"""Lay out the program set every host driver reads.

A JSON parser is a dependency, and the C host has none — so the set is a
directory of plain files rather than a JSONL every host would have to parse
its own way. One directory per program: the program itself, its stdin, and its
arguments one per line. Every host walks it the same way, which is the only
reason the results are comparable across hosts.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness as H  # noqa: E402

OUT = H.STUDY / "data" / "hostset"


def main() -> int:
    src = H.STUDY / "data" / "programs.jsonl"
    if not src.exists():
        raise SystemExit("no %s yet — run the generation workflows first" % src)
    seen = {}
    for ln in src.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        rec = json.loads(ln)
        prog = rec["program"]
        if prog not in seen:
            seen[prog] = rec
    tasks = {t["id"]: t for t in H.load_tasks()}
    # The reference solutions go in too: they are programs a human wrote for the
    # same tasks, and a host set made only of generated programs would under-
    # represent the shapes the corpus already has.
    for t in tasks.values():
        seen.setdefault(t["reference"], {"task": t["id"], "program": t["reference"]})

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    n = 0
    for i, (prog, rec) in enumerate(sorted(seen.items()), 1):
        d = OUT / ("%04d" % i)
        d.mkdir()
        (d / "program.py").write_text(prog, encoding="utf-8")
        t = tasks.get(rec.get("task"), {})
        (d / "stdin").write_text(t.get("stdin") or "", encoding="utf-8")
        (d / "args").write_text("".join(str(a) + "\n" for a in (t.get("argv") or [])),
                                encoding="utf-8")
        # The task's fixtures, so a host that chdir's into this directory gives
        # the program the working directory it was written against. Without
        # them every file-reading program raises, and every host agrees on a
        # FileNotFoundError instead of on what the subset takes.
        for name, content in (t.get("files") or {}).items():
            f = d / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(content.encode("latin-1") if any(ord(c) > 127 for c in content)
                          else content.encode("utf-8"))
        n += 1
    print("%s: %d distinct programs" % (OUT.relative_to(H.ROOT), n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
