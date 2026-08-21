"""Importing someone else's programs: found by shape, folded, never republished.

Two failures are what these tests exist to catch, and neither one is loud.

The first is a source that goes quiet. Discovery matches on the *shape* of a
file's lines, so a repository that publishes under a directory name this one has
never heard of is still importable — and if that ever regresses to a name match,
the import does not fail, it reports zero programs, which is exactly what a
source with nothing to give reports. So the trees built here are deliberately
named nothing like this repository's.

The second is a forged provenance. An import may add to the corpus and may never
add to ``tests/corpus/sightings``: those files say a program ran *here*, in a
session that existed, and every frequency claim in ``docs/`` is read off them.
A test asserting the sightings directory was never created is the only thing
standing between "imported" and "observed".

Everything below writes into ``tmp_path`` and passes an explicit ``corpus_path``.
No test may touch this repository's own corpus, and none may reach a network:
``git`` appears only against a ``file://`` URL that cannot resolve.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from lypning import collect, harvest, paths

from conftest import requires_git

# A shape, not a credential: 30 characters no scanner has ever issued.
FAKE_KEY = "sk-not-a-real-key-0123456789ab"

# An opaque run near a credential-ish name that no pattern describes, so
# redaction cannot make it safe and the program has to be dropped whole.
UNSCRUBBABLE = "k9QpLm2xR7vT4wYz1sD8fG5hJ0nB6c"

# Nothing in the shipped corpus contains these, so a fold that reports them as
# added is reporting this test's records and not something already on disk.
MARK = "lypning collect fixture 8c1d"


def _record(program, **over):
    """One line of a published collection, in the shape another repo would write.

    The declared ``key`` is deliberately a spelling this tree does not produce.
    A foreign key is the normal case — the upstream tree keys on raw program
    text and hand-slugs its seeds — and merging on it would file one program
    under two ids, so every test here gets a record whose key is wrong on
    purpose.
    """
    rec = {
        "key": "foreign-" + str(abs(hash(program)) % 10 ** 8),
        "program": program,
        "argv_tail": [],
        "source": "shim",
        "session": "their-session",
        "first_seen": "2026-01-01T00:00:00.000Z",
        "count": 1,
    }
    rec.update(over)
    rec["id"] = rec["key"]
    return rec


def _collection(path, programs):
    """Write a .jsonl of published programs at ``path``, creating its parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(_record(p)) + "\n" for p in programs)
    path.write_text(body, encoding="utf-8")
    return path


def _source(location, name="theirs"):
    return collect.Source(name=name, location=str(location))


# --- discovery ---------------------------------------------------------------


def test_discover_finds_a_sightings_directory_and_a_corpus_file(tmp_path):
    root = tmp_path / "theirs"
    published = _collection(root / "tests" / "corpus" / "sightings" / "sess-a.jsonl",
                            ["print('%s a')" % MARK])
    # A file name that says nothing: under a `sightings` directory the name is
    # a session id, so matching on it would find only the files we happen to
    # name the way we do.
    rolled = _collection(root / "var" / "sightings" / "2026-04-11.jsonl",
                         ["print('%s b')" % MARK])
    corpus_file = _collection(root / "data" / "corpus.jsonl", ["print('%s c')" % MARK])

    assert collect.discover(root) == sorted([published, rolled, corpus_file])


def test_discover_finds_a_collection_under_a_directory_nobody_told_us_about(tmp_path):
    # The point of the module: a source that calls its published directory
    # something else is still importable. Nothing in this path is named
    # `sightings`, and no registry entry said where to look.
    root = tmp_path / "theirs"
    odd = _collection(root / "evidence" / "harvested" / "runs-corpus.jsonl",
                      ["print('%s odd')" % MARK])
    assert collect.discover(root) == [odd]


def test_a_name_that_matches_is_not_enough_the_lines_decide(tmp_path):
    # `_is_candidate` is a cheap filter in front of the shape test, never the
    # answer. A repository with a corpus of something else — coverage data,
    # embeddings, benchmark rows — must import as zero programs rather than as
    # several thousand records that are not programs.
    root = tmp_path / "theirs"
    not_programs = root / "metrics" / "timing-corpus.jsonl"
    not_programs.parent.mkdir(parents=True)
    not_programs.write_text(
        "".join(json.dumps({"name": "n%d" % i, "ms": i}) + "\n" for i in range(6)),
        encoding="utf-8")
    assert collect.discover(root) == []


