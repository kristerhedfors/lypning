"""The route ledger, and the one property it must never lose.

The ledger records the refusals a static route provably cannot predict — a
CLEAN route whose tier then refused at RUNTIME, on a value. That is useful only
if it is WRITE-ONLY with respect to routing, and the first test in this file is
the one that says so: a graded conformance run produces byte-identical output
with the store populated and with it absent. Every other test here is about the
write being unable to hurt anything, and about the header discarding records
that were learned against a binary that no longer exists.

The dispatcher tests need the real Rust core, because the whole signal is a
binary refusing at runtime and no fabricated `Result` can prove that the
dispatcher keys on the right thing. They are skipped, not faked, when it is not
built — the rule every optional tier in this suite follows.
"""

from __future__ import annotations

import json

import pytest

from lypning import conformance, corpus, engines, paths, routes

#: The spectrum binaries, resolved AT IMPORT — before the autouse fixture moves
#: ``$LYPNING_HOME`` to a temp dir and hides ``~/.lypning/bin`` from discovery.
#: Same trick ``conftest`` uses for the C ABI, and for the same reason: a suite
#: that skipped every dispatcher test on a machine where the core IS built would
#: be testing nothing on the only machine that could test it.
try:
    _BUILT = {e: engines.find(e) for e in engines.SPECTRUM}
except Exception:  # a bad override in the developer's shell
    _BUILT = {}


@pytest.fixture
def spectrum():
    """The real spectrum binaries, linked into the isolated state bin dir."""
    if not _BUILT.get(engines.SPECTRUM[0]):
        pytest.skip("the Rust core is not built (`lypning build --rust`)")
    d = paths.ensure_dir(paths.bin_dir())
    for name, src in _BUILT.items():
        if src is not None:
            (d / name).symlink_to(src)
    return d


@pytest.fixture
def cpython():
    if engines.find_cpython() is None:
        pytest.skip("no reference CPython on PATH")
    return engines.find_cpython()


CORE = engines.SPECTRUM[0]

#: Routes clean on every built variant, then refuses at runtime on a VALUE.
#: `print(2**10)` is the same program to a static walker and runs fine, which is
#: the whole reason a static plan cannot contain this row.
BIGINT = "print(2**100)"


def _read(path):
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# --- the property the whole feature is subordinate to ------------------------


def test_a_populated_store_cannot_move_a_measurement(spectrum, cpython):
    """The most important test here: the ledger may not be able to change a grade.

    A store full of exactly the entries being graded is the adversarial case —
    if anything ever consulted it, this is where it would show. The report is
    compared verbatim, with only the wall clock zeroed: two runs of a battery
    cannot take the same number of nanoseconds, and that field is not a
    measurement of correctness.
    """
    entries = [corpus.Entry(id="r-1", program=BIGINT),
               corpus.Entry(id="r-2", program="print(2**10)"),
               corpus.Entry(id="r-3", program="print({3,1,2})")]
    arms = [CORE, conformance.MIXTURE]

    def graded():
        report = conformance.run(entries, engines=arms, workers=1)
        report.seconds = 0.0
        return conformance.render(report)

    absent = graded()
    # The run has to have actually graded something, or "identical" is vacuous.
    assert "conformance over 3 corpus programs" in absent
    assert "MISMATCH 0 — ok" in absent
    assert not routes.store_path(CORE).exists()

    for e in entries:
        routes.note(CORE, e.program, "bigint", "integer result beyond 64-bit range")
        routes.note(CORE, e.program, "set-order", "repr() of a set")
    store = routes.load(CORE)
    assert len(store.records) == 6, "the adversarial store did not populate"

    present = graded()
    assert present == absent
    # And the run did not touch what was there — the battery is not a session.
    assert len(routes.load(CORE).records) == 6


# --- invalidation ------------------------------------------------------------


def test_a_header_that_no_longer_describes_the_engine_discards_the_file(monkeypatch):
    monkeypatch.setattr(engines, "VARIANT_CAPS", dict(engines.VARIANT_CAPS, **{CORE: ()}))
    assert routes.note(CORE, BIGINT, "bigint", "beyond 64-bit")
    assert len(routes.load(CORE).records) == 1

    # The hillclimb adds a capability. Every record above was learned against a
    # binary that cannot answer for this one.
    monkeypatch.setattr(engines, "VARIANT_CAPS",
                        dict(engines.VARIANT_CAPS, **{CORE: ("cap-bigint",)}))
    store = routes.load(CORE)
    assert store.stale and store.records == []


