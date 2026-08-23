"""The Python host, over ``lypning.embed`` — ctypes, stdlib only.

Same walk as the C, C++, Rust and Node drivers, and no fall-onward for the same
reason: this counts what the subset itself takes, and a driver that quietly
answered from CPython would report a coverage the subset does not have.

It logs each run to ``$LYPNING_LOG`` in the shim's own record shape. This is the
one host where the omission is easiest to miss: the driver *is* a python
process, so the shim on ``$PATH`` logs `run_py.py` — the script — and none of
the programs it ran through the library. One sighting where there should be
hundreds is worse than none, because it looks like the feed is working.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from lypning import embed  # noqa: E402

LOG = os.environ.get("LYPNING_LOG", "")
SESSION = os.environ.get("LYPNING_STUDY_SESSION", "")


def capture(host: str, program: str, args, exit_code: int, wall_ms: int) -> None:
    if not LOG:
        return
    rec = {
        "kind": "python_invocation",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session": SESSION or None,
        "shim": host,
        "pid": os.getpid(),
        "program": program,
        "module": None,
        "script": None,
        "argv_tail": list(args),
        "stdin_pipe": True,
        "stdin_kind": "bytes",
        "exit_code": exit_code,
        "wall_ms": wall_ms,
    }
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError:
        pass  # best-effort, exactly like the shim


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("usage: run_py.py <hostset-dir>\n")
        return 2
    root = Path(sys.argv[1])
    lib = embed.Library()
    ran = refused = other = n = 0
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        prog_path = d / "program.py"
        if not prog_path.exists():
            continue
        program = prog_path.read_text(encoding="utf-8")
        n += 1
        stdin = (d / "stdin").read_bytes() if (d / "stdin").exists() else b""
        args = [a for a in ((d / "args").read_text(encoding="utf-8").splitlines()
                            if (d / "args").exists() else []) if a]
        # The program runs in THIS process; give it the entry directory, where
        # prepare.py put the fixtures it was written against.
        home = Path.cwd()
        os.chdir(d)
        t0 = time.monotonic()
        try:
            out = lib.run(program, args=args, stdin=stdin,
                          step_limit=200_000_000, output_limit=1 << 20)
        finally:
            os.chdir(home)
        ms = int((time.monotonic() - t0) * 1000)
        if out.status == embed.OK:
            ran += 1
        elif out.status == embed.UNSUPPORTED:
            refused += 1
        else:
            other += 1
        capture("python-embed", program, args, out.exit_code, ms)
    print("python-embed %d programs: %d ran, %d refused, %d other" % (n, ran, refused, other))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
