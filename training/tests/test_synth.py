"""Bank v3 through the oracle and the engine, without a provider and without a build.

The engine is a shell script that refuses by construct, lies on request, and
otherwise hands the program to CPython, so every route in `synth.judge` is
reachable from a fixture. The real binary is exercised by
`test_repair_rules.py` for the preludes and by the workflow for the batch.
"""
from __future__ import annotations

import json
import stat
import sys

import pytest

from pipeline import engines as eng
from pipeline import synth
from pipeline.cli import main as cli_main
from pipeline.training_data import validate_cases

MODEL = "qwen-3.8-27b"
INPUTS = [{"stdin": "3 1 2\n"}, {"stdin": "10 20\n"}, {"stdin": "5\n"}, {"stdin": "\n"}]
SUM = "import sys\nprint(sum(int(x) for x in sys.stdin.read().split()))"
SUM2 = "import sys\nns=[int(t) for t in sys.stdin.read().split()]\nprint(sum(ns))"
MEDIAN = ("import sys, statistics\nns=[int(x) for x in sys.stdin.read().split()]\n"
          "print(statistics.median(ns) if ns else 'none')")
MEDIAN2 = ("import sys\nimport statistics\nvals=[int(v) for v in sys.stdin.read().split()]\n"
           "print('none' if not vals else statistics.median(vals))")
FRACTION = ("import sys\nfrom fractions import Fraction\nns=[int(x) for x in sys.stdin.read().split()]\n"
            "print(Fraction(sum(ns), len(ns)) if ns else 'none')")
FRACTION2 = ("import sys, fractions\nns=[int(x) for x in sys.stdin.read().split()]\n"
             "print('none' if not ns else fractions.Fraction(sum(ns), len(ns)))")
TEXTWRAP = "import sys, textwrap\nprint('\\n'.join(textwrap.wrap(sys.stdin.read().strip(), 5)))"
TEXTWRAP2 = "import sys\nimport textwrap\nt=sys.stdin.read().strip()\nprint('\\n'.join(textwrap.wrap(t, 5)))"


def fake_engine(tmp_path):
    """Refuses statistics/fractions/textwrap; lies, crashes or half-refuses on a marker."""
    path = tmp_path / "fake-lypning-l"
    path.write_text(
        "#!/bin/sh\n"
        "prog=\"$1\"; shift\n"
        "for m in statistics fractions textwrap; do\n"
        "  if grep -Eq \"^(import|from) .*$m\" \"$prog\"; then\n"
        "    echo \"fake-l: unsupported: module: import $m\" >&2; exit 90; fi\n"
        "done\n"
        "if grep -q MARK_LIE \"$prog\"; then echo wrong; exit 0; fi\n"
        "if grep -q MARK_CRASH \"$prog\"; then echo boom >&2; exit 3; fi\n"
        "if grep -q MARK_HALF \"$prog\"; then echo half; echo \"fake-l: unsupported: x: y\" >&2; exit 90; fi\n"
        "if [ \"$1\" = refuse ]; then echo \"fake-l: unsupported: builtin: argv\" >&2; exit 90; fi\n"
        "exec %s \"$prog\" \"$@\"\n" % sys.executable)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def with_file(name):
    """INPUTS with one setup file of that name, as new dicts: INPUTS is shared."""
    return [dict(spec, files={name: "1 2 3\n"}) for spec in INPUTS]


def candidate(programs, *, stratum="rewrite", construct="functools.reduce", inputs=INPUTS,
              task="Read integers from stdin and print their sum; print 0 when there are none at all."):
    return {"task": task, "inputs": inputs, "programs": programs, "stratum": stratum,
            "target_construct": construct, "model": MODEL, "domain": stratum + ":x",
            "generated_on": "2026-09-18"}


@pytest.fixture
def runner(tmp_path):
    return synth.Runner(fake_engine(tmp_path), timeout_s=20.0)


# --- the candidate shape ------------------------------------------------------


