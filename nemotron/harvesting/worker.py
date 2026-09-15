"""Image-owned OpenCode entry point; outputs are untrusted observations."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import traceback

OPENCODE_VERSION = "1.18.31"
MODEL = "qwen-3.8-27b"


def config(task):
    if task["model"] != MODEL or task["proxy_url"] != "http://harvest-proxy:8080":
        raise ValueError("Only the fixed Qwen model and isolated proxy are supported")
    return {
        "$schema": "https://opencode.ai/config.json",
        "autoupdate": False, "share": "disabled", "lsp": False,
        "formatter": False, "compaction": {"auto": False, "prune": False},
        "enabled_providers": ["harvest"],
        "plugin": ["file:///app/capture.js"],
        "model": "harvest/" + MODEL, "small_model": "harvest/" + MODEL,
        "provider": {"harvest": {
            "npm": "@ai-sdk/openai-compatible", "name": "Bounded Cerebras proxy",
            "options": {"baseURL": task["proxy_url"] + "/v1", "apiKey": "local-proxy-only"},
            "models": {MODEL: {"name": MODEL, "tool_call": True,
                "limit": {"context": 64000, "output": 2048}}},
        }},
        # This is permission configuration for a disposable isolated container,
        # NOT an authorization decision made by the capture hook.
        "permission": {"*": "deny", "bash": "allow", "read": "allow",
                       "edit": "allow", "glob": "allow", "grep": "allow",
                       "list": "allow", "todowrite": "allow"},
        "agent": {"harvest": {"mode": "primary", "steps": 20,
            "description": "Complete one authored Python project",
            "prompt": "Implement the user's project in /work/project. Use Python's standard library only. "
                      "You have no internet or package installation access. Write and run tests using python3. "
                      "Preserve correct behavior; do not optimize for any timing threshold. "
                      "Do not access or modify /work/output or harness configuration. "
                      "Finish without asking questions; document assumptions."}},
    }


def session_ids(path):
    found = set()
    with path.open(errors="replace") as handle:
        for line in handle:
            if len(line) > 4 * 1024 * 1024:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            value = item.get("sessionID") if isinstance(item, dict) else None
            if isinstance(value, str) and re.fullmatch(r"ses_[A-Za-z0-9]+", value):
                found.add(value)
    return sorted(found)


def main():
    os.umask(0o077)
    root = Path("/work")
    for _ in range(300):
        if (root / "task.json").is_file():
            break
        time.sleep(0.1)
    task = json.loads((root / "task.json").read_text())
    project, output = root / "project", root / "output"
    project.mkdir(exist_ok=True)
    output.mkdir(exist_ok=True)
    # Build-time plugin dependencies/model metadata only; no credentials exist.
    shutil.copytree("/opt/template", root / "home", dirs_exist_ok=True)
    settings = root / "config.json"
    settings.write_text(json.dumps(config(task)))
    env = dict(os.environ, HOME="/work/home", XDG_CONFIG_HOME="/work/home/.config",
               XDG_DATA_HOME="/work/home/.local/share", XDG_CACHE_HOME="/work/home/.cache",
               OPENCODE_CONFIG=str(settings), OPENCODE_DISABLE_PROJECT_CONFIG="true",
               OPENCODE_DISABLE_AUTOUPDATE="true", OPENCODE_DISABLE_LSP_DOWNLOAD="true",
               OPENCODE_DISABLE_DEFAULT_PLUGINS="true", LYPNING_CAPTURE="1")
    start = time.time()
    with (output / "events.jsonl").open("wb") as stdout, (output / "stderr.txt").open("wb") as stderr:
        result = subprocess.run(["opencode", "run", "--format", "json", "--model",
            "harvest/" + MODEL, "--agent", "harvest", "--title", task["id"], task["prompt"]],
            cwd=project, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
            check=False)
    exports = {}
    for session in session_ids(output / "events.jsonl")[:8]:
        with (output / (session + ".json")).open("wb") as stdout, (output / "export-stderr.txt").open("ab") as stderr:
            try:
                exports[session] = subprocess.run(["opencode", "export", session], cwd=project,
                    env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                    timeout=30, check=False).returncode
            except subprocess.TimeoutExpired:
                exports[session] = "timeout"
    (output / "worker.json").write_text(json.dumps({"schema": 1, "task": task,
        "opencode_version": OPENCODE_VERSION, "started_at": start, "finished_at": time.time(),
        "exit_code": result.returncode, "exports": exports, "trainable": False,
        "correctness": "unknown", "producer_trust": "agent-container-untrusted"}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # There are no provider credentials in this container. Preserve setup
        # failures too; the finally-loop otherwise delays exception reporting.
        traceback.print_exc()
    finally:
        # Keep tmpfs mounted until the trusted host pauses and collects it.
        # This marker reports process completion, NEVER correctness.
        print("HARVEST_DONE", flush=True)
        while True:
            time.sleep(60)
