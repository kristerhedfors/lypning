"""One candidate per pooled Hugging Face sandbox; the host VM is never the trainer's.

The boundary this buys, in the words of the platform's own conceptual guide:
a Job is a VM, and a pooled sandbox inside it is "the classic Unix multi-user
primitive" — a dedicated uid (>= 20000), a private 0700 home, a scrubbed
environment, NO_NEW_PRIVS, per-process rlimits and a per-sandbox Landlock
ruleset. Pooled sandboxes are for workloads within one trust boundary: they
share the host's kernel, protection from every cross-sandbox attack is not
guaranteed, and outbound network stays open. The operator chose this tier on
2026-09-15 for candidates the trainer treats as one trust class.

What it does NOT share, and why that is the point: the host Job is a different
VM from the trainer Job. The bundle, the expected outputs, the model, the GPU
and the trainer's credentials never enter it. The request carries only the
program, argv, stdin, input files and limits — the same protocol as the Docker
runner, unchanged, so `container_worker.py` is the same file in both images.

Two things the Docker runner had that this one cannot: `--network=none` (a
pooled sandbox may connect outbound, and Landlock only stops it binding), and
a fresh kernel per candidate. Both are written down in the handoff as the
residual risk of this tier. A dedicated sandbox per request (one VM each,
about six seconds to boot) is the stronger tier and needs only `Sandbox.create`
in place of the pool; it was not chosen.
"""
from __future__ import annotations

import json
import math
import re

from .sandbox import RunResult
from .training_types import TrainingError, VerificationBlocked

PROTOCOL = 1
#: A Space-built image the sandbox host boots, pinned by the Space commit.
IMAGE_PATTERN = r"hf\.co/spaces/[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*"
REVISION_PATTERN = r"[0-9a-f]{40}"
RESPONSE_CAP = 24 * 1024 * 1024
REQUEST_CAP = 8 * 1024 * 1024
FLAVOR = "cpu-basic"
IDLE_TIMEOUT = "10m"
#: Under /usr/local on purpose: a pooled sandbox's Landlock ruleset lets it read
#: the standard system trees, and a top-level /runner is not one of them.
WORKER = "/usr/local/lib/lypning-verifier/container_worker.py"


class HfSandboxPoolRunner:
    """Same call shape as `ContainerRunner`; one pooled sandbox per request."""

    def __init__(self, image, revision, identity, *, check=True, pool=None, flavor=FLAVOR,
                 sandboxes_per_host=None, hf_token=None):
        if not isinstance(image, str) or not re.fullmatch(IMAGE_PATTERN, image):
            raise TrainingError("hf-sandbox-pool execution image must be an hf.co/spaces/<owner>/<name> image")
        if not isinstance(revision, str) or not re.fullmatch(REVISION_PATTERN, revision):
            raise TrainingError("hf-sandbox-pool execution needs the Space's immutable 40-character commit")
        self.image = image
        self.revision = revision
        self.identity = identity
        self._pool = pool
        self._flavor = flavor
        self._sandboxes_per_host = sandboxes_per_host
        self._hf_token = hf_token
        if check:
            got = self._request({"protocol": PROTOCOL, "action": "identity"}, 10, 1024)
            expected = {k: identity[k] for k in ("sha256", "version", "oracle", "sandbox_sha256", "child_exec_sha256")}
            if got != expected:
                raise TrainingError("sandbox engine/oracle/harness differs from the bundle worker: " + str(got))

    # -- the pool, created lazily so `--plan` and unit tests never touch the Hub

    def pool(self):
        if self._pool is None:
            from huggingface_hub import SandboxPool
            # Named by the Space revision: a pool attaches to any warm host with
            # the same image, flavor and NAME, and a host booted from an earlier
            # build of the same Space must never serve this bundle's requests.
            kwargs = {"image": self.image, "flavor": self._flavor,
                      "name": "lypning-verifier-" + self.revision[:12]}
            if self._sandboxes_per_host:
                kwargs["sandboxes_per_host"] = self._sandboxes_per_host
            if self._hf_token:
                kwargs["token"] = self._hf_token
            self._pool = SandboxPool(**kwargs)
        return self._pool

    def close(self):
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.close()

    # -- transport

    def transport(self, request_bytes, timeout_s):
        """Run the worker in a fresh pooled sandbox; return (exit code, stdout bytes).

        The sandbox is killed in `finally`, including on failure, so a wedged
        request never keeps a slot. The HF token is never forwarded.
        """
        sandbox = self.pool().create(idle_timeout=IDLE_TIMEOUT, forward_hf_token=False)
        try:
            result = sandbox.run(["python3", "-I", WORKER],
                                 stdin=request_bytes.decode("utf-8"), timeout=timeout_s, check=False)
            if getattr(result, "timed_out", False):
                # A transport deadline is not an observed candidate timeout: the
                # worker enforces the candidate's own limit inside the sandbox.
                raise VerificationBlocked("sandbox transport timed out")
            out = result.stdout if isinstance(result.stdout, str) else ""
            if len(out) > RESPONSE_CAP:
                raise VerificationBlocked("sandbox transport output exceeded cap")
            return result.exit_code, out.encode("utf-8")
        finally:
            try:
                sandbox.kill()
            except Exception:
                pass

    def _request(self, request, timeout_s, memory_mb):
        request_bytes = json.dumps(request, allow_nan=False).encode("utf-8")
        if len(request_bytes) > REQUEST_CAP:
            raise VerificationBlocked("sandbox request exceeds protocol cap")
        try:
            code, raw = self.transport(request_bytes, timeout_s + 30)
        except VerificationBlocked:
            raise
        except Exception as exc:  # transport, proxy or pool failure: never a low reward
            raise VerificationBlocked("sandbox transport failed: %s" % type(exc).__name__) from exc
        if code != 0:
            raise VerificationBlocked("sandbox worker failed; exit %s" % code)
        try:
            response = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise VerificationBlocked("invalid sandbox response") from exc
        if not isinstance(response, dict) or response.get("protocol") != PROTOCOL or "result" not in response:
            raise VerificationBlocked("sandbox protocol mismatch")
        return response["result"]

    def __call__(self, program, *, argv=None, stdin=None, files=None, timeout_s=5,
                 mem_mb=1024, interpreter=None):
        if not math.isfinite(timeout_s) or timeout_s <= 0 or not isinstance(mem_mb, int) or mem_mb <= 0:
            raise TrainingError("sandbox requires positive timeout and memory cap")
        # No expected stdout, case ID, test registry, host path or model details
        # cross this boundary. A fixed in-image engine is selected by a boolean.
        raw = self._request(dict(protocol=PROTOCOL, action="run", program=program,
                                 argv=argv, stdin=stdin, files=files,
                                 timeout_s=timeout_s, mem_mb=mem_mb,
                                 native=bool(interpreter)), timeout_s, mem_mb)
        try:
            result = RunResult(**raw)
        except (TypeError, ValueError) as exc:
            raise VerificationBlocked("malformed sandbox result") from exc
        if (not isinstance(result.stdout, str) or not isinstance(result.stderr, str) or
                type(result.timed_out) is not bool or type(result.truncated) is not bool or
                type(result.memory_exceeded) is not bool or
                type(result.encoding_error) is not bool or
                (result.exit_code is not None and type(result.exit_code) is not int)):
            raise VerificationBlocked("malformed sandbox result fields")
        return result
