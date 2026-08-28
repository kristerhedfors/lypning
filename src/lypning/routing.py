"""Routing safety — a different question from conformance, with a different gate.

Conformance asks whether an engine that *ran* a program agreed with CPython.
This module asks whether the classifier **sent** the program to an engine that
could run it, by comparing where each program went against where it should
ideally have gone. The two questions come apart: every arm can be at MISMATCH 0
while the classifier still sends half the corpus one tier too high, and a
classifier can be perfectly accurate on a tier that disagrees with CPython.

The vocabulary is deliberately asymmetric, and the asymmetry is the design:

  ``UNSAFE``     the tier that ANSWERED mismatched. **Fatal.** The whole point
                 of a three-tier mixture is that a wrong route costs a process
                 spawn and never a wrong answer; an UNSAFE route is the one
                 outcome that spends the user's trust instead of their
                 milliseconds. Must be zero, always.

                 "The tier that answered" and not "the tier that was named":
                 a refusal falls through, so the named tier can refuse
                 correctly and the tier *below* it answer wrongly. Grading the
                 named one reads that as a spare spawn. See :func:`_delivered`.
  ``WASTED``     routed to an engine that refuses, when a cheaper engine would
                 have run it — *and* the tier that then answered was right.
                 Costs one spawn (~1 ms). A quality number.
  ``LATE``       routed to a more expensive engine than necessary. Costs the
                 difference. A quality number.
  ``IDEAL``      routed to the cheapest engine that matches.
  ``NO-ENGINE``  no engine matched at all. Not the classifier's fault, so it is
                 counted apart rather than blamed on the route — folding it into
                 the failures would make an engine's coverage gap look like a
                 routing bug.

So UNSAFE is a gate and the other two are a budget. Reducing LATE means
implementing something in a cheaper tier; reducing UNSAFE means the classifier
is claiming a capability the engine does not have, which is the same defect as a
capability table edited to describe what someone wished the engine did.

The grade needs a *measured* answer from every arm, so it is derived from a
:class:`lypning.conformance.Report` rather than measured here: the battery
already ran each program on each tier and recorded which engine the dispatcher
was told to start from. This module is pure arithmetic over that record, which
is why :func:`score_route` can be tested without a single engine built.

Direction of the dependency is one way on purpose — this module imports
:mod:`lypning.conformance` and that module knows nothing about this one, so the
battery keeps working with no routing report and :mod:`lypning.cli` composes the
two.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import conformance as conf
from . import engines as eng
from . import paths

IDEAL = "IDEAL"
WASTED = "WASTED"
LATE = "LATE"
UNSAFE = "UNSAFE"
NO_ENGINE = "NO-ENGINE"

#: Report order, worst first — a reader looking for the gate should not have to
#: scan past two quality numbers to find it.
GRADES = (IDEAL, WASTED, LATE, UNSAFE, NO_ENGINE)

#: Route kinds that are not a classifier decision. ``unbuilt`` means there was
#: no classifier to ask, so every program "routed" to CPython by default;
#: grading those would report a 100% LATE corpus as if something had chosen it.
NOT_A_DECISION = ("unbuilt",)


# --- the scoring rule --------------------------------------------------------


@dataclass
class RouteScore:
    """One program's route, graded against where it could have gone."""

    entry_id: str
    predicted: str
    ideal: str
    grade: str
    detail: str = ""
    rescued: bool = False

    @property
    def fatal(self) -> bool:
        return self.grade == UNSAFE

    def __str__(self) -> str:
        why = " — %s" % self.detail if self.detail else ""
        return "%-9s %s: predicted %s, ideal %s%s" % (
            self.grade, self.entry_id, self.predicted, self.ideal or "none", why)


def _verdict_of(x: Any) -> str:
    """The verdict string out of whatever the caller had.

    A :class:`conformance.Verdict` in the battery's own path, a bare string in a
    test that wants to pin one rule without building three interpreters. Both
    are accepted because the rule is worth testing on its own — it is the piece
    that decides whether a number is a gate or a budget.
    """
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    return getattr(x, "verdict", "") or ""


def _why_of(x: Any) -> str:
    if x is None or isinstance(x, str):
        return ""
    kind = getattr(x, "kind", "") or ""
    detail = getattr(x, "detail", "") or ""
    return ("%s: %s" % (kind, detail)).strip(": ")


def _matched_by_failing(x: Any) -> bool:
    """A MATCH that is two interpreters failing alike, rather than an answer.

    The battery compares stdout and the exit code. A program that does not parse
    has an empty stdout and a non-zero exit on *every* interpreter, so each one
    scores MATCH for producing nothing — and the cheapest of them would be named
    the ideal destination for a program none of them can run.

    A bare verdict string carries no exit code and is never treated as one of
    these, which keeps :func:`score_route` callable with plain strings.
    """
    if x is None or isinstance(x, str):
        return False
    return bool(getattr(x, "actual_rc", 0))


