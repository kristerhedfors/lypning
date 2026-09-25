//! `binascii` — the whole of the `cap-binascii` capability, compiled into
//! `lypning-l` and into nothing smaller. Every line of this file, and every
//! line that reaches it, is behind `cfg(feature = "cap-binascii")`.
//!
//! Five names, all `bytes` in and `bytes` out, so there is **no new `Value`
//! variant** and no arm of the interpreter has to remember this module exists
//! (the defect `docs/HILLCLIMB.md` iterations 74, 76 and 77 paid for):
//!
//!   * `hexlify` / `b2a_hex` — lowercase hex of a `bytes`.
//!   * `unhexlify` / `a2b_hex` — the inverse, over `bytes` or an ASCII `str`.
//!   * `a2b_base64` — `base64.rs`'s lenient decoder, called, not copied: the
//!     3.11/3.12 against 3.13+ split that decoder refuses is `binascii.c`'s own
//!     loop, and a second decoder would be a second place to get it wrong.
//!   * `b2a_base64` — `base64.rs`'s encoder plus the trailing `\n` that
//!     `newline=True` (the default) appends.
//!
//! **Every `binascii.Error` is a refusal and never a raise.** The class does
//! not exist in this engine: it is a `ValueError` subclass whose `__name__` is
//! `Error` and whose traceback line is `binascii.Error: …`, and a raise that got
//! any one of those wrong would be a wrong answer under `except ValueError` or
//! `type(e).__name__`. So `binascii.Error` itself is an unserved attribute
//! (`except binascii.Error` refuses in the WALK, out of `route::MODULE_ATTRS`),
//! and every input that would raise one refuses too. The same holds for the
//! `TypeError` a `str` handed to `hexlify` raises and the `ValueError` a
//! non-ASCII `str` handed to `unhexlify` raises: the words are CPython's.
//!
//! **One decision, two callers.** [`block`] is asked by the walk
//! (`route::binascii_call_block`, over literals, before the program starts) and
//! by [`call`] (over the values, at runtime), so the two cannot disagree about
//! whether a call is served. What stays a runtime refusal is only what the
//! source does not spell — a computed argument.
//!
//! Not served, all refused as `module-attr` from the CORE's walk: `Error`,
//! `Incomplete`, `crc32` (no corpus program calls it; mined 2026-09-24),
//! `crc_hqx`, `b2a_uu`/`a2b_uu`, `b2a_qp`/`a2b_qp`. `hexlify`'s `sep` and
//! `bytes_per_sep`, and every keyword but `b2a_base64(newline=…)`, refuse.

use crate::args::Args;
use crate::err::{unsupported, R};
use crate::value::Value;
use std::rc::Rc;

/// The names this module serves — `route::BINASCII_SERVED` itself, so the
/// router's table and this file are one list.
use crate::route::BINASCII_SERVED as SERVED;

/// `binascii.<name>` as a value: a served name is a bound module method, every
/// other one refuses with the kind the router blocks on statically.
pub fn module_attr(name: &str) -> R<Value> {
    match SERVED.iter().find(|n| **n == name) {
        Some(n) => Ok(Value::Bound(Rc::new(Value::Module("binascii")), n)),
        None => Err(unsupported("module-attr", &format!("binascii.{name}"))),
    }
}

