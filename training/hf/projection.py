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
  sft             every `eval_every`th step AND the last step are each a full
                  dev evaluation, plus the steps. Step 0 is one more unless
                  `reuse_step0`: then `train_verified --reuse-step0` records it
                  from base-dev (the fresh LoRA is a proven no-op), for free
  sft-dev-reload  one more dev evaluation of the selected adapter
  probe           train cases x generations -- only when `grpo_steps` > 0;
                  arm A (0) skips it (`round02_pilot.sh` step 7d)
  test            per arm, test cases x 4
  eval2           per arm, eval-2 cases x 16 (worst case: no step-0 reuse)

Every GPU stage is its own `train_verified.py` process and loads the model
once; the 55 GB download is paid once per job (the cache serves the rest).
Generation is modelled per `generate` call, as `verified_evaluation.chunked`
cuts it, because a call's wall clock is set by its longest completion and not
by how full the batch is. A chunk is scored on the pool WHILE the next one
generates (`verified_evaluation.ScoringStage`), one chunk at a time, so an
evaluation lasts its first call, then max(generation, previous chunk's
scoring) per further call, then the last chunk's scoring as a tail
(`evaluation_minutes`). `serial=True` (`--serial-scoring`) prices the old
generate-then-score loop, which is what the smoke itself projected.

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
#: probe at 4 generations would be 5,420 draws, but GRPO_STEPS is 0 so it does
#: not run; SFT's step 0 is reused from base-dev (`reuse_step0` 1); test at 4
#: draws and eval-2 at 16, for two arms (base and SFT).
PLAN = {"dev_cases": 306, "dev_draws": 16, "sft_steps": 1050, "eval_every": 350,
        "probe_cases": 1355, "probe_draws": 4, "test_cases": 315, "test_draws": 4,
        "eval2_cases": 803, "eval2_draws": 16, "arms": 2, "eval_sequences": 256,
        "score_workers": 48, "grpo_steps": 0, "reuse_step0": 1}

#: The realistic reading's assumptions. STATED CONSTANTS, each from one read:
#: the per-draw probability that a draw runs to max_new_tokens, seed 1111's
#: base dev truncation rate 0.0016 (`reports/2026-09-21-codex-step0-aggregates.json`,
#: 0.16339...%); the mean completion length of a graded target draw, 165
#: tokens (Step 2's bare arm, 2026-09-23), which is also the SFT row length;
#: and the length of the longest draw of a call in which no draw truncated,
#: 600 tokens, a guess with no measurement behind it, set well above the mean
#: because a call ends at its LONGEST draw and not its average one.
TRUNCATION_P = 0.0016
MEAN_COMPLETION_TOKENS = 165
TAIL_TOKENS = 600


def sft_evaluations(steps, every):
    """Dev evaluations one SFT stage runs: step 0, each `every`th step, and the last.

    Mirrors `verified_stages.train_sft` (`step % every == 0 or step == steps`)
    plus `train_verified.run`'s `measure(0)`.
    """
    if steps < 1 or every < 1:
        raise ValueError("steps and eval_every must be positive")
    return 1 + len(set(range(every, steps + 1, every)) | {steps})


def sft_generated_evaluations(steps, every, reuse_step0):
    """The dev evaluations the SFT stage GENERATES: all of them, less step 0 if reused."""
    return sft_evaluations(steps, every) - (1 if reuse_step0 else 0)


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


def capped_call_probability(batch, truncation_p):
    """P(at least one of `batch` independent draws runs to max_new_tokens)."""
    return 1.0 - (1.0 - truncation_p) ** int(batch)


def expected_call_tokens(batch, truncation_p=TRUNCATION_P, tail_tokens=TAIL_TOKENS,
                         max_new_tokens=1024):
    """Decode steps one `generate` call of `batch` draws lasts, conservatively.

    A call lasts until its longest draw ends. With probability
    `capped_call_probability` some draw runs to the cap; otherwise the longest
    is taken as `tail_tokens`. Per-draw truncation is treated as independent,
    which is the conservative direction for draws of one prompt that truncate
    together (a correlated call truncates less often than this says).
    """
    capped = capped_call_probability(batch, truncation_p)
    return capped * max_new_tokens + (1.0 - capped) * tail_tokens


