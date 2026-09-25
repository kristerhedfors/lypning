"""A finish job evaluates the pilot's own adapter on the pilot's bundles, or refuses.

`round02_finish.sh` runs steps 7f and 7g of a pilot job that completed SFT and
then died, from the adapter that pilot saved (seed 1111's arm A, HF job
6ab52a686b030d633f68e503, 2026-09-24). Everything that could make that second
job measure something else -- a rebuilt Space, a moved Qwen pin, a verifier
the bundles do not recognise, another seed, split, chunking or density, a
swapped bundle or adapter, a checkpoint nobody registered -- is compared
before a weight load, and each refusal names its field. Newer evaluation code
is allowed, and recorded. The job script is read as text: its 7f and 7g are
the pilot's, command for command.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from pipeline.jsonio import sha256_of
from pipeline.training_contract import seal_adapter, source_identity
from pipeline.training_metrics import SELECTION_RULE_V2

ROOT = Path(__file__).resolve().parents[2]
JOB = "6ab52a686b030d633f68e503"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


finish = load("finish_lineage_under_test", ROOT / "training" / "hf" / "finish_lineage.py")
ENGINE = {"engine": "lypning-l", "sha256": "e" * 64, "verifier_sha256": "v" * 64}
EXECUTION = {"kind": "hf-sandbox-pool", "image": "hf.co/spaces/o/verifier", "revision": "a" * 40}


def bundle(purpose):
    body = {"purpose": purpose, "identity": dict(ENGINE), "cases": [], "seed": 1111,
            "execution": dict(EXECUTION)}
    return dict(body, digest=sha256_of(body))


def fixture():
    pilot, eval2 = bundle("pilot"), bundle("benchmark")
    manifest = {"job": JOB, "status": "failed", "last_stage": "test", "steps": 1050, "grpo_steps": 0,
                "sft_selected_step": 350, "pilot_bundle_digest": pilot["digest"],
                "eval2_bundle_digest": eval2["digest"], "sft_sha256": "t" * 64,
                "space": "o/verifier", "space_revision": "a" * 40, "qwen_revision": "b" * 40,
                "seed": 1111, "split_seed": 1111, "eval_draws": 16, "eval_sequences": 256,
                "pool_sandboxes_per_host": 4, "bank_path": "banks/v3-20260920b", "commit": "c" * 40}
    best = {"step": 350, "observed": [{"step": s} for s in (350, 700, 1050)]}
    experiment = {"stage": "sft", "checkpoint_step": 1050, "bundle_digest": pilot["digest"],
                  "revision": "b" * 40, "seed": 1111,
                  "sft_targets": {"sft_sha256": "t" * 64, "lineage": {"engine_sha256": "e" * 64}}}
    here = {"space": "o/verifier", "space_head": "a" * 40, "qwen_revision": "b" * 40,
            "engine_identity": dict(ENGINE), "verifier_sha256": "v" * 64, "seed": 1111,
            "split_seed": 1111, "eval_draws": 16, "eval_sequences": 256, "pool_sandboxes_per_host": 4,
            "commit_descends": True}
    return dict(manifest=manifest, best=best, adapter_experiment=experiment, seal_ok=True,
                pilot_bundle=pilot, pilot_digest=pilot["digest"], eval2_bundle=eval2,
                eval2_digest=eval2["digest"], here=here, job=JOB, step=1050)


def fields(found):
    return {line.split(":", 1)[0] for line in found}


def test_the_seed_1111_finish_at_the_registered_step_has_no_problems():
    assert finish.problems(**fixture()) == []
    override = finish.OVERRIDES[JOB]
    assert (override["step"], override["date"]) == (1050, "2026-09-25")
    assert "EVAL2.md" in override["amendment"] and "fragile" in override["reason"]


def test_the_rules_own_step_needs_no_override_and_an_unregistered_one_is_refused():
    f = fixture()
    f.update(step=700)
    f["adapter_experiment"]["checkpoint_step"] = 700
    assert fields(finish.problems(**f)) == {"selection_override"}
    f.update(step=350)
    f["adapter_experiment"]["checkpoint_step"] = 350
    assert finish.problems(**f) == []


@pytest.mark.parametrize("field, mutate", [
    ("job", lambda f: f.update(job="0" * 24)),
    ("job", lambda f: f.update(job="round-02/" + JOB)),
    ("sft_completed", lambda f: f["manifest"].update(last_stage="sft")),
    ("sft_completed", lambda f: f["manifest"].update(last_stage="prepare")),
    ("sft_completed", lambda f: f["best"].update(observed=[{"step": 350}, {"step": 700}])),
    ("grpo_steps", lambda f: f["manifest"].update(grpo_steps=20)),
    ("selected_step", lambda f: f.update(step=1000)),
    ("selected_step", lambda f: f["adapter_experiment"].update(checkpoint_step=700)),
    ("selected_step", lambda f: f["adapter_experiment"].update(stage="grpo")),
    ("seal", lambda f: f.update(seal_ok=False)),
    ("pilot_bundle_digest", lambda f: f.update(pilot_digest="0" * 64)),
    ("pilot_bundle_digest", lambda f: f["manifest"].update(pilot_bundle_digest="0" * 64)),
    ("pilot_bundle_digest", lambda f: f["adapter_experiment"].update(bundle_digest="x" * 64)),
    ("eval2_bundle_digest", lambda f: f.update(eval2_digest="0" * 64)),
    ("eval2_bundle_digest", lambda f: f["manifest"].update(eval2_bundle_digest="0" * 64)),
    ("sft_sha256", lambda f: f["manifest"].update(sft_sha256="u" * 64)),
    ("verifier_sha256", lambda f: f.update(here=dict(f["here"], verifier_sha256="w" * 64))),
    ("engine", lambda f: f["here"]["engine_identity"].update(sha256="f" * 64)),
    ("engine", lambda f: f["adapter_experiment"]["sft_targets"]["lineage"].update(engine_sha256="f" * 64)),
    ("space", lambda f: f["here"].update(space="o/other")),
    ("space_revision", lambda f: f["here"].update(space_head="d" * 40)),
    ("space_revision", lambda f: f["manifest"].update(space_revision="d" * 40)),
    ("qwen_revision", lambda f: f["here"].update(qwen_revision="d" * 40)),
    ("qwen_revision", lambda f: f["adapter_experiment"].update(revision="d" * 40)),
    ("seed", lambda f: f["here"].update(seed=2222)),
    ("seed", lambda f: f["adapter_experiment"].update(seed=2222)),
    ("split_seed", lambda f: f["here"].update(split_seed=2222)),
    ("eval_draws", lambda f: f["here"].update(eval_draws=4)),
    ("eval_sequences", lambda f: f["here"].update(eval_sequences=128)),
    ("pool_sandboxes_per_host", lambda f: f["here"].update(pool_sandboxes_per_host=2)),
    ("commit", lambda f: f["here"].update(commit_descends=False)),
])
def test_each_identity_refusal_names_its_field(field, mutate):
    f = fixture()
    mutate(f)
    found = finish.problems(**f)
    assert field in fields(found), found


def test_newer_evaluation_code_is_allowed_where_a_split_eval2_would_refuse_it():
    """The adapter's `code_sha256` is not an input to `problems` at all: the
    finish runs #126's mismatch counting by design, and `main` records both."""
    import inspect
    assert "code_sha256" not in inspect.signature(finish.problems).parameters
    assert finish.problems(**fixture()) == []


