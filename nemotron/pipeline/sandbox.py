"""Run a generated Python program and come back with a verdict, not a surprise.

WHAT THIS IS. Model-generated Python is arbitrary code written by something that
was not thinking about your filesystem. Every program gets: its own temporary
working directory, a scrubbed environment (no HF_TOKEN, no cloud credentials, no
PYTHONPATH), a wall-clock timeout enforced by killing the whole process *group*,
a CPU-time rlimit so a spin loop dies even if the killer is wedged, an address
space cap, a file-size cap that also bounds stdout, and — where the kernel allows
it — its own empty network namespace.

WHAT THIS IS NOT. It is not a security boundary. `unshare -n` is real isolation
and is used when available, but the filesystem is shared: a program that names an
absolute path can still write to it, and nothing here can undo that. Same posture
as the repository's own corpus battery (CLAUDE.md invariant 4): this is a net that
makes the next occurrence loud, not a sandbox that makes it impossible. Run
harvests and evals in a throwaway checkout.

THE HARNESS/PROGRAM SPLIT. ``harness_error`` is set when *we* failed — the temp
dir could not be made, the interpreter is missing. It is never set because the
program misbehaved. A case is only ever dropped or scored on a run whose
``harness_error`` is None; anything else is our bug and must be fixed, not
counted.
"""

from __future__ import annotations

import base64
import binascii
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MEM_MB = 1024
DEFAULT_OUTPUT_CAP = 8 * 1024 * 1024  # also the RLIMIT_FSIZE, so stdout is bounded

# Kept out of the child's environment no matter what the parent has. The eval
# driver holds an inference API key; generated code must never see it.
_ENV_KEEP = ("PATH", "LANG", "LC_ALL", "TZ", "TMPDIR")


