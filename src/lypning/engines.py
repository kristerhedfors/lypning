"""Finding the three engines, running one, and routing between them.

The invariants this module exists to hold:

**A refusal is not a failure.** Exit ``90`` with one line on stderr means "this
program is outside my subset", and it is the only reason a caller may move to
the next tier. Any other non-zero exit is the program's own, and must be
reported as the program's own — a dispatcher that retried on exit 1 would run a
half-completed program twice.

**The refusal line goes to stderr and stdout stays clean.** lypning-mp once
wrote tracebacks to stdout and poisoned every ``… | wc -l`` pipeline while the
exit code still looked right, so :func:`run` keeps the two apart and the build
scripts pin it.

**CPython means the real CPython.** Capture installs a shim named ``python3``
first on ``$PATH``; a conformance run that measured the shim would be measuring
a shell script. :func:`find_cpython` walks past anything carrying the shim
marker.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from . import paths
from . import UNSUPPORTED_EXIT

SHIM_MARKER = "LYPNING_SHIM_MARKER"

LYPNING = "lypning"
MICROPYTHON = "lypning-mp"
CPYTHON = "cpython"

#: The Rust spectrum, cheapest first: every variant built from the one crate, in
#: cost order. One entry today; ``"lypning-l"`` joins when the 4 MB variant lands.
#: This tuple is the Python copy of ``route::SPECTRUM`` — it must stay a module
#: constant (argparse choices and the fork-free hooks read it at import, and can
#: spawn no binary to ask), and a test pins it to the Rust table once one exists.
#: A variant's name is ``lypning`` plus one lowercase letter from the closed set
#: {``l``} (``s`` is reserved for a small variant; ``m`` is never used, it reads
#: as ``-mp``), and it precedes the install-target suffix: ``lypning-l-i686``.
SPECTRUM = (LYPNING,)

#: Not a fourth engine — the same lypning, reached through the C ABI instead of
#: through a process, the way an embedding host reaches it. It is spelled
#: separately because it is a separate ARM to measure: the interpreter is
#: identical, the plumbing around it (`embed.rs`, `capi.rs`) is not, and a
#: conformance battery that never exercised the plumbing would prove nothing
#: about the artefact a harness actually links.
LIBRARY = "library"

ENGINE_ORDER = SPECTRUM + (MICROPYTHON, CPYTHON)

#: Install-target suffixes `lypning build --target` appends so a cross-target
#: binary never overwrites the host's: ``lypning-i686``. Documented, not
#: enforced — :func:`parse_binary_name` takes whatever follows the engine name
#: as the target, exactly as the gate's name check always has.
ARCH_TOKENS = ("host", "i686", "x86_64", "aarch64", "arm64")


def parse_binary_name(name: str) -> tuple[str, str]:
    """``(engine, target)`` from an installed binary's file name.

    The one parser for the ``<engine>[-<target>]`` shape. Engine names are tried
    longest first so ``lypning-mp-i386`` is MicroPython for i386 and, once the
    spectrum has ``lypning-l``, ``lypning-l-i686`` is that variant for i686 —
    the ordering bug the gate's docstring used to warn about, solved once.
    A name that is not an engine's parses as ``("", name)``.
    """
    base = name.rsplit("/", 1)[-1]
    for engine in sorted(SPECTRUM + (MICROPYTHON,), key=len, reverse=True):
        if base == engine:
            return engine, ""
        if base.startswith(engine + "-"):
            return engine, base[len(engine) + 1:]
    return "", base


def env_var_for(engine: str) -> str:
    """The ``LYPNING_*`` variable that pins ``engine``'s binary.

    ``LYPNING_BIN`` for the unsuffixed Rust variant, ``LYPNING_<V>_BIN`` for a
    suffixed one (``LYPNING_L_BIN``), and the two historical names for the other
    tiers. Spelled by rule so the five places that used to spell them by hand
    cannot disagree.
    """
    if engine == CPYTHON:
        return "LYPNING_CPYTHON"
    if engine == MICROPYTHON:
        return "LYPNING_MP_BIN"
    if engine == LYPNING:
        return "LYPNING_BIN"
    if engine in SPECTRUM and engine.startswith(LYPNING + "-"):
        return "LYPNING_%s_BIN" % engine[len(LYPNING) + 1:].upper()
    raise ValueError("not an engine: %r" % (engine,))


def refusal_line(engine: str, kind: str, detail: str) -> str:
    """One refusal line as ``engine`` writes it (invariant 2), spelled once.

    Every variant writes its OWN name at the head; the build and the embedding
    check assert the line for the binary they just produced through this, never
    through a literal.
    """
    return "%s: unsupported: %s: %s" % (engine, kind, detail)

#: The refusal line, exactly as every tier writes it:
#: ``<engine>: unsupported: <kind>: <detail>``. Spelled once here because two
#: readers depend on it agreeing — :attr:`Result.refused`, which decides whether
#: the chain moves on, and :mod:`lypning.conformance`, which decides whether the
#: refusal counts as coverage.
_REFUSAL_RE = re.compile(r"^[\w.-]+: unsupported: [\w-]+: .+$", re.M)


# --- discovery ---------------------------------------------------------------


class EngineError(Exception):
    """An engine was named explicitly and cannot be used. One line, no traceback.

    Only raised for a path the *caller* chose — an ``$LYPNING_BIN`` that points
    at nothing. Discovery finding nothing is not an error: "not built" is a
    status line everywhere in this package.
    """


def _is_compiled(p: Path) -> bool:
    """Is this a compiled binary rather than a script?

    ``pip install lypning`` puts a console script named ``lypning`` on ``$PATH``,
    which is this package's own entry point. Take that for the Rust core and
    ``lypning -c PROG`` execs into itself, re-enters interpreter mode, and execs
    into itself again — a hang with no output, forever. The tell is the ``#!``:
    an engine is ELF (or Mach-O), never a text script.
    """
    try:
        with open(p, "rb") as fh:
            return fh.read(2) != b"#!"
    except OSError:
        return False


def _first_engine(candidates: Iterable[Path | None]) -> Path | None:
    for c in candidates:
        if c and c.is_file() and os.access(c, os.X_OK) and _is_compiled(c):
            return c.resolve()
    return None


def _env_path(name: str) -> Path | None:
    v = os.environ.get(name, "").strip()
    return Path(v).expanduser() if v else None


def _override(name: str, build_hint: str) -> Path | None:
    """``$NAME`` as an engine, or one line saying why it is not one.

    An override that silently falls back to discovery is the worst of both: the
    run measures a binary the caller did not name and reports the number as if
    it had.
    """
    p = _env_path(name)
    if p is None:
        return None
    if not p.is_file():
        raise EngineError("$%s points at %s, which does not exist — %s"
                          % (name, p, build_hint))
    if not os.access(p, os.X_OK):
        raise EngineError("$%s points at %s, which is not executable — chmod +x it"
                          % (name, p))
    if not _is_compiled(p):
        raise EngineError("$%s points at %s, which is a script, not a compiled engine "
                          "— %s" % (name, p, build_hint))
    return p.resolve()


def find_lypning() -> Path | None:
    """The Rust core: ``$LYPNING_BIN``, the state bin dir, a cargo target, PATH."""
    rust_target = paths.build_dir() / "rust" / "target"
    which = shutil.which(LYPNING)
    return _first_engine([
        _override(env_var_for(LYPNING), "point it at a `lypning build --rust` binary"),
        paths.bin_dir() / LYPNING,
        rust_target / "x86_64-unknown-linux-musl" / "release" / LYPNING,
        rust_target / "release" / LYPNING,
        Path(which) if which else None,
    ])


def find_micropython() -> Path | None:
    """The MicroPython variant: ``$LYPNING_MP_BIN``, state bin dir, build dir, PATH."""
    which = shutil.which(MICROPYTHON)
    return _first_engine([
        _override(env_var_for(MICROPYTHON), "point it at a `lypning build --micropython` binary"),
        paths.bin_dir() / MICROPYTHON,
        paths.build_dir() / "micropython" / "build" / MICROPYTHON,
        Path(which) if which else None,
    ])


def _is_shim(p: Path) -> bool:
    try:
        with open(p, "rb") as fh:
            head = fh.read(2048)
    except OSError:
        return False
    if head[:2] != b"#!":
        return False  # a real interpreter is a native image (ELF or Mach-O), not a script
    return SHIM_MARKER.encode() in head


def find_cpython() -> Path | None:
    """The real CPython, never the capture shim.

    ``$LYPNING_CPYTHON`` wins if set. Otherwise every ``python3`` on ``$PATH``
    is considered in order and the first one that is not one of our shims is
    taken; ``sys.executable`` is the last resort, which is correct because this
    package is itself running under a real interpreter.
    """
    explicit = _env_path("LYPNING_CPYTHON")
    if explicit is not None:
        if not explicit.is_file():
            raise EngineError("$LYPNING_CPYTHON points at %s, which does not exist "
                              "— unset it or name a real python3" % explicit)
        if not os.access(explicit, os.X_OK):
            raise EngineError("$LYPNING_CPYTHON points at %s, which is not executable "
                              "— chmod +x it" % explicit)
        return explicit.resolve()
    for name in ("python3", "python"):
        seen: set[str] = set()
        for d in os.environ.get("PATH", "").split(os.pathsep):
            if not d or d in seen:
                continue
            seen.add(d)
            cand = Path(d) / name
            if cand.is_file() and os.access(cand, os.X_OK) and not _is_shim(cand):
                return cand.resolve()
    import sys
    return Path(sys.executable).resolve() if sys.executable else None


def find_variant(engine: str) -> Path | None:
    """A Rust variant other than the unsuffixed core: its env pin, the state bin
    dir, then PATH — the order `main.rs` `engine_path_named` searches, so the
    two dispatchers find the same sibling or the same nothing."""
    which = shutil.which(engine)
    feature = "variant-" + engine[len(LYPNING) + 1:]
    root = paths.build_dir() / "rust" / "target" / feature
    return _first_engine([
        _override(env_var_for(engine), "point it at a `lypning build --variant` binary"),
        paths.bin_dir() / engine,
        root / "x86_64-unknown-linux-musl" / "release" / LYPNING,
        root / "release" / LYPNING,
        Path(which) if which else None,
    ])


def find(engine: str) -> Path | None:
    if engine == LYPNING:
        return find_lypning()
    if engine == MICROPYTHON:
        return find_micropython()
    if engine == CPYTHON:
        return find_cpython()
    if engine in SPECTRUM:
        return find_variant(engine)
    raise KeyError(engine)


def available() -> dict[str, Path | None]:
    return {e: find(e) for e in ENGINE_ORDER}


# --- running -----------------------------------------------------------------


@dataclass
class Result:
    """One execution. ``wall_ns`` covers spawn to reap, which is what costs."""

    engine: str
    binary: str
    returncode: int
    stdout: str
    stderr: str
    wall_ns: int
    timed_out: bool = False

    @property
    def wall_ms(self) -> float:
        return self.wall_ns / 1e6

    @property
    def unsupported(self) -> bool:
        """Exit 90 — the code a refusal uses. Not by itself a refusal: see
        :attr:`refused`, which is what a dispatcher must key on."""
        return self.returncode == UNSUPPORTED_EXIT

    @property
    def refused(self) -> bool:
        """Exit 90 **and** the contract line on stderr. The only reason to
        move to the next tier.

        Exit 90 alone is not enough, and the difference is not academic:
        ``python3 -c 'import sys; sys.exit(90)'`` is a program choosing its own
        exit code, and 90 is as available to it as any other number. A
        dispatcher that read that as "outside my subset" would run the program
        again on the next tier — and again on the one after — replaying every
        write it had already made. The contract line is what a *tier* says and a
        program does not, so it is what the fall-through is keyed on.
        """
        return self.unsupported and _REFUSAL_RE.search(self.stderr or "") is not None

    @property
    def refusal(self) -> tuple[str, str]:
        """``(kind, detail)`` parsed out of ``<engine>: unsupported: kind: detail``."""
        if not self.unsupported:
            return ("", "")
        for line in self.stderr.splitlines():
            marker = ": unsupported: "
            i = line.find(marker)
            if i == -1:
                continue
            rest = line[i + len(marker):]
            kind, _, detail = rest.partition(": ")
            return (kind.strip(), detail.strip())
        return ("", self.stderr.strip())


# --- the library arm ---------------------------------------------------------

#: One loaded library per path. Loading a shared object costs a `dlopen` and a
#: relocation pass, which is small but is not zero — and the whole argument for
#: the library is that a run costs a function call, so paying a load per run
#: would measure the wrong thing.
_LIBRARIES: dict[str, object] = {}

#: The library runs IN THIS PROCESS, so the cwd it sees is the process's own —
#: and `os.chdir` is process-wide, not per thread. The conformance battery gives
#: every run its own temp cwd and runs the arms in a thread pool, which those two
#: facts together make unsafe: two threads chdir-ing for two entries would each
#: run in the other's directory. The lock serialises the library arm alone; the
#: spawned arms pass `cwd=` to the child and are unaffected.
_CHDIR_LOCK = threading.Lock()


def find_library() -> Path | None:
    """The C ABI library, or ``None``. See :func:`lypning.embed.find_library`.

    Raises :class:`lypning.embed.LibraryError` for a ``$LYPNING_LIB`` that names
    nothing, exactly as :func:`_override` does for the engine binaries: a caller
    who named a path must be told it is wrong, not told to build what they have.
    """
    from . import embed
    return embed.find_library()


def library_ready() -> tuple[bool, str]:
    """``(usable, why_not)`` for the C ABI, without running a program.

    Asked before a battery starts, because "the library is stale" and "the
    library disagrees with CPython" are different facts and only one of them is
    a bug in the runtime. A library built before a symbol was added loads fine
    and then fails at the first call — reporting that as 800 MISMATCHes would
    bury the one line that says to rebuild it.
    """
    from . import embed
    try:
        path = embed.find_library()
        if path is None:
            return False, "not built — run `lypning build --lib`"
        embed.Library(path)
        return True, ""
    except embed.LibraryError as e:
        return False, str(e)


def run_library(
    program: str = "",
    *,
    argv_tail: Sequence[str] = (),
    stdin: str | None = None,
    cwd: Path | str | None = None,
    library: Path | None = None,
    step_limit: int = 0,
    env: dict[str, str] | None = None,
) -> Result:
    """Run ``program`` through the C ABI, in this process, and score it like a spawn.

    Returns the same :class:`Result` the spawned arms return, so every consumer —
    :func:`lypning.conformance.classify` above all — compares the library against
    CPython by exactly the rules it uses for a binary. That is the point: the
    library is only worth shipping if it is held to the same MISMATCH-is-a-bug
    standard as the tier it embeds.

    ``wall_ns`` here is the function call, not a spawn, which is why it is not
    comparable to the other arms' wall times without saying so out loud.
    """
    from . import embed
    try:
        path = Path(library) if library else embed.find_library()
        if path is None:
            return Result(LIBRARY, "", 127, "", "lypning: the C ABI is not built — run "
                                                "`lypning build --lib`\n", 0)
        key = str(path)
        lib = _LIBRARIES.get(key)
        if lib is None:
            lib = embed.Library(path)
            _LIBRARIES[key] = lib
    except embed.LibraryError as e:
        return Result(LIBRARY, "", 127, "", "lypning: %s\n" % e, 0)

    data = (stdin or "").encode("utf-8")
    saved_env = {k: os.environ.get(k) for k in (env or {})}
    with _CHDIR_LOCK:
        previous = os.getcwd()
        try:
            if cwd:
                os.chdir(str(cwd))
            # The spawned arms are handed an environment (`conformance._env_for`);
            # this one shares the caller's, because the program runs HERE. So the
            # same variables are set around the call and put back after — without
            # it, `os.environ['PYTHONHASHSEED']` answers differently in this arm
            # than in the CPython reference it is being compared against, which
            # is a MISMATCH reported against the runtime for a difference the
            # battery itself created.
            if env:
                os.environ.update(env)
            t0 = time.perf_counter_ns()
            try:
                out = lib.run(program, args=list(argv_tail), stdin=data, step_limit=step_limit)
            finally:
                wall = time.perf_counter_ns() - t0
        except OSError as e:
            # A cwd that cannot be entered is `run`'s 127, not an exception: a
            # battery that raised here would abandon the run with the repository
            # still holding a corpus program's writes.
            return Result(LIBRARY, str(path), 127, "", "lypning: %s\n" % e, 0)
        finally:
            try:
                os.chdir(previous)
            except OSError:
                pass
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    # `errors="replace"`, matching `run` above and for the same reason: the two
    # sides of a comparison must decode by identical rules or two different
    # non-ASCII outputs compare equal. `_as_text` adds the other half of that
    # agreement — `subprocess` translates newlines and a raw `decode` does not.
    return Result(
        LIBRARY, str(path), out.exit_code,
        _as_text(out.stdout), _as_text(out.stderr), wall,
    )


def _as_text(raw: bytes) -> str:
    """Decode exactly as ``subprocess.run(..., text=True)`` would.

    Two rules, and both must match or a comparison lies. ``errors="replace"``
    is the first. UNIVERSAL NEWLINES is the second and is the one that bites: a
    spawned arm's ``\r\n`` arrives as ``\n``, so a program printing a carriage
    return matched CPython through a subprocess and MISMATCHed through the
    library — a disagreement the battery invented rather than found.
    """
    return raw.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")


def _argv_for(engine: str, binary: Path, program: str, script: Path | None) -> list[str]:
    if script is not None:
        return [str(binary), str(script)]
    return [str(binary), "-c", program]


def _pool_socket(env: dict[str, str] | None = None) -> str:
    """The pool a CPython run should use, or "" for none.

    Opt-in and env-driven on purpose: a resident daemon is a deployment
    decision, not a default. ``LYPNING_POOL`` names the socket; the pool is
    used only for the CPython tier, only when it answers a ping, and any
    failure falls back to a cold spawn rather than failing the run.
    """
    source = env if env is not None else os.environ
    return source.get("LYPNING_POOL", "") or ""


def _run_via_pool(program: str, socket_path: str, *, argv_tail: Sequence[str] = (),
                  stdin: str | None = None, cwd: Path | str | None = None) -> Result | None:
    """Answer from the warm pool, or return None so the caller spawns instead."""
    from . import pool as _pool
    child_env = dict(os.environ)
    child_env["LYPNING_CAPTURE"] = "0"
    if env:
        child_env.update(env)
    t0 = time.perf_counter_ns()
    try:
        reply = _pool.Client(socket_path).run(program, cwd=cwd, argv_tail=argv_tail,
                                              stdin=stdin, env=child_env)
    except Exception:
        # A pool that is down, wedged or speaking nonsense must cost a caller
        # nothing but a cold spawn: this tier exists to be faster than CPython,
        # never to be a new way for CPython to be unavailable.
        return None
    if not reply.get("ok"):
        return None
    wall = time.perf_counter_ns() - t0
    # The spawned arms decode with `text=True`, which applies universal-newline
    # translation; the pool hands back exactly what the program wrote. Without
    # this the two Result kinds are not comparable and a CRLF-emitting program
    # (csv.writer, say) grades as a divergence against its own reference.
    def _universal(text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n")
    return Result(CPYTHON, "pool:" + socket_path, int(reply.get("returncode", 1)),
                  _universal(reply.get("stdout", "")),
                  _universal(reply.get("stderr", "")), wall)


def run(
    engine: str,
    program: str = "",
    *,
    binary: Path | None = None,
    argv_tail: Sequence[str] = (),
    stdin: str | None = None,
    cwd: Path | str | None = None,
    timeout: float | None = 30.0,
    script: Path | None = None,
    env: dict[str, str] | None = None,
    prefix: Sequence[str] = (),
) -> Result:
    """Run ``program`` on one engine. Never raises for a program's own failure.

    ``prefix`` goes between the binary and the program — ``("run",)`` turns the
    Rust core into its own dispatcher, which is how the `mixture-rust` arm runs.
    """
    if engine == CPYTHON and script is None:
        sock = _pool_socket(env)
        if sock:
            served = _run_via_pool(program, sock, argv_tail=argv_tail, stdin=stdin, cwd=cwd)
            if served is not None:
                return served
    b = binary or find(engine)
    if b is None:
        return Result(engine, "", 127, "", f"lypning: engine not built: {engine}\n", 0)
    cmd = _argv_for(engine, Path(b), program, script)
    cmd[1:1] = list(prefix)
    cmd.extend(argv_tail)
    full_env = dict(os.environ)
    # A nested capture would log the conformance run's own corpus back into the
    # corpus. Disable it for every engine invocation we make ourselves.
    full_env["LYPNING_CAPTURE"] = "0"
    if env:
        full_env.update(env)
    t0 = time.perf_counter_ns()
    try:
        proc = subprocess.run(
            cmd,
            input=stdin,
            capture_output=True,
            text=True,
            # Named, never inherited from the caller's locale. `text=True` alone
            # decodes with `locale.getpreferredencoding()`, so under `LC_ALL=C`
            # every non-ASCII byte becomes U+FFFD — and two engines printing
            # DIFFERENT non-ASCII (`é` against `ü`) then decode to the same
            # string of replacement characters and compare EQUAL. A third of the
            # corpus is non-ASCII, so that is a MISMATCH silently scored MATCH.
            # The children are run under `LC_ALL=C.UTF-8` (conformance._env_for);
            # this is the other half of that agreement.
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
            env=full_env,
            check=False,
        )
        wall = time.perf_counter_ns() - t0
        return Result(engine, str(b), proc.returncode, proc.stdout, proc.stderr, wall)
    except subprocess.TimeoutExpired as e:
        wall = time.perf_counter_ns() - t0
        out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        return Result(engine, str(b), 124, out, err, wall, timed_out=True)
    except (OSError, ValueError) as e:
        # ValueError is the NUL byte: argv cannot carry one, and a corpus
        # harvested from a shim's argv is exactly where one turns up. That is a
        # property of the program, so it comes back as a Result like every other
        # failure to run — a battery that raised here would abandon the run with
        # the repository still holding whatever the previous entries wrote.
        wall = time.perf_counter_ns() - t0
        return Result(engine, str(b), 127, "", f"lypning: cannot exec {b}: {e}\n", wall)


# --- routing -----------------------------------------------------------------


@dataclass
class Route:
    """What the classifier decided, and why."""

    engine: str
    kind: str = ""
    detail: str = ""
    #: What the program imports, as the parser saw it. Read again when a
    #: RUNTIME refusal falls onward: a tier that cannot import one of these was
    #: ruled out before the program ran and stays ruled out after it refuses.
    imports: tuple = ()
    #: Every rung's verdict on the program, ``(engine, kind, detail)`` in
    #: ``ENGINE_ORDER`` — kind ``""`` is "can run it". What the binary derived
    #: ``engine`` from, and what :func:`chain_after_refusal` walks: a sibling
    #: whose static verdict was "can run" is the next stop after a runtime
    #: refusal in a smaller variant.
    verdicts: tuple = ()

    def __str__(self) -> str:
        why = f"\t{self.kind}: {self.detail}" if self.kind else ""
        return f"{self.engine}{why}"


#: The kind :func:`route` reports when the binary names an engine this copy of
#: the table does not list. Never silent: the program still goes to CPython
#: (the safe direction), and the grader counts the route as ungraded.
ROUTE_UNKNOWN_ENGINE = "route-unknown-engine"


def _route_from_json(d: dict) -> Route:
    """A :class:`Route` from what ``route --json`` printed."""
    engine = str(d.get("engine") or CPYTHON)
    imports = tuple(str(m) for m in d.get("imports") or ())
    verdicts = tuple(
        (str(v.get("engine") or ""), str(v.get("kind") or ""), str(v.get("detail") or ""))
        for v in d.get("verdicts") or () if isinstance(v, dict))
    if engine not in ENGINE_ORDER:
        return Route(CPYTHON, ROUTE_UNKNOWN_ENGINE, engine, imports, verdicts)
    return Route(engine, str(d.get("kind") or ""), str(d.get("detail") or ""), imports, verdicts)


def route(program: str, *, binary: Path | None = None, timeout: float | None = 30.0,
          env: dict[str, str] | None = None) -> Route:
    """Ask the Rust front end which tier should run this.

    Routing is a static analysis over lypning's own parser, not a heuristic over
    the program text: the parser already reports the exact construct that would
    stop it, so asking it is an exact answer rather than a guess, and it costs
    one parse and no execution. With no lypning binary built there is nothing to
    ask, so everything routes to CPython — correct, just not cheap.

    Bounded and never raising, because every caller is already inside one of
    those two contracts: :func:`dispatch` was handed a ``timeout`` the caller
    expects to be honoured end to end, and the conformance battery runs this on
    a thread pool where one wedged parse would hang the whole run — and take the
    repository restore with it. A router that cannot answer is not fatal; it
    just means CPython, which is where an unroutable program was going anyway.
    """
    b = binary or find_lypning()
    if b is None:
        return Route(CPYTHON, "unbuilt", "no lypning binary")
    # `route` is a subcommand rather than a program, so the argv is built here
    # instead of going through run().
    try:
        proc = subprocess.run(
            [str(b), "route", "--json", "-c", program],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=timeout,
            env={**os.environ, "LYPNING_CAPTURE": "0", **(env or {})},
        )
    except subprocess.TimeoutExpired:
        return Route(CPYTHON, "route-failed", "the classifier did not answer within %gs" % timeout)
    except (OSError, ValueError) as e:
        return Route(CPYTHON, "route-failed", "cannot run %s: %s" % (b, e))
    line = proc.stdout.strip()
    if proc.returncode != 0 or not line:
        return Route(CPYTHON, "route-failed", proc.stderr.strip()[:200])
    try:
        d = json.loads(line)
    except ValueError:
        return Route(CPYTHON, "route-failed", "unreadable route: %s" % line[:200])
    return _route_from_json(d)


def chain_from(engine: str) -> list[str]:
    """The fallback chain starting at ``engine``, cheapest tier first."""
    try:
        i = ENGINE_ORDER.index(engine)
    except ValueError:
        i = len(ENGINE_ORDER) - 1
    return list(ENGINE_ORDER[i:])


#: Refusal kinds after which the chain jumps straight to CPython.
#:
#: Falling through assumes the next tier down is at least as correct as the one
#: that refused, and for most refusals it is: "I have no decorators" is a
#: capability gap, and MicroPython has decorators. But some refusals are not
#: about a missing feature at all. They say *CPython's behaviour here is subtle
#: and I decline to guess it* — and a second independent reimplementation is no
#: likelier to have replicated that subtlety than the first was. It is the
#: defining property of these constructs that reimplementations get them wrong.
#: That is why the refusal exists.
#:
#: For those, falling through does not cost a spawn. It converts a correct
#: refusal into a **silent wrong answer at exit 0**, which is the one outcome
#: the whole three-tier design exists to prevent.
#:
#: Measured over the corpus the run loaded (2,239 programs, 2026-08-28): tier 1
#: refuses 569 programs, and 25 of those are then answered *wrongly* by the tier
#: below.
#:
#: What each kind costs, on that corpus — programs tier 1 refuses with it, by
#: what lypning-mp then did. The right-hand column is the price: a program mp
#: would have answered correctly now pays a CPython spawn instead of a
#: MicroPython one. It is not zero, and an earlier revision of this comment said
#: it was:
#:
#:     nan-identity 0/2   percent-format 0/2   del 0/1   dict-view 0/1
#:     exception-chaining 0/1   json 0/1   set-method 0/1     (mp right / wrong)
#:     set-order 4/1      repr-unicode 1/1     int-div-precision 0/1
#:
#: So five programs get slower and nine wrong answers become right ones. The
#: trade is deliberately asymmetric in the same direction as invariant 1: a
#: spawn is milliseconds and a wrong answer is the thing the mixture exists to
#: prevent.
#:
#: A kind where MicroPython is *usually* right is deliberately NOT here, because
#: escalating it would pay that spawn on every occurrence. `bigint` was, and was
#: the reason this comment needed correcting: it names eleven refusals of which
#: MicroPython answers TEN correctly, since MicroPython has arbitrary-precision
#: integers and this is exactly the gap. Only the eleventh — int/int past 2**53,
#: where the quotient needs rounding neither engine can do — is a subtlety, and
#: it now carries its own kind, `int-div-precision`. Where a kind is mixed, split
#: it in the engine; do not escalate the whole of it from here.
#:
#: This is not the capability table and must not be edited like one. Adding a
#: kind here is a claim that no reimplementation short of CPython gets the
#: construct right; removing one is a claim that a wrong answer was acceptable.
#: `tests/test_routing.py` checks it against the battery in both directions.
ONLY_CPYTHON_REFUSALS = frozenset({
    "dunder-missing",     # mp builtins carry no __module__/__doc__; the getattr default wins
    "encoding",           # mp ignores every non-UTF-8 codec and answers the UTF-8 bytes
    "nan-identity",       # `in` and `==` decide by identity first: NaN finds itself
    "nan-order",          # a sort over a NaN is the algorithm's answer, and mp's differs
    "identity",           # `is` on equal immutables — mp's small-int boxing answers True
    "iterator-type-name",  # mp spells every iterator type `iterator`; CPython has a family
    "int-div-precision",  # int/int past 2**53 — mp converts to double and loses the low bits
    "set-order",          # CPython's hash order is observable, and it is CPython's
    "set-method",         # ...including hash(-1) == -2, reserved as an error sentinel
    "dict-view",          # keys/items are set-like, values compare by identity
    "exception-chaining",  # __context__/__cause__ do not exist one tier down
    "repr-unicode",       # repr() escapes a character set nothing else reproduces
    "percent-format",     # the '0' flag, grouping, and their interaction with '-'
    "del",                # the ValueError text of a failed list.remove/index
    "json",               # hooks, and control characters inside a string
    "random",             # mp's generator is not MT19937; a seeded stream there is a plausible wrong number
})


_MP_MODULES: "frozenset[str] | None" = None


def micropython_can_import(imports: Iterable[str]) -> bool:
    """Can lypning-mp import everything in ``imports``?

    The static router asks this before naming a tier; the dispatcher asks it
    again in :func:`chain_after_refusal`, or a program sent to tier 1 on its
    imports would fall onto a tier those imports had ruled out. Read from
    ``route.rs`` (through :mod:`lypning.routing`, the one reader of that table)
    so the two dispatchers cannot disagree; an unreadable table answers ``True``,
    which is the pre-existing behaviour and never narrower than the binary's own.
    """
    global _MP_MODULES
    if _MP_MODULES is None:
        from . import routing  # a cycle at import time, not at call time
        try:
            _MP_MODULES = frozenset(routing.micropython_modules())
        except Exception:
            _MP_MODULES = frozenset()
    if not _MP_MODULES:
        return True
    return all(m in _MP_MODULES for m in imports)


def chain_after_refusal(engine: str, kind: str, imports: Iterable[str] = (),
                        verdicts: Iterable[tuple] = ()) -> list[str]:
    """What is left of the chain once ``engine`` has refused with ``kind``.

    The rule `route.rs` spells in ``chain_after``, held to it by a cross-product
    test (`lypning route --next`): a kind in :data:`ONLY_CPYTHON_REFUSALS` rules
    out every reimplementation; otherwise each later Rust sibling whose STATIC
    verdict was "can run" (it already satisfied the imports and every static
    kind), then lypning-mp if it can import everything, then CPython.

    :mod:`lypning.routing` reads the same rule to grade a route, so the grader
    models the chain the dispatcher actually walks.
    """
    rest = chain_from(engine)[1:]
    if kind in ONLY_CPYTHON_REFUSALS:
        return [CPYTHON]
    out = [e for e in rest if e in SPECTRUM
           and any(v[0] == e and not v[1] for v in verdicts)]
    if MICROPYTHON in rest and micropython_can_import(imports):
        out.append(MICROPYTHON)
    out.append(CPYTHON)
    return out


@dataclass
class Dispatch:
    """The result of a dispatch, plus every tier that refused on the way."""

    result: Result
    route: Route
    attempts: list[Result] = field(default_factory=list)

    @property
    def engine(self) -> str:
        return self.result.engine


def dispatch(
    program: str,
    *,
    argv_tail: Sequence[str] = (),
    stdin: str | None = None,
    cwd: Path | str | None = None,
    timeout: float | None = 30.0,
    env: dict[str, str] | None = None,
) -> Dispatch:
    """Route, then run, falling through on a REFUSAL until a tier answers.

    A route is a prediction and predictions are wrong sometimes; the chain makes
    a wrong one cost one wasted spawn rather than a wrong answer.

    The chain moves on for :attr:`Result.refused` and for nothing else — exit 90
    *with* the contract line. Exit 90 on its own is a number the program chose
    (``sys.exit(90)``), and treating it as a refusal re-runs a program that has
    already done half its work, once per remaining tier.

    ``stdin`` is handed to **every** attempt, and supplying it is the caller's
    job. Leaving it ``None`` lets each engine inherit the caller's own stream,
    which the first tier then consumes — so a program that reads stdin and only
    afterwards hits a refusal gives the next tier an empty stream and the run
    prints nothing. :func:`lypning.cli._replayable_stdin` is what fills it in
    for ``lypning run``.
    """
    r = route(program, timeout=timeout, env=env)
    attempts: list[Result] = []
    last: Result | None = None
    remaining = chain_from(r.engine)
    while remaining:
        engine, remaining = remaining[0], remaining[1:]
        if find(engine) is None:
            continue
        res = run(engine, program, argv_tail=argv_tail, stdin=stdin, cwd=cwd,
                  timeout=timeout, env=env)
        last = res
        if not res.refused:
            return Dispatch(res, r, attempts)
        attempts.append(res)
        # The refusal says WHY, and some reasons rule out every tier but CPython.
        kind, _ = res.refusal
        remaining = [e for e in chain_after_refusal(engine, kind, r.imports, r.verdicts) if e in remaining]
    if last is None:
        last = Result(CPYTHON, "", 127, "", "lypning: no engine available\n", 0)
    return Dispatch(last, r, attempts[:-1] if attempts else [])


#: Constructs chosen to sit ON the refusal frontier, where the subset's answer
#: is most likely to change between builds. A drift probe is only as good as
#: its probes: these are not a proof of agreement, they are a smoke test that
#: has already caught one real drift (2026-08-31, a library a day behind its
#: binary answered dict-view set algebra the binary had learned to refuse).
DRIFT_PROBES: tuple[str, ...] = (
    'print(1)',
    'd={"a":1}; print(d.keys() | {"b"})',
    'print(" a b ".rsplit(None, 1))',
    'print({3,1,2})',
    'print(9007199254740993 / 3)',
    'print(1.5 // 0.5)',
    'import json; print(json.dumps({"a": 1}))',
)


def _verdict(res: "Result") -> tuple[str, str]:
    """Collapse a run to (class, detail) so two engines can be compared.

    A refusal compares by KIND, not by message: the detail after the kind is
    prose and may legitimately be reworded between builds. Everything else
    compares by exit class and stdout, because that is what a caller sees.
    """
    if res.refused:
        kind, _ = res.refusal
        return ("refused", kind)
    if res.returncode == 0:
        return ("ok", res.stdout)
    return ("failed", str(res.returncode))


def library_binary_drift(programs: Sequence[str] = DRIFT_PROBES,
                         *, timeout: float = 10.0) -> list[tuple[str, str, str]] | None:
    """Return the probes on which the C ABI and the spawned binary disagree.

    The two artifacts are built from one tree but installed independently, so
    ``lypning build --rust`` without ``--lib`` leaves a library that answers
    from an older subset. Nothing else in this package would notice: both pass
    their own contract assertion, both report the same version string, and the
    disagreement only shows up as a wrong answer in whichever arm a caller
    happens to use.

    Each element is ``(program, binary_verdict, library_verdict)``. An empty
    list means the probes agree; it does not mean the artifacts are identical.
    A host-linked library and a musl-static binary can legitimately differ in
    the last ULP of a libm result, so float-sensitive probes stay out of the
    default set.

    ``None`` means NOT MEASURED — the binary or a usable library is absent, so
    there was one artifact to ask. It is not an empty list on purpose: "agree"
    and "could not compare" are different facts, and a caller that rendered the
    second as the first would report a comparison nobody made (a hole, never a
    zero — the rule every unbuilt tier in this package follows).
    """
    if find(LYPNING) is None or run_library("").returncode == 127:
        return None
    out: list[tuple[str, str, str]] = []
    for program in programs:
        binary = run(LYPNING, program, timeout=timeout)
        library = run_library(program)
        b, l = _verdict(binary), _verdict(library)
        if b != l:
            out.append((program, "%s: %s" % b, "%s: %s" % l))
    return out
