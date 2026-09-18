"""Disjoint ids across two banks buy nothing; five rules ask what an id cannot."""
from __future__ import annotations

import json

from pipeline import eval2_leaks as L
from pipeline.jsonio import write_jsonl


def case(cid, task, reference, stdouts, *, source_group="", evidence=()):
    row = {
        "case_id": cid,
        "family": "family-" + cid,
        "task": task,
        "reference": reference,
        "provenance": "test",
        "population": "coverage",
        "tests": [{"stdin": "in%d" % i, "stdout": s} for i, s in enumerate(stdouts)],
        "review": {"origin": "authored", "evidence_ids": list(evidence)},
    }
    if source_group:
        row["source_group"] = source_group
    return row


# One eval-2 case per rule, plus one that no training case resembles.
EVAL2 = [
    case("e-clean", "Read a line from stdin and print the number of vowels in it.",
         "import sys\nprint(sum(c in 'aeiou' for c in sys.stdin.read()))",
         ["vowels: 3\n", "vowels: 0\n", "vowels: 11\n"],
         source_group="src-clean", evidence=["cap-clean"]),
    case("e-task", "Read integers from stdin, one per line, and print their sum.",
         "import sys\nprint(sum(int(l) for l in sys.stdin))",
         ["sum is 10\n", "sum is 0\n", "sum is -4\n"],
         source_group="src-task", evidence=["cap-task"]),
    case("e-stdout", "Print the word counts of the file named in argv.",
         "import sys\nprint(len(open(sys.argv[1]).read().split()))",
         ["long-identical-output-1\n", "long-identical-output-2\n", "z\n"],
         source_group="src-stdout", evidence=["cap-stdout"]),
    case("e-fingerprint", "Given a line on stdin, echo it reversed.",
         "import sys\nx = sys.stdin.readline().rstrip('\\n')\nprint(x[::-1])",
         ["reversed-a\n", "reversed-b\n", "reversed-c\n"],
         source_group="src-fp", evidence=["cap-fp"]),
    case("e-source", "Print the largest of three argv integers.",
         "import sys\nprint(max(map(int, sys.argv[1:4])))",
         ["largest: 9\n", "largest: 0\n", "largest: -1\n"],
         source_group="src-shared", evidence=["cap-source"]),
    case("e-evidence", "Count blank lines in the file passed as the first argument.",
         "import sys\nprint(sum(1 for l in open(sys.argv[1]) if not l.strip()))",
         ["blank lines: 2\n", "blank lines: 0\n", "blank lines: 7\n"],
         source_group="src-evidence", evidence=["cap-shared", "cap-evidence"]),
]

TRAIN = [
    # Same question, reworded: trips task only.
    case("t-task", "Read integers from stdin, one per line, and print the sum.",
         "import sys\nprint(sum(map(int, sys.stdin.read().split())))",
         ["total 10\n", "total 0\n", "total -4\n"],
         source_group="src-t-task", evidence=["cap-t-task"]),
    # A different prompt sharing one long expected stdout: trips stdout only.
    case("t-stdout", "Emit the canonical marker for the mode given on stdin.",
         "import sys\nprint('long-identical-output-' + sys.stdin.read().strip())",
         ["long-identical-output-1\n", "marker-9\n", "marker-4\n"],
         source_group="src-t-stdout", evidence=["cap-t-stdout"]),
    # Same AST under different formatting and a different prompt: fingerprint only.
    case("t-fingerprint", "Flip the characters of the first stdin line end to end.",
         "import sys  # read\nx   = sys.stdin.readline().rstrip( '\\n' )\n\nprint( x[ : : -1] )",
         ["flipped-a\n", "flipped-b\n", "flipped-c\n"],
         source_group="src-t-fp", evidence=["cap-t-fp"]),
    # Nothing in common but the declared source: source_group only.
    case("t-source", "Print the smallest of the argv integers, or nothing if none.",
         "import sys\nvals = list(map(int, sys.argv[1:]))\nprint(min(vals) if vals else '')",
         ["smallest: 1\n", "smallest: -8\n", "\n"],
         source_group="src-shared", evidence=["cap-t-source"]),
    # Nothing in common but one evidence id: evidence only.
    case("t-evidence", "Print the longest line of the file named on argv.",
         "import sys\nprint(max(open(sys.argv[1]).read().splitlines(), key=len))",
         ["longest: abc\n", "longest: x\n", "longest: hello world\n"],
         source_group="src-t-evidence", evidence=["cap-shared"]),
    # A clean training case: shares a short stdout ("0\\n") with nothing that counts.
    case("t-clean", "Print the number of distinct words in the stdin text.",
         "import sys\nprint(len(set(sys.stdin.read().split())))",
         ["0\n", "5\n", "12\n"],
         source_group="src-t-clean", evidence=["cap-t-clean"]),
]


