"""Build SFT targets the only way that can be trusted here: by execution.

THE PROBLEM THIS SOLVES. The corpus has 175 training cases and not one reference
solution — a tier-1 rewrite is exactly what nobody has written. You cannot
supervise on targets you do not have, and a target written by a judge model
would be graded by the same kind of thing that wrote it.

So the targets are *sampled and verified*: draw k completions from the base model
at temperature, run each through the real acceptance test, and keep what passes.
Every training example is then a program that provably reproduces CPython's
output and provably runs on the engine. On-policy, verified by execution, and
free of any grader.

The method also scopes the corpus for free. A case with no tier-1 solution yields
nothing however hard you sample, so it simply does not enter training — which
settles by measurement the question of which refusals are fair targets and which
are engine coverage gaps.

TWO GUARDS, both of which this would be worthless without.

*Contamination.* The held-out split is frozen and must never be sampled from.
:func:`train_cases` reads the lock and asserts disjointness; a held-out id
reaching the sampler raises rather than warns.

*The degenerate solution.* The acceptance test asks for byte-identical stdout, so
`print("<the expected output>")` passes it. Rejection sampling would happily
accept that and then teach the model to do it. :func:`looks_like_literal_output`
rejects a completion that carries the expected output as a literal, and every
rejection is written down and counted — a high count is a finding about the
corpus, not a nuisance.
"""

from __future__ import annotations

import concurrent.futures
import datetime as _dt
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import split as splitmod
from .acceptance import run_test
from .backends import BackendError, ChatBackend
from .evaluate import render_messages
from .extract import extract_program
from .jsonio import append_jsonl, read_jsonl, write_json, write_jsonl

# Below this, an output is too short for its presence in the source to mean
# anything: a case whose answer is `42` is legitimately solved by `print(42)`.
_LITERAL_MIN_CHARS = 12
_LITERAL_MIN_LINE = 4


def looks_like_literal_output(program: str, expected_stdout: str) -> str:
    """Why this completion is printing the answer rather than computing it, or ""."""
    want = (expected_stdout or "").strip()
    if not want:
        return ""
    if len(want) >= _LITERAL_MIN_CHARS and want in program:
        return "whole expected output appears verbatim in the source"
    lines = [ln.strip() for ln in want.splitlines() if len(ln.strip()) >= _LITERAL_MIN_LINE]
    if len(lines) >= 2:
        hits = [ln for ln in lines if ln in program]
        if len(hits) >= 2 and len("".join(hits)) >= 0.5 * len("".join(lines)):
            return "%d of %d output lines appear verbatim in the source" % (
                len(hits), len(lines))
    return ""


def train_cases(data_dir: Path) -> List[Dict[str, Any]]:
    """The training split, with the frozen held-out set asserted absent."""
    lock = splitmod.load_lock(data_dir)
    if lock is None:
        raise ValueError("the split is not frozen; refusing to sample")
    held = {e["id"] for e in lock["holdout"]}
    cases = read_jsonl(data_dir / "corpus.jsonl")
    train = [c for c in cases if c["id"] not in held]
    leaked = held & {c["id"] for c in train}
    if leaked:
        raise AssertionError("held-out cases reached the sampler: %s" % sorted(leaked)[:5])
    return train


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sample_targets(
    backend: ChatBackend,
    cases: Sequence[Dict[str, Any]],
    out_dir: Path,
    *,
    k: int = 16,
    keep: int = 2,
    temperature: float = 1.0,
    top_p: float = 0.95,
    max_tokens: int = 2048,
    enable_thinking: bool = False,
    concurrency: int = 16,
    price_hour: float = 0.0,
    max_spend: float = 0.0,
    progress=None,
) -> Dict[str, Any]:
    """Draw k, verify each, keep the shortest ``keep`` that pass and do not cheat."""
    out_dir.mkdir(parents=True, exist_ok=True)
    draws_path = out_dir / "draws.jsonl"
    started = __import__("time").time()
    lock = threading.Lock()
    spend = [0.0]
    aborted: List[str] = []

    def budget() -> float:
        return spend[0] + price_hour * (__import__("time").time() - started) / 3600.0

    def one(job: Tuple[Dict[str, Any], int]) -> Optional[Dict[str, Any]]:
        case, idx = job
        if aborted:
            return None
        try:
            comp = backend.complete(
                render_messages(case), temperature=temperature, top_p=top_p,
                max_tokens=max_tokens, enable_thinking=enable_thinking, seed=1000 + idx,
            )
        except BackendError as exc:
            return {"case_id": case["id"], "draw": idx, "harness_error": str(exc)[:300]}
        program, how = extract_program(comp.text, comp.reasoning)
        rec: Dict[str, Any] = {
            "case_id": case["id"], "draw": idx, "how": how,
            "completion_tokens": comp.completion_tokens,
            "cost_usd": backend.cost(comp.prompt_tokens, comp.completion_tokens),
        }
        if program is None:
            return dict(rec, kept=False, reason="no-code")
        verdict = run_test(case["test"], program)
        if verdict.harness_error:
            return dict(rec, harness_error=verdict.harness_error)
        if not verdict.passed:
            return dict(rec, kept=False, reason=verdict.reason, program=program)
        cheat = looks_like_literal_output(program, case["test"].get("expect_stdout", ""))
        if cheat:
            return dict(rec, kept=False, reason="literal-output", detail=cheat,
                        program=program)
        return dict(rec, kept=True, reason="pass", program=program)

    jobs = [(c, i) for c in cases for i in range(k)]
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        for rec in pool.map(one, jobs):
            if rec is None:
                continue
            with lock:
                append_jsonl(draws_path, rec)
                spend[0] += rec.get("cost_usd", 0.0)
                done += 1
                if max_spend and budget() > max_spend and not aborted:
                    aborted.append("spend cap: $%.2f > $%.2f" % (budget(), max_spend))
            if progress:
                progress(done, len(jobs), rec)

    return fold_draws(out_dir, cases, k=k, keep=keep,
                      spend=round(budget(), 4), aborted=(aborted[0] if aborted else None))


