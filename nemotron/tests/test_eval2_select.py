"""Eval-2 candidates are drawn by shape, verified by running, never by refusal kind."""
from __future__ import annotations

import json

import pytest

from pipeline import eval2_select as e2s
from pipeline.jsonio import read_jsonl, write_jsonl
from pipeline.sandbox import RunResult


def _rec(program, outcome="tier1", kind="module", stdout="x\n", ident=None, argv=(), stdin=None):
    entry = {"id": ident or ("py-" + str(abs(hash(program)) % 10 ** 8)), "program": program,
             "argv_tail": list(argv), "stdin_sample": stdin, "source": "hook"}
    info = {"engine": "lypning-l"} if outcome == "tier1" else {
        "kind": kind, "detail": kind, "expect_stdout": stdout, "expect_exit": 0}
    return {"entry": entry, "outcome": outcome, "info": info}


class FakeRunner:
    """A stand-in for sandbox.run_python that answers from a table and logs its calls."""

    def __init__(self, table=None, default=None):
        self.table = table or {}
        self.default = default
        self.calls = []

    def __call__(self, program, **kw):
        self.calls.append((program, kw))
        answers = self.table.get(program)
        if answers is None:
            out = self.default if self.default is not None else _echo(program)
            return RunResult(0, out, "", 0.01)
        idx = sum(1 for p, _ in self.calls if p == program) - 1
        exit_code, out = answers[min(idx, len(answers) - 1)]
        return RunResult(exit_code, out, "", 0.01)


def _echo(program):
    # `print("abc")` -> "abc\n": enough of an interpreter for a deterministic double.
    inner = program.strip()[len("print("):-1].strip("'\"")
    return inner + "\n"


# --- the rules ---------------------------------------------------------------


@pytest.mark.parametrize("program, rule", [
    ("import lypning\nprint(1)", "mentions-tooling"),
    ("print('ntx harness')", "mentions-tooling"),
    ("print(open('/etc/hostname').read())", "absolute-path"),
    ("open('out.txt', 'w').write('x')\nprint(1)", "writes-files"),
    ("import os\nos.remove(name)\nprint(1)", "writes-files"),
    ("from pathlib import Path\nPath(n).write_text('x')\nprint(1)", "writes-files"),
    ("import subprocess\nprint(subprocess.run(['true']))", "spawns-subprocess"),
    ("import os\nprint(os.system('true'))", "spawns-subprocess"),
    ("import socket\nprint(socket.gethostname())", "reads-network"),
    ("from urllib.request import urlopen\nprint(urlopen)", "reads-network"),
    ("import random\nprint(random.random())", "nondeterministic"),
    ("import time\nprint(time.time())", "nondeterministic"),
    ("import os\nprint(os.getpid())", "nondeterministic"),
    ("print(hash('a'))", "nondeterministic"),
])
def test_each_named_rule_rejects_its_program(program, rule):
    assert e2s.static_rule(_rec(program)) == rule


def test_the_nondeterminism_detector_is_handed_a_record_not_a_dict():
    # lypning.conformance reads `.program`; a dict given to it directly reads
    # as an empty program and waives nothing, so the wrap has to be there.
    assert e2s.is_nondeterministic({"program": "import time\nprint(time.time())", "argv_tail": []})
    assert not e2s.is_nondeterministic({"program": "print(2 + 2)", "argv_tail": []})


def test_str_replace_is_not_a_file_write():
    assert e2s.static_rule(_rec("print('a-b'.replace('-', '+'))")) == ""


def test_refused_with_empty_recorded_stdout_and_other_outcomes_are_excluded():
    assert e2s.static_rule(_rec("pass", outcome="refused", stdout="")) == "empty-stdout"
    assert e2s.static_rule(_rec("print(1)", outcome="unusable")) == "outcome"
    assert e2s.static_rule(_rec("print(1)", outcome="skip")) == "outcome"


