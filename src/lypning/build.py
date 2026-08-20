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

Nothing here prints. :func:`report` renders a table and returns it.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from . import UNSUPPORTED_EXIT
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
REFUSAL_LINE = "lypning: unsupported: module: import subprocess"

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


# --- records -----------------------------------------------------------------


@dataclass
class BuildResult:
    """One engine's build. ``ok`` means built *and* the contract still holds.

    ``skipped_reason`` is not restricted to skips: it carries the one-line "why"
    for anything a caller would otherwise have to read ``log`` to discover — a
    missing toolchain, a fallback to the host target, a broken contract.
    """

    engine: str
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


def check_refusal_contract(binary: Path | str) -> tuple[bool, str]:
    """``(ok, why)`` for the three things a refusal must do, all at once.

    Exit ``90``; the exact line on stderr; **nothing** on stdout. The last one is
    the one that hurts: a refusal written to stdout still exits 90 and still
    looks right in a terminal, while it silently poisons every ``… | wc -l``
    the caller had around it.
    """
    res = engines.run(engines.LYPNING, REFUSAL_PROGRAM, binary=Path(binary), timeout=60.0)
    if res.returncode != UNSUPPORTED_EXIT:
        return False, "exit %d, expected %d (%s)" % (
            res.returncode, UNSUPPORTED_EXIT, res.stderr.strip()[:160] or "no stderr")
    if res.stdout != "":
        return False, "the refusal line reached stdout: %r" % res.stdout[:120]
    if res.stderr.strip() != REFUSAL_LINE:
        return False, "stderr was %r, expected %r" % (res.stderr.strip()[:160], REFUSAL_LINE)
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
               verbose: bool = False, dry_run: bool = False) -> BuildResult:
    """Build the Rust core, then refuse to call it ok until the contract holds.

    The default is STATIC MUSL, and that is not a preference — it is the same
    default scripts/build-rust.sh has documented since the first measurement.
    A dynamically linked core opens five files before `main` and starts 5.5x
    slower (docs/LYPNING.md §1), which gives back most of what the runtime won;
    worse, :func:`install_binaries` puts whatever is built into the state bin
    dir, which :func:`engines.find_lypning` prefers over every cargo target —
    so a host build silently becomes the binary every later measurement uses.
    ``--target host`` still builds the glibc control, deliberately.
    """
    t0 = time.perf_counter()
    triple = resolve_target(target)
    if triple is None:
        return BuildResult(engines.LYPNING, target=str(target), seconds=time.perf_counter() - t0,
                           skipped_reason="unknown target %r (host, musl, x86_64, i686)" % target)

    tc = toolchain()
    if tc["cargo"] is None:
        return BuildResult(engines.LYPNING, target=triple or "host",
                           seconds=time.perf_counter() - t0,
                           skipped_reason="cargo not found — install Rust: https://rustup.rs")
    if not (paths.RUST_DIR / "Cargo.toml").is_file():
        return BuildResult(engines.LYPNING, target=triple or "host",
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

    cmd = [tc["cargo"], "build", "--manifest-path", str(workdir / "Cargo.toml"), "--release"]
    if triple:
        cmd += ["--target", triple]
    if jobs:
        cmd += ["--jobs", str(int(jobs))]
    if verbose:
        cmd.append("--verbose")

    out_dir = workdir / "target" / triple / "release" if triple else workdir / "target" / "release"
    binary = out_dir / engines.LYPNING

    if dry_run:
        return BuildResult(
            engines.LYPNING, target=triple or "host", binary=None,
            seconds=time.perf_counter() - t0,
            log=_join(*notes, " ".join(cmd), "would produce: %s" % binary),
            skipped_reason="dry run: nothing was built", dry_run=True,
        )

    rc, out = _run(cmd, cwd=workdir, timeout=_CARGO_TIMEOUT)
    if rc != 0:
        return BuildResult(engines.LYPNING, target=triple or "host",
                           seconds=time.perf_counter() - t0,
                           log=_join(*notes, _tail(out, verbose)),
                           skipped_reason="cargo build failed (exit %d)%s (`-v` for the full log)"
                                          % (rc, _why(out)))
    if not binary.is_file():
        return BuildResult(engines.LYPNING, target=triple or "host",
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

    ok, why = check_refusal_contract(binary)
    shape.append("unsupported contract: %s" % ("held" if ok else "BROKEN — " + why))
    return BuildResult(
        engines.LYPNING,
        ok=ok,
        binary=binary,
        size_bytes=size,
        seconds=time.perf_counter() - t0,
        target=triple or "host",
        log=_join(*notes, _tail(out, verbose), *shape),
        skipped_reason=fallback if ok else "the unsupported contract is broken: " + why,
    )


# --- micropython -------------------------------------------------------------


def _micropython_stage() -> tuple[Path, Path, Path, str]:
    """``(script, tree, out, note)`` — lay out what the shell script expects.

    ``build-micropython.sh`` derives everything from its own location: it builds
    ``<script>/../micropython`` and writes the binary to ``build/lypning-mp``
    inside that tree. Rather than fork the script or move the asset, a staging
    directory is handed to it with the script copied in and ``micropython``
    symlinked at the engine tree under :func:`paths.build_dir`, which is exactly
    where :func:`engines.find_micropython` looks for the result.

    The link name is the script's, not the engine's. It was ``lypning-mp`` once
    — the engine's name reads better — and the build died at "no patches in
    micropython/variant/patches" every time, because the script had been handed
    a tree under a name it never looks for.
    """
    tree = paths.build_dir() / "micropython"
    note = ""
    try:
        same = tree.resolve() == paths.MICROPYTHON_DIR.resolve()
    except OSError:
        same = False
    if not same:
        n = _sync_tree(paths.MICROPYTHON_DIR, tree, skip={".build", "build", ".git"})
        note = "engine tree copied to %s, %d file(s) refreshed" % (tree, n)

    # The stage lives in state, not in build_dir(): in a checkout build_dir() is
    # the asset tree and this plumbing is not source, and it cannot live inside
    # $LYPNING_WORK either because a clean rebuild removes that directory out
    # from under the running script.
    stage = paths.ensure_dir(paths.state_dir() / "mp-stage")
    scripts = paths.ensure_dir(stage / "scripts")
    script = scripts / "build-micropython.sh"
    shutil.copy2(paths.SCRIPTS_DIR / "build-micropython.sh", script)
    os.chmod(script, 0o755)

    # A stage left over from the version that named this link after the engine.
    stale = stage / engines.MICROPYTHON
    if stale.is_symlink():
        stale.unlink()

    link = stage / "micropython"
    if link.is_symlink() or link.exists():
        if not link.is_symlink() or os.readlink(str(link)) != str(tree):
            if link.is_symlink() or link.is_file():
                link.unlink()
            else:
                shutil.rmtree(link, ignore_errors=True)
    if not link.exists():
        link.symlink_to(tree, target_is_directory=True)
    return script, tree, tree / "build" / engines.MICROPYTHON, note


def build_micropython(verbose: bool = False, clean: bool = False,
                      dry_run: bool = False) -> BuildResult:
    """Build the MicroPython tier, or say precisely why it cannot be built.

    Every precondition the shell script would ``die`` on is checked here first,
    because a caller running ``lypning build --all`` on a machine without
    ``gcc-multilib`` wants one line telling it which apt package is missing, not
    a 5,000-line log with the answer in the middle.
    """
    t0 = time.perf_counter()

    def skipped(reason: str, log: str = "") -> BuildResult:
        return BuildResult(engines.MICROPYTHON, target="i386-musl",
                           seconds=time.perf_counter() - t0, log=log, skipped_reason=reason)

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
        return skipped("missing build tools: %s" % ", ".join(missing))
    if shutil.which("curl") is None and shutil.which("wget") is None:
        return skipped("need curl or wget to download the musl tarball")

    # The 32-bit host toolchain. Naming the apt package is the single most
    # useful thing this check can do, so it says it the way the script does.
    rc, out = _c_probe()
    if rc != 0:
        return skipped(
            "gcc cannot target i386 — install the multilib toolchain: "
            "sudo apt-get install -y gcc-multilib libc6-dev-i386",
            _tail(out, verbose))

    try:
        script, tree, out_bin, note = _micropython_stage()
    except OSError as e:
        return skipped("cannot stage the build tree: %s" % e)

    work = tree / ".build"
    env = {
        "LYPNING_WORK": str(work),
        "LYPNING_HOME": str(paths.state_dir()),
        "LYPNING_CAPTURE": "0",
    }
    cmd = ["bash", str(script)]
    if clean:
        cmd.append("--clean")

    # Two pinned downloads, once. Cached, the build needs no network at all, so
    # only probe when the cache is cold — an offline rebuild is legitimate.
    musl_cached = (work / "musl-i386" / "lib" / "libc.a").is_file()
    mpy_cached = (work / "micropython" / ".git").exists()
    if not (musl_cached and mpy_cached) and not _can_reach("musl.libc.org"):
        return skipped(
            "no network, and the pinned musl/MicroPython downloads are not cached in %s" % work,
            note)

    if dry_run:
        return BuildResult(
            engines.MICROPYTHON, target="i386-musl", seconds=time.perf_counter() - t0,
            log=_join(note,
                      " ".join("%s=%s" % kv for kv in sorted(env.items())) + " " + " ".join(cmd),
                      "would produce: %s" % out_bin,
                      "musl cached: %s, micropython cached: %s" % (musl_cached, mpy_cached)),
            skipped_reason="dry run: nothing was built", dry_run=True,
        )

    rc, out = _run(cmd, cwd=script.parent.parent, env=env, timeout=_MICROPYTHON_TIMEOUT)
    if rc != 0 or not out_bin.is_file():
        return skipped("build-micropython.sh failed (exit %d)%s (`-v` for the full log)"
                       % (rc, _why(out)), _join(note, _tail(out, verbose)))

    size = _size(out_bin)
    return BuildResult(
        engines.MICROPYTHON, ok=True, binary=out_bin, size_bytes=size,
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
              dry_run: bool = False) -> list[BuildResult]:
    """Build what was asked for, in tier order, and never stop on a failure.

    The tiers are independent — a missing 32-bit toolchain says nothing about
    the Rust core — so one failing must not cost the caller the other's result.
    """
    results: list[BuildResult] = []
    if rust:
        results.append(build_rust(target=target, jobs=jobs, verbose=verbose, dry_run=dry_run))
    if micropython:
        results.append(build_micropython(verbose=verbose, dry_run=dry_run))
    return results


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
        src = Path(r.binary)
        if not src.is_file():
            continue
        dest = dest_dir / r.engine
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
        else:
            status = "FAILED: " + (r.skipped_reason or "unknown")
        rows.append((
            r.engine,
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
            lines.append("--- %s ---" % r.engine)
            lines.append(r.log)
    return "\n".join(lines)