def realistic_rate(seconds_per_token, truncation_p=TRUNCATION_P, tail_tokens=TAIL_TOKENS,
                   max_new_tokens=1024, mean_tokens=MEAN_COMPLETION_TOKENS):
    """seconds(batch) for one `generate` call: expected decode steps x seconds per step.

    `seconds_per_token` is the forced full-length 256 call's wall clock over its
    1,024 decode steps -- the slowest per-step rate measured (the longest KV
    cache, the largest batch), used for every batch size, so a smaller call is
    not priced cheaper per step than a 256 call.
    """
    if not 0 <= truncation_p < 1:
        raise ValueError("truncation probability must be in [0, 1)")
    if not 0 < mean_tokens <= tail_tokens <= max_new_tokens:
        raise ValueError("need 0 < mean tokens <= tail tokens <= max_new_tokens")
    if seconds_per_token <= 0:
        raise ValueError("seconds per token must be positive")
    return lambda batch: seconds_per_token * expected_call_tokens(
        batch, truncation_p, tail_tokens, max_new_tokens)


def sft_seconds_at(report, row_tokens):
    """SFT seconds per step at `row_tokens`-token assistant turns, from the two measured sizes.

    Linear between `sft_typical` (~256) and `sft` (~1,024), clamped to that
    range: a row shorter than the shortest measured one is priced at it, never
    extrapolated below it.
    """
    points = sorted((float(r["assistant_tokens"]), float(r["seconds_per_step"]))
                    for r in (report.get("sft"), report.get("sft_typical"))
                    if isinstance(r, dict) and r.get("seconds_per_step") and r.get("assistant_tokens"))
    if not points:
        raise ValueError("the hardware smoke has no SFT seconds per step")
    if len(points) == 1 or row_tokens <= points[0][0]:
        return points[0][1]
    (t1, s1), (t2, s2) = points[0], points[-1]
    if row_tokens >= t2:
        return s2
    return s1 + (s2 - s1) * (row_tokens - t1) / (t2 - t1)


def flat_rate(draws_per_minute):
    """seconds(batch) at a flat draws-per-minute rate that already includes scoring."""
    if draws_per_minute <= 0:
        raise ValueError("draws per minute must be positive")
    return lambda batch: batch * 60.0 / draws_per_minute


