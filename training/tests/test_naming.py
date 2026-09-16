"""The earlier target's name appears only in frozen evidence. This is the grep.

`SWITCH.md` states the rule: the target changed on 2026-09-11, and the name of
the model it changed *from* survives only where rewriting it would falsify a
record — the recorded run ids under ``training/runs/`` and their metadata, the
captured corpus and sightings JSONL, and the dated documents that quote them.
Everywhere else the tree says *the earlier target*, and nothing spells the name
as a live identifier: no directory, module, variable, path or command.

The directory this file sits in was itself the largest violation, and was
renamed ``nemotron`` -> ``training`` on 2026-09-16 (`CHANGELOG.md`). A rule
that cost a 208-file rename is worth a test, because the way it comes back is
not a decision — it is one path in one new workflow, copied from an old one.

**This file does not spell the name either.** It reads it off the recorded run
directory names, so there is no literal here to go stale, and so that deleting
the last piece of evidence deletes the rule rather than leaving a test policing
a word nobody can find. That is the same move `tests/test_docs.py` makes for
the two upstream engine names, which it reads out of README §8.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TRAINING = ROOT / "training"
RUNS = TRAINING / "runs"

#: Dated records that quote the recorded runs, including the free-text model
#: string the audit's arm-identity finding is *about*. Rewriting these would
#: make the ledger describe a branch, a model or a measurement that never was.
DATED_RECORDS = {"CHANGELOG.md", "training/AUDIT.md", "training/REFACTOR.md"}

#: Program text an agent actually typed, and the runs graded from it. Pinned by
#: hash; a rewrite here is a falsified capture, not a rename.
def _is_frozen_evidence(path: str) -> bool:
    return path.endswith(".jsonl") or path.startswith("training/runs/")


def _tracked_files():
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True,
                             capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover - no git
        pytest.skip("git is not available to enumerate tracked files")
    return [p for p in out.split("\0") if p]


def _recorded_run_ids():
    """Every recorded run directory whose name carries the earlier target's.

    The naming shape is ``baseline-<name><digits>-<rest>``; the run records the
    same string back as its own ``run_id``, which is why the directory cannot
    be renamed without making the run disagree with itself.
    """
    if not RUNS.is_dir():  # pragma: no cover - runs/ is committed
        pytest.skip("no recorded runs in this tree")
    return sorted((p.name for p in RUNS.iterdir() if p.is_dir()), key=len, reverse=True)


def _retired_name():
    for name in _recorded_run_ids():
        m = re.match(r"baseline-([a-z]{4,})\d", name)
        if m:
            return m.group(1)
    pytest.skip("no recorded run names the earlier target; the rule has no subject left")


def test_the_earlier_targets_name_is_only_in_frozen_evidence():
    name = _retired_name()
    run_ids = [r for r in _recorded_run_ids() if name in r.lower()]
    offenders = {}
    for rel in _tracked_files():
        if rel in DATED_RECORDS or _is_frozen_evidence(rel):
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        # A run id may be cited anywhere: it is the join key between a number
        # and its evidence (`CLAUDE.md` invariant 3), here and in the private
        # work dataset. What may not appear is the bare name.
        for run_id in run_ids:
            text = text.replace(run_id, "")
        if name in text.lower():
            line = next((i for i, l in enumerate(text.splitlines(), 1)
                         if name in l.lower()), 0)
            offenders[rel] = line
    assert not offenders, (
        "the earlier target's name is spelled outside the frozen evidence "
        "(`training/SWITCH.md` names its homes): "
        + ", ".join("%s:%d" % (f, n) for f, n in sorted(offenders.items())))


def test_no_tracked_path_spells_the_earlier_target_outside_runs():
    """The half that matters: the name must not *resolve* to anything.

    A directory named after a retired model is a name the tooling keeps typing
    — `PYTHONPATH`, a workflow path filter, a `cd` in a runbook — which is how
    it survived five days past the switch that retired it.
    """
    name = _retired_name()
    bad = [p for p in _tracked_files()
           if name in p.lower() and not p.startswith("training/runs/")]
    assert not bad, "a tracked path spells the earlier target: " + ", ".join(sorted(bad))


def test_switch_md_states_the_rule_without_spelling_the_name():
    switch = (TRAINING / "SWITCH.md").read_text(encoding="utf-8")
    assert _retired_name() not in switch.lower(), \
        "SWITCH.md is the home of the rule, not of the name"
    assert "the earlier target" in switch, \
        "SWITCH.md must name the phrase every other document uses instead"
