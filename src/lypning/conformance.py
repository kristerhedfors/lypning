"""The conformance battery: the one number that must be zero.

Every corpus program is run twice — once by the real CPython, which is the
reference by definition, and once by each engine — and the two are compared on
stdout, exit code, and what stderr says happened. Each engine's result is one of
three things:

  ``MATCH``        stdout, exit code, and the exception (if either arm raised)
                   identical to CPython. stdout is identical as BYTES: what the
                   process wrote, before any decode. A grader that compared
                   decoded text was comparing two strings Python had already run
                   universal-newline translation over, and could not see a
                   line-ending disagreement on either side (issue #50). Of
                   stderr, what is compared is the exception TYPE and the fact
                   of raising, plus the non-traceback part, which is the
                   program's own writing; the traceback body and the message
                   wording are not, because CPython rewords them between 3.11,
                   3.12 and 3.14 (:func:`stderr_shape`). Every verdict records
                   which of the three it actually rested on
                   (:attr:`Verdict.compared`), because an agreement about an
                   exit code and an agreement about a page of output are not
                   the same fact and must not print as one number.
  ``UNSUPPORTED``  exit 90 with a ``<engine>: unsupported: <kind>: <detail>``
                   line on stderr. **Not a failure.** It is coverage, and
                   :func:`plan` turns it into the build order.
  ``MISMATCH``     anything else. Always a failure.

The asymmetry is the whole point: **a subset runtime that silently disagrees
with CPython is worse than no runtime at all**, because the agent that typed the
one-liner will not notice. A refusal it will notice, because the answer comes
from CPython instead and costs one extra spawn.

Two invariants this module exists to hold, beyond the verdicts themselves.

**Nothing the corpus does may touch the repository.** The corpus is harvested
from real agent sessions, so it is full of programs that rewrite ``src/`` and
``docs/`` — running it is running an agent's edit history. Every program gets
its own :func:`tempfile.mkdtemp` cwd (and a *separate* one per engine, so the
second engine cannot read back what the first wrote), every program naming an
absolute path is skipped rather than run, and the whole battery is bracketed by
a snapshot of the work tree — git's status *and* a digest and a copy of every
file already dirty — that restores and reports anything that changed anyway.
Digests rather than the set of dirty paths, because the set cannot see a second
change to a file that was already modified, which is the state every developer
runs this in. That last one is a net, not a sandbox: it cannot undo a write outside
the repository, it only makes the next occurrence loud. It is here because the
first measurement runs on the upstream project rewrote 34 tracked files and the
escape route was never pinned down — which is precisely the argument for a net.

**A timeout is never scored as a disagreement.** The reference run and the
engine runs share one deadline, so a program that is simply slow times out on
both sides and is dropped from the measurement instead of being recorded as an
engine that printed the wrong thing.
"""

from __future__ import annotations

import ast
import copy
import functools
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import corpus
from . import engines as eng
from . import paths
from .engines import CPYTHON, LIBRARY, LYPNING, MICROPYTHON

MATCH = "MATCH"
UNSUPPORTED = "UNSUPPORTED"
MISMATCH = "MISMATCH"

#: The dispatcher, measured end to end as a synthetic fourth engine. It is not
#: a tier — it *uses* the tiers — but it is the arm a caller of ``lypning run``
#: actually experiences, so it is the arm CI gates on.
MIXTURE = "mixture"
#: The same chain walked by the OTHER dispatcher — `lypning run`, the Rust one
#: users actually exec — measured as its own arm so the two can be held to each
#: other over the corpus. Until this arm existed the battery graded a dispatcher
#: nobody runs and the benchmark timed one nothing graded.
MIXTURE_RUST = "mixture-rust"

#: The same lypning, reached through the C ABI in this process instead of
#: through a spawn. It is measured as its own arm for one reason: the
#: interpreter is shared with the ``lypning`` arm but the plumbing around it —
#: the in-process exit path, the captured streams, the injected stdin and argv —
#: is a SECOND implementation of the refusal contract, and invariant 2 says that
#: contract has only ever broken silently. If this arm and the ``lypning`` arm
#: ever disagree, one of the two is wrong and the corpus is what says so.
#:
#: Not in :data:`DEFAULT_ARMS`, exactly like the MicroPython tier is not: the
#: library is optional, and an absent one is a missing arm, never a failure.

VERDICTS = (MATCH, UNSUPPORTED, MISMATCH)

#: The arms measured when the caller names none, cheapest first. CPython is the
#: reference and would trivially match itself, so it is not an arm by default.
DEFAULT_ARMS = tuple(eng.SPECTRUM) + (MIXTURE,)

#: Arms that are measured only when asked for by name. The oracle is here for
#: the reason LIBRARY is: it needs a build most machines cannot do (a 32-bit
#: toolchain and a network), and it grades a question — "what does a second
#: reimplementation get wrong?" — that is not "did the chain answer correctly".
OPT_IN_ARMS = tuple(eng.ORACLES) + (LIBRARY, MIXTURE_RUST)

DEFAULT_TIMEOUT = 30.0

# stdout is kept on a Verdict only when it is evidence — a MISMATCH — and even
# then clipped. One harvested entry prints 18,454 lines; holding three copies of
# it per arm across 839 entries turns a report into a memory hazard.
_STDOUT_CLIP = 8192


# --- what cannot be compared -------------------------------------------------

# Programs that interrogate the INTERPRETER rather than compute something. By
# construction these can never match: an engine IS a different executable and is
# required not to claim a CPython version (docs/SUBSET.md). Reporting them as
# MISMATCH would permanently accuse an engine of a bug for behaving correctly,
# and — the expensive part — train the reader to expect a non-zero MISMATCH
# count, which is how a real divergence gets waved through.
#
# Matched against the program text rather than a hand-applied tag, because these
# arrive automatically from capture and nobody will tag them.
_INTERPRETER_SPECIFIC = tuple(re.compile(p) for p in (
    r"\bsys\s*\.\s*(?:version|version_info|executable|implementation|path|prefix"
    r"|base_prefix|maxsize|byteorder|flags)\b",
    # The strongest member of the class, not the weakest: it names the modules
    # that interpreter ships, and two conformant CPythons disagree — 305 names
    # on 3.11, 293 on 3.12. No corpus can pin it.
    r"\bsys\s*\.\s*stdlib_module_names\b",
    r"\bplatform\s*\.\s*\w+",
    r"\bdir\s*\(",
    r"\bos\s*\.\s*uname\b",
    r"__file__|__spec__|__loader__",
))

# Programs that ask about THIS RUN rather than about a computation: the wall
# clock, a process id, the inode behind a file descriptor, an unseeded PRNG. Two
# runs of the SAME interpreter disagree on these, so comparing them would fail
# forever and bury the real signal — `os.fstat(1).st_ino` prints the inode of
# whichever pipe this run was handed, and `datetime.now()` is never twice the
# same. The exit code still proves the program executed.
_RUN_SPECIFIC = tuple(re.compile(p) for p in (
    r"\b(?:datetime|date)\s*\.\s*(?:now|today|utcnow|utctimetuple)\b",
    r"\btime\s*\.\s*(?:time|time_ns|monotonic|monotonic_ns|perf_counter|perf_counter_ns"
    r"|process_time|process_time_ns|ctime|asctime|localtime|gmtime)\b",
    r"\bos\s*\.\s*(?:getpid|getppid|urandom|times|fstat|cpu_count|getcwd|getlogin)\b",
    # `os.path.getsize` and its siblings are NOT here: whether they are
    # run-specific depends on WHICH file, which is a question about the program
    # rather than about a name in it. :func:`stats_an_ambient_file` answers it.
    # A subprocess's output belongs to the environment it ran in. The corpus
    # holds a probe that spawns python3 three hundred times with
    # PYTHONHASHSEED deliberately REMOVED to count both set orders — its own
    # reference drifts run to run, which is the definition of this list.
    r"\bsubprocess\s*\.\s*(?:run|Popen|check_output|check_call|call)\b",
    r"\bst_(?:ino|dev|mtime|atime|ctime|nlink)\b",
    # `random` is handled in `is_run_specific` — unseeded it belongs here,
    # seeded it is CPython's Mersenne Twister and tier 1 reproduces it.
    r"\bsecrets\s*\.\s*\w+",
    r"\buuid\s*\.\s*uuid[14]\b",
    r"\btempfile\s*\.\s*(?:mkdtemp|mkstemp|NamedTemporaryFile|TemporaryDirectory|gettempdir)\b",
    # The address in a default repr, and the identity it comes from.
    r"\bid\s*\(",
))

# The one class that must be matched against the program TEXT, quotes and all,
# because what it is about IS the quoted path. The CONTENT of the harness's own
# live state is the run's: the capture log under ~/.lypning grows on every
# python spawn — including the reference spawn the battery itself just made —
# and the transcripts under ~/.claude grow as the session running the battery
# types. A program that counts records in either can never match a reference
# taken a moment earlier; on 2026-09-05 one such program (py-627dabb6be55) read
# 7180 distinct commands for the reference and 7181 for the arm, both from
# CPython. Only the two harness directories are named: a program reading any
# other file under ~ is graded, because that file is not ours to grow.
#
# The residual is accepted knowingly: one of these paths quoted inside a
# docstring still buys a waiver, because there is no way to ask about a path
# without reading the quotes. It is the smallest possible carve-out from the
# rule above it and it is one pattern wide.
_RUN_SPECIFIC_LITERAL = tuple(re.compile(p) for p in (
    r"""(?:expanduser\(\s*['"]~/\.(?:lypning|claude)\b"""
    r"""|Path\.home\(\)\s*/\s*['"]\.(?:lypning|claude)\b"""
    r"""|\$HOME/\.(?:lypning|claude)\b"""
    r"""|HOME['"]\]\s*\+\s*['"]/\.(?:lypning|claude)\b)""",
))

# Quantities Python itself declines to specify, so two conformant
# implementations may legitimately disagree. This class must stay SMALL and each
# member must be justified by a written standard rather than by convenience —
# the temptation is to silence a real divergence by declaring it unspecified.
_IMPLEMENTATION_DEFINED = tuple(re.compile(p) for p in (
    # DEFLATE (RFC 1951) constrains the stream, never its length: 12 bytes under
    # CPython's zlib, 11 under MicroPython's deflate. Both are valid.
    r"len\s*\(\s*(?:zlib|gzip)\s*\.\s*compress",
))


# --- reading the program, not the text of it ---------------------------------
#
# Every table above is a regex over the program's SOURCE, and a regex over
# source cannot tell a call from a quotation. This corpus is one-liners captured
# from agent sessions: it is full of programs that rewrite files and quote code
# they never execute — `subprocess.run` inside a patch body, `__file__` inside a
# triple-quoted string, `datetime.now` inside a docstring. Every one of those
# bought a waiver, and a waiver means stdout is not compared at all. Measured on
# this tree on 2026-09-07 over 3,688 loaded: 634 programs waived on the text,
# 582 on the AST — 52 whose stdout nothing had ever looked at.
#
# So the tables are matched against a CODE VIEW instead — the program parsed and
# printed back with every string and bytes literal blanked. A name inside a
# string is gone; a name inside an f-string's `{...}` survives, because that one
# really is evaluated. The text is only fallen back to when the program does not
# parse, which is a real corpus population (captured shell fragments, truncated
# heredocs) and is reported by :func:`waiver_basis` rather than hidden.


