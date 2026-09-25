//! `itertools` — the whole of the `cap-itertools` capability, compiled into
//! `lypning-l` and into nothing smaller. Every line of this file, and every
//! line that reaches it, is behind `cfg(feature = "cap-itertools")`.
//!
//! Two names are served, `product` and `combinations`: the corpus mined on
//! 2026-09-24 uses `product` in 20 programs, `combinations` in 5, `islice` in
//! 4, `permutations` and `count` once each, and nothing else on the module. The
//! rest of the surface is ABSENT, so `route::MODULE_ATTRS` blocks every other
//! `itertools.<name>` in the CORE's walk and the program never starts here.
//!
//! **The two things a naive port gets wrong, and this one does not.**
//!
//!   * The INPUTS are drained at construction — CPython's `PySequence_Tuple`
//!     over every argument, before the first result. So a generator handed to
//!     `product` is empty afterwards, and a list appended to after the call
//!     changes nothing: `x = [1, 2]; p = product(x, x); x.append(3)` is four
//!     tuples. Draining goes through [`Interp::iter_collect`], which is the
//!     interpreter's own iteration, so a set refuses (`set-order`) exactly as
//!     it does everywhere else.
//!   * The OUTPUT is lazy, an index counter advanced one result per `next`:
//!     `next(product(range(10**6), repeat=3))` is `(0, 0, 0)` at once in CPython,
//!     and an eager version is a memory bomb.
//!
//! **No new `Value` variant.** The object is `Value::IterObj` over
//! [`Iter::Itertools`] — the shape `zip`, `map` and `csv.reader` already have —
//! with CPython's `tp_name` (`itertools.product`) as its kind, so `repr`
//! refuses (heap address), `len` refuses (`iterator-type-name`), a dict key
//! refuses (`iterator-identity`), `==` and `is` are identity, `bool` is True,
//! and `p[0]`, `p + 1` and `p.x` raise CPython's own TypeError/AttributeError
//! naming `'itertools.product'`, all by construction (`docs/HILLCLIMB.md`
//! iterations 74, 76, 77 are what a new variant cost instead).
//!
//! Every error below is CPython 3.14's own text, read off a running
//! interpreter on 2026-09-24 and pinned by `tests/test_itertools_grid.py`.
//! What this does NOT word it refuses: an integer past 64 bits (CPython's
//! `OverflowError` names `C ssize_t`), a keyword `combinations` does not bind,
//! a `product` whose pool list would be absurdly long, `mro()` of either class
//! (`value::attr_error`), an UNCAUGHT NameError or AttributeError in a program
//! that names this module (`Interp::run`: CPython's last line carries a
//! suggestion), and more than 8 MiB of output from such a program
//! (`io::hold`: flushing it would turn any later refusal into an exit 1).
//! `isinstance(x, itertools.product)` is answered: the object's kind IS the
//! class's tp_name (`builtins.rs`, `isinstance`).

use crate::args::Args;
use crate::err::*;
use crate::eval::Interp;
use crate::iter::Iter;
use crate::value::*;
use std::cell::RefCell;
use std::rc::Rc;

/// The served names, sorted. `route::MODULE_ATTRS`' `itertools` row IS this
/// constant, so the core's walk and this file cannot disagree about it.
pub const SERVED: &[&str] = &["combinations", "product"];

/// More pools than this is a refusal rather than an allocation: `product(x,
/// repeat=10**9)` holds a billion references in CPython too, and the answer is
/// a `MemoryError` there that this engine has no business imitating.
const MAX_POOLS: usize = 1 << 16;

/// `combinations(x, r)` with `r > len(x)` is `[]` in principle, but CPython
/// mallocs `r` indices first: `r=2**62` is a MemoryError, `r=2**40` an OOM
/// kill, `r=2**24` a quiet `[]` (3.14.5, 2026-09-24). Below this bound the
/// allocation is 8 MiB and every host answers `[]`; above it this refuses.
const MAX_R: u64 = 1 << 20;

/// The index state of one `product` or `combinations` object — CPython's own
/// `productobject`/`combinationsobject` fields, less the result-tuple reuse.
pub struct Combo {
    /// `product`: one pool per output position (the repeat already expanded,
    /// as CPython does). `combinations`: exactly one pool.
    pools: Vec<Rc<Vec<Value>>>,
    /// The current index into each pool (`product`) or into THE pool
    /// (`combinations`, `r` of them, strictly increasing).
    idx: Vec<usize>,
    combinations: bool,
    started: bool,
    done: bool,
}

pub fn refuse(what: &str) -> LypningError {
    unsupported("itertools", what)
}

