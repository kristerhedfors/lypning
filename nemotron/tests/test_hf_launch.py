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