def fold_draws(out_dir: Path, cases: Sequence[Dict[str, Any]], *,
               k: int, keep: int, spend: float = 0.0,
               aborted: Optional[str] = None) -> Dict[str, Any]:
    """Turn the draws into an SFT file and a yield report. Pure; safe to re-run."""
    by_id = {c["id"]: c for c in cases}
    draws = [d for d in read_jsonl(out_dir / "draws.jsonl") if not d.get("harness_error")]
    passing: Dict[str, List[Dict[str, Any]]] = {}
    counts: Dict[str, List[int]] = {}
    reasons: Dict[str, int] = {}
    for d in draws:
        cid = d["case_id"]
        c = counts.setdefault(cid, [0, 0])
        c[1] += 1
        if d.get("kept"):
            c[0] += 1
            passing.setdefault(cid, []).append(d)
        else:
            reasons[d.get("reason", "?")] = reasons.get(d.get("reason", "?"), 0) + 1

    rows: List[Dict[str, Any]] = []
    for cid, ds in sorted(passing.items()):
        case = by_id.get(cid)
        if case is None:
            continue
        # Shortest first: among programs that all pass, the short one is the one
        # that actually used the substitution rather than working around it.
        best = sorted({d["program"] for d in ds}, key=lambda p: (len(p), p))[:keep]
        for prog in best:
            rows.append({
                "case_id": cid,
                "category": case["category"],
                "messages": render_messages(case) + [
                    {"role": "assistant", "content": "```python\n%s\n```" % prog.strip()}],
                "program": prog,
                "verified": True,
            })
    write_jsonl(out_dir / "sft.jsonl", rows)

    solved = [cid for cid, (p, _) in counts.items() if p > 0]
    report = {
        "sampled_at": _now(), "k": k, "keep": keep,
        "cases": len(counts),
        "cases_with_a_verified_solution": len(solved),
        "yield_rate": (len(solved) / len(counts)) if counts else 0.0,
        "sft_examples": len(rows),
        "draws": len(draws),
        "draw_pass_rate": sum(p for p, _ in counts.values()) / max(1, len(draws)),
        "rejected_by_reason": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "spend_usd": spend,
        "aborted": aborted,
        "by_category": _by_category(by_id, counts),
    }
    write_json(out_dir / "sample.json", report)
    return report


def _by_category(by_id, counts) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for cid, (p, n) in counts.items():
        cat = by_id[cid]["category"] if cid in by_id else "?"
        e = out.setdefault(cat, {"cases": 0, "solved": 0})
        e["cases"] += 1
        if p > 0:
            e["solved"] += 1
    for e in out.values():
        e["yield"] = e["solved"] / e["cases"]
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["cases"]))
