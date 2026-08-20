"""The four-arm benchmark, and the two ways a benchmark like this lies.

Four arms over the SAME corpus of real one-liners:

  ``cpython``     the real python3 — the baseline everything is measured against
  ``lypning``     the Rust subset alone
  ``lypning-mp``  the MicroPython-derived subset alone
  ``mixture``     ``lypning run`` — route, then execute on whichever tier fits

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
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

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

ARM_ORDER: Tuple[str, ...] = (engines.CPYTHON, engines.LYPNING, engines.MICROPYTHON, MIXTURE)

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


def resolve_arms(names: Optional[Sequence[str]] = None) -> List[Arm]:
    """The arms that can actually be measured, in :data:`ARM_ORDER`.

    An engine that is not built is dropped rather than reported as a zero: a
    missing arm is a hole in the table, and a zero in it would be a lie that
    reads as a win. ``lypning-mp`` is the usual absentee — it needs a network
    build — so every path here has to survive its absence.

    The mixture arm is the ``lypning`` binary with its ``run`` subcommand, and it
    is told where the other two engines are. That matters for CPython in
    particular: capture installs a shim named ``python3`` first on ``$PATH``, and
    a mixture arm that fell through to the shim would be timing a shell script.
    """
    want = tuple(names) if names else ARM_ORDER
    found = engines.available()
    out: List[Arm] = []
    for name in want:
        if name == MIXTURE:
            b = found.get(engines.LYPNING)
            if b is None:
                continue
            env: Dict[str, str] = {}
            if found.get(engines.CPYTHON):
                env["LYPNING_CPYTHON"] = str(found[engines.CPYTHON])
            if found.get(engines.MICROPYTHON):
                env["LYPNING_MP_BIN"] = str(found[engines.MICROPYTHON])
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
    """
    env = dict(os.environ)
    env["LYPNING_CAPTURE"] = "0"
    env["LYPNING_LOG"] = str((cwd or Path(tempfile.gettempdir())) / "capture.jsonl")
    env["PYTHONHASHSEED"] = "0"
    env["LC_ALL"] = "C.UTF-8"
    env.update(arm.env)
    return env


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


def startup(repeat: int = DEFAULT_STARTUP_REPEAT, arms: Optional[Sequence[str]] = None) -> Dict[str, float]:
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
    return {
        "cpu_count": os.cpu_count() or 0,
        "kernel": "{0} {1}".format(platform.system(), platform.release()),
        "machine": platform.machine(),
        "engines": eng,
        "ci": is_ci(),
    }


def corpus_time(
    entries: Optional[Sequence[corpus.Entry]] = None,
    *,
    repeat: int = 1,
    limit: Optional[int] = None,
    arms: Optional[Sequence[str]] = None,
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
    arms: Optional[Sequence[str]] = None,
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
    out.append("host: {0} cpus, {1} ({2})".format(
        host.get("cpu_count", "?"), host.get("kernel", "?"), host.get("machine", "?")))
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

    if report.damage:
        out.append("")
        n = len(report.damage.paths)
        out.append("!! {0} repository file{1} changed by corpus programs:".format(
            n, "" if n == 1 else "s"))
        for p in report.damage.paths[:20]:
            out.append("     {0}".format(p))
        if len(report.damage.paths) > 20:
            out.append("     … and {0} more".format(len(report.damage.paths) - 20))
        if report.damage.restored:
            out.append("   restored {0} — but a run that damaged the tree at all is a run "
                       "whose numbers were taken across a moving target.".format(
                           len(report.damage.restored)))
        if report.damage.failed:
            out.append("   COULD NOT RESTORE: {0}".format(", ".join(report.damage.failed[:10])))

    out.append("")
    out.append("{0:.1f} s of wall clock.".format(report.seconds))
    return "\n".join(line.rstrip() for line in out) + "\n"


def _median(xs: Sequence[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


__all__ = [
    "MIXTURE", "ARM_ORDER", "Arm", "ArmResult", "BenchReport", "Damage", "EntryTime",
    "RAN", "REFUSED", "ERROR",
    "bench", "corpus_time", "host_info", "is_ci", "render", "resolve_arms",
    "skip_reason", "startup", "time_one",
]
