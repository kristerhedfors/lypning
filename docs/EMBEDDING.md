# Embedding lypning

**What this is.** `liblypning` is the same runtime the `lypning` binary is,
linked into your process instead of spawned. Your harness calls a function; the
program runs in your thread; its stdout comes back as bytes. There is no fork,
no exec, no pipe and no serialisation, which is the whole reason to do it: on
the programs lypning accepts, the process was 96% of the cost.

**What it is not.** It is not a Python. It runs the bottom slice of Python that
a coding agent actually types, and it **refuses** everything else — by design,
loudly, and cheaply. The refusal is the product. Everything below is about
handling it correctly.

---

## 1. The critical requirement

```c
lypning_result *r = lypning_run(q);
if (lypning_result_should_fall_onward(r)) {
    run_on_python3(src);                 /* your existing path, unchanged */
} else {
    use(lypning_result_stdout(r, &n), lypning_result_exit_code(r));
}
lypning_result_free(r);
```

`LYPNING_UNSUPPORTED` **is not an error.** It means the program is outside the
subset, that lypning executed none of it, and that CPython should now answer.
A harness that surfaces a refusal to its user as a failure has turned a speedup
into a bug — and it will do so silently, because the program was fine.

This is CLAUDE.md invariant 1 seen from the outside: lypning is allowed to be
small precisely because refusing is free and always safe. Take the refusal away
and the whole design stops being sound.

**Why it is safe to run the program again elsewhere.** A refused run is
observably a no-op. Output is staged in memory and thrown away, file writes are
staged per path and never reach the disk, stdin is replayable because the host
supplied it as bytes. That is the commit barrier (`assets/rust/src/io.rs`), and
`lypning_result_committed()` says whether a run passed the point where its
effects stop being reversible — true for anything that finished, whether or not
it wrote a file, and for the one effect the barrier cannot stage: a directory
created with `os.mkdir`, which has no content to hold back. (`os.makedirs(...,
exist_ok=True)` is idempotent, so it stays routable — a retry makes the same
directory and carries on.)

`should_fall_onward()` folds that in, which is why it is the call to branch on
rather than the status. It is true for three outcomes, not one: a refusal, a
`BUSY` that executed nothing, and a `PANIC` that reached no commit. All three
mean the same thing — lypning did not answer, and the program still needs one.

## 2. Capabilities and costs

Measured on this machine (4 CPUs, Linux 6.18.5) on **2026-08-20**, against a
corpus capture that had grown to **842 programs, 528 of them routed to lypning**.
Timing is `subprocess.run` against a direct call from Python — *not* the method
`lypning bench` uses, so these numbers are not comparable to the ones in
`docs/BENCH-LEDGER.md`. **Re-measure rather than quoting this.**

```
the empty program `pass` — min of 200 calls / 20 spawns

  library     0.0071 ms      in-process call
  binary      0.2547 ms      spawn + exec + reap
  cpython    11.1235 ms

150 corpus programs lypning accepts, one pass each

  library       12.2 ms      0.006x cpython
  binary        88.7 ms      0.043x cpython
  cpython     2079.8 ms      1.000x
```

A second run of the same script on the same box, an hour later, put `pass` at
0.0072 ms through the library and 0.2576 ms spawned — and CPython at 16.6 ms
rather than 11.1, which moved every ratio with it (0.007x and 0.029x on the
corpus arm). **The two library numbers agree to a tenth of a percent and the
CPython baseline moved by half.** That is the whole reason this section names
its method and its date instead of quoting a ratio: the in-process cost is
stable because there is nothing in it but a function call, and everything it is
being compared against is a process, which is what the machine's load actually
moves.

Read it as one claim only: **linking removes the process, and the process was
the cost.** It says nothing about programs lypning refuses — those still cost a
CPython spawn, plus a few microseconds to be told so.

## 3. Building and linking

```bash
lypning build --lib          # the shared library, liblypning.a, and the headers
lypning lib                  # where they went, and the exact cc line
cc $(lypning lib --cflags) my_harness.c $(lypning lib --libs)
```

