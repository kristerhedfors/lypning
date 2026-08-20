//! `str()`, `repr()` and the format-spec mini-language.
//!
//! This file is where a subset runtime most easily produces a *plausible wrong
//! answer*, because every one of these is a rule CPython implements exactly and
//! an independent implementation approximates. So each of the three known traps
//! is either implemented to the letter or refused outright:
//!
//!   1. **float repr** is shortest-roundtrip with a fixed/scientific switch at
//!      `decpt <= -4 || decpt > 16`. Rust's `{}` never uses scientific notation
//!      and `{:e}` always does, so neither is usable directly; we take the
//!      shortest digits from `{:e}` and reassemble under Python's rule.
//!   2. **str repr quoting** follows CPython: prefer `'`, switch to `"` only
//!      when the string holds a `'` and no `"`.
//!   3. **non-ASCII printability** needs Unicode category tables to decide
//!      whether `repr` escapes a character. We carry a whitelist of blocks that
//!      are unambiguously printable and REFUSE the rest rather than guess.

use crate::err::{unsupported, value_err, R};
use crate::value::{set_order_refused, type_name, Dict, Value};

pub fn to_str(v: &Value) -> R<String> {
    Ok(match v {
        Value::Str(s) => s.to_string(),
        Value::Exc(_, m) => m.to_string(),
        _ => repr(v)?,
    })
}

pub fn repr(v: &Value) -> R<String> {
    // A list that contains itself at 50,000 levels is not a program anyone
    // typed, but it is a program a loop can build in one line — and without
    // this the answer to printing it is SIGSEGV. See `err::Nest`.
    let _nest = crate::err::Nest::enter("repr")?;
    Ok(match v {
        Value::None => "None".into(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        Value::Int(i) => i.to_string(),
        Value::Float(f) => float_repr(*f),
        Value::Str(s) => str_repr(s)?,
        Value::Bytes(b) => bytes_repr(b),
        Value::List(l) => {
            let l = l.borrow();
            let mut out = String::from("[");
            for (i, x) in l.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                out.push_str(&repr(x)?);
            }
            out.push(']');
            out
        }
        Value::Tuple(t) => {
            let mut out = String::from("(");
            for (i, x) in t.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                out.push_str(&repr(x)?);
            }
            if t.len() == 1 {
                out.push(',');
            }
            out.push(')');
            out
        }
        Value::Dict(d) => dict_repr(&d.borrow())?,
        // See value.rs: CPython's set order is a property of its hashing, so a
        // second implementation cannot reproduce it. Refuse rather than differ.
        Value::Set(s) => {
            if s.borrow().len() == 0 {
                "set()".into()
            } else if s.borrow().len() == 1 {
                format!("{{{}}}", repr(&s.borrow().items[0])?)
            } else {
                return Err(set_order_refused("repr() of a set with more than one element"));
            }
        }
        Value::Range(a, b, st) => {
            if *st == 1 {
                format!("range({a}, {b})")
            } else {
                format!("range({a}, {b}, {st})")
            }
        }
        Value::DictView(d, kind) => {
            let d = d.borrow();
            let items: Vec<Value> = match *kind {
                "keys" => d.keys(),
                "values" => d.values(),
                _ => d.items(),
            };
            let mut out = String::from(match *kind {
                "keys" => "dict_keys([",
                "values" => "dict_values([",
                _ => "dict_items([",
            });
            for (i, x) in items.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                out.push_str(&repr(x)?);
            }
            out.push_str("])");
            out
        }
        // `<enumerate object at 0x7f…>` embeds a heap address, so no
        // implementation can reproduce it — refuse rather than differ.
        Value::IterObj(_, kind) => {
            return Err(unsupported(
                "repr",
                &format!("repr() of a {kind} object, whose CPython repr contains a heap address"),
            ))
        }
        Value::Gen(_) => {
            return Err(unsupported(
                "repr",
                "repr() of a generator, whose CPython repr contains a heap address",
            ))
        }
        Value::Exc(kind, msg) => {
            if msg.is_empty() {
                format!("{kind}()")
            } else {
                format!("{kind}({})", str_repr(msg)?)
            }
        }
        other => {
            return Err(unsupported(
                "repr",
                &format!("repr() of a {}", type_name(other)),
            ))
        }
    })
}

