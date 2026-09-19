"""The saturation finding, pinned as arithmetic instead of as a paragraph.

The one that matters is `test_the_recorded_base_arm_cannot_host_the_effect`: on
the real round-02 base arm the coverage population sits at 96.86%
correct-and-native, which leaves an estimated 3.14pp to the ceiling against a
+3pp bar, and the whole 95% interval would have to fit in the 0.14pp that
remains. The finding was made once in prose on 2026-09-18; this is the fixture
that re-makes it, and a bank that moves has to move this number before it can
claim otherwise.

That number is on the RIGHT statistic, and the fixture proves it rather than
assuming it either way. The file carries no `by_family` block, but it does carry
a `families` count per population, and its overall `correct_native` is the
families-weighted mean of its two populations' to the last bit while case and
draw weights on the same rows land 6pp away — an identity only a macro over
families satisfies. `test_the_recorded_arm_is_a_detected_family_macro` is that
test; an earlier revision of this file called the same number a case-weighted
aggregate, which the arithmetic refutes.

One caveat travels with it and is pinned here rather than remembered: it is a
k=4 arm on the DEV split over 7 family clusters, and §4 calls 4 draws a smoke
setting against its own confirmatory k=16. So every rate in it is a point
estimate with its own sampling error, the headroom is an estimate and not an
exact ceiling, and the finding is a design signal and not a proof
(`test_a_detected_endpoint_still_refuses_to_be_certainty`).

The rest pin the ways the table could lie: an arm whose headroom is really a
`fallback-control` counterweight's nominal room, a bar boundary read inclusively
when the rule is strict, and a metrics file with a field missing, which must
refuse rather than print a zeros table at exit 0 — `STATUS.md` §10 rows S0b and
S0c, the same defect twice.
"""

from __future__ import annotations

import json

import pytest

from pipeline import headroom
from pipeline.training_contract import PROTOCOL_EVAL_DRAWS
from pipeline.training_data import POPULATIONS

#: `round-02/6aacd5cfb1dc2b62dc590b82/base-dev/metrics.json`, base arm,
#: unadapted, DEV split, read on 2026-09-18 from the private artifact repository
#: `headforce/lypning-round02-artifacts`; measured upstream on 2026-09-18, not
#: reproducible from this tree. 1,024 draws over 256 cases is k = 4, and the
#: file carries no per-family breakdown. The per-capability rates were recorded
#: to four decimals without their denominators and are pinned separately below.
#: No `by_family` block — the per-population `families` counts are what let the
#: module test, rather than assume, what the rate is weighted by.
BASE_DEV = {
    "cases": 256, "draws": 1024, "families": 9, "truncation_rate": 0.0,
    "correct": 0.93011063011063, "correct_native": 0.7533910533910534,
    "by_population": {
        "coverage": {"cases": 215, "draws": 860, "families": 7,
                     "correct": 0.9686456400742115,
                     "correct_native": 0.9686456400742115},
        "fallback-control": {"cases": 41, "draws": 164, "families": 2,
                             "correct": 0.7952380952380953,
                             "correct_native": 0.0},
    },
}

#: The same arm's correct-and-native rate by capability, as recorded. Their mean
#: — 0.9659 over the five coverage capabilities — is a macro over CAPABILITIES
#: and not over families, so it is not the `EVAL2.md` §4 endpoint either; it is
#: pinned because the per-capability rows are what say where the ceiling bites.
BASE_DEV_CAPABILITY_NATIVE = {"set-ops": 1.0, "text": 0.9964, "argv-arith": 0.9929,
                              "int-reduce": 0.9545, "stdlib-module": 0.8857,
                              "fallback-control": 0.0}

#: SYNTHETIC, and deliberately so: no arm in this tree carries a `by_family`
#: block, so the explicit-macro path has no recorded fixture to run on. Two
#: families at 0.80 and 0.60 macro to 0.70 while the file's own rate says 0.90
#: — the two statistics disagree by 20pp here on purpose, because the point is
#: that neither bounds the other. k = 64/4 = 16, the confirmatory setting. Its
#: single population states no partition, so nothing here is decided by the
#: decomposition test.
WITH_FAMILIES = {
    "cases": 4, "draws": 64, "families": 2, "correct": 0.95, "correct_native": 0.90,
    "by_family": {"alpha": {"cases": 2, "draws": 32, "families": 1,
                            "correct_native": 0.80},
                  "beta": {"cases": 2, "draws": 32, "families": 1,
                           "correct_native": 0.60}},
    "by_population": {
        "coverage": {"cases": 4, "draws": 64, "families": 2, "correct_native": 0.90,
                     "by_family": {"alpha": {"correct_native": 0.80},
                                   "beta": {"correct_native": 0.60}}},
    },
}


