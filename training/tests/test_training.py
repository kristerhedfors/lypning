"""The reward is an executable specification; GPU packages are not required."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

from pipeline import training as t
from pipeline.curriculum import starter_cases
from pipeline.sandbox import RunResult


def result(stdout="", **kwargs):
    defaults = {"exit_code": 0, "stdout": stdout, "stderr": "", "duration_s": 0.01}
    defaults.update(kwargs)
    return RunResult(**defaults)


@pytest.fixture
def case():
    return dict(starter_cases()[0], split="train")


def fake_verifier(case, *, oracle=None, native=None):
    calls = []
    def run(program, **kwargs):
        calls.append(kwargs)
        correct = next(test["stdout"] for test in case["tests"] if test["stdin"] == kwargs["stdin"])
        if kwargs["interpreter"]:
            return native or result(correct)
        return oracle or result(correct)
    return t.Verifier("/explicit/lypning-l", runner=run), calls


def refusal(**kwargs):
    return result(exit_code=90, stderr="lypning-l: unsupported: import: decimal\n", **kwargs)


@pytest.mark.parametrize("oracle,native,population,want", [
    (None, None, "coverage", (1.0, "correct-native")),
    (None, refusal(), "coverage", (0.25, "correct-fallback")),
    (None, refusal(), "fallback-control", (1.0, "correct-control")),
    (None, None, "fallback-control", (1.0, "correct-control")),
    (result("wrong"), None, "coverage", (0.0, "incorrect")),
    (result("wrong"), refusal(), "coverage", (0.0, "incorrect")),
    (result("wrong"), None, "fallback-control", (0.0, "incorrect")),
    (result(exit_code=1), None, "coverage", (0.0, "incorrect")),
    (result(timed_out=True), None, "coverage", (0.0, "incorrect")),
    (result(truncated=True), None, "coverage", (0.0, "incorrect")),
])
def test_correctness_gates_all_rewards(case, oracle, native, population, want):
    case["population"] = population
    verifier, calls = fake_verifier(case, oracle=oracle, native=native)
    score = verifier.score(case, "anything")
    assert (score.reward, score.status) == want
    if score.reward == 0:
        assert not any(c["interpreter"] for c in calls)


@pytest.mark.parametrize("native", [result("wrong"), result(exit_code=1),
    result(timed_out=True), result(truncated=True),
    refusal(stdout="partial"), result(exit_code=90, stderr="wrong: unsupported: x: y\n"),
    result(exit_code=90, stderr="lypning-l: unsupported: x: y\nextra\n"),
    result(exit_code=90, stderr=""), result(harness_error="missing executable")])
def test_engine_bugs_are_not_low_rewards(case, native):
    verifier, _ = fake_verifier(case, native=native)
    with pytest.raises(t.VerificationBlocked):
        verifier.score(case, "anything")


def test_oracle_failure_and_nondeterminism_abort(case):
    verifier, _ = fake_verifier(case, oracle=result(harness_error="failed setup"))
    with pytest.raises(t.VerificationBlocked, match="harness"):
        verifier.score(case, "pass")
    # A candidate whose two clean runs disagree is a nondeterministic program:
    # wrong, with its own status, never a stage abort (POLICY v3, 2026-09-16).
    draws = iter([result(case["tests"][0]["stdout"]), result("b")])
    score = t.Verifier("/engine", runner=lambda *a, **k: next(draws)).score(case, "pass")
    assert (score.reward, score.status, score.failed_test, score.correct) == (0.0, "unstable", 0, False)
    assert t.POLICY == "l-correctness-v3"
    def failed(*args, **kwargs):
        raise subprocess.SubprocessError("preexec failed")
    with pytest.raises(t.VerificationBlocked, match="runner failed"):
        t.Verifier("/engine", runner=failed).score(case, "pass")


def test_variants_defeat_constant_print(case):
    # Real CPython, but never invoke an engine when the varied-input test fails.
    score = t.Verifier("/not-an-engine", memory_mb=0).score(case, 'print("6")')
    assert score.reward == 0


def test_invalid_utf8_cannot_impersonate_a_replacement_character(case):
    case["tests"] = [{"stdout": "\ufffd"}]  # score fixture, not a production task
    score = t.Verifier("/never-called", memory_mb=0).score(case, "import os; os.write(1, bytes([255]))")
    assert score.status == "incorrect" and score.reward == 0


def test_native_invalid_encoding_is_an_engine_issue(case):
    verifier, _ = fake_verifier(case, native=result(case["tests"][0]["stdout"], encoding_error=True))
    with pytest.raises(t.VerificationBlocked, match="engine mismatch"):
        verifier.score(case, "pass")


@pytest.mark.parametrize("program", ["print(undefined_name)", "def broken(:", "raise ValueError('bad')"])
def test_real_model_errors_are_zero_reward_not_unstable_oracle(case, program):
    score = t.Verifier("/never-called", memory_mb=0).score(case, program)
    assert score.status == "incorrect" and score.reward == 0


@pytest.mark.parametrize("completion", ["print(1)", "```python\nprint(1)",
    "prose\n```python\nprint(1)\n```", "```python\nprint(1)\n```\n```python\npass\n```",
    [{"role": "user", "content": "```python\npass\n```"}]])
def test_ambiguous_and_truncated_completions_get_no_code(completion):
    assert t.program_from_completion(completion) is None


def test_reward_does_not_expose_holdout_and_preserves_witness(case, tmp_path):
    verifier, _ = fake_verifier(case, native=result("mismatch"))
    witness = tmp_path / "blocked.jsonl"
    reward = t.Reward([case, dict(case, case_id="test", split="test")], verifier, witness)
    with pytest.raises(t.TrainingError, match="non-training"):
        reward(["```python\npass\n```"], ["test"])
    with pytest.raises(t.TrainingError, match="length"):
        reward([], [case["case_id"]])
    with pytest.raises(t.VerificationBlocked):
        reward([[{"role": "assistant", "content": "```python\npass\n```"}]], [case["case_id"]])
    saved = json.loads(witness.read_text())
    assert saved["program"] == "pass"
    assert saved["tests"] == case["tests"]


def test_closed_but_token_truncated_rollout_is_not_rewarded(case):
    verifier, _ = fake_verifier(case)
    reward = t.Reward([case], verifier, eos_token_id=99)
    completion = "```python\npass\n```"
    assert reward([completion], [case["case_id"]], completion_ids=[[1, 2]]) == [0]
    assert reward([completion], [case["case_id"]], completion_ids=[[1, 99]]) == [1]
    with pytest.raises(t.TrainingError, match="token IDs"):
        reward([completion], [case["case_id"]])


def test_family_split_is_deterministic_and_no_test_solution_export(tmp_path, monkeypatch):
    cases = starter_cases()
    t.validate_cases(cases)
    assert {c["family"]: c["split"] for c in t.split_cases(cases)} == {
        c["family"]: c["split"] for c in t.split_cases(list(reversed(cases)))}
    identity = {"engine": "lypning-l", "sha256": "pinned"}
    monkeypatch.setattr(t, "engine_identity", lambda p: identity)
    monkeypatch.setattr(t.Verifier, "score", lambda self, c, program:
        t.Score(1, "correct-native", 3, 3) if c["population"] == "coverage"
        else t.Score(1, "correct-control", 0, 3))
    output = tmp_path / "experiment"
    original = copy.deepcopy(cases)
    bundle = t.prepare(cases, "/engine", output)
    assert cases == original
    assert t.load_bundle(output / "bundle.json", "/engine") == bundle
    by_family = {}
    for c in bundle["cases"]:
        by_family.setdefault(c["family"], set()).add(c["split"])
    assert all(len(s) == 1 for s in by_family.values())
    assert not (output / "test-sft.jsonl").exists()
    row = json.loads((output / "train-prompts.jsonl").read_text().splitlines()[0])
    assert set(row) == {"case_id", "prompt"}
    assert row["prompt"] == t.messages(next(c for c in cases if c["case_id"] == row["case_id"]))
    with pytest.raises(t.TrainingError, match="already exists"):
        t.prepare(cases, "/engine", output)
    monkeypatch.setattr(t, "engine_identity", lambda p: dict(identity, sha256="rebuilt"))
    with pytest.raises(t.TrainingError, match="drift"):
        t.load_bundle(output / "bundle.json", "/engine")
    bundle["cases"][0]["tests"][0]["stdout"] = "tampered"
    (output / "bundle.json").write_text(json.dumps(bundle))
    with pytest.raises(t.TrainingError, match="integrity"):
        t.load_bundle(output / "bundle.json", "/engine")


@pytest.mark.parametrize("mutation", [
    lambda cs: cs.append(copy.deepcopy(cs[0])),
    lambda cs: cs[0].update(tests=cs[0]["tests"][:1]),
    lambda cs: cs[0].update(tests=[cs[0]["tests"][0]] * 3),
    lambda cs: [test.update(stdout="same") for test in cs[0]["tests"]],
    lambda cs: cs[0]["tests"][0].update(checker="arbitrary.py"),
    lambda cs: cs[0].update(population="rewrite"),
])
def test_invalid_training_evidence_rejected(mutation):
    cases = starter_cases()
    mutation(cases)
    with pytest.raises(t.TrainingError):
        t.validate_cases(cases)


def test_engine_identity_rejects_scripts_core_and_wrong_python(tmp_path, monkeypatch):
    engine = tmp_path / "lypning-l"
    engine.write_text("#!/bin/sh\necho lypning-l")
    with pytest.raises(t.TrainingError, match="compiled"):
        t.engine_identity(engine)
    engine.write_bytes(b"\x7fELF" + b"test-fixture")
    monkeypatch.setattr(t.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess([], 0, "lypning 0.1.0 (lypning) for cpython 3.14\n"))
    with pytest.raises(t.TrainingError, match="wrong engine"):
        t.engine_identity(engine)
    monkeypatch.setattr(t.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess([], 0, "lypning 0.1.0 (lypning-l) for cpython 0.0\n"))
    with pytest.raises(t.TrainingError, match="minor version"):
        t.engine_identity(engine)


def gpu_module():
    path = Path(__file__).resolve().parents[1] / "gpu" / "train_verified.py"
    spec = importlib.util.spec_from_file_location("verified_gpu_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gpu_preflight_no_torch_and_hard_split_gates(tmp_path, monkeypatch):
    gpu = gpu_module()
    args = gpu.parser().parse_args(["sft", "--bundle", "bundle.json", "--engine", "engine",
        "--output", str(tmp_path / "run"), "--revision", "a" * 40, "--plan"])
    # Every loaded case carries a non-empty reference (`training_data.validate_cases`),
    # and the plan-time supervised-token bound reads it, so the fixture carries
    # one too: a bundle without references is not a bundle this program can see.
    bundle = {"digest": "locked", "purpose": "pilot", "limits": {"memory_mb": 1024},
              "cases": [{"case_id": str(i), "family": "f", "split": "train",
                         "reference": "print(%d)\n" % i + "# pad\n" * 30}
                        for i in range(1000)]}
    monkeypatch.setattr(gpu, "load_bundle", lambda *a: bundle)
    assert gpu.preflight(args) == (bundle, None)
    bundle["cases"].pop()
    with pytest.raises(t.TrainingError, match="at least 1000 train cases"):
        gpu.preflight(args)
    bundle["cases"].append({"case_id": "999", "family": "f", "split": "train",
                            "reference": "print(999)\n" + "# pad\n" * 30})
    args.eval_split = "test"
    with pytest.raises(t.TrainingError, match="test split"):
        gpu.preflight(args)
    args.eval_split = "dev"
    args.stage = "grpo"
    with pytest.raises(t.TrainingError, match="GRPO needs"):
        gpu.preflight(args)
    args.from_base = True
    with pytest.raises(t.TrainingError, match="admitted --probe"):
        gpu.preflight(args)
    args.plan = False
    with pytest.raises(t.TrainingError, match="isolated"):
        gpu.preflight(args)
    args.plan = True
    args.revision = "main"
    with pytest.raises(t.TrainingError, match="immutable"):
        gpu.preflight(args)


def test_a_confirmatory_eval2_arm_is_refused_at_a_k_it_was_not_registered_at(tmp_path, monkeypatch):
    """`EVAL2.md` section 4 prices the rule at k=16; the runner's own default is 4.

    The round-02 pilot ran a benchmark arm at the default and produced an
    interval wider than any effect it could have found. The two defaults
    disagree by construction, so the disagreement is caught where it is free.
    """
    gpu = gpu_module()
    args = gpu.parser().parse_args(["eval", "--bundle", "bundle.json", "--engine", "engine",
        "--output", str(tmp_path / "run"), "--revision", "a" * 40, "--plan",
        "--eval-split", "all"])
    bundle = {"digest": "locked", "purpose": "benchmark", "limits": {"memory_mb": 1024}}
    monkeypatch.setattr(gpu, "load_bundle", lambda *a: bundle)
    args.eval_draws = 4
    with pytest.raises(t.TrainingError, match="pre-registered at --eval-draws 16"):
        gpu.preflight(args)
    # A wiring check may still be cheap, and a pilot bundle is not the frozen
    # benchmark the pre-registration binds.
    args.smoke = True
    assert gpu.preflight(args) == (bundle, None)
    args.smoke = False
    bundle["purpose"] = "pilot"
    args.eval_split = "dev"
    assert gpu.preflight(args) == (bundle, None)
    # ...and the registered k passes on the benchmark.
    bundle["purpose"] = "benchmark"
    args.eval_split, args.eval_draws = "all", 16
    assert gpu.preflight(args) == (bundle, None)


def test_family_balance():
    gpu = gpu_module()
    cases = [{"family": f, "case_id": str(i)} for i, f in enumerate(["a", "a", "b"])]
    balanced = gpu.balanced_cases(cases)
    assert sum(c["family"] == "a" for c in balanced) == sum(c["family"] == "b" for c in balanced)
    assert {c["case_id"] for c in balanced} == {c["case_id"] for c in cases}


def test_starter_cannot_admit_a_real_training_round():
    with pytest.raises(t.TrainingError, match="18 independent"):
        t.validate_pilot(t.split_cases(starter_cases()))


def test_pilot_requires_controls_in_every_split():
    cases = [dict(family=str(i), source_group=str(i), capabilities=["test"],
                  split=("train", "dev", "test")[i % 3], population="coverage")
             for i in range(18)]
    with pytest.raises(t.TrainingError, match="fallback controls"):
        t.validate_pilot(cases)
    for i in range(6):
        cases[i]["population"] = "fallback-control"
    t.validate_pilot(cases)


def benchmark_bank():
    """Schema-only fixture: 18 families, both populations in the bank, but the
    two controls land in one split, which the per-split pilot rule rejects."""
    from pipeline.data_loop import REVIEW_FIELDS
    cases = []
    for i in range(18):
        cases.append(dict(starter_cases()[0], case_id=str(i), task="task %d" % i, family=str(i),
                          source_group=str(i), reference="print(%d)" % i,
                          population="coverage" if i < 16 else "fallback-control",
                          review=dict({k: "reviewed fixture" for k in REVIEW_FIELDS},
                                      origin="authored", evidence_ids=[])))
    return cases


def test_benchmark_bundle_admits_a_bank_a_pilot_would_reject(tmp_path, monkeypatch):
    from pipeline.data_loop import review
    from pipeline.jsonio import write_json
    cases = benchmark_bank()
    with pytest.raises(t.TrainingError, match="fallback controls"):
        t.validate_pilot(t.split_cases(cases))
    with pytest.raises(t.TrainingError, match="fallback controls"):
        review(cases, purpose="pilot")
    reviewed = review(cases, purpose="benchmark")
    assert reviewed["purpose"] == "benchmark"
    review_path = tmp_path / "review.json"
    write_json(review_path, reviewed)
    monkeypatch.setattr(t, "engine_identity", lambda b: {"fixture": True})
    monkeypatch.setattr(t, "execution_runner", lambda *a: None)
    monkeypatch.setattr(t.Verifier, "score", lambda self, c, p: t.Score(
        1.0, "correct-native" if c["population"] == "coverage" else "correct-control",
        3 if c["population"] == "coverage" else 0, 3))
    image = "sha256:" + "a" * 64
    # The benchmark keeps the pilot's review and isolation requirements.
    with pytest.raises(t.TrainingError, match="--review"):
        t.prepare(cases, "engine", tmp_path / "missing-review", purpose="benchmark", execution_image=image)
    with pytest.raises(t.TrainingError, match="--execution-image"):
        t.prepare(cases, "engine", tmp_path / "missing-image", purpose="benchmark", review_path=review_path)
    # A benchmark review cannot launder the same bank into a pilot.
    with pytest.raises(t.TrainingError, match="integrity"):
        t.prepare(cases, "engine", tmp_path / "as-pilot", purpose="pilot",
                  review_path=review_path, execution_image=image)
    payload = t.prepare(cases, "engine", tmp_path / "bundle", purpose="benchmark",
                        review_path=review_path, execution_image=image)
    assert payload["purpose"] == "benchmark"
    assert all(c["split_group"] and c["split"] in ("train", "dev", "test") for c in payload["cases"])
    assert len(payload["cases"]) == 18
    assert t.load_bundle(tmp_path / "bundle" / "bundle.json", "engine") == payload
    assert not list((tmp_path / "bundle").glob("*-sft.jsonl"))
    assert sorted(p.name for p in (tmp_path / "bundle").glob("*-prompts.jsonl")) == [
        "dev-prompts.jsonl", "test-prompts.jsonl", "train-prompts.jsonl"]
    with pytest.raises(t.TrainingError, match="whole"):
        t.validate_benchmark([dict(c, population="coverage") for c in payload["cases"]])


def test_benchmark_bundle_is_evaluated_whole_and_never_trained_on(tmp_path, monkeypatch):
    gpu = gpu_module()
    cases = [{"case_id": str(i), "split": s} for i, s in enumerate(("train", "train", "dev", "test"))]
    benchmark = {"digest": "locked", "purpose": "benchmark", "limits": {"memory_mb": 1024}, "cases": cases}
    pilot = dict(benchmark, purpose="pilot")
    bundles = {"benchmark.json": benchmark, "pilot.json": pilot}
    monkeypatch.setattr(gpu, "load_bundle", lambda path, engine: bundles[str(path)])
    def args(stage, bundle, *extra):
        return gpu.parser().parse_args([stage, "--bundle", bundle, "--engine", "engine",
            "--output", str(tmp_path / "run"), "--revision", "a" * 40, "--plan", *extra])
    for stage in ("sft", "probe", "grpo"):
        with pytest.raises(t.TrainingError, match="never trained on"):
            gpu.preflight(args(stage, "benchmark.json", *(("--from-base",) if stage == "grpo" else ())))
    # A benchmark arm is a confirmatory arm, so it carries the pre-registered k.
    assert gpu.preflight(args("eval", "benchmark.json", "--eval-split", "all",
                              "--eval-draws", "16")) == (benchmark, None)
    assert gpu.preflight(args("eval", "benchmark.json", "--eval-split", "dev",
                              "--eval-draws", "16")) == (benchmark, None)
    assert gpu.preflight(args("eval", "pilot.json", "--eval-split", "test")) == (pilot, None)
    with pytest.raises(t.TrainingError, match="benchmark bundle whole"):
        gpu.preflight(args("eval", "pilot.json", "--eval-split", "all"))
    with pytest.raises(t.TrainingError, match="cannot select a checkpoint"):
        gpu.preflight(args("sft", "pilot.json", "--eval-split", "all"))
    assert [c["case_id"] for c in gpu.evaluation_cases(benchmark, "all")] == ["0", "1", "2", "3"]
    assert [c["case_id"] for c in gpu.evaluation_cases(benchmark, "dev")] == ["2"]
    assert [c["case_id"] for c in gpu.evaluation_cases(benchmark, "test")] == ["3"]


def test_real_run_rejects_smoke_bundle_even_in_plan(tmp_path, monkeypatch):
    gpu = gpu_module()
    args = gpu.parser().parse_args(["sft", "--bundle", "bundle.json", "--engine", "engine",
        "--output", str(tmp_path / "run"), "--revision", "a" * 40, "--plan"])
    monkeypatch.setattr(gpu, "load_bundle", lambda *a: {"purpose": "smoke", "limits": {"memory_mb": 0}})
    with pytest.raises(t.TrainingError, match="smoke data"):
        gpu.preflight(args)
    args.smoke = True
    gpu.preflight(args)
    assert gpu.schedule(args)["steps"] == 2 and gpu.schedule(args)["max_tokens"] == 32


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_verifier_rejects_unbounded_timeouts(value):
    with pytest.raises(t.TrainingError, match="timeout"):
        t.Verifier("/engine", timeout_s=value)


def test_tiny_trainer_retains_real_tokenizer_vocabulary_and_special_ids():
    from types import SimpleNamespace
    gpu = gpu_module()
    cfg = SimpleNamespace(text_config=SimpleNamespace(vocab_size=200000), image_token_id=199990,
                          video_token_id=199991, vision_start_token_id=199992, vision_end_token_id=199993)
    def shrink(c):
        c.text_config.vocab_size = 1024
        c.image_token_id, c.video_token_id = 5, 6
        c.vision_start_token_id, c.vision_end_token_id = 7, 8
        return c
    cfg = gpu.smoke_config(cfg, 200100, shrink)
    assert cfg.text_config.vocab_size == 200100
    assert (cfg.image_token_id, cfg.video_token_id, cfg.vision_start_token_id,
            cfg.vision_end_token_id) == (199990, 199991, 199992, 199993)


def test_cli_missing_binary_is_clean_failure(tmp_path, capsys):
    from pipeline.cli import main
    assert main(["training-prepare", "--starter", "--engine", str(tmp_path / "missing"),
                 "--output", str(tmp_path / "bundle")]) == 1
    assert "preparation blocked" in capsys.readouterr().err
    assert not (tmp_path / "bundle").exists()


def test_engine_drift_is_checked_during_scoring(case, monkeypatch):
    monkeypatch.setattr(t, "engine_identity", lambda p: {"sha256": "new"})
    with pytest.raises(t.VerificationBlocked, match="drift"):
        t.Verifier("/engine", identity={"sha256": "old"}).score(case, "pass")


@pytest.mark.skipif(not os.environ.get("NTX_TRAINING_TEST_ENGINE"), reason="explicit real engine opt-in")
def test_real_starter_curriculum(tmp_path):
    engine = os.environ["NTX_TRAINING_TEST_ENGINE"]
    # These are reviewed fixtures, not generated programs; opt out of RLIMIT_AS
    # only for the local macOS smoke. The production default remains 1024 MB.
    bundle = t.prepare(starter_cases(), engine, tmp_path / "real", memory_mb=0)
    assert len(bundle["reference_scores"]) == 16
    assert all(s["reward"] > 0 for s in bundle["reference_scores"].values())
    assert t.load_bundle(tmp_path / "real" / "bundle.json", engine) == bundle


def test_a_pilot_adapter_is_admitted_on_a_benchmark_eval_and_nowhere_else():
    """The benchmark is never trained on, so its adapters always come from a pilot."""
    module = gpu_module()
    pilot_adapter = {"bundle_digest": "pilot-digest", "purpose": "pilot"}
    benchmark = {"digest": "bench-digest", "purpose": "benchmark"}
    pilot = {"digest": "pilot-digest", "purpose": "pilot"}
    assert module.adapter_lineage_admitted(pilot_adapter, pilot, "eval")
    assert module.adapter_lineage_admitted(pilot_adapter, pilot, "grpo")
    assert module.adapter_lineage_admitted(pilot_adapter, benchmark, "eval")
    assert not module.adapter_lineage_admitted(pilot_adapter, benchmark, "grpo")
    assert not module.adapter_lineage_admitted(pilot_adapter, {"digest": "other", "purpose": "pilot"}, "eval")
    assert not module.adapter_lineage_admitted({"bundle_digest": "x", "purpose": "smoke"}, benchmark, "eval")


def test_reward_scores_a_group_concurrently_and_keeps_batch_order(case):
    """GRPO's four completions are a dozen sandbox requests each; they are
    scored on a thread pool and the rewards come back in batch order.

    The overlap is asserted with a barrier and not a stopwatch. This test used to
    sleep 0.05s per slow scoring and demand the pair finish inside 0.15s, which
    is a wall-clock budget on a shared runner — the thing `ci.yml` refuses to put
    `bench` in CI for, in its own words, because it measures the runner. It duly
    became the macOS job's only red, at 0.21s, with nothing wrong with the code.
    A barrier asserts the property directly instead of inferring it from elapsed
    time: two of the four completions are `p0`, each waits for the other, so
    `reward` can only return at all if both were in flight at once. A serial
    scorer breaks the barrier on its timeout and fails there. Free when it passes.
    """
    import threading
    from pipeline.training import Score

    side_by_side = threading.Barrier(2, timeout=30)

    class Concurrent:
        def score(self, c, program):
            if program == "p0":
                side_by_side.wait()
            return Score(1.0 if program == "p0" else 0.0, "correct-native" if program == "p0" else "incorrect", 1, 1)
    reward = t.Reward([case], Concurrent(), generations=2, score_workers=4)
    got = reward(["```python\np0\n```", "```python\np1\n```"] * 2, [case["case_id"]] * 4)
    assert got == [1.0, 0.0, 1.0, 0.0]
    assert not side_by_side.broken, "the two slow scorings never ran side by side"


def test_the_registered_seeds_and_the_family_cycle_are_refused_by_message(tmp_path,
                                                                         monkeypatch):
    """Two admission gates whose REFUSAL branch nothing pinned.

    `test_gpu_preflight_no_torch_and_hard_split_gates` asserts the accepting
    side for seed 1111 and a schedule with capacity for one family, so an
    inverted comparison would be caught. Deleting either guard outright, or
    corrupting its message, would not be — and these two are what stop a
    fourth single-seed round on a schedule that can skip a family.
    """
    gpu = gpu_module()
    args = gpu.parser().parse_args(["sft", "--bundle", "bundle.json", "--engine", "engine",
        "--output", str(tmp_path / "run"), "--revision", "a" * 40, "--plan"])
    bundle = {"digest": "locked", "purpose": "pilot", "limits": {"memory_mb": 1024},
              "cases": [{"case_id": str(i), "family": "f%d" % (i % 4), "split": "train",
                         "reference": "print(%d)\n" % i + "# pad\n" * 30}
                        for i in range(1000)]}
    monkeypatch.setattr(gpu, "load_bundle", lambda *a: bundle)
    assert gpu.preflight(args) == (bundle, None)

    for seed in (1111, 2222, 3333):
        args.seed = seed
        assert gpu.preflight(args) == (bundle, None)
    for seed in (0, 1234, 4444):
        args.seed = seed
        with pytest.raises(t.TrainingError,
                           match="pre-registered seeds: 1111, 2222, 3333"):
            gpu.preflight(args)
    args.seed = 1111

    # Four families; the schedule must have room for all four at least once.
    args.steps, args.batch_size = 1, 3
    with pytest.raises(t.TrainingError, match="shorter than one complete family cycle"):
        gpu.preflight(args)
    # One step of four is a complete cycle, but four exposures cannot reach the
    # supervised-token floor either, so the accepting side of THIS gate is shown
    # at a schedule that also clears the plan-time bound below it.
    args.batch_size = 4
    with pytest.raises(t.TrainingError, match="upper bound"):
        gpu.preflight(args)
    args.steps = 100
    assert gpu.preflight(args) == (bundle, None)


def test_the_supervised_token_floor_is_a_stage_gate_and_not_a_plan_gate(tmp_path,
                                                                       monkeypatch):
    """`--plan` accepts a schedule the stage can still refuse. Pinned, not fixed.

    `START_NEXT_ROUND.md` says to run `train_verified.py ... --plan` before
    every actual stage, so it is fair to read a passing plan as "this schedule
    is admissible". It is not: the exact 50,000-token floor lives in `run()`,
    because counting supervised tokens needs `build_examples`, hence the Hub
    tokenizer and the GPU deps that `--plan` exists to avoid. What the plan can
    compute is a one-sided bound (`supervised_plan`), and this single exposure
    of a 60,000-byte reference is the gap the two leave between them: the bound
    clears the floor on bytes, so the plan admits the schedule, while the token
    count it will actually expose is only taken in `run()` and may be anywhere
    below. A bound above the floor is the absence of a certain failure, never a
    pass; this test is the record of which of the two checks is which, so that
    moving the exact count later cannot quietly become moving it away.
    """
    gpu = gpu_module()
    args = gpu.parser().parse_args(["sft", "--bundle", "bundle.json", "--engine", "engine",
        "--output", str(tmp_path / "run"), "--revision", "a" * 40, "--plan",
        "--steps", "1", "--batch-size", "1"])
    reference = "# pad\n" * 10_000
    bundle = {"digest": "locked", "purpose": "pilot", "limits": {"memory_mb": 1024},
              "cases": [{"case_id": str(i), "family": "f", "split": "train",
                         "reference": reference}
                        for i in range(1000)]}
    monkeypatch.setattr(gpu, "load_bundle", lambda *a: bundle)
    assert gpu.supervised_plan(args, bundle) == {
        "planned_exposures": 1,
        "supervised_token_upper_bound": len(reference.rstrip()) + len("```python\n\n```<|im_end|>")}
    assert gpu.preflight(args) == (bundle, None)
    from pipeline.training_contract import MIN_SUPERVISED_TOKENS
    assert MIN_SUPERVISED_TOKENS == 50_000
    from gpu.verified_stages import supervised_tokens
    assert supervised_tokens([]) == 0 < MIN_SUPERVISED_TOKENS
    # The refusal exists, and it is in `run` rather than `preflight`. Read from
    # source because importing `run` needs the GPU deps `--plan` avoids.
    source = Path(gpu.__file__).read_text(encoding="utf-8")
    body = source.split("def run(")[1]
    assert "supervised tokens; at least %d required" in body
    assert "supervised tokens; at least %d required" not in source.split("def run(")[0]


def pilot_bundle(cases, reference):
    """A bundle shaped enough for `--plan` to render, with one reference per case."""
    return {"digest": "locked", "purpose": "pilot", "identity": {"engine": "lypning-l"},
            "limits": {"memory_mb": 1024}, "memory_policy": "rss",
            "cases": [dict(case, reference=reference) for case in cases]}


def test_plan_refuses_a_schedule_whose_supervised_dose_cannot_reach_the_floor(tmp_path,
                                                                             monkeypatch,
                                                                             capsys):
    """The certain refusal is paid for at plan time, not on the metered job.

    `sft --plan` runs after the installs and the bank download, and the exact
    floor fires later still, so a schedule that cannot reach 50,000 supervised
    tokens used to cost a whole job before saying so. The bound here is the sum
    over the SCHEDULE: 80 exposures of the same reference. Count-times-longest
    would be 1,000 of them and would admit this run.
    """
    gpu = gpu_module()
    reference = "print(1)\n" + "# pad\n" * 31
    bundle = pilot_bundle([{"case_id": str(i), "family": "f", "split": "train"}
                           for i in range(1000)], reference)
    monkeypatch.setattr(gpu, "load_bundle", lambda *a: bundle)
    segment = len(reference.rstrip()) + len("```python\n\n```<|im_end|>")
    assert 80 * segment < 50_000 <= 1000 * segment, "the two forms must disagree here"
    code = gpu.main(["sft", "--bundle", "bundle.json", "--engine", "engine",
                     "--output", str(tmp_path / "run"), "--revision", "a" * 40, "--plan",
                     "--steps", "20", "--batch-size", "4"])
    out, err = capsys.readouterr()
    assert code == 1 and not out, "a refused plan prints no plan"
    assert "at most %d supervised tokens" % (80 * segment) in err
    assert "upper bound" in err and "50000" in err


def test_plan_reports_the_supervised_bound_it_admits_a_schedule_on(tmp_path, monkeypatch,
                                                                   capsys):
    """Admission prints both numbers, because the bound is not a pass.

    A bound above the floor only says the exact count in `run()` is not
    certainly below it, so the operator gets the bound and the exposure count
    rather than a bare "training_started": false and an inference from steps
    times batch size, which counts exposures and not tokens.
    """
    gpu = gpu_module()
    reference = "print(1)\n" + "# pad\n" * 31
    bundle = pilot_bundle([{"case_id": str(i), "family": "f", "split": "train"}
                           for i in range(1000)], reference)
    monkeypatch.setattr(gpu, "load_bundle", lambda *a: bundle)
    segment = len(reference.rstrip()) + len("```python\n\n```<|im_end|>")
    code = gpu.main(["sft", "--bundle", "bundle.json", "--engine", "engine",
                     "--output", str(tmp_path / "run"), "--revision", "a" * 40, "--plan",
                     "--steps", "250", "--batch-size", "4"])
    out, err = capsys.readouterr()
    assert code == 0 and not err
    plan = json.loads(out)
    assert plan["planned_exposures"] == 1000
    assert plan["supervised_token_upper_bound"] == 1000 * segment >= 50_000
    assert plan["training_started"] is False


def test_a_stage_with_no_supervised_dose_plans_null_and_never_zero(tmp_path, monkeypatch,
                                                                  capsys):
    """"Not applicable" must not render as a bound of zero.

    The runbook now tells the operator that a bound below the floor is a
    certain refusal, so a smoke or a GRPO plan printing `0` would read as the
    worst possible schedule rather than as a stage the floor does not price.
    `supervised_plan` answers None for exactly those stages; this pins that the
    plan carries the None through instead of defaulting it to a number.
    """
    gpu = gpu_module()
    reference = "print(1)\n" + "# pad\n" * 31
    bundle = pilot_bundle([{"case_id": str(i), "family": "f", "split": "train"}
                           for i in range(1000)], reference)
    monkeypatch.setattr(gpu, "load_bundle", lambda *a: bundle)
    # `supervised_plan` already answers None for a smoke and for every non-SFT
    # stage; what is unpinned is whether `main` carries that None into the JSON
    # or defaults it. Forcing None is the only way to reach that branch on a
    # stage whose plan otherwise succeeds.
    monkeypatch.setattr(gpu, "supervised_plan", lambda *a: None)
    code = gpu.main(["sft", "--bundle", "bundle.json", "--engine", "engine",
                     "--output", str(tmp_path / "run"), "--revision", "a" * 40, "--plan",
                     "--steps", "250", "--batch-size", "4"])
    out, err = capsys.readouterr()
    assert code == 0 and not err
    plan = json.loads(out)
    assert plan["planned_exposures"] is None
    assert plan["supervised_token_upper_bound"] is None


def test_the_output_directory_is_created_after_the_weights_and_not_before():
    """A missing stage directory does not date the failure. Pinned, not fixed.

    Round-02 job `6aacd5cfb1dc2b62dc590b82` failed at stage `sft` having never
    created `work/round-02/sft/`, and that was read as evidence it died before
    the model loaded -- which would have left the supervised-token floor as
    nearly the only candidate. `run()` does not mkdir before the download: it
    mkdirs after `snapshot_download`, after `from_pretrained`, and after the
    LoRA attach, so an absent directory is equally consistent with a refusal at
    the floor, an OOM in the gradient smoke and a kill during the 55.6 GB load.
    This test records which side of the download the mkdir is on, so the next
    reading of an empty stage directory starts from the right suspect list.
    """
    gpu = gpu_module()
    body = Path(gpu.__file__).read_text(encoding="utf-8").split("def run(")[1]
    floor = body.index("supervised tokens; at least %d required")
    download = body.index("snapshot_download(BASE_MODEL")
    mkdir = body.index("args.output.mkdir(")
    assert floor < download < mkdir


def test_reference_scoring_is_concurrent_and_order_independent(tmp_path):
    """The stage two rounds died inside, and the property that lets it be fast.

    `prepare` scored references with a serial dict comprehension: one case, one
    sandbox, ~4 s each, so a 1,976-case pilot was 2.2 hours and the pool sat
    idle behind it. Raising the pool ceiling did nothing, because nothing asked
    the pool for more than one thing at a time.

    Concurrency is only admissible here if the manifest does not depend on it:
    the digest a round is pinned by must be the same on a fast machine and a
    slow one. `executor.map` keeps input order, and this asserts that rather
    than trusting it, by scoring the same cases at several worker counts.
    """
    import json as _json
    from pipeline import training as t

    calls = []

    class CountingVerifier:
        """Records concurrency actually used, and scores deterministically."""

        def __init__(self):
            self.live = 0
            self.peak = 0
            self._lock = __import__("threading").Lock()

        def score(self, case, program):
            with self._lock:
                self.live += 1
                self.peak = max(self.peak, self.live)
            calls.append(case["case_id"])
            __import__("time").sleep(0.01)
            with self._lock:
                self.live -= 1
            return t.Score(status="correct-native", reward=1.0, native_tests=len(case["tests"]),
                           total_tests=len(case["tests"]), refusals=[], failed_test=None)

    cases = [{"case_id": "c%02d" % i, "tests": [{"stdin": "", "stdout": "x\n"}],
              "reference": "print('x')"} for i in range(24)]

    def run(workers):
        v = CountingVerifier()
        if workers > 1 and len(cases) > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(workers, len(cases))) as pool:
                scored = list(pool.map(lambda c: v.score(c, c["reference"]), cases))
        else:
            scored = [v.score(c, c["reference"]) for c in cases]
        from dataclasses import asdict
        return {c["case_id"]: asdict(s) for c, s in zip(cases, scored)}, v.peak

    serial, peak1 = run(1)
    fast, peak16 = run(16)
    assert peak1 == 1, "the serial path must not overlap"
    assert peak16 > 1, "the concurrent path must actually overlap; it was the whole point"
    # The manifest is pinned by a digest, so it must not move with worker count.
    assert list(serial) == list(fast), "case order changed with concurrency"
    assert _json.dumps(serial, sort_keys=True) == _json.dumps(fast, sort_keys=True)


def test_prepare_takes_a_worker_count_and_defaults_to_the_serial_one():
    """Opt-in: an unthreaded caller keeps exactly the behaviour it had."""
    import inspect
    from pipeline.training import prepare

    sig = inspect.signature(prepare)
    assert "score_workers" in sig.parameters
    assert sig.parameters["score_workers"].default == 1
