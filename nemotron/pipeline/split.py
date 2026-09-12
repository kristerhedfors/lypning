"""Step 1's last act: a 70/30 stratified split, and a lock that makes "frozen" true.

A held-out split that is frozen by intention drifts the first time someone
re-harvests. This one is frozen by a file: ``holdout.lock.json`` records every
held-out case id together with the SHA-256 of its canonical record, plus a
manifest hash over the whole list. :func:`verify` recomputes both from the
current corpus and fails loudly on any drift — a changed test, a dropped case, an
added one. A baseline measured before the drift is not comparable to anything
measured after it, and the lock is what turns that from a worry into a check.

Assignment is by hash, not by a shuffle: within each category, cases are ordered
by ``sha256(salt || id)`` and the first 30% are held out. No RNG state to carry,
and the same corpus plus the same salt always gives the same split.

After the freeze, growth only ever lands in train. Re-freezing is possible and
requires saying so out loud (``--refreeze``), because it invalidates every
baseline and every delta measured against one.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .jsonio import read_json, read_jsonl, sha256_of, write_json, write_jsonl

DEFAULT_FRACTION = 0.30
DEFAULT_SALT = "ntx-holdout-v1"
LOCK_NAME = "holdout.lock.json"


def _rank(salt: str, cid: str) -> str:
    return hashlib.sha256((salt + "\x00" + cid).encode("utf-8")).hexdigest()


def _stratified(cases: List[Dict[str, Any]], fraction: float, salt: str) -> List[str]:
    by_cat: Dict[str, List[str]] = defaultdict(list)
    for c in cases:
        by_cat[c["category"]].append(c["id"])
    holdout: List[str] = []
    for cat in sorted(by_cat):
        ids = sorted(by_cat[cat], key=lambda i: _rank(salt, i))
        n = int(len(ids) * fraction + 0.5)
        holdout.extend(ids[:n])
    return sorted(holdout)


def freeze(
    corpus_path: Path,
    *,
    fraction: float = DEFAULT_FRACTION,
    salt: str = DEFAULT_SALT,
    refreeze: bool = False,
) -> Dict[str, Any]:
    """Create (or, with ``refreeze``, replace) the lock. Returns the lock body."""
    out_dir = corpus_path.parent
    lock_path = out_dir / LOCK_NAME
    cases = read_jsonl(corpus_path)
    by_id = {c["id"]: c for c in cases}
    if not cases:
        raise ValueError("corpus is empty: nothing to split")

    previous: Optional[Dict[str, Any]] = None
    if lock_path.exists():
        previous = read_json(lock_path)
        if not refreeze:
            # Growth is allowed; it lands in train. The held-out set does not move.
            missing = [e["id"] for e in previous["holdout"] if e["id"] not in by_id]
            if missing:
                raise ValueError(
                    "frozen held-out cases are no longer in the corpus: %s\n"
                    "the split cannot be honoured; restore them or --refreeze "
                    "(which invalidates every baseline)" % ", ".join(missing[:5])
                )
            return previous

    ids = _stratified(cases, fraction, salt)
    body = {
        "frozen_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fraction": fraction,
        "salt": salt,
        "refrozen_from": (previous or {}).get("manifest_sha256"),
        "n_corpus": len(cases),
        "n_holdout": len(ids),
        "n_train": len(cases) - len(ids),
        "strata": {
            cat: {
                "total": tot,
                "holdout": sum(1 for i in ids if by_id[i]["category"] == cat),
            }
            for cat, tot in sorted(Counter(c["category"] for c in cases).items())
        },
        "holdout": [{"id": i, "sha256": sha256_of(by_id[i])} for i in ids],
    }
    body["manifest_sha256"] = sha256_of(body["holdout"])
    write_json(lock_path, body)
    return body


def load_lock(out_dir: Path) -> Optional[Dict[str, Any]]:
    p = out_dir / LOCK_NAME
    return read_json(p) if p.exists() else None


def verify(corpus_path: Path) -> Tuple[bool, List[str]]:
    """Re-derive the held-out set from the corpus and compare it to the lock."""
    out_dir = corpus_path.parent
    lock = load_lock(out_dir)
    if lock is None:
        return False, ["no %s: the held-out split has never been frozen" % LOCK_NAME]
    by_id = {c["id"]: c for c in read_jsonl(corpus_path)}
    problems: List[str] = []
    for entry in lock["holdout"]:
        case = by_id.get(entry["id"])
        if case is None:
            problems.append("%s: held out, but gone from the corpus" % entry["id"])
        elif sha256_of(case) != entry["sha256"]:
            problems.append("%s: held-out case changed since the freeze" % entry["id"])
    if sha256_of(lock["holdout"]) != lock.get("manifest_sha256"):
        problems.append("lock manifest hash does not match its own holdout list")
    return (not problems), problems


def materialize(corpus_path: Path) -> Dict[str, int]:
    """Write train.jsonl / holdout.jsonl as views. The lock stays the authority."""
    out_dir = corpus_path.parent
    lock = load_lock(out_dir)
    if lock is None:
        raise ValueError("split is not frozen; run `nt split` first")
    held = {e["id"] for e in lock["holdout"]}
    cases = read_jsonl(corpus_path)
    train = [c for c in cases if c["id"] not in held]
    holdout = [c for c in cases if c["id"] in held]
    write_jsonl(out_dir / "train.jsonl", train)
    write_jsonl(out_dir / "holdout.jsonl", holdout)
    return {"train": len(train), "holdout": len(holdout)}


# --- cross-split leakage: an id is not an independence proof ------------------

#: How similar a train prompt may be to a held-out one before the train case is
#: treated as the same question. Fixed here rather than passed in, so it cannot
#: be tuned once someone has seen what it costs them.
#:
#: 0.85 is conservative on purpose. The asymmetry is the whole argument: a train
#: case wrongly dropped costs a few training examples, and a train case wrongly
#: kept costs the defensibility of the final number. Measured on 2026-09-12 it
#: drops 58 of 175 train cases.
SIMILARITY_CEILING = 0.85


def cross_split_leaks(
    train: List[Dict[str, Any]],
    holdout: List[Dict[str, Any]],
    *,
    ceiling: float = SIMILARITY_CEILING,
) -> Dict[str, Any]:
    """Train cases that are the same question as a held-out one, by three tests.

    DISJOINT IDS BUY NOTHING HERE, and this is the defect that would have voided
    the whole comparison. The corpus is capture-derived: the same agent, hitting
    the same wall twice in one session, produces two entries that differ in a
    variable name. `freeze` splits those by id, so one lands in train and its
    twin in held-out, and a model trained on the first has seen the second.

    Measured on 2026-09-12 over the frozen split: **27 of 74 held-out cases have
    a train neighbour at >= 0.85 prompt similarity**, 11 at >= 0.95, and 11 train
    cases carry an expected stdout byte-identical to a held-out case's. Three of
    the 154 rows in the first SFT set were verified solutions to two held-out
    cases.

    Three tests, because each catches a shape the others miss: prompt
    similarity finds the reworded twin, an identical `expect_stdout` finds the
    same task under a different prompt, and an identical negative program finds
    the same capture harvested twice.

    The held-out set is NOT re-cut — the lock is the integrity guarantee and
    re-cutting it after seeing a baseline is how a split gets chosen for its
    score. The training side shrinks instead.
    """
    import difflib

    held_prompts = [(c["id"], c.get("prompt") or "") for c in holdout]
    held_stdout = {
        (c.get("test") or {}).get("expect_stdout"): c["id"]
        for c in holdout
        if (c.get("test") or {}).get("expect_stdout")
    }
    held_programs: Dict[str, str] = {}
    for c in holdout:
        for neg in c.get("negatives") or []:
            held_programs.setdefault(neg["program"].strip(), c["id"])

    leaks: List[Dict[str, Any]] = []
    for case in train:
        why: List[str] = []
        twin = ""
        prompt = case.get("prompt") or ""
        best, best_id = 0.0, ""
        for hid, hp in held_prompts:
            ratio = difflib.SequenceMatcher(None, prompt, hp).ratio()
            if ratio > best:
                best, best_id = ratio, hid
        if best >= ceiling:
            why.append("prompt %.3f" % best)
            twin = best_id
        want = (case.get("test") or {}).get("expect_stdout")
        if want and want in held_stdout:
            why.append("identical expect_stdout")
            twin = twin or held_stdout[want]
        for neg in case.get("negatives") or []:
            if neg["program"].strip() in held_programs:
                why.append("identical program")
                twin = twin or held_programs[neg["program"].strip()]
                break
        if why:
            leaks.append({"id": case["id"], "twin": twin, "why": ", ".join(why),
                          "similarity": round(best, 4)})
    return {
        "ceiling": ceiling,
        "n_train": len(train),
        "n_holdout": len(holdout),
        "leaks": leaks,
        "clean": [c["id"] for c in train if c["id"] not in {r["id"] for r in leaks}],
    }
