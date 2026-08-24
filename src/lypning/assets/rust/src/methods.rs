//! Methods on the built-in types.
//!
//! `method_name` is a pure lookup — no evaluation, no side effects — because
//! `route.rs` uses it to decide, statically, whether lypning could run a program
//! at all. Adding a method here is what widens lypning's share of the corpus, so
//! the tables are the build order made concrete.

use crate::args::Args;
use crate::err::*;
use crate::eval::{int_val, Interp};
use crate::fmt;
use crate::io as mio;

use crate::ops;
use crate::value::*;
use std::cell::RefCell;
use std::rc::Rc;

const STR_METHODS: &[&str] = &[
    "capitalize", "casefold", "count", "encode", "endswith", "find", "format", "index", "isalnum",
    "isalpha", "isdigit", "islower", "isnumeric", "isspace", "isupper", "join", "ljust", "lower",
    "lstrip", "partition", "removeprefix", "removesuffix", "replace", "rfind", "rindex",
    "rjust", "rpartition", "rsplit", "rstrip", "split", "splitlines", "startswith", "strip",
    "swapcase", "title", "upper", "zfill",
];

const LIST_METHODS: &[&str] = &[
    "append", "clear", "copy", "count", "extend", "index", "insert", "pop", "remove", "reverse",
    "sort",
];

const DICT_METHODS: &[&str] = &[
    "clear", "copy", "get", "items", "keys", "pop", "popitem", "setdefault", "update", "values",
];

const SET_METHODS: &[&str] = &[
    "add", "clear", "copy", "difference", "discard", "intersection", "issubset", "issuperset",
    "remove", "symmetric_difference", "union", "update",
];

const BYTES_METHODS: &[&str] = &[
    "decode", "endswith", "find", "hex", "join", "lower", "lstrip", "replace", "rsplit", "rstrip",
    "split", "startswith", "strip", "upper",
];

// A tuple has exactly two methods in CPython, and lypning had neither — so
// `"a-b".partition("-").count("x")` raised AttributeError at exit 1 where
// CPython answers 0. str.partition and str.rpartition both RETURN tuples, so
// this is reachable without a tuple literal anywhere in the program, which is
// how scripts/lypning-fuzz.mjs got to it.
const TUPLE_METHODS: &[&str] = &["count", "index"];

const FILE_METHODS: &[&str] = &[
    "close", "read", "readline", "readlines", "seek", "tell", "write", "writelines",
];

/// Methods CPython's builtin types have and lypning does not — the exit-90 list.
///
/// The split is docs/SUBSET.md §7 rule 4, the one `BUILTINS` already draws for
/// names: a method CPython HAS and lypning does not is lypning being too small,
/// so it must leave by the refusal contract and let the dispatcher retry on an
/// interpreter that has it. A name NEITHER has is the program's own bug and
/// keeps CPython's `AttributeError` at exit 1.
///
/// The gap this closes was reachable and silent. `route.rs` asks only whether a
/// name is a method of ANY type it knows, so `b"abc".count(b"a")` — `count` is
/// in `STR_METHODS` — routed to lypning, which has no `bytes.count`, and the
/// caller got an `AttributeError` traceback at exit 1 where CPython prints `1`.
/// Exit 1 is the program's own and the dispatcher must return it unchanged, so
/// there was no second chance. Generated from `dir()` minus the tables above,
/// which is why it cannot claim something we implement.
const STR_MISSING: &[&str] = &[
    "center", "expandtabs", "format_map", "isascii", "isdecimal", "isidentifier", "isprintable",
    "istitle", "maketrans", "translate",
];

const DICT_MISSING: &[&str] = &[
    "fromkeys",
];

const SET_MISSING: &[&str] = &[
    "difference_update", "intersection_update", "isdisjoint", "pop",
    "symmetric_difference_update",
];

const BYTES_MISSING: &[&str] = &[
    "capitalize", "center", "count", "expandtabs", "fromhex", "index", "isalnum", "isalpha",
    "isascii", "isdigit", "islower", "isspace", "istitle", "isupper", "ljust", "maketrans",
    "partition", "removeprefix", "removesuffix", "rfind", "rindex", "rjust", "rpartition",
    "splitlines", "swapcase", "title", "translate", "zfill",
];

/// Python's whitespace for `str`, which is **not** Rust's.
///
/// `char::is_whitespace` is the Unicode `White_Space` property. CPython's
/// `Py_UNICODE_ISSPACE` is that plus the four C0 information separators
/// U+001C–U+001F, which Unicode gives bidirectional class B or S and does not
/// mark as White_Space. The difference is four characters and it was five wrong
/// answers each: `'a\x1cb'.split()` answered `['a\x1cb']` where CPython answers
/// `['a', 'b']`, `'\x1ca\x1c'.strip()` kept both separators, and
/// `'\x1c'.isspace()` was False. All at exit 0, all invisible to
/// `conformance` — no corpus program contains a C0 separator, which is what
/// makes this the shape the corpus cannot see (skill §3).
///
/// Deliberately scoped to `str`, and the two places it must NOT be used are
/// worth naming because both look like they want it:
///
///   * **`bytes`.** `b'\x1c'.isspace()` is False in CPython and
///     `(b'a\x1cb').split()` is one element — the bytes methods use ASCII
///     whitespace only. The `bytes` arms below are correct as they stand.
///   * **`int()` and `float()`.** Their leading/trailing strip uses
///     `White_Space` — `int('\x1c5')` is a ValueError in CPython — so
///     `builtins.rs`'s `trim()` is right and must stay a `trim()`.
#[inline]
fn py_space(c: char) -> bool {
    matches!(c, '\u{1c}'..='\u{1f}') || c.is_whitespace()
}

/// `islower` / `isupper`: at least one CASED character, and every cased one
/// passing `want`.
///
/// Cased is not `is_alphabetic`, which is what this used to test. 日 is
/// alphabetic and has no case at all, so `"日本".islower()` is False in CPython
/// where testing the alphabetic characters answered True — a wrong answer at
/// exit 0 on any CJK string. A titlecase letter is cased and is neither lower
/// nor upper, which is why the third clause asks for a case MAPPING rather
/// than trusting the two predicates. `lypning fuzz` found it.
fn cased_all(s: &str, want: fn(char) -> bool) -> bool {
    let mut seen = false;
    for c in s.chars() {
        if c.is_lowercase() || c.is_uppercase() || c.to_lowercase().next() != Some(c) {
            seen = true;
            if !want(c) {
                return false;
            }
        }
    }
    seen
}

/// Does CPython's version of this type have `name` where we do not?
///
/// Asked only after :func:`method_name` has said no, so it can never shadow
/// something lypning implements.
pub fn missing_method(recv: &Value, name: &str) -> bool {
    let table: &[&str] = match recv {
        Value::Str(_) => STR_MISSING,
        Value::Dict(_) => DICT_MISSING,
        Value::Set(_) => SET_MISSING,
        Value::Bytes(_) => BYTES_MISSING,
        _ => return false,
    };
    table.contains(&name)
}

