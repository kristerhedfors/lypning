"""Persist private scored rows and publish only the aggregate report."""
from __future__ import annotations

import os
from pathlib import Path

from huggingface_hub import HfApi


def main():
    token = os.environ['HF_TOKEN'].strip()
    api = HfApi(token=token)
    repo = api.whoami()['name'] + '/lypning-round02-artifacts'
    if not api.repo_info(repo, repo_type='dataset').private:
        raise SystemExit('artifact repository must be private')
    grade = Path(os.environ['RUNNER_TEMP']) / 'step2-grade'
    api.upload_folder(repo_id=repo, repo_type='dataset', folder_path=grade,
                      path_in_repo='positive-control/' + os.environ['STEP2_RUN_ID'] + '/grade',
                      commit_message='Store graded positive-control evidence ' + os.environ['STEP2_RUN_ID'])
    print('Stored private graded evidence')


if __name__ == '__main__':
    main()
