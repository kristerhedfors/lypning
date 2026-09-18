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
    "cal_isleap": (
        "def _cal_isleap(y):\n"
        "    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)\n"),
    "cal_monthrange": (
        "_CAL_MDAYS = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]\n"
        "def _cal_weekday(y, m, d):\n"
        "    t = [0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4]\n"
        "    yy = y\n"
        "    if m < 3:\n"
        "        yy = yy - 1\n"
        "    return (yy + yy // 4 - yy // 100 + yy // 400 + t[m - 1] + d - 1) % 7\n"
        "def _cal_monthrange(y, m):\n"
        "    if m < 1 or m > 12:\n"
        "        raise ValueError(\"bad month number %r; must be 1-12\" % m)\n"
        "    n = _CAL_MDAYS[m]\n"
        "    if m == 2 and _cal_isleap(y):\n"
        "        n = 29\n"
        "    return (_cal_weekday(y, m, 1), n)\n"),
    "cal_month_name": (
        "_CAL_MONTH_NAME = ['', 'January', 'February', 'March', 'April', 'May', 'June',\n"
        "                   'July', 'August', 'September', 'October', 'November', 'December']\n"),
    "dt_civil": (
        "def _dt_dfc(y, m, d):\n"
        "    yy = y\n"
        "    if m <= 2:\n"
        "        yy = yy - 1\n"
        "    era = yy // 400\n"
        "    yoe = yy - era * 400\n"
        "    if m > 2:\n"
        "        mp = m - 3\n"
        "    else:\n"
        "        mp = m + 9\n"
        "    doy = (153 * mp + 2) // 5 + d - 1\n"
        "    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy\n"
        "    return era * 146097 + doe - 719468\n"
        "def _dt_cfd(z):\n"
        "    zz = z + 719468\n"
        "    era = zz // 146097\n"
        "    doe = zz - era * 146097\n"
        "    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365\n"
        "    y = yoe + era * 400\n"
        "    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)\n"
        "    mp = (5 * doy + 2) // 153\n"
        "    d = doy - (153 * mp + 2) // 5 + 1\n"
        "    if mp < 10:\n"
        "        m = mp + 3\n"
        "    else:\n"
        "        m = mp - 9\n"
        "    if m <= 2:\n"
        "        y = y + 1\n"
        "    return (y, m, d)\n"),
    "dt_date": (
        "_DT_MDAYS = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]\n"
        "def _dt_leap(y):\n"
        "    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)\n"
        "def _dt_date(y, m, d):\n"
        "    if y < 1 or y > 9999:\n"
        "        raise ValueError(\"year %d is out of range\" % y)\n"
        "    if m < 1 or m > 12:\n"
        "        raise ValueError(\"month must be in 1..12\")\n"
        "    n = _DT_MDAYS[m]\n"
        "    if m == 2 and _dt_leap(y):\n"
        "        n = 29\n"
        "    if d < 1 or d > n:\n"
        "        raise ValueError(\"day is out of range for month\")\n"
        "    return _dt_dfc(y, m, d)\n"),
    "dt_fields": (
        "def _dt_year(z):\n"
        "    return _dt_cfd(z)[0]\n"
        "def _dt_month(z):\n"
        "    return _dt_cfd(z)[1]\n"
        "def _dt_day(z):\n"
        "    return _dt_cfd(z)[2]\n"
        "def _dt_yday(z):\n"
        "    return z - _dt_dfc(_dt_cfd(z)[0], 1, 1) + 1\n"),
    "dt_strptime": (
        "def _dt_dig(c):\n"
        "    return '0' <= c <= '9'\n"
        "def _dt_mo_alts(s, i):\n"
        "    out = []\n"
        "    n = len(s)\n"
        "    if i + 2 <= n and s[i] == '1' and '0' <= s[i + 1] <= '2':\n"
        "        out.append((int(s[i:i + 2]), i + 2))\n"
        "    if i + 2 <= n and s[i] == '0' and '1' <= s[i + 1] <= '9':\n"
        "        out.append((int(s[i:i + 2]), i + 2))\n"
        "    if i < n and '1' <= s[i] <= '9':\n"
        "        out.append((int(s[i]), i + 1))\n"
        "    return out\n"
        "def _dt_dy_alts(s, i):\n"
        "    out = []\n"
        "    n = len(s)\n"
        "    if i + 2 <= n and s[i] == '3' and '0' <= s[i + 1] <= '1':\n"
        "        out.append((int(s[i:i + 2]), i + 2))\n"
        "    if i + 2 <= n and '1' <= s[i] <= '2' and _dt_dig(s[i + 1]):\n"
        "        out.append((int(s[i:i + 2]), i + 2))\n"
        "    if i + 2 <= n and s[i] == '0' and '1' <= s[i + 1] <= '9':\n"
        "        out.append((int(s[i:i + 2]), i + 2))\n"
        "    if i < n and '1' <= s[i] <= '9':\n"
        "        out.append((int(s[i]), i + 1))\n"
        "    if i + 2 <= n and s[i] == ' ' and '1' <= s[i + 1] <= '9':\n"
        "        out.append((int(s[i + 1]), i + 2))\n"
        "    return out\n"
        "def _dt_dig4(s):\n"
        "    return _dt_dig(s[0]) and _dt_dig(s[1]) and _dt_dig(s[2]) and _dt_dig(s[3])\n"
        "def _dt_strptime_ymd(s):\n"
        "    n = len(s)\n"
        "    y = 0\n"
        "    mv = 0\n"
        "    dv = 0\n"
        "    ok = False\n"
        "    if n >= 5 and s[4] == '-' and _dt_dig4(s):\n"
        "        y = int(s[0:4])\n"
        "        for cand in _dt_mo_alts(s, 5):\n"
        "            mv = cand[0]\n"
        "            j = cand[1]\n"
        "            if j < n and s[j] == '-':\n"
        "                for cd in _dt_dy_alts(s, j + 1):\n"
        "                    if cd[1] == n:\n"
        "                        dv = cd[0]\n"
        "                        ok = True\n"
        "                        break\n"
        "            if ok:\n"
        "                break\n"
        "    if not ok:\n"
        "        raise ValueError(\"time data %r does not match format '%%Y-%%m-%%d'\" % s)\n"
        "    return _dt_date(y, mv, dv)\n"),
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


