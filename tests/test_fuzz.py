"""The fuzzer's pure parts, and the two properties a fuzz run is worthless without.

Nothing here spawns an engine for a *finding* — a test that ran the fuzzer for
real would either be too short to find anything or too long for CI, and would
fail on the day the Rust core grew a bug rather than on the day this module
grew one. So the generator is tested as the pure function of a
:class:`random.Random` that it is, the shrinker is driven by a fabricated
predicate, and the verdict logic is fed fabricated :class:`engines.Result`
objects — which is the only way to pin ``exit 90 with no contract line`` and
``timed out`` at all, since no engine produces them on demand.

The two properties:

**A refusal is not a counterexample.** Exit 90 with the contract line means the
generator wandered outside the subset. Score it as a finding and every run
drowns in the design working as intended.

**A failure that cannot be replayed is not a failure.** The seed has to fix the
programs exactly, and :func:`fuzz.render` has to print it.

The vocabulary tables get their own test because a transcription rots silently
and in one direction: a method added to the Rust and not to the table here is
surface that never gets generated, so the run still reports a large program
count and no findings and reads as evidence that the new method is correct.
Nothing else in this repository would notice.
"""

from __future__ import annotations

import random
import re

import pytest

from lypning import fuzz
from lypning import engines as eng
from lypning import paths

# Keep every count in this file SMALL. CI must not run a long fuzz — the fuzzer
# is a tool you point at the engine deliberately, not a test that runs on every
# commit and adds a minute to it.
ITERATIONS = 3


# --- the generator -----------------------------------------------------------


def test_the_same_seed_generates_the_same_program():
    assert fuzz.generate(random.Random(4242)) == fuzz.generate(random.Random(4242))


def test_the_same_seed_generates_the_same_whole_run():
    # Not implied by the one above: `run` draws a child seed per program from a
    # master, and a stream that drifted would still pass the single-program
    # check while making --seed useless.
    def stream(seed):
        master = random.Random(seed)
        return [fuzz.generate(random.Random(master.randrange(1, 2 ** 31)))
                for _ in range(8)]
    assert stream(7) == stream(7)


def test_a_different_seed_generates_different_programs():
    a = [fuzz.generate(random.Random(1 + i)) for i in range(8)]
    b = [fuzz.generate(random.Random(101 + i)) for i in range(8)]
    assert a != b


def test_a_generated_program_is_syntactically_valid_python():
    # The whole argument for a typed generator: it emits programs that are valid
    # by construction. A generator that emits syntax errors spends its budget
    # confirming that both interpreters can raise one, and the deep expressions
    # are never reached.
    for seed in range(60):
        program = fuzz.generate(random.Random(seed))
        compile(program, "<probe>", "exec")


def test_a_generated_program_never_reaches_outside_the_process():
    # Nothing whose answer CPython does not fix, and nothing that touches the
    # filesystem: a fuzzer that reports unspecified behaviour trains its reader
    # to skim, and the one real finding then goes past unread.
    forbidden = ("open(", "input(", "import ", "id(", "hash(", "__")
    for seed in range(120):
        program = fuzz.generate(random.Random(seed))
        for token in forbidden:
            assert token not in program, "%s in seed %d" % (token, seed)


def test_every_call_the_generator_emits_is_a_builtin_lypning_claims():
    # Checked against what is actually generated rather than against a list
    # somebody maintains: a name in the table that the grammar never emits is
    # harmless, a name the grammar emits that lypning does not claim is a probe
    # that reports a refusal forever and costs budget doing it.
    called = set()
    for seed in range(200):
        for name in re.findall(r"(?<![.\w\"'])([A-Za-z_]\w*)\(",
                               fuzz.generate(random.Random(seed))):
            called.add(name)
    unknown = sorted(n for n in called if n not in fuzz.GENERATED_BUILTINS)
    assert unknown == []


def test_a_probe_is_exactly_one_output_line():
    # Line N is probe N is what lets a diff localise a finding without a
    # bisection step. If a probe could emit two lines, every probe after a
    # divergence would be reported as one too.
    program = fuzz.build_program(["1 + 1", '"a"', "[1, 2]"])
    assert len(re.findall(r"^    print\(repr\(", program, re.M)) == 3
    assert fuzz.expressions(program) == ["1 + 1", '"a"', "[1, 2]"]


def test_a_parenthesised_expression_survives_the_round_trip():
    e = "(('AbC'.swapcase() + 'x') * 2)"
    assert fuzz.expressions(fuzz.build_program([e])) == [e]


