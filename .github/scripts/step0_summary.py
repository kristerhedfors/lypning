"""Read seed 1111's saved evidence; publish aggregates, never private rows.

No execution, generation, weight download, selection or artifact mutation.
The Hub snapshot and file hashes bind the read. Unknown/malformed evidence
fails closed, with no exception text (which could contain a private row).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
from collections import Counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "training"))
from pipeline.training_metrics import summarize

JOB = "6ab01cbb51992417dfccd64c"
REPO = "headforce/lypning-round02-artifacts"
STEPS = {"sft": (0, 25, 50, 75), "grpo": (0, 5, 10, 15, 20)}
STATUSES = ("correct-native", "correct-fallback", "correct-control",
            "incorrect", "no-code", "unstable")
POPULATIONS = ("coverage", "fallback-control")
FILES = ("pilot/bundle.json", "eval2/bundle.json", "base-dev/evaluations.jsonl",
         "sft/evaluations.jsonl", "grpo/evaluations.jsonl",
         "probe/probe-rollouts.jsonl")


def require(condition):
    if not condition:
        raise ValueError("incomplete or inconsistent evidence")


def case_index(cases):
    require(isinstance(cases, list) and bool(cases))
    result = {}
    for case in cases:
        for key in ("case_id", "family", "split_group"):
            require(isinstance(case[key], str) and bool(case[key]))
        require(case["population"] in POPULATIONS)
        require(case["case_id"] not in result)
        result[case["case_id"]] = case
    return result


def checked_rows(rows, cases, *, draws=4):
    """Require the complete declared population, not just equal partial arms."""
    expected = case_index(cases)
    keys = set()
    for row in rows:
        require(row["case_id"] in expected)
        case = expected[row["case_id"]]
        require(all(row[k] == case[k] for k in ("family", "population", "split_group")))
        require(type(row["draw"]) is int and 0 <= row["draw"] < draws)
        require(type(row["seed"]) is int)
        require(type(row["step"]) is int and row["step"] >= 0)
        require(row["status"] in STATUSES)
        require(type(row["correct"]) is bool and type(row["native"]) is bool)
        require(type(row["truncated"]) is bool)
        require(type(row["completion_tokens"]) is int and row["completion_tokens"] >= 0)
        require(row["correct"] == row["status"].startswith("correct-"))
        require(not row["native"] or row["correct"])
        require(row["status"] != "correct-native" or row["native"])
        require(row["status"] != "correct-fallback" or not row["native"])
        require(not row["truncated"] or row["status"] == "no-code")
        key = (row["case_id"], row["draw"])
        require(key not in keys)
        keys.add(key)
    require(keys == {(cid, d) for cid in expected for d in range(draws)})
    return rows


def metrics(rows):
    # Use the production family macro, but never return dynamic private labels
    # (capabilities/families), row IDs, completions or refusal details.
    safe = [{k: r[k] for k in ("family", "case_id", "draw", "population",
                              "correct", "native", "status", "truncated",
                              "completion_tokens")} for r in rows]
    result = summarize(safe)
    del result["by_capability"]
    result["draw_weighted_correct"] = sum(r["correct"] for r in rows) / len(rows)
    result["draw_weighted_native"] = sum(r["native"] for r in rows) / len(rows)
    return result


def checkpoints(rows, cases, expected_steps, base):
    require({r["step"] for r in rows} == set(expected_steps))
    base_index = {(r["case_id"], r["draw"]): r for r in base}
    groups = {}
    for step in expected_steps:
        group = checked_rows([r for r in rows if r["step"] == step], cases)
        for row in group:
            old = base_index[(row["case_id"], row["draw"])]
            require(all(row[k] == old[k] for k in
                        ("family", "population", "split_group", "seed", "capabilities")))
            if step == 0:
                require(all(row[k] == old[k] for k in
                            ("correct", "native", "status", "truncated", "completion_tokens")))
        groups[step] = metrics(group)
    baseline = groups[0]
    output = []
    for step, stats in groups.items():
        dc = stats["correct"] - baseline["correct"]
        dn = stats["correct_native"] - baseline["correct_native"]
        output.append(dict(step=step, metrics=stats,
                           correct_delta_pp=100 * dc, native_delta_pp=100 * dn,
                           meets_step0_rule=step > 0 and dn >= .02 - 1e-12 and dc >= -.02 - 1e-12))
    return output


def family_sizes(cases):
    case_index(cases)
    def counts(subset):
        sizes = Counter(c["family"] for c in subset)
        return {"cases": len(subset), "families": len(sizes),
                "size_histogram": dict(sorted(Counter(sizes.values()).items())),
                "families_under_five": sum(n < 5 for n in sizes.values()),
                "cases_in_families_under_five": sum(n for n in sizes.values() if n < 5)}
    return dict(counts(cases), by_population={p: counts([c for c in cases if c["population"] == p])
                                            for p in POPULATIONS})


def known_kinds():
    """Only public engine literals may become public labels; details never do."""
    kinds = set()
    for path in (ROOT / "src/lypning/assets/rust/src").glob("*.rs"):
        kinds.update(re.findall(r'unsupported\(\s*"([a-z][a-z0-9-]*)"', path.read_text()))
    return kinds


def refusal_vector(rows):
    allowed = known_kinds()
    by_kind = Counter()
    fallback = [r for r in rows if r["status"] == "correct-fallback"]
    missing = 0
    for row in fallback:
        kinds = set()
        for item in row.get("refusals", []):
            require(isinstance(item, (list, tuple)) and len(item) == 2)
            require(type(item[0]) is int and isinstance(item[1], str))
            # Consume but never expose the detail, even for unknown kinds.
            match = re.fullmatch(r"lypning-l: unsupported: ([a-z][a-z0-9-]*): [^\n]+", item[1])
            require(match is not None)
            kind = match.group(1)
            kinds.add(kind if kind in allowed else "other-kind")
        missing += not bool(kinds)
        by_kind.update(kinds)
    return {"fallback_draws": len(fallback), "without_refusal": missing,
            "draws_by_kind": dict(sorted(by_kind.items()))}


def probe_summary(probe, base):
    groups = {}
    for row in probe:
        groups.setdefault(row["case_id"], []).append(row)
    ids = set(groups)
    base_ids = {r["case_id"] for r in base}
    histogram = Counter(sum(r["native"] for r in rs) for rs in groups.values())
    return {"probe": metrics(probe), "base_dev": metrics(base),
            "matched_cases": len(ids & base_ids), "probe_only_cases": len(ids - base_ids),
            "base_only_cases": len(base_ids - ids),
            "train_cases_by_native_draw_count": dict(sorted(histogram.items())),
            "probe_refusals": refusal_vector(probe), "base_dev_refusals": refusal_vector(base)}


def summarise(data):
    pilot = data["pilot/bundle.json"]["cases"]
    train = [c for c in pilot if c["split"] == "train"]
    dev = [c for c in pilot if c["split"] == "dev"]
    base = checked_rows(data["base-dev/evaluations.jsonl"], dev)
    require({r["step"] for r in base} == {0})
    probe = checked_rows(data["probe/probe-rollouts.jsonl"], train)
    require({r["step"] for r in probe} == {0})
    stages = {stage: checkpoints(data[stage + "/evaluations.jsonl"], dev, steps, base)
              for stage, steps in STEPS.items()}
    qualifying = [{"stage": stage, "step": row["step"]}
                  for stage, rows in stages.items() for row in rows if row["meets_step0_rule"]]
    return {"stages": stages, "qualifying_checkpoints": qualifying,
            "probe_vs_base_dev": probe_summary(probe, base),
            "dev_family_sizes": family_sizes(dev),
            "eval2_family_sizes": family_sizes(data["eval2/bundle.json"]["cases"])}


def main():
    # Catch at the private-data boundary. JSON/parser/library exceptions can
    # carry private payloads; publish only a fixed failure and the phase.
    phase = "authentication"
    try:
        from huggingface_hub import HfApi, hf_hub_download
        token = os.environ["HF_TOKEN"].strip()
        require(bool(token))
        info = HfApi(token=token).repo_info(REPO, repo_type="dataset")
        require(info.private and re.fullmatch(r"[0-9a-f]{40}", info.sha) is not None)
        revision = info.sha
        data, hashes = {}, {}
        for name in FILES:
            phase = name
            local = hf_hub_download(REPO, "round-02/%s/%s" % (JOB, name),
                                    repo_type="dataset", revision=revision, token=token)
            raw = Path(local).read_bytes()
            hashes[name] = hashlib.sha256(raw).hexdigest()
            data[name] = ([json.loads(line) for line in raw.splitlines() if line.strip()]
                          if name.endswith(".jsonl") else json.loads(raw))
        phase = "validation and aggregation"
        result = summarise(data)
        result.update(job=JOB, repository=REPO, revision=revision, sha256=hashes)
        print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
        return 0
    except Exception:
        print("Step 0 read failed during %s; no private payload printed." % phase, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
