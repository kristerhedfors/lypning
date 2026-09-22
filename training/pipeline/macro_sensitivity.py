"""Stress-test macro weighting using saved bundle-size aggregates, no private rows.

The simulated draws are hypothetical: Bernoulli(.5) per independent case,
with all k=16 draws within each case perfectly correlated. This isolates
case-count fragility; it is neither measured model quality nor a power claim.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from statistics import pstdev


def simulate(profile, *, trials=2000, seed=1111, minimum=5):
    sizes = [int(size) for size, count in sorted(profile["size_histogram"].items(),
             key=lambda item: int(item[0])) for _ in range(count)]
    if not sizes or min(sizes) < 1 or trials < 2:
        raise ValueError("positive family sizes and at least two trials required")
    if sum(sizes) != profile["cases"] or len(sizes) != profile["families"]:
        raise ValueError("family-size histogram does not match totals")
    eligible = [i for i, size in enumerate(sizes) if size >= minimum]
    rng = random.Random(seed)
    metrics = {"family_macro": [], "case_weighted": [], "floor_macro": []}
    for _ in range(trials):
        successes = [sum(rng.random() < .5 for _ in range(n)) for n in sizes]
        rates = [yes / n for yes, n in zip(successes, sizes)]
        metrics["family_macro"].append(sum(rates) / len(rates))
        metrics["case_weighted"].append(sum(successes) / sum(sizes))
        if eligible:
            metrics["floor_macro"].append(sum(rates[i] for i in eligible) / len(eligible))
    return {"cases": sum(sizes), "families": len(sizes),
            "excluded_cases": sum(n for n in sizes if n < minimum),
            "excluded_families": len(sizes) - len(eligible),
            "max_single_case_weight": {"family_macro": 1 / (len(sizes) * min(sizes)),
                                       "case_weighted": 1 / sum(sizes)},
            "simulated_sd_pp": {key: 100 * pstdev(values) if values else None
                                for key, values in metrics.items()}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("aggregates", type=Path)
    args = p.parse_args()
    data = json.loads(args.aggregates.read_text())
    profiles = {}
    for bank in ("eval2", "dev"):
        summary = data[bank + "_family_sizes"]
        profiles[bank + "/all"] = simulate(summary)
        for population, profile in summary["by_population"].items():
            profiles[bank + "/" + population] = simulate(profile)
    print(json.dumps({"seed": 1111, "trials": 2000, "minimum": 5,
                     "assumption": "hypothetical Bernoulli(.5) per case; k=16 perfectly correlated within case",
                     "profiles": profiles}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
