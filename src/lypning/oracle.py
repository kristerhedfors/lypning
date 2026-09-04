"""lypning-mp as an ORACLE: what a second reimplementation of Python got wrong.

lypning-mp was tier 2 until 2026-09-04. It is not a routing destination any
more — nothing falls through to it, and `engines.ENGINE_ORDER` does not list it
— but deleting it would have thrown away the most expensive thing this project
owns: a second, independent, from-scratch implementation of Python that has been
run against CPython over the whole corpus and had every disagreement written
down.

That is the question a Rust variant needs answered before it implements
anything. `lypning-l` is a reimplementation too, and the constructs a
reimplementation gets wrong are not evenly distributed — they cluster in float
formatting, sort stability, hash order, error-message text, and the places where
CPython's behaviour is defined by its own internals rather than by the language.
The oracle is the measured list of those clusters, so a build order can be
chosen against evidence instead of taste.

**What the oracle may not do.** It never widens a capability table (invariant 1):
a family here is a reason to implement something exactly or to refuse it, never
a reason to answer approximately. It is never a substitute for the CPython
reference — `conformance` still grades against real CPython and MISMATCH must
still be 0. And it never gates a build: the oracle binary is absent on almost
every machine (it needs a 32-bit toolchain and a network), so a missing oracle
is a hole in the report, never a zero.

The data is `.github/known-mismatches.json`, which the MicroPython CI job
already maintains by identity — a divergence that stops reproducing reddens
that job, so the catalogue cannot quietly rot.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import engines, paths

#: The recorded divergences. Written and pruned by the micropython-conformance
#: job through `.github/scripts/known-mismatches.py`.
LEDGER = "known-mismatches.json"


def ledger_path(root: Optional[Path] = None) -> Path:
    """Where the catalogue lives — the source checkout's ``.github/``.

    Deliberately not a package asset. The ledger is maintained by the CI job
    that runs the oracle, by identity, and a copy shipped in the wheel would be
    a second one nothing prunes. A wheel therefore has no catalogue and says so
    (:func:`render`), which is the honest answer: the oracle is a development
    instrument, and "no data here" is a hole, not a clean bill.
    """
    base = Path(root) if root is not None else paths.PACKAGE_ROOT.parent.parent
    return base / ".github" / LEDGER


def load(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Every accepted divergence, or ``[]`` when the ledger is unreadable.

    Empty is a HOLE, never a zero: the caller says "not available", because a
    silent empty catalogue reads as "a reimplementation gets nothing wrong",
    which is the most wrong thing this file could say.
    """
    p = ledger_path() if path is None else Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data.get("accepted")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def families(rows: Optional[List[Dict[str, Any]]] = None,
             engine: str = engines.MICROPYTHON) -> List[Tuple[str, int, str]]:
    """``(family, programs, why)`` for one engine, most programs first.

    Ranked by how many corpus programs the trap covers, because that is what
    decides how much it matters to get right — the same reason
    ``conformance --plan`` ranks by cost rather than by count of distinct
    blockers.
    """
    rows = load() if rows is None else rows
    by: Dict[str, Tuple[int, str]] = {}
    for r in rows:
        if engine and r.get("engine") != engine:
            continue
        fam = str(r.get("family") or "unclassified")
        n, why = by.get(fam, (0, ""))
        by[fam] = (n + 1, why or str(r.get("why") or ""))
    out = [(f, n, w) for f, (n, w) in by.items()]
    out.sort(key=lambda t: (-t[1], t[0]))
    return out


def render(rows: Optional[List[Dict[str, Any]]] = None, full: bool = False) -> str:
    """The catalogue, as a build-order briefing for a larger Rust variant."""
    rows = load() if rows is None else rows
    out: List[str] = []
    if not rows:
        out.append("oracle: no catalogue — %s is unreadable." % ledger_path())
        out.append("  This is a hole, not a clean bill: it means nothing is known here,")
        out.append("  never that a reimplementation gets nothing wrong.")
        return "\n".join(out) + "\n"

    fams = families(rows)
    total = sum(n for _, n, _ in fams)
    built = engines.find(engines.MICROPYTHON)
    out.append("oracle — lypning-mp against CPython: %d recorded divergences in %d families"
               % (total, len(fams)))
    out.append("")
    out.append("lypning-mp is a second, independent reimplementation of Python and is NOT a")
    out.append("tier: nothing routes to it. What it gets wrong is evidence of what a")
    out.append("reimplementation gets wrong — so every family below is something a larger")
    out.append("Rust variant must implement EXACTLY or refuse. Never approximate one.")
    out.append("")
    out.append("  %-30s %5s  %s" % ("family", "progs", "what it teaches"))
    for fam, n, why in fams:
        first = (why or "").strip().replace("\n", " ")
        if not full and len(first) > 96:
            first = first[:95] + "…"
        out.append("  %-30s %5d  %s" % (fam, n, first))
    out.append("")
    out.append("binary: %s" % (built if built else
                               "not built — the catalogue above is read from the ledger, "
                               "which is what makes it useful without a 32-bit toolchain"))
    out.append("ledger: %s (maintained by identity; a divergence that stops reproducing "
               "reddens CI)" % ledger_path())
    return "\n".join(out) + "\n"
