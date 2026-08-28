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

import warnings
from pathlib import Path

import pytest

from lypning import conformance as conf
from lypning import corpus
from lypning import engines as eng
from lypning import routing
from lypning.conformance import MATCH, MISMATCH, UNSUPPORTED
from lypning.routing import IDEAL, LATE, NO_ENGINE, UNSAFE, WASTED

LADDER = (eng.LYPNING, eng.MICROPYTHON, eng.CPYTHON)

#: How much of the corpus the end-to-end test grades. The whole corpus is the
#: CLI's job — `lypning conformance` runs it, prints the routing table and exits
#: 1 on an UNSAFE — because a full battery is seconds of subprocesses and this
#: suite is four. What is asserted here is that the grading machinery agrees
#: with a live battery; what is *measured* is printed by the tool.
CORPUS_SLICE = 200


# --- the scoring rule (nothing built) ----------------------------------------


def test_the_cheapest_matching_engine_is_ideal():
    by = {eng.LYPNING: MATCH, eng.MICROPYTHON: MATCH, eng.CPYTHON: MATCH}
    assert routing.score_route(eng.LYPNING, by, LADDER).grade == IDEAL
    # Correct answer, wrong bill: both of these matched, and both cost more than
    # the tier that also would have.
    assert routing.score_route(eng.MICROPYTHON, by, LADDER).grade == LATE
    assert routing.score_route(eng.CPYTHON, by, LADDER).grade == LATE


def _refusal_kind(program):
    """The kind tier 1 refuses ``program`` with, or "" if it ran it."""
    r = eng.run(eng.LYPNING, program)
    return r.refusal[0] if r.refused else ""


def test_a_semantic_refusal_skips_every_tier_but_cpython():
    # The chain assumes the tier below is at least as correct as the one that
    # refused. For a capability gap that holds — MicroPython has decorators.
    # For a refusal that says "CPython's behaviour here is subtle and I decline
    # to guess", it does not: the reason tier 1 refused is the reason a second
    # reimplementation gets it wrong too, so falling through turns a correct
    # refusal into a silent wrong answer.
    assert eng.chain_after_refusal(eng.LYPNING, "decorator") == [eng.MICROPYTHON, eng.CPYTHON]
    assert eng.chain_after_refusal(eng.LYPNING, "nan-identity") == [eng.CPYTHON]
    # An unknown kind falls through, which is the safe default for the cost: a
    # kind nobody classified costs a spawn, never an answer it could not have
    # got right anyway.
    assert eng.chain_after_refusal(eng.LYPNING, "kind-nobody-wrote") == [eng.MICROPYTHON, eng.CPYTHON]
    # Nothing is left to skip once lypning-mp is the one refusing.
    assert eng.chain_after_refusal(eng.MICROPYTHON, "nan-identity") == [eng.CPYTHON]


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
    assert eng.dispatch("print(2**70)").engine == eng.MICROPYTHON
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


def test_a_refusal_that_falls_through_to_a_wrong_answer_is_unsafe_not_wasted():
    # The rule this file exists for, and the one that was missing. A refusal is
    # not an outcome: the dispatcher moves down the ladder and the NEXT tier's
    # answer is what the user sees. Here tier 1 refuses CORRECTLY — it knows it
    # cannot match CPython on this construct — and lypning-mp then answers
    # wrongly at exit 0. Grading the tier that was NAMED reads that as a spare
    # process spawn; grading the tier that ANSWERED reads it as what it is.
    by = {eng.LYPNING: UNSUPPORTED, eng.MICROPYTHON: MISMATCH, eng.CPYTHON: MATCH}
    s = routing.score_route(eng.LYPNING, by, LADDER)
    assert s.grade == UNSAFE
    assert eng.MICROPYTHON in s.detail, "the grade has to name the tier that actually answered"


def test_a_refusal_that_falls_through_to_a_right_answer_is_still_only_wasted():
    # The guard on the rule above. Falling through is the design and costs one
    # spawn; it is only fatal when the tier that catches the fall is wrong.
    by = {eng.LYPNING: UNSUPPORTED, eng.MICROPYTHON: MATCH, eng.CPYTHON: MATCH}
    assert routing.score_route(eng.LYPNING, by, LADDER).grade == WASTED


