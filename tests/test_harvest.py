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
                        lambda text: scanned.append(text) or real(text))
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
    ids that are no longer in it. The head digest is what notices.
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

    for corrupt in (
        "",                                             # a zero-length write
        "{not json",                                    # a half-written file
        json.dumps({"version": 999, "files": {}}),      # a schema we do not read
        json.dumps({"version": 1, "files": "nope"}),
        json.dumps({"version": 1, "files": {str(main): {"offset": "far"}}}),
        json.dumps({"version": 1, "files": {str(main): {
            "offset": 10, "ino": 1, "dev": 1, "head": "x",
            "ids": {"toolu_a": 3}, "timeline": []}}}),  # a model that is not a name
        json.dumps({"version": 1, "files": {str(main): {
            "offset": 10, "ino": 1, "dev": 1, "head": "x",
            "ids": {}, "timeline": [[1, "m"]]}}}),      # a stamp bisect cannot compare
        # Well-formed and STALE, which is the dangerous one: it names a model,
        # and believing it would be the silent wrong answer rather than a slow
        # one. The inode it claims is not this file's.
        json.dumps({"version": 1, "files": {str(main): {
            "offset": 10, "ino": 1, "dev": 1, "head": "x",
            "ids": {"toolu_a": "claude-not-this-one"}, "timeline": []}}}),
        # Well-formed, right inode, and the head of the file has moved: same
        # path, different bytes. Only re-reading can tell.
        json.dumps({"version": 1, "files": {str(main): {
            "offset": 10, "ino": os.stat(str(main)).st_ino,
            "dev": os.stat(str(main)).st_dev, "head": "0" * 32,
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
