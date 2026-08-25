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
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from . import bench, corpus, engines

#: Arms worth comparing by default. The mixture and lypning-mp are absent on
#: purpose: this table's question is "how fast is the Rust interpreter at a
#: construct it already supports", and a dispatcher arm answers a routing
#: question instead.
DEFAULT_ARMS: Tuple[str, ...] = (engines.CPYTHON, engines.LYPNING)

#: The reference every ratio is taken against.
REFERENCE = engines.CPYTHON

DEFAULT_REPEAT = 5
DEFAULT_TIMEOUT = 60.0

#: Below this much reference-arm work, a case's ratio is mostly the startup
#: subtraction and its noise. CPython's startup is ~11 ms here and is subtracted
#: whole, so a case where CPython did 0.5 ms of work divides two numbers that are
#: both rounding error. Cases are SIZED past this; the check is there to catch a
#: case that drifted under it on a faster machine, and it prints rather than
#: failing — the row is still evidence, just not a ranking.
NOISE_FLOOR_MS = 2.0


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
    #: A regex matched against every corpus program, to say how often agents
    #: actually type the construct this case times. A ratio without it is a
    #: microbenchmark: the suite once reported `s += x` in a loop at 43x
    #: CPython, and that construct appears in ONE of the corpus's programs.
    #: Empty means "no honest probe" — reported as unknown, never as zero.
    probe: str = ""


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
         "s = 0\nfor i in range(200000):\n    s += i\nprint(s)",
         '\\bfor\\s+\\w+\\s+in\\s+range\\('),
    Case("loop-while", "loop",
         "i = 0\ns = 0\nwhile i < 150000:\n    s += i\n    i += 1\nprint(s)",
         '^\\s*while\\s'),
    Case("loop-nested", "loop",
         "s = 0\nfor i in range(400):\n    for j in range(400):\n        s += i * j\nprint(s)",
         '\\bfor\\s+\\w+\\s+in\\b'),

    # --- names and calls ---
    Case("name-lookup", "name",
         "a = 1\nb = 2\nc = 3\nd = 4\ns = 0\nfor i in range(100000):\n    s += a + b + c + d\nprint(s)",
         '\\bfor\\s+\\w+\\s+in\\b'),
    Case("call-func", "call",
         "def f(a, b):\n    return a + b\ns = 0\nfor i in range(100000):\n    s = f(s, i)\nprint(s)",
         '^\\s*def\\s'),
    Case("call-recursive", "call",
         "def fib(n):\n    return n if n < 2 else fib(n - 1) + fib(n - 2)\nprint(fib(24))",
         '^\\s*def\\s'),
    Case("call-method", "call",
         "t = 'abcdef'\nn = 0\nfor i in range(100000):\n    n += t.count('a')\nprint(n)",
         '\\.\\w+\\('),

    # --- strings ---
    Case("str-concat", "str",
         "s = ''\nfor i in range(20000):\n    s += 'x'\nprint(len(s))",
         '(?s)(for |while ).{0,400}?^\\s*\\w+\\s*\\+=\\s*[\'\\"f]'),
    Case("str-join", "str",
         "a = ['ab'] * 600000\nprint(len(''.join(a)))",
         '\\.join\\('),
    Case("str-methods", "str",
         "t = '  Hello World  '\nn = 0\nfor i in range(40000):\n    n += len(t.strip().lower().replace('o', '0'))\nprint(n)",
         '\\.(strip|lower|upper|replace|lstrip|rstrip)\\('),
    Case("str-split", "str",
         "t = 'a b c d e f g h'\nn = 0\nfor i in range(40000):\n    n += len(t.split())\nprint(n)",
         '\\.split(lines)?\\('),
    # The six methods that take optional start/end bounds, and the suite had no
    # case for ANY of them until iteration 30 — `call-method` probes `.count()`
    # on a six-character string, which does not reach `slice_str`. Two scouts
    # independently found `slice_str` walking the receiver three times before
    # looking at the needle, on calls that pass no bounds at all, and neither
    # could show it: a measured win that moves no row means the suite has a hole
    # (skill §3). The haystack is 1,000 bytes on purpose — the defect is linear
    # in the RECEIVER, so a short one hides it, which is how it survived.
    Case("str-scan", "str",
         "t = 'abcdefghij' * 100\nn = 0\nfor i in range(40000):\n"
         "    n += 1 if t.startswith('abc') else 0\n    n += t.find('hij')\nprint(n)",
         r'\.(startswith|endswith|find|rfind|index|rindex)\('),
    Case("str-slice", "str",
         "t = 'abcdefghij' * 10\nn = 0\nfor i in range(60000):\n    n += len(t[5:-5])\nprint(n)",
         '\\[[^]\\[]*:[^]\\[]*\\]'),
    Case("str-fmt-pct", "fmt",
         "n = 0\nfor i in range(60000):\n    n += len('%d-%s' % (i, 'a'))\nprint(n)",
         '[\'\\"][^\'\\"]*%[sdrfx][^\'\\"]*[\'\\"]\\s*%'),
    Case("str-fstring", "fmt",
         "n = 0\nfor i in range(60000):\n    n += len(f'{i}-a')\nprint(n)",
         'f[\'\\"]'),
    # `str(x)` and `repr(x)` of a SCALAR, which is a different path from the
    # composite one below it and was invisible here until a change improved it
    # by a fifth and moved no row in the table (ledger, iteration 6).
    Case("str-of-scalar", "fmt",
         "n = 0\nfor i in range(60000):\n    n += len(str(i)) + len(repr('ab'))\nprint(n)",
         r"\b(str|repr)\("),
    # Probed on `repr(` alone — 6% of the corpus — and NOT on `print(`, which is
    # 87% and was what this row was weighted by until iteration 7. The case
    # reprs a LIST: printing a composite reaches the same code, so the probe
    # understates a little, and understating is the right direction. `print()`
    # of a scalar is a different path, it is `print-lines`, and lypning wins it.
    Case("str-repr", "fmt",
         "n = 0\nfor i in range(40000):\n    n += len(repr([i, 'a', 1.5]))\nprint(n)",
         r"\brepr\("),

    # --- containers ---
    Case("list-append", "list",
         "a = []\nfor i in range(150000):\n    a.append(i * 2)\nprint(len(a), a[-1])",
         '\\.append\\('),
    Case("list-index", "list",
         "a = list(range(100000))\ns = 0\nfor i in range(100000):\n    s += a[i]\nprint(s)",
         '\\[\\s*-?\\w+\\s*\\]'),
    # A generator expression is a different machine from a comprehension — it
    # suspends between elements — and the suite had no case for one until a
    # change made them 2.5x faster and no row moved (ledger, iteration 15).
    Case("genexpr", "iter",
         "print(sum(x * x for x in range(150000)))",
         r"\((?![^()]*\[)[^()]*\bfor\b[^()]*\)"),
    Case("list-comp", "list",
         "print(sum([i * i for i in range(200000)]))",
         '\\[[^]\\[]*\\bfor\\b[^]\\[]*\\]'),
    Case("list-sort", "list",
         "a = [(i * 7919) % 100000 for i in range(100000)]\n"
         "b = list(a)\nfor _ in range(6):\n    b = list(a)\n    b.sort()\nprint(b[0], b[-1])",
         '\\b(sorted\\(|\\.sort\\()'),
    Case("dict-set", "dict",
         "keys = [str(i) for i in range(60000)]\n"
         "d = {}\nfor k in keys:\n    d[k] = 1\nprint(len(d))",
         '\\[[^]\\[]*\\]\\s*='),
    Case("dict-get", "dict",
         "keys = [str(i) for i in range(2000)]\n"
         "d = {}\nfor k in keys:\n    d[k] = 1\n"
         "s = 0\nfor j in range(30):\n    for k in keys:\n        s += d[k]\nprint(s)",
         '\\.(get|items|keys|values)\\(|\\bdict\\('),
    Case("tuple-unpack", "tuple",
         "s = 0\nfor a, b in [(1, 2)] * 100000:\n    s += a * b\nprint(s)",
         '\\bfor\\s+\\w+\\s*,\\s*\\w+'),
    Case("membership", "container",
         "a = list(range(200))\nn = 0\nfor i in range(2000):\n    for j in range(100):\n        if j in a:\n            n += 1\nprint(n)",
         '\\bin \\[|\\bin \\(|\\bnot in \\['),

    # --- iterators the corpus leans on ---
    Case("enumerate-zip", "iter",
         "a = list(range(60000))\ns = 0\nfor i, (x, y) in enumerate(zip(a, a)):\n    s += x + y\nprint(s)",
         '\\b(enumerate|zip)\\('),
    Case("builtin-sum-len", "iter",
         "a = list(range(50000))\ns = 0\nfor i in range(20):\n    s += sum(a) + len(a)\nprint(s)",
         '\\b(sum|len|min|max)\\('),

    # --- the two modules the corpus actually imports ---
    Case("json-dumps", "json",
         "import json\nd = {'a': 1, 'b': [1, 2, 3], 'c': 'x' * 20}\nn = 0\nfor i in range(20000):\n    n += len(json.dumps(d))\nprint(n)",
         'json\\.dump'),
    Case("json-loads", "json",
         "import json\nt = '{\"a\": 1, \"b\": [1, 2, 3], \"c\": \"xxxxxxxxxx\"}'\nn = 0\nfor i in range(20000):\n    n += len(json.loads(t))\nprint(n)",
         'json\\.load'),

    # --- I/O, which is what `open` at 652 corpus sightings means ---
    # The content is built ONCE, outside what is timed, and by repetition rather
    # than by formatting. This case used to build its lines with `'%d' % i` in a
    # comprehension, which callgrind showed was most of its cost — so a row
    # labelled `io`, weighted by how often the corpus opens a file, was really
    # reporting on `%` formatting, which the corpus barely uses (ledger,
    # iteration 7).
    #
    # **20,000 lines was too few and the suite said so**: it began printing
    # "the reference arm spent under 2 ms of work on file-write-read, so its
    # ratio is mostly the startup subtraction". Raised to 100,000 in iteration
    # 43, where the reference arm does ~17 ms and the row measures I/O. It is
    # not a cosmetic change — the net ratio GROWS with the size (1.82x at
    # 20,000, 2.36x at 100,000, 2.45x at 200,000), so the small case was hiding
    # a real per-byte gap rather than merely measuring it imprecisely. Resizing
    # renumbers the row and the TOTAL; earlier ledger entries are not comparable
    # across it.
    Case("file-write-read", "io",
         "block = 'a line of text\\n' * 100000\n"
         "with open('perf.txt', 'w') as fh:\n    fh.write(block)\n"
         "n = 0\nwith open('perf.txt') as fh:\n    for line in fh:\n        n += len(line)\nprint(n)",
         '\\bopen\\('),
    Case("print-lines", "io",
         "for i in range(30000):\n    print(i)",
         '\\bprint\\('),
)

