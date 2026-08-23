//! Builtin functions.
//!
//! The set is chosen from the corpus, not from the Python manual: `print`
//! appears in 93.5% of harvested one-liners, `open` in 42.7%, `len` in 10.4%.
//! Anything not here is `unsupported: builtin`, which is a routing signal, not
//! a failure — see `route.rs`.

use crate::args::Args;
use crate::err::*;
use crate::eval::{int_val, Interp};
use crate::fmt;
use crate::io as mio;
use crate::iter::{decode_utf8, Iter};
use crate::ops;
use crate::value::*;
use std::cell::RefCell;
use std::rc::Rc;

/// Every name resolvable as a builtin. Kept as one table so `route.rs` can ask
/// "would lypning know this name?" without executing anything.
pub const BUILTINS: &[&str] = &[
    "abs", "all", "any", "bin", "bool", "bytes", "chr", "dict", "divmod", "enumerate", "filter",
    "float", "format", "hex", "input", "int", "isinstance", "iter", "len", "list", "map", "max",
    "min", "next", "oct", "open", "ord", "print", "range", "repr", "reversed", "round", "set",
    "sorted", "str", "sum", "tuple", "type", "zip",
];

/// f64 -> i64 the way CPython converts, refusing where it cannot.
///
/// `as i64` SATURATES in Rust: `1e308 as i64` is i64::MAX and `f64::INFINITY as
/// i64` is i64::MAX, so `int(1e308)` answered 9223372036854775807 and
/// `round(1e100)` answered the same — both at exit 0, both wrong, and both in
/// exactly the place docs/LYPNING.md §5 promises an `unsupported: bigint` refusal.
/// The promise was kept for arithmetic (every op is checked) and quietly broken
/// on the two conversions. scripts/lypning-fuzz.mjs found it in its first 120
/// probes.
///
/// The three outcomes are CPython's, not an approximation of them: NaN is a
/// ValueError, an infinity is an OverflowError, and a finite value too large
/// for i64 is a value Python WOULD represent — so it is the bigint refusal, and
/// the dispatcher hands the program to an interpreter that has bignums.
pub fn float_to_int(f: f64, what: &str) -> R<i64> {
    if f.is_nan() {
        return Err(value_err(format!("cannot convert float NaN to integer")));
    }
    if f.is_infinite() {
        return Err(overflow_err("cannot convert float infinity to integer"));
    }
    // 2^63 exactly; f64 cannot represent i64::MAX, so comparing against the
    // power of two is the only correct bound.
    if f >= 9223372036854775808.0 || f < -9223372036854775808.0 {
        return Err(unsupported(
            "bigint",
            &format!("{what}() of a float beyond 64-bit range (Python would use a bignum)"),
        ));
    }
    Ok(f as i64)
}

pub const EXCEPTIONS: &[&str] = &[
    "ArithmeticError",
    "AssertionError",
    "AttributeError",
    "BaseException",
    "Exception",
    "FileExistsError",
    "FileNotFoundError",
    "IndexError",
    "KeyError",
    "LookupError",
    "NameError",
    "NotImplementedError",
    "OverflowError",
    "OSError",
    "IOError",
    "PermissionError",
    "RuntimeError",
    "StopIteration",
    "SystemExit",
    "TypeError",
    "UnboundLocalError",
    "UnicodeDecodeError",
    "ValueError",
    "ZeroDivisionError",
];

pub fn is_exception_name(n: &str) -> bool {
    EXCEPTIONS.contains(&n)
}

pub fn exception_static(n: &str) -> &'static str {
    EXCEPTIONS.iter().find(|e| **e == n).copied().unwrap_or("Exception")
}

pub fn builtin(name: &str) -> Option<Value> {
    if let Some(b) = BUILTINS.iter().find(|b| **b == name) {
        return Some(Value::Builtin(b));
    }
    if let Some(e) = EXCEPTIONS.iter().find(|b| **b == name) {
        return Some(Value::Builtin(e));
    }
    match name {
        "True" => Some(Value::Bool(true)),
        "False" => Some(Value::Bool(false)),
        "None" => Some(Value::None),
        "__name__" => Some(Value::Str("__main__".into())),
        _ => None,
    }
}

fn kwget(kw: &[(Rc<str>, Value)], name: &str) -> Option<Value> {
    kw.iter().find(|(k, _)| k.as_ref() == name).map(|(_, v)| v.clone())
}

