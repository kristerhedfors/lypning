//! main.rs — lypning embedded in a Rust host, through the native API.
//!
//! The one invariant this file holds: **`Status::Unsupported` is a route, not
//! an error**, and it is asserted against the crate that was just compiled
//! rather than described in a comment. The refusal path is the only part of
//! lypning that has ever broken *silently* — a parser change that turns a
//! refusal into a traceback still compiles, still links, still answers
//! `--version` — so the four properties a host branches on (status, exit 90,
//! empty stdout, `should_fall_onward()`) are `assert!`ed here, and this
//! program exits non-zero the moment one of them stops being true.
//!
//! It is also the file a Rust harness author copies, so it does not stop at
//! the refusal: it implements the other half of the mixture, the
//! `std::process::Command("python3")` fall-onward, because a demo that prints
//! "would fall onward" and stops has demonstrated the easy half.
//!
//! No C ABI anywhere below. `capi.rs` exists to hand `embed::run` to languages
//! that cannot call it; a Rust program can. Going through FFI would cost this
//! host an `unsafe` block per call and hand it `*const u8` + length pairs that
//! die with a handle, in exchange for nothing — [`lypning::Outcome`] is the
//! same value `lypning_result` wraps, with `Vec<u8>` where C gets a borrow.
//! There is also no ABI version to check: the linker already did it.
//!
//! ## The one thing a Rust host must get right that a C host does not
//!
//! **The panic guard is yours, not the crate's.**
//!
//! A C host links `liblypning.so`, and that artifact was built by the crate's
//! own `Cargo.toml` under `--profile release-lib`, which pins `panic =
//! "unwind"` and makes `capi.rs` refuse to compile without it. The guard is
//! baked into the object file the C host receives. The C host cannot spoil it,
//! and cannot extend it either: an unwind across an `extern "C"` frame is
//! undefined behaviour, so `capi.rs` catches at the boundary and hands back
//! `LYPNING_PANIC` — a status code standing in for the unwinding C has no way
//! to hold.
//!
//! A Rust host does not link that artifact. It compiles the crate **from
//! source, under its own profile**, and profile settings apply from the
//! package being built, never from a dependency. So `[profile.release]` in
//! `../../rust/Cargo.toml` — `panic = "abort"`, right for a CLI image whose
//! cold cost is counted in device blocks — governs the `lypning` binary in
//! that package and reaches nothing here. Whether this program unwinds is
//! decided by *this* `Cargo.toml` and the profile cargo was asked for.
//!
//! That cuts both ways, and both are worth knowing before the first
//! interpreter bug rather than after:
//!
//!   * With unwinding (cargo's default for `dev` and `release`), the
//!     `catch_unwind` inside `embed::run` is real: an interpreter panic comes
//!     back as [`Status::Panic`] with `committed` telling you whether anything
//!     reached the disk. On top of that a Rust host may wrap `run` in its own
//!     `catch_unwind` and keep the payload — it is not on an `extern "C"`
//!     frame, so nothing is undefined and nothing is lost in translation.
//!     `unwinding()` below proves this binary is in that state.
//!   * With `panic = "abort"` in the host's profile, that same `catch_unwind`
//!     compiles to nothing, [`Status::Panic`] becomes unreachable, and a bug
//!     in the interpreter takes down an application that merely asked to run a
//!     one-liner. That is a legitimate choice for, say, a fuzzer harness — but
//!     it is the host's choice, made in the host's manifest, and no
//!     `compile_error!` will mention it, because the guard that would is
//!     inside the `capi` feature this example deliberately leaves off.
//!
//! Run it: `cargo run`. It builds the crate as an rlib; nothing under
//! `assets/rust/target` is read or written.
//!
//! In a wheel this directory is read-only and cargo cannot create a `target/`
//! in it, so it has to be copied somewhere writable first. The C and C++
//! examples survive that move because nothing in them refers to where they
//! are; this one has exactly one thing that does — `path = "../../rust"` in
//! `Cargo.toml`. Copy the directory to `~/.lypning/build/examples/rust` and
//! that line still resolves, because `lypning build --rust` puts the crate's
//! writable copy at `~/.lypning/build/rust`. Copy it anywhere else and that
//! one line is the only thing to change.
//!
//! SPDX-License-Identifier: MIT

