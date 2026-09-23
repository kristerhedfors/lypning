"""Download one named positive-control evidence directory from the private repo.

A resumed run holds only the requests it made itself; its result names the
runs it resumed (`resumed_from`), and their generation evidence is copied
read-only beside it under ``chain/<run>/paid`` so the grade reads the union.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download


def main():
    token = os.environ['HF_TOKEN'].strip()
    api = HfApi(token=token)
    repo = api.whoami()['name'] + '/lypning-round02-artifacts'
    info = api.repo_info(repo, repo_type='dataset')
    if not info.private:
        raise SystemExit('artifact repository must be private')
    prefix = 'positive-control/' + os.environ['STEP2_RUN_ID']
    snapshot = Path(snapshot_download(repo, repo_type='dataset', revision=info.sha,
                                     allow_patterns=[prefix + '/**'], token=token))
    source = snapshot / prefix
    if not (source / 'paid' / 'result.json').is_file():
        raise SystemExit('named private run is absent or incomplete')
    import shutil
    target = Path(os.environ['RUNNER_TEMP']) / 'step2-downloaded'
    shutil.copytree(source, target)
    result = json.loads((target / 'paid' / 'result.json').read_text())
    if result.get('resumed_from'):
        from step2_resume import download_recorded_chain, fetcher
        chain = download_recorded_chain(result, fetcher(os.environ), target / 'chain')
        print('Downloaded named private positive-control evidence and the %d run(s) it resumed'
              % len(chain))
        return
    print('Downloaded named private positive-control evidence')


if __name__ == '__main__':
    main()
