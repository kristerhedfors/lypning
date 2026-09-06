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
//!
//! The other half of "what the process provides" is what CPython reads **before
//! the program starts** and then never mentions again. There is one so far —
//! [`int_max_str_digits`] — and it belongs here rather than in `bigint.rs` for
//! two reasons: an invalid setting stops CPython from starting at all, so the
//! check is on the run path of every variant and not inside a capability only
//! one of them has; and a number that was compiled in as a constant is a number
//! nobody thinks to re-read.

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

// ---- what CPython reads before the program starts --------------------------

/// CPython's default `sys.get_int_max_str_digits()`, and the ceiling this
/// engine will raise its own to. See [`int_max_str_digits`].
pub const DEFAULT_MAX_STR_DIGITS: usize = 4300;

/// CPython's `sys.int_info.str_digits_check_threshold`. Every limit CPython
/// accepts is either 0 or at least this, so a conversion with no more digits
/// than this is under **every** possible limit and the environment need not be
/// read at all. The check sites use it as exactly that screen — one `getenv` on
/// the rare wide conversion, none on the common one.
pub const STR_DIGITS_CHECK_THRESHOLD: usize = 640;

/// Is a conversion between a string of `digits` digits and an integer in `base`
/// past the limit CPython applies to it? **The one place that rule lives.**
///
/// Two halves, both of them CPython's and neither an approximation.
///
/// **The count is of DIGITS, and on the way IN those are the input string's,
/// not the result's.** `int('0' * 5000 + '1')` is a `ValueError` in CPython —
/// it reports 5001 digits — and answered `1` here, because the only screen sat
/// on the arm that `i64::from_str_radix` reached by overflowing, and a literal
/// that fits an `i64` never reaches it however long it is. Leading zeros are
/// counted; a sign, surrounding whitespace and underscores are not, so the
/// caller passes the digit run it has already stripped and cleaned. On the way
/// OUT the count is the digits about to be written, which is the same number.
///
/// **A power-of-two base is EXEMPT.** Bases 2, 4, 8, 16 and 32 convert in
/// linear time, so CPython applies no limit to them and every other base in
/// `2..=36` gets one: `int('1' * 100000, 2)` and `hex(10**5000)` answer while
/// `int('1' * 4301, 3)` and `int('z' * 4301, 36)` raise. Checked against
/// CPython 3.14.5 for all 35 bases and both directions before it was written
/// here. Base 10 was the only base screened, which left 3, 5, 6, 7, 9, 11..15,
/// 17..31 and 33..36 answering at exit 0.
///
/// The environment is read only when `digits` is past CPython's own
/// `str_digits_check_threshold`, below which no accepted limit can bite — so
/// `print(2**100)` costs no `getenv` at all and only a conversion already
/// hundreds of digits wide pays for one. That test is written first because it
/// is one compare and it is false for every ordinary program; the base test is
/// second because it is one instruction and it is the whole of `hex()`.
///
/// A `PYTHONINTMAXSTRDIGITS` CPython would not start with answers `true`, so
/// the caller refuses; `main.rs` has already refused the run for the same
/// reason, and this keeps the library path honest for a host that has not.
pub fn over_str_digit_limit(digits: usize, base: u32) -> bool {
    digits > STR_DIGITS_CHECK_THRESHOLD
        && !base.is_power_of_two()
        && int_max_str_digits().map_or(true, |lim| digits > lim)
}

/// `sys.get_int_max_str_digits()` as this process would see it, or `Err` when
/// `PYTHONINTMAXSTRDIGITS` holds something CPython refuses to start with.
///
/// The limit is **not** the constant 4300 it was written as. CPython reads
/// `PYTHONINTMAXSTRDIGITS` at interpreter start: 0 means unlimited, anything
/// else must be at least [`STR_DIGITS_CHECK_THRESHOLD`], and anything else
/// again is `Fatal Python error: config_init_int_max_str_digits` — exit 1, no
/// program run, nothing on stdout. Answering such a program at exit 0 is the
/// worst outcome this project has, so the `Err` is a refusal and CPython
/// produces its own fatal error one spawn later.
///
/// **The value is only ever LOWERED, never raised** — `min(env, 4300)`. That is
/// two decisions in one line. It keeps `to_dec`'s O(n²) decimal conversion
/// inside the budget it already had, next to `MAX_BITS` and `DIV_BUDGET_BITS`;
/// and it makes `-E` and `-I` — which this binary accepts and ignores, and
/// which tell CPython to ignore every `PYTHON*` variable — safe for free, since
/// under them CPython's limit is the default and a limit we never raise above
/// the default can only refuse where CPython answers. A raised limit honoured
/// literally would have answered a 100,000-digit `str()` that `python3 -E`
/// raises `ValueError` for: a wrong answer at exit 0, which is what this
/// function exists to prevent. So `PYTHONINTMAXSTRDIGITS=0` (unlimited) buys
/// coverage from CPython, not from here — a refusal, which invariant 1 says is
/// never a bug.
pub fn int_max_str_digits() -> Result<usize, ()> {
    let Some(raw) = std::env::var_os("PYTHONINTMAXSTRDIGITS") else {
        return Ok(DEFAULT_MAX_STR_DIGITS);
    };
    // CPython's `_Py_GetEnv` treats an EMPTY variable as unset, so
    // `PYTHONINTMAXSTRDIGITS= python3` is the default and not an error. A
    // non-UTF-8 value cannot be a decimal number, so it is invalid.
    let s = match raw.to_str() {
        Some("") => return Ok(DEFAULT_MAX_STR_DIGITS),
        Some(s) => s,
        None => return Err(()),
    };
    match strtol10(s) {
        // 0 is CPython's "unlimited"; the ceiling above is why it is not.
        Some(0) => Ok(DEFAULT_MAX_STR_DIGITS),
        Some(v) if v >= STR_DIGITS_CHECK_THRESHOLD as i64 => {
            Ok((v as usize).min(DEFAULT_MAX_STR_DIGITS))
        }
        _ => Err(()),
    }
}

/// CPython's `_Py_str_to_int`: `strtol(s, &end, 10)` with `*end == '\0'`
/// required and the result held to a C `int`.
///
/// Written out rather than handed to `str::parse` because the two disagree on
/// four inputs that all appear in a shell: `" 640"` is accepted (strtol skips
/// leading whitespace) while `"640 "` is not, `"+640"` and `"0640"` are 640,
/// and `"0x280"` is a parse that stops at the `x` and therefore an error rather
/// than 640. Each was checked against CPython 3.14.5 before it was written
/// here; `tests/test_bigint_grid.py` keeps them checked.
fn strtol10(s: &str) -> Option<i64> {
    let b = s.as_bytes();
    let mut i = 0;
    while i < b.len() && (b[i] == b' ' || (0x09..=0x0d).contains(&b[i])) {
        i += 1;
    }
    let neg = match b.get(i) {
        Some(b'-') => {
            i += 1;
            true
        }
        Some(b'+') => {
            i += 1;
            false
        }
        _ => false,
    };
    let start = i;
    let mut v: i64 = 0;
    while i < b.len() && b[i].is_ascii_digit() {
        // Overflow is CPython's `errno == ERANGE`, and the `int` ceiling is its
        // `value > INT_MAX`. Both are the same answer here: not a limit.
        v = v * 10 + (b[i] - b'0') as i64;
        if v > i32::MAX as i64 {
            return None;
        }
        i += 1;
    }
    if i == start || i != b.len() {
        return None;
    }
    Some(if neg { -v } else { v })
}
