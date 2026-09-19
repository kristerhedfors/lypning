"""Bank v3: tasks the model proposed, judged by execution, adapted to the subset.

THE BOUNDARY THIS MODULE SITS BEHIND. `HARVESTING.md` says the upstream key
belongs to the trusted controller and that the model-facing execution boundary
must never receive it. :mod:`pipeline.synth_generate` holds the key and runs
nothing; this module runs every model-written program and holds no key. The
JSONL of candidates between them is the only channel, and the job boundary in
`.github/workflows/bank-v3.yml` is what makes that a property rather than a
convention.

THE ORACLE, AND WHAT IT IS NOT. Each task was answered k times independently.
A task survives only when at least ``agree`` samples produce byte-identical
stdout on every input, with exit 0 and empty stderr, and the winner reproduces
that output on a second clean run under a different hash seed. That is
self-consistency across independent samples: real evidence, much stronger than
one program, and NOT independent derivation from the task. Samples share a
model and a prompt, so they share a misreading of an ambiguous task. Every case
this module emits says so in its ``review.oracle_basis``, and a report must
keep this tier separate from ``bank_v2``, whose expectations were derived from
the task by a different author than the reference.

ROUTING is `ORCHESTRATION.md`'s verified-outcome table, decided by what the
pinned engine does with the winning program and never by the label it was
generated under:

  served natively on every input     -> a ``coverage`` case
  refused, generated as ``ceiling``  -> a ``fallback-control`` case: the right
                                        answer keeps the import, and these are
                                        the counterweight that stops an arm
                                        scoring well by avoiding every import
                                        (`PREREGISTRATION.md` §2 item (g))
  refused, generated as ``rewrite``  -> the repair queue
  refused on some inputs only        -> the queue if ``rewrite``; rejected if
                                        ``ceiling``, since a control that is
                                        native somewhere is not a control
  runs natively to a DIFFERENT answer,
  crashes, or breaks the refusal
  contract                           -> a witness. Root ``CLAUDE.md`` invariant
                                        1: an engine that disagrees with
                                        CPython is a bug, never a data point

THE REPAIR IS PROVED, NOT TRUSTED. A rule from :mod:`pipeline.repair_rules`
rewrites a queued program; the rewrite is accepted only when the engine serves
it natively on every input AND it reproduces the expected output agreed before
the repair existed. A rule cannot move its own target. What no rule repairs is
written out by refusal kind: that list is a capability request for the engine,
and the useful half of the failure.

ONE RECORD SHAPE LEAVES HERE: schema-3, the shape `training-prepare`,
`data_loop` and `eval2-leaks` already read (:func:`to_case`). Before this
module the loop ended at a private dataset repo in a shape nothing downstream
could load. Emitting schema-3 admits nothing by itself — review, preparation
and every floor in `training-bundle` still stand between a batch and a round —
it only makes the batch a candidate for them.

Library code: everything returns data; ``cli.py`` renders and maps exit codes.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import engines as eng
from .jsonio import digest, write_json, write_jsonl
from .repair_rules import RULES
from .sandbox import RunResult, run_python
from .training_data import unsafe_input_path, validate_cases
from .training_types import TrainingError, VerificationBlocked

#: The two populations a candidate is generated toward. ``rewrite`` means a
#: native equivalent is believed to exist; ``ceiling`` means the right answer
#: keeps the import. The engine, not the label, decides what a row becomes.
STRATA = ("rewrite", "ceiling")

#: The keys a candidate input may carry. Same contract as a schema-3 test
#: minus the answer; the answer is what the oracle supplies.
INPUT_KEYS = ("stdin", "argv", "files")

#: What each admitted kind is, in the vocabulary `training_data` admits.
POPULATION_OF = {"native": "coverage", "repaired": "coverage", "ceiling": "fallback-control"}

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MEM_MB = 1024
#: The hash seed of the winner's second run. The sandbox pins the first.
DEFAULT_STABILITY_SEED = 1111

_RUN_ON = "%s, on input %d"


# --- the candidate ------------------------------------------------------------


def validate_candidate(row: Any) -> Optional[str]:
    """Why this generated row cannot be judged, or None."""
    if not isinstance(row, dict):
        return "not an object"
    if not isinstance(row.get("task"), str) or not row["task"].strip():
        return "task must be non-empty text"
    if row.get("stratum") not in STRATA:
        return "stratum must be one of %s" % ", ".join(STRATA)
    for key in ("model", "target_construct"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            return "%s must be non-empty text" % key
    programs = row.get("programs")
    if (not isinstance(programs, list) or len(programs) < 2
            or any(not isinstance(p, str) or not p.strip() for p in programs)):
        return "programs must be at least two non-empty sources"
    inputs = row.get("inputs")
    if not isinstance(inputs, list) or len(inputs) < 3:
        return "need at least three inputs"
    for i, spec in enumerate(inputs):
        if not isinstance(spec, dict) or set(spec) - set(INPUT_KEYS) or not spec:
            return "input %d must carry only %s" % (i, "/".join(INPUT_KEYS))
        if "stdin" in spec and not isinstance(spec["stdin"], str):
            return "input %d stdin must be text" % i
        argv = spec.get("argv", [])
        if not isinstance(argv, list) or any(not isinstance(a, str) for a in argv):
            return "input %d argv must be a list of strings" % i
        files = spec.get("files", {})
        if not isinstance(files, dict) or any(
                not isinstance(k, str) or not isinstance(v, str) for k, v in files.items()):
            return "input %d files must map names to text" % i
        # THE ROW THAT COSTS THE BATCH. `sandbox.materialize` reports a file
        # name that leaves the working directory as a *harness* error, and the
        # harness/program split makes a harness error abort the run: one
        # candidate asking for `/data/logs.txt` threw away 2,078 already-judged
        # candidates in run 35399909232 (adapt job 105802008535, 2026-09-19).
        # It is the model's error, not ours, so it is caught here — before
        # anything is executed — and costs one rejected row. The rule is
        # `training_data.unsafe_input_path`, the same one the schema-3
        # validator applies afterwards; checking it early also spares the
        # execution of a candidate that could never be admitted.
        for name in files:
            bad = unsafe_input_path(name, files)
            if bad:
                return "input %d file %r %s" % (i, name, bad)
        # The same loss, one layer lower. A NUL inside an argv element or a
        # file name never reaches a verdict at all: `subprocess.Popen` and
        # `Path.mkdir` raise `ValueError`, which is neither a harness error nor
        # a program result, so it leaves `run` as a traceback and takes the
        # batch with it. Three of run 35340137976's 2,784 candidates carry one,
        # at rows 28, 31 and 1712 -- row 461 of that artifact is the escaping
        # name above and not this (`bank-v3-candidates` scanned 2026-09-19).
        # Content may hold a NUL -- bytes on a pipe and bytes in a file both
        # arrive intact, and `validate_cases` admits them.
        for value in list(argv) + list(files):
            if "\0" in value:
                return "input %d argv and file names cannot contain NUL" % i
    return None


def tests_of(inputs: Sequence[Dict[str, Any]], expected: Sequence[str]) -> List[Dict[str, Any]]:
    """The schema-3 tests: each input joined to the stdout the oracle agreed on."""
    tests = []
    for spec, out in zip(inputs, expected):
        test: Dict[str, Any] = {}
        for key in INPUT_KEYS:
            if key in spec:
                test[key] = spec[key]
        test["stdout"] = out
        tests.append(test)
    return tests


# --- execution ----------------------------------------------------------------


class Runner:
    """Both interpreters, through the one net every other verifier uses.

    ``sandbox.run_python`` scrubs the environment, kills the process group on
    timeout, caps memory and output, and isolates the network where the kernel
    allows. The three scripts this replaced ran model-written code through a
    bare ``subprocess.run`` with a timeout and nothing else. A harness failure
    raises :class:`VerificationBlocked` — ours, never the program's.
    """

    def __init__(self, engine: str, *, timeout_s: float = DEFAULT_TIMEOUT_S,
                 mem_mb: int = DEFAULT_MEM_MB, seed: int = DEFAULT_STABILITY_SEED,
                 runner: Optional[Callable[..., RunResult]] = None) -> None:
        self.engine = str(engine)
        self.timeout_s = float(timeout_s)
        self.mem_mb = int(mem_mb)
        self.seed = int(seed)
        self._run = runner or run_python

    def _exec(self, program: str, test: Dict[str, Any], *, native: bool = False,
              hash_seed: Optional[int] = None) -> RunResult:
        env_extra = {"PYTHONHASHSEED": str(hash_seed)} if hash_seed is not None else None
        result = self._run(program, argv=list(test.get("argv", [])), stdin=test.get("stdin", ""),
                           files=dict(test.get("files", {})), timeout_s=self.timeout_s,
                           mem_mb=self.mem_mb, interpreter=[self.engine] if native else None,
                           env_extra=env_extra)
        if result.harness_error:
            raise VerificationBlocked("harness: " + result.harness_error)
        return result

    @staticmethod
    def _clean(result: RunResult) -> bool:
        return (result.ok and not result.stderr.strip() and not result.truncated
                and not result.memory_exceeded and not result.encoding_error)

    def outputs(self, program: str, inputs: Sequence[Dict[str, Any]],
                hash_seed: Optional[int] = None) -> Tuple[Optional[List[str]], str]:
        """Every input's stdout under CPython, or ``(None, why)`` at the first failure."""
        got: List[str] = []
        for i, spec in enumerate(inputs):
            result = self._exec(program, spec, hash_seed=hash_seed)
            if not self._clean(result):
                return None, _RUN_ON % (result.brief(), i)
            got.append(result.stdout)
        return got, ""

    def engine_verdict(self, program: str, tests: Sequence[Dict[str, Any]]) -> Tuple[str, List[str]]:
        """What the pinned engine does with a program CPython already ran clean.

        ``native`` with no kinds, ``refused`` with the sorted refusal buckets,
        ``mixed`` (refused on some inputs, served on others) with the buckets,
        or a witness kind — ``bad-refusal``, ``crash``, ``mismatch`` — with the
        one detail that names the input it happened on.
        """
        buckets, served = set(), 0
        for i, test in enumerate(tests):
            result = self._exec(program, test, native=True)
            if result.exit_code == eng.REFUSAL_EXIT:
                broken = eng.check_refusal_contract(result.exit_code, result.stdout, result.stderr)
                if broken:
                    return "bad-refusal", [_RUN_ON % (broken, i)]
                buckets.add(eng.refusal_bucket(result.stderr))
                continue
            if not self._clean(result):
                return "crash", [_RUN_ON % (result.brief(), i)]
            if result.stdout != test["stdout"]:
                return "mismatch", [_RUN_ON % ("engine stdout differs from CPython", i)]
            served += 1
        if not buckets:
            return "native", []
        return ("refused" if not served else "mixed"), sorted(buckets)

    def verify(self, program: str, tests: Sequence[Dict[str, Any]]) -> Tuple[bool, str]:
        """Native on every input AND byte-identical to the pre-agreed stdout."""
        for i, test in enumerate(tests):
            oracle = self._exec(program, test)
            if not self._clean(oracle) or oracle.stdout != test["stdout"]:
                return False, "cpython-mismatch"
            native = self._exec(program, test, native=True)
            if native.exit_code == eng.REFUSAL_EXIT:
                return False, "still-refused"
            if not self._clean(native):
                return False, "engine-crash"
            if native.stdout != test["stdout"]:
                return False, "engine-mismatch"
        return True, ""


