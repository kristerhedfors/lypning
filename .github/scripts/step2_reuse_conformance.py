"""Reuse a passing corpus result only for identical engine/runtime/corpus code."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

RUNTIME_PATHS = ('src', '.github/scripts/step2_candidate_check.py',
                 'training/pipeline/sandbox.py', 'training/pipeline/child_exec.py',
                 'training/pipeline/container_worker.py', 'training/worker/Dockerfile.verifier',
                 'training/hf/launch.py')


def reusable(report, image, binary, base_image, unchanged):
    try:
        arm = report['engines']['lypning-l']
        counts = arm['counts']
        return bool(
            re.fullmatch('[0-9a-f]{40}', image['source_commit']) and
            report['base_image'] == image['base_image'] == base_image and
            report['engine_sha256'] == hashlib.sha256(binary).hexdigest() and
            report['loaded'] > 0 and counts['total'] > 0 and
            counts['match'] + counts['unsupported'] == counts['total'] and
            counts['total'] + report['skipped'] == report['loaded'] and
            counts['mismatch'] == 0 and arm['mismatches'] == [] and
            report['unbuilt'] == [] and report['damage'] == [] and
            unchanged(image['source_commit']))
    except (KeyError, TypeError, ValueError):
        return False


def main():
    root = Path(os.environ['RUNNER_TEMP'])
    prior = root / 'step2-prior'
    reused = False
    try:
        report = json.loads((prior / 'candidate.json').read_text())
        image = json.loads((prior / 'image.json').read_text())
        def unchanged(commit):
            return subprocess.run(['git', 'diff', '--quiet', commit, 'HEAD', '--', *RUNTIME_PATHS]).returncode == 0
        binary = (Path(os.environ['LYPNING_HOME']) / 'bin' / 'lypning-l').read_bytes()
        reused = reusable(report, image, binary, os.environ['CHECK_BASE_IMAGE'], unchanged)
        if reused:
            out = root / 'step2-result'
            out.mkdir(exist_ok=True)
            report['reused_from_run'] = 35683688427
            report['reused_source_commit'] = image['source_commit']
            (out / 'candidate.json').write_text(json.dumps(report, indent=2) + '\n')
    except (OSError, ValueError):
        pass
    with open(os.environ['GITHUB_OUTPUT'], 'a') as fh:
        fh.write('reused=' + str(reused).lower() + '\n')
    print('Identical runtime/corpus proof reused' if reused else 'Full corpus check required')


if __name__ == '__main__':
    main()
