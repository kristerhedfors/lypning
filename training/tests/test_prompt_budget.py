"""The prompt budget is an admission check, so it must count tokens.

`apply_chat_template(tokenize=True)` returned a plain list of token ids under
transformers 4.x and returns a `BatchEncoding` -- a mapping of `input_ids` and
`attention_mask` -- under the 5.17.0 the experiment pins. Both answer `len()`.
The 5.x answer is 2, the number of keys, so every guard that counted the call
instead of its ids compared 2 against a sequence budget and admitted a prompt
of any length. These tests hold both shapes against the one extraction, and
sweep the tree for a call site that counts the call again.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pipeline.training import chat_prompt_token_ids, messages
from pipeline.training_types import TrainingError

ROOT = Path(__file__).resolve().parents[2]
#: the one module allowed to call `apply_chat_template(tokenize=True)`.
EXTRACTION = ROOT / "training" / "pipeline" / "training.py"


class BatchEncodingDouble(dict):
    """transformers 5.x. A mapping: `len()` is the number of KEYS, which is 2."""


class FakeTokenizer:
    """A tokenizer that renders `n_tokens` ids in the shape asked for."""

    def __init__(self, n_tokens, shape="batchencoding"):
        self.n_tokens = n_tokens
        self.shape = shape
        self.calls = []

    def apply_chat_template(self, msgs, **kwargs):
        self.calls.append(kwargs)
        ids = list(range(1000, 1000 + self.n_tokens))
        if self.shape == "list":
            return ids
        if self.shape == "text":
            return "".join(chr(65 + i % 26) for i in range(self.n_tokens))
        if self.shape == "sizeless":
            return self.n_tokens
        if self.shape == "nested":
            return BatchEncodingDouble(input_ids=[ids], attention_mask=[[1] * self.n_tokens])
        return BatchEncodingDouble(input_ids=ids, attention_mask=[1] * self.n_tokens)


def case(case_id="c1"):
    return {"case_id": case_id, "task": "print the first ten primes"}


def gpu_module():
    import importlib.util
    path = ROOT / "training" / "gpu" / "train_verified.py"
    spec = importlib.util.spec_from_file_location("verified_gpu_budget_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_batchencoding_is_counted_in_tokens_and_not_in_keys():
    """The measured 5.17.0 shape: two keys, forty-four tokens."""
    tok = FakeTokenizer(44)
    assert len(tok.apply_chat_template(messages(case()))) == 2, "the double is not the bug's shape"
    assert len(chat_prompt_token_ids(tok, messages(case()))) == 44


def test_the_transformers_4x_list_shape_still_counts():
    assert len(chat_prompt_token_ids(FakeTokenizer(44, "list"), messages(case()))) == 44


def test_a_batched_encoding_of_one_conversation_is_unnested():
    assert len(chat_prompt_token_ids(FakeTokenizer(44, "nested"), messages(case()))) == 44


def test_the_render_settings_are_the_ones_the_rest_of_the_run_uses():
    """Training on a prompt shaped differently from the one at inference."""
    tok = FakeTokenizer(44)
    chat_prompt_token_ids(tok, messages(case()))
    assert tok.calls == [{"tokenize": True, "add_generation_prompt": True,
                          "enable_thinking": False}]


def test_a_shape_that_is_not_token_ids_raises_instead_of_being_counted():
    """A third shape is a refusal, never a measurement of whatever it is."""
    with pytest.raises(TrainingError) as exc:
        chat_prompt_token_ids(FakeTokenizer(44, "text"), messages(case()))
    assert "str" in str(exc.value)


def test_a_shape_with_no_length_at_all_raises_rather_than_crashing_the_caller():
    with pytest.raises(TrainingError) as exc:
        chat_prompt_token_ids(FakeTokenizer(44, "sizeless"), messages(case()))
    assert "int" in str(exc.value)


def test_a_mapping_without_input_ids_names_the_keys_it_did_get():
    class OddTokenizer(FakeTokenizer):
        def apply_chat_template(self, msgs, **kwargs):
            return BatchEncodingDouble(token_ids=[1, 2, 3], attention_mask=[1, 1, 1])
    with pytest.raises(TrainingError) as exc:
        chat_prompt_token_ids(OddTokenizer(3), messages(case()))
    assert "attention_mask, token_ids" in str(exc.value)


def test_the_admission_check_refuses_a_long_batchencoding_prompt():
    """The bug itself: 4,000 tokens through the 5.x shape, budget 2,048."""
    gpu = gpu_module()
    with pytest.raises(TrainingError) as exc:
        gpu.check_prompt_budget(FakeTokenizer(4000), [case("too-long")],
                                max_new_tokens=512, max_seq=2048)
    # Named by digest, never by id: this message reaches the GPU job's
    # public log (2026-09-23).
    from pipeline.training_types import case_ref
    assert case_ref("too-long") in str(exc.value) and "too-long" not in str(exc.value)


def test_the_admission_check_refuses_a_long_list_prompt_too():
    gpu = gpu_module()
    with pytest.raises(TrainingError):
        gpu.check_prompt_budget(FakeTokenizer(4000, "list"), [case("too-long")],
                                max_new_tokens=512, max_seq=2048)


def test_a_prompt_inside_the_budget_is_admitted():
    gpu = gpu_module()
    gpu.check_prompt_budget(FakeTokenizer(100), [case("short")],
                            max_new_tokens=512, max_seq=2048)


def _template_calls(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "apply_chat_template"]


def _sources():
    return (sorted((ROOT / "training" / "gpu").glob("*.py"))
            + sorted((ROOT / "training" / "pipeline").glob("*.py"))
            + sorted((ROOT / ".github" / "scripts").glob("*.py")))


def test_nothing_outside_the_extraction_tokenizes_a_chat_template():
    """Every `tokenize=True` render goes through `chat_prompt_token_ids`."""
    offenders = []
    for path in _sources():
        if path == EXTRACTION:
            continue
        for call in _template_calls(path):
            for keyword in call.keywords:
                if (keyword.arg == "tokenize" and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is True):
                    offenders.append("%s:%d" % (path.relative_to(ROOT), call.lineno))
    assert offenders == [], ("these tokenize a chat template themselves instead of calling "
                            "chat_prompt_token_ids, so they own the shape bug: " + ", ".join(offenders))


def test_no_call_site_takes_the_length_of_a_chat_template_call():
    """`len(apply_chat_template(...))` is the defect in one expression."""
    offenders = []
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "len" and node.args):
                continue
            inner = node.args[0]
            if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "apply_chat_template"):
                offenders.append("%s:%d" % (path.relative_to(ROOT), node.lineno))
    assert offenders == [], ("len() of a chat-template call counts keys under transformers 5.x: "
                            + ", ".join(offenders))
