//! `statistics` — `mean`, `median`, `median_low`, `median_high`: the
//! `cap-statistics` capability, compiled into `lypning-l` and into nothing
//! smaller. Every line of this file, and every line that reaches it, is behind
//! `cfg(feature = "cap-statistics")`.
//!
//! **No arithmetic of its own.** CPython's three medians are `sorted(data)`, an
//! index, and — for an even-length `median` only — `(data[i - 1] + data[i]) / 2`
//! with the ordinary operators. So this file calls the engine's own sort
//! (`ops::sort_values`, which refuses a NaN as `nan-order` because the result
//! would be timsort's and not Python's) and its own `+` and `/`
//! (`Interp::binop`): the result TYPE, float overflow to `inf`, the `str / int`
//! TypeError and the refusal of an inexact wide-int division all come from the
//! code every other program already runs, rather than from a second copy of it.
//!
//! **`mean` is served over `int` and `bool` only.** CPython sums through
//! `Fraction`, exactly, and converts once: with only ints the result is an
//! `int` when the total divides by `n` (`mean([1, 2, 3])` is `2`, not `2.0`) and
//! otherwise `float(Fraction(total, n))`, which is the correctly rounded
//! quotient — the same number `int / int` is, and the engine's `int / int` is
//! correctly rounded (or refuses past 64 bits, `cap-bigint`). A FLOAT element
//! refuses: `mean([0.1, 0.2, 0.3])` is `0.2` in CPython, a float sum gives
//! `0.20000000000000004`, and `fsum(xs) / n` rounds twice and was measured by
//! the scout to disagree on 17,526 of 100,000 random lists. Serving floats
//! needs a correctly rounded quotient of a ~2,100-bit rational, and no corpus
//! program (mined 2026-09-24) calls `mean` on one.
//!
//! **What refuses, and where.** Every name outside [`SERVED`] — `stdev`,
//! `pstdev`, `fmean`, `mode`, `StatisticsError`, … — is a `module-attr` block
//! in the CORE's walk, out of `route::MODULE_ATTRS`, which a test below holds
//! to this list. At runtime (kind `statistics`): empty data, which CPython
//! answers with a `StatisticsError` this engine has no class for; a float or
//! non-numeric element in `mean`; keywords and a wrong argument count, whose
//! TypeError wording is CPython's.

use crate::args::Args;
use crate::ast::BinOp;
use crate::err::{unsupported, LypningError, R};
use crate::value::{ival, Value};
use std::rc::Rc;

/// The names this module serves. Sorted, because the route table is and a test
/// compares them as written.
pub const SERVED: &[&str] = &["mean", "median", "median_high", "median_low"];

fn refuse(what: &str) -> LypningError {
    unsupported("statistics", what)
}

/// `statistics.<name>` as a value. A served name is a bound module method;
/// every other name refuses with the kind the router blocks on statically.
pub fn module_attr(name: &str) -> R<Value> {
    match SERVED.iter().find(|n| **n == name) {
        Some(n) => Ok(Value::Bound(Rc::new(Value::Module("statistics")), n)),
        None => Err(unsupported("module-attr", &format!("statistics.{name}"))),
    }
}

pub fn call(
    it: &mut crate::eval::Interp,
    name: &str,
    args: &mut Args,
    kw: &[(Rc<str>, Value)],
) -> R<Value> {
    if !kw.is_empty() || args.len() != 1 {
        // CPython: `mean() got an unexpected keyword argument …` / `missing 1
        // required positional argument` / `takes 1 positional argument but 2
        // were given`. The wording is CPython's to write.
        return Err(refuse(&format!("statistics.{name}() with other than one positional argument")));
    }
    let data = args.take(0);
    // A set is fine here: every served function either sorts or sums exact
    // integers, so the engine's own iteration order never shows.
    let mut items = it.collect_unordered(data)?;
    if name == "mean" {
        return mean(it, &items);
    }
    let mut keys = items.clone();
    crate::ops::sort_values(&mut items, &mut keys, false)?;
    let n = items.len();
    if n == 0 {
        return Err(refuse("no median for empty data (a StatisticsError)"));
    }
    Ok(match name {
        "median" if n % 2 == 0 => {
            let s = it.binop(BinOp::Add, &items[n / 2 - 1], &items[n / 2])?;
            it.binop(BinOp::Div, &s, &ival(2))?
        }
        "median_low" if n % 2 == 0 => items.swap_remove(n / 2 - 1),
        _ => items.swap_remove(n / 2),
    })
}

/// The exact integer mean: `total // n` when it divides, else the engine's
/// correctly rounded `total / n`.
fn mean(it: &mut crate::eval::Interp, items: &[Value]) -> R<Value> {
    let mut total = ival(0);
    for x in items {
        match x {
            Value::Int(_) | Value::Bool(_) => total = it.binop(BinOp::Add, &total, x)?,
            Value::Float(_) => {
                return Err(refuse(
                    "mean() over a float, which CPython sums as an exact fraction and rounds once",
                ))
            }
            other => {
                return Err(refuse(&format!(
                    "mean() over a '{}' (CPython raises TypeError)",
                    crate::value::type_name(other)
                )))
            }
        }
    }
    if items.is_empty() {
        return Err(refuse("mean requires at least one data point (a StatisticsError)"));
    }
    let n = ival(items.len() as i64);
    let rem = it.binop(BinOp::Mod, &total, &n)?;
    let exact = matches!(&rem, Value::Int(i) if i.small() == Some(0));
    it.binop(if exact { BinOp::FloorDiv } else { BinOp::Div }, &total, &n)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_route_table_names_exactly_what_is_served() {
        let row = crate::route::MODULE_ATTRS
            .iter()
            .find(|(m, _)| *m == "statistics")
            .expect("route::MODULE_ATTRS has no statistics row");
        assert_eq!(row.1, SERVED);
        let mut sorted = SERVED.to_vec();
        sorted.sort_unstable();
        assert_eq!(sorted, SERVED, "SERVED must be sorted");
        for name in SERVED {
            assert!(module_attr(name).is_ok());
        }
        for name in ["stdev", "pstdev", "fmean", "mode", "StatisticsError", "variance"] {
            assert!(module_attr(name).is_err(), "statistics.{name} must refuse");
        }
    }
}