/// Would this call refuse, and with what `(kind, detail)`? The whole of the
/// served surface, asked in CPython's own order of checks.
///
/// `npos` is the positional count, `kws` each keyword with its truth value when
/// that is known (`None`: not a value this engine reads truthiness of the way
/// CPython does), and `arg` the first positional as its type name plus, when
/// known, its bytes (a `str`'s UTF-8). `arg == None` means the walk cannot see
/// the argument's type, so only the call's SHAPE is decided and the rest is
/// the runtime's.
pub fn block(
    name: &str,
    npos: usize,
    kws: &[(&str, Option<bool>)],
    arg: Option<(&str, Option<&[u8]>)>,
) -> Option<(&'static str, String)> {
    let shape = |d: String| Some(("binascii", d));
    match npos {
        0 => return shape(format!("binascii.{name}() with no positional argument")),
        1 => {}
        // `hexlify(data, sep, bytes_per_sep)` is real in CPython; the
        // grouping is not implemented here. On the others it is a TypeError.
        _ => return shape(format!("binascii.{name}() with extra positional arguments (sep/bytes_per_sep are not served)")),
    }
    for (k, truth) in kws {
        if name == "b2a_base64" && *k == "newline" && truth.is_some() {
            continue;
        }
        return shape(format!("binascii.{name}({k}=…)"));
    }
    let (t, data) = arg?;
    match name {
        "hexlify" | "b2a_hex" | "b2a_base64" => (t != "bytes").then(|| {
            ("binascii", format!("binascii.{name}() over a {t} (CPython requires a bytes-like object)"))
        }),
        _ => {
            if t != "bytes" && t != "str" {
                return shape(format!(
                    "binascii.{name}() over a {t} (CPython requires bytes or an ASCII str)"
                ));
            }
            let data = data?;
            if t == "str" && !data.is_ascii() {
                return shape(format!(
                    "binascii.{name}() over a str holding a non-ASCII character (CPython raises a ValueError this engine does not word)"
                ));
            }
            if name == "a2b_base64" {
                return crate::base64::data_block(data, false)
                    .map(|why| ("binascii", format!("binascii.a2b_base64(): {why}")));
            }
            if data.len() % 2 == 1 {
                return shape(format!(
                    "binascii.{name}() over an odd-length string (CPython raises binascii.Error, which this engine does not have)"
                ));
            }
            if !data.iter().all(u8::is_ascii_hexdigit) {
                return shape(format!(
                    "binascii.{name}() over a non-hexadecimal digit (CPython raises binascii.Error, which this engine does not have)"
                ));
            }
            None
        }
    }
}

/// The truth value of a keyword's VALUE, for the few types whose truthiness is
/// fixed; anything else is `None` and refuses — the safe direction. Before
/// 3.12 `newline` was converted as a C `int` rather than by truth, so on such
/// a reference only a `bool` or an `int` in C `int` range is read.
fn truth(v: &Value) -> Option<bool> {
    if crate::err::REF_PY_MINOR < 12 {
        return match v {
            Value::Bool(b) => Some(*b),
            Value::Int(i) => i.small().filter(|n| *n as i32 as i64 == *n).map(|n| n != 0),
            _ => None,
        };
    }
    match v {
        Value::None => Some(false),
        Value::Bool(b) => Some(*b),
        Value::Int(i) => Some(!i.is_zero()),
        Value::Str(s) => Some(!s.is_empty()),
        Value::Bytes(b) => Some(!b.is_empty()),
        _ => None,
    }
}

pub fn call(_it: &mut crate::eval::Interp, name: &str, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    let kws: Vec<(&str, Option<bool>)> = kw.iter().map(|(k, v)| (k.as_ref(), truth(v))).collect();
    let arg = args.first().map(|v| match v {
        Value::Bytes(b) => ("bytes", Some(b.as_slice())),
        Value::Str(s) => ("str", Some(s.as_bytes())),
        v => (crate::value::type_name(v), None),
    });
    if let Some((k, d)) = block(name, args.len(), &kws, arg) {
        return Err(unsupported(k, &d));
    }
    let data: &[u8] = match args.first() {
        Some(Value::Bytes(b)) => b.as_slice(),
        Some(Value::Str(s)) => s.as_bytes(),
        // `block` admits nothing else once it has seen the value.
        _ => return Err(unsupported("binascii", &format!("binascii.{name}()"))),
    };
    let out = match name {
        "hexlify" | "b2a_hex" => hexlify(data),
        "unhexlify" | "a2b_hex" => unhexlify(data),
        "a2b_base64" => match crate::base64::scan(data, false) {
            Ok(v) => v,
            Err(why) => return Err(unsupported("binascii", why)),
        },
        _ => {
            let mut v = crate::base64::encode(data, false);
            if kws.iter().all(|(_, t)| *t != Some(false)) {
                v.push(b'\n');
            }
            v
        }
    };
    Ok(Value::Bytes(Rc::new(out)))
}

fn hexlify(data: &[u8]) -> Vec<u8> {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = Vec::with_capacity(data.len() * 2);
    for b in data {
        out.push(HEX[(b >> 4) as usize]);
        out.push(HEX[(b & 15) as usize]);
    }
    out
}

