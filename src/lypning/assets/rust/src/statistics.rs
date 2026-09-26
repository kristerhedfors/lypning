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
//! **`mean` is exact, as CPython's is.** CPython sums through `Fraction` and
//! converts once. With only ints the result is an `int` when the total divides
//! by `n` (`mean([1, 2, 3])` is `2`, not `2.0`) and otherwise the correctly
//! rounded `int / int` the engine already has. With a float anywhere it is
//! `float(Fraction(total, n))`: `mean([0.1, 0.2, 0.3])` is `0.2` where a float
//! sum gives `0.20000000000000004` and `fsum(xs) / n` rounds twice (the scout
//! measured 17,526 disagreements in 100,000 random lists). So the floats go
//! into [`Fix`], an exact fixed-point sum in units of `2**-1074`, which is
//! divided by `n` and rounded ONCE, half to even. A float mean used to refuse
//! here, and a refusal at runtime is not free: past `io::COMMIT_THRESHOLD` of
//! output it cannot be routed onward and the run fails at exit 1 where CPython
//! answers. A non-numeric element raises `_exact_ratio`'s own TypeError.
//!
//! **What refuses, and where.** Every name outside [`SERVED`] — `stdev`,
//! `pstdev`, `fmean`, `mode`, `StatisticsError`, … — is a `module-attr` block
//! in the CORE's walk, out of `route::MODULE_ATTRS`, which a test below holds
//! to this list. At runtime (kind `statistics`): empty data, which CPython
//! answers with a `StatisticsError` this engine has no class for; a set
//! holding a non-numeric element in `mean` (its first one is set order's), a
//! `RegexFlag` (an int subclass `_convert` rebuilds), a float next to an int
//! past 64 bits; keywords and a wrong argument count, whose
//! TypeError wording is CPython's; and a median over items `<` does not
//! totally order (mixed kinds, a NaN at any depth, a set, a wide int next to a
//! float — [`Shape`]), where the engine's sort would not give timsort's answer
//! — unless the first pair timsort compares already has no order, whose
//! TypeError is raised exactly.

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
    let set = matches!(data, Value::Set(_));
    let mut items = it.collect_unordered(data)?;
    let n = items.len();
    if n == 0 {
        return Err(refuse("no median for empty data (a StatisticsError)"));
    }
    // One element is returned without a comparison, in CPython and here.
    if n > 1 {
        // Timsort's FIRST comparison is always `data[1] < data[0]` (measured on
        // 3.14.5 at n = 2 … 200), so when that pair has no order at all the
        // TypeError is known exactly — and is CPython's answer, where a
        // refusal after a flush would be an exit 1 of our own. A set's order
        // is not CPython's, so it falls through to `Shape`, which refuses.
        let kind = |v: &Value| match v {
            Value::Bool(_) | Value::Int(_) | Value::Float(_) => Some(1),
            Value::Str(_) => Some(2),
            Value::Bytes(_) => Some(3),
            Value::Tuple(_) => Some(4),
            Value::List(_) => Some(5),
            Value::None => Some(6),
            _ => None,
        };
        if let (false, Some(a), Some(b)) = (set, kind(&items[1]), kind(&items[0])) {
            if a != b || a == 6 {
                return Err(crate::err::type_err(format!(
                    "'<' not supported between instances of '{}' and '{}'",
                    crate::value::type_name(&items[1]),
                    crate::value::type_name(&items[0])
                )));
            }
        }
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

/// The exact mean. Ints and bools add into `total` through the engine's own
/// `+` (so a wide total is a bigint, as in CPython); floats add into [`Fix`],
/// an exact fixed-point sum, and a non-finite float into `inf` — CPython's
/// `partials[None]`, a plain float sum that drops every finite term. It
/// STREAMS, as CPython's `_sum` does: `mean` over a generator of 10**10 items
/// holds one running total, never 10**10 values. A `range` needs no loop at
/// all — its mean is `(first + last) / 2` exactly, the same rational CPython's
/// `Fraction(total, n)` reduces to.
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
    let set = matches!(data, Value::Set(_));
    let mut it_ = match data {
        // A set's order never shows in an exact sum — but it would in WHICH
        // non-numeric element raises first, so there that one refuses.
        Value::Set(_) => {
            let items = it.collect_unordered(data)?;
            crate::iter::Iter::Tuple(Rc::new(items), 0)
        }
        other => it.make_iter(other)?,
    };
    let mut total = ival(0);
    let mut fix = Fix([0; LIMBS]);
    let mut float = false;
    let mut wide = false;
    let mut inf: Option<f64> = None;
    let mut n: i64 = 0;
    while let Some(x) = it.iter_next(&mut it_)? {
        match &x {
            Value::Int(_) | Value::Bool(_) => {
                total = it.binop(BinOp::Add, &total, &x)?;
                // Into the exact sum too, in case a float comes: the int total
                // itself may be past 64 bits long before any single item is.
                match &x {
                    Value::Int(i) => match i.small() {
                        Some(v) => fix.add_int(v),
                        None => wide = true,
                    },
                    _ => fix.add_int(matches!(x, Value::Bool(true)) as i64),
                }
            }
            Value::Float(f) => {
                float = true;
                if f.is_finite() {
                    fix.add_f64(*f);
                } else {
                    inf = Some(inf.map_or(*f, |s| s + f));
                }
            }
            #[cfg(feature = "cap-re")]
            Value::ReFlag(_) => return Err(refuse("mean() over a RegexFlag, an int subclass")),
            other if set => {
                return Err(refuse(&format!(
                    "mean() over a set holding a '{}'",
                    crate::value::type_name(other)
                )))
            }
            // `_exact_ratio`'s own message: nothing this engine holds but an
            // int, a bool or a float has `as_integer_ratio` or `numerator`.
            other => {
                return Err(crate::err::type_err(format!(
                    "can't convert type '{}' to numerator/denominator",
                    crate::value::type_name(other)
                )))
            }
        }
        n += 1;
    }
    if n == 0 {
        return Err(refuse("mean requires at least one data point (a StatisticsError)"));
    }
    if !float {
        return exact_div(it, &total, &ival(0), n);
    }
    // `_convert(total / n, float)`: a non-finite sum is returned divided by n
    // (still inf or nan); otherwise `float(Fraction)`, rounded once.
    if let Some(s) = inf {
        return Ok(Value::Float(s / n as f64));
    }
    if wide {
        return Err(refuse("mean() over a float and an int past 64 bits"));
    }
    Ok(Value::Float(fix.div_round(n as u64)))
}

