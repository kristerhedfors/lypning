"""Subset-legality: what fraction of what a model writes the engine will simply run.

WHY THIS EXISTS, BESIDE `pass@1`
    The pre-registered endpoint (PREREGISTRATION.md §3) is a *correctness* pass
    rate on a `lypning`-kind test: the program must reproduce CPython's output
    **and** run on tier 1. That number moves for two reasons at once, and the
    second leg of the rule — exact McNemar on solved/not-solved — asks an
    ACQUISITION question: did the model learn to solve problems it could not
    solve before? This project's question is not acquisition. It is a prior
    shift: *given that the model can already write a program for this task, how
    often does it write one the engine will run?* Nothing has to become newly
    solvable for that to improve, so a rule that required the acquisition leg to
    fire could only ever report the prior shift as a failure.

    Subset-Legality Rate is the endpoint that matches the goal:

        SLR = programs the pinned engine runs without refusing
              ÷ programs generated

    Four choices, each deliberate. **First draft only** — no retries, no repair
    loop, no refusal line fed back, because a retry loop measures the harness and
    not the prior. **No correctness gate inside the number** — a program can be
    legal and wrong, and keeping correctness out is exactly what makes SLR
    isolate the thing being moved. **The engine is part of the number** — SLR is
    defined relative to what this binary accepts today, so it is quoted with its
    fingerprint or it is not quoted. **The denominator is programs** — which is
    what buys back the power the case-level flip test did not have.

WHY IT IS NOT THE ONLY NUMBER
    SLR has degenerate maxima and every one of them is worse than the status
    quo. Emit nothing: legal. Emit a hard-coded literal: legal, sometimes even
    correct. Never import anything ever: legal, and quietly worse at the job. So
    SLR is reported only alongside three gates, and a gate that fails voids the
    result rather than discounting it:

        A  correctness non-regression   blocks empty and degenerate programs
        B  supported-import retention   blocks the "never import" collapse
        C  length non-inflation         blocks paying for it in output tokens

    Gate B's module list is asked of the ENGINE, never written down here. A
    hand-maintained table of what the subset serves is invariant 1's failure
    mode in a different file: it describes what you wish the engine did, and it
    goes stale in the direction that flatters the result.

THE CLUSTERING, WHICH IS NOT OPTIONAL
    The k draws for one task are correlated — same prompt, same task difficulty,
    one sample of the model's behaviour on it. Bootstrapping over *programs*
    treats them as k independent observations and returns an interval that is
    far too narrow; at k=16 it would claim significance for effects that are one
    task changing its mind. So the per-case rate is the unit and
    :func:`stats.paired_delta` resamples cases, which is the cluster bootstrap
    this endpoint needs and the same one the correctness leg already uses.

WHAT THIS MODULE DOES NOT DO
    It does not generate. Every number here comes out of programs a run already
    recorded, so a comparison costs CPU and nothing else — which is the point:
    the endpoint can be re-cut over runs that were already paid for.
"""

from __future__ import annotations

import re as _re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import engines as eng
from . import refusals, stats

# Gate defaults. Pre-register them before a run; they are arguments, not beliefs.
GATE_A_MAX_DROP = 0.02    # correctness may not fall more than 2pp
GATE_B_MIN_RETENTION = 0.80   # of the base arm's supported-import rate
GATE_C_MAX_GROWTH = 0.20  # mean completion tokens may not grow more than 20%

_IMPORT = _re.compile(r"^\s*(?:import\s+(?P<plain>[\w.]+(?:\s*,\s*[\w.]+)*)"
                      r"|from\s+(?P<frm>[\w.]+)\s+import)", _re.M)


def modules_served(engine: str, names: Sequence[str]) -> Dict[str, bool]:
    """Ask the ENGINE which of these modules it will import. Never a table here."""
    out: Dict[str, bool] = {}
    for name in sorted(set(names)):
        probe = refusals.probe("import %s" % name, engine)
        out[name] = probe is None
    return out


