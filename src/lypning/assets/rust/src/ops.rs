//! Operators, indexing, slicing and attribute access.
//!
//! Two families of Python rule are easy to get wrong by writing the Rust that
//! looks equivalent, and both are implemented explicitly here:
//!
//!   * **Floor division and modulo round toward negative infinity**, and `%`
//!     takes the sign of the divisor. Rust's `/` truncates and `%` takes the
//!     sign of the dividend, so `-7 // 2` is `-4` in Python and `-3` in Rust.
//!   * **Integers do not wrap.** Every int op is checked; an overflow is
//!     `unsupported: bigint`, because Python's answer would be a bignum and any
//!     64-bit answer we produced would simply be wrong.

use crate::ast::{BinOp, CmpOp};
use crate::err::*;
use crate::eval::Interp;
use crate::fmt;
use crate::value::*;
use std::cell::RefCell;
use std::cmp::Ordering;
use std::rc::Rc;

impl Interp {
    pub fn binop(&mut self, op: BinOp, a: &Value, b: &Value) -> R<Value> {
        use BinOp::*;
        // Numeric fast path, then the per-type cases.
        if let (Some(x), Some(y)) = (as_num(a), as_num(b)) {
            if !matches!(op, Add | Sub | Mul | Div | FloorDiv | Mod | Pow)
                || matches!((x, y), (Num::I(_), Num::I(_)))
                || matches!(op, Add | Sub | Mul | Div | FloorDiv | Mod | Pow)
            {
                return num_binop(op, x, y);
            }
        }
        Ok(match (op, a, b) {
            (Add, Value::Str(x), Value::Str(y)) => Value::Str(format!("{x}{y}").into()),
            (Add, Value::Bytes(x), Value::Bytes(y)) => {
                let mut v = (**x).clone();
                v.extend_from_slice(y);
                Value::Bytes(Rc::new(v))
            }
            (Add, Value::List(x), Value::List(y)) => {
                let mut v = x.borrow().clone();
                v.extend(y.borrow().iter().cloned());
                list(v)
            }
            (Add, Value::Tuple(x), Value::Tuple(y)) => {
                let mut v = (**x).clone();
                v.extend(y.iter().cloned());
                Value::Tuple(Rc::new(v))
            }
            (Mul, Value::Str(s), n) | (Mul, n, Value::Str(s)) => {
                let n = crate::eval::int_val(n)?.max(0) as usize;
                Value::Str(s.repeat(check_alloc(s.len(), n, MAX_ALLOC_BYTES, "bytes")?).into())
            }
            (Mul, Value::Bytes(s), n) | (Mul, n, Value::Bytes(s)) => {
                let n = crate::eval::int_val(n)?.max(0) as usize;
                Value::Bytes(Rc::new(s.repeat(check_alloc(s.len(), n, MAX_ALLOC_BYTES, "bytes")?)))
            }
            (Mul, Value::List(l), n) | (Mul, n, Value::List(l)) => {
                let n = crate::eval::int_val(n)?.max(0) as usize;
                let src = l.borrow();
                let n = check_alloc(src.len(), n, MAX_ALLOC_ITEMS, "items")?;
                let mut v = Vec::with_capacity(src.len() * n);
                for _ in 0..n {
                    v.extend(src.iter().cloned());
                }
                list(v)
            }
            (Mul, Value::Tuple(t), n) | (Mul, n, Value::Tuple(t)) => {
                let n = crate::eval::int_val(n)?.max(0) as usize;
                let n = check_alloc(t.len(), n, MAX_ALLOC_ITEMS, "items")?;
                let mut v = Vec::with_capacity(t.len() * n);
                for _ in 0..n {
                    v.extend(t.iter().cloned());
                }
                Value::Tuple(Rc::new(v))
            }
            // `'%s' % x` — printf-style formatting, still common in one-liners.
            (Mod, Value::Str(f), arg) => Value::Str(percent_format(f, arg)?.into()),
            (BitOr, Value::Set(x), Value::Set(y)) => set_op(x, y, SetOp::Union)?,
            (BitAnd, Value::Set(x), Value::Set(y)) => set_op(x, y, SetOp::Inter)?,
            (Sub, Value::Set(x), Value::Set(y)) => set_op(x, y, SetOp::Diff)?,
            (BitXor, Value::Set(x), Value::Set(y)) => set_op(x, y, SetOp::Sym)?,
            (BitOr, Value::Dict(x), Value::Dict(y)) => {
                let mut d = Dict::new();
                for (k, v) in x.borrow().iter() {
                    d.insert(k.clone(), v.clone())?;
                }
                for (k, v) in y.borrow().iter() {
                    d.insert(k.clone(), v.clone())?;
                }
                Value::Dict(Rc::new(RefCell::new(d)))
            }
            _ => {
                return Err(type_err(format!(
                    "unsupported operand type(s) for {}: '{}' and '{}'",
                    op_sym(op),
                    type_name(a),
                    type_name(b)
                )))
            }
        })
    }

