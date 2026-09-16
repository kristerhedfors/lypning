"""The bridge between the schema-3 eval-2 bank and the legacy `nt` tree.

Each rule in `eval2_legacy` exists because the alternative silently changes what
the legacy tree measures; these pin the rules, and one test runs the projected
records through the real harvest gates so the shape is known to survive them.
"""
from __future__ import annotations

import importlib
import json

import pytest

from pipeline import eval2_legacy as L
from pipeline import eval2_rows as R
from pipeline.adapters import jsonl_adapter
from pipeline.jsonio import read_jsonl, write_jsonl
from pipeline.schema import make_case
from pipeline.training_metrics import paired_comparison, summarize


def bank_case(cid, *, family="fam-" + "x", group="grp", population="coverage",
              caps=("stdin",), tests=None):
    return {
        "case_id": cid, "family": family, "source_group": group,
        "capabilities": list(caps), "population": population,
        "task": "Read integers from stdin, one per line, and print their sum. (%s)" % cid,
        "reference": "import sys\nprint(sum(int(l) for l in sys.stdin if l.strip()))",
        "provenance": "test",
        "tests": tests if tests is not None else [
            {"stdin": "1\n2\n3\n", "stdout": "6\n"},
            {"stdin": "", "stdout": "0\n"},
            {"stdin": "-4\n", "stdout": "-4\n"},
        ],
        "review": {"origin": "authored", "evidence_ids": ["ev-" + cid]},
    }


# --- projection rules ---------------------------------------------------------


def test_one_record_per_case_from_the_first_test_with_output():
    rec = L.to_record(bank_case("c1", tests=[
        {"stdin": "x\n", "stdout": ""},           # empty: the discriminates gate drops it
        {"stdin": "1\n2\n", "argv": ["a"], "files": {"f.txt": "z"}, "stdout": "3\n"},
        {"stdin": "9\n", "stdout": "9\n"},
    ]))
    assert rec["id"] == "c1"
    assert rec["category"] == "unobserved"
    t = rec["test"]
    assert t["kind"] == "stdout" and t["normalize"] == "exact"
    assert "require_tier1" not in t and t["kind"] != "lypning"
    assert t["expect_stdout"] == "3\n"
    assert t["stdin"] == "1\n2\n" and t["argv"] == ["a"] and t["files"] == {"f.txt": "z"}
    assert rec["prompt"].startswith("Read integers")
    assert rec["reference"].startswith("import sys")


def test_tags_carry_the_bank_identity_and_read_back():
    rec = L.to_record(bank_case("c2", family="f2", group="g2", population="fallback-control",
                                caps=("argv", "files")))
    assert set(rec["tags"]) == {"family:f2", "group:g2", "population:fallback-control",
                                "capability:argv", "capability:files"}
    back = L.read_tags(rec["tags"])
    assert back == {"family": "f2", "population": "fallback-control",
                    "split_group": "g2", "capabilities": ["argv", "files"]}


def test_a_case_with_no_non_empty_stdout_is_skipped_with_a_reason():
    out = L.project([bank_case("ok"),
                     bank_case("blank", tests=[{"stdin": "a", "stdout": ""}]),
                     {"case_id": "notask", "tests": [{"stdout": "x"}]}])
    assert [r["id"] for r in out["records"]] == ["ok"]
    assert [(s["case_id"], s["reason"]) for s in out["skipped"]] == [
        ("blank", "no test with a non-empty stdout"), ("notask", "no task")]
    assert out["cases"] == 3


def test_the_jsonl_adapter_reads_a_projected_record_unchanged(tmp_path):
    """The adapter must not re-derive anything: category, test and id pass through."""
    path = tmp_path / "records.jsonl"
    write_jsonl(path, L.project([bank_case("c3")])["records"])
    cands = list(jsonl_adapter({"name": "jsonl", "path": str(path)}))
    assert len(cands) == 1
    c = cands[0]
    assert c["category"] == "unobserved" and c["source_id"] == "c3"
    assert c["test"]["kind"] == "stdout" and c["test"]["expect_stdout"] == "6\n"
    assert "family:fam-x" in c["tags"]