def test_a_rebuilt_binary_discards_the_file(tmp_path, monkeypatch):
    fake = tmp_path / "engine"
    fake.write_bytes(b"\x7fELF" + b"\0" * 64)
    monkeypatch.setattr(engines, "find", lambda e: fake if e == CORE else None)
    assert routes.note(CORE, BIGINT, "bigint", "beyond 64-bit", binary=fake)
    assert len(routes.load(CORE).records) == 1
    fake.write_bytes(b"\x7fELF" + b"\0" * 128)  # a rebuild: size and mtime move
    store = routes.load(CORE)
    assert store.stale and store.records == []


def test_a_file_with_no_header_at_all_is_discarded_not_read():
    # Hand-edited, truncated, or written by something that is not this module.
    p = paths.ensure_dir(paths.routes_dir()) / (CORE + ".jsonl")
    p.write_text('{"id":"aaaaaaaaaaaa","kind":"bigint","detail":"x","n":9,"t":"2026-09-04"}\n',
                 encoding="utf-8")
    store = routes.load(CORE)
    assert store.stale and store.records == []


def test_a_discarded_store_renders_as_a_hole_and_says_why():
    p = paths.ensure_dir(paths.routes_dir()) / (CORE + ".jsonl")
    p.write_text('{"v":1,"engine":"%s","caps":["cap-nope"],"bin":""}\n' % CORE, encoding="utf-8")
    text = routes.render(routes.load_all())
    assert "no routes learned yet" in text
    assert "stale" in text
    assert "loaded 0 records" in text


# --- the write must be unable to hurt anything -------------------------------


def test_the_write_survives_an_unwritable_directory(tmp_path, monkeypatch):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        monkeypatch.setattr(paths, "routes_dir", lambda: locked / "routes")
        assert routes.note(CORE, BIGINT, "bigint", "beyond 64-bit") is False
        assert routes.load(CORE).present is False
    finally:
        locked.chmod(0o700)


def test_the_write_survives_a_corrupt_file():
    p = paths.ensure_dir(paths.routes_dir()) / (CORE + ".jsonl")
    p.write_bytes(b"\x00\xff not json at all\n{\"half\": \n")
    # Appending to nonsense is still an append: it cannot raise, and the reader
    # is the half that has to cope.
    assert routes.note(CORE, BIGINT, "bigint", "beyond 64-bit") is True
    store = routes.load(CORE)
    assert store.stale and store.records == []


def test_a_full_store_stops_growing_rather_than_growing_without_bound():
    p = paths.ensure_dir(paths.routes_dir()) / (CORE + ".jsonl")
    assert routes.note(CORE, BIGINT, "bigint", "beyond 64-bit")
    size = p.stat().st_size
    with open(p, "a", encoding="utf-8") as fh:
        fh.write("x" * (routes.MAX_BYTES - size))
    assert p.stat().st_size >= routes.MAX_BYTES
    assert routes.note(CORE, BIGINT, "bigint", "beyond 64-bit") is False
    assert p.stat().st_size == size + (routes.MAX_BYTES - size)


def test_lypning_routes_0_disables_the_writer(monkeypatch):
    monkeypatch.setenv("LYPNING_ROUTES", "0")
    assert routes.enabled() is False
    assert routes.note(CORE, BIGINT, "bigint", "beyond 64-bit") is False
    assert not routes.store_path(CORE).exists()
    # The READER still works — turning the writer off must not make the report
    # claim there is nothing to see.
    monkeypatch.delenv("LYPNING_ROUTES")
    assert routes.note(CORE, BIGINT, "bigint", "beyond 64-bit")
    monkeypatch.setenv("LYPNING_ROUTES", "0")
    assert len(routes.load(CORE).records) == 1


def test_one_write_is_one_line_and_fits_in_a_page():
    assert routes.note(CORE, BIGINT, "bigint", "d" * 4000)
    raw = routes.store_path(CORE).read_bytes()
    # Header plus record, written together, under one page so an O_APPEND write
    # from a parallel session cannot shred it.
    assert len(raw) < routes.MAX_LINE
    assert raw.count(b"\n") == 2
    assert len(_read(routes.store_path(CORE))[1]["detail"]) == routes.DETAIL_MAX


