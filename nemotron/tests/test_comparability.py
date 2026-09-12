"""The guards that stop two different measurements being subtracted.

Every test here pins a refusal: a number withheld, named and loud rather than
printed. Two families of failure are covered, and they are not the same family.
The first is the silent subtraction the guards exist to remove. The second is
the guards' own first draft, which withheld the right numbers and then said
something untrue about why — an unknown reported as a difference, a harness
flake reported as a truncated run, "not comparable to the baseline" printed
where there was no baseline, and a WIN line three lines below its own warnings.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from pipeline import stats

SAMPLING = {"enable_thinking": True, "max_tokens": 12288, "samples": 1,
            "temperature": 1.0, "top_p": 0.95, "seed": 1234}
MANIFEST = "80b2fc52" * 8
ENDPOINT = "https://ev8eognh8prrpgs7.us-east-1.aws.endpoints.huggingface.cloud/v1"


def _summary(run_id, pass_rate=0.35, sampling=None, evaluated=74, planned=74,
             lo=0.24, harness_errors=0, model="nemotron", base_url=ENDPOINT):
    return {
        "run_id": run_id, "label": "", "model": model, "pass_rate": pass_rate,
        "ci95": {"lo": lo, "hi": 0.46, "method": "bootstrap-percentile",
                 "resamples": 10000, "seed": 1},
        "ci95_wilson": {"lo": 0.24, "hi": 0.46},
        "prompt_sha": "cbb7be44937a6b41",
        "holdout_manifest_sha256": MANIFEST,
        "sampling": dict(SAMPLING) if sampling is None else sampling,
        "cases_evaluated": evaluated, "cases_planned": planned,
        "harness_errors": harness_errors,
        "n_attempts": evaluated, "spend_usd": 0.1,
        "backend": {"base_url": base_url, "model": model},
    }


# --------------------------------------------------------------------- stats


def test_a_token_budget_difference_is_named_not_subtracted():
    """Same weights, same prompt, same split, 4096 vs 12288 tokens: not a delta."""
    why = stats.comparability(_summary("t12k"),
                              _summary("t4k", sampling=dict(SAMPLING, max_tokens=4096)))
    assert [d["field"] for d in why] == ["sampling.max_tokens"]
    assert why[0]["baseline"] == 12288 and why[0]["run"] == 4096
    assert why[0]["established"] is True


def test_every_sampling_knob_that_moves_the_number_is_guarded():
    for key, other in (("enable_thinking", False), ("samples", 16),
                       ("temperature", 0.2), ("top_p", 0.5), ("max_tokens", 2048)):
        run = _summary("b", sampling=dict(SAMPLING, **{key: other}))
        assert [d["field"] for d in stats.comparability(_summary("a"), run)] \
            == ["sampling." + key]


def test_a_different_seed_is_noise_the_interval_prices_not_a_difference():
    assert stats.comparability(_summary("a"),
                               _summary("b", sampling=dict(SAMPLING, seed=7))) == []


def test_matching_runs_are_comparable():
    assert stats.comparability(_summary("a"), _summary("b")) == []


def test_an_unrecorded_sampling_block_is_an_unknown_and_says_which_side():
    """REGRESSION (reviewer 1A): one unrecorded side used to be reported as two."""
    one = stats.comparability(_summary("base"), _summary("replay", sampling={"replayed": True}))
    assert [d["field"] for d in one] == ["sampling"]
    assert one[0]["established"] is False
    assert one[0]["baseline"] == stats.RECORDED and one[0]["run"] == stats.UNRECORDED

    both = stats.comparability(_summary("r1", sampling={"replayed": True}),
                               _summary("r2", sampling={"replayed": True}))
    assert both[0]["baseline"] == both[0]["run"] == stats.UNRECORDED
    assert both[0]["established"] is False


def test_a_run_is_comparable_with_itself_whatever_it_recorded():
    """REGRESSION (reviewer 1A): a promoted graded run was n/c against itself."""
    r = _summary("replay-base", sampling={"replayed": True})
    assert stats.comparability(r, dict(r)) == []


def test_two_endpoints_under_one_model_name_cannot_be_told_apart():
    """The audit's arm-identity finding: both shipped runs recorded model 'nemotron'."""
    why = stats.comparability(_summary("a"), _summary("b", base_url="https://other/v1"))
    assert [d["field"] for d in why] == ["backend.base_url"]
    assert why[0]["same_model_name"] == "nemotron"


