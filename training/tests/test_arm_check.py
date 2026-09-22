"""Three seeds are one result only if they are one experiment.

A complete S4 replicate is 1111, 2222 and 3333, read from three manifests.
Nothing related those three to each other: `s0_inventory` can print a manifest
and no code anywhere compared two. So seeds run weeks apart, across an engine
fix or a re-cut bank, would be combined by hand and nothing would say they were
different experiments -- silently, at about $27 a seed, discoverable only by
someone remembering what changed between two dates.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location(
        "arm_check", ROOT / ".github" / "scripts" / "arm_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest(**over):
    base = dict(seed=1111, job="j1", status="ok", bank_path="banks/v3-20260920b",
                space_revision="a" * 40, qwen_revision="b" * 40, steps=300, grpo_steps=20,
                eval_draws=16, eval_sequences=128, pool_sandboxes_per_host=4,
                pool_max_hosts=16, score_workers=48, commit="c" * 40)
    base.update(over)
    return base


def test_seeds_of_one_arm_agree_and_differences_are_named():
    arm = load()
    same = [manifest(seed=1111), manifest(seed=2222, job="j2"), manifest(seed=3333, job="j3")]
    assert arm.differences(same) == {}, "the seed itself must never be an arm difference"

    # A re-cut bank between seeds is the expensive silent one.
    drifted = same[:2] + [manifest(seed=3333, job="j3", bank_path="banks/v3-REcut")]
    bad = arm.differences(drifted)
    assert "bank_path" in bad
    assert sorted(v for vs in bad["bank_path"].values() for v in vs) == [1111, 2222, 3333]


def test_density_is_an_instrument_and_host_count_is_not():
    """`native` is host-load dependent, so packing changes what the label MEANS.

    The host ceiling is a cost decision -- cents on `cpu-basic` beside an h200 --
    and raising it between seeds must not invalidate a result. Packing more
    sandboxes onto each host must.
    """
    arm = load()
    assert "pool_sandboxes_per_host" in arm.ARM_FIELDS
    for cost_only in ("pool_max_hosts", "score_workers", "seed"):
        assert cost_only not in arm.ARM_FIELDS, cost_only

    density = [manifest(seed=1111), manifest(seed=2222, job="j2", pool_sandboxes_per_host=8)]
    assert "pool_sandboxes_per_host" in arm.differences(density)
    hosts = [manifest(seed=1111), manifest(seed=2222, job="j2", pool_max_hosts=4)]
    assert arm.differences(hosts) == {}, "a cost ceiling is not an arm"


def test_commit_is_reported_but_not_enforced():
    """This file cannot tell a workflow fix from an engine change.

    Refusing every round that landed an unrelated commit would make the check
    fire constantly and be turned off, which is worse than showing the
    difference and letting a reader judge.
    """
    arm = load()
    for field in arm.REPORTED:
        assert field not in arm.ARM_FIELDS
    drift = [manifest(seed=1111), manifest(seed=2222, job="j2", commit="d" * 40)]
    assert arm.differences(drift) == {}


def arm_a(**over):
    """A manifest as `round02_pilot.sh` writes it since the split was decoupled."""
    base = manifest(grpo_steps=0, split_seed=1111, sft_target_run="targets-1",
                    sft_sha256="d" * 64, sft_learning_rate=1e-4, grpo_learning_rate=None,
                    kernels={"NTX_USE_FLA": "0", "flash-linear-attention": "blocked"})
    base.update(over)
    return base


def test_two_target_sets_are_two_arms():
    """What is trained on is the arm A field, and nothing else would say so.

    Seed 1111 of 2026-09-21 and arm A share a bank, a Space, a revision and a
    step count; without the target run, the rates and the kernel this check
    called them one arm, and PLAN.md's gate before the second seed would have
    passed on the wrong experiment.
    """
    arm = load()
    for field in ("split_seed", "sft_target_run", "sft_sha256", "sft_learning_rate",
                  "grpo_learning_rate", "kernels"):
        assert field in arm.ARM_FIELDS, field
    one = arm_a(seed=1111)
    other = arm_a(seed=2222, job="j2", sft_target_run="targets-2")
    assert arm.arm_of(one) != arm.arm_of(other)
    assert set(arm.differences([one, other])) == {"sft_target_run"}
    for field, value in (("sft_sha256", "e" * 64), ("sft_learning_rate", 2e-5),
                         ("kernels", {"NTX_USE_FLA": "0", "flash-linear-attention": "usable"}),
                         ("split_seed", 2222)):
        assert set(arm.differences([one, arm_a(seed=2222, job="j2", **{field: value})])) == {field}
    same = [arm_a(seed=s, job="j%d" % s) for s in (1111, 2222, 3333)]
    assert arm.differences(same) == {}, "three seeds of arm A are one arm"


def test_old_manifests_stay_readable_and_never_match_a_new_one():
    """Absence is a value, not a wildcard; the split seed of an old round is known."""
    arm = load()
    old = [manifest(seed=1111), manifest(seed=2222, job="j2")]
    assert arm.differences(old) == {}, "two old manifests still agree with each other"
    assert arm.arm_value(old[0], "split_seed") == arm.SPLIT_FOLLOWS_SEED
    assert arm.arm_value(old[0], "sft_sha256") == arm.UNRECORDED
    mixed = arm.differences([manifest(seed=1111, grpo_steps=0), arm_a(seed=2222, job="j2")])
    assert {"split_seed", "sft_target_run", "sft_sha256", "kernels"} <= set(mixed)


def test_only_rounds_that_ran_are_evidence_about_an_arm():
    """A round that died at `prepare` says nothing about the experiment."""
    arm = load()
    assert "failed" not in arm.OK
    assert "ok" in arm.OK and "succeeded" in arm.OK


def test_arm_of_is_stable_and_orders_the_same_way():
    arm = load()
    assert arm.arm_of(manifest(seed=1111)) == arm.arm_of(manifest(seed=2222, job="j2"))
    assert arm.arm_of(manifest()) != arm.arm_of(manifest(steps=250))


def test_one_arm_is_selected_by_its_fields_and_the_rest_are_not_compared():
    """Seed 1111's old round and arm A share a bank; the check is about one of them."""
    arm = load()
    old = manifest(seed=1111, job="old")
    seeds = [arm_a(seed=1111, job="a1"), arm_a(seed=2222, job="a2")]
    assert arm.differences([old] + seeds), "compared together they are two arms"
    want, problem = arm.parse_pairs(["sft_target_run=targets-1"], "--select")
    assert problem is None
    assert arm.selected([old] + seeds, want) == seeds
    assert arm.differences(arm.selected([old] + seeds, want)) == {}
    for bad in ("seed=1111", "no-equals"):
        want, problem = arm.parse_pairs([bad], "--select")
        assert want is None and "--select" in problem
