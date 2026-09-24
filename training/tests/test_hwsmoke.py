"""The hardware smoke measures the pilot's model, and the projection is right arithmetic.

`round02_hwsmoke.sh` is billed on an h200 and runs nowhere else, so what can
be decided without one is decided here: that `hwsmoke.py` loads through
`train_verified`'s own loaders and not a copy of them, that it generates and
steps through the pilot's real `evaluate` and `train_sft`, that nothing it
records or prints is case text, and that `projection.py` counts the stages
exactly as the job script runs them.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import math
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
HF = ROOT / "training" / "hf"


def load(name):
    spec = importlib.util.spec_from_file_location("hw_" + name, HF / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


projection = load("projection")
launch = load("launch")
hwsmoke = load("hwsmoke")          # also puts training/gpu on sys.path, as in the job


# --- projection ---------------------------------------------------------------

@pytest.mark.parametrize("steps, every, want", [(1050, 350, 4), (300, 50, 7), (75, 25, 4),
                                                (1050, 400, 4), (10, 50, 2), (1, 1, 2)])
def test_sft_evaluations_are_step_zero_the_cadence_and_the_last_step(steps, every, want):
    assert projection.sft_evaluations(steps, every) == want
    # The same rule as `train_sft`, counted the slow way.
    assert 1 + sum(1 for s in range(1, steps + 1) if s % every == 0 or s == steps) == want


def test_sft_evaluations_refuse_a_zero_schedule():
    with pytest.raises(ValueError):
        projection.sft_evaluations(0, 350)
    with pytest.raises(ValueError):
        projection.sft_evaluations(1050, 0)


@pytest.mark.parametrize("cases, draws, seq", [(306, 16, 256), (803, 16, 256), (315, 4, 256),
                                               (1355, 4, 256), (16, 16, 256), (8, 16, 128), (5, 300, 256)])
def test_chunks_are_cut_as_evaluate_cuts_them(cases, draws, seq):
    from verified_evaluation import chunked
    want = [len(chunk) * draws for chunk in chunked(list(range(cases)), draws, seq)]
    assert projection.chunk_batches(cases, draws, seq) == want
    assert sum(want) == cases * draws


def test_the_batch_model_fits_two_sizes_and_floors_below_them():
    seconds = projection.batch_seconds_from({256: 300.0, 128: 200.0})
    assert seconds(256) == pytest.approx(300.0) and seconds(128) == pytest.approx(200.0)
    assert seconds(192) == pytest.approx(250.0)
    assert seconds(32) == pytest.approx(125.0), "decode-bound: a small call is not proportionally cheap"
    superlinear = projection.batch_seconds_from({256: 500.0, 128: 100.0})
    assert superlinear(32) == pytest.approx(25.0), "floored at proportional, never zero"
    one = projection.batch_seconds_from({128: 200.0, 256: None})
    assert one(32) == 200.0 and one(256) == pytest.approx(400.0)
    for bad in ({}, {256: None}, {256: 0.0}):
        with pytest.raises(ValueError):
            projection.batch_seconds_from(bad)


def test_a_flat_rate_projects_draws_over_rate_exactly():
    at40 = projection.flat_rate(40)
    assert projection.evaluation_minutes(306, 16, 256, at40, 0.0, 48) == pytest.approx(4896 / 40)
    with_scoring = projection.evaluation_minutes(16, 16, 256, at40, 20.8, 48)
    assert with_scoring == pytest.approx((256 * 60 / 40 + 256 * 20.8 / 48) / 60)
    with pytest.raises(ValueError):
        projection.flat_rate(0)


def hand_total(dpm, step_s, load_min, download_min, probe=False, sft_evaluations=3):
    """The approved arm-A job, counted by hand at a flat rate.

    Arm A: no probe, and SFT generates three dev evaluations (350, 700, 1,050);
    step 0 is base-dev's. `probe=True, sft_evaluations=4` is the job before
    2026-09-24, which the hardware smoke projected.
    """
    dev = 306 * 16 / dpm
    pilot = (projection.PREP_MINUTES + download_min + (load_min + dev)
             + (load_min + sft_evaluations * dev + 1050 * step_s / 60)
             + (load_min + dev) + ((load_min + 1355 * 4 / dpm) if probe else 0)
             + 2 * (load_min + 315 * 4 / dpm))
    eval2 = 2 * (load_min + 803 * 16 / dpm)
    return pilot, eval2


def test_the_upper_reading_prices_every_call_at_the_full_length_call():
    report = smoke_report(generation_full_length={"sequences": 256, "seconds": 600.0, "full_length": True},
                          sft_typical={"seconds_per_step": 5.0})
    upper = projection.from_hwsmoke(report, reading="upper", score_worker_seconds=0.0)
    measured = projection.from_hwsmoke(report, reading="measured", score_worker_seconds=0.0)
    lower = projection.from_hwsmoke(report, reading="lower", score_worker_seconds=0.0)
    dev = {r: next(s for s in got["stages"] if s["stage"] == "base-dev")["minutes"]
           for r, got in (("upper", upper), ("measured", measured))}
    # 306 cases x 16 draws at 256 per call is 20 calls (19 full, one of 32);
    # the upper reading prices each at the full-length 600 s, the short one too.
    assert dev["upper"] == pytest.approx(3.0 + 20 * 600 / 60, abs=0.1)
    assert dev["upper"] > dev["measured"]
    assert upper["sft_seconds_per_step"] == 20.0 and lower["sft_seconds_per_step"] == 5.0
    assert lower["same_job_minutes"] < measured["same_job_minutes"] < upper["same_job_minutes"]
    with pytest.raises(ValueError):
        projection.from_hwsmoke(smoke_report(), reading="upper")      # no full-length call
    with pytest.raises(ValueError):
        projection.from_hwsmoke(report, reading="sideways")


def test_the_projection_cli_reads_each_reading_and_refuses_a_missing_one(tmp_path, capsys):
    path = tmp_path / "hwsmoke.json"
    path.write_text(json.dumps(smoke_report()))
    assert projection.main(["--hwsmoke", str(path), "--reading", "upper"]) == 1
    assert "generation" not in capsys.readouterr().out


def test_the_projection_counts_every_stage_the_job_runs():
    got = projection.project(projection.flat_rate(40), 20.0, 3.0, 5.0, score_worker_seconds=0.0)
    pilot, eval2 = hand_total(40, 20.0, 3.0, 5.0)
    assert [s["stage"] for s in got["stages"]] == ["prep", "base-dev", "sft", "sft-dev-reload",
                                                   "probe", "test", "eval2"]
    assert got["sft_evaluations"] == 4 and got["sft_generated_evaluations"] == 3
    assert got["split"]["pilot_minutes"] == pytest.approx(pilot, abs=0.5)
    assert got["same_job_minutes"] == pytest.approx(pilot + eval2, abs=0.5)
    assert got["split"]["eval2_minutes"] == pytest.approx(projection.EVAL2_PREP_MINUTES + 5.0 + eval2, abs=0.5)
    stages = {s["stage"]: s for s in got["stages"]}
    draws = {name: stage["draws"] for name, stage in stages.items()}
    # Arm A: step 0 is base-dev's draws, and the probe does not run.
    assert (draws["base-dev"], draws["sft"], draws["probe"], draws["test"], draws["eval2"]) == \
        (4896, 3 * 4896, 0, 2 * 315 * 4, 2 * 803 * 16)
    assert stages["probe"]["skipped"] and stages["probe"]["minutes"] == 0
    assert (stages["sft"]["evaluations"], stages["sft"]["generated_evaluations"],
            stages["sft"]["reused_evaluations"]) == (4, 3, 1)


def test_the_probe_and_a_fresh_step_zero_are_counted_when_the_plan_asks_for_them():
    """GRPO_STEPS > 0 runs the probe; without reuse SFT generates step 0 too."""
    got = projection.project(projection.flat_rate(40), 20.0, 3.0, 5.0,
                             {"grpo_steps": 300, "reuse_step0": 0}, score_worker_seconds=0.0)
    pilot, _ = hand_total(40, 20.0, 3.0, 5.0, probe=True, sft_evaluations=4)
    assert got["split"]["pilot_minutes"] == pytest.approx(pilot, abs=0.5)
    draws = {s["stage"]: s["draws"] for s in got["stages"]}
    assert (draws["sft"], draws["probe"]) == (4 * 4896, 5420)
    assert "GRPO steps not modelled" in got["assumes"]


def test_the_verdict_is_same_job_split_or_neither():
    fast = projection.project(projection.flat_rate(400), 5.0, 2.0, 3.0, score_worker_seconds=0.0)
    assert fast["verdict"] == "same-job" and fast["fits_single_job"]
    # At seed 1111's order of magnitude the whole arm is days, not a split.
    slow = projection.project(projection.flat_rate(40), 20.0, 3.0, 5.0, score_worker_seconds=0.0)
    assert slow["verdict"] == "does-not-fit" and not slow["fits_split"]
    # A rate where the pilot fits and eval-2 fits alone but not together.
    middle = None
    for dpm in range(100, 400, 5):
        got = projection.project(projection.flat_rate(dpm), 5.0, 2.0, 3.0, score_worker_seconds=0.0)
        if got["verdict"] == "separate":
            middle = got
            break
    assert middle and not middle["fits_single_job"] and middle["fits_split"]
    assert middle["budget_minutes"] == pytest.approx(720 * 0.9)


def test_the_projections_plan_is_the_workflows_arm():
    text = (ROOT / ".github" / "workflows" / "round02.yml").read_text(encoding="utf-8")

    def env(name):
        return int(re.search(r'^  %s: "(\d+)"$' % name, text, re.M).group(1))
    plan = projection.PLAN
    assert plan["sft_steps"] == env("PILOT_STEPS") == math.ceil(4197 / 4)
    assert plan["eval_every"] == env("PILOT_EVAL_EVERY")
    assert plan["dev_draws"] == env("PILOT_DEV_EVAL_DRAWS")
    assert plan["score_workers"] == env("PILOT_SCORERS")
    assert plan["eval_sequences"] == launch.DEFAULT_EVAL_SEQUENCES
    assert plan["probe_cases"] * plan["probe_draws"] == 5420
    assert plan["eval2_draws"] == 16 and plan["test_draws"] == 4 and plan["arms"] == 2
    assert plan["grpo_steps"] == env("PILOT_GRPO_STEPS") == 0, "arm A: no probe"
    assert plan["reuse_step0"] == 1 and '--reuse-step0 "$ROUND/base-dev"' in \
        (HF / "round02_pilot.sh").read_text(encoding="utf-8")
    assert projection.JOB_CEILING_MINUTES * 60 == launch.timeout_seconds(launch.BANKED_TIMEOUT)


def smoke_report(**over):
    report = {"load_seconds": 180.0, "download_seconds": 300.0,
              "generation": [{"sequences": 256, "seconds": 300.0}, {"sequences": 128, "seconds": 200.0}],
              "sft": {"seconds_per_step": 20.0}}
    report.update(over)
    return report


def test_a_projection_reads_a_hwsmoke_report_and_skips_what_did_not_fit(tmp_path, capsys):
    got = projection.from_hwsmoke(smoke_report())
    assert got["load_minutes"] == 3.0 and got["download_minutes"] == 5.0
    oom = projection.from_hwsmoke(smoke_report(generation=[
        {"sequences": 256, "error": "out-of-memory"}, {"sequences": 128, "seconds": 200.0}]))
    assert oom["same_job_minutes"] > 0
    with pytest.raises(ValueError):
        projection.from_hwsmoke(smoke_report(generation=[{"sequences": 256, "error": "out-of-memory"}]))
    with pytest.raises(ValueError):
        projection.from_hwsmoke(smoke_report(sft={"error": "out-of-memory"}))
    flat = projection.from_hwsmoke(smoke_report(), draws_per_minute=40)
    assert flat["score_worker_seconds_per_draw"] == 0.0
    path = tmp_path / "hwsmoke.json"
    path.write_text(json.dumps(smoke_report()))
    assert projection.main(["--hwsmoke", str(path), "--eval-every", "525"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["sft_evaluations"] == 3 and printed["plan"]["eval_every"] == 525


# --- the h200 smoke's own numbers ------------------------------------------------

#: What the h200 hardware smoke measured: HF job 6ab4582d6b030d633f68c90e,
#: Actions run 35930577878, 2026-09-24, commit 83b62d1. Aggregates only, copied
#: from the `== hwsmoke:` line of that public log; the projections below are
#: re-derived from these numbers, never quoted.
SMOKE_6AB4582D = {
    "job": "6ab4582d6b030d633f68c90e", "load_seconds": 8.6, "download_seconds": 34.8,
    "generation": [
        {"sequences": 256, "max_new_tokens": 1024, "full_length": False, "seconds": 44.82,
         "max_completion_tokens": 147, "mean_completion_tokens": 63.3, "truncated": 0,
         "peak_memory_bytes": 104861628416},
        {"sequences": 128, "max_new_tokens": 1024, "full_length": False, "seconds": 16.12,
         "max_completion_tokens": 90, "mean_completion_tokens": 59.3, "truncated": 0,
         "peak_memory_bytes": 79976356864}],
    "generation_full_length": {"sequences": 256, "max_new_tokens": 1024, "full_length": True,
                               "seconds": 359.44, "max_completion_tokens": 1024, "truncated": 256,
                               "peak_memory_bytes": 120700543488},
    "sft": {"batch_size": 4, "assistant_tokens": 1024, "seconds_per_step": 7.289,
            "peak_memory_bytes": 61525806080},
    "sft_typical": {"batch_size": 4, "assistant_tokens": 256, "seconds_per_step": 4.218,
                    "peak_memory_bytes": 58140779520},
}
#: The job as the smoke projected it: the probe ran and SFT generated step 0.
BEFORE = {"grpo_steps": 300, "reuse_step0": 0}


def test_the_fixture_reproduces_what_the_smoke_itself_printed():
    """The smoke's own `hwsmoke.json` projection, from these numbers and the old plan.

    Printed by the job: same-job 788.1, split 527.3 + 271.4 (measured); upper
    2126.0 (1329.1 + 807.5); lower 734.3 (473.5 + 271.4). If the fixture or the
    arithmetic drifted, the new numbers below would mean nothing. The smoke
    projected generate-then-score, so this is read serially.
    """
    want = {"measured": (788.1, 527.3, 271.4), "upper": (2126.0, 1329.1, 807.5),
            "lower": (734.3, 473.5, 271.4)}
    for reading, (same, pilot, eval2) in want.items():
        got = projection.from_hwsmoke(SMOKE_6AB4582D, BEFORE, reading=reading, serial=True)
        assert (got["same_job_minutes"], got["split"]["pilot_minutes"],
                got["split"]["eval2_minutes"]) == pytest.approx((same, pilot, eval2), abs=0.15), reading


def test_arm_a_without_the_probe_and_with_step_zero_reused_from_the_smokes_numbers():
    """Arm A scored serially, as before the overlap: pilot job and eval-2 job minutes."""
    table = projection.readings(SMOKE_6AB4582D, serial=True)
    got = {r: (row["pilot_minutes"], row["eval2_minutes"]) for r, row in table.items()}
    assert got == {"measured": pytest.approx((422.6, 271.4), abs=0.15),
                   "realistic": pytest.approx((764.4, 638.3), abs=0.15),
                   "upper": pytest.approx((1002.8, 807.5), abs=0.15),
                   "lower": pytest.approx((368.8, 271.4), abs=0.15)}
    assert table["measured"]["verdict"] == "separate"
    assert table["upper"]["verdict"] == table["realistic"]["verdict"] == "does-not-fit"
    # The realistic pilot is over the 648-minute budget, and the stated scoring
    # constant is 195 of its minutes: 27,000 draws x 20.8 worker-s / 48 workers.
    assert table["realistic"]["pilot_minutes"] > table["realistic"]["budget_minutes"] == 648.0
    assert table["realistic"]["scoring_minutes"]["pilot"] == pytest.approx(27000 * 20.8 / 48 / 60, abs=0.1)
    assert table["realistic"]["eval2_minutes"] <= 648.0
    # Removing the probe and step 0 takes 10,316 draws off the pilot job at every reading.
    for reading in ("measured", "realistic", "upper", "lower"):
        before = projection.from_hwsmoke(SMOKE_6AB4582D, BEFORE, reading=reading, serial=True)
        assert before["split"]["pilot_minutes"] > table[reading]["pilot_minutes"], reading
        drawn = sum(s["draws"] for s in before["stages"] if s["job"] == "pilot")
        now = sum(s["draws"] for s in projection.from_hwsmoke(SMOKE_6AB4582D, reading=reading)["stages"]
                  if s["job"] == "pilot")
        assert drawn - now == 5420 + 4896


def test_arm_a_with_scoring_overlapped_from_the_smokes_numbers():
    """The numbers `PLAN.md` Step 4 quotes, re-derived: pilot job and eval-2 job minutes.

    Scoring overlapped with the next call's generation hides whichever of the
    two is shorter. At the realistic and upper readings generation is longer
    (a 256 call is ~261 s and ~359 s, its scoring 110.9 s), so almost all of
    the 195 scoring minutes leave the pilot's clock; at the measured and lower
    readings scoring is the longer, and the calls' generation leaves instead.
    """
    table = projection.readings(SMOKE_6AB4582D)
    got = {r: (row["pilot_minutes"], row["eval2_minutes"]) for r, row in table.items()}
    assert got == {"measured": pytest.approx((349.2, 198.0), abs=0.15),
                   "realistic": pytest.approx((574.0, 453.4), abs=0.15),
                   "upper": pytest.approx((812.4, 622.6), abs=0.15),
                   "lower": pytest.approx((295.5, 198.0), abs=0.15)}
    assert {row["scoring"] for row in table.values()} == {"overlapped"}
    assert table["measured"]["verdict"] == table["lower"]["verdict"] == "same-job"
    assert table["realistic"]["verdict"] == "separate"
    assert table["upper"]["verdict"] == "does-not-fit"
    realistic = table["realistic"]
    assert realistic["pilot_minutes"] <= realistic["budget_minutes"] == 648.0
    # The stated constant still stands for 195.0 pool minutes; 4.7 reach the clock.
    assert realistic["scoring_minutes"]["pilot"] == pytest.approx(195.0, abs=0.1)
    assert realistic["scoring_exposed_minutes"] == {"pilot": pytest.approx(4.7, abs=0.15),
                                                    "eval2": pytest.approx(0.7, abs=0.15)}
    # Generation covers scoring until the pool is ~2.7x slower than stated.
    def pilot_at(per_draw):
        return projection.from_hwsmoke(SMOKE_6AB4582D, reading="realistic",
                                       score_worker_seconds=per_draw)["split"]["pilot_minutes"]
    assert pilot_at(55.0) <= 648.0 < pilot_at(56.0)
    serial = projection.readings(SMOKE_6AB4582D, serial=True)
    for reading, row in table.items():
        assert row["pilot_minutes"] < serial[reading]["pilot_minutes"], reading
        assert row["eval2_minutes"] < serial[reading]["eval2_minutes"], reading
        assert serial[reading]["scoring_exposed_minutes"]["pilot"] == pytest.approx(195.0, abs=0.2)


def test_an_overlapped_evaluation_is_the_first_call_the_longer_of_each_pair_and_the_last_scoring():
    """Chunk i scores while chunk i+1 generates; the last chunk's scoring is a tail."""
    def seconds(batch):
        return {256: 100.0, 32: 30.0}[batch]
    # 34 cases x 16 draws at 256 per call: calls of 256, 256 and 32 draws.
    assert projection.chunk_batches(34, 16, 256) == [256, 256, 32]
    score = [256 * 20.8 / 48, 256 * 20.8 / 48, 32 * 20.8 / 48]           # 110.9, 110.9, 13.9 s
    by_hand = 100.0 + max(100.0, score[0]) + max(30.0, score[1]) + score[2]
    assert projection.evaluation_minutes(34, 16, 256, seconds, 20.8, 48) == pytest.approx(by_hand / 60)
    serial = projection.evaluation_minutes(34, 16, 256, seconds, 20.8, 48, serial=True)
    assert serial == pytest.approx((230.0 + sum(score)) / 60)
    # Never slower than serial, never faster than either resource alone.
    for cases, draws in ((306, 16), (803, 16), (315, 4), (1, 1), (34, 16)):
        for per_draw in (0.0, 1.0, 20.8, 200.0):
            args = (cases, draws, 256, projection.realistic_rate(0.351), per_draw, 48)
            over, ser = projection.evaluation_minutes(*args), projection.evaluation_minutes(*args, serial=True)
            batches = projection.chunk_batches(cases, draws, 256)
            gen = sum(args[3](b) for b in batches) / 60
            scoring = sum(projection.scoring_seconds(b, per_draw, 48) for b in batches) / 60
            assert max(gen, scoring) - 1e-9 <= over <= ser + 1e-9
            assert ser == pytest.approx(gen + scoring)
    # One call: nothing to overlap with.
    one = projection.evaluation_minutes(16, 16, 256, seconds, 20.8, 48)
    assert one == projection.evaluation_minutes(16, 16, 256, seconds, 20.8, 48, serial=True)


