"""The stdlib unit corpus, differentially tested against live CPython.

The invariant: **every file in ``training/stdlib/units`` is a deterministic
program CPython runs, it agrees with the module it says it replaces, and the
engine label written next to it is the one the binaries actually give it.**

The units are pure Python in the engines' subset, so they run under CPython
directly, which is what makes most of this file cheap enough for every CI leg.
Nothing here is asserted from memory: the reference half of every comparison is
produced by importing the real module and running the unit's own cases through
it (:data:`_REF_DRIVER`), the same trick ``tests/test_shims.py`` plays on the
frozen shim tree. Reasoning about what CPython does is exactly how a silent
wrong answer gets into training data, and a wrong answer in a corpus a model
reproduces INLINE is a wrong answer with no engine left to catch it.

Four layers, cheapest first, and each one catches something the next cannot:

1. **Shape.** :func:`pipeline.stdlib.parse_unit` on every file, plus the two
   structural rules the parser does not enforce — the helpers are definitions
   only, checked statically *and* by running them alone and requiring silence.
2. **CPython.** Every unit exits 0 and prints something. No engine binary
   needed; this is the gate that runs everywhere.
3. **Determinism.** Every unit twice, in fresh interpreters, under a different
   hash seed and from a different working directory, requiring byte-identical
   stdout. A clock, a pid, a temp path or a set iteration order that leaked into
   an output dies here rather than in a ``git diff`` six weeks later.
4. **The reference.** The unit's cases run once against its own helpers and once
   against the real module's same-named attributes. Bytes must match, unless the
   unit is one of the few in :data:`_DIVERGENCES` — reviewed, pinned with the
   input that shows it, and declared in the unit's own docstring as well. And
   the reference arm must reach the END of the case block: comparing the lines
   it HAS is worth nothing for the lines it never got to, which is how 310 case
   lines across five units stayed unverified while this file was green
   (:func:`test_the_reference_arm_ran_every_case`, measured 2026-09-17).

The engine-dependent layer — labels, mismatches, row stability — is SKIPPED, not
failed, when the binaries are absent, the way every other path in this tree
degrades when ``lypning-mp`` is not built (CLAUDE.md, "The oracle is absent by
default"). A contributor without a Rust toolchain still gets layers 1-4.

Two normalizations are applied to the reference side, and only two, both for
things the subset **cannot express** rather than things it got wrong: an
iterator becomes a list (no ``yield``, so a unit returns a materialised list),
and a tuple subclass becomes a plain tuple (no ``class``, so a unit can never
build a ``SplitResult``). Anything else is compared exactly as the case printed
it. See :data:`_REF_DRIVER`.
"""

from __future__ import annotations

import ast
import builtins
import importlib
import io
import json
import os
import subprocess
import tokenize
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import pytest

from lypning import engines as lyp
from pipeline import stdlib

# --- the tree -----------------------------------------------------------------

TRAINING = Path(__file__).resolve().parents[1]
UNITS_DIR = TRAINING / "stdlib" / "units"

#: The verified rows for those units, committed so the corpus is readable
#: and mergeable without a Rust toolchain. Section 9 is the only thing that
#: reads it, and the only thing that can notice it has gone stale.
SEED_CORPUS = TRAINING / "data" / "stdlib" / "stdlib.jsonl"

#: Passed IN, never read from the clock. ``first_seen`` is an argument to every
#: row builder for exactly this reason: a library that called ``date.today()``
#: would rewrite every row it re-verified and the workflow ends in
#: ``git diff --exit-code`` (``training/STDLIB.md`` §3, "Where the verified rows
#: live").
FIRST_SEEN = "2026-09-16"

if not UNITS_DIR.is_dir():  # pragma: no cover - a checkout without the corpus
    pytest.skip("no unit corpus at %s" % UNITS_DIR, allow_module_level=True)

_UNIT_PATHS: Tuple[Path, ...] = tuple(stdlib.unit_paths(UNITS_DIR))
_UNIT_IDS: Tuple[str, ...] = tuple(p.stem for p in _UNIT_PATHS)

#: Every unit, by name. Collected at import so the parametrised tests carry the
#: unit's name as the test id and a failure names the file without reading a
#: traceback.
unit_case = pytest.mark.parametrize("name", _UNIT_IDS, ids=_UNIT_IDS)

#: Units whose cases, run against the real module, do NOT agree byte for byte —
#: measured, then reviewed, then written down with the input that shows it.
#: This table, and nothing else, is what exempts a unit from
#: :func:`test_unit_agrees_with_its_reference`. An exemption read out of the
#: docstring instead would be an exemption driven by prose: most units here use
#: the word "divergence" somewhere, usually to say a surface has none, and the
#: differential would switch itself off for every one of them.
#:
#: Checked in both directions, which is what keeps it from becoming a list of
#: waivers: a unit outside it must agree byte for byte, and a unit inside it
#: must still disagree AND still declare the divergence in its own docstring.
#: Adding a name here is a review, never a fix — a unit that differs from
#: CPython by accident is a bug in the unit (CLAUDE.md invariant 1), and the
#: entry has to name an input a reader can run.
#:
#: A divergence a unit DECLARES but no case reaches does not belong here either,
#: and ``binascii_hex`` is the worked example: its docstring declares that an
#: explicit ``sep=None`` diverges, and for a while a helper reached it, which
#: killed the reference run at case 28 of 71 and left the other 43 compared
#: against nothing. The entry that admitted it would have kept them that way.
#: The call site was the bug; the table is not where a unit's own cases go to
#: stop being checked.
_DIVERGENCES: Dict[str, str] = {
    "hashlib_digest": "pbkdf2_hmac('bogus', b'p', b's', 1): a ValueError "
                      "carrying hashlib.new's message here; CPython fails in "
                      "the OpenSSL layer and its own answer is not one answer "
                      "-- a plain ValueError saying 'unsupported hash type' on "
                      "3.9, and UnsupportedDigestmodError saying '[digital "
                      "envelope routines] unsupported' from 3.11",
    "itertools_accumulate": "exception TYPE on an unexpected keyword: "
                            "accumulate([1, 2], bogus=1) is ValueError here, "
                            "TypeError in CPython",
    "itertools_chain": "exception TYPE on three inputs: islice('ABCDE') and "
                       "islice('ABCDE', 0, 1, 1, 1) (a wrong argument COUNT, "
                       "not a bad index) and zip_longest('ab', bogus=1) are "
                       "ValueError here, TypeError in CPython",
    "itertools_product": "exception TYPE on an unexpected keyword: "
                         "product([1], bogus=1) is ValueError here, "
                         "TypeError in CPython",
    "struct_pack": "exception TYPE, ValueError here for every failure: "
                   "pack('<Q', 2**64) is struct.error in CPython, and "
                   "calcsize('<\\xb2i') is UnicodeEncodeError -- two CPython "
                   "types this port flattens into one",
    "struct_unpack": "exception TYPE, same reason and the same two CPython "
                     "types: unpack('<i', b'abc') is struct.error, "
                     "calcsize('<\\xb2i') is UnicodeEncodeError",
}

