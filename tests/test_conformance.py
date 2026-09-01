"""The verdict classifier, and the net under the battery.

Two things are tested and nothing else. The classifier is a pure function of two
:class:`engines.Result` objects, so it is fed fabricated ones — which is the only
way to pin ``timeout`` and ``exit 90 with no refusal line`` at all, since no
engine produces them on demand. The net is tested against a throwaway git repo
with a program that really does write into it: the reason the net exists is that
the first battery run upstream rewrote 34 tracked files, and a net that has never
been seen to fire is a comment.
"""

from __future__ import annotations

import pytest

from lypning import conformance, corpus
from lypning import engines as eng
from lypning.conformance import MATCH, MISMATCH, UNSUPPORTED

from conftest import requires_git

ENTRY = corpus.Entry(id="py-test", program="print('hello')")


def _res(rc=0, stdout="hello\n", stderr="", *, engine=eng.LYPNING, binary="/bin/engine",
         timed_out=False):
    return eng.Result(engine, binary, rc, stdout, stderr, 1_000_000, timed_out)


def _classify(got, ref=None, engine=eng.LYPNING, entry=ENTRY):
    return conformance.classify(ref if ref is not None else _res(engine=eng.CPYTHON),
                                got, engine, entry)


def test_identical_stdout_and_exit_code_is_a_match():
    v = _classify(_res())
    assert v.verdict == MATCH
    assert not v.failed
    assert v.entry_id == "py-test"


def test_a_refusal_is_coverage_not_a_failure():
    v = _classify(_res(90, "", "lypning: unsupported: module: import ctypes\n"))
    assert v.verdict == UNSUPPORTED
    assert (v.kind, v.detail) == ("module", "import ctypes")
    assert not v.failed


def test_exit_90_without_the_contract_line_is_a_mismatch():
    # The alternative is counting a crash as coverage, which moves the build
    # order towards features nothing was actually blocked on.
    v = _classify(_res(90, "", "Segmentation fault\n"))
    assert v.verdict == MISMATCH
    assert v.kind == "contract"


def test_the_mixture_arm_may_relay_any_tier_s_refusal():
    v = _classify(_res(90, "", "lypning-mp: unsupported: syntax: f-string\n",
                       engine=conformance.MIXTURE), engine=conformance.MIXTURE)
    assert v.verdict == UNSUPPORTED
    assert v.kind == "syntax"


def test_a_refusal_line_from_something_that_is_not_a_tier_is_not_a_refusal():
    v = _classify(_res(90, "", "someprogram: unsupported: module: nope\n"))
    assert v.verdict == MISMATCH
    assert v.kind == "contract"


def test_differing_stdout_is_a_mismatch_with_the_first_differing_line():
    v = _classify(_res(stdout="hello\nextra\n"), ref=_res(stdout="hello\nother\n",
                                                          engine=eng.CPYTHON))
    assert v.verdict == MISMATCH
    assert v.kind == "stdout"
    assert "line 2" in v.detail
    assert v.expected_stdout and v.actual_stdout  # evidence is kept for a failure


def test_a_matching_verdict_carries_no_stdout():
    # 839 entries times three arms times two copies of an 18k-line stdout is a
    # memory hazard, so evidence is kept only where it is evidence.
    v = _classify(_res())
    assert v.expected_stdout == "" and v.actual_stdout == ""


def test_a_differing_exit_code_is_a_mismatch():
    v = _classify(_res(rc=1))
    assert v.verdict == MISMATCH
    assert v.kind == "exit"
    assert v.expected_rc == 0 and v.actual_rc == 1


def test_a_timeout_is_a_mismatch_not_a_slow_program():
    # The reference ran inside the same deadline and finished.
    v = _classify(_res(rc=124, stdout="", timed_out=True))
    assert v.verdict == MISMATCH
    assert v.kind == "timeout"


def test_an_absent_engine_is_a_mismatch_of_its_own_kind():
    v = _classify(_res(rc=127, stdout="", binary=""))
    assert v.verdict == MISMATCH
    assert v.kind == "unbuilt"


