"""Routing safety: where the classifier SENDS a program against where it could go.

Conformance asks whether an engine agreed with CPython. This asks the other
question — whether the program was ever handed to that engine — and grades the
gap with a deliberately asymmetric vocabulary:

  UNSAFE  routed to an engine that MISMATCHES. Fatal. The mixture's whole claim
          is that a wrong route costs a spawn and never an answer.
  WASTED  routed to an engine that refuses when a cheaper one would have run it.
          One spawn.
  LATE    routed higher up the ladder than necessary. The difference in run time.

The rule is a pure function of verdicts the battery already measured, so most of
this file needs no interpreter built at all: the grading stays covered on a
machine with nothing compiled, which is the half most likely to rot silently.
The rest needs the Rust core (the classifier IS the Rust core's parser) and the
capability-table check needs lypning-mp, and both skip rather than fail when the
binary is absent.

The last section is the one this file exists for. lypning-mp is a separate
binary that cannot be asked what it imports, so `route.rs` carries a TABLE, and
a table is only as honest as the thing that checks it. Editing one to describe
what someone wished the engine did converts a loud failure into a silent one —
so this checks it in both directions and changes nothing.
"""

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
    # These kinds skip EVERY Rust variant, not just the departed MicroPython
    # tier: a kind is in ONLY_CPYTHON_REFUSALS because a REIMPLEMENTATION gets
    # it wrong, and every variant is the same reimplementation at a different
    # size. With the tier gone the rule means strictly more than it used to.
    assert eng.chain_after_refusal(eng.LYPNING, "decorator") == [eng.CPYTHON]
    assert eng.chain_after_refusal(eng.LYPNING, "nan-identity") == [eng.CPYTHON]
    # An unknown kind falls through, which is the safe default for the cost: a
    # kind nobody classified costs a spawn, never an answer it could not have
    # got right anyway.
    assert eng.chain_after_refusal(eng.LYPNING, "kind-nobody-wrote") == [eng.CPYTHON]
    # A larger sibling with no capability the refusing one lacks is not tried:
    # it cannot answer what the smaller one could not.
    assert eng.chain_after_refusal(eng.LYPNING_L, "nan-identity") == [eng.CPYTHON]


