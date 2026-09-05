"""The fixture tables of ``docs/VERIFICATION.md``, executed.

That document quotes a handful of rows from each table and points here for
the rest, so the tables live as data under ``tests/verification/`` — taken
from a run of the built binaries, not typed — and this file is what keeps
them true:

* ``route-fixtures.json``    program -> the line ``lypning route -c`` prints
  (§C5: the output grammar, both variants, the kinds that rule out every
  Rust variant, and the runtime-only refusals a static route cannot see);
* ``refusal-probes.json``    engine, program -> exit code, stdout, stderr
  (§C1: exit 90, one line headed by the variant's own name, nothing on
  stdout; ``sys.exit(90)`` is not a refusal; a program's own failure is
  returned unchanged; and what the dispatcher answers after a refusal);
* ``hook-fixtures.json``     hook event, stdin, environment -> stdout, exit
  (§C9: the protocol line and exit 0 on every path, the failures included).

A row that stops holding is a contract that moved, and the document's
EXPECTED block for it is stale in the same way: refresh both together
(``docs/VERIFICATION.md`` §0), never one by hand.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lypning import cli, engines

FIXTURES = Path(__file__).resolve().parent / "verification"


def _rows(name: str) -> list:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _run(argv: list, timeout: float = 60.0) -> subprocess.CompletedProcess:
    # Capture off: a probe is not a session's traffic, and a developer's
    # capture log must not gain a line per test run.
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout,
                          env={**os.environ, "LYPNING_CAPTURE": "0"})


def test_every_route_fixture_routes_as_the_table_says(lypning_bin):
    bad = []
    for row in _rows("route-fixtures.json"):
        proc = _run([str(lypning_bin), "route", "-c", row["program"]], timeout=30.0)
        got = proc.stdout.rstrip("\n")
        if proc.returncode != 0 or got != row["route"]:
            bad.append("%r -> %r (exit %d), the table says %r"
                       % (row["program"], got, proc.returncode, row["route"]))
    assert not bad, "\n".join(bad)


def test_every_refusal_probe_exits_and_prints_as_the_table_says(lypning_bin):
    """Every variant, the dispatcher, and the carve-outs, on the probes the
    document quotes. A variant that is not built skips its rows and says so
    at the end — after the rows that could run have been held to the table."""
    unbuilt = set()
    bad = []
    for row in _rows("refusal-probes.json"):
        engine = row["engine"]
        if engine == "run":
            argv = [str(lypning_bin), "run", "-c", row["program"]]
        elif engine == engines.LYPNING:
            argv = [str(lypning_bin), "-c", row["program"]]
        else:
            binary = engines.find(engine)
            if binary is None:
                unbuilt.add(engine)
                continue
            argv = [str(binary), "-c", row["program"]]
        proc = _run(argv)
        ok = proc.returncode == row["exit"] and proc.stdout == row["stdout"]
        if "stderr" in row:
            ok = ok and proc.stderr == row["stderr"]
        else:
            ok = ok and proc.stderr.startswith(row["stderr_startswith"])
        if not ok:
            bad.append("%s %r: exit %d stdout %r stderr %r; the table says exit %d stdout %r stderr %r"
                       % (engine, row["program"], proc.returncode, proc.stdout[:80],
                          proc.stderr[:120], row["exit"], row["stdout"][:80],
                          row.get("stderr", row.get("stderr_startswith"))[:120]))
    assert not bad, "\n".join(bad)
    if unbuilt:
        pytest.skip("rows for %s not run: not built (`lypning build --rust`)"
                    % ", ".join(sorted(unbuilt)))


def test_every_hook_fixture_answers_the_protocol_line(monkeypatch, capsys, tmp_path):
    """One line on stdout and exit 0, whatever stdin and the environment hold.

    Driven through ``cli.main`` — the entry point ``lypning hook <event>`` is —
    with the event on a replaced ``sys.stdin``. The log is a fresh file per
    row so a row can also say whether anything was written."""
    for i, row in enumerate(_rows("hook-fixtures.json")):
        log = tmp_path / ("%d.jsonl" % i)
        monkeypatch.setenv("LYPNING_LOG", str(log))
        for key, value in row["env"].items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(sys, "stdin", io.StringIO(row["stdin"]))
        rc = cli.main(["hook", row["event"]])
        out = capsys.readouterr().out
        assert rc == row["exit"], "%s: exit %d" % (row["name"], rc)
        assert out == row["stdout"], "%s: stdout %r" % (row["name"], out)
        if "logs" in row:
            assert log.exists() == row["logs"], "%s: log written=%s" % (row["name"], log.exists())
        for key, value in row.get("log_has", {}).items():
            record = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
            assert record.get(key) == value, "%s: %s=%r" % (row["name"], key, record.get(key))
        for key in row["env"]:
            monkeypatch.delenv(key, raising=False)


DOCS = FIXTURES.parent.parent / "docs"


def test_every_measurement_table_under_verification_is_dated_and_cited():
    """A table moved out of a document into ``tests/verification/*.md`` keeps
    its run header (tool, YYYY-MM-DD) and is quoted by the document that names
    it, so the number policy holds on both sides of the move."""
    import re
    for table in sorted(FIXTURES.glob("*.md")):
        text = table.read_text(encoding="utf-8")
        head = text.split("\n|", 1)[0]
        assert re.search(r"20\d\d-\d\d-\d\d", head), "%s: no date in its header" % table.name
        assert "`" in head, "%s: no tool named in its header" % table.name
        citers = re.findall(r"`docs/([A-Z-]+\.md)`", head)
        assert citers, "%s: names no document" % table.name
        for doc in citers:
            assert ("tests/verification/" + table.name) in (DOCS / doc).read_text(encoding="utf-8"), \
                "%s does not cite %s" % (doc, table.name)
        for name in re.findall(r"`(lypning(?:-[a-z]+)?)`", text):
            assert name in engines.ENGINE_ORDER or name in engines.ORACLES, name