def _delivered(predicted: str, by_engine: Mapping[str, Any],
               ladder: Sequence[str]) -> tuple:
    """The tier whose answer the user actually receives, and its verdict.

    A refusal is not the end of the program: :func:`engines.dispatch` falls
    through to the next tier down and that tier's answer is what gets printed.
    So the tier the classifier *named* is not necessarily the tier that
    *answered*, and grading the named one is grading a process that did not
    produce the output.

    That distinction was invisible for as long as the fall-through was assumed
    safe. It is not: 25 corpus programs are refused by tier 1 — correctly, on
    constructs it knows it cannot match CPython on — and then answered *wrongly*
    by the tier below, at exit 0. Every one of them was graded WASTED, whose
    definition ends "and the chain still produces the right answer".

    Engines missing from ``by_engine`` are skipped rather than treated as an
    answer, which mirrors the dispatcher skipping a tier that is not built.
    """
    if predicted not in ladder:
        return predicted, _verdict_of(by_engine.get(predicted))
    remaining = list(ladder[ladder.index(predicted):])
    while remaining:
        name, remaining = remaining[0], remaining[1:]
        v = by_engine.get(name)
        if v is None:
            continue
        if _verdict_of(v) != conf.UNSUPPORTED:
            return name, _verdict_of(v)
        # A refusal names its reason, and some reasons rule out every tier but
        # CPython. Read the same table the dispatcher reads, or the grade
        # describes a chain nothing walks.
        allowed = eng.chain_after_refusal(name, getattr(v, "kind", "") or "")
        remaining = [e for e in remaining if e in allowed]
    return predicted, conf.UNSUPPORTED


def score_route(
    predicted: str,
    by_engine: Mapping[str, Any],
    order: Optional[Sequence[str]] = None,
    *,
    entry_id: str = "",
    rescued: bool = False,
    route_kind: str = "",
) -> RouteScore:
    """Grade one route, given every engine's measured verdict for that program.

    ``order`` is the engine ladder cheapest first, and the *ideal* engine is the
    first one on it that MATCHED. It must not contain the mixture arm: the
    mixture answers everything by falling through, so calling it the ideal
    destination would say the cheapest correct route for every program is the
    dispatcher itself.

    A predicted engine with no result at all is UNSAFE rather than unscored. The
    classifier named a destination the battery could not measure, and the honest
    reading of "I cannot tell whether this route is safe" is not "safe".
    """
    ladder = list(order) if order is not None else [e for e in eng.ENGINE_ORDER if e in by_engine]
    ideal = ""
    for name in ladder:
        v = by_engine.get(name)
        if _verdict_of(v) != conf.MATCH:
            continue
        if route_kind == "syntax" and name != conf.CPYTHON and _matched_by_failing(v):
            # A syntax error is not a capability gap, and the classifier sends it
            # to CPython on purpose: CPython's message names the file, the line
            # and the column and prints the offending source, where lypning's
            # says "line 1". That difference lives entirely on **stderr**, which
            # the battery does not compare — so a cheaper tier scores MATCH for
            # failing the same way and would be graded the ideal destination for
            # a program it cannot run. Nineteen corpus programs read LATE for
            # this reason alone, which is a quarter of the LATE budget spent on
            # nothing anyone can fix.
            #
            # Guarded on the exit code rather than on the route kind alone, so
            # the case actually worth catching stays visible: if the cheaper tier
            # RAN the program — exit 0, real output — while the classifier called
            # it a syntax error, that is a misclassification and a real defect,
            # and it still grades LATE.
            continue
        ideal = name
        break
    if not ideal:
        return RouteScore(entry_id, predicted, "", NO_ENGINE,
                          "no engine matched CPython", rescued)
    got = by_engine.get(predicted)
    if got is None:
        return RouteScore(entry_id, predicted, ideal, UNSAFE,
                          "no result for predicted engine %s" % (predicted or "(none)"), rescued)
    verdict = _verdict_of(got)
    if verdict == conf.MISMATCH:
        return RouteScore(entry_id, predicted, ideal, UNSAFE, _why_of(got), rescued)
    if predicted == ideal:
        return RouteScore(entry_id, predicted, ideal, IDEAL, "", rescued)
    if verdict == conf.UNSUPPORTED:
        # A refusal is not an outcome — the dispatcher falls through, and the
        # NEXT tier's answer is the one the user sees. Grade that one. Skipping
        # this step is how a correct refusal followed by a wrong answer came to
        # be counted as a spawn: WASTED, whose definition ends "and the chain
        # still produces the right answer", against 25 measured programs where
        # it does not.
        answered, delivered = _delivered(predicted, by_engine, ladder)
        if delivered == conf.MISMATCH:
            return RouteScore(entry_id, predicted, ideal, UNSAFE,
                              "%s refused, %s answered wrongly: %s"
                              % (predicted, answered,
                                 _why_of(by_engine.get(answered))), rescued)
        # Refused where a cheaper tier would have answered, and the tier that
        # did answer was right: one spawn.
        return RouteScore(entry_id, predicted, ideal, WASTED, _why_of(got), rescued)
    return RouteScore(entry_id, predicted, ideal, LATE, "", rescued)


