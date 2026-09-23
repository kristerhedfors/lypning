"""Persist private scored rows and publish only the aggregate report.

``--failure`` persists a FAILED grade's private evidence instead: the
traceback and any engine-mismatch witnesses `step2_grade.keep_failure` kept
under ``step2-grade-failure``, which the public log names only by type and
digest. It goes to ``grade-failure/<Actions run id>`` beside the run, never
over a grade, and is a no-op when the grade failed before writing any.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

from huggingface_hub import HfApi


def main(argv=None):
    failure = '--failure' in (sys.argv[1:] if argv is None else argv)
    temp = Path(os.environ['RUNNER_TEMP'])
    folder = temp / ('step2-grade-failure' if failure else 'step2-grade')
    if failure and not folder.is_dir():
        print('No private failure evidence to store')
        return
    token = os.environ['HF_TOKEN'].strip()
    api = HfApi(token=token)
    repo = api.whoami()['name'] + '/lypning-round02-artifacts'
    if not api.repo_info(repo, repo_type='dataset').private:
        raise SystemExit('artifact repository must be private')
    run = os.environ['STEP2_RUN_ID']
    dest = 'positive-control/' + run + '/grade'
    if failure:
        dest = 'positive-control/%s/grade-failure/%s' % (run, os.environ.get('GITHUB_RUN_ID', 'local'))
    api.upload_folder(repo_id=repo, repo_type='dataset', folder_path=folder, path_in_repo=dest,
                      commit_message=('Store private grade-failure evidence ' if failure
                                      else 'Store graded positive-control evidence ') + run)
    print('Stored private grade-failure evidence' if failure else 'Stored private graded evidence')


if __name__ == '__main__':
    main()