def test_looks_like_collection_rejects_what_is_not_a_collection(tmp_path):
    other = tmp_path / "other-corpus.jsonl"
    other.write_text(json.dumps({"id": "x", "ms": 3}) + "\n", encoding="utf-8")
    assert collect.looks_like_collection(other) is False

    prose = tmp_path / "notes-corpus.jsonl"
    prose.write_text("this file is not JSON at all\nnor is this line\n", encoding="utf-8")
    assert collect.looks_like_collection(prose) is False

    empty = tmp_path / "empty-corpus.jsonl"
    empty.write_text("", encoding="utf-8")
    assert collect.looks_like_collection(empty) is False

    blank = tmp_path / "blank-corpus.jsonl"
    blank.write_text("\n\n   \n", encoding="utf-8")
    assert collect.looks_like_collection(blank) is False

    blob = tmp_path / "blob-corpus.jsonl"
    blob.write_bytes(bytes(range(256)) * 4)
    assert collect.looks_like_collection(blob) is False

    missing = tmp_path / "gone" / "corpus.jsonl"
    assert collect.looks_like_collection(missing) is False


def test_one_line_of_the_right_shape_does_not_carry_a_log_file(tmp_path):
    # An `any` test over the sample would let a log with one python-shaped
    # record in it import as a collection, and the rest of it as sightings.
    mixed = tmp_path / "events-corpus.jsonl"
    mixed.write_text(
        json.dumps({"program": "print(1)"}) + "\n"
        + json.dumps({"kind": "exit", "code": 0}) + "\n",
        encoding="utf-8")
    assert collect.looks_like_collection(mixed) is False


def test_an_empty_program_string_is_not_a_program(tmp_path):
    hollow = tmp_path / "hollow-corpus.jsonl"
    hollow.write_text(json.dumps({"program": ""}) + "\n", encoding="utf-8")
    assert collect.looks_like_collection(hollow) is False


def test_discover_prunes_dot_git(tmp_path):
    # Most of the bytes of any clone, and a packed object that decompresses into
    # something ending in .jsonl is not a published collection.
    root = tmp_path / "theirs"
    wanted = _collection(root / "sightings" / "a.jsonl", ["print('%s keep')" % MARK])
    _collection(root / ".git" / "sightings" / "b.jsonl", ["print('%s drop')" % MARK])
    _collection(root / "node_modules" / "pkg" / "corpus.jsonl", ["print('%s drop')" % MARK])
    _collection(root / "target" / "corpus.jsonl", ["print('%s drop')" % MARK])
    assert collect.discover(root) == [wanted]


def test_discover_survives_a_symlink_loop_and_a_directory_it_cannot_read(tmp_path):
    # A walk over somebody else's repository is a walk over an unknown tree.
    # Either of these raising would cost the import every source after it.
    root = tmp_path / "theirs"
    wanted = _collection(root / "sightings" / "a.jsonl", ["print('%s loop')" % MARK])
    try:
        (root / "sightings" / "back").symlink_to(root)
    except OSError:
        pytest.skip("this filesystem does not do symlinks")

    closed = root / "closed"
    closed.mkdir()
    (closed / "corpus.jsonl").write_text("", encoding="utf-8")
    os.chmod(str(closed), 0o000)
    try:
        # Holds whether or not the caller is root: nothing findable is inside.
        assert collect.discover(root) == [wanted]
    finally:
        os.chmod(str(closed), 0o755)


def test_discover_accepts_a_single_file_because_that_is_what_people_type(tmp_path):
    # `--from ../other/tests/corpus/sightings/sess-a.jsonl` is the obvious thing
    # to try when importing one file, and a walk of a file finds nothing.
    one = _collection(tmp_path / "sightings" / "sess-a.jsonl", ["print('%s one')" % MARK])
    assert collect.discover(one) == [one]
    assert collect.discover(one.parent) == [one]


