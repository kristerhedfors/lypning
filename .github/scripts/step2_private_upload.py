"""Persist paid evidence only in the existing private dataset repository."""
from __future__ import annotations

import os
from pathlib import Path

from huggingface_hub import HfApi


def main():
    token = os.environ['HF_TOKEN'].strip()
    api = HfApi(token=token)
    who = api.whoami()
    repo = who['name'] + '/lypning-round02-artifacts'
    info = api.repo_info(repo, repo_type='dataset')
    if not info.private:
        raise SystemExit('artifact repository must be private')
    root = Path(os.environ['RUNNER_TEMP'])
    staging = root / 'step2-private-upload'
    staging.mkdir()
    for source, name in ((root / 'step2-paid', 'paid'),
                         (root / 'step2-private' / 'admission.json', 'admission.json')):
        target = staging / name
        if source.is_dir():
            import shutil
            shutil.copytree(source, target)
        else:
            target.write_bytes(source.read_bytes())
    path = 'positive-control/' + os.environ['STEP2_RUN_ID']
    api.upload_folder(repo_id=repo, repo_type='dataset', folder_path=staging,
                      path_in_repo=path, commit_message='Store private positive-control evidence ' + os.environ['STEP2_RUN_ID'])
    print('Stored private positive-control evidence at an immutable dataset commit under ' + path)


if __name__ == '__main__':
    main()