def test_whole_scoring_waves_are_printed_beside_the_projection_not_added_to_it():
    """256 draws on 48 workers is six waves if draws take equal time, not 5.33.

    The linear pool model is the optimistic end; the wave count is the other.
    Serially every wave adds; overlapped, a wave adds only where it outlasts
    the next call's generation, and in each evaluation's tail.
    """
    assert projection.scoring_wave_minutes(16, 16, 256, 20.8, 48) == pytest.approx((6 - 256 / 48) * 20.8 / 60)
    assert projection.scoring_wave_minutes(3, 16, 256, 20.8, 48) == 0.0, "48 draws: one whole wave"
    serial = projection.readings(SMOKE_6AB4582D, serial=True)["realistic"]
    assert serial["scoring_wave_minutes"] == {"pilot": pytest.approx(24.4, abs=0.15),
                                              "eval2": pytest.approx(23.1, abs=0.15)}
    assert serial["eval2_minutes"] <= serial["budget_minutes"] \
        < serial["eval2_minutes"] + serial["scoring_wave_minutes"]["eval2"]
    table = projection.readings(SMOKE_6AB4582D)
    assert table["realistic"]["scoring_wave_minutes"] == {"pilot": pytest.approx(0.6, abs=0.15),
                                                          "eval2": pytest.approx(0.0, abs=0.15)}
    # Scoring-bound (measured): every wave still lands on the clock.
    assert table["measured"]["scoring_wave_minutes"] == {"pilot": pytest.approx(24.4, abs=0.15),
                                                         "eval2": pytest.approx(23.1, abs=0.15)}
    for row in table.values():
        assert row["pilot_minutes"] + row["scoring_wave_minutes"]["pilot"] <= 648.0 or \
            row["verdict"] == "does-not-fit"
    got = projection.from_hwsmoke(SMOKE_6AB4582D, reading="realistic")
    assert got["split"]["pilot_minutes"] == pytest.approx(574.0, abs=0.15), "a sensitivity, not a term"


