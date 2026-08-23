<!-- treatment T5 — rewrite rules only, no capability tables -->
# Staying in the fast subset: rewrite rules

The Python you write here runs on a tiered runtime whose fastest tier is a
small Python subset. These are the rewrites that keep a program on it. Each is
a real substitution, not an approximation — **correctness comes first, and a
wrong answer is far worse than falling back to CPython.**

| instead of | write |
|---|---|
| `import re` for a fixed pattern | `str.split`, `str.startswith`, `str.find`, `str.partition`, a character loop |
| `re.sub(r"\s+", " ", s)` | `" ".join(s.split())` |
| `re.findall(r"\d+", s)` | accumulate digit runs in a `for` loop over the characters |
| `collections.Counter(xs)` | `d = {}` then `d[x] = d.get(x, 0) + 1` |
| `Counter(xs).most_common(k)` | `sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:k]` |
| `collections.defaultdict(list)` | `d.setdefault(k, []).append(v)` |
| `import csv` for simple rows | `line.split(",")` over `f.read().splitlines()` |
| `import pathlib` | `os.path.join`, `os.path.basename`, `os.path.splitext`, `open` |
| `import glob` | `os.listdir` filtered with `str.endswith` — but see the refusals below |
| `import math`, `math.sqrt(n)` | `n ** 0.5` |
| `math.isqrt(n)` | an integer binary search, or `int(n ** 0.5)` corrected by a loop |
| `math.floor/ceil` | `//` and `-(-a // b)` |
| `import itertools` | a plain nested loop or a comprehension |
| `import functools.reduce` | an accumulator in a `for` loop |
| `import datetime` / `timedelta` | `divmod` arithmetic on integer seconds |
| `import textwrap` | `str.ljust`, `str.rjust`, `str.zfill`, slicing |
| `str.center(n)` | `s.ljust(...)` / `s.rjust(...)` with the padding computed yourself |
| `dict.fromkeys(xs)` to dedupe | a `for` loop with a membership test into a list |
| `class Foo:` for a record | a `dict` or a `tuple` |
| a decorator | call the wrapping function directly |
| a generator function with `yield` | build and return a list, or use a generator *expression* |
| the walrus `x := ...` | a plain assignment on its own line |
| `def f(*, a)` (keyword-only) | `def f(a)` |
| `f"{x=}"` | `f"x={x}"` |

**Three refusals no rewrite of an import can dodge.** They are decided while
the program runs:

* **Integers are 64-bit.** Anything whose exact value leaves the signed 64-bit
  range — `2**64`, factorials past 20, large products — is refused. Do not
  approximate it with a float; let it fall back.
* **Set iteration order is refused.** `set(...)` and `len(set(...))` are fine.
  Printing a set, looping over one, or `list(set(...))` is refused because no
  independent implementation reproduces CPython's order. `sorted(set(...))`
  is fine and is usually what you wanted.
* **`os.listdir()` is refused** for the same reason — the filesystem defines
  its order — and so is `repr()` of most non-ASCII text.

Cheapest imports first: if you must import, `re`, `collections`, `math`, `csv`,
`hashlib`, `datetime`, `random`, `struct`, `base64`, `pathlib`, `textwrap`,
`glob`, `statistics`, `time`, `urllib.parse`, `zlib`, `shutil` and `tempfile`
all land on a second small interpreter that is still far cheaper than CPython.
Anything else — `subprocess`, `itertools`, `functools`, `argparse`,
`unicodedata`, `importlib` — goes straight to CPython.