def _capabilities(rates):
    """The recorded rates with placeholder denominators, which change nothing.

    Only the rates were recorded. A verdict is a function of the rate, the bar
    and the noise floor alone, so the counts here are a shape the validator
    needs and are deliberately uniform rather than guessed at.
    """
    return {name: {"cases": 8, "draws": 32, "families": 1, "correct_native": rate}
            for name, rate in rates.items()}


def test_the_recorded_base_arm_cannot_host_the_effect():
    """3.14pp of estimated ceiling, a 3pp bar, 0.14pp left for the whole interval.

    The arm's own 75.3% looks like 24.7pp of room. It is not: 16% of its draws
    are the fallback-control counterweight, whose 0.0 is the design. The
    carriers decide, and the only carrier is saturated in the sense that
    matters — a lift large enough to fire the rule cannot exist in it.
    """
    result = headroom.assess(BASE_DEV)
    coverage = next(r for r in result["populations"] if r["name"] == "coverage")
    assert coverage["headroom"] == pytest.approx(0.0313543599257885)
    assert coverage["slack"] == pytest.approx(0.0013543599257885)
    assert coverage["verdict"] == headroom.INSUFFICIENT
    assert coverage["can_fire"] is False
    assert coverage["families"] == 7 and coverage["draws_per_case"] == 4.0

    assert result["verdict"] == headroom.INSUFFICIENT
    assert result["headline_from"] == "populations"
    assert result["carriers"] == ["coverage"]
    # The arm in isolation would have said "room", which is the misreading.
    assert result["arm"]["verdict"] == headroom.ROOM
    assert result["arm"]["headroom"] == pytest.approx(0.2466089466089466)
    assert result["counterweight_draws"] == 164
    assert result["counterweight_share"] == pytest.approx(164 / 1024)


def test_the_recorded_arm_is_a_detected_family_macro():
    """The file's rate IS the rule's rate, and the module tests that rather than assuming.

    `EVAL2.md` §4 freezes the primary metric as correct-and-native macro-averaged
    over FAMILIES. This file carries no `by_family` block, so an earlier revision
    called it a case-weighted aggregate. The arithmetic says otherwise: 9
    families split 7/2 between the populations, and

        (7 * 0.9686456400742115 + 2 * 0.0) / 9 == 0.7533910533910534

    is the file's own overall `correct_native`, to the last bit. Case weights on
    the same rows give 0.8135 and draw weights the same again — both excluded —
    so the identity is one only a macro over families satisfies, and the
    coverage row's 0.9686 is the macro over its 7 families.
    """
    result = headroom.assess(BASE_DEV)
    check = result["decomposition"]["by_population"]
    assert check["expected"] == pytest.approx(0.7533910533910534, abs=1e-15)
    assert check["recorded"] == check["expected"] and check["delta"] == 0.0
    assert check["matches"] is True and check["distinguishes"] is True
    assert check["case_weighted"] == pytest.approx(0.813510986781076)
    assert check["draw_weighted"] == pytest.approx(0.813510986781076)
    # `correct` decomposes at the same weights: one coincidence would have to
    # hold twice over, on rates that share nothing but their families.
    assert check["corroborated"] is True
    assert check["parts"] == ["coverage", "fallback-control"]

    assert result["basis"] == headroom.FAMILY_MACRO
    assert result["basis_evidence"] == headroom.FROM_DECOMPOSITION
    assert result["is_endpoint"] is True
    assert result["can_fire"] is False              # a verdict about the rule, not a shrug
    assert all(r["basis"] == headroom.FAMILY_MACRO
               for r in [result["arm"]] + result["populations"])
    # Detected, not computed here: no scope carries the per-family block.
    assert result["arm"]["family_macro"] is None
    text = headroom.render(result)
    assert "BASIS UNDETERMINED." not in text
    assert "so the rate was TESTED," in text and "not assumed:" in text
    assert "MACRO OVER FAMILIES" in text and "EVAL2.md section 4" in text
    # The bar is §7b's; the endpoint is not, and the header may not say it is.
    assert "PREREGISTRATION.md section 7b, and only the bar" in text


