"""Select reverse-prompting candidates for eval-2 from ``data/classified.jsonl``.

WHAT A CANDIDATE IS. A program the engine already runs (``tier1``) or refuses
(``refused``), whose stdout is *reproducible*: it was regenerated twice under
:func:`pipeline.sandbox.run_python`, once with the sandbox's pinned hash seed
and once with a different one, and both runs exited 0 with the same bytes.
For a refused entry the recorded ``expect_stdout`` must also match, or the
record is reported as drift rather than silently replaced.

WHAT IS NEVER A KEY. The refusal kind is carried on the record for the reader
and is used nowhere in this module: not to include, not to exclude, not to
stratify. Stratification is by *program shape* — a line-count bucket and a
coarse token-shape signature — so that a draw reflects what agents type, not
what the engine happens to refuse this week.

THE EXCLUSION RULES are named, counted and reported one by one, and each is
decided from the program text alone. The repository's own detectors are reused
where they exist (``absolute_paths``, ``is_nondeterministic``,
``spawns_a_battery`` from ``lypning.conformance``); the
detector takes an object with a ``.program`` attribute, so a corpus dict is
wrapped through ``lypning.corpus.Entry`` first — a dict handed to it directly
reads as an empty program and waives nothing.

This module returns data. ``cli.py`` renders it.
"""

from __future__ import annotations

import ast
import hashlib
import random
import re
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .jsonio import digest, read_jsonl
from .sandbox import RunResult, run_python

#: The outcomes a candidate may come from. Refusal kind is deliberately absent.
OUTCOMES = ("tier1", "refused")

#: Every rule, in the order it is applied. A program is charged to the FIRST
#: rule that rejects it, so the counts sum to the number excluded.
RULES = (
    "outcome",            # not tier1 / refused
    "mentions-tooling",   # names lypning or ntx
    "absolute-path",      # names a path outside its temp cwd (program or argv)
    "writes-files",       # creates, truncates, renames or removes a file
    "spawns-subprocess",  # subprocess, os.system, fork, exec, multiprocessing
    "reads-network",      # socket, urllib, http and friends
    "nondeterministic",   # randomness, clocks, pids, hash order
    "empty-stdout",       # nothing to reverse-prompt from
    "duplicate",          # same normalized program text as an earlier entry
    "run-failed",         # a regeneration run did not exit 0
    "runs-disagree",      # the two regeneration runs differ
    "stdout-drift",       # refused: regenerated stdout is not the recorded one
)

#: The second regeneration run's hash seed. The sandbox pins ``PYTHONHASHSEED``
#: to 0; a program whose stdout survives a different seed does not depend on
#: string-hash order, which no static rule can decide.
ALT_HASH_SEED = "1111"

_TOOLING = re.compile(r"lypning|(?<![A-Za-z0-9_])ntx", re.I)

_SUBPROCESS_MODULES = frozenset(("subprocess", "multiprocessing", "pty", "concurrent"))
_SUBPROCESS_CALLS = re.compile(
    r"\bos\s*\.\s*(?:system|popen|fork|forkpty|exec[lv]p?e?|spawn[lv]p?e?|posix_spawnp?)\s*\(")
_NETWORK_MODULES = frozenset((
    "socket", "ssl", "urllib", "http", "ftplib", "smtplib", "poplib", "imaplib",
    "telnetlib", "xmlrpc", "socketserver", "requests", "httpx", "aiohttp",
    "websocket", "websockets", "urllib3", "asyncio",
))
_HASH_ORDER = re.compile(r"\bhash\s*\(")
_WRITE_MODE = re.compile(r"""\bopen\s*\([^)]*['"][rb]*[wxa+][a-z+]*['"]""")
_WRITE_CALLS = re.compile(
    r"\b(?:write_text|write_bytes|touch|mkdir|makedirs|unlink|rmdir|rename|remove|"
    r"rmtree|copyfile|copy2|copytree|move|symlink|link|truncate|chmod|NamedTemporaryFile|"
    r"mkstemp|mkdtemp)\s*\(")
