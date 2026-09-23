"""Step 3 pair supply: counted exactly, per source and arm mode, and never a case in the log."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".github" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("step3_pairs", SCRIPTS / "step3_pairs.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

PRIVATE = "PRIVATE_CASE_PROGRAM_DETAIL"
RUN = "full-merged-" + "0" * 40 + "-1"


def refusal(kind):
    return [0, "lypning-l: unsupported: %s: %s" % (kind, PRIVATE)]


def draw(case_id, family, arm, i, status, population="coverage", kinds=()):
    correct = status.startswith("correct-")
    native = status == "correct-native" or (status == "correct-control" and not kinds)
    return {"case_id": case_id, "family": family, "arm": arm, "draw": i, "seed": 1111 + i,
            "split_group": family, "population": population, "capabilities": [PRIVATE],
            "correct": correct, "native": native, "status": status,
            "completion_tokens": 10, "truncated": False,
            "score": {"reward": 1.0, "status": status, "native_tests": 1, "total_tests": 1,
                      "refusals": [refusal(k) for k in kinds], "failed_test": None}}


N, F, I = "correct-native", "correct-fallback", "incorrect"


def case_rows(case_id, family, arms, population="coverage"):
    """``arms`` maps arm -> list of (status, kinds) per draw."""
    return [draw(PRIVATE + case_id, PRIVATE + family, arm, i, status, population, kinds)
            for arm, statuses in arms.items() for i, (status, kinds) in enumerate(statuses)]


def fixture():
    """Four coverage prompts over three families and two control prompts, k = 2.

    c0: a pair inside each arm; c1: pair only across arms, positive from
    subset-spec (context distillation); c2: pair only in bare; c3: no positive.
    """
    rows = []
    rows += case_rows("c0", "fa", {"bare": [(N, ()), (F, ("module",))],
                                   "subset-spec": [(N, ()), (F, ("fstring", "module"))]})
    rows += case_rows("c1", "fa", {"bare": [(F, ("class",)), (I, ())],
                                   "subset-spec": [(N, ()), (N, ())]})
    rows += case_rows("c2", "fb", {"bare": [(N, ()), (F, ("zz-not-an-engine-kind",))],
                                   "subset-spec": [(I, ()), (I, ())]})
    rows += case_rows("c3", "fc", {"bare": [(F, ("module",)), (F, ("module",))],
                                   "subset-spec": [(I, ()), (F, ("module",))]})
    control = "fallback-control"
    rows += case_rows("k0", "fk", {"bare": [("correct-control", ()), ("correct-control", ("module",))],
                                   "subset-spec": [("correct-control", ()), (I, ())]}, control)
    rows += case_rows("k1", "fk", {"bare": [("correct-control", ("module",)), (I, ())],
                                   "subset-spec": [(I, ()), (I, ())]}, control)
    return rows


def probe_rows(rows, statuses):
    """Probe draws for the fixture's cases: ``statuses`` maps case suffix -> list."""
    out = []
    by_case = {r["case_id"]: r for r in rows}
    for suffix, draws in statuses.items():
        base = by_case[PRIVATE + suffix]
        for i, (status, kinds) in enumerate(draws):
            row = draw(base["case_id"], base["family"], None, i, status, base["population"], kinds)
            del row["arm"], row["score"]
            row.update(step=0, refusals=[refusal(k) for k in kinds])
            out.append(row)
    return out


def test_counts_per_arm_mode():
    result = m.summarise(fixture())
    assert result["cases"] == 6 and result["samples_per_arm"] == 2
    assert result["families"] == {"coverage": 3, "fallback-control": 1, "all": 4}
    cov = result["coverage"]
    assert {k: cov[k]["pair_prompts"] for k in cov} == {
        "bare": 2, "subset-spec": 1, "same_arm": 2, "any_arm": 3, "context_distillation": 2}
    # c0 in same-arm: 1x1 per arm = 2 pairs; c2: 1.
    assert cov["same_arm"]["pairs_at_most_per_prompt"] == {"1": 2, "2": 3, "4": 3, "all": 3}
    # any-arm: c0 2 positives x 2 negatives = 4; c1 2x1 = 2; c2 1x1 = 1.
    assert cov["any_arm"]["pairs_at_most_per_prompt"] == {"1": 3, "2": 5, "4": 7, "all": 7}
    assert cov["any_arm"]["disjoint_pairs_at_most_per_prompt"] == {"1": 3, "2": 4, "4": 4,
                                                                   "all": 4}
    assert cov["any_arm"]["pair_prompts_split"] == {"with_a_bare_positive": 2,
                                                    "only_context_distillation": 1}
    assert cov["any_arm"]["families_with_pair_prompt"] == 2
    assert cov["subset-spec"]["families_with_pair_prompt"] == 1
    assert all(stats["coverage_prompts"] == 4 for stats in cov.values())
    assert not any(stats["clears_pair_prompt_threshold"] for stats in cov.values())
    assert result["modes_clearing_threshold"] == []
    assert result["probe"] == {"included": False, "reason": "not read"}


