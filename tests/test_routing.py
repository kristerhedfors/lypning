"""Routing safety: where the classifier SENDS a program against where it could go.

Conformance asks whether an engine agreed with CPython. This asks the other
question — whether the program was ever handed to that engine — and grades the
gap with a deliberately asymmetric vocabulary:

  UNSAFE  routed to an engine that MISMATCHES. Fatal. The mixture's whole claim
          is that a wrong route costs a spawn and never an answer.
  WASTED  routed to an engine that refuses when a cheaper one would have run it.
          One spawn.
  LATE    routed higher up the ladder than necessary. The difference in run time."""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import pytest

from lypning import conformance as conf
from lypning import corpus
from lypning import engines as eng
from lypning import routing
from lypning.conformance import MATCH, MISMATCH, UNSUPPORTED
from lypning.routing import IDEAL, LATE, NO_ENGINE, UNSAFE, WASTED

LADDER = tuple(eng.ENGINE_ORDER)

#: How much of the corpus the end-to-end test grades. The whole corpus is the
#: CLI's job — `lypning conformance` runs it, prints the routing table and exits
#: 1 on an UNSAFE — because a full battery is seconds of subprocesses and this
#: suite is four. What is asserted here is that the grading machinery agrees
#: with a live battery; what is *measured* is printed by the tool.
CORPUS_SLICE = 200


# --- the scoring rule (nothing built) ----------------------------------------


def test_the_cheapest_matching_engine_is_ideal():
    by = {eng.LYPNING: MATCH, eng.LYPNING_L: MATCH, eng.CPYTHON: MATCH}
    assert routing.score_route(eng.LYPNING, by, LADDER).grade == IDEAL
    # Correct answer, wrong bill: both of these matched, and both cost more than
    # the tier that also would have.
    assert routing.score_route(eng.LYPNING_L, by, LADDER).grade == LATE
    assert routing.score_route(eng.CPYTHON, by, LADDER).grade == LATE


def _refusal_kind(program):
    """The kind tier 1 refuses ``program`` with, or "" if it ran it."""
    r = eng.run(eng.LYPNING, program)
    return r.refusal[0] if r.refused else ""


def test_a_semantic_refusal_skips_every_tier_but_cpython():
    assert eng.chain_after_refusal(eng.LYPNING, "decorator") == [eng.CPYTHON]
    assert eng.chain_after_refusal(eng.LYPNING, "nan-identity") == [eng.CPYTHON]
    # An unknown kind falls through, which is the safe default for the cost: a
    # kind nobody classified costs a spawn, never an answer it could not have
    # got right anyway.
    assert eng.chain_after_refusal(eng.LYPNING, "kind-nobody-wrote") == [eng.CPYTHON]
    # A larger sibling with no capability the refusing one lacks is not tried:
    # it cannot answer what the smaller one could not.
    assert eng.chain_after_refusal(eng.LYPNING_L, "nan-identity") == [eng.CPYTHON]




def test_both_dispatchers_walk_the_same_chain_after_a_runtime_refusal(lypning_bin):
    """The Rust dispatcher's `chain_after` against Python's `chain_after_refusal`.

    Over the cross product of (every rung that can refuse) x (every kind the
    evaluator or the classifier can emit, plus a few that nothing emits) x
    (programs whose imports fit no tier, the middle tier, or every tier). The
    Rust answer is `lypning route --next --after E --kind K -c PROG`; the
    Python answer is computed from the same route's verdicts. Two dispatchers,
    one rule — this is what holds them to it.
    """
    import json as _json
    import subprocess as _sp
    kinds = sorted(set(routing.classifier_kinds() or []) | set(eng.ONLY_CPYTHON_REFUSALS)
                   | {"bigint", "format-spec", "bytes", "random", "float-sum", "nonesuch"})
    programs = ["print(1)", "import os\nprint(os.sep)", "import re\nprint(re)", "import subprocess\nprint(1)",
                "import random\nrandom.seed(1)\nprint(random.random())"]
    checked = 0
    for prog in programs:
        r = eng.route(prog, binary=lypning_bin)
        assert r.kind != eng.ROUTE_UNKNOWN_ENGINE
        for after in eng.ENGINE_ORDER[:-1]:
            for kind in kinds:
                out = _sp.run([str(lypning_bin), "route", "--next", "--after", after, "--kind", kind, "-c", prog],
                              capture_output=True, text=True, timeout=60)
                assert out.returncode == 0, out.stderr
                rust = _json.loads(out.stdout)
                py = eng.chain_after_refusal(after, kind, r.imports, r.verdicts)
                assert rust == py, "after %s kind %s prog %r: rust %r, python %r" % (after, kind, prog, rust, py)
                checked += 1
    assert checked >= 100


def test_route_json_carries_a_verdict_per_rung(lypning_bin):
    r = eng.route("import re\nprint(1)", binary=lypning_bin)
    assert [v[0] for v in r.verdicts] == list(eng.ENGINE_ORDER)
    assert r.verdicts[0][1:] == ("module", "import re")   # this binary refuses
    assert r.verdicts[-1] == ("cpython", "", "")            # CPython always can
    assert r.engine == eng.LYPNING_L                         # first "can run" at or above self


