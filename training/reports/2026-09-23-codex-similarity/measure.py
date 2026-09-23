"""Criterion (A) of the pre-registered GPT-vs-Claude material-similarity test.

Static only: nothing captured is executed. Reads ~/.lypning/invocations.jsonl,
~/.lypning/attribution.jsonl (via training/pipeline/capture_export helpers) and
~/.codex/sessions (via lypning.codex.collect), read-only.

Pools
  C1 = claude-opus-5, C2 = claude-fable-5-1, C3 = claude-opus-5-5 (reported
  separately), C = C1 u C2 (deduped), G = every Codex-host program (all GPT
  models pooled), G:<model> per model when n >= 30.

Symmetric processing: every program, Claude or Codex, goes through
harvest.redact and is dropped when a credential-shaped residue remains (this is
what codex.collect already does), then dedupe by sha256 of that text per pool.
A sha present in a Claude pool (C1/C2/C3) AND in G is dropped from both.

Distributions (fixed bins; bins derived from the union of all pools in the set,
never from one pool):
  verdict    capture_quality first-failing rule, "" = tier A (ALL set only)
  nodes      AST node count decile bin (parseable programs)
  stdlib     incidence of stdlib import roots; roots with pooled incidence < 5
             fold into <other>; programs with no stdlib import add one <none>
  constructs incidence of the 12 constructs; a program with none adds <none>
JSD base 2. Bootstrap: 1,000 resamples of programs within each pool, seed 7,
90% percentile interval. PASS per distribution iff point JSD(G, C) <= point
JSD(C1, C2). Diagnostics (not part of the criterion): threshold using the max
over all Claude pairs incl. C3; permutation null means (finite-sample bias).

Outputs /tmp/sim/pools.json (tier-A bodies only) and /tmp/sim/result.json
(aggregates only). Prints aggregates only.
"""

from __future__ import annotations

import ast
import bisect
import collections
import hashlib
import json
import math
import random
import statistics
import sys
import warnings
from pathlib import Path

warnings.simplefilter("ignore")

from lypning import codex, harvest  # noqa: E402
from pipeline import capture_export, capture_quality  # noqa: E402

HOME = Path.home()
LOG = HOME / ".lypning" / "invocations.jsonl"
JOURNAL = HOME / ".lypning" / "attribution.jsonl"
OUT = Path("/tmp/sim")
SEED = 7
B = 1000
CLAUDE_POOLS = {"claude-opus-5": "C1", "claude-fable-5-1": "C2", "claude-opus-5-5": "C3"}
CONSTRUCTS = ("def", "class", "comprehension", "f-string", "lambda", "try", "with",
              "generator", "type-hints", "dataclass", "walrus", "decorator")


def sha(text):
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def sanitize(program):
    """harvest.redact + is_safe, exactly as codex.collect treats a Codex program."""
    text, hits = harvest.redact(program)
    if not text.strip() or not harvest.is_safe(hits):
        return None
    try:
        text.encode("utf-8")
    except UnicodeError:
        return None
    return text


# --- Claude ------------------------------------------------------------------

def claude_programs():
    journal = capture_export.read_journal(JOURNAL)
    indexes = {}
    seen_calls = set()
    out = []  # (model, text, command)
    stats = collections.Counter()
    for n, raw, rec in capture_export._log_lines(LOG):
        if rec is None or rec.get("kind") != "bash_command" or not isinstance(rec.get("command"), str):
            continue
        host = rec.get("host")
        if isinstance(host, str) and host and host != "claude":
            continue
        tid = rec.get("tool_use_id")
        if isinstance(tid, str) and tid:
            if tid in seen_calls:
                continue
            seen_calls.add(tid)
        extracted = harvest.extract_with_tails(rec["command"])
        if not extracted:
            continue
        model, basis, ok = capture_export.attribute(rec, journal, indexes)
        for program, tail in extracted:
            stats["occurrences"] += 1
            text = sanitize(program)
            if text is None:
                stats["dropped_unsafe_or_invalid"] += 1
                continue
            out.append((model, text, rec["command"]))
            stats["model:" + model] += 1
    return out, stats


