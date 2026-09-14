"""What the legality endpoint must not be allowed to do quietly.

Three of these pin claims the module's docstring makes in prose. A prose claim
that no test holds is a claim that will be true until someone edits around it,
and the two that matter most here are both invisible at a glance: that the unit
of resampling is the CASE and not the program, and that the gates can fail.
"""

from __future__ import annotations

import json
import math

import pytest

from pipeline import legality


def _arm(legal_by_case, pass_by_case=None, tokens=None, fingerprint="fp"):
    """A minimal arm, as :func:`legality.arm` would return one."""
    return {
        "engine": "e", "fingerprint": fingerprint,
        "programs": sum(1 for _ in legal_by_case) * 2, "cases": len(legal_by_case),
        "slr": sum(legal_by_case.values()) / len(legal_by_case),
        "mismatch": 0, "error": 0, "by_kind": {}, "blockers": {},
        "legal_rate": dict(legal_by_case),
        "pass_rate": dict(pass_by_case or legal_by_case),
        "tokens": dict(tokens or {c: 100.0 for c in legal_by_case}),
        "programs_by_case": {}, "rows": [],
    }


def test_two_engines_are_never_one_comparison():
    """The whole endpoint is relative to the engine; crossing two voids it."""
    a = _arm({"c1": 0.5}, fingerprint="aaa")
    b = _arm({"c1": 0.9}, fingerprint="bbb")
    with pytest.raises(ValueError) as exc:
        legality.compare(a, b)
    assert "aaa" in str(exc.value) and "bbb" in str(exc.value)


def test_the_bootstrap_unit_is_the_case():
    """k draws of one task are one observation, not k.

    Pinned by construction: two arms whose per-case rates are identical give a
    zero delta over exactly the shared cases, and ``n_pairs`` counts cases. If
    someone rewires this to resample programs, ``n_pairs`` stops matching the
    case count and this fails — which is the only cheap way to notice, because
    resampling programs does not error, it just returns a narrower interval.
    """
    rates = {"c1": 0.25, "c2": 0.75, "c3": 1.0}
    cmp = legality.compare(_arm(rates), _arm(rates))
    assert cmp["delta"]["n_pairs"] == 3
    assert cmp["delta"]["delta"] == pytest.approx(0.0)


def test_gate_a_fails_when_correctness_is_traded_for_legality():
    """The degenerate maximum this gate exists to block: legal and wrong."""
    base = _arm({"c%d" % i: 0.0 for i in range(10)},
                pass_by_case={"c%d" % i: 0.5 for i in range(10)})
    tuned = _arm({"c%d" % i: 1.0 for i in range(10)},
                 pass_by_case={"c%d" % i: 0.2 for i in range(10)})
    cmp = legality.compare(base, tuned)
    assert cmp["delta"]["delta"] == pytest.approx(1.0)   # SLR maxed out
    gate_a = [g for g in cmp["gates"] if g["name"].startswith("A")][0]
    assert gate_a["pass"] is False
    assert cmp["gates_pass"] is False                    # and the result is void


def test_gate_c_fails_when_the_rewrite_costs_tokens():
    """A hand-rolled scan is legal and can cost more than the spawn it saves."""
    cases = {"c%d" % i: 1.0 for i in range(5)}
    cmp = legality.compare(_arm(cases, tokens={c: 100.0 for c in cases}),
                           _arm(cases, tokens={c: 200.0 for c in cases}))
    gate_c = [g for g in cmp["gates"] if g["name"].startswith("C")][0]
    assert gate_c["growth"] == pytest.approx(1.0)
    assert gate_c["pass"] is False


def test_gate_b_is_unmeasured_rather_than_passed_without_cases():
    """A hole in the table, never a zero — and never a silent PASS."""
    cases = {"c1": 1.0}
    cmp = legality.compare(_arm(cases), _arm(cases), None)
    gate_b = [g for g in cmp["gates"] if g["name"].startswith("B")][0]
    assert gate_b.get("unmeasured") is True
    assert gate_b["pass"] is False


def test_imports_in_reads_both_spellings():
    assert legality.imports_in("import os, sys\nprint(1)") == {"os", "sys"}
    assert legality.imports_in("from os.path import join") == {"os"}
    assert legality.imports_in("import os.path as p") == {"os"}
    assert legality.imports_in("print('import re')") == set()


def test_mismatch_makes_the_number_unreportable():
    """Invariant 1 reaches this module too: a silent wrong answer voids the run."""
    a = _arm({"c1": 1.0})
    b = _arm({"c1": 1.0})
    b["mismatch"] = 1
    cmp = legality.compare(a, b)
    assert cmp["mismatch"] == 1
    assert "MISMATCH 1" in legality.report(cmp, before="a", after="b")


# --- reachability -------------------------------------------------------------


def _census(rows):
    tally = {}
    for r in rows:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    return {"engine": "e", "programs": len(rows), "tally": tally,
            "details": {}, "blockers": {}, "rows": rows}


def _draw(case_id, verdict, correct=None, sample=0):
    return {"case_id": case_id, "sample": sample, "passed": bool(correct),
            "verdict": verdict, "correct": correct, "detail": "d", "blocker": "b"}


def _reach(rows, tests, monkeypatch):
    monkeypatch.setattr(legality.refusals, "on_policy",
                        lambda attempts, engine, tests=None, workers=1: _census(rows))
    monkeypatch.setattr(legality.eng, "identity", lambda: {"fingerprint": "fp"})
    monkeypatch.setattr(legality.refusals, "closed_kinds", lambda: frozenset(["set-order"]))
    attempts = [{"case_id": r["case_id"], "program": "p"} for r in rows]
    return legality.reachability(attempts, "e", tests=tests,
                                 kinds={c: "refused:module" for c in
                                        {r["case_id"] for r in rows}})