class _BlankLiterals(ast.NodeTransformer):
    """Every string/bytes constant replaced by an empty one of the same type.

    The tree keeps its shape, so ``ast.unparse`` still renders every call and
    attribute exactly where it was; what disappears is the contents of the
    quotes, which is the only place a false waiver has ever come from.
    """

    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        if isinstance(node.value, bytes):
            return ast.copy_location(ast.Constant(value=b""), node)
        if isinstance(node.value, str):
            return ast.copy_location(ast.Constant(value=""), node)
        return node


#: Serialises the one call that has to touch a global. `ast.parse` runs the
#: tokenizer, which raises `SyntaxWarning` for things like `"\d"` — advisory,
#: about a corpus program rather than about us, and invariant 8 says library
#: code does not print. `catch_warnings` swaps `warnings.filters` process-wide,
#: so it is held under a lock; the parses it serialises are microseconds each
#: and memoised, while every other thread here is waiting on a subprocess.
_PARSE_LOCK = threading.Lock()


@functools.lru_cache(maxsize=4096)
def _tree(src: str) -> Optional[ast.AST]:
    """The program's AST, or None when it does not parse under this interpreter."""
    try:
        with _PARSE_LOCK, warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.parse(src)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return None


@functools.lru_cache(maxsize=4096)
def code_view(src: str) -> Optional[str]:
    """``src`` with the inside of every literal removed, or None if it will not parse."""
    tree = _tree(src)
    if tree is None:
        return None
    try:
        return ast.unparse(_BlankLiterals().visit(copy.deepcopy(tree)))
    except Exception:  # unparse is best-effort; a tree it cannot print is a text case
        return None


def waiver_basis(entry: Any) -> str:
    """``"ast"`` or ``"text"`` — which view the waiver tables were matched against.

    Reported rather than silent: a program graded on the text view carries the
    old false-waiver risk, and a reader who cannot see which programs those were
    cannot tell a shrinking waiver count from a shrinking parse rate.
    """
    src = getattr(entry, "program", "") or ""
    return "ast" if code_view(src) is not None else "text"


def _view(entry: Any) -> str:
    src = getattr(entry, "program", "") or ""
    view = code_view(src)
    return src if view is None else view


def _dotted(node: Any) -> str:
    """``os.path.getsize`` for the attribute chain of a call target, else ``""``."""
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _string_env(tree: ast.AST) -> Dict[str, str]:
    """``name -> literal`` for the simplest possible assignments, ``p = "out.txt"``.

    Deliberately not a constant folder: one level, string constants only, and a
    name assigned twice is dropped rather than guessed at. It exists so that the
    two-statement spelling of a thing — write to ``p``, then ask about ``p`` —
    is read the same way as the one-statement spelling.
    """
    env: Dict[str, str] = {}
    shadowed = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t, val = node.targets[0], node.value
            if isinstance(t, ast.Name) and isinstance(val, ast.Constant) and isinstance(val.value, str):
                if t.id in env and env[t.id] != val.value:
                    shadowed.add(t.id)
                env[t.id] = val.value
            elif isinstance(t, ast.Name):
                shadowed.add(t.id)
    for name in shadowed:
        env.pop(name, None)
    return env


def _literal_str(node: Any, env: Dict[str, str]) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id)
    return None


#: ``open()``'s mode argument means "this call creates or truncates the file".
_WRITES = set("wxa+")


def _open_call(node: ast.Call) -> Optional[Tuple[Any, str]]:
    """``(path node, mode)`` for a call that is ``open``/``io.open``, else None."""
    name = _dotted(node.func)
    if name not in ("open", "io.open", "codecs.open"):
        return None
    if not node.args:
        return None
    mode = "r"
    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) \
            and isinstance(node.args[1].value, str):
        mode = node.args[1].value
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant) \
                and isinstance(kw.value.value, str):
            mode = kw.value.value
    return node.args[0], mode


#: Methods that put bytes on disk under a path the receiver already names.
_PATH_WRITERS = ("write_text", "write_bytes", "touch", "mkdir", "rename", "replace", "unlink")
_PATH_READERS = ("read_text", "read_bytes")


def _path_arg(node: Any, env: Dict[str, str]) -> Optional[str]:
    """The literal inside ``Path("x")`` when ``node`` is that call."""
    if isinstance(node, ast.Call) and _dotted(node.func) in ("Path", "pathlib.Path", "PurePath"):
        if node.args:
            return _literal_str(node.args[0], env)
    return None


def paths_written(src: str) -> frozenset:
    """Relative paths the program itself creates, truncates or removes.

    Only literals — a path built at runtime is unknowable from here, and
    guessing at one would be worse than admitting it is unknown.
    """
    tree = _tree(src)
    if tree is None:
        return frozenset()
    env = _string_env(tree)
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        opened = _open_call(node)
        if opened is not None and set(opened[1]) & _WRITES:
            lit = _literal_str(opened[0], env)
            if lit:
                out.add(lit)
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in _PATH_WRITERS:
            lit = _path_arg(node.func.value, env) or _literal_str(node.func.value, env)
            if lit:
                out.add(lit)
        if _dotted(node.func) in ("os.remove", "os.unlink", "os.rename", "os.replace",
                                  "os.mkdir", "os.makedirs", "shutil.rmtree"):
            if node.args:
                lit = _literal_str(node.args[0], env)
                if lit:
                    out.add(lit)
    return frozenset(out)


def paths_read(src: str) -> frozenset:
    """Relative paths the program reads the CONTENT of and does not itself write.

    Content, not existence: seeding a file an ``os.path.exists`` asks about
    would answer a question the program was entitled to hear "no" to, whereas a
    program that opens a file for reading has already assumed it is there.
    """
    tree = _tree(src)
    if tree is None:
        return frozenset()
    env = _string_env(tree)
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        opened = _open_call(node)
        if opened is not None and not (set(opened[1]) & _WRITES):
            lit = _literal_str(opened[0], env)
            if lit:
                out.add(lit)
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in _PATH_READERS:
            lit = _path_arg(node.func.value, env) or _literal_str(node.func.value, env)
            if lit:
                out.add(lit)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "open":
            lit = _path_arg(node.func.value, env)
            if lit and not (node.args and isinstance(node.args[0], ast.Constant)
                            and isinstance(node.args[0].value, str)
                            and set(node.args[0].value) & _WRITES):
                out.add(lit)
    return frozenset(out) - paths_written(src)


#: The metadata calls whose answer is about a FILE rather than about a program.
_FILE_STAT_CALLS = ("os.path.getsize", "os.path.getmtime", "os.path.getatime",
                    "os.path.getctime", "os.stat", "os.lstat")
_FILE_STAT_TEXT = re.compile(r"\bos\.path\s*\.\s*(?:getsize|getmtime|getatime|getctime)\b")


