"""Temporal generalization — does the profile hold as the corpus moves?

The honest limitation first: this tree's capability tables were revised WHILE
the corpus grew, so no clean pre-registered holdout exists in this data. What
can be measured, and is reported here, is two things:

  (a) SHAPE STABILITY — is the feature distribution the same in early and late
      capture, or is the profile an artifact of one week's tasks?
  (b) ADMISSION BY COHORT — does tier 1 admit late-captured programs at the
      rate it admits early ones, or only the ones it was tuned on?

A genuine prospective holdout is a standing experiment, not a table: capture
continues, and `--after DATE` re-runs (a) and (b) on programs first seen after
a date that predates the current tables.
"""
from __future__ import annotations

import ast
import collections
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from lypning import corpus, conformance as conf  # noqa: E402

ENV = dict(os.environ)
ENV.update({"PYTHONHASHSEED": "0", "LC_ALL": "C.UTF-8", "LYPNING_CAPTURE": "0"})
FEATURES = {"loop": {"For", "While"}, "comprehension":
            {"ListComp", "SetComp", "DictComp", "GeneratorExp"},
            "function-def": {"FunctionDef"}, "try-except": {"Try"},
            "lambda": {"Lambda"}, "fstring": {"JoinedStr"}, "with": {"With"},
            "class-def": {"ClassDef"}}


def shape(programs):
    """The profile's shape: feature presence and top imports, as fractions."""
    feat = collections.Counter()
    imports = collections.Counter()
    sizes = []
    n = 0
    for src in programs:
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        n += 1
        sizes.append(len(src.encode("utf-8", "replace")))
        seen = {type(x).__name__ for x in ast.walk(tree)}
        for k, names in FEATURES.items():
            if names & seen:
                feat[k] += 1
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imports[a.name.split(".")[0]] += 1
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports[node.module.split(".")[0]] += 1
    if not n:
        return None
    sizes.sort()
    return {"n": n, "median_bytes": sizes[len(sizes) // 2],
            "features_pct": {k: round(100.0 * feat[k] / n, 1) for k in FEATURES},
            "top_imports": [k for k, _ in imports.most_common(6)]}


def admission(entries):
    """Tier-1 outcome on a cohort: admitted-and-correct, refused, or wrong."""
    c = collections.Counter()
    for e in entries:
        with tempfile.TemporaryDirectory() as cwd:
            env = dict(ENV)
            env["LYPNING_LOG"] = os.path.join(cwd, "c.jsonl")
            try:
                ref = subprocess.run([sys.executable, "-c", e.program], capture_output=True,
                                     text=True, errors="replace", timeout=15, cwd=cwd, env=env)
                got = subprocess.run(["/root/.lypning/bin/lypning", "-c", e.program],
                                     capture_output=True, text=True, errors="replace",
                                     timeout=15, cwd=cwd, env=env)
            except subprocess.TimeoutExpired:
                continue
        if ref.returncode != 0:
            continue                                  # not a clean program
        c["clean"] += 1
        if got.returncode == 90 and "unsupported:" in got.stderr:
            c["refused"] += 1
        elif got.returncode == 0 and got.stdout == ref.stdout:
            c["match"] += 1
        elif got.returncode == 0:
            c["silent-diff"] += 1
        else:
            c["loud-error"] += 1
    return c


def main() -> int:
    after = None
    if "--after" in sys.argv:
        after = sys.argv[sys.argv.index("--after") + 1]
    entries = [e for e in corpus.load_default()
               if not conf.absolute_paths(e.program) and not conf.is_nondeterministic(e)]
    dated = [e for e in entries if (e.first_seen or "")[:10]]
    dated.sort(key=lambda e: e.first_seen)
    sys.stderr.write("dated entries: %d of %d graded candidates\n" % (len(dated), len(entries)))

    if after:
        cohorts = [("after " + after, [e for e in dated if e.first_seen[:10] > after]),
                   ("on/before " + after, [e for e in dated if e.first_seen[:10] <= after])]
    else:                                             # quartiles by capture time
        q = len(dated) // 4
        cohorts = [("Q%d %s..%s" % (i + 1, dated[i * q].first_seen[:10],
                                    dated[min(len(dated) - 1, (i + 1) * q - 1)].first_seen[:10]),
                    dated[i * q:(i + 1) * q if i < 3 else len(dated)]) for i in range(4)]

    out = {"cohorts": []}
    w = sys.stderr.write
    w("\n%-26s %6s %8s %8s %8s %8s %8s\n"
      % ("cohort", "n", "clean", "match", "refused", "wrong", "admit%"))
    for label, es in cohorts:
        if not es:
            continue
        sh = shape([e.program for e in es])
        ad = admission(es)
        clean = ad["clean"] or 1
        rec = {"cohort": label, "n": len(es), "shape": sh, "admission": dict(ad),
               "match_pct": round(100.0 * ad["match"] / clean, 1)}
        out["cohorts"].append(rec)
        w("%-26s %6d %8d %8d %8d %8d %7.1f%%\n"
          % (label, len(es), ad["clean"], ad["match"], ad["refused"],
             ad["silent-diff"] + ad["loud-error"], rec["match_pct"]))
    w("\nshape stability across cohorts (%% of programs with the feature):\n")
    keys = list(FEATURES)
    w("%-26s %10s " % ("cohort", "med bytes") + "".join("%8s" % k[:7] for k in keys) + "\n")
    for rec in out["cohorts"]:
        s = rec["shape"]
        w("%-26s %10d " % (rec["cohort"], s["median_bytes"])
          + "".join("%8.1f" % s["features_pct"][k] for k in keys) + "\n")
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
