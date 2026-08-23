"""Run every generated program through the capture shim, once.

This is the feed that needs no new code at all: `lypning install` puts a
`python3` on `$PATH` that logs one JSON line and then execs the real
interpreter, so a program only has to be *run* to be captured. The study
generated 884 of them, and running each once is what turns a study into corpus
growth.

It is deliberately separate from :mod:`study.score`, which points
``STUDY_CPYTHON`` at ``/usr/bin/python3`` and logs nothing: the oracle must be
the real interpreter, and folding the scoring pass into the capture feed would
put three timed runs of every program in the log for every one that was typed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as H  # noqa: E402


def main() -> int:
    shim = Path(os.environ.get("LYPNING_HOME", str(Path.home() / ".lypning"))) / "bin" / "python3"
    if not shim.exists():
        raise SystemExit("no shim at %s — run `lypning install` first" % shim)
    if not os.environ.get("LYPNING_LOG"):
        raise SystemExit("set LYPNING_LOG to the capture log this pass should append to")
    os.environ["STUDY_CPYTHON"] = str(shim)

    tasks = {t["id"]: t for t in H.load_tasks()}
    rows = [json.loads(ln) for ln in
            (H.STUDY / "data" / "programs.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    before = H.git_snapshot()
    ok = skipped = 0
    for i, r in enumerate(rows, 1):
        t = tasks[r["task"]]
        if H.skips_for_absolute_path(r["program"], t):
            skipped += 1
            continue
        H.run_program(r["program"], t, "cpython")
        ok += 1
        if i % 200 == 0:
            print("  %d/%d" % (i, len(rows)), file=sys.stderr)
    changed = H.check_net(before, H.git_snapshot())
    if changed:
        print("\nNET TRIPPED — the capture pass changed tracked files:\n  %s"
              % "\n  ".join(changed), file=sys.stderr)
    print("shim pass: %d programs run through %s, %d skipped for an absolute path"
          % (ok, shim, skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
