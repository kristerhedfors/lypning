"""Download only the pilot bank at an immutable private Hub revision; plan, no calls."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
from huggingface_hub import HfApi, hf_hub_download


def main():
    api = HfApi(token=os.environ['HF_TOKEN'].strip())
    repo = api.whoami()['name'] + '/lypning-round02-artifacts'
    info = api.repo_info(repo, repo_type='dataset')
    if not info.private:
        raise SystemExit('artifact repository must be private')
    bank = 'banks/v3-20260920b'
    root = Path(os.environ['RUNNER_TEMP']) / 'step2-bank'
    root.mkdir()
    for name in ('bank.json', 'train.jsonl'):
        source = hf_hub_download(repo, bank + '/' + name, repo_type='dataset', revision=info.sha)
        (root / name).write_bytes(Path(source).read_bytes())
    rows = [json.loads(s) for s in (root / 'train.jsonl').read_text().splitlines() if s.strip()]
    manifest = json.loads((root / 'bank.json').read_text())
    if manifest['train']['cases'] != len(rows):
        raise SystemExit('bank count differs from manifest')
    if os.environ.get("STEP2_DOWNLOAD_ONLY") == "1":
        print("Downloaded the manifest-checked pilot bank; no generation")
        return
    revision = api.model_info('Qwen/Qwen3.8-27B').sha
    out = Path(os.environ['RUNNER_TEMP']) / 'step2-plan.json'
    subprocess.run([sys.executable, '-m', 'pipeline.positive_control', str(root / 'train.jsonl'),
                    '--revision', revision, '--out', str(out),
                    '--cases', os.environ.get('STEP2_CASES', '300'),
                    '--samples', os.environ.get('STEP2_SAMPLES', '16')], check=True)



if __name__ == '__main__':
    main()
