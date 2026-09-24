//! `random.Random(int)`, `random.sample`, `random.shuffle` and four spellings
//! of `sys.version_info`: the `cap-random` capability.
//!
//! The stream is `random.rs`'s — MT19937 exactly as `_randommodule.c` runs it,
//! with `_randbelow_with_getrandbits` on top — and this file adds nothing to
//! it. What it adds is a second STATE and the two Python-level algorithms of
//! `random.py` that consume the stream in an order a reimplementation can get
//! plausibly wrong:
//!
//! * **`sample(population, k)`** — the set-size rule and its two branches. A
//!   pool of `n` survives when `n <= setsize`, where `setsize` is 21 plus, for
//!   `k > 5`, `4 ** ceil(log(3k, 4))`. `3k` is never a power of four, so that is
//!   the smallest power of four ABOVE `3k`, computed here in integers — no
//!   libm, which is the trap `math.rs` exists to stay out of. The pool branch
//!   draws `below(n - i)` and moves the last live element into the hole; the
//!   other draws `below(n)` and redraws on a repeat.
//! * **`shuffle(x)`** — `for i in reversed(range(1, len(x))): j = below(i + 1)`,
//!   swap. The same on every version this package supports (3.11 only dropped
//!   the `random=` argument, which is refused here as an extra positional).
//!
//! # The instance is an `Iter`, not a `Value`
//!
//! For the reason `hashlib.rs` gives in full: a new `Value` variant reaches
//! `eq`, `hash`, `repr`, `bool`, `len`, `in` and a dozen arms nothing forces you
//! to remember, and five capabilities in a row answered one of them wrongly at
//! exit 0 (`docs/HILLCLIMB.md` iterations 74, 76, 77). A `Random` is
//! `Value::IterObj(Iter::Rng(..), "Random")`, and every one of those arms is
//! already right or already a refusal for it: `repr`/`str`/`type()` refuse (the
//! repr holds an address), `==`/`is` are identity (CPython's `object.__eq__`),
//! `bool` is `True`, a dict key refuses (hashed by identity), and an operator
//! is a `TypeError` naming `'Random'`, which is CPython's `tp_name`. The arms an
//! iterator has and a `Random` does not — iteration and `in` — refuse; the
//! attribute surface is [`METHODS`], and every other name refuses rather than
//! raising, because CPython answers `gauss`, `choices` and `getstate`.
//!
//! An instance method runs `random::call` against the instance's OWN state by
//! swapping it into `Interp::rng` for the call and back out after, so there is
//! one implementation of `randint`, `randrange`, `choice`, `getrandbits`,
//! `random` and `seed`, and it is the one the module-level functions already
//! use. `Random(n)` is `seed(n)` into a fresh slot, for the same reason — its
//! refusals (`str`, `bytes` and `float` seeds, a seed past 64 bits) are
//! `seed`'s own.
//!
//! # `sys.version_info`
//!
//! Grounded to the MINOR version only: `err::REF_PY_MINOR` is what `build.rs`
//! measured on the reference interpreter and `doctor` checks, and nothing
//! checks the patch level. So only the spellings whose answer is the major and
//! minor are served — `[0]`, `[1]`, `[:n]` for `n <= 2`, `.major`, `.minor`,
//! and a comparison with a tuple of at most two items, which only ever reads
//! those two (a longer `version_info` is greater than an equal prefix, and a
//! third placeholder item stands in for that length and is never compared). It
//! is resolved at the PARENT expression in `eval.rs`, so `sys.version_info`
//! never becomes a value: anywhere else it is `modules::get_attr`'s
//! `module-attr` refusal, at the same point the core refuses it. A build whose
//! reference version was guessed (`err::REF_PY_MEASURED`) serves none of it.

use crate::args::Args;
use crate::ast::{CmpOp, Expr};
use crate::err::{unsupported, LypningError, R};
use crate::eval::Interp;
use crate::iter::Iter;
use crate::value::{ival, list, Value};
use std::cell::RefCell;
use std::rc::Rc;

/// The type name CPython's error messages print for an instance.
pub const TYPE: &str = "Random";

/// The attributes an instance serves — every one a method, and all of them
/// `route::CAP_METHODS`'s `random` row, which a test holds to this list.
pub const METHODS: &[&str] =
    &["choice", "getrandbits", "randint", "random", "randrange", "sample", "seed", "shuffle"];

fn refuse(what: &str) -> LypningError {
    unsupported("random", what)
}

