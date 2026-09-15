"""Trusted Linux/Docker controller for one bounded, untrusted project session.

Never execute generated code on this host. Copy regular-file bytes from paused
containers into a content-addressed observation archive; never extract their tar.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import selectors
import signal
import subprocess
import tarfile
import time
import uuid

from lypning.evidence import snapshot
from .worker import MODEL, OPENCODE_VERSION

MAX_ARCHIVE = 64 * 1024 * 1024
MAX_FILE = 16 * 1024 * 1024
CONTEXT = Path(__file__).resolve().parent


class ContainerCommandError(RuntimeError):
    def __init__(self, argv, stderr):
        super().__init__("Container command failed: " + argv[0])
        self.operation = argv[:3]
        secret = os.environ.get("CEREBRAS_API_KEY", "").encode()
        clean = stderr.replace(secret, b"[REDACTED_PROVIDER_KEY]") if secret else stderr
        self.detail = clean[:4096].decode("utf-8", errors="replace")


def command(argv, *, limit=MAX_ARCHIVE, timeout=60, env=None):
    """Bound Docker transport output and wall time, including malicious logs."""
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    out, err = bytearray(), bytearray()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, out)
    selector.register(process.stderr, selectors.EVENT_READ, err)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            if time.monotonic() >= deadline:
                raise RuntimeError("Container command deadline exceeded")
            for key, _ in selector.select(0.2):
                block = os.read(key.fd, 65536)
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                key.data.extend(block)
                if len(out) + len(err) > limit:
                    raise RuntimeError("Container command output limit exceeded")
        process.wait(timeout=max(0.01, deadline - time.monotonic()))
        if process.returncode:
            # No raw stderr in the trusted job log (could contain credentials).
            raise ContainerCommandError(argv, bytes(err))
        return bytes(out)
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()


def load_tasks():
    tasks = json.loads((CONTEXT / "tasks.json").read_text())
    if len(tasks) != 12 or len({task["id"] for task in tasks}) != len(tasks):
        raise ValueError("Invalid authored catalog")
    return tasks


def plan(projects=4, rounds=1, parallelism=4):
    if not 1 <= projects <= 12 or not 1 <= rounds <= 4 or not 1 <= parallelism <= 4:
        raise ValueError("projects=1..12, rounds=1..4, parallelism=1..4 required")
    return {"include": [{"task_index": index, "round": repeat}
                        for repeat in range(rounds) for index in range(projects)]}


def collect(tar_bytes, output, prefix, secret=b""):
    """Untrusted paths/types never become filesystem targets, even on failure."""
    blobs = output / "blobs"
    blobs.mkdir(exist_ok=True)
    records = []
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as archive:
        for index, member in enumerate(archive):
            if index >= 4096:
                raise ValueError("Archive member cap exceeded")
            name = PurePosixPath(member.name)
            row = {"kind": "harvest_file", "collection": prefix, "path": member.name,
                   "size": member.size, "trainable": False, "correctness": "unknown"}
            if name.is_absolute() or ".." in name.parts or not member.isfile() or member.size > MAX_FILE:
                row["omitted"] = "unsafe-path/nonregular/oversized"
            else:
                handle = archive.extractfile(member)
                data = handle.read(MAX_FILE + 1)
                if len(data) != member.size:
                    raise ValueError("Incomplete archive member")
                row["redacted"] = bool(secret and secret in data)
                if row["redacted"]:
                    data = data.replace(secret, b"[REDACTED_PROVIDER_KEY]")
                digest = hashlib.sha256(data).hexdigest()
                row["sha256"] = digest
                (blobs / digest).write_bytes(data)
                if prefix == "project" and name.suffix == ".py" and len(data) <= 65536:
                    try:
                        row["program"] = data.decode("utf-8")
                    except UnicodeDecodeError:
                        row["program_omitted"] = "non-UTF8"
            records.append(row)
    return records


def container_args(name, network, image):
    if not image.startswith("sha256:") or len(image) != 71:
        raise ValueError("Use the locally resolved image digest")
    return ["docker", "create", "--name", name, "--network", network,
            "--pull=never", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--user=65534:65534",
            "--pids-limit=128", "--cpus=2", "--memory=2g", "--memory-swap=2g",
            "--ulimit", "fsize=16777216:16777216",
            "--log-opt", "max-size=1m", "--log-opt", "max-file=1",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
            "--tmpfs", "/work:rw,nosuid,nodev,size=768m,uid=65534,gid=65534,mode=700"]


def run(task_index, repeat, output, worker_image, proxy_image, *, smoke=False, deadline=900):
    tasks = load_tasks()
    if not 0 <= task_index < len(tasks) or not 0 <= repeat < 4:
        raise ValueError("Invalid task index or round")
    secret = os.environ.get("CEREBRAS_API_KEY", "")
    if not smoke and not secret:
        raise ValueError("CEREBRAS_API_KEY is required on the trusted controller")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    tag = "lyp-harvest-" + uuid.uuid4().hex[:12]
    worker, proxy, network = tag + "-worker", tag + "-proxy", tag + "-net"
    revision = os.environ.get("GITHUB_SHA", "local-uncommitted")
    task = dict(tasks[task_index], model=MODEL, proxy_url="http://harvest-proxy:8080",
                run_id=os.environ.get("GITHUB_RUN_ID", tag),
                run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
                round=repeat, repo_revision=revision)
    if smoke:
        task["prompt"] = "Create main.py printing 42 and run it with python3."
    task_path = output / "task.json"
    task_path.write_text(json.dumps(task, indent=2))
    # Authored, nonsecret prompt must be readable by container UID 65534.
    # Parent output directory remains private (0700).
    task_path.chmod(0o644)
    records, errors, diagnostics, rule = [], [], [], None
    state = "infrastructure_failure"
    started = time.time()
    try:
        command(["docker", "network", "create", "--internal", network])
        subnet = command(["docker", "network", "inspect", network, "--format",
                          "{{(index .IPAM.Config 0).Subnet}}"], limit=4096).decode().strip()
        # Internal bridges still reach host services without an INPUT rule.
        import ipaddress
        ipaddress.ip_network(subnet)
        rule = ["sudo", "iptables", "-w", "-I", "INPUT", "-s", subnet, "-j", "DROP"]
        command(rule)
        args = container_args(proxy, "bridge", proxy_image)
        args += ["--tmpfs",
                 "/data:rw,nosuid,nodev,noexec,size=64m,uid=65534,gid=65534,mode=700"]
        # Do not print or embed the key in argv/config/image. Only the proxy gets it.
        child_env = {key: value for key, value in os.environ.items() if key != "CEREBRAS_API_KEY"}
        if smoke:
            args += ["--entrypoint", "python3", proxy_image, "-I", "/app/smoke_provider.py"]
        else:
            child_env["CEREBRAS_API_KEY"] = secret
            args += ["--env", "CEREBRAS_API_KEY", proxy_image, "--host", "0.0.0.0", "--port", "8080",
                     "--model", MODEL, "--max-requests", "24", "--max-output-tokens", "2048",
                     "--total-output-tokens", "49152", "--ledger", "/data/proxy.jsonl"]
        command(args, env=child_env)
        command(["docker", "network", "connect", "--alias", "harvest-proxy", network, proxy])
        command(["docker", "start", proxy])
        command(container_args(worker, network, worker_image) + [worker_image])
        # /work is tmpfs: copy the task after starting via stdin to an image-owned
        # bootstrap, not docker cp into an unmounted/soon-shadowed tmpfs.
        command(["docker", "start", worker])
        command(["docker", "cp", str(task_path), worker + ":/work/task.json"])
        until = time.monotonic() + deadline
        state = "deadline"
        while time.monotonic() < until:
            status = command(["docker", "inspect", worker, "--format", "{{.State.Running}}"], limit=4096)
            if status.strip() != b"true":
                state = "worker_exited_early"
                break
            logs = command(["docker", "logs", worker], limit=2 * 1024 * 1024)
            (output / "worker-console.txt").write_bytes(
                logs.replace(secret.encode(), b"[REDACTED_PROVIDER_KEY]") if secret else logs)
            if b"HARVEST_DONE" in logs:
                state = "worker_reported_completion"
                break
            time.sleep(2)
        command(["docker", "pause", worker])
        for directory in ("project", "output"):
            try:
                payload = command(["docker", "cp", worker + ":/work/" + directory, "-"])
                records.extend(collect(payload, output, directory, secret.encode()))
            except (RuntimeError, ValueError, tarfile.TarError):
                errors.append("collection_failed:" + directory)
        command(["docker", "pause", proxy])
        if not smoke:
            try:
                payload = command(["docker", "cp", proxy + ":/data", "-"])
                records.extend(collect(payload, output, "proxy", secret.encode()))
            except (RuntimeError, ValueError, tarfile.TarError):
                errors.append("collection_failed:proxy")
        worker_rows = [r for r in records if r["collection"] == "output" and r["path"].endswith("/worker.json")]
        if not worker_rows or not worker_rows[0].get("sha256"):
            errors.append("worker_report_missing")
        else:
            try:
                report = json.loads((output / "blobs" / worker_rows[0]["sha256"]).read_text())
                if report.get("exit_code") != 0:
                    errors.append("opencode_nonzero_exit")
            except (ValueError, AttributeError):
                errors.append("invalid_worker_report")
        for row in records:
            if row["collection"] == "output" and row["path"].endswith("/events.jsonl") and row.get("sha256"):
                for line in (output / "blobs" / row["sha256"]).read_text(errors="replace").splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and event.get("type") == "error":
                        errors.append("opencode_error_event")
                        break
    except Exception as exc:
        # No exception text: provider/container failures can echo sensitive data.
        errors.append(type(exc).__name__)
        if isinstance(exc, ContainerCommandError):
            diagnostics.append({"operation": exc.operation, "stderr": exc.detail})
    finally:
        for name in (worker, proxy):
            try:
                command(["docker", "rm", "--force", name])
            except RuntimeError:
                pass
        if rule:
            try:
                command(["-D" if part == "-I" else part for part in rule])
            except RuntimeError:
                errors.append("firewall_cleanup_failed")
        try:
            command(["docker", "network", "rm", network])
        except RuntimeError:
            pass
        manifest = {"schema": 1, "task": task, "worker_image": worker_image,
            "proxy_image": proxy_image, "opencode_version": OPENCODE_VERSION,
            "model_revision": "provider-managed; not an immutable training checkpoint",
            "started_at": started, "finished_at": time.time(), "state": state, "errors": errors,
            "diagnostics": diagnostics,
            "smoke": smoke, "trainable": False, "correctness": "unknown",
            "native_compatibility": "unmeasured; no L runtime in generation container",
            "records": records, "caps": {"requests": 24, "output_tokens_per_request": 2048,
                "reserved_output_tokens": 49152, "request_bytes": 262144, "deadline_seconds": deadline}}
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
        observations = output / "observations.jsonl"
        with observations.open("w") as handle:
            handle.write(json.dumps({"kind": "harvest_context", **{k: v for k, v in manifest.items() if k != "records"}}) + "\n")
            for row in records:
                handle.write(json.dumps(dict(row, task_id=task["id"], family=task["family"],
                    run_id=task["run_id"], round=repeat, repo_revision=revision)) + "\n")
        snapshot(observations, output / "evidence", origin=tag)
    print(json.dumps({"task": task["id"], "state": state, "files": len(records), "errors": errors}))
    return 0 if state == "worker_reported_completion" and not errors else 1


def main(argv=None):
    os.umask(0o077)
    def stop(_signum, _frame):
        raise KeyboardInterrupt("Harvest cancelled")
    signal.signal(signal.SIGTERM, stop)
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--projects", type=int, default=4)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--parallelism", type=int, default=4)
    parser.add_argument("--task-index", type=int, default=0)
    parser.add_argument("--round", type=int, default=0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker-image")
    parser.add_argument("--proxy-image")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args(argv)
    if args.plan:
        print(json.dumps(plan(args.projects, args.rounds, args.parallelism)))
        return 0
    if not args.output or not args.worker_image or not args.proxy_image:
        parser.error("--output, --worker-image and --proxy-image required")
    return run(args.task_index, args.round, args.output, args.worker_image, args.proxy_image,
               smoke=args.smoke, deadline=120 if args.smoke else 900)


if __name__ == "__main__":
    raise SystemExit(main())
