//! `json.loads` / `json.dumps`, written directly against CPython's output.
//!
//! This is the single largest capability win lypning has over lypning: lypning-mp's
//! `json` is a frozen PYTHON module, so `json.dumps` inherits its ordered-dict
//! cost (measured at 3.8x stock in the lypning-mp bench ledger). Here it is Rust
//! over the same insertion-ordered dict.
//!
//! The defaults matter and are easy to get subtly wrong: `ensure_ascii=True`,
//! separators `(', ', ': ')` with no indent and `(',', ': ')` with one, floats
//! rendered by `repr`, and non-BMP characters escaped as a surrogate PAIR.

use crate::err::*;
use crate::fmt;
use crate::value::*;
use std::cell::RefCell;
use std::rc::Rc;

// ---- parse ----------------------------------------------------------------

struct P<'a> {
    b: &'a [u8],
    i: usize,
    src: &'a str,
}

pub fn parse(text: &str) -> R<Value> {
    let mut p = P {
        b: text.as_bytes(),
        i: 0,
        src: text,
    };
    p.ws();
    let v = p.value()?;
    p.ws();
    if p.i != p.b.len() {
        return Err(decode_err("Extra data", p.src, p.i));
    }
    Ok(v)
}

fn decode_err(msg: &str, src: &str, pos: usize) -> LypningError {
    let line = src[..pos.min(src.len())].matches('\n').count() + 1;
    let col = pos - src[..pos.min(src.len())].rfind('\n').map_or(0, |i| i + 1) + 1;
    LypningError::exc(
        "JSONDecodeError",
        format!("{msg}: line {line} column {col} (char {pos})"),
    )
}

