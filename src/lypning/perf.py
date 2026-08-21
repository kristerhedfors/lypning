"""The hot-loop diagnostic: where lypning's interpreter spends time, per construct.

This is **not** an acceptance instrument and must never be used as one.
:mod:`lypning.bench` and ``lypning corpus-time`` are — they time the programs
agents actually type, which run once and exit, so their cost is dominated by
process spawn and parse. A microbenchmark loops fifty thousand times and
measures steady-state dispatch instead. The two disagree by an order of
magnitude, routinely, and the corpus is the one that decides
(``docs/MICROPYTHON.md`` §8a).

So what is this for? **Finding the gradient.** A corpus run says "the 500
programs cost 416 ms"; it does not say which construct to open the profiler on.
This does: it runs one small program per construct on lypning and on CPython,
subtracts each arm's own startup, and prints the ratio. A construct at 50x is
where the next hour goes. A construct at 1.2x is finished.

Three rules hold it honest, and each exists because the obvious version of this
tool is a liar:

* **Startup is subtracted.** lypning starts in a fraction of a millisecond and
  CPython in eleven. Without subtracting, every entry in the table reads as a
  lypning win and the slow constructs hide inside the startup gap.
* **The output is compared, not just timed.** Every suite program prints a
  checksum, and the arms must agree on it. A construct that is fast because it
  computes something else is the one failure this table could otherwise reward
  — invariant 1, on the tree's own terms. A disagreement is fatal.
* **A refusal is fatal too.** The suite is a claim about what lypning already
  supports. If an entry exits 90, either the subset narrowed or the suite drifted
  out of it, and both are things to notice rather than to average over.

Nothing here prints; :mod:`lypning.cli` renders. See CLAUDE.md invariant 8.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from . import bench, engines

#: Arms worth comparing by default. The mixture and lypning-mp are absent on
#: purpose: this table's question is "how fast is the Rust interpreter at a
#: construct it already supports", and a dispatcher arm answers a routing
#: question instead.
DEFAULT_ARMS: Tuple[str, ...] = (engines.CPYTHON, engines.LYPNING)

#: The reference every ratio is taken against.
REFERENCE = engines.CPYTHON

DEFAULT_REPEAT = 5
DEFAULT_TIMEOUT = 60.0


@dataclass(frozen=True)
class Case:
    """One construct, sized so that compute — not spawn — is what is measured.

    ``group`` is what the entry is evidence about; several cases share one so a
    single slow primitive shows up as a band in the table rather than as one
    outlier that is easy to dismiss.

    Every program ends in a ``print`` of something derived from all the work it
    did. That is the checksum the arms are held to, and it is why the loop
    cannot be optimised away by either interpreter: the answer is observed.
    """

    name: str
    group: str
    program: str


#: The suite. Sized against CPython at roughly 3–30 ms per case on the container
#: this was written on, which is enough that each arm's startup is a correction
#: rather than the measurement.
#:
#: Every case must be INSIDE the subset lypning already implements. Adding a
#: case for something lypning refuses does not measure a gap, it just makes the
#: run fatal — put that in the corpus and let `conformance --plan` rank it.
SUITE: Tuple[Case, ...] = (
    # --- the interpreter loop itself ---
    Case("loop-range", "loop",
         "s = 0\nfor i in range(200000):\n    s += i\nprint(s)"),
    Case("loop-while", "loop",
         "i = 0\ns = 0\nwhile i < 150000:\n    s += i\n    i += 1\nprint(s)"),
    Case("loop-nested", "loop",
         "s = 0\nfor i in range(400):\n    for j in range(400):\n        s += i * j\nprint(s)"),

    # --- names and calls ---
    Case("name-lookup", "name",
         "a = 1\nb = 2\nc = 3\nd = 4\ns = 0\nfor i in range(100000):\n    s += a + b + c + d\nprint(s)"),
    Case("call-func", "call",
         "def f(a, b):\n    return a + b\ns = 0\nfor i in range(100000):\n    s = f(s, i)\nprint(s)"),
    Case("call-recursive", "call",
         "def fib(n):\n    return n if n < 2 else fib(n - 1) + fib(n - 2)\nprint(fib(21))"),
    Case("call-method", "call",
         "t = 'abcdef'\nn = 0\nfor i in range(100000):\n    n += t.count('a')\nprint(n)"),

    # --- strings ---
    Case("str-concat", "str",
         "s = ''\nfor i in range(20000):\n    s += 'x'\nprint(len(s))"),
    Case("str-join", "str",
         "a = ['ab'] * 200000\nprint(len(''.join(a)))"),
    Case("str-methods", "str",
         "t = '  Hello World  '\nn = 0\nfor i in range(40000):\n    n += len(t.strip().lower().replace('o', '0'))\nprint(n)"),
    Case("str-split", "str",
         "t = 'a b c d e f g h'\nn = 0\nfor i in range(40000):\n    n += len(t.split())\nprint(n)"),
    Case("str-slice", "str",
         "t = 'abcdefghij' * 10\nn = 0\nfor i in range(60000):\n    n += len(t[5:-5])\nprint(n)"),
    Case("str-fmt-pct", "fmt",
         "n = 0\nfor i in range(40000):\n    n += len('%d-%s' % (i, 'a'))\nprint(n)"),
    Case("str-fstring", "fmt",
         "n = 0\nfor i in range(40000):\n    n += len(f'{i}-a')\nprint(n)"),
    Case("str-repr", "fmt",
         "n = 0\nfor i in range(40000):\n    n += len(repr([i, 'a', 1.5]))\nprint(n)"),

    # --- containers ---
    Case("list-append", "list",
         "a = []\nfor i in range(150000):\n    a.append(i * 2)\nprint(len(a), a[-1])"),
    Case("list-index", "list",
         "a = list(range(100000))\ns = 0\nfor i in range(100000):\n    s += a[i]\nprint(s)"),
    Case("list-comp", "list",
         "print(sum([i * i for i in range(200000)]))"),
    Case("list-sort", "list",
         "a = [(i * 7919) % 100000 for i in range(100000)]\na.sort()\nprint(a[0], a[-1])"),
    Case("dict-set", "dict",
         "d = {}\nfor i in range(60000):\n    d[str(i)] = i\nprint(len(d))"),
    Case("dict-get", "dict",
         "d = {}\nfor i in range(2000):\n    d[str(i)] = i\ns = 0\nfor j in range(30):\n    for i in range(2000):\n        s += d[str(i)]\nprint(s)"),
    Case("tuple-unpack", "tuple",
         "s = 0\nfor a, b in [(1, 2)] * 100000:\n    s += a * b\nprint(s)"),
    Case("membership", "container",
         "a = list(range(200))\nn = 0\nfor i in range(2000):\n    for j in range(100):\n        if j in a:\n            n += 1\nprint(n)"),

    # --- iterators the corpus leans on ---
    Case("enumerate-zip", "iter",
         "a = list(range(60000))\ns = 0\nfor i, (x, y) in enumerate(zip(a, a)):\n    s += x + y\nprint(s)"),
    Case("builtin-sum-len", "iter",
         "a = list(range(50000))\ns = 0\nfor i in range(20):\n    s += sum(a) + len(a)\nprint(s)"),

    # --- the two modules the corpus actually imports ---
    Case("json-dumps", "json",
         "import json\nd = {'a': 1, 'b': [1, 2, 3], 'c': 'x' * 20}\nn = 0\nfor i in range(20000):\n    n += len(json.dumps(d))\nprint(n)"),
    Case("json-loads", "json",
         "import json\nt = '{\"a\": 1, \"b\": [1, 2, 3], \"c\": \"xxxxxxxxxx\"}'\nn = 0\nfor i in range(20000):\n    n += len(json.loads(t))\nprint(n)"),

    # --- I/O, which is what `open` at 652 corpus sightings means ---
    Case("file-write-read", "io",
         "lines = ['line %d\\n' % i for i in range(20000)]\n"
         "with open('perf.txt', 'w') as fh:\n    fh.write(''.join(lines))\n"
         "n = 0\nwith open('perf.txt') as fh:\n    for line in fh:\n        n += len(line)\nprint(n)"),
    Case("print-lines", "io",
         "for i in range(30000):\n    print(i)"),
)

RAN = bench.RAN
REFUSED = bench.REFUSED
ERROR = bench.ERROR

#: Verdicts a case can carry. ``ok`` is the only one that contributes a ratio.
OK = "ok"
DIFFER = "differ"
REFUSED_V = "refused"
FAILED = "failed"
UNMEASURED = "unmeasured"


@dataclass
class Sample:
    """One arm's reading for one case."""

    arm: str
    ms: Optional[float]
    outcome: str
    returncode: int