use lypning::{route, run, Engine, Outcome, Request, Status};
use std::io::Write;
use std::process::{Command, Stdio};

/// What this binary can do about a panic, decided by the profile cargo used
/// and not by anything the lypning crate says. Printed rather than asserted:
/// `abort` is a defensible host choice, it is just one whose consequences the
/// reader should meet here instead of in production.
#[cfg(panic = "abort")]
const PANIC_STRATEGY: &str = "abort";
#[cfg(not(panic = "abort"))]
const PANIC_STRATEGY: &str = "unwind";

// ---------------------------------------------------------------------------
// The programs
// ---------------------------------------------------------------------------

/// Shared by the program lypning runs and the program it refuses, so the
/// fallback below has to forward stdin faithfully or the two answers disagree
/// in a way you can see.
const SPEECH: &[u8] = b"the quick brown fox jumps over the lazy dog the fox\n";

/// One program, two policies: the only difference between the fourth entry and
/// the fifth is `filesystem`. The path is repeated inside the source because a
/// Python program is text, and `concat!` would only hide that.
const OUT_FILE: &str = "lypning-embed-example.txt";
const WRITER: &str = "open(\"lypning-embed-example.txt\", \"w\").write(\"staged, committed at exit\\n\")\n\
                      print(\"wrote lypning-embed-example.txt\")\n";

struct Program {
    label: &'static str,
    src: &'static str,
    /// `sys.argv[1:]`.
    args: &'static [&'static str],
    /// Bytes, not a string: a program's stdin may hold a NUL and often is not
    /// text at all. `None` is an empty stream, never this process's fd 0.
    stdin: Option<&'static [u8]>,
    filesystem: bool,
    /// Refuse past this many statements and iterator advances. `0` is no
    /// limit, which is right for a program this file wrote and wrong for one a
    /// model wrote — an in-process run has no PID to kill.
    step_limit: u64,
    /// What this host does with a refusal. Not a property of the refusal: a
    /// refusal is always routable, and whether it is worth a CPython spawn is
    /// the host's call.
    route_onward: bool,
    /// Asserted, so a crate change that starts accepting — or starts refusing
    /// — one of these fails here rather than in somebody's harness.
    ///
    /// A `Status` rather than a bool because the third case is the one this
    /// file's invariant is about from the other side: [`Status::Error`] is the
    /// program's own failure. It exits non-zero exactly as a refusal does, and
    /// routing it onward would run what it already did a second time.
    expect: Status,
}

