"""The corpus as a review artifact: one normal form, one merge, one key.

The round-trip is the load-bearing test. The corpus grows by harvest and every
growth is read by a human in a diff; a rewrite that re-ordered keys or escaped
the non-ASCII third of the file would turn a three-line harvest into an
839-line diff that nobody reviews, and an unreviewed corpus is where a captured
credential lives forever.
"""

from __future__ import annotations

import json


from lypning import corpus, paths

# The id of the most-captured program in the shipped corpus. Pinned as a literal
# because the key is content-addressed: changing how it is derived silently
# re-files every record and makes the next harvest re-add the whole corpus.
PINNED_PROGRAM = "import sys;print(sys.argv)"
PINNED_ID = "py-00055c6cb527"


def test_program_id_is_stable():
    assert corpus.program_id(PINNED_PROGRAM) == PINNED_ID
    assert corpus.program_id("") == "py-" + "e3b0c44298fc"


def test_program_id_is_the_id_the_shipped_corpus_uses():
    entries = {e.id: e for e in corpus.load(paths.CORPUS_FILE)}
    assert PINNED_ID in entries
    assert entries[PINNED_ID].program.strip() == PINNED_PROGRAM


def test_shipped_corpus_rewrites_byte_for_byte(tmp_path):
    src = paths.CORPUS_FILE
    original = src.read_bytes()
    out = tmp_path / "corpus.jsonl"
    written = corpus.write(corpus.load(src), out)
    assert written == len(original.splitlines())
    assert out.read_bytes() == original


def test_seed_corpus_rewrites_byte_for_byte(tmp_path):
    # The seed file is hand-authored — slug ids, the `stdin` spelling of
    # `stdin_sample`, and a deliberate tiered line order — so the FIRST write
    # normalises it. What must hold is that the normal form is a fixed point and
    # that normalising loses nothing: every record survives with every field,
    # extras included, or the seed's `expect_stdout` vectors would evaporate.
    first = tmp_path / "seed1.jsonl"
    second = tmp_path / "seed2.jsonl"
    loaded = corpus.load(paths.SEED_CORPUS_FILE, default_source=corpus.SEED)
    corpus.write(loaded, first)
    reloaded = corpus.load(first, default_source=corpus.SEED)
    corpus.write(reloaded, second)

    assert second.read_bytes() == first.read_bytes()
    assert {e.id: e for e in reloaded} == {e.id: e for e in loaded}
    assert [e.extra for e in sorted(reloaded, key=lambda e: e.id)] == \
           [e.extra for e in sorted(loaded, key=lambda e: e.id)]


def test_write_keeps_non_ascii_unescaped(tmp_path):
    out = tmp_path / "c.jsonl"
    corpus.write([corpus.Entry(id="py-x", program="print('café ☕')")], out)
    text = out.read_text(encoding="utf-8")
    assert "café ☕" in text
    assert text.endswith("\n") and text.count("\n") == 1


def test_load_skips_a_half_written_last_line(tmp_path):
    p = tmp_path / "c.jsonl"
    p.write_text('{"id":"py-a","program":"print(1)"}\n{"id":"py-b","prog\n',
                 encoding="utf-8")
    # A shim killed mid-append is expected, not exceptional.
    assert [e.id for e in corpus.load(p)] == ["py-a"]


def _entry(**kw):
    base = dict(id="py-1", program="print(1)", source=corpus.SHIM, count=1, first_seen="")
    base.update(kw)
    return corpus.Entry(**base)


EARLY = "2026-01-01T00:00:00.000Z"
LATE = "2026-01-02T00:00:00.000Z"


def _objs(entries):
    return [e.to_obj() for e in entries]


def test_merge_sums_distinct_records_and_keeps_the_earliest():
    a = _entry(first_seen=LATE, count=2, source=corpus.SHIM)
    b = _entry(first_seen=EARLY, count=3, source="hook")
    merged = corpus.merge([a], [b])
    assert len(merged) == 1
    assert merged[0].count == 5
    assert merged[0].first_seen == EARLY
    assert merged[0].source == "hook"  # the earliest sighting supplies the rest


def test_merge_collapses_identical_records():
    # Two sessions exporting the same line is ONE sighting seen twice. Summing
    # it would double every count each time the harvest ran.
    a = _entry(first_seen=EARLY, count=4)
    assert corpus.merge([a], [a])[0].count == 4
    assert corpus.merge([a, a], [a])[0].count == 4


def test_merge_is_idempotent():
    group = [_entry(first_seen=EARLY, count=2),
             _entry(first_seen=LATE, count=3, source="hook"),
             _entry(id="py-2", program="print(2)", count=1)]
    once = corpus.merge(group)
    assert _objs(corpus.merge(once)) == _objs(once)
    assert _objs(corpus.merge(once, once)) == _objs(once)


def test_merge_is_commutative():
    a = _entry(first_seen=LATE, count=2)
    b = _entry(first_seen=EARLY, count=3, source="hook")
    c = _entry(id="py-2", program="print(2)", first_seen=EARLY, count=7)
    assert _objs(corpus.merge([a, b], [c])) == _objs(corpus.merge([c], [b, a]))
    assert _objs(corpus.merge([c, a], [b])) == _objs(corpus.merge([b], [a, c]))


def test_merge_sorts_by_id():
    ids = [e.id for e in corpus.merge([_entry(id="py-9"), _entry(id="py-1"), _entry(id="py-5")])]
    assert ids == sorted(ids)


def test_entry_carries_unknown_keys_through(tmp_path):
    p = tmp_path / "c.jsonl"
    p.write_text(json.dumps({"id": "py-a", "program": "pass", "tags": ["x"],
                             "expect_stdout": "\n"}) + "\n", encoding="utf-8")
    e = corpus.load(p)[0]
    assert e.extra == {"tags": ["x"], "expect_stdout": "\n"}
    assert e.to_obj()["tags"] == ["x"]


def test_stdin_and_key_aliases_are_consumed_not_carried(tmp_path):
    p = tmp_path / "c.jsonl"
    p.write_text(json.dumps({"key": "py-a", "program": "pass", "stdin": "in\n"}) + "\n",
                 encoding="utf-8")
    e = corpus.load(p)[0]
    assert e.id == "py-a"
    assert e.stdin_sample == "in\n"
    assert e.extra == {}


# --- a file that is not JSON must not load as silence -------------------------


def test_undecodable_lines_are_reported_when_asked(tmp_path):
    """A corrupted corpus loads as zero programs, and every count downstream is
    then quietly wrong. The skip is right; the silence is not."""
    p = tmp_path / "corpus.jsonl"
    p.write_text("not json\nnor this\n", encoding="utf-8")
    problems = []
    assert corpus.load(p, problems=problems) == []
    assert len(problems) == 1
    assert str(p) in problems[0] and "2 lines" in problems[0]


def test_a_truncated_last_line_is_still_skipped_silently_without_the_list(tmp_path):
    p = tmp_path / "corpus.jsonl"
    p.write_text('{"id":"a","program":"print(1)"}\n{"id":"b","prog', encoding="utf-8")
    entries = corpus.load(p)
    assert [e.id for e in entries] == ["a"]


def test_a_missing_file_is_not_a_problem(tmp_path):
    problems = []
    assert corpus.load(tmp_path / "absent.jsonl", problems=problems) == []
    assert problems == []


def test_an_unreadable_file_is_a_problem(tmp_path):
    d = tmp_path / "corpus.jsonl"
    d.mkdir()  # a directory where a file belongs: OSError on open, for any uid
    problems = []
    assert corpus.load(d, problems=problems) == []
    assert len(problems) == 1 and "not readable" in problems[0]