#: The word a docstring uses to declare a divergence. One token, case-folded, so
#: "DIVERGENCE", "divergences" and "diverges further" all count. Deliberately
#: weak, and only ever asked of a unit already in :data:`_DIVERGENCES`: it
#: cannot tell a declaration from a mention, so it can confirm that a reviewed
#: divergence reached the docstring — where the model reads it
#: (``training/stdlib/README.md``, "The three commands": "Byte-identical, or the
#: divergence goes in the docstring") — and it can never grant an exemption.
_DECLARES = "diverg"

#: Units whose reference arm is allowed to print FEWER lines than the unit arm —
#: measured, then reviewed, then written down with the reason it cannot be
#: removed. Empty is the natural state and the only state that needs no
#: argument: a short reference run means the real module stopped part way
#: through the unit's own cases, and every case below that point was compared
#: against nothing at all. :func:`test_the_reference_arm_ran_every_case` is the
#: check; this table, and nothing else, exempts a unit from it.
#:
#: An entry here is NOT the same kind of thing as a :data:`_DIVERGENCES` entry,
#: and the distinction is the whole point. A divergence entry says "these two
#: lines differ, and here is the input"; it is about one line and leaves the
#: rest of the unit under test. An entry here says "the comparison stops here
#: and does not resume", which costs coverage of every case beneath it — so it
#: must name what those cases are and why they cannot be reached any other way.
#: A short run caused by a diverging case is not one of those: move the case to
#: the end of the case block, or widen what the unit's capture helper catches.
#: Both are fixes in the unit, both keep the divergence declared, and neither
#: needs a line here.
#:
#: Checked in both directions, like :data:`_DIVERGENCES`: a unit outside it must
#: run its reference arm to the end, and a unit inside it must still stop short
#: — an allowance nobody can demonstrate is an allowance held open for the next
#: short run.
_SHORT_REFERENCE_RUNS: Dict[str, str] = {}


# --- the reference differential -----------------------------------------------
#
# Written to the scratch dir and run once per mode per unit, in a fresh
# interpreter each time. A raw string because it is Python source, not a Python
# value.

_REF_DRIVER = r'''
import sys, io, json, types, functools, importlib, tokenize

SEP = "# --- cases ---"
path, mode, reference = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path, encoding="utf-8").read()

# The separator is a COMMENT token at column 0, which is how
# `pipeline.stdlib.parse_unit` finds it. A line scan would also find one quoted
# inside a docstring, cut the file there, and leave every helper below the cut
# -- and then the rebinding loop further down would find no functions to rebind,
# and this whole differential would pass by doing nothing. A check that can turn
# itself off silently is worse than no check, so the two readers of this format
# agree on what a comment is.
starts = [0]
at = text.find("\n")
while at != -1:
    starts.append(at + 1)
    at = text.find("\n", at + 1)
seps = [tok.start[0] for tok in tokenize.generate_tokens(io.StringIO(text).readline)
        if tok.type == tokenize.COMMENT and tok.start[1] == 0
        and tok.string.rstrip() == SEP]
if len(seps) != 1:
    sys.exit("%s: expected exactly one %r line, found %d" % (path, SEP, len(seps)))
helpers = text[:starts[seps[0] - 1]]
cases = text[starts[seps[0]]:] if seps[0] < len(starts) else ""


def settle(value, depth=0):
    """The two things the subset cannot express, normalised away. Nothing else.

    A unit has no ``yield`` and no ``class``, so it can never hand back an
    iterator or a namedtuple however right its values are. Comparing those
    shapes would report a divergence the corpus is forbidden to fix, and every
    real divergence would then be buried under them. Values themselves are
    untouched: a float, a bytes, a dict, an exception message all arrive here
    exactly as the module produced them.
    """
    if depth > 3:
        return value
    if isinstance(value, tuple):        # SplitResult -> a plain tuple
        return tuple(settle(v, depth + 1) for v in value)
    if isinstance(value, (str, bytes, bytearray, list, dict, set, frozenset)):
        return value
    if hasattr(value, "__next__") and hasattr(value, "__iter__"):
        return [settle(v, depth + 1) for v in value]    # islice(...) -> a list
    return value


_WRAPPED = {}


def wrap(fn):
    """``fn`` with its result settled, and the SAME wrapper every time.

    Memoised on identity because ``bisect.insort is bisect.insort_right`` is a
    fact a case prints, and two wrappers around one function would answer False
    — a divergence this driver invented.
    """
    if id(fn) in _WRAPPED:
        return _WRAPPED[id(fn)]

    def inner(*a, **k):
        return settle(fn(*a, **k))

    try:
        functools.update_wrapper(inner, fn)
    except (AttributeError, TypeError):
        pass
    _WRAPPED[id(fn)] = inner
    return inner


buf = io.BytesIO()
wrapper = io.TextIOWrapper(buf, encoding="utf-8", newline="", write_through=True)
real_stdout = sys.stdout
sys.stdout = wrapper
env = {"__name__": "__main__", "__file__": path}
rebound, err = [], ""
try:
    exec(compile(helpers, path, "exec"), env)
    if mode == "reference":
        module = importlib.import_module(reference)
        for name in sorted(env):
            if name.startswith("_"):
                continue
            if not isinstance(env[name], types.FunctionType):
                continue        # only what the unit itself defined
            target = getattr(module, name, None)
            if target is None or not callable(target):
                continue
            env[name] = wrap(target)
            rebound.append(name)
    exec(compile(cases, path, "exec"), env)
except BaseException as exc:      # SystemExit included: a case that exits early
    err = "%s: %s" % (type(exc).__name__, exc)
finally:
    try:
        wrapper.flush()
    except Exception:
        pass
    sys.stdout = real_stdout
    wrapper.detach()              # leave the BytesIO open

# Hex, not text: the comparison downstream is between BYTES, and a decode here
# would apply universal-newline translation and hide a CRLF divergence — the
# reason pipeline.stdlib keys everything on stdout_raw.
real_stdout.write(json.dumps(
    {"out": buf.getvalue().hex(), "rebound": rebound, "err": err}))
'''