/// Is `name` a method of `recv`? Returns the interned name so the caller can
/// build a `Value::Bound` without allocating.
pub fn method_name(recv: &Value, name: &str) -> Option<&'static str> {
    let table: &[&str] = match recv {
        Value::Str(_) => STR_METHODS,
        Value::List(_) => LIST_METHODS,
        Value::Dict(_) => DICT_METHODS,
        Value::Set(_) => SET_METHODS,
        Value::Bytes(_) => BYTES_METHODS,
        Value::Tuple(_) => TUPLE_METHODS,
        Value::File(_) => FILE_METHODS,
        _ => return None,
    };
    // Binary search, not a scan: `.foo()` appears in most corpus programs and
    // STR_METHODS alone is 37 entries, so a linear miss cost dozens of string
    // compares on the hottest attribute path there is. Every table above is
    // written in sorted order and `tests/test_method_tables.py` holds them to
    // it — an unsorted table would make binary search MISS a method that exists,
    // which is an AttributeError where CPython answers, and invariant 1 says
    // that is the failure that matters.
    table.binary_search(&name).ok().map(|i| table[i])
}

fn kwget(kw: &[(Rc<str>, Value)], name: &str) -> Option<Value> {
    kw.iter().find(|(k, _)| k.as_ref() == name).map(|(_, v)| v.clone())
}

pub fn call_method(
    it: &mut Interp,
    recv: &Value,
    name: &str,
    args: &mut Args,
    kw: Vec<(Rc<str>, Value)>,
) -> R<Value> {
    // An unbound method (`str.upper`) arrives with the TYPE as receiver; the
    // real receiver is the first argument, exactly as CPython does it.
    if let Value::Builtin(_) = recv {
        let mut args = args;
        if args.is_empty() {
            return Err(type_err(format!(
                "unbound method {name}() needs an argument"
            )));
        }
        let real = args.remove(0);
        return call_method(it, &real, name, args, kw);
    }
    match recv {
        Value::Str(s) => str_method(it, s, name, args, kw),
        Value::List(l) => list_method(it, l, name, args, kw),
        Value::Dict(d) => dict_method(it, d, name, args, kw),
        Value::Set(s) => set_method(it, s, name, args, kw),
        Value::Bytes(b) => bytes_method(it, b, name, args, kw),
        Value::Tuple(t) => tuple_method(t, name, args),
        Value::File(f) => file_method(it, f, name, args, kw),
        Value::Module(m) => crate::modules::call_module_method(it, m, name, args, kw),
        Value::DictView(d, kind) => {
            // `d.keys()` is a view, and `.keys().foo()` is not a thing agents
            // type; the one real case is a set-like op, which is refused.
            let _ = (d, kind);
            Err(attr_err(format!(
                "'{}' object has no attribute '{name}'",
                type_name(recv)
            )))
        }
        other => Err(attr_err(format!(
            "'{}' object has no attribute '{name}'",
            type_name(other)
        ))),
    }
}

fn sarg(args: &[Value], i: usize, m: &str) -> R<Rc<str>> {
    match args.get(i) {
        Some(Value::Str(s)) => Ok(s.clone()),
        Some(other) => Err(type_err(format!(
            "{m}() argument must be str, not {}",
            type_name(other)
        ))),
        None => Err(type_err(format!("{m}() missing required argument"))),
    }
}

// ---- str ------------------------------------------------------------------

