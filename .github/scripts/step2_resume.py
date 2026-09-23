"""The explicit resume of a stopped Step 2 run: validate, fetch, account. Free.

A transport error on a paid run is a RESUME, not a rerun. The generator takes
zero retries, so one ambiguous failure stops a run whose completions are
already paid for; rerunning the shard would buy them twice. Dispatch
`step2-control.yml` again with the same rung and shard, `resume_run_id` set to
the stopped run's id, and `ceiling_usd` set to the TOTAL the shard may cost:
the stopped run's charged-or-reserved dollars count against it.
`pipeline.positive_control_resume` holds the rules; this script is the CI side.

    python .github/scripts/step2_resume.py validate   # before anything; no network
    python .github/scripts/step2_resume.py download   # after the bank; HF_TOKEN

`validate` refuses a run id of the wrong shape, rung or shard. `download`
copies the named run's private evidence and its recorded chain, read-only,
into ``$RUNNER_TEMP/step2-resume/<run>/paid``, refuses when another run of
the shard already resumed any run of that chain (resume the latest run, never
an earlier one: the successor's dollars would be spent twice), proves the
chain's own ledgers, checks the identity fields known before the build, and refuses when
what is left of the ceiling or the dispatch window cannot cover the requests
that remain. `step2_generate.py` re-reads the same copy and checks the full
identity -- engine, base image, oracle, provider, sampling -- before its first
call. Output is aggregates only: run ids, counts and dollars, never a case.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

from pipeline.positive_control_resume import (PLANNED_IDENTITY, RUN_ID, ResumeError,
                                              chain_links, check_identity, check_not_forked,
                                              check_target, parse_run_id, plan,
                                              planned_identity, read_link, request_key,
                                              shard_prefix, summary)
from step2_shard import ShardError, generation, refusals, rpm_from_env, shard_from_env

SPEC = Path("training/prompts/subset-spec.md")


def resume_run_id(environ):
    """The operator's `resume_run_id`, exactly as typed; empty means no resume."""
    return environ.get("STEP2_RESUME_RUN_ID", "")


def shard_record(environ):
    """What `step2_generate.write_shard` records for this run."""
    return dict(shard_from_env(environ), cases=int(environ["STEP2_CASES"]))


def validate(environ):
    named = resume_run_id(environ)
    if not named:
        return {"resume": False}
    check_target(named, environ["STEP2_RUN_ID"], environ["STEP2_RUNG"], shard_from_env(environ))
    return {"resume": True, "resume_run_id": named}


def fetcher(environ):
    """``fetch(runs) -> snapshot root`` over the private artifact repository."""
    from huggingface_hub import HfApi, snapshot_download
    token = environ["HF_TOKEN"].strip()
    api = HfApi(token=token)
    repo = api.whoami()["name"] + "/lypning-round02-artifacts"
    info = api.repo_info(repo, repo_type="dataset")
    if not info.private:
        raise ResumeError("artifact repository must be private")

    def fetch(runs=(), *, manifests_of=None):
        # ``manifests_of``: a shard prefix whose runs' manifests are wanted.
        patterns = ["positive-control/%s/paid/*" % run for run in runs]
        if manifests_of:
            patterns.append("positive-control/%s-*/paid/manifest.json" % manifests_of)
        return Path(snapshot_download(repo, repo_type="dataset", revision=info.sha,
                                      allow_patterns=patterns, token=token))
    return fetch


def download_chain(named, fetch, dest):
    """Copy the named run and the chain it records into ``dest/<run>/paid``.

    Every run id is shape-checked before it becomes a path or a pattern.
    Returns the run ids, oldest first. The Hub copies are never written.
    """
    parse_run_id(named)
    result = _copy(fetch([named]), named, dest)
    return download_recorded_chain(result, fetch, dest) + [named]


def _copy(root, run, dest):
    source = Path(root) / "positive-control" / run / "paid"
    if not (source / "result.json").is_file():
        raise ResumeError("run %s has no private generation evidence" % run)
    target = Path(dest) / run / "paid"
    if target.exists():
        raise ResumeError("run %s was already copied; a chain names each run once" % run)
    shutil.copytree(source, target)
    return json.loads((target / "result.json").read_text(encoding="utf-8"))


def download_recorded_chain(result, fetch, dest):
    """Copy the runs a result's `resumed_from` names; their ids, oldest first."""
    chain = [entry.get("run_id") for entry in result.get("resumed_from") or []]
    for run in chain:
        parse_run_id(run)
    if chain:
        root = fetch(chain)
        for run in chain:
            _copy(root, run, dest)
    return chain