The artefacts are the shared library (`liblypning.so` on Linux,
`liblypning.dylib` on macOS), the static archive `liblypning.a`, and the two
headers, `lypning.h` and `lypning.hpp`. On macOS the shared library is built
with an `@rpath` install name (`assets/rust/build.rs` says why), so the
`-rpath` a host links with is what decides where it is found at load time, and
the `-Wl,-rpath` in `lypning lib --libs` is not decoration.

The library is built for the **host** target, not the static-musl target the
binary uses: a shared object has to match the libc of the process that loads it,
so a musl build would be unloadable by every glibc host. `--target musl` is
still available for a host that is itself musl.

It is also built with a different cargo profile — `release-lib`, which differs
from `release` in exactly one setting, `panic = "unwind"`. That is not a
preference. `abort` is right for a binary, where there is nothing to save, and
catastrophic for a library, where it kills an application that only asked to run
a one-liner. Every C ABI entry point catches at the boundary, and `capi.rs`
refuses to compile under `abort` rather than trusting a comment.

**If you link the static archive** into a host built with `panic = "abort"` or
`-fno-exceptions`, you take that guard away again — the strategy is chosen by
the final link, not by our crate. The shared object is the supported artefact.

## 4. The hosts, one ABI

This table is the one place the hosts are counted. Everything else in the
repository says "every host" and points here, so the next binding is one row
and not fifteen edits. Paths are under `src/lypning/` in a checkout and under
the package's `assets/` in a wheel; the run column is written from the
repository root, after `lypning build --lib`. Every row finds a checkout's
library by itself; the Python row imports the package, so in a checkout that
has not been `pip install`ed it wants `PYTHONPATH=src` in front.

| host | binding | quickstart | contract test | build and run |
|---|---|---|---|---|
| C | `assets/include/lypning.h`, the ABI itself | `assets/examples/c/quickstart.c` | `assets/examples/c/embed.c` (`make -C src/lypning/assets/examples/c run`) | `make -C src/lypning/assets/examples/c quickstart && src/lypning/assets/examples/c/quickstart "print(sum(range(10)))"` |
| C++ | `assets/include/lypning.hpp`, header-only RAII over the ABI | `assets/examples/cpp/quickstart.cpp` | `assets/examples/cpp/embed.cpp` (`make -C src/lypning/assets/examples/cpp run`) | `make -C src/lypning/assets/examples/cpp quickstart && src/lypning/assets/examples/cpp/quickstart "print(sum(range(10)))"` |
| Rust | the crate directly, no FFI: `lypning::run`, `Request::new`, `Outcome::should_fall_onward` | `assets/examples/rust/examples/quickstart.rs` | `assets/examples/rust/src/main.rs` (`cargo run --release`) | `cargo run --release --manifest-path src/lypning/assets/examples/rust/Cargo.toml --example quickstart -- "print(sum(range(10)))"` |
| Node | `assets/node/`, a Node-API addon with no npm dependencies | `assets/node/quickstart.js` | `assets/node/example.js` (`npm test`) | `cd src/lypning/assets/node && cargo build --release && node quickstart.js "print(sum(range(10)))"` |
| Python | `lypning.embed`, `ctypes`, stdlib only | `assets/examples/python/quickstart.py` | `tests/test_embed.py` | `python3 src/lypning/assets/examples/python/quickstart.py "print(sum(range(10)))"` |
| Go | `assets/go/`, cgo over the unchanged header, zero modules | `assets/go/quickstart/main.go` | `assets/go/lypning_test.go` (`go test`) | `cd src/lypning/assets/go && go run ./quickstart "print(sum(range(10)))"` |
| Swift | `assets/swift/`, a Clang module map over the header (SwiftPM, or plain `swiftc` via its Makefile) | `assets/swift/Sources/quickstart/main.swift` | `assets/swift/Tests/LypningTests` (`swift test`) | `swift build -c release --package-path src/lypning/assets/swift && src/lypning/assets/swift/.build/release/quickstart "print(sum(range(10)))"` |
| LuaJIT | `assets/lua/lypning.lua`, LuaJIT `ffi` over the header, read at load; no build step | `assets/lua/quickstart.lua` | `assets/lua/test.lua` (`luajit test.lua`) | `luajit src/lypning/assets/lua/quickstart.lua "print(sum(range(10)))"` |