    pub fn compare(&mut self, op: CmpOp, a: &Value, b: &Value) -> R<bool> {
        Ok(match op {
            CmpOp::Eq => eq(a, b)?,
            CmpOp::Ne => !eq(a, b)?,
            CmpOp::Is => is_same(a, b),
            CmpOp::IsNot => !is_same(a, b),
            CmpOp::In => self.contains(b, a)?,
            CmpOp::NotIn => !self.contains(b, a)?,
            _ => {
                // Sets compare by subset, not by order.
                if let (Value::Set(x), Value::Set(y)) = (a, b) {
                    let (xs, ys) = (x.borrow(), y.borrow());
                    let sub = |p: &Set, q: &Set| -> R<bool> {
                        for it in p.items.iter() {
                            if !q.contains(it)? {
                                return Ok(false);
                            }
                        }
                        Ok(true)
                    };
                    return Ok(match op {
                        CmpOp::Le => sub(&xs, &ys)?,
                        CmpOp::Lt => xs.len() < ys.len() && sub(&xs, &ys)?,
                        CmpOp::Ge => sub(&ys, &xs)?,
                        CmpOp::Gt => xs.len() > ys.len() && sub(&ys, &xs)?,
                        _ => unreachable!(),
                    });
                }
                // NaN IS UNORDERED, AND THAT IS AN ANSWER, NOT AN ERROR.
                //
                // Every ordering comparison involving a NaN is False in Python
                // — `nan > 99.0`, `nan < 99.0` and `nan >= nan` alike — because
                // IEEE 754 says the relation does not hold, not because the
                // operands cannot be compared. lypning raised TypeError("cannot
                // order NaN") instead, turning a value CPython computes into an
                // exception. Found by scripts/lypning-fuzz.mjs.
                //
                // Equality already behaves correctly through eq() above, where
                // `nan == nan` is False for the same reason.
                if is_nan(a) || is_nan(b) {
                    return Ok(false);
                }
                let o = order(a, b)?;
                match op {
                    CmpOp::Lt => o == Ordering::Less,
                    CmpOp::Le => o != Ordering::Greater,
                    CmpOp::Gt => o == Ordering::Greater,
                    CmpOp::Ge => o != Ordering::Less,
                    _ => unreachable!(),
                }
            }
        })
    }

    pub fn contains(&mut self, container: &Value, needle: &Value) -> R<bool> {
        Ok(match container {
            Value::Str(s) => match needle {
                Value::Str(n) => s.contains(n.as_ref()),
                other => {
                    return Err(type_err(format!(
                        "'in <string>' requires string as left operand, not {}",
                        type_name(other)
                    )))
                }
            },
            Value::Bytes(b) => match needle {
                Value::Bytes(n) => b.windows(n.len().max(1)).any(|w| w == n.as_slice()) || n.is_empty(),
                Value::Int(i) => b.contains(&(*i as u8)),
                _ => return Err(type_err("a bytes-like object is required")),
            },
            Value::List(l) => {
                // The borrow is held across the loop rather than snapshotted:
                // `eq` takes no `&mut Interp` and cannot re-enter the evaluator,
                // so nothing can mutate the list underneath it. The snapshot it
                // replaces cost a Vec allocation and a refcount bump per element
                // on every `x in xs`.
                let items = l.borrow();
                for x in items.iter() {
                    if eq(x, needle)? {
                        return Ok(true);
                    }
                }
                false
            }
            Value::Tuple(t) => {
                for x in t.iter() {
                    if eq(x, needle)? {
                        return Ok(true);
                    }
                }
                false
            }
            Value::Dict(d) => d.borrow().contains(needle)?,
            Value::Set(s) => s.borrow().contains(needle)?,
            Value::Range(a, b, st) => match needle {
                Value::Int(i) => {
                    let inrange = if *st > 0 { *i >= *a && *i < *b } else { *i <= *a && *i > *b };
                    inrange && (*i - *a).rem_euclid(*st) == 0
                }
                _ => false,
            },
            Value::Gen(_) => {
                let mut it = self.make_iter(container.clone())?;
                while let Some(x) = self.iter_next(&mut it)? {
                    if eq(&x, needle)? {
                        return Ok(true);
                    }
                }
                false
            }
            other => {
                return Err(type_err(format!(
                    "argument of type '{}' is not iterable",
                    type_name(other)
                )))
            }
        })
    }

    pub fn index(&mut self, base: &Value, idx: &Value) -> R<Value> {
        Ok(match base {
            Value::Dict(d) => match d.borrow().get(idx)? {
                Some(v) => v,
                None => return Err(key_err(fmt::repr(idx)?)),
            },
            Value::List(l) => {
                let b = l.borrow();
                let i = norm_index(crate::eval::int_val(idx)?, b.len(), "list")?;
                b[i].clone()
            }
            Value::Tuple(t) => {
                let i = norm_index(crate::eval::int_val(idx)?, t.len(), "tuple")?;
                t[i].clone()
            }
            Value::Str(s) => {
                // ASCII is the case every one-liner is, and there a byte offset
                // IS a character offset — so the index is O(1) and the result is
                // a subslice of bytes that already exist. The general path below
                // collects the whole string into a `Vec<char>` to reach one of
                // them, which is O(n) in the string for an O(1) question.
                if s.is_ascii() {
                    let i = norm_index(crate::eval::int_val(idx)?, s.len(), "string")?;
                    Value::Str(s[i..i + 1].into())
                } else {
                    let chars: Vec<char> = s.chars().collect();
                    let i = norm_index(crate::eval::int_val(idx)?, chars.len(), "string")?;
                    Value::Str(chars[i].to_string().into())
                }
            }
            Value::Bytes(b) => {
                let i = norm_index(crate::eval::int_val(idx)?, b.len(), "bytearray")?;
                Value::Int(b[i] as i64)
            }
            Value::Range(a, bb, st) => {
                let n = range_len(*a, *bb, *st);
                let i = norm_index(crate::eval::int_val(idx)?, n as usize, "range")?;
                Value::Int(a + (i as i64) * st)
            }
            other => {
                return Err(type_err(format!(
                    "'{}' object is not subscriptable",
                    type_name(other)
                )))
            }
        })
    }

