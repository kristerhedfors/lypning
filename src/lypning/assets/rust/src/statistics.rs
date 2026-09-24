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
//! TypeError wording is CPython's; and a median over items `<` does not
//! totally order (mixed kinds, a NaN at any depth, a set, a wide int next to a
//! float — [`Shape`]), where the engine's sort would not give timsort's answer.

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
    if name == "mean" {
        return mean(it, data);
    }
    // A set is fine here: the medians sort, and a total order with a stable
    // sort makes the input order invisible — which is what `comparable` below
    // guarantees before the sort runs.
    let mut items = it.collect_unordered(data)?;
    let n = items.len();
    if n == 0 {
        return Err(refuse("no median for empty data (a StatisticsError)"));
    }
    // One element is returned without a comparison, in CPython and here.
    if n > 1 {
        let mut shape = Shape::Unset;
        if !items.iter().all(|x| shape.admit(x, 0)) || !shape.exact() {
            return Err(refuse(&format!(
                "statistics.{name}() over items the engine's sort cannot order as timsort does"
            )));
        }
    }
    let mut keys = items.clone();
    crate::ops::sort_values(&mut items, &mut keys, false)?;
    Ok(match name {
        "median" if n % 2 == 0 => {
            let s = it.binop(BinOp::Add, &items[n / 2 - 1], &items[n / 2])?;
            it.binop(BinOp::Div, &s, &ival(2))?
        }
        "median_low" if n % 2 == 0 => items.swap_remove(n / 2 - 1),
        _ => items.swap_remove(n / 2),
    })
}

/// What every item a median sorts must share, position by position, for the
/// engine's merge sort to give timsort's permutation and raise nothing.
///
/// The sort is only interchangeable with CPython's when `<` is a TOTAL order on
/// the items: then a stable sort has one answer, whatever order it asks its
/// comparisons in and whatever order a set iterates in. Anything else shows —
/// a TypeError between two kinds names the first pair asked, and the engine
/// asks in a different order from timsort; a NaN (at any depth) makes `<` no
/// order at all; a set is ordered by subset; and an int past 2**53 next to a
/// float is compared through `f64` by the core, so `2**53 + 1 == 2.0**53`
/// there. So the items must be all numbers, all `str` or all `bytes`, or all
/// tuples (or all lists) whose elements obey the same rule at each index; a
/// number column that holds a float holds no int outside ±2**53. Past a
/// nesting depth of 32 (a list that contains itself) the answer is no.
enum Shape {
    Unset,
    Num { float: bool, wide: bool },
    Str,
    Bytes,
    Seq { list: bool, at: Vec<Shape> },
}

impl Shape {
    fn admit(&mut self, v: &Value, depth: u32) -> bool {
        if depth > 32 {
            return false;
        }
        const EXACT: u64 = 1 << 53;
        let (float, wide) = match v {
            Value::Bool(_) => (false, false),
            Value::Int(i) => (false, !matches!(i.small(), Some(x) if x.unsigned_abs() <= EXACT)),
            Value::Float(f) if !f.is_nan() => (true, false),
            Value::Str(_) => return self.is(Shape::Str),
            Value::Bytes(_) => return self.is(Shape::Bytes),
            Value::Tuple(t) => return self.seq(false, &t[..], depth),
            Value::List(l) => return self.seq(true, &l.borrow()[..], depth),
            _ => return false,
        };
        match self {
            Shape::Unset => *self = Shape::Num { float, wide },
            Shape::Num { float: f, wide: w } => {
                *f |= float;
                *w |= wide;
            }
            _ => return false,
        }
        true
    }

    fn is(&mut self, want: Shape) -> bool {
        match (&*self, &want) {
            (Shape::Unset, _) => {
                *self = want;
                true
            }
            (Shape::Str, Shape::Str) | (Shape::Bytes, Shape::Bytes) => true,
            _ => false,
        }
    }

    fn seq(&mut self, list: bool, xs: &[Value], depth: u32) -> bool {
        if let Shape::Unset = self {
            *self = Shape::Seq { list, at: Vec::new() };
        }
        let Shape::Seq { list: l, at } = self else { return false };
        if *l != list {
            return false;
        }
        for (i, x) in xs.iter().enumerate() {
            if i == at.len() {
                at.push(Shape::Unset);
            }
            if !at[i].admit(x, depth + 1) {
                return false;
            }
        }
        true
    }

    /// No number column mixes a float with an int the core compares inexactly.
    fn exact(&self) -> bool {
        match self {
            Shape::Num { float, wide } => !(*float && *wide),
            Shape::Seq { at, .. } => at.iter().all(Shape::exact),
            _ => true,
        }
    }
}

/// The exact integer mean: `total // n` when it divides, else the engine's
/// correctly rounded `total / n`. It STREAMS, as CPython's does: `mean` over a
/// generator of 10**10 items holds one running total, never 10**10 values. A
/// `range` needs no loop at all — its mean is `(first + last) / 2` exactly,
/// the same rational CPython's `Fraction(total, n)` reduces to.
fn mean(it: &mut crate::eval::Interp, data: Value) -> R<Value> {
    if let Value::Range(a, b, st) = data {
        let n = if st > 0 {
            (b as i128 - a as i128 + st as i128 - 1) / st as i128
        } else {
            (a as i128 - b as i128 - st as i128 - 1) / -(st as i128)
        };
        if n <= 0 {
            return Err(refuse("mean requires at least one data point (a StatisticsError)"));
        }
        let last = a as i128 + (n - 1) * st as i128;
        return exact_div(it, &ival(a), &ival(last as i64), 2);
    }
    let mut it_ = match data {
        // A set's order never shows in an exact integer sum.
        Value::Set(_) => {
            let items = it.collect_unordered(data)?;
            crate::iter::Iter::Tuple(Rc::new(items), 0)
        }
        other => it.make_iter(other)?,
    };
    let mut total = ival(0);
    let mut n: i64 = 0;
    while let Some(x) = it.iter_next(&mut it_)? {
        match &x {
            Value::Int(_) | Value::Bool(_) => total = it.binop(BinOp::Add, &total, &x)?,
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
        n += 1;
    }
    if n == 0 {
        return Err(refuse("mean requires at least one data point (a StatisticsError)"));
    }
    exact_div(it, &total, &ival(0), n)
}

/// `(x + y) / n` as CPython's `mean` converts it: an int when it divides.
fn exact_div(it: &mut crate::eval::Interp, x: &Value, y: &Value, n: i64) -> R<Value> {
    let total = it.binop(BinOp::Add, x, y)?;
    let n = ival(n);
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
