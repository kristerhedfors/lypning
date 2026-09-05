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
        // A flag FIRST, before the numeric fast path: `as_num` reads a
        // `RegexFlag` as its int, which is right for `+ - * < ==` and every
        // other operator — and would answer `re.I | re.M` as `10` at exit 0
        // where CPython keeps `| & ^` in the flag type. `re::binop` takes
        // those three and refuses a non-int partner; everything else it
        // leaves to the paths below.
        #[cfg(feature = "cap-re")]
        {
            if let Some(v) = crate::re::binop(op, a, b)? {
                return Ok(v);
            }
            // `'ab' * re.I` — sequence repetition reads the flag through
            // `__index__`, and the fast path cannot see it because the other
            // operand is not a number. Only `*`: `%` is percent formatting,
            // which must see the flag itself (`'%s' % re.I` prints its repr).
            if let Mul = op {
                let (x, y) = (crate::re::as_int(a), crate::re::as_int(b));
                if x.is_some() || y.is_some() {
                    return self.binop(op, x.as_ref().unwrap_or(a), y.as_ref().unwrap_or(b));
                }
            }
        }
        // Numeric fast path, then the per-type cases.
        if let (Some(x), Some(y)) = (as_num(a), as_num(b)) {
            if !matches!(op, Add | Sub | Mul | Div | FloorDiv | Mod | Pow)
                || matches!((x, y), (Num::I(_), Num::I(_)))
                || matches!(op, Add | Sub | Mul | Div | FloorDiv | Mod | Pow)
            {
                return num_binop(op, x, y, matches!((a, b), (Value::Bool(_), Value::Bool(_))));
            }
        }
        // A `Counter` operand means multiset arithmetic, which this engine does
        // not have — and for `|` the dict arm below would answer a MERGE at
        // exit 0 rather than fail. After the numeric fast path, so ordinary
        // arithmetic never sees it.
        #[cfg(feature = "cap-collections")]
        crate::collections::guard_operand(a, b)?;
        // `p / "x"`, `"x" / p` and `p / q`. Anything else with a path operand
        // falls through to the generic message below, which is CPython's own
        // text for it word for word.
        #[cfg(feature = "cap-pathlib")]
        if let Div = op {
            if let Some(v) = crate::pathlib::truediv(a, b)? {
                return Ok(v);
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
            // `d.keys() | {"c"}` is SET ALGEBRA in CPython — keys and items
            // views are set-like — and it fell through to the generic TypeError
            // here, so a valid program died at exit 1 with a message CPython
            // never prints. The result is a SET whose iteration order this
            // engine refuses to expose anyway, so the whole family refuses as
            // `dict-view`, which the chain escalates to CPython. A VALUES view
            // is not set-like and falls through: the generic message below is
            // CPython's own for it.
            (BitOr | BitAnd | Sub | BitXor, Value::DictView(_, k), Value::Set(_))
            | (BitOr | BitAnd | Sub | BitXor, Value::Set(_), Value::DictView(_, k))
                if *k != "values" =>
            {
                return Err(unsupported(
                    "dict-view",
                    "set algebra over a dict view, whose result is a set with CPython's order",
                ))
            }
            (BitOr | BitAnd | Sub | BitXor, Value::DictView(_, k1), Value::DictView(_, k2))
                if *k1 != "values" && *k2 != "values" =>
            {
                return Err(unsupported(
                    "dict-view",
                    "set algebra over a dict view, whose result is a set with CPython's order",
                ))
            }
            // `bytes % args` is real Python (PEP 461) and is not implemented
            // here. Falling into the arm below made it a TypeError — the
            // program's own exit, which the dispatcher does not treat as a
            // refusal, so a valid `b"%d" % 5` died at exit 1 with nothing to
            // rescue it. A refusal routes it onward instead.
            (Mod, Value::Bytes(_), _) => {
                return Err(unsupported(
                    "percent-format",
                    "bytes % args (PEP 461 formatting)",
                ))
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
            CmpOp::Is => return identity(a, b),
            CmpOp::IsNot => return Ok(!identity(a, b)?),
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
                // `Counter` has multiset containment for `< <= > >=` (3.10+),
                // where a plain dict has no ordering at all — so the TypeError
                // below is CPython's answer for a dict and is not its answer
                // for these.
                #[cfg(feature = "cap-collections")]
                crate::collections::guard_operand(a, b)?;
                return order_cmp(op, a, b);
            }
        })
    }

    pub fn contains(&mut self, container: &Value, needle: &Value) -> R<bool> {
        // `in` compares identity first (`x is y or x == y`), and a NaN is the
        // one value for which that is observable. The rule lives in
        // `value::elem_eq`, once, at the element level — a blanket refusal here
        // used to reject `n in [1, 2]`, which has exactly one answer.
        // `x in p.parents` is a Sequence membership test in CPython, which
        // this value cannot answer without an identity; falling through would
        // be a TypeError at exit 1 for a program that works there.
        #[cfg(feature = "cap-pathlib")]
        crate::pathlib::guard_view(container, "`in`")?;
        // `re.I in re.I | re.M` is `Flag.__contains__`, a subset test CPython
        // answers True; the arm below would raise at exit 1. Refused.
        #[cfg(feature = "cap-re")]
        if let Value::ReFlag(_) = container {
            return Err(crate::re::refuse("`in` over a RegexFlag (Flag.__contains__)"));
        }
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
                // `bool` is a SUBCLASS of int, so `True in b"ab"` is the same
                // byte-value test as `1 in b"ab"` and answers False. Matching
                // only `Value::Int` raised "a bytes-like object is required"
                // for the bool — the same subclass slip the ledger records for
                // `bytes.find(False)`, which was fixed there and not here.
                // `as u8` TRUNCATES, so `300 in b"abc"` tested byte 44 and
                // answered False where CPython raises, and `-1 in b"ab"` tested
                // 255. A byte value outside range(0, 256) is a ValueError there.
                Value::Int(i) => {
                    if !(0..=255).contains(i) {
                        return Err(value_err("byte must be in range(0, 256)"));
                    }
                    b.contains(&(*i as u8))
                }
                Value::Bool(t) => b.contains(&(*t as u8)),
                // An IntFlag is an int here too: `re.I in b"\x02"` is True and
                // `re.A in b"x"` is the ValueError, as for 256.
                #[cfg(feature = "cap-re")]
                Value::ReFlag(f) => {
                    if *f > 255 {
                        return Err(value_err("byte must be in range(0, 256)"));
                    }
                    b.contains(&(*f as u8))
                }
                // ...and the message names the type, as everywhere else.
                other => {
                    return Err(type_err(format!(
                        "a bytes-like object is required, not '{}'",
                        type_name(other)
                    )))
                }
            },
            Value::List(l) => {
                // The borrow is held across the loop rather than snapshotted:
                // `eq` takes no `&mut Interp` and cannot re-enter the evaluator,
                // so nothing can mutate the list underneath it. The snapshot it
                // replaces cost a Vec allocation and a refcount bump per element
                // on every `x in xs`.
                let items = l.borrow();
                // The needle is fixed, so the NaN half of `elem_eq`'s question
                // is hoisted: one bool, tested only on the miss path.
                let needle_nan = crate::value::nan_here(needle);
                for x in items.iter() {
                    if eq(x, needle)? {
                        return Ok(true);
                    }
                    if needle_nan && crate::value::nan_here(x) {
                        return Err(crate::value::refuse_nan_elem());
                    }
                }
                false
            }
            Value::Tuple(t) => {
                let needle_nan = crate::value::nan_here(needle);
                for x in t.iter() {
                    if eq(x, needle)? {
                        return Ok(true);
                    }
                    if needle_nan && crate::value::nan_here(x) {
                        return Err(crate::value::refuse_nan_elem());
                    }
                }
                false
            }
            Value::Dict(d) => d.borrow().contains(needle)?,
            // CPython converts an unhashable SET to a frozenset for the
            // membership test rather than raising: `{1} in {1}` is False, not a
            // TypeError. This subset has no frozenset and its sets cannot hold
            // one, so the answer is always False — which is the answer CPython
            // gives for every set this runtime can build.
            Value::Set(s) => match needle {
                Value::Set(_) => false,
                _ => s.borrow().contains(needle)?,
            },
            Value::Range(a, b, st) => {
                // A RANGE HOLDS INTEGERS, BUT `in` ASKS ABOUT VALUES. CPython
                // compares by equality, so `1.0 in range(5)` and
                // `True in range(5)` are both True — `1.0 == 1` and `True == 1`.
                // Matching only `Value::Int` answered False to both, at exit 0.
                // A non-integral float is still False, which is why the test is
                // on the VALUE and not on the type.
                let want = match needle {
                    Value::Int(i) => Some(*i),
                    Value::Bool(t) => Some(*t as i64),
                    // `re.I in range(5)`: the flag is its int, `re.I == 2`.
                    #[cfg(feature = "cap-re")]
                    Value::ReFlag(f) => Some(*f as i64),
                    Value::Float(f) => {
                        if f.fract() == 0.0 && f.is_finite() && *f >= -(2f64.powi(63)) && *f < 2f64.powi(63) {
                            Some(*f as i64)
                        } else {
                            None
                        }
                    }
                    _ => None,
                };
                match want {
                    None => false,
                    Some(i) => {
                        let inrange =
                            if *st > 0 { i >= *a && i < *b } else { i <= *a && i > *b };
                        inrange && (i - *a).rem_euclid(*st) == 0
                    }
                }
            }
            Value::Gen(_) => {
                let mut it = self.make_iter(container.clone())?;
                while let Some(x) = self.iter_next(&mut it)? {
                    if crate::value::elem_eq(&x, needle)? {
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
        #[cfg(feature = "cap-pathlib")]
        if let Value::Path(s, true) = base {
            return crate::pathlib::view_index(s, crate::eval::int_val(idx)?);
        }
        Ok(match base {
            Value::Dict(d) => {
                // The missing key is where the two `collections` types differ
                // from a dict and from each other: `0` and no insert for a
                // Counter, the factory's value INSERTED for a defaultdict.
                #[cfg(feature = "cap-collections")]
                if let Some(k) = crate::collections::kind_of(d) {
                    return crate::collections::index(d, k, idx);
                }
                match d.borrow().get(idx)? {
                    Some(v) => v,
                    None => return Err(key_err(fmt::repr(idx)?)),
                }
            }
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
                    Value::Str(crate::value::substr(&s[i..i + 1]))
                } else {
                    let chars: Vec<char> = s.chars().collect();
                    let i = norm_index(crate::eval::int_val(idx)?, chars.len(), "string")?;
                    Value::Str(crate::value::char_str(chars[i]))
                }
            }
            Value::Bytes(b) => {
                // The receiver is `bytes`, and CPython names the type in the
                // message: "index out of range" for bytes, not "bytearray index
                // out of range" — which named a type this subset does not even have.
                let i = norm_index(crate::eval::int_val(idx)?, b.len(), "")?;
                Value::Int(b[i] as i64)
            }
            Value::Range(a, bb, st) => {
                let n = range_len(*a, *bb, *st);
                // A range can be longer than i64 can count. CPython indexes one
                // fine — its arithmetic is arbitrary precision — and this cannot,
                // so it refuses rather than truncating the count and answering
                // out of a range that is the wrong size. Before the width fix
                // the count wrapped NEGATIVE and `range(-2**62, 2**62)[0]`
                // raised a spurious IndexError.
                if n > i64::MAX as i128 {
                    return Err(unsupported(
                        "bigint",
                        "index into a range longer than 2**63 - 1, whose length is a bignum",
                    ));
                }
                // CPython says "range object index out of range" here, not "range".
                let i = norm_index(crate::eval::int_val(idx)?, n as usize, "range object")?;
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
            Value::Dict(d) => {
                // Named before the borrow, so an unhashable key that IS this
                // dict still gets its own type name in the TypeError.
                #[cfg(feature = "cap-collections")]
                if crate::collections::kind_of(d).is_some() {
                    crate::collections::self_key_guard(&idx)?;
                }
                d.borrow_mut().insert(idx, v)?
            }
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
                // `del c[missing]` is a NO-OP on a Counter — it overrides
                // `__delitem__` precisely so that it is — and a KeyError on a
                // defaultdict, which does not.
                #[cfg(feature = "cap-collections")]
                if let Some(k) = crate::collections::kind_of(d) {
                    return crate::collections::del_item(d, k, idx);
                }
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
        // CPython answers a slice of `.parents` with a TUPLE; falling through
        // would be a TypeError at exit 1 for a program that works there.
        #[cfg(feature = "cap-pathlib")]
        crate::pathlib::guard_view(base, "a slice")?;
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
            // Slicing a range yields a RANGE, not a list — `range(4)[::-1]` is
            // `range(3, -1, -1)`. Indexing one was already here; slicing fell
            // through to the arm below and raised
            // `'range' object is not subscriptable`, which is exit 1 and
            // therefore the program's own: the dispatcher does not treat it as a
            // refusal, so nothing rescued a construct CPython answers.
            //
            // The picked indices give the answer without a second normalisation
            // to keep in step with `slice_indices`: the first one names the new
            // start, the steps multiply, and the stop is derived from the COUNT
            // so that it holds exactly the elements picked.
            Value::Range(a, b, st) => {
                let n = range_len(*a, *b, *st);
                // Same reason as indexing, and this is where the SIGABRT was:
                // the wrapped negative count reached `slice_span`, whose
                // `clamp(0, n)` panicked on `min > max`.
                if n > i64::MAX as i128 {
                    return Err(unsupported(
                        "bigint",
                        "slice of a range longer than 2**63 - 1, whose length is a bignum",
                    ));
                }
                let (start, stop) = slice_bounds(n as usize, lo, hi, step);
                // Both endpoints map straight through the parent's own start and
                // step. Deriving the stop from a COUNT instead gives a range with
                // the same ELEMENTS and a different repr — `range(4)[::3]` came
                // out as `range(0, 6, 3)` where CPython says `range(0, 4, 3)` —
                // and a range's repr is observable, so same-elements is not
                // good enough.
                // ...and the arithmetic that builds it can overflow too. A
                // range holds three i64s, so a slice whose combined step does
                // not fit one cannot be represented: `range(0, 4, 2)[::2**62]`
                // is `range(0, 4, 9223372036854775808)` in CPython, and
                // `st * step` wrapped to i64::MIN here — a NEGATIVE step, so
                // `list(...)` answered `[]` where CPython answers `[0]`.
                // Checked in i128 and refused, like every other place an i64
                // cannot hold Python's answer.
                let fits = |v: i128| -> R<i64> {
                    i64::try_from(v).map_err(|_| {
                        unsupported(
                            "bigint",
                            "slice of a range whose start, stop or step falls outside 64 bits",
                        )
                    })
                };
                let (a128, st128, step128) = (*a as i128, *st as i128, step as i128);
                Value::Range(
                    fits(a128 + start as i128 * st128)?,
                    fits(a128 + stop as i128 * st128)?,
                    fits(st128 * step128)?,
                )
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
        // `.name`, `.value`, `.bit_length()`, `.real`: CPython answers every
        // one, and an AttributeError here is exit 1, which the chain never
        // retries. Refused, so it reaches CPython one spawn later.
        #[cfg(feature = "cap-re")]
        if let Value::ReFlag(_) = base {
            return Err(crate::re::attr_refused(name));
        }
        // A path's properties are COMPUTED here — `.name`, `.parts`, `.parent`
        // are not methods — and every name this engine does not answer refuses
        // rather than raising AttributeError, for the reason `collections` does
        // the same: CPython answers `.resolve()` and `.glob()`, and an
        // AttributeError is exit 1, which the chain never retries.
        #[cfg(feature = "cap-pathlib")]
        if let Value::Path(s, view) = base {
            return crate::pathlib::get_attr(s, *view, name);
        }
        // `Path.cwd` — a classmethod on the type object.
        #[cfg(feature = "cap-pathlib")]
        if matches!(base, Value::Builtin("Path")) {
            return if name == "cwd" {
                Ok(Value::Bound(Rc::new(Value::Module("pathlib")), "cwd"))
            } else {
                Err(crate::pathlib::refuse(&format!("Path.{name}")))
            };
        }
        // An instance's own attributes, then its class's — and a REFUSAL for
        // every name neither holds that CPython answers off `object`
        // (`__dict__`, `__module__`, `__doc__`). Before the method table,
        // because a class is free to name a method `get` or `items` and the
        // program means its own, not a dict's.
        #[cfg(feature = "cap-class")]
        if let Some(r) = crate::classes::get_attr(base, name) {
            return r;
        }
        if let Some(m) = crate::methods::method_name(base, name) {
            return Ok(Value::Bound(Rc::new(base.clone()), m));
        }
        // Anything else on a Counter or a defaultdict — `.default_factory`,
        // `.elements`, `.subtract`, `.total` — is a refusal and not an
        // AttributeError: CPython answers all four, and an AttributeError is
        // exit 1, the program's own, which the chain never retries.
        #[cfg(feature = "cap-collections")]
        if let Value::Dict(d) = base {
            if let Some(k) = crate::collections::kind_of(d) {
                return Err(crate::collections::attr_refused(k, name));
            }
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
        // `range.start`, `.stop` and `.step` are ordinary attributes CPython
        // exposes, and this raised AttributeError for them — exit 1, the
        // program's own exit, which the chain does not retry, so a program
        // CPython answers simply died.
        if let Value::Range(a, b, st) = base {
            match name {
                "start" => return Ok(Value::Int(*a)),
                "stop" => return Ok(Value::Int(*b)),
                "step" => return Ok(Value::Int(*st)),
                _ => {}
            }
        }
        if let Value::Exc(kind, msg) = base {
            match name {
                // `SystemExit.code` is the exit status, typed — the message is
                // its `str()`, and the constructor kept the two reversible.
                "code" if *kind == "SystemExit" => {
                    return Ok(crate::builtins::system_exit_code(msg))
                }
                "args" if *kind == "SystemExit" => {
                    let a = if msg.is_empty() {
                        vec![]
                    } else {
                        vec![crate::builtins::system_exit_code(msg)]
                    };
                    return Ok(Value::Tuple(Rc::new(a)));
                }
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
        // AN EXCEPTION REALLY DOES HAVE THESE, so reporting "no such attribute"
        // is a claim about Python rather than about this program.
        // `__context__` is whatever was being handled when this one was raised,
        // `__cause__` is what `raise X from Y` attached, `__traceback__` is the
        // frame chain. `Value::Exc` is a flat `(kind, message)` pair with
        // nowhere to put any of them, and widening it reaches every site that
        // matches on an exception -- so this refuses, and the dispatcher hands
        // the program to an interpreter that has them.
        //
        // The distinction matters because AttributeError is exit 1, the
        // program's OWN exit, which the chain does not retry: a handler that
        // inspects `e.__context__` died here instead of being answered one
        // spawn later.
        if matches!(base, Value::Exc(..))
            && matches!(name, "__context__" | "__cause__" | "__traceback__")
        {
            return Err(unsupported(
                "exception-chaining",
                &format!("exception.{name}, which needs the chained exception object"),
            ));
        }
        // ...and the same argument, made once for every dunder instead of three
        // times for three names. A DUNDER IS PART OF THE DATA MODEL: Python
        // defines what `__name__`, `__class__`, `__doc__`, `__dict__` mean and
        // every object CPython builds carries the ones its type declares. So
        // answering `AttributeError` for one is not a fact about this program,
        // it is a claim about Python — and a false one:
        //
        //     print(type(2).__name__)          CPython: int    this: AttributeError
        //     print(e.__class__.__name__)      CPython: ...    this: AttributeError
        //     print(len.__doc__ is not None)   CPython: True   this: AttributeError
        //
        // Three MISMATCHes on the `lypning` arm, measured, and invariant 1 says a
        // MISMATCH is always a bug. Worse than the count: AttributeError is
        // exit 1, the PROGRAM's own exit, which the chain does not retry — so
        // unlike a refusal it cannot be answered one spawn later. The program
        // simply dies.
        //
        // Deliberately a wildcard and not a list of the dunders CPython has.
        // A list is incomplete the moment someone uses the next one, and being
        // incomplete here means a silent wrong answer; being over-broad means a
        // process spawn on `o.__notathing__`, which CPython then raises
        // AttributeError for anyway. That is the asymmetry invariant 1 is
        // about, and it only points one way.
        // ONE dunder is answered, and only on the one receiver where its value
        // is not a guess: a `Value::Builtin` carries its own name, and CPython's
        // `__name__` for both a builtin TYPE and a builtin FUNCTION is exactly
        // that name (`int.__name__ == 'int'`, `len.__name__ == 'len'`). So this
        // is not a partial data model — it is the whole answer for this
        // receiver, which is what the wildcard argument above demands before an
        // exception is carved out of it.
        //
        // It earns its place by routing, not by speed. `type(e).__name__` is the
        // ordinary format-an-exception idiom and appeared in 22 corpus programs;
        // refusing it sent them out of the tier that is CPython-exact. Rerouting
        // them instead was tried first and REVERTED (docs/HILLCLIMB.md, this
        // date): letting them fall to the rung then below (the oracle, out of
        // the chain since 2026-09-04) exposed four programs to defects it has
        // elsewhere, and the block had been shielding them
        // by accident. Answering here keeps them where the answer is right.
        if name == "__name__" {
            if let Value::Builtin(b) = base {
                return Ok(Value::Str((*b).into()));
            }
        }
        if name.starts_with("__") && name.ends_with("__") && name.len() > 4 {
            // TWO KINDS, because the tier below splits exactly here — measured
            // 2026-08-30 on lypning-mp-i386: it answers `__name__` and
            // `__class__` correctly and gets `__module__` and `__doc__` wrong
            // (built-in types carry neither there, so the ordinary
            // format-an-exception idiom prints the getattr DEFAULT at exit 0).
            // `dunder-missing` is in ONLY_CPYTHON_KINDS; `dunder-attr` falls
            // through to the engine that answers it.
            if matches!(name, "__module__" | "__doc__") {
                return Err(unsupported(
                    "dunder-missing",
                    &format!("{}.{name}, which only CPython's builtins carry", type_name(base)),
                ));
            }
            return Err(unsupported(
                "dunder-attr",
                &format!("{}.{name}, which is part of Python's data model", type_name(base)),
            ));
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

fn num_binop(op: BinOp, a: Num, b: Num, both_bool: bool) -> R<Value> {
    use BinOp::*;
    // Bit operations are integer-only in Python.
    if matches!(op, BitAnd | BitOr | BitXor | LShift | RShift) {
        let (Num::I(x), Num::I(y)) = (a, b) else {
            return Err(type_err(format!(
                "unsupported operand type(s) for {}: 'float' and 'float'",
                op_sym(op)
            )));
        };
        // `bool` is a subclass of `int`, but its three bitwise operators are
        // overridden to return `bool` — and ONLY when both operands are bool.
        // `True | False` is `True`, while `True | 1` is `1`. Returning an int
        // for both printed `1` where CPython prints `True`, at exit 0, on an
        // ordinary flag expression. The shifts are not overridden: `True << 1`
        // is `2` either way, so they stay out of this.
        if both_bool && matches!(op, BitAnd | BitOr | BitXor) {
            return Ok(Value::Bool(match op {
                BitAnd => (x != 0) & (y != 0),
                BitOr => (x != 0) | (y != 0),
                _ => (x != 0) ^ (y != 0),
            }));
        }
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
                // `int / int` is CORRECTLY ROUNDED in CPython, computed from the
                // integers themselves. Converting each to f64 first loses the
                // low bits of anything past 2**53, and the error survives the
                // division: `9007199254740993 / 3` answered
                // 3002399751580330.5 where CPython answers 3002399751580331.0,
                // because the numerator had already become …992 before the
                // divide. Refused past the exactly-representable range rather
                // than answered approximately — the same line every other
                // 64-bit-range refusal in this file draws.
                const EXACT: i64 = 1 << 53;
                if x.unsigned_abs() > EXACT as u64 || y.unsigned_abs() > EXACT as u64 {
                    // Its OWN kind, and not `bigint`, because the two ask
                    // different things of the tier below. Every other `bigint`
                    // refusal here means "Python would use a bignum" — a
                    // capability, and MicroPython HAS arbitrary-precision
                    // integers, so falling through gets the right answer. This
                    // one means "the quotient needs rounding I cannot do
                    // exactly", and MicroPython converts both operands to
                    // double exactly as this would have: it answers, and it
                    // answers wrongly. Measured over the corpus the run loaded
                    // (2,239 programs, 2026-08-28): of the programs this file
                    // refuses as `bigint`, MicroPython gets 10 right and this
                    // one wrong. Sharing a kind with them would escalate all
                    // eleven to CPython to rescue one.
                    return Err(unsupported(
                        "int-div-precision",
                        "int / int where an operand is past 2**53 and the quotient needs exact rounding",
                    ));
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
            {
                // There USED to be a guard here: `if !(x / y).is_finite() { NAN }`,
                // put there because `inf // 2.5` is nan in CPython where Rust's
                // `(inf / 2.5).floor()` is inf. It was right about that case and
                // wrong about the one that looks identical from the quotient
                // alone — an OVERFLOW, where both operands are finite and only
                // the quotient is not:
                //
                //     7.0 // 1e-308   ->  inf      (this answered nan)
                //
                // CPython does not test the quotient at all. It computes
                // `fmod` first, and the two cases separate themselves there:
                // `fmod(inf, 2.5)` is nan and poisons everything after it, while
                // `fmod(7.0, 1e-308)` is an ordinary small number and the
                // overflow happens in the division that follows, where inf is
                // the right answer. Rust's `f64::floor` is total — `floor(inf)`
                // is inf, `floor(nan)` is nan — so the code below needs no guard
                // to produce either, and `div - floordiv > 0.5` is false for
                // both (inf - inf and nan - nan are both nan).
                //
                // Measured over a 390-program grid of the overflow
                // neighbourhood: 98 divergences, all of them this, all at exit 0.
                // The guard could not have been found by testing `inf` operands,
                // which is what it was written for.
                //
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
                    // …and then CPython CORRECTS the floor, which this did not.
                    // `(x - mod) / y` is exact in real arithmetic but the
                    // division rounds, so a true quotient of 12 can arrive as
                    // 11.999999999999998 and floor to 11. `float_floor_div` in
                    // `floatobject.c` adds the value back when the fraction it
                    // discarded was more than half a unit:
                    //
                    //     9.0 // 0.7   ->  12.0   (this answered 11.0)
                    //
                    // Exit 0, one off, and only for the divisors where the
                    // rounding lands that way — which is why a handful of
                    // examples would not have found it.
                    let floordiv = div.floor();
                    Value::Float(if div - floordiv > 0.5 { floordiv + 1.0 } else { floordiv })
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

/// `a is b`, refusing the question CPython answers from INTERNING.
///
/// `is` is object identity, and for a mutable value that is a fact this engine
/// has: `[1] is [1]` is False here and in CPython, because two list displays
/// build two objects. For an immutable one it is not a fact about the program
/// at all. CPython folds equal constants in a code object into one object, so
/// `0 is 0`, `'ab' is 'ab'` and `(1,) is (1,)` are True — while
/// `int('1000') is 1000` is False for the same values. The answer depends on
/// where the value CAME FROM, which nothing in a value can tell you.
///
/// Answering False was wrong 30 times in a 5,460-program grid over the
/// comparison operators, and answering True would be wrong for the
/// runtime-built half. `is_same` has carried the comment "refusing beats
/// guessing either way" since it was written; it returned `false`.
///
/// Note what still answers, because refusing more than necessary is its own
/// cost: the singletons (`x is None`, `is True`, `is False`) are identity
/// questions with one possible answer; two values that are not EQUAL are never
/// the same object either; and `x is x` still holds wherever the value carries
/// an `Rc` to compare, which is every str, tuple, list, dict and set.
fn identity(a: &Value, b: &Value) -> R<bool> {
    if is_same(a, b) {
        return Ok(true);
    }
    let foldable = |v: &Value| {
        matches!(
            v,
            Value::Int(_) | Value::Float(_) | Value::Str(_) | Value::Bytes(_) | Value::Tuple(_)
        )
    };
    if foldable(a) && foldable(b) && eq(a, b)? {
        return Err(unsupported(
            "identity",
            "`is` between two equal immutable values, which CPython answers from interning",
        ));
    }
    Ok(false)
}

/// The symbol an ordering operator puts in its TypeError.
fn cmp_symbol(op: CmpOp) -> &'static str {
    match op {
        CmpOp::Lt => "<",
        CmpOp::Le => "<=",
        CmpOp::Gt => ">",
        CmpOp::Ge => ">=",
        _ => "<",
    }
}

/// `a op b` for the four ordering operators — CPython's `list_richcompare`,
/// which is not [`order`] plus a mapping from `Ordering`.
///
/// Two things fall out of the difference, and both were wrong before:
///
/// * **The operator's own name reaches the error, at every depth.** CPython
///   compares a sequence element-wise and then hands the ORIGINAL operator to
///   the first differing pair, so `[1] <= ['a']` reports `'<='`. Deriving the
///   answer from an `Ordering` cannot do that: it has only one comparison to
///   name, and it named `'<'` for all four. 1,461 divergences in a grid over
///   the comparison operators, every one of them the wrong symbol in a message
///   an agent prints with `str(e)`.
///
/// * **A NaN makes an ordering False only between values that are ORDERABLE.**
///   `nan < 1.0` is False because IEEE 754 says the relation does not hold.
///   `'' < nan` is a TypeError, because a str and a float have no ordering to
///   fail. Short-circuiting on `is_nan` before the type check answered False to
///   both, which is 120 more divergences and the more dangerous kind: an
///   exception CPython raises, silently turned into a value.
///
/// [`order`] stays as it is and keeps naming `'<'`, because that is what sort
/// asks and what CPython's sort reports.
pub fn order_cmp(op: CmpOp, a: &Value, b: &Value) -> R<bool> {
    let len_cmp = |x: usize, y: usize| match op {
        CmpOp::Lt => x < y,
        CmpOp::Le => x <= y,
        CmpOp::Gt => x > y,
        _ => x >= y,
    };
    match (a, b) {
        (Value::List(x), Value::List(y)) => {
            let _nest = crate::err::Nest::enter("comparison")?;
            let (x, y) = (x.borrow(), y.borrow());
            for (p, q) in x.iter().zip(y.iter()) {
                if !crate::value::elem_eq(p, q)? {
                    return order_cmp(op, p, q);
                }
            }
            return Ok(len_cmp(x.len(), y.len()));
        }
        (Value::Tuple(x), Value::Tuple(y)) => {
            let _nest = crate::err::Nest::enter("comparison")?;
            for (p, q) in x.iter().zip(y.iter()) {
                if !crate::value::elem_eq(p, q)? {
                    return order_cmp(op, p, q);
                }
            }
            return Ok(len_cmp(x.len(), y.len()));
        }
        _ => {}
    }
    // NaN IS UNORDERED, AND THAT IS AN ANSWER, NOT AN ERROR — between numbers.
    // `nan > 99.0`, `nan < 99.0` and `nan >= nan` are all False in Python
    // because IEEE 754 says the relation does not hold, not because the
    // operands cannot be compared. Equality already behaves correctly through
    // eq(), where `nan == nan` is False for the same reason.
    if (is_nan(a) || is_nan(b)) && as_num(a).is_some() && as_num(b).is_some() {
        return Ok(false);
    }
    let o = order_as(cmp_symbol(op), a, b)?;
    Ok(match op {
        CmpOp::Lt => o == Ordering::Less,
        CmpOp::Le => o != Ordering::Greater,
        CmpOp::Gt => o == Ordering::Greater,
        _ => o != Ordering::Less,
    })
}

/// Python's `<` on values of different types is a TypeError, not a fallback to
/// some arbitrary total order. Reproducing that exactly is what keeps `sorted`
/// on a mixed list from silently succeeding here and failing there.
pub fn order(a: &Value, b: &Value) -> R<Ordering> {
    order_as("<", a, b)
}

fn order_as(sym: &str, a: &Value, b: &Value) -> R<Ordering> {
    // `sorted`, `min`, `max` and `list.sort` reach the comparator HERE rather
    // than through `Interp::cmp`, so a type this engine declines to ORDER has to
    // be caught here too. Without it the generic path raised a TypeError at exit
    // 1 where CPython answers — a wrong exit code instead of a refusal the
    // dispatcher can act on. (`sorted([Counter('ab'), Counter('a')])` is
    // multiset containment in CPython 3.10+.)
    #[cfg(feature = "cap-collections")]
    crate::collections::guard_operand(a, b)?;
    // Two paths order by their SPLIT strings, and only when they share a root
    // — `pathlib.rs` trap 2 has the measurement. A path against anything else
    // falls to the TypeError below, which is CPython's answer for it.
    #[cfg(feature = "cap-pathlib")]
    if let Some(o) = crate::pathlib::order(a, b)? {
        return Ok(o);
    }
    // The numeric and scalar paths run BEFORE the guard, because neither can
    // descend and `sorted()` of a list of ints reaches this once per
    // comparison. See `value::eq` for the same split and the same reasoning.
    if let (Some(x), Some(y)) = (as_num(a), as_num(b)) {
        let (x, y) = (fl(x), fl(y));
        if let (Num::I(i), Num::I(j)) = (as_num(a).unwrap(), as_num(b).unwrap()) {
            return Ok(i.cmp(&j));
        }
        // A NaN COMPARES FALSE TO EVERYTHING, and sort only ever asks `b < a`,
        // so "neither less nor greater" is what reproduces CPython here:
        // `sorted([nan, 1.0])` is `[nan, 1.0]` and `max(nan, 1.0)` is `nan`,
        // because in both cases the test that would move the NaN is False.
        // Raising TypeError("cannot order NaN") instead turned three values
        // CPython computes into exceptions — sorted(), min() and max() over any
        // list containing one.
        return Ok(x.partial_cmp(&y).unwrap_or(Ordering::Equal));
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
            seq_order(sym, &x, &y)?
        }
        (Value::Tuple(x), Value::Tuple(y)) => {
            let _nest = crate::err::Nest::enter("comparison")?;
            seq_order(sym, x, y)?
        }
        _ => {
            return Err(type_err(format!(
                "'{sym}' not supported between instances of '{}' and '{}'",
                type_name(a),
                type_name(b)
            )))
        }
    })
}

fn seq_order(sym: &str, x: &[Value], y: &[Value]) -> R<Ordering> {
    for (a, b) in x.iter().zip(y.iter()) {
        if !crate::value::elem_eq(a, b)? {
            return order_as(sym, a, b);
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
    // `reverse=True` is NOT "sort, then reverse". Reversing the finished order
    // reverses the ties along with everything else, and Python guarantees a sort
    // that leaves equal elements in the order it found them — descending
    // included. So `sorted(counts, key=counts.get, reverse=True)` must keep `b`
    // ahead of `a` when both count 2, and reversing the result puts `a` first.
    //
    // CPython reverses the input, sorts ascending, and reverses again
    // (`listobject.c`, `reverse_slice` either side of the merge). The second
    // reversal undoes the first for equal elements and inverts everything else,
    // which is the whole trick. Doing it here rather than after the merge costs
    // one extra `reverse` of an index vector.
    if reverse {
        idx.reverse();
    }
    let mut buf = vec![0usize; n];
    let mut width = 1;
    while width < n {
        let mut i = 0;
        while i < n {
            let mid = (i + width).min(n);
            let end = (i + 2 * width).min(n);
            let (mut l, mut r, mut k) = (i, mid, i);
            while l < mid && r < end {
                // A NaN MAKES THE COMPARATOR STOP BEING AN ORDER, and a sort
                // over one is then the ALGORITHM's answer rather than Python's.
                // Every comparison against a NaN is false, so "not less" holds
                // in both directions and which element moves depends entirely
                // on the order the sort asks its questions in. CPython's answer
                // is timsort's:
                //
                //     sorted([3, 1, float('nan'), 2])
                //     CPython  [1, 2, 3, nan]      this merge sort  [1, 3, nan, 2]
                //
                // Both are stable and deterministic, and they disagree because
                // the two algorithms differ — which no amount of fixing the
                // comparison can close. `min` and `max` are unaffected and stay
                // on `order`: they are linear scans asking one question per
                // element, so "neither less nor greater" gives CPython's answer
                // exactly.
                //
                // The guard is `has_nan`, which sees one level into a list or
                // tuple key and no deeper — the same reach as the guard on `in`,
                // and for the same reason (a recursive scan overflowed the host
                // stack when it was tried).
                if crate::value::has_nan(&keys[idx[r]]) || crate::value::has_nan(&keys[idx[l]]) {
                    return Err(unsupported(
                        "nan-order",
                        "sort over a NaN, whose comparisons are all false — so the result \
                         is the sort algorithm's and not Python's",
                    ));
                }
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
    // (see the note above the first `reverse`: this is the second half of the
    // pair, not a lone post-pass)
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
        // An empty `what` means the caller has no type name to offer, and
        // CPython does not invent one: `b"abcd"[9]` is "index out of range".
        // Formatting it in unconditionally left a leading space.
        return Err(index_err(if what.is_empty() {
            "index out of range".to_string()
        } else {
            format!("{what} index out of range")
        }));
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

/// The normalised `(start, stop)` a slice names, in index space.
///
/// Split out of [`slice_indices`] so the range arm of `slice` can build a range
/// from the same numbers the gather path uses. Two normalisations of the same
/// rule would be two things to keep in step, and this rule already has a grid of
/// 10,990 cells behind it.
pub fn slice_bounds(n: usize, lo: Option<i64>, hi: Option<i64>, step: i64) -> (i64, i64) {
    let n = n as i64;
    let clamp = |v: i64, lodef: i64, hidef: i64| -> i64 {
        let v = if v < 0 { n + v } else { v };
        v.clamp(lodef, hidef)
    };
    if step > 0 {
        (lo.map_or(0, |v| clamp(v, 0, n)), hi.map_or(n, |v| clamp(v, 0, n)))
    } else {
        (
            lo.map_or(n - 1, |v| clamp(v, -1, n - 1)),
            hi.map_or(-1, |v| clamp(v, -1, n - 1)),
        )
    }
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
    // Borrowed, never cloned: the values are only ever read through `get`, so
    // copying the tuple bought one Vec and one refcount bump per element per
    // call on an allocator where the allocation count is the lever.
    let args: &[Value] = match arg {
        Value::Tuple(t) => t,
        other => std::slice::from_ref(other),
    };
    let b = f.as_bytes();
    // Reserved past the growth cliff: `f.len()` under-reserves whenever any
    // conversion expands (the normal case), so nearly every call paid a
    // mallocng grow-copy-free. Eight bytes per conversion covers every i64 an
    // agent one-liner prints; an overshoot inside the same size class is free.
    let pct = b.iter().filter(|&&c| c == b'%').count();
    let mut out = String::with_capacity(f.len() + 8 * pct);
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
        // The two bare conversions agent programs actually type, written into
        // `out` directly instead of through `percent_one`'s temporary String —
        // one malloc, one memcpy and one free per conversion, gone. Anything
        // with a width, a flag or another type takes the full path unchanged.
        match (spec.as_str(), v) {
            ("s", Value::Str(sv)) => out.push_str(sv),
            ("d", Value::Int(n)) if min_digits == 0 => {
                use std::fmt::Write;
                let _ = write!(out, "{n}");
            }
            _ => out.push_str(&percent_one(v, &spec, min_digits)?),
        }
    }
    // The leftover-argument check is skipped for anything CPython considers a
    // MAPPING in the C sense — `PyMapping_Check`, which is true for dict, list
    // and bytes because each has `mp_subscript`, and false for int, str, tuple
    // and None. So `'ab' % [1]` is `'ab'` while `'ab' % 5` and `'ab' % (1,)`
    // both raise. Only `Dict` was exempt here, so the two ordinary sequence
    // cases raised where CPython answers.
    if ai < args.len()
        && !matches!(arg, Value::Dict(_) | Value::List(_) | Value::Bytes(_))
    {
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
    // A flag under `%`: the `s` and `r` conversions are `str()`/`repr()` and
    // then padding — `'%s' % re.I` and `'%-20s' % re.I` print `re.IGNORECASE`
    // on every CPython, and the two arms below do exactly that — so they pass.
    // The numeric conversions (`%d`, `%x`, `%c`, …) are refused with the
    // format specs (re.rs, trap 2). `spec` is already `read_spec`'s
    // translation (`%s` arrives as `>s`), so the conversion is its last byte.
    #[cfg(feature = "cap-re")]
    if let Value::ReFlag(_) = v {
        if !(spec.ends_with('s') || spec.ends_with('r')) {
            return Err(crate::re::spec_refused());
        }
    }
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
                return fmt::format_value_pct(v, &as_str);
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
        return fmt::format_value_pct(&Value::Str(s.into()), &format!("{rest}s"));
    }
    if spec.ends_with('s') {
        let s = fmt::to_str(v)?;
        return fmt::format_value_pct(&Value::Str(s.into()), spec);
    }
    fmt::format_value_pct(v, spec)
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