def test_the_realistic_call_lasts_its_expected_longest_draw():
    # 1-(1-0.0016)^256: about one 256-draw call in three reaches the cap.
    assert projection.capped_call_probability(256, 0.0016) == pytest.approx(0.3363, abs=1e-4)
    assert projection.capped_call_probability(1, 0.0016) == pytest.approx(0.0016)
    assert projection.expected_call_tokens(256) == pytest.approx(0.3363 * 1024 + 0.6637 * 600, abs=0.1)
    assert projection.expected_call_tokens(32) < projection.expected_call_tokens(256) < 1024
    assert projection.expected_call_tokens(256, truncation_p=0.0) == 600
    rate = projection.realistic_rate(359.44 / 1024)
    assert rate(256) == pytest.approx(359.44 / 1024 * projection.expected_call_tokens(256))
    assert rate(256) < 359.44, "below the forced full-length call, which is the upper reading"
    for bad in (dict(truncation_p=1.0), dict(tail_tokens=100), dict(tail_tokens=2000),
                dict(mean_tokens=0)):
        with pytest.raises(ValueError):
            projection.realistic_rate(0.35, **bad)
    with pytest.raises(ValueError):
        projection.realistic_rate(0.0)


def test_the_realistic_sft_step_is_read_at_the_mean_row_length_and_never_extrapolated():
    assert projection.sft_seconds_at(SMOKE_6AB4582D, 165) == 4.218, "below 256: the 256 measurement"
    assert projection.sft_seconds_at(SMOKE_6AB4582D, 2048) == 7.289
    assert projection.sft_seconds_at(SMOKE_6AB4582D, 640) == pytest.approx((4.218 + 7.289) / 2)
    with pytest.raises(ValueError):
        projection.sft_seconds_at({}, 165)
    got = projection.from_hwsmoke(SMOKE_6AB4582D, reading="realistic")
    assert got["sft_seconds_per_step"] == 4.22
    stated = got["realistic_assumptions"]
    assert (stated["truncation_p"], stated["mean_completion_tokens"], stated["tail_tokens"]) == (
        projection.TRUNCATION_P, projection.MEAN_COMPLETION_TOKENS, projection.TAIL_TOKENS) == (0.0016, 165, 600)
    assert stated["seconds_per_decode_step"] == pytest.approx(0.351, abs=1e-3)
    with pytest.raises(ValueError):
        projection.from_hwsmoke(smoke_report(), reading="realistic")      # no full-length call


