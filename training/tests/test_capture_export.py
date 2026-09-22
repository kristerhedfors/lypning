"""The capture-tier export: exact bytes, exact joins, read-only log, its own artifact."""
from __future__ import annotations

import hashlib
import json
import shlex

import pytest

from pipeline import capture_export as cx
from pipeline import eval2_select as e2s
from pipeline.jsonio import read_jsonl
from pipeline.sandbox import RunResult

GOOD = '''def collatz(n):
    steps = 0
    while n != 1:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        steps += 1
    return steps


print(max(range(1, 30), key=collatz))'''

OTHER = GOOD.replace("range(1, 30)", "range(1, 50)")
THIRD = GOOD.replace("range(1, 30)", "range(1, 70)")
FOURTH = GOOD.replace("range(1, 30)", "range(1, 90)")


def heredoc(program, prefix=""):
    return "%spython3 - <<'PY'\n%s\nPY" % (prefix, program)


def _assistant(model, blocks):
    return {"type": "assistant", "timestamp": "2026-09-22T10:00:00Z",
            "message": {"model": model, "content": blocks}}


def _bash(block_id, command):
    return {"type": "tool_use", "id": block_id, "name": "Bash", "input": {"command": command}}


def _result(block_id, is_error):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": block_id, "is_error": is_error, "content": "x"}]}}


@pytest.fixture()
def world(tmp_path):
    """A log, a transcript with one subagent, and a journal. Returns their paths."""
    proj = tmp_path / "projects"
    proj.mkdir()
    main = proj / "sess-a.jsonl"
    sub = proj / "sess-a" / "subagents" / "workflows" / "wf_1"
    sub.mkdir(parents=True)
    main.write_text("\n".join(json.dumps(e) for e in [
        _assistant("claude-opus-5-5", [_bash("tu1", heredoc(GOOD))]),
        _result("tu1", False),
        _assistant("claude-opus-5", [_bash("tu3", heredoc(THIRD))]),
        _result("tu3", True),
    ]) + "\n")
    (sub / "agent-x.jsonl").write_text(json.dumps(
        _assistant("claude-fable-5-1", [_bash("tu-old", heredoc(OTHER))])) + "\n")
    journal = tmp_path / "attribution.jsonl"
    journal.write_text(json.dumps({"tool_use_id": "tu2", "model": "claude-opus-5-5",
                                   "is_error": False, "interrupted": True}) + "\n")
    records = [
        {"kind": "bash_command", "session": "sess-a", "host": "claude", "tool_use_id": "tu1",
         "transcript": str(main), "command": heredoc(GOOD), "ts": "2026-09-22T10:00:00Z"},
        None,  # a blank line still counts in evidence's numbering
        {"kind": "bash_command", "session": "sess-a", "host": "claude", "tool_use_id": "tu2",
         "transcript": str(main), "command": heredoc(GOOD), "ts": "2026-09-22T10:01:00Z"},
        # Written before #26: no tool_use_id, joined by the byte-identical command.
        {"kind": "bash_command", "session": "sess-a", "transcript": str(main),
         "command": heredoc(OTHER), "ts": "2026-09-05T10:00:00Z"},
        {"kind": "bash_command", "session": "sess-a", "host": "claude", "tool_use_id": "tu3",
         "transcript": str(main), "command": heredoc(THIRD), "ts": "2026-09-22T10:02:00Z"},
        {"kind": "bash_command", "session": "sess-b", "host": "claude", "tool_use_id": "tu9",
         "command": heredoc(FOURTH, "cd training/data/eval2 && "), "ts": "2026-09-22T11:00:00Z"},
        {"kind": "bash_command", "session": "sess-b", "host": "claude", "tool_use_id": "tu10",
         "command": "python3 -c 'print(1)'", "ts": "2026-09-22T11:01:00Z"},
        {"kind": "embedding_invocation", "program": "print(2)"},
    ]
    log = tmp_path / "invocations.jsonl"
    lines = [("" if r is None else json.dumps(r)) + "\n" for r in records]
    lines.append("{not json\n")
    log.write_text("".join(lines))
    return {"log": log, "journal": journal, "tmp": tmp_path}


def _export(world, **kw):
    kw.setdefault("attribution", world["journal"])
    return cx.export(world["log"], origin="host-1/gen-1", local=frozenset(), keep="all", **kw)


def test_the_log_and_the_journal_are_read_and_never_written(world):
    before = (world["log"].read_bytes(), world["journal"].read_bytes())
    _export(world)
    assert (world["log"].read_bytes(), world["journal"].read_bytes()) == before


