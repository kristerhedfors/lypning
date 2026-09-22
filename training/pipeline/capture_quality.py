"""Which captured programs are worth an author's time: a static verdict, no execution.

WHAT THIS IS FOR. The capture log (``~/.lypning/invocations.jsonl``) is what the
agents actually typed while working in this repository, and almost all of it is
repository work: patch a file, drive the engine, glue one command's JSON into
the next. Measured with this module on 2026-09-22 (``nt capture-export`` over a
7,997-line log, ``DATA_PRODUCTION.md`` *Capture tier*): 204 of 5,046 distinct
programs are pure, deterministic and non-trivial enough to write a task for.
This module is the filter that finds those without running anything, so that the expensive half of the route — regeneration in
``eval2_select``, authoring, the differential oracle in ``eval2_bank`` — only
ever sees programs it could use.

IT EXECUTES NOTHING. Every rule is decided from the text and its ``ast``. That
is the point, not a limitation: a captured program that writes files, spawns a
process or reads ``~/.ssh`` is exactly the kind this filter exists to keep away
from an interpreter, so the filter cannot be the thing that runs it. The pinning
test monkeypatches ``subprocess`` and ``os.system`` and classifies the whole
fixture set.

THE RULES, IN ORDER. A program is charged to the FIRST rule that rejects it, so
per-rule counts sum to the number rejected and a report can be read as a
funnel. The order is the one the plan's first read-only census used (127 of
4,934 on 2026-09-22), so the funnels line up. The definitions are not
identical — this module counts a comprehension as a loop, for one — so its
tier A is not expected to reproduce that 127:

``unparseable``   ``ast.parse`` fails (a fragment, a shell line, a REPL paste)
``repo-import``   imports a module that only resolves inside this repository
``process``       subprocess, os.system and friends, multiprocessing, signals
``network``       socket, urllib, http and the rest of eval2_select's list
``environment``   reads or writes the process environment
``writes-files``  eval2_select's own writer detector, unchanged
``reads-files``   opens, globs, lists, stats or builds a path to anything
``third-party``   imports something outside the standard library
``stdin-glue``    reads stdin and is under :data:`MIN_STDIN_NODES` nodes
``trivial``       under :data:`MIN_NODES` nodes, or has no def and no loop
``unseeded``      clocks, pids, unseeded ``random``, ``uuid``, ``secrets``
``privacy``       a ``harvest.redact`` hit, an email, a home or absolute path

Reused, not reimplemented, where the repository already decides the question:
``eval2_select.writes_files`` and its process/network module lists, and
``lypning.harvest.redact``. Two copies of a rule is one copy too many.

Library code; returns data, prints nothing (invariant 8). Stdlib only.
"""

from __future__ import annotations

import ast
import functools
import re
import sys
import threading
import warnings
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

from .eval2_select import (_NETWORK_MODULES, _SUBPROCESS_CALLS, _SUBPROCESS_MODULES,
                           _WRITER_DOTTED, _dotted, _imported_roots, writes_files)

#: Every rule, in the order it is applied.
RULES = (
    "unparseable", "repo-import", "process", "network", "environment",
    "writes-files", "reads-files", "third-party", "stdin-glue", "trivial",
    "unseeded", "privacy",
)

#: The rules whose programs must never be EXECUTED by the pipeline, as opposed
#: to merely being poor training material. ``lypning_source.classify_entry``
#: gates on these; ``trivial`` or ``third-party`` programs are harmless to run.
HAZARDS = ("process", "network", "writes-files")

#: A program under this many AST nodes is a one-liner an author cannot write a
#: task for (``print(2**64)`` is 7). 40 is the 2026-09-22 measurement's cut.
MIN_NODES = 40

#: A stdin reader this small is glue between two shell commands
#: (``json.load(sys.stdin)["x"]``), not a program with a contract of its own.
MIN_STDIN_NODES = 60

_ROOT = Path(__file__).resolve().parents[2]

_PROCESS_EXTRA_MODULES = frozenset(("signal", "pty", "resource"))
_PROCESS_DOTTED = frozenset(("os.kill", "os.killpg", "os.wait", "os.waitpid", "os.abort"))
_ENV_DOTTED = frozenset(("os.environ", "os.getenv", "os.putenv", "os.unsetenv",
                         "os.environb", "os.getenvb"))
_ENV_NAMES = frozenset(("environ", "getenv", "putenv", "unsetenv"))

