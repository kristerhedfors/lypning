"""The prompting study's artifacts, held to each other and to the engine.

`docs/PROMPTING.md` is a document made of numbers, and every one of them is
derived from files in `study/`. Three ways those files can rot without anything
else noticing, and this is where each is caught:

* **the capability brief drifts.** `study/prompts/capability-brief.md` is
  generated from `builtins.rs`, `methods.rs`, `modules.rs` and `route.rs`. Add a
  builtin and the brief silently describes the engine of two commits ago — and
  the treatment that scored best in the study becomes a treatment nobody ran.
* **a task loses its expectation.** `expect_stdout` and `tier1_feasible` are
  derived by ``study/bless.py`` from a reference solution. A task that acquires
  neither is a task no program can pass, and it would read as a prompt failing.
* **a MISMATCH creeps into the scored rows.** CLAUDE.md invariant 1 does not
  stop applying because the programs came from a study. 884 agent-written
  programs are a second conformance battery, and the one thing they must not
  show is the Rust core quietly disagreeing with CPython.

What this file deliberately does NOT do is re-run anything: scoring spawns two
interpreters per program and the corpus battery already owns that job. These are
consistency checks over committed files, and they are fast.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "study"

pytestmark = pytest.mark.skipif(
    not STUDY.is_dir(), reason="the study directory is not part of this checkout")


def _jsonl(path: Path) -> list:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_every_task_carries_a_derived_expectation() -> None:
    tasks = _jsonl(STUDY / "tasks.jsonl")
    assert tasks, "study/tasks.jsonl is empty"
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids)), "duplicate task id in study/tasks.jsonl"
    for t in tasks:
        assert "expect_stdout" in t, (
            "task %s has no expect_stdout — run `python3 study/bless.py`, which "
            "derives it from the reference solution rather than trusting a typed one"
            % t["id"])
        assert "tier1_feasible" in t, (
            "task %s has no tier1_feasible — without it the ceiling in "
            "docs/PROMPTING.md is an assumption instead of a measurement" % t["id"])
        assert t.get("reference"), "task %s has no reference solution" % t["id"]


def test_every_treatment_names_prompt_files_that_exist() -> None:
    treatments = json.loads((STUDY / "treatments.json").read_text(encoding="utf-8"))
    for tid, spec in treatments.items():
        for part in spec["parts"]:
            p = STUDY / "prompts" / part
            assert p.is_file(), (
                "treatment %s (%s) names study/prompts/%s, which is not there — a "
                "treatment whose prompt is missing cannot be reproduced or re-run"
                % (tid, spec["label"], part))


def test_the_capability_brief_still_matches_the_engines_tables() -> None:
    """Regenerate it and compare. A drifted brief measures the wrong engine."""
    brief = STUDY / "prompts" / "capability-brief.md"
    before = brief.read_text(encoding="utf-8")
    out = subprocess.run([sys.executable, str(STUDY / "gen_brief.py")],
                         cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    after = brief.read_text(encoding="utf-8")
    if after != before:
        brief.write_text(before, encoding="utf-8")   # leave the tree as we found it
    assert out.returncode == 0, "study/gen_brief.py failed:\n%s" % out.stderr[-800:]
    assert after == before, (
        "study/prompts/capability-brief.md is out of date with the engine's own "
        "tables. Re-run `python3 study/gen_brief.py` — and note that the study's "
        "T4/T6/T7/T8 numbers were measured against the OLD text, so either re-run "
        "those treatments or say in docs/PROMPTING.md which engine they describe.")


def test_the_generated_task_brief_matches_the_tasks() -> None:
    """Every task the battery has must appear in the brief the agents were handed."""
    brief = (STUDY / "task-brief.md").read_text(encoding="utf-8")
    for t in _jsonl(STUDY / "tasks.jsonl"):
        assert "`%s`" % t["id"] in brief, (
            "task %s is in study/tasks.jsonl but not in study/task-brief.md — run "
            "`python3 study/gen_taskbrief.py`. A task no agent was shown is a task "
            "every treatment scores zero on." % t["id"])
    assert "expect_stdout" not in brief, (
        "study/task-brief.md contains the string 'expect_stdout' — the brief is "
        "handed to the generating agents and must never carry the answers")


def test_the_scored_rows_cover_the_generated_programs() -> None:
    programs = _jsonl(STUDY / "data" / "programs.jsonl")
    results = _jsonl(STUDY / "data" / "results.jsonl")
    assert len(results) == len(programs), (
        "study/data has %d generated programs and %d scored rows — re-run "
        "`python3 study/score.py`" % (len(programs), len(results)))
    known = {t["id"] for t in _jsonl(STUDY / "tasks.jsonl")}
    for r in results:
        assert r["task"] in known, "scored row names unknown task %r" % r["task"]


def test_no_generated_program_made_the_engine_disagree_with_cpython() -> None:
    """Invariant 1, over the study's own corpus.

    A MISMATCH here means the same thing it means in `lypning conformance`: the
    Rust core answered, and answered differently from CPython, and the agent that
    typed the one-liner would never have known. Fix the engine, not this test.
    """
    bad = [r for r in _jsonl(STUDY / "data" / "results.jsonl") if r.get("verdict") == "MISMATCH"]
    assert not bad, "%d generated program(s) MISMATCH:\n%s" % (
        len(bad), "\n".join("  %s/%s: %s" % (r["treatment"], r["task"], r["program"][:120])
                            for r in bad[:5]))
