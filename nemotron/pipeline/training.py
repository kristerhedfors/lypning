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
from dataclasses import asdict
from pathlib import Path

from . import sandbox
from .jsonio import sha256_of, write_json, write_jsonl

from .training_types import Score, TrainingError, VerificationBlocked
from .training_contract import complete
from .training_data import validate_cases, split_cases, validate_pilot, validate_reference_scores

SCHEMA = 3
POLICY = "l-correctness-v2"
SYSTEM = "Write a Python standard-library program. Return exactly one fenced python code block."


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
            "verifier_sha256": sha256_of({name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                for name in ("training.py", "training_types.py", "training_data.py", "data_loop.py", "container_runner.py")}),
            "sandbox_sha256": hashlib.sha256(Path(sandbox.__file__).read_bytes()).hexdigest(),
            "child_exec_sha256": hashlib.sha256(Path(sandbox.__file__).with_name("child_exec.py").read_bytes()).hexdigest()}


def messages(case):
    # No reference, expected output, engine hints, or refusal feedback in prompts.
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": case["task"]}]


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
        return (r.exit_code, r.stdout, r.stderr, r.timed_out, r.truncated, r.memory_exceeded, r.encoding_error)

    def score(self, case, program):
        if self.identity is not None and engine_identity(self.binary) != self.identity:
            raise VerificationBlocked("engine/oracle/verifier drift during run")
        if not program or not program.strip():
            return Score(0.0, "no-code")
        tests = case["tests"]
        # Correctness gates the ENTIRE program before native coverage is scored.
        for i, test in enumerate(tests):
            oracle = self._run(program, test)
            if (not oracle.ok or oracle.memory_exceeded or oracle.truncated or oracle.encoding_error or oracle.stderr or
                    oracle.stdout != test["stdout"]):
                return Score(0.0, "incorrect", total_tests=len(tests), failed_test=i)
            # Only a would-be SUCCESS needs a stability check. Tracebacks name
            # the fresh temporary script path: comparing two failing runs byte
            # for byte would abort RL on every ordinary model NameError.
            repeat = self._run(program, test)
            if self._observed(oracle) != self._observed(repeat):
                raise VerificationBlocked("unstable oracle: %s test %d" % (case["case_id"], i))
        native_count = 0
        refusals = []
        for i, test in enumerate(tests):
            native = self._run(program, test, native=True)
            if native.exit_code == 90:
                if (native.stdout or native.timed_out or native.memory_exceeded or native.truncated or native.encoding_error or not re.fullmatch(
                        r"lypning-l: unsupported: [^:\n]+: [^\n]+\n?", native.stderr)):
                    raise VerificationBlocked("refusal protocol: %s test %d" % (case["case_id"], i))
                refusals.append((i, native.stderr.strip()))
                continue
            # Native timeout/exception/wrong output after a correct oracle is an
            # engine issue, not permission to teach the model to avoid a feature.
            if (not native.ok or native.memory_exceeded or native.truncated or native.encoding_error or native.stderr or
                    native.stdout != test["stdout"]):
                raise VerificationBlocked("engine mismatch: " + json.dumps({
                    "case_id": case["case_id"], "test": i, "expected_stdout": test["stdout"],
                    "observed": self._observed(native)}, ensure_ascii=False))
            native_count += 1
        if case["population"] == "fallback-control":
            return Score(1.0, "correct-control", native_count, len(tests), tuple(refusals))
        if native_count == len(tests):
            return Score(1.0, "correct-native", native_count, len(tests))
        return Score(0.25, "correct-fallback", native_count, len(tests), tuple(refusals))