# --- the oracle and the routing ----------------------------------------------


def judge(row: Dict[str, Any], runner: Runner, *, agree: int = 2) -> Dict[str, Any]:
    """One candidate through the oracle and the engine: ``{"kind", "row" | "why"}``.

    ``kind`` is ``native``, ``ceiling`` or ``repair`` with the judged ``row``;
    ``rejected`` or ``witness`` with a ``why``. A rejected row is an
    observation and never a training row; a witness is an engine bug.
    """
    bad = validate_candidate(row)
    if bad:
        return {"kind": "rejected", "why": "malformed: " + bad, "row": row}
    inputs, programs = row["inputs"], row["programs"]
    usable = []
    for program in programs:
        got, _ = runner.outputs(program, inputs)
        if got is not None:
            usable.append((program, got))
    if len(usable) < agree:
        return {"kind": "rejected", "row": row,
                "why": "no-quorum: %d of %d samples ran clean, %d needed" % (len(usable), len(programs), agree)}
    votes: Counter = Counter(tuple(o) for _, o in usable)
    best, count = votes.most_common(1)[0]
    if count < agree:
        # Samples ran but disagreed: the task is ambiguous or the model is
        # guessing. Either way there is no expected output to keep.
        return {"kind": "rejected", "row": row,
                "why": "disagreed: the largest agreement is %d of %d clean samples" % (count, len(usable))}
    winner = next(p for p, o in usable if tuple(o) == best)
    again, why = runner.outputs(winner, inputs, hash_seed=runner.seed)
    if again is None or tuple(again) != best:
        return {"kind": "rejected", "row": row,
                "why": "unstable: " + (why or "stdout moved between two clean runs")}
    if all(not s.strip() for s in best):
        return {"kind": "rejected", "row": row, "why": "blank-output: every expected output is blank"}
    if len(set(best)) < 2:
        # Tests that agree on one answer cannot fail a wrong program;
        # `training_data.validate_cases` refuses the case later anyway.
        return {"kind": "rejected", "row": row, "why": "constant-output: every input prints the same thing"}
    tests = tests_of(inputs, best)
    judged = {"task": row["task"], "tests": tests, "program": winner,
              "agreement": count, "samples": len(programs), "model": row["model"],
              "stratum": row["stratum"], "target_construct": row["target_construct"],
              "domain": row.get("domain", ""), "generated_on": row.get("generated_on", "")}
    verdict, kinds = runner.engine_verdict(winner, tests)
    if verdict == "native":
        return {"kind": "native", "row": judged}
    if verdict in ("refused", "mixed"):
        judged["refusals"] = kinds
        if row["stratum"] == "rewrite":
            return {"kind": "repair", "row": judged}
        if verdict == "refused":
            return {"kind": "ceiling", "row": judged}
        return {"kind": "rejected", "row": judged,
                "why": "partly-native: a control must refuse on every input, this one was served on some"}
    return {"kind": "witness", "row": judged, "why": "%s: %s" % (verdict, kinds[0] if kinds else "")}


