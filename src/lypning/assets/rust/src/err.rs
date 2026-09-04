//! Errors, and the unsupported-feature contract that makes routing safe.
//!
//! lypning inherits lypning-mp's contract verbatim (`docs/SUBSET.md`):
//!
//!   exit 90, one line on **stderr**: `<engine>: unsupported: <kind>: <detail>`
//!
//! `<engine>` is THIS binary's own name — a point on the Rust spectrum, named at
//! compile time by `build.rs` ([`ENGINE`]) — because the dispatcher reads the
//! head of the line to know which tier refused, and a variant that wrote a
//! sibling's name would misroute silently.
//!
//! 90 is clear of 0/1/2 (python's own), 126/127 (shell) and 128+n (signals), so
//! the dispatcher can branch on it unambiguously and retry with the next
//! interpreter. Two rules from the lypning-mp experience carry over:
//!
//!   * The line goes to **stderr**. lypning-mp once wrote tracebacks to stdout and
//!     poisoned every `… | wc -l` pipeline while the exit code looked right.
//!   * A program's OWN `NotImplementedError` must keep its traceback and exit 1.
//!     Only the runtime's own refusal takes 90.

use std::cell::Cell;
use std::fmt;

pub const UNSUPPORTED_EXIT: i32 = 90;

/// What went wrong. Reached through [`LypningError::kind`], never stored inline
/// — see the type below for why that indirection is worth a heap allocation on
/// a path that is, by construction, not hot.
#[derive(Debug, Clone)]
pub enum ErrKind {
    /// A construct outside the subset. Safe to retry on another interpreter.
    Unsupported { kind: String, detail: String },
    /// A syntax error. CPython would also fail, so this is NOT routed onward
    /// as a capability gap — but it is reported the way CPython reports it.
    Syntax { line: u32, msg: String },
    /// A Python-level exception the program could have caught — `SystemExit`
    /// included, which is why there is no separate exit variant. See
    /// [`LypningError::is_exit`].
    Exc(Exc),
}

/// One pointer, and the size is the whole point.
///
/// `R<T>` is the return type of essentially every function in this interpreter
/// and there are ~790 `?` operators applying it. `ErrKind` is 48 bytes, so while
/// it lived inline **every** `R<T>` was at least 48 bytes: `R<()>` — twenty
/// functions return it — was 48 bytes of stack traffic to say "nothing went
/// wrong", `R<bool>` likewise, and `R<Value>` carried a discriminant word beside
/// a `Value` that already has a spare tag.
///
/// Boxed, the same three are **8, 16 and 40** bytes: `R<()>` is one register,
/// `R<bool>` is two, and `R<Value>` niche-encodes the error into `Value`'s own
/// tag and costs nothing over a bare `Value`.
///
/// The trade is one heap allocation per error CONSTRUCTED, and it is a good
/// trade here precisely because errors are not control flow in this runtime:
/// nothing raises per element, and the one builtin that raises per call
/// (`next()`'s StopIteration) raises once per call and not once per item. The
/// ~790 `?` sites pay on every single evaluation; the allocation pays when a
/// program is about to stop.
#[derive(Debug, Clone)]
pub struct LypningError(Box<ErrKind>);

#[derive(Debug, Clone)]
pub struct Exc {
    pub kind: &'static str,
    pub msg: String,
}

impl LypningError {
    #[inline]
    pub fn new(kind: ErrKind) -> Self {
        LypningError(Box::new(kind))
    }
    /// The payload. Every site that used to `match` on the enum matches on this.
    #[inline]
    pub fn kind(&self) -> &ErrKind {
        &self.0
    }
    pub fn syntax(line: u32, msg: &str) -> Self {
        Self::new(ErrKind::Syntax {
            line,
            msg: msg.to_string(),
        })
    }
    pub fn exc(kind: &'static str, msg: impl Into<String>) -> Self {
        Self::new(ErrKind::Exc(Exc {
            kind,
            msg: msg.into(),
        }))
    }
    pub fn is_unsupported(&self) -> bool {
        matches!(*self.0, ErrKind::Unsupported { .. })
    }
    /// An uncaught `SystemExit`, read the way CPython's `handle_system_exit`
    /// reads `.code`: the process status, and the text that goes on stderr
    /// when the code was not an integer. `None` for any other error.
    ///
    /// `SystemExit` used to be its own variant, raised by `sys.exit` alone and
    /// invisible to `except`. That was two silent disagreements with CPython:
    /// `raise SystemExit(4)` was an ordinary exception — traceback, exit 1 —
    /// and `try: sys.exit(1) / except SystemExit:` never ran its handler. So
    /// it is one exception now, built the same way by both, caught by the same
    /// clauses as in CPython (`BaseException`, bare `except`, not `Exception`),
    /// and recognised HERE, at the end, by its kind. What `Value::Exc` cannot
    /// carry — the type of the code — the constructor refuses instead; see
    /// `builtins::system_exit_code`.
    pub fn is_exit(&self) -> Option<(i32, Option<&str>)> {
        match &*self.0 {
            ErrKind::Exc(e) if e.kind == "SystemExit" => {
                Some(match crate::builtins::system_exit_code(&e.msg) {
                    crate::value::Value::None => (0, None),
                    crate::value::Value::Bool(b) => (b as i32, None),
                    crate::value::Value::Int(i) => (i as i32, None),
                    _ => (1, Some(e.msg.as_str())),
                })
            }
            _ => None,
        }
    }
}