# --- the graded report -------------------------------------------------------


@dataclass
class RoutingReport:
    """Routing safety over a whole battery run."""

    counts: Dict[str, int] = field(default_factory=dict)
    predictions: Dict[str, int] = field(default_factory=dict)
    scores: List[RouteScore] = field(default_factory=list)
    graded: int = 0
    #: ``{reason: count}`` for the routes that could not be graded at all — no
    #: classifier to ask, or a route to a tier this run did not measure. A
    #: reason and a count rather than a bare number, because the two degrade for
    #: opposite causes and only one of them is fixed by building something.
    ungraded: Dict[str, int] = field(default_factory=dict)

    @property
    def ungraded_total(self) -> int:
        return sum(self.ungraded.values())

    @property
    def note(self) -> str:
        if self.graded:
            return ""
        if not self.ungraded:
            return "no program carried a route: the mixture arm did not run"
        return "; ".join("%s (%d)" % (why, n) for why, n in sorted(self.ungraded.items()))

    @property
    def measured(self) -> bool:
        return self.graded > 0

    @property
    def scored(self) -> int:
        """Programs where routing *could* be graded — at least one engine matched."""
        return self.graded - self.counts.get(NO_ENGINE, 0)

    @property
    def ok(self) -> bool:
        """The gate, and only the gate. WASTED and LATE are budget."""
        return self.counts.get(UNSAFE, 0) == 0

    def unsafe(self) -> List[RouteScore]:
        return [s for s in self.scores if s.grade == UNSAFE]

    def _pct(self, n: int) -> float:
        return (100.0 * n / self.scored) if self.scored else 0.0

    @property
    def ideal_pct(self) -> float:
        return self._pct(self.counts.get(IDEAL, 0))

    @property
    def first_try_pct(self) -> float:
        """Routed somewhere that answered correctly on the first spawn — IDEAL
        plus LATE. The chain recovers from the rest at a cost in milliseconds."""
        return self._pct(self.counts.get(IDEAL, 0) + self.counts.get(LATE, 0))


def grade(report: conf.Report) -> RoutingReport:
    """Grade every route the battery recorded. Never raises; never runs anything.

    Programs the battery skipped are not here at all — a program that was never
    run has no verdicts to be right or wrong about. Programs routed with no
    classifier built are counted as ``ungraded`` and named in ``note``, because
    "0 UNSAFE over 0 graded programs" and "0 UNSAFE over the whole corpus" are
    the same number and very different facts.
    """
    # CPython is on the ladder whether or not it was an arm. The battery does not
    # measure it by default — an arm that is the reference would trivially match
    # itself — but the classifier can and does route to it, and a destination the
    # grader has no verdict for scores UNSAFE. That would report every route to
    # the top tier as a wrong answer, which is the exact inverse of the truth:
    # CPython's answer is the definition of right. So the reference is injected,
    # as MATCH, at its own place on the ladder.
    ladder = [e for e in eng.ENGINE_ORDER if e in report.engines or e == conf.CPYTHON]
    by_entry: Dict[str, Dict[str, Any]] = {}
    for name, er in report.engines.items():
        for v in er.verdicts:
            by_entry.setdefault(v.entry_id, {})[name] = v

    out = RoutingReport(counts={g: 0 for g in GRADES})

    def ungraded(why: str) -> None:
        out.ungraded[why] = out.ungraded.get(why, 0) + 1

    for entry_id, route in report.routes.items():
        if route.kind in NOT_A_DECISION:
            ungraded("no classifier built")
            continue
        verdicts = dict(by_entry.get(entry_id, {}))
        verdicts.setdefault(conf.CPYTHON, conf.MATCH)
        if route.engine in eng.ENGINE_ORDER and route.engine not in verdicts:
            # A tier that exists but was not one of this run's arms — most often
            # lypning-mp, which needs a 32-bit toolchain and a network and is
            # absent almost everywhere. There is no measured answer to grade the
            # route against, and inventing one in either direction would be a
            # lie: calling it UNSAFE would report a machine's build state as a
            # classifier bug, calling it IDEAL would report a tier nobody ran as
            # a tier that worked. It is a hole in the table, never a zero.
            ungraded("%s was not measured in this run" % route.engine)
            continue
        mixture = verdicts.get(conf.MIXTURE)
        s = score_route(
            route.engine, verdicts, ladder, entry_id=entry_id,
            rescued=_verdict_of(mixture) == conf.MATCH,
            route_kind=route.kind,
        )
        out.scores.append(s)
        out.counts[s.grade] = out.counts.get(s.grade, 0) + 1
        out.predictions[route.engine] = out.predictions.get(route.engine, 0) + 1
        out.graded += 1
    return out


