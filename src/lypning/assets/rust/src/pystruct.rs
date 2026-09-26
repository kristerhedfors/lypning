//! `struct` — `pack`, `unpack`, `unpack_from` and `calcsize`, the second half
//! of the `cap-binascii` capability (binary data in, binary data out). Compiled
//! into `lypning-l` and into nothing smaller: every line of this file, and
//! every line that reaches it, is behind `cfg(feature = "cap-binascii")`.
//!
//! No new `Value` variant: a format is a `str`, a packed record is `bytes`,
//! and an unpacked one is a `tuple` of `int`, `float`, `bool` and `bytes`.
//!
//! **What is served.** A format of ASCII only, no whitespace: an optional
//! byte-order character `<` `>` `!` `=` or `@`, then items `[count]code` over
//! the codes `x c b B ? h H i I l L q Q s d`. The integer codes take an `int`
//! or a `bool`, `d` a `float` or an `int` that fits a machine word, `?` the
//! truth of a scalar, `c` one byte and `s` bytes (truncated or NUL-padded to
//! the count). `unpack` takes `bytes` of exactly `calcsize` bytes, and
//! `unpack_from` a non-negative offset, positional or as `offset=`.
//!
//! **Native mode** (`@`, or no order character) is served only on a 64-bit,
//! little-endian, non-Windows target, and never for `l L n N P`, whose sizes
//! are the platform's: every other code's alignment is its size, which is what
//! `offsetof` gives on every such target. CPython aligns BEFORE each item, even
//! at a count of 0 (`calcsize('@c0q') == 8`), and never pads the end
//! (`calcsize('@dI') == 12`). A native `?` is read through a C `_Bool`, whose
//! answer for a byte other than 0 or 1 differs between CPython versions
//! (probed: `unpack('?', b'\x02')` is `(False,)` on 3.11 and `(True,)` on
//! 3.14), so such a byte refuses; the standard `?` is `!= 0` on every version.
//!
//! **Every error is a refusal and never a raise.** `struct.error` does not
//! exist in this engine, and its texts changed in 3.12 and again in 3.14
//! (`'argument out of range'` / `'ubyte format requires 0 <= number <= 255'` /
//! `"'B' format requires 0 <= number <= 255"`), so a value out of range, a
//! wrong item count, a buffer of the wrong size and a bad format character all
//! refuse — as do the `TypeError`s: a wrong arity, a keyword other than
//! `unpack_from`'s `offset=`, a `bytes` format, a `str` buffer or `s` item.
//! A NaN refuses to pack (its bits depend on how it was made and on the
//! hardware); unpacking one is exact. `f` and `e` refuse (3.14 changed how a
//! float32 NaN packs), and so do `n N P p`, `Struct`, `error`, `pack_into` and
//! `iter_unpack` — the names as `module-attr`.
//!
//! **Routing.** The core stops on `module: import struct` and
//! `route::CAPS`'s `cap-binascii` row names `struct`, so the program goes to
//! `lypning-l` — and `modules::import` holds the run there (`io::hold`) for
//! that same reason, so a refusal below stays possible. There is deliberately
//! no `route::MODULE_ATTRS` row (it would cost the frozen core bytes) and no
//! pre-run stop in any walk: an unserved name refuses where the run reaches it,
//! and a program that never reaches `import struct` is the core's answer.

use crate::args::Args;
use crate::err::{unsupported, LypningError, R};
use crate::value::{type_name, Int, Value};
use std::rc::Rc;

/// The names `struct` serves here — `docs/DIFFERENCES.md` §4.3's row is held
/// to this list by `tests/test_differences.py`.
pub const SERVED: &[&str] = &["calcsize", "pack", "unpack", "unpack_from"];

/// Native layout is served only where every served code's alignment is its
/// size and the bytes are little-endian: 64-bit Unix on x86_64 or aarch64.
const NATIVE: bool = cfg!(all(target_pointer_width = "64", target_endian = "little", not(windows)));

