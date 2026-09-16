"""The eval-2 power curve resamples families, not cases, and is a number only
because it is the same number twice (`EVAL2.md` section 7)."""
from __future__ import annotations

import random
import re

import pytest

from pipeline import cli, stats
from pipeline.jsonio import write_jsonl


def _pilot(seed=1, cases=48, k=8):
    """A synthetic pilot: families of one to three cases, rates from the floor up."""
    rng = random.Random(seed)
    out, fam, i = {}, 0, 0
    while i < cases:
        p = rng.choice([0.0, 0.0, 0.0, 0.1, 0.25, 0.5, 0.75, 0.9])
        for _ in range(rng.choice([1, 1, 2, 3])):
            if i >= cases:
                break
            out["c%03d" % i] = {"family": "f%02d" % fam,
                                "scores": [int(rng.random() < p) for _ in range(k)]}
            i += 1
        fam += 1
    return out


def _curve(**kw):
    args = dict(trials=12, resamples=120, sizes=(40, 160), deltas=(0.0, 0.05, 0.10))
    args.update(kw)
    return stats.power_curve_clustered(_pilot(), 8, **args)


def test_pilot_from_rows_scores_correct_and_native_in_draw_order():
    rows = [
        {"case_id": "a", "draw": 1, "family": "fa", "split_group": "g1", "correct": True, "native": False},
        {"case_id": "a", "draw": 0, "family": "fa", "split_group": "g1", "correct": True, "native": True},
        {"case_id": "b", "draw": 0, "family": "fb", "correct": False, "native": True},
    ]
    pilot = stats.pilot_from_rows(rows)
    assert pilot["a"] == {"family": "fa", "split_group": "g1", "scores": [1, 0]}
    # No split_group: the family is its own cluster, the default both homes use.
    assert pilot["b"] == {"family": "fb", "split_group": "fb", "scores": [0]}


def test_the_curve_is_deterministic_and_each_cell_stands_alone():
    a = _curve()
    b = _curve()
    assert a == b, "the cluster power curve is not deterministic"
    # A cell seeds itself from (seed, shape, delta, N): adding a size to the
    # grid moves no other row.
    c = _curve(sizes=(40, 80, 160))
    keep = [r for r in c["rows"] if r["N"] != 80]
    assert keep == a["rows"]
    assert set(r["shape"] for r in a["rows"]) == set(stats.CLUSTER_SHAPES)
    assert a["pilot"]["cases"] == 48 and a["k"] == 8 and a["mde"] == 0.03


def test_the_null_rarely_fires():
    a = _curve(trials=20)
    for shape, cells in a["false_positive"].items():
        assert set(cells) == {40, 160}
        for n, rate in cells.items():
            assert rate <= 0.1, "null fired %.2f of the time for %s at N=%d" % (rate, shape, n)
    nulls = [r for r in a["rows"] if r["delta"] == 0.0]
    assert all(r["mean_effect"] == 0.0 for r in nulls)


def test_power_rises_with_bank_size():
    a = _curve(trials=16, deltas=(0.10,))
    for shape in stats.CLUSTER_SHAPES:
        small, large = [r["power"] for r in a["rows"] if r["shape"] == shape]
        assert large >= small
        lo_small, lo_large = [r["mean_lo"] for r in a["rows"] if r["shape"] == shape]
        assert lo_large > lo_small, "the lower bound did not tighten with N for %s" % shape
    assert a["smallest_n"]["uniform"][0.10] in (40, 160)
    assert a["smallest_n"]["concentrated"][0.10] in (40, 160, None)


def test_the_two_shapes_differ_at_the_same_mean_effect():
    """Same mean lift, different power: the case-resampling curve cannot show
    this, and it is the shape a rejection-sampling adapter actually produces."""
    a = _curve(trials=16, deltas=(0.10,), sizes=(80,))
    uni, con = [r for r in a["rows"] if r["shape"] == "uniform"][0], \
               [r for r in a["rows"] if r["shape"] == "concentrated"][0]
    assert abs(uni["mean_effect"] - 0.10) < 0.01 and abs(con["mean_effect"] - 0.10) < 1e-9
    assert uni["power"] != con["power"]
    assert uni["power"] > con["power"], "concentrating the lift on the floor widened nothing"


def test_the_curve_refuses_bad_input():
    with pytest.raises(ValueError):
        stats.power_curve_clustered({}, 8)
    with pytest.raises(ValueError):
        stats.power_curve_clustered(_pilot(), 8, shapes=("triangular",))
    with pytest.raises(ValueError):
        stats.power_curve_clustered({"x": {"family": "f", "scores": []}}, 8)


def _rows(pilot):
    return [{"case_id": cid, "draw": d, "family": e["family"], "split_group": e["family"],
             "population": "coverage", "capabilities": [], "correct": bool(s), "native": bool(s)}
            for cid, e in pilot.items() for d, s in enumerate(e["scores"])]


def test_nt_power_eval2_renders_the_table(tmp_path, capsys):
    rows = tmp_path / "rows.jsonl"
    write_jsonl(rows, _rows(_pilot(cases=24, k=4)))
    rc = cli.main(["power", "--eval2", "--rows", str(rows), "--mde", "0.03",
                   "--sizes", "30,60", "--deltas", "0,0.1", "--trials", "4", "--resamples", "100"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "pilot 24 cases" in out and "k=4" in out
    assert "N=30" in out and "N=60" in out
    assert "<- false-positive rate" in out
    assert re.search(r"concentrated\s+\+10pp\s+\d+%\s+\d+%", out)
    assert re.search(r"uniform\s+\+10pp\s+\d+%\s+\d+%", out)
    assert "80%% power" not in out
    assert "smallest N at 80% power" in out


def test_nt_power_eval2_reads_a_run_id_and_says_what_is_missing(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "RUNS", tmp_path)
    assert cli.main(["power", "--eval2"]) == 2
    assert cli.main(["power", "--eval2", "--rows", "r1"]) == 1
    err = capsys.readouterr().err
    assert "eval2-rows r1" in err
    (tmp_path / "r1").mkdir()
    write_jsonl(tmp_path / "r1" / "eval2_rows.jsonl", _rows(_pilot(cases=12, k=2)))
    rc = cli.main(["power", "--eval2", "--rows", "r1", "--sizes", "20", "--deltas", "0.1",
                   "--trials", "2", "--resamples", "100"])
    out = capsys.readouterr().out
    assert rc == 0 and "pilot 12 cases" in out and "k=2" in out
    # Without --eval2 the legacy curve still wants its run id.
    assert cli.main(["power"]) == 2
