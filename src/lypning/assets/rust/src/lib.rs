//! lypning as a **library** — the same runtime the CLI is, minus the process.
//!
//! `main.rs` is one consumer of this crate and the C ABI in [`capi`] is
//! another; both run programs through [`embed::run`], so there is exactly one
//! implementation of the refusal contract and no way for the two shapes to
//! drift apart. That is the entire reason the split exists.
//!
//! Two entry points matter to a host:
//!
//!   * [`route`] — ask, for the cost of one parse and no execution, which of
//!     the three interpreters should run a program. This is lypning's own
//!     front end answering, not a guess over the text.
//!   * [`embed::run`] — run it here, in this thread, with its output captured
//!     and its stdin supplied. On the programs lypning accepts there is no
//!     process spawn at all, which is the 96% of a one-liner's cost that the
//!     mixture could only ever move to a cheaper interpreter.
//!
//! The contract a host must hold up is one line long: **`Status::Unsupported`
//! means run it on CPython.** A refusal is not a failure and never an answer;
//! it is the runtime saying the program is outside the subset, having left
//! nothing behind. See [`embed`] for how the process contract translates.
//!
//! Threading: a run is confined to one thread (the interpreter's values are
//! `Rc`, the commit barrier's staging is thread_local). Two threads may run two
//! programs at once; one thread may not run two at once, and gets
//! [`Status::Busy`] rather than an interleaved answer if it tries.

pub mod args;
pub mod ast;
pub mod builtins;
pub mod embed;
pub mod err;
pub mod eval;
pub mod fmt;
pub mod hash;
pub mod host;
pub mod io;
pub mod iter;
pub mod json;
pub mod lex;
pub mod methods;
pub mod modules;
pub mod ops;
pub mod parse;
pub mod route;
pub mod value;

// The handle types are named the way C names them, because they ARE the C
// names: `lypning_route` in the header and `lypning_route` in the module is one
// type with one spelling, and a reader following a symbol out of a host program
// should not have to translate it on the way in.
#[cfg(feature = "capi")]
#[allow(non_camel_case_types)]
pub mod capi;

pub use embed::{fall_onward, run, Outcome, Request, Status};
pub use err::UNSUPPORTED_EXIT;
pub use route::{route, Engine, Route};

/// The crate version, which is also what `lypning --version` prints.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// The C ABI's version, bumped only when a symbol changes shape.
///
/// Separate from [`VERSION`] on purpose: a host links against the ABI, not
/// against the runtime, and the runtime grows constructs far more often than
/// the ABI grows symbols.
pub const ABI_VERSION: u32 = 1;