def repair(row: Dict[str, Any], runner: Runner,
           rules: Sequence[Tuple[str, Callable[[str], Optional[str]]]] = RULES) -> Dict[str, Any]:
    """The first rule whose rewrite verifies, or the row unrepaired.

    Returns ``{"row", "repaired": bool, "fired": [...], "rejected": {rule: reason}}``.
    An ``engine-mismatch`` reason is also a witness: CPython accepted the
    rewrite and the engine ran it to a different answer.
    """
    fired: List[str] = []
    rejected: Dict[str, str] = {}
    for name, rule in rules:
        candidate = rule(row["program"])
        if candidate is None or candidate == row["program"]:
            continue
        fired.append(name)
        ok, reason = runner.verify(candidate, row["tests"])
        if not ok:
            rejected[name] = reason
            continue
        out = dict(row, program=candidate, original=row["program"], repair_rule=name)
        return {"row": out, "repaired": True, "fired": fired, "rejected": rejected}
    return {"row": row, "repaired": False, "fired": fired, "rejected": rejected}


# --- schema-3 ---------------------------------------------------------------


def family_of(construct: str) -> str:
    """One family per target construct: the tasks proposed for it are near-twins."""
    return re.sub(r"[^a-z0-9]+", "-", construct.lower()).strip("-")[:48] or "unnamed"