def test_negative_refusal_kinds_count_only_paired_negatives():
    cov = m.summarise(fixture())["coverage"]
    # c3's module negatives have no positive anywhere: never counted.
    assert cov["any_arm"]["negatives"] == {
        "draws": 4, "without_refusal": 0,
        "draws_by_kind": {"class": 1, "fstring": 1, "module": 2, "other-kind": 1},
        "pair_prompts_by_kind": {"class": 1, "fstring": 1, "module": 1, "other-kind": 1}}
    assert cov["bare"]["negatives"]["draws_by_kind"] == {"module": 1, "other-kind": 1}


def test_controls_are_counted_apart_and_never_paired():
    result = m.summarise(fixture())
    assert result["controls"]["bare"] == {
        "control_prompts": 2, "prompts_with_native_ran_draw": 1,
        "prompts_with_fallback_draw": 2, "prompts_with_both": 1,
        "draws": {"correct_fallback": 2, "correct_native_ran": 1, "not_correct": 1}}
    assert result["controls"]["any_arm"]["prompts_with_native_ran_draw"] == 1
    assert "any_source" not in result["controls"]
    assert result["controls"]["subset-spec"]["prompts_with_fallback_draw"] == 0
    assert result["statuses"]["bare"]["fallback-control"] == {"correct-control": 3,
                                                              "incorrect": 1}


def test_threshold_is_the_plan_s_300_prompts():
    rows = []
    for i in range(300):
        rows += case_rows("p%d" % i, "f%d" % (i % 31), {"bare": [(N, ()), (F, ("module",))],
                                                        "subset-spec": [(I, ()), (I, ())]})
    result = m.summarise(rows)
    assert result["coverage"]["bare"]["clears_pair_prompt_threshold"]
    assert result["coverage"]["bare"]["families_with_pair_prompt"] == 31
    assert "subset-spec" not in result["modes_clearing_threshold"]
    assert set(result["modes_clearing_threshold"]) == {"bare", "same_arm", "any_arm"}


def test_probe_joins_as_its_own_source():
    rows = fixture()
    probe = probe_rows(rows, {"c3": [(N, ()), (F, ("module",))], "c2": [(I, ()), (I, ())],
                              "c1": [(F, ("class",)), (I, ())], "c0": [(I, ()), (I, ())],
                              "k0": [("correct-control", ("module",)), (I, ())],
                              "k1": [(I, ()), (I, ())]})
    result = m.summarise(rows, probe)
    assert result["probe"]["included"] and result["probe"]["identical_case_sets"]
    assert result["probe"]["samples_per_case"] == 2
    cov = result["coverage"]
    assert cov["probe"]["pair_prompts"] == 1
    # c3 gains a positive from the probe; c1 still has no unconditioned positive.
    assert cov["unconditioned"]["pair_prompts"] == 3
    assert cov["any_source"]["pair_prompts"] == 4
    # Step 2 modes are unchanged by the probe.
    assert cov["any_arm"]["pair_prompts"] == 3
    assert result["controls"]["probe"]["prompts_with_fallback_draw"] == 1
    # any_arm keeps its Step 2 meaning when the probe joins; any_source adds it.
    assert result["controls"]["any_arm"] == m.summarise(rows)["controls"]["any_arm"]
    assert result["controls"]["any_source"]["prompts_with_fallback_draw"] == 2
    assert result["probe"]["same_engine_as_run"] is None
    assert result["statuses"]["probe"]["coverage"] == {N: 1, F: 2, I: 5}