static PROGRAMS: &[Program] = &[
    Program {
        label: "stdin -> transform -> stdout",
        src: "import sys\n\
              counts = {}\n\
              for w in sys.stdin.read().split():\n\
              \x20   counts[w] = counts.get(w, 0) + 1\n\
              for w in sorted(counts):\n\
              \x20   print(w, counts[w])\n",
        args: &[],
        stdin: Some(SPEECH),
        filesystem: true,
        step_limit: 0,
        route_onward: true,
        expect: Status::Ok,
    },
    Program {
        label: "sys.argv[1:]",
        src: "import sys\nprint(sum(int(a) for a in sys.argv[1:]))\n",
        args: &["3", "4", "5"],
        stdin: None,
        filesystem: true,
        step_limit: 0,
        route_onward: true,
        expect: Status::Ok,
    },
    Program {
        label: "outside the subset — the same stdin, answered by CPython",
        src: "import re, sys\nprint(len(re.findall(r\"[aeiou]\", sys.stdin.read())))\n",
        args: &[],
        stdin: Some(SPEECH),
        filesystem: true,
        step_limit: 0,
        route_onward: true,
        expect: Status::Unsupported,
    },
    Program {
        label: "a program that fails on its own — an error, not a refusal",
        // It prints before it raises, and that is the whole point: by the time
        // the exception arrives the first line has already committed, so there
        // is no version of "try it again on CPython" that does not print it
        // twice. This is the entry that makes the header's invariant an
        // if-and-only-if instead of half a claim.
        src: "print(\"half the answer\")\n1 / 0\n",
        args: &[],
        stdin: None,
        filesystem: true,
        step_limit: 0,
        // Nothing to decide: a refusal is routable and this is not one. The
        // field is read only on the refusal path, and `false` is the honest
        // value to leave in the table.
        route_onward: false,
        expect: Status::Error,
    },
    Program {
        label: "a program that writes a file",
        src: WRITER,
        args: &[],
        stdin: None,
        filesystem: true,
        step_limit: 0,
        route_onward: true,
        expect: Status::Ok,
    },
    Program {
        label: "the same program, filesystem denied",
        src: WRITER,
        args: &[],
        stdin: None,
        filesystem: false,
        step_limit: 0,
        // Declined on purpose. Handing this to CPython would run on CPython
        // exactly the write this host just refused to allow, which would make
        // the sandbox a speed bump. The refusal is routable; routing it would
        // be a policy mistake, and the API leaves that mistake to us to not
        // make.
        route_onward: false,
        expect: Status::Unsupported,
    },
    Program {
        label: "a program that does not stop",
        src: "n = 0\nwhile True:\n\x20   n += 1\n",
        args: &[],
        stdin: None,
        filesystem: true,
        // Without this the call below never returns. There is no process to
        // kill and no timeout to apply to a function call in your own thread,
        // so the budget is the only exit — which is why the crate warns about
        // it and why this example spends an entry on it.
        step_limit: 200_000,
        // Declined for the same reason as the sandbox above: the refusal is
        // routable, but a program that will not stop under a step budget will
        // not stop under CPython either, and there it would hold a process
        // instead of a thread.
        route_onward: false,
        expect: Status::Unsupported,
    },
];

fn request(p: &Program) -> Request {
    Request {
        args: p.args.iter().map(|a| a.to_string()).collect(),
        stdin: p.stdin.map(<[u8]>::to_vec),
        filesystem: p.filesystem,
        step_limit: p.step_limit,
        ..Request::new(p.src)
    }
}

// ---------------------------------------------------------------------------
// The fallback — the half of the mixture that is not lypning
// ---------------------------------------------------------------------------

/// Run the refused program on CPython, forwarding exactly what lypning was
/// given: the same source, the same `sys.argv`, the same stdin bytes.
/// `python3 -c SRC a b` gives the program `sys.argv == ['-c', 'a', 'b']`,
/// which is the shape `Request` produces when `filename` is `None` — the two
/// engines see the same argv, and that is what makes the second answer the
/// same answer.
///
/// Returns the child's exit code, `128 + n` if it died of a signal, `-1` if it
/// could not be started at all.
fn cpython(p: &Program) -> (i32, Vec<u8>, Vec<u8>) {
    let child = Command::new("python3")
        .arg("-c")
        .arg(p.src)
        .args(p.args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn();
    let mut child = match child {
        Ok(c) => c,
        // No python3 on this machine is a fact about the machine, not a
        // failure of the refusal contract — everything asserted above still
        // held. Say so and carry on rather than aborting the demo.
        Err(e) => return (-1, Vec::new(), format!("could not spawn python3: {e}\n").into_bytes()),
    };

    // Written in full before anything is read back. That is safe only because
    // every stdin above is far smaller than a pipe buffer; a harness taking
    // arbitrary input must write on a thread, or the child fills its stdout
    // pipe, stops reading, and the two processes wait on each other forever.
    if let Some(bytes) = p.stdin {
        if let Some(mut sink) = child.stdin.take() {
            let _ = sink.write_all(bytes);
        }
    }
    // Dropped either way, so the program's `sys.stdin.read()` sees EOF.
    drop(child.stdin.take());

    match child.wait_with_output() {
        Ok(out) => (exit_code(out.status), out.stdout, out.stderr),
        Err(e) => (-1, Vec::new(), format!("python3: {e}\n").into_bytes()),
    }
}

fn exit_code(st: std::process::ExitStatus) -> i32 {
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        if let Some(sig) = st.signal() {
            // The shell's convention, and the one `fall_onward` was measured
            // against. A code of `None` on unix means a signal, always.
            return 128 + sig;
        }
    }
    st.code().unwrap_or(-1)
}

