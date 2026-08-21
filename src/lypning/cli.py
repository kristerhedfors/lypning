"""The front door.

One invariant, and everything else here is downstream of it: **a program that
runs under ``python3`` must run under ``lypning`` unchanged**. Things invoke
this binary as if it were an interpreter — a shim on ``$PATH``, a Makefile, a
shebang — and an interpreter that stopped to parse subcommands first would
answer ``lypning -c 'print(1)'`` with an argparse usage error.

So interpreter mode is decided before argparse exists: ``-c``, a file, ``-``,
or any flag that is not one of ours, and the process turns into the Rust core
with :func:`os.execv`. Not :mod:`subprocess` — an exec leaves nothing of this
process behind, so the exit code, the signals and the three stdio handles are
the interpreter's own, and a ``SIGINT`` at the terminal kills the interpreter
rather than a Python parent holding a child.

Subcommands take the other branch, and everything they do lives in a sibling
module: this file resolves arguments, renders what those modules return, and
maps outcomes onto exit codes. That is the whole job.

Exit codes: ``0`` success, ``1`` this command's own failure (a MISMATCH, a
failed gate), ``2`` usage — including "the core is not built", because there is
nothing to run and the fix is a command — and ``90`` passed straight through
from an engine refusal, unmodified, because a caller that dispatches on 90 is
the reason 90 exists.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import UNSUPPORTED_EXIT, __version__, engines, paths

PROG = "lypning"

#: Everything argparse owns. ``argv[1]`` in here is a subcommand; anything else
#: that could plausibly be a program is the interpreter's.
COMMANDS = (
    "run", "route", "build", "lib", "status", "doctor", "install", "uninstall",
    "shim", "hook", "conformance", "fuzz", "bench", "corpus-time", "perf", "gate",
    "harvest", "corpus",
)

#: The only dash-flags this CLI keeps for itself. Every other flag belongs to
#: the interpreter — ``-m``, ``-u``, ``-E``, ``-I`` and whatever the core grows
#: next — and is passed through untouched rather than rejected here.
TOP_FLAGS = frozenset(("-h", "--help", "--version", "-V"))

_NOT_BUILT = (
    "lypning: the Rust core is not built — run `lypning build --rust` "
    "(or point $LYPNING_BIN at a binary)"
)


class Failure(Exception):
    """A failure we understood. One line, no traceback, exit 1."""


class Usage(Exception):
    """The caller asked for something malformed. One line, exit 2."""


# --- interpreter mode --------------------------------------------------------


def _looks_like_program(token: str) -> bool:
    """Is this argv[1] a script rather than a subcommand?

    Existing file first, because that is the unambiguous case. The two
    heuristics after it exist so that a *missing* script still reaches the
    interpreter: ``lypning missing.py`` must say "can't open file", which is
    what every python says, not "invalid choice: 'missing.py'".
    """
    if os.path.exists(token):
        return True
    return token.endswith(".py") or os.sep in token or token.startswith("~")


def interpreter_argv(argv: Sequence[str]) -> Optional[List[str]]:
    """The argv to exec into, or ``None`` when argparse should have it."""
    if not argv:
        return None
    head = argv[0]
    if head in COMMANDS or head in TOP_FLAGS:
        return None
    if head == "-" or head == "-c":
        return list(argv)
    if head.startswith("-"):
        return list(argv)
    return list(argv) if _looks_like_program(head) else None


def exec_interpreter(argv: Sequence[str]) -> int:
    """Become the Rust core. Returns only on failure to do so."""
    try:
        binary = engines.find_lypning()
    except engines.EngineError as e:
        sys.stderr.write("lypning: %s\n" % e)
        return 2
    if binary is None:
        sys.stderr.write(_NOT_BUILT + "\n")
        return 2
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except (OSError, ValueError):
        pass
    try:
        os.execv(str(binary), [str(binary)] + list(argv))
    except OSError as e:
        sys.stderr.write("lypning: cannot exec %s: %s\n" % (binary, e))
        return 2
    return 2  # unreachable: execv either replaces this process or raises


# --- lazy module loading -----------------------------------------------------


def _mod(name: str):
    """Import a sibling on demand.

    Deferred on purpose. Interpreter mode must not pay for :mod:`ast`, and a
    module that is missing or does not import has to degrade to one line about
    that command rather than taking the whole CLI down with it — ``lypning
    status`` still works on a tree where ``bench.py`` does not.
    """
    try:
        return importlib.import_module("." + name, __package__)
    except Exception as e:  # ImportError, SyntaxError, anything at import time
        if os.environ.get("LYPNING_DEBUG") == "1":
            raise
        raise Failure("the %s module is unavailable: %s: %s"
                      % (name, type(e).__name__, e))


# --- small shared helpers ----------------------------------------------------


def _out(text: str) -> None:
    if not text:
        return
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def _json(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, indent=2, default=str) + "\n")


def _plain(obj: Any) -> Any:
    return asdict(obj) if is_dataclass(obj) and not isinstance(obj, type) else obj


def _size_of(p: Optional[Path]) -> int:
    try:
        return p.stat().st_size if p else 0
    except OSError:
        return 0


def _read_program(ns: argparse.Namespace) -> Tuple[str, List[str], Optional[str]]:
    """``(program, argv_tail, stdin)`` from ``-c`` / FILE / ``-``.

    The stdin slot is only filled when the program itself came from stdin —
    then it is empty, because the program ate it. See ``--stdin`` for why this
    does not guess.
    """
    tail = list(getattr(ns, "argv", []) or [])
    if getattr(ns, "command", None) is not None:
        return ns.command, tail, None
    if not tail:
        raise Usage("nothing to run: pass -c PROG, a FILE, or - to read stdin")
    head, rest = tail[0], tail[1:]
    if head == "-":
        return sys.stdin.read(), rest, ""
    try:
        return Path(head).expanduser().read_text(encoding="utf-8"), rest, None
    except OSError as e:
        raise Failure("can't open file '%s': %s" % (head, e.strerror or e))


def _progress(label: str) -> Optional[Callable[..., None]]:
    """A counter on stderr, and only when a human is watching it.

    Not to stdout: ``lypning conformance --json | jq`` is a supported thing to
    type, and a progress line in that stream is a parse error.
    """
    try:
        if not sys.stderr.isatty():
            return None
    except (AttributeError, ValueError):
        return None

    def cb(done: int, total: int, *_rest: Any) -> None:
        sys.stderr.write("\r%s %d/%d" % (label, done, total))
        sys.stderr.flush()
        if done >= total:
            sys.stderr.write("\r%s\r" % (" " * (len(label) + 16)))
            sys.stderr.flush()

    return cb


# --- run / route -------------------------------------------------------------


def cmd_run(ns: argparse.Namespace) -> int:
    program, tail, stdin = _read_program(ns)
    if ns.stdin and stdin is None:
        stdin = sys.stdin.read()
    d = engines.dispatch(program, argv_tail=tail, stdin=stdin, timeout=ns.timeout)
    if ns.verbose:
        chain = " -> ".join([a.engine for a in d.attempts] + [d.engine])
        sys.stderr.write("lypning: route %s (%s: %s), ran %s\n"
                         % (d.route.engine, d.route.kind, d.route.detail, chain))
    sys.stdout.write(d.result.stdout)
    sys.stdout.flush()
    sys.stderr.write(d.result.stderr)
    # Passed through verbatim, 90 included: the caller's fallback logic is the
    # reason the code exists, and re-mapping it here would break it.
    return d.result.returncode


def cmd_route(ns: argparse.Namespace) -> int:
    program, _tail, _stdin = _read_program(ns)
    r = engines.route(program)
    if ns.json:
        _json({"engine": r.engine, "kind": r.kind, "detail": r.detail})
    else:
        _out(str(r))
    return 0


# --- build -------------------------------------------------------------------


def cmd_build(ns: argparse.Namespace) -> int:
    build = _mod("build")
    rust, mp, lib = ns.rust, ns.micropython, ns.lib
    # `--stock` alone builds only the control: it is the benchmark's control,
    # not a tier, and asking for it must not silently rebuild the two engines.
    # `--lib` is the same shape of thing — an artefact of the Rust core rather
    # than a tier — so asking for it alone must not rebuild the engines either.
    if ns.all or not (rust or mp or ns.stock or lib):
        rust = mp = True
    if ns.all:
        lib = True
    results = build.build_all(rust=rust, micropython=mp, target=ns.target,
                              jobs=ns.jobs, verbose=ns.verbose, dry_run=ns.dry_run,
                              stock=ns.stock, lib=lib,
                              # Only when the caller named one. `--target` defaults
                              # to musl for the binary, which is the one thing a
                              # loadable shared object must not be on a glibc host.
                              lib_target=ns.target if "--target" in sys.argv else "")

    if ns.json:
        _json({"results": [_plain(r) for r in results],
               "dry_run": bool(ns.dry_run),
               "installed": []})
        return 0 if ns.dry_run or all(r.ok for r in results) else 1
    _out(build.report(results, verbose=ns.verbose))
    if ns.dry_run:
        return 0
    installed = build.install_binaries(results)
    for r in results:
        # The library and its headers go to `lib/` and `include/`, not to the
        # directory the engine finders read — `find_lypning` offering a shared
        # object to `os.execv` is a hang with no output.
        if r.artifact == "lib":
            for p in build.install_library(r):
                _out("installed %s" % p)
    by_engine = {r.engine: r for r in results if not r.artifact}
    for p in installed:
        # Name the target. `--target host` is the dynamically linked control and
        # `--target i686` is the sandbox build, and both land under the same
        # name as the default musl build — so a control measured once is still
        # the engine every route uses afterwards unless the line said so.
        r = by_engine.get(Path(p).name)
        target = getattr(r, "target", None) if r else None
        _out("installed %s%s" % (p, "  (target: %s)" % target if target else ""))
    ok = all(r.ok for r in results)
    if ns.verify:
        # Verified even when a build failed, and the exit code is the AND of the
        # two: the tiers are independent, so a broken MicroPython build must not
        # cost the Rust core its battery — and must not let it report ok either.
        ok = _render_verify(build.verify(results)) and ok
    return 0 if ok else 1


def _render_verify(v: Any) -> bool:
    """Print the gate and the battery, each through its own module's renderer.

    Returns whether both held. Rendering here rather than in :mod:`build` keeps
    every report formatted by the module that owns its vocabulary — a gate is
    checks against budgets, a conformance run is verdicts against CPython, and
    a third renderer for the pair would be a third place for either to drift.
    """
    gate = _mod("gate")
    conformance = _mod("conformance")
    for note in v.notes:
        _out("note: %s" % note)
    for binary, report in v.gates:
        _out("")
        _out("--- gate: %s ---" % binary)
        _out(gate.render(report))
    if v.conformance is not None:
        _out("")
        _out("--- conformance ---")
        _out(conformance.render(v.conformance))
    _out("")
    _out("verify: %s in %.1f s" % ("ok" if v.ok else "FAILED", v.seconds))
    return bool(v.ok)


# --- lib ---------------------------------------------------------------------

_LIB_NOT_BUILT = ("the C ABI library is not built — run `lypning build --lib` "
                  "(or point $LYPNING_LIB at a built library)")


def _header_dir(embed: Any, lib: Optional[Path]) -> Path:
    """The include dir that describes THIS library, not merely one that exists.

    A header from one build and a library from another is the one way to get a
    segfault out of a frozen ABI, so the installed headers are offered only for
    the installed library — `lypning build --lib` copies the two out together,
    which is what makes them describe each other. A library still sitting in a
    cargo target dir is described by the asset tree it was compiled from,
    whatever an older install left behind in the state dir.
    """
    installed = embed.include_dir()
    if lib is not None and lib.parent == embed.lib_dir() and (installed / "lypning.h").is_file():
        return installed
    if (paths.INCLUDE_DIR / "lypning.h").is_file():
        return paths.INCLUDE_DIR
    return installed


def _lib_obj() -> Dict[str, Any]:
    """Everything `lib`, `status` and `doctor` need to say about the C ABI.

    The ABI number and the runtime version are the library's own answers, read
    by loading it: a build tree that grew a second library, or an install that
    is one build behind the headers beside it, is exactly what a constant
    remembered from this side would hide.
    """
    embed = _mod("embed")
    try:
        lib = embed.find_library()
    except embed.LibraryError as e:
        # A $LYPNING_LIB that names nothing is a mistake in the caller's
        # environment, not an absent artifact, and telling them to build
        # something they already built would never stop being wrong.
        lib = None
        return {"built": False, "path": None, "bytes": None, "static": None,
                "static_bytes": None, "include": None, "abi": None,
                "cli_abi": embed.ABI_VERSION, "runtime": None, "error": str(e),
                "cflags": None, "libs": None, "gcc": None}
    inc = _header_dir(embed, lib)
    header = (inc / "lypning.h").is_file() if inc else False
    obj: Dict[str, Any] = {
        "built": lib is not None,
        "path": str(lib) if lib else None,
        # A hole, not a zero. These rows are read by things that compile against
        # the library, and `0 B` reads as an empty library rather than an absent
        # one — the same reason an unbuilt arm is a hole in the bench table.
        "bytes": _size_of(lib) if lib else None,
        "static": None,
        "static_bytes": None,
        "include": str(inc),
        # Whether the directory above actually holds the contract. A header
        # and a library from different installs is the one way to get a segfault
        # out of a frozen ABI, and an absent header is the cheap half of that.
        "header": header,
        "abi": None,
        "cli_abi": embed.ABI_VERSION,
        "runtime": None,
        "error": None,
        "cflags": "-I%s" % inc,
        "libs": None,
        "gcc": None,
    }
    if lib is None:
        return obj
    static = lib.with_suffix(".a")
    if static.is_file():
        obj["static"] = str(static)
        obj["static_bytes"] = _size_of(static)
    d = lib.parent
    # -rpath, which pkg-config would not have emitted: nothing runs ldconfig
    # over ~/.lypning/lib, so a harness linked without it compiles and links
    # cleanly and then dies at exec with "cannot open shared object file".
    obj["libs"] = "-L%s -llypning -Wl,-rpath,%s" % (d, d)
    obj["gcc"] = "gcc %s prog.c -o prog %s" % (obj["cflags"], obj["libs"])
    try:
        loaded = embed.Library(lib)
    except embed.LibraryError as e:
        obj["error"] = str(e)
        return obj
    # The number the LIBRARY answered with, not the one this side remembers.
    # `Library` refuses to bind a library whose `lypning_abi_version()`
    # disagrees, so the two are equal whenever this line is reached — but a
    # field named `abi` that was never read out of the shared object is a fact
    # about our own source, and reporting it beside `cli_abi` would look like a
    # comparison that had been made.
    obj["abi"] = loaded.abi
    obj["runtime"] = loaded.version
    return obj


def _render_lib(info: Dict[str, Any]) -> str:
    """The summary. Every line is something a host has to type somewhere."""
    def row(name: str, value: str) -> str:
        return "  %-9s %s" % (name, value)

    # The title carries the verdict, because every row under it is a path and a
    # path is right whether or not the file at the end of it will load.
    lines = ["lypning C ABI — %s" % ("built" if not info["error"]
                                     else "present, but it does not load"), ""]
    lines.append(row("shared", "%s  (%s B)" % (info["path"], format(info["bytes"], ","))))
    lines.append(row("static", "%s  (%s B)" % (info["static"], format(info["static_bytes"] or 0, ","))
                     if info["static"] else "not built — it installs beside the shared object"))
    lines.append(row("headers", info["include"]))
    if info["error"]:
        lines.append(row("abi", "UNREADABLE: %s" % info["error"]))
    else:
        lines.append(row("abi", "%s  (this CLI speaks %s)" % (info["abi"], info["cli_abi"])))
        lines.append(row("runtime", info["runtime"]))
    lines += ["", "compile against it:", "  " + info["gcc"], "",
              "LYPNING_UNSUPPORTED is not an error: the run executed none of the program,",
              "so hand it to CPython. lypning_result_should_fall_onward() in %s"
              % (Path(info["include"]) / "lypning.h"), "already answers that question."]
    return "\n".join(lines)


def cmd_lib(ns: argparse.Namespace) -> int:
    info = _lib_obj()
    if not info["built"]:
        # Exit 2 and not one byte on stdout, for the same reason a missing core
        # is: `gcc $(lypning lib --cflags) …` must fail loudly rather than
        # compile against whatever header the include path already had.
        #
        # The reason matters when there is one. "Not built" tells a caller whose
        # $LYPNING_LIB names a file that does not exist to run a build they have
        # already run, and it will be wrong every time they try it.
        raise Usage(info.get("error") or _LIB_NOT_BUILT)
    asked = [key for key, on in (("cflags", ns.cflags), ("libs", ns.libs),
                                 ("path", ns.path), ("static", ns.static),
                                 ("include", ns.include)) if on]
    if asked and set(asked) & {"cflags", "include"} and not info.get("header"):
        # An `-I` at a directory with no `lypning.h` in it compiles to
        # "No such file or directory" one step later, with a message about the
        # user's own source file rather than about the install. Refuse here,
        # where the reason is still known.
        raise Usage("no lypning.h in %s — reinstall with `lypning build --lib`"
                    % (info.get("include") or "any include dir"))
    if asked:
        # The flag path is the one that gets substituted into a compiler, so it
        # is answered in full BEFORE a byte reaches stdout. Half a flag list is
        # worse than none: `gcc $(lypning lib --cflags --static)` that printed
        # the -I and then failed would compile — against the right header and
        # without the archive it was told to use — and only the exit code
        # nobody in a `$(…)` looked at would say so.
        if info["error"]:
            # A library the CLI could not load is one whose -L and -llypning
            # would link a host against an ABI nothing checked. The summary can
            # report that and still print the paths, because a person reads it;
            # these are consumed by a compiler, so here it is a refusal.
            raise Failure("lib: %s" % info["error"])
        values = []
        for key in asked:
            if info[key] is None:
                raise Usage("no %s: %s is not beside %s — `lypning build --lib` "
                            "installs both" % (key, Path(info["path"]).with_suffix(".a").name,
                                               Path(info["path"]).parent))
            values.append(str(info[key]))
        # One line each, in a fixed order rather than the order they were typed
        # — argparse does not keep that — so `$(lypning lib --cflags --libs)`
        # word-splits to the same thing every time.
        for value in values:
            _out(value)
        return 0
    if ns.json:
        _json(info)
    else:
        _out(_render_lib(info))
    # A library that is there and will not load is this command's own failure:
    # the paths above are still right, but an ABI it could not read is a number
    # it must not print.
    return 1 if info["error"] else 0


# --- status ------------------------------------------------------------------


def _status_obj() -> Dict[str, Any]:
    install = _mod("install")
    corpus = _mod("corpus")
    st = install.status()
    found = engines.available()
    st["version"] = __version__
    st["engines"] = {}
    for name, p in found.items():
        size = _size_of(p)
        st["engines"][name] = {
            "path": str(p) if p else None,
            "built": p is not None,
            "bytes": size,
            # 131072-byte device blocks: on the sandbox this project targets,
            # that is what a cold start actually costs (docs/LYPNING.md §8).
            "blocks": (size + 131071) // 131072 if size else 0,
        }
    # Listed apart from the engines, not as a fourth one: it is an artefact of
    # the same core, and anything walking `engines` hands its paths to execv.
    try:
        st["library"] = _lib_obj()
    except Failure as e:
        # The same keys :func:`_lib_obj` would have returned, so one absent
        # library has one shape in `status --json` whether the library is
        # missing or the module that reads it is.
        st["library"] = {"built": False, "path": None, "bytes": None,
                         "static": None, "static_bytes": None, "include": None,
                         "abi": None, "cli_abi": None, "runtime": None,
                         "error": str(e), "cflags": None, "libs": None, "gcc": None}
    try:
        problems: List[str] = []
        entries = corpus.load_default(problems)
        st["corpus"] = {"path": str(paths.CORPUS_FILE), "entries": len(entries),
                        "seed": str(paths.SEED_CORPUS_FILE),
                        "problems": problems}
    except Exception as e:
        st["corpus"] = {"path": str(paths.CORPUS_FILE), "entries": None,
                        "problems": [],
                        "error": "%s: %s" % (type(e).__name__, e)}
    st["state_dir"] = str(paths.state_dir())
    st["cli"] = shutil.which(PROG)
    return st


def _render_status(st: Dict[str, Any]) -> str:
    lines = ["lypning %s" % st.get("version", __version__), ""]
    lines.append("engines")
    for name in ("lypning", "lypning-mp", "cpython"):
        e = st["engines"].get(name) or {}
        if not e.get("built"):
            hint = {"lypning": "  — `lypning build --rust`",
                    "lypning-mp": "  — `lypning build --micropython` (needs a network)",
                    "cpython": ""}.get(name, "")
            lines.append("  %-11s not built%s" % (name + ":", hint))
            continue
        size = e.get("bytes") or 0
        detail = "  (%s B, %d blocks)" % (format(size, ","), e.get("blocks") or 0) if size else ""
        lines.append("  %-11s %s%s" % (name + ":", e["path"], detail))

    lib = st.get("library") or {}
    lines += ["", "library"]
    if not lib.get("built"):
        lines.append("  %-11s not built  — `lypning build --lib`" % "liblypning:")
    else:
        abi = ("ABI %s" % lib["abi"] if lib.get("abi") is not None
               else "UNREADABLE: %s" % lib.get("error", ""))
        lines.append("  %-11s %s  (%s B, %s)"
                     % ("liblypning:", lib["path"], format(lib.get("bytes") or 0, ","), abi))
    lines.append("  %-11s %s" % ("headers:", lib.get("include") or "not installed"))

    c = st.get("corpus") or {}
    n = c.get("entries")
    lines += ["", "corpus       %s  (%s)" % (
        "%d programs" % n if n is not None else "UNREADABLE: " + str(c.get("error", "")),
        c.get("path", ""))]
    # A file that loaded as zero programs still prints a number. Say which file
    # was skipped, or the count above is quietly wrong.
    for problem in c.get("problems") or []:
        lines.append("             WARNING: %s" % problem)

    sh = st.get("shim") or {}
    lines += ["", "shim — %s" % sh.get("bin_dir", "")]
    for s in sh.get("states", []):
        lines.append("  %-8s %s%s" % (s["name"] + ":", s["state"],
                                      "  [backup: %s]" % Path(s["backup"]).name if s.get("backup") else ""))
    problem = sh.get("path_problem")
    lines.append("  PATH   : %s" % (problem if problem else "ok — the shim is what python3 resolves to"))

    lines.append("")
    for scope in ("project", "user"):
        sc = (st.get("scopes") or {}).get(scope) or {}
        hooks = sc.get("hooks") or {}
        wired = ", ".join(sorted(hooks)) if hooks else "no lypning hooks"
        lines.append("%s scope — %s" % (scope, sc.get("claude_dir", "")))
        lines.append("  hooks    : %s" % wired)
        lines.append("  skill    : %s" % (sc.get("skill") or "not installed"))
        if sc.get("settings_error"):
            lines.append("  settings : %s — %s" % (sc.get("settings"), sc["settings_error"]))

    log = st.get("log") or {}
    if log.get("error"):
        lines += ["", "log          WARNING: %s" % log["error"]]
    else:
        lines += ["", "log          %s%s" % (
            log.get("path", ""),
            " (%s B)" % format(log.get("bytes", 0), ",") if log.get("exists")
            else " (not created yet)")]
    lines.append("state dir    %s" % st.get("state_dir", ""))
    return "\n".join(lines)


def cmd_status(ns: argparse.Namespace) -> int:
    st = _status_obj()
    if ns.json:
        _json(st)
    else:
        _out(_render_status(st))
    return 0


# --- doctor ------------------------------------------------------------------

#: NOTE is the fourth level and it is neither good news nor bad: an optional
#: part that is absent. A WARN there would train a reader to ignore WARNs, and
#: the parts that are optional here — the MicroPython tier, the C ABI — are
#: absent on most machines by design, not by neglect.
OK, WARN, FAIL, NOTE = "OK", "WARN", "FAIL", "NOTE"


def _pip_scripts_dirs() -> List[Path]:
    """Where the console script named ``lypning`` plausibly lives."""
    out: List[Path] = []
    argv0 = Path(sys.argv[0] if sys.argv and sys.argv[0] else "")
    if argv0.name == PROG:
        try:
            out.append(argv0.resolve().parent)
        except OSError:
            pass
    try:
        import sysconfig
        for key in ("scripts", "purelib"):
            v = sysconfig.get_path(key)
            if key == "scripts" and v:
                out.append(Path(v))
    except Exception:
        pass
    out.append(Path(sys.prefix) / "bin")
    seen: Dict[str, Path] = {}
    for p in out:
        seen.setdefault(str(p), p)
    return list(seen.values())


def _is_elf(p: Path) -> bool:
    try:
        with open(str(p), "rb") as fh:
            return fh.read(4) == b"\x7fELF"
    except OSError:
        return False


def _check_cli_collision() -> Tuple[str, str, str]:
    """The one genuine name collision in this design.

    The pip console script is called ``lypning`` and so is the Rust binary, and
    :func:`paths.bin_dir` is a directory users are told to put on ``$PATH`` so
    the shim resolves. If it lands ahead of the scripts directory, ``lypning
    status`` reaches the interpreter, which has never heard of a subcommand and
    answers in the vocabulary of an interpreter: it looks for a file called
    ``status``, and refuses ``--json`` as an unsupported CLI option.

    ``$PATH`` order is not consulted — the resolution is. Whatever ``which``
    returns is what a user typing ``lypning`` gets, and its first four bytes say
    which of the two it is.
    """
    which = shutil.which(PROG)
    bd = paths.bin_dir()
    if which is None:
        scripts = _pip_scripts_dirs()
        return (WARN, "lypning on PATH",
                "not on PATH — pip put the console script in %s; add it to $PATH"
                % (scripts[0] if scripts else "the scripts directory"))
    if _is_elf(Path(which)):
        return (FAIL, "lypning on PATH",
                "%s is the Rust core, not this CLI — `lypning status` reaches the "
                "interpreter, which looks for a file called `status` and refuses "
                "`--json` as an unsupported CLI option. Fix: put the pip scripts "
                "directory ahead of %s on $PATH; the shim only ever needs "
                "`python`/`python3` from there, never `lypning`." % (which, bd))
    note = ""
    if (bd / PROG).exists():
        note = "  (a binary of the same name sits in %s, behind it)" % bd
    return (OK, "lypning on PATH", which + note)


def _doctor_checks() -> List[Tuple[str, str, str]]:
    checks: List[Tuple[str, str, str]] = []

    build = None
    try:
        build = _mod("build")
    except Failure as e:
        checks.append((WARN, "build module", str(e)))

    # 1. toolchain
    if build is not None:
        tc = build.toolchain()
        need = ("cargo", "rustc", "cc", "make")
        missing = [t for t in need if not tc.get(t)]
        checks.append((OK if not missing else WARN, "toolchain",
                       "cargo/rustc/cc/make present" if not missing
                       else "missing: %s — `lypning build` cannot rebuild the core" % ", ".join(missing)))
        checks.append((OK if tc.get("strace") else WARN, "strace",
                       tc.get("strace") or "absent — `lypning gate` cannot count file opens, "
                                           "and reports them unmeasured rather than zero"))

    # 2. engines
    found = engines.available()
    core = found.get(engines.LYPNING)
    checks.append((OK if core else FAIL, "lypning core",
                   "%s (%s B)" % (core, format(_size_of(core), ",")) if core
                   else "not built — run `lypning build --rust`"))
    mp = found.get(engines.MICROPYTHON)
    checks.append((OK if mp else WARN, "lypning-mp",
                   "%s (%s B)" % (mp, format(_size_of(mp), ",")) if mp
                   else "not built — `lypning build --micropython` needs a network; "
                        "everything routes past this tier meanwhile"))
    py = found.get(engines.CPYTHON)
    checks.append((OK if py else FAIL, "cpython",
                   "%s" % py if py else "no real CPython found — the last tier is missing"))

    # 3. the refusal contract, on the binary that is actually installed
    if core is not None and build is not None:
        ok, why = build.check_refusal_contract(core)
        checks.append((OK if ok else FAIL, "refusal contract",
                       "exit 90, one line on stderr, clean stdout" if ok else why))
    if mp is not None:
        res = engines.run(engines.MICROPYTHON, "import subprocess", binary=mp, timeout=30.0)
        ok = res.returncode == UNSUPPORTED_EXIT and res.stdout == ""
        checks.append((OK if ok else FAIL, "lypning-mp refusal",
                       "exit 90, clean stdout" if ok else
                       "exit %d, stdout %r" % (res.returncode, res.stdout[:80])))

    # 3b. the same contract, through the ABI instead of through a process. A
    #     shared object has no exit code, so the three pinned properties are
    #     re-asserted in library terms — plus the fourth only embedding needs:
    #     the refusal must ask to be routed onward, because in-process it is the
    #     HOST that owns the retry, and a refusal nobody acts on is a program
    #     that never ran. FAIL when it is broken: that is the silent kind of
    #     wrong answer, arriving inside somebody else's process.
    try:
        embed = _mod("embed")
    except Failure as e:
        checks.append((WARN, "embed module", str(e)))
        embed = None
    if embed is not None:
        bad_override = ""
        try:
            lib = embed.find_library()
        except embed.LibraryError as e:
            # A named path that is not there is the CALLER's mistake, and it is
            # a FAIL rather than a NOTE: "optional and absent" is fine, "you
            # told me where it is and you were wrong" is not.
            lib, bad_override = None, str(e)
        if bad_override:
            checks.append((FAIL, "library refusal", bad_override))
        elif lib is None:
            checks.append((NOTE, "library refusal",
                           "liblypning is not built — `lypning build --lib`; the C ABI is "
                           "optional and nothing routes through it until a host links it"))
        else:
            ok, why = embed.check_refusal_contract(lib)
            checks.append((OK if ok else FAIL, "library refusal",
                           "%s — unsupported status, clean stdout, one line, falls onward" % lib
                           if ok else why if str(lib) in why else "%s: %s" % (lib, why)))

    # 4. the collision
    checks.append(_check_cli_collision())

    # 5. capture wiring — informational, because a checkout that only runs the
    #    interpreter never wants it.
    try:
        install = _mod("install")
        st = install.status()
        wired = [s for s in ("project", "user")
                 if (st.get("scopes", {}).get(s, {}).get("hooks"))]
        checks.append((OK if wired else WARN, "capture hooks",
                       "wired in %s scope" % ", ".join(wired) if wired
                       else "not wired — `lypning install` adds the hooks and the skill"))
        # A settings.json that does not parse is why the hooks are not wired,
        # and it is a different fix from "run lypning install".
        for scope in ("project", "user"):
            sc = (st.get("scopes") or {}).get(scope) or {}
            if sc.get("settings_error"):
                checks.append((WARN, "%s settings" % scope,
                               "%s — %s; lypning will not touch it until it parses"
                               % (sc.get("settings"), sc["settings_error"])))
        problem = (st.get("shim") or {}).get("path_problem")
        states = {s["name"]: s["state"] for s in (st.get("shim") or {}).get("states", [])}
        if any(v == "foreign" for v in states.values()):
            checks.append((WARN, "shim", "a foreign file occupies a shim path — "
                                         "`lypning shim status` shows which"))
        elif not any(v in ("current", "stale") for v in states.values()):
            checks.append((WARN, "shim", "not installed — `lypning shim install`"))
        elif problem:
            checks.append((WARN, "shim", problem))
        else:
            checks.append((OK, "shim", "installed and first on PATH"))
        log = st.get("log") or {}
        if log.get("error"):
            checks.append((FAIL, "log", log["error"]))
        else:
            checks.append((OK, "log", "%s%s" % (log.get("path"), "" if log.get("exists")
                                                else " (not created yet)")))
    except Failure as e:
        checks.append((WARN, "install module", str(e)))

    # 6. the corpus
    try:
        corpus = _mod("corpus")
        problems: List[str] = []
        n = len(corpus.load_default(problems))
        if problems:
            # Not a WARN: the corpus is the argument this whole project makes,
            # and a count taken over a file that did not parse is a wrong number
            # reported as a right one.
            for problem in problems:
                checks.append((FAIL, "corpus", problem))
        else:
            checks.append((OK if n else WARN, "corpus",
                           "%d programs from %s" % (n, paths.CORPUS_FILE)))
    except Failure as e:
        checks.append((WARN, "corpus", str(e)))

    return checks


def cmd_doctor(ns: argparse.Namespace) -> int:
    checks = _doctor_checks()
    if ns.json:
        _json({"checks": [{"level": l, "name": n, "detail": d} for l, n, d in checks],
               "ok": not any(l == FAIL for l, _, _ in checks)})
    else:
        width = max(len(n) for _, n, _ in checks)
        _out("\n".join("%-4s %s  %s" % (level, name.ljust(width), detail)
                       for level, name, detail in checks))
        bad = sum(1 for l, _, _ in checks if l == FAIL)
        warn = sum(1 for l, _, _ in checks if l == WARN)
        _out("\n%d check(s), %d FAIL, %d WARN" % (len(checks), bad, warn))
    return 1 if any(l == FAIL for l, _, _ in checks) else 0


# --- install / uninstall / shim ----------------------------------------------


def cmd_install(ns: argparse.Namespace) -> int:
    install = _mod("install")
    scope = "user" if ns.user else "project"
    plan = install.plan_install(ns.project, scope=scope, shim=not ns.no_shim,
                                hooks=not ns.no_hooks, skill=not ns.no_skill)
    if ns.dry_run:
        if ns.json:
            _json(plan.as_dict())
        else:
            _out(install.render_plan(plan))
        return 0
    actions = install.apply(plan, force=ns.force)
    failed = [a for a in actions if a.kind == "skip" and a.note.startswith("FAILED")]
    if ns.json:
        _json({"project": str(plan.project), "scope": scope,
               "actions": [a.as_dict() for a in actions], "ok": not failed})
    else:
        _out(install.render_actions(actions))
    return 1 if failed else 0


def _uninstall_preview(install, project: Optional[str], scope: str) -> List[str]:
    """What :func:`install.uninstall` would remove, computed by looking.

    There is no plan object for the removal side, and inventing one in this
    file would put a second definition of "what we installed" next to the real
    one. This lists what is *there* instead, which is the same question asked
    of the disk rather than of a model of it.
    """
    root = install.claude_dir(project, scope)
    out: List[str] = []
    skill = root / "skills" / "lypning"
    if skill.is_dir():
        out.append("- %s  (skill)" % skill)
    hooks = root / "hooks"
    if hooks.is_dir():
        for p in sorted(hooks.glob("*.sh")):
            if p.name.startswith("lypning"):
                out.append("- %s  (hook script)" % p)
    st = install.status(project)
    present = (st.get("scopes", {}).get(scope, {}) or {}).get("hooks") or {}
    for event, cmds in sorted(present.items()):
        out.append("- %s  (%d hook entr%s under %s)"
                   % (root / "settings.json", len(cmds), "y" if len(cmds) == 1 else "ies", event))
    shim = _mod("shim")
    for s in shim.status():
        if s.installed:
            out.append("- %s  (shim)" % s.path)
        if s.backup:
            out.append("+ %s  (restored from %s)" % (s.path, s.backup.name))
    return out or ["nothing installed for this scope"]


def cmd_uninstall(ns: argparse.Namespace) -> int:
    install = _mod("install")
    scope = "user" if ns.user else "project"
    if ns.dry_run:
        lines = _uninstall_preview(install, ns.project, scope)
        if ns.json:
            _json({"scope": scope, "would_remove": lines})
        else:
            _out("\n".join(lines))
        return 0
    actions = install.uninstall(ns.project, scope=scope)
    if ns.json:
        _json({"scope": scope, "actions": [a.as_dict() for a in actions]})
    else:
        _out(install.render_actions(actions))
    return 0


def cmd_shim(ns: argparse.Namespace) -> int:
    shim = _mod("shim")
    if ns.action == "status":
        if ns.json:
            _json({"states": [s.as_dict() for s in shim.status(ns.bin_dir)],
                   "path_problem": shim.path_problem(ns.bin_dir)})
        else:
            _out(shim.render(shim.status(ns.bin_dir)))
        return 0
    try:
        lines = (shim.install(ns.bin_dir, force=ns.force) if ns.action == "install"
                 else shim.uninstall(ns.bin_dir))
    except shim.ShimError as e:
        # The refusal is the message; what it managed first is the context.
        for line in getattr(e, "lines", []) or []:
            _out(line)
        raise Failure(str(e))
    if ns.json:
        _json({"action": ns.action, "lines": lines})
    else:
        _out("\n".join(lines) if lines else "nothing to do")
    return 0


def cmd_hook(ns: argparse.Namespace) -> int:
    """Hook entry points. Stdout carries a protocol response — nothing else."""
    capture = _mod("capture")
    if ns.event == "pre-tool-use":
        return int(capture.hook_pre_tool_use())
    return int(capture.hook_stop())


# --- conformance -------------------------------------------------------------


def cmd_conformance(ns: argparse.Namespace) -> int:
    conf = _mod("conformance")
    routing = _mod("routing")
    arms = list(ns.engine) if ns.engine else None
    report = conf.run(engines=arms, limit=ns.limit, timeout=ns.timeout,
                      progress=_progress("conformance"))
    # Two gates over one battery, and they fail for different reasons: an engine
    # that disagrees with CPython, and a classifier that sends a program to one
    # that does. Both are correctness; WASTED and LATE are cost and gate nothing.
    routed = routing.grade(report)
    if ns.json:
        obj: Dict[str, Any] = {
            "total": report.total,
            "seconds": report.seconds,
            "reference": report.reference,
            "unbuilt": report.unbuilt,
            "damage": report.damage,
            "ok": report.ok,
            "mismatches": report.mismatches,
            "skipped": [_plain(s) for s in report.skipped],
            "routing_errors": [_plain(r) for r in report.routing_errors],
            "routing": {
                "counts": routed.counts,
                "predictions": routed.predictions,
                "graded": routed.graded,
                "ungraded": routed.ungraded,
                "ungraded_total": routed.ungraded_total,
                "scored": routed.scored,
                "ideal_pct": routed.ideal_pct,
                "first_try_pct": routed.first_try_pct,
                "note": routed.note,
                "ok": routed.ok,
                # Only the fatal grade is enumerated: UNSAFE is a bug report and
                # needs its programs named, while 200 LATE ids are a histogram
                # nobody reads out of a JSON blob.
                "unsafe": [_plain(s) for s in routed.unsafe()],
            },
            "engines": {},
        }
        for name, er in report.engines.items():
            obj["engines"][name] = {
                "match": er.match, "unsupported": er.unsupported,
                "mismatch": er.mismatch, "total": er.total, "coverage": er.coverage,
                "failures": [_plain(v) for v in er.failures()],
            }
        if ns.plan:
            obj["plan"] = [{"feature": f, "blocks": n, "examples": ids}
                           for f, n, ids in conf.plan(report)]
        _json(obj)
    else:
        _out(conf.render(report, plan=ns.plan))
        if not ns.plan:
            # `--plan` is a build order and nothing else; a routing table under
            # it would be answering a question the caller did not ask.
            _out("\n" + routing.render(routed))
    return 0 if report.ok and routed.ok else 1


# --- fuzz --------------------------------------------------------------------


def cmd_fuzz(ns: argparse.Namespace) -> int:
    fuzz = _mod("fuzz")
    report = fuzz.run(iterations=ns.iterations, seed=ns.seed, engine=ns.engine,
                      timeout=ns.timeout, progress=_progress("fuzz"))
    if report.not_run:
        raise Usage(report.not_run)
    if report.unbuilt:
        # Exit 2, like everywhere else the core is missing: nothing ran, and the
        # fix is a command rather than a change to anything measured.
        raise Usage("%s or the reference CPython is not built — `lypning build --rust`"
                    % ns.engine)
    if ns.json:
        _json({
            "seed": report.seed, "iterations": report.iterations, "engine": report.engine,
            "binary": report.binary, "reference": report.reference,
            "ran": report.ran, "agreed": report.agreed, "refused": report.refused,
            "seconds": report.seconds, "ok": report.ok,
            "refusal_kinds": report.refusal_kinds,
            "counterexamples": [_plain(c) for c in report.counterexamples],
        })
    else:
        _out(fuzz.render(report))
    return 0 if report.ok else 1


# --- bench -------------------------------------------------------------------


def _render_startup(report: Any, base: str = "cpython") -> str:
    """The startup half on its own.

    :func:`bench.render` always prints both halves, and on a startup-only run
    its corpus half reads "no arm was available to measure" — true of a
    measurement nobody asked for, and misleading. So this one case is rendered
    here instead of being trimmed out of that string.
    """
    bench = _mod("bench")
    lines = []
    host = report.host or {}
    lines.append("host: %s cpus, %s (%s)" % (host.get("cpu_count", "?"),
                                             host.get("kernel", "?"), host.get("machine", "?")))
    lines.append("")
    lines.append("startup — `-c 'pass'`, min of %d, arms interleaved" % report.startup_repeat)
    lines.append("")
    if not report.startup:
        lines.append("no arm was available to measure. Build one: `lypning build`.")
        return "\n".join(lines)
    ref = report.startup.get(base)
    lines.append("%-12s %10s   %s" % ("arm", "min ms", "vs " + base))

    # Every arm gets a row, measured or not. An arm that silently vanishes from
    # the table reads as an arm that was never part of the comparison, which is
    # the same lie as a zero — see `bench.render`, which does this for the
    # corpus half. "Not built" and "built but silent" are different failures.
    eng = host.get("engines") or {}
    built = {n for n, i in eng.items() if isinstance(i, dict)} if isinstance(eng, dict) else set()
    if bench.engines.LYPNING in built:
        built.add(bench.MIXTURE)
    order = [a for a in bench.ARM_ORDER]
    for extra in report.startup:
        if extra not in order:
            order.append(extra)
    measured = sorted((n for n in order if report.startup.get(n) is not None),
                      key=lambda n: report.startup[n])
    for name in measured + [n for n in order if report.startup.get(n) is None]:
        ms = report.startup.get(name)
        if ms is None:
            why = "(did not run `pass`)" if name in built else "(not built)"
            lines.append("%-12s %10s   %s" % (name, "—", why))
        else:
            rel = "%.3fx" % (ms / ref) if ref else "—"
            lines.append("%-12s %10.2f   %s" % (name, ms, rel))
    return "\n".join(lines)


def cmd_bench(ns: argparse.Namespace) -> int:
    bench = _mod("bench")
    if ns.micropython:
        return _bench_micropython(bench, ns)
    if ns.record or ns.note:
        # Said rather than ignored: a caller who typed `--record` and got a
        # table and no file would have to diff the ledger to find out.
        raise Usage("--record and --note belong to --micropython, which is the run the "
                    "ledger is a history of")
    arms = list(ns.arm) if ns.arm else None
    both = not (ns.startup or ns.corpus)
    if both:
        report = bench.bench(startup_repeat=ns.startup_repeat, repeat=ns.repeat,
                             limit=ns.limit, arms=arms, timeout=ns.timeout,
                             progress=_progress("bench"))
    elif ns.corpus:
        report = bench.corpus_time(repeat=ns.repeat, limit=ns.limit, arms=arms,
                                   timeout=ns.timeout, progress=_progress("bench"))
    else:
        report = bench.BenchReport(host=bench.host_info(bench.resolve_arms(arms)))
        report.startup = bench.startup(ns.startup_repeat, arms)
        report.startup_repeat = max(1, int(ns.startup_repeat))
    if ns.json:
        _json(_plain(report))
    elif ns.startup and not both:
        _out(_render_startup(report))
    else:
        _out(bench.render(report))
    return 0


def _bench_micropython(bench: Any, ns: argparse.Namespace) -> int:
    """lypning-mp against the benchmark control, and the ledger entry for it."""
    report = bench.micropython(repeat=ns.repeat, startup_repeat=ns.startup_repeat,
                               limit=ns.limit, timeout=ns.timeout, note=ns.note or "",
                               progress=_progress("bench"))
    if report.missing:
        # Exit 2, not 1: nothing was measured and the fix is a command, which is
        # what this CLI reserves 2 for.
        raise Usage("; ".join(report.missing))
    if ns.json:
        _json(dict(_plain(report), ledger=bench.ledger_entry(report)))
    else:
        _out(bench.render_micropython(report))
    if ns.record:
        # The ledger is append-only and newest-first, so the entry is INSERTED
        # at its marker. record_ledger refuses a file without one rather than
        # appending an entry that would read as the oldest in the file.
        try:
            written = bench.record_ledger(ns.record, bench.ledger_entry(report))
        except ValueError as e:
            raise Failure(str(e))
        if not ns.json:
            _out("recorded in %s" % written)
    return 0


# --- corpus-time ---------------------------------------------------------------


def cmd_corpus_time(ns: argparse.Namespace) -> int:
    bench = _mod("bench")
    baseline = None
    # Read BEFORE the run: a baseline that does not parse should cost a second,
    # not the ten minutes of measurement whose comparison it was going to be.
    if ns.baseline:
        try:
            baseline = bench.load_record(ns.baseline)
        except ValueError as e:
            raise Failure(str(e))
    # Same reasoning for the destination: an unwritable --record discovered
    # AFTER the corpus has been timed throws the measurement away.
    if ns.record:
        try:
            bench.check_record_target(ns.record)
        except ValueError as e:
            raise Failure(str(e))
    timing = bench.corpus_time_one(ns.engine, repeat=ns.repeat, limit=ns.limit,
                                   timeout=ns.timeout, progress=_progress("corpus-time"))
    if timing is None:
        raise Usage("%s is not built and is not a path to a binary — `lypning build`, or "
                    "name one with --engine PATH" % ns.engine)
    diff = None
    if baseline is not None:
        diff = bench.diff_record(baseline, timing, top=ns.top)
        diff.baseline_path = str(ns.baseline)
    if ns.record:
        try:
            written = bench.write_record(timing, ns.record)
        except ValueError as e:
            raise Failure(str(e))
        if not ns.json:
            _out("recorded in %s" % written)
    if ns.json:
        payload = bench.timing_record(timing)
        if diff is not None:
            payload["diff"] = _plain(diff)
        _json(payload)
    else:
        _out(bench.render_corpus_time(timing, diff))
    return 0


# --- perf --------------------------------------------------------------------


def cmd_perf(ns: argparse.Namespace) -> int:
    perf = _mod("perf")
    # Both files are checked BEFORE the suite runs, for the reason corpus-time
    # checks them first: a baseline that does not parse, or a --record that
    # cannot be written, should cost a millisecond rather than the whole run.
    baseline = None
    if ns.baseline:
        try:
            baseline = perf.load_record(ns.baseline)
        except ValueError as e:
            raise Failure(str(e))
    if ns.record:
        try:
            perf.check_record_target(ns.record)
        except ValueError as e:
            raise Failure(str(e))
    names = set(c.name for c in perf.SUITE)
    if ns.only:
        unknown = [n for n in ns.only if n not in names]
        if unknown:
            raise Usage("no such case: %s (try `lypning perf --list`)" % ", ".join(unknown))
    if ns.list:
        _out("\n".join("%-18s %-9s %s" % (c.name, c.group, c.program.splitlines()[0][:48])
                        for c in perf.SUITE))
        return 0
    report = perf.run(arms=list(ns.arm) if ns.arm else None, repeat=ns.repeat,
                      only=ns.only or None, timeout=ns.timeout,
                      progress=_progress("perf"))
    d = perf.diff(baseline, report, arm=ns.diff_arm) if baseline is not None else None
    if d is not None:
        d.baseline_path = str(ns.baseline)
    if ns.record:
        try:
            written = perf.write_record(report, ns.record)
        except ValueError as e:
            raise Failure(str(e))
        if not ns.json:
            _out("recorded in %s" % written)
    if ns.json:
        payload = perf.record(report)
        if d is not None:
            payload["diff"] = _plain(d)
        _json(payload)
    else:
        _out(perf.render(report, top=ns.top))
        if d is not None:
            _out(perf.render_diff(d))
    # A case the arms disagreed about, refused, or failed is a bug in the tree,
    # not a slow row — invariant 1 on the suite's own terms. It exits 1 for the
    # same reason a MISMATCH does.
    return 0 if report.ok else 1


# --- gate --------------------------------------------------------------------


def cmd_gate(ns: argparse.Namespace) -> int:
    gate = _mod("gate")
    # Checked here rather than in gate(): with nothing named it substitutes one
    # engine for another and says so, which is right for a default and wrong
    # for a path the caller typed.
    if ns.binary and not Path(ns.binary).is_file():
        raise Usage("no such binary: %s" % ns.binary)
    report = gate.gate(ns.binary, compare=ns.compare)
    if ns.json:
        _json({"binary": report.binary, "ok": report.ok,
               "checks": [_plain(c) for c in report.checks],
               "baseline": report.baseline})
    else:
        _out(gate.render(report))
    return 0 if report.ok else 1


# --- harvest -----------------------------------------------------------------


def cmd_harvest(ns: argparse.Namespace) -> int:
    harvest = _mod("harvest")
    if ns.dry_run:
        sightings = harvest.collect(transcripts=ns.transcripts)
        if ns.json:
            _json({"mode": "dry-run", "sightings": len(sightings),
                   "corpus": str(paths.corpus_write_file())})
        elif not ns.quiet:
            _out("harvest: %d sighting(s) collected; nothing written (--dry-run).\n"
                 "         corpus would be %s" % (len(sightings), paths.corpus_write_file()))
        return 0

    # `_export` rather than `export_sightings`: the two do the same work and
    # only the first hands back the record `harvest.render` reports from —
    # including WHY a sighting was dropped, which is the whole content of a run
    # that publishes nothing.
    result = harvest._export()
    counts = None
    if not ns.export:
        counts = harvest.fold_into_corpus(harvest.collect(transcripts=ns.transcripts))
    if ns.json:
        obj = result.to_obj()
        obj["mode"] = "export" if ns.export else "fold"
        obj["corpus"] = ({"path": str(paths.corpus_write_file()), "added": counts[0], "total": counts[1]}
                         if counts else None)
        _json(obj)
    elif not ns.quiet:
        _out(harvest.render(result, corpus_counts=counts))
    return 0


# --- corpus ------------------------------------------------------------------


def cmd_corpus(ns: argparse.Namespace) -> int:
    corpus = _mod("corpus")
    problems: List[str] = []
    entries = corpus.load_default(problems)
    # To stderr, not stdout: `lypning corpus --json | jq` is a supported thing
    # to type. Loud enough to notice, out of the way of the data.
    for problem in problems:
        sys.stderr.write("lypning: corpus: %s\n" % problem)
    if ns.stats:
        s = corpus.stats(entries, top=ns.top)
        _json(_plain(s)) if ns.json else _out(corpus.render_stats(s))
        return 0
    if ns.json:
        # The records, in the on-disk normal form — `--list` in a shape a
        # machine can read, which is the only thing --json can usefully mean
        # here that --stats does not already cover.
        _json([e.to_obj() for e in entries])
        return 0
    if ns.list:
        width = max((len(e.id) for e in entries), default=16)
        for e in entries:
            head = (e.program.splitlines() or [""])[0]
            _out("%-*s %-11s %4d  %s" % (width, e.id, e.source, e.lines, head[:100]))
        return 0
    _out(corpus.render_stats(corpus.stats(entries, top=ns.top)))
    return 0


# --- argument parsing --------------------------------------------------------


def _sub(subparsers, name: str, help_: str, description: str, epilog: str = "") -> argparse.ArgumentParser:
    return subparsers.add_parser(
        name, help=help_, description=description.strip(), epilog=epilog.strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=PROG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
lypning — a mixture of Pythons.

Three interpreters, cheapest first, and a classifier that picks one per
program: a Python subset in Rust, a MicroPython variant with a frozen shim
stdlib, and the real CPython for everything the first two refuse.

Used as an interpreter it IS one — `lypning -c PROG`, `lypning FILE` and
`lypning -` exec straight into the Rust core, so anything that calls python3
can call this instead. The subcommands below are the tooling around that.
""",
        epilog="""
examples:
  lypning -c 'print(2**8)'            run it, as python would
  lypning run -c 'import re; ...'     route it, then fall through on a refusal
  lypning route -c 'import os'        say which tier would take it, and why
  lypning status                      what is built, wired and captured
  lypning doctor                      the same, but opinionated

Exit codes: 0 ok, 1 the command failed, 2 usage, 90 an engine refusal passed
through untouched. LYPNING_DEBUG=1 turns our one-line errors back into
tracebacks.
""",
    )
    p.add_argument("-V", "--version", action="version", version="%s %s" % (PROG, __version__))
    subs = p.add_subparsers(dest="cmd", metavar="COMMAND")

    # run
    s = _sub(subs, "run", "route a program, then dispatch through the chain", """
Route the program, run it on the tier the router picked, and fall through to
the next tier on exit 90 — and only on exit 90. Any other non-zero exit is the
program's own and is returned unchanged, because a dispatcher that retried on
exit 1 would run a half-completed program twice.

Output is buffered, not streamed: this command collects each tier's stdout and
stderr so it can tell a refusal from a result. If you want an interpreter's
native stdio, do not use `run` — `lypning -c PROG` execs and leaves nothing of
us in the process.
""", """
examples:
  lypning run -c 'print(1+1)'
  lypning run -c 'import sys; print(sys.argv[1:])' a 'b c'
  lypning run script.py --flag
  echo 1 | lypning run --stdin -c 'import sys; print(sys.stdin.read())'
""")
    s.add_argument("-c", dest="command", metavar="PROG", help="program passed as a string")
    s.add_argument("--stdin", action="store_true",
                   help="read stdin and hand it to the program (off by default: this "
                        "command would otherwise block waiting for a pipe nobody wrote to)")
    s.add_argument("--timeout", type=float, default=30.0, metavar="S", help="per-tier timeout (default 30)")
    s.add_argument("-v", "--verbose", action="store_true", help="print the route and the chain to stderr")
    s.add_argument("argv", nargs=argparse.REMAINDER, metavar="FILE|- [args...]")
    s.set_defaults(func=cmd_run)

    # route
    s = _sub(subs, "route", "print the tier a program would run on, and why", """
Prints `<engine>\\t<kind>: <detail>` — the tier, and the exact construct that
stopped the cheaper one. This is the Rust core's own parser answering, not a
heuristic over the text: it costs one parse and no execution, and it names the
feature rather than guessing at it.

With no core built, everything routes to cpython and says so.
""", """
examples:
  lypning route -c 'print(1)'        -> lypning
  lypning route -c 'import re'       -> lypning-mp  module: import re
  lypning route script.py
""")
    s.add_argument("-c", dest="command", metavar="PROG", help="program passed as a string")
    s.add_argument("--json", action="store_true", help="machine-readable")
    s.add_argument("argv", nargs=argparse.REMAINDER, metavar="FILE|-")
    s.set_defaults(func=cmd_route)

    # build
    s = _sub(subs, "build", "build the engines and install them into ~/.lypning/bin", """
Builds what you ask for and never stops on a failure: the tiers are
independent, so a missing 32-bit toolchain says nothing about the Rust core and
must not cost you its result.

A build is not `ok` until the refusal contract holds on the binary it just
produced — exit 90, one exact line on stderr, nothing at all on stdout. Only
`ok` binaries are installed into the bin dir; a broken one is left in the build
tree with its reason printed. `--lib` is held to the same contract through the
ABI instead of through a process: the refusal must arrive as a status, with an
empty stdout, the same one line, and a request to be routed onward.

The MicroPython tier downloads a musl toolchain and needs a network and
gcc-multilib. Without them this reports precisely which one is missing and
moves on.
""", """
examples:
  lypning build                       both tiers
  lypning build --lib                 the embeddable C ABI, for a harness
  lypning build --rust --target host  the dynamically linked glibc control
  lypning build --micropython --jobs 4
  lypning build --stock               the benchmark control, on its own
  lypning build --micropython --verify
  lypning build --all --dry-run       print the commands, build nothing
""")
    s.add_argument("--rust", action="store_true", help="build the Rust core")
    s.add_argument("--micropython", action="store_true", help="build the MicroPython tier")
    s.add_argument("--all", action="store_true",
                   help="both tiers and the library (the default when none is named is both tiers)")
    s.add_argument("--lib", action="store_true",
                   help="build the embeddable C ABI (liblypning.so, liblypning.a and the "
                        "headers) into ~/.lypning/lib and ~/.lypning/include — for C, C++, "
                        "Rust and Node hosts that run programs in-process instead of "
                        "spawning them")
    s.add_argument("--stock", action="store_true",
                   help="build the benchmark CONTROL: upstream MicroPython, unpatched, no "
                        "frozen stdlib, same pinned commit and toolchain. Not an engine and "
                        "not installed — it is what `lypning bench --micropython` measures "
                        "against")
    s.add_argument("--verify", action="store_true",
                   help="after building, gate what was built and run the whole conformance "
                        "battery with the new binaries pinned — the battery covers every tier "
                        "that is built, so it can fail on a tier this line did not touch")
    s.add_argument("--target", default="musl", metavar="T",
                   help="musl, host, x86_64, i686, or a full triple (default: musl — "
                        "static; `host` is the dynamically linked control)")
    s.add_argument("--jobs", type=int, metavar="N", help="parallel jobs for cargo")
    s.add_argument("--dry-run", action="store_true", help="print what would run; build nothing")
    s.add_argument("--json", action="store_true", help="machine-readable")
    s.add_argument("-v", "--verbose", action="store_true", help="append each build's log")
    s.set_defaults(func=cmd_build)

    # lib
    s = _sub(subs, "lib", "the flags a C or C++ host needs to link liblypning", """
What pkg-config would tell you, if this project shipped a .pc file. It does
not: a .pc file is a dependency on pkg-config, and a package that installs
itself next to whatever an agent is working on does not get to add one.

One line per flag, so it substitutes — `gcc $(lypning lib --cflags) prog.c
$(lypning lib --libs)`. --libs carries -Wl,-rpath on purpose, which pkg-config
would not have: nothing runs ldconfig over ~/.lypning/lib, so a host linked
without it compiles and links cleanly and then dies at exec with "cannot open
shared object file".

With no flag it prints the summary — where the shared object, the archive and
the headers are, how big they are, the ABI the library itself answers with, the
runtime version, and the gcc line to paste. With no library at all it is exit 2
and one line on stderr and nothing on stdout, because an empty -I is a compile
against whatever header was already on the include path. A flag this build
cannot answer — no archive beside the shared object, or a library that will not
load — is the same: one line on stderr, nothing on stdout, and never a partial
flag list. The summary and --json still describe it, because a person reads
those and a compiler does not.

The one rule of the surface those flags open: a run whose status is
LYPNING_UNSUPPORTED executed NOTHING, so run the program on CPython. That is a
route, never an error, and it is what lets the subset stay small.
""", """
examples:
  lypning lib
  gcc $(lypning lib --cflags) prog.c -o prog $(lypning lib --libs)
  g++ -std=c++17 $(lypning lib --cflags) host.cpp -o host $(lypning lib --libs)
  cc -I$(lypning lib --include) -c prog.c
  lypning lib --json | jq -r .gcc
""")
    s.add_argument("--cflags", action="store_true", help="-I<include dir>")
    s.add_argument("--libs", action="store_true",
                   help="-L<lib dir> -llypning -Wl,-rpath,<lib dir>")
    s.add_argument("--path", action="store_true", help="the shared object itself")
    s.add_argument("--static", action="store_true",
                   help="the archive, for a host that will not ship a .so beside its binary")
    s.add_argument("--include", action="store_true",
                   help="the directory holding lypning.h and lypning.hpp")
    s.add_argument("--json", action="store_true", help="machine-readable")
    s.set_defaults(func=cmd_lib)

    # status
    s = _sub(subs, "status", "what is built, wired and captured", """
The command to run when something is not behaving. It answers, in one screen:
which engines are built and where, how big they are in bytes and in 128 KiB
device blocks, whether the embeddable C ABI is built and which ABI it answers
with, how many programs the corpus holds, whether the shim is
installed and whether its directory is on PATH ahead of the real python,
whether this project has the capture hooks wired, and where the log is.

Reads only. `--json` is the same data for a machine.
""", "examples:\n  lypning status\n  lypning status --json | jq .engines")
    s.add_argument("--json", action="store_true", help="machine-readable")
    s.set_defaults(func=cmd_status)

    # doctor
    s = _sub(subs, "doctor", "check the install and say what to do about it", """
status with an opinion. Every line is OK, WARN or FAIL; doctor exits non-zero
if anything FAILed, so it is usable in CI.

It checks the toolchain, that each engine is built, that the refusal contract
still holds on the binary you actually have, that a real CPython (not the
capture shim) is findable, that the capture wiring is in place — and one
collision specific to this design: the pip console script and the Rust
interpreter are both named `lypning`, so a bin dir placed ahead of the scripts
directory on PATH makes `lypning status` reach the interpreter, which refuses
it as an unsupported CLI option.
""", "examples:\n  lypning doctor\n  lypning doctor --json")
    s.add_argument("--json", action="store_true", help="machine-readable")
    s.set_defaults(func=cmd_doctor)

    # install
    s = _sub(subs, "install", "wire capture into a project's .claude/", """
Installs three things, any of which can be turned off: the shim (`python` and
`python3` in the bin dir, which log what an agent runs), the Claude Code hooks
that publish those sightings, and the skill that documents the whole thing.

Settings are MERGED, never overwritten, and backed up first. Nothing outside
.claude/ and the bin dir is touched. `--dry-run` prints the plan and the exact
settings.json diff without writing a byte — read it first.

The shim refuses to overwrite a file it did not write unless you pass --force,
in which case the original is moved aside and restored on uninstall.
""", """
examples:
  lypning install --dry-run
  lypning install --project /path/to/repo
  lypning install --user --no-shim
""")
    s.add_argument("--project", metavar="DIR",
                   help="project root (default: $CLAUDE_PROJECT_DIR, else the git "
                        "toplevel, else the current directory)")
    s.add_argument("--user", action="store_true", help="install into ~/.claude instead of the project")
    s.add_argument("--no-shim", action="store_true", help="skip the python/python3 shim")
    s.add_argument("--no-hooks", action="store_true", help="skip the Claude Code hooks")
    s.add_argument("--no-skill", action="store_true", help="skip the skill documentation")
    s.add_argument("--force", action="store_true", help="move a foreign python/python3 aside instead of refusing")
    s.add_argument("--dry-run", action="store_true", help="print the plan and the settings diff; write nothing")
    s.add_argument("--json", action="store_true", help="machine-readable")
    s.set_defaults(func=cmd_install)

    # uninstall
    s = _sub(subs, "uninstall", "remove exactly what install added", """
Removes the skill, our hook scripts, our entries in settings.json and the
shims, restoring anything --force moved aside. Somebody else's hooks in the
same file are left alone.

The capture log is never touched: the programs in it outlive the harness that
captured them, and deleting them here would be unrecoverable.
""", "examples:\n  lypning uninstall --dry-run\n  lypning uninstall --user")
    s.add_argument("--project", metavar="DIR", help="project root")
    s.add_argument("--user", action="store_true", help="uninstall from ~/.claude")
    s.add_argument("--dry-run", action="store_true", help="list what would go; remove nothing")
    s.add_argument("--json", action="store_true", help="machine-readable")
    s.set_defaults(func=cmd_uninstall)

    # shim
    s = _sub(subs, "shim", "install, remove or inspect the python/python3 shim", """
The shim is a POSIX sh script named `python` and `python3` in the bin dir. It
appends one JSON line per invocation to the log and then execs the real
interpreter, so it is transparent to whatever ran it.

It only ever runs if its directory is first on PATH; `status` says whether it
is, and says so loudly when it is installed but shadowed — that failure and
"not installed at all" have the same symptom, an empty log.
""", """
examples:
  lypning shim status
  lypning shim install --force
  lypning shim uninstall --bin-dir /tmp/bin
""")
    s.add_argument("action", choices=("install", "uninstall", "status"))
    s.add_argument("--force", action="store_true", help="move a foreign python/python3 aside")
    s.add_argument("--bin-dir", metavar="DIR", help="where the shims live (default: ~/.lypning/bin)")
    s.add_argument("--json", action="store_true", help="machine-readable")
    s.set_defaults(func=cmd_shim)

    # hook
    s = _sub(subs, "hook", "Claude Code hook entry points (event JSON on stdin)", """
Not for typing. Claude Code runs these with the hook event on stdin and reads a
protocol response from stdout, so stdout carries that response and nothing
else — every diagnostic goes to stderr.

  pre-tool-use  screen a Bash command and log it if it is python
  stop          publish this session's sightings to tests/corpus/sightings/

Both exit 0 whatever happens. A capture failure must never fail the tool call
it was watching.
""", "examples:\n"
     "  echo '{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"python3 -c 1\"}}' |\n"
     "      lypning hook pre-tool-use\n"
     "  (only tool_name \"Bash\" is recorded; every other tool is answered and dropped)")
    s.add_argument("event", choices=("pre-tool-use", "stop"))
    s.set_defaults(func=cmd_hook)

    # conformance
    s = _sub(subs, "conformance", "run the corpus against CPython and grade the answers", """
Every harvested program on every built tier, graded against the real CPython:
MATCH, UNSUPPORTED (a clean exit-90 refusal — not a failure), or MISMATCH. Any
MISMATCH exits 1.

Programs whose output cannot be equal on two interpreters — timestamps, pids,
memory addresses, set ordering — are skipped by rule and listed, not quietly
passed. Each program runs in a temp cwd and the repository is checked for
collateral damage afterwards; the corpus is harvested from real sessions and is
full of programs that rewrite src/.

--plan turns the refusals into a build order: which unimplemented feature
blocks the most programs.

The same run grades ROUTING SAFETY, which is a different question: not whether
an engine agreed with CPython, but whether the classifier SENT each program to
one that did. UNSAFE (routed to an engine that mismatches) is fatal and exits 1
alongside a MISMATCH; WASTED and LATE are what a wrong route costs in spawns and
milliseconds, and neither gates anything.
""", """
examples:
  lypning conformance --limit 50
  lypning conformance --engine lypning --plan
  lypning conformance --engine library          the embedded tier, against CPython
  lypning conformance --json > conformance.json
""")
    s.add_argument("--engine", action="append", metavar="E",
                   help="arm to measure: lypning, lypning-mp, mixture, library "
                        "(repeatable). `library` is the same lypning reached through "
                        "the C ABI in this process — it shares the interpreter with the "
                        "`lypning` arm but not the exit path, so the two disagreeing is "
                        "the thing it is there to catch")
    s.add_argument("--plan", action="store_true",
                   help="print the build order implied by the refusals INSTEAD of the table")
    s.add_argument("--limit", type=int, metavar="N", help="only the first N corpus programs")
    s.add_argument("--timeout", type=float, default=30.0, metavar="S", help="per-program timeout (default 30)")
    s.add_argument("--json", action="store_true", help="machine-readable")
    s.set_defaults(func=cmd_conformance)

    # fuzz
    s = _sub(subs, "fuzz", "generate programs from the subset and diff against CPython", """
The conformance battery grades the programs agents happened to type; this
generates them. The corpus is a sample, not a specification, so "MISMATCH 0"
over it is evidence about lypning's surface only in proportion to how much of
that surface it touches — a fraction nobody has measured.

Programs are drawn from a typed grammar over lypning's OWN tables, so every
probe is something it claims to handle. Exit 90 means the generator wandered
outside the subset: counted as a refusal, never a finding. A genuine
disagreement at exit 0, a crash, or a refusal that broke the exit-90 contract
is a counterexample, and each one is shrunk to a minimal program before it is
printed. Any counterexample exits 1.

The seed makes a run exactly reproducible and is always printed: a fuzzer whose
failures cannot be replayed is a random number generator.
""", """
examples:
  lypning fuzz --iterations 2000
  lypning fuzz --seed 1 --iterations 600
  lypning fuzz --engine lypning-mp --json
""")
    s.add_argument("--iterations", type=int, default=500, metavar="N",
                   help="programs to generate (default 500)")
    s.add_argument("--seed", type=int, metavar="S",
                   help="fix the run; the default is random and is printed")
    # The tiers only. CPython is the oracle every arm is diffed against, so
    # offering it as an arm offers a run that compares it with itself, agrees on
    # every program, and prints a clean bill of health having tested nothing.
    s.add_argument("--engine", default=engines.LYPNING, metavar="E",
                   choices=[e for e in engines.ENGINE_ORDER if e != engines.CPYTHON],
                   help="tier under test (default lypning)")
    s.add_argument("--timeout", type=float, default=30.0, metavar="S",
                   help="per-program timeout (default 30)")
    s.add_argument("--json", action="store_true", help="machine-readable")
    s.set_defaults(func=cmd_fuzz)

    # bench
    s = _sub(subs, "bench", "time startup and the whole corpus, arm by arm", """
Two measurements. `--startup` is `-c 'pass'` on each arm, best of N, arms
interleaved so page-cache warmth is not handed to whichever arm went last.
`--corpus` is every harvested program on every arm. With neither flag you get
both.

An arm that is not built is a hole in the table, never a zero — a zero reads as
a win. Refusals are counted separately from runs, and the fair comparison is
the shared subset: only the programs EVERY arm actually ran.

Wall-clock numbers from a shared CI runner measure the runner, so a CI
environment is detected and labelled as such.
""", """
examples:
  lypning bench --startup
  lypning bench --corpus --limit 100
  lypning bench --repeat 3 --json
  lypning bench --micropython --record docs/BENCH-LEDGER.md
""")
    s.add_argument("--startup", action="store_true", help="startup only")
    s.add_argument("--corpus", action="store_true", help="corpus only")
    s.add_argument("--micropython", action="store_true",
                   help="a different comparison: lypning-mp against the benchmark control "
                        "(`lypning build --stock`), which is what says what OUR variant costs")
    s.add_argument("--record", metavar="FILE",
                   help="with --micropython, insert the run as an entry at the marker in "
                        "docs/BENCH-LEDGER.md (newest first)")
    s.add_argument("--note", metavar="TEXT",
                   help="with --micropython, the recorded entry's title — what this run was for")
    s.add_argument("--repeat", type=int, default=1, metavar="N", help="corpus passes, best per entry (default 1)")
    s.add_argument("--startup-repeat", type=int, default=5, metavar="N", help="startup samples (default 5)")
    s.add_argument("--limit", type=int, metavar="N", help="only the first N corpus programs")
    s.add_argument("--arm", action="append", metavar="A", help="cpython, lypning, lypning-mp, mixture (repeatable)")
    s.add_argument("--timeout", type=float, default=30.0, metavar="S", help="per-program timeout (default 30)")
    s.add_argument("--json", action="store_true", help="machine-readable")
    s.set_defaults(func=cmd_bench)

    # corpus-time
    s = _sub(subs, "corpus-time", "time the whole corpus on ONE binary", """
The instrument a speed change is accepted on, and a different question from
`bench`. `bench` compares arms — what the mixture costs against CPython.
This compares runs of one binary: did the change I just made speed up the
programs lypning is actually asked to run.

They disagree, routinely and by an order of magnitude, because a microbenchmark
loops twenty thousand times while a corpus entry runs once and exits — so its
cost is startup, parse and compile rather than steady-state dispatch. A
synthetic workload once said a compiler change was worth 48 ms per program
where the corpus said 0.14 ms (docs/MICROPYTHON.md §8a).

Min of --repeat, one temp cwd per entry, LYPNING_CAPTURE=0 in every child.
Entries that exit 90 are TIMED, not skipped — a refusal costs a spawn the agent
waited for — and counted separately, because a change that moves an entry in or
out of the subset changes what is being timed.

--record writes this run; --baseline reads one back and diffs the two over the
entries BOTH runs timed. The corpus grows every session, so that intersection
is stated rather than assumed. The corpus size loaded is printed every time.
""", """
examples:
  lypning corpus-time --record before.json
  lypning corpus-time --engine lypning-mp --repeat 5
  lypning corpus-time --engine target/release/lypning --baseline before.json
""")
    s.add_argument("--engine", default=engines.LYPNING, metavar="NAME|PATH",
                   help="cpython, lypning, lypning-mp, mixture — or a path to a binary that is "
                        "not installed yet (default: lypning)")
    s.add_argument("--repeat", type=int, default=3, metavar="N",
                   help="passes, best per entry (default 3)")
    s.add_argument("--limit", type=int, metavar="N", help="only the first N corpus programs")
    s.add_argument("--baseline", metavar="FILE", help="a --record file to diff this run against")
    s.add_argument("--record", metavar="FILE", help="write this run for a later --baseline")
    s.add_argument("--top", type=int, default=12, metavar="N",
                   help="biggest movers to list against a baseline (default 12)")
    s.add_argument("--timeout", type=float, default=30.0, metavar="S",
                   help="per-program timeout (default 30)")
    s.add_argument("--json", action="store_true", help="machine-readable")
    s.set_defaults(func=cmd_corpus_time)

    # perf
    s = _sub(subs, "perf", "time one construct at a time against CPython", """
Where the interpreter's time goes, construct by construct — and the one thing
in this tree that is deliberately NOT an acceptance gate.

`bench` and `corpus-time` time the programs agents actually type. Those run once
and exit, so most of what they cost is the spawn, and they are what a speed
change is accepted on. They cannot tell you WHICH construct to open next, because
a corpus entry touches twenty. This can: one small loop per construct, run on
lypning and on CPython, each arm's own startup subtracted, sorted worst ratio
first. That ordering is the work queue.

Read it as a diagnostic and accept on `corpus-time --baseline`. A microbenchmark
once said a change was worth 48 ms per program where the corpus said 0.14
(docs/MICROPYTHON.md 8a) — this table is the same kind of instrument and lies
the same way.

Every case prints a checksum and the arms must agree on it, so a construct that
is fast because it computes something else fails here rather than winning. A case
lypning REFUSES fails too: the suite is a claim about what the subset already
covers, and drift out of it is news. Either exits 1.
""", """
examples:
  lypning perf
  lypning perf --only str-concat --repeat 9
  lypning perf --record before.json
  lypning perf --baseline before.json
""")
    s.add_argument("--arm", action="append", metavar="A",
                   help="cpython, lypning, lypning-mp, mixture (repeatable; "
                        "default cpython and lypning)")
    s.add_argument("--repeat", type=int, default=5, metavar="N",
                   help="samples per case, best of (default 5)")
    s.add_argument("--only", action="append", metavar="CASE",
                   help="just this case (repeatable)")
    s.add_argument("--list", action="store_true", help="the suite; run nothing")
    s.add_argument("--top", type=int, default=0, metavar="N",
                   help="only the N worst rows (default 0, all)")
    s.add_argument("--baseline", metavar="FILE", help="a --record file to diff this run against")
    s.add_argument("--record", metavar="FILE", help="write this run for a later --baseline")
    s.add_argument("--diff-arm", default=engines.LYPNING, metavar="A",
                   help="which arm the --baseline diff is about (default lypning)")
    s.add_argument("--timeout", type=float, default=60.0, metavar="S",
                   help="per-case timeout (default 60)")
    s.add_argument("--json", action="store_true", help="machine-readable")
    s.set_defaults(func=cmd_perf)

    # gate
    s = _sub(subs, "gate", "measure a binary against the acceptance table", """
The acceptance table for a binary that has to start cold in a sandbox: it runs
`-c 'pass'`, it is statically linked, it links no shared objects, it fits the
size budget, and it opens at most a handful of files at startup. Exits 1 if any
check that could be taken failed.

A check that could NOT be taken — no strace, no readelf — is reported as
unmeasured rather than as a pass. Zero shared objects from a toolchain that
cannot read them is an artefact, not a result.

With no BIN named it gates lypning-mp, whose budget this is; if that is not
built the Rust core stands in and the substitution gets its own row so nobody
reads the numbers as lypning-mp's. --compare adds the same measurements taken
against the real CPython on this machine.
""", """
examples:
  lypning gate
  lypning gate --compare
  lypning gate ./target/release/lypning --json
""")
    s.add_argument("binary", nargs="?", metavar="BIN", help="binary to measure (default: lypning-mp, else lypning)")
    s.add_argument("--compare", action="store_true", help="measure the real CPython alongside it")
    s.add_argument("--json", action="store_true", help="machine-readable")
    s.set_defaults(func=cmd_gate)

    # harvest
    s = _sub(subs, "harvest", "turn captured invocations into corpus entries", """
Two steps that are deliberately separate.

  --export   publish THIS session's captures as tests/corpus/sightings/<id>.jsonl
             and write no corpus. One writer per path, so two sessions cannot
             conflict. Idempotent: re-running rewrites nothing.

  (default)  DERIVE the corpus from every published sightings file plus the
             live log, and write it. Run deliberately, never from a hook.

Everything is redacted before it is written — these files are committed — and
programs already in the corpus are not re-counted.
""", """
examples:
  lypning harvest --export --quiet     what the Stop hook runs
  lypning harvest --dry-run
  lypning harvest --json
""")
    s.add_argument("--export", action="store_true", help="publish sightings only; write no corpus")
    s.add_argument("--transcripts", action="store_true", help="also scan Claude Code transcripts")
    s.add_argument("--dry-run", action="store_true", help="report; write no corpus")
    s.add_argument("--quiet", action="store_true", help="say nothing; the exit code is the answer")
    s.add_argument("--json", action="store_true", help="machine-readable")
    s.set_defaults(func=cmd_harvest)

    # corpus
    s = _sub(subs, "corpus", "inspect the harvested programs", """
The corpus is the argument: every design decision in this project is downstream
of what agents actually type. --stats is the shape of it — how many programs,
how many are one-liners, the length distribution, and which modules and
builtins appear most, which is the same thing as the implementation order.

--list prints one line per program. --json prints the records themselves, in
the on-disk normal form.
""", """
examples:
  lypning corpus --stats
  lypning corpus --list | head
  lypning corpus --json | jq -r .[].id
""")
    s.add_argument("--stats", action="store_true", help="the distribution and the top modules/builtins")
    s.add_argument("--list", action="store_true", help="one line per program")
    s.add_argument("--top", type=int, default=12, metavar="N", help="how many modules/builtins to rank (default 12)")
    s.add_argument("--json", action="store_true", help="machine-readable")
    s.set_defaults(func=cmd_corpus)

    return p