def test_probe_on_another_split_is_reported_and_left_out():
    rows = fixture()
    probe = probe_rows(rows, {"c0": [(N, ()), (F, ("module",))]})
    for row in probe:
        row["case_id"] = PRIVATE + "elsewhere"
    result = m.summarise(rows, probe)
    assert result["probe"] == {"probe_cases": 1, "run_cases": 6, "shared_cases": 0,
                               "probe_only_cases": 1, "run_only_cases": 6,
                               "identical_case_sets": False,
                               "shared_cases_disagreeing_on_family_or_population": 0,
                               "same_engine_as_run": None,
                               "included": False, "samples_per_case": 2}
    assert "probe" not in result["coverage"] and "probe" not in result["controls"]


def test_probe_whose_shared_ids_name_other_cases_is_left_out():
    rows = fixture()
    probe = probe_rows(rows, {"c0": [(N, ()), (F, ("module",))]})
    for row in probe:
        row["family"] = PRIVATE + "another-family"
    join = m.summarise(rows, probe)["probe"]
    assert join["shared_cases"] == 1
    assert join["shared_cases_disagreeing_on_family_or_population"] == 1
    assert not join["included"]


@pytest.mark.parametrize("fault", ["missing_draw", "duplicate", "arm", "status", "native",
                                  "refusal", "population", "family", "empty"])
def test_malformed_rows_fail_closed(fault):
    rows = fixture()
    if fault == "missing_draw":
        rows.pop()
    elif fault == "duplicate":
        rows.append(dict(rows[0]))
    elif fault == "arm":
        rows[0]["arm"] = "other"
    elif fault == "status":
        rows[0]["status"] = "maybe"
    elif fault == "native":
        rows[1]["native"] = True
    elif fault == "refusal":
        rows[1]["score"]["refusals"] = [[0, PRIVATE]]
    elif fault == "population":
        rows[0]["population"] = "fallback-control"
    elif fault == "family":
        rows[1]["family"] = "other"
    else:
        rows = []
    with pytest.raises(m.EvidenceError) as caught:
        m.summarise(rows)
    assert PRIVATE not in str(caught.value)


def fake_hub(monkeypatch, tmp_path, files):
    local = {}
    for name, rows in files.items():
        path = tmp_path / name.replace("/", "__")
        text = (json.dumps(rows) if isinstance(rows, dict)
                else "".join(json.dumps(r) + "\n" for r in rows))
        path.write_text(text, encoding="utf-8")
        local[name] = str(path)
    api = SimpleNamespace(whoami=lambda: {"name": "owner"},
                          list_repo_files=lambda *a, **k: sorted(files),
                          repo_info=lambda *a, **k: SimpleNamespace(private=True, sha="a" * 40))
    fake = SimpleNamespace(HfApi=lambda **kw: api,
                           hf_hub_download=lambda repo, name, **kw: local[name])
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake)
    monkeypatch.setenv("HF_TOKEN", "token")
    monkeypatch.setenv("STEP3_RUN_ID", RUN)


def test_main_prints_aggregates_and_no_case(monkeypatch, tmp_path, capsys):
    rows = fixture()
    probe = probe_rows(rows, {s: [(N, ()), (F, ("module",))]
                              for s in ("c0", "c1", "c2", "c3")}
                       | {s: [(I, ()), (I, ())] for s in ("k0", "k1")})
    fake_hub(monkeypatch, tmp_path, {"positive-control/%s/grade/rows.jsonl" % RUN: rows,
                                     m.PROBE_PATH: probe})
    assert m.main() == 0, capsys.readouterr().err
    captured = capsys.readouterr()
    assert PRIVATE not in captured.out + captured.err
    printed = json.loads(captured.out)
    assert printed["run"] == RUN and printed["probe"]["included"]
    assert set(printed["sha256"]) == {"rows", "probe"}
    assert printed["coverage"]["any_source"]["pair_prompts"] == 4
    assert printed["probe"]["same_engine_as_run"] is None


@pytest.mark.parametrize("probe_engine,expected", [("e" * 64, True), ("f" * 64, False),
                                                   (PRIVATE, None)])
