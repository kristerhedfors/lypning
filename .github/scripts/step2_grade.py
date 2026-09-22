"""Grade a named private positive-control run in the pinned candidate image."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from pipeline.container_runner import ContainerRunner
from pipeline.positive_control import population, stratified_population
from pipeline.positive_control_grade import grade_files
from pipeline.training import Verifier, engine_identity
from pipeline.training_types import TrainingError


def main():
    root = Path(os.environ['RUNNER_TEMP'])
    private = root / 'step2-downloaded'
    admission = json.loads((private / 'admission.json').read_text())
    rows = [json.loads(line) for line in (root / 'step2-bank' / 'train.jsonl').read_text().splitlines()
            if line.strip()]
    cases = stratified_population(population(rows), int(os.environ['STEP2_CASES']))
    binary = Path(os.environ['LYPNING_HOME']) / 'bin' / 'lypning-l'
    identity = engine_identity(binary)
    expected_sha = admission['conformance']['engine_sha256']
    if hashlib.sha256(binary.read_bytes()).hexdigest() != expected_sha:
        raise TrainingError('grading engine differs from generation admission')
    runner = ContainerRunner(os.environ['CANDIDATE_IMAGE'], identity)
    verifier = Verifier(binary, runner=runner)
    public = grade_files(cases, private / 'paid' / 'completions.jsonl', verifier,
                         root / 'step2-grade', samples=int(os.environ['STEP2_SAMPLES']), workers=8)
    print(json.dumps(public, sort_keys=True))


if __name__ == '__main__':
    main()
