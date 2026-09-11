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
from typing import Dict, List, Sequence, Tuple

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


def paired_delta(
    before: Dict[str, float],
    after: Dict[str, float],
    *,
    resamples: int = RESAMPLES,
    alpha: float = 0.05,
    seed: int = SEED,
) -> Dict[str, object]:
    """Bootstrap the delta over the cases BOTH runs measured, resampling cases.

    Why this exists beside the unpaired rule. Two runs over the same held-out
    set are not two independent samples: a case the model finds easy is easy in
    both, and that shared difficulty is variance the unpaired comparison pays
    for twice. Resampling the *pairs* cancels it, and the interval collapses
    onto the cases that actually changed. A +6/-1 swing that the unpaired rule
    cannot separate from noise is unambiguous here.

    The unpaired rule stays the headline because it is the rule that was fixed
    before any run happened. This is reported next to it, never instead of it,
    and where they disagree the disagreement is the finding.
    """
    shared = sorted(set(before) & set(after))
    if not shared:
        return {"n_pairs": 0, "delta": float("nan")}
    diffs = [after[c] - before[c] for c in shared]
    point = sum(diffs) / len(diffs)
    rng = random.Random(seed)
    means: List[float] = []
    for _ in range(resamples):
        means.append(sum(rng.choices(diffs, k=len(diffs))) / len(diffs))
    means.sort()
    lo = means[int((alpha / 2) * resamples)]
    hi = means[min(resamples - 1, int((1 - alpha / 2) * resamples))]
    gained = [c for c in shared if after[c] > before[c]]
    lost = [c for c in shared if after[c] < before[c]]
    return {
        "n_pairs": len(shared),
        "delta": point,
        "ci95": {"lo": lo, "hi": hi, "method": "paired-bootstrap",
                 "resamples": resamples, "seed": seed},
        "gained": len(gained), "lost": len(lost),
        "gained_ids": gained[:50], "lost_ids": lost[:50],
        "mcnemar_p": _mcnemar(len(gained), len(lost)),
        "significant": lo > 0.0,
    }


def _mcnemar(b: int, c: int) -> float:
    """Exact two-sided McNemar over the discordant pairs. No approximation at small n."""
    n = b + c
    if n == 0:
        return 1.0
    # P(X <= min(b,c)) + P(X >= max(b,c)) under Binomial(n, 0.5), computed exactly.
    k = min(b, c)
    total = 0.0
    for i in range(0, k + 1):
        total += math.comb(n, i)
    p = 2.0 * total / (2.0 ** n)
    return min(1.0, p)


def pass_at_k(per_case_counts: Sequence[Tuple[int, int]]) -> Dict[str, float]:
    """pass@k: the fraction of cases solved at least once in k samples.

    This is the headroom number, and it is the one that decides whether training
    is worth paying for. pass@1 says what the model DOES; pass@k says what it
    CAN do. Rejection-sampling SFT closes the gap between them and nothing more,
    so when pass@k is barely above pass@1 there is no gap to close and no run to
    fund.
    """
    if not per_case_counts:
        return {"pass_at_k": float("nan"), "pass_at_1": float("nan"), "n": 0, "k": 0}
    solved = sum(1 for c, _ in per_case_counts if c > 0)
    total_pass = sum(c for c, _ in per_case_counts)
    total_draws = sum(n for _, n in per_case_counts)
    return {
        "pass_at_k": solved / len(per_case_counts),
        "pass_at_1": (total_pass / total_draws) if total_draws else float("nan"),
        "n": len(per_case_counts),
        "k": max(n for _, n in per_case_counts),
        "headroom": (solved / len(per_case_counts)) - (
            (total_pass / total_draws) if total_draws else 0.0),
    }
