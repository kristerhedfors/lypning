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
from .training_data import validate_cases, split_cases, validate_benchmark, validate_pilot, validate_reference_scores

SCHEMA = 3
#: smoke: authored starter data, local execution allowed, never a real run.
#: pilot: a reviewed, isolated, split bank that sft/probe/grpo train on.
#: benchmark: a reviewed, isolated bank that stage eval measures WHOLE
#: (--eval-split all) and nothing trains on; split_groups still cluster bootstraps.
PURPOSES = ("smoke", "pilot", "benchmark")
ADMISSION = {"pilot": validate_pilot, "benchmark": validate_benchmark}
#: v3 (2026-09-16): a candidate program whose two clean oracle runs disagree
#: scores ``unstable`` (reward 0) instead of aborting the stage. Preparation
#: runs every reference twice, so the oracle's own stability on each test is
#: established before any candidate is scored; a disagreement on a candidate
#: is the program's (set order under hash randomisation, the clock, randomness).
#: Under v2 one such program ended the probe of job 6aaa73e9 at its first chunk.
POLICY = "l-correctness-v3"
SYSTEM = "Write a Python standard-library program. Return exactly one fenced python code block."
#: The modules whose bytes ARE the verifier, hashed into `verifier_sha256`: a
#: bundle is reusable only by code that scores exactly as the code that
#: prepared it did. `sandbox.py` and `child_exec.py` are hashed separately under
#: their own identity keys. `hf_sandbox_runner.py` is the runner every pilot and
#: benchmark bundle actually executes through (`execution.kind ==
#: "hf-sandbox-pool"`), and `container_worker.py` is the program that runs each
#: candidate inside that sandbox. Until 2026-09-22 neither was here, so a change
#: to how a candidate is executed or its result reported left every prepared
#: bundle's identity untouched. Adding them is a POLICY-visible identity change:
#: a bundle prepared before it no longer loads and must be re-prepared.
VERIFIER_MODULES = ("training.py", "training_types.py", "training_data.py", "data_loop.py",
                    "container_runner.py", "hf_sandbox_runner.py", "container_worker.py")


def verifier_sha256(directory=None):
    """One digest over `VERIFIER_MODULES`, read from `directory` (default: here)."""
    here = Path(directory) if directory is not None else Path(__file__).parent
    return sha256_of({name: hashlib.sha256((here / name).read_bytes()).hexdigest()
                      for name in VERIFIER_MODULES})


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
            "verifier_sha256": verifier_sha256(),
            "sandbox_sha256": hashlib.sha256(Path(sandbox.__file__).read_bytes()).hexdigest(),
            "child_exec_sha256": hashlib.sha256(Path(sandbox.__file__).with_name("child_exec.py").read_bytes()).hexdigest()}


def messages(case):
    # No reference, expected output, engine hints, or refusal feedback in prompts.
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": case["task"]}]


def assistant_turn(program):
    """The assistant message an SFT target supervises: one fenced python block.

    Built here, once, so every SFT row, every supervised-token bound and every
    exported target wraps a program identically; four hand-written copies of
    this string could each drift on trailing-newline handling and the plan
    bound would stop describing the rows it bounds. It is the inverse of
    `program_from_completion` for any program the verifier could score:
    `program_from_completion(assistant_turn(p)) == p.rstrip()` whenever `p`
    holds no triple backtick of its own. Trailing whitespace goes because the
    fence's closing newline replaces it; leading whitespace stays, since it can
    be the program's own indentation.
    """
    return "```python\n" + program.rstrip() + "\n```"


def _sized(value):
    """`len(value)`, or 0 for something that has no length to take."""
    try:
        return len(value)
    except TypeError:
        return 0