_READ_MODULES = frozenset((
    "glob", "fileinput", "zipfile", "tarfile", "shelve", "dbm", "sqlite3", "mmap",
    "linecache", "filecmp", "shutil", "tempfile", "gzip", "bz2", "lzma", "zipimport",
))
_READ_CALLS = frozenset(("open", "io.open", "codecs.open", "tokenize.open"))
_PATH_TYPES = frozenset(("Path", "PurePath", "PosixPath", "WindowsPath",
                         "PurePosixPath", "PureWindowsPath"))
_READ_ATTRS = frozenset(("read_text", "read_bytes", "iterdir", "glob", "rglob",
                         "is_file", "is_dir", "resolve", "readlink", "open"))
_READ_DOTTED = frozenset((
    "os.listdir", "os.scandir", "os.walk", "os.stat", "os.lstat", "os.access",
    "os.readlink", "os.getcwd", "os.chdir", "os.path.exists", "os.path.isfile",
    "os.path.isdir", "os.path.getsize", "os.path.getmtime", "os.path.realpath",
    "os.path.abspath", "os.path.expanduser", "os.path.islink", "os.path.lexists",
    "os.path.samefile", "os.fspath", "pathlib.Path", "pathlib.PurePath",
))

_CLOCK_DOTTED = frozenset((
    "time.time", "time.time_ns", "time.monotonic", "time.monotonic_ns",
    "time.perf_counter", "time.perf_counter_ns", "time.process_time",
    "time.process_time_ns", "time.localtime", "time.gmtime", "time.ctime",
    "time.asctime", "time.strftime", "time.thread_time",
    "datetime.now", "datetime.today", "datetime.utcnow", "date.today",
    "datetime.datetime.now", "datetime.datetime.today", "datetime.datetime.utcnow",
    "datetime.date.today", "os.urandom", "os.getrandom", "os.times", "os.getpid",
    "os.getppid",
))
_ALWAYS_RANDOM = frozenset(("secrets", "uuid"))

#: Spellings that import a module named by a STRING. ``__import__('subprocess')``
#: is the commonest one-liner import there is, and a detector that only reads
#: ``import`` statements never sees it — nor ``from os import system``, nor
#: ``import os as o; o.system(...)``, nor ``getattr(os, "system")``. Each of those
#: is resolved to the dotted name it means before any rule looks (:func:`_facts`).
_DYNAMIC_IMPORTS = frozenset(("__import__", "builtins.__import__",
                              "importlib.import_module", "importlib.__import__"))
_GETATTR = frozenset(("getattr", "builtins.getattr"))
#: The root charged for a dynamic import whose module is not a literal: it is
#: never stdlib, so such a program can never be tier A, and never a repo module.
DYNAMIC_ROOT = "<dynamic>"
_OPENERS = frozenset(("open", "io.open", "codecs.open", "builtins.open"))

# The email and home-path shapes are text rules: they must fire on a comment or
# a docstring as readily as on a literal, because the training text is the
# whole program and a comment leaks as well as a string does.
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_HOME = re.compile(r"(?:/Users/|/home/|(?<![A-Za-z0-9_])~/)")
_ABS_LITERAL = re.compile(r"^/(?:[A-Za-z0-9._@+-]+/)*[A-Za-z0-9._@+-]+/?$")


# --- what is local, what is stdlib -------------------------------------------


def _stems(directory: Path) -> Set[str]:
    out: Set[str] = set()
    if not directory.is_dir():
        return out
    for child in directory.iterdir():
        if child.suffix == ".py" and child.stem.isidentifier():
            out.add(child.stem)
        elif child.is_dir() and child.name.isidentifier() and any(child.glob("*.py")):
            out.add(child.name)
    return out


@functools.lru_cache(maxsize=None)
def stdlib_names() -> Optional[FrozenSet[str]]:
    """``sys.stdlib_module_names``, or None below 3.10 — the caller abstains then.

    None rather than an empty set, for the reason ``levers.stdlib_names`` gives:
    an empty set would make every import third-party and report that as a
    finding. Here the effect of None is that ``third-party`` is decided by
    ``sys.builtin_module_names`` plus a filesystem lookup of where the module
    WOULD load from — :func:`is_stdlib` — which imports nothing.
    """
    names = getattr(sys, "stdlib_module_names", None)
    return frozenset(names) if names else None


@functools.lru_cache(maxsize=None)
def is_stdlib(root: str) -> bool:
    names = stdlib_names()
    if names is not None:
        return root in names
    if root in sys.builtin_module_names:
        return True
    # Python 3.9: find where the module would come from without importing it.
    # ``find_spec`` on a TOP-LEVEL name runs the finders and nothing else.
    import importlib.util
    import sysconfig
    try:
        spec = importlib.util.find_spec(root)
    except (ImportError, ValueError):
        return False
    origin = getattr(spec, "origin", None) or ""
    stdlib = sysconfig.get_paths().get("stdlib") or ""
    return bool(origin and stdlib and origin.startswith(stdlib)
                and "site-packages" not in origin)