def test_both_dispatchers_read_the_same_escalation_table():
    """There are TWO dispatchers, and the rule was added to one of them.

    `engines.dispatch` is the Python one — the one `lypning conformance`
    measures through its `mixture` arm. `main.rs::dispatch` is the Rust one,
    which is what `lypning run` executes and what `lypning bench` times. The
    escalation rule went into the Python half only, so the correctness gate
    tested a dispatcher users do not run and the cost gate ran a dispatcher
    nothing checked. Measured through the binary at the time:

        lypning run -c 'print({3,1,2})'      {3, 1, 2}   CPython {1, 2, 3}"""
    rust = routing.only_cpython_kinds()
    if not rust:
        pytest.skip("ONLY_CPYTHON_KINDS was not found in %s" % routing.table_source())
    assert rust == sorted(rust), "the table is read by eye; keep it sorted"
    assert set(rust) == set(eng.ONLY_CPYTHON_REFUSALS), (
        "the two dispatchers disagree about which refusals go straight to CPython: "
        "route.rs has %s, engines.py has %s"
        % (sorted(set(rust) - set(eng.ONLY_CPYTHON_REFUSALS)),
           sorted(set(eng.ONLY_CPYTHON_REFUSALS) - set(rust)))
    )


@pytest.mark.parametrize("program,want", [
    ("print({3,1,2})", "{1, 2, 3}"),
    ("x = float('nan')\nprint(x in [x])", "True"),
    ("print(9007199254740993 / 3)", "3002399751580331.0"),
])
def test_the_rust_dispatcher_escalates_too(program, want, lypning_bin):
    """The gate that was missing, run through the binary rather than the battery."""
    import subprocess
    got = subprocess.run(
        [str(lypning_bin), "run", "-c", program],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "LYPNING_CAPTURE": "0"},
    )
    assert got.returncode == 0, got.stderr[-300:]
    assert got.stdout.strip() == want, (
        "the Rust dispatcher answered %r; CPython answers %r" % (got.stdout.strip(), want))


def test_the_rust_dispatcher_still_falls_through_for_a_capability_gap(lypning_bin):
    """The other direction, which bounds what the table may cost."""
    import subprocess
    got = subprocess.run(
        [str(lypning_bin), "run", "-c", "print(2**70)"],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "LYPNING_CAPTURE": "0"},
    )
    assert got.stdout.strip() == "1180591620717411303424"


def test_a_construct_the_runtime_table_escalates_is_answered_right_if_late(lypning_bin):
    """The hole between the two tables, and what is actually guaranteed across it.

    The assertion passed anyway, for a reason that had nothing to do with what
    it said: `import math` was an unserved module, so the static MODULE blocker
    sent the program to CPython and the marker was never consulted. Serving
    `math` removed the prop and the claim fell over. Measured on the binary
    before `math.rs` landed, the same programs with the import dropped, or with
    `import json` in its place, already routed to `lypning` — so the marker was
    never there.

    What IS guaranteed, and is what the mixture actually promises: the chain's
    ANSWER matches CPython's. Tier 1 refuses at run time, nothing reaches stdout
    before the refusal, and some rung above answers correctly. That costs one
    tier-1 spawn — `WASTED`, not `UNSAFE`, which is the distinction this file's
    vocabulary exists to draw. Restoring a static marker would buy that spawn
    back; it is a change to `route::walk_expr` touching programs with nothing to
    do with `math`, and it is a separate step."""
    # The rung each one lands on differs, and the difference is itself the
    # point: `nan-identity` is in ONLY_CPYTHON_REFUSALS so it skips every Rust
    # variant, while `int-div-precision` left that set when `cap-bigint` landed
    # — `bigint.div_exact` rounds once from the integers, so `lypning-l` answers
    # it and one spawn is saved. Both are WASTED, neither is UNSAFE.
    for program, expect_kind, expect_engine in (
        ("import math\nx = float('nan')\nprint(x in [x])", "nan-identity", eng.CPYTHON),
        ("import math\nprint(9007199254740993 / 3)", "int-div-precision", eng.LYPNING_L),
    ):
        # tier 1 refuses at run time, with the kind that decides the chain...
        kind = _refusal_kind(program)
        assert kind == expect_kind, "%r refused as %r" % (program, kind)

        # ...nothing escapes before the refusal, so the rerun cannot double up...
        r = eng.run(eng.LYPNING, program, binary=lypning_bin)
        assert r.stdout == "", "output escaped ahead of a refusal"

        # ...and the answer the caller actually gets is CPython's.
        theirs = eng.run(eng.CPYTHON, program)
        if theirs.returncode == 127:
            pytest.skip("no reference CPython")
        ours = eng.dispatch(program).result
        assert ours.stdout == theirs.stdout
        assert ours.returncode == theirs.returncode
        assert ours.engine == expect_engine




def test_the_integer_refusals_split_by_what_the_tier_below_can_do(lypning_bin):
    """One kind, two populations, and only one of them is a subtlety.

    They shared a kind until this session, and escalating that kind sent all
    eleven of the corpus' `bigint` refusals to CPython to rescue the one."""
    assert _refusal_kind("print(2**70)") == "bigint"
    assert _refusal_kind("print(9007199254740993/3)") == "int-div-precision"
    assert eng.dispatch("print(2**70)").engine == eng.LYPNING_L
    assert eng.dispatch("print(9007199254740993/3)").engine == eng.LYPNING_L