/// The largest record `pack` builds. CPython would allocate more; a record
/// past this is refused rather than held in memory twice.
const MAX_PACK: usize = 1 << 26;

fn refuse(what: &str) -> LypningError {
    unsupported("struct", what)
}

/// `struct.<name>` as a value: a served name is a bound module method, every
/// other one refuses with the `module-attr` kind.
pub fn module_attr(name: &str) -> R<Value> {
    match SERVED.iter().find(|n| **n == name) {
        Some(n) => Ok(Value::Bound(Rc::new(Value::Module("struct")), n)),
        None => Err(unsupported("module-attr", &format!("struct.{name}"))),
    }
}

/// One compiled item: its code, repeat count and byte offset in the record.
#[derive(Clone, Copy, Debug, PartialEq)]
struct Item {
    code: u8,
    count: usize,
    offset: usize,
}

/// A compiled format: the items, the record size and the byte order.
#[derive(Debug, PartialEq)]
struct Layout {
    items: Vec<Item>,
    size: usize,
    big: bool,
    native: bool,
}

/// Standard size of a served code, or `None` for one that is not served.
fn std_size(code: u8) -> Option<usize> {
    Some(match code {
        b'x' | b'c' | b'b' | b'B' | b'?' | b's' => 1,
        b'h' | b'H' => 2,
        b'i' | b'I' | b'l' | b'L' => 4,
        b'q' | b'Q' | b'd' => 8,
        _ => return None,
    })
}

/// `Struct(fmt)`'s compile step — `prepare_s` in `_struct.c` — over the served
/// subset. Every format CPython rejects, and every one it accepts that is not
/// served, is an `Err` with the reason.
fn compile(fmt: &str) -> Result<Layout, String> {
    let b = fmt.as_bytes();
    if !fmt.is_ascii() {
        return Err("a format with a non-ASCII character".into());
    }
    if b.iter().any(|c| c.is_ascii_whitespace() || *c == 0x0b) {
        // CPython skips whitespace between items; its rules have moved
        // between versions (`calcsize('< I')`), so none is served.
        return Err("a format with whitespace".into());
    }
    let (big, native, rest) = match b.first() {
        Some(b'<') => (false, false, &b[1..]),
        Some(b'>' | b'!') => (true, false, &b[1..]),
        Some(b'=') => (false, false, &b[1..]),
        Some(b'@') => (false, true, &b[1..]),
        _ => (false, true, b),
    };
    if b.first() == Some(&b'=') && !cfg!(target_endian = "little") {
        return Err("'=' order on a big-endian host".into());
    }
    if native && !NATIVE {
        return Err("native layout on a host that is not 64-bit little-endian Unix".into());
    }
    let mut items = Vec::new();
    let mut size: usize = 0;
    let mut i = 0;
    while i < rest.len() {
        let mut count: usize = 1;
        if rest[i].is_ascii_digit() {
            count = 0;
            while i < rest.len() && rest[i].is_ascii_digit() {
                count = count * 10 + (rest[i] - b'0') as usize;
                if count > i32::MAX as usize {
                    return Err("a repeat count past 2**31-1".into());
                }
                i += 1;
            }
            if i == rest.len() {
                return Err("a repeat count given without a format character".into());
            }
        }
        let code = rest[i];
        i += 1;
        let item = match std_size(code) {
            Some(n) => n,
            None => {
                return Err(format!(
                    "the format character {:?} (not served, or struct.error)",
                    code as char
                ))
            }
        };
        if native && matches!(code, b'l' | b'L') {
            return Err(format!("native '{}', whose size is the platform's", code as char));
        }
        // Native alignment BEFORE the item, even at a count of 0 — and none
        // after the last.
        if native && item > 1 {
            size = (size + item - 1) / item * item;
        }
        items.push(Item { code, count, offset: size });
        size = count
            .checked_mul(item)
            .and_then(|n| size.checked_add(n))
            .filter(|s| *s <= i32::MAX as usize)
            .ok_or_else(|| "a record past 2**31-1 bytes".to_string())?;
    }
    Ok(Layout { items, size, big, native })
}