def test_a_detected_endpoint_still_refuses_to_be_certainty():
    """The right statistic at a smoke k is a design signal, not a proof.

    The identity above is exact arithmetic about WHICH statistic the number is.
    It says nothing about the sampling error IN it: this is k=4 on a dev split
    over 7 family clusters, and the report may not read as though the ceiling
    were known to the last basis point.
    """
    text = headroom.render(headroom.assess(BASE_DEV))
    assert "A strong design signal, not arithmetic certainty" in text
    assert "near-perfect adapter AND a" in text
    assert "point estimate at k=4.0 with its own sampling error" in text
    assert "exactness about WHICH statistic this is, and buys nothing about" in text
    assert "headroom is an estimate and never an exact ceiling" in text
    assert "the arithmetic is exact" not in text


def test_the_macro_hypothesis_is_tested_and_can_fail():
    """A rate that is NOT the families-weighted mean of its parts is not labelled one.

    The same rows with the arm's rate replaced by the case-weighted aggregate of
    them: the identity misses by 6pp, so the file's rates are not consistently
    the §4 statistic and the module says the basis is undetermined rather than
    picking one.
    """
    aggregate = dict(BASE_DEV, correct_native=0.813510986781076)
    result = headroom.assess(aggregate)
    check = result["decomposition"]["by_population"]
    assert check["matches"] is False
    assert check["delta"] == pytest.approx(-0.06012, abs=5e-5)
    assert result["basis"] == headroom.UNDETERMINED
    assert result["basis_evidence"] == headroom.FAILED_DECOMPOSITION
    assert result["is_endpoint"] is False and result["can_fire"] is None
    assert all(r["can_fire"] is None for r in result["populations"])
    text = headroom.render(result)
    assert "BASIS UNDETERMINED." in text
    assert headroom.FAILED_DECOMPOSITION in text
    assert " und " in text
    assert "so the rate was TESTED," not in text


def test_a_file_that_states_no_partition_is_undetermined_not_aggregate():
    """No `by_family`, and nothing to test it against: the label says unknown.

    One part reproduces the whole under every weighting alike, and parts whose
    families do not add up to the whole's are not a partition of them. Neither
    states an identity, so neither is evidence — and the module says the
    weighting is unknown rather than asserting an aggregate it cannot see.
    """
    alone = {k: v for k, v in BASE_DEV.items() if k != "by_population"}
    result = headroom.assess(alone)
    assert result["decomposition"]["by_population"] is None
    assert result["basis"] == headroom.UNDETERMINED
    assert result["basis_evidence"] == headroom.NO_PARTITION
    assert result["can_fire"] is None
    assert "BASIS UNDETERMINED." in headroom.render(result)

    one_part = dict(alone, by_population={"coverage": BASE_DEV["by_population"]["coverage"]})
    assert headroom.assess(one_part)["decomposition"]["by_population"] is None

    # Six capabilities of one family each do not partition nine families.
    capabilities = headroom.assess(
        dict(BASE_DEV, by_capability=_capabilities(BASE_DEV_CAPABILITY_NATIVE)))
    assert capabilities["decomposition"]["by_capability"] is None
    assert all(r["basis"] == headroom.UNDETERMINED
               for r in capabilities["capabilities"])
    # ... and the populations, which DO partition them, are unaffected by that.
    assert capabilities["basis"] == headroom.FAMILY_MACRO


def test_a_family_macro_is_the_endpoint_and_an_aggregate_is_not():
    """Where the file lists its families, the rule's own statistic is computed."""
    result = headroom.assess(WITH_FAMILIES)
    assert result["arm"]["family_macro"] == pytest.approx(0.70)
    assert result["arm"]["recorded_correct_native"] == pytest.approx(0.90)
    assert result["arm"]["correct_native"] == pytest.approx(0.70)
    assert result["arm"]["headroom"] == pytest.approx(0.30)     # not 0.10
    assert result["basis"] == headroom.FAMILY_MACRO
    assert result["is_endpoint"] is True and result["can_fire"] is True
    assert result["verdict"] == headroom.ROOM
    text = headroom.render(result)
    assert "NOT THE ENDPOINT." not in text
    assert " fam " in text and "ROOM." in text


