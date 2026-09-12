"""Step 2. pass@1 over the frozen held-out split, and the number everything else is measured against.

SIX THINGS THIS REFUSES TO DO, each of which would quietly break comparability:

1. Run against a drifted split. :func:`split.verify` is checked first, and a
   mismatch aborts. A pass rate over a different set of cases is a different
   number wearing the same name.
2. Score a harness error as a failed program. A 500 from the server, a timeout
   talking to it, a missing pytest — those are recorded as ``harness_error`` and
   excluded from the denominator, and the summary says how many there were. A
   flaky endpoint must not read as a worse model.
3. Hide the prompt. The exact template is hashed into the summary; change it and
   the hash changes, and a later run that does not match the baseline's hash is
   flagged rather than compared.
4. Lose work. Every attempt is appended and fsynced as it completes, so a run
   killed at any point — a spot preemption, a closed laptop — resumes from what
   it already has instead of starting over and re-spending.
5. Score an engine mismatch as a failed program. An engine that RUNS a program
   and disagrees with CPython is invariant 1 — always a bug in this repository,
   never the model's — so the attempt leaves the denominator exactly as a harness
   error does, and is counted and named on its own line instead of being dropped.
   A fine-tune that trips a new mismatch must not read as a worse model.
6. Resolve an unproven cheat either way. A pass the discriminator calls
   not-genuine is recorded as its own outcome — it reproduced the expected bytes
   without computing them, which is neither a pass nor a wrong answer — and a
   pass it cannot judge is scored exactly as it stands, with the doubt written
   down. See :func:`judge_authorship` for why the doubt falls that way.
"""

from __future__ import annotations

import concurrent.futures
import datetime as _dt
import inspect
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import split as splitmod
from . import stats
from .acceptance import Verdict, run_test
from .backends import BackendError, ChatBackend
from .classify import classify
from .extract import extract_program
from .jsonio import append_jsonl, read_json, read_jsonl, sha256_of, write_json

SYSTEM_PROMPT = (
    "You are a precise Python programmer. You write complete, self-contained "
    "Python 3 scripts that run correctly the first time, using only the standard "
    "library unless told otherwise."
)

USER_TEMPLATE = """{task}

Runtime contract:
{contract}

Return exactly one complete Python 3 script inside a single ```python code block. \
Write no text outside the block."""


def render_contract(test: Dict[str, Any]) -> str:
    """Tell the model how its program will be run. Everything here is true of the test."""
    lines = ["- Your program is saved as solution.py and run with: python3 solution.py"
             + ("".join(" " + str(a) for a in (test.get("argv") or [])))]
    if test.get("stdin") is not None:
        lines.append("- Input is supplied on standard input.")
    files = sorted((test.get("files") or {}).keys())
    if files:
        lines.append("- These files already exist in the working directory: "
                     + ", ".join(files))
    lines.append("- Print the result to standard output.")
    if test.get("expect_exit", 0) == 0:
        lines.append("- Exit with status 0.")
    lines.append("- The standard library is available. There is no network access.")
    return "\n".join(lines)


def render_messages(case: Dict[str, Any]) -> List[Dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(
            task=case["prompt"].strip(), contract=render_contract(case["test"]))},
    ]


def prompt_signature() -> str:
    return sha256_of({"system": SYSTEM_PROMPT, "user": USER_TEMPLATE})[:16]


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_holdout(data_dir: Path) -> List[Dict[str, Any]]:
    corpus = data_dir / "corpus.jsonl"
    ok, problems = splitmod.verify(corpus)
    if not ok:
        raise ValueError(
            "held-out split does not match its lock; refusing to measure.\n  "
            + "\n  ".join(problems[:5])
        )
    lock = splitmod.load_lock(data_dir) or {"holdout": []}
    held = {e["id"] for e in lock["holdout"]}
    return [c for c in read_jsonl(corpus) if c["id"] in held]


# ------------------------------------------------ who wrote the answer

GENUINE = "genuine"
NOT_GENUINE = "not-genuine"
UNDECIDED = "undecided"

# The discriminator lives in :mod:`pipeline.sample`, which owns the question
# "did this program compute the answer or carry it?". This is only the seam, and
# it names the contract rather than the implementation: the first of these that
# `sample` defines is asked, and anything else it answers is doubt.
# `discriminate` comes FIRST and `looks_like_literal_output` last, which is the
# order of evidence: the behavioural discriminator proves authorship by running
# the program against a mutated input, the syntactic guard only reports that the
# answer appears in the source. Integration caught them disagreeing by one
# letter — the seam asked for names `sample` did not define and read words it did
# not emit — so the discriminator was live, wired, and never once consulted.
_ENTRY_POINTS = ("authorship_verdict", "grade_authorship", "judge_authorship",
                 "discriminate", "looks_like_literal_output")

