"""Run generated programs on each engine, behind the same net the corpus runs behind.

The study's unit of evidence is one *program*, and every number in
``docs/PROMPTING.md`` is derived here rather than asserted. A program is scored
on three independent axes, and keeping them separate is the whole point:

``correct``    CPython ran it and it produced the task's expected stdout. This is
               "did the agent solve the task", and it is measured on CPython so
               that a subset refusal can never be mistaken for a wrong answer.
``route``      what ``lypning route`` — the Rust core's own parser — predicts,
               with no execution. The cheap static answer.
``verdict``    what actually happened on the engine: MATCH, UNSUPPORTED (exit
               90), or MISMATCH. These are conformance's words on purpose, and
               MISMATCH here means the same thing it means there: a bug.

Route and verdict come apart, which is why both are recorded. ``print(2**100)``
routes to ``lypning`` because no parser can see an overflow coming, and then
refuses at runtime with ``unsupported: bigint``. A study that measured only the
static route would report that prompting had produced a tier-1 program when it
had produced a refusal.

THE NET. CLAUDE.md invariant 4 applies with full force: these programs were
written by agents asked to read files, write files and list directories, and a
few of them will do it to whatever directory they are started in. So every run
gets its own temp cwd, a *separate* one per engine so the second cannot read
back what the first wrote; a program naming an absolute path is skipped rather
than run; and :func:`git_snapshot` brackets the whole battery. That is a net,
not a sandbox — it cannot undo a write outside the repository, it only makes
the next occurrence loud.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "study"
TASKS = STUDY / "tasks.jsonl"

#: The refusal contract. Exit 90, one line on stderr, nothing on stdout.
UNSUPPORTED_EXIT = 90

MATCH, UNSUPPORTED, MISMATCH, ERROR = "MATCH", "UNSUPPORTED", "MISMATCH", "ERROR"

# An entry naming an absolute path cannot be run in a temp cwd without reaching
# outside it. `lypning conformance` skips those and so does this.
ABSOLUTE_PATH = re.compile(r"""["'](/(?:etc|usr|var|home|root|srv|opt|tmp|bin|sbin|proc|sys|dev)\b[^"']*)["']""")


def lypning_bin() -> Path:
    return Path(os.environ.get("LYPNING_HOME", str(Path.home() / ".lypning"))) / "bin" / "lypning"


def cpython_bin() -> str:
    return os.environ.get("STUDY_CPYTHON", "/usr/bin/python3")


def load_tasks() -> List[Dict[str, Any]]:
    return [json.loads(ln) for ln in TASKS.read_text(encoding="utf-8").splitlines() if ln.strip()]


def skips_for_absolute_path(program: str, task: Dict[str, Any]) -> Optional[str]:
    """Absolute paths the task did not itself supply are a reason not to run."""
    allowed = set()
    for a in task.get("argv") or []:
        allowed.add(a)
    for m in ABSOLUTE_PATH.finditer(program):
        if m.group(1) not in allowed:
            return m.group(1)
    return None


def _materialise(cwd: Path, task: Dict[str, Any]) -> None:
    for name, content in (task.get("files") or {}).items():
        p = cwd / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content.encode("latin-1") if any(ord(c) > 127 for c in content)
                      else content.encode("utf-8"))


def run_program(program: str, task: Dict[str, Any], engine: str,
                timeout: float = 20.0) -> Dict[str, Any]:
    """One program, one engine, in its own temp cwd. Never raises."""
    if engine == "cpython":
        argv = [cpython_bin(), "-c", program]
    elif engine == "lypning":
        argv = [str(lypning_bin()), "-c", program]
    elif engine == "mixture":
        argv = [str(lypning_bin()), "run", "-c", program]
    else:
        raise ValueError("unknown engine %r" % engine)
    argv += [str(a) for a in (task.get("argv") or [])]

    stdin = (task.get("stdin") or "").encode("utf-8")
    # LYPNING_LOG is passed through so a run made deliberately through the
    # capture shim lands in the log the caller chose. Scoring runs point
    # STUDY_CPYTHON at the real interpreter and log nothing.
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/root"),
           "LC_ALL": "C.UTF-8", "LYPNING_HOME": os.environ.get("LYPNING_HOME", ""),
           "LYPNING_LOG": os.environ.get("LYPNING_LOG", ""),
           "CLAUDE_CODE_SESSION_ID": os.environ.get("LYPNING_STUDY_SESSION", "")}
    env = {k: v for k, v in env.items() if v}
    cwd = Path(tempfile.mkdtemp(prefix="lypning-study-%s-" % engine))
    try:
        _materialise(cwd, task)
        try:
            proc = subprocess.run(argv, input=stdin, cwd=str(cwd), env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"exit": -9, "stdout": "", "stderr": "timeout", "timeout": True}
        except OSError as exc:
            return {"exit": -1, "stdout": "", "stderr": str(exc), "error": True}
        return {
            "exit": proc.returncode,
            "stdout": proc.stdout.decode("utf-8", "replace"),
            "stderr": proc.stderr.decode("utf-8", "replace"),
        }
    finally:
        shutil.rmtree(cwd, ignore_errors=True)


def route(program: str) -> Dict[str, str]:
    """The classifier's static answer — one parse, no execution."""
    try:
        proc = subprocess.run([str(lypning_bin()), "route", "--json", "-c", program],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"engine": "?", "kind": "route-failed", "detail": str(exc)}
    try:
        return json.loads(proc.stdout.decode("utf-8", "replace"))
    except ValueError:
        return {"engine": "?", "kind": "route-unparsed", "detail": proc.stdout[:200].decode("utf-8", "replace")}


