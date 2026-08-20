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

## 1. The one thing you must get right

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
effects stop being reversible — false for every refusal the barrier produces,
and true for anything that finished, whether or not it wrote a file. `should_fall_onward()`
already accounts for it, which is why it is the call to branch on rather than
the status.

## 2. What you get, and what it costs

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

Read it as one claim only: **linking removes the process, and the process was
the cost.** It says nothing about programs lypning refuses — those still cost a
CPython spawn, plus a few microseconds to be told so.

## 3. Building and linking

```bash
lypning build --lib          # liblypning.so, liblypning.a, and the headers
lypning lib                  # where they went, and the exact gcc line
gcc $(lypning lib --cflags) my_harness.c $(lypning lib --libs)
```

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

## 4. The four bindings

| host | where | how it binds |
|---|---|---|
| C | `assets/include/lypning.h`, `assets/examples/c/` | the ABI itself |
| C++ | `assets/include/lypning.hpp`, `assets/examples/cpp/` | header-only RAII over the ABI |
| Rust | `assets/examples/rust/` | the crate directly — no FFI |
| Node | `assets/node/` | a Node-API addon, no npm dependencies |
| Python | `lypning.embed` | `ctypes`, stdlib only |

The C ABI is the real API; four of the five are conveniences over it. Add a
capability there and it exists everywhere; add it anywhere else and the five
quietly disagree.

## 5. Rules of the surface

* **A run belongs to one thread.** Two threads may run two programs at once —
  the state is thread_local. One thread may not run two at once, and gets
  `LYPNING_BUSY` rather than two interleaved answers.
* **The library never touches your stdio.** Program output is captured, always.
  Stdin is bytes you supply; unset means an empty stream, never your fd 0.
* **Handles are opaque and freed by their own `_free`.** Returned pointers die
  with their handle. Program output is bytes with a length, because a program's
  stdout is whatever it printed.
* **The program sees your process's environment and working directory.** There
  is nothing else it could see: `chdir` and `environ` are process-wide. If a
  program must run somewhere specific, put your process there, or use the
  binary.

## 6. Bounding a program you did not write

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

## 7. What can still take your process down

Honesty first: a stack overflow is not an unwind, so no guard at the ABI
boundary can catch one. Everything below was reachable from ordinary program
text, was measured crashing the process, and is now a refusal:

| what | now |
|---|---|
| deeply nested expressions (`((((…`) | `unsupported: recursion`, past 64 levels — the deepest program in the corpus nests 18 |
| `repr` of a deeply nested container | `unsupported: recursion`, past 500 |
| a deeply nested tuple used as a dict key | the same |
| deeply nested JSON | the same |
| tearing down what a program built | taken apart iteratively; a million-level list is fine |
| a bug in the interpreter | `LYPNING_PANIC`, caught at the boundary |

The last row is worth stating plainly: `LYPNING_PANIC` means the runtime failed,
not your program. Report it, then run the program on CPython.

## 8. Holding it honest

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
  refusal into an error, and every crash in §7.

`lypning doctor` reports the first of those on whatever library is installed.
