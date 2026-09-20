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
import os
from pathlib import Path
import re
import threading
import time

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
#: A transport failure that may not recur — a Hub 5xx or 429, a dropped
#: connection — is retried this many times with doubling backoff before the
#: request is blocked; a 4xx is refused at once. Six tries wait 155 s in all,
#: a fraction of a GPU hour. Identity drift is never retried (`_admit`): on
#: 2026-09-16 one Hub 500 on a sandbox create ended a 55-minute run at its
#: first eval request.
TRANSPORT_ATTEMPTS = 6
TRANSPORT_BACKOFF_S = 5.0


#: The five fields the bundle records, plus the worker file this tree ships.
BUNDLE_FIELDS = ("sha256", "version", "oracle", "sandbox_sha256", "child_exec_sha256")
WORKER_SOURCE = Path(__file__).with_name("container_worker.py")


def pool_name(revision, tag=None, stage=None):
    """The pool's name: the Space revision, the run's own tag, and its STAGE.

    The stage is in the name because a round's stages are separate processes
    and `prepare` now cancels its hosts on the way out. A host that has just
    been cancelled still answers `list_jobs(status="RUNNING")` for a few
    seconds, so a later stage sharing the NAME can adopt twelve dying hosts,
    then need twelve live ones, and hit `max_hosts` — which is the failure
    releasing them was meant to prevent, re-created by the release. Names that
    differ cannot adopt each other, so the race does not exist rather than
    being narrow enough to usually win.

    The tag alone cannot carry the stage: a job id is exactly 24 characters and
    the tag is truncated to 24, so `<job>-eval2` truncates back to `<job>`.
    """
    tag = os.environ.get("NTX_POOL_TAG", "") if tag is None else tag
    tag = re.sub(r"[^A-Za-z0-9._-]", "", str(tag))[:24]
    stage = re.sub(r"[^A-Za-z0-9._-]", "", str(stage or ""))[:12]
    return ("lypning-verifier-" + revision[:12]
            + ("-" + tag if tag else "") + ("-" + stage if stage else ""))


def pool_limit(value, name):
    """A positive optional pool limit, from a flag or the job environment."""
    if value in (None, ""):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise TrainingError("%s must be a positive integer" % name) from exc
    if value <= 0:
        raise TrainingError("%s must be a positive integer" % name)
    return value


def http_status(exc):
    """The HTTP status an SDK error carries, or None for a connection-level failure."""
    code = getattr(getattr(exc, "response", None), "status_code", None)
    return code if isinstance(code, int) else None


def transient(exc):
    """A transport failure worth one more try: no HTTP status, a 5xx, or 429."""
    code = http_status(exc)
    return code is None or code >= 500 or code == 429


def expected_identity(identity):
    """What every sandbox must report: the bundle's engine/oracle/harness and this worker."""
    expected = {k: identity[k] for k in BUNDLE_FIELDS}
    expected["worker_sha256"] = hashlib.sha256(WORKER_SOURCE.read_bytes()).hexdigest()
    return expected


class HfSandboxPoolRunner:
    """Same call shape as `ContainerRunner`; one pooled sandbox per request."""

    def __init__(self, image, revision, identity, *, check=True, pool=None, flavor=FLAVOR,
                 sandboxes_per_host=None, max_hosts=None, hf_token=None, space_sha=None,
                 sleep=time.sleep, stage=None):
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
        self._sandboxes_per_host = pool_limit(
            sandboxes_per_host if sandboxes_per_host is not None else
            os.environ.get("NTX_POOL_SANDBOXES_PER_HOST"),
            "sandboxes per host")
        self._max_hosts = pool_limit(
            max_hosts if max_hosts is not None else os.environ.get("NTX_POOL_MAX_HOSTS"),
            "maximum pool hosts")
        self._hf_token = hf_token
        self._space_sha = space_sha
        self._stage = stage
        self._sleep = sleep
        #: Scorings run concurrently (`gpu/verified_evaluation.py`); the pool is
        #: built once and the interpreter admitted once, whichever thread is first.
        self._lock = threading.RLock()
        if check:
            got = self._request({"protocol": PROTOCOL, "action": "identity"}, 10, 1024)
            self._admit(got, "handshake")

    def _admit(self, got, where):
        """Abort unless `got` is the admitted identity; never a low reward, never a retry."""
        with self._lock:
            self._admit_locked(got, where)

    def _admit_locked(self, got, where):
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
        with self._lock:
            return self._pool_locked()

    def _pool_locked(self):
        if self._pool is None:
            head = self.space_head()
            if head != self.revision:
                raise TrainingError("verifier Space %s is at %s, not the pinned %s; a Space name is not an immutable image"
                                    % (self.image, head, self.revision))
            from huggingface_hub import SandboxPool
            # Named by the Space revision: a pool attaches to any warm host with
            # the same image, flavor and NAME, and a host booted from an earlier
            # build of the same Space must never serve this bundle's requests.
            # And by the run (`NTX_POOL_TAG`, the job id): a pool that owns its
            # hosts cancels them on close, so two runs sharing a name would tear
            # each other's hosts down mid-stage (jobs 6aaa4b2c and 6aaa5c1e,
            # 2026-09-16).
            kwargs = {"image": self.image, "flavor": self._flavor, "name": pool_name(self.revision, stage=self._stage)}
            if self._sandboxes_per_host is not None:
                kwargs["sandboxes_per_host"] = self._sandboxes_per_host
            if self._max_hosts is not None:
                kwargs["max_hosts"] = self._max_hosts
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
        attempt = 0
        while True:
            attempt += 1
            try:
                code, raw = self.transport(request_bytes, timeout_s + 30)
                break
            except (VerificationBlocked, TrainingError):
                raise  # a block is final; a moved Space or drifted identity aborts, never softens
            except Exception as exc:  # transport, proxy or pool failure: never a low reward
                reason = "sandbox transport failed: %s: %s" % (type(exc).__name__, str(exc)[:200])
                if attempt >= TRANSPORT_ATTEMPTS or not transient(exc):
                    if attempt > 1:
                        reason += " (after %d attempts)" % attempt
                    raise VerificationBlocked(reason) from exc
                self._sleep(TRANSPORT_BACKOFF_S * 2 ** (attempt - 1))
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
