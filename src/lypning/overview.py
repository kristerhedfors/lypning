"""One answer to "what is in this repository, and which of its claims hold today".

WHY THIS IS A COMMAND AND NOT A DOCUMENT. Every number here would be stale the
day after it was written down, and invariant 3 exists because this project has
been wrong about its own numbers before — quoting a remembered corpus size, a
budget met two weeks ago, a contract that stopped being checked without anyone
noticing. A document can say `MISMATCH 0`; only a run can mean it.

So the structure is prose — it changes when the architecture changes — and every
verdict is computed. The one thing that IS written down is the list of contracts,
and that is not written down here either: it is read out of
``docs/VERIFICATION.md``, which is where the project already keeps it. A contract
added there with no runner and no pin shows up as a hole in this output rather
than as silence.

THREE KINDS OF ANSWER, kept apart on purpose:

  held / red      measured on this machine, in this run
  not measured    the check exists and this run did not pay for it; the command
                  is printed so the reader can
  no runner       the contract is described and nothing executes it — the
                  failure mode this module exists to make loud

An absent oracle is never a zero and never a pass (CLAUDE.md, the oracle-absent
contract): it is a hole with a note, here as everywhere.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import engines, gate, paths

#: `## 6. C6 — The byte budget`
_HEADING = re.compile(r"^## \d+\. (C\d+) — (.+)$", re.M)
#: ``# CHECK — `c6-gate.sh`.``
_CHECK = re.compile(r"^# CHECK — `([^`]+)`", re.M)
#: a pytest node id inside a `# PINNED BY` block
_NODE = re.compile(r"(tests/[\w/]+\.py::[\w\[\]\-.]+)")


def _root() -> Path:
    """The checkout, or None-ish behaviour in a wheel where there is no tree."""
    return Path(__file__).resolve().parents[2]


# --- the map -----------------------------------------------------------------

#: What each part of the tree is, in the order a reader needs them. The one-line
#: summaries are the module docstrings' first sentences where they exist, so
#: this cannot drift from the code without the code's own docstring drifting
#: first; the paths are checked to exist on every run.
COMPONENTS = (
    ("engine", "src/lypning/assets/rust/src", "*.rs",
     "the interpreter: parse, evaluate, refuse. `route.rs` decides which variant "
     "may run a program; `main.rs` owns the exit-90 contract."),
    ("harness", "src/lypning", "*.py",
     "everything that measures the engine — conformance, routing, gate, perf, "
     "fuzz — plus install, capture and the CLI that renders them."),
    ("corpus", "src/lypning/assets/corpus", "*.jsonl",
     "real python that real agents typed, captured in real sessions. Every "
     "grade in this project is over this."),
    ("tests", "tests", "*.py",
     "the differential suite: the engine against CPython, one program at a time."),
    ("pipeline", "nemotron/pipeline", "*.py",
     "the LoRA measurement pipeline — corpus, held-out split, eval, statistics. "
     "Imports the engine's own rules rather than restating them."),
    ("docs", "docs", "*.md",
     "VERIFICATION.md is the contract list; HILLCLIMB.md is the ledger of every "
     "measured step; the rest is background."),
)


def component_map(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Each component with its real file and line counts, or a note that it is absent."""
    root = root or _root()
    out = []
    for name, rel, glob, summary in COMPONENTS:
        d = root / rel
        if not d.is_dir():
            out.append({"name": name, "path": rel, "present": False, "files": 0,
                        "lines": 0, "summary": summary})
            continue
        files = sorted(d.glob(glob))
        lines = 0
        for f in files:
            try:
                lines += f.read_text(encoding="utf-8", errors="replace").count("\n")
            except OSError:
                pass
        out.append({"name": name, "path": rel, "present": True, "files": len(files),
                    "lines": lines, "summary": summary})
    return out


# --- the contracts -----------------------------------------------------------


