"""Does the provider honour `seed`? Counts over paid runs; never a completion.

Every Step 2 request carries `seed = 1111 + draw` with messages fixed by
(case, arm), so the 64-case smoke's 512 (case, draw, arm) keys recur inside
the 192-case targets run, and the targets attempt that stopped at 327 calls
overlaps both. If the seed is honoured, overlapping keys come back byte-
identical and re-buying a prefix buys the same completions again; if not,
the draws are independent and a shard is a fresh sample. Either way the full
rung reuses the prefix (`step2_shard.FULL_SKIP_PREFIX`), so this is a check on
that design, not an input to it.

It reads the private artifact repo and prints, per pair of runs and per arm,
only counts: overlapping keys, byte-identical completions, identical
completion-token counts, identical finish reasons. No case id, completion,
prompt or usage value is printed, and any failure prints its phase and
exception type only.
"""
from __future__ import annotations

from collections import Counter
from itertools import combinations
import json
import os
from pathlib import Path
import re
import sys

#: Actions run ids of the paid runs to compare (2026-09-22): the smoke
#: (35751938025), the complete 192-case targets run (35767396604) and the
#: targets attempt that stopped after 327 calls (35763603648).
DEFAULT_RUNS = ("35751938025", "35767396604", "35763603648")
ARMS = ("bare", "subset-spec")
REPO = "lypning-round02-artifacts"


class CompareError(ValueError):
    """A refusal whose message is safe to print."""


def parse_runs(text):
    runs = [part for part in re.split(r"[\s,]+", text or "") if part] or list(DEFAULT_RUNS)
    if any(not re.fullmatch(r"[0-9]{6,15}", run) for run in runs) or len(set(runs)) < 2:
        raise CompareError("runs must be two or more distinct Actions run ids")
    return runs


def resolve(files, runs):
    """Map each Actions run id to its one positive-control directory.

    Run directories are `<rung>-<commit>-<actions run id>`; matching on the
    suffix lets the operator name a run by the number `gh run list` shows.
    """
    dirs = {}
    for path in files:
        match = re.fullmatch(r"positive-control/([A-Za-z0-9._-]+-([0-9]+))/paid/completions\.jsonl", path)
        if match and match.group(2) in runs:
            if match.group(2) in dirs:
                raise CompareError("run %s names two positive-control directories" % match.group(2))
            dirs[match.group(2)] = match.group(1)
    missing = [run for run in runs if run not in dirs]
    if missing:
        raise CompareError("no paid completions for run(s) %s" % ", ".join(missing))
    return dirs


def keyed(rows):
    """Completions by (case, draw, arm); a repeated key is kept once (the first)."""
    out = {}
    for row in rows:
        key = (row.get("case_id"), row.get("draw"), row.get("arm"))
        out.setdefault(key, row)
    return out


def compare(left, right):
    """Counts of agreement over the keys two runs share, per arm and overall."""
    shared = set(left) & set(right)
    counts = {arm: Counter() for arm in ARMS + ("all",)}
    for key in shared:
        a, b = left[key], right[key]
        tokens = ((a.get("usage") or {}).get("completion_tokens"),
                  (b.get("usage") or {}).get("completion_tokens"))
        for arm in (key[2] if key[2] in ARMS else "other", "all"):
            bucket = counts.setdefault(arm, Counter())
            bucket["overlapping_keys"] += 1
            bucket["identical_completion"] += a.get("completion") == b.get("completion")
            bucket["identical_completion_tokens"] += tokens[0] is not None and tokens[0] == tokens[1]
            bucket["identical_finish_reason"] += a.get("finish_reason") == b.get("finish_reason")
            bucket["identical_seed"] += a.get("seed") == b.get("seed")
    return {arm: dict(sorted(c.items())) for arm, c in counts.items()}


def report(completions_by_run):
    runs = list(completions_by_run)
    return {"runs": {run: {"keys": len(rows)} for run, rows in completions_by_run.items()},
            "pairs": [{"left": a, "right": b,
                       "counts": compare(completions_by_run[a], completions_by_run[b])}
                      for a, b in combinations(runs, 2)]}


def main():
    phase = "arguments"
    try:
        runs = parse_runs(os.environ.get("PROVIDER_SEED_RUNS", ""))
        phase = "inventory"
        from huggingface_hub import HfApi, hf_hub_download
        token = os.environ["HF_TOKEN"].strip()
        api = HfApi(token=token)
        repo = api.whoami()["name"] + "/" + REPO
        info = api.repo_info(repo, repo_type="dataset")
        if info.private is not True:
            raise CompareError("artifact repository must be private")
        dirs = resolve(api.list_repo_files(repo, repo_type="dataset", revision=info.sha), runs)
        completions = {}
        for run in runs:
            phase = "download " + run
            local = hf_hub_download(repo, "positive-control/%s/paid/completions.jsonl" % dirs[run],
                                    repo_type="dataset", revision=info.sha, token=token)
            completions[run] = keyed(json.loads(line) for line in
                                     Path(local).read_text().splitlines() if line.strip())
        phase = "comparison"
        result = report(completions)
    except CompareError as exc:
        print("provider seed read refused during %s: %s" % (phase, exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print("provider seed read failed during %s (%s); no private payload printed."
              % (phase, type(exc).__name__), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