def stats_an_ambient_file(entry: Any) -> bool:
    """Does the program ask the filesystem about a file it did not itself write?

    The size or timestamp of an *ambient* file is the run's, not the
    interpreter's — a program printing the capture log's own size can never
    match a reference taken a moment earlier, because the log grew in between.
    The size of a file the program created three statements earlier is nothing
    of the sort: it is a deterministic function of what the program wrote, and
    waiving stdout for it hid six spellings of ``open("out.txt","w")
    .write("hello world"); print(os.path.getsize("out.txt"))`` — CPython prints
    ``12`` every time, and an engine printing anything at all scored MATCH.
    """
    src = getattr(entry, "program", "") or ""
    tree = _tree(src)
    if tree is None:
        # No AST to ask, so the old, blunter answer: any such call is ambient.
        return bool(_FILE_STAT_TEXT.search(src))
    written = paths_written(src)
    env = _string_env(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _dotted(node.func) in _FILE_STAT_CALLS:
            lit = _literal_str(node.args[0], env) if node.args else None
            if lit is None or lit not in written:
                return True  # unknown or not ours: ambient, and stdout is waived
        elif isinstance(node.func, ast.Attribute) and node.func.attr in ("stat", "lstat"):
            lit = _path_arg(node.func.value, env) or _literal_str(node.func.value, env)
            if lit is None or lit not in written:
                return True
    return False


def _tags(entry: Any) -> Tuple[str, ...]:
    """Corpus tags, which live in ``extra`` — the shipped schema has no column."""
    extra = getattr(entry, "extra", None) or {}
    tags = extra.get("tags")
    return tuple(str(t) for t in tags) if isinstance(tags, (list, tuple)) else ()


def is_nondeterministic(entry: Any) -> bool:
    """True when stdout cannot be compared at all, only the exit code.

    A wall clock and an unseeded PRNG stream differ between two runs of the
    *same* interpreter, so demanding a match would fail these forever and bury
    the real signal. They are still worth running: the exit code proves the
    program executed. A *seeded* stream is not this — see
    :func:`is_seeded_stream`, which is per engine rather than per program.
    """
    if "nondeterministic" in _tags(entry):
        return True
    return is_run_specific(entry) or is_interpreter_specific(entry)


def is_run_specific(entry: Any) -> bool:
    """Matched against :func:`code_view`, not the program text — see that
    function. ``os.path.getsize`` is asked about per FILE by
    :func:`stats_an_ambient_file` rather than per name."""
    view = _view(entry)
    if draws_from_random(view) and not is_seeded_stream(entry):
        return True
    if any(p.search(view) for p in _RUN_SPECIFIC):
        return True
    src = getattr(entry, "program", "") or ""
    if any(p.search(src) for p in _RUN_SPECIFIC_LITERAL):
        return True
    return stats_an_ambient_file(entry)


_RANDOM_DOTTED = re.compile(r"\brandom\s*\.\s*\w+")
_RANDOM_ALIAS = re.compile(r"^\s*import\s+random\s+as\s+(\w+)", re.M)
#: The names on the import line: inside its parentheses when it has them,
#: otherwise to the end of the line — never across it.
_RANDOM_FROM = re.compile(r"^\s*from\s+random\s+import\s+(?:\(([^)]*)\)|([^\n]*))", re.M)


def draws_from_random(src: str) -> bool:
    """Does the program DRAW from `random` — call something of it?

    An import alone is not a draw: `import random as r` followed by `print(2 +
    2)` has a stdout the battery must compare, or a wrong answer in the rest of
    the program hides behind an unused import. So the alias and the imported
    names are read out of the import line and looked for as a call. A star
    import names nothing and counts as a draw, because it could be one.
    """
    if _RANDOM_DOTTED.search(src):
        return True
    for m in _RANDOM_ALIAS.finditer(src):
        if re.search(r"\b%s\s*\.\s*\w+" % re.escape(m.group(1)), src):
            return True
    for m in _RANDOM_FROM.finditer(src):
        names = m.group(1) if m.group(1) is not None else m.group(2)
        if names.strip() == "*":
            return True
        for raw in names.split(","):
            name = raw.strip().split()[-1] if raw.strip() else ""  # `x as y` binds y
            if name and re.search(r"\b%s\s*\(" % re.escape(name), src):
                return True
    return False
#: A `seed(...)` call with a real argument, under any spelling — `random.seed(7)`,
#: `r.seed(7)`, bare `seed(7)` after `from random import seed`. `seed()` and
#: `seed(None)` draw from the OS and are not this.
_SEEDS = re.compile(r"\bseed\s*\(\s*(?!\)|None\b)")


def is_seeded_stream(entry: Any) -> bool:
    """A `random` program whose stream is fixed by an explicit seed.

    Reproducible — but only by an engine running CPython's Mersenne Twister.
    Tier 1 does (`random.rs`), so its stdout is compared like any other
    program's; MicroPython's generator is a different algorithm, so for the
    `lypning-mp` arm stdout is not compared and the exit code stands alone.
    That arm is graded on a program the chain never gives it: lypning-mp is an
    ORACLE, not a tier — nothing routes to it at all. The exemption describes a
    binary the chain cannot reach, not one it trusts, and it stays because the
    oracle is still *measured*: a seeded stream there is a plausible wrong
    number, so its exit code has to stand alone.

    The seed regex is a heuristic and errs loud: `seed(x)` with `x = None`
    counts as seeded, and such a program is compared and may MISMATCH against
    its own unseeded reference — a false alarm somebody reads, never a wrong
    answer nobody does.
    """
    if "seeded" in _tags(entry):
        return True
    view = _view(entry)
    return bool(draws_from_random(view) and _SEEDS.search(view))


def is_interpreter_specific(entry: Any) -> bool:
    if "interpreter-specific" in _tags(entry):
        return True
    # `sys.argv` and `sys.stdin` are about the RUN, not the interpreter, and must
    # still be compared — so the patterns above name attributes, never bare `sys`.
    if any(p.search(_view(entry)) for p in _INTERPRETER_SPECIFIC):
        return True
    return is_implementation_defined(entry)


def is_implementation_defined(entry: Any) -> bool:
    if "implementation-defined" in _tags(entry):
        return True
    return any(p.search(_view(entry)) for p in _IMPLEMENTATION_DEFINED)


_BRACE = re.compile(r"\{[^{}]*\}")


def only_set_order_differs(want: str, got: str) -> bool:
    """Do the two outputs differ ONLY by element order inside set displays?

    ``print({1, 2})`` may print ``{2, 1}``: the data model calls a set "an
    unordered collection", so two conformant implementations may disagree and
    the engine is not wrong. It is detected by comparing the OUTPUTS rather than
    by pattern-matching the source — a source regex would have to tell a set
    display from a dict display from an f-string brace from a ``{2,3}`` regex
    repeat, and would silently excuse a real divergence in any program that
    happened to contain a brace. Dict displays are excluded by the ``:`` test:
    dict order IS specified (insertion order, since 3.7).
    """
    want_parts = _BRACE.split(want)
    got_parts = _BRACE.split(got)
    if len(want_parts) != len(got_parts) or want_parts != got_parts:
        return False
    want_braces = _BRACE.findall(want)
    got_braces = _BRACE.findall(got)
    if not want_braces:
        return False
    saw_reorder = False
    for a, b in zip(want_braces, got_braces):
        if a == b:
            continue
        if ":" in a or ":" in b:
            return False
        if sorted(a[1:-1].split(", ")) != sorted(b[1:-1].split(", ")):
            return False
        saw_reorder = True
    return saw_reorder


def first_diff(want: str, got: str) -> str:
    """The first differing line, short enough to read in a terminal."""
    a = str(want).split("\n")
    b = str(got).split("\n")

    def clip(lines: List[str], i: int) -> str:
        if i >= len(lines):
            return "<no line>"
        s = lines[i]
        return repr(s[:90] + "…" if len(s) > 90 else s)

    for i in range(max(len(a), len(b))):
        if (a[i] if i < len(a) else None) != (b[i] if i < len(b) else None):
            return "line %d: want %s, got %s" % (i + 1, clip(a, i), clip(b, i))
    return "trailing whitespace only"


# --- what must not be run ----------------------------------------------------

# Two or more segments, not preceded by anything that would make the slash a
# division or a URL's `//`. Deliberately not a Python tokenizer: the path may sit
# in an f-string, a triple-quoted patch body or a `%`-format template, and all
# this has to decide is whether the program NAMES somewhere outside its temp cwd.
_ABS_PATH = re.compile(r"(?<![\w./~+-])/(?:[A-Za-z0-9_.+@-]+/)+[A-Za-z0-9_.+@-]*")

# The first segment must be a real root directory, or `/api/chat` and `/ISSN/`
# — URL routes and regex fragments, which no corpus is short of — would take a
# tenth of the corpus out of the measurement for nothing. The fixed set is
# unioned with the live root so the verdict does not drift between machines.
_ROOT_NAMES = frozenset((
    "bin", "boot", "dev", "etc", "home", "lib", "lib64", "media", "mnt", "opt",
    "proc", "root", "run", "sbin", "srv", "sys", "tmp", "usr", "var",
    "workspace", "Users", "Volumes", "private",
))


_ROOTS: Optional[frozenset] = None


def _live_roots() -> frozenset:
    global _ROOTS
    if _ROOTS is None:
        try:
            _ROOTS = frozenset(p.name for p in Path("/").iterdir())
        except OSError:
            _ROOTS = frozenset()
    return _ROOTS


def absolute_paths(program: str) -> List[str]:
    """Every absolute filesystem path the program names, in order of appearance.

    A program's text is fixed and a temp cwd is randomly named, so nothing here
    can be inside the directory the program is about to run in: an absolute path
    is by construction a path *out* of the sandbox. Three of the upstream
    corpus' seventeen could write.
    """
    roots = _ROOT_NAMES | _live_roots()
    out: List[str] = []
    for m in _ABS_PATH.finditer(program or ""):
        text = m.group(0)
        head = text.split("/")[1]
        if head in roots and text not in out:
            out.append(text)
    return out


#: A program that would launch one of lypning's own batteries — the CLI
#: subcommands that run the whole corpus, or the runner modules driven from
#: Python. The corpus is harvested from real sessions, and this project's own
#: development sessions type `lypning conformance`, `conf.run(...)`,
#: `eng.dispatch(...)` constantly; those land in the corpus like any other
#: one-liner. Left to run, each such entry spawns a battery *inside* the battery
#: that is running it — a fork bomb whose fan-out is the corpus size squared,
#: which is exactly how a shared machine reached load average 340.
_SPAWNS_BATTERY_CLI = re.compile(
    # `lypning` and a battery subcommand as adjacent shell tokens, whether a
    # string (`lypning conformance`) or an argv list (`'lypning','bench'`) —
    # only quotes, commas and spaces may sit between, never other words, so
    # `from lypning import bench` is not a match.
    r"\blypning\b[\s'\",)\]]{0,8}(?:conformance|bench|corpus-time|perf)\b")
_IMPORTS_LYPNING = re.compile(r"(?:^|\n|;)\s*(?:from\s+lypning\b|import\s+lypning\b)")
_DRIVES_A_BATTERY = re.compile(
    r"\b(?:conf|conformance|bench|perf)\.run\s*\("      # a battery run()
    r"|\bbench\.corpus_time\s*\("                        # the bench battery
    r"|\.run\s*\(\s*engines"                             # .run(engines=…)
    r"|\b(?:eng|engines)\.(?:run|dispatch)\s*\("         # an engine spawn
    r"|\bcorpus\.load(?:_default)?\s*\(")                # the whole corpus


def spawns_a_battery(program: str) -> str:
    """Why running ``program`` would recursively launch a battery, or ``""``.

    A net, not a sandbox (CLAUDE.md invariant 4): capture still records these —
    an agent that typed `lypning conformance` is real usage worth knowing — but
    the runner refuses to *replay* them, exactly as it records an absolute-path
    program and skips it. `lypning route`/`run` over a single program are safe
    and deliberately not matched; only the batteries and the engine-driving APIs
    are, plus loading the whole corpus to iterate over.
    """
    program = program or ""
    if _SPAWNS_BATTERY_CLI.search(program):
        return "would spawn a lypning battery (conformance/bench/corpus-time/perf)"
    if _IMPORTS_LYPNING.search(program) and _DRIVES_A_BATTERY.search(program):
        m = _DRIVES_A_BATTERY.search(program)
        return "imports lypning and drives a battery: %s…" % m.group(0).strip()
    return ""


# --- records -----------------------------------------------------------------


@dataclass
class Verdict:
    """One engine's answer for one program, and the evidence for it."""

    engine: str
    entry_id: str
    verdict: str
    kind: str = ""
    detail: str = ""
    expected_stdout: str = ""
    actual_stdout: str = ""
    #: Kept for the same reason and on the same terms as the stdout pair: a
    #: stderr verdict whose evidence is not printed is a verdict nobody can act
    #: on, and for a program with empty stdout stderr is the whole answer.
    expected_stderr: str = ""
    actual_stderr: str = ""
    expected_rc: int = 0
    actual_rc: int = 0
    wall_ns: int = 0
    #: A digest of the whole stdout, kept for every verdict so two arms can be
    #: compared for agreement without carrying the bytes (`dispatchers agree`).
    stdout_digest: str = ""
    #: What the verdict actually rested on: ``"exit"``, plus ``"stdout"`` when
    #: either arm wrote any and it was not waived, plus ``"stderr"`` when either
    #: arm said anything after warnings — or ``"refusal"`` for coverage. A
    #: MATCH reading ``"exit"`` alone is two runs agreeing that a program
    #: failed, which is worth strictly less than two runs agreeing on an answer
    #: and must not be presented as the same number.
    compared: str = "exit"

    @property
    def failed(self) -> bool:
        return self.verdict == MISMATCH

    def __str__(self) -> str:
        why = "%s: %s" % (self.kind, self.detail) if self.kind else self.detail
        return "%-11s %-8s %s: %s" % (self.verdict, self.engine, self.entry_id, why)


@dataclass
class EngineReport:
    """One arm's totals. ``coverage`` is percent MATCH of what was run."""

    engine: str
    match: int
    unsupported: int
    mismatch: int
    total: int
    coverage: float
    verdicts: List[Verdict] = field(default_factory=list)

    def failures(self) -> List[Verdict]:
        return [v for v in self.verdicts if v.verdict == MISMATCH]

    def _matches(self) -> List[Verdict]:
        return [v for v in self.verdicts if v.verdict == MATCH]

    @property
    def match_stdout(self) -> int:
        """MATCHes where stdout was actually compared and had bytes in it."""
        return sum(1 for v in self._matches() if "stdout" in v.compared)

    @property
    def match_stderr(self) -> int:
        """MATCHes where stderr had something to compare on one arm or both."""
        return sum(1 for v in self._matches() if "stderr" in v.compared)

    @property
    def match_exit_only(self) -> int:
        """MATCHes decided on an exit code and nothing else.

        The number the battery could not previously print, and the one that says
        how much of ``coverage`` is an opinion about output rather than about a
        return value.
        """
        return sum(1 for v in self._matches() if v.compared == "exit")

    @property
    def match_both_failed(self) -> int:
        """MATCHes where both arms exited non-zero with the same code.

        Agreement, and real — but agreement that a program did not work, which
        is the cheapest kind of agreement there is and the one a sandbox
        missing the program's files manufactures in bulk.
        """
        return sum(1 for v in self._matches() if v.actual_rc and v.actual_rc == v.expected_rc)


@dataclass
class Skip:
    """A program that was deliberately not run, and why. Not a verdict."""

    entry_id: str
    reason: str


@dataclass
class RoutingError:
    """A route that sent a program somewhere it does not work.

    Being late is a cost in milliseconds and is not recorded here; being *wrong*
    is a cost in correctness. Only the classifier's fatal outcome — routed to an
    engine that MISMATCHES — and a route that could not be obtained at all are.
    The dispatcher usually recovers from the first (the mixture arm shows
    whether it did), which is exactly why the two are measured separately.
    """

    entry_id: str
    predicted: str
    ideal: str = ""
    detail: str = ""


@dataclass
class Report:
    engines: Dict[str, EngineReport]
    routing_errors: List[RoutingError]
    skipped: List[Skip]
    seconds: float
    #: ``{entry_id: Route}`` — where the classifier sent each program it was
    #: asked about. Recorded rather than graded here: whether a route was
    #: *cheap enough* is a separate measurement with its own vocabulary and its
    #: own gate (:mod:`lypning.routing`), and this module owns only the one
    #: verdict that must be zero. Empty when the mixture arm did not run, since
    #: that is the arm that consults the classifier.
    routes: Dict[str, eng.Route] = field(default_factory=dict)
    damage: List[str] = field(default_factory=list)
    unbuilt: List[str] = field(default_factory=list)
    #: ``arm -> why``, for an arm that is absent for a reason worth reading.
    #: "Not built" is the usual one and needs no entry; "built, but this binding
    #: cannot speak to it" is a different fact, and reporting it as absence
    #: sends the reader to rebuild something that is already there.
    unbuilt_why: Dict[str, str] = field(default_factory=dict)
    reference: str = ""
    total: int = 0
    #: ``(entries seeded, files written per sandbox)`` — see :func:`seed_files`.
    seeded: Tuple[int, int] = (0, 0)
    #: Entries whose waiver was decided on the program TEXT because the program
    #: does not parse (:func:`waiver_basis`). Reported because those carry the
    #: old false-waiver risk and a reader has to be able to tell a shrinking
    #: waiver count from a shrinking parse rate.
    text_view: int = 0
    #: ``(agreed, compared)`` over the entries both dispatcher arms ran, and the
    #: entries where they did not agree. Only when both arms were requested.
    dispatchers: Optional[Tuple[int, int]] = None
    disagreements: List[str] = field(default_factory=list)
    #: ``(violations, compared)`` over adjacent spectrum rungs: a larger variant
    #: must never do worse than a smaller one on a program both ran — a MATCH
    #: below must be a MATCH above with the same stdout where stdout was
    #: compared. One violation is a variant that lost a capability it claims
    #: to be a superset of, which no other gate can see.
    monotone: Optional[Tuple[int, int]] = None
    monotone_violations: List[str] = field(default_factory=list)

    @property
    def mismatches(self) -> int:
        return sum(r.mismatch for r in self.engines.values())

    @property
    def ok(self) -> bool:
        """Zero MISMATCH across every engine present — and an intact repository.

        Damage is part of the gate rather than a warning beside it: a run that
        rewrote the tree it was measuring produced its numbers against a moving
        target, so reporting ``ok`` for it would be reporting a measurement we
        cannot stand behind.
        """
        # Two dispatchers disagreeing, or a larger variant doing worse than a
        # smaller one, are MISMATCH-class: an engine gave the user an answer the
        # battery did not grade. Step 3 printed FAIL for the first and exited 0.
        return (self.mismatches == 0 and not self.damage and not self.disagreements
                and not self.monotone_violations)


# --- the safety net ----------------------------------------------------------


def _git(root: Path, *args: str, timeout: float = 60.0) -> Optional[str]:
    try:
        p = subprocess.run(
            ["git", "-C", str(root)] + list(args),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def dirty_paths(root: Path) -> Optional[Dict[str, str]]:
    """``{path: status}`` as git sees it, or None when this is not a work tree.

    ``-z`` rather than plain ``--porcelain`` because the plain form quotes and
    escapes paths containing spaces or non-ASCII, and a corpus harvested from
    agent sessions is exactly where such a path shows up.
    """
    out = _git(root, "status", "--porcelain", "-z")
    if out is None:
        return None
    fields = [f for f in out.split("\0") if f]
    seen: Dict[str, str] = {}
    i = 0
    while i < len(fields):
        rec = fields[i]
        i += 1
        if len(rec) < 4:
            continue
        status, path = rec[:2], rec[3:]
        seen[path] = status
        # A rename/copy carries its source as the next NUL-separated field.
        if "R" in status or "C" in status:
            i += 1
    return seen


# The snapshot keeps the BYTES of everything already dirty, so a file the
# developer was midway through editing can be put back exactly as it was. 64 MiB
# is the budget; past it the net keeps the digest and reports the change as
# unrestorable, which is still louder than not noticing it at all.
_SNAPSHOT_BUDGET = 64 * 1024 * 1024


def _expand(root: Path, rel: str) -> List[str]:
    """One git-status entry as the files it actually stands for.

    git collapses an untracked DIRECTORY into a single ``?? dir/`` entry, so
    every file the corpus creates inside one leaves the status line unchanged.
    That is not an exotic case — ``tests/`` is an untracked directory in a fresh
    checkout of this very repository. Expanded with git rather than with
    ``rglob`` so the ignore rules still decide what counts.
    """
    if not rel.endswith("/"):
        return [rel]
    out = _git(root, "ls-files", "--others", "--exclude-standard", "-z", "--", rel)
    if out is None:
        return [rel.rstrip("/")]
    return [f for f in out.split("\0") if f]


def _digest(p: Path) -> str:
    """Content identity, or ``""`` when the path is absent. Never raises."""
    h = hashlib.sha256()
    try:
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


@dataclass
class _TreeState:
    """The work tree as it was before the corpus ran: git's view, and the bytes."""

    status: Dict[str, str] = field(default_factory=dict)
    digests: Dict[str, str] = field(default_factory=dict)
    saved: Dict[str, Path] = field(default_factory=dict)
    store: Optional[Path] = None

    def discard(self) -> None:
        if self.store is not None:
            shutil.rmtree(self.store, ignore_errors=True)
            self.store = None


def _fingerprint(root: Path, status: Dict[str, str]) -> Dict[str, str]:
    """``{path: digest}`` over every file the status entries stand for."""
    out: Dict[str, str] = {}
    for rel in sorted(status):
        for f in _expand(root, rel):
            out[f] = _digest(root / f)
    return out


def _snapshot(root: Path) -> Optional[_TreeState]:
    """What the tree looked like, in enough detail to detect a SECOND change.

    Comparing the SET of dirty paths — which is all this did until a probe
    caught it — cannot see the case that matters: a file that was already
    modified when the battery started stays `` M`` no matter what the corpus
    appends to it, so the one state a developer ever runs this in, mid-edit, was
    the one state where the net was blind. Digests close that. The saved bytes
    are what makes "restored" true afterwards: the pre-run content of an
    already-dirty file was never committed, so ``git checkout`` would replace
    the corpus' damage with the tool's own.
    """
    status = dirty_paths(root)
    if status is None:
        return None
    state = _TreeState(status=status, digests=_fingerprint(root, status))
    spent = 0
    for f in sorted(state.digests):
        src = root / f
        try:
            size = src.stat().st_size
        except OSError:
            continue
        if spent + size > _SNAPSHOT_BUDGET:
            continue
        if state.store is None:
            state.store = Path(tempfile.mkdtemp(prefix="lypning-conf-snap-"))
        dest = state.store / ("%d.blob" % len(state.saved))
        try:
            shutil.copyfile(src, dest)
        except OSError:
            continue
        state.saved[f] = dest
        spent += size
    return state


def _collateral(root: Path, before: _TreeState) -> Dict[str, str]:
    """``{path: digest_now}`` for every path whose bytes are not what they were.

    The union of what git calls dirty now and what it called dirty before: a
    corpus program that "helpfully" reverts an edited file to its committed
    content takes it *off* the status list, and that is a change like any other.
    """
    status = dirty_paths(root) or {}
    now = _fingerprint(root, status)
    for f in before.digests:
        if f not in now:
            now[f] = _digest(root / f)
    return {f: d for f, d in now.items() if before.digests.get(f, "") != d}


def _is_tracked(root: Path, rel: str) -> bool:
    return _git(root, "ls-files", "--error-unmatch", "--", rel) is not None


def _remove(target: Path) -> None:
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target, ignore_errors=True)
    else:
        target.unlink()


def _restore(root: Path, collateral: Dict[str, str], before: _TreeState) -> List[str]:
    """Undo what the corpus wrote. Returns the paths that could not be undone.

    A path that was ALREADY dirty is put back from the snapshot, never with
    ``git checkout``: its pre-run content is not in any commit, so checking it
    out would delete the developer's own uncommitted work in the name of
    protecting it. Only a path that was clean before goes back through git.
    """
    failed: List[str] = []
    fresh: List[str] = []
    for p in sorted(collateral):
        target = root / p
        # Never touch anything outside the tree that was snapshotted, whatever
        # git said — a symlink out of the tree is a path out of the tree.
        try:
            target.resolve().relative_to(root.resolve())
        except (ValueError, OSError):
            failed.append(p)
            continue
        if p not in before.digests:
            fresh.append(p)
            continue
        saved = before.saved.get(p)
        if saved is None and before.digests[p]:
            # Too large for the budget: say so rather than pretend.
            failed.append(p)
            continue
        try:
            if not before.digests[p]:
                _remove(target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(saved, target)
        except OSError:
            failed.append(p)

    tracked = [p for p in fresh if _is_tracked(root, p)]
    if tracked:
        if _git(root, "checkout", "--", *tracked) is None:
            failed.extend(tracked)
    for p in fresh:
        if p in tracked:
            continue
        try:
            _remove(root / p)
        except OSError:
            failed.append(p)
    return failed


def close_net(root: Path, before: Optional[_TreeState]) -> List[str]:
    """Diff the bracket, put back what the corpus wrote, and free the snapshot.

    Split out of :func:`run` so it can be called from a ``finally``. A battery
    that leaves by raising — one malformed entry, a ``KeyboardInterrupt`` on a
    long run — has run every entry before it, so it is the path where the tree
    is MOST likely to be dirty and was the one path with no restore at all. The
    exception still propagates; it just no longer takes the repository with it.

    Never raises on its own account: an exception from the net would replace the
    one the caller is already handling, and lose it.
    """
    if before is None:
        return []
    try:
        collateral = _collateral(root, before)
        if not collateral:
            return []
        failed = _restore(root, collateral, before)
        return sorted("%s%s" % (_damage_line(root, before, p, d),
                                "  [NOT RESTORED]" if p in failed else "")
                      for p, d in collateral.items())
    except Exception as e:  # noqa: BLE001 - see the docstring
        return ["the repository could not be checked or restored: %s: %s"
                % (type(e).__name__, e)]
    finally:
        before.discard()


def _damage_line(root: Path, before: _TreeState, path: str, digest: str) -> str:
    """How the path changed, in the vocabulary a reader can act on.

    A path absent from the snapshot was clean, not absent: git only lists what
    is dirty. Whether it existed is therefore a question for ``ls-files``, not
    for the snapshot.
    """
    known = path in before.digests
    if not digest:
        return "deleted  %s" % path
    if not known:
        return "%s %s" % ("modified" if _is_tracked(root, path) else "created ", path)
    if not before.digests[path]:
        return "created  %s" % path
    return "modified %s (it was ALREADY dirty before the run)" % path


# --- running one entry -------------------------------------------------------


def _clip(s: str) -> str:
    return s if len(s) <= _STDOUT_CLIP else s[:_STDOUT_CLIP] + "\n…[clipped]"


#: Anchored, and matched against ONE line rather than searched across stderr:
#: the contract puts the refusal at the head and puts nothing else there.
_UNSUPPORTED_RE = re.compile(r"^([\w.-]+): unsupported: ([\w-]+): (.+)$")

#: One CPython warning as it lands on stderr: ``<file>:<line>: <Kind>Warning:
#: <message>``, then — when the file is readable, so never for ``-c`` — the
#: offending source line echoed under it with a two-space indent. Advisory
#: only: the interpreter carries on and exits 0, so it is not a failure the
#: engine was expected to reproduce (see :func:`classify`). Python 3.14 added
#: one for ``return`` inside ``finally`` (PEP 765), which is how a corpus program
#: with identical stdout and exit code came to be scored MISMATCH.
_WARNING_RE = re.compile(r"^[^\n:]+:\d+: \w+Warning: .*\n(?:  .*\n)?", re.M)


def _without_warnings(stderr: str) -> str:
    """``stderr`` with CPython's warning blocks removed, so what is left is
    the part that meant something went wrong."""
    return _WARNING_RE.sub("", stderr or "")


# --- what stderr says, minus what it is not entitled to be believed about -----
#
# Until 2026-09-07 stderr was compared in one direction and one direction only:
# CPython said something and the engine said nothing. One byte of anything
# satisfied it. An engine that kept stdout and the exit code and replaced its
# whole stderr with an invented `RuntimeError` kept every MATCH it had; one that
# appended a banner to every run was invisible. And for a MATCH with empty
# stdout — 1,060 of `lypning`'s 1,547 on this tree on 2026-09-07 — stderr IS
# the whole answer the agent reads.
#
# The reason it was left uncompared is real, though: CPython's message wording
# drifts between 3.11, 3.12 and 3.14, and this project deliberately refuses
# error paths rather than chase it. So what is compared is what does NOT drift:
#
#   * the fact of raising — a run that ended in an exception, on both arms;
#   * the exception TYPE, which is API and does not get reworded;
#   * the part of stderr that is not a traceback at all, which is the program's
#     own output and is as comparable as stdout.
#
# What is NOT compared is the traceback body — the preamble, the frames, the
# source echoes, the `^^^^` markers, the message text. lypning prints a
# deliberately shorter traceback than CPython (`Traceback (most recent call
# last):` and then straight to the exception line, with no frames, and `  line
# N` where CPython writes `  File "<string>", line N`); CPython prints no
# preamble at all for a SyntaxError under `-c`. Both are normalised away by
# taking the exception line and the text above the block, and nothing between.

_TRACEBACK_OPENER = "Traceback (most recent call last):"

#: CPython's two connectors between chained tracebacks. They belong to the
#: block, not to the program's own output, so they are peeled with it.
_CHAIN_LINES = (
    "During handling of the above exception, another exception occurred:",
    "The above exception was the direct cause of the following exception:",
)

#: The last line of a traceback: a dotted exception name, then optionally a
#: colon and a message. Bare (``KeyboardInterrupt``) and messaged
#: (``ValueError: x``) are both this.
_EXC_HEADER = re.compile(r"^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)(?::(?:\s.*)?)?$")


def stderr_shape(stderr: str) -> Tuple[str, str]:
    """``(what the program itself wrote, the exception type that ended the run)``.

    Traceback blocks are peeled off the tail — as many as chaining left there —
    and only the type of the outermost one survives, spelled by its last dotted
    component: CPython qualifies a non-builtin exception with the module it was
    defined in, and two implementations that agree on the exception may lay its
    module out differently.

    A line that *looks* like an exception header but has no frame and no
    preamble above it is not one; it is a program writing to stderr, and it
    stays in the first half of the pair where it can be compared as bytes.
    """
    lines = _without_warnings(stderr or "").splitlines()
    exc = ""
    while True:
        end = len(lines)
        while end and (not lines[end - 1].strip() or lines[end - 1].strip() in _CHAIN_LINES):
            end -= 1
        if not end:
            lines = lines[:end]
            break
        m = _EXC_HEADER.match(lines[end - 1])
        if m is None:
            lines = lines[:end]
            break
        start = end - 1
        while start > 0:
            above = lines[start - 1]
            if above.strip() == _TRACEBACK_OPENER:
                start -= 1
                break
            if above[:1] in (" ", "\t"):
                start -= 1
                continue
            break
        if start == end - 1:
            # Nothing framed it: the program wrote this line itself.
            lines = lines[:end]
            break
        if not exc:
            exc = m.group(1).rsplit(".", 1)[-1]
        lines = lines[:start]
    return "\n".join(lines).rstrip(), exc


#: An in-process run cannot be killed, so the library arm's stand-in for the
#: battery's timeout is a step budget: a program that will not stop refuses
#: instead of hanging the run. Far above anything a one-liner does — the whole
#: corpus runs in milliseconds per entry — and it is a refusal rather than a
#: verdict, so an entry that somehow reached it is scored as coverage, never as
#: a disagreement with CPython.
LIBRARY_STEP_LIMIT = 100_000_000


def _refusal(engine: str, stderr: str) -> Optional[Tuple[str, str]]:
    """``(kind, detail)`` from the refusal line at the HEAD of stderr, or None.

    Invariant 2 defines a refusal as *exactly one* ``<engine>: unsupported:
    <kind>: <detail>`` line on stderr and nothing on stdout, so that is the
    shape matched: the first line, written by a tier whose name this arm may
    speak with. Until 2026-09-07 the match was ``re.M`` and floated anywhere in
    stderr, which meant a program could print one halfway through its own
    output and have its exit 90 counted as coverage.

    The mixture arm relays whichever tier answered, so its line may carry any
    engine's name — but it must carry one of them, or the "refusal" is a program
    printing something that looks like one.
    """
    head = (stderr or "").split("\n", 1)[0]
    m = _UNSUPPORTED_RE.match(head)
    if m is None:
        return None
    who = m.group(1)
    # The library writes lypning's own line, because it IS lypning — the
    # arm name is ours, for the report, and never reaches the runtime.
    if (who == engine
            or (engine in (MIXTURE, MIXTURE_RUST) and who in eng.ENGINE_ORDER)
            or (engine == LIBRARY and who == eng.SPECTRUM[-1])):
        return m.group(2), m.group(3)
    return None


def _looks_like_a_refusal(stderr: str) -> bool:
    """Is the head of ``stderr`` refusal-shaped, whoever it names?

    Asked of the REFERENCE, where the answer can only mean one thing: CPython
    is not a tier and never refuses, so a contract line coming out of it was
    written by the program.
    """
    return _UNSUPPORTED_RE.match((stderr or "").split("\n", 1)[0]) is not None


def classify(ref: eng.Result, got: eng.Result, engine: str, entry: Any) -> Verdict:
    """Score one engine's run against the reference run of the same program.

    Four things are compared, and :attr:`Verdict.compared` records which of them
    this particular verdict actually rested on — because they are not all
    present in every program and a MATCH that compared nothing is not the same
    fact as a MATCH that compared output.

    **The exit code**, always. **stdout**, as BYTES on both sides, unless the
    program is one whose output cannot be reproduced (:func:`is_nondeterministic`).
    **The exception**, if either arm ended in one: its type, and the fact of
    there being one. **The rest of stderr**, which is the program's own writing
    and is compared like stdout.

    What is deliberately not compared is traceback text — the preamble, the
    frames, the message wording. CPython rewords its messages between 3.11,
    3.12 and 3.14 and this project refuses error paths rather than track that;
    the exception type is the part that is API. See :func:`stderr_shape`.

    A CPython *warning* is not a failure — the interpreter prints it and carries
    on — so warning blocks are stripped from BOTH arms before either is read;
    otherwise a new advisory in the reference interpreter (3.14's PEP 765
    ``SyntaxWarning``) would score an engine that agreed everywhere else as a
    MISMATCH.
    """
    entry_id = getattr(entry, "id", "")
    # BYTES, on both sides. `Result.stdout` is decoded for a reader, and that
    # decode applies universal-newline translation to the engine's output AND to
    # CPython's before either is compared — which made line endings an axis this
    # battery structurally could not see a disagreement on (issue #50). What is
    # graded is what the process wrote; what is shown is `exact_text` of the
    # same bytes, decoded but not normalised, so a CRLF divergence prints as one.
    want, mine = ref.stdout_bytes, got.stdout_bytes

    skip_stdout = is_nondeterministic(entry) or (
        engine == eng.MICROPYTHON and is_seeded_stream(entry))
    ref_head, ref_exc = stderr_shape(ref.stderr)
    got_head, got_exc = stderr_shape(got.stderr)

    # What this verdict rests on, recorded on every verdict and reported per arm.
    # Before stderr was compared at all, a MATCH with empty stdout on both arms
    # rested on an exit code and nothing else. Measured on this tree on
    # 2026-09-07, after the fixes below: of `lypning`'s 1,547 MATCHes only 487
    # compare stdout, so 1,060 would have been that — 900 of them two arms
    # failing with the same non-zero code, and 172 with nothing on either
    # stream at all. Those are still MATCHes and still worth having; they are
    # not the same MATCH as one that compared a page of output, and a report
    # that prints one number for both cannot be read.
    evidence_of: List[str] = ["exit"]
    if not skip_stdout and (want or mine):
        evidence_of.append("stdout")
    if ref_head or got_head or ref_exc or got_exc:
        evidence_of.append("stderr")
    compared = ",".join(evidence_of)

    def v(verdict: str, kind: str = "", detail: str = "", evidence: bool = False) -> Verdict:
        return Verdict(
            engine=engine, entry_id=entry_id, verdict=verdict, kind=kind, detail=detail,
            expected_stdout=_clip(eng.exact_text(want)) if evidence else "",
            actual_stdout=_clip(eng.exact_text(mine)) if evidence else "",
            expected_stderr=_clip(ref.stderr or "") if evidence else "",
            actual_stderr=_clip(got.stderr or "") if evidence else "",
            expected_rc=ref.returncode, actual_rc=got.returncode, wall_ns=got.wall_ns,
            stdout_digest=hashlib.sha256(mine).hexdigest()[:16],
            compared=compared if verdict != UNSUPPORTED else "refusal",
        )

    if got.timed_out:
        # The reference ran inside the same deadline and finished, so this is the
        # engine hanging where CPython did not — a divergence, not a slow program.
        return v(MISMATCH, "timeout", "no output within the deadline", evidence=True)
    if not got.binary:
        return v(MISMATCH, "unbuilt", "engine not available")

    # The reference decides first, and it has to: CPython is not a tier and
    # cannot refuse, so an exit 90 WITH a contract line coming out of it means
    # the PROGRAM wrote that line and chose that code. Reading it as coverage
    # was a way to buy `--plan` rows and an `ok` report with a two-line program
    # — `sys.stderr.write("lypning: unsupported: forged: …"); sys.exit(90)`
    # graded UNSUPPORTED although CPython had run it identically and the honest
    # verdict is MATCH. The fall-through three lines below existed for exactly
    # this case and was unreachable, because the refusal branch returned first.
    forged = ref.returncode == eng.UNSUPPORTED_EXIT and _looks_like_a_refusal(ref.stderr)
    refusal = None if forged else _refusal(engine, got.stderr)
    if got.returncode == eng.UNSUPPORTED_EXIT and not forged:
        if refusal:
            if mine:
                # A refusal is only interchangeable with the next tier's answer
                # because it leaves nothing behind. Output that already reached
                # stdout is the one thing the next tier cannot take back: the
                # caller gets the refusing tier's lines AND the answering tier's,
                # so a `… | wc -l` reads high while the exit code still looks
                # right — which is exactly how lypning-mp's tracebacks-to-stdout
                # went unnoticed (engines.py, build.check_refusal_contract).
                # Counting this as coverage would leave the battery blind to the
                # only failure mode the three-tier design cannot survive.
                return v(MISMATCH, "contract",
                         "refused after %d byte(s) had already reached stdout"
                         % len(mine), evidence=True)
            extra = (got.stderr or "").split("\n", 1)[1].strip() if "\n" in (got.stderr or "") else ""
            if extra:
                # Same argument, one stream over: the contract is one line and
                # nothing else, and a caller who then gets CPython's answer sees
                # the refusing tier's stderr in front of it.
                return v(MISMATCH, "contract",
                         "refused after %d byte(s) had already reached stderr"
                         % len(extra), evidence=True)
            return v(UNSUPPORTED, refusal[0], refusal[1])
        if ref.returncode != eng.UNSUPPORTED_EXIT:
            # Exit 90 without the contract line is itself a contract violation;
            # the alternative is silently counting a crash as coverage.
            return v(MISMATCH, "contract", "exit 90 with no `%s: unsupported: …` line" % engine,
                     evidence=True)
        # ...unless CPython exited 90 too, in which case 90 is the PROGRAM's own
        # exit code (`sys.exit(90)`) and the engine reproduced it. Scoring that
        # as a broken contract accuses an engine of a bug for agreeing with the
        # reference. Fall through and compare it like any other exit code.

    if not skip_stdout and mine != want:
        # The one excuse, and it is applied to the UNNORMALISED text: a set
        # display may legitimately reorder (see :func:`only_set_order_differs`),
        # and running that test on newline-translated text would let it excuse a
        # line-ending divergence that happened to share a program with one.
        a, b = eng.exact_text(want), eng.exact_text(mine)
        if not only_set_order_differs(a, b):
            return v(MISMATCH, "stdout", first_diff(a, b), evidence=True)
    if got.returncode != ref.returncode:
        return v(MISMATCH, "exit",
                 "exit %d, CPython gave %d" % (got.returncode, ref.returncode), evidence=True)
    if _without_warnings(ref.stderr) and not got.stderr:
        return v(MISMATCH, "stderr", "CPython reported an error, this engine was silent",
                 evidence=True)
    if ref_exc != got_exc:
        return v(MISMATCH, "stderr-exc", "CPython %s, this engine %s"
                 % ("raised " + ref_exc if ref_exc else "raised nothing",
                    "raised " + got_exc if got_exc else "raised nothing"), evidence=True)
    if not skip_stdout and ref_head != got_head:
        # The non-traceback part of stderr is the program's own writing, so it
        # is compared exactly like stdout — and waived exactly like it, because
        # a program whose stdout is a wall clock puts a wall clock on stderr too.
        return v(MISMATCH, "stderr-text", first_diff(ref_head, got_head), evidence=True)
    return v(MATCH, "", "stdout uncompared" if skip_stdout else "")


#: What a seeded file contains, by suffix. Small, fixed, and the same in every
#: sandbox — the point is that both arms read identical bytes, not that the bytes
#: resemble whatever the agent's own tree held when the one-liner was captured.
_SEED_CONTENT: Dict[str, bytes] = {
    ".json": b'{"name": "seed", "items": [1, 2, 3]}\n',
    ".jsonl": (b'{"id": "py-seed-1", "program": "print(1)"}\n'
               b'{"id": "py-seed-2", "program": "print(2)"}\n'),
    ".csv": b"a,b,c\n1,2,3\n4,5,6\n",
    ".tsv": b"a\tb\tc\n1\t2\t3\n",
    ".py": b"x = 1\n\n\ndef f(n):\n    return n + 1\n",
    ".rs": b"fn main() {\n    println!(\"seed\");\n}\n",
    ".md": b"# seed\n\nalpha\nbeta\n",
    ".toml": b"[seed]\na = 1\n",
    ".yml": b"a: 1\nb: 2\n",
    ".yaml": b"a: 1\nb: 2\n",
    ".bin": b"\x00\x01\x02\x03",
}
_SEED_DEFAULT = b"alpha\nbeta\ngamma\n"

#: A ceiling, because a program is free to name a hundred paths and a sandbox is
#: built once per run per arm.
_SEED_LIMIT = 32

#: The exceptions an empty cwd manufactures, and the only ones a seed is allowed
#: to answer. A program that fails for its own reasons is left failing.
_MISSING_FILE = ("FileNotFoundError", "IsADirectoryError", "NotADirectoryError")


def seed_files(program: str) -> Dict[str, bytes]:
    """The files to put in the sandbox before running ``program``, and their bytes.

    Every entry gets a fresh empty cwd, which is what keeps the corpus off the
    repository — and which also means a program that opens ``data.csv`` fails to
    find it. That failure is *symmetric*: the reference misses the file exactly
    as the engine does, both raise ``FileNotFoundError``, and the grader reads
    two identical failures as agreement. The entries naming an ABSOLUTE path —
    the ones that would have found their file — are skipped, so the skip rule is
    precisely what leaves this class in. Measured on this tree on 2026-09-07:
    363 entries were seeded with 399 files between them, and 23 verdicts per
    Rust arm moved from MATCH to UNSUPPORTED as a result.

    Seeding is not a fidelity claim. The bytes are invented; what is real is that
    the program gets far enough to make the engine do something, and whatever it
    then does is compared against CPython doing the same. Re-grading the class
    this way surfaced refusals that were never counted, in a table ``--plan``
    ranks the build order by — the blind spot corrupted the roadmap, not just
    the verdict.

    This function only says which files an entry COULD be given.
    :func:`_run_entry` decides whether to give them, and only does so when the
    reference run in an empty sandbox died of a missing file: seeding a program
    that deliberately opens something it expects to be absent would delete the
    branch it was written to test.

    Only paths whose CONTENT is read, only string literals, and never one the
    program itself writes: seeding a file a program is about to create with
    ``open(p, "x")`` would invent a failure rather than remove one.
    """
    out: Dict[str, bytes] = {}
    for rel in sorted(paths_read(program)):
        if len(out) >= _SEED_LIMIT:
            break
        pure = PurePosixPath(rel)
        if pure.is_absolute() or not rel or rel.startswith("~") or len(rel) > 200:
            continue
        if any(part in ("..", "") for part in pure.parts) or len(pure.parts) > 8:
            continue
        if "\\" in rel or "\x00" in rel:
            continue
        out[rel] = _SEED_CONTENT.get(pure.suffix.lower(), _SEED_DEFAULT)
    return out


class _Sandbox:
    """A fresh cwd per run, removed afterwards, never the repository.

    Per *run*, not per entry: the reference and each engine get their own, or the
    second one to run would read back the file the first one created and "match"
    without having written anything. ``seed`` is written into each of them
    identically — see :func:`seed_files` for why an empty cwd is not neutral.
    """

    def __init__(self, tag: str, seed: Optional[Dict[str, bytes]] = None) -> None:
        self.tag = tag
        self.seed = seed or {}

    def __enter__(self) -> Path:
        self.path = Path(tempfile.mkdtemp(prefix="lypning-conf-%s-" % self.tag))
        for rel, data in self.seed.items():
            target = self.path / rel
            try:
                # Belt to seed_files' braces: a path that resolves outside the
                # sandbox is dropped, never written.
                target.resolve().relative_to(self.path.resolve())
            except ValueError:
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            except OSError:
                pass
        return self.path

    def __exit__(self, *exc: Any) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def _env_for(cwd: Path) -> Dict[str, str]:
    """The environment every run shares — reference and engines alike.

    ``LYPNING_LOG`` is redirected into the sandbox rather than left pointing at
    the real capture log: a conformance run executes the whole corpus, and a
    capture of that would fold the test suite back into the corpus as observed
    evidence — the feedback loop that once made 138 of 197 "harvested" programs
    verbatim seed programs, all with the same count. Since the build order is
    ranked by those counts, the loop does not merely add noise, it ranks guesses
    above what an agent actually typed. ``engines.run`` sets ``LYPNING_CAPTURE=0``
    as the other half of that belt and braces.

    What is NOT here is the other half of "the same environment": the variables
    the arms INHERIT. A path-like one is resolved against the child's cwd, and
    every child here has a sandbox cwd of its own — so ``PYTHONPATH=src`` broke
    the reference's imports exactly as it broke the engine's, and two identical
    failures are what :func:`classify` reads as agreement. Those are made
    absolute in one place, :func:`lypning.engines.child_env`, which every spawn
    goes through (issue #57).
    """
    return {
        "LYPNING_LOG": str(cwd / "capture.jsonl"),
        "PWD": str(cwd),
        "LC_ALL": "C.UTF-8",
        # Two runs of CPython must agree with each other before either can be a
        # reference for something else.
        "PYTHONHASHSEED": "0",
    }


@dataclass
class _EntryResult:
    entry_id: str
    verdicts: Dict[str, Verdict] = field(default_factory=dict)
    predicted: str = ""
    route_kind: str = ""
    route_detail: str = ""
    skip: Optional[Skip] = None
    #: How many files were written into every sandbox for this entry.
    seeded: int = 0


def _run_entry(
    entry: Any,
    arms: Sequence[str],
    binaries: Dict[str, Optional[Path]],
    ref_bin: Optional[Path],
    timeout: float,
) -> _EntryResult:
    out = _EntryResult(entry_id=getattr(entry, "id", ""))
    program = entry.program
    argv_tail = list(getattr(entry, "argv_tail", ()) or ())
    stdin = getattr(entry, "stdin_sample", None) or ""

    if "\0" in program or any("\0" in a for a in argv_tail):
        # An argv element cannot carry a NUL — the kernel's argv is NUL
        # terminated — so no interpreter on earth can be handed this entry, and
        # neither side of the comparison ever starts. It is a skip, not a
        # verdict: scoring "both failed to spawn" as MATCH would report agreement
        # between two runs that did not happen. The shim captures argv verbatim,
        # which is where such a record comes from.
        out.skip = Skip(out.entry_id, "NUL byte in the program or its argv: unspawnable")
        return out

    battery = spawns_a_battery(program)
    if battery:
        # A fork bomb, not a divergence: this program runs the whole battery
        # again. Skipped like an absolute path — recorded, never replayed.
        out.skip = Skip(out.entry_id, battery)
        return out

    outside = absolute_paths(program)
    for a in argv_tail:
        # The shim captures a shell redirect as an argv element (`>`, `/tmp/f`),
        # and a program is free to open `sys.argv[1]`. Same rule.
        outside.extend(p for p in absolute_paths(a) if p not in outside)
    if outside:
        out.skip = Skip(out.entry_id, "absolute path outside the sandbox: %s" % outside[0])
        return out

    # The reference runs FIRST in an empty sandbox, and the seed is applied only
    # if that run says the sandbox is what broke it. Seeding unconditionally
    # trades one blind spot for another: py-dbfc5ba3b7a3 opens a file it expects
    # to be missing and prints what it caught, and a seeded `nope.txt` deletes
    # the branch the program was written to exercise. So the rule is narrow —
    # seed only where the REFERENCE itself died of `FileNotFoundError`, which is
    # exactly the class the empty cwd manufactures and nothing else.
    seed = seed_files(program)

    with _Sandbox("ref") as cwd:
        ref = eng.run(CPYTHON, program, binary=ref_bin, argv_tail=argv_tail, stdin=stdin,
                      cwd=cwd, timeout=timeout, env=_env_for(cwd))
    if ref.timed_out:
        # Same deadline on both sides, so a timeout here is the program being
        # slow, not an engine being wrong. Scoring it either way would be a lie.
        out.skip = Skip(out.entry_id, "reference timed out after %gs" % timeout)
        return out
    if seed and ref.returncode and stderr_shape(ref.stderr)[1] in _MISSING_FILE:
        with _Sandbox("ref", seed) as cwd:
            seeded_ref = eng.run(CPYTHON, program, binary=ref_bin, argv_tail=argv_tail,
                                 stdin=stdin, cwd=cwd, timeout=timeout, env=_env_for(cwd))
        if seeded_ref.timed_out:
            # The seed turned a program that failed instantly into one that runs
            # forever. Drop the seed rather than the entry: the unseeded pair is
            # still a comparison, and a timeout would throw the entry away.
            seed = {}
        else:
            ref = seeded_ref
            out.seeded = len(seed)
    else:
        seed = {}

    for arm in arms:
        if arm == CPYTHON:
            # The reference against itself: recorded so a caller who asks for the
            # arm gets its wall time, never to be interpreted as agreement.
            out.verdicts[arm] = Verdict(CPYTHON, out.entry_id, MATCH, "reference", "",
                                        expected_rc=ref.returncode, actual_rc=ref.returncode,
                                        wall_ns=ref.wall_ns)
            continue
        if arm == MIXTURE:
            with _Sandbox("mix", seed) as cwd:
                # `env=` and not the ambient environment, which is the whole
                # point of _env_for and the one arm that used to skip it. A
                # mixture child without PYTHONHASHSEED=0 disagrees with the
                # reference at random on any program where set order is
                # observable; one without LC_ALL=C.UTF-8 decodes every non-ASCII
                # byte to U+FFFD, so two engines printing DIFFERENT non-ASCII
                # compare equal and a MISMATCH is scored MATCH.
                # `ledger=False` for the reason `_env_for` redirects the capture
                # log: the route ledger records what REAL sessions hit, and a
                # battery run would fold the shipped corpus into it — after
                # which `lypning routes` and `conformance --plan` would rank the
                # same programs and the second signal would be the first one
                # again. It changes no verdict either way; the store is never
                # read while routing.
                d = eng.dispatch(program, argv_tail=argv_tail, stdin=stdin, cwd=cwd,
                                 timeout=timeout, env=_env_for(cwd), ledger=False)
            got = d.result
            # End to end is what the caller pays: every refused tier plus the one
            # that answered.
            got.wall_ns = sum(a.wall_ns for a in d.attempts) + got.wall_ns
            out.predicted, out.route_kind, out.route_detail = (
                d.route.engine, d.route.kind, d.route.detail)
            out.verdicts[arm] = classify(ref, got, MIXTURE, entry)
            continue
        if arm == MIXTURE_RUST:
            # `lypning run`: the binary routes and falls onward itself. Its
            # siblings and the other tiers are pinned the way the Python
            # dispatcher's are, so the two walk the same ladder over the same
            # binaries — a disagreement is then the dispatcher, never the build.
            with _Sandbox("mixr", seed) as cwd:
                env = _env_for(cwd)
                if ref_bin:
                    env[eng.env_var_for(CPYTHON)] = str(ref_bin)
                for name in eng.ENGINE_ORDER:
                    if name != CPYTHON and name != LYPNING and binaries.get(name):
                        env[eng.env_var_for(name)] = str(binaries[name])
                got = eng.run(LYPNING, program, binary=binaries.get(LYPNING), argv_tail=argv_tail,
                              stdin=stdin, cwd=cwd, timeout=timeout, env=env, prefix=("run",))
            got.engine = MIXTURE_RUST
            out.verdicts[arm] = classify(ref, got, MIXTURE_RUST, entry)
            continue
        if arm == LIBRARY:
            # In-process, so there is no child to give a cwd to: `run_library`
            # chdirs under a lock instead, which is why this arm serialises
            # while the spawned arms do not.
            with _Sandbox("lib", seed) as cwd:
                got = eng.run_library(program, argv_tail=argv_tail, stdin=stdin, cwd=cwd,
                                      step_limit=LIBRARY_STEP_LIMIT, env=_env_for(cwd))
            out.verdicts[arm] = classify(ref, got, LIBRARY, entry)
            continue
        with _Sandbox(arm, seed) as cwd:
            got = eng.run(arm, program, binary=binaries.get(arm), argv_tail=argv_tail,
                          stdin=stdin, cwd=cwd, timeout=timeout, env=_env_for(cwd))
        out.verdicts[arm] = classify(ref, got, arm, entry)
    return out


# --- the battery -------------------------------------------------------------


def run(
    entries: Optional[Sequence[Any]] = None,
    *,
    engines: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
    timeout: float = DEFAULT_TIMEOUT,
    workers: Optional[int] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Report:
    """Run the whole battery and return the report. Never raises for a verdict.

    ``entries`` defaults to the shipped corpus, ``engines`` to every tier that is
    actually built plus the mixture — an engine that is not built is reported as
    an absent arm rather than as an arm that failed, because "not built" and
    "wrong" are different facts and only one of them is a bug.

    Threads, not processes: every unit of work is a subprocess, so the GIL is
    released for all of it.
    """
    started = time.perf_counter()
    root = paths.project_dir()
    before = _snapshot(root)

    pool: List[Any] = []
    arms: List[str] = []
    seeded: Tuple[int, int] = (0, 0)
    text_view = 0
    unbuilt: List[str] = []
    unbuilt_why: Dict[str, str] = {}
    ref_bin: Optional[Path] = None
    reports: Dict[str, EngineReport] = {}
    routing_errors: List[RoutingError] = []
    routes: Dict[str, eng.Route] = {}
    skipped: List[Skip] = []
    # The net closes on EVERY path out of here, the ones that leave by raising
    # included: by then every entry before the failure has already run, so that
    # is the path where the tree is most likely to be holding a corpus program's
    # writes. Nothing between here and the finally may return early.
    try:
        pool = list(entries) if entries is not None else corpus.load_default()
        if limit is not None and limit >= 0:
            pool = pool[:limit]

        # The ladder PLUS the oracles: lypning-mp is not a routing destination
        # any more, but `--engine lypning-mp` must still run it. Building this
        # map from ENGINE_ORDER alone would report the oracle as "not built" on
        # a machine that has it — an absent arm and a deliberately-unrouted one
        # rendering identically is exactly how an oracle stops being measured.
        binaries: Dict[str, Optional[Path]] = {
            e: eng.find(e) for e in eng.ENGINE_ORDER + eng.ORACLES}
        ref_bin = binaries.get(CPYTHON)

        wanted = list(engines) if engines is not None else list(DEFAULT_ARMS)
        for a in wanted:
            if a == LIBRARY:
                # "Not built" is a missing arm, not a failed one — the same rule
                # the engine binaries get, applied to the artefact a host links.
                # Loadability is checked here rather than per entry: a stale
                # library fails identically 800 times, and 800 MISMATCHes would
                # bury the one line that says to rebuild it.
                usable, why = eng.library_ready()
                if usable:
                    arms.append(a)
                else:
                    # The reason, not the arm name: `render` appends "is not
                    # built" to whatever it is given, and a name carrying its own
                    # reason came out as "library (not built — run `lypning build
                    # --lib`) is not built". Worse, an ABI-incompatible library
                    # was reported as absent, which is the one thing it is not.
                    unbuilt.append(a)
                    unbuilt_why[a] = why
            elif a in (MIXTURE, CPYTHON) or binaries.get(a) is not None:
                arms.append(a)
            elif a == MIXTURE_RUST and binaries.get(LYPNING) is not None:
                arms.append(a)
            else:
                unbuilt.append(a)

        n_workers = workers if workers else min(8, os.cpu_count() or 1)
        results: List[_EntryResult] = []
        done = 0
        lock = threading.Lock()

        def tick(r: _EntryResult) -> _EntryResult:
            nonlocal done
            with lock:
                done += 1
                if progress is not None:
                    progress(done, len(pool))
            return r

        if pool:
            with ThreadPoolExecutor(max_workers=max(1, n_workers)) as ex:
                futures = [ex.submit(_run_entry, e, arms, binaries, ref_bin, timeout) for e in pool]
                for f in futures:
                    results.append(tick(f.result()))

        skipped = [r.skip for r in results if r.skip is not None]
        scored = [r for r in results if r.skip is None]

        for arm in arms:
            vs = [r.verdicts[arm] for r in scored if arm in r.verdicts]
            counts = {v: 0 for v in VERDICTS}
            for v in vs:
                counts[v.verdict] = counts.get(v.verdict, 0) + 1
            total = len(vs)
            reports[arm] = EngineReport(
                engine=arm,
                match=counts[MATCH], unsupported=counts[UNSUPPORTED], mismatch=counts[MISMATCH],
                total=total,
                coverage=(100.0 * counts[MATCH] / total) if total else 0.0,
                verdicts=vs,
            )

        routing_errors = _routing_errors(scored, arms)
        dispatchers = None
        disagreements: List[str] = []
        if MIXTURE in arms and MIXTURE_RUST in arms:
            compared = 0
            for r in scored:
                a, b = r.verdicts.get(MIXTURE), r.verdicts.get(MIXTURE_RUST)
                if a is None or b is None:
                    continue
                compared += 1
                # stdout is part of the comparison only where the grader
                # compared it: a clock or a pid differs between two runs of the
                # SAME dispatcher, and that is not a disagreement.
                uncompared = "stdout uncompared" in (a.detail, b.detail)
                same = (a.verdict, a.actual_rc) == (b.verdict, b.actual_rc) and (
                    uncompared or a.stdout_digest == b.stdout_digest)
                if not same:
                    disagreements.append("%s: python %s rc=%s, rust %s rc=%s"
                                         % (r.entry_id, a.verdict, a.actual_rc, b.verdict, b.actual_rc))
            dispatchers = (compared - len(disagreements), compared)
        monotone = None
        monotone_violations: List[str] = []
        pairs = [(a, b) for a, b in zip(eng.SPECTRUM, eng.SPECTRUM[1:]) if a in arms and b in arms]
        if pairs:
            compared = 0
            for r in scored:
                for a, b in pairs:
                    va, vb = r.verdicts.get(a), r.verdicts.get(b)
                    if va is None or vb is None:
                        continue
                    compared += 1
                    if va.verdict != MATCH:
                        continue
                    uncompared = "stdout uncompared" in (va.detail, vb.detail)
                    if vb.verdict != MATCH or (not uncompared and va.stdout_digest != vb.stdout_digest):
                        monotone_violations.append("%s: %s %s, %s %s" % (r.entry_id, a, va.verdict, b, vb.verdict))
            monotone = (len(monotone_violations), compared)
        routes = {r.entry_id: eng.Route(r.predicted, r.route_kind, r.route_detail)
                  for r in scored if r.predicted}
        seeded = (sum(1 for r in results if r.seeded), sum(r.seeded for r in results))
        text_view = sum(1 for e in pool if waiver_basis(e) == "text")
    finally:
        damage = close_net(root, before)

    return Report(
        engines=reports,
        routing_errors=routing_errors,
        dispatchers=dispatchers,
        disagreements=disagreements,
        monotone=monotone,
        monotone_violations=monotone_violations,
        skipped=skipped,
        seconds=time.perf_counter() - started,
        routes=routes,
        damage=damage,
        unbuilt=unbuilt,
        unbuilt_why=unbuilt_why,
        reference=str(ref_bin or ""),
        total=len(pool),
        seeded=seeded,
        text_view=text_view,
    )


def _routing_errors(scored: Sequence[_EntryResult], arms: Sequence[str]) -> List[RoutingError]:
    """Only the fatal routing outcomes: routed somewhere wrong, or not routed."""
    order = [a for a in eng.ENGINE_ORDER if a in arms]
    out: List[RoutingError] = []
    for r in scored:
        if not r.predicted:
            continue
        if r.route_kind == "unbuilt":
            continue  # no classifier built at all: nothing here to grade
        if r.route_kind in ("route-failed", "route-unparseable"):
            out.append(RoutingError(r.entry_id, r.predicted, "",
                                    "%s: %s" % (r.route_kind, r.route_detail)))
            continue
        got = r.verdicts.get(r.predicted)
        if got is None:
            # A tier that exists but was not one of this run's arms is not an
            # error; a name that is not a tier at all means the classifier told
            # the dispatcher to run something that cannot be run.
            if r.predicted not in eng.ENGINE_ORDER:
                out.append(RoutingError(r.entry_id, r.predicted, "",
                                        "route named an engine that does not exist"))
            continue
        if got.verdict != MISMATCH:
            continue
        ideal = next((e for e in order if r.verdicts.get(e) and r.verdicts[e].verdict == MATCH), "")
        if not ideal:
            continue  # no engine ran it correctly: not the classifier's fault
        rescued = r.verdicts.get(MIXTURE)
        note = " (dispatcher recovered)" if rescued and rescued.verdict == MATCH else ""
        out.append(RoutingError(r.entry_id, r.predicted, ideal,
                                "%s: %s%s" % (got.kind, got.detail, note)))
    return out


# --- reporting ---------------------------------------------------------------


def plan(report: Report) -> List[Tuple[str, int, List[str]]]:
    """The build order: refusal features ranked by what they COST.

    ``(feature, blocks, example_ids)``, most costly first. A program is blocked
    by the FIRST thing it hits, so the counts are a lower bound that shifts as
    features land — which is the point. Re-run after each one.

    **Ranked by CPython reach, not by block count, and the difference is the
    whole value of this function.** A tier-1 refusal that the classifier sends
    to lypning-mp costs that tier's spawn; one that reaches CPython costs
    roughly thirty times as much. Ranking by block count alone put `import re`
    first at 182 programs — of which 176 are answered by lypning-mp and 6 reach
    CPython — and `import pathlib` third at 83 programs, of which **none** reach
    CPython at all. Measured 2026-08-31: those two rows are worth 0.07 s and
    0.00 s, while `.__name__()` at 22 programs, ranked sixth by count, is worth
    0.24 s. Two iterations of this loop were spent proposing the top rows before
    the routing was measured, which is what this ordering exists to prevent.

    Falls back to block count when :attr:`Report.routes` is empty — the mixture
    arm did not run, so there is nothing to say about destinations, and a count
    is still a truthful lower bound on what a feature unblocks.

    Taken from the Rust core's arm when it is present: it is the tier the corpus
    is a build order *for*. The mixture arm never appears here, because a
    dispatcher that reaches CPython refuses nothing.
    """
    source = None
    for name in eng.SPECTRUM:
        if name in report.engines:
            source = report.engines[name]
            break
    if source is None:
        return []
    blocks: Dict[str, int] = {}
    reaching_cpython: Dict[str, int] = {}
    ids: Dict[str, List[str]] = {}
    for v in source.verdicts:
        if v.verdict != UNSUPPORTED:
            continue
        key = "%s: %s" % (v.kind, v.detail)
        blocks[key] = blocks.get(key, 0) + 1
        route = report.routes.get(v.entry_id)
        if route is not None and route.engine == eng.CPYTHON:
            reaching_cpython[key] = reaching_cpython.get(key, 0) + 1
        bucket = ids.setdefault(key, [])
        if len(bucket) < 4:
            bucket.append(v.entry_id)
    if report.routes:
        order = sorted(blocks, key=lambda k: (-reaching_cpython.get(k, 0), -blocks[k], k))
    else:
        order = sorted(blocks, key=lambda k: (-blocks[k], k))
    return [(k, blocks[k], ids[k]) for k in order]


def plan_cost(report: Report) -> Dict[str, int]:
    """``{feature: programs that reach CPython}`` — the ranking key of :func:`plan`.

    Separate from :func:`plan` so its return shape stays a three-tuple that
    existing callers can keep unpacking. Empty when the mixture arm did not run.
    """
    source = None
    for name in eng.SPECTRUM:
        if name in report.engines:
            source = report.engines[name]
            break
    if source is None or not report.routes:
        return {}
    out: Dict[str, int] = {}
    for v in source.verdicts:
        if v.verdict != UNSUPPORTED:
            continue
        route = report.routes.get(v.entry_id)
        if route is not None and route.engine == eng.CPYTHON:
            key = "%s: %s" % (v.kind, v.detail)
            out[key] = out.get(key, 0) + 1
    return out


# `render(report, plan=True)` shadows the name inside that function, so the
# function object is bound here, once, where it is still reachable.
_plan_rows = plan


def _pad(s: Any, n: int) -> str:
    s = str(s)
    return s if len(s) >= n else s + " " * (n - len(s))


def render(report: Report, plan: bool = False) -> str:
    """The human view. The only place in this module that formats for a terminal."""
    out: List[str] = []
    if plan:
        rows = _plan_rows(report)
        cost = plan_cost(report)
        total = max((r.total for r in report.engines.values()), default=report.total)
        out.append("build order — %d distinct blockers over %d programs" % (len(rows), total))
        out.append("(a program is blocked by the FIRST thing it hits, so counts shift"
                   " as features land)")
        if cost:
            out.append("ranked by ->cpy: the refusals that reach CPython, which is what")
            out.append("costs. Until 2026-09-04 a refusal could land on the MicroPython")
            out.append("tier at ~30x less and the two columns diverged; that tier left the")
            out.append("chain, so every refusal below costs a CPython spawn. This is the")
            out.append("build order for the larger spectrum variant.")
            out.append("")
            out.append("%s %s %s  %s" % ("->cpy".rjust(6), "blocks".rjust(7),
                                         _pad("blocker", 44), "e.g."))
        else:
            out.append("(the mixture arm did not run, so there is no destination to rank"
                       " by; these are block counts)")
            out.append("")
        for feature, blocks, ids in rows[:40]:
            if cost:
                out.append("%s %s %s  %s" % (str(cost.get(feature, 0)).rjust(6),
                                             str(blocks).rjust(7), _pad(feature, 44),
                                             ", ".join(ids[:2])))
            else:
                out.append("%s  %s  e.g. %s" % (str(blocks).rjust(4), _pad(feature, 46),
                                                ", ".join(ids[:3])))
        if len(rows) > 40:
            # The tail is one or two programs each, but a list that stops
            # without saying so reads as the whole list.
            out.append("  … %d more blockers, %d programs between them"
                       % (len(rows) - 40, sum(b for _f, b, _i in rows[40:])))
        by_kind: Dict[str, int] = {}
        for feature, blocks, _ids in rows:
            kind = feature.split(":", 1)[0]
            by_kind[kind] = by_kind.get(kind, 0) + blocks
        if by_kind:
            out.append("")
            out.append("by kind:")
            for kind in sorted(by_kind, key=lambda k: (-by_kind[k], k)):
                out.append("%s  %s" % (str(by_kind[kind]).rjust(4), kind))
        return "\n".join(out) + "\n"

    n = max((r.total for r in report.engines.values()), default=0)
    out.append("conformance over %d corpus programs in %.1fs (reference: %s)"
               % (n, report.seconds, report.reference or "none"))
    out.append("")
    for name in report.unbuilt:
        why = report.unbuilt_why.get(name)
        out.append("note: %s was not measured — %s" % (name, why) if why
                   else "note: %s is not built — that arm was not measured" % name)
    if report.unbuilt:
        out.append("")
    out.append("engine       MATCH  UNSUPPORTED  MISMATCH   coverage")
    for name, r in report.engines.items():
        out.append("%s %s  %s  %s   %s%%" % (
            _pad(name, 11), str(r.match).rjust(5), str(r.unsupported).rjust(11),
            str(r.mismatch).rjust(8), ("%.1f" % r.coverage).rjust(6)))

    shown = 0
    for name, r in report.engines.items():
        for v in r.failures():
            shown += 1
            if shown <= 25:
                out.append("  MISMATCH  %s %s: %s: %s"
                           % (_pad(name, 10), v.entry_id, v.kind, v.detail))
    if shown > 25:
        out.append("  … %d more" % (shown - 25))

    # What the MATCHes rested on. A coverage percentage is unreadable without
    # it: two runs agreeing that a program raised FileNotFoundError is a MATCH,
    # and it is not the same fact as two runs agreeing on a page of output.
    out.append("")
    out.append("what the MATCHes compared — an agreement about nothing is still an"
               " agreement, but not the same one:")
    out.append("engine       MATCH  stdout  stderr  exit only  both failed")
    for name, r in report.engines.items():
        out.append("%s %s  %s  %s  %s  %s" % (
            _pad(name, 11), str(r.match).rjust(5), str(r.match_stdout).rjust(6),
            str(r.match_stderr).rjust(6), str(r.match_exit_only).rjust(9),
            str(r.match_both_failed).rjust(11)))
    out.append("  exit only    = neither arm wrote anything on either stream; the exit"
               " code is the whole verdict")
    out.append("  both failed  = both arms exited non-zero with the same code — real"
               " agreement, cheapest kind")
    if report.seeded[0]:
        out.append("  seeded %d entries with %d files so the sandbox was not the reason"
                   " both arms failed" % report.seeded)
    if report.text_view:
        out.append("  %d programs do not parse: their stdout waiver was decided on the"
                   " program text, not its AST" % report.text_view)

    if report.skipped:
        out.append("")
        reasons: Dict[str, int] = {}
        for s in report.skipped:
            head = s.reason.split(":", 1)[0]
            reasons[head] = reasons.get(head, 0) + 1
        out.append("skipped %d not run: %s" % (
            len(report.skipped),
            ", ".join("%s (%d)" % (k, v) for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]))))

    if report.routing_errors:
        out.append("")
        out.append("routing errors %d (must be 0) — a route that costs correctness, not time:"
                   % len(report.routing_errors))
        for e in report.routing_errors[:10]:
            out.append("  %s: predicted %s, ideal %s — %s"
                       % (e.entry_id, e.predicted, e.ideal or "none", e.detail))

    if report.monotone is not None:
        bad, compared = report.monotone
        out.append("")
        out.append("monotone violations %d over %d — a larger variant never does worse than a "
                   "smaller one on a program both ran%s" % (bad, compared, "" if not bad else ":  FAIL"))
        for d in report.monotone_violations[:10]:
            out.append("  %s" % d)

    if report.dispatchers is not None:
        agreed, compared = report.dispatchers
        out.append("")
        out.append("dispatchers agree %d/%d — the Python dispatcher (`mixture`) and the "
                   "Rust one (`mixture-rust`, what `lypning run` execs) over the same "
                   "binaries%s" % (agreed, compared, "" if agreed == compared else ":  FAIL"))
        for d in report.disagreements[:10]:
            out.append("  %s" % d)

    if report.damage:
        out.append("")
        n = len(report.damage)
        out.append("!! %d repository file%s changed by corpus programs and %s been"
                   " restored:" % (n, "" if n == 1 else "s",
                                   "has" if n == 1 else "have"))
        for d in report.damage[:20]:
            out.append("   %s" % d)
        if len(report.damage) > 20:
            out.append("   … and %d more" % (len(report.damage) - 20))
        out.append("   the run is a failure regardless of its verdicts")

    out.append("")
    out.append("MISMATCH %d — %s" % (report.mismatches, "ok" if report.ok else "FAIL"))
    return "\n".join(out) + "\n"
