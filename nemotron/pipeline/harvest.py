"""Step 1. Candidates in, corpus out, and a written reason for everything dropped.

The ledger is the product, not a side effect. A harvest that silently keeps 40 of
120 candidates has told you nothing; one that says *which gate* took the other 80
tells you whether your export is missing tests, missing references, or full of
tests that pass anything. Drops are written to ``drops.jsonl`` with the gate that
rejected them, so the next harvest is an argument about data rather than a guess.

Duplicate candidates — the same prompt and the same test arriving from two
sources — are merged, not doubled: negatives accumulate, and an observed failure
category always beats ``unobserved``.
"""

from __future__ import annotations

import concurrent.futures
import datetime as _dt
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .acceptance import gate_case
from .adapters import ADAPTERS, Candidate
from .jsonio import sha256_of, write_json, write_jsonl
from .schema import case_id, make_case, primary_negative, validate_case


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def collect(sources: Iterable[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Run every adapter, merge by case identity, and split off the untestable.

    Returns (cases, early_drops). An early drop is a candidate that never reached
    the gates — no test at all, or a malformed record — and it is reported under
    its own reason so ``no-test`` never hides inside ``malformed``.
    """
    merged: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    early: List[Dict[str, Any]] = []
    for spec in sources:
        adapter = ADAPTERS[spec["name"]]
        for cand in adapter(spec):
            test = cand.get("test")
            if not isinstance(test, dict):
                early.append(_drop_record(cand, "no-test",
                                          "candidate carries nothing executable"))
                continue
            cid = case_id(cand["prompt"], test)
            negative = (cand.get("negative") or "").strip()
            if cid in merged:
                case = merged[cid]
                if negative and negative not in [n["program"] for n in case["negatives"]]:
                    case["negatives"].append({
                        "program": negative,
                        "detail": cand.get("negative_detail", ""),
                        "source_id": cand.get("source_id", ""),
                    })
                if case["category"] == "unobserved" and cand["category"] != "unobserved":
                    case["category"] = cand["category"]
                if case.get("reference") is None and cand.get("reference"):
                    case["reference"] = cand["reference"]
                case["tags"] = sorted(set(case["tags"]) | set(cand.get("tags") or []))
                continue
            case = make_case(
                prompt=cand["prompt"],
                test=test,
                reference=cand.get("reference") or None,
                category=cand.get("category") or "wrong-output",
                source=cand.get("source") or spec["name"],
                source_id=cand.get("source_id", ""),
                negatives=([{"program": negative,
                             "detail": cand.get("negative_detail", ""),
                             "source_id": cand.get("source_id", "")}] if negative else []),
                tags=cand.get("tags"),
                notes=cand.get("notes", ""),
            )
            err = validate_case(case)
            if err:
                early.append(_drop_record(cand, "malformed", err))
                continue
            merged[cid] = case
    return list(merged.values()), early


def _drop_record(cand: Dict[str, Any], gate: str, detail: str) -> Dict[str, Any]:
    return {
        "gate": gate,
        "detail": detail,
        "source": cand.get("source", "?"),
        "source_id": cand.get("source_id", ""),
        "prompt": (cand.get("prompt") or "")[:400],
    }


def harvest(
    sources: Iterable[Dict[str, Any]],
    *,
    out_dir: Path,
    jobs: int = 4,
    allow_no_witness: bool = False,
    progress=None,
) -> Dict[str, Any]:
    cases, drops = collect(sources)
    kept: List[Dict[str, Any]] = []

    def _gate(case: Dict[str, Any]) -> Tuple[Dict[str, Any], Any]:
        return case, gate_case(
            case["test"], case.get("reference"), primary_negative(case),
            allow_no_witness=allow_no_witness,
        )

    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        for case, report in pool.map(_gate, cases):
            done += 1
            if progress:
                progress(done, len(cases), case["id"], report.gate)
            if report.kept:
                case["gates"] = report.gates
                kept.append(case)
            else:
                drops.append({
                    "gate": report.gate, "detail": report.detail,
                    "source": case["source"], "source_id": case["source_id"],
                    "prompt": case["prompt"][:400], "id": case["id"],
                })

    kept.sort(key=lambda c: c["id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "corpus.jsonl", kept)
    write_jsonl(out_dir / "drops.jsonl", drops)
    ledger = {
        "harvested_at": _now(),
        "candidates": len(cases) + len([d for d in drops if "id" not in d]),
        "kept": len(kept),
        "dropped": len(drops),
        "drops_by_gate": dict(Counter(d["gate"] for d in drops)),
        "kept_by_category": dict(Counter(c["category"] for c in kept)),
        "kept_by_source": dict(Counter(c["source"] for c in kept)),
        "with_reference": sum(1 for c in kept if c.get("reference")),
        "with_negative": sum(1 for c in kept if c.get("negatives")),
        "corpus_sha256": sha256_of(kept),
    }
    write_json(out_dir / "harvest.json", ledger)
    return ledger