_WRITER_ATTRS = frozenset((
    # Not ``replace``: ``str.replace`` is in half the corpus. ``os.replace`` is
    # caught by its dotted name; a bare ``Path(...).replace`` is a known miss.
    "write_text", "write_bytes", "touch", "mkdir", "unlink", "rmdir", "rename",
    "symlink_to", "hardlink_to", "chmod",
))
_WRITER_DOTTED = frozenset((
    "os.remove", "os.unlink", "os.rename", "os.replace", "os.mkdir", "os.makedirs",
    "os.rmdir", "os.removedirs", "os.symlink", "os.link", "os.truncate", "os.chmod",
    "os.open", "os.fdopen", "shutil.rmtree", "shutil.copy", "shutil.copyfile",
    "shutil.copy2", "shutil.copytree", "shutil.move", "tempfile.mkstemp",
    "tempfile.mkdtemp", "tempfile.NamedTemporaryFile", "tempfile.TemporaryDirectory",
    "tempfile.TemporaryFile",
))

_LINE_BUCKETS = ((1, "1"), (3, "2-3"), (8, "4-8"), (20, "9-20"))


def _repo():
    """The repository's own skip rules and record type. Lazy, like lypning_source."""
    from lypning import conformance as conf  # noqa: WPS433
    from lypning import corpus  # noqa: WPS433
    return conf, corpus


def _tree(src: str) -> Optional[ast.AST]:
    try:
        return ast.parse(src)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return None


def _dotted(node: Any) -> str:
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _imported_roots(tree: ast.AST) -> set:
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _open_mode(node: ast.Call) -> str:
    """The mode of an ``open``-like call, ``"r"`` when it is left to default."""
    mode = "r"
    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) \
            and isinstance(node.args[1].value, str):
        mode = node.args[1].value
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant) \
                and isinstance(kw.value.value, str):
            mode = kw.value.value
    return mode


# --- the rules ----------------------------------------------------------------


def mentions_tooling(program: str) -> bool:
    return bool(_TOOLING.search(program or ""))


def writes_files(program: str) -> bool:
    """Does the program put bytes on disk, by any spelling it could be written in?

    Not ``conformance.paths_written``: it answers only for literal paths, and it
    reads ``"a-b".replace("-", "+")`` as a write to the file ``a-b`` (its writer
    list has ``replace`` and it takes a string-literal receiver as the path),
    which would throw out a slice of the corpus for nothing. The AST is walked
    for the call shapes themselves, literal path or not; the text is the
    fallback when the program does not parse.
    """
    tree = _tree(program)
    if tree is None:
        return bool(_WRITE_MODE.search(program) or _WRITE_CALLS.search(program))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        if name in ("open", "io.open", "codecs.open") and set(_open_mode(node)) & set("wxa+"):
            return True
        if name in _WRITER_DOTTED:
            return True
        if isinstance(node.func, ast.Attribute) and node.func.attr in _WRITER_ATTRS:
            return True
    return False


def spawns_subprocess(program: str) -> bool:
    conf, _ = _repo()
    if conf.spawns_a_battery(program):
        return True
    if _SUBPROCESS_CALLS.search(program):
        return True
    tree = _tree(program)
    if tree is None:
        return bool(re.search(r"\b(?:subprocess|multiprocessing|pty)\b", program))
    return bool(_imported_roots(tree) & _SUBPROCESS_MODULES)


def reads_network(program: str) -> bool:
    tree = _tree(program)
    if tree is None:
        return bool(re.search(r"\b(?:%s)\b" % "|".join(sorted(_NETWORK_MODULES)), program))
    return bool(_imported_roots(tree) & _NETWORK_MODULES)


