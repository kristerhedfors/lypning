"""Trusted image entrypoint. Copy only this file, sandbox and child_exec.

Never COPY the repository, training registry, credentials or weights into the
candidate image. The outer runner supplies only one program and its inputs.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys

# -I ignores PYTHONPATH and script directory; this directory is image-owned,
# read-only, not a candidate input mount. Its PATH is not fixed: the Docker
# image keeps it at /runner, and the Hugging Face sandbox image puts it under
# /usr/local/lib, because a pooled sandbox's Landlock ruleset reads the
# standard system trees and nothing else at the root.
HERE = str(Path(__file__).resolve().parent)
sys.path.insert(0, HERE)
import sandbox


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity():
    """What this image is, measured from inside it on every request.

    A pooled sandbox image is named by a Space, and a Space name is not an
    immutable image: a rebuild or a replacement host can serve different bytes
    under the same name. So every response, not only the handshake, carries
    the engine, the harness, this worker and the interpreter, and the runner
    refuses any response whose identity is not the one the bundle admitted.
    """
    return {"sha256": sha("/usr/local/bin/lypning-l"),
            "version": subprocess.check_output(["/usr/local/bin/lypning-l", "--version"], text=True).strip(),
            "oracle": sys.version, "sandbox_sha256": sha(Path(HERE) / "sandbox.py"),
            "child_exec_sha256": sha(Path(HERE) / "child_exec.py"),
            "worker_sha256": sha(__file__),
            "python_sha256": sha(Path(sys.executable).resolve())}


class HealthHandler(BaseHTTPRequestHandler):
    """A content-free liveness endpoint for the private verifier Space."""

    def do_GET(self):
        if self.path not in ("/", "/healthz"):
            self.send_error(404)
            return
        body = b"ok\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *args):
        pass


def serve_health(host="0.0.0.0", port=None):
    """Keep a Space healthy without exposing the verifier protocol or identity."""
    port = int(os.environ.get("PORT", "7860")) if port is None else int(port)
    ThreadingHTTPServer((host, port), HealthHandler).serve_forever()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--health-server"]:
        serve_health()
        return
    if argv:
        raise ValueError("unknown arguments")
    request = json.loads(sys.stdin.buffer.read(8 * 1024 * 1024 + 1))
    if request["protocol"] != 1:
        raise ValueError("protocol mismatch")
    if request["action"] == "identity":
        result = identity()
    elif request["action"] == "run":
        result = asdict(sandbox.run_python(request["program"], argv=request["argv"],
            stdin=request["stdin"], files=request["files"], timeout_s=request["timeout_s"],
            mem_mb=request["mem_mb"],
            interpreter=["/usr/local/bin/lypning-l"] if request["native"] else None))
    else:
        raise ValueError("unknown action")
    print(json.dumps({"protocol": 1, "result": result, "identity": identity()}))


if __name__ == "__main__":
    main()