def _pairs(report):
    return {(p["eval2_id"], p["train_id"]): p["rules"] for p in report["pairs"]}


def test_one_pair_per_rule_and_a_clean_pair():
    report = L.bank_leaks(EVAL2, TRAIN)
    assert _pairs(report) == {
        ("e-task", "t-task"): ["task"],
        ("e-stdout", "t-stdout"): ["stdout"],
        ("e-fingerprint", "t-fingerprint"): ["fingerprint"],
        ("e-source", "t-source"): ["source_group"],
        ("e-evidence", "t-evidence"): ["evidence"],
    }
    assert report["by_rule"] == {"task": 1, "stdout": 1, "fingerprint": 1,
                                 "source_group": 1, "evidence": 1}
    assert report["eval2_cases_with_any_leak"] == [
        "e-evidence", "e-fingerprint", "e-source", "e-stdout", "e-task"]
    assert report["n_eval2_cases_with_any_leak"] == 5
    assert report["clean"] == ["e-clean"]
    assert report["unparsable"] == []
    assert (report["n_eval2"], report["n_train"]) == (6, 6)


def test_task_rule_is_the_split_ceiling_on_normalised_text():
    report = L.bank_leaks(EVAL2, TRAIN)
    sim = {p["eval2_id"]: p["similarity"] for p in report["pairs"]}
    assert sim["e-task"] >= L.SIMILARITY_CEILING == 0.85
    # Case and whitespace never change a task.
    twin = dict(TRAIN[0], task="  READ integers  from STDIN, one per line, and print the sum. ")
    assert _pairs(L.bank_leaks([EVAL2[1]], [twin])) == {("e-task", "t-task"): ["task"]}
    # A stricter ceiling drops the reworded pair; nothing else moves.
    strict = L.bank_leaks(EVAL2, TRAIN, ceiling=1.0)
    assert ("e-task", "t-task") not in _pairs(strict) and len(strict["pairs"]) == 4


def test_short_stdouts_do_not_count():
    # "0\n" is shared between e-clean-like outputs and t-clean but is under the floor.
    short_eval = case("e-short", "Print zero.", "print(0)", ["0\n", "1\n", "2\n"])
    short_train = case("t-short", "Print nought.", "print('0')", ["0\n", "3\n", "4\n"])
    assert L.bank_leaks([short_eval], [short_train])["pairs"] == []
    # Lower the floor and the same pair leaks.
    lowered = L.bank_leaks([short_eval], [short_train], min_stdout_chars=1)
    assert _pairs(lowered) == {("e-short", "t-short"): ["stdout"]}


def test_fingerprint_is_training_data_solution_fingerprint():
    from pipeline.training_data import solution_fingerprint
    assert (solution_fingerprint(EVAL2[3]["reference"])
            == solution_fingerprint(TRAIN[2]["reference"]))
    assert L._fingerprint(EVAL2[3]) == solution_fingerprint(EVAL2[3]["reference"])


def test_unparsable_reference_is_listed_not_fatal():
    broken = dict(TRAIN[5], case_id="t-broken", reference="def (:")
    report = L.bank_leaks([EVAL2[0]], [broken])
    assert report["pairs"] == [] and report["unparsable"] == ["t-broken"]