def capability_of(construct: str) -> str:
    return construct.split(".")[0].split()[0].lower()


def case_id_for(task: str, tests: Sequence[Dict[str, Any]]) -> str:
    return "v3-" + digest({"task": task, "tests": list(tests)}, 12)


def to_case(row: Dict[str, Any], *, kind: str, batch: str) -> Dict[str, Any]:
    """A judged row as a schema-3 case, with its evidence tier written on it.

    ``reference`` is the program the engine serves (the repaired one for a
    ``repaired`` row); the refused original and the rule that rewrote it ride
    along under ``synth`` so a later preference pair can be built from the
    same row without re-running anything. The ``review`` block states what
    actually happened: no reviewer, a self-consistency oracle, a model under
    the operator's account. It is not softened; ledger row T6 flags this tier.
    """
    population = POPULATION_OF[kind]
    tests = row["tests"]
    when = row.get("generated_on") or "date not recorded"
    provenance = ("bank v3: proposed and answered by %s on Cerebras, %s; batch %s; generated "
                  "toward the %s stratum (%s); %d of %d samples agreed; %s"
                  % (row["model"], when, batch, row["stratum"], row["target_construct"],
                     row["agreement"], row["samples"],
                     {"native": "served natively as written",
                      "repaired": "refused as written, repaired by rule %s" % row.get("repair_rule"),
                      "ceiling": "refused as written and kept as a control"}[kind]))
    synth: Dict[str, Any] = {"tier": "self-consistency", "kind": kind, "batch": batch,
                             "model": row["model"], "stratum": row["stratum"],
                             "target_construct": row["target_construct"],
                             "agreement": row["agreement"], "samples": row["samples"]}
    for key in ("refusals", "original", "repair_rule", "domain"):
        if row.get(key):
            synth[key] = row[key]
    return {
        "case_id": case_id_for(row["task"], tests),
        "family": family_of(row["target_construct"]),
        "source_group": family_of(row["target_construct"]),
        "capabilities": [capability_of(row["target_construct"])],
        "task": row["task"],
        "reference": row["program"],
        "tests": tests,
        "population": population,
        "provenance": provenance,
        "review": {
            "origin": "authored",
            "evidence_ids": [],
            "reviewer": "none: proposed and answered by %s, admitted by the self-consistency "
                        "oracle in pipeline.synth (a program, not a human reviewer)" % row["model"],
            "intent_basis": "the task text as the model wrote it; no human read it",
            "oracle_basis": "self-consistency: %d of %d independent samples produced byte-identical "
                            "stdout on every input and the winner reproduced it under a second hash "
                            "seed; NOT independent derivation from the task, so a misreading shared "
                            "by every sample survives" % (row["agreement"], row["samples"]),
            "rights_basis": "generated by %s under the operator's Cerebras account; no captured, "
                            "third-party or licensed material" % row["model"],
            "independence_basis": "one family per target construct: tasks proposed for one construct "
                                  "are near-twins and must never straddle a split",
        },
        "synth": synth,
    }