// ---------------------------------------------------------------------------
// The contract, asserted
// ---------------------------------------------------------------------------

/// The four properties a host branches on, plus the facts each rests on.
/// Checked on the crate that was just compiled, because "the refusal still
/// works" is not something a build can tell you.
fn assert_refusal_contract(r: &Outcome) {
    // 1. It is a refusal, not an error and not an answer.
    assert_eq!(r.status, Status::Unsupported);

    // 2. Exit 90, the number the `lypning` binary would have returned, so a
    //    host that shells out and a host that links get the same answer.
    assert_eq!(r.exit_code, lypning::UNSUPPORTED_EXIT);

    // 3. Nothing on stdout. This is the commit barrier, and it is what makes
    //    step 4 safe: re-running the program elsewhere cannot repeat output
    //    that was never written.
    assert!(r.stdout.is_empty());

    // 4. The call to branch on says yes — not the status, not the exit code,
    //    and never the text of the stderr line.
    assert!(r.should_fall_onward());

    assert!(!r.committed, "a refusal that committed is not a route");
    assert!(!r.kind.is_empty(), "branch on the kind, not on the message");
    assert!(r.stderr.ends_with(b"\n"));
    assert_eq!(
        r.stderr.iter().filter(|&&b| b == b'\n').count(),
        1,
        "exactly one line, as on the binary's stderr"
    );
}

/// The converse, and the half that is expensive in the other direction.
///
/// An uncaught exception is the program's *answer*: exit 1, a traceback, and —
/// the part that decides everything — whatever it printed before it raised,
/// already committed. A dispatcher that reads "non-zero" as "try the next
/// engine" runs those side effects again. So this is checked with the same
/// force as the refusal above, and the property that matters is that neither
/// predicate routes a result that has a traceback in it.
fn assert_error_contract(r: &Outcome) {
    assert_eq!(r.status, Status::Error);
    assert_eq!(r.exit_code, 1);
    assert_ne!(r.exit_code, lypning::UNSUPPORTED_EXIT);

    // Both say no, and the second is the interesting one: it sees
    // `Traceback (` in the stderr and still says no, because the exit code is
    // not 0. Exit 0 with a traceback is an engine that lost its own error;
    // exit 1 with one is a program that reported its own.
    assert!(!r.should_fall_onward());
    assert!(
        !lypning::fall_onward(r.exit_code, &r.stderr),
        "an ordinary exception must not route onward"
    );

    assert!(r.kind.is_empty(), "an error has no refusal kind to branch on");
    assert!(r.stderr.starts_with(b"Traceback (most recent call last):\n"));
    assert!(r.committed, "the output reached the host, so the run is not a no-op");
}

// ---------------------------------------------------------------------------
// Reporting
// ---------------------------------------------------------------------------