def test_the_cli_prints_every_reading_side_by_side(tmp_path, capsys):
    path = tmp_path / "hwsmoke.json"
    path.write_text(json.dumps(SMOKE_6AB4582D))
    assert projection.main(["--hwsmoke", str(path), "--reading", "all"]) == 0
    table = json.loads(capsys.readouterr().out)
    assert set(table) == {"measured", "upper", "lower", "realistic"}
    assert "assumptions" in table["realistic"] and "assumptions" not in table["measured"]
    assert table["realistic"]["scoring"] == "overlapped"
    assert projection.main(["--hwsmoke", str(path), "--reading", "all", "--serial-scoring"]) == 0
    serial = json.loads(capsys.readouterr().out)
    assert serial["realistic"]["scoring"] == "serial"
    assert serial["realistic"]["pilot_minutes"] == pytest.approx(764.4, abs=0.15)
    assert projection.main(["--hwsmoke", str(path), "--reading", "realistic", "--tail-tokens", "1024"]) == 0
    worst = json.loads(capsys.readouterr().out)
    assert worst["realistic_assumptions"]["expected_call_tokens_256"] == 1024.0


# --- the hardware smoke ---------------------------------------------------------

HWSMOKE = (HF / "hwsmoke.py").read_text(encoding="utf-8")
LOADERS = ("block_fused_kernels", "refuse_import_binding", "load_tokenizer", "download_base",
           "load_base_model", "attach_fresh_lora", "refuse_bound_kernels")