@pytest.mark.parametrize("edit,why", [
    (lambda r: r.pop("task"), "task"),
    (lambda r: r.update(stratum="unobserved"), "stratum"),
    (lambda r: r.update(programs=[SUM]), "programs"),
    (lambda r: r.update(inputs=INPUTS[:2]), "three inputs"),
    (lambda r: r.update(inputs=INPUTS[:3] + [{"stdout": "x"}]), "input 3"),
    (lambda r: r.update(inputs=INPUTS[:3] + [{"argv": [1]}]), "argv"),
    (lambda r: r.update(inputs=INPUTS[:3] + [{"files": {"/data/x.txt": "1\n"}}]),
     "escapes the working directory"),
    (lambda r: r.update(inputs=INPUTS[:3] + [{"argv": ["a\0b"]}]), "NUL"),
    (lambda r: r.update(inputs=INPUTS[:3] + [{"files": {"a\0b": "1\n"}}]), "NUL"),
    (lambda r: r.pop("target_construct"), "target_construct"),
    (lambda r: r.update(inputs=with_file("/data/logs.txt")), "escapes the working directory"),
    (lambda r: r.update(inputs=with_file("../out")), "escapes the working directory"),
    (lambda r: r.update(inputs=with_file("solution.py")), "reserved for the program itself"),
])
def test_a_malformed_candidate_names_its_defect(edit, why):
    row = candidate([SUM, SUM2])
    edit(row)
    assert why in (synth.validate_candidate(row) or "")


def test_a_well_formed_candidate_validates():
    assert synth.validate_candidate(candidate([SUM, SUM2])) is None


@pytest.mark.parametrize("spec", [
    {"files": {"/data/x.txt": "1\n"}},   # materialize: a harness error, which aborts the run
    {"argv": ["a\0b"]},                  # Popen: a ValueError, which is not caught at all
    {"files": {"a\0b": "1\n"}},          # mkdir: the same ValueError, one call later
])
def test_an_unspawnable_row_costs_one_row_and_not_the_batch(runner, spec):
    """Every input the sandbox cannot set up is refused before anything runs.

    Both failures used to leave `run` rather than return from it, so a single
    generated row discarded every candidate judged before it — 2,078 of them in
    run 35399909232. The batch must survive the row.
    """
    bad = candidate([SUM, SUM2], inputs=INPUTS[:3] + [spec],
                    task="Read integers from stdin and print their sum, or 0 for none.")
    result = synth.run([bad, candidate([SUM, SUM2])], runner)
    assert len(result["cases"]) == 1
    assert result["report"]["tally"]["rejected:malformed"] == 1


def test_tests_carry_the_input_keys_and_the_agreed_stdout():
    tests = synth.tests_of([{"stdin": "a", "argv": ["b"], "files": {"f": "x"}}], ["out\n"])
    assert tests == [{"stdin": "a", "argv": ["b"], "files": {"f": "x"}, "stdout": "out\n"}]


# --- the routing ------------------------------------------------------------


def test_a_program_the_engine_serves_is_a_native_row(runner):
    verdict = synth.judge(candidate([SUM, SUM2]), runner)
    assert verdict["kind"] == "native"
    row = verdict["row"]
    assert row["agreement"] == 2 and row["samples"] == 2
    assert [t["stdout"] for t in row["tests"]] == ["6\n", "30\n", "5\n", "0\n"]


def test_a_refused_rewrite_row_goes_to_the_repair_queue(runner):
    verdict = synth.judge(candidate([MEDIAN, MEDIAN2], construct="statistics.median"), runner)
    assert verdict["kind"] == "repair"
    assert verdict["row"]["refusals"] == ["module: import statistics"]


def test_a_refused_ceiling_row_is_a_control_not_a_repair(runner):
    verdict = synth.judge(candidate([FRACTION, FRACTION2], stratum="ceiling",
                                    construct="fractions.Fraction"), runner)
    assert verdict["kind"] == "ceiling"
    assert verdict["row"]["refusals"] == ["module: import fractions"]


def test_a_ceiling_row_the_engine_serves_is_native_by_execution_not_by_label(runner):
    """The label says ceiling; the engine served it; the engine wins."""
    verdict = synth.judge(candidate([SUM, SUM2], stratum="ceiling", construct="fractions.Fraction"),
                          runner)
    assert verdict["kind"] == "native"


def test_a_control_refused_on_some_inputs_only_is_rejected(runner):
    """`validate_reference_scores` wants a control refused on EVERY test."""
    argv_inputs = [{"argv": ["refuse"]}, {"argv": ["ok"]}, {"argv": ["also"]}]
    echo = "import sys\nprint(sys.argv[1])"
    verdict = synth.judge(candidate([echo, echo + "\n"], stratum="ceiling", inputs=argv_inputs,
                                    construct="uuid.uuid5"), runner)
    assert verdict["kind"] == "rejected" and verdict["why"].startswith("partly-native")
    # The same shape generated as a rewrite is a repair to attempt, not a rejection.
    verdict = synth.judge(candidate([echo, echo + "\n"], inputs=argv_inputs), runner)
    assert verdict["kind"] == "repair" and verdict["row"]["refusals"] == ["builtin: argv"]


