"""Publishing captures: idempotent bytes, and nothing published that leaks.

The export runs on every Stop event — every turn boundary of every session — so
"unchanged input produces unchanged bytes" is not tidiness, it is the difference
between a harness that adds one line to a commit and one that dirties the tree
on every turn. Redaction is tested for what it removes AND for what it records:
the hit list is how a human reading a sightings diff learns that a credential
was scrubbed, and which one to rotate, without the value ever being written.
"""

from __future__ import annotations

import json
import os

import pytest


from lypning import corpus, harvest, paths

# Nothing in the shipped corpus, so `is_interesting` keeps it: a program the
# corpus already holds is dropped as `known`, which would make this test pass
# for the wrong reason.
UNIQUE = "print('lypning export fixture 4f2a')"
UNIQUE_HEREDOC = "print('lypning heredoc fixture 4f2a')"


def _log(records):
    log = paths.log_path()
    paths.ensure_dir(log.parent)
    log.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return log


def _invocation(program, session="sess-a", ts="2026-01-01T00:00:00.000Z"):
    return {"kind": "python_invocation", "program": program, "argv_tail": [],
            "session": session, "ts": ts}


def test_export_is_byte_identical_on_a_second_run(project):
    _log([
        _invocation(UNIQUE),
        {"kind": "bash_command", "session": "sess-a", "ts": "2026-01-01T00:00:01.000Z",
         "command": "python3 <<'PY'\n%s\nPY" % UNIQUE_HEREDOC},
    ])

    path, added, total = harvest.export_sightings(project, quiet=True)
    assert path is not None
    assert path == paths.sightings_dir(project) / "sess-a.jsonl"
    assert added == 2 and total == 2
    first = path.read_bytes()

    path2, added2, _ = harvest.export_sightings(project, quiet=True)
    # Nothing changed, so nothing was written — not even the same bytes again.
    assert added2 == 0
    assert path2 is None
    assert path.read_bytes() == first


def test_export_sorts_by_key_so_the_log_order_does_not_reach_the_file(project):
    _log([_invocation(UNIQUE), _invocation(UNIQUE_HEREDOC)])
    path, _, _ = harvest.export_sightings(project, quiet=True)
    keys = [json.loads(l)["key"] for l in path.read_text(encoding="utf-8").splitlines()]
    assert keys == sorted(keys)

    _log([_invocation(UNIQUE_HEREDOC), _invocation(UNIQUE)])
    before = path.read_bytes()
    harvest.export_sightings(project, quiet=True)
    assert path.read_bytes() == before


def test_export_writes_one_file_per_session(project):
    _log([_invocation(UNIQUE, session="sess-a"),
          _invocation(UNIQUE_HEREDOC, session="sess-b")])
    harvest.export_sightings(project, quiet=True)
    names = sorted(p.name for p in paths.sightings_dir(project).iterdir())
    assert names == ["sess-a.jsonl", "sess-b.jsonl"]


def test_a_session_that_ran_no_python_writes_nothing(project):
    _log([{"kind": "exit", "session": "sess-a", "ts": "2026-01-01T00:00:00.000Z"}])
    assert harvest.export_sightings(project, quiet=True) == (None, 0, 0)
    assert not paths.sightings_dir(project).exists()


def test_an_empty_program_is_not_published(project):
    _log([_invocation("   \n  "), _invocation("pass")])
    path, added, _ = harvest.export_sightings(project, quiet=True)
    assert (path, added) == (None, 0)


# A shape, not a credential: 30 characters that no scanner has ever issued.
FAKE_KEY = "sk-not-a-real-key-0123456789ab"


def test_redaction_removes_an_assigned_key_and_records_the_hit():
    program = 'api_key = "%s"\nprint(api_key)' % FAKE_KEY
    out, hits = harvest.redact(program)
    assert FAKE_KEY not in out
    assert "REDACTED" in out
    assert hits == ["api_key"]
    assert harvest.is_safe(hits)
    # The NAME survives: it says which credential to rotate without restating it.
    assert "api_key" in out and "print(api_key)" in out


def test_redaction_is_idempotent():
    once, _ = harvest.redact('token = "%s"' % FAKE_KEY)
    twice, hits = harvest.redact(once)
    assert twice == once
    assert hits == []  # a marker must not re-match as a credential


def test_redaction_catches_a_flag_and_its_value():
    out, hits = harvest.redact("run --password hunter2seventeen")
    assert "hunter2seventeen" not in out
    assert hits == ["password"]
    # A bare value gets the space-free marker, which is itself a long opaque
    # run: the residual scan must not read our own output as the leftover
    # credential and throw away the sighting it just made safe.
    assert harvest.is_safe(hits)


def test_a_credential_split_across_two_argv_elements_is_caught():
    # Neither element contains both the name and the value, so a per-string scan
    # sees a harmless flag followed by a harmless word.
    tail, hits = harvest.redact_argv(["script.py", "--token", "abc123def456"])
    assert tail[0] == "script.py"
    assert "abc123def456" not in tail
    assert "argv value" in hits


def test_a_redacted_program_is_published_under_the_key_of_the_text_written(project):
    program = 'secret = "%s"\nprint("lypning redaction fixture 4f2a")' % FAKE_KEY
    _log([_invocation(program)])
    path, added, _ = harvest.export_sightings(project, quiet=True)
    assert added == 1
    rec = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert FAKE_KEY not in path.read_text(encoding="utf-8")
    assert rec["key"] == rec["id"] == harvest.sighting_key(rec["program"])


def test_a_program_whose_credential_cannot_be_scrubbed_is_dropped(project):
    # A name near an opaque token that no pattern describes: publishing it would
    # cost a rotation, dropping it costs one corpus entry.
    program = "headers = {}\nheaders['x'] = 'Bearer' + 'Ab3' + 'k9QpLm2xR7vT4wYz1sD8fG5hJ0nB6c'"
    _log([_invocation(program)])
    path, added, _ = harvest.export_sightings(project, quiet=True)
    assert added == 0 and path is None


def test_normalise_keeps_indentation_and_drops_the_rest():
    # Indentation is syntax; surrounding blank lines are how the shim sees
    # `-c $'\nimport os'` and would otherwise file one program under two keys.
    assert harvest.normalise("\n\nif x:\n    y  \n\n") == "if x:\n    y"
    assert harvest.sighting_key("\nprint(1)\n") == harvest.sighting_key("print(1)")


# --- which model issued it ----------------------------------------------------
#
# The join is the whole feature: the hook cannot know the model (nothing in its
# payload names one), so it writes down the `tool_use_id` and the transcript
# path, and the model is resolved here, once, on the cold path. What these pin
# is the shape of the answer when the join misses — unattributed, never a guess
# — and the merge asymmetry between this module and `corpus`, which no gate can
# see.

SESSION = "11111111-2222-3333-4444-555555555555"


def _assistant(ts, model, tool_use_id=None, command=None):
    content = []
    if tool_use_id is not None:
        content.append({"type": "tool_use", "id": tool_use_id, "name": "Bash",
                        "input": {"command": command or "python3 -c 'print(1)'"}})
    return json.dumps({"type": "assistant", "timestamp": ts,
                       "message": {"model": model, "content": content}})


def _transcripts(tmp_path, main_records, subagent_records=()):
    """A main session transcript and, beside it, its subagents tree.

    The layout is the CLI's own: `transcript_path` names the MAIN file, and a
    subagent's tool_use blocks are only ever in
    `<dir>/<session>/subagents/**/agent-*.jsonl`.
    """
    root = tmp_path / "projects" / "-tmp-p"
    root.mkdir(parents=True, exist_ok=True)
    main = root / (SESSION + ".jsonl")
    main.write_text("".join(r + "\n" for r in main_records), encoding="utf-8")
    if subagent_records:
        sub = root / SESSION / "subagents" / "workflows" / "wf_1"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "agent-abc.jsonl").write_text(
            "".join(r + "\n" for r in subagent_records), encoding="utf-8")
    return main


def _hook(command, tool_use_id, transcript, ts="2026-09-02T10:00:00.500Z"):
    return {"kind": "bash_command", "session": SESSION, "ts": ts,
            "command": command, "transcript": str(transcript),
            "tool_use_id": tool_use_id}


def _write_log(tmp_path, records):
    log = tmp_path / "invocations.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return log


def test_the_model_is_joined_by_tool_use_id_across_the_subagent_tree(tmp_path):
    # Most captured python in this project is issued by subagents, whose
    # tool_use blocks are not in the transcript the hook recorded. Indexing only
    # that file would leave them unattributed; a time join over it would do
    # worse and file them under the parent loop's model.
    main = _transcripts(
        tmp_path,
        [_assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_main",
                    "python3 -c 'print(1)'")],
        [_assistant("2026-09-02T10:00:10.000Z", "claude-fable-5-1", "toolu_sub",
                    "python3 -c 'print(2)'")],
    )
    log = _write_log(tmp_path, [
        _hook("python3 -c 'print(1)'", "toolu_main", main),
        _hook("python3 -c 'print(2)'", "toolu_sub", main, "2026-09-02T10:00:10.500Z"),
    ])
    by_program = {s.program: s for s in harvest.parse_log(log)}
    assert by_program["print(1)"].models == (("claude-opus-5", 1),)
    assert by_program["print(2)"].models == (("claude-fable-5-1", 1),)


def test_an_occurrence_that_cannot_be_joined_is_left_unattributed(tmp_path):
    # sum(models) <= count, never ==. A log line written before the id was
    # captured, or one whose id is in no transcript, contributes to the count
    # and to nothing else — and no "unknown" bucket is stored for it.
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_main"),
    ])
    log = _write_log(tmp_path, [
        _hook("python3 -c 'print(1)'", "toolu_main", main),
        _hook("python3 -c 'print(1)'", "toolu_gone", main, "2026-09-02T10:00:01.500Z"),
        {"kind": "bash_command", "session": SESSION, "ts": "2026-09-02T10:00:02.500Z",
         "command": "python3 -c 'print(1)'", "transcript": str(main)},  # no id at all
    ])
    s = harvest.parse_log(log)[0]
    assert s.count == 3
    assert s.models == (("claude-opus-5", 1),)
    assert sum(n for _, n in s.models) < s.count
    assert "unknown" not in s.to_obj().get("models", {})


