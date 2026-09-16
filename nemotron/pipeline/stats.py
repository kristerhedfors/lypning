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

import bisect
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
SAMPLING_KEYS = ("enable_thinking", "max_tokens", "samples", "temperature", "top_p", "top_k")
#: Keys whose ABSENCE from a recorded block is a value, not an unknown. Until
#: 2026-09-16 the backend could not pass `top_k`, so a block written without it
#: sampled with none — the same arm as a block that records `top_k: null`.
SAMPLING_DEFAULTS: Dict[str, Any] = {"top_k": None}
UNRECORDED = "unrecorded"
RECORDED = "recorded"


def _sampling(summary: Dict[str, Any]) -> Dict[str, Any]:
    s = summary.get("sampling")
    return sampling_block(s) if isinstance(s, dict) else {}


def sampling_block(block: Dict[str, Any]) -> Dict[str, Any]:
    """A recorded sampling block with the defaulted keys filled in."""
    return dict(SAMPLING_DEFAULTS, **block)


def sampling_missing(block: Any) -> List[str]:
    """The SAMPLING_KEYS a block leaves unknown — absent and with no default."""
    if not isinstance(block, dict):
        return list(SAMPLING_KEYS)
    return [k for k in SAMPLING_KEYS if k not in block and k not in SAMPLING_DEFAULTS]


def _differs(field: str, baseline: Any, run: Any) -> Dict[str, Any]:
    return {"field": field, "baseline": baseline, "run": run, "established": True}


def _unestablished(field: str, have_baseline: bool, have_run: bool) -> Dict[str, Any]:
    return {"field": field,
            "baseline": RECORDED if have_baseline else UNRECORDED,
            "run": RECORDED if have_run else UNRECORDED,
            "established": False}


