from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from types import SimpleNamespace

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
    return HfSandboxPoolRunner(IMAGE, REVISION, BUNDLE, check=check, pool=pool, **kw), log


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
