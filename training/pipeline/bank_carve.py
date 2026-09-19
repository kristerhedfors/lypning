"""Carve one bank into a pilot and a benchmark that share no family.

WHY A CARVE AND NOT A SPLIT. `training_data.split_cases` divides ONE bank into
train/dev/test for checkpoint selection, and every split it makes is trained
against or selected on. A round also needs a bank that nothing in training has
seen at all — the held-out benchmark the endpoint is read on (`EVAL2.md` §4).
Those are two different operations and only the first existed: bank v2's two
banks were separated by hand, which is why nothing could re-make the decision
and why bank v3 had none.

WHAT THE FAMILY BUYS. A family is one target construct and its tasks are
near-twins, so a family straddling the two banks leaks the benchmark into
training. Measured 2026-09-19, every family in bank v2 carries exactly one
population, which is what makes a per-population allocation possible at all:
coverage families and control families are drawn separately so neither bank can
end up without one.

WHAT THIS REFUSES. A carve that cannot be validated is not written. The pilot
must pass `validate_pilot` after `split_cases` at EVERY protocol seed, not the
one the caller happened to pass, because a bank admissible at 1111 and not at
2222 is a bank that fails halfway through a three-seed round. The floors are
arithmetic and are reported when they bite: ≥18 families per bank
(`validate_bank`), and ≥6 control families in the pilot, because `split_cases`
gives each split `n = max(2 if groups >= 6 else 1, groups // 6)` and
`validate_pilot` wants two independent families per population per split.

Library code: everything returns data; `cli.py` renders it and maps exit codes.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .jsonio import sha256_of
from .training_contract import PROTOCOL_TRAIN_SEEDS
from .training_data import (split_cases, validate_bank, validate_benchmark,
                            validate_cases, validate_pilot)
from .training_types import TrainingError

#: `validate_bank`'s floor, restated only to explain a refusal before one happens.
MIN_FAMILIES = 18

#: The pilot's control floor. `split_cases` hands each split
#: `max(2 if groups >= 6 else 1, groups // 6)` families of a population; at five
#: control families that is one per split and `validate_pilot` wants two.
MIN_PILOT_CONTROL_FAMILIES = 6

#: Enough control families to cluster on. One would satisfy
#: `validate_benchmark` and give a family-clustered bootstrap a single cluster,
#: which is not a bootstrap.
MIN_BENCHMARK_CONTROL_FAMILIES = 2

COVERAGE = "coverage"
CONTROL = "fallback-control"


def family_populations(cases: Sequence[Dict[str, Any]]) -> Dict[str, "frozenset[str]"]:
    """The populations each family carries, which is not always one.

    A ceiling-stratum construct produces a control case when the engine refuses
    the program, and a COVERAGE case when the model happens to write one the
    engine serves — `synth.judge` labels by what the engine did, not by which
    pool the construct came from. So a family can carry both, and measured
    2026-09-19 five of bank v3's do (`collections-namedtuple`,
    `dataclasses-dataclass`, `base64-b32encode` among them). bank v2 had none,
    which is why an earlier reading of it said a family is single-population.

    A mixed family is still allocated whole — it is one construct and its tasks
    are near-twins — it simply counts toward both populations in whichever bank
    takes it.
    """
    seen: Dict[str, set] = {}
    for case in cases:
        family, population = case.get("family"), case.get("population")
        if not family or not population:
            raise TrainingError("every case needs a family and a population to be carved")
        seen.setdefault(family, set()).add(population)
    return {family: frozenset(pops) for family, pops in seen.items()}


def _order(families: Sequence[str], seed: int) -> List[str]:
    """A deterministic order that a seed change actually moves."""
    return sorted(families, key=lambda f: sha256_of([seed, f]))


def plan(cases: Sequence[Dict[str, Any]], *, seed: int = 1111,
         benchmark_families: Optional[int] = None) -> Dict[str, Any]:
    """Which families go to the benchmark, and why that many.

    `benchmark_families` is a TOTAL; the split between populations follows the
    bank's own ratio so neither bank is starved of controls. Default: enough for
    the benchmark to clear its floors with the pilot still clearing its own.
    """
    per = family_populations(cases)
    # A family counts toward every population it carries, so a mixed one helps
    # both banks meet both floors.
    coverage = sorted(f for f, pops in per.items() if COVERAGE in pops)
    control = sorted(f for f, pops in per.items() if CONTROL in pops)
    if not coverage or not control:
        raise TrainingError("a carve needs both populations; this bank has only %s"
                            % (", ".join(sorted({p for pops in per.values() for p in pops}))
                               or "nothing"))

    # The control families decide the carve, because both banks have a floor on
    # them and there are always far fewer than coverage families.
    spare_control = len(control) - MIN_PILOT_CONTROL_FAMILIES
    if spare_control < MIN_BENCHMARK_CONTROL_FAMILIES:
        raise TrainingError(
            "%d control families cannot make two banks: the pilot needs %d and the "
            "benchmark %d. Generate more ceiling-stratum families."
            % (len(control), MIN_PILOT_CONTROL_FAMILIES, MIN_BENCHMARK_CONTROL_FAMILIES))

    if benchmark_families is None:
        # Mirror the bank's own population ratio, then clamp to the floors.
        want_control = max(MIN_BENCHMARK_CONTROL_FAMILIES, len(control) // 4)
        want_control = min(want_control, spare_control)
        want_coverage = max(MIN_FAMILIES - want_control, len(coverage) // 4)
    else:
        share = float(benchmark_families) / max(1, len(coverage) + len(control))
        want_control = min(spare_control,
                           max(MIN_BENCHMARK_CONTROL_FAMILIES, int(round(len(control) * share))))
        want_coverage = max(0, benchmark_families - want_control)
    want_coverage = min(want_coverage, len(coverage) - 1)

    bench_control = _order(control, seed)[:want_control]
    # A control family may also carry coverage, so the coverage draw skips what
    # the control draw already took rather than counting it twice.
    taken = set(bench_control)
    bench_coverage = [f for f in _order(coverage, seed) if f not in taken][:want_coverage]
    benchmark = taken | set(bench_coverage)
    every = set(per)
    pilot = every - benchmark

    def tally(families):
        return {"coverage": sum(1 for f in families if COVERAGE in per[f]),
                "control": sum(1 for f in families if CONTROL in per[f])}

    return {"benchmark": sorted(benchmark), "pilot": sorted(pilot), "seed": seed,
            "counts": {"coverage": len(coverage), "control": len(control),
                       "mixed": sum(1 for pops in per.values() if len(pops) > 1)},
            "benchmark_counts": tally(benchmark), "pilot_counts": tally(pilot)}


def _admits(cases: List[Dict[str, Any]], *, purpose: str) -> List[str]:
    """Every reason this bank is not admissible for `purpose`, or an empty list."""
    problems: List[str] = []
    try:
        validate_cases(cases)
        validate_bank(cases, purpose)
    except TrainingError as exc:
        problems.append(str(exc))
        return problems
    if purpose == "pilot":
        # Every protocol seed, because a round is three seeds and a bank that is
        # admissible at one of them fails the round halfway through.
        for protocol_seed in PROTOCOL_TRAIN_SEEDS:
            try:
                validate_pilot(split_cases(cases, seed=protocol_seed))
            except TrainingError as exc:
                problems.append("seed %d: %s" % (protocol_seed, exc))
    else:
        try:
            validate_benchmark(split_cases(cases, seed=PROTOCOL_TRAIN_SEEDS[0]))
        except TrainingError as exc:
            problems.append(str(exc))
    return problems


def carve(cases: Sequence[Dict[str, Any]], *, seed: int = 1111,
          benchmark_families: Optional[int] = None) -> Dict[str, Any]:
    """Two banks that share no family, or a refusal naming what failed.

    Returns ``{"pilot", "benchmark", "plan", "problems"}``. `problems` empty is
    the only result a caller may write: a carve that does not validate is a
    finding about the bank, and writing it would move the failure to a metered
    job.
    """
    allocation = plan(cases, seed=seed, benchmark_families=benchmark_families)
    bench_families = set(allocation["benchmark"])
    benchmark = [c for c in cases if c["family"] in bench_families]
    pilot = [c for c in cases if c["family"] not in bench_families]
    problems = (["pilot: " + p for p in _admits(pilot, purpose="pilot")]
                + ["benchmark: " + p for p in _admits(benchmark, purpose="benchmark")])
    shared = sorted({c["family"] for c in pilot} & {c["family"] for c in benchmark})
    if shared:                                   # cannot happen; asserted, not assumed
        problems.append("benchmark: families in both banks: " + ", ".join(shared[:5]))
    return {"pilot": pilot, "benchmark": benchmark, "plan": allocation, "problems": problems}


def render(result: Dict[str, Any]) -> str:
    allocation, pilot, benchmark = result["plan"], result["pilot"], result["benchmark"]
    lines = [
        "carve at seed %d: %d case(s) over %d families -> two banks sharing none"
        % (allocation["seed"], len(pilot) + len(benchmark),
           len(allocation["pilot"]) + len(allocation["benchmark"])),
        "  pilot      %5d case(s)  %3d families  (coverage %d, control %d)"
        % (len(pilot), len(allocation["pilot"]),
           allocation["pilot_counts"]["coverage"], allocation["pilot_counts"]["control"]),
        "  benchmark  %5d case(s)  %3d families  (coverage %d, control %d)"
        % (len(benchmark), len(allocation["benchmark"]),
           allocation["benchmark_counts"]["coverage"], allocation["benchmark_counts"]["control"]),
    ]
    if result["problems"]:
        lines.append("  NOT ADMISSIBLE, so nothing is written:")
        for problem in result["problems"]:
            lines.append("    " + problem)
    else:
        lines.append("  pilot admits at every protocol seed %s; benchmark admits"
                     % (", ".join(str(s) for s in PROTOCOL_TRAIN_SEEDS)))
    return "\n".join(lines)