def test_multiple_rules_on_one_pair_count_each_once():
    twin = dict(EVAL2[1], case_id="t-twin")
    report = L.bank_leaks([EVAL2[1]], [twin])
    assert _pairs(report) == {("e-task", "t-twin"): list(L.RULES)}
    assert report["by_rule"] == {r: 1 for r in L.RULES}
    assert report["n_eval2_cases_with_any_leak"] == 1


def test_banks_are_never_written(tmp_path):
    ev, tr = tmp_path / "eval2.jsonl", tmp_path / "train.jsonl"
    write_jsonl(ev, EVAL2)
    write_jsonl(tr, TRAIN)
    before = (ev.read_bytes(), tr.read_bytes())
    L.bank_leaks(EVAL2, TRAIN)
    from pipeline.cli import main
    main(["eval2-leaks", str(ev), str(tr)])
    assert (ev.read_bytes(), tr.read_bytes()) == before


def test_cli_prints_a_table_and_exits_one_unless_allowed(tmp_path, capsys):
    from pipeline.cli import main
    ev, tr = tmp_path / "eval2.jsonl", tmp_path / "train.jsonl"
    write_jsonl(ev, EVAL2)
    write_jsonl(tr, TRAIN)
    assert main(["eval2-leaks", str(ev), str(tr)]) == 1
    out = capsys.readouterr().out
    assert "5 pairs; 5 of 6 eval-2 cases leak; 1 are clean" in out
    assert "by rule: task 1  stdout 1  fingerprint 1  source_group 1  evidence 1" in out
    for rule in L.RULES:
        assert rule in out
    assert "e-task" in out and "t-task" in out and "e-clean" not in out.split("\n", 3)[3]

    assert main(["eval2-leaks", str(ev), str(tr), "--allow"]) == 0
    assert "5 pairs" in capsys.readouterr().out

    assert main(["eval2-leaks", str(ev), str(tr), "--json", "--allow"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["by_rule"]["evidence"] == 1 and len(body["pairs"]) == 5


def test_cli_clean_banks_exit_zero(tmp_path, capsys):
    from pipeline.cli import main
    ev, tr = tmp_path / "eval2.jsonl", tmp_path / "train.jsonl"
    write_jsonl(ev, [EVAL2[0]])
    write_jsonl(tr, [TRAIN[5]])
    assert main(["eval2-leaks", str(ev), str(tr)]) == 0
    assert "0 pairs; 0 of 1 eval-2 cases leak; 1 are clean" in capsys.readouterr().out


def test_cli_missing_bank_is_usage(tmp_path, capsys):
    from pipeline.cli import main
    ev = tmp_path / "eval2.jsonl"
    write_jsonl(ev, EVAL2)
    assert main(["eval2-leaks", str(ev), str(tmp_path / "missing.jsonl")]) == 2
    err = capsys.readouterr()
    assert "not a file" in err.err and err.out == ""


def test_cli_empty_banks_are_not_two_clean_banks(tmp_path, capsys):
    """`is_file` covers absence, not emptiness, and they read alike downstream.

    "0 pairs; 0 of 0 eval-2 cases leak" is a clean bill over a comparison that
    did not happen, issued by the gate standing between a training bank and the
    number eval-2 exists to produce. An all-filtered bank arrives the same way.
    """
    from pipeline.cli import main
    ev, tr = tmp_path / "eval2.jsonl", tmp_path / "train.jsonl"
    ev.write_text("", encoding="utf-8")
    tr.write_text("", encoding="utf-8")
    assert main(["eval2-leaks", str(ev), str(tr)]) == 1
    err = capsys.readouterr()
    assert "nothing was compared" in err.err and err.out == ""

    # One populated side is still nothing compared, and --allow does not buy it:
    # --allow forgives found pairs, not an absent comparison.
    write_jsonl(ev, EVAL2)
    assert main(["eval2-leaks", str(ev), str(tr)]) == 1
    assert "0 training case(s)" in capsys.readouterr().err
    assert main(["eval2-leaks", str(ev), str(tr), "--allow"]) == 1
    assert "nothing was compared" in capsys.readouterr().err
