"""The full rung's shards: one helper picks them, and each one must fit its caps.

A merge of shards is only a merge if generation, the reference check and the
grader picked the same cases, and the reuse of the 192-case `targets` run as
the first shard is only sound if the full order really begins with it. Both
are properties of `shard_cases` that can be decided here, for free.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

from pipeline import positive_control as pc
from pipeline.training_types import TrainingError

SCRIPTS = Path(__file__).resolve().parents[2] / ".github" / "scripts"
PRIVATE = "PRIVATE_CASE_ID_"


def load(name):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def train(sizes=(1, 3, 5, 8, 13, 21, 34)):
    return [{"case_id": "%s%d-%d" % (PRIVATE, f, i), "family": "fam-%d" % f, "split": "train"}
            for f, n in enumerate(sizes) for i in range(n)]


def ids(cases):
    return [c["case_id"] for c in cases]


def test_the_full_order_begins_with_every_smaller_rung():
    """Why run 35767396604's 192 cases can be the full rung's first shard."""
    cases = train()
    whole = ids(pc.stratified_population(cases, len(cases)))
    for prefix in (7, 20, 42, len(cases) - 1):
        assert ids(pc.stratified_population(cases, prefix)) == whole[:prefix]


def test_shards_partition_the_rest_and_the_defaults_change_nothing():
    cases = train()
    total = len(cases)
    assert ids(pc.shard_cases(cases, 40)) == ids(pc.stratified_population(cases, 40))
    whole = ids(pc.stratified_population(cases, total))
    for count in (1, 2, 3, 4):
        shards = [ids(pc.shard_cases(cases, total, shard_index=i, shard_count=count, skip_prefix=20))
                  for i in range(count)]
        flat = [case for shard in shards for case in shard]
        assert len(flat) == len(set(flat)) == total - 20
        assert set(flat) == set(whole[20:])
        # Interleaved: every shard reaches into the family rounds at both ends.
        assert all(shard[0] in whole[20:20 + count] for shard in shards)


@pytest.mark.parametrize("kwargs", [
    dict(shard_index=2, shard_count=2), dict(shard_count=0), dict(shard_index=-1, shard_count=2),
    dict(skip_prefix=85), dict(shard_index=True, shard_count=2), dict(skip_prefix="192"),
    dict(skip_prefix=83, shard_index=2, shard_count=3),
])
def test_a_shard_that_cannot_be_drawn_is_refused(kwargs):
    with pytest.raises(TrainingError):
        pc.shard_cases(train(), 85, **kwargs)


def test_the_environment_is_the_only_input_and_is_strict():
    shard = load("step2_shard")
    assert shard.shard_from_env({}) == {"shard_index": 0, "shard_count": 1, "skip_prefix": 0}
    assert shard.shard_from_env({"STEP2_SHARD_INDEX": "1", "STEP2_SHARD_COUNT": "2",
                                 "STEP2_SKIP_PREFIX": "192"}) == {
        "shard_index": 1, "shard_count": 2, "skip_prefix": 192}
    for bad in ({"STEP2_SHARD_INDEX": "2", "STEP2_SHARD_COUNT": "2"},
                {"STEP2_SHARD_COUNT": "0"}, {"STEP2_SKIP_PREFIX": "-1"},
                {"STEP2_SKIP_PREFIX": "1/../x"}, {"STEP2_SHARD_INDEX": " 1"}):
        with pytest.raises(shard.ShardError):
            shard.shard_from_env(bad)
    assert shard.shard_size(1355, 0, 2, 192) == 582
    assert shard.shard_size(1355, 1, 2, 192) == 581
    assert shard.shard_size(1355) == 1355


def test_the_scripts_select_through_the_one_helper(monkeypatch):
    shard = load("step2_shard")
    cases = train()
    monkeypatch.setattr(pc, "population", lambda rows: rows)
    got = shard.cases_from_env(cases, {"STEP2_CASES": str(len(cases)), "STEP2_SHARD_INDEX": "1",
                                       "STEP2_SHARD_COUNT": "3", "STEP2_SKIP_PREFIX": "10"})
    assert ids(got) == ids(pc.stratified_population(cases, len(cases)))[10:][1::3]
    for name in ("step2_generate", "step2_reference_check", "step2_grade"):
        text = (SCRIPTS / (name + ".py")).read_text()
        assert "cases_from_env(rows" in text, name
        assert "stratified_population" not in text, name


def env(**kw):
    base = {"STEP2_RUNG": "full", "STEP2_CASES": "1355", "STEP2_SAMPLES": "4",
            "STEP2_MAX_SECONDS": "14400", "STEP2_CEILING_USD": "14",
            "STEP2_SHARD_INDEX": "0", "STEP2_SHARD_COUNT": "2", "STEP2_SKIP_PREFIX": "192"}
    base.update(kw)
    return base


def test_two_shards_fit_and_are_priced_from_the_measured_run(capsys):
    shard = load("step2_shard")
    assert shard.main(env()) == 0
    plan = json.loads(capsys.readouterr().out)
    # 582 cases x 4 draws x 2 arms at $3.63061517 / 1,536 (run 35767396604).
    assert plan["cases"] == 582 and plan["requests"] == 4656
    assert plan["projected_usd"] == 11.01
    assert plan["ceiling_needed_usd"] <= 14
    assert plan["generation_minutes"] == pytest.approx(106.3)
    assert plan["grade_minutes"] == pytest.approx(194.0)
    assert PRIVATE not in json.dumps(plan)


@pytest.mark.parametrize("override, reason", [
    (dict(STEP2_CEILING_USD="12"), "ceiling"),
    (dict(STEP2_SHARD_COUNT="1"), "grading needs"),
    (dict(STEP2_SKIP_PREFIX="0"), "ceiling"),
    (dict(STEP2_MAX_SECONDS="3600"), "generation needs"),
])
def test_a_shard_that_would_stop_half_way_is_refused_before_it_is_paid(override, reason, capsys):
    shard = load("step2_shard")
    assert shard.main(env(**override)) == 1
    assert reason in capsys.readouterr().err


@pytest.mark.parametrize("override", [
    dict(STEP2_CASES="1000"), dict(STEP2_RUNG="targets", STEP2_CASES="192"),
    dict(STEP2_SHARD_INDEX="3"), dict(STEP2_SKIP_PREFIX="1355"),
])
def test_only_the_full_split_is_sharded(override, capsys):
    shard = load("step2_shard")
    assert shard.main(env(**override)) == 1
    assert "refused" in capsys.readouterr().err


def test_unsharded_rungs_are_projected_but_keep_their_own_ceilings(capsys):
    shard = load("step2_shard")
    unsharded = dict(STEP2_RUNG="confirmatory", STEP2_CASES="300", STEP2_SAMPLES="16",
                     STEP2_SHARD_INDEX="0", STEP2_SHARD_COUNT="1", STEP2_SKIP_PREFIX="0",
                     STEP2_CEILING_USD="1")
    assert shard.main(env(**unsharded)) == 0
    assert json.loads(capsys.readouterr().out)["requests"] == 9600


def test_the_grader_refuses_a_shard_other_than_the_one_generated(tmp_path):
    grade = load("step2_grade")
    shard = {"shard_index": 1, "shard_count": 2, "skip_prefix": 192}
    grade.recorded_shard(tmp_path, 192, {"shard_index": 0, "shard_count": 1, "skip_prefix": 0})
    with pytest.raises(TrainingError, match="differ"):
        grade.recorded_shard(tmp_path, 1355, shard)
    (tmp_path / "shard.json").write_text(json.dumps(dict(shard, cases=1355)))
    grade.recorded_shard(tmp_path, 1355, shard)
    with pytest.raises(TrainingError, match="differ"):
        grade.recorded_shard(tmp_path, 1355, dict(shard, shard_index=0))


def test_generation_records_its_shard_beside_the_manifest(tmp_path):
    generate = load("step2_generate")
    generate.write_shard(tmp_path / "absent", 1355, {"shard_index": 0})
    assert not (tmp_path / "absent").exists()
    shard = {"shard_index": 1, "shard_count": 2, "skip_prefix": 192}
    generate.write_shard(tmp_path, 1355, shard)
    assert json.loads((tmp_path / "shard.json").read_text()) == dict(shard, cases=1355)


def test_the_grader_refuses_an_image_recipe_or_base_other_than_generations():
    """The verdicts come from the grade job's rebuilt image, not generation's."""
    grade = load("step2_grade")
    blobs = {"a" * 40: {"training/pipeline/sandbox.py": "blob-1"},
             "b" * 40: {"training/pipeline/sandbox.py": "blob-1"},
             "c" * 40: {"training/pipeline/sandbox.py": "blob-2"}}
    admission = {"source_commit": "a" * 40, "conformance": {"base_image": "base@sha256:1"}}
    assert grade.admitted_recipe(admission, "base@sha256:1", "b" * 40, blobs.__getitem__) == blobs["b" * 40]
    with pytest.raises(TrainingError, match="recipe differs"):
        grade.admitted_recipe(admission, "base@sha256:1", "c" * 40, blobs.__getitem__)
    with pytest.raises(TrainingError, match="base image differs"):
        grade.admitted_recipe(admission, "base@sha256:2", "b" * 40, blobs.__getitem__)
    with pytest.raises(TrainingError, match="lacks runtime lineage"):
        grade.admitted_recipe({"conformance": {}}, "base@sha256:1", "b" * 40, blobs.__getitem__)
    # It is called before the container runner exists, and the grade job has
    # the history the recipe is read from.
    text = (SCRIPTS / "step2_grade.py").read_text()
    assert text.index("admitted_recipe(admission,") < text.index("ContainerRunner(os.environ")
    workflow = (SCRIPTS.parent / "workflows" / "step2-control-grade.yml").read_text()
    assert "fetch-depth: 0" in workflow


def test_the_recipe_is_read_from_this_checkouts_history(monkeypatch):
    """git_recipe against the real repository: HEAD's blobs, and a bad commit refused."""
    import subprocess
    merge = load("step2_merge")
    root = SCRIPTS.parents[1]
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                          text=True)
    if head.returncode != 0:
        pytest.skip("not a git checkout")
    monkeypatch.chdir(root)
    recipe = merge.git_recipe(head.stdout.strip())
    assert sorted(recipe) == sorted(merge.RECIPE)
    assert all(len(blob) == 40 for blob in recipe.values())
    with pytest.raises(merge.MergeError):
        merge.git_recipe("HEAD")