/// `itertools.<name>` as a value. The two served names are CLASSES in CPython
/// (`value::bound_kind` says so); every other name refuses with the kind the
/// router blocks on statically.
pub fn module_attr(name: &str) -> R<Value> {
    match SERVED.iter().find(|n| **n == name) {
        Some(n) => Ok(Value::Bound(Rc::new(Value::Module("itertools")), n)),
        None => Err(unsupported("module-attr", &format!("itertools.{name}"))),
    }
}

/// A `Py_ssize_t` argument: `int` or `bool`, CPython's TypeError for anything
/// else, and a refusal past 64 bits, where CPython's `OverflowError` is a
/// message about a C type this engine does not have.
fn ssize(v: &Value) -> R<i64> {
    if let Value::Int(i) = v {
        if i.small().is_none() {
            return Err(refuse("an integer argument past 64 bits"));
        }
    }
    crate::eval::int_val(v)
}

fn object(c: Combo) -> Value {
    let kind = if c.combinations { "itertools.combinations" } else { "itertools.product" };
    Value::IterObj(Rc::new(RefCell::new(Iter::Itertools(Box::new(c)))), kind)
}

/// `itertools.product(*iterables, repeat=1)` and
/// `itertools.combinations(iterable, r)`, in CPython's order of checks.
pub fn call(it: &mut Interp, name: &str, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    match name {
        "product" => {
            // `product_new`: the keywords first — one at most, and only
            // `repeat` — then the repeat's type and sign, and only then the
            // iterables, left to right.
            let mut repeat = 1;
            if kw.len() > 1 {
                return Err(type_err(format!(
                    "product() takes at most 1 keyword argument ({} given)",
                    kw.len()
                )));
            }
            if let Some((k, v)) = kw.first() {
                if k.as_ref() != "repeat" {
                    // 3.12 words this `'foo' is an invalid keyword argument
                    // for product()`; 3.13 and 3.14 as below (measured on
                    // 2026-09-24). An older reference refuses instead.
                    if REF_PY_MINOR < 13 {
                        return Err(refuse(&format!("product({k}=…)")));
                    }
                    return Err(type_err(format!(
                        "product() got an unexpected keyword argument '{k}'"
                    )));
                }
                repeat = ssize(v)?;
                if repeat < 0 {
                    return Err(value_err("repeat argument cannot be negative"));
                }
            }
            let n = (args.len() as u64).saturating_mul(repeat as u64);
            if n > MAX_POOLS as u64 {
                return Err(refuse("product() over more than 65,536 pools"));
            }
            // `repeat=0` is no pools at all: `product_new` sets `nargs = 0`
            // and never touches an argument, so a non-iterable is not an
            // error and a generator is not consumed.
            let nargs = if repeat == 0 { 0 } else { args.len() };
            let mut drained = Vec::with_capacity(nargs);
            for i in 0..nargs {
                let v = args.take(i);
                drained.push(Rc::new(it.iter_collect(v)?));
            }
            // No iterables is no pools whatever `repeat` says — `[()]` in
            // CPython even at `repeat=2**63-1` — so the loop is skipped
            // rather than spun `repeat` times over nothing.
            let mut pools = Vec::with_capacity(n as usize);
            if !drained.is_empty() {
                for _ in 0..repeat {
                    pools.extend(drained.iter().cloned());
                }
            }
            let idx = vec![0; pools.len()];
            Ok(object(Combo { pools, idx, combinations: false, started: false, done: false }))
        }
        "combinations" => {
            // Argument Clinic: the count first, then each parameter by name
            // or position, and `r` converted BEFORE the iterable is drained;
            // the sign of `r` is checked AFTER (`itertools_combinations_impl`).
            let total = args.len() + kw.len();
            if total > 2 {
                // Clinic says "keyword arguments" when NO argument was
                // positional, "arguments" otherwise (3.14.5, measured).
                let what = if args.len() == 0 { "keyword arguments" } else { "arguments" };
                return Err(type_err(format!(
                    "combinations() takes at most 2 {what} ({total} given)"
                )));
            }
            let mut iterable = args.first().cloned();
            let mut r = args.get(1).cloned();
            for (k, v) in kw {
                let slot = match k.as_ref() {
                    "iterable" => &mut iterable,
                    "r" => &mut r,
                    _ => return Err(refuse(&format!("combinations({k}=…)"))),
                };
                if slot.is_some() {
                    return Err(refuse(&format!("combinations() given {k} twice")));
                }
                *slot = Some(v.clone());
            }
            let Some(iterable) = iterable else {
                return Err(type_err("combinations() missing required argument 'iterable' (pos 1)"));
            };
            let Some(r) = r else {
                return Err(type_err("combinations() missing required argument 'r' (pos 2)"));
            };
            let r = ssize(&r)?;
            let pool = it.iter_collect(iterable)?;
            if r < 0 {
                return Err(value_err("r must be non-negative"));
            }
            let r = r as u64;
            // CPython allocates an `r`-sized index array BEFORE it compares
            // `r` with `n`, so a huge `r > n` is a MemoryError there (or a
            // lazily-committed `[]`, depending on the host's memory). Past
            // MAX_R that answer is the host's, not the language's: refuse.
            if r > pool.len() as u64 && r > MAX_R {
                return Err(refuse("combinations() with r past 2**20 and above len(iterable)"));
            }
            // `r > n` yields nothing, and is decided here so no index vector
            // of a size the program chose is ever allocated.
            let done = r > pool.len() as u64;
            let idx = if done { Vec::new() } else { (0..r as usize).collect() };
            Ok(object(Combo {
                pools: vec![Rc::new(pool)],
                idx,
                combinations: true,
                started: false,
                done,
            }))
        }
        _ => Err(unsupported("module-attr", &format!("itertools.{name}"))),
    }
}

