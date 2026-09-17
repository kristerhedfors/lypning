from __future__ import annotations

from pipeline import probe_vector
from pipeline.jsonio import write_jsonl


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


def test_an_absent_input_is_a_usage_error_and_never_a_zeros_table(tmp_path, capsys):
    """Rung S0c reads two private artifacts. Absent, they must not read as zero.

    `read_jsonl` answers `[]` for a path that is not there, so before the guard
    both sides parsed empty, `probe_only` was empty, and the command printed a
    zeros table at exit 0 — indistinguishable, by exit code, from every probe
    case matching its base.
    """
    from pipeline import cli
    probe, base = tmp_path / "probe.jsonl", tmp_path / "base.jsonl"
    write_jsonl(probe, [{"case_id": "a", "status": "correct-native"}])
    write_jsonl(base, [{"case_id": "a", "status": "correct-fallback"}])
    missing = tmp_path / "missing.jsonl"

    for probe_arg, base_arg, named in ((missing, base, missing), (probe, missing, missing),
                                       (missing, missing, missing)):
        assert cli.main(["probe-vector", "--probe", str(probe_arg),
                         "--base", str(base_arg)]) == 2
        captured = capsys.readouterr()
        assert captured.err.strip() == "not a file: %s" % named
        assert captured.out == ""          # never the zeros table

    assert cli.main(["probe-vector", "--probe", str(probe), "--base", str(base)]) == 0
    assert "probe rows 1   base rows 1" in capsys.readouterr().out


def test_a_present_but_empty_probe_is_not_a_comparison_either(tmp_path, capsys):
    """Absent was one step; empty is the same read of nothing one step later.

    A probe stage that produced no rollouts did not run, and `probe_only` is
    empty over it, so the hole detector cannot catch this one either.
    """
    from pipeline import cli
    probe, base = tmp_path / "probe.jsonl", tmp_path / "base.jsonl"
    probe.write_text("", encoding="utf-8")
    write_jsonl(base, [{"case_id": "a", "status": "correct-native"}])
    assert cli.main(["probe-vector", "--probe", str(probe), "--base", str(base)]) == 1
    captured = capsys.readouterr()
    assert "no probe rows in" in captured.err
    assert captured.out == ""          # never the zeros table
