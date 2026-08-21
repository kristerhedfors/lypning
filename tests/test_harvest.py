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


from lypning import harvest, paths

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


# --- what is not a program, and what is not evidence -------------------------
#
# Both rules below are enforced in `_why_unusable`, which is the gate BOTH the
# export and the fold ask. They live here rather than beside the import tests
# because neither one is about importing: a local capture produces both shapes,
# and did.


def test_an_unexpanded_shell_variable_is_not_a_program():
    # `python3 -c "$PROG"`, where the shell would have expanded `$PROG` and the
    # capture deliberately does not evaluate anything. What lands is the
    # reference, not the program — and `$P` is not even syntactically python.
    for text in ("$P", "$2", "${prog}", "$e"):
        assert harvest._why_uninteresting(text, set()) == "placeholder", text
    # A `$` that is part of a real program is untouched: only a program that is
    # NOTHING but the reference is refused.
    assert harvest._why_uninteresting("print('$P')", set()) == ""
    assert harvest._why_uninteresting("import re;re.match(r'^\\$\\w+$', s)", set()) == ""


def test_a_program_that_imports_this_package_is_not_evidence():
    # Every session that develops lypning types dozens of these and the hooks
    # capture them like anything else. The corpus is what `conformance --plan`
    # turns into a build order, so our own development traffic entering it
    # measures what we were doing last week rather than what agents type.
    assert harvest._why_uninteresting("import lypning", set()) == "self"
    assert harvest._why_uninteresting("from lypning import harvest", set()) == "self"
    assert harvest._why_uninteresting("  import lypning.collect as c", set()) == "self"
    # Mentioning the name is ordinary python — reading a path, grepping a file —
    # and stays. Only the import is refused.
    assert harvest._why_uninteresting("print(open('lypning.txt').read())", set()) == ""
    assert harvest._why_uninteresting("import json  # for lypning", set()) == ""


def test_the_fold_applies_the_content_gate_the_export_does(tmp_path):
    # The hole this closes: `_clean` runs on the export path only, so
    # `--transcripts` and every imported collection reached `fold_into_corpus`
    # without passing through it. Measured on this container's transcripts
    # before the fix, that let dozens of `import lypning` one-liners into the
    # corpus and made lypning its most-imported module.
    target = tmp_path / "corpus.jsonl"
    sightings = [
        harvest.Sighting(key=harvest.sighting_key(p), program=p)
        for p in ("import lypning", "$P", "pass", "", "print('kept')")
    ]
    added, total = harvest.fold_into_corpus(sightings, target)
    assert (added, total) == (1, 1)
    assert json.loads(target.read_text(encoding="utf-8").splitlines()[0])["program"] == "print('kept')"


def test_the_fold_still_merges_a_record_the_corpus_already_holds(tmp_path):
    # The identity test must NOT move into the fold with the content tests: a
    # program already in the corpus is exactly what a fold is merging, and
    # rejecting it as "known" would drop every count and timestamp update.
    target = tmp_path / "corpus.jsonl"
    program = "print('merged')"
    one = harvest.Sighting(key=harvest.sighting_key(program), program=program,
                           first_seen="2026-02-02T00:00:00.000Z", count=2)
    assert harvest.fold_into_corpus([one], target) == (1, 1)
    again = harvest.Sighting(key=harvest.sighting_key(program), program=program,
                             first_seen="2026-01-01T00:00:00.000Z", count=7)
    added, total = harvest.fold_into_corpus([again], target)
    assert (added, total) == (0, 1)
    rec = json.loads(target.read_text(encoding="utf-8").splitlines()[0])
    assert rec["count"] == 7 and rec["first_seen"] == "2026-01-01T00:00:00.000Z"
