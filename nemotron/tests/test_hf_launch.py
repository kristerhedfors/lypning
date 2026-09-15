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