def chat_prompt_token_ids(tok, msgs):
    """The prompt's TOKEN IDS. Count these; never count the call that made them.

    `apply_chat_template(tokenize=True)` returns a plain list of ids under
    transformers 4.x and a `BatchEncoding` -- a mapping of `input_ids` and
    `attention_mask` -- under the 5.x the experiment pins. Both answer `len()`,
    and the 5.x answer is 2, the number of keys: a budget check written against
    the 4.x shape reads every prompt as two tokens and admits any length at all
    (measured 2026-09-19: transformers 5.17.0, `len(out)` 2, `len(out["input_ids"])`
    44). So the extraction lives here, once, and every caller that needs a
    length calls this instead of `len()` on the render; a shape that is neither
    of the two raises rather than being counted as whatever it is.

    The render settings are the same three the SFT examples and the held-out
    generation use, for the same reason they agree there: a budget measured
    under a different template is a budget for a prompt nothing sends.
    """
    ids = tok.apply_chat_template(msgs, tokenize=True, add_generation_prompt=True,
                                  enable_thinking=False)
    if hasattr(ids, "keys"):                        # BatchEncoding is a mapping
        if "input_ids" not in ids:
            raise TrainingError("apply_chat_template returned a mapping without input_ids: "
                                + ", ".join(sorted(str(k) for k in ids.keys())))
        ids = ids["input_ids"]
    # One conversation went in, so a batched row comes back nested by one level.
    if _sized(ids) == 1 and isinstance(ids[0], (list, tuple)):
        ids = ids[0]
    count = _sized(ids)
    if not count or isinstance(ids, (str, bytes)) or not hasattr(ids[0], "__index__"):
        raise TrainingError("apply_chat_template(tokenize=True) returned %s, which is not token "
                            "ids; its length is a measurement of the wrong thing"
                            % type(ids).__name__)
    return ids


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
                # The oracle proved stable on this test at preparation (every
                # reference ran twice); a candidate that disagrees with itself
                # is a nondeterministic program, scored as wrong (POLICY v3).
                return Score(0.0, "unstable", total_tests=len(tests), failed_test=i)
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


