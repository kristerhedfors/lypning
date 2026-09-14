"""What the legality endpoint must not be allowed to do quietly.

Three of these pin claims the module's docstring makes in prose. A prose claim
that no test holds is a claim that will be true until someone edits around it,
and the two that matter most here are both invisible at a glance: that the unit
of resampling is the CASE and not the program, and that the gates can fail.
"""

from __future__ import annotations

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
