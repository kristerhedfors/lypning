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

/// Characters whose Unicode **full case folding** is not their lowercasing.
///
/// `str.casefold()` was `to_lowercase()`, which is right for every ASCII string
/// and wrong for 297 codepoints — including the one everybody reaches for:
/// `'ß'.casefold()` is `'ss'` in CPython and was `'ß'` here, so
/// `'ß'.casefold() == 'ss'.casefold()` was False. Caseless comparison is the
/// entire purpose of the method.
///
/// Rust's `std` has no full case folding and this refuses rather than shipping a
/// table, because a table here would be a **second** source of Unicode truth in
/// a runtime whose first one is whatever the toolchain shipped — and two of them
/// drift apart silently. `docs/SUBSET.md` §7 rule 4: a method CPython has and
/// lypning does not have *correctly* leaves by the refusal contract, and the
/// dispatcher gets the real answer one spawn later. Over-refusing is a coverage
/// number; answering `'ß'` is a wrong answer.
///
/// 41 ranges, derived by asking CPython for every codepoint where
/// `chr(c).casefold() != chr(c).lower()` — not copied from a document.
fn casefold_differs(c: char) -> bool {
    matches!(c,
        '\u{b5}' | '\u{df}' | '\u{149}' | '\u{17f}' | '\u{1f0}' | '\u{345}' | '\u{390}'
        | '\u{3b0}' | '\u{3c2}' | '\u{3d0}'..='\u{3d1}' | '\u{3d5}'..='\u{3d6}'
        | '\u{3f0}'..='\u{3f1}' | '\u{3f5}' | '\u{587}' | '\u{13a0}'..='\u{13f5}'
        | '\u{13f8}'..='\u{13fd}' | '\u{1c80}'..='\u{1c88}' | '\u{1e96}'..='\u{1e9b}'
        | '\u{1e9e}' | '\u{1f50}' | '\u{1f52}' | '\u{1f54}' | '\u{1f56}'
        | '\u{1f80}'..='\u{1faf}' | '\u{1fb2}'..='\u{1fb4}' | '\u{1fb6}'..='\u{1fb7}'
        | '\u{1fbc}' | '\u{1fbe}' | '\u{1fc2}'..='\u{1fc4}' | '\u{1fc6}'..='\u{1fc7}'
        | '\u{1fcc}' | '\u{1fd2}'..='\u{1fd3}' | '\u{1fd6}'..='\u{1fd7}'
        | '\u{1fe2}'..='\u{1fe4}' | '\u{1fe6}'..='\u{1fe7}' | '\u{1ff2}'..='\u{1ff4}'
        | '\u{1ff6}'..='\u{1ff7}' | '\u{1ffc}' | '\u{ab70}'..='\u{abbf}'
        | '\u{fb00}'..='\u{fb06}' | '\u{fb13}'..='\u{fb17}')
}

/// Characters whose Unicode **titlecase** is not their uppercase.
///
/// `title()` and `capitalize()` uppercased the leading letter of each word where
/// CPython titlecases it. For 135 codepoints those differ, and the two families
/// that matter are the digraphs — `'ǅ'.title()` is `'ǅ'`, not `'Ǆ'` — and the
/// sharp s, where `'ß'.capitalize()` is `'Ss'` and not `'SS'`.
///
/// Refused for the same reason as `casefold_differs`: `std` has no
/// `char::to_titlecase`, and inventing a table is inventing a second Unicode.
/// 18 ranges, derived from CPython the same way.
fn titlecase_differs(c: char) -> bool {
    matches!(c,
        '\u{df}' | '\u{1c4}'..='\u{1cc}' | '\u{1f1}'..='\u{1f3}' | '\u{587}'
        | '\u{10d0}'..='\u{10fa}' | '\u{10fd}'..='\u{10ff}' | '\u{1f80}'..='\u{1faf}'
        | '\u{1fb2}'..='\u{1fb4}' | '\u{1fb7}' | '\u{1fbc}' | '\u{1fc2}'..='\u{1fc4}'
        | '\u{1fc7}' | '\u{1fcc}' | '\u{1ff2}'..='\u{1ff4}' | '\u{1ff7}' | '\u{1ffc}'
        | '\u{fb00}'..='\u{fb06}' | '\u{fb13}'..='\u{fb17}')
}

/// Refuse the whole call if any character in the receiver is one this mapping
/// cannot reproduce. Scanning the receiver rather than only the positions the
/// method would map is deliberate over-refusal: it costs one linear pass on a
/// path that already makes a copy, and it cannot be wrong the way a clever
/// position-aware version could be.
fn refuse_unmappable(s: &str, bad: fn(char) -> bool, method: &str) -> R<()> {
    match s.chars().find(|&c| bad(c)) {
        None => Ok(()),
        Some(c) => Err(unsupported(
            "str-method",
            &format!("str.{method}() of U+{:04X}", c as u32),
        )),
    }
}