def test_samples_that_disagree_leave_no_expected_output(runner):
    smallest = "import sys\nns=[int(x) for x in sys.stdin.read().split()]\nprint(min(ns) if ns else 0)"
    verdict = synth.judge(candidate([SUM, smallest]), runner)
    assert verdict["kind"] == "rejected" and verdict["why"].startswith("disagreed")


def test_fewer_clean_samples_than_the_quorum_is_no_quorum(runner):
    verdict = synth.judge(candidate([SUM, "import sys\nraise SystemExit(2)"]), runner)
    assert verdict["kind"] == "rejected" and verdict["why"].startswith("no-quorum")


def test_a_program_whose_output_moves_between_two_runs_is_unstable(runner):
    """Agreement across samples is not stability: the winner is run again."""
    noisy = "import os\nprint(os.urandom(8).hex())"
    verdict = synth.judge(candidate([noisy, noisy + "\n"]), runner)
    assert verdict["kind"] == "rejected" and verdict["why"].startswith(("unstable", "disagreed"))


def test_tests_that_cannot_discriminate_are_rejected_before_the_engine_runs(runner):
    verdict = synth.judge(candidate(["print('hello')", "print('hello')\n"]), runner)
    assert verdict["kind"] == "rejected" and verdict["why"].startswith("constant-output")
    verdict = synth.judge(candidate(["print('')", "print()"]), runner)
    assert verdict["kind"] == "rejected" and verdict["why"].startswith("blank-output")


@pytest.mark.parametrize("marker,kind", [("MARK_LIE", "mismatch"), ("MARK_CRASH", "crash"),
                                         ("MARK_HALF", "bad-refusal")])
def test_an_engine_that_disagrees_with_cpython_is_a_witness_never_a_row(runner, marker, kind):
    """Root CLAUDE.md invariant 1, applied at authoring time."""
    program = SUM + "\n# " + marker
    verdict = synth.judge(candidate([program, program + "\n"]), runner)
    assert verdict["kind"] == "witness"
    assert verdict["why"].startswith(kind + ":")


# --- the repair ----------------------------------------------------------------


def test_a_repair_is_accepted_only_when_native_and_byte_identical(runner):
    queued = synth.judge(candidate([MEDIAN, MEDIAN2], construct="statistics.median"), runner)["row"]
    outcome = synth.repair(queued, runner)
    assert outcome["repaired"] and outcome["row"]["repair_rule"] == "statistics"
    assert "statistics" not in outcome["row"]["program"]
    assert outcome["row"]["original"] == MEDIAN
    assert outcome["fired"] == ["statistics"] and outcome["rejected"] == {}


def test_a_queue_row_no_rule_reaches_stays_owed(runner):
    queued = synth.judge(candidate([TEXTWRAP, TEXTWRAP2], construct="textwrap.fill",
                                   inputs=[{"stdin": "a bb ccc dddd eeeee\n"}, {"stdin": "x\n"},
                                           {"stdin": "abcdefghijklmnop\n"}, {"stdin": "\n"}]),
                         runner)["row"]
    outcome = synth.repair(queued, runner)
    assert not outcome["repaired"] and outcome["row"] is queued and outcome["fired"] == []


def test_a_rule_that_changes_behaviour_is_rejected_by_verification(runner):
    """The expected output was agreed before the repair existed; a rule cannot move it."""
    queued = synth.judge(candidate([MEDIAN, MEDIAN2], construct="statistics.median"), runner)["row"]
    lying = [("liar", lambda src: src.replace("statistics.median(ns)", "0").replace(
        "statistics.median(vals)", "0").replace("import sys, statistics", "import sys"))]
    outcome = synth.repair(queued, runner, rules=lying)
    assert not outcome["repaired"] and outcome["rejected"] == {"liar": "cpython-mismatch"}


# --- schema-3 --------------------------------------------------------------------