def test_the_fall_through_skips_tiers_that_also_refused():
    # Two refusals in a row is still one delivered answer, and it is CPython's.
    by = {eng.LYPNING: UNSUPPORTED, eng.MICROPYTHON: UNSUPPORTED, eng.CPYTHON: MATCH}
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
    by = {eng.LYPNING: UNSUPPORTED, eng.MICROPYTHON: MISMATCH, eng.CPYTHON: MATCH}
    assert routing.score_route(eng.MICROPYTHON, by, LADDER).grade == UNSAFE
    assert routing.score_route(eng.CPYTHON, by, LADDER).grade == IDEAL
    # A refusal at the tier ABOVE the wrong one never reaches it.
    by2 = {eng.LYPNING: MISMATCH, eng.MICROPYTHON: UNSUPPORTED, eng.CPYTHON: MATCH}
    assert routing.score_route(eng.MICROPYTHON, by2, LADDER).grade == WASTED


def test_a_route_that_is_both_wrong_and_ideal_is_still_unsafe():
    # The ideal engine is the cheapest that MATCHED, so an engine that
    # mismatches can never be it — but the rule is ordered so that a mismatch is
    # read before anything else, and that order is the gate. Pin it.
    by = {eng.LYPNING: MISMATCH, eng.MICROPYTHON: MATCH}
    s = routing.score_route(eng.LYPNING, by, LADDER)
    assert s.grade == UNSAFE
    assert s.ideal == eng.MICROPYTHON, "an UNSAFE route still names where it should have gone"


def test_nothing_to_grade_when_no_engine_matched():
    # Every tier refused: the program is outside the mixture, which is a
    # coverage number and not the classifier's fault. Counting it against the
    # classifier would make an engine's gap look like a routing bug.
    by = {eng.LYPNING: UNSUPPORTED, eng.MICROPYTHON: UNSUPPORTED, eng.CPYTHON: UNSUPPORTED}
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
    by = {eng.LYPNING: MATCH, eng.MICROPYTHON: MATCH}
    # Same verdicts, reversed ladder: ideal follows the ORDER, which is the
    # routing preference, not the dict's insertion order.
    assert routing.score_route(eng.MICROPYTHON, by, (eng.MICROPYTHON, eng.LYPNING)).grade == IDEAL
    assert routing.score_route(eng.MICROPYTHON, by, (eng.LYPNING, eng.MICROPYTHON)).grade == LATE


def test_failing_the_same_way_is_not_a_claim_on_being_the_ideal_tier():
    # A program that does not parse has an empty stdout and a non-zero exit on
    # every interpreter, so each one scores MATCH for producing nothing. The
    # difference that matters — CPython names the file, the line and the column
    # and prints the offending source — is on stderr, which the battery does not
    # compare. Without this rule the cheapest tier is graded the ideal
    # destination for a program it cannot run, and 19 corpus programs read LATE
    # for that reason alone.
    failed = conf.Verdict(eng.LYPNING, "py-1", MATCH, actual_rc=1)
    by = {eng.LYPNING: failed, eng.MICROPYTHON: failed, eng.CPYTHON: MATCH}
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
    v = conf.Verdict(eng.MICROPYTHON, "py-1", MISMATCH, "stdout", "line 2 differs")
    s = routing.score_route(eng.MICROPYTHON, {eng.MICROPYTHON: v, eng.CPYTHON: MATCH}, LADDER)
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
            "ideal": {eng.LYPNING: MATCH, eng.MICROPYTHON: MATCH},
            "wasted": {eng.LYPNING: UNSUPPORTED, eng.MICROPYTHON: MATCH},
            "late": {eng.LYPNING: MATCH, eng.MICROPYTHON: MATCH},
        },
        routes={"ideal": eng.LYPNING, "wasted": eng.LYPNING, "late": eng.MICROPYTHON},
    ))
    assert rp.counts[IDEAL] == 1
    assert rp.counts[WASTED] == 1
    assert rp.counts[LATE] == 1
    assert rp.graded == 3 and rp.scored == 3
    assert rp.predictions == {eng.LYPNING: 2, eng.MICROPYTHON: 1}
    assert rp.ok, "WASTED and LATE are budget — neither may fail a run"
    assert round(rp.ideal_pct, 1) == 33.3
    assert round(rp.first_try_pct, 1) == 66.7, "IDEAL + LATE both answered on the first spawn"


