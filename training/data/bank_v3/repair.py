"""Rewrite refused programs into the supported subset, and prove each rewrite.

This is the `ORCHESTRATION.md` step-6 repair: "Supply a complete implementation,
not an unsupported-construct deletion." A rule here replaces a refused library
call with an equivalent written in the subset the engine serves. Deleting the
construct, or quietly changing what the program computes, is the failure mode
these rules are shaped to avoid.

NOTHING IS TRUSTED. A repair is accepted only when both hold:

  1. the engine serves it natively on every input, and
  2. it reproduces the ALREADY-AGREED expected output byte for byte.

Condition 2 is the one that matters. The expected output came from independent
samples agreeing in `triage.py`, before any repair existed, so a rewrite cannot
move the target it is measured against. A rule that changes behaviour fails here
and the row stays in the queue; it is never nudged through.

WHAT SURVIVES THE QUEUE IS EVIDENCE TOO. A refusal kind with no rule, or whose
rule keeps failing, is a capability request for the engine — the
`engine-addressable` bucket `levers` ranks — not a case to discard. Unrepaired
rows are written out with their kind so the next round can read what the subset
actually costs.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

# --------------------------------------------------------------------------
# The rules. Each is (name, applies-to-kind predicate, source transform).
# Transforms are deliberately narrow: a rule that fires on a shape it does not
# understand will fail verification, which is cheap, but a rule that fires
# broadly and happens to verify on four inputs is how a silent behaviour change
# gets into a bank.
# --------------------------------------------------------------------------

PRELUDE = {
    "median": (
        "def _median(xs):\n"
        "    s = sorted(xs)\n"
        "    n = len(s)\n"
        "    if n % 2:\n"
        "        return s[n // 2]\n"
        "    return (s[n // 2 - 1] + s[n // 2]) / 2\n"),
    "mean": (
        "def _mean(xs):\n"
        "    return sum(xs) / len(xs)\n"),
    "reduce": (
        "def _reduce(f, xs, *rest):\n"
        "    it = list(xs)\n"
        "    if rest:\n"
        "        acc = rest[0]\n"
        "    else:\n"
        "        acc = it.pop(0)\n"
        "    for x in it:\n"
        "        acc = f(acc, x)\n"
        "    return acc\n"),
    "chain": (
        "def _chain(*groups):\n"
        "    out = []\n"
        "    for g in groups:\n"
        "        out.extend(list(g))\n"
        "    return out\n"),
    "bisect_left": (
        "def _bisect_left(a, x):\n"
        "    lo, hi = 0, len(a)\n"
        "    while lo < hi:\n"
        "        mid = (lo + hi) // 2\n"
        "        if a[mid] < x:\n"
        "            lo = mid + 1\n"
        "        else:\n"
        "            hi = mid\n"
        "    return lo\n"),
}


def _drop_import(src, module):
    """Remove `import m`, `import m as x` and `from m import ...` lines."""
    out = []
    for line in src.split("\n"):
        stripped = line.strip()
        if re.match(r"^from\s+%s\s+import\s" % re.escape(module), stripped):
            continue
        # The multi-import case FIRST. `import statistics, sys` must lose only
        # `statistics`: matching the plain form first would drop the whole line
        # and take `sys` with it, which fails verification later as a puzzling
        # cpython-mismatch rather than as the import bug it is.
        m = re.match(r"^import\s+(.+)$", stripped)
        if m:
            names = [p.strip() for p in m.group(1).split(",")]
            if module in [n.split(" as ")[0].strip() for n in names]:
                keep = [n for n in names if n.split(" as ")[0].strip() != module]
                indent = " " * (len(line) - len(line.lstrip()))
                if keep:
                    out.append(indent + "import " + ", ".join(keep))
                continue
        out.append(line)
    return "\n".join(out)


def _sub_calls(src, module, attr, replacement):
    """`mod.attr(` and a bare `attr(` imported from `mod` become `replacement(`."""
    src = re.sub(r"\b%s\.%s\b" % (re.escape(module), re.escape(attr)), replacement, src)
    if re.search(r"^from\s+%s\s+import\b[^\n]*\b%s\b" % (re.escape(module), re.escape(attr)),
                 src, re.M):
        src = re.sub(r"\b%s\b(?=\s*\()" % re.escape(attr), replacement, src)
    return src


def rule_statistics(src):
    if "statistics" not in src:
        return None
    body, need = src, []
    for attr, helper in (("median", "_median"), ("fmean", "_mean"), ("mean", "_mean")):
        if re.search(r"\b(statistics\.%s|^from\s+statistics\s+import[^\n]*\b%s\b)" % (attr, attr),
                     body, re.M):
            body = _sub_calls(body, "statistics", attr, helper)
            need.append("median" if helper == "_median" else "mean")
    if not need:
        return None
    return "".join(PRELUDE[n] for n in dict.fromkeys(need)) + _drop_import(body, "statistics")


def rule_functools(src):
    if "functools" not in src or "reduce" not in src:
        return None
    return PRELUDE["reduce"] + _drop_import(
        _sub_calls(src, "functools", "reduce", "_reduce"), "functools")


def rule_itertools(src):
    if "itertools" not in src or "chain" not in src:
        return None
    return PRELUDE["chain"] + _drop_import(
        _sub_calls(src, "itertools", "chain", "_chain"), "itertools")


def rule_operator(src):
    if "operator" not in src:
        return None
    body = src
    for attr, lam in (("add", "(lambda a, b: a + b)"), ("mul", "(lambda a, b: a * b)"),
                      ("sub", "(lambda a, b: a - b)"), ("itemgetter", None)):
        if lam and re.search(r"\boperator\.%s\b" % attr, body):
            body = re.sub(r"\boperator\.%s\b" % attr, lam, body)
    if body == src:
        return None
    return _drop_import(body, "operator")


def rule_copy(src):
    if "copy" not in src:
        return None
    # A deep copy of JSON-ish data is a slice for a list; anything nested is out
    # of scope and must fail verification rather than be guessed at.
    body = re.sub(r"\bcopy\.deepcopy\(([^()]+)\)", r"list(\1)", src)
    body = re.sub(r"\bcopy\.copy\(([^()]+)\)", r"list(\1)", body)
    if body == src:
        return None
    return _drop_import(body, "copy")


def rule_heapq(src):
    if "heapq" not in src:
        return None
    body = re.sub(r"\bheapq\.nsmallest\(\s*([^,]+),\s*([^()]+)\)", r"sorted(\2)[:\1]", src)
    body = re.sub(r"\bheapq\.nlargest\(\s*([^,]+),\s*([^()]+)\)",
                  r"sorted(\2, reverse=True)[:\1]", body)
    if body == src:
        return None
    return _drop_import(body, "heapq")


def rule_bisect(src):
    if "bisect" not in src:
        return None
    body = _sub_calls(src, "bisect", "bisect_left", "_bisect_left")
    body = re.sub(r"\bbisect\.bisect\b(?=\s*\()", "_bisect_left", body)
    if body == src:
        return None
    return PRELUDE["bisect_left"] + _drop_import(body, "bisect")


def rule_array(src):
    if "array" not in src:
        return None
    body = re.sub(r"\barray\.array\(\s*['\"][a-zA-Z]['\"]\s*,\s*([^()]+)\)", r"list(\1)", src)
    if body == src:
        return None
    return _drop_import(body, "array")


def rule_string_constants(src):
    if "string" not in src:
        return None
    consts = {
        "string.ascii_lowercase": "'abcdefghijklmnopqrstuvwxyz'",
        "string.ascii_uppercase": "'ABCDEFGHIJKLMNOPQRSTUVWXYZ'",
        "string.ascii_letters": "'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'",
        "string.digits": "'0123456789'",
        "string.punctuation": repr("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"),
        "string.whitespace": repr(" \t\n\r\x0b\x0c"),
    }
    body = src
    for name, literal in consts.items():
        body = body.replace(name, literal)
    if body == src:
        return None
    return _drop_import(body, "string")


def rule_decimal_fractions(src):
    """`Decimal(x)` / `Fraction(x)` around integer work is the integer itself."""
    body = src
    for mod, cls in (("decimal", "Decimal"), ("fractions", "Fraction")):
        if mod not in body:
            continue
        body = re.sub(r"\b%s\.%s\(\s*([^(),]+)\s*\)" % (mod, cls), r"\1", body)
        body = re.sub(r"\b%s\(\s*([^(),]+)\s*\)" % cls, r"\1", body)
        body = _drop_import(body, mod)
    if body == src:
        return None
    return body


RULES = [
    ("statistics", rule_statistics), ("functools", rule_functools),
    ("itertools", rule_itertools), ("operator", rule_operator),
    ("copy", rule_copy), ("heapq", rule_heapq), ("bisect", rule_bisect),
    ("array", rule_array), ("string", rule_string_constants),
    ("decimal/fractions", rule_decimal_fractions),
]


def run(binary, program, spec, workdir):
    for name, text in (spec.get("files") or {}).items():
        if "/" in name or name.startswith("."):
            return None
        (workdir / name).write_text(text, encoding="utf-8")
    try:
        return subprocess.run(
            [binary, "-I", "-c", program] + list(spec.get("argv") or []),
            input=(spec.get("stdin") or ""), capture_output=True, text=True,
            cwd=str(workdir), timeout=10.0)
    except (subprocess.TimeoutExpired, OSError, ValueError, UnicodeError):
        return None


def verify(python, engine, program, row, workdir):
    """Native on every input AND byte-identical to the pre-agreed expected output."""
    for spec, want in zip(row["inputs"], row["expected"]):
        for p in workdir.iterdir():
            p.unlink()
        got = run(python, program, spec, workdir)
        if got is None or got.returncode != 0 or got.stderr.strip() or got.stdout != want:
            return False, "cpython-mismatch"
        for p in workdir.iterdir():
            p.unlink()
        nat = run(engine, program, spec, workdir)
        if nat is None or nat.returncode != 0:
            return False, "still-refused"
        if nat.stdout != want:
            return False, "engine-mismatch"
    return True, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", required=True, type=Path)
    ap.add_argument("--engine", required=True)
    ap.add_argument("--out-repaired", required=True, type=Path)
    ap.add_argument("--out-unrepaired", required=True, type=Path)
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()

    if not Path(args.engine).is_file():
        print("not a file: %s" % args.engine, file=sys.stderr)
        return 2
    rows = [json.loads(l) for l in args.queue.read_text(encoding="utf-8").splitlines() if l]
    if not rows:
        print("empty repair queue: %s" % args.queue, file=sys.stderr)
        return 1

    repaired, unrepaired = [], []
    fired, accepted, why = Counter(), Counter(), Counter()
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for row in rows:
            done = False
            for name, rule in RULES:
                candidate = rule(row["program"])
                if candidate is None or candidate == row["program"]:
                    continue
                fired[name] += 1
                ok, reason = verify(args.python, args.engine, candidate, row, workdir)
                if not ok:
                    why["%s:%s" % (name, reason)] += 1
                    continue
                accepted[name] += 1
                repaired.append({**row, "program": candidate, "original": row["program"],
                                 "repair_rule": name})
                done = True
                break
            if not done:
                unrepaired.append(row)

    for path, rows_out in ((args.out_repaired, repaired), (args.out_unrepaired, unrepaired)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows_out),
                        encoding="utf-8")

    still = Counter(k for r in unrepaired for k in r.get("refusals", []))
    print(json.dumps({
        "queue": len(rows), "repaired": len(repaired), "unrepaired": len(unrepaired),
        "rules_fired": dict(fired), "rules_accepted": dict(accepted),
        "rejected_by_verification": dict(why),
        # This is the capability request, and the useful half of a failure.
        "unserved_kinds": still.most_common(20),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
