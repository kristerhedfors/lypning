"""Project a round-02 pilot job's wall clock, stage by stage, from measured rates.

WHY. The pilot raised `--eval-sequences` to 256 and dev-selection draws to 16
without either being run on hardware (`reports/2026-09-22-evaluation-cost.md`:
"a real hardware smoke is still required"), and seed 1111's eval-2 arm did not
finish inside the job. Whether arm A fits one 720-minute job, or needs its
eval-2 moved into a second job (`PILOT_EVAL2=separate`), is arithmetic over
three measured numbers -- seconds per `generate` call at a batch size, SFT
seconds per optimizer step, seconds to load the model -- and the planned
schedule. This module is that arithmetic and nothing else.

WHAT IT COUNTS, stage by stage, exactly as `round02_pilot.sh` runs them:

  prep            deps, engine, handshake, bank, review, both preparations
  base-dev        one dev evaluation
  sft             step 0 AND every `eval_every`th step AND the last step are
                  each a full dev evaluation (`train_verified.run` measures
                  step 0 afresh; nothing reuses base-dev), plus the steps
  sft-dev-reload  one more dev evaluation of the selected adapter
  probe           train cases x generations
  test            per arm, test cases x 4
  eval2           per arm, eval-2 cases x 16 (worst case: no step-0 reuse)

Every GPU stage is its own `train_verified.py` process and loads the model
once; the 55 GB download is paid once per job (the cache serves the rest).
Generation is modelled per `generate` call, as `verified_evaluation.chunked`
cuts it, because a call's wall clock is set by its longest completion and not
by how full the batch is; scoring a chunk follows its generation, on the pool.

Stdlib only, loaded by path (`hwsmoke.py`, the tests) like `launch.py`; no
I/O outside `main`. It prints aggregates and nothing case-level exists here.
"""
from __future__ import annotations

import argparse
import json
import sys

#: The single-job ceiling: `PILOT_TIMEOUT` in round02.yml, `launch.BANKED_TIMEOUT`.
JOB_CEILING_MINUTES = 720
#: Fraction of the ceiling a projection must leave free: checkpoint uploads,
#: the reports, the in-container TERM a minute early, and plain noise.
DEFAULT_MARGIN = 0.10
#: Everything before a fresh pilot's first GPU stage. A STATED CONSTANT: no
#: stage timing is recorded in this tree. Seed 1111's launch log (Actions run
#: 35527021744, 2026-09-20) received the `sft --plan` marker at 18:09:00Z,
#: 16.6 minutes after the job's `startedAt` of 17:52:26Z (metadata audit
#: 35680032058); a log line arrives after it is written, so that bounds that
#: job's preparation from above. Rounded up to 20 for a bank prepared afresh.
PREP_MINUTES = 20.0
#: Before a split eval-2 job's first GPU stage: deps, engine and the pilot
#: job's adapter and eval-2 bundle; no review and no preparation. Stated, not
#: measured.
EVAL2_PREP_MINUTES = 10.0
#: Pool worker-seconds to verify one draw. Stated, not measured by the
#: hardware smoke, which has no pool: `verified_evaluation`'s docstring
#: records ~13 pooled-sandbox requests at ~1.6 s each (job 6aaa4b2c,
#: 2026-09-16).
SCORE_WORKER_SECONDS_PER_DRAW = 20.8

#: The operator-approved arm-A job (2026-09-23): bank v3-20260920b at split
#: seed 1111 is 1,355 / 306 / 315 train / dev / test cases and an 803-case
#: eval-2 bundle (seed 1111's prepared bundles); 4,197 target rows at batch 4
#: is 1,050 steps; dev selection at 16 draws, evaluated every 350 steps; the
#: probe at 4 generations is 5,420 draws; test at 4 draws and eval-2 at 16,
#: for two arms (base and SFT; GRPO_STEPS is 0).
PLAN = {"dev_cases": 306, "dev_draws": 16, "sft_steps": 1050, "eval_every": 350,
        "probe_cases": 1355, "probe_draws": 4, "test_cases": 315, "test_draws": 4,
        "eval2_cases": 803, "eval2_draws": 16, "arms": 2, "eval_sequences": 256,
        "score_workers": 48}


def sft_evaluations(steps, every):
    """Dev evaluations one SFT stage runs: step 0, each `every`th step, and the last.

    Mirrors `verified_stages.train_sft` (`step % every == 0 or step == steps`)
    plus `train_verified.run`'s `measure(0)`.
    """
    if steps < 1 or every < 1:
        raise ValueError("steps and eval_every must be positive")
    return 1 + len(set(range(every, steps + 1, every)) | {steps})


