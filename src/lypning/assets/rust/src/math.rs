//! `math` — the functions whose answer is EXACTLY defined, and nothing else.
//!
//! The bound is the whole design. A `math` that is *mostly* right is a MISMATCH
//! generator, because every function here returns a plausible number at exit 0
//! and nothing in the output says which library computed it. So the line this
//! file draws is not "what is useful" but **"what has one answer"**:
//!
//! - **IEEE-754 operations** are bit-exact on every conforming platform, and
//!   `sqrt`, `fmod` (Rust's `%` on `f64` *is* C's `fmod`), `copysign` and
//!   `fabs` are all of them. `sqrt` is the only root IEEE-754 requires to be
//!   correctly rounded, and CPython computes it with the same instruction.
//! - **Integer arithmetic** is exact by construction: `isqrt`, `gcd`,
//!   `factorial`, and `floor`/`ceil`/`trunc`, which return an **int** in Python
//!   3 and not a float.
//! - **The transcendentals are NOT here and must not be.** `sin`, `cos`, `tan`,
//!   `exp`, `log`, `log2`, `log10`, `atan2`, `pow` and `hypot` are libm's
//!   answers; libm is not correctly rounded, and musl's last ulp is not glibc's
//!   or Apple's. `math.log10(1000)` happens to agree everywhere and
//!   `math.log10(0.001)` is a coin toss, and nothing in the output tells the two
//!   apart. They refuse at [`module_attr`], which is a STATIC block — the walk
//!   resolves `math.sin` through `modules::get_attr` before the program starts —
//!   so not having them costs a route, not a spawn.
//!
//! `math.fsum` is absent for a different reason and is the obvious next step:
//! it IS exactly specified (Shewchuk's exact summation, correctly rounded), it
//! is a name the corpus reaches for, and it is a second mechanism with its own
//! overflow and `inf`/`nan` domain edges.
//!
//! # Every error path refuses instead of raising
//!
//! `math.sqrt(-1)` is `ValueError: math domain error`, `math.isqrt(-1)` is
//! `ValueError: isqrt() argument must be nonnegative`, `math.gcd(1.0)` is
//! `TypeError: 'float' object cannot be interpreted as an integer`, and a wrong
//! argument count names the function and both counts. That text is CPython's to
//! print and has been re-worded between versions, so each of them is a refusal
//! carrying the kind `math` — `random.rs`'s rule, for `random.rs`'s reason. The
//! kind is in [`crate::route::ONLY_CPYTHON_KINDS`]: every variant carries this
//! file, so falling to a larger sibling would spend a spawn to be told no twice.
//!
//! The three errors NOT refused are the three [`float_to_int`] already spells
//! exactly as CPython does — `math.floor(nan)` is a `ValueError`,
//! `math.floor(inf)` an `OverflowError`, and `math.floor(1e300)` the `bigint`
//! refusal, because CPython answers that one with a bignum. `factorial` past
//! 20! and the one `gcd` whose answer is 2**63 raise that same `bigint` kind
//! rather than wrap.

use std::rc::Rc;

use crate::args::Args;
use crate::builtins::float_to_int;
use crate::err::{unsupported, LypningError, R};
use crate::eval::Interp;
use crate::value::{ival, type_name, Value};

/// The callable names. Sorted, and the ONE list: [`module_attr`] answers from it
/// and `modules::get_attr` hands every `math` attribute straight here, so there
/// is no second table to drift. Every other name on the module refuses with
/// `module-attr`, which `route.rs` blocks on statically out of its walk.
pub const SERVED: &[&str] = &[
    "ceil", "copysign", "fabs", "factorial", "floor", "fmod", "gcd", "isfinite", "isinf", "isnan",
    "isqrt", "sqrt", "trunc",
];

/// The constants, as the `f64` CPython's are: each is the nearest double to the
/// real number and every library agrees on it. `math.nan` is a positive quiet
/// NaN, whose `repr` is `nan` either way.
const CONSTS: &[(&str, f64)] = &[
    ("e", std::f64::consts::E),
    ("inf", f64::INFINITY),
    ("nan", f64::NAN),
    ("pi", std::f64::consts::PI),
    ("tau", std::f64::consts::TAU),
];

fn refuse(what: &str) -> LypningError {
    unsupported("math", what)
}

/// A result Python would hold in a bignum — the kind `bigint.rs` raises, so the
/// chain hands the program to an interpreter that has one.
fn wide(what: &str) -> LypningError {
    unsupported("bigint", what)
}

