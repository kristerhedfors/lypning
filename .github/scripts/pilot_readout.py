"""Print a round-02 pilot job's SFT selection, aggregates only.

Reads `round-02/<job>/sft/best.json` and `base-dev/metrics.json` from the
private artifact repo at a pinned revision and prints the selected step, the
selection rule, every observation's metrics and the headline population
metrics of base and the selected checkpoint. Never a case id, program or text:
only the named aggregate keys below are printed, through `public_view`, which
also drops `case_clusters` (private since 2026-09-23).
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "training"))
from pipeline.public_view import public_view  # noqa: E402

REPO = "headforce/lypning-round02-artifacts"
HEADLINE = ("correct", "correct_native", "case_weighted_correct", "case_weighted_native",
            "cases", "draws", "families", "mean_completion_tokens", "truncation_rate")
OBSERVED = ("step", "correct", "correct_native", "selection_correct_native", "delta",
            "standard_error", "margin", "selected", "rejected_for", "retention_lost")
RULE = ("metric", "selection_population", "gate_a_tolerance", "selection_z",
        "baseline_standard_error", "baseline_correct", "baseline_correct_native")


def headline(metrics):
    """The whole-split numbers and each population's, by the named keys only."""
    out = {k: metrics.get(k) for k in HEADLINE if k in metrics}
    out["by_population"] = {pop: {k: v.get(k) for k in HEADLINE if k in v}
                            for pop, v in sorted((metrics.get("by_population") or {}).items())
                            if isinstance(v, dict)}
    return out


def readout(best, base):
    return {"selected_step": best.get("step"),
            "rule": {k: (best.get("rule") or {}).get(k) for k in RULE},
            "observed": [{k: o.get(k) for k in OBSERVED} for o in best.get("observed") or []],
            "selected": headline(best), "base_dev": headline(base)}


def main():
    phase = "input"
    try:
        job = os.environ.get("PILOT_JOB", "")
        if not re.fullmatch(r"[0-9a-f]{24}", job):
            raise ValueError("PILOT_JOB must be a 24-hex HF job id")
        from huggingface_hub import HfApi, hf_hub_download
        phase = "authentication"
        token = os.environ["HF_TOKEN"].strip()
        info = HfApi(token=token).repo_info(REPO, repo_type="dataset")
        if not info.private:
            raise ValueError("artifact repo is not private")
        data = {}
        for name in ("sft/best.json", "base-dev/metrics.json"):
            phase = name
            path = hf_hub_download(REPO, "round-02/%s/%s" % (job, name), repo_type="dataset",
                                   revision=info.sha, token=token)
            data[name] = json.loads(Path(path).read_text(encoding="utf-8"))
        result = readout(data["sft/best.json"], data["base-dev/metrics.json"])
        result.update(job=job, revision=info.sha)
        print(json.dumps(public_view(result), indent=2, sort_keys=True, allow_nan=False))
        return 0
    except Exception as exc:
        lines = [f.lineno for f in traceback.extract_tb(exc.__traceback__) if f.filename == __file__]
        print("pilot readout failed during %s (%s, lines %s); no private payload printed."
              % (phase, type(exc).__name__, lines), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
