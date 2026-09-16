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


def test_replay_cache_pins_engine_programs_and_test_context(tmp_path, monkeypatch):
    monkeypatch.setattr(legality.eng, "identity", lambda: {"fingerprint": "same-installed-binaries"})
    calls = []
    def census(attempts, engine, tests=None, workers=1):
        calls.append((attempts, engine, tests))
        return {"call": len(calls)}
    monkeypatch.setattr(legality.refusals, "on_policy", census)
    cache = tmp_path / "replay.json"
    attempts = [{"case_id": "a", "program": "print(1)"}]
    assert legality.replay(attempts, "core", cache=cache) == {"call": 1}
    assert legality.replay(attempts, "core", cache=cache) == {"call": 1}
    assert legality.replay(attempts, "large", cache=cache) == {"call": 2}
    assert legality.replay(attempts, "large", tests={"a": {"stdin": "changed"}}, cache=cache) == {"call": 3}
    assert legality.replay([dict(attempts[0], program="print(2)")], "large", cache=cache) == {"call": 4}


# --- native: correct AND the engine ran it ------------------------------------


def _armed(rows, tests, monkeypatch):
    monkeypatch.setattr(legality.refusals, "on_policy",
                        lambda attempts, engine, tests=None, workers=1: _census(rows))
    monkeypatch.setattr(legality.eng, "identity", lambda: {"fingerprint": "fp"})
    attempts = [{"case_id": r["case_id"], "sample": r["sample"], "program": "p",
                 "passed": r["passed"], "completion_tokens": 10} for r in rows]
    return legality.arm(attempts, "e", tests=tests)


def test_native_rate_is_match_and_correct_read_off_the_replay(monkeypatch):
    """A draw that passed on CPython and was refused is legal-rate 0, pass-rate 1,
    native 0: the eval-2 primary metric needs both halves from the same engine."""
    rows = [_draw("a", "MATCH", True, sample=0), _draw("a", "UNSUPPORTED", None, sample=1),
            _draw("b", "MATCH", False, sample=0), _draw("b", "MATCH", True, sample=1)]
    rows[1]["passed"] = True                      # correct on CPython, refused
    tests = {"a": {"expect_stdout": "x"}, "b": {"expect_stdout": "x"}}
    a = _armed(rows, tests, monkeypatch)
    assert a["native_rate"] == {"a": 0.5, "b": 0.5}
    assert a["pass_rate"]["a"] == 1.0 and a["legal_rate"]["a"] == 0.5
    assert a["native"] == pytest.approx(0.5) and a["measured"] == 4


def test_a_case_without_an_expected_stdout_is_unmeasured_not_zero(monkeypatch):
    rows = [_draw("scored", "MATCH", True), _draw("free", "MATCH", None)]
    tests = {"scored": {"expect_stdout": "x"}, "free": {}}
    a = _armed(rows, tests, monkeypatch)
    assert a["native_rate"] == {"scored": 1.0}
    assert a["native"] == 1.0
    # Every draw refused: measurable is a property of the case, so it stays in at 0.
    a = _armed([_draw("z", "UNSUPPORTED"), _draw("z", "UNSUPPORTED", sample=1)],
               {"z": {"expect_stdout": "x"}}, monkeypatch)
    assert a["native_rate"] == {"z": 0.0}
    # No tests at all: nothing is measurable, so the rate is a hole and not a zero.
    a = _armed([_draw("q", "MATCH", None)], None, monkeypatch)
    assert a["native_rate"] == {} and a["native"] != a["native"]


def test_compare_and_report_carry_native_beside_slr():
    a = _arm({"c1": 0.5, "c2": 0.5})
    b = _arm({"c1": 0.5, "c2": 0.5})
    a.update(native_rate={"c1": 0.0, "c2": 0.5}, native=0.25)
    b.update(native_rate={"c1": 1.0, "c2": 0.5}, native=0.75)
    cmp = legality.compare(a, b)
    assert cmp["native"]["n_pairs"] == 2 and cmp["native"]["delta"] == pytest.approx(0.5)
    assert cmp["base_native"] == 0.25 and cmp["tuned_native"] == 0.75
    text = legality.report(cmp, before="a", after="b")
    lines = text.splitlines()
    slr = next(i for i, l in enumerate(lines) if l.startswith("  SLR "))
    assert lines[slr + 2].startswith("  native  base 25.00%   tuned 75.00%")
    assert "dnative +50.00pp" in lines[slr + 3]
    # An arm from before native was measured: a hole in the report, never a zero.
    old = legality.compare(_arm({"c1": 0.5}), _arm({"c1": 0.5}))
    assert old["native"]["n_pairs"] == 0
    assert "dnative not measured" in legality.report(old, before="a", after="b")