def test_every_escalated_refusal_kind_is_one_an_engine_actually_emits():
    # A table is only as honest as the thing that checks it, and this one fails
    # SILENTLY: a kind misspelled here never matches a refusal, so the
    # escalation simply never happens and the corpus goes on getting the wrong
    # answer with a green suite. Check each name against the source that emits
    # it. Skips rather than fails where the Rust tree did not ship.
    src = Path(__file__).resolve().parents[1] / "src/lypning/assets/rust/src"
    if not src.is_dir():
        pytest.skip("no Rust source tree in this install shape")
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in sorted(src.glob("*.rs")))
    missing = sorted(k for k in eng.ONLY_CPYTHON_REFUSALS if '"%s"' % k not in text)
    assert not missing, (
        "these refusal kinds are escalated to CPython but no engine emits them, "
        "so the escalation is dead code: %s" % missing)


def test_a_refusal_that_falls_through_to_a_wrong_answer_is_unsafe_not_wasted(monkeypatch):
    # The rule this file exists for, and the one that was missing. A refusal is
    # not an outcome: the dispatcher moves up the spectrum and the NEXT rung's
    # answer is what the user sees. Here tier 1 refuses CORRECTLY — it knows it
    # cannot match CPython on this construct — and the larger variant then
    # answers wrongly at exit 0. Grading the rung that was NAMED reads that as a
    # spare spawn; grading the rung that ANSWERED reads it as what it is.
    #
    # The larger variant is only IN the chain when it is strictly more capable,
    # so the caps and the verdicts both have to say so — which is the same
    # condition the dispatcher itself checks.
    monkeypatch.setattr(eng, "VARIANT_CAPS", {eng.LYPNING: (), eng.LYPNING_L: ("cap-bigint",)})
    verdicts = ((eng.LYPNING, "bigint", "x"), (eng.LYPNING_L, "", ""), (eng.CPYTHON, "", ""))
    by = {eng.LYPNING: UNSUPPORTED, eng.LYPNING_L: MISMATCH, eng.CPYTHON: MATCH}
    s = routing.score_route(eng.LYPNING, by, LADDER, route_verdicts=verdicts)
    assert s.grade == UNSAFE
    assert eng.LYPNING_L in s.detail, "the grade has to name the rung that actually answered"


def test_a_refusal_that_falls_through_to_a_right_answer_is_still_only_wasted(monkeypatch):
    # The guard on the rule above. Falling through is the design and costs one
    # spawn; it is only fatal when the rung that catches the fall is wrong.
    monkeypatch.setattr(eng, "VARIANT_CAPS", {eng.LYPNING: (), eng.LYPNING_L: ("cap-bigint",)})
    verdicts = ((eng.LYPNING, "bigint", "x"), (eng.LYPNING_L, "", ""), (eng.CPYTHON, "", ""))
    by = {eng.LYPNING: UNSUPPORTED, eng.LYPNING_L: MATCH, eng.CPYTHON: MATCH}
    assert routing.score_route(eng.LYPNING, by, LADDER, route_verdicts=verdicts).grade == WASTED


def test_a_same_caps_sibling_is_not_in_the_chain_at_all():
    # With identical capabilities the larger variant cannot answer what the
    # smaller one refused, so it is never tried and its verdict cannot be
    # delivered — the refusal goes straight to CPython. This is what makes the
    # spectrum's first N=2 step behaviour-free.
    by = {eng.LYPNING: UNSUPPORTED, eng.LYPNING_L: MISMATCH, eng.CPYTHON: MATCH}
    assert routing.score_route(eng.LYPNING, by, LADDER).grade == WASTED


def test_the_fall_through_skips_tiers_that_also_refused():
    # Two refusals in a row is still one delivered answer, and it is CPython's.
    by = {eng.LYPNING: UNSUPPORTED, eng.LYPNING_L: UNSUPPORTED, eng.CPYTHON: MATCH}
    assert routing.score_route(eng.LYPNING, by, LADDER).grade == WASTED
    # ...and a tier that was never measured is skipped like one that is not built,
    # rather than counted as the answer.
    by = {eng.LYPNING: UNSUPPORTED, eng.CPYTHON: MISMATCH}
    assert routing.score_route(eng.LYPNING, by, LADDER).grade == NO_ENGINE


def test_a_refusal_is_wasted_and_a_wrong_answer_is_unsafe():
    by = {eng.LYPNING: UNSUPPORTED, eng.LYPNING_L: MISMATCH, eng.CPYTHON: MATCH}
    assert routing.score_route(eng.LYPNING_L, by, LADDER).grade == UNSAFE
    assert routing.score_route(eng.CPYTHON, by, LADDER).grade == IDEAL
    # A refusal at the tier ABOVE the wrong one never reaches it.
    by2 = {eng.LYPNING: MISMATCH, eng.LYPNING_L: UNSUPPORTED, eng.CPYTHON: MATCH}
    assert routing.score_route(eng.LYPNING_L, by2, LADDER).grade == WASTED