def test_every_admitted_kind_projects_to_a_case_the_trainer_validates(runner):
    rows = {
        "native": synth.judge(candidate([SUM, SUM2]), runner)["row"],
        "ceiling": synth.judge(candidate([FRACTION, FRACTION2], stratum="ceiling",
                                         construct="fractions.Fraction",
                                         task="Print the exact mean of the integers on stdin as a fraction."),
                               runner)["row"],
    }
    queued = synth.judge(candidate([MEDIAN, MEDIAN2], construct="statistics.median",
                                   task="Print the median of the integers on stdin, or none."), runner)["row"]
    rows["repaired"] = synth.repair(queued, runner)["row"]
    cases = [synth.to_case(row, kind=kind, batch="t") for kind, row in rows.items()]
    for case in cases:
        validate_cases([case])
    by_kind = {c["synth"]["kind"]: c for c in cases}
    assert by_kind["native"]["population"] == "coverage"
    assert by_kind["repaired"]["population"] == "coverage"
    assert by_kind["ceiling"]["population"] == "fallback-control"
    assert by_kind["repaired"]["reference"] == rows["repaired"]["program"]
    assert by_kind["repaired"]["synth"]["original"] == MEDIAN
    assert by_kind["repaired"]["synth"]["repair_rule"] == "statistics"
    assert by_kind["ceiling"]["synth"]["refusals"] == ["module: import fractions"]
    for case in cases:
        assert case["case_id"].startswith("v3-")
        assert case["family"] == case["source_group"] == synth.family_of(case["synth"]["target_construct"])
        assert case["synth"]["tier"] == "self-consistency"
        assert "self-consistency" in case["review"]["oracle_basis"]
        assert "not a human" in case["review"]["reviewer"]
        assert "batch t" in case["provenance"] and MODEL in case["provenance"]


def test_the_case_id_is_a_function_of_task_and_tests_only():
    tests = [{"stdin": "a", "stdout": "1\n"}]
    assert synth.case_id_for("t", tests) == synth.case_id_for("t", list(tests))
    assert synth.case_id_for("t", tests) != synth.case_id_for("u", tests)


def test_family_and_capability_come_from_the_construct():
    assert synth.family_of("heapq.heappush and heapq.heappop") == "heapq-heappush-and-heapq-heappop"
    assert synth.capability_of("heapq.heappush and heapq.heappop") == "heapq"
    assert synth.capability_of("zip with strict=True") == "zip"
    assert synth.family_of("") == "unnamed"


# --- the batch -------------------------------------------------------------------


def batch_rows():
    return [
        candidate([SUM, SUM2]),
        candidate([MEDIAN, MEDIAN2], construct="statistics.median",
                  task="Print the median of the integers on stdin, or none when there are none."),
        candidate([FRACTION, FRACTION2], stratum="ceiling", construct="fractions.Fraction",
                  task="Print the exact mean of the integers on stdin as a reduced fraction."),
        candidate([TEXTWRAP, TEXTWRAP2], construct="textwrap.fill",
                  task="Wrap the line on stdin at five columns and print the wrapped lines.",
                  inputs=[{"stdin": "a bb ccc dddd eeeee\n"}, {"stdin": "x\n"},
                          {"stdin": "abcdefghijklmnop\n"}, {"stdin": "\n"}]),
        candidate([SUM, SUM2]),                                    # duplicate task
        candidate([SUM + "\n# MARK_LIE", SUM2 + "\n# MARK_LIE"],
                  task="Print the sum of the integers on stdin, and nothing else at all, please."),
        {"task": "malformed"},
    ]


def test_run_routes_a_whole_batch_and_counts_every_outcome(runner):
    result = synth.run(batch_rows(), runner, batch="b1")
    report = result["report"]
    assert report["cases"] == 3 and report["populations"] == {"coverage": 2, "fallback-control": 1}
    assert report["families"] == 3
    assert report["unrepaired"] == 1 and report["unserved_kinds"] == [["module: import textwrap", 1]] \
        or report["unserved_kinds"] == [("module: import textwrap", 1)]
    assert report["witnesses"] == 1 and result["witnesses"][0]["why"].startswith("mismatch")
    assert report["tally"]["duplicate"] == 1 and report["tally"]["rejected:malformed"] == 1
    assert report["rules_accepted"] == {"statistics": 1} and report["rules_fired"] == {"statistics": 1}
    assert all(c["synth"]["batch"] == "b1" for c in result["cases"])
    validate_cases(result["cases"])


