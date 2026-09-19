"""Whether a bank's programs are ones the engine already runs — the ceiling, per bank.

WHY. `EVAL2.md` §9's falsifier is a property of the BANK: a base rate so high
that the §4 rule (+3pp on the lower bound of a clustered bootstrap over the
correct-and-native family macro) cannot be met inside what is left of the
ceiling. :mod:`pipeline.headroom` reads that off a finished arm's
`metrics.json`, which costs a draw. This module reads a weaker but FREE
proxy off the bank file itself, before anything is booked: what fraction of
the programs a bank ships are served natively by the pinned engine.

WHAT THE PROXY IS AND IS NOT. It is not the endpoint. The endpoint is what a
MODEL writes first-draft; this is what the bank's own program does. The two are
joined only through how the bank was built, and the join differs by bank:

  bank_v2   `reference` was authored to be native; ~100% is expected and is an
            INSTRUMENT CHECK, not a finding. It says nothing about base-model
            saturation on its own — `metrics.json` said that.
  bank v3   `reference` is the program a base sample actually wrote (a
            ``synth.kind`` of ``native``) or a rule's repair of one
            (``repaired``). On a ``native`` row the base model demonstrably
            wrote a native program for that task at k=3, so the row's base
            native rate is >= 1/k by construction and empirically far higher:
            those rows are saturated the same way bank_v2 was. The refused
            first draft rides along as ``synth.original``, and THAT is the
            program with headroom in it. Hence ``--program original``.

So the number that decides a bank is the MIX — how many rows carry a first
draft the engine refuses — and this module reports the mix beside the rate
rather than collapsing them.

NOTHING HERE MAY BE PRINTED INTO A PUBLIC LOG. `kristerhedfors/lypning` is
public and an Actions log is world-readable, so :func:`render` prints
aggregates and refusal KINDS (the engine's own fixed vocabulary) and never a
task text, a program, a case id, or a refusal DETAIL — a detail interpolates
identifiers off the program that provoked it (``module-attr`` carries
``<module>.<name>``). ``--details`` opts back in and belongs to a local run.

Library code returns data; ``cli.py`` renders it and maps the exit code.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from . import engines as eng


def synth_kinds():
    """The admitted ``synth.kind`` values, asked of `pipeline.synth` itself."""
    from .synth import POPULATION_OF

    return tuple(POPULATION_OF)

#: A row's program under each read. ``original`` is present only on a row whose
#: first draft was refused and then repaired, which is the whole point of it.
PROGRAMS = ("reference", "original")

#: Verdicts :meth:`synth.Runner.engine_verdict` can return. The first three are
#: the census; the last three are witnesses — root ``CLAUDE.md`` invariant 1
#: says an engine that disagrees with CPython is a bug, never a data point.
VERDICTS = ("native", "refused", "mixed")
WITNESSES = ("bad-refusal", "crash", "mismatch")


def program_of(row: Dict[str, Any], which: str) -> Optional[str]:
    """The program this read wants from a schema-3 row, or None if it has none.

    ``original`` lives under ``synth`` because `pipeline.synth.to_case` puts the
    refused first draft there so a preference pair can be built later without
    re-running anything.
    """
    if which == "reference":
        program = row.get("reference")
    else:
        synth = row.get("synth")
        program = synth.get("original") if isinstance(synth, dict) else None
    return program if isinstance(program, str) and program.strip() else None


def _macro(per_family: Dict[str, List[bool]]) -> Optional[float]:
    """The §4 shape: mean over families of each family's rate, never over rows.

    A case-weighted rate and a family macro sit on either side of each other and
    neither stands in for the other; the rule reads the macro, so the macro is
    what is reported as the bank's rate.
    """
    if not per_family:
        return None
    return sum(sum(v) / len(v) for v in per_family.values()) / len(per_family)


def measure(rows: Iterable[Dict[str, Any]], verdict_of: Callable[[str, Sequence[Dict[str, Any]]], Any],
            *, which: str = "reference") -> Dict[str, Any]:
    """Run every row's program through the engine and census the verdicts.

    ``verdict_of`` is :meth:`synth.Runner.engine_verdict` — the same call
    `pipeline.synth` admitted the bank with, so this measures the bank against
    its own admission test and not a second opinion about it.

    Returns counts, the family macro of the native rate, the mix by
    ``synth.kind`` and by ``population``, and a refusal-kind histogram.
    Nothing is printed and nothing is written.
    """
    if which not in PROGRAMS:
        raise ValueError("program must be one of %s" % ", ".join(PROGRAMS))
    verdicts: Counter = Counter()
    kinds: Counter = Counter()
    details: Counter = Counter()
    by_kind: Counter = Counter()
    by_population: Counter = Counter()
    #: verdict census split by the field whose value the population NAMES: a
    #: `coverage` row that refuses and a `fallback-control` row that is served
    #: are each the bank contradicting its own label, and an aggregate rate
    #: hides both. `training.Score` pins a fallback-control row's contribution
    #: to correct_native at 0 whatever the model writes, so its refusals are
    #: not headroom and must never be counted as any.
    strata: Dict[str, Counter] = {}
    witnesses: List[Dict[str, Any]] = []
    per_family: Dict[str, List[bool]] = {}
    skipped = 0
    seen = 0
    for row in rows:
        seen += 1
        synth = row.get("synth") if isinstance(row.get("synth"), dict) else {}
        by_kind[str(synth.get("kind", "unlabelled"))] += 1
        by_population[str(row.get("population", "unlabelled"))] += 1
        program = program_of(row, which)
        if program is None:
            skipped += 1
            continue
        tests = row.get("tests") or []
        if not tests:
            skipped += 1
            continue
        verdict, buckets = verdict_of(program, tests)
        verdicts[verdict] += 1
        if verdict in WITNESSES:
            # Invariant 1: kept and named, never folded into a rate.
            witnesses.append({"case_id": str(row.get("case_id", "")), "verdict": verdict,
                              "family": str(row.get("family", ""))})
        for bucket in buckets:
            details[bucket] += 1
            kinds[bucket.split(":", 1)[0].strip()] += 1
        for label, value in (("population", row.get("population")), ("kind", synth.get("kind"))):
            if value:
                strata.setdefault("%s=%s" % (label, value), Counter())[verdict] += 1
        if verdict in VERDICTS:
            per_family.setdefault(str(row.get("family", "unnamed")), []).append(verdict == "native")
    measured = sum(verdicts[v] for v in VERDICTS)
    return {
        "program": which,
        "rows": seen,
        "skipped": skipped,
        "measured": measured,
        "verdicts": {v: verdicts[v] for v in VERDICTS},
        "witnesses": witnesses,
        "witness_verdicts": {w: verdicts[w] for w in WITNESSES if verdicts[w]},
        "native_rate_cases": (verdicts["native"] / measured) if measured else None,
        "native_rate_family_macro": _macro(per_family),
        "families": len(per_family),
        "by_stratum": {k: dict(sorted(v.items())) for k, v in sorted(strata.items())},
        "by_kind": dict(sorted(by_kind.items())),
        "by_population": dict(sorted(by_population.items())),
        "refusal_kinds": dict(sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))),
        "refusal_buckets": dict(sorted(details.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


def _pct(value: Optional[float]) -> str:
    return "-" if value is None else "%.4f" % value


def render(result: Dict[str, Any], *, details: bool = False) -> str:
    """Aggregates only. See the module docstring for why ``details`` is opt-in."""
    engine = result.get("engine") or {}
    lines = ["bank native-program census (read-only; no gate moves)",
             "engine %s  sha256 %s" % (engine.get("version", "?"), (engine.get("sha256") or "?")[:16]),
             "program %s   rows %d   measured %d   skipped %d   families %d"
             % (result["program"], result["rows"], result["measured"],
                result["skipped"], result["families"]),
             "native %d   refused %d   mixed %d"
             % (result["verdicts"]["native"], result["verdicts"]["refused"],
                result["verdicts"]["mixed"]),
             "native rate: case-weighted %s   family macro %s"
             % (_pct(result["native_rate_cases"]), _pct(result["native_rate_family_macro"])),
             "", "mix by synth.kind:   " + (", ".join("%s=%d" % kv for kv in result["by_kind"].items()) or "-"),
             "mix by population:   " + (", ".join("%s=%d" % kv for kv in result["by_population"].items()) or "-")]
    if result["by_stratum"]:
        lines.extend(("", "%-28s %8s %8s %8s %8s" % ("stratum", "native", "refused", "mixed", "rate")))
        for name, counts in result["by_stratum"].items():
            total = sum(counts.get(v, 0) for v in VERDICTS)
            lines.append("%-28s %8d %8d %8d %8s"
                         % (name, counts.get("native", 0), counts.get("refused", 0),
                            counts.get("mixed", 0),
                            "%.4f" % (counts.get("native", 0) / total) if total else "-"))
    if result["witness_verdicts"]:
        lines.extend(("", "WITNESSES (invariant 1: an engine bug, never a data point): "
                      + ", ".join("%s=%d" % kv for kv in sorted(result["witness_verdicts"].items()))))
    if result["refusal_kinds"]:
        lines.extend(("", "refusal kinds (engine vocabulary only):"))
        for kind, count in result["refusal_kinds"].items():
            lines.append("  %-20s %d" % (kind, count))
    if details:
        lines.extend(("", "refusal buckets (LOCAL ONLY: a detail can echo a program identifier):"))
        for bucket, count in result["refusal_buckets"].items():
            lines.append("  %-60s %d" % (bucket[:60], count))
    return "\n".join(lines)


# --- the free read: what the labels already say about the ceiling -------------

#: A ``synth.kind`` whose FIRST DRAFT the engine refused. ``repaired`` is the
#: only kind with movable room in it: the base model wrote a refused program and
#: a rule proved a native equivalent exists, so a trained model reaching that
#: equivalent is exactly the pre-registered effect. ``ceiling`` is refused too,
#: but `training.Score` returns ``correct-control`` for a `fallback-control` row
#: and `Score.native` is then False for EVERY arm: its room is nominal, never
#: movable, and `pipeline.headroom` says so in the same words.
DRAFT_REFUSED = ("repaired", "ceiling")
MOVABLE = ("repaired",)

#: The kinds `pipeline.synth` admits, named there and not re-listed here:
#: a second copy of that vocabulary is invariant 1's failure mode in a new file.
KINDS = tuple(sorted(synth_kinds()))


def first_draft_mix(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-family ceiling on the §4 macro delta, off the labels, executing nothing.

    THE ARITHMETIC AND ITS THREE ASSUMPTIONS, stated so they can be falsified.
    A row's ``synth.kind`` records what the pinned engine did with the program a
    base sample actually wrote, so it is already an observation about the base
    model and not only about the bank:

      ``native``    a base sample's own program was served natively, so the base
                    correct-and-native rate on that row is >= 1/samples by
                    construction. Assumed 1. **Optimistic about the base**: the
                    true rate is lower, and every point it is lower is headroom
                    this estimate does not credit.
      ``repaired``  the base sample's program was REFUSED and a rule's rewrite is
                    native, so the base rate is assumed 0 and the row is movable.
                    **Optimistic about the effect**: a sibling sample may have
                    been native, and every such row is room this over-credits.
      ``ceiling``   refused, and the right answer keeps the import. Pinned at 0
                    for both arms. Counted in the denominator, never as room.

    So the returned ``macro_delta_ceiling`` is the mean over families of the
    movable fraction — `EVAL2.md` §4's unit, never a row-weighted rate — and it
    is a CEILING on a point estimate, not a power calculation: `stats.
    power_curve_clustered` is the instrument for the rule and it needs a pilot.
    A bank whose ceiling is under the `PREREGISTRATION.md` §7b bar cannot meet a
    rule that reads the LOWER bound of an interval around a point estimate that
    cannot itself reach the bar.

    A bank whose rows carry no ``synth.kind`` cannot be read this way at all —
    `bank_v2` is such a bank — and this returns ``macro_delta_ceiling`` of None
    rather than a zero. Refusing is the point: a zero here reads as "no room".
    """
    per_family: Dict[str, Counter] = {}
    labelled = 0
    unlabelled = 0
    for row in rows:
        synth = row.get("synth") if isinstance(row.get("synth"), dict) else {}
        kind = synth.get("kind")
        family = str(row.get("family", "unnamed"))
        counter = per_family.setdefault(family, Counter())
        if kind in KINDS:
            counter[kind] += 1
            labelled += 1
        else:
            counter["unlabelled"] += 1
            unlabelled += 1
    families = []
    for family, counter in sorted(per_family.items()):
        total = sum(counter.values())
        known = sum(counter[k] for k in KINDS)
        families.append({
            "family": family, "rows": total,
            "native": counter["native"], "repaired": counter["repaired"],
            "ceiling": counter["ceiling"], "unlabelled": counter["unlabelled"],
            "movable_fraction": (sum(counter[k] for k in MOVABLE) / total) if known == total else None,
            "refused_draft_fraction": (sum(counter[k] for k in DRAFT_REFUSED) / total)
                                      if known == total else None,
        })
    readable = [f for f in families if f["movable_fraction"] is not None]
    return {
        "rows": labelled + unlabelled,
        "labelled": labelled,
        "unlabelled": unlabelled,
        "families": len(families),
        "readable_families": len(readable),
        "macro_delta_ceiling": (sum(f["movable_fraction"] for f in readable) / len(readable))
                               if readable and not unlabelled else None,
        "macro_refused_draft": (sum(f["refused_draft_fraction"] for f in readable) / len(readable))
                               if readable and not unlabelled else None,
        "by_family": families,
    }