fn dict_repr(d: &Dict) -> R<String> {
    let mut out = String::from("{");
    let mut first = true;
    for (k, v) in d.iter() {
        if !first {
            out.push_str(", ");
        }
        first = false;
        out.push_str(&repr(k)?);
        out.push_str(": ");
        out.push_str(&repr(v)?);
    }
    out.push('}');
    Ok(out)
}

// ---- float ----------------------------------------------------------------

/// Python's `repr(float)`: shortest digits that round-trip, laid out fixed or
/// scientific by the position of the decimal point.
pub fn float_repr(f: f64) -> String {
    if f.is_nan() {
        return "nan".into();
    }
    if f.is_infinite() {
        return if f < 0.0 { "-inf".into() } else { "inf".into() };
    }
    if f == 0.0 {
        return if f.is_sign_negative() { "-0.0".into() } else { "0.0".into() };
    }
    let neg = f < 0.0;
    let (digits, exp) = shortest_digits(f.abs());
    let decpt = exp + 1;
    let mut out = String::new();
    if neg {
        out.push('-');
    }
    if decpt <= -4 || decpt > 16 {
        out.push_str(&digits[..1]);
        if digits.len() > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        out.push('e');
        let e = decpt - 1;
        if e < 0 {
            out.push('-');
        } else {
            out.push('+');
        }
        let ea = e.unsigned_abs();
        if ea < 10 {
            out.push('0');
        }
        out.push_str(&ea.to_string());
    } else {
        out.push_str(&fixed_from_digits(&digits, decpt));
    }
    out
}

fn fixed_from_digits(digits: &str, decpt: i32) -> String {
    let n = digits.len() as i32;
    if decpt <= 0 {
        let mut s = String::from("0.");
        for _ in 0..(-decpt) {
            s.push('0');
        }
        s.push_str(digits);
        s
    } else if decpt >= n {
        let mut s = String::from(digits);
        for _ in 0..(decpt - n) {
            s.push('0');
        }
        s.push_str(".0");
        s
    } else {
        let d = decpt as usize;
        format!("{}.{}", &digits[..d], &digits[d..])
    }
}

/// Shortest round-tripping decimal digits and the base-10 exponent of the
/// leading digit.
///
/// Rust's `{:e}` gives the shortest round-tripping digits, which is most of the
/// answer — but not all of it, because "shortest" can be a TIE and the two
/// implementations break ties differently. `(1/-143.0) * 1e17` is exactly
/// -699300699300699.25, one ulp being 0.125 here, so both …699.2 and …699.3
/// are 17 digits and both round-trip. CPython (David Gay) resolves the tie to
/// EVEN and prints …699.2; Rust's `{:e}` rounds away and printed …699.3. A
/// wrong answer at exit 0, which is the class the contract exists to prevent —
/// `lypning fuzz` found it, seed 1223909964.
///
/// So `{:e}` is asked only for the digit COUNT, and the digits themselves come
/// from `{:.*e}`, which converts exactly and rounds half-to-even like CPython.
/// The exponent is re-read from that second render rather than carried over
/// from the first: rounding at the chosen width can carry (9.99 -> 1.0e+1) and
/// move the decimal point with it.
fn shortest_digits(x: f64) -> (String, i32) {
    let shortest = format!("{:e}", x); // e.g. "1.2345e3", "1e-5"
    let (mant, _) = shortest.split_once('e').unwrap();
    let ndigits = mant.chars().filter(|c| c.is_ascii_digit()).count().max(1);

    let s = format!("{:.*e}", ndigits - 1, x);
    let (mant, exp) = s.split_once('e').unwrap();
    let exp: i32 = exp.parse().unwrap();
    let digits: String = mant.chars().filter(|c| c.is_ascii_digit()).collect();
    let digits = digits.trim_end_matches('0');
    let digits = if digits.is_empty() { "0" } else { digits };
    (digits.to_string(), exp)
}