def _live(src):
    """Source with `#`-to-end-of-line removed.

    The guards below ask "is there an attribute of this module I do not serve",
    and a comment naming one is not a use. One of the 39 calendar rows mentions
    the module only in `# calendar.timegm returns Unix timestamp, ...`; without
    this the rule refuses a row whose whole repair is deleting one line.
    Measured 2026-09-18 over /tmp/bigart/unrepaired.jsonl (185 rows): without
    stripping the calendar rule scores 38/39, with it 39/39.
    """
    return re.sub(r"#[^\n]*", "", src)


def rule_calendar(src):
    """`monthrange`, `isleap` and `month_name` — the whole of what the rows use.

    Census over the unrepaired queue (185 rows loaded 2026-09-18): 39 refuse on
    `module: import calendar` and on nothing else, all 39 spell it exactly
    `import calendar`, 15 never name the module outside a comment, and the other
    24 make 26 calls — monthrange 22 sites, month_name 2, isleap 2. No weekday,
    monthcalendar, day_name or setfirstweekday, so none of those are served: an
    attribute this rule has not measured sends it back None rather than guess.

    `monthrange` returns BOTH halves of its tuple even though every one of the
    22 sites subscripts `[1]` and throws the weekday away. A shim returning a
    plausible-but-wrong element 0 is exactly the silent behaviour change this
    bank exists to catch, and the true weekday costs nine lines. Verified
    2026-09-18 against CPython 3.12.13 (work/round-02/venv): 119,988
    (year, month) pairs over 1..9999 x 1..12 and a further 96,024 pairs over
    -4000..0 and 10000..14000, 0 mismatches; 3,652,059 dates for the weekday
    half, 0 mismatches; `isleap` over 14,001 years -2000..12000, 0 mismatches.

    Two things are deliberately narrow. An aliased import returns None, because
    `_drop_import` would take the line while the guard, looking for `calendar.`,
    never sees `cal.monthrange` — none of the 39 are aliased, and the wasted
    fire is not worth the reach. And the prelude is emitted only when a
    substitution actually happened, so the 15 dead imports get a one-line
    deletion rather than a preamble they never call. That deletion is not the
    "unsupported-construct deletion" the module docstring forbids: the guard has
    already proved there is no live `calendar.` name left to delete.

    The name tables hard-code English and fail safe. `calendar.month_name` is
    locale-dependent, so under a non-English LC_TIME the CPython leg of verify()
    would produce a name this table does not, and the row would stay in the
    queue rather than land wrong.
    """
    if re.search(r"^\s*(?:from\s+calendar\b|import\s+calendar\s+as\b)", src, re.M):
        return None
    if not re.search(r"^\s*import\s+calendar\s*(?:#[^\n]*)?$", src, re.M):
        return None
    live = _live(src)
    for m in re.finditer(r"\bcalendar\.(\w+)", live):
        if m.group(1) not in ("monthrange", "month_name", "isleap"):
            return None
    body, need = src, []
    for attr, repl, key in (("monthrange", "_cal_monthrange", "cal_monthrange"),
                            ("isleap", "_cal_isleap", "cal_isleap"),
                            ("month_name", "_CAL_MONTH_NAME", "cal_month_name")):
        if re.search(r"\bcalendar\.%s\b" % attr, live):
            body = re.sub(r"\bcalendar\.%s\b" % attr, repl, body)
            need.append(key)
    if "cal_monthrange" in need:
        need.append("cal_isleap")          # _cal_monthrange calls it for February
    order = ("cal_isleap", "cal_monthrange", "cal_month_name")
    return "".join(PRELUDE[k] for k in order if k in need) + _drop_import(body, "calendar")