def test_a_route_that_is_both_wrong_and_ideal_is_still_unsafe():
    # The ideal engine is the cheapest that MATCHED, so an engine that
    # mismatches can never be it — but the rule is ordered so that a mismatch is
    # read before anything else, and that order is the gate. Pin it.
    by = {eng.LYPNING: MISMATCH, eng.LYPNING_L: MATCH}
    s = routing.score_route(eng.LYPNING, by, LADDER)
    assert s.grade == UNSAFE
    assert s.ideal == eng.LYPNING_L, "an UNSAFE route still names where it should have gone"


def test_nothing_to_grade_when_no_engine_matched():
    # Every tier refused: the program is outside the mixture, which is a
    # coverage number and not the classifier's fault. Counting it against the
    # classifier would make an engine's gap look like a routing bug.
    by = {eng.LYPNING: UNSUPPORTED, eng.LYPNING_L: UNSUPPORTED, eng.CPYTHON: UNSUPPORTED}
    s = routing.score_route(eng.LYPNING, by, LADDER)
    assert s.grade == NO_ENGINE
    assert s.ideal == ""


def test_a_route_to_an_engine_that_does_not_exist_is_unsafe_not_unscored():
    # The classifier named a destination nothing measured. "I cannot tell
    # whether this route is safe" does not read as "safe".
    s = routing.score_route("pypy", {eng.LYPNING: MATCH}, LADDER)
    assert s.grade == UNSAFE
    assert "pypy" in s.detail


def test_the_ladder_decides_which_match_is_ideal():
    by = {eng.LYPNING: MATCH, eng.LYPNING_L: MATCH}
    # Same verdicts, reversed ladder: ideal follows the ORDER, which is the
    # routing preference, not the dict's insertion order.
    assert routing.score_route(eng.LYPNING_L, by, (eng.LYPNING_L, eng.LYPNING)).grade == IDEAL
    assert routing.score_route(eng.LYPNING_L, by, (eng.LYPNING, eng.LYPNING_L)).grade == LATE


def test_failing_the_same_way_is_not_a_claim_on_being_the_ideal_tier():
    # A program that does not parse has an empty stdout and a non-zero exit on
    # every interpreter, so each one scores MATCH for producing nothing. The
    # difference that matters — CPython names the file, the line and the column
    # and prints the offending source — is on stderr, which the battery does not
    # compare. Without this rule the cheapest tier is graded the ideal
    # destination for a program it cannot run, and 19 corpus programs read LATE
    # for that reason alone.
    failed = conf.Verdict(eng.LYPNING, "py-1", MATCH, actual_rc=1)
    by = {eng.LYPNING: failed, eng.LYPNING_L: failed, eng.CPYTHON: MATCH}
    s = routing.score_route(eng.CPYTHON, by, LADDER, route_kind="syntax")
    assert s.grade == IDEAL
    assert s.ideal == eng.CPYTHON


def test_a_tier_that_actually_ran_a_supposed_syntax_error_is_still_late():
    # The guard on the rule above, and the case worth catching: exit 0 with real
    # output means the tier ANSWERED, so the classifier calling it a syntax error
    # is a misclassification and a real defect. It must stay visible.
    answered = conf.Verdict(eng.LYPNING, "py-1", MATCH, actual_rc=0)
    by = {eng.LYPNING: answered, eng.CPYTHON: MATCH}
    s = routing.score_route(eng.CPYTHON, by, LADDER, route_kind="syntax")
    assert s.grade == LATE
    assert s.ideal == eng.LYPNING


def test_the_shared_failure_rule_applies_only_to_syntax_routes():
    # A tier that refuses at exit 90 and a tier that crashes are graded by the
    # existing rules. Widening this to every route kind would excuse a genuine
    # LATE whenever the program happened to exit non-zero — `sys.exit(3)`
    # reproduced exactly is a tier answering correctly, not failing.
    exited = conf.Verdict(eng.LYPNING, "py-1", MATCH, actual_rc=3)
    by = {eng.LYPNING: exited, eng.CPYTHON: MATCH}
    assert routing.score_route(eng.CPYTHON, by, LADDER, route_kind="module").grade == LATE


def test_a_verdict_record_grades_the_same_as_a_bare_verdict_string():
    # The battery hands over `conformance.Verdict` objects; a test that wants to
    # pin one rule hands over strings. Both must mean the same thing, or the
    # rule covered here is not the rule that runs.
    v = conf.Verdict(eng.LYPNING_L, "py-1", MISMATCH, "stdout", "line 2 differs")
    s = routing.score_route(eng.LYPNING_L, {eng.LYPNING_L: v, eng.CPYTHON: MATCH}, LADDER)
    assert s.grade == UNSAFE
    assert s.detail == "stdout: line 2 differs", "the evidence has to survive into the report"


# --- grading a whole run ------------------------------------------------------


