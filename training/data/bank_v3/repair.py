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
    # CPython's own heap algorithm, reimplemented in the subset. `nsmallest` is
    # a one-line substitution but `heappush`/`heappop` are a discipline, and a
    # program that balances two heaps to track a running median cannot be
    # repaired by deleting an import. Sift order is copied from CPython's
    # `_siftdown`/`_siftup` rather than improvised, because a different-but-
    # valid heap pops equal elements in a different order, and that is
    # observable the moment the values carry payloads.
    "heap": (
        "def _siftdown(h, start, pos):\n"
        "    item = h[pos]\n"
        "    while pos > start:\n"
        "        parent = (pos - 1) >> 1\n"
        "        if item < h[parent]:\n"
        "            h[pos] = h[parent]\n"
        "            pos = parent\n"
        "            continue\n"
        "        break\n"
        "    h[pos] = item\n"
        "def _siftup(h, pos):\n"
        "    endpos = len(h)\n"
        "    startpos = pos\n"
        "    item = h[pos]\n"
        "    child = 2 * pos + 1\n"
        "    while child < endpos:\n"
        "        right = child + 1\n"
        "        if right < endpos and not h[child] < h[right]:\n"
        "            child = right\n"
        "        h[pos] = h[child]\n"
        "        pos = child\n"
        "        child = 2 * pos + 1\n"
        "    h[pos] = item\n"
        "    _siftdown(h, startpos, pos)\n"
        "def _heappush(h, item):\n"
        "    h.append(item)\n"
        "    _siftdown(h, 0, len(h) - 1)\n"
        "def _heappop(h):\n"
        "    last = h.pop()\n"
        "    if h:\n"
        "        top = h[0]\n"
        "        h[0] = last\n"
        "        _siftup(h, 0)\n"
        "        return top\n"
        "    return last\n"
        "def _heapify(x):\n"
        "    n = len(x)\n"
        "    for i in reversed(range(n // 2)):\n"
        "        _siftup(x, i)\n"
        "def _heapreplace(h, item):\n"
        "    top = h[0]\n"
        "    h[0] = item\n"
        "    _siftup(h, 0)\n"
        "    return top\n"
        "def _heappushpop(h, item):\n"
        "    if h and h[0] < item:\n"
        "        item, h[0] = h[0], item\n"
        "        _siftup(h, 0)\n"
        "    return item\n"),
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
    need_heap = False
    for attr in ("heappushpop", "heapreplace", "heappush", "heappop", "heapify"):
        if re.search(r"\b(heapq\.%s|^from\s+heapq\s+import[^\n]*\b%s\b)" % (attr, attr),
                     body, re.M):
            body = _sub_calls(body, "heapq", attr, "_" + attr)
            need_heap = True
    if body == src:
        return None
    prelude = PRELUDE["heap"] if need_heap else ""
    return prelude + _drop_import(body, "heapq")


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


def rule_decimal(src):
    """`Decimal(x)` around INTEGER work is the integer itself — and only then.

    Withdrawn for `Fraction` on 2026-09-18 after firing 15 times and being
    accepted 0 times (GH run 35320962503). Unwrapping a Fraction is wrong twice
    over: `Fraction(1, 3)` takes two arguments the pattern never matched, and
    where it did match, `Fraction(a) / Fraction(b)` prints `1/3` while `a / b`
    prints `0.333...`. Decimal survives only because the cases that reach it are
    integer sums, where `Decimal(n)` really is `n`; a Decimal doing division or
    quantisation is the same trap and is left to fail verification.
    """
    if "decimal" not in src or "/" in src:
        return None
    body = re.sub(r"\bdecimal\.Decimal\(\s*([^(),]+)\s*\)", r"\1", src)
    body = re.sub(r"\bDecimal\(\s*([^(),]+)\s*\)", r"\1", body)
    if body == src:
        return None
    return _drop_import(body, "decimal")


def _withdrawn_rule_fraction_exact(src):
    """Exact rationals as a numerator/denominator pair reduced by gcd.

    `Fraction` is not unwrappable — it prints `1/3`, not `0.333...` — so the
    repair has to carry the arithmetic, not delete it. Narrow on purpose: only
    a Fraction built from one or two integer arguments, because that is the
    shape whose behaviour a pair of ints reproduces exactly.
    """
    if "Fraction" not in src:
        return None
    body = _sub_calls(src, "fractions", "Fraction", "_Fraction")
    body = re.sub(r"(?<![\w.])Fraction\b(?=\s*\()", "_Fraction", body)
    if body == src:
        return None
    return PRELUDE["fraction"] + _drop_import(body, "fractions")


RULES = [
    ("statistics", rule_statistics), ("functools", rule_functools),
    ("itertools", rule_itertools), ("operator", rule_operator),
    ("copy", rule_copy), ("heapq", rule_heapq), ("bisect", rule_bisect),
    ("array", rule_array), ("string", rule_string_constants),
    ("decimal", rule_decimal),
    # `fraction` withdrawn 2026-09-18: the shim was a class, and the engine
    # refuses `class` outright (`class: class definition`), so the repair could
    # never be native. Fractions move to the CEILING list in cerebras_gen.py —
    # keeping the import is the right answer, which is what a control is for.
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
