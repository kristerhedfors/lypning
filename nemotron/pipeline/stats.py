"""Pass rate with an interval wide enough to be honest about the sample size.

Bootstrap over *cases*, as specified: resample the per-case scores with
replacement 10,000 times, take the 2.5th and 97.5th percentiles of the resampled
means. Seeded, so the same attempts file gives the same interval twice.

A Wilson interval is reported alongside it and is not decoration. Percentile
bootstrap degenerates at small n — with eight cases the resampled means can only
take nine distinct values, and when every case passes the interval collapses to a
point, which is false. Wilson does not do that. When the two disagree, the sample
is too small for the question being asked, and that is the finding.

The guards on subtraction live here too, beside the win rule they protect.
`comparability` reports every way in which two runs failed to be shown to have
measured the same thing, and `completeness` every way a run's number fails to
cover the whole held-out set. Both return reason records rather than a verdict,
because the caller has to be able to print which field it was and what its two
values were: a refusal nobody can act on gets overridden.

Two kinds of reason are distinguished and must not be collapsed. A *difference*
is established — both values are known and they disagree. An *unestablished*
match is an unknown — one or both sides never wrote the field down. Both
withhold the delta, and reporting the second as the first is itself a claim
nobody established.
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

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


# What has to hold before one run's pass rate may be subtracted from another's.
# The sampling knobs are here because each moves the number on its own: the same
# weights at max_tokens 4096 and 12288 are one model measured twice, not two
# models. `seed` is deliberately absent — a different seed is the sampling noise
# the interval already prices, not a different measurement.
IDENTITY_KEYS = ("holdout_manifest_sha256", "prompt_sha")
SAMPLING_KEYS = ("enable_thinking", "max_tokens", "samples", "temperature", "top_p")
UNRECORDED = "unrecorded"
RECORDED = "recorded"


def _sampling(summary: Dict[str, Any]) -> Dict[str, Any]:
    s = summary.get("sampling")
    return s if isinstance(s, dict) else {}


def _differs(field: str, baseline: Any, run: Any) -> Dict[str, Any]:
    return {"field": field, "baseline": baseline, "run": run, "established": True}


def _unestablished(field: str, have_baseline: bool, have_run: bool) -> Dict[str, Any]:
    return {"field": field,
            "baseline": RECORDED if have_baseline else UNRECORDED,
            "run": RECORDED if have_run else UNRECORDED,
            "established": False}


def _arm(baseline: Dict[str, Any], run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The one arm-identity question this record can actually answer.

    A model name is free text chosen by whoever launched the run, so it is not
    an identity: in this tree's own recorded runs two different endpoints both
    answer to "nemotron". When the names match and the endpoints do not, the
    record cannot say which weights produced which number — that withholds the
    delta. When the names differ, the endpoint difference is the arm under test.
    """
    b_url = (baseline.get("backend") or {}).get("base_url")
    r_url = (run.get("backend") or {}).get("base_url")
    name = baseline.get("model")
    if b_url and r_url and b_url != r_url and name and name == run.get("model"):
        return [{"field": "backend.base_url", "baseline": b_url, "run": r_url,
                 "established": True, "same_model_name": run.get("model")}]
    return []