    pub fn set_item(&mut self, base: &Value, idx: Value, v: Value) -> R<()> {
        match base {
            Value::Dict(d) => d.borrow_mut().insert(idx, v)?,
            Value::List(l) => {
                let n = l.borrow().len();
                let i = norm_index(crate::eval::int_val(&idx)?, n, "list")?;
                l.borrow_mut()[i] = v;
            }
            other => {
                return Err(type_err(format!(
                    "'{}' object does not support item assignment",
                    type_name(other)
                )))
            }
        }
        Ok(())
    }

    pub fn del_item(&mut self, base: &Value, idx: &Value) -> R<()> {
        match base {
            Value::Dict(d) => {
                if d.borrow_mut().remove(idx)?.is_none() {
                    return Err(key_err(fmt::repr(idx)?));
                }
            }
            Value::List(l) => {
                let n = l.borrow().len();
                let i = norm_index(crate::eval::int_val(idx)?, n, "list")?;
                l.borrow_mut().remove(i);
            }
            other => {
                return Err(type_err(format!(
                    "'{}' object doesn't support item deletion",
                    type_name(other)
                )))
            }
        }
        Ok(())
    }

    pub fn slice(
        &mut self,
        base: &Value,
        lo: Option<Value>,
        hi: Option<Value>,
        step: Option<Value>,
    ) -> R<Value> {
        let step = match &step {
            None | Some(Value::None) => 1i64,
            Some(v) => {
                let s = crate::eval::int_val(v)?;
                if s == 0 {
                    return Err(value_err("slice step cannot be zero"));
                }
                s
            }
        };
        let opt = |v: &Option<Value>| -> R<Option<i64>> {
            Ok(match v {
                None | Some(Value::None) => None,
                Some(x) => Some(crate::eval::int_val(x)?),
            })
        };
        let (lo, hi) = (opt(&lo)?, opt(&hi)?);
        // `step == 1` is what almost every slice in the corpus is, and it names a
        // CONTIGUOUS range. The general path below has to materialise the picked
        // indices because a step can skip or reverse; taking that range directly
        // is one copy instead of an index vector plus a gather.
        let contiguous = step == 1;
        Ok(match base {
            Value::Str(s) => {
                // Byte offsets are character offsets exactly when the text is
                // ASCII. Non-ASCII falls through to the general path rather than
                // growing a second boundary-scanning implementation of its own —
                // one more way to slice a string is one more way to slice it
                // differently from CPython.
                if contiguous && s.is_ascii() {
                    let (a, b) = slice_span(s.len(), lo, hi);
                    Value::Str(s[a..b].into())
                } else {
                    let chars: Vec<char> = s.chars().collect();
                    let picked = slice_indices(chars.len(), lo, hi, step);
                    Value::Str(picked.into_iter().map(|i| chars[i]).collect::<String>().into())
                }
            }
            Value::Bytes(b) => {
                if contiguous {
                    let (x, y) = slice_span(b.len(), lo, hi);
                    Value::Bytes(Rc::new(b[x..y].to_vec()))
                } else {
                    let picked = slice_indices(b.len(), lo, hi, step);
                    Value::Bytes(Rc::new(picked.into_iter().map(|i| b[i]).collect()))
                }
            }
            Value::List(l) => {
                let b = l.borrow();
                if contiguous {
                    let (x, y) = slice_span(b.len(), lo, hi);
                    list(b[x..y].to_vec())
                } else {
                    let picked = slice_indices(b.len(), lo, hi, step);
                    list(picked.into_iter().map(|i| b[i].clone()).collect())
                }
            }
            Value::Tuple(t) => {
                if contiguous {
                    let (x, y) = slice_span(t.len(), lo, hi);
                    Value::Tuple(Rc::new(t[x..y].to_vec()))
                } else {
                    let picked = slice_indices(t.len(), lo, hi, step);
                    Value::Tuple(Rc::new(picked.into_iter().map(|i| t[i].clone()).collect()))
                }
            }
            other => {
                return Err(type_err(format!(
                    "'{}' object is not subscriptable",
                    type_name(other)
                )))
            }
        })
    }

    pub fn get_attr(&mut self, base: &Value, name: &str) -> R<Value> {
        if let Value::Module(_) = base {
            return crate::modules::get_attr(base, name);
        }
        if let Some(m) = crate::methods::method_name(base, name) {
            return Ok(Value::Bound(Rc::new(base.clone()), m));
        }
        // `str.upper` — the UNBOUND method, which `map(str.upper, xs)` uses.
        // Represented as a bound method on the type object; `call_method` then
        // takes the receiver from the first argument.
        if let Value::Builtin(t) = base {
            let probe = match *t {
                "str" => Some(Value::Str("".into())),
                "list" => Some(crate::value::list(Vec::new())),
                "dict" => Some(Value::Dict(Rc::new(RefCell::new(Dict::new())))),
                "set" => Some(Value::Set(Rc::new(RefCell::new(Set::new())))),
                "bytes" => Some(Value::Bytes(Rc::new(Vec::new()))),
                _ => None,
            };
            if let Some(p) = probe {
                if let Some(m) = crate::methods::method_name(&p, name) {
                    return Ok(Value::Bound(Rc::new(base.clone()), m));
                }
                if crate::methods::missing_method(&p, name) {
                    return Err(missing_method_err(&p, name));
                }
            }
        }
        if let Value::Exc(_, msg) = base {
            match name {
                "args" => return Ok(Value::Tuple(Rc::new(vec![Value::Str(msg.clone())]))),
                // OSError-family exceptions carry `.errno`/`.strerror`/
                // `.filename`, and the message we build always has the shape
                // `[Errno N] text: 'path'`, so read them back from it.
                "errno" | "strerror" | "filename" => {
                    if let Some(rest) = msg.strip_prefix("[Errno ") {
                        if let Some(close) = rest.find(']') {
                            let n: i64 = rest[..close].parse().unwrap_or(0);
                            let tail = rest[close + 1..].trim_start();
                            let (text, file) = match tail.rfind(": '") {
                                Some(i) => (&tail[..i], tail[i + 3..].trim_end_matches('\'')),
                                None => (tail, ""),
                            };
                            return Ok(match name {
                                "errno" => Value::Int(n),
                                "strerror" => Value::Str(text.into()),
                                _ => Value::Str(file.into()),
                            });
                        }
                    }
                    return Ok(Value::None);
                }
                _ => {}
            }
        }
        if crate::methods::missing_method(base, name) {
            return Err(missing_method_err(base, name));
        }
        Err(attr_err(format!(
            "'{}' object has no attribute '{name}'",
            type_name(base)
        )))
    }
}