def test_read_collection_drops_a_corrupt_line_and_keeps_the_file(tmp_path):
    # These files are appended to by hooks that can be killed mid-write, so a
    # truncated last line is expected. Losing the other four hundred records to
    # it would not be.
    path = tmp_path / "sightings" / "a.jsonl"
    _collection(path, ["print('%s 1')" % MARK, "print('%s 2')" % MARK])
    with open(str(path), "a", encoding="utf-8") as fh:
        fh.write('{"program": "print(3)"\n')  # killed mid-write
        fh.write(json.dumps({"note": "no program here"}) + "\n")

    got = collect.read_collection(path)
    assert [s.program for s in got] == ["print('%s 1')" % MARK, "print('%s 2')" % MARK]
    # Provenance is what the record declared. It is not ours to relabel: a shim
    # record from another repository still means a program that actually ran.
    assert {s.source for s in got} == {"shim"}


# --- the import --------------------------------------------------------------


def _corpus_text(path):
    return path.read_text(encoding="utf-8")


def test_import_folds_a_local_source_into_the_corpus(tmp_path, capsys):
    root = tmp_path / "theirs"
    _collection(root / "sightings" / "sess-a.jsonl",
                ["print('%s x')" % MARK, "print('%s y')" % MARK])
    target = tmp_path / "corpus.jsonl"

    result = collect.import_sources([_source(root)], corpus_path=target)

    assert result.gathered == 2
    assert result.new == 2
    assert (result.added, result.total) == (2, 2)
    assert result.corpus == str(target)
    assert result.sources[0]["note"] == "local"
    assert result.sources[0]["files"] == 1
    assert result.sources[0]["programs"] == 2
    assert capsys.readouterr().out == ""  # library code does not print

    ids = [json.loads(l)["id"] for l in _corpus_text(target).splitlines()]
    # Re-keyed on OUR key function. A key is only a dedup handle if both sides
    # compute it the same way, and the record declared one this tree does not
    # produce — folding on it would file the same program under two ids.
    assert ids == sorted(harvest.sighting_key("print('%s %s')" % (MARK, c)) for c in "xy")


def test_a_second_import_adds_nothing_and_rewrites_nothing(tmp_path):
    # Collect is a command a user runs again whenever a source may have moved.
    # A run that rewrites byte-identical content dirties the tree every time and
    # turns "did anything change" into a question nobody can answer from git.
    root = tmp_path / "theirs"
    _collection(root / "sightings" / "sess-a.jsonl", ["print('%s idem')" % MARK])
    target = tmp_path / "corpus.jsonl"

    first = collect.import_sources([_source(root)], corpus_path=target)
    assert first.added == 1
    before = target.read_bytes()
    stamp = target.stat().st_mtime_ns

    second = collect.import_sources([_source(root)], corpus_path=target)
    assert (second.added, second.new) == (0, 0)
    assert second.total == 1
    assert target.read_bytes() == before
    assert target.stat().st_mtime_ns == stamp


def test_an_import_never_publishes_into_this_repositorys_sightings(tmp_path, project, monkeypatch):
    # The invariant the module exists for. A sightings file asserts that a
    # program ran in a session that existed; someone else's program written into
    # one forges the single field nobody can check afterwards.
    monkeypatch.delenv("LYPNING_SIGHTINGS", raising=False)
    root = tmp_path / "theirs"
    _collection(root / "sightings" / "sess-a.jsonl", ["print('%s theirs')" % MARK])
    target = tmp_path / "corpus.jsonl"

    result = collect.import_sources([_source(root)], corpus_path=target)

    assert result.added == 1
    assert not paths.sightings_dir(project).exists()
    assert not (project / "tests").exists()


def test_a_credential_is_redacted_on_the_way_in(tmp_path):
    root = tmp_path / "theirs"
    _collection(root / "sightings" / "sess-a.jsonl",
                ['api_key = "%s"\nprint("%s leak")' % (FAKE_KEY, MARK)])
    target = tmp_path / "corpus.jsonl"

    result = collect.import_sources([_source(root)], corpus_path=target)

    assert result.added == 1
    text = _corpus_text(target)
    assert FAKE_KEY not in text
    assert "REDACTED" in text
    # The name survives, so a reader of the diff knows which credential to
    # rotate without the value ever having been written.
    assert "api_key" in text


def test_a_program_whose_credential_cannot_be_scrubbed_is_dropped(tmp_path):
    # Publishing it would cost somebody a rotation; dropping it costs one entry.
    root = tmp_path / "theirs"
    _collection(root / "sightings" / "sess-a.jsonl", [
        "print('%s safe')" % MARK,
        "headers = {}\nheaders['x'] = 'Bearer' + 'Ab3' + '%s'" % UNSCRUBBABLE,
    ])
    target = tmp_path / "corpus.jsonl"

    result = collect.import_sources([_source(root)], corpus_path=target)

    assert result.gathered == 2  # both were read
    assert (result.new, result.added, result.total) == (1, 1, 1)  # one was published
    text = _corpus_text(target)
    assert UNSCRUBBABLE not in text
    assert "%s safe" % MARK in text