def _report(verdicts, routes):
    """A :class:`conformance.Report` with fabricated verdicts.

    Fabricated on purpose: an UNSAFE route needs an engine that disagrees with
    CPython, and no engine produces one on demand. The alternative is a grader
    whose failing path has never been executed.
    """
    arms = []
    for row in verdicts.values():
        for arm in row:
            if arm not in arms:
                arms.append(arm)
    reports = {}
    for arm in arms:
        vs = [conf.Verdict(arm, eid, row[arm]) for eid, row in verdicts.items() if arm in row]
        counts = {v: sum(1 for x in vs if x.verdict == v) for v in conf.VERDICTS}
        reports[arm] = conf.EngineReport(
            engine=arm, match=counts[MATCH], unsupported=counts[UNSUPPORTED],
            mismatch=counts[MISMATCH], total=len(vs),
            coverage=(100.0 * counts[MATCH] / len(vs)) if vs else 0.0, verdicts=vs)
    return conf.Report(
        engines=reports, routing_errors=[], skipped=[], seconds=0.0,
        routes={eid: eng.Route(*r) if isinstance(r, tuple) else eng.Route(r)
                for eid, r in routes.items()})


def test_a_run_is_graded_program_by_program_and_summed():
    rp = routing.grade(_report(
        verdicts={
            "ideal": {eng.LYPNING: MATCH, eng.LYPNING_L: MATCH},
            "wasted": {eng.LYPNING: UNSUPPORTED, eng.LYPNING_L: MATCH},
            "late": {eng.LYPNING: MATCH, eng.LYPNING_L: MATCH},
        },
        routes={"ideal": eng.LYPNING, "wasted": eng.LYPNING, "late": eng.LYPNING_L},
    ))
    assert rp.counts[IDEAL] == 1
    assert rp.counts[WASTED] == 1
    assert rp.counts[LATE] == 1
    assert rp.graded == 3 and rp.scored == 3
    assert rp.predictions == {eng.LYPNING: 2, eng.LYPNING_L: 1}
    assert rp.ok, "WASTED and LATE are budget — neither may fail a run"
    assert round(rp.ideal_pct, 1) == 33.3
    assert round(rp.first_try_pct, 1) == 66.7, "IDEAL + LATE both answered on the first spawn"


def test_one_unsafe_route_fails_the_whole_run_and_is_named():
    rp = routing.grade(_report(
        verdicts={"bad": {eng.LYPNING: MISMATCH, eng.LYPNING_L: MATCH, conf.MIXTURE: MATCH}},
        routes={"bad": eng.LYPNING},
    ))
    assert not rp.ok
    (s,) = rp.unsafe()
    assert (s.entry_id, s.predicted, s.ideal) == ("bad", eng.LYPNING, eng.LYPNING_L)
    # The dispatcher fell onward and the caller got the right answer. That is
    # the difference between a wasted spawn and a wrong answer, and it is
    # recorded rather than allowed to excuse the route.
    assert s.rescued
    assert "UNSAFE bad" in routing.render(rp)


def test_cpython_is_on_the_ladder_even_though_it_is_never_an_arm():
    # The battery does not measure CPython — it IS the reference, and an arm
    # that matches itself measures nothing — but the classifier routes to it for
    # every program neither cheaper tier can run. Grading those against no
    # verdict scored every one of them UNSAFE, which is the exact inverse of the
    # truth: CPython's answer is the definition of right.
    rp = routing.grade(_report(
        verdicts={"p": {eng.LYPNING: UNSUPPORTED, eng.LYPNING_L: UNSUPPORTED}},
        routes={"p": eng.CPYTHON},
    ))
    assert rp.counts[IDEAL] == 1 and rp.ok
    assert rp.counts[NO_ENGINE] == 0, "the reference always matches; nothing is unroutable"


def test_a_tier_that_was_not_measured_is_a_hole_not_a_failure(no_large_engine):
    rp = routing.grade(_report(
        verdicts={"p": {eng.LYPNING: UNSUPPORTED, conf.MIXTURE: MATCH}},
        routes={"p": eng.LYPNING_L},
    ))
    assert rp.ok and rp.counts[UNSAFE] == 0
    assert rp.graded == 0 and rp.ungraded_total == 1
    assert eng.LYPNING_L in rp.note
    assert eng.LYPNING_L in routing.render(rp), "the hole is named, never rendered as a zero"


def test_no_classifier_built_means_nothing_to_grade_rather_than_a_perfect_score():
    # With no Rust core there is no classifier, `engines.route` says so with
    # kind `unbuilt`, and every program goes to CPython by default. Grading that
    # would report a corpus nobody classified as 100% correctly routed.
    rp = routing.grade(_report(
        verdicts={"p": {eng.LYPNING: MATCH}},
        routes={"p": (eng.CPYTHON, "unbuilt", "no lypning binary")},
    ))
    assert not rp.measured and rp.graded == 0
    assert rp.ungraded_total == 1
    assert "no classifier" in rp.note
    assert rp.ok, "a run that graded nothing has no UNSAFE to report"


def test_a_report_with_no_routes_says_so_instead_of_claiming_zero():
    rp = routing.grade(_report(verdicts={"p": {eng.LYPNING: MATCH}}, routes={}))
    assert not rp.measured
    assert "mixture arm did not run" in rp.note
    assert "mixture arm did not run" in routing.render(rp)