# --- the capability table ----------------------------------------------------

_TABLE_RE = re.compile(
    r"const\s+MICROPYTHON_MODULES\s*:\s*&\[&str\]\s*=\s*&\[(?P<body>.*?)\];",
    re.S,
)
_STRING_RE = re.compile(r'"([^"]*)"')


def table_source() -> Path:
    return paths.RUST_DIR / "src" / "route.rs"


def micropython_modules(source: Optional[Path] = None) -> List[str]:
    """The modules ``route.rs`` claims lypning-mp serves, read from the source.

    Read rather than restated. lypning-mp is a separate binary that cannot be
    asked what it imports, so the classifier carries a table — and a table is
    only ever as honest as the thing that checks it. A copy of it kept here
    would be checked against itself.

    An empty list means the table could not be found, which is what happens if
    someone renames it; the caller decides whether that is a skip or a failure,
    and ``tests/test_routing.py`` decides it is a failure.
    """
    p = Path(source) if source is not None else table_source()
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    m = _TABLE_RE.search(text)
    if not m:
        return []
    return _STRING_RE.findall(m.group("body"))


#: The `match kind { … => Engine::MicroPython }` arm of `engine_for`, which is
#: the classifier's other table: not "which modules that tier has" but "which of
#: lypning's refusal KINDS that tier can pick up". Read from the source for the
#: same reason as the module list — a copy kept here would be checked against
#: itself.
_KIND_ARM_RE = re.compile(
    r"match kind \{(?P<body>.*?)=> Engine::MicroPython", re.S)


def micropython_kinds(source: Optional[Path] = None) -> List[str]:
    """The refusal kinds ``route.rs`` sends to lypning-mp, read from the source.

    Empty when the arm cannot be found, which is what happens if someone
    restructures ``engine_for``; the caller decides whether that is a skip.
    """
    p = Path(source) if source is not None else table_source()
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    m = _KIND_ARM_RE.search(text)
    if not m:
        return []
    return _STRING_RE.findall(m.group("body"))


# --- reporting ---------------------------------------------------------------


def render(rp: RoutingReport) -> str:
    """The human view. The only function here that formats for a terminal."""
    out: List[str] = []
    out.append("routing safety over %d programs (%d have an engine that matches)"
               % (rp.graded, rp.scored))
    out.append("")
    if not rp.measured:
        out.append("  %s" % (rp.note or "nothing to grade"))
        return "\n".join(out) + "\n"
    rows = (
        (IDEAL, "routed to the cheapest engine that works"),
        (WASTED, "engine refused; one extra spawn, right answer"),
        (LATE, "worked, but a cheaper engine would have too"),
        (UNSAFE, "routed to an engine that MISMATCHES  <-- must be 0"),
        (NO_ENGINE, "no engine matched; not the router's fault"),
    )
    for name, why in rows:
        out.append("  %-10s %s  %s" % (name, str(rp.counts.get(name, 0)).rjust(4), why))
    out.append("")
    out.append("  accuracy %.1f%% ideal, %.1f%% correct-on-first-try"
               % (rp.ideal_pct, rp.first_try_pct))
    if rp.predictions:
        out.append("  predictions: %s" % "  ".join(
            "%s=%d" % (k, v) for k, v in sorted(rp.predictions.items(),
                                                key=lambda kv: (-kv[1], kv[0]))))
    for why, n in sorted(rp.ungraded.items(), key=lambda kv: (-kv[1], kv[0])):
        out.append("  %-10s %s  %s" % ("not graded", str(n).rjust(4), why))
    for s in rp.unsafe()[:20]:
        # Named individually, always. UNSAFE is a bug report, and a bug report
        # without the program is a number nobody can act on.
        out.append("  UNSAFE %s: predicted %s, ideal %s — %s%s"
                   % (s.entry_id, s.predicted, s.ideal or "none", s.detail,
                      " (dispatcher recovered)" if s.rescued else ""))
    return "\n".join(out) + "\n"