fn no_kw(name: &str, kw: &[(Rc<str>, Value)]) -> R<()> {
    match kw.first() {
        None => Ok(()),
        Some((k, _)) => Err(type_err(format!(
            "{name}() takes no keyword arguments (got '{k}')"
        ))),
    }
}

pub fn call_builtin(
    it: &mut Interp,
    name: &str,
    args: &mut Args,
    kw: Vec<(Rc<str>, Value)>,
) -> R<Value> {
    // `raise ValueError("x")` / `except E as e` construct exception instances.
    if is_exception_name(name) {
        let msg = match args.first() {
            Some(v) => fmt::to_str(v)?,
            None => String::new(),
        };
        return Ok(Value::Exc(exception_static(name), msg.into()));
    }
    Ok(match name {
        "print" => {
            let sep = match kwget(&kw, "sep") {
                Some(Value::None) | None => " ".to_string(),
                Some(v) => fmt::to_str(&v)?,
            };
            let end = match kwget(&kw, "end") {
                Some(Value::None) | None => "\n".to_string(),
                Some(v) => fmt::to_str(&v)?,
            };
            let to_err = match kwget(&kw, "file") {
                Some(Value::Module("sys.stderr")) => true,
                Some(Value::Module("sys.stdout")) | None => false,
                Some(other) => {
                    return Err(unsupported(
                        "print-file",
                        &format!("print(file=…) to a {}", type_name(&other)),
                    ))
                }
            };
            for (k, _) in &kw {
                if !matches!(k.as_ref(), "sep" | "end" | "file" | "flush") {
                    return Err(type_err(format!(
                        "'{k}' is an invalid keyword argument for print()"
                    )));
                }
            }
            let mut out = String::new();
            for (i, a) in args.iter().enumerate() {
                if i > 0 {
                    out.push_str(&sep);
                }
                out.push_str(&fmt::to_str(a)?);
            }
            out.push_str(&end);
            if to_err {
                mio::write_err(out.as_bytes())?;
            } else {
                mio::write_out(out.as_bytes())?;
            }
            Value::None
        }
        "len" => {
            no_kw("len", &kw)?;
            let v = arg1(name, &args)?;
            Value::Int(length(&v)? as i64)
        }
        "repr" => Value::Str(fmt::repr_rc(&arg1(name, &args)?)?),
        "str" => match args.first() {
            None => Value::Str("".into()),
            Some(v) => {
                if let (Value::Bytes(b), Some(_)) = (v, kwget(&kw, "encoding")) {
                    Value::Str(decode_utf8(b)?.into())
                } else if let (Value::Bytes(_), Some(_)) = (v, args.get(1)) {
                    let Value::Bytes(b) = v else { unreachable!() };
                    Value::Str(decode_utf8(b)?.into())
                } else {
                    Value::Str(fmt::to_rc(v)?)
                }
            }
        },
        "int" => {
            let base = match args.get(1) {
                Some(v) => int_val(v)?,
                None => match kwget(&kw, "base") {
                    Some(v) => int_val(&v)?,
                    None => 10,
                },
            };
            match args.first() {
                None => Value::Int(0),
                Some(Value::Str(s)) => {
                    let t = s.trim();
                    let (t, neg) = match t.strip_prefix('-') {
                        Some(r) => (r, true),
                        None => (t.strip_prefix('+').unwrap_or(t), false),
                    };
                    let t2 = if base == 16 {
                        t.strip_prefix("0x").or_else(|| t.strip_prefix("0X")).unwrap_or(t)
                    } else if base == 8 {
                        t.strip_prefix("0o").or_else(|| t.strip_prefix("0O")).unwrap_or(t)
                    } else if base == 2 {
                        t.strip_prefix("0b").or_else(|| t.strip_prefix("0B")).unwrap_or(t)
                    } else {
                        t
                    };
                    let cleaned: String = t2.chars().filter(|c| *c != '_').collect();
                    match i64::from_str_radix(&cleaned, base as u32) {
                        Ok(v) => Value::Int(if neg { -v } else { v }),
                        Err(e) if cleaned.len() > 18 && !cleaned.is_empty()
                            && cleaned.chars().all(|c| c.is_digit(base as u32)) =>
                        {
                            let _ = e;
                            return Err(unsupported("bigint", "int() result beyond 64-bit range"));
                        }
                        Err(_) => {
                            return Err(value_err(format!(
                                "invalid literal for int() with base {base}: {}",
                                fmt::str_repr(s)?
                            )))
                        }
                    }
                }
                Some(Value::Float(f)) => Value::Int(float_to_int(*f, "int")?),
                Some(Value::Int(i)) => Value::Int(*i),
                Some(Value::Bool(b)) => Value::Int(*b as i64),
                Some(Value::Bytes(b)) => {
                    let s = decode_utf8(b)?;
                    return call_builtin(it, "int", &mut Args::one(Value::Str(s.into())), kw);
                }
                Some(other) => {
                    return Err(type_err(format!(
                        "int() argument must be a string or a number, not '{}'",
                        type_name(other)
                    )))
                }
            }
        }
        "float" => match args.first() {
            None => Value::Float(0.0),
            Some(Value::Str(s)) => {
                let t = s.trim();
                let lower = t.to_ascii_lowercase();
                match lower.as_str() {
                    "inf" | "+inf" | "infinity" | "+infinity" => Value::Float(f64::INFINITY),
                    "-inf" | "-infinity" => Value::Float(f64::NEG_INFINITY),
                    "nan" | "+nan" | "-nan" => Value::Float(f64::NAN),
                    _ => match t.replace('_', "").parse::<f64>() {
                        Ok(v) => Value::Float(v),
                        Err(_) => {
                            return Err(value_err(format!(
                                "could not convert string to float: {}",
                                fmt::str_repr(s)?
                            )))
                        }
                    },
                }
            }
            Some(Value::Int(i)) => Value::Float(*i as f64),
            Some(Value::Bool(b)) => Value::Float(*b as i64 as f64),
            Some(Value::Float(f)) => Value::Float(*f),
            Some(other) => {
                return Err(type_err(format!(
                    "float() argument must be a string or a real number, not '{}'",
                    type_name(other)
                )))
            }
        },
        "bool" => Value::Bool(match args.first() {
            None => false,
            Some(v) => truthy(v)?,
        }),
        "list" => match args.first() {
            None => list(Vec::new()),
            Some(v) => list(it.iter_collect(v.clone())?),
        },
        "tuple" => match args.first() {
            None => Value::Tuple(Rc::new(Vec::new())),
            Some(v) => Value::Tuple(Rc::new(it.iter_collect(v.clone())?)),
        },
        "set" => {
            let mut s = Set::new();
            if let Some(v) = args.first() {
                for x in it.collect_unordered(v.clone())? {
                    s.add(x)?;
                }
            }
            Value::Set(Rc::new(RefCell::new(s)))
        }
        "dict" => {
            let mut d = Dict::new();
            if let Some(v) = args.first() {
                match v {
                    Value::Dict(src) => {
                        let pairs: Vec<(Value, Value)> =
                            src.borrow().iter().map(|(k, v)| (k.clone(), v.clone())).collect();
                        for (k, v) in pairs {
                            d.insert(k, v)?;
                        }
                    }
                    other => {
                        for pair in it.iter_collect(other.clone())? {
                            let kv = it.iter_collect(pair)?;
                            if kv.len() != 2 {
                                return Err(value_err(
                                    "dictionary update sequence element has length != 2",
                                ));
                            }
                            d.insert(kv[0].clone(), kv[1].clone())?;
                        }
                    }
                }
            }
            for (k, v) in kw {
                d.insert(Value::Str(k), v)?;
            }
            Value::Dict(Rc::new(RefCell::new(d)))
        }
        "range" => {
            no_kw("range", &kw)?;
            let n: Vec<i64> = args.iter().map(int_val).collect::<R<Vec<_>>>()?;
            match n.len() {
                1 => Value::Range(0, n[0], 1),
                2 => Value::Range(n[0], n[1], 1),
                3 => {
                    if n[2] == 0 {
                        return Err(value_err("range() arg 3 must not be zero"));
                    }
                    Value::Range(n[0], n[1], n[2])
                }
                _ => return Err(type_err("range expected 1 to 3 arguments")),
            }
        }
        "sum" => {
            let start = args.get(1).cloned().unwrap_or(Value::Int(0));
            // A set is fine here only because `sum` over floats is
            // order-sensitive; ints are not, so refuse the risky half only.
            let items = match args.first() {
                Some(Value::Set(s)) => {
                    let v = s.borrow().items.clone();
                    if v.iter().any(|x| matches!(x, Value::Float(_))) {
                        return Err(set_order_refused("sum() of a set of floats"));
                    }
                    v
                }
                Some(v) => it.iter_collect(v.clone())?,
                None => return Err(type_err("sum() missing 1 required positional argument")),
            };
            let mut acc = start;
            for x in items {
                acc = it.binop(crate::ast::BinOp::Add, &acc, &x)?;
            }
            acc
        }
        "min" | "max" => {
            let want_max = name == "max";
            let items: Vec<Value> = if args.len() == 1 {
                it.collect_unordered(args.remove(0))?
            } else {
                args.to_vec()
            };
            let keyf = kwget(&kw, "key");
            let default = kwget(&kw, "default");
            if items.is_empty() {
                return match default {
                    Some(d) => Ok(d),
                    None => Err(value_err(format!("{name}() arg is an empty sequence"))),
                };
            }
            let mut best = items[0].clone();
            let mut bestk = keyed(it, &keyf, &best)?;
            for x in &items[1..] {
                let k = keyed(it, &keyf, x)?;
                let o = ops::order(&k, &bestk)?;
                // Ties keep the FIRST element, matching CPython.
                if (want_max && o == std::cmp::Ordering::Greater)
                    || (!want_max && o == std::cmp::Ordering::Less)
                {
                    best = x.clone();
                    bestk = k;
                }
            }
            best
        }
        "sorted" => {
            let v = args
                .first()
                .cloned()
                .ok_or_else(|| type_err("sorted expected 1 argument, got 0"))?;
            let mut items = it.collect_unordered(v)?;
            let keyf = kwget(&kw, "key");
            let rev = match kwget(&kw, "reverse") {
                Some(v) => truthy(&v)?,
                None => false,
            };
            let mut keys = Vec::with_capacity(items.len());
            for x in &items {
                keys.push(keyed(it, &keyf, x)?);
            }
            ops::sort_values(&mut items, &mut keys, rev)?;
            list(items)
        }
        "abs" => match arg1(name, &args)? {
            Value::Int(i) => Value::Int(i.checked_abs().ok_or_else(|| {
                unsupported("bigint", "abs() result beyond 64-bit range")
            })?),
            Value::Bool(b) => Value::Int(b as i64),
            Value::Float(f) => Value::Float(f.abs()),
            other => {
                return Err(type_err(format!(
                    "bad operand type for abs(): '{}'",
                    type_name(&other)
                )))
            }
        },
        "round" => {
            let v = arg1(name, &args)?;
            let nd = match args.get(1) {
                Some(x) => Some(int_val(x)?),
                None => match kwget(&kw, "ndigits") {
                    Some(x) => Some(int_val(&x)?),
                    None => None,
                },
            };
            match (&v, nd) {
                (Value::Int(i), None) => Value::Int(*i),
                (Value::Int(i), Some(n)) if n >= 0 => Value::Int(*i),
                (Value::Float(f), None) => Value::Int(float_to_int(round_half_even(*f, 0), "round")?),
                (Value::Float(f), Some(n)) => Value::Float(round_half_even(*f, n)),
                (Value::Bool(b), _) => Value::Int(*b as i64),
                _ => {
                    return Err(unsupported(
                        "round",
                        "round() of this argument combination",
                    ))
                }
            }
        }
        "divmod" => {
            let (a, b) = (arg1(name, &args)?, args.get(1).cloned().unwrap_or(Value::None));
            let q = it.binop(crate::ast::BinOp::FloorDiv, &a, &b)?;
            let r = it.binop(crate::ast::BinOp::Mod, &a, &b)?;
            Value::Tuple(Rc::new(vec![q, r]))
        }
        "any" | "all" => {
            let want_all = name == "all";
            let v = arg1(name, &args)?;
            // Short-circuits, which is why the iterator is driven rather than
            // materialised: `any(1/x for x in [1,0])` must not divide by zero.
            let mut iter = match &v {
                Value::Set(s) => Iter::Vec(s.borrow().items.clone(), 0),
                other => it.make_iter(other.clone())?,
            };
            let mut result = want_all;
            while let Some(x) = it.iter_next(&mut iter)? {
                let t = truthy(&x)?;
                if t != want_all {
                    result = t;
                    break;
                }
            }
            Value::Bool(result)
        }
        "enumerate" => {
            let v = arg1(name, &args)?;
            let start = match args.get(1) {
                Some(x) => int_val(x)?,
                None => match kwget(&kw, "start") {
                    Some(x) => int_val(&x)?,
                    None => 0,
                },
            };
            let inner = it.make_iter(v)?;
            Value::IterObj(
                Rc::new(RefCell::new(Iter::Enumerate(Box::new(inner), start))),
                "enumerate",
            )
        }
        "zip" => {
            let mut its = Vec::with_capacity(args.len());
            for i in 0..args.len() {
                let a = args.take(i);
                its.push(it.make_iter(a)?);
            }
            Value::IterObj(Rc::new(RefCell::new(Iter::Zip(its))), "zip")
        }
        "map" => {
            if args.is_empty() {
                return Err(type_err("map() must have at least two arguments."));
            }
            let f = args.remove(0);
            let mut its = Vec::with_capacity(args.len());
            for i in 0..args.len() {
                let a = args.take(i);
                its.push(it.make_iter(a)?);
            }
            Value::IterObj(Rc::new(RefCell::new(Iter::Map(f, its))), "map")
        }
        "filter" => {
            let f = args
                .first()
                .cloned()
                .ok_or_else(|| type_err("filter expected 2 arguments, got 0"))?;
            let v = args
                .get(1)
                .cloned()
                .ok_or_else(|| type_err("filter expected 2 arguments, got 1"))?;
            let inner = it.make_iter(v)?;
            let pred = if matches!(f, Value::None) { None } else { Some(f) };
            Value::IterObj(
                Rc::new(RefCell::new(Iter::Filter(pred, Box::new(inner)))),
                "filter",
            )
        }
        "reversed" => {
            let v = arg1(name, &args)?;
            if matches!(v, Value::Set(_)) {
                return Err(set_order_refused("reversed() of a set"));
            }
            let mut items = it.iter_collect(v)?;
            items.reverse();
            Value::IterObj(
                Rc::new(RefCell::new(Iter::Vec(items, 0))),
                "list_reverseiterator",
            )
        }
        "iter" => {
            let v = arg1(name, &args)?;
            if let Value::IterObj(..) = v {
                return Ok(v);
            }
            let inner = it.make_iter(v)?;
            Value::IterObj(Rc::new(RefCell::new(inner)), "iterator")
        }
        "next" => {
            let v = arg1(name, &args)?;
            let mut i = match &v {
                Value::IterObj(inner, _) => Iter::Shared(inner.clone()),
                Value::Gen(g) => Iter::Gen(g.clone()),
                other => {
                    return Err(type_err(format!(
                        "'{}' object is not an iterator",
                        type_name(other)
                    )))
                }
            };
            match it.iter_next(&mut i)? {
                Some(v) => v,
                None => match args.get(1) {
                    Some(d) => d.clone(),
                    None => return Err(LypningError::exc("StopIteration", "")),
                },
            }
        }
        "ord" => {
            let v = arg1(name, &args)?;
            match &v {
                Value::Str(s) => {
                    let mut c = s.chars();
                    match (c.next(), c.next()) {
                        (Some(ch), None) => Value::Int(ch as i64),
                        _ => {
                            return Err(type_err(format!(
                                "ord() expected a character, but string of length {} found",
                                s.chars().count()
                            )))
                        }
                    }
                }
                Value::Bytes(b) if b.len() == 1 => Value::Int(b[0] as i64),
                _ => return Err(type_err("ord() expected string of length 1")),
            }
        }
        "chr" => {
            let n = int_val(&arg1(name, &args)?)?;
            match u32::try_from(n).ok().and_then(char::from_u32) {
                Some(c) => Value::Str(c.to_string().into()),
                None => return Err(value_err("chr() arg not in range(0x110000)")),
            }
        }
        "hex" | "oct" | "bin" => {
            let n = int_val(&arg1(name, &args)?)?;
            let (pfx, body) = match name {
                "hex" => ("0x", format!("{:x}", n.unsigned_abs())),
                "oct" => ("0o", format!("{:o}", n.unsigned_abs())),
                _ => ("0b", format!("{:b}", n.unsigned_abs())),
            };
            Value::Str(format!("{}{pfx}{body}", if n < 0 { "-" } else { "" }).into())
        }
        "format" => {
            let v = arg1(name, &args)?;
            let spec = match args.get(1) {
                Some(s) => fmt::to_str(s)?,
                None => String::new(),
            };
            Value::Str(fmt::format_value(&v, &spec)?.into())
        }
        "type" => {
            let v = arg1(name, &args)?;
            Value::Builtin(match type_name(&v) {
                "int" => "int",
                "str" => "str",
                "float" => "float",
                "bool" => "bool",
                "list" => "list",
                "dict" => "dict",
                "set" => "set",
                "tuple" => "tuple",
                "bytes" => "bytes",
                other => {
                    return Err(unsupported(
                        "type",
                        &format!("type() of a {other}"),
                    ))
                }
            })
        }
        "isinstance" => {
            let v = arg1(name, &args)?;
            let cls = args
                .get(1)
                .cloned()
                .ok_or_else(|| type_err("isinstance expected 2 arguments, got 1"))?;
            // `&'static str`, not `String`: these come out of `Value::Builtin`,
            // which already interns them, and building a `String` per class was
            // an allocation for a comparison.
            let names: Vec<&'static str> = match &cls {
                Value::Tuple(t) => t
                    .iter()
                    .map(|c| match c {
                        Value::Builtin(b) => Ok(*b),
                        other => Err(type_err(format!(
                            "isinstance() arg 2 must be a type, not {}",
                            type_name(other)
                        ))),
                    })
                    .collect::<R<Vec<_>>>()?,
                Value::Builtin(b) => vec![*b],
                other => {
                    return Err(type_err(format!(
                        "isinstance() arg 2 must be a type, not {}",
                        type_name(other)
                    )))
                }
            };
            // `isinstance(x, type)` asks whether x is a CLASS. lypning has no
            // class objects of its own and `Value::Builtin` is both `int` and
            // `print`, so answering would mean guessing which builtins are
            // types. Refused instead: a refusal costs one spawn and CPython
            // answers, and this is exactly the trade invariant 1 describes.
            if names.contains(&"type") {
                return Err(unsupported("isinstance", "isinstance() against `type`"));
            }
            let t = type_name(&v);
            Value::Bool(names.iter().any(|n| {
                // An exception instance is matched through the SAME hierarchy
                // table `except` uses, not by its type name. `type_name` of any
                // `Exc` is the literal string "Exception", so comparing against
                // it answered False for `isinstance(ValueError('b'),
                // ValueError)` and True for `isinstance(SystemExit(),
                // Exception)` — both at exit 0, both wrong, and neither visible
                // to `conformance` because no corpus entry did it yet. One
                // table, so this can never disagree with an `except` clause.
                if let Value::Exc(kind, _) = &v {
                    return crate::eval::exc_matches(n, kind);
                }
                *n == t
                    // bool is a subclass of int in Python; str/bytes are not
                    // related, and neither are list/tuple.
                    || (*n == "int" && t == "bool")
                    || (*n == "float" && matches!(t, "float"))
            }))
        }
        "open" => {
            let path = match args.first() {
                Some(Value::Str(s)) => s.to_string(),
                Some(other) => fmt::to_str(other)?,
                None => return Err(type_err("open() missing required argument: 'file'")),
            };
            let mode = match args.get(1).cloned().or_else(|| kwget(&kw, "mode")) {
                Some(v) => fmt::to_str(&v)?,
                None => "r".to_string(),
            };
            open_value(&path, &mode, &kw)?
        }
        "input" => {
            if let Some(p) = args.first() {
                mio::write_out(fmt::to_str(p)?.as_bytes())?;
            }
            match mio::stdin_line()? {
                Some(b) => {
                    let s = crate::iter::decode_text(
                        &b,
                        "non-UTF-8 bytes on stdin (CPython decodes it with surrogateescape)",
                    )?;
                    Value::Str(s.trim_end_matches('\n').trim_end_matches('\r').into())
                }
                None => return Err(LypningError::exc("EOFError", "EOF when reading a line")),
            }
        }
        "bytes" => match args.first() {
            None => Value::Bytes(Rc::new(Vec::new())),
            Some(Value::Str(s)) => Value::Bytes(Rc::new(s.as_bytes().to_vec())),
            Some(Value::Bytes(b)) => Value::Bytes(b.clone()),
            Some(Value::Int(n)) => Value::Bytes(Rc::new(vec![0u8; (*n).max(0) as usize])),
            Some(other) => {
                let items = it.iter_collect(other.clone())?;
                let mut out = Vec::with_capacity(items.len());
                for x in items {
                    // `as u8` TRUNCATES in Rust, so bytes([2**62]) was b"\x00"
                    // and bytes([300]) was b"\x2c" — silent data corruption at
                    // exit 0 where CPython raises. Found by scripts/lypning-fuzz.mjs.
                    let n = int_val(&x)?;
                    if !(0..=255).contains(&n) {
                        return Err(value_err("bytes must be in range(0, 256)"));
                    }
                    out.push(n as u8);
                }
                Value::Bytes(Rc::new(out))
            }
        },
        other => {
            return Err(unsupported(
                "builtin",
                &format!("builtin function {other}()"),
            ))
        }
    })
}

