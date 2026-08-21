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

#: Not a fourth engine — the same lypning, reached through the C ABI instead of
#: through a process, the way an embedding host reaches it. It is spelled
#: separately because it is a separate ARM to measure: the interpreter is
#: identical, the plumbing around it (`embed.rs`, `capi.rs`) is not, and a
#: conformance battery that never exercised the plumbing would prove nothing
#: about the artefact a harness actually links.
LIBRARY = "library"

ENGINE_ORDER = (LYPNING, MICROPYTHON, CPYTHON)

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
        _override("LYPNING_BIN", "point it at a `lypning build --rust` binary"),
        paths.bin_dir() / LYPNING,
        rust_target / "x86_64-unknown-linux-musl" / "release" / LYPNING,
        rust_target / "release" / LYPNING,
        Path(which) if which else None,
    ])


def find_micropython() -> Path | None:
    """The MicroPython variant: ``$LYPNING_MP_BIN``, state bin dir, build dir, PATH."""
    which = shutil.which(MICROPYTHON)
    return _first_engine([
        _override("LYPNING_MP_BIN", "point it at a `lypning build --micropython` binary"),
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
        return False  # a real interpreter is ELF, not a script
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


def find(engine: str) -> Path | None:
    return {
        LYPNING: find_lypning,
        MICROPYTHON: find_micropython,
        CPYTHON: find_cpython,
    }[engine]()


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
) -> Result:
    """Run ``program`` on one engine. Never raises for a program's own failure."""
    b = binary or find(engine)
    if b is None:
        return Result(engine, "", 127, "", f"lypning: engine not built: {engine}\n", 0)
    cmd = _argv_for(engine, Path(b), program, script)
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

    def __str__(self) -> str:
        why = f"\t{self.kind}: {self.detail}" if self.kind else ""
        return f"{self.engine}{why}"


def route(program: str, *, binary: Path | None = None, timeout: float | None = 30.0) -> Route:
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
            [str(b), "route", "-c", program],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=timeout,
            env={**os.environ, "LYPNING_CAPTURE": "0"},
        )
    except subprocess.TimeoutExpired:
        return Route(CPYTHON, "route-failed", "the classifier did not answer within %gs" % timeout)
    except (OSError, ValueError) as e:
        return Route(CPYTHON, "route-failed", "cannot run %s: %s" % (b, e))
    line = proc.stdout.strip()
    if proc.returncode != 0 or not line:
        return Route(CPYTHON, "route-failed", proc.stderr.strip()[:200])
    parts = line.split("\t")
    engine = parts[0].strip() or CPYTHON
    why = parts[1] if len(parts) > 1 else ""
    kind, _, detail = why.partition(": ")
    return Route(engine, kind.strip(), detail.strip())


def chain_from(engine: str) -> list[str]:
    """The fallback chain starting at ``engine``, cheapest tier first."""
    try:
        i = ENGINE_ORDER.index(engine)
    except ValueError:
        i = len(ENGINE_ORDER) - 1
    return list(ENGINE_ORDER[i:])


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

    ``env`` reaches EVERY tier the chain touches, and a caller that compares this
    result against another run must pass the same one it gave that run. The
    mixture is the arm where forgetting is invisible: it falls through to CPython
    for anything the cheap tiers refuse, so a caller grading it against a CPython
    reference is then comparing two CPythons started under different rules. That
    reads as an engine disagreeing with CPython when what actually differs is the
    hash seed — ``conformance._env_for`` pins ``PYTHONHASHSEED`` precisely so two
    CPythons agree before either is used as a reference, and a set-derived error
    message (``csv.DictWriter`` builds one by joining ``keys() - fieldnames``)
    then comes out in a different order about half the time. A MISMATCH that
    lands on one run in two is worse than a steady one: it trains a reader to
    re-run until the gate is green.

    Routing is exempt because it never executes the program — ``route`` is a
    static parse that already runs with capture disabled.
    """
    r = route(program, timeout=timeout)
    attempts: list[Result] = []
    last: Result | None = None
    for engine in chain_from(r.engine):
        if find(engine) is None:
            continue
        res = run(engine, program, argv_tail=argv_tail, stdin=stdin, cwd=cwd, timeout=timeout,
                  env=env)
        last = res
        if not res.refused:
            return Dispatch(res, r, attempts)
        attempts.append(res)
    if last is None:
        last = Result(CPYTHON, "", 127, "", "lypning: no engine available\n", 0)
    return Dispatch(last, r, attempts[:-1] if attempts else [])
