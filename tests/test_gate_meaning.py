"""Can a gate say WHY it is red?

Two mechanisms came out of that, and both fail silently if they regress, which
is the only reason a test can justify its existence here:

* :attr:`build.BuildResult.unavailable` — a precondition this machine does not
  meet, as distinct from a build that ran and broke. Collapse the two and the
  CI job loses the distinction again with no visible symptom.
* the accepted-mismatch ledger, which must accept by IDENTITY and never by
  count. A count-based ledger passes every test you would think to write for it
  right up until the day one defect is fixed and another appears."""

from __future__ import annotations

from lypning import build


# --- unavailable is not failed -----------------------------------------------


def test_a_missing_precondition_renders_as_unavailable_not_failed():
    # The exact string matters: "FAILED" sends a reader looking for a regression
    # that does not exist, which is how four runs of "no network" were read as a
    # wrong answer in the tier.
    r = build.BuildResult("lypning", target="i386-musl",
                          skipped_reason="no network", unavailable=True)
    line = build.report(r)
    assert "unavailable: no network" in line
    assert "FAILED" not in line


def test_a_build_that_ran_and_broke_still_renders_as_failed():
    r = build.BuildResult("lypning", target="i386-musl",
                          skipped_reason="the linker rejected it")
    line = build.report(r)
    assert "FAILED: the linker rejected it" in line
    assert "unavailable" not in line


def test_ok_is_never_reported_as_either():
    r = build.BuildResult("lypning", ok=True, size_bytes=1024, target="musl")
    assert "ok" in build.report(r)
    assert "FAILED" not in build.report(r) and "unavailable" not in build.report(r)
