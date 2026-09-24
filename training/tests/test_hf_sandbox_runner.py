from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from types import SimpleNamespace

from pathlib import Path

import pytest

from pipeline import hf_sandbox_runner
from pipeline.hf_sandbox_runner import HfSandboxPoolRunner, expected_identity
from pipeline.sandbox import RunResult
from pipeline.training import execution_contract, execution_runner, validate_execution
from pipeline.training_types import TrainingError, VerificationBlocked

IMAGE = "hf.co/spaces/someone/verifier"
REVISION = "d" * 40
BUNDLE = {k: "expected-" + k for k in hf_sandbox_runner.BUNDLE_FIELDS}
WORKER_SHA = hashlib.sha256(hf_sandbox_runner.WORKER_SOURCE.read_bytes()).hexdigest()


def image_identity(python="python-a", **drift):
    """What the worker inside the admitted image reports, optionally drifted."""
    ident = dict(BUNDLE, worker_sha256=WORKER_SHA, python_sha256=python)
    ident.update(drift)
    return ident


def reply(result, identity=None, code=0, timed_out=False):
    body = {"protocol": 1, "result": result}
    if identity is not None:
        body["identity"] = identity
    return code, json.dumps(body), timed_out


OK = reply(asdict(RunResult(0, "answer", "", .1)), image_identity())


class FakeSandbox:
    def __init__(self, log, replies):
        self.log, self.replies, self.killed = log, replies, False

    def run(self, cmd, **kw):
        self.log.append(("run", cmd, kw))
        code, out, timed_out = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return SimpleNamespace(exit_code=code, stdout=out, stderr="", timed_out=timed_out)

    def kill(self):
        self.killed = True
        self.log.append(("kill",))


class FakePool:
    def __init__(self, log, replies):
        self.log, self.replies, self.boxes = log, list(replies), []

    def create(self, **kw):
        self.log.append(("create", kw))
        box = FakeSandbox(self.log, self.replies)
        self.boxes.append(box)
        return box

    def close(self):
        self.log.append(("close",))


def runner(*replies, check=False, **kw):
    log = []
    pool = FakePool(log, replies or (OK,))
    kw.setdefault("sleep", lambda s: log.append(("sleep", s)))
    return HfSandboxPoolRunner(IMAGE, REVISION, BUNDLE, check=check, pool=pool, **kw), log


class HubError(Exception):
    """Shaped like `HfHubHTTPError`: the status rides on `.response`."""
    def __init__(self, status):
        super().__init__("Server error '%d' for url https://huggingface.co/api/sandboxes" % status)
        self.response = SimpleNamespace(status_code=status)


class FlakyPool(FakePool):
    """`create` raises the queued errors first, then serves like the fake pool."""
    def __init__(self, log, replies, errors):
        super().__init__(log, replies)
        self.errors = list(errors)

    def create(self, **kw):
        if self.errors:
            self.log.append(("create-failed",))
            raise self.errors.pop(0)
        return super().create(**kw)


def test_request_carries_only_the_candidate_and_never_the_token():
    r, log = runner()
    result = r("print(1)", stdin="input", files={"a.txt": "x"}, interpreter=["/host/private/engine"])
    assert result.stdout == "answer"
    created = [e for e in log if e[0] == "create"]
    assert created == [("create", {"idle_timeout": hf_sandbox_runner.IDLE_TIMEOUT, "forward_hf_token": False})]
    ran = [e for e in log if e[0] == "run"][0]
    assert ran[1] == ["python3", "-I", hf_sandbox_runner.WORKER]
    request = json.loads(ran[2]["stdin"])
    assert request["native"] is True and "/host/private" not in ran[2]["stdin"]
    assert set(request) == {"protocol", "action", "program", "argv", "stdin", "files", "timeout_s", "mem_mb", "native"}
    assert "stdout" not in request and "case_id" not in request
    assert log[-1] == ("kill",), "the sandbox is released after every request"


def test_worker_failure_timeout_and_bad_json_are_blocked_not_scored():
    for rep, match in ((reply({}, image_identity(), code=3), "exit 3"), ((None, "", True), "timed out"),
                       ((0, "not json", False), "invalid"), ((0, json.dumps({"protocol": 2}), False), "mismatch")):
        r, log = runner(rep)
        with pytest.raises(VerificationBlocked, match=match):
            r("pass")
        assert log[-1] == ("kill",), "killed even on failure"


def test_transport_exceptions_become_blocks_and_never_rewards():
    r, log = runner()
    class Boom(FakePool):
        def create(self, **kw):
            raise RuntimeError("proxy down")
    r._pool = Boom(log, [OK])
    with pytest.raises(VerificationBlocked, match="transport failed"):
        r("pass")


