"""Which cases a Step 2 rung draws, and whether one shard of it fits its caps.

Generation, the reference check and the grader each select their cases through
`cases_from_env`, so the three can never disagree about a shard: they read the
same four environment variables and call the one helper,
`pipeline.positive_control.shard_cases`.

Run as a script it is the free gate in front of a `full` shard. It prints the
shard's projected cost and wall-clock -- counts and dollars, never a case id
-- and refuses a shard that could not finish, because a paid run that stops
half-way is never graded (`positive_control_grade.generation_complete`):
money spent on a partial shard buys nothing.

The projection is measured, not listed-price arithmetic. Every constant below
names the run it came from; re-measure before relying on it after the prompt,
the provider or the grader changes.
"""
from __future__ import annotations

from decimal import Decimal
import json
import os
import re
import sys

#: The train split at seed 1111 of `banks/v3-20260920b` (run 35491218203): the
#: `full` rung draws all of it.
FULL_CASES = 1355
#: Dollars per request, both arms pooled: $3.63061517 for the 1,536 requests
#: of the 192-case `targets` rung (run 35767396604, 2026-09-22). The 64-case
#: smoke measured $1.20715678 / 512 (run 35751938025), within 0.3%.
USD_PER_REQUEST = Decimal("3.63061517") / 1536
#: Requests per minute actually achieved at the nominal 45: 1,536 requests in
#: 35.07 minutes (run 35767396604). Sizing by the nominal rate is how a shard
#: ends up needing more wall-clock than its dispatch window.
MEASURED_RPM = Decimal("43.8")
#: Grading cost per completion with 8 workers: 512 completions in 21 min 19 s
#: (run 35759939928). More workers is not a free fix -- `native` is
#: host-load-dependent.
GRADE_SECONDS_PER_COMPLETION = Decimal("2.5")
#: What of the grade job's 360-minute timeout is left for grading once the
#: engine and image are built and the evidence and tokenizer downloaded.
GRADE_MINUTES_CAP = 300
#: Headroom over the measured cost: completion length varies by shard.
COST_MARGIN = Decimal("0.15")
#: `step2_generate` runs four requests in flight, and each holds a full-context
#: reservation (`positive_control_generate.Budget`) until it settles.
IN_FLIGHT = 4

SHARD_ENV = (("shard_index", "STEP2_SHARD_INDEX"), ("shard_count", "STEP2_SHARD_COUNT"),
             ("skip_prefix", "STEP2_SKIP_PREFIX"))
_DEFAULTS = {"shard_index": 0, "shard_count": 1, "skip_prefix": 0}


class ShardError(ValueError):
    pass


def shard_from_env(environ=None):
    """The shard as three integers; unset means the whole rung, unsharded."""
    environ = os.environ if environ is None else environ
    shard = {}
    for key, name in SHARD_ENV:
        raw = environ.get(name, "")
        if raw == "":
            shard[key] = _DEFAULTS[key]
        elif re.fullmatch(r"[0-9]{1,5}", raw):
            shard[key] = int(raw)
        else:
            raise ShardError("%s must be a non-negative integer" % name)
    if shard["shard_count"] < 1 or shard["shard_index"] >= shard["shard_count"]:
        raise ShardError("STEP2_SHARD_INDEX must lie in 0..STEP2_SHARD_COUNT-1")
    return shard


def cases_from_env(rows, environ=None):
    """The rung's cases for this shard, from the raw private train bank."""
    from pipeline.positive_control import population, shard_cases
    environ = os.environ if environ is None else environ
    return shard_cases(population(rows), int(environ["STEP2_CASES"]), **shard_from_env(environ))


def shard_size(total, shard_index=0, shard_count=1, skip_prefix=0):
    """How many cases `shard_cases` returns, without the bank."""
    return len(range(total)[skip_prefix:][shard_index::shard_count])


def reservation_usd():
    from pipeline.positive_control import MAX_TOKENS, PRICE_IN, PRICE_OUT
    from pipeline.positive_control_generate import CONTEXT_TOKENS
    return (Decimal(CONTEXT_TOKENS) * Decimal(str(PRICE_IN)) +
            Decimal(MAX_TOKENS) * Decimal(str(PRICE_OUT))) / Decimal(1000000)


def projection(total, samples, shard, *, max_seconds):
    """Requests, dollars and minutes for one shard; aggregates only."""
    cases = shard_size(total, **shard)
    requests = cases * samples * 2
    usd = USD_PER_REQUEST * requests
    return {
        "cases": cases, "requests": requests,
        "projected_usd": float(round(usd, 2)),
        "ceiling_needed_usd": float(round(usd * (1 + COST_MARGIN) + IN_FLIGHT * reservation_usd(), 2)),
        "generation_minutes": float(round(Decimal(requests) / MEASURED_RPM, 1)),
        "generation_window_minutes": max_seconds // 60,
        "grade_minutes": float(round(Decimal(requests) * GRADE_SECONDS_PER_COMPLETION / 60, 1)),
        "grade_window_minutes": GRADE_MINUTES_CAP,
        "basis": "measured: run 35767396604 ($3.63061517 / 1,536 requests, 43.8 rpm); "
                 "run 35759939928 (2.5 s per graded completion)",
    }


def refusals(plan, ceiling_usd):
    """Why this shard would stop before it is complete; empty when it fits."""
    why = []
    if plan["ceiling_needed_usd"] > ceiling_usd:
        why.append("ceiling $%.2f is below the $%.2f this shard needs"
                   % (ceiling_usd, plan["ceiling_needed_usd"]))
    # 10% of the window is left for the rate limiter's own jitter.
    if plan["generation_minutes"] > 0.9 * plan["generation_window_minutes"]:
        why.append("generation needs %.1f of a %d-minute window"
                   % (plan["generation_minutes"], plan["generation_window_minutes"]))
    if plan["grade_minutes"] > plan["grade_window_minutes"]:
        why.append("grading needs %.1f of %d minutes" % (plan["grade_minutes"], plan["grade_window_minutes"]))
    return why


def main(environ=None):
    environ = os.environ if environ is None else environ
    try:
        shard = shard_from_env(environ)
        rung = environ["STEP2_RUNG"]
        total = int(environ["STEP2_CASES"])
        if rung == "full" and total != FULL_CASES:
            raise ShardError("the full rung draws all %d train cases" % FULL_CASES)
        if rung != "full" and shard != _DEFAULTS:
            raise ShardError("only the full rung is sharded")
        if shard["skip_prefix"] >= total:
            raise ShardError("STEP2_SKIP_PREFIX leaves no case to draw")
        plan = projection(total, int(environ["STEP2_SAMPLES"]), shard,
                          max_seconds=int(environ["STEP2_MAX_SECONDS"]))
    except (KeyError, ShardError) as exc:
        print("step2 shard refused: %s" % exc, file=sys.stderr)
        return 1
    plan.update(rung=rung, **shard)
    print(json.dumps(plan, sort_keys=True))
    why = refusals(plan, float(environ["STEP2_CEILING_USD"])) if rung == "full" else []
    for reason in why:
        print("step2 shard refused: %s" % reason, file=sys.stderr)
    return int(bool(why))


if __name__ == "__main__":
    sys.exit(main())
