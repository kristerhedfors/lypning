"""A component never straddles banks; the cut is a function of bank and seed; leaks exit 1."""
from __future__ import annotations

import hashlib
import json

from pipeline import eval2_split as S
from pipeline.eval2_split import BANKS, FILES, components, split_bank
from pipeline.jsonio import read_jsonl, write_jsonl
from pipeline.training_data import split_cases

REVIEW = {"origin": "authored", "evidence_ids": [], "reviewer": "fixture", "intent_basis": "fixture",
          "oracle_basis": "fixture", "rights_basis": "fixture", "independence_basis": "fixture"}


def case(i, family, source_group, population="coverage", reference=None, task=None):
    """A schema-3 case that passes `training_data.validate_cases` on its own."""
    return {"case_id": "c%03d" % i, "family": family, "source_group": source_group,
            "capabilities": ["stdin"],
            # A long unique token keeps unrelated fixture tasks under the 0.85 task-similarity ceiling.
            "task": task or "Read one integer from stdin and print it plus %d; ticket %s."
            % (i, hashlib.sha256(str(i).encode()).hexdigest()[:40]),
            "reference": reference or "import sys\nprint(int(sys.stdin.read()) + %d)\n" % i,
            "provenance": "fixture", "population": population,
            "tests": [{"stdin": "%d\n" % k, "stdout": "%d\n" % (k + i)} for k in (1, 2, 3)],
            "review": dict(REVIEW)}


def bank(families=40, per_family=2, controls=8):
    """40 families of two cases each, the last 8 families fallback controls, plus three joins:

    c900 shares family f0 (a family join), c901 shares source_group s1 under
    its own family (a source join), c902 shares c004's reference under its own
    family and source (a solution join). Each join is one component.
    """
    cases = []
    i = 0
    for f in range(families):
        for _ in range(per_family):
            population = "fallback-control" if f >= families - controls else "coverage"
            cases.append(case(i, "f%d" % f, "s%d" % f, population))
            i += 1
    cases.append(case(900, "f0", "s900"))
    cases.append(case(901, "f901", "s1"))
    cases.append(dict(case(902, "f902", "s902"), reference=cases[4]["reference"]))
    return cases


def owner_of(result):
    return {c["case_id"]: b for b in BANKS for c in result["banks"][b]}


def test_components_match_split_cases_and_stay_in_one_bank():
    cases = bank()
    by_split_group = {}
    for row in split_cases(cases, 1111):
        by_split_group.setdefault(row["split_group"], set()).add(row["case_id"])
    ours = {frozenset(c["case_id"] for c in g) for g in components(cases)}
    assert ours == {frozenset(ids) for ids in by_split_group.values()}
    result = split_bank(cases, seed=1111, pilot_size=12, eval2_size=30)
    owner = owner_of(result)
    for ids in by_split_group.values():
        assert len({owner[i] for i in ids}) == 1, "a component straddles banks"
    assert owner["c900"] == owner["c000"] == owner["c001"]
    assert owner["c901"] == owner["c002"] == owner["c003"]
    assert owner["c902"] == owner["c004"]


def test_banks_are_disjoint_and_complete_and_records_unchanged():
    cases = bank()
    result = split_bank(cases, seed=7, pilot_size=12, eval2_size=30)
    ids = [c["case_id"] for b in BANKS for c in result["banks"][b]]
    assert sorted(ids) == sorted(c["case_id"] for c in cases) and len(ids) == len(set(ids))
    for b in BANKS:
        for row in result["banks"][b]:
            assert "split" not in row and "split_group" not in row
            assert row == next(c for c in cases if c["case_id"] == row["case_id"])
    summary = result["summary"]
    assert summary["banks"]["pilot"]["cases"] >= 12 and summary["banks"]["eval2"]["cases"] >= 30
    assert sum(summary["banks"][b]["cases"] for b in BANKS) == len(cases)