def comparability(baseline: Dict[str, Any], run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every reason two run summaries cannot be shown to have measured one thing.

    One record per reason, empty when the delta is a delta. Each record carries
    `field`, both sides' values, and `established`: True when both values are
    known and disagree, False when a side never wrote the field down. A sampling
    block missing any of SAMPLING_KEYS is one unestablished reason rather than
    five differences, because an unknown is a reason to withhold the subtraction
    and never a reason to assert a difference.
    """
    out: List[Dict[str, Any]] = []
    # A run is the same measurement as itself whatever it recorded, so there is
    # nothing here to establish: the baseline's own row on the board is not news.
    if baseline.get("run_id") and baseline.get("run_id") == run.get("run_id"):
        return out
    for key in IDENTITY_KEYS:
        b, r = baseline.get(key), run.get(key)
        if b and r != b:
            out.append(_differs(key, b, r))
    bs, rs = _sampling(baseline), _sampling(run)
    b_full = all(k in bs for k in SAMPLING_KEYS)
    r_full = all(k in rs for k in SAMPLING_KEYS)
    if not (b_full and r_full):
        out.append(_unestablished("sampling", b_full, r_full))
    else:
        for key in SAMPLING_KEYS:
            if bs[key] != rs[key]:
                out.append(_differs("sampling." + key, bs[key], rs[key]))
    out.extend(_arm(baseline, run))
    return out


def completeness(summary: Dict[str, Any],
                 progress: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Every reason a run's pass rate does not cover the whole held-out set.

    One record per reason, each carrying `blocks`: whether the shortfall bends
    the number or merely shrinks the sample. The two have opposite characters. A
    spend-capped run drops whatever it had not reached — the slow and the hard,
    disproportionately — so its number is biased upward and must not be ranked
    beside a complete one. A case lost to a harness error is excluded precisely
    because it is not the model's, carries no such bias, and must not brick a
    run that one sandbox flake touched.
    """
    out: List[Dict[str, Any]] = []
    planned = summary.get("cases_planned")
    done = summary.get("cases_evaluated")
    if isinstance(planned, int) and isinstance(done, int) and done < planned:
        errors = summary.get("harness_errors")
        excused = errors if isinstance(errors, int) else 0
        field = "cases_evaluated" if (planned - done) > excused else "harness_errors"
        out.append({"field": field, "expected": planned, "actual": done,
                    "harness_errors": excused, "blocks": field == "cases_evaluated"})
    p = progress or {}
    state = p.get("state")
    if state is not None and state != "done":
        out.append({"field": "state", "expected": "done", "actual": state, "blocks": True})
    if p.get("aborted"):
        out.append({"field": "aborted", "expected": None, "actual": p["aborted"],
                    "blocks": True})
    return out


def blocking(reasons: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The completeness reasons that bar a run from being ranked or promoted."""
    return [d for d in reasons if d.get("blocks")]


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
        return {"n_pairs": 0, "delta": float("nan"),
                "dropped_before_only": len(before), "dropped_after_only": len(after),
                "significant": False, "direction": "none"}
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
        # Cases one run measured and the other did not are dropped by the
        # intersection, and they are not dropped at random: an aborted run stops
        # on the slow and the hard. Counted here so the caller can say over what
        # the delta was taken, rather than leaving the reader to notice n_pairs.
        "dropped_before_only": len(set(before) - set(after)),
        "dropped_after_only": len(set(after) - set(before)),
        "delta": point,
        "ci95": {"lo": lo, "hi": hi, "method": "paired-bootstrap",
                 "resamples": resamples, "seed": seed},
        "gained": len(gained), "lost": len(lost),
        "gained_ids": gained[:50], "lost_ids": lost[:50],
        "mcnemar_p": _mcnemar(len(gained), len(lost)),
        # An interval lying wholly below zero is exactly as separable from noise
        # as one lying wholly above it, and it is the direction this pipeline
        # exists to catch: a tune that buys the route by giving up correctness.
        # Calling it "not separable" hid the one result that should stop a ship.
        "significant": lo > 0.0 or hi < 0.0,
        "direction": "improvement" if lo > 0.0 else ("regression" if hi < 0.0 else "none"),
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

    pass@1 here is the mean of the per-case means, the weighting every other
    site in this pipeline uses, so it can be subtracted from a summary's
    pass_rate and from the pass@1 printed beside it. The draw-weighted figure —
    in which a case drawn sixteen times outvotes a case drawn once — is reported
    next to it under a name that says which of the two it is. They coincide only
    while every case has the same number of draws, and one harness error is
    enough to end that, so `k_min` is reported too: when it differs from `k` the
    column head "pass@k" is naming the largest k, not the one every case got.
    """
    if not per_case_counts:
        return {"pass_at_k": float("nan"), "pass_at_1": float("nan"),
                "pass_at_1_draw_weighted": float("nan"), "n": 0, "k": 0,
                "k_min": 0, "ragged": False, "headroom": float("nan")}
    solved = sum(1 for c, _ in per_case_counts if c > 0)
    case_means = [c / n for c, n in per_case_counts if n]
    total_pass = sum(c for c, _ in per_case_counts)
    total_draws = sum(n for _, n in per_case_counts)
    k_max = max(n for _, n in per_case_counts)
    k_min = min(n for _, n in per_case_counts)
    pass_k = solved / len(per_case_counts)
    pass_1 = (sum(case_means) / len(case_means)) if case_means else float("nan")
    return {
        "pass_at_k": pass_k,
        "pass_at_1": pass_1,
        "pass_at_1_draw_weighted": (total_pass / total_draws) if total_draws else float("nan"),
        "n": len(per_case_counts),
        "k": k_max,
        "k_min": k_min,
        "ragged": k_min != k_max,
        "headroom": pass_k - (pass_1 if pass_1 == pass_1 else 0.0),
    }


# --- power: what this instrument can and cannot see --------------------------

#: The lifts the power curve is reported at, in absolute pass-rate points. The
#: grid is fixed so the answer is a table rather than a number someone chose
#: after seeing it.
POWER_GRID = (0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.12, 0.15, 0.20)


def power_curve(
    scores: Sequence[float],
    k: int,
    *,
    trials: int = 200,
    resamples: int = 1500,
    seed: int = SEED,
) -> Dict[str, object]:
    """How large a true improvement must be before each rule can see it.

    WHY THIS IS NOT OPTIONAL. A rule that cannot detect the effect you are
    paying for is an experiment that will report "no win" whatever happens, and
    the money is spent either way. Both rules in this file are simulated against
    the baseline's OWN per-case scores, so the answer is about this held-out set
    and not about a textbook one.

    The simulation lifts every case's underlying rate by the same absolute
    amount (capped at 1), redraws ``k`` Bernoulli attempts per case for both
    arms, and asks each rule whether it fires. That is deliberately the most
    favourable shape an improvement can take — a uniform lift — so the numbers
    here are an UPPER bound on power, not an estimate of it.

    Run it before the run, not after: a rule chosen once the outcome is visible
    is not a rule.
    """
    rng = random.Random(seed)
    base_point = sum(scores) / len(scores) if scores else 0.0
    rows: List[Dict[str, object]] = []

    def _boot_lo(diffs: List[float]) -> float:
        m = len(diffs)
        means = [sum(rng.choices(diffs, k=m)) / m for _ in range(resamples)]
        means.sort()
        return means[int(0.025 * resamples)]

    for lift in POWER_GRID:
        unpaired = 0
        paired = 0
        for _ in range(trials):
            before: List[float] = []
            after: List[float] = []
            for s in scores:
                p = min(1.0, s + lift)
                before.append(sum(1 for _ in range(k) if rng.random() < s) / k)
                after.append(sum(1 for _ in range(k) if rng.random() < p) / k)
            if bootstrap_ci(after, resamples=resamples, seed=rng.randrange(1 << 30))["lo"] > base_point:
                unpaired += 1
            if _boot_lo([a - b for a, b in zip(after, before)]) > 0:
                paired += 1
        rows.append({
            "lift": lift,
            "unpaired_power": unpaired / trials,
            "paired_power": paired / trials,
        })
    return {
        "n_cases": len(scores),
        "k": k,
        "trials": trials,
        "baseline_point": base_point,
        "never_passes": sum(1 for s in scores if s == 0.0),
        "always_passes": sum(1 for s in scores if s == 1.0),
        "rows": rows,
    }


def minimum_detectable(curve: Dict[str, Any], rule: str, want: float = 0.8) -> Optional[float]:
    """The smallest lift on the grid at which ``rule`` reaches ``want`` power."""
    key = "%s_power" % rule
    for row in curve["rows"]:  # type: ignore[index]
        if float(row[key]) >= want:  # type: ignore[index]
            return float(row["lift"])  # type: ignore[index]
    return None
