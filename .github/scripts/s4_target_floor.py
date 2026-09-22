"""Free exact token and shape gate for a private rejection-sampling SFT set."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
from types import SimpleNamespace

from huggingface_hub import HfApi, hf_hub_download
from transformers import AutoTokenizer

from pipeline.training import chat_prompt_token_ids, messages
from pipeline.training_contract import BASE_MODEL, MIN_SUPERVISED_TOKENS
from pipeline.training_data import split_cases, validate_pilot
from token_floor import LORA, borrow
from train_verified import load_sft_targets
from verified_stages import sft_batches, supervised_tokens


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--target-run', required=True)
    ap.add_argument('--bank-path', default='banks/v3-20260920b')
    ap.add_argument('--revision', required=True)
    ap.add_argument('--steps', type=int, default=300)
    ap.add_argument('--batch-size', type=int, default=4)
    ap.add_argument('--seed', type=int, default=1111)
    ap.add_argument('--max-seq', type=int, default=4096)
    ap.add_argument('--max-new-tokens', type=int, default=1024)
    ap.add_argument('--out', type=Path)
    args = ap.parse_args(argv)
    if ('/' in args.target_run or '..' in args.target_run or len(args.revision) != 40 or
            min(args.steps, args.batch_size, args.max_seq, args.max_new_tokens) <= 0):
        raise SystemExit('invalid target run, revision or schedule')
    token = os.environ['HF_TOKEN'].strip()
    api = HfApi(token=token)
    repo = api.whoami()['name'] + '/lypning-round02-artifacts'
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
    assigned = split_cases(raw, args.seed)
    validate_pilot(assigned)

    prefix = 'positive-control/%s/grade/' % args.target_run
    target_dir = Path(os.environ.get('RUNNER_TEMP', '/tmp')) / 's4-target-floor'
    target_dir.mkdir(parents=True, exist_ok=False)
    for name in ('sft.jsonl', 'sft-report.json'):
        source = hf_hub_download(repo, prefix + name, repo_type='dataset',
                                 revision=info.sha, token=token)
        (target_dir / name).write_bytes(Path(source).read_bytes())
    target_report = json.loads((target_dir / 'sft-report.json').read_text())
    engine_sha = (target_report.get('lineage') or {}).get('engine_sha256')
    bundle = {'cases': assigned, 'identity': {'sha256': engine_sha}}
    cases, rows, admitted = load_sft_targets(target_dir / 'sft.jsonl', bundle)

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
    batches = sft_batches(cases, examples, args.steps, args.batch_size, args.seed)
    tokens = supervised_tokens(batches)
    public = {
        'schema': 1, 'target_run': args.target_run, 'bank_path': bank,
        'tokenizer_revision': args.revision, 'seed': args.seed,
        'steps': args.steps, 'batch_size': args.batch_size,
        'examples': len(rows), 'cases': len(set(r['case_id'] for r in rows)),
        'families': len(set(r['family'] for r in rows)),
        'populations': dict(sorted(Counter(r['population'] for r in rows).items())),
        'supervised_tokens': tokens, 'minimum_supervised_tokens': MIN_SUPERVISED_TOKENS,
        'clears': tokens >= MIN_SUPERVISED_TOKENS,
        'max_seq': args.max_seq, 'max_new_tokens': args.max_new_tokens,
        'build_examples_sha256': build_sha,
        'target_digest': admitted['sft_sha256'],
    }
    print(json.dumps(public, indent=2, sort_keys=True))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(public, indent=2, sort_keys=True) + '\n')
    return 0 if public['clears'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