/// Bytes, a line at a time, under a tag. A real harness hands these on
/// unchanged instead: `from_utf8_lossy` is a wrong answer of exactly the kind
/// this project exists to refuse, and it is used here only because a terminal
/// is the destination.
fn show(tag: &str, bytes: &[u8]) {
    if bytes.is_empty() {
        println!("  {tag:<9} (empty)");
        return;
    }
    let mut tag = tag;
    for line in bytes.split(|&b| b == b'\n') {
        if line.is_empty() {
            continue;
        }
        println!("  {tag:<9} {}", String::from_utf8_lossy(line));
        tag = "";
    }
}

// ---------------------------------------------------------------------------
// The three steps
// ---------------------------------------------------------------------------

fn answer(p: &Program) {
    println!("== {}", p.label);

    // Step 1 — decide, without running anything. One parse, no execution.
    // This is lypning's own front end answering, not a guess over the program
    // text, so a harness can use it to skip a spawn it was going to lose.
    let routed = route(p.src);
    print!("  route     {}", routed.engine.as_str());
    if !routed.kind.is_empty() {
        print!("  ({}: {})", routed.kind, routed.detail);
    }
    println!();
    for module in &routed.imports {
        println!("  import    {module}");
    }

    // Step 2 — execute, in this thread. No fork, no exec, no pipe: on a
    // program lypning accepts, this call is the entire cost of the run.
    let r = run(&request(p));

    // The table says what this program is; a crate that changed its mind about
    // it must say so here.
    assert_eq!(r.status, p.expect, "{}", p.label);

    // Step 3 — decide again, on what actually happened.
    if r.should_fall_onward() {
        assert_refusal_contract(&r);
        println!("  refused   {}: {}", r.kind, r.detail);

        // Two things the route could not have told the host, both of which a
        // harness author meets on day one.
        if routed.engine == Engine::Lypning {
            println!(
                "  note      routing said lypning; only running it could tell. One parse cannot\n\
                 \x20           see a policy the host set — that is what the run is for"
            );
        } else if p.route_onward && routed.engine != Engine::CPython {
            println!(
                "  note      routing named {}; a chain with that tier in it would try that\n\
                 \x20           first. This example has two engines, so: CPython",
                routed.engine.as_str()
            );
        }

        if !p.route_onward {
            println!("  onward    declined by this host — see the table for why");
            println!();
            return;
        }
        println!("  onward    python3 -c ... (same argv, same stdin)");
        let (code, out, err) = cpython(p);
        println!("  exit      {code} (cpython)");
        show("stdout", &out);
        if !err.is_empty() {
            show("stderr", &err);
        }
        // The dispatcher's own predicate, asked of the next engine's result. A
        // harness chaining lypning -> lypning-mp -> CPython asks it at every
        // link; here there is no link after CPython, so a yes is worth saying
        // out loud rather than swallowing.
        if lypning::fall_onward(code, &err) {
            println!("  note      that result asks to fall onward too, and there is no engine after CPython");
        }
    } else {
        // Not a refusal, so nothing here is routable and `exit_code` is the
        // program's own number. The error case gets a contract of its own
        // because it is the one a dispatcher is tempted to retry.
        if r.status == Status::Error {
            assert_error_contract(&r);
        } else {
            assert_eq!(r.status, Status::Ok);
            assert!(!r.should_fall_onward());
        }
        println!("  exit      {} (lypning, in-process)", r.exit_code);
        show("stdout", &r.stdout);
        if !r.stderr.is_empty() {
            show("stderr", &r.stderr);
        }
        if r.status == Status::Error {
            println!(
                "  onward    no — exit 1 is the program's own answer, and its stdout committed\n\
                 \x20           before the exception. Re-running it prints that line twice"
            );
        }
    }
    println!();
}

// ---------------------------------------------------------------------------
// Threads, honestly
// ---------------------------------------------------------------------------