def test_parent_event_id_is_the_id_an_evidence_snapshot_gives_that_line(world):
    from lypning import evidence
    rows = _export(world)["rows"]
    evidence.snapshot(world["log"], world["tmp"] / "ev", "host-1/gen-1")
    ids = {e["line"]: e["event_id"] for e in read_jsonl(world["tmp"] / "ev" / "events.jsonl")}
    assert rows and all(r["parent_event_id"] == ids[r["parent_line"]] for r in rows)
    assert [r["parent_line"] for r in rows] == [1, 3, 4, 5, 6, 7]  # the blank line counted


def test_program_bytes_are_exact_and_sha_links_through_an_export_snapshot(world, tmp_path):
    from lypning import evidence
    rows = [r for r in _export(world)["rows"] if r["program"] is not None]
    assert rows[0]["program"] == GOOD
    for r in rows:
        assert r["source_sha256"] == hashlib.sha256(r["program"].encode("utf-8")).hexdigest()
    path = tmp_path / "rows.jsonl"
    cx.write_rows(path, rows)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    cx.snapshot(path, tmp_path / "ev-export", "host-1/gen-1")
    events = read_jsonl(tmp_path / "ev-export" / "events.jsonl")
    assert [e["source_sha256"] for e in events] == [r["source_sha256"] for r in rows]


def test_attribution_is_journal_then_transcript_id_then_command_then_unknown(world):
    by_line = {r["parent_line"]: r for r in _export(world)["rows"]}
    assert (by_line[1]["model"], by_line[1]["model_basis"], by_line[1]["ok"]) == \
        ("claude-opus-5-5", "transcript-id", True)
    # The journal outlives the transcript; interrupted means not ok.
    assert (by_line[3]["model"], by_line[3]["model_basis"], by_line[3]["ok"]) == \
        ("claude-opus-5-5", "journal", False)
    # No id: the byte-identical Bash block, found in the subagent tree.
    assert (by_line[4]["model"], by_line[4]["model_basis"]) == ("claude-fable-5-1", "transcript-command")
    assert (by_line[5]["model"], by_line[5]["ok"]) == ("claude-opus-5", False)
    assert (by_line[6]["model"], by_line[6]["model_basis"], by_line[6]["ok"]) == ("unknown", "unknown", None)


def test_the_journal_wins_over_the_transcript(world):
    with world["journal"].open("a") as fh:
        fh.write(json.dumps({"tool_use_id": "tu1", "model": "claude-sonnet-5"}) + "\n")
    row = [r for r in _export(world)["rows"] if r["parent_line"] == 1][0]
    assert (row["model"], row["model_basis"], row["ok"]) == ("claude-sonnet-5", "journal", True)


def test_a_command_join_needs_one_unambiguous_model(world):
    main = world["tmp"] / "projects" / "sess-a.jsonl"
    with main.open("a") as fh:
        fh.write(json.dumps(_assistant("claude-opus-5", [_bash("tu-dup", heredoc(OTHER))])) + "\n")
    row = [r for r in _export(world)["rows"] if r["parent_line"] == 4][0]
    assert row["model"] == "unknown"


def test_without_transcripts_only_the_journal_attributes(world):
    by_line = {r["parent_line"]: r for r in _export(world, transcripts=False)["rows"]}
    assert by_line[1]["model"] == "unknown" and by_line[3]["model"] == "claude-opus-5-5"
    assert _export(world, transcripts=False)["transcripts_read"] == 0


def test_contaminated_and_low_quality_programs_are_counted_but_not_kept(world):
    result = cx.export(world["log"], origin="o", attribution=world["journal"], local=frozenset())
    kept = {r["parent_line"] for r in result["rows"]}
    assert kept == {1, 3, 4, 5}
    c = result["counts"]
    assert c["contaminated"]["eval2"] == 1 and c["quality"]["trivial"] == 1
    assert c["programs"] == 6 and c["distinct"] == 5 and c["distinct_tier_a"] == 4
    assert c["distinct_tier_a_clean"] == 3 and c["distinct_tainted"] == 1
    assert c["lines"] == 9 and c["records"] == 7 and c["commands"] == 6


def test_taint_is_per_program_so_a_clean_occurrence_does_not_launder_it(tmp_path):
    log = tmp_path / "log.jsonl"
    log.write_text("".join(json.dumps({"kind": "bash_command", "command": c}) + "\n" for c in (
        heredoc(GOOD), heredoc(GOOD, "ls bank_v3 && "), heredoc(OTHER))))
    result = cx.export(log, origin="o", local=frozenset(), transcripts=False)
    assert [r["program"] for r in result["rows"]] == [OTHER]
    every = cx.export(log, origin="o", local=frozenset(), transcripts=False, keep="all")["rows"]
    assert [r["contaminated"] for r in every] == ["elsewhere:bank", "bank", ""]
    assert cx.select_rows(every) == cx.select_rows(result["rows"])