def test_the_except_chain_puts_leaves_before_their_bases():
    # A base class earlier in the chain makes every subclass after it dead, and
    # the probe then reports the wrong class — which is a divergence the fuzzer
    # would report against an engine that got it right.
    classes = [eval(name) for name in fuzz.REPORTED_EXCEPTIONS]  # noqa: S307
    for i, base in enumerate(classes):
        for leaf in classes[i + 1:]:
            assert not issubclass(leaf, base), \
                "%s is shadowed by %s" % (leaf.__name__, base.__name__)


# --- the vocabulary, against the Rust ----------------------------------------


def _rust_table(source: str, name: str):
    m = re.search(r"const %s: &\[&str\] = &\[(.*?)\];" % name, source, re.S)
    assert m, "%s not found — did the table move or get renamed?" % name
    return sorted(re.findall(r'"([^"]+)"', m.group(1)))


@pytest.fixture
def methods_rs():
    p = paths.RUST_DIR / "src" / "methods.rs"
    if not p.is_file():
        pytest.skip("the Rust crate source is not in this tree")
    return p.read_text(encoding="utf-8")


@pytest.mark.parametrize("table,name", [
    (fuzz.STR_METHODS, "STR_METHODS"),
    (fuzz.LIST_METHODS, "LIST_METHODS"),
    (fuzz.DICT_METHODS, "DICT_METHODS"),
    (fuzz.BYTES_METHODS, "BYTES_METHODS"),
    (fuzz.TUPLE_METHODS, "TUPLE_METHODS"),
])
def test_the_fuzzer_knows_every_method_lypning_implements(methods_rs, table, name):
    assert sorted(table) == _rust_table(methods_rs, name)


def test_the_fuzzers_set_methods_are_a_subset(methods_rs):
    # Deliberately a subset: docs/LYPNING.md §3 refuses anything exposing set
    # iteration order, so only the order-free operations are worth generating.
    # The direction is asserted, not equality.
    claimed = set(_rust_table(methods_rs, "SET_METHODS"))
    assert set(fuzz.SET_METHODS) <= claimed
    assert set(fuzz._SET_ALGEBRA) | set(fuzz._SET_PREDICATES) == set(fuzz.SET_METHODS)


def test_the_generated_builtins_are_ones_lypning_claims():
    p = paths.RUST_DIR / "src" / "builtins.rs"
    if not p.is_file():
        pytest.skip("the Rust crate source is not in this tree")
    claimed = set(_rust_table(p.read_text(encoding="utf-8"), "BUILTINS"))
    assert fuzz.GENERATED_BUILTINS <= claimed


# --- the verdict -------------------------------------------------------------


def _res(rc=0, stdout="1\n", stderr="", engine=eng.LYPNING, timed_out=False):
    return eng.Result(engine, "/bin/engine", rc, stdout, stderr, 1_000_000, timed_out)


def test_a_refusal_is_a_refusal_and_not_a_counterexample():
    refusal = _res(90, "", "lypning: unsupported: bigint: integer result beyond 64-bit range\n")
    assert fuzz.judge(refusal, None) == "refused"


def test_identical_answers_agree():
    assert fuzz.judge(_res(), _res(engine=eng.CPYTHON)) == ""


def test_different_stdout_is_a_counterexample():
    assert fuzz.judge(_res(stdout="2\n"), _res(engine=eng.CPYTHON)) == fuzz.OUTPUT


def test_exit_90_without_the_contract_line_breaks_the_contract():
    # Not a refusal, so not coverage: a crash scored as coverage is a bug the
    # dispatcher will happily fall through on.
    assert fuzz.judge(_res(90, "", "Segmentation fault\n"), None) == fuzz.CONTRACT


def test_a_refusal_that_wrote_to_stdout_breaks_the_contract():
    # Invariant 2: exit 90 is one line on stderr and NOTHING on stdout. A
    # half-printed answer is a half-completed program the dispatcher is about to
    # run again on the next tier.
    got = _res(90, "1\n", "lypning: unsupported: module: import re\n")
    assert fuzz.judge(got, _res(engine=eng.CPYTHON)) == fuzz.CONTRACT


def test_a_timeout_is_a_harness_error_not_a_disagreement():
    assert fuzz.judge(_res(124, "", timed_out=True), _res(engine=eng.CPYTHON)) == fuzz.HARNESS


def test_a_reference_that_could_not_run_is_a_harness_error():
    # CPython failing to run a generated program is a bug in the GENERATOR, and
    # it has to be as loud as a bug in the engine — a generator quietly emitting
    # SyntaxErrors measures nothing while reporting a large number.
    assert fuzz.judge(_res(), _res(rc=1, stdout="", engine=eng.CPYTHON)) == fuzz.HARNESS