@dataclass
class CaseResult:
    """One row of the table."""

    name: str
    group: str
    verdict: str
    samples: Dict[str, Sample] = field(default_factory=dict)
    #: Startup-subtracted milliseconds per arm. Absent for an arm that did not run.
    net: Dict[str, float] = field(default_factory=dict)
    #: ``net[arm] / net[REFERENCE]``. Absent when either side is unmeasurable.
    ratio: Dict[str, float] = field(default_factory=dict)
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.verdict == OK


@dataclass
class PerfReport:
    """A whole run. ``ok`` is false if any case differed, refused or failed."""

    arms: List[str]
    startup: Dict[str, float]
    cases: List[CaseResult]
    seconds: float
    repeat: int
    host: Dict[str, object]
    reference: str = REFERENCE

    @property
    def ok(self) -> bool:
        return all(c.verdict in (OK, UNMEASURED) for c in self.cases)

    @property
    def bad(self) -> List[CaseResult]:
        return [c for c in self.cases if c.verdict in (DIFFER, REFUSED_V, FAILED)]

    def totals(self) -> Dict[str, float]:
        """Summed net milliseconds per arm over the cases every arm measured.

        The shared subset, for the same reason :mod:`lypning.bench` insists on
        one: a total over different case sets is not a comparison.
        """
        out: Dict[str, float] = {a: 0.0 for a in self.arms}
        for c in self.cases:
            if not c.ok or any(a not in c.net for a in self.arms):
                continue
            for a in self.arms:
                out[a] += c.net[a]
        return out

    def shared(self) -> int:
        return sum(1 for c in self.cases
                   if c.ok and all(a in c.net for a in self.arms))