def verdict_for(ref: Dict[str, Any], got: Dict[str, Any]) -> str:
    """Conformance's vocabulary, and its rule.

    A refusal is exit 90 with an empty stdout — the contract, checked rather
    than trusted, because a "refusal" that printed something is a barrier bug
    and must not be scored as a clean UNSUPPORTED.
    """
    if got.get("exit") == UNSUPPORTED_EXIT:
        return UNSUPPORTED if got.get("stdout") == "" else MISMATCH
    if got.get("exit", -1) < 0:
        return ERROR
    if got.get("stdout") == ref.get("stdout") and got.get("exit") == ref.get("exit"):
        return MATCH
    return MISMATCH


def score_one(program: str, task: Dict[str, Any]) -> Dict[str, Any]:
    """Everything known about one generated program."""
    rec: Dict[str, Any] = {"task": task["id"], "program": program,
                           "bytes": len(program.encode("utf-8")),
                           "lines": len(program.splitlines())}
    skip = skips_for_absolute_path(program, task)
    if skip:
        rec.update(skipped=skip, correct=False, verdict="SKIPPED", route="-")
        return rec

    ref = run_program(program, task, "cpython")
    rec["cpython_exit"] = ref["exit"]
    rec["cpython_stdout"] = ref["stdout"][:4096]
    rec["correct"] = bool(ref["exit"] == 0 and ref["stdout"] == task["expect_stdout"])

    r = route(program)
    rec["route"] = r.get("engine", "?")
    rec["route_kind"] = r.get("kind", "")
    rec["route_detail"] = r.get("detail", "")

    got = run_program(program, task, "lypning")
    rec["lypning_exit"] = got["exit"]
    rec["verdict"] = verdict_for(ref, got)
    if rec["verdict"] == UNSUPPORTED:
        line = (got.get("stderr") or "").strip().splitlines()
        rec["refusal"] = line[-1] if line else ""
        m = re.search(r"unsupported:\s*([a-z-]+):", rec["refusal"])
        rec["refusal_kind"] = m.group(1) if m else ""
    elif rec["verdict"] == MISMATCH:
        rec["lypning_stdout"] = got["stdout"][:2048]
        rec["lypning_stderr"] = got["stderr"][:2048]
    # The number the study is actually about: the program ran on the cheapest
    # tier, agreed with CPython, AND answered the question that was asked.
    rec["tier1_win"] = bool(rec["verdict"] == MATCH and rec["correct"])
    return rec


# --- the net -----------------------------------------------------------------


def git_snapshot() -> str:
    try:
        return subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT),
                              stdout=subprocess.PIPE, timeout=60).stdout.decode()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def check_net(before: str, after: str) -> List[str]:
    b, a = set(before.splitlines()), set(after.splitlines())
    return sorted(a - b)


if __name__ == "__main__":
    print("%d tasks, lypning at %s" % (len(load_tasks()), lypning_bin()))
