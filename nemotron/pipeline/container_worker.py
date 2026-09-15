"""Trusted image entrypoint. Copy only this file, sandbox and child_exec.

Never COPY the repository, training registry, credentials or weights into the
candidate image. The outer runner supplies only one program and its inputs.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys

# -I ignores PYTHONPATH and script directory; this directory is image-owned,
# read-only, not a candidate input mount.
sys.path.insert(0, "/runner")
import sandbox


def main():
    request = json.loads(sys.stdin.buffer.read(8 * 1024 * 1024 + 1))
    if request["protocol"] != 1:
        raise ValueError("protocol mismatch")
    if request["action"] == "identity":
        def sha(path):
            return hashlib.sha256(Path(path).read_bytes()).hexdigest()
        result = {"sha256": sha("/usr/local/bin/lypning-l"),
                  "version": subprocess.check_output(["/usr/local/bin/lypning-l", "--version"], text=True).strip(),
                  "oracle": sys.version, "sandbox_sha256": sha("/runner/sandbox.py"),
                  "child_exec_sha256": sha("/runner/child_exec.py")}
    elif request["action"] == "run":
        result = asdict(sandbox.run_python(request["program"], argv=request["argv"],
            stdin=request["stdin"], files=request["files"], timeout_s=request["timeout_s"],
            mem_mb=request["mem_mb"],
            interpreter=["/usr/local/bin/lypning-l"] if request["native"] else None))
    else:
        raise ValueError("unknown action")
    print(json.dumps({"protocol": 1, "result": result}))


if __name__ == "__main__":
    main()
