"""The capture-tier quality verdict: every reject rule has a fixture, nothing runs."""
from __future__ import annotations

import os
import subprocess

import pytest

from pipeline import capture_quality as q
from pipeline import lypning_source

#: A pure, deterministic, non-trivial program: 62 AST nodes, a def and a loop.
#: Every reject fixture below is this plus exactly one offending line, so the
#: fixture proves the rule and not some accident of a different program.
GOOD = '''def collatz(n):
    steps = 0
    while n != 1:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        steps += 1
    return steps


print(max(range(1, 30), key=collatz))
'''

LOCAL = frozenset(("pipeline", "verified_stages", "lypning"))

FIXTURES = [
    ("def f(:\n    pass\n", "unparseable"),
    ("", "unparseable"),
    ("import pipeline.eval2_select\n" + GOOD, "repo-import"),
    ("from verified_stages import run\n" + GOOD, "repo-import"),
    ("import subprocess\n" + GOOD, "process"),
    ("import os\nos.system('ls')\n" + GOOD, "process"),
    ("import os\nos.kill(1, 9)\n" + GOOD, "process"),
    ("import urllib.request\n" + GOOD, "network"),
    ("import socket\n" + GOOD, "network"),
    ("import os\nprint(os.environ.get('HOME'))\n" + GOOD, "environment"),
    ("from os import getenv\nprint(getenv('X'))\n" + GOOD, "environment"),
    (GOOD + "open('out.txt', 'w').write('x')\n", "writes-files"),
    (GOOD + "import pathlib\npathlib.Path('o').write_text('x')\n", "writes-files"),
    (GOOD + "print(open('in.txt').read())\n", "reads-files"),
    (GOOD + "import glob\nprint(glob.glob('*'))\n", "reads-files"),
    (GOOD + "import os\nprint(os.listdir('.'))\n", "reads-files"),
    (GOOD + "print(x.read_text())\n", "reads-files"),
    ("import numpy\n" + GOOD, "third-party"),
    ("import sys, json\nprint(json.load(sys.stdin)['a'])\n", "stdin-glue"),
    ("print(2 ** 64)\n", "trivial"),
    # 40+ nodes and still no def and no loop: straight-line arithmetic.
    ("a = 1\nb = 2\nc = 3\nd = a + b * c - a // b + c % a\ne = d * d + a * b + b * c\n"
     "f = e - d + c - b + a\nprint(a, b, c, d, e, f, a + b + c + d + e + f)\n", "trivial"),
    ("import random\n" + GOOD + "print(random.random())\n", "unseeded"),
    ("import time\n" + GOOD + "print(time.time())\n", "unseeded"),
    ("import uuid\n" + GOOD + "print(uuid.UUID(int=1))\n", "unseeded"),
    ("from datetime import datetime\n" + GOOD + "print(datetime.now())\n", "unseeded"),
    (GOOD + "password = 'hunter2hunter2x'\n", "privacy"),
    (GOOD + "# written for someone@example.com\n", "privacy"),
    (GOOD + "print('/Users/someone/notes')\n", "privacy"),
    (GOOD + "print('/etc/hosts')\n", "privacy"),
]


@pytest.mark.parametrize("program,rule", FIXTURES)
def test_each_rule_rejects_its_fixture(program, rule):
    assert q.classify(program, local=LOCAL) == rule


def test_every_rule_has_a_fixture():
    assert {rule for _, rule in FIXTURES} == set(q.RULES)


def test_the_good_program_is_tier_a_and_seeding_keeps_random_in():
    assert q.classify(GOOD, local=LOCAL) == ""
    seeded = "import random\nrandom.seed(7)\n" + GOOD + "print(random.random())\n"
    assert q.classify(seeded, local=LOCAL) == ""
    assert q.classify("import random\nrng = random.Random(3)\n" + GOOD, local=LOCAL) == ""
    # A root-level `/` or a division sign is not an absolute path.
    assert q.classify(GOOD + "print('a/b', '/')\n", local=LOCAL) == ""


