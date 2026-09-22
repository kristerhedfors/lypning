"""The four-arm benchmark, and the two ways a benchmark like this lies.

Four arms over the SAME corpus of real one-liners:

The mixture is the product; the other three are its components, and they are
here so that a claim about the mixture can be attributed to one of them.

The invariants this module exists to hold, each of which is a way to get the
measurement wrong that this project has already paid for:

**Arms are interleaved per entry, never run arm-at-a-time.** A machine that
warms, throttles or picks up a neighbour partway through a run charges the drift
to whichever arm was running at the time. Interleaved, it charges every arm
equally, which is all a comparison needs.

**The verdict is the MIN over repeats, not the mean.** Noise here is one-sided:
scheduling, page faults and neighbours can only ADD time. The minimum is the
least biased estimate of the true cost.

**Two totals, and they answer different questions.** ``shared_total_ms`` is over
the subset every arm ran — the only apples-to-apples comparison. ``total_ms`` is
over the whole corpus, which is what a session actually costs, and where an arm
that refuses work looks cheap for a reason that is not speed. Both are rendered
and both are labelled, because a total over different program sets is not a
comparison and quoting the wrong one is the easiest mistake this table invites.

**Print the corpus size you loaded, every time.** The capture harness grows the
corpus every session; this project's first published table said 420 programs and
was stale within the day. Never carry a remembered count.

**Not in CI.** A wall-clock benchmark on a shared runner measures the runner.
:func:`is_ci` exists so :func:`render` can say so in a banner rather than let a
number from a noisy box get quoted as a finding.

Two more instruments live here because they share that machinery — the same
interleave, the same per-entry temp cwd, the same absolute-path skip and the
same repository net — and because two tools that skipped different entries
would produce two totals nobody can read next to each other. They answer
questions the four-arm table does not:"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from . import conformance
from . import corpus
from . import engines
from . import paths

MIXTURE = conformance.MIXTURE
"""The dispatcher arm. Not an engine — it USES the other three.