def test_two_endpoints_under_two_model_names_are_the_arm_under_test():
    assert stats.comparability(
        _summary("base"),
        _summary("tuned", model="nemotron-lora-v1", base_url="https://other/v1")) == []


def test_an_unrecorded_endpoint_is_not_reported_as_a_difference():
    run = _summary("b")
    run["backend"] = {}
    assert stats.comparability(_summary("a"), run) == []


def test_a_partial_run_is_reported_incomplete_and_blocks():
    why = stats.completeness(_summary("short", evaluated=61, planned=74))
    assert [d["field"] for d in why] == ["cases_evaluated"]
    assert why[0]["actual"] == 61 and why[0]["expected"] == 74 and why[0]["blocks"]


def test_a_case_lost_to_a_harness_error_is_named_but_does_not_block():
    """REGRESSION (reviewer 1B): one sandbox flake used to brick a whole GPU run.

    summarize_run drops harness_error attempts from the denominator by design —
    server or sandbox, not the model — so with samples=1 a single flake takes a
    case out of cases_evaluated. That shortfall carries none of the upward bias
    a spend-cap abort does, and must not be reported as a truncated run.
    """
    why = stats.completeness(_summary("flake", evaluated=73, planned=74, harness_errors=1))
    assert [d["field"] for d in why] == ["harness_errors"]
    assert why[0]["blocks"] is False
    assert stats.blocking(why) == []


def test_a_shortfall_bigger_than_the_harness_errors_still_blocks():
    why = stats.completeness(_summary("short", evaluated=40, planned=74, harness_errors=1))
    assert [d["field"] for d in why] == ["cases_evaluated"]
    assert stats.blocking(why)


def test_an_aborted_run_blocks_even_with_every_case_evaluated():
    why = stats.completeness(_summary("capped"),
                             {"state": "aborted", "aborted": "spend cap: $9 > $5"})
    assert [d["field"] for d in why] == ["state", "aborted"]
    assert len(stats.blocking(why)) == 2


def test_a_finished_run_is_complete():
    assert stats.completeness(_summary("ok"), {"state": "done", "aborted": None}) == []
    assert stats.completeness(_summary("ok")) == []


def test_a_paired_regression_is_significant_and_named_as_one():
    """The direction this pipeline exists to catch used to read as noise."""
    before = {"c%d" % i: 1.0 for i in range(30)}
    after = dict(before)
    for i in range(8):
        after["c%d" % i] = 0.0
    d = stats.paired_delta(before, after)
    assert d["delta"] < 0 and d["ci95"]["hi"] < 0.0
    assert d["significant"] and d["direction"] == "regression"


def test_an_improvement_is_still_named_an_improvement():
    before = {"c%d" % i: 0.0 for i in range(30)}
    after = dict(before)
    for i in range(8):
        after["c%d" % i] = 1.0
    d = stats.paired_delta(before, after)
    assert d["significant"] and d["direction"] == "improvement"


def test_a_wash_is_still_not_separable_from_noise():
    before = {"c%d" % i: (1.0 if i % 2 else 0.0) for i in range(40)}
    after = dict(before)
    after["c0"], after["c1"] = 1.0, 0.0
    d = stats.paired_delta(before, after)
    assert not d["significant"] and d["direction"] == "none"


def test_no_shared_cases_still_answers_both_verdict_questions():
    d = stats.paired_delta({"a": 1.0}, {"b": 1.0})
    assert d["n_pairs"] == 0 and d["significant"] is False and d["direction"] == "none"
    assert d["dropped_before_only"] == 1 and d["dropped_after_only"] == 1


def test_the_cases_the_intersection_drops_are_counted():
    """A 20-pair comparison used to read like a 74-pair one but for the count."""
    before = {"c%02d" % i: 1.0 for i in range(74)}
    after = {"c%02d" % i: 1.0 for i in range(20)}
    d = stats.paired_delta(before, after)
    assert d["n_pairs"] == 20
    assert d["dropped_before_only"] == 54 and d["dropped_after_only"] == 0


