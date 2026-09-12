"""Frozen has to mean checkable, or it means nothing."""
from __future__ import annotations

import json

import pytest

from pipeline import split as S
from pipeline.jsonio import read_json, write_jsonl
from pipeline.schema import make_case

CATS = ["wrong-output", "runtime-error", "timeout", "no-code"]


def _corpus(tmp_path, n=40):
    cases = [
        make_case(prompt="task %d" % i,
                  test={"kind": "stdout", "expect_stdout": "%d\n" % i},
                  reference="print(%d)" % i, category=CATS[i % len(CATS)])
        for i in range(n)
    ]
    p = tmp_path / "corpus.jsonl"
    write_jsonl(p, cases)
    return p, cases


def test_split_is_deterministic_and_independent_of_input_order(tmp_path):
    p, cases = _corpus(tmp_path)
    a = S._stratified(cases, 0.30, S.DEFAULT_SALT)
    b = S._stratified(list(reversed(cases)), 0.30, S.DEFAULT_SALT)
    assert a == b


def test_split_is_stratified_at_thirty_percent(tmp_path):
    p, cases = _corpus(tmp_path, 40)
    lock = S.freeze(p)
    assert lock["n_holdout"] == 12 and lock["n_train"] == 28
    for cat, s in lock["strata"].items():
        assert s["holdout"] == 3, cat


def test_freezing_twice_does_not_move_the_holdout(tmp_path):
    p, _ = _corpus(tmp_path)
    first = S.freeze(p)
    again = S.freeze(p)
    assert first["manifest_sha256"] == again["manifest_sha256"]


def test_growth_after_the_freeze_lands_in_train(tmp_path):
    p, cases = _corpus(tmp_path, 40)
    first = S.freeze(p)
    extra = [make_case(prompt="new %d" % i,
                       test={"kind": "stdout", "expect_stdout": "x\n"},
                       reference="print('x')", category="timeout") for i in range(20)]
    write_jsonl(p, cases + extra)
    after = S.freeze(p)
    assert after["manifest_sha256"] == first["manifest_sha256"]
    counts = S.materialize(p)
    assert counts["holdout"] == 12 and counts["train"] == 48


def test_verify_catches_a_changed_holdout_case(tmp_path):
    p, cases = _corpus(tmp_path)
    lock = S.freeze(p)
    held = lock["holdout"][0]["id"]
    for c in cases:
        if c["id"] == held:
            c["test"]["expect_stdout"] = "tampered\n"
    write_jsonl(p, cases)
    ok, problems = S.verify(p)
    assert not ok and any("changed since the freeze" in x for x in problems)


def test_verify_catches_a_deleted_holdout_case(tmp_path):
    p, cases = _corpus(tmp_path)
    lock = S.freeze(p)
    held = {e["id"] for e in lock["holdout"]}
    write_jsonl(p, [c for c in cases if c["id"] not in held])
    ok, problems = S.verify(p)
    assert not ok and len(problems) == len(held)


def test_dropping_a_held_out_case_blocks_a_plain_refreeze(tmp_path):
    p, cases = _corpus(tmp_path)
    lock = S.freeze(p)
    held = {e["id"] for e in lock["holdout"]}
    write_jsonl(p, [c for c in cases if c["id"] not in held])
    with pytest.raises(ValueError, match="no longer in the corpus"):
        S.freeze(p)
    moved = S.freeze(p, refreeze=True)
    assert moved["manifest_sha256"] != lock["manifest_sha256"]
    assert moved["refrozen_from"] == lock["manifest_sha256"]


def test_verify_without_a_lock_fails_rather_than_inventing_one(tmp_path):
    p, _ = _corpus(tmp_path)
    ok, problems = S.verify(p)
    assert not ok and "never been frozen" in problems[0]


# --- cross-split leakage -----------------------------------------------------


def test_disjoint_ids_are_not_an_independence_proof():
    """The defect this whole check exists for, as the smallest example of it.

    Two cases with different ids, the same question, one in each split. The old
    assertion in `sample.train_cases` filtered by held id and then asserted no
    held id had survived — true however leaky the split is.
    """
    from pipeline import split as splitmod

    train = [
        {"id": "t1", "prompt": "Rewrite this program so it avoids `import math`:\n\nprint(math.sqrt(16))"},
        {"id": "t2", "prompt": "Something else entirely, about reading a csv file by hand."},
    ]
    holdout = [
        {"id": "h1", "prompt": "Rewrite this program so it avoids `import math`:\n\nprint(math.sqrt(25))"},
    ]
    report = splitmod.cross_split_leaks(train, holdout)
    ids = {row["id"] for row in report["leaks"]}
    assert "t1" in ids, "a reworded twin across the split was not caught"
    assert "t2" not in ids, "an unrelated case was dropped"
    assert report["clean"] == ["t2"]


def test_an_identical_expected_output_leaks_even_when_the_prompt_differs():
    from pipeline import split as splitmod

    train = [{"id": "t1", "prompt": "wholly different wording, no overlap at all",
              "test": {"expect_stdout": "4.0 2 3\n"}}]
    holdout = [{"id": "h1", "prompt": "zzzz", "test": {"expect_stdout": "4.0 2 3\n"}}]
    report = splitmod.cross_split_leaks(train, holdout)
    assert [r["id"] for r in report["leaks"]] == ["t1"]
    assert "identical expect_stdout" in report["leaks"][0]["why"]


def test_an_identical_negative_program_leaks():
    from pipeline import split as splitmod

    prog = "import datetime\nprint(datetime.datetime.now().isoformat())"
    train = [{"id": "t1", "prompt": "aaaa", "negatives": [{"program": prog}]}]
    holdout = [{"id": "h1", "prompt": "bbbb", "negatives": [{"program": prog}]}]
    report = splitmod.cross_split_leaks(train, holdout)
    assert [r["id"] for r in report["leaks"]] == ["t1"]
    assert "identical program" in report["leaks"][0]["why"]


def test_the_sampler_cannot_be_handed_a_leaking_case_by_default():
    """`train_cases` excludes them unless asked not to, and the reporter is the
    only caller that asks."""
    from pathlib import Path

    from pipeline.sample import train_cases

    data = Path(__file__).resolve().parents[1] / "data"
    if not (data / "holdout.lock.json").exists():
        pytest.skip("no frozen split in this checkout")
    clean = {c["id"] for c in train_cases(data)}
    everything = {c["id"] for c in train_cases(data, allow_leaks=True)}
    assert clean < everything, "the default path is not excluding anything"
    assert len(everything - clean) == 58, (
        "the number of leaking train cases moved; that is a corpus change and "
        "belongs in a commit that says so (was 58 on 2026-09-12)"
    )
