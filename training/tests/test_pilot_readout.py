"""The pilot readout prints named aggregates only (public log)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location("pilot_readout", ROOT / ".github/scripts/pilot_readout.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def metrics(native):
    return {"correct": 0.9, "correct_native": native, "cases": 306, "draws": 4896,
            "case_clusters": {"v3-secretcase": [1, 2]},
            "by_capability": {"base64": {"correct": 0.8, "statuses": {"x": 1}}},
            "by_population": {"coverage": {"correct": 0.9, "correct_native": native,
                                           "case_clusters": {"v3-secretcase": [1]}}}}


def test_readout_keeps_named_aggregates_and_drops_everything_else():
    m = load()
    best = dict(metrics(0.8), step=700, rule={"metric": "by_population.coverage.correct_native",
                                              "selection_z": 1.2816, "secret": "v3-secretcase"},
                observed=[{"step": 0, "correct_native": 0.7, "selected": True, "rejected_for": [],
                           "case_id": "v3-secretcase"},
                          {"step": 700, "correct_native": 0.8, "selected": True, "rejected_for": []}])
    out = json.dumps(m.readout(best, metrics(0.7)))
    assert "secretcase" not in out and "by_capability" not in out and "case_clusters" not in out
    got = json.loads(out)
    assert got["selected_step"] == 700
    assert got["selected"]["by_population"]["coverage"]["correct_native"] == 0.8
    assert got["base_dev"]["correct_native"] == 0.7
    assert [o["step"] for o in got["observed"]] == [0, 700]


def test_a_bad_job_id_fails_without_payload(monkeypatch, capsys):
    m = load()
    monkeypatch.setenv("PILOT_JOB", "../../etc")
    assert m.main() == 1
    err = capsys.readouterr().err
    assert "no private payload" in err
