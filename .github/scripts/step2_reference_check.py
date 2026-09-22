"""Check train references in the pinned, networkless runtime; aggregates only.

Each oracle/native execution uses the minimal candidate image: no bank,
expected outputs, credentials, host mounts or network cross that boundary.
The trusted controller runs on the disposable GitHub worker without API keys.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import asdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import json
import os
from pathlib import Path
import sys
import subprocess
import time
from pipeline.container_runner import ContainerRunner
from pipeline.positive_control import population
from pipeline.jsonio import sha256_of
from pipeline.training import Verifier, engine_identity
from pipeline.training_data import validate_reference_scores
from pipeline.training_types import TrainingError


def worker_count(cpus, available_mb):
    # Each live container is capped at 1,152 MiB. Leave 2 GiB for Docker,
    # the controller and OS; startup is I/O-heavy, so allow two per CPU.
    return max(1, min(8, 2 * cpus, (available_mb - 2048) // 1152))


def verify_cases(cases, check, workers, report, *, deadline_s=3000,
                 heartbeat_s=30):
    """Bound work in flight; report completion order, including slow cases.

    Stop admitting new work after a failure or the local deadline. Running
    checks drain behind the same container limits; partial counts never admit.
    """
    started = time.monotonic()
    counts = Counter()
    remaining = iter(cases)
    stopped = False

    def snapshot():
        elapsed = time.monotonic() - started
        completed = sum(counts.values())
        result = {'cases': len(cases), 'completed': completed,
                  'complete': completed == len(cases) and not counts['failed'],
                  'counts': dict(counts), 'workers': workers,
                  'elapsed_seconds': round(elapsed, 1), 'stopped_early': stopped}
        if completed:
            result['estimated_remaining_seconds'] = round(elapsed * (len(cases) - completed) / completed)
        report(result)
        return result

    snapshot()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = set()
        def fill():
            while len(pending) < workers:
                case = next(remaining, None)
                if case is None:
                    break
                pending.add(pool.submit(check, case))
        fill()
        last_report = started
        while pending:
            done, pending = wait(pending, timeout=heartbeat_s, return_when=FIRST_COMPLETED)
            for future in done:
                counts[future.result()] += 1
            now = time.monotonic()
            stopped = stopped or bool(counts['failed']) or now - started >= deadline_s
            if not stopped:
                fill()
            if now - last_report >= heartbeat_s:
                snapshot()
                last_report = now
    return snapshot()


def main():
    rows = [json.loads(line) for line in (Path(os.environ['RUNNER_TEMP']) / 'step2-bank' / 'train.jsonl').read_text().splitlines() if line.strip()]
    cases = population(rows)
    binary = Path(os.environ['LYPNING_HOME']) / 'bin' / 'lypning-l'
    identity = engine_identity(binary)
    # The host and the pinned base can differ in CPython patch/build. Expected
    # oracle identity comes from that exact base, not from the candidate's reply.
    oracle = subprocess.check_output([
        'docker', 'run', '--rm', '--network=none', '--entrypoint=python3',
        os.environ['CHECK_BASE_IMAGE'], '-c', 'import sys; print(sys.version)'], text=True).strip()
    expected = dict(identity, oracle=oracle)
    runner = ContainerRunner(os.environ['CANDIDATE_IMAGE'], expected)
    # The image ID is immutable and every candidate uses it. The host binary
    # is hashed again at the end, not confused with the in-image interpreter.
    verifier = Verifier(binary, runner=runner)
    private = Path(os.environ['RUNNER_TEMP']) / 'step2-private'
    private.mkdir(exist_ok=True)
    result_dir = Path(os.environ['RUNNER_TEMP']) / 'step2-result'
    result_dir.mkdir(exist_ok=True)
    # This job is a Linux worker. Unknown memory means the conservative floor.
    memory = Path('/proc/meminfo')
    available_mb = 4096
    if memory.exists():
        available_mb = int(next(line.split()[1] for line in memory.read_text().splitlines()
                                if line.startswith('MemAvailable:'))) // 1024
    workers = worker_count(os.cpu_count() or 1, available_mb)
    print(json.dumps({'event': 'reference_start', 'cases': len(cases),
                      'tests': sum(len(c['tests']) for c in cases),
                      'workers': workers, 'deadline_seconds': 3000}), flush=True)
    def check(case):
        try:
            score = verifier.score(case, case['reference'])
            validate_reference_scores([case], {case['case_id']: asdict(score)})
        except TrainingError as exc:
            # Error messages can contain expected stdout. Keep only privately.
            (private / ('failure-' + sha256_of(case) + '.json')).write_text(
                json.dumps({'case_id': case['case_id'], 'error': str(exc)}) + '\n')
            return 'failed'
        else:
            return score.status
    def report(progress):
        text = json.dumps(progress, sort_keys=True)
        temporary = result_dir / 'references-progress.tmp'
        temporary.write_text(text + '\n')
        temporary.replace(result_dir / 'references-progress.json')
        print(text, flush=True)
    progress = verify_cases(cases, check, workers, report)
    if engine_identity(binary) != identity:
        raise TrainingError('host engine changed during validation')
    result = dict(progress, **{'python': oracle,
              'base_image': os.environ['CHECK_BASE_IMAGE'],
              'engine_sha256': identity['sha256'], 'provider_calls': 0})
    (Path(os.environ['RUNNER_TEMP']) / 'step2-result' / 'references.json').write_text(json.dumps(result, indent=2) + '\n')
    if result['complete']:
        conformance = json.loads((Path(os.environ['RUNNER_TEMP']) / 'step2-result' / 'candidate.json').read_text())
        proof = {'conformance': conformance, 'references': result,
                 'candidate_image': os.environ['CANDIDATE_IMAGE'],
                 'source_commit': os.environ['GITHUB_SHA'],
                 'case_fingerprints': {c['case_id']: sha256_of(c) for c in cases}}
        # Private proof must never be included in a public Actions artifact.
        (private / 'admission.json').write_text(json.dumps(proof, sort_keys=True) + '\n')
    print(json.dumps(result, indent=2))
    return int(not result['complete'])


if __name__ == '__main__':
    sys.exit(main())