def is_nondeterministic(entry: Dict[str, Any]) -> bool:
    """The repository's detector, plus ``hash()``, whose order the sandbox pins
    but the eval that consumes these candidates may not."""
    conf, corpus = _repo()
    rec = corpus.Entry.from_obj(entry)
    if rec is None or conf.is_nondeterministic(rec):
        return True
    view = conf.code_view(rec.program)
    return bool(_HASH_ORDER.search(rec.program if view is None else view))


def absolute_paths(entry: Dict[str, Any]) -> List[str]:
    conf, _ = _repo()
    out = conf.absolute_paths(entry.get("program") or "")
    for a in entry.get("argv_tail") or []:
        out.extend(p for p in conf.absolute_paths(str(a)) if p not in out)
    return out


# --- shape ------------------------------------------------------------------


def normalized(program: str) -> str:
    """The dedupe key: trailing whitespace and blank lines are not the program."""
    return "\n".join(l.rstrip() for l in (program or "").strip().splitlines() if l.strip())


def line_bucket(program: str) -> str:
    n = len(normalized(program).splitlines())
    for ceiling, name in _LINE_BUCKETS:
        if n <= ceiling:
            return name
    return "21+"


def token_shape(program: str) -> str:
    """A coarse signature of what the program is made of, never of what it means.

    Four flags, present or absent: ``I`` imports, ``D`` defines a function or
    class, ``L`` loops (or comprehends), ``B`` branches (if, try).
    ``-`` when none. Coarse on purpose: a signature that separates every
    program is a partition into singletons, not a stratification.
    """
    tree = _tree(program)
    flags = []
    if tree is not None:
        kinds = {type(n) for n in ast.walk(tree)}
        if kinds & {ast.Import, ast.ImportFrom}:
            flags.append("I")
        if kinds & {ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda}:
            flags.append("D")
        if kinds & {ast.For, ast.While, ast.AsyncFor, ast.ListComp, ast.SetComp,
                    ast.DictComp, ast.GeneratorExp}:
            flags.append("L")
        if kinds & {ast.If, ast.IfExp, ast.Try, ast.While}:
            flags.append("B")
    else:
        text = program or ""
        if re.search(r"^\s*(?:import|from)\s", text, re.M):
            flags.append("I")
        if re.search(r"\b(?:def|class|lambda)\b", text):
            flags.append("D")
        if re.search(r"\b(?:for|while)\b", text):
            flags.append("L")
        if re.search(r"\b(?:if|try)\b", text):
            flags.append("B")
    return "".join(flags) or "-"


def shape_bucket(program: str) -> str:
    return "%s:%s" % (line_bucket(program), token_shape(program))


def source_sha256(program: str) -> str:
    """SHA-256 of the exact program bytes — the identity ``lypning.evidence`` uses."""
    return hashlib.sha256(program.encode("utf-8")).hexdigest()


# --- session provenance -------------------------------------------------------


def session_index(sightings_dir: Optional[Path]) -> Dict[str, str]:
    """``entry id -> sightings file name`` for every session file in the tree.

    The classified corpus does not say which session typed a program; the
    per-session sightings files do. An id seen in several sessions maps to the
    lexically first file, so the answer does not depend on directory order.
    """
    index: Dict[str, str] = {}
    if sightings_dir is None or not Path(sightings_dir).is_dir():
        return index
    for path in sorted(Path(sightings_dir).glob("*.jsonl")):
        try:
            rows = read_jsonl(path)
        except (ValueError, OSError):
            continue
        for row in rows:
            for key in ("id", "key"):
                ident = row.get(key)
                if isinstance(ident, str) and ident and ident not in index:
                    index[ident] = path.name
    return index


# --- regeneration -----------------------------------------------------------


Runner = Callable[..., RunResult]


def _stdin_of(entry: Dict[str, Any]) -> Optional[str]:
    for key in ("stdin_sample", "stdin"):
        v = entry.get(key)
        if isinstance(v, str):
            return v
    return None


