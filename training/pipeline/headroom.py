"""Whether a population leaves room for the pre-registered effect to exist in it.

`EVAL2.md` §4 is the one home of the primary metric: the correct-and-native
first-draft rate, **macro-averaged over families**, sampled pass@1 at k = 16,
compared paired and cluster-bootstrapped by `split_group` through
`training_metrics.paired_comparison` (2,000 resamples, percentile interval).
The rule fires when the 95% interval's LOWER bound is above +3pp, the minimum
detectable effect `PREREGISTRATION.md` §7b fixes — §7b is the bar and nothing
else here: it is written in ΔSLR terms and the string "correct-and-native" does
not occur in it (`grep -c 'correct-and-native' training/PREREGISTRATION.md` → 0,
run 2026-09-18).

`EVAL2.md` §9 pre-registers the falsifier this module mechanises: a base rate
high enough that the +3pp rule cannot be met inside what is left of the ceiling
means the bank cannot host the effect, whatever the adapter does. That is a
property of the BANK, knowable before a GPU is booked and before a draw is paid
for, and this module makes it a command that re-runs on a file rather than a
paragraph someone has to remember.

**What this adds, plainly.** `stats.power_curve_clustered` — priced by `nt power
--eval2` — is the instrument for the rule: it simulates the §4 bootstrap itself
and returns power at a bank size, in the rule's own macro units. It is the
better answer wherever it can run, and it needs a pilot — per-case, per-draw
rows (`stats.pilot_from_rows`). This module needs only a summary `metrics.json`,
which is all that exists for the round-02 arms in the private artifact
repository, and over that summary it does four things and adds no statistics to
the curve: it attributes the headroom to populations (a counterweight's nominal
room is not room), it refuses rather than reports a zero, it TESTS whether the
rate it was handed is the rule's rate at all, and it does the boundary
arithmetic below. A ceiling argument is a necessary condition and never a power
calculation: when rows exist, run the curve.

Three quantities and nothing clever. `headroom` is 1 − correct-and-native, the
largest point lift that can exist because the endpoint is a rate with a ceiling;
it is an ESTIMATE, not an exact ceiling, because the rate it is taken from is a
point estimate with its own sampling error. `mde` is the bar. `slack` is what is
left for the interval once the bar is paid — and a rule that reads the interval's
LOWER bound needs the point estimate above the bar by at least the noise between
two arms, which is why `slack` and not `headroom` decides.

**The rate must be the rule's rate, and whether it is can be TESTED.** The §4
endpoint is a macro over families, and a case- or draw-weighted aggregate of the
same draws can sit on either side of it, so one cannot stand in for the other in
either direction. Where a scope carries a `by_family` block this module computes
the macro from it. Where it does not, the rate is not ASSUMED to be an
aggregate: a summary that reports its parts with a `families` count each states
an identity only a family macro satisfies — the whole is the family-count-
weighted mean of the parts — and `macro_decomposition` checks it. A match marks
the row `fam`; a miss, or a file that states no partition to test, marks it
`und`, the headline REFUSES to be read as the endpoint, and `can_fire` is `None`
rather than a verdict about a rule this file cannot answer. The arithmetic is
still printed, because it is the arithmetic of the number the file does carry.

The round-02 base-dev arm is that test's first subject and it PASSES: its
overall correct-and-native is the families-weighted mean of its two populations'
to the last bit, where case weights and draw weights on the same rows both land
6.0pp away (`nt headroom <base-dev metrics.json>`, run 2026-09-18). So its
coverage rate is §4's statistic and the headroom taken from it is headroom on
the right one. That exactness is about WHICH statistic the number is; it buys
nothing about the sampling error IN it. The arm is k = 4 — the setting §4 calls
a smoke setting against its own confirmatory k = 16 — on the DEV split, over 7
family clusters, which is a small cluster count for a bootstrap that resamples
families whole. A point estimate on the right statistic is a design signal, and
never arithmetic certainty.

A NOMINAL 1.0 OF HEADROOM IS NOT ROOM. `training.Verifier` scores a
`fallback-control` case `correct-control` with no native test at all, so its
correct-and-native rate is 0.0 by construction and lifting it means breaking the
case the population exists to protect. `COUNTERWEIGHT_POPULATIONS` names those,
they get a verdict of their own, and they never carry the headline; the whole
arm is reported too, with the share of its draws that come from them, because
an arm's mixed headroom is the number most likely to be misread as room.

It refuses rather than reports a zero. A metrics file with no `correct_native`
or no `draws` is a read of nothing, and a confident table over it is the defect
`STATUS.md` §10 rows S0b and S0c were opened twice to close: an absent artifact
that printed a zeros table at exit 0. Every missing or non-numeric field is
named in the refusal, and the CLI turns it into exit 2.

Library code does not print (root `CLAUDE.md` invariant 8): every function here
returns data or a string, and `cli.py` renders it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .training_contract import PROTOCOL_EVAL_DRAWS
from .training_data import POPULATIONS
from .training_types import TrainingError

#: `PREREGISTRATION.md` §7b: the lower bound of the 95% CI must exceed +3pp.
#: §7b is the bar and only the bar — the endpoint it states is ΔSLR, and the
#: endpoint this module reasons about is `EVAL2.md` §4's. A parameter everywhere
#: below, because the bar belongs to a protocol and this module only does its
#: arithmetic — `--mde 0` asks what any effect at all needs.
PREREGISTERED_MDE = 0.03

#: The largest arm-to-arm difference this programme has measured with nothing
#: changed that was supposed to matter. It is a legality delta and not a
#: correct-and-native one, so it is used here as a floor on the noise between two
#: arms and never as a variance estimate for this endpoint: an effect whose whole
#: interval must fit inside less than this has no instrument. `--noise 0` asks
#: the strict arithmetic question instead, and whichever value is in force is
#: reported beside the bar — with this provenance when it is this number
#: (`CLAUDE.md` invariant 3: no figure is printed without its run and date).
NOISE_FLOOR = 0.0157
NOISE_RUN = "qwen38-base-arm-v3 vs qwen38-base-torchref"
NOISE_DATE = "2026-09-14"
NOISE_PROVENANCE = ("dSLR between two kernels on identical weights, %s, measured "
                    "upstream on %s (STATUS.md section 2), not reproducible from "
                    "this tree" % (NOISE_RUN, NOISE_DATE))

#: Populations whose correct-and-native rate is a counterweight, not an
#: objective. A `fallback-control` case is correct when the model keeps the
#: import the engine refuses, so its 0.0 native rate is the design and its
#: nominal 1.0 of headroom is room to break the control rather than room for the
#: effect. The same name is also a capability label on the round-02 arms.
COUNTERWEIGHT_POPULATIONS = ("fallback-control",)

#: A scope is unreadable without all four: the rate is the endpoint, `draws` and
#: `cases` say whether anything was read, and `families` is the bootstrap's
#: cluster count — seven clusters is a small bootstrap at any case count, and a
#: blank column invites exactly the misreading this module exists to prevent.
REQUIRED_FIELDS = ("cases", "draws", "families", "correct_native")

#: The optional per-family block that settles a scope's rate outright.
#: `training_metrics.summarize` does not emit it today, which is why
#: `macro_decomposition` exists: a file with no per-family block can still SHOW
#: that its rate is the family macro, by reporting the parts it splits into.
FAMILY_KEY = "by_family"

#: How a row's rate was arrived at. `FAMILY_MACRO` is `EVAL2.md` §4's unit,
#: computed here from `by_family` or established by the decomposition test;
#: `UNDETERMINED` is a rate this file gives no way to check the weighting of.
#: `UNDETERMINED` is NOT "aggregate": what the weighting is remains unknown, and
#: an unknown weighting cannot be presented as the endpoint in either direction.
FAMILY_MACRO = "family-macro"
UNDETERMINED = "undetermined"

#: How a `FAMILY_MACRO` label was earned, for a reader who has to trust it, and
#: why an `UNDETERMINED` one was not.
FROM_BY_FAMILY = "the scope's own by_family block"
FROM_DECOMPOSITION = "the family-weighted decomposition of the file's own parts"
NO_PARTITION = "this file states no partition of its families to test"
FAILED_DECOMPOSITION = "the file's parts do not reproduce this rate at family weights"

#: How close the decomposition identity must hold. Each side is one weighted sum
#: of rates that were themselves one division each, so a file whose rates really
#: are family macros matches to the last few ulps; this tolerance absorbs those
#: and nothing else. It is deliberately far tighter than any weighting
#: difference could be, and a file that rounds its rates for printing fails it
#: and is reported `UNDETERMINED` — which is the honest answer, because rounded
#: rates cannot tell two weightings apart either.
MACRO_TOLERANCE = 1e-12

ROOM = "room"
INSUFFICIENT = "insufficient"
SATURATED = "saturated"
COUNTERWEIGHT = "counterweight"
#: A headline only, never a row: every population in the arm is a counterweight,
#: so the effect has nowhere to come from and the arm's own nominal headroom is
#: the controls' room to break. Reporting that as `ROOM` is the misreading.
NO_CARRIER = "no-carrier"

#: Worst first. The headline is the best verdict any carrier population reaches,
#: because one population with room is enough for an effect to come from
#: somewhere; `COUNTERWEIGHT` is not on this ladder and never competes for it.
_ORDER = (SATURATED, INSUFFICIENT, ROOM)


def _check_counterweights() -> None:
    """A renamed population must not leave a stale string deciding a verdict."""
    unknown = sorted(set(COUNTERWEIGHT_POPULATIONS) - set(POPULATIONS))
    if unknown:
        raise TrainingError("counterweight names no population: " + " ".join(unknown))


_check_counterweights()


def _number(where: str, block: Dict[str, Any], field: str) -> float:
    value = block.get(field)
    if value is None:
        raise TrainingError("%s has no %s" % (where, field))
    # `True` is an `int`; a boolean rate is a shape confusion, not a measurement.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrainingError("%s has a non-numeric %s: %r" % (where, field, value))
    return float(value)


def _rate(where: str, block: Dict[str, Any]) -> float:
    value = _number(where, block, "correct_native")
    if not 0.0 <= value <= 1.0:
        raise TrainingError("%s has correct_native %g outside [0, 1]" % (where, value))
    return value


def _validate(where: str, block: Any) -> Dict[str, float]:
    if not isinstance(block, dict):
        raise TrainingError("%s is not an object" % where)
    values = {field: _number(where, block, field) for field in REQUIRED_FIELDS}
    for field in ("cases", "draws", "families"):
        if values[field] <= 0:
            raise TrainingError("%s reports %g %s — a read of nothing is not a measurement"
                                % (where, values[field], field))
    _rate(where, block)
    return values


def family_macro(where: str, block: Dict[str, Any], families: int) -> Optional[float]:
    """The scope's correct-and-native rate in `EVAL2.md` §4's unit, or `None`.

    §4 macro-averages over families: each family's own rate, then families
    weighted equally. That is computable from a scope's own block only when the
    block carries the families, so `None` here is "this scope cannot answer the
    rule's question BY ITSELF" — `macro_decomposition` may still settle it from
    the parts the file reports — and never "the aggregate will do". A `by_family`
    that does not
    cover the scope's own `families` count is refused rather than averaged: a
    macro over a subset of the families is a different statistic again.
    """
    by_family = block.get(FAMILY_KEY)
    if by_family is None:
        return None
    if not isinstance(by_family, dict) or not by_family:
        raise TrainingError("%s has an empty or non-object %s" % (where, FAMILY_KEY))
    rates = []
    for name in sorted(by_family):
        entry = by_family[name]
        if not isinstance(entry, dict):
            raise TrainingError("%s family %r is not an object" % (where, name))
        rates.append(_rate("%s family %r" % (where, name), entry))
    if len(rates) != int(families):
        raise TrainingError("%s reports %d families but %s carries %d — a macro over "
                            "a subset of the families is not the rule's macro"
                            % (where, int(families), FAMILY_KEY, len(rates)))
    return sum(rates) / len(rates)


def _weighted(parts: List[Dict[str, Any]], field: str,
              whole: Dict[str, Any]) -> Optional[float]:
    """The parts' mean rate under ``field`` weights, or `None` if they are not a partition.

    A weighting whose parts do not add up to the whole's own count is not a
    weighting of the whole, and its mean answers a question nobody asked.
    """
    total = sum(part[field] for part in parts)
    if total <= 0 or total != whole[field]:
        return None
    return sum(part[field] * part["recorded_correct_native"] for part in parts) / total


def macro_decomposition(whole: Dict[str, Any], parts: List[Dict[str, Any]],
                        *, tolerance: float = MACRO_TOLERANCE) -> Optional[Dict[str, Any]]:
    """Test whether ``whole``'s recorded rate IS the macro over families, from its parts.

    A macro over families splits exactly along any partition of those families:
    the whole is each part's own macro weighted by how many families that part
    holds. A case- or draw-weighted rate obeys that identity only when the parts
    happen to hold cases (or draws) in family proportion, so a match is evidence
    that the file's rate is `EVAL2.md` §4's statistic and a miss is evidence
    that this file's rates are not consistently that statistic. This is a test
    run on the file, never an assumption about it, and it needs nothing but the
    per-scope `families` counts the file already carries. The rates compared are
    the ones the file RECORDS at each scope, because the hypothesis under test
    is about those numbers; a scope carrying its own `by_family` has settled its
    label without this test and keeps it.

    `None` is "this file states no identity to test": fewer than two parts,
    since one part reproduces the whole under every weighting alike, or parts
    whose `families` do not sum to the whole's, which is not a partition of them
    and whose mean is a different statistic again.

    ``distinguishes`` is False when case or draw weights predict the same value
    to ``tolerance``. The match still holds — the recorded rate IS the
    family-weighted mean of the parts — but the identity then cannot say which
    weighting produced it, so it is weaker evidence about the parts' own rates
    and the report says so. ``corroborated`` repeats the test on the same rows'
    `correct` rate, a second identity at the same weights that a coincidence
    would have to satisfy twice.
    """
    if len(parts) < 2:
        return None
    families = sum(part["families"] for part in parts)
    if families != whole["families"]:
        return None
    recorded = whole["recorded_correct_native"]
    expected = sum(part["families"] * part["recorded_correct_native"]
                   for part in parts) / families
    alternatives = {"case_weighted": _weighted(parts, "cases", whole),
                    "draw_weighted": _weighted(parts, "draws", whole)}
    corroborated = None
    if whole["correct"] is not None and all(part["correct"] is not None for part in parts):
        predicted = sum(part["families"] * part["correct"] for part in parts) / families
        corroborated = abs(predicted - whole["correct"]) <= tolerance
    result = {"parts": [part["name"] for part in parts], "families": families,
              "recorded": recorded, "expected": expected, "delta": expected - recorded,
              "tolerance": tolerance, "matches": abs(expected - recorded) <= tolerance,
              "distinguishes": any(value is not None and abs(value - recorded) > tolerance
                                   for value in alternatives.values()),
              "corroborated": corroborated}
    result.update(alternatives)
    return result


def _label(row: Dict[str, Any], basis: str, evidence: str) -> Dict[str, Any]:
    """Record how a row's rate was established, and what that lets the row answer.

    A row whose weighting is unknown answers `None`: the §4 rule is stated in
    the family macro, and a rate that may not be one neither clears it nor
    fails it. The verdict stands either way — it is the arithmetic of the
    number the file carries — but it is not an answer about the rule.
    """
    row["basis"] = basis
    row["basis_evidence"] = evidence
    row["can_fire"] = (None if row["counterweight"] or basis == UNDETERMINED
                       else row["verdict"] == ROOM)
    return row


def _row(scope: str, name: str, block: Any, mde: float, noise: float) -> Dict[str, Any]:
    where = "%s %r" % (scope, name)
    values = _validate(where, block)
    macro = family_macro(where, block, int(values["families"]))
    # The macro where the file carries the families to compute one; the file's
    # own rate otherwise, labelled UNDETERMINED here and possibly upgraded by
    # `macro_decomposition` in `assess`, which can test what this scope alone
    # cannot. Nothing downstream has to guess: `basis` says which it got.
    rate = values["correct_native"] if macro is None else macro
    headroom = 1.0 - rate
    slack = headroom - mde
    counterweight = name in COUNTERWEIGHT_POPULATIONS
    if counterweight:
        verdict = COUNTERWEIGHT
    elif headroom <= mde:
        # STRICT, and the boundary belongs on this side of it. The rule fires on
        # the 95% interval's LOWER bound exceeding `mde`, and a lower bound sits
        # strictly below its point estimate under any sampling noise at all
        # (`EVAL2.md` §7: "at a realised effect equal to +3pp the lower bound
        # sits under the point estimate and no size reaches 80% power"). A
        # headroom exactly equal to the bar therefore admits no firing effect
        # either, so `headroom == mde` is SATURATED and not ROOM.
        verdict = SATURATED
    elif slack <= noise:
        # Same reason one rung up: the interval has to fit inside the slack, and
        # an interval exactly as wide as the slack still puts its lower bound
        # AT the bar rather than above it.
        verdict = INSUFFICIENT
    else:
        verdict = ROOM
    correct = block.get("correct")
    row = {"scope": scope, "name": name,
            "cases": int(values["cases"]), "draws": int(values["draws"]),
            "families": int(values["families"]),
            "draws_per_case": values["draws"] / values["cases"],
            "correct": float(correct) if isinstance(correct, (int, float))
                       and not isinstance(correct, bool) else None,
            "correct_native": rate, "recorded_correct_native": values["correct_native"],
            "family_macro": macro,
            "headroom": headroom, "mde": mde,
            "noise": noise, "slack": slack, "counterweight": counterweight,
            "verdict": verdict}
    return _label(row, UNDETERMINED if macro is None else FAMILY_MACRO,
                  NO_PARTITION if macro is None else FROM_BY_FAMILY)


def _section(metrics: Dict[str, Any], key: str, scope: str,
             mde: float, noise: float) -> List[Dict[str, Any]]:
    block = metrics.get(key)
    if block is None:
        return []
    if not isinstance(block, dict):
        raise TrainingError("%s is not an object" % key)
    return [_row(scope, name, block[name], mde, noise) for name in sorted(block)]


def assess(metrics: Any, *, mde: float = PREREGISTERED_MDE,
           noise: float = NOISE_FLOOR) -> Dict[str, Any]:
    """Can a lift of at least ``mde`` exist in this arm, per population and capability.

    ``metrics`` is a `training_metrics.summarize` object — one arm's
    `metrics.json`. Returns rows, never a verdict about an adapter: this says
    what the BANK can host, and a bank that cannot host the effect reports "no
    win" whatever the adapter does.

    ``can_fire`` is ``True``/``False`` only when the headline was taken in
    `EVAL2.md` §4's unit — every headline row's rate either computed from a
    `by_family` block or shown to be the family macro by `macro_decomposition`,
    or an arm with no carrier at all, where the weighting cannot change the
    answer. Otherwise it is ``None``: the file gives no way to tell whether its
    rate is the rule's rate, and this module will not launder one into the
    other. ``decomposition`` carries the test itself for each section, so a
    reader can check the label rather than take it.
    """
    if not isinstance(metrics, dict):
        raise TrainingError("metrics must be a JSON object, not %s" % type(metrics).__name__)
    if not 0.0 <= mde < 1.0:
        raise TrainingError("mde %g is not a fraction in [0, 1)" % mde)
    if noise < 0.0:
        raise TrainingError("noise %g is negative" % noise)
    arm = _row("arm", "overall", metrics, mde, noise)
    populations = _section(metrics, "by_population", "population", mde, noise)
    capabilities = _section(metrics, "by_capability", "capability", mde, noise)
    # Each section that partitions the arm's families is an identity the file
    # states about its own rate, and the test decides the label. `by_population`
    # is the one that matters, because the headline is taken from it; a
    # `by_capability` that happens to partition the families is tested on the
    # same terms, and one that does not simply states nothing.
    decomposition = {"by_population": macro_decomposition(arm, populations),
                     "by_capability": macro_decomposition(arm, capabilities)}
    for key, parts in (("by_population", populations), ("by_capability", capabilities)):
        check = decomposition[key]
        if check is None:
            continue
        # The identity holds for the whole AND for each part: the whole is the
        # parts' macros at family weights, so a part that failed to be its own
        # families' macro would have to be cancelled out by another. A miss is
        # recorded on the rows it was run over, so the report can say the test
        # ran and what it said, rather than that there was nothing to test.
        for row in [arm] + parts:
            if row["basis"] == UNDETERMINED:
                _label(row, FAMILY_MACRO if check["matches"] else UNDETERMINED,
                       FROM_DECOMPOSITION if check["matches"] else FAILED_DECOMPOSITION)

    carriers = [r for r in populations if not r["counterweight"]]
    counterweights = [r for r in populations if r["counterweight"]]
    # With no `by_population` the arm is all that was read, and its headroom is
    # unattributed: it may be entirely a counterweight's nominal room. With
    # populations but no carrier among them, it provably is.
    if populations and not carriers:
        verdict, headline_from, headline_rows = NO_CARRIER, "none", []
    else:
        headline_rows = carriers or [arm]
        verdict = max((r["verdict"] for r in headline_rows), key=_ORDER.index)
        headline_from = "populations" if carriers else "arm"
    # NO_CARRIER is an endpoint answer whatever the weighting is: a population
    # whose native rate is 0 by design carries no effect at any family weight.
    basis = (FAMILY_MACRO
             if headline_rows and all(r["basis"] == FAMILY_MACRO for r in headline_rows)
             else UNDETERMINED)
    is_endpoint = basis == FAMILY_MACRO or verdict == NO_CARRIER
    # What the headline's label rests on, in the words of whatever earned it.
    evidence = sorted(set(r["basis_evidence"] for r in headline_rows))
    basis_evidence = ("; ".join(evidence) if evidence else
                      "every population is a counterweight, and no weighting of "
                      "zeroes is anything but zero")
    counterweight_draws = sum(r["draws"] for r in counterweights)
    k = arm["draws_per_case"]
    return {"mde": mde, "noise": noise, "protocol_draws": PROTOCOL_EVAL_DRAWS,
            "k": k, "confirmatory_k": PROTOCOL_EVAL_DRAWS,
            "k_below_confirmatory": k < PROTOCOL_EVAL_DRAWS,
            "arm": arm, "populations": populations, "capabilities": capabilities,
            "carriers": [r["name"] for r in carriers],
            "counterweights": [r["name"] for r in counterweights],
            "counterweight_draws": counterweight_draws,
            "counterweight_share": counterweight_draws / arm["draws"],
            "headline_from": headline_from, "decomposition": decomposition,
            "basis_evidence": basis_evidence,
            "basis": basis, "is_endpoint": is_endpoint,
            "sections": {"by_population": bool(populations),
                         "by_capability": bool(capabilities)},
            "verdict": verdict,
            "can_fire": (verdict == ROOM) if is_endpoint else None}


def _pp(value: float) -> str:
    return "%+6.2fpp" % (100.0 * value)


def _pct(value: Optional[float]) -> str:
    return "     -" if value is None else "%5.1f%%" % (100.0 * value)


def _wt(basis: str) -> str:
    return "fam" if basis == FAMILY_MACRO else "und"


def _basis_lines(result: Dict[str, Any]) -> List[str]:
    """How the `wt` column was decided, in numbers a reader can re-add.

    A label nobody can check is a label nobody should trust, so where the
    decomposition test decided it, the test is printed: the prediction, the
    recorded value, and what the other two weightings would have said.
    """
    # Whichever section stated the identity — `by_population` decides the
    # headline, so it is preferred, but a file that only partitions its families
    # by capability has still shown what its rate is.
    matched = [result["decomposition"][key] for key in ("by_population", "by_capability")
               if result["decomposition"][key] is not None
               and result["decomposition"][key]["matches"]]
    labelled = [row for row in [result["arm"]] + result["populations"] + result["capabilities"]
                if row["basis_evidence"] == FROM_DECOMPOSITION]
    if not matched or not labelled:
        return []
    check = matched[0]
    lines = ["  basis     the rows marked fam carry no %s block, so the rate was TESTED,"
             % FAMILY_KEY,
             "            not assumed:",
             "            the arm's recorded correct_native (%.12f) and the"
             % check["recorded"],
             "            families-weighted mean of its %d parts (%s)"
             % (len(check["parts"]), ", ".join(check["parts"])),
             "            over %d families differ by %.1e, inside %g. That identity is"
             % (check["families"], abs(check["delta"]), check["tolerance"]),
             "            what a macro over families decomposes into."]
    if check["distinguishes"]:
        case, draw = check["case_weighted"], check["draw_weighted"]
        if case is not None and draw is not None and abs(case - draw) <= check["tolerance"]:
            said = "case- and draw-weighted %.4f" % case
        else:
            said = ", ".join("%s %.4f" % (label, value)
                             for label, value in (("case-weighted", case),
                                                  ("draw-weighted", draw))
                             if value is not None)
        lines.append("            The same rows %s — excluded." % said)
    else:
        lines.append("            Case and draw weights predict the same value here, so the")
        lines.append("            identity holds but does not say which weighting produced it.")
    if check["corroborated"]:
        lines.append("            The same weights reproduce the arm's `correct` rate too.")
    lines.append("            So the rows marked fam are EVAL2.md section 4's statistic. That")
    lines.append("            is exactness about WHICH statistic this is, and buys nothing about")
    lines.append("            the sampling error in it.")
    return lines


def render(result: Dict[str, Any]) -> str:
    """The table, and the one sentence it was written to make re-runnable."""
    arm = result["arm"]
    lines = ["headroom for the pre-registered effect",
             "  endpoint  correct-and-native, MACRO OVER FAMILIES, paired and",
             "            cluster-bootstrapped by split_group (EVAL2.md section 4)",
             "  bar       %s on the 95%% interval's LOWER bound, strictly above"
             % _pp(result["mde"]),
             "            (PREREGISTRATION.md section 7b, and only the bar)"]
    if result["noise"] == NOISE_FLOOR:
        # `CLAUDE.md` invariant 3: the figure never appears without the run and
        # the date that produced it, and they go on its own line so no quoting
        # of that line can separate them.
        lines.append("  noise     %s arm-to-arm, measured upstream on %s:"
                     % (_pp(result["noise"]), NOISE_DATE))
        lines.append("            dSLR between two kernels on identical weights")
        lines.append("            (%s, STATUS.md section 2)," % NOISE_RUN)
        lines.append("            not reproducible from this tree. A legality delta, used as a")
        lines.append("            floor and never as this endpoint's variance.")
    else:
        lines.append("  noise     %s arm-to-arm, given on the command line, not measured here"
                     % _pp(result["noise"]))
    lines += _basis_lines(result)
    lines += ["  arm       %d cases   %d draws   %d families   k=%.1f draws/case"
              " (confirmatory k=%d)"
              % (arm["cases"], arm["draws"], arm["families"], result["k"],
                 result["confirmatory_k"]),
              "",
              "%-11s %-18s %6s %6s %5s %7s %7s %4s %9s %9s  %s"
              % ("scope", "name", "cases", "draws", "fams", "correct", "native",
                 "wt", "headroom", "slack", "verdict")]
    for row in [arm] + result["populations"] + result["capabilities"]:
        lines.append("%-11s %-18s %6d %6d %5d %7s %7s %4s %9s %9s  %s"
                     % (row["scope"], row["name"][:18], row["cases"], row["draws"],
                        row["families"], _pct(row["correct"]), _pct(row["correct_native"]),
                        _wt(row["basis"]), _pp(row["headroom"]), _pp(row["slack"]),
                        row["verdict"].upper()))
    lines.append("")
    lines.append("  wt: fam = EVAL2.md section 4's macro over families, computed from a by_family")
    lines.append("  block or shown by the decomposition test; und = a rate whose weighting this")
    lines.append("  file gives no way to check, which is not the same as knowing it to be an")
    lines.append("  aggregate. Every rate is a point estimate with its own sampling error, so")
    lines.append("  headroom is an estimate and never an exact ceiling.")
    if result["k_below_confirmatory"]:
        lines.append("  drawn at k=%.1f, below the confirmatory k=%d: EVAL2.md section 4 calls"
                     % (result["k"], result["confirmatory_k"]))
        lines.append("  the runner's default of 4 draws a smoke setting, in as many words. A")
        lines.append("  dev arm at a smoke k is not the confirmatory instrument: fewer draws")
        lines.append("  widen the interval and leave every rate above noisier than it looks.")
    if result["counterweights"]:
        lines.append("  %d of %d draws (%.0f%%) are %s, where correct-and-native is 0"
                     % (result["counterweight_draws"], arm["draws"],
                        100.0 * result["counterweight_share"],
                        "/".join(result["counterweights"])))
        lines.append("  BY DESIGN — the right answer keeps the import. That nominal headroom")
        lines.append("  is room to break the control, and the arm row inherits it: read the")
        lines.append("  carrier populations, never the arm.")
    if not result["sections"]["by_population"]:
        lines.append("  no by_population section: this arm's headroom is unattributed, and")
        lines.append("  the verdict below is the arm's own.")
    lines.append("")
    if not result["is_endpoint"]:
        # The refusal comes before the arithmetic, so no reader reaches the
        # verdict line without it. An unknown weighting is not a bound on the
        # family macro in either direction, so this cannot clear the rule and
        # cannot condemn it either.
        lines.append("  BASIS UNDETERMINED. EVAL2.md section 4 freezes the primary metric as")
        lines.append("  the correct-and-native rate MACRO-AVERAGED OVER FAMILIES. No headline")
        lines.append("  row here carries a %s block, and" % FAMILY_KEY)
        lines.append("  %s," % result["basis_evidence"])
        lines.append("  so what the rate above is weighted by is unknown — not known to be an")
        lines.append("  aggregate, unknown. An aggregate can sit on either side of the family")
        lines.append("  macro, so this table neither clears the rule nor condemns it — see the")
        lines.append("  instrument named at the foot of this table. What follows is the same")
        lines.append("  arithmetic on the rate the file does carry:")
    if result["verdict"] == ROOM:
        lines.append("  ROOM. Some carrier population can host a lift of at least %s with"
                     % _pp(result["mde"]))
        lines.append("  %s to spare for the interval. Necessary, not sufficient: a lift that"
                     % _pp(result["noise"]))
        lines.append("  can exist is not a lift that happened.")
    elif result["verdict"] == NO_CARRIER:
        lines.append("  NO CARRIER. Every population in this arm is a counterweight, so the")
        lines.append("  effect has nowhere to come from: the arm's %s of nominal headroom is"
                     % _pp(arm["headroom"]))
        lines.append("  the controls' room to break. This arm cannot measure the endpoint.")
    else:
        lines.append("  %s. No carrier population can host an effect this rule could fire on:"
                     % result["verdict"].upper())
        # The same rows the headline was taken from: an arm whose populations
        # are ALL counterweights has no carrier, and the arm is what was read.
        for row in [r for r in result["populations"] if not r["counterweight"]] or [arm]:
            lines.append("    %-18s ceiling leaves %s, bar takes %s, %s left for the"
                         % (row["name"][:18], _pp(row["headroom"]),
                            _pp(-row["mde"]), _pp(row["slack"])))
            lines.append("    %-18s whole 95%% interval, over %d family clusters."
                         % ("", row["families"]))
        lines.append("  The bank is the blocker, not the adapter. Retarget the population")
        lines.append("  before buying draws against this one.")
        if result["is_endpoint"]:
            # Said here and only here: the reader who reached a verdict on the
            # rule's own statistic is the one at risk of reading it as a proof.
            lines.append("  A strong design signal, not arithmetic certainty: every rate above is")
            lines.append("  a point estimate at k=%.1f with its own sampling error, and firing"
                         % result["k"])
            lines.append("  the rule from here would need a near-perfect adapter AND a")
            lines.append("  near-zero-width interval at once.")
    # Always, on every verdict: this is a ceiling argument over a summary, and
    # the thing that prices the rule is a simulation over rows. Naming it here
    # rather than only in the refusal keeps the reader who got an answer from
    # mistaking it for the one the protocol will take.
    lines.append("")
    lines.append("  A ceiling argument from a summary is a necessary condition, never a power")
    lines.append("  calculation. The instrument for the rule is stats.power_curve_clustered")
    lines.append("  (`nt power --eval2 --rows <rows JSONL>`) over per-case, per-draw rows, or")
    lines.append("  training_metrics.paired_comparison over two arms' draws. Where those rows")
    lines.append("  exist, run them; this module reads the summary that is all an arm left.")
    return "\n".join(lines)
