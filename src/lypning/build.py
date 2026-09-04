"""Building the two engines, and pinning what a build must not break.

The invariant this module exists to hold: **a build that produces a binary has
not necessarily produced a working tier.** The refusal contract — exit ``90``,
one line on stderr, nothing at all on stdout — is what makes the three engines
interchangeable, and it is the one thing about lypning that has only ever broken
*silently*. A parser change that turns a refusal into a traceback still compiles,
still links, still passes ``--version``. So it is asserted here, on the binary
that was just built, before ``ok`` is allowed to be true; this mirrors the assert
at the end of ``assets/scripts/build-rust.sh`` rather than trusting that whoever
built the crate went through that script.

The second thing this module holds is the asset/state split from :mod:`paths`.
In a wheel install the crate source is read-only, which is the path a ``pip``
user actually hits and the path nobody tests, so the crate is copied into
``paths.build_dir()`` and the copy is built. In a checkout the two are the same
directory and no copy happens, which is what keeps ``cargo build`` by hand and
``lypning build`` sharing one object cache.

Two things here are not tiers and are shaped by that. The benchmark CONTROL
(:func:`build_stock`) is a binary this package builds and deliberately never
installs, because the engine bin dir is what the finders read and a control that
can be found is a control that can be run. And :func:`verify` is the build's own
``--verify``: gate the shape, run the whole battery, both pointed at the binary
that was just produced rather than at whatever is already installed.

Nothing here prints. :func:`report` renders a table and returns it; the two
reports :func:`verify` collects are rendered by the modules that own them.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from . import UNSUPPORTED_EXIT
from . import embed
from . import engines
from . import paths

# 131,072 B is CheerpX's device block. Cold cost in the sandbox is a step
# function in blocks — a byte over a boundary costs a whole block's fetch — so
# the block count is the number worth reporting, not the byte count
# (docs/SANDBOX-PERFORMANCE.md).
CHEERPX_BLOCK = 131072

# The pinned contract. `import subprocess` is the canonical refusal because it is
# unambiguous: no subset will ever grow it, so the expected output can be an
# exact string rather than a pattern.
REFUSAL_PROGRAM = "import subprocess"
REFUSAL_LINE = engines.refusal_line(engines.LYPNING, "module", "import subprocess")

MUSL_X86_64 = "x86_64-unknown-linux-musl"
MUSL_I686 = "i686-unknown-linux-musl"

# "" means the host default target: plain `cargo build --release`, dynamically
# linked against the host libc. It is the control, not the shipping target —
# the dynamic loader's five file opens cost 5.5x on startup (build-rust.sh).
_TARGETS = {
    "host": "",
    "native": "",
    "glibc": "",
    "musl": MUSL_X86_64,
    "x86_64": MUSL_X86_64,
    "x86-64": MUSL_X86_64,
    "amd64": MUSL_X86_64,
    MUSL_X86_64: MUSL_X86_64,
    "i686": MUSL_I686,
    "i386": MUSL_I686,
    "x86": MUSL_I686,
    MUSL_I686: MUSL_I686,
}

_TOOLS = ("cargo", "rustc", "cc", "make", "git", "strace")

_LOG_TAIL = 40
_LOG_TAIL_VERBOSE = 400

_CARGO_TIMEOUT = 1800.0
_MICROPYTHON_TIMEOUT = 5400.0

#: The host the MicroPython tier's two pinned downloads come from, and the one
#: the preflight asks about before starting a build that would need it. Named
#: rather than spelled inline because it appears in the reason line too, and two
#: spellings would drift into two different answers.
_PINNED_HOST = "musl.libc.org"

#: Exit codes that mean the transport failed, not the build. ``build-
#: micropython.sh`` runs ``curl``/``wget`` unguarded under ``set -e``, so the
#: fetcher's own exit code becomes the script's — which is the most precise
#: signal available about whose fault a failure was, and far better than asking
#: the network a second time.
#:
#: curl: 5/6 resolve, 7 connect, 18 partial transfer, 28 timeout, 35 TLS
#: connect, 52 empty reply, 55/56 send/recv, 92 HTTP/2 stream.
#: wget: 4 network failure, 8 server error.
#:
#: curl's 22 — an HTTP error status under ``-f`` — is deliberately NOT here. It
#: covers a transient 503 and a permanent 404 alike, and a 404 means the pinned
#: URL has rotted, which is a real break that needs a human and must redden.
#: Reddening on somebody's 503 is the cheaper of the two mistakes.
_FETCH_NETWORK_EXITS = frozenset((4, 5, 6, 7, 8, 18, 28, 35, 52, 55, 56, 92))

#: What a transport failure says when it is the shell script's ``die`` that
#: reports it, so the exit code is 1 and only the message names the cause.
#: Matched against the tail of the build log, case-insensitively.
_NETWORK_PHRASES = (
    "could not resolve", "couldn't resolve", "failed to connect", "connection reset",
    "connection refused", "recv failure", "send failure", "ssl connect error",
    "tls connect error", "empty reply from server", "operation timed out",
    "timed out", "network is unreachable", "temporary failure in name resolution",
    "could not clone", "unable to access", "transfer closed", "remote end hung up",
)


def _transport_failed(rc: int, out: str) -> bool:
    """Did the fetch fail, rather than the build?

    Two signals, because neither alone is enough. The exit code is exact when
    ``curl`` or ``wget`` died on its own; when the script caught the failure and
    called ``die`` instead, the code is a flat 1 and only the message knows.

    A network probe is deliberately NOT one of the signals. The failure this
    exists to classify — ``curl: (35) Recv failure: Connection reset by peer``
    — happens while a TCP connect to the same host on the same port still
    succeeds, so a re-probe answers "reachable" and calls an outage a
    regression. Ask what actually broke, not whether the host answers now.
    """
    if rc in _FETCH_NETWORK_EXITS:
        return True
    low = (out or "").lower()
    return any(p in low for p in _NETWORK_PHRASES)


# --- records -----------------------------------------------------------------


@dataclass
class BuildResult:
    """One engine's build. ``ok`` means built *and* the contract still holds.

    ``skipped_reason`` is not restricted to skips: it carries the one-line "why"
    for anything a caller would otherwise have to read ``log`` to discover — a
    missing toolchain, a fallback to the host target, a broken contract.

    ``unavailable`` splits that "why" into the two kinds a caller must be able
    to act on differently, because until it existed they rendered identically
    and a gate could not tell them apart:

    * **unavailable** — a precondition this machine does not meet. No 32-bit
      toolchain, no cargo, no network to fetch a pinned tarball that is not yet
      cached. Nothing was attempted, so nothing can be broken; the correct
      response is to install something or to carry on without the tier.
    * **failed** (``unavailable`` false, ``ok`` false) — every precondition
      held, the build ran, and it did not produce a binary. That is a
      regression and the only one of the two worth reddening a gate.

    The distinction is the whole reason a CI job can say what its red means.
    A build that dies mid-download is classified by what the fetcher said (see
    :func:`_transport_failed`) and reported as *unavailable* rather than failed:
    an upstream outage is an outage, whichever side of the preflight it lands on.
    """

    engine: str
    #: What was built FOR that engine. ``""`` is the engine binary itself;
    #: ``"lib"`` is the C ABI library, which is an artefact of the same engine
    #: rather than a fourth one — the engine strings are exactly three, and
    #: inventing a fourth here would put it in every table that reads them.
    artifact: str = ""
    ok: bool = False
    binary: Path | None = None
    size_bytes: int = 0
    seconds: float = 0.0
    target: str = ""
    log: str = ""
    skipped_reason: str = ""
    # A dry run is neither built nor broken, and reporting it as FAILED is how a
    # first `lypning build --all --dry-run` reads as a broken install.
    dry_run: bool = False
    #: A precondition this machine does not meet, rather than a build that
    #: broke. See the class docstring: this is the field a gate branches on.
    unavailable: bool = False

    @property
    def label(self) -> str:
        return "%s %s" % (self.engine, self.artifact) if self.artifact else self.engine

    @property
    def cheerpx_blocks(self) -> int:
        return cheerpx_blocks(self.size_bytes)


def cheerpx_blocks(size_bytes: int) -> int:
    """``ceil(size / 131072)`` — what a static binary actually costs cold."""
    if size_bytes <= 0:
        return 0
    return (size_bytes + CHEERPX_BLOCK - 1) // CHEERPX_BLOCK


def toolchain() -> dict[str, str | None]:
    """Resolved paths for the tools a build needs, ``None`` for each absent one.

    ``strace`` is in the list because the static-startup check needs it; its
    absence is not an error, it just costs the file-open count.
    """
    return {name: shutil.which(name) for name in _TOOLS}


# --- helpers -----------------------------------------------------------------


def _tail(text: str, verbose: bool = False) -> str:
    limit = _LOG_TAIL_VERBOSE if verbose else _LOG_TAIL
    lines = (text or "").splitlines()
    return "\n".join(lines[-limit:])


def _join(*parts: str) -> str:
    return "\n".join(p for p in parts if p)


def _why(out: str) -> str:
    """The last thing the build actually said, for the one-line status.

    An exit code alone is not a fix: `build-micropython.sh failed (exit 35)` is
    a curl TLS error that reads as a mystery, while the line above it names the
    URL it could not reach. The full log stays behind ``-v``.
    """
    lines = [l.strip() for l in (out or "").splitlines() if l.strip()]
    if not lines:
        return ""
    # ANSI from the shell script's own headings would otherwise land mid-table.
    last = re.sub(r"\x1b\[[0-9;]*m", "", lines[-1])
    return " — %s%s" % (last[:160], "" if len(last) <= 160 else "…")


def _size(p: Path | None) -> int:
    try:
        return p.stat().st_size if p else 0
    except OSError:
        return 0


def _run(cmd: Sequence[str], *, cwd: Path | None = None,
         env: dict[str, str] | None = None,
         timeout: float | None = 120.0) -> tuple[int, str]:
    """Run, never raise. Returns ``(rc, stdout+stderr)``; rc 127/124 on failure."""
    full = dict(os.environ)
    full["LYPNING_CAPTURE"] = "0"  # a build must not log itself into the corpus
    full["CARGO_TERM_COLOR"] = "never"
    if env:
        full.update(env)
    try:
        proc = subprocess.run(
            list(cmd), capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(cwd) if cwd else None, env=full, timeout=timeout, check=False,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timed out after %ss: %s" % (timeout, " ".join(cmd))
    except OSError as e:
        return 127, "cannot exec %s: %s" % (cmd[0] if cmd else "", e)


def _sync_tree(src: Path, dst: Path, skip: Iterable[str] = ()) -> int:
    """Copy ``src`` into ``dst``, touching only what differs.

    Not ``copytree(dirs_exist_ok=True)``: that rewrites every file on every call
    and the fresh mtimes make cargo rebuild the world each time. Comparing size
    and mtime keeps an incremental rebuild incremental in the wheel case too.
    """
    skipset = set(skip)
    copied = 0
    src = Path(src)
    dst = Path(dst)
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in skipset]
        rel = Path(root).relative_to(src)
        target_dir = dst / rel if str(rel) != "." else dst
        paths.ensure_dir(target_dir)
        for name in files:
            if name in skipset:
                continue
            s = Path(root) / name
            d = target_dir / name
            try:
                ss = s.stat()
                if d.exists():
                    ds = d.stat()
                    if ds.st_size == ss.st_size and int(ds.st_mtime) >= int(ss.st_mtime):
                        continue
                shutil.copy2(s, d)
                copied += 1
            except OSError:
                continue
    return copied


def _can_reach(host: str, port: int = 443, timeout: float = 5.0) -> bool:
    """Cheap reachability probe, so a missing network is a reason, not a stack."""
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


# --- the pinned contract -----------------------------------------------------


def variant_feature(variant: str) -> str:
    """The one cargo feature that names ``variant``: ``variant-m`` for the
    unsuffixed core, ``variant-<letter>`` for a suffixed one (invariant 9)."""
    if variant == engines.LYPNING:
        return "variant-m"
    if variant in engines.SPECTRUM and variant.startswith(engines.LYPNING + "-"):
        return "variant-" + variant[len(engines.LYPNING) + 1:]
    raise ValueError("not a Rust variant: %r" % (variant,))


def check_refusal_contract(binary: Path | str, expected: str = engines.LYPNING) -> tuple[bool, str]:
    """``(ok, why)`` for the three things a refusal must do, all at once.

    Exit ``90``; the exact line on stderr — headed by ``expected``'s own name,
    which is the half a spectrum adds: a variant writing a sibling's name would
    misroute the dispatcher silently; **nothing** on stdout. The last one is the
    one that hurts: a refusal written to stdout still exits 90 and still looks
    right in a terminal, while it silently poisons every ``… | wc -l`` the
    caller had around it.
    """
    want = engines.refusal_line(expected, "module", REFUSAL_PROGRAM)
    res = engines.run(engines.LYPNING, REFUSAL_PROGRAM, binary=Path(binary), timeout=60.0)
    if res.returncode != UNSUPPORTED_EXIT:
        return False, "exit %d, expected %d (%s)" % (
            res.returncode, UNSUPPORTED_EXIT, res.stderr.strip()[:160] or "no stderr")
    if res.stdout != "":
        return False, "the refusal line reached stdout: %r" % res.stdout[:120]
    if res.stderr.strip() != want:
        return False, "stderr was %r, expected %r" % (res.stderr.strip()[:160], want)
    return True, ""


def check_spectrum_contract(binary: Path | str,
                            expected: str = engines.LYPNING) -> tuple[bool, str]:
    """``(ok, why)``: the binary knows which variant it is, and agrees with us.

    ``route --spectrum`` must name ``expected`` as ``self``, list it in the table
    it carries, and that table's names must be exactly ``engines.SPECTRUM`` —
    the Python copy pinned to the compiled table at the moment a binary is
    produced, which is the earliest a drift could exist. ``--version`` must say
    the same name. A variant that mis-names itself writes a sibling's name at
    the head of its refusal line and the dispatcher misroutes silently; this is
    the assertion that makes that loud.
    """
    b = str(binary)
    rc, out = _run([b, "route", "--spectrum"], timeout=60)
    if rc != 0:
        return False, "route --spectrum exited %d: %s" % (rc, out.strip()[:160])
    try:
        table = json.loads(out.strip().splitlines()[-1] if out.strip() else "")
    except (ValueError, IndexError):
        return False, "route --spectrum was not JSON: %r" % out.strip()[:160]
    names = [row.get("name") for row in table.get("spectrum", [])]
    if table.get("self") != expected:
        return False, "the binary calls itself %r, expected %r" % (table.get("self"), expected)
    if expected not in names:
        return False, "%r is not a row of the table it carries: %r" % (expected, names)
    if names != list(engines.SPECTRUM):
        return False, "the compiled spectrum %r is not engines.SPECTRUM %r" % (names, list(engines.SPECTRUM))
    rc, ver = _run([b, "--version"], timeout=60)
    if rc != 0 or "(%s)" % expected not in ver:
        return False, "--version says %r, expected it to name (%s)" % (ver.strip()[:80], expected)
    return True, ""


def _startup_opens(binary: Path) -> int | None:
    """File opens on ``-c 'pass'``. A static build must do zero of them."""
    strace = shutil.which("strace")
    if not strace:
        return None
    rc, out = _run([strace, "-f", "-e", "trace=openat,open", str(binary), "-c", "pass"],
                   timeout=60.0)
    if rc in (124, 127):
        return None
    return sum(1 for line in out.splitlines()
               if "openat(" in line or line.lstrip().startswith("open("))


# --- rust --------------------------------------------------------------------


def resolve_target(target: str) -> str | None:
    """Alias to triple. ``""`` is the host default; ``None`` means unknown."""
    key = (target or "host").strip()
    if key in _TARGETS:
        return _TARGETS[key]
    # Anything already shaped like a triple is passed through untouched: pinning
    # the alias table shut would make a legal cross target un-buildable here.
    if key.count("-") >= 2:
        return key
    return None


def _rust_workdir() -> tuple[Path, str]:
    """The crate to build, and a note if it had to be copied to get there.

    In a checkout ``build_dir()/rust`` *is* ``paths.RUST_DIR`` and this is a
    no-op. In a wheel it is under ``~/.lypning/build``, because site-packages is
    read-only and cargo needs to write ``target/`` next to ``Cargo.toml``.
    """
    dest = paths.build_dir() / "rust"
    src = paths.RUST_DIR
    try:
        same = dest.resolve() == src.resolve()
    except OSError:
        same = False
    if same:
        return dest, ""
    n = _sync_tree(src, dest, skip={"target", ".git"})
    return dest, "crate copied to %s (package tree is read-only), %d file(s) refreshed" % (dest, n)


def _ensure_rust_target(triple: str, verbose: bool) -> tuple[str, str]:
    """``(triple_to_use, note)``. Falls back to the host rather than failing.

    A missing ``rustup`` is common (distro rustc, or a vendored toolchain) and a
    host build is still a useful build, so the fallback is not an error — but it
    changes what was produced, so it is always said out loud in the note.
    """
    if not triple:
        return "", ""
    rustup = shutil.which("rustup")
    if rustup is None:
        return "", "rustup not found: built for the host instead of %s" % triple
    rc, out = _run([rustup, "target", "list", "--installed"], timeout=120.0)
    if rc == 0 and triple in out.split():
        return triple, ""
    rc, out = _run([rustup, "target", "add", triple], timeout=900.0)
    if rc != 0:
        return "", "rustup target add %s failed (%s): built for the host instead" % (
            triple, _tail(out, verbose).strip().splitlines()[-1][:160] if out.strip() else "no output")
    return triple, "installed rust std for %s" % triple


def build_rust(target: str = "musl", jobs: int | None = None,
               verbose: bool = False, dry_run: bool = False,
               variant: str = engines.LYPNING) -> BuildResult:
    """Build the Rust core, then refuse to call it ok until the contract holds.

    The default is STATIC MUSL, and that is not a preference — it is the same
    default scripts/build-rust.sh has documented since the first measurement.
    A dynamically linked core opens five files before `main` and starts 5.5x
    slower (docs/LYPNING.md §1), which gives back most of what the runtime won;
    worse, :func:`install_binaries` puts whatever is built into the state bin
    dir, which :func:`engines.find_lypning` prefers over every cargo target —
    so a host build silently becomes the binary every later measurement uses.
    ``--target host`` still builds the glibc control, deliberately.

    ``variant`` is which point on the Rust spectrum to build (invariant 9): one
    cargo feature names it, the default variant keeps cargo's default target
    dir (so a by-hand ``cargo build`` shares the object cache), every other one
    builds under ``target/variant-<letter>/`` because alternating feature sets
    in one dir recompiles the world. Both contracts are asserted with the
    variant's OWN name.
    """
    t0 = time.perf_counter()
    try:
        feature = variant_feature(variant)
    except ValueError as e:
        return BuildResult(variant, target=str(target), seconds=time.perf_counter() - t0,
                           skipped_reason=str(e))
    triple = resolve_target(target)
    if triple is None:
        return BuildResult(variant, target=str(target), seconds=time.perf_counter() - t0,
                           skipped_reason="unknown target %r (host, musl, x86_64, i686)" % target)

    tc = toolchain()
    if tc["cargo"] is None:
        return BuildResult(variant, target=triple or "host",
                           seconds=time.perf_counter() - t0,
                           skipped_reason="cargo not found — install Rust: https://rustup.rs",
                           unavailable=True)
    if not (paths.RUST_DIR / "Cargo.toml").is_file():
        return BuildResult(variant, target=triple or "host",
                           seconds=time.perf_counter() - t0,
                           skipped_reason="no crate source at %s" % paths.RUST_DIR)

    notes: list[str] = []
    workdir, note = _rust_workdir()
    if note:
        notes.append(note)
    requested = triple
    triple, note = _ensure_rust_target(triple, verbose)
    if note:
        notes.append(note)
    # A note is for the log; ``skipped_reason`` is reserved for the one thing a
    # caller cannot see from ok/binary alone — that it did not get what it asked
    # for. Building for the host after asking for musl is exactly that.
    fallback = note if (requested and not triple) else ""

    # `--bin lypning` and not a bare `cargo build`: the crate also has a `[lib]`
    # target now, and a bare build would compile the cdylib and the 25 MB static
    # archive on every `lypning build --rust` — minutes of cargo for an artefact
    # this command was not asked for and does not install.
    cmd = [tc["cargo"], "build", "--manifest-path", str(workdir / "Cargo.toml"),
           "--release", "--bin", engines.LYPNING, "--features", feature]
    if variant != engines.LYPNING:
        cmd.append("--no-default-features")
    if triple:
        cmd += ["--target", triple]
    target_root = workdir / "target"
    if variant != engines.LYPNING:
        target_root = target_root / feature
        cmd += ["--target-dir", str(target_root)]
    if jobs:
        cmd += ["--jobs", str(int(jobs))]
    if verbose:
        cmd.append("--verbose")

    out_dir = target_root / triple / "release" if triple else target_root / "release"
    binary = out_dir / engines.LYPNING   # cargo's file name; install renames it to the variant

    if dry_run:
        return BuildResult(
            variant, target=triple or "host", binary=None,
            seconds=time.perf_counter() - t0,
            log=_join(*notes, " ".join(cmd), "would produce: %s" % binary),
            skipped_reason="dry run: nothing was built", dry_run=True,
        )

    rc, out = _run(cmd, cwd=workdir, timeout=_CARGO_TIMEOUT)
    if rc != 0:
        return BuildResult(variant, target=triple or "host",
                           seconds=time.perf_counter() - t0,
                           log=_join(*notes, _tail(out, verbose)),
                           skipped_reason="cargo build failed (exit %d)%s (`-v` for the full log)"
                                          % (rc, _why(out)))
    if not binary.is_file():
        return BuildResult(variant, target=triple or "host",
                           seconds=time.perf_counter() - t0,
                           log=_join(*notes, _tail(out, verbose)),
                           skipped_reason="cargo reported success but %s does not exist" % binary)

    size = _size(binary)
    shape = ["%s — %d bytes" % (binary, size),
             "CheerpX device blocks (%d B each): %d" % (CHEERPX_BLOCK, cheerpx_blocks(size))]
    if triple:
        opens = _startup_opens(binary)
        if opens is not None:
            shape.append("file opens on -c 'pass': %d%s" % (
                opens, "" if opens == 0 else "  WARNING: a static build should open nothing"))

    ok, why = check_refusal_contract(binary, expected=variant)
    if ok:
        ok, why = check_spectrum_contract(binary, expected=variant)
    shape.append("unsupported contract: %s" % ("held" if ok else "BROKEN — " + why))
    return BuildResult(
        variant,
        ok=ok,
        binary=binary,
        size_bytes=size,
        seconds=time.perf_counter() - t0,
        target=triple or "host",
        log=_join(*notes, _tail(out, verbose), *shape),
        skipped_reason=fallback if ok else "the unsupported contract is broken: " + why,
    )


# --- the C ABI library -------------------------------------------------------

#: What ``--lib`` produces, and the two rules that shape the list. The shared
#: library is what a harness dlopens or links; the static archive is for a host
#: that would rather ship one file; the headers are the contract both compile
#: against, so they are installed BESIDE the library and never left in the
#: package tree, which a wheel makes read-only. The shared library's name is
#: the platform's (``.dylib`` on macOS) and is decided once, in :mod:`embed`,
#: because the build, the installer and discovery must agree on it or the
#: build reports ``ok`` about a file discovery never finds.
LIB_SHARED = embed.LIB_NAME
LIB_STATIC = "liblypning.a"
LIB_HEADERS = ("lypning.h", "lypning.hpp")

#: The cargo profile the library must be built with. Not `--release`: that
#: profile sets ``panic = "abort"``, which is right for a process with nothing
#: to save and catastrophic for a library, where an abort takes down an
#: application that only asked to run a one-liner. ``capi.rs`` refuses to
#: compile under it rather than trusting this comment.
LIB_PROFILE = "release-lib"


def build_lib(target: str = "host", jobs: int | None = None,
              verbose: bool = False, dry_run: bool = False) -> BuildResult:
    """Build the embeddable C ABI, then assert the refusal contract THROUGH it.

    Two things differ from :func:`build_rust`, and both are consequences of the
    artefact being linked rather than executed.

    **The default target is the host, not musl.** A static-musl binary is the
    right shipping choice for something the OS spawns; a shared object has to
    match the libc of the process that loads it, so a musl build of this would
    be unloadable by every glibc host. ``--target musl`` is still available, for
    a host that is itself musl.

    **The contract check has no exit code to look at.** A library cannot exit
    90, so the three pinned properties are re-asserted in library terms by
    :func:`lypning.embed.check_refusal_contract` — status, an empty stdout, and
    the one line — plus a fourth that only embedding needs: the refusal must ask
    to be routed onward, because in-process it is the HOST that owns the retry.
    A build that cannot demonstrate all four is not ``ok``, for exactly the
    reason the binary's check exists: this is the part that breaks silently.
    """
    t0 = time.perf_counter()
    triple = resolve_target(target)
    if triple is None:
        return BuildResult(engines.LYPNING, artifact="lib", target=str(target),
                           seconds=time.perf_counter() - t0,
                           skipped_reason="unknown target %r (host, musl, x86_64, i686)" % target)

    tc = toolchain()
    if tc["cargo"] is None:
        return BuildResult(engines.LYPNING, artifact="lib", target=triple or "host",
                           seconds=time.perf_counter() - t0,
                           skipped_reason="cargo not found — install Rust: https://rustup.rs",
                           unavailable=True)
    if not (paths.RUST_DIR / "Cargo.toml").is_file():
        return BuildResult(engines.LYPNING, artifact="lib", target=triple or "host",
                           seconds=time.perf_counter() - t0,
                           skipped_reason="no crate source at %s" % paths.RUST_DIR)

    notes: list[str] = []
    workdir, note = _rust_workdir()
    if note:
        notes.append(note)
    if triple:
        triple, note = _ensure_rust_target(triple, verbose)
        if note:
            notes.append(note)

    # The library is the LARGEST variant, said in code: inheriting `default`
    # would make it whichever variant is the default, silently.
    cmd = [tc["cargo"], "build", "--manifest-path", str(workdir / "Cargo.toml"),
           "--lib", "--no-default-features",
           "--features", "capi,%s" % variant_feature(engines.SPECTRUM[-1]),
           "--profile", LIB_PROFILE]
    if triple:
        cmd += ["--target", triple]
    if jobs:
        cmd += ["--jobs", str(int(jobs))]
    if verbose:
        cmd.append("--verbose")

    out_dir = workdir / "target" / triple / LIB_PROFILE if triple else workdir / "target" / LIB_PROFILE
    shared = out_dir / LIB_SHARED

    if dry_run:
        return BuildResult(
            engines.LYPNING, artifact="lib", target=triple or "host", binary=None,
            seconds=time.perf_counter() - t0,
            log=_join(*notes, " ".join(cmd), "would produce: %s" % shared,
                      "would install: %s and %s" % (_lib_dir(), _include_dir())),
            skipped_reason="dry run: nothing was built", dry_run=True,
        )

    rc, out = _run(cmd, cwd=workdir, timeout=_CARGO_TIMEOUT)
    if rc != 0:
        return BuildResult(engines.LYPNING, artifact="lib", target=triple or "host",
                           seconds=time.perf_counter() - t0,
                           log=_join(*notes, _tail(out, verbose)),
                           skipped_reason="cargo build failed (exit %d)%s (`-v` for the full log)"
                                          % (rc, _why(out)))
    if not shared.is_file():
        return BuildResult(engines.LYPNING, artifact="lib", target=triple or "host",
                           seconds=time.perf_counter() - t0,
                           log=_join(*notes, _tail(out, verbose)),
                           skipped_reason="cargo reported success but %s does not exist" % shared)

    size = _size(shared)
    shape = ["%s — %d bytes" % (shared, size)]
    static = out_dir / LIB_STATIC
    if static.is_file():
        shape.append("%s — %d bytes" % (static, _size(static)))
    shape.append("exported symbols: %d" % _lib_symbols(shared))

    ok, why = embed.check_refusal_contract(shared)
    shape.append("unsupported contract (in-process): %s" % ("held" if ok else "BROKEN — " + why))

    return BuildResult(
        engines.LYPNING, artifact="lib",
        ok=ok,
        binary=shared,
        size_bytes=size,
        seconds=time.perf_counter() - t0,
        target=triple or "host",
        log=_join(*notes, _tail(out, verbose), *shape),
        skipped_reason="" if ok else "the unsupported contract is broken: " + why,
    )


def exported_symbols(shared: Path | str) -> set[str]:
    """The names a shared library exports, as C sees them.

    ``nm`` in the platform's dialect: ``-gU`` on macOS, where Mach-O prefixes
    every C symbol with an underscore that is stripped here so the names match
    the header; ``-D --defined-only`` elsewhere, where the dynamic symbol table
    is the one a host links against. Raises :class:`OSError` naming what
    failed — no ``nm``, or one that would not read the file — rather than
    answering an empty set, because "exports nothing" and "could not look" are
    different facts and a test skipping on the second must say so.
    """
    nm = shutil.which("nm")
    if not nm:
        raise OSError("nm is not on PATH")
    macho = sys.platform == "darwin"
    cmd = [nm, "-gU", str(shared)] if macho else [nm, "-D", "--defined-only", str(shared)]
    rc, out = _run(cmd, timeout=60.0)
    if rc != 0:
        raise OSError("%s exited %d: %s" % (" ".join(cmd[:2]), rc, out.strip()[-200:]))
    names: set[str] = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[-1]
        if macho and name.startswith("_"):
            name = name[1:]
        names.add(name)
    return names


def _lib_symbols(shared: Path) -> int:
    """How many ``lypning_*`` symbols the library exports.

    Reported rather than asserted: the number is a build's fingerprint, and a
    build that suddenly exports two of them is a linker script gone wrong long
    before anyone's host fails to find a symbol. ``nm`` is not required to be
    present, and 0 means "could not tell", never "none".
    """
    try:
        return sum(1 for name in exported_symbols(shared) if name.startswith("lypning_"))
    except OSError:
        return 0


def _lib_dir() -> Path:
    return embed.lib_dir()


def _include_dir() -> Path:
    return embed.include_dir()


def install_library(result: BuildResult) -> list[Path]:
    """Put the library and its headers where a host compiler can find them.

    ``~/.lypning/lib`` and ``~/.lypning/include``, beside the engine bin dir and
    for the same reason: the package tree is read-only in a wheel, and a header
    a caller cannot ``-I`` is not a contract they can compile against. The
    headers come from the asset tree rather than from the build, because they
    are source, not output — and they are copied on every install so a header
    and a library in the same directory always describe each other.
    """
    if not result.ok or result.binary is None or result.artifact != "lib":
        return []
    installed: list[Path] = []
    lib_dest = paths.ensure_dir(_lib_dir())
    inc_dest = paths.ensure_dir(_include_dir())
    built = Path(result.binary)
    for src in (built, built.with_name(LIB_STATIC)):
        if not src.is_file():
            continue
        dest = lib_dest / src.name
        tmp = dest.with_name(dest.name + ".new")
        try:
            shutil.copy2(src, tmp)
            os.chmod(tmp, 0o755)
            os.replace(tmp, dest)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            continue
        installed.append(dest)
    for name in LIB_HEADERS:
        src = paths.INCLUDE_DIR / name
        if not src.is_file():
            continue
        try:
            shutil.copy2(src, inc_dest / name)
        except OSError:
            continue
        installed.append(inc_dest / name)
    return installed


# --- micropython -------------------------------------------------------------

#: The benchmark control's file name. Deliberately **not** an engine name —
#: there are exactly three of those — because the control is the thing
#: lypning-mp is measured *against*. An engine finder that could turn it up
#: would eventually route a program to unpatched upstream MicroPython, and the
#: whole comparison would read 1.00x and look like a clean result.
STOCK_BINARY = "micropython-stock"

#: The pin lives in the build script and is read back out of it, never restated
#: here: an entry in ``docs/BENCH-LEDGER.md`` claims both binaries came from one
#: commit, and that claim has to come from the file that does the checking out.
_PIN_RE = {
    "tag": re.compile(r'^MPY_TAG="([^"]+)"', re.M),
    "commit": re.compile(r'^MPY_COMMIT="([0-9a-f]+)"', re.M),
}


def micropython_pin() -> dict[str, str]:
    """``{"tag": ..., "commit": ...}``, or empty strings when it cannot be read."""
    out = {"tag": "", "commit": ""}
    try:
        text = (paths.SCRIPTS_DIR / "build-micropython.sh").read_text(encoding="utf-8")
    except OSError:
        return out
    for key, pattern in _PIN_RE.items():
        m = pattern.search(text)
        if m:
            out[key] = m.group(1)
    return out


def _micropython_workdir() -> tuple[Path, Path, str]:
    """``(script, tree, note)`` — the tree to build, and the script to build it with.

    ``build-micropython.sh`` derives everything from its own location: the
    engine tree is ``<script>/../micropython`` and both binaries land in
    ``build/`` inside it, which is exactly where :func:`engines.find_micropython`
    and :func:`stock_binary` look. In a checkout that is the asset tree as it
    ships, nothing is copied, and a ``make`` by hand shares the musl and
    MicroPython caches with this.

    In a wheel it cannot be: the assets are read-only, and the script would
    derive a tree inside site-packages and try to write a MicroPython checkout,
    a musl build and two binaries into it. So **both** halves are copied under
    :func:`paths.build_dir` keeping the same relative layout — the script beside
    a ``micropython`` sibling — because the layout is the interface.
    """
    root = paths.build_dir()
    tree = root / "micropython"
    script = root / "scripts" / "build-micropython.sh"
    note = ""

    # A staging tree built by the version of this function that worked around a
    # bug in the script's own path derivation (it looked for `$REPO_ROOT/lypning-mp`
    # while the asset ships at `micropython/`). The script derives the right
    # tree now, so the symlink farm is dead weight — and a stale symlink into
    # the asset tree is worse than dead weight the day the asset moves.
    shutil.rmtree(paths.state_dir() / "mp-stage", ignore_errors=True)

    try:
        same = tree.resolve() == paths.MICROPYTHON_DIR.resolve()
    except OSError:
        same = False
    if same:
        return paths.SCRIPTS_DIR / "build-micropython.sh", tree, note

    n = _sync_tree(paths.MICROPYTHON_DIR, tree, skip={".build", "build", ".git"})
    paths.ensure_dir(script.parent)
    shutil.copy2(paths.SCRIPTS_DIR / "build-micropython.sh", script)
    os.chmod(script, 0o755)
    return script, tree, ("engine tree copied to %s (package tree is read-only), "
                          "%d file(s) refreshed" % (tree, n))


def stock_binary() -> Path | None:
    """The benchmark control, or ``None``. Absent far more often than present.

    ``$LYPNING_STOCK_BIN`` first, then the one path the build script writes it
    to. Never ``$PATH`` and never the engine bin dir: the control has to be
    something a caller asked for by name, and nothing else should be able to
    pick it up by accident.
    """
    env = os.environ.get("LYPNING_STOCK_BIN", "").strip()
    candidates = [Path(env).expanduser()] if env else []
    candidates.append(paths.build_dir() / "micropython" / "build" / STOCK_BINARY)
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c.resolve()
    return None


def build_micropython(verbose: bool = False, clean: bool = False,
                      dry_run: bool = False) -> BuildResult:
    """Build the MicroPython tier, or say precisely why it cannot be built.

    Every precondition the shell script would ``die`` on is checked first,
    because a caller running ``lypning build --all`` on a machine without
    ``gcc-multilib`` wants one line telling it which apt package is missing, not
    a 5,000-line log with the answer in the middle.
    """
    return _build_micropython(False, verbose=verbose, clean=clean, dry_run=dry_run)


def build_stock(verbose: bool = False, clean: bool = False,
                dry_run: bool = False) -> BuildResult:
    """Build the benchmark CONTROL: upstream MicroPython, unpatched.

    Same pinned commit, same musl-i386 libc, same compiler and flags, same strip
    — and none of our port patch and none of the frozen shim stdlib. That
    subtraction is the only reason a lypning-mp timing means anything, and it is
    valid only if the two binaries differ in nothing else, so the script does
    not hand-write the control's makefile: it **extracts the block between the
    ``SHARED TOOLCHAIN BLOCK`` markers in
    ``assets/micropython/variant/mpconfigvariant.mk`` verbatim** into it, and
    dies rather than fall back to copied flags if the markers are gone. ``-m32``,
    ``-static``, ``-Wl,-m,elf_i386``, ``-fno-stack-protector`` and
    ``COPT=-Os -DNDEBUG`` therefore cannot drift apart: editing them edits both
    binaries. The control's tree is additionally asserted clean at the pinned
    commit after its reset, which is the mechanical proof that no patch of ours
    reached it, and its own shape checks assert it is **not** lypning-mp — a copy
    of lypning-mp sitting here would make every ratio in the ledger read 1.00
    and look like a clean result.

    The five things the offline static build forces on the control instead —
    empty ``FROZEN_MANIFEST``, no btree, no ffi, no ssl, no FAT/littlefs — are
    listed in ``build_stock()`` in the script, which is the authority. The
    result is what ``lypning bench --micropython`` compares against.
    """
    return _build_micropython(True, verbose=verbose, clean=clean, dry_run=dry_run)


def _build_micropython(stock: bool, verbose: bool = False, clean: bool = False,
                       dry_run: bool = False) -> BuildResult:
    """The shared preflight and invocation. ``stock`` picks which binary comes out.

    One function because the two builds share every precondition — the same
    toolchain, the same musl, the same checkout, the same network — and a second
    copy of those checks is a second place for them to go stale.
    """
    t0 = time.perf_counter()
    label = STOCK_BINARY if stock else engines.MICROPYTHON

    def skipped(reason: str, log: str = "", unavailable: bool = False) -> BuildResult:
        return BuildResult(label, target="i386-musl",
                           seconds=time.perf_counter() - t0, log=log, skipped_reason=reason,
                           unavailable=unavailable)

    src_script = paths.SCRIPTS_DIR / "build-micropython.sh"
    if not src_script.is_file():
        return skipped("no build script at %s" % src_script)
    if not paths.MICROPYTHON_DIR.is_dir():
        return skipped("no engine source at %s" % paths.MICROPYTHON_DIR)

    # The same list the script checks, plus cc: it dies on any one of them, and
    # dying five minutes in with a partly-built musl is worse than not starting.
    missing = [t for t in ("gcc", "make", "git", "tar", "python3") if shutil.which(t) is None]
    if shutil.which("cc") is None and "gcc" not in missing:
        missing.append("cc")
    if missing:
        return skipped("missing build tools: %s" % ", ".join(missing), unavailable=True)
    if shutil.which("curl") is None and shutil.which("wget") is None:
        return skipped("need curl or wget to download the musl tarball", unavailable=True)

    # The 32-bit host toolchain. Naming the apt package is the single most
    # useful thing this check can do, so it says it the way the script does.
    rc, out = _c_probe()
    if rc != 0:
        return skipped(
            "gcc cannot target i386 — install the multilib toolchain: "
            "sudo apt-get install -y gcc-multilib libc6-dev-i386",
            _tail(out, verbose), unavailable=True)

    try:
        script, tree, note = _micropython_workdir()
    except OSError as e:
        return skipped("cannot prepare the build tree: %s" % e)
    out_bin = tree / "build" / label

    work = tree / ".build"
    env = {
        "LYPNING_WORK": str(work),
        "LYPNING_HOME": str(paths.state_dir()),
        "LYPNING_CAPTURE": "0",
    }
    cmd = ["bash", str(script)]
    if clean:
        cmd.append("--clean")
    if stock:
        cmd.append("--stock")

    # Two pinned downloads, once. Cached, the build needs no network at all, so
    # only probe when the cache is cold — an offline rebuild is legitimate.
    musl_cached = (work / "musl-i386" / "lib" / "libc.a").is_file()
    mpy_cached = (work / "micropython" / ".git").exists()
    if not (musl_cached and mpy_cached) and not _can_reach(_PINNED_HOST):
        return skipped(
            "no network, and the pinned musl/MicroPython downloads are not cached in %s" % work,
            note, unavailable=True)

    if dry_run:
        return BuildResult(
            label, target="i386-musl", seconds=time.perf_counter() - t0,
            log=_join(note,
                      " ".join("%s=%s" % kv for kv in sorted(env.items())) + " " + " ".join(cmd),
                      "would produce: %s" % out_bin,
                      "musl cached: %s, micropython cached: %s" % (musl_cached, mpy_cached)),
            skipped_reason="dry run: nothing was built", dry_run=True,
        )

    rc, out = _run(cmd, cwd=script.parent.parent, env=env, timeout=_MICROPYTHON_TIMEOUT)
    if rc != 0 or not out_bin.is_file():
        # The preflight probe ran minutes ago and only proves the host answered
        # *then*. A download that died halfway is an outage wearing a build
        # failure's clothes, so ask what broke before calling this a regression.
        network = _transport_failed(rc, out)
        return skipped(
            "build-micropython.sh %s (exit %d)%s (`-v` for the full log)"
            % ("could not fetch its pinned downloads" if network else "failed", rc, _why(out)),
            _join(note, _tail(out, verbose)), unavailable=network)

    size = _size(out_bin)
    return BuildResult(
        label, ok=True, binary=out_bin, size_bytes=size,
        seconds=time.perf_counter() - t0, target="i386-musl",
        log=_join(note, _tail(out, verbose),
                  "%s — %d bytes" % (out_bin, size),
                  "CheerpX device blocks (%d B each): %d" % (CHEERPX_BLOCK, cheerpx_blocks(size))),
    )


def _c_probe() -> tuple[int, str]:
    """``gcc -m32`` against a real one-line program, via a temp file.

    Piping the source on stdin is what the shell script does; doing the same
    from Python means feeding a subprocess stdin *and* capturing both streams,
    so a temp file is used instead and the answer is identical.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "probe.c"
        src.write_text("int main(void){return 0;}\n")
        return _run(["gcc", "-m32", str(src), "-o", str(Path(td) / "probe")],
                    timeout=120.0, env={"LC_ALL": "C"})