def test_a_synthetic_record_is_not_an_issuing_model(tmp_path):
    # The CLI writes assistant records with the literal model "<synthetic>" for
    # turns it produced without asking a model. Storing that as an attribution
    # would put a name in the corpus nobody can slice on.
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "<synthetic>", "toolu_main"),
    ])
    log = _write_log(tmp_path, [_hook("python3 -c 'print(1)'", "toolu_main", main)])
    s = harvest.parse_log(log)[0]
    assert s.models == ()
    assert "models" not in s.to_obj()


def test_a_shim_occurrence_joins_on_its_timestamp(tmp_path):
    """The nested-spawn feed has no id to join on — the hook never saw it.

    It has a session and a timestamp, and the session leads to a transcript
    only because a hook record in the same log named one. The pick is the latest
    assistant record at or before the invocation, by plain string compare: both
    stamps are UTC with a literal Z, and the shim's is second-precision on a BSD
    host, which is why nothing here parses a date.
    """
    main = _transcripts(
        tmp_path,
        [_assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_main")],
        [_assistant("2026-09-02T10:00:10.000Z", "claude-fable-5-1", "toolu_sub")],
    )
    log = _write_log(tmp_path, [
        _hook("python3 -c 'print(1)'", "toolu_main", main),
        {"kind": "python_invocation", "session": SESSION, "ts": "2026-09-02T10:00:11Z",
         "program": UNIQUE, "argv_tail": []},
    ])
    by_program = {s.program: s for s in harvest.parse_log(log)}
    assert by_program[UNIQUE].models == (("claude-fable-5-1", 1),)


def test_a_shim_occurrence_with_no_transcript_in_the_log_stays_unattributed(tmp_path):
    log = _write_log(tmp_path, [
        {"kind": "python_invocation", "session": SESSION, "ts": "2026-09-02T10:00:11Z",
         "program": UNIQUE, "argv_tail": []},
    ])
    assert harvest.parse_log(log)[0].models == ()


def test_one_program_under_two_models_is_one_record_carrying_both(tmp_path):
    main = _transcripts(
        tmp_path,
        [_assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_a"),
         _assistant("2026-09-02T10:00:20.000Z", "claude-opus-5", "toolu_c")],
        [_assistant("2026-09-02T10:00:10.000Z", "claude-fable-5-1", "toolu_b")],
    )
    log = _write_log(tmp_path, [
        _hook("python3 -c 'print(1)'", "toolu_a", main, "2026-09-02T10:00:00.500Z"),
        _hook("python3 -c 'print(1)'", "toolu_b", main, "2026-09-02T10:00:10.500Z"),
        _hook("python3 -c 'print(1)'", "toolu_c", main, "2026-09-02T10:00:20.500Z"),
    ])
    s = harvest.parse_log(log)[0]
    assert s.count == 3
    assert s.models == (("claude-fable-5-1", 1), ("claude-opus-5", 2))
    assert s.to_obj()["models"] == {"claude-fable-5-1": 1, "claude-opus-5": 2}


def test_export_with_models_is_byte_identical_on_a_second_run(project, tmp_path):
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-fable-5-1", "toolu_a",
                   "python3 -c \"%s\"" % UNIQUE),
    ])
    _log([_hook("python3 -c \"%s\"" % UNIQUE, "toolu_a", main)])

    path, added, _ = harvest.export_sightings(project, quiet=True)
    assert added == 1
    first = path.read_bytes()
    assert b'"models":{"claude-fable-5-1":1}' in first

    path2, added2, _ = harvest.export_sightings(project, quiet=True)
    # Not a byte changed, and therefore not written: the export runs on every
    # turn boundary, and a models field that grew on each pass would dirty the
    # tree forever.
    assert added2 == 0 and path2 is None
    assert path.read_bytes() == first


def test_a_sightings_line_with_no_models_gains_no_key(tmp_path):
    # Every committed sightings line predates this field. Reading and rewriting
    # one must not add `"models":{}` to it — that is ~4000 committed lines of
    # churn, and the diff nobody reviews is where a captured credential lives.
    line = ('{"key":"py-a","id":"py-a","program":"print(1)","argv_tail":[],'
            '"source":"hook","session":"s","first_seen":"2026-01-01T00:00:00.000Z",'
            '"count":2,"stdin_sample":null}\n')
    p = tmp_path / "s.jsonl"
    p.write_text(line, encoding="utf-8")
    got = harvest.read_sightings(p)
    assert got[0].models == ()
    assert harvest.serialise(got) == line


def test_a_sighting_carries_unknown_keys_through(tmp_path):
    """The forward-compatibility bucket, and it is live rather than theoretical.

    These files are committed and every session rewrites them with whatever
    version of lypning it is running. Without this, an older harvest strips
    every key a newer one added — including `models` — one Stop hook at a time,
    and the loss looks exactly like nothing happening.
    """
    obj = {"key": "py-a", "id": "py-a", "program": "print(1)", "argv_tail": [],
           "source": "hook", "session": "s", "first_seen": "", "count": 1,
           "stdin_sample": None, "invented_later": {"by": "a peer"}}
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps(obj) + "\n", encoding="utf-8")
    s = harvest.read_sightings(p)[0]
    assert s.extra == {"invented_later": {"by": "a peer"}}
    assert s.to_obj()["invented_later"] == {"by": "a peer"}


# --- the merge asymmetry ------------------------------------------------------
#
# The one thing about this field that no gate can catch. The two functions below
# merge the same-looking histogram in two different ways, on purpose, and a
# future copy-paste from one to the other is silent: the counts stay plausible
# and only drift.


def _the_hole_is_not_negative(record):
    """``count - sum(models)`` is the unattributed hole every reader is invited
    to compute — the docstring on :class:`harvest.Sighting`, `docs/CAPTURE.md`
    and the changelog all promise it — so it is asserted on every path that can
    produce a record, not only on the one that first got it wrong."""
    assert sum(n for _, n in record.models) <= record.count, record


def test_two_sightings_of_one_program_merge_their_models_with_max():
    # Both sides count the SAME occurrence keys — a session's live log and its
    # own published file describe the same invocations — so summing here would
    # double the record on every export, which is the exact bug `count=max`
    # exists to prevent.
    a = harvest.Sighting(key="py-a", program="print(1)", count=3,
                         models=(("claude-fable-5-1", 3),))
    b = harvest.Sighting(key="py-a", program="print(1)", count=3,
                         models=(("claude-fable-5-1", 3), ("claude-opus-5", 1)))
    merged = harvest._combine(a, b)
    assert merged.models == (("claude-fable-5-1", 3), ("claude-opus-5", 1))
    # Four occurrences are accounted for by name, so the count is four. The
    # scalar max of two 3s was the undercount: `b` saw a run of this program
    # that `a` never did, and taking the larger of two partial views threw it
    # away. See `_count_at_least_the_models`.
    assert merged.count == 4
    _the_hole_is_not_negative(merged)
    assert harvest._combine(merged, merged) == merged


def test_two_disjoint_models_raise_the_count_instead_of_promising_a_negative_hole():
    """The ordinary cross-session shape, and the one the per-key max broke.

    A scalar max bounds a per-key max only when both sides carry the same model
    keys. Two sessions that ran one program under two different models do not,
    and their occurrence sets are then genuinely disjoint — so the count follows
    the histogram up rather than the histogram being trimmed to the count.
    """
    a = harvest.Sighting(key="py-a", program="print(1)", count=1,
                         models=(("claude-opus-5", 1),))
    b = harvest.Sighting(key="py-a", program="print(1)", count=1,
                         models=(("claude-fable-5-1", 1),))
    merged = harvest._combine(a, b)
    assert merged.models == (("claude-fable-5-1", 1), ("claude-opus-5", 1))
    assert merged.count == 2
    _the_hole_is_not_negative(merged)
    # Commutative, and idempotent: re-merging re-derives the same sum, which the
    # max absorbs. A count that grew on every export would be the doubling bug
    # this whole merge exists to prevent, wearing a new hat.
    assert harvest._combine(b, a) == merged
    assert harvest._combine(merged, merged) == merged


def test_a_record_with_no_models_keeps_the_count_it_had():
    # Almost every committed sighting predates this field. An empty histogram
    # sums to zero, so the clamp cannot move one of them and no committed line
    # is rewritten by it.
    a = harvest.Sighting(key="py-a", program="print(1)", count=7)
    b = harvest.Sighting(key="py-a", program="print(1)", count=2)
    assert harvest._combine(a, b).count == 7


def test_a_cross_session_merge_never_promises_more_models_than_occurrences(project, tmp_path):
    """End to end, through the public path a Stop hook actually takes.

    Session A published its sighting; session B ran the same program under a
    different model and is still in the live log. `collect` merges the two, and
    a reader that computed `count - sum(models)` used to get -1.
    """
    program = UNIQUE
    key = harvest.sighting_key(program)
    root = paths.sightings_dir(project)
    paths.ensure_dir(root)
    (root / "sess-a.jsonl").write_text(json.dumps({
        "key": key, "id": key, "program": program, "argv_tail": [], "source": "hook",
        "session": "sess-a", "first_seen": "2026-09-01T00:00:00.000Z", "count": 1,
        "stdin_sample": None, "models": {"claude-opus-5": 1},
    }) + "\n", encoding="utf-8")

    command = "python3 -c \"%s\"" % program
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-fable-5-1", "toolu_b", command),
    ])
    _log([_hook(command, "toolu_b", main)])

    merged = {s.key: s for s in harvest.collect(project)}[key]
    assert merged.models == (("claude-fable-5-1", 1), ("claude-opus-5", 1))
    assert merged.count == 2
    _the_hole_is_not_negative(merged)