def test_dry_run_writes_nothing(tmp_path):
    # `--dry-run` is real everywhere else in this package, and a dry run that
    # left a corpus behind would be the one place a user could not trust it.
    root = tmp_path / "theirs"
    _collection(root / "sightings" / "sess-a.jsonl",
                ["print('%s dry1')" % MARK, "print('%s dry2')" % MARK])
    target = tmp_path / "corpus.jsonl"

    result = collect.import_sources([_source(root)], corpus_path=target, dry_run=True)

    assert result.dry_run is True
    assert (result.added, result.total, result.new) == (2, 2, 2)
    assert not target.exists()
    assert not target.parent.joinpath("corpus.jsonl").exists()


def test_dry_run_over_an_existing_corpus_leaves_its_bytes_alone(tmp_path):
    root = tmp_path / "theirs"
    _collection(root / "sightings" / "sess-a.jsonl", ["print('%s keep')" % MARK])
    target = tmp_path / "corpus.jsonl"
    collect.import_sources([_source(root)], corpus_path=target)
    before = target.read_bytes()

    _collection(root / "sightings" / "sess-b.jsonl", ["print('%s later')" % MARK])
    result = collect.import_sources([_source(root)], corpus_path=target, dry_run=True)

    assert (result.added, result.total) == (1, 2)  # what the real run would do
    assert target.read_bytes() == before


def test_two_sources_publishing_one_program_land_as_a_single_record(tmp_path):
    # Both sides are counting the same occurrences of the same program, so the
    # merge takes the max rather than the sum — a fold that summed would inflate
    # the frequency table that decides what the engines are built to be fast at.
    a = tmp_path / "a"
    b = tmp_path / "b"
    shared = "print('%s shared')" % MARK
    _collection(a / "sightings" / "s.jsonl", [shared])
    (b / "sightings").mkdir(parents=True)
    (b / "sightings" / "s.jsonl").write_text(
        json.dumps(_record(shared, count=7)) + "\n", encoding="utf-8")
    target = tmp_path / "corpus.jsonl"

    result = collect.import_sources([_source(a, "a"), _source(b, "b")], corpus_path=target)

    assert result.gathered == 1
    assert (result.added, result.total) == (1, 1)
    rec = json.loads(_corpus_text(target).splitlines()[0])
    assert rec["count"] == 7


def test_offline_never_runs_git(tmp_path, monkeypatch):
    # `--offline` is a promise that no process touches the network, not a
    # preference for the cache. A "try git and fall back" implementation would
    # still hang a session on a URL that resolves slowly.
    def forbidden(*a, **kw):
        raise AssertionError("git ran under --offline: %r" % (a,))

    monkeypatch.setattr(collect.subprocess, "run", forbidden)

    local = tmp_path / "theirs"
    _collection(local / "sightings" / "s.jsonl", ["print('%s off')" % MARK])
    remote = collect.Source(name="remote", location="https://example.invalid/repo.git")
    target = tmp_path / "corpus.jsonl"

    result = collect.import_sources([_source(local), remote],
                                    corpus_path=target, offline=True)

    assert result.added == 1
    notes = {s["name"]: s["note"] for s in result.sources}
    assert notes["theirs"] == "local"
    assert notes["remote"] == "offline"
    assert result.sources[1]["resolved"] is None


def test_render_returns_a_string_and_prints_nothing(tmp_path, capsys):
    root = tmp_path / "theirs"
    _collection(root / "sightings" / "s.jsonl", ["print('%s render')" % MARK])
    result = collect.import_sources([_source(root, "their-repo")],
                                    corpus_path=tmp_path / "corpus.jsonl")

    out = collect.render(result)

    assert isinstance(out, str)
    assert out.endswith("\n")
    out.encode("ascii")  # a report is not worth a UnicodeEncodeError
    assert "their-repo" in out and str(root) in out
    assert "1 of 1 resolved" in out
    assert capsys.readouterr().out == ""


def test_render_says_so_when_there_is_nothing_configured():
    out = collect.render(collect.ImportResult())
    assert "no sources configured" in out
    out.encode("ascii")


