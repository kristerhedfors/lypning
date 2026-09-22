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
    assert launch.STAGES["pilot"] == "training/hf/round02_pilot.sh"
    assert launch.STAGES["smoke"] == "training/hf/round02_smoke.sh"
    cmd = launch.bootstrap("pilot", "b", "c" * 40)
    assert cmd.endswith(launch.STAGES["pilot"])


def args(stage, **overrides):
    base = dict(stage=stage, space="o/space", space_revision="a" * 40, qwen_revision="b" * 40,
                work_repo="o/work", bank_path=None, steps=launch.DEFAULT_STEPS,
                grpo_steps=launch.DEFAULT_GRPO_STEPS,
                eval_draws=launch.DEFAULT_EVAL_DRAWS, seed=launch.DEFAULT_SEED,
                eval_sequences=launch.DEFAULT_EVAL_SEQUENCES, score_workers=launch.DEFAULT_SCORE_WORKERS,
                pool_sandboxes_per_host=launch.DEFAULT_POOL_SANDBOXES_PER_HOST,
                pool_max_hosts=launch.DEFAULT_POOL_MAX_HOSTS,
                bundles_from="", sft_target_run="")
    base.update(overrides)
    return SimpleNamespace(**base)


def test_smoke_env_carries_only_the_four_original_keys():
    env = launch.job_env(args("smoke", bank_path="ignored/for/smoke", steps=5))
    assert env == {"SPACE_REPO": "o/space", "SPACE_REV": "a" * 40, "QWEN_REV": "b" * 40, "WORK_REPO": "o/work"}


def test_pilot_env_wires_the_bank_and_its_knobs_as_strings():
    env = launch.job_env(args("pilot", bank_path="banks/2026-09-16", steps=40, eval_draws=8, seed=2222))
    assert env == {"SPACE_REPO": "o/space", "SPACE_REV": "a" * 40, "QWEN_REV": "b" * 40, "WORK_REPO": "o/work",
                   "BANK_PATH": "banks/2026-09-16", "STEPS": "40", "GRPO_STEPS": "20",
                   "EVAL_DRAWS": "8", "SEED": "2222",
                   "EVAL_SEQUENCES": "256", "SCORE_WORKERS": "12",
                   "NTX_POOL_SANDBOXES_PER_HOST": "4", "NTX_POOL_MAX_HOSTS": "4",
                   "BUNDLES_FROM": "", "SFT_TARGET_RUN": ""}
    defaults = launch.job_env(args("pilot", bank_path="banks/x"))
    assert (defaults["STEPS"], defaults["GRPO_STEPS"], defaults["EVAL_DRAWS"],
            defaults["SEED"]) == ("250", "20", "16", "1111")
    reused = launch.job_env(args("pilot", bank_path="banks/x", bundles_from="round-02/6aaa4b2c", eval_sequences=64, score_workers=8))
    assert (reused["BUNDLES_FROM"], reused["EVAL_SEQUENCES"], reused["SCORE_WORKERS"]) == ("round-02/6aaa4b2c", "64", "8")
    targets = launch.job_env(args("pilot", bank_path="banks/x", sft_target_run="confirmatory-a-1"))
    assert targets["SFT_TARGET_RUN"] == "confirmatory-a-1"


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
    assert launch.main(argv + ["--bank-path", "banks/x", "--score-workers", "16",
                               "--pool-sandboxes-per-host", "2", "--pool-max-hosts", "2"]) == 2
    assert "pool capacity" in capsys.readouterr().err


def pool_argv(stage, workers, per_host, hosts):
    """A launch of `stage` at one pool shape; every other word is a fixed legal value."""
    return [stage, "--branch", "b", "--commit", "c" * 40, "--space", "o/space",
            "--space-revision", "a" * 40, "--qwen-revision", "b" * 40, "--work-repo", "o/work",
            "--bank-path", "banks/x", "--score-workers", str(workers),
            "--pool-sandboxes-per-host", str(per_host), "--pool-max-hosts", str(hosts)]