class Reference:
    """One unit's two runs, or the reason there is only one."""

    def __init__(self, unit_out: bytes, ref_out: bytes, rebound: Tuple[str, ...],
                 unit_err: str, ref_err: str) -> None:
        self.unit_out = unit_out
        self.ref_out = ref_out
        self.rebound = rebound
        self.unit_err = unit_err
        self.ref_err = ref_err

    @property
    def agrees(self) -> bool:
        return self.unit_out == self.ref_out and self.unit_err == self.ref_err

    @property
    def unit_lines(self) -> int:
        """Lines the unit's own cases printed. The number to match."""
        return len(self.unit_out.splitlines())

    @property
    def ref_lines(self) -> int:
        """Lines the same cases printed through the real module.

        Fewer than :attr:`unit_lines` means the reference arm died part way
        down the case block, and the cases below the death were never compared
        with anything — see :func:`test_the_reference_arm_ran_every_case`.
        """
        return len(self.ref_out.splitlines())

    @property
    def ref_last_line(self) -> bytes:
        """The last line the reference arm managed, or ``b""``.

        Bytes, not text, for the reason the whole comparison is in bytes: a
        decode here would apply newline translation to the one line a reader
        uses to find the case that killed the run.
        """
        lines = self.ref_out.splitlines()
        return lines[-1] if lines else b""

    def report(self, name: str) -> str:
        """One failure with both sides, rendered — never printed (invariant 8)."""
        lines = ["%s: the unit and the real module disagree" % name,
                 "  rebound: %s" % (", ".join(self.rebound) or "(nothing)")]
        if self.unit_err or self.ref_err:
            lines.append("  unit raised:      %s" % (self.unit_err or "(nothing)"))
            lines.append("  reference raised: %s" % (self.ref_err or "(nothing)"))
        a, b = self.unit_out, self.ref_out
        i = 0
        while i < min(len(a), len(b)) and a[i] == b[i]:
            i += 1
        lo = max(0, i - 40)
        lines.append("  %d vs %d bytes, first differ at %d" % (len(a), len(b), i))
        lines.append("  unit:      %r" % (a[lo:i + 60],))
        lines.append("  reference: %r" % (b[lo:i + 60],))
        return "\n".join(lines)


# --- spawning -----------------------------------------------------------------


def _child_env(**extra: str) -> Dict[str, str]:
    """This process's environment plus the overrides, with capture off.

    ``LYPNING_CAPTURE=0`` for the reason :func:`lypning.engines.child_env` sets
    it: a nested capture would log this suite's own spawns back into the corpus
    the pipeline is built from.
    """
    env = dict(os.environ)
    env["LYPNING_CAPTURE"] = "0"
    env.update(extra)
    return env


def _spawn(python: str, script: Path, cwd: Path, env: Dict[str, str],
           args: Sequence[str] = ()) -> "subprocess.CompletedProcess":
    """One fresh interpreter. Bytes on both pipes, stdin closed, no bytecode.

    ``-B`` because ``training/stdlib/units`` is tracked source reviewed in a
    diff, and a ``__pycache__`` dropped into it is bytes the contributor did not
    have before a test ran (CLAUDE.md §7). Stdin is closed rather than inherited:
    a unit that read stdin would otherwise block forever against a terminal.
    """
    return subprocess.run(
        [python, "-B", str(script)] + [str(a) for a in args],
        cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300, check=False,
    )


def _sandbox(root: Path, arm: str, name: str) -> Path:
    """``<root>/<arm>/<name>``, made. Every run gets its own cwd.

    The same net the corpus battery runs behind (CLAUDE.md §4): a unit is not
    supposed to touch the filesystem at all, and one that does writes here
    rather than into the checkout, where the next run would read it back and the
    determinism check would pass for the wrong reason.
    """
    d = root / arm / name
    d.mkdir(parents=True, exist_ok=True)
    return d


# --- fixtures -----------------------------------------------------------------


@pytest.fixture(scope="session")
def cpython() -> str:
    """The real CPython, never the capture shim.

    Through :func:`pipeline.stdlib.resolve_cpython`, which walks past anything
    carrying the shim marker: a suite that measured the shim would be measuring
    a shell script.
    """
    found = stdlib.resolve_cpython()
    if not found:  # pragma: no cover - a host with no python3 cannot run pytest
        pytest.skip("no real CPython found to be the oracle")
    return found


@pytest.fixture(scope="session")
def units() -> Dict[str, stdlib.Unit]:
    """Every unit that parses, by name. Malformed files are absent, not fatal.

    A file that will not parse is one finding, reported once by
    :func:`test_unit_parses`; raising from a session fixture would instead error
    every test in the file with a stack naming pytest's fixture machinery.
    """
    out: Dict[str, stdlib.Unit] = {}
    for path in _UNIT_PATHS:
        try:
            out[path.stem] = stdlib.parse_unit(str(path), path.read_text(encoding="utf-8"))
        except (stdlib.UnitError, OSError, UnicodeDecodeError):
            continue
    return out


def _unit(units: Dict[str, stdlib.Unit], name: str) -> stdlib.Unit:
    unit = units.get(name)
    if unit is None:
        pytest.skip("%s does not parse — see test_unit_parses" % name)
    return unit


@pytest.fixture(scope="session")
def cpython_runs(cpython: str, tmp_path_factory) -> Dict[str, Tuple[Any, Any]]:
    """Every unit run TWICE under CPython, in two deliberately different worlds.

    Arm ``a`` gets ``PYTHONHASHSEED=0`` and its own directory; arm ``b`` gets a
    different seed and a different directory. Both are what a unit would have to
    survive to be reproducible material, and each targets one leak: the seed
    catches an output that depends on ``set`` or ``dict`` iteration order built
    from strings, the directory catches a ``getcwd`` or a temp path in an output.
    Session-scoped because these two spawns per unit are the cost of this file
    and three tests ask the same question of them.
    """
    root = tmp_path_factory.mktemp("stdlib-cpython")
    out: Dict[str, Tuple[Any, Any]] = {}
    for path in _UNIT_PATHS:
        name = path.stem
        a_dir, b_dir = _sandbox(root, "a", name), _sandbox(root, "b", name)
        script_a, script_b = a_dir / path.name, b_dir / path.name
        source = path.read_text(encoding="utf-8")
        script_a.write_text(source, encoding="utf-8")
        script_b.write_text(source, encoding="utf-8")
        out[name] = (
            _spawn(cpython, script_a, a_dir, _child_env(PYTHONHASHSEED="0")),
            _spawn(cpython, script_b, b_dir, _child_env(PYTHONHASHSEED="12345")),
        )
    return out


@pytest.fixture(scope="session")
def helper_runs(cpython: str, units: Dict[str, stdlib.Unit],
                tmp_path_factory) -> Dict[str, Any]:
    """The helpers of every unit, run ALONE. Inlining them must be silent."""
    root = tmp_path_factory.mktemp("stdlib-helpers")
    out: Dict[str, Any] = {}
    for name, unit in units.items():
        d = _sandbox(root, "helpers", name)
        script = d / (name + ".py")
        script.write_text(unit.helpers, encoding="utf-8")
        out[name] = _spawn(cpython, script, d, _child_env())
    return out


