"""Read-only-source corpus check in a disposable, networkless pinned-base worker.

Only aggregate counts and public corpus IDs leave the worker. No training bank
or provider token is present. The git snapshot is this worker's own worktree.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys
from lypning import corpus, conformance


def main():
    report = conformance.run(entries=corpus.load_default(), engines=['lypning-l'],
                             workers=1, timeout=60)
    binary = Path(os.environ['LYPNING_HOME']) / 'bin' / 'lypning-l'
    result = {
        'python': sys.version, 'base_image': os.environ['CHECK_BASE_IMAGE'],
        'engine_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'damage': report.damage, 'loaded': report.total, 'skipped': len(report.skipped), 'unbuilt': report.unbuilt,
        'engines': {name: {'counts': {k: getattr(arm, k) for k in ('match', 'unsupported', 'mismatch', 'total')},
                           'mismatches': [v.entry_id for v in arm.failures()]}
                    for name, arm in report.engines.items()},
    }
    Path('/result/candidate.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return int(bool(report.damage) or bool(report.unbuilt) or not report.engines or any(arm.failures() for arm in report.engines.values()))


if __name__ == '__main__':
    sys.exit(main())