# --- orchestration -----------------------------------------------------------


def build_all(rust: bool = True, micropython: bool = True, target: str = "musl",
              jobs: int | None = None, verbose: bool = False,
              dry_run: bool = False, stock: bool = False,
              lib: bool = False, lib_target: str = "",
              variant: str = "all") -> list[BuildResult]:
    """Build what was asked for, in tier order, and never stop on a failure.

    The tiers are independent — a missing 32-bit toolchain says nothing about
    the Rust core — so one failing must not cost the caller the other's result.

    ``stock`` is the benchmark control rather than a tier, and it comes last for
    the same reason: it is the slowest thing here and the only one nothing else
    depends on.
    """
    results: list[BuildResult] = []
    if rust:
        # Every variant on the spectrum by default, so the dev tree is never a
        # build behind on one sibling; `--variant NAME` narrows an inner loop.
        names = list(engines.SPECTRUM) if variant == "all" else [variant]
        for name in names:
            results.append(build_rust(target=target, jobs=jobs, verbose=verbose,
                                      dry_run=dry_run, variant=name))
    if lib:
        # The HOST target unless the caller named one, whatever the binary is
        # building for: a shared object has to match the libc of the process
        # that loads it, and the musl default that is right for a spawned
        # binary would produce one no glibc host could dlopen. A caller who
        # asks for `--target musl` on a musl host gets it — the default is a
        # default, not a restriction, and a docstring that said otherwise while
        # the flag was silently dropped was worse than either.
        results.append(build_lib(target=lib_target or "host", jobs=jobs,
                                 verbose=verbose, dry_run=dry_run))
    if micropython:
        results.append(build_micropython(verbose=verbose, dry_run=dry_run))
    if stock:
        results.append(build_stock(verbose=verbose, dry_run=dry_run))
    return results


