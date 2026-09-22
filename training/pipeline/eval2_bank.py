"""Assemble a schema-3 eval-2 case bank from authored proposals, verified by execution.

THREE INPUTS, ONE BANK. An authoring workflow writes *proposals* — a task text,
a family, capability labels, a reference program and claimed tests — for the
candidates that ``eval2-select`` drew, and a second agent writes an
*independent solution* for each from the task text alone. The candidate records
carry the captured program's identity (``source_sha256``), and the evidence
snapshots (``lypning.evidence``) carry the observations that identity links
to. This module joins the three and admits a case only when every rule below
holds, in this order, charging the case to the FIRST rule that fails:

``author-dropped``         the author's own ``keep`` was false
``no-candidate``           the proposal names a candidate id the draw does not have
``lint-runtime-name``      the task names the runtime or the boundary
``lint-module-constraint`` the task states a module as a constraint
``lint-short``             fewer than :data:`MIN_TASK_WORDS` words
``lint-code-fence``        the task carries a code fence
``no-family``              the family slugifies to nothing
``tests-shape``            the cheap structural rules of ``validate_cases``
``reference-fails``        the reference did not exit 0, wrote stderr, or was
                           unstable across two hash seeds, on some test
``unsolved``               no independent solution for this candidate
``solver-disagrees``       the independent solution answers differently
``native-mismatch``        the engine neither runs nor cleanly refuses every test
``no-evidence``            no observation links to the captured program
``duplicate``              a case id or task text seen earlier in this run
``invalid-case``           ``training_data.validate_cases`` rejected the case

THE ORACLE IS CPYTHON, TWICE. Every claimed stdout is replaced by what the
reference prints under :func:`pipeline.sandbox.run_python`, run once with the
sandbox's pinned hash seed and once with ``--seed``; a claimed stdout that
differs is *corrected*, not rejected, and the correction is recorded in the
report row. The engine's answer decides the population and never the stdout: a
native wrong answer is an engine bug, listed as a witness and never a case.

THE NO-RUNTIME-NAME RULE has one home in code: :data:`RUNTIME_NAMES`. It is
the pre-registration's rule that the task never names the runtime or the
boundary (``EVAL2.md`` §2), and the lint is deliberately over-broad — a
rejected ordinary task costs one proposal, a leaked runtime name costs the
bank.

This module returns data and writes only what :func:`write_outputs` is handed.
``cli.py`` renders the report.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import engines as eng
from .data_loop import REVIEW_FIELDS
from .jsonio import read_json, read_jsonl, sha256_of, write_json, write_jsonl
from .sandbox import RunResult, run_python
from .training_data import unsafe_input_path, validate_cases
from .training_types import TrainingError, VerificationBlocked

#: The day the proposals were authored; part of every case's provenance.
AUTHORED_ON = "2026-09-16"

#: A task shorter than this cannot state how input arrives and what to print.
MIN_TASK_WORDS = 20


#: The pre-registration's no-runtime-name rule, as ``(label, pattern)`` pairs
#: matched case-insensitively against the task text. Each pattern is a whole
#: word or phrase plus its obvious inflections; add here, nowhere else.
RUNTIME_NAMES: Tuple[Tuple[str, str], ...] = (
    ("lypning", r"\blypning\b"),
    ("cpython", r"\bcpython\b"),
    ("interpreter", r"\binterpreters?\b"),
    ("runtime", r"\bruntimes?\b"),
    ("engine", r"\bengines?\b"),
    ("tier", r"\btiers?\b"),
    ("refuse", r"\brefus(?:e|es|ed|al|als|ing)\b"),
    ("unsupported", r"\bunsupported\b"),
    ("fallback", r"\bfall-?backs?\b"),
    ("standard library", r"\bstandard[\s-]+librar(?:y|ies)\b"),
    ("stdlib", r"\bstdlib\b"),
    ("without importing", r"\bwithout\s+import(?:ing|s)?\b"),
    ("do not import", r"\b(?:do\s+not|don'?t|never)\s+import\b"),
    ("only use", r"\bonly\s+(?:ever\s+)?(?:use|using|import)\b"),
    ("pure python", r"\bpure[\s-]+python\b"),
    ("must not use", r"\bmust\s+not\s+(?:use|import)\b"),
)

#: ``import``/``module`` within three words of ``not``/``only``/``without``:
#: a module name stated as a constraint, whichever order the words come in.
_MODULE_CONSTRAINT = re.compile(
    r"\b(?:not|only|without)\b(?:\W+\w+){0,3}?\W+(?:import(?:s|ing|ed)?|modules?)\b"
    r"|\b(?:import(?:s|ing|ed)?|modules?)\b(?:\W+\w+){0,3}?\W+(?:not|only|without)\b",
    re.I)

_RUNTIME_PATTERNS = tuple((label, re.compile(pat, re.I)) for label, pat in RUNTIME_NAMES)

#: Every drop reason, in the order the rules run.
REASONS: Tuple[str, ...] = (
    "author-dropped", "no-candidate", "lint-runtime-name", "lint-module-constraint",
    "lint-short", "lint-code-fence", "no-family", "tests-shape", "reference-fails",
    "unsolved", "solver-disagrees", "native-mismatch", "no-evidence", "duplicate",
    "invalid-case",
)

_TEST_KEYS = frozenset(("argv", "stdin", "files", "stdout"))


# --- the lint -----------------------------------------------------------------


def lint_task(task: Any) -> Optional[Tuple[str, str]]:
    """``(reason, detail)`` if the task text is not allowed, else None."""
    if not isinstance(task, str) or not task.strip():
        return "lint-short", "task is empty"
    for label, pattern in _RUNTIME_PATTERNS:
        m = pattern.search(task)
        if m:
            return "lint-runtime-name", "%s: %r" % (label, m.group(0))
    m = _MODULE_CONSTRAINT.search(task)
    if m:
        return "lint-module-constraint", repr(m.group(0))
    words = len(task.split())
    if words < MIN_TASK_WORDS:
        return "lint-short", "%d words, need %d" % (words, MIN_TASK_WORDS)
    if "```" in task:
        return "lint-code-fence", "task contains ```"
    return None


def slugify(family: Any) -> str:
    if not isinstance(family, str):
        return ""
    return re.sub(r"[^a-z0-9]+", "-", family.lower()).strip("-")


def case_id_for(candidate_id: str, task: str) -> str:
    raw = (candidate_id + "\n" + task).encode("utf-8")
    return "e2-" + hashlib.sha256(raw).hexdigest()[:12]


# --- structural pre-checks (the cheap half of validate_cases, named) -----------


def _input_key(test: Dict[str, Any]) -> str:
    return sha256_of({"stdin": test.get("stdin", ""), "argv": test.get("argv", []),
                      "files": test.get("files", {})})


def check_tests_shape(tests: Any) -> Optional[str]:
    """Why the claimed tests cannot become a case, or None."""
    if not isinstance(tests, list) or len(tests) < 3:
        return "need at least three tests"
    inputs = set()
    for i, test in enumerate(tests):
        if not isinstance(test, dict):
            return "test %d is not an object" % i
        extra = set(test) - _TEST_KEYS
        if extra:
            return "test %d has keys outside argv/stdin/files/stdout: %s" % (i, ", ".join(sorted(extra)))
        if not isinstance(test.get("stdout", ""), str):
            return "test %d stdout must be text" % i
        if not isinstance(test.get("stdin", ""), str):
            return "test %d stdin must be text" % i
        argv = test.get("argv", [])
        if not isinstance(argv, list) or any(not isinstance(a, str) for a in argv):
            return "test %d argv must be a list of strings" % i
        files = test.get("files", {})
        if not isinstance(files, dict):
            return "test %d files must be a mapping" % i
        for name, content in files.items():
            if not isinstance(name, str) or not name or not isinstance(content, str):
                return "test %d file %r must map a path to text" % (i, name)
            # The rule itself is `training_data.unsafe_input_path` and is not
            # restated here. The copy that used to stand in its place never
            # grew that function's directory-collision clause, so a proposal
            # naming both `d` and `d/x` passed this cheap check and was charged
            # to `invalid-case` after two executions of the reference and an
            # engine consultation -- which is the wrong rule and the wrong
            # price. This module still owns the WORDING, because a dropped
            # proposal is charged to the first rule it fails by name.
            bad = unsafe_input_path(name, files)
            if bad:
                return "test %d input file path %r %s" % (i, name, bad)
        inputs.add(_input_key(test))
    if len(inputs) < 3:
        return "tests must vary inputs: %d distinct of %d" % (len(inputs), len(tests))
    return None


# --- execution ----------------------------------------------------------------


def _run(program: str, test: Dict[str, Any], *, timeout_s: float, mem_mb: int,
         interpreter: Optional[Sequence[str]] = None,
         env_extra: Optional[Dict[str, str]] = None) -> RunResult:
    r = run_python(program, argv=list(test.get("argv", [])), stdin=test.get("stdin", ""),
                   files=dict(test.get("files", {})), timeout_s=timeout_s, mem_mb=mem_mb,
                   interpreter=interpreter, env_extra=env_extra)
    if r.harness_error:
        # Ours, not the case's: never counted as a drop (sandbox.py, THE HARNESS/PROGRAM SPLIT).
        raise VerificationBlocked("harness error: " + r.harness_error)
    return r


def run_twice(program: str, test: Dict[str, Any], seed: int, *, timeout_s: float,
              mem_mb: int) -> Tuple[Optional[str], str]:
    """``(stdout, "")`` when both CPython runs agree cleanly, else ``(None, why)``.

    Run one takes the sandbox's pinned hash seed; run two takes ``seed``, so a
    program whose output follows set or dict-view order is caught here rather
    than graded by coin flip later.
    """
    first = _run(program, test, timeout_s=timeout_s, mem_mb=mem_mb)
    if not first.ok:
        return None, first.brief()
    if first.stderr.strip():
        return None, "stderr: " + first.stderr.strip().splitlines()[-1][:200]
    second = _run(program, test, timeout_s=timeout_s, mem_mb=mem_mb,
                  env_extra={"PYTHONHASHSEED": str(seed)})
    if not second.ok or second.stderr.strip():
        return None, "second run: " + second.brief()
    if second.stdout != first.stdout:
        return None, "stdout differs across hash seeds"
    if first.encoding_error or "�" in first.stdout:
        return None, "stdout is not unambiguous UTF-8"
    return first.stdout, ""


def native_verdict(result: RunResult, expected: str) -> str:
    """``native``, ``refused`` or the witness kind: ``wrong-answer``, ``bad-refusal``, ``crash``."""
    if result.ok:
        return "native" if result.stdout == expected else "wrong-answer"
    if result.exit_code == eng.REFUSAL_EXIT:
        broken = eng.check_refusal_contract(result.exit_code, result.stdout, result.stderr,
                                            engine="lypning-l")
        return "bad-refusal" if broken else "refused"
    return "crash"


# --- evidence -----------------------------------------------------------------


def load_evidence(dirs: Iterable[Any]) -> Dict[str, Any]:
    """``source_sha256 -> sorted event ids`` over every snapshot's ``events.jsonl``.

    Quarantined events are skipped: ``data_loop.review`` will not accept a link
    to one, so a case must not be built on one either.
    """
    index: Dict[str, List[str]] = {}
    snapshots: List[str] = []
    events = 0
    for d in dirs:
        path = Path(d) / "events.jsonl"
        if not path.is_file():
            raise TrainingError("evidence snapshot has no events.jsonl: %s" % d)
        snapshots.append(str(d))
        for event in read_jsonl(path):
            events += 1
            key = event.get("source_sha256")
            eid = event.get("event_id")
            if event.get("quarantine") is not None or not isinstance(key, str) or not isinstance(eid, str):
                continue
            ids = index.setdefault(key, [])
            if eid not in ids:
                ids.append(eid)
    return {"index": {k: sorted(v) for k, v in index.items()},
            "snapshots": snapshots, "events": events}


def _check_review(case: Dict[str, Any]) -> None:
    """The per-case half of ``data_loop.review``: the split-wide half needs the whole bank."""
    record = case.get("review")
    if not isinstance(record, dict) or any(
            not isinstance(record.get(k), str) or not record[k].strip() for k in REVIEW_FIELDS):
        raise TrainingError("case needs explicit review: " + ", ".join(REVIEW_FIELDS))
    links = record.get("evidence_ids")
    if record.get("origin") not in ("captured", "authored") or not isinstance(links, list) \
            or any(not isinstance(k, str) for k in links):
        raise TrainingError("review needs origin and evidence_ids")
    if len(set(links)) != len(links) or (record["origin"] == "captured" and not links):
        raise TrainingError("captured task needs unique observation links")


# --- the build ----------------------------------------------------------------


def _proposals(batches: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Flatten batch results to ``(proposals, solutions by candidate id)``."""
    if not isinstance(batches, list):
        raise TrainingError("proposals must be a JSON array of batch results")
    proposals: List[Dict[str, Any]] = []
    solutions: Dict[str, Dict[str, Any]] = {}
    for batch in batches:
        if not isinstance(batch, dict):
            raise TrainingError("each batch result must be an object")
        for p in ((batch.get("authored") or {}).get("proposals") or []):
            if isinstance(p, dict):
                proposals.append(p)
        for s in ((batch.get("solved") or {}).get("solutions") or []):
            if isinstance(s, dict) and isinstance(s.get("candidate_id"), str):
                solutions.setdefault(s["candidate_id"], s)
    return proposals, solutions