def test_a_transient_hub_failure_is_retried_with_backoff_and_a_persistent_one_blocks():
    r, log = runner()
    r._pool = FlakyPool(log, [OK], [HubError(500), ConnectionError("reset")])
    assert r("pass").stdout == "answer", "the third attempt answers"
    b = hf_sandbox_runner.TRANSPORT_BACKOFF_S
    assert [e for e in log if e[0] in ("sleep", "create-failed")] == [
        ("create-failed",), ("sleep", b), ("create-failed",), ("sleep", 2 * b)]
    n = hf_sandbox_runner.TRANSPORT_ATTEMPTS
    r, log = runner()
    r._pool = FlakyPool(log, [OK], [HubError(503)] * n)
    with pytest.raises(VerificationBlocked, match=r"HubError: Server error '503'.*after %d attempts" % n):
        r("pass")
    waits = [e[1] for e in log if e[0] == "sleep"]
    assert waits == hf_sandbox_runner.backoff_schedule(), "bounded: one wait fewer than tries"
    assert waits[:5] == [b * 2 ** i for i in range(5)], "doubling from the first wait"
    assert max(waits) == hf_sandbox_runner.TRANSPORT_BACKOFF_CAP_S, "each wait is capped"
    # 2026-09-24: a 503 outage longer than 155 s ended a ~9.5 h h200 run 2.2 h in.
    # The budget rides out a half-hour outage and still ends, never hangs.
    assert 1500 < sum(waits) <= hf_sandbox_runner.TRANSPORT_BUDGET_S


def test_a_client_error_is_refused_at_once():
    r, log = runner()
    r._pool = FlakyPool(log, [OK], [HubError(403)])
    with pytest.raises(VerificationBlocked, match="403"):
        r("pass")
    assert not [e for e in log if e[0] == "sleep"], "a 4xx will not change on retry"


def test_a_moved_space_aborts_through_a_request_instead_of_blocking():
    r = HfSandboxPoolRunner(IMAGE, REVISION, BUNDLE, check=False, space_sha=lambda: "e" * 40, sleep=lambda s: None)
    with pytest.raises(TrainingError, match="not the pinned"):
        r("pass")


def test_image_and_revision_must_be_immutable():
    with pytest.raises(TrainingError, match="hf.co/spaces"):
        HfSandboxPoolRunner("python:3.12", REVISION, BUNDLE, check=False, pool=object())
    with pytest.raises(TrainingError, match="40-character"):
        HfSandboxPoolRunner(IMAGE, "main", BUNDLE, check=False, pool=object())


def test_identity_drift_is_refused_at_the_handshake_before_any_candidate_runs():
    for drift in ({"sha256": "other"}, {"worker_sha256": "other"}, {"oracle": "3.13"}):
        pool = FakePool([], [reply(image_identity(**drift), image_identity(**drift))])
        with pytest.raises(TrainingError, match="differs"):
            HfSandboxPoolRunner(IMAGE, REVISION, BUNDLE, pool=pool)
    assert expected_identity(BUNDLE)["worker_sha256"] == WORKER_SHA, "the worker this tree ships is part of the identity"


def test_every_response_must_carry_the_admitted_identity():
    """A replacement host or a rebuilt image is caught on the first request it serves."""
    handshake = reply(image_identity(), image_identity())
    rebuilt = reply(asdict(RunResult(0, "answer", "", .1)), image_identity(sha256="rebuilt-engine"))
    r, log = runner(handshake, OK, rebuilt, check=True)
    assert r("pass").stdout == "answer", "the admitted image answers"
    with pytest.raises(TrainingError, match="differs .* at request"):
        r("pass")
    assert log[-1] == ("kill",)


def test_a_response_without_identity_or_with_another_interpreter_aborts():
    r, _ = runner(reply(asdict(RunResult(0, "x", "", .1))))
    with pytest.raises(TrainingError, match="no identity"):
        r("pass")
    handshake = reply(image_identity(python="python-a"), image_identity(python="python-a"))
    other_host = reply(asdict(RunResult(0, "x", "", .1)), image_identity(python="python-b"))
    r, _ = runner(handshake, other_host, check=True)
    with pytest.raises(TrainingError, match="interpreter changed"):
        r("pass")


