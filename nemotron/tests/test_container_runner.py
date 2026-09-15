from __future__ import annotations

from dataclasses import asdict
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from pipeline.container_runner import ContainerRunner
from pipeline.sandbox import RunResult
from pipeline.training_types import TrainingError, VerificationBlocked

IMAGE = "sha256:" + "a" * 64


def test_container_contract_no_mounts_no_expected_outputs(monkeypatch):
    calls = []
    def execute(cmd, **kw):
        calls.append((cmd, kw))
        if cmd[1] == "run":
            kw["stdout"].write(json.dumps(dict(protocol=1, result=asdict(RunResult(0, "answer", "", .1)))).encode())
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, "run", execute)
    runner = ContainerRunner(IMAGE, {}, check=False)
    result = runner("print(1)", stdin="input", interpreter=["/host/private/engine"])
    assert result.stdout == "answer"
    command, options = calls[0]
    assert "--network=none" in command and "--read-only" in command and "--pull=never" in command
    assert "--user=65534:65534" in command and "--cap-drop=ALL" in command
    assert not any(c in command for c in ("--volume", "-v", "--mount", "--gpus", "--privileged"))
    request = json.loads(options["input"])
    assert request["native"] and "/host/private" not in options["input"].decode()
    assert "stdout" not in request and "case_id" not in request
    assert calls[-1][0][:3] == ["docker", "rm", "--force"]
    assert calls[-1][0][-1] == command[command.index("--name") + 1]


def test_container_transport_timeout_cleans_up_and_blocks(monkeypatch):
    calls = []
    def execute(cmd, **kw):
        calls.append(cmd)
        if cmd[1] == "run":
            raise subprocess.TimeoutExpired(cmd, 1)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, "run", execute)
    with pytest.raises(VerificationBlocked, match="transport"):
        ContainerRunner(IMAGE, {}, check=False)("pass")
    assert calls[-1][:3] == ["docker", "rm", "--force"]


def test_mutable_image_and_identity_drift_rejected(monkeypatch):
    with pytest.raises(TrainingError, match="immutable"):
        ContainerRunner("latest", {})
    monkeypatch.setattr(ContainerRunner, "_request", lambda *a: {})
    identity = {k: "expected" for k in ("sha256", "version", "oracle", "sandbox_sha256", "child_exec_sha256")}
    with pytest.raises(TrainingError, match="differs"):
        ContainerRunner(IMAGE, identity)


@pytest.mark.skipif(not os.environ.get("NTX_TEST_EXECUTION_IMAGE"), reason="explicit reviewed Docker integration image required")
def test_real_container_boundary(tmp_path, monkeypatch):
    image = os.environ["NTX_TEST_EXECUTION_IMAGE"]
    # This protocol integration test identifies its own image. Production
    # instead compares with engine_identity() on the actual training worker.
    initial = ContainerRunner(image, {}, check=False)
    identity = initial._request({"protocol": 1, "action": "identity"}, 10, 1024)
    runner = ContainerRunner(image, identity)
    assert runner("import sys; print(sys.stdin.read())", stdin="hello").stdout == "hello\n"
    assert runner("print(6)", interpreter=["native"]).stdout == "6\n"
    refused = runner("import decimal", interpreter=["native"])
    assert refused.exit_code == 90 and not refused.stdout
    assert refused.stderr.startswith("lypning-l: unsupported: ")
    canary = tmp_path / "trainer-secret"
    canary.write_text("must not be visible")
    monkeypatch.setenv("LYPNING_TEST_SECRET", "not inherited")
    code = "import os; print(os.path.exists(%r)); print(os.getenv('LYPNING_TEST_SECRET')); print(os.path.exists('/var/run/docker.sock'))" % str(canary)
    assert runner(code).stdout == "False\nNone\nFalse\n"
    assert not runner("open('/runner/owned', 'w').write('x')").ok
    assert runner("while True: pass", timeout_s=.2).timed_out
    assert runner("print(open('input.txt').read())", files={"input.txt": "fixture"}).stdout == "fixture\n"