/// A method CPython has and lypning does not: exit 90, never `AttributeError`.
///
/// `AttributeError` at exit 1 is the program's own failure and the dispatcher
/// returns it unchanged, so it is the one answer the caller cannot recover
/// from. See `methods::missing_method`.
fn missing_method_err(recv: &Value, name: &str) -> LypningError {
    let ty = type_name(recv);
    unsupported(&format!("{ty}-method"), &format!("{ty}.{name}()"))
}

// ---- numbers --------------------------------------------------------------

fn num_binop(op: BinOp, a: Num, b: Num) -> R<Value> {
    use BinOp::*;
    // Bit operations are integer-only in Python.
    if matches!(op, BitAnd | BitOr | BitXor | LShift | RShift) {
        let (Num::I(x), Num::I(y)) = (a, b) else {
            return Err(type_err(format!(
                "unsupported operand type(s) for {}: 'float' and 'float'",
                op_sym(op)
            )));
        };
        return Ok(Value::Int(match op {
            BitAnd => x & y,
            BitOr => x | y,
            BitXor => x ^ y,
            LShift => {
                if !(0..64).contains(&y) {
                    return Err(if y < 0 {
                        value_err("negative shift count")
                    } else {
                        unsupported("bigint", "left shift beyond 64-bit range")
                    });
                }
                x.checked_shl(y as u32)
                    .filter(|r| r >> y == x)
                    .ok_or_else(|| unsupported("bigint", "left shift overflow"))?
            }
            RShift => {
                if y < 0 {
                    return Err(value_err("negative shift count"));
                }
                if y >= 64 {
                    if x < 0 {
                        -1
                    } else {
                        0
                    }
                } else {
                    x >> y
                }
            }
            _ => unreachable!(),
        }));
    }
    if let (Num::I(x), Num::I(y)) = (a, b) {
        return Ok(match op {
            Add => Value::Int(x.checked_add(y).ok_or_else(ovf)?),
            Sub => Value::Int(x.checked_sub(y).ok_or_else(ovf)?),
            Mul => Value::Int(x.checked_mul(y).ok_or_else(ovf)?),
            // `/` is ALWAYS float in Python 3, even for two ints.
            Div => {
                if y == 0 {
                    return Err(zero_div("division by zero"));
                }
                Value::Float(x as f64 / y as f64)
            }
            FloorDiv => {
                if y == 0 {
                    return Err(zero_div("integer division or modulo by zero"));
                }
                Value::Int(x.checked_div_euclid(y).ok_or_else(ovf).map(|q| {
                    // div_euclid rounds toward -inf only for positive divisors.
                    if y < 0 && x.rem_euclid(y) != 0 {
                        q - 1
                    } else {
                        q
                    }
                })?)
            }
            Mod => {
                if y == 0 {
                    return Err(zero_div("integer division or modulo by zero"));
                }
                // Python's % has the SIGN OF THE DIVISOR; Rust's has the sign
                // of the dividend.
                let r = x.checked_rem(y).ok_or_else(ovf)?;
                Value::Int(if r != 0 && (r < 0) != (y < 0) { r + y } else { r })
            }
            Pow => {
                if y < 0 {
                    Value::Float((x as f64).powf(y as f64))
                } else {
                    let mut acc: i64 = 1;
                    let mut base = x;
                    let mut e = y as u64;
                    while e > 0 {
                        if e & 1 == 1 {
                            acc = acc.checked_mul(base).ok_or_else(ovf)?;
                        }
                        e >>= 1;
                        if e > 0 {
                            base = base.checked_mul(base).ok_or_else(ovf)?;
                        }
                    }
                    Value::Int(acc)
                }
            }
            _ => unreachable!(),
        });
    }
    let (x, y) = (fl(a), fl(b));
    Ok(match op {
        Add => Value::Float(x + y),
        Sub => Value::Float(x - y),
        Mul => Value::Float(x * y),
        Div => {
            if y == 0.0 {
                return Err(zero_div("float division by zero"));
            }
            Value::Float(x / y)
        }
        FloorDiv => {
            if y == 0.0 {
                return Err(zero_div("float floor division by zero"));
            }
            // inf // 2.5 is nan in CPython, not inf: the floor of an infinite
            // quotient has no value, and math.floor(inf) is an error. Rust's
            // (inf/2.5).floor() is inf, so this needed saying.
            let q = x / y;
            if !q.is_finite() {
                // inf // 2.5 is nan in CPython, not inf: the floor of an
                // infinite quotient has no value. Rust's (inf/2.5).floor() is
                // inf, so this needed saying.
                Value::Float(f64::NAN)
            } else {
                // CPython computes fmod first and derives the quotient from it,
                // which is exact where flooring the rounded quotient is not:
                // 1e16 // -3.0 is -3333333333333335.0, and (1e16 / -3.0).floor()
                // gives -3333333333333334.0 because the division rounded up
                // before the floor could see it.
                let mod_ = x % y;
                let mut div = (x - mod_) / y;
                if mod_ != 0.0 && (mod_ < 0.0) != (y < 0.0) {
                    div -= 1.0;
                }
                // A ZERO QUOTIENT KEEPS THE SIGN THE DIVISION WOULD HAVE
                // GIVEN IT. -0.0 // 1.0 is -0.0 in CPython, and deriving div
                // from (x - mod_) loses that because the subtraction produces a
                // positive zero. repr() shows the sign, so it is observable.
                if div == 0.0 {
                    Value::Float(if x.is_sign_negative() != y.is_sign_negative() { -0.0 } else { 0.0 })
                } else {
                    Value::Float(div.floor())
                }
            }
        }
        Mod => {
            if y == 0.0 {
                return Err(zero_div("float modulo"));
            }
            let r = x % y;
            let mut m = if r != 0.0 && (r < 0.0) != (y < 0.0) { r + y } else { r };
            // A ZERO REMAINDER TAKES THE DIVISOR'S SIGN. Rust's % keeps the
            // DIVIDEND's, so -516.0 % 1.0 was -0.0 where CPython gives 0.0, and
            // 99.0 % -3.0 was 0.0 where CPython gives -0.0. repr() shows the
            // sign of zero, so this is visible in output rather than academic.
            if m == 0.0 {
                m = if y.is_sign_negative() { -0.0 } else { 0.0 };
            }
            Value::Float(m)
        }
        Pow => {
            // Three cases Rust's powf answers and Python does not.
            if x == 0.0 && y < 0.0 {
                return Err(zero_div("0.0 cannot be raised to a negative power"));
            }
            if x < 0.0 && y != y.trunc() {
                // CPython returns a COMPLEX number here. lypning has no complex
                // type, so this is a refusal, not a nan: (-2.0) ** 0.5 answered
                // nan at exit 0 where CPython answers 1.4142135623730951j.
                return Err(unsupported(
                    "complex",
                    "a negative float raised to a fractional power (Python returns a complex number)",
                ));
            }
            let r = x.powf(y);
            if r.is_infinite() && x.is_finite() && y.is_finite() {
                return Err(overflow_err("(34, 'Numerical result out of range')"));
            }
            Value::Float(r)
        }
        _ => unreachable!(),
    })
}

