//! Running a program **inside somebody else's process**.
//!
//! The CLI's contract is a process contract: exit 90, one line on stderr,
//! nothing on stdout. A library has no exit code and must never touch the
//! host's stderr, so each of those three has to be carried by a value instead —
//! and the translation has to be exact, because the whole point of the refusal
//! is that a caller can act on it mechanically.
//!
//!   process                      embedded
//!   -------------------------    ------------------------------------------
//!   exit 90                      `Status::Unsupported`, `exit_code == 90`
//!   one line on stderr           `stderr` is that line and nothing else
//!   nothing on stdout            `stdout` is empty, by the commit barrier
//!   any other non-zero exit      `Status::Error` / the program's own code
//!
//! What the host gains for accepting that translation is the thing the mixture
//! could never have: on the programs lypning accepts there is **no process at
//! all**. The dispatcher in `main.rs` opens with the observation that 96% of a
//! one-liner's cost is the OS spawning it; embedding deletes that 96% rather
//! than moving it to a cheaper interpreter.
//!
//! What the host takes on is the other half of `main.rs::dispatch`: when this
//! returns `Unsupported`, **the host** must run the program on CPython. That is
//! not a detail to leave implicit — a harness that treats a refusal as a
//! failure has converted lypning from a speedup into a bug. `fall_onward`
//! below is the same predicate the dispatcher uses, exported so no host has to
//! reinvent it.

use crate::err::{ErrKind, LypningError, UNSUPPORTED_EXIT};
use crate::host::{self, Policy};
use crate::io;
use std::cell::Cell;
use std::panic::{catch_unwind, AssertUnwindSafe};

/// Who the outcome belongs to. `exit_code` carries the number; this says how to
/// read it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    /// The program ran to completion. `exit_code` is the program's own — `0`,
    /// or whatever it passed to `sys.exit`.
    Ok,
    /// The program raised. `stderr` holds the traceback, `exit_code` is 1.
    Error,
    /// **lypning refused.** Not a failure: the program is outside the subset,
    /// nothing was written, and the host should run it on CPython.
    Unsupported,
    /// A run is already in flight on this thread. Nothing was executed.
    Busy,
    /// The interpreter panicked. Nothing about the program is known; whether
    /// anything reached the disk is in `committed`.
    Panic,
}

/// One program, and everything the host has to decide for it.
#[derive(Clone)]
pub struct Request {
    pub source: String,
    /// `sys.argv[0]`. `None` gives CPython's `-c` shape, which is what a
    /// one-liner from a harness actually is.
    pub filename: Option<String>,
    /// `sys.argv[1:]`.
    pub args: Vec<String>,
    /// The program's stdin. `None` is an empty stream — never the host's fd 0,
    /// which a library call has no business reading.
    pub stdin: Option<Vec<u8>>,
    /// May the program reach the filesystem? `false` turns every file
    /// operation into a refusal, so the host is told rather than lied to.
    pub filesystem: bool,
    /// Statements and loop iterations past which the run refuses. `0` is no
    /// limit — which is the right default for a harness that already trusts
    /// what it is running, and the wrong one for anything else. See
    /// [`crate::host::Policy::step_limit`].
    pub step_limit: u64,
    /// Bytes of captured output past which the run refuses. `0` is no limit.
    pub output_limit: usize,
}

impl Default for Request {
    fn default() -> Self {
        Request {
            source: String::new(),
            filename: None,
            args: Vec::new(),
            stdin: None,
            filesystem: true,
            step_limit: 0,
            output_limit: 0,
        }
    }
}

impl Request {
    pub fn new(source: impl Into<String>) -> Self {
        Request {
            source: source.into(),
            ..Request::default()
        }
    }

    /// `sys.argv` as the program must see it: `['-c', ...]` for a command
    /// string, `['file.py', ...]` for a file. Reproduced here rather than left
    /// to the caller because every `sys.argv[1:]` one-liner in the corpus
    /// depends on the exact shape.
    fn sys_argv(&self) -> Vec<String> {
        let mut out = Vec::with_capacity(self.args.len() + 1);
        out.push(self.filename.clone().unwrap_or_else(|| "-c".to_string()));
        out.extend(self.args.iter().cloned());
        out
    }
}