# `test_the_mp_kind_arm_lists_only_kinds_the_classifier_can_emit` is gone with its subject: micropython_kinds() read engine_for's mp arm, which no longer exists


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

        lypning run -c 'print({3,1,2})'      {3, 1, 2}   CPython {1, 2, 3}

    `route.rs` now owns the table and the Rust dispatcher reads it. This holds
    the Python copy to it, read out of the source the way
    `micropython_modules()` is — a copy that cannot drift silently rather than a
    copy that already had.
    """
    rust = routing.only_cpython_kinds()
    if not rust:
        pytest.skip("ONLY_CPYTHON_KINDS was not found in %s" % routing.table_source())
    assert rust == sorted(rust), "the table is read by eye; keep it sorted"
    assert set(rust) == set(eng.ONLY_CPYTHON_REFUSALS), (
        "the two dispatchers disagree about which refusals skip lypning-mp: "
        "route.rs has %s, engines.py has %s"
        % (sorted(set(rust) - set(eng.ONLY_CPYTHON_REFUSALS)),
           sorted(set(eng.ONLY_CPYTHON_REFUSALS) - set(rust)))
    )


@pytest.mark.parametrize("program,want", [
    ("print({3,1,2})", "{1, 2, 3}"),
    ("x = float('nan')\nprint(x in [x])", "True"),
    ("print(9007199254740993 / 3)", "3002399751580331.0"),
])
def test_the_rust_dispatcher_escalates_too(program, want, lypning_bin, micropython_bin):
    """The gate that was missing, run through the binary rather than the battery.

    Every one of these is refused by tier 1 by name, and every kind is in the
    escalation table. Before `route.rs` owned it, the Rust dispatcher handed all
    three to lypning-mp and printed a wrong answer at exit 0 — while the Python
    dispatcher, which is the only one conformance exercises, printed the right
    one. A gate that measures the wrong dispatcher is not a gate.
    """
    import subprocess
    got = subprocess.run(
        [str(lypning_bin), "run", "-c", program],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "LYPNING_MP_BIN": str(micropython_bin), "LYPNING_CAPTURE": "0"},
    )
    assert got.returncode == 0, got.stderr[-300:]
    assert got.stdout.strip() == want, (
        "the Rust dispatcher answered %r; CPython answers %r" % (got.stdout.strip(), want))


def test_the_rust_dispatcher_still_falls_through_for_a_capability_gap(lypning_bin, micropython_bin):
    """The other direction, which bounds what the table may cost.

    A `bigint` refusal is a capability gap and MicroPython HAS arbitrary-precision
    integers, so it must still reach the cheaper tier rather than paying a CPython
    spawn. Escalating everything would be safe and slow; the table's whole value
    is that it does not.
    """
    import subprocess
    got = subprocess.run(
        [str(lypning_bin), "run", "-c", "print(2**70)"],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "LYPNING_MP_BIN": str(micropython_bin), "LYPNING_CAPTURE": "0"},
    )
    assert got.stdout.strip() == "1180591620717411303424"


def test_a_construct_the_runtime_table_would_escalate_is_kept_off_the_tier_statically(lypning_bin):
    """The hole between the two tables, and the shape that gets through it.

    `engines.ONLY_CPYTHON_REFUSALS` is the RUNTIME half: it fires on a refusal
    tier 1 actually emitted. But tier 1 only runs when the classifier sends the
    program there, and a program whose FIRST blocker is an ordinary capability
    gap goes straight to lypning-mp — so tier 1 never refuses, the runtime table
    never sees the kind, and the tier answers wrongly at exit 0.

    An unused import is enough to open it::

        import math
        x = float("nan")
        print(x in [x])      # CPython True, lypning-mp False

    A NaN literal and an oversized division operand are both visible in the
    SOURCE, so the static half can catch what the runtime half cannot.
    """
    assert _route("import math\nx = float('nan')\nprint(x in [x])").engine == eng.CPYTHON
    assert _route("import math\nprint(9007199254740993 / 3)").engine == eng.CPYTHON


# `test_the_static_markers_are_narrow_enough_to_be_worth_their_spawns` is gone with its subject: the MICROPYTHON_UNSAFE AST markers existed only to keep a program off the MicroPython tier, which left the chain


def test_the_integer_refusals_split_by_what_the_tier_below_can_do(lypning_bin, micropython_bin):
    """One kind, two populations, and only one of them is a subtlety.

    Every `bigint` refusal but one means "Python would use a bignum here" — a
    capability, and lypning-mp IS MicroPython, which has arbitrary-precision
    integers. Falling through gets the right answer for one cheap spawn. The
    exception is `int / int` past 2\*\*53, where the quotient needs rounding from
    the integers themselves: MicroPython converts both to double exactly as
    lypning would have, so it answers, and it answers wrongly.

    They shared a kind until this session, and escalating that kind sent all
    eleven of the corpus' `bigint` refusals to CPython to rescue the one.
    """
    assert _refusal_kind("print(2**70)") == "bigint"
    assert _refusal_kind("print(9007199254740993/3)") == "int-div-precision"
    assert eng.dispatch("print(2**70)").engine == eng.LYPNING_L
    assert eng.dispatch("print(9007199254740993/3)").engine == eng.CPYTHON


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
    # The first line of this used to assert WASTED for the lypning route, and
    # that assertion was the bug written down: with lypning-mp MISMATCHing, a
    # refusal at tier 1 falls through INTO the wrong answer. It is covered as
    # UNSAFE above; what is left here is the part that was always true.
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


def test_a_tier_that_was_not_measured_is_a_hole_not_a_failure(no_micropython):
    # lypning-mp needs a 32-bit toolchain and a network, so it is absent almost
    # everywhere. The classifier still routes to it — it reads a table, not a
    # filesystem — and those routes have no measured answer to grade against.
    # Ungraded with a reason, never UNSAFE: a machine's build state is not a
    # classifier bug.
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
    The core cannot compile a pattern; deciding which patterns lypning-l can
    serve is lypning-l's own walker's job, and `tests/test_re_grid.py` pins it.
    The Python chain after the core's refusal agrees with the Rust one."""
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
    # A second module the sibling does NOT serve still rules it out.
    r2 = _route("import re, glob\nprint(re.sub('a', 'b', 'a'), glob.glob('*'))")
    assert r2.engine == eng.CPYTHON, r2


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
    # Not via lypning-mp: the import fails there first, so that spawn is pure
    # waste — the difference between a LATE route and a WASTED one.
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


