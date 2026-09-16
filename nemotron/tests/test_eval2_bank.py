"""A proposal becomes a case only by execution; the lint has one home; engine bugs are witnesses."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys

import pytest

from pipeline import eval2_bank as B
from pipeline.jsonio import read_json, read_jsonl, write_jsonl

TASK = ("Read every line from standard input and print each line reversed, one per "
        "line, in the order the lines arrived, followed by a final line giving the "
        "number of lines that were read.")
REFERENCE = ("import sys\n"
             "lines = sys.stdin.read().splitlines()\n"
             "for l in lines:\n"
             "    print(l[::-1])\n"
             "print(len(lines))\n")
SOLVER = ("import sys\n"
          "n = 0\n"
          "for line in sys.stdin:\n"
          "    print(line.rstrip('\\n')[::-1])\n"
          "    n += 1\n"
          "print(n)\n")
TESTS = [
    {"argv": [], "stdin": "ab\ncd\n", "files": {}, "stdout": "ba\ndc\n2\n"},
    {"argv": [], "stdin": "xyz\n", "files": {}, "stdout": "zyx\n1\n"},
    {"argv": [], "stdin": "", "files": {}, "stdout": "0\n"},
]
CID = "e2-candidate0001"


def sha(program):
    return hashlib.sha256(program.encode("utf-8")).hexdigest()


def proposal(**over):
    p = {"candidate_id": CID, "keep": True, "drop_reason": None, "task": TASK,
         "family": "Line Reversal", "capabilities": ["stdin", "strings", "stdin"],
         "reference": REFERENCE, "reference_edit": "verbatim",
         "tests": [dict(t) for t in TESTS]}
    p.update(over)
    return p


def batches(proposals, solutions=None):
    if solutions is None:
        solutions = [{"candidate_id": CID, "program": SOLVER, "ambiguity": "none"}]
    return [{"authored": {"proposals": proposals}, "solved": {"solutions": solutions}}]


def candidate(program=REFERENCE, cid=CID):
    return {"candidate_id": cid, "source_entry_id": "abc123", "session_file": None,
            "program": program, "argv": [], "stdin": "", "stdout": "", "exit_code": 0,
            "outcome": "tier1", "refusal_kind": None, "shape_bucket": "4+:L",
            "source_sha256": sha(program)}


def evidence_dir(tmp_path, program=REFERENCE, name="snap"):
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    write_jsonl(d / "events.jsonl", [
        {"event_id": "ev-one", "source_sha256": sha(program), "quarantine": None, "record": {}},
        {"event_id": "ev-two", "source_sha256": sha(program), "quarantine": None, "record": {}},
        {"event_id": "ev-other", "source_sha256": sha("print(1)"), "quarantine": None},
        {"event_id": "ev-bad", "source_sha256": sha(program), "quarantine": "oversized"},
    ])
    return d


def fake_engine(tmp_path, body, name="fake-lypning-l"):
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def build(tmp_path, bat, engine=sys.executable, cands=None, **kw):
    ev = B.load_evidence([evidence_dir(tmp_path)])
    return B.build(bat, cands if cands is not None else [candidate()], ev, engine, **kw)


def test_clean_proposal_is_admitted_with_computed_stdouts_and_evidence(tmp_path):
    result = build(tmp_path, batches([proposal()]))
    assert result["dropped"] == [] and result["witnesses"] == []
    [case] = result["cases"]
    assert case["case_id"] == B.case_id_for(CID, TASK) and case["case_id"].startswith("e2-")
    assert case["family"] == "line-reversal"
    assert case["capabilities"] == ["stdin", "strings"]
    assert case["source_group"] == sha(REFERENCE)
    assert case["population"] == "coverage"
    assert [t["stdout"] for t in case["tests"]] == [t["stdout"] for t in TESTS]
    assert case["provenance"] == ("captured program py-abc123, session main corpus, "
                                  "reverse-prompted 2026-09-16 by an authoring agent; "
                                  "reference verbatim")
    assert case["review"]["origin"] == "captured"
    assert case["review"]["evidence_ids"] == ["ev-one", "ev-two"]
    assert "flags" not in case
    report = result["report"]
    assert report["admitted"] == 1 and report["solver_agree"] == 1
    assert report["corrected_stdouts"] == 0 and report["populations"]["coverage"] == 1
    assert report["cases"][0]["stdout_corrected"] == []


@pytest.mark.parametrize("task, reason", [
    ("Print the sum of the numbers on stdin, one per line, and make sure the program runs "
     "under lypning as well as anywhere else.", "lint-runtime-name"),
    ("Print the sum of the integers given on standard input, one per line; the runtime "
     "must not be assumed to have anything beyond the basics.", "lint-runtime-name"),
    ("Print the sum of the integers given on standard input, one per line, without "
     "importing anything at all from anywhere in the program.", "lint-runtime-name"),
    ("Print the sum of the integers given on standard input, one per line, using only "
     "the basic builtins that CPython provides out of the box.", "lint-runtime-name"),
    ("Print the sum of the integers given on standard input, one per line; the json "
     "module is not available so parse the lines by hand.", "lint-module-constraint"),
    ("Print the sum of the integers on stdin.", "lint-short"),
    ("Print the sum of the integers given on standard input, one per line, exactly as "
     "in this example:\n```\n1\n2\n```\nand nothing else after it.", "lint-code-fence"),
])
def test_the_lint_rejects_runtime_names_constraints_short_and_fenced_tasks(tmp_path, task, reason):
    result = build(tmp_path, batches([proposal(task=task)]))
    assert result["cases"] == []
    assert [d["reason"] for d in result["dropped"]] == [reason]
    assert result["report"]["dropped_by_reason"] == {reason: 1}


def test_runtime_names_is_the_one_home_of_the_rule():
    labels = {label for label, _ in B.RUNTIME_NAMES}
    for word in ("lypning", "cpython", "micropython", "interpreter", "runtime", "engine",
                 "tier", "refuse", "unsupported", "fallback", "standard library", "stdlib",
                 "without importing", "do not import", "only use", "pure python", "must not use"):
        assert word in labels
    assert B.lint_task(TASK) is None
    assert B.lint_task("The Refusal of the engines is a TIERED runtime " * 3)[0] == "lint-runtime-name"


def test_a_claimed_wrong_stdout_is_corrected_and_flagged(tmp_path):
    tests = [dict(t) for t in TESTS]
    tests[1]["stdout"] = "wrong\n"
    result = build(tmp_path, batches([proposal(tests=tests)]))
    [case] = result["cases"]
    assert case["tests"][1]["stdout"] == "zyx\n1\n"
    assert result["report"]["corrected_stdouts"] == 1
    assert result["report"]["cases"][0]["stdout_corrected"] == [1]


def test_a_failing_reference_is_dropped(tmp_path):
    result = build(tmp_path, batches([proposal(reference="import sys\nsys.exit(3)\n")]))
    assert [d["reason"] for d in result["dropped"]] == ["reference-fails"]
    assert "test 0" in result["dropped"][0]["detail"]


def test_a_disagreeing_solver_drops_unless_kept_and_is_then_flagged(tmp_path):
    bad = [{"candidate_id": CID, "program": "print('nope')\n", "ambiguity": "none"}]
    result = build(tmp_path, batches([proposal()], bad))
    assert result["cases"] == []
    assert [d["reason"] for d in result["dropped"]] == ["solver-disagrees"]
    kept = build(tmp_path, batches([proposal()], bad), keep_disagreements=True)
    [case] = kept["cases"]
    assert case["flags"] == ["solver-disagrees"]
    assert kept["report"]["solver_disagree"] == 1 and kept["report"]["solver_agree"] == 0


def test_a_missing_solution_is_unsolved_and_treated_like_disagreement(tmp_path):
    result = build(tmp_path, batches([proposal()], []))
    assert [d["reason"] for d in result["dropped"]] == ["unsolved"]
    kept = build(tmp_path, batches([proposal()], []), keep_disagreements=True)
    assert kept["cases"][0]["flags"] == ["unsolved"]
    assert kept["report"]["unsolved"] == 1


def test_a_clean_exit_90_refusal_on_every_test_is_a_fallback_control(tmp_path):
    engine = fake_engine(tmp_path, "printf 'lypning-l: unsupported: module: import sys\\n' >&2\nexit 90\n")
    result = build(tmp_path, batches([proposal()]), engine=engine)
    [case] = result["cases"]
    assert case["population"] == "fallback-control"
    assert result["witnesses"] == []
    assert result["report"]["populations"] == {"coverage": 0, "fallback-control": 1}


def test_a_native_wrong_answer_is_a_witness_and_never_a_case(tmp_path):
    engine = fake_engine(tmp_path, "printf 'wrong\\n'\nexit 0\n")
    result = build(tmp_path, batches([proposal()]), engine=engine)
    assert result["cases"] == []
    assert [d["reason"] for d in result["dropped"]] == ["native-mismatch"]
    assert [w["test"] for w in result["witnesses"]] == [0, 1, 2]
    w = result["witnesses"][0]
    assert w["candidate_id"] == CID and w["kind"] == "wrong-answer"
    assert w["expected"] == "ba\ndc\n2\n" and w["native_stdout"] == "wrong\n" and w["native_exit"] == 0
    assert result["report"]["mismatch_witnesses"] == result["witnesses"]


def test_a_refusal_with_stdout_or_the_wrong_name_is_a_bad_refusal_witness(tmp_path):
    engine = fake_engine(tmp_path, "printf 'half\\n'\nprintf 'lypning-l: unsupported: x: y\\n' >&2\nexit 90\n")
    result = build(tmp_path, batches([proposal()]), engine=engine)
    assert result["cases"] == [] and result["witnesses"][0]["kind"] == "bad-refusal"
    engine = fake_engine(tmp_path, "printf 'lypning: unsupported: x: y\\n' >&2\nexit 90\n", "other")
    result = build(tmp_path, batches([proposal()]), engine=engine)
    assert result["cases"] == [] and result["witnesses"][0]["kind"] == "bad-refusal"


def test_evidence_ids_come_from_matching_unquarantined_events_only(tmp_path):
    ev = B.load_evidence([evidence_dir(tmp_path), evidence_dir(tmp_path, "print(2)", "snap2")])
    assert ev["index"][sha(REFERENCE)] == ["ev-one", "ev-two"]
    assert ev["index"][sha("print(2)")] == ["ev-one", "ev-two"]
    assert ev["events"] == 8 and len(ev["snapshots"]) == 2
    other = candidate(program="print('unobserved')")
    result = B.build(batches([proposal()]), [other], ev, sys.executable)
    assert [d["reason"] for d in result["dropped"]] == ["no-evidence"]
    with pytest.raises(B.TrainingError, match="events.jsonl"):
        B.load_evidence([tmp_path / "missing"])


def test_case_id_is_deterministic_over_candidate_id_and_task(tmp_path):
    a = build(tmp_path, batches([proposal()]))["cases"][0]["case_id"]
    b = build(tmp_path, batches([proposal()]))["cases"][0]["case_id"]
    assert a == b == B.case_id_for(CID, TASK)
    assert B.case_id_for(CID, TASK + " ") != a and B.case_id_for("other", TASK) != a
    assert len(a) == 15 and all(c in "0123456789abcdef" for c in a[3:])


def test_author_drops_unknown_candidates_and_bad_shapes_are_named(tmp_path):
    bat = batches([
        proposal(keep=False, drop_reason="ambiguous"),
        proposal(candidate_id="e2-nobody"),
        proposal(family="!!!"),
        proposal(tests=TESTS[:2]),
        proposal(tests=[dict(t, expect="x") for t in TESTS]),
        proposal(tests=[dict(t, files={"../x": "y"}) for t in TESTS]),
    ])
    result = build(tmp_path, bat)
    assert [d["reason"] for d in result["dropped"]] == [
        "author-dropped", "no-candidate", "no-family", "tests-shape", "tests-shape", "tests-shape"]
    assert result["dropped"][0]["detail"] == "ambiguous"
    assert result["report"]["kept"] == 5 and result["report"]["proposals"] == 6


def test_a_second_proposal_for_the_same_task_is_a_duplicate(tmp_path):
    result = build(tmp_path, batches([proposal(), proposal()]))
    assert len(result["cases"]) == 1
    assert [d["reason"] for d in result["dropped"]] == ["duplicate"]


def test_cli_writes_the_four_outputs_refuses_to_overwrite_and_exits_1_when_empty(tmp_path, capsys):
    from pipeline import cli
    props = tmp_path / "proposals.json"
    props.write_text(json.dumps(batches([proposal(), proposal(task=TASK + " Use only the lypning runtime.")])))
    cands = tmp_path / "candidates.jsonl"
    write_jsonl(cands, [candidate()])
    ev = evidence_dir(tmp_path)
    out = tmp_path / "bank"
    argv = ["eval2-bank", "--proposals", str(props), "--candidates", str(cands),
            "--evidence", str(ev), "--engine", sys.executable, "--output", str(out)]
    assert cli.main(argv) == 0
    text = capsys.readouterr().out
    assert "admitted 1" in text and "lint-runtime-name" in text and "coverage 1" in text
    bank = read_jsonl(out / "bank.jsonl")
    assert [c["case_id"] for c in bank] == [B.case_id_for(CID, TASK)]
    report = read_json(out / "report.json")
    assert report["admitted"] == 1 and report["dropped_by_reason"] == {"lint-runtime-name": 1}
    assert read_jsonl(out / "dropped.jsonl")[0]["reason"] == "lint-runtime-name"
    assert read_jsonl(out / "witnesses.jsonl") == []
    assert (out / "bank.jsonl").read_text().endswith("\n")
    assert cli.main(argv) == 2
    assert "refusing to overwrite" in capsys.readouterr().err
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps(batches([proposal(keep=False)])))
    assert cli.main(argv[:2] + [str(empty)] + argv[3:-1] + [str(tmp_path / "bank2")]) == 1
    assert cli.main(argv[:-1] + [str(tmp_path / "bank3"), "--engine", str(tmp_path / "nope")]) == 1
    assert "no lypning-l" in capsys.readouterr().err
