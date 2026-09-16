"""The gates decide what a case is. These pin what each one refuses."""
from __future__ import annotations

from pipeline.acceptance import gate_case, run_test, validate_test_spec

GOOD = "print(sum(n for n in range(1,101) if n%2==0))"
BAD = "print(2551)"
T = {"kind": "stdout", "expect_stdout": "2550\n"}


def test_reference_passes_and_a_wrong_program_fails():
    assert run_test(T, GOOD).passed
    assert not run_test(T, BAD).passed


def test_a_discriminating_case_is_kept():
    r = gate_case(T, GOOD, BAD)
    assert r.kept and set(r.gates) == {"runs", "satisfiable", "discriminates", "stable"}


def test_a_test_nothing_can_fail_is_dropped():
    vacuous = {"kind": "stdout", "expect_stdout_re": ".*", "expect_exit": None}
    r = gate_case(vacuous, GOOD, BAD)
    assert not r.kept and r.gate == "discriminates"


def test_a_test_the_reference_fails_is_dropped_not_patched():
    r = gate_case({"kind": "stdout", "expect_stdout": "WRONG\n"}, GOOD, BAD)
    assert not r.kept and r.gate == "satisfiable"


def test_the_recorded_failure_passing_the_test_drops_the_case():
    # The negative control earns its keep: this test is satisfied by the very
    # program that was recorded as failing, so it measures the wrong thing.
    r = gate_case(T, GOOD, GOOD)
    assert not r.kept and r.gate == "discriminates"


def test_no_witness_is_dropped_by_default_and_keepable_on_request():
    assert gate_case(T, None, None).gate == "no-witness"
    assert gate_case(T, None, None, allow_no_witness=True).kept


def test_a_flaky_test_is_dropped():
    flaky = {"kind": "script",
             "checker": "import random; assert_(random.SystemRandom().random() < 0.5)"}
    seen = {gate_case(flaky, "pass", None, allow_no_witness=True).kept for _ in range(12)}
    assert False in seen          # it must be caught at least sometimes
    assert all(g in (True, False) for g in seen)


def test_script_checker_sees_the_files_the_program_left_behind():
    t = {"kind": "script",
         "checker": "assert_(open('out.txt').read()=='hello')"}
    assert run_test(t, "open('out.txt','w').write('hello')").passed
    assert not run_test(t, "open('out.txt','w').write('bye')").passed


def test_malformed_specs_are_rejected_statically():
    assert validate_test_spec({"kind": "nope"})
    assert validate_test_spec({"kind": "stdout"})
    assert validate_test_spec({"kind": "stdout", "expect_stdout": "x", "timeout_s": 0})
    assert validate_test_spec({"kind": "script", "checker": "  "})
    assert validate_test_spec({"kind": "stdout", "expect_stdout": "x",
                               "files": {"a": {"nope": 1}}})
    assert validate_test_spec({"kind": "stdout", "expect_stdout": "x"}) is None


def test_normalize_is_named_not_guessed():
    t = {"kind": "stdout", "expect_stdout": "a  \nb\n", "normalize": "rstrip"}
    assert run_test(t, "print('a');print('b')").passed
    strict = {"kind": "stdout", "expect_stdout": "a  \nb\n"}
    assert not run_test(strict, "print('a');print('b')").passed


def test_the_checker_sees_the_program_files_and_nothing_of_the_harness():
    # The whole point: a checker that lists the directory gets exactly what the
    # program created -- not the script, not the captured streams.
    t = {"kind": "script",
         "checker": "import os;assert_(sorted(os.listdir('.'))==['out.txt'], "
                    "sorted(os.listdir('.')))"}
    assert run_test(t, "open('out.txt','w').write('x')").passed


def test_a_program_cannot_shadow_a_stdlib_module_the_checker_imports():
    # The checker runs in the program's working directory, so anything that put
    # that directory on the checker's import path -- a PYTHONPATH pointed at it,
    # say -- would let a program named `json.py` decide its own verdict.
    prog = "open('json.py','w').write('raise SystemExit(3)\\n')\nprint('done')\n"
    t = {"kind": "script",
         "checker": "assert_(stdout.strip()=='done')\n"
                    "assert_(os.path.exists('json.py'))\n"}
    assert run_test(t, prog).passed
