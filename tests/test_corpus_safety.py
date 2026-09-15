"""Admission checks never execute the package/download commands under test."""

from __future__ import annotations

import pytest

from lypning import bench, conformance, corpus
from lypning.corpus_safety import external_mutation


@pytest.mark.parametrize("program", [
    "import subprocess, sys; subprocess.run([sys.executable, '-m', 'pip', 'install', '-U', 'x'])",
    "import subprocess as sp; sp.check_call(['pip3.12', 'uninstall', '-y', 'x'])",
    "from subprocess import run as spawn; spawn(args=['pip', 'install', package])",
    "import os; os.system('python3 -m pip install x')",
    "import os as o; o.popen('echo ready && pip install x')",
    "import subprocess; subprocess.run(['sh', '-c', 'pip install x'])",
    "import subprocess; subprocess.run(['env', 'A=B', 'pip', 'install', 'x'])",
    "import subprocess; subprocess.run(['pip', '--python', 'venv', '--quiet', 'install', 'x'])",
    "import subprocess; subprocess.run(['uv', 'pip', 'install', 'x'])",
    "import subprocess; subprocess.run(['uv', 'sync'])",
    "import pip; pip.main(['install', 'x'])",
    "from pip._internal.cli.main import main; main(['install', 'x'])",
    "import os; os.execvp('pip', ['pip', 'install', 'x'])",
    "import subprocess; cmd = ['pip', 'install', 'x']; subprocess.run(cmd)",
    "import subprocess; cmd = 'pip install x'; subprocess.run(cmd, shell=True); cmd = 'echo safe'",
])
def test_package_mutations_are_excluded(program):
    assert external_mutation(conformance._tree(program)).startswith("external mutation: package")


@pytest.mark.parametrize("program", [
    "import huggingface_hub as h; h.hf_hub_download('model', 'weights')",
    "from huggingface_hub import snapshot_download as download; download('model')",
])
def test_model_downloads_are_excluded(program):
    assert external_mutation(conformance._tree(program)).startswith("external mutation: model")


@pytest.mark.parametrize("program", [
    "print('pip install x')",
    "patch = \"subprocess.run(['pip', 'install', 'x'])\"; print(patch)",
    "# subprocess.run(['pip', 'install', 'x'])\nprint(1)",
    "print(['pip', 'install', 'x'])",
    "import subprocess; subprocess.run(['pip', 'show', 'install'])",
    "import subprocess; subprocess.run(['pip', '--version'])",
    "import subprocess; subprocess.run(['python3', '-c', \"print('pip install x')\"])",
    "import subprocess; subprocess.run(['python3', '-c', 'print(1)', '-m', 'pip', 'install'])",
    "import subprocess; subprocess.run(['python3', 'script.py', '-m', 'pip', 'install'])",
    "import subprocess; subprocess.run('pip install x')",
    "import subprocess; subprocess.run('pip install x', shell=False)",
    "import os; os.system(\"echo 'pip install x'\")",
    "import huggingface_hub; print(huggingface_hub.__version__)",
    "hf_hub_download = 'quoted only'; print(hf_hub_download)",
    "def install(x): return x\nprint(install('pip'))",
    "this is invalid Python !!!",
])
def test_read_only_calls_and_quoted_code_remain_admitted(program):
    assert external_mutation(conformance._tree(program)) == ""


@pytest.mark.parametrize("entry_id", ["py-ab4bbc603d5e", "py-09ef230cf195"])
def test_captured_mutation_is_skipped_before_either_interpreter(monkeypatch, entry_id):
    entry = next(e for e in corpus.load_default() if e.id == entry_id)

    def must_not_run(*args, **kwargs):
        pytest.fail("unsafe capture reached an interpreter")

    monkeypatch.setattr(conformance.eng, "run", must_not_run)
    result = conformance._run_entry(entry, ["mixture"], {}, None, 1)
    assert result.skip.entry_id == entry.id
    assert result.skip.reason.startswith("external mutation:")
    assert result.verdicts == {}
    assert bench.skip_reason(entry) == result.skip.reason


def test_ordinary_program_is_still_graded():
    entry = corpus.Entry(id="py-wrong", program="print(1)")
    result = conformance.run([entry], engines=["cpython"], timeout=10, workers=1)
    assert not result.skipped
    assert not result.mismatches