def test_folding_two_models_into_one_corpus_record_keeps_the_hole_non_negative(tmp_path):
    target = tmp_path / "corpus.jsonl"
    key = harvest.sighting_key(UNIQUE)

    def sighting(model):
        return harvest.Sighting(key=key, program=UNIQUE, count=1,
                                models=((model, 1),))

    harvest.fold_into_corpus([sighting("claude-opus-5")], target)
    harvest.fold_into_corpus([sighting("claude-fable-5-1")], target)
    e = corpus.load(target)[0]
    assert e.models == (("claude-fable-5-1", 1), ("claude-opus-5", 1))
    assert e.count == 2
    _the_hole_is_not_negative(e)
    # And the fold is still a pure function of its inputs: the same sightings
    # again move not one byte.
    before = target.read_bytes()
    harvest.fold_into_corpus([sighting("claude-fable-5-1")], target)
    assert target.read_bytes() == before


def test_a_merge_carries_the_unknown_keys_of_both_sides():
    """The forward-compatibility bucket has to survive the MERGE, not just the
    read and the write.

    That is the path a peer session running older code takes: it reads a
    published record, merges its own sighting into it, and writes the result
    back. A merge that kept only the published side's unknown keys would strip
    everything a newer lypning had just added to the incoming one — which is the
    silent field loss `extra` exists to prevent, arriving by another door.
    """
    a = harvest.Sighting(key="py-a", program="print(1)",
                         extra={"published_by": "an older lypning"})
    b = harvest.Sighting(key="py-a", program="print(1)",
                         extra={"invented_later": {"by": "a peer"}})
    merged = harvest._combine(a, b)
    assert merged.extra == {"published_by": "an older lypning",
                            "invented_later": {"by": "a peer"}}
    assert merged.to_obj()["invented_later"] == {"by": "a peer"}
    # On a key both sides know, the published record's own value wins: it is the
    # one already committed, and a merge is not the place to overwrite it.
    x = harvest.Sighting(key="py-a", program="print(1)", extra={"k": "published"})
    y = harvest.Sighting(key="py-a", program="print(1)", extra={"k": "incoming"})
    assert harvest._combine(x, y).extra == {"k": "published"}


def test_the_corpus_merge_sums_models_because_the_corpus_merge_sums():
    # The opposite rule, in corpus.merge, and it is not a mistake: two DISTINCT
    # records for one id there are two different sets of sightings, and the
    # identical-record collapse is what stops one being added twice.
    a = corpus.Entry(id="py-a", program="print(1)", count=2, first_seen="2026-01-01",
                     models=(("claude-fable-5-1", 2),))
    b = corpus.Entry(id="py-a", program="print(1)", count=1, first_seen="2026-01-02",
                     source="shim", models=(("claude-opus-5", 1),))
    merged = corpus.merge([a], [b])[0]
    assert merged.count == 3
    assert merged.models == (("claude-fable-5-1", 2), ("claude-opus-5", 1))
    # Disjoint model keys cannot tell a sum from a max — both answer 2 and 1 —
    # so the rule is pinned on a SHARED one, which is the only place the two
    # differ and therefore the only place a call site copied from the merge
    # above would show up at all.
    c = corpus.Entry(id="py-a", program="print(1)", count=1, first_seen="2026-01-03",
                     source="hook", models=(("claude-fable-5-1", 1),))
    shared = corpus.merge([a], [c])[0]
    assert shared.count == 3
    assert shared.models == (("claude-fable-5-1", 3),)
    # The models follow the count exactly, so the histogram is still a subset of
    # the occurrences it describes.
    assert sum(n for _, n in shared.models) <= shared.count
    # And the collapse still holds: the same record twice is one sighting.
    assert corpus.merge([a], [a])[0].models == (("claude-fable-5-1", 2),)


def test_folding_the_same_sightings_twice_does_not_inflate_the_models(tmp_path):
    target = tmp_path / "corpus.jsonl"
    sightings = [harvest.Sighting(key=harvest.sighting_key(UNIQUE), program=UNIQUE,
                                  count=2, models=(("claude-fable-5-1", 2),))]
    harvest.fold_into_corpus(sightings, target)
    first = target.read_bytes()
    harvest.fold_into_corpus(sightings, target)
    assert target.read_bytes() == first
    assert corpus.load(target)[0].models == (("claude-fable-5-1", 2),)


def test_a_log_with_nothing_to_join_does_not_read_a_transcript(tmp_path, monkeypatch):
    """The export runs at every turn boundary and a session's transcript tree is
    tens of megabytes, so a path nothing asks about is not opened.

    A log written before `tool_use_id` was captured — every line already on
    disk — therefore costs exactly what it cost before this field existed.
    """
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_main"),
    ])
    log = _write_log(tmp_path, [
        {"kind": "bash_command", "session": SESSION, "ts": "2026-09-02T10:00:01.500Z",
         "command": "python3 -c 'print(1)'", "transcript": str(main)},
    ])
    indexed = []
    monkeypatch.setattr(harvest, "_model_index",
                        lambda t, cache=None: indexed.append(t) or harvest._EMPTY_INDEX)
    assert harvest.parse_log(log)[0].models == ()
    assert indexed == []


# --- the time join, and the widths that make a string compare a compare -------


def test_timestamps_of_different_widths_are_ordered_by_the_instant():
    # The shim writes whole seconds on a host whose `date` has no %3N, the
    # transcript writes milliseconds, and raw `"…24Z" > "…24.900Z"` because Z
    # sorts after `.` — so one canonical width is what makes bisect a search
    # over time rather than over spelling.
    assert (harvest._canonical_ts("2026-09-02T01:05:24Z")
            < harvest._canonical_ts("2026-09-02T01:05:24.900Z"))
    assert (harvest._canonical_ts("2026-09-02T01:05:24.9Z")
            == harvest._canonical_ts("2026-09-02T01:05:24.900Z"))
    assert harvest._canonical_ts("2026-09-02T01:05:24") == harvest._canonical_ts(
        "2026-09-02T01:05:24.000Z")
    # Not this shape at all: no model, rather than a stamp that sorts anywhere.
    assert harvest._canonical_ts("2026-09-02T01:05:24+02:00") is None
    assert harvest._canonical_ts("yesterday") is None
    assert harvest._canonical_ts(None) is None


def test_a_second_precision_stamp_does_not_join_to_the_model_that_spoke_next(tmp_path):
    """The shim's stamp is second-precision on this host, and the models here
    change inside that second.

    Compared raw, `2026-09-02T01:05:24Z` sorts after `2026-09-02T01:05:24.900Z`,
    so "the latest record at or before the spawn" would return the model that
    started speaking 900 ms AFTER it. A wrong model is worse than no model: the
    unattributed hole is reported, and a lie is not.
    """
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T01:05:20.100Z", "claude-sonnet-5", "toolu_main"),
        _assistant("2026-09-02T01:05:24.900Z", "claude-haiku-4-5-20251001"),
    ])
    log = _write_log(tmp_path, [
        _hook("python3 -c 'print(1)'", "toolu_main", main),
        {"kind": "python_invocation", "session": SESSION,
         "ts": "2026-09-02T01:05:24Z", "program": UNIQUE, "argv_tail": []},
    ])
    by_program = {s.program: s for s in harvest.parse_log(log)}
    assert by_program[UNIQUE].models == (("claude-sonnet-5", 1),)


# --- the incremental index ----------------------------------------------------
#
# The join runs on every Stop, and the log it reads is append-only: the set of
# transcripts it asks about grows for as long as the log lives, so a full
# re-index is a cost that rises with the age of the log and not with the work
# the turn did. These pin the two halves of the answer — that only appended
# bytes are read, and that every way the cache can be wrong costs time and
# never a model.


def _append(path, text):
    with open(str(path), "a", encoding="utf-8") as fh:
        fh.write(text)


def _forget_the_journal():
    """Delete the attribution journal, so the next harvest has only the cache.

    The tests below are about the INDEX CACHE: whether it notices that a file
    no longer holds the ids it cached. With the journal in place those ids stay
    attributed after the file loses them — correctly, since a tool_use id is
    issued by one model forever — and the cache's answer would never be seen.
    """
    try:
        harvest.attribution_path().unlink()
    except OSError:
        pass


def test_the_second_harvest_reads_only_the_bytes_that_were_appended(tmp_path, monkeypatch):
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_a",
                   "python3 -c 'print(1)'"),
    ])
    log = _write_log(tmp_path, [_hook("python3 -c 'print(1)'", "toolu_a", main)])
    assert harvest.parse_log(log)[0].models == (("claude-opus-5", 1),)

    scanned = []
    real = harvest._scan_transcript
    monkeypatch.setattr(harvest, "_scan_transcript",
                        lambda text, *rest: scanned.append(text) or real(text, *rest))
    _append(main, _assistant("2026-09-02T10:00:10.000Z", "claude-fable-5-1",
                             "toolu_b", "python3 -c 'print(2)'") + "\n")
    log = _write_log(tmp_path, [
        _hook("python3 -c 'print(1)'", "toolu_a", main),
        _hook("python3 -c 'print(2)'", "toolu_b", main, "2026-09-02T10:00:10.500Z"),
    ])
    by_program = {s.program: s for s in harvest.parse_log(log)}
    # The first record is still attributed although it was never read again:
    # that is the cache answering, and it is the whole point of it.
    assert by_program["print(1)"].models == (("claude-opus-5", 1),)
    assert by_program["print(2)"].models == (("claude-fable-5-1", 1),)
    text = "".join(scanned)
    assert "toolu_b" in text
    assert "toolu_a" not in text


def test_a_half_written_final_line_is_read_when_it_is_whole(tmp_path):
    """The CLI is appending while this runs, so the last line is routinely half
    a line. Consuming it would move the offset past bytes that were never really
    read, and that record would never be seen again."""
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_a"),
    ])
    whole = _assistant("2026-09-02T10:00:10.000Z", "claude-fable-5-1", "toolu_b")
    _append(main, whole[:30])  # a line the writer has not finished

    log = _write_log(tmp_path, [_hook("python3 -c 'print(1)'", "toolu_b", main)])
    assert harvest.parse_log(log)[0].models == ()

    _append(main, whole[30:] + "\n")
    assert harvest.parse_log(log)[0].models == (("claude-fable-5-1", 1),)