def test_skipped_programs_are_not_graded():
    # A program the battery skipped — an absolute path, a NUL in its argv — was
    # never run, so it has no verdicts to be right or wrong about and carries no
    # route. It must not appear in any bucket.
    rp = routing.grade(_report(verdicts={"ran": {eng.LYPNING: MATCH}},
                               routes={"ran": eng.LYPNING}))
    assert rp.graded == 1
    assert sum(rp.counts.values()) == 1


# --- the classifier's decisions ----------------------------------------------


def _route(program):
    return eng.route(program)


def test_a_plain_one_liner_goes_to_the_cheapest_tier(lypning_bin):
    assert _route("print(1 + 1)").engine == eng.LYPNING
    assert _route("import json\nprint(json.dumps({'a': 1}))").engine == eng.LYPNING


def test_an_import_only_the_larger_variant_serves_names_the_blocker(lypning_bin):
    # The core refuses `re` and names it; the larger variant serves the module
    # (its surface: the flags, `escape`, `purge`). A MATCHER call is a different
    # decision, and the next test's.
    r = _route("import re\nprint(re.escape('a.b'))")
    assert r.engine == eng.LYPNING_L
    assert (r.kind, r.detail) == ("module", "import re")


def test_a_matcher_call_routes_to_the_variant_that_has_the_matcher(lypning_bin):
    """The core's blocker is the IMPORT and nothing else, so the sibling that
    serves `re` is the route — including for the shapes that used to be a static
    row of the router's own (`os.makedirs` before a `re.sub`, a piped stdin).
    Every pattern below is one lypning-l serves; the ones it does not are the
    next test. The Python chain after the core's refusal agrees with the Rust
    one."""
    for src in [
        "import re\nprint(re.sub('a', 'b', 'a'))",
        "import re as x\nprint(x.findall('a', 'a'))",
        "from re import search\nprint(search('a', 'a'))",
        "from re import compile as c\nprint(c('a'))",
        "import re, os\nos.makedirs('d1/d2')\nprint(re.sub('a', 'b', 'a'))",
        "import sys, re\nd = sys.stdin.read()\nprint(re.findall(r'\\d', d))",
        "import re\nf = re.sub\nprint(f)",
        "re = 'a,b'\nprint(re.split(','))",
    ]:
        r = _route(src)
        assert r.engine != eng.CPYTHON, (src, r)
    r = _route("import re\nprint(re.sub('a', 'b', 'a'))")
    assert (r.kind, r.detail) == ("module", "import re")
    assert eng.chain_after_refusal(eng.LYPNING, r.kind, r.imports, r.verdicts) == [eng.LYPNING_L,
                                                                                  eng.CPYTHON]
    # A second module the sibling does NOT serve still rules it out. It used to
    # be `glob`, then `csv`; both are served now, so the spelling has to be a
    # module no row of `route::CAPS` claims — `itertools` is one, and the
    # assertion is about the RULE, not about which module is currently on the
    # far side of it.
    r2 = _route("import re, itertools\nprint(re.sub('a', 'b', 'a'), list(itertools.count()))")
    assert r2.engine == eng.CPYTHON, r2


def test_a_pattern_no_rung_can_compile_is_the_cores_verdict_too(lypning_bin):
    """Issue #48: the router is the CORE, so the core has to answer it.

    A static blocker only `lypning-l` could compute was inert on the default
    path. The core stopped at `module: import re`, read `cap-re` off
    `lypning-l`'s row and named it; the chain then handed the program to
    `lypning-l` as `<bin> -c PROG` rather than `<bin> run -c PROG`, so
    `lypning-l`'s walker was never asked either, and the block fired at RUNTIME
    — after `os.makedirs()` had committed the barrier, which is exit 1 and a
    chain that cannot fall onward. The fix gives every variant the pattern
    PARSER (`repat.rs`), so there is still exactly ONE routing decision and the
    core makes it.

    The KIND stays the core's own first blocker, because that is the row
    `--plan` ranks. The VERDICTS are the spectrum's, and they are what picks
    the engine.
    """
    for src in [
        'import re; print(re.findall(b"a", b"aa"))',
        'import re; print(re.search(r"(?P<é>x)", "x"))',
        'import re; print(re.findall(r"(?<=a)b", "ab"))',
        'import re, os\nos.makedirs("d")\nprint(re.findall(b"a", b"aa"))',
        'from re import compile as c\nprint(c("a{2,1}"))',
        'import re\nP = b"a"\nprint(re.findall(P, b"aa"))',
    ]:
        r = _route(src)
        assert r.engine == eng.CPYTHON, (src, r)
        # Not a chain that runs lypning-l first and finds out there.
        assert eng.chain_from(r.engine) == [eng.CPYTHON], (src, r)
        larger = [v for v in r.verdicts if v[0] == eng.LYPNING_L]
        assert larger and larger[0][1] == "re", (src, r.verdicts)
    # …and a pattern lypning-l DOES serve is untouched: the point is the
    # verdict, not a blanket retreat from the capability.
    assert _route('import re; print(re.sub("a", "b", "aa"))').engine == eng.LYPNING_L