def test_the_pair_fields_are_the_ones_arm_check_joins_on():
    arm = load("arm_check_finish_pair", ROOT / ".github" / "scripts" / "arm_check.py")
    split = load("split_eval2_finish_pair", ROOT / "training" / "hf" / "split_eval2.py")
    assert finish.PAIR_FIELDS == arm.PAIR_FIELDS == split.PAIR_FIELDS


def test_the_post_sft_stages_are_stages_the_pilot_script_sets_after_sft():
    pilot = (ROOT / "training" / "hf" / "round02_pilot.sh").read_text(encoding="utf-8")
    after = pilot[pilot.index("STAGE=sft\n"):]
    for stage in finish.POST_SFT_STAGES:
        assert "STAGE=%s\n" % stage in after, stage
    assert "sft" not in finish.POST_SFT_STAGES


def adapter_dir(root, step, experiment):
    d = root / "sft" / ("adapter-%d" % step)
    d.mkdir(parents=True)
    (d / "adapter_model.safetensors").write_bytes(b"weights")
    (d / "adapter_config.json").write_text("{}")
    (d / "experiment.json").write_text(json.dumps(experiment))
    (d / "seal.json").write_text(json.dumps(seal_adapter(d)))
    return d


def dev_rows(step, native_every):
    """A tiny SFT dev evaluation: two coverage families and a control, 4 draws a case."""
    rows = []
    for family, population, cases in (("a", "coverage", 6), ("b", "coverage", 6),
                                      ("c", "fallback-control", 6)):
        for i in range(cases):
            for d in range(4):
                native = population == "coverage" and (i * 4 + d) % native_every == 0
                rows.append(dict(step=step, case_id="SECRET-%s-%d" % (family, i), draw=d, family=family,
                                 population=population, correct=True, native=native))
    return rows