// ---- str / bytes ----------------------------------------------------------

pub fn str_repr(s: &str) -> R<String> {
    let has_single = s.contains('\'');
    let has_double = s.contains('"');
    let quote = if has_single && !has_double { '"' } else { '\'' };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || (c as u32) == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32));
            }
            c if (c as u32) < 0x80 => out.push(c),
            c => {
                if !printable_non_ascii(c) {
                    return Err(unsupported(
                        "repr-unicode",
                        &format!(
                            "repr() of U+{:04X}, whose printability needs CPython's unicode tables",
                            c as u32
                        ),
                    ));
                }
                out.push(c);
            }
        }
    }
    out.push(quote);
    Ok(out)
}

/// Blocks that are unambiguously printable under Python's `str.isprintable`.
/// Deliberately conservative: a character outside this list is refused, not
/// guessed, because getting it wrong means `repr` differs from CPython by an
/// escape sequence and nothing catches it.
fn printable_non_ascii(c: char) -> bool {
    let u = c as u32;
    match u {
        // C1 controls, NBSP (Zs), soft hyphen (Cf)
        0x80..=0xA0 | 0xAD => false,
        0xA1..=0x02FF => true,          // Latin-1 supplement .. spacing modifiers
        0x0370..=0x058F => true,        // Greek, Cyrillic, Armenian
        0x0590..=0x05FF => true,        // Hebrew
        0x0600..=0x06FF => true,        // Arabic
        0x1E00..=0x1FFF => true,        // Latin/Greek extended additional
        0x2010..=0x2027 => true,        // general punctuation, printable part
        0x2030..=0x205E => true,
        0x2070..=0x2BFF => true,        // super/subscripts, currency, arrows, math, shapes
        0x3040..=0x30FF => true,        // Hiragana, Katakana
        0x3400..=0x4DBF | 0x4E00..=0x9FFF => true, // CJK
        0xAC00..=0xD7A3 => true,        // Hangul syllables
        0xF900..=0xFAFF => true,        // CJK compatibility ideographs
        0x1F300..=0x1FAFF => true,      // emoji
        _ => false,
    }
}

pub fn bytes_repr(b: &[u8]) -> String {
    let has_single = b.contains(&b'\'');
    let has_double = b.contains(&b'"');
    let quote = if has_single && !has_double { b'"' } else { b'\'' };
    let mut out = String::from("b");
    out.push(quote as char);
    for &c in b {
        match c {
            b'\\' => out.push_str("\\\\"),
            b'\n' => out.push_str("\\n"),
            b'\r' => out.push_str("\\r"),
            b'\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c as char);
            }
            0x20..=0x7e => out.push(c as char),
            c => out.push_str(&format!("\\x{c:02x}")),
        }
    }
    out.push(quote as char);
    out
}

// ---- the format-spec mini-language ---------------------------------------

#[derive(Default, Debug)]
pub struct Spec {
    pub fill: Option<char>,
    pub align: Option<char>,
    pub sign: Option<char>,
    pub alt: bool,
    pub zero: bool,
    pub width: Option<usize>,
    pub grouping: Option<char>,
    pub precision: Option<usize>,
    pub ty: Option<char>,
}