/// Two threads, two programs, at the same time — and every answer checked.
///
/// A run is confined to one thread because the interpreter's values are `Rc`
/// and the commit barrier stages into thread-locals. That is a real
/// restriction and it is also the whole restriction: nothing is shared between
/// threads, so two of them may run two programs at once with no lock and no
/// contention. Each thread loops so the two overlap for real; a single run
/// each would prove only that they did not crash. If the staging ever stopped
/// being per-thread, one of these threads would read the other's output and
/// this function is where that shows up.
fn concurrent_threads() {
    const ROUNDS: usize = 200;
    // Two answers that cannot be mistaken for each other, in bulk, so an
    // interleave would be visible rather than plausible.
    const A: &str = "print(\"\".join(str(n % 10) for n in range(64)))";
    const B: &str = "print(\"\".join(chr(65 + n % 26) for n in range(64)))";

    let expect = |src: &str| {
        let r = run(&Request::new(src));
        assert_eq!(r.status, Status::Ok);
        r.stdout
    };
    let (want_a, want_b) = (expect(A), expect(B));
    assert_ne!(want_a, want_b);

    let start = std::sync::Arc::new(std::sync::Barrier::new(2));
    let spawn = |src: &'static str, want: Vec<u8>, gate: std::sync::Arc<std::sync::Barrier>| {
        std::thread::spawn(move || {
            gate.wait();
            for _ in 0..ROUNDS {
                let r = run(&Request::new(src));
                assert_eq!(r.status, Status::Ok);
                // Not `Busy` either: the other thread's run is invisible here.
                assert_eq!(r.stdout, want, "one thread read the other's output");
            }
        })
    };
    let ta = spawn(A, want_a.clone(), start.clone());
    let tb = spawn(B, want_b.clone(), start);
    ta.join().expect("thread A");
    tb.join().expect("thread B");

    println!("== two threads, {ROUNDS} runs each, concurrently");
    // `trim_end` on the lossy string, not `trim_ascii_end` on the bytes: the
    // same answer, and it does not put a rustc 1.80 floor on a file whose
    // whole purpose is to be copied into somebody else's project. Nothing in
    // the crate needs anything newer than let-else.
    println!("  thread A  {}", String::from_utf8_lossy(&want_a).trim_end());
    println!("  thread B  {}", String::from_utf8_lossy(&want_b).trim_end());
    println!("  checked   every run returned Ok and its own output, unmixed");
    println!();
}

/// The other half of the threading rule, and the half this example cannot
/// fully demonstrate — so it says which half it is showing.
///
/// `Status::Busy` means *this thread is already inside a run*. Reaching it
/// requires calling `run` from within `run`, and the native API offers no way
/// to get there: the interpreter never calls back into host code, so between
/// entering `run` and leaving it a host has no frame to run in. Contriving one
/// would mean patching the crate, and a demonstration that needs the subject
/// changed is not a demonstration.
///
/// What is checkable is the property a host actually depends on, and it is the
/// stronger one: the flag is per-run, not per-thread-lifetime, so **runs on
/// one thread compose**. The second call below returns `Ok`, not `Busy` — and
/// it would still return `Ok` if the first had panicked, because `embed::run`
/// clears the flag outside the `catch_unwind`. A thread cannot be poisoned
/// into permanent `Busy` by a bad program.
///
/// So `Busy` is not a case a host must code around today. It is there so that
/// the day the interpreter does call outward, the answer is a refusal with
/// nothing executed rather than two programs' output in one buffer.
fn sequential_runs_are_not_busy() {
    let q = Request::new("print(6 * 7)");
    let first = run(&q);
    let second = run(&q);
    let third = run(&Request::new("print('after')"));

    assert_eq!(first.status, Status::Ok);
    assert_eq!(second.status, Status::Ok);
    assert_eq!(third.status, Status::Ok);
    assert_eq!(first.stdout, second.stdout);
    assert_eq!(third.stdout.as_slice(), b"after\n");

    println!("== the same thread, three runs in a row");
    println!("  status    Ok, Ok, Ok — never Busy; the flag is per run, not per thread");
    println!("  stdout    {}", String::from_utf8_lossy(&second.stdout).trim_end());
    println!("  note      Busy needs a re-entrant call, which the native API gives no way to");
    println!("            construct. Not simulated here — see this function's doc comment");
    println!();
}