fn ovf() -> LypningError {
    unsupported(
        "bigint",
        "integer result beyond 64-bit range (Python would use a bignum)",
    )
}

fn fl(n: Num) -> f64 {
    match n {
        Num::I(i) => i as f64,
        Num::F(f) => f,
    }
}

pub fn op_sym(op: BinOp) -> &'static str {
    use BinOp::*;
    match op {
        Add => "+",
        Sub => "-",
        Mul => "*",
        Div => "/",
        FloorDiv => "//",
        Mod => "%",
        Pow => "**",
        BitAnd => "&",
        BitOr => "|",
        BitXor => "^",
        LShift => "<<",
        RShift => ">>",
    }
}

/// Is this value a float NaN? Only a float can be one — an int never is, and a
/// bool never is — so this deliberately does not go through as_num().
fn is_nan(v: &Value) -> bool {
    matches!(v, Value::Float(f) if f.is_nan())
}

// ---- ordering -------------------------------------------------------------

/// Python's `<` on values of different types is a TypeError, not a fallback to
/// some arbitrary total order. Reproducing that exactly is what keeps `sorted`
/// on a mixed list from silently succeeding here and failing there.
pub fn order(a: &Value, b: &Value) -> R<Ordering> {
    // The numeric and scalar paths run BEFORE the guard, because neither can
    // descend and `sorted()` of a list of ints reaches this once per
    // comparison. See `value::eq` for the same split and the same reasoning.
    if let (Some(x), Some(y)) = (as_num(a), as_num(b)) {
        let (x, y) = (fl(x), fl(y));
        if let (Num::I(i), Num::I(j)) = (as_num(a).unwrap(), as_num(b).unwrap()) {
            return Ok(i.cmp(&j));
        }
        return x
            .partial_cmp(&y)
            .ok_or_else(|| type_err("cannot order NaN"));
    }
    Ok(match (a, b) {
        (Value::Str(x), Value::Str(y)) => x.as_bytes().cmp(y.as_bytes()),
        (Value::Bytes(x), Value::Bytes(y)) => x.cmp(y),
        // The two arms that descend. `sorted([x, y])` over two deep lists is a
        // stack overflow without this, and a stack overflow embedded is the
        // HOST's SIGSEGV rather than a refusal it can route onward.
        (Value::List(x), Value::List(y)) => {
            let _nest = crate::err::Nest::enter("comparison")?;
            let (x, y) = (x.borrow(), y.borrow());
            seq_order(&x, &y)?
        }
        (Value::Tuple(x), Value::Tuple(y)) => {
            let _nest = crate::err::Nest::enter("comparison")?;
            seq_order(x, y)?
        }
        _ => {
            return Err(type_err(format!(
                "'<' not supported between instances of '{}' and '{}'",
                type_name(a),
                type_name(b)
            )))
        }
    })
}

fn seq_order(x: &[Value], y: &[Value]) -> R<Ordering> {
    for (a, b) in x.iter().zip(y.iter()) {
        if !eq(a, b)? {
            return order(a, b);
        }
    }
    Ok(x.len().cmp(&y.len()))
}