def regenerate(entry: Dict[str, Any], *, runner: Runner = run_python,
               timeout_s: float = 10.0) -> Tuple[Optional[str], str]:
    """``(stdout, reason)``: the agreed stdout, or None and the rule that failed.

    Two runs, the second under a different hash seed. Both must exit 0 under
    their own power and print the same bytes.
    """
    program = entry.get("program") or ""
    argv = [str(a) for a in (entry.get("argv_tail") or [])]
    stdin = _stdin_of(entry)
    first = runner(program, argv=argv, stdin=stdin, timeout_s=timeout_s)
    if not first.ok:
        return None, "run-failed"
    second = runner(program, argv=argv, stdin=stdin, timeout_s=timeout_s,
                    env_extra={"PYTHONHASHSEED": ALT_HASH_SEED})
    if not second.ok:
        return None, "run-failed"
    if first.stdout != second.stdout:
        return None, "runs-disagree"
    return first.stdout, ""


# --- selection --------------------------------------------------------------


def static_rule(rec: Dict[str, Any]) -> str:
    """The first static rule that rejects ``rec``, or ``""``. No execution."""
    if rec.get("outcome") not in OUTCOMES:
        return "outcome"
    entry = rec.get("entry") or {}
    program = entry.get("program") or ""
    if mentions_tooling(program):
        return "mentions-tooling"
    if absolute_paths(entry):
        return "absolute-path"
    if writes_files(program):
        return "writes-files"
    if spawns_subprocess(program):
        return "spawns-subprocess"
    if reads_network(program):
        return "reads-network"
    if is_nondeterministic(entry):
        return "nondeterministic"
    if rec["outcome"] == "refused" and not str((rec.get("info") or {}).get("expect_stdout") or "").strip():
        return "empty-stdout"
    return ""


def _candidate(rec: Dict[str, Any], stdout: str, sessions: Dict[str, str]) -> Dict[str, Any]:
    entry = rec["entry"]
    info = rec.get("info") or {}
    program = entry.get("program") or ""
    argv = [str(a) for a in (entry.get("argv_tail") or [])]
    stdin = _stdin_of(entry)
    return {
        "candidate_id": "e2-" + digest({"program": program, "argv": argv, "stdin": stdin}),
        "source_entry_id": entry.get("id"),
        "session_file": sessions.get(entry.get("id") or ""),
        "program": program,
        "argv": argv,
        "stdin": stdin,
        "stdout": stdout,
        "exit_code": 0,
        "outcome": rec["outcome"],
        "refusal_kind": info.get("kind") if rec["outcome"] == "refused" else None,
        "shape_bucket": shape_bucket(program),
        "source_sha256": source_sha256(program),
    }


def _draw(cands: List[Dict[str, Any]], limit: int, seed: int) -> List[Dict[str, Any]]:
    """A deterministic stratified draw of ``limit`` across shape buckets.

    Proportional allocation with largest remainders, then a seeded shuffle
    inside each bucket. Sorted by candidate id first so the answer does not
    depend on the order the corpus was read in.
    """
    by_bucket: Dict[str, List[Dict[str, Any]]] = {}
    for c in sorted(cands, key=lambda c: c["candidate_id"]):
        by_bucket.setdefault(c["shape_bucket"], []).append(c)
    if limit <= 0 or limit >= len(cands):
        return [c for b in sorted(by_bucket) for c in by_bucket[b]]
    rng = random.Random(seed)
    total = len(cands)
    quota: Dict[str, int] = {}
    remainders: List[Tuple[float, str]] = []
    for b in sorted(by_bucket):
        share = limit * len(by_bucket[b]) / total
        quota[b] = int(share)
        remainders.append((share - quota[b], b))
    short = limit - sum(quota.values())
    for _, b in sorted(remainders, key=lambda r: (-r[0], r[1])):
        if short <= 0:
            break
        if quota[b] < len(by_bucket[b]):
            quota[b] += 1
            short -= 1
    out: List[Dict[str, Any]] = []
    for b in sorted(by_bucket):
        pool = list(by_bucket[b])
        rng.shuffle(pool)
        out.extend(sorted(pool[: quota[b]], key=lambda c: c["candidate_id"]))
    return out


