"""`sys.version` on lypning-l: the reference's own text, or a refusal.

The text differs on every CPython BUILD (date and compiler, even at one micro),
so lypning-l serves it only from a bake (`sys_probe.py`, run by `build.rs` on
the probed reference) and only while the CPython a refusal would fall through
to is that very file: same realpath, length and mtime, and the same for its
shared libpython (`randobj::fallback_is_reference`). Everything else refuses,
and that CPython prints its own version one spawn later.

`conformance` cannot grade any of this — `_INTERPRETER_SPECIFIC` scores a
`sys.version` program on its exit code only — so this grid is the gate. A
served row is compared with the interpreter `engines.find_cpython()` names; a
refused row must leave by the contract (exit 90, one line, empty stdout); and
both chains — `lypning run` in Python and the core binary's own `run` — must
print the FALLBACK's text, including a fallback that is not the reference.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lypning import engines, paths

CORE = engines.find(engines.LYPNING)
LARGER = engines.find(engines.LYPNING_L)
needs_l = pytest.mark.skipif(LARGER is None, reason="lypning-l is not built: a hole, not a pass")
needs_both = pytest.mark.skipif(CORE is None or LARGER is None,
                                reason="needs the core and lypning-l (lypning build --rust)")

PRINT = "import sys\nprint(sys.version)"

#: Served where the fallback is the reference: stdout equal to that CPython's.
#: The first four are corpus programs (py-bb8af111f97f, py-d292d4cb0a66,
#: py-bc63e423664e, py-186936681076's head).
SERVED = [
    PRINT,
    "import sys; print(sys.version.split()[0])",
    "import sys;print(sys.version)",
    "import re, sys\nv = sys.version.split()[0]\nprint(re.sub(r'\\.', '-', v), re.findall(r'\\d+', v))",
    "import sys\nprint(repr(sys.version))",
    "import sys\ns = sys.version\nprint(s.split()[0], len(s), s.startswith('3.'))",
    "from sys import version\nprint(version)",
    "import sys as s\nprint(s.version[:4], s.version_info[:2])",
    "import sys\nfor _ in range(3):\n    print(len(sys.version))",
]


def _env(**changes: str | None) -> dict:
    env = dict(os.environ)
    env.pop("LYPNING_CPYTHON", None)
    for k, v in changes.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return env


def _run(argv: list, program: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """One program in a temp cwd of its own — invariant 4."""
    with tempfile.TemporaryDirectory() as d:
        return subprocess.run([str(a) for a in argv] + ["-c", program], capture_output=True,
                               text=True, cwd=d, timeout=120, env=env or _env())


def _refused(got: subprocess.CompletedProcess, engine: str = engines.LYPNING_L,
             kind: str = "module-attr") -> None:
    assert (got.returncode, got.stdout) == (engines.UNSUPPORTED_EXIT, ""), (
        got.returncode, got.stdout, got.stderr)
    line = got.stderr.strip()
    assert "\n" not in line and line.startswith("%s: unsupported: %s: " % (engine, kind)), line


def _fallback() -> Path:
    exe = engines.find_cpython()
    assert exe is not None
    return exe


def _served() -> bool:
    """Does this build serve `sys.version` against this host's fallback?"""
    return _run([LARGER], PRINT).returncode == 0


def _other_minor() -> Path | None:
    """A CPython of another minor than the fallback, to be the wrong one."""
    ref = _run([_fallback()], "import sys;print(sys.version_info[:2])").stdout
    for name in ("python3.9", "python3.10", "python3.11", "python3.12", "python3.13", "python3.14"):
        p = shutil.which(name)
        if p and _run([p], "import sys;print(sys.version_info[:2])").stdout not in ("", ref):
            return Path(p)
    return None


def _script(d: Path, name: str, body: str) -> Path:
    p = d / name
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


# --- served: the reference's text, byte for byte --------------------------------


@needs_l
@pytest.mark.parametrize("program", SERVED, ids=range(len(SERVED)))
def test_a_served_row_prints_the_fallbacks_own_text(program: str) -> None:
    if not _served():
        pytest.skip("this lypning-l was not built against this host's fallback CPython")
    got, ref = _run([LARGER], program), _run([_fallback()], program)
    assert (got.returncode, got.stdout) == (ref.returncode, ref.stdout) == (0, ref.stdout), got.stderr


