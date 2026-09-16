"""Bank-vs-bank contamination: every training case that is an eval-2 case in disguise.

`split.cross_split_leaks` asks this of one corpus cut in two; eval-2 is a
separate bank in the schema-3 shape, and the training bank it must be
independent of is another file in the same shape. Disjoint ids buy nothing
across files any more than within one, so this module asks the same three
questions over the schema-3 fields — task text, test stdouts, the reference —
and two more that schema 3 makes askable: a shared ``source_group`` (the
declared provenance that `training_data.split_cases` already keeps in one
component) and a shared ``review.evidence_ids`` entry (two cases reverse-
prompted from the same capture).

Five rules, each catching a shape the others miss:

``task``         difflib ratio >= 0.85 on whitespace- and case-normalised task
                 text — the reworded twin.
``stdout``       an identical expected stdout on any test of at least
                 ``MIN_STDOUT_CHARS`` characters — the same task under a
                 different prompt. Short outputs are ignored because the corpus
                 audit found ``0\\n``, ``True\\n`` and friends collide across
                 unrelated tasks.
``fingerprint``  `training_data.solution_fingerprint` of the reference is equal
                 — the same solution, whatever it was asked as.
``source_group`` the two cases declare the same source.
``evidence``     the two cases cite the same evidence id.

The module reads both banks and writes nothing. It returns data; the CLI's
``eval2-leaks`` verb renders the table and maps "any pair" onto exit 1.
"""

from __future__ import annotations

import difflib
from typing import Any, Dict, List, Optional, Set, Tuple

from .split import SIMILARITY_CEILING
from .training_data import solution_fingerprint
from .training_types import TrainingError

#: Below this many characters an expected stdout is too common to be evidence.
MIN_STDOUT_CHARS = 8

RULES: Tuple[str, ...] = ("task", "stdout", "fingerprint", "source_group", "evidence")


def normalize_text(text: Any) -> str:
    """Case-folded, single-spaced: the two edits that never change a task."""
    if not isinstance(text, str):
        return ""
    return " ".join(text.lower().split())


def _stdouts(case: Dict[str, Any], min_chars: int) -> Set[str]:
    out: Set[str] = set()
    for test in case.get("tests") or []:
        if not isinstance(test, dict):
            continue
        s = test.get("stdout")
        if isinstance(s, str) and len(s) >= min_chars:
            out.add(s)
    return out


def _evidence(case: Dict[str, Any]) -> Set[str]:
    review = case.get("review")
    if not isinstance(review, dict):
        return set()
    ids = review.get("evidence_ids")
    if not isinstance(ids, list):
        return set()
    return {e for e in ids if isinstance(e, str) and e}


def _fingerprint(case: Dict[str, Any]) -> Optional[str]:
    reference = case.get("reference")
    if not isinstance(reference, str):
        return None
    try:
        return solution_fingerprint(reference)
    except TrainingError:
        return None


def _prepared(case: Dict[str, Any], min_chars: int) -> Dict[str, Any]:
    return {
        "id": str(case.get("case_id", "")),
        "task": normalize_text(case.get("task")),
        "stdouts": _stdouts(case, min_chars),
        "fingerprint": _fingerprint(case),
        "source_group": case.get("source_group") or None,
        "evidence": _evidence(case),
    }