def codex_programs():
    progs = codex.collect()
    out = []
    stats = collections.Counter()
    for p in progs:
        stats["occurrences"] += 1
        text = p.program
        try:
            text.encode("utf-8")
        except UnicodeError:
            stats["dropped_invalid"] += 1
            continue
        out.append((p.model or "unknown", text, ""))
        stats["model:" + (p.model or "unknown")] += 1
    return out, stats


# --- features ------------------------------------------------------------------

def features(text):
    verdict = capture_quality.classify(text)
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        tree = None
    f = {"verdict": verdict, "lines": len(text.strip("\n").split("\n")),
         "parse": tree is not None, "nodes": 0, "imports": [], "constructs": [],
         "contaminated": capture_export.contamination("", text)}
    if tree is None:
        return f
    f["nodes"] = sum(1 for _ in ast.walk(tree))
    roots = set()
    cons = set()
    dc_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                roots.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and not node.level:
                roots.add(node.module.split(".")[0])
                if node.module == "dataclasses":
                    for a in node.names:
                        if a.name == "dataclass":
                            dc_names.add(a.asname or a.name)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "__import__" and node.args \
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            roots.add(node.args[0].value.split(".")[0])
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            cons.add("def")
            a = node.args
            allargs = a.posonlyargs + a.args + a.kwonlyargs + [x for x in (a.vararg, a.kwarg) if x]
            if node.returns is not None or any(x.annotation is not None for x in allargs):
                cons.add("type-hints")
        if isinstance(node, ast.ClassDef):
            cons.add("class")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.decorator_list:
            cons.add("decorator")
            for d in node.decorator_list:
                target = d.func if isinstance(d, ast.Call) else d
                name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
                if name == "dataclass" or name in dc_names:
                    cons.add("dataclass")
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            cons.add("comprehension")
        if isinstance(node, ast.JoinedStr):
            cons.add("f-string")
        if isinstance(node, ast.Lambda):
            cons.add("lambda")
        if isinstance(node, ast.Try) or type(node).__name__ == "TryStar":
            cons.add("try")
        if isinstance(node, (ast.With, ast.AsyncWith)):
            cons.add("with")
        if isinstance(node, (ast.Yield, ast.YieldFrom)):
            cons.add("generator")
        if isinstance(node, ast.AnnAssign):
            cons.add("type-hints")
        if isinstance(node, ast.NamedExpr):
            cons.add("walrus")
    roots.discard("__future__")
    f["imports"] = sorted(roots)
    f["stdlib"] = sorted(r for r in roots if capture_quality.is_stdlib(r))
    f["nonstd"] = sorted(r for r in roots if not capture_quality.is_stdlib(r))
    f["constructs"] = sorted(cons)
    return f


# --- histograms and JSD ------------------------------------------------------------

def jsd(p, q):
    keys = set(p) | set(q)
    sp, sq = float(sum(p.values())), float(sum(q.values()))
    if sp == 0 or sq == 0:
        return float("nan")
    out = 0.0
    for k in keys:
        a, b = p.get(k, 0) / sp, q.get(k, 0) / sq
        m = (a + b) / 2
        if a > 0:
            out += 0.5 * a * math.log2(a / m)
        if b > 0:
            out += 0.5 * b * math.log2(b / m)
    return max(out, 0.0)


class Binner:
    def __init__(self, dist, universe):
        self.dist = dist
        if dist == "nodes":
            xs = sorted(f["nodes"] for f in universe if f["parse"])
            edges = sorted(set(xs[int(len(xs) * i / 10)] for i in range(1, 10))) if xs else []
            self.edges = edges
        if dist == "stdlib":
            c = collections.Counter(m for f in universe if f["parse"] for m in f["stdlib"])
            self.keep = {m for m, k in c.items() if k >= 5}

    def items(self, f):
        d = self.dist
        if d == "verdict":
            return [f["verdict"] or "tier-A"]
        if not f["parse"]:
            return []
        if d == "nodes":
            return ["d%d" % bisect.bisect_right(self.edges, f["nodes"])]
        if d == "stdlib":
            if not f["stdlib"]:
                return ["<none>"]
            return [m if m in self.keep else "<other>" for m in f["stdlib"]]
        if d == "constructs":
            return list(f["constructs"]) or ["<none>"]
        raise ValueError(d)