def test_a_silent_engine_where_cpython_reported_an_error():
    ref = _res(rc=1, stdout="", stderr="Traceback…\n", engine=eng.CPYTHON)
    v = _classify(_res(rc=1, stdout="", stderr=""), ref=ref)
    assert v.verdict == MISMATCH
    assert v.kind == "stderr"


def test_a_cpython_warning_is_not_an_error_the_engine_had_to_reproduce():
    # Python 3.14 warns on `return` inside `finally` (PEP 765) and carries on.
    # stdout and exit code agree, so an engine that says nothing is right, not
    # silent about a failure.
    ref = _res(stderr="/tmp/p.py:109: SyntaxWarning: 'return' in a 'finally' block\n"
                      "  return \"b\"\n", engine=eng.CPYTHON)
    assert _classify(_res(), ref=ref).verdict == MATCH
    # The -c shape has no source echo under it.
    ref = _res(stderr="<string>:1: SyntaxWarning: \"\\d\" is an invalid escape sequence.\n",
               engine=eng.CPYTHON)
    assert _classify(_res(), ref=ref).verdict == MATCH


def test_a_warning_does_not_hide_the_error_beside_it():
    ref = _res(rc=1, stdout="",
               stderr="<string>:1: SyntaxWarning: x\nTraceback (most recent call last):\n",
               engine=eng.CPYTHON)
    v = _classify(_res(rc=1, stdout="", stderr=""), ref=ref)
    assert v.verdict == MISMATCH
    assert v.kind == "stderr"


def test_stderr_text_itself_is_not_compared():
    # Traceback text carries paths, line numbers and interpreter internals a
    # subset runtime has no business reproducing; the exit code is the contract.
    ref = _res(rc=1, stdout="", stderr="Traceback (most recent call last):\n…\n",
               engine=eng.CPYTHON)
    assert _classify(_res(rc=1, stdout="", stderr="error: boom\n"), ref=ref).verdict == MATCH


def test_stdout_is_not_compared_for_a_run_specific_program():
    entry = corpus.Entry(id="py-clock", program="import time; print(time.time())")
    assert conformance.is_nondeterministic(entry)
    v = _classify(_res(stdout="1755000000.0\n"),
                  ref=_res(stdout="1755000001.5\n", engine=eng.CPYTHON), entry=entry)
    assert v.verdict == MATCH
    assert v.detail == "stdout uncompared"


def test_a_run_specific_program_still_has_its_exit_code_compared():
    entry = corpus.Entry(id="py-clock", program="import time; print(time.time())")
    assert _classify(_res(rc=2, stdout="x\n"), entry=entry).verdict == MISMATCH


def test_set_order_alone_is_not_a_divergence():
    v = _classify(_res(stdout="{1, 2, 3}\n"), ref=_res(stdout="{3, 1, 2}\n",
                                                       engine=eng.CPYTHON))
    assert v.verdict == MATCH


def test_dict_order_is_specified_so_reordering_one_is_a_divergence():
    v = _classify(_res(stdout="{'a': 1, 'b': 2}\n"),
                  ref=_res(stdout="{'b': 2, 'a': 1}\n", engine=eng.CPYTHON))
    assert v.verdict == MISMATCH


def test_absolute_paths_are_what_keeps_a_program_out_of_the_battery():
    assert conformance.absolute_paths("open('/etc/passwd').read()") == ["/etc/passwd"]
    # A URL route and a regex fragment are not filesystem paths, and skipping
    # them would take a tenth of the corpus out of the measurement for nothing.
    assert conformance.absolute_paths("re.match(r'/api/chat/', u)") == []
    assert conformance.absolute_paths("print(6/2/1)") == []


