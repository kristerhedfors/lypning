from __future__ import annotations

from pipeline import probe_vector


def test_probe_vector_keeps_every_status_and_unmatched_id():
    probe = [{"case_id": "a", "status": "correct-native"},
             {"case_id": "a", "status": "correct-fallback"},
             {"case_id": "probe-only", "status": "incorrect"}]
    base = [{"case_id": "a", "status": "correct-fallback"},
            {"case_id": "base-only", "status": "correct-native"}]
    result = probe_vector.compare(probe, base)
    assert result["probe_rows"] == 3 and result["base_rows"] == 2
    assert result["matched_cases"] == 1
    assert result["probe_only"] == ["probe-only"] and result["base_only"] == ["base-only"]
    row = next(row for row in result["cases"] if row["case_id"] == "a")
    assert row["probe_native_rate"] == .5 and row["base_native_rate"] == 0
    text = probe_vector.render(result)
    assert "correct-native=1" in text and "probe-only IDs: probe-only" in text