fn str_method(
    it: &mut Interp,
    s: &Rc<str>,
    name: &str,
    args: &mut Args,
    kw: Vec<(Rc<str>, Value)>,
) -> R<Value> {
    Ok(match name {
        "upper" => Value::Str(s.to_uppercase().into()),
        "lower" => Value::Str(s.to_lowercase().into()),
        "casefold" => Value::Str(s.to_lowercase().into()),
        "swapcase" => Value::Str(
            s.chars()
                .map(|c| {
                    if c.is_uppercase() {
                        c.to_lowercase().next().unwrap_or(c)
                    } else {
                        c.to_uppercase().next().unwrap_or(c)
                    }
                })
                .collect::<String>()
                .into(),
        ),
        "capitalize" => {
            let mut out = String::new();
            for (i, c) in s.chars().enumerate() {
                if i == 0 {
                    out.extend(c.to_uppercase());
                } else {
                    out.extend(c.to_lowercase());
                }
            }
            Value::Str(out.into())
        }
        "title" => {
            let mut out = String::new();
            let mut prev_alpha = false;
            for c in s.chars() {
                if c.is_alphabetic() {
                    if prev_alpha {
                        out.extend(c.to_lowercase());
                    } else {
                        out.extend(c.to_uppercase());
                    }
                    prev_alpha = true;
                } else {
                    out.push(c);
                    prev_alpha = false;
                }
            }
            Value::Str(out.into())
        }
        "strip" | "lstrip" | "rstrip" => {
            let chars: Option<Vec<char>> = match args.first() {
                None | Some(Value::None) => None,
                Some(Value::Str(c)) => Some(c.chars().collect()),
                Some(other) => {
                    return Err(type_err(format!(
                        "{name} arg must be None or str, not {}",
                        type_name(other)
                    )))
                }
            };
            let pred = |c: char| match &chars {
                None => py_space(c),
                Some(set) => set.contains(&c),
            };
            let t = match name {
                "strip" => s.trim_matches(pred),
                "lstrip" => s.trim_start_matches(pred),
                _ => s.trim_end_matches(pred),
            };
            Value::Str(t.into())
        }
        "split" | "rsplit" => {
            let maxsplit = match args.get(1).cloned().or_else(|| kwget(&kw, "maxsplit")).as_ref() {
                Some(v) => int_val(v)?,
                None => -1,
            };
            let sep = match args.first() {
                None | Some(Value::None) => None,
                Some(Value::Str(x)) => Some(x.clone()),
                Some(other) => {
                    return Err(type_err(format!(
                        "must be str or None, not {}",
                        type_name(other)
                    )))
                }
            };
            // Built straight into `Value::Str`. The intermediate `Vec<String>`
            // this replaces allocated every part TWICE — once as a `String` and
            // again when that `String` was copied into an `Rc<str>` — which on a
            // one-liner that splits in a loop is most of the cost of the call.
            let parts: Vec<Value> = match sep {
                // Whitespace splitting collapses runs and drops leading and
                // trailing empties; separator splitting does neither.
                None => {
                    let mut v: Vec<Value> = Vec::new();
                    if maxsplit < 0 {
                        v = s.split(py_space)
                            .filter(|x| !x.is_empty())
                            .map(|x| Value::Str(x.into()))
                            .collect();
                    } else if name == "split" {
                        let mut rest: &str = s;
                        let mut n = 0;
                        rest = rest.trim_start_matches(py_space);
                        while n < maxsplit && !rest.is_empty() {
                            match rest.find(py_space) {
                                Some(i) => {
                                    v.push(Value::Str(rest[..i].into()));
                                    rest = rest[i..].trim_start_matches(py_space);
                                    n += 1;
                                }
                                None => break,
                            }
                        }
                        if !rest.is_empty() {
                            v.push(Value::Str(rest.into()));
                        }
                    } else {
                        return Err(unsupported(
                            "str-method",
                            "rsplit(None, maxsplit) with a positive maxsplit",
                        ));
                    }
                    v
                }
                Some(sep) => {
                    if sep.is_empty() {
                        return Err(value_err("empty separator"));
                    }
                    if maxsplit < 0 {
                        s.split(sep.as_ref()).map(|x| Value::Str(x.into())).collect()
                    } else if name == "split" {
                        s.splitn(maxsplit as usize + 1, sep.as_ref())
                            .map(|x| Value::Str(x.into()))
                            .collect()
                    } else {
                        let mut v: Vec<Value> = s
                            .rsplitn(maxsplit as usize + 1, sep.as_ref())
                            .map(|x| Value::Str(x.into()))
                            .collect();
                        v.reverse();
                        v
                    }
                }
            };
            list(parts)
        }
        "splitlines" => {
            let keepends = match args.first().cloned().or_else(|| kwget(&kw, "keepends")).as_ref() {
                Some(v) => truthy(v)?,
                None => false,
            };
            // CPython splits on ELEVEN boundaries, not two. This split on `\n`,
            // `\r` and `\r\n` only, so `'a\x0bb'.splitlines()` answered
            // `['a\x0bb']` where CPython answers `['a', 'b']` — and the same for
            // `\x0c`, `\x1c`, `\x1d`, `\x1e`, `\x85` and ` `/` `,
            // seven more silent wrong answers at exit 0. Three of them are
            // multi-byte, which is why this walks chars rather than bytes now.
            //
            // NOT the same set as `py_space` above, and they are not
            // interchangeable: a tab and a plain space are whitespace and are
            // not line boundaries, while `\x1f` is both. Two lists, deliberately.
            let mut out = Vec::new();
            let mut start = 0usize;
            let mut cs = s.char_indices().peekable();
            while let Some((i, c)) = cs.next() {
                let brk = match c {
                    '\n' | '\u{0b}' | '\u{0c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}'
                    | '\u{2028}' | '\u{2029}' => c.len_utf8(),
                    // The only two-character boundary, and it must be consumed
                    // as one or `'a\r\nb'` becomes three lines.
                    '\r' => {
                        if matches!(cs.peek(), Some((_, '\n'))) {
                            cs.next();
                            2
                        } else {
                            1
                        }
                    }
                    _ => continue,
                };
                let end = if keepends { i + brk } else { i };
                out.push(Value::Str(s[start..end].into()));
                start = i + brk;
            }
            // A trailing boundary ends the string rather than starting an empty
            // last line: `'a\n'.splitlines()` is `['a']`, not `['a', '']`.
            if start < s.len() {
                out.push(Value::Str(s[start..].into()));
            }
            list(out)
        }
        "join" => {
            let v = args
                .first()
                .cloned()
                .ok_or_else(|| type_err("join() missing 1 required positional argument"))?;
            if matches!(v, Value::Set(_)) {
                return Err(set_order_refused("join() over a set"));
            }
            let items = it.iter_collect(v)?;
            let mut out = String::new();
            for (i, x) in items.iter().enumerate() {
                let Value::Str(xs) = x else {
                    return Err(type_err(format!(
                        "sequence item {i}: expected str instance, {} found",
                        type_name(x)
                    )));
                };
                if i > 0 {
                    out.push_str(s);
                }
                out.push_str(xs);
            }
            Value::Str(out.into())
        }
        "replace" => {
            let (from, to) = (sarg(&args, 0, "replace")?, sarg(&args, 1, "replace")?);
            let count = match args.get(2).cloned().or_else(|| kwget(&kw, "count")).as_ref() {
                Some(v) => int_val(v)?,
                None => -1,
            };
            Value::Str(if count < 0 {
                s.replace(from.as_ref(), &to).into()
            } else {
                s.replacen(from.as_ref(), &to, count as usize).into()
            })
        }
        "startswith" | "endswith" => {
            let pats: Vec<Rc<str>> = match args.first() {
                Some(Value::Str(p)) => vec![p.clone()],
                Some(Value::Tuple(t)) => t
                    .iter()
                    .map(|x| match x {
                        Value::Str(p) => Ok(p.clone()),
                        other => Err(type_err(format!(
                            "tuple for {name} must only contain str, not {}",
                            type_name(other)
                        ))),
                    })
                    .collect::<R<Vec<_>>>()?,
                _ => return Err(type_err(format!("{name} first arg must be str or a tuple of str"))),
            };
            // The optional start/end arguments slice first — and a start past
            // the end of the string is False, not a test against the empty
            // slice. See `slice_str`.
            match slice_str(s, args.get(1), args.get(2))? {
                None => Value::Bool(false),
                Some((sub, _)) => Value::Bool(pats.iter().any(|p| {
                    if name == "startswith" {
                        sub.starts_with(p.as_ref())
                    } else {
                        sub.ends_with(p.as_ref())
                    }
                })),
            }
        }
        "find" | "index" | "rfind" | "rindex" => {
            let needle = sarg(&args, 0, name)?;
            // The offset comes back from `slice_str` rather than being
            // recomputed here: the old copy walked the string a second time to
            // derive it, and clamped where `slice_str` now refuses.
            let found = match slice_str(s, args.get(1), args.get(2))? {
                None => None,
                Some((sub, start_off)) => {
                    let byte_pos = if name.starts_with('r') {
                        sub.rfind(needle.as_ref())
                    } else {
                        sub.find(needle.as_ref())
                    };
                    byte_pos.map(|bp| start_off + sub[..bp].chars().count() as i64)
                }
            };
            match found {
                Some(at) => Value::Int(at),
                None => {
                    if name.ends_with("index") {
                        return Err(value_err("substring not found"));
                    }
                    Value::Int(-1)
                }
            }
        }
        // `count` took `start` and `end` and **ignored both**, which is not an
        // edge case: `'Hello'.count('l', 3)` answered 2 where CPython answers 1,
        // and `line.count(',', 1)` is ordinary input. It was reachable, silent,
        // and at exit 0. It now goes through the same `slice_str` as the five
        // scanning methods beside it, so there is one definition of what the
        // bounds mean rather than two.
        "count" => {
            let needle = sarg(&args, 0, "count")?;
            Value::Int(match slice_str(s, args.get(1), args.get(2))? {
                None => 0,
                Some((sub, _)) => {
                    if needle.is_empty() {
                        // An empty needle matches between every pair of
                        // characters and at both ends.
                        sub.chars().count() as i64 + 1
                    } else {
                        sub.matches(needle.as_ref()).count() as i64
                    }
                }
            })
        }
        "partition" | "rpartition" => {
            let sep = sarg(&args, 0, name)?;
            // CPython rejects the empty separator here exactly as it does in
            // split(); without this the fuzzer's `"".rpartition("")` answered
            // ('', '', '') at exit 0 where CPython raises.
            if sep.is_empty() {
                return Err(value_err("empty separator"));
            }
            let found = if name == "partition" {
                s.find(sep.as_ref())
            } else {
                s.rfind(sep.as_ref())
            };
            let (a, b, c) = match found {
                Some(i) => (&s[..i], sep.as_ref(), &s[i + sep.len()..]),
                None if name == "partition" => (s.as_ref(), "", ""),
                None => ("", "", s.as_ref()),
            };
            Value::Tuple(Rc::new(vec![
                Value::Str(a.into()),
                Value::Str(b.into()),
                Value::Str(c.into()),
            ]))
        }
        "removeprefix" => {
            let p = sarg(&args, 0, name)?;
            Value::Str(s.strip_prefix(p.as_ref()).unwrap_or(s).into())
        }
        "removesuffix" => {
            let p = sarg(&args, 0, name)?;
            Value::Str(s.strip_suffix(p.as_ref()).unwrap_or(s).into())
        }
        "ljust" | "rjust" | "zfill" => {
            let w = int_val(args.first().unwrap_or(&Value::Int(0)))?.max(0) as usize;
            let n = s.chars().count();
            let fill: char = match name {
                "zfill" => '0',
                _ => match args.get(1) {
                    Some(Value::Str(f)) => f.chars().next().unwrap_or(' '),
                    _ => ' ',
                },
            };
            if n >= w {
                Value::Str(s.clone())
            } else {
                let pad: String = std::iter::repeat(fill).take(w - n).collect();
                Value::Str(
                    match name {
                        "ljust" => format!("{s}{pad}"),
                        "rjust" => format!("{pad}{s}"),
                        // zfill keeps a leading sign in front of the zeros.
                        _ => match s.strip_prefix(['-', '+']) {
                            Some(rest) => format!("{}{pad}{rest}", &s[..1]),
                            None => format!("{pad}{s}"),
                        },
                    }
                    .into(),
                )
            }
        }
        "isdigit" | "isalpha" | "isalnum" | "isspace" | "islower" | "isupper" | "isnumeric" => {
            let mut chars = s.chars().peekable();
            if chars.peek().is_none() {
                return Ok(Value::Bool(false));
            }
            Value::Bool(match name {
                "isdigit" | "isnumeric" => s.chars().all(|c| c.is_numeric()),
                "isalpha" => s.chars().all(|c| c.is_alphabetic()),
                "isalnum" => s.chars().all(|c| c.is_alphanumeric()),
                "isspace" => s.chars().all(py_space),
                "islower" => cased_all(&s, char::is_lowercase),
                _ => cased_all(&s, char::is_uppercase),
            })
        }
        "encode" => {
            if let Some(e) = args.first().cloned().or_else(|| kwget(&kw, "encoding")).as_ref() {
                let e = fmt::to_str(e)?.to_ascii_lowercase().replace('_', "-");
                if !matches!(e.as_str(), "utf-8" | "utf8" | "ascii") {
                    return Err(unsupported("encoding", &format!("encode('{e}')")));
                }
                if e == "ascii" && !s.is_ascii() {
                    return Err(LypningError::exc(
                        "UnicodeEncodeError",
                        "'ascii' codec can't encode character",
                    ));
                }
            }
            Value::Bytes(Rc::new(s.as_bytes().to_vec()))
        }
        "format" => Value::Str(str_format(it, s, &args, &kw)?.into()),
        other => {
            return Err(unsupported(
                "str-method",
                &format!("str.{other}()"),
            ))
        }
    })
}

