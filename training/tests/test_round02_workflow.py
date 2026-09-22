"""round02.yml and its preflights, read as text and checked against the launcher.

A workflow runs only on GitHub, and the first run of a wrong one is either a
skipped submit nobody notices or a billed job. Everything here is a property
of the YAML that can be decided without running it: which seed reaches which
command, which base-model revision is used, whether the smoke route is
reachable at all, and whether any author-controlled string is expanded into a
script that holds HF_TOKEN.
"""
from __future__ import annotations

import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

from pipeline.training_contract import PROTOCOL_TRAIN_SEEDS

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
ROUND02 = (WORKFLOWS / "round02.yml").read_text(encoding="utf-8")


def job(text, name):
    """The body of one top-level job, by its key."""
    start = re.search(r"^  %s:\n" % re.escape(name), text, re.M).end()
    rest = text[start:]
    end = re.search(r"^  [\w-]+:\n", rest, re.M)
    return rest[:end.start()] if end else rest


def run_blocks(text):
    """Every `run:` script, block or inline, as text."""
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)(?:- )?run: (.*)$", line)
        if not m:
            continue
        if m.group(2).strip() not in ("|", "|-", ">", ">-"):
            out.append(m.group(2))
            continue
        indent = len(m.group(1))
        body = []
        for follow in lines[i + 1:]:
            if follow.strip() and len(follow) - len(follow.lstrip()) <= indent:
                break
            body.append(follow)
        out.append("\n".join(body))
    return out


@pytest.mark.parametrize("name", ["round02.yml", "s4-target-preflight.yml", "round02-preflight.yml",
                                  "step2-control.yml", "step2-control-grade.yml", "step2-merge.yml",
                                  "provider-seed.yml"])
def test_no_expression_is_expanded_into_a_script(name):
    """Values reach scripts through env; a commit message with `$(...)` would run otherwise."""
    for script in run_blocks((WORKFLOWS / name).read_text(encoding="utf-8")):
        assert "${{" not in script, script


def test_the_seed_input_is_the_protocol_seeds_and_reaches_both_commands():
    options = re.search(r"      seed:\n(?:        .*\n)*?        options: \[([^\]]*)\]", ROUND02)
    assert tuple(int(v.strip().strip('"')) for v in options.group(1).split(",")) == PROTOCOL_TRAIN_SEEDS
    assert "PILOT_SEED: ${{ github.event.inputs.seed || '1111' }}" in ROUND02
    floor, submit = job(ROUND02, "token-floor"), job(ROUND02, "submit")
    for body in (floor, submit):
        assert '--seed "${PILOT_SEED}" --split-seed "${PILOT_SPLIT_SEED}"' in body
    assert "--seed 1111" not in ROUND02, "no hard-coded seed is left"
    assert re.search(r'^  PILOT_SPLIT_SEED: "1111"$', ROUND02, re.M)


def test_arm_a_is_dispatched_without_grpo():
    assert re.search(r'^  PILOT_GRPO_STEPS: "0"$', ROUND02, re.M)
    assert '--grpo-steps "${PILOT_GRPO_STEPS}"' in job(ROUND02, "submit")


def test_dev_draws_and_the_arm_c_knobs_are_chosen_here_not_defaulted_in_the_launcher():
    """Absent, launch.py's DEFAULT_DEV_EVAL_DRAWS applied and nobody chose it."""
    submit = job(ROUND02, "submit")
    for env, flag, value in (("PILOT_DEV_EVAL_DRAWS", "--dev-eval-draws", "4"),
                             ("PILOT_GRPO_GENERATIONS", "--grpo-generations", "4"),
                             ("PILOT_GRPO_PROMPTS", "--grpo-prompts", "4")):
        assert re.search(r'^  %s: "%s"$' % (env, value), ROUND02, re.M), env
        assert '%s "${%s}"' % (flag, env) in submit, flag
    assert "--eval-draws 16" in submit


def bundles_pattern():
    submit = job(ROUND02, "submit")
    check = re.search(r'\[\[ "\$\{PILOT_BUNDLES_FROM\}" =~ (\S+) \]\]', submit)
    assert check, "bundles_from is validated in submit, on the whole value"
    assert check.start() < submit.index("launch.py pilot"), "validated before the launch"
    assert "grep -Eqx" not in submit, "grep -x matches one line of a multi-line value"
    return check.group(1)


BUNDLES_BAD = ("round-02/6ab01cbb51992417dfccd64c/pilot", "/round-02/6ab01cbb51992417dfccd64c",
               "round-02/../banks/v3-20260920b", "banks/v3-20260920b", "round-02/",
               "round-02/6AB01CBB51992417DFCCD64C", "round-02/6ab01cbb",
               # A dispatch input sent through the API can span lines; one
               # valid line must not carry the others through.
               "round-02/6ab01cbb51992417dfccd64c\nbanks/v3-20260920b",
               "banks/v3-20260920b\nround-02/6ab01cbb51992417dfccd64c",
               "round-02/6ab01cbb51992417dfccd64c\n")


def test_bundles_from_is_one_job_directory_validated_before_the_launch():
    assert "PILOT_BUNDLES_FROM: ${{ github.event.inputs.bundles_from || '' }}" in ROUND02
    assert '--bundles-from "${PILOT_BUNDLES_FROM}"' in job(ROUND02, "submit")
    pattern = bundles_pattern()
    assert re.fullmatch(pattern.lstrip("^").rstrip("$"), "round-02/6ab01cbb51992417dfccd64c")
    for bad in BUNDLES_BAD:
        assert not re.fullmatch(pattern.lstrip("^").rstrip("$"), bad), bad