def imports_in(program: str) -> "set[str]":
    """Top-level module names a program imports, as written."""
    found = set()
    for m in _IMPORT.finditer(program or ""):
        raw = m.group("plain") or m.group("frm") or ""
        for name in raw.split(","):
            name = name.strip()
            if name:
                found.add(name.split(".")[0])
    return found


def replay(attempts: List[Dict[str, Any]], engine: str,
           tests: Optional[Dict[str, Dict[str, Any]]] = None,
           workers: int = 1, cache: Optional[Any] = None) -> Dict[str, Any]:
    """The on-policy census for these programs, from cache when the engine matches.

    One place, because the cache's fingerprint check is the whole safety of it:
    every caller that replays programs has to be unable to reuse a replay taken
    under a different binary — or under a different GRADER, which the fingerprint
    cannot see — and the only way to make that true is to leave them no second
    door.
    """
    from .jsonio import sha256_of, write_json
    # A census belongs to these exact programs AND their execution contexts,
    # not merely to whichever binaries happen to be installed on this host.
    key = sha256_of({"identity": eng.identity(), "engine": str(engine),
                     "binary_sha256": eng._sha256_of_file(str(engine)),
                     "grader": refusals.GRADER, "attempts": attempts, "tests": tests})
    if cache is not None and cache.exists():
        import json as _json
        stored = _json.loads(cache.read_text(encoding="utf-8"))
        # A cached replay is only a replay of the SAME engine. Reusing one across
        # a rebuild is the confound this endpoint is most exposed to, so the
        # fingerprint is checked rather than trusted.
        if stored.get("cache_key") == key:
            return stored["census"]
    census = refusals.on_policy(attempts, engine, tests=tests, workers=workers)
    if cache is not None:
        import json as _json
        cache.parent.mkdir(parents=True, exist_ok=True)
        write_json(cache,
            {"fingerprint": eng.identity()["fingerprint"], "engine": engine,
             "grader": refusals.GRADER, "cache_key": key, "census": census})
    return census


def arm(attempts: List[Dict[str, Any]], engine: str,
        tests: Optional[Dict[str, Dict[str, Any]]] = None,
        workers: int = 1, cache: Optional[Any] = None) -> Dict[str, Any]:
    """Replay one run's programs through ``engine`` and reduce to per-case rates.

    ``passed`` comes from the attempt as graded; legality is measured here and
    now, because the recorded ``reason`` cannot supply it. ``reason == "refused"``
    is only reachable for a program that ALREADY passed correctness
    (acceptance._run_lypning grades correctness first and decisively), so the
    recorded refusal count is *correct-and-refused* and systematically
    undercounts what the engine declined to run.
    """
    census = replay(attempts, engine, tests=tests, workers=workers, cache=cache)
    by_case: Dict[str, List[Dict[str, Any]]] = {}
    for row in census["rows"]:
        by_case.setdefault(row["case_id"], []).append(row)

    tokens: Dict[str, List[int]] = {}
    passed: Dict[str, List[bool]] = {}
    programs: Dict[str, List[str]] = {}
    for a in attempts:
        if not a.get("program"):
            continue
        cid = a.get("case_id")
        tokens.setdefault(cid, []).append(int(a.get("completion_tokens") or 0))
        passed.setdefault(cid, []).append(bool(a.get("passed")))
        programs.setdefault(cid, []).append(a["program"])

    # A harness error is not a verdict about the program, so it leaves the
    # denominator rather than counting as legal — the same exclusion the
    # correctness endpoint already makes. A case whose every draw errored has no
    # rate at all and drops out of the pairing, which is what `paired_delta`'s
    # shared-key intersection is for.
    graded = {c: [r for r in rows if r["verdict"] != "ERROR"] for c, rows in by_case.items()}
    graded = {c: rows for c, rows in graded.items() if rows}
    legal_rate = {c: sum(1 for r in rows if r["verdict"] != "UNSUPPORTED") / len(rows)
                  for c, rows in graded.items()}
    pass_rate = {c: sum(1 for p in v if p) / len(v) for c, v in passed.items()}
    tok_mean = {c: sum(v) / len(v) for c, v in tokens.items() if v}
    n_graded = sum(len(v) for v in graded.values()) or 1
    degenerate = sum(1 for a in attempts if a.get("failure_category") == "not-genuine")
    return {
        "not_genuine": degenerate,
        "engine": engine,
        "fingerprint": eng.identity()["fingerprint"],
        "programs": census["programs"],
        "cases": len(by_case),
        "graded": sum(len(v) for v in graded.values()),
        "slr": sum(1 for rows in graded.values()
                   for r in rows if r["verdict"] != "UNSUPPORTED") / n_graded,
        "mismatch": census["tally"].get("MISMATCH", 0),
        "error": census["tally"].get("ERROR", 0),
        "by_kind": dict(census["details"]),
        "blockers": dict(census["blockers"]),
        "legal_rate": legal_rate,
        "pass_rate": pass_rate,
        "tokens": tok_mean,
        "programs_by_case": programs,
        "rows": census["rows"],
    }