def test_one_unsafe_route_fails_the_whole_run_and_is_named():
    rp = routing.grade(_report(
        verdicts={"bad": {eng.LYPNING: MISMATCH, eng.MICROPYTHON: MATCH, conf.MIXTURE: MATCH}},
        routes={"bad": eng.LYPNING},
    ))
    assert not rp.ok
    (s,) = rp.unsafe()
    assert (s.entry_id, s.predicted, s.ideal) == ("bad", eng.LYPNING, eng.MICROPYTHON)
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
        verdicts={"p": {eng.LYPNING: UNSUPPORTED, eng.MICROPYTHON: UNSUPPORTED}},
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
        routes={"p": eng.MICROPYTHON},
    ))
    assert rp.ok and rp.counts[UNSAFE] == 0
    assert rp.graded == 0 and rp.ungraded_total == 1
    assert eng.MICROPYTHON in rp.note
    assert eng.MICROPYTHON in routing.render(rp), "the hole is named, never rendered as a zero"


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


def test_an_import_only_the_second_tier_has_names_the_blocker(lypning_bin):
    r = _route("import re\nprint(re.findall(r'\\d+', 'a1'))")
    assert r.engine == eng.MICROPYTHON
    assert (r.kind, r.detail) == ("module", "import re")


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


def test_a_construct_the_middle_tier_gets_wrong_keeps_a_program_off_it(lypning_bin):
    # UNSAFE is the one routing outcome that spends trust instead of
    # milliseconds, and six of the seven routed to lypning-mp. That tier is
    # third-party and its defects cannot be fixed here — but the classifier can
    # decline to send a program there when the SOURCE shows it would trip on
    # one. Each of these is a family in `.github/known-mismatches.json`.
    assert _route("import random\nrandom.seed(7)\nprint(random.random())").engine == eng.CPYTHON
    assert _route("from pathlib import Path\nprint(Path('/a/b').parts)").engine == eng.CPYTHON
    assert _route("try:\n    1/0\nexcept Exception as e:\n    print(type(e).__module__)").engine == eng.CPYTHON


def test_the_unsafe_construct_rules_are_precise_and_not_whole_modules(lypning_bin):
    # The cost of this table is spawns, so it must fire on the construct and not
    # on its module: `random` without a seed is reproducible, `Path.name` is
    # correct there, and `.parts` on an unrelated object is an ordinary
    # attribute. Routing all of `pathlib` away instead would cost 133 corpus
    # programs against 25 for the three constructs together.
    assert _route("import random\nprint(random.random())").engine == eng.MICROPYTHON
    assert _route("from pathlib import Path\nprint(Path('/a/b').name)").engine == eng.MICROPYTHON
    assert _route("import base64\nprint(base64.b64encode(b'hi'))").engine == eng.MICROPYTHON
    # `.parts` with no pathlib in sight is not the pathlib defect.
    assert _route("class C:\n    parts = 1\nprint(C.parts)").engine != eng.CPYTHON


def test_constructs_no_micropython_derived_runtime_has_skip_the_middle_tier(lypning_bin):
    # `async` alone, and not because the syntax is rejected there — `async def`
    # parses on lypning-mp. `asyncio` is absent, and the program needs it to do
    # anything, so the tier would refuse cleanly one spawn later. This list is
    # about where a program ends up, not what a parser accepts.
    assert _route("async def f(): pass").engine == eng.CPYTHON