def test_projected_records_survive_the_real_harvest_gates(tmp_path):
    """End to end through `harvest`: the reference passes, the empty program fails,
    and the bank's case_id lands as source_id under a hashed corpus id."""
    from pipeline.harvest import harvest
    path = tmp_path / "records.jsonl"
    write_jsonl(path, L.project([bank_case("h1"), bank_case("h2", family="fam-y")])["records"])
    out = tmp_path / "data"
    ledger = harvest([{"name": "jsonl", "path": str(path)}], out_dir=out, jobs=1)
    assert ledger["kept"] == 2 and ledger["dropped"] == 0, ledger
    corpus = read_jsonl(out / "corpus.jsonl")
    assert sorted(c["source_id"] for c in corpus) == ["h1", "h2"]
    assert all(c["id"].startswith("ntx-") and c["category"] == "unobserved" for c in corpus)


def test_the_cli_verb_writes_records_and_names_what_it_skipped(tmp_path, capsys):
    from pipeline import cli
    bank = tmp_path / "bank.jsonl"
    write_jsonl(bank, [bank_case("v1"), bank_case("v2", tests=[{"stdin": "", "stdout": ""}])])
    out = tmp_path / "records.jsonl"
    rc = cli.main(["eval2-legacy", "--bank", str(bank), "--output", str(out)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "projected 1 of 2 cases" in text and "skipped v2" in text
    assert [r["id"] for r in read_jsonl(out)] == ["v1"]
    assert cli.main(["eval2-legacy", "--bank", str(tmp_path / "missing"), "--output", str(out)]) == 2


# --- rows ---------------------------------------------------------------------


def _corpus_case(cid, tags):
    return dict(make_case(prompt="p " + cid, test={"kind": "stdout", "expect_stdout": "6\n"},
                          category="unobserved", source_id=cid, tags=tags), id="ntx-" + cid)


def test_rows_read_native_off_the_replay_and_correct_off_the_run():
    cases = {"ntx-a": _corpus_case("a", ["family:fa", "group:ga", "population:coverage",
                                        "capability:stdin"]),
             "ntx-b": _corpus_case("b", ["family:fb", "population:fallback-control"])}
    attempts = [
        {"case_id": "ntx-a", "sample": 0, "program": "p", "passed": True,
         "completion_tokens": 10, "finish_reason": "stop"},
        {"case_id": "ntx-a", "sample": 1, "program": "p", "passed": True,
         "completion_tokens": 12, "finish_reason": "stop"},
        {"case_id": "ntx-b", "sample": 0, "program": "p", "passed": False,
         "completion_tokens": 300, "finish_reason": "length"},
        {"case_id": "ntx-b", "sample": 1, "harness_error": "HTTP 500", "passed": False},
    ]
    replay = [
        {"case_id": "ntx-a", "sample": 0, "verdict": "MATCH", "correct": True},
        {"case_id": "ntx-a", "sample": 1, "verdict": "UNSUPPORTED", "correct": None},
        {"case_id": "ntx-b", "sample": 0, "verdict": "MATCH", "correct": False},
    ]
    out = R.rows(attempts, replay, cases, seed=1234)
    rows = out["rows"]
    assert out["unknown_cases"] == [] and out["replayed"] == 3
    a0, a1, b0, b1 = rows
    assert (a0["case_id"], a0["draw"], a0["family"], a0["split_group"]) == ("a", 0, "fa", "ga")
    assert a0["population"] == "coverage" and a0["capabilities"] == ["stdin"]
    assert a0["correct"] is True and a0["native"] is True and a0["status"] == "correct-native"
    assert a0["seed"] == 1234 and a1["seed"] == 1235
    # Correct on CPython, refused by the engine: correct, not native.
    assert a1["correct"] is True and a1["native"] is False and a1["status"] == "correct-fallback"
    # No group tag: the family is its own split group, the same default as the GPU path.
    assert b0["split_group"] == "fb" and b0["population"] == "fallback-control"
    assert b0["truncated"] is True and b0["completion_tokens"] == 300
    assert b0["correct"] is False and b0["native"] is False and b0["status"] == "incorrect"
    # A harness error is still a draw the arm was budgeted, so the counts stay equal.
    assert b1["status"] == "harness-error" and b1["verdict"] is None and b1["native"] is False
    s = summarize(rows)
    assert s["draws"] == 4 and s["cases"] == 2 and s["families"] == 2
    assert s["correct"] == pytest.approx(0.5) and s["correct_native"] == pytest.approx(0.25)
    assert s["by_population"]["coverage"]["correct_native"] == pytest.approx(0.5)
    assert s["by_capability"]["stdin"]["draws"] == 2


def test_rows_from_two_runs_pair_through_the_shared_summariser():
    cases = {"ntx-a": _corpus_case("a", ["family:fa", "group:g"]),
             "ntx-b": _corpus_case("b", ["family:fb", "group:g"])}
    att = [{"case_id": c, "sample": i, "program": "p", "passed": True}
           for c in ("ntx-a", "ntx-b") for i in range(2)]
    base = R.rows(att, [dict(a, verdict="UNSUPPORTED", correct=None) for a in att], cases)["rows"]
    cand = R.rows(att, [dict(a, verdict="MATCH", correct=True) for a in att], cases)["rows"]
    cmp = paired_comparison(base, cand, resamples=100)
    assert cmp["metrics"]["native"]["delta"] == pytest.approx(1.0)
    assert cmp["metrics"]["correct"]["delta"] == pytest.approx(0.0)
    assert cmp["independent_clusters"] == 1


def test_a_resumed_run_keeps_one_row_per_draw_and_the_real_attempt_wins():
    """`nt eval --run-id` redraws harness errors and appends: the draw that
    failed twice and then reached the model is one draw, and a correct one."""
    cases = {"ntx-a": _corpus_case("a", ["family:fa"])}
    attempts = [
        {"case_id": "ntx-a", "sample": 0, "harness_error": "HTTP 500", "passed": False},
        {"case_id": "ntx-a", "sample": 1, "program": "p", "passed": True, "finish_reason": "stop"},
        {"case_id": "ntx-a", "sample": 0, "harness_error": "HTTP 500 again", "passed": False},
        {"case_id": "ntx-a", "sample": 0, "program": "p", "passed": True, "finish_reason": "stop"},
        {"case_id": "ntx-a", "sample": 1, "harness_error": "a late 500", "passed": False},
    ]
    out = R.rows(attempts, [{"case_id": "ntx-a", "sample": 0, "verdict": "MATCH", "correct": True}], cases)
    assert out["superseded"] == 3
    assert [(r["draw"], r["status"]) for r in out["rows"]] == [(0, "correct-native"), (1, "correct-fallback")]
    still_failing = attempts[:1] + attempts[2:3]
    assert R.rows(still_failing, [], cases)["rows"][0]["status"] == "harness-error", "no real draw yet: still one row"


def test_an_attempt_for_a_case_this_tree_does_not_know_is_reported_not_invented():
    out = R.rows([{"case_id": "ntx-zzz", "sample": 0, "program": "p", "passed": True}], [], {})
    assert out["rows"] == [] and out["unknown_cases"] == ["ntx-zzz"]


def test_the_rows_verb_replays_once_and_prints_the_family_macro(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NTX_ROOT", str(tmp_path))
    from pipeline import cli, legality
    importlib.reload(cli)
    try:
        (tmp_path / "data").mkdir()
        write_jsonl(tmp_path / "data" / "corpus.jsonl",
                    [_corpus_case("a", ["family:fa", "group:g", "population:coverage"]),
                     _corpus_case("b", ["family:fb", "group:g", "population:coverage"])])
        run = tmp_path / "runs" / "r1"
        run.mkdir(parents=True)
        (run / "meta.json").write_text(json.dumps({"sampling": {"seed": 7}}))
        write_jsonl(run / "attempts.jsonl", [
            {"case_id": "ntx-a", "sample": 0, "program": "p", "passed": True},
            {"case_id": "ntx-b", "sample": 0, "program": "p", "passed": False}])
        seen = {}

        def fake_replay(attempts, engine, tests=None, workers=1, cache=None):
            seen["tests"] = tests
            seen["engine"] = engine
            return {"tally": {"MATCH": 2}, "rows": [
                {"case_id": "ntx-a", "sample": 0, "verdict": "MATCH", "correct": True},
                {"case_id": "ntx-b", "sample": 0, "verdict": "MATCH", "correct": False}]}

        monkeypatch.setattr(legality, "replay", fake_replay)
        monkeypatch.setattr(cli.eng, "identity", lambda: {"fingerprint": "fp"})
        out = tmp_path / "rows.jsonl"
        rc = cli.main(["eval2-rows", "r1", "--engine", "/bin/true", "--output", str(out)])
        assert rc == 0
        text = capsys.readouterr().out
        assert "2 rows, 2 replayed" in text and "correct  50.0%" in text
        assert "correct-native  50.0%" in text
        assert seen["engine"] == "/bin/true" and "ntx-a" in seen["tests"]
        rows = read_jsonl(out)
        assert [r["seed"] for r in rows] == [7, 7]
        assert [r["native"] for r in rows] == [True, False]
        assert cli.main(["eval2-rows", "nope", "--engine", "/bin/true",
                         "--output", str(out)]) == 1
    finally:
        monkeypatch.delenv("NTX_ROOT", raising=False)
        importlib.reload(cli)