/// How many arguments `pack` takes for this layout — CPython's `s_len`: one per
/// `s`, none per `x`, `count` for every other code.
fn arg_count(l: &Layout) -> usize {
    l.items
        .iter()
        .map(|it| match it.code {
            b's' => 1,
            b'x' => 0,
            _ => it.count,
        })
        .sum()
}

/// An integer argument as an `i128`: an `int` of any width up to 64 bits, or a
/// `bool`. `None` for a float, a wider `int` and every other type — all of
/// which CPython answers with a `struct.error` this engine does not raise.
fn as_i128(v: &Value) -> Option<i128> {
    match v {
        Value::Bool(b) => Some(*b as i128),
        Value::Int(Int::S(i)) => Some(*i as i128),
        #[cfg(feature = "cap-bigint")]
        Value::Int(Int::B(b)) => {
            if crate::bigint::bit_length(b) > 64 {
                return None;
            }
            let m = b.mag.iter().rev().fold(0u128, |a, w| (a << 32) | *w as u128) as i128;
            Some(if b.neg { -m } else { m })
        }
        _ => None,
    }
}

fn int_range(code: u8) -> (i128, i128) {
    match code {
        b'b' => (-128, 127),
        b'B' => (0, 255),
        b'h' => (-32_768, 32_767),
        b'H' => (0, 65_535),
        b'i' | b'l' => (i32::MIN as i128, i32::MAX as i128),
        b'I' | b'L' => (0, u32::MAX as i128),
        b'q' => (i64::MIN as i128, i64::MAX as i128),
        _ => (0, u64::MAX as i128),
    }
}

/// One item into `out`, `size` bytes wide.
fn pack_one(code: u8, v: &Value, size: usize, big: bool, out: &mut Vec<u8>) -> R<()> {
    let put = |out: &mut Vec<u8>, bits: u64| {
        let le = bits.to_le_bytes();
        if big {
            out.extend(le[..size].iter().rev());
        } else {
            out.extend_from_slice(&le[..size]);
        }
    };
    match code {
        b'd' => {
            let f = match v {
                Value::Float(f) => *f,
                Value::Bool(b) => *b as i64 as f64,
                // `i64 as f64` rounds to nearest, ties to even — CPython's
                // `int.__float__`. A wider int refuses.
                Value::Int(Int::S(i)) => *i as f64,
                _ => return Err(refuse(&format!("struct.pack('d') of a {}", type_name(v)))),
            };
            if f.is_nan() {
                return Err(refuse("struct.pack('d') of a NaN, whose bits are the hardware's"));
            }
            put(out, f.to_bits());
        }
        b'?' => {
            let t = match v {
                Value::None => false,
                Value::Bool(b) => *b,
                Value::Int(n) => !n.is_zero(),
                Value::Float(f) => *f != 0.0,
                Value::Str(s) => !s.is_empty(),
                Value::Bytes(b) => !b.is_empty(),
                _ => return Err(refuse(&format!("struct.pack('?') of a {}", type_name(v)))),
            };
            out.push(t as u8);
        }
        b'c' => match v {
            Value::Bytes(b) if b.len() == 1 => out.push(b[0]),
            _ => return Err(refuse("struct.pack('c') of anything but one byte")),
        },
        _ => {
            let n = as_i128(v).ok_or_else(|| {
                refuse(&format!("struct.pack('{}') of a {}", code as char, type_name(v)))
            })?;
            let (lo, hi) = int_range(code);
            if n < lo || n > hi {
                return Err(refuse(&format!(
                    "struct.pack('{}') of a value out of range (struct.error, worded by version)",
                    code as char
                )));
            }
            put(out, n as u64);
        }
    }
    Ok(())
}

