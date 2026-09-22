"""Check train references in the pinned, networkless runtime; aggregates only.

The enclosing disposable container has no credentials or provider access.
Only previously reviewed references execute here; generated completions must
use the minimal per-candidate container boundary instead.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from pipeline.positive_control import population
from pipeline.training import Verifier, engine_identity
from pipeline.training_data import validate_reference_scores
from pipeline.training_types import TrainingError


def main():
    rows = [json.loads(line) for line in Path('/bank/train.jsonl').read_text().splitlines() if line.strip()]
    cases = population(rows)
    binary = Path(os.environ['LYPNING_HOME']) / 'bin' / 'lypning-l'
    identity = engine_identity(binary)
    verifier = Verifier(binary, identity=identity)
    counts = Counter()
    for case in cases:
        try:
            score = verifier.score(case, case['reference'])
            validate_reference_scores([case], {case['case_id']: asdict(score)})
        except TrainingError:
            # Error messages can contain expected stdout. Never print them.
            counts['failed'] += 1
        else:
            counts[score.status] += 1
    result = {'cases': len(cases), 'counts': dict(counts), 'python': sys.version,
              'base_image': os.environ['CHECK_BASE_IMAGE'],
              'engine_sha256': identity['sha256'], 'provider_calls': 0}
    Path('/result/references.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return int(bool(counts['failed']))


if __name__ == '__main__':
    sys.exit(main())