The C ABI is the real API; every other row is a convenience over it. Add a
capability there and it exists everywhere; add it anywhere else and the hosts
quietly disagree.

**The quickstart contract.** Every quickstart is `quickstart "<python source>"
[args...]`: it runs the program in-process with a 10M step limit, and the
arguments become `sys.argv[1:]`. If `should_fall_onward` is true it runs
`python3 -c` exactly once and exits with CPython's code. Otherwise it writes the
program's stdout and stderr bytes and exits with the program's own code, so a
traceback is exit 1 and is never retried. Five probes cover the contract, and
every host answers them byte for byte the same:

```
quickstart "print(sum(range(10)))"                 45
quickstart "import subprocess; print(1)"           1, via CPython, once
quickstart "import sys; print(sys.argv[1:])" a b   ['a', 'b']
quickstart "print(1/0)"                            traceback on stderr, exit 1, not retried
quickstart "import sys; sys.exit(3)"               exit 3
```

Where a host differs from the rest, it is for one reason each:

*Rust* has no FFI to cross. The example crate compiles the runtime from source
and links no library, so `lypning build --lib` is not a prerequisite and the
`Outcome` it branches on is the same struct the C ABI wraps.

*Node* is a separate `cargo build` in `assets/node/`: the addon links the
runtime statically, so `lypning build --lib` does not produce it and does not
have to have run.

*Go* finds a checkout's library through the binding's own `#cgo` directives.
Against an installed library it needs `CGO_LDFLAGS="$(lypning lib --libs)"`,
which carries the `-rpath` the binary will need at load time. `go run` is fine
for the first probe and wrong for the last two: it reports every non-zero exit
as 1 with an `exit status N` line of its own, so the contract test and CI
`go build` the binary and run that.

*Swift* passes the library path as `unsafeFlags`, which SwiftPM refuses in a
dependency, so the package is consumed in-tree or by `.package(path:)`. The
Makefile beside it is the same binding through plain `swiftc`, and writes no `.build` directory.

*LuaJIT* reads `lypning.h` at load and hands it to `ffi.cdef`, so there is one
source of truth for the prototypes and no build step. PUC Lua has no `ffi` and
is refused with a message that says so.

**They have been run against each other rather than assumed to agree.** The
prompting study ([PROMPTING.md](PROMPTING.md) §7) drove every host of that date
(C, C++, Rust, Node, Python) over one shared set of 393 agent-written programs
on 2026-08-23, each host with its own copy of the set and each program with its
own working directory, and all of them answered identically: 341 ran, 52
refused, 0 other. On **2026-09-02**, on macOS arm64 (clang, cargo, node 26, go 1.26,
swift 6.3, luajit 2.1), the same battery over every row of the table above
printed:

```
c-embed      393 programs: 341 ran, 52 refused, 0 other
cpp-embed    393 programs: 341 ran, 52 refused, 0 other
rust-embed   393 programs: 341 ran, 52 refused, 0 other
node-embed   393 programs: 341 ran, 52 refused, 0 other
python-embed 393 programs: 341 ran, 52 refused, 0 other
go-embed     393 programs: 341 ran, 52 refused, 0 other
swift-embed  393 programs: 341 ran, 52 refused, 0 other
lua-embed    393 programs: 341 ran, 52 refused, 0 other
```

3144 capture records, 393 per host, and `git status` unchanged afterwards. That
includes the refusal path, which is the half that has only ever broken
silently; a disagreement there is a binding bug and nothing else in the project
would notice one. The recipe, from a checkout with the toolchains on `PATH`:

