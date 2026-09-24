"""Compare two standalone matched eval directories without loading a model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .training_types import TrainingError
from .mismatch_policy import engine_mismatches
from .training_metrics import paired_comparison, summarize


def compare(base, candidate):
    paths = [Path(base), Path(candidate)]
    manifests = [json.loads((p / "experiment.json").read_text()) for p in paths]
    a, b = manifests
    if any(m["stage"] != "eval" for m in manifests):
        raise TrainingError("compare standalone evaluations, not checkpoint-search logs")
    for key in ("base_model", "revision", "bundle_digest", "smoke", "decoding",
                "enable_thinking", "tokenizer_sha256", "model_config_sha256", "code_sha256", "versions", "hardware"):
        if key not in a or key not in b or a[key] != b[key]:
            raise TrainingError("unmatched evaluation contract: " + key)
    for key in ("seed", "eval_split", "eval_draws", "greedy"):
        if a["args"][key] != b["args"][key]:
            raise TrainingError("unmatched evaluation argument: " + key)
    rows = [[json.loads(line) for line in (p / "evaluations.jsonl").read_text().splitlines()] for p in paths]
    # Old runs retain their old metric; an amendment never rewrites evidence.
    policy = a.get("metric_policy", {"min_family_cases": 1})
    if policy != b.get("metric_policy", {"min_family_cases": 1}):
        raise TrainingError("unmatched evaluation contract: metric_policy")
    if set(policy) != {"min_family_cases"}:
        raise TrainingError("unknown evaluation metric policy")
    return {"base": summarize(rows[0], **policy), "candidate": summarize(rows[1], **policy),
            "paired": paired_comparison(*rows, **policy),
            # Over EVERY draw of each arm, not the primary families only: a
            # mismatch counts against the arm that drew it, so the paired
            # delta is read beside both counts (EVAL2.md §4, 2026-09-24).
            "engine_mismatches": {"base": engine_mismatches(rows[0]),
                                  "candidate": engine_mismatches(rows[1])},
            "quality_evidence": not a["smoke"],
            "note": "Correctness non-inferiority margins and release decisions must be preregistered."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(compare(args.base, args.candidate), indent=2))
        return 0
    except (TrainingError, OSError, KeyError, ValueError) as exc:
        print("comparison blocked: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
