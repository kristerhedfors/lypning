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


def hand_total(dpm, step_s, load_min, download_min):
    """The approved arm-A job, counted by hand at a flat rate."""
    dev = 306 * 16 / dpm
    pilot = (projection.PREP_MINUTES + download_min + (load_min + dev) + (load_min + 4 * dev + 1050 * step_s / 60)
             + (load_min + dev) + (load_min + 1355 * 4 / dpm) + 2 * (load_min + 315 * 4 / dpm))
    eval2 = 2 * (load_min + 803 * 16 / dpm)
    return pilot, eval2


def test_the_projection_counts_every_stage_the_job_runs():
    got = projection.project(projection.flat_rate(40), 20.0, 3.0, 5.0, score_worker_seconds=0.0)
    pilot, eval2 = hand_total(40, 20.0, 3.0, 5.0)
    assert [s["stage"] for s in got["stages"]] == ["prep", "base-dev", "sft", "sft-dev-reload",
                                                   "probe", "test", "eval2"]
    assert got["sft_evaluations"] == 4
    assert got["split"]["pilot_minutes"] == pytest.approx(pilot, abs=0.5)
    assert got["same_job_minutes"] == pytest.approx(pilot + eval2, abs=0.5)
    assert got["split"]["eval2_minutes"] == pytest.approx(projection.EVAL2_PREP_MINUTES + 5.0 + eval2, abs=0.5)
    draws = {s["stage"]: s["draws"] for s in got["stages"]}
    assert (draws["base-dev"], draws["sft"], draws["probe"], draws["test"], draws["eval2"]) == \
        (4896, 4 * 4896, 5420, 2 * 315 * 4, 2 * 803 * 16)


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
    assert set(row) == {"sequences", "prompts", "draws", "max_new_tokens", "seconds", "generated_tokens",
                        "max_completion_tokens", "mean_completion_tokens", "truncated",
                        "sequences_per_minute", "generated_tokens_per_second", "peak_memory_bytes"}


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
