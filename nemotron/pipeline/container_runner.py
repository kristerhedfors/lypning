"""One candidate per locked-down, immutable Docker container; no host mounts.

Docker shares a kernel: use a disposable dedicated worker, not a sensitive
machine. This is not a claim of resistance to kernel/container-runtime exploits.
The daemon belongs to the trusted trainer; candidates never receive its socket,
GPU, credentials, bundle, expected outputs, or other candidates' directories.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

from .sandbox import RunResult
from .training_types import TrainingError, VerificationBlocked

PROTOCOL = 1
IMAGE_PATTERN = r"sha256:[0-9a-f]{64}"
RESPONSE_CAP = 24 * 1024 * 1024


class ContainerRunner:
    def __init__(self, image, identity, *, check=True):
        if not isinstance(image, str) or not re.fullmatch(IMAGE_PATTERN, image):
            raise TrainingError("execution image must be a local immutable sha256 image ID")
        self.image = image
        self.identity = identity
        if check:
            got = self._request({"protocol": PROTOCOL, "action": "identity"}, 10, 1024)
            expected = {k: identity[k] for k in ("sha256", "version", "oracle", "sandbox_sha256", "child_exec_sha256")}
            if got != expected:
                raise TrainingError("container engine/oracle/harness differs from the bundle worker: " + str(got))

    def command(self, name, memory_mb):
        return ["docker", "run", "--name", name, "--rm", "--pull=never", "--interactive",
                "--network=none", "--read-only", "--cap-drop=ALL",
                "--security-opt=no-new-privileges", "--user=65534:65534",
                "--pids-limit=64", "--cpus=1", "--memory=%dm" % (memory_mb + 128),
                "--memory-swap=%dm" % (memory_mb + 128),
                "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
                "--entrypoint=python3", self.image, "-I", "/runner/container_worker.py"]

    def _request(self, request, timeout_s, memory_mb):
        request_bytes = json.dumps(request, allow_nan=False).encode("utf-8")
        if len(request_bytes) > 8 * 1024 * 1024:
            raise VerificationBlocked("container request exceeds protocol cap")
        name = "lypning-verify-" + uuid.uuid4().hex
        # Disk-backed transport prevents candidate output from filling trainer
        # memory. The trusted worker also caps each child output at 8 MiB.
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                result = subprocess.run(self.command(name, memory_mb),
                    input=request_bytes,
                    stdout=out, stderr=err, timeout=timeout_s + 30, check=False)
                out.seek(0)
                raw = out.read(RESPONSE_CAP + 1)
                if result.returncode != 0 or len(raw) > RESPONSE_CAP:
                    raise VerificationBlocked("container failed or response exceeded cap; exit %s" % result.returncode)
                try:
                    response = json.loads(raw)
                except (ValueError, UnicodeError) as exc:
                    raise VerificationBlocked("invalid container response") from exc
                if not isinstance(response, dict) or response.get("protocol") != PROTOCOL or "result" not in response:
                    raise VerificationBlocked("container protocol mismatch")
                return response["result"]
            except subprocess.TimeoutExpired as exc:
                # A transport/daemon timeout is not an observed candidate timeout.
                raise VerificationBlocked("container transport timed out") from exc
            finally:
                # Killing a docker client does NOT kill its container. Target
                # only the unique name this request created, including failures.
                subprocess.run(["docker", "rm", "--force", name], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=15, check=False)

    def __call__(self, program, *, argv=None, stdin=None, files=None, timeout_s=5,
                 mem_mb=1024, interpreter=None):
        if not math.isfinite(timeout_s) or timeout_s <= 0 or not isinstance(mem_mb, int) or mem_mb <= 0:
            raise TrainingError("container requires positive timeout and memory cap")
        # No expected stdout, case ID, test registry, host path or model details
        # cross this boundary. A fixed in-image engine is selected by a boolean.
        raw = self._request(dict(protocol=PROTOCOL, action="run", program=program,
                                 argv=argv, stdin=stdin, files=files,
                                 timeout_s=timeout_s, mem_mb=mem_mb,
                                 native=bool(interpreter)), timeout_s, mem_mb)
        try:
            result = RunResult(**raw)
        except (TypeError, ValueError) as exc:
            raise VerificationBlocked("malformed container result") from exc
        if (not isinstance(result.stdout, str) or not isinstance(result.stderr, str) or
                type(result.timed_out) is not bool or type(result.truncated) is not bool or
                type(result.memory_exceeded) is not bool or
                type(result.encoding_error) is not bool or
                (result.exit_code is not None and type(result.exit_code) is not int)):
            raise VerificationBlocked("malformed container result fields")
        return result