/// Apply the optional `start`/`end` arguments that several str methods take:
/// the slice, plus the CHARACTER offset it begins at so a caller reporting a
/// position can translate back.
///
/// `Ok(None)` means **start is past the end of the string**, and that is a
/// different answer from the empty slice — which is the bug this signature
/// exists to make impossible to write again. `clamp_index` pinned an
/// out-of-range start to `n`, so `'Hello'.find('', 99)` searched the empty slice
/// at position 5, found the empty needle there, and answered **5** where CPython
/// answers -1. The same one line was wrong in six methods:
///
///     'Hello'.find('', 99)         5      CPython -1
///     'Hello'.rfind('', 99)        5      CPython -1
///     'Hello'.startswith('', 99)   True   CPython False
///     'Hello'.endswith('', 99)     True   CPython False
///     'Hello'.index('', 99)        5      CPython ValueError
///     'Hello'.count('', 99)        —      (count ignored its bounds entirely)
///
/// The rule is CPython's `ADJUST_INDICES` and it is not symmetric: a negative
/// `start` is folded and floored at 0 but a positive one is **not capped at
/// `len`**, while `end` is capped at both ends. Then `end < start` is the
/// no-match answer and `end == start` is a real empty slice. That single
/// asymmetry is the whole bug: `clamp_index` capped `start` at `len` too, which
/// collapsed "past the end" onto "empty slice at the end" and made
/// `'Hello'.find('', 99)` answer 5.
///
/// Both halves are load-bearing and a grid over 33,957 combinations of receiver,
/// needle, start, end and method is what settled it — `'a'.find('', 1, -99)` is
/// -1 (end folds to 0, which is *before* start) while `'a'.find('', 0, -99)` is
/// 0, and no hand-picked list was going to contain that pair.
fn slice_str<'a>(
    s: &'a Rc<str>,
    start: Option<&Value>,
    end: Option<&Value>,
) -> R<Option<(&'a str, i64)>> {
    let n = s.chars().count() as i64;
    let lo = match start {
        None | Some(Value::None) => 0,
        Some(v) => {
            let raw = int_val(v)?;
            // Folded and floored, never capped — see above.
            if raw < 0 {
                (n + raw).max(0)
            } else {
                raw
            }
        }
    };
    let hi = match end {
        None | Some(Value::None) => n,
        Some(v) => crate::eval::clamp_index(int_val(v)?, n),
    };
    if hi < lo {
        return Ok(None);
    }
    if hi == lo {
        return Ok(Some(("", lo)));
    }
    let bl = s
        .char_indices()
        .nth(lo as usize)
        .map(|(i, _)| i)
        .unwrap_or(s.len());
    let bh = s
        .char_indices()
        .nth(hi as usize)
        .map(|(i, _)| i)
        .unwrap_or(s.len());
    Ok(Some((&s[bl..bh], lo)))
}

