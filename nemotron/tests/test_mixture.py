"""The training mixture: three populations, and the ones that cannot teach the task.

The corpus is not one task. A `lypning` test with ``require_tier1`` true asks for
the program to be REWRITTEN into the subset — the thing the fine-tune is for. The
same test with ``require_tier1`` false is a *ceiling* case whose right answer
keeps the import and takes the fallback. A test of any other kind never asks an
engine anything at all. Sampling treats all three the same way and yield decides
the mixture, which is the wrong way round: the ceiling cases pass whenever the
model can copy, so they yield most and teach least.

Every test here asks the question of the CASE'S OWN TEST, never of its category
name, because the name is written by the harvester and the test is what the eval
runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import split as splitmod
from pipeline.jsonio import read_jsonl, write_jsonl
from pipeline.sample import CEILING_KEEP, census, fold_draws, population, sampling_pool
from pipeline.schema import make_case

DATA = Path(__file__).resolve().parents[1] / "data"


def _lyp(require_tier1, **kw):
    test = {"kind": "lypning", "expect_stdout": kw.pop("out", "1\n"), "expect_exit": 0,
            "engines": ["lypning", "lypning-l"], "require_tier1": require_tier1,
            "timeout_s": 10}
    return make_case(prompt=kw.pop("prompt", "p"), test=test, **kw)


def test_the_population_is_read_off_the_test_not_the_name():
    rewrite = _lyp(True, category="refused:module")
    ceiling = _lyp(False, category="ceiling:module")
    other = make_case(prompt="p3", test={"kind": "stdout", "expect_stdout": "1\n"},
                      category="unobserved")
    assert population(rewrite) == "rewrite"
    assert population(ceiling) == "ceiling"
    assert population(other) == "unobserved"

    # A case mislabelled by hand is still what its test says it is: the test is
    # what the eval runs, so it is what decides which task was measured.
    mislabelled = _lyp(False, category="refused:module")
    assert population(mislabelled) == "ceiling"


def test_the_corpus_census_by_population():
    """Recorded 2026-09-13 over the 249-case corpus.

    These move when the corpus grows, and growth is expected — a change here is
    a corpus change and belongs in a commit that says so. What must not change
    silently is the RATIO: the populations are 71/18/10, so a sampling run that
    does not choose its mixture will be given one.
    """
    corpus = DATA / "corpus.jsonl"
    if not corpus.exists():
        pytest.skip("no corpus in this checkout")
    cases = read_jsonl(corpus)
    counts = census(cases)
    assert sum(counts.values()) == len(cases)
    assert counts == {"rewrite": 178, "ceiling": 45, "unobserved": 26}, (
        "the population census moved (was rewrite 178, ceiling 45, unobserved 26 "
        "over 249 cases on 2026-09-13)")


def test_the_category_name_agrees_with_the_test_today():
    """Belt and braces, and it is the braces that are load-bearing.

    Nothing enforces the agreement — the name is written once at harvest and the
    test is edited by hand — so this checks it rather than assuming it. If it
    ever fails, `population` is right and the name is wrong.
    """
    corpus = DATA / "corpus.jsonl"
    if not corpus.exists():
        pytest.skip("no corpus in this checkout")
    for case in read_jsonl(corpus):
        expected = {"rewrite": "refused:", "ceiling": "ceiling:",
                    "unobserved": "unobserved"}[population(case)]
        assert case["category"].startswith(expected), case["id"]


# --- what a ceiling case is worth, and what it is not ------------------------


def _draw(case, program, **kw):
    rec = {"case_id": case["id"], "draw": 0, "how": "fenced-python", "passed": True,
           "program": program, "kept": True, "reason": "pass",
           "discrimination": {"verdict": "undecidable", "method": "no-perturbation",
                              "detail": "", "carried": 0.0}}
    rec.update(kw)
    return rec


def test_a_ceiling_case_contributes_one_row_and_a_rewrite_case_four(tmp_path):
    """Because a second ceiling target is the same answer typed twice.

    Measured over the recorded v1 draws on 2026-09-13: distinct kept programs
    within one ceiling case are 0.865 mean pairwise similar — above
    `split.SIMILARITY_CEILING`, the number this repository already uses to call
    two things the same question — against 0.427 within a rewrite case.
    """
    ceiling = _lyp(False, prompt="reproduce this", out="1\n")
    rewrite = _lyp(True, prompt="rewrite this", out="1\n")
    programs = ["print(1)", "print(0 + 1)", "print(len('x'))", "print(int('1'))"]
    write_jsonl(tmp_path / "draws.jsonl",
                [_draw(ceiling, p) for p in programs] + [_draw(rewrite, p) for p in programs])

    report = fold_draws(tmp_path, [ceiling, rewrite], k=4, keep=4)
    rows = read_jsonl(tmp_path / "sft.jsonl")
    by_pop = {}
    for row in rows:
        by_pop[row["population"]] = by_pop.get(row["population"], 0) + 1
    assert by_pop == {"ceiling": CEILING_KEEP, "rewrite": 4}
    assert report["sft_examples_on_task"] == 4, (
        "the abandon threshold is about the rewrite rows; a total that counts "
        "ceiling rows can clear it while the task population does not")
    assert report["by_population"]["ceiling"]["rows"] == CEILING_KEEP


# --- the pool: four exclusions, each mechanical -------------------------------


def _engine():
    from pipeline import engines as eng
    binary = eng.engine_path("lypning-l") or eng.engine_path("lypning")
    if not binary:
        pytest.skip("no lypning binary: run `lypning build --rust`")
    return binary


def _freeze(tmp_path, cases):
    write_jsonl(tmp_path / "corpus.jsonl", cases)
    splitmod.freeze(tmp_path / "corpus.jsonl")


def test_the_pool_drops_what_cannot_teach_the_task(tmp_path):
    """Unobserved, degenerate and unsatisfiable, each for its own reason.

    The degenerate one is the dangerous shape and the reason this is not just a
    tidy-up: the engine RUNS the program the prompt hands over, so that program
    passes the case's own acceptance test, and a sampler that keeps it writes a
    row whose prompt says "rewrite it so it does not use this" and whose answer
    uses it.
    """
    engine = _engine()
    good = _lyp(True, prompt="rewrite me, I import a module the engine will not take",
                out="64\n",
                negatives=[{"program": "import hashlib\nprint(len(hashlib.sha256(b'')"
                                       ".hexdigest()))"}])
    degenerate = _lyp(True, prompt="a program the engine already runs",
                      out="2\n", negatives=[{"program": "print(2)"}])
    unsatisfiable = _lyp(True, prompt="a case nothing can pass",
                         out="never\n",
                         negatives=[{"program": "import hashlib\nprint(3)"}])
    unobserved = make_case(prompt="no engine is ever asked about this",
                           test={"kind": "stdout", "expect_stdout": "4\n"},
                           reference="print(4)", category="unobserved")
    _freeze(tmp_path, [good, degenerate, unsatisfiable, unobserved])

    pool = sampling_pool(tmp_path, engine)
    kept = {c["id"] for c in pool["cases"]}
    held = {e["id"] for e in (splitmod.load_lock(tmp_path) or {})["holdout"]}
    for case, why in ((degenerate, "dropped_degenerate"),
                      (unsatisfiable, "dropped_unsatisfiable"),
                      (unobserved, "dropped_population")):
        if case["id"] in held:
            continue                    # the freeze held this one out; not our subject
        assert case["id"] not in kept, "%s survived %s" % (case["id"], why)
    if good["id"] not in held:
        assert good["id"] in kept
    assert pool["census"]["unobserved"] == 0


def test_the_degenerate_case_really_does_pass_with_the_program_it_was_given(tmp_path):
    """The premise of the exclusion above, checked rather than asserted."""
    _engine()
    from pipeline.acceptance import run_test

    degenerate = _lyp(True, prompt="a program the engine already runs", out="1\n",
                      negatives=[{"program": "print(1)"}])
    assert run_test(degenerate["test"], "print(1)").passed, (
        "if this ever fails, the degenerate exclusion is measuring something else")


# --- the last test: does a target already pass a held-out case? ---------------


def test_a_target_that_solves_a_held_out_case_is_named():
    holdout = [make_case(prompt="print three",
                         test={"kind": "stdout", "expect_stdout": "3\n"},
                         reference="print(3)", category="wrong-output")]
    rows = [{"case_id": "t-solves", "program": "print(1 + 2)"},
            {"case_id": "t-clean", "program": "print(4)"}]
    report = splitmod.sft_solves_holdout(rows, holdout)
    assert report["holdout_cases_solved"] == [holdout[0]["id"]]
    assert [h["row_case_id"] for h in report["solved"]] == ["t-solves"]


def test_nothing_in_the_shipped_sft_set_solves_a_held_out_case():
    """The check that the similarity filter actually excludes.

    The old assertion in `sample.train_cases` filtered by held id and then asked
    for held ids — true however leaky the split was. This asks the question of
    the TARGETS: measured 2026-09-13, 16 of the 154 rows in `data/sft/v1` are
    verified solutions to 7 held-out cases, every train case behind them is
    dropped by `split.SIMILARITY_CEILING`, and the same draws re-folded over the
    clean pool solve none.
    """
    sft = DATA / "sft" / "v1" / "sft.jsonl"
    lock = DATA / "holdout.lock.json"
    if not sft.exists() or not lock.exists():
        pytest.skip("no sampled SFT set in this checkout")
    import pipeline.sample as samplemod

    held = {e["id"] for e in json.loads(lock.read_text())["holdout"]}
    corpus = read_jsonl(DATA / "corpus.jsonl")
    clean = {c["id"] for c in samplemod.train_cases(DATA)}
    rows = read_jsonl(sft)
    survivors = [r for r in rows if r["case_id"] in clean]
    report = splitmod.sft_solves_holdout(survivors, [c for c in corpus if c["id"] in held])
    assert report["solved"] == [], (
        "a target sampled from a case the leak filter KEPT passes a held-out "
        "case: %s" % report["holdout_cases_solved"])


def test_a_ceiling_case_is_checked_against_its_reference_not_a_negative():
    """`satisfiable` on a case that has no negative used to answer "no-negative".

    That reads like "checked, fine" and means "not checked", and it was the
    answer for every ceiling and every unobserved case — 45 of 249 on
    2026-09-13. A ceiling case carries its original program as the *reference*,
    because falling back is the right answer there, so that is what gets run.
    """
    from pipeline.refusals import satisfiable

    stable = _lyp(False, prompt="reproduce this", out="7\n")
    stable["reference"] = "print(7)"
    assert satisfiable(stable)["verdict"] == "ok"

    unreproducible = _lyp(False, prompt="reproduce this", out="8\n")
    unreproducible["reference"] = "print(7)"
    assert satisfiable(unreproducible)["verdict"] == "unreproducible"

    nothing = _lyp(False, prompt="nothing to run on", out="7\n")
    assert satisfiable(nothing)["verdict"] == "no-negative"
