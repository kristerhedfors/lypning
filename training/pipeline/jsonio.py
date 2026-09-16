"""Canonical JSON, stable ids, and JSONL that round-trips byte-for-byte.

Everything downstream hashes records, so there is exactly one serialization:
sorted keys, no spaces, UTF-8 kept as UTF-8. A record written twice is the same
bytes twice, which is what makes a frozen split checkable rather than promised.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List


def canon(obj: Any) -> str:
    """The one serialization. Sorted, compact, unescaped."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(obj: Any, length: int = 12) -> str:
    """A stable short hash of ``obj``'s canonical form."""
    return hashlib.sha256(canon(obj).encode("utf-8")).hexdigest()[:length]


def sha256_of(obj: Any) -> str:
    return hashlib.sha256(canon(obj).encode("utf-8")).hexdigest()


def read_jsonl(path: str | os.PathLike) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    out: List[Dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError("%s:%d: %s" % (p, n, exc)) from None
    return out


def iter_jsonl(path: str | os.PathLike) -> Iterator[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_jsonl(path: str | os.PathLike, rows: Iterable[Dict[str, Any]]) -> int:
    """Write atomically. A half-written corpus is worse than no corpus."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(canon(row))
                fh.write("\n")
                n += 1
        os.replace(tmp, str(p))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return n


def append_jsonl(path: str | os.PathLike, row: Dict[str, Any]) -> None:
    """Append one record and fsync. The sweep must survive a preemption."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(canon(row))
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())


def write_json(path: str | os.PathLike, obj: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, sort_keys=True, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, str(p))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_json(path: str | os.PathLike) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))