def test_every_bank_keeps_both_populations_near_the_bank_ratio():
    cases = bank()
    result = split_bank(cases, seed=1111, pilot_size=16, eval2_size=30)
    whole = result["summary"]["populations"]
    ratio = whole["fallback-control"] / (whole["coverage"] + whole["fallback-control"])
    for b in BANKS:
        pops = result["summary"]["banks"][b]["populations"]
        assert pops["coverage"] > 0 and pops["fallback-control"] > 0, b
        share = pops["fallback-control"] / (pops["coverage"] + pops["fallback-control"])
        assert abs(share - ratio) < 0.15, (b, share, ratio)


def test_the_cut_is_deterministic_in_the_seed():
    cases = bank()
    a = split_bank(cases, seed=1111, pilot_size=12, eval2_size=30)
    b = split_bank(list(reversed(cases)), seed=1111, pilot_size=12, eval2_size=30)
    assert owner_of(a) == owner_of(b)
    assert a["summary"]["banks"] == b["summary"]["banks"]
    c = split_bank(cases, seed=2222, pilot_size=12, eval2_size=30)
    assert owner_of(a) != owner_of(c)


def test_train_floor_is_met_and_reported():
    cases = bank()
    result = split_bank(cases, seed=1111, pilot_size=12, eval2_size=30)
    floor = result["summary"]["train_floor"]
    assert floor["met"] and floor["families"] >= 18 and floor["fallback_families"] >= 2
    # Taking almost everything leaves train short; the repair pass moves what helps and says so.
    tight = split_bank(cases, seed=1111, pilot_size=40, eval2_size=40)
    floor = tight["summary"]["train_floor"]
    assert floor["moves"], "a short train bank is repaired from the other banks"
    assert floor["met"] == (floor["families"] >= 18 and floor["fallback_families"] >= 2)
    rest = split_bank(cases, seed=1111, pilot_size=12, eval2_size="rest")
    assert rest["banks"]["train"] == [] and rest["summary"]["train_floor"]["met"] is False
    assert rest["summary"]["banks"]["eval2"]["cases"] + rest["summary"]["banks"]["pilot"]["cases"] == len(cases)


def test_main_writes_three_banks_and_a_leak_free_summary(tmp_path, capsys):
    path = tmp_path / "bank.jsonl"
    write_jsonl(path, bank())
    out = tmp_path / "split"
    assert S.main(["--bank", str(path), "--output", str(out), "--pilot-size", "12", "--eval2-size", "30"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["leak_free"] and all(v["pairs"] == 0 for v in summary["leaks"].values())
    assert {p.name for p in out.iterdir()} == set(FILES.values()) | {"split.json"}
    written = {b: read_jsonl(out / FILES[b]) for b in BANKS}
    assert sum(len(v) for v in written.values()) == len(bank())
    assert json.loads((out / "split.json").read_text())["banks"] == summary["banks"]
    assert S.main(["--bank", str(path), "--output", str(out)]) == 1, "never overwrite a cut"


def test_a_leak_the_components_cannot_see_exits_one(tmp_path, capsys):
    cases = bank()
    owner = owner_of(split_bank(cases, seed=1111, pilot_size=12, eval2_size=30))
    e = next(c for c in cases if owner[c["case_id"]] == "eval2")
    t = next(c for c in cases if owner[c["case_id"]] == "train")
    # Task text is not a component key, so the same cut now carries a reworded twin.
    t["task"] = e["task"].replace("print it", "output it")
    path = tmp_path / "bank.jsonl"
    write_jsonl(path, cases)
    assert S.main(["--bank", str(path), "--output", str(tmp_path / "split"),
                   "--pilot-size", "12", "--eval2-size", "30"]) == 1
    summary = json.loads(capsys.readouterr().out)
    assert not summary["leak_free"]
    assert summary["leaks"]["eval2-vs-train"]["by_rule"]["task"] >= 1


def test_sizes_that_exceed_the_bank_are_refused(tmp_path):
    path = tmp_path / "bank.jsonl"
    write_jsonl(path, bank())
    assert S.main(["--bank", str(path), "--output", str(tmp_path / "s"), "--pilot-size", "80"]) == 1
    assert S.main(["--bank", str(path), "--output", str(tmp_path / "s"), "--eval2-size", "some"]) == 1
    assert not (tmp_path / "s").exists()
