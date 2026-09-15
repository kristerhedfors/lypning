from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from harvesting import runner, worker


def archive(items):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as handle:
        for name, data, kind in items:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.size = len(data)
            if kind == tarfile.SYMTYPE:
                info.linkname = "/etc/passwd"
            handle.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_plan_is_bounded_and_preserves_project_family():
    assert len(runner.plan()["include"]) == 4
    assert len(runner.plan(12, 4, 4)["include"]) == 48
    for args in [(0, 1, 1), (13, 1, 1), (1, 0, 1), (1, 5, 1), (1, 1, 5)]:
        with pytest.raises(ValueError):
            runner.plan(*args)
    tasks = runner.load_tasks()
    assert len({t["family"] for t in tasks}) == 12
    for task in tasks:
        assert all(task.get(key) for key in ("id", "family", "capabilities", "prompt", "rights_basis"))


def test_untrusted_archive_never_extracts_paths_or_symlinks(tmp_path):
    rows = runner.collect(archive([
        ("project/main.py", b"print(42)\n", tarfile.REGTYPE),
        ("../../escape.py", b"bad", tarfile.REGTYPE),
        ("/absolute", b"bad", tarfile.REGTYPE),
        ("project/link", b"", tarfile.SYMTYPE),
        ("project/fifo", b"", tarfile.FIFOTYPE),
    ]), tmp_path, "project")
    assert rows[0]["program"] == "print(42)\n"
    assert all(row.get("omitted") for row in rows[1:])
    assert not (tmp_path / "project").exists()
    assert len(list((tmp_path / "blobs").iterdir())) == 1
    assert all(row["trainable"] is False for row in rows)


def test_binary_redaction_oversize_and_dedup(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "MAX_FILE", 16)
    rows = runner.collect(archive([
        ("project/x.py", b"x=secret", tarfile.REGTYPE),
        ("project/y.py", b"x=secret", tarfile.REGTYPE),
        ("project/big.py", b"a" * 17, tarfile.REGTYPE),
        ("project/binary.py", b"\xff", tarfile.REGTYPE),
    ]), tmp_path, "project", b"secret")
    assert rows[0]["redacted"]
    assert rows[0]["sha256"] == rows[1]["sha256"]
    assert rows[2]["omitted"]
    assert rows[3]["program_omitted"] == "non-UTF8"
    assert all(b"secret" not in p.read_bytes() for p in (tmp_path / "blobs").iterdir())


def test_container_boundary_and_config():
    args = runner.container_args("name", "internal", "sha256:" + "a" * 64)
    for flag in ("--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--user=65534:65534"):
        assert flag in args
    assert not any(flag in args for flag in ("--privileged", "--volume", "-v", "--env", "--pid=host"))
    cfg = worker.config({"model": worker.MODEL, "proxy_url": "http://harvest-proxy:8080"})
    assert cfg["provider"]["harvest"]["options"]["apiKey"] == "local-proxy-only"
    assert cfg["share"] == "disabled"
    assert cfg["permission"]["*"] == "deny"
    assert cfg["agent"]["harvest"]["steps"] == 20
    with pytest.raises(ValueError):
        worker.config({"model": "other", "proxy_url": "https://api.cerebras.ai"})


def test_transport_bounds_timeout_and_failure():
    with pytest.raises(RuntimeError, match="output limit"):
        runner.command([sys.executable, "-c", "print('x' * 9999)"], limit=16)
    with pytest.raises(RuntimeError, match="deadline"):
        runner.command([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.1)
    with pytest.raises(RuntimeError, match="failed"):
        runner.command([sys.executable, "-c", "raise SystemExit(1)"])


def test_session_ids_never_become_arbitrary_export_arguments(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('\n'.join(["not-json", "[]", json.dumps({"sessionID": "ses_abc"}),
                              json.dumps({"sessionID": "--unsafe"})]))
    assert worker.session_ids(path) == ["ses_abc"]


def test_workflows_never_run_billable_calls_on_pr():
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/harvest.yml").read_text()
    triggers = workflow.split("permissions:")[0]
    assert "workflow_dispatch:" in triggers
    assert "pull_request:" not in triggers and "push:" not in triggers and "schedule:" not in triggers
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "persist-credentials: false" in workflow
    assert "cancel-in-progress: false" in workflow
    smoke = (root / ".github/workflows/harvest-checks.yml").read_text()
    assert "secrets." not in smoke
