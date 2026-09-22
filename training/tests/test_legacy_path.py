"""The ntx-era path is marked legacy, and the task-first path does not use it.

`sample`, `split`, `schema` and `extract` are a second, disconnected SFT/eval
path with its own record, split, prompt and a lenient extractor. Their module
docstrings say so; this pins both halves of that claim, so a live stage that
starts importing one of them fails here instead of quietly mixing two prompts
or two extractors into one measurement.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ("sample", "split", "schema", "extract")
#: The modules a round-02 / S4 job executes, from preparation to evaluation.
LIVE = ["pipeline/" + name + ".py" for name in (
    "training", "training_data", "training_contract", "training_metrics", "training_types",
    "data_loop", "container_runner", "hf_sandbox_runner", "round_plan",
    "positive_control", "positive_control_generate", "positive_control_grade",
    "positive_control_targets")] + ["gpu/train_verified.py", "gpu/verified_stages.py",
                                    "gpu/verified_evaluation.py"]


@pytest.mark.parametrize("name", LEGACY)
def test_legacy_modules_say_so_and_name_the_live_counterpart(name):
    doc = importlib.import_module("pipeline." + name).__doc__
    assert "LEGACY" in doc
    assert "program_from_completion" in doc or ":mod:`sample`" in doc


def imported_pipeline_modules(path):
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 1 or module == "pipeline":
                found.update(a.name for a in node.names)
            if module.startswith("pipeline."):
                found.add(module.split(".", 1)[1])
            elif node.level == 1 and module:
                found.add(module)
        elif isinstance(node, ast.Import):
            found.update(a.name.split(".", 1)[1] for a in node.names if a.name.startswith("pipeline."))
    return found


@pytest.mark.parametrize("relative", LIVE)
def test_the_task_first_path_imports_no_legacy_module(relative):
    assert not imported_pipeline_modules(ROOT / relative) & set(LEGACY), relative


def test_the_import_scan_sees_each_form_it_must_refuse(tmp_path):
    probe = tmp_path / "probe.py"
    for source in ("from .split import SIMILARITY_CEILING", "from . import sample",
                   "from pipeline.extract import extract_program", "import pipeline.schema",
                   "from pipeline import split"):
        probe.write_text(source + "\n", encoding="utf-8")
        assert imported_pipeline_modules(probe) & set(LEGACY), source