# --- measurement -------------------------------------------------------------


def _capture(arm: bench.Arm, program: str, cwd: Path, timeout: float) -> Tuple[int, str]:
    """Run once and keep the output. **Untimed, on purpose.**

    The number in the table comes from :func:`bench.time_one` and from nothing
    else — one timing path in the tree, so a difference between two paths cannot
    end up inside an arm's reading. This is the correctness half: it runs the
    same program one more time, throws the clock away, and keeps what was
    printed so the arms can be held to the same answer.
    """
    cmd = [str(arm.binary)]
    cmd.extend(arm.prefix)
    cmd.extend(["-c", program])
    env = dict(os.environ)
    env["LYPNING_CAPTURE"] = "0"
    env["LYPNING_LOG"] = str(cwd / "capture.jsonl")
    env["PYTHONHASHSEED"] = "0"
    env["LC_ALL"] = "C.UTF-8"
    env.update(arm.env)
    try:
        proc = subprocess.run(
            cmd, input="", capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            cwd=str(cwd), timeout=timeout, env=env, check=False,
        )
    except subprocess.TimeoutExpired:
        return (124, "")
    except OSError:
        return (127, "")
    return (proc.returncode, proc.stdout)


def _net(ms: Optional[float], startup: Optional[float]) -> Optional[float]:
    """Milliseconds of work, with the arm's own spawn cost taken out.

    Floored at a tenth of a microsecond rather than at zero: a case whose whole
    cost is startup would otherwise divide by zero and read as an infinite
    speedup.
    """
    if ms is None:
        return None
    base = startup or 0.0
    return max(ms - base, 0.0001)