def test_a_banked_launch_is_refused_above_the_per_host_density_ceiling(monkeypatch, capsys):
    """Sixteen sandboxes on one host clears the product check and must not clear this one.

    `native` is host-load-dependent, so density is an instrument parameter and
    two arms scored at different densities are not comparable. A shape that
    passes reaches the token check, which is how this test says "admitted"
    without a Hub call; HF_TOKEN stays unset throughout.
    """
    monkeypatch.delenv("HF_TOKEN", raising=False)
    assert launch.main(pool_argv("pilot", 16, 16, 1)) == 2
    err = capsys.readouterr()
    assert "--pool-sandboxes-per-host" in err.err and launch.POOL_FLAVOR in err.err
    assert err.out == "", "a refused shape prints no plan"
    # Lower density is never refused HERE: this ceiling is about contention on
    # one host, and `16/2/8` puts less of it on each. The host ceiling is a cost
    # ceiling and lives further out, so it refuses only beyond MAX_POOL_HOSTS —
    # raised to 16 on 2026-09-20 to buy scoring throughput, which is a cost
    # decision, while this number stayed at 4 because it is the instrument.
    # Every shape here keeps one host of slack, because the capacity guard sits
    # one check earlier and `(16, 4, 4)` / `(64, 4, 16)` are exactly-full pools
    # — the shape that lost 6ab01391, refused since 2026-09-20.
    for workers, per_host, hosts in ((12, 4, 4), (1, 1, 1), (4, 4, 2), (14, 2, 8), (60, 4, 16)):
        assert launch.main(pool_argv("pilot", workers, per_host, hosts)) == 2
        assert "HF_TOKEN" in capsys.readouterr().err, (workers, per_host, hosts)
    for workers, per_host, hosts in ((16, 2, 17), (16, 1, 32)):
        assert launch.main(pool_argv("pilot", workers, per_host, hosts)) == 2
        assert "cost ceiling" in capsys.readouterr().err, (workers, per_host, hosts)


def test_the_density_ceiling_does_not_reach_a_smoke(monkeypatch, capsys):
    """A smoke carries no pool knob into the job (`job_env`), so no density of its own."""
    monkeypatch.delenv("HF_TOKEN", raising=False)
    # One scorer, so the product check — which is not stage-conditioned — never
    # fires and what is left under test is the density alone.
    for per_host in (1, launch.MAX_POOL_SANDBOXES_PER_HOST, launch.MAX_POOL_SANDBOXES_PER_HOST + 12):
        assert launch.main(pool_argv("smoke", 1, per_host, 1)) == 2
        assert "HF_TOKEN" in capsys.readouterr().err, per_host


def test_the_ceilings_flavor_is_the_flavor_the_pool_actually_runs_on():
    """The ceiling is four *at* `cpu-basic`; nothing else ties the two constants.

    `launch.py` is loaded by path and deliberately does not import the package,
    so its flavor is a literal. That leaves the number free to outlive the host
    it was decided for: if the runner's pool moves to another flavor, four
    sandboxes per host is a different amount of contention and has to be
    decided again rather than carried over. This is the link the module cannot
    make for itself.
    """
    from pipeline import hf_sandbox_runner

    assert launch.POOL_FLAVOR == hf_sandbox_runner.FLAVOR


def test_a_banked_launch_is_refused_above_the_host_cost_ceiling(monkeypatch, capsys):
    """The other half of the envelope: four hosts is a ceiling, not just a default.

    Contributed by the second independent review of the same round. It is
    conditioned on a banked stage for the same reason the density ceiling is —
    only a banked stage carries these knobs into the job, so refusing them on a
    smoke would refuse a value that does nothing.
    """
    monkeypatch.delenv("HF_TOKEN", raising=False)
    assert launch.main(pool_argv("pilot", 16, 4, launch.MAX_POOL_HOSTS + 1)) == 2
    err = capsys.readouterr()
    assert "cost ceiling is %d CPU hosts" % launch.MAX_POOL_HOSTS in err.err
    assert err.out == "", "a refused shape prints no plan"
    # The ceiling itself is admitted, and a smoke is not reached by it at all.
    assert launch.main(pool_argv("pilot", 16, 4, launch.MAX_POOL_HOSTS)) == 2
    assert "HF_TOKEN" in capsys.readouterr().err
    assert launch.main(pool_argv("smoke", 1, 1, launch.MAX_POOL_HOSTS + 6)) == 2
    assert "HF_TOKEN" in capsys.readouterr().err