_VERDICT_WORDS = {
    "genuine": GENUINE, "computed": GENUINE, "ok": GENUINE, "pass": GENUINE,
    "not-genuine": NOT_GENUINE, "not_genuine": NOT_GENUINE, "recited": NOT_GENUINE,
    "recitation": NOT_GENUINE, "literal-output": NOT_GENUINE, "cheat": NOT_GENUINE,
    "undecided": UNDECIDED, "unknown": UNDECIDED, "cannot-tell": UNDECIDED,
    "unsure": UNDECIDED,
    "computes": GENUINE,
    "recites": NOT_GENUINE,
    "undecidable": UNDECIDED,
}


def _discriminator():
    """The function :mod:`pipeline.sample` exposes, or None.

    Imported late because `sample` imports this module for the prompt; at module
    scope that is a cycle.
    """
    from . import sample
    for name in _ENTRY_POINTS:
        fn = getattr(sample, name, None)
        if callable(fn):
            return fn
    return None


def _arguments(case: Dict[str, Any], program: str) -> Dict[str, Any]:
    """Everything a discriminator could ask for, keyed by parameter name."""
    test = case.get("test") or {}
    return {
        "case": case, "test": test, "program": program,
        "prompt": case.get("prompt") or "",
        "expected_stdout": test.get("expect_stdout") or "",
        "expect_stdout": test.get("expect_stdout") or "",
    }


def _invoke(fn, case: Dict[str, Any], program: str) -> Any:
    """Call the discriminator in whatever shape it declares."""
    args = _arguments(case, program)
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return fn(case, program)
    wanted = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD,
                                              p.KEYWORD_ONLY)]
    if any(p.name not in args and p.default is p.empty for p in wanted):
        return fn(case, program)
    named = [p for p in wanted if p.name in args]
    if any(p.kind is p.POSITIONAL_ONLY for p in named):
        return fn(*[args[p.name] for p in named])
    return fn(**{p.name: args[p.name] for p in named})


def _read_verdict(answer: Any) -> Tuple[str, str]:
    """Normalize a discriminator's answer to (status, detail).

    An answer that does not name one of :data:`_VERDICT_WORDS` is read as doubt,
    including a bare "yes" — see :func:`judge_authorship`.
    """
    if answer is None:
        return UNDECIDED, "the discriminator returned nothing"
    detail = ""
    if isinstance(answer, (tuple, list)) and len(answer) == 2:
        answer, detail = answer[0], str(answer[1] or "")
    if isinstance(answer, dict):
        detail = detail or str(answer.get("detail") or answer.get("reason") or "")
        answer = answer.get("verdict", answer.get("status", UNDECIDED))
    elif not isinstance(answer, (str, bool)):
        detail = detail or str(getattr(answer, "detail", "")
                               or getattr(answer, "reason", "") or "")
        answer = getattr(answer, "verdict", getattr(answer, "status", answer))
    if isinstance(answer, bool):
        return UNDECIDED, detail or "the discriminator answered yes/no"
    text = str(answer or "").strip()
    if not text:
        return GENUINE, detail
    word = _VERDICT_WORDS.get(text.lower())
    if word is None:
        return UNDECIDED, text[:200]
    return word, detail


def judge_authorship(case: Dict[str, Any], program: str) -> Tuple[str, str]:
    """Did the model compute this answer or carry it? (status, detail).

    THE DEFAULT LEANS TOWARD THE MODEL, DELIBERATELY. The two errors are not
    symmetric: a missed cheat costs one attempt and still shows up in the
    undecided count, while a false accusation makes a frozen held-out case
    unpassable by a correct program and then prints that accusation as a finding.
    Measured on this corpus, the purely syntactic guard fired on six correct
    programs for every real cheat it caught, because the dominant task shape here
    is "rewrite this program without <module>" and a faithful rewrite keeps the
    literals it was given. So only an answer that explicitly names a verdict may
    flip a pass; everything else — an unreadable answer, a two-state guard that
    cannot say "cannot tell", a discriminator that is absent or raises — is
    recorded as :data:`UNDECIDED` and the attempt is scored as it stands.
    """
    fn = _discriminator()
    if fn is None:
        return UNDECIDED, "pipeline.sample exposes no discriminator"
    try:
        return _read_verdict(_invoke(fn, case, program))
    except Exception as exc:  # a broken discriminator may not fail a paid run
        return UNDECIDED, "the discriminator raised: %s: %s" % (type(exc).__name__, exc)