def test_the_space_must_sit_at_the_pinned_commit_before_any_host_is_used(monkeypatch):
    """The Hub SDK has no image pin for a Space, so the Space head is the pin."""
    import sys
    class FakeSandboxPool:
        def __init__(self, **kw):
            self.kw = kw
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(SandboxPool=FakeSandboxPool))
    calls = []
    r = HfSandboxPoolRunner(IMAGE, REVISION, BUNDLE, check=False, space_sha=lambda: calls.append(1) or "e" * 40)
    with pytest.raises(TrainingError, match="not the pinned"):
        r.pool()
    assert calls == [1] and r._pool is None, "no SandboxPool was constructed"
    r = HfSandboxPoolRunner(IMAGE, REVISION, BUNDLE, check=False, space_sha=lambda: REVISION)
    pool = r.pool()
    assert pool.kw["image"] == IMAGE and pool.kw["name"] == "lypning-verifier-" + REVISION[:12]
    monkeypatch.setenv("NTX_POOL_TAG", "6aaa5c1e/f76d")
    r = HfSandboxPoolRunner(IMAGE, REVISION, BUNDLE, check=False, space_sha=lambda: REVISION)
    assert r.pool().kw["name"] == "lypning-verifier-" + REVISION[:12] + "-6aaa5c1ef76d", \
        "a run's pool is its own: a pool cancels the hosts it owns on close"
    assert hf_sandbox_runner.pool_name(REVISION, "") == "lypning-verifier-" + REVISION[:12]


def test_pool_density_and_host_ceiling_are_explicit_and_validated(monkeypatch):
    import sys
    class FakeSandboxPool:
        def __init__(self, **kw):
            self.kw = kw
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(SandboxPool=FakeSandboxPool))
    monkeypatch.setenv("NTX_POOL_SANDBOXES_PER_HOST", "4")
    monkeypatch.setenv("NTX_POOL_MAX_HOSTS", "4")
    pool = HfSandboxPoolRunner(IMAGE, REVISION, BUNDLE, check=False,
                               space_sha=lambda: REVISION).pool()
    assert pool.kw["sandboxes_per_host"] == 4 and pool.kw["max_hosts"] == 4
    for key, value in (("NTX_POOL_SANDBOXES_PER_HOST", "0"),
                       ("NTX_POOL_MAX_HOSTS", "many")):
        monkeypatch.setenv(key, value)
        with pytest.raises(TrainingError, match="positive integer"):
            HfSandboxPoolRunner(IMAGE, REVISION, BUNDLE, check=False, pool=object())
        monkeypatch.setenv(key, "4")


def test_execution_contract_round_trips_through_validation():
    assert execution_contract("docker", None) == {"kind": "local-reviewed-smoke"}
    hf = execution_contract("hf-sandbox-pool", IMAGE, REVISION)
    assert hf == {"kind": "hf-sandbox-pool", "image": IMAGE, "revision": REVISION}
    validate_execution(hf)
    for bad in ({"kind": "hf-sandbox-pool", "image": IMAGE}, {"kind": "hf-sandbox-pool", "image": IMAGE, "revision": "main"},
                {"kind": "hf-sandbox-pool", "image": "python:3.12", "revision": REVISION}, {"kind": "elsewhere"}):
        with pytest.raises(TrainingError, match="invalid execution contract"):
            validate_execution(bad)
    with pytest.raises(TrainingError, match="unknown execution kind"):
        execution_contract("elsewhere", IMAGE)


def test_execution_runner_builds_the_pool_runner_lazily(monkeypatch):
    monkeypatch.setattr(HfSandboxPoolRunner, "_request", lambda self, *a: image_identity())
    r = execution_runner({"kind": "hf-sandbox-pool", "image": IMAGE, "revision": REVISION}, BUNDLE)
    assert isinstance(r, HfSandboxPoolRunner) and r._pool is None, "no Hub call until the first request"


@pytest.mark.skipif(not os.environ.get("NTX_TEST_HF_SANDBOX_IMAGE"), reason="explicit reviewed Space image and HF token required")
def test_real_pooled_sandbox_boundary():
    """The protocol against a real pool: authored fixtures, never captured code."""
    image, revision = os.environ["NTX_TEST_HF_SANDBOX_IMAGE"].split("@")
    r = HfSandboxPoolRunner(image, revision, {}, check=False)
    try:
        res = r("import sys\nprint(sum(int(x) for x in sys.argv[1:]))", argv=["1", "2"], timeout_s=5, mem_mb=256)
        assert res.stdout == "3\n" and res.exit_code == 0 and res.harness_error is None
        res = r("print(open('in.txt').read())", files={"in.txt": "seeded"}, timeout_s=5, mem_mb=256, interpreter=["native"])
        assert res.stdout == "seeded\n"
        res = r("while True: pass", timeout_s=1, mem_mb=256)
        assert res.timed_out
        res = r("import os; print(os.getuid() >= 20000, os.environ.get('HF_TOKEN'))", timeout_s=5, mem_mb=256)
        assert res.stdout == "True None\n", res.stdout
    finally:
        r.close()


