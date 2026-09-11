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
