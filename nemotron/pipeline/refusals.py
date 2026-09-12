"""What the engine still refuses, ranked by how much of the corpus it blocks.

Every case in the corpus carries the program that provoked it — the *negative*,
correct python that lypning declines and hands to CPython. Running the negatives
back through the live engine answers the only question that decides where engine
work goes next: which single missing construct is standing in front of the most
real programs.

THE COLUMN THAT MATTERS IS `closed`. A refusal is closed when the kind appears
in :data:`lypning.engines.ONLY_CPYTHON_REFUSALS` — the repository's own declared
list, whose docstring says an entry there "is a claim that no reimplementation
short of CPython gets the construct right". Those cases are not a backlog. They
are the corpus's counterweight: the model's job on them is to rewrite, and the
engine's job is to keep refusing. Counting them as work remaining would put the
whole set-order family on a roadmap, and the first entry delivered would be a
silent wrong answer.

The list is imported, never restated. A second copy of it is the one that goes
stale, and stale here means an invariant-1 violation shipped as a feature.
"""

from __future__ import annotations

import subprocess
from typing import Any, Dict, List, Optional

from . import engines as eng
from .jsonio import read_jsonl


def closed_kinds() -> "frozenset[str]":
    """The kinds no Rust variant may answer, read from the engine's own list."""
    from lypning.engines import ONLY_CPYTHON_REFUSALS  # the one copy

    return frozenset(ONLY_CPYTHON_REFUSALS)


def probe(program: str, engine: str, timeout_s: float = 10.0) -> Optional[Dict[str, str]]:
    """Refusal kind and detail for one program, or None if the engine took it.

    No sandbox: `-c` with no stdin and no argv never reaches the program's own
    side effects, because the engine decides to refuse before it runs anything
    that could have one. A program the engine *accepts* does run here, which is
    why this is only ever pointed at corpus negatives — already vetted by
    `lypning.conformance`'s own skip rules on the way into the corpus.
    """
    try:
        p = subprocess.run([engine, "-c", program], capture_output=True,
                           text=True, timeout=timeout_s)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != eng.REFUSAL_EXIT:
        return None
    parsed = eng.parse_refusal(p.stderr)
    if parsed is None:
        return None
    _, kind, detail = parsed
    return {"kind": kind, "detail": detail}


def census(cases: List[Dict[str, Any]], engine: str) -> Dict[str, Any]:
    """Per-kind counts over the corpus negatives, with the closed kinds marked.

    One negative per case, not all of them: the unit is the case, because the
    case is what the eval scores and what a delivered construct retires.
    """
    closed = closed_kinds()
    by_kind: Dict[str, Dict[str, Any]] = {}
    accepted: List[str] = []
    no_negative = 0

    for case in cases:
        negatives = case.get("negatives") or []
        if not negatives:
            no_negative += 1
            continue
        found = probe(negatives[0]["program"], engine)
        if found is None:
            accepted.append(case["id"])
            continue
        row = by_kind.setdefault(found["kind"], {
            "kind": found["kind"],
            "closed": found["kind"] in closed,
            "cases": [],
            "details": {},
        })
        row["cases"].append(case["id"])
        row["details"][found["detail"]] = row["details"].get(found["detail"], 0) + 1

    kinds = sorted(by_kind.values(), key=lambda r: (r["closed"], -len(r["cases"]), r["kind"]))
    refused = sum(len(r["cases"]) for r in kinds)
    return {
        "engine": engine,
        "cases": len(cases),
        "no_negative": no_negative,
        "accepted": accepted,
        "refused": refused,
        "open_cases": sum(len(r["cases"]) for r in kinds if not r["closed"]),
        "closed_cases": sum(len(r["cases"]) for r in kinds if r["closed"]),
        "kinds": kinds,
    }


def report(result: Dict[str, Any], *, limit: int = 0, show_details: bool = False) -> str:
    """Render a census. The open kinds are the build order; the closed ones are not."""
    lines = ["engine %s   %d cases, %d carry a negative program"
             % (result["engine"], result["cases"], result["cases"] - result["no_negative"])]
    if result["accepted"]:
        lines.append("%d of them the engine now ACCEPTS — those cases are retired: %s"
                     % (len(result["accepted"]), " ".join(sorted(result["accepted"])[:6])
                        + (" ..." if len(result["accepted"]) > 6 else "")))
    lines.append("")
    lines.append("%-20s %6s  %s" % ("kind", "cases", "status"))
    shown = result["kinds"][:limit] if limit else result["kinds"]
    for row in shown:
        lines.append("%-20s %6d  %s"
                     % (row["kind"], len(row["cases"]),
                        "closed — no reimplementation may answer it" if row["closed"] else "open"))
        if show_details:
            for detail, n in sorted(row["details"].items(), key=lambda kv: -kv[1]):
                lines.append("%-20s %6d    %s" % ("", n, detail[:110]))
    if limit and len(result["kinds"]) > limit:
        lines.append("%-20s %6s  (%d more kinds not shown)"
                     % ("", "", len(result["kinds"]) - limit))
    lines.append("")
    lines.append("%d cases blocked by OPEN kinds — the engine build order" % result["open_cases"])
    lines.append("%d cases blocked by CLOSED kinds — the model's job is to rewrite these,"
                 % result["closed_cases"])
    lines.append("   and the engine's job is to go on refusing them")
    return "\n".join(lines)

