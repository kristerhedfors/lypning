"""Project a schema-3 eval-2 bank onto the legacy `nt` tree's ``jsonl`` adapter.

`EVAL2.md` §3 gives the bank two homes. The task-first path grades a case on
every one of its ``tests``; the legacy tree grades one ``stdout``-kind test per
case and exists for the exploratory numbers only — the base-rate pilot, the
power analysis, the SLR by-kind census. This module is the bridge between the
two shapes, and each of its rules exists because the alternative silently
changes what the legacy tree measures:

``category``    always ``unobserved``, the only :func:`classify.is_category`
                value an ordinary task may carry: nothing has failed it yet.
``test.kind``   ``stdout`` with ``normalize: exact``, never ``lypning``. The
                ``lypning`` kind defaults ``require_tier1`` to True and would
                fail a draw that is correct and refused — the draw the eval-2
                metric must count as correct-but-not-native, not as wrong.
``expect_stdout``  non-empty, because the empty program prints nothing and the
                ``discriminates`` gate drops a test the empty program passes.
one record      per case — the first test with a non-empty stdout. One record
                per test would hand the case bootstrap k inputs of one task as
                k independent cases.
``id``          the bank's ``case_id``, which the corpus keeps as ``source_id``
                (the corpus id is a hash of prompt and test, `schema.case_id`).
``tags``        ``family:``, ``group:``, ``population:`` and one ``capability:``
                per label, so `eval2_rows` can read the case back.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

CATEGORY = "unobserved"
TAG_FAMILY = "family:"
TAG_GROUP = "group:"
TAG_POPULATION = "population:"
TAG_CAPABILITY = "capability:"


def _first_testable(tests: Any) -> Optional[Dict[str, Any]]:
    """The first test whose expected stdout is a non-empty string."""
    for t in tests or []:
        if isinstance(t, dict) and isinstance(t.get("stdout"), str) and t["stdout"]:
            return t
    return None


def tags_for(case: Dict[str, Any]) -> List[str]:
    tags = [TAG_FAMILY + str(case.get("family") or "")]
    if case.get("source_group"):
        tags.append(TAG_GROUP + str(case["source_group"]))
    if case.get("population"):
        tags.append(TAG_POPULATION + str(case["population"]))
    for cap in case.get("capabilities") or []:
        tags.append(TAG_CAPABILITY + str(cap))
    return tags


def to_record(case: Dict[str, Any]) -> Dict[str, Any]:
    """One bank case to one ``jsonl``-adapter record, or ``{"skipped": why}``."""
    cid = case.get("case_id")
    if not isinstance(cid, str) or not cid:
        return {"skipped": "no case_id"}
    task = case.get("task")
    if not isinstance(task, str) or not task.strip():
        return {"skipped": "no task"}
    test = _first_testable(case.get("tests"))
    if test is None:
        return {"skipped": "no test with a non-empty stdout"}
    spec: Dict[str, Any] = {"kind": "stdout", "normalize": "exact",
                            "expect_stdout": test["stdout"]}
    if test.get("stdin"):
        spec["stdin"] = test["stdin"]
    if test.get("argv"):
        spec["argv"] = list(test["argv"])
    if test.get("files"):
        spec["files"] = dict(test["files"])
    return {
        "id": cid,
        "prompt": task,
        "reference": case.get("reference"),
        "category": CATEGORY,
        "test": spec,
        "tags": tags_for(case),
        "notes": "eval2-legacy: test 1 of %d" % len(case.get("tests") or []),
    }


def project(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Every case to a record or a written reason. Nothing here is a gate; the
    harvest's gates still run on what comes out."""
    records: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for i, case in enumerate(cases):
        rec = to_record(case if isinstance(case, dict) else {})
        if "skipped" in rec:
            skipped.append({"case_id": (case or {}).get("case_id") if isinstance(case, dict) else None,
                            "index": i, "reason": rec["skipped"]})
        else:
            records.append(rec)
    return {"cases": len(cases), "records": records, "skipped": skipped}


def read_tags(tags: Any) -> Dict[str, Any]:
    """Family, population, capabilities and split group back out of a case's tags."""
    out: Dict[str, Any] = {"family": None, "population": None,
                           "split_group": None, "capabilities": []}
    for t in tags or []:
        if not isinstance(t, str):
            continue
        if t.startswith(TAG_FAMILY):
            out["family"] = t[len(TAG_FAMILY):]
        elif t.startswith(TAG_GROUP):
            out["split_group"] = t[len(TAG_GROUP):]
        elif t.startswith(TAG_POPULATION):
            out["population"] = t[len(TAG_POPULATION):]
        elif t.startswith(TAG_CAPABILITY):
            out["capabilities"].append(t[len(TAG_CAPABILITY):])
    out["capabilities"] = sorted(out["capabilities"])
    return out