# --- regeneration ------------------------------------------------------------


def test_regenerate_runs_twice_and_the_second_run_moves_the_hash_seed():
    runner = FakeRunner()
    stdout, why = e2s.regenerate({"program": "print('hi')", "argv_tail": ["a"], "stdin_sample": "s"},
                                 runner=runner)
    assert (stdout, why) == ("hi\n", "")
    assert len(runner.calls) == 2
    first, second = runner.calls
    assert first[1]["argv"] == ["a"] and first[1]["stdin"] == "s"
    assert "env_extra" not in first[1]
    assert second[1]["env_extra"] == {"PYTHONHASHSEED": e2s.ALT_HASH_SEED}


def test_regenerate_rejects_a_failed_run_and_disagreeing_runs():
    failing = FakeRunner({"print('a')": [(1, "")]})
    assert e2s.regenerate({"program": "print('a')"}, runner=failing) == (None, "run-failed")
    flaky = FakeRunner({"print('a')": [(0, "1\n"), (0, "2\n")]})
    assert e2s.regenerate({"program": "print('a')"}, runner=flaky) == (None, "runs-disagree")


def test_refused_entries_are_held_to_their_recorded_stdout():
    recs = [_rec("print('same')", outcome="refused", stdout="same\n", ident="a"),
            _rec("print('moved')", outcome="refused", stdout="old\n", ident="b")]
    result = e2s.select(recs, runner=FakeRunner())
    assert [c["source_entry_id"] for c in result["candidates"]] == ["a"]
    assert result["excluded"] == {"stdout-drift": 1}
    assert result["candidates"][0]["stdout"] == "same\n"


def test_tier1_entries_get_regenerated_stdout_and_empty_output_is_excluded():
    recs = [_rec("print('v')", ident="a"), _rec("print('')", ident="b")]
    result = e2s.select(recs, runner=FakeRunner())
    assert [c["source_entry_id"] for c in result["candidates"]] == ["a"]
    assert result["candidates"][0]["stdout"] == "v\n"
    assert result["excluded"] == {"empty-stdout": 1}


# --- dedupe, shape, provenance -------------------------------------------------


def test_duplicates_by_normalized_text_keep_the_first():
    recs = [_rec("print('x')\n", ident="first"), _rec("  print('x')  \n\n", ident="second")]
    result = e2s.select(recs, runner=FakeRunner())
    assert [c["source_entry_id"] for c in result["candidates"]] == ["first"]
    assert result["excluded"] == {"duplicate": 1}


def test_shape_bucket_is_lines_and_token_flags_and_never_the_refusal_kind():
    assert e2s.shape_bucket("print(1)") == "1:-"
    assert e2s.shape_bucket("import os\nprint(os.getcwd)") == "2-3:I"
    assert e2s.shape_bucket("def f(x):\n    return x\nfor i in range(3):\n    if i:\n        print(f(i))") == "4-8:DLB"
    assert e2s.shape_bucket("\n".join("print(%d)" % i for i in range(30))) == "21+:-"
    a = _rec("import csv\nprint(csv)", outcome="refused", kind="module", ident="a")
    b = _rec("import csv\nprint(csv)", outcome="refused", kind="bigint", ident="a")
    assert e2s.shape_bucket(a["entry"]["program"]) == e2s.shape_bucket(b["entry"]["program"])


def test_refusal_kind_is_recorded_but_changes_nothing_about_the_draw():
    def corpus(kind):
        return [_rec("print(%d)" % i, outcome="refused", kind=kind, stdout="%d\n" % i,
                     ident="r%d" % i) for i in range(12)]
    one = e2s.select(corpus("module"), limit=5, seed=3, runner=FakeRunner())
    other = e2s.select(corpus("set-order"), limit=5, seed=3, runner=FakeRunner())
    assert [c["source_entry_id"] for c in one["candidates"]] == \
        [c["source_entry_id"] for c in other["candidates"]]
    assert {c["refusal_kind"] for c in one["candidates"]} == {"module"}
    assert {c["refusal_kind"] for c in other["candidates"]} == {"set-order"}