def run(
    arms: Optional[Sequence[Union[str, bench.Arm]]] = None,
    *,
    repeat: int = DEFAULT_REPEAT,
    only: Optional[Sequence[str]] = None,
    timeout: float = DEFAULT_TIMEOUT,
    progress: Any = None,
) -> PerfReport:
    """Time the suite on each arm, min of ``repeat``, arms interleaved per case.

    Interleaved for the reason :func:`bench.startup` interleaves: consecutive
    spawns of one binary sit in a warmer page cache than the first spawn of the
    next, and arm-at-a-time hands that warmth to whichever arm went last.

    Every case gets its own temp cwd. Two of them write files, and a suite that
    wrote them into the repository would be the corpus battery's mistake made
    again in a smaller way (CLAUDE.md invariant 4).
    """
    t0 = time.perf_counter()
    resolved = bench.resolve_arms(arms or DEFAULT_ARMS)
    names = [a.name for a in resolved]
    start = bench.startup(repeat=max(3, int(repeat)), arms=resolved) if resolved else {}
    wanted = set(only) if only else None
    cases = [c for c in SUITE if wanted is None or c.name in wanted]

    results: List[CaseResult] = []
    for idx, case in enumerate(cases):
        if progress is not None:
            progress(idx, len(cases), case.name)
        row = CaseResult(name=case.name, group=case.group, verdict=OK)
        if not resolved:
            row.verdict = UNMEASURED
            row.note = "no arm is built"
            results.append(row)
            continue
        tmp = Path(tempfile.mkdtemp(prefix="lypning-perf-"))
        try:
            # Correctness first: an entry the arms disagree about is not a
            # measurement, and timing it five times would only make the wrong
            # number more precise.
            outs: Dict[str, str] = {}
            for arm in resolved:
                rc, out = _capture(arm, case.program, tmp, timeout)
                outs[arm.name] = out
                if rc == engines.UNSUPPORTED_EXIT:
                    row.verdict = REFUSED_V
                    row.note = "%s refused it — the suite is a claim about the subset" % arm.name
                elif rc != 0 and row.verdict == OK:
                    row.verdict = FAILED
                    row.note = "%s exited %d" % (arm.name, rc)
            if row.verdict == OK and len(set(outs.values())) > 1:
                row.verdict = DIFFER
                row.note = "arms printed different answers: " + ", ".join(
                    "%s=%r" % (n, (outs[n] or "")[:40]) for n in names)
            if row.verdict != OK:
                results.append(row)
                continue

            best: Dict[str, bench.EntryTime] = {}
            for _ in range(max(1, int(repeat))):
                for arm in resolved:
                    s = bench.time_one(arm, case.program, cwd=tmp, timeout=timeout)
                    prev = best.get(arm.name)
                    if prev is None or s.ms < prev.ms:
                        best[arm.name] = s
            for name in names:
                s = best.get(name)
                if s is None:
                    continue
                row.samples[name] = Sample(name, s.ms, s.outcome, s.returncode)
                if s.outcome != RAN or s.returncode != 0:
                    continue
                net = _net(s.ms, start.get(name))
                if net is not None:
                    row.net[name] = net
            ref = row.net.get(REFERENCE)
            if ref:
                for name, v in row.net.items():
                    row.ratio[name] = v / ref
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)
        results.append(row)

    return PerfReport(
        arms=names,
        startup=start,
        cases=results,
        seconds=time.perf_counter() - t0,
        repeat=int(repeat),
        host=bench.host_info(resolved),
    )


# --- rendering ---------------------------------------------------------------