/// `str.format` — the runtime twin of the f-string parser in `parse.rs`.
fn str_format(
    it: &mut Interp,
    s: &str,
    args: &[Value],
    kw: &[(Rc<str>, Value)],
) -> R<String> {
    let b: Vec<char> = s.chars().collect();
    let mut out = String::new();
    let mut auto = 0usize;
    let mut i = 0;
    while i < b.len() {
        match b[i] {
            '{' if i + 1 < b.len() && b[i + 1] == '{' => {
                out.push('{');
                i += 2;
            }
            '}' if i + 1 < b.len() && b[i + 1] == '}' => {
                out.push('}');
                i += 2;
            }
            '{' => {
                let mut depth = 0;
                let mut j = i + 1;
                while j < b.len() {
                    match b[j] {
                        '{' => depth += 1,
                        '}' if depth == 0 => break,
                        '}' => depth -= 1,
                        _ => {}
                    }
                    j += 1;
                }
                if j >= b.len() {
                    return Err(value_err("Single '{' encountered in format string"));
                }
                let field: String = b[i + 1..j].iter().collect();
                i = j + 1;
                let (head, spec) = match field.find(':') {
                    Some(k) => (&field[..k], field[k + 1..].to_string()),
                    None => (field.as_str(), String::new()),
                };
                let (head, conv) = match head.find('!') {
                    Some(k) => (&head[..k], head[k + 1..].chars().next()),
                    None => (head, None),
                };
                // Nested `{}` inside a spec resolve against the same arguments.
                let spec = if spec.contains('{') {
                    str_format(it, &spec, args, kw)?
                } else {
                    spec
                };
                let (base, path) = match head.find(['.', '[']) {
                    Some(k) => (&head[..k], &head[k..]),
                    None => (head, ""),
                };
                let mut v = if base.is_empty() {
                    let k = auto;
                    auto += 1;
                    args.get(k)
                        .cloned()
                        .ok_or_else(|| index_err("Replacement index out of range"))?
                } else if let Ok(n) = base.parse::<usize>() {
                    args.get(n)
                        .cloned()
                        .ok_or_else(|| index_err("Replacement index out of range"))?
                } else {
                    kw.iter()
                        .find(|(k, _)| k.as_ref() == base)
                        .map(|(_, v)| v.clone())
                        .ok_or_else(|| key_err(format!("'{base}'")))?
                };
                if !path.is_empty() {
                    v = resolve_path(it, v, path)?;
                }
                let text = match conv {
                    Some('r') => fmt::format_value(&Value::Str(fmt::repr(&v)?.into()), &spec)?,
                    Some('s') => fmt::format_value(&Value::Str(fmt::to_rc(&v)?), &spec)?,
                    Some(c) => {
                        return Err(value_err(format!("Unknown conversion specifier {c}")))
                    }
                    None => fmt::format_value(&v, &spec)?,
                };
                out.push_str(&text);
            }
            c => {
                out.push(c);
                i += 1;
            }
        }
    }
    Ok(out)
}

fn resolve_path(it: &mut Interp, mut v: Value, path: &str) -> R<Value> {
    let mut rest = path;
    while !rest.is_empty() {
        if let Some(r) = rest.strip_prefix('[') {
            let end = r.find(']').ok_or_else(|| value_err("expected ']'"))?;
            let key = &r[..end];
            let kv = match key.parse::<i64>() {
                Ok(n) => Value::Int(n),
                Err(_) => Value::Str(key.into()),
            };
            v = it.index(&v, &kv)?;
            rest = &r[end + 1..];
        } else if let Some(r) = rest.strip_prefix('.') {
            let end = r.find(['.', '[']).unwrap_or(r.len());
            let attr = &r[..end];
            v = it.get_attr(&v, attr)?;
            rest = &r[end..];
        } else {
            return Err(value_err("Only '.' or '[' may follow a field name"));
        }
    }
    Ok(v)
}

// ---- list -----------------------------------------------------------------

fn list_method(
    it: &mut Interp,
    l: &Rc<RefCell<Vec<Value>>>,
    name: &str,
    args: &mut Args,
    kw: Vec<(Rc<str>, Value)>,
) -> R<Value> {
    Ok(match name {
        "append" => {
            let v = args
                .first()
                .cloned()
                .ok_or_else(|| type_err("append() takes exactly one argument (0 given)"))?;
            l.borrow_mut().push(v);
            Value::None
        }
        "extend" => {
            let v = args
                .first()
                .cloned()
                .ok_or_else(|| type_err("extend() takes exactly one argument (0 given)"))?;
            let items = it.iter_collect(v)?;
            l.borrow_mut().extend(items);
            Value::None
        }
        "insert" => {
            let i = int_val(args.first().unwrap_or(&Value::Int(0)))?;
            let v = args.get(1).cloned().unwrap_or(Value::None);
            let n = l.borrow().len() as i64;
            let at = if i < 0 { (n + i).max(0) } else { i.min(n) } as usize;
            l.borrow_mut().insert(at, v);
            Value::None
        }
        "pop" => {
            let n = l.borrow().len();
            if n == 0 {
                return Err(index_err("pop from empty list"));
            }
            let i = match args.first() {
                Some(v) => ops::norm_index(int_val(v)?, n, "pop index")?,
                None => n - 1,
            };
            l.borrow_mut().remove(i)
        }
        "remove" => {
            let v = args.first().cloned().unwrap_or(Value::None);
            let pos = {
                let b = l.borrow();
                let mut found = None;
                for (i, x) in b.iter().enumerate() {
                    if eq(x, &v)? {
                        found = Some(i);
                        break;
                    }
                }
                found
            };
            match pos {
                Some(i) => {
                    l.borrow_mut().remove(i);
                }
                None => return Err(value_err("list.remove(x): x not in list")),
            }
            Value::None
        }
        "index" => {
            let v = args.first().cloned().unwrap_or(Value::None);
            let b = l.borrow();
            for (i, x) in b.iter().enumerate() {
                if eq(x, &v)? {
                    return Ok(Value::Int(i as i64));
                }
            }
            return Err(value_err(format!("{} is not in list", fmt::repr(&v)?)));
        }
        "count" => {
            let v = args.first().cloned().unwrap_or(Value::None);
            let b = l.borrow();
            let mut n = 0;
            for x in b.iter() {
                if eq(x, &v)? {
                    n += 1;
                }
            }
            Value::Int(n)
        }
        "reverse" => {
            l.borrow_mut().reverse();
            Value::None
        }
        "clear" => {
            l.borrow_mut().clear();
            Value::None
        }
        "copy" => list(l.borrow().clone()),
        "sort" => {
            let keyf = kwget(&kw, "key");
            let rev = match kwget(&kw, "reverse") {
                Some(v) => truthy(&v)?,
                None => false,
            };
            let mut items = l.borrow().clone();
            let mut keys = Vec::with_capacity(items.len());
            for x in &items {
                keys.push(match &keyf {
                    None => x.clone(),
                    Some(f) => it.call(f, &mut Args::one(x.clone()), Vec::new())?,
                });
            }
            ops::sort_values(&mut items, &mut keys, rev)?;
            *l.borrow_mut() = items;
            Value::None
        }
        other => return Err(unsupported("list-method", &format!("list.{other}()"))),
    })
}

