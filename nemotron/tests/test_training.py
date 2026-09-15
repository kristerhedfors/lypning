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
    draws = iter([result(case["tests"][0]["stdout"]), result("b")])
    with pytest.raises(t.VerificationBlocked, match="unstable"):
        t.Verifier("/engine", runner=lambda *a, **k: next(draws)).score(case, "pass")
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
    bundle = {"digest": "locked", "purpose": "pilot", "limits": {"memory_mb": 1024}}
    monkeypatch.setattr(gpu, "load_bundle", lambda *a: bundle)
    assert gpu.preflight(args) == (bundle, None)
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