@pytest.fixture(scope="session")
def references(cpython: str, units: Dict[str, stdlib.Unit],
               tmp_path_factory) -> Dict[str, Reference]:
    """Every unit's cases, run against its own helpers and against the real module.

    Units whose ``# reference:`` is ``-`` are absent: they fill a language gap,
    so there is no module to be the other side of a differential, and an invented
    one would be a claim rather than a measurement. They are still covered by the
    CPython and determinism layers.
    """
    root = tmp_path_factory.mktemp("stdlib-reference")
    driver = root / "refdriver.py"
    driver.write_text(_REF_DRIVER, encoding="utf-8")
    out: Dict[str, Reference] = {}
    for name, unit in units.items():
        if not unit.reference:
            continue
        results: Dict[str, Dict[str, Any]] = {}
        for mode in ("unit", "reference"):
            d = _sandbox(root, mode, name)
            script = d / (name + ".py")
            script.write_text(unit.source, encoding="utf-8")
            proc = _spawn(cpython, driver, d, _child_env(PYTHONHASHSEED="0"),
                          args=(script, mode, unit.reference))
            if proc.returncode != 0 or not proc.stdout:
                # Held, not raised: a driver that will not start is one finding,
                # and it belongs to the unit it failed on rather than to every
                # test that happens to want this fixture.
                results[mode] = {
                    "out": "", "rebound": [],
                    "err": "the %s driver failed: exit %d: %s"
                           % (mode, proc.returncode,
                              proc.stderr.decode("utf-8", "replace")[-500:]),
                }
                continue
            results[mode] = json.loads(proc.stdout.decode("utf-8"))
        out[name] = Reference(
            unit_out=bytes.fromhex(results["unit"]["out"]),
            ref_out=bytes.fromhex(results["reference"]["out"]),
            rebound=tuple(results["reference"]["rebound"]),
            unit_err=results["unit"]["err"],
            ref_err=results["reference"]["err"],
        )
    return out


@pytest.fixture(scope="session")
def engine_map() -> Dict[str, str]:
    """Every engine in the spectrum, or a skip naming the one that is missing.

    A missing engine is not a failure here. ``lypning status`` degrades to
    ``not built``, the oracle is absent by default, and every path touching a
    binary in this tree answers with a status line rather than a zero
    (CLAUDE.md, "The oracle is absent by default"). A label taken with a cheaper
    engine unasked would understate what a unit needs, so the whole layer waits
    rather than guessing — ``pipeline.stdlib.label_unit`` rejects that case for
    the same reason.
    """
    found, missing = stdlib.resolve_engines()
    if missing:
        pytest.skip("engine not built: %s — run `lypning build --rust`"
                    % ", ".join(missing))
    return found


@pytest.fixture(scope="session")
def verification(cpython: str, engine_map: Dict[str, str]) -> stdlib.Verification:
    """One full verify pass over the tree: CPython, then each engine cheapest-first."""
    return stdlib.verify_units(list(_UNIT_PATHS), cpython, engine_map,
                               first_seen=FIRST_SEEN)


@pytest.fixture(scope="session")
def reverification(cpython: str, engine_map: Dict[str, str]) -> stdlib.Verification:
    """A SECOND, independent pass. Two answers to one question, on purpose."""
    return stdlib.verify_units(list(_UNIT_PATHS), cpython, engine_map,
                               first_seen=FIRST_SEEN)


# --- 0. the tree itself -------------------------------------------------------


def test_the_corpus_is_not_empty() -> None:
    """Every other test here is parametrised over the directory.

    An empty directory would make this whole file pass by collecting nothing,
    which is the one way a test suite lies without a single assertion failing.
    """
    assert _UNIT_PATHS, "no unit files under %s" % UNITS_DIR


def test_unit_names_and_ids_are_unique(units: Dict[str, stdlib.Unit]) -> None:
    """Two files, two units. A shared id means one source committed twice.

    ``pipeline.stdlib.assemble`` de-duplicates by id and reports a collision, so
    a duplicate does not corrupt the corpus — it silently halves it, and "how
    many units" then answers differently before and after a merge.
    """
    by_id: Dict[str, List[str]] = {}
    for name, unit in units.items():
        by_id.setdefault(stdlib.unit_id(unit.source), []).append(name)
    collisions = {i: sorted(n) for i, n in by_id.items() if len(n) > 1}
    assert not collisions, "units with the same source id: %s" % collisions
    assert len(units) == len(_UNIT_PATHS), (
        "%d of %d unit files did not parse — see test_unit_parses"
        % (len(_UNIT_PATHS) - len(units), len(_UNIT_PATHS)))


# --- 1. shape -----------------------------------------------------------------


@unit_case
def test_unit_parses(name: str) -> None:
    """The format is a contract, and this is the whole of it.

    ``parse_unit`` enforces the docstring, the two headers, the single separator
    and a non-empty case block; the assertions below re-state the rules
    ``training/STDLIB.md`` §2 lists under "The rules the parser enforces", so a
    parser that stopped enforcing one is a failure here rather than a silently
    looser corpus.

    Re-stated from ``tokenize``, not from ``splitlines``, for the reason
    :func:`pipeline.stdlib._comments` gives: a ``#`` inside a string is not a
    comment. A line scan here would disagree with the parser on a unit that
    documents the format it is written in — which the parser deliberately
    allows — and would fail a legal unit rather than catch an illegal one.
    """
    path = UNITS_DIR / (name + ".py")
    text = path.read_text(encoding="utf-8")
    try:
        unit = stdlib.parse_unit(str(path), text)
    except stdlib.UnitError as exc:
        pytest.fail(str(exc))
    assert unit.doc.strip(), "%s: the docstring is empty" % name
    assert unit.fills, "%s: '# fills:' is empty" % name
    assert all(f.strip() for f in unit.fills), "%s: a '# fills:' name is blank" % name
    comments = [(tok.start[1], tok.string) for tok in
                tokenize.generate_tokens(io.StringIO(text).readline)
                if tok.type == tokenize.COMMENT]
    assert any(col == 0 and c.startswith("# reference:") for col, c in comments), (
        "%s: no '# reference:' header" % name)
    separators = [c for col, c in comments
                  if col == 0 and c.rstrip() == stdlib.UNIT_SEPARATOR]
    assert len(separators) == 1, (
        "%s: expected exactly one %r line, found %d"
        % (name, stdlib.UNIT_SEPARATOR, len(separators)))
    assert unit.helpers.strip(), "%s: nothing above the separator" % name
    assert unit.cases.strip(), "%s: nothing below the separator" % name


@unit_case
def test_helpers_are_definitions_only(name: str, units: Dict[str, stdlib.Unit]) -> None:
    """Above the separator: definitions. The static half of "no side effects".

    A unit is INLINED into whatever program needs it, so every top-level
    statement in its helpers becomes a statement in that program. A loop, a
    call, an ``if`` or a ``print`` up here would run in the caller's program at a
    moment the caller never chose. A constant table is fine — including one folded
    from a literal expression, which costs the caller nothing but a value.
    """
    unit = _unit(units, name)
    allowed = (ast.FunctionDef, ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign)
    offenders = []
    for i, node in enumerate(ast.parse(unit.helpers).body):
        if i == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue                      # the module docstring
        if not isinstance(node, allowed):
            offenders.append("line %d: %s" % (node.lineno, type(node).__name__))
    assert not offenders, (
        "%s: top-level statements above the separator that are not definitions: %s"
        % (name, "; ".join(offenders)))


