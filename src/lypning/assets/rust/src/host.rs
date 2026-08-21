//! What the *process* provides, and how an embedded run takes it over.
//!
//! The CLI and the library run the same interpreter over the same source. The
//! difference is entirely in what surrounds it: the CLI's program inherits the
//! process — its argv, its stdin, its two output streams — while an embedded
//! program is handed each of those by the host application and must not be able
//! to reach the real ones by accident.
//!
//! One thread_local `Policy` is that boundary. It is off by default, so the
//! binary behaves exactly as it did before this module existed: every accessor
//! here falls through to `std::env` when no override is installed, and the cost
//! of the fall-through is one thread-local read.
//!
//! **Per thread, not per process.** `Value` is `Rc`-based and the commit
//! barrier's staging lives in thread_locals (`io.rs`), so a run is confined to
//! one thread by construction. Making the policy thread_local too means two
//! host threads can run two programs at once without either seeing the other's
//! argv — and means a host that ignores that rule gets an honest `Busy` from
//! `embed::run` rather than a silently interleaved answer.

use std::cell::RefCell;

/// What an embedded run is allowed to reach, and what it may not.
#[derive(Clone)]
pub struct Policy {
    /// `sys.argv` as the program must see it, already in CPython's shape.
    pub argv: Option<Vec<String>>,
    /// May the program read and write files at all?
    ///
    /// `false` does NOT mean "pretend the file is missing" — a lie is the one
    /// outcome this project refuses. It means the run REFUSES, by the same
    /// exit-90 contract every tier uses, so the host is told plainly that
    /// lypning did not answer and can decide what to do about it.
    pub filesystem: bool,
    /// Statements and loop iterations past which the run refuses. `0` is no
    /// limit.
    ///
    /// The CLI does not need this — a process that will not stop can be killed,
    /// and `lypning run --timeout` does exactly that. A LIBRARY has no such
    /// escape: `while True: pass` inside a host's own thread is a hang with no
    /// timeout, no signal and no way back, and "do not run untrusted programs"
    /// is not advice a coding harness can follow, since every program it runs
    /// was typed by a language model.
    ///
    /// So the bound is expressed the only way that is both cheap and honest: a
    /// counter on the interpreter's two hot doors, and a REFUSAL when it is
    /// passed. A refusal is routable, so the program still gets its answer from
    /// CPython — under whatever timeout the host already has for spawning it.
    pub step_limit: u64,
    /// Bytes of captured stdout+stderr past which the run refuses. `0` is no
    /// limit. This exists because the CLI's own ceiling — flush early and give
    /// up the ability to fall back (`io::COMMIT_THRESHOLD`) — is the wrong
    /// trade for a caller who has not been handed the bytes yet: refusing is
    /// free and still routable, flushing into a host's memory is neither.
    pub output_limit: usize,
}

impl Default for Policy {
    fn default() -> Self {
        Policy {
            argv: None,
            filesystem: true,
            step_limit: 0,
            output_limit: 0,
        }
    }
}

thread_local! {
    static POLICY: RefCell<Option<Policy>> = const { RefCell::new(None) };
}

/// Install the policy for one embedded run. Returns the previous one so a
/// nested call — which `embed::run` refuses, but a future caller might not —
/// cannot leave the thread configured for someone else's program.
pub fn set_policy(p: Option<Policy>) -> Option<Policy> {
    POLICY.with(|c| std::mem::replace(&mut *c.borrow_mut(), p))
}

pub fn embedded() -> bool {
    POLICY.with(|c| c.borrow().is_some())
}

pub fn filesystem_allowed() -> bool {
    POLICY.with(|c| c.borrow().as_ref().map_or(true, |p| p.filesystem))
}

pub fn step_limit() -> u64 {
    POLICY.with(|c| c.borrow().as_ref().map_or(0, |p| p.step_limit))
}

pub fn output_limit() -> usize {
    POLICY.with(|c| c.borrow().as_ref().map_or(0, |p| p.output_limit))
}

/// `sys.argv`, verbatim, when the host supplied it.
pub fn argv_override() -> Option<Vec<String>> {
    POLICY.with(|c| c.borrow().as_ref().and_then(|p| p.argv.clone()))
}