/// A one-byte ASCII needle, or `None` for anything else.
///
/// `str`'s substring routines build a **two-way searcher** — a Boyer-Moore-class
/// algorithm with a setup phase — for any `&str` pattern, however short.
/// Callgrind on `t.count('a')` over a six-character string, 2026-08-25:
/// `TwoWaySearcher::next` and `StrSearcher::new` together are **3.64M of
/// 49.9M instructions retired**, 7.3%, to look for one byte.
///
/// A single ASCII byte cannot occur as a UTF-8 continuation byte — those are all
/// `>= 0x80` — so finding or counting it in the bytes is exactly finding or
/// counting it in the characters. That is what makes the shortcut sound, and it
/// is why the guard is `< 0x80` and not `len() == 1`: a one-BYTE needle is not
/// the same thing as a one-CHARACTER needle, and `'é'` is two bytes.
#[inline]
fn one_ascii_byte(needle: &str) -> Option<u8> {
    match needle.as_bytes() {
        [b] if *b < 0x80 => Some(*b),
        _ => None,
    }
}

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
/// Methods `range` has in CPython and this engine does not implement. Sorted,
/// like every other table here (`tests/test_method_tables.py` holds them to it).
const RANGE_MISSING: &[&str] = &["count", "index"];

pub fn missing_method(recv: &Value, name: &str) -> bool {
    let table: &[&str] = match recv {
        Value::Str(_) => STR_MISSING,
        Value::Dict(_) => DICT_MISSING,
        Value::Set(_) => SET_MISSING,
        Value::Bytes(_) => BYTES_MISSING,
        // `range.index` and `range.count` are real methods CPython has, and
        // this answered AttributeError for them — exit 1, the program's own
        // exit, which the chain does not retry. A refusal is answered one spawn
        // later; that error was not.
        Value::Range(..) => RANGE_MISSING,
        _ => return false,
    };
    table.contains(&name)
}

/// Is `name` a method of `recv`? Returns the interned name so the caller can
/// build a `Value::Bound` without allocating.
pub fn method_name(recv: &Value, name: &str) -> Option<&'static str> {
    // A `Counter` is a tagged `Dict`, and its table is `dict`'s plus
    // `most_common`; a `defaultdict`'s is `dict`'s exactly. Asked here rather
    // than by widening `DICT_METHODS`, which would resolve `{}.most_common`
    // on a plain dict — an answer where CPython raises.
    #[cfg(feature = "cap-collections")]
    if let Value::Dict(d) = recv {
        if let Some(k) = crate::collections::kind_of(d) {
            return crate::collections::method_name(k, name);
        }
    }
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

/// The methods that accept keyword arguments at all, by receiver type.
///
/// Almost none do. `str.replace`, `str.strip`, `dict.get` and their neighbours
/// are C functions with positional-only parameters, so CPython answers
/// `TypeError: str.strip() takes no keyword arguments` — and lypning **silently
/// ignored** the keyword and answered without it. `'xax'.strip(chars='x')`
/// returned `'xax'` at exit 0, `'a'.ljust(width=5)` returned `'a'`, and
/// `d.get('b', default=2)` returned `None`. Every one of those is a plausible
/// spelling that CPython refuses and this accepted with the wrong answer.
///
/// Enumerated by asking CPython 3.11 rather than by reading the manual: each
/// name below was called with a keyword and kept only if it did not raise.
/// `str.format` and `dict.update` take arbitrary keywords by design and are the
/// reason this is a per-method allow-list and not a per-type flag.
fn accepts_kw(ty: &str, name: &str) -> bool {
    match ty {
        "str" => matches!(
            name,
            "split" | "rsplit" | "splitlines" | "encode" | "expandtabs" | "format" | "format_map"
        ),
        "bytes" => matches!(name, "decode" | "split" | "rsplit" | "splitlines" | "hex"),
        "list" => name == "sort",
        "dict" => name == "update",
        _ => false,
    }
}

/// CPython's own wording, and the check every dispatcher below runs first.
fn reject_kw(ty: &str, name: &str, kw: &[(Rc<str>, Value)]) -> R<()> {
    if kw.is_empty() || accepts_kw(ty, name) {
        return Ok(());
    }
    Err(type_err(format!("{ty}.{name}() takes no keyword arguments")))
}

/// How many POSITIONAL arguments each method takes, as `(min, max)`.
///
/// Extra arguments were silently dropped and missing ones silently defaulted,
/// so a malformed call answered instead of raising:
///
/// ```text
/// 'ab'.strip('a', 'b')   ->  'b'    cpython: TypeError   (the second arg won)
/// [1].insert(0)          ->  inserts None
/// [1].append(1, 2)       ->  appends 1, drops 2
/// {}.get()               ->  None
/// {}.get('a', 1, 2)      ->  1
/// [1].pop(0, 1)          ->  1
/// ```
///
/// Every one is exit 0 with a plausible-looking result, which is the shape this
/// project treats as always a bug: CPython tells the caller immediately and this
/// did something else quietly. `[1].insert(0)` is the worst of them — it does
/// not merely answer wrongly, it corrupts the list with a `None`.
///
/// Derived by calling CPython 3.11 with 0..6 arguments and recording which
/// counts it accepted, not by reading the manual. A name absent from this table
/// is unchecked, so adding a method does not silently acquire a wrong limit —
/// the arm's own argument handling stays the fallback. `str.format` and the
/// variadic set operations are deliberately absent for that reason.
fn arity(ty: &str, name: &str) -> Option<(usize, usize)> {
    let n = match (ty, name) {
        ("str", "replace") => (2, 3),
        ("str", "find" | "rfind" | "index" | "rindex" | "count" | "startswith" | "endswith") => (1, 3),
        ("str", "split" | "rsplit") => (0, 2),
        ("str", "strip" | "lstrip" | "rstrip") => (0, 1),
        ("str", "join" | "partition" | "rpartition" | "removeprefix" | "removesuffix" | "zfill") => (1, 1),
        ("str", "splitlines" | "expandtabs") => (0, 1),
        ("str", "encode") => (0, 2),
        ("str", "ljust" | "rjust" | "center") => (1, 2),
        ("str", "upper" | "lower" | "title" | "capitalize" | "swapcase" | "casefold") => (0, 0),
        ("bytes", "replace") => (2, 3),
        ("bytes", "find" | "rfind" | "index" | "rindex" | "count" | "startswith" | "endswith") => (1, 3),
        ("bytes", "split" | "rsplit") => (0, 2),
        ("bytes", "strip" | "lstrip" | "rstrip") => (0, 1),
        ("bytes", "join" | "partition" | "rpartition") => (1, 1),
        ("bytes", "splitlines") => (0, 1),
        ("bytes", "decode") => (0, 2),
        ("list", "append" | "remove" | "extend" | "count") => (1, 1),
        ("list", "insert") => (2, 2),
        ("list", "pop") => (0, 1),
        ("list", "index") => (1, 3),
        ("list", "sort" | "reverse" | "clear" | "copy") => (0, 0),
        ("dict", "get" | "pop" | "setdefault") => (1, 2),
        ("dict", "update") => (0, 1),
        ("dict", "keys" | "values" | "items" | "clear" | "copy") => (0, 0),
        ("set", "add" | "discard" | "remove") => (1, 1),
        ("set", "clear" | "copy" | "pop") => (0, 0),
        _ => return None,
    };
    Some(n)
}