def calls(source, owner):
    return [n.func.attr for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name) and n.func.value.id == owner]


def test_the_smoke_loads_through_the_trainers_own_loaders_in_its_order():
    used = [c for c in calls(HWSMOKE, "tv") if c in LOADERS]
    assert used == list(LOADERS), used
    trainer = (ROOT / "training" / "gpu" / "train_verified.py").read_text(encoding="utf-8")
    run = next(n for n in ast.walk(ast.parse(trainer)) if isinstance(n, ast.FunctionDef) and n.name == "run")
    in_run = [n.func.id for n in ast.walk(run) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    for loader in LOADERS:
        assert loader in in_run, "run() no longer shares %s with the smoke" % loader
    # No second copy of the loading path.
    for forked in (r"from_pretrained", r"snapshot_download", r"get_peft_model", r"core\.attach_lora\(",
                   r"kernel_block\.install", r"\bbound_kernels\("):
        assert not re.search(forked, HWSMOKE), forked


def test_the_smoke_measures_through_the_pilots_own_generation_and_sft_paths():
    assert "from verified_evaluation import evaluate" in HWSMOKE
    assert "from verified_stages import supervised_tokens, train_sft" in HWSMOKE
    assert "decoding(max_new_tokens)" in HWSMOKE and "decoding(args.max_new_tokens)" in HWSMOKE
    hw = load("hwsmoke")
    assert hw.GENERATION_BATCHES == (256, 128) and hw.GENERATION_DRAWS == 16
    assert (hw.SFT_TIMED_STEPS, hw.SFT_BATCH) == (20, 4)


def test_the_prompts_are_the_public_starter_tasks():
    from pipeline.curriculum import starter_cases
    hw = load("hwsmoke")
    tasks = {c["task"] for c in starter_cases()}
    for count in (16, 8, 4, 40):
        got = hw.prompts(count)
        assert len(got) == count and len({c["case_id"] for c in got}) == count
        assert {c["task"] for c in got} <= tasks
    assert len(hw.prompts(256 // hw.GENERATION_DRAWS)) == 16


class WordTok:
    def __call__(self, text, add_special_tokens=False):
        return {"input_ids": text.split()}


def test_the_synthetic_sft_row_reaches_its_length():
    hw = load("hwsmoke")
    program = hw.synthetic_program(WordTok(), 1024)
    from pipeline.training import assistant_turn, program_from_completion
    assert len(assistant_turn(program).split()) >= 1024
    assert program_from_completion(assistant_turn(program)) == program.rstrip()
    compile(program, "<synthetic>", "exec")


class FakeCuda:
    class OutOfMemoryError(Exception):
        pass

    def empty_cache(self):
        pass

    def reset_peak_memory_stats(self):
        pass

    def synchronize(self):
        pass

    def max_memory_allocated(self):
        return 123


def fake_torch():
    return SimpleNamespace(cuda=FakeCuda())


def test_a_generation_row_is_aggregates_only(monkeypatch, tmp_path):
    import verified_evaluation
    hw = load("hwsmoke")
    seen = {}

    def evaluate(model, tok, cases, verifier, policy, output, step, torch, **kw):
        seen.update(kw, cases=len(cases), policy=policy)
        return {}, [{"case_id": c["case_id"], "completion": "SECRET COMPLETION " + c["task"],
                     "completion_tokens": 100 + i, "truncated": i == 0} for i, c in enumerate(cases * 16)]
    monkeypatch.setattr(verified_evaluation, "evaluate", evaluate)
    row = hw.measure_generation(None, None, fake_torch(), 256, 1024, 1111, tmp_path)
    assert seen["cases"] == 16 and seen["draws"] == 16 and seen["sequences_per_call"] == 256
    assert seen["policy"]["max_new_tokens"] == 1024 and seen["seed"] == 1111
    assert row["truncated"] == 1 and row["peak_memory_bytes"] == 123
    text = json.dumps(row)
    assert "SECRET" not in text and "case_id" not in text and "completion\"" not in text
    assert set(row) == {"sequences", "prompts", "draws", "max_new_tokens", "full_length", "seconds",
                        "generated_tokens", "max_completion_tokens", "mean_completion_tokens", "truncated",
                        "sequences_per_minute", "generated_tokens_per_second", "peak_memory_bytes"}
    assert row["full_length"] is False and "min_new_tokens" not in seen["policy"]


def test_an_out_of_memory_call_is_a_recorded_result_not_a_crash(monkeypatch, tmp_path):
    import verified_evaluation
    hw = load("hwsmoke")
    torch = fake_torch()

    def evaluate(*a, **kw):
        raise FakeCuda.OutOfMemoryError()
    monkeypatch.setattr(verified_evaluation, "evaluate", evaluate)
    row = hw.measure_generation(None, None, torch, 256, 1024, 1111, tmp_path)
    assert row["error"] == "out-of-memory" and "seconds" not in row


def test_the_sft_measurement_steps_train_sft_at_batch_4(monkeypatch, tmp_path):
    import verified_stages
    hw = load("hwsmoke")
    runs = []

    def train_sft(model, tok, args, train_cases, examples, core, torch, effective, checkpoint, batches=None):
        assert args.batch_size == 4 and len(batches) == effective["steps"]
        assert all(len(b) == 4 for b in batches)
        runs.append(effective["steps"])
    monkeypatch.setattr(verified_stages, "train_sft", train_sft)
    core = SimpleNamespace(build_examples=lambda tok, rows, max_seq: (
        [{"case_id": r["case_id"], "input_ids": [1] * 1200, "labels": [-100] * 200 + [1] * 1000}
         for r in rows], 0))
    out = hw.measure_sft(None, WordTok(), fake_torch(), core, 1111, 64, 4096, tmp_path)
    assert runs == [hw.SFT_WARMUP_STEPS, hw.SFT_TIMED_STEPS]
    assert out["row_tokens"] == 1200 and out["batch_size"] == 4 and "seconds_per_step" in out
    assert out["supervised_tokens_per_step"] == 4 * 1000


SCRIPT = (HF / "round02_hwsmoke.sh").read_text(encoding="utf-8")


def test_the_smoke_job_needs_no_bank_and_uploads_privately_under_its_job():
    assert "BANK_PATH" not in SCRIPT and "snapshot_download" not in SCRIPT
    assert "HfSandboxPoolRunner" not in SCRIPT, "no verifier Space is needed"
    assert "bash training/hf/pinned_deps.sh" in SCRIPT
    assert SCRIPT.index("bash training/hf/pinned_deps.sh") < SCRIPT.index("python3 training/hf/hwsmoke.py")
    assert 'python3 training/hf/hwsmoke.py --revision "$QWEN_REV" --output "$OUT"' in SCRIPT
    assert 'path_in_repo="round-02/%s/hwsmoke" % job' in SCRIPT
    assert "private is not True" in SCRIPT
    assert 'print("== hwsmoke:", json.dumps(public_view(report)))' in SCRIPT


def test_the_full_length_call_forces_every_sequence_to_the_cap(monkeypatch, tmp_path):
    import verified_evaluation
    hw = load("hwsmoke")
    seen = {}

    def evaluate(model, tok, cases, verifier, policy, output, step, torch, **kw):
        seen.update(policy=policy)
        return {}, [{"completion_tokens": 1024, "truncated": True} for _ in range(len(cases) * 16)]
    monkeypatch.setattr(verified_evaluation, "evaluate", evaluate)
    row = hw.measure_generation(None, None, fake_torch(), 256, 1024, 1111, tmp_path, full_length=True)
    assert seen["policy"]["min_new_tokens"] == seen["policy"]["max_new_tokens"] == 1024
    # Otherwise the pilot's decoding, untouched.
    from pipeline.training_contract import decoding
    assert {k: v for k, v in seen["policy"].items() if k != "min_new_tokens"} == decoding(1024)
    assert row["full_length"] is True and row["truncated"] == 256 and row["max_completion_tokens"] == 1024


@pytest.mark.parametrize("exc, want", [(RuntimeError("CUDA error: out of memory"), "out-of-memory"),
                                       (RuntimeError("CUBLAS_STATUS_ALLOC_FAILED\nmore"),
                                        "RuntimeError: CUBLAS_STATUS_ALLOC_FAILED"),
                                       (ValueError("x" * 500), "ValueError: " + "x" * 200)])
def test_any_failed_call_is_recorded_and_one_line(monkeypatch, tmp_path, exc, want):
    import verified_evaluation
    hw = load("hwsmoke")

    def evaluate(*a, **kw):
        raise exc
    monkeypatch.setattr(verified_evaluation, "evaluate", evaluate)
    row = hw.measure_generation(None, None, fake_torch(), 128, 1024, 1111, tmp_path)
    assert row["error"] == want and row["peak_memory_bytes"] == 123 and "seconds" not in row


def test_an_sft_row_that_cannot_be_built_is_recorded_not_raised(tmp_path):
    hw = load("hwsmoke")

    def build_examples(tok, rows, max_seq):
        raise ValueError("the tokeniser merges across the prompt/completion boundary")
    out = hw.measure_sft(None, WordTok(), fake_torch(), SimpleNamespace(build_examples=build_examples),
                         1111, 64, 4096, tmp_path)
    assert out["error"].startswith("ValueError: the tokeniser merges") and "seconds_per_step" not in out
    over = hw.measure_sft(None, WordTok(), fake_torch(),
                          SimpleNamespace(build_examples=lambda tok, rows, max_seq: ([], 4)),
                          1111, 64, 4096, tmp_path)
    assert "exceed --max-seq" in over["error"]


def test_the_report_is_written_atomically(tmp_path):
    hw = load("hwsmoke")
    path = tmp_path / "hwsmoke.json"
    hw.write_atomically(path, {"status": "measuring"})
    hw.write_atomically(path, {"status": "complete"})
    assert json.loads(path.read_text()) == {"status": "complete"}
    assert [p.name for p in tmp_path.iterdir()] == ["hwsmoke.json"]


class FakeModel:
    def __init__(self):
        self.config = SimpleNamespace()
        self.generation_config = SimpleNamespace()


def fake_gpu_modules(monkeypatch, hw):
    """torch, transformers and lypning_lora as `measure` imports them, on no GPU."""
    import sys
    cuda = FakeCuda()
    cuda.is_available = lambda: True
    cuda.is_bf16_supported = lambda: True
    cuda.get_device_name = lambda: "fake"
    cuda.get_device_properties = lambda i: SimpleNamespace(total_memory=141 * 2 ** 30)
    cuda.memory_allocated = lambda: 54 * 2 ** 30
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=cuda, bfloat16="bf16"))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(set_seed=lambda seed: None))
    monkeypatch.setitem(sys.modules, "lypning_lora", SimpleNamespace(
        check_adapted_modules=lambda model: (400, 100), log=lambda msg: None))
    tv = SimpleNamespace(block_fused_kernels=lambda: {"fla": "blocked"}, refuse_import_binding=lambda: None,
                         load_tokenizer=lambda rev: SimpleNamespace(pad_token_id=0),
                         download_base=lambda rev: "/snapshot", load_base_model=lambda path, dtype: FakeModel(),
                         attach_fresh_lora=lambda model, rank, seed, core: model,
                         refuse_bound_kernels=lambda model: {"chunk_gated_delta_rule": "torch"})
    monkeypatch.setattr(hw, "tv", tv)
    monkeypatch.setattr(hw, "runtime_versions", lambda: {"torch": "fake"})