pub fn parse_spec(s: &str) -> R<Spec> {
    let c: Vec<char> = s.chars().collect();
    let mut i = 0;
    let mut sp = Spec::default();
    if c.len() >= 2 && matches!(c[1], '<' | '>' | '^' | '=') {
        sp.fill = Some(c[0]);
        sp.align = Some(c[1]);
        i = 2;
    } else if !c.is_empty() && matches!(c[0], '<' | '>' | '^' | '=') {
        sp.align = Some(c[0]);
        i = 1;
    }
    if i < c.len() && matches!(c[i], '+' | '-' | ' ') {
        sp.sign = Some(c[i]);
        i += 1;
    }
    if i < c.len() && c[i] == '#' {
        sp.alt = true;
        i += 1;
    }
    if i < c.len() && c[i] == '0' {
        sp.zero = true;
        i += 1;
        if sp.align.is_none() {
            sp.align = Some('=');
            sp.fill = Some('0');
        }
    }
    let ws = i;
    while i < c.len() && c[i].is_ascii_digit() {
        i += 1;
    }
    if i > ws {
        sp.width = Some(c[ws..i].iter().collect::<String>().parse().unwrap());
    }
    if i < c.len() && (c[i] == ',' || c[i] == '_') {
        sp.grouping = Some(c[i]);
        i += 1;
    }
    if i < c.len() && c[i] == '.' {
        i += 1;
        let ps = i;
        while i < c.len() && c[i].is_ascii_digit() {
            i += 1;
        }
        if i == ps {
            return Err(value_err("Format specifier missing precision"));
        }
        sp.precision = Some(c[ps..i].iter().collect::<String>().parse().unwrap());
    }
    if i < c.len() {
        let t = c[i];
        i += 1;
        if i != c.len() {
            return Err(value_err(format!("Invalid format specifier '{s}'")));
        }
        match t {
            's' | 'd' | 'f' | 'F' | 'e' | 'E' | 'g' | 'G' | 'x' | 'X' | 'o' | 'b' | 'c' | '%' => {
                sp.ty = Some(t)
            }
            'n' => return Err(unsupported("format", "locale-aware 'n' format type")),
            _ => return Err(value_err(format!("Unknown format code '{t}'"))),
        }
    }
    Ok(sp)
}

pub fn format_value(v: &Value, spec_src: &str) -> R<String> {
    if spec_src.is_empty() {
        return to_str(v);
    }
    let sp = parse_spec(spec_src)?;

    // A FLOAT WITH NO PRESENTATION TYPE IS NOT 'g'.
    //
    // CPython gives the empty presentation type its own mode for floats: "like
    // 'g', except that when fixed-point notation is used it always includes at
    // least one digit past the decimal point, and the default precision is as
    // high as needed to represent the particular value". That is str(float),
    // padded and signed. Defaulting to 'g' instead lost the fractional part and
    // took the 6-digit default precision with it, so
    //     format(0.0, "+")        gave '+0'          not '+0.0'
    //     f"{12345.0:^7}"         gave ' 12345 '     not '12345.0'
    //     format(1e10, ",")       gave '1e+10'       not '10,000,000,000.0'
    // — a whole family of wrong answers at exit 0 on the most ordinary
    // formatting there is. scripts/lypning-fuzz.mjs found sixteen of them.
    if sp.ty.is_none() {
        if let Value::Float(f) = v {
            let body = if f.is_finite() {
                let s = to_str(&Value::Float(f.abs()))?;
                group_float(&s, sp.grouping)
            } else {
                return Ok(pad_signed(nonfinite_sign(*f, &sp), &nonfinite(*f, false), &sp, true));
            };
            let signch = if f.is_sign_negative() {
                "-"
            } else {
                match sp.sign {
                    Some('+') => "+",
                    Some(' ') => " ",
                    _ => "",
                }
            };
            return Ok(pad_signed(signch, &body, &sp, true));
        }
    }

    let ty = sp.ty.unwrap_or_else(|| match v {
        Value::Int(_) | Value::Bool(_) => 'd',
        Value::Float(_) => 'g',
        _ => 's',
    });
    let body = match ty {
        's' => {
            let mut s = to_str(v)?;
            if let Some(p) = sp.precision {
                s = s.chars().take(p).collect();
            }
            return Ok(pad(&s, &sp, false));
        }
        'c' => {
            let n = int_of(v)?;
            let ch = char::from_u32(n as u32).ok_or_else(|| value_err("chr() arg out of range"))?;
            return Ok(pad(&ch.to_string(), &sp, false));
        }
        'd' => {
            let n = int_of(v)?;
            group(&n.unsigned_abs().to_string(), sp.grouping, 3)
        }
        'x' | 'X' | 'o' | 'b' => {
            let n = int_of(v)?;
            let a = n.unsigned_abs();
            let mut s = match ty {
                'x' => format!("{a:x}"),
                'X' => format!("{a:X}"),
                'o' => format!("{a:o}"),
                _ => format!("{a:b}"),
            };
            let radix = if ty == 'b' { 4 } else { 4 };
            s = group(&s, sp.grouping, radix);
            if sp.alt {
                s = format!(
                    "{}{s}",
                    match ty {
                        'x' => "0x",
                        'X' => "0X",
                        'o' => "0o",
                        _ => "0b",
                    }
                );
            }
            s
        }
        'f' | 'F' => {
            let f = float_of(v)?;
            if !f.is_finite() {
                return Ok(pad_signed(nonfinite_sign(f, &sp), &nonfinite(f, ty.is_uppercase()), &sp, true));
            }
            group_float(&format!("{:.*}", sp.precision.unwrap_or(6), f.abs()), sp.grouping)
        }
        'e' | 'E' => {
            let f = float_of(v)?;
            if !f.is_finite() {
                return Ok(pad_signed(nonfinite_sign(f, &sp), &nonfinite(f, ty == 'E'), &sp, true));
            }
            exp_format(f.abs(), sp.precision.unwrap_or(6), ty == 'E')
        }
        'g' | 'G' => {
            let f = float_of(v)?;
            if !f.is_finite() {
                return Ok(pad_signed(nonfinite_sign(f, &sp), &nonfinite(f, ty == 'G'), &sp, true));
            }
            g_format(f.abs(), sp.precision.unwrap_or(6), ty == 'G', sp.alt)
        }
        '%' => {
            let f = float_of(v)? * 100.0;
            format!("{:.*}%", sp.precision.unwrap_or(6), f.abs())
        }
        _ => return Err(value_err(format!("Unknown format code '{ty}'"))),
    };
    let neg = match v {
        Value::Int(i) => *i < 0,
        Value::Bool(_) => false,
        Value::Float(f) => f.is_sign_negative() && (*f != 0.0 || sp.ty.is_some()),
        _ => false,
    };
    let signch = if neg {
        "-"
    } else {
        match sp.sign {
            Some('+') => "+",
            Some(' ') => " ",
            _ => "",
        }
    };
    Ok(pad_signed(signch, &body, &sp, true))
}