def staged_pilot(tmp_path):
    f = fixture()
    pilot = tmp_path / "pilot"
    for name, key in (("pilot", "pilot_bundle"), ("eval2", "eval2_bundle")):
        (pilot / name).mkdir(parents=True)
        (pilot / name / "bundle.json").write_text(json.dumps(f[key]))
    (pilot / "job-manifest.json").write_text(json.dumps(f["manifest"]))
    code = dict(source_identity(ROOT / "training"), **{"pipeline/training_metrics.py": "0" * 64})
    adapter = adapter_dir(pilot, 1050, dict(f["adapter_experiment"], code_sha256=code))
    (pilot / "sft" / "best.json").write_text(json.dumps(f["best"]))
    rows = dev_rows(0, 4) + dev_rows(350, 3) + dev_rows(700, 2) + dev_rows(1050, 1)
    (pilot / "sft" / "evaluations.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return pilot, adapter


def environment(monkeypatch):
    from pipeline import training
    monkeypatch.setattr(training, "engine_identity", lambda path: dict(ENGINE))
    monkeypatch.setattr(training, "verifier_sha256", lambda: "v" * 64)
    monkeypatch.setattr(finish, "git_lineage", lambda commit: (
        True, "f" * 40, [{"sha": "1" * 40, "subject": "Count an engine-mismatch draw"}]))
    for key, value in (("SPACE_REPO", "o/verifier"), ("SPACE_HEAD", "a" * 40), ("QWEN_REV", "b" * 40),
                       ("SEED", "1111"), ("SPLIT_SEED", "1111"), ("EVAL_DRAWS", "16"),
                       ("EVAL_SEQUENCES", "256"), ("NTX_POOL_SANDBOXES_PER_HOST", "4")):
        monkeypatch.setenv(key, value)


def test_main_stages_a_verified_finish_records_its_lineage_and_prints_aggregates_only(
        tmp_path, monkeypatch, capsys):
    pilot, adapter = staged_pilot(tmp_path)
    environment(monkeypatch)
    round_dir = tmp_path / "round"
    round_dir.mkdir()
    argv = ["--pilot", str(pilot), "--job", JOB, "--step", "1050", "--engine", "/unused", "--round"]
    assert finish.main(argv + [str(round_dir)]) == 0
    lineage = json.loads((round_dir / "lineage.json").read_text())
    assert (lineage["finish_of"], lineage["selected_step"], lineage["rule_selected_step"]) == (JOB, 1050, 350)
    assert lineage["rule_version"] == finish.LEGACY_RULE, "a pre-versioned best.json is rule v1's"
    override = lineage["selection_override"]
    assert (override["step"], override["date"], override["rule_selected_step"]) == (1050, "2026-09-25", 350)
    moved = lineage["finish_lineage"]
    assert moved["code_sha256_moved"] == ["pipeline/training_metrics.py"]
    assert moved["pilot_code_sha256_digest"] != moved["finish_code_sha256_digest"]
    assert moved["commits"][0]["subject"].startswith("Count")
    assert lineage["pair"]["space_revision"] == "a" * 40 and lineage["pair"]["split_seed"] == 1111
    # The corrected rule, re-read over the pilot's own dev draws: steps only.
    assert lineage["reselection"]["rule"] == SELECTION_RULE_V2
    assert lineage["reselection"]["selected_step"] == 1050
    for name in ("pilot/bundle.json", "eval2/bundle.json", "sft/adapter-1050/seal.json", "sft/best.json"):
        assert (round_dir / name).is_file(), name
    out = capsys.readouterr().out
    assert "finish lineage verified" in out and "SECRET" not in out

    # One byte of the sealed adapter moves: refused on the seal, nothing staged.
    (adapter / "adapter_model.safetensors").write_bytes(b"weightz")
    other = tmp_path / "round2"
    other.mkdir()
    assert finish.main(argv + [str(other)]) == 1
    assert "FINISH LINEAGE REFUSED: seal:" in capsys.readouterr().out
    assert not (other / "lineage.json").exists() and not (other / "sft").exists()


def test_the_reselection_is_evidence_and_never_stops_the_finish():
    rows = dev_rows(0, 4) + dev_rows(1050, 1)
    got = finish.reselect(rows, SELECTION_RULE_V2)
    assert got["selected_step"] == 1050 and set(got["observed"][0]) == {
        "step", "delta", "standard_error", "margin", "rejected_for"}
    assert finish.reselect(dev_rows(350, 1), SELECTION_RULE_V2) is None, "no step 0, no baseline"
    broken = rows + [dict(rows[-1], draw=99)]          # unequal draws per case
    assert finish.reselect(broken, SELECTION_RULE_V2) == {
        "rule": SELECTION_RULE_V2, "selected_step": None, "unreadable": "TrainingError"}


@pytest.mark.parametrize("job, step", [("round-02/" + JOB, "1050"), (JOB + "\n", "1050"),
                                       (JOB, "0"), (JOB, "-350"), (JOB, "1050\n")])
def test_main_refuses_a_malformed_job_or_step_before_reading_anything(tmp_path, job, step, capsys):
    assert finish.main(["--pilot", str(tmp_path / "absent"), "--job", job, "--step", step,
                        "--engine", "/unused", "--round", str(tmp_path)]) == 2
    assert "finish refused" in capsys.readouterr().out


SCRIPT = (ROOT / "training" / "hf" / "round02_finish.sh").read_text(encoding="utf-8")
PILOT = (ROOT / "training" / "hf" / "round02_pilot.sh").read_text(encoding="utf-8")


def command(text, output):
    return next(line.strip() for line in text.splitlines() if '--output "$ROUND/%s"' % output in line)


def test_the_finish_runs_the_pilots_7f_and_7g_command_for_command():
    for output in ("base-test", "sft-test", "base-eval2", "sft-eval2"):
        assert command(SCRIPT, output) == command(PILOT, output), output
    assert "--reuse-evaluation \"$ROUND/base-eval2\"" in command(SCRIPT, "sft-eval2")
    assert "--eval-draws" not in command(SCRIPT, "base-test"), "7f runs at the trainer's default"
    common = re.search(r"COMMON=\(([^)]*)\)", SCRIPT).group(1)
    assert " ".join(common.split()) == " ".join(re.search(r"COMMON=\(([^)]*)\)", PILOT).group(1).split())
    assert "grpo" not in SCRIPT[SCRIPT.index("TV=("):].lower().replace("grpo arm", ""), \
        "arm A: no third arm"
    for report in ("report base-vs-sft-test", "report base-vs-sft-eval2"):
        assert report in SCRIPT, report


def test_every_identity_check_precedes_the_handshake_and_every_weight_load():
    lineage = SCRIPT.index("training/hf/finish_lineage.py")
    assert SCRIPT.index("bash training/hf/pinned_deps.sh") < SCRIPT.index("STAGE=pilot-download") < lineage
    assert lineage < SCRIPT.index("STAGE=handshake") < SCRIPT.index('--output "$ROUND/base-test"')
    # The engine comes from the PILOT's revision, never the dispatch's head.
    engine = SCRIPT[SCRIPT.index("STAGE=engine"):SCRIPT.index("STAGE=lineage")]
    assert 'revision = os.environ["SPACE_REPO"], os.environ["PILOT_SPACE_REV"]' in engine
    assert "no longer fetchable" in engine and 'os.environ["SPACE_REV"]' not in engine
    assert engine.index("hf_hub_download") < engine.index('export SPACE_REV="$PILOT_SPACE_REV"')
    for pattern, name in ((r'\[\[ "\$FINISH_OF" =~ (\S+) \]\]', "^[0-9a-f]{24}$"),
                          (r'\[\[ "\$SFT_STEP" =~ (\S+) \]\]', "^[1-9][0-9]*$")):
        check = re.search(pattern, SCRIPT)
        assert check and check.group(1) == name
        assert check.start() < SCRIPT.index("trap 'finish $?' EXIT")


def test_the_finish_downloads_from_the_private_repo_only_and_uploads_what_it_made():
    download = SCRIPT[SCRIPT.index("STAGE=pilot-download"):SCRIPT.index("STAGE=engine")]
    assert "info.private is not True" in download and "revision=info.sha" in download
    for pattern in ('src + "/job-manifest.json"', 'src + "/sft/best.json"', '"%s/sft/adapter-%d/**"',
                    'src + "/pilot/bundle.json"', 'src + "/eval2/bundle.json"'):
        assert pattern in download, pattern
    assert "bank" not in download, "a finish prepares nothing"
    assert SCRIPT.count('UPLOAD_IGNORE = ["pilot-download/**", "pilot/**", "eval2/**", "sft/**"]') == 2
    # A checkpoint after the lineage and after every GPU stage.
    for stage in ("lineage", "base-test", "sft-test", "base-eval2", "sft-eval2", "report"):
        body = SCRIPT[SCRIPT.index("STAGE=%s\n" % stage):]
        assert body.index("checkpoint\n") < body.index("STAGE=", 1), stage


def test_the_finish_manifest_names_the_pilot_the_steps_and_the_override():
    trap = SCRIPT[SCRIPT.index("finish() {"):SCRIPT.index("trap 'finish $?' EXIT")]
    for key in ('"kind": "finish"', '"stage": "finish"', '"finish_of"', '"selected_step"',
                '"rule_selected_step"', '"selection_override"', '"finish_lineage"', '"reselection"',
                '"dispatch_space_revision"', '"test_eval_draws"', 'lineage.get("pair")'):
        assert key in trap, key