@functools.lru_cache(maxsize=None)
def repo_modules(root: Optional[str] = None) -> FrozenSet[str]:
    """Every top-level name an import could resolve to only inside this tree.

    Computed from the tree rather than listed, because the list is what goes
    stale: ``src/`` and ``src/lypning/``, every directory under ``training/``
    and the modules inside each (``pipeline``, and ``verified_stages`` from
    ``training/gpu``, which an agent imports with that directory as its cwd),
    ``tests/``, ``study/`` and ``.github/scripts``. A name the standard library
    also has is NOT local: ``import stats`` is ours, ``import json`` never is,
    whatever a file in the tree happens to be called.
    """
    base = Path(root) if root else _ROOT
    names: Set[str] = {"lypning", "pipeline", "ntx", "tests", "conftest"}
    names |= _stems(base / "src")
    names |= _stems(base / "src" / "lypning")
    training = base / "training"
    if training.is_dir():
        for child in training.iterdir():
            if child.is_dir() and child.name.isidentifier():
                names.add(child.name)
                names |= _stems(child)
    names |= _stems(base / "tests")
    names |= _stems(base / "study")
    names |= _stems(base / ".github" / "scripts")
    # ``__init__``/``__main__`` are file names, not anything an import names.
    return frozenset(n for n in names if not n.startswith("__") and not is_stdlib(n))


# --- the walk -----------------------------------------------------------------


_WARNINGS_LOCK = threading.RLock()


def _quiet(fn: Any) -> Any:
    """Run ``fn`` with warnings silenced.

    Captured programs are full of ``"\\d"`` in plain strings, and on 3.12+ every
    parse of one — ours, and the ones inside the detectors reused from
    ``eval2_select`` and ``lypning.conformance`` — is a ``SyntaxWarning`` on
    stderr: screens of noise over a real log, none of which is a verdict. A
    warning is not a rule here.
    """
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # ``catch_warnings`` saves and restores the PROCESS-wide filter list, so
        # two threads interleaving it (``nt classify`` runs ``hazard`` from a
        # pool) can restore each other's "ignore" and leave it on for good. The
        # lock makes every save/restore pair nest; the verdict is cheap.
        with _WARNINGS_LOCK, warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return fn(*args, **kwargs)
    return wrapper


def _parse(program: str) -> Optional[ast.AST]:
    """``ast.parse``, or None when it cannot parse."""
    try:
        return ast.parse(program or "")
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return None


def _node_count(tree: ast.AST) -> int:
    return sum(1 for _ in ast.walk(tree))


def _import_from_names(tree: ast.AST) -> Set[str]:
    """``(module, name)`` pairs of every ``from m import n``, as ``m.n``."""
    out: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                out.add("%s.%s" % (node.module, alias.name))
    return out


def _calls(tree: ast.AST) -> List[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def _dotted_refs(tree: ast.AST) -> Set[str]:
    """Every dotted name the program mentions, called or not (``os.environ[...]``)."""
    out: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Attribute, ast.Name)):
            name = _dotted(node)
            if name:
                out.add(name)
    return out