```bash
lypning build --lib                               # the library every driver but Rust and Node loads
export LYPNING_LOG=/tmp/lypning-study-log.jsonl   # the drivers append the capture record themselves
python3 study/hosts/prepare.py                    # lay out the shared program set
make -C study/hosts                               # C, C++, Rust, Go, Swift drivers and the Node addon
sh study/hosts/run_all.sh                         # every host, one summary line each
git status                                        # the programs ran behind a net; check anyway
```

**What the ABI does not have is a capture hook.** A `lypning_run()` spawns no
interpreter, so neither of the capture feeds in [CAPTURE.md](CAPTURE.md) can see
it, and a harness that wants its programs to reach the corpus has to write the
record itself. `study/hosts/capture.h` is a working example in about forty lines
of C and argues that the right home for it is here, where every host would
inherit it at once.

### The branch, per host

The same four lines in each quickstart, copied from the files rather than
paraphrased, C first. What they share is the shape: test `should_fall_onward`,
hand a refusal to `python3 -c` once, and otherwise return the program's own
bytes and code.

```c
    if (r == NULL || lypning_result_should_fall_onward(r)) {
        /* A refusal is not an error: lypning ran none of it and wrote nothing, so CPython runs it once. */
        /* ... */
        execvp("python3", py);
```

```cpp
    if (r.should_fall_onward()) {
        // A refusal is not an error: lypning ran none of it and wrote nothing,
        // so CPython runs it once, on the same empty stdin lypning was given.
        // ...
        execvp(python3, cargv.data());
```

```rust
    if r.should_fall_onward() {
        // A refusal is not an error: lypning ran none of it and wrote nothing, so CPython runs it once.
        let _ = std::io::stdout().flush();
        let status = Command::new("python3")
```

```js
if (r.fallOnward) {
  // A refusal is not an error: lypning ran none of it and wrote nothing, so CPython runs it once.
  const p = spawnSync('python3', ['-c', src, ...args], { stdio: ['ignore', 'inherit', 'inherit'] });
  process.exit(p.status ?? 1);
```

```python
    if out.fall_onward:
        # A refusal is not an error: lypning ran none of it and wrote nothing, so CPython runs it once.
        sys.stdout.flush()
        return subprocess.run([sys.executable, "-c", src, *rest], stdin=subprocess.DEVNULL).returncode
```

```go
    if r.FallOnward {
        // A refusal is not an error: lypning ran none of it and wrote nothing, so CPython runs it once.
        cmd := exec.Command("python3", append([]string{"-c", src}, args...)...)
```

```swift
if r.fallOnward {
    // A refusal is not an error: lypning ran none of it and wrote nothing, so CPython runs it once.
    // ...
    execvp("python3", cargv)
```

```lua
if r.fall_onward then
  -- A refusal is not an error: lypning ran none of it and wrote nothing, so CPython runs it once.
  -- ...
  local status, how, code = os.execute(cmd .. " </dev/null")
```

## 5. Rules of the API surface

* **A run belongs to one thread.** Two threads may run two programs at once —
  the state is thread_local. One thread may not run two at once, and gets
  `LYPNING_BUSY` rather than two interleaved answers.
* **Program output never touches your stdio.** It is captured, always. Stdin is
  bytes you supply; unset means an empty stream, never your fd 0. One exception,
  and it is not the program's: an interpreter bug prints Rust's own panic
  message to fd 2 before the unwind is caught. Silencing that means installing a
  process-global panic hook, which is not a library's to install over yours.
* **Handles are opaque and freed by their own `_free`.** Returned pointers die
  with their handle. Program output is bytes with a length, because a program's
  stdout is whatever it printed.
* **The program sees your process's environment and working directory.** There
  is nothing else it could see: `chdir` and `environ` are process-wide. If a
  program must run somewhere specific, put your process there, or use the
  binary.

## 6. Bounding an untrusted program

Every program a coding harness runs was written by a language model, so
"do not run untrusted programs" is not advice a harness can follow.

