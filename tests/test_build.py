"""What a build must lay out, install, and refuse to install.

No test here compiles anything: cargo and a musl bootstrap are minutes, and
what actually breaks is not the compile. It is the plumbing around it — where
the shell script thinks its engine tree is, which of the produced binaries is
allowed into the directory the engine finders read, and whether `--verify`
measures the binary that was just built or the one that was already there.
Every one of those fails silently, which is why they are pinned here.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from lypning import build, engines, paths


# --- the tree the shell script derives ---------------------------------------


def test_the_wheel_path_copies_the_crate_to_writable_state(tmp_path, monkeypatch):
    dest = tmp_path / "build"
    monkeypatch.setattr(paths, "build_dir", lambda: dest)

    crate, note = build._rust_workdir()

    assert crate == dest / "rust"
    assert (crate / "Cargo.toml").is_file()
    assert (crate / "src" / "main.rs").is_file()
    assert "crate copied" in note








# --- the benchmark control ---------------------------------------------------


def test_the_host_build_is_this_machines_engine_and_installs_unsuffixed():
    # `lypning build --target host` used to install as `lypning-host`, a name no
    # finder looked for, so a darwin host had a built engine and no engine.
    assert build._runs_here("host")
    assert build._runs_here("")
    assert not build._runs_here("i686-unknown-linux-musl") or __import__("platform").machine() == "i686"


def test_each_variant_is_one_cargo_feature_and_its_own_target_dir(monkeypatch):
    assert build.variant_feature(engines.LYPNING) == "variant-m"
    assert build.variant_feature("lypning-l") == "variant-l"
    with pytest.raises(ValueError):
        build.variant_feature("lypning-q")          # not on the spectrum
    r = build.build_rust(target="host", dry_run=True, variant=engines.LYPNING)
    assert r.engine == engines.LYPNING and r.dry_run
    assert "--features variant-m" in r.log and "--no-default-features" not in r.log
    assert "--target-dir" not in r.log            # the default variant shares cargo's target/
    r = build.build_rust(target="host", dry_run=True, variant="lypning-l")
    assert r.engine == "lypning-l" and r.dry_run
    assert "--features variant-l" in r.log and "--no-default-features" in r.log
    assert "target/variant-l" in r.log and r.log.rstrip().endswith("release/lypning")
    r = build.build_rust(target="host", dry_run=True, variant="lypning-q")
    assert not r.ok and "not a Rust variant" in r.skipped_reason


def test_build_all_builds_every_variant_by_default(monkeypatch):
    results = build.build_all(rust=True, target="host", dry_run=True)
    rust = [r.engine for r in results if r.engine in engines.SPECTRUM]
    assert rust == ["lypning", "lypning-l"]
    results = build.build_all(rust=True, target="host", dry_run=True, variant="lypning-l")
    assert [r.engine for r in results if r.engine in engines.SPECTRUM] == ["lypning-l"]














# --- verify ------------------------------------------------------------------


def test_verify_measures_the_binary_that_was_just_built(tmp_path, monkeypatch):
    """The pin is the point.

    Without it a build whose binary is broken enough not to be installed gets
    verified against the previous one still sitting in the bin dir, and reports
    ok for a binary nobody measured.
    """
    from lypning import conformance, gate

    fresh = tmp_path / "lypning-fresh"
    fresh.write_bytes(b"\x7fELF fake")
    seen = {}

    class _Report:
        ok = True

    def fake_gate(binary, compare=False):
        seen["gated"] = str(binary)
        seen["env_at_gate"] = os.environ.get("LYPNING_BIN")
        return _Report()

    def fake_run(**kwargs):
        seen["env_at_battery"] = os.environ.get("LYPNING_BIN")
        return _Report()

    monkeypatch.setattr(gate, "gate", fake_gate)
    monkeypatch.setattr(conformance, "run", fake_run)
    monkeypatch.setenv("LYPNING_BIN", "")
    monkeypatch.delenv("LYPNING_BIN")

    out = build.verify([build.BuildResult(engines.LYPNING, ok=True, binary=fresh)])
    assert out.ok
    assert seen["gated"] == str(fresh)
    assert seen["env_at_gate"] == str(fresh)
    assert seen["env_at_battery"] == str(fresh)
    # And restored: this is a library, and a caller that runs anything else in
    # the same process must not inherit our overrides.
    assert "LYPNING_BIN" not in os.environ




def test_a_failed_battery_fails_the_verify(tmp_path, monkeypatch):
    from lypning import conformance, gate

    class _Bad:
        ok = False

    class _Good:
        ok = True

    core = tmp_path / "lypning"
    core.write_bytes(b"\x7fELF fake")
    monkeypatch.setattr(gate, "gate", lambda *a, **k: _Good())
    monkeypatch.setattr(conformance, "run", lambda **k: _Bad())
    out = build.verify([build.BuildResult(engines.LYPNING, ok=True, binary=core)])
    assert out.ok is False


# --- the code column, beside the file bytes ----------------------------------


def _row(text: str, engine: str) -> list[str]:
    for line in text.splitlines():
        if line.startswith(engine + " "):
            return line.split()
    raise AssertionError("no %s row in:\n%s" % (engine, text))


def test_the_build_table_reports_code_bytes_beside_file_bytes():
    # The two answer different questions: bytes on disk are what a cold start
    # fetches, code bytes are what the commit added. On Darwin arm64 the Mach-O
    # __TEXT segment is padded to 16,384 B, so a build can grow by 2,384 B of
    # code and not one byte of file — a table with only one of the columns
    # cannot tell those two builds apart.
    r = build.BuildResult(engines.LYPNING, ok=True, size_bytes=834_720,
                          text_bytes=657_700, target="host")
    text = build.report(r)
    assert text.splitlines()[0].split() == ["engine", "target", "bytes",
                                            "code", "blocks", "secs", "status"]
    row = _row(text, engines.LYPNING)
    assert row[2:5] == ["834720", "657700", "7"]


def test_an_unreadable_code_section_reads_unmeasured_and_never_the_file_bytes():
    # A hole, never a zero and never the file size wearing the other column's
    # label: `size(1)` is absent on a machine without the Xcode command line
    # tools, and a silent fallback would make the two columns agree by fiction.
    r = build.BuildResult(engines.LYPNING, ok=True, size_bytes=834_720,
                          text_bytes=None, text_note="unmeasured: no size(1)",
                          target="host")
    row = _row(build.report(r), engines.LYPNING)
    assert row[3] == "unmeasured"
    assert "834720" not in row[3]


def test_a_build_that_produced_nothing_has_no_code_column_either():
    # `-` and not `unmeasured`: nothing was built, so there is no section to
    # have failed to read.
    r = build.BuildResult(engines.LYPNING, ok=False, skipped_reason="cargo not found",
                          unavailable=True)
    row = _row(build.report(r), engines.LYPNING)
    assert row[2:5] == ["-", "-", "-"]


def test_the_measured_code_size_reaches_the_result_object(tmp_path, monkeypatch):
    # Wired through `gate.text_bytes`, so `--json` (which is `asdict`) and the
    # table are reading one measurement rather than two.
    monkeypatch.setattr(build, "_text", lambda p: (4242, ".text only"))
    r = build.BuildResult(engines.LYPNING, ok=True, size_bytes=1, **dict(
        zip(("text_bytes", "text_note"), build._text(tmp_path))))
    assert r.text_bytes == 4242 and r.text_note == ".text only"


def test_the_build_tells_the_crate_which_cpython_it_stands_in_front_of(tmp_path, monkeypatch):
    # A handful of CPython's own messages, and one type name, are not the same
    # on every version `pyproject.toml` supports (`docs/SUBSET.md` §6a), so the
    # crate compiles the reference version in. The value must come from the
    # interpreter `conformance` grades against and the dispatcher falls through
    # to — `engines.find_cpython()` — and NOT from `sys.version_info`, which is
    # whichever python happens to be running this build.
    #
    # Pinned against a STAND-IN rather than against the real interpreter,
    # because comparing `find_cpython()` with `sys.version_info` pins nothing:
    # `find_cpython()` walks $PATH and `uv run --python X` puts X at the head of
    # it, so the two agree under every harness this suite runs on and the
    # assertion would hold just as well for the implementation it exists to
    # forbid. A fake that answers a version no harness can be is the version of
    # this test that can fail.
    fake = tmp_path / "python3"
    fake.write_text("#!/bin/sh\necho 3.99\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(engines, "find_cpython", lambda: fake)
    assert "3.%d" % sys.version_info[1] != "3.99", "the stand-in must not be the harness"
    assert build.reference_python_env() == {"LYPNING_REF_PY": "3.99", "LYPNING_CPYTHON": str(fake)}


def test_the_suite_and_the_engine_it_grades_speak_the_same_cpython(lypning_bin):
    """The harness mistake that reads as an engine defect, said once.

    Every grid takes its oracle from ``sys.executable`` and compares it with
    this binary — which answers the wordings of the CPython it was BUILT for
    (``err::REF_PY_MINOR``, ``docs/SUBSET.md`` §6a). Let those be two versions
    and every version-dependent row fails for the harness's reason while naming
    the engine. Measured 2026-09-12 in a fresh worktree where ``uv run --with
    pytest`` resolved ``requires-python = ">=3.9"`` down to 3.9 against an
    engine built for the host's 3.11: **137 failures**, not one of them an
    engine defect. One loud line is the whole fix.

    The ARTEFACT is asked, not :func:`engines.find_cpython` — find_cpython says
    what the next build would compile in, and a binary built before ``$PATH``
    last moved does not have to agree with it. A binary is a skip only when
    there is none (the fixture's job); one that will not say which CPython it
    speaks cannot be shown to agree with anything, and rebuilding is one line.
    """
    built_for = engines.reference_minor(lypning_bin)
    ours = "3.%d" % sys.version_info[1]
    assert built_for is not None, (
        "%s does not say which CPython it was built to agree with, so this suite cannot "
        "tell whether it agrees with the %s it is graded against (%s). Rebuild it: "
        "`lypning build --rust`." % (lypning_bin, ours, sys.executable))
    assert built_for == ours, (
        "this suite grades with CPython %s (%s) an engine built to answer CPython %s's "
        "wordings (%s). Every version-dependent row will fail for that reason and blame "
        "the engine. Rebuild the engine for this interpreter — `%s -m lypning build "
        "--rust` — or run the suite under CPython %s."
        % (ours, sys.executable, built_for, lypning_bin, sys.executable, built_for))


def test_a_reference_python_that_cannot_answer_sets_nothing(monkeypatch):
    # Never fails a build and never guesses: with no usable answer the variable
    # is absent, `build.rs` asks for itself, and its fallback is the version the
    # crate's tables were read off.
    monkeypatch.setattr(build, "_run", lambda *a, **k: (1, "boom"))
    assert build.reference_python_env() == {}
    monkeypatch.setattr(build, "_run", lambda *a, **k: (0, "3.11.15\n"))
    assert build.reference_python_env() == {}      # "3.11.15" is not "3.<minor>"
    monkeypatch.setattr(build, "_run", lambda *a, **k: (0, "warning: x\n3.13\n"))
    assert build.reference_python_env() == {"LYPNING_REF_PY": "3.13", "LYPNING_CPYTHON": str(engines.find_cpython())}


def test_reference_behavior_probe_reports_the_selected_interpreter():
    import subprocess
    result = subprocess.run([sys.executable, str(paths.RUST_DIR / "reference_probe.py")],
                            capture_output=True, text=True, check=True, timeout=10)
    version, normpath, reverse, iterator, zero_bytes = result.stdout.splitlines()
    assert version == "%d.%d" % sys.version_info[:2]
    assert {normpath, reverse, iterator, zero_bytes} <= {"0", "1"}
    import os.path
    assert normpath == str(int(type(os.path.normpath).__name__ == "builtin_function_or_method"))
    try:
        sorted([2, 1], reverse=None)
    except TypeError:
        assert reverse == "0"
    else:
        assert reverse == "1"
        assert sorted([2, 1], reverse=[0]) == [2, 1]
        assert sorted([2, 1], reverse=[]) == [1, 2]
    try:
        iter([], None)
    except TypeError as exc:
        assert iterator == str(int(str(exc) == "iter(v, w): v must be callable"))
    try:
        result = (-1).to_bytes(0, "big", signed=True)
    except OverflowError:
        assert zero_bytes == "1"
    else:
        assert result == b""
        assert zero_bytes == "0"