# --- entry point -------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    # Before argparse, always: an interpreter that parsed its own subcommands
    # first would fail on `-c`, which is the argument it exists to accept.
    interp = interpreter_argv(args)
    if interp is not None:
        return exec_interpreter(interp)

    parser = build_parser()
    ns = parser.parse_args(args)
    func = getattr(ns, "func", None)
    if func is None:
        parser.print_help()
        return 0

    try:
        return int(func(ns) or 0)
    except Usage as e:
        sys.stderr.write("lypning: %s: %s\n" % (ns.cmd, e))
        return 2
    except engines.EngineError as e:
        # A named engine that is not one. Exit 2: nothing ran, and the fix is
        # an environment variable rather than a change to the program.
        sys.stderr.write("lypning: %s\n" % e)
        return 2
    except Failure as e:
        sys.stderr.write("lypning: %s\n" % e)
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("\nlypning: interrupted\n")
        return 130
    except BrokenPipeError:
        # `lypning corpus --list | head` closes the pipe under us. That is the
        # caller getting what it wanted, not an error worth a message.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        return 0
    except Exception as e:
        # No traceback ever reaches a user — except a developer's, on request.
        if os.environ.get("LYPNING_DEBUG") == "1":
            raise
        sys.stderr.write("lypning: %s: %s\n" % (type(e).__name__, e))
        sys.stderr.write("lypning: set LYPNING_DEBUG=1 to see the traceback\n")
        return 1
    finally:
        try:
            sys.stdout.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
