"""The job script's stage order and gates, read and run without a job.

`round02_pilot.sh` runs only inside a billed Hugging Face Job, so what it does
with GRPO_STEPS=0, where it checks the targets and what its manifest records
are otherwise first observed on an h200. The embedded Python is extracted and
executed here; the stage order is read from the text.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "training" / "hf" / "round02_pilot.sh"
TEXT = SCRIPT.read_text(encoding="utf-8")


def heredoc(opening):
    """The Python body that follows the line containing `opening`."""
    start = TEXT.index(opening)
    body = TEXT[TEXT.index("\n", start) + 1:]
    return body[:body.index("\nPYEOF")] + "\n"


def run_snippet(source, cwd, env, *argv):
    full = dict(os.environ, PYTHONPATH=str(ROOT / "training"), **env)
    return subprocess.run([sys.executable, "-c", source] + list(argv), cwd=cwd, env=full,
                          capture_output=True, text=True, timeout=60)


def probe(tmp_path, admitted):
    (tmp_path / "work/round-02/probe").mkdir(parents=True)
    (tmp_path / "work/round-02/probe/probe.json").write_text(json.dumps({
        "admitted": admitted, "informative_groups": 7, "groups": 30, "correct_draws": 40,
        "truncated_draws": 0, "draws": 120}))


def test_arm_a_runs_the_probe_and_records_why_grpo_did_not_run(tmp_path):
    """GRPO_STEPS=0 skips GRPO even on an admitted probe, and says so with its verdict."""
    gate = heredoc('GRPO_STEPS="$GRPO_STEPS" python3 - <<\'PYEOF\'')
    probe(tmp_path, admitted=True)
    done = run_snippet(gate, tmp_path, {"GRPO_STEPS": "0"})
    assert done.returncode == 3, done.stderr
    marker = json.loads((tmp_path / "work/round-02/grpo-skipped.json").read_text())
    assert "arm A only" in marker["why"] and marker["admitted"] is True
    assert marker["informative_groups"] == 7, "arm C's admission evidence is kept"


def test_a_positive_dose_still_runs_grpo_on_an_admitted_probe_only(tmp_path):
    gate = heredoc('GRPO_STEPS="$GRPO_STEPS" python3 - <<\'PYEOF\'')
    probe(tmp_path / "a", admitted=True)
    assert run_snippet(gate, tmp_path / "a", {"GRPO_STEPS": "300"}).returncode == 0
    assert not (tmp_path / "a/work/round-02/grpo-skipped.json").exists()
    probe(tmp_path / "b", admitted=False)
    assert run_snippet(gate, tmp_path / "b", {"GRPO_STEPS": "300"}).returncode == 3
    marker = json.loads((tmp_path / "b/work/round-02/grpo-skipped.json").read_text())
    assert "probe not admitted" in marker["why"]


def test_every_grpo_stage_is_behind_the_gate():
    """No GRPO train, GRPO test, GRPO eval-2 or GRPO report runs without an adapter.

    GRPO_ADAPTER is assigned only in the gate's admit branch, so every stage
    that names a GRPO output must sit inside that branch or inside a
    `[ -n "$GRPO_ADAPTER" ]` block; with GRPO_STEPS=0 the gate never admits.
    """
    assigned = [line for line in TEXT.splitlines() if re.match(r'\s*GRPO_ADAPTER="\$ROUND', line)]
    assert len(assigned) == 1
    stack = []
    guarded = ('if [ "$GATE" -eq 0 ]', 'if [ -n "$GRPO_ADAPTER" ]')
    names = ("--output \"$ROUND/grpo", "$ROUND/grpo-test", "$ROUND/grpo-eval2",
             "base-vs-grpo")
    seen = 0
    shell = re.sub(r"<<'PYEOF'.*?\nPYEOF\n", "\n", TEXT, flags=re.S)   # bash only
    for line in shell.splitlines():
        stripped = line.strip()
        if stripped.startswith("if ") and not stripped.endswith("fi"):
            stack.append(any(stripped.startswith(g) for g in guarded))
        elif stripped.startswith(("elif ", "else")) and stack:
            stack[-1] = False
        elif stripped == "fi":
            stack.pop()
        if any(name in line for name in names) and not stripped.startswith("#"):
            seen += 1
            assert stack and stack[-1], "an ungated GRPO stage: " + stripped
    assert seen >= 5


def test_the_targets_are_checked_right_after_the_engine():
    """Lineage and split are free to check, so they are checked before the slow stages."""
    order = [TEXT.index("STAGE=" + s) for s in ("engine", "sft-targets", "handshake", "bank",
                                                  "review", "prepare", "plan")]
    assert order == sorted(order)
    targets = heredoc("  STAGE=sft-targets")
    assert "LYPNING_L_BIN" in targets and "ENGINE LINEAGE" in targets
    review = TEXT.index("STAGE=review")
    prepare = TEXT.index("STAGE=prepare")
    assert review < TEXT.index('targets_fit_split "$ROUND/reviewed-train', review) < prepare


def test_review_and_preparation_split_at_the_split_seed_and_training_uses_the_seed():
    """Two reviews and two preparations split; only the trainer's flags carry SEED."""
    assert TEXT.count('--seed "$SPLIT_SEED"') == 4
    assert TEXT.count('--seed "$SEED"') == 1
    common = re.search(r"COMMON=\(([^)]*)\)", TEXT).group(1)
    assert '--seed "$SEED"' in common