def test_each_stage_names_its_own_pool_so_it_cannot_adopt_a_dying_one():
    """The race that releasing the hosts would otherwise have created.

    `prepare` cancels its pool's hosts on the way out, which is what stops the
    next stage inheriting their occupancy. But a host cancelled a moment ago
    still answers `list_jobs(status="RUNNING")`, and `_discover_hosts` adopts
    by NAME — so a next stage sharing the name can adopt twelve dying hosts,
    then need twelve live ones, and hit `max_hosts`. That is precisely the
    failure the release was added to prevent, re-created by the release.

    Distinct names cannot adopt each other, so the race stops existing instead
    of becoming narrow enough to usually win.
    """
    from pipeline import hf_sandbox_runner as r

    pilot = r.pool_name(REVISION, "6ab01cbb51992417dfccd64c", stage="pilot")
    eval2 = r.pool_name(REVISION, "6ab01cbb51992417dfccd64c", stage="eval2")
    grpo = r.pool_name(REVISION, "6ab01cbb51992417dfccd64c", stage="grpo")
    assert len({pilot, eval2, grpo}) == 3, "stages of one job must not share a pool"

    # The tag ALONE cannot carry the stage, which is why this is a parameter: a
    # job id is exactly 24 characters and the tag is truncated to 24, so
    # `<job>-eval2` truncates straight back to `<job>`.
    job = "6ab01cbb51992417dfccd64c"
    assert len(job) == 24
    assert r.pool_name(REVISION, job + "-pilot") == r.pool_name(REVISION, job + "-eval2")

    # Two different jobs still differ, which is the property the tag was for:
    # a pool that owns its hosts cancels them on close, so two runs sharing a
    # name would tear each other's hosts down mid-stage.
    assert r.pool_name(REVISION, "6ab0139152d0dbd7f1d74da2", stage="pilot") != pilot
    # And no stage is still the old name, so nothing else has to change.
    assert r.pool_name(REVISION, "") == "lypning-verifier-" + REVISION[:12]


def test_the_runner_carries_its_stage_into_the_pool_it_builds():
    """A name nothing passes is a name nothing distinguishes."""
    import inspect
    from pipeline import hf_sandbox_runner as r
    from pipeline.training import execution_runner

    assert "stage" in inspect.signature(r.HfSandboxPoolRunner.__init__).parameters
    assert "stage" in inspect.signature(execution_runner).parameters
    source = inspect.getsource(r.HfSandboxPoolRunner._pool_locked)
    assert "stage=self._stage" in source, "the pool must be named with the stage, not without"


def test_pool_hosts_outlive_the_training_gap_between_evaluations():
    """The HOST idle timeout, not the per-sandbox one (2026-09-24, two dead jobs).

    SFT trains 25-40 minutes between evaluations with no scoring. At the SDK's
    600 s host default every host was gone by the next evaluation, and
    huggingface_hub 1.31.0 re-raises for a host it has already used instead of
    replacing it, so each request got a dead host's 503 until the budget ran out.
    """
    built = []
    def factory(**kw):
        built.append(kw)
        return FakePool([], [OK])
    r = HfSandboxPoolRunner(IMAGE, REVISION, BUNDLE, check=False, space_sha=lambda: REVISION,
                            pool_factory=factory, sleep=lambda s: None)
    assert r("pass").stdout == "answer"
    assert built[0]["idle_timeout"] == hf_sandbox_runner.HOST_IDLE_TIMEOUT == "3h"


def test_a_sandbox_server_4xx_is_refused_at_once():
    """`SandboxError` carries its status on `.status_code`, not `.response`."""
    class SandboxError(Exception):
        def __init__(self, status):
            super().__init__("Sandbox API error (%d)" % status)
            self.status_code = status
    assert hf_sandbox_runner.http_status(SandboxError(403)) == 403
    assert not hf_sandbox_runner.transient(SandboxError(401))
    assert hf_sandbox_runner.transient(SandboxError(503))
    r, log = runner()
    r._pool = FlakyPool(log, [OK], [SandboxError(403)])
    with pytest.raises(VerificationBlocked, match="403"):
        r("pass")
    assert not [e for e in log if e[0] == "sleep"]


def test_train_verified_closes_its_verifier_pool_on_every_exit():
    """Hosts outlive an unclosed pool by HOST_IDLE_TIMEOUT; `run` must release them."""
    import ast
    src = (Path(__file__).resolve().parents[1] / "gpu" / "train_verified.py").read_text()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "run")
    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    assert tries and any("release_runner" in ast.dump(t.finalbody[0]) for t in tries if t.finalbody)