def test_pass_at_1_is_case_weighted_like_every_other_pass_rate():
    r = stats.pass_at_k([(1, 1), (1, 16)])
    assert abs(r["pass_at_1"] - 0.53125) < 1e-9        # mean of 1.0 and 1/16
    assert abs(r["pass_at_1_draw_weighted"] - 2 / 17) < 1e-9
    assert abs(r["headroom"] - (1.0 - 0.53125)) < 1e-9
    assert r["k"] == 16 and r["k_min"] == 1 and r["ragged"] is True


def test_uniform_draws_leave_the_published_numbers_alone():
    r = stats.pass_at_k([(0, 16), (1, 16), (16, 16), (0, 16)])
    assert r["pass_at_1"] == r["pass_at_1_draw_weighted"] == 17 / 64
    assert r["ragged"] is False


# ----------------------------------------------------------------------- cli


@pytest.fixture()
def nt(tmp_path, monkeypatch):
    """A pipeline rooted in a temp tree, so no recorded run is touched."""
    (tmp_path / "data").mkdir()
    (tmp_path / "runs").mkdir()
    monkeypatch.setenv("NTX_ROOT", str(tmp_path))
    from pipeline import cli
    importlib.reload(cli)
    yield cli
    monkeypatch.delenv("NTX_ROOT", raising=False)
    importlib.reload(cli)