def _provenance(cand: Dict[str, Any], edit: Any) -> str:
    return ("captured program py-%s, session %s, reverse-prompted %s by an authoring agent; "
            "reference %s" % (cand.get("source_entry_id"), cand.get("session_file") or "main corpus",
                               AUTHORED_ON, edit if isinstance(edit, str) and edit else "unknown"))


def _review(evidence_ids: List[str]) -> Dict[str, Any]:
    return {
        "reviewer": "fable-authoring-agent",
        "intent_basis": "task written from the captured program's observable behaviour; "
                        "independently solved from the text by a second agent",
        "oracle_basis": "expected outputs computed by running the reference under CPython "
                        "in a sandbox, twice",
        "rights_basis": "captured by this repository's own capture harness in its own sessions",
        "independence_basis": "one case per distinct captured program; families and solution "
                              "fingerprints grouped by pipeline.training_data.split_cases",
        "origin": "captured",
        "evidence_ids": list(evidence_ids),
    }


def build(batches: Any, candidates: Iterable[Dict[str, Any]], evidence: Dict[str, Any],
          engine: str, *, seed: int = 1111, keep_disagreements: bool = False,
          timeout_s: float = 10.0, mem_mb: int = 1024) -> Dict[str, Any]:
    """Every proposal through the rules; the bank, the report, the drops, the witnesses."""
    proposals, solutions = _proposals(batches)
    by_id = {c["candidate_id"]: c for c in candidates if isinstance(c.get("candidate_id"), str)}
    ev_index = evidence.get("index", {})
    cases: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    witnesses: List[Dict[str, Any]] = []
    seen_ids, seen_tasks = set(), set()
    kept = 0

    def drop(cid: Any, reason: str, detail: str) -> None:
        dropped.append({"candidate_id": cid, "reason": reason, "detail": detail})

    for p in proposals:
        cid = p.get("candidate_id")
        if not p.get("keep"):
            drop(cid, "author-dropped", str(p.get("drop_reason") or ""))
            continue
        kept += 1
        cand = by_id.get(cid) if isinstance(cid, str) else None
        if cand is None:
            drop(cid, "no-candidate", "candidate id not in the draw")
            continue
        task = p.get("task")
        bad = lint_task(task)
        if bad:
            drop(cid, bad[0], bad[1])
            continue
        family = slugify(p.get("family"))
        if not family:
            drop(cid, "no-family", repr(p.get("family")))
            continue
        why = check_tests_shape(p.get("tests"))
        if why:
            drop(cid, "tests-shape", why)
            continue
        reference = p.get("reference")
        if not isinstance(reference, str) or not reference.strip():
            drop(cid, "reference-fails", "reference is not program text")
            continue

        # c. the reference under CPython, twice per test; its stdout is the truth.
        tests: List[Dict[str, Any]] = []
        corrected: List[int] = []
        failed = None
        for i, t in enumerate(p["tests"]):
            stdout, detail = run_twice(reference, t, seed, timeout_s=timeout_s, mem_mb=mem_mb)
            if stdout is None:
                failed = "test %d: %s" % (i, detail)
                break
            if stdout != t.get("stdout", ""):
                corrected.append(i)
            test = {"argv": list(t.get("argv", [])), "stdin": t.get("stdin", ""),
                    "files": dict(t.get("files", {})), "stdout": stdout}
            tests.append(test)
        if failed:
            drop(cid, "reference-fails", failed)
            continue
        if any(not t["stdout"] for t in tests):
            drop(cid, "tests-shape", "reference prints nothing on test %d"
                 % next(i for i, t in enumerate(tests) if not t["stdout"]))
            continue
        if len({t["stdout"] for t in tests}) < 2:
            drop(cid, "tests-shape", "computed stdouts do not distinguish a constant answer")
            continue

        # d. the independent solution, the same way.
        sol = solutions.get(cid)
        if sol is None or not isinstance(sol.get("program"), str):
            solver = "unsolved"
            solver_detail = "no independent solution"
        else:
            solver, solver_detail = "solver-agrees", ""
            for i, t in enumerate(tests):
                got, detail = run_twice(sol["program"], t, seed, timeout_s=timeout_s, mem_mb=mem_mb)
                if got != t["stdout"]:
                    solver = "solver-disagrees"
                    solver_detail = "test %d: %s" % (i, detail or "different stdout")
                    break
        if solver != "solver-agrees" and not keep_disagreements:
            drop(cid, solver, solver_detail)
            continue

        # e. the engine decides the population and nothing else.
        verdicts: List[str] = []
        for i, t in enumerate(tests):
            r = _run(reference, t, timeout_s=timeout_s, mem_mb=mem_mb, interpreter=[engine])
            v = native_verdict(r, t["stdout"])
            verdicts.append(v)
            if v not in ("native", "refused"):
                witnesses.append({"candidate_id": cid, "test": i, "kind": v,
                                  "expected": t["stdout"], "native_stdout": r.stdout,
                                  "native_stderr": r.stderr, "native_exit": r.exit_code})
        if all(v == "native" for v in verdicts):
            population = "coverage"
        elif all(v == "refused" for v in verdicts):
            population = "fallback-control"
        else:
            drop(cid, "native-mismatch", "per-test: " + ", ".join(verdicts))
            continue

        # f. assemble.
        evidence_ids = list(ev_index.get(cand.get("source_sha256") or "", []))
        if not evidence_ids:
            drop(cid, "no-evidence", "no observation with source_sha256 %s"
                 % (cand.get("source_sha256") or "?")[:16])
            continue
        capabilities: List[Any] = []
        for label in (p.get("capabilities") or []) if isinstance(p.get("capabilities"), list) else []:
            if label not in capabilities:
                capabilities.append(label)
        case_id = case_id_for(cid, task)
        if case_id in seen_ids or task.strip() in seen_tasks:
            drop(cid, "duplicate", case_id)
            continue
        case = {
            "case_id": case_id,
            "family": family,
            "source_group": cand.get("source_sha256"),
            "capabilities": capabilities,
            "task": task,
            "reference": reference,
            "provenance": _provenance(cand, p.get("reference_edit")),
            "population": population,
            "tests": tests,
            "review": _review(evidence_ids),
        }
        if solver != "solver-agrees":
            case["flags"] = [solver]
        try:
            _check_review(case)
            validate_cases([case])
        except TrainingError as exc:
            drop(cid, "invalid-case", str(exc))
            continue
        seen_ids.add(case_id)
        seen_tasks.add(task.strip())
        cases.append(case)
        rows.append({"case_id": case_id, "candidate_id": cid, "population": population,
                     "solver": solver, "stdout_corrected": corrected,
                     "evidence_ids": len(evidence_ids)})

    cases.sort(key=lambda c: c["case_id"])
    rows.sort(key=lambda r: r["case_id"])
    if cases:
        validate_cases(cases)  # a failure here is this module's bug, not a drop
    by_reason = {r: 0 for r in REASONS}
    for d in dropped:
        by_reason[d["reason"]] = by_reason.get(d["reason"], 0) + 1
    families: Dict[str, int] = {}
    caps: Dict[str, int] = {}
    pops = {"coverage": 0, "fallback-control": 0}
    for c in cases:
        families[c["family"]] = families.get(c["family"], 0) + 1
        pops[c["population"]] += 1
        for label in c["capabilities"]:
            caps[label] = caps.get(label, 0) + 1
    report = {
        "schema": 1,
        "seed": seed,
        "engine": engine,
        "engine_identity": eng.identity(),
        "keep_disagreements": keep_disagreements,
        "proposals": len(proposals),
        "kept": kept,
        "admitted": len(cases),
        "dropped_by_reason": {k: v for k, v in by_reason.items() if v},
        "corrected_stdouts": sum(len(r["stdout_corrected"]) for r in rows),
        "solver_agree": sum(r["solver"] == "solver-agrees" for r in rows),
        "solver_disagree": sum(r["solver"] == "solver-disagrees" for r in rows),
        "unsolved": sum(r["solver"] == "unsolved" for r in rows),
        "populations": pops,
        "families": families,
        "capabilities": caps,
        "evidence": {"snapshots": list(evidence.get("snapshots", [])),
                     "events": evidence.get("events", 0)},
        "mismatch_witnesses": witnesses,
        "cases": rows,
    }
    return {"cases": cases, "report": report, "dropped": dropped, "witnesses": witnesses}


