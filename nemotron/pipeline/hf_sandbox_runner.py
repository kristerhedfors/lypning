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

A third thing it cannot have: an immutable image reference. The Hub SDK names
a Space image `hf.co/spaces/<owner>/<name>` and offers no revision or digest
on that string (huggingface_hub 1.31.0, read 2026-09-16), so the pinned commit
in the bundle selects nothing by itself. This runner fails closed instead, in
two places. Before the first host is touched, the Space's current commit on
the Hub must equal the pinned one, so the only image the Space can currently
have built is the pinned commit's. And every response from every sandbox
carries the identity the worker measures from inside the image (engine,
harness, the worker itself, the interpreter); a response whose identity is
not the one the handshake admitted aborts the run. A replacement host or a
rebuilt image cannot serve a single request unnoticed.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
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


#: The five fields the bundle records, plus the worker file this tree ships.
BUNDLE_FIELDS = ("sha256", "version", "oracle", "sandbox_sha256", "child_exec_sha256")
WORKER_SOURCE = Path(__file__).with_name("container_worker.py")


def expected_identity(identity):
    """What every sandbox must report: the bundle's engine/oracle/harness and this worker."""
    expected = {k: identity[k] for k in BUNDLE_FIELDS}
    expected["worker_sha256"] = hashlib.sha256(WORKER_SOURCE.read_bytes()).hexdigest()
    return expected


class HfSandboxPoolRunner:
    """Same call shape as `ContainerRunner`; one pooled sandbox per request."""

    def __init__(self, image, revision, identity, *, check=True, pool=None, flavor=FLAVOR,
                 sandboxes_per_host=None, hf_token=None, space_sha=None):
        if not isinstance(image, str) or not re.fullmatch(IMAGE_PATTERN, image):
            raise TrainingError("hf-sandbox-pool execution image must be an hf.co/spaces/<owner>/<name> image")
        if not isinstance(revision, str) or not re.fullmatch(REVISION_PATTERN, revision):
            raise TrainingError("hf-sandbox-pool execution needs the Space's immutable 40-character commit")
        self.image = image
        self.revision = revision
        self.identity = identity
        self._expected = expected_identity(identity)
        #: The interpreter hash the handshake observed; every later response must repeat it.
        self._python_sha256 = None
        self._pool = pool
        self._flavor = flavor
        self._sandboxes_per_host = sandboxes_per_host
        self._hf_token = hf_token
        self._space_sha = space_sha
        if check:
            got = self._request({"protocol": PROTOCOL, "action": "identity"}, 10, 1024)
            self._admit(got, "handshake")

    def _admit(self, got, where):
        """Abort unless `got` is the admitted identity; never a low reward, never a retry."""
        if not isinstance(got, dict):
            raise TrainingError("sandbox %s carried no identity" % where)
        observed = {k: got.get(k) for k in self._expected}
        if observed != self._expected:
            raise TrainingError("sandbox engine/oracle/harness differs from the bundle worker at %s: %s" % (where, observed))
        python = got.get("python_sha256")
        if not isinstance(python, str) or not python:
            raise TrainingError("sandbox %s did not identify its interpreter" % where)
        if self._python_sha256 is None:
            self._python_sha256 = python
        elif python != self._python_sha256:
            raise TrainingError("sandbox interpreter changed under the run at %s" % where)

    # -- the pool, created lazily so `--plan` and unit tests never touch the Hub

    def space_head(self):
        """The Space's current commit on the Hub; the only image it can have built now."""
        if self._space_sha is not None:
            return self._space_sha()
        from huggingface_hub import HfApi
        repo_id = self.image[len("hf.co/spaces/"):]
        return HfApi(token=self._hf_token).space_info(repo_id).sha

    def pool(self):
        if self._pool is None:
            head = self.space_head()
            if head != self.revision:
                raise TrainingError("verifier Space %s is at %s, not the pinned %s; a Space name is not an immutable image"
                                    % (self.image, head, self.revision))
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
        # Every response identifies the image that produced it; a fresh
        # sandbox on a replacement host is admitted or refused on its own words.
        self._admit(response.get("identity"), "request")
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
