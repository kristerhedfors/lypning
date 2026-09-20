"""Publish a carved bank as `banks/<name>/{train,eval2}.jsonl`, or refuse.

`bank3_publish.py --push` banks one immutable BATCH, which is the record of
what a run judged. This publishes the thing a ROUND reads: the union of every
batch, carved into a pilot and a benchmark that share no family, under the
`--bank-path` layout `training/hf/launch.py` expects.

The two are different objects and the distinction is load-bearing. A batch is
append-only evidence and is never rewritten; a bank is derived from every batch
that exists when it is cut, so it is dated, named and replaced rather than
edited. Overwriting `banks/v3` under a round that is running would change the
bank out from under it, so a name that already exists is refused unless
`--replace` says otherwise.

Nothing is uploaded unless `bank_carve` admits both halves — the pilot at every
protocol seed. Writing a bank a round cannot use moves the failure onto a
metered job, which is the whole shape this tree keeps removing.

Prints counts, families and digests. No task text, no program, no expected
output: the repository is public and an Actions log is world-readable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path[:0] = ["src", "training"]

from pipeline import bank_carve                                    # noqa: E402
from pipeline.jsonio import sha256_of                              # noqa: E402
from pipeline.training_types import TrainingError                  # noqa: E402

#: What `launch.py --bank-path` expects to find in the directory.
TRAIN, EVAL2 = "train.jsonl", "eval2.jsonl"

#: Written beside them, and the reason a round can trust what it downloaded.
#: A bank is a path in a shared repository, and on 2026-09-19 another job
#: uploaded a different bank over this one: the round read 1,689 cases where
#: 8,370 were published, trained on the wrong population and reported a result.
#: Nothing in the bank said which bank it was. This does — and because a
#: foreign writer overwrites the JSONL without knowing to update the manifest,
#: the mismatch is what a round refuses on.
MANIFEST = "bank.json"


def non_reproducing(cases, engine: str = "/bin/true"):
    """Cases whose reference does not reproduce its own expected stdout.

    `training-prepare` already refuses these — `validate_reference_scores`
    requires reward 1.0 on every test — but it refuses them on a metered GPU
    job, an hour in, after the bank download and the weights. On 2026-09-20
    one such case ended a round at `last_stage: prepare`. The same question is
    answerable here for nothing.

    Run through `synth.Runner`, not a bare subprocess: the runner materialises
    each test's input FILES, passes argv and stdin, and pins the hash seed. A
    plain `subprocess.run` of the reference misses all three and reported 572
    failures where the runner finds 34 — an instrument that disagrees with the
    one that decides is not a cheaper version of it.

    WHAT THIS CANNOT SEE, and it is the reason `--exclude-family` exists. This
    runs on a GitHub runner; the verifier runs in `launch.BASE_IMAGE`, a
    `python:3.12-slim`. `ubuntu-latest` ships locales that the slim image does
    not, so a `locale.setlocale(..., 'en_US.UTF-8')` case reproduces here and
    fails there — which is exactly the case that ended the round of 2026-09-20,
    and this check run on the runner did not drop it. A family whose answer
    depends on the clock, the installed locales or the tz database is excluded
    by name, because the property belongs to the construct rather than to any
    one case. Running this inside the pinned image would close the gap
    properly and is the honest next step.

    The engine is never consulted; only the CPython side is asked.
    """
    from pipeline.synth import Runner

    runner = Runner(engine)
    bad = {}
    for case in cases:
        try:
            got, why = runner.outputs(case["reference"], case["tests"])
        except Exception as exc:                                  # noqa: BLE001
            bad[case["case_id"]] = (case["family"], type(exc).__name__)
            continue
        if got is None:
            bad[case["case_id"]] = (case["family"], why.split(":")[0][:40])
        elif list(got) != [t["stdout"] for t in case["tests"]]:
            bad[case["case_id"]] = (case["family"], "stdout differs")
    return bad


def rows_of(path: Path):
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write(path: Path, rows) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
    path.write_text(body, encoding="utf-8")
    return sha256_of(body)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("union", type=Path, help="the normalised union of every batch")
    ap.add_argument("--name", required=True, help="bank name, e.g. v3-20260919")
    ap.add_argument("--out", type=Path, required=True, help="staging dir, RUNNER_TEMP only")
    ap.add_argument("--seed", type=int, default=1111, help="which families the benchmark takes")
    ap.add_argument("--benchmark-families", type=int)
    ap.add_argument("--exclude-family", action="append", default=[],
                    help="drop every case of this family (repeatable)")
    ap.add_argument("--verify-references", action="store_true",
                    help="drop cases whose reference does not reproduce its own stdout; "
                         "training-prepare refuses these on a metered job. Runs HERE, "
                         "which is not the verifier's image -- prefer --reference-report")
    ap.add_argument("--reference-report", type=Path,
                    help="a report from verify_references.py run INSIDE the verifier image; "
                         "the only form of this check that predicts the verifier")
    ap.add_argument("--max-cases-per-family", type=int,
                    help="trim each family to at most this many cases; family COUNT is "
                         "untouched, and preparation costs about four seconds a case")
    ap.add_argument("--push", action="store_true", help="upload; otherwise stage and report only")
    ap.add_argument("--replace", action="store_true",
                    help="allow overwriting a bank name that already exists")
    args = ap.parse_args()

    if not args.union.is_file():
        print("not a file: %s" % args.union, file=sys.stderr)
        return 2
    cases = rows_of(args.union)
    if not cases:
        print("no cases in %s" % args.union, file=sys.stderr)
        return 2

    # WHERE the check ran is part of the answer, so the manifest records it
    # rather than a bare boolean: a `locale` case reproduces on `ubuntu-latest`
    # and fails in `python:3.12-slim`, and a round died of exactly that gap.
    verified_in = None
    bad = {}
    if args.reference_report:
        if args.verify_references:
            print("--reference-report and --verify-references both given; the report is "
                  "the one that ran in the verifier's image", file=sys.stderr)
            return 2
        if not args.reference_report.is_file():
            print("not a file: %s" % args.reference_report, file=sys.stderr)
            return 2
        report = json.loads(args.reference_report.read_text(encoding="utf-8"))
        verified_in = report.get("image") or "an unnamed image"
        bad = {k: tuple(v) for k, v in report.get("bad", {}).items()}
        # A report cut against a different union is not a report about this one.
        if report.get("cases") != len(cases):
            print("reference report covers %s case(s), not this union's %d"
                  % (report.get("cases"), len(cases)), file=sys.stderr)
            return 2
        print("reference report from %s (python %s)" % (verified_in, report.get("python")))
    elif args.verify_references:
        verified_in = "the publishing runner, NOT the verifier image"
        bad = non_reproducing(cases)

    if verified_in is not None:
        if bad:
            from collections import Counter
            by_family = Counter(family for family, _ in bad.values())
            print("dropping %d case(s) whose reference does not reproduce, by family:" % len(bad))
            for family, n in by_family.most_common():
                print("   %-42s %d" % (family, n))
            cases = [c for c in cases if c["case_id"] not in bad]
        else:
            print("every reference reproduces its own stdout")

    try:
        carved = bank_carve.carve(cases, seed=args.seed,
                                  benchmark_families=args.benchmark_families,
                                  max_cases_per_family=args.max_cases_per_family,
                                  exclude_families=args.exclude_family)
    except TrainingError as exc:
        print("bank-publish: %s" % exc, file=sys.stderr)
        return 1
    print(bank_carve.render(carved))
    if carved["problems"]:
        return 1

    stage = args.out / args.name
    digests = {TRAIN: write(stage / TRAIN, carved["pilot"]),
               EVAL2: write(stage / EVAL2, carved["benchmark"])}
    summary = {
        "bank": args.name, "seed": args.seed,
        "max_cases_per_family": args.max_cases_per_family,
        "references_verified": verified_in is not None,
        "references_verified_in": verified_in,
        "excluded_families": sorted(args.exclude_family),
        "train": {"cases": len(carved["pilot"]), "families": len(carved["plan"]["pilot"]),
                  "sha256": digests[TRAIN], **carved["plan"]["pilot_counts"]},
        "eval2": {"cases": len(carved["benchmark"]), "families": len(carved["plan"]["benchmark"]),
                  "sha256": digests[EVAL2], **carved["plan"]["benchmark_counts"]},
    }
    (stage / MANIFEST).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                  encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not args.push:
        print("staged only; pass --push to upload")
        return 0

    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        print("HF_TOKEN is not set", file=sys.stderr)
        return 2
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    repo = "%s/%s" % (api.whoami()["name"],
                      os.environ.get("WORK_REPO_NAME", "lypning-round02-artifacts"))
    info = api.repo_info(repo, repo_type="dataset")
    if not info.private:
        print("refusing: %s is not private" % repo, file=sys.stderr)
        return 2
    target = "banks/%s" % args.name
    existing = [f for f in api.list_repo_files(repo, repo_type="dataset")
                if f.startswith(target + "/")]
    if existing and not args.replace:
        # A round reads a bank by name. Replacing one while a round is running
        # would change the bank under it, and the round's own manifest would
        # still name it.
        print("refusing: %s already exists (%d file(s)); a bank is dated and replaced, "
              "not edited. Pass --replace only if no round is reading it."
              % (target, len(existing)), file=sys.stderr)
        return 1
    api.upload_folder(repo_id=repo, repo_type="dataset", folder_path=str(stage),
                      path_in_repo=target, allow_patterns=[TRAIN, EVAL2, MANIFEST],
                      commit_message="bank %s: %d pilot / %d benchmark cases, no shared family"
                      % (args.name, len(carved["pilot"]), len(carved["benchmark"])))
    print("pushed %s to %s" % (target, repo))
    print("set BANK_PATH to: %s" % target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
