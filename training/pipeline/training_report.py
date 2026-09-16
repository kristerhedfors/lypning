"""Compare two standalone matched eval directories without loading a model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .training_types import TrainingError
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
    return {"base": summarize(rows[0]), "candidate": summarize(rows[1]),
            "paired": paired_comparison(*rows),
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