def _aliases(tree: ast.AST) -> Dict[str, str]:
    """Local name -> the dotted name it is bound to by an import statement."""
    out: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    out[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                if alias.name != "*":
                    out[alias.asname or alias.name] = "%s.%s" % (node.module, alias.name)
    return out


def _const_str(node: Any) -> Optional[str]:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _resolve(node: Any, aliases: Dict[str, str], depth: int = 0) -> str:
    """The dotted name ``node`` means, import aliases expanded, or ``""``.

    ``o.system`` after ``import os as o`` is ``os.system``; ``system`` after
    ``from os import system`` is ``os.system``; ``__import__("os").system`` and
    ``getattr(os, "system")`` are ``os.system`` too. A local variable that
    shadows an imported name resolves as the import — an over-match, which here
    costs one candidate and never runs anything.
    """
    parts: List[str] = []
    while depth < 50:
        depth += 1
        if isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
            continue
        if isinstance(node, ast.Call):
            fn = _resolve(node.func, aliases, depth)
            if fn in _DYNAMIC_IMPORTS and node.args and _const_str(node.args[0]):
                head = str(_const_str(node.args[0]))
                break
            if fn in _GETATTR and len(node.args) >= 2 and _const_str(node.args[1]):
                parts.append(str(_const_str(node.args[1])))
                node = node.args[0]
                continue
            return ""
        if isinstance(node, ast.Name):
            head = aliases.get(node.id, node.id)
            break
        return ""
    else:
        return ""
    return ".".join([head] + list(reversed(parts)))


def _facts(tree: ast.AST) -> Tuple[Set[str], Set[str], Set[str], Set[str]]:
    """``(roots, refs, froms, called)`` with every spelling of an import resolved.

    ``roots`` adds the modules a ``__import__``/``import_module`` call names (or
    :data:`DYNAMIC_ROOT` when it names one by a non-literal); ``refs`` adds the
    alias-resolved dotted name of every name and attribute; ``called`` is the
    resolved name of every call's callee.
    """
    aliases = _aliases(tree)
    roots = set(_imported_roots(tree))
    refs = _dotted_refs(tree)
    called: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Attribute, ast.Name)):
            name = _resolve(node, aliases)
            if name:
                refs.add(name)
        elif isinstance(node, ast.Call):
            fn = _resolve(node.func, aliases)
            if fn:
                called.add(fn)
                refs.add(fn)
            if fn in _DYNAMIC_IMPORTS:
                mod = _const_str(node.args[0]) if node.args else None
                roots.add(mod.split(".")[0] if mod and not mod.startswith(".") else DYNAMIC_ROOT)
    for name in list(refs):
        if "." in name:
            roots_of = name.split(".")[0]
            if roots_of in _SUBPROCESS_MODULES | _PROCESS_EXTRA_MODULES | _NETWORK_MODULES:
                roots.add(roots_of)
    return roots, refs, _import_from_names(tree), called


def _is_process(tree: ast.AST, program: str, roots: Set[str], refs: Set[str],
                froms: Set[str]) -> bool:
    if roots & (_SUBPROCESS_MODULES | _PROCESS_EXTRA_MODULES):
        return True
    if _SUBPROCESS_CALLS.search(program):
        return True
    if refs & _PROCESS_DOTTED or froms & _PROCESS_DOTTED:
        return True
    if any(_SUBPROCESS_CALLS.search(r + "(") for r in refs | froms):
        return True
    from lypning import conformance as conf  # noqa: WPS433 - lazy, like eval2_select
    return bool(conf.spawns_a_battery(program))


def _writes(tree: ast.AST, program: str, called: Set[str]) -> bool:
    """``eval2_select.writes_files``, plus what it cannot see by construction.

    Its detector reads callee names as spelled, so ``from os import remove``
    then ``remove(p)`` and ``import shutil as s`` then ``s.rmtree(p)`` pass it;
    here the callee is alias-resolved first. An ``open`` whose mode is not a
    literal is taken as a write: the mode is decided at run time, and this
    module does not run anything to find out.
    """
    if writes_files(program):
        return True
    if called & _WRITER_DOTTED:
        return True
    aliases = _aliases(tree)
    for call in _calls(tree):
        if _resolve(call.func, aliases) not in _OPENERS:
            continue
        mode = call.args[1] if len(call.args) > 1 else None
        for kw in call.keywords:
            if kw.arg == "mode":
                mode = kw.value
        if mode is not None and _const_str(mode) is None:
            return True
    return False


def _is_environment(refs: Set[str], froms: Set[str]) -> bool:
    if refs & _ENV_DOTTED:
        return True
    return any(f.startswith("os.") and f[3:] in _ENV_NAMES for f in froms)


def _reads_files(tree: ast.AST, roots: Set[str], refs: Set[str], froms: Set[str]) -> bool:
    if roots & (_READ_MODULES | {"pathlib"}):
        return True
    if refs & _READ_DOTTED or froms & _READ_DOTTED:
        return True
    if refs & ((_READ_CALLS | {"builtins.open"}) - {"open"}):
        return True
    for call in _calls(tree):
        name = _dotted(call.func)
        if name in _READ_CALLS or name.split(".")[-1] in _PATH_TYPES:
            return True
        if isinstance(call.func, ast.Attribute) and call.func.attr in _READ_ATTRS:
            # By attribute alone, receiver unknown: ``Path("x").read_text()``
            # has no dotted name at all, and it is the commonest read there is.
            return True
    return False


def _reads_stdin(refs: Set[str], tree: ast.AST) -> bool:
    if any(r == "sys.stdin" or r.startswith("sys.stdin.") for r in refs):
        return True
    return any(_dotted(c.func) == "input" for c in _calls(tree))


def _has_def_or_loop(tree: ast.AST) -> bool:
    kinds = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.For, ast.AsyncFor,
             ast.While, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    return any(isinstance(n, kinds) for n in ast.walk(tree))