# `test_a_construct_the_middle_tier_gets_wrong_keeps_a_program_off_it` is gone with its subject: there is no middle tier to keep a program off; the constructs it named are the oracle's families in .github/known-mismatches.json


# `test_the_unsafe_construct_rules_are_precise_and_not_whole_modules` is gone with its subject: same — the construct-level rules were the mp_risk markers


def test_constructs_no_micropython_derived_runtime_has_skip_the_middle_tier(lypning_bin):
    # `async` alone, and not because the syntax is rejected there — `async def`
    # parses on lypning-mp. `asyncio` is absent, and the program needs it to do
    # anything, so the tier would refuse cleanly one spawn later. This list is
    # about where a program ends up, not what a parser accepts.
    assert _route("async def f(): pass").engine == eng.CPYTHON


# `test_decorators_and_generators_are_language_features_the_middle_tier_has` is gone with its subject: engine_for's `=> Engine::MicroPython` arm is gone with the tier


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


# `import hashlib` is in route.rs's table, so this routes to lypning-mp — and
# `hashlib.algorithms_guaranteed` is not in that tier's frozen shim, so it
# prints, then refuses, having already committed the print. Minimised from
# corpus entry py-876af0f0a956's sibling py-b2a043f241f1, the corpus' only
# UNSAFE route.
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


def test_the_one_unsafe_route_is_the_tracked_barrier_defect(lypning_bin, micropython_bin):
    """KNOWN DEFECT, and the only shape an UNSAFE route takes in this tree.

    A route is safe when the engine it names either answers correctly or refuses
    cleanly. lypning-mp streams stdout, so on this program it does neither: the
    print is already committed when the refusal comes, which conformance scores
    MISMATCH — and a route to an engine that mismatches is UNSAFE by definition.
    The classifier is not wrong about the import; the tier is wrong about the
    barrier (docs/LYPNING.md §6, tests/test_commit_barrier.py).

    If this fails, lypning-mp gained a commit barrier. That is good news and
    makes docs/LYPNING.md §6 (the paragraph on lypning-mp streaming stdout),
    the README's conformance section and this test stale — say so rather than
    deleting the assertion.
    """
    assert eng.route(PRINT_THEN_REFUSE_MP).engine == eng.LYPNING_L
    report = conf.run(entries=[corpus.Entry(id="unsafe-repro", program=PRINT_THEN_REFUSE_MP)],
                      timeout=20.0)
    rp = routing.grade(report)
    (s,) = rp.unsafe()
    assert (s.predicted, s.grade) == (eng.LYPNING_L, UNSAFE)
    assert "already reached stdout" in s.detail
    assert not rp.ok, "UNSAFE is the gate; it must fail the run it appears in"
    assert s.rescued, "the dispatcher still contains the leak, so the caller is unharmed"


# `test_a_barrier_construct_the_classifier_can_see_is_kept_off_the_tier` is gone with its subject: the barrier markers routed away from lypning-mp; nothing routes there now


