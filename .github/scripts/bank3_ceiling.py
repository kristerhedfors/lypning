"""Pull the banked bank-v3 batches into RUNNER_TEMP in one schema. Prints counts only.

The Hub is the device: HF_TOKEN exists only as an Actions secret, so a read of
the private dataset happens in CI or not at all (`.claude/skills/round02-
evidence/SKILL.md`). This is a read. It downloads nothing but `bank-v3/**`,
uploads nothing, runs no model-written code and bills nothing.

TWO LAYOUTS, ONE SCHEMA. The batches already banked predate `pipeline.synth`
and carry the kind in the FILE NAME — `native.jsonl`, `repaired.jsonl`,
`ceiling.jsonl`. `synth.to_case` writes one `cases.jsonl` whose rows carry
`synth.kind` instead. Both are normalised here to the schema-3 shape
`pipeline.bank_native` reads, and which layout each batch was in is printed,
because a batch that turns out to hold neither is a failed read and not an
empty bank.

`kristerhedfors/lypning` IS PUBLIC and an Actions log is world-readable. This
prints file names, row counts and nothing else: never a task text, a program, a
test, a case id or a refusal detail. The normalised JSONL stays in RUNNER_TEMP
and is never uploaded as an artifact.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

#: The old layout's file stem is the `synth.kind` the row would carry today.
BY_FILENAME = ("native", "repaired", "ceiling")
NEW_LAYOUT = "cases.jsonl"

#: `synth.POPULATION_OF`, which cannot be imported here: this script runs before
#: `PYTHONPATH` includes `training/` in some jobs. A mismatch is caught by
#: `training/tests/test_bank_native.py::test_the_kind_vocabulary_is_asked_of_synth_and_never_copied`
#: for the library; here the stamp is only applied to a file already named for it.
POPULATION_OF = {"native": "coverage", "repaired": "coverage", "ceiling": "fallback-control"}


def rows_of(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def stamped(row, kind):
    """The old layout's row with the kind its file name states, and nothing else changed."""
    synth = dict(row.get("synth") or {})
    synth.setdefault("kind", kind)
    out = dict(row, synth=synth)
    out.setdefault("population", POPULATION_OF[kind])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, required=True, help="normalised JSONL, RUNNER_TEMP only")
    ap.add_argument("--batch", action="append", default=[],
                    help="restrict to these batch ids (default: every batch banked)")
    args = ap.parse_args()

    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        print("HF_TOKEN is not set", file=sys.stderr)
        return 2
    from huggingface_hub import HfApi, snapshot_download

    client = HfApi(token=token)
    repo = "%s/%s" % (client.whoami()["name"],
                      os.environ.get("WORK_REPO_NAME", "lypning-round02-artifacts"))
    info = client.repo_info(repo, repo_type="dataset")
    if not info.private:
        print("refusing: %s is not private" % repo, file=sys.stderr)
        return 2
    local = Path(snapshot_download(repo, repo_type="dataset",
                                   allow_patterns=["bank-v3/batches/**"], token=token))
    batches = sorted(p for p in (local / "bank-v3" / "batches").glob("*") if p.is_dir())
    if args.batch:
        batches = [p for p in batches if p.name in set(args.batch)]
    if not batches:
        # A read of nothing is not a clean read of an empty bank.
        print("no bank-v3 batches matched", file=sys.stderr)
        return 1

    merged, per_batch = [], []
    for batch in batches:
        rows, layout = [], "none"
        if (batch / NEW_LAYOUT).is_file():
            layout = NEW_LAYOUT
            rows = rows_of(batch / NEW_LAYOUT)
        else:
            for kind in BY_FILENAME:
                path = batch / ("%s.jsonl" % kind)
                if path.is_file():
                    layout = "by-filename"
                    rows.extend(stamped(r, kind) for r in rows_of(path))
        report = {}
        if (batch / "report.json").is_file():
            # `synth.run`'s report carries counts and refusal KINDS only — no
            # task text and no program — so it is safe in a public log.
            report = json.loads((batch / "report.json").read_text(encoding="utf-8"))
        missing_family = sum(1 for r in rows if not r.get("family"))
        missing_kind = sum(1 for r in rows if not (r.get("synth") or {}).get("kind"))
        per_batch.append({"batch": batch.name, "layout": layout, "rows": len(rows),
                          "files": sorted(p.name for p in batch.glob("*.jsonl")),
                          "rows_without_family": missing_family,
                          "rows_without_synth_kind": missing_kind,
                          "report": report})
        merged.extend(rows)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in merged),
                           encoding="utf-8")
    print(json.dumps({"repo": repo, "batches": per_batch, "merged_rows": len(merged),
                      "output": str(args.output)}, indent=2, sort_keys=True))
    # A row with no family cannot be placed in EVAL2.md section 4's unit, and a
    # row with no kind cannot be read for the first-draft mix. Both are the
    # answer to "can this bank be assessed", so neither exits 0 in silence.
    if any(b["rows_without_family"] or b["rows_without_synth_kind"] for b in per_batch):
        print("some rows carry no family or no synth.kind; the mix read cannot place them",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
