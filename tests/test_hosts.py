"""Every host's quickstart, driven through the same five probes, byte for byte.

What only this test can see: the bindings over one C ABI drifting apart on the
refusal path. Each host has its own contract test, and each of those proves the
host against the header. None of them proves the hosts against *each other*,
and the place they disagree is never the happy path -- ``print(sum(range(10)))``
is 45 in all of them on the day they are written. It is the refusal: one host
retries a traceback through CPython, so a program that failed once fails twice;
another treats the refusal as an error and exits 1 with a message where the
others silently produced CPython's answer; a third forwards ``sys.exit(3)`` as
1. Every one of those compiles, links and passes its own test. So this file
runs the one contract every quickstart claims -- ``quickstart "<source>"
[args...]``: in-process, or ``python3 -c`` exactly once, never both -- and
compares the bytes, per host, per probe.

The set of hosts is the table in ``docs/EMBEDDING.md`` section 4;
:file:`tests/test_docs.py` holds the two in step. Each row here says what the
host needs on ``PATH``, how it is built once per session, and the argv that
runs it. A missing toolchain skips that host and says which tool; a missing
library skips the module, because the premise is one library under every host
(the two hosts that do not load it at run time -- Rust, which is the crate,
and Node, which links it statically -- are gated the same way on purpose: a
sixth of the table running while the rest is "not built" would read as a
result about the ABI). A build that fails with the library present is a
failure, printed in full, because there is nothing left for it to be but a
broken host.

Where each host is built follows where it already builds: cargo target dirs
and SwiftPM's ``.build`` stay in the tree, and the C, C++ and Go binaries go to
a scratch directory so the tree gains nothing the build did not already leave.
Every probe runs from a fresh temporary cwd with stdin closed (CLAUDE.md
invariant 4: a quickstart runs whatever it is handed, and one of the five
probes hands it to CPython with no restriction at all).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pytest

from conftest import _INSTALLED_LIBRARY

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "lypning" / "assets"

#: Captured at import, before the autouse fixture moves ``$HOME`` and
#: ``$LYPNING_HOME``. The builds run under this environment, not the test's:
#: cargo, go and swift keep their caches under the real home, and a build that
#: started from a fresh ``$HOME`` every session would rebuild the runtime crate
#: three times over from nothing. The probes themselves run under the moved
#: environment, which is the point of the fixture.
_BUILD_ENV: Dict[str, str] = dict(os.environ)

#: How long one build, or one probe, may take. The Rust example compiles the
#: runtime crate from source the first time; nothing else comes close.
BUILD_TIMEOUT = 900
PROBE_TIMEOUT = 120

pytestmark = pytest.mark.skipif(
    _INSTALLED_LIBRARY is None,
    reason="the C ABI is not built (`lypning build --lib`); every host here sits over it",
)


# --- where the library is, in both shapes of the tree --------------------------

def _libdir() -> Path:
    return Path(_INSTALLED_LIBRARY).parent


def _incdir() -> Path:
    """The header beside the library if the library is an installed tree
    (``$LYPNING_HOME/lib`` has ``$LYPNING_HOME/include``), else the checkout's."""
    installed = _libdir().parent / "include"
    if (installed / "lypning.h").is_file():
        return installed
    return ASSETS / "include"


def _home_of_library() -> str:
    """The ``$LYPNING_HOME`` whose ``lib/`` holds the library the suite found.

    The C, C++ and Swift builds look in ``<checkout>/rust/target/release-lib``
    first and ``$LYPNING_HOME/lib`` second, and in a checkout that has both they
    would link the first while ``lypning.embed`` loaded the second. C and C++
    take an explicit ``LIBDIR`` below and are exact; Swift's manifest only
    reads the environment, so it is told this and left to its own rule.
    """
    return str(_libdir().parent)


# --- the hosts -------------------------------------------------------------------

Command = Tuple[List[str], Optional[Path], Dict[str, str]]  # argv, cwd, extra env


class Host:
    """One row of the table: what it needs, how it is built, how it runs.

    ``build`` returns the commands to run once, in order, each with its cwd and
    any environment the build needs on top of the developer's. ``prefix`` is
    the argv that runs the quickstart; the probe's source and args go after it.
    ``env`` is what the quickstart needs at run time to find the library, in
    the same terms its own README uses (``LYPNING_LIB`` for the two hosts that
    discover it by name; nothing for the ones that were linked with an rpath).
    """

    def __init__(self, name: str, needs: Tuple[str, ...],
                 build: Callable[[Path], List[Command]],
                 prefix: Callable[[Path], List[str]],
                 env: Optional[Callable[[], Dict[str, str]]] = None) -> None:
        self.name = name
        self.needs = needs
        self.build = build
        self.prefix = prefix
        self.env = env or (lambda: {})

    def __repr__(self) -> str:
        return self.name