def test_an_engine_crash_is_a_counterexample():
    assert fuzz.judge(_res(rc=1, stdout=""), _res(engine=eng.CPYTHON)) == fuzz.CRASH


# --- shrinking ---------------------------------------------------------------


def test_shrink_reduces_a_multi_probe_program_to_the_failing_probe():
    program = fuzz.build_program(['"ok"', '"AbC".swapcase()', "1 + 1", "[2]"])

    def still_fails(candidate):
        return "swapcase" in candidate

    small = fuzz.shrink(program, still_fails)
    # The receiver goes too: `swapcase` is a str method, so `""` is offered as
    # a receiver and kept because the predicate still accepts it.
    assert fuzz.expressions(small) == ['"".swapcase()']
    assert len(small) < len(program)
    assert still_fails(small)


def test_shrink_peels_the_nest_off_the_failing_leaf():
    program = fuzz.build_program(['((("AbC".swapcase() + "x") * 2)[1:])'])

    def still_fails(candidate):
        return "swapcase" in candidate

    small = fuzz.shrink(program, still_fails)
    assert fuzz.expressions(small) == ['"".swapcase()']


def test_shrink_never_substitutes_a_different_failure():
    # The usual way a shrinker wastes an afternoon: it reduces to something
    # smaller that fails for an unrelated reason and reports that instead.
    program = fuzz.build_program(['(1 + round(2.675, 2))'])
    seen = []

    def still_fails(candidate):
        seen.append(candidate)
        return "round(2.675, 2)" in candidate

    small = fuzz.shrink(program, still_fails)
    assert fuzz.expressions(small) == ["round(2.675, 2)"]
    # `1` alone is smaller and is offered; it must have been rejected, because
    # the only thing that makes a shrink trustworthy is that every step was
    # verified to still fail the SAME way.
    assert any(fuzz.expressions(c) == ["1"] for c in seen)


def test_shrink_returns_the_program_unchanged_when_nothing_helps():
    program = fuzz.build_program(["1 + 1"])
    assert fuzz.shrink(program, lambda _c: False) == program


def test_shrink_respects_its_budget():
    program = fuzz.build_program(["1 + 1 + 1 + 1", "2 + 2", "3 + 3"])
    calls = []

    def still_fails(candidate):
        calls.append(candidate)
        return True

    fuzz.shrink(program, still_fails, budget=3)
    assert len(calls) <= 3


def test_shrink_leaves_a_program_it_cannot_parse_alone():
    assert fuzz.shrink("print(1)\n", lambda _c: True) == "print(1)\n"


def test_candidates_are_strictly_smaller_and_keep_the_failing_leaf():
    e = '(("AbC".swapcase() + "x") * 2)'
    cands = fuzz.candidates(e)
    assert cands
    for c in cands:
        assert len(c) < len(e)
    assert any("swapcase" in c for c in cands)


# --- the report --------------------------------------------------------------


def test_render_prints_the_seed_so_a_failure_can_be_replayed():
    report = fuzz.FuzzReport(ran=10, agreed=10, seed=123456, iterations=10,
                             binary="/x/lypning", reference="/usr/bin/python3.11")
    text = fuzz.render(report)
    assert "123456" in text
    assert "--seed 123456" in text
    assert report.ok


def test_a_run_that_did_not_happen_is_not_a_pass():
    # Two engines that both failed to spawn produce two empty stdouts, which
    # compare equal. A harness that cannot run the program must never report a
    # clean bill of health.
    report = fuzz.FuzzReport(ran=0, agreed=0, seed=1)
    assert not report.ok
    assert "NOT a clean bill of health" in fuzz.render(report)


def test_render_shows_both_answers_for_a_counterexample():
    c = fuzz.Counterexample(
        program=fuzz.build_program(['"a".partition("")']), seed=99,
        cpython=fuzz.Answer("! ValueError\n", 0), engine=fuzz.Answer("('a', '', '')\n", 0),
        kind=fuzz.OUTPUT, shrunk=True)
    text = fuzz.render(fuzz.FuzzReport(ran=1, counterexamples=[c], seed=1, iterations=1))
    assert '"a".partition("")' in text
    assert "ValueError" in text and "('a', '', '')" in text
    assert "FAIL" in text


