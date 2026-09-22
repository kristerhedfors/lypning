"""The free S4 target gate refuses what the billed job would refuse, for nothing.

WHY THIS FILE EXISTS. Three refusals of a rejection-target set used to live
only inside the h200 job: a target case outside the bundle's train split
(seeds 2222 and 3333, whose split followed their training seed), a target set
graded by another engine (the CI check compared the report with itself), and a
curriculum far smaller than the bundle behind it (54 cases passing a 1,000-case
floor). Each is decidable from the bank, the targets and the Space's engine.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".github" / "scripts"))

from pipeline.jsonio import sha256_of                                    # noqa: E402
from pipeline.training import messages                                   # noqa: E402
from pipeline.training_contract import PROTOCOL_TRAIN_SEEDS              # noqa: E402
from pipeline.training_types import TrainingError                        # noqa: E402

ENGINE = "e" * 64
RUN = "targets-" + "a" * 40 + "-1"


def load():
    spec = importlib.util.spec_from_file_location(
        "s4_target_floor", ROOT / ".github" / "scripts" / "s4_target_floor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tag(family):
    return 1000 + sum(ord(ch) for ch in family)


def case(family, population, n):
    """A schema-3 case `validate_cases` admits; structurally unique per family."""
    return {
        "case_id": "%s-%02d" % (family, n),
        "family": family, "source_group": family, "capabilities": [family.split("-")[0]],
        "task": "Task %s %d: read an integer from stdin and print a derived value." % (family, n),
        "reference": "import sys\nprint(int(sys.stdin.read() or 0) + %d + %d)" % (n, _tag(family)),
        "population": population,
        "tests": [{"stdin": str(i), "stdout": "%d\n" % (i + n + _tag(family))} for i in (1, 2, 3)],
        "provenance": "authored for test_s4_target_floor",
        "review": {"origin": "authored", "evidence_ids": [], "reviewer": "test",
                   "intent_basis": "t", "oracle_basis": "t", "rights_basis": "t",
                   "independence_basis": "one family per construct"},
    }


def bank():
    cases = []
    for i in range(30):
        cases += [case("cov-%02d" % i, "coverage", n) for n in range(4)]
    for i in range(10):
        cases += [case("ctl-%02d" % i, "fallback-control", n) for n in range(4)]
    return cases


def write_targets(directory, cases, engine=ENGINE):
    """Graded targets over `cases`, shaped as `positive_control_targets` writes them."""
    rows = []
    for c in cases:
        program = c["reference"]
        rows.append({"case_id": c["case_id"], "family": c["family"], "population": c["population"],
                     "messages": messages(c) + [{"role": "assistant",
                                                 "content": "```python\n%s\n```" % program}],
                     "source": {"run_id": RUN, "arm": "subset-spec", "draw": 0,
                                "program_sha256": hashlib.sha256(program.encode()).hexdigest()}})
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "sft.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (directory / "sft-report.json").write_text(json.dumps({
        "schema": 1, "run_id": RUN, "rows": len(rows), "sft_sha256": sha256_of(rows),
        "lineage": {"engine_sha256": engine}}))
    return rows


def test_every_training_seed_accepts_the_targets_graded_on_the_one_split(tmp_path):
    """The decoupled split: one target set, admissible at 1111, 2222 and 3333.

    Targets were graded on the train split at split seed 1111. While the split
    followed the training seed, seed 2222's bundle put some of those cases in
    dev or test and its trainer refused them at the plan stage, billed.
    """
    floor = load()
    raw = bank()
    split = floor.assigned_split(raw, floor.PROTOCOL_SPLIT_SEED)
    graded_on = [c for c in split if c["split"] == "train"]
    write_targets(tmp_path, graded_on)
    for seed in PROTOCOL_TRAIN_SEEDS:
        # The split seed is fixed; the training seed only orders the schedule.
        cases, rows, _ = floor.admitted_targets(
            tmp_path, floor.assigned_split(raw, floor.PROTOCOL_SPLIT_SEED), ENGINE)
        assert len(rows) == len(graded_on)
        examples = [{"labels": [-100, 1, 2]} for _ in rows]
        report = floor.floor_report(cases, rows, examples, 10, 4, seed)
        assert report["examples"] == len(graded_on)
    # The coupled form -- split at the training seed -- is the refusal that
    # cost a billed job, at some protocol seed other than the grading one.
    refused = 0
    for seed in PROTOCOL_TRAIN_SEEDS:
        if seed == floor.PROTOCOL_SPLIT_SEED:
            continue
        try:
            floor.admitted_targets(tmp_path, floor.assigned_split(raw, seed), ENGINE)
        except TrainingError:
            refused += 1
    assert refused, "the fixture must show the coupling bug, or it proves nothing"


def test_targets_graded_by_another_engine_are_refused_by_name(tmp_path):
    """Compared against the engine the pilot runs, never the report's own sha."""
    floor = load()
    split = floor.assigned_split(bank(), floor.PROTOCOL_SPLIT_SEED)
    write_targets(tmp_path, [c for c in split if c["split"] == "train"], engine="f" * 64)
    with pytest.raises(SystemExit, match="ENGINE LINEAGE"):
        floor.admitted_targets(tmp_path, split, ENGINE)