@needs_l
def test_a_capture_shim_first_on_path_is_looked_through() -> None:
    """The shim execs the next non-shim `python3`, and so this resolves it."""
    shim = paths.ASSETS / "shim" / "python-shim"
    with tempfile.TemporaryDirectory() as d:
        _script(Path(d), "python3", shim.read_text())
        env = _env(PATH=d + os.pathsep + os.environ.get("PATH", ""))
        got, plain = _run([LARGER], PRINT, env=env), _run([LARGER], PRINT)
    assert (got.returncode, got.stdout) == (plain.returncode, plain.stdout), got.stderr


# --- refused: any doubt about which CPython answers ----------------------------


@needs_l
def test_another_interpreter_file_refuses() -> None:
    """A byte-identical copy is a different realpath and mtime: not the reference."""
    with tempfile.TemporaryDirectory() as d:
        copy = Path(d) / "python3"
        shutil.copy2(_fallback(), copy)
        os.utime(copy, None)
        _refused(_run([LARGER], PRINT, env=_env(LYPNING_CPYTHON=str(copy))))


@needs_l
def test_another_minor_refuses() -> None:
    other = _other_minor()
    if other is None:
        pytest.skip("no CPython of another minor on PATH")
    _refused(_run([LARGER], PRINT, env=_env(LYPNING_CPYTHON=str(other))))


@needs_l
@pytest.mark.parametrize("pin", ["python3", "~/python3"])
def test_a_pin_the_two_dispatchers_would_resolve_differently_refuses(pin: str) -> None:
    """`main.rs` execvp's a bare name; `engines.find_cpython` takes it as a path."""
    _refused(_run([LARGER], PRINT, env=_env(LYPNING_CPYTHON=pin)))


@needs_l
def test_a_script_first_on_path_that_is_not_the_shim_refuses() -> None:
    """A pyenv-style `#!` stub could run anything."""
    with tempfile.TemporaryDirectory() as d:
        _script(Path(d), "python3", "#!/bin/sh\nexec %s \"$@\"\n" % _fallback())
        _refused(_run([LARGER], PRINT, env=_env(PATH=d + os.pathsep + os.environ.get("PATH", ""))))


@needs_l
def test_no_python3_on_path_refuses() -> None:
    with tempfile.TemporaryDirectory() as d:
        _refused(_run([LARGER], PRINT, env=_env(PATH=d)))


@needs_l
def test_a_held_run_refuses_an_uncaught_error_after_it() -> None:
    """Evaluated, `sys.version` holds the run: the core refused it right there."""
    got = _run([LARGER], PRINT + "\nundefined_name")
    assert (got.returncode, got.stdout) == (engines.UNSUPPORTED_EXIT, ""), got
    if _served():
        assert ": unsupported: name-hint: " in got.stderr or engines.reference_minor(LARGER) == "3.9"


# --- the core, the router and both chains ---------------------------------------


@needs_both
def test_the_core_refuses_and_routes_to_lypning_l() -> None:
    _refused(_run([CORE], PRINT), engine=engines.LYPNING)
    route = engines.route(PRINT, binary=CORE)
    assert (route.engine, route.kind) == (engines.LYPNING_L, "module-attr"), route


@needs_both
@pytest.mark.parametrize("fallback", ["default", "other"])
def test_both_chains_print_the_fallbacks_text(fallback: str) -> None:
    if fallback == "default":
        exe, env = _fallback(), _env()
    else:
        exe = _other_minor()
        if exe is None:
            pytest.skip("no CPython of another minor on PATH")
        env = _env(LYPNING_CPYTHON=str(exe))
    want = _run([exe], PRINT).stdout
    assert want.startswith("3.")
    rust = _run([CORE, "run"], PRINT, env=env)
    assert (rust.returncode, rust.stdout) == (0, want), rust.stderr
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    py = _run([sys.executable, "-m", "lypning", "run"], PRINT, env=env)
    assert (py.returncode, py.stdout) == (0, want), py.stderr


# --- the bake ----------------------------------------------------------------


def test_the_probe_is_separate_and_prints_three_lines() -> None:
    """`reference_probe.py`'s five lines are the core's flags; this is not them."""
    out = subprocess.run([sys.executable, str(paths.RUST_DIR / "sys_probe.py")],
                         capture_output=True, text=True, timeout=60)
    lines = out.stdout.split("\n")[:-1]
    assert out.returncode == 0 and len(lines) == 3, out
    assert bytes.fromhex(lines[0]).decode() == sys.version
    assert lines[1] == os.path.realpath(sys.executable)
    assert lines[2] == "" or lines[2] == "-" or os.path.isfile(lines[2])