def test_a_by_family_that_does_not_cover_the_scope_refuses():
    """A macro over a subset of the families is a different statistic again."""
    short = dict(WITH_FAMILIES, families=3)
    with pytest.raises(ValueError, match="reports 3 families but by_family carries 2"):
        headroom.assess(short)
    empty = dict(WITH_FAMILIES, by_family={})
    with pytest.raises(ValueError, match="empty or non-object by_family"):
        headroom.assess(empty)
    bad = dict(WITH_FAMILIES,
               by_family={"alpha": {"correct_native": 0.8}, "beta": {"correct_native": 1.2}})
    with pytest.raises(ValueError, match="family 'beta' has correct_native 1.2 outside"):
        headroom.assess(bad)


def test_the_counterweight_never_carries_the_headline():
    """A `fallback-control` 0.0 is the design, and its 1.0 is not headroom."""
    result = headroom.assess(BASE_DEV)
    control = next(r for r in result["populations"] if r["name"] == "fallback-control")
    assert control["headroom"] == 1.0
    assert control["verdict"] == headroom.COUNTERWEIGHT
    assert control["can_fire"] is None
    assert result["counterweights"] == ["fallback-control"]
    assert control["name"] not in result["carriers"]


def test_only_one_capability_of_six_has_room():
    """Five of the six recorded capabilities are at or past the ceiling.

    `int-reduce` is the boundary the arithmetic really has: 4.55pp of ceiling
    less the 3pp bar leaves 1.55pp, under the 1.57pp noise floor by 0.02pp. It
    is pinned at the recorded rate, not at a rounder one.
    """
    metrics = dict(BASE_DEV, by_capability=_capabilities(BASE_DEV_CAPABILITY_NATIVE))
    rows = {r["name"]: r["verdict"] for r in headroom.assess(metrics)["capabilities"]}
    assert rows == {"set-ops": headroom.SATURATED, "text": headroom.SATURATED,
                    "argv-arith": headroom.SATURATED, "int-reduce": headroom.INSUFFICIENT,
                    "stdlib-module": headroom.ROOM,
                    "fallback-control": headroom.COUNTERWEIGHT}


def test_the_bar_boundary_is_strict_on_both_rungs():
    """A headroom equal to the bar cannot fire, and neither can slack equal to noise.

    The rule is the 95% interval's LOWER bound ABOVE the bar, and a lower bound
    sits strictly under its point estimate at any sampling noise — `EVAL2.md` §7
    says no bank size reaches 80% power at a realised effect equal to +3pp. So
    the boundary belongs on the refusing side, and `<` would have let it fire.
    The rates here are exact in binary on purpose: `1 - 0.97` is not 0.03.
    """
    arm = {k: v for k, v in BASE_DEV.items() if k != "by_population"}
    exact = headroom.assess(dict(arm, correct_native=0.75), mde=0.25, noise=0.0)
    assert exact["arm"]["headroom"] == 0.25
    assert exact["verdict"] == headroom.SATURATED
    above = headroom.assess(dict(arm, correct_native=0.7499), mde=0.25, noise=0.0)
    assert above["verdict"] == headroom.ROOM

    # One rung up: the interval must fit INSIDE the slack, so an interval
    # exactly as wide as the slack puts its lower bound at the bar, not above.
    tight = headroom.assess(dict(arm, correct_native=0.5), mde=0.25, noise=0.25)
    assert tight["arm"]["slack"] == 0.25
    assert tight["verdict"] == headroom.INSUFFICIENT


def test_k_is_reported_and_a_smoke_k_is_warned_about():
    """A k=4 arm is a smoke setting by `EVAL2.md` §4's own words; say so."""
    smoke = headroom.assess(BASE_DEV)
    assert smoke["k"] == 4.0 and smoke["confirmatory_k"] == PROTOCOL_EVAL_DRAWS == 16
    assert smoke["k_below_confirmatory"] is True
    text = headroom.render(smoke)
    assert "k=4.0 draws/case (confirmatory k=16)" in text
    assert "drawn at k=4.0, below the confirmatory k=16" in text
    assert "a smoke setting" in text
    assert "point estimate with its own sampling error" in text

    full = headroom.assess(WITH_FAMILIES)
    assert full["k"] == 16.0 and full["k_below_confirmatory"] is False
    assert "below the confirmatory" not in headroom.render(full)