def _is_unseeded(roots: Set[str], refs: Set[str], froms: Set[str], tree: ast.AST) -> bool:
    if roots & _ALWAYS_RANDOM:
        return True
    if refs & _CLOCK_DOTTED or froms & _CLOCK_DOTTED:
        return True
    if "random" in roots:
        # Seeded is ``random.seed(<anything>)`` or a ``random.Random(<arg>)``
        # instance: both make the stream a function of the program text.
        for call in _calls(tree):
            name = _dotted(call.func)
            if name in ("random.seed", "seed") and (call.args or call.keywords):
                return False
            if name in ("random.Random", "Random") and (call.args or call.keywords):
                return False
        return True
    return False


def _privacy(program: str, tree: Optional[ast.AST]) -> bool:
    from lypning import harvest  # noqa: WPS433 - lazy, like eval2_select
    _, hits = harvest.redact(program)
    if hits:
        return True
    if _EMAIL.search(program) or _HOME.search(program):
        return True
    for node in ast.walk(tree) if tree is not None else ():
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and _ABS_LITERAL.match(node.value) and node.value != "/":
            return True
    from lypning import conformance as conf  # noqa: WPS433
    return bool(conf.absolute_paths(program))


# --- the verdict --------------------------------------------------------------


def classify(program: str, *, local: Optional[Iterable[str]] = None) -> str:
    """The first rule that rejects ``program``, or ``""`` when it is tier A.

    ``local`` overrides :func:`repo_modules` (tests pass a fixed set so a fixture
    does not depend on which files this tree happens to hold).
    """
    return verdict(program, local=local)["rule"]


@_quiet
def verdict(program: str, *, local: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """``{"rule": first rejecting rule or "", "nodes": AST size or 0}``."""
    tree = _parse(program)
    if tree is None:
        return {"rule": "unparseable", "nodes": 0}
    if not (program or "").strip():
        return {"rule": "unparseable", "nodes": 0}
    nodes = _node_count(tree)
    local_set = frozenset(local) if local is not None else repo_modules()
    roots, refs, froms, called = _facts(tree)

    def out(rule: str) -> Dict[str, Any]:
        return {"rule": rule, "nodes": nodes}

    if roots & local_set:
        return out("repo-import")
    if _is_process(tree, program, roots, refs, froms):
        return out("process")
    if roots & _NETWORK_MODULES:
        return out("network")
    if _is_environment(refs, froms):
        return out("environment")
    if _writes(tree, program, called):
        return out("writes-files")
    if _reads_files(tree, roots, refs, froms):
        return out("reads-files")
    if any(r == DYNAMIC_ROOT or not is_stdlib(r) for r in roots if r != "__future__"):
        return out("third-party")
    if _reads_stdin(refs, tree) and nodes < MIN_STDIN_NODES:
        return out("stdin-glue")
    if nodes < MIN_NODES or not _has_def_or_loop(tree):
        return out("trivial")
    if _is_unseeded(roots, refs, froms, tree):
        return out("unseeded")
    if _privacy(program, tree):
        return out("privacy")
    return out("")


@_quiet
def hazard(program: str) -> str:
    """The first rule in :data:`HAZARDS` that ``program`` trips, or ``""``.

    Narrower than :func:`classify` on purpose: this answers "may the pipeline
    execute it at all", not "is it worth a task", so a short or third-party
    program passes. An unparseable program also passes — CPython will refuse it
    at compile time, which costs nothing and changes nothing.
    """
    tree = _parse(program)
    if tree is None:
        return ""
    roots, refs, froms, called = _facts(tree)
    if _is_process(tree, program, roots, refs, froms):
        return "process"
    if roots & _NETWORK_MODULES:
        return "network"
    if _writes(tree, program, called):
        return "writes-files"
    return ""


@_quiet
def private(text: str) -> bool:
    """The ``privacy`` rule ALONE, whatever rule a verdict charged first.

    A verdict stops at the first rule that rejects, so a program that reads
    ``~/.ssh`` is charged to ``reads-files`` and never reaches ``privacy``. This
    answers the question an output file has to ask of every row it writes —
    "does this text carry a credential, an email or a home path?" — for text
    that does not even parse (an argv element, a fragment).
    """
    return _privacy(text or "", _parse(text or ""))


def tally(programs: Iterable[str], *, local: Optional[Iterable[str]] = None) -> Dict[str, int]:
    """``rule -> count`` over ``programs``; ``""`` counts tier A. Every rule present."""
    counts: Dict[str, int] = {r: 0 for r in RULES}
    counts[""] = 0
    local_set = frozenset(local) if local is not None else None
    for p in programs:
        counts[classify(p, local=local_set)] += 1
    return counts
