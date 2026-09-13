"""The guards on the three steps that cost money, checked where they cost none.

Every test here pins something that, left unpinned, turns into a number nobody
can defend or a GPU-hour nobody gets back. None of them make a request.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import cli as nt
from pipeline import sample as sample_mod
from pipeline import split as splitmod
from pipeline.backends import ChatBackend
from pipeline.jsonio import read_jsonl, write_json, write_jsonl


class _Recorder(ChatBackend):
    """Counts requests instead of making them. A test that spends is not a test."""

    def __init__(self, **kw):
        super().__init__(base_url="http://recorder/v1", model=kw.pop("model", "m-1"), **kw)
        self.calls = []

    def complete(self, messages, **kw):  # type: ignore[override]
        from pipeline.backends import Completion
        self.calls.append(kw.get("seed"))
        return Completion(text="```python\nprint(1)\n```", reasoning=None,
                          prompt_tokens=1, completion_tokens=1, latency_s=0.0,
                          finish_reason="stop")


def _case(cid: str) -> dict:
    return {"id": cid, "prompt": "print one", "category": "wrong-output",
            "test": {"kind": "stdout", "expect_stdout": "1\n"}}



def _gpu_constants() -> dict:
    """The GPU script's module lists, read without importing torch.

    The file it comes from declares its dependencies inline for `hf jobs uv run`
    and imports torch at module scope; this suite runs where neither exists, so
    the constants are lifted out of the source with `ast` rather than executed.
    """
    import ast
    src = (Path(__file__).resolve().parents[1] / "gpu" / "lypning_lora.py").read_text()
    out = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in (
                    "TARGET_MODULES", "EXCLUDE_MODULES", "BASE_MODEL"):
                out[target.id] = ast.literal_eval(node.value)
    return out


# --- step 1: the sampling directory is on-policy or it is refused -------------

def test_draws_from_another_model_are_not_folded_into_this_run(tmp_path):
    """`data/sft/v1/draws.jsonl` is in the tree and `--name` defaults to v1.

    Rejection sampling is on-policy by construction, so mixing two models' draws
    is off-policy training data with nothing in the report to show it.
    """
    out = tmp_path / "sft"
    out.mkdir()
    (out / "draws.jsonl").write_text(
        json.dumps({"case_id": "c1", "draw": 0, "kept": True, "program": "print(1)",
                    "reason": "pass"}) + "\n", encoding="utf-8")
    b = _Recorder()
    with pytest.raises(ValueError) as exc:
        sample_mod.sample_targets(b, [_case("c1")], out, k=1)
    assert "backend.json" in str(exc.value)
    assert b.calls == [], "it spent before refusing"


def test_a_second_model_pointed_at_one_directory_is_refused(tmp_path):
    out = tmp_path / "sft"
    first = _Recorder(model="model-a")
    sample_mod.sample_targets(first, [_case("c1")], out, k=1)
    second = _Recorder(model="model-b")
    with pytest.raises(ValueError) as exc:
        sample_mod.sample_targets(second, [_case("c1")], out, k=1, resume=True)
    assert "model-a" in str(exc.value) and "model-b" in str(exc.value)
    assert second.calls == []


def test_resume_does_not_redraw_what_is_already_recorded(tmp_path):
    """A run that dies at draw 2,900 of 2,976 must cost the 76 that are missing."""
    out = tmp_path / "sft"
    b = _Recorder()
    sample_mod.sample_targets(b, [_case("c1")], out, k=4)
    assert len(b.calls) == 4
    again = _Recorder()
    rep = sample_mod.sample_targets(again, [_case("c1")], out, k=6, resume=True)
    assert again.calls == [1004, 1005], "resume redrew a recorded (case, draw) pair"
    assert rep["resumed_draws"] == 4 and rep["drawn_this_run"] == 2
    assert len({(d["case_id"], d["draw"]) for d in read_jsonl(out / "draws.jsonl")}) == 6


def test_without_resume_the_whole_run_is_drawn_again(tmp_path):
    """The behaviour --resume exists to avoid, pinned so it cannot be mistaken
    for resume: every pair is redrawn and the file then holds two runs."""
    out = tmp_path / "sft"
    b = _Recorder()
    sample_mod.sample_targets(b, [_case("c1")], out, k=3)
    again = _Recorder()
    again.api_key = None
    sample_mod.sample_targets(again, [_case("c1")], out, k=3)
    assert len(again.calls) == 3
    assert len(read_jsonl(out / "draws.jsonl")) == 6


# --- step 1: the lock is verified before a dollar, not only before the eval ---

def test_a_corpus_edited_after_the_freeze_stops_the_sampler(tmp_path):
    cases = [dict(_case("c%d" % i), prompt="prompt %d" % i) for i in range(8)]
    write_jsonl(tmp_path / "corpus.jsonl", cases)
    splitmod.freeze(tmp_path / "corpus.jsonl")
    held = {e["id"] for e in (splitmod.load_lock(tmp_path) or {})["holdout"]}
    assert held, "the fixture froze nothing"
    moved = [dict(c, prompt="EDITED") if c["id"] in held else c for c in cases]
    write_jsonl(tmp_path / "corpus.jsonl", moved)
    with pytest.raises(ValueError) as exc:
        sample_mod.train_cases(tmp_path)
    assert "refusing to sample" in str(exc.value)


# --- step 3: the grader will not stamp a run with a prompt it never saw -------

def test_grade_refuses_completions_generated_from_another_prompt(tmp_path, capsys):
    from pipeline.evaluate import prompt_signature
    comps = tmp_path / "completions.jsonl"
    header = {"model": "arm", "prompt_sha": "0000dead0000beef",
              "sampling": {"enable_thinking": False, "max_tokens": 2048, "samples": 1,
                           "temperature": 1.0, "top_p": 0.95}}
    write_jsonl(comps, [header, {"case_id": "whatever", "sample": 0, "text": ""}])
    rc = nt.main(["grade", "r1", "--completions", str(comps)])
    assert rc == 1
    err = capsys.readouterr().err
    assert prompt_signature() in err and "0000dead0000beef" in err


def test_a_matching_prompt_sha_is_not_what_stops_the_grade(tmp_path, capsys):
    """The refusal above must be about the prompt and nothing else: with the
    right sha the command gets past it and fails on the next thing instead."""
    from pipeline.evaluate import prompt_signature
    comps = tmp_path / "completions.jsonl"
    header = {"model": "arm", "prompt_sha": prompt_signature(),
              "sampling": {"enable_thinking": False, "max_tokens": 2048, "samples": 1,
                           "temperature": 1.0, "top_p": 0.95}}
    write_jsonl(comps, [header, {"case_id": "not-a-real-case", "sample": 0, "text": ""}])
    rc = nt.main(["grade", "r1", "--completions", str(comps)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "prompt" not in err.lower() or "frozen held-out split" in err


# --- step 3: the arm guard reads a field, so a field has to be written --------

def test_the_generator_states_the_stack_the_arm_guard_compares(tmp_path):
    """`stats._arm` withholds a delta on differing `backend.base_url`. It reads a
    field; if the GPU script never writes one the guard is inert and would NOT
    have fired had the two arms really come off different stacks."""
    src = (Path(__file__).resolve().parents[1] / "gpu" / "lypning_lora.py").read_text()
    assert "base_url=stack" in src, "the completions header no longer names its stack"
    assert 'stack = "incontainer://' in src


def test_two_arms_off_one_stack_compare_and_two_stacks_do_not():
    from pipeline import stats
    one = {"run_id": "base", "backend": {"base_url": "incontainer://s", "model": "a"}}
    two = {"run_id": "tuned", "backend": {"base_url": "incontainer://s", "model": "b"}}
    assert stats._arm(one, two) == []
    other = {"run_id": "tuned", "backend": {"base_url": "https://router/v1", "model": "b"}}
    assert [r["field"] for r in stats._arm(one, other)] == ["backend.base_url"]


# --- step 2: what the adapter is put on, and why -----------------------------

def test_out_proj_is_adapted_and_only_the_vision_tower_is_excluded():
    """Qwen3.5 calls out_proj as a module (`output = self.out_proj(core_attn_out)`,
    transformers modeling_qwen3_5.py:662, declared nn.Linear at :540); Nemotron
    passes the weight into a kernel, which is where the exclusion came from and
    where it belongs. Excluding it here froze the 48 widest projections in the
    text tower."""
    names = _gpu_constants()
    targets, excludes = names["TARGET_MODULES"], names["EXCLUDE_MODULES"]
    assert "out_proj" in targets
    assert excludes == [".*visual.*"], excludes
    assert not any("out_proj" in e for e in excludes)


def test_the_smoke_test_proves_every_targeted_leaf_reaches_the_loss():
    """The exclusion was a belief that was wrong for this model for a day. What
    replaced it has to be a measurement, taken before the checkpoint downloads."""
    src = (Path(__file__).resolve().parents[1] / "gpu" / "lypning_lora.py").read_text()
    assert "carried NO gradient" in src
    assert "def smoke(" in src and src.index("def smoke(") < src.index("carried NO gradient")
