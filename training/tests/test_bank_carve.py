"""The pilot/benchmark carve: disjoint by family, admissible at every seed.

WHY THIS FILE EXISTS. bank v2's two banks were separated by hand, so nothing
could re-make the decision and nothing checked it. The properties that matter
are not visible by reading the code: that no family reaches both banks, that
the pilot admits at all three protocol seeds rather than the one a caller
passed, and that a bank which cannot be carved is refused with the arithmetic
rather than written and discovered on a metered job.
"""

from __future__ import annotations

import pytest

from pipeline import bank_carve
from pipeline.training_contract import PROTOCOL_TRAIN_SEEDS
from pipeline.training_data import split_cases, validate_benchmark, validate_pilot
from pipeline.training_types import TrainingError


def _tag(family):
    """A per-family integer, so the AST of one family's reference is its own."""
    return 1000 + sum(ord(ch) for ch in family)


def case(family, population, n):
    """A schema-3 case whose tests discriminate, so `validate_cases` admits it."""
    return {
        "case_id": "%s-%02d" % (family, n),
        "family": family, "source_group": family, "capabilities": [family.split("-")[0]],
        "task": "Task %s %d: read an integer from stdin and print a derived value." % (family, n),
        # Structurally unique per family. `split_cases` unions by
        # source/family/SOLUTION and `solution_fingerprint` hashes the AST dump,
        # which discards comments -- so a per-family comment changes nothing and
        # every family sharing an `n` would merge into one component.
        "reference": "import sys\nprint(int(sys.stdin.read() or 0) + %d + %d)" % (n, _tag(family)),
        "population": population,
        "tests": [{"stdin": "1", "stdout": "%d\n" % (1 + n + _tag(family))},
                  {"stdin": "2", "stdout": "%d\n" % (2 + n + _tag(family))},
                  {"stdin": "3", "stdout": "%d\n" % (3 + n + _tag(family))}],
        "provenance": "authored for test_bank_carve",
        "review": {"origin": "authored", "evidence_ids": [], "reviewer": "test",
                   "intent_basis": "t", "oracle_basis": "t", "rights_basis": "t",
                   "independence_basis": "one family per construct"},
    }


def bank(coverage_families=40, control_families=10, per_family=6):
    cases = []
    for i in range(coverage_families):
        cases += [case("cov-%02d" % i, "coverage", n) for n in range(per_family)]
    for i in range(control_families):
        cases += [case("ctl-%02d" % i, "fallback-control", n) for n in range(per_family)]
    return cases


def test_no_family_reaches_both_banks():
    """The property the whole carve exists for: a family is near-twin tasks."""
    result = bank_carve.carve(bank())
    assert result["problems"] == []
    pilot = {c["family"] for c in result["pilot"]}
    benchmark = {c["family"] for c in result["benchmark"]}
    assert pilot & benchmark == set()
    assert pilot | benchmark == {c["family"] for c in bank()}


def test_every_case_lands_in_exactly_one_bank():
    result = bank_carve.carve(bank())
    ids = [c["case_id"] for c in result["pilot"]] + [c["case_id"] for c in result["benchmark"]]
    assert sorted(ids) == sorted(c["case_id"] for c in bank())
    assert len(ids) == len(set(ids))


def test_the_pilot_admits_at_every_protocol_seed_not_just_the_carve_seed():
    """A bank admissible at 1111 and not 2222 fails a three-seed round halfway."""
    result = bank_carve.carve(bank(), seed=1111)
    assert result["problems"] == []
    for protocol_seed in PROTOCOL_TRAIN_SEEDS:
        validate_pilot(split_cases(result["pilot"], seed=protocol_seed))
    validate_benchmark(split_cases(result["benchmark"], seed=PROTOCOL_TRAIN_SEEDS[0]))


def test_both_banks_get_both_populations():
    result = bank_carve.carve(bank())
    for name in ("pilot", "benchmark"):
        assert {c["population"] for c in result[name]} == {"coverage", "fallback-control"}, name


def test_too_few_control_families_is_refused_with_the_arithmetic():
    """Bank v3 before 2026-09-19: ten control families, and both banks have a floor."""
    with pytest.raises(TrainingError) as exc:
        bank_carve.carve(bank(coverage_families=40, control_families=7))
    message = str(exc.value)
    assert "control families cannot make two banks" in message
    assert str(bank_carve.MIN_PILOT_CONTROL_FAMILIES) in message
    assert "ceiling-stratum" in message, "the refusal must say what to generate"


def test_a_bank_with_one_population_is_refused_rather_than_carved_into_a_useless_pair():
    with pytest.raises(TrainingError) as exc:
        bank_carve.carve(bank(control_families=0))
    assert "needs both populations" in str(exc.value)


def test_a_family_carrying_two_populations_is_allocated_whole_and_counted_twice():
    """bank v3 has five. A ceiling construct the model writes natively is judged
    `native` by `synth.judge`, which labels by what the engine did rather than
    by the pool the construct came from, so its family carries both. It is still
    one construct with near-twin tasks, so it moves as a unit."""
    cases = bank()
    cases.append(case("cov-00", "fallback-control", 99))
    result = bank_carve.carve(cases)
    assert result["problems"] == []
    assert result["plan"]["counts"]["mixed"] == 1
    side = "pilot" if "cov-00" in result["plan"]["pilot"] else "benchmark"
    assert {c["population"] for c in result[side] if c["family"] == "cov-00"} == {
        "coverage", "fallback-control"}, "both of its populations go with it"
    other = "benchmark" if side == "pilot" else "pilot"
    assert not any(c["family"] == "cov-00" for c in result[other])


def test_a_bank_too_small_for_two_banks_reports_problems_and_is_not_written():
    """Eighteen families each is `validate_bank`'s floor; twenty cannot make two."""
    result = bank_carve.carve(bank(coverage_families=12, control_families=8))
    assert result["problems"], "a bank this small cannot make two admissible banks"
    assert "NOT ADMISSIBLE" in bank_carve.render(result)


def test_the_seed_moves_which_families_the_benchmark_takes():
    a = bank_carve.carve(bank(), seed=1111)
    b = bank_carve.carve(bank(), seed=2222)
    assert a["problems"] == [] and b["problems"] == []
    assert set(a["plan"]["benchmark"]) != set(b["plan"]["benchmark"])
    # ... and is reproducible for a given seed.
    assert bank_carve.carve(bank(), seed=1111)["plan"] == a["plan"]


def test_benchmark_families_is_honoured_and_still_leaves_the_pilot_admissible():
    result = bank_carve.carve(bank(), benchmark_families=24)
    assert result["problems"] == []
    assert len(result["plan"]["benchmark"]) == 24


def test_the_shipped_bank_v2_union_carves_back_to_its_own_shape():
    """The hand split is the oracle: 51 pilot / 18 benchmark families."""
    import json
    import os
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "bank_v2")
    if not os.path.isdir(root):
        pytest.skip("no bank_v2 in this tree")
    union = []
    for name in ("train.jsonl", "eval2.jsonl"):
        with open(os.path.join(root, name), encoding="utf-8") as fh:
            union += [json.loads(line) for line in fh if line.strip()]
    result = bank_carve.carve(union, seed=1111)
    assert result["problems"] == []
    assert len(result["plan"]["pilot"]) == 51
    assert len(result["plan"]["benchmark"]) == 18
