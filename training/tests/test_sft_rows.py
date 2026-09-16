"""The four things that would make stage 1a fail for reasons that are not the data.

A train-pool SLR that does not move says one of two things — the pipeline is
broken, or the data does not generalise — and those have completely different
fixes and identical symptoms. These tests take the first one off the table
before a GPU is rented, by pinning the four places where a training row can stop
being the thing the model is asked to continue at eval time:

    1. loss is on the completion and nothing else
    2. the training prefix is byte-for-byte the eval prefix, empty think block
       and all
    3. every completion decodes, through the EVAL's own extractor, back to the
       program the verifier passed
    4. the LoRA reaches the MLP projections, where a prior shift mostly lives

The first two need a tokeniser. The real one is a 27B checkpoint's, so it is
used when it is there and a double stands in when it is not — and the double is
built to be HOSTILE (it merges across the boundary the masking code slices at),
because a double that can only agree tests nothing.

`gpu/lypning_lora.py` is one file on purpose: it is uploaded whole to a rented
box and must not import from this tree. So its functions are read out of it
rather than imported, which keeps the one-file contract and still leaves no room
for this file's belief about them to drift from what runs.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from pipeline import extract

ROOT = Path(__file__).resolve().parents[1]
LORA = ROOT / "gpu" / "lypning_lora.py"
SFT = ROOT / "data" / "sft"


def _from_lora(*names):
    """Pull top-level defs and assignments out of the GPU script, without torch.

    The file imports torch at module scope, which is right for the box it runs on
    and fatal for a test runner. Reading the source and executing only the nodes
    asked for gets the real code under test with none of the machinery.
    """
    tree = ast.parse(LORA.read_text(encoding="utf-8"))
    wanted, ns = set(names), {}
    keep = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
            keep.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in wanted for t in node.targets):
            keep.append(node)
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(LORA), "exec"), ns)
    missing = wanted - set(ns)
    if missing:
        raise AssertionError("gpu/lypning_lora.py no longer defines: %s" % sorted(missing))
    return [ns[n] for n in names]


# The template shape this project trains and generates under: Qwen3 with
# enable_thinking=False, which still emits the think block — empty.
EMPTY_THINK = "<think>\n\n</think>\n\n"


class FakeTokenizer:
    """Character-level, with one deliberate multi-character merge.

    ``MERGE`` spans the last character of the rendered prompt and the first of a
    fenced completion. A tokeniser that fuses those makes ``tok(prompt)`` stop
    being a prefix of ``tok(prompt + completion)``, and the mask that slices at
    ``len(tok(prompt))`` then covers one token too few or too many. Nothing about
    that shows up in a loss curve, so it shows up here.
    """

    MERGE = "\n```"

    def __init__(self, merging: bool = False):
        self.merging = merging

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=False,
                            enable_thinking=True):
        assert tokenize is False, "this project renders to text, then tokenises"
        out = []
        for m in messages:
            out.append("<|im_start|>%s\n%s<|im_end|>\n" % (m["role"], m["content"]))
        if add_generation_prompt:
            out.append("<|im_start|>assistant\n")
            if not enable_thinking:
                out.append(EMPTY_THINK)
        return "".join(out)

    def __call__(self, text, add_special_tokens=False):
        ids, i = [], 0
        while i < len(text):
            if self.merging and text.startswith(self.MERGE, i):
                ids.append(-7)
                i += len(self.MERGE)
            else:
                ids.append(ord(text[i]))
                i += 1
        return {"input_ids": ids}


def _row(program="print(1)"):
    return {"case_id": "ntx-test", "messages": [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "```python\n%s\n```" % program}]}


def test_only_the_completion_carries_loss():
    """Every prompt token masked, every completion token not. §4, assertion 1."""
    (build_examples,) = _from_lora("build_examples")
    tok = FakeTokenizer()
    (ex,), truncated = build_examples(tok, [_row()], 4096)
    assert truncated == 0

    prompt = tok.apply_chat_template(_row()["messages"][:-1], tokenize=False,
                                     add_generation_prompt=True, enable_thinking=False)
    n_prompt = len(tok(prompt)["input_ids"])
    assert ex["labels"][:n_prompt] == [-100] * n_prompt
    assert all(x != -100 for x in ex["labels"][n_prompt:])
    # And the completion half is not empty, which is the way this assertion
    # passes while teaching the model nothing.
    assert len(ex["labels"]) > n_prompt


def test_a_tokeniser_that_merges_at_the_boundary_is_refused_not_mismasked():
    """The failure mode the slice cannot see, made loud. §4, assertion 1."""
    (build_examples,) = _from_lora("build_examples")
    with pytest.raises(ValueError) as exc:
        build_examples(FakeTokenizer(merging=True), [_row()], 4096)
    assert "boundary" in str(exc.value)


def test_the_training_prefix_is_the_eval_prefix():
    """What training continues and what generation continues are one string.

    §4, assertion 2. The empty think block is the whole point: with
    enable_thinking=False the template still opens and closes one, and a training
    row that does not carry it byte-for-byte asks the tuned model to continue a
    prefix it is never given.
    """
    (build_examples,) = _from_lora("build_examples")
    tok = FakeTokenizer()
    row = _row()
    (ex,), _ = build_examples(tok, [row], 4096)

    # The eval side renders exactly this, in `generate`, from the same three
    # arguments. If that call changes, this test is where the two part company.
    at_eval = tok.apply_chat_template(row["messages"][:-1], tokenize=False,
                                      add_generation_prompt=True, enable_thinking=False)
    assert EMPTY_THINK in at_eval, "the double stopped modelling the think block"
    n = len(tok(at_eval)["input_ids"])
    assert ex["input_ids"][:n] == tok(at_eval)["input_ids"]
    assert "".join(chr(c) for c in ex["input_ids"][:n]) == at_eval


def test_generate_renders_the_prompt_the_same_way_build_examples_does():
    """Not the strings — the CALL. §4, assertion 2, from the other end."""
    src = ast.parse(LORA.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(src)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "apply_chat_template"]
    assert len(calls) >= 2, "expected the training and the generation render"
    for call in calls:
        kw = {k.arg: ast.literal_eval(k.value) for k in call.keywords
              if isinstance(k.value, ast.Constant)}
        assert kw.get("tokenize") is False
        assert kw.get("add_generation_prompt") is True
        assert kw.get("enable_thinking") is False, (
            "one render asks for thinking and the other does not: the tuned model "
            "would be trained on a prefix it is never given at eval")


def _sft_files():
    return sorted(SFT.glob("*/sft.jsonl"))


def test_every_sft_completion_decodes_to_the_program_that_passed():
    """Through the EVAL's extractor, not a second one written for training.

    §4, assertion 3. A completion the eval parses differently from the verifier
    is a target for a program nothing ever ran.
    """
    files = _sft_files()
    assert files, "no SFT set in this tree to check"
    for path in files:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        assert rows, "%s is empty" % path
        for row in rows:
            msgs = row["messages"]
            assert msgs[-1]["role"] == "assistant", "%s: %s" % (path.name, row["case_id"])
            got, _how = extract.extract_program(msgs[-1]["content"])
            assert got is not None, "%s: %s: no program in the completion" % (
                path.name, row["case_id"])
            assert got.strip() == row["program"].strip(), (
                "%s: %s: the completion does not decode to the verified program"
                % (path.name, row["case_id"]))


def test_every_sft_row_is_marked_verified():
    """§4, assertion 3's other half: nothing unverified is a training target."""
    for path in _sft_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                assert row.get("verified") is True, "%s: %s" % (path.name, row["case_id"])


def test_the_lora_reaches_the_mlp_projections():
    """A prior shift lives in the MLPs at least as much as in attention. §4, 4."""
    (targets,) = _from_lora("TARGET_MODULES")
    for name in ("gate_proj", "up_proj", "down_proj"):
        assert name in targets, (
            "%s is not adapted: a style/prior shift is mostly an MLP effect, and "
            "an adapter that skips them is the cheapest thing to have got wrong"
            % name)
    # out_proj is the one this project already paid to learn about: excluded on a
    # borrowed rationale, it froze the 48 widest projections in the text tower.
    assert "out_proj" in targets


def test_the_vision_tower_is_excluded():
    """It writes no python and is 333 of 1,199 tensors."""
    (excluded,) = _from_lora("EXCLUDE_MODULES")
    assert any("visual" in pattern for pattern in excluded)