def test_a_transcript_rewritten_in_place_is_indexed_again_rather_than_resumed(tmp_path):
    """Same path, same inode, no shorter — and a completely different file.

    Append-only is a property of the writer, not of the filesystem: a restored
    backup or a copied-over path breaks it, and resuming at the stored offset
    would then splice one file's records onto another's and keep answering with
    ids that are no longer in it. The prefix digest is what notices.
    """
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_a"),
    ])
    log = _write_log(tmp_path, [_hook("python3 -c 'print(1)'", "toolu_a", main)])
    assert harvest.parse_log(log)[0].models == (("claude-opus-5", 1),)

    main.write_text("".join(
        _assistant("2026-09-02T11:00:0%d.000Z" % i, "claude-fable-5-1", "toolu_b") + "\n"
        for i in range(4)), encoding="utf-8")
    # `toolu_a` is not in this file any more, so nothing can attribute it.
    _forget_the_journal()
    assert harvest.parse_log(log)[0].models == ()


def test_every_way_the_cache_can_be_wrong_costs_time_and_not_a_model(tmp_path):
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_a"),
    ])
    log = _write_log(tmp_path, [_hook("python3 -c 'print(1)'", "toolu_a", main)])
    expected = (("claude-opus-5", 1),)
    assert harvest.parse_log(log)[0].models == expected
    cache = harvest._index_cache_path()
    assert cache.is_file()

    # Read from the module, not spelled 1: a bumped version would otherwise
    # reject every case below on the version alone and pass this test without
    # ever exercising the shape it is about.
    v = harvest._CACHE_VERSION
    for corrupt in (
        "",                                             # a zero-length write
        "{not json",                                    # a half-written file
        json.dumps({"version": 999, "files": {}}),      # a schema we do not read
        json.dumps({"version": v, "files": "nope"}),
        json.dumps({"version": v, "files": {str(main): {"offset": "far"}}}),
        json.dumps({"version": v, "files": {str(main): {
            "offset": 10, "ino": 1, "dev": 1, "digest": "x",
            "ids": {"toolu_a": 3}, "timeline": []}}}),  # a model that is not a name
        json.dumps({"version": v, "files": {str(main): {
            "offset": 10, "ino": 1, "dev": 1, "digest": "x",
            "ids": {}, "timeline": [[1, "m"]]}}}),      # a stamp bisect cannot compare
        # Well-formed and STALE, which is the dangerous one: it names a model,
        # and believing it would be the silent wrong answer rather than a slow
        # one. The inode it claims is not this file's.
        json.dumps({"version": v, "files": {str(main): {
            "offset": 10, "ino": 1, "dev": 1, "digest": "x",
            "ids": {"toolu_a": "claude-not-this-one"}, "timeline": []}}}),
        # Well-formed, right inode, and the consumed prefix has moved: same
        # path, different bytes. Only re-reading can tell.
        json.dumps({"version": v, "files": {str(main): {
            "offset": 10, "ino": os.stat(str(main)).st_ino,
            "dev": os.stat(str(main)).st_dev, "digest": "0" * 32,
            "ids": {"toolu_a": "claude-not-this-one"}, "timeline": []}}}),
    ):
        cache.write_text(corrupt, encoding="utf-8")
        assert harvest.parse_log(log)[0].models == expected, corrupt


def test_a_cache_that_cannot_be_written_is_not_an_error(tmp_path):
    # Invariant 5: this runs inside a Stop hook, so an unwritable state dir is
    # a slower harvest and nothing else. A directory in the cache file's place
    # fails both the read and the atomic replace.
    cache = harvest._index_cache_path()
    paths.ensure_dir(cache)
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_a"),
    ])
    log = _write_log(tmp_path, [_hook("python3 -c 'print(1)'", "toolu_a", main)])
    assert harvest.parse_log(log)[0].models == (("claude-opus-5", 1),)
    assert harvest.parse_log(log)[0].models == (("claude-opus-5", 1),)
    assert cache.is_dir()  # nothing wrote over it, and nothing raised


def test_the_cache_forgets_transcripts_that_are_gone(tmp_path):
    # It is a cache of files that exist, not a record of every session that ever
    # ran here — otherwise nothing ever removes a line from it.
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_a"),
    ])
    log = _write_log(tmp_path, [_hook("python3 -c 'print(1)'", "toolu_a", main)])
    harvest.parse_log(log)
    stored = json.loads(harvest._index_cache_path().read_text(encoding="utf-8"))
    assert str(main) in stored["files"]

    main.unlink()
    other = _transcripts(tmp_path / "other", [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_c"),
    ])
    harvest.parse_log(_write_log(tmp_path, [_hook("python3 -c 'print(3)'", "toolu_c", other)]))
    stored = json.loads(harvest._index_cache_path().read_text(encoding="utf-8"))
    assert str(main) not in stored["files"]
    assert str(other) in stored["files"]


def test_a_truncated_transcript_is_re_read_rather_than_resumed(tmp_path):
    """Truncated in place, with more prefix left than the digest window covers.

    The three staleness signals are `(dev, ino)`, `size >= offset`, and a digest
    of the last `_CACHE_DIGEST_BYTES` consumed bytes. This is the case that
    needs at least two of them to have been thought about: same inode, and a
    surviving prefix LONGER than the digest window, so a digest of the head of
    that prefix — which is what this used to hash — would still match. Believing
    the cache here serves ids the file no longer contains, which is the silent
    wrong answer, not a slow one.
    """
    keep = [_assistant("2026-09-02T10:00:0%d.000Z" % i, "claude-opus-5", "toolu_a",
                       "python3 -c 'print(1)'   # %s" % ("p" * 900))
            for i in range(6)]
    rest = [_assistant("2026-09-02T10:01:00.000Z", "claude-fable-5-1", "toolu_b"),
            _assistant("2026-09-02T10:01:01.000Z", "claude-fable-5-1", "toolu_c")]
    main = _transcripts(tmp_path, keep + rest)
    surviving = len("".join(r + "\n" for r in keep).encode("utf-8"))
    assert surviving > harvest._CACHE_DIGEST_BYTES, surviving
    assert main.stat().st_size > surviving

    log = _write_log(tmp_path, [
        _hook("python3 -c 'print(1)'", "toolu_a", main),
        _hook("python3 -c 'print(2)'", "toolu_b", main),
        _hook("python3 -c 'print(3)'", "toolu_c", main),
    ])
    by_program = {s.program: s for s in harvest.parse_log(log)}
    assert by_program["print(2)"].models == (("claude-fable-5-1", 1),)

    # Truncate in place: same path, same inode, and `toolu_b`/`toolu_c` are gone.
    with open(str(main), "r+b") as fh:
        fh.truncate(surviving)
    _forget_the_journal()
    by_program = {s.program: s for s in harvest.parse_log(log)}
    assert by_program["print(1)"].models == (("claude-opus-5", 1),)
    assert by_program["print(2)"].models == ()
    assert by_program["print(3)"].models == ()


def test_a_shrunk_file_is_stale_even_when_the_stored_digest_says_nothing(tmp_path):
    """The one shrink the digest cannot see, which is why the size check stays.

    `_tail_digest` returns `""` when it cannot read its window, and an entry
    carrying `""` beside a non-zero offset is a shape `_cache_entry_ok` accepts
    — from an older writer, a transient read failure, or anyone with an editor,
    since this file is on disk and outlives the version that wrote it. Then the
    digests compare equal whatever the file now holds, and `st.st_size <
    entry["offset"]` is the only signal left that the bytes the offset claims
    are not there.
    """
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_a"),
        _assistant("2026-09-02T10:00:01.000Z", "claude-opus-5", "toolu_b"),
    ])
    log = _write_log(tmp_path, [_hook("python3 -c 'print(1)'", "toolu_b", main)])
    assert harvest.parse_log(log)[0].models == (("claude-opus-5", 1),)

    st = os.stat(str(main))
    cache = harvest._index_cache_path()
    cache.write_text(json.dumps({"version": harvest._CACHE_VERSION, "files": {str(main): {
        "offset": st.st_size, "ino": st.st_ino, "dev": st.st_dev, "digest": "",
        "ids": {"toolu_b": "claude-opus-5"}, "timeline": []}}}), encoding="utf-8")
    with open(str(main), "w", encoding="utf-8") as fh:
        fh.write(_assistant("2026-09-02T11:00:00.000Z", "claude-fable-5-1", "toolu_z") + "\n")
    # `toolu_b` is not in this file any more. The cache still claims it.
    _forget_the_journal()
    assert harvest.parse_log(log)[0].models == ()


def test_the_time_join_includes_the_instant_it_was_asked_about(tmp_path):
    """"At or before", not "strictly before" — and the tie is the likely case.

    The shim's stamp is second-precision on this host, so a spawn that happens
    in the same second an assistant record was written lands on exactly its
    canonical key. `bisect_right(timeline, (key, high)) - 1` includes that
    record; `bisect_left(timeline, (key, "")) - 1` would skip it and hand back
    whoever spoke before — a different model, silently, on the commonest shape
    of tie there is.
    """
    at = "2026-09-02T01:05:24.000000"
    index = harvest._ModelIndex({}, [("2026-09-02T01:05:20.100000", "claude-sonnet-5"),
                                     (at, "claude-fable-5-1")])
    assert index.at("2026-09-02T01:05:24Z") == "claude-fable-5-1"
    assert index.at("2026-09-02T01:05:24.000Z") == "claude-fable-5-1"
    # And one instant earlier is still the earlier model, so this is a boundary
    # and not a test that any answer would satisfy.
    assert index.at("2026-09-02T01:05:23.999Z") == "claude-sonnet-5"
    assert index.at("2026-09-02T01:05:20.000Z") is None

    # The same tie through the whole pipeline: the transcript writes the model
    # at :24.000 and the shim spawns in that second.
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T01:05:20.100Z", "claude-sonnet-5", "toolu_main"),
        _assistant("2026-09-02T01:05:24.000Z", "claude-fable-5-1"),
    ])
    log = _write_log(tmp_path, [
        _hook("python3 -c 'print(1)'", "toolu_main", main),
        {"kind": "python_invocation", "session": SESSION,
         "ts": "2026-09-02T01:05:24Z", "program": UNIQUE, "argv_tail": []},
    ])
    by_program = {s.program: s for s in harvest.parse_log(log)}
    assert by_program[UNIQUE].models == (("claude-fable-5-1", 1),)


# --- one bad record must not cost every session its export --------------------


