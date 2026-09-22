"""Check train references in the pinned, networkless runtime; aggregates only.

Each oracle/native execution uses the minimal candidate image: no bank,
expected outputs, credentials, host mounts or network cross that boundary.
The trusted controller runs on the disposable GitHub worker without API keys.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import subprocess
from pipeline.container_runner import ContainerRunner
from pipeline.positive_control import population
from pipeline.jsonio import sha256_of
from pipeline.training import Verifier, engine_identity
from pipeline.training_data import validate_reference_scores
from pipeline.training_types import TrainingError


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
    def check(case):
        try:
            score = verifier.score(case, case['reference'])
            validate_reference_scores([case], {case['case_id']: asdict(score)})
        except TrainingError:
            # Error messages can contain expected stdout. Never print them.
            return 'failed'
        else:
            return score.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = Counter(pool.map(check, cases))
    if engine_identity(binary) != identity:
        raise TrainingError('host engine changed during validation')
    result = {'cases': len(cases), 'counts': dict(counts), 'python': oracle,
              'base_image': os.environ['CHECK_BASE_IMAGE'],
              'engine_sha256': identity['sha256'], 'provider_calls': 0}
    (Path(os.environ['RUNNER_TEMP']) / 'step2-result' / 'references.json').write_text(json.dumps(result, indent=2) + '\n')
    if not counts['failed']:
        conformance = json.loads((Path(os.environ['RUNNER_TEMP']) / 'step2-result' / 'candidate.json').read_text())
        proof = {'conformance': conformance, 'references': result,
                 'candidate_image': os.environ['CANDIDATE_IMAGE'],
                 'source_commit': os.environ['GITHUB_SHA'],
                 'case_fingerprints': {c['case_id']: sha256_of(c) for c in cases}}
        private = Path(os.environ['RUNNER_TEMP']) / 'step2-private'
        private.mkdir(exist_ok=True)
        # Private proof must never be included in a public Actions artifact.
        (private / 'admission.json').write_text(json.dumps(proof, sort_keys=True) + '\n')
    print(json.dumps(result, indent=2))
    return int(bool(counts['failed']))


if __name__ == '__main__':
    sys.exit(main())