/// The instance state inside a value, if it is a `Random`.
pub fn as_random(v: &Value) -> Option<&Rc<RefCell<Iter>>> {
    match v {
        Value::IterObj(i, k) if *k == TYPE => Some(i),
        _ => None,
    }
}

/// `random.Random(n)`: `seed(n)` into a fresh state, the global one untouched.
pub fn construct(it: &mut Interp, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    if !kw.is_empty() || args.len() != 1 {
        return Err(refuse("random.Random() with no seed, a keyword or two arguments"));
    }
    let global = it.rng.take();
    let r = crate::random::call(it, "seed", args, &[]);
    let mine = std::mem::replace(&mut it.rng, global);
    r?;
    Ok(Value::IterObj(Rc::new(RefCell::new(Iter::Rng(mine))), TYPE))
}

/// `r.<name>` as a value: a bound method for [`METHODS`], a refusal for the
/// rest of CPython's surface.
pub fn attr(recv: &Value, name: &str) -> R<Value> {
    match METHODS.iter().find(|m| **m == name) {
        Some(m) => Ok(Value::Bound(Rc::new(recv.clone()), m)),
        None => Err(refuse("an attribute of a Random instance outside its eight methods")),
    }
}

/// `r.<name>(…)`: the instance's state swapped into the interpreter's slot for
/// the length of the call, so the module's implementation draws from it.
pub fn method(
    it: &mut Interp,
    cell: &Rc<RefCell<Iter>>,
    name: &str,
    args: &mut Args,
    kw: &[(Rc<str>, Value)],
) -> R<Value> {
    if !METHODS.contains(&name) {
        return Err(refuse("an attribute of a Random instance outside its eight methods"));
    }
    let mine = match &mut *cell.borrow_mut() {
        Iter::Rng(m) => m.take(),
        _ => None,
    };
    let global = std::mem::replace(&mut it.rng, mine);
    let r = match name {
        "sample" | "shuffle" => call(it, name, args, kw),
        _ => crate::random::call(it, name, args, kw),
    };
    let mine = std::mem::replace(&mut it.rng, global);
    if let Iter::Rng(m) = &mut *cell.borrow_mut() {
        *m = mine;
    }
    r
}

/// `random.sample` / `random.shuffle` on whatever state is in `Interp::rng`.
pub fn call(it: &mut Interp, name: &str, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    if !kw.is_empty() {
        return Err(refuse("random.sample()/shuffle() with keyword arguments"));
    }
    if it.rng.is_none() {
        return Err(refuse("unseeded stream — CPython seeds it from the OS, which is not reproducible"));
    }
    match name {
        "shuffle" => shuffle(it, args),
        _ => sample(it, args),
    }
}

fn shuffle(it: &mut Interp, args: &mut Args) -> R<Value> {
    // A tuple or a str is CPython's TypeError, a dict its KeyError: the
    // program's errors, whose wording is not this file's to write.
    let l = match (args.len(), args.first()) {
        (1, Some(Value::List(l))) => l.clone(),
        _ => return Err(refuse("random.shuffle() of anything but one list")),
    };
    let rng = it.rng.as_mut().expect("seeded");
    let n = l.borrow().len();
    for i in (1..n).rev() {
        let j = rng.below(i as u64 + 1) as usize;
        l.borrow_mut().swap(i, j);
    }
    Ok(Value::None)
}