def test_the_space_engine_is_downloaded_at_the_pinned_revision_and_hashed(tmp_path, monkeypatch):
    floor = load()
    binary = tmp_path / "lypning-l"
    binary.write_bytes(b"\x7fELF engine bytes")
    seen = {}

    def download(repo, name, repo_type, revision, token):
        seen.update(repo=repo, name=name, repo_type=repo_type, revision=revision)
        return str(binary)

    import types
    monkeypatch.setitem(sys.modules, "huggingface_hub",
                        types.SimpleNamespace(hf_hub_download=download))

    class Api:
        def repo_info(self, repo, repo_type):
            return types.SimpleNamespace(sha="d" * 40)

    got = floor.space_engine(Api(), "o/space", "c" * 40, "t")
    assert got == {"space": "o/space", "space_revision": "c" * 40, "resolved": "pinned",
                   "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
    assert seen == {"repo": "o/space", "name": "lypning-l", "repo_type": "space",
                    "revision": "c" * 40}
    head = floor.space_engine(Api(), "o/space", "", "t")
    assert (head["space_revision"], head["resolved"]) == ("d" * 40, "space head")


def test_the_floor_is_counted_on_what_is_trained_and_repetition_is_visible(tmp_path, monkeypatch):
    """A few rows repeated clear the token floor; the case floor and the pair say so."""
    floor = load()
    split = floor.assigned_split(bank(), floor.PROTOCOL_SPLIT_SEED)
    train = [c for c in split if c["split"] == "train"]
    rows = write_targets(tmp_path, train)
    cases, rows, _ = floor.admitted_targets(tmp_path, split, ENGINE)
    examples = [{"labels": [-100] + [1] * 100} for _ in rows]
    report = floor.floor_report(cases, rows, examples, 200, 4, 1111)
    assert report["supervised_tokens"] == 800 * 100
    assert report["unique_supervised_tokens"] == len(rows) * 100
    assert report["passes_over_rows"] == round(800 / len(rows), 2) > 1
    assert report["supervised_tokens"] >= report["minimum_supervised_tokens"]
    assert report["cases"] == len({c["case_id"] for c in train}) < 1000
    assert report["clears"] is False, "the token floor alone is cleared by repetition"
    assert any("distinct train cases" in p for p in report["curriculum_problems"])
    # At a case floor this fixture reaches, the same set clears.
    monkeypatch.setattr(sys.modules["train_verified"], "MIN_TRAIN_CASES", len(train))
    assert floor.floor_report(cases, rows, examples, 200, 4, 1111)["clears"] is True


def test_a_curriculum_on_one_family_per_population_is_refused():
    import train_verified
    covered = [case("cov-00", "coverage", n) for n in range(3)]
    control = [case("ctl-00", "fallback-control", n) for n in range(3)]
    problems = train_verified.curriculum_floor(covered + control)["problems"]
    assert any("coverage families" in p for p in problems)
    assert any("fallback-control families" in p for p in problems)


def test_preflight_refuses_a_target_curriculum_below_the_case_floor(tmp_path, monkeypatch):
    """The bundle clears MIN_TRAIN_CASES; the 20 cases the targets train on do not."""
    spec = importlib.util.spec_from_file_location(
        "verified_gpu_floor", ROOT / "training" / "gpu" / "train_verified.py")
    gpu = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gpu)
    cases = []
    for i in range(1000):
        family = "cov-%02d" % (i % 10) if i % 5 else "ctl-%02d" % (i % 4)
        population = "coverage" if i % 5 else "fallback-control"
        c = case(family, population, i)
        c["split"] = "train"
        cases.append(c)
    targets = [c for c in cases[:20]]
    write_targets(tmp_path / "targets", targets)
    bundle = {"digest": "locked", "purpose": "pilot", "limits": {"memory_mb": 1024},
              "identity": {"sha256": ENGINE}, "cases": cases}
    monkeypatch.setattr(gpu, "load_bundle", lambda *a: bundle)
    argv = ["sft", "--bundle", "bundle.json", "--engine", "engine", "--output",
            str(tmp_path / "run"), "--revision", "a" * 40, "--plan", "--steps", "300"]
    assert gpu.preflight(gpu.parser().parse_args(argv)) == (bundle, None)
    with_targets = gpu.parser().parse_args(argv + ["--sft-targets",
                                                    str(tmp_path / "targets" / "sft.jsonl")])
    with pytest.raises(TrainingError, match="reaches 20 distinct train cases; at least 1000"):
        gpu.preflight(with_targets)
    unique = gpu.curriculum_plan(with_targets, bundle)
    scheduled = gpu.supervised_plan(with_targets, bundle)
    assert unique["curriculum_rows"] == 20 and unique["curriculum_cases"] == 20
    assert scheduled["planned_exposures"] == 1200
    assert unique["unique_supervised_token_upper_bound"] < scheduled["supervised_token_upper_bound"]


def test_token_floor_upper_bound_runs_on_the_authored_curriculum():
    """round02-preflight's token-floor step calls this on every grid row.

    Its namespace lacked `sft_targets`, which `sft_curriculum` reads, so every
    bank ended in an AttributeError and the free preflight could never pass.
    """
    spec = importlib.util.spec_from_file_location(
        "token_floor", ROOT / ".github" / "scripts" / "token_floor.py")
    token_floor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(token_floor)
    plan = token_floor.upper_bound(bank(), 3, 4, 1111, 4096, 1024)
    assert plan["planned_exposures"] == 12
    assert plan["supervised_token_upper_bound"] > 0
