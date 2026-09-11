"""Run a generated Python program and come back with a verdict, not a surprise.

WHAT THIS IS. Model-generated Python is arbitrary code written by something that
was not thinking about your filesystem. Every program gets: its own temporary
working directory holding nothing of ours but the script itself, a scrubbed
environment (no HF_TOKEN, no cloud credentials, no inherited PYTHON* of any
kind), a wall-clock timeout enforced by killing the whole process *group*,
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

THE CHILD IS THE ORACLE. CPython here is not just a runner, it is the reference
every rewrite is graded against, so two things have to hold at once and they
pull apart. The run must be *reproducible* — the same program must give the same
answer on the next run and on someone else's machine — and it must still be the
*same interpreter* a plain ``python3 solution.py`` would give. So the child's
PYTHON* settings are chosen one at a time, each with its reason, in
:func:`_scrubbed_env`: a setting earns its place only by removing a source of
drift, and one that would also move the oracle is pinned to whatever the
ambient default already was rather than to the tidier value.
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

# The whole of the child's inherited environment: `env=` replaces it wholesale,
# so anything not named here simply is not there. The eval driver holds an
# inference API key; generated code must never see it. No PYTHON* name may ever
# join this tuple — the child's interpreter settings are ours to decide, and a
# harness whose oracle moves with the operator's shell is measuring the shell.
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
    # Each of these reaches the child for real (the spawn below does not pass
    # -E), so each is a deliberate choice about the oracle, not decoration.
    #
    # A generated program must not be flaky on set or dict-view order: without
    # this, the same program grades differently on consecutive runs and every
    # pass@k is a coin flip on order-sensitive cases.
    env["PYTHONHASHSEED"] = "0"
    # Importing a case's setup module otherwise drops __pycache__/ into the
    # program's own working directory: a file the program can see that was not
    # there on the first run, so run 1 and run 2 of one program differ.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Pin the text encoding so a program emitting non-ASCII is graded the same
    # everywhere, but pin the *error handler* to what an unconfigured CPython
    # already does here rather than to the stricter default: plain "utf-8" would
    # move sys.stdout.errors from surrogateescape to strict and turn a program
    # that writes a lone surrogate from exit 0 into a UnicodeEncodeError. That
    # is the oracle changing under us, which no determinism fix may buy.
    env["PYTHONIOENCODING"] = "utf-8:surrogateescape"
    # Same reasoning one level down: LANG and LC_ALL are inherited, so without
    # this the filesystem encoding and open()'s default follow the operator's
    # shell. Under this repo's own C locale CPython already coerces to UTF-8
    # mode, so this pins the behaviour we have instead of introducing one.
    env["PYTHONUTF8"] = "1"
    # Deliberately NOT set: PYTHONUNBUFFERED. It buys only partial stdout from a
    # program we SIGKILL, and a killed run is scored `timeout` without its stdout
    # ever being compared — while it does flip sys.stdout.write_through, which
    # order-of-output programs can observe.
    # Deliberately NOT set: PYTHONPATH. The script runs from the directory it
    # sits in, so sys.path[0] already reaches the case's setup files; pointing
    # PYTHONPATH at the program's working directory would additionally let a
    # program shadow a stdlib module for every later child, the acceptance
    # checker included.
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
    interpreter: Optional[Sequence[str]] = None,
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

    The captured streams are never in the working directory — always a private
    directory of ours, removed on the way out. They are pure harness artefacts:
    a program that can see them counts them in ``os.listdir()``, and a program
    that can unlink and rewrite ``.ntx-stdout`` replaces its own recorded stdout
    with whatever it likes, which we would read back as its output and score.

    The entry script, by contrast, stays in the working directory, because that
    is where a plain ``python3 solution.py`` puts it: it is what ``sys.path[0]``
    and ``__file__`` mean, and what the model is told to expect. ``scratch_dir``
    moves it out for a caller that needs the working directory to hold nothing
    but the program's own files — the ``script`` kind, whose checker asserts on
    exactly that.
    """
    started = time.time()
    tmp = keep_workdir
    made_tmp = False
    capture: Optional[Path] = None
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

        try:
            capture = Path(tempfile.mkdtemp(prefix="ntx-cap-"))
        except OSError as exc:
            return RunResult(None, "", "", 0.0, harness_error="mkdtemp: %s" % exc)
        out_path = capture / ".ntx-stdout"
        err_path = capture / ".ntx-stderr"

        cmd: List[str] = []
        if isolate_network and netns_available():
            cmd += [shutil.which("unshare") or "unshare", "-n", "--"]
        if interpreter:
            # An engine binary. No -E/-s: those are CPython's flags, and the
            # Rust core would reject them as program arguments.
            cmd += [str(x) for x in interpreter] + [str(entry_path)]
        else:
            # -s alone. Not -E: `env=` above already replaces the environment
            # wholesale, so -E protects against nothing that can still reach us
            # and instead voids the PYTHON* settings we just chose — including
            # the PYTHONHASHSEED that keeps set and dict order reproducible.
            # Not -I either: it implies -E, and drops the script's own directory
            # from sys.path, which breaks a case whose test imports the solution.
            cmd += [sys.executable, "-s", str(entry_path)]
        cmd += [str(a) for a in (argv or [])]

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
        # The entry script is excluded by identity, not by name: a program is
        # free to write its own `solution.py`, or a `sub/solution.py`, and the
        # listing has to say so.
        try:
            ours = entry_path.resolve()
        except OSError:
            ours = entry_path
        listing = {}
        try:
            for p in sorted(tmp.rglob("*")):
                if not p.is_file():
                    continue
                try:
                    same = p.resolve() == ours
                except OSError:
                    same = False
                if not same:
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
        if capture is not None:
            shutil.rmtree(capture, ignore_errors=True)
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