@unit_case
def test_inlining_the_helpers_is_silent(name: str, helper_runs: Dict[str, Any]) -> None:
    """The measured half: run the helpers ALONE and require nothing at all.

    Static shape cannot see what a top-level ``_TABLE = _build()`` does, and the
    rule that matters is not "no calls" but "no output and no failure". So the
    helpers are executed on their own: exit 0, empty stdout, empty stderr. That
    also proves they stand up without the cases below them — a helper reaching
    forward to a name the case block defines is a unit nobody can inline.
    """
    proc = helper_runs.get(name)
    if proc is None:
        pytest.skip("%s does not parse — see test_unit_parses" % name)
    assert proc.returncode == 0, (
        "%s: the helpers alone exited %d:\n%s"
        % (name, proc.returncode, proc.stderr.decode("utf-8", "replace")[-2000:]))
    assert proc.stdout == b"", (
        "%s: the helpers printed %r before any case ran"
        % (name, proc.stdout[:200]))
    assert proc.stderr == b"", (
        "%s: the helpers wrote to stderr: %r" % (name, proc.stderr[:200]))


@unit_case
def test_fills_names_exist_on_cpython(name: str, units: Dict[str, stdlib.Unit]) -> None:
    """Every ``# fills:`` name resolves on the live interpreter.

    The header is the unit's claim about which CPython surface it replaces, and
    it is the column the plan de-duplicates targets on. A typo there costs a
    generation round chasing a name nothing has, and it is a claim about CPython
    — so it is checked against CPython rather than against a list written here.
    """
    unit = _unit(units, name)
    missing = [target for target in unit.fills if not _resolves(target)]
    assert not missing, (
        "%s: '# fills:' names that do not exist on this CPython: %s"
        % (name, ", ".join(missing)))


def _resolves(dotted: str) -> bool:
    """True when ``dotted`` names something reachable from a module or a builtin.

    ``urllib.parse.quote`` is a module attribute, ``dict.fromkeys`` is an
    attribute of a builtin type, ``pow`` is a builtin. The longest importable
    prefix wins, then attributes.
    """
    parts = [p for p in dotted.split(".") if p]
    if not parts:
        return False
    obj: Any = None
    rest: List[str] = []
    for i in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:i]))
        except Exception:
            continue
        rest = parts[i:]
        break
    if obj is None:
        if not hasattr(builtins, parts[0]):
            return False
        obj, rest = getattr(builtins, parts[0]), parts[1:]
    for attr in rest:
        if not hasattr(obj, attr):
            return False
        obj = getattr(obj, attr)
    return True


# --- 2. CPython ---------------------------------------------------------------


@unit_case
def test_unit_runs_on_cpython(name: str, cpython_runs: Dict[str, Tuple[Any, Any]]) -> None:
    """CPython is the oracle. A unit it rejects is never a corpus row.

    No engine binary is needed, which is what makes this gate runnable on every
    CI leg and on a contributor's machine with no Rust toolchain. Stdout must be
    non-empty too: a unit that prints nothing has cases that check nothing, and
    the determinism and reference layers would both pass on it vacuously.
    """
    first, _second = cpython_runs[name]
    assert first.returncode == 0, (
        "%s: cpython exited %d:\n%s"
        % (name, first.returncode, first.stderr.decode("utf-8", "replace")[-2000:]))
    assert first.stdout, "%s: printed nothing — the cases check nothing" % name


# --- 3. determinism -----------------------------------------------------------


@unit_case
def test_unit_is_deterministic(name: str, cpython_runs: Dict[str, Tuple[Any, Any]]) -> None:
    """Twice, in fresh interpreters, byte for byte.

    The two runs differ in hash seed and in working directory, which is what
    makes this more than a repeat: a set or a string-keyed dict printed in
    iteration order answers differently under a different seed, and a ``getcwd``
    or a temp path in an output answers differently from a different directory.
    A clock, a pid or an address differs between any two runs at all.

    Compared as BYTES on both sides. Decoding first would apply universal-newline
    translation and hide a CRLF divergence, which is the whole reason
    ``pipeline.stdlib`` keys on ``stdout_raw``.
    """
    first, second = cpython_runs[name]
    assert first.returncode == second.returncode, (
        "%s: exited %d then %d" % (name, first.returncode, second.returncode))
    if first.stdout != second.stdout:
        a, b = first.stdout, second.stdout
        i = 0
        while i < min(len(a), len(b)) and a[i] == b[i]:
            i += 1
        lo = max(0, i - 40)
        pytest.fail(
            "%s: two runs printed different bytes — something leaked into the "
            "output (a clock, a pid, a temp path, or set iteration order).\n"
            "  %d vs %d bytes, first differ at %d\n  run a: %r\n  run b: %r"
            % (name, len(a), len(b), i, a[lo:i + 60], b[lo:i + 60]))


# --- 4. the reference ---------------------------------------------------------


@unit_case
def test_unit_agrees_with_its_reference(name: str, units: Dict[str, stdlib.Unit],
                                        references: Dict[str, Reference]) -> None:
    """The unit's cases, run through the real module. Bytes, or a declaration.

    This is ``tests/test_shims.py`` aimed at the unit tree: the right answer is
    not written down here, it is produced by importing the module the unit names
    and running the unit's own cases against it. Every public function the unit
    defines whose name the module also defines is rebound to the module's; the
    cases then exercise CPython's implementation through the unit's own call
    sites, with the unit's own arguments.

    A divergence is allowed, but only one that is in :data:`_DIVERGENCES` —
    reviewed once, by a human, and pinned there with the input that shows it.
    The exemption is driven by that table and NOT by what the docstring says,
    because a docstring is prose: most units here use the word "divergence"
    somewhere, usually only to say a surface has none, and a test that looked for
    the word would switch itself off for every one of them. A unit that quietly
    differs teaches the difference as if it were CPython.

    So: a unit outside the table must agree byte for byte, and a unit inside it
    must still disagree AND still declare it in its docstring (`training/stdlib/
    README.md`, "The three commands" — "Byte-identical, or the divergence goes
    in the docstring"), because the docstring is the part a model reads. An
    entry whose divergence closed fails here rather than rotting.
    """
    unit = _unit(units, name)
    if not unit.reference:
        pytest.skip("%s fills a language gap ('# reference: -'), so there is no "
                    "module to differ from" % name)
    ref = references[name]
    if name not in _DIVERGENCES:
        if ref.agrees:
            return
        pytest.fail(
            "%s: it differs from the real `%s` and no reviewed entry in "
            "_DIVERGENCES says it may. Either the unit is wrong — fix the unit "
            "— or the divergence is real, in which case it goes in the unit's "
            "docstring, where a model will read it, AND in _DIVERGENCES with "
            "the input that demonstrates it.\n\n%s"
            % (name, unit.reference, ref.report(name)))
    assert not ref.agrees, (
        "%s: _DIVERGENCES claims it differs from the real `%s`, and it no "
        "longer does. The limit closed — drop the entry here and the note in "
        "the unit's docstring." % (name, unit.reference))
    assert _DECLARES in unit.doc.lower(), (
        "%s: _DIVERGENCES pins a divergence its docstring never mentions (%s). "
        "The table is for the reviewer; the docstring is for the model, and a "
        "model only ever reads the unit." % (name, _DIVERGENCES[name]))


