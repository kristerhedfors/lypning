"""Download one named positive-control evidence directory from the private repo."""
from __future__ import annotations

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
    print('Downloaded named private positive-control evidence')


if __name__ == '__main__':
    main()