# The datetime names that withdraw the rule instead of being served. strftime,
# isoformat, fromisoformat, toordinal and isocalendar appear ZERO times across
# all 185 unrepaired rows (measured 2026-09-18), so a blind implementation would
# be bytes in the bank for no accepts — how the `fractions` rule died. The clock
# readers are here for the other reason: no row in this cohort calls one either,
# but the queue is wider than the cohort and a clock makes a repair
# unreproducible by construction, so the guard ships regardless.
_DT_UNSERVED = re.compile(
    r"\b(?:strftime|isoformat|fromisoformat|toordinal|isocalendar|isoweekday"
    r"|weekday|timezone|tzinfo|astimezone|utcoffset|monthcalendar)\b")
_DT_UNSERVED_ATTR = re.compile(
    r"\.(?:now|today|utcnow|fromtimestamp|utcfromtimestamp|combine|hour|minute"
    r"|second|microsecond|seconds|microseconds|total_seconds|resolution)\b")
# A 3-argument call whose arguments are themselves free of calls and commas.
# Anything nested is a shape this rule has not measured and must not rewrite.
_DT_CTOR = r"\(\s*([^(),]+?)\s*,\s*([^(),]+?)\s*,\s*([^(),]+?)\s*\)"


def _dt_carriers(body):
    """The names that hold a date, by assignment, after substitution.

    A date is one int — days since 1970-01-01 — so `d1 < d2` and `(d2 - d1)`
    need no rewrite at all and only the field accessors do. Finding which names
    to rewrite is therefore the whole of the analysis, and it is kept to two
    shapes it can prove: a name assigned from `_dt_date(`/`_dt_strptime_ymd(`,
    and a name assigned `<carrier> + ...`, which is the `start + timedelta`
    form. A carrier this misses costs a fire (the leftover `.month` fails
    verification as an AttributeError); a name it invents cannot arise, because
    neither shape is reachable without a substitution having already happened.
    """
    names = set()
    for _ in range(len(body.split("\n"))):
        grew = False
        for m in re.finditer(r"^[ \t]*([A-Za-z_]\w*)[ \t]*=[ \t]*(.+)$", body, re.M):
            nm, rhs = m.group(1), m.group(2)
            if nm in names:
                continue
            carrier = "_dt_date(" in rhs or "_dt_strptime_ymd(" in rhs
            if not carrier:
                head = re.match(r"^([A-Za-z_]\w*)\s*\+", rhs)
                carrier = bool(head) and head.group(1) in names
            if carrier:
                names.add(nm)
                grew = True
        if not grew:
            break
    return names