def grade_against_engine(program: str, engine: str, timeout_s: float = 10.0) -> Dict[str, str]:
    """One program, judged against CPython: MATCH, UNSUPPORTED, MISMATCH or ERROR.

    Behind the sandbox, because unlike :func:`probe` this RUNS what the engine
    accepts, and what it is pointed at is model output rather than a vetted
    corpus entry — the one population nobody has read.
    """
    from . import sandbox

    truth = sandbox.run_python(program, timeout_s=timeout_s)
    got = sandbox.run_python(program, timeout_s=timeout_s, interpreter=[engine])
    if got.exit_code == eng.REFUSAL_EXIT:
        parsed = eng.parse_refusal(got.stderr)
        return {"verdict": "UNSUPPORTED",
                "detail": parsed[1] if parsed else "?",
                "blocker": ("%s: %s" % (parsed[1], parsed[2])) if parsed else "?"}
    if got.harness_error or truth.harness_error:
        return {"verdict": "ERROR", "detail": got.harness_error or truth.harness_error or ""}
    if (got.exit_code, got.stdout) == (truth.exit_code, truth.stdout):
        return {"verdict": "MATCH", "detail": ""}
    return {"verdict": "MISMATCH",
            "detail": "exit %s vs %s" % (truth.exit_code, got.exit_code)}


def on_policy(attempts: List[Dict[str, Any]], engine: str) -> Dict[str, Any]:
    """What the engine makes of the programs a MODEL wrote, not the ones it was given.

    THE CORPUS'S BLIND SPOTS ARE SHAPED LIKE ITS CAPTURE MECHANISM. `conformance`
    grades programs real agents typed, so `MISMATCH 0` there means no
    disagreement among the shapes a human reached for. A model reaches for
    others: a `try` block truncated by a token cap, and an `is` comparison
    written to route around a refusal it already met. Both were live MISMATCHes
    in this engine with every gate green, and both are in here.

    It is also the number the fine-tune is trying to move, measured from the
    other end — the engine side of `pass@1` on a `lypning`-kind acceptance test.
    """
    rows: List[Dict[str, Any]] = []
    tally: Dict[str, int] = {}
    details: Dict[str, int] = {}
    blockers: Dict[str, int] = {}
    for attempt in attempts:
        program = attempt.get("program")
        if not program:
            continue
        graded = grade_against_engine(program, engine)
        rows.append({"case_id": attempt.get("case_id"), "sample": attempt.get("sample"),
                     "passed": bool(attempt.get("passed")), **graded})
        tally[graded["verdict"]] = tally.get(graded["verdict"], 0) + 1
        if graded["verdict"] == "UNSUPPORTED":
            details[graded["detail"]] = details.get(graded["detail"], 0) + 1
            # The kind is the table row; the BLOCKER is the build order. `module`
            # says nothing about what to write next, `module: import datetime`
            # does, and the two rank differently because one kind can hold a
            # dozen unrelated features.
            blocker = graded.get("blocker") or graded["detail"]
            blockers[blocker] = blockers.get(blocker, 0) + 1
    return {"engine": engine, "programs": len(rows), "tally": tally,
            "details": details, "blockers": blockers, "rows": rows}


def on_policy_report(result: Dict[str, Any], *, limit: int = 12) -> str:
    """Render an on-policy census. MISMATCH is the line that matters."""
    tally = result["tally"]
    n = result["programs"] or 1
    lines = ["engine %s   %d programs a model wrote" % (result["engine"], result["programs"])]
    for verdict in ("MATCH", "UNSUPPORTED", "MISMATCH", "ERROR"):
        if verdict in tally:
            lines.append("  %-12s %5d  %5.1f%%" % (verdict, tally[verdict], 100.0 * tally[verdict] / n))
    if tally.get("MISMATCH"):
        lines.append("")
        lines.append("MISMATCH is never traded (invariant 1). Each one is a silent wrong")
        lines.append("answer this engine gave a program CPython answers differently:")
        for row in result["rows"]:
            if row["verdict"] == "MISMATCH":
                lines.append("  %s sample %s  %s" % (row["case_id"], row["sample"], row["detail"]))
    lines.append("")
    lines.append("what it refused, by kind:")
    for kind, count in sorted(result["details"].items(), key=lambda kv: -kv[1])[:limit]:
        lines.append("  %-20s %5d" % (kind, count))
    closed = closed_kinds()
    lines.append("")
    lines.append("the build order — one missing feature per row, open kinds only:")
    ranked = [(b, n) for b, n in sorted(result.get("blockers", {}).items(), key=lambda kv: -kv[1])
              if b.split(":")[0] not in closed]
    for blocker, count in ranked[:limit]:
        lines.append("  %5d  %s" % (count, blocker[:98]))
    return "\n".join(lines)
