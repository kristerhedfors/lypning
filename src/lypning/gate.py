"""The build gate: does this binary still have the shape the cost model requires?

Cold cost in the target sandbox is neither CPU nor RSS. The root filesystem is
an ext2 image streamed block by block over a WebSocket and cached in IndexedDB,
so the first run of a binary pays for its own ELF, for every shared object it
links, and for every path it opens — and for nothing else
(docs/MICROPYTHON.md §1). CPython loses there by shape rather than by speed:
``-c 'pass'`` opens 22 files, probes 7 more that miss, and makes 65 stat calls,
each one a lookup that crosses a network. That is the 8573 ms.

So this module measures the three things that actually predict cold cost, on the
built artifact, in seconds instead of a Playwright run:

  1. **shared objects** — a dynamic binary means an ``ld.so`` path search plus
     every ``.so`` streamed over the wire. Zero means one file, ever.
  2. **bytes** — every byte is a byte fetched, and in 131,072 B device blocks
     (docs/LYPNING.md §8), so size is a step function rather than a tiebreak.
  3. **file opens** — how many paths a trivial ``-c 'pass'`` touches. This is
     the proxy for cold blocks, and it is where a stdlib that lives as files on
     disk becomes a stdlib fetched over a WebSocket.

None of the three needs a VM, which is the point: a build change is judged here
before it reaches one.

The invariants this module exists to hold:

**An unmeasurable check is not a passing check.** strace is absent, or ptrace is
blocked, in plenty of containers, and a gate that rendered a missing open count
as ``0`` would be reporting a spectacular pass. Every such check carries a
``note`` beginning with ``"unmeasured"``, :func:`render` marks the row ``--``
rather than ``ok``, and the number is never printed as a zero.

**A tier that is not built is reported, not raised on.** lypning-mp needs a
network to build and frequently is not present; every path through here must say
so and carry on.

**The gate never accepts on its own numbers.** docs/MICROPYTHON.md §2 accepts on
a cold run in a real VM, and the projection printed at the bottom of the report
is an estimate from shape — labelled as one everywhere it appears.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import engines

# --- the budget, docs/MICROPYTHON.md §2 --------------------------------------

MAX_BYTES = 700_000
"""lypning-mp's stripped-static budget.

