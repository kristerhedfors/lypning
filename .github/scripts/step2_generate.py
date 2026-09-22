"""Run one bounded positive-control generation from a private admission proof."""
from __future__ import annotations

import json
import os
from pathlib import Path

from pipeline.backends import ChatBackend
from pipeline.positive_control import MODEL, population, stratified_population
from pipeline.positive_control_generate import PROVIDER, generate


def main():
    root = Path(os.environ['RUNNER_TEMP'])
    rows = [json.loads(line) for line in (root / 'step2-bank' / 'train.jsonl').read_text().splitlines()
            if line.strip()]
    target = int(os.environ['STEP2_CASES'])
    samples = int(os.environ['STEP2_SAMPLES'])
    cases = stratified_population(population(rows), target)
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
    print(json.dumps(result, sort_keys=True))
    return int(not result['complete'])


if __name__ == '__main__':
    raise SystemExit(main())
