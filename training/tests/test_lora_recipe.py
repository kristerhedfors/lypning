from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path

import pytest

from pipeline.jsonio import sha256_of
from pipeline.training import messages
from pipeline.training_types import TrainingError

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("recipe_gpu", ROOT / "gpu/train_verified.py")
gpu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gpu)


def args(stage, *extra):
    return gpu.parser().parse_args([stage, "--bundle", "/bundle", "--engine", "/engine",
        "--output", "/new", "--revision", "a" * 40] + list(extra))


@pytest.mark.parametrize("stage,steps,want", [("sft", 99, 2e-4), ("sft", 100, 1e-4),
    ("sft", 300, 1e-4), ("grpo", 20, 1e-5), ("grpo", 500, 1e-5)])
def test_registered_lora_recipe_and_override(stage, steps, want):
    parsed = args(stage, "--steps", str(steps))
    assert gpu.schedule(parsed)["learning_rate"] == want
    assert gpu.schedule(parsed)["eval_every"] == 50
    parsed.lr = 3e-5
    assert gpu.schedule(parsed)["learning_rate"] == 3e-5


def test_smoke_uses_its_effective_dose_and_evaluates_every_step():
    parsed = args("sft", "--smoke", "--steps", "300")
    schedule = gpu.schedule(parsed)
    assert schedule == dict(steps=2, eval_every=1, max_tokens=32, learning_rate=2e-4)


def test_pilot_uses_shared_cadence_and_keeps_small_effective_batch():
    import re
    text = (ROOT / "hf/round02_pilot.sh").read_text()
    # 50 in both stages unless the arm says otherwise; SFT's cadence is now a
    # dispatched arm field (EVAL_EVERY, 350 for arm A), GRPO's is still 50.
    assert 'EVAL_EVERY="${EVAL_EVERY:-50}"' in text
    for stage, cadence in (("SFT", '--eval-every "$EVAL_EVERY"'), ("GRPO", "--eval-every 50")):
        flags = re.search(stage + r"_TRAIN=\(([^\n]+)\)", text).group(1)
        assert cadence in flags, stage
        assert "--lr" not in flags  # one recipe, resolved by the trainer
    assert '--batch-size 4' in text


def test_private_conditioned_targets_are_bound_to_bare_prompts_and_engine(tmp_path):
    cases = [
        {"case_id": "c", "family": "coverage-family", "task": "coverage task",
         "population": "coverage", "split": "train", "reference": "print(1)"},
        {"case_id": "r", "family": "retention-family", "task": "retention task",
         "population": "fallback-control", "split": "train", "reference": "print(2)"},
    ]
    run_id = "confirmatory-" + "a" * 40 + "-1"
    rows = []
    for case in cases:
        program = case["reference"]
        rows.append({"case_id": case["case_id"], "family": case["family"],
                     "population": case["population"],
                     "messages": messages(case) + [{"role": "assistant",
                         "content": "```python\n%s\n```" % program}],
                     "source": {"run_id": run_id, "arm": "subset-spec", "draw": 0,
                         "program_sha256": hashlib.sha256(program.encode()).hexdigest()}})
    path = tmp_path / "sft.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    (tmp_path / "sft-report.json").write_text(json.dumps({
        "schema": 1, "run_id": run_id, "rows": len(rows), "sft_sha256": sha256_of(rows),
        "lineage": {"engine_sha256": "e" * 64}}))
    bundle = {"identity": {"sha256": "e" * 64}, "cases": cases}
    parsed = args("sft", "--sft-targets", str(path))
    selected, loaded, report = gpu.sft_curriculum(parsed, bundle)
    assert [case["case_id"] for case in selected] == ["c", "r"]
    assert loaded == rows and report["run_id"] == run_id

    rows[0]["messages"][1]["content"] = "conditioned prompt leaked"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    (tmp_path / "sft-report.json").write_text(json.dumps({
        "schema": 1, "run_id": run_id, "rows": len(rows), "sft_sha256": sha256_of(rows),
        "lineage": {"engine_sha256": "e" * 64}}))
    with pytest.raises(TrainingError, match="bare train prompt"):
        gpu.sft_curriculum(parsed, bundle)


# --- the arm-A / arm-C recipe as declared (S4 preparation, 2026-09-22) -------


