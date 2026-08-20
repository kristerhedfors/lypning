# The lypning-mp cookbook — unsupported Python, rewritten

lypning-mp (`docs/MICROPYTHON.md`) runs a SUBSET of Python. When it meets something
outside that subset it exits **90** with one line on stderr naming the precise
thing:

```
$ lypning-mp -c 'import subprocess'
lypning-mp: unsupported: module: subprocess
$ echo $?
90
```

That exit code is a fork in the road, and both branches are legitimate:

- **Retry with real `python3`.** Always correct, and the right move when the
  program is long, when the missing piece is load-bearing, or when you do not
  want to think about it. 90 is clear of 0/1/2, of the shell's 126/127 and of
  128+n, so a caller can branch on it unambiguously.
- **Rewrite the line.** Usually two or three tokens, and then it runs in the
  sandbox at a fraction of the cost — `python3 --version` alone is 8573 ms
  cold in there, against zero file opens for lypning-mp
  (`docs/SANDBOX-PERFORMANCE.md` §1).

This file is the second branch: the rewrites, one per thing an agent actually
typed. It is not a style guide and none of it is about writing better Python —
every "before" here is perfectly good Python that this interpreter cannot run.

**Every recipe on this page is executed by `tests/test_cookbook.py`,
against a real lypning-mp build and a real CPython.** For each one the test asserts
three things: the *before* still exits 90 with the stated contract line, the
*after* produces byte-identical stdout and exit code under lypning-mp and CPython,
and — unless the recipe says otherwise — the two forms print the same thing
under CPython, which is what makes the rewrite a rewrite and not a different
program. A recipe whose *before* stops being refused fails the suite as
**obsolete**, which is how this page finds out that coverage landed.

## What is missing, and why

Three different reasons, and they call for different reactions:

| reason | examples | what to do |
|---|---|---|
| **Deliberately excluded** | `subprocess`, `os.system`, `threading`, `input()` | Rewrite. These are not coming: shelling out from inside a subset interpreter would be a second, worse shell, and there is no scheduler to thread against. |
| **Not built yet** | `itertools`, `functools`, `bisect`, `string`, `copy`, `decimal` | Rewrite, or retry. `lypning conformance --plan` ranks these by how many real programs each would unblock — that ranking is the build order. |
| **Cannot be built** | `@dataclass`, `enum` | Rewrite. `@dataclass` reads `__annotations__`, which MicroPython's compiler parses and discards; `enum` needs a metaclass. Neither has anything for a shim to stand on. |


## Shelling out

### Shelling out — hoist the command into the shell

<!-- recipe id=subprocess-capture kind=module detail="subprocess" stdin="hi\n" -->

The largest cluster in the corpus by a wide margin. lypning-mp has no `subprocess` on purpose: faking a shell-out from inside a subset interpreter would be a second, worse shell. The rewrite is always the same shape — run the command in the bash block you are already in, and let Python read the result on stdin.

```python
# before — lypning-mp: unsupported: module: subprocess
import subprocess
r = subprocess.run(["echo", "hi"], capture_output=True, text=True)
print(r.stdout.strip().upper())
```

```python
# after
import sys
print(sys.stdin.read().strip().upper())
```

Run it as:

```bash
echo hi | lypning-mp script.py
```

### Walking a command's output line by line

<!-- recipe id=subprocess-file-list kind=module detail="subprocess" stdin="a.py\nb.txt\nc.py\n" -->

The same hoist, for the other half of the cluster: a command that emits one record per line. `git ls-files`, `find`, `rg -l` all fit.

```python
# before — lypning-mp: unsupported: module: subprocess
import subprocess
out = subprocess.run(["printf", "a.py\nb.txt\nc.py\n"], capture_output=True, text=True).stdout
print(sum(1 for f in out.split("\n") if f.endswith(".py")))
```

```python
# after
import sys
print(sum(1 for f in sys.stdin.read().split("\n") if f.endswith(".py")))
```

Run it as:

```bash
git ls-files | lypning-mp script.py
```

### os.system — same hoist

<!-- recipe id=os-system kind=attribute detail="os.system" argv=["0"] -->

`os.system` is `subprocess` wearing a smaller hat, and the variant disables it for the same reason. If the exit code is what you want, the shell already has it in `$?`.

```python
# before — lypning-mp: unsupported: attribute: os.system
import os
rc = os.system("true")
print("rc", rc)
```

```python
# after
import sys
print("rc", int(sys.argv[1]))
```

Run it as:

```bash
true; lypning-mp script.py $?
```

### input() — read the line yourself

<!-- recipe id=input-builtin kind=builtin detail="input" stdin="world\n" -->

`input()` is compiled out: it exists to prompt an interactive user, and there is never one on the other end of a sandbox command. Reading stdin directly is what it did anyway, minus the prompt.