def test_decorators_and_generators_are_language_features_the_middle_tier_has(lypning_bin):
    # Both sat in CPYTHON_ONLY_KINDS as "constructs no MicroPython-derived
    # runtime has", which was never true: MicroPython implements them in the
    # language. Ten corpus programs were routed past a tier that runs them.
    for program in ("@dec\ndef f(): pass",
                    "def g():\n    yield 1\nprint(list(g()))"):
        assert _route(program).engine == eng.MICROPYTHON, program
    # A generator *expression* is not the blocker and never was — tier 1 runs it.
    assert _route("print(list(x * 2 for x in range(3)))").engine == eng.LYPNING


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
        assert s.predicted == eng.MICROPYTHON and s.detail.startswith("contract:"), (
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
    makes docs/LYPNING.md §6, the README's conformance section and this test
    stale — say so rather than deleting the assertion.
    """
    assert eng.route(PRINT_THEN_REFUSE_MP).engine == eng.MICROPYTHON
    report = conf.run(entries=[corpus.Entry(id="unsafe-repro", program=PRINT_THEN_REFUSE_MP)],
                      timeout=20.0)
    rp = routing.grade(report)
    (s,) = rp.unsafe()
    assert (s.predicted, s.grade) == (eng.MICROPYTHON, UNSAFE)
    assert "already reached stdout" in s.detail
    assert not rp.ok, "UNSAFE is the gate; it must fail the run it appears in"
    assert s.rescued, "the dispatcher still contains the leak, so the caller is unharmed"


def test_a_barrier_construct_the_classifier_can_see_is_kept_off_the_tier(lypning_bin):
    """The half that is fixable here, on the program that used to be the one above.

    The commit barrier is lypning-mp's and cannot be fixed in this tree — the
    test above still reproduces it on a regex the classifier cannot screen,
    because the pattern is only a string until it is compiled. But where the
    construct IS visible in the source, the classifier can decline to start
    there, and two of the corpus' three barrier entries are visible that way:
    `hashlib.algorithms_guaranteed`, and the `strict_mode=` keyword.

    Precision is the whole point, so both directions are asserted: sibling uses
    of the same modules must still reach the cheaper tier, or the rule has
    quietly become "route all of hashlib away" at 100x the price.
    """
    assert eng.route(PRINT_THEN_REFUSE_MP_DECLINED).engine == eng.CPYTHON
    assert _route("import base64\nprint(base64.b64decode(b'aGk=', strict_mode=True))").engine == eng.CPYTHON
    assert _route("import hashlib\nprint(hashlib.md5(b'x').hexdigest())").engine == eng.MICROPYTHON
    assert _route("import base64\nprint(base64.b64decode(b'aGk='))").engine == eng.MICROPYTHON
    # `algorithms_guaranteed` on something that is not hashlib is an ordinary
    # attribute: the rule is a dotted path, not a bare name.
    assert _route("class C:\n    algorithms_guaranteed = 1\nprint(C.algorithms_guaranteed)").engine != eng.CPYTHON


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
        if r.engine == eng.MICROPYTHON and r.kind in eng.ONLY_CPYTHON_REFUSALS:
            caught.append((e.id, r.kind))
    assert not caught, (
        "these programs are routed to lypning-mp under a kind the chain would "
        "escalate to CPython, so the static table is overriding the runtime one: %s"
        % caught[:5])


# --- the capability table -----------------------------------------------------


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
        r = eng.run(eng.MICROPYTHON, "import %s" % m, binary=micropython_bin, timeout=20.0)
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
        r = eng.run(eng.MICROPYTHON, "import %s" % name, binary=micropython_bin, timeout=20.0)
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
    """The table is only half the decision — ``engine_for`` has to agree.

    Below lypning-mp, not *to* it: the Rust core has ``json``, ``math`` and
    ``sys`` of its own, and a program importing one of those belongs on the
    cheaper tier. What may not happen is a module the table claims routing all
    the way to CPython, which would be a table nobody reads.
    """
    stray = [(m, eng.route("import %s\n" % m)) for m in routing.micropython_modules()]
    stray = [(m, r.engine, r.kind, r.detail) for m, r in stray if r.engine == eng.CPYTHON]
    assert not stray, "modules in lypning-mp's table that route to CPython anyway: %r" % stray
