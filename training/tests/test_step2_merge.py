"""Merging graded shards: one experiment or a refusal naming the shard that is not.

The merge re-runs nothing, so everything it decides is decidable here: that
its aggregation is the grader's own, byte for byte; that shards built from a
different engine, base image, spec, provider or image recipe are refused by
name; that a rebuilt candidate-image id alone is not a difference; and that
nothing it prints carries a case.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from pipeline import positive_control_grade as grade
from pipeline.jsonio import read_jsonl, sha256_of
from pipeline.training_types import Score, TrainingError

SCRIPTS = Path(__file__).resolve().parents[2] / ".github" / "scripts"
PRIVATE = "PRIVATE_CASE_"
FILES = ("rows.jsonl", "report.json", "sft.jsonl", "sft-report.json", "public-report.json")


def load(name):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = load("step2_merge")


def cases():
    out = []
    for i in range(8):
        control = i % 4 == 3
        out.append({"case_id": "%s%d" % (PRIVATE, i), "family": "%sfam-%d" % (PRIVATE, i % 4),
                    "task": "task %d" % i, "split_group": "g%d" % (i % 3),
                    "population": "fallback-control" if control else "coverage",
                    "capabilities": [], "split": "train", "tests": [{}, {}]})
    return out


def completions(rows, samples=2):
    return [{"case_id": c["case_id"], "draw": d, "arm": a, "seed": 1111 + d,
             "completion": "```python\nprint(%r)\n```" % ("%s/%d/%s" % (c["case_id"], d, a)),
             "finish_reason": "stop", "usage": {"completion_tokens": 8 + d}}
            for c in rows for d in range(samples) for a in ("bare", "subset-spec")]


class Verifier:
    """Deterministic, varied verdicts: arms differ, draws differ, controls stay controls."""

    def score(self, case, program):
        n = sum(map(ord, program))
        if case["population"] == "fallback-control":
            return Score(1, "correct-control", 0, 2) if n % 3 else Score(0, "incorrect", 0, 2)
        if n % 4 == 0:
            return Score(0, "incorrect", 0, 2)
        return Score(1, "correct-native", 2, 2) if n % 2 else Score(1, "correct-fallback", 0, 2)


@pytest.fixture
def graded(tmp_path):
    cs = cases()
    comp = completions(cs)
    grade.grade(cs, comp, Verifier(), tmp_path / "graded", samples=2, workers=2,
                run_id="merged", lineage={"engine_sha256": "e" * 64})
    return cs, comp, read_jsonl(tmp_path / "graded" / "rows.jsonl"), tmp_path / "graded"


def test_the_aggregation_is_the_graders_own_byte_for_byte(graded, tmp_path):
    cs, comp, rows, out = graded
    m.aggregate(cs, list(reversed(comp)), list(reversed(rows)), tmp_path / "again", samples=2,
                run_id="merged", lineage={"engine_sha256": "e" * 64}, target_arms=("subset-spec",))
    for name in FILES:
        assert (tmp_path / "again" / name).read_bytes() == (out / name).read_bytes(), name


def admission(rows, **conformance):
    return {"source_commit": "a" * 40, "candidate_image": "sha256:" + "1" * 64,
            "case_fingerprints": {c["case_id"]: sha256_of(c) for c in rows},
            "conformance": dict({"engine_sha256": "e" * 64, "base_image": "base@sha256:" + "b" * 64},
                                **conformance)}


def shards(graded, count=2):
    cs, comp, rows, _ = graded
    out = []
    for i in range(count):
        mine = cs[i::count]
        ids = {c["case_id"] for c in mine}
        out.append({"run": "shard-%d" % i, "admission": admission(mine),
                    "manifest": {"spec_sha256": "s" * 64, "samples": 2,
                                 "sampling": {"temperature": .7, "top_p": .8, "max_tokens": 2048,
                                              "reasoning_effort": "none", "seed": "1111 + draw"},
                                 "provider": {"base_url": "https://api.cerebras.ai/v1", "model": "m"}},
                    "completions": [r for r in comp if r["case_id"] in ids],
                    "rows": [r for r in rows if r["case_id"] in ids]})
    return out


def recipe(commit):
    return {"training/worker/Dockerfile.verifier": "blob-" + commit[:1]}


def test_shards_merge_into_one_run_the_pilot_reads_like_any_other(graded, tmp_path):
    cs, _, _, out = graded
    parts = shards(graded)
    # Two runners, two builds of one recipe, two image ids: still one experiment.
    parts[1]["admission"]["candidate_image"] = "sha256:" + "2" * 64
    parts[1]["admission"]["source_commit"] = "a" + "c" * 39
    public = m.merge(parts, cs, tmp_path / "merged", run_id="merged",
                     target_arms=("subset-spec",), recipe_of=recipe)
    assert public == json.loads((out / "public-report.json").read_text())
    for name in ("rows.jsonl", "report.json", "sft.jsonl"):
        assert (tmp_path / "merged" / name).read_bytes() == (out / name).read_bytes(), name
    report = json.loads((tmp_path / "merged" / "sft-report.json").read_text())
    assert report["run_id"] == "merged"
    assert report["lineage"]["engine_sha256"] == "e" * 64
    assert [s["candidate_image"][-1] for s in report["lineage"]["shards"]] == ["1", "2"]
    assert [s["cases"] for s in report["lineage"]["shards"]] == [4, 4]


@pytest.mark.parametrize("field", ["engine_sha256", "base_image", "spec_sha256", "provider",
                                   "samples", "sampling", "candidate_recipe"])
def test_a_shard_from_another_experiment_is_refused_by_name(graded, tmp_path, field):
    cs = graded[0]
    parts = shards(graded, 3)
    odd = parts[2]
    if field in ("engine_sha256", "base_image"):
        odd["admission"]["conformance"][field] = "moved"
    elif field == "candidate_recipe":
        odd["admission"]["source_commit"] = "f" * 40
    else:
        odd["manifest"][field] = "moved"
    with pytest.raises(m.MergeError) as refused:
        m.merge(parts, cs, tmp_path / "merged", run_id="merged",
                target_arms=("subset-spec",), recipe_of=recipe)
    assert str(refused.value) == "shard shard-2 differs from shard shard-0 in " + field
    assert not (tmp_path / "merged").exists()


def overlap(parts):
    parts[1]["admission"]["case_fingerprints"].update(parts[0]["admission"]["case_fingerprints"])


def missing(parts):
    del parts[1]


def edited(parts):
    key = next(iter(parts[0]["admission"]["case_fingerprints"]))
    parts[0]["admission"]["case_fingerprints"][key] = "0" * 64


def short(parts):
    parts[1]["rows"].pop()


def duplicated(parts):
    parts[0]["completions"][-1] = copy.deepcopy(parts[0]["completions"][0])


@pytest.mark.parametrize("fault, words", [
    (overlap, "overlap"), (missing, "the shards cover 5 of the 8 cases"),
    (edited, "outside the full split, or one since edited"),
    (short, "shard-1 rows do not exactly cover"),
    (duplicated, "shard-0 completions do not exactly cover"),
])
def test_an_incomplete_or_overlapping_set_is_refused_without_a_case(graded, tmp_path, fault, words):
    parts = shards(graded, 3 if fault is missing else 2)
    fault(parts)
    with pytest.raises(m.MergeError) as refused:
        m.merge(parts, graded[0], tmp_path / "merged", run_id="merged",
                target_arms=("subset-spec",), recipe_of=recipe)
    assert words in str(refused.value)
    assert PRIVATE not in str(refused.value)


@pytest.mark.parametrize("text", ["", "one", "a b a", "a ../b", "positive-control/a b"])
def test_run_ids_are_single_segments(text):
    with pytest.raises(m.MergeError):
        m.parse_runs(text)
    assert m.parse_runs("targets-%s-35767396604, full-0of2-%s-1" % ("a" * 40, "b" * 40))


def write_shard(root, part):
    base = root / "positive-control" / part["run"]
    (base / "paid").mkdir(parents=True)
    (base / "grade").mkdir()
    (base / "admission.json").write_text(json.dumps(part["admission"]))
    (base / "paid" / "manifest.json").write_text(json.dumps(part["manifest"]))
    n = len(part["completions"])
    (base / "paid" / "result.json").write_text(json.dumps(
        {"complete": True, "completed": n, "planned": n, "reason": None, "failure_types": []}))
    (base / "paid" / "completions.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in part["completions"]))
    (base / "grade" / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in part["rows"]))


def hub(monkeypatch, root):
    fake = SimpleNamespace(
        HfApi=lambda token: SimpleNamespace(
            whoami=lambda: {"name": "owner"},
            repo_info=lambda *a, **k: SimpleNamespace(private=True, sha="a" * 40)),
        snapshot_download=lambda *a, **k: str(root))
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake)
    monkeypatch.setenv("HF_TOKEN", "token")
    monkeypatch.setenv("STEP2_MERGE_RUNS", "shard-0 shard-1")
    monkeypatch.setenv("STEP2_RUN_ID", "full-merged-x-1")


def test_the_log_carries_counts_and_refusals_never_a_case(graded, tmp_path, monkeypatch, capsys):
    cs = graded[0]
    parts = shards(graded)
    root = tmp_path / "hub"
    for part in parts:
        write_shard(root, part)
    hub(monkeypatch, root)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    (tmp_path / "step2-bank").mkdir()
    (tmp_path / "step2-bank" / "train.jsonl").write_text("")
    monkeypatch.setitem(sys.modules, "step2_grade", SimpleNamespace(
        target_arms=lambda value: ("subset-spec",), token_counter=lambda: (None, None)))
    monkeypatch.setitem(sys.modules, "step2_shard", SimpleNamespace(
        FULL_CASES=len(cs), cases_from_env=lambda bank, environ: cs))
    monkeypatch.setattr(m, "git_recipe", recipe)
    assert m.main() == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["cases"] == len(cs)
    assert PRIVATE not in out.out + out.err

    # A library refusal can quote a case; only its type reaches the log.
    def private_failure(*a, **k):
        raise TrainingError("a passing grade has no program: %s0" % PRIVATE)
    monkeypatch.setattr(m, "merge", private_failure)
    assert m.main() == 1
    out = capsys.readouterr()
    assert "TrainingError" in out.err and PRIVATE not in out.out + out.err


def test_a_shard_missing_its_grade_is_named_not_quoted(graded, tmp_path, monkeypatch, capsys):
    parts = shards(graded)
    root = tmp_path / "hub"
    for part in parts:
        write_shard(root, part)
    (root / "positive-control" / "shard-1" / "grade" / "rows.jsonl").unlink()
    hub(monkeypatch, root)
    assert m.main() == 1
    err = capsys.readouterr().err
    assert "shard shard-1 is missing" in err and PRIVATE not in err


def test_an_incomplete_generation_is_never_merged(graded, tmp_path, monkeypatch, capsys):
    parts = shards(graded)
    root = tmp_path / "hub"
    for part in parts:
        write_shard(root, part)
    result = root / "positive-control" / "shard-0" / "paid" / "result.json"
    result.write_text(json.dumps({"complete": False, "completed": 3, "planned": 16,
                                  "reason": "provider/usage failure", "failure_types": ["x"]}))
    hub(monkeypatch, root)
    assert m.main() == 1
    assert "shard shard-0 generation is absent or incomplete" in capsys.readouterr().err


def test_every_identity_field_is_pinned_by_a_refusal_test():
    """A field added to IDENTITY without a refusal case would be unchecked here."""
    marks = test_a_shard_from_another_experiment_is_refused_by_name.pytestmark
    fields = next(mark.args[1] for mark in marks if mark.name == "parametrize")
    assert tuple(fields) == m.IDENTITY
