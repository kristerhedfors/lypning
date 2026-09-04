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
from pathlib import Path

import pytest

from lypning import build, engines, paths


# --- the tree the shell script derives ---------------------------------------


def test_the_script_and_the_engine_tree_are_siblings_in_a_checkout():
    # build-micropython.sh derives its engine tree as `<script>/../micropython`.
    # That derivation IS the interface, so it is asserted rather than trusted:
    # handed a tree under a name it does not look for, the build dies at "no
    # patches in micropython/variant/patches" with no hint as to why.
    script, tree, note = build._micropython_workdir()
    assert script.parent.parent / "micropython" == tree
    assert tree == paths.MICROPYTHON_DIR
    assert script.is_file() and tree.is_dir()
    # A checkout copies nothing: the asset tree is already where the script
    # looks, which is what keeps `make` by hand and `lypning build` sharing one
    # musl and one MicroPython checkout.
    assert note == ""


def test_the_wheel_path_copies_both_halves_keeping_the_layout(tmp_path, monkeypatch):
    # The path a pip user actually hits and the one nobody tests by accident:
    # assets are read-only, so the tree AND the script move under build_dir(),
    # and they have to keep the same relative positions or the derivation above
    # points back into site-packages.
    dest = tmp_path / "build"
    monkeypatch.setattr(paths, "package_is_writable", lambda: False)
    monkeypatch.setattr(paths, "build_dir", lambda: dest)

    script, tree, note = build._micropython_workdir()
    assert tree == dest / "micropython"
    assert script == dest / "scripts" / "build-micropython.sh"
    assert script.parent.parent / "micropython" == tree
    assert script.is_file() and (tree / "variant").is_dir()
    assert os.access(script, os.X_OK)
    assert str(dest) in note

    # The binaries land inside the copied tree, which is where the finders look.
    assert build.stock_binary() is None  # nothing built there yet
    assert engines.find_micropython() is None


def test_the_staging_tree_the_old_workaround_left_behind_is_removed(tmp_path, monkeypatch):
    # `mp-stage` was a symlink farm that existed only because the script derived
    # its tree from a name the asset never had. The script is fixed; a stale
    # symlink into the asset tree is worse than dead weight the day that moves.
    stage = paths.ensure_dir(paths.state_dir() / "mp-stage")
    (stage / "scripts").mkdir()
    build._micropython_workdir()
    assert not stage.exists()


# --- the benchmark control ---------------------------------------------------


def test_the_host_build_is_this_machines_engine_and_installs_unsuffixed():
    # `lypning build --target host` used to install as `lypning-host`, a name no
    # finder looked for, so a darwin host had a built engine and no engine.
    assert build._runs_here("host")
    assert build._runs_here("")
    assert not build._runs_here("i686-unknown-linux-musl") or __import__("platform").machine() == "i686"


def test_each_variant_is_one_cargo_feature_and_its_own_target_dir(monkeypatch):
    assert build.variant_feature(engines.LYPNING) == "variant-m"
    with pytest.raises(ValueError):
        build.variant_feature("lypning-l")          # not on the spectrum yet
    monkeypatch.setattr(engines, "SPECTRUM", ("lypning", "lypning-l"))
    assert build.variant_feature("lypning-l") == "variant-l"
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
    monkeypatch.setattr(engines, "SPECTRUM", ("lypning", "lypning-l"))
    results = build.build_all(rust=True, micropython=False, target="host", dry_run=True)
    rust = [r.engine for r in results if r.engine in engines.SPECTRUM]
    assert rust == ["lypning", "lypning-l"]
    results = build.build_all(rust=True, micropython=False, target="host", dry_run=True, variant="lypning-l")
    assert [r.engine for r in results if r.engine in engines.SPECTRUM] == ["lypning-l"]


def test_the_control_is_not_an_engine_name():
    # CLAUDE.md §9: there are exactly three engine strings. The control is a
    # fourth binary, and naming it after one of them is how a bench arm ends up
    # comparing stock against stock and reporting 1.00x as a clean result.
    assert build.STOCK_BINARY not in engines.ENGINE_ORDER


def test_stock_binary_is_absent_until_it_is_built(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "build_dir", lambda: tmp_path)
    assert build.stock_binary() is None

    control = tmp_path / "micropython" / "build" / build.STOCK_BINARY
    control.parent.mkdir(parents=True)
    control.write_bytes(b"\x7fELF fake")
    os.chmod(control, 0o755)
    assert build.stock_binary() == control.resolve()


def test_an_override_names_the_control_explicitly(tmp_path, monkeypatch):
    elsewhere = tmp_path / "control"
    elsewhere.write_bytes(b"\x7fELF fake")
    os.chmod(elsewhere, 0o755)
    monkeypatch.setenv("LYPNING_STOCK_BIN", str(elsewhere))
    assert build.stock_binary() == elsewhere.resolve()


def test_the_control_is_never_installed_into_the_engine_bin_dir(tmp_path):
    # The bin dir is what engines.find_* reads. A control that can be found is a
    # control that can be run, and then a program gets answered by unpatched
    # upstream MicroPython.
    fake = tmp_path / "micropython-stock"
    fake.write_bytes(b"\x7fELF fake")
    core = tmp_path / "lypning"
    core.write_bytes(b"\x7fELF fake")
    installed = build.install_binaries([
        build.BuildResult(build.STOCK_BINARY, ok=True, binary=fake),
        build.BuildResult(engines.LYPNING, ok=True, binary=core),
    ])
    assert [p.name for p in installed] == [engines.LYPNING]
    assert not (paths.bin_dir() / build.STOCK_BINARY).exists()


def test_the_pin_is_read_out_of_the_script_rather_than_restated():
    # An entry in docs/BENCH-LEDGER.md claims both binaries came from one
    # commit. That claim has to come from the file that does the checking out.
    pin = build.micropython_pin()
    text = (paths.SCRIPTS_DIR / "build-micropython.sh").read_text(encoding="utf-8")
    assert pin["tag"] and pin["commit"]
    assert 'MPY_TAG="%s"' % pin["tag"] in text
    assert 'MPY_COMMIT="%s"' % pin["commit"] in text


def test_a_stock_dry_run_asks_the_script_for_the_control():
    result = build.build_stock(dry_run=True)
    if not result.dry_run:
        pytest.skip("no i386 toolchain here: %s" % result.skipped_reason)
    assert result.engine == build.STOCK_BINARY
    assert "--stock" in result.log
    assert build.STOCK_BINARY in result.log
    # A dry run is neither built nor broken.
    assert not result.ok and "dry run" in result.skipped_reason


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


def test_verify_leaves_the_control_alone_and_says_so(tmp_path, monkeypatch):
    from lypning import conformance, gate

    monkeypatch.setattr(gate, "gate", lambda *a, **k: pytest.fail("the control was gated"))
    monkeypatch.setattr(conformance, "run", lambda **k: None)
    monkeypatch.setattr(engines, "find_cpython", lambda: None)

    control = tmp_path / build.STOCK_BINARY
    control.write_bytes(b"\x7fELF fake")
    out = build.verify([build.BuildResult(build.STOCK_BINARY, ok=True, binary=control)])
    assert out.gates == []
    assert any("control" in n for n in out.notes)
    # No reference CPython is a missing measurement, not a pass to be assumed.
    assert any("battery was not run" in n for n in out.notes)
    assert out.ok is True and out.conformance is None


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