@requires_git
def test_the_git_net_reports_and_restores_what_a_program_dirtied(git_repo, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(git_repo))
    monkeypatch.setenv("LYPNING_DIRT_NEW", str(git_repo / "collateral.txt"))
    monkeypatch.setenv("LYPNING_DIRT_TRACKED", str(git_repo / "tracked.txt"))
    # The path is read from the environment at run time: a literal one in the
    # program text would be skipped by the absolute-path guard instead, which is
    # the OTHER half of the sandbox and not the half under test here.
    program = (
        "import os\n"
        "open(os.environ['LYPNING_DIRT_NEW'], 'w').write('escaped\\n')\n"
        "open(os.environ['LYPNING_DIRT_TRACKED'], 'a').write('clobbered\\n')\n"
    )
    entry = corpus.Entry(id="py-escape", program=program)

    # No arms: the reference run alone is enough to dirty the tree, and this test
    # must not need an engine.
    report = conformance.run([entry], engines=[], timeout=30)

    assert report.damage, "a program wrote into the repository and the net said nothing"
    assert any("collateral.txt" in d for d in report.damage)
    assert any("tracked.txt" in d for d in report.damage)
    assert not report.ok, "a run that rewrote the tree it measured cannot report ok"
    assert not (git_repo / "collateral.txt").exists()
    assert (git_repo / "tracked.txt").read_text(encoding="utf-8") == "original\n"


@requires_git
def test_the_net_sees_a_second_change_to_an_already_dirty_file(git_repo, monkeypatch):
    """The state a developer actually runs the battery in: mid-edit.

    A file that was already modified stays `` M`` no matter what the corpus
    appends to it, so a net that compares the SET of dirty paths reports nothing
    — and the restore must put back the UNCOMMITTED content, not the commit.
    """
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(git_repo))
    (git_repo / "tracked.txt").write_text("my own edit\n", encoding="utf-8")
    monkeypatch.setenv("LYPNING_DIRT_TRACKED", str(git_repo / "tracked.txt"))
    entry = corpus.Entry(
        id="py-second-write",
        program="import os\nopen(os.environ['LYPNING_DIRT_TRACKED'], 'a').write('clobbered\\n')\n",
    )

    report = conformance.run([entry], engines=[], timeout=30)

    assert any("tracked.txt" in d for d in report.damage), report.damage
    assert not report.ok
    assert (git_repo / "tracked.txt").read_text(encoding="utf-8") == "my own edit\n"