def test_main_reports_whether_the_probe_shares_the_run_engine(monkeypatch, tmp_path, capsys,
                                                              probe_engine, expected):
    rows = fixture()
    probe = probe_rows(rows, {s: [(N, ()), (F, ("module",))] for s in ("c0", "c1")})
    fake_hub(monkeypatch, tmp_path, {
        "positive-control/%s/grade/rows.jsonl" % RUN: rows, m.PROBE_PATH: probe,
        m.RUN_ENGINE_PATH % RUN: {"lineage": {"engine_sha256": "e" * 64}, "private": PRIVATE},
        m.PROBE_ENGINE_PATH: {"identity": {"sha256": probe_engine},
                              "cases": [{"case_id": PRIVATE}]}})
    assert m.main() == 0, capsys.readouterr().err
    captured = capsys.readouterr()
    assert PRIVATE not in captured.out + captured.err
    printed = json.loads(captured.out)
    assert printed["probe"]["same_engine_as_run"] is expected
    assert set(printed["sha256"]) == {"rows", "probe", "run_engine", "probe_engine"}


def test_engine_identity_is_not_read_without_the_probe(monkeypatch, tmp_path, capsys):
    fake_hub(monkeypatch, tmp_path, {
        "positive-control/%s/grade/rows.jsonl" % RUN: fixture(),
        m.RUN_ENGINE_PATH % RUN: {"lineage": {"engine_sha256": "e" * 64}}})
    assert m.main() == 0
    printed = json.loads(capsys.readouterr().out)
    assert set(printed["sha256"]) == {"rows"} and printed["probe"]["reason"] == "absent"


@pytest.mark.parametrize("kind", ["dict_keys-method", "_Environ-method", "TextIOWrapper-method",
                                  "NoneType-method", PRIVATE + "Point-method"])
def test_dynamic_engine_kinds_fold_to_other_kind_and_never_print(monkeypatch, tmp_path,
                                                                 capsys, kind):
    """The engine writes `<type>-method` kinds; the grader accepted them, so must this."""
    rows = fixture()
    for row in rows:
        if row["case_id"] == PRIVATE + "c2" and row["status"] == F:
            row["score"]["refusals"] = [[0, "lypning-l: unsupported: %s: %s.x()" % (kind, PRIVATE)]]
    fake_hub(monkeypatch, tmp_path, {"positive-control/%s/grade/rows.jsonl" % RUN: rows})
    assert m.main() == 0, capsys.readouterr().err
    captured = capsys.readouterr()
    assert kind not in captured.out + captured.err and PRIVATE not in captured.out
    printed = json.loads(captured.out)
    assert printed["coverage"]["bare"]["negatives"]["draws_by_kind"] == {"module": 1,
                                                                         "other-kind": 1}


def test_a_refusal_whose_detail_was_stripped_away_still_parses():
    # The verifier stores `stderr.strip()`: a whitespace-only detail leaves `kind:`.
    rows = fixture()
    for row in rows:
        if row["case_id"] == PRIVATE + "c2" and row["status"] == F:
            row["score"]["refusals"] = [[0, "lypning-l: unsupported: module:"]]
    negatives = m.summarise(rows)["coverage"]["bare"]["negatives"]
    assert negatives["draws_by_kind"] == {"module": 2}


def test_main_without_probe_and_on_an_absent_run(monkeypatch, tmp_path, capsys):
    fake_hub(monkeypatch, tmp_path, {"positive-control/%s/grade/rows.jsonl" % RUN: fixture()})
    monkeypatch.setenv("STEP3_INCLUDE_PROBE", "false")
    assert m.main() == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["probe"] == {"included": False, "reason": "not requested"}
    monkeypatch.setenv("STEP3_INCLUDE_PROBE", "true")
    assert m.main() == 0
    assert json.loads(capsys.readouterr().out)["probe"]["reason"] == "absent"
    monkeypatch.setenv("STEP3_RUN_ID", "another-run")
    assert m.main() == 1
    assert "no graded rows" in capsys.readouterr().err


def test_main_fails_closed_without_printing_the_row(monkeypatch, tmp_path, capsys):
    rows = fixture()
    rows[0]["status"] = PRIVATE
    fake_hub(monkeypatch, tmp_path, {"positive-control/%s/grade/rows.jsonl" % RUN: rows})
    assert m.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert PRIVATE not in captured.err and "validation and counting" in captured.err


@pytest.mark.parametrize("run", ["", "../x", "a/b", "-x"])
def test_main_refuses_a_run_id_that_is_not_one_path_segment(monkeypatch, tmp_path, capsys, run):
    fake_hub(monkeypatch, tmp_path, {})
    monkeypatch.setenv("STEP3_RUN_ID", run)
    assert m.main() == 1
    assert "during arguments" in capsys.readouterr().err