def _arm(baseline: Dict[str, Any], run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Two runs generated on different serving stacks are not a delta.

    A model name is free text chosen by whoever launched the run, so it is not
    an identity: in this tree's own recorded runs two different endpoints both
    answer to one model name.

    THIS USED TO FIRE ONLY WHEN THE NAMES MATCHED, on the reasoning that a
    different name means the weights are the thing under test. That reasoning
    has a hole, and the fine-tune walked straight into it: the stock baseline was
    generated on a hosted provider's stack and a tuned arm is generated on our
    own vLLM, so the names differ AND the stacks differ, and the difference
    between the numbers is weights PLUS kernels PLUS sampling implementation
    PLUS tokenizer handling with nothing to separate them. Under the old rule
    that pair was waved through as "the endpoint difference IS the arm".
    
    So any known difference in endpoint withholds the subtraction now. The
    module's own rule is that an unknown is a reason to withhold and never a
    reason to assert, and a confound is a stronger reason than an unknown. The
    remedy is not a flag: it is to generate both arms on one stack, which is
    what `PREREGISTRATION.md` §5 requires and what one vLLM process with
    `--enable-lora` gives for free.
    """
    b_url = ((baseline.get("backend") or {}).get("base_url")
             or (baseline.get("backend") or {}).get("endpoint"))
    r_url = ((run.get("backend") or {}).get("base_url")
             or (run.get("backend") or {}).get("endpoint"))
    if b_url and r_url and b_url != r_url:
        rec = {"field": "backend.base_url", "baseline": b_url, "run": r_url,
               "established": True}
        if baseline.get("model") and baseline.get("model") == run.get("model"):
            # The stronger shape: one name, two stacks, so the record cannot even
            # say which weights produced which number.
            rec["same_model_name"] = run.get("model")
        return [rec]
    return []


def _engine(baseline: Dict[str, Any], run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Two runs graded by two engines are not a delta either.

    Same shape and same reason as :func:`_arm` one function up. A `lypning`-kind
    acceptance test asks whether THE ENGINE accepts the program, so the engine is
    half of what a pass rate is about — and this project has already watched that
    half move a published baseline without anyone touching a model: the same
    1,184 completions scored 40.37% before the engine gained math and type() and
    43.75% after. That re-grade was caught by hand, in a paragraph. A paragraph
    is not a guard.

    Since the engine started answering as the CPython it was built for, the same
    source builds a different engine on a 3.9 host than on a 3.11 one, so "the
    same commit" stopped being an identity and `engines.identity()` records the
    binary instead. A difference in it withholds the subtraction; the remedy is
    not a flag, it is to grade both arms against one build, which costs one
    command because re-grading recorded completions needs no GPU.

    An engine nobody recorded is an unknown, and an unknown is neither reported
    as a difference nor allowed to assert one — the runs graded before this field
    existed compare exactly as they did. Every run graded after it records it.
    """
    b, r = baseline.get("engine") or {}, run.get("engine") or {}
    if not (b.get("fingerprint") and r.get("fingerprint")):
        return []
    if b["fingerprint"] == r["fingerprint"]:
        return []
    out: List[Dict[str, Any]] = []
    bc, rc = b.get("chain") or {}, r.get("chain") or {}
    for name in sorted(set(bc) | set(rc)):
        bv = (bc.get(name) or {}).get("sha256")
        rv = (rc.get(name) or {}).get("sha256")
        if bv != rv:
            out.append(_differs("engine." + name, bv or UNRECORDED, rv or UNRECORDED))
    if b.get("oracle_python") != r.get("oracle_python"):
        out.append(_differs("engine.oracle_python",
                            b.get("oracle_python") or UNRECORDED,
                            r.get("oracle_python") or UNRECORDED))
    # A fingerprint that differs while every part of it compares equal would mean
    # the two sides recorded parts this function does not know about. Withhold on
    # the fingerprint itself rather than report no reason for a refusal.
    return out or [_differs("engine", b["fingerprint"], r["fingerprint"])]


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
        if key == "prompt_sha":
            # One rendered prompt has carried two names since 2026-09-16
            # (evaluate.PROMPT_SHA_ALIASES); compare the current names.
            from .evaluate import canonical_prompt_sha
            b, r = canonical_prompt_sha(b), canonical_prompt_sha(r)
        if b and r != b:
            out.append(_differs(key, b, r))
    bs, rs = _sampling(baseline), _sampling(run)
    b_full = bool(bs) and not sampling_missing(bs)
    r_full = bool(rs) and not sampling_missing(rs)
    if not (b_full and r_full):
        out.append(_unestablished("sampling", b_full, r_full))
    else:
        for key in SAMPLING_KEYS:
            if bs[key] != rs[key]:
                out.append(_differs("sampling." + key, bs[key], rs[key]))
    out.extend(_arm(baseline, run))
    out.extend(_engine(baseline, run))
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
    # DISCORDANCE IS SOLVED/NOT-SOLVED, not "the per-case mean moved".
    # AMENDMENT, 2026-09-13, §3c — and it is a change of rule, not a
    # clarification. The mean-moved definition counts a case that went 1/16 to
    # 2/16 as discordant, which is one Bernoulli draw of noise at k=16, and it
    # counts a case that went 0/16 to 5/16 the same way. Diluting the real
    # acquisitions with draw-level noise is what left the rule at 12% power
    # against the effect a rejection-sampling LoRA actually produces (six
    # previously-hopeless cases solved), measured against this baseline's own
    # per-case scores at the primary n=70. Under solved/not-solved that is 27%,
    # eight cases 20% -> 60%, ten 32% -> 90%, while the uniform shapes this rule
    # already saw are unchanged (+2pp 31/31, +4pp 84/85, +6pp 99/99) and the
    # null fires at 0% under both.
    #
    # The mean-moved counts are still computed and reported as
    # `mcnemar_p_mean_moved`, because a rule you can no longer see the
    # alternative to is a rule nobody can check.
    gained = [c for c in shared if after[c] > 0.0 and before[c] == 0.0]
    lost = [c for c in shared if after[c] == 0.0 and before[c] > 0.0]
    moved_up = [c for c in shared if after[c] > before[c]]
    moved_down = [c for c in shared if after[c] < before[c]]
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
        "moved_up": len(moved_up), "moved_down": len(moved_down),
        "mcnemar_p_mean_moved": _mcnemar(len(moved_up), len(moved_down)),
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

    THE SHAPE THIS DOES NOT SIMULATE is the one a rejection-sampling LoRA
    actually produces: not every case a little better, but a handful of
    previously-hopeless cases becoming possible. A uniform grid cannot show it,
    and reading these rows as "the power of this design" is how the rule came to
    be pre-registered at 12% against the effect it was bought to detect. The
    concentrated curve is in `PREREGISTRATION.md` §3c and was measured
    separately; the two belong together.

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
        unpaired = boot = mcnemar = both = 0
        for _ in range(trials):
            before: List[float] = []
            after: List[float] = []
            for s in scores:
                p = min(1.0, s + lift)
                before.append(sum(1 for _ in range(k) if rng.random() < s) / k)
                after.append(sum(1 for _ in range(k) if rng.random() < p) / k)
            if bootstrap_ci(after, resamples=resamples, seed=rng.randrange(1 << 30))["lo"] > base_point:
                unpaired += 1
            # BOTH LEGS, because the pre-registered rule is the CONJUNCTION and
            # simulating one of them answers a question nobody asked. Counted the
            # way `paired_delta` counts — which since the 2026-09-13 amendment is
            # solved/not-solved, not "the mean moved". Simulating the old
            # definition here while the rule uses the new one is the same defect
            # as simulating one leg of two.
            leg_boot = _boot_lo([a - b for a, b in zip(after, before)]) > 0
            gained = sum(1 for a, b in zip(after, before) if a > 0 and b == 0)
            lost = sum(1 for a, b in zip(after, before) if a == 0 and b > 0)
            leg_mcnemar = _mcnemar(gained, lost) < 0.05
            boot += leg_boot
            mcnemar += leg_mcnemar
            both += leg_boot and leg_mcnemar
        rows.append({
            "lift": lift,
            "unpaired_power": unpaired / trials,
            "bootstrap_power": boot / trials,
            "mcnemar_power": mcnemar / trials,
            # The name the caller reads for "the primary rule".
            "paired_power": both / trials,
        })
    return {
        "n_cases": len(scores),
        "k": k,
        "trials": trials,
        "baseline_point": base_point,
        "never_passes": sum(1 for s in scores if s == 0.0),
        "always_passes": sum(1 for s in scores if s == 1.0),
        # THE DISCRETE FLOOR UNDER THE WHOLE RULE. Exact two-sided McNemar over
        # b gained and c lost is 2*P(X <= min(b,c)) under Binomial(b+c, 1/2), so
        # with NOTHING lost it is 2/2**b: p = 0.0625 at five cases and 0.03125
        # at six. **A fine-tune that flips five cases and loses none cannot fire
        # this rule at any effect size.** Reported beside the curve because a
        # percentage-point MDE hides it — the rule is shape-dependent, and the
        # shape that matters is how many CASES moved, not how far the mean did.
        "min_gained_if_none_lost": _min_discordant(),
        "rows": rows,
    }


def _min_discordant(alpha: float = 0.05, cap: int = 64) -> Optional[int]:
    """Fewest gained cases that fire exact McNemar when nothing is lost."""
    for b in range(1, cap + 1):
        if _mcnemar(b, 0) < alpha:
            return b
    return None


def minimum_detectable(curve: Dict[str, Any], rule: str, want: float = 0.8) -> Optional[float]:
    """The smallest lift on the grid at which ``rule`` reaches ``want`` power."""
    key = "%s_power" % rule
    for row in curve["rows"]:  # type: ignore[index]
        if float(row[key]) >= want:  # type: ignore[index]
            return float(row["lift"])  # type: ignore[index]
    return None


# --- power for eval-2: family clusters, not cases -----------------------------

#: The effect shapes `power_curve_clustered` simulates. `uniform` lifts every
#: case's per-draw rate by delta (capped at 1). `concentrated` lifts the
#: CONCENTRATED_FRACTION of cases with the lowest base rate to ONE target rate,
#: chosen so the mean per-case lift is that same delta — a handful of families
#: solved outright, the shape `PREREGISTRATION.md` §3c found the case-resampling
#: rule near-blind to. Same delta, two shapes: the difference is the shape.
CLUSTER_SHAPES = ("uniform", "concentrated")
CLUSTER_DELTAS = (0.0, 0.03, 0.05, 0.08, 0.10)
CLUSTER_SIZES = (100, 200, 300, 500, 800)
CONCENTRATED_FRACTION = 0.1


def pilot_from_rows(rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """`training_metrics` rows as the pilot a cluster power curve reads.

    One entry per case: its family, its independent cluster (``split_group``,
    the family when absent — the default `eval2_rows` and the task-first path
    both use) and one 0/1 correct-AND-native score per draw, in draw order.
    """
    per: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if r.get("status") == "harness-error":
            # A draw the server or sandbox failed to produce is not an
            # observation of the policy: the first pilot draw of 2026-09-16
            # lost 796 of 1,024 calls to a billing refusal, and counting them
            # as wrong answers read a 97% base rate as 21%.
            continue
        cid = str(r["case_id"])
        e = per.setdefault(cid, {"family": r["family"],
                                 "split_group": r.get("split_group") or r["family"],
                                 "draws": []})
        e["draws"].append((int(r.get("draw", len(e["draws"]))),
                           int(bool(r.get("correct")) and bool(r.get("native")))))
    out: Dict[str, Dict[str, Any]] = {}
    for cid, e in per.items():
        e["draws"].sort()
        out[cid] = {"family": e["family"], "split_group": e["split_group"],
                    "scores": [s for _, s in e["draws"]]}
    return out


def _binomial(rng: random.Random, k: int, p: float, cache: Dict[float, List[float]]) -> int:
    """One Binomial(k, p) draw by inverse CDF: one uniform per case, not k."""
    if p <= 0.0:
        return 0
    if p >= 1.0:
        return k
    cdf = cache.get(p)
    if cdf is None:
        q = 1.0 - p
        pr = q ** k
        acc = pr
        cdf = [acc]
        for i in range(1, k + 1):
            pr *= (k - i + 1) / i * p / q
            acc += pr
            cdf.append(acc)
        cache[p] = cdf
    return min(k, bisect.bisect_left(cdf, rng.random()))


def _draw_bank(units: List[List[Tuple[str, List[float]]]], n: int,
               rng: random.Random) -> List[List[List[float]]]:
    """A bank of ``n`` cases: pilot clusters drawn with replacement, each drawn
    cluster carrying its families and their cases whole. Only the last cluster
    is cut, to land on ``n`` exactly; its cases stay together in it."""
    bank: List[List[List[float]]] = []
    count = 0
    while count < n:
        room = n - count
        fams: List[List[float]] = []
        for _, rates in rng.choice(units):
            if room <= 0:
                break
            take = rates[:room]
            fams.append(take)
            room -= len(take)
        bank.append(fams)
        count += sum(len(f) for f in fams)
    return bank


def _treated(bank: List[List[List[float]]], shape: str, delta: float, n: int,
             fraction: float, rng: random.Random) -> Tuple[List[List[List[float]]], float]:
    """The treated arm's per-case rates, and the realised mean per-case lift
    (below ``delta`` only where the cap at 1 bit)."""
    if delta <= 0.0:
        return bank, 0.0
    if shape == "uniform":
        out = [[[min(1.0, p + delta) for p in fam] for fam in cl] for cl in bank]
    else:
        refs = [(ci, fi, pi, p) for ci, cl in enumerate(bank)
                for fi, fam in enumerate(cl) for pi, p in enumerate(fam)]
        # Ties at the floor are broken by the cell's own rng, so WHICH of the
        # never-passing cases get solved varies by trial as it would in life.
        rng.shuffle(refs)
        refs.sort(key=lambda t: t[3])
        m = max(1, int(round(fraction * n)))
        chosen = refs[:m]
        target = min(1.0, sum(t[3] for t in chosen) / m + delta * n / m)
        out = [[list(fam) for fam in cl] for cl in bank]
        for ci, fi, pi, p in chosen:
            out[ci][fi][pi] = max(p, target)
    lifted = sum(t - b for cl, tcl in zip(bank, out) for fam, tfam in zip(cl, tcl)
                 for b, t in zip(fam, tfam))
    return out, lifted / n


def power_curve_clustered(
    pilot: Dict[str, Dict[str, Any]],
    k: int,
    *,
    mde: float = 0.03,
    deltas: Sequence[float] = CLUSTER_DELTAS,
    sizes: Sequence[int] = CLUSTER_SIZES,
    shapes: Sequence[str] = CLUSTER_SHAPES,
    fraction: float = CONCENTRATED_FRACTION,
    trials: int = 100,
    resamples: int = 400,
    seed: int = SEED,
) -> Dict[str, object]:
    """The power of the eval-2 rule as a function of bank size, from a pilot.

    `EVAL2.md` §4 fixes the rule: the 95% lower bound of the paired
    family-cluster percentile bootstrap of the macro-over-family
    correct-and-native delta is above ``mde``. `power_curve` above resamples
    cases and cannot price that rule: a family's cases share a task, so the
    bootstrap resamples families as whole clusters (`split_group`, the family
    when absent), exactly as `training_metrics.paired_comparison` does, and so
    does this simulation.

    ``pilot`` is one entry per case, ``{family, split_group?, scores: [0/1 per
    draw]}`` (`pilot_from_rows`). Every trial draws a fresh bank of ``N`` cases
    by resampling the pilot's clusters with replacement, cases kept with their
    families; simulates ``k`` draws per case for a base arm at each case's
    pilot rate and a treated arm at that rate lifted by ``delta`` in the given
    shape; and asks whether the lower bound clears ``mde``. ``power`` is the
    fraction of trials that fired; at ``delta`` 0 that fraction is the
    false-positive rate. ``mean_lo`` says how far the bound typically sat.

    Deterministic by ``seed``: each (shape, delta, N) cell seeds its own
    generator from the three, so adding a size or a delta to the grid moves no
    other row.
    """
    if k < 1:
        raise ValueError("k must be at least 1")
    for shape in shapes:
        if shape not in CLUSTER_SHAPES:
            raise ValueError("unknown effect shape %r" % (shape,))
    clusters: Dict[str, Dict[str, List[float]]] = {}
    for cid in sorted(pilot):
        e = pilot[cid]
        scores = e["scores"]
        if not scores:
            raise ValueError("case %s has no draws" % cid)
        group = str(e.get("split_group") or e["family"])
        clusters.setdefault(group, {}).setdefault(str(e["family"]), []).append(
            sum(scores) / len(scores))
    if not clusters:
        raise ValueError("empty pilot")
    units = [sorted(fams.items()) for _, fams in sorted(clusters.items())]
    fam_means = [sum(r) / len(r) for u in units for _, r in u]
    rows: List[Dict[str, object]] = []
    for shape in shapes:
        for delta in deltas:
            for n in sizes:
                rng = random.Random("%d:%s:%r:%d" % (seed, shape, delta, n))
                cache: Dict[float, List[float]] = {}
                fired = 0
                lo_sum = point_sum = effect_sum = 0.0
                for _ in range(trials):
                    bank = _draw_bank(units, n, rng)
                    treated, effect = _treated(bank, shape, delta, n, fraction, rng)
                    csum: List[float] = []
                    cnf: List[int] = []
                    for cl, tcl in zip(bank, treated):
                        fd = []
                        for fam, tfam in zip(cl, tcl):
                            fd.append(sum(_binomial(rng, k, t, cache) - _binomial(rng, k, b, cache)
                                          for b, t in zip(fam, tfam)) / (k * len(fam)))
                        csum.append(sum(fd))
                        cnf.append(len(fd))
                    # The paired cluster bootstrap, as `paired_comparison` does
                    # it: clusters drawn with replacement, the statistic the
                    # mean over every family the drawn clusters carry.
                    g = len(csum)
                    idx = range(g)
                    samples: List[float] = []
                    for _ in range(resamples):
                        pick = rng.choices(idx, k=g)
                        samples.append(sum(map(csum.__getitem__, pick))
                                       / sum(map(cnf.__getitem__, pick)))
                    samples.sort()
                    lo = samples[int(0.025 * resamples)]
                    fired += lo > mde
                    lo_sum += lo
                    point_sum += sum(csum) / sum(cnf)
                    effect_sum += effect
                rows.append({"shape": shape, "delta": delta, "N": n,
                             "power": fired / trials, "mean_lo": lo_sum / trials,
                             "mean_delta": point_sum / trials,
                             "mean_effect": effect_sum / trials})
    smallest: Dict[str, Dict[float, Optional[int]]] = {}
    for shape in shapes:
        smallest[shape] = {}
        for delta in deltas:
            hit = [r for r in rows if r["shape"] == shape and r["delta"] == delta
                   and float(r["power"]) >= 0.8]  # type: ignore[arg-type]
            smallest[shape][delta] = int(hit[0]["N"]) if hit else None  # type: ignore[arg-type]
    return {
        "k": k, "mde": mde, "trials": trials, "resamples": resamples, "seed": seed,
        "shapes": list(shapes), "deltas": list(deltas), "sizes": list(sizes),
        "fraction": fraction,
        "pilot": {"cases": sum(len(r) for u in units for _, r in u),
                  "families": len(fam_means), "clusters": len(units),
                  "base_point": sum(fam_means) / len(fam_means)},
        "rows": rows,
        "smallest_n": smallest,
        "false_positive": {shape: {int(r["N"]): float(r["power"])  # type: ignore[arg-type]
                                   for r in rows if r["shape"] == shape and r["delta"] == 0.0}
                           for shape in shapes},
    }


# --- the pre-registered verdict, as code rather than a paragraph -------------


def decide(before: Dict[str, float], after: Dict[str, float],
           *, exclude: Optional[Sequence[str]] = None,
           **kw) -> Dict[str, object]:
    """The pre-registered rule, applied to one denominator.

    THE RULE IS THE CONJUNCTION and that is the whole reason this exists.
    `PREREGISTRATION.md` §3 makes a win require BOTH the paired bootstrap CI
    lower bound above 0 AND exact McNemar p < 0.05, and until this function the
    rule lived only in that paragraph — every time it was applied it was applied
    by a hand-written script, twice by the session that wrote the paragraph.
    A rule with no implementation is a rule nobody can be held to, and this
    project has already shipped one seam that was wired, live and never
    consulted.

    `exclude` is the denominator: the case ids that cannot measure a model. It
    is passed in rather than computed here because the criterion is mechanical
    and lives in `refusals.usable_cases` — degenerate (the engine now runs the
    program the case asks the model to rewrite, so echoing the input passes) and
    unsatisfiable (nothing passes it). Deciding which cases to drop by looking
    at scores is how a delta gets manufactured; deciding it from the engine is
    not.
    """
    drop = set(exclude or ())
    b = {k: v for k, v in before.items() if k not in drop}
    a = {k: v for k, v in after.items() if k not in drop}
    if not b or not a:
        return {"n_pairs": 0, "fires": False, "why": "no cases left in this denominator"}
    d = paired_delta(b, a, **kw)
    leg_ci = d["ci95"]["lo"] > 0                    # type: ignore[index]
    leg_mcnemar = float(d["mcnemar_p"]) < 0.05      # type: ignore[arg-type]
    return dict(
        d,
        before_point=sum(b.values()) / len(b),
        after_point=sum(a.values()) / len(a),
        leg_ci=leg_ci,
        leg_mcnemar=leg_mcnemar,
        fires=bool(leg_ci and leg_mcnemar),
        why=("" if leg_ci and leg_mcnemar else
             "; ".join(([] if leg_ci else ["CI lower bound is not above 0"])
                       + ([] if leg_mcnemar else
                          ["McNemar p=%.4f is not < 0.05" % d["mcnemar_p"]]))),
    )