@unit_case
def test_the_reference_arm_ran_every_case(name: str, units: Dict[str, stdlib.Unit],
                                          references: Dict[str, Reference]) -> None:
    """The reference arm printed as many lines as the unit arm. Length, not content.

    :func:`test_unit_agrees_with_its_reference` compares the two arms and is
    satisfied when the lines it HAS agree. It never asks whether the reference
    arm got as far as the unit arm did, and for a unit in :data:`_DIVERGENCES`
    it cannot: that table's whole job is to accept a disagreement, and "the
    reference arm died at case 12 and printed nothing for cases 13-240" is a
    disagreement like any other. So an entry written to excuse ONE diverging
    line silently bought silence for every line beneath it.

    That is not hypothetical. On 2026-09-17, before this test existed, five
    units ran their reference arm into an uncaught exception and left 310 case
    lines compared against nothing:

    ====================  =========  ==============  ==================================
    unit                  unit arm   reference arm   what killed the reference arm
    ====================  =========  ==============  ==================================
    ``struct_pack``             240              44  ``struct.error`` past ``ValueError``
    ``struct_unpack``           126              42  ``struct.error`` past ``ValueError``
    ``itertools_chain``          76              62  ``TypeError`` past ``ValueError``
    ``itertools_product``        42              33  ``TypeError`` past ``ValueError``
    ``itertools_accumulate``     67              60  ``TypeError`` past ``ValueError``
    ====================  =========  ==============  ==================================

    Each of those units declares a real exception-TYPE divergence — it cannot
    ``raise struct.error``, because ``class E(Exception)`` refuses on both
    engines, so it raises ``ValueError`` — and each caught only ``ValueError``
    when capturing the message. In the unit arm that works; in the reference arm
    the real module raises the real type, which escapes and takes the rest of
    the case block with it.

    Two honest fixes, both of which this test accepts: move the diverging cases
    to the END of the case block, so the abort happens after everything else has
    been compared, or widen the capture so the reference arm survives the line.
    Neither weakens anything — the divergence itself is still declared, still in
    :data:`_DIVERGENCES`, and still fails
    :func:`test_unit_agrees_with_its_reference` if it closes.

    Counted in LINES rather than bytes on purpose: a length in bytes would move
    whenever a case's output changed and would report a content divergence,
    which is the other test's question. This one asks only "did every case
    run", so it stays quiet about everything else and cannot be silenced by the
    table that silences that one.
    """
    unit = _unit(units, name)
    if not unit.reference:
        pytest.skip("%s fills a language gap ('# reference: -'), so there is no "
                    "module to run the cases through" % name)
    ref = references[name]
    reviewed = _SHORT_REFERENCE_RUNS.get(name)
    if ref.unit_lines == ref.ref_lines:
        assert reviewed is None, (
            "%s: _SHORT_REFERENCE_RUNS says its reference arm stops early (%s), "
            "and it no longer does — both arms print %d lines. Drop the entry: "
            "an allowance nobody can demonstrate is an allowance for the next "
            "short run instead." % (name, reviewed, ref.unit_lines))
        return
    if reviewed is not None:
        return
    detail = (
        "  unit arm:      %d lines\n"
        "  reference arm: %d lines  (%+d)\n"
        "  the last line the reference arm managed: %r\n"
        "  it then raised: %s"
        % (ref.unit_lines, ref.ref_lines, ref.ref_lines - ref.unit_lines,
           ref.ref_last_line, ref.ref_err or "(nothing — it stopped without an "
           "exception, so look for a case that returns or exits early)"))
    pytest.fail(
        "%s: the reference arm stopped short, so every case line below it was "
        "compared against nothing.\n%s\n\n"
        "The usual cause is a message-capturing helper that catches only the "
        "type the UNIT raises: the real `%s` raises a different one, which "
        "escapes and ends the run. Fix it in the unit, either by moving the "
        "diverging cases to the end of the case block or by widening the "
        "capture — not by adding a name to _SHORT_REFERENCE_RUNS, which is for "
        "a short run that cannot be removed at all and has to be argued."
        % (name, detail, unit.reference))


def test_short_reference_runs_are_still_short_and_still_in_the_corpus(
        references: Dict[str, Reference]) -> None:
    """The whole-table twin, for what a parametrised test cannot see.

    The same shape as :func:`test_declared_divergences_have_not_quietly_closed`,
    and for the same reason: an entry naming a unit that has left the corpus is
    never reached by the per-unit test above, so it would sit here forever,
    reading as a reviewed fact about a file nobody can open.
    """
    gone = sorted(n for n in _SHORT_REFERENCE_RUNS if n not in references)
    assert not gone, (
        "_SHORT_REFERENCE_RUNS names units that are not in the corpus any more "
        "(or that name no reference module): %s" % ", ".join(gone))


def test_the_reference_differential_really_rebinds(
        units: Dict[str, stdlib.Unit], references: Dict[str, Reference]) -> None:
    """The one failure mode that would make the layer above a lie.

    If nothing were ever rebound, both arms would run the unit's own helpers and
    every unit would "agree" with CPython by comparing the unit with itself. So
    the binding is asserted, not assumed: the differential must reach a majority
    of the units that name a real module, and the ones it cannot reach are named
    with their reason rather than discovered later.

    A unit the differential cannot bind is not unchecked — layers 1-3 run on it,
    :func:`test_fills_names_exist_on_cpython` checks the surface it claims, and
    the engines label it. It is only unchecked against the module's behaviour,
    which is a real hole and is why it is counted here.
    """
    with_reference = sorted(n for n, u in units.items() if u.reference)
    assert with_reference, "no unit names a reference module"
    bound = [n for n in with_reference if references[n].rebound]
    unbound = [n for n in with_reference if not references[n].rebound]
    assert bound, (
        "the reference differential rebound NOTHING anywhere: every unit above "
        "was compared with itself. The driver, not the corpus, is broken.")
    assert len(bound) * 2 >= len(with_reference), (
        "the reference differential reaches only %d of %d units that name a "
        "module (%s bind nothing) — most of the corpus is being compared with "
        "itself" % (len(bound), len(with_reference), ", ".join(unbound)))


