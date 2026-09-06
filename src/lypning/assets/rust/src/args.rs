//! The argument list, and why it is not a `Vec`.
//!
//! Every call in a Python program builds one of these and drops it again. When
//! it was a `Vec<Value>`, that was a `malloc` and a `free` per call — and on
//! this build, measured on this container, that pair costs **more than an
//! entire zero-argument call**:
//!
//! ```text
//! def f(...): return 1     us/call     delta vs 0 args
//!   f()        0 args       0.258            —
//!   f(1)       1 arg        0.446         +0.188
//!   f(1, 2)    2 args       0.461         +0.203
//!   f(1, 2, 3) 3 args       0.494         +0.236
//! ```
//!
//! The step is at the FIRST argument, not per argument: 0.188 µs to go from
//! none to one, then 0.024 µs each after that. That shape is an allocation, not
//! work — which agrees with callgrind, where musl's `malloc` and `free` are
//! about a quarter of the interpreter's whole instruction stream.
//!
//! So arguments live in the caller's stack frame until there are more than
//! [`INLINE`] of them, at which point they spill to a `Vec` and behave exactly
//! as before.
//!
//! [`INLINE`] is **two**, and that is a measurement rather than a guess. The
//! array is initialised on every call whether or not it is used, so a wider one
//! taxes the zero- and one-argument calls that dominate — `len(x)`, `str(x)`,
//! `open(p)`, `x.split(s)`, `f(x)` — to spare the three-argument ones that do
//! not. Swept against the whole `lypning perf` suite, which weights each case
//! by how much of the corpus types it:
//!
//! ```text
//!   Vec (before)  3.72x CPython      INLINE=3      3.67x
//!   INLINE=2      3.55x  <- kept     INLINE=4      3.63x
//! ```
//!
//! Two costs `print(a, b, c)` about 2%, and buys 24% on `len(t)`, 26% on
//! `t.count(c)` and 11% on recursive calls. Re-sweep before changing it; the
//! answer is a property of this corpus and of musl's allocator, not of the
//! code.
//!
//! **No `unsafe`.** The vacated slots hold `Value::None`, which is a discriminant
//! write and allocates nothing, so the array can be a plain `[Value; INLINE]`
//! and [`Deref`] can hand out a real `&[Value]`. That last part is what keeps
//! this from being a refactor of every call site: `args.first()`, `args.get(i)`,
//! `args.len()` and `args.iter()` all still work, unchanged, through the deref.
//!
//! The one invariant, and everything here depends on it: **`spilled` says which
//! half holds the values.** It is a flag rather than `spill.is_empty()` because
//! a spilled list has to be allocated at its FINAL size in one go. Growing into
//! it instead — start inline, move four values across, then push — was measured
//! at eight times the cost of the `Vec` this replaces for a six-argument call,
//! and only for six: another musl size-class resonance of the kind
//! `docs/HILLCLIMB.md` iteration 1 already recorded. The caller knows the
//! argument count before it starts, so it never has to find out the hard way.

use crate::err::{type_err, LypningError, R};
use crate::value::Value;
use std::ops::Deref;
use std::rc::Rc;

/// Arguments held without allocating. Two — see the module note; it is swept,
/// not chosen.
pub const INLINE: usize = 2;

pub struct Args {
    inline: [Value; INLINE],
    n: usize,
    spill: Vec<Value>,
    spilled: bool,
}

impl Default for Args {
    fn default() -> Self {
        Args::new()
    }
}

impl Args {
    pub fn new() -> Self {
        Args {
            inline: std::array::from_fn(|_| Value::None),
            n: 0,
            // `Vec::new` does not allocate, so an empty argument list costs
            // nothing beyond the stack the array already occupies.
            spill: Vec::new(),
            spilled: false,
        }
    }

    /// Below [`INLINE`] the hint is ignored, which is the whole point. Above it,
    /// the hint is honoured EXACTLY and once — see the module note on why
    /// growing into the spill is the expensive way to get there.
    pub fn with_capacity(cap: usize) -> Self {
        let mut a = Args::new();
        if cap > INLINE {
            a.spill = Vec::with_capacity(cap);
            a.spilled = true;
        }
        a
    }

    /// The one-argument list, which is most of them.
    pub fn one(v: Value) -> Self {
        let mut a = Args::new();
        a.push(v);
        a
    }

    pub fn push(&mut self, v: Value) {
        if self.spilled {
            self.spill.push(v);
            return;
        }
        if self.n < INLINE {
            self.inline[self.n] = v;
            self.n += 1;
            return;
        }
        // Reached only when the caller did not know the count up front — a
        // `*args` unpack, or `Args::new()` pushed past INLINE. Correct, and the
        // slow way in; `with_capacity` is the fast one.
        let mut sp = Vec::with_capacity(INLINE * 2);
        for i in 0..self.n {
            sp.push(std::mem::replace(&mut self.inline[i], Value::None));
        }
        sp.push(v);
        self.n = 0;
        self.spill = sp;
        self.spilled = true;
    }

    /// `Vec::remove`, including its panic on an out-of-range index — every
    /// caller here removes index 0 after checking that there is one.
    pub fn remove(&mut self, i: usize) -> Value {
        if self.spilled {
            return self.spill.remove(i);
        }
        let out = std::mem::replace(&mut self.inline[i], Value::None);
        for j in i..self.n - 1 {
            self.inline[j] = std::mem::replace(&mut self.inline[j + 1], Value::None);
        }
        self.n -= 1;
        out
    }