def scoring_seconds(batch, score_worker_seconds, score_workers, waves=False):
    """Pool seconds to score one chunk of `batch` draws.

    Evenly spread over the workers (batch x seconds / workers), or in WHOLE
    waves (`waves`): with equal per-draw times 256 draws on 48 workers take
    six waves, not 5.33.
    """
    workers = max(1, int(score_workers))
    share = -(-int(batch) // workers) if waves else batch / float(workers)
    return share * score_worker_seconds


def evaluation_minutes(cases, draws, sequences_per_call, batch_seconds,
                       score_worker_seconds, score_workers, *, serial=False, waves=False):
    """Wall minutes of one evaluation.

    Overlapped (the default, `verified_evaluation.ScoringStage`): chunk i is
    scored while chunk i+1 generates and the next call waits for both, so the
    evaluation is the first call's generation, then max(generation, the
    previous chunk's scoring) for every further call, then the last chunk's
    scoring. `serial`: each call's generation, then its scoring.
    """
    batches = chunk_batches(cases, draws, sequences_per_call)
    generation = [batch_seconds(batch) for batch in batches]
    scoring = [scoring_seconds(batch, score_worker_seconds, score_workers, waves) for batch in batches]
    if serial:
        return (sum(generation) + sum(scoring)) / 60.0
    total = generation[0] + scoring[-1]
    total += sum(max(g, s) for g, s in zip(generation[1:], scoring[:-1]))
    return total / 60.0


def scoring_wave_minutes(cases, draws, sequences_per_call, score_worker_seconds, score_workers,
                         batch_seconds=None):
    """Minutes one evaluation's scoring adds if each call scores in WHOLE waves of workers.

    `evaluation_minutes` spreads a call's draws evenly over the workers; the
    pool is a `ThreadPoolExecutor` the stage waits on, so with equal per-draw
    times a chunk scores in whole waves (`scoring_seconds`). This is that
    difference over the evaluation. With no `batch_seconds` scoring is taken
    as serial and every extra wave adds; with it, overlapped, a wave adds only
    where it pushes a chunk's scoring past the next call's generation, and in
    the tail. A sensitivity printed beside the projection, not added to it --
    unequal draw times land between the two.
    """
    if batch_seconds is None:
        return sum(scoring_seconds(batch, score_worker_seconds, score_workers, True)
                   - scoring_seconds(batch, score_worker_seconds, score_workers)
                   for batch in chunk_batches(cases, draws, sequences_per_call)) / 60.0
    args = (cases, draws, sequences_per_call, batch_seconds, score_worker_seconds, score_workers)
    return evaluation_minutes(*args, waves=True) - evaluation_minutes(*args)


def project(batch_seconds, sft_seconds_per_step, load_minutes, download_minutes, plan=None, *,
            score_worker_seconds=SCORE_WORKER_SECONDS_PER_DRAW, prep_minutes=PREP_MINUTES,
            eval2_prep_minutes=EVAL2_PREP_MINUTES, ceiling_minutes=JOB_CEILING_MINUTES,
            margin=DEFAULT_MARGIN, serial=False):
    """Minutes per stage for one pilot job, and whether it fits one job or two.

    `batch_seconds` is a function from sequences per call to seconds
    (`batch_seconds_from`, `flat_rate`). With `flat_rate` pass
    `score_worker_seconds=0`: that rate already includes scoring. `serial`
    prices generate-then-score (`--serial-scoring`) instead of the overlap.
    """
    plan = dict(PLAN, **(plan or {}))
    seq, workers = plan["eval_sequences"], plan["score_workers"]
    sft_evals = sft_evaluations(plan["sft_steps"], plan["eval_every"])
    generated = sft_generated_evaluations(plan["sft_steps"], plan["eval_every"], plan["reuse_step0"])
    probe_runs = plan["grpo_steps"] > 0
    arms = plan["arms"]

    def stages_at(per_draw):
        """Every stage, with scoring at `per_draw` worker-seconds a draw."""
        def evaluation(cases, draws):
            return evaluation_minutes(cases, draws, seq, batch_seconds, per_draw, workers, serial=serial)

        dev = evaluation(plan["dev_cases"], plan["dev_draws"])
        stages = [
            {"stage": "prep", "job": "pilot", "minutes": prep_minutes + download_minutes, "draws": 0},
            {"stage": "base-dev", "job": "pilot", "minutes": load_minutes + dev,
             "draws": plan["dev_cases"] * plan["dev_draws"]},
            {"stage": "sft", "job": "pilot",
             "minutes": load_minutes + generated * dev + plan["sft_steps"] * sft_seconds_per_step / 60.0,
             "draws": generated * plan["dev_cases"] * plan["dev_draws"], "evaluations": sft_evals,
             "generated_evaluations": generated, "reused_evaluations": sft_evals - generated},
            {"stage": "sft-dev-reload", "job": "pilot", "minutes": load_minutes + dev,
             "draws": plan["dev_cases"] * plan["dev_draws"]},
            {"stage": "probe", "job": "pilot",
             "minutes": (load_minutes + evaluation(plan["probe_cases"], plan["probe_draws"])
                         if probe_runs else 0.0),
             "draws": plan["probe_cases"] * plan["probe_draws"] if probe_runs else 0,
             "skipped": not probe_runs},
            {"stage": "test", "job": "pilot",
             "minutes": arms * (load_minutes + evaluation(plan["test_cases"], plan["test_draws"])),
             "draws": arms * plan["test_cases"] * plan["test_draws"]},
            {"stage": "eval2", "job": "eval2",
             "minutes": arms * (load_minutes + evaluation(plan["eval2_cases"], plan["eval2_draws"])),
             "draws": arms * plan["eval2_cases"] * plan["eval2_draws"]},
        ]
        for stage in stages:
            stage["minutes"] = round(stage["minutes"], 1)
        return stages

    def job_minutes(stages, job):
        return sum(s["minutes"] for s in stages if s["job"] == job)

    stages = stages_at(score_worker_seconds)
    unscored = stages_at(0.0)
    pilot, eval2 = job_minutes(stages, "pilot"), job_minutes(stages, "eval2")
    # The stated per-draw scoring constant, not anything the smoke measured:
    # the pool time it stands for, and how much of that reaches the wall clock
    # above. Serial, all of it; overlapped, what generation does not cover --
    # each evaluation's last chunk, and any chunk that scores slower than the
    # next one generates.
    scoring = {job: round(sum(s["draws"] for s in stages if s["job"] == job)
                          * score_worker_seconds / max(1, workers) / 60.0, 1)
               for job in ("pilot", "eval2")}
    exposed = {job: round(job_minutes(stages, job) - job_minutes(unscored, job), 1)
               for job in ("pilot", "eval2")}

    def waves(cases, draws):
        return scoring_wave_minutes(cases, draws, seq, score_worker_seconds, workers,
                                    None if serial else batch_seconds)
    dev_waves = waves(plan["dev_cases"], plan["dev_draws"])
    scoring_waves = {
        "pilot": round(dev_waves * (2 + generated)
                       + (waves(plan["probe_cases"], plan["probe_draws"]) if probe_runs else 0.0)
                       + arms * waves(plan["test_cases"], plan["test_draws"]), 1),
        "eval2": round(arms * waves(plan["eval2_cases"], plan["eval2_draws"]), 1)}
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
            "sft_generated_evaluations": generated,
            "load_minutes": round(load_minutes, 1), "download_minutes": round(download_minutes, 1),
            "sft_seconds_per_step": round(sft_seconds_per_step, 2),
            "score_worker_seconds_per_draw": score_worker_seconds,
            "scoring": "serial" if serial else "overlapped",
            "ceiling_minutes": ceiling_minutes, "margin": margin, "budget_minutes": round(budget, 1),
            "same_job_minutes": round(same_job, 1),
            "split": {"pilot_minutes": round(pilot, 1), "eval2_minutes": round(split_eval2, 1)},
            "scoring_minutes": scoring,
            "scoring_exposed_minutes": exposed,
            # Not in the minutes above: what whole scoring waves would add.
            "scoring_wave_minutes": scoring_waves,
            "fits_single_job": same_job <= budget,
            "fits_split": pilot <= budget and split_eval2 <= budget,
            "verdict": verdict,
            "assumes": "no step-0 eval-2 reuse; SFT step 0 %s; probe %s; GRPO steps not modelled; "
                       "prep %.0f min and eval-2 prep %.0f min are stated constants; scoring at "
                       "%.1f worker-s/draw, %s" % (
                           "reused from base-dev" if plan["reuse_step0"] else "generated",
                           "runs" if probe_runs else "skipped (grpo_steps 0)",
                           prep_minutes, eval2_prep_minutes, score_worker_seconds,
                           "after each call's generation" if serial
                           else "overlapped with the next call's generation")}