def test_route_json_says_whether_the_program_can_read_stdin(lypning_bin):
    """`Route.reads_stdin` is what both dispatchers read before buffering a
    piped stdin for replay. Generous by design: an over-match costs one read of
    bytes the program was going to read anyway, a miss is the exhausted-stream
    bug back — and a program that CANNOT read it must not wait for the writer
    to close (`(sleep 30; echo hi) | lypning run -c 'print(1)'`)."""
    for src in ["import sys\nprint(sys.stdin.read())", "print(input())", "print(open(0).read())",
                "import os\nprint(os.read(0, 4))",
                "import fileinput\nfor l in fileinput.input(): print(l)",
                "print(open('/dev/stdin').read())",
                # a parse-time blocker stops the walk; the text scan still answers
                "class C: pass\nprint(open(0).read())",
                # `input` bound to a name: the bare identifier reads the pipe, and
                # a scan that looked only for `input(` handed CPython an exhausted
                # stream after the core's bigint refusal (EOFError at exit 1)
                "f = input\nprint(int(f()) * 10**30)"]:
        assert _route(src).reads_stdin, src
    for src in ["print(1)", "import collections\nprint(collections.Counter('ab'))",
                "import sys\nprint(sys.argv[1:])", "import re\nprint(re.escape('a'))",
                # the word, not the substring
                "inputs = [1]\nprint(inputs[0])"]:
        assert not _route(src).reads_stdin, src


def test_an_import_nobody_but_cpython_has_skips_the_middle_tier(lypning_bin):
    assert _route("import subprocess\nsubprocess.run(['true'])").engine == eng.CPYTHON
    assert _route("import ctypes").engine == eng.CPYTHON


def test_a_syntax_error_goes_to_cpython_whose_message_is_the_expected_one(lypning_bin):
    r = _route("def (")
    assert r.engine == eng.CPYTHON
    assert r.kind == "syntax"


def test_a_nested_module_path_is_resolved_rather_than_read_as_a_method(lypning_bin):
    # `os.path.basename` has an `Expr::Attr` for a base, not an `Expr::Name`, so
    # for as long as the module check only looked one level down it fell into
    # the method table, missed every entry and was blocked as `.basename()` —
    # for functions the engine has implemented all along. The cost was not the
    # spawn: `lypning route` is what the skill tells an agent to trust, and the
    # prompting study watched agents rewrite working `os.path` calls to satisfy
    # a tier that already ran them (docs/LYPNING.md §4).
    for name in ("basename", "dirname", "splitext", "join", "getsize", "exists",
                 "isfile", "isdir", "abspath", "normpath", "split", "relpath",
                 "expanduser", "islink"):
        program = "import os\nprint(os.path.%s('a/b.txt'))" % name
        assert _route(program).engine == eng.LYPNING, program


def test_an_attribute_the_nested_module_lacks_is_named_as_one(lypning_bin):
    # The other half of resolving the path: an unknown name under a module the
    # engine does have is a `module-attr`, not a `method`. The engine's own
    # refusal says `module-attr` too, so the classifier and the tier agree on
    # the words — which is what makes `--plan` a build order rather than noise.
    r = _route("import os\nprint(os.path.nosuchfn('x'))")
    assert (r.kind, r.detail) == ("module-attr", "os.path.nosuchfn")


def test_resolution_stops_at_the_first_thing_that_is_not_a_module(lypning_bin):
    # `os.environ` is a dict, so `.get` is a method and must stay one. A walk
    # that kept going would ask `get_attr` about a dict and block a call the
    # engine runs.
    assert _route("import os\nprint(os.environ.get('HOME'))").engine == eng.LYPNING


def test_constructs_no_large_rust_variant_has_go_straight_to_cpython(lypning_bin):
    assert _route("async def f(): pass").engine == eng.CPYTHON




def test_a_decorator_from_an_absent_module_is_still_decided_by_the_import(lypning_bin):
    # The imports are checked before the blocker kind is, so relaxing the kind
    # did not start sending `@functools.lru_cache` to a tier without functools.
    # This is what keeps WASTED flat across that change.
    assert _route("import functools\n@functools.lru_cache\ndef f(x): return x").engine == eng.CPYTHON


def test_an_unbuilt_classifier_routes_everything_to_cpython_and_says_why(monkeypatch):
    # Tested by pointing the finder at nothing rather than by reasoning about
    # it. "Not built" must be a route with a reason, never an exception.
    monkeypatch.setattr(eng, "find_lypning", lambda: None)
    r = eng.route("print(1)")
    assert (r.engine, r.kind) == (eng.CPYTHON, "unbuilt")


# --- the whole battery, end to end -------------------------------------------


def test_routing_grades_a_live_battery_run(lypning_bin):
    report = conf.run(limit=CORPUS_SLICE, timeout=20.0)
    rp = routing.grade(report)
    assert rp.measured, "the mixture arm ran, so every scored program has a route"
    assert rp.graded + rp.ungraded_total == len(report.routes)
    assert sum(rp.counts.values()) == rp.graded
    assert sum(rp.predictions.values()) == rp.graded
    assert rp.counts[IDEAL] == max(rp.counts.values()), (
        "the classifier picks the cheapest working tier for most of the corpus, "
        "or it is not paying for itself: %r" % rp.counts)
    for s in rp.unsafe():
        # The tree's one known UNSAFE class, asserted in full below. Anything
        # else is a new defect and this is where it surfaces.
        assert s.predicted == eng.LYPNING_L and s.detail.startswith("contract:"), (
            "a new UNSAFE route, and an UNSAFE route is a wrong answer: %s" % s)


