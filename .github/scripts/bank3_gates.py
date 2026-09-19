"""Does this bank clear the plan-time gates, at every protocol seed? Counts only.

The dosage gates in `train_verified.preflight` read a prepared bundle, and a
bundle is one seed. A bank is admissible for a round only if it clears them at
all three protocol seeds, because `split_cases` divides by connected component
and a split that is fine at 1111 can strand a population at 2222. This asks
the question once, over the bank as banked, before anything is bundled: the
split each seed produces, its composition by population and family, whether
`validate_pilot` and `validate_benchmark` admit it, and whether the train
half clears `MIN_TRAIN_CASES`.

Exit 1 when a seed fails a gate. That is a finding about the bank, not a usage
error, and it is the reason to run this before a GPU is booked. Prints no
task text, no program and no expected output: the repository is public and an
Actions log is world-readable. Needs `PYTHONPATH=src:training`.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from pipeline.training_contract import MIN_TRAIN_CASES, PROTOCOL_TRAIN_SEEDS
from pipeline.training_data import (split_cases, validate_benchmark, validate_cases,
                                    validate_pilot)
from pipeline.training_types import TrainingError


def rows_of(path: Path):
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def verdict(fn, cases) -> str:
    try:
        fn(cases)
        return "OK"
    except TrainingError as exc:
        return "REFUSED: %s" % exc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bank", type=Path, help="schema-3 cases JSONL (normalised bank-v3, or a bank_v* file)")
    ap.add_argument("--seeds", default=",".join(str(s) for s in PROTOCOL_TRAIN_SEEDS),
                    help="protocol seeds to split at (default: the contract's three)")
    args = ap.parse_args()

    cases = rows_of(args.bank)
    print("bank %s   rows %d   families %d   populations %s"
          % (args.bank.name, len(cases), len({c.get("family") for c in cases}),
             dict(Counter(c.get("population") for c in cases))))
    print("  validate_cases: %s" % verdict(validate_cases, cases))

    failed = False
    for seed in (int(s) for s in args.seeds.split(",") if s.strip()):
        try:
            split = split_cases(cases, seed=seed)
        except TrainingError as exc:
            print("seed %d   split REFUSED: %s" % (seed, exc))
            failed = True
            continue
        by_split = Counter(c["split"] for c in split)
        by_pop = Counter((c["split"], c["population"]) for c in split)
        fams = {s: len({c["family"] for c in split if c["split"] == s}) for s in ("train", "dev", "test")}
        train = by_split.get("train", 0)
        floor = "clears" if train >= MIN_TRAIN_CASES else "BELOW"
        print("seed %d   train %d (%s %d floor)   dev %d   test %d"
              % (seed, train, floor, MIN_TRAIN_CASES, by_split.get("dev", 0), by_split.get("test", 0)))
        for s in ("train", "dev", "test"):
            print("           %-5s coverage %-5d fallback-control %-5d families %d"
                  % (s, by_pop.get((s, "coverage"), 0), by_pop.get((s, "fallback-control"), 0), fams[s]))
        pilot = verdict(validate_pilot, split)
        bench = verdict(validate_benchmark, split)
        print("           validate_pilot: %s" % pilot)
        print("           validate_benchmark: %s" % bench)
        if train < MIN_TRAIN_CASES or not pilot.startswith("OK"):
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