def test_a_banked_launch_above_its_ceiling_is_told_the_ceiling(monkeypatch, capsys):
    """The dead end the density fix removed, re-created one knob further out.

    Both pool knobs are capped for a banked stage, so the admissible capacity is
    their product and any `--score-workers` above it is unsatisfiable. The
    product check used to answer "increase --pool-max-hosts", and following that
    advice earned "pool cost ceiling is 4 CPU hosts" — two refusals, no legal
    shape, and the number that would have ended it stated in neither. Above the
    ceiling the advice has to be the ceiling; at or below it, the knob is still
    the right answer and must stay.
    """
    monkeypatch.delenv("HF_TOKEN", raising=False)
    # The admissible ceiling is the product LESS one host: capacity exactly
    # equal to the scorer count has no room for a sandbox still winding down,
    # and the pool raises rather than waits. See the test below.
    ceiling = launch.MAX_POOL_SANDBOXES_PER_HOST * (launch.MAX_POOL_HOSTS - 1)

    # At the MAXIMAL shape, so no knob has room left; at 4/4 the host knob does
    # and "increase --pool-max-hosts" is then the right answer, not a dead end.
    assert launch.main(pool_argv("pilot", ceiling + 16, launch.MAX_POOL_SANDBOXES_PER_HOST,
                                 launch.MAX_POOL_HOSTS)) == 2
    err = capsys.readouterr()
    assert "tops out at %d scorers" % ceiling in err.err
    assert "--pool-max-hosts" not in err.err, "no knob reaches above the ceiling"
    assert err.out == ""

    # Below the ceiling the shape exists, and the advice names the knob that
    # still has room — not the knob that matches the stage. `16/1/4` is the case
    # that made this a dead end: hosts are already at MAX_POOL_HOSTS, so the old
    # "increase --pool-max-hosts" earned "pool cost ceiling is 4 CPU hosts" on
    # the next attempt, and the knob that does reach was the suppressed half.
    assert launch.main(pool_argv("pilot", ceiling, 1, launch.MAX_POOL_HOSTS)) == 2
    err = capsys.readouterr()
    assert "increase --pool-sandboxes-per-host" in err.err
    assert "--pool-max-hosts" not in err.err, "that knob is already at its ceiling"

    # Symmetrically at full density and one host, and both when both have room.
    assert launch.main(pool_argv("pilot", ceiling, launch.MAX_POOL_SANDBOXES_PER_HOST, 1)) == 2
    err = capsys.readouterr()
    assert "increase --pool-max-hosts" in err.err
    assert "--pool-sandboxes-per-host" not in err.err
    assert launch.main(pool_argv("pilot", ceiling, 1, 1)) == 2
    err = capsys.readouterr()
    assert "--pool-sandboxes-per-host or --pool-max-hosts" in err.err

    assert launch.main(pool_argv("pilot", ceiling, launch.MAX_POOL_SANDBOXES_PER_HOST,
                                 launch.MAX_POOL_HOSTS)) == 2
    assert "HF_TOKEN" in capsys.readouterr().err, "the admissible shape is admitted"

    # A smoke carries no pool knob into the job, so it has no ceiling of its own
    # and both knobs always have room.
    assert launch.main(pool_argv("smoke", ceiling + 16, 4, 4)) == 2
    err = capsys.readouterr()
    assert "tops out at" not in err.err
    assert "--pool-sandboxes-per-host or --pool-max-hosts" in err.err


