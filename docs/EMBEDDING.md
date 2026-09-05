# Embedding lypning

**What this is.** `liblypning` is the runtime linked into your process instead
of spawned. It is the **largest** spectrum variant, `lypning-l` —
`build.build_lib` passes `--features capi,variant-l`
(`build.variant_feature(engines.SPECTRUM[-1])`) — so its refusal line begins
`lypning-l: unsupported:` and `lypning_engine_self()` reads the name
(`embed.REFUSAL_LINE`). It **refuses** everything outside the slice of Python a
coding agent types; the checks are `docs/VERIFICATION.md` §C14.

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

`LYPNING_UNSUPPORTED` is not an error: lypning executed nothing and the program
needs CPython; a harness that reports it as a failure fails silently on a
correct program (`CLAUDE.md` invariant 1). The commit barrier (`io.rs`) makes a
refused run a no-op — output and writes staged, stdin replayable;
`lypning_result_committed()` is true once a run finished or made a directory
with `os.mkdir`, the one effect it cannot stage.

`should_fall_onward()` folds that in, which is why it is the call to branch on
rather than the status. It is true for three outcomes, not one: a refusal, a
`BUSY` that executed nothing, and a `PANIC` that reached no commit. All three
mean the same thing — lypning did not answer, and the program still needs one.

## 2. Capabilities and costs

Measured upstream on 2026-08-20; not reproducible from this tree.

```
upstream container · 2026-08-20 · 4 CPUs, Linux 6.18.5 · min of 200 calls / 20 spawns · subprocess.run vs a direct call from Python, not lypning bench's method
the empty program `pass`              library  0.0071 ms   binary  0.2547 ms   cpython  11.1235 ms
150 corpus programs lypning accepts   library    12.2 ms   binary    88.7 ms   cpython   2079.8 ms
`pass` again, one hour later          library  0.0072 ms   binary  0.2576 ms   cpython     16.6 ms
```

The in-process number is stable; every process baseline moves with load, so
re-measure and do not quote a ratio. A refusal costs a CPython spawn as before.

## 3. Building and linking

```bash
lypning build --lib          # the shared library, liblypning.a, and the headers
lypning lib                  # where they went, and the exact cc line
cc $(lypning lib --cflags) my_harness.c $(lypning lib --libs)
# → each exits 0 once built; before that `lypning lib` exits 2 with the `build --lib` hint on stderr
```

The artefacts are the shared library, the static archive `liblypning.a`, and
`lypning.h` and `lypning.hpp` from `src/lypning/assets/include/`. On macOS the
shared library carries an `@rpath` install name (`assets/rust/build.rs`), so
the `-Wl,-rpath` in `lypning lib --libs` is load-bearing. It is built for the
host target, not the binary's static-musl one, under the profile `release-lib`
(`Cargo.toml`: `release` plus `panic = "unwind"`); every entry point catches at
the boundary, `capi.rs` refuses to compile under `abort`, and a host linking
`liblypning.a` with `panic = "abort"` or `-fno-exceptions` loses that guard.

## 4. The hosts, one ABI

This table is the one place the hosts are counted
(`tests/test_docs.py::test_every_host_quickstart_is_documented`); paths are
under `src/lypning/`.

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

The C ABI is the real API; every other row is a convenience over it, branching
on one predicate: `should_fall_onward`, `fallOnward`, `fall_onward`.

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

Where a host differs it is for one reason each: *Rust* compiles the runtime
from source and links no library; *Node* is its own `cargo build`; *Go* needs
`CGO_LDFLAGS="$(lypning lib --libs)"` against an installed library, and `go
run` reports every non-zero exit as 1, so the contract test `go build`s;
*Swift* needs `unsafeFlags`, so in-tree or `.package(path:)`; *LuaJIT* reads
`lypning.h` into `ffi.cdef` at load; PUC Lua is refused.

The hosts are run against each other, not assumed to agree (the five-host run
of 2026-08-23 is `PROMPTING.md` §7) — on 2026-09-02, on macOS arm64 (clang,
cargo, node 26, go 1.26, swift 6.3, luajit 2.1):

```
study/hosts/run_all.sh · 2026-09-02 · macOS arm64 · 393 programs per host · git status unchanged
c-embed cpp-embed rust-embed node-embed python-embed go-embed swift-embed lua-embed — each: 393 programs: 341 ran, 52 refused, 0 other
```

```bash
lypning build --lib                               # the library every driver but Rust and Node loads
export LYPNING_LOG=/tmp/lypning-study-log.jsonl   # the drivers append the capture record themselves
python3 study/hosts/prepare.py                    # lay out the shared program set
make -C study/hosts                               # C, C++, Rust, Go, Swift drivers and the Node addon
sh study/hosts/run_all.sh                         # every host, one summary line each
git status                                        # the programs ran behind a net; check anyway
# → the per-host summary lines above, exit 0, and an empty `git status --porcelain`
```

The ABI has no capture hook: `lypning_run()` spawns nothing, so neither feed in
[CAPTURE.md](CAPTURE.md) sees it; `study/hosts/capture.h` appends the record.

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

