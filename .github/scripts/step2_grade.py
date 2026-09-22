"""Grade a named private positive-control run in the pinned candidate image."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from pipeline.container_runner import ContainerRunner
from pipeline.positive_control import MODEL_REPO, population, stratified_population
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


def target_arms(value):
    """The dispatch input, as build_targets' arms; the reviewed default first."""
    arms = tuple(part.strip() for part in (value or 'subset-spec').split(',') if part.strip())
    if not arms:
        raise TrainingError('STEP2_TARGET_ARMS names no arm')
    return arms


def token_counter():
    """Count supervised segments with the trained model's own tokenizer.

    Without it the target builder falls back to a byte bound that is safe but
    drops targets that would have fit. The revision is resolved once, pinned
    to a 40-hex commit and recorded next to every count it produced.
    """
    import re
    from huggingface_hub import HfApi
    from transformers import AutoTokenizer
    revision = HfApi().model_info(MODEL_REPO).sha
    if not re.fullmatch('[0-9a-f]{40}', revision or ''):
        raise TrainingError('tokenizer revision must be an immutable 40-hex commit')
    tok = AutoTokenizer.from_pretrained(MODEL_REPO, revision=revision, trust_remote_code=False)
    return (lambda text: len(tok(text, add_special_tokens=False)['input_ids']),
            '%s@%s' % (MODEL_REPO, revision))


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
    count, tokenizer = token_counter()
    public = grade_files(cases, private / 'paid' / 'completions.jsonl', verifier,
                         root / 'step2-grade', samples=int(os.environ['STEP2_SAMPLES']), workers=8,
                         run_id=os.environ['STEP2_RUN_ID'], lineage={
                             'source_commit': admission['source_commit'],
                             'engine_sha256': admission['conformance']['engine_sha256'],
                             'base_image': admission['conformance']['base_image'],
                             'candidate_image': admission['candidate_image'],
                         }, progress=lambda event: print(json.dumps(event, sort_keys=True), flush=True),
                         target_arms=target_arms(os.environ.get('STEP2_TARGET_ARMS')),
                         token_count=count, tokenizer=tokenizer)
    print(json.dumps(public, sort_keys=True))


if __name__ == '__main__':
    main()