def _runs_here(target: str) -> bool:
    """Can a binary built for ``target`` execute on this machine?

    Only the architecture is asked about. A 32-bit build often *does* run on a
    64-bit host, but only when the loader for it is installed, and a musl-static
    i686 binary that happens to run here is still not what this host should be
    dispatching to — the host build exists and is faster to nobody's surprise.
    Same-arch is the only honest yes. ``"host"`` is this machine by definition:
    it used to be compared as an architecture name, fail, and install the only
    binary that runs here as ``lypning-host`` — which no finder ever looked for.
    """
    if not target or target == "host":
        return True
    import platform

    host = platform.machine()
    arch = target.split("-", 1)[0]
    if arch == host:
        return True
    # x86_64 and amd64 are the same machine under two names.
    return {arch, host} == {"x86_64", "amd64"}


def install_binaries(results: Iterable[BuildResult]) -> list[Path]:
    """Copy every successful build into :func:`paths.bin_dir`, named by engine.

    Written to a sibling and ``os.replace``d in: the destination may be the
    binary a shim is executing right now, and overwriting it in place is an
    ``ETXTBSY`` at best and a half-written interpreter at worst.
    """
    dest_dir = paths.ensure_dir(paths.bin_dir())
    installed: list[Path] = []
    for r in results:
        if not r.ok or r.binary is None:
            continue
        if r.artifact:
            # Not an engine. The C ABI library goes to `lib/` and its headers to
            # `include/` (:func:`install_library`); dropping a shared library
            # into the directory the engine finders read would make
            # `find_lypning` offer it to `os.execv`.
            continue
        if r.engine == STOCK_BINARY:
            # The control stays in the build tree. This directory is where the
            # engine finders look, and a control that can be found is a control
            # that can be run — at which point the benchmark compares stock
            # against stock and reports 1.00x as a result.
            continue
        src = Path(r.binary)
        if not src.is_file():
            continue
        # A CROSS-TARGET build is not this machine's engine. `--target i686`
        # exists for the CheerpX sandbox, and installing it as `lypning` made
        # the default engine a 32-bit binary: every dispatch, conformance run
        # and benchmark afterwards silently measured the wrong artifact, and on
        # a host without multilib it would not have executed at all. So a build
        # that cannot run here is installed under a suffixed name and reported,
        # never over the plain one.
        dest = dest_dir / r.engine
        if r.target and not _runs_here(r.target):
            dest = dest_dir / ("%s-%s" % (r.engine, r.target.split("-")[0]))
        tmp = dest.with_name(dest.name + ".new")
        try:
            shutil.copy2(src, tmp)
            os.chmod(tmp, 0o755)
            os.replace(tmp, dest)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            continue
        installed.append(dest)
    return installed