def test_a_program_that_cannot_be_digested_is_a_silent_no_op():
    assert routes.note(CORE, "\ud800", "bigint", "lone surrogate") is False
    assert not routes.store_path(CORE).exists()


# --- identity ----------------------------------------------------------------


def test_the_digest_is_stable_and_exact():
    assert routes.digest("print(2**100)") == routes.digest("print(2**100)")
    assert len(routes.digest("x")) == 12
    int(routes.digest("x"), 16)
    # The reason it is a digest of the text and not a feature key: these two
    # share every feature anything cheap could extract, and refuse differently.
    assert routes.digest("print(2**10)") != routes.digest("print(2**100)")


def test_identical_programs_fold_into_one_record_with_a_count():
    for _ in range(3):
        routes.note(CORE, BIGINT, "bigint", "beyond 64-bit")
    routes.note(CORE, "print({3,1,2})", "set-order", "repr of a set")
    assert len(routes.load(CORE).records) == 4

    folded = routes.compact()
    assert folded == [(CORE, 4, 2)]
    rows = {r["id"]: r for r in routes.load(CORE).records}
    assert rows[routes.digest(BIGINT)]["n"] == 3
    assert rows[routes.digest("print({3,1,2})")]["n"] == 1
    # Folding happens ONLY here. A second compact is a no-op, not a re-fold.
    assert routes.compact() == [(CORE, 2, 2)]


def test_forget_drops_one_program_and_clear_drops_the_stores():
    routes.note(CORE, BIGINT, "bigint", "beyond 64-bit")
    routes.note(CORE, "print({3,1,2})", "set-order", "repr of a set")
    assert routes.forget(routes.digest(BIGINT)) == [(CORE, 1)]
    assert [r["kind"] for r in routes.load(CORE).records] == ["set-order"]
    assert routes.forget("ffffffffffff") == []
    assert routes.clear() == [CORE]
    assert not routes.store_path(CORE).exists()


# --- reporting ---------------------------------------------------------------


def test_only_cpython_kinds_are_marked_not_implementable():
    blocked = sorted(engines.ONLY_CPYTHON_REFUSALS)[0]
    routes.note(CORE, "print({3,1,2})", blocked, "a reimplementation gets this wrong")
    routes.note(CORE, BIGINT, "bigint", "beyond 64-bit")
    rows = {k.kind: k for k in routes.kinds(routes.load_all())}
    assert rows[blocked].implementable is False
    assert rows["bigint"].implementable is True

    text = routes.render(routes.load_all())
    assert "NOT IMPLEMENTABLE" in text
    line = next(ln for ln in text.splitlines() if ln.strip().startswith(blocked))
    assert "NOT IMPLEMENTABLE" in line
    assert "NOT IMPLEMENTABLE" not in next(
        ln for ln in text.splitlines() if ln.strip().startswith("bigint"))

    # --plan is a BUILD ORDER, so a kind nobody may implement is not in it.
    plan = routes.render(routes.load_all(), plan=True)
    assert "bigint" in plan and blocked not in plan


def test_kinds_are_ranked_by_invocations():
    for _ in range(3):
        routes.note(CORE, "print(2**%d)" % _, "bigint", "beyond 64-bit")
    routes.note(CORE, "print({3,1,2})", "set-order", "repr of a set")
    ranked = [k.kind for k in routes.kinds(routes.load_all())]
    assert ranked == ["bigint", "set-order"]


def test_an_empty_store_is_a_hole_not_a_zero():
    text = routes.render(routes.load_all())
    assert "no routes learned yet" in text
    assert "never that" in text, "an empty ledger must not read as a clean bill"
    assert "0 record" not in text.split("loaded")[0]
    assert routes.status_line().startswith("routes       no routes learned yet")


def test_every_rendering_says_the_ledger_under_counts():
    assert routes.UNDERCOUNT in routes.render(routes.load_all())
    routes.note(CORE, BIGINT, "bigint", "beyond 64-bit")
    text = routes.render(routes.load_all())
    assert routes.UNDERCOUNT in text
    # Invariant 3: the count is the one this run loaded, with its date.
    assert "loaded 1 record(s)" in text
    assert routes.to_obj(routes.load_all())["undercounts"] is True


# --- the dispatcher ----------------------------------------------------------