/// This binary's engine name, from `build.rs` — the head of every refusal line
/// and what `--version` and `route --spectrum` report. One point on the Rust
/// spectrum (`route::SPECTRUM`); the same constant in every target of the crate.
pub const ENGINE: &str = env!("LYPNING_ENGINE");

/// The refusal line, spelled in exactly one place (CLAUDE.md invariant 2 and 9):
/// `<engine>: unsupported: <kind>: <detail>`. Everything that writes one — the
/// error's `Display`, the CLI's option refusal, the embedding's NUL refusal —
/// goes through here, so no variant can ever write a name that is not its own.
pub fn refusal_line(kind: &str, detail: &str) -> String {
    format!("{ENGINE}: unsupported: {kind}: {detail}")
}

impl fmt::Display for LypningError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &*self.0 {
            ErrKind::Unsupported { kind, detail } => f.write_str(&refusal_line(kind, detail)),
            ErrKind::Syntax { line, msg } => write!(f, "  line {line}\nSyntaxError: {msg}"),
            ErrKind::Exc(e) => {
                if e.msg.is_empty() {
                    write!(f, "{}", e.kind)
                } else {
                    write!(f, "{}: {}", e.kind, e.msg)
                }
            }
        }
    }
}

pub fn unsupported(kind: &str, detail: &str) -> LypningError {
    LypningError::new(ErrKind::Unsupported {
        kind: kind.to_string(),
        detail: detail.to_string(),
    })
}

pub type R<T> = Result<T, LypningError>;

pub fn type_err(msg: impl Into<String>) -> LypningError {
    LypningError::exc("TypeError", msg)
}
pub fn value_err(msg: impl Into<String>) -> LypningError {
    LypningError::exc("ValueError", msg)
}
/// Every name CPython 3.11 puts in `builtins`, minus the dunders — 149 of them.
///
/// This is the SAME distinction lypning-mp draws in lypning_unsupported.h and for
/// the same reason (docs/SUBSET.md §7 rule 4): a name CPython HAS and
/// lypning does not is lypning being too small, and must leave by the exit-90
/// contract so the dispatcher retries on an interpreter that has it. A name
/// NEITHER has is the program's own bug and keeps CPython's NameError and
/// exit 1.
///
/// Without the split, `dir(sys)` and `hash("a")` raised NameError at exit 1 —
/// an ordinary traceback, which the dispatcher deliberately does NOT treat as a
/// refusal (re-running would repeat the program's side effects). So the router
/// sent the program to lypning, lypning failed, and the chain stopped: a program
/// CPython runs, reported as broken. That is an UNSAFE route, the one outcome
/// docs/LYPNING.md §3 says must not happen.
static CPYTHON_BUILTINS: &[&str] = &[
    "ArithmeticError", "AssertionError", "AttributeError", "BaseException",
    "BaseExceptionGroup", "BlockingIOError", "BrokenPipeError", "BufferError",
    "BytesWarning", "ChildProcessError", "ConnectionAbortedError",
    "ConnectionError", "ConnectionRefusedError", "ConnectionResetError",
    "DeprecationWarning", "EOFError", "Ellipsis", "EncodingWarning",
    "EnvironmentError", "Exception", "ExceptionGroup", "False",
    "FileExistsError", "FileNotFoundError", "FloatingPointError",
    "FutureWarning", "GeneratorExit", "IOError", "ImportError",
    "ImportWarning", "IndentationError", "IndexError", "InterruptedError",
    "IsADirectoryError", "KeyError", "KeyboardInterrupt", "LookupError",
    "MemoryError", "ModuleNotFoundError", "NameError", "None",
    "NotADirectoryError", "NotImplemented", "NotImplementedError", "OSError",
    "OverflowError", "PendingDeprecationWarning", "PermissionError",
    "ProcessLookupError", "RecursionError", "ReferenceError",
    "ResourceWarning", "RuntimeError", "RuntimeWarning", "StopAsyncIteration",
    "StopIteration", "SyntaxError", "SyntaxWarning", "SystemError",
    "SystemExit", "TabError", "TimeoutError", "True", "TypeError",
    "UnboundLocalError", "UnicodeDecodeError", "UnicodeEncodeError",
    "UnicodeError", "UnicodeTranslateError", "UnicodeWarning", "UserWarning",
    "ValueError", "Warning", "ZeroDivisionError", "__build_class__",
    "__debug__", "__doc__", "__import__", "__loader__", "__package__",
    "__spec__", "abs", "aiter", "all",
    "anext", "any", "ascii", "bin", "bool", "breakpoint", "bytearray", "bytes",
    "callable", "chr", "classmethod", "compile", "complex", "copyright",
    "credits", "delattr", "dict", "dir", "divmod", "enumerate", "eval", "exec",
    "exit", "filter", "float", "format", "frozenset", "getattr", "globals",
    "hasattr", "hash", "help", "hex", "id", "input", "int", "isinstance",
    "issubclass", "iter", "len", "license", "list", "locals", "map", "max",
    "memoryview", "min", "next", "object", "oct", "open", "ord", "pow",
    "print", "property", "quit", "range", "repr", "reversed", "round", "set",
    "setattr", "slice", "sorted", "staticmethod", "str", "sum", "super",
    "tuple", "type", "vars", "zip",
];

