"""Diagnostic only (not the registered criterion): G restricted to Codex calls whose cwd is this repository."""
from __future__ import annotations
import collections, random, sys, json
sys.path.insert(0, "/tmp/sim")
import measure as M
from lypning import codex
cl, _ = M.claude_programs()
feats = {}
C = {}; C1 = {}; C2 = {}; C3 = {}
for model, text, _c in cl:
    h = M.sha(text)
    p = M.CLAUDE_POOLS.get(model)
    if p:
        feats.setdefault(h, None)
        {"C1": C1, "C2": C2, "C3": C3}[p][h] = text
C.update(C1); C.update(C2)
G = {}
for p in codex.collect():
    if p.cwd and "lypning" in p.cwd:
        G[M.sha(p.program)] = p.program
for h in set(G) & (set(C) | set(C3)):
    G.pop(h); C.pop(h, None); C1.pop(h, None); C2.pop(h, None); C3.pop(h, None)
F = {h: M.features(t) for d in (C, C3, G) for h, t in d.items()}
out = {"n_G_lypning_cwd": len(G), "tier_a_G": sum(F[h]["verdict"] == "" for h in G)}
sel = {k: [F[h] for h in d] for k, d in (("C", C), ("C1", C1), ("C2", C2), ("C3", C3), ("G", G))}
universe = sel["C1"] + sel["C2"] + sel["C3"] + sel["G"]
for d in ("verdict", "nodes", "stdlib", "constructs"):
    b = M.Binner(d, universe)
    g = M.compare(b, sel["G"], sel["C"], random.Random(7))
    c = M.compare(b, sel["C1"], sel["C2"], random.Random(7))
    out[d] = {"G-C": round(g["jsd"], 4), "ci90": [round(x, 4) for x in g["ci90"]], "null": round(g["perm_null_mean"], 4),
              "C1-C2": round(c["jsd"], 4), "pass": g["jsd"] <= c["jsd"]}
out["verdict_mix_G"] = dict(collections.Counter(F[h]["verdict"] or "tier-A" for h in G).most_common())
print(json.dumps(out, indent=1))