/// CPython's wording, which distinguishes an exact count from a range.
fn plural(n: usize) -> &'static str {
    if n == 1 {
        "argument"
    } else {
        "arguments"
    }
}

fn check_arity(ty: &str, name: &str, args: &Args, kw: &[(Rc<str>, Value)]) -> R<()> {
    let Some((lo, hi)) = arity(ty, name) else { return Ok(()) };
    let n = args.len();
    // The floor counts POSITIONAL arguments, so it only applies when nothing was
    // passed by name: `round(number=2.5)` has none and is still a complete call.
    // The ceiling always applies — a keyword never makes an extra positional
    // legal.
    if n <= hi && (n >= lo || !kw.is_empty()) {
        return Ok(());
    }
    Err(type_err(if lo == hi {
        format!("{ty}.{name}() takes exactly {lo} {} ({n} given)", plural(lo))
    } else if n > hi {
        format!("{ty}.{name}() takes at most {hi} {} ({n} given)", plural(hi))
    } else {
        format!("{ty}.{name}() takes at least {lo} {} ({n} given)", plural(lo))
    }))
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
        Value::Dict(d) => {
            #[cfg(feature = "cap-collections")]
            if let Some(k) = crate::collections::kind_of(d) {
                return crate::collections::method(it, d, k, name, args, kw);
            }
            dict_method(it, d, name, args, kw)
        }
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
    reject_kw("str", name, &kw)?;
    check_arity("str", name, args, &kw)?;
    Ok(match name {
        // `std`'s `to_uppercase` / `to_lowercase` already carry a vectorised
        // ASCII fast path and size the output exactly. Measured on this
        // container, 200,000 iterations over a 960-byte ASCII string:
        // `to_lowercase` 17.3 ms against **306.6 ms** for a hand-rolled
        // `push(b.to_ascii_lowercase() as char)` loop — 18x slower, because
        // pushing one char at a time is exactly what the fast path exists to
        // avoid. Do not "optimise" these.
        "upper" => Value::Str(s.to_uppercase().into()),
        "lower" => Value::Str(s.to_lowercase().into()),
        "casefold" => {
            refuse_unmappable(s, casefold_differs, "casefold")?;
            Value::Str(s.to_lowercase().into())
        }
        // `.next()` here TRUNCATED every multi-character mapping: `'ß'` swapped
        // to `'S'` where CPython gives `'SS'`, `'İ'` to `'i'` where CPython gives
        // `'i̇'` (two codepoints), `'ǰ'` to `'J'` where CPython gives `'J̌'`. It is
        // `extend` now, which is the same thing `capitalize` and `title` below
        // were already doing correctly — one arm out of three had it wrong.
        "swapcase" => {
            let mut out = String::new();
            for c in s.chars() {
                if c.is_uppercase() {
                    out.extend(c.to_lowercase());
                } else if c.is_lowercase() {
                    out.extend(c.to_uppercase());
                } else {
                    // Neither, so CPython leaves it alone — and the `else`
                    // that used to uppercase everything else was wrong for
                    // every TITLECASE character: `'ǅ'.swapcase()` is `'ǅ'` in
                    // CPython and was `'Ǆ'` here. `Lt` is not `Lu`, and
                    // `is_uppercase()` is the Uppercase property, not "has a
                    // lowercase mapping".
                    out.push(c);
                }
            }
            Value::Str(out.into())
        }
        "capitalize" => {
            refuse_unmappable(s, titlecase_differs, "capitalize")?;
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
            refuse_unmappable(s, titlecase_differs, "title")?;
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
            // Nothing was trimmed, so the answer IS the receiver — hand back the
            // same `Rc` instead of allocating a copy of it. `trim_matches`
            // returns a subslice, so equal length is equal content.
            if t.len() == s.len() {
                Value::Str(s.clone())
            } else {
                Value::Str(t.into())
            }
        }
        "split" | "rsplit" => {
            let maxsplit = match args.get(1).cloned().or_else(|| kwget(&kw, "maxsplit")).as_ref() {
                Some(v) => int_val(v)?,
                None => -1,
            };
            // `maxsplit=` was read as a keyword and `sep=` was not, so
            // `'a,b'.split(sep=',')` split on WHITESPACE and answered `['a,b']`
            // at exit 0 — a wrong answer for an ordinary spelling, and one the
            // half-finished keyword handling right above it made easy to miss.
            let sep_arg = args.first().cloned().or_else(|| kwget(&kw, "sep"));
            let sep = match sep_arg.as_ref() {
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
                // The same rule the bytes twin uses — see `split_ws_ranges`.
                // It lived twice, and this copy was the one missing a case:
                // `'a b  c'.rsplit(None, 2)` refused here and answered there.
                None => {
                    let mut v: Vec<Value> = Vec::new();
                    split_ws_each(
                        s.len(),
                        |i| str_unit_at(s, i),
                        |j| str_unit_before(s, j),
                        maxsplit,
                        name == "rsplit",
                        |lo, hi| v.push(Value::Str(crate::value::substr(&s[lo..hi]))),
                    );
                    if name == "rsplit" {
                        v.reverse();
                    }
                    v
                }
                Some(sep) => {
                    if sep.is_empty() {
                        return Err(value_err("empty separator"));
                    }
                    if maxsplit < 0 {
                        s.split(sep.as_ref()).map(|x| Value::Str(crate::value::substr(x))).collect()
                    } else if name == "split" {
                        s.splitn(maxsplit as usize + 1, sep.as_ref())
                            .map(|x| Value::Str(crate::value::substr(x)))
                            .collect()
                    } else {
                        let mut v: Vec<Value> = s
                            .rsplitn(maxsplit as usize + 1, sep.as_ref())
                            .map(|x| Value::Str(crate::value::substr(x)))
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
        // Two things were wasted here and the larger one is invisible in the
        // code: `iter_collect` on a list **copies the whole list** before a
        // single byte is joined. On `''.join(['ab'] * 600000)` that is 24 MB of
        // `Value` moved to produce 1.2 MB of answer, and the copy is never read
        // twice. A list and a tuple are borrowed in place now; anything else
        // still materialises, because a generator has to be drained before it
        // can be measured.
        "join" => {
            let v = args
                .first()
                .cloned()
                .ok_or_else(|| type_err("join() missing 1 required positional argument"))?;
            if matches!(v, Value::Set(_)) {
                return Err(set_order_refused("join() over a set"));
            }
            match &v {
                Value::List(l) => join_parts(s, &l.borrow())?,
                Value::Tuple(t) => join_parts(s, t)?,
                _ => {
                    // `join` has its own message for a non-iterable and it is
                    // not the generic one: CPython says "can only join an
                    // iterable" where `iter_collect` produced "'int' object is
                    // not iterable". Same exit code, different sentence, and
                    // the sentence is what the caller reads.
                    //
                    // The remap is on `make_iter` ALONE and the draining loop is
                    // written out for that reason. Wrapping `iter_collect`
                    // instead — which is what this was for one build — swallows
                    // every exception the sequence raises *while being drained*:
                    // `','.join(str(1//x) for x in [1, 0])` reported "can only
                    // join an iterable" where CPython raises ZeroDivisionError.
                    // Turning one exception into a different one is the same
                    // class of defect as answering the wrong number.
                    let mut iter = it.make_iter(v).map_err(|e| {
                        if e.is_unsupported() {
                            e
                        } else {
                            type_err("can only join an iterable")
                        }
                    })?;
                    let mut items = Vec::new();
                    while let Some(x) = it.iter_next(&mut iter)? {
                        items.push(x);
                    }
                    join_parts(s, &items)?
                }
            }
        }
        "replace" => {
            let (from, to) = (sarg(&args, 0, "replace")?, sarg(&args, 1, "replace")?);
            let count = match args.get(2).cloned().or_else(|| kwget(&kw, "count")).as_ref() {
                Some(v) => int_val(v)?,
                None => -1,
            };
            // The needle is absent, so the answer IS the receiver — return the
            // same `Rc` rather than the full copy `str::replace` would build.
            // The corpus is full of `text.replace(old, new)` over whole files
            // where the needle is often not there at all.
            //
            // `find` and not a match COUNT: counting first so the output could
            // be presized was measured and it made `str-methods` **29% slower**
            // — a second pass over the receiver costs more than the growth it
            // saves at the sizes strings actually have here. `find` stops at the
            // first match, so the case that pays for it is the case that skips
            // an allocation entirely. (`s.find("")` is `Some(0)`, so the empty
            // needle falls through to `std`, which handles it exactly as
            // CPython does.)
            if count != 0 && s.find(from.as_ref()).is_none() {
                Value::Str(s.clone())
            } else if count < 0 {
                Value::Str(s.replace(from.as_ref(), &to).into())
            } else {
                Value::Str(s.replacen(from.as_ref(), &to, count as usize).into())
            }
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
                    // `str::find(char)` takes std's own single-character path;
                    // `str::find(&str)` builds the two-way searcher. Same answer,
                    // and for a one-byte needle the char form is the cheap one.
                    let byte_pos = match (one_ascii_byte(&needle), name.starts_with('r')) {
                        (Some(b), false) => sub.find(b as char),
                        (Some(b), true) => sub.rfind(b as char),
                        (None, false) => sub.find(needle.as_ref()),
                        (None, true) => sub.rfind(needle.as_ref()),
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
                    } else if let Some(b) = one_ascii_byte(&needle) {
                        sub.as_bytes().iter().filter(|&&x| x == b).count() as i64
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

/// `sep.join(parts)` over an already-materialised slice: measure, then fill.
///
/// The first pass sums the bytes and does the type check; the second writes into
/// a `String` that is already exactly the right size. `String::new()` doubled
/// its way to the answer, which for a 1.2 MB result is around twenty
/// reallocations and as many bytes copied again as the answer contains.
///
/// The type check staying in the FIRST pass is not incidental: CPython reports
/// `sequence item {i}` for the first non-str and produces no output, so finding
/// it before anything is written is what keeps the error identical.
fn join_parts(sep: &str, items: &[Value]) -> R<Value> {
    let mut total = 0usize;
    for (i, x) in items.iter().enumerate() {
        let Value::Str(xs) = x else {
            return Err(type_err(format!(
                "sequence item {i}: expected str instance, {} found",
                type_name(x)
            )));
        };
        total += xs.len();
    }
    total += sep.len() * items.len().saturating_sub(1);
    let mut out = String::with_capacity(total);
    for (i, x) in items.iter().enumerate() {
        if i > 0 {
            out.push_str(sep);
        }
        if let Value::Str(xs) = x {
            out.push_str(xs);
        }
    }
    Ok(Value::Str(out.into()))
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
    // No bounds, no work. Without this, `t.startswith('#')` — no `start`, no
    // `end` — walked the receiver THREE times before looking at the needle:
    // `chars().count()` to find `n`, then two `char_indices().nth()` walks to
    // turn 0 and `n` back into byte offsets it already had. Measured on a
    // 4,000-byte haystack that is ~69,000 instructions to answer an O(1)
    // question, and `line.startswith('#')` over a long line is an ordinary
    // one-liner.
    //
    // This is a scan removal, which the ledger's iteration-4 negative result
    // says usually buys nothing. That result was about shortening a FIXED
    // 39-entry table scan; this deletes three passes whose length is the
    // caller's data.
    if matches!(start, None | Some(Value::None)) && matches!(end, None | Some(Value::None)) {
        return Ok(Some((s, 0)));
    }
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
    let mut auto = 0usize;
    str_format_at(it, s, args, kw, &mut auto)
}

/// The body of [`str_format`], carrying the AUTO-NUMBERING COUNTER by reference.
///
/// A nested replacement field inside a spec draws from the same numbering as the
/// field it sits in: `"{:.{}f}".format(3.14159, 2)` gives the outer field
/// argument 0 and the inner one argument 1. Recursing into a fresh `str_format`
/// restarted the count, so the inner `{}` took argument 0 as well — the spec
/// became `.3.14159f` and the call raised `Invalid format specifier`, while
/// `"{:{}}".format(3.0, 5)` quietly built the spec `3.0` and answered `'3e+00'`.
/// Explicit numbering (`"{0:.{1}f}"`) never had the bug, which is why it
/// survived every example anyone wrote down.
fn str_format_at(
    it: &mut Interp,
    s: &str,
    args: &[Value],
    kw: &[(Rc<str>, Value)],
    auto: &mut usize,
) -> R<String> {
    let b: Vec<char> = s.chars().collect();
    let mut out = String::new();
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
                let (base, path) = match head.find(['.', '[']) {
                    Some(k) => (&head[..k], &head[k..]),
                    None => (head, ""),
                };
                // The FIELD takes its argument first, and only then does the
                // spec take its own. Expanding the spec first gave the inner
                // `{}` the number the outer field was about to claim.
                let mut v = if base.is_empty() {
                    let k = *auto;
                    *auto += 1;
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
                // Nested `{}` inside a spec resolve against the same arguments
                // AND the same auto-numbering counter.
                let spec = if spec.contains('{') {
                    str_format_at(it, &spec, args, kw, auto)?
                } else {
                    spec
                };
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
    reject_kw("list", name, &kw)?;
    check_arity("list", name, args, &kw)?;
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
            let vn = crate::value::nan_here(&v);
            let pos = {
                let b = l.borrow();
                let mut found = None;
                for (i, x) in b.iter().enumerate() {
                    if crate::value::eq(x, &v)? {
                        found = Some(i);
                        break;
                    }
                    if vn && crate::value::nan_here(x) {
                        return Err(crate::value::refuse_nan_elem());
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
            // `start` and `stop` were accepted and thrown away, so
            // `[1, 1, 1].index(1, 1)` answered 0 — the caller asked to skip the
            // first match and got it anyway — and `[1, 2, 3].index(3, 0, 2)`
            // answered 2 for an element outside the range it named, where
            // CPython raises. Both exit 0.
            //
            // The bounds are CLAMPED here, not the asymmetric `ADJUST_INDICES`
            // the string methods use: `list.index` raises rather than returning
            // -1, so "past the end" and "empty range" both end at the same
            // ValueError and the two rules cannot be told apart.
            let v = args.first().cloned().unwrap_or(Value::None);
            let b = l.borrow();
            let n = b.len() as i64;
            let lo = match args.get(1) {
                None | Some(Value::None) => 0,
                Some(x) => crate::eval::clamp_index(int_val(x)?, n),
            };
            let hi = match args.get(2) {
                None | Some(Value::None) => n,
                Some(x) => crate::eval::clamp_index(int_val(x)?, n),
            };
            let vn = crate::value::nan_here(&v);
            let mut i = lo;
            while i < hi {
                if crate::value::eq(&b[i as usize], &v)? {
                    return Ok(Value::Int(i));
                }
                if vn && crate::value::nan_here(&b[i as usize]) {
                    return Err(crate::value::refuse_nan_elem());
                }
                i += 1;
            }
            return Err(value_err(format!("{} is not in list", fmt::repr(&v)?)));
        }
        "count" => {
            let v = args.first().cloned().unwrap_or(Value::None);
            let b = l.borrow();
            let vn = crate::value::nan_here(&v);
            let mut n = 0;
            for x in b.iter() {
                if crate::value::eq(x, &v)? {
                    n += 1;
                } else if vn && crate::value::nan_here(x) {
                    return Err(crate::value::refuse_nan_elem());
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
            let keyf = crate::builtins::key_arg(&kw, "key");
            let rev = crate::builtins::reverse_arg(&kw)?;
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

pub(crate) fn dict_method(
    it: &mut Interp,
    d: &Rc<RefCell<Dict>>,
    name: &str,
    args: &mut Args,
    kw: Vec<(Rc<str>, Value)>,
) -> R<Value> {
    reject_kw("dict", name, &kw)?;
    check_arity("dict", name, args, &kw)?;
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
            for (k, v) in kw {
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
    kw: Vec<(Rc<str>, Value)>,
) -> R<Value> {
    reject_kw("set", name, &kw)?;
    check_arity("set", name, args, &kw)?;
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
            let vn = crate::value::nan_here(needle);
            let mut n = 0i64;
            for v in t.iter() {
                if crate::value::eq(v, needle)? {
                    n += 1;
                } else if vn && crate::value::nan_here(v) {
                    return Err(crate::value::refuse_nan_elem());
                }
            }
            Ok(Value::Int(n))
        }
        "index" => {
            // Kept in step with `list.index` above, which had the same defect.
            let n = t.len() as i64;
            let lo = match args.get(1) {
                None | Some(Value::None) => 0,
                Some(x) => crate::eval::clamp_index(int_val(x)?, n),
            };
            let hi = match args.get(2) {
                None | Some(Value::None) => n,
                Some(x) => crate::eval::clamp_index(int_val(x)?, n),
            };
            let vn = crate::value::nan_here(needle);
            let mut i = lo;
            while i < hi {
                if crate::value::eq(&t[i as usize], needle)? {
                    return Ok(Value::Int(i));
                }
                if vn && crate::value::nan_here(&t[i as usize]) {
                    return Err(crate::value::refuse_nan_elem());
                }
                i += 1;
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
    reject_kw("bytes", name, &kw)?;
    check_arity("bytes", name, args, &kw)?;
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
        "hex" => {
            // `b.hex('-')` groups the output; the separator was accepted and
            // dropped, so it answered the unseparated string at exit 0.
            // `bytes_per_sep` counts from the RIGHT when positive and from the
            // left when negative, which is why the grouping is computed against
            // the distance from the end.
            let sep = match args.first() {
                None => None,
                Some(Value::Str(x)) => Some(x.to_string()),
                Some(other) => {
                    return Err(type_err(format!(
                        "sep must be str or bytes, not {}",
                        type_name(other)
                    )))
                }
            };
            let per = match args.get(1) {
                Some(v) => int_val(v)?,
                None => 1,
            };
            let mut out = String::with_capacity(b.len() * 2);
            for (i, x) in b.iter().enumerate() {
                if let Some(sp) = &sep {
                    if i > 0 && per != 0 {
                        let boundary = if per > 0 {
                            (b.len() - i) % per as usize == 0
                        } else {
                            i % (-per) as usize == 0
                        };
                        if boundary {
                            out.push_str(sp);
                        }
                    }
                }
                out.push_str(&format!("{x:02x}"));
            }
            Value::Str(out.into())
        }
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
                None => py_byte_space(c),
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
            // Same keyword gap as `str.split` above, kept in step with it.
            let sep_arg = args.first().cloned().or_else(|| kwget(&kw, "sep"));
            let sep = match sep_arg.as_ref() {
                None | Some(Value::None) => None,
                Some(v) => Some(as_bytes_arg(v, name)?),
            };
            // `b"abc".split(b"")` is a ValueError in CPython, not a one-element
            // list. `str.split("")` already raised it; the bytes twin returned
            // the subject unsplit, at exit 0.
            if sep.as_deref() == Some(&[][..]) {
                return Err(value_err("empty separator"));
            }
            let maxsplit = match args.get(1).cloned().or_else(|| kwget(&kw, "maxsplit")) {
                Some(v) => crate::eval::int_val(&v)?,
                None => -1,
            };
            let parts = bytes_split(b, sep.as_deref(), maxsplit, name == "rsplit");
            list(parts.into_iter().map(|p| Value::Bytes(Rc::new(p))).collect())
        }
        "find" => {
            let needle = as_bytes_or_byte(args.first(), name)?;
            match slice_bytes(b, args.get(1), args.get(2))? {
                None => Value::Int(-1),
                Some((hay, off)) => Value::Int(match find_sub(hay, &needle) {
                    Some(i) => i as i64 + off,
                    None => -1,
                }),
            }
        }
        "startswith" | "endswith" => {
            // A TUPLE OF PREFIXES IS THE POINT OF THIS METHOD and it was not
            // accepted: `b"abc".startswith((b"a",))` is True in CPython and was
            // a TypeError here. `str.startswith` has taken a tuple since it was
            // written; the bytes twin never did.
            //
            // The wording is CPython's own and is NOT the shared bytes-like
            // message — note the unquoted type name. A tuple whose ELEMENTS are
            // wrong falls back to the shared one, because by then CPython is
            // checking an element rather than the first argument.
            let arg = args
                .first()
                .ok_or_else(|| type_err(format!("{name}() takes at least 1 argument")))?;
            let pats: Vec<Vec<u8>> = match arg {
                Value::Bytes(x) => vec![(**x).clone()],
                Value::Tuple(t) => t
                    .iter()
                    .map(|x| as_bytes_arg(x, name))
                    .collect::<R<Vec<_>>>()?,
                other => {
                    return Err(type_err(format!(
                        "{name} first arg must be bytes or a tuple of bytes, not {}",
                        crate::value::type_name(other)
                    )))
                }
            };
            match slice_bytes(b, args.get(1), args.get(2))? {
                None => Value::Bool(false),
                Some((hay, _)) => Value::Bool(pats.iter().any(|p| {
                    if name == "startswith" {
                        hay.starts_with(p)
                    } else {
                        hay.ends_with(p)
                    }
                })),
            }
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
            // The same map_err as `str.join` above, and the fourth place the
            // bytes twin had drifted from the str original: `b"".join(1)` said
            // "'int' object is not iterable" where CPython — and str.join right
            // here — say "can only join an iterable". A refusal from inside is
            // passed through untouched; only a real TypeError is reworded.
            let arg = if args.is_empty() {
                return Err(type_err("join() takes exactly one argument"));
            } else {
                args.take(0)
            };
            let items = it.iter_collect(arg).map_err(|e| {
                if e.is_unsupported() {
                    e
                } else {
                    type_err("can only join an iterable")
                }
            })?;
            let mut out: Vec<u8> = Vec::new();
            for (i, v) in items.iter().enumerate() {
                if i > 0 {
                    out.extend_from_slice(b);
                }
                let Value::Bytes(part) = v else {
                    return Err(type_err(format!(
                        "sequence item {i}: expected a bytes-like object, {} found",
                        crate::value::type_name(v)
                    )));
                };
                out.extend_from_slice(part);
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
fn as_bytes_arg(v: &Value, _method: &str) -> R<Vec<u8>> {
    match v {
        Value::Bytes(x) => Ok((**x).clone()),
        // CPython's message, EXACTLY. This used to append " (in bytes.split())"
        // — an annotation nothing in CPython prints, and 162 divergences in a
        // 642-program grid over the bytes methods, every one of them a message
        // an agent prints with str(e).
        other => Err(type_err(format!(
            "a bytes-like object is required, not '{}'",
            crate::value::type_name(other)
        ))),
    }
}

/// The message `find`, `rfind`, `index`, `rindex` and `count` use, which is not
/// the one every other bytes method uses: those five accept a single INTEGER
/// byte value as well as a bytes-like, and CPython's wording says so.
fn bytes_search_arg_err(v: &Value) -> LypningError {
    type_err(format!(
        "argument should be integer or bytes-like object, not '{}'",
        crate::value::type_name(v)
    ))
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
        Some(Value::Bytes(x)) => Ok((**x).clone()),
        Some(other) => Err(bytes_search_arg_err(other)),
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
/// Python's ASCII whitespace for `bytes`, which is NOT Rust's.
///
/// `u8::is_ascii_whitespace` is space, `\t`, `\n`, `\x0c`, `\r` — it leaves out
/// **`\x0b`, the vertical tab**, which CPython counts. One byte value, and it
/// made `b"a\vb".split()` answer `[b'a\x0bb']` and `b"\v".strip()` answer
/// `b'\x0b'`, both at exit 0. Found by sweeping all 256 byte values through
/// `split` and `strip`: exactly one differed.
///
/// The `str` side is a different set again (`py_space` above adds U+001C–U+001F
/// to Unicode White_Space) and was verified clean over the same sweep across
/// 0..=0x3000. These two must not be merged.
fn py_byte_space(c: u8) -> bool {
    matches!(c, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c)
}

/// `slice_str`'s rule for `bytes`: the same asymmetric `ADJUST_INDICES`, on
/// indices that are already byte offsets.
///
/// `bytes.find`, `startswith` and `endswith` took `start` and `end` and threw
/// them away, so `b"abcabc".find(b"a", 1)` answered 0 and
/// `b"abcabc".startswith(b"b", 1)` answered False — both exit 0, both wrong for
/// an argument the caller went out of their way to pass.
fn slice_bytes<'a>(b: &'a [u8], start: Option<&Value>, end: Option<&Value>) -> R<Option<(&'a [u8], i64)>> {
    if matches!(start, None | Some(Value::None)) && matches!(end, None | Some(Value::None)) {
        return Ok(Some((b, 0)));
    }
    let n = b.len() as i64;
    let lo = match start {
        None | Some(Value::None) => 0,
        Some(v) => {
            let raw = int_val(v)?;
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
    if lo > n {
        return Ok(None);
    }
    Ok(Some((&b[lo as usize..hi as usize], lo)))
}

/// The whitespace-split RULE, in one place for `str` and for `bytes`.
///
/// Fields are HANDED TO THE CALLER as they are found rather than returned in a
/// `Vec`: this replaced `str::split(py_space)`, which built the answer in one
/// pass, and an intermediate vector of ranges was measurably most of what was
/// left of a 21% regression. `from_right` emits right-to-left, so a caller that
/// asked for it reverses its own output once at the end.
///
/// It lived in two: `bytes_split`'s `None` arm and the `str.split` arm, and the
/// bytes copy's own comment said "this is the only place the rule lives". It was
/// not, and the str copy was the incomplete one — `'a b  c'.rsplit(None, 2)`
/// answered `unsupported: str-method` where the bytes twin answers
/// `[b'a', b'b', b'c']`. Two implementations of one rule, one of them missing a
/// case, and nothing making them agree: the same shape as the five drifts a grid
/// campaign found between these two functions this session.
///
/// Fields come back as **byte ranges**, so each caller slices its own type. A
/// `&str` range is always on a character boundary because `space_at` and
/// `space_before` report whole characters — which is also why this is not just
/// `bytes_split` called on `s.as_bytes()`: the two whitespace sets genuinely
/// differ. `str` splits on U+00A0, U+2000, U+3000, `\x1c` and `\x85`; `bytes`
/// splits on ASCII only, so `'a\xa0b'.split()` is `['a', 'b']` and
/// `b'a\xc2\xa0b'.split()` is one field.
///
/// The rule itself, which is not "split at every run":
///
///   * leading and trailing whitespace never produce an empty field;
///   * once `maxsplit` is spent the REMAINDER comes back verbatim, whitespace
///     and all — from the far end, so a bounded `rsplit` keeps the leading
///     whitespace and drops the trailing;
///   * `maxsplit < 0` means unbounded.
///
/// `from_right` walks backwards rather than reversing the input. The bytes
/// version built a reversed copy and reversed every field back, because a
/// hand-written mirror would have been "a second place to get that wrong" — but
/// with one implementation serving both types the mirror IS the one place, and
/// it costs no allocation.
/// The unit at an index: `(is_space, width_in_bytes)`. The WIDTH IS THE POINT —
/// a first version returned only "is this a space" and advanced the non-space
/// scan one byte at a time, which walks into the middle of a multi-byte
/// character and makes `&str` slicing panic. `'café'.split()` aborted the
/// process with a SIGABRT, and a 602-program grid missed it because every
/// subject in it was either ASCII or whitespace: there was no non-space
/// multi-byte character anywhere. Returning the width makes the mistake
/// unrepresentable — every advance is by a whole unit.
type Unit = (bool, usize);

fn split_ws_each(
    len: usize,
    at: impl Fn(usize) -> Unit,
    before: impl Fn(usize) -> Unit,
    maxsplit: i64,
    from_right: bool,
    mut emit: impl FnMut(usize, usize),
) {
    if !from_right {
        let mut i = 0usize;
        while i < len {
            let (sp, w) = at(i);
            if !sp {
                break;
            }
            i += w;
        }
        let mut splits = 0i64;
        while i < len {
            if maxsplit >= 0 && splits >= maxsplit {
                emit(i, len);
                return;
            }
            let start = i;
            while i < len {
                let (sp, w) = at(i);
                if sp {
                    break;
                }
                i += w;
            }
            emit(start, i);
            splits += 1;
            while i < len {
                let (sp, w) = at(i);
                if !sp {
                    break;
                }
                i += w;
            }
        }
        return;
    }
    let mut j = len;
    while j > 0 {
        let (sp, w) = before(j);
        if !sp {
            break;
        }
        j -= w;
    }
    let mut splits = 0i64;
    while j > 0 {
        if maxsplit >= 0 && splits >= maxsplit {
            emit(0, j);
            return;
        }
        let end = j;
        while j > 0 {
            let (sp, w) = before(j);
            if sp {
                break;
            }
            j -= w;
        }
        emit(j, end);
        splits += 1;
        while j > 0 {
            let (sp, w) = before(j);
            if !sp {
                break;
            }
            j -= w;
        }
    }
}

/// The `str` unit readers: one character, forwards or backwards.
///
/// The ASCII branch is not premature: this replaced `str::split(py_space)`, a
/// tuned iterator, and decoding a character per position to ask one question
/// cost **21%** on a whitespace-split microbenchmark — measured A/B against the
/// pre-refactor binary, against a ±5% noise floor. A byte below 0x80 IS its own
/// character and is one byte wide, which is the whole string for most programs;
/// the decode is kept for the case that actually needs it.
fn str_unit_at(s: &str, i: usize) -> Unit {
    let b = s.as_bytes()[i];
    if b < 0x80 {
        return (matches!(b, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c | 0x1c..=0x1f), 1);
    }
    match s[i..].chars().next() {
        Some(c) => (py_space(c), c.len_utf8()),
        None => (false, 1),
    }
}

fn str_unit_before(s: &str, j: usize) -> Unit {
    let b = s.as_bytes()[j - 1];
    if b < 0x80 {
        return (matches!(b, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c | 0x1c..=0x1f), 1);
    }
    match s[..j].chars().next_back() {
        Some(c) => (py_space(c), c.len_utf8()),
        None => (false, 1),
    }
}

fn bytes_split(b: &[u8], sep: Option<&[u8]>, maxsplit: i64, from_right: bool) -> Vec<Vec<u8>> {
    let mut out: Vec<Vec<u8>> = Vec::new();
    match sep {
        // The rule lives in `split_ws_ranges`, which `str.split` uses too.
        None => {
            split_ws_each(
                b.len(),
                |i| (py_byte_space(b[i]), 1),
                |j| (py_byte_space(b[j - 1]), 1),
                maxsplit,
                from_right,
                |lo, hi| out.push(b[lo..hi].to_vec()),
            );
            if from_right {
                out.reverse();
            }
        }
        Some(s) if from_right && maxsplit >= 0 => {
            // Same trick with a separator, and the separator is reversed too.
            let rev: Vec<u8> = b.iter().rev().copied().collect();
            let rsep: Vec<u8> = s.iter().rev().copied().collect();
            let mut parts = bytes_split(&rev, Some(&rsep), maxsplit, false);
            parts.reverse();
            return parts
                .into_iter()
                .map(|p| p.into_iter().rev().collect())
                .collect();
        }
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