/// Over input [`block`] has already checked: even length, hex digits only.
fn unhexlify(data: &[u8]) -> Vec<u8> {
    let nib = |c: u8| (c as char).to_digit(16).unwrap_or(0) as u8;
    data.chunks(2).map(|p| (nib(p[0]) << 4) | nib(p[1])).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hex_round_trips_and_the_raising_inputs_refuse() {
        let all: Vec<u8> = (0..=255u8).collect();
        let h = hexlify(&all);
        assert_eq!(&h[..6], b"000102");
        assert_eq!(unhexlify(&h), all);
        assert_eq!(unhexlify(b"0aFf"), vec![0x0a, 0xff]);
        let one = |d: &[u8]| block("unhexlify", 1, &[], Some(("bytes", Some(d))));
        assert!(one(b"0aFf").is_none());
        assert!(one(b"").is_none());
        assert!(one(b"0").is_some());
        assert!(one(b"0g").is_some());
        assert!(one(b" 00").is_some());
        assert!(block("unhexlify", 1, &[], Some(("str", Some("é0".as_bytes())))).is_some());
        assert!(block("hexlify", 1, &[], Some(("str", Some(b"ab")))).is_some());
        assert!(block("hexlify", 2, &[], None).is_some());
        assert!(block("b2a_base64", 1, &[("newline", Some(false))], Some(("bytes", None))).is_none());
        assert!(block("b2a_base64", 1, &[("newline", None)], Some(("bytes", None))).is_some());
        assert!(block("a2b_base64", 1, &[("strict_mode", Some(false))], None).is_some());
        assert!(block("a2b_base64", 1, &[], Some(("bytes", Some(b"AA==AA==")))).is_some());
    }

    /// `route::BINASCII_SERVED` is read by the CORE, which has none of this
    /// file compiled in — so the variant that DOES have `module_attr` holds the
    /// two lists to each other.
    #[test]
    fn the_route_table_names_exactly_what_is_served() {
        let table = crate::route::MODULE_ATTRS
            .iter()
            .find(|(m, _)| *m == "binascii")
            .expect("route::MODULE_ATTRS has no binascii row")
            .1;
        for name in table {
            assert!(module_attr(name).is_ok(), "route claims binascii.{name} and binascii.rs refuses it");
        }
        for name in SERVED {
            assert!(table.contains(name), "binascii.rs serves binascii.{name} and route::MODULE_ATTRS omits it");
        }
        assert!(table.windows(2).all(|w| w[0] < w[1]), "route::MODULE_ATTRS binascii row is unsorted");
        for name in ["Error", "crc32", "Incomplete", "b2a_uu"] {
            assert!(module_attr(name).is_err(), "binascii.{name} must refuse");
        }
    }
}

/// `bytes.fromhex(s)` — on the type or an instance, a classmethod either way.
///
/// Pairs of hex digits, with ASCII whitespace (` \t\n\r\x0b\x0c`) skipped
/// BETWEEN pairs, as CPython 3.7+ does. Everything CPython raises for — a
/// digit that is not hex, a lone or split digit, whitespace inside a pair —
/// refuses, and so does a non-`str` argument (3.14 also takes bytes-like).
pub fn fromhex(args: &Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    // The core lacks this: from here the run must stay refusable (`io::hold`).
    crate::io::hold();
    let s = match (args.len(), kw.is_empty(), args.first()) {
        (1, true, Some(Value::Str(s))) => s.clone(),
        _ => return Err(unsupported("binascii", "bytes.fromhex() of anything but one str")),
    };
    let bad = || unsupported("binascii", "bytes.fromhex() of text CPython rejects");
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len() / 2);
    let mut i = 0;
    while i < b.len() {
        if matches!(b[i], b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c) {
            i += 1;
            continue;
        }
        let hi = (b[i] as char).to_digit(16).ok_or_else(bad)?;
        let lo = b.get(i + 1).and_then(|c| (*c as char).to_digit(16)).ok_or_else(bad)?;
        out.push((hi * 16 + lo) as u8);
        i += 2;
    }
    Ok(Value::Bytes(Rc::new(out)))
}