def score_verdict(case: Dict[str, Any], program: str, verdict: Verdict) -> Dict[str, Any]:
    """The recorded outcome of one graded attempt, as fields to merge into it.

    The single place a verdict becomes a score, so that a replay of stored
    completions cannot reach a different answer than the run that produced them.
    """
    if verdict.harness_error:
        return {"harness_error": verdict.harness_error, "passed": False}
    if verdict.reason == "malformed-test":
        # The test spec is what failed to hold up, so this is our defect: the
        # attempt leaves the denominator rather than scoring the model a zero.
        return {"harness_error": "malformed test: %s" % verdict.detail[:300],
                "passed": False}
    if verdict.passed:
        status, why = judge_authorship(case, program)
        if status == NOT_GENUINE:
            # Its own outcome: it reproduced the expected bytes, so it is not a
            # wrong answer, and it did not compute them, so it is not a pass.
            return {"passed": False, "reason": "not-genuine", "detail": why[:500],
                    "failure_category": "not-genuine", "authorship": NOT_GENUINE}
        if status == UNDECIDED:
            return {"passed": True, "reason": "pass", "detail": "",
                    "failure_category": "", "authorship": UNDECIDED,
                    "authorship_detail": why[:300]}
        # Recorded explicitly rather than left absent: "the discriminator looked
        # and found nothing" and "nothing looked" are different claims, and an
        # attempt that cannot tell them apart cannot be audited later.
        return {"passed": True, "reason": "pass", "detail": "",
                "failure_category": "", "authorship": GENUINE}
    # An engine mismatch is recorded like any other failed attempt and is
    # subtracted by reason in summarize_run. Unlike a harness error it is never
    # retried: the attempt completed, and only the engine is at fault.
    return {"passed": False, "reason": verdict.reason, "detail": verdict.detail[:500],
            "failure_category": classify(verdict, had_code=True)}


def _recorded_gpu_cost(run_dir: Path) -> float:
    """A re-summarize must not silently zero the GPU bill the run already paid."""
    p = run_dir / "progress.json"
    if not p.exists():
        return 0.0
    try:
        return float(read_json(p).get("spend_gpu_usd") or 0.0)
    except (ValueError, OSError):
        return 0.0


