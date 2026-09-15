"""Inert fake-Docker lifecycle tests: no containers, network or generated code."""
from __future__ import annotations

import io
import json
import re
import tarfile

import pytest

from harvesting import runner
from lypning.evidence import load_snapshot


FAKE_KEY = "fake-provider-key-for-lifecycle-tests"
WORKER_IMAGE = "sha256:" + "a" * 64
PROXY_IMAGE = "sha256:" + "b" * 64


def _archive(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return stream.getvalue()


class FakeDocker:
    def __init__(self, mode):
        self.mode = mode
        self.calls = []
        self.interrupted = False
        self.project = _archive({"project/main.py": b"print(42)\n"})
        outputs = {"output/events.jsonl": b'{"type":"step_start"}\n'}
        if mode == "complete":
            outputs["output/worker.json"] = b'{"exit_code":0,"exports":{"ses_fixture":0},"trainable":false}'
        self.output = _archive(outputs)
        self.proxy = _archive({"data/proxy.jsonl": json.dumps({
            "kind": "request", "observed_text": FAKE_KEY,
            "trainable": False,
        }).encode() + b"\n"})

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append((argv, kwargs))
        if argv[:3] == ["docker", "network", "inspect"]:
            return b"172.29.91.0/24\n"
        if argv[:2] == ["docker", "inspect"]:
            if not self.interrupted and self.mode in ("controller_error", "cancelled"):
                self.interrupted = True
                if self.mode == "cancelled":
                    raise KeyboardInterrupt("fake cancellation")
                raise RuntimeError("fake Docker transport failure")
            return b"false\n" if self.mode == "early_exit" else b"true\n"
        if argv[:2] == ["docker", "logs"]:
            return b"HARVEST_DONE\n"
        if argv[:2] == ["docker", "exec"]:
            assert "-i" in argv
            payload = kwargs["input_bytes"]
            assert isinstance(payload, bytes) and len(payload) <= 8192
            assert FAKE_KEY.encode() not in payload
            task = json.loads(payload)
            assert task["model"] == runner.MODEL
            return b""
        if argv[:2] == ["docker", "pause"]:
            if self.mode == "early_exit" and argv[2].endswith("-worker"):
                raise runner.ContainerCommandError(argv, b"container is not running")
            return b""
        if argv[:2] == ["docker", "run"]:
            source = argv[-1]
            if source == "/capture/project":
                return self.project
            if source == "/capture/output":
                return self.output
            if source == "/capture":
                return self.proxy
            raise AssertionError("unexpected fake collection source")
        if argv[:3] in (["docker", "network", "create"],
                        ["docker", "network", "connect"],
                        ["docker", "network", "rm"],
                        ["docker", "volume", "create"],
                        ["docker", "volume", "rm"]):
            return b""
        if argv[:2] in (["docker", "create"], ["docker", "start"],
                        ["docker", "rm"], ["sudo", "iptables"]):
            return b""
        raise AssertionError("unexpected fake Docker operation")


def _assert_targeted_cleanup(fake):
    calls = [argv for argv, _ in fake.calls]
    creates = [argv for argv in calls if argv[:2] == ["docker", "create"]]
    names = [argv[argv.index("--name") + 1] for argv in creates]
    assert len(names) == 2 and len(set(names)) == 2
    assert all(re.fullmatch(r"lyp-harvest-[0-9a-f]{12}-(worker|proxy)", name) for name in names)
    collectors = [argv for argv in calls if argv[:2] == ["docker", "run"]]
    collector_names = [argv[argv.index("--name") + 1] for argv in collectors]
    assert len(collector_names) == (1 if fake.mode == "early_exit" else 3)
    assert len(collector_names) == len(set(collector_names))
    assert all(any(name.startswith(producer + "-collector-") for producer in names)
               for name in collector_names)
    removals = [argv for argv in calls if argv[:2] == ["docker", "rm"]]
    assert sorted(removals) == sorted([["docker", "rm", "--force", name]
                                      for name in names + collector_names])
    network_create = next(argv for argv in calls if argv[:3] == ["docker", "network", "create"])
    network = network_create[-1]
    assert network.endswith("-net") and "--internal" in network_create
    assert [argv for argv in calls if argv[:3] == ["docker", "network", "rm"]] == [
        ["docker", "network", "rm", network]
    ]
    firewall = [argv for argv in calls if argv[:2] == ["sudo", "iptables"]]
    assert len(firewall) == 2
    assert firewall[0] == ["sudo", "iptables", "-w", "-I", "INPUT", "-s",
                           "172.29.91.0/24", "-j", "DROP"]
    assert firewall[1] == ["-D" if item == "-I" else item for item in firewall[0]]
    first_removal = min(calls.index(["docker", "rm", "--force", name]) for name in names)
    collections = [index for index, argv in enumerate(calls)
                   if argv[:2] == ["docker", "run"]]
    assert len(collections) == (1 if fake.mode == "early_exit" else 3)
    assert max(collections) < first_removal
    pauses = [argv for argv in calls if argv[:2] == ["docker", "pause"]]
    assert sorted(argv[2] for argv in pauses) == sorted(names)
    assert calls.index(["docker", "network", "rm", network]) > first_removal
    volume_creates = [argv for argv in calls if argv[:3] == ["docker", "volume", "create"]]
    volumes = [argv[-1] for argv in volume_creates]
    assert len(volumes) == 2 and len(set(volumes)) == 2
    assert all(re.fullmatch(r"lyp-harvest-[0-9a-f]{12}-(work|ledger)", volume) for volume in volumes)
    for argv in volume_creates:
        assert "type=tmpfs" in argv and "device=tmpfs" in argv
        assert any(value.startswith("o=size=") and "uid=65534,gid=65534,mode=700" in value
                   for value in argv)
    volume_removes = [argv for argv in calls if argv[:3] == ["docker", "volume", "rm"]]
    assert sorted(volume_removes) == sorted([["docker", "volume", "rm", volume] for volume in volumes])
    assert min(calls.index(argv) for argv in volume_removes) > max(calls.index(argv) for argv in removals)
    for argv in collectors:
        assert "--rm" in argv and "--network=none" in argv and "--read-only" in argv
        assert "--cap-drop=ALL" in argv and "--security-opt=no-new-privileges" in argv
        assert "--user=65534:65534" in argv and "--memory=256m" in argv
        assert "--env" not in argv and "--env-file" not in argv
        mount = argv[argv.index("--mount") + 1]
        assert mount in {"type=volume,source=" + volume + ",destination=/capture,readonly"
                         for volume in volumes}
        assert PROXY_IMAGE in argv and "/app/collector.py" in argv
        collector = argv[argv.index("--name") + 1]
        assert calls.index(["docker", "rm", "--force", collector]) > calls.index(argv)
    assert all("docker.sock" not in " ".join(argv) and "type=bind" not in " ".join(argv)
               for argv in calls)


def _assert_credential_boundary(fake):
    creates = [(argv, kwargs) for argv, kwargs in fake.calls
               if argv[:2] == ["docker", "create"]]
    proxy = next((argv, kwargs) for argv, kwargs in creates
                 if argv[argv.index("--name") + 1].endswith("-proxy"))
    worker = next((argv, kwargs) for argv, kwargs in creates
                  if argv[argv.index("--name") + 1].endswith("-worker"))
    assert proxy[0][proxy[0].index("--env") + 1] == "CEREBRAS_API_KEY"
    assert proxy[1]["env"]["CEREBRAS_API_KEY"] == FAKE_KEY
    assert "--env" not in worker[0] and "-e" not in worker[0]
    assert "CEREBRAS_API_KEY" not in (worker[1].get("env") or {})
    assert all(FAKE_KEY not in " ".join(argv) for argv, _ in fake.calls)
    assert all("--env-file" not in argv for argv, _ in fake.calls)


@pytest.mark.parametrize("mode", ["complete", "early_exit", "controller_error", "cancelled", "deadline"])
def test_partial_evidence_and_targeted_cleanup_survive_lifecycle(mode, tmp_path, monkeypatch):
    fake = FakeDocker(mode)
    monkeypatch.setenv("CEREBRAS_API_KEY", FAKE_KEY)
    monkeypatch.setenv("GITHUB_SHA", "c" * 40)
    monkeypatch.setattr(runner, "command", fake)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)

    def no_real_process(*args, **kwargs):
        raise AssertionError("Lifecycle test must not start a process")

    monkeypatch.setattr(runner.subprocess, "Popen", no_real_process)
    output = tmp_path / "harvest"
    result = runner.run(0, 0, output, WORKER_IMAGE, PROXY_IMAGE,
                        deadline=0 if mode == "deadline" else 1)
    if mode == "complete":
        assert result == 0
    else:
        assert result == 1

    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["trainable"] is False and manifest["correctness"] == "unknown"
    assert manifest["task"]["repo_revision"] == "c" * 40
    if mode == "complete":
        assert manifest["state"] == "worker_reported_completion"
        assert not manifest["errors"]
    else:
        assert manifest["state"] != "worker_reported_completion"
    if mode == "cancelled":
        assert manifest["state"] == "cancelled"
        assert "cancelled" in manifest["errors"]
    if mode == "deadline":
        assert manifest["state"] == "deadline"
    if mode == "controller_error":
        assert "RuntimeError" in manifest["errors"]
    expected_collections = {"proxy"} if mode == "early_exit" else {"project", "output", "proxy"}
    assert {row["collection"] for row in manifest["records"]} == expected_collections
    if mode == "early_exit":
        assert "container_unavailable_for_collection:worker" in manifest["errors"]
    else:
        assert any(row.get("program") == "print(42)\n" for row in manifest["records"])
    assert all(row["trainable"] is False for row in manifest["records"])
    assert not (output / "project").exists()  # Generated names were never extracted.
    snapshot_manifest, events = load_snapshot(output / "evidence")
    assert snapshot_manifest["digest"]
    assert len(events) == len(manifest["records"]) + 1
    assert all(not event["quarantine"] for event in events)
    assert all(FAKE_KEY.encode() not in path.read_bytes()
               for path in output.rglob("*") if path.is_file())
    _assert_targeted_cleanup(fake)
    _assert_credential_boundary(fake)