# --- fetching ----------------------------------------------------------------


def test_a_local_location_resolves_to_itself_and_is_never_copied(tmp_path):
    root = tmp_path / "theirs"
    (root / "sightings").mkdir(parents=True)
    path, note = collect.fetch(_source(root))
    assert path == root.resolve()
    assert note == "local"
    assert not paths.sources_cache_dir().exists()


def test_a_path_that_is_not_there_is_a_reason_not_an_exception(tmp_path):
    path, note = collect.fetch(_source(tmp_path / "never-existed"))
    assert path is None
    assert note == "no such path"


def test_a_blank_location_is_refused_before_anything_runs(monkeypatch):
    monkeypatch.setattr(collect.subprocess, "run",
                        lambda *a, **kw: pytest.fail("git ran for a blank location"))
    assert collect.fetch(collect.Source(name="x", location="   ")) == (None, "no location")


def test_offline_without_a_cache_says_offline_and_does_not_run_git(tmp_path, monkeypatch):
    monkeypatch.setattr(collect.subprocess, "run",
                        lambda *a, **kw: pytest.fail("git ran under offline"))
    src = collect.Source(name="r", location="https://example.invalid/repo.git")
    assert collect.fetch(src, dest=tmp_path / "cache", offline=True) == (None, "offline")


@requires_git
def test_a_bogus_url_reports_a_reason_rather_than_raising(tmp_path):
    # `file://` on purpose: this asserts the failure path without a network, and
    # a test that reached one would fail on the machines this package is built
    # on. The timeout is short for the same reason — a hang here is a hang in a
    # Stop hook, which is a session that never ends.
    src = collect.Source(name="ghost", location="file://" + str(tmp_path / "no-such-repo.git"))
    path, note = collect.fetch(src, dest=tmp_path / "cache", timeout=60)
    assert path is None
    assert note and note != "cloned"
    assert not (tmp_path / "cache" / ".git").exists()


def test_a_cache_directory_holding_something_else_is_refused_not_deleted(tmp_path):
    # It is under the user's home and we did not put what is in it there.
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "someones-notes.txt").write_text("mine\n", encoding="utf-8")
    src = collect.Source(name="r", location="https://example.invalid/repo.git")
    path, note = collect.fetch(src, dest=cache)
    assert path is None
    assert "not a clone" in note
    assert (cache / "someones-notes.txt").exists()


def test_is_url_tells_a_path_from_a_location_by_shape(tmp_path):
    # By shape rather than by trying it: a typo in a path must not become a
    # network request to a host somebody registered.
    assert collect.Source("a", "https://example.invalid/x.git").is_url
    assert collect.Source("a", "git@example.invalid:owner/x.git").is_url
    assert collect.Source("a", "ssh://git@example.invalid/x.git").is_url
    assert not collect.Source("a", str(tmp_path)).is_url
    assert not collect.Source("a", "../next-door").is_url
    assert not collect.Source("a", "~/src/next-door").is_url


def test_two_forks_with_the_same_name_do_not_share_a_cache_directory():
    # Landing both in `sources/lypning` would have the second import quietly
    # read the first one's tree and report the first one's programs.
    a = collect.slug("https://example.invalid/alice/lypning.git")
    b = collect.slug("https://example.invalid/bob/lypning.git")
    assert a != b
    assert a.startswith("lypning-") and b.startswith("lypning-")
    assert a == collect.slug("https://example.invalid/alice/lypning.git")
    # Whatever a location contains, the result is one filesystem-safe segment.
    assert "/" not in collect.slug("git@example.invalid:owner/name with spaces")


# --- the registry ------------------------------------------------------------


def test_load_sources_reads_the_registry(tmp_path, monkeypatch):
    monkeypatch.delenv("LYPNING_SOURCES", raising=False)
    reg = tmp_path / "sources.json"
    reg.write_text(json.dumps({"sources": [
        {"name": "theirs", "location": "https://example.invalid/x.git", "note": "why"},
        {"location": "/srv/other"},
    ]}), encoding="utf-8")

    got = collect.load_sources(reg)

    assert [s.name for s in got] == ["theirs", collect.slug("/srv/other")]
    assert got[0].note == "why"
    assert got[1].location == "/srv/other"