def test_every_measurement_is_saved_before_it_starts_and_one_failure_does_not_stop_the_rest(
        monkeypatch, tmp_path):
    hw = load("hwsmoke")
    fake_gpu_modules(monkeypatch, hw)
    out = tmp_path / "hwsmoke"
    order = []

    def on_disk():
        return json.loads((out / "hwsmoke.json").read_text())

    def generation(model, tok, torch, sequences, max_new_tokens, seed, scratch, full_length=False):
        name = "generation-full-length-%d" % sequences if full_length else "generation-%d" % sequences
        assert on_disk()["in_progress"] == name
        order.append(name)
        if sequences == 128:
            return {"sequences": 128, "error": "out-of-memory"}
        return {"sequences": sequences, "seconds": 900.0 if full_length else 300.0}

    def sft(model, tok, torch, core, seed, row_tokens, max_seq, scratch, timed_steps=20, name="sft"):
        assert on_disk()["in_progress"] == name
        order.append(name)
        return {"seconds_per_step": 20.0 if name == "sft" else 6.0}
    monkeypatch.setattr(hw, "measure_generation", generation)
    monkeypatch.setattr(hw, "measure_sft", sft)
    assert hw.main(["--revision", "r" * 40, "--output", str(out)]) == 1
    # Every generation call precedes every optimizer step.
    assert order == ["generation-256", "generation-128", "generation-full-length-256", "sft", "sft-typical"]
    report = on_disk()
    assert "in_progress" not in report and report["status"] == "failed"
    assert report["errors"] == ["generation-128"]
    assert report["verdict"] == {"eval_sequences_256_fits": True, "sft_batch_4_fits": True}
    assert report["projection"]["verdict"] and set(report["projection_bounds"]) == {"upper", "lower",
                                                                                     "realistic"}
    assert report["projection_bounds"]["upper"]["same_job_minutes"] > report["projection"]["same_job_minutes"]
    assert report["kernel_binding"] == {"chunk_gated_delta_rule": "torch"}


