"""Where candidate cases come from. One function per source shape.

Adding a source is adding a function to :data:`ADAPTERS` that yields *candidates*
— dicts with ``prompt``, an optional ``reference``, a ``test``, and whatever
failing program put the case on the list. Nothing here decides what is kept;
:mod:`pipeline.acceptance` does, and it does it by running the test.

The three shipped adapters:

``study``      the repository's own 26-task bank (``study/tasks.jsonl``). Prompt,
               reference and an exact-stdout acceptance test, already executable.
               No generation has failed these yet, so they land as ``unobserved``.
``evalfail``   the failures of an eval run (step 2's ``attempts.jsonl``). This is
               the loop that actually produces "previously-failing cases": run
               the eval, harvest what it failed, and each failure arrives with
               its own negative control attached.
``jsonl``      anything else, with ``--map`` to rename fields. Write your export
               to the native shape and no mapping is needed.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from .classify import is_category
from .jsonio import iter_jsonl, read_jsonl

# A candidate is the pre-gate record. Keys: prompt, reference, test, category,
# source_id, negative (program string), negative_detail, tags, notes.
Candidate = Dict[str, Any]


def _test_from_convenience(rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build a test spec from the flat fields, or None if the record has no test.

    This is the only place a test is ever *constructed*. It never invents an
    expectation: if the record carries nothing that can be checked, the answer
    is None and the case is dropped upstream as ``no-test``.
    """
    if isinstance(rec.get("test"), dict):
        return rec["test"]
    common = {}
    for k in ("stdin", "argv", "files", "timeout_s", "mem_mb", "normalize", "expect_exit"):
        if rec.get(k) is not None:
            common[k] = rec[k]
    if rec.get("test_file"):
        return dict(common, kind="pytest", test_file=rec["test_file"])
    if rec.get("checker"):
        return dict(common, kind="script", checker=rec["checker"])
    if rec.get("expect_stdout") is not None:
        return dict(common, kind="stdout", expect_stdout=rec["expect_stdout"])
    if rec.get("expect_stdout_re") is not None:
        return dict(common, kind="stdout", expect_stdout_re=rec["expect_stdout_re"])
    return None


def _study_files(files: Dict[str, str]) -> Dict[str, Any]:
    """study/tasks.jsonl stores non-text setup files as latin-1 in a JSON string.

    That convention is local to that file, so it is decoded here rather than
    carried into the corpus: anything with a codepoint above 127 becomes explicit
    base64, and everything downstream sees one unambiguous shape.
    """
    out: Dict[str, Any] = {}
    for name, content in files.items():
        if any(ord(ch) > 127 for ch in content):
            out[name] = {"base64": base64.b64encode(content.encode("latin-1")).decode("ascii")}
        else:
            out[name] = content
    return out


def study_adapter(spec: Dict[str, Any]) -> Iterator[Candidate]:
    path = Path(spec.get("path") or "../study/tasks.jsonl")
    for t in read_jsonl(path):
        test: Dict[str, Any] = {"kind": "stdout", "expect_stdout": t["expect_stdout"]}
        for k in ("stdin", "argv"):
            if t.get(k):
                test[k] = t[k]
        if t.get("files"):
            test["files"] = _study_files(t["files"])
        yield {
            "prompt": t["ask"],
            "reference": t.get("reference"),
            "test": test,
            "category": "unobserved",
            "source": "study",
            "source_id": t["id"],
            "tags": ["task:%s" % t.get("category", "?")],
            "notes": t.get("tempts", ""),
        }


def evalfail_adapter(spec: Dict[str, Any]) -> Iterator[Candidate]:
    """Failures of a completed eval run, each carrying the program that failed."""
    attempts = Path(spec["attempts"])
    corpus_path = spec.get("corpus")
    by_id: Dict[str, Dict[str, Any]] = {}
    for src in ([corpus_path] if corpus_path else []):
        for c in read_jsonl(src):
            by_id[c["id"]] = c
    for a in iter_jsonl(attempts):
        if a.get("passed"):
            continue
        case = by_id.get(a.get("case_id"))
        if case is None:
            continue
        yield {
            "prompt": case["prompt"],
            "reference": case.get("reference"),
            "test": case["test"],
            "category": a.get("failure_category") or "wrong-output",
            "source": "evalfail",
            "source_id": "%s:%s" % (a.get("run_id", "?"), a.get("case_id")),
            "negative": a.get("program") or "",
            "negative_detail": a.get("detail", ""),
            "tags": list(case.get("tags") or []),
            "notes": case.get("notes", ""),
        }


def jsonl_adapter(spec: Dict[str, Any]) -> Iterator[Candidate]:
    path = Path(spec["path"])
    mapping: Dict[str, str] = spec.get("map") or {}
    for i, raw in enumerate(iter_jsonl(path)):
        rec = dict(raw)
        for dst, src in mapping.items():
            val: Any = rec
            for part in src.split("."):
                val = val.get(part) if isinstance(val, dict) else None
            if val is not None:
                rec[dst] = val
        prompt = rec.get("prompt") or rec.get("instruction") or rec.get("ask")
        if not prompt:
            continue
        cat = rec.get("category") or rec.get("failure_category") or "wrong-output"
        yield {
            "prompt": prompt,
            "reference": rec.get("reference") or rec.get("solution"),
            "test": _test_from_convenience(rec),
            "category": cat if is_category(cat) else "wrong-output",
            "source": spec.get("name") or "jsonl",
            "source_id": str(rec.get("id") or rec.get("task_id") or "%s#%d" % (path.name, i)),
            "negative": rec.get("failing_program") or rec.get("generation") or "",
            "negative_detail": rec.get("failure_detail") or rec.get("error") or "",
            "tags": list(rec.get("tags") or []),
            "notes": rec.get("notes", ""),
        }


ADAPTERS: Dict[str, Callable[[Dict[str, Any]], Iterator[Candidate]]] = {
    "study": study_adapter,
    "evalfail": evalfail_adapter,
    "jsonl": jsonl_adapter,
}


def parse_source(arg: str) -> Dict[str, Any]:
    """``name`` or ``name:key=value,key=value``. ``map`` takes ``dst=src`` pairs."""
    if ":" in arg:
        name, rest = arg.split(":", 1)
    else:
        name, rest = arg, ""
    if name not in ADAPTERS:
        raise ValueError("unknown source %r (have: %s)" % (name, ", ".join(sorted(ADAPTERS))))
    spec: Dict[str, Any] = {"name": name}
    for part in [p for p in rest.split(",") if p]:
        if "=" not in part:
            raise ValueError("source option needs key=value: %r" % part)
        k, v = part.split("=", 1)
        if k == "map":
            dst, _, src = v.partition("->")
            spec.setdefault("map", {})[dst] = src or dst
        else:
            spec[k] = v
    return spec