fn nonfinite(f: f64, upper: bool) -> String {
    let s = if f.is_nan() { "nan" } else { "inf" };
    if upper {
        s.to_uppercase()
    } else {
        s.to_string()
    }
}

/// The sign slot for an infinity or a NaN — CPython gives them one too.
///
/// Two things fall out of putting it in the SIGN slot rather than in the body:
/// `format(inf, "+")` is `+inf` and not `inf`, and `format(-inf, "010")` is
/// `-000000inf` and not `000000-inf`, because zero fill goes between the sign
/// and the digits. Both were wrong while the body carried its own minus.
fn nonfinite_sign(f: f64, sp: &Spec) -> &'static str {
    if f.is_sign_negative() && !f.is_nan() {
        "-"
    } else {
        match sp.sign {
            Some('+') => "+",
            Some(' ') => " ",
            _ => "",
        }
    }
}

fn int_of(v: &Value) -> R<i64> {
    match v {
        Value::Int(i) => Ok(*i),
        Value::Bool(b) => Ok(*b as i64),
        _ => Err(unsupported(
            "format",
            &format!("integer format code applied to {}", type_name(v)),
        )),
    }
}
fn float_of(v: &Value) -> R<f64> {
    match v {
        Value::Int(i) => Ok(*i as f64),
        Value::Bool(b) => Ok(*b as i64 as f64),
        Value::Float(f) => Ok(*f),
        _ => Err(unsupported(
            "format",
            &format!("float format code applied to {}", type_name(v)),
        )),
    }
}

fn group(digits: &str, sep: Option<char>, size: usize) -> String {
    let Some(sep) = sep else {
        return digits.to_string();
    };
    let b: Vec<char> = digits.chars().collect();
    let mut out = String::new();
    for (i, c) in b.iter().enumerate() {
        if i > 0 && (b.len() - i) % size == 0 {
            out.push(sep);
        }
        out.push(*c);
    }
    out
}