def test_targets_outside_the_split_are_refused_before_preparation(tmp_path):
    fit = heredoc('  python3 - "$1" <<\'PYEOF\'')
    (tmp_path / "work/round-02/sft-targets").mkdir(parents=True)
    rows = [{"case_id": "a"}, {"case_id": "b"}]
    (tmp_path / "work/round-02/sft-targets/sft.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps({"cases": [{"case_id": "a", "split": "train"},
                                            {"case_id": "b", "split": "train"}]}))
    ok = run_snippet(fit, tmp_path, {"SPLIT_SEED": "1111"}, str(bundle))
    assert ok.returncode == 0, ok.stderr
    bundle.write_text(json.dumps({"cases": [{"case_id": "a", "split": "train"},
                                            {"case_id": "b", "split": "dev"}]}))
    refused = run_snippet(fit, tmp_path, {"SPLIT_SEED": "1111"}, str(bundle))
    assert refused.returncode != 0 and "not train cases" in refused.stderr


def test_the_manifest_records_every_arm_field():
    """arm_check can only compare what finish() writes."""
    spec = importlib.util.spec_from_file_location("arm_check_fields",
                                                  ROOT / ".github/scripts/arm_check.py")
    arm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(arm)
    finish = TEXT[TEXT.index("finish() {"):TEXT.index("trap 'finish $?' EXIT")]
    written = set(re.findall(r'"(\w+)":', finish))
    assert set(arm.ARM_FIELDS) <= written, set(arm.ARM_FIELDS) - written
    # Copied from the stage's own record, not restated from intent.
    assert 'sft.get("kernels")' in finish
    assert '(sft.get("effective") or {}).get("learning_rate")' in finish
    # What the targets were drawn from and which kernel the stage bound.
    assert '"sft_target_arms": targets.get("arms")' in finish
    assert '"kernel_binding": sft.get("kernel_binding")' in finish


def test_the_split_seed_default_reaches_the_python_that_reads_it(tmp_path):
    """SPLIT_SEED defaults to 1111 and two heredocs read it from os.environ.

    Assigned without `export`, the default existed only in bash: a job
    started without SPLIT_SEED in its environment died with KeyError in the
    reused-bundle check, after the dependency install and the engine download.
    """
    head = TEXT[TEXT.index("set -euo pipefail"):TEXT.index('cd "$(dirname "$0")/../.."')]
    probe = head + 'python3 -c \'import os; print(os.environ["SPLIT_SEED"])\'\n'
    env = {k: v for k, v in os.environ.items() if k != "SPLIT_SEED"}
    env.update({k: "x" for k in ("SPACE_REPO", "SPACE_REV", "QWEN_REV", "WORK_REPO",
                                 "BANK_PATH", "HF_TOKEN")})
    done = subprocess.run(["bash", "-c", probe], env=env, capture_output=True, text=True,
                          timeout=60, cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "1111"