def release_runner(runner):
    """Give a pooled runner's hosts back before the next stage asks for them.

    A round's stages are separate processes sharing one named pool, and
    `SandboxPool.create` RAISES rather than waits once every host is full: it
    adopts the warm hosts, reads each one's true live-sandbox count from the
    host itself, and then asks for host number `max_hosts + 1`. So a stage that
    exits while its sandboxes are still winding down hands the next stage a
    pool that is full against a ceiling that cannot move.

    Job 6ab01391 died exactly there on 2026-09-20, at `--purpose benchmark`,
    AFTER the pilot bundle was built -- the expensive half, paid for and then
    discarded. Nothing had ever called `close()`; the hosts simply outlived the
    process that booted them.

    Best-effort by construction: the caller's bundle is already written and
    hosts idle-time out on their own, so failing to release must not turn a
    finished preparation into a failed one. A runner without `close` (the
    docker `ContainerRunner`, or `None` for an in-process run) is not an error.
    """
    close = getattr(runner, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:                                             # noqa: BLE001
        pass


def prepare(cases, binary, output, seed=1111, timeout_s=5.0, memory_mb=1024, purpose="smoke", execution_image=None, review_path=None,
            execution_kind="docker", execution_revision=None, score_workers=1):
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
    if purpose not in PURPOSES:
        raise TrainingError("purpose must be one of " + ", ".join(PURPOSES))
    if purpose in ADMISSION:
        ADMISSION[purpose](cases)
        if not review_manifest:
            raise TrainingError(purpose + " requires --review; observations are not verified tasks")
        if not execution_image:
            raise TrainingError(purpose + " preparation requires --execution-image; a temporary cwd is not isolation")
    execution = execution_contract(execution_kind, execution_image, execution_revision)
    # The output directory names the stage ("pilot", "eval2"), which is what
    # keeps this preparation's pool distinct from the previous one's.
    runner = execution_runner(execution, identity, stage=output.name)
    verifier = Verifier(binary, timeout_s=timeout_s, memory_mb=memory_mb, identity=identity, runner=runner)
    # Scored CONCURRENTLY, because this loop is the stage a round runs out of
    # clock in. It was a serial dict comprehension: one case, one sandbox, ~4 s
    # each, so a 1,976-case pilot is 2.2 hours and a 16- or 64-sandbox pool sat
    # idle behind it. Two rounds died at the wall inside here on 2026-09-19 and
    # 2026-09-20, and raising the pool ceiling did nothing because nothing was
    # asking the pool for more than one thing at a time.
    #
    # `executor.map` keeps input order, so `references` is identical whatever
    # the worker count — the manifest digest must not depend on how fast the
    # machine was — and it re-raises the first exception on iteration, which
    # keeps `VerificationBlocked` aborting the whole preparation as before.
    try:
        if score_workers > 1 and len(cases) > 1:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=min(score_workers, len(cases))) as pool:
                scored = list(pool.map(lambda c: verifier.score(c, c["reference"]), cases))
        else:
            scored = [verifier.score(c, c["reference"]) for c in cases]
    finally:
        # Hand the hosts back before the next stage asks for them; see
        # `release_runner`. In `finally` because a stage that fails still has to
        # release, or the retry meets the pool it just filled.
        release_runner(runner)
    references = {c["case_id"]: asdict(v) for c, v in zip(cases, scored)}
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
    # Prompt views only: line-countable audit evidence that holds no solution.
    # Until 2026-09-22 this also wrote `train-sft.jsonl` and `dev-sft.jsonl`.
    # No stage read either -- train_verified rebuilds its SFT rows from
    # `bundle["cases"]` -- and `dev-sft.jsonl` exported the verified reference
    # solutions of the SELECTION split into every uploaded bundle directory.
    for split in ("train", "dev", "test"):
        subset = [c for c in cases if c["split"] == split]
        write_jsonl(output / (split + "-prompts.jsonl"),
                    ({"case_id": c["case_id"], "prompt": messages(c)} for c in subset))
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
    purpose = payload.get("purpose")
    if purpose not in PURPOSES:
        raise TrainingError("missing experiment purpose")
    if purpose in ADMISSION:
        ADMISSION[purpose](payload["cases"])
        reviewed = payload.get("data_review")
        if not isinstance(reviewed, dict) or reviewed.get("purpose") != purpose:
            raise TrainingError(purpose + " missing reviewed data lineage")
        review_body = {k: v for k, v in reviewed.items() if k != "digest"}
        original_cases = [{k: v for k, v in c.items() if k not in ("split", "split_group")} for c in payload["cases"]]
        if (sha256_of(review_body) != reviewed.get("digest") or
                sha256_of(original_cases) != reviewed.get("cases_sha256") or reviewed.get("seed") != payload["seed"]):
            raise TrainingError("reviewed case lineage changed")
        if payload.get("execution", {}).get("kind") not in ISOLATED_KINDS:
            raise TrainingError(purpose + " requires an isolated execution contract; prepare a new bundle")
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


def execution_runner(execution, identity, stage=None):
    """The runner for one stage. `stage` names its pool; see `pool_name`."""
    validate_execution(execution)
    if execution["kind"] == "docker":
        from .container_runner import ContainerRunner
        return ContainerRunner(execution["image"], identity)
    if execution["kind"] == "hf-sandbox-pool":
        from .hf_sandbox_runner import HfSandboxPoolRunner
        return HfSandboxPoolRunner(execution["image"], execution["revision"], identity,
                                   stage=stage)
    return None