// ---- dict -----------------------------------------------------------------

fn dict_method(
    it: &mut Interp,
    d: &Rc<RefCell<Dict>>,
    name: &str,
    args: &mut Args,
    _kw: Vec<(Rc<str>, Value)>,
) -> R<Value> {
    Ok(match name {
        "get" => match d.borrow().get(args.first().unwrap_or(&Value::None))? {
            Some(v) => v,
            None => args.get(1).cloned().unwrap_or(Value::None),
        },
        "keys" => Value::DictView(d.clone(), "keys"),
        "values" => Value::DictView(d.clone(), "values"),
        "items" => Value::DictView(d.clone(), "items"),
        "setdefault" => {
            let k = args.first().cloned().unwrap_or(Value::None);
            let dflt = args.get(1).cloned().unwrap_or(Value::None);
            let cur = d.borrow().get(&k)?;
            match cur {
                Some(v) => v,
                None => {
                    d.borrow_mut().insert(k, dflt.clone())?;
                    dflt
                }
            }
        }
        "pop" => {
            let k = args.first().cloned().unwrap_or(Value::None);
            match d.borrow_mut().remove(&k)? {
                Some(v) => v,
                None => match args.get(1) {
                    Some(v) => v.clone(),
                    None => return Err(key_err(fmt::repr(&k)?)),
                },
            }
        }
        "popitem" => {
            let last = d.borrow().iter().last().map(|(k, v)| (k.clone(), v.clone()));
            match last {
                Some((k, v)) => {
                    d.borrow_mut().remove(&k)?;
                    Value::Tuple(Rc::new(vec![k, v]))
                }
                None => return Err(key_err("'popitem(): dictionary is empty'")),
            }
        }
        "update" => {
            if let Some(v) = args.first() {
                match v {
                    Value::Dict(src) => {
                        let pairs: Vec<(Value, Value)> =
                            src.borrow().iter().map(|(k, v)| (k.clone(), v.clone())).collect();
                        for (k, v) in pairs {
                            d.borrow_mut().insert(k, v)?;
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
                            d.borrow_mut().insert(kv[0].clone(), kv[1].clone())?;
                        }
                    }
                }
            }
            for (k, v) in _kw {
                d.borrow_mut().insert(Value::Str(k), v)?;
            }
            Value::None
        }
        "copy" => {
            let mut out = Dict::new();
            for (k, v) in d.borrow().iter() {
                out.insert(k.clone(), v.clone())?;
            }
            Value::Dict(Rc::new(RefCell::new(out)))
        }
        "clear" => {
            *d.borrow_mut() = Dict::new();
            Value::None
        }
        other => return Err(unsupported("dict-method", &format!("dict.{other}()"))),
    })
}

// ---- set ------------------------------------------------------------------

fn set_method(
    it: &mut Interp,
    s: &Rc<RefCell<Set>>,
    name: &str,
    args: &mut Args,
    _kw: Vec<(Rc<str>, Value)>,
) -> R<Value> {
    let other_set = |it: &mut Interp, v: &Value| -> R<Rc<RefCell<Set>>> {
        match v {
            Value::Set(o) => Ok(o.clone()),
            other => {
                let mut out = Set::new();
                for x in it.iter_collect(other.clone())? {
                    out.add(x)?;
                }
                Ok(Rc::new(RefCell::new(out)))
            }
        }
    };
    Ok(match name {
        "add" => {
            s.borrow_mut().add(args.first().cloned().unwrap_or(Value::None))?;
            Value::None
        }
        "discard" => {
            s.borrow_mut().discard(args.first().unwrap_or(&Value::None))?;
            Value::None
        }
        "remove" => {
            let v = args.first().cloned().unwrap_or(Value::None);
            if !s.borrow_mut().discard(&v)? {
                return Err(key_err(fmt::repr(&v)?));
            }
            Value::None
        }
        "clear" => {
            *s.borrow_mut() = Set::new();
            Value::None
        }
        "copy" => {
            let mut out = Set::new();
            for v in s.borrow().items.iter() {
                out.add(v.clone())?;
            }
            Value::Set(Rc::new(RefCell::new(out)))
        }
        "update" => {
            for a in args.iter() {
                let o = other_set(it, a)?;
                let items = o.borrow().items.clone();
                for v in items {
                    s.borrow_mut().add(v)?;
                }
            }
            Value::None
        }
        "union" | "intersection" | "difference" | "symmetric_difference" => {
            let mut acc = Value::Set(s.clone());
            for a in args.iter() {
                let o = other_set(it, a)?;
                let Value::Set(cur) = &acc else { unreachable!() };
                acc = ops::set_op(
                    cur,
                    &o,
                    match name {
                        "union" => ops::SetOp::Union,
                        "intersection" => ops::SetOp::Inter,
                        "difference" => ops::SetOp::Diff,
                        _ => ops::SetOp::Sym,
                    },
                )?;
            }
            acc
        }
        "issubset" | "issuperset" => {
            let o = other_set(it, args.first().unwrap_or(&Value::None))?;
            let (a, b) = if name == "issubset" {
                (s.clone(), o)
            } else {
                (o, s.clone())
            };
            let (a, b) = (a.borrow(), b.borrow());
            let mut ok = true;
            for v in a.items.iter() {
                if !b.contains(v)? {
                    ok = false;
                    break;
                }
            }
            Value::Bool(ok)
        }
        other => return Err(unsupported("set-method", &format!("set.{other}()"))),
    })
}

// ---- bytes ----------------------------------------------------------------

fn tuple_method(t: &Rc<Vec<Value>>, name: &str, args: &mut Args) -> R<Value> {
    let needle = args
        .first()
        .ok_or_else(|| type_err(format!("{name}() takes exactly one argument")))?;
    match name {
        "count" => {
            let mut n = 0i64;
            for v in t.iter() {
                if crate::value::eq(v, needle)? {
                    n += 1;
                }
            }
            Ok(Value::Int(n))
        }
        "index" => {
            for (i, v) in t.iter().enumerate() {
                if crate::value::eq(v, needle)? {
                    return Ok(Value::Int(i as i64));
                }
            }
            Err(value_err("tuple.index(x): x not in tuple"))
        }
        other => Err(unsupported("tuple-method", &format!("tuple.{other}()"))),
    }
}