// ---------------------------------------------------------------------------
// The profile, demonstrated
// ---------------------------------------------------------------------------

/// Prove that this binary really does unwind — the property the header comment
/// claims and the one a C host cannot have.
///
/// The panic caught below is **ours**, raised two lines above the catch. That
/// is deliberate: the claim being tested is about the profile this package was
/// compiled under, not about a bug in lypning, and there is no honest way to
/// make the interpreter panic on demand. What it shows is that `catch_unwind`
/// is live in this build — which is exactly the precondition for the one
/// inside `embed::run` returning `Status::Panic` instead of aborting.
fn unwinding() {
    println!("== panic strategy: {PANIC_STRATEGY}");

    #[cfg(panic = "abort")]
    {
        println!("  effect    catch_unwind is a no-op here, so the guard inside embed::run is too:");
        println!("            an interpreter bug aborts this process and Status::Panic is dead code");
        println!("  fix       drop `panic = \"abort\"` from this package's profile if that is not");
        println!("            what you meant — nothing in the lypning crate can decide it for you");
    }
    #[cfg(not(panic = "abort"))]
    {
        // The hook is process-global; silencing it around our own deliberate
        // panic keeps the demo's output readable without a library ever
        // imposing that on a host.
        let previous = std::panic::take_hook();
        std::panic::set_hook(Box::new(|_| {}));
        let caught = std::panic::catch_unwind(|| {
            panic!("this host's own panic, not the interpreter's");
        });
        std::panic::set_hook(previous);

        let payload = caught
            .err()
            .expect("catch_unwind returned Ok from a panicking closure");
        let what = payload
            .downcast_ref::<&'static str>()
            .copied()
            .unwrap_or("panic");
        println!("  caught    {what}");
        println!("  effect    the same catch inside embed::run is live, so an interpreter bug");
        println!("            comes back as Status::Panic with `committed` saying what reached disk");
        println!("  ours      a Rust host may also catch around run() itself; a C host may not,");
        println!("            because unwinding out of an extern \"C\" frame is undefined");

        // And the run after a caught panic still works, which is the point of
        // catching at all.
        let r = run(&Request::new("print('still here')"));
        assert_eq!(r.status, Status::Ok);
        assert_eq!(r.stdout.as_slice(), b"still here\n");
        println!("  after     {}", String::from_utf8_lossy(&r.stdout).trim_end());
    }
    println!();
}

/// Removes what the writer program wrote, on **every** path out of `main`.
///
/// The last line of `main` was enough only for the run that succeeds. Every
/// check in this file is an `assert!`, so the run this file exists for — the
/// failing one — unwinds straight past a tidy-up at the bottom and leaves
/// `OUT_FILE` in whatever directory cargo ran from, which in a source checkout
/// is a tracked one. A guard is the same discipline the `filesystem` switch
/// offers a host that would rather the write had never happened.
///
/// It runs on an unwind and not on an abort, which is one more consequence of
/// the profile choice this file is otherwise about.
struct Cleanup;

impl Drop for Cleanup {
    fn drop(&mut self) {
        if std::fs::remove_file(OUT_FILE).is_ok() {
            println!("removed {OUT_FILE}");
        }
    }
}

fn main() {
    // Before anything runs, so it covers everything that runs.
    let _cleanup = Cleanup;

    println!("lypning {} — native Rust API, no FFI\n", lypning::VERSION);

    for p in PROGRAMS {
        answer(p);
    }
    concurrent_threads();
    sequential_runs_are_not_busy();
    unwinding();

    // One of the programs above really did write this, into whatever directory
    // cargo ran from. `_cleanup` takes it away on the way out of this
    // function — including the way out an assertion takes.
    println!("all assertions held");
}