def test_the_dispatcher_writes_on_a_clean_route_then_a_runtime_refusal(spectrum):
    d = engines.dispatch(BIGINT)
    assert d.route.kind == "", "the static route was not clean; this test proves nothing"
    assert d.result.returncode == 0 and d.result.engine == engines.CPYTHON

    records = routes.load(CORE).records
    assert len(records) == 1
    assert records[0]["id"] == routes.digest(BIGINT)
    assert records[0]["kind"] == "bigint"
    assert records[0]["n"] == 1


def test_a_static_route_to_cpython_writes_nothing(spectrum):
    d = engines.dispatch("import ctypes; print(1)")
    assert d.route.engine == engines.CPYTHON and d.route.kind
    assert not routes.store_path(CORE).exists()
    assert not routes.store_path(engines.CPYTHON).exists()


def test_a_program_that_merely_exits_non_zero_writes_nothing(spectrum):
    d = engines.dispatch("import sys; sys.exit(3)")
    assert d.result.returncode == 3
    assert not routes.store_path(CORE).exists()


def test_exit_90_without_the_contract_line_writes_nothing(spectrum):
    # The dispatcher does not fall through for it, so there is nothing to
    # record: a program choosing 90 is not a tier refusing.
    engines.dispatch("import sys; sys.exit(90)")
    assert not routes.store_path(CORE).exists()


def test_the_dispatcher_can_be_told_not_to_write(spectrum):
    engines.dispatch(BIGINT, ledger=False)
    assert not routes.store_path(CORE).exists()


def test_lypning_routes_0_reaches_the_dispatcher(spectrum, monkeypatch):
    monkeypatch.setenv("LYPNING_ROUTES", "0")
    engines.dispatch(BIGINT)
    assert not routes.store_path(CORE).exists()


# --- the CLI (invariant 8: the module returns the string, cli prints it) -----


def test_the_cli_renders_the_hole_and_the_json(capsys):
    from lypning import cli

    assert cli.main(["routes"]) == 0
    assert "no routes learned yet" in capsys.readouterr().out

    routes.note(CORE, BIGINT, "bigint", "beyond 64-bit")
    assert cli.main(["routes", "--json"]) == 0
    obj = json.loads(capsys.readouterr().out)
    assert obj["loaded"] == 1
    assert obj["kinds"][0]["kind"] == "bigint"

    assert cli.main(["routes", "--compact"]) == 0
    assert "folded" in capsys.readouterr().out
    assert cli.main(["routes", "--forget", routes.digest(BIGINT)]) == 0
    assert "dropped 1" in capsys.readouterr().out
    assert cli.main(["routes", "--clear"]) == 0
    assert "cleared" in capsys.readouterr().out


def test_status_carries_one_line_about_the_ledger(capsys):
    from lypning import cli

    routes.note(CORE, BIGINT, "bigint", "beyond 64-bit")
    assert cli.main(["status"]) == 0
    line = next(ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("routes"))
    assert "1 record(s), 1 kind(s)" in line
    assert str(paths.routes_dir()) in line


# --- the five defects an adversarial pass found in the first cut --------------
#
# Every one of these is a HONESTY defect rather than a safety defect: the
# write-only invariant survived the attack intact, and what did not survive was
# the rendering telling the truth about what it does and does not know. They
# are grouped because they are one lesson — a store is a fact about the world,
# and the three ways of not having one (absent, unreadable, truncated) are three
# different facts.


def test_an_unreadable_store_is_not_an_empty_one(tmp_path):
    """A hole, never a zero — and "unreadable" is a DIFFERENT hole from "absent".

    The first cut caught OSError from `read_text` and returned the same Store it
    returns for a file that was never written. A store of 3,000 records behind a
    permission fault then read as "no routes learned yet", which is the exact
    confusion the rendering contract exists to prevent, and it hid the fault for
    as long as it lasted.
    """
    p = tmp_path / "x.jsonl"
    p.write_text(json.dumps(routes.header(CORE)) + "\n", encoding="utf-8")
    p.chmod(0o000)
    try:
        store = routes.load(CORE, path=p)
        if store.present:  # a root-ish CI user can read it anyway
            assert store.unreadable
            assert not store.stale
            text = routes.render([store])
            assert "UNREADABLE" in text
            assert "no routes learned yet" not in text
            assert "UNREADABLE" in routes.status_line([store])
    finally:
        p.chmod(0o600)


