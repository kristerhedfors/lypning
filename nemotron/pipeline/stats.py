"""Pass rate with an interval wide enough to be honest about the sample size.

Bootstrap over *cases*, as specified: resample the per-case scores with
replacement 10,000 times, take the 2.5th and 97.5th percentiles of the resampled
means. Seeded, so the same attempts file gives the same interval twice.

A Wilson interval is reported alongside it and is not decoration. Percentile
bootstrap degenerates at small n — with eight cases the resampled means can only
take nine distinct values, and when every case passes the interval collapses to a
point, which is false. Wilson does not do that. When the two disagree, the sample
is too small for the question being asked, and that is the finding.
"""

from __future__ import annotations

import math
import random
from typing import Dict, List, Sequence

RESAMPLES = 10000
SEED = 20260911


def bootstrap_ci(
    scores: Sequence[float],
    *,
    resamples: int = RESAMPLES,
    alpha: float = 0.05,
    seed: int = SEED,
) -> Dict[str, float]:
    n = len(scores)
    if n == 0:
        return {"point": float("nan"), "lo": float("nan"), "hi": float("nan"), "n": 0}
    point = sum(scores) / n
    if n == 1:
        return {"point": point, "lo": 0.0, "hi": 1.0, "n": 1}
    rng = random.Random(seed)
    vals = list(scores)
    means: List[float] = []
    for _ in range(resamples):
        means.append(sum(rng.choices(vals, k=n)) / n)
    means.sort()
    lo = means[int((alpha / 2) * resamples)]
    hi = means[min(resamples - 1, int((1 - alpha / 2) * resamples))]
    return {"point": point, "lo": lo, "hi": hi, "n": n,
            "resamples": resamples, "seed": seed}


def wilson_ci(successes: float, n: int, *, z: float = 1.959963984540054) -> Dict[str, float]:
    """Score interval for a binomial proportion. Well-behaved at small n and at 0/1."""
    if n == 0:
        return {"point": float("nan"), "lo": float("nan"), "hi": float("nan"), "n": 0}
    p = successes / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = (z / d) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return {"point": p, "lo": max(0.0, centre - half), "hi": min(1.0, centre + half), "n": n}


def summarize(scores: Sequence[float], **kw) -> Dict[str, object]:
    b = bootstrap_ci(scores, **kw)
    w = wilson_ci(sum(scores), len(scores))
    return {
        "pass_rate": b["point"],
        "ci95": {"lo": b["lo"], "hi": b["hi"], "method": "bootstrap-percentile",
                 "resamples": b.get("resamples"), "seed": b.get("seed")},
        "ci95_wilson": {"lo": w["lo"], "hi": w["hi"]},
        "n_cases": len(scores),
        # The interval the bootstrap cannot see: at n small it can report zero
        # width while Wilson reports tens of points. Flagged, not hidden.
        "ci_disagreement": abs((b["hi"] - b["lo"]) - (w["hi"] - w["lo"])),
    }


def beats(candidate: Dict[str, object], baseline_point: float) -> bool:
    """The win rule: a run counts only if its CI lower bound clears the baseline."""
    return float(candidate["ci95"]["lo"]) > baseline_point  # type: ignore[index]