```python
# before — lypning-mp: unsupported: builtin: input
name = input()
print("hello", name)
```

```python
# after
import sys
name = sys.stdin.readline().rstrip("\n")
print("hello", name)
```

## Missing modules

### itertools — the two-line versions

<!-- recipe id=itertools-chain kind=module detail="itertools" -->

Every itertools function an agent reaches for has a comprehension that is the same length. The module is 30 KB of C that would buy about six lines of Python.

```python
# before — lypning-mp: unsupported: module: itertools
import itertools
print(list(itertools.chain([1, 2], [3])))
print(list(itertools.islice(range(10), 3)))
```

```python
# after
print([x for xs in ([1, 2], [3]) for x in xs])
print([x for i, x in enumerate(range(10)) if i < 3])
```

### functools.reduce — write the loop

<!-- recipe id=functools-reduce kind=module detail="functools" -->

`reduce` is three lines written out, and the written-out version is the one the next reader understands. `functools.lru_cache` has no short rewrite; use a dict.

```python
# before — lypning-mp: unsupported: module: functools
import functools
print(functools.reduce(lambda a, b: a * b, [1, 2, 3, 4], 1))
```

```python
# after
acc = 1
for b in [1, 2, 3, 4]:
    acc = acc * b
print(acc)
```

### string — the constants are shorter than the import

<!-- recipe id=string-constants kind=module detail="string" -->

`string` is five string constants and a template class. Two of the constants are what anyone imports it for.

```python
# before — lypning-mp: unsupported: module: string
import string
print(string.ascii_lowercase[:3], string.digits[:3])
```

```python
# after
ascii_lowercase = "abcdefghijklmnopqrstuvwxyz"
digits = "0123456789"
print(ascii_lowercase[:3], digits[:3])
```

### operator — a lambda says it more clearly

<!-- recipe id=operator-itemgetter kind=module detail="operator" -->

`itemgetter(1)` and `lambda r: r[1]` are the same thing; `attrgetter` and `methodcaller` likewise.

```python
# before — lypning-mp: unsupported: module: operator
import operator
rows = [("b", 2), ("a", 1)]
print(sorted(rows, key=operator.itemgetter(1)))
```

```python
# after
rows = [("b", 2), ("a", 1)]
print(sorted(rows, key=lambda r: r[1]))
```

### bisect — sort once instead

<!-- recipe id=bisect-insort kind=module detail="bisect" -->

`bisect` is worth having when a list is maintained in order across many inserts. In a one-liner it is almost always used once, and sorting once costs less than the import would.

```python
# before — lypning-mp: unsupported: module: bisect
import bisect
xs = [1, 3, 5, 7]
print(bisect.bisect_left(xs, 4))
```

```python
# after
xs = [1, 3, 5, 7]
print(sum(1 for x in xs if x < 4))
```

### enum — module constants

<!-- recipe id=enum-constants kind=module detail="enum" -->

`enum` needs a metaclass, which MicroPython does not have. A class holding constants covers what a one-liner uses it for; what it loses is the `repr` and the iteration, so check whether you print the member.

```python
# before — lypning-mp: unsupported: module: enum
import enum
class Colour(enum.Enum):
    RED = 1
    GREEN = 2
print(Colour.RED.value, Colour.GREEN.value)
```

```python
# after
class Colour:
    RED = 1
    GREEN = 2
print(Colour.RED, Colour.GREEN)
```

### @dataclass — write __init__ and __repr__

<!-- recipe id=dataclass-plain-class kind=module detail="dataclasses" -->

This one cannot be shimmed rather than merely is not: `@dataclass` reads the class body's annotations out of `__annotations__`, and MicroPython's compiler parses annotations and discards them, so there is nothing for a shim to read. The generated methods are the short part.

```python
# before — lypning-mp: unsupported: module: dataclasses
from dataclasses import dataclass
@dataclass
class P:
    x: int
    y: int = 0
print(P(1), P(1, 2).y)
```

```python
# after
class P:
    def __init__(self, x, y=0):
        self.x = x
        self.y = y
    def __repr__(self):
        return "P(x=" + repr(self.x) + ", y=" + repr(self.y) + ")"
print(P(1), P(1, 2).y)
```

### threading — do it in order

<!-- recipe id=threading-sequential kind=module detail="threading" -->

There is no scheduler here and nothing to overlap with: the work is CPU-bound inside a single-threaded VM, so threads would serialise anyway. Concurrency in the sandbox belongs to the shell (`&`, `xargs -P`), not to the interpreter.

```python
# before — lypning-mp: unsupported: module: threading
import threading
out = []
ts = [threading.Thread(target=lambda i=i: out.append(i * i)) for i in range(4)]
for t in ts: t.start()
for t in ts: t.join()
print(sorted(out))
```

