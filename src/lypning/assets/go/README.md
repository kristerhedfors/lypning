# lypning for Go

Runs the bottom slice of agent-typed Python, the one-liners, *inside* this Go
process: no child process, no pipe, no serialisation. Everything else it
REFUSES, and the refusal is the design, not a failure.

```go
import "lypning.dev/lypning"

r := lypning.Run(src, &lypning.Options{StepLimit: 10000000}) // no process to kill
if r.FallOnward {
	runOnPython3(src) // lypning ran NOTHING; your existing path, unchanged
} else {
	use(r.Stdout, r.Stderr, r.ExitCode) // []byte; a traceback IS the answer: exit 1
}
```

`FallOnward` is true exactly when lypning declined *and left nothing behind*:
nothing printed, no file touched, no stdin consumed, which is what makes the
retry safe. `Run` never returns an error and never panics for a program; a
refusal is a `Result` with `Kind`/`Detail` saying what it declined, and so is a
`src` or an argument that is not UTF-8 (the ABI will not build a request for
one, and that too is CPython's to reject with its own message). `Route(src)`
asks without running. `quickstart/main.go` is the complete minimal host, and
the file to copy.

## Building

This is cgo over the unchanged C ABI in `../include/lypning.h`, so it needs a C
compiler on `$PATH` and `CGO_ENABLED=1` (Go's default when a compiler is
present; cross-compiling turns it off). Zero Go dependencies: `go.mod` has no
`require` line and never will.

Where the library is depends on which of the repository's two shapes you have.

**A source checkout.** Build the library once, then nothing else is needed:
the `#cgo` directives point at the crate's own `release-lib` target.

```sh
lypning build --lib
cd src/lypning/assets/go && go test ./... && go run ./quickstart "print(sum(range(10)))"
```

**An installed library** (a `pip install`, where `assets/` is read-only and
`lypning build --lib` put the library under `~/.lypning/lib`), or any other
location: say where through `CGO_LDFLAGS`, which cgo appends after the
directives.

```sh
CGO_LDFLAGS="$(lypning lib --libs)" go build ./...
CGO_LDFLAGS="-L<dir>/rust/target/release-lib -Wl,-rpath,<dir>/rust/target/release-lib" go build ./...
```

`lypning lib --libs` carries `-Wl,-rpath` on purpose: without it the binary
links cleanly and dies at exec, because nothing runs `ldconfig` over
`~/.lypning/lib`.

The checkout default stays in the directives either way, and costs nothing
when its directory is absent: GNU ld ignores a `-L` it cannot find, and macOS
ld says so (`ld: warning: search path '...' not found`) and then links exactly
as it would have. macOS ld also notes that cgo hands it `CGO_LDFLAGS` twice
(`duplicate -rpath`, `ignoring duplicate libraries`); those are Go's, not
lypning's, and none of the three changes which library the binary loads. In a
checkout the build is warning-free.

**When the library is absent** the link fails with the toolchain's own words,
`ld: library 'lypning' not found` (macOS) or `cannot find -llypning` (GNU ld).
Both mean the same thing: run `lypning build --lib`, and if it is not a
checkout, set `CGO_LDFLAGS` as above. A binding cannot say it more usefully
than that, because the failure happens before any of its code exists.

## Threads

A run belongs to one OS thread, and a cgo call pins its goroutine to the
thread it is on for the duration of the call while no other goroutine runs
there. So the rule is simply **one `Run` per goroutine at a time**: any number
of goroutines may call `Run` concurrently, each on its own thread, and none can
see `Busy` from another. The program sees the process's working directory and
environment, which are process-wide and not this package's to change.

## The quickstart

```sh
cd src/lypning/assets/go && go run ./quickstart "print(sum(range(10)))"
```

`quickstart "<python source>" [args...]` runs the program in-process under a
step limit and prints its bytes, or, when lypning refuses, runs `python3 -c`
once with inherited stdout and stderr and exits with its code. Stdin is not
forwarded either way (lypning sees an empty stream, python3 gets `/dev/null`),
so the two paths agree. Go's `os.Stdout` is unbuffered, so there is nothing to
flush before the child inherits it. A traceback is the program's own answer:
exit 1, never retried.

## Two things cgo costs you

* **A C compiler.** `go build` with no C compiler fails with `cgo: C compiler
  "<cc>" not found`, and `CGO_ENABLED=0` compiles this package out entirely
  (`build constraints exclude all Go files`). Neither is a lypning property;
  both are what linking a native library through Go means.
* **`CGO_ENABLED=1`** when cross-compiling, together with a cross C toolchain
  and a `liblypning` built for the target. The library is built for the host
  it runs on, never musl, so a cross build needs one built there.
