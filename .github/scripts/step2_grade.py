"""Grade a named private positive-control run in the pinned candidate image."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess

from pipeline.container_runner import ContainerRunner
from pipeline.positive_control import MODEL_REPO
from pipeline.positive_control_grade import grade_files
from pipeline.public_view import public_view
from pipeline.training import Verifier, engine_identity
from pipeline.training_types import TrainingError
from step2_merge import git_recipe
from step2_shard import cases_from_env, shard_from_env


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


def admitted_recipe(admission, base_image, head, recipe_of=git_recipe):
    """Refuse to grade in an image built from another recipe than generation's.

    Every verdict -- and so every SFT target -- comes from THIS job's image,
    rebuilt from the dispatched commit; the admission only names the image
    generation built. The engine bytes are checked in `admitted_identity`, but
    the base image and the four files the image is built from were not, so a
    grade dispatched after `sandbox.py` changed would score one shard under
    another sandbox, and `step2_merge.py`, which compares the recipe at each
    shard's GENERATION commit, would merge it. Same recipe as generation here,
    and the merge's recipe check covers the image that actually graded.
    """
    try:
        commit = admission['source_commit']
        admitted_base = admission['conformance']['base_image']
    except (KeyError, TypeError) as exc:
        raise TrainingError('generation admission lacks runtime lineage') from exc
    if base_image != admitted_base:
        raise TrainingError('grading base image differs from generation admission')
    recipe = recipe_of(head)
    if recipe != recipe_of(commit):
        raise TrainingError('grading image recipe differs from generation admission')
    return recipe


def grading_head():
    """The commit this job checked out, and so built its image from."""
    return subprocess.run(['git', 'rev-parse', 'HEAD'], check=True, capture_output=True,
                          text=True).stdout.strip()


def recorded_shard(paid, cases, shard):
    """Refuse to grade a run as a shard other than the one it paid for.

    Runs from before sharding have no shard.json and were never sharded, so
    their absence is the unsharded default and nothing else. The exact
    case/draw/arm cover in `grade` would refuse a wrong case set too, but only
    with a message that says nothing about which input was wrong.
    """
    path = paid / 'shard.json'
    recorded = (json.loads(path.read_text()) if path.is_file()
                else {'shard_index': 0, 'shard_count': 1, 'skip_prefix': 0, 'cases': cases})
    if recorded != dict(shard, cases=cases):
        raise TrainingError('the dispatched rung and shard differ from the ones this run generated')


def resumed_chain(private, run_id, recipe_of=git_recipe):
    """A resumed run's recorded chain and the run itself, oldest first; else None.

    `step2_private_download` copied each run the result names under
    ``chain/<run>/paid``. Each is re-read from its own ledger, and every run
    of the chain must be this run's experiment (`positive_control_resume`).
    """
    from pipeline.positive_control_resume import read_link
    result = json.loads((private / 'paid' / 'result.json').read_text())
    recorded = result.get('resumed_from')
    if not recorded:
        return None
    links = [read_link(entry.get('run_id'), private / 'chain' / str(entry.get('run_id')) / 'paid',
                       recipe_of) for entry in recorded]
    return links + [read_link(run_id, private / 'paid', recipe_of)]


def target_arms(value):
    """The dispatch input, as build_targets' arms; the reviewed default first."""
    arms = tuple(part.strip() for part in (value or 'subset-spec').split(',') if part.strip())
    if not arms:
        raise TrainingError('STEP2_TARGET_ARMS names no arm')
    return arms


def token_counter():
    """Count supervised segments with the trained model's own tokenizer.

    Without it the target builder falls back to a byte bound that is safe but
    drops targets that would have fit. The revision is the workflow's
    `QWEN_REV` pin -- the one the S4 job loads -- and is recorded next to
    every count it produced. Resolving the Hub's `main` here instead would let
    an upstream tokenizer commit size targets for a model nobody trains.
    """
    import re
    from transformers import AutoTokenizer
    revision = os.environ.get('QWEN_REV', '')
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise TrainingError('QWEN_REV must pin an immutable 40-hex commit')
    tok = AutoTokenizer.from_pretrained(MODEL_REPO, revision=revision, trust_remote_code=False)
    return (lambda text: len(tok(text, add_special_tokens=False)['input_ids']),
            '%s@%s' % (MODEL_REPO, revision))


def main():
    root = Path(os.environ['RUNNER_TEMP'])
    private = root / 'step2-downloaded'
    admission = json.loads((private / 'admission.json').read_text())
    rows = [json.loads(line) for line in (root / 'step2-bank' / 'train.jsonl').read_text().splitlines()
            if line.strip()]
    recorded_shard(private / 'paid', int(os.environ['STEP2_CASES']), shard_from_env())
    cases = cases_from_env(rows)
    binary = Path(os.environ['LYPNING_HOME']) / 'bin' / 'lypning-l'
    identity = engine_identity(binary)
    expected = admitted_identity(identity, admission)
    # Check the bytes independently of engine_identity so a mocked or changed
    # identity implementation cannot weaken the paid-run lineage check.
    if hashlib.sha256(binary.read_bytes()).hexdigest() != expected['sha256']:
        raise TrainingError('grading engine differs from generation admission')
    base_image = runpy.run_path('training/hf/launch.py')['BASE_IMAGE']
    head = grading_head()
    recipe = admitted_recipe(admission, base_image, head)
    runner = ContainerRunner(os.environ['CANDIDATE_IMAGE'], expected)
    verifier = Verifier(binary, runner=runner)
    count, tokenizer = token_counter()
    chain = resumed_chain(private, os.environ['STEP2_RUN_ID'])
    lineage_chain = {'resumed_from': [link['run_id'] for link in chain[:-1]]} if chain else {}
    public = grade_files(cases, private / 'paid' / 'completions.jsonl', verifier,
                         root / 'step2-grade', samples=int(os.environ['STEP2_SAMPLES']), workers=8,
                         run_id=os.environ['STEP2_RUN_ID'], lineage={
                             'source_commit': admission['source_commit'],
                             'engine_sha256': admission['conformance']['engine_sha256'],
                             'base_image': admission['conformance']['base_image'],
                             'candidate_image': admission['candidate_image'],
                             **lineage_chain,
                         }, progress=lambda event: print(json.dumps(event, sort_keys=True), flush=True),
                         target_arms=target_arms(os.environ.get('STEP2_TARGET_ARMS')),
                         token_count=count, tokenizer=tokenizer, chain=chain)
    # Which image graded, beside the rows it graded: the lineage above names
    # generation's image, and a rebuild never reproduces its id.
    (root / 'step2-grade' / 'grader.json').write_text(json.dumps({
        'source_commit': head, 'candidate_image': os.environ['CANDIDATE_IMAGE'],
        'base_image': base_image, 'candidate_recipe': recipe}, sort_keys=True) + '\n')
    print(json.dumps(public_view(public), sort_keys=True))


if __name__ == '__main__':
    main()
