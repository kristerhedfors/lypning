"""Static profile of the agent corpus — what coding agents actually type.

Parses every captured program and reports the distributions a language
implementer would need to decide what to optimize: size, AST node mix,
imports, builtins, and which constructs appear at all. Parsing only; nothing
is executed here (invariant 4 — these programs edit repositories).

Emits JSON on stdout and a human table on stderr.
"""
from __future__ import annotations

import ast
import collections
import json
import sys

sys.path.insert(0, "src")
from lypning import corpus  # noqa: E402


def _stat(xs):
    xs = sorted(xs)
    n = len(xs)
    def q(p):
        return xs[min(n - 1, int(p * n))]
    return {"n": n, "min": xs[0], "p25": q(.25), "median": q(.5),
            "p75": q(.75), "p90": q(.9), "p99": q(.99), "max": xs[-1],
            "mean": round(sum(xs) / n, 2)}


def main() -> int:
    entries = corpus.load_default()
    nodes = collections.Counter()
    imports = collections.Counter()
    builtins_used = collections.Counter()
    methods = collections.Counter()
    feature = collections.Counter()
    sizes, lines, nodecounts = [], [], []
    unparsed = 0

    for e in entries:
        src = e.program
        sizes.append(len(src.encode("utf-8", "replace")))
        lines.append(src.count("\n") + 1)
        try:
            tree = ast.parse(src)
        except SyntaxError:
            unparsed += 1
            continue
        count = 0
        seen = set()
        for node in ast.walk(tree):
            count += 1
            name = type(node).__name__
            nodes[name] += 1
            seen.add(name)
            if isinstance(node, ast.Import):
                for a in node.names:
                    imports[a.name.split(".")[0]] += 1
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports[node.module.split(".")[0]] += 1
            elif isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name):
                    builtins_used[f.id] += 1
                elif isinstance(f, ast.Attribute):
                    methods[f.attr] += 1
        nodecounts.append(count)
        # Per-program feature presence (not occurrence count).
        for feat, names in (
            ("loop", {"For", "While", "AsyncFor"}),
            ("function-def", {"FunctionDef", "AsyncFunctionDef"}),
            ("class-def", {"ClassDef"}),
            ("comprehension", {"ListComp", "SetComp", "DictComp", "GeneratorExp"}),
            ("try-except", {"Try", "TryStar"}),
            ("with", {"With", "AsyncWith"}),
            ("lambda", {"Lambda"}),
            ("fstring", {"JoinedStr"}),
            ("global-nonlocal", {"Global", "Nonlocal"}),
            ("yield", {"Yield", "YieldFrom"}),
            ("await-async", {"Await", "AsyncFunctionDef"}),
            ("decorator", set()),
            ("walrus", {"NamedExpr"}),
            ("match", {"Match"}),
        ):
            if names & seen:
                feature[feat] += 1
        if any(getattr(n, "decorator_list", None)
               for n in ast.walk(tree)):
            feature["decorator"] += 1

    total = len(entries)
    parsed = total - unparsed
    out = {
        "corpus_entries": total,
        "parsed": parsed,
        "syntax_error": unparsed,
        "bytes": _stat(sizes),
        "lines": _stat(lines),
        "ast_nodes": _stat(nodecounts),
        "top_ast_nodes": nodes.most_common(30),
        "top_imports": imports.most_common(25),
        "top_calls": builtins_used.most_common(30),
        "top_methods": methods.most_common(25),
        "feature_presence": {k: (v, round(100.0 * v / parsed, 1))
                             for k, v in feature.most_common()},
    }
    print(json.dumps(out, indent=1))

    w = sys.stderr.write
    w("corpus %d entries, %d parsed, %d syntax-error\n" % (total, parsed, unparsed))
    w("\nsize: bytes median %d, p90 %d, max %d | lines median %d, p90 %d\n"
      % (out["bytes"]["median"], out["bytes"]["p90"], out["bytes"]["max"],
         out["lines"]["median"], out["lines"]["p90"]))
    w("AST nodes/program: median %d, p90 %d, max %d\n"
      % (out["ast_nodes"]["median"], out["ast_nodes"]["p90"], out["ast_nodes"]["max"]))
    w("\nfeature presence (%% of parsed programs):\n")
    for k, (n, pct) in out["feature_presence"].items():
        w("  %-16s %5d  %5.1f%%\n" % (k, n, pct))
    w("\ntop imports: %s\n" % ", ".join("%s(%d)" % kv for kv in imports.most_common(12)))
    w("top calls:   %s\n" % ", ".join("%s(%d)" % kv for kv in builtins_used.most_common(12)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