```python
# after
out = []
for i in range(4):
    out.append(i * i)
print(sorted(out))
```

### typing — delete the import

<!-- recipe id=typing-annotations kind=module detail="typing" -->

Annotations are parsed and discarded, so they cost nothing and mean nothing here. Only the `typing` import fails; the annotations themselves are fine, including `int | None`.

```python
# before — lypning-mp: unsupported: module: typing
from typing import List, Optional
def first(xs: List[int]) -> Optional[int]:
    return xs[0] if xs else None
print(first([7]), first([]))
```

```python
# after
def first(xs: list) -> int:
    return xs[0] if xs else None
print(first([7]), first([]))
```

## Missing methods and functions

### str.removeprefix / removesuffix — slice on a test

<!-- recipe id=str-removeprefix kind=attribute detail="str.removeprefix" -->

The 3.9 string methods are absent. The slice is the definition, and the `startswith` guard is the part people forget when they hand-roll it.

```python
# before — lypning-mp: unsupported: attribute: str.removeprefix
s = "src/prompts.js"
print(s.removeprefix("src/"), "x.py".removesuffix(".py"))
```

```python
# after
s = "src/prompts.js"
print(s[4:] if s.startswith("src/") else s, "x.py"[:-3] if "x.py".endswith(".py") else "x.py")
```

### str.casefold — lower() is enough here

<!-- recipe id=str-casefold kind=attribute detail="str.casefold" -->

`casefold` differs from `lower` only for a handful of scripts — German ß, Greek final sigma, Cherokee. For ASCII and for Swedish they are identical, so if the input is either, `lower()` is the same answer. If it is not, this is a case to send to real python3.

```python
# before — lypning-mp: unsupported: attribute: str.casefold
print("Räksmörgås ABC".casefold())
```

```python
# after
print("Räksmörgås ABC".lower())
```

### math.prod — multiply in a loop

<!-- recipe id=math-prod kind=attribute detail="math.prod" -->

`math` is present; `prod` (3.8) is one of the few names missing from it.

```python
# before — lypning-mp: unsupported: attribute: math.prod
import math
print(math.prod([2, 3, 7]))
```

```python
# after
p = 1
for n in [2, 3, 7]:
    p = p * n
print(p)
```

### heapq.nsmallest / nlargest — sort and slice

<!-- recipe id=heapq-nsmallest kind=attribute detail="heapq.nsmallest" -->

`heapq`'s heap operations are there; the two convenience wrappers are not. For the list sizes a one-liner handles, sorting is not the expensive part.

```python
# before — lypning-mp: unsupported: attribute: heapq.nsmallest
import heapq
xs = [5, 1, 9, 3]
print(heapq.nsmallest(2, xs), heapq.nlargest(2, xs))
```

```python
# after
xs = [5, 1, 9, 3]
print(sorted(xs)[:2], sorted(xs, reverse=True)[:2])
```

## Keyword arguments the C builtins do not take

### zip(strict=True) — check the lengths yourself

<!-- recipe id=zip-strict min_python=3.10 kind=argument detail="keyword strict" -->

The 3.10 keyword. What it buys is the error, so the rewrite is to raise it.

```python
# before — lypning-mp: unsupported: argument: keyword strict
a, b = [1, 2], [3, 4]
print(list(zip(a, b, strict=True)))
```

```python
# after
a, b = [1, 2], [3, 4]
if len(a) != len(b):
    raise ValueError("zip() argument 2 is longer than argument 1")
print(list(zip(a, b)))
```

### Builtin keywords — pass them positionally

<!-- recipe id=builtin-keywords-positional kind=argument detail="keyword maxsplit" -->

A whole family lands here at once: MicroPython's C builtins take these arguments, they just do not take them by NAME. `round(x, 2)`, `int(s, 16)`, `s.split(sep, 1)`, `b.decode('utf-8', 'replace')` all work; only the keyword form does not.

```python
# before — lypning-mp: unsupported: argument: keyword maxsplit
print("a b c".split(" ", maxsplit=1))
print(round(1.2345, ndigits=2), int("ff", base=16))
```

```python
# after
print("a b c".split(" ", 1))
print(round(1.2345, 2), int("ff", 16))
```

## Syntax this parser cannot read

### match — if / elif

<!-- recipe id=match-statement min_python=3.10 kind=syntax detail="match statement" -->

The 3.10 structural-pattern statement is not in this parser. A `match` on a literal is an if-chain; a `match` that destructures needs the unpacking written out, which is where the rewrite stops being mechanical.

```python
# before — lypning-mp: unsupported: syntax: match statement
def kind(x):
    match x:
        case 0:
            return "zero"
        case int():
            return "int"
        case _:
            return "other"
print(kind(0), kind(3), kind("s"))
```

