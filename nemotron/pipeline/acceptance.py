"""What makes a case a case: a test that runs, and that can tell two programs apart.

THE RULE (step 1 of the build order). A harvested case is kept only if its
acceptance test *executes* and *discriminates*. Four gates, in order, and a case
that fails any of them is dropped with the gate's name recorded — never patched,
never widened. A test nobody can pass is a mislabelled case; a test everybody
passes is not a test.

    runs          the test executes with no harness error, inside its timeout
    satisfiable   the reference solution PASSES it
                  (no reference and no recorded failing program => `no-witness`,
                   dropped by default: nothing here can show the test works)
    discriminates the empty program FAILS it, and the recorded failing
                  generation — the one that put this case in the corpus —
                  FAILS it too
    stable        the witness program gets the same verdict twice

The third gate is the one that pays for itself. The failing generation is a
*negative control we already have*: a test that the known-bad program passes is
measuring something other than the bug we harvested.

TEST KINDS. ``stdout`` compares stdout and exit code after a named
normalization. ``script`` hands an arbitrary stdlib checker the observation
(stdout, stderr, exit, the files the program left behind) on stdin and reads its
exit code. ``pytest`` runs a test file against ``solution.py``. ``lypning`` is
this project's own, and is described below. All four run through
:mod:`pipeline.sandbox`, so all four get the same net.

THE ``lypning`` KIND, AND WHY CORRECTNESS IS CHECKED FIRST. This repository's
subset runtime answers a one-liner in-process; a program that leaves the subset
refuses with exit 90 and costs a full CPython spawn instead. So a program here
is graded on two axes: it must produce CPython's answer, and it should stay on
the cheap engine. **Correctness is evaluated first and a failure there ends the
attempt**, because the one thing this project exists to prevent is a plausible
wrong answer produced to stay inside the subset. A model that hand-rolls SHA-256
rather than importing hashlib has lost, not won, and this test says so.

``require_tier1: false`` marks a *ceiling* case — one whose task cannot be done
inside the subset (arbitrary-precision integers, set iteration order,
``os.listdir`` order). There the correct behaviour is to fall back, and the case
is scored on correctness alone. Those cases are the control that keeps the metric
honest: without them, "stay in the subset" has no counterweight.

An engine that RUNS a program and disagrees with CPython is not a model failure.
It is a MISMATCH — invariant 1, always a bug — and it is reported under its own
category so it can never be quietly counted as the model getting something wrong.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from . import engines as eng
from .sandbox import DEFAULT_TIMEOUT_S, RunResult, run_python

KINDS = ("stdout", "script", "pytest", "lypning")
EMPTY_PROGRAM = "pass\n"

# The normalizations a `stdout` test may name. Anything else is a malformed test.
NORMALIZERS = {
    "exact": lambda s: s,
    "rstrip": lambda s: "\n".join(ln.rstrip() for ln in s.rstrip("\n").split("\n")),
    "strip": lambda s: s.strip(),
}


@dataclass
class Verdict:
    passed: bool
    reason: str
    detail: str = ""
    run: Optional[RunResult] = None
    check: Optional[RunResult] = None

    @property
    def harness_error(self) -> Optional[str]:
        for r in (self.run, self.check):
            if r is not None and r.harness_error:
                return r.harness_error
        return None


@dataclass
class GateReport:
    kept: bool
    gate: str                       # the gate that decided; "kept" when all passed
    detail: str = ""
    gates: Dict[str, str] = field(default_factory=dict)


def validate_test_spec(test: Dict[str, Any]) -> Optional[str]:
    """Static check. Returns an error string, or None if the shape is usable."""
    if not isinstance(test, dict):
        return "test is not an object"
    kind = test.get("kind")
    if kind not in KINDS:
        return "unknown test kind: %r" % (kind,)
    files = test.get("files") or {}
    if not isinstance(files, dict):
        return "files must be an object"
    for rel, content in files.items():
        if not isinstance(content, (str, dict)):
            return "files[%r] must be a string or {\"base64\": ...}" % rel
        if isinstance(content, dict) and "base64" not in content:
            return "files[%r] is an object without a base64 key" % rel
    timeout = test.get("timeout_s", DEFAULT_TIMEOUT_S)
    if not isinstance(timeout, (int, float)) or not (0 < timeout <= 120):
        return "timeout_s must be in (0, 120]"
    if kind == "stdout":
        if "expect_stdout" not in test and "expect_stdout_re" not in test:
            return "stdout test needs expect_stdout or expect_stdout_re"
        if test.get("normalize", "exact") not in NORMALIZERS:
            return "unknown normalize: %r" % (test.get("normalize"),)
    elif kind == "script":
        if not (test.get("checker") or "").strip():
            return "script test needs a non-empty checker"
    elif kind == "pytest":
        if not (test.get("test_file") or "").strip():
            return "pytest test needs a non-empty test_file"
    elif kind == "lypning":
        if "expect_stdout" not in test:
            return "lypning test needs expect_stdout (CPython's answer)"
        engines = test.get("engines") or list(eng.DEFAULT_CHAIN)
        if not isinstance(engines, list) or not engines:
            return "engines must be a non-empty list"
        missing = [e for e in engines if eng.engine_path(e) is None]
        if missing:
            return "engine not built: %s (run `lypning build --rust`)" % ", ".join(missing)
    return None


@contextlib.contextmanager
def _workdir() -> Iterator[Path]:
    d = Path(tempfile.mkdtemp(prefix="ntx-case-"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(d.parent / (d.name + "-scratch"), ignore_errors=True)


def run_test(test: Dict[str, Any], program: str) -> Verdict:
    """Execute ``program`` against ``test`` and return a pass/fail verdict."""
    err = validate_test_spec(test)
    if err:
        return Verdict(False, "malformed-test", err)
    kind = test["kind"]
    if kind == "stdout":
        return _run_stdout(test, program)
    if kind == "script":
        return _run_script(test, program)
    if kind == "lypning":
        return _run_lypning(test, program)
    return _run_pytest(test, program)


def _solution_run(test: Dict[str, Any], program: str, workdir: Optional[Path],
                  scratch: Optional[Path] = None) -> RunResult:
    return run_python(
        program,
        argv=test.get("argv") or [],
        stdin=test.get("stdin"),
        files=test.get("files") or {},
        timeout_s=float(test.get("timeout_s", DEFAULT_TIMEOUT_S)),
        mem_mb=int(test.get("mem_mb", 1024)),
        keep_workdir=workdir,
        scratch_dir=scratch,
    )


def _run_stdout(test: Dict[str, Any], program: str) -> Verdict:
    r = _solution_run(test, program, None)
    if r.harness_error:
        return Verdict(False, "harness-error", r.harness_error, run=r)
    if r.timed_out:
        return Verdict(False, "timeout", r.brief(), run=r)
    want_exit = test.get("expect_exit", 0)
    if want_exit is not None and r.exit_code != want_exit:
        return Verdict(False, "exit", "want %s, got %s: %s" % (want_exit, r.exit_code, r.brief()), run=r)
    norm = NORMALIZERS[test.get("normalize", "exact")]
    got = norm(r.stdout)
    if "expect_stdout" in test:
        want = norm(test["expect_stdout"])
        if got != want:
            return Verdict(False, "stdout", "want %r, got %r" % (want[:200], got[:200]), run=r)
    if "expect_stdout_re" in test:
        if re.fullmatch(test["expect_stdout_re"], got, re.S) is None:
            return Verdict(False, "stdout-re", "no fullmatch: %r" % got[:200], run=r)
    return Verdict(True, "pass", run=r)


_CHECKER_PREAMBLE = '''\
import json, sys, os
_obs = json.loads(sys.stdin.read())
stdout = _obs["stdout"]; stderr = _obs["stderr"]
exit_code = _obs["exit_code"]; timed_out = _obs["timed_out"]
workdir = os.getcwd()
def _fail(msg=""):
    sys.stderr.write(str(msg)); sys.exit(1)
def assert_(cond, msg=""):
    if not cond: _fail(msg)
'''


def _run_script(test: Dict[str, Any], program: str) -> Verdict:
    with _workdir() as wd:
        scratch = wd.parent / (wd.name + "-scratch")
        r = _solution_run(test, program, wd, scratch=scratch)
        if r.harness_error:
            return Verdict(False, "harness-error", r.harness_error, run=r)
        obs = json.dumps({
            "stdout": r.stdout, "stderr": r.stderr,
            "exit_code": r.exit_code, "timed_out": r.timed_out,
        })
        # The checker sees the same workdir the program left behind, but is a
        # fresh process: it cannot be corrupted by the program's globals. Both
        # runs keep their script and captured streams in a sibling scratch
        # directory, so a checker asserting on what the program created sees
        # exactly that and nothing of ours.
        for name in (".ntx-stdout", ".ntx-stderr"):
            try:
                (scratch / name).unlink()
            except OSError:
                pass
        c = run_python(
            _CHECKER_PREAMBLE + test["checker"],
            stdin=obs,
            timeout_s=float(test.get("checker_timeout_s", 20)),
            entry=".ntx-checker.py",
            keep_workdir=wd,
            scratch_dir=scratch,
        )
        if c.harness_error:
            return Verdict(False, "harness-error", c.harness_error, run=r, check=c)
        if c.timed_out:
            return Verdict(False, "checker-timeout", c.brief(), run=r, check=c)
        if c.exit_code == 0:
            return Verdict(True, "pass", run=r, check=c)
        return Verdict(False, "checker", c.brief(), run=r, check=c)


_PYTEST_RUNNER = '''\
import sys
try:
    import pytest
except ImportError:
    sys.stderr.write("pytest-missing")
    sys.exit(97)
sys.exit(pytest.main(["-q", "-p", "no:cacheprovider", "test_case.py"]))
'''


def _run_pytest(test: Dict[str, Any], program: str) -> Verdict:
    with _workdir() as wd:
        files = dict(test.get("files") or {})
        files["solution.py"] = program
        files["test_case.py"] = test["test_file"]
        c = run_python(
            _PYTEST_RUNNER,
            files=files,
            timeout_s=float(test.get("timeout_s", DEFAULT_TIMEOUT_S)),
            entry=".ntx-pytest.py",
            keep_workdir=wd,
        )
        if c.harness_error:
            return Verdict(False, "harness-error", c.harness_error, check=c)
        if c.exit_code == 97:
            # Our environment is missing pytest. That is a harness problem, not a
            # failing program, and must not be scored as one.
            return Verdict(False, "harness-error", "pytest not importable in sandbox", check=c)
        if c.timed_out:
            return Verdict(False, "timeout", c.brief(), check=c)
        if c.exit_code == 0:
            return Verdict(True, "pass", check=c)
        return Verdict(False, "pytest", c.brief(), check=c)


# ---------------------------------------------------------------- the gates


def gate_case(
    test: Dict[str, Any],
    reference: Optional[str],
    failing_program: Optional[str],
    *,
    allow_no_witness: bool = False,
) -> GateReport:
    """Decide whether this case earns a place in the corpus. See module docstring."""
    gates: Dict[str, str] = {}

    err = validate_test_spec(test)
    if err:
        return GateReport(False, "malformed-test", err, gates)

    witness = reference or failing_program
    if witness is None and not allow_no_witness:
        return GateReport(False, "no-witness",
                          "no reference and no recorded failing program", gates)

    # runs -- does the test execute at all?
    probe = run_test(test, witness if witness is not None else EMPTY_PROGRAM)
    if probe.harness_error:
        return GateReport(False, "runs", probe.harness_error, gates)
    gates["runs"] = "ok"

    # satisfiable -- a reference, if we have one, must pass.
    if reference is not None:
        v = probe if witness is reference else run_test(test, reference)
        if not v.passed:
            return GateReport(False, "satisfiable",
                              "reference does not pass: %s: %s" % (v.reason, v.detail), gates)
        gates["satisfiable"] = "reference-passes"
    else:
        gates["satisfiable"] = "unproven-no-reference"

    # discriminates -- the empty program must fail, and so must the generation
    # that put this case in the corpus.
    empty = run_test(test, EMPTY_PROGRAM)
    if empty.harness_error:
        return GateReport(False, "runs", empty.harness_error, gates)
    if empty.passed:
        return GateReport(False, "discriminates", "empty program passes the test", gates)
    if failing_program is not None:
        bad = run_test(test, failing_program)
        if bad.harness_error:
            return GateReport(False, "runs", bad.harness_error, gates)
        if bad.passed:
            return GateReport(False, "discriminates",
                              "the recorded failing program passes the test", gates)
        gates["discriminates"] = "empty+recorded-failure both fail"
    else:
        gates["discriminates"] = "empty-fails"

    # stable -- same verdict twice for the witness.
    again = run_test(test, witness if witness is not None else EMPTY_PROGRAM)
    first = probe.passed if witness is not None else empty.passed
    if again.passed != first:
        return GateReport(False, "stable", "witness verdict changed between runs", gates)
    gates["stable"] = "ok"

    return GateReport(True, "kept", "", gates)


# ------------------------------------------------- the lypning kind


def _matches(test: Dict[str, Any], r: RunResult) -> Optional[str]:
    """None if the run reproduced CPython's answer, else why not."""
    if r.timed_out:
        return "timeout"
    want_exit = test.get("expect_exit", 0)
    if want_exit is not None and r.exit_code != want_exit:
        return "exit: want %s, got %s: %s" % (want_exit, r.exit_code, r.brief())
    norm = NORMALIZERS[test.get("normalize", "exact")]
    if norm(r.stdout) != norm(test["expect_stdout"]):
        return "stdout: want %r, got %r" % (
            norm(test["expect_stdout"])[:160], norm(r.stdout)[:160])
    return None