# --- verify ------------------------------------------------------------------


@dataclass
class VerifyResult:
    """What ``--verify`` found. Every part is kept; ``ok`` is their conjunction.

    ``gates`` is ``[(binary, GateReport)]`` and ``conformance`` is a
    :class:`lypning.conformance.Report`, both held rather than reduced to a
    boolean: the caller renders them with their own modules' renderers, which is
    the only way the reason a gate failed survives the trip.
    """

    gates: list[tuple[str, object]] = field(default_factory=list)
    conformance: object = None
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        gates_ok = all(getattr(g, "ok", False) for _, g in self.gates)
        conf = self.conformance
        return bool(gates_ok and (conf is None or getattr(conf, "ok", False)))


def verify(results: Iterable[BuildResult] | None = None, *, limit: int | None = None,
           compare: bool = True, timeout: float = 30.0) -> VerifyResult:
    """Build's ``--verify``: gate the binaries, then run the whole battery.

    The two halves answer different questions and neither is optional because
    the other passed. The gate is shape — static, bytes, file opens — and is
    what predicts cold cost in the sandbox. The battery is agreement with
    CPython, and it is the only thing that catches a build that produces a
    perfectly shaped binary which quietly answers differently.

    **Both are pointed at the binaries this build just produced**, by pinning
    ``$LYPNING_BIN`` and ``$LYPNING_MP_BIN`` for the duration. Without that a
    build whose binary is broken enough not to be installed would be verified
    against the previous one still sitting in the bin dir, and report ``ok`` for
    a binary nobody measured. The environment is restored afterwards: this is a
    library, and a caller that runs anything else in the same process must not
    inherit our overrides.

    The benchmark control is skipped and said so: it is unpatched upstream
    MicroPython and owes none of these contracts.
    """
    t0 = time.perf_counter()
    # Imported here rather than at module scope: a plain `lypning build --rust`
    # must not pay for the corpus loader and `ast` in order to run cargo.
    from . import conformance, gate

    out = VerifyResult()
    pins: dict[str, str] = {}
    subjects: list[Path] = []
    for r in (list(results) if results is not None else []):
        if r.engine == STOCK_BINARY:
            out.notes.append("the benchmark control is not gated: it is upstream "
                             "MicroPython and owes none of these contracts")
            continue
        if not r.ok or r.binary is None:
            continue
        if r.artifact == "lib":
            # A shared object is not a binary. Gating it would ask `gate` how
            # many files it opens on `-c 'pass'`, which means exec'ing it — and
            # pinning it as $LYPNING_BIN would hand every later dispatch, route
            # and battery run an artefact the kernel cannot start. It is pinned
            # as the LIBRARY instead, so the battery's `library` arm measures
            # the one that was just built rather than the one already installed.
            pins["LYPNING_LIB"] = str(r.binary)
            continue
        subjects.append(Path(r.binary))
        if r.engine in engines.SPECTRUM + (engines.MICROPYTHON,):
            pins[engines.env_var_for(r.engine)] = str(r.binary)
    if results is None:
        subjects = [p for p in (engines.find(e) for e in engines.SPECTRUM + (engines.MICROPYTHON,)) if p]

    saved = {k: os.environ.get(k) for k in
             [engines.env_var_for(e) for e in engines.SPECTRUM + (engines.MICROPYTHON,)] + ["LYPNING_LIB"]}
    try:
        os.environ.update(pins)
        for b in subjects:
            out.gates.append((str(b), gate.gate(b, compare=compare)))
        if engines.find_cpython() is None:
            out.notes.append("no reference CPython: the battery was not run")
        else:
            # The library arm is added when a library was built, and only then:
            # pinning $LYPNING_LIB for a battery that never runs that arm was a
            # verification of nothing, reported as ok.
            arms = list(conformance.DEFAULT_ARMS)
            if "LYPNING_LIB" in pins:
                arms.append(conformance.LIBRARY)
            out.conformance = conformance.run(engines=arms, limit=limit, timeout=timeout)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    out.seconds = time.perf_counter() - t0
    return out