It was 400 KB until the floor was measured and found to sit above it
(docs/MICROPYTHON.md §2): an empty ``main`` costs 635,744 B under glibc-static
on i386, and Berry — a complete, mature dynamic-language VM carrying neither
``re`` nor ``json`` — compiles to 365,660 B. 400 KB was unreachable by anything
that speaks Python. 700 KB is set against the measured 541,688 B MicroPython
prototype with room for the frozen shims, and it is only a meaningful number on
a musl build: glibc-static would spend 91% of it before the interpreter starts.
"""

MAX_OPENS = 3
"""Paths a ``-c 'pass'`` may touch. A static binary with a frozen stdlib opens
itself and little else; CPython opens 22."""

MAX_SHARED_OBJECTS = 0
"""Not a budget, a precondition. One ``NEEDED`` entry reintroduces the whole
loader path search on a filesystem where a directory lookup is a round trip."""

DEVICE_BLOCK = 131_072
"""The sandbox device block. Bytes are fetched in these, so 1 B over a boundary
costs the same as 131,072 B (docs/LYPNING.md §8)."""

PROBE = "pass"
"""The empty program. Everything it costs is the interpreter arriving."""

CPYTHON_COLD_MS = 8573
"""``python3 --version``, cold, in a real VM against production
(docs/SANDBOX-PERFORMANCE.md §1). The one true anchor this module has; it cannot
measure it here and does not pretend to."""

UNMEASURED = "unmeasured"
"""Prefix on :attr:`Check.note` for a check that could not be taken."""

STRACE_SYSCALLS = "openat,open,stat,access,newfstatat,statx,lstat,readlink,faccessat,faccessat2"
"""``openat,open,stat,access`` is the intent; the rest are the names glibc
actually issues. On x86-64 a ``stat`` is a ``newfstatat`` and an ``access`` is
often a ``faccessat2``, so a trace limited to the four classic names reports
zero stat calls on a binary making sixty-five of them — which reads as a pass.
:func:`file_opens` falls back to ``-e trace=file`` if a strace build rejects
this list."""


# --- records -----------------------------------------------------------------


@dataclass
class Check:
    """One gate row. ``value`` is what was measured, ``limit`` what was wanted.

    ``ok`` is false only for a check that was taken and failed. A check that
    could not be taken is ``ok`` with ``note`` starting ``"unmeasured"`` — see
    the module docstring for why the two must not collapse into each other.
    """

    name: str
    value: Any
    limit: Any
    ok: bool
    unit: str = ""
    note: str = ""

    @property
    def measured(self) -> bool:
        return not self.note.startswith(UNMEASURED)


@dataclass
class GateReport:
    """Every row for one binary, plus the baseline when one was asked for."""

    binary: str
    checks: List[Check] = field(default_factory=list)
    ok: bool = False
    baseline: Optional[Dict[str, Any]] = None

    def check(self, name: str) -> Optional[Check]:
        for c in self.checks:
            if c.name == name:
                return c
        return None

    def value(self, name: str) -> Any:
        c = self.check(name)
        return c.value if c is not None else None


# --- primitives --------------------------------------------------------------


def _run(cmd: Sequence[str], timeout: float = 30.0,
         env: Optional[Dict[str, str]] = None) -> Optional[subprocess.CompletedProcess]:
    """Run a helper tool. ``None`` means the tool is not here; a non-zero exit is
    the tool's own answer and is handed back for the caller to read."""
    try:
        return subprocess.run(
            list(cmd), capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=env, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def size_bytes(binary: Path | str) -> int:
    """Bytes on disk. ``0`` for a binary that is not there — a missing artifact
    is caught by its own check, not by an exception from the size probe."""
    try:
        return Path(binary).stat().st_size
    except OSError:
        return 0


def device_blocks(size: int) -> int:
    """Device blocks a file of ``size`` occupies, rounded up.

    The unit cold cost is actually charged in. 541,688 B and 655,360 B are both
    5 blocks; 655,361 B is 6, and that last byte costs as much as the preceding
    131,071.
    """
    if size <= 0:
        return 0
    return -(-int(size) // DEVICE_BLOCK)


def _needed(binary: Path | str) -> Tuple[List[str], bool]:
    """``(DT_NEEDED entries, measured)``.

    The second element is the whole reason this is not just
    :func:`shared_objects`. An empty list is the headline result for a static
    binary AND what you get from a container with neither ``readelf`` nor
    ``ldd`` installed, and a gate that could not tell those apart would hand a
    dynamically linked build a clean bill of health on the one check that is a
    precondition rather than a budget.

    ``readelf`` first and ``ldd`` only as a fallback: ``ldd`` resolves by
    *running* the binary's own loader, which is slower and is a thing you should
    not do to an artifact you are still deciding to trust, while ``readelf -d``
    only reads the dynamic section.
    """
    p = Path(binary)
    if not p.is_file():
        return ([], False)
    proc = _run(["readelf", "-d", str(p)])
    if proc is not None and proc.returncode == 0:
        return ([m.group(1) for m in (_NEEDED_RE.search(ln) for ln in proc.stdout.splitlines()) if m], True)
    proc = _run(["ldd", str(p)])
    if proc is None:
        return ([], False)
    text = proc.stdout + proc.stderr
    if "not a dynamic executable" in text:
        return ([], True)  # ldd exits non-zero to say "static", which is an answer
    if proc.returncode != 0:
        return ([], False)
    out: List[str] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        name = line.split("=>")[0].strip().split(" (")[0].strip()
        # The vDSO is not a file, and the loader is implied by having one at
        # all; neither is a library whose blocks get streamed.
        if not name or name.startswith("linux-vdso") or "ld-linux" in name or "/ld-musl" in name:
            continue
        out.append(name)
    return (out, True)


def shared_objects(binary: Path | str) -> List[str]:
    """``DT_NEEDED`` entries, in link order. Empty for a static binary — and
    also when nothing could read the ELF, which is why :func:`gate` uses
    :func:`_needed` and reports the difference."""
    return _needed(binary)[0]


def is_static(binary: Path | str) -> Tuple[bool, str]:
    """``(static, evidence)``.

    ``file(1)`` is the primary because its answer is the same string
    docs/MICROPYTHON.md §2 quotes, and printing that line is half of what makes
    a failed gate diagnosable. Where ``file`` is absent the ELF is read
    directly: no ``DT_NEEDED`` and no ``PT_INTERP`` is static, and a static-pie
    correctly passes both tests. When neither tool is present the answer is
    ``unknown`` rather than a guess, and the caller marks the row unmeasured.
    """
    p = Path(binary)
    if not p.is_file():
        return (False, "missing")
    proc = _run(["file", "-L", str(p)])
    if proc is not None and proc.returncode == 0 and proc.stdout.strip():
        desc = proc.stdout.strip()
        head, sep, tail = desc.partition(": ")
        if sep:
            desc = tail.strip()
        low = desc.lower()
        if "statically linked" in low or "static-pie linked" in low:
            return (True, desc)
        if "dynamically linked" in low:
            return (False, desc)
    needed, measured = _needed(p)
    if measured and needed:
        return (False, "dynamically linked: " + ", ".join(needed))
    proc = _run(["readelf", "-l", str(p)])
    if proc is not None and proc.returncode == 0 and proc.stdout.strip():
        if "INTERP" in proc.stdout:
            return (False, "readelf: PT_INTERP present")
        return (True, "readelf: no PT_INTERP, no DT_NEEDED")
    return (False, "unknown: no file(1) and no readelf")


def strace_available() -> bool:
    return shutil.which("strace") is not None


def _parse_trace(log: str) -> Dict[str, Any]:
    """Pull the paths a run touched out of an strace log.

    Counts SUCCESSFUL opens of DISTINCT paths, which is the closest cheap proxy
    for "files whose blocks must be fetched". Failed opens are counted
    separately rather than dropped: a probe for a file that does not exist still
    costs a directory lookup over the wire, and CPython's import machinery makes
    a great many of them.
    """
    opened: List[str] = []
    seen = set()
    failed = 0
    stat_calls = 0
    for line in log.splitlines():
        m = _OPEN_RE.match(line)
        if m:
            if _FAILED_RE.search(line):
                failed += 1
            elif m.group(1) not in seen:
                seen.add(m.group(1))
                opened.append(m.group(1))
            continue
        if _STATLIKE_RE.match(line):
            stat_calls += 1
    opened.sort()
    return {"paths": opened, "opens": len(opened), "failed_opens": failed, "stat_calls": stat_calls}


def _trace(binary: Path | str, program: str = PROBE) -> Dict[str, Any]:
    """One traced run. Never raises; ``opens is None`` means it could not be taken."""
    empty: Dict[str, Any] = {"paths": [], "opens": None, "failed_opens": None,
                             "stat_calls": None, "note": ""}
    p = Path(binary)
    if not p.is_file():
        return dict(empty, note="%s: binary not present" % UNMEASURED)
    strace = shutil.which("strace")
    if strace is None:
        # Containers routinely ship without strace or forbid ptrace. That is a
        # missing measurement, not a violation, and it must stay visible.
        return dict(empty, note="%s: strace not available" % UNMEASURED)
    env = dict(os.environ)
    env["LYPNING_CAPTURE"] = "0"  # a traced probe must not log itself into the corpus
    # The caller's PYTHONPATH is a directory the baseline interpreter then opens
    # and stats, and it belongs to whoever launched the gate rather than to the
    # interpreter being measured. Running under `PYTHONPATH=src` is enough to
    # move the CPython baseline off the 22 opens docs/MICROPYTHON.md §2 records.
    env.pop("PYTHONPATH", None)
    last = ""
    with tempfile.TemporaryDirectory(prefix="lypning-gate-") as d:
        for spec in ("trace=" + STRACE_SYSCALLS, "trace=file"):
            log = Path(d) / ("trace-%d.txt" % len(spec))
            proc = _run([strace, "-f", "-e", spec, "-o", str(log), str(p), "-c", program],
                        timeout=60.0, env=env)
            if proc is None:
                last = "strace did not run"
                continue
            try:
                text = log.read_text(errors="replace")
            except OSError:
                text = ""
            # strace writes "+++ exited +++" into the log on any successful
            # attach, so an empty log is a failed attach — usually a ptrace
            # restriction — and never a binary that opened nothing.
            if text.strip():
                return dict(_parse_trace(text), note="")
            last = (proc.stderr or "").strip().splitlines()[-1:] or ["empty trace"]
            last = last[0][:200]
    return dict(empty, note="%s: strace failed: %s" % (UNMEASURED, last))


def file_opens(binary: Path | str, program: str = PROBE) -> Tuple[Optional[int], List[str]]:
    """``(count, paths)`` for one ``-c program`` run; ``(None, [])`` when untraceable."""
    rec = _trace(binary, program)
    return (rec["opens"], list(rec["paths"]))


# --- the gate ----------------------------------------------------------------


def _engine_of(p: Path) -> str:
    """Which engine a binary is, from its name.

    The suffixed forms are cross-target builds — `lypning build --target i686`
    installs `lypning-i686` rather than overwriting the host's engine — and they
    are still the engine they are named after. Missing that put the i686 Rust
    core against lypning-mp's 700 KB budget and FAILed a build that was fine:
    two different runtimes with two different jobs, and only the opens==0 rule
    is shared between them.

    The parsing itself is :func:`engines.parse_binary_name` — the one place the
    ``<engine>[-<target>]`` shape is read, longest engine name first, which is
    what makes `lypning-mp` (and a spectrum variant) win over plain `lypning`.
    """
    return engines.parse_binary_name(p.name)[0]


def _resolve(binary: Path | str | None) -> Tuple[Optional[Path], str]:
    """The artifact to gate, and which engine it is.

    With nothing named, lypning-mp is the subject — the budget below is its
    budget. It needs a network to build and often is not present, so the Rust
    core stands in rather than the gate returning nothing at all; the
    substitution is reported as its own row so no one reads the result as
    lypning-mp's.
    """
    if binary is not None:
        p = Path(binary)
        return (p if p.is_file() else None, _engine_of(p))
    mp = engines.find_micropython()
    if mp is not None:
        return (mp, engines.MICROPYTHON)
    rust = engines.find_lypning()
    if rust is not None:
        return (rust, engines.LYPNING)
    return (None, engines.MICROPYTHON)


def _size_check(engine: str, size: int) -> Check:
    """Bytes against :data:`MAX_BYTES` — for lypning-mp and for an unnamed
    binary, which is gated as a candidate for that tier.

    The Rust core is measured and reported here but NOT failed against this
    number, and the reason is not leniency. They are different runtimes with
    different jobs: lypning-mp is MicroPython carrying a frozen Python stdlib,
    sized against the 541,688 B prototype, while lypning is a from-scratch
    subset whose bytes are its own code and whose release profile is tuned
    separately. docs/LYPNING.md §8 measured the difference in the VM and
    accepted it — 1,280 KB against 768 KB on first touch, 8 device blocks
    against 3 — as a real and known cost rather than a violation, because
    lypning buys back multiples of it on the programs it accepts. Sharing a byte
    budget between them would be inventing a number no document argues for. What
    they do share is the constraint that actually predicts cold cost: opens and
    shared objects at zero, enforced identically on both.
    """
    if engine == engines.LYPNING:
        return Check("size", size, None, True, "B",
                     "the Rust core has its own budget; %s B = %d device blocks, "
                     "not gated against lypning-mp's %s B"
                     % (format(size, ","), device_blocks(size), format(MAX_BYTES, ",")))
    return Check("size", size, MAX_BYTES, size <= MAX_BYTES, "B")


def gate(binary: Path | str | None = None, *, compare: bool = False) -> GateReport:
    """Measure one binary against the acceptance table. Never raises."""
    target, engine = _resolve(binary)
    baseline = compare_baseline() if compare else None

    if target is None:
        want = engine or engines.MICROPYTHON
        return GateReport("", [Check("built", "no", "yes", False, "",
                                     "%s is not built — `lypning build --micropython`"
                                     % want)], False, baseline)

    checks: List[Check] = []
    if binary is None and engine != engines.MICROPYTHON:
        checks.append(Check("target", engine, engines.MICROPYTHON, True, "",
                            "lypning-mp is not built; gated %s instead — these numbers "
                            "are not lypning-mp's" % engine))

    # A gate that passes a binary which cannot execute `-c 'pass'` has measured
    # a paperweight.
    probe = engines.run(engine or "gate", PROBE, binary=target, timeout=20.0)
    checks.append(Check("runs -c 'pass'", "exit %d" % probe.returncode, "exit 0",
                        probe.returncode == 0, "",
                        probe.stderr.strip().replace("\n", " ")[:200] if probe.returncode else ""))

    static, evidence = is_static(target)
    if evidence.startswith("unknown"):
        checks.append(Check("statically linked", "unknown", "static", True, "",
                            "%s: %s" % (UNMEASURED, evidence)))
    else:
        checks.append(Check("statically linked", "static" if static else "dynamic",
                            "static", static, "", evidence))

    sos, so_measured = _needed(target)
    if so_measured:
        checks.append(Check("shared objects", len(sos), MAX_SHARED_OBJECTS,
                            len(sos) <= MAX_SHARED_OBJECTS, "", ", ".join(sos)))
    else:
        checks.append(Check("shared objects", None, MAX_SHARED_OBJECTS, True, "",
                            "%s: no readelf and no ldd — zero here would be an "
                            "artefact of the toolchain, not of the build" % UNMEASURED))

    size = size_bytes(target)
    checks.append(_size_check(engine, size))
    # Informational, and deliberately not a gate: the block count is what the
    # byte count means, and a build that drops 40 KB without dropping a block
    # has not moved the cold cost at all.
    checks.append(Check("device blocks", device_blocks(size), None, True, "",
                        "%s B each" % format(DEVICE_BLOCK, ",")))

    rec = _trace(target, PROBE)
    if rec["opens"] is None:
        checks.append(Check("file opens", None, MAX_OPENS, True, "", rec["note"]))
    else:
        checks.append(Check("file opens", rec["opens"], MAX_OPENS,
                            rec["opens"] <= MAX_OPENS, "",
                            "%d failed probes, %d stat/access calls; %s"
                            % (rec["failed_opens"], rec["stat_calls"],
                               ", ".join(rec["paths"][:6]) or "no paths")))

    return GateReport(str(target), checks, all(c.ok for c in checks), baseline)


# --- the baseline ------------------------------------------------------------


def compare_baseline() -> Dict[str, Any]:
    """The same measurements against the real CPython on this box.

    This is the table that retired the original 400 KB gate: 22 files opened on
    ``-c 'pass'``, 7 more probed and missed, 65 stat calls, against 0/0/0 for a
    static binary. Those figures are not written down here — they are measured
    on every run, because the point of the row is what CPython costs *on the
    machine the comparison is being made on*, and a hardcoded 22 would keep
    reading 22 after the interpreter under it changed.

    :func:`engines.find_cpython` is what finds it, and that matters: capture
    installs a shim named ``python3`` first on ``$PATH``, and measuring the shim
    once produced a baseline of 30 file opens and a projected cold cost of 330
    seconds — worse than no baseline, because it looks like a number.
    """
    py = engines.find_cpython()
    rec: Dict[str, Any] = {
        "engine": engines.CPYTHON,
        "binary": str(py) if py else "",
        "exists": bool(py and Path(py).is_file()),
        "bytes": 0, "device_blocks": 0,
        "static": False, "linkage": "", "shared_objects": [], "shared_objects_measured": False,
        "opens": None, "paths": [], "failed_opens": None, "stat_calls": None,
        "measured": False, "note": "", "cold_ms": CPYTHON_COLD_MS,
    }
    if not rec["exists"]:
        rec["note"] = "%s: no real CPython on PATH" % UNMEASURED
        return rec
    rec["bytes"] = size_bytes(py)
    rec["device_blocks"] = device_blocks(rec["bytes"])
    static, evidence = is_static(py)
    rec["static"] = static
    rec["linkage"] = evidence
    rec["shared_objects"], rec["shared_objects_measured"] = _needed(py)
    traced = _trace(py, PROBE)
    rec.update({k: traced[k] for k in ("paths", "opens", "failed_opens", "stat_calls")})
    rec["note"] = traced["note"]
    rec["measured"] = traced["opens"] is not None
    return rec


def project_cold_ms(size: int, opens: Optional[int], baseline: Dict[str, Any]) -> Optional[int]:
    """An ESTIMATE of cold cost in the VM, from shape alone.

    Cold cost is bytes fetched and round trips taken; neither term alone explains
    it, so scale on both against the one real measurement this project has —
    CPython at 8573 ms — and take the larger, which is the pessimistic read. The
    byte term is in device blocks rather than bytes because docs/LYPNING.md §8
    measured the fetch as a step function in them.

    It exists so a build change can be judged in seconds. docs/MICROPYTHON.md §2
    accepts on a cold run in a real VM and never on this.
    """
    if opens is None or not baseline.get("measured") or not baseline.get("exists"):
        return None
    base_blocks = baseline.get("device_blocks") or 0
    base_opens = baseline.get("opens") or 0
    block_share = (device_blocks(size) / base_blocks) if base_blocks else 0.0
    open_share = (opens / base_opens) if base_opens else 0.0
    ms = baseline.get("cold_ms", CPYTHON_COLD_MS) * max(block_share, open_share)
    return int(round(ms))


# --- rendering ---------------------------------------------------------------


def _pad(s: Any, n: int) -> str:
    s = str(s)
    return s if len(s) >= n else s + " " * (n - len(s))


def _fmt(value: Any, unit: str) -> str:
    if value is None:
        return "unmeasured"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return format(value, ",") + (" " + unit if unit else "")
    return str(value)


def render(report: GateReport) -> str:
    """The human view. The only place in this module that formats for a terminal."""
    out: List[str] = []
    out.append("gate  %s" % (report.binary or "(nothing built)"))
    static = report.check("statically linked")
    if static is not None and static.note and not static.note.startswith(UNMEASURED):
        out.append("      %s" % static.note)
    out.append("")

    for c in report.checks:
        mark = "ok  " if c.ok else "FAIL"
        if not c.measured:
            mark = "--  "
        want = "" if c.limit is None else "want <= %s" % _fmt(c.limit, c.unit)
        if isinstance(c.limit, str):
            want = "want %s" % c.limit
        out.append("  %s %s %s %s" % (mark, _pad(c.name, 18), _pad(_fmt(c.value, c.unit), 22), want))

    for c in report.checks:
        if c.note:
            out.append("       %s: %s" % (c.name, c.note))

    base = report.baseline
    if base:
        out.append("")
        out.append("--- baseline: the real CPython, not gated ---")
        out.append("  %s" % (base["binary"] or "not found"))
        if base["exists"]:
            sos = (str(len(base["shared_objects"])) if base["shared_objects_measured"]
                   else "an unmeasured number of")
            out.append("  %s %s, %d device blocks, %s shared objects"
                       % (_pad("size", 18), _fmt(base["bytes"], "B"),
                          base["device_blocks"], sos))
            if base["measured"]:
                out.append("  %s %d opened, %d failed probes, %d stat/access calls"
                           % (_pad("-c 'pass'", 18), base["opens"],
                              base["failed_opens"], base["stat_calls"]))
            else:
                # Never render a missing count as a zero: a zero here is the
                # headline result for a static binary and a lie for CPython.
                out.append("  %s %s" % (_pad("-c 'pass'", 18), base["note"] or "unmeasured"))

        opens = report.value("file opens")
        size = report.value("size")
        if base["exists"] and base["measured"] and opens is not None:
            fewer = ("no opens at all" if opens == 0
                     else "%.1fx fewer" % (base["opens"] / opens))
            out.append("")
            out.append("  opens: %d vs %d (%s)" % (opens, base["opens"], fewer))
            if size and base["bytes"]:
                out.append("  bytes: %s vs %s (%.1fx smaller)"
                           % (_fmt(size, "B"), _fmt(base["bytes"], "B"), base["bytes"] / size))
            ms = project_cold_ms(int(size or 0), opens, base)
            if ms is not None:
                out.append("")
                out.append("  ESTIMATED cold cost in the VM: ~%d ms against CPython's"
                           " measured %d ms." % (ms, base["cold_ms"]))
                out.append("  A projection from shape, not a measurement."
                           " docs/MICROPYTHON.md §2 accepts")
                out.append("  on a cold run in a real VM, never on this.")

    # The verdict carries the unmeasured count with it. An unmeasurable check is
    # not a failure, but a PASS resting on three checks nobody took is a
    # different claim from a PASS resting on six that were.
    skipped = sum(1 for c in report.checks if not c.measured)
    tail = "" if not skipped else "  (%d of %d checks unmeasured)" % (skipped, len(report.checks))
    out.append("")
    out.append(("PASS" if report.ok else "FAIL") + tail)
    return "\n".join(out) + "\n"


# --- regexes, at the bottom because they are noise ---------------------------

_NEEDED_RE = re.compile(r"\(NEEDED\)\s+Shared library:\s+\[([^\]]+)\]")
# strace writes the pid two different ways: "[pid 123] call(…)" when it attaches
# and a bare "123  call(…)" under -f -o. Both must be stripped, or the whole
# trace parses as zero opens — which reads as a spectacular pass.
_PID = r"(?:\[pid\s+\d+\]\s*|\d+\s+)?"
_OPEN_RE = re.compile(r'^\s*' + _PID + r'open(?:at)?\((?:[^,]+,\s*)?"([^"]*)"')
_STATLIKE_RE = re.compile(r'^\s*' + _PID + r'(?:newfstatat|statx?|lstat|access|readlink|faccessat2?)\(')
_FAILED_RE = re.compile(r"=\s*-1\b")
