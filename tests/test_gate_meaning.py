"""Can a gate say WHY it is red?

Every test here exists because of one incident: the MicroPython CI job failed to
build for four consecutive runs, and its red rendered identically whether the
tier had answered a program wrongly or `musl.libc.org` had merely stopped
answering. The whole battery underneath it silently never ran, so the mismatch
count drifted unnoticed — and a gate nobody can read is a gate nobody heeds.

Two mechanisms came out of that, and both fail silently if they regress, which
is the only reason a test can justify its existence here:

* :attr:`build.BuildResult.unavailable` — a precondition this machine does not
  meet, as distinct from a build that ran and broke. Collapse the two and the
  CI job loses the distinction again with no visible symptom.
* the accepted-mismatch ledger, which must accept by IDENTITY and never by
  count. A count-based ledger passes every test you would think to write for it
  right up until the day one defect is fixed and another appears.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from lypning import build


REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / ".github" / "known-mismatches.json"
CHECKER = REPO / ".github" / "scripts" / "known-mismatches.py"


# --- unavailable is not failed -----------------------------------------------


def test_a_missing_precondition_renders_as_unavailable_not_failed():
    # The exact string matters: "FAILED" sends a reader looking for a regression
    # that does not exist, which is how four runs of "no network" were read as a
    # wrong answer in the tier.
    r = build.BuildResult("lypning-mp", target="i386-musl",
                          skipped_reason="no network", unavailable=True)
    line = build.report(r)
    assert "unavailable: no network" in line
    assert "FAILED" not in line


def test_a_build_that_ran_and_broke_still_renders_as_failed():
    r = build.BuildResult("lypning-mp", target="i386-musl",
                          skipped_reason="the linker rejected it")
    line = build.report(r)
    assert "FAILED: the linker rejected it" in line
    assert "unavailable" not in line


def test_ok_is_never_reported_as_either():
    r = build.BuildResult("lypning", ok=True, size_bytes=1024, target="musl")
    assert "ok" in build.report(r)
    assert "FAILED" not in build.report(r) and "unavailable" not in build.report(r)


# --- whose fault was the failure ---------------------------------------------


@pytest.mark.parametrize("rc, out", [
    # The one that actually happened, twice, in front of two different people:
    # curl's exit 35 while a TCP connect to the same host:port still succeeds.
    (35, "curl: (35) Recv failure: Connection reset by peer"),
    (6, "curl: (6) Could not resolve host: musl.libc.org"),
    (7, "curl: (7) Failed to connect to musl.libc.org port 443"),
    (28, "curl: (28) Operation timed out"),
    (4, "wget: unable to resolve host address"),
    # The script caught it and called die(), so the code is a flat 1 and only
    # the message knows what happened.
    (1, "lypning-build: could not clone MicroPython v1.28.0"),
    (1, "fatal: unable to access 'https://github.com/...': Connection reset"),
])
def test_a_transport_failure_is_not_a_build_failure(rc, out):
    assert build._transport_failed(rc, out) is True


@pytest.mark.parametrize("rc, out", [
    (1, "lypning-build: the musl-i386 wrapper cannot link a static binary"),
    (1, "lypning-build: patch failed to apply: 0001-frozen-stdlib.patch"),
    (1, "lypning-build: static floor is 4096 B — this is not linking against musl"),
    (2, "lypning-build: unknown option --nope"),
    # A checksum mismatch is the one download failure that must NOT read as an
    # outage: the bytes arrived, and they were the wrong bytes.
    (1, "lypning-build: musl tarball checksum mismatch — refusing to build against it"),
])
def test_a_real_build_failure_is_not_excused_as_a_transport_one(rc, out):
    assert build._transport_failed(rc, out) is False


def test_the_pinned_host_is_named_once():
    # It is asked about before the build and again in the reason line; two
    # spellings would drift into two different answers.
    assert build._PINNED_HOST == "musl.libc.org"
    assert build._PINNED_HOST in (build.paths.SCRIPTS_DIR / "build-micropython.sh").read_text()


# --- the ledger ---------------------------------------------------------------


def _ledger():
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def _report(failures):
    return {"total": 1430, "engines": {"lypning-mp": {"failures": list(failures)}}}


def _run(report, ledger=None, tmp_path=None):
    rp = tmp_path / "report.json"
    rp.write_text(json.dumps(report) if not isinstance(report, str) else report)
    lp = LEDGER
    if ledger is not None:
        lp = tmp_path / "ledger.json"
        lp.write_text(json.dumps(ledger))
    return subprocess.run(
        [sys.executable, str(CHECKER), "--report", str(rp), "--ledger", str(lp)],
        capture_output=True, text=True)


def test_the_ledger_is_valid_json_and_accepts_by_identity():
    data = _ledger()
    for e in data["accepted"]:
        # Every field of the identity must be present, or two different defects
        # collapse into one ledger line and one of them goes quiet.
        assert e["engine"] and e["entry_id"] and e["kind"]
        assert e["why"], "an accepted mismatch without a reason is a waiver"
        assert e["class"] in ("defect", "corpus-artifact")
    # Never a count. This is the assertion the whole file exists for.
    assert "count" not in data and "expected" not in data


def test_a_mismatch_the_ledger_does_not_name_is_a_regression(tmp_path):
    r = _run(_report([{"engine": "lypning-mp", "entry_id": "py-new", "kind": "stdout",
                       "detail": "want 'a', got 'b'"}]), tmp_path=tmp_path)
    assert r.returncode == 1
    assert "NOT IN THE LEDGER" in r.stdout and "py-new" in r.stdout


def test_a_ledger_entry_that_stopped_reproducing_is_also_red(tmp_path):
    # Good news still has to be acted on: a ledger nobody prunes waives things
    # nobody remembers.
    r = _run(_report([]), ledger={"accepted": [
        {"engine": "lypning-mp", "entry_id": "py-fixed", "kind": "contract",
         "class": "defect", "why": "the commit barrier"}]}, tmp_path=tmp_path)
    assert r.returncode == 1
    assert "GOOD NEWS" in r.stdout and "py-fixed" in r.stdout


def test_exactly_the_accepted_set_is_green(tmp_path):
    accepted = [{"engine": "lypning-mp", "entry_id": "py-a", "kind": "contract",
                 "class": "defect", "why": "the commit barrier"}]
    r = _run(_report([dict(accepted[0], detail="refused after 54 byte(s)")]),
             ledger={"accepted": accepted}, tmp_path=tmp_path)
    assert r.returncode == 0


def test_swapping_one_defect_for_another_is_not_green(tmp_path):
    # THE test. A count-based ledger passes this and it must not: one accepted
    # mismatch disappeared and one unknown one arrived, so the total is
    # unchanged and two separate things are wrong.
    accepted = [{"engine": "lypning-mp", "entry_id": "py-a", "kind": "contract",
                 "class": "defect", "why": "the commit barrier"}]
    r = _run(_report([{"engine": "lypning-mp", "entry_id": "py-b", "kind": "stdout",
                       "detail": "a wrong answer"}]),
             ledger={"accepted": accepted}, tmp_path=tmp_path)
    assert r.returncode == 1
    assert "NOT IN THE LEDGER" in r.stdout and "GOOD NEWS" in r.stdout


def test_a_battery_that_died_is_not_scored_as_clean(tmp_path):
    # A crashed conformance run leaves a truncated file, and reading that as
    # "no mismatches observed" turns a dead battery into a green tick.
    for bad in ("{trunc", json.dumps({"total": 1430}), json.dumps({"engines": {}})):
        r = _run(bad if bad.startswith("{tr") else json.loads(bad), tmp_path=tmp_path)
        assert r.returncode == 1, bad