@dataclass
class RunResult:
    exit_code: Optional[int]
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    truncated: bool = False
    signal: Optional[int] = None
    harness_error: Optional[str] = None
    workdir_files: Dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Exited 0, under its own power."""
        return self.harness_error is None and not self.timed_out and self.exit_code == 0

    def brief(self, limit: int = 300) -> str:
        if self.harness_error:
            return "harness-error: " + self.harness_error
        if self.timed_out:
            return "timeout after %.1fs" % self.duration_s
        tail = (self.stderr or "").strip().splitlines()
        return "exit %s: %s" % (self.exit_code, (tail[-1][:limit] if tail else ""))


_NETNS_PROBE: Optional[bool] = None


def netns_available() -> bool:
    """Can we give the child an empty network namespace? Probed once."""
    global _NETNS_PROBE
    if _NETNS_PROBE is None:
        exe = shutil.which("unshare")
        if not exe:
            _NETNS_PROBE = False
        else:
            try:
                p = subprocess.run(
                    [exe, "-n", "--", "true"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                )
                _NETNS_PROBE = p.returncode == 0
            except Exception:
                _NETNS_PROBE = False
    return _NETNS_PROBE


def _child_setup(timeout_s: float, mem_mb: int, output_cap: int, nproc: int):
    def setup() -> None:  # pragma: no cover - runs in the forked child
        os.setsid()
        cpu = int(timeout_s) + 2
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (output_cap, output_cap))
        if mem_mb > 0:
            nbytes = mem_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (nbytes, nbytes))
        if nproc > 0:
            resource.setrlimit(resource.RLIMIT_NPROC, (nproc, nproc))
    return setup


def _scrubbed_env(workdir: Path, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
    env.setdefault("PATH", "/usr/bin:/bin")
    env["HOME"] = str(workdir)
    env["TMPDIR"] = str(workdir)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"       # a generated program must not be flaky on dict order
    env["PYTHONUNBUFFERED"] = "1"
    env["NO_COLOR"] = "1"
    if extra:
        env.update(extra)
    return env


def _read_capped(path: Path, cap: int) -> "tuple[str, bool]":
    try:
        size = path.stat().st_size
    except OSError:
        return "", False
    with path.open("rb") as fh:
        raw = fh.read(cap)
    return raw.decode("utf-8", "replace"), size > cap


def materialize(workdir: Path, files: Optional[Dict[str, Any]]) -> Optional[str]:
    """Write a case's setup files. Returns an error string if any path escapes.

    A value is either a string (written as UTF-8) or ``{"base64": "..."}`` for
    content that is not text. Binary is explicit on purpose: the alternative is a
    heuristic — "encode as latin-1 if any codepoint is above 127" — and a
    heuristic that guesses wrong writes a *different file* than the case means,
    which surfaces later as a reference solution that mysteriously fails.
    """
    for rel, content in (files or {}).items():
        if os.path.isabs(rel) or ".." in Path(rel).parts:
            return "setup file escapes workdir: %r" % rel
        target = workdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, dict):
            if "base64" not in content:
                return "binary setup file %r needs a base64 key" % rel
            try:
                target.write_bytes(base64.b64decode(content["base64"], validate=True))
            except (ValueError, binascii.Error) as exc:
                return "setup file %r: bad base64: %s" % (rel, exc)
        elif isinstance(content, str):
            target.write_text(content, encoding="utf-8")
        else:
            return "setup file %r must be a string or {\"base64\": ...}" % rel
    return None


def run_python(
    source: str,
    *,
    argv: Optional[Sequence[str]] = None,
    stdin: Optional[str] = None,
    files: Optional[Dict[str, Any]] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    mem_mb: int = DEFAULT_MEM_MB,
    output_cap: int = DEFAULT_OUTPUT_CAP,
    nproc: int = 0,
    entry: str = "solution.py",
    isolate_network: bool = True,
    env_extra: Optional[Dict[str, str]] = None,
    keep_workdir: Optional[Path] = None,
    scratch_dir: Optional[Path] = None,
) -> RunResult:
    """Run ``source`` as a script and report what happened.

    ``isolate_network`` is a request, not a guarantee: it is honoured only where
    :func:`netns_available` says the kernel will let us. The result records which
    way it went nowhere, so if you need to *assert* isolation, call
    :func:`netns_available` yourself and refuse.

    ``scratch_dir`` puts the script and the captured streams somewhere other than
    the working directory. That matters for an acceptance checker asserting on
    the files a program created: with everything in one directory, a plain
    ``os.listdir()`` returns the harness's own files alongside the program's, and
    the checker is wrong through no fault of its author.
    """
    started = time.time()
    tmp = keep_workdir
    made_tmp = False
    try:
        if tmp is None:
            tmp = Path(tempfile.mkdtemp(prefix="ntx-run-"))
            made_tmp = True
        tmp.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return RunResult(None, "", "", 0.0, harness_error="mkdtemp: %s" % exc)

    try:
        err = materialize(tmp, files)
        if err:
            return RunResult(None, "", "", 0.0, harness_error=err)
        scratch = scratch_dir or tmp
        scratch.mkdir(parents=True, exist_ok=True)
        entry_path = scratch / entry
        entry_path.write_text(source, encoding="utf-8")

        out_path = scratch / ".ntx-stdout"
        err_path = scratch / ".ntx-stderr"

        cmd: List[str] = []
        if isolate_network and netns_available():
            cmd += [shutil.which("unshare") or "unshare", "-n", "--"]
        # -E -s, not -I: -I also drops the script's own directory from sys.path,
        # which breaks any case whose test imports the solution as a module.
        cmd += [sys.executable, "-E", "-s", str(entry_path)] + [str(a) for a in (argv or [])]

        proc = None
        timed_out = False
        try:
            with out_path.open("wb") as fo, err_path.open("wb") as fe:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(tmp),
                    stdin=subprocess.PIPE,
                    stdout=fo,
                    stderr=fe,
                    env=_scrubbed_env(tmp, env_extra),
                    preexec_fn=_child_setup(timeout_s, mem_mb, output_cap, nproc),
                    close_fds=True,
                )
                try:
                    proc.communicate(
                        input=(stdin or "").encode("utf-8"), timeout=timeout_s
                    )
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _kill_group(proc)
                    try:
                        proc.communicate(timeout=5)
                    except Exception:
                        pass
        except OSError as exc:
            return RunResult(None, "", "", time.time() - started,
                             harness_error="spawn: %s" % exc)

        rc = proc.returncode if proc is not None else None
        sig = -rc if (rc is not None and rc < 0) else None
        stdout, t1 = _read_capped(out_path, output_cap)
        stderr, t2 = _read_capped(err_path, output_cap)
        listing = {}
        try:
            for p in sorted(tmp.rglob("*")):
                if p.is_file() and not p.name.startswith(".ntx-") and p.name != entry:  # noqa: E501
                    listing[str(p.relative_to(tmp))] = p.stat().st_size
        except OSError:
            pass
        return RunResult(
            exit_code=None if timed_out else rc,
            stdout=stdout,
            stderr=stderr,
            duration_s=time.time() - started,
            timed_out=timed_out,
            truncated=t1 or t2,
            signal=sig,
            workdir_files=listing,
        )
    finally:
        if made_tmp and tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def _kill_group(proc: "subprocess.Popen") -> None:
    for sig in (signal.SIGKILL,):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except Exception:
                pass