# --- the whole batch ----------------------------------------------------------


def rule_verdicts(fired: "Counter") -> Dict[str, str]:
    """The `levers` bucket of every module a rule rewrote this batch.

    A repair teaches the model to route around a refusal, so the refusal's
    verdict belongs beside the count: ``engine-addressable`` means the pair is
    a stopgap the engine roadmap will retire; anything else means a rule is
    rewriting a module nobody has classified as deliberate or not. Derived
    here and never written on the case -- the case carries ``repair_rule``,
    and the verdict has one home.
    """
    from . import levers

    stdlib = levers.stdlib_names()
    return {name: levers.classify("module", "import " + name, stdlib=stdlib)["bucket"]
            for name in sorted(fired)}


def run(candidates: Sequence[Dict[str, Any]], runner: Runner, *, agree: int = 2,
        batch: str = "local",
        rules: Sequence[Tuple[str, Callable[[str], Optional[str]]]] = RULES) -> Dict[str, Any]:
    """Judge every candidate, repair the queue, project what survives to schema-3.

    Returns ``cases`` (schema-3), ``unrepaired`` (the capability request),
    ``rejected`` (observations, never training rows), ``witnesses`` (engine
    bugs) and a ``report`` of counts. Nothing is written.
    """
    cases: List[Dict[str, Any]] = []
    unrepaired: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    witnesses: List[Dict[str, Any]] = []
    tally: Counter = Counter()
    fired: Counter = Counter()
    accepted: Counter = Counter()
    verification: Counter = Counter()
    seen_tasks = set()

    def admit(row: Dict[str, Any], kind: str) -> None:
        case = to_case(row, kind=kind, batch=batch)
        try:
            validate_cases([case])
        except TrainingError as exc:
            tally["invalid-case"] += 1
            rejected.append({"why": "invalid-case: %s" % exc, "row": row})
            return
        tally[kind] += 1
        cases.append(case)

    for row in candidates:
        task = row.get("task") if isinstance(row, dict) else None
        if task in seen_tasks:
            tally["duplicate"] += 1
            rejected.append({"why": "duplicate: task text seen earlier in this batch", "row": row})
            continue
        if isinstance(task, str):
            seen_tasks.add(task)
        verdict = judge(row, runner, agree=agree)
        kind = verdict["kind"]
        if kind == "rejected":
            tally["rejected:" + verdict["why"].split(":")[0]] += 1
            rejected.append({"why": verdict["why"], "row": verdict["row"]})
        elif kind == "witness":
            tally["witness"] += 1
            witnesses.append({"why": verdict["why"], "row": verdict["row"]})
        elif kind == "repair":
            tally["queued"] += 1
            outcome = repair(verdict["row"], runner, rules)
            for name in outcome["fired"]:
                fired[name] += 1
            for name, reason in outcome["rejected"].items():
                verification["%s:%s" % (name, reason)] += 1
                if reason == "engine-mismatch":
                    witnesses.append({"why": "engine-mismatch: rule %s produced a program CPython "
                                             "accepts and the engine answers differently" % name,
                                      "row": verdict["row"]})
            if outcome["repaired"]:
                accepted[outcome["row"]["repair_rule"]] += 1
                admit(outcome["row"], "repaired")
            else:
                tally["unrepaired"] += 1
                unrepaired.append(verdict["row"])
        else:
            admit(verdict["row"], kind)

    unserved: Counter = Counter(k for r in unrepaired for k in r.get("refusals", []))
    report = {
        "batch": batch, "candidates": len(candidates), "agree": agree,
        "cases": len(cases),
        "populations": dict(Counter(c["population"] for c in cases)),
        "families": len({c["family"] for c in cases}),
        "unrepaired": len(unrepaired), "rejected": len(rejected), "witnesses": len(witnesses),
        "tally": dict(sorted(tally.items())),
        "rules_fired": dict(sorted(fired.items())),
        "rules_accepted": dict(sorted(accepted.items())),
        "rule_verdicts": rule_verdicts(fired),
        "rejected_by_verification": dict(sorted(verification.items())),
        # The capability request, and the useful half of a failure.
        "unserved_kinds": unserved.most_common(20),
    }
    return {"cases": cases, "unrepaired": unrepaired, "rejected": rejected,
            "witnesses": witnesses, "report": report}