def test_load_sources_survives_a_missing_registry(tmp_path, monkeypatch):
    monkeypatch.delenv("LYPNING_SOURCES", raising=False)
    assert collect.load_sources(tmp_path / "not-here.json") == []


def test_a_malformed_registry_does_not_cost_the_user_the_source_they_typed(tmp_path, monkeypatch):
    # The whole reason this never raises: a half-written data file must not be
    # the reason an explicitly named location cannot be imported.
    reg = tmp_path / "sources.json"
    reg.write_text('{"sources": [{"location": "/a"},', encoding="utf-8")
    monkeypatch.setenv("LYPNING_SOURCES", "/srv/typed")

    got = collect.load_sources(reg)

    assert [s.location for s in got] == ["/srv/typed"]


def test_a_registry_of_the_wrong_shape_yields_nothing_rather_than_junk(tmp_path, monkeypatch):
    monkeypatch.delenv("LYPNING_SOURCES", raising=False)
    for body in ('[]', '{"sources": {}}', '{"sources": ["/a", 3]}',
                 '{"sources": [{"location": ""}, {"name": "n"}]}', 'null'):
        reg = tmp_path / "sources.json"
        reg.write_text(body, encoding="utf-8")
        assert collect.load_sources(reg) == [], body


def test_lypning_sources_adds_locations_and_keeps_urls_whole(tmp_path, monkeypatch):
    # POSIX pathsep is `:`, which is also the character inside every URL this
    # variable may hold. A naive split turns one location into two that nobody
    # named, and both of them silently resolve to nothing.
    reg = tmp_path / "sources.json"
    reg.write_text(json.dumps({"sources": [{"location": "/from/file"}]}), encoding="utf-8")
    monkeypatch.setenv("LYPNING_SOURCES", os.pathsep.join(
        ["https://example.invalid/x.git", "/srv/next-door", "git@example.invalid:owner/y.git"]))

    got = collect.load_sources(reg)

    assert [s.location for s in got] == [
        "/from/file",
        "https://example.invalid/x.git",
        "/srv/next-door",
        "git@example.invalid:owner/y.git",
    ]
    assert [s.note for s in got[1:]] == ["$LYPNING_SOURCES"] * 3


def test_a_blank_lypning_sources_adds_nothing(tmp_path, monkeypatch):
    # An exported-but-empty variable is the shell's way of saying unset; taking
    # it literally would register the current directory as a source.
    monkeypatch.setenv("LYPNING_SOURCES", os.pathsep + "  " + os.pathsep)
    assert collect.load_sources(tmp_path / "none.json") == []


def test_the_shipped_registry_is_readable_and_names_real_locations():
    # It ships in the wheel, so a typo in it is a release, not a patch.
    got = collect.load_sources()
    assert isinstance(got, list)
    for s in got:
        assert s.name and s.location
        assert isinstance(s.is_url, bool)


def test_nothing_here_ever_reaches_for_the_sightings_directory():
    # Grep-level, because the guarantee is about code that does not exist: the
    # module docstring promises no path in it opens a sightings file, and the
    # cheapest way for that to regress is a convenience call added later.
    src = (paths.PACKAGE_ROOT / "collect.py").read_text(encoding="utf-8")
    body = src.split('"""', 2)[-1]  # past the module docstring, which says why
    assert "sightings_dir" not in body
    assert "export_sightings" not in body


def test_import_result_to_obj_is_json_and_holds_no_paths(tmp_path):
    root = tmp_path / "theirs"
    _collection(root / "sightings" / "s.jsonl", ["print('%s obj')" % MARK])
    result = collect.import_sources([_source(root)], corpus_path=tmp_path / "corpus.jsonl")

    obj = result.to_obj()
    round_tripped = json.loads(json.dumps(obj))

    assert round_tripped == obj
    assert set(obj) == {"sources", "gathered", "new", "added", "total", "corpus", "dry_run"}
    assert obj["corpus"] == str(tmp_path / "corpus.jsonl")


def test_a_source_that_fails_does_not_cost_the_import_the_ones_that_work(tmp_path):
    root = tmp_path / "theirs"
    _collection(root / "sightings" / "s.jsonl", ["print('%s survivor')" % MARK])
    target = tmp_path / "corpus.jsonl"

    result = collect.import_sources(
        [collect.Source("gone", str(tmp_path / "never-existed")), _source(root)],
        corpus_path=target)

    assert result.added == 1
    assert len(result.resolved) == 1
    assert result.sources[0]["note"] == "no such path"
    assert "1 of 2 resolved" in collect.render(result)