/// What happened, in full. Nothing here borrows: a host may hold it as long as
/// it likes and run another program meanwhile.
#[derive(Debug, Clone)]
pub struct Outcome {
    pub status: Status,
    /// The exit code the CLI would have returned for the same program: the
    /// program's own, `1` for an uncaught exception, `90` for a refusal, and
    /// `-1` for the library itself failing.
    pub exit_code: i32,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
    /// The two halves of `unsupported: <kind>: <detail>`, split so a host can
    /// branch on the kind without parsing the line back apart.
    pub kind: String,
    pub detail: String,
    /// Did the run pass the commit point — the moment staged writes are
    /// flushed and the run stops being reversible?
    ///
    /// True for every run that finished, whether or not it touched a file:
    /// the question is not "was anything written" but "is this still undoable",
    /// and the answer stops being yes at the barrier rather than at the first
    /// write. False for a refusal, which is what makes falling onward safe —
    /// so it is reported rather than assumed.
    pub committed: bool,
}

impl Outcome {
    fn empty(status: Status, exit_code: i32) -> Self {
        Outcome {
            status,
            exit_code,
            stdout: Vec::new(),
            stderr: Vec::new(),
            kind: String::new(),
            detail: String::new(),
            committed: false,
        }
    }

    /// Should the host hand this program to the next interpreter?
    ///
    /// True for every outcome that is NOT the program's own answer and left
    /// nothing behind — a refusal, a `Busy` that executed nothing, and a
    /// `Panic` that reached no commit. All three mean one thing to a caller:
    /// lypning did not answer, and the program still needs an answer.
    ///
    /// `committed` is what makes it safe rather than merely convenient:
    /// re-running a program that already wrote half its output would repeat the
    /// half, which is the mixture's one unforgivable failure.
    ///
    /// `Ok` and `Error` are never true. Those ARE the program's answer, and an
    /// uncaught exception is as much of an answer as a printed line.
    pub fn should_fall_onward(&self) -> bool {
        !self.committed
            && matches!(
                self.status,
                Status::Unsupported | Status::Busy | Status::Panic
            )
    }
}

thread_local! {
    /// One run per thread at a time. The interpreter's values are `Rc` and the
    /// commit barrier's staging is thread_local, so a re-entrant call would
    /// interleave two programs' output into one buffer. Refusing is the only
    /// answer that cannot be wrong.
    static RUNNING: Cell<bool> = const { Cell::new(false) };
}

/// Run one program in this thread and hand back everything it produced.
pub fn run(req: &Request) -> Outcome {
    if RUNNING.with(|r| r.get()) {
        return Outcome::empty(Status::Busy, -1);
    }
    RUNNING.with(|r| r.set(true));
    let out = run_guarded(req);
    RUNNING.with(|r| r.set(false));
    out
}

/// The lexer reads end-of-input as a zero byte, so a literal NUL in the source
/// ends the program there — and the ABI takes a pointer and a LENGTH, which is
/// the one way a NUL can arrive. Silently running the first half of a program
/// and reporting success is the wrong-answer failure this whole runtime is
/// built to avoid, so it is refused instead: CPython runs the whole thing.
///
/// The CLI cannot reach this (a command line and a file are both NUL-free by
/// construction), which is exactly why it went unnoticed until there was an API.
fn nul_in_source(src: &str) -> Option<usize> {
    src.bytes().position(|b| b == 0)
}

fn run_guarded(req: &Request) -> Outcome {
    if let Some(at) = nul_in_source(&req.source) {
        return Outcome {
            stderr: format!("lypning: unsupported: source: NUL byte at offset {at}\n")
                .into_bytes(),
            kind: "source".into(),
            detail: format!("NUL byte at offset {at}"),
            ..Outcome::empty(Status::Unsupported, UNSUPPORTED_EXIT)
        };
    }
    // Reset on the way IN. A previous run that panicked cannot be trusted to
    // have tidied up, and the caller who pays for that is the next one.
    io::reset();
    crate::err::reset_nesting();
    let previous = host::set_policy(Some(Policy {
        argv: Some(req.sys_argv()),
        filesystem: req.filesystem,
        step_limit: req.step_limit,
        output_limit: req.output_limit,
    }));
    io::set_stdin(req.stdin.clone());

    // The interpreter is created OUTSIDE the catch so that whatever the program
    // built can be taken apart afterwards, iteratively. Dropping it in place is
    // what a derived `Drop` would do — one stack frame per level of a structure
    // the program chose the depth of — and that is a segfault in the host's
    // thread, which is not an unwind and cannot be caught.
    let mut interp = crate::eval::Interp::new();
    let result = catch_unwind(AssertUnwindSafe(|| {
        let body = crate::parse::parse(&req.source)?;
        interp.run(&body)
    }));

    let mut outcome = match result {
        Ok(r) => finish(r),
        Err(payload) => panicked(payload),
    };
    outcome.committed = io::is_committed() || outcome.committed;

    // Drain before the policy comes off: `io::take_*` is where the captured
    // bytes live, and leaving them would hand them to the next program.
    if outcome.status != Status::Unsupported {
        outcome.stdout = io::take_out();
        let mut err = io::take_err();
        err.extend_from_slice(&outcome.stderr);
        outcome.stderr = err;
    }
    io::reset();
    host::set_policy(previous);
    crate::eval::dismantle_interp(interp);
    outcome
}