def test_one_poisoned_transcript_path_does_not_stop_the_export(project, tmp_path):
    """`parse_log` says it never raises, and the Stop hook is why it must not.

    A `transcript` string comes out of the log — some other program wrote it,
    this one only read it back — and `os.walk` on one holding a NUL raises
    ValueError, not OSError. Unguarded, one such line stops the export of EVERY
    session, on every Stop, silently (the hook still prints its contract line
    and exits 0), and forever, because the log is append-only.
    """
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_ok"),
    ])
    _log([
        {"kind": "bash_command", "session": "poisoned", "ts": "2026-09-02T10:00:00.500Z",
         "command": "python3 -c %r" % UNIQUE_HEREDOC, "transcript": "/tmp/tr\x00ans.jsonl",
         "tool_use_id": "toolu_bad"},
        {"kind": "bash_command", "session": SESSION, "ts": "2026-09-02T10:00:01.500Z",
         "command": "python3 -c %r" % UNIQUE, "transcript": str(main),
         "tool_use_id": "toolu_ok"},
    ])
    result = harvest._export()
    published = {}
    for path, _added, _total, _changed in result.files:
        for s in harvest.read_sightings(path):
            published[s.program] = s
    # The good record is published AND attributed...
    assert published[UNIQUE].models == (("claude-opus-5", 1),)
    # ...and the poisoned one costs itself a model and nothing else.
    assert published[UNIQUE_HEREDOC].models == ()


def test_a_lone_surrogate_in_a_transcript_path_is_the_same_non_event(tmp_path):
    """The other exception `os.walk` raises for a string this module did not
    choose: UnicodeEncodeError, a subclass of ValueError, out of the filesystem
    encoder.

    ``U+D800``, not one of the ``U+DC80``-``U+DCFF`` surrogates: those are what
    `surrogateescape` produces for undecodable BYTES and they encode straight
    back, so a path built from one raises nothing at all. Only a surrogate with
    no byte behind it does — asserted here rather than assumed, because a test
    aimed at the wrong half of the range is a test that passes on any code.
    """
    bad = "/tmp/\ud800/x.jsonl"
    with pytest.raises(UnicodeEncodeError):
        list(os.walk(bad))
    log = _write_log(tmp_path, [
        {"kind": "bash_command", "session": SESSION, "ts": "2026-09-02T10:00:00.500Z",
         "command": "python3 -c 'print(1)'", "transcript": bad,
         "tool_use_id": "toolu_bad"},
    ])
    assert harvest.parse_log(log)[0].models == ()


# --- the other harnesses are unattributed, and cost the join nothing ----------
#
# opencode's tool hooks (`tool.execute.before/after`, `shell.env`) carry
# sessionID and callID and no model; OpenHands' PostToolUse payload carries
# `session_id`, `tool_name`, `tool_input`, `tool_response` and no model, and its
# hook subprocess env names no model either. Neither has a transcript on disk to
# join against. So both are unattributed by construction, which is exactly what
# the hole in `models` is for — and what must not quietly become a WRONG model
# the day one of them starts writing a field this module reads.


def _opencode_bash(command, run="call_1", ts="2026-09-02T10:00:00.500Z"):
    return {"kind": "bash_command", "ts": ts, "session": "ses_opencode_1",
            "cwd": "/tmp/p", "tool": "bash", "command": command,
            "description": None, "transcript": None, "host": "opencode",
            "run": run}


def _openhands_bash(command, ts="2026-09-02T10:00:00.500Z"):
    return {"kind": "bash_command", "ts": ts, "session": "oh-session-1",
            "cwd": "/tmp/p", "tool": "terminal", "command": command,
            "description": None, "transcript": None, "host": "openhands",
            "exit_code": 0}


def test_an_opencode_or_openhands_record_is_unattributed_and_never_wrong(tmp_path):
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_claude",
                   "python3 -c 'print(0)'"),
    ])
    log = _write_log(tmp_path, [
        _hook("python3 -c 'print(0)'", "toolu_claude", main),
        _opencode_bash("python3 -c 'print(1)'"),
        _openhands_bash("python3 -c 'print(2)'"),
    ])
    by_program = {s.program: s for s in harvest.parse_log(log)}
    assert by_program["print(0)"].models == (("claude-opus-5", 1),)
    assert by_program["print(1)"].models == ()
    assert by_program["print(2)"].models == ()


def test_a_non_claude_record_naming_a_transcript_is_still_not_joined(tmp_path):
    """Neither harness writes `transcript` today. This is the guard for the day
    one does: its path must not be walked as a Claude transcript, and its ids
    must not be looked up in one."""
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_x",
                   "python3 -c 'print(1)'"),
    ])
    rec = _opencode_bash("python3 -c 'print(1)'")
    rec["transcript"] = str(main)
    rec["tool_use_id"] = "toolu_x"          # the same id, in another namespace
    log = _write_log(tmp_path, [rec])
    assert harvest.parse_log(log)[0].models == ()

    # And a shim spawn under that session cannot borrow the path either.
    log = _write_log(tmp_path, [
        rec,
        {"kind": "python_invocation", "session": rec["session"],
         "ts": "2026-09-02T10:00:01Z", "program": UNIQUE, "argv_tail": []},
    ])
    by_program = {s.program: s for s in harvest.parse_log(log)}
    assert by_program[UNIQUE].models == ()


def test_the_records_that_carry_no_program_read_no_transcript(tmp_path, monkeypatch):
    """opencode's `{"kind":"note"}` and `{"kind":"exit"}` records, and the
    opt-in `{"kind":"tool_call"}`, flow through the same prepass the join is
    built in. None of them names a program, none should reach the index, and
    none should make the harvest open a transcript."""
    log = _write_log(tmp_path, [
        {"kind": "note", "ts": "2026-09-02T10:00:00.000Z", "session": "ses_1",
         "host": "opencode", "detail": "shim not on PATH"},
        {"kind": "exit", "ts": "2026-09-02T10:00:01.000Z", "session": "ses_1",
         "host": "opencode", "run": "call_1", "exit_code": 0},
        {"kind": "tool_call", "ts": "2026-09-02T10:00:02.000Z", "session": "ses_1",
         "host": "opencode", "tool": "bash"},
    ])
    indexed = []
    monkeypatch.setattr(harvest, "_model_index",
                        lambda t, cache=None: indexed.append(t) or harvest._EMPTY_INDEX)
    assert harvest.parse_log(log) == []
    assert indexed == []
    assert not harvest._index_cache_path().exists()


# --- --dry-run is real: it opens files and writes none ------------------------


def test_a_dry_run_harvest_writes_nothing_at_all(tmp_path):
    """Invariant 7. The index cache is the only write on the collect path, and
    it is under $LYPNING_HOME — where a --dry-run must leave no trace."""
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_a",
                   "python3 -c 'print(1)'"),
    ])
    _log([_hook("python3 -c 'print(1)'", "toolu_a", main)])
    state = paths.state_dir()
    before = sorted(p.name for p in state.iterdir()) if state.is_dir() else []

    dry = harvest.collect(persist=False)
    assert [s.models for s in dry] == [(("claude-opus-5", 1),)]
    after = sorted(p.name for p in state.iterdir()) if state.is_dir() else []
    assert after == before
    assert not harvest._index_cache_path().exists()

    # And the same call that is allowed to write does, so this is a test of the
    # flag rather than of a join that never happened.
    harvest.collect()
    assert harvest._index_cache_path().is_file()


def test_a_rewrite_that_keeps_the_opening_bytes_is_still_noticed(tmp_path):
    """Same inode, same first bytes, no shorter — the shape a `cp` over the path,
    an `rsync --inplace` or a restored backup actually has.

    This is why the digest covers the TAIL of the consumed prefix and not its
    head. A transcript opens with the same session and the same first turns
    however many times it is rewritten, so a head digest is the one window such
    a rewrite is most likely to reproduce exactly; move the window back to the
    head and this test is the one that goes red.
    """
    opening = [_assistant("2026-09-02T10:00:0%d.000Z" % (i % 10), "claude-opus-5",
                          "toolu_h%d" % i, "python3 -c 'print(0)'  # %s" % ("q" * 400))
               for i in range(14)]
    shared = "".join(r + "\n" for r in opening)
    assert len(shared.encode("utf-8")) > harvest._CACHE_DIGEST_BYTES

    main = _transcripts(tmp_path, opening + [
        _assistant("2026-09-02T10:05:00.000Z", "claude-opus-5", "toolu_b",
                   "python3 -c 'print(1)'"),
    ])
    log = _write_log(tmp_path, [_hook("python3 -c 'print(1)'", "toolu_b", main)])
    assert harvest.parse_log(log)[0].models == (("claude-opus-5", 1),)
    st = os.stat(str(main))

    # Rewritten in place: byte-identical opening, and `toolu_b` gone from the
    # part a head digest never looks at. Longer, so the size check is silent.
    with open(str(main), "w", encoding="utf-8") as fh:
        fh.write(shared)
        for i in range(3):
            fh.write(_assistant("2026-09-02T11:00:0%d.000Z" % i, "claude-fable-5-1",
                                "toolu_z%d" % i) + "\n")
    after = os.stat(str(main))
    assert after.st_ino == st.st_ino and after.st_size >= st.st_size
    assert main.read_text(encoding="utf-8").startswith(shared)
    _forget_the_journal()
    assert harvest.parse_log(log)[0].models == ()


# --- the attribution journal: what survives the transcript ---------------------
#
# Claude Code deletes transcripts on its own schedule, and the index cache
# prunes what is gone. The journal is where a resolved id's model and outcome
# are kept instead, append-only and never pruned.


def _result(tool_use_id, is_error=False, interrupted=None, ts="2026-09-02T10:00:01.000Z"):
    """A user record carrying one tool_result, in the CLI's own shape."""
    rec = {"type": "user", "timestamp": ts, "sessionId": SESSION,
           "message": {"role": "user", "content": [
               {"type": "tool_result", "tool_use_id": tool_use_id, "content": "x",
                "is_error": is_error}]}}
    if interrupted is not None:
        rec["toolUseResult"] = {"stdout": "", "stderr": "", "interrupted": interrupted}
    return json.dumps(rec)