def test_pass_at_k_is_the_estimator_and_not_c_over_n():
    """One success in sixteen draws is not a 100% pass@8, and saying so is the
    whole reason a k appears in the name."""
    assert legality.pass_at_k(16, 1, 8) == pytest.approx(0.5)
    assert legality.pass_at_k(16, 0, 4) == 0.0
    assert legality.pass_at_k(16, 16, 1) == 1.0
    assert legality.pass_at_k(4, 1, 1) == pytest.approx(0.25)
    # A budget bigger than the evidence is not an answer, it is a nan.
    assert legality.pass_at_k(4, 1, 8) != legality.pass_at_k(4, 1, 8)


def test_the_three_tiers_come_off_the_test_and_absence_is_not_false():
    assert legality.tier_of({"require_tier1": True}) == legality.TIER_REQUIRED
    assert legality.tier_of({"require_tier1": False}) == legality.TIER_FALLBACK
    # A `stdout`-kind case never mentions the engine. Reading that as "tier 1 not
    # required" would claim falling back is RIGHT there, which nothing said.
    assert legality.tier_of({"expect_stdout": "x"}) == legality.TIER_FREE
    assert legality.tier_of(None) == legality.TIER_FREE


def test_the_floor_reads_only_the_cases_that_require_tier_1(monkeypatch):
    """A ceiling case the model never got into the subset is not a miss.

    Its reference solution IS the program the engine refuses, so folding it in
    scores the model for failing to do the wrong thing — and moves the headline
    by the corpus mix rather than by the model.
    """
    rows = [_draw("rewrite", "MATCH", True), _draw("rewrite", "UNSUPPORTED"),
            _draw("ceiling1", "UNSUPPORTED"), _draw("ceiling2", "UNSUPPORTED"),
            _draw("free", "UNSUPPORTED")]
    tests = {"rewrite": {"require_tier1": True, "expect_stdout": "x"},
             "ceiling1": {"require_tier1": False, "expect_stdout": "x"},
             "ceiling2": {"require_tier1": False, "expect_stdout": "x"},
             "free": {"expect_stdout": "x"}}
    r = _reach(rows, tests, monkeypatch)
    assert r["cases"] == 1 and r["reachable"] == 1.0
    assert r["fallback"]["cases"] == 2 and r["fallback"]["reached"] == 0
    assert r["free"]["cases"] == 1


def test_a_case_whose_every_draw_was_refused_is_unreachable_not_unmeasured(monkeypatch):
    """Scorability is a property of the case, not of how its draws came back.

    Reading it off "did any draw report a correct flag" files the very worst
    cases — the ones nothing legal ever came back for — as blanks in the table.
    """
    rows = [_draw("a", "UNSUPPORTED"), _draw("a", "UNSUPPORTED")]
    r = _reach(rows, {"a": {"require_tier1": True, "expect_stdout": "x"}}, monkeypatch)
    assert r["scored"] == 1
    assert r["rewardable"] == 0.0
    assert r["unreachable"] == ["a"]


def test_unstable_is_legal_never_correct_and_never_a_mismatch(monkeypatch):
    """The engine ran it, so it is legal; nothing reproduces it, so nothing is right."""
    rows = [_draw("a", "UNSTABLE", None)]
    r = _reach(rows, {"a": {"require_tier1": True, "expect_stdout": "x"}}, monkeypatch)
    assert r["reachable"] == 1.0
    assert r["rewardable"] == 0.0
    assert r["mismatch"] == 0 and r["unstable"] == 1


def test_a_harness_error_leaves_the_denominator(monkeypatch):
    rows = [_draw("a", "ERROR"), _draw("a", "MATCH", True)]
    r = _reach(rows, {"a": {"require_tier1": True, "expect_stdout": "x"}}, monkeypatch)
    assert r["draws"] == 1 and r["errors"] == 1
    assert r["reachable"] == 1.0


def test_the_report_says_below_when_it_is_below(monkeypatch):
    rows = [_draw("a", "UNSUPPORTED"), _draw("b", "MATCH", True)]
    tests = {c: {"require_tier1": True, "expect_stdout": "x"} for c in "ab"}
    r = _reach(rows, tests, monkeypatch)
    assert r["reachable"] == pytest.approx(0.5)
    text = legality.reachability_report(r, label="t", floor=0.60)
    assert "BELOW" in text and "advantage of zero" in text
    assert "at or above" in legality.reachability_report(r, label="t", floor=0.40)


def test_a_replay_cache_is_not_reused_across_a_grader_change(tmp_path, monkeypatch):
    """The fingerprint is the engine's half of a verdict. The grader is the other.

    The day UNSTABLE was split out of MISMATCH, every cached census still said
    MISMATCH and every fingerprint still matched.
    """
    monkeypatch.setattr(legality.eng, "identity", lambda: {"fingerprint": "fp"})
    cache = tmp_path / "r.json"
    cache.write_text(json.dumps({"fingerprint": "fp", "grader": -1,
                                 "census": {"rows": ["stale"]}}), encoding="utf-8")
    fresh = _census([_draw("a", "MATCH", True)])
    monkeypatch.setattr(legality.refusals, "on_policy",
                        lambda attempts, engine, tests=None, workers=1: fresh)
    got = legality.replay([{"case_id": "a", "program": "p"}], "e", cache=cache)
    assert got == fresh
    assert json.loads(cache.read_text())["grader"] == legality.refusals.GRADER