def write_outputs(result: Dict[str, Any], output: Any) -> Dict[str, str]:
    """``cases.jsonl``, ``unrepaired.jsonl``, ``rejected.jsonl``, ``witnesses.jsonl``, ``report.json``."""
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name in ("cases", "unrepaired", "rejected", "witnesses"):
        path = out / (name + ".jsonl")
        write_jsonl(path, result[name])
        paths[name] = str(path)
    write_json(out / "report.json", result["report"])
    paths["report"] = str(out / "report.json")
    return paths


def render(report: Dict[str, Any]) -> str:
    lines = [
        "synth-adapt  batch %s   %d candidate(s), agree >= %d"
        % (report["batch"], report["candidates"], report["agree"]),
        "  cases %d   %s   over %d family(ies)"
        % (report["cases"],
           "  ".join("%s %d" % kv for kv in sorted(report["populations"].items())) or "none",
           report["families"]),
        "  unrepaired %d   rejected %d   witnesses %d"
        % (report["unrepaired"], report["rejected"], report["witnesses"]),
    ]
    if report["tally"]:
        lines.append("  tally: " + ", ".join("%s %d" % kv for kv in report["tally"].items()))
    if report["rules_fired"]:
        lines.append("  rules fired: " + ", ".join(
            "%s %d/%d" % (name, report["rules_accepted"].get(name, 0), n)
            for name, n in report["rules_fired"].items()) + "  (accepted/fired)")
    if report.get("rule_verdicts"):
        lines.append("  rule verdicts: " + ", ".join(
            "%s %s" % kv for kv in report["rule_verdicts"].items()))
        loose = [n for n, b in report["rule_verdicts"].items() if b != "engine-addressable"]
        if loose:
            lines.append("  RULE WITHOUT AN ENGINE-ADDRESSABLE VERDICT: %s -- a repair teaches around "
                         "a refusal; the refusal needs a bucket before the pair needs a model"
                         % ", ".join(loose))
    if report["rejected_by_verification"]:
        lines.append("  rejected by verification: " + ", ".join(
            "%s %d" % kv for kv in report["rejected_by_verification"].items()))
    if report["unserved_kinds"]:
        lines.append("  unserved kinds (the capability request):")
        for kind, n in report["unserved_kinds"]:
            lines.append("    %5d  %s" % (n, kind))
    if report["witnesses"]:
        lines.append("  WITNESSES %d — invariant 1: an engine that disagrees with CPython is a bug, "
                     "never a data point" % report["witnesses"])
    return "\n".join(lines)