def _journal_lines():
    path = harvest.attribution_path()
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_the_journal_lives_under_the_state_dir():
    # Redirected by LYPNING_HOME, like the log and the cache: a test, or a
    # worktree with its own state, never appends to the developer's journal.
    assert harvest.attribution_path() == paths.state_dir() / "attribution.jsonl"


def test_a_model_survives_the_transcript_being_deleted(tmp_path):
    """The deadline this exists for. Resolve an id, delete the transcript AND
    the cache — what Claude Code's cleanup and the cache's own prune do between
    them — and re-harvest: the model is still there, from the journal."""
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_a"),
        _result("toolu_a"),
    ])
    log = _write_log(tmp_path, [_hook("python3 -c 'print(1)'", "toolu_a", main)])
    assert harvest.parse_log(log)[0].models == (("claude-opus-5", 1),)

    main.unlink()
    harvest._index_cache_path().unlink()
    s = harvest.parse_log(log)[0]
    assert s.models == (("claude-opus-5", 1),)
    assert s.outcomes == (("ok", 1),)
    assert _journal_lines() == [{"tool_use_id": "toolu_a", "model": "claude-opus-5",
                                 "is_error": False, "interrupted": False, "session": SESSION}]
    # No command text, ever: the journal is not a second copy of the log.
    assert "print" not in harvest.attribution_path().read_text(encoding="utf-8")


def test_a_journaled_id_reads_no_transcript(tmp_path, monkeypatch):
    # An id the journal answers in full asks the transcript nothing, which is
    # what makes an expired transcript cost a stat rather than a re-read.
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_a"),
        _result("toolu_a"),
    ])
    log = _write_log(tmp_path, [_hook("python3 -c 'print(1)'", "toolu_a", main)])
    harvest.parse_log(log)
    indexed = []
    monkeypatch.setattr(harvest, "_model_index",
                        lambda t, cache=None: indexed.append(t) or harvest._EMPTY_INDEX)
    assert harvest.parse_log(log)[0].models == (("claude-opus-5", 1),)
    assert indexed == []


def test_a_second_harvest_appends_nothing_to_the_journal(tmp_path):
    # It grows by what was learned, not by how often anyone asked: the Stop
    # hook runs this at every turn boundary.
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_a"),
    ])
    log = _write_log(tmp_path, [_hook("python3 -c 'print(1)'", "toolu_a", main)])
    harvest.parse_log(log)
    first = harvest.attribution_path().read_bytes()
    harvest.parse_log(log)
    assert harvest.attribution_path().read_bytes() == first

    # And an outcome that arrives later is one more line, not a rewrite.
    _append(main, _result("toolu_a", is_error=True) + "\n")
    assert harvest.parse_log(log)[0].outcomes == (("error", 1),)
    lines = _journal_lines()
    assert len(lines) == 2 and lines[1]["is_error"] is True
    assert harvest.attribution_path().read_bytes().startswith(first)


def test_a_torn_journal_line_is_skipped_and_the_rest_still_answers(tmp_path):
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_a"),
        _result("toolu_a"),
    ])
    log = _write_log(tmp_path, [_hook("python3 -c 'print(1)'", "toolu_a", main)])
    harvest.parse_log(log)
    _append(harvest.attribution_path(), '{"tool_use_id": "toolu_z", "mod')
    main.unlink()
    assert harvest.parse_log(log)[0].models == (("claude-opus-5", 1),)


def test_a_dry_run_leaves_the_journal_unchanged(tmp_path):
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_a"),
        _result("toolu_a"),
    ])
    _log([_hook("python3 -c 'print(1)'", "toolu_a", main)])
    dry = harvest.collect(persist=False)
    assert [s.models for s in dry] == [(("claude-opus-5", 1),)]
    assert not harvest.attribution_path().exists()

    # Nor is an existing journal appended to, by a dry run that has something
    # new to say.
    harvest.collect()
    before = harvest.attribution_path().read_bytes()
    _append(main, _assistant("2026-09-02T10:00:05.000Z", "claude-fable-5-1", "toolu_b") + "\n")
    _log([_hook("python3 -c 'print(1)'", "toolu_a", main),
          _hook("python3 -c 'print(2)'", "toolu_b", main)])
    harvest.collect(persist=False)
    assert harvest.attribution_path().read_bytes() == before


def test_the_cli_dry_run_leaves_the_journal_unchanged(tmp_path, monkeypatch, capsys):
    from lypning import cli

    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_a"),
    ])
    _log([_hook("python3 -c 'print(1)'", "toolu_a", main)])
    monkeypatch.setenv("LYPNING_TRANSCRIPTS", str(main.parent))
    assert cli.main(["harvest", "--dry-run", "--transcripts", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["journal"]["would_add"] == 1
    assert not harvest.attribution_path().exists()


# --- outcomes -------------------------------------------------------------------


def test_a_tool_result_with_is_error_counts_as_an_error(tmp_path):
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_a"),
        _result("toolu_a", is_error=True),
        _assistant("2026-09-02T10:00:02.000Z", "claude-opus-5", "toolu_b"),
        _result("toolu_b", is_error=False),
        _assistant("2026-09-02T10:00:04.000Z", "claude-opus-5", "toolu_c"),
        _result("toolu_c", is_error=False, interrupted=True),
        _assistant("2026-09-02T10:00:06.000Z", "claude-opus-5", "toolu_d"),  # no result
    ])
    log = _write_log(tmp_path, [_hook("python3 -c 'print(1)'", tid, main)
                                for tid in ("toolu_a", "toolu_b", "toolu_c", "toolu_d")])
    s = harvest.parse_log(log)[0]
    assert s.count == 4
    # Interrupted is an error; no result at all is the hole, not an ok.
    assert s.outcomes == (("error", 2), ("ok", 1))
    assert s.to_obj()["outcomes"] == {"error": 2, "ok": 1}


def test_an_openhands_exit_code_is_an_outcome(tmp_path):
    log = _write_log(tmp_path, [
        {"kind": "bash_command", "session": "conv", "ts": "2026-09-02T10:00:00.000Z",
         "host": "openhands", "command": "python3 -c 'print(9)'", "exit_code": 1},
    ])
    s = harvest.parse_log(log)[0]
    assert s.outcomes == (("error", 1),)
    assert s.host == "openhands" and s.to_obj()["host"] == "openhands"


def test_old_sighting_files_still_load_and_keep_their_bytes(tmp_path):
    # Every line committed before `outcomes` and `host` existed must round-trip
    # byte for byte: the export rewrites these files at every turn boundary.
    line = ('{"key":"py-000000000001","id":"py-000000000001","program":"print(1)",'
            '"argv_tail":[],"source":"hook","session":"s","first_seen":"2026-09-01T00:00:00Z",'
            '"count":3,"stdin_sample":null,"models":{"claude-opus-5":2}}\n')
    path = tmp_path / "old.jsonl"
    path.write_text(line, encoding="utf-8")
    [s] = harvest.read_sightings(path)
    assert s.outcomes == () and s.host is None
    assert harvest.serialise([s]) == line


def test_outcomes_merge_with_max_and_never_out_count_the_count():
    a = harvest.Sighting(key="py-x", program="p", count=2, outcomes=(("ok", 2),))
    b = harvest.Sighting(key="py-x", program="p", count=2, outcomes=(("error", 1),))
    merged = harvest._combine(a, b)
    assert merged.outcomes == (("error", 1), ("ok", 2))
    assert merged.count == 3
    assert harvest._combine(merged, merged) == merged
    # An unknown bucket is dropped on read rather than counted under a name.
    s = harvest.Sighting.from_obj({"program": "p", "outcomes": {"ok": 1, "weird": 4}})
    assert s.outcomes == (("ok", 1),)


def test_a_fold_merges_outcomes_and_host_through_the_corpus_extras(tmp_path):
    target = tmp_path / "corpus.jsonl"
    prog = "print('fold outcome fixture 4f2a')"
    key = harvest.sighting_key(prog)
    harvest.fold_into_corpus([harvest.Sighting(key=key, program=prog, count=1,
                                               outcomes=(("ok", 1),), host="codex")], target)
    [e] = corpus.load(target)
    assert e.extra["outcomes"] == {"ok": 1} and e.extra["host"] == "codex"
    harvest.fold_into_corpus([harvest.Sighting(key=key, program=prog, count=2,
                                               outcomes=(("error", 2),))], target)
    [e] = corpus.load(target)
    assert e.extra["outcomes"] == {"error": 2, "ok": 1}
    assert e.count == 3
    assert "host" not in e.extra  # a Claude sighting made it a mix


# --- records that predate tool_use_id: the per-block join ------------------------


def test_a_record_without_an_id_joins_by_block_through_the_journal(tmp_path):
    command = "python3 -c 'print(\"pre-id fixture\")'"
    main = _transcripts(tmp_path, [
        json.dumps({"type": "assistant", "timestamp": "2026-09-02T10:00:00.000Z",
                    "sessionId": SESSION, "message": {"model": "claude-opus-5", "content": [
                        {"type": "tool_use", "id": "toolu_old", "name": "Bash",
                         "input": {"command": command}}]}}),
        _result("toolu_old", is_error=True),
    ])
    assert harvest.transcript_blocks(main.parent) == [{
        "tool_use_id": "toolu_old", "session": SESSION, "ts": "2026-09-02T10:00:00.000Z",
        "model": "claude-opus-5", "command_sha256": harvest._command_sha256(command),
        "is_error": True, "interrupted": False}]

    assert harvest.journal_transcripts(main.parent, persist=False) == 1
    assert not harvest.attribution_path().exists()
    assert harvest.journal_transcripts(main.parent) == 1
    assert harvest.journal_transcripts(main.parent) == 0  # idempotent
    main.unlink()

    rec = {"kind": "bash_command", "session": SESSION, "command": command,
           "ts": "2026-09-02T10:00:00.500Z"}
    assert harvest.attribution_for(rec) == {"model": "claude-opus-5", "via": "command"}
    # Another session's identical command is another call.
    assert harvest.attribution_for(dict(rec, session="other")) == {}
    # A record with an id joins on the id.
    got = harvest.attribution_for(dict(rec, tool_use_id="toolu_old"))
    assert got["model"] == "claude-opus-5" and got["via"] == "tool_use_id"
    # And never for a harness whose ids are not Claude's.
    assert harvest.attribution_for(dict(rec, host="codex")) == {}


