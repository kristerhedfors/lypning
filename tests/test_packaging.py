"""The distribution, not the code.

Everything here fails silently in the ordinary course of events, which is the
only reason it is worth a test file. A `package-data` glob that stops matching
ships a wheel with a hole in it — `lypning build` then fails for a `pip` user on
a missing crate file, three commands after the mistake. A `MANIFEST.in` that
grows a `graft src` ships cargo's `target/` to everyone. And a benchmark that
creeps into CI turns a shared runner's noise into a number somebody quotes.

None of these are visible in a diff of the module they break.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
MANIFEST = ROOT / "MANIFEST.in"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PACKAGE = ROOT / "src" / "lypning"

# tomllib arrived in 3.11 and the floor here is 3.9; `tomli` would be a
# third-party dependency for the sake of three assertions, which is a worse
# trade than running them on the newer half of the matrix.
tomllib = None
if sys.version_info >= (3, 11):
    import tomllib  # noqa: F401  (rebound on purpose, guarded above)

needs_toml = pytest.mark.skipif(tomllib is None, reason="no tomllib before 3.11")


def _project() -> dict:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


@needs_toml
def test_zero_runtime_dependencies() -> None:
    """Invariant 6, pinned where it is stated rather than where it is felt.

    This package installs into the same environment as whatever the agent is
    working on, so one dependency of ours is a version conflict in someone
    else's project. The symptom of breaking it is a resolver error in a
    repository nobody here will ever see.
    """
    cfg = _project()
    assert cfg["project"]["dependencies"] == [], (
        "`pip install lypning` must resolve nothing. This is the invariant; the "
        "extras below are the door it is easy to leave open."
    )

    # An extra is opt-in, so it is not a runtime dependency — but it is one
    # keystroke away from being treated as normal, and every extra added is one
    # more thing a user might install into a shared environment. So the set is
    # an allowlist with a stated reason each, and adding one is a deliberate
    # edit HERE, not a quiet line in pyproject.toml.
    allowed = {
        "dev": "the test suite",
        "docs": "site/build.py only; never imported by the package",
    }
    extras = cfg["project"]["optional-dependencies"]
    unexpected = set(extras) - set(allowed)
    assert not unexpected, (
        "undeclared extra(s) %s — add them here with a reason, or drop them"
        % sorted(unexpected)
    )
    assert all(d.startswith("pytest") for d in extras["dev"])


@needs_toml
def test_every_package_data_glob_matches_a_file() -> None:
    """A glob that matches nothing is an asset that is not in the wheel.

    setuptools does not complain about it: the pattern simply contributes no
    files. The failure surfaces later and elsewhere — `lypning build --rust`
    dying on an absent `Cargo.lock`, `lypning install` on an absent hook script
    — in an environment that has no checkout to compare against.
    """
    patterns = _project()["tool"]["setuptools"]["package-data"]["lypning"]
    empty = [p for p in patterns if not list(PACKAGE.glob(p))]
    assert not empty, "package-data patterns matching no file: %s" % empty


@needs_toml
def test_the_wheel_carries_the_crate_cargo_config() -> None:
    """The one crate file whose absence produces a working, WRONG binary.

    Every other entry in `package-data` fails loudly when it is missing:
    `cargo build` cannot compile without `Cargo.toml` or a `.rs`, so a `pip`
    user gets an error and files a bug. `assets/rust/.cargo/config.toml` is not
    like that. Without it the build succeeds, every gate passes, and the binary
    is 29 KB larger — a whole CheerpX device block (`docs/LYPNING.md` §8) — for
    the sole reason that cargo defaulted back to a position-independent
    executable. Nothing downstream would ever say so.

    It is also the entry most likely to be lost by accident: it names a *dot*
    directory, which every "tidy the globs" pass treats as an editor artefact.
    """
    patterns = _project()["tool"]["setuptools"]["package-data"]["lypning"]
    assert "assets/rust/.cargo/config.toml" in patterns, (
        "the crate's cargo config is not in package-data: a wheel built from "
        "this tree builds a PIE, one device block larger, and says nothing"
    )
    config = PACKAGE / "assets" / "rust" / ".cargo" / "config.toml"
    assert "relocation-model=static" in config.read_text(encoding="utf-8"), (
        "the flag the entry above exists to ship is not in the file"
    )


@needs_toml
def test_the_python_floor_is_stated_once_and_claimed_everywhere() -> None:
    """`requires-python` and the version classifiers must agree.

    A classifier is a promise to an installer that reads metadata and never runs
    the code; CI executes every version it names, so the two lists disagreeing
    means one of them is untested.
    """
    cfg = _project()["project"]
    assert cfg["requires-python"] == ">=3.9"
    claimed = {c.rsplit(" :: ", 1)[1] for c in cfg["classifiers"]
               if c.startswith("Programming Language :: Python :: 3.")}
    assert claimed == {"3.9", "3.10", "3.11", "3.12", "3.13"}


def test_manifest_ships_a_runnable_test_suite() -> None:
    """setuptools takes `tests/test_*.py` by name and leaves `conftest.py`.

    An sdist missing it unpacks to 500+ tests that all error on the first
    fixture, which reads as a broken package rather than a broken manifest.
    """
    text = MANIFEST.read_text()
    assert "recursive-include tests *.py" in text
    assert (ROOT / "tests" / "conftest.py").is_file()


def test_manifest_prunes_every_build_directory() -> None:
    """`include_package_data = true` makes MANIFEST.in a wheel-contents file.

    These three directories are where cargo and the MicroPython make put their
    object files. Nothing names them today; the prunes are what keeps a future
    `graft src` from putting a gigabyte inside `pip install lypning`.
    """
    text = MANIFEST.read_text()
    for d in ("src/lypning/assets/rust/target",
              "src/lypning/assets/micropython/build",
              "src/lypning/assets/micropython/.build"):
        assert re.search(r"^prune %s$" % re.escape(d), text, re.M), d


@pytest.mark.skipif(not WORKFLOW.is_file(), reason="no .github/ in this tree (an sdist)")
def test_ci_runs_the_deterministic_half_only() -> None:
    """`lypning bench` may never become a CI step.

    A wall-clock benchmark on a shared runner measures the runner: the arms are
    1-2 ms apart and a noisy neighbour moves them further than a regression
    would. `bench` prints a banner when it detects CI, but a banner in a log
    nobody reads is not a defence — the defence is that no step invokes it.
    """
    steps = [ln.strip() for ln in WORKFLOW.read_text().splitlines()
             if not ln.lstrip().startswith("#")]
    offenders = [ln for ln in steps if re.search(r"\blypning bench\b", ln)]
    assert not offenders, "bench is not a CI gate: %s" % offenders


@pytest.mark.skipif(not WORKFLOW.is_file(), reason="no .github/ in this tree (an sdist)")
def test_ci_executes_the_floor_it_claims() -> None:
    """3.9 is enforced by running the suite on it, not by asserting it.

    A `match` statement or a runtime `X | Y` compiles cleanly on every version
    CI would otherwise test, and fails at import on the one it does not.
    """
    text = WORKFLOW.read_text()
    assert re.search(r'python:\s*\[.*"3\.9".*\]', text)


@needs_toml
def test_no_extra_is_imported_by_the_package() -> None:
    """The allowlist above is only true while nothing under `src/lypning/`
    imports what an extra installs.

    An extra that the package imports is a runtime dependency wearing a
    disguise: it works on the machine that ran `pip install .[docs]` and raises
    ImportError on every other one. `markdown` and `pygments` are build-time
    tools for the docs site, which lives outside the package for exactly this
    reason.
    """
    forbidden = {"markdown", "pygments"}
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        for mod in forbidden:
            if re.search(r"^\s*(import %s|from %s\b)" % (mod, mod), src, re.M):
                offenders.append("%s imports %s" % (path.name, mod))
    assert not offenders, offenders