def _engine_run(test: Dict[str, Any], program: str, binary: str) -> RunResult:
    return run_python(
        program,
        argv=test.get("argv") or [],
        stdin=test.get("stdin"),
        files=test.get("files") or {},
        timeout_s=float(test.get("timeout_s", DEFAULT_TIMEOUT_S)),
        mem_mb=int(test.get("mem_mb", 1024)),
        interpreter=[binary],
    )


def _run_lypning(test: Dict[str, Any], program: str) -> Verdict:
    # 1. Correctness, on CPython, first and decisively.
    ref = _solution_run(test, program, None)
    if ref.harness_error:
        return Verdict(False, "harness-error", ref.harness_error, run=ref)
    why = _matches(test, ref)
    if why:
        kind = why.split(":", 1)[0]
        return Verdict(False, "timeout" if kind == "timeout" else kind, why, run=ref)

    if not test.get("require_tier1", True):
        # A ceiling case: falling back IS the right answer. Correct is enough.
        return Verdict(True, "pass", "ceiling case: correct, route not required", run=ref)

    # 2. Routing, only now that the answer is known to be right.
    last = ""
    for name in (test.get("engines") or list(eng.DEFAULT_CHAIN)):
        binary = eng.engine_path(name)
        if binary is None:
            return Verdict(False, "harness-error", "engine not built: %s" % name, run=ref)
        r = _engine_run(test, program, binary)
        if r.harness_error:
            return Verdict(False, "harness-error", r.harness_error, run=ref, check=r)
        if r.exit_code == eng.REFUSAL_EXIT:
            last = (r.stderr or "").strip().splitlines()[-1] if r.stderr.strip() else "exit 90"
            continue
        bad = _matches(test, r)
        if bad:
            # The engine ran it and disagreed with CPython. Invariant 1: always a
            # bug, and never the model's.
            return Verdict(False, "engine-mismatch",
                           "%s disagrees with CPython: %s" % (name, bad), run=ref, check=r)
        return Verdict(True, "pass", "tier-1 on %s" % name, run=ref, check=r)
    return Verdict(False, "refused", last or "every engine refused", run=ref)