```python
# after
def kind(x):
    if x == 0:
        return "zero"
    elif isinstance(x, int):
        return "int"
    else:
        return "other"
print(kind(0), kind(3), kind("s"))
```

### {**a, **b} — build it with update()

<!-- recipe id=dict-unpack-literal kind=syntax detail="dict unpacking in a literal ({**d})" -->

`f(**kw)` works; `{**a, **b}` does not. `dict(a)` copies and `update` merges, in the same left-to-right order the literal has.

```python
# before — lypning-mp: unsupported: syntax: dict unpacking in a literal ({**d})
defaults = {"n": 1, "v": False}
overrides = {"v": True}
print({**defaults, **overrides, "extra": 1})
```

```python
# after
defaults = {"n": 1, "v": False}
overrides = {"v": True}
merged = dict(defaults)
merged.update(overrides)
merged["extra"] = 1
print(merged)
```

### def f(a, /, b) — drop the marker

<!-- recipe id=posonly-params kind=syntax detail="positional-only parameter (def f(a, /, b))" -->

The `/` marks parameters that callers may not pass by name. Nothing in a one-liner depends on that being enforced, and removing it does not change any call that was already legal.

```python
# before — lypning-mp: unsupported: syntax: positional-only parameter (def f(a, /, b))
def clamp(x, /, lo=0, hi=10):
    return max(lo, min(hi, x))
print(clamp(15), clamp(-1, hi=5))
```

```python
# after
def clamp(x, lo=0, hi=10):
    return max(lo, min(hi, x))
print(clamp(15), clamp(-1, hi=5))
```

### except* — catch the group as one

<!-- recipe id=except-star min_python=3.11 kind=syntax detail="except* (exception groups)" -->

Exception groups are 3.11 and there is no `ExceptionGroup` here. Unless the code genuinely raises a group, `except*` over a single exception is a plain `except`.

```python
# before — lypning-mp: unsupported: syntax: except* (exception groups)
try:
    raise ValueError("bad")
except* ValueError as eg:
    print("caught", len(eg.exceptions))
```

```python
# after
try:
    raise ValueError("bad")
except ValueError:
    print("caught", 1)
```

### Parenthesized with-items — one with per line

<!-- recipe id=with-parens kind=syntax detail="parenthesized with-items" -->

The 3.10 parenthesized form exists to wrap a long line. The unparenthesized multi-item form parses fine, and so does nesting.

```python
# before — lypning-mp: unsupported: syntax: parenthesized with-items
open("a.txt", "w").write("1")
open("b.txt", "w").write("2")
with (
    open("a.txt") as f,
    open("b.txt") as g,
):
    print(f.read(), g.read())
```

```python
# after
open("a.txt", "w").write("1")
open("b.txt", "w").write("2")
with open("a.txt") as f, open("b.txt") as g:
    print(f.read(), g.read())
```

## Running out of heap

### Reading a big file — do not hold two copies of it

<!-- recipe id=json-load-large kind=memory detail="heap exhausted (this input is larger than lypning-mp's heap)" equivalent=no synthetic=yes -->

lypning-mp's heap is a fraction of CPython's, and `json.load` followed by `json.dumps` holds the text, the parsed object and the re-serialised text at once. This was the single MISMATCH in a 472-entry conformance run before heap exhaustion was given its own exit code. If the question is about the TEXT, never build the object; if it is about the object, read the file in pieces.

```python
# before — lypning-mp: unsupported: memory: heap exhausted (this input is larger than lypning-mp's heap)
import json
d = json.loads(BIG)
s = json.dumps(d)
print(s.count("needle"))
```

```python
# after
print(BIG.count("needle"))
```

The two forms are not compared against each other here: the point of the rewrite is that it never materialises the parsed object, so there is nothing to compare it against — `BIG` stands for a file the before form cannot hold.

## Adding a recipe

Do not write one from imagination. The point of this page is that every entry
came from a program someone ran, so start from the evidence:

```bash
lypning conformance --engine lypning-mp --plan
```

`--plan` ranks the refusals by how many corpus programs each one blocks — that
is the order worth writing recipes in — and names an example id per blocker.
Pull the program itself out of the corpus by that id:

```bash
lypning corpus --json | jq -r '.[] | select(.id=="py-0a1daaa0c965") | .program'
```

Then add the recipe to the section it belongs in, with the marker comment above
it and both fenced blocks, and check both halves against the real thing:

```bash
lypning route -c '<the before form>'     # it must actually be refused
lypning run   -c '<the after form>'      # it must match python3
```

A recipe whose *before* is not actually refused, or whose *after* does not
match CPython, is worse than no recipe. All three of those have caught a draft
that read perfectly well and was wrong.