#: The three readings of one smoke. `measured`: the pilot-decoding calls and the
#: max-length SFT rows. `upper`: every `generate` call lasts as long as the
#: forced full-length 256 call (`generation_full_length`), because a call ends
#: at its LONGEST completion and a real 256-draw chunk truncates at
#: max_new_tokens often (seed 1111's base dev truncated 0.16% of draws,
#: `reports/2026-09-21-codex-step0-aggregates.json`: about one call in three
#: of 256), while the public starter tasks the smoke prompts with are
#: short and may never reach it. `lower`: the pilot-decoding calls and
#: typical-length SFT rows (`sft_typical`). `realistic`: every call lasts its
#: EXPECTED longest draw (`expected_call_tokens`: capped with probability
#: 1-(1-p)^batch, else `TAIL_TOKENS`) at the full-length call's seconds per
#: decode step, and SFT at `MEAN_COMPLETION_TOKENS`-token rows
#: (`sft_seconds_at`); its constants are stated assumptions, printed with it.
READINGS = {"measured": ("generation", "sft"), "upper": ("generation_full_length", "sft"),
            "lower": ("generation", "sft_typical"),
            "realistic": ("generation_full_length", None)}


def from_hwsmoke(report, plan=None, draws_per_minute=None, reading="measured", *,
                 truncation_p=TRUNCATION_P, mean_tokens=MEAN_COMPLETION_TOKENS,
                 tail_tokens=TAIL_TOKENS, **kw):
    """`project` over a `hwsmoke.json`: its generation calls, SFT step and load.

    `reading` picks which measurements (`READINGS`). `draws_per_minute`, when
    given, replaces the generation measurement with a flat rate that already
    includes scoring (e.g. an earlier job's observed rate), so the two
    readings can be printed side by side. The realistic reading's constants
    are keyword arguments and are returned under `realistic_assumptions`.
    """
    if reading not in READINGS:
        raise ValueError("reading must be one of %s" % ", ".join(sorted(READINGS)))
    generation_key, sft_key = READINGS[reading]
    load = (report.get("load_seconds") or 0) / 60.0
    download = (report.get("download_seconds") or 0) / 60.0
    if reading == "realistic":
        full = report.get(generation_key)
        if not isinstance(full, dict) or full.get("error") or not full.get("seconds"):
            raise ValueError("the realistic reading needs the full-length generation call")
        cap = int(full.get("max_new_tokens") or 1024)
        per_token = float(full["seconds"]) / cap
        rate = realistic_rate(per_token, truncation_p, tail_tokens, cap, mean_tokens)
        got = project(rate, sft_seconds_at(report, mean_tokens), load, download, plan, **kw)
        got["realistic_assumptions"] = {
            "truncation_p": truncation_p, "mean_completion_tokens": mean_tokens,
            "tail_tokens": tail_tokens, "max_new_tokens": cap,
            "seconds_per_decode_step": round(per_token, 4),
            "capped_call_probability_256": round(capped_call_probability(256, truncation_p), 4),
            "expected_call_tokens_256": round(expected_call_tokens(256, truncation_p, tail_tokens, cap), 1),
            "basis": "stated constants, not measurements: p from seed 1111's base dev, the mean "
                     "from Step 2's bare arm (2026-09-23), the tail length a guess"}
        return got
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