pub fn open_value(path: &str, mode: &str, kw: &[(Rc<str>, Value)]) -> R<Value> {
    let binary = mode.contains('b');
    if let Some(enc) = kwget(kw, "encoding") {
        let e = fmt::to_str(&enc)?.to_ascii_lowercase().replace('_', "-");
        if !matches!(e.as_str(), "utf-8" | "utf8" | "ascii" | "none") {
            return Err(unsupported("encoding", &format!("text encoding '{e}'")));
        }
    }
    if let Some(nl) = kwget(kw, "newline") {
        if !matches!(nl, Value::None) && fmt::to_str(&nl)? != "\n" {
            return Err(unsupported("open-newline", "open(newline=…) translation"));
        }
    }
    let base: String = mode.chars().filter(|c| !matches!(c, 'b' | 't')).collect();
    let f = mio::open_file(path, if base.is_empty() { "r" } else { &base }, binary)?;
    Ok(Value::File(Rc::new(RefCell::new(f))))
}

fn arg1(name: &str, args: &[Value]) -> R<Value> {
    args.first()
        .cloned()
        .ok_or_else(|| type_err(format!("{name}() missing 1 required positional argument")))
}

fn keyed(it: &mut Interp, keyf: &Option<Value>, v: &Value) -> R<Value> {
    match keyf {
        None => Ok(v.clone()),
        Some(f) => it.call(f, &mut Args::one(v.clone()), Vec::new()),
    }
}