def select(records: Iterable[Dict[str, Any]], *, limit: int = 0, seed: int = 1111,
           sightings_dir: Optional[Path] = None, runner: Runner = run_python,
           timeout_s: float = 10.0, jobs: int = 1) -> Dict[str, Any]:
    """Select candidates. Returns counts and the records; prints nothing.

    ``runner`` is :func:`pipeline.sandbox.run_python` or a test double with the
    same signature. ``jobs`` regenerates that many programs at once.
    """
    records = list(records)
    excluded: Dict[str, int] = {r: 0 for r in RULES}
    dropped: List[Dict[str, Any]] = []
    survivors: List[Dict[str, Any]] = []
    seen: set = set()
    for rec in records:
        rule = static_rule(rec)
        if not rule:
            key = normalized((rec.get("entry") or {}).get("program") or "")
            if key in seen:
                rule = "duplicate"
            else:
                seen.add(key)
        if rule:
            excluded[rule] += 1
            dropped.append({"id": ((rec.get("entry") or {}).get("id")), "rule": rule})
        else:
            survivors.append(rec)

    def regen(rec: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str], str]:
        stdout, why = regenerate(rec["entry"], runner=runner, timeout_s=timeout_s)
        return rec, stdout, why

    if jobs > 1 and survivors:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(regen, survivors))
    else:
        results = [regen(r) for r in survivors]

    sessions = session_index(sightings_dir)
    cands: List[Dict[str, Any]] = []
    for rec, stdout, why in results:
        if stdout is None:
            excluded[why] += 1
            dropped.append({"id": rec["entry"].get("id"), "rule": why})
            continue
        if rec["outcome"] == "refused" and stdout != (rec.get("info") or {}).get("expect_stdout"):
            excluded["stdout-drift"] += 1
            dropped.append({"id": rec["entry"].get("id"), "rule": "stdout-drift"})
            continue
        if not stdout.strip():
            excluded["empty-stdout"] += 1
            dropped.append({"id": rec["entry"].get("id"), "rule": "empty-stdout"})
            continue
        cands.append(_candidate(rec, stdout, sessions))

    chosen = _draw(cands, limit, seed)
    by_bucket: Dict[str, int] = {}
    by_outcome: Dict[str, int] = {}
    for c in chosen:
        by_bucket[c["shape_bucket"]] = by_bucket.get(c["shape_bucket"], 0) + 1
        by_outcome[c["outcome"]] = by_outcome.get(c["outcome"], 0) + 1
    return {
        "loaded": len(records),
        "eligible": len(cands),
        "selected": len(chosen),
        "limit": limit,
        "seed": seed,
        "excluded": {r: n for r, n in excluded.items() if n},
        "by_bucket": by_bucket,
        "by_outcome": by_outcome,
        "dropped": dropped,
        "candidates": chosen,
    }


def render(result: Dict[str, Any]) -> str:
    """The counts as text, for the CLI. Every number is this run's."""
    lines = ["classified %d loaded   eligible %d   selected %d   (seed %d, limit %s)"
             % (result["loaded"], result["eligible"], result["selected"], result["seed"],
                result["limit"] or "none")]
    lines.append("  by outcome:")
    for k, n in sorted(result["by_outcome"].items()):
        lines.append("    %-10s %5d" % (k, n))
    lines.append("  by shape bucket (lines:shape):")
    for k, n in sorted(result["by_bucket"].items()):
        lines.append("    %-10s %5d" % (k, n))
    lines.append("  excluded per rule:")
    for rule in RULES:
        n = result["excluded"].get(rule, 0)
        if n:
            lines.append("    %-18s %5d" % (rule, n))
    return "\n".join(lines)