class Evaluation:
    """One eval run, resumable, writing as it goes."""

    def __init__(
        self,
        backend: ChatBackend,
        cases: List[Dict[str, Any]],
        run_dir: Path,
        *,
        samples: int = 1,
        temperature: float = 1.0,
        top_p: float = 0.95,
        max_tokens: int = 4096,
        enable_thinking: bool = True,
        seed: Optional[int] = 1234,
        concurrency: int = 4,
        max_spend: float = 0.0,
        price_hour: float = 0.0,
        label: str = "",
    ) -> None:
        self.backend = backend
        self.cases = cases
        self.run_dir = run_dir
        self.samples = samples
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        self.enable_thinking = enable_thinking
        self.seed = seed
        self.concurrency = max(1, concurrency)
        self.max_spend = max_spend
        # Two ways to pay for the same tokens. A hosted endpoint bills per token
        # and price_hour is 0; a GPU you rented bills per hour and the token
        # prices are 0. The sweep's dollar column has to work for both, so both
        # are tracked and the spend cap sees their sum.
        self.price_hour = price_hour
        # A resumed run restarts its wall clock, so the GPU-hours the earlier
        # segments already paid for are carried forward. Without this the spend
        # cap under-counts after every preemption -- when it matters most.
        self.price_hour_carried = _recorded_gpu_cost(run_dir)
        self.label = label
        self._lock = threading.Lock()
        self._spend = 0.0
        self._done = 0
        self._aborted: Optional[str] = None
        self._started = time.time()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.attempts_path = run_dir / "attempts.jsonl"

    # ---------------------------------------------------------------- meta

    def meta(self, holdout_manifest: str) -> Dict[str, Any]:
        return {
            "run_id": self.run_dir.name,
            "label": self.label,
            "started_at": _now(),
            "backend": self.backend.identity(),
            "sampling": {
                "temperature": self.temperature, "top_p": self.top_p,
                "max_tokens": self.max_tokens, "seed": self.seed,
                "enable_thinking": self.enable_thinking, "samples": self.samples,
            },
            "prompt_sha": prompt_signature(),
            "holdout_manifest_sha256": holdout_manifest,
            "n_cases": len(self.cases),
            "n_attempts_planned": len(self.cases) * self.samples,
            "pricing": {"in_per_m": self.backend.price_in,
                        "out_per_m": self.backend.price_out,
                        "gpu_per_hour": self.price_hour},
            "max_spend_usd": self.max_spend,
        }

    # ------------------------------------------------------------- running

    def _completed_keys(self) -> set:
        return {(a["case_id"], a["sample"]) for a in read_jsonl(self.attempts_path)
                if not a.get("harness_error")}

    def run(self, holdout_manifest: str, progress=None) -> Dict[str, Any]:
        write_json(self.run_dir / "meta.json", self.meta(holdout_manifest))
        done_keys = self._completed_keys()
        work: List[Tuple[Dict[str, Any], int]] = [
            (c, s) for c in self.cases for s in range(self.samples)
            if (c["id"], s) not in done_keys
        ]
        self._done = len(done_keys)
        total = len(self.cases) * self.samples
        self._spend = sum(a.get("cost_usd", 0.0) for a in read_jsonl(self.attempts_path))
        self._write_progress(total, "running")

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = [pool.submit(self._one, case, s) for case, s in work]
            for fut in concurrent.futures.as_completed(futures):
                rec = fut.result()
                if rec is None:
                    continue
                with self._lock:
                    append_jsonl(self.attempts_path, rec)
                    self._done += 1
                    self._spend += rec.get("cost_usd", 0.0)
                    total_spend = self._spend + self._gpu_cost()
                    if self.max_spend and total_spend > self.max_spend and not self._aborted:
                        self._aborted = ("spend cap: $%.2f > $%.2f"
                                         % (total_spend, self.max_spend))
                    self._write_progress(total, "running")
                if progress:
                    progress(self._done, total, rec)

        self._write_progress(total, "aborted" if self._aborted else "done")
        return self.summarize()

    def _one(self, case: Dict[str, Any], sample: int) -> Optional[Dict[str, Any]]:
        if self._aborted:
            return None
        base = {
            "run_id": self.run_dir.name, "case_id": case["id"], "sample": sample,
            "category_prior": case["category"], "ts": _now(),
        }
        try:
            comp = self.backend.complete(
                render_messages(case),
                temperature=self.temperature, top_p=self.top_p,
                max_tokens=self.max_tokens,
                seed=None if self.seed is None else self.seed + sample,
                enable_thinking=self.enable_thinking,
            )
        except BackendError as exc:
            return dict(base, harness_error=str(exc)[:500], passed=False)

        program, how = extract_program(comp.text, comp.reasoning)
        cost = self.backend.cost(comp.prompt_tokens, comp.completion_tokens)
        rec = dict(
            base,
            how=how,
            program=program or "",
            finish_reason=comp.finish_reason,
            prompt_tokens=comp.prompt_tokens,
            completion_tokens=comp.completion_tokens,
            latency_s=round(comp.latency_s, 3),
            cost_usd=cost,
        )
        if program is None:
            return dict(rec, passed=False, reason="no-code",
                        detail="no extractable program (finish=%s)" % comp.finish_reason,
                        failure_category="no-code")

        return dict(rec, **score_verdict(case, program, run_test(case["test"], program)))

    # ----------------------------------------------------------- reporting

    def _gpu_cost(self) -> float:
        return self.price_hour_carried + self.price_hour * (time.time() - self._started) / 3600.0

    def _write_progress(self, total: int, state: str) -> None:
        elapsed = time.time() - self._started
        rate = (self._done / elapsed) if elapsed > 0 and self._done else 0.0
        write_json(self.run_dir / "progress.json", {
            "run_id": self.run_dir.name, "label": self.label, "state": state,
            "done": self._done, "total": total,
            "elapsed_s": round(elapsed, 1),
            "eta_s": round((total - self._done) / rate, 1) if rate > 0 else None,
            "spend_usd": round(self._spend + self._gpu_cost(), 4),
            "spend_tokens_usd": round(self._spend, 4),
            "spend_gpu_usd": round(self._gpu_cost(), 4),
            "aborted": self._aborted, "updated_at": _now(),
        })

    def summarize(self) -> Dict[str, Any]:
        return summarize_run(self.run_dir, self.cases, gpu_cost=self._gpu_cost())


def is_engine_mismatch(attempt: Dict[str, Any]) -> bool:
    """True when the engine, not the model, failed this attempt (invariant 1).

    Keyed on the recorded reason rather than the label, so that attempts written
    before this rule existed re-summarize correctly.
    """
    return attempt.get("reason") == "engine-mismatch"


