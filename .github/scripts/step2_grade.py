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


def admitted_identity(identity, admission):
    """Match the runtime identity admitted before the paid provider calls.

    The GitHub controller and the pinned verifier image both run CPython 3.12,
    but their patch/build strings need not be identical.  Generation records
    the verifier image's exact ``sys.version`` after its reference check; use
    that value when admitting the rebuilt, immutable grading image.
    """
    try:
        oracle = admission['references']['python']
        expected_sha = admission['conformance']['engine_sha256']
    except (KeyError, TypeError) as exc:
        raise TrainingError('generation admission lacks runtime lineage') from exc
    if identity.get('sha256') != expected_sha:
        raise TrainingError('grading engine differs from generation admission')
    return dict(identity, oracle=oracle)


def main():
    root = Path(os.environ['RUNNER_TEMP'])
    private = root / 'step2-downloaded'
    admission = json.loads((private / 'admission.json').read_text())
    rows = [json.loads(line) for line in (root / 'step2-bank' / 'train.jsonl').read_text().splitlines()
            if line.strip()]
    cases = stratified_population(population(rows), int(os.environ['STEP2_CASES']))
    binary = Path(os.environ['LYPNING_HOME']) / 'bin' / 'lypning-l'
    identity = engine_identity(binary)
    expected = admitted_identity(identity, admission)
    # Check the bytes independently of engine_identity so a mocked or changed
    # identity implementation cannot weaken the paid-run lineage check.
    if hashlib.sha256(binary.read_bytes()).hexdigest() != expected['sha256']:
        raise TrainingError('grading engine differs from generation admission')
    runner = ContainerRunner(os.environ['CANDIDATE_IMAGE'], expected)
    verifier = Verifier(binary, runner=runner)
    public = grade_files(cases, private / 'paid' / 'completions.jsonl', verifier,
                         root / 'step2-grade', samples=int(os.environ['STEP2_SAMPLES']), workers=8,
                         run_id=os.environ['STEP2_RUN_ID'], lineage={
                             'source_commit': admission['source_commit'],
                             'engine_sha256': admission['conformance']['engine_sha256'],
                             'base_image': admission['conformance']['base_image'],
                             'candidate_image': admission['candidate_image'],
                         })
    print(json.dumps(public, sort_keys=True))


if __name__ == '__main__':
    main()
