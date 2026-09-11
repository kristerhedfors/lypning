"""The two drivers: what the harvester merges, and what the eval refuses to count."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import split as S
from pipeline.adapters import _study_files, _test_from_convenience, parse_source
from pipeline.evaluate import (SYSTEM_PROMPT, load_holdout, prompt_signature,
                               render_contract, render_messages, summarize_run)
from pipeline.harvest import collect, harvest
from pipeline.jsonio import read_json, read_jsonl, write_jsonl
from pipeline.schema import case_id, make_case, validate_case

REF = "print(sum(n for n in range(1,101) if n%2==0))"
BAD = "print(2551)"


def _src(tmp_path, rows, name="jsonl"):
    p = tmp_path / "in.jsonl"
    write_jsonl(p, rows)
    return {"name": name, "path": str(p)}


def test_a_candidate_with_no_test_is_dropped_not_patched(tmp_path):
    cases, drops = collect([_src(tmp_path, [{"prompt": "do a thing", "reference": REF}])])
    assert cases == []
    assert drops[0]["gate"] == "no-test"


def test_the_same_prompt_and_test_from_two_sources_merge(tmp_path):
    rows = [
        {"prompt": "p", "expect_stdout": "2550\n", "reference": REF,
         "failing_program": BAD, "category": "wrong-output"},
        {"prompt": "p", "expect_stdout": "2550\n", "failing_program": "print(0)",
         "category": "runtime-error"},
    ]
    cases, _ = collect([_src(tmp_path, rows)])
    assert len(cases) == 1
    assert len(cases[0]["negatives"]) == 2
    assert cases[0]["reference"] == REF


def test_an_observed_category_beats_unobserved_on_merge(tmp_path):
    rows = [{"prompt": "p", "expect_stdout": "x\n", "category": "unobserved"},
            {"prompt": "p", "expect_stdout": "x\n", "category": "timeout"}]
    cases, _ = collect([_src(tmp_path, rows)])
    assert cases[0]["category"] == "timeout"


def test_harvest_writes_a_ledger_that_accounts_for_every_candidate(tmp_path):
    rows = [
        {"prompt": "good", "expect_stdout": "2550\n", "reference": REF, "failing_program": BAD},
        {"prompt": "vacuous", "expect_stdout_re": ".*", "expect_exit": None,
         "reference": REF, "failing_program": BAD},
        {"prompt": "no test at all", "reference": REF},
    ]
    led = harvest([_src(tmp_path, rows)], out_dir=tmp_path / "out", jobs=2)
    assert led["kept"] == 1 and led["dropped"] == 2
    assert led["drops_by_gate"] == {"discriminates": 1, "no-test": 1}
    assert led["candidates"] == led["kept"] + led["dropped"]
    kept = read_jsonl(tmp_path / "out" / "corpus.jsonl")
    assert validate_case(kept[0]) is None and kept[0]["gates"]["stable"] == "ok"


def test_harvest_is_a_pure_function_of_its_input(tmp_path):
    rows = [{"prompt": "p%d" % i, "expect_stdout": "%d\n" % i,
             "reference": "print(%d)" % i} for i in range(6)]
    a = harvest([_src(tmp_path, rows)], out_dir=tmp_path / "a", jobs=3)
    b = harvest([_src(tmp_path, rows)], out_dir=tmp_path / "b", jobs=1)
    assert a["corpus_sha256"] == b["corpus_sha256"]
    assert (tmp_path / "a" / "corpus.jsonl").read_bytes() == \
           (tmp_path / "b" / "corpus.jsonl").read_bytes()


def test_field_mapping_reshapes_a_foreign_export(tmp_path):
    spec = parse_source("jsonl:path=%s,map=prompt->task.text" % (tmp_path / "in.jsonl"))
    write_jsonl(tmp_path / "in.jsonl",
                [{"task": {"text": "p"}, "expect_stdout": "2550\n", "reference": REF,
                  "failing_program": BAD}])
    cases, _ = collect([spec])
    assert cases[0]["prompt"] == "p"


def test_test_kind_is_inferred_only_from_what_is_actually_there():
    assert _test_from_convenience({"expect_stdout": "x"})["kind"] == "stdout"
    assert _test_from_convenience({"checker": "pass"})["kind"] == "script"
    assert _test_from_convenience({"test_file": "def test_x(): pass"})["kind"] == "pytest"
    assert _test_from_convenience({"reference": "print(1)"}) is None


def test_study_binary_files_become_explicit_base64():
    out = _study_files({"b.bin": "\x00\x01\xfe\xff", "t.txt": "plain"})
    assert out["b.bin"] == {"base64": "AAH+/w=="}
    assert out["t.txt"] == "plain"


# ------------------------------------------------------------------- eval


def test_the_contract_tells_the_model_the_truth_about_the_run():
    c = render_contract({"kind": "stdout", "stdin": "x", "argv": ["a.txt"],
                         "files": {"in.csv": "1"}, "expect_stdout": ""})
    assert "solution.py a.txt" in c and "standard input" in c and "in.csv" in c
    bare = render_contract({"kind": "stdout", "expect_stdout": ""})
    assert "standard input" not in bare


def test_the_prompt_is_hashed_so_a_change_is_visible():
    assert prompt_signature() == prompt_signature()
    assert len(prompt_signature()) == 16
    msgs = render_messages(make_case(prompt="do it",
                                     test={"kind": "stdout", "expect_stdout": "x"}))
    assert msgs[0]["content"] == SYSTEM_PROMPT and "do it" in msgs[1]["content"]


def test_eval_refuses_to_measure_a_drifted_holdout(tmp_path):
    cases = [make_case(prompt="p%d" % i, test={"kind": "stdout", "expect_stdout": "%d\n" % i},
                       reference="print(%d)" % i, category="wrong-output") for i in range(10)]
    write_jsonl(tmp_path / "corpus.jsonl", cases)
    S.freeze(tmp_path / "corpus.jsonl")
    assert len(load_holdout(tmp_path)) == 3
    cases[0]["test"]["expect_stdout"] = "tampered\n"
    write_jsonl(tmp_path / "corpus.jsonl", cases)
    if any(e["id"] == cases[0]["id"] for e in read_json(tmp_path / "holdout.lock.json")["holdout"]):
        with pytest.raises(ValueError, match="refusing to measure"):
            load_holdout(tmp_path)


def test_harness_errors_leave_the_denominator_rather_than_scoring_zero(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    write_jsonl(run / "attempts.jsonl", [
        {"case_id": "a", "sample": 0, "passed": True, "cost_usd": 0.1},
        {"case_id": "b", "sample": 0, "passed": False, "failure_category": "timeout"},
        {"case_id": "c", "sample": 0, "passed": False, "harness_error": "HTTP 503"},
    ])
    s = summarize_run(run)
    assert s["cases_evaluated"] == 2 and s["pass_rate"] == 0.5
    assert s["harness_errors"] == 1
    assert s["failures_by_category"] == {"timeout": 1}


def test_multiple_samples_average_within_a_case(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    write_jsonl(run / "attempts.jsonl", [
        {"case_id": "a", "sample": 0, "passed": True},
        {"case_id": "a", "sample": 1, "passed": False},
        {"case_id": "b", "sample": 0, "passed": True},
        {"case_id": "b", "sample": 1, "passed": True},
    ])
    s = summarize_run(run)
    assert s["cases_evaluated"] == 2 and s["pass_rate"] == 0.75


def test_a_resumed_run_carries_its_earlier_gpu_spend(tmp_path):
    from pipeline.backends import ChatBackend
    from pipeline.evaluate import Evaluation
    from pipeline.jsonio import write_json
    run = tmp_path / "r"
    run.mkdir()
    write_json(run / "progress.json", {"spend_gpu_usd": 4.25})
    ev = Evaluation(ChatBackend("http://x/v1", "m"), [], run, price_hour=3.69)
    assert ev._gpu_cost() >= 4.25


def test_the_sampler_refuses_to_touch_the_frozen_holdout(tmp_path):
    from pipeline import split as S
    from pipeline.sample import train_cases
    cases = [make_case(prompt="p%d" % i, test={"kind": "stdout", "expect_stdout": "%d\n" % i},
                       reference="print(%d)" % i, category="wrong-output") for i in range(20)]
    write_jsonl(tmp_path / "corpus.jsonl", cases)
    lock = S.freeze(tmp_path / "corpus.jsonl")
    held = {e["id"] for e in lock["holdout"]}
    train = train_cases(tmp_path)
    assert train and not ({c["id"] for c in train} & held)
    assert len(train) == 20 - len(held)


def test_sampling_without_a_frozen_split_is_refused(tmp_path):
    from pipeline.sample import train_cases
    write_jsonl(tmp_path / "corpus.jsonl", [make_case(
        prompt="p", test={"kind": "stdout", "expect_stdout": "x"}, category="timeout")])
    with pytest.raises(ValueError, match="not frozen"):
        train_cases(tmp_path)


def test_the_literal_output_guard_catches_printing_the_answer():
    from pipeline.sample import looks_like_literal_output as L
    assert L('print("alpha 1\\nbeta 22\\ngamma 333")', "alpha 1\nbeta 22\ngamma 333")
    assert not L("print(6*7)", "42")
    assert not L("print(sum(range(10)))", "45")