@pytest.mark.parametrize("command,rule", [
    ("python3 -c 'print(1)' > bank_v3/x", "bank"),
    ("cat positive-control/out | python3 -c 'print(1)'", "positive-control"),
    ("python3 -c 'print(1)' completions.jsonl", "completions"),
    ("tail ~/.lypning/invocations.jsonl | python3 -c 'print(1)'", "capture-log"),
    ("ls eval-2 && python3 -c 'print(1)'", "eval2"),
    ("python3 -c 'print(1)'", ""),
])
def test_contamination_rules(command, rule):
    assert cx.contamination(command) == rule


def test_privacy_rejected_text_is_withheld_even_from_the_audit_file(tmp_path):
    log = tmp_path / "log.jsonl"
    secret = GOOD + "\npassword = 'hunter2hunter2x'"
    log.write_text(json.dumps({"kind": "bash_command", "command": heredoc(secret)}) + "\n")
    (row,) = cx.export(log, origin="o", local=frozenset(), keep="all", transcripts=False)["rows"]
    assert row["quality"] == "privacy" and row["program"] is None
    assert row["source_sha256"] == hashlib.sha256(secret.encode()).hexdigest()


def test_selection_folds_occurrences_prefers_opus_5_5_and_drops_other_vendors():
    base = {"quality": "", "contaminated": "", "argv_tail": [], "ok": None}
    rows = [
        dict(base, program="a", source_sha256="sa", model="claude-opus-5", parent_event_id="e1"),
        dict(base, program="a", source_sha256="sa", model="claude-opus-5", parent_event_id="e2"),
        dict(base, program="a", source_sha256="sa", model="claude-opus-5-5", parent_event_id="e3"),
        dict(base, program="b", source_sha256="sb", model="claude-opus-5", parent_event_id="e4"),
        dict(base, program="c", source_sha256="sc", model="unknown", parent_event_id="e5"),
        dict(base, program="d", source_sha256="sd", model="gpt-5.1-codex", host="codex",
             parent_event_id="e6"),
        dict(base, program="e", source_sha256="se", model="gpt-5", parent_event_id="e7"),
        dict(base, program="f", source_sha256="sf", model="claude-opus-5-5", quality="trivial"),
    ]
    out = cx.select_rows(rows)
    assert [r["source_sha256"] for r in out] == ["sa", "sb", "sc"]
    assert out[0]["model"] == "claude-opus-5-5" and out[0]["preferred"]
    assert out[0]["parent_event_ids"] == ["e1", "e2", "e3"]
    assert out[0]["models"] == {"claude-opus-5": 2, "claude-opus-5-5": 1}
    assert [r["source_sha256"] for r in cx.select_rows(rows, models=["claude-opus-5-5"])] == ["sa"]
    opted = cx.select_rows(rows, hosts=("claude", "codex"), allow_other_vendors=True)
    assert {"sd", "se"} <= {r["source_sha256"] for r in opted}
    assert [r["hosts"] for r in opted if r["source_sha256"] == "sd"] == [["codex"]]


# --- eval2_select pointed at the export ------------------------------------------


def test_eval2_select_takes_export_rows_and_carries_their_provenance(world):
    rows = cx.export(world["log"], origin="o", attribution=world["journal"], local=frozenset())["rows"]
    records = e2s.records_from_export(rows)
    assert [r["outcome"] for r in records] == ["captured"] * 3
    assert records[0]["capture"]["model"] == "claude-opus-5-5"
    calls = []

    def runner(program, **kw):
        calls.append(program)
        return RunResult(0, "27\n", "", 0.01)

    result = e2s.select(records, runner=runner)
    assert result["selected"] == 3 and set(calls) == {GOOD, OTHER, THIRD}
    cand = [c for c in result["candidates"] if c["program"] == GOOD][0]
    assert cand["outcome"] == "captured" and cand["source_entry_id"].startswith("cx-")
    assert cand["source_sha256"] == hashlib.sha256(GOOD.encode()).hexdigest()
    assert cand["session_file"] == "sess-a"
    assert cand["capture"]["tier"] == "capture" and len(cand["capture"]["parent_event_ids"]) == 2


def test_cli_capture_export_writes_rows_and_an_evidence_snapshot(world, tmp_path, capsys):
    from pipeline import cli
    out = tmp_path / "export.jsonl"
    rc = cli.main(["capture-export", "--log", str(world["log"]), "--origin", "host-1/gen-1",
                   "--attribution", str(world["journal"]), "--output", str(out),
                   "--evidence", str(tmp_path / "ev")])
    assert rc == 0
    text = capsys.readouterr().out
    assert "distinct tier A" in text and "rows written" in text and GOOD not in text
    rows = read_jsonl(out)
    assert rows and all(r["tier"] == "capture" and not r["quality"] for r in rows)
    assert (tmp_path / "ev" / "manifest.json").is_file()
    cands = tmp_path / "cands.jsonl"
    rc = cli.main(["eval2-select", "--export", str(out), "--output", str(cands),
                   "--sightings", str(tmp_path / "none"), "--jobs", "1"])
    assert rc == 0 and all(c["outcome"] == "captured" for c in read_jsonl(cands))