def test_a_command_two_models_issued_in_one_session_answers_nothing(tmp_path):
    command = "python3 -c 'print(7)'"
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_1", command),
        _assistant("2026-09-02T10:00:09.000Z", "claude-fable-5-1", "toolu_2", command),
    ])
    harvest.journal_transcripts(main.parent)
    rec = {"kind": "bash_command", "session": SESSION, "command": command}
    assert harvest.attribution_for(rec) == {}


# --- one call, one occurrence, however many hooks saw it ------------------------


def test_the_same_event_logged_twice_counts_once(tmp_path):
    """Project scope and user scope both installed: two hook commands, both
    fire, two log lines for one Bash call. Keyed by id, one occurrence."""
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_a"),
    ])
    rec = _hook("python3 -c 'print(1)'", "toolu_a", main)
    log = _write_log(tmp_path, [rec, rec, _hook("python3 -c 'print(1)'", "toolu_b", main)])
    s = harvest.parse_log(log)[0]
    assert s.count == 2
    assert s.models == (("claude-opus-5", 1),)


def test_a_published_count_under_the_old_line_keys_does_not_shrink_or_grow(project, tmp_path):
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_a"),
    ])
    program = 'print("dedup fixture 4f2a")'
    rec = _hook("python3 -c '%s'" % program, "toolu_a", main)
    _log([rec, rec])
    path = paths.sightings_dir(project) / (SESSION + ".jsonl")
    paths.ensure_dir(path.parent)
    old = harvest.Sighting(key=harvest.sighting_key(program), program=program,
                           session=SESSION, count=2)
    path.write_text(harvest.serialise([old]), encoding="utf-8")
    harvest.export_sightings(project, quiet=True)
    [s] = harvest.read_sightings(path)
    assert s.count == 2  # max(2 published, 1 derived): not 3, and not 1


# --- programs written to a file, then run ---------------------------------------


def _write_tool(ts, model, tool_use_id, file_path, content, name="Write"):
    inp = {"file_path": file_path, "content": content} if name == "Write" else {
        "file_path": file_path, "old_string": "a", "new_string": "b"}
    return json.dumps({"type": "assistant", "timestamp": ts, "sessionId": SESSION,
                       "message": {"model": model, "content": [
                           {"type": "tool_use", "id": tool_use_id, "name": name, "input": inp}]}})


BODY = "import sys\n\ndef main():\n    print('written then run 4f2a')\n\nmain()\n"


@pytest.mark.parametrize("header,delim", [
    ("cat > /tmp/a.py <<'EOF'", "EOF"),
    ("cat > /tmp/a.py <<EOF", "EOF"),
    ("cat <<'END' > /tmp/a.py", "END"),
    ("tee /tmp/a.py <<'X' >/dev/null", "X"),
])
def test_a_heredoc_into_a_py_file_is_extracted_whatever_the_delimiter(header, delim):
    command = "%s\n%s%s\n" % (header, BODY, delim)
    assert harvest.extract_from_command(command) == [BODY.rstrip("\n")]


@pytest.mark.parametrize("header", [
    "cat >> /tmp/a.py <<'EOF'",           # an append is a fragment
    "tee -a /tmp/a.py <<'EOF'",
    "cat > /tmp/a.txt <<'EOF'",           # not a .py
    "cat > \"$D/a.py\" <<'EOF'",          # a path nobody can know
    "cat 2>/tmp/a.py <<'EOF'",            # stderr, not the body
])
def test_a_heredoc_that_is_not_a_whole_py_file_is_not(header):
    command = "%s\nnot python at all, just text\nEOF\n" % header
    assert harvest.extract_from_command(command) == []


def test_a_heredoc_fed_to_python_does_not_name_its_output_file():
    # The body is the program; out.py receives its OUTPUT.
    assert harvest.py_write_target("python3 - > out.py <<'EOF'") is None
    assert harvest.py_write_target("cat > a.py <<'EOF' && python3 a.py") == ("write", "a.py")


def test_a_write_then_a_run_is_one_file_sighting_with_the_writers_model(tmp_path):
    main = _transcripts(tmp_path, [
        _write_tool("2026-09-02T10:00:00.000Z", "claude-opus-5-5", "toolu_w", "/tmp/a.py", BODY),
        _assistant("2026-09-02T10:00:05.000Z", "claude-opus-5", "toolu_r", "python3 /tmp/a.py 3 4"),
        _result("toolu_r", is_error=False, ts="2026-09-02T10:00:06.000Z"),
    ])
    log = _write_log(tmp_path, [
        dict(_hook("python3 /tmp/a.py 3 4", "toolu_r", main, "2026-09-02T10:00:05.500Z"), cwd="/w"),
    ])
    [s] = harvest.parse_log(log)
    assert s.program == BODY
    assert s.source == "file"
    assert s.argv_tail == ("3", "4")
    assert s.models == (("claude-opus-5-5", 1),)
    assert s.outcomes == (("ok", 1),)


def test_a_write_that_is_never_run_gives_nothing(tmp_path):
    main = _transcripts(tmp_path, [
        _write_tool("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_w", "/tmp/a.py", BODY),
        _assistant("2026-09-02T10:00:05.000Z", "claude-opus-5", "toolu_r", "python3 -c 'print(1)'"),
    ])
    log = _write_log(tmp_path, [_hook("python3 -c 'print(1)'", "toolu_r", main)])
    assert [s.program for s in harvest.parse_log(log)] == ["print(1)"]
    # And a run of a DIFFERENT path joins nothing either.
    log = _write_log(tmp_path, [_hook("python3 /tmp/b.py", "toolu_r", main)])
    assert harvest.parse_log(log) == []


def test_a_write_after_the_run_is_not_what_ran(tmp_path):
    main = _transcripts(tmp_path, [
        _write_tool("2026-09-02T10:00:09.000Z", "claude-opus-5", "toolu_w", "/tmp/a.py", BODY),
    ])
    log = _write_log(tmp_path, [_hook("python3 /tmp/a.py", "toolu_r", main,
                                      "2026-09-02T10:00:05.000Z")])
    assert harvest.parse_log(log) == []


def test_an_edit_between_the_write_and_the_run_joins_nothing(tmp_path):
    # Edits are not replayed, so the text that ran is a text nobody has.
    main = _transcripts(tmp_path, [
        _write_tool("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_w", "/tmp/a.py", BODY),
        _write_tool("2026-09-02T10:00:02.000Z", "claude-opus-5", "toolu_e", "/tmp/a.py", "",
                    name="Edit"),
    ])
    log = _write_log(tmp_path, [_hook("python3 /tmp/a.py", "toolu_r", main,
                                      "2026-09-02T10:00:05.000Z")])
    assert harvest.parse_log(log) == []


def test_a_heredoc_then_a_relative_run_joins_through_cd(tmp_path):
    """The log alone: `cat > …/a.py` in one call, `cd` then `uv run python
    a.py` in the next. One program, run once: the run is the occurrence, and
    the heredoc that only WROTE the file does not count as a second run."""
    log = _write_log(tmp_path, [
        {"kind": "bash_command", "session": "s1", "ts": "2026-09-02T10:00:00.000Z",
         "cwd": "/w/proj", "command": "cat > /w/proj/tool/a.py <<'EOF'\n%sEOF" % BODY},
        {"kind": "bash_command", "session": "s1", "ts": "2026-09-02T10:00:03.000Z",
         "cwd": "/w/proj", "command": "cd tool && uv run python a.py --n 3"},
        # Another session's run of the same path is another session's file.
        {"kind": "bash_command", "session": "s2", "ts": "2026-09-02T10:00:04.000Z",
         "cwd": "/w/proj/tool", "command": "python3 a.py"},
    ])
    [s] = harvest.parse_log(log)
    assert s.program == BODY.rstrip("\n")
    assert s.count == 1 and s.source == "file"
    assert s.argv_tail == ("--n", "3")


def test_an_append_after_the_heredoc_joins_nothing(tmp_path):
    log = _write_log(tmp_path, [
        {"kind": "bash_command", "session": "s1", "ts": "2026-09-02T10:00:00.000Z",
         "cwd": "/w", "command": "cat > a.py <<'EOF'\n%sEOF" % BODY},
        {"kind": "bash_command", "session": "s1", "ts": "2026-09-02T10:00:01.000Z",
         "cwd": "/w", "command": "cat >> a.py <<'EOF'\nmain()\nEOF\npython3 a.py"},
    ])
    [s] = harvest.parse_log(log)
    assert s.count == 1 and s.source == "hook"  # the heredoc only; the run joined nothing


def test_a_write_and_its_run_in_one_command(tmp_path):
    log = _write_log(tmp_path, [
        {"kind": "bash_command", "session": "s1", "ts": "2026-09-02T10:00:00.000Z",
         "cwd": "/w", "command": "cat > a.py <<'EOF' && python3 a.py\n%sEOF" % BODY},
    ])
    [s] = harvest.parse_log(log)
    assert s.count == 1
    assert sorted(r[3] for r in harvest._raws_from_log(log.read_text())) == ["file"]


def test_the_cache_keeps_write_positions_and_reads_the_body_only_on_demand(tmp_path):
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T09:59:00.000Z", "claude-opus-5", "toolu_x",
                   "python3 -c 'print(\"éé\")'"),
        _write_tool("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_w", "/tmp/a.py", BODY),
    ])
    # A raw non-ASCII line before the write, so a character offset and a byte
    # offset differ.
    text = main.read_text(encoding="utf-8").replace("\\u00e9", "é")
    main.write_text(text, encoding="utf-8")
    log = _write_log(tmp_path, [_hook("python3 /tmp/a.py", "toolu_r", main,
                                      "2026-09-02T10:00:05.000Z")])
    assert [s.program for s in harvest.parse_log(log)] == [BODY]
    cached = harvest._index_cache_path().read_text(encoding="utf-8")
    [w] = json.loads(cached)["files"][str(main)]["writes"]
    assert w[1:3] == ["write", "/tmp/a.py"]
    # A position, not a copy of the file: no body sits in the cache.
    assert "written then run" not in cached
    assert harvest._write_body(str(main), w[3], "toolu_w") == BODY
    assert harvest._write_body(str(main), w[3] + 1, "toolu_w") is None