def hist(binned, idx):
    c = collections.Counter()
    for i in idx:
        c.update(binned[i])
    return c


def pct(xs, q):
    xs = sorted(x for x in xs if not math.isnan(x))
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * q
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def compare(binner, pa, pb, rng, boot=B, perm=B):
    ba = [binner.items(f) for f in pa]
    bb = [binner.items(f) for f in pb]
    na, nb = len(ba), len(bb)
    point = jsd(hist(ba, range(na)), hist(bb, range(nb)))
    bs = []
    for _ in range(boot):
        ia = [rng.randrange(na) for _ in range(na)]
        ib = [rng.randrange(nb) for _ in range(nb)]
        bs.append(jsd(hist(ba, ia), hist(bb, ib)))
    allb = ba + bb
    ps = []
    order = list(range(na + nb))
    for _ in range(perm):
        rng.shuffle(order)
        ps.append(jsd(hist(allb, order[:na]), hist(allb, order[na:])))
    return {"jsd": point, "ci90": [pct(bs, 0.05), pct(bs, 0.95)],
            "perm_null_mean": statistics.fmean(ps),
            "perm_p": (1 + sum(1 for x in ps if x >= point)) / (perm + 1),
            "n": [na, nb]}


# --- main ---------------------------------------------------------------------------

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cl, cl_stats = claude_programs()
    cx, cx_stats = codex_programs()

    feats = {}

    def feat(text):
        h = sha(text)
        if h not in feats:
            feats[h] = features(text)
        return h

    pools = collections.defaultdict(dict)   # pool -> sha -> text
    models = collections.defaultdict(dict)  # pool -> sha -> model
    claude_any = set()
    for model, text, command in cl:
        h = feat(text)
        claude_any.add(h)
        p = CLAUDE_POOLS.get(model)
        if p:
            pools[p][h] = text
            models[p][h] = model
    for model, text, _ in cx:
        h = feat(text)
        pools["G"][h] = text
        models["G"][h] = model
        pools["G:" + model][h] = text
        models["G:" + model][h] = model

    claude_pool_shas = set(pools["C1"]) | set(pools["C2"]) | set(pools["C3"])
    overlap = claude_pool_shas & set(pools["G"])
    overlap_any_claude = claude_any & set(pools["G"])
    for p in list(pools):
        for h in overlap:
            pools[p].pop(h, None)
    pools["C"] = dict(pools["C1"])
    pools["C"].update(pools["C2"])
    for h in pools["C"]:
        models["C"][h] = models["C1"].get(h) or models["C2"].get(h)

    c1c2 = set(pools["C1"]) & set(pools["C2"])
    c_pairs = {"C1&C2": len(c1c2), "C1&C3": len(set(pools["C1"]) & set(pools["C3"])),
               "C2&C3": len(set(pools["C2"]) & set(pools["C3"]))}

    per_model_g = [p for p in pools if p.startswith("G:") and len(pools[p]) >= 30]
    main_pools = ["C1", "C2", "C3", "C", "G"] + sorted(per_model_g)

    def summary(p, shas):
        fs = [feats[h] for h in shas]
        n = len(fs)
        ta = [f for f in fs if f["verdict"] == ""]
        parse = [f for f in fs if f["parse"]]
        return {
            "n": n,
            "tier_a_n": len(ta),
            "tier_a_rate": round(len(ta) / n, 4) if n else None,
            "median_lines": statistics.median([f["lines"] for f in fs]) if fs else None,
            "median_nodes_parseable": statistics.median([f["nodes"] for f in parse]) if parse else None,
            "share_parseable": round(len(parse) / n, 4) if n else None,
            "share_with_def": round(sum("def" in f["constructs"] for f in fs) / n, 4) if n else None,
            "share_only_stdlib_imports": round(sum(1 for f in parse if not f["nonstd"]) / n, 4) if n else None,
            "share_no_imports": round(sum(1 for f in parse if not f["imports"]) / n, 4) if n else None,
            "share_contaminated": round(sum(1 for f in fs if f["contaminated"]) / n, 4) if n else None,
            "tier_a_contaminated": sum(1 for f in ta if f["contaminated"]),
            "tier_a_median_lines": statistics.median([f["lines"] for f in ta]) if ta else None,
            "tier_a_share_with_def": round(sum("def" in f["constructs"] for f in ta) / len(ta), 4) if ta else None,
            "verdict_mix": dict(collections.Counter(f["verdict"] or "tier-A" for f in fs).most_common()),
            "construct_prevalence": {c: round(sum(c in f["constructs"] for f in fs) / n, 4) for c in CONSTRUCTS} if n else {},
            "models": dict(collections.Counter(models[p][h] for h in shas).most_common()),
        }

    result = {
        "date": "2026-09-23",
        "inputs": {"claude_log_occurrences": cl_stats["occurrences"],
                   "claude_dropped_unsafe_or_invalid": cl_stats["dropped_unsafe_or_invalid"],
                   "claude_models_occurrences": {k[6:]: v for k, v in cl_stats.items() if k.startswith("model:")},
                   "codex_occurrences": cx_stats["occurrences"],
                   "codex_models_occurrences": {k[6:]: v for k, v in cx_stats.items() if k.startswith("model:")}},
        "overlap_dropped_claude_pools_vs_G": len(overlap),
        "overlap_any_claude_row_vs_G": len(overlap_any_claude),
        "claude_pool_overlaps_kept": c_pairs,
        "pools": {p: summary(p, list(pools[p])) for p in main_pools},
        "sets": {},
    }

    dists = ("verdict", "nodes", "stdlib", "constructs")
    for setname in ("all", "tierA"):
        sel = {p: [feats[h] for h in pools[p] if setname == "all" or feats[h]["verdict"] == ""]
               for p in main_pools}
        universe = sel["C1"] + sel["C2"] + sel["C3"] + sel["G"]
        res = {}
        for d in dists:
            if d == "verdict" and setname == "tierA":
                res[d] = {"note": "degenerate: every tier-A verdict is ''"}
                continue
            binner = Binner(d, universe)
            rng = random.Random(SEED)
            gc = compare(binner, sel["G"], sel["C"], rng)
            rng = random.Random(SEED)
            c12 = compare(binner, sel["C1"], sel["C2"], rng)
            extra = {}
            for a, b in (("C1", "C3"), ("C2", "C3")):
                if len(sel["C3"]) >= 30:
                    rng = random.Random(SEED)
                    extra[a + "-" + b] = compare(binner, sel[a], sel[b], rng, perm=200)
            per_model = {}
            for p in per_model_g:
                if len(sel[p]) >= 30:
                    rng = random.Random(SEED)
                    per_model[p + "-C"] = compare(binner, sel[p], sel["C"], rng, perm=200)
            max_claude = max([c12["jsd"]] + [v["jsd"] for v in extra.values()])
            res[d] = {
                "G-C": gc, "C1-C2": c12, "claude_pairs_with_C3": extra,
                "per_gpt_model_vs_C": per_model,
                "threshold_C1C2": c12["jsd"],
                "pass": gc["jsd"] <= c12["jsd"],
                "sensitivity_threshold_max_claude_pairs": max_claude,
                "sensitivity_pass_max_claude_pairs": gc["jsd"] <= max_claude,
                "bins": len(set(x for f in universe for x in binner.items(f))),
            }
        res["overall_pass"] = all(v["pass"] for v in res.values() if isinstance(v, dict) and "pass" in v)
        result["sets"][setname] = res
    result["A_pass"] = result["sets"]["all"]["overall_pass"] and result["sets"]["tierA"]["overall_pass"]

    tier_a = {}
    for p in ["C1", "C2", "C3", "G"] + sorted(per_model_g):
        tier_a[p] = [{"sha": h, "program": pools[p][h], "verdict": feats[h]["verdict"],
                      "model": models[p][h], "contaminated": feats[h]["contaminated"]}
                     for h in sorted(pools[p]) if feats[h]["verdict"] == ""]
    fd_path = OUT / "pools.json"
    import os
    fd = os.open(str(fd_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(tier_a, fh, ensure_ascii=False)
    (OUT / "result.json").write_text(json.dumps(result, indent=1, sort_keys=True))
    print(json.dumps(result, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