def rule_datetime(src):
    """A date as one int: days since 1970-01-01, proleptic Gregorian.

    Census over the unrepaired queue (185 rows loaded 2026-09-18): 31 refuse on
    `module: import datetime`, 9 of them never reference the module (they
    already hand-roll a days_in_month table), and the 22 real users spell it
    strptime 13 rows / 17 sites, a 3-argument date ctor 9, `(a - b).days` 4,
    date comparison 2, `+ timedelta(days=N)` 2, `.timetuple().tm_yday` 2,
    `.month`/`.day` 2, `.year` 1. strftime, isoformat, fromisoformat, toordinal
    and isocalendar: zero, in all 185. Every strptime site uses the one format
    '%Y-%m-%d', and any other format — or a strftime — withdraws the rule.

    THE INT IS THE POINT. A tuple would preserve `<` and `>` but not
    subtraction; an int preserves both, so two of the six shapes above need no
    rewrite. It is safe here ONLY because no row prints a date object, and the
    rule enforces that: a carrier reaching print/str/repr or an f-string slot
    without a field accessor returns None, because `print(d)` would emit 19358
    where CPython emits 2023-01-01.

    strptime's INPUT grammar is not `s[0:4]/s[5:7]/s[8:10]`. %Y is exactly four
    digits, %m takes one or two ('1' and '01' both parse, '0' and '13' do not),
    %d takes one or two and also the leading-space form ' 9', and CPython's
    regex backtracks between them, so '2023-1-5' is valid and '2023-01-00' is
    not. Verified 2026-09-18 against CPython 3.12.13: `_dt_strptime_ymd` over
    93,063 strings (a cross-product of malformed year/month/day tokens, 60,000
    random strings over '0123456789- x' of length 0-13, and 40,000
    '%04d-%02d-%02d' strings, seed 7) agrees on 92,959; all 104 differences have
    a non-ASCII decimal digit in the input (fullwidth, Arabic-Indic), which
    CPython's `\\d` accepts and this does not. Zero ASCII-input mismatches.
    `_dt_dfc`/`_dt_cfd`/`_dt_yday` match date.toordinal() and tm_yday on all
    3,652,059 days from 0001-01-01 to 9999-12-31; `_dt_date` matches
    datetime.date ok-vs-ValueError and value on 13,209 (y, m, d) cases spanning
    -3..5, 1899..2400, 9998..10001 x -2..14 x -2..34. 0 mismatches each.

    As with calendar, the 9 dead imports get a one-line deletion and no prelude:
    the residual guard below has proved no `datetime`, `date(` or `timedelta`
    survives, so nothing the program computes is being removed.
    """
    mod = bool(re.search(r"^\s*import\s+datetime\s*(?:#[^\n]*)?$", src, re.M))
    froms = set()
    for m in re.finditer(r"^\s*from\s+datetime\s+import\s+([^\n#]+)", src, re.M):
        froms.update(p.strip() for p in m.group(1).split(","))
    if not mod and not froms:
        return None
    if re.search(r"^\s*import\s+datetime\s+as\b", src, re.M):
        return None
    if froms - {"datetime", "date", "timedelta"}:
        return None

    live = _live(src)
    if _DT_UNSERVED.search(live) or _DT_UNSERVED_ATTR.search(live):
        return None
    # Every strptime must be the one format, and must be countable: a call this
    # pattern cannot read is a call whose format is unknown, not one to assume.
    fmts = re.findall(r"strptime\([^()]*?,\s*(['\"])([^'\"]*)\1\s*\)", live)
    if len(fmts) != len(re.findall(r"\bstrptime\s*\(", live)):
        return None
    for _, fmt in fmts:
        if fmt != "%Y-%m-%d":
            return None

    body = re.sub(
        r"\b(?:datetime\.)?datetime\.strptime\(\s*([^,()]+?)\s*,\s*['\"]%Y-%m-%d['\"]\s*\)",
        r"_dt_strptime_ymd(\1)", src)
    # `.date()` on a strptime result is already what we returned: one int.
    body = re.sub(r"(_dt_strptime_ymd\([^()]*\))\.date\(\)", r"\1", body)
    # Only `days=`. A timedelta carrying hours or weeks is not a whole number of
    # days and would land silently wrong in the subtraction.
    # `date + timedelta(days=1.5)` advances ONE day: date.__add__ reads only
    # `.days`, which timedelta has already truncated. Rewriting the call to
    # `(1.5)` and adding it to a day-count advances 1.5, and with the fractional
    # call on an untested branch verify() accepts it (demonstrated 2026-09-18:
    # original `2023 1 2`, repaired `2023.0 1.0 2.5`). Only an integer literal
    # or a plain name is rewritten; anything else refuses rather than guesses,
    # because the truncation is invisible in the source.
    def _days_arg(m):
        arg = m.group(1).strip()
        if re.fullmatch(r"-?\d+", arg) or re.fullmatch(r"[A-Za-z_]\w*", arg):
            return "(%s)" % arg
        return m.group(0)

    body = re.sub(r"\b(?:datetime\.)?timedelta\(\s*days\s*=\s*([^(),]+?)\s*\)",
                  _days_arg, body)
    body = re.sub(r"\bdatetime\.(?:date|datetime)" + _DT_CTOR, r"_dt_date(\1, \2, \3)", body)
    for name in ("date", "datetime"):
        if name in froms:
            body = re.sub(r"(?<![\w.])%s%s" % (name, _DT_CTOR), r"_dt_date(\1, \2, \3)", body)
    body = re.sub(r"\)\s*\.days\b", ")", body)

    for nm in sorted(_dt_carriers(body)):
        # A date is carried as an int day-count, so the moment one reaches any
        # stringifying position the repair prints 21358 where the original
        # printed 2028-06-23. The first version of this guard matched the
        # carrier only as a FIRST argument, so `print("far", d)`, `"%s" % d`,
        # `",".join(...)` and `sys.stdout.write(...)` all walked through it —
        # and with the leak on a branch the four tests never take, verify()
        # ACCEPTS the wrong program. Demonstrated 2026-09-18 on a constructed
        # row: original `far 2028-06-23`, repaired `far 21358`.
        #
        # So this asks the opposite question: does the carrier appear ANYWHERE
        # that is not one of the handful of uses known to be safe? Arithmetic,
        # comparison, assignment and the field accessors are safe; everything
        # else refuses. Over-refusing costs a fire, under-refusing costs a
        # silently wrong training row.
        stripped = re.sub(r"#[^\n]*", "", body)
        safe = (r"(?:\s*[-+<>=!]=?|\s*[-+]|\s*[,)\]]|\s*$"
                r"|\.timetuple\(\)\.tm_yday\b|\.(?:year|month|day)\b)")
        for m in re.finditer(r"(?<![\w.])%s\b" % re.escape(nm), stripped):
            tail = stripped[m.end():]
            if not re.match(safe, tail):
                return None
            # `d,` and `d)` are safe in arithmetic and in a call to one of our
            # own helpers, but not inside print(...)/str(...)/an f-string/a
            # join(...)/a %-format, which is where the leak actually happens.
            if re.match(r"\s*[,)]", tail):
                head = stripped[:m.start()]
                if re.search(r"(?:print|str|repr|format|write|join)\s*\([^()]*$", head):
                    return None
        if re.search(r"\{[^{}]*\b%s\b[^{}]*\}" % re.escape(nm), body):
            return None
        # The literal percent signs are doubled: this pattern is itself built
        # with %-interpolation, and `%[sdr]` read as a format spec is a
        # ValueError at import-adjacent runtime rather than a bad match.
        if re.search(r"%%[sdr][^\n]*%%[^\n]*\b%s\b" % re.escape(nm), body):
            return None
        body = re.sub(r"\b%s\.timetuple\(\)\.tm_yday\b" % nm, "_dt_yday(%s)" % nm, body)
        for attr, fn in (("year", "_dt_year"), ("month", "_dt_month"), ("day", "_dt_day")):
            body = re.sub(r"\b%s\.%s\b" % (nm, attr), "%s(%s)" % (fn, nm), body)

    # Nothing of the module may survive. A leftover would fail verification
    # anyway, but as a NameError three steps from its cause.
    rest = _live(_drop_import(body, "datetime"))
    if re.search(r"\b(?:datetime|timedelta)\b", rest):
        return None
    if "date" in froms and re.search(r"(?<![\w.])date\s*\(", rest):
        return None
    if re.search(r"\.days\b|\.date\(\)|\.timetuple\b|\.year\b|\.month\b|\.day\b", rest):
        return None

    need = []
    if "_dt_date(" in body or "_dt_strptime_ymd(" in body:
        need.append("dt_date")
    if "_dt_strptime_ymd(" in body:
        need.append("dt_strptime")
    if re.search(r"_dt_(?:year|month|day|yday)\(", body):
        need.append("dt_fields")
    if need:
        need.append("dt_civil")           # _dt_date and the accessors both call it
    order = ("dt_civil", "dt_date", "dt_fields", "dt_strptime")
    return "".join(PRELUDE[k] for k in order if k in need) + _drop_import(body, "datetime")



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
    ("calendar", rule_calendar), ("datetime", rule_datetime),
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