def test_fuzzing_the_reference_against_itself_is_refused_not_reported_as_clean():
    # `--engine cpython` would run CPython against CPython: every program agrees
    # because both sides ARE the oracle, and the run prints "agreed N, ok" over
    # N programs that could not have come out any other way. Exactly the shape
    # `ok` exists to catch, so it is refused in the library rather than only in
    # argparse — a programmatic caller does not go through argparse.
    report = fuzz.run(iterations=ITERATIONS, seed=1, engine=eng.CPYTHON)
    assert report.not_run and report.ran == 0
    assert not report.ok, "a run that compared nothing is not a pass"
    assert "reference" in fuzz.render(report)


def test_the_cli_will_not_take_the_reference_as_an_arm(capsys):
    from lypning import cli
    with pytest.raises(SystemExit) as e:
        cli.main(["fuzz", "--engine", eng.CPYTHON])
    assert e.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_an_unbuilt_engine_is_a_status_line_not_a_crash(monkeypatch):
    # Tested by pointing the finder at nothing rather than by reasoning about
    # it: every path that touches an engine has to degrade to "not built".
    monkeypatch.setattr(eng, "find", lambda _e: None)
    report = fuzz.run(iterations=ITERATIONS, seed=1)
    assert report.unbuilt and report.ran == 0 and not report.ok
    assert "not built" in fuzz.render(report)


# --- end to end, deliberately tiny -------------------------------------------


def test_a_short_run_against_the_real_engine_accounts_for_every_program(lypning_bin):
    report = fuzz.run(iterations=ITERATIONS, seed=20260820)
    assert report.ran == ITERATIONS
    # The identity the report is worth nothing without: nothing is unaccounted
    # for, so a shrinking `agreed` cannot hide behind a growing `refused`.
    assert report.ran == report.agreed + report.refused + sum(
        c.count for c in report.counterexamples)
    assert report.seed == 20260820
    assert report.binary == str(lypning_bin)


def test_the_generator_is_replaceable_so_a_run_can_be_pinned(lypning_bin):
    # A program that cannot possibly diverge, so this asserts the plumbing —
    # spawn, compare, account — and not the engine.
    report = fuzz.run(iterations=2, seed=3,
                      generator=lambda _rng: fuzz.build_program(["1 + 1"]))
    assert report.ran == 2 and report.agreed == 2 and report.ok


# --- the front door ----------------------------------------------------------


@pytest.fixture
def trivial_programs(monkeypatch):
    """Pin the generator to something that cannot possibly diverge.

    The CLI's job is to resolve arguments, render, and map the outcome onto an
    exit code; wiring a real fuzz run into that test would make the exit code
    depend on whether the engine has a bug today, which is the sibling module's
    question and not this one's.
    """
    monkeypatch.setattr(fuzz, "generate", lambda _rng, probes=None:
                        fuzz.build_program(["1 + 1"]))


def test_the_cli_exits_0_and_prints_the_seed_when_nothing_disagrees(
        lypning_bin, trivial_programs, capsys):
    from lypning import cli
    assert cli.main(["fuzz", "--iterations", "2", "--seed", "11"]) == 0
    out = capsys.readouterr().out
    assert "--seed 11" in out and "ran 2" in out


def test_the_cli_json_is_machine_readable(lypning_bin, trivial_programs, capsys):
    import json
    from lypning import cli
    assert cli.main(["fuzz", "--iterations", "2", "--seed", "11", "--json"]) == 0
    obj = json.loads(capsys.readouterr().out)
    assert obj["seed"] == 11 and obj["ran"] == 2 and obj["ok"] is True
    assert obj["counterexamples"] == []


def test_the_cli_exits_1_on_a_counterexample(lypning_bin, monkeypatch, capsys):
    from lypning import cli
    monkeypatch.setattr(fuzz, "run", lambda **_kw: fuzz.FuzzReport(
        ran=1, seed=5, iterations=1,
        counterexamples=[fuzz.Counterexample(
            program=fuzz.build_program(['"".partition("")']), seed=5,
            cpython=fuzz.Answer("! ValueError\n", 0),
            engine=fuzz.Answer("('', '', '')\n", 0), shrunk=True)]))
    assert cli.main(["fuzz"]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_the_cli_exits_2_when_the_engine_is_not_built(monkeypatch, capsys):
    # Exit 2, not 1 and not 0: nothing ran, and the fix is a command. A fuzz run
    # that could not happen must never be reported as a clean one.
    from lypning import cli
    monkeypatch.setattr(eng, "find", lambda _e: None)
    assert cli.main(["fuzz", "--iterations", "1"]) == 2
    assert "not built" in capsys.readouterr().err
