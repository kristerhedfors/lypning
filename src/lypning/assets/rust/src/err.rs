//! Errors, and the unsupported-feature contract that makes routing safe.
//!
//! lypning inherits lypning-mp's contract verbatim (`docs/SUBSET.md`):
//!
//!   exit 90, one line on **stderr**: `lypning: unsupported: <kind>: <detail>`
//!
//! 90 is clear of 0/1/2 (python's own), 126/127 (shell) and 128+n (signals), so
//! the dispatcher can branch on it unambiguously and retry with the next
//! interpreter. Two rules from the lypning-mp experience carry over:
//!
//!   * The line goes to **stderr**. lypning-mp once wrote tracebacks to stdout and
//!     poisoned every `… | wc -l` pipeline while the exit code looked right.
//!   * A program's OWN `NotImplementedError` must keep its traceback and exit 1.
//!     Only the runtime's own refusal takes 90.

use std::fmt;

pub const UNSUPPORTED_EXIT: i32 = 90;

#[derive(Debug, Clone)]
pub enum LypningError {
    /// A construct outside the subset. Safe to retry on another interpreter.
    Unsupported { kind: String, detail: String },
    /// A syntax error. CPython would also fail, so this is NOT routed onward
    /// as a capability gap — but it is reported the way CPython reports it.
    Syntax { line: u32, msg: String },
    /// A Python-level exception the program could have caught.
    Exc(Exc),
    /// `sys.exit(n)` / SystemExit.
    Exit(i32),
}

#[derive(Debug, Clone)]
pub struct Exc {
    pub kind: &'static str,
    pub msg: String,
}

impl LypningError {
    pub fn syntax(line: u32, msg: &str) -> Self {
        LypningError::Syntax {
            line,
            msg: msg.to_string(),
        }
    }
    pub fn exc(kind: &'static str, msg: impl Into<String>) -> Self {
        LypningError::Exc(Exc {
            kind,
            msg: msg.into(),
        })
    }
    pub fn is_unsupported(&self) -> bool {
        matches!(self, LypningError::Unsupported { .. })
    }
}

impl fmt::Display for LypningError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            LypningError::Unsupported { kind, detail } => {
                write!(f, "lypning: unsupported: {kind}: {detail}")
            }
            LypningError::Syntax { line, msg } => write!(f, "  line {line}\nSyntaxError: {msg}"),
            LypningError::Exc(e) => {
                if e.msg.is_empty() {
                    write!(f, "{}", e.kind)
                } else {
                    write!(f, "{}: {}", e.kind, e.msg)
                }
            }
            LypningError::Exit(n) => write!(f, "SystemExit: {n}"),
        }
    }
}

pub fn unsupported(kind: &str, detail: &str) -> LypningError {
    LypningError::Unsupported {
        kind: kind.to_string(),
        detail: detail.to_string(),
    }
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
    "ValueError", "Warning", "ZeroDivisionError", "abs", "aiter", "all",
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
