"""Step 3: count contrastive pair supply in a graded Step 2 run. Aggregates only.

`training/PLAN.md` Step 3: per train prompt, a positive is a correct-native
draw and a negative a correct-but-refused (``correct-fallback``) draw. This
counts the train prompts that hold both, before anything is built, so the
Step 3 decision (fewer than ~300 pair prompts: carry the signal in RL, arm C,
not a preference arm) is read from the graded rows rather than estimated.

Sources are columns, never pooled silently:

- ``bare`` and ``subset-spec``: the two arms of the named graded
  positive-control run (``positive-control/<run>/grade/rows.jsonl``).
- ``probe``: seed 1111's probe rollouts (the base policy on the train prompts
  of seed 1111's pilot), included only when their case ids join the run's and
  every shared case agrees on family and population. The join is reported
  whether or not it holds.

A pair whose positive comes from ``subset-spec`` is a context-distillation
pair: the positive was drawn with the subset spec in context, and training
would pair it with the bare prompt. ``any_arm`` therefore also reports how
many of its prompts need such a positive.

Draws are counted as the rows record them. Rows carry no program text, so two
draws that wrote the same program count twice: every pair count is an upper
bound on distinct programs. Controls (``fallback-control``) are never paired;
they are counted separately.

Nothing here prints a case id, family name, program, task, expected output or
refusal detail -- this runs in a public Actions log. A refusal contributes
only its kind, and only when that kind is a literal in the engine source
(`step0_summary.known_kinds`); any other kind is ``other-kind``. Malformed
evidence fails closed with a phase and source line numbers, never exception
text. Everything printed passes `pipeline.public_view`.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "training"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline.public_view import public_view  # noqa: E402
from step0_summary import JOB as PROBE_JOB, known_kinds  # noqa: E402

ARMS = ("bare", "subset-spec")
PROBE = "probe"
POPULATIONS = ("coverage", "fallback-control")
STATUSES = ("correct-native", "correct-fallback", "correct-control",
            "incorrect", "no-code", "unstable", "engine-mismatch")
#: Per-prompt caps on the pairs one prompt may contribute.
CAPS = (1, 2, 4)
#: PLAN.md Step 3: fewer pair prompts than this and the preference arm is
#: under-powered.
PAIR_PROMPT_THRESHOLD = 300
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")
REFUSAL = re.compile(r"lypning-l: unsupported: ([a-z][a-z0-9-]*): [^\n]+")
PROBE_PATH = "round-02/%s/probe/probe-rollouts.jsonl" % PROBE_JOB

#: A mode is a set of pools; a pool pairs positives from some sources with
#: negatives from some sources, within one prompt. A prompt is a pair prompt
#: in a mode when any of its pools holds a positive and a negative.
STEP2_MODES = {
    "bare": ((("bare",), ("bare",)),),
    "subset-spec": ((("subset-spec",), ("subset-spec",)),),
    "same_arm": ((("bare",), ("bare",)), (("subset-spec",), ("subset-spec",))),
    "any_arm": ((ARMS, ARMS),),
    # Positive drawn with the spec in context, negative from either arm.
    "context_distillation": ((("subset-spec",), ARMS),),
}
PROBE_MODES = {
    "probe": (((PROBE,), (PROBE,)),),
    # The two unconditioned samplers: the Step 2 bare arm and seed 1111's probe.
    "unconditioned": ((("bare", PROBE), ("bare", PROBE)),),
    "any_source": ((ARMS + (PROBE,), ARMS + (PROBE,)),),
}


class EvidenceError(ValueError):
    """Incomplete or inconsistent evidence. The message is fixed text."""


def require(condition):
    if not condition:
        raise EvidenceError("incomplete or inconsistent evidence")


def refusal_kinds(row, allowed):
    """The public refusal kinds of one draw; details are consumed, never kept."""
    score = row.get("score")
    items = score.get("refusals") if isinstance(score, dict) else row.get("refusals")
    kinds = set()
    for item in items or ():
        require(isinstance(item, (list, tuple)) and len(item) == 2)
        require(type(item[0]) is int and isinstance(item[1], str))
        match = REFUSAL.fullmatch(item[1].strip())
        require(match is not None)
        kinds.add(match.group(1) if match.group(1) in allowed else "other-kind")
    return frozenset(kinds)


def check_row(row, source):
    require(isinstance(row, dict))
    for key in ("case_id", "family"):
        require(isinstance(row.get(key), str) and bool(row[key]))
    require(row.get("population") in POPULATIONS)
    require(row.get("status") in STATUSES)
    require(type(row.get("draw")) is int and row["draw"] >= 0)
    require(type(row.get("correct")) is bool and type(row.get("native")) is bool)
    require(row["correct"] == row["status"].startswith("correct-"))
    require(not row["native"] or row["correct"])
    require(row["status"] != "correct-native" or row["native"])
    require(row["status"] != "correct-fallback" or not row["native"])
    require(row["status"] != "correct-control" or row["population"] == "fallback-control")
    require(row["status"] not in ("correct-native", "correct-fallback")
            or row["population"] == "coverage")
    if source == PROBE:
        require(row.get("step", 0) == 0)
    else:
        require(row.get("arm") == source)


def index_rows(rows, sources, allowed, cases=None):
    """Validate one evidence file into ``{case: {"family", "population", source: [draw]}}``.

    ``sources`` are the sources the file must cover completely: every case
    with the same draws 0..k-1 in each. A draw is (status, correct, native,
    refusal kinds). Returns the index and k.
    """
    cases = {} if cases is None else cases
    seen = set()
    draws = {}
    for row in rows:
        source = row.get("arm") if sources == ARMS else PROBE
        require(source in sources)
        check_row(row, source)
        key = (row["case_id"], source, row["draw"])
        require(key not in seen)
        seen.add(key)
        case = cases.setdefault(row["case_id"], {"family": row["family"],
                                                 "population": row["population"]})
        require(case["family"] == row["family"] and case["population"] == row["population"])
        case.setdefault(source, []).append({
            "status": row["status"], "correct": row["correct"], "native": row["native"],
            "kinds": refusal_kinds(row, allowed)})
        draws.setdefault((row["case_id"], source), set()).add(row["draw"])
    require(bool(seen))
    ids = {case_id for case_id, _ in draws}
    k = len(next(iter(draws.values())))
    for case_id in ids:
        for source in sources:
            require(draws.get((case_id, source)) == set(range(k)))
    return cases, k


def join_probe(step2, probe):
    """How seed 1111's probe case set meets the run's; aggregates only."""
    shared = set(step2) & set(probe)
    disagree = sum(step2[c]["family"] != probe[c]["family"]
                   or step2[c]["population"] != probe[c]["population"] for c in shared)
    usable = bool(shared) and disagree == 0
    return {"probe_cases": len(probe), "run_cases": len(step2), "shared_cases": len(shared),
            "probe_only_cases": len(set(probe) - set(step2)),
            "run_only_cases": len(set(step2) - set(probe)),
            "identical_case_sets": set(step2) == set(probe),
            "shared_cases_disagreeing_on_family_or_population": disagree,
            "included": usable}


def _pool_counts(case, pool):
    positives = [d for s in pool[0] for d in case.get(s, ()) if d["status"] == "correct-native"]
    negatives = [(s, i, d) for s in pool[1] for i, d in enumerate(case.get(s, ()))
                 if d["status"] == "correct-fallback"]
    return positives, negatives


def coverage_mode(cases, pools, sources):
    """Pair supply of one mode over the coverage prompts that have every source."""
    prompts = [c for c in cases.values()
               if c["population"] == "coverage" and all(s in c for s in sources)]
    pair_prompts = 0
    capped = Counter()
    disjoint = Counter()
    families = set()
    kind_draws, kind_prompts = Counter(), Counter()
    negatives_used = without_refusal = 0
    for case in prompts:
        pairs = lone = 0
        used = {}
        for pool in pools:
            positives, negatives = _pool_counts(case, pool)
            if positives and negatives:
                pairs += len(positives) * len(negatives)
                lone += min(len(positives), len(negatives))
                for source, i, draw in negatives:
                    used[(source, i)] = draw
        if not pairs:
            continue
        pair_prompts += 1
        families.add(case["family"])
        for cap in CAPS:
            capped[cap] += min(cap, pairs)
            disjoint[cap] += min(cap, lone)
        capped["all"] += pairs
        disjoint["all"] += lone
        prompt_kinds = set()
        for draw in used.values():
            negatives_used += 1
            without_refusal += not draw["kinds"]
            kind_draws.update(draw["kinds"])
            prompt_kinds |= draw["kinds"]
        kind_prompts.update(prompt_kinds)
    return {
        "coverage_prompts": len(prompts),
        "pair_prompts": pair_prompts,
        "clears_pair_prompt_threshold": pair_prompts >= PAIR_PROMPT_THRESHOLD,
        # Pairs as positive x negative within each pool, per prompt capped.
        "pairs_at_most_per_prompt": {str(k): capped[k] for k in CAPS + ("all",)},
        # Pairs in which no draw is used twice.
        "disjoint_pairs_at_most_per_prompt": {str(k): disjoint[k] for k in CAPS + ("all",)},
        "families_with_pair_prompt": len(families),
        "negatives": {"draws": negatives_used, "without_refusal": without_refusal,
                      "draws_by_kind": dict(sorted(kind_draws.items())),
                      "pair_prompts_by_kind": dict(sorted(kind_prompts.items()))},
    }


def any_arm_split(cases):
    """Of the any-arm pair prompts, how many need a spec-conditioned positive."""
    own = context_only = 0
    for case in cases.values():
        if case["population"] != "coverage" or not all(s in case for s in ARMS):
            continue
        positives = {s: sum(d["status"] == "correct-native" for d in case[s]) for s in ARMS}
        negatives = sum(d["status"] == "correct-fallback" for s in ARMS for d in case[s])
        if not negatives or not sum(positives.values()):
            continue
        if positives["bare"]:
            own += 1
        else:
            context_only += 1
    return {"with_a_bare_positive": own, "only_context_distillation": context_only}


def controls(cases, sources):
    """Control prompts per source: ran natively, fell back, or both. Never paired."""
    out = {}
    for label, group in [(s, (s,)) for s in sources] + [("any", sources)]:
        prompts = [c for c in cases.values()
                   if c["population"] == "fallback-control" and all(s in c for s in group)]
        ran = fell = both = 0
        draws = Counter()
        for case in prompts:
            mine = [d for s in group for d in case[s]]
            native = any(d["correct"] and d["native"] for d in mine)
            fallback = any(d["correct"] and not d["native"] for d in mine)
            ran += native
            fell += fallback
            both += native and fallback
            for d in mine:
                draws["correct_native_ran" if d["correct"] and d["native"] else
                      "correct_fallback" if d["correct"] else "not_correct"] += 1
        out[label] = {"control_prompts": len(prompts),
                      "prompts_with_native_ran_draw": ran,
                      "prompts_with_fallback_draw": fell,
                      "prompts_with_both": both,
                      "draws": dict(sorted(draws.items()))}
    return out


def statuses(cases, sources):
    out = {}
    for source in sources:
        out[source] = {population: dict(sorted(Counter(
            d["status"] for c in cases.values() if c["population"] == population
            for d in c.get(source, ())).items())) for population in POPULATIONS}
    return out


def summarise(step2_rows, probe_rows=None):
    """Aggregates over one graded run and, when it joins, seed 1111's probe."""
    allowed = known_kinds()
    cases, k = index_rows(step2_rows, ARMS, allowed)
    result = {"schema": 1, "cases": len(cases), "samples_per_arm": k,
              "families": {p: len({c["family"] for c in cases.values() if c["population"] == p})
                           for p in POPULATIONS},
              "pair_prompt_threshold": PAIR_PROMPT_THRESHOLD}
    result["families"]["all"] = len({c["family"] for c in cases.values()})
    sources = ARMS
    modes = dict(STEP2_MODES)
    if probe_rows is None:
        result["probe"] = {"included": False, "reason": "not read"}
    else:
        probe, probe_k = index_rows(probe_rows, (PROBE,), allowed)
        join = dict(join_probe(cases, probe), samples_per_case=probe_k)
        result["probe"] = join
        if join["included"]:
            for case_id, case in cases.items():
                if case_id in probe:
                    case[PROBE] = probe[case_id][PROBE]
            sources = ARMS + (PROBE,)
            modes.update(PROBE_MODES)
    result["statuses"] = statuses(cases, sources)
    coverage = {}
    for name, pools in modes.items():
        needed = sorted({s for pool in pools for side in pool for s in side})
        coverage[name] = coverage_mode(cases, pools, needed)
    coverage["any_arm"]["pair_prompts_split"] = any_arm_split(cases)
    result["coverage"] = coverage
    result["controls"] = controls(cases, sources)
    result["modes_clearing_threshold"] = sorted(
        name for name, stats in coverage.items() if stats["clears_pair_prompt_threshold"])
    return result