def _copy_example(scratch: Path, lang: str) -> Path:
    """The wheel shape of the C and C++ examples: copied somewhere writable and
    built there unchanged, with the library and header named explicitly."""
    src = ASSETS / "examples" / lang
    dst = scratch / lang
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("Makefile", "quickstart.c", "quickstart.cpp"):
        if (src / name).is_file():
            shutil.copy2(src / name, dst / name)
    return dst


def _make_quickstart(lang: str) -> Callable[[Path], List[Command]]:
    def build(scratch: Path) -> List[Command]:
        d = _copy_example(scratch, lang)
        return [(["make", "-C", str(d), "quickstart",
                  "LIBDIR=%s" % _libdir(), "INCDIR=%s" % _incdir()], None, {})]
    return build


RUST_MANIFEST = ASSETS / "examples" / "rust" / "Cargo.toml"
NODE_MANIFEST = ASSETS / "node" / "Cargo.toml"
SWIFT_PACKAGE = ASSETS / "swift"

HOSTS: Tuple[Host, ...] = (
    Host("c", ("make", "cc"),
         _make_quickstart("c"),
         lambda s: [str(s / "c" / "quickstart")]),
    Host("cpp", ("make", "c++"),
         _make_quickstart("cpp"),
         lambda s: [str(s / "cpp" / "quickstart")]),
    Host("rust", ("cargo",),
         lambda s: [(["cargo", "build", "--release", "--quiet",
                      "--manifest-path", str(RUST_MANIFEST), "--example", "quickstart"],
                     None, {})],
         lambda s: [str(RUST_MANIFEST.parent / "target" / "release" / "examples" / "quickstart")]),
    Host("node", ("node", "cargo"),
         lambda s: [(["cargo", "build", "--release", "--quiet",
                      "--manifest-path", str(NODE_MANIFEST)], None, {})],
         lambda s: ["node", str(ASSETS / "node" / "quickstart.js")]),
    Host("python", (),
         lambda s: [],
         lambda s: [sys.executable, str(ASSETS / "examples" / "python" / "quickstart.py")],
         lambda: {"LYPNING_LIB": str(_INSTALLED_LIBRARY),
                  "PYTHONPATH": os.pathsep.join(
                      [str(ROOT / "src")] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p])}),
    # `go run` would build into GOCACHE and run from there; `go build -o` gives
    # a binary the probes can run from any cwd. CGO_LDFLAGS is the README's
    # own mechanism for a library that is not in the checkout's target dir,
    # appended after the #cgo directives, so it is right in both shapes.
    Host("go", ("go", "cc"),
         lambda s: [(["go", "build", "-o", str(s / "go" / "quickstart"), "./quickstart"],
                     ASSETS / "go",
                     {"CGO_LDFLAGS": "-L%s -Wl,-rpath,%s" % (_libdir(), _libdir())})],
         lambda s: [str(s / "go" / "quickstart")]),
    Host("swift", ("swift",),
         lambda s: [(["swift", "build", "-c", "release", "--product", "quickstart",
                      "--package-path", str(SWIFT_PACKAGE)],
                     None, {"LYPNING_HOME": _home_of_library()})],
         lambda s: [str(SWIFT_PACKAGE / ".build" / "release" / "quickstart")]),
    Host("lua", ("luajit",),
         lambda s: [],
         lambda s: ["luajit", str(ASSETS / "lua" / "quickstart.lua")],
         lambda: {"LYPNING_LIB": str(_INSTALLED_LIBRARY)}),
)


# --- the probes ----------------------------------------------------------------

class Probe:
    """One property of the contract, as argv after the quickstart and what it
    must produce. ``stderr_has`` is a byte string that must appear exactly once
    -- once, because appearing twice is the drift this file exists to catch."""

    def __init__(self, name: str, argv: List[str], stdout: bytes, code: int,
                 stderr_has: Optional[bytes] = None) -> None:
        self.name = name
        self.argv = argv
        self.stdout = stdout
        self.code = code
        self.stderr_has = stderr_has

    def __repr__(self) -> str:
        return self.name