impl<'a> P<'a> {
    fn ws(&mut self) {
        while self.i < self.b.len() && matches!(self.b[self.i], b' ' | b'\t' | b'\n' | b'\r') {
            self.i += 1;
        }
    }
    fn value(&mut self) -> R<Value> {
        let _nest = crate::err::Nest::enter("json value")?;
        if self.i >= self.b.len() {
            return Err(decode_err("Expecting value", self.src, self.i));
        }
        match self.b[self.i] {
            b'{' => self.object(),
            b'[' => self.array(),
            b'"' => Ok(Value::Str(self.string()?.into())),
            b't' if self.b[self.i..].starts_with(b"true") => {
                self.i += 4;
                Ok(Value::Bool(true))
            }
            b'f' if self.b[self.i..].starts_with(b"false") => {
                self.i += 5;
                Ok(Value::Bool(false))
            }
            b'n' if self.b[self.i..].starts_with(b"null") => {
                self.i += 4;
                Ok(Value::None)
            }
            b'-' | b'0'..=b'9' => self.number(),
            // CPython's json accepts these three by default.
            b'N' if self.b[self.i..].starts_with(b"NaN") => {
                self.i += 3;
                Ok(Value::Float(f64::NAN))
            }
            b'I' if self.b[self.i..].starts_with(b"Infinity") => {
                self.i += 8;
                Ok(Value::Float(f64::INFINITY))
            }
            _ => Err(decode_err("Expecting value", self.src, self.i)),
        }
    }
    fn object(&mut self) -> R<Value> {
        self.i += 1;
        let mut d = Dict::new();
        self.ws();
        if self.i < self.b.len() && self.b[self.i] == b'}' {
            self.i += 1;
            return Ok(Value::Dict(Rc::new(RefCell::new(d))));
        }
        loop {
            self.ws();
            if self.i >= self.b.len() || self.b[self.i] != b'"' {
                return Err(decode_err(
                    "Expecting property name enclosed in double quotes",
                    self.src,
                    self.i,
                ));
            }
            let k = self.string()?;
            self.ws();
            if self.i >= self.b.len() || self.b[self.i] != b':' {
                return Err(decode_err("Expecting ':' delimiter", self.src, self.i));
            }
            self.i += 1;
            self.ws();
            let v = self.value()?;
            d.insert(Value::Str(k.into()), v)?;
            self.ws();
            match self.b.get(self.i) {
                Some(b',') => self.i += 1,
                Some(b'}') => {
                    self.i += 1;
                    break;
                }
                _ => return Err(decode_err("Expecting ',' delimiter", self.src, self.i)),
            }
        }
        Ok(Value::Dict(Rc::new(RefCell::new(d))))
    }
    fn array(&mut self) -> R<Value> {
        self.i += 1;
        let mut out = Vec::new();
        self.ws();
        if self.i < self.b.len() && self.b[self.i] == b']' {
            self.i += 1;
            return Ok(list(out));
        }
        loop {
            self.ws();
            out.push(self.value()?);
            self.ws();
            match self.b.get(self.i) {
                Some(b',') => self.i += 1,
                Some(b']') => {
                    self.i += 1;
                    break;
                }
                _ => return Err(decode_err("Expecting ',' delimiter", self.src, self.i)),
            }
        }
        Ok(list(out))
    }
    fn string(&mut self) -> R<String> {
        self.i += 1;
        let mut out = String::new();
        loop {
            let Some(&c) = self.b.get(self.i) else {
                return Err(decode_err("Unterminated string starting at", self.src, self.i));
            };
            match c {
                b'"' => {
                    self.i += 1;
                    return Ok(out);
                }
                b'\\' => {
                    self.i += 1;
                    let Some(&e) = self.b.get(self.i) else {
                        return Err(decode_err("Invalid \\escape", self.src, self.i));
                    };
                    self.i += 1;
                    match e {
                        b'"' => out.push('"'),
                        b'\\' => out.push('\\'),
                        b'/' => out.push('/'),
                        b'b' => out.push('\u{8}'),
                        b'f' => out.push('\u{c}'),
                        b'n' => out.push('\n'),
                        b'r' => out.push('\r'),
                        b't' => out.push('\t'),
                        b'u' => {
                            let cp = self.hex4()?;
                            // A high surrogate must be paired with the low one
                            // that follows; unpaired ones are what CPython's
                            // decoder passes through as lone surrogates, which
                            // Rust's `char` cannot hold — refuse those.
                            if (0xD800..0xDC00).contains(&cp) {
                                if self.b.get(self.i) == Some(&b'\\')
                                    && self.b.get(self.i + 1) == Some(&b'u')
                                {
                                    self.i += 2;
                                    let lo = self.hex4()?;
                                    if (0xDC00..0xE000).contains(&lo) {
                                        let c = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                                        out.push(char::from_u32(c).unwrap());
                                        continue;
                                    }
                                }
                                return Err(unsupported(
                                    "json",
                                    "lone surrogate in a \\u escape",
                                ));
                            }
                            if (0xDC00..0xE000).contains(&cp) {
                                return Err(unsupported("json", "lone surrogate in a \\u escape"));
                            }
                            out.push(char::from_u32(cp).unwrap_or('\u{fffd}'));
                        }
                        _ => return Err(decode_err("Invalid \\escape", self.src, self.i - 1)),
                    }
                }
                _ => {
                    let start = self.i;
                    while self.i < self.b.len()
                        && self.b[self.i] != b'"'
                        && self.b[self.i] != b'\\'
                    {
                        self.i += 1;
                    }
                    out.push_str(
                        std::str::from_utf8(&self.b[start..self.i])
                            .map_err(|_| decode_err("Invalid control character at", self.src, start))?,
                    );
                }
            }
        }
    }
    fn hex4(&mut self) -> R<u32> {
        if self.i + 4 > self.b.len() {
            return Err(decode_err("Invalid \\uXXXX escape", self.src, self.i));
        }
        let s = std::str::from_utf8(&self.b[self.i..self.i + 4])
            .map_err(|_| decode_err("Invalid \\uXXXX escape", self.src, self.i))?;
        let v = u32::from_str_radix(s, 16)
            .map_err(|_| decode_err("Invalid \\uXXXX escape", self.src, self.i))?;
        self.i += 4;
        Ok(v)
    }
    fn number(&mut self) -> R<Value> {
        let start = self.i;
        if self.b[self.i] == b'-' {
            self.i += 1;
        }
        while self.i < self.b.len() && self.b[self.i].is_ascii_digit() {
            self.i += 1;
        }
        let mut is_float = false;
        if self.b.get(self.i) == Some(&b'.') {
            is_float = true;
            self.i += 1;
            while self.i < self.b.len() && self.b[self.i].is_ascii_digit() {
                self.i += 1;
            }
        }
        if matches!(self.b.get(self.i), Some(b'e') | Some(b'E')) {
            is_float = true;
            self.i += 1;
            if matches!(self.b.get(self.i), Some(b'+') | Some(b'-')) {
                self.i += 1;
            }
            while self.i < self.b.len() && self.b[self.i].is_ascii_digit() {
                self.i += 1;
            }
        }
        let text = &self.src[start..self.i];
        if is_float {
            Ok(Value::Float(text.parse().map_err(|_| {
                decode_err("Expecting value", self.src, start)
            })?))
        } else {
            match text.parse::<i64>() {
                Ok(v) => Ok(Value::Int(v)),
                Err(_) => Err(unsupported(
                    "bigint",
                    "JSON integer beyond 64-bit range",
                )),
            }
        }
    }
}

// ---- dumps ----------------------------------------------------------------