def load_inputs(proposals: Any, candidates: Any, evidence_dirs: Iterable[Any]) -> Dict[str, Any]:
    return {"batches": read_json(proposals), "candidates": read_jsonl(candidates),
            "evidence": load_evidence(evidence_dirs)}


def write_outputs(result: Dict[str, Any], output: Any) -> Dict[str, str]:
    """``bank.jsonl``, ``report.json``, ``dropped.jsonl``, ``witnesses.jsonl`` into a NEW directory."""
    out = Path(output)
    if out.exists():
        raise FileExistsError("refusing to overwrite %s: a bank is frozen at its digest, "
                              "a changed bank is a new directory" % out)
    out.mkdir(parents=True)
    paths = {"bank": out / "bank.jsonl", "report": out / "report.json",
             "dropped": out / "dropped.jsonl", "witnesses": out / "witnesses.jsonl"}
    write_jsonl(paths["bank"], result["cases"])
    write_json(paths["report"], result["report"])
    write_jsonl(paths["dropped"], result["dropped"])
    write_jsonl(paths["witnesses"], result["witnesses"])
    return {k: str(v) for k, v in paths.items()}


def render(report: Dict[str, Any]) -> str:
    """The short table the CLI prints."""
    lines = ["eval2-bank  proposals %d   kept %d   admitted %d   @ engine %s"
             % (report["proposals"], report["kept"], report["admitted"],
                report["engine_identity"]["fingerprint"] or report["engine"])]
    pops = report["populations"]
    lines.append("  populations   coverage %d   fallback-control %d"
                 % (pops["coverage"], pops["fallback-control"]))
    lines.append("  solver        agree %d   disagree %d   unsolved %d   stdouts corrected %d"
                 % (report["solver_agree"], report["solver_disagree"], report["unsolved"],
                    report["corrected_stdouts"]))
    lines.append("  evidence      %d events in %d snapshot(s)"
                 % (report["evidence"]["events"], len(report["evidence"]["snapshots"])))
    lines.append("  families      %d   capabilities %d"
                 % (len(report["families"]), len(report["capabilities"])))
    if report["dropped_by_reason"]:
        lines.append("  dropped")
        for reason in REASONS:
            n = report["dropped_by_reason"].get(reason)
            if n:
                lines.append("    %-24s %5d" % (reason, n))
    if report["mismatch_witnesses"]:
        lines.append("  native mismatch witnesses (engine bugs, never cases): %d"
                     % len(report["mismatch_witnesses"]))
        for w in report["mismatch_witnesses"][:10]:
            lines.append("    %s test %d  %s  exit %s" % (w["candidate_id"], w["test"],
                                                         w["kind"], w["native_exit"]))
    return "\n".join(lines)