def test_the_git_calls_disable_hooks_and_the_credential_prompt(tmp_path, monkeypatch):
    # A fetched tree is data, not code. If a clone ever ran with hooks enabled,
    # a line in a JSON file would be arbitrary code execution; if it ever ran
    # without GIT_TERMINAL_PROMPT=0, a private URL would block a session on a
    # prompt nobody is there to answer.
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = list(cmd)
        seen["env"] = kw.get("env") or {}
        seen["timeout"] = kw.get("timeout")
        raise OSError("git is not going to run in a test")

    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    src = collect.Source("r", "https://example.invalid/repo.git")

    path, note = collect.fetch(src, dest=tmp_path / "cache", timeout=30)

    assert (path, bool(note)) == (None, True)
    assert "core.hooksPath=/dev/null" in seen["cmd"]
    assert seen["cmd"][:2] == ["git", "-c"]
    assert "--depth" in seen["cmd"] and "1" in seen["cmd"]
    assert seen["env"].get("GIT_TERMINAL_PROMPT") == "0"
    assert "GIT_DIR" not in seen["env"]
    assert seen["timeout"] == 30


def test_a_clone_is_never_placed_inside_the_package(monkeypatch, tmp_path):
    # A working tree under site-packages is one `pip uninstall` has never heard
    # of; one under a checkout's assets/ is a nested repository in `git status`
    # forever. Both put someone else's code inside our package.
    monkeypatch.setenv("LYPNING_HOME", str(tmp_path / "state"))
    cache = paths.sources_cache_dir()
    assert cache == tmp_path / "state" / "sources"
    assert paths.ASSETS not in cache.parents
    assert paths.PACKAGE_ROOT not in cache.parents


def test_the_walk_is_bounded_by_the_file_limit(tmp_path):
    # A source is an arbitrary repository, so the ceiling must not depend on the
    # source behaving. The cost of the limit is a short report; the cost of no
    # limit is an import that never returns.
    root = tmp_path / "theirs"
    for i in range(12):
        _collection(root / "sightings" / ("s%02d.jsonl" % i), ["print('%s %d')" % (MARK, i)])
    assert len(collect.discover(root, limit=4)) <= 4
    assert len(collect.discover(root)) == 12


def test_a_file_larger_than_the_cap_is_not_a_collection(tmp_path, monkeypatch):
    # Past the cap it is a data dump that happens to end in .jsonl, and reading
    # it would cost more memory than the whole corpus is worth.
    monkeypatch.setattr(collect, "MAX_SOURCE_BYTES", 64)
    root = tmp_path / "theirs"
    _collection(root / "sightings" / "big.jsonl",
                ["print('%s %s')" % (MARK, "padding" * 40)])
    assert collect.discover(root) == []


@requires_git
def test_a_clone_of_a_real_local_repository_is_read_but_never_run(tmp_path, git_repo):
    # The end-to-end shape without a network: a `file://` URL is still a URL, so
    # this exercises clone, cache placement and discovery over a git tree.
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})
    _collection(git_repo / "sightings" / "sess-a.jsonl", ["print('%s cloned')" % MARK])
    # An executable in the tree, to be sure nothing here ever runs one.
    (git_repo / "setup.sh").write_text("#!/bin/sh\ntouch %s\n" % (tmp_path / "RAN"),
                                       encoding="utf-8")
    os.chmod(str(git_repo / "setup.sh"), 0o755)
    subprocess.run(["git", "-C", str(git_repo), "add", "-A"], check=True,
                   capture_output=True, env=env)
    subprocess.run(["git", "-C", str(git_repo), "commit", "-qm", "publish"], check=True,
                   capture_output=True, env=env)

    src = collect.Source("theirs", "file://" + str(git_repo))
    target = tmp_path / "corpus.jsonl"
    result = collect.import_sources([src], corpus_path=target)

    assert result.sources[0]["note"] == "cloned"
    assert result.added == 1
    assert not (tmp_path / "RAN").exists()
    cache = paths.sources_cache_dir() / collect.slug(src.location)
    assert (cache / ".git").exists()

    # Second run updates in place rather than cloning again, and adds nothing.
    again = collect.import_sources([src], corpus_path=target)
    assert again.sources[0]["note"] in ("updated", "cloned")
    assert again.added == 0