def test_the_shell_check_refuses_what_python_refuses():
    """Run the workflow's own test in bash, the step's shell, value through env."""
    import os
    import shutil
    import subprocess
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not installed")
    pattern = bundles_pattern()
    script = '[[ "${PILOT_BUNDLES_FROM}" =~ %s ]]' % pattern

    def shell(value):
        return subprocess.run([bash, "-c", script], env=dict(os.environ, PILOT_BUNDLES_FROM=value),
                              stdin=subprocess.DEVNULL).returncode == 0
    assert shell("round-02/6ab01cbb51992417dfccd64c")
    for bad in BUNDLES_BAD:
        assert not shell(bad), repr(bad)


def test_the_qwen_revision_is_one_pin_everywhere_and_never_resolved_at_dispatch():
    pins = {}
    for name in ("round02.yml", "s4-target-preflight.yml", "round02-preflight.yml",
                 "step2-control-grade.yml", "step2-merge.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        pins[name] = re.findall(r"^\s*QWEN_REV: ([0-9a-f]{40})$", text, re.M)
    assert {pin for found in pins.values() for pin in found} == {
        "1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0"}, pins
    assert all(pins.values())
    assert "needs.preflight.outputs.qwen_rev" not in job(ROUND02, "submit")
    assert "needs.preflight.outputs.qwen_rev" not in job(ROUND02, "token-floor")


def test_the_preflight_prices_the_flavor_a_pilot_bills():
    probe = job(ROUND02, "preflight")
    assert "env.PILOT_FLAVOR" in probe and "FLAVOR:" in probe


def test_the_floor_compares_against_the_engine_bootstrap_pinned():
    floor = job(ROUND02, "token-floor")
    assert re.search(r"needs: \[preflight, bootstrap\]", floor)
    assert "needs.bootstrap.outputs.space_rev" in floor
    assert '--space "${SPACE_REPO}" --space-revision "${SPACE_REV}"' in floor


def submit_condition():
    body = job(ROUND02, "submit")
    expr = re.search(r"    if: >-\n((?:      .*\n)+)", body).group(1)
    expr = " ".join(expr.split())
    # GitHub's rule: without a status function the condition is ANDed with an
    # implicit success() over every need, and a skipped need is not a success.
    implicit = not re.search(r"\b(always|failure|cancelled|success)\(\)", expr)
    assert "always()" not in expr, "always() is true in a cancelled run too"
    for old, new in (("!cancelled()", "not cancelled"), ("&&", " and "), ("||", " or "),
                     ("!contains", "not contains"),
                     ("github.event.head_commit.message", "msg"),
                     ("github.event.inputs.stage", "stage"),
                     ("github.event.inputs.submit", "submit")):
        expr = expr.replace(old, new)
    expr = re.sub(r"needs\.([\w-]+)\.result", r"needs['\1']", expr)
    assert "!" not in expr.replace("!=", ""), expr

    def decide(msg="", stage=None, submit=None, floor="skipped", cancelled=False):
        needs = {"preflight": "success", "bootstrap": "success", "token-floor": floor}
        scope = {"msg": msg or "", "stage": stage, "submit": submit, "needs": needs,
                 "cancelled": cancelled,
                 "contains": lambda text, marker: marker in (text or "")}
        if implicit and any(result != "success" for result in needs.values()):
            return False
        return eval(expr, {"__builtins__": {}}, scope)                   # noqa: S307
    return decide


def test_a_smoke_submit_is_reachable_and_a_pilot_needs_its_floor():
    """The smoke route was dead: submit needed a floor job that a smoke skips."""
    decide = submit_condition()
    assert decide(stage="smoke", submit="SUBMIT") is True
    assert decide(msg="x [submit-smoke]") is True
    assert decide(stage="pilot", submit="SUBMIT", floor="success") is True
    assert decide(msg="[submit-pilot]", floor="success") is True
    for floor in ("skipped", "failure", "cancelled"):
        assert decide(stage="pilot", submit="SUBMIT", floor=floor) is False
        assert decide(msg="[submit-smoke] [submit-pilot]", floor=floor) is False
    assert decide() is False and decide(stage="smoke") is False
    assert decide(stage="pilot", submit="nope", floor="success") is False


def test_a_cancelled_run_submits_nothing():
    """Cancelling after bootstrap must not bill: `always()` would still run submit."""
    decide = submit_condition()
    assert decide(stage="smoke", submit="SUBMIT", cancelled=True) is False
    assert decide(msg="x [submit-smoke]", cancelled=True) is False
    assert decide(stage="pilot", submit="SUBMIT", floor="success", cancelled=True) is False


def test_a_moved_hub_head_fails_the_preflight(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(HfApi=None))
    spec = importlib.util.spec_from_file_location(
        "round02_preflight", ROOT / ".github" / "scripts" / "round02_preflight.py")
    pre = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pre)
    assert pre.revision_moved("a" * 40, "a" * 40) == (False, "a" * 40)
    assert pre.revision_moved("a" * 40, "b" * 40) == (True, "")
    assert "PINNED REVISION MOVED" in capsys.readouterr().err