/// The exit path, and it mirrors `main.rs::finish` case for case on purpose:
/// two implementations of the refusal contract that could drift is exactly how
/// a MISMATCH gets into a release.
fn finish(r: Result<(), LypningError>) -> Outcome {
    match r {
        Ok(()) => match io::commit() {
            Ok(()) => Outcome {
                committed: true,
                ..Outcome::empty(Status::Ok, 0)
            },
            Err(e) => finish(Err(e)),
        },
        Err(ref e) if e.is_exit().is_some() => {
            let n = e.is_exit().unwrap_or(0);
            let committed = io::commit().is_ok();
            Outcome {
                committed,
                ..Outcome::empty(Status::Ok, n)
            }
        }
        Err(e) if e.is_unsupported() => {
            let (kind, detail) = match e.kind() {
                ErrKind::Unsupported { kind, detail } => (kind.clone(), detail.clone()),
                _ => (String::new(), String::new()),
            };
            if io::is_committed() {
                // Output already left the process, so the host cannot re-run
                // this anywhere. Say so as an error rather than as a refusal
                // the host would act on by running the program twice.
                let _ = io::commit();
                return Outcome {
                    stderr: format!(
                        "lypning: error: {e} — reached after output was already flushed, so the \
                         run cannot be routed onward\n"
                    )
                    .into_bytes(),
                    committed: true,
                    kind,
                    detail,
                    ..Outcome::empty(Status::Error, 1)
                };
            }
            // The barrier makes the refusal a no-op: staged output and staged
            // writes go away, and the host may run the program anywhere.
            io::discard();
            Outcome {
                // Exactly the line the CLI puts on stderr, newline included, so
                // a host that logs it sees what a terminal would have shown.
                stderr: format!("{e}\n").into_bytes(),
                kind,
                detail,
                ..Outcome::empty(Status::Unsupported, UNSUPPORTED_EXIT)
            }
        }
        Err(e) => {
            let committed = io::commit().is_ok();
            Outcome {
                stderr: format!("Traceback (most recent call last):\n{e}\n").into_bytes(),
                committed,
                ..Outcome::empty(Status::Error, 1)
            }
        }
    }
}

/// A panic is the library's own failure, and the host must survive it.
///
/// The crate builds the CLI with `panic = "abort"` — for a process that is the
/// right trade, since there is nothing to save. A library cannot make that
/// choice on an application's behalf, so the C ABI is built with
/// `panic = "unwind"` (the `release-lib` profile) and every entry point catches.
/// The default panic hook still prints; silencing a process-global hook from
/// inside a library would be a worse imposition than the noise.
fn panicked(payload: Box<dyn std::any::Any + Send>) -> Outcome {
    let what = payload
        .downcast_ref::<&'static str>()
        .map(|s| s.to_string())
        .or_else(|| payload.downcast_ref::<String>().cloned())
        .unwrap_or_else(|| "panic".to_string());
    let committed = io::is_committed();
    if !committed {
        io::discard();
    }
    Outcome {
        stderr: format!("lypning: internal error: {what}\n").into_bytes(),
        committed,
        ..Outcome::empty(Status::Panic, -1)
    }
}

/// Should a chain move past an intermediate engine's result?
///
/// Lifted out of `main.rs` unchanged so the dispatcher and every embedding host
/// share one implementation. The two signals beyond exit 90 come from
/// measurement, not design: a `MemoryError` is a property of the engine's heap
/// rather than the program's answer, and a traceback reported with exit 0 means
/// the caller was about to be handed empty stdout and a success status.
///
/// Deliberately NOT here: an ordinary non-zero exit with a traceback. That is
/// very often the program's own correct answer, and re-running it would execute
/// its side effects twice.
pub fn fall_onward(exit_code: i32, stderr: &[u8]) -> bool {
    if exit_code == UNSUPPORTED_EXIT {
        return true;
    }
    let has = |needle: &[u8]| stderr.windows(needle.len()).any(|w| w == needle);
    has(b"MemoryError") || (exit_code == 0 && has(b"Traceback ("))
}