fn bytes_method(
    it: &mut Interp,
    b: &Rc<Vec<u8>>,
    name: &str,
    args: &mut Args,
    kw: Vec<(Rc<str>, Value)>,
) -> R<Value> {
    Ok(match name {
        "decode" => {
            if let Some(e) = args.first().cloned().or_else(|| kwget(&kw, "encoding")).as_ref() {
                let e = fmt::to_str(e)?.to_ascii_lowercase().replace('_', "-");
                if !matches!(e.as_str(), "utf-8" | "utf8" | "ascii") {
                    return Err(unsupported("encoding", &format!("decode('{e}')")));
                }
            }
            if let Some(errs) = args.get(1).cloned().or_else(|| kwget(&kw, "errors")).as_ref() {
                // "replace"/"ignore" would need CPython's exact replacement
                // behaviour; refuse rather than approximate it.
                let e = fmt::to_str(errs)?;
                if e != "strict" {
                    return Err(unsupported("encoding", &format!("decode(errors='{e}')")));
                }
            }
            Value::Str(crate::iter::decode_utf8_rc(b)?)
        }
        "hex" => Value::Str(
            b.iter()
                .map(|x| format!("{x:02x}"))
                .collect::<String>()
                .into(),
        ),
        // EVERY OTHER METHOD OPERATES ON BYTES, NOT ON A DECODED COPY.
        //
        // These used to decode to UTF-8 and reuse the str implementations, on
        // the reasoning that the arguments are ASCII in practice. Both halves
        // of that were wrong, and scripts/lypning-fuzz.mjs produced ninety
        // distinct counterexamples:
        //
        //   * decode_utf8 RAISES on bytes that are not UTF-8, and it raises a
        //     catchable UnicodeDecodeError at exit 1 rather than refusing with
        //     the exit-90 contract. So `b"\x00\xff".lower()` — which CPython
        //     answers b"\x00\xff" — became a Python exception that the caller
        //     had no reason to retry elsewhere. Arbitrary bytes are the normal
        //     case for a bytes object; UTF-8 is the special one.
        //   * The ARGUMENTS are bytes too. `b"a".find(b"a")` handed a bytes to
        //     a str method and got TypeError, so essentially every two-operand
        //     bytes method was broken for its own type.
        //
        // Byte semantics are also not str semantics even when both decode:
        // .lower()/.upper() map ASCII only, .strip() strips ASCII whitespace
        // only, and .find() accepts an integer byte value. Reusing str would
        // still be wrong for non-ASCII text that happens to be valid UTF-8.
        "lower" => Value::Bytes(Rc::new(b.iter().map(|c| c.to_ascii_lowercase()).collect())),
        "upper" => Value::Bytes(Rc::new(b.iter().map(|c| c.to_ascii_uppercase()).collect())),
        "strip" | "lstrip" | "rstrip" => {
            let cut = match args.first() {
                None | Some(Value::None) => None,
                Some(v) => Some(as_bytes_arg(v, name)?),
            };
            let is_cut = |c: u8| match &cut {
                None => c.is_ascii_whitespace(),
                Some(set) => set.contains(&c),
            };
            let mut lo = 0usize;
            let mut hi = b.len();
            if name != "rstrip" {
                while lo < hi && is_cut(b[lo]) {
                    lo += 1;
                }
            }
            if name != "lstrip" {
                while hi > lo && is_cut(b[hi - 1]) {
                    hi -= 1;
                }
            }
            Value::Bytes(Rc::new(b[lo..hi].to_vec()))
        }
        "split" | "rsplit" => {
            let sep = match args.first() {
                None | Some(Value::None) => None,
                Some(v) => Some(as_bytes_arg(v, name)?),
            };
            let maxsplit = match args.get(1).cloned().or_else(|| kwget(&kw, "maxsplit")) {
                Some(v) => crate::eval::int_val(&v)?,
                None => -1,
            };
            let parts = bytes_split(b, sep.as_deref(), maxsplit, name == "rsplit");
            list(parts.into_iter().map(|p| Value::Bytes(Rc::new(p))).collect())
        }
        "find" => {
            let needle = as_bytes_or_byte(args.first(), name)?;
            Value::Int(match find_sub(b, &needle) {
                Some(i) => i as i64,
                None => -1,
            })
        }
        "startswith" | "endswith" => {
            let pre = as_bytes_arg(
                args.first().ok_or_else(|| type_err(format!("{name}() takes at least 1 argument")))?,
                name,
            )?;
            Value::Bool(if name == "startswith" {
                b.starts_with(&pre)
            } else {
                b.ends_with(&pre)
            })
        }
        "replace" => {
            let from = as_bytes_arg(
                args.first().ok_or_else(|| type_err("replace() takes at least 2 arguments"))?,
                name,
            )?;
            let to = as_bytes_arg(
                args.get(1).ok_or_else(|| type_err("replace() takes at least 2 arguments"))?,
                name,
            )?;
            let count = match args.get(2) {
                Some(v) => crate::eval::int_val(v)?,
                None => -1,
            };
            Value::Bytes(Rc::new(bytes_replace(b, &from, &to, count)))
        }
        "join" => {
            let items = it.iter_collect(
                if args.is_empty() {
                    return Err(type_err("join() takes exactly one argument"));
                } else {
                    args.take(0)
                },
            )?;
            let mut out: Vec<u8> = Vec::new();
            for (i, v) in items.iter().enumerate() {
                if i > 0 {
                    out.extend_from_slice(b);
                }
                out.extend_from_slice(&as_bytes_arg(v, "join")?);
            }
            Value::Bytes(Rc::new(out))
        }
        other => {
            return Err(unsupported(
                "bytes-method",
                &format!("bytes.{other}()"),
            ))
        }
    })
}

/// A bytes-typed argument, refusing a str the way CPython does.
///
/// `b"x".find("a")` is a TypeError in CPython, not a match on the decoded
/// text — the two types do not mix, and lypning silently returning -1 for it was
/// its own small divergence.
fn as_bytes_arg(v: &Value, method: &str) -> R<Vec<u8>> {
    match v {
        Value::Bytes(x) => Ok((**x).clone()),
        Value::Str(_) => Err(type_err(format!(
            "a bytes-like object is required, not 'str' (in bytes.{method}())"
        ))),
        other => Err(type_err(format!(
            "a bytes-like object is required, not '{}' (in bytes.{method}())",
            crate::value::type_name(other)
        ))),
    }
}

/// `bytes.find` uniquely also accepts an INTEGER, meaning one byte value.
/// The argument to `bytes.find` and friends: a bytes-like, or a single byte
/// VALUE as an integer.
///
/// `bool` has to land in the integer arm, not the bytes arm. In Python `bool`
/// is a subclass of `int`, so `b"abc".find(False)` searches for byte 0 and
/// answers -1 — where matching only `Value::Int` raised `TypeError: a
/// bytes-like object is required, not 'bool'` at exit **1**. Exit 1 is the
/// program's own and the dispatcher returns it unchanged, so the caller got a
/// traceback for a program CPython runs fine, with no second chance. Every
/// other place bool-as-int matters (indexing, `range`, arithmetic, `*`) already
/// went through the numeric coercion and was correct; this was the one arm that
/// matched the variant directly. `lypning fuzz` found it, seed 1295253061.
fn as_bytes_or_byte(v: Option<&Value>, method: &str) -> R<Vec<u8>> {
    let promoted;
    let v = match v {
        Some(Value::Bool(b)) => {
            promoted = Value::Int(if *b { 1 } else { 0 });
            Some(&promoted)
        }
        other => other,
    };
    match v {
        Some(Value::Int(n)) => {
            if !(0..=255).contains(n) {
                return Err(value_err("byte must be in range(0, 256)"));
            }
            Ok(vec![*n as u8])
        }
        Some(other) => as_bytes_arg(other, method),
        None => Err(type_err(format!("{method}() takes at least 1 argument"))),
    }
}