def test_the_bar_and_the_noise_floor_are_both_parameters():
    """The arithmetic is the module's; the thresholds belong to the protocol."""
    assert headroom.assess(BASE_DEV, mde=0.0)["verdict"] == headroom.ROOM
    # Strict ceiling arithmetic: 3.14pp of headroom does clear a 3pp bar by
    # itself, and it is the interval that cannot fit. Both answers are true and
    # they are different questions, so the command asks whichever it is given.
    strict = headroom.assess(BASE_DEV, noise=0.0)
    assert strict["verdict"] == headroom.ROOM
    # ... and on this file both answers ARE the endpoint's, because the rate
    # was shown to be the §4 macro. The threshold moved, not the statistic.
    assert strict["can_fire"] is True and strict["basis"] == headroom.FAMILY_MACRO
    assert headroom.assess(BASE_DEV, mde=0.05)["verdict"] == headroom.SATURATED


def test_the_noise_figure_never_appears_without_its_run_and_date():
    """`CLAUDE.md` invariant 3, on the one number in here that is not re-runnable."""
    text = headroom.render(headroom.assess(BASE_DEV))
    quoting = [line for line in text.splitlines() if "1.57" in line]
    assert quoting and all(headroom.NOISE_DATE in line for line in quoting)
    assert headroom.NOISE_RUN in text and "not reproducible from this tree" in text
    # A noise given on the command line is not that measurement and may not
    # borrow its provenance.
    given = headroom.render(headroom.assess(BASE_DEV, noise=0.02))
    assert headroom.NOISE_RUN not in given and "not measured here" in given


def test_a_missing_field_refuses_instead_of_reporting_a_zero():
    """A metrics file that cannot answer is not a metrics file that answers no.

    `STATUS.md` §10 rows S0b and S0c were both opened by a stage that read
    nothing and printed a confident zero. Every refusal names the field and the
    scope it was missing from.
    """
    for field in headroom.REQUIRED_FIELDS:
        broken = {k: v for k, v in BASE_DEV.items() if k != field}
        with pytest.raises(ValueError, match="arm 'overall' has no %s" % field):
            headroom.assess(broken)
        population = {k: v for k, v in BASE_DEV["by_population"]["coverage"].items()
                      if k != field}
        with pytest.raises(ValueError, match="population 'coverage' has no %s" % field):
            headroom.assess(dict(BASE_DEV, by_population=dict(
                BASE_DEV["by_population"], coverage=population)))


def test_an_empty_or_impossible_measurement_refuses_too():
    with pytest.raises(ValueError, match="a read of nothing is not a measurement"):
        headroom.assess(dict(BASE_DEV, draws=0))
    with pytest.raises(ValueError, match="non-numeric correct_native"):
        headroom.assess(dict(BASE_DEV, correct_native=True))
    with pytest.raises(ValueError, match="correct_native 1.2 outside"):
        headroom.assess(dict(BASE_DEV, correct_native=1.2))
    with pytest.raises(ValueError, match="metrics must be a JSON object"):
        headroom.assess([BASE_DEV])
    with pytest.raises(ValueError, match="by_population is not an object"):
        headroom.assess(dict(BASE_DEV, by_population=[]))


def test_an_unattributed_arm_says_so_rather_than_guessing():
    """With no `by_population`, the arm is the headline and the report says it."""
    result = headroom.assess({k: v for k, v in BASE_DEV.items() if k != "by_population"})
    assert result["headline_from"] == "arm" and result["carriers"] == []
    assert result["verdict"] == headroom.ROOM
    assert result["can_fire"] is None       # weighting undetermined, and unattributed
    assert "unattributed" in headroom.render(result)


def test_an_arm_of_nothing_but_controls_has_no_carrier_and_says_so():
    """The counterweight's nominal 1.0 must never become the arm's headline.

    An arm restricted to `fallback-control` has 100pp of nominal headroom and no
    population that may spend any of it. Falling back to the arm row there would
    report ROOM on a bank that cannot measure the endpoint at all. This one
    answer does not depend on the weighting — a population whose native rate is
    0 by design carries no effect at any family weight — so it is an endpoint
    answer even where the weighting is undetermined, and `can_fire` is False,
    not None.
    """
    control = BASE_DEV["by_population"]["fallback-control"]
    result = headroom.assess(dict(control, by_population={"fallback-control": control}))
    assert result["carriers"] == [] and result["headline_from"] == "none"
    assert result["verdict"] == headroom.NO_CARRIER
    assert result["is_endpoint"] is True and result["can_fire"] is False
    assert result["arm"]["verdict"] == headroom.ROOM       # what it would have said
    assert "NO CARRIER." in headroom.render(result)


