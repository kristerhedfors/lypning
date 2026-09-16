"""A legacy `nt eval` run as `training_metrics` rows, so both eval-2 homes report
through one summariser.

`EVAL2.md` §3: the task-first path writes one row per (case, draw) with
``correct`` and ``native`` (`gpu/verified_evaluation.py`), and
`training_metrics.summarize` / `paired_comparison` read those rows. The legacy
tree records an ``attempts.jsonl`` per run instead, graded on one ``stdout``
test, and knows nothing about families. This module makes the two meet:

``correct``   the attempt's own ``passed`` — CPython reproduced the expected
              stdout of the case's one projected test.
``native``    read off the legality replay, never off the run: the pinned
              engine ran the program (verdict ``MATCH``) and its own output was
              correct. A ``passed`` flag paired with a verdict from another
              binary would be two engines in one number (`legality.arm`).
``family``, ``population``, ``capabilities``, ``split_group``
              read back from the case's tags as `eval2_legacy` wrote them;
              ``split_group`` is the bank's ``source_group`` and falls back to
              the family, the same default the task-first path uses.
``seed``      the run's recorded sampling seed plus the draw index, which is
              what `evaluate.Evaluation` sent; None when the run had none.

Every attempt becomes a row — a harness error or a no-code draw included, with
``correct`` and ``native`` False and a ``status`` that says why — because the
summariser refuses unequal draw counts per case, and a draw that failed to
happen is still a draw the arm was budgeted.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from .eval2_legacy import read_tags

STATUS_HARNESS = "harness-error"
STATUS_NO_CODE = "no-code"
STATUS_NATIVE = "correct-native"
STATUS_FALLBACK = "correct-fallback"
STATUS_INCORRECT = "incorrect"


def _replay_index(replay_rows: Iterable[Dict[str, Any]]) -> Dict[Tuple[Any, Any], Dict[str, Any]]:
    out: Dict[Tuple[Any, Any], Dict[str, Any]] = {}
    for r in replay_rows:
        out[(r.get("case_id"), r.get("sample"))] = r
    return out


def row_for(attempt: Dict[str, Any], case: Dict[str, Any],
            replay: Optional[Dict[str, Any]], seed: Optional[int]) -> Dict[str, Any]:
    meta = read_tags(case.get("tags"))
    family = meta["family"] or case.get("id")
    draw = int(attempt.get("sample") or 0)
    correct = bool(attempt.get("passed"))
    native = bool(replay and replay.get("verdict") == "MATCH" and replay.get("correct") is True)
    if attempt.get("harness_error"):
        status = STATUS_HARNESS
    elif not attempt.get("program"):
        status = STATUS_NO_CODE
    elif native:
        status = STATUS_NATIVE
    elif correct:
        status = STATUS_FALLBACK
    else:
        status = STATUS_INCORRECT
    return {
        "case_id": case.get("source_id") or case.get("id"),
        "corpus_id": case.get("id"),
        "draw": draw,
        "family": family,
        "population": meta["population"] or "coverage",
        "capabilities": meta["capabilities"],
        "seed": (None if seed is None else int(seed) + draw),
        "split_group": meta["split_group"] or family,
        "correct": correct,
        "native": native,
        "status": status,
        "verdict": (replay or {}).get("verdict"),
        "completion_tokens": int(attempt.get("completion_tokens") or 0),
        "truncated": attempt.get("finish_reason") == "length",
    }


def rows(attempts: List[Dict[str, Any]], replay_rows: Iterable[Dict[str, Any]],
         cases: Dict[str, Dict[str, Any]], *, seed: Optional[int] = None) -> Dict[str, Any]:
    """Every attempt of one run as a metrics row. Returns the rows and what
    could not be placed: an attempt whose case this tree does not know."""
    by_key = _replay_index(replay_rows)
    out: List[Dict[str, Any]] = []
    unknown: List[str] = []
    for a in attempts:
        cid = a.get("case_id")
        case = cases.get(cid)
        if case is None:
            unknown.append(str(cid))
            continue
        out.append(row_for(a, case, by_key.get((cid, a.get("sample"))), seed))
    return {"rows": out, "unknown_cases": sorted(set(unknown)),
            "replayed": sum(1 for r in out if r["verdict"] is not None)}