pub fn dumps(v: &Value, kw: &[(Rc<str>, Value)]) -> R<String> {
    let get = |n: &str| kw.iter().find(|(k, _)| k.as_ref() == n).map(|(_, v)| v.clone());
    for (k, _) in kw {
        if !matches!(
            k.as_ref(),
            "indent" | "sort_keys" | "ensure_ascii" | "separators" | "default" | "skipkeys"
        ) {
            return Err(unsupported("json", &format!("json.dumps({k}=…)")));
        }
    }
    if get("default").is_some_and(|d| !matches!(d, Value::None)) {
        return Err(unsupported("json", "json.dumps(default=…)"));
    }
    let ensure_ascii = match get("ensure_ascii") {
        Some(v) => truthy(&v)?,
        None => true,
    };
    let sort_keys = match get("sort_keys") {
        Some(v) => truthy(&v)?,
        None => false,
    };
    let indent = match get("indent") {
        None | Some(Value::None) => None,
        Some(Value::Int(n)) => Some(" ".repeat(n.max(0) as usize)),
        Some(Value::Str(s)) => Some(s.to_string()),
        Some(other) => {
            return Err(type_err(format!(
                "indent must be int or str, not {}",
                type_name(&other)
            )))
        }
    };
    let (item_sep, key_sep) = match get("separators") {
        None | Some(Value::None) => (
            if indent.is_some() { "," } else { ", " }.to_string(),
            ": ".to_string(),
        ),
        Some(Value::Tuple(t)) if t.len() == 2 => (fmt::to_str(&t[0])?, fmt::to_str(&t[1])?),
        Some(_) => return Err(type_err("separators must be a 2-tuple")),
    };
    let mut out = String::new();
    write_value(
        &mut out,
        v,
        &Opts {
            ensure_ascii,
            sort_keys,
            indent,
            item_sep,
            key_sep,
        },
        0,
    )?;
    Ok(out)
}

struct Opts {
    ensure_ascii: bool,
    sort_keys: bool,
    indent: Option<String>,
    item_sep: String,
    key_sep: String,
}

fn write_value(out: &mut String, v: &Value, o: &Opts, depth: usize) -> R<()> {
    match v {
        Value::None => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Int(i) => out.push_str(&i.to_string()),
        Value::Float(f) => {
            // json uses repr(float), including the bare `NaN`/`Infinity` that
            // are not valid JSON but are what CPython emits by default.
            out.push_str(&if f.is_nan() {
                "NaN".to_string()
            } else if f.is_infinite() {
                if *f < 0.0 { "-Infinity" } else { "Infinity" }.to_string()
            } else {
                fmt::float_repr(*f)
            })
        }
        Value::Str(s) => write_json_str(out, s, o.ensure_ascii),
        Value::List(l) => {
            let items = l.borrow().clone();
            write_seq(out, &items, o, depth)?;
        }
        Value::Tuple(t) => write_seq(out, t, o, depth)?,
        Value::Dict(d) => {
            let mut pairs: Vec<(String, Value)> = Vec::new();
            for (k, val) in d.borrow().iter() {
                let ks = match k {
                    Value::Str(s) => s.to_string(),
                    Value::Int(i) => i.to_string(),
                    Value::Bool(b) => if *b { "true" } else { "false" }.to_string(),
                    Value::None => "null".to_string(),
                    Value::Float(f) => fmt::float_repr(*f),
                    other => {
                        return Err(type_err(format!(
                            "keys must be str, int, float, bool or None, not {}",
                            type_name(other)
                        )))
                    }
                };
                pairs.push((ks, val.clone()));
            }
            if o.sort_keys {
                pairs.sort_by(|a, b| a.0.as_bytes().cmp(b.0.as_bytes()));
            }
            if pairs.is_empty() {
                out.push_str("{}");
                return Ok(());
            }
            out.push('{');
            for (i, (k, val)) in pairs.iter().enumerate() {
                if i > 0 {
                    out.push_str(&o.item_sep);
                }
                newline_indent(out, o, depth + 1);
                write_json_str(out, k, o.ensure_ascii);
                out.push_str(&o.key_sep);
                write_value(out, val, o, depth + 1)?;
            }
            newline_indent(out, o, depth);
            out.push('}');
        }
        Value::Set(_) => {
            return Err(type_err("Object of type set is not JSON serializable"))
        }
        other => {
            return Err(type_err(format!(
                "Object of type {} is not JSON serializable",
                type_name(other)
            )))
        }
    }
    Ok(())
}

fn write_seq(out: &mut String, items: &[Value], o: &Opts, depth: usize) -> R<()> {
    if items.is_empty() {
        out.push_str("[]");
        return Ok(());
    }
    out.push('[');
    for (i, x) in items.iter().enumerate() {
        if i > 0 {
            out.push_str(&o.item_sep);
        }
        newline_indent(out, o, depth + 1);
        write_value(out, x, o, depth + 1)?;
    }
    newline_indent(out, o, depth);
    out.push(']');
    Ok(())
}

fn newline_indent(out: &mut String, o: &Opts, depth: usize) {
    if let Some(ind) = &o.indent {
        out.push('\n');
        for _ in 0..depth {
            out.push_str(ind);
        }
    }
}

fn write_json_str(out: &mut String, s: &str, ensure_ascii: bool) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c if (c as u32) < 0x80 => out.push(c),
            c if !ensure_ascii => out.push(c),
            c => {
                let u = c as u32;
                if u > 0xFFFF {
                    // Non-BMP characters are emitted as a surrogate PAIR, which
                    // is what CPython does and what a naive \u{:04x} misses.
                    let v = u - 0x10000;
                    out.push_str(&format!(
                        "\\u{:04x}\\u{:04x}",
                        0xD800 + (v >> 10),
                        0xDC00 + (v & 0x3FF)
                    ));
                } else {
                    out.push_str(&format!("\\u{u:04x}"));
                }
            }
        }
    }
    out.push('"');
}