def chunk_batches(cases, draws, sequences_per_call):
    """Sequences per `generate` call, in order, as `verified_evaluation.chunked` cuts them."""
    per_chunk = max(1, int(sequences_per_call) // max(1, int(draws)))
    full, rest = divmod(int(cases), per_chunk)
    return [per_chunk * draws] * full + ([rest * draws] if rest else [])


def batch_seconds_from(measured):
    """seconds(batch) for one `generate` call, from {batch: seconds} measured.

    Two sizes fit a line; below the smaller size the line is floored at
    proportional scaling, so a superlinear fit cannot go to zero. One size is
    taken as the per-call floor below it (decode-bound: a call lasts as long
    as its longest completion) and proportional above it.
    """
    points = sorted((int(b), float(s)) for b, s in measured.items() if s is not None)
    if not points or any(b <= 0 or s <= 0 for b, s in points):
        raise ValueError("need at least one positive (batch, seconds) measurement")
    if len(points) == 1:
        (b0, s0), = points
        return lambda batch: s0 if batch <= b0 else s0 * batch / b0
    (b1, s1), (b2, s2) = points[0], points[-1]
    slope = (s2 - s1) / (b2 - b1)
    intercept = s1 - slope * b1

    def seconds(batch):
        line = intercept + slope * batch
        return max(line, s1 * batch / b1) if batch < b1 else max(line, min(s1, s2))
    return seconds


def flat_rate(draws_per_minute):
    """seconds(batch) at a flat draws-per-minute rate that already includes scoring."""
    if draws_per_minute <= 0:
        raise ValueError("draws per minute must be positive")
    return lambda batch: batch * 60.0 / draws_per_minute


def evaluation_minutes(cases, draws, sequences_per_call, batch_seconds,
                       score_worker_seconds, score_workers):
    """Wall minutes of one evaluation: each call's generation, then its scoring."""
    total = 0.0
    for batch in chunk_batches(cases, draws, sequences_per_call):
        total += batch_seconds(batch) + batch * score_worker_seconds / max(1, score_workers)
    return total / 60.0


def project(batch_seconds, sft_seconds_per_step, load_minutes, download_minutes, plan=None, *,
            score_worker_seconds=SCORE_WORKER_SECONDS_PER_DRAW, prep_minutes=PREP_MINUTES,
            eval2_prep_minutes=EVAL2_PREP_MINUTES, ceiling_minutes=JOB_CEILING_MINUTES,
            margin=DEFAULT_MARGIN):
    """Minutes per stage for one pilot job, and whether it fits one job or two.

    `batch_seconds` is a function from sequences per call to seconds
    (`batch_seconds_from`, `flat_rate`). With `flat_rate` pass
    `score_worker_seconds=0`: that rate already includes scoring.
    """
    plan = dict(PLAN, **(plan or {}))
    seq, workers = plan["eval_sequences"], plan["score_workers"]

    def evaluation(cases, draws):
        return evaluation_minutes(cases, draws, seq, batch_seconds, score_worker_seconds, workers)

    dev = evaluation(plan["dev_cases"], plan["dev_draws"])
    sft_evals = sft_evaluations(plan["sft_steps"], plan["eval_every"])
    arms = plan["arms"]
    stages = [
        {"stage": "prep", "job": "pilot", "minutes": prep_minutes + download_minutes, "draws": 0},
        {"stage": "base-dev", "job": "pilot", "minutes": load_minutes + dev,
         "draws": plan["dev_cases"] * plan["dev_draws"]},
        {"stage": "sft", "job": "pilot",
         "minutes": load_minutes + sft_evals * dev + plan["sft_steps"] * sft_seconds_per_step / 60.0,
         "draws": sft_evals * plan["dev_cases"] * plan["dev_draws"], "evaluations": sft_evals},
        {"stage": "sft-dev-reload", "job": "pilot", "minutes": load_minutes + dev,
         "draws": plan["dev_cases"] * plan["dev_draws"]},
        {"stage": "probe", "job": "pilot",
         "minutes": load_minutes + evaluation(plan["probe_cases"], plan["probe_draws"]),
         "draws": plan["probe_cases"] * plan["probe_draws"]},
        {"stage": "test", "job": "pilot",
         "minutes": arms * (load_minutes + evaluation(plan["test_cases"], plan["test_draws"])),
         "draws": arms * plan["test_cases"] * plan["test_draws"]},
        {"stage": "eval2", "job": "eval2",
         "minutes": arms * (load_minutes + evaluation(plan["eval2_cases"], plan["eval2_draws"])),
         "draws": arms * plan["eval2_cases"] * plan["eval2_draws"]},
    ]
    for stage in stages:
        stage["minutes"] = round(stage["minutes"], 1)
    pilot = sum(s["minutes"] for s in stages if s["job"] == "pilot")
    eval2 = sum(s["minutes"] for s in stages if s["job"] == "eval2")
    budget = ceiling_minutes * (1 - margin)
    same_job = pilot + eval2
    split_eval2 = eval2_prep_minutes + download_minutes + eval2
    if same_job <= budget:
        verdict = "same-job"
    elif pilot <= budget and split_eval2 <= budget:
        verdict = "separate"
    else:
        verdict = "does-not-fit"
    return {"stages": stages, "plan": plan, "sft_evaluations": sft_evals,
            "load_minutes": round(load_minutes, 1), "download_minutes": round(download_minutes, 1),
            "sft_seconds_per_step": round(sft_seconds_per_step, 2),
            "score_worker_seconds_per_draw": score_worker_seconds,
            "ceiling_minutes": ceiling_minutes, "margin": margin, "budget_minutes": round(budget, 1),
            "same_job_minutes": round(same_job, 1),
            "split": {"pilot_minutes": round(pilot, 1), "eval2_minutes": round(split_eval2, 1)},
            "fits_single_job": same_job <= budget,
            "fits_split": pilot <= budget and split_eval2 <= budget,
            "verdict": verdict,
            "assumes": "no step-0 eval-2 reuse; prep %.0f min and eval-2 prep %.0f min are stated "
                       "constants; scoring at %.1f worker-s/draw" % (prep_minutes, eval2_prep_minutes,
                                                                    score_worker_seconds)}


#: The three readings of one smoke. `measured`: the pilot-decoding calls and the
#: max-length SFT rows. `upper`: every `generate` call lasts as long as the
#: forced full-length 256 call (`generation_full_length`), because a call ends
#: at its LONGEST completion and a real 256-draw chunk truncates at
#: max_new_tokens often (seed 1111's base dev truncated 0.16% of draws,
#: `reports/2026-09-21-codex-step0-aggregates.json`: about one call in three
#: of 256), while the public starter tasks the smoke prompts with are
#: short and may never reach it. `lower`: the pilot-decoding calls and
#: typical-length SFT rows (`sft_typical`).
READINGS = {"measured": ("generation", "sft"), "upper": ("generation_full_length", "sft"),
            "lower": ("generation", "sft_typical")}


def from_hwsmoke(report, plan=None, draws_per_minute=None, reading="measured", **kw):
    """`project` over a `hwsmoke.json`: its generation calls, SFT step and load.

    `reading` picks which measurements (`READINGS`). `draws_per_minute`, when
    given, replaces the generation measurement with a flat rate that already
    includes scoring (e.g. an earlier job's observed rate), so the two
    readings can be printed side by side.
    """
    if reading not in READINGS:
        raise ValueError("reading must be one of %s" % ", ".join(sorted(READINGS)))
    generation_key, sft_key = READINGS[reading]
    load = (report.get("load_seconds") or 0) / 60.0
    download = (report.get("download_seconds") or 0) / 60.0
    step = (report.get(sft_key) or {}).get("seconds_per_step")
    if step is None:
        raise ValueError("the hardware smoke has no %s seconds per step" % sft_key)
    if draws_per_minute:
        return project(flat_rate(draws_per_minute), step, load, download, plan,
                       score_worker_seconds=0.0, **kw)
    rows = report.get(generation_key) or []
    rows = [rows] if isinstance(rows, dict) else rows
    measured = {g["sequences"]: g["seconds"] for g in rows
                if isinstance(g, dict) and g.get("seconds") and not g.get("error")}
    return project(batch_seconds_from(measured), step, load, download, plan, **kw)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--hwsmoke", required=True, help="hwsmoke.json written by training/hf/hwsmoke.py")
    p.add_argument("--draws-per-minute", type=float,
                   help="replace the generation measurement with a flat rate that includes scoring")
    for key, value in PLAN.items():
        p.add_argument("--" + key.replace("_", "-"), type=int, default=value)
    p.add_argument("--margin", type=float, default=DEFAULT_MARGIN)
    p.add_argument("--reading", choices=sorted(READINGS), default="measured",
                   help="measured (pilot decoding, max-length SFT rows), upper (every call "
                        "full-length) or lower (typical-length SFT rows)")
    args = p.parse_args(argv)
    with open(args.hwsmoke, encoding="utf-8") as fh:
        report = json.load(fh)
    plan = {key: getattr(args, key) for key in PLAN}
    try:
        got = from_hwsmoke(report, plan, args.draws_per_minute, args.reading, margin=args.margin)
    except ValueError as exc:
        print("projection: %s" % exc, file=sys.stderr)
        return 1
    print(json.dumps(got, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