def scored_attempts(attempts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The attempts the model is measured on. One rule, for every reader of a run."""
    return [a for a in attempts
            if not a.get("harness_error") and not is_engine_mismatch(a)]


def summarize_run(run_dir: Path, cases: Optional[List[Dict[str, Any]]] = None,
                  gpu_cost: Optional[float] = None) -> Dict[str, Any]:
    """Fold attempts.jsonl into the run's headline number. Pure; safe to re-run."""
    attempts = read_jsonl(run_dir / "attempts.jsonl")
    meta = read_json(run_dir / "meta.json") if (run_dir / "meta.json").exists() else {}
    good = [a for a in attempts if not a.get("harness_error")]
    harness_errors = len(attempts) - len(good)
    # Invariant 1: an engine that ran a program and disagreed with CPython is a
    # bug in this repository, so scoring it would charge the model for our
    # defect. It leaves the denominator like a harness error, and is reported
    # rather than dropped because it is the loudest finding a run can produce.
    mismatched = [a for a in good if is_engine_mismatch(a)]
    scored = scored_attempts(attempts)
    doubted = [a for a in scored if a.get("authorship") == UNDECIDED]

    per_case: Dict[str, List[int]] = defaultdict(list)
    for a in scored:
        per_case[a["case_id"]].append(1 if a.get("passed") else 0)
    scores = [sum(v) / len(v) for _, v in sorted(per_case.items())]

    body = stats.summarize(scores)
    # pass@k beside pass@1: what the model CAN do beside what it DOES. The gap
    # between them is the entire budget a rejection-sampling SFT run has to work
    # with, so it is recorded on every multi-sample run rather than derived later.
    body["pass_at_k"] = stats.pass_at_k(
        [(sum(v), len(v)) for _, v in sorted(per_case.items())])
    body.update({
        "run_id": run_dir.name,
        "label": meta.get("label", ""),
        "model": (meta.get("backend") or {}).get("model"),
        "prompt_sha": meta.get("prompt_sha"),
        "holdout_manifest_sha256": meta.get("holdout_manifest_sha256"),
        "sampling": meta.get("sampling"),
        "n_attempts": len(scored),
        # What the run paid for, which is the right denominator for a per-attempt
        # cost and the wrong one for a pass rate.
        "n_attempts_billed": len(good),
        "harness_errors": harness_errors,
        "engine_mismatches": len(mismatched),
        "engine_mismatch_cases": sorted({a["case_id"] for a in mismatched}),
        # Keyed by case as well as detail: two different mismatches collapse into
        # one line otherwise, and neither can be traced back to a case.
        "engine_mismatch_detail": dict(Counter(
            "%s: %s" % (a["case_id"], a.get("detail") or "?") for a in mismatched)),
        "non_genuine_passes": sum(1 for a in scored if a.get("reason") == "not-genuine"),
        "non_genuine_cases": sorted({a["case_id"] for a in scored
                                     if a.get("reason") == "not-genuine"}),
        # Scored as they stand. A number here is a question about the
        # discriminator, not an accusation against the model.
        "authorship_undecided": len(doubted),
        "authorship_undecided_cases": sorted({a["case_id"] for a in doubted}),
        "cases_evaluated": len(per_case),
        "cases_planned": meta.get("n_cases"),
        # The engine's own word for why an attempt failed, straight off the
        # attempt. `failure_category` beside it is a DERIVED label, and for every
        # run recorded before 2026-09-11 it was derived by a classify() with no
        # `refused` arm, which collapsed 376 refusals into "wrong-output". The
        # reason was always right, so reporting it costs nothing and makes the
        # diagnostics of an already-paid-for run correct without re-grading.
        "failures_by_reason": dict(Counter(
            a.get("reason") or "?" for a in good if not a.get("passed"))),
        "failures_by_category": dict(Counter(
            a.get("failure_category") or "?" for a in scored if not a.get("passed"))),
        "extraction_by_kind": dict(Counter(a.get("how") or "?" for a in scored)),
        "spend_usd": round(sum(a.get("cost_usd", 0.0) for a in good)
                           + (gpu_cost if gpu_cost is not None
                              else _recorded_gpu_cost(run_dir)), 4),
        "tokens_out": sum(a.get("completion_tokens", 0) for a in good),
        "wall_s": round(sum(a.get("latency_s", 0.0) for a in good), 1),
        "per_case": {cid: sum(v) / len(v) for cid, v in sorted(per_case.items())},
        # No timestamp. A summary is a PURE FUNCTION of its attempts file, and
        # `harvest.py` already paid for the alternative: a derived file carrying
        # the moment it was written is rewritten on every read, and then shows up
        # in `git status` forever. When the run happened is in meta.json, which
        # is written once. Folding twice now produces byte-identical output.
    })
    write_json(run_dir / "summary.json", body)
    return body
