"""The record every later step reads, and the only place its shape is decided.

A case is (prompt, optional reference, executable test, failure category,
provenance). Its id is a hash of the two fields that define identity — the
prompt and the test — so re-harvesting the same source produces the same id and
the frozen held-out split survives a corpus rebuild. Evidence and provenance are
deliberately *not* in the id: enriching a case with another hard negative must
not make it a different case.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .acceptance import validate_test_spec
from .classify import is_category
from .jsonio import digest

SCHEMA_VERSION = 1


def case_id(prompt: str, test: Dict[str, Any]) -> str:
    return "ntx-" + digest({"prompt": prompt, "test": test}, 12)


def make_case(
    *,
    prompt: str,
    test: Dict[str, Any],
    reference: Optional[str] = None,
    category: str = "wrong-output",
    source: str = "unknown",
    source_id: str = "",
    negatives: Optional[List[Dict[str, str]]] = None,
    tags: Optional[List[str]] = None,
    notes: str = "",
) -> Dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "id": case_id(prompt, test),
        "prompt": prompt,
        "reference": reference,
        "test": test,
        "category": category,
        "source": source,
        "source_id": source_id,
        # Every generation we have *seen fail* this case. Step 3 draws its
        # `hard_negatives` mix component from here; step 1's discriminates gate
        # uses negatives[0] as its negative control.
        "negatives": list(negatives or []),
        "tags": sorted(set(tags or [])),
        "notes": notes,
    }


def validate_case(case: Dict[str, Any]) -> Optional[str]:
    """Static validation. Execution-based validation is :func:`acceptance.gate_case`."""
    for key in ("id", "prompt", "test", "category", "source"):
        if key not in case:
            return "missing field: %s" % key
    if not isinstance(case["prompt"], str) or not case["prompt"].strip():
        return "prompt must be a non-empty string"
    if case.get("reference") is not None and not isinstance(case["reference"], str):
        return "reference must be a string or null"
    err = validate_test_spec(case["test"])
    if err:
        return "test: " + err
    if not is_category(case["category"]):
        return "unknown category: %r" % (case["category"],)
    if case["id"] != case_id(case["prompt"], case["test"]):
        return "id does not match (prompt, test) — record was edited by hand"
    for n in case.get("negatives", []):
        if not isinstance(n, dict) or "program" not in n:
            return "negative must be an object with a program"
    return None


def primary_negative(case: Dict[str, Any]) -> Optional[str]:
    negs = case.get("negatives") or []
    return negs[0]["program"] if negs else None