# --- the Codex feed ---------------------------------------------------------------


class _FakeCodex:
    def __init__(self, records):
        self.records = records

    def log_records(self, sessions_dir=None):
        return iter(self.records)


def _codex_rec(command, call_id, model="gpt-5.9-codex", session="rollout-1"):
    return {"kind": "bash_command", "session": session, "ts": "2026-09-02T10:00:00.000Z",
            "cwd": "/w", "command": command, "host": "codex", "model": model,
            "call_id": call_id}


def test_codex_records_are_tagged_and_keyed_by_call_id(monkeypatch):
    feed = _FakeCodex([
        _codex_rec("python3 -c 'print(\"codex 4f2a\")'", "call_1"),
        _codex_rec("python3 -c 'print(\"codex 4f2a\")'", "call_1"),  # a rescan
        _codex_rec("python3 -c 'print(\"codex 4f2a\")'", "call_2"),
    ])
    monkeypatch.setattr(harvest, "_codex_module", lambda: feed)
    [s] = harvest.parse_codex()
    assert s.count == 2
    assert s.source == "codex" and s.host == "codex"
    assert s.models == (("gpt-5.9-codex", 2),)
    # Never journaled as, or joined against, a Claude attribution.
    assert not harvest.attribution_path().exists()


def test_a_codex_record_that_forgot_its_host_is_still_not_claude(monkeypatch, tmp_path):
    main = _transcripts(tmp_path, [
        _assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_a"),
    ])
    rec = _codex_rec("python3 -c 'print(1)'", "call_1")
    rec.pop("host")
    rec.update(transcript=str(main), tool_use_id="toolu_a")
    monkeypatch.setattr(harvest, "_codex_module", lambda: _FakeCodex([rec]))
    [s] = harvest.parse_codex()
    assert s.models == (("gpt-5.9-codex", 1),) and s.host == "codex"


def test_codex_feed_absent_is_reported_not_empty(monkeypatch, capsys):
    from lypning import cli

    monkeypatch.setattr(harvest, "_codex_module", lambda: None)
    assert not harvest.codex_available()
    with pytest.raises(harvest.FeedUnavailable):
        harvest.parse_codex()
    assert cli.main(["harvest", "--dry-run", "--codex"]) == 1
    err = capsys.readouterr().err
    assert "--codex" in err and "not in this build" in err


def test_collect_with_codex_merges_the_feed(monkeypatch):
    monkeypatch.setattr(harvest, "_codex_module", lambda: _FakeCodex([
        _codex_rec("python3 -c 'print(\"codex merge 4f2a\")'", "call_9")]))
    assert [s.host for s in harvest.collect(persist=False, codex=True)] == ["codex"]


def test_the_real_codex_module_satisfies_the_feed_interface(tmp_path):
    """TODO(capture lane L1): `lypning.codex` is written in another lane. Until
    it lands this is skipped; once it does, it pins the interface this module
    codes against — `log_records(sessions_dir=None)` yielding log-shaped records."""
    pytest.importorskip("lypning.codex")
    assert harvest.codex_available()
    empty = tmp_path / "sessions"
    empty.mkdir()
    assert harvest.parse_codex(sessions_dir=empty) == []


# --- review: a write is not a run; a run is in command position; edits count ---


def _heredoc_rec(path, ts, session="s1", cwd="/w", extra=""):
    return {"kind": "bash_command", "session": session, "ts": ts, "cwd": cwd,
            "command": "cat > %s <<'EOF'%s\n%sEOF" % (path, extra, BODY)}


def _cmd_rec(command, ts, session="s1", cwd="/w"):
    return {"kind": "bash_command", "session": session, "ts": ts, "cwd": cwd, "command": command}


def test_count_is_runs_not_writes(tmp_path):
    """corpus.Stats: `count` says how often a program RAN. A heredoc run twice
    is two; the heredoc itself is not a third. One nothing ran is still one
    sighting (the session wrote it), and a second one is never double-joined."""
    log = _write_log(tmp_path, [
        _heredoc_rec("/w/a.py", "2026-09-02T10:00:00.000Z"),
        _cmd_rec("python3 /w/a.py 1", "2026-09-02T10:00:01.000Z"),
        _cmd_rec("python3 /w/a.py 2", "2026-09-02T10:00:02.000Z"),
    ])
    [s] = harvest.parse_log(log)
    assert s.count == 2 and s.source == "file"
    log = _write_log(tmp_path, [_heredoc_rec("/w/a.py", "2026-09-02T10:00:00.000Z")])
    [s] = harvest.parse_log(log)
    assert s.count == 1 and s.source == "hook"


def test_the_same_heredoc_and_run_logged_by_two_hooks_count_once(tmp_path):
    main = _transcripts(tmp_path, [_assistant("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_a")])
    rec = _hook("cat > /w/a.py <<'EOF' && python3 /w/a.py\n%sEOF" % BODY, "toolu_a", main)
    log = _write_log(tmp_path, [rec, rec])
    [s] = harvest.parse_log(log)
    assert s.count == 1


@pytest.mark.parametrize("command", [
    "grep -n python3 /w/a.py",
    "echo python3 /w/a.py",
    "git log -- python /w/a.py",
    "rg 'uv run' /w/a.py",
])
def test_a_python_word_that_is_an_argument_runs_nothing(tmp_path, command):
    log = _write_log(tmp_path, [
        _heredoc_rec("/w/a.py", "2026-09-02T10:00:00.000Z"),
        _cmd_rec(command, "2026-09-02T10:00:01.000Z"),
    ])
    [s] = harvest.parse_log(log)
    assert s.source == "hook" and s.count == 1  # the heredoc alone; no run joined
    assert [e for e in harvest._file_events(command, "/w") if e[0] == "run"] == []


@pytest.mark.parametrize("command,tail", [
    ("timeout 60 python3 /w/a.py x", ["x"]),
    ("env PYTHONHASHSEED=0 python3 -u /w/a.py", []),
    ("A=1 python3 a.py 3", ["3"]),
    ("sudo python3 /w/a.py", []),
    ("env -u VIRTUAL_ENV python3 /w/a.py", []),
    ("for i in 1 2; do python3 a.py $i; done", ["$i"]),
    ("if true; then X=1 python3 a.py; fi", []),
    ("uv run --no-project --with rich --python 3.14 python /w/a.py 5", ["5"]),
    ("uv run -q --with pytest python -u a.py", []),
    ("timeout 60 uv run --python 3.12 /w/a.py", []),
])
def test_a_wrapped_or_assigned_run_is_still_a_run(command, tail):
    assert harvest._file_events(command, "/w") == [("run", "/w/a.py", "", tail)]


@pytest.mark.parametrize("change", [
    "cp /w/b.py /w/a.py",
    "sed -i 's/x/y/' a.py",
    "perl -pi -e 's/x/y/' /w/a.py",
    "echo 'x = 1' >> a.py",
    "python3 gen.py > /w/a.py",
    "git checkout -- a.py",
    "printf x | tee /w/a.py",
])
def test_a_shell_change_between_the_heredoc_and_the_run_joins_nothing(tmp_path, change):
    """The text that ran is not the heredoc's any more, so it is not joined —
    and the outcome of that run is never filed under the stale body."""
    log = _write_log(tmp_path, [
        _heredoc_rec("/w/a.py", "2026-09-02T10:00:00.000Z"),
        _cmd_rec(change + " && python3 /w/a.py", "2026-09-02T10:00:01.000Z"),
    ])
    assert [s.source for s in harvest.parse_log(log)] == ["hook"]


def test_a_read_of_the_file_is_not_a_change(tmp_path):
    log = _write_log(tmp_path, [
        _heredoc_rec("/w/a.py", "2026-09-02T10:00:00.000Z"),
        _cmd_rec("sed -n 1,5p a.py; cat a.py; python3 a.py", "2026-09-02T10:00:01.000Z"),
    ])
    [s] = harvest.parse_log(log)
    assert s.source == "file" and s.count == 1


def _bash_block(ts, model, tool_use_id, command, cwd="/w"):
    return json.dumps({"type": "assistant", "timestamp": ts, "sessionId": SESSION, "cwd": cwd,
                       "message": {"model": model, "content": [
                           {"type": "tool_use", "id": tool_use_id, "name": "Bash",
                            "input": {"command": command}}]}})


def test_a_transcript_only_bash_change_after_a_write_joins_nothing(tmp_path):
    """The hook never logs `sed -i a.py` (it screens for python), but the
    transcript holds it: a Write, then that edit, then a run is not a run of
    the Write's body."""
    main = _transcripts(tmp_path, [
        _write_tool("2026-09-02T10:00:00.000Z", "claude-opus-5", "toolu_w", "/w/a.py", BODY),
        _bash_block("2026-09-02T10:00:02.000Z", "claude-opus-5", "toolu_s", "sed -i 's/4f2a/x/' a.py"),
    ])
    log = _write_log(tmp_path, [dict(_hook("python3 /w/a.py", "toolu_r", main,
                                           "2026-09-02T10:00:05.000Z"), cwd="/w")])
    assert harvest.parse_log(log) == []


def test_a_logged_heredoc_is_not_undone_by_its_own_transcript_copy(tmp_path):
    """The logged call is also in the transcript, as a Bash write of a.py. That
    copy must not count as an edit AFTER the log's own heredoc — it is the same
    call — even when its stamp is the later of the two."""
    cmd = "cat > a.py <<'EOF'\n%sEOF" % BODY
    main = _transcripts(tmp_path, [
        _bash_block("2026-09-02T10:00:01.000Z", "claude-opus-5", "toolu_h", cmd),
    ])
    log = _write_log(tmp_path, [
        dict(_hook(cmd, "toolu_h", main, "2026-09-02T10:00:00.500Z"), cwd="/w"),
        dict(_hook("python3 a.py", "toolu_r", main, "2026-09-02T10:00:05.000Z"), cwd="/w"),
    ])
    [s] = harvest.parse_log(log)
    assert s.source == "file" and s.count == 1
    assert s.models == (("claude-opus-5", 1),)