def test_a_file_name_that_escapes_the_workdir_costs_its_row_not_the_batch(runner):
    """The failure of adapt job 105802008535: one such row aborted 2,078 judged ones.

    `sandbox.materialize` calls an escaping name a HARNESS error, and a harness
    error is ours and stops everything. It is the model's error, so
    `validate_candidate` now catches it before the sandbox is ever asked.
    """
    rows = batch_rows()
    rows.insert(0, candidate([SUM, SUM2], inputs=with_file("/data/logs.txt"),
                             task="Sum the integers named by the file argument, please."))
    result = synth.run(rows, runner, batch="b1")
    assert result["report"]["cases"] == 3
    assert result["report"]["tally"]["rejected:malformed"] == 2
    assert any("escapes the working directory" in r["why"] for r in result["rejected"])


def test_write_outputs_and_render(runner, tmp_path):
    result = synth.run(batch_rows(), runner, batch="b1")
    paths = synth.write_outputs(result, tmp_path / "out")
    for name in ("cases", "unrepaired", "rejected", "witnesses"):
        rows = [json.loads(l) for l in (tmp_path / "out" / (name + ".jsonl")).read_text().splitlines()]
        assert len(rows) == result["report"][name]
    assert json.loads((tmp_path / "out" / "report.json").read_text())["batch"] == "b1"
    text = synth.render(result["report"])
    assert "cases 3" in text and "WITNESSES 1" in text and "import textwrap" in text
    assert set(paths) == {"cases", "unrepaired", "rejected", "witnesses", "report"}


# --- the refusal contract, in one place -------------------------------------------


def test_check_refusal_contract_names_the_half_that_broke():
    line = "lypning-l: unsupported: module: import re\n"
    assert eng.check_refusal_contract(90, "", line) is None
    assert eng.check_refusal_contract(90, "", line, engine="lypning-l") is None
    assert "names lypning-l" in eng.check_refusal_contract(90, "", line, engine="lypning")
    assert "stdout" in eng.check_refusal_contract(90, "x", line)
    assert "exactly one" in eng.check_refusal_contract(90, "", line + line)
    assert "exactly one" in eng.check_refusal_contract(90, "", "")
    assert "not a refusal line" in eng.check_refusal_contract(90, "", "Traceback\n")
    assert "not 90" in eng.check_refusal_contract(1, "", line)
    assert eng.refusal_bucket(line) == "module: import re"
    assert eng.refusal_bucket("garbage") == "unknown"


# --- the command -------------------------------------------------------------------


def write_candidates(path, rows):
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def test_synth_adapt_exit_codes(tmp_path, capsys):
    engine = fake_engine(tmp_path)
    cands = tmp_path / "c.jsonl"
    write_candidates(cands, batch_rows())
    assert cli_main(["synth-adapt", "--candidates", str(cands), "--engine", str(tmp_path / "nope"),
                     "--output", str(tmp_path / "o1")]) == 2
    assert cli_main(["synth-adapt", "--candidates", str(tmp_path / "missing"), "--engine", engine,
                     "--output", str(tmp_path / "o1")]) == 2
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    assert cli_main(["synth-adapt", "--candidates", str(empty), "--engine", engine,
                     "--output", str(tmp_path / "o1")]) == 1
    assert cli_main(["synth-adapt", "--candidates", str(cands), "--engine", engine,
                     "--output", str(tmp_path / "o2"), "--batch", "b2", "--timeout", "20"]) == 0
    out = capsys.readouterr()
    assert "cases 3" in out.out and "witness: mismatch" in out.err
    assert (tmp_path / "o2" / "cases.jsonl").is_file()
    # A re-run is a new directory.
    assert cli_main(["synth-adapt", "--candidates", str(cands), "--engine", engine,
                     "--output", str(tmp_path / "o2")]) == 2
    # Nothing admitted is a failed read, not a clean one.
    only_bad = tmp_path / "bad.jsonl"
    write_candidates(only_bad, [candidate(["print('x')", "print('x')"])])
    assert cli_main(["synth-adapt", "--candidates", str(only_bad), "--engine", engine,
                     "--output", str(tmp_path / "o3")]) == 1


def test_synth_generate_refuses_without_the_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    assert cli_main(["synth-generate", "--output", str(tmp_path / "c.jsonl")]) == 2
    assert "CEREBRAS_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "c.jsonl").exists()
