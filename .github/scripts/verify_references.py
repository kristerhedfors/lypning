"""Check every case's reference against its own stdout, INSIDE a named image.

WHY THIS EXISTS, and why it is a separate script. `bank_publish.py
--verify-references` asks the same question, but it asks it on `ubuntu-latest`
while the verifier runs in `launch.BASE_IMAGE`, a `python:3.12-slim`.
`ubuntu-latest` ships locales the slim image does not, so a
`locale.setlocale(..., 'en_US.UTF-8')` case reproduces on the runner and fails
in the container. That is not hypothetical: it is the case that ended a round
at `last_stage: prepare` on 2026-09-20, and the runner-side check had already
passed it. A check that runs somewhere other than the thing it is predicting is
a check of the wrong machine.

`pipeline.sandbox.run_python` spawns `sys.executable`, so the interpreter that
runs THIS script is the interpreter that runs every reference. Putting the
script in the image is therefore the whole fix.

It imports nothing outside the standard library and `training/pipeline`, which
has no runtime dependencies either (invariant 6), so the pinned image needs no
`pip install` and the check cannot drift by resolving a wheel. It does not read
the Hub, needs no token, builds no engine and writes one JSON report.

Output is a report, not a verdict: `bank_publish.py --reference-report` applies
it. Splitting the two keeps the expensive, image-bound half runnable on its own
and lets the manifest record WHERE the answer came from.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent / "training"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("union", type=Path, help="the normalised union of every batch")
    ap.add_argument("--out", type=Path, required=True, help="where to write the JSON report")
    ap.add_argument("--image", default=os.environ.get("VERIFY_IMAGE", ""),
                    help="the image this is running in; recorded in the report verbatim")
    args = ap.parse_args()

    if not args.union.is_file():
        print("not a file: %s" % args.union, file=sys.stderr)
        return 2
    with args.union.open(encoding="utf-8") as fh:
        cases = [json.loads(line) for line in fh if line.strip()]
    if not cases:
        print("no cases in %s" % args.union, file=sys.stderr)
        return 2

    # Imported here so `--help` works without the tree on the path.
    from pipeline.synth import Runner

    runner = Runner("/bin/true")  # the engine is never consulted; only CPython is asked
    bad = {}
    for case in cases:
        try:
            got, why = runner.outputs(case["reference"], case["tests"])
        except Exception as exc:                                  # noqa: BLE001
            bad[case["case_id"]] = [case["family"], type(exc).__name__]
            continue
        if got is None:
            bad[case["case_id"]] = [case["family"], why.split(":")[0][:40]]
        elif list(got) != [t["stdout"] for t in case["tests"]]:
            bad[case["case_id"]] = [case["family"], "stdout differs"]

    report = {
        "image": args.image,
        # The interpreter is the instrument here, so it is part of the answer.
        "python": sys.version.split()[0],
        "cases": len(cases),
        "bad": bad,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print("== checked %d reference(s) in %s (python %s)"
          % (len(cases), args.image or "an unnamed image", report["python"]))
    if not bad:
        print("   every reference reproduces its own stdout")
        return 0
    from collections import Counter

    by_family = Counter(family for family, _ in bad.values())
    print("   %d case(s) do not reproduce, by family:" % len(bad))
    for family, n in by_family.most_common():
        print("      %-42s %d" % (family, n))
    # A finding, not a failure: the publisher decides what to do with it, and a
    # non-zero exit here would stop the workflow before it could.
    return 0


if __name__ == "__main__":
    sys.exit(main())