PRINT_THEN_REFUSE_MP = (
    'print("BEFORE")\n'
    "import re\n"
    'print(re.findall(r"(?i)ab", "AB ab"))\n'
)

#: The same defect, on the construct the CLASSIFIER now declines. This one used
#: to be the example above; it stopped being a live UNSAFE not because the tier
#: gained a barrier but because `route.rs` learnt to keep the construct off it.
PRINT_THEN_REFUSE_MP_DECLINED = (
    'print("BEFORE")\n'
    "import hashlib\n"
    "print(sorted(hashlib.algorithms_guaranteed))\n"
)






def test_no_program_is_routed_to_the_tier_with_a_kind_the_chain_would_escalate():
    """Two tables decide a related question, and they must not fight over one program.

    Six kind names appear in both, and that overlap is NOT by itself a
    contradiction — the two describe different populations. A kind the parser
    can see never reaches the runtime table, because lypning is never the tier
    that runs. The kinds in the runtime table are the ones discovered by
    running: `nan-identity` depends on a value, not a syntax.

    What WOULD be a contradiction is one program caught by both: statically sent
    to the tier under a kind the chain would have escalated away from it. That
    measures zero, and it is what this asserts — over a slice, because the whole
    corpus is a `lypning conformance` and this suite is seconds. The parser gets
    better at seeing things, and the day it learns to spot one of these
    statically is the day the static answer starts overriding the runtime one,
    silently and in the wrong direction."""
    entries = corpus.load_default()[:CORPUS_SLICE]
    if not entries:
        pytest.skip("no corpus to route")
    caught = []
    for e in entries:
        r = eng.route(e.program)
        if r.engine == eng.LYPNING_L and r.kind in eng.ONLY_CPYTHON_REFUSALS:
            caught.append((e.id, r.kind))
    assert not caught, (
        "these programs are routed to lypning-l under a kind the chain would "
        "send to CPython, so the static table is overriding the runtime one: %s"
        % caught[:5])


# --- the capability table -----------------------------------------------------


def test_the_spectrum_copy_in_engines_is_the_rust_table():
    # engines.SPECTRUM is a copy of route::SPECTRUM (it has to be a module
    # constant). A copy is honest only while something checks it: this reads
    # the Rust source and FAILS — never skips — if the two disagree, because a
    # missing table is the same silent drift as a wrong one.
    names = routing.spectrum()
    assert names, "route::SPECTRUM was not found in %s" % routing.table_source()
    assert list(eng.SPECTRUM) == names


def test_the_larger_variant_knows_its_own_name_and_the_floor_rule_holds():
    # Skips only when lypning-l is not built; when it is, it must call itself
    # lypning-l, refuse with its own name at the head, and — being row 1 —
    # never route a program below itself.
    import json as _json
    import subprocess as _sp
    p = eng.find(eng.LYPNING_L)
    if p is None:
        pytest.skip("lypning-l is not built")
    out = _sp.run([str(p), "route", "--spectrum"], capture_output=True, text=True, timeout=60)
    assert _json.loads(out.stdout)["self"] == eng.LYPNING_L
    r = _sp.run([str(p), "-c", "import subprocess"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 90 and r.stdout == "" and r.stderr.strip() == eng.refusal_line(eng.LYPNING_L, "module", "import subprocess")
    ver = _sp.run([str(p), "--version"], capture_output=True, text=True, timeout=60).stdout
    assert "(%s)" % eng.LYPNING_L in ver
    r = eng.route("print(1)", binary=p)
    assert r.engine == eng.LYPNING_L                         # itself, never row 0
    assert r.verdicts[0][1] == "floor"                       # row 0 is below the routing binary
    from lypning import build
    assert build.check_spectrum_contract(p, expected=eng.LYPNING_L) == (True, "")


def test_a_built_core_knows_its_own_name(lypning_bin):
    # The other half of the pin: not the source, the artefact. `route --spectrum`
    # names the binary and the table it carries; `--version` says the same.
    import json as _json
    import subprocess as _sp
    out = _sp.run([str(lypning_bin), "route", "--spectrum"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    table = _json.loads(out.stdout)
    assert table["self"] == eng.LYPNING
    assert [r["name"] for r in table["spectrum"]] == list(eng.SPECTRUM)
    assert {r["name"]: tuple(r["caps"]) for r in table["spectrum"]} == eng.VARIANT_CAPS
    assert table["self_caps"] == [], "no capability is gated yet"
    ver = _sp.run([str(lypning_bin), "--version"], capture_output=True, text=True, timeout=60).stdout
    assert ver.startswith("lypning ") and "(%s)" % eng.LYPNING in ver
    from lypning import build
    assert build.check_spectrum_contract(lypning_bin) == (True, "")
    ok, why = build.check_spectrum_contract(lypning_bin, expected="lypning-l")
    assert not ok and "calls itself" in why