/// `math.<name>` as a value: a constant, a bound module method, or the refusal
/// the router sees before the program starts.
pub fn module_attr(name: &str) -> R<Value> {
    if let Some((_, v)) = CONSTS.iter().find(|(n, _)| *n == name) {
        return Ok(Value::Float(*v));
    }
    match SERVED.iter().find(|n| **n == name) {
        Some(n) => Ok(Value::Bound(Rc::new(Value::Module("math")), n)),
        None => Err(unsupported("module-attr", &format!("math.{name}"))),
    }
}

/// The argument as a `float`, as CPython's `PyFloat_AsDouble` takes it: a float
/// unchanged, an int rounded to nearest with ties to even (`as f64` and
/// `PyLong_AsDouble` agree), a bool as 1 or 0. Anything else is a `TypeError`
/// whose wording is CPython's, so it refuses.
fn num(name: &str, v: &Value) -> R<f64> {
    match v {
        Value::Float(f) => Ok(*f),
        Value::Int(i) => Ok(i.get()? as f64),
        Value::Bool(b) => Ok(*b as i64 as f64),
        other => Err(refuse(&format!("math.{name}() of a {}", type_name(other)))),
    }
}

/// The argument as an `int`. `isqrt`, `gcd` and `factorial` take no float at all
/// in CPython — `math.isqrt(4.0)` is a `TypeError` — so this is not [`num`] with
/// a cast on the end.
fn int(name: &str, v: &Value) -> R<i64> {
    match v {
        Value::Int(i) => i.get(),
        Value::Bool(b) => Ok(*b as i64),
        other => Err(refuse(&format!("math.{name}() of a {}", type_name(other)))),
    }
}

/// `floor(sqrt(n))`, exactly. The `f64` seed is within one of the answer for
/// every `n` an `i64` holds — the rounding is at most half an ulp and the root
/// is under 2**32 — and both corrections are checked rather than assumed.
/// `(r + 1) * (r + 1)` is at most 9.22e18 here and fits a `u64` with room over.
fn isqrt_u64(n: u64) -> u64 {
    let mut r = (n as f64).sqrt() as u64;
    while r > 0 && r * r > n {
        r -= 1;
    }
    while (r + 1) * (r + 1) <= n {
        r += 1;
    }
    r
}

fn gcd_u64(mut a: u64, mut b: u64) -> u64 {
    while b != 0 {
        let t = a % b;
        a = b;
        b = t;
    }
    a
}