/// Stable merge sort using Python's ordering rules, so a comparison TypeError
/// propagates instead of being swallowed by a `sort_by` that must return an
/// `Ordering`.
pub fn sort_values(items: &mut Vec<Value>, keys: &mut Vec<Value>, reverse: bool) -> R<()> {
    let n = items.len();
    let mut idx: Vec<usize> = (0..n).collect();
    let mut buf = vec![0usize; n];
    let mut width = 1;
    while width < n {
        let mut i = 0;
        while i < n {
            let mid = (i + width).min(n);
            let end = (i + 2 * width).min(n);
            let (mut l, mut r, mut k) = (i, mid, i);
            while l < mid && r < end {
                // `<=` on the left keeps the sort stable.
                let o = order(&keys[idx[r]], &keys[idx[l]])?;
                if o == Ordering::Less {
                    buf[k] = idx[r];
                    r += 1;
                } else {
                    buf[k] = idx[l];
                    l += 1;
                }
                k += 1;
            }
            while l < mid {
                buf[k] = idx[l];
                l += 1;
                k += 1;
            }
            while r < end {
                buf[k] = idx[r];
                r += 1;
                k += 1;
            }
            i += 2 * width;
        }
        std::mem::swap(&mut idx, &mut buf);
        width *= 2;
    }
    if reverse {
        idx.reverse();
    }
    let src = std::mem::take(items);
    let mut taken: Vec<Option<Value>> = src.into_iter().map(Some).collect();
    *items = idx.iter().map(|i| taken[*i].take().unwrap()).collect();
    keys.clear();
    Ok(())
}

// ---- indexing helpers -----------------------------------------------------

pub fn norm_index(i: i64, n: usize, what: &str) -> R<usize> {
    let n = n as i64;
    let j = if i < 0 { n + i } else { i };
    if j < 0 || j >= n {
        return Err(index_err(format!("{what} index out of range")));
    }
    Ok(j as usize)
}

/// The half-open `[start, stop)` a `step == 1` slice selects — Python's own
/// clamping, without the index vector.
///
/// Kept beside [`slice_indices`] and derived the same way on purpose: these two
/// must agree for every `(n, lo, hi)`, and a reader checking that should not
/// have to go looking. `stop` is never below `start`, which is how Python's
/// empty slice (`'abc'[2:1]`) comes out empty rather than panicking on an
/// inverted range.
pub fn slice_span(n: usize, lo: Option<i64>, hi: Option<i64>) -> (usize, usize) {
    let n = n as i64;
    let clamp = |v: i64| -> i64 {
        let v = if v < 0 { n + v } else { v };
        v.clamp(0, n)
    };
    let start = lo.map_or(0, clamp);
    let stop = hi.map_or(n, clamp).max(start);
    (start as usize, stop as usize)
}

pub fn slice_indices(n: usize, lo: Option<i64>, hi: Option<i64>, step: i64) -> Vec<usize> {
    let n = n as i64;
    let clamp = |v: i64, lodef: i64, hidef: i64| -> i64 {
        let v = if v < 0 { n + v } else { v };
        v.clamp(lodef, hidef)
    };
    let mut out = Vec::new();
    if step > 0 {
        let start = lo.map_or(0, |v| clamp(v, 0, n));
        let stop = hi.map_or(n, |v| clamp(v, 0, n));
        let mut i = start;
        while i < stop {
            out.push(i as usize);
            i += step;
        }
    } else {
        let start = lo.map_or(n - 1, |v| clamp(v, -1, n - 1));
        let stop = hi.map_or(-1, |v| clamp(v, -1, n - 1));
        let mut i = start;
        while i > stop {
            if i >= 0 {
                out.push(i as usize);
            }
            i += step;
        }
    }
    out
}

// ---- set algebra ----------------------------------------------------------

pub enum SetOp {
    Union,
    Inter,
    Diff,
    Sym,
}

pub fn set_op(x: &Rc<RefCell<Set>>, y: &Rc<RefCell<Set>>, op: SetOp) -> R<Value> {
    let mut out = Set::new();
    let (xs, ys) = (x.borrow(), y.borrow());
    match op {
        SetOp::Union => {
            for v in xs.items.iter().chain(ys.items.iter()) {
                out.add(v.clone())?;
            }
        }
        SetOp::Inter => {
            for v in xs.items.iter() {
                if ys.contains(v)? {
                    out.add(v.clone())?;
                }
            }
        }
        SetOp::Diff => {
            for v in xs.items.iter() {
                if !ys.contains(v)? {
                    out.add(v.clone())?;
                }
            }
        }
        SetOp::Sym => {
            for v in xs.items.iter() {
                if !ys.contains(v)? {
                    out.add(v.clone())?;
                }
            }
            for v in ys.items.iter() {
                if !xs.contains(v)? {
                    out.add(v.clone())?;
                }
            }
        }
    }
    Ok(Value::Set(Rc::new(RefCell::new(out))))
}

// ---- printf-style % formatting -------------------------------------------