fn pack(l: &Layout, vals: &[Value]) -> R<Vec<u8>> {
    if vals.len() != arg_count(l) {
        return Err(refuse(&format!(
            "struct.pack() of {} items for a format of {} (struct.error)",
            vals.len(),
            arg_count(l)
        )));
    }
    if l.size > MAX_PACK {
        return Err(refuse("struct.pack() of a record past 64 MiB"));
    }
    let mut out = Vec::with_capacity(l.size);
    let mut vals = vals.iter();
    for it in &l.items {
        out.resize(it.offset, 0);
        match it.code {
            b'x' => out.resize(it.offset + it.count, 0),
            b's' => {
                let data = match vals.next() {
                    Some(Value::Bytes(b)) => b,
                    _ => return Err(refuse("struct.pack('s') of anything but bytes")),
                };
                let n = it.count.min(data.len());
                out.extend_from_slice(&data[..n]);
                out.resize(it.offset + it.count, 0);
            }
            code => {
                let size = std_size(code).unwrap_or(1);
                for _ in 0..it.count {
                    let v = vals.next().ok_or_else(|| refuse("struct.pack() item count"))?;
                    pack_one(code, v, size, l.big, &mut out)?;
                }
            }
        }
    }
    out.resize(l.size, 0);
    Ok(out)
}

fn unpack(l: &Layout, data: &[u8]) -> R<Value> {
    let mut out = Vec::new();
    for it in &l.items {
        match it.code {
            b'x' => {}
            b's' => out.push(Value::Bytes(Rc::new(data[it.offset..it.offset + it.count].to_vec()))),
            code => {
                let size = std_size(code).unwrap_or(1);
                for k in 0..it.count {
                    let at = it.offset + k * size;
                    let raw = &data[at..at + size];
                    let mut le = [0u8; 8];
                    for (j, byte) in raw.iter().enumerate() {
                        le[if l.big { size - 1 - j } else { j }] = *byte;
                    }
                    let u = u64::from_le_bytes(le);
                    let shift = 64 - 8 * size as u32;
                    out.push(match code {
                        b'd' => Value::Float(f64::from_bits(u)),
                        b'c' => Value::Bytes(Rc::new(vec![raw[0]])),
                        b'?' => {
                            if l.native && raw[0] > 1 {
                                return Err(refuse(
                                    "struct.unpack('?') of a byte other than 0 or 1 in native \
                                     mode (a C _Bool, read differently by version)",
                                ));
                            }
                            Value::Bool(raw[0] != 0)
                        }
                        b'b' | b'h' | b'i' | b'l' | b'q' => {
                            Value::Int(Int::S(((u << shift) as i64) >> shift))
                        }
                        _ => wide_u64(u)?,
                    });
                }
            }
        }
    }
    Ok(Value::Tuple(Rc::new(out)))
}

/// An unsigned 64-bit field: a small `int`, or a wide one past `i64::MAX`.
fn wide_u64(u: u64) -> R<Value> {
    if u <= i64::MAX as u64 {
        return Ok(Value::Int(Int::S(u as i64)));
    }
    #[cfg(feature = "cap-bigint")]
    return Ok(Value::Int(crate::bigint::norm(false, vec![u as u32, (u >> 32) as u32])));
    #[cfg(not(feature = "cap-bigint"))]
    Err(refuse("struct.unpack('Q') past 2**63 without cap-bigint"))
}

/// The format argument: a `str` only. CPython also takes `bytes`; refused.
fn format_of(name: &str, v: Option<&Value>) -> R<Layout> {
    match v {
        Some(Value::Str(s)) => compile(s).map_err(|why| refuse(&format!("struct.{name}(): {why}"))),
        Some(v) => Err(refuse(&format!("struct.{name}() with a {} format", type_name(v)))),
        None => Err(refuse(&format!("struct.{name}() with no format"))),
    }
}