    /// Move the value at `i` out, leaving `None` behind and the length alone.
    ///
    /// This is what binding parameters uses instead of `into_iter`: it takes
    /// each argument by value, in order, without turning the list into a `Vec`
    /// first — which would put back the allocation this type exists to remove.
    pub fn take(&mut self, i: usize) -> Value {
        if self.spilled {
            return std::mem::replace(&mut self.spill[i], Value::None);
        }
        std::mem::replace(&mut self.inline[i], Value::None)
    }

    /// Everything, as a `Vec`. Allocates — for the cold paths only.
    pub fn into_vec(mut self) -> Vec<Value> {
        if self.spilled {
            return std::mem::take(&mut self.spill);
        }
        let mut v = Vec::with_capacity(self.n);
        for i in 0..self.n {
            v.push(std::mem::replace(&mut self.inline[i], Value::None));
        }
        v
    }
}

/// One POSITIONAL-OR-KEYWORD parameter, bound the way CPython binds one.
///
/// Every call site that reads a parameter which may arrive either way used to
/// spell it `args.get(i).cloned().or_else(|| kwget(&kw, "name"))` — fifteen of
/// them across `builtins.rs` and `methods.rs`, plus one in `pathlib.rs`. That
/// idiom is `Option::or_else`, and `or_else` does not run when the first option
/// is `Some`: **the keyword was silently discarded whenever a positional was
/// also given**, so the same parameter supplied twice answered at exit 0 with
/// the positional's value where CPython raises.
///
/// ```text
/// b"abcd".hex("-", sep=":")            this: '61-62-63-64'   cpython: TypeError
/// "ab".encode("ascii", encoding="l1")  this: b'ab'           cpython: TypeError
/// "a b".split(" ", sep=",")            this: ['a', 'b']      cpython: TypeError
/// round(2.5, number=9)                 this: 2               cpython: TypeError
/// open(p, "r", mode="w")               this: opened for READ cpython: TypeError
/// ```
///
/// The last one is the shape that says why this is not a cosmetic disagreement:
/// two spellings of the same parameter is what a program looks like when it was
/// EDITED — a keyword added without the positional being removed — and the
/// answer lypning gave was the one the edit meant to replace. CPython refuses
/// the call rather than choose, and so does this.
///
/// The message is CPython's own for a C function with positional-or-keyword
/// parameters (`_PyArg_UnpackKeywords`), which names the bare function and the
/// 1-based POSITION rather than the type — `str.split` reports `split()`. It is
/// a `TypeError` and not a refusal on purpose: a refusal is exit 90 and would
/// send a program CPython rejects off to CPython to be rejected there, one
/// spawn later and with the same answer. Raising costs nothing and is exact.
///
/// CPython checks the TOTAL argument count first, so two positionals plus a
/// duplicating keyword reports `takes at most N arguments (M given)` instead;
/// this reports the duplicate for that shape too. Same exit code and the same
/// empty stdout, so `conformance.classify` — which compares those two and not
/// stderr — cannot tell them apart. `tests/test_keyword_grid.py` DOES compare
/// the sentence, which is why its rows are the one-positional shape only.
pub fn bind(
    args: &Args,
    kw: &[(Rc<str>, Value)],
    pos: usize,
    name: &str,
    func: &str,
) -> R<Option<Value>> {
    // The keyword list is walked only when there IS one, which for almost every
    // call in the corpus is never: `kw` is empty and this is a slice-index and a
    // branch, the same work the `or_else` spelling did.
    let named = if kw.is_empty() {
        None
    } else {
        kw.iter().find(|(k, _)| k.as_ref() == name)
    };
    match (args.get(pos), named) {
        (Some(_), Some(_)) => Err(given_twice(func, name, pos)),
        (Some(v), None) => Ok(Some(v.clone())),
        (None, Some((_, v))) => Ok(Some(v.clone())),
        (None, None) => Ok(None),
    }
}

/// Out of line and `#[cold]`: two allocations on a path that never answers, and
/// [`bind`] is on the hottest argument-reading calls the interpreter has.
#[cold]
#[inline(never)]
fn given_twice(func: &str, name: &str, pos: usize) -> LypningError {
    type_err(format!(
        "argument for {func}() given by name ('{name}') and position ({})",
        pos + 1
    ))
}

impl Clone for Args {
    fn clone(&self) -> Self {
        self.iter().cloned().collect()
    }
}

impl Deref for Args {
    type Target = [Value];

    fn deref(&self) -> &[Value] {
        if self.spilled {
            &self.spill
        } else {
            &self.inline[..self.n]
        }
    }
}

impl Extend<Value> for Args {
    fn extend<I: IntoIterator<Item = Value>>(&mut self, it: I) {
        for v in it {
            self.push(v);
        }
    }
}

impl FromIterator<Value> for Args {
    fn from_iter<I: IntoIterator<Item = Value>>(it: I) -> Self {
        let mut a = Args::new();
        a.extend(it);
        a
    }
}

impl From<Vec<Value>> for Args {
    fn from(v: Vec<Value>) -> Self {
        v.into_iter().collect()
    }
}

impl IntoIterator for Args {
    type Item = Value;
    type IntoIter = std::vec::IntoIter<Value>;

    /// Allocates. Deliberately kept for the cold callers (`map`, `filter`,
    /// `join`); the hot one — binding a function's parameters — uses
    /// [`Args::take`] instead.
    fn into_iter(self) -> Self::IntoIter {
        self.into_vec().into_iter()
    }
}