def prepare(cases, binary, output, seed=1111, timeout_s=5.0, memory_mb=1024, purpose="smoke", execution_image=None, review_path=None,
            execution_kind="docker", execution_revision=None):
    """Verify references then publish a new immutable experiment directory."""
    output = Path(output)
    if output.exists():
        raise TrainingError("output already exists; use a new experiment directory")
    validate_cases(cases)
    review_manifest = None
    if review_path:
        from .data_loop import load_review
        review_manifest = load_review(review_path, cases, seed, purpose)
    identity = engine_identity(binary)
    cases = split_cases(cases, seed)
    if purpose not in ("smoke", "pilot"):
        raise TrainingError("purpose must be smoke or pilot")
    if purpose == "pilot":
        validate_pilot(cases)
        if not review_manifest:
            raise TrainingError("pilot requires --review; observations are not verified tasks")
        if not execution_image:
            raise TrainingError("pilot preparation requires --execution-image; a temporary cwd is not isolation")
    execution = execution_contract(execution_kind, execution_image, execution_revision)
    runner = execution_runner(execution, identity)
    verifier = Verifier(binary, timeout_s=timeout_s, memory_mb=memory_mb, identity=identity, runner=runner)
    references = {c["case_id"]: asdict(verifier.score(c, c["reference"])) for c in cases}
    references = json.loads(json.dumps(references))  # same tuple/list shape after manifest reload
    validate_reference_scores(cases, references)
    # The engine may have been rebuilt while validating the inputs.
    if engine_identity(binary) != identity:
        raise TrainingError("engine changed during preparation")
    payload = {"cases": cases, "identity": identity, "seed": seed,
               "schema": SCHEMA, "system": SYSTEM, "reference_scores": references,
               "purpose": purpose, "memory_policy": sandbox.memory_policy(memory_mb),
               "execution": execution,
               "data_review": review_manifest,
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
    validate_reference_scores(payload["cases"], payload["reference_scores"])
    if payload.get("purpose") not in ("smoke", "pilot"):
        raise TrainingError("missing experiment purpose")
    if payload["purpose"] == "pilot":
        validate_pilot(payload["cases"])
        reviewed = payload.get("data_review")
        if not isinstance(reviewed, dict) or reviewed.get("purpose") != "pilot":
            raise TrainingError("pilot missing reviewed data lineage")
        review_body = {k: v for k, v in reviewed.items() if k != "digest"}
        original_cases = [{k: v for k, v in c.items() if k not in ("split", "split_group")} for c in payload["cases"]]
        if (sha256_of(review_body) != reviewed.get("digest") or
                sha256_of(original_cases) != reviewed.get("cases_sha256") or reviewed.get("seed") != payload["seed"]):
            raise TrainingError("reviewed case lineage changed")
        if payload.get("execution", {}).get("kind") not in ISOLATED_KINDS:
            raise TrainingError("pilot requires an isolated execution contract; prepare a new bundle")
    validate_execution(payload.get("execution", {"kind": "local-reviewed-smoke"}))
    if payload["memory_policy"] != sandbox.memory_policy(payload["limits"]["memory_mb"]):
        raise TrainingError("memory enforcement policy changed")
    if split_cases(payload["cases"], payload["seed"]) != payload["cases"]:
        raise TrainingError("family split changed")
    payload["digest"] = digest
    return payload


#: The execution contracts that count as an isolation boundary for generated
#: code: a locked-down Docker container on a disposable worker, or a pooled
#: Hugging Face sandbox on a host VM that is never the trainer's. The local
#: subprocess helper is neither and is admitted for reviewed smoke fixtures only.
ISOLATED_KINDS = ("docker", "hf-sandbox-pool")


def execution_contract(kind, image, revision=None):
    """The bundle's execution record from the operator's three inputs."""
    if not image:
        return {"kind": "local-reviewed-smoke"}
    if kind == "docker":
        return {"kind": "docker", "image": image}
    if kind == "hf-sandbox-pool":
        return {"kind": "hf-sandbox-pool", "image": image, "revision": revision}
    raise TrainingError("unknown execution kind: %r" % (kind,))


def validate_execution(execution):
    from .container_runner import IMAGE_PATTERN
    from .hf_sandbox_runner import IMAGE_PATTERN as SPACE_PATTERN, REVISION_PATTERN
    if execution == {"kind": "local-reviewed-smoke"}:
        return
    if not isinstance(execution, dict):
        raise TrainingError("invalid execution contract")
    if execution.get("kind") == "docker":
        if (set(execution) != {"kind", "image"} or not isinstance(execution["image"], str)
                or not re.fullmatch(IMAGE_PATTERN, execution["image"])):
            raise TrainingError("invalid execution contract")
        return
    if execution.get("kind") == "hf-sandbox-pool":
        if (set(execution) != {"kind", "image", "revision"} or not isinstance(execution["image"], str)
                or not re.fullmatch(SPACE_PATTERN, execution["image"])
                or not isinstance(execution["revision"], str)
                or not re.fullmatch(REVISION_PATTERN, execution["revision"])):
            raise TrainingError("invalid execution contract")
        return
    raise TrainingError("invalid execution contract")


def execution_runner(execution, identity):
    validate_execution(execution)
    if execution["kind"] == "docker":
        from .container_runner import ContainerRunner
        return ContainerRunner(execution["image"], identity)
    if execution["kind"] == "hf-sandbox-pool":
        from .hf_sandbox_runner import HfSandboxPoolRunner
        return HfSandboxPoolRunner(execution["image"], execution["revision"], identity)
    return None


class Reward:
    """TRL callable: only train IDs are addressable; infrastructure errors abort."""
    __name__ = "verified_lypning_l"

    def __init__(self, cases, verifier, witness_path=None, eos_token_id=None, rollout_path=None,
                 generations=None, max_no_signal=0):
        self.cases = {c["case_id"]: c for c in cases if c["split"] == "train"}
        self.verifier = verifier
        self.witness_path = witness_path
        self.eos_token_id = eos_token_id
        self.rollout_path = rollout_path
        self.generations = generations
        self.max_no_signal = max_no_signal
        self.no_signal = 0

    def __call__(self, completions, case_id, **kwargs):
        if len(completions) != len(case_id):
            raise TrainingError("completion/case_id batch length mismatch")
        token_ids = kwargs.get("completion_ids")
        if self.eos_token_id is not None and (token_ids is None or len(token_ids) != len(completions)):
            raise TrainingError("TRL completion token IDs required for truncation check")
        rewards, scores, truncated_flags = [], [], []
        for i, (completion, cid) in enumerate(zip(completions, case_id)):
            if cid not in self.cases:
                raise TrainingError("non-training case in RL batch: " + cid)
            program = program_from_completion(completion)
            truncated = self.eos_token_id is not None and not complete(token_ids[i], self.eos_token_id)
            truncated_flags.append(truncated)
            if truncated:
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
                    "split_group": self.cases[cid].get("split_group", self.cases[cid]["family"]),
                    "population": self.cases[cid]["population"], "program": program,
                    "capabilities": self.cases[cid].get("capabilities", []),
                    "truncated": truncated, "completion_tokens": len(token_ids[i]) if token_ids is not None else None,
                    **asdict(score)})
        if self.generations:
            if len(scores) % self.generations:
                raise TrainingError("partial GRPO generation group")
            for start in range(0, len(scores), self.generations):
                end = start + self.generations
                if len(set(case_id[start:end])) != 1:
                    raise TrainingError("GRPO group mixes task IDs")
                live = {r for r, truncated in zip(rewards[start:end], truncated_flags[start:end]) if not truncated}
                self.no_signal = self.no_signal + 1 if len(live) < 2 else 0
                if self.max_no_signal and self.no_signal >= self.max_no_signal:
                    raise TrainingError("RL has no usable reward variation; stop and review probe/data/SFT")
        log_metric = kwargs.get("log_metric")
        if log_metric and scores:
            log_metric("verified/correct", sum(s.correct for s in scores) / len(scores))
            log_metric("verified/correct_native", sum(s.native for s in scores) / len(scores))
        return rewards