def read_jsonl(raw):
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def main():
    phase = "arguments"
    try:
        run = os.environ.get("STEP3_RUN_ID", "").strip()
        require(RUN_ID.fullmatch(run) is not None and ".." not in run)
        with_probe = os.environ.get("STEP3_INCLUDE_PROBE", "true").strip().lower() != "false"
        phase = "authentication"
        from huggingface_hub import HfApi, hf_hub_download
        token = os.environ["HF_TOKEN"].strip()
        require(bool(token))
        api = HfApi(token=token)
        repo = api.whoami()["name"] + "/lypning-round02-artifacts"
        info = api.repo_info(repo, repo_type="dataset")
        require(info.private and re.fullmatch(r"[0-9a-f]{40}", info.sha or "") is not None)
        phase = "inventory"
        files = set(api.list_repo_files(repo, repo_type="dataset", revision=info.sha))
        rows_path = "positive-control/%s/grade/rows.jsonl" % run
        if rows_path not in files:
            print("step3 pairs: the named run has no graded rows", file=sys.stderr)
            return 1
        hashes, data = {}, {}
        wanted = {"rows": rows_path}
        if with_probe and PROBE_PATH in files:
            wanted["probe"] = PROBE_PATH
        for name, path in wanted.items():
            phase = "download " + name
            raw = Path(hf_hub_download(repo, path, repo_type="dataset", revision=info.sha,
                                       token=token)).read_bytes()
            hashes[name] = hashlib.sha256(raw).hexdigest()
            data[name] = read_jsonl(raw)
        phase = "validation and counting"
        result = summarise(data["rows"], data.get("probe"))
        if "probe" not in data:
            result["probe"] = {"included": False,
                               "reason": "not requested" if not with_probe else "absent"}
        result.update(run=run, revision=info.sha, sha256=hashes)
        print(json.dumps(public_view(result), sort_keys=True, indent=2, allow_nan=False))
        return 0
    except Exception as exc:
        print("step3 pairs failed during %s; no private payload printed." % phase, file=sys.stderr)
        lines = [frame.lineno for frame in traceback.extract_tb(exc.__traceback__)
                 if frame.filename == __file__]
        print("Reader source lines: " + json.dumps(public_view(lines)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
