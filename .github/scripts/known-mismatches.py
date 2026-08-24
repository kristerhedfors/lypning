#!/usr/bin/env python3
"""Score a conformance report against the mismatches this repository accepts.

`lypning conformance` is absolute: MISMATCH must be 0, and nothing here changes
that. What this adds is a way for ONE job to stay red for ONE documented defect
without that red swallowing every other signal it carries — which is what
happened when the MicroPython job's only outcome was "failed", and four runs of
"could not reach musl.libc.org" read exactly like a wrong answer in the tier.

A mismatch is accepted by IDENTITY — engine, entry, kind — never by count. The
difference is the whole point: with a count, fixing one defect while introducing
another nets to the same number and the job stays green, which is invariant 1's
silent failure wearing a CI badge. With identities, that swap is two findings and
two lines of output.

Three outcomes, and each of them exits differently on purpose:

  * an observed mismatch that is not in the ledger      -> exit 1, a regression
  * a ledger entry that no longer reproduces            -> exit 1, GOOD NEWS,
    and the fix is to delete the line (and, when the ledger empties, the
    `continue-on-error` the ledger exists to justify)
  * everything matches                                  -> exit 0

Usage:
    known-mismatches.py --report <conformance --json> --ledger <ledger.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Tuple

#: What makes two mismatches "the same one". Deliberately not `detail`: that
#: carries a first-diff of the program's actual output, which moves when the
#: corpus entry is re-captured or the diff formatting changes, and a ledger that
#: reddens on cosmetic drift is a ledger people start ignoring.
Identity = Tuple[str, str, str]


def identity(v: Dict[str, Any]) -> Identity:
    return (str(v.get("engine", "")), str(v.get("entry_id", "")), str(v.get("kind", "")))


def show(ident: Identity, detail: str = "") -> str:
    engine, entry, kind = ident
    return "  %-12s %-34s %-10s %s" % (engine, entry, kind, detail[:100])


def load(path: str, what: str) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        # A conformance run that crashed leaves a truncated or empty file, and
        # scoring that as "no mismatches observed" would turn a dead battery
        # into a green tick — the exact shape of failure this script exists to
        # prevent, one level up.
        sys.stderr.write("cannot read the %s at %s: %s\n" % (what, path, e))
        raise SystemExit(1)


def observed(report: Dict[str, Any]) -> Dict[Identity, str]:
    engines = report.get("engines")
    if not isinstance(engines, dict) or not engines:
        sys.stderr.write(
            "the report has no `engines` section — the battery did not run to completion.\n"
            "Refusing to score it: an empty report is not the same claim as a clean one.\n")
        raise SystemExit(1)
    out: Dict[Identity, str] = {}
    for er in engines.values():
        for v in er.get("failures", []):
            out[identity(v)] = str(v.get("detail", ""))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", required=True, help="`lypning conformance --json` output")
    ap.add_argument("--ledger", required=True, help="the accepted-mismatch ledger")
    ns = ap.parse_args()

    report = load(ns.report, "conformance report")
    ledger = load(ns.ledger, "ledger")

    seen = observed(report)
    accepted: Dict[Identity, Dict[str, Any]] = {}
    for e in ledger.get("accepted", []):
        accepted[identity(e)] = e

    total = report.get("total", "?")
    print("corpus: %s programs, %d mismatch(es) observed, %d accepted by the ledger"
          % (total, len(seen), len(accepted)))
    # The count this repository is never allowed to quote from memory is the
    # corpus size (invariant 3) — so it is printed, from this run, right here.

    unexpected = sorted(k for k in seen if k not in accepted)
    stale = sorted(k for k in accepted if k not in seen)

    if unexpected:
        print("\nNOT IN THE LEDGER — these are regressions:")
        for k in unexpected:
            print(show(k, seen[k]))

    if stale:
        print("\nGOOD NEWS — the ledger accepts these and they no longer reproduce:")
        for k in stale:
            print(show(k, str(accepted[k].get("why", ""))))
        print("\nDelete them from %s. When it empties, this job's defect is gone:" % ns.ledger)
        print("drop `continue-on-error` from the conformance job and replace the `core`")
        print("job's `--engine lypning --engine mixture` with a bare `lypning conformance`.")

    if not unexpected and not stale:
        if accepted:
            print("\nevery mismatch is one the ledger names, and every one it names still")
            print("reproduces. Known-red, and known to be exactly this red:")
            for k in sorted(accepted):
                print(show(k, str(accepted[k].get("why", ""))))
        else:
            print("\nMISMATCH 0, and the ledger is empty. Nothing is being waived.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