/// 35 limbs, two's complement: a finite float is `m * 2**e` with
/// `e >= -1074`, so every one is an integer multiple of `2**-1074` below
/// `2**2098`, and 2**63 of them (or of i64s) stay below `2**2239`.
const LIMBS: usize = 35;
/// Bit `k` of the sum is worth `2**(k - 1074)`.
struct Fix([u64; LIMBS]);

impl Fix {
    /// `±(m << pos)`, exactly.
    fn add(&mut self, neg: bool, m: u64, pos: u32) {
        let (l, off) = ((pos / 64) as usize, pos % 64);
        let w = (m as u128) << off;
        let mut carry = 0u128;
        let mut borrow = false;
        for (k, limb) in self.0.iter_mut().enumerate().skip(l) {
            let part = match k - l {
                0 => w as u64,
                1 => (w >> 64) as u64,
                _ => 0,
            };
            if neg {
                let (a, b1) = limb.overflowing_sub(part);
                let (a, b2) = a.overflowing_sub(borrow as u64);
                *limb = a;
                borrow = b1 || b2;
                if k > l + 1 && !borrow {
                    break;
                }
            } else {
                let t = *limb as u128 + part as u128 + carry;
                *limb = t as u64;
                carry = t >> 64;
                if k > l + 1 && carry == 0 {
                    break;
                }
            }
        }
    }

    fn add_f64(&mut self, f: f64) {
        let b = f.to_bits();
        let e = ((b >> 52) & 0x7ff) as u32;
        let m = b & ((1 << 52) - 1);
        let (m, pos) = if e == 0 { (m, 0) } else { (m | 1 << 52, e - 1) };
        self.add(b >> 63 == 1, m, pos);
    }

    fn add_int(&mut self, i: i64) {
        self.add(i < 0, i.unsigned_abs(), 1074);
    }

    /// `float(Fraction(sum, n))`: the magnitude divided by `n` with the
    /// remainder as a sticky bit, rounded once, half to even. The bits of a
    /// float `m * 2**(s - 1074)` with `m < 2**53` are `(s << 52) + m` — the
    /// subnormals (`s == 0`) included, and a mantissa that rounds up to 2**53
    /// carries into the exponent by the same addition.
    fn div_round(mut self, n: u64) -> f64 {
        let neg = self.0[LIMBS - 1] >> 63 == 1;
        if neg {
            let mut c = true;
            for l in self.0.iter_mut() {
                let (v, o) = (!*l).overflowing_add(c as u64);
                *l = v;
                c = o;
            }
        }
        let mut r: u128 = 0;
        for l in self.0.iter_mut().rev() {
            let cur = (r << 64) | *l as u128;
            *l = (cur / n as u128) as u64;
            r = cur % n as u128;
        }
        let q = &self.0;
        let bit = |k: usize| (q[k / 64] >> (k % 64)) & 1;
        // A quotient of zero has no top bit: `h = 0` reads `m = 0`, and the
        // `s == 0` rounding below gives 0 or the smallest subnormal. An exact
        // zero sum is `+0.0` (`neg` is false for it, as `Fraction(0)` has no
        // sign); a negative one that ROUNDS to zero is `-0.0`, as in CPython.
        let h = (0..LIMBS)
            .rev()
            .find(|&k| q[k] != 0)
            .map_or(0, |k| k * 64 + 63 - q[k].leading_zeros() as usize);
        let s = h.saturating_sub(52);
        let mut m: u64 = 0;
        for k in (s..=h).rev() {
            m = m << 1 | bit(k);
        }
        let up = if s == 0 {
            let r2 = r * 2;
            r2 > n as u128 || (r2 == n as u128 && m & 1 == 1)
        } else {
            let rest = r != 0 || (0..s - 1).any(|k| bit(k) == 1);
            bit(s - 1) == 1 && (rest || m & 1 == 1)
        };
        let v = f64::from_bits(((s as u64) << 52) + m + up as u64);
        if neg {
            -v
        } else {
            v
        }
    }
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
