"""A split eval-2 job measures the pilot job's arm, or refuses naming what moved.

`round02_eval2.sh` runs step 7g of a pilot that deferred it, in a second
billed job. Everything that could make that second job measure something else
-- a rebuilt Space, a moved Qwen pin, changed trainer code, another seed or
chunking, a swapped bundle or adapter -- is compared before a draw, and each
refusal names its field. The job script itself is read as text: its 7g must be
the pilot's 7g, command for command.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from pipeline.jsonio import sha256_of
from pipeline.training_contract import seal_adapter, source_identity

ROOT = Path(__file__).resolve().parents[2]
JOB = "6ab01cbb51992417dfccd64c"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


split = load("split_eval2_under_test", ROOT / "training" / "hf" / "split_eval2.py")
ENGINE = {"engine": "lypning-l", "sha256": "e" * 64, "verifier_sha256": "v" * 64}
CODE = {"gpu/train_verified.py": "1" * 64, "pipeline/training.py": "2" * 64}


def bundle():
    body = {"purpose": "benchmark", "identity": dict(ENGINE), "cases": [],
            "execution": {"kind": "hf-sandbox-pool", "image": "hf.co/spaces/o/verifier",
                          "revision": "a" * 40}}
    return dict(body, digest=sha256_of(body))


def fixture():
    b = bundle()
    manifest = {"job": JOB, "status": "complete", "eval2_deferred": True, "sft_selected_step": 350,
                "eval2_bundle_digest": b["digest"], "pilot_bundle_digest": "p" * 64,
                "space": "o/verifier", "space_revision": "a" * 40, "qwen_revision": "b" * 40,
                "seed": 1111, "eval_draws": 16, "eval_sequences": 256, "pool_sandboxes_per_host": 4,
                "bank_path": "banks/v3-20260920b", "split_seed": 1111, "commit": "c" * 40}
    deferred = {"sft_selected_step": 350, "eval2_bundle_digest": b["digest"]}
    best = {"step": 350}
    experiment = {"checkpoint_step": 350, "bundle_digest": "p" * 64, "revision": "b" * 40,
                  "code_sha256": dict(CODE), "seed": 1111}
    here = {"space": "o/verifier", "space_revision": "a" * 40, "qwen_revision": "b" * 40,
            "engine_identity": dict(ENGINE), "code_sha256": dict(CODE), "seed": 1111,
            "eval_draws": 16, "eval_sequences": 256, "pool_sandboxes_per_host": 4}
    return dict(manifest=manifest, deferred=deferred, best=best, adapter_experiment=experiment,
                seal_ok=True, eval2_bundle=b, eval2_digest=b["digest"], here=here, job=JOB)


def fields(found):
    return {line.split(":", 1)[0] for line in found}


def test_a_faithful_pair_has_no_problems():
    assert split.problems(**fixture()) == []


@pytest.mark.parametrize("field, mutate", [
    ("status", lambda f: f["manifest"].update(status="failed")),
    ("eval2_deferred", lambda f: f["manifest"].update(eval2_deferred=False)),
    ("eval2_deferred", lambda f: f.update(deferred={})),
    ("job", lambda f: f.update(job="0" * 24)),
    ("sft_selected_step", lambda f: f["best"].update(step=700)),
    ("sft_selected_step", lambda f: f["adapter_experiment"].update(checkpoint_step=0)),
    ("seal", lambda f: f.update(seal_ok=False)),
    ("eval2_bundle_digest", lambda f: f.update(eval2_digest="0" * 64)),
    ("eval2_bundle_digest", lambda f: f["manifest"].update(eval2_bundle_digest="0" * 64)),
    ("pilot_bundle_digest", lambda f: f["adapter_experiment"].update(bundle_digest="x" * 64)),
    ("engine", lambda f: f["here"]["engine_identity"].update(sha256="f" * 64)),
    ("space", lambda f: f["here"].update(space="o/other")),
    ("space_revision", lambda f: f["here"].update(space_revision="d" * 40)),
    ("qwen_revision", lambda f: f["here"].update(qwen_revision="d" * 40)),
    ("qwen_revision", lambda f: f["adapter_experiment"].update(revision="d" * 40)),
    ("code_sha256", lambda f: f["here"]["code_sha256"].update({"gpu/train_verified.py": "9" * 64})),
    ("seed", lambda f: f["here"].update(seed=2222)),
    ("eval_draws", lambda f: f["here"].update(eval_draws=4)),
    ("eval_sequences", lambda f: f["here"].update(eval_sequences=128)),
    ("pool_sandboxes_per_host", lambda f: f["here"].update(pool_sandboxes_per_host=2)),
])
def test_each_identity_refusal_names_its_field(field, mutate):
    f = fixture()
    mutate(f)
    found = split.problems(**f)
    assert field in fields(found), found


def test_a_moved_engine_and_trainer_name_what_moved():
    f = fixture()
    f["here"]["engine_identity"]["verifier_sha256"] = "w" * 64
    f["here"]["code_sha256"]["pipeline/training.py"] = "3" * 64
    found = split.problems(**f)
    assert any(line.startswith("engine:") and "verifier_sha256" in line for line in found)
    assert any(line.startswith("code_sha256:") and "pipeline/training.py" in line for line in found)


def test_the_pair_fields_are_the_ones_arm_check_joins_on():
    arm = load("arm_check_pair", ROOT / ".github" / "scripts" / "arm_check.py")
    assert split.PAIR_FIELDS == arm.PAIR_FIELDS


def adapter_dir(root, step, experiment):
    d = root / "sft" / ("adapter-%d" % step)
    d.mkdir(parents=True)
    (d / "adapter_model.safetensors").write_bytes(b"weights")
    (d / "adapter_config.json").write_text("{}")
    (d / "experiment.json").write_text(json.dumps(experiment))
    (d / "seal.json").write_text(json.dumps(seal_adapter(d)))
    return d


def test_main_stages_a_verified_pair_and_refuses_a_tampered_adapter(tmp_path, monkeypatch, capsys):
    from pipeline import training

    f = fixture()
    pilot = tmp_path / "pilot"
    (pilot / "eval2").mkdir(parents=True)
    (pilot / "eval2" / "bundle.json").write_text(json.dumps(f["eval2_bundle"]))
    (pilot / "job-manifest.json").write_text(json.dumps(f["manifest"]))
    (pilot / "eval2-deferred.json").write_text(json.dumps(f["deferred"]))
    experiment = dict(f["adapter_experiment"], code_sha256=source_identity(ROOT / "training"))
    adapter = adapter_dir(pilot, 350, experiment)
    (pilot / "sft" / "best.json").write_text(json.dumps(f["best"]))
    monkeypatch.setattr(training, "engine_identity", lambda path: dict(ENGINE))
    for key, value in (("SPACE_REPO", "o/verifier"), ("SPACE_REV", "a" * 40), ("QWEN_REV", "b" * 40),
                       ("SEED", "1111"), ("EVAL_DRAWS", "16"), ("EVAL_SEQUENCES", "256"),
                       ("NTX_POOL_SANDBOXES_PER_HOST", "4")):
        monkeypatch.setenv(key, value)
    argv = ["--pilot", str(pilot), "--job", JOB, "--engine", "/unused", "--round"]

    round_dir = tmp_path / "round"
    round_dir.mkdir()
    assert split.main(argv + [str(round_dir)]) == 0
    lineage = json.loads((round_dir / "lineage.json").read_text())
    assert (lineage["eval2_of"], lineage["sft_selected_step"]) == (JOB, 350)
    assert (round_dir / "sft" / "adapter-350" / "seal.json").is_file()
    assert (round_dir / "eval2" / "bundle.json").is_file()
    assert lineage["pair"]["seed"] == 1111 and lineage["pair"]["bank_path"] == "banks/v3-20260920b"
    out = capsys.readouterr().out
    assert "split lineage verified" in out and "cases" not in out

    # One byte of the sealed adapter moves: refused on the seal, nothing staged.
    (adapter / "adapter_model.safetensors").write_bytes(b"weightz")
    other = tmp_path / "round2"
    other.mkdir()
    assert split.main(argv + [str(other)]) == 1
    assert "SPLIT LINEAGE REFUSED: seal:" in capsys.readouterr().out
    assert not (other / "lineage.json").exists() and not (other / "eval2").exists()

    assert split.main(["--pilot", str(pilot), "--job", "round-02/" + JOB, "--engine", "/unused",
                       "--round", str(other)]) == 2


SCRIPT = (ROOT / "training" / "hf" / "round02_eval2.sh").read_text(encoding="utf-8")
PILOT = (ROOT / "training" / "hf" / "round02_pilot.sh").read_text(encoding="utf-8")


def seven_g(text, output):
    return next(line.strip() for line in text.splitlines() if '--output "$ROUND/%s"' % output in line)


def test_the_split_job_runs_the_pilots_7g_command_for_command():
    for output in ("base-eval2", "sft-eval2"):
        assert seven_g(SCRIPT, output) == seven_g(PILOT, output), output
    assert "--reuse-evaluation \"$ROUND/base-eval2\"" in seven_g(SCRIPT, "sft-eval2")
    common = re.search(r"COMMON=\(([^)]*)\)", SCRIPT).group(1)
    assert " ".join(common.split()) == " ".join(re.search(r"COMMON=\(([^)]*)\)", PILOT).group(1).split())


def test_the_split_job_verifies_lineage_before_any_draw_and_validates_its_input():
    lineage = SCRIPT.index("training/hf/split_eval2.py")
    assert lineage < SCRIPT.index('--output "$ROUND/base-eval2"')
    assert SCRIPT.index("bash training/hf/pinned_deps.sh") < lineage
    check = re.search(r'\[\[ "\$EVAL2_OF" =~ (\S+) \]\]', SCRIPT)
    assert check and check.group(1) == "^[0-9a-f]{24}$"
    assert check.start() < SCRIPT.index("trap 'finish $?' EXIT")
    finish = SCRIPT[SCRIPT.index("finish() {"):SCRIPT.index("trap 'finish $?' EXIT")]
    for key in ('"kind": "eval2"', '"eval2_of"', '"pilot_commit"', 'lineage.get("pair")'):
        assert key in finish, key
    # The pilot's bundle and adapter are its own; this job uploads what it made.
    assert SCRIPT.count('UPLOAD_IGNORE = ["pilot-download/**", "eval2/**", "sft/**"]') == 2


def test_the_pilot_defers_7g_only_in_separate_mode_and_says_so():
    body = PILOT[PILOT.index("# 7g."):PILOT.index("# 8. Paired")]
    assert body.index('if [ "$EVAL2_MODE" = separate ]; then') < body.index('--output "$ROUND/base-eval2"')
    marker = body[body.index("STAGE=eval2-deferred"):body.index("else\nSTAGE=eval2")]
    assert "eval2-deferred.json" in marker and "base-eval2" not in marker
    report = PILOT[PILOT.index("# 8. Paired"):]
    assert 'if [ "$EVAL2_MODE" = same-job ]; then\n  report base-vs-sft-eval2' in report


def test_the_deferral_marker_prints_aggregates_only(tmp_path):
    import os
    import subprocess
    import sys
    start = PILOT.index("STAGE=eval2-deferred")
    body = PILOT[PILOT.index("\n", PILOT.index("python3 - <<'PYEOF'", start)) + 1:]
    body = body[:body.index("\nPYEOF")] + "\n"
    (tmp_path / "work/round-02/eval2").mkdir(parents=True)
    (tmp_path / "work/round-02/eval2/bundle.json").write_text(json.dumps(
        {"digest": "d" * 64, "cases": [{"case_id": "SECRET-CASE", "task": "SECRET TASK"}]}))
    done = subprocess.run([sys.executable, "-c", body], cwd=tmp_path, capture_output=True, text=True,
                          env=dict(os.environ, SFT_STEP="350", PYTHONPATH=str(ROOT / "training")))
    assert done.returncode == 0, done.stderr
    marker = json.loads((tmp_path / "work/round-02/eval2-deferred.json").read_text())
    assert marker["sft_selected_step"] == 350 and marker["eval2_bundle_digest"] == "d" * 64
    assert "SECRET" not in done.stdout