# --- rendering ---------------------------------------------------------------


def report(results: Iterable[BuildResult] | BuildResult, verbose: bool = False) -> str:
    """The one function here that formats. Returns the table; prints nothing."""
    items = [results] if isinstance(results, BuildResult) else list(results)
    if not items:
        return "nothing to build"
    head = ("engine", "target", "bytes", "blocks", "secs", "status")
    rows = [head]
    for r in items:
        if r.ok:
            status = "ok" + ("  (%s)" % r.skipped_reason if r.skipped_reason else "")
        elif r.dry_run:
            status = r.skipped_reason or "dry run"
        elif r.unavailable:
            # Deliberately not "FAILED". Nothing was attempted, so nothing is
            # broken, and a reader who sees FAILED here goes looking for a
            # regression that does not exist — which is exactly how four CI
            # runs' worth of "no network" read as a wrong answer in the tier.
            status = "unavailable: " + (r.skipped_reason or "unknown")
        else:
            status = "FAILED: " + (r.skipped_reason or "unknown")
        rows.append((
            r.label,
            r.target or "-",
            str(r.size_bytes) if r.size_bytes else "-",
            str(r.cheerpx_blocks) if r.size_bytes else "-",
            "%.1f" % r.seconds,
            status,
        ))
    widths = [max(len(row[i]) for row in rows) for i in range(len(head))]
    lines = []
    for row in rows:
        cells = [row[i].ljust(widths[i]) for i in range(len(head) - 1)] + [row[-1]]
        lines.append("  ".join(cells).rstrip())
    for r in items:
        # A dry run's whole output IS the commands — printing only the table
        # would leave `--dry-run` saying nothing a plain `--help` does not.
        if (verbose or r.dry_run) and r.log:
            lines.append("")
            lines.append("--- %s ---" % r.label)
            lines.append(r.log)
    return "\n".join(lines)
