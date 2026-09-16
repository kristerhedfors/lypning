from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("test_hf_launch_runtime", Path(__file__).resolve().parents[1] / "hf" / "launch.py")
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)


class FakeApi:
    def __init__(self, stages):
        self.stages, self.calls = list(stages), 0

    def inspect_job(self, job_id):
        stage = self.stages[min(self.calls, len(self.stages) - 1)]
        self.calls += 1
        return SimpleNamespace(status=SimpleNamespace(stage=stage, message=None))


def test_final_status_waits_for_the_hub_to_flip_from_running():
    api = FakeApi(["RUNNING", "RUNNING", "COMPLETED"])
    assert launch.final_status(api, "j", sleep=lambda s: None).stage == "COMPLETED"
    assert api.calls == 3


def test_final_status_gives_up_after_the_deadline():
    api = FakeApi(["RUNNING"])
    assert launch.final_status(api, "j", wait_s=0, sleep=lambda s: None).stage == "RUNNING"
    assert api.calls == 1


def test_bootstrap_checks_out_the_exact_commit():
    cmd = launch.bootstrap("smoke", "b", "c" * 40)
    assert "--branch b" in cmd and "git checkout -q " + "c" * 40 in cmd and cmd.endswith(launch.STAGES["smoke"])


class RepoApi:
    """The two Hub calls the launcher makes about its artifact destination."""
    def __init__(self, existing):
        self.existing, self.created, self.jobs = existing, [], []

    def repo_exists(self, repo_id, repo_type):
        return self.existing is not None

    def repo_info(self, repo_id, repo_type):
        assert self.existing is not None, "repo_info is only asked of a repository that exists"
        return SimpleNamespace(private=self.existing)

    def create_repo(self, repo_id, repo_type, private):
        self.created.append((repo_id, repo_type, private))

    def run_job(self, **kw):
        self.jobs.append(kw)


def test_an_existing_public_repository_is_refused_and_nothing_is_submitted():
    api = RepoApi(existing=False)
    assert launch.private_dataset(api, "someone/work") is False
    assert api.created == [] and api.jobs == [], "visibility is never flipped and no job runs"


def test_a_missing_repository_is_created_private_and_a_private_one_is_accepted():
    api = RepoApi(existing=None)
    assert launch.private_dataset(api, "someone/work") is True
    assert api.created == [("someone/work", "dataset", True)]
    assert launch.private_dataset(RepoApi(existing=True), "someone/work") is True


def test_bootstrap_quotes_every_operator_word():
    cmd = launch.bootstrap("smoke", "feature; rm -rf /", "c" * 40)
    assert "--branch 'feature; rm -rf /'" in cmd and "checkout -q " + "c" * 40 in cmd
    assert cmd.endswith(launch.STAGES["smoke"]) or cmd.endswith("'" + launch.STAGES["smoke"] + "'")


def test_pilot_stage_maps_to_its_script_and_smoke_is_unchanged():
    assert launch.STAGES["pilot"] == "nemotron/hf/round02_pilot.sh"
    assert launch.STAGES["smoke"] == "nemotron/hf/round02_smoke.sh"
    cmd = launch.bootstrap("pilot", "b", "c" * 40)
    assert cmd.endswith(launch.STAGES["pilot"])


def args(stage, **overrides):
    base = dict(stage=stage, space="o/space", space_revision="a" * 40, qwen_revision="b" * 40,
                work_repo="o/work", bank_path=None, steps=launch.DEFAULT_STEPS,
                eval_draws=launch.DEFAULT_EVAL_DRAWS, seed=launch.DEFAULT_SEED)
    base.update(overrides)
    return SimpleNamespace(**base)


def test_smoke_env_carries_only_the_four_original_keys():
    env = launch.job_env(args("smoke", bank_path="ignored/for/smoke", steps=5))
    assert env == {"SPACE_REPO": "o/space", "SPACE_REV": "a" * 40, "QWEN_REV": "b" * 40, "WORK_REPO": "o/work"}


def test_pilot_env_wires_the_bank_and_its_knobs_as_strings():
    env = launch.job_env(args("pilot", bank_path="banks/2026-09-16", steps=40, eval_draws=8, seed=2222))
    assert env == {"SPACE_REPO": "o/space", "SPACE_REV": "a" * 40, "QWEN_REV": "b" * 40, "WORK_REPO": "o/work",
                   "BANK_PATH": "banks/2026-09-16", "STEPS": "40", "EVAL_DRAWS": "8", "SEED": "2222"}
    defaults = launch.job_env(args("pilot", bank_path="banks/x"))
    assert (defaults["STEPS"], defaults["EVAL_DRAWS"], defaults["SEED"]) == ("20", "16", "1111")


def test_pilot_without_a_bank_path_is_rejected_before_any_hub_call(monkeypatch, capsys):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    argv = ["pilot", "--branch", "b", "--commit", "c" * 40, "--space", "o/space",
            "--space-revision", "a" * 40, "--qwen-revision", "b" * 40, "--work-repo", "o/work"]
    assert launch.main(argv) == 2
    assert "--bank-path" in capsys.readouterr().err
    assert launch.main(argv + ["--bank-path", "/"]) == 2
    # With a bank path the next gate is the token, i.e. the usage checks passed.
    assert launch.main(argv + ["--bank-path", "banks/x"]) == 2
    assert "HF_TOKEN" in capsys.readouterr().err
    assert launch.main(argv + ["--bank-path", "banks/x", "--steps", "0"]) == 2
