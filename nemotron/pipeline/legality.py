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
    census = None
    if cache is not None and cache.exists():
        import json as _json
        stored = _json.loads(cache.read_text(encoding="utf-8"))
        # A cached replay is only a replay of the SAME engine. Reusing one across
        # a rebuild is the confound this endpoint is most exposed to, so the
        # fingerprint is checked rather than trusted.
        if stored.get("fingerprint") == eng.identity()["fingerprint"]:
            census = stored["census"]
    if census is None:
        census = refusals.on_policy(attempts, engine, tests=tests, workers=workers)
        if cache is not None:
            import json as _json
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(_json.dumps(
                {"fingerprint": eng.identity()["fingerprint"], "engine": engine,
                 "census": census}, sort_keys=True), encoding="utf-8")
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

    legal_rate = {c: sum(1 for r in rows if r["verdict"] != "UNSUPPORTED") / len(rows)
                  for c, rows in by_case.items()}
    pass_rate = {c: sum(1 for p in v if p) / len(v) for c, v in passed.items()}
    tok_mean = {c: sum(v) / len(v) for c, v in tokens.items() if v}
    n = census["programs"] or 1
    degenerate = sum(1 for a in attempts if a.get("failure_category") == "not-genuine")
    return {
        "not_genuine": degenerate,
        "engine": engine,
        "fingerprint": eng.identity()["fingerprint"],
        "programs": census["programs"],
        "cases": len(by_case),
        "slr": sum(1 for r in census["rows"] if r["verdict"] != "UNSUPPORTED") / n,
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
            "base_slr": base["slr"], "tuned_slr": tuned["slr"],
            "by_kind": by_kind, "gates": gates,
            "gates_pass": all(g.get("pass") for g in gates),
            "mismatch": base["mismatch"] + tuned["mismatch"],
            "programs": base["programs"] + tuned["programs"]}


def _pct(x: float) -> str:
    return "n/a" if x != x else "%.2f%%" % (100.0 * x)


def report(cmp: Dict[str, Any], *, before: str, after: str, limit: int = 12,
           mde: float = 0.0) -> str:
    """Render a comparison. The gates come before the headline, on purpose."""
    d, c = cmp["delta"], cmp["correctness"]
    out: List[str] = []
    out.append("subset-legality: %s -> %s   @ engine %s" % (before, after, cmp["fingerprint"]))
    out.append("  %d programs, %d cases, cluster-bootstrapped by case" % (cmp["programs"], d["n_pairs"]))
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