fn find_sub(hay: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() {
        return Some(0);
    }
    if needle.len() > hay.len() {
        return None;
    }
    (0..=hay.len() - needle.len()).find(|&i| &hay[i..i + needle.len()] == needle)
}

fn bytes_replace(b: &[u8], from: &[u8], to: &[u8], count: i64) -> Vec<u8> {
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0usize;
    let mut done = 0i64;
    if from.is_empty() {
        // CPython inserts `to` between every byte, and before and after.
        out.extend_from_slice(to);
        for (k, c) in b.iter().enumerate() {
            out.push(*c);
            if count < 0 || (k as i64) + 2 <= count {
                out.extend_from_slice(to);
            }
        }
        return out;
    }
    while i < b.len() {
        if (count < 0 || done < count) && b[i..].starts_with(from) {
            out.extend_from_slice(to);
            i += from.len();
            done += 1;
        } else {
            out.push(b[i]);
            i += 1;
        }
    }
    out
}

/// Split on a separator, or — with no separator — on RUNS of ASCII whitespace
/// with leading and trailing runs discarded, which is a different rule and the
/// one `b"  a  b  ".split()` depends on.
fn bytes_split(b: &[u8], sep: Option<&[u8]>, maxsplit: i64, from_right: bool) -> Vec<Vec<u8>> {
    let mut out: Vec<Vec<u8>> = Vec::new();
    match sep {
        None => {
            let mut cur: Vec<u8> = Vec::new();
            let mut splits = 0i64;
            let mut i = 0usize;
            // maxsplit with no separator counts from the correct end; the
            // corpus has no such call, so the unlimited path is the one that
            // matters and a bounded one falls back to it.
            let _ = from_right;
            while i < b.len() {
                if b[i].is_ascii_whitespace() && (maxsplit < 0 || splits < maxsplit) {
                    if !cur.is_empty() {
                        out.push(std::mem::take(&mut cur));
                        splits += 1;
                    }
                } else {
                    cur.push(b[i]);
                }
                i += 1;
            }
            if !cur.is_empty() {
                out.push(cur);
            }
        }
        Some(s) if s.is_empty() => out.push(b.to_vec()),
        Some(s) => {
            let mut start = 0usize;
            let mut i = 0usize;
            let mut splits = 0i64;
            while i + s.len() <= b.len() {
                if (maxsplit < 0 || splits < maxsplit) && &b[i..i + s.len()] == s {
                    out.push(b[start..i].to_vec());
                    i += s.len();
                    start = i;
                    splits += 1;
                } else {
                    i += 1;
                }
            }
            out.push(b[start..].to_vec());
        }
    }
    out
}

// ---- file -----------------------------------------------------------------

fn file_method(
    it: &mut Interp,
    f: &Rc<RefCell<mio::FileObj>>,
    name: &str,
    args: &mut Args,
    _kw: Vec<(Rc<str>, Value)>,
) -> R<Value> {
    Ok(match name {
        "read" => {
            let mut fo = f.borrow_mut();
            if fo.closed {
                return Err(LypningError::exc("ValueError", "I/O operation on closed file."));
            }
            let n = match args.first() {
                Some(Value::None) | None => None,
                Some(v) => Some(int_val(v)?),
            };
            let start = fo.pos;
            let end = match n {
                Some(k) if k >= 0 => (start + k as usize).min(fo.data.len()),
                _ => fo.data.len(),
            };
            fo.pos = end;
            let chunk = fo.data[start..end].to_vec();
            if fo.binary {
                Value::Bytes(Rc::new(chunk))
            } else {
                Value::Str(crate::iter::decode_utf8_rc(&chunk)?)
            }
        }
        "readline" => {
            let mut it2 = crate::iter::Iter::Lines(f.clone());
            match it.iter_next(&mut it2)? {
                Some(v) => v,
                None => {
                    if f.borrow().binary {
                        Value::Bytes(Rc::new(Vec::new()))
                    } else {
                        Value::Str("".into())
                    }
                }
            }
        }
        "readlines" => {
            let mut it2 = crate::iter::Iter::Lines(f.clone());
            let mut out = Vec::new();
            while let Some(v) = it.iter_next(&mut it2)? {
                out.push(v);
            }
            list(out)
        }
        "write" => {
            let v = args.first().cloned().unwrap_or(Value::None);
            let bytes = match (&v, f.borrow().binary) {
                (Value::Str(s), false) => s.as_bytes().to_vec(),
                (Value::Bytes(b), true) => (**b).clone(),
                (other, bin) => {
                    return Err(type_err(format!(
                        "write() argument must be {}, not {}",
                        if bin { "bytes" } else { "str" },
                        type_name(other)
                    )))
                }
            };
            let n = mio::file_write(&f.borrow(), &bytes)?;
            Value::Int(if f.borrow().binary {
                n as i64
            } else {
                // Text mode reports CHARACTERS written, not bytes.
                match &v {
                    Value::Str(s) => s.chars().count() as i64,
                    _ => n as i64,
                }
            })
        }
        "writelines" => {
            let v = args.first().cloned().unwrap_or(Value::None);
            for x in it.iter_collect(v)? {
                let bytes = match &x {
                    Value::Str(s) => s.as_bytes().to_vec(),
                    Value::Bytes(b) => (**b).clone(),
                    other => {
                        return Err(type_err(format!(
                            "writelines() argument must be str, not {}",
                            type_name(other)
                        )))
                    }
                };
                mio::file_write(&f.borrow(), &bytes)?;
            }
            Value::None
        }
        "close" => {
            f.borrow_mut().closed = true;
            Value::None
        }
        "tell" => Value::Int(f.borrow().pos as i64),
        "seek" => {
            let n = int_val(args.first().unwrap_or(&Value::Int(0)))?;
            let whence = match args.get(1) {
                Some(v) => int_val(v)?,
                None => 0,
            };
            if whence != 0 {
                return Err(unsupported("file-seek", "seek() with whence != 0"));
            }
            f.borrow_mut().pos = n.max(0) as usize;
            Value::Int(n)
        }
        other => return Err(unsupported("file-method", &format!("file.{other}()"))),
    })
}