/// `'%d-%s' % (i, 'a')` — seven bytes of output, and this used to allocate a
/// dozen times to produce them.
///
/// Three of those are gone. The format string was collected into a
/// `Vec<char>` on **every call** — four bytes per character of a string that is
/// almost always ASCII — where the scan can walk the bytes and copy each literal
/// run with one `push_str`; `%` is ASCII and cannot occur inside a multi-byte
/// sequence, so every slice boundary here is a character boundary. The
/// translated spec was a fresh `format!` **per conversion**, and is now one
/// buffer cleared and refilled for the whole call. And the output starts at the
/// format string's length instead of at zero.
///
/// What is left per conversion is `percent_one`'s chain, which is its own
/// problem and is documented there.
fn percent_format(f: &str, arg: &Value) -> R<String> {
    let args: Vec<Value> = match arg {
        Value::Tuple(t) => (**t).clone(),
        other => vec![other.clone()],
    };
    let b = f.as_bytes();
    let mut out = String::with_capacity(f.len());
    // One buffer for every conversion in this format string.
    let mut spec = String::new();
    let mut min_digits = 0usize;
    let mut ai = 0;
    let mut i = 0;
    while i < b.len() {
        if b[i] != b'%' {
            // The whole literal run in one copy rather than a push per char.
            let start = i;
            while i < b.len() && b[i] != b'%' {
                i += 1;
            }
            out.push_str(&f[start..i]);
            continue;
        }
        i += 1;
        if i < b.len() && b[i] == b'%' {
            out.push('%');
            i += 1;
            continue;
        }
        // Mapping key `%(name)s` — used with a dict on the right.
        if i < b.len() && b[i] == b'(' {
            let mut j = i + 1;
            while j < b.len() && b[j] != b')' {
                j += 1;
            }
            let key = &f[i + 1..j];
            let Value::Dict(d) = arg else {
                return Err(type_err("format requires a mapping"));
            };
            let v = d
                .borrow()
                .get(&Value::Str(key.into()))?
                .ok_or_else(|| key_err(format!("'{key}'")))?;
            i = j + 1;
            i = read_spec(f, b, i, &mut spec, &mut min_digits)?;
            out.push_str(&percent_one(&v, &spec, min_digits)?);
            continue;
        }
        i = read_spec(f, b, i, &mut spec, &mut min_digits)?;
        let v = args
            .get(ai)
            .ok_or_else(|| type_err("not enough arguments for format string"))?;
        ai += 1;
        out.push_str(&percent_one(v, &spec, min_digits)?);
    }
    if ai < args.len() && !matches!(arg, Value::Dict(_)) {
        return Err(type_err(
            "not all arguments converted during string formatting",
        ));
    }
    Ok(out)
}

/// Read one printf conversion and translate it into a `format()` spec, written
/// into `spec` rather than returned. Returns the index just past the conversion.
///
/// Every part of a conversion is ASCII, so the pieces are byte slices of the
/// format string itself and none of them allocates. The one place a non-ASCII
/// character can appear is the conversion letter, and that is an error path —
/// it is decoded there so the message can name it.
fn read_spec(
    f: &str,
    b: &[u8],
    mut i: usize,
    spec: &mut String,
    min_digits: &mut usize,
) -> R<usize> {
    spec.clear();
    *min_digits = 0;
    let flag0 = i;
    while i < b.len() && matches!(b[i], b'-' | b'+' | b' ' | b'#' | b'0') {
        i += 1;
    }
    let flags = &f[flag0..i];
    let width0 = i;
    while i < b.len() && b[i].is_ascii_digit() {
        i += 1;
    }
    let width = &f[width0..i];
    let prec0 = i;
    let mut bare_dot = false;
    if i < b.len() && b[i] == b'.' {
        i += 1;
        let digits0 = i;
        while i < b.len() && b[i].is_ascii_digit() {
            i += 1;
        }
        bare_dot = i == digits0;
    }
    let prec = &f[prec0..i];
    while i < b.len() && matches!(b[i], b'l' | b'h' | b'L') {
        i += 1;
    }
    if i >= b.len() {
        return Err(value_err("incomplete format"));
    }
    let ty = match b[i] {
        b'd' | b'i' | b'u' => "d",
        b's' => "s",
        b'r' => "r",
        b'f' | b'F' => "f",
        b'e' => "e",
        b'E' => "E",
        b'g' => "g",
        b'G' => "G",
        b'x' => "x",
        b'X' => "X",
        b'o' => "o",
        b'c' => "c",
        _ => {
            let other = f[i..].chars().next().unwrap_or('?');
            return Err(unsupported(
                "percent-format",
                &format!("%{other} conversion"),
            ));
        }
    };
    i += 1;
    // `[align][sign][#][0][width][.prec][type]`, in that order, because that is
    // the order `format()` parses and the pieces are NOT commutative: `+0f` is
    // valid and `0+f` is a ValueError, `#05x` is `0x0ff` and `0#5x` is a
    // ValueError. This used to emit the zero-pad flag as if it were an
    // alignment, first, so every `%` conversion combining `0` with a sign or
    // with `#` raised ValueError where CPython formats — 1,560 cells of the
    // conversion grid.
    let left = flags.contains('-');
    if left {
        spec.push('<');
    } else if ty == "s" || ty == "r" || ty == "c" {
        // **The default alignment for `%s` is RIGHT**, and `format()`'s default
        // for a string is LEFT — so the translation has to say so out loud.
        // Without this `'%5s' % 'ab'` produced `'ab   '` where CPython produces
        // `'   ab'`, which is every `%` one-liner that lines a column up.
        spec.push('>');
    }
    if flags.contains('+') {
        spec.push('+');
    } else if flags.contains(' ') {
        spec.push(' ');
    }
    if flags.contains('#') {
        spec.push('#');
    }
    // A `-` beats a `0`: `'%-05d' % 255` is `'255  '`, not zero-padded. And a
    // `0` never applies to a string conversion: `'%05s' % 'a'` is `'    a'`.
    if flags.contains('0') && !left && ty != "s" && ty != "r" && ty != "c" {
        spec.push('0');
    }
    spec.push_str(width);
    // **A precision on an integer conversion is MINIMUM DIGITS**, not a
    // `format()` precision — `'%.2d' % 1` is `'01'` and `'%.7d' % -42` is
    // `'-0000042'`. `format()` has no such thing, and passing `.N` through to it
    // meant the precision was silently ignored: 3,724 cells of the conversion
    // grid answered without it, at exit 0.
    //
    // The DECISION is `percent_one`'s, because it needs the value: a precision
    // that asks for no more digits than the number already has changes nothing,
    // and refusing those would give away coverage for free (`'%.2d' % 42` is
    // `'42'` either way). Reported here, acted on there.
    //
    // `%.0d` and `%.d` never reach it — zero minimum digits is what every value
    // already has — and neither does `%.Nc`, which CPython ignores.
    if !prec.is_empty() && !bare_dot && prec != ".0" && matches!(ty, "d" | "x" | "X" | "o" | "b") {
        *min_digits = prec[1..].parse().unwrap_or(0);
    } else {
        spec.push_str(prec);
    }
    if bare_dot {
        spec.push('0');
    }
    spec.push_str(ty);
    Ok(i)
}