def test_cli_refuses_to_overwrite_its_own_input(world, capsys):
    from pipeline import cli
    before = world["log"].read_bytes()
    rc = cli.main(["capture-export", "--log", str(world["log"]), "--origin", "o",
                   "--output", str(world["log"])])
    assert rc == 2 and world["log"].read_bytes() == before
    rc = cli.main(["eval2-select", "--export", "x", "--classified", "y", "--output", "z"])
    assert rc == 2


# --- privacy, double counting and robustness ----------------------------------------


def _log(tmp_path, records):
    log = tmp_path / "log.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in records))
    return log


def test_the_audit_file_withholds_private_text_whichever_rule_rejected_it_first(tmp_path):
    reader = GOOD + "\nprint(open('/Users/someone/.ssh/id_rsa').read())"
    log = _log(tmp_path, [{"kind": "bash_command", "command": heredoc(reader)},
                          {"kind": "bash_command", "command": "python3 -c 'import sys; x=' "
                                                              "--token ghp_abcdefghijklmnop"}])
    rows = cx.export(log, origin="o", local=frozenset(), keep="all", transcripts=False)["rows"]
    assert rows[0]["quality"] == "reads-files"
    assert rows[0]["program"] is None and rows[0]["argv_tail"] is None
    text = json.dumps(rows)
    assert "/Users/someone" not in text and "ghp_abcdefghijklmnop" not in text


def test_a_private_argv_makes_a_tier_a_occurrence_private(tmp_path):
    program = GOOD + "\nimport sys\nprint(sys.argv[1:])"
    log = _log(tmp_path, [
        {"kind": "bash_command", "command": "python3 -c %s --password hunter2"
                                            % shlex.quote(program)},
        {"kind": "bash_command", "command": "python3 -c %s /Users/someone/x" % shlex.quote(program)},
        {"kind": "bash_command", "command": "python3 -c %s 7" % shlex.quote(program)},
    ])
    result = cx.export(log, origin="o", local=frozenset(), keep="all", transcripts=False)
    assert [r["quality"] for r in result["rows"]] == ["privacy", "privacy", ""]
    assert [r["argv_tail"] for r in result["rows"]] == [None, None, ["7"]]
    assert result["counts"]["private_argv"] == 2
    kept = cx.export(log, origin="o", local=frozenset(), transcripts=False)
    assert [r["argv_tail"] for r in kept["rows"]] == [["7"]]
    assert kept["counts"]["distinct_tier_a_clean"] == 1


def test_one_tool_call_logged_twice_counts_once(tmp_path):
    """The same PreToolUse event from a user-scope and a project-scope hook."""
    rec = {"kind": "bash_command", "tool_use_id": "tu1", "session": "s",
           "command": heredoc(GOOD)}
    log = _log(tmp_path, [rec, rec, dict(rec, tool_use_id="tu2")])
    result = cx.export(log, origin="o", local=frozenset(), transcripts=False)
    c = result["counts"]
    assert c["duplicate_events"] == 1 and c["commands"] == 2 and c["programs"] == 2
    assert [r["parent_line"] for r in result["rows"]] == [1, 3]
    (sel,) = cx.select_rows(result["rows"])
    assert sel["models"] == {"unknown": 2} and len(sel["parent_event_ids"]) == 2


def test_a_lone_surrogate_is_counted_and_does_not_abort_the_export(tmp_path):
    log = tmp_path / "log.jsonl"
    bad = json.dumps({"kind": "bash_command", "command": "python3 -c 'print(\"\\ud800\")'"})
    assert "\\ud800" in bad
    log.write_text(bad.replace("\\\\ud800", "\\ud800") + "\n"
                   + json.dumps({"kind": "bash_command", "command": heredoc(GOOD)}) + "\n")
    result = cx.export(log, origin="o", local=frozenset(), transcripts=False)
    assert result["counts"]["invalid_utf8"] == 1
    assert [r["program"] for r in result["rows"]] == [GOOD]
    cx.write_rows(tmp_path / "out.jsonl", result["rows"])


def test_write_rows_makes_an_existing_file_owner_only(tmp_path):
    import os
    path = tmp_path / "rows.jsonl"
    path.write_text("old\n")
    os.chmod(str(path), 0o644)
    cx.write_rows(path, [{"a": 1}])
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert read_jsonl(path) == [{"a": 1}]