The symbol-by-symbol contract of `src/lypning/assets/include/lypning.h` is the
`abi` section of `tests/verification/claims.json`, checked against the header
by `tests/test_verification.py::test_every_claim_map_entry_resolves`: the ABI
version (`LYPNING_ABI_VERSION`, 1) for a `dlopen` host, the five statuses and
the exit code each carries (90 is `LYPNING_UNSUPPORTED_EXIT`), the setters' `0`
/ `-1` returns, `lypning_run(q)` NULL only if `q` is, `lypning_route_*` (one
parse, no execution), and the dispatcher's predicate `lypning_fall_onward`
(`tests/test_embed.py::test_fall_onward_matches_the_dispatchers_rule`).

## 6. Bounding an untrusted program

`lypning_request_set_step_limit(q, n)` is the bound: there is no process to
kill, so `while True: pass` in your thread is a hang with no way back. It ticks
on every statement and iterator advance (`sum(range(10**12))` is bounded too);
passing it is the refusal `unsupported: steps` (`eval.rs`), routable to CPython.

It bounds **work, not time**. It is not a wall-clock timeout and does not
pretend to be one.

**`lypning_request_set_output_limit(q, n)`** refuses rather than growing a
buffer in your address space. **`lypning_request_set_filesystem(q, 0)`** turns
every file operation into a refusal — never into a lie. A denied program is not
told the file is missing, which would be a wrong answer at exit 0; you are told
lypning would not run it, and you decide whether CPython gets it. Policy belongs
to the host, not to the runtime.

Their kinds are `unsupported: output` and `unsupported: sandbox` (`io.rs`;
`tests/test_embed.py::test_denying_the_filesystem_refuses_rather_than_lying`).

## 7. Remaining failure modes

A stack overflow is not an unwind, so no guard at the ABI boundary can catch
one; each row below was measured crashing the process and is a refusal:

| what | refusal |
|---|---|
| deeply *nested* expressions (`((((…`) | `unsupported: recursion`, past 64 levels |
| a long *flat* chain (`1+1+1+…`) | `unsupported: recursion`, past 1,000 operators — not the same limit, because a chain does not nest: it is one node per term down a left-leaning spine, which the evaluator and the AST's own destructor each walk one stack frame at a time |
| `repr`, `==`, `<`, `in`, `sorted`, a tuple dict key, `json.loads`, `json.dumps` over a deeply nested value | `unsupported: recursion`, past 500 |
| an allocation nothing can satisfy (`"a" * 10**14`) | `unsupported: alloc` — Rust's allocator failure handler *aborts*, which is not an unwind, so the size is ceilinged before it is asked for |
| a NUL byte in the source | `unsupported: source` — the lexer reads zero as end-of-input, so this silently ran half a program and reported success |
| tearing down what a program built | taken apart iteratively; a million-level list is fine |
| a bug in the interpreter | `LYPNING_PANIC`, caught at the boundary |
| a legal recursive function, deep | `unsupported: recursion`, past 180 call frames (`eval.rs:MAX_DEPTH`) |
| one expression evaluated deep | `unsupported: recursion`, past 600 levels (`eval.rs:MAX_EXPR_DEPTH`), sized against `MAX_DEPTH` on a 1 MB host thread |

The constants: `parse.rs:MAX_PARSE_DEPTH` (64), `parse.rs:MAX_CHAIN_OPS`
(1,000), `err.rs:MAX_NEST` (500); every limit is sized for a **host** thread
with a 1 MB floor (`threading.stack_size(1 << 20)` in
`tests/test_embed.py::test_deep_programs_stay_refusals_on_a_small_host_stack`).

## 8. Verification

The library shares the interpreter with the binary but not the exit path, so it
is verified on its own. `lypning build --lib` asserts the refusal contract
through the ABI before `ok` — status, exit code, empty stdout, the one line
headed `lypning-l:`, a request to be routed onward
(`embed.check_refusal_contract`); the log line is `unsupported contract
(in-process): held`, or `BROKEN — <why>` on a failure, printed under `-v` or in
`--json`. `lypning lib --json` (keys in §C14) exits 2 with nothing on stdout
when no library is built or `$LYPNING_LIB` names a missing one
(`embed.LibraryError`). `lypning doctor` has two library rows: `library
refusal`, and `core/library agreement` (`engines.library_binary_drift` over
`engines.DRIFT_PROBES` — FAIL when `build --rust` without `--lib` left a library
from an older tree; a hole, never OK, with an artefact absent). `lypning
conformance --engine library` is opt-in (`conformance.OPT_IN_ARMS`), scored
against CPython by the spawned arms' rules with `conformance.LIBRARY_STEP_LIMIT`
graded UNSUPPORTED, never MISMATCH, and serialised under `engines._CHDIR_LOCK`;
it is not compared with the `lypning` arm program for program — `lypning-l`
legitimately runs programs `lypning` refuses, and binary/library agreement is
the doctor row. `tests/test_embed.py` pins the library-only failure modes
(leaked state, a latched commit flag, the deep programs of §7 on a 1 MB thread,
BUSY and PANIC routable);
`tests/test_commit_barrier.py::test_rust_core_refuses_with_stdout_untouched` the
barrier; `tests/test_hosts.py::test_quickstart` every §4 host over the five
probes, bytes compared per host (a host missing its toolchain is skipped).

```bash
lypning build --lib -v; echo $?   # → … unsupported contract (in-process): held … ok · 0 (the contract line is in the build log: `-v` or `--json`)
lypning lib --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["abi"], d["cli_abi"])'   # → 1 1
lypning doctor | grep -E 'library refusal|core/library'   # → OK … falls onward · OK … frontier probes answer alike in both artifacts
```

The run of record's bytes for each are `docs/VERIFICATION.md` §C14.