/// `min_digits` is the precision of an INTEGER conversion, which `format()` has
/// no spelling for: `'%.2d' % 1` is `'01'` and `'%.7d' % -42` is `'-0000042'`.
///
/// It is honoured when the value already satisfies it and **refused** when it
/// does not. That split is the whole point of deciding here rather than in
/// `read_spec`: only a value knows how many digits it has, so `'%.2d' % 42`
/// keeps working and only `'%.2d' % 1` leaves.
///
/// Refused rather than implemented, deliberately. It IS expressible — the body
/// is `format(v, "0{P + 1 if signed}d")`, and the outer width composes on top,
/// collapsing to a single call of width `max(P + signlen, W)` when the `0` flag
/// is set. That is three composition rules to get exactly right on a construct
/// the corpus barely contains, and this session has already shipped one bug by
/// being clever on an error path (ledger, iteration 28). A refusal costs one
/// spawn and CPython answers it; a wrong answer costs the caller's trust.
fn percent_one(v: &Value, spec: &str, min_digits: usize) -> R<String> {
    // `%c` is the one conversion that cannot be handed to `format()` wholesale:
    // it takes an int **or a one-character string**, where `format()`'s `c`
    // takes only an int (`format('a', 'c')` is a ValueError there). So
    // `'%c' % 'a'` was refused where CPython answers `'a'`, and `'%c' % 1.5`
    // refused where CPython raises a TypeError naming the conversion.
    if spec.ends_with('c') {
        match v {
            Value::Int(_) | Value::Bool(_) => {}
            Value::Str(s) if s.chars().count() == 1 => {
                let as_str = format!("{}s", &spec[..spec.len() - 1]);
                return fmt::format_value(v, &as_str);
            }
            _ => return Err(type_err("%c requires int or char")),
        }
    }
    if min_digits > 0 {
        let n = match v {
            Value::Int(i) => *i,
            Value::Bool(b) => *b as i64,
            // A float or anything else here is already the `integer format code
            // applied to …` refusal one line down; let it produce its message.
            _ => 0,
        };
        let a = n.unsigned_abs();
        let have = match spec.chars().last() {
            Some('x') | Some('X') => format!("{a:x}").len(),
            Some('o') => format!("{a:o}").len(),
            Some('b') => format!("{a:b}").len(),
            _ => a.to_string().len(),
        };
        if have < min_digits {
            return Err(unsupported(
                "percent-format",
                &format!(
                    "%.{min_digits}{} — a precision on an integer conversion is minimum digits",
                    spec.chars().last().unwrap_or('d')
                ),
            ));
        }
    }
    if let Some(rest) = spec.strip_suffix('r') {
        let s = fmt::repr(v)?;
        return fmt::format_value(&Value::Str(s.into()), &format!("{rest}s"));
    }
    if spec.ends_with('s') {
        let s = fmt::to_str(v)?;
        return fmt::format_value(&Value::Str(s.into()), spec);
    }
    fmt::format_value(v, spec)
}

// ---- the allocation ceiling ------------------------------------------------
//
// `"a" * (10**14)` asks Rust's global allocator for 100 TB. There is no
// fallible path: the allocator's failure handler ABORTS, which is not an
// unwind, so `catch_unwind` at the C ABI boundary cannot see it and a host
// application dies with no status and no message. CPython answers the same
// program with `MemoryError` or `OverflowError`, so the honest thing for a
// subset runtime is the honest thing everywhere else here — refuse, and let
// the program be answered by an interpreter that can raise.
//
// The two ceilings are deliberately far above anything a one-liner does and far
// below anything that threatens a host: a quarter of a gigabyte of bytes, and
// sixteen million elements (a `Value` is several words, so that is the same
// order of memory).

const MAX_ALLOC_BYTES: usize = 256 << 20;
const MAX_ALLOC_ITEMS: usize = 16 << 20;

/// `n`, if `unit * n` stays under `limit`. A refusal otherwise.
///
/// Checked rather than computed: `unit * n` itself overflows `usize` for a
/// large enough `n`, and with `overflow-checks` off in release that wraps to a
/// small number and allocates the WRONG size, which is worse than either
/// failure it is standing in for.
fn check_alloc(unit: usize, n: usize, limit: usize, what: &str) -> R<usize> {
    let total = unit.checked_mul(n).unwrap_or(usize::MAX);
    if total > limit {
        return Err(unsupported(
            "alloc",
            &format!("a sequence of {total} {what} — over this runtime's {limit} ceiling"),
        ));
    }
    Ok(n)
}