def bank_leaks(
    eval_cases: List[Dict[str, Any]],
    train_cases: List[Dict[str, Any]],
    *,
    ceiling: float = SIMILARITY_CEILING,
    min_stdout_chars: int = MIN_STDOUT_CHARS,
) -> Dict[str, Any]:
    """Every (eval-2, training) pair that trips at least one rule.

    Returns ``pairs`` (one row per pair, with the rules it tripped and the task
    similarity), ``eval2_cases_with_any_leak`` (sorted ids), ``by_rule`` (pair
    counts per rule, every rule present even at zero), ``clean`` (eval-2 ids
    with no pair) and ``unparsable`` (ids on either side whose reference did
    not parse, so the fingerprint rule could not be asked of them — listed
    rather than silently skipped).
    """
    ev = [_prepared(c, min_stdout_chars) for c in eval_cases]
    tr = [_prepared(c, min_stdout_chars) for c in train_cases]
    unparsable = sorted({p["id"] for p in ev + tr if p["fingerprint"] is None
                         and p["id"]})

    pairs: List[Dict[str, Any]] = []
    by_rule: Dict[str, int] = {r: 0 for r in RULES}
    for e in ev:
        for t in tr:
            rules: List[str] = []
            detail: List[str] = []
            ratio = 0.0
            if e["task"] and t["task"]:
                ratio = difflib.SequenceMatcher(None, e["task"], t["task"]).ratio()
            if ratio >= ceiling:
                rules.append("task")
                detail.append("task %.3f" % ratio)
            shared_out = e["stdouts"] & t["stdouts"]
            if shared_out:
                rules.append("stdout")
                detail.append("identical stdout x%d" % len(shared_out))
            if e["fingerprint"] and e["fingerprint"] == t["fingerprint"]:
                rules.append("fingerprint")
                detail.append("identical reference fingerprint")
            if e["source_group"] and e["source_group"] == t["source_group"]:
                rules.append("source_group")
                detail.append("source_group %s" % e["source_group"])
            shared_ev = sorted(e["evidence"] & t["evidence"])
            if shared_ev:
                rules.append("evidence")
                detail.append("evidence %s" % ",".join(shared_ev))
            if not rules:
                continue
            for r in rules:
                by_rule[r] += 1
            pairs.append({
                "eval2_id": e["id"],
                "train_id": t["id"],
                "rules": rules,
                "similarity": round(ratio, 4),
                "why": ", ".join(detail),
            })

    leaked = sorted({p["eval2_id"] for p in pairs})
    return {
        "ceiling": ceiling,
        "min_stdout_chars": min_stdout_chars,
        "n_eval2": len(ev),
        "n_train": len(tr),
        "pairs": pairs,
        "eval2_cases_with_any_leak": leaked,
        "n_eval2_cases_with_any_leak": len(leaked),
        "by_rule": by_rule,
        "clean": [p["id"] for p in ev if p["id"] not in set(leaked)],
        "unparsable": unparsable,
    }


def render(report: Dict[str, Any]) -> str:
    """The table the CLI prints. A string, so the decision stays testable."""
    lines = [
        "eval-2 %d, training %d, task ceiling %.2f, stdout floor %d chars"
        % (report["n_eval2"], report["n_train"], report["ceiling"],
           report["min_stdout_chars"]),
        "%d pairs; %d of %d eval-2 cases leak; %d are clean"
        % (len(report["pairs"]), report["n_eval2_cases_with_any_leak"],
           report["n_eval2"], len(report["clean"])),
        "by rule: " + "  ".join("%s %d" % (r, report["by_rule"].get(r, 0))
                                for r in RULES),
    ]
    if report["unparsable"]:
        lines.append("fingerprint not asked of %d unparsable reference(s): %s"
                     % (len(report["unparsable"]), " ".join(report["unparsable"])))
    if report["pairs"]:
        lines.append("")
        w_e = max(len("eval-2"), max(len(p["eval2_id"]) for p in report["pairs"]))
        w_t = max(len("training"), max(len(p["train_id"]) for p in report["pairs"]))
        lines.append("  %-*s  %-*s  %-6s  %s" % (w_e, "eval-2", w_t, "training",
                                                 "sim", "rules"))
        rows = sorted(report["pairs"],
                      key=lambda p: (-len(p["rules"]), -p["similarity"],
                                     p["eval2_id"], p["train_id"]))
        for p in rows:
            lines.append("  %-*s  %-*s  %.3f  %s" % (w_e, p["eval2_id"], w_t,
                                                     p["train_id"], p["similarity"],
                                                     p["why"]))
    return "\n".join(lines)