# --- reachability: what the model can already write, given enough tries -------

#: The k values a reachability ladder is cut at. 8 is not decoration: it is the
#: rollout count a GRPO step is planned at, so pass@8 — not pass@16 — is the
#: ceiling that on-policy training actually stands on.
LADDER = (1, 2, 4, 8, 16)

#: Below this, a set of cases is not a training set for anything reward-based.
REACHABLE_FLOOR = 0.60


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k for ``c`` successes in ``n`` draws (Chen et al., 2021).

    Not ``c > 0``: that is pass@n reported under a smaller k's name. The
    estimator answers what a *k*-draw budget would have got from the same
    distribution, which is the question a rollout count asks.
    """
    if k > n or n <= 0:
        return float("nan")
    if n - c < k:
        return 1.0
    prod = 1.0
    for i in range(k):
        prod *= (n - c - i) / (n - i)
    return 1.0 - prod


#: What a case's own test says about tier 1, which is three answers and not two.
TIER_REQUIRED = "required"   # a rewrite case: the engine must run the program
TIER_FALLBACK = "fallback"   # a ceiling case: the engine refusing IS the answer
TIER_FREE = "free"           # a plain task: the test never mentions the engine


def tier_of(test: Optional[Dict[str, Any]]) -> str:
    """Which of the three a case is, read off its test and never off its name.

    The third one is easy to lose and expensive to lose. A `stdout`-kind case —
    this repository's own task bank — carries no tier clause at all: the model is
    asked for a program, nothing asks it to stay in the subset, and legality
    there is a free choice rather than a score. Folding those into the required
    population raises the headline by the mix (they are the easy ones), and
    folding them into the fallback population claims the engine refusing is
    right, which nothing said. They are also the only prompts in this corpus
    shaped like the deployment question, so they are worth their own line.
    """
    if not test:
        return TIER_FREE
    if "require_tier1" not in test:
        return TIER_FREE
    return TIER_REQUIRED if test["require_tier1"] else TIER_FALLBACK


def reachability(attempts: List[Dict[str, Any]], engine: str, *,
                 tests: Optional[Dict[str, Dict[str, Any]]] = None,
                 kinds: Optional[Dict[str, str]] = None,
                 workers: int = 1, cache: Optional[Any] = None,
                 ladder: Sequence[int] = LADDER) -> Dict[str, Any]:
    """Per case: did ANY draw land in the subset — and did any land there correct.

    THE CEILING ON EVERYTHING REWARD-BASED. GRPO reinforces what the policy
    already produces; a case whose every draw the engine refuses hands it a
    group of identically-scored rollouts, an advantage of zero, and no gradient.
    So the fraction of cases with at least one legal draw is not a diagnostic
    beside the training plan, it is the bound on what the training plan can
    reach — and the by-kind cut says *which* part of the subset is out of reach,
    which a scalar cannot.

    Two columns, and the second is the strict one. **Reachable** is legality
    alone: some draw ran on tier 1. **Rewardable** is legal AND reproducing the
    case's expected stdout, which is the only outcome the reward table pays +1.0
    for. Reachable-but-never-correct is a case where every positive signal
    available is the +0.5 consolation row, and a reward curve that climbs on
    those is climbing toward legal-and-wrong.

    THE POPULATION IS SPLIT, AND THAT IS NOT A PRESENTATION CHOICE. A ceiling
    case's own test carries ``require_tier1: False`` because falling back IS the
    right answer there — the reference solution is the program the engine
    refuses. Averaging those into one reachability rate scores the model for
    failing to do the wrong thing, and it moves the headline by the mix of the
    corpus rather than by the model. So the floor is read off the cases that
    demand tier 1, and the rest are reported beside it, unaveraged, where a legal
    draw is an option the model took and not a box it ticked.

    Both columns are measured HERE, through the engine passed in, from the
    engine's own run of the program. Nothing is read out of the run's recorded
    verdict: that was graded under whatever binary that run pinned, and one
    number mixing two engines is the confound this whole module refuses.
    """
    census = replay(attempts, engine, tests=tests, workers=workers, cache=cache)
    by_case: Dict[str, List[Dict[str, Any]]] = {}
    for row in census["rows"]:
        by_case.setdefault(row["case_id"], []).append(row)

    cases: Dict[str, Dict[str, Any]] = {}
    for cid, rows in by_case.items():
        # A harness error is not a verdict about the program; it leaves the
        # denominator rather than counting as a failure to reach.
        graded = [r for r in rows if r["verdict"] != "ERROR"]
        if not graded:
            continue
        legal = [r for r in graded if r["verdict"] != "UNSUPPORTED"]
        # Scorable is a property of the CASE, not of its draws. Reading it off
        # "did any draw come back with a correct flag" would file a case whose
        # every draw was refused as *unmeasured* — the one reading that turns the
        # worst cases in the table into blanks.
        if tests is not None:
            measurable = (tests.get(cid) or {}).get("expect_stdout") is not None
        else:
            measurable = any(r.get("correct") is not None for r in graded)
        good = [r for r in legal if r.get("correct") is True]
        cases[cid] = {
            "kind": (kinds or {}).get(cid, "unknown"),
            "tier": tier_of((tests or {}).get(cid)),
            "draws": len(graded), "errors": len(rows) - len(graded),
            "legal": len(legal), "rewardable": len(good),
            "measurable": measurable,
            "reached": bool(legal), "rewarded": bool(good),
            "refusals": sorted({r["detail"] for r in graded
                                if r["verdict"] == "UNSUPPORTED"}),
        }

    def _roll(rows: Sequence[Dict[str, Any]], key: str, k: int) -> float:
        vals = [pass_at_k(r["draws"], r[key], k) for r in rows]
        vals = [v for v in vals if v == v]
        return sum(vals) / len(vals) if vals else float("nan")

    rows = [r for r in cases.values() if r["tier"] == TIER_REQUIRED]
    fallback = [r for r in cases.values() if r["tier"] == TIER_FALLBACK]
    free = [r for r in cases.values() if r["tier"] == TIER_FREE]
    scored = [r for r in rows if r["measurable"]]
    kind_rows: Dict[str, List[Dict[str, Any]]] = {}
    for r in cases.values():
        kind_rows.setdefault(r["kind"], []).append(r)
    closed = refusals.closed_kinds()
    by_kind = []
    for kind, krows in sorted(kind_rows.items()):
        kscored = [r for r in krows if r["measurable"]]
        by_kind.append({
            "kind": kind,
            # `refused:set-order` is a kind the engine has DECLARED it will never
            # answer, so its cases are not headroom and a plan that ranked them
            # would be ranking work nobody may do.
            "closed": kind.split(":", 1)[-1] in closed,
            "cases": len(krows),
            "tier": (krows[0]["tier"] if len({r["tier"] for r in krows}) == 1
                     else "mixed"),
            "reached": sum(1 for r in krows if r["reached"]),
            "rewarded": sum(1 for r in kscored if r["rewarded"]),
            "scored": len(kscored),
            "legal_draws": sum(r["legal"] for r in krows),
            "draws": sum(r["draws"] for r in krows),
        })
    by_kind.sort(key=lambda r: (r["closed"], r["reached"] / (r["cases"] or 1), -r["cases"]))

    allrows = list(cases.values())
    ks = [k for k in ladder if k <= max([r["draws"] for r in rows] or [0])]
    return {
        "engine": engine, "fingerprint": eng.identity()["fingerprint"],
        "cases": len(rows),
        "fallback": {"cases": len(fallback),
                     "reached": sum(1 for r in fallback if r["reached"]),
                     "rewarded": sum(1 for r in fallback if r["rewarded"])},
        "free": {"cases": len(free),
                 "reached": sum(1 for r in free if r["reached"]),
                 "rewarded": sum(1 for r in free if r["rewarded"]),
                 "legal_draws": sum(r["legal"] for r in free),
                 "draws": sum(r["draws"] for r in free)},
        "draws": sum(r["draws"] for r in allrows),
        "errors": sum(r["errors"] for r in allrows),
        "k": min([r["draws"] for r in allrows] or [0]),
        "k_max": max([r["draws"] for r in allrows] or [0]),
        "reached": sum(1 for r in rows if r["reached"]),
        "rewarded": sum(1 for r in scored if r["rewarded"]),
        "scored": len(scored),
        "reachable": (sum(1 for r in rows if r["reached"]) / len(rows)) if rows else float("nan"),
        "rewardable": (sum(1 for r in scored if r["rewarded"]) / len(scored))
                      if scored else float("nan"),
        "ladder": [{"k": k, "legal": _roll(rows, "legal", k),
                    "rewardable": _roll(scored, "rewardable", k)} for k in ks],
        "mismatch": census["tally"].get("MISMATCH", 0),
        "unstable": census["tally"].get("UNSTABLE", 0),
        "by_kind": by_kind, "by_case": cases,
        "unreachable": sorted(cid for cid, r in cases.items()
                              if r["tier"] == TIER_REQUIRED and not r["reached"]),
    }


_TIER_MARK = {TIER_FALLBACK: " -", TIER_FREE: " ~"}


def reachability_report(r: Dict[str, Any], *, label: str, limit: int = 20,
                        floor: float = REACHABLE_FLOOR,
                        held_out: bool = False) -> str:
    """Render a reachability table. The floor verdict is a stop, not a footnote."""
    out: List[str] = []
    out.append("reachability: %s   @ engine %s" % (label, r["fingerprint"]))
    k = r["k"] if r["k"] == r["k_max"] else r["k_max"]
    out.append("  %d cases require tier 1, %d are ceiling cases where falling back is "
               "the answer," % (r["cases"], r["fallback"]["cases"]))
    out.append("  %d ask for a program and never mention the engine"
               % r["free"]["cases"])
    out.append("  %d draws graded%s, k=%d%s"
               % (r["draws"],
                  (" (%d harness errors dropped)" % r["errors"]) if r["errors"] else "",
                  k, "" if r["k"] == r["k_max"] else " at most (%d at least)" % r["k"]))
    if r.get("unstable"):
        out.append("  %d of them are not reproducible under the harness — a fresh temp "
                   "cwd, the clock," % r["unstable"])
        out.append("  the hash seed. Legal (the engine ran them), never correct "
                   "(nothing is), never a MISMATCH.")
    out.append("")
    out.append("  over the cases that REQUIRE tier 1 — the population the floor reads")
    out.append("    reachable   %s have >=1 LEGAL draw              (%d/%d)"
               % (_pct(r["reachable"]), r["reached"], r["cases"]))
    if r["scored"]:
        out.append("    rewardable  %s have >=1 legal AND correct draw  (%d/%d)"
                   % (_pct(r["rewardable"]), r["rewarded"], r["scored"]))
    else:
        out.append("    rewardable  not measured: no case here carries an expected stdout")
    if r["fallback"]["cases"]:
        out.append("")
        out.append("  over the %d CEILING cases, where the reference solution is the "
                   "program the" % r["fallback"]["cases"])
        out.append("  engine refuses: %d reached the subset anyway, %d of those correct."
                   % (r["fallback"]["reached"], r["fallback"]["rewarded"]))
        out.append("    Not a target. A reward that pays for legality here pays the "
                   "model for")
        out.append("    working around the right answer, and these rows are where that "
                   "would show.")
    if r["free"]["cases"]:
        fr = r["free"]
        out.append("")
        out.append("  over the %d cases that just ask for a program — no rewrite "
                   "instruction," % fr["cases"])
        out.append("  no mention of a runtime. The only prompts here shaped like the "
                   "deployment")
        out.append("  question, and a preview of what eval-2 will ask at scale:")
        out.append("    %d of %d reached the subset unprompted, %s of draws legal"
                   % (fr["reached"], fr["cases"],
                      _pct(fr["legal_draws"] / fr["draws"]) if fr["draws"] else "n/a"))
    out.append("")
    out.append("  what a smaller rollout budget would have reached (unbiased pass@k)")
    out.append("    %-6s %-12s %s" % ("k", "legal", "legal+correct"))
    for row in r["ladder"]:
        out.append("    %-6d %-12s %s" % (row["k"], _pct(row["legal"]),
                                          _pct(row["rewardable"])))
    out.append("")
    out.append("  by the kind the case was refused for, worst first "
               "(closed kinds last: the engine may never answer those)")
    out.append("    %-24s %6s %14s %14s %10s"
               % ("kind", "cases", "reached", "rewarded", "legal/draw"))
    for row in r["by_kind"][:limit]:
        reached = "%3d/%-3d %7s" % (row["reached"], row["cases"],
                                    _pct(row["reached"] / row["cases"]))
        rewarded = ("%3d/%-3d %7s" % (row["rewarded"], row["scored"],
                                      _pct(row["rewarded"] / row["scored"]))
                    if row["scored"] else "%14s" % "no test")
        out.append("    %-24s %6d %14s %14s %10s"
                   % (row["kind"] + (" *" if row["closed"] else
                                     _TIER_MARK.get(row["tier"], "")), row["cases"],
                      reached, rewarded,
                      _pct(row["legal_draws"] / row["draws"]) if row["draws"] else "n/a"))
    if len(r["by_kind"]) > limit:
        out.append("    (%d more kinds not shown)" % (len(r["by_kind"]) - limit))
    out.append("")
    out.append("  * closed kind: the engine has declared no reimplementation may answer it,")
    out.append("    so the model's job there is to REWRITE and a low row is the corpus")
    out.append("    working as designed, not a gap to close in the engine.")
    out.append("  - falling back is the right answer for this kind; ~ nothing asked "
               "for the subset")
    out.append("    at all. Neither is in the floor.")
    out.append("")
    verdict = "at or above" if r["reachable"] >= floor else "BELOW"
    out.append("  reachability %s the %s floor: %s"
               % (verdict, _pct(floor), _pct(r["reachable"])))
    if r["reachable"] < floor:
        out.append("  Below the floor, a reward-based stage is reinforcing what the policy")
        out.append("  cannot produce: the unreachable cases return an advantage of zero.")
        out.append("  Either a teacher supplies the trajectories or the engine grows toward")
        out.append("  them — training harder on this pool is the one option ruled out.")
    if r["mismatch"]:
        out.append("")
        out.append("  MISMATCH %d — invariant 1: always a bug, never the model's."
                   % r["mismatch"])
        out.append("  No reachability number is reportable until these are closed.")
    if held_out:
        out.append("")
        out.extend("  " + line for line in refusals.HELD_OUT_BANNER.splitlines())
    return "\n".join(out)


def _gate_b(base: Dict[str, Any], tuned: Dict[str, Any], cases: Dict[str, Dict[str, Any]],
            engine: str, floor: float) -> Dict[str, Any]:
    """Did the tuned model stop importing things the engine is happy to serve?

    Scoped to cases whose REFERENCE solution imports a served module — those are
    the tasks where reaching for the import is the right answer and dropping it
    is the degenerate maximum. The comparison is a retention ratio rather than a
    difference, because the base rate is what "still uses it" is relative to.
    """
    wanted: Dict[str, "set[str]"] = {}
    for cid, case in cases.items():
        mods = imports_in(case.get("reference") or "")
        if mods:
            wanted[cid] = mods
    served = modules_served(engine, sorted({m for v in wanted.values() for m in v}))
    scoped = {c: {m for m in v if served.get(m)} for c, v in wanted.items()}
    scoped = {c: v for c, v in scoped.items() if v}

    def rate(a: Dict[str, Any]) -> Tuple[float, int]:
        hits = tot = 0
        for cid, mods in scoped.items():
            for prog in a["programs_by_case"].get(cid, []):
                tot += 1
                hits += 1 if (imports_in(prog) & mods) else 0
        return (hits / tot if tot else float("nan")), tot

    b_rate, n = rate(base)
    t_rate, _ = rate(tuned)
    ok = (n == 0) or (b_rate == 0) or (t_rate >= floor * b_rate)
    return {"name": "B supported-import retention", "cases": len(scoped), "programs": n,
            "served": sorted({m for v in scoped.values() for m in v}),
            "base": b_rate, "tuned": t_rate,
            "retention": (t_rate / b_rate) if b_rate else float("nan"),
            "floor": floor, "pass": bool(ok),
            "unmeasured": n == 0 or b_rate == 0}


def compare(base: Dict[str, Any], tuned: Dict[str, Any],
            cases: Optional[Dict[str, Dict[str, Any]]] = None, *,
            engine: Optional[str] = None,
            gate_a: float = GATE_A_MAX_DROP,
            gate_b: float = GATE_B_MIN_RETENTION,
            gate_c: float = GATE_C_MAX_GROWTH) -> Dict[str, Any]:
    """ΔSLR, cluster-bootstrapped by case, with the three gates beside it."""
    if base["fingerprint"] != tuned["fingerprint"]:
        raise ValueError("two fingerprints, one comparison: %s vs %s"
                         % (base["fingerprint"], tuned["fingerprint"]))
    delta = stats.paired_delta(base["legal_rate"], tuned["legal_rate"])
    corr = stats.paired_delta(base["pass_rate"], tuned["pass_rate"])

    shared = sorted(set(base["tokens"]) & set(tuned["tokens"]))
    b_tok = sum(base["tokens"][c] for c in shared) / len(shared) if shared else float("nan")
    t_tok = sum(tuned["tokens"][c] for c in shared) / len(shared) if shared else float("nan")

    c_shared = sorted(set(base["pass_rate"]) & set(tuned["pass_rate"]))
    c_base = (sum(base["pass_rate"][c] for c in c_shared) / len(c_shared)
              if c_shared else float("nan"))
    c_tuned = (sum(tuned["pass_rate"][c] for c in c_shared) / len(c_shared)
               if c_shared else float("nan"))
    gates = [
        {"name": "A correctness non-regression",
         "base": c_base, "tuned": c_tuned,
         "delta": corr["delta"], "limit": -gate_a,
         "pass": bool(corr["delta"] >= -gate_a)},
    ]
    if cases:
        gates.append(_gate_b(base, tuned, cases, engine or tuned["engine"], gate_b))
    else:
        gates.append({"name": "B supported-import retention", "pass": False,
                      "unmeasured": True, "note": "no case file: gate B not evaluated"})
    growth = (t_tok / b_tok - 1.0) if b_tok else float("nan")
    gates.append({"name": "C length non-inflation", "base": b_tok, "tuned": t_tok,
                  "growth": growth, "limit": gate_c,
                  "pass": bool(growth <= gate_c)})

    kinds = sorted(set(base["by_kind"]) | set(tuned["by_kind"]))
    by_kind = [{"kind": k, "base": base["by_kind"].get(k, 0),
                "tuned": tuned["by_kind"].get(k, 0),
                "delta": tuned["by_kind"].get(k, 0) - base["by_kind"].get(k, 0)}
               for k in kinds]
    by_kind.sort(key=lambda r: (-abs(r["delta"]), -max(r["base"], r["tuned"])))

    return {"fingerprint": base["fingerprint"], "delta": delta, "correctness": corr,
            "not_genuine": base.get("not_genuine", 0) + tuned.get("not_genuine", 0),
            "base_slr": base["slr"], "tuned_slr": tuned["slr"],
            "by_kind": by_kind, "gates": gates,
            "gates_pass": all(g.get("pass") for g in gates),
            "mismatch": base["mismatch"] + tuned["mismatch"],
            "programs": base.get("graded", base["programs"]) + tuned.get("graded", tuned["programs"])}


def _pct(x: float) -> str:
    return "n/a" if x != x else "%.2f%%" % (100.0 * x)


def report(cmp: Dict[str, Any], *, before: str, after: str, limit: int = 12,
           mde: float = 0.0) -> str:
    """Render a comparison. The gates come before the headline, on purpose."""
    d, c = cmp["delta"], cmp["correctness"]
    out: List[str] = []
    out.append("subset-legality: %s -> %s   @ engine %s" % (before, after, cmp["fingerprint"]))
    out.append("  %d programs graded, %d cases, cluster-bootstrapped by case"
               % (cmp["programs"], d["n_pairs"]))
    if cmp.get("not_genuine"):
        out.append("  %d of them flagged not-genuine (a hard-coded literal is legal too) "
                   "— gate A is what answers that" % cmp["not_genuine"])
    out.append("")
    out.append("  SLR   base %s   tuned %s" % (_pct(cmp["base_slr"]), _pct(cmp["tuned_slr"])))
    ci = d.get("ci95") or {}
    lo, hi = ci.get("lo", float("nan")), ci.get("hi", float("nan"))
    out.append("  dSLR  %+.2fpp   95%% CI [%+.2f, %+.2f]"
               % (100 * d["delta"], 100 * lo, 100 * hi))
    if mde:
        out.append("  MDE   lower bound must exceed %+.2fpp — %s"
                   % (100 * mde, "met" if lo > mde else "NOT met"))
    out.append("")
    out.append("  gates (a failed gate voids the result; it does not discount it)")
    for g in cmp["gates"]:
        mark = "PASS" if g.get("pass") else ("n/a " if g.get("unmeasured") else "FAIL")
        if g["name"].startswith("A"):
            extra = "%s -> %s, %+.2fpp, floor %+.2fpp" % (
                _pct(g["base"]), _pct(g["tuned"]), 100 * g["delta"], 100 * g["limit"])
        elif g["name"].startswith("B"):
            extra = ("not evaluated" if g.get("unmeasured") else
                     "%s -> %s over %d programs, retention %.2f, floor %.2f"
                     % (_pct(g["base"]), _pct(g["tuned"]), g["programs"],
                        g["retention"], g["floor"]))
        else:
            extra = "%.0f -> %.0f tokens, %+.1f%%, cap %+.0f%%" % (
                g["base"], g["tuned"], 100 * g["growth"], 100 * g["limit"])
        out.append("    %s  %-30s %s" % (mark, g["name"], extra))
    out.append("")
    out.append("  by refusal kind (programs refused; the scalar can move on one row)")
    for r in cmp["by_kind"][:limit]:
        out.append("    %-22s %5d -> %5d   %+d" % (r["kind"], r["base"], r["tuned"], r["delta"]))
    if cmp["mismatch"]:
        out.append("")
        out.append("  MISMATCH %d — invariant 1: always a bug, never the model's."
                   % cmp["mismatch"])
        out.append("  The legality number is not reportable until these are closed.")
    return "\n".join(out)