/// One result, or `None` once exhausted — and exhausted STAYS exhausted.
pub fn next(c: &mut Combo) -> Option<Value> {
    if c.done {
        return None;
    }
    if !c.started {
        c.started = true;
        // `product` with any empty pool yields nothing; with NO pools it
        // yields one empty tuple, which the tuple below already is.
        if !c.combinations && c.pools.iter().any(|p| p.is_empty()) {
            c.done = true;
            return None;
        }
    } else if c.combinations {
        // The rightmost index not yet at its ceiling `i + n - r`, bumped, and
        // every index to its right reset to follow it.
        let n = c.pools[0].len();
        let r = c.idx.len();
        let Some(i) = (0..r).rev().find(|&i| c.idx[i] != i + n - r) else {
            c.done = true;
            return None;
        };
        c.idx[i] += 1;
        for j in i + 1..r {
            c.idx[j] = c.idx[j - 1] + 1;
        }
    } else {
        // An odometer, rightmost wheel fastest.
        let mut i = c.idx.len();
        loop {
            if i == 0 {
                c.done = true;
                return None;
            }
            i -= 1;
            c.idx[i] += 1;
            if c.idx[i] < c.pools[i].len() {
                break;
            }
            c.idx[i] = 0;
        }
    }
    let out: Vec<Value> = if c.combinations {
        c.idx.iter().map(|&i| c.pools[0][i].clone()).collect()
    } else {
        c.idx.iter().zip(&c.pools).map(|(&i, p)| p[i].clone()).collect()
    };
    Some(Value::Tuple(Rc::new(out)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_route_table_names_exactly_what_is_served() {
        let row = crate::route::MODULE_ATTRS
            .iter()
            .find(|(m, _)| *m == "itertools")
            .expect("route::MODULE_ATTRS has no itertools row");
        assert_eq!(row.1, SERVED);
        let mut sorted = SERVED.to_vec();
        sorted.sort_unstable();
        assert_eq!(sorted, SERVED, "SERVED must be sorted");
        for n in SERVED {
            assert!(module_attr(n).is_ok(), "{n}");
        }
        for n in ["chain", "islice", "permutations", "count", "groupby", "tee"] {
            assert!(module_attr(n).is_err(), "{n}");
        }
    }

    fn ints(xs: &[i64]) -> Rc<Vec<Value>> {
        Rc::new(xs.iter().map(|&x| ival(x)).collect())
    }

    fn drain(mut c: Combo) -> usize {
        let mut n = 0;
        while next(&mut c).is_some() {
            n += 1;
        }
        assert!(next(&mut c).is_none(), "an exhausted object must stay exhausted");
        n
    }

    #[test]
    fn the_counts_are_the_binomials_and_the_products() {
        for n in 0..7usize {
            for r in 0..9usize {
                let pool: Vec<i64> = (0..n as i64).collect();
                let c = Combo {
                    pools: vec![ints(&pool)],
                    idx: if r > n { Vec::new() } else { (0..r).collect() },
                    combinations: true,
                    started: false,
                    done: r > n,
                };
                let want = if r > n {
                    0
                } else {
                    (0..r).fold(1usize, |acc, i| acc * (n - i) / (i + 1))
                };
                assert_eq!(drain(c), want, "C({n},{r})");
            }
        }
        for sizes in [vec![], vec![0], vec![3], vec![2, 3], vec![3, 0, 2], vec![1, 1, 4]] {
            let pools: Vec<_> = sizes.iter().map(|&k| ints(&(0..k).collect::<Vec<_>>())).collect();
            let c = Combo {
                idx: vec![0; pools.len()],
                pools,
                combinations: false,
                started: false,
                done: false,
            };
            let want: i64 = sizes.iter().product();
            assert_eq!(drain(c) as i64, want, "{sizes:?}");
        }
    }
}
