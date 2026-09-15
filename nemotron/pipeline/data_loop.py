"""Observation -> reviewed task -> frozen experiment, without executing code.

This is admission bookkeeping, not an automatic labeler. The reviewer supplies
task intent, independent test expectations and lineage. Capture success, native
execution and timing alone never imply task correctness or permission to train.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from lypning.evidence import load_snapshot
from .jsonio import read_jsonl, sha256_of, write_json, write_jsonl
from .training_data import split_cases, validate_cases, validate_pilot
from .training_types import TrainingError


REVIEW_FIELDS = ("reviewer", "intent_basis", "oracle_basis", "rights_basis", "independence_basis")


def review(cases, snapshots=(), seed=1111, purpose="pilot"):
    """Build a reproducible reviewed-data manifest; never change observations.

    Every case includes `review` with nonempty REVIEW_FIELDS, `origin` equal to
    captured or authored, and `evidence_ids`. Capture-derived cases require
    actual event links; authored cases explicitly need none. This validates the
    assertions/links, not their truth: human/source review remains necessary.
    """
    validate_cases(cases)
    if any("split" in c or "split_group" in c for c in cases):
        raise TrainingError("review unsplit cases; split assignments are derived, not supplied")
    manifests = []
    events = {}
    for path in snapshots:
        manifest, rows = load_snapshot(path)
        manifests.append(manifest["digest"])
        for event in rows:
            previous = events.get(event["event_id"])
            if previous is not None and previous != event:
                raise TrainingError("conflicting observation identity")
            events[event["event_id"]] = event
    for case in cases:
        record = case.get("review")
        if not isinstance(record, dict) or any(not isinstance(record.get(k), str) or not record[k].strip() for k in REVIEW_FIELDS):
            raise TrainingError("case %s needs explicit review: %s" % (case["case_id"], ", ".join(REVIEW_FIELDS)))
        origin = record.get("origin")
        links = record.get("evidence_ids")
        if origin not in ("captured", "authored") or not isinstance(links, list) or any(not isinstance(k, str) for k in links):
            raise TrainingError("review needs origin and evidence_ids")
        if len(set(links)) != len(links) or (origin == "captured" and not links):
            raise TrainingError("captured task needs unique observation links")
        for key in links:
            if key not in events or events[key]["quarantine"]:
                raise TrainingError("unknown/quarantined observation link: " + key)
    split = split_cases(cases, seed)
    if purpose == "pilot":
        validate_pilot(split)
    elif purpose != "smoke":
        raise TrainingError("invalid review purpose")
    # All uses of one exact source or occurrence must be grouped, even when
    # rewritten references have different ASTs/families. Fail instead of silently
    # moving the held-out split after review.
    owners = {}
    for case in split:
        for key in case["review"]["evidence_ids"]:
            event = events[key]
            for identity in ("event:" + key, "source:" + event["source_sha256"] if "source_sha256" in event else "event:" + key):
                if identity in owners and owners[identity] != case["split_group"]:
                    raise TrainingError("evidence leakage across components; unify source_group before review")
                owners[identity] = case["split_group"]
    linked = {k for c in cases for k in c["review"]["evidence_ids"]}
    result = {"schema": 1, "purpose": purpose, "seed": seed,
              "cases_sha256": sha256_of(cases), "snapshots": sorted(set(manifests)),
              "cases": len(cases), "observations": len(events), "linked_observations": len(linked),
              "retained_unselected": len(events) - len(linked),
              "splits": {part: {"cases": sum(c["split"] == part for c in split),
                                  "families": len({c["family"] for c in split if c["split"] == part}),
                                  "components": len({c["split_group"] for c in split if c["split"] == part})}
                         for part in ("train", "dev", "test")},
              "correctness_verified": False, "performance_gate": None}
    result["digest"] = sha256_of(result)
    return result


def load_review(path, cases, seed, purpose):
    result = json.loads(Path(path).read_text(encoding="utf-8"))
    expected = result.pop("digest", None)
    if (sha256_of(result) != expected or result.get("schema") != 1 or
            result.get("cases_sha256") != sha256_of(cases) or
            result.get("seed") != seed or result.get("purpose") != purpose):
        raise TrainingError("review/cases/split integrity changed; review again")
    result["digest"] = expected
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--snapshot", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path, required=True, help="new private review directory")
    parser.add_argument("--seed", type=int, default=1111)
    parser.add_argument("--purpose", choices=("smoke", "pilot"), default="pilot")
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise TrainingError("review output exists; use a new directory")
        cases = read_jsonl(args.cases)
        result = review(cases, args.snapshot, args.seed, args.purpose)
        args.output.mkdir(parents=True, mode=0o700, exist_ok=False)
        write_jsonl(args.output / "cases.jsonl", cases)
        write_json(args.output / "review.json", result)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, "data review blocked: %s\n" % exc)


if __name__ == "__main__":
    main()