def _ms(v: Optional[float], width: int = 9, digits: int = 2) -> str:
    return ("%*s" % (width, "—")) if v is None else ("%*.*f" % (width, digits, v))


def _x(v: Optional[float]) -> str:
    if v is None:
        return "—"
    if v >= 100:
        return "%.0fx" % v
    return "%.2fx" % v


def render(report: PerfReport, *, top: int = 0) -> str:
    """The table. Worst ratio first, because that ordering IS the work queue."""
    out: List[str] = []
    cpus = report.host.get("cpu_count")
    out.append("hot-loop diagnostic — %d cases, min of %d, arms interleaved"
               % (len(report.cases), report.repeat))
    out.append("NOT an acceptance gate: `lypning corpus-time --baseline` is."
               " This says WHERE the time goes, not whether a change is worth keeping.")
    out.append("")
    if report.host.get("ci"):
        out.append("  ! CI detected — a wall clock on a shared runner measures the runner.")
    out.append("host: %s cpus, %s" % (cpus, report.host.get("kernel", "?")))
    for name in report.arms:
        s = report.startup.get(name)
        out.append("  %-12s startup %s ms  (subtracted from every row below)"
                   % (name, "%.2f" % s if s is not None else "—"))
    out.append("")

    others = [a for a in report.arms if a != REFERENCE]
    head = "%-18s %-9s" % ("case", "group")
    for a in report.arms:
        head += " %9s" % a[:9]
    for a in others:
        head += " %8s" % "vs ref"
    out.append(head)
    rows = [c for c in report.cases if c.ok and c.ratio]
    rows.sort(key=lambda c: -max([c.ratio.get(a, 0.0) for a in others] or [0.0]))
    shown = rows[:top] if top else rows
    for c in shown:
        line = "%-18s %-9s" % (c.name, c.group)
        for a in report.arms:
            line += " " + _ms(c.net.get(a))
        for a in others:
            line += " %8s" % _x(c.ratio.get(a))
        out.append(line)
    if top and len(rows) > top:
        out.append("  … %d more cases, %s" % (len(rows) - top, "--top 0 for all"))

    totals = report.totals()
    shared = report.shared()
    out.append("")
    out.append("%-18s %-9s" % ("TOTAL", "%d/%d" % (shared, len(report.cases)))
               + "".join(" " + _ms(totals.get(a)) for a in report.arms)
               + "".join(" %8s" % _x((totals.get(a) or 0) / totals[REFERENCE])
                         if totals.get(REFERENCE) else " %8s" % "—" for a in others))

    slow = [c for c in rows if any(c.ratio.get(a, 0) > 1.0 for a in others)]
    if slow:
        out.append("")
        out.append("the queue — every case where lypning is slower than CPython at "
                   "something it already supports:")
        for c in slow[:8]:
            worst = max((c.ratio.get(a, 0.0), a) for a in others)
            out.append("  %-18s %-9s %8s slower on %s" % (c.name, c.group, _x(worst[0]), worst[1]))

    bad = report.bad
    if bad:
        out.append("")
        out.append("NOT MEASURED — each of these is a bug, not a slow row:")
        for c in bad:
            out.append("  %-18s %-8s %s" % (c.name, c.verdict, c.note))
    out.append("")
    out.append("%.1f s of wall clock." % report.seconds)
    return "\n".join(out)


# --- the record, for a before/after --------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(report: PerfReport) -> Dict[str, Any]:
    """The run, in the shape :func:`diff` reads back."""
    return {
        "tool": "lypning perf",
        "when": _now(),
        "repeat": report.repeat,
        "arms": list(report.arms),
        "reference": report.reference,
        "startup": dict(report.startup),
        "host": dict(report.host),
        "seconds": report.seconds,
        "ok": report.ok,
        "cases": [
            {
                "name": c.name,
                "group": c.group,
                "verdict": c.verdict,
                "net": dict(c.net),
                "ratio": dict(c.ratio),
                "note": c.note,
            }
            for c in report.cases
        ],
    }