PROBES: Tuple[Probe, ...] = (
    # Runs in-process; the number is lypning's own.
    Probe("sum", ["print(sum(range(10)))"], b"45\n", 0),
    # A refusal: lypning wrote nothing, CPython answers once, and nothing on
    # stderr says "unsupported" -- a refusal is not an error at this layer.
    Probe("refusal", ["import subprocess; print(1)"], b"1\n", 0),
    # The args after the source are sys.argv[1:], in-process.
    Probe("argv", ["import sys; print(sys.argv[1:])", "a", "b"], b"['a', 'b']\n", 0),
    # The program's own failure: exit 1, the traceback on stderr, and NOT
    # retried through CPython -- a host that retries prints it twice.
    Probe("traceback", ["print(1/0)"], b"", 1, b"ZeroDivisionError"),
    # The program's own exit code, passed through unchanged.
    Probe("exit", ["import sys; sys.exit(3)"], b"", 3),
)


# --- building once per session ---------------------------------------------------

class _Builds:
    """Lazy, cached per host: the argv prefix, or the reason it cannot run."""

    def __init__(self, scratch: Path) -> None:
        self.scratch = scratch
        self.ready: Dict[str, List[str]] = {}
        self.skipped: Dict[str, str] = {}
        self.failed: Dict[str, str] = {}

    def prefix(self, host: Host) -> List[str]:
        if host.name not in self.ready and host.name not in self.skipped \
                and host.name not in self.failed:
            self._build(host)
        if host.name in self.skipped:
            pytest.skip(self.skipped[host.name])
        if host.name in self.failed:
            pytest.fail(self.failed[host.name], pytrace=False)
        return self.ready[host.name]

    def _build(self, host: Host) -> None:
        # python3 is what every quickstart falls onward to, so it is a need of
        # every row, whether or not the row says so.
        for tool in host.needs + ("python3",):
            if shutil.which(tool, path=_BUILD_ENV.get("PATH")) is None:
                self.skipped[host.name] = "%s: `%s` is not installed" % (host.name, tool)
                return
        for argv, cwd, extra in host.build(self.scratch):
            env = dict(_BUILD_ENV)
            env.update(extra)
            try:
                r = subprocess.run(argv, cwd=str(cwd) if cwd else None, env=env,
                                   stdin=subprocess.DEVNULL, capture_output=True,
                                   timeout=BUILD_TIMEOUT)
            except (OSError, subprocess.SubprocessError) as exc:
                self.failed[host.name] = "%s: %s did not run: %s" % (host.name, argv[0], exc)
                return
            if r.returncode != 0:
                self.failed[host.name] = (
                    "%s: build failed (exit %d) with the library at %s:\n$ %s\n%s%s"
                    % (host.name, r.returncode, _INSTALLED_LIBRARY, " ".join(argv),
                       r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")))
                return
        prefix = host.prefix(self.scratch)
        # A prefix that names a file names one the build was supposed to leave;
        # "not found" from the probe would blame the probe.
        if os.sep in prefix[0] and not Path(prefix[0]).is_file():
            self.failed[host.name] = "%s: the build reported ok but left no %s" % (host.name, prefix[0])
            return
        self.ready[host.name] = prefix


@pytest.fixture(scope="module")
def builds(tmp_path_factory) -> _Builds:
    return _Builds(tmp_path_factory.mktemp("hosts"))


# --- the test ----------------------------------------------------------------------

CASES = [(h, p) for h in HOSTS for p in PROBES]


@pytest.mark.parametrize("host,probe", CASES, ids=["%s-%s" % (h, p) for h, p in CASES])
def test_quickstart(host: Host, probe: Probe, builds: _Builds, lypning_lib, tmp_path) -> None:
    prefix = builds.prefix(host)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    env = dict(os.environ)
    env.update(host.env())
    r = subprocess.run(prefix + probe.argv, cwd=str(cwd), env=env,
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=PROBE_TIMEOUT)
    shown = "%s %s\nstdout: %r\nstderr: %r" % (
        host.name, probe.name, r.stdout, r.stderr.decode("utf-8", "replace"))
    assert r.stdout == probe.stdout, shown
    assert r.returncode == probe.code, shown
    if probe.stderr_has is None:
        assert r.stderr == b"", shown
    else:
        assert r.stderr.count(probe.stderr_has) == 1, (
            "%s\n%s must appear exactly once: 0 means the failure was swallowed, "
            "2 means the host retried a program that had already run" % (shown, probe.stderr_has))
    assert b"unsupported" not in r.stderr, "a refusal leaked to stderr:\n" + shown
