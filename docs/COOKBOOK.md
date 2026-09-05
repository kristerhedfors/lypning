# The lypning cookbook — unsupported Python, rewritten

A recipe pairs a program a Rust variant refuses — exit 90, one
`<engine>: unsupported: <kind>: <detail>` line on stderr, nothing on stdout
(`docs/VERIFICATION.md` §C1) — with the program that says the same thing
inside the subset. `lypning run` falls onward to the next rung and then to
CPython on its own (§C4); this page is for staying on the cheapest rung. It is
not a style guide and none of it is about writing better Python — every
"before" here is perfectly good Python that this interpreter cannot run.

Every recipe on this page is executed by tests/test_cookbook.py, under the
engine its marker names and under CPython, with three assertions:

1. the *before* exits 90 on that engine with the `kind` and `detail` stated;
2. the *after* matches CPython on that engine — stdout and exit code;
3. both halves print the same thing under CPython (the label's `prints:`).

A recipe whose *before* stops being refused fails the suite as **obsolete**,
which is how this page finds out that coverage landed. Two readings of that
rule are wrong. A *before* that `lypning` refuses and `lypning-l` runs is a
routing fact, not an obsolete recipe: `lypning route` sends it to the larger
rung (§C5), the marker says `served_by=lypning-l`, and the suite checks that
the larger rung runs it CPython-identically. A *before* that exits 1 —
`match`, a `SyntaxError` to the parser — is not refused at all: the router
sends it to CPython before execution (§C5), a routing question, not a recipe.

## The marker

An HTML comment — `<!--`, the word `recipe`, `key=value` attributes, `-->` —
then exactly two fenced python blocks whose first lines are `# before` and
`# after`. A malformed marker is an error, never a skipped recipe.

| attribute | meaning |
|---|---|
| `id` | names the recipe in the test report: `pytest -k <id>` |
| `kind`, `detail` | the refusal line the *before* must produce; `detail` is matched as a prefix |
| `engine` | the variant that refuses; default `lypning`, the floor of `engines.SPECTRUM`; `lypning-l` when the point is that no rung short of CPython runs the *before* |
| `served_by` | a larger variant that runs the *before* unchanged — a routing fact, checked |
| `stdin`, `argv` | what the shell supplies: `echo hi` piped into `lypning script.py`; `lypning script.py 0`. `lypning run` reads a piped stdin once and replays it to every rung (`cli._replayable_stdin`); `--stdin` forces the read when stdin is a terminal |
| `min_python`, `equivalent=no`, `synthetic=yes` | assertion 3 skips below the CPython named; the two opt-outs are pinned by id in `tests/test_cookbook.py` `OPT_OUTS` |
| the `# before` / `# after` labels | the refusal line as the engine prints it / `prints:` and the rewrite's stdout, a second line written `\n` |

## Refused by every variant — rewrite for the floor

<!-- recipe id=subprocess-capture kind=module detail="import subprocess" stdin="hi\n" -->
```python
# before — lypning: unsupported: module: import subprocess
import subprocess
print(subprocess.run(["echo", "hi"], capture_output=True, text=True).stdout.strip().upper())
```
```python
# after — hoist the command into the shell: echo hi | lypning script.py — prints: HI
import sys
print(sys.stdin.read().strip().upper())
```
<!-- recipe id=subprocess-file-list kind=module detail="import subprocess" stdin="a.py\nb.txt\nc.py\n" -->
```python
# before — lypning: unsupported: module: import subprocess
import subprocess
out = subprocess.run(["printf", "a.py\nb.txt\nc.py\n"], capture_output=True, text=True).stdout
print(sum(1 for f in out.split("\n") if f.endswith(".py")))
```
```python
# after — git ls-files | lypning script.py — prints: 2
import sys
print(sum(1 for f in sys.stdin.read().split("\n") if f.endswith(".py")))
```
<!-- recipe id=os-system kind=module-attr detail="os.system" argv=["0"] -->
```python
# before — lypning: unsupported: module-attr: os.system
import os
print("rc", os.system("true"))
```
```python
# after — true; lypning script.py $? — prints: rc 0
import sys
print("rc", int(sys.argv[1]))
```
<!-- recipe id=itertools-chain kind=module detail="import itertools" -->
```python
# before — lypning: unsupported: module: import itertools
import itertools
print(list(itertools.chain([1, 2], [3])))
print(list(itertools.islice(range(10), 3)))
```
```python
# after — prints: [1, 2, 3]\n[0, 1, 2]
print([x for xs in ([1, 2], [3]) for x in xs])
print([x for i, x in enumerate(range(10)) if i < 3])
```
<!-- recipe id=functools-reduce kind=module detail="import functools" -->
```python
# before — lypning: unsupported: module: import functools
import functools
print(functools.reduce(lambda a, b: a * b, [1, 2, 3, 4], 1))
```
```python
# after — prints: 24
acc = 1
for b in [1, 2, 3, 4]:
    acc = acc * b
print(acc)
```
<!-- recipe id=string-constants kind=module detail="import string" -->
```python
# before — lypning: unsupported: module: import string
import string
print(string.ascii_lowercase[:3], string.digits[:3])
```
```python
# after — prints: abc 012
ascii_lowercase = "abcdefghijklmnopqrstuvwxyz"
digits = "0123456789"
print(ascii_lowercase[:3], digits[:3])
```
<!-- recipe id=operator-itemgetter kind=module detail="import operator" -->
```python
# before — lypning: unsupported: module: import operator
import operator
print(sorted([("b", 2), ("a", 1)], key=operator.itemgetter(1)))
```
```python
# after — prints: [('a', 1), ('b', 2)]
rows = [("b", 2), ("a", 1)]
print(sorted(rows, key=lambda r: r[1]))
```
<!-- recipe id=bisect-insort kind=module detail="import bisect" -->
```python
# before — lypning: unsupported: module: import bisect
import bisect
print(bisect.bisect_left([1, 3, 5, 7], 4))
```
```python
# after — prints: 2
xs = [1, 3, 5, 7]
print(sum(1 for x in xs if x < 4))
```
<!-- recipe id=threading-sequential kind=module detail="import threading" -->
```python
# before — lypning: unsupported: module: import threading
import threading
out = []
ts = [threading.Thread(target=lambda i=i: out.append(i * i)) for i in range(4)]
for t in ts: t.start()
for t in ts: t.join()
print(sorted(out))
```
```python
# after — concurrency belongs to the shell (&, xargs -P) — prints: [0, 1, 4, 9]
out = []
for i in range(4):
    out.append(i * i)
print(sorted(out))
```
<!-- recipe id=typing-annotations kind=module detail="import typing" -->
```python
# before — lypning: unsupported: module: import typing
from typing import List, Optional
def first(xs: List[int]) -> Optional[int]: return xs[0] if xs else None
print(first([7]), first([]))
```
```python
# after — annotations themselves are accepted — prints: 7 None
def first(xs: list) -> int:
    return xs[0] if xs else None
print(first([7]), first([]))
```
<!-- recipe id=math-prod kind=module detail="import math" -->
```python
# before — lypning: unsupported: module: import math
import math
print(math.prod([2, 3, 7]))
```
```python
# after — prints: 42
p = 1
for n in [2, 3, 7]:
    p = p * n
print(p)
```
<!-- recipe id=heapq-nsmallest kind=module detail="import heapq" -->
```python
# before — lypning: unsupported: module: import heapq
import heapq
print(heapq.nsmallest(2, [5, 1, 9, 3]), heapq.nlargest(2, [5, 1, 9, 3]))
```
```python
# after — prints: [1, 3] [9, 5]
xs = [5, 1, 9, 3]
print(sorted(xs)[:2], sorted(xs, reverse=True)[:2])
```
<!-- recipe id=re-backreference kind=re detail="pattern '(\\w)\\1': backreference \\1..\\99" engine=lypning-l -->
```python
# before — lypning-l: unsupported: re: pattern '(\w)\1': backreference \1..\99
import re
print(re.findall(r"(\w)\1", "aabbc"))
```
```python
# after — prints: ['a', 'b']
s = "aabbc"
print([a for a, b in zip(s, s[1:]) if a == b])
```
<!-- recipe id=getattr-builtin kind=builtin detail="getattr" -->
```python
# before — lypning: unsupported: builtin: getattr
print(getattr({"n": 1}, "get")("n"))
```
```python
# after — a runtime refusal: `lypning route` predicts lypning — prints: 1
print({"n": 1}.get("n"))
```
<!-- recipe id=zip-strict min_python=3.10 kind=argument detail="keyword strict" -->
```python
# before — lypning: unsupported: argument: keyword strict
print(list(zip([1, 2], [3, 4], strict=True)))
```
```python
# after — what strict buys is the error, so raise it — prints: [(1, 3), (2, 4)]
a, b = [1, 2], [3, 4]
if len(a) != len(b):
    raise ValueError("zip() argument 2 is longer than argument 1")
print(list(zip(a, b)))
```
<!-- recipe id=except-star min_python=3.11 kind=except-star detail="except* group" -->
```python
# before — lypning: unsupported: except-star: except* group
try:
    raise ValueError("bad")
except* ValueError as eg:
    print("caught", len(eg.exceptions))
```
```python
# after — over a single exception, except* is a plain except — prints: caught 1
try:
    raise ValueError("bad")
except ValueError:
    print("caught", 1)
```

## Refused by lypning, served by lypning-l — rewrite to stay on the floor

<!-- recipe id=collections-counter kind=module detail="import collections" served_by=lypning-l -->
```python
# before — lypning: unsupported: module: import collections
import collections
print(sorted(collections.Counter("abracadabra").items()))
```
```python
# after — prints: [('a', 5), ('b', 2), ('c', 1), ('d', 1), ('r', 2)]
counts = {}
for ch in "abracadabra":
    counts[ch] = counts.get(ch, 0) + 1
print(sorted(counts.items()))
```
<!-- recipe id=collections-defaultdict kind=module detail="import collections" served_by=lypning-l -->
```python
# before — lypning: unsupported: module: import collections
import collections
by_ext = collections.defaultdict(list)
for f in ["a.py", "b.txt", "c.py"]: by_ext[f.split(".")[-1]].append(f)
print(sorted(by_ext.items()))
```
```python
# after — prints: [('py', ['a.py', 'c.py']), ('txt', ['b.txt'])]
by_ext = {}
for f in ["a.py", "b.txt", "c.py"]:
    by_ext.setdefault(f.split(".")[-1], []).append(f)
print(sorted(by_ext.items()))
```
<!-- recipe id=pathlib-read-text kind=module detail="import pathlib" served_by=lypning-l -->
```python
# before — lypning: unsupported: module: import pathlib
from pathlib import Path
Path("x.txt").write_text("hi"); print(Path("x.txt").read_text())
```
```python
# after — prints: hi
with open("x.txt", "w") as f:
    f.write("hi")
print(open("x.txt").read())
```
<!-- recipe id=pathlib-name-suffix kind=module detail="import pathlib" served_by=lypning-l -->
```python
# before — lypning: unsupported: module: import pathlib
import pathlib
p = pathlib.Path("src/lypning/cli.py"); print(p.name, p.stem, p.suffix, p.parent)
```
```python
# after — prints: cli.py cli .py src/lypning
import os.path
p = "src/lypning/cli.py"
stem, suffix = os.path.splitext(os.path.basename(p))
print(stem + suffix, stem, suffix, os.path.dirname(p))
```

## Adding and verifying a recipe

Start from the evidence: `--plan` ranks the refusals (by `->cpy` cost when the
mixture arm ran, by block count otherwise; it says which) and names an example
corpus id per blocker. Add the marker and both blocks under the section the
refusal belongs in, then run the suite for that id with both variants built:
an unbuilt engine skips assertions 1–2 for every recipe naming it, and the
suite's last test fails a partial spectrum and skips, with the count in its
reason, only when nothing is built. A recipe whose *before* is not actually
refused, or whose *after* does not match CPython, is worse than no recipe.

```bash
lypning conformance --engine lypning --plan       # or --engine lypning-l
lypning corpus --json | jq -r '.[] | select(.id=="<id>") | .program'
lypning build --rust; echo $?                            # ok, ok, then 0
lypning -c 'import bisect; print(bisect.bisect_left([1, 3, 5, 7], 4))'; echo $?
lypning -c 'print(sum(1 for x in [1, 3, 5, 7] if x < 4))' \
  | diff - <(python3 -c 'print(sum(1 for x in [1, 3, 5, 7] if x < 4))'); echo $?
uv run --with "pytest>=7" python -m pytest -q tests/test_cookbook.py -k bisect
```

```
lypning: unsupported: module: import bisect      # the shape is docs/VERIFICATION.md §C1
90
0
6 passed, 129 deselected    # pytest · 2026-09-05 · 980dd97 · 20 recipes parsed
```
