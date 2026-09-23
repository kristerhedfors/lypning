"""The deps-stage kernel refusal: the first wrong-kernel answer in minute one.

`train_verified.run` refuses a non-reference gated-delta rule, but its first
call comes after review and preparation; `kernel_block.refusal` asks the same
question from `round02_pilot.sh`'s deps stage, before anything is billed.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "training" / "gpu"))

import kernel_block  # noqa: E402


def wrapper_over(implementation):
    """A stand-in for transformers' hub-kernel wrapper: the closure is the answer."""
    def torch_chunk_gated_delta_rule(*a, **kw):
        return implementation(*a, **kw)
    return torch_chunk_gated_delta_rule


def modeling(name, implementation):
    module = types.ModuleType(name)
    module.torch_chunk_gated_delta_rule = wrapper_over(implementation)
    module.torch_recurrent_gated_delta_rule = wrapper_over(implementation)
    return module


@pytest.fixture
def meta_path(monkeypatch):
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))


def reference(*a, **kw):
    return None


reference.__module__ = "transformers.models.qwen3_5.modeling_qwen3_5"


def fused(*a, **kw):
    return None


fused.__module__ = "fla.ops.gated_delta_rule"


def test_the_torch_reference_is_admitted(meta_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "fake_modeling_ok", modeling("fake_modeling_ok", reference))
    assert kernel_block.refusal("fake_modeling_ok") is None


def test_a_fused_binding_is_refused_before_anything_is_billed(meta_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "fake_modeling_fla", modeling("fake_modeling_fla", fused))
    assert "torch reference" in kernel_block.refusal("fake_modeling_fla")


def test_an_already_imported_fla_is_refused(meta_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "fla", types.ModuleType("fla"))
    assert "before its blocker" in kernel_block.refusal("fake_modeling_ok")


def test_an_unimportable_modeling_module_is_a_refusal_not_a_crash(meta_path):
    assert "cannot import" in kernel_block.refusal("no_such_modeling_module_for_test")


def test_the_pilot_asks_in_its_deps_stage():
    text = (ROOT / "training" / "hf" / "round02_pilot.sh").read_text(encoding="utf-8")
    deps = text[text.index("STAGE=deps"):text.index("STAGE=engine")]
    # Through the installer the hardware smoke and the split eval-2 share.
    assert "bash training/hf/pinned_deps.sh" in deps
    shared = (ROOT / "training" / "hf" / "pinned_deps.sh").read_text(encoding="utf-8")
    assert "kernel_block.refusal()" in shared and "NTX_USE_FLA=0" in shared