pub fn call(_it: &mut crate::eval::Interp, name: &str, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    let arity = |ok: bool| -> R<()> {
        if ok {
            Ok(())
        } else {
            // The TypeError's text is version-shaped; refused, not raised.
            Err(refuse(&format!("struct.{name}() with {} positional argument(s)", args.len())))
        }
    };
    let mut offset: Option<&Value> = None;
    for (k, v) in kw {
        if name == "unpack_from" && k.as_ref() == "offset" && offset.is_none() {
            offset = Some(v);
            continue;
        }
        return Err(refuse(&format!("struct.{name}({k}=…)")));
    }
    let layout = format_of(name, args.first())?;
    match name {
        "calcsize" => {
            arity(args.len() == 1)?;
            Ok(Value::Int(Int::S(layout.size as i64)))
        }
        "pack" => Ok(Value::Bytes(Rc::new(pack(&layout, &args[1..])?))),
        "unpack" | "unpack_from" => {
            if name == "unpack" {
                arity(args.len() == 2)?;
            } else {
                arity(args.len() == 2 || (args.len() == 3 && offset.is_none()))?;
                if args.len() == 3 {
                    offset = Some(&args[2]);
                }
            }
            let data: &[u8] = match &args[1] {
                Value::Bytes(b) => b.as_slice(),
                v => {
                    return Err(refuse(&format!(
                        "struct.{name}() over a {} (bytearray and memoryview do not exist here)",
                        type_name(v)
                    )))
                }
            };
            let at = match offset {
                None => 0,
                Some(Value::Int(Int::S(n))) if *n >= 0 => *n as usize,
                // A negative offset counts from the end in CPython; a bool
                // is an int there. Neither is served.
                Some(v) => {
                    return Err(refuse(&format!(
                        "struct.unpack_from() with an offset that is a negative int or a {}",
                        type_name(v)
                    )))
                }
            };
            let fits = if name == "unpack" {
                data.len() == layout.size
            } else {
                at <= data.len() && data.len() - at >= layout.size
            };
            if !fits {
                return Err(refuse(&format!(
                    "struct.{name}() over a buffer of the wrong size (struct.error)"
                )));
            }
            unpack(&layout, &data[at..at + layout.size])
        }
        _ => Err(unsupported("module-attr", &format!("struct.{name}()"))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn size(f: &str) -> Option<usize> {
        compile(f).ok().map(|l| l.size)
    }

    #[test]
    fn standard_sizes_have_no_alignment() {
        assert_eq!(size("<8I"), Some(32));
        assert_eq!(size("<Id"), Some(12));
        assert_eq!(size(">bhiqd"), Some(23));
        assert_eq!(size("=lL"), Some(8));
        assert_eq!(size("!3s2x?"), Some(6));
        assert_eq!(size(""), Some(0));
        assert_eq!(size("<"), Some(0));
        assert_eq!(size("<0I"), Some(0));
    }

    #[test]
    fn native_aligns_before_each_item_and_never_after_the_last() {
        if !NATIVE {
            assert!(compile("Id").is_err());
            return;
        }
        assert_eq!(size("@Id"), Some(16));
        assert_eq!(size("Id"), Some(16));
        assert_eq!(size("@dI"), Some(12));
        assert_eq!(size("@hq?"), Some(17));
        assert_eq!(size("@c0q"), Some(8));
        assert_eq!(size("@0d"), Some(0));
        assert_eq!(size("@bh"), Some(4));
        assert_eq!(size("@3sI"), Some(8));
        assert!(compile("@l").is_err());
        assert!(compile("L").is_err());
    }

    #[test]
    fn what_cpython_rejects_or_this_engine_does_not_serve_is_an_error() {
        for f in ["< I", "<\tI", "<²i", "<f", "<e", "<n", "<P", "<p", "<3", "<z", "<4294967296x", "I "] {
            assert!(compile(f).is_err(), "{f:?} must not compile");
        }
    }

    #[test]
    fn the_route_table_names_struct_in_the_binascii_row() {
        let row = crate::route::CAPS.iter().find(|r| r.0 == "cap-binascii").expect("no cap-binascii row");
        assert!(row.1.contains(&"struct"));
        for n in SERVED {
            assert!(module_attr(n).is_ok());
        }
        for n in ["error", "Struct", "pack_into", "iter_unpack"] {
            assert!(module_attr(n).is_err(), "struct.{n} must refuse");
        }
    }
}