Taken from :mod:`lypning.conformance` rather than spelled again here: the two
tools run the same corpus and their tables get read side by side, so an arm that
is named differently in each is an arm nobody can line up.
"""

ARM_ORDER: Tuple[str, ...] = (engines.CPYTHON,) + tuple(engines.SPECTRUM) + (MIXTURE,)

STARTUP_PROGRAM = "pass"
"""The empty program. Everything left is the interpreter arriving and leaving."""

DEFAULT_STARTUP_REPEAT = 5

_CI_VARS = (
    "CI", "CONTINUOUS_INTEGRATION", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE",
    "CIRCLECI", "TRAVIS", "JENKINS_URL", "TEAMCITY_VERSION", "TF_BUILD",
)


def is_ci() -> bool:
    """True on a shared runner, where a wall clock measures the runner.

    Checked by name rather than by heuristic: every CI worth naming sets one of
    these, and a false negative here only loses a warning banner.
    """
    for name in _CI_VARS:
        v = os.environ.get(name, "").strip().lower()
        if v and v not in ("0", "false", "no"):
            return True
    return False


# --- arms --------------------------------------------------------------------


@dataclass(frozen=True)
class Arm:
    """One thing to time: a binary, the argv that precedes ``-c``, and its env."""

    name: str
    binary: Path
    prefix: Tuple[str, ...] = ()
    env: Dict[str, str] = field(default_factory=dict)


def resolve_arms(names: Optional[Sequence[Union[str, Arm]]] = None) -> List[Arm]:
    """The arms that can actually be measured, in :data:`ARM_ORDER`.

    The mixture arm is the ``lypning`` binary with its ``run`` subcommand, and it
    is told where the other two engines are. That matters for CPython in
    particular: capture installs a shim named ``python3`` first on ``$PATH``, and
    a mixture arm that fell through to the shim would be timing a shell script."""
    want = tuple(names) if names else ARM_ORDER
    found = engines.available()
    out: List[Arm] = []
    for name in want:
        if isinstance(name, Arm):
            out.append(name)
            continue
        if name == MIXTURE:
            b = found.get(engines.LYPNING)
            if b is None:
                continue
            env: Dict[str, str] = {}
            if found.get(engines.CPYTHON):
                env[engines.env_var_for(engines.CPYTHON)] = str(found[engines.CPYTHON])
            for name in engines.SPECTRUM:
                if name != engines.LYPNING and found.get(name):
                    env[engines.env_var_for(name)] = str(found[name])
            out.append(Arm(MIXTURE, b, ("run",), env))
            continue
        b = found.get(name)
        if b is not None:
            out.append(Arm(name, Path(b)))
    return out


# --- one timed invocation ----------------------------------------------------

RAN = "ran"
REFUSED = "refused"
ERROR = "error"


@dataclass
class EntryTime:
    """The best sample for one (arm, entry) pair.

    ``outcome`` is the outcome of the sample that produced ``ms``, not of the
    last one taken: the number and the label have to describe the same run.

    Three outcomes, and the split is the one :mod:`lypning.engines` insists on.
    A ``refused`` is exit 90 — the arm did not run the program, so its time is
    not a time for that program, only for the refusal. An ``error`` is a spawn
    failure or a timeout, where the measurement itself did not happen. A
    program's own non-zero exit is ``ran``: ``assert`` and ``raise`` are answers,
    and the wall clock spent reaching them is real.
    """

    ms: float
    outcome: str
    returncode: int


def _child_env(arm: Arm, cwd: Optional[Path]) -> Dict[str, str]:
    """The environment every arm shares — the same one conformance runs under.

    ``LYPNING_CAPTURE=0`` and a ``LYPNING_LOG`` pointed into the throwaway cwd
    are belt and braces against the same accident: a bench run executes the
    whole corpus, and capturing that would fold the benchmark back into the
    corpus as observed evidence, ranking its own replays above what an agent
    actually typed (``conformance._env_for``).

    ``PYTHONHASHSEED`` and ``LC_ALL`` are pinned because they are free to pin and
    because a benchmark whose baseline arm varies with the ambient locale is
    measuring the shell it was started from.

    Built through :func:`lypning.engines.child_env`, which is where
    ``LYPNING_CAPTURE=0`` comes from and where the path-like variables are made
    absolute: every arm here is timed in a temp cwd, so a relative ``PYTHONPATH``
    would name a different directory in each of them (issue #57).
    """
    return engines.child_env({
        "LYPNING_LOG": str((cwd or Path(tempfile.gettempdir())) / "capture.jsonl"),
        "PYTHONHASHSEED": "0",
        "LC_ALL": "C.UTF-8",
        **arm.env,
    })


def time_one(
    arm: Arm,
    program: str,
    *,
    argv_tail: Sequence[str] = (),
    stdin: Optional[str] = None,
    cwd: Optional[Path] = None,
    timeout: float = 30.0,
) -> EntryTime:
    """Spawn to reap, in milliseconds. Never raises.

    Every arm goes through this one function. Timing three arms here and the
    fourth through a different code path would put the difference between the
    two paths into the fourth arm's number.

    The output is captured and thrown away: what is returned is a clock and an
    exit code. That is why ``text=True`` is still right here and is wrong in
    every grader (`engines.run`, `perf._capture`) — this function compares
    nothing, so it cannot be blinded by a decode. Do not start comparing
    ``proc.stdout`` here; the correctness half lives in `conformance` and
    `perf`, and it compares bytes (issue #50).
    """
    cmd = [str(arm.binary)]
    cmd.extend(arm.prefix)
    cmd.extend(["-c", program])
    cmd.extend(argv_tail)
    t0 = time.perf_counter_ns()
    try:
        proc = subprocess.run(
            cmd,
            # Never None: an inherited stdin lets a `sys.stdin.read()` one-liner
            # block on the terminal the benchmark was started from.
            input=stdin if stdin is not None else "",
            capture_output=True,
            text=True,
            # Named, not inherited from the caller's locale: the children run
            # under `LC_ALL=C.UTF-8` (`_child_env`), so the parent must decode
            # what they actually wrote. `text=True` alone decodes with
            # `locale.getpreferredencoding()`, which under `LC_ALL=C` turns
            # every non-ASCII byte into U+FFFD.
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
            env=_child_env(arm, cwd),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return EntryTime((time.perf_counter_ns() - t0) / 1e6, ERROR, 124)
    except OSError:
        return EntryTime((time.perf_counter_ns() - t0) / 1e6, ERROR, 127)
    ms = (time.perf_counter_ns() - t0) / 1e6
    if proc.returncode == engines.UNSUPPORTED_EXIT:
        return EntryTime(ms, REFUSED, proc.returncode)
    return EntryTime(ms, RAN, proc.returncode)


def _keep_best(current: Optional[EntryTime], sample: EntryTime) -> EntryTime:
    if current is None or sample.ms < current.ms:
        return sample
    return current


# --- startup -----------------------------------------------------------------


def startup(repeat: int = DEFAULT_STARTUP_REPEAT,
            arms: Optional[Sequence[Union[str, Arm]]] = None) -> Dict[str, float]:
    """``-c 'pass'`` on each arm, min of ``repeat``, arms interleaved.

    Returns milliseconds per arm name. An arm that is not built, or that could
    not run ``pass`` cleanly, is absent from the mapping rather than present
    with a number. That second case is the one worth guarding: a binary that
    fails to exec fails FAST, so a broken engine posts the best startup time in
    the table and reads as the winner.

    The interleave matters even here, where each sample is a millisecond: five
    consecutive spawns of one binary sit in a warmer page cache than the first
    spawn of the next one, and arm-at-a-time would hand that warmth to whichever
    arm went last.
    """
    resolved = resolve_arms(arms)
    if not resolved:
        return {}
    best: Dict[str, float] = {}
    tmp = tempfile.mkdtemp(prefix="lypning-bench-startup-")
    try:
        for _ in range(max(1, int(repeat))):
            for arm in resolved:
                s = time_one(arm, STARTUP_PROGRAM, cwd=Path(tmp), timeout=30.0)
                # `pass` exits 0 on every Python there is. Anything else means
                # the binary is not one, and its time is not a startup time.
                if s.outcome != RAN or s.returncode != 0:
                    continue
                prev = best.get(arm.name)
                if prev is None or s.ms < prev:
                    best[arm.name] = s.ms
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return best


# --- the repository net ------------------------------------------------------
#
# Both halves are :mod:`lypning.conformance`'s. The two tools run the same
# corpus for different reasons and must be dangerous in exactly the same ways:
# a bench that skipped a different set of entries, or restored a different set
# of files, would produce a table that cannot be read next to a conformance run.


@dataclass
class Damage:
    """What a corpus run left behind in the repository, and what was put back.

    A net, not a sandbox. The corpus is harvested from real agent sessions, so
    it is full of programs that rewrite ``src/`` and ``docs/``; the per-entry
    temp cwd contains every relative path and nothing else. The first
    measurement runs of the upstream project rewrote 34 tracked files, and the
    failure looked like "my change broke the suite" for a while before it looked
    like what it was.
    """

    paths: List[str] = field(default_factory=list)
    restored: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.paths)


def _report_damage(before: Optional[Any], root: Path, *, restore: bool = True) -> Damage:
    """Diff the bracket and undo what the corpus wrote.

    The bracket is conformance's snapshot, not a second implementation of one:
    this compared git's two-letter status codes, which cannot change when a file
    that was already `` M`` is modified again — so the bench's net was blind in
    exactly the state a bench is run in.
    """
    d = Damage()
    if before is None:
        return d
    try:
        collateral = conformance._collateral(root, before)
        if not collateral:
            return d
        d.paths = sorted(collateral)
        if restore:
            d.failed = list(conformance._restore(root, collateral, before))
            d.restored = [p for p in d.paths if p not in set(d.failed)]
        return d
    except Exception as e:  # noqa: BLE001
        # Called from a `finally`, so an exception raised here would REPLACE the
        # one the caller is already unwinding with — and lose it.
        d.failed = ["the repository could not be checked or restored: %s: %s"
                    % (type(e).__name__, e)]
        d.paths = d.paths or list(d.failed)
        return d
    finally:
        # The snapshot holds a copy of every file that was already dirty; it is
        # a temp directory, and every early return above is a leak without this.
        before.discard()


# --- the absolute-path skip --------------------------------------------------


def skip_reason(entry: corpus.Entry) -> str:
    """Why this entry is not timed, or ``""`` if it is.

    One rule, and it is conformance's: a program's text is fixed while the temp
    cwd is randomly named, so an absolute path in it is by construction a path
    *out* of the sandbox — three of the upstream corpus' seventeen could write.
    ``argv_tail`` gets the same treatment because the shim captures a shell
    redirect as an argv element and a program is free to open ``sys.argv[1]``.

    The entries this removes are removed from the corpus size too, which is why
    both counts are rendered: a subset silently smaller than the file on disk is
    the other way this table misleads.
    """
    if "\0" in entry.program or any("\0" in a for a in entry.argv_tail):
        # No argv can carry a NUL, so nothing can be spawned for this entry and
        # there is no time to take. Conformance's rule, again.
        return "NUL byte in the program or its argv: unspawnable"
    battery = conformance.spawns_a_battery(entry.program)
    if battery:
        # Timing a program that runs the whole battery would fork-bomb the host
        # and measure the fork bomb. Conformance's rule, again.
        return battery
    mutation = conformance.external_mutation(entry.program)
    if mutation:
        return mutation
    outside = conformance.absolute_paths(entry.program)
    for a in entry.argv_tail:
        outside.extend(p for p in conformance.absolute_paths(a) if p not in outside)
    if not outside:
        return ""
    return "absolute path outside the sandbox: %s" % outside[0]


# --- the corpus measurement --------------------------------------------------


@dataclass
class ArmResult:
    """One arm's whole-corpus result.

    ``total_ms`` counts every entry, refusals and all — a refusal still costs a
    spawn and the caller still waited for it. ``shared_total_ms`` counts only the
    entries EVERY arm ran. They are different questions and the second is the
    only fair comparison; see the module docstring.
    """

    arm: str
    ran: int = 0
    refused: int = 0
    errors: int = 0
    total_ms: float = 0.0
    shared_total_ms: float = 0.0
    per_entry: Dict[str, EntryTime] = field(default_factory=dict)

    @property
    def timed(self) -> int:
        return len(self.per_entry)

    @property
    def failed(self) -> int:
        """A program's own non-zero exit. An answer, not an error — see EntryTime."""
        return sum(1 for t in self.per_entry.values()
                   if t.outcome == RAN and t.returncode != 0)


@dataclass
class BenchReport:
    """Everything one benchmark run measured, with nothing derived away."""

    arms: Dict[str, ArmResult] = field(default_factory=dict)
    shared_ids: List[str] = field(default_factory=list)
    corpus_size: int = 0
    startup: Dict[str, float] = field(default_factory=dict)
    seconds: float = 0.0
    host: Dict[str, object] = field(default_factory=dict)
    # Not part of the comparison, but part of what was measured.
    repeat: int = 1
    startup_repeat: int = 0
    skipped: Dict[str, str] = field(default_factory=dict)
    damage: Damage = field(default_factory=Damage)

    @property
    def ci(self) -> bool:
        return bool(self.host.get("ci"))


def host_info(arms: Optional[Sequence[Arm]] = None) -> Dict[str, object]:
    """What the numbers below are numbers *about*.

    Engine sizes are in here because size is the cost model in the sandbox
    (``docs/LYPNING.md`` §8a): on a filesystem the two subsets are within noise
    of each other, and the byte count is what separates them once a device has
    to stream the binary in blocks.
    """
    found = engines.available()
    eng: Dict[str, object] = {}
    for name in engines.ENGINE_ORDER:
        p = found.get(name)
        if p is None:
            eng[name] = None
            continue
        try:
            size = Path(p).stat().st_size
        except OSError:
            size = 0
        eng[name] = {"path": str(p), "size": size}
    cpus = os.cpu_count() or 0
    # The 1-minute load average, and whether it exceeds the CPU count. Every
    # timing tool here is spawn-bound, so on a host that is oversubscribed the
    # reading is the scheduler's queue, not the change under test — one session
    # spent an evening quoting 28x "regressions" that were a load average of
    # 340 from a dozen concurrent batteries. Recorded so the header can say so
    # and the baseline verdict can refuse to call it a measurement.
    try:
        load1 = round(os.getloadavg()[0], 1)
    except (AttributeError, OSError):
        load1 = None
    return {
        "cpu_count": cpus,
        "kernel": "{0} {1}".format(platform.system(), platform.release()),
        "machine": platform.machine(),
        "engines": eng,
        "ci": is_ci(),
        "load": load1,
        "loaded": bool(load1 is not None and cpus and load1 > cpus),
    }


def host_load_line(host: Dict[str, object]) -> str:
    """The load fragment for a header, and the warning when it disqualifies the run."""
    load = host.get("load")
    frag = ", load %s" % load if load is not None else ""
    if host.get("loaded"):
        frag += ("\n  ! host loaded: load %s over %s cpus — a spawn-bound timing here "
                 "measures the scheduler, not the change; do not quote it"
                 % (load, host.get("cpu_count", "?")))
    return frag


def corpus_time(
    entries: Optional[Sequence[corpus.Entry]] = None,
    *,
    repeat: int = 1,
    limit: Optional[int] = None,
    arms: Optional[Sequence[Union[str, Arm]]] = None,
    timeout: float = 30.0,
    progress: Optional[Callable[[int, int, corpus.Entry], None]] = None,
) -> BenchReport:
    """Every harvested program on every available arm, arms interleaved.

    ``progress`` is called after each entry finishes all arms, as
    ``progress(done, total, entry)`` where ``total`` is ``repeat * len(entries)``.

    ``report.startup`` is left empty: startup is a separate measurement and
    running it from here would double the cost of a corpus run without saying so.
    :func:`bench` does both; a caller that wants both may also just assign it.
    """
    t_start = time.perf_counter()
    es = list(entries) if entries is not None else corpus.load_default()
    if limit:
        es = es[: int(limit)]
    resolved = resolve_arms(arms)

    skipped: Dict[str, str] = {}
    runnable: List[corpus.Entry] = []
    for e in es:
        why = skip_reason(e)
        if why:
            skipped[e.id] = why
        else:
            runnable.append(e)

    root = paths.project_dir()
    before = conformance._snapshot(root)

    results = {a.name: ArmResult(a.name) for a in resolved}
    reps = max(1, int(repeat))
    total_units = reps * len(runnable)
    done = 0
    # The net closes on every path out of the loop, the ones that leave by
    # raising included — a `KeyboardInterrupt` on a long run is the ordinary way
    # a benchmark ends, and it is the moment the tree is most likely to be
    # holding a corpus program's writes (conformance.close_net).
    try:
        for _ in range(reps):
            for entry in runnable:
                tail = list(entry.argv_tail)
                stdin = entry.stdin_sample
                for arm in resolved:
                    # A fresh cwd per (arm, entry): corpus programs write files,
                    # and without isolation one arm reads back what the previous
                    # one just created and times a different program than the
                    # others did.
                    cwd = tempfile.mkdtemp(prefix="lypning-bench-")
                    try:
                        s = time_one(
                            arm, entry.program, argv_tail=tail, stdin=stdin,
                            cwd=Path(cwd), timeout=timeout,
                        )
                    finally:
                        shutil.rmtree(cwd, ignore_errors=True)
                    per = results[arm.name].per_entry
                    per[entry.id] = _keep_best(per.get(entry.id), s)
                done += 1
                if progress is not None:
                    progress(done, total_units, entry)
    finally:
        damage = _report_damage(before, root)

    # The shared subset: entries every available arm actually executed. With no
    # arms there is nothing shared, which is different from everything shared.
    shared: List[str] = []
    if resolved:
        for e in runnable:
            if all(results[a.name].per_entry.get(e.id, EntryTime(0.0, ERROR, 0)).outcome == RAN
                   for a in resolved):
                shared.append(e.id)

    for r in results.values():
        for t in r.per_entry.values():
            if t.outcome == RAN:
                r.ran += 1
            elif t.outcome == REFUSED:
                r.refused += 1
            else:
                r.errors += 1
            r.total_ms += t.ms
        r.shared_total_ms = sum(r.per_entry[i].ms for i in shared if i in r.per_entry)

    return BenchReport(
        arms=results,
        shared_ids=shared,
        corpus_size=len(runnable),
        startup={},
        seconds=time.perf_counter() - t_start,
        host=host_info(resolved),
        repeat=reps,
        skipped=skipped,
        damage=damage,
    )


def bench(
    *,
    startup_repeat: int = DEFAULT_STARTUP_REPEAT,
    repeat: int = 1,
    limit: Optional[int] = None,
    arms: Optional[Sequence[Union[str, Arm]]] = None,
    timeout: float = 30.0,
    progress: Optional[Callable[[int, int, corpus.Entry], None]] = None,
) -> BenchReport:
    """Both measurements, in the order the table prints them.

    Startup first and deliberately: it is the cheap one, so a run that is going
    to be abandoned because the numbers look wrong is abandoned early.
    """
    t0 = time.perf_counter()
    su = startup(startup_repeat, arms)
    report = corpus_time(repeat=repeat, limit=limit, arms=arms, timeout=timeout, progress=progress)
    report.startup = su
    report.startup_repeat = max(1, int(startup_repeat))
    report.seconds = time.perf_counter() - t0
    return report


# --- rendering ---------------------------------------------------------------

_CI_BANNER = (
    "!! CI DETECTED — a wall-clock benchmark on a shared runner measures the\n"
    "!! runner. These numbers are not a finding. Upstream keeps this out of CI\n"
    "!! on purpose and keeps the deterministic half (conformance, routing)."
)


def _ms(v: Optional[float], width: int = 10, digits: int = 2) -> str:
    if v is None:
        return "—".rjust(width)
    return ("{0:.%df}" % digits).format(v).rjust(width)


def _rel(v: Optional[float], base: Optional[float]) -> str:
    if not base or v is None:
        return ""
    return "{0:.3f}x".format(v / base)


def _size(n: object) -> str:
    return "{0:,} B".format(n) if isinstance(n, int) else "?"


def render(report: BenchReport) -> str:
    """The table, plus every caveat that keeps a row from being misquoted."""
    out: List[str] = []
    if report.ci:
        out.append(_CI_BANNER)
        out.append("")

    host = report.host
    out.append("host: {0} cpus, {1} ({2}){3}".format(
        host.get("cpu_count", "?"), host.get("kernel", "?"), host.get("machine", "?"),
        host_load_line(host)))
    eng = host.get("engines") or {}
    if isinstance(eng, dict):
        for name in engines.ENGINE_ORDER:
            info = eng.get(name)
            if isinstance(info, dict):
                out.append("  {0:<12} {1:>12}  {2}".format(name, _size(info.get("size")), info.get("path")))
            else:
                out.append("  {0:<12} {1:>12}".format(name, "not built"))
    out.append("")

    # "Not built" and "built but did not answer" are different failures and the
    # table has to say which: an arm that is present and silent is a bug, an arm
    # that is absent is a build step nobody ran.
    built = set()
    if isinstance(eng, dict):
        built = {n for n, i in eng.items() if isinstance(i, dict)}
        if engines.LYPNING in built:
            built.add(MIXTURE)

    known = [a for a in ARM_ORDER]
    for extra in list(report.startup) + list(report.arms):
        if extra not in known:
            known.append(extra)

    if report.startup:
        base = report.startup.get(engines.CPYTHON)
        out.append("startup — `-c 'pass'`, min of {0}, arms interleaved".format(
            report.startup_repeat or DEFAULT_STARTUP_REPEAT))
        out.append("")
        out.append("arm             min ms   vs cpython")
        for name in known:
            v = report.startup.get(name)
            if v is None:
                why = "(did not run `pass`)" if name in built else "(not built)"
                out.append("{0:<12}{1}   {2}".format(name, _ms(None), why))
            else:
                out.append("{0:<12}{1}   {2}".format(name, _ms(v), _rel(v, base)))
        out.append("")

    if not report.arms:
        out.append("corpus — no arm was available to measure. Build one: `lypning build`.")
        return "\n".join(out) + "\n"

    # The count is printed every time and is never a remembered number: the
    # capture harness grows the corpus every session.
    loaded = report.corpus_size + len(report.skipped)
    out.append("corpus — {0} programs loaded, {1} measured, min of {2}, arms interleaved "
               "per entry".format(loaded, report.corpus_size, report.repeat))
    if report.skipped:
        out.append("  {0} skipped: they name an absolute path, which the per-entry temp cwd "
                   "does not contain".format(len(report.skipped)))
    out.append("shared subset (every arm executed it): {0} programs".format(len(report.shared_ids)))
    out.append("")
    out.append("arm          ran  refused  errors   shared total    median   vs cpython")
    cp = report.arms.get(engines.CPYTHON)
    base_shared = cp.shared_total_ms if cp else None
    for name in known:
        r = report.arms.get(name)
        if r is None:
            out.append("{0:<12}{1:>5}{2:>9}{3:>8}{4}      {5}".format(
                name, "—", "—", "—", _ms(None, 15, 1), "(not built)"))
            continue
        med = _median([r.per_entry[i].ms for i in report.shared_ids if i in r.per_entry])
        out.append("{0:<12}{1:>5}{2:>9}{3:>8}{4} ms{5}    {6}".format(
            name, r.ran, r.refused, r.errors, _ms(r.shared_total_ms, 15, 1),
            _ms(med, 9), _rel(r.shared_total_ms, base_shared)))
    out.append("")
    out.append("  ^ the SHARED subset — the only apples-to-apples comparison, because a")
    out.append("    total over different program sets is not a comparison.")
    if not report.shared_ids:
        out.append("    It is EMPTY here: some arm ran nothing, so there is no comparison to")
        out.append("    make. Read the whole-corpus table below and nothing else.")
    out.append("")

    out.append("whole corpus — what a session of {0} one-liners actually costs".format(
        report.corpus_size))
    out.append("(a refusal still costs its spawn, so this counts every entry for every arm)")
    out.append("")
    out.append("arm             total     vs cpython")
    base_total = cp.total_ms if cp else None
    for name in known:
        r = report.arms.get(name)
        if r is None:
            out.append("{0:<12}{1}      {2}".format(name, _ms(None, 10, 1), "(not built)"))
            continue
        note = ""
        unanswered = r.refused + r.errors
        if unanswered:
            note = "   ({0} unanswered — cheaper because it REFUSES, not because it is faster)".format(
                unanswered)
        out.append("{0:<12}{1} ms      {2}{3}".format(
            name, _ms(r.total_ms, 10, 1), _rel(r.total_ms, base_total), note))

    mix = report.arms.get(MIXTURE)
    if mix and cp and cp.total_ms:
        saved = cp.total_ms - mix.total_ms
        out.append("")
        out.append(
            "mixture vs cpython over the whole corpus: {0} {1:.1f} ms across {2} programs "
            "({3:.1f}%), with {4} unanswered.".format(
                "saves" if saved >= 0 else "costs", abs(saved), report.corpus_size,
                100.0 * saved / cp.total_ms, mix.refused + mix.errors))

    damage = _damage_lines(report.damage)
    if damage:
        out.append("")
        out.extend(damage)

    out.append("")
    out.append("{0:.1f} s of wall clock.".format(report.seconds))
    return "\n".join(line.rstrip() for line in out) + "\n"


def _median(xs: Sequence[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


# --- corpus-time: one binary, the whole corpus -------------------------------
#
# A different question from the table above, and this project has already paid
# for confusing the two. `bench` compares ARMS — what does the mixture cost
# against CPython. This compares RUNS of one binary: did the change I just made
# make the programs lypning is actually asked to run faster. A microbenchmark
# loops twenty thousand times; a corpus entry runs once and exits, so its cost
# is startup, parse and compile rather than steady-state dispatch. Optimising
# against the first distribution and shipping to the second is how a speed pass
# produces numbers nobody can feel: docs/BENCH-LEDGER.md (the entry dated
# 2026-09-05) records a synthetic workload that said computed-goto was worth
# 48 ms per program where the corpus said 0.14 ms.
#
# So this is the instrument a speed change is ACCEPTED on, and `bench` is the
# diagnostic that says where the time went.
#
# Everything it measures goes through :func:`corpus_time` rather than a second
# loop of its own — same interleave, same per-entry temp cwd, same absolute-path
# skip, same repository net. Two instruments that skipped different entries
# would produce two totals nobody can read next to each other.

#: The record format `--record` writes and `--baseline` reads. Versioned because
#: a baseline outlives the tree that produced it: a file from an older schema
#: must be refused by name, not silently half-read into a comparison.
RECORD_SCHEMA = "lypning-corpus-time/1"

DEFAULT_CORPUS_REPEAT = 3


@dataclass
class CorpusTiming:
    """One binary's time over the whole corpus, entry by entry.

    ``entries`` holds every timed program including the refusals: exit 90 costs
    a spawn and a parse, which is real time the agent waited for. They are
    counted separately all the same, because a change that moves an entry
    between supported and unsupported changes *what is being timed*, and that
    has to be visible rather than absorbed into a total.
    """

    engine: str
    binary: str
    entries: Dict[str, EntryTime] = field(default_factory=dict)
    loaded: int = 0
    repeat: int = 1
    seconds: float = 0.0
    binary_bytes: int = 0
    recorded: str = ""
    skipped: Dict[str, str] = field(default_factory=dict)
    damage: Damage = field(default_factory=Damage)
    host: Dict[str, object] = field(default_factory=dict)

    @property
    def timed(self) -> int:
        return len(self.entries)

    @property
    def total_ms(self) -> float:
        return sum(e.ms for e in self.entries.values())

    @property
    def refused(self) -> int:
        return sum(1 for e in self.entries.values() if e.outcome == REFUSED)

    @property
    def failed(self) -> int:
        """A program's own non-zero exit. An answer, not an error — see EntryTime."""
        return sum(1 for e in self.entries.values() if e.outcome == RAN and e.returncode != 0)

    @property
    def timeouts(self) -> int:
        return sum(1 for e in self.entries.values() if e.outcome == ERROR and e.returncode == 124)

    @property
    def errors(self) -> int:
        return sum(1 for e in self.entries.values() if e.outcome == ERROR)


def arm_for(spec: Union[str, Arm, Path]) -> Optional[Arm]:
    """An arm from an engine name, or from a path to a binary. ``None`` if absent.

    A path is accepted because the acceptance question is usually asked about a
    binary that is not installed yet — the candidate in ``target/release`` next
    to the one in the bin dir — and requiring an install first would mean
    measuring after committing to it. The rule for telling the two apart is the
    same one the front door uses for a script: a path is anything that exists,
    or that carries a separator.

    It is resolved to an absolute path here, and that is load-bearing rather
    than tidy: every entry runs in its own temp cwd, so a relative binary path
    would resolve against that directory and vanish.
    """
    if isinstance(spec, Arm):
        return spec
    text = str(spec)
    p = Path(text).expanduser()
    if os.sep in text or p.is_file():
        if not p.is_file():
            return None
        return Arm(p.name, p.resolve())
    resolved = resolve_arms([text])
    return resolved[0] if resolved else None


def corpus_time_one(
    spec: Union[str, Arm, Path] = engines.LYPNING,
    *,
    entries: Optional[Sequence[corpus.Entry]] = None,
    repeat: int = DEFAULT_CORPUS_REPEAT,
    limit: Optional[int] = None,
    timeout: float = 30.0,
    progress: Optional[Callable[[int, int, corpus.Entry], None]] = None,
) -> Optional[CorpusTiming]:
    """Time every corpus program on ONE binary. ``None`` when it is not built."""
    arm = arm_for(spec)
    if arm is None:
        return None
    report = corpus_time(entries, repeat=repeat, limit=limit, arms=[arm],
                         timeout=timeout, progress=progress)
    result = report.arms.get(arm.name)
    try:
        size = Path(arm.binary).stat().st_size
    except OSError:
        size = 0
    return CorpusTiming(
        engine=arm.name,
        binary=str(arm.binary),
        entries=dict(result.per_entry) if result else {},
        # Loaded, not measured: the skipped entries are part of the count the
        # corpus grew to, and printing only the measured one would quietly
        # shrink the corpus every time an entry gained an absolute path.
        loaded=report.corpus_size + len(report.skipped),
        repeat=report.repeat,
        seconds=report.seconds,
        binary_bytes=size,
        recorded=_now(),
        skipped=report.skipped,
        damage=report.damage,
        host=report.host,
    )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def timing_record(timing: CorpusTiming) -> Dict[str, Any]:
    """The JSON ``--record`` writes: the totals, and every entry's own time.

    Per-entry times are kept rather than summarised because the total alone
    cannot tell a real 3% win from three entries that stopped being executed at
    all. The diff needs both sides entry by entry to say which of those it is.
    """
    return {
        "schema": RECORD_SCHEMA,
        "recorded": timing.recorded or _now(),
        "engine": timing.engine,
        "binary": timing.binary,
        "binary_bytes": timing.binary_bytes,
        "corpus_loaded": timing.loaded,
        "timed": timing.timed,
        "skipped": len(timing.skipped),
        "repeat": timing.repeat,
        "seconds": timing.seconds,
        "total_ms": timing.total_ms,
        "refused": timing.refused,
        "failed": timing.failed,
        "timeouts": timing.timeouts,
        "host": timing.host,
        "entries": {
            i: {"ms": round(e.ms, 4), "outcome": e.outcome, "rc": e.returncode}
            for i, e in sorted(timing.entries.items())
        },
    }


def check_record_target(path: Union[Path, str]) -> Path:
    """Refuse an unwritable ``--record`` target BEFORE the measurement runs.

    Same reasoning as reading a ``--baseline`` up front: timing the whole corpus
    and *then* discovering the destination cannot be written throws the run
    away, and the run is the expensive part. Raises ``ValueError`` with the fix
    in the sentence; returns the resolved path.
    """
    p = Path(path).expanduser()
    if p.is_dir():
        raise ValueError("%s is a directory — --record names the FILE to write" % p)
    anc = p.parent
    while not anc.exists() and anc != anc.parent:
        anc = anc.parent
    if not anc.is_dir():
        raise ValueError("cannot write %s: %s is a file, not a directory — --record needs a "
                         "path under a real directory" % (p, anc))
    if not os.access(str(anc), os.W_OK | os.X_OK):
        raise ValueError("cannot write %s: %s is not writable — name a --record path you own"
                         % (p, anc))
    return p


def write_record(timing: CorpusTiming, path: Union[Path, str]) -> Path:
    """Write the record. Returns the path; prints nothing.

    Raises ``ValueError`` rather than letting an ``OSError`` out: the CLI turns
    a ``ValueError`` into one line naming the fix, and an unwritable path is a
    caller mistake, not a bug worth a traceback.
    """
    p = Path(path).expanduser()
    try:
        paths.ensure_dir(p.parent)
        p.write_text(json.dumps(timing_record(timing), indent=2, sort_keys=True) + "\n",
                     encoding="utf-8")
    except OSError as e:
        raise ValueError("cannot write %s: %s — name a --record path you own"
                         % (p, e.strerror or e))
    return p


def load_record(path: Union[Path, str]) -> Dict[str, Any]:
    """Read a baseline. Raises ``ValueError`` for anything that is not one.

    Refused by name rather than half-read: a baseline is compared entry by
    entry, and a file whose ``entries`` are missing or shaped differently would
    produce a diff over an empty intersection — which renders as "no change".
    """
    p = Path(path).expanduser()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError as e:
        raise ValueError("cannot read %s: %s — write one with "
                         "`lypning corpus-time --record %s`" % (p, e.strerror or e, p))
    except ValueError as e:
        raise ValueError("%s is not JSON: %s — a baseline is written by "
                         "`lypning corpus-time --record`, not by hand" % (p, e))
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        raise ValueError("%s is not a corpus-time record (no `entries` map) — write one with "
                         "`lypning corpus-time --record %s`" % (p, p))
    schema = data.get("schema", "")
    if schema != RECORD_SCHEMA:
        raise ValueError("%s was written by schema %r, this build reads %r — re-record it"
                         % (p, schema or "none", RECORD_SCHEMA))
    return data


def _status(outcome: str, rc: Any) -> str:
    return "%s:%s" % (outcome, rc)


@dataclass
class TimingDiff:
    """Two runs, compared over the entries BOTH of them timed.

    ``shared`` is not a detail. The capture harness grows the corpus every
    session, so a baseline recorded last week covers a different set of programs
    than a run today, and two totals over different program sets are not a
    comparison — the same trap the shared-subset total exists for in
    :class:`BenchReport`. ``added`` and ``dropped`` say how far apart the two
    sets were so the reader can judge the intersection.
    """

    baseline_path: str = ""
    baseline_engine: str = ""
    baseline_recorded: str = ""
    baseline_loaded: int = 0
    shared: int = 0
    added: int = 0
    dropped: int = 0
    before_ms: float = 0.0
    after_ms: float = 0.0
    movers: List[Tuple[str, float, float]] = field(default_factory=list)
    flips: List[Tuple[str, str, str]] = field(default_factory=list)

    @property
    def delta_ms(self) -> float:
        return self.after_ms - self.before_ms

    @property
    def ratio(self) -> Optional[float]:
        return (self.after_ms / self.before_ms) if self.before_ms else None


def diff_record(baseline: Dict[str, Any], timing: CorpusTiming, *, top: int = 12) -> TimingDiff:
    """Baseline against this run. The verdict is the total; the movers are a pointer.

    A change worth shipping moves the total. Per-entry deltas are noisy at the
    millisecond scale a one-liner lives at, so they are ranked and shown as
    where to look next, never as the result.

    Status flips are the thing that silently invalidates the whole comparison:
    an entry that started exiting 90 got cheaper by losing a capability, and its
    time is no longer a time for the same work.
    """
    old = baseline.get("entries") or {}
    d = TimingDiff(
        baseline_engine=str(baseline.get("engine", "")),
        baseline_recorded=str(baseline.get("recorded", "")),
        baseline_loaded=int(baseline.get("corpus_loaded") or 0),
    )
    shared = [i for i in timing.entries if i in old]
    d.shared = len(shared)
    d.added = len(timing.entries) - d.shared
    d.dropped = len(old) - d.shared
    movers: List[Tuple[str, float, float]] = []
    for i in shared:
        rec = old[i] or {}
        try:
            before = float(rec.get("ms") or 0.0)
        except (TypeError, ValueError):
            continue
        after = timing.entries[i].ms
        d.before_ms += before
        d.after_ms += after
        movers.append((i, before, after))
        was = _status(str(rec.get("outcome", "")), rec.get("rc"))
        now = _status(timing.entries[i].outcome, timing.entries[i].returncode)
        if was != now:
            d.flips.append((i, was, now))
    movers.sort(key=lambda m: abs(m[2] - m[1]), reverse=True)
    d.movers = movers[: max(0, int(top))]
    return d


def _damage_lines(damage: Damage) -> List[str]:
    """The net's report. Empty when the corpus wrote nothing outside its temp cwd."""
    if not damage:
        return []
    n = len(damage.paths)
    out = ["!! %d repository file%s changed by corpus programs:" % (n, "" if n == 1 else "s")]
    out.extend("     %s" % p for p in damage.paths[:20])
    if n > 20:
        out.append("     … and %d more" % (n - 20))
    if damage.restored:
        out.append("   restored %d — but a run that damaged the tree at all is a run whose "
                   "numbers were taken across a moving target." % len(damage.restored))
    if damage.failed:
        out.append("   COULD NOT RESTORE: %s" % ", ".join(damage.failed[:10]))
    return out


def render_corpus_time(timing: Optional[CorpusTiming],
                       diff: Optional[TimingDiff] = None) -> str:
    """The total, and the biggest movers when there is a baseline to move from.

    Deliberately not a per-entry table: 763 rows invites reading noise as
    signal. The total is the number a session should quote — with the corpus
    size printed beside it, every time, because that count grew twice while this
    package was being written.
    """
    out: List[str] = []
    if is_ci():
        out.append(_CI_BANNER)
        out.append("")
    if timing is None:
        return ("corpus-time — nothing to measure: that binary is not built.\n"
                "Build one (`lypning build`) or name a path with --engine.\n")

    out.append("corpus-time — %s   %s" % (timing.engine, timing.binary))
    host = timing.host or {}
    out.append("host: %s cpus, %s (%s)%s" % (host.get("cpu_count", "?"),
                                             host.get("kernel", "?"), host.get("machine", "?"),
                                             host_load_line(host)))
    out.append("")
    out.append("%d programs loaded, %d timed, min of %d, one temp cwd per entry" % (
        timing.loaded, timing.timed, timing.repeat))
    if timing.skipped:
        out.append("  %d skipped: they name an absolute path, which the per-entry temp cwd "
                   "does not contain" % len(timing.skipped))
    out.append("")
    out.append("  total   %s over %d programs   (%d exit-90, %d nonzero, %d timeout)" % (
        _fmt_ms(timing.total_ms), timing.timed, timing.refused, timing.failed, timing.timeouts))
    med = _median([e.ms for e in timing.entries.values()])
    if med is not None:
        out.append("  median  %s per program" % _fmt_ms(med))
    out.append("")
    out.append("  exit-90 entries are TIMED, not skipped: a refusal costs a spawn and a parse,")
    out.append("  and the agent waited for it. They are counted apart because a change that")
    out.append("  moves an entry in or out of the subset changes what is being timed.")

    if diff is not None:
        out.append("")
        out.append("baseline: %s   %s, recorded %s" % (
            diff.baseline_path or "?", diff.baseline_engine or "?",
            diff.baseline_recorded or "?"))
        if diff.baseline_engine and diff.baseline_engine != timing.engine:
            out.append("  !! the baseline is a DIFFERENT arm (%s against %s) — this is an "
                       "arm comparison wearing a speed change's clothes." % (
                           diff.baseline_engine, timing.engine))
        out.append("  compared over the %d programs both runs timed%s" % (
            diff.shared,
            "" if not (diff.added or diff.dropped)
            else " (%d new here, %d only in the baseline — the corpus moved under the "
                 "comparison, so the totals below are over the intersection ONLY)"
                 % (diff.added, diff.dropped)))
        if not diff.shared:
            out.append("  nothing is shared: there is no comparison to make.")
            return "\n".join(line.rstrip() for line in out) + "\n"
        ratio = diff.ratio
        verdict = "no change"
        if ratio is not None:
            verdict = "FASTER" if ratio < 0.995 else ("SLOWER" if ratio > 1.005 else "no change")
        if (timing.host or {}).get("loaded"):
            # The number is printed — hiding it would be its own lie — but it
            # is not a reading and must not be recorded as one.
            verdict += " — UNRELIABLE, host loaded"
        out.append("  before %s  ->  after %s   %s   %s (%s on the total)" % (
            _fmt_ms(diff.before_ms), _fmt_ms(diff.after_ms),
            "%.3fx" % ratio if ratio is not None else "—",
            verdict,
            ("+" if diff.delta_ms >= 0 else "") + _fmt_ms(diff.delta_ms)))
        if diff.movers:
            out.append("")
            out.append("  biggest movers (pointer, not verdict)")
            for i, before, after in diff.movers:
                out.append("    %-28s %8.1f -> %8.1f ms  %s%.1f" % (
                    i[:28], before, after, "+" if after >= before else "", after - before))
        if diff.flips:
            out.append("")
            out.append("  !! %d entr%s changed EXIT STATUS between the two runs — the comparison"
                       % (len(diff.flips), "y" if len(diff.flips) == 1 else "ies"))
            out.append("     is not like for like. Run `lypning conformance`.")
            for i, was, now in diff.flips[:10]:
                out.append("       %-28s %s -> %s" % (i[:28], was, now))

    damage = _damage_lines(timing.damage)
    if damage:
        out.append("")
        out.extend(damage)
    out.append("")
    out.append("%.1f s of wall clock." % timing.seconds)
    return "\n".join(line.rstrip() for line in out) + "\n"


def _fmt_ms(ms: float) -> str:
    """Seconds past a thousand milliseconds — the mjs rule, kept for readability."""
    return "%.2f s" % (ms / 1000.0) if abs(ms) >= 1000 else "%.1f ms" % ms
