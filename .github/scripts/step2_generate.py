"""Run one bounded positive-control generation from a private admission proof."""
from __future__ import annotations

import json
import os
from pathlib import Path

from pipeline.backends import ChatBackend
from pipeline.positive_control import MODEL
from pipeline.positive_control_generate import PROVIDER, generate
from step2_shard import cases_from_env, shard_from_env


def write_shard(paid, cases, shard):
    """Record which slice of which rung this run drew; counts only."""
    if paid.is_dir():
        (paid / 'shard.json').write_text(json.dumps(dict(shard, cases=cases), sort_keys=True) + '\n')


def main():
    root = Path(os.environ['RUNNER_TEMP'])
    rows = [json.loads(line) for line in (root / 'step2-bank' / 'train.jsonl').read_text().splitlines()
            if line.strip()]
    samples = int(os.environ['STEP2_SAMPLES'])
    cases = cases_from_env(rows)
    admission = json.loads((root / 'step2-private' / 'admission.json').read_text())
    backend = ChatBackend(PROVIDER, MODEL, api_key=os.environ['CEREBRAS_API_KEY'].strip(),
                          max_retries=0, timeout_s=120)
    result = generate(
        cases, Path('training/prompts/subset-spec.md').read_text(), backend,
        root / 'step2-paid', ceiling_usd=os.environ['STEP2_CEILING_USD'],
        admission=admission, workers=4,
        max_seconds=int(os.environ.get('STEP2_MAX_SECONDS', '14400')),
        requests_per_minute=int(os.environ.get('STEP2_RPM', '60')),
        samples=samples)
    # Beside manifest.json, not in it: the manifest is the generator's own
    # record and this is the orchestrator's. The grader and the merge read it
    # to prove they selected the very cases this run paid for.
    write_shard(root / 'step2-paid', int(os.environ['STEP2_CASES']), shard_from_env())
    print(json.dumps(result, sort_keys=True))
    return int(not result['complete'])


if __name__ == '__main__':
    raise SystemExit(main())