def test_grpo_geometry_defaults_to_four_prompts_of_eight_and_is_bounded():
    """One prompt group per step followed one task's variance; four of eight do not.

    The product is capped so a flag cannot quietly move a step into another
    batch regime, and the geometry is checked in `main` so --plan refuses it.
    """
    parsed = args("grpo")
    assert gpu.grpo_geometry(parsed) == {"prompts_per_step": 4, "generations": 8,
                                         "sequences_per_step": 32, "informative_only": False}
    assert gpu.grpo_geometry(args("sft")) is None
    with pytest.raises(TrainingError, match="exceeds 32"):
        gpu.grpo_geometry(args("grpo", "--grpo-prompts", "5"))
    with pytest.raises(TrainingError, match="two generations"):
        gpu.grpo_geometry(args("grpo", "--generations", "1"))
    with pytest.raises(TrainingError, match="give both"):
        gpu.grpo_geometry(args("grpo", "--grpo-informative-only"))
    with pytest.raises(TrainingError, match="give both"):
        gpu.grpo_geometry(args("sft", "--grpo-informative-only", "--probe", "/p/probe.json"))
    both = args("grpo", "--grpo-informative-only", "--probe", "/p/probe.json")
    assert gpu.grpo_geometry(both)["informative_only"] is True


def test_the_manifest_declares_seeded_init_the_optimizer_and_the_geometry():
    """Arm identity that is recorded rather than implied."""
    source = (ROOT / "gpu/train_verified.py").read_text(encoding="utf-8")
    run = source.split("def run(")[1].split("\ndef main(")[0]
    for key in ('"lora_init":', '"sft_optimizer":', '"grpo_geometry": grpo_geometry(args)',
                '"kernel_binding": kernel_binding'):
        assert key in run, key
    stages = (ROOT / "gpu/verified_stages.py").read_text(encoding="utf-8")
    assert "**SFT_OPTIMIZER" in stages


def test_lora_init_is_reseeded_immediately_before_attach_lora():
    """The gradient smoke reseeds to 0; without this every seed drew one `lora_A`."""
    import ast
    source = (ROOT / "gpu/train_verified.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    # The attach lives in `attach_fresh_lora`, shared with the hardware smoke;
    # the reseed must be the statement directly before it, from its own seed.
    fresh = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "attach_fresh_lora")
    body = [s for s in fresh.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
            and not isinstance(s, ast.ImportFrom)]
    assert ast.unparse(body[0]) == "set_seed(seed)", ast.unparse(body[0])
    assert "core.attach_lora(" in ast.unparse(body[1]) and len(body) == 2
    # And `run` reaches it with the training seed, and attaches no other way.
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run")
    run = ast.unparse(fn)
    assert "attach_fresh_lora(model, args.rank, args.seed, core)" in run
    assert "core.attach_lora(" not in run, "run() attaches a LoRA without the reseed"


def test_the_smoke_steps_the_optimizer_train_sft_steps():
    """One SFT optimiser definition: the smoke used (0.9, 0.95) / 0.1, SFT did not."""
    import ast
    stages_spec = importlib.util.spec_from_file_location(
        "recipe_stages", ROOT / "gpu/verified_stages.py")
    stages = importlib.util.module_from_spec(stages_spec)
    stages_spec.loader.exec_module(stages)
    # The live values, kept so the arm does not drift.
    assert stages.SFT_OPTIMIZER == {"betas": (0.9, 0.999), "eps": 1e-8, "weight_decay": 0.01}
    assert stages.MAX_GRAD_NORM == 1.0
    lora = (ROOT / "gpu/lypning_lora.py").read_text(encoding="utf-8")
    smoke = next(n for n in ast.parse(lora).body
                 if isinstance(n, ast.FunctionDef) and n.name == "smoke")
    text = ast.unparse(ast.Module(body=smoke.body[1:], type_ignores=[]))   # code, not docstring
    assert "**args.optimizer" in text and "0.95" not in text and "weight_decay=0.1" not in text
    source = (ROOT / "gpu/train_verified.py").read_text(encoding="utf-8")
    assert "optimizer=SFT_OPTIMIZER" in source


def test_two_seeds_draw_two_lora_initialisations_and_one_seed_draws_one():
    """The replicate claim itself, on a tiny module; needs the GPU deps, so may skip."""
    torch = pytest.importorskip("torch")
    peft = pytest.importorskip("peft")
    transformers = pytest.importorskip("transformers")

    def lora_a(seed):
        torch.manual_seed(0)
        base = torch.nn.Sequential(torch.nn.Linear(8, 8))
        torch.manual_seed(0)
        torch.randn(100)                       # the smoke's fixed consumption
        transformers.set_seed(seed)
        model = peft.get_peft_model(base, peft.LoraConfig(r=4, lora_alpha=8, target_modules=["0"]))
        return next(p.detach().clone() for n, p in model.named_parameters() if "lora_A" in n)

    assert torch.equal(lora_a(1111), lora_a(1111))
    assert not torch.equal(lora_a(1111), lora_a(2222))