pub fn is_cpython_builtin(name: &str) -> bool {
    CPYTHON_BUILTINS.contains(&name)
}

/// An undefined name: lypning being small, or the program being wrong.
pub fn name_err(name: &str) -> LypningError {
    if is_cpython_builtin(name) {
        return unsupported("builtin", name);
    }
    LypningError::exc("NameError", format!("name '{name}' is not defined"))
}
pub fn index_err(msg: impl Into<String>) -> LypningError {
    LypningError::exc("IndexError", msg)
}
pub fn key_err(msg: impl Into<String>) -> LypningError {
    LypningError::exc("KeyError", msg)
}
pub fn attr_err(msg: impl Into<String>) -> LypningError {
    LypningError::exc("AttributeError", msg)
}
/// CPython raises OverflowError where a float result leaves the double range
/// or a float cannot become an integer. lypning must NOT return infinity or a
/// saturated i64 for these: both are answers, and a wrong answer at exit 0 is
/// the outcome the whole refusal contract exists to prevent.
pub fn overflow_err(msg: impl Into<String>) -> LypningError {
    LypningError::exc("OverflowError", msg)
}

pub fn zero_div(msg: &str) -> LypningError {
    LypningError::exc("ZeroDivisionError", msg)
}


// ---- the stack, and why running out of it is a refusal ----------------------
//
// A tree-walking interpreter recurses over the shape of its input, so the depth
// of a program's data is the depth of its own call stack. Three descents here
// are driven by text or values a program supplies — `fmt::repr` over nested
// containers, `value::hkey` over nested tuples, `json`'s parser over nested
// arrays — and every one of them was measured overflowing the stack and taking
// the process down with SIGSEGV.
//
// In the binary that is a crashed one-liner, which is bad enough: 139 is not 90,
// so the dispatcher does not fall onward and a program CPython answers is
// reported as broken. Embedded it is far worse — the segfault is the HOST's,
// and an application that merely asked to run a one-liner dies with no
// traceback and nothing to catch, because a stack overflow is not an unwind and
// `catch_unwind` cannot see it.
//
// So the depth is bounded before the stack is, and the bound is expressed the
// way every other limit in this runtime is expressed: as a refusal, which
// routes the program to CPython and gets it its real answer.

/// Measured, not guessed. On this build a nested `repr` cost roughly 80 bytes
/// of stack per level and overflowed an 8 MB stack somewhere past 50,000
/// levels; 500 leaves three orders of magnitude of margin, which is what makes
/// it safe on a host thread whose stack is 1 MB rather than the main thread's 8.
/// CPython raises `RecursionError` on the same programs at a comparable depth,
/// so the refusal routes onward into an error, not into a different answer.
pub const MAX_NEST: u32 = 500;

thread_local! {
    static NEST: Cell<u32> = const { Cell::new(0) };
}

/// One level of a value-shaped recursion, released on drop.
///
/// Drop rather than a matching decrement, because every one of these descents
/// is written with `?` on almost every line: a hand-balanced counter would leak
/// a level on each early return and the limit would tighten with every error a
/// long-running host had ever seen.
pub struct Nest;

impl Nest {
    pub fn enter(what: &str) -> R<Nest> {
        let n = NEST.with(|n| {
            let v = n.get() + 1;
            n.set(v);
            v
        });
        if n > MAX_NEST {
            NEST.with(|c| c.set(c.get() - 1));
            return Err(unsupported(
                "recursion",
                &format!("{what} nested deeper than {MAX_NEST}"),
            ));
        }
        Ok(Nest)
    }
}

impl Drop for Nest {
    fn drop(&mut self) {
        NEST.with(|n| n.set(n.get().saturating_sub(1)));
    }
}

/// Return the counter to zero between runs in one process.
///
/// A panic unwinds through the `Drop`s and would balance on its own; an abort
/// does not, and neither does a future descent that forgets to hold the guard.
/// Resetting on the way in costs one store and removes the whole class.
pub fn reset_nesting() {
    NEST.with(|n| n.set(0));
}
