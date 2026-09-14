"""Correctness-gated, multi-input training for lypning-l (stdlib only).

This is a new task-first experiment, not a regrade of the frozen rewrite corpus.
The observable contract is deterministic UTF-8 stdout, empty stderr, exit zero.
It does not certify file effects, security, performance, or arbitrary Python
semantics. sandbox.run_python limits accidents; it is NOT a security boundary.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from . import sandbox
from .jsonio import sha256_of, write_json, write_jsonl

SCHEMA = 2
POLICY = "l-correctness-v1"
SYSTEM = "Write a Python standard-library program. Return exactly one fenced python code block."


class TrainingError(ValueError):
    """Invalid experiment, not a bad model answer."""


class VerificationBlocked(TrainingError):
    """An oracle, harness, or engine failure must not become an RL reward."""


@dataclass(frozen=True)
class Score:
    reward: float
    status: str
    native_tests: int = 0
    total_tests: int = 0


def engine_identity(binary):
    """Require the actual large native engine, never discovery or a dispatcher."""
    path = Path(binary).resolve(strict=True)
    magic = path.read_bytes()[:4]
    if magic not in (b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
                     b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
        raise TrainingError("--engine must be a compiled lypning-l binary")
    result = subprocess.run([str(path), "--version"], capture_output=True, text=True,
                            timeout=10, check=True)
    version = result.stdout.strip()
    if not re.fullmatch(r"lypning \S+ \(lypning-l\) for cpython \d+\.\d+", version):
        raise TrainingError("wrong engine identity: " + version)
    py = "%d.%d" % sys.version_info[:2]
    if not version.endswith("cpython " + py):
        raise TrainingError("engine/CPython minor version mismatch: " + version)
    return {"engine": "lypning-l", "version": version,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "oracle": sys.version, "policy": POLICY,
            "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "sandbox_sha256": hashlib.sha256(Path(sandbox.__file__).read_bytes()).hexdigest(),
            "child_exec_sha256": hashlib.sha256(Path(sandbox.__file__).with_name("child_exec.py").read_bytes()).hexdigest()}


def messages(case):
    # No reference, expected output, engine hints, or refusal feedback in prompts.
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": case["task"]}]


def validate_cases(cases):
    if not isinstance(cases, list) or not cases:
        raise TrainingError("no training cases")
    ids, prompts = set(), set()
    for case in cases:
        if not isinstance(case, dict):
            raise TrainingError("each case must be an object")
        for key in ("case_id", "family", "task", "reference", "provenance"):
            if not isinstance(case.get(key), str) or not case[key].strip():
                raise TrainingError("case needs nonempty " + key)
        if case["case_id"] in ids or case["task"].strip() in prompts:
            raise TrainingError("duplicate case id or prompt")
        ids.add(case["case_id"])
        prompts.add(case["task"].strip())
        if case.get("population") not in ("coverage", "fallback-control"):
            raise TrainingError("population must be coverage or fallback-control")
        tests = case.get("tests", [])
        if not isinstance(tests, list) or len(tests) < 3:
            raise TrainingError("need at least three independently specified tests")
        inputs = set()
        for test in tests:
            if not isinstance(test, dict):
                raise TrainingError("each test must be an object")
            if set(test) - {"stdin", "argv", "files", "stdout"}:
                raise TrainingError("unsupported observable contract")
            if not isinstance(test.get("stdout"), str) or "\ufffd" in test["stdout"]:
                raise TrainingError("expected stdout must be unambiguous UTF-8 text")
            if not isinstance(test.get("stdin", ""), str):
                raise TrainingError("stdin must be text")
            if not isinstance(test.get("argv", []), list) or not all(
                    isinstance(x, str) for x in test.get("argv", [])):
                raise TrainingError("argv must be a list of strings")
            if not isinstance(test.get("files", {}), dict):
                raise TrainingError("files must be a mapping")
            for name, content in test.get("files", {}).items():
                if (not isinstance(name, str) or not name or Path(name).is_absolute()
                        or ".." in Path(name).parts or name == "solution.py"):
                    raise TrainingError("unsafe or reserved input file path")
                if not isinstance(content, str):
                    raise TrainingError("this verifier accepts UTF-8 input files only")
            inputs.add(sha256_of({k: test.get(k, d) for k, d in
                                 (("stdin", ""), ("argv", []), ("files", {}))}))
        if len(inputs) < 3 or len({t["stdout"] for t in tests}) < 2:
            raise TrainingError("tests must vary inputs and distinguish constant-output answers")


def split_cases(cases, seed=1111):
    """Split semantic families BEFORE any sampling; IDs are not independence."""
    families = sorted({c["family"] for c in cases},
                      key=lambda f: sha256_of([seed, f]))
    if len(families) < 6:
        raise TrainingError("need at least six semantic families for train/dev/test")
    n = max(1, len(families) // 6)
    assignment = {f: "test" if i < n else "dev" if i < 2 * n else "train"
                  for i, f in enumerate(families)}
    return [dict(c, split=assignment[c["family"]]) for c in cases]


def program_from_completion(completion):
    if isinstance(completion, list):
        if len(completion) != 1 or not isinstance(completion[0], dict) or completion[0].get("role") != "assistant":
            return None
        completion = completion[0].get("content", "")
    if not isinstance(completion, str):
        return None
    # Fail closed on prose, ambiguous multiple blocks, unclosed/truncated blocks.
    match = re.fullmatch(r"\s*```python\s*\n(.*?)\n```\s*", completion, re.DOTALL)
    if not match or "```" in match.group(1):
        return None
    return match.group(1)


class Verifier:
    def __init__(self, binary, timeout_s=5.0, memory_mb=1024, runner=None, identity=None):
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise TrainingError("timeout must be positive")
        self.binary = str(Path(binary).resolve())
        self.timeout_s = timeout_s
        if not isinstance(memory_mb, int) or memory_mb < 0:
            raise TrainingError("memory limit cannot be negative")
        self.memory_mb = memory_mb
        self.runner = runner or sandbox.run_python
        self.identity = identity

    def _run(self, program, test, native=False):
        try:
            r = self.runner(program, argv=test.get("argv"), stdin=test.get("stdin"),
                            files=test.get("files"), timeout_s=self.timeout_s,
                            mem_mb=self.memory_mb,
                            interpreter=[self.binary] if native else None)
        except (OSError, subprocess.SubprocessError) as exc:
            raise VerificationBlocked("runner failed: " + str(exc)) from exc
        if r.harness_error:
            raise VerificationBlocked("harness: " + r.harness_error)
        return r

    @staticmethod
    def _observed(r):
        return (r.exit_code, r.stdout, r.stderr, r.timed_out, r.truncated, r.memory_exceeded)

    def score(self, case, program):
        if self.identity is not None and engine_identity(self.binary) != self.identity:
            raise VerificationBlocked("engine/oracle/verifier drift during run")
        if not program or not program.strip():
            return Score(0.0, "no-code")
        tests = case["tests"]
        # Correctness gates the ENTIRE program before native coverage is scored.
        for i, test in enumerate(tests):
            oracle = self._run(program, test)
            if (not oracle.ok or oracle.truncated or oracle.stderr or
                    oracle.stdout != test["stdout"]):
                return Score(0.0, "incorrect", total_tests=len(tests))
            # Only a would-be SUCCESS needs a stability check. Tracebacks name
            # the fresh temporary script path: comparing two failing runs byte
            # for byte would abort RL on every ordinary model NameError.
            repeat = self._run(program, test)
            if self._observed(oracle) != self._observed(repeat):
                raise VerificationBlocked("unstable oracle: %s test %d" % (case["case_id"], i))
        native_count = 0
        for i, test in enumerate(tests):
            native = self._run(program, test, native=True)
            if native.exit_code == 90:
                if (native.stdout or native.timed_out or native.truncated or not re.fullmatch(
                        r"lypning-l: unsupported: [^:\n]+: [^\n]+\n?", native.stderr)):
                    raise VerificationBlocked("refusal protocol: %s test %d" % (case["case_id"], i))
                continue
            # Native timeout/exception/wrong output after a correct oracle is an
            # engine issue, not permission to teach the model to avoid a feature.
            if (not native.ok or native.truncated or native.stderr or
                    native.stdout != test["stdout"]):
                raise VerificationBlocked("engine mismatch: " + json.dumps({
                    "case_id": case["case_id"], "test": i, "expected_stdout": test["stdout"],
                    "observed": self._observed(native)}, ensure_ascii=False))
            native_count += 1
        if case["population"] == "fallback-control":
            return Score(1.0, "correct-control", native_count, len(tests))
        if native_count == len(tests):
            return Score(1.0, "correct-native", native_count, len(tests))
        return Score(0.25, "correct-fallback", native_count, len(tests))


def validate_pilot(cases):
    """An admission floor, not a statistical guarantee of benchmark quality."""
    if len({c["family"] for c in cases}) < 18:
        raise TrainingError("pilot needs at least 18 independent semantic families; starter is smoke-only")
    for split in ("train", "dev", "test"):
        if {c["population"] for c in cases if c["split"] == split} != {"coverage", "fallback-control"}:
            raise TrainingError("pilot split %s needs coverage AND fallback controls" % split)


def prepare(cases, binary, output, seed=1111, timeout_s=5.0, memory_mb=1024, purpose="smoke"):
    """Verify references then publish a new immutable experiment directory."""
    output = Path(output)
    if output.exists():
        raise TrainingError("output already exists; use a new experiment directory")
    validate_cases(cases)
    identity = engine_identity(binary)
    cases = split_cases(cases, seed)
    if purpose not in ("smoke", "pilot"):
        raise TrainingError("purpose must be smoke or pilot")
    if purpose == "pilot":
        validate_pilot(cases)
    verifier = Verifier(binary, timeout_s=timeout_s, memory_mb=memory_mb, identity=identity)
    references = {c["case_id"]: asdict(verifier.score(c, c["reference"])) for c in cases}
    if any(s["reward"] == 0 for s in references.values()):
        raise TrainingError("reference fails independently specified tests: " + json.dumps(references))
    # The engine may have been rebuilt while validating the inputs.
    if engine_identity(binary) != identity:
        raise TrainingError("engine changed during preparation")
    payload = {"cases": cases, "identity": identity, "seed": seed,
               "schema": SCHEMA, "system": SYSTEM, "reference_scores": references,
               "purpose": purpose, "memory_policy": sandbox.memory_policy(memory_mb),
               "limits": {"timeout_s": timeout_s, "memory_mb": memory_mb}}
    payload["digest"] = sha256_of(payload)
    # Publish the manifest LAST: interruption leaves a non-loadable incomplete
    # directory, never a manifest claiming all exports are complete.
    output.mkdir(parents=True, exist_ok=False)
    for split in ("train", "dev", "test"):
        subset = [c for c in cases if c["split"] == split]
        write_jsonl(output / (split + "-prompts.jsonl"),
                    ({"case_id": c["case_id"], "prompt": messages(c)} for c in subset))
        # Never export test solutions as SFT rows.
        if split != "test":
            write_jsonl(output / (split + "-sft.jsonl"),
                        ({"case_id": c["case_id"], "messages": messages(c) + [{
                            "role": "assistant", "content": "```python\n" + c["reference"].rstrip() + "\n```"}]}
                         for c in subset))
    write_json(output / "bundle.json", payload)
    return payload


def load_bundle(path, binary):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    digest = payload.pop("digest", None)
    if sha256_of(payload) != digest:
        raise TrainingError("bundle integrity check failed")
    if payload["schema"] != SCHEMA or payload["system"] != SYSTEM:
        raise TrainingError("bundle schema/prompt changed; prepare a new experiment")
    if payload["identity"] != engine_identity(binary):
        raise TrainingError("engine/oracle/policy drift; prepare a new experiment")
    validate_cases(payload["cases"])
    if payload.get("purpose") not in ("smoke", "pilot"):
        raise TrainingError("missing experiment purpose")
    if payload["purpose"] == "pilot":
        validate_pilot(payload["cases"])
    if payload["memory_policy"] != sandbox.memory_policy(payload["limits"]["memory_mb"]):
        raise TrainingError("memory enforcement policy changed")
    if split_cases(payload["cases"], payload["seed"]) != payload["cases"]:
        raise TrainingError("family split changed")
    payload["digest"] = digest
    return payload


class Reward:
    """TRL callable: only train IDs are addressable; infrastructure errors abort."""
    __name__ = "verified_lypning_l"

    def __init__(self, cases, verifier, witness_path=None, eos_token_id=None, rollout_path=None):
        self.cases = {c["case_id"]: c for c in cases if c["split"] == "train"}
        self.verifier = verifier
        self.witness_path = witness_path
        self.eos_token_id = eos_token_id
        self.rollout_path = rollout_path

    def __call__(self, completions, case_id, **kwargs):
        if len(completions) != len(case_id):
            raise TrainingError("completion/case_id batch length mismatch")
        token_ids = kwargs.get("completion_ids")
        if self.eos_token_id is not None and (token_ids is None or len(token_ids) != len(completions)):
            raise TrainingError("TRL completion token IDs required for truncation check")
        rewards, scores = [], []
        for i, (completion, cid) in enumerate(zip(completions, case_id)):
            if cid not in self.cases:
                raise TrainingError("non-training case in RL batch: " + cid)
            program = program_from_completion(completion)
            if self.eos_token_id is not None and (not token_ids[i] or token_ids[i][-1] != self.eos_token_id):
                program = None
            try:
                score = self.verifier.score(self.cases[cid], program)
            except VerificationBlocked as exc:
                if self.witness_path:
                    from .jsonio import append_jsonl
                    append_jsonl(self.witness_path, {"case_id": cid, "program": program,
                                                     "error": str(exc), "tests": self.cases[cid]["tests"]})
                raise
            rewards.append(score.reward)
            scores.append(score)
            if self.rollout_path:
                from .jsonio import append_jsonl
                append_jsonl(self.rollout_path, {"case_id": cid, "family": self.cases[cid]["family"],
                    "population": self.cases[cid]["population"], "program": program, **asdict(score)})
        log_metric = kwargs.get("log_metric")
        if log_metric and scores:
            log_metric("verified/correct", sum(s.reward > 0 for s in scores) / len(scores))
            log_metric("verified/correct_native", sum(s.reward > 0 and s.native_tests == s.total_tests
                                                       for s in scores) / len(scores))
        return rewards