def _put_run(root: Path, summary, progress=None, per_case=None, meta=True):
    d = root / "runs" / summary["run_id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "summary.json").write_text(json.dumps(summary))
    if meta:
        (d / "meta.json").write_text(json.dumps({"backend": summary["backend"]}))
    if progress is not None:
        (d / "progress.json").write_text(json.dumps(progress))
    if per_case is not None:
        lines = [json.dumps({"case_id": c, "sample": 0, "passed": bool(v)})
                 for c, v in sorted(per_case.items())]
        (d / "attempts.jsonl").write_text("\n".join(lines) + "\n")
    return d


class _Args(object):
    ids = False
    anyway = False


def test_results_withholds_the_delta_when_only_the_token_budget_differs(nt, tmp_path, capsys):
    base = _summary("t12k")
    (tmp_path / "data" / "baseline.json").write_text(json.dumps(base))
    _put_run(tmp_path, base)
    _put_run(tmp_path, _summary("t4k", pass_rate=0.284,
                                sampling=dict(SAMPLING, max_tokens=4096)))
    assert nt.cmd_results(_Args()) == 0
    out = capsys.readouterr().out
    assert "-6.8pp" not in out
    assert "different sampling.max_tokens (baseline 12288, run 4096)" in out
    assert "n/c t4k" in out


def test_results_never_calls_an_unrecorded_sampling_block_a_difference(nt, tmp_path, capsys):
    """REGRESSION (reviewer 1A): the false sentence the guard existed to remove."""
    base = _summary("replay-base", sampling={"replayed": True})
    (tmp_path / "data" / "baseline.json").write_text(json.dumps(base))
    _put_run(tmp_path, base)
    _put_run(tmp_path, _summary("later"))
    assert nt.cmd_results(_Args()) == 0
    out = capsys.readouterr().out
    assert "different sampling" not in out
    assert "sampling not recorded by baseline (baseline unrecorded, run recorded)" in out
    # and the baseline is not declared not-comparable to itself
    assert "n/c replay-base" not in out


def test_results_names_the_side_when_both_are_unrecorded(nt, tmp_path, capsys):
    base = _summary("r1", sampling={"replayed": True})
    (tmp_path / "data" / "baseline.json").write_text(json.dumps(base))
    _put_run(tmp_path, base)
    _put_run(tmp_path, _summary("r2", sampling={"replayed": True}))
    assert nt.cmd_results(_Args()) == 0
    out = capsys.readouterr().out
    assert "sampling recorded by neither (baseline unrecorded, run unrecorded)" in out
    assert "different sampling" not in out


def test_results_does_not_mention_a_baseline_that_does_not_exist(nt, tmp_path, capsys):
    """REGRESSION (reviewer 1C): an incomplete run in an empty tree blamed a baseline."""
    _put_run(tmp_path, _summary("capped", evaluated=40),
             progress={"state": "aborted", "aborted": "spend cap: $9 > $5"})
    assert nt.cmd_results(_Args()) == 0
    out = capsys.readouterr().out
    assert "not comparable to the baseline" not in out
    assert "partial run: 40 of 74 cases evaluated" in out


def test_results_withholds_the_delta_from_a_partial_run(nt, tmp_path, capsys):
    base = _summary("full")
    (tmp_path / "data" / "baseline.json").write_text(json.dumps(base))
    _put_run(tmp_path, base)
    _put_run(tmp_path, _summary("capped", pass_rate=0.60, lo=0.50, evaluated=40),
             progress={"state": "aborted", "aborted": "spend cap: $9 > $5"})
    assert nt.cmd_results(_Args()) == 0
    out = capsys.readouterr().out
    assert "+25.0pp" not in out
    assert "partial run: 40 of 74 cases evaluated" in out and "spend cap" in out


def test_results_still_ranks_a_run_that_lost_a_case_to_a_harness_error(nt, tmp_path, capsys):
    """REGRESSION (reviewer 1B): the flake must be reported, not disqualifying."""
    base = _summary("full")
    (tmp_path / "data" / "baseline.json").write_text(json.dumps(base))
    _put_run(tmp_path, base, progress={"state": "done", "aborted": None})
    _put_run(tmp_path, _summary("flake", pass_rate=0.40, evaluated=73, harness_errors=1),
             progress={"state": "done", "aborted": None})
    assert nt.cmd_results(_Args()) == 0
    out = capsys.readouterr().out
    assert "+5.0pp" in out
    assert "n/c flake" not in out
    assert "note flake:" in out and "harness errors" in out


def test_results_refuses_every_delta_when_the_baseline_itself_is_partial(nt, tmp_path, capsys):
    base = _summary("partial-base", evaluated=40)
    (tmp_path / "data" / "baseline.json").write_text(json.dumps(base))
    _put_run(tmp_path, base)
    _put_run(tmp_path, _summary("later"))
    assert nt.cmd_results(_Args()) == 0
    out = capsys.readouterr().out
    assert "UNUSABLE" in out and "partial run: 40 of 74 cases evaluated" in out
    # not "+0.0pp" against a baseline that measured 40 of the 74 cases
    assert "pp" not in out
    assert "n/c later" not in out


def test_results_prints_the_arm_behind_every_number(nt, tmp_path, capsys):
    _put_run(tmp_path, _summary("a"))
    _put_run(tmp_path, _summary("b", base_url="https://other/v1"))
    assert nt.cmd_results(_Args()) == 0
    out = capsys.readouterr().out
    assert "nemotron @ %s" % ENDPOINT in out
    assert "nemotron @ https://other/v1" in out


def test_results_withholds_the_delta_between_two_endpoints_of_one_name(nt, tmp_path, capsys):
    base = _summary("base")
    (tmp_path / "data" / "baseline.json").write_text(json.dumps(base))
    _put_run(tmp_path, base)
    _put_run(tmp_path, _summary("other-box", pass_rate=0.5, base_url="https://other/v1"))
    assert nt.cmd_results(_Args()) == 0
    out = capsys.readouterr().out
    assert "+15.0pp" not in out
    assert "different backend.base_url under one model name nemotron" in out


def test_results_abbreviates_a_manifest_sha_instead_of_wrapping_the_line(nt, tmp_path, capsys):
    base = _summary("base")
    (tmp_path / "data" / "baseline.json").write_text(json.dumps(base))
    _put_run(tmp_path, base)
    other = _summary("othersplit")
    other["holdout_manifest_sha256"] = "aa11bb22" * 8
    _put_run(tmp_path, other)
    assert nt.cmd_results(_Args()) == 0
    out = capsys.readouterr().out
    assert "different holdout_manifest_sha256 (baseline 80b2fc5280b2…, run aa11bb22aa11…)" in out


def test_promote_refuses_a_run_that_never_finished_the_split(nt, tmp_path, capsys):
    _put_run(tmp_path, _summary("capped", pass_rate=0.60, evaluated=40),
             progress={"state": "aborted", "aborted": "spend cap: $9 > $5"})

    class A(_Args):
        run_id = "capped"

    assert nt.cmd_promote(A()) == 1
    err = capsys.readouterr().err
    assert "refusing to promote capped" in err and "40 of 74" in err
    assert not (tmp_path / "data" / "baseline.json").exists()


def test_promote_refuses_a_run_that_never_recorded_its_sampling(nt, tmp_path, capsys):
    _put_run(tmp_path, _summary("replay", sampling={"replayed": True}),
             progress={"state": "done", "aborted": None})

    class A(_Args):
        run_id = "replay"

    assert nt.cmd_promote(A()) == 1
    assert "sampling not recorded" in capsys.readouterr().err
    assert not (tmp_path / "data" / "baseline.json").exists()


def test_promote_accepts_a_run_that_lost_a_case_to_a_harness_error(nt, tmp_path):
    """REGRESSION (reviewer 1B): one flake must not permanently bar a GPU run."""
    _put_run(tmp_path, _summary("flake", evaluated=73, harness_errors=1),
             progress={"state": "done", "aborted": None})

    class A(_Args):
        run_id = "flake"

    assert nt.cmd_promote(A()) == 0
    assert json.loads((tmp_path / "data" / "baseline.json").read_text())["run_id"] == "flake"


def test_promote_still_accepts_a_finished_run(nt, tmp_path):
    _put_run(tmp_path, _summary("good"), progress={"state": "done", "aborted": None})

    class A(_Args):
        run_id = "good"

    assert nt.cmd_promote(A()) == 0
    assert json.loads((tmp_path / "data" / "baseline.json").read_text())["run_id"] == "good"


def test_eval_baseline_flag_is_not_a_way_around_the_promote_check(nt, tmp_path, capsys,
                                                                 monkeypatch):
    """REGRESSION (reviewer 2.2): `nt eval --baseline` wrote whatever it produced."""
    summary = _summary("capped", evaluated=40)
    _put_run(tmp_path, summary, progress={"state": "aborted", "aborted": "spend cap"})

    class _Ev(object):
        def __init__(self, *a, **kw):
            pass

        def run(self, manifest, progress=None):
            return summary

    monkeypatch.setattr(nt, "load_holdout", lambda data: [{"id": "c1"}])
    monkeypatch.setattr(nt, "_backend", lambda args: object())
    monkeypatch.setattr(nt, "Evaluation", _Ev)
    monkeypatch.setattr(nt, "render_summary", lambda s, elapsed=None: "")

    class A(_Args):
        baseline = True
        samples = 1; temperature = 1.0; top_p = 0.95; max_tokens = 4096
        no_thinking = False; concurrency = 1; max_spend = 0.0; price_hour = 0.0
        label = ""

    assert nt._eval_foreground(A(), "capped") == 1
    err = capsys.readouterr().err
    assert "NOT written as the baseline" in err and "40 of 74" in err
    assert not (tmp_path / "data" / "baseline.json").exists()


def test_compare_calls_a_regression_a_regression(nt, tmp_path, capsys):
    before = {"c%02d" % i: 1.0 for i in range(30)}
    after = dict(before)
    for i in range(8):
        after["c%02d" % i] = 0.0
    _put_run(tmp_path, _summary("before"), per_case=before)
    _put_run(tmp_path, _summary("after"), per_case=after)

    class A(_Args):
        before, after = "before", "after"

    assert nt.cmd_compare(A()) == 0
    out = capsys.readouterr().out
    assert "SIGNIFICANT REGRESSION" in out
    assert "not separable from noise" not in out


def test_compare_refuses_two_runs_that_measured_different_things(nt, tmp_path, capsys):
    per_case = {"c%02d" % i: float(i % 2) for i in range(30)}
    _put_run(tmp_path, _summary("before"), per_case=per_case)
    _put_run(tmp_path, _summary("after", sampling=dict(SAMPLING, max_tokens=4096)),
             per_case=per_case)

    class A(_Args):
        before, after = "before", "after"

    assert nt.cmd_compare(A()) == 1
    cap = capsys.readouterr()
    assert "refusing to compare" in cap.err
    assert "different sampling.max_tokens (before 12288, after 4096)" in cap.err
    assert "paired delta" not in cap.out


def test_compare_anyway_prints_arithmetic_but_never_a_model_verdict(nt, tmp_path, capsys):
    before = {"c%02d" % i: 1.0 for i in range(30)}
    after = dict(before)
    for i in range(8):
        after["c%02d" % i] = 0.0
    _put_run(tmp_path, _summary("before"), per_case=before)
    _put_run(tmp_path, _summary("after", sampling=dict(SAMPLING, max_tokens=4096)),
             per_case=after)

    class A(_Args):
        before, after, anyway = "before", "after", True

    assert nt.cmd_compare(A()) == 0
    out = capsys.readouterr().out
    assert "WARNING" in out and "different sampling.max_tokens" in out
    assert "paired delta" in out
    assert "SIGNIFICANT REGRESSION" not in out
    assert "separable from noise, but between runs that differ as above" in out


def test_compare_never_declares_an_incomparable_run_a_win(nt, tmp_path, capsys):
    """REGRESSION (reviewer 2.1): the WIN line sat unguarded below the warnings.

    `nt results` and `nt compare` ran off the same files and printed opposite
    verdicts — and `nt compare` is the command the GPU README hands the operator.
    """
    per_case = {"c%02d" % i: float(i % 2) for i in range(30)}
    base = _summary("base", pass_rate=0.20)
    (tmp_path / "data" / "baseline.json").write_text(json.dumps(base))
    _put_run(tmp_path, base)
    _put_run(tmp_path, _summary("before"), per_case=per_case)
    _put_run(tmp_path, _summary("cand", pass_rate=0.60, lo=0.50, evaluated=30,
                                sampling=dict(SAMPLING, max_tokens=4096)),
             per_case=per_case)

    class A(_Args):
        before, after, anyway = "before", "cand", True

    assert nt.cmd_compare(A()) == 0
    out = capsys.readouterr().out
    assert "WIN" not in out
    assert "unpaired rule: n/c against the baseline" in out
    assert "different sampling.max_tokens (baseline 12288, after 4096)" in out
    assert "partial run: 30 of 74 cases evaluated" in out


def test_compare_says_how_many_cases_the_intersection_dropped(nt, tmp_path, capsys):
    before = {"c%02d" % i: float(i % 2) for i in range(30)}
    after = {"c%02d" % i: float(i % 2) for i in range(20)}
    _put_run(tmp_path, _summary("before"), per_case=before)
    _put_run(tmp_path, _summary("after"), per_case=after)

    class A(_Args):
        before, after = "before", "after"

    assert nt.cmd_compare(A()) == 0
    out = capsys.readouterr().out
    assert "over 20 cases" in out
    assert "10 case(s) dropped: 10 only in before, 0 only in after" in out


def test_slices_interval_does_not_depend_on_the_order_attempts_landed(nt, tmp_path, capsys):
    """Two evals with byte-identical per-case results must publish one interval."""
    d = _put_run(tmp_path, _summary("r1"))
    ids = ["ntx-%02d" % i for i in range(24)]
    # Ragged per-case means, which is what makes the resampled order visible.
    rows = [{"case_id": cid, "sample": s, "passed": s < (i % 7),
             "category_prior": "rewrite"}
            for i, cid in enumerate(ids) for s in range(6)]
    (tmp_path / "data" / "corpus.jsonl").write_text("\n".join(
        json.dumps({"id": cid, "category": "rewrite"}) for cid in ids) + "\n")
    (d / "attempts.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    class A(_Args):
        run_id, fine = "r1", False

    assert nt.cmd_slices(A()) == 0
    first = capsys.readouterr().out
    (d / "attempts.jsonl").write_text("\n".join(
        json.dumps(r) for r in reversed(rows)) + "\n")
    assert nt.cmd_slices(A()) == 0
    assert capsys.readouterr().out == first


def test_compare_still_declares_a_clean_win(nt, tmp_path, capsys):
    per_case = {"c%02d" % i: float(i % 2) for i in range(30)}
    base = _summary("base", pass_rate=0.20)
    (tmp_path / "data" / "baseline.json").write_text(json.dumps(base))
    _put_run(tmp_path, base)
    _put_run(tmp_path, _summary("before"), per_case=per_case)
    _put_run(tmp_path, _summary("cand", pass_rate=0.60, lo=0.50), per_case=per_case)

    class A(_Args):
        before, after = "before", "cand"

    assert nt.cmd_compare(A()) == 0
    assert "-> WIN" in capsys.readouterr().out


# --------------------------------------------------------------------- grade


def test_grade_takes_the_sampling_from_a_completions_header(nt):
    header, comps = nt._split_header([{"sampling": dict(SAMPLING)},
                                      {"case_id": "x", "text": ""}])
    samp, why = nt._declared_sampling(header, comps, "")
    assert why == "" and samp["max_tokens"] == 12288
    assert samp["declared_by"] == "completions header"


def test_grade_takes_the_sampling_from_every_record_when_they_agree(nt):
    comps = [{"case_id": "x", "sampling": dict(SAMPLING)},
             {"case_id": "y", "sampling": dict(SAMPLING)}]
    samp, why = nt._declared_sampling({}, comps, "")
    assert why == "" and samp["declared_by"] == "completions records"
    comps[1]["sampling"] = dict(SAMPLING, max_tokens=4096)
    samp, why = nt._declared_sampling({}, comps, "")
    assert samp is None and "more than one sampling config" in why


def test_grade_takes_an_operator_declaration_and_records_it_as_one(nt, tmp_path):
    samp, why = nt._declared_sampling({}, [{"case_id": "x"}], json.dumps(SAMPLING))
    assert why == "" and samp["declared_by"] == "operator"
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"sampling": SAMPLING}))
    samp, why = nt._declared_sampling({}, [{"case_id": "x"}], str(p))
    assert why == "" and samp["temperature"] == 1.0


