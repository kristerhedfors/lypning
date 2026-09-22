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
    admission = root / 'step2-private' / 'admission.json'
    if not admission.is_file():
        print('No runtime admission exists; nothing private to persist')
        return
    staging = root / 'step2-private-upload'
    staging.mkdir()
    sources = [(admission, 'admission.json')]
    paid = root / 'step2-paid'
    if paid.is_dir():
        sources.insert(0, (paid, 'paid'))
    for source, name in sources:
        target = staging / name
        if source.is_dir():
            import shutil
            shutil.copytree(source, target)
        else:
            target.write_bytes(source.read_bytes())
    path = 'positive-control/' + os.environ['STEP2_RUN_ID']
    api.upload_folder(repo_id=repo, repo_type='dataset', folder_path=staging,
                      path_in_repo=path, commit_message='Store private positive-control evidence ' + os.environ['STEP2_RUN_ID'])
    state = 'generation evidence' if paid.is_dir() else 'runtime admission only; generation did not start'
    print('Stored private positive-control %s at an immutable dataset commit under %s' % (state, path))


if __name__ == '__main__':
    main()