class Reward:
    """TRL callable: only train IDs are addressable; infrastructure errors abort."""
    __name__ = "verified_lypning_l"

    def __init__(self, cases, verifier, witness_path=None, eos_token_id=None, rollout_path=None,
                 generations=None, max_no_signal=0, score_workers=16):
        self.cases = {c["case_id"]: c for c in cases if c["split"] == "train"}
        self.score_workers = max(1, int(score_workers))
        self.verifier = verifier
        self.witness_path = witness_path
        self.eos_token_id = eos_token_id
        self.rollout_path = rollout_path
        self.generations = generations
        # A group with fewer than two distinct non-truncated rewards has zero
        # reward spread, so its advantages are all zero and it carries no
        # gradient (DAPO's dynamic-sampling observation, arXiv 2504.11343).
        # This used to ABORT the run after `max_no_signal` such groups in a
        # row. At seed 1111's 21% informative rate a 20-group run has
        # probability 0.79**20, about 0.009, at any starting point: roughly one
        # expected abort per 500 groups, which is the arm-C dose itself, so the
        # guard would have ended a healthy run by chance. It is a MEASUREMENT
        # now: counted here, logged as a fraction, never raised. Whether RL may
        # start at all is the probe's admission gate, which runs before this.
        # `max_no_signal` is still accepted so existing callers construct the
        # same object; it no longer does anything.
        self.max_no_signal = max_no_signal
        self.groups = 0
        self.no_signal_groups = 0

    @property
    def no_signal_fraction(self):
        """Cumulative fraction of scored GRPO groups with no reward spread."""
        return self.no_signal_groups / self.groups if self.groups else None

    def __call__(self, completions, case_id, **kwargs):
        if len(completions) != len(case_id):
            raise TrainingError("completion/case_id batch length mismatch")
        token_ids = kwargs.get("completion_ids")
        if self.eos_token_id is not None and (token_ids is None or len(token_ids) != len(completions)):
            raise TrainingError("TRL completion token IDs required for truncation check")
        rewards, truncated_flags, programs = [], [], []
        for i, (completion, cid) in enumerate(zip(completions, case_id)):
            if cid not in self.cases:
                raise TrainingError("non-training case in RL batch: " + cid)
            program = program_from_completion(completion)
            truncated = self.eos_token_id is not None and not complete(token_ids[i], self.eos_token_id)
            truncated_flags.append(truncated)
            programs.append(None if truncated else program)

        def score_one(item):
            cid, program = item
            try:
                return self.verifier.score(self.cases[cid], program)
            except VerificationBlocked as exc:
                if self.witness_path:
                    from .jsonio import append_jsonl
                    append_jsonl(self.witness_path, {"case_id": cid, "program": program,
                                                     "error": str(exc), "tests": self.cases[cid]["tests"]})
                raise
        # A group's completions are scored concurrently (each one is a dozen
        # sandbox requests) and kept in batch order; the first block wins.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(self.score_workers, max(1, len(programs)))) as pool:
            scores = list(pool.map(score_one, zip(case_id, programs)))
        for i, (cid, program, score) in enumerate(zip(case_id, programs, scores)):
            truncated = truncated_flags[i]
            rewards.append(score.reward)
            if self.rollout_path:
                from .jsonio import append_jsonl
                append_jsonl(self.rollout_path, {"case_id": cid, "family": self.cases[cid]["family"],
                    "split_group": self.cases[cid].get("split_group", self.cases[cid]["family"]),
                    "population": self.cases[cid]["population"], "program": program,
                    "capabilities": self.cases[cid].get("capabilities", []),
                    "truncated": truncated, "completion_tokens": len(token_ids[i]) if token_ids is not None else None,
                    **asdict(score)})
        batch_groups = batch_no_signal = 0
        if self.generations:
            if len(scores) % self.generations:
                raise TrainingError("partial GRPO generation group")
            for start in range(0, len(scores), self.generations):
                end = start + self.generations
                if len(set(case_id[start:end])) != 1:
                    raise TrainingError("GRPO group mixes task IDs")
                # Truncated completions are masked out of the loss, so they
                # cannot supply the spread that makes a group informative.
                live = {r for r, truncated in zip(rewards[start:end], truncated_flags[start:end]) if not truncated}
                batch_groups += 1
                batch_no_signal += len(live) < 2
            self.groups += batch_groups
            self.no_signal_groups += batch_no_signal
        log_metric = kwargs.get("log_metric")
        if log_metric and scores:
            log_metric("verified/correct", sum(s.correct for s in scores) / len(scores))
            log_metric("verified/correct_native", sum(s.native for s in scores) / len(scores))
            if batch_groups:
                log_metric("verified/frac_no_signal_groups", batch_no_signal / batch_groups)
                log_metric("verified/no_signal_fraction", self.no_signal_fraction)
        return rewards