def test_an_absent_store_still_reads_as_absent(tmp_path):
    """The other side of the same fix: absent must not become "unreadable"."""
    store = routes.load(CORE, path=tmp_path / "nope.jsonl")
    assert not store.present and not store.unreadable and not store.stale
    assert "no routes learned yet" in routes.render([store])


def test_compact_folds_and_never_truncates(tmp_path):
    """`--compact` is documented as folding. It may not also destroy.

    `load` stops at MAX_RECORDS and `compact` rewrites the file from what
    loaded, so an over-cap store was silently rewritten to the cap — with the
    loaded count quoted as the before-count, which made the loss invisible.
    """
    p = tmp_path / "big.jsonl"
    rows = [json.dumps(routes.header(CORE), separators=(",", ":"))]
    rows += [json.dumps({"id": "%012x" % i, "kind": "bigint", "detail": "d",
                         "n": 1, "t": "2026-09-01"}, separators=(",", ":"))
             for i in range(routes.MAX_RECORDS + 200)]
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    before = len(p.read_text(encoding="utf-8").splitlines())

    store = routes.load(CORE, path=p)
    assert store.truncated and len(store.records) == routes.MAX_RECORDS
    assert "TRUNCATED" in routes.render([store])

    assert routes.compact([store]) == [(CORE, routes.MAX_RECORDS, routes.MAX_RECORDS)]
    assert len(p.read_text(encoding="utf-8").splitlines()) == before


def test_compact_does_not_erase_a_stale_store(tmp_path, monkeypatch):
    """Rewriting a stale store with a current header turns a reason into "empty"."""
    p = tmp_path / "s.jsonl"
    head = dict(routes.header(CORE), bin="1:1")
    p.write_text(json.dumps(head, separators=(",", ":")) + "\n"
                 + json.dumps({"id": "a" * 12, "kind": "bigint", "detail": "d",
                               "n": 1, "t": "2026-09-01"}, separators=(",", ":")) + "\n",
                 encoding="utf-8")
    kept = p.read_text(encoding="utf-8")
    store = routes.load(CORE, path=p)
    assert store.stale
    routes.compact([store])
    assert p.read_text(encoding="utf-8") == kept
    assert "stale" in routes.render([routes.load(CORE, path=p)])


def test_a_store_cannot_smuggle_terminal_bytes_into_a_later_render(tmp_path):
    """The ledger is the first thing here that PERSISTS program-chosen bytes.

    `Result.refusal` reads its kind from the first stderr line merely CONTAINING
    the marker — looser than the `refused` gate — so a program can choose that
    text. The store is also editable by hand. Either way the bytes are replayed
    to a terminal on every later `lypning routes`, out of context from the run.
    """
    p = tmp_path / "evil.jsonl"
    p.write_text(
        json.dumps(routes.header(CORE), separators=(",", ":")) + "\n"
        + json.dumps({"id": "b" * 12, "kind": "\x1b[2Jbig\x07nt" + "A" * 5000,
                      "detail": "x\x1b]0;pwned\x07y", "n": 10 ** 40,
                      "t": "2026-09-01"}, separators=(",", ":")) + "\n",
        encoding="utf-8")
    rec = routes.load(CORE, path=p).records[0]
    assert "\x1b" not in rec["kind"] and "\x07" not in rec["kind"]
    assert "\x1b" not in rec["detail"]
    assert len(rec["kind"]) <= routes.KIND_MAX
    assert rec["n"] == 1                      # an invocation count is a count
    text = routes.render([routes.load(CORE, path=p)])
    assert "\x1b" not in text and "\x07" not in text


def test_the_documented_capture_opt_out_covers_this_feed(monkeypatch):
    """Invariant 7: a switch the user already has may not quietly narrow.

    README documents LYPNING_CAPTURE=0 as turning the capture harness off. This
    is a second feed writing program digests under $LYPNING_HOME, and a user who
    set that switch believes recording stopped.
    """
    monkeypatch.setenv("LYPNING_CAPTURE", "0")
    assert not routes.enabled()
    assert not routes.note(CORE, BIGINT, "bigint", "beyond 64-bit")
    assert not routes.store_path(CORE).exists()

    monkeypatch.setenv("LYPNING_CAPTURE", "1")
    monkeypatch.setenv("LYPNING_ROUTES", "0")
    assert not routes.enabled()
    monkeypatch.delenv("LYPNING_ROUTES")
    assert routes.enabled()