def readings(report, plan=None, **kw):
    """Every reading of one smoke side by side: pilot, eval-2 and same-job minutes, verdict."""
    table = {}
    for reading in READINGS:
        try:
            got = from_hwsmoke(report, plan, reading=reading, **kw)
        except ValueError as exc:
            table[reading] = {"error": str(exc)}
            continue
        table[reading] = {"pilot_minutes": got["split"]["pilot_minutes"],
                          "eval2_minutes": got["split"]["eval2_minutes"],
                          "same_job_minutes": got["same_job_minutes"],
                          "budget_minutes": got["budget_minutes"], "verdict": got["verdict"],
                          "scoring": got["scoring"], "scoring_minutes": got["scoring_minutes"],
                          "scoring_exposed_minutes": got["scoring_exposed_minutes"],
                          "scoring_wave_minutes": got["scoring_wave_minutes"]}
        if "realistic_assumptions" in got:
            table[reading]["assumptions"] = got["realistic_assumptions"]
    return table


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--hwsmoke", required=True, help="hwsmoke.json written by training/hf/hwsmoke.py")
    p.add_argument("--draws-per-minute", type=float,
                   help="replace the generation measurement with a flat rate that includes scoring")
    for key, value in PLAN.items():
        p.add_argument("--" + key.replace("_", "-"), type=int, default=value)
    p.add_argument("--margin", type=float, default=DEFAULT_MARGIN)
    p.add_argument("--reading", choices=sorted(READINGS) + ["all"], default="measured",
                   help="measured (pilot decoding, max-length SFT rows), upper (every call "
                        "full-length), lower (typical-length SFT rows), realistic (expected "
                        "longest draw per call; stated constants) or all, side by side")
    p.add_argument("--truncation-p", type=float, default=TRUNCATION_P)
    p.add_argument("--mean-tokens", type=int, default=MEAN_COMPLETION_TOKENS)
    p.add_argument("--tail-tokens", type=int, default=TAIL_TOKENS)
    p.add_argument("--serial-scoring", action="store_true",
                   help="price generate-then-score, as `train_verified --serial-scoring` runs "
                        "it and as the smoke itself projected; default: overlapped")
    args = p.parse_args(argv)
    with open(args.hwsmoke, encoding="utf-8") as fh:
        report = json.load(fh)
    plan = {key: getattr(args, key) for key in PLAN}
    realistic = dict(truncation_p=args.truncation_p, mean_tokens=args.mean_tokens,
                     tail_tokens=args.tail_tokens, serial=args.serial_scoring)
    if args.reading == "all":
        print(json.dumps(readings(report, plan, margin=args.margin, **realistic), indent=2))
        return 0
    try:
        got = from_hwsmoke(report, plan, args.draws_per_minute, args.reading, margin=args.margin,
                           **realistic)
    except ValueError as exc:
        print("projection: %s" % exc, file=sys.stderr)
        return 1
    print(json.dumps(got, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
