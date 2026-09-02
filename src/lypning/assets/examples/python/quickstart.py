#!/usr/bin/env python3
# quickstart.py - the smallest correct lypning host, in Python: run one program
# in this process, and hand a refusal to python3 unchanged.
#   python3 src/lypning/assets/examples/python/quickstart.py "print(sum(range(10)))"
#   python3 "$(python3 -c 'from lypning import paths; print(paths.EXAMPLES_DIR / "python/quickstart.py")')" "print(1)"
# Usage: quickstart.py "<python source>" [args...]   (args become sys.argv[1:])
# SPDX-License-Identifier: MIT
from __future__ import annotations

import subprocess
import sys

from lypning import embed


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write('usage: quickstart.py "<python source>" [args...]\n')
        return 2
    src, rest = sys.argv[1], sys.argv[2:]
    lib = embed.Library()  # absent: a LibraryError whose message names `lypning build --lib`
    out = lib.run(src, args=rest, step_limit=10_000_000)  # no process to kill, so bound the work
    if out.fall_onward:
        # A refusal is not an error: lypning ran none of it and wrote nothing, so CPython runs it once.
        sys.stdout.flush()
        return subprocess.run([sys.executable, "-c", src, *rest], stdin=subprocess.DEVNULL).returncode
    sys.stdout.buffer.write(out.stdout)
    sys.stderr.buffer.write(out.stderr)
    return out.exit_code


if __name__ == "__main__":
    sys.exit(main())