def check_record_target(path: Union[Path, str]) -> Path:
    """Fail now rather than after the suite has run.

    A ``--record`` whose directory does not exist, or which names a directory,
    is discovered here — before the measurement — for the reason
    :func:`bench.check_record_target` exists: a write that fails afterwards
    throws the run away.
    """
    p = Path(path)
    if p.is_dir():
        raise ValueError("%s is a directory" % p)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ValueError("cannot write %s: %s" % (p, e))
    return p


def write_record(report: PerfReport, path: Union[Path, str]) -> Path:
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(record(report), indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        raise ValueError("cannot write %s: %s" % (p, e))
    return p


def load_record(path: Union[Path, str]) -> Dict[str, Any]:
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError as e:
        raise ValueError("cannot read %s: %s" % (p, e))
    except ValueError as e:
        raise ValueError("%s is not a perf record: %s" % (p, e))
    if not isinstance(data, dict) or data.get("tool") != "lypning perf":
        raise ValueError("%s is not a `lypning perf --record` file" % p)
    return data


@dataclass
class PerfDiff:
    """This run against a recorded one, over the cases BOTH measured."""

    baseline_path: str = ""
    baseline_when: str = ""
    arm: str = engines.LYPNING
    rows: List[Tuple[str, float, float]] = field(default_factory=list)
    gone: List[str] = field(default_factory=list)
    added: List[str] = field(default_factory=list)

    @property
    def totals(self) -> Tuple[float, float]:
        return (sum(r[1] for r in self.rows), sum(r[2] for r in self.rows))


def diff(baseline: Dict[str, Any], report: PerfReport,
         *, arm: str = engines.LYPNING) -> PerfDiff:
    """Per-case before/after for one arm, over the intersection.

    The intersection is stated rather than assumed: the suite grows, and a total
    that silently spans different case sets is the way a speed claim goes wrong.
    """
    was = {c.get("name"): c for c in baseline.get("cases", [])
           if isinstance(c, dict) and c.get("verdict") == OK}
    now = {c.name: c for c in report.cases if c.ok}
    d = PerfDiff(baseline_when=str(baseline.get("when", "")), arm=arm)
    for name in sorted(set(was) | set(now)):
        b, n = was.get(name), now.get(name)
        bv = (b or {}).get("net", {}).get(arm) if b else None
        nv = n.net.get(arm) if n else None
        if bv is None and nv is not None:
            d.added.append(name)
        elif bv is not None and nv is None:
            d.gone.append(name)
        elif bv is not None and nv is not None:
            d.rows.append((name, float(bv), float(nv)))
    d.rows.sort(key=lambda r: (r[2] - r[1]))
    return d


def render_diff(d: PerfDiff) -> str:
    out: List[str] = []
    before, after = d.totals
    out.append("")
    out.append("against %s (%s) — %d cases in both, arm %s"
               % (d.baseline_path or "the baseline", d.baseline_when or "?",
                  len(d.rows), d.arm))
    if not d.rows:
        out.append("  nothing in common to compare.")
        return "\n".join(out)
    out.append("%-18s %9s %9s %9s" % ("case", "before", "after", "delta"))
    movers = [r for r in d.rows if abs(r[2] - r[1]) > max(0.05, 0.02 * r[1])]
    for name, b, n in movers[:12]:
        out.append("%-18s %9.2f %9.2f %+9.2f" % (name, b, n, n - b))
    if not movers:
        out.append("  no case moved by more than 2%.")
    out.append("%-18s %9.2f %9.2f %+9.2f  (%s)"
               % ("TOTAL", before, after, after - before,
                  "faster" if after < before else "slower" if after > before else "level"))
    if d.added:
        out.append("  new since the baseline: " + ", ".join(d.added[:8]))
    if d.gone:
        out.append("  in the baseline and not measured now: " + ", ".join(d.gone[:8]))
    return "\n".join(out)