def test_draw_is_deterministic_stratified_and_independent_of_input_order():
    recs = [_rec("print(%d)" % i, ident="one%d" % i) for i in range(20)]           # 1:-
    recs += [_rec("import os\nprint(%d)" % i, ident="two%d" % i) for i in range(10)]  # 2-3:I
    a = e2s.select(recs, limit=9, seed=7, runner=FakeRunner())
    b = e2s.select(list(reversed(recs)), limit=9, seed=7, runner=FakeRunner())
    assert a["candidates"] == b["candidates"]
    assert a["selected"] == 9 and a["by_bucket"] == {"1:-": 6, "2-3:I": 3}
    c = e2s.select(recs, limit=9, seed=8, runner=FakeRunner())
    assert c["by_bucket"] == a["by_bucket"]
    assert sum(a["excluded"].values()) == 0 and a["eligible"] == 30


def test_record_shape_and_source_sha256_match_evidence(tmp_path):
    from lypning import evidence
    program = "import os\nprint('ünïcode', os.sep)"
    sightings = tmp_path / "sightings"
    sightings.mkdir()
    (sightings / "sess-1.jsonl").write_text(json.dumps({"key": "py-1", "id": "py-1"}) + "\n")
    result = e2s.select([_rec(program, ident="py-1", argv=("a",))], sightings_dir=sightings,
                        runner=FakeRunner(default="ünïcode /\n"))
    (cand,) = result["candidates"]
    assert set(cand) == {"candidate_id", "source_entry_id", "session_file", "program", "argv",
                         "stdin", "stdout", "exit_code", "outcome", "refusal_kind",
                         "shape_bucket", "source_sha256"}
    assert cand["source_sha256"] == evidence.digest(program.encode("utf-8"))
    assert cand["session_file"] == "sess-1.jsonl"
    assert cand["exit_code"] == 0 and cand["outcome"] == "tier1" and cand["refusal_kind"] is None
    assert cand["argv"] == ["a"] and cand["candidate_id"].startswith("e2-")


def test_session_index_prefers_the_lexically_first_file(tmp_path):
    (tmp_path / "b.jsonl").write_text('{"key":"py-1"}\n')
    (tmp_path / "a.jsonl").write_text('{"key":"py-1"}\n{"key":"py-2"}\n')
    assert e2s.session_index(tmp_path) == {"py-1": "a.jsonl", "py-2": "a.jsonl"}
    assert e2s.session_index(None) == {}


# --- the verb ----------------------------------------------------------------


def test_cli_verb_runs_the_real_sandbox_writes_jsonl_and_prints_counts(tmp_path, capsys):
    from pipeline import cli
    classified = tmp_path / "classified.jsonl"
    write_jsonl(classified, [
        _rec("print('alpha')", ident="a"),
        _rec("import sys\nprint(sys.argv[1])", outcome="refused", kind="module",
             stdout="beta\n", ident="b", argv=("beta",)),
        _rec("import time\nprint(time.time())", ident="c"),
        _rec("print('alpha')", ident="d"),
    ])
    out = tmp_path / "cands.jsonl"
    rc = cli.main(["eval2-select", "--output", str(out), "--classified", str(classified),
                   "--sightings", str(tmp_path / "none"), "--seed", "1", "--jobs", "1"])
    assert rc == 0
    rows = read_jsonl(out)
    assert {r["source_entry_id"]: r["stdout"] for r in rows} == {"a": "alpha\n", "b": "beta\n"}
    text = capsys.readouterr().out
    assert "classified 4 loaded   eligible 2   selected 2" in text
    assert "nondeterministic" in text and "duplicate" in text
    assert "1:-" in text and "2-3:I" in text and "tier1" in text and "refused" in text