def render_mix(result: Dict[str, Any], *, mde: float, limit: int = 0) -> str:
    """Aggregates and family names only; a family name is a construct, never a task."""
    ceiling = result["macro_delta_ceiling"]
    lines = ["first-draft mix (labels only; nothing executed, nothing spent)",
             "rows %d   labelled %d   unlabelled %d   families %d"
             % (result["rows"], result["labelled"], result["unlabelled"], result["families"])]
    if ceiling is None:
        lines.append("macro delta ceiling: UNREADABLE - %d rows carry no synth.kind, so the "
                     "base-arm assumption this estimate rests on has nothing to rest on"
                     % result["unlabelled"])
        return "\n".join(lines)
    lines.extend([
        "macro refused-draft fraction %.4f   macro delta CEILING %.4f   bar %.4f  -> %s"
        % (result["macro_refused_draft"], ceiling, mde,
           "room to try" if ceiling > mde else "CANNOT MEET THE BAR"),
        "", "%-40s %6s %8s %9s %8s %9s" % ("family", "rows", "native", "repaired", "ceiling", "movable")])
    shown = result["by_family"][:limit] if limit else result["by_family"]
    for f in shown:
        lines.append("%-40s %6d %8d %9d %8d %9s"
                     % (f["family"][:40], f["rows"], f["native"], f["repaired"], f["ceiling"],
                        "-" if f["movable_fraction"] is None else "%.4f" % f["movable_fraction"]))
    if limit and len(result["by_family"]) > limit:
        lines.append("... %d more families" % (len(result["by_family"]) - limit))
    return "\n".join(lines)