fn sample(it: &mut Interp, args: &mut Args) -> R<Value> {
    if args.len() != 2 {
        return Err(refuse("random.sample() without exactly two arguments"));
    }
    let pop = args[0].clone();
    let n = match &pop {
        Value::List(l) => l.borrow().len(),
        Value::Tuple(t) => t.len(),
        Value::Bytes(b) => b.len(),
        Value::Str(s) => s.chars().count(),
        Value::Range(a, b, st) => {
            let (a, b, st) = (*a as i128, *b as i128, *st as i128);
            let len = if st > 0 { (b - a + st - 1) / st } else { (a - b - st - 1) / -st };
            usize::try_from(len.max(0)).map_err(|_| refuse("random.sample() of a range past usize"))?
        }
        // A set or a dict is CPython's TypeError ("Population must be a
        // sequence"), and an iterator the same: its message to print.
        _ => return Err(refuse("random.sample() of anything but a list, tuple, str, bytes or range")),
    };
    let k = match &args[1] {
        Value::Int(i) => i.get()?,
        Value::Bool(b) => *b as i64,
        _ => return Err(refuse("random.sample() with a k that is not an int")),
    };
    // `ValueError: Sample larger than population or is negative` — refused, as
    // every error path in `random.rs` is.
    if k < 0 || k as u64 > n as u64 {
        return Err(refuse("random.sample() larger than the population or negative"));
    }
    let k = k as usize;
    let mut setsize = 21u128;
    if k > 5 {
        let mut p = 1u128;
        while p <= 3 * k as u128 {
            p *= 4;
        }
        setsize += p;
    }
    let mut out = Vec::with_capacity(k);
    if n as u128 <= setsize {
        let mut pool = Vec::with_capacity(n);
        for j in 0..n {
            pool.push(it.index(&pop, &ival(j as i64))?);
        }
        let rng = it.rng.as_mut().expect("seeded");
        for i in 0..k {
            let j = rng.below((n - i) as u64) as usize;
            out.push(pool[j].clone());
            pool[j] = pool[n - i - 1].clone();
        }
    } else {
        // `selected` as a bitmap over the indices: one bit a slot, so a
        // `range(10**6)` costs 125 KB and no hashing or tree code is linked for
        // it. Past 2**27 slots (16 MiB) the population is refused instead.
        if n > 1 << 27 {
            return Err(refuse("random.sample() of a population past 2**27"));
        }
        let rng = it.rng.as_mut().expect("seeded");
        let mut seen = vec![0u64; n / 64 + 1];
        let mut picks = Vec::with_capacity(k);
        for _ in 0..k {
            let mut j = rng.below(n as u64) as usize;
            while seen[j / 64] >> (j % 64) & 1 == 1 {
                j = rng.below(n as u64) as usize;
            }
            seen[j / 64] |= 1 << (j % 64);
            picks.push(j);
        }
        for j in picks {
            out.push(it.index(&pop, &ival(j as i64))?);
        }
    }
    Ok(list(out))
}

// ---- sys.version_info --------------------------------------------------------

/// Is the build allowed to answer `sys.version_info` at all?
pub const VERSION_INFO: bool = crate::err::REF_PY_MEASURED;

fn vi_refuse(what: &str) -> LypningError {
    unsupported("module-attr", what)
}

/// `e`'s value, or `None` when `e` IS `sys.version_info` on a build that
/// serves it — the one spelling that must never become a value. Everything
/// else is evaluated exactly as `eval` would, the attribute included, so a
/// `version_info` on some other object is that object's business.
fn eval_vi(it: &mut Interp, e: &Expr) -> R<Option<Value>> {
    if let Expr::Attr(b, n) = e {
        if n.as_ref() == "version_info" && VERSION_INFO {
            let bv = it.eval(b)?;
            if matches!(bv, Value::Module("sys")) {
                return Ok(None);
            }
            return it.get_attr(&bv, n).map(Some);
        }
    }
    it.eval(e).map(Some)
}

fn eval_opt(it: &mut Interp, e: &Option<Box<Expr>>) -> R<Option<Value>> {
    match e {
        Some(e) => it.eval(e).map(Some),
        None => Ok(None),
    }
}

/// An expression whose operand is spelled `<x>.version_info` — `.attr`,
/// `[i]`, `[a:b:c]` or a comparison chain — evaluated with the same order,
/// short-circuit and single evaluation as `eval_inner`'s own arm, and with
/// `sys.version_info` answered only where it is grounded. Out of line so the
/// interpreter's hottest function carries none of it.
#[inline(never)]
pub fn parent(it: &mut Interp, e: &Expr) -> R<Value> {
    match e {
        Expr::Attr(b, n) => match eval_vi(it, b)? {
            None if matches!(n.as_ref(), "major" | "minor") => Ok(vi_attr(n)),
            None => Err(vi_refuse("sys.version_info.<attr> past the minor version")),
            Some(bv) => it.get_attr(&bv, n),
        },
        Expr::Index(b, i) => {
            let bv = eval_vi(it, b)?;
            let iv = it.eval(i)?;
            match bv {
                None => vi_index(&iv),
                Some(bv) => it.index(&bv, &iv),
            }
        }
        Expr::Slice { base, lo, hi, step } => {
            let bv = eval_vi(it, base)?;
            let (lo, hi, st) = (eval_opt(it, lo)?, eval_opt(it, hi)?, eval_opt(it, step)?);
            match bv {
                None => vi_slice(lo, hi, st),
                Some(bv) => it.slice(&bv, lo, hi, st),
            }
        }
        Expr::Compare { first, rest } => {
            let mut left = eval_vi(it, first)?;
            for (op, rhs) in rest {
                let right = eval_vi(it, rhs)?;
                let ok = match (&left, &right) {
                    (Some(l), Some(r)) => it.compare(*op, l, r)?,
                    _ if matches!(op, CmpOp::In | CmpOp::NotIn | CmpOp::Is | CmpOp::IsNot) => {
                        return Err(vi_refuse("sys.version_info with in/is"))
                    }
                    (None, Some(r)) => {
                        let v = vi_operand(r)?;
                        it.compare(*op, &v, r)?
                    }
                    (Some(l), None) => {
                        let v = vi_operand(l)?;
                        it.compare(*op, l, &v)?
                    }
                    (None, None) => return Err(vi_refuse("sys.version_info compared with itself")),
                };
                if !ok {
                    return Ok(Value::Bool(false));
                }
                left = right;
            }
            Ok(Value::Bool(true))
        }
        _ => it.eval(e),
    }
}