def test_a_full_length_out_of_memory_fails_the_256_verdict(monkeypatch, tmp_path):
    hw = load("hwsmoke")
    fake_gpu_modules(monkeypatch, hw)
    monkeypatch.setattr(hw, "measure_generation", lambda *a, full_length=False: (
        {"sequences": a[3], "error": "out-of-memory"} if full_length else {"sequences": a[3], "seconds": 60.0}))
    monkeypatch.setattr(hw, "measure_sft", lambda *a, **kw: {"seconds_per_step": 10.0})
    assert hw.main(["--revision", "r" * 40, "--output", str(tmp_path / "o")]) == 1
    report = json.loads((tmp_path / "o" / "hwsmoke.json").read_text())
    assert report["verdict"]["eval_sequences_256_fits"] is False
    assert "error" in report["projection_bounds"]["upper"]


def test_sigterm_mid_measurement_is_recorded_before_the_upload_trap(monkeypatch, tmp_path):
    hw = load("hwsmoke")
    fake_gpu_modules(monkeypatch, hw)

    def generation(*a, **kw):
        raise hw.Terminated()
    monkeypatch.setattr(hw, "measure_generation", generation)
    assert hw.main(["--revision", "r" * 40, "--output", str(tmp_path / "o")]) == 124
    report = json.loads((tmp_path / "o" / "hwsmoke.json").read_text())
    assert report["status"] == "terminated" and "generation-256" in report["error"]
    assert report["load_seconds"] is not None
    import signal
    assert signal.getsignal(signal.SIGTERM) is not hw._terminate, "the handler outlived main"


