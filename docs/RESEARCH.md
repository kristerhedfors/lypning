# lypning-mp — what to build it from

*Research date: 2026-08-13. Question: the site's in-browser Linux sandbox needs
to run Python one-liners. CPython costs 8.5 seconds the first time it is
touched. What should we ship instead?*

**lypning-mp** is the working name for a minimal
Python-subset interpreter for the CheerpX sandbox. This document surveys what
it could be built from, and recommends one.

Everything in [§2](#2-measured-in-this-container) was run in the session
container and is reproduced with real output. Everything else is marked as read
rather than measured. The two are not mixed.

---

## 1. The constraint that actually decides this

The obvious ranking criteria — RSS, warm benchmark speed, "how fast is the VM" —
are the wrong ones. `docs/SANDBOX-PERFORMANCE.md` §1 measures the real cost
model against production, and it is dominated by one thing: the root filesystem
is an ext2 image streamed block by block over a WebSocket and cached in
IndexedDB, so **the first execution of a binary pays for its ELF and every
library it links, over the network.**

| command | cold | warm | ratio |
|---|---|---|---|
| `python3 --version` | 8573 ms | 87 ms | 98× |
| `perl -e 'print 42'` | 8333 ms | 108 ms | 77× |

Three further figures from the same document bound the design:

- the `execInSandbox` round-trip floor is **50–85 ms**, paid by every command
  whatsoever;
- a process spawn costs **6.5 ms** minimum;
- a command that runs long enough to hit the 30 s exec ceiling **destroys the
  VM** — `resetSandbox("exec_timeout")` discards the CheerpX instance, and every
  later command in the turn returns `sandbox not ready`. A `command -v` for a
  tool that was *not* installed did exactly this once, because it stats every
  `PATH` directory and all of them are cold.

So the ranking criterion for lypning-mp is **how many distinct filesystem bytes and
path probes a cold `-c` one-liner touches**. Not speed. Not memory.

This has a sharp corollary. A dynamically linked interpreter pays `ld.so`'s
search — a `stat` per candidate directory per library, then the library itself.
A **fully static binary links nothing, so there is no `ld.so` path search and no
`.so` streaming at all.** Static is not a nice-to-have here; it is the
mechanism.

The second-largest source of probes is the interpreter's own import machinery.
CPython's `import site` walks `encodings/`, `sitecustomize`, `.pth` files and
the `/usr/lib/python3.x` tree; that is very likely most of the 8573 ms. Any
candidate that also keeps its standard library as `.py` files on disk inherits
the same problem. **A stdlib frozen into the binary is the other half of the
mechanism.**

### The delivery path

The upstream image builder assembles the Alpine i386 image and its
`PKGS_COMMON` currently reads:

```sh
PKGS_COMMON="bash coreutils grep sed gawk findutils file less python3 jq nodejs git"
```

So "what should lypning-mp be built from" is really "what can replace `python3` in
that list". Alpine's own index gives the size of what would be removed
([§2.6](#26-what-python3-costs-in-the-alpine-i386-image)): **27.0 MiB across
python3 and its 16 shared-library dependencies.**

---

## 2. Measured in this container

Ubuntu 24.04.4, x86_64 host kernel, gcc 13.3.0, CPython 3.11.15. Commands and
output are verbatim; long output is trimmed where marked.

### 2.1 The i386 toolchain, and building musl for i386

The container starts with no 32-bit libc and no musl for i386. Both are
obtainable, and the second one is the finding that matters.

`gcc-multilib` + `libc6-dev-i386` install cleanly (after an `apt-get update` —
the first attempt 404'd on stale `libc6-i386` URLs) and give a working i386
glibc:

```
$ gcc -m32 -O2 hello.c -o hello32 && ./hello32 && file hello32
hello 4
hello32: ELF 32-bit LSB pie executable, Intel 80386, version 1 (SYSV), dynamically linked, ...
```

**i386 binaries also execute here**, so the real target artifact can be built
*and tested* locally and in CI without a VM or an emulator.

glibc-static, however, is disqualifying on size, and it is also *broken* here:

```
$ gcc -m32 -static -fno-builtin fm2.c -o fm2 -lm
/usr/bin/ld: /tmp/cc0mVDhP.o: in function `main':
fm2.c:(.text+0x3a): undefined reference to `fmod'
```

`/usr/lib32/libm.a` exists but does not resolve `fmod`. (A naive test with a
constant argument appears to link only because gcc constant-folds the call —
worth knowing before concluding the toolchain is fine.) MicroPython's standard
variant hits this immediately.

Ubuntu's `musl-tools` is x86_64-only and its `-m32` mode cannot work — there is
no 32-bit musl libc in the package. **musl builds for i386 from source in this
container in about a minute, with no cross toolchain and no Docker:**

```
$ ./configure --prefix=/opt/musl-i386 --target=i686 CC="gcc -m32" AR=ar RANLIB=ranlib
$ make -j && make install
BUILD_OK
-rw-r--r-- 1 root root 2184116 /opt/musl-i386/lib/libc.a
```

Two traps, both worth recording because each cost a build:

- `--target=i386` makes musl look for `i386-ar` and `i386-ranlib`, which do not
  exist. Use `--target=i686` **and** override `AR=ar RANLIB=ranlib`.
- The generated `musl-gcc` wrapper does not propagate `-m32` to the linker's
  emulation, so the link fails with `skipping incompatible .../libc.a`. The
  wrapper needs `-Wl,-m,elf_i386`:

```sh
#!/bin/sh
exec gcc -m32 "$@" -Wl,-m,elf_i386 -specs "/opt/musl-i386/lib/musl-gcc.specs"
```

`tcc` 0.9.27 from Ubuntu is x86_64-only and has no i386 crt files
(`error: file 'crt1.o' not found`); it is not a route to an i386 artifact here.

### 2.2 The static floor — glibc vs musl on i386

Same `int main(void){return 0;}`, `-Os`, stripped:

| libc | arch | bytes |
|---|---|---|
| glibc, static | i386 | **635,744** |
| musl, static | i386 | **13,020** |
| musl, static | x86_64 | 13,392 |

With a `printf` pulled in: glibc 639,840 vs musl **25,520**.

**glibc-static costs 636 KB before a single line of interpreter exists. musl
costs 13 KB.** That single number decides the libc question; everything below is
built against musl i386.

### 2.3 What each candidate compiles to

All i386, fully static, stripped, built against the musl from §2.1.

| candidate | version | bytes | notes |
|---|---|---|---|
| MicroPython, `minimal` variant | 1.29.0-preview | **193,772** | no `re`, no `json` — the floor, not a product |
| MicroPython, `standard` variant | 1.29.0-preview | **549,880** | frozen manifest; `re` + `json` built in |
| **lypning-mp prototype** (§2.5) | — | **541,688** | standard + `re` span/groups + 3 frozen shims |
| Berry | c304823 (2026-08-13) | **365,660** | not Python syntax — see §3 |
| pocketpy | 2.2.0 | **816,956** | C11, not C++17 |
| CPython 3.11.15 (host binary, dynamic, x86_64) | 3.11.15 | 6,639,992 | plus 3.1 MB of `.so` |

Build notes: MicroPython's `standard` variant needs `make submodules` first;
pocketpy needs `-fno-stack-protector` against musl (`__stack_chk_fail_local` is
a glibc-ism) and only builds a static lib by default, so `src2/main.c` must be
linked by hand; Berry hard-codes `-lreadline` in its Makefile.

### 2.4 File touches — the measurement that decides it

`strace -f -c -e trace=file`, and separately a count of distinct existing files
actually opened and their total size.

Trivial run (`-c 'pass'` or equivalent):

| runtime | file syscalls | of which ENOENT | files opened | bytes in those files |
|---|---|---|---|---|
| CPython 3.11 | **109** | 34 | 16 | **3,536,023** |
| CPython `-S` | 63 | 28 | 12 | 3,524,407 |
| CPython `-S -E -I` | 63 | 28 | 12 | 3,524,407 |
| MicroPython (static i386) | **2** | 0 | **0** | **0** |
| pocketpy (static i386) | 1 | 0 | 1 | 11 (the script itself) |
| Berry (static i386) | 1 | 0 | 0 | 0 |
| static `hello` (control) | 1 | 0 | 0 | 0 |

`-S` is worth having — it removes `site`, cutting file syscalls 109 → 63 (42%)
— but `-E` and `-I` add nothing on top of it. **`-S` cannot get CPython below
63 syscalls and 3.5 MB, because `encodings` and the shared libraries are not
`site`'s doing.** The five largest files CPython opens on a bare `-c 'pass'`:

```
2125328 /lib/x86_64-linux-gnu/libc.so.6
 952616 /lib/x86_64-linux-gnu/libm.so.6
 360460 /usr/lib/locale/C.utf8/LC_CTYPE
  37483 /etc/ld.so.cache
  27028 /usr/lib/x86_64-linux-gnu/gconv/gconv-modules.cache
```

Three of those five exist only because the binary is dynamically linked.

Now the realistic corpus workload — importing what Claude Code one-liners
actually import:

| runtime | workload | file syscalls | ENOENT | files opened | bytes |
|---|---|---|---|---|---|
| CPython 3.11 | `import base64, os.path, re, json` | **233** | 44 | 36 | **4,038,290** |
| lypning-mp prototype, default `sys.path` | equivalent | 56 | 54 | 0 | 0 |
| lypning-mp prototype, `sys.path` trimmed | equivalent | **26** | 26 | **0** | **0** |

**Total cold surface** — the binary plus every file it opens:

- CPython 3.11: 6,639,992 + 3,536,023 = **10,176,015 bytes (9.7 MiB)**
- lypning-mp prototype: **541,688 bytes (0.52 MiB)**, single file, nothing else

That is a **19× reduction in cold bytes and a 9× reduction in file syscalls** on
the realistic workload.

### 2.5 The lypning-mp prototype, and the one remaining probe source

MicroPython out of the box is not enough: its `re` match objects have only
`.group()`, and `base64`/`os.path`/`glob`/`collections.Counter` are absent. Both
gaps close at build time.

`.start()`, `.end()`, `.span()` and `.groups()` are build options
(`MICROPY_PY_RE_MATCH_SPAN_START_END`, `MICROPY_PY_RE_MATCH_GROUPS`), gated off
in the standard variant only because its ROM level is below `EVERYTHING`.
Turning them on plus freezing three small pure-Python shims (`base64` over
`binascii`, an `os.path`, and `findall`/`finditer`/`split` over `re.search`)
produced a **541,688-byte** binary — *smaller* than the stock standard build —
and all of it works:

```
$ /tmp/lypning-mp -c 'import re; m=re.search("b+","abbbc"); print(m.group(0), m.start(), m.end(), m.span())'
bbb 1 4 (1, 4)
$ /tmp/lypning-mp -c 'import base64; print(base64.b64encode(b"hi"), base64.b64decode(b"aGk="))'
b'aGk=' b'hi'
$ /tmp/lypning-mp -c 'import refull; print(refull.findall(r"\d+","a1b22c333"))'
['1', '22', '333']
```

**The frozen-stdlib idea survives contact with measurement, but with one
caveat that has to be designed for.** Freezing a module does not by itself make
importing it free: MicroPython searches `sys.path` *before* the frozen table, at
three `statx` calls per path entry per module:

```
statx(AT_FDCWD, "base64", ...) = -1 ENOENT
statx(AT_FDCWD, "base64.py", ...) = -1 ENOENT
statx(AT_FDCWD, "base64.mpy", ...) = -1 ENOENT
statx(AT_FDCWD, "/root/.micropython/lib/binascii", ...) = -1 ENOENT
... 12 probes for one import
```

The default path is `['', '.frozen', '/root/.micropython/lib', '/usr/lib/micropython']`.
Trimming it to `.frozen` alone cuts the five-import workload from 56 file
syscalls to 26. Removing `.frozen` too breaks frozen imports entirely
(`ImportError: no module named 'base64'`), so `.frozen` is required.

In the sandbox these probes are exactly the pathology that made
`command -v <absent tool>` cost 30 s, so **lypning-mp must pin `sys.path` to
`['.frozen']` at build time** rather than inherit MicroPython's default. That is
a one-line port change, not a fork.

### 2.6 What `python3` costs in the Alpine i386 image

From Alpine's own `x86` package index (`latest-stable/main`), installed sizes:

| package | version | installed bytes |
|---|---|---|
| python3 | 3.14.7-r0 | 23,625,209 |
| sqlite-libs | 3.53.2-r0 | 1,800,396 |
| libssl3 | 3.5.7-r0 | 852,544 |
| openssl | 3.5.7-r0 | 769,404 |
| bzip2 | 1.0.8-r6 | 343,773 |
| readline | 8.3.3-r1 | 263,856 |
| xz-libs | 5.8.3-r0 | 259,536 |
| mpdecimal | 4.0.1-r0 | 195,452 |
| zlib | 1.3.2-r0 | 103,808 |
| gdbm | 1.26-r0 | 72,312 |
| libffi | 3.5.2-r1 | 34,220 |
| expat | 2.8.2-r0 | 25,876 |
| **total** | | **28,346,386 (27.0 MiB)** |

`python3` declares 16 shared-library dependencies. Replacing it with a single
541 KB static binary removes **27 MiB from the image and 16 `.so` chains from
the cold path**, and the image is the thing being streamed.

### 2.7 Wall-clock startup (host, native, warm)

Averaged over 40–50 runs. **These are x86_64 host numbers on a warm page cache.
They are not sandbox numbers** — they measure interpreter initialisation only,
with block streaming and CPU emulation removed.

| runtime | ms/run |
|---|---|
| CPython 3.11 | 15.72 |
| CPython `-S` | 12.15 |
| CPython `-S -E -I` | 11.50 |
| MicroPython / lypning-mp | **0.96** |
| Berry | 0.97 |
| pocketpy | 3.76 |
| static `hello` (floor) | 0.92 |

MicroPython's interpreter init is **within 0.04 ms of an empty C program**. There
is nothing left to optimise: startup is `execve` plus page-in, and that is it.

---

## 3. The candidates

### MicroPython — the recommendation

MIT, v1.29.0-preview (commit `1827631`, 2026-08-05). Unix port, `make`-based, no
build-system surprises. Built here to **549,880 bytes** static i386 musl, or
**541,688** as the lypning-mp prototype.

Its stdlib is **frozen into the binary** — that is the whole reason it opens
zero files. `sys`, `os`, `json`, `re`, `binascii`, `struct`, `collections`,
`io`, `time`, `math`, `hashlib`, `random`, `select`, `argparse`, `heapq` and
`deflate` are all present.

Distance from CPython semantics, measured against the corpus in §4: closer than
anything else in this survey, and the residue is mostly *missing library
surface* rather than *different language*, which is the kind of gap a frozen
pure-Python shim closes. Language-level, the things one-liners use — f-strings,
`%`-formatting, comprehensions, dict/set literals, generators, classes,
`with`, lambdas, `sorted(key=)` — all work.

The known language-level divergences that matter: integers are arbitrary
precision only if `MICROPY_LONGINT_IMPL` says so (the unix port enables it),
`str.center` takes no fill character, and there is no `collections.Counter`.

### pocketpy — the runner-up, and not close

MIT, **2.2.0**. Note the brief's premise is out of date: pocketpy v2 is **pure
C11** (84 `.c` files, zero `.cpp`), not headers-only C++17. That is an
improvement — it means no `libstdc++`, which does not exist for musl i386 anyway.

Built to **816,956 bytes**: 1.5× MicroPython for substantially less. It opens no
files, and its startup is 3.76 ms — good, but 4× MicroPython's.

It fails the corpus badly (§4): **no `re` module at all**, no `sys.stdin`,
`open()` requires three arguments, generator expressions are a syntax error in
argument position, and `str.center` takes no fill character. `re` is not a shim
away — it would have to be written. Its design centre is game scripting, where
those are reasonable omissions; ours is text munging, where `re` is the point.

### Berry — the right shape, the wrong language

MIT, commit `c304823`. The smallest real result here: **365,660 bytes** static
i386 musl, 0.97 ms startup, zero file opens. Architecturally it is exactly what
lypning-mp wants to be.

**It is not Python syntax.** Berry is a Python-*inspired* language with `end`
terminators, `var` declarations and `def f() ... end`. The brief's own rule —
must be Python syntax — rules it out, for the same reason Lua and Wren are out.
The lesson worth keeping is its size: a complete, useful dynamic-language VM
with a frozen stdlib fits in 366 KB, which is the yardstick for judging whether
a purpose-built interpreter (option (b) in §6) would actually be smaller than
MicroPython. It would not be, by much.

### The rest

| candidate | verdict | why |
|---|---|---|
| **Snek** | Out | GPLv3, and a much smaller subset than the corpus needs (targets 32 KB ROM / 2 KB RAM). Aimed at microcontrollers, not text processing. No `re`, no `json`. |
| **tinypy** | Out | Effectively unmaintained for over a decade. Adopting it means owning it, with none of MicroPython's test suite or ecosystem. |
| **Wren / Lua** | Out by rule | Not Python syntax. Lua's lesson is real, though: it is the existence proof that a ~200 KB VM can be genuinely fast and complete — which is why Berry, its closest Python-flavoured analogue, lands at 366 KB. |
| **RustPython** | Out | Aims at CPython compatibility, and pays for it. Its own tracker discusses WASM binaries around 30 MB unoptimised, still too large after optimisation and gzip. That is 60× the lypning-mp prototype for a runtime we would additionally have to cross-compile to i386 musl. |
| **CPython, frozen/stripped minimal** | Out — see below | |
| **Cinder / Pyston** | Irrelevant | Both are throughput optimisations for long-running server workloads (JIT, inline caches). They make startup *worse*, not better, and neither targets i386. |
| **`python-minimal` via `-DPy_…`** | Out | There is no supported "compile CPython small" switch set. `--without-doc-strings`, `--disable-test-modules` and friends shave low single-digit MB off a 23 MB install; they do not change the shape of the problem, which is that the stdlib lives in files. |
| **`Programs/_freeze_module`** | Interesting, insufficient | This is the machinery CPython itself uses to freeze startup modules, and it is the right *idea* — it is what MicroPython does by default. But CPython freezes only the startup set; freezing the whole stdlib is a long-standing open issue, deep-freezing of code objects was **removed** in 3.13 as too slow to build and a poor fit for the build system, and even a fully frozen CPython still carries a 6.6 MB binary linked against 3.1 MB of shared libraries. You would spend the effort and land at 10× lypning-mp's size. |

### Newer, as of 2026

The one genuinely new entrant worth naming is **PikaPython** (MIT), an
ultra-lightweight Python interpreter advertised as running in 4 KB of RAM with
zero dependencies. It is smaller than everything above and correspondingly
further from CPython; like Snek it targets microcontrollers, so `re` and `json`
are not its concern. Not evaluated by build here — the module surface rules it
out before size becomes interesting.

On the WASM-oriented side, nothing changes the analysis, because **our target is
not WASM**. CheerpX runs an i386 Linux userland, so Pyodide, py2wasm and
`wasi-python` are all irrelevant: they would have to run *inside* the browser
alongside CheerpX rather than inside the guest, which is a different feature.
Their size stories (Pyodide's runtime is tens of MB) would not help anyway.

---

## 4. Semantics against the actual corpus

Twenty one-liners of the kind the brief describes — json parsing, `re`, string
munging, file IO, `sys.argv`/stdin, base64, `os.path` — run three ways. Verbatim
first lines of output.

| case | MicroPython | pocketpy | CPython |
|---|---|---|---|
| `json.loads` / `json.dumps` | ✅ `2 {"b": true}` | ✅ | ✅ |
| `base64.b64encode` | ❌ no module (`binascii` works) | ✅ | ✅ |
| `os.path.join` / `basename` | ❌ no module | ❌ no module | ✅ |
| `str.center(5,"-")` | ❌ takes 2 args | ❌ no attribute | ✅ |
| `collections.Counter` | ❌ absent | ✅ (plain dict repr) | ✅ |
| `subprocess.run` | ❌ no module | ❌ no module | ✅ |
| iterate `sys.stdin` | ✅ | ❌ no attribute | ✅ |
| `open(path).read()` | ✅ | ❌ needs 3 args | ✅ |
| `re.findall` | ❌ absent | ❌ **no `re` at all** | ✅ |
| `re.finditer` | ❌ absent | ❌ | ✅ |
| `re.split` | ❌ absent | ❌ | ✅ |
| `re.compile().match()` | ✅ | ❌ syntax error | ✅ |
| `re.sub` | ✅ `Xb` | ❌ | ✅ |
| `re.match` with groups | ✅ `42 foo` | ❌ | ✅ |
| genexp inside `join` | ✅ | ❌ syntax error | ✅ |
| f-string `{1+1:03d}` | ✅ | ✅ | ✅ |
| `"%s-%d" %` | ✅ | ✅ | ✅ |
| `json.load(sys.stdin)` | ✅ | ❌ | ✅ |
| `sys.argv` | ✅ | ✅ | ✅ |
| `os.listdir` / `glob` | ❌ `glob` absent (`os.listdir` ✅) | ❌ | ✅ |

MicroPython: 11/20 unmodified. pocketpy: 6/20.

Every MicroPython ❌ in that table is closeable, and §2.5 closed four of them
(`base64`, `os.path`, `re.findall`, `re.split`) in about 60 lines of frozen
Python at **negative** size cost. The remainder:

- `collections.Counter` — 10 lines, frozen.
- `glob` — 20 lines over `os.ilistdir`, frozen.
- `str.center` fill character — a frozen helper, or a small C patch.
- **`subprocess` — the one real hole.** MicroPython has `os.system` but no
  `subprocess`. For the observed corpus this is mostly acceptable (a one-liner
  that shells out could have been a shell pipeline), but it should be a stated
  non-goal rather than a surprise, and `bashAgentPrompt` should say so.

---

## 5. Startup latency, and whether a daemon earns its keep

### The extrapolated cold cost

The sandbox measurement gives one anchor: CPython's 9.7 MiB cold surface costs
8573 ms. Scaling by cold bytes — the dominant term — lypning-mp's 0.52 MiB predicts
roughly **450–500 ms cold**, plus the 50–85 ms exec floor. Call it **under
0.6 s against 8.6 s**, a ~15–19× improvement.

This is an **extrapolation, not a measurement**. It assumes cold cost is roughly
linear in bytes streamed, which the ratios in `SANDBOX-PERFORMANCE.md` support
but do not prove at this size. It must be confirmed by
the upstream Playwright battery against a real image before it is quoted as
fact. The direction is not in doubt; the constant is.

Warm, lypning-mp's own contribution is ~1 ms of a 50–85 ms round trip — i.e. **below
the noise floor of the thing that calls it.**

### The fork-server / zygote technique

The technique is well established and the brief asks for it, so here is what it
would involve and what it would buy.

The idea, in all its forms — Android's zygote, `multiprocessing.forkserver` with
`set_forkserver_preload()`, preforked application servers, `module-launcher` on
PyPI — is the same: pay interpreter initialisation and module import **once** in
a parent, then `fork()` per request so children inherit the initialised heap
copy-on-write. CPython's own `-X importtime` exists to find what to preload;
`python -S` is the cheap version of the same instinct (skip `site`), and it is
worth 42% of the file syscalls as measured in §2.4.

A unix-socket fork server that must behave like `python3 -c` has to get all of
the following right, and each is a place bugs live:

| concern | what the server must do |
|---|---|
| **fds** | Pass the client's stdin/stdout/stderr over `SCM_RIGHTS` and `dup2` them in the child. Without this the child writes to the daemon's console. |
| **cwd** | Send the client's cwd and `chdir()` in the child — relative paths are extremely common in one-liners. |
| **env** | Ship the full environment and `execle`-style replace it; inheriting the daemon's env silently changes `PATH`, `HOME`, locale. |
| **argv** | Ship `sys.argv` verbatim, including `argv[0]`. |
| **exit codes** | `waitpid()` in the daemon, send the status back, and have the thin client `exit()` with it. |
| **signals** | Reset handlers and the signal mask in the child (they are inherited), and forward SIGINT/SIGTERM from client to child. |
| **tty** | Decide whether the child is a session leader; `isatty()` must answer correctly or `input()` and progress output misbehave. |

### Verdict: the daemon does not earn its keep here

Three reasons, in order of force.

**1. There is nothing left to save.** The daemon's entire product is
"interpreter init, amortised". lypning-mp's interpreter init measures **0.96 ms
against a 0.92 ms empty-C-program floor** (§2.7). The daemon would amortise
0.04 ms. Meanwhile every one-liner still goes through `execInSandbox` →
`/bin/sh -c`, whose **50–85 ms floor the daemon cannot touch** — a socket
round-trip from a thin client is *inside* that envelope, not instead of it. The
daemon would be optimising 0.05% of the cost.

**2. The mechanism a daemon needs is broken in this guest.**
`SANDBOX-PERFORMANCE.md` records a direct probe: `timeout 2 sleep 60`,
`timeout -s KILL 2 sleep 60`, and an explicit `kill -9` on a known PID **all
fail** — "signal delivery and process termination are not functional in the
CheerpX guest". A fork server's core loop is fork, wait, reap, and forward
signals. Half of that does not work. It would also die with the VM on any
`resetSandbox`, so it would need re-establishing constantly.

**3. It would be the wrong fix for the right problem.** The daemon is genuinely
the correct answer *for CPython* — 8573 ms cold, 87 ms warm is exactly the
profile a zygote repairs. But it repairs it by keeping a 9.7 MiB working set
resident in a VM whose memory is the browser's, and it does nothing about the
first boot. Shipping a 541 KB static binary removes the problem instead of
amortising it.

**Where a daemon would still be justified:** if a future workload runs *many*
one-liners inside a single `execInSandbox` batch and the interpreter grows
expensive initialisation (a large frozen corpus, a loaded index). Neither is
true today. The honest recommendation is to **not build the daemon**, and to
revisit only if a measurement — not an intuition — shows interpreter init
appearing in a trace.

---

## 6. Recommendation

**Build lypning-mp as a MicroPython unix-port variant: static, musl, i386, with a
frozen lypning-mp stdlib and `sys.path` pinned to `['.frozen']`.** That is option
(a) — fork an existing tiny VM — with the fork kept as shallow as possible.

### Against the alternatives

**(b) A purpose-built C interpreter for the observed corpus.** The corpus is
narrow enough that this is not absurd, and it is the option with the most
appeal on paper. It is still wrong. Berry is the control experiment: a complete,
mature, well-tested dynamic-language VM with a frozen stdlib, written by people
who optimised for exactly this, lands at **365,660 bytes** — only 32% below
MicroPython's 541,688, which already speaks Python and already has `re` and
`json`. The realistic saving is a couple of hundred KB, against writing and then
*owning* a tokenizer, parser, bytecode VM, GC, `re` engine and `json` parser,
plus the semantics work of matching CPython closely enough that generated
one-liners do not silently produce wrong answers. The measured cold-cost
difference between 366 KB and 542 KB is on the order of 150 ms in a VM where the
exec floor is 50–85 ms. It does not buy back the risk.

**(c) Strip CPython.** This is the option that looks safest and is not. The
binary is 6.6 MB before stripping anything, it is dynamically linked against
3.1 MB of libraries, and its stdlib is files on disk — which is the actual
source of the 8573 ms. Freezing the stdlib means taking on machinery CPython
itself found unsatisfactory (deep-freeze was removed in 3.13 for being slow to
build and a poor build-system fit), and the end state is still ~10× lypning-mp's
size with full semantics we do not need. The one genuinely cheap win from this
family is worth stealing regardless of what we ship: **`-S`** cuts CPython's
startup file syscalls 109 → 63, so while `python3` remains in the image the
agent prompt should prefer `python3 -S -c`.

### What "done" looks like

1. **A variant, not a fork.** `ports/unix/variants/lypning-mp/` — a
   `mpconfigvariant.h` enabling `MICROPY_PY_RE_MATCH_SPAN_START_END` and
   `MICROPY_PY_RE_MATCH_GROUPS`, a `manifest.py` freezing the shim modules, and
   the `sys.path` pin. Tracking upstream stays a rebase, not a merge.
2. **The shim stdlib**, frozen: `base64` (over `binascii`), `os.path`, `glob`,
   `collections.Counter`, and `re.findall`/`finditer`/`split` (over
   `re.search` with spans enabled). §2.5 has working versions of four of these.
3. **The size gate.** Not <400 KB — that number was set before the floor was
   known, and it is unreachable for anything with `re` and `json` (Berry, with
   *neither*, is 366 KB). **Set the gate at 700 KB, static, i386, stripped.**
   The prototype sits at 542 KB, leaving room for the shims and a `re` engine
   improvement. The gate is only meaningful with musl: glibc-static spends
   636 KB of it on an empty `main`.
4. **A CI job that builds and runs the real artifact.** This container proves it
   is possible without Docker, a VM, or a cross toolchain: install
   `gcc-multilib`, build musl for i386 from source with the two flag fixes in
   §2.1, build the variant, and **execute it** — i386 binaries run here. Network
   is needed once, for the musl tarball and the MicroPython checkout; both are
   pinnable. Of the routes considered, this is the only one that works in this
   session: `musl-cross-make` needs a full GCC bootstrap (long, and unnecessary
   once `gcc -m32` exists), `apk` in a chroot and "build against the Alpine
   image" both need root plus loopback mounts, prebuilt `i686-linux-musl`
   toolchains mean trusting a third-party binary, and **the Docker daemon is not
   available here.** Building musl from source took under a minute.
5. **Live verification before the claim.** The 450–500 ms cold figure in §5 is
   an extrapolation. Confirm it with the upstream Playwright battery against a
   real Alpine i386 image, cold and warm, before it goes in any user-facing
   copy.
6. **Then edit `PKGS_COMMON`.** Replacing `python3` removes 27.0 MiB and 16
   shared-library dependencies from an image whose whole design goal is to be
   small and to stream without stalling. Whether lypning-mp is installed *as*
   `python3` or beside it is a real decision with a real cost: aliasing it means
   every generated `python3 -c` one-liner gets the subset and fails differently
   on `subprocess` and `glob`; not aliasing it means the model must be told the
   binary is called `lypning-mp`. **Alias it**, and state the subset in
   `bashAgentPrompt` — the alternative is a `command -v python3` that finds
   nothing, which §1 shows is the single most expensive failure mode in this VM.

### What this does not fix

Cold *boot* still dominates a sandbox turn. The agent trace in
`SANDBOX-PERFORMANCE.md` shows 24.4 s of VM boot against 290 ms of commands, so
lypning-mp improves a real but secondary term. It is worth doing because it is
cheap, because 8.5 s is a large secondary term, and because 27 MiB out of the
image helps the boot too — not because it makes the sandbox fast.

---

## Sources

Measured here: §2 in full. Read, not measured:

- Cost model and the CheerpX signal-delivery probe: `docs/SANDBOX-PERFORMANCE.md`
- i386-only constraint and the image recipe: `docs/SANDBOX-LOCAL-IMAGE.md`, `scripts/build-sandbox-image.sh`
- Engine decision: `docs/JS-VM-RESEARCH.md`
- [MicroPython](https://github.com/micropython/micropython) · [pocketpy](https://github.com/pocketpy/pocketpy) · [Berry](https://github.com/berry-lang/berry) · [Snek](https://github.com/keith-packard/snek) · [PikaPython](https://github.com/pikasTech/PikaPython)
- [RustPython WASM binary size (issue #4203)](https://github.com/RustPython/RustPython/issues/4203)
- CPython freezing: [bpo-45020 freeze startup modules](https://bugs.python.org/issue45020) · [bpo-45661 freeze commonly used stdlib](https://bugs.python.org/issue45661) · [deep-freeze removal, issue #108716](https://github.com/python/cpython/issues/108716) · [`Programs/_freeze_module.c`](https://github.com/python/cpython/blob/main/Programs/_freeze_module.c)
- Fork-server prior art: [`multiprocessing/forkserver.py`](https://fossies.org/linux/Python/Lib/multiprocessing/forkserver.py) · [forkserver preload](https://bnikolic.co.uk/blog/python/parallelism/2019/11/13/python-forkserver-preload.html) · [Yelp zygote](https://github.com/YelpArchive/zygote) · [module-launcher](https://pypi.org/project/module-launcher/)
- Alpine package sizes: `http://dl-cdn.alpinelinux.org/alpine/latest-stable/main/x86/APKINDEX.tar.gz` (fetched 2026-08-13)
