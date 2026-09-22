"""The provider-seed read prints counts of agreement and nothing a run contained."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "provider_seed_compare.py"
SPEC = importlib.util.spec_from_file_location("provider_seed_compare", SCRIPT)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
PRIVATE = "PRIVATE_COMPLETION_OR_CASE_"


def row(case, draw, arm, text, tokens=10, finish="stop"):
    return {"case_id": PRIVATE + case, "draw": draw, "arm": arm, "seed": 1111 + draw,
            "completion": PRIVATE + text, "finish_reason": finish,
            "usage": {"completion_tokens": tokens, "prompt_tokens": 99}}


def test_agreement_is_counted_over_shared_keys_per_arm():
    left = m.keyed([row("a", 0, "bare", "x"), row("a", 0, "subset-spec", "y"),
                    row("b", 0, "bare", "z"), row("only-left", 0, "bare", "q")])
    right = m.keyed([row("a", 0, "bare", "x"), row("a", 0, "subset-spec", "other", tokens=11),
                     row("b", 0, "bare", "z", finish="length"), row("only-right", 1, "bare", "q")])
    counts = m.compare(left, right)
    assert counts["all"] == {"overlapping_keys": 3, "identical_completion": 2,
                             "identical_completion_tokens": 2, "identical_finish_reason": 2,
                             "identical_seed": 3}
    assert counts["bare"]["overlapping_keys"] == 2 and counts["bare"]["identical_completion"] == 2
    assert counts["subset-spec"] == {"overlapping_keys": 1, "identical_completion": 0,
                                     "identical_completion_tokens": 0, "identical_finish_reason": 1,
                                     "identical_seed": 1}


def test_a_run_is_named_by_its_actions_id_and_resolved_to_one_directory():
    files = ["positive-control/smoke-%s-35751938025/paid/completions.jsonl" % ("a" * 40),
             "positive-control/targets-%s-35767396604/paid/completions.jsonl" % ("b" * 40),
             "positive-control/targets-%s-35767396604/grade/rows.jsonl" % ("b" * 40)]
    dirs = m.resolve(files, ["35751938025", "35767396604"])
    assert dirs["35767396604"] == "targets-%s-35767396604" % ("b" * 40)
    with pytest.raises(m.CompareError, match="35763603648"):
        m.resolve(files, ["35751938025", "35763603648"])
    assert m.parse_runs("") == list(m.DEFAULT_RUNS)
    for bad in ("1", "35751938025", "35751938025 ../x", "35751938025 35751938025"):
        with pytest.raises(m.CompareError):
            m.parse_runs(bad)


def fake_hub(monkeypatch, tmp_path, payloads):
    names = {}
    for run, rows in payloads.items():
        path = tmp_path / (run + ".jsonl")
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        names["positive-control/r-%s/paid/completions.jsonl" % run] = path
    fake = SimpleNamespace(
        HfApi=lambda token: SimpleNamespace(
            whoami=lambda: {"name": "owner"},
            repo_info=lambda *a, **k: SimpleNamespace(private=True, sha="c" * 40),
            list_repo_files=lambda *a, **k: list(names)),
        hf_hub_download=lambda repo, name, **kw: str(names[name]))
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake)
    monkeypatch.setenv("HF_TOKEN", "token")
    monkeypatch.setenv("PROVIDER_SEED_RUNS", " ".join(payloads))


def test_the_log_holds_counts_and_never_a_completion_or_case(monkeypatch, tmp_path, capsys):
    rows = [row("a", d, arm, "%d%s" % (d, arm)) for d in range(2) for arm in ("bare", "subset-spec")]
    fake_hub(monkeypatch, tmp_path, {"111111": rows, "222222": rows[:3]})
    assert m.main() == 0
    out = capsys.readouterr()
    assert PRIVATE not in out.out + out.err
    result = json.loads(out.out)
    assert result["runs"] == {"111111": {"keys": 4}, "222222": {"keys": 3}}
    assert result["pairs"][0]["counts"]["all"]["identical_completion"] == 3


def test_a_malformed_private_file_fails_without_its_content(monkeypatch, tmp_path, capsys):
    fake_hub(monkeypatch, tmp_path, {"111111": [], "222222": []})
    (tmp_path / "222222.jsonl").write_text(PRIVATE + " not json\n")
    assert m.main() == 1
    err = capsys.readouterr().err
    assert "download 222222" in err and "JSONDecodeError" in err
    assert PRIVATE not in err
