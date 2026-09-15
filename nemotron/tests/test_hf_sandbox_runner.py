from __future__ import annotations

from dataclasses import asdict
import json
import os
from types import SimpleNamespace

import pytest

from pipeline import hf_sandbox_runner
from pipeline.hf_sandbox_runner import HfSandboxPoolRunner
from pipeline.sandbox import RunResult
from pipeline.training import execution_contract, execution_runner, validate_execution
from pipeline.training_types import TrainingError, VerificationBlocked

IMAGE = "hf.co/spaces/someone/verifier"
REVISION = "d" * 40


class FakeSandbox:
    def __init__(self, log, reply):
        self.log, self.reply, self.killed = log, reply, False

    def run(self, cmd, **kw):
        self.log.append(("run", cmd, kw))
        code, out, timed_out = self.reply
        return SimpleNamespace(exit_code=code, stdout=out, stderr="", timed_out=timed_out)

    def kill(self):
        self.killed = True
        self.log.append(("kill",))


class FakePool:
    def __init__(self, log, reply):
        self.log, self.reply, self.boxes = log, reply, []

    def create(self, **kw):
        self.log.append(("create", kw))
        box = FakeSandbox(self.log, self.reply)
        self.boxes.append(box)
        return box

    def close(self):
        self.log.append(("close",))


def runner(reply, **kw):
    log = []
    pool = FakePool(log, reply)
    return HfSandboxPoolRunner(IMAGE, REVISION, {}, check=False, pool=pool, **kw), log


def test_request_carries_only_the_candidate_and_never_the_token():
    reply = (0, json.dumps(dict(protocol=1, result=asdict(RunResult(0, "answer", "", .1)))), False)
    r, log = runner(reply)
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
    for reply, match in (((3, "", False), "exit 3"), ((None, "", True), "timed out"),
                         ((0, "not json", False), "invalid"), ((0, json.dumps({"protocol": 2}), False), "mismatch")):
        r, log = runner(reply)
        with pytest.raises(VerificationBlocked, match=match):
            r("pass")
        assert log[-1] == ("kill",), "killed even on failure"


def test_transport_exceptions_become_blocks_and_never_rewards():
    r, log = runner((0, "", False))
    class Boom(FakePool):
        def create(self, **kw):
            raise RuntimeError("proxy down")
    r._pool = Boom(log, (0, "", False))
    with pytest.raises(VerificationBlocked, match="transport failed"):
        r("pass")


def test_image_and_revision_must_be_immutable():
    with pytest.raises(TrainingError, match="hf.co/spaces"):
        HfSandboxPoolRunner("python:3.12", REVISION, {}, check=False, pool=object())
    with pytest.raises(TrainingError, match="40-character"):
        HfSandboxPoolRunner(IMAGE, "main", {}, check=False, pool=object())


def test_identity_drift_is_refused_before_any_candidate_runs():
    identity = {k: "expected" for k in ("sha256", "version", "oracle", "sandbox_sha256", "child_exec_sha256")}
    reply = (0, json.dumps(dict(protocol=1, result=dict(identity, sha256="other"))), False)
    with pytest.raises(TrainingError, match="differs"):
        HfSandboxPoolRunner(IMAGE, REVISION, identity, pool=FakePool([], reply))


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
    monkeypatch.setattr(HfSandboxPoolRunner, "_request", lambda self, *a: {
        k: "same" for k in ("sha256", "version", "oracle", "sandbox_sha256", "child_exec_sha256")})
    identity = {k: "same" for k in ("sha256", "version", "oracle", "sandbox_sha256", "child_exec_sha256")}
    r = execution_runner({"kind": "hf-sandbox-pool", "image": IMAGE, "revision": REVISION}, identity)
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