def test_no_program_is_routed_to_the_tier_with_a_kind_the_chain_would_escalate():
    """Two tables decide a related question, and they must not fight over one program.

    `route.rs` decides STATICALLY, from a parse: "lypning would refuse this with
    kind K, and lypning-mp has K, so start there." `ONLY_CPYTHON_REFUSALS`
    decides at RUNTIME, from a refusal that actually happened: "kind K is a
    subtlety no reimplementation gets right, so skip to CPython."

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
    silently and in the wrong direction.
    """
    entries = corpus.load_default()[:CORPUS_SLICE]
    if not entries:
        pytest.skip("no corpus to route")
    caught = []
    for e in entries:
        r = eng.route(e.program)
        if r.engine == eng.LYPNING_L and r.kind in eng.ONLY_CPYTHON_REFUSALS:
            caught.append((e.id, r.kind))
    assert not caught, (
        "these programs are routed to lypning-mp under a kind the chain would "
        "escalate to CPython, so the static table is overriding the runtime one: %s"
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


def test_the_table_is_read_from_route_rs_not_restated_here():
    mods = routing.micropython_modules()
    assert mods, "MICROPYTHON_MODULES was not found in %s" % routing.table_source()
    # Sorted is not cosmetic: the table is edited by hand and read by eye, and
    # an entry filed in the wrong place is an entry added twice.
    assert mods == sorted(mods)
    assert len(set(mods)) == len(mods)


def test_every_module_the_table_claims_can_actually_be_imported(micropython_bin):
    """The table's honest direction, and the one that costs correctness.

    A module listed here but absent from the tier sends every program importing
    it one tier too low, where it refuses — WASTED at best, and UNSAFE the
    moment that program printed something first. The table cannot be asked of
    the binary, so it is asked of the binary here.
    """
    missing = []
    for m in routing.micropython_modules():
        r = eng.run(eng.LYPNING_L, "import %s" % m, binary=micropython_bin, timeout=20.0)
        if r.returncode != 0:
            missing.append((m, (r.stderr or "").strip().splitlines()[-1:] or [""]))
    assert not missing, (
        "route.rs claims lypning-mp serves modules it cannot import: %r — fix the "
        "engine or the table, and never the table alone" % missing)


def test_modules_the_tier_serves_but_the_table_omits_are_reported_not_fixed(micropython_bin):
    """The other direction, which costs money rather than correctness.

    A module the tier serves but the table omits sends its programs straight to
    CPython — coverage left on the table. Reported and never asserted: adding
    one is a routing change that has to be earned with a conformance run, and a
    test that failed until someone edited the table would be a test that demands
    exactly the edit CLAUDE.md prohibits. Candidates are the modules the corpus
    actually imports, so the number is demand and not a stdlib inventory.
    """
    table = set(routing.micropython_modules())
    entries = corpus.load_default()
    left = []
    for name, count in corpus.stats(entries, top=0).top_imports:
        if name in table:
            continue
        r = eng.run(eng.LYPNING_L, "import %s" % name, binary=micropython_bin, timeout=20.0)
        if r.returncode == 0:
            left.append((name, count))
    if left:
        warnings.warn(
            "lypning-mp imports %d module(s) the route.rs table omits, over %d corpus "
            "entries: %s. Every one of them routes to CPython today. Adding one to "
            "the table is a routing change: measure it with `lypning conformance` "
            "first — importable is not the same as complete."
            % (len(left), sum(c for _m, c in left), left),
            stacklevel=1,
        )
    assert isinstance(left, list)  # the finding is the warning; this never fails


def test_the_table_check_degrades_when_the_source_is_missing(tmp_path):
    # A renamed table must not read as an empty one somewhere it is trusted.
    assert routing.micropython_modules(tmp_path / "nothing.rs") == []
    (tmp_path / "other.rs").write_text("const OTHER: &[&str] = &[\"x\"];\n", encoding="utf-8")
    assert routing.micropython_modules(tmp_path / "other.rs") == []


def test_no_module_in_the_table_routes_past_the_tier_that_claims_it(lypning_bin):
    """The oracle's import table is lypning-l's BUILD ORDER, and it is measured.

    This used to assert the inverse — that no module lypning-mp claimed could
    route past it — because the tier was in the chain. It left on 2026-09-04,
    so every module the oracle serves that no Rust variant serves now reaches
    CPython at ~11 ms, and the list of those modules is exactly the work
    `lypning-l` has left to do. The test pins the direction: each name is
    either served by a Rust variant or a row on the build order, never
    silently neither.
    """
    served, todo = [], []
    for m in routing.micropython_modules():
        r = eng.route("import %s\n" % m)
        (todo if r.engine == eng.CPYTHON else served).append(m)
    assert served, "the Rust core serves none of the oracle's modules — that cannot be right"
    # Every remaining name must be a plain module blocker, not something subtler:
    # a build-order row has to be actionable.
    for m in todo:
        r = eng.route("import %s\n" % m)
        assert (r.kind, r.detail) == ("module", "import %s" % m), (m, r.kind, r.detail)