def test_a_loader_refusal_is_recorded_and_still_raised(monkeypatch, tmp_path):
    hw = load("hwsmoke")
    fake_gpu_modules(monkeypatch, hw)

    def refuse(model):
        raise SystemExit("gated-delta-net is not bound to the torch reference")
    monkeypatch.setattr(hw.tv, "refuse_bound_kernels", refuse)
    with pytest.raises(SystemExit):
        hw.main(["--revision", "r" * 40, "--output", str(tmp_path / "o")])
    report = json.loads((tmp_path / "o" / "hwsmoke.json").read_text())
    assert report["status"] == "failed" and "torch reference" in report["error"]
    assert report["in_progress"] == "load"


def test_a_dependency_drift_refuses_before_the_weight_pull(monkeypatch, tmp_path):
    hw = load("hwsmoke")
    fake_gpu_modules(monkeypatch, hw)
    pulled = []
    monkeypatch.setattr(hw.tv, "download_base", lambda rev: pulled.append(rev))

    def drift():
        raise RuntimeError("GPU dependency versions differ from the pinned experiment")
    monkeypatch.setattr(hw, "runtime_versions", drift)
    with pytest.raises(RuntimeError):
        hw.main(["--revision", "r" * 40, "--output", str(tmp_path / "o")])
    assert pulled == []
    assert json.loads((tmp_path / "o" / "hwsmoke.json").read_text())["in_progress"] == "kernels"
