"""Cut one assembled eval-2 bank into three disjoint banks: pilot draw, eval-2, train.

THE UNIT IS A COMPONENT, NEVER A CASE. `training_data.split_cases` joins cases
that share a family, a declared ``source_group`` or a normalised solution AST
into one connected component and never lets a component straddle its splits;
this module forms the same components, with the same keys, and assigns each
whole component to exactly one bank, so that a training case is never the
eval-2 case next door under another id. Case records are written unchanged:
``data_loop.review`` refuses a case that already carries ``split`` or
``split_group``, and each bank is reviewed and split again on its own.

THE THREE BANKS. ``pilot-draw.jsonl`` is the spent pilot of ``EVAL2.md`` §7 —
the cases whose base rate sizes the bank and that are then set aside;
``eval2.jsonl`` is the benchmark of §2, frozen at its bundle digest and never
trained on; ``train.jsonl`` is the remainder, the reviewed pilot dataset that
sft/probe/grpo train on. Sizes are targets on cases; a component is assigned
whole, so a bank can overshoot its target by at most one component.

STRATIFIED BY POPULATION. Components are grouped by the set of populations
they carry, exactly as `split_cases` does, and each bank takes its share of
every stratum, so the coverage / fallback-control ratio of the whole bank is
kept in each bank as closely as whole components allow. Within a stratum the
order is a hash of the seed and the component's case ids, so the cut is a
pure function of the bank and the seed.

THE TRAIN FLOOR. `training_data.validate_bank` admits a pilot only with at
least 18 families, and `validate_pilot` wants fallback controls in every one
of its three splits, which needs several control families. After the
proportional cut this module moves the smallest components that help from
eval-2 (then the pilot draw) into train until train has at least
:data:`TRAIN_MIN_FAMILIES` families and :data:`TRAIN_MIN_FALLBACK_FAMILIES`
fallback-control families, backfilling the donor from train with a component
train can spare, and reports what it moved and whether the floor was met. It
is a preference: an eval-2 size of ``rest`` leaves train empty on purpose.

The module returns data and writes only what ``main`` is handed. ``main``
prints the summary, runs `eval2_leaks.bank_leaks` on eval-2 against train and
against the pilot draw, and exits 1 on any leaking pair — the component keys
cover the fingerprint and source-group rules, so a pair that trips the task,
stdout or evidence rule is a bank that needs its ``source_group`` unified,
not a cut that needs a different seed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .eval2_leaks import bank_leaks
from .jsonio import read_jsonl, sha256_of, write_json, write_jsonl
from .training_data import POPULATIONS, solution_fingerprint, validate_cases
from .training_types import TrainingError

BANKS: Tuple[str, ...] = ("pilot", "eval2", "train")
FILES: Dict[str, str] = {"pilot": "pilot-draw.jsonl", "eval2": "eval2.jsonl", "train": "train.jsonl"}
DEFAULT_PILOT_SIZE = 80
DEFAULT_EVAL2_SIZE = 300
#: `training_data.validate_bank`'s floor for a pilot.
TRAIN_MIN_FAMILIES = 18
#: Enough control families for `validate_pilot`'s per-split rule to have a chance.
TRAIN_MIN_FALLBACK_FAMILIES = 2


def components(cases: Sequence[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """The connected components `training_data.split_cases` forms, same keys, same order."""
    parents = list(range(len(cases)))

    def root(i: int) -> int:
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    seen: Dict[Tuple[str, str], int] = {}
    for i, case in enumerate(cases):
        keys = [("family", case["family"]), ("solution", solution_fingerprint(case["reference"]))]
        if case.get("source_group"):
            keys.append(("source", case["source_group"]))
        for key in keys:
            if key in seen:
                parents[root(i)] = root(seen[key])
            seen[key] = i
    groups: Dict[int, List[Dict[str, Any]]] = {}
    for i, case in enumerate(cases):
        groups.setdefault(root(i), []).append(case)
    return list(groups.values())


def component_id(group: Sequence[Dict[str, Any]]) -> str:
    return sha256_of(sorted(c["case_id"] for c in group))


def _quotas(target: int, sizes: Sequence[int]) -> List[int]:
    """Largest-remainder shares of ``target`` across strata of the given sizes."""
    total = sum(sizes)
    if total == 0 or target <= 0:
        return [0] * len(sizes)
    exact = [target * n / total for n in sizes]
    shares = [int(x) for x in exact]
    short = target - sum(shares)
    order = sorted(range(len(sizes)), key=lambda i: (-(exact[i] - shares[i]), i))
    for i in order[:short]:
        shares[i] += 1
    return shares


def _families(groups: Sequence[Sequence[Dict[str, Any]]], population: Optional[str] = None) -> set:
    return {c["family"] for g in groups for c in g if population is None or c["population"] == population}


def _short(train: Sequence[Sequence[Dict[str, Any]]]) -> Optional[str]:
    """Which floor train misses, or None."""
    if len(_families(train, "fallback-control")) < TRAIN_MIN_FALLBACK_FAMILIES:
        return "fallback-control"
    if len(_families(train)) < TRAIN_MIN_FAMILIES:
        return "families"
    return None


def _repair(assignment: Dict[str, List[List[Dict[str, Any]]]]) -> List[Dict[str, Any]]:
    """Move the smallest helpful components into train until the floor holds; backfill donors.

    Every move is recorded. The loop ends when the floor holds or no donor
    component helps; the caller reports which.
    """
    moves: List[Dict[str, Any]] = []
    while True:
        need = _short(assignment["train"])
        if need is None:
            return moves
        moved = False
        for donor in ("eval2", "pilot"):
            helpful = [g for g in assignment[donor]
                       if need == "families" or any(c["population"] == "fallback-control" for c in g)]
            if not helpful:
                continue
            group = min(helpful, key=lambda g: (len(g), component_id(g)))
            assignment[donor].remove(group)
            assignment["train"].append(group)
            move = {"component": component_id(group), "cases": len(group), "from": donor, "to": "train",
                    "why": "train needs " + need}
            moves.append(move)
            moved = True
            # Give the donor back a component train can spare without dropping under the floor.
            spare = [g for g in assignment["train"] if g is not group
                     and _short([h for h in assignment["train"] if h is not g]) is None]
            spare = [g for g in spare if len(g) <= len(group)]
            if spare:
                back = min(spare, key=lambda g: (-len(g), component_id(g)))
                assignment["train"].remove(back)
                assignment[donor].append(back)
                moves.append({"component": component_id(back), "cases": len(back), "from": "train",
                              "to": donor, "why": "backfill " + donor})
            break
        if not moved:
            return moves


def split_bank(cases: Sequence[Dict[str, Any]], seed: int = 1111, pilot_size: int = DEFAULT_PILOT_SIZE,
               eval2_size: Any = DEFAULT_EVAL2_SIZE) -> Dict[str, Any]:
    """Assign every component of ``cases`` to one of the three banks.

    Returns ``banks`` (bank name -> case list, input order kept) and
    ``summary`` (what ``main`` prints, before the leak check is added).
    """
    cases = list(cases)
    validate_cases(cases)
    rest = eval2_size == "rest"
    if not rest and (not isinstance(eval2_size, int) or isinstance(eval2_size, bool) or eval2_size < 0):
        raise TrainingError("eval2 size must be a non-negative integer or 'rest'")
    if not isinstance(pilot_size, int) or isinstance(pilot_size, bool) or pilot_size < 0:
        raise TrainingError("pilot size must be a non-negative integer")
    if pilot_size + (0 if rest else eval2_size) > len(cases):
        raise TrainingError("pilot and eval-2 sizes exceed the bank: %d + %s > %d"
                            % (pilot_size, eval2_size, len(cases)))
    groups = components(cases)
    strata: Dict[Tuple[str, ...], List[List[Dict[str, Any]]]] = {}
    for group in groups:
        strata.setdefault(tuple(sorted({c["population"] for c in group})), []).append(group)
    keys = sorted(strata)
    sizes = [sum(len(g) for g in strata[k]) for k in keys]
    pilot_quota = _quotas(pilot_size, sizes)
    eval2_quota = [n - p for n, p in zip(sizes, pilot_quota)] if rest else _quotas(eval2_size, sizes)
    assignment: Dict[str, List[List[Dict[str, Any]]]] = {b: [] for b in BANKS}
    for key, p_quota, e_quota in zip(keys, pilot_quota, eval2_quota):
        ordered = sorted(strata[key], key=lambda g: sha256_of([seed, sorted(c["case_id"] for c in g)]))
        filled = {"pilot": 0, "eval2": 0}
        for group in ordered:
            if filled["pilot"] < p_quota:
                bank = "pilot"
            elif filled["eval2"] < e_quota:
                bank = "eval2"
            else:
                bank = "train"
            assignment[bank].append(group)
            if bank in filled:
                filled[bank] += len(group)
    moves = [] if rest else _repair(assignment)
    owner: Dict[str, str] = {}
    for bank, bank_groups in assignment.items():
        for group in bank_groups:
            for case in group:
                owner[case["case_id"]] = bank
    banks = {b: [c for c in cases if owner[c["case_id"]] == b] for b in BANKS}
    short = _short(assignment["train"])
    summary = {
        "seed": seed,
        "cases": len(cases),
        "components": len(groups),
        "families": len({c["family"] for c in cases}),
        "populations": {p: sum(c["population"] == p for c in cases) for p in sorted(POPULATIONS)},
        "strata": {"+".join(k): {"components": len(strata[k]), "cases": n} for k, n in zip(keys, sizes)},
        "targets": {"pilot": pilot_size, "eval2": "rest" if rest else eval2_size},
        "banks": {b: {"file": FILES[b], "cases": len(banks[b]), "components": len(assignment[b]),
                      "families": len({c["family"] for c in banks[b]}),
                      "populations": {p: sum(c["population"] == p for c in banks[b])
                                      for p in sorted(POPULATIONS)}}
                  for b in BANKS},
        "train_floor": {"min_families": TRAIN_MIN_FAMILIES,
                        "min_fallback_families": TRAIN_MIN_FALLBACK_FAMILIES,
                        "families": len(_families(assignment["train"])),
                        "fallback_families": len(_families(assignment["train"], "fallback-control")),
                        "met": short is None,
                        "why_not": None if short is None else
                        ("eval-2 takes the rest by request" if rest else "no donor component helps: train needs " + short),
                        "moves": moves},
    }
    return {"banks": banks, "summary": summary}


def leak_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    return {"pairs": len(report["pairs"]), "by_rule": report["by_rule"],
            "eval2_cases_with_any_leak": report["n_eval2_cases_with_any_leak"],
            "unparsable": report["unparsable"],
            "examples": [{"eval2_id": p["eval2_id"], "other_id": p["train_id"], "rules": p["rules"], "why": p["why"]}
                         for p in report["pairs"][:20]]}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.eval2_split", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bank", type=Path, required=True, help="assembled schema-3 bank JSONL")
    parser.add_argument("--output", type=Path, required=True,
                        help="NEW directory for pilot-draw.jsonl, eval2.jsonl, train.jsonl, split.json")
    parser.add_argument("--seed", type=int, default=1111)
    parser.add_argument("--pilot-size", type=int, default=DEFAULT_PILOT_SIZE,
                        help="cases in the spent pilot draw (default %d)" % DEFAULT_PILOT_SIZE)
    parser.add_argument("--eval2-size", default=str(DEFAULT_EVAL2_SIZE),
                        help="cases in eval-2, or 'rest' for everything the pilot leaves (default %d)"
                             % DEFAULT_EVAL2_SIZE)
    args = parser.parse_args(argv)
    try:
        if args.eval2_size == "rest":
            eval2_size: Any = "rest"
        else:
            try:
                eval2_size = int(args.eval2_size)
            except ValueError:
                raise TrainingError("--eval2-size must be an integer or 'rest'")
        if args.output.exists():
            raise TrainingError("split output exists; use a new directory")
        cases = read_jsonl(args.bank)
        result = split_bank(cases, args.seed, args.pilot_size, eval2_size)
        banks, summary = result["banks"], result["summary"]
        summary["bank"] = args.bank.as_posix()
        summary["bank_sha256"] = sha256_of(cases)
        summary["leaks"] = {"eval2-vs-train": leak_summary(bank_leaks(banks["eval2"], banks["train"])),
                            "eval2-vs-pilot": leak_summary(bank_leaks(banks["eval2"], banks["pilot"]))}
        summary["leak_free"] = all(v["pairs"] == 0 for v in summary["leaks"].values())
        args.output.mkdir(parents=True, mode=0o700, exist_ok=False)
        for bank in BANKS:
            write_jsonl(args.output / FILES[bank], banks[bank])
        write_json(args.output / "split.json", summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        if not summary["leak_free"]:
            print("eval-2 split: leaking pairs remain; unify source_group in the bank and cut again",
                  file=sys.stderr)
            return 1
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print("eval-2 split blocked: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