**`lypning_request_set_step_limit(q, n)` is the bound.** There is no process to
kill: `while True: pass` inside your own thread is a hang with no timeout, no
signal and no way back. The counter ticks on every statement *and* every
iterator advance — so `sum(range(10**12))`, which is one statement, is bounded
too — and passing it is a refusal, which routes the program to CPython under
whatever timeout you already apply to spawning it.

It bounds **work, not time**. It is not a wall-clock timeout and does not
pretend to be one.

**`lypning_request_set_output_limit(q, n)`** refuses rather than growing a
buffer in your address space. **`lypning_request_set_filesystem(q, 0)`** turns
every file operation into a refusal — never into a lie. A denied program is not
told the file is missing, which would be a wrong answer at exit 0; you are told
lypning would not run it, and you decide whether CPython gets it. Policy belongs
to the host, not to the runtime.

## 7. Remaining failure modes

Honesty first: a stack overflow is not an unwind, so no guard at the ABI
boundary can catch one. Everything below was reachable from ordinary program
text, was measured crashing the process, and is now a refusal:

| what | now |
|---|---|
| deeply *nested* expressions (`((((…`) | `unsupported: recursion`, past 64 levels |
| a long *flat* chain (`1+1+1+…`) | `unsupported: recursion`, past 1,000 operators — not the same limit, because a chain does not nest: it is one node per term down a left-leaning spine, which the evaluator and the AST's own destructor each walk one stack frame at a time |
| `repr`, `==`, `<`, `in`, `sorted`, a tuple dict key, `json.loads`, `json.dumps` over a deeply nested value | `unsupported: recursion`, past 500 |
| an allocation nothing can satisfy (`"a" * 10**14`) | `unsupported: alloc` — Rust's allocator failure handler *aborts*, which is not an unwind, so the size is ceilinged before it is asked for |
| a NUL byte in the source | `unsupported: source` — the lexer reads zero as end-of-input, so this silently ran half a program and reported success |
| tearing down what a program built | taken apart iteratively; a million-level list is fine |
| a bug in the interpreter | `LYPNING_PANIC`, caught at the boundary |

The 64 is a measurement, not a taste: the deepest program in the corpus (842
loaded, 2026-08-20) nests **18**, the 99th percentile nests 11 and the median
nests 2, counting `(`, `[` and `{` alike, which is what the parser's own guard
counts. Every limit above is sized for a **host thread**, not a main one — a
Node worker or a pthread default hands the runtime a fraction of the 8 MB the
main thread gets, and a guard tuned on the main thread's stack is a guard that
holds only where nobody embeds. `tests/test_embed.py` runs the deep cases on a
1 MB thread on purpose.

The last row is worth stating plainly: `LYPNING_PANIC` means the runtime failed,
not your program. Report it — and route it onward, which
`should_fall_onward()` will already be telling you to do.

## 8. Verification

The library is not trusted because it shares an interpreter with the binary —
it shares the interpreter but not the exit path, and the exit path is the part
that has only ever broken silently. So:

* `lypning build --lib` asserts the refusal contract **through the ABI** before
  it reports `ok` — status, exit code, an empty stdout, the exact one line, and
  a request to be routed onward. A build that cannot demonstrate all five is not
  `ok`.
* `lypning conformance --engine library` runs the whole corpus through the
  library in-process and scores it against CPython by exactly the rules the
  spawned arms are scored by. MISMATCH must be 0, and the arm must agree with
  the `lypning` arm program for program.
* `tests/test_embed.py` pins what only a library can get wrong: state leaking
  from one run into the next, a latched commit flag turning the second run's
  refusal into an error, and every program in §7 — the deep ones twice, once on
  the main thread and once on a 1 MB one. The last row of that table is the
  exception and is honest about it: no program is known that panics the
  interpreter, so what the suite pins there is the routing, not a crash.
* `tests/test_hosts.py` drives every quickstart in §4 through the five probes
  and compares the bytes per host, per probe, so the bindings are checked
  against each other and not only against the header; CI runs the same
  quickstarts on Linux and macOS. A host missing its toolchain is skipped and
  named, never failed.

`lypning doctor` reports the first of those on whatever library is installed.