def sibling_manifests(root, prefix):
    """``{run: manifest}`` for every stored run of the shard ``prefix`` names.

    Only directory names of the recorded run-id shape are read, so nothing
    else in the repository becomes a path.
    """
    base = Path(root) / "positive-control"
    manifests = {}
    if not base.is_dir():
        return manifests
    for entry in sorted(base.iterdir()):
        match = RUN_ID.fullmatch(entry.name)
        if not match or match.group("rung") != prefix:
            continue
        path = entry / "paid" / "manifest.json"
        if path.is_file():
            try:
                manifests[entry.name] = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise ResumeError("run %s has an unreadable manifest" % entry.name) from exc
    return manifests


def check_siblings(named, chain, fetch):
    """Refuse when a run outside ``chain`` already resumed a run of it."""
    prefix = shard_prefix(named)
    check_not_forked(chain, sibling_manifests(fetch(manifests_of=prefix), prefix))


def reader(dest, recipe_of):
    return lambda run: read_link(run, Path(dest) / run / "paid", recipe_of)


def links_from_env(environ, recipe_of):
    """The downloaded chain, oldest first, re-read from ``$RUNNER_TEMP``."""
    dest = Path(environ["RUNNER_TEMP"]) / "step2-resume"
    return chain_links(resume_run_id(environ), reader(dest, recipe_of))


def planned_keys(cases, samples):
    from pipeline.positive_control_generate import request_order
    return [request_key(c["case_id"], d, a) for c, d, a in request_order(cases, samples)]


def account(environ, links, cases, recipe_of):
    """Early identity, the remainder, and whether this run can finish it."""
    samples = int(environ["STEP2_SAMPLES"])
    keys = planned_keys(cases, samples)
    current = planned_identity(environ["STEP2_RUN_ID"], cases,
                               hashlib.sha256(SPEC.read_text().encode()).hexdigest(),
                               samples, shard_record(environ), len(keys))
    current["candidate_recipe"] = recipe_of(environ["GITHUB_SHA"])
    check_identity(links, current, PLANNED_IDENTITY + ("candidate_recipe",))
    state = plan(links, keys, environ["STEP2_CEILING_USD"])
    report = summary(state)
    sized = generation(len(state["remaining"]), max_seconds=int(environ["STEP2_MAX_SECONDS"]),
                       rpm=rpm_from_env(environ, environ["STEP2_RUNG"]))
    report["remaining_projection"] = sized
    return report, refusals(sized, float(state["run_ceiling_usd"]))


def main(argv=None, environ=None):
    argv = sys.argv[1:] if argv is None else argv
    environ = os.environ if environ is None else environ
    mode = argv[0] if argv else ""
    phase = mode
    try:
        if mode == "validate":
            print(json.dumps(validate(environ), sort_keys=True))
            return 0
        if mode != "download":
            print("usage: step2_resume.py validate|download", file=sys.stderr)
            return 2
        if not validate(environ)["resume"]:
            print(json.dumps({"resume": False}))
            return 0
        from step2_merge import git_recipe
        from step2_shard import cases_from_env
        phase = "download"
        dest = Path(environ["RUNNER_TEMP"]) / "step2-resume"
        fetch = fetcher(environ)
        chain = download_chain(resume_run_id(environ), fetch, dest)
        # A run already resumed by another is never resumed again: its
        # successor's dollars would be spent twice, outside the ceiling.
        phase = "siblings"
        check_siblings(resume_run_id(environ), chain, fetch)
        phase = "chain"
        links = links_from_env(environ, git_recipe)
        phase = "plan"
        bank = Path(environ["RUNNER_TEMP"]) / "step2-bank" / "train.jsonl"
        rows = [json.loads(line) for line in bank.read_text().splitlines() if line.strip()]
        report, why = account(environ, links, cases_from_env(rows, environ), git_recipe)
    except (ResumeError, ShardError) as exc:
        print("step2 resume refused during %s: %s" % (phase, exc), file=sys.stderr)
        return 1
    except Exception as exc:
        # A library message can carry a case id; only the type reaches the log.
        print("step2 resume failed during %s (%s); no private payload printed."
              % (phase, type(exc).__name__), file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    for reason in why:
        print("step2 resume refused: %s" % reason, file=sys.stderr)
    return int(bool(why))


if __name__ == "__main__":
    sys.exit(main())