pub fn call(_it: &mut Interp, name: &str, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    if !kw.is_empty() {
        // CPython: "math.floor() takes no keyword arguments".
        return Err(refuse(&format!("math.{name}() with keyword arguments")));
    }
    let arity = |lo: usize, hi: usize| -> R<()> {
        if args.len() < lo || args.len() > hi {
            // CPython names the function and both counts, and the text is
            // version-shaped, so this refuses rather than guess it.
            return Err(refuse(&format!("math.{name}() with {} argument(s)", args.len())));
        }
        Ok(())
    };
    Ok(match name {
        // An int comes back UNCHANGED — `int.__floor__` is identity, so a wide
        // one stays wide and never touches a double — and a bool comes back as
        // the int `1` or `0`, not as `True`. The float path is CPython's
        // `PyLong_FromDouble`, which is `float_to_int`: a NaN is a ValueError,
        // an infinity an OverflowError, anything past 64 bits the `bigint`
        // refusal, and all three messages are already CPython's own.
        "floor" | "ceil" | "trunc" => {
            arity(1, 1)?;
            match &args[0] {
                Value::Int(_) => args[0].clone(),
                Value::Bool(b) => ival(*b as i64),
                v => {
                    let x = num(name, v)?;
                    let r = match name {
                        "floor" => x.floor(),
                        "ceil" => x.ceil(),
                        _ => x.trunc(),
                    };
                    ival(float_to_int(r, name)?)
                }
            }
        }
        "fabs" => {
            arity(1, 1)?;
            // `abs` clears the sign bit: `fabs(-0.0)` is `0.0` and `fabs(nan)`
            // is `nan`, both as CPython.
            Value::Float(num(name, &args[0])?.abs())
        }
        "sqrt" => {
            arity(1, 1)?;
            let x = num(name, &args[0])?;
            // `-0.0 < 0.0` is false, so `sqrt(-0.0)` is `-0.0` here as it is
            // there; a NaN compares false too and answers NaN. Everything
            // genuinely negative, `-inf` included, is CPython's
            // `ValueError: math domain error`, whose text is CPython's to print.
            if x < 0.0 {
                return Err(refuse("math.sqrt() of a negative number"));
            }
            Value::Float(x.sqrt())
        }
        "copysign" => {
            arity(2, 2)?;
            Value::Float(num(name, &args[0])?.copysign(num(name, &args[1])?))
        }
        "fmod" => {
            arity(2, 2)?;
            let (x, y) = (num(name, &args[0])?, num(name, &args[1])?);
            let r = x % y;
            // `mathmodule.c`: an infinite `x` is a domain error unless `y` is a
            // NaN, and a NaN result from two non-NaN operands is one too — which
            // is the `y == 0` case. Everything else is libm's `fmod`, which is
            // exact, and which Rust's `%` on `f64` is.
            if (x.is_infinite() && !y.is_nan()) || (r.is_nan() && !x.is_nan() && !y.is_nan()) {
                return Err(refuse("math.fmod() outside its domain"));
            }
            Value::Float(r)
        }
        "isnan" | "isinf" | "isfinite" => {
            arity(1, 1)?;
            let x = num(name, &args[0])?;
            Value::Bool(match name {
                "isnan" => x.is_nan(),
                "isinf" => x.is_infinite(),
                _ => x.is_finite(),
            })
        }
        "isqrt" => {
            arity(1, 1)?;
            let n = int(name, &args[0])?;
            if n < 0 {
                return Err(refuse("math.isqrt() of a negative number"));
            }
            ival(isqrt_u64(n as u64) as i64)
        }
        "gcd" => {
            // Any number of arguments in modern Python: none is 0, one is its
            // own absolute value.
            let mut g: u64 = 0;
            for i in 0..args.len() {
                g = gcd_u64(g, int(name, &args[i])?.unsigned_abs());
            }
            // The one result an i64 does not hold: `gcd(-2**63)` is 2**63.
            if g > i64::MAX as u64 {
                return Err(wide("math.gcd() of -2**63 (Python would use a bignum)"));
            }
            ival(g as i64)
        }
        "factorial" => {
            arity(1, 1)?;
            let n = int(name, &args[0])?;
            if n < 0 {
                return Err(refuse("math.factorial() of a negative number"));
            }
            let mut acc: i64 = 1;
            // 20! is the last one an i64 holds, so this leaves at k == 21 rather
            // than running to `n`.
            for k in 2..=n {
                acc = acc
                    .checked_mul(k)
                    .ok_or_else(|| wide("math.factorial() past 20! (Python would use a bignum)"))?;
            }
            ival(acc)
        }
        _ => return Err(unsupported("module-attr", &format!("math.{name}"))),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn isqrt_is_exact_at_every_square_and_its_neighbours() {
        for k in [0u64, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 46_340, 46_341, 3_037_000_499] {
            let sq = k * k;
            assert_eq!(isqrt_u64(sq), k, "isqrt({sq})");
            if k > 0 {
                assert_eq!(isqrt_u64(sq - 1), k - 1, "isqrt({})", sq - 1);
                // Only for k >= 1: 1 is itself a square, so `isqrt(0 + 1)` is 1
                // and not 0 — which is CPython's answer too.
                assert_eq!(isqrt_u64(sq + 1), k, "isqrt({})", sq + 1);
            }
        }
        assert_eq!(isqrt_u64(i64::MAX as u64), 3_037_000_499);
    }

    /// Both lists `modules::get_attr` reaches through are this file's, so the
    /// only things that can go stale are their order and their overlap.
    #[test]
    fn the_served_names_are_sorted_and_disjoint_from_the_constants() {
        assert!(SERVED.windows(2).all(|w| w[0] < w[1]), "SERVED is unsorted");
        assert!(CONSTS.windows(2).all(|w| w[0].0 < w[1].0), "CONSTS is unsorted");
        for (n, _) in CONSTS {
            assert!(!SERVED.contains(n), "{n} is both a constant and a function");
        }
    }

    /// Named so that adding one is a deliberate act: every one of these is
    /// libm's answer, and libm's last ulp is the platform's.
    #[test]
    fn no_transcendental_is_served() {
        let libm = ["sin", "cos", "tan", "atan", "atan2", "exp", "log", "log2", "log10", "pow"];
        for n in libm.iter().chain(["hypot", "fsum", "dist", "expm1", "log1p"].iter()) {
            assert!(module_attr(n).is_err(), "math.{n} must refuse: libm is not correctly rounded");
        }
    }
}
