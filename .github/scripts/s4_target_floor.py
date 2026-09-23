"""Free exact token and shape gate for a private rejection-sampling SFT set.

Everything the billed job would refuse about a target set, asked on a plain
runner with only the tokenizer, before the meter starts:

  THE SPLIT. Targets are graded on one train split, the one `split_cases` makes
  at the SPLIT seed, and the trainer refuses any target whose case is not a
  train case of its bundle. Since 2026-09-22 the split seed is fixed (1111)
  for every training seed of an arm (`training/hf/launch.py` says why), so
  `--split-seed` splits and `--seed` only orders the schedule. Counting at
  `--seed 2222` therefore counts what seed 2222 would train on.

  THE ENGINE. The trainer also refuses targets whose `lineage.engine_sha256`
  is not the engine of the bundle, which is the `lypning-l` the job downloads
  from the verifier Space at its pinned revision. This compares against THAT
  engine, downloaded and hashed here. It used to compare the target report
  against its own `engine_sha256`, which always passed, and left the real
  comparison to the plan stage of the billed job, after ~45 minutes of GPU-idle
  preparation.

  THE DOSE. The supervised-token floor over the exact schedule, beside the
  same count over each distinct row once: a small set clears the floor by
  repetition (Actions run 35762924601, 2026-09-22: 156,691 scheduled tokens
  over 157 rows, about 7.6 passes), and only the pair says so. And the case
  and family floors on the rows actually trained (`curriculum_floor`), not on
  the bundle behind them.

Exit 0 only when every floor clears; the report is written either way.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path

from pipeline.public_view import public_view
from pipeline.training_contract import BASE_MODEL, MIN_SUPERVISED_TOKENS, PROTOCOL_TRAIN_SEEDS
from pipeline.training_data import split_cases, validate_pilot
from pipeline.training import chat_prompt_token_ids, messages
from token_floor import LORA, borrow
from train_verified import curriculum_floor, load_sft_targets
from verified_stages import sft_batches, supervised_tokens

#: The verifier Space `round02.yml`'s bootstrap maintains, under the token's account.
SPACE_NAME = 'lypning-round02-verifier'
#: The split every training seed of an S4 arm trains on (`launch.DEFAULT_SPLIT_SEED`).
PROTOCOL_SPLIT_SEED = 1111


def assigned_split(raw, split_seed):
    """The bank as review and preparation assign it at `split_seed`, admitted as a pilot."""
    assigned = split_cases(raw, split_seed)
    validate_pilot(assigned)
    return assigned


def admitted_targets(target_dir, assigned, engine_sha256):
    """Load the targets exactly as the trainer will, against the engine it will run.

    The lineage is compared first and by name, because `load_sft_targets`
    folds it into one refusal with the digest and the report shape.
    """
    report = json.loads((Path(target_dir) / 'sft-report.json').read_text())
    graded = (report.get('lineage') or {}).get('engine_sha256')
    if graded != engine_sha256:
        raise SystemExit('ENGINE LINEAGE: targets were graded by engine %s; the pilot would run '
                         'engine %s, and its trainer refuses them at the plan stage'
                         % (graded, engine_sha256))
    bundle = {'cases': assigned, 'identity': {'sha256': engine_sha256}}
    return load_sft_targets(Path(target_dir) / 'sft.jsonl', bundle)


def space_engine(api, space, revision, token):
    """The engine a pilot would download: `lypning-l` from the Space at `revision`.

    With no revision, the Space's current head -- what the next bootstrap
    serves unless its build changes the bytes, which is why `round02.yml`
    passes the revision it just pinned and this workflow's standalone form
    reports which of the two it compared against.
    """
    from huggingface_hub import hf_hub_download

    resolved = 'pinned'
    if not revision:
        revision, resolved = api.repo_info(space, repo_type='space').sha, 'space head'
    path = hf_hub_download(space, 'lypning-l', repo_type='space', revision=revision, token=token)
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return {'space': space, 'space_revision': revision, 'resolved': resolved, 'sha256': digest}


def floor_report(cases, rows, examples, steps, batch_size, seed):
    """The dose the schedule exposes, the dose the rows hold, and the curriculum floors."""
    tokens = supervised_tokens(sft_batches(cases, examples, steps, batch_size, seed))
    unique = supervised_tokens([examples])
    floor = curriculum_floor(cases)
    return {
        'examples': len(rows), 'cases': floor['cases'], 'families': floor['families'],
        'families_by_population': floor['families_by_population'],
        'populations': dict(sorted(Counter(r['population'] for r in rows).items())),
        'supervised_tokens': tokens, 'unique_supervised_tokens': unique,
        'passes_over_rows': round(tokens / unique, 2) if unique else None,
        'minimum_supervised_tokens': MIN_SUPERVISED_TOKENS,
        'minimum_cases': floor['minimum_cases'],
        'minimum_families_per_population': floor['minimum_families_per_population'],
        'curriculum_problems': floor['problems'],
        'clears': tokens >= MIN_SUPERVISED_TOKENS and not floor['problems'],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--target-run', required=True)
    ap.add_argument('--bank-path', default='banks/v3-20260920b')
    ap.add_argument('--revision', required=True)
    ap.add_argument('--steps', type=int, default=300)
    ap.add_argument('--batch-size', type=int, default=4)
    ap.add_argument('--seed', type=int, default=1111,
                    help='training seed: the schedule order (one of %s)'
                         % ', '.join(map(str, PROTOCOL_TRAIN_SEEDS)))
    ap.add_argument('--split-seed', type=int, default=PROTOCOL_SPLIT_SEED,
                    help='review and preparation seed: which cases are train')
    ap.add_argument('--space', help='verifier Space, owner/name; default <whoami>/' + SPACE_NAME)
    ap.add_argument('--space-revision', default='',
                    help="the Space commit the pilot pins; default the Space's head")
    ap.add_argument('--max-seq', type=int, default=4096)
    ap.add_argument('--max-new-tokens', type=int, default=1024)
    ap.add_argument('--out', type=Path)
    args = ap.parse_args(argv)
    if ('/' in args.target_run or '..' in args.target_run or len(args.revision) != 40 or
            min(args.steps, args.batch_size, args.max_seq, args.max_new_tokens) <= 0):
        raise SystemExit('invalid target run, revision or schedule')
    if args.seed not in PROTOCOL_TRAIN_SEEDS or args.split_seed not in PROTOCOL_TRAIN_SEEDS:
        raise SystemExit('--seed and --split-seed must be pre-registered seeds: %s'
                         % ', '.join(map(str, PROTOCOL_TRAIN_SEEDS)))
    if args.space_revision and len(args.space_revision) != 40:
        raise SystemExit('--space-revision must be a 40-character commit')
    from huggingface_hub import HfApi, hf_hub_download
    from transformers import AutoTokenizer

    token = os.environ['HF_TOKEN'].strip()
    api = HfApi(token=token)
    owner = api.whoami()['name']
    repo = owner + '/lypning-round02-artifacts'
    info = api.repo_info(repo, repo_type='dataset')
    if info.private is not True:
        raise SystemExit('artifact repository must be private')
    bank = args.bank_path.strip('/')
    bank_file = Path(hf_hub_download(repo, bank + '/train.jsonl', repo_type='dataset',
                                     revision=info.sha, token=token))
    manifest_file = Path(hf_hub_download(repo, bank + '/bank.json', repo_type='dataset',
                                         revision=info.sha, token=token))
    raw = [json.loads(line) for line in bank_file.read_text().splitlines() if line.strip()]
    manifest = json.loads(manifest_file.read_text())
    if manifest['train']['cases'] != len(raw):
        raise SystemExit('bank count differs from manifest')
    assigned = assigned_split(raw, args.split_seed)

    prefix = 'positive-control/%s/grade/' % args.target_run
    target_dir = Path(os.environ.get('RUNNER_TEMP', '/tmp')) / 's4-target-floor'
    target_dir.mkdir(parents=True, exist_ok=False)
    for name in ('sft.jsonl', 'sft-report.json'):
        source = hf_hub_download(repo, prefix + name, repo_type='dataset',
                                 revision=info.sha, token=token)
        (target_dir / name).write_bytes(Path(source).read_bytes())
    engine = space_engine(api, args.space or owner + '/' + SPACE_NAME, args.space_revision, token)
    cases, rows, admitted = admitted_targets(target_dir, assigned, engine['sha256'])

    tok = AutoTokenizer.from_pretrained(BASE_MODEL, revision=args.revision,
                                        token=token, trust_remote_code=False)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    if tok.eos_token_id is None or tok.encode('<|im_end|>', add_special_tokens=False) != [tok.eos_token_id]:
        raise SystemExit('Qwen assistant terminator does not equal tokenizer EOS')
    build_examples, build_sha = borrow(LORA, 'build_examples')
    examples, dropped = build_examples(tok, rows, args.max_seq)
    if dropped or len(examples) != len(rows):
        raise SystemExit('SFT targets exceed max sequence length')
    over = sum(len(chat_prompt_token_ids(tok, messages(case))) + args.max_new_tokens > args.max_seq
               for case in cases)
    if over:
        raise SystemExit('target prompt plus completion allowance exceeds max sequence length')
    public = {
        'schema': 2, 'target_run': args.target_run, 'bank_path': bank,
        'tokenizer_revision': args.revision, 'seed': args.seed, 'split_seed': args.split_seed,
        'steps': args.steps, 'batch_size': args.batch_size,
        'engine': engine,
        'max_seq': args.max_seq, 'max_new_tokens': args.max_new_tokens,
        'build_examples_sha256': build_sha,
        'target_digest': admitted['sft_sha256'],
    }
    public.update(floor_report(cases, rows, examples, args.steps, args.batch_size, args.seed))
    print(json.dumps(public_view(public), indent=2, sort_keys=True))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        # Uploaded as a PUBLIC Actions artifact: the same view as the log.
        args.out.write_text(json.dumps(public_view(public), indent=2, sort_keys=True) + '\n')
    return 0 if public['clears'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