pub fn length(v: &Value) -> R<usize> {
    Ok(match v {
        Value::Str(s) => s.chars().count(),
        Value::Bytes(b) => b.len(),
        Value::List(l) => l.borrow().len(),
        Value::Tuple(t) => t.len(),
        Value::Dict(d) => d.borrow().len(),
        Value::Set(s) => s.borrow().len(),
        Value::DictView(d, _) => d.borrow().len(),
        Value::Range(a, b, st) => range_len(*a, *b, *st).max(0) as usize,
        other => {
            return Err(type_err(format!(
                "object of type '{}' has no len()",
                type_name(other)
            )))
        }
    })
}

/// Python rounds half to EVEN, and does so on the exact binary value — which is
/// why `round(2.675, 2)` is 2.67 and not 2.68.
fn round_half_even(f: f64, ndigits: i64) -> f64 {
    if !f.is_finite() {
        return f;
    }
    if ndigits == 0 {
        let r = f.round();
        return if (f - f.trunc()).abs() == 0.5 && r % 2.0 != 0.0 {
            // The half-even correction can land on zero, and `-1.0 - -1.0` is
            // +0.0 in IEEE where CPython keeps `round(-0.5, 0) == -0.0`. The
            // sign of a zero is the only thing this line restores.
            let even = r - f.signum();
            if even == 0.0 {
                even.copysign(f)
            } else {
                even
            }
        } else {
            r
        };
    }
    // Formatting already rounds half-to-even on the exact value, so reuse it
    // rather than scaling by a power of ten (which introduces its own error).
    if (0..=17).contains(&ndigits) {
        let s = format!("{:.*}", ndigits as usize, f);
        return s.parse().unwrap_or(f);
    }
    if ndigits > 17 {
        return f;
    }
    let scale = 10f64.powi(-ndigits as i32);
    let r = (f / scale).round();
    r * scale
}
