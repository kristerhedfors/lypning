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
//! as before. Four covers `print(a, b, c, d)` and everything narrower, which is
//! very nearly all of the corpus.
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

use crate::value::Value;
use std::ops::Deref;

/// Arguments held without allocating. Four, because five is `print(a,b,c,d,e)`.
pub const INLINE: usize = 4;

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