def test_declared_divergences_have_not_quietly_closed(
        references: Dict[str, Reference]) -> None:
    """A written-down limit that is no longer a limit is a stale document.

    The whole-table twin of :func:`test_unit_agrees_with_its_reference`, which
    asks the same question one unit at a time. This one adds what a parametrised
    test cannot see: an entry naming a unit that has left the corpus, which would
    otherwise sit here unexercised and unnoticed.
    """
    closed = sorted(n for n in _DIVERGENCES
                    if n in references and references[n].agrees)
    assert not closed, (
        "_DIVERGENCES names units that now AGREE with their reference: %s. The "
        "limit closed — drop the entry here and the note in the unit's "
        "docstring." % ", ".join(closed))
    gone = sorted(n for n in _DIVERGENCES if n not in references)
    assert not gone, (
        "_DIVERGENCES names units that are not in the corpus any more: %s"
        % ", ".join(gone))


# --- 5. the closed kinds ------------------------------------------------------


@unit_case
def test_unit_does_not_target_a_closed_kind(name: str, units: Dict[str, stdlib.Unit],
                                            request) -> None:
    """``engines.ONLY_CPYTHON_REFUSALS``, imported live, over every unit.

    Each kind in that set is a claim that no reimplementation short of CPython
    gets the construct right. That makes them the one thing this corpus must
    never contain, because a unit over a closed kind fails QUIETLY: a
    pure-Python ``math.log`` agrees on the twelve values its cases print and
    diverges in the last place on the thirteenth. Every other unit here fails
    loudly when it is wrong — invariant 1 applied to the corpus.

    The DECLARED half — the leading segment of each ``# fills:`` name — runs
    everywhere. The MEASURED half — the refusal a naive ``import <reference>``
    actually got — needs the binaries, so it joins in when they are there and is
    simply absent when they are not, rather than turning into a skip that would
    take the declared half down with it.
    """
    unit = _unit(units, name)
    label = _label_for(name, request)
    violation = stdlib.closed_kind_violation(unit, label, lyp.ONLY_CPYTHON_REFUSALS)
    assert not violation, "%s: %s" % (name, violation)


_NO_MEASUREMENT = stdlib.Label(requires="", requires_static="", route_agrees=False,
                               runs=(), mismatch="", naive_kind="", naive_detail="")


def _label_for(name: str, request) -> stdlib.Label:
    """The measured label if the binaries are here, else one with nothing measured.

    Asked through ``request.getfixturevalue`` so that the absence of an engine
    costs this test its measured half and nothing else; taking ``verification``
    as an ordinary argument would make the whole test skip.
    """
    try:
        verified = request.getfixturevalue("verification")
    except pytest.skip.Exception:
        return _NO_MEASUREMENT
    return verified.labels.get(name, _NO_MEASUREMENT)


# --- 6. the row bytes ---------------------------------------------------------


def test_row_bytes_are_stable_without_any_engine(units: Dict[str, stdlib.Unit]) -> None:
    """Build the rows twice from the same units and the same ``first_seen``.

    Pure library, no binary: this is the serialisation itself under test — fixed
    key order, ``sort_keys=False``, ``ensure_ascii=False``, compact separators,
    one trailing newline. ``.github/workflows/training.yml`` ends in
    ``git diff --exit-code``, so a row that re-serialises differently turns a
    green pipeline red for a corpus that did not change.
    """
    ordered = [units[n] for n in sorted(units)]
    label = stdlib.Label(requires="lypning", requires_static="lypning",
                         route_agrees=True, runs=(), mismatch="",
                         naive_kind="module", naive_detail="import x",
                         caps=("cap-re",))
    first = stdlib.rows_text(
        [stdlib.unit_row(u, label, "authored", FIRST_SEEN) for u in ordered])
    second = stdlib.rows_text(
        [stdlib.unit_row(u, label, "authored", FIRST_SEEN) for u in ordered])
    assert first.encode("utf-8") == second.encode("utf-8")
    assert first.endswith("\n") and not first.endswith("\n\n")
    for line in first.splitlines():
        row = json.loads(line)
        assert list(row) == list(stdlib.ROW_KEYS), (
            "row keys are out of contract order: %s" % list(row))


def test_a_row_is_identical_after_a_round_trip(units: Dict[str, stdlib.Unit]) -> None:
    """``json.loads`` then :func:`pipeline.stdlib.row_line` gives the same bytes.

    ``ensure_ascii=False`` keeps UTF-8 as UTF-8 and several units print
    non-ASCII on purpose. A row that re-serialised differently after a read is a
    corpus that cannot be merged and rewritten — which is exactly what
    ``stdlib-assemble`` does.
    """
    label = stdlib.Label(requires="lypning", requires_static="lypning",
                         route_agrees=True, runs=(), mismatch="",
                         naive_kind="", naive_detail="")
    assert units, (
        "no unit parsed, so this test round-tripped nothing and passed by "
        "checking nothing — see test_unit_parses")
    for name in sorted(units):
        line = stdlib.row_line(stdlib.unit_row(units[name], label, "authored", FIRST_SEEN))
        assert stdlib.row_line(json.loads(line)) == line, "%s does not round-trip" % name


def test_verifying_twice_writes_the_same_bytes(verification: stdlib.Verification,
                                               reverification: stdlib.Verification) -> None:
    """Two independent verify passes, one corpus. This is the ``git diff`` step.

    Engine-gated, and the strongest form of the same promise as the test above:
    not merely that the serialiser is stable, but that the whole pass — three
    spawns per unit, a router query, a naive-import probe — lands on the same
    bytes the second time. Anything that reached for a clock, a temp path or a
    dict built from a set would differ here.
    """
    first = stdlib.rows_text(verification.rows).encode("utf-8")
    second = stdlib.rows_text(reverification.rows).encode("utf-8")
    assert first == second, (
        "two verify passes over the same tree wrote different bytes — "
        "`git diff --exit-code` at the end of the workflow would fail on a "
        "corpus nobody changed")
    assert stdlib.rows_text(verification.drops) == stdlib.rows_text(reverification.drops)


# --- 7. the engines -----------------------------------------------------------


@unit_case
def test_unit_labels_to_a_real_engine(name: str, verification: stdlib.Verification) -> None:
    """Every unit is run by one of the Rust engines, and none of them mismatches.

    ``requires == "cpython"`` is a recorded gap, never a shipped row: a unit no
    engine runs is reference material for a program that cannot use it. A
    MISMATCH is worse and is fatal — an engine that exited 0 with different bytes
    than CPython is a wrong answer wearing the shape of an answer, and the fix is
    never to relabel around it (invariant 1).
    """
    label = verification.labels.get(name)
    if label is None:
        pytest.skip("%s does not parse — see test_unit_parses" % name)
    assert not label.mismatch, "%s: %s" % (name, label.mismatch)
    assert label.requires in stdlib.SPECTRUM, (
        "%s: no engine ran it (requires=%s) — a recorded gap, not a corpus row"
        % (name, label.requires))