def test_a_counterweight_name_must_still_be_a_population():
    """A renamed population may not leave a stale string deciding a verdict."""
    assert set(headroom.COUNTERWEIGHT_POPULATIONS) <= POPULATIONS


def test_render_states_the_finding_and_never_only_the_arm():
    text = headroom.render(headroom.assess(
        dict(BASE_DEV, by_capability=_capabilities(BASE_DEV_CAPABILITY_NATIVE))))
    assert "INSUFFICIENT" in text and "coverage" in text
    assert "+3.14pp" in text and "+0.14pp" in text
    assert "carrier populations, never the arm." in text
    assert "The bank is the blocker, not the adapter." in text
    assert "confirmatory k=16" in text and "drawn at k=4.0" in text
    # The instrument for the rule itself is named on every verdict, because
    # this is a ceiling argument over a summary and never a power calculation.
    assert "power_curve_clustered" in text and "paired_comparison" in text
    assert "necessary condition, never a power" in text


def test_the_cli_exits_1_on_a_bank_that_cannot_host_the_effect(tmp_path, capsys):
    """The finding is a command with an exit code, not a paragraph to remember."""
    from pipeline import cli
    path = tmp_path / "metrics.json"
    path.write_text(json.dumps(BASE_DEV), encoding="utf-8")
    assert cli.main(["headroom", str(path)]) == 1
    assert "INSUFFICIENT" in capsys.readouterr().out

    # The bar is a parameter and the statistic is not: asked whether ANY effect
    # fits, the same file answers yes, on the endpoint, at exit 0.
    assert cli.main(["headroom", str(path), "--mde", "0"]) == 0
    out = capsys.readouterr().out
    assert "ROOM." in out and "BASIS UNDETERMINED." not in out

    assert cli.main(["headroom", str(path), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "insufficient" and payload["can_fire"] is False
    assert payload["basis"] == "family-macro"
    assert payload["decomposition"]["by_population"]["matches"] is True

    # A file whose weighting cannot be established exits 1 whatever it says:
    # a summary that cannot answer the rule is not one that answers yes.
    undetermined = tmp_path / "undetermined.json"
    undetermined.write_text(json.dumps(
        {k: v for k, v in BASE_DEV.items() if k != "by_population"}), encoding="utf-8")
    assert cli.main(["headroom", str(undetermined), "--mde", "0"]) == 1
    out = capsys.readouterr().out
    assert "ROOM." in out and "BASIS UNDETERMINED." in out

    families = tmp_path / "families.json"
    families.write_text(json.dumps(WITH_FAMILIES), encoding="utf-8")
    assert cli.main(["headroom", str(families)]) == 0
    assert "ROOM." in capsys.readouterr().out


def test_the_help_string_survives_argparse_expansion(capsys):
    """A help string carrying a literal `%` crashes `--help`, not the parser.

    `--mde`'s help interpolates its own default, so a second percent sign in it
    is read as a conversion when argparse expands the string — which happens at
    `--help` time and nowhere else, so nothing but this test would see it.
    """
    from pipeline import cli
    with pytest.raises(SystemExit) as exc:
        cli.main(["headroom", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "the rule's bar" in out
    assert headroom.NOISE_DATE in out           # invariant 3, in the help too


def test_the_cli_refuses_an_absent_or_incomplete_file(tmp_path, capsys):
    from pipeline import cli
    missing = tmp_path / "missing.json"
    assert cli.main(["headroom", str(missing)]) == 2
    captured = capsys.readouterr()
    assert captured.err.strip() == "not a file: %s" % missing
    assert captured.out == ""                     # never the zeros table

    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({k: v for k, v in BASE_DEV.items()
                                  if k != "correct_native"}), encoding="utf-8")
    assert cli.main(["headroom", str(broken)]) == 2
    captured = capsys.readouterr()
    assert "has no correct_native" in captured.err and captured.out == ""

    unparsable = tmp_path / "unparsable.json"
    unparsable.write_text("{", encoding="utf-8")
    assert cli.main(["headroom", str(unparsable)]) == 2
    assert capsys.readouterr().out == ""