/// `sys.version_info[i]`, for the two items that are grounded.
fn vi_index(i: &Value) -> R<Value> {
    match i {
        Value::Int(i) if i.small() == Some(0) => Ok(ival(3)),
        Value::Int(i) if i.small() == Some(1) => Ok(ival(crate::err::REF_PY_MINOR as i64)),
        _ => Err(vi_refuse("sys.version_info[i] past the minor version")),
    }
}

/// `.major` / `.minor`.
fn vi_attr(name: &str) -> Value {
    ival(if name == "major" { 3 } else { crate::err::REF_PY_MINOR as i64 })
}

/// `sys.version_info[:n]`, `n <= 2`.
fn vi_slice(lo: Option<Value>, hi: Option<Value>, step: Option<Value>) -> R<Value> {
    let n = match (lo, hi, step) {
        (None, Some(Value::Int(h)), None) => h.small().filter(|h| (0..=2).contains(h)),
        _ => None,
    };
    match n {
        Some(n) => Ok(Value::Tuple(Rc::new(
            [ival(3), ival(crate::err::REF_PY_MINOR as i64)][..n as usize].to_vec(),
        ))),
        None => Err(vi_refuse("sys.version_info[…] past the minor version")),
    }
}

/// What `sys.version_info` compares as against `other`, which must be a tuple
/// of at most two items: `(3, minor, 0)` — the third item is never reached, and
/// stands in for the fact that the real one is LONGER than any such tuple.
fn vi_operand(other: &Value) -> R<Value> {
    match other {
        Value::Tuple(t) if t.len() <= 2 => Ok(Value::Tuple(Rc::new(vec![
            ival(3),
            ival(crate::err::REF_PY_MINOR as i64),
            ival(0),
        ]))),
        _ => Err(vi_refuse("sys.version_info compared with anything but a tuple of at most two items")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The ROUTER's copy of this capability is two rows the core carries —
    /// `route::CAP_ATTRS` and `route::CAP_METHODS` — and a copy is honest only
    /// while something holds it to what this variant actually serves.
    #[test]
    fn the_route_table_names_exactly_what_is_served() {
        let row = crate::route::CAP_METHODS
            .iter()
            .find(|(m, _)| *m == "random")
            .expect("route::CAP_METHODS has no random row");
        let words: Vec<&str> = row.1.split_whitespace().collect();
        assert_eq!(words, METHODS);
        let mut sorted = METHODS.to_vec();
        sorted.sort_unstable();
        assert_eq!(sorted, METHODS, "METHODS must be sorted");
        for (cap, m, any, shaped) in crate::route::CAP_ATTRS {
            assert_eq!(*cap, "cap-random");
            // Served anywhere: a VALUE out of `get_attr`.
            for n in *any {
                assert!(crate::modules::get_attr(&Value::Module(m), n).is_ok(), "{m}.{n}");
            }
            // Served only in a shape `eval.rs` resolves at the parent: never a
            // value, so every other spelling refuses where the core does.
            for n in *shaped {
                assert!(crate::modules::get_attr(&Value::Module(m), n).is_err(), "{m}.{n}");
            }
        }
        assert!(crate::modules::get_attr(&Value::Module("random"), "choices").is_err());
    }

    /// `4 ** ceil(log(3k, 4))` is the smallest power of four above `3k`,
    /// because `3k` is never a power of four — checked against the float
    /// formula CPython evaluates, over a range of `k` wider than any list here.
    #[test]
    fn the_integer_setsize_is_cpythons_float_one() {
        for k in 6u64..200_000 {
            let mut p = 1u64;
            while p <= 3 * k {
                p *= 4;
            }
            let f = ((3 * k) as f64).ln() / 4f64.ln();
            assert_eq!(p, 4u64.pow(f.ceil() as u32), "k={k}");
        }
    }
}