fn group_float(s: &str, sep: Option<char>) -> String {
    if sep.is_none() {
        return s.to_string();
    }
    // A body already in exponent form has no integer part to group. Grouping
    // the whole string put a separator inside the exponent — CPython renders
    // `format(1e17, "_")` as `1e+17` where this answered `1e_+17`. `lypning
    // fuzz` found it.
    if s.contains('e') || s.contains('E') {
        return s.to_string();
    }
    match s.split_once('.') {
        Some((a, b)) => format!("{}.{}", group(a, sep, 3), b),
        None => group(s, sep, 3),
    }
}

fn exp_format(x: f64, prec: usize, upper: bool) -> String {
    let s = format!("{:.*e}", prec, x); // Rust: "1.50e3"
    let (mant, exp) = s.split_once('e').unwrap();
    let e: i32 = exp.parse().unwrap();
    let sign = if e < 0 { '-' } else { '+' };
    let ea = e.unsigned_abs();
    let ech = if upper { 'E' } else { 'e' };
    format!("{mant}{ech}{sign}{ea:02}")
}

/// Python's `g`: significant-digit precision, scientific when the exponent
/// falls outside `[-4, precision)`, trailing zeros stripped unless `#`.
fn g_format(x: f64, prec: usize, upper: bool, alt: bool) -> String {
    let p = if prec == 0 { 1 } else { prec };
    if x == 0.0 {
        return if alt {
            format!("{:.*}", p - 1, 0.0)
        } else {
            "0".into()
        };
    }
    let exp = x.log10().floor() as i32;
    // Recompute the exponent from the rounded representation: log10 can land on
    // the wrong side of a power of ten for values like 9.9999e2 at p=3.
    let probe = format!("{:.*e}", p - 1, x);
    let exp: i32 = probe.split_once('e').unwrap().1.parse().unwrap_or(exp);
    if exp < -4 || exp >= p as i32 {
        let s = exp_format(x, p - 1, upper);
        if alt {
            return s;
        }
        let (mant, tail) = s.split_once(['e', 'E']).unwrap();
        let ech = if upper { 'E' } else { 'e' };
        let m = strip_zeros(mant);
        return format!("{m}{ech}{tail}");
    }
    let decimals = (p as i32 - 1 - exp).max(0) as usize;
    let s = format!("{:.*}", decimals, x);
    if alt {
        s
    } else {
        strip_zeros(&s)
    }
}

fn strip_zeros(s: &str) -> String {
    if !s.contains('.') {
        return s.to_string();
    }
    let t = s.trim_end_matches('0');
    t.trim_end_matches('.').to_string()
}

fn pad(body: &str, sp: &Spec, numeric: bool) -> String {
    pad_signed("", body, sp, numeric)
}

fn pad_signed(sign: &str, body: &str, sp: &Spec, numeric: bool) -> String {
    let full_len = sign.chars().count() + body.chars().count();
    let Some(w) = sp.width else {
        return format!("{sign}{body}");
    };
    if full_len >= w {
        return format!("{sign}{body}");
    }
    let padn = w - full_len;
    let fill = sp.fill.unwrap_or(' ');
    // Default alignment: '>' for numbers, '<' for everything else, and the
    // CALLER says which — it is the only one that knows. Inferring it from a
    // non-empty sign slot instead left every positive number with no
    // presentation type aligned as if it were a string: `format(7, "10")` and
    // `f"{1.5:10}"` padded on the right where CPython pads on the left.
    let align = sp.align.unwrap_or(if numeric { '>' } else { '<' });
    let f: String = std::iter::repeat(fill).take(padn).collect();
    match align {
        '<' => format!("{sign}{body}{f}"),
        '>' => format!("{f}{sign}{body}"),
        '=' => format!("{sign}{f}{body}"),
        _ => {
            let left = padn / 2;
            let right = padn - left;
            format!(
                "{}{sign}{body}{}",
                std::iter::repeat(fill).take(left).collect::<String>(),
                std::iter::repeat(fill).take(right).collect::<String>()
            )
        }
    }
}