def contracts(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Every contract `docs/VERIFICATION.md` declares, with its runner and pins.

    Read out of the document rather than listed here, because a second list is
    the one that goes stale — and a contract with no runner or no pin is the
    thing this is looking for, not an edge case.
    """
    root = root or _root()
    doc = root / "docs" / "VERIFICATION.md"
    if not doc.is_file():
        return []
    text = doc.read_text(encoding="utf-8")
    marks = [(m.group(1), m.group(2), m.start()) for m in _HEADING.finditer(text)]
    out = []
    for i, (cid, title, start) in enumerate(marks):
        end = marks[i + 1][2] if i + 1 < len(marks) else len(text)
        body = text[start:end]
        check = _CHECK.search(body)
        pinned = sorted(set(_NODE.findall(body)))
        missing = [n for n in pinned
                   if not (root / n.split("::", 1)[0]).is_file()]
        out.append({
            "id": cid,
            "title": title,
            "check": check.group(1) if check else None,
            "pinned": pinned,
            "pins_missing_file": missing,
        })
    return out


#: pytest's exit codes, which are the whole difference between "passed" and
#: "did not run". 0 all passed, 1 some failed, 2 interrupted, 3 internal error,
#: 4 usage error, 5 nothing collected.
_PYTEST_PASSED, _PYTEST_FAILED, _PYTEST_NOTHING = 0, 1, 5


def _pytest_cmd() -> Optional[List[str]]:
    """An interpreter that can actually import pytest, or None.

    THE DEFECT THIS EXISTS FOR, found 2026-09-12 by disbelieving this module's
    own output: `python3 -m pytest` under the CLI's interpreter answers
    `No module named pytest` on stderr, exit 1, and prints no `FAILED` line.
    Both readers below parsed that as "no failures" and reported every contract
    held and the suite green while 57 tests were failing. Absence of evidence
    was being read as evidence, in the one tool whose whole purpose is not to.
    """
    for argv in ([sys.executable, "-m", "pytest"],
                 ["uv", "run", "--with", "pytest", "python", "-m", "pytest"]):
        try:
            probe = subprocess.run(argv + ["--version"], capture_output=True,
                                   text=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return argv
    return None


def run_pins(nodes: List[str], root: Optional[Path] = None,
             timeout: float = 900.0) -> Dict[str, Any]:
    """Run pinned tests and report pass/fail per node id.

    pytest, not a reimplementation of it: these are the project's own pins and
    the only honest verdict is the one its own runner gives.
    """
    root = root or _root()
    if not nodes:
        return {"ran": False, "reason": "no pins", "failed": [], "passed": []}
    base = _pytest_cmd()
    if base is None:
        return {"ran": False, "reason": "no interpreter here can import pytest",
                "failed": [], "passed": []}
    cmd = base + ["-q", "--no-header", "-p", "no:cacheprovider"] + nodes
    try:
        p = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True,
                           timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ran": False, "reason": str(exc), "failed": [], "passed": []}
    # A verdict comes from the EXIT CODE, never from the absence of a FAILED
    # line: pytest that could not start prints neither.
    if p.returncode not in (_PYTEST_PASSED, _PYTEST_FAILED):
        tail = (p.stderr or p.stdout).strip().splitlines()
        return {"ran": False,
                "reason": "pytest exited %d: %s" % (p.returncode, tail[-1][:160] if tail else ""),
                "failed": [], "passed": []}
    failed = sorted(set(_NODE.findall(
        "\n".join(l for l in p.stdout.splitlines() if l.startswith("FAILED")))))
    return {"ran": True, "reason": "", "returncode": p.returncode,
            "failed": failed, "passed": [n for n in nodes if n not in failed]}


# --- the state ---------------------------------------------------------------


def binaries() -> List[Dict[str, Any]]:
    """Each variant: built or not, its size, and its block count against ITS budget.

    `lypning-mp` is the oracle and is measured, never routed to (invariant 9).
    Absent, it is a hole with a note — never a zero and never a pass.
    """
    out = []
    # Through `engines`, never spelled here: invariant 9 says a name that still
    # resolves is a name that can drift back into the code, and
    # `tests/test_engines.py` enforces it. The oracle is appended rather than
    # being in SPECTRUM because nothing routes to it.
    for name in engines.SPECTRUM + (engines.MICROPYTHON,):
        found = engines.find(name)
        path = str(found) if found else None
        row: Dict[str, Any] = {"name": name, "path": path,
                               "oracle": name == engines.MICROPYTHON}
        if not found or not found.is_file():
            row.update({"built": False, "bytes": None, "blocks": None,
                        "budget": gate.VARIANT_BLOCK_BUDGET.get(name),
                        "over": None})
            out.append(row)
            continue
        size = found.stat().st_size
        blocks = gate.device_blocks(size)
        budget = gate.VARIANT_BLOCK_BUDGET.get(name)
        row.update({"built": True, "bytes": size, "blocks": blocks,
                    "budget": budget,
                    "over": (budget is not None and blocks > budget)})
        out.append(row)
    return out


def suite(root: Optional[Path] = None, timeout: float = 1800.0) -> Dict[str, Any]:
    """The whole differential suite, grouped by file.

    Separate from the contract pins on purpose, and the distinction is the one
    this module exists to keep straight: a contract's pins holding says its
    MECHANISM is right, and says nothing about whether this tree passes. C6's
    three pins pass while `lypning gate` is red, because they check that
    `device_blocks` rounds up and that a variant is measured against its own
    budget — not that the binary on disk is under it.
    """
    root = root or _root()
    base = _pytest_cmd()
    if base is None:
        return {"ran": False, "reason": "no interpreter here can import pytest",
                "failed": [], "by_file": {}}
    cmd = base + ["-q", "--no-header", "-p", "no:cacheprovider", "tests"]
    try:
        p = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True,
                           timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ran": False, "reason": str(exc), "failed": [], "by_file": {}}
    if p.returncode not in (_PYTEST_PASSED, _PYTEST_FAILED):
        tail = (p.stderr or p.stdout).strip().splitlines()
        return {"ran": False,
                "reason": "pytest exited %d: %s" % (p.returncode, tail[-1][:160] if tail else ""),
                "failed": [], "by_file": {}}
    failed = sorted(
        line.split(None, 1)[1].split(" - ")[0].strip()
        for line in p.stdout.splitlines() if line.startswith("FAILED ")
    )
    by_file: Dict[str, int] = {}
    for node in failed:
        by_file[node.split("::", 1)[0]] = by_file.get(node.split("::", 1)[0], 0) + 1
    tail = [l for l in p.stdout.splitlines() if " passed" in l or " failed" in l]
    return {"ran": True, "reason": "", "failed": failed, "by_file": by_file,
            "summary": tail[-1] if tail else ""}


def corpus_size() -> Optional[int]:
    """The count this run loaded — never a remembered one (invariant 3).

    Through `corpus.load_default`, not by counting lines in `corpus.jsonl`.
    The two disagree — 3,688 against 3,525 on 2026-09-12 — because the loader
    reads the sightings the capture harness files alongside the corpus, and a
    number this module printed that no other tool in the tree agreed with would
    be worse than printing nothing.
    """
    from . import corpus as corpusmod

    try:
        return len(corpusmod.load_default())
    except Exception:
        return None