def test_no_unit_is_dropped(verification: stdlib.Verification) -> None:
    """The whole tree in one line, so a failure reads as a list and not as 34.

    The drops ledger is the pass's own account of what it refused to ship, with
    a reason per entry. A committed unit that lands in it is a defect in the
    unit, and reading them together is how the shape of the defect shows up.
    """
    assert not verification.drops, "\n".join(
        "%-26s %-16s %s" % (d["name"], d["reason"], d["detail"][:180])
        for d in verification.drops)
    assert len(verification.rows) == len(_UNIT_PATHS)


@unit_case
def test_label_is_reproducible(name: str, verification: stdlib.Verification,
                               reverification: stdlib.Verification) -> None:
    """The same unit, labelled twice by the same binaries, gets the same label.

    The label is the column this corpus exists for — it says which engine will
    run what a model reproduces inline. A label that moved between two passes
    over an unchanged tree would be a coin flip written into training data.
    """
    first = verification.labels.get(name)
    second = reverification.labels.get(name)
    if first is None or second is None:
        pytest.skip("%s does not parse — see test_unit_parses" % name)
    assert (first.requires, first.caps, first.naive_kind, first.requires_static) == \
           (second.requires, second.caps, second.naive_kind, second.requires_static), (
        "%s: labelled %s%s then %s%s"
        % (name, first.requires, list(first.caps), second.requires, list(second.caps)))


# --- 8. the label column ------------------------------------------------------


def test_the_label_distribution_is_not_degenerate(
        verification: stdlib.Verification) -> None:
    """At least one unit on each engine. A constant column is not labelled data.

    The point of the corpus is that a model learns which engine runs what it
    reproduces, and that is only learnable if the answer varies. An all-``lypning``
    corpus teaches "always the core", which is wrong the moment a unit needs
    ``re`` or an integer past 64 bits; an all-``lypning-l`` corpus teaches the
    opposite and sends every unit to the wider binary for nothing. Either way the
    column is a constant and the label has stopped being a label — this test is
    what stops that happening one accepted unit at a time.
    """
    seen: Dict[str, List[str]] = {}
    for row in verification.rows:
        seen.setdefault(row["requires"], []).append(row["name"])
    missing = [e for e in stdlib.SPECTRUM if not seen.get(e)]
    assert not missing, (
        "no unit is labelled %s — the label column is %s for every row, which is "
        "not labelled data. Add a unit that needs it (%s adds re, collections, "
        "pathlib, csv, glob, base64, hashlib and bigint over the core)."
        % (", ".join(missing), sorted(seen), stdlib.SPECTRUM[-1]))
    assert sum(len(v) for v in seen.values()) == len(verification.rows)


def test_every_capability_named_is_one_the_engine_offers(
        verification: stdlib.Verification) -> None:
    """``caps`` is drawn from the variant's own table, so it cannot invent one.

    A cap is the corpus's answer to "why does this unit need the wider binary",
    and an answer the binary does not recognise is worse than no answer: it reads
    as a fact and routes nothing.
    """
    strays = []
    for row in verification.rows:
        offered = set(lyp.VARIANT_CAPS.get(row["requires"], ()))
        for cap in row["caps"]:
            if cap not in offered:
                strays.append("%s: %s is not offered by %s"
                              % (row["name"], cap, row["requires"]))
        if row["requires"] == stdlib.SPECTRUM[0]:
            assert not row["caps"], (
                "%s runs on the core engine but claims %s" % (row["name"], row["caps"]))
    assert not strays, "\n".join(strays)


# --- 9. the committed seed corpus ---------------------------------------------
#
# `training/data/stdlib/stdlib.jsonl` is the verified rows for the units in this
# tree, committed so a reader (and the assemble stage) has the corpus without
# building a Rust binary first. That makes it the one artifact here that can go
# stale silently: a unit edited without re-running the writer leaves a row whose
# `helpers`, `cases` and `source_sha256` describe a file that no longer exists.
# Both checks below exist because nothing else in this file reads that path.


def test_the_committed_seed_corpus_tracks_the_units(units: Dict[str, stdlib.Unit]) -> None:
    """Names and source hashes, engine-free, so this runs on every CI leg.

    A hash comparison, not a text one: it catches the whole staleness class
    (a unit added, removed or edited since the writer last ran) without needing
    an engine to say which binary runs it. The byte-exact check is the
    engine-gated test below.
    """
    if not SEED_CORPUS.is_file():
        pytest.fail(
            "no committed seed corpus at %s. Write it with `stdlib-verify "
            "--units %s --out %s --first-seen %s --producer authored --strict`."
            % (SEED_CORPUS, UNITS_DIR, SEED_CORPUS, FIRST_SEEN))
    rows = [json.loads(line) for line in
            SEED_CORPUS.read_text(encoding="utf-8").splitlines() if line]
    committed = {str(r["name"]): str(r["source_sha256"]) for r in rows}
    assert len(committed) == len(rows), "the committed corpus repeats a name"
    on_disk = {name: stdlib.source_sha256(u.source) for name, u in units.items()}
    missing = sorted(set(on_disk) - set(committed))
    extra = sorted(set(committed) - set(on_disk))
    changed = sorted(n for n in set(on_disk) & set(committed)
                     if on_disk[n] != committed[n])
    assert not (missing or extra or changed), (
        "%s is stale — re-run `stdlib-verify` over %s and commit the result.\n"
        "  units with no row:   %s\n"
        "  rows with no unit:   %s\n"
        "  source changed since: %s"
        % (SEED_CORPUS, UNITS_DIR, ", ".join(missing) or "(none)",
           ", ".join(extra) or "(none)", ", ".join(changed) or "(none)"))


def test_the_committed_seed_corpus_is_what_a_verify_writes(
        verification: stdlib.Verification) -> None:
    """The committed bytes, against a verify pass run right now.

    Engine-gated, because the label in each row is a measurement and there is
    nothing honest to compare it with when the binaries are absent. It is the
    same promise as ``test_verifying_twice_writes_the_same_bytes``, extended
    over time rather than over one session: the file in the tree is what this
    tree's units verify to, or it is a document about an older tree.
    """
    if not SEED_CORPUS.is_file():  # reported once, by the test above
        pytest.skip("no committed seed corpus at %s" % SEED_CORPUS)
    fresh = stdlib.rows_text(verification.rows).encode("utf-8")
    committed = SEED_CORPUS.read_bytes()
    assert fresh == committed, (
        "%s is not what verifying %s writes today (%d vs %d bytes). Re-run the "
        "writer and commit, or find out what changed the labels."
        % (SEED_CORPUS, UNITS_DIR, len(committed), len(fresh)))