#: How much of the corpus each case's construct appears in, and why that column
#: exists at all.
#:
#: A ratio on its own ranks by how badly the interpreter loses. It does not rank
#: by how much that costs anybody, and the two orderings are not the same list.
#: This suite reported `s += x` in a loop at 43x CPython — the worst row it had —
#: against a corpus in which that construct appears in ONE program out of 842.
#: Rewriting the string representation to fix it would have been an afternoon
#: spent on 0.1% of the workload, and the microbenchmark would have applauded.
#:
#: So every case carries a regex, the corpus is scanned once per run, and the
#: work queue is ordered by how far the case is behind TIMES how often the
#: construct is typed. CLAUDE.md invariant 3 applies to the denominator like
#: everything else: the corpus size is loaded and printed, never remembered.


def prevalence(entries: Optional[Sequence[Any]] = None) -> Tuple[Dict[str, float], int]:
    """Fraction of corpus programs each case's probe matches, and the corpus size.

    A case with no probe is absent from the mapping rather than present with a
    zero: "we did not ask" and "nobody types this" are different answers, and a
    zero in a weight column would silently retire a case nobody meant to retire.

    Never raises. A corpus that cannot be loaded gives ``({}, 0)`` — the column
    becomes a hole and the table still prints, because a diagnostic that refuses
    to run without its denominator is a diagnostic nobody runs.
    """
    try:
        rows = list(entries) if entries is not None else corpus.load_default()
    except Exception:
        return ({}, 0)
    programs = [getattr(e, "program", "") or "" for e in rows]
    if not programs:
        return ({}, 0)
    out: Dict[str, float] = {}
    for case in SUITE:
        if not case.probe:
            continue
        try:
            rx = re.compile(case.probe, re.M | re.S)
        except re.error:
            continue
        out[case.name] = sum(1 for t in programs if rx.search(t)) / len(programs)
    return (out, len(programs))


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
    #: Corpus prevalence per case name, and the corpus size it was taken over.
    prevalence: Dict[str, float] = field(default_factory=dict)
    corpus_size: int = 0

    def weight(self, case: CaseResult) -> Optional[float]:
        """How much of the corpus this case is behind on.

        ``(ratio - 1) x prevalence``: how far the interpreter is behind CPython,
        times how much of the workload types the construct. A case that is
        faster than CPython weighs zero rather than negative — being ahead
        somewhere does not buy time back somewhere else.
        """
        p = self.prevalence.get(case.name)
        r = max(case.ratio.get(a, 0.0) for a in self.arms if a != self.reference) \
            if len(self.arms) > 1 else None
        if p is None or not r:
            return None
        return max(r - 1.0, 0.0) * p

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

    weights, size = prevalence()
    return PerfReport(
        arms=names,
        startup=start,
        cases=results,
        seconds=time.perf_counter() - t0,
        repeat=int(repeat),
        host=bench.host_info(resolved),
        prevalence=weights,
        corpus_size=size,
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
    head += " %7s" % "corpus"
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
        pv = report.prevalence.get(c.name)
        line += " %7s" % ("—" if pv is None else "%.0f%%" % (pv * 100))
        out.append(line)
    if top and len(rows) > top:
        out.append("  … %d more cases, %s" % (len(rows) - top, "--top 0 for all"))

    thin = [c.name for c in report.cases
            if c.ok and 0 < c.net.get(REFERENCE, 0.0) < NOISE_FLOOR_MS]
    if thin:
        out.append("")
        out.append("  ! too small to trust — the reference arm spent under %.0f ms of work "
                   "on %s, so its ratio is mostly the startup subtraction. Grow the case."
                   % (NOISE_FLOOR_MS, ", ".join(thin)))

    totals = report.totals()
    shared = report.shared()
    out.append("")
    out.append("%-18s %-9s" % ("TOTAL", "%d/%d" % (shared, len(report.cases)))
               + "".join(" " + _ms(totals.get(a)) for a in report.arms)
               + "".join(" %8s" % _x((totals.get(a) or 0) / totals[REFERENCE])
                         if totals.get(REFERENCE) else " %8s" % "—" for a in others))

    slow = [c for c in rows if any(c.ratio.get(a, 0) > 1.0 for a in others)]
    weighted = [(report.weight(c), c) for c in slow]
    weighted = [(w, c) for w, c in weighted if w]
    weighted.sort(key=lambda t: -t[0])
    if weighted:
        out.append("")
        out.append("THE QUEUE — how far behind, TIMES how much of the corpus types it.")
        out.append("A ratio alone ranks by how badly lypning loses, not by what that "
                   "costs anyone; the two are different lists.")
        out.append("%-18s %-9s %8s %8s %9s" % ("case", "group", "vs ref", "corpus", "weight"))
        for w, c in weighted[:10]:
            worst = max(c.ratio.get(a, 0.0) for a in others)
            out.append("%-18s %-9s %8s %7.0f%% %9.2f"
                       % (c.name, c.group, _x(worst),
                          100 * report.prevalence.get(c.name, 0.0), w))
        out.append("  prevalence over the %d corpus programs loaded on THIS run "
                   "(invariant 3)." % report.corpus_size)
    elif slow:
        out.append("")
        out.append("the queue — slower than CPython, but the corpus could not be "
                   "loaded, so these are unweighted:")
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
        "prevalence": dict(report.prevalence),
        "corpus_size": report.corpus_size,
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
                "weight": report.weight(c),
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