@requires_git
def test_the_net_sees_a_file_created_inside_an_untracked_directory(git_repo, monkeypatch):
    """git collapses an untracked directory into one `?? dir/` entry."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(git_repo))
    (git_repo / "scratch").mkdir()
    (git_repo / "scratch" / "kept.txt").write_text("keep\n", encoding="utf-8")
    monkeypatch.setenv("LYPNING_DIRT_NEW", str(git_repo / "scratch" / "escaped.txt"))
    entry = corpus.Entry(
        id="py-into-untracked-dir",
        program="import os\nopen(os.environ['LYPNING_DIRT_NEW'], 'w').write('escaped\\n')\n",
    )

    report = conformance.run([entry], engines=[], timeout=30)

    assert any("escaped.txt" in d for d in report.damage), report.damage
    assert not (git_repo / "scratch" / "escaped.txt").exists()
    # The directory was already there and is not the corpus' doing.
    assert (git_repo / "scratch" / "kept.txt").read_text(encoding="utf-8") == "keep\n"


@requires_git
def test_dirty_paths_is_none_outside_a_work_tree(tmp_path):
    assert conformance.dirty_paths(tmp_path) is None


@requires_git
def test_a_clean_tree_has_no_dirty_paths(git_repo):
    assert conformance.dirty_paths(git_repo) == {}


def test_an_unbuilt_engine_is_an_absent_arm_not_a_failed_one(no_micropython):
    # "Not built" and "wrong" are different facts and only one of them is a bug.
    report = conformance.run([ENTRY], engines=[eng.MICROPYTHON], timeout=30)
    assert report.unbuilt == [eng.MICROPYTHON]
    assert report.engines == {}
    assert report.mismatches == 0
    assert report.ok


def test_every_arm_runs_in_the_same_environment_as_the_reference_it_is_scored_against():
    """The mixture arm used to be the one exception, and it was flaky for it.

    :func:`conformance._env_for` is not decoration. ``PYTHONHASHSEED=0`` is
    there because two runs of CPython must agree with each other before either
    can be a reference; ``LC_ALL=C.UTF-8`` because under ``LC_ALL=C`` every
    non-ASCII byte decodes to U+FFFD, so two engines printing *different*
    non-ASCII compare equal and a MISMATCH is scored MATCH.

    Every arm was handed it except the mixture, which called
    :func:`engines.dispatch` — a function that had no ``env`` parameter to hand
    it to. So the one arm that measures what a user actually runs was the one
    arm scored against a reference it did not share an environment with, and a
    program whose output depends on set iteration order flipped between MATCH
    and MISMATCH from run to run.

    Asserted through the chain rather than by reading the source, because what
    matters is that the value reaches the tier that finally answers — including
    across a fall-through, which is a second child this has to survive.
    """
    d = eng.dispatch("import os\nprint(os.environ.get('LYPNING_ENV_PROBE'))\n",
                     env={"LYPNING_ENV_PROBE": "reached"}, timeout=30)
    assert d.result.stdout == "reached\n", (
        "the environment did not reach the tier that answered (%s)" % d.engine)


def test_the_battery_is_stable_on_a_program_whose_output_depends_on_set_order():
    # The program this was found on, reduced: `min` over a set with tied keys
    # returns whichever element iteration reached first, and CPython randomises
    # that per process unless the seed is pinned. Under the fixed environment
    # the answer is the same one every time, so the battery is comparing an
    # answer rather than a coin flip. Ten runs, not one: a single run of a coin
    # flip passes half the time.
    entry = corpus.Entry(
        id="py-set-order-through-min",
        program='print(min({(1,"z"),(1,"a")}, key=lambda t: t[0]))\n',
    )
    seen = set()
    for _ in range(10):
        report = conformance.run([entry], engines=[conformance.MIXTURE], timeout=30)
        arm = report.engines.get(conformance.MIXTURE)
        if arm is None:
            pytest.skip("no engine built to dispatch to")
        seen.add(arm.verdicts[0].verdict)
    assert seen == {conformance.MATCH}, "the battery disagreed with itself: %s" % sorted(seen)


def test_plan_ranks_by_cpython_reach_not_by_block_count() -> None:
    """The build order's key is cost, and the two orderings genuinely differ.

    Ranking by block count is what sent two iterations of the improvement loop
    at `import re` (185 blocks, 12 of them reaching CPython) and `import
    pathlib` (83 blocks, none reaching CPython, so worth nothing at all). The
    ordering is the steering wheel, so it is pinned here rather than left to
    whoever reads the table next.
    """
    from lypning import conformance as conf
    from lypning import engines as eng

    def verdict(entry_id, kind):
        v = conf.Verdict.__new__(conf.Verdict)
        object.__setattr__(v, "entry_id", entry_id)
        object.__setattr__(v, "verdict", conf.UNSUPPORTED)
        object.__setattr__(v, "kind", "module")
        object.__setattr__(v, "detail", kind)
        return v

    # `cheap` blocks three programs and none of them reach CPython; `dear`
    # blocks one and it does. Count says cheap-first, cost says dear-first.
    verdicts = [verdict("a", "cheap"), verdict("b", "cheap"), verdict("c", "cheap"),
                verdict("d", "dear")]
    arm = conf.EngineReport.__new__(conf.EngineReport)
    object.__setattr__(arm, "verdicts", verdicts)
    object.__setattr__(arm, "total", 4)
    report = conf.Report.__new__(conf.Report)
    object.__setattr__(report, "engines", {conf.LYPNING: arm})
    object.__setattr__(report, "total", 4)
    object.__setattr__(report, "routes", {
        "a": eng.Route(eng.MICROPYTHON, "module", "cheap"),
        "b": eng.Route(eng.MICROPYTHON, "module", "cheap"),
        "c": eng.Route(eng.MICROPYTHON, "module", "cheap"),
        "d": eng.Route(eng.CPYTHON, "module", "dear"),
    })

    rows = conf.plan(report)
    assert [r[0] for r in rows] == ["module: dear", "module: cheap"], rows
    assert conf.plan_cost(report) == {"module: dear": 1}

    # With no routes — the mixture arm did not run — there is nothing to rank
    # by, and the honest fallback is the block count.
    object.__setattr__(report, "routes", {})
    assert [r[0] for r in conf.plan(report)] == ["module: cheap", "module: dear"]
    assert conf.plan_cost(report) == {}