def test_grade_refuses_a_declaration_that_is_missing_a_knob(nt):
    partial = dict(SAMPLING)
    del partial["max_tokens"]
    samp, why = nt._declared_sampling({}, [{"case_id": "x"}], json.dumps(partial))
    assert samp is None and "missing max_tokens" in why


def test_grade_refuses_a_completions_file_that_states_nothing(nt, tmp_path, capsys):
    """REGRESSION (reviewer 1B / 2.4): the graded run is the headline measurement.

    It used to record {"replayed": true}, which is not a sampling config, and
    round 1 warned and graded anyway — producing a run whose delta is withheld
    forever with nothing in the repo able to fix it. Grading is free to re-run,
    so the refusal costs one command and names the three ways to comply.
    """
    comps = tmp_path / "completions.jsonl"
    comps.write_text(json.dumps({"case_id": "x", "text": "print(1)"}) + "\n")

    class A(_Args):
        run_id = "cand"; completions = str(comps); label = ""
        model = "nemotron-lora-v1"; sampling = ""; endpoint = ""

    assert nt.cmd_grade(A()) == 1
    err = capsys.readouterr().err
    assert "refusing to grade" in err
    assert "does not state how it was sampled" in err
    assert "--sampling" in err and "header line" in err
    assert not (tmp_path / "runs" / "cand").exists()


def test_grade_refuses_completions_that_cannot_name_their_arm(nt, tmp_path, capsys):
    comps = tmp_path / "completions.jsonl"
    comps.write_text("\n".join([json.dumps({"sampling": dict(SAMPLING)}),
                                json.dumps({"case_id": "x", "text": "print(1)"})]) + "\n")

    class A(_Args):
        run_id = "cand"; completions = str(comps); label = ""
        model = ""; sampling = ""; endpoint = ""

    assert nt.cmd_grade(A()) == 1
    assert "--model is required" in capsys.readouterr().err


def test_a_graded_run_that_declares_itself_is_comparable(nt, tmp_path):
    """The whole point: a declared grade can be subtracted from the baseline."""
    graded = _summary("cand", sampling=dict(SAMPLING, declared_by="operator", replayed=True),
                      model="nemotron-lora-v1", base_url="")
    graded["backend"] = {"model": "nemotron-lora-v1", "source": "replay:/tmp/c.jsonl"}
    assert stats.comparability(_summary("base"), graded) == []
    assert nt._promotion_bars(graded) == []