def test_scorers_exactly_equal_to_pool_capacity_are_refused(monkeypatch, capsys):
    """The shape that lost job 6ab01391 on 2026-09-20, now unlaunchable.

    `--score-workers 64` against 4 sandboxes on each of 16 hosts is exactly
    64 slots for exactly 64 scorers. That reads like a perfect fit and is
    instead the one shape with no recovery: `SandboxPool.create` RAISES rather
    than waits once every host is full, and a round's stages are separate
    processes that adopt the previous stage's still-warm hosts *at their true
    occupancy*. One sandbox not yet reaped and the next stage asks for host
    seventeen against `max_hosts=16`.

    It cost a pilot bundle that had already been built -- the expensive half of
    the round -- because the stage that fails is the second one.

    So capacity must exceed the scorers by a whole host, and the exact-fit
    shape must be refused here, for nothing, rather than six hours in.
    """
    monkeypatch.delenv("HF_TOKEN", raising=False)
    per_host, hosts = launch.MAX_POOL_SANDBOXES_PER_HOST, launch.MAX_POOL_HOSTS
    exact = per_host * hosts

    assert launch.main(pool_argv("pilot", exact, per_host, hosts)) == 2
    err = capsys.readouterr()
    assert "tops out at %d scorers" % (per_host * (hosts - 1)) in err.err
    assert err.out == "", "library code does not print"

    # One host of slack is the boundary: it is admitted, and one more is not.
    assert launch.main(pool_argv("pilot", exact - per_host, per_host, hosts)) == 2
    assert "HF_TOKEN" in capsys.readouterr().err, "a full host of slack is enough"
    assert launch.main(pool_argv("pilot", exact - per_host + 1, per_host, hosts)) == 2
    assert "tops out at" in capsys.readouterr().err, "one slot short of a host is not"


def test_the_round_the_workflow_would_actually_launch_is_admissible():
    """round02.yml's own numbers, checked against the guard rather than by eye.

    The scorer count and host count live in a YAML file and the rule that
    admits them lives in Python, so nothing but this test makes them agree. The
    dead round had `PILOT_SCORERS: "64"` sitting beside `PILOT_POOL_HOSTS: "16"`
    and no check anywhere related the two.
    """
    import re
    from pathlib import Path

    text = Path(__file__).resolve().parents[2].joinpath(
        ".github/workflows/round02.yml").read_text(encoding="utf-8")
    scorers = int(re.search(r'^  PILOT_SCORERS: "(\d+)"', text, re.M).group(1))
    hosts = int(re.search(r'^  PILOT_POOL_HOSTS: "(\d+)"', text, re.M).group(1))
    per_host = launch.MAX_POOL_SANDBOXES_PER_HOST

    assert hosts <= launch.MAX_POOL_HOSTS, "the workflow is above the cost ceiling"
    assert per_host * hosts >= scorers + per_host, (
        "round02.yml would be refused by the launch guard: %d scorers against "
        "%d x %d slots leaves less than one host of slack" % (scorers, per_host, hosts))


def test_provider_and_process_group_deadlines_use_the_same_duration():
    a = args("pilot", timeout="720m", branch="example", commit="a" * 40)
    command = launch.bounded_command(a)
    assert launch.timeout_seconds(a.timeout) == 43200
    assert command[:6] == ["timeout", "--signal=TERM", "--kill-after=60s", "43140s", "bash", "-c"]
    assert command[-1] == launch.bootstrap(a.stage, a.branch, a.commit)
    import pytest
    for bad in ("0", "1s", "-2h", "nan", "1h;echo secret", ""):
        with pytest.raises(ValueError):
            launch.timeout_seconds(bad)


def test_timeout_kills_children_that_ignore_term(tmp_path):
    import shutil
    import subprocess
    import sys
    import time
    import pytest
    timeout = shutil.which("timeout") or shutil.which("gtimeout")
    if timeout is None:
        pytest.skip("GNU timeout is supplied by the Linux worker image")
    started = time.monotonic()
    proc = subprocess.run([timeout, "--signal=TERM", "--kill-after=1s", "1s", sys.executable,
        "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"],
        timeout=8)
    assert proc.returncode in (-9, 137)
    assert time.monotonic() - started < 8


def test_timeout_audit_prints_no_environment_or_log_payload():
    import json
    spec = importlib.util.spec_from_file_location("timeout_audit", Path(__file__).parents[2] /
                                                ".github/scripts/hf_timeout_audit.py")
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    summary = audit.safe_summary(dict(timeoutSeconds=28800, timeout="SECRET", createdAt="SECRET",
        startedAt="2026-09-20T17:49:47Z", secrets={"HF_TOKEN": "SECRET"}, command=["SECRET"],
        environment={"SECRET": "SECRET"}, status={"stage": "CANCELED", "message": "SECRET"},
        durations={"running": 40000, "private": "SECRET"}))
    assert "SECRET" not in json.dumps(summary)
    assert summary["timeoutSeconds"] == 28800
    assert summary["startedAt"] == "2026-09-20T17:49:47Z"