def test_a_long_stdin_program_is_not_glue():
    long_reader = "import sys\n" + GOOD + "for line in sys.stdin:\n    print(collatz(int(line)))\n"
    assert q.verdict(long_reader, local=LOCAL)["nodes"] >= q.MIN_STDIN_NODES
    assert q.classify(long_reader, local=LOCAL) == ""


def test_first_rule_wins_and_tally_sums_to_the_input():
    both = "import subprocess\nopen('x', 'w')\n" + GOOD
    assert q.classify(both, local=LOCAL) == "process"
    counts = q.tally([p for p, _ in FIXTURES] + [GOOD], local=LOCAL)
    assert sum(counts.values()) == len(FIXTURES) + 1 and counts[""] == 1


def test_the_classifier_never_spawns_a_process(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the static classifier tried to execute something")

    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, refuse)
    monkeypatch.setattr(os, "system", refuse)
    monkeypatch.setattr(os, "popen", refuse)
    if hasattr(os, "fork"):
        monkeypatch.setattr(os, "fork", refuse)
    for program, _ in FIXTURES:
        q.verdict(program, local=LOCAL)
        q.hazard(program)
    q.classify(GOOD)  # the default, tree-derived local set too


def test_hazard_is_the_execution_subset_only():
    assert q.hazard("import subprocess\n") == "process"
    assert q.hazard("import socket\n") == "network"
    assert q.hazard("open('x', 'w')\n") == "writes-files"
    for harmless in ("print(1)", "import numpy", "def f(:", "print(open('x').read())"):
        assert q.hazard(harmless) == ""
    assert set(q.HAZARDS) <= set(q.RULES)


def test_repo_modules_are_computed_from_the_tree_minus_the_stdlib(tmp_path):
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "__init__.py").write_text("")
    (tmp_path / "src" / "lypning").mkdir()
    (tmp_path / "src" / "lypning" / "helper.py").write_text("")
    (tmp_path / "training" / "gpu").mkdir(parents=True)
    (tmp_path / "training" / "gpu" / "verified_stages.py").write_text("")
    (tmp_path / "training" / "gpu" / "json.py").write_text("")  # shadows the stdlib
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("")
    names = q.repo_modules(str(tmp_path))
    assert {"pkg", "helper", "gpu", "verified_stages", "test_x", "lypning", "pipeline"} <= names
    assert "json" not in names and "__init__" not in names


def test_the_real_tree_knows_its_own_modules():
    names = q.repo_modules()
    assert {"lypning", "pipeline", "verified_stages", "eval2_select"} <= names
    assert not names & {"json", "re", "os", "sys"}


# --- lypning_source ------------------------------------------------------------


def test_is_tooling_covers_every_repo_local_module_not_only_lypning():
    local = ("pipeline", "verified_stages")
    assert lypning_source.is_tooling("module", "import lypning.harvest", local=local)
    assert lypning_source.is_tooling("module", "import pipeline.eval2_select", local=local)
    assert lypning_source.is_tooling("module", "from verified_stages import x", local=local)
    assert lypning_source.is_tooling("module-attr", "pipeline.cli", local=local)
    assert not lypning_source.is_tooling("module", "import hashlib", local=local)
    assert not lypning_source.is_tooling("method", "import pipeline", local=local)
    assert lypning_source.is_tooling("module", "import verified_stages")  # tree default


def test_classify_entry_never_runs_a_hazardous_program(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("classify_entry executed a program the static gate rejects")

    monkeypatch.setattr(lypning_source, "run_python", refuse)
    for program in ("import subprocess\nsubprocess.run(['true'])\n",
                    "import socket\nprint(1)\n",
                    "open('x.txt', 'w').write('x')\n"):
        outcome, info = lypning_source.classify_entry({"id": "py-1", "program": program})
        assert outcome == "skip" and info["reason"].startswith("static: ")


def test_a_capture_full_of_bad_escapes_makes_no_warning_noise():
    import warnings
    program = "import re\n" + GOOD + "print(re.findall('\\d+', 'a1b22'))\n"
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        assert q.classify(program, local=LOCAL) == ""
        q.hazard(program)
    assert not seen
