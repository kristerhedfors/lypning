//! `ast` — `import ast` and `ast.literal_eval(s)` over a `str`: the whole of
//! the `cap-ast` capability, compiled into `lypning-l` and into nothing
//! smaller. Every line of this file, and every line that reaches it, is behind
//! `cfg(feature = "cap-ast")`. Named `pyast.rs` because `ast.rs` is the
//! engine's OWN syntax tree.
//!
//! **One name is served.** `ast.parse` is NOT, in any form: `parse.rs` accepts
//! programs CPython's `ast.parse` rejects (a one-line suite with no separator,
//! a backslash continuation at EOF, a NUL, a signed `\x` escape, invalid
//! f-string fields — found by an adversarial review on 2026-09-25), so a
//! syntax check built on it would answer "ok" where CPython raises. Every other
//! attribute — `parse`, `walk`, `dump`, `unparse`, the node classes — is a
//! `module-attr` block out of `route::MODULE_ATTRS`, in the CORE's walk.
//!
//! # `literal_eval`, and why it does not use `parse.rs`
//!
//! `parse.rs` folds `-<literal>`, so `--1` and `-(-1)` would come out as a
//! literal where CPython raises `ValueError`. This is a small recursive descent
//! of its own over `lex.rs` TOKENS, accepting exactly: adjacent `str` literals,
//! or adjacent `bytes` literals (never a mix); an `int` or `float` literal with
//! at most one `+`/`-` directly before it; `True`/`False`/`None`; a
//! parenthesised value; a tuple (a trailing comma, or `()`, or a bare top-level
//! `1, 2`); a list; a dict without `**`; a non-empty set display; and the exact
//! token sequence `set ( )`, which ignores any rebinding of `set`, as CPython's
//! does. Then only NEWLINE and ENDMARKER, with comments and blank lines.
//!
//! **Every failure is a refusal and never a raise.** CPython's `ValueError`
//! text is the repr of an AST node, which changed in 3.14; its `SyntaxError`
//! wording and offsets are its tokenizer's; its unhashable-key `TypeError`
//! changed in 3.14. None of it is this engine's to write, so a `try: … except
//! ValueError` around a malformed input refuses too.
//!
//! # The text is screened BEFORE `lex.rs` sees it
//!
//! `lex.rs` is lax in ways the running engine tolerates and `literal_eval`
//! cannot ([`screen`]): it reads a NUL as the end of input, `u32::from_str_radix`
//! lets `'\x+1'` through as `'\x01'`, an unknown escape is kept where CPython
//! 3.12+ also WARNS, an octal escape past `\377` is a warning on 3.9 and 3.11+,
//! and a number glued to a name (`1if`, `0x1for`) is a warning since 3.11. The
//! screen refuses all of those, and every `\r`, form feed, vertical tab, a
//! backslash outside a string (a continuation, which at EOF CPython rejects),
//! an `f` prefix (a `JoinedStr`, which CPython rejects), a coding cookie, and
//! a last line of only whitespace (an indent to CPython, nothing to `lex.rs`).
//!
//! Leading spaces and tabs are stripped only on a 3.10+ reference whose minor
//! was measured (`" 1"` is an `IndentationError` on 3.9); otherwise they refuse.

use crate::args::Args;
use crate::err::{unsupported, LypningError, R};
use crate::lex::{Tok, Token};
use crate::value::{Dict, Int, Set, Value};
use std::cell::RefCell;
use std::rc::Rc;

/// The names this module serves — `route::AST_SERVED` itself, so the router's
/// table and this file are one list.
use crate::route::AST_SERVED as SERVED;

/// Deeper than this refuses. CPython's own limit (200 nested brackets) is far
/// above it; the point is a bounded Rust stack, and a refusal is never wrong.
const MAX_DEPTH: u32 = 50;

fn refuse(why: &str) -> LypningError {
    unsupported("ast", &format!("ast.literal_eval(): {why}"))
}

/// `ast.<name>` as a value: a served name is a bound module method, every
/// other one refuses with the kind the router blocks on statically.
pub fn module_attr(name: &str) -> R<Value> {
    match SERVED.iter().find(|n| **n == name) {
        Some(n) => Ok(Value::Bound(Rc::new(Value::Module("ast")), n)),
        None => Err(unsupported("module-attr", &format!("ast.{name}"))),
    }
}

pub fn call(_it: &mut crate::eval::Interp, name: &str, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    if name != "literal_eval" {
        return Err(unsupported("module-attr", &format!("ast.{name}")));
    }
    if !kw.is_empty() || args.len() != 1 {
        return Err(refuse("anything but one positional argument"));
    }
    match args.first() {
        Some(Value::Str(s)) => literal_eval(s).map_err(refuse),
        Some(v) => Err(refuse(&format!(
            "over a {} (only a str is served)",
            crate::value::type_name(v)
        ))),
        None => Err(refuse("no argument")),
    }
}

/// The whole of `literal_eval` over a `str`, or why it refuses.
pub fn literal_eval(s: &str) -> Result<Value, &'static str> {
    let s = if crate::err::REF_PY_KNOWN && crate::err::REF_PY_MINOR >= 10 {
        s.trim_start_matches([' ', '\t'])
    } else if s.starts_with([' ', '\t']) {
        return Err("leading whitespace, stripped from 3.10 and an IndentationError on 3.9");
    } else {
        s
    };
    screen(s.as_bytes())?;
    let toks = crate::lex::tokenize(s).map_err(|_| "source the tokenizer rejects or cannot represent")?;
    let mut p = P { t: &toks, i: 0, depth: 0 };
    let v = p.top()?;
    while matches!(p.tok(), Tok::Newline) {
        p.i += 1;
    }
    if !matches!(p.tok(), Tok::Eof) {
        return Err("more than one expression, or a construct that is not a literal");
    }
    Ok(v)
}

fn ident(c: u8) -> bool {
    c == b'_' || c.is_ascii_alphanumeric()
}

/// Refuse every text on which `lex.rs` and CPython's tokenizer could disagree,
/// or on which CPython would print a warning. Accepting is not the screen's
/// job: the token parser below decides the grammar.
fn screen(b: &[u8]) -> Result<(), &'static str> {
    if b.iter().any(|c| matches!(c, 0 | b'\r' | 0x0b | 0x0c)) {
        return Err("a NUL, carriage return, form feed or vertical tab in the source");
    }
    // A last line of only spaces and tabs, with no newline after it, is an
    // indent to CPython's tokenizer (`'1\n '` is an IndentationError on every
    // version) and nothing to `lex.rs`, which stops at the end of input.
    let tail = b.rsplit(|c| *c == b'\n').next().unwrap_or(b"");
    if b.contains(&b'\n') && !tail.is_empty() && tail.iter().all(|c| matches!(c, b' ' | b'\t')) {
        return Err("a last line of only whitespace, which CPython reads as an indent");
    }
    let mut i = 0;
    while i < b.len() {
        let c = b[i];
        if c == b'\\' {
            return Err("a backslash outside a string literal");
        } else if c == b'#' {
            let end = b[i..].iter().position(|c| *c == b'\n').map_or(b.len(), |n| i + n);
            if b[i..end].windows(6).any(|w| w == b"coding") {
                return Err("a comment that may be a coding declaration");
            }
            i = end;
        } else if c == b'\'' || c == b'"' {
            i = string(b, i, false, false)?;
        } else if c.is_ascii_digit() || (c == b'.' && b.get(i + 1).is_some_and(u8::is_ascii_digit)) {
            i = number(b, i)?;
        } else if c == b'_' || c.is_ascii_alphabetic() {
            let s = i;
            while i < b.len() && ident(b[i]) {
                i += 1;
            }
            if i < b.len() && (b[i] == b'\'' || b[i] == b'"') {
                let (raw, bytes) = match b[s..i].to_ascii_lowercase().as_slice() {
                    b"r" => (true, false),
                    b"b" => (false, true),
                    b"u" => (false, false),
                    b"rb" | b"br" => (true, true),
                    _ => return Err("a string prefix other than r, b, u, rb or br"),
                };
                i = string(b, i, raw, bytes)?;
            }
        } else {
            i += 1;
        }
    }
    Ok(())
}

/// One string literal starting at its quote; returns the index past it. In a
/// non-raw literal every escape must be one CPython decodes without a warning.
fn string(b: &[u8], mut i: usize, raw: bool, bytes: bool) -> Result<usize, &'static str> {
    let q = b[i];
    let triple = b.get(i + 1) == Some(&q) && b.get(i + 2) == Some(&q);
    i += if triple { 3 } else { 1 };
    let hex = |from: usize, n: usize| -> Option<u32> {
        let d = b.get(from..from + n)?;
        if !d.iter().all(u8::is_ascii_hexdigit) {
            return None;
        }
        u32::from_str_radix(std::str::from_utf8(d).ok()?, 16).ok()
    };
    loop {
        let Some(&c) = b.get(i) else { return Err("an unterminated string") };
        if bytes && c >= 0x80 {
            return Err("a non-ASCII character in a bytes literal");
        }
        match c {
            b'\n' if !triple => return Err("an unterminated string"),
            b'\\' if raw => i += 2,
            b'\\' => {
                let Some(&d) = b.get(i + 1) else { return Err("an unterminated string") };
                i += 2;
                match d {
                    b'\n' | b'\\' | b'\'' | b'"' | b'a' | b'b' | b'f' | b'n' | b'r' | b't' | b'v' => {}
                    b'0'..=b'7' => {
                        let mut v = (d - b'0') as u32;
                        let mut n = 1;
                        while n < 3 && b.get(i).is_some_and(|c| (b'0'..=b'7').contains(c)) {
                            v = v * 8 + (b[i] - b'0') as u32;
                            i += 1;
                            n += 1;
                        }
                        if v > 0o377 {
                            return Err("an octal escape above \\377");
                        }
                    }
                    b'x' => {
                        hex(i, 2).ok_or("a \\x escape without two hex digits")?;
                        i += 2;
                    }
                    b'u' | b'U' if !bytes => {
                        let n = if d == b'u' { 4 } else { 8 };
                        let v = hex(i, n).ok_or("a \\u or \\U escape without its hex digits")?;
                        if char::from_u32(v).is_none() {
                            return Err("a \\u or \\U escape that names no character");
                        }
                        i += n;
                    }
                    _ => return Err("an escape CPython warns about or decodes by name"),
                }
            }
            _ if c == q => {
                if !triple {
                    return Ok(i + 1);
                }
                if b.get(i + 1) == Some(&q) && b.get(i + 2) == Some(&q) {
                    return Ok(i + 3);
                }
                i += 1;
            }
            _ => i += 1,
        }
    }
}

/// One numeric literal, spelled exactly as CPython's tokenizer takes it with no
/// warning; returns the index past it. Anything glued on — a name, a `j`, a
/// second `.` — refuses.
fn number(b: &[u8], mut i: usize) -> Result<usize, &'static str> {
    const BAD: &str = "a numeric literal CPython rejects or warns about";
    // One run of digits of `radix`, a single underscore only BETWEEN two
    // digits — or, with `lead`, once before the first (`0x_1`). An underscore
    // left over after the run is leading, trailing or doubled, and refuses.
    let run = |mut i: usize, radix: u32, lead: bool| -> Result<(usize, Vec<u8>), &'static str> {
        let mut ds = Vec::new();
        let digit = |c: Option<&u8>| c.is_some_and(|c| (*c as char).is_digit(radix));
        if lead && b.get(i) == Some(&b'_') && digit(b.get(i + 1)) {
            i += 1;
        }
        loop {
            match b.get(i) {
                Some(&c) if digit(Some(&c)) => {
                    ds.push(c);
                    i += 1;
                }
                Some(b'_') if !ds.is_empty() && digit(b.get(i + 1)) => i += 1,
                _ => break,
            }
        }
        if b.get(i) == Some(&b'_') {
            return Err(BAD);
        }
        Ok((i, ds))
    };
    let prefixed = b[i] == b'0' && b.get(i + 1).is_some_and(|c| matches!(c | 0x20, b'x' | b'o' | b'b'));
    if prefixed {
        let radix = match b[i + 1] | 0x20 {
            b'x' => 16,
            b'o' => 8,
            _ => 2,
        };
        let (end, ds) = run(i + 2, radix, true)?;
        if ds.is_empty() {
            return Err(BAD);
        }
        i = end;
    } else {
        let (end, int) = run(i, 10, false)?;
        i = end;
        let mut is_int = true;
        if b.get(i) == Some(&b'.') {
            is_int = false;
            i += 1;
            if b.get(i).is_some_and(u8::is_ascii_digit) {
                i = run(i, 10, false)?.0;
            } else if int.is_empty() {
                return Err(BAD);
            }
        }
        if b.get(i).is_some_and(|c| c | 0x20 == b'e') {
            is_int = false;
            i += 1;
            if matches!(b.get(i), Some(b'+' | b'-')) {
                i += 1;
            }
            let (end, ds) = run(i, 10, false)?;
            if ds.is_empty() {
                return Err(BAD);
            }
            i = end;
        }
        if is_int && int.first() == Some(&b'0') && int.iter().any(|c| *c != b'0') {
            return Err(BAD);
        }
    }
    if b.get(i).is_some_and(|c| ident(*c) || *c == b'.') {
        return Err(BAD);
    }
    Ok(i)
}

struct P<'a> {
    t: &'a [Token],
    i: usize,
    depth: u32,
}

impl P<'_> {
    fn tok(&self) -> &Tok {
        // `lex::tokenize` always ends in `Eof`, and nothing reads past it.
        &self.t[self.i.min(self.t.len() - 1)].tok
    }

    fn op(&self, o: &str) -> bool {
        matches!(self.tok(), Tok::Op(x) if *x == o)
    }

    fn eat(&mut self, o: &str) -> bool {
        let hit = self.op(o);
        if hit {
            self.i += 1;
        }
        hit
    }

    /// The top level of `mode='eval'`: an expression, or a bare tuple.
    fn top(&mut self) -> Result<Value, &'static str> {
        let first = self.value()?;
        if !self.op(",") {
            return Ok(first);
        }
        let mut v = vec![first];
        while self.eat(",") {
            if matches!(self.tok(), Tok::Newline | Tok::Eof) {
                break;
            }
            v.push(self.value()?);
        }
        Ok(Value::Tuple(Rc::new(v)))
    }

    /// Comma-separated values up to `close`, which is consumed; and whether a
    /// comma was seen.
    fn items(&mut self, close: &str, mut v: Vec<Value>) -> Result<(Vec<Value>, bool), &'static str> {
        let mut comma = false;
        loop {
            if self.eat(close) {
                return Ok((v, comma));
            }
            if !v.is_empty() {
                if !self.eat(",") {
                    return Err("a display that is not comma-separated literals");
                }
                comma = true;
                if self.eat(close) {
                    return Ok((v, comma));
                }
            }
            v.push(self.value()?);
        }
    }

    fn value(&mut self) -> Result<Value, &'static str> {
        self.depth += 1;
        if self.depth > MAX_DEPTH {
            return Err("nesting deeper than this engine serves");
        }
        let v = self.atom()?;
        self.depth -= 1;
        Ok(v)
    }

    fn atom(&mut self) -> Result<Value, &'static str> {
        let tok = self.tok().clone();
        self.i += 1;
        Ok(match tok {
            Tok::Str { value, is_bytes } => {
                let mut all = value;
                while let Tok::Str { value, is_bytes: b } = self.tok() {
                    if *b != is_bytes {
                        return Err("bytes and str literals concatenated");
                    }
                    all.extend_from_slice(value);
                    self.i += 1;
                }
                if is_bytes {
                    Value::Bytes(Rc::new(all))
                } else {
                    Value::Str(String::from_utf8(all).map_err(|_| "a str literal that is not text")?.into())
                }
            }
            Tok::Int(n) => Value::Int(n),
            Tok::Float(f) => Value::Float(f),
            Tok::Op(s @ ("+" | "-")) => {
                let neg = s == "-";
                let t = self.tok().clone();
                self.i += 1;
                match t {
                    Tok::Float(f) => Value::Float(if neg { -f } else { f }),
                    Tok::Int(n) if !neg => Value::Int(n),
                    Tok::Int(n) => match n.small().and_then(i64::checked_neg) {
                        Some(v) => Value::Int(Int::S(v)),
                        #[cfg(feature = "cap-bigint")]
                        None => crate::bigint::neg(&n),
                        #[cfg(not(feature = "cap-bigint"))]
                        None => return Err("an integer beyond 64 bits"),
                    },
                    _ => return Err("a sign before anything but a number"),
                }
            }
            Tok::Name(n) => match n.as_str() {
                "True" => Value::Bool(true),
                "False" => Value::Bool(false),
                "None" => Value::None,
                "set" if self.op("(") && matches!(self.t.get(self.i + 1).map(|t| &t.tok), Some(Tok::Op(")"))) => {
                    self.i += 2;
                    Value::Set(Rc::new(RefCell::new(Set::new())))
                }
                _ => return Err("a name, which is not a literal"),
            },
            Tok::Op("(") => {
                let (v, comma) = self.items(")", Vec::new())?;
                if v.len() == 1 && !comma {
                    v.into_iter().next().unwrap_or(Value::None)
                } else {
                    Value::Tuple(Rc::new(v))
                }
            }
            Tok::Op("[") => crate::value::list(self.items("]", Vec::new())?.0),
            Tok::Op("{") => {
                if self.eat("}") {
                    return Ok(Value::Dict(Rc::new(RefCell::new(Dict::new()))));
                }
                let first = self.value()?;
                if self.eat(":") {
                    self.dict(first)?
                } else {
                    let (v, _) = self.items("}", vec![first])?;
                    let mut s = Set::new();
                    for x in v {
                        if !hashable(&x) {
                            return Err("an unhashable set element");
                        }
                        s.add(x).map_err(|_| "a set element this engine cannot hash")?;
                    }
                    Value::Set(Rc::new(RefCell::new(s)))
                }
            }
            _ => return Err("a construct that is not a literal"),
        })
    }

    /// The rest of a dict display after `first :`. Last value wins, the first
    /// key object and its position stay — `Dict::insert`, as CPython's.
    fn dict(&mut self, first: Value) -> Result<Value, &'static str> {
        let mut d = Dict::new();
        let mut k = first;
        loop {
            let v = self.value()?;
            if !hashable(&k) {
                return Err("an unhashable dict key");
            }
            d.insert(k, v).map_err(|_| "a dict key this engine cannot hash")?;
            if self.eat("}") {
                break;
            }
            if !self.eat(",") {
                return Err("a dict display that is not comma-separated pairs");
            }
            if self.eat("}") {
                break;
            }
            k = self.value()?;
            if !self.eat(":") {
                return Err("a dict display mixing pairs and elements");
            }
        }
        Ok(Value::Dict(Rc::new(RefCell::new(d))))
    }
}

/// What `literal_eval` can build that CPython hashes: everything but a list,
/// dict or set, and a tuple of hashables.
fn hashable(v: &Value) -> bool {
    match v {
        Value::List(_) | Value::Dict(_) | Value::Set(_) => false,
        Value::Tuple(t) => t.iter().all(hashable),
        _ => true,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_screen_refuses_what_lex_would_misread() {
        for s in [
            "'\\x+1'", "'\\u+041'", "1\0", "1 # \0", "'\\d'", "'\\777'", "b'\\u0041'", "'\\N{EM DASH}'",
            "1if 1 else 2", "0x1for", "1_", "1__0", "0777", "1j", "1..real", "1e", "x\\\n", "f'a'",
            "ur'a'", "1\n ", "1\n\t", "1\r", "'\\x4'", "'\\ud800x'", "b'\u{e9}'", "# coding: latin-1\n1",
        ] {
            assert!(literal_eval(s).is_err(), "{s:?} must refuse");
        }
        for s in ["1", "0x_1f", "1_000", "1.", ".5", "1e5", "1E+5", "1.e-3", "0_0", "00", "09.5", "'\\x41\\101\\n'", "r'\\d'"] {
            assert!(screen(s.as_bytes()).is_ok(), "{s:?} must pass the screen");
        }
    }

    #[test]
    fn the_grammar_refuses_every_non_literal() {
        for s in ["--1", "-(1)", "+True", "-True", "...", "1;2", "[1]*2", "x", "b'a' 'b'", "{**{}}", "set(())",
            "(set)()", "{[1]: 2}", "{(1, [2])}", "1 2", "", "()()", "[1][0]", "1+2", "{1: 2, 3}", "{1, 2: 3}",
            "1\n2", "(,)", "[,]"] {
            assert!(literal_eval(s).is_err(), "{s:?} must refuse");
        }
    }

    /// `route::MODULE_ATTRS` is read by the CORE, which has none of this file
    /// compiled in — so the variant that DOES have `module_attr` holds the two
    /// lists to each other.
    #[test]
    fn the_route_table_names_exactly_what_is_served() {
        let table = crate::route::MODULE_ATTRS
            .iter()
            .find(|(m, _)| *m == "ast")
            .expect("route::MODULE_ATTRS has no ast row")
            .1;
        for name in table {
            assert!(module_attr(name).is_ok(), "route claims ast.{name} and pyast.rs refuses it");
        }
        for name in SERVED {
            assert!(table.contains(name), "pyast.rs serves ast.{name} and route::MODULE_ATTRS omits it");
        }
        for name in ["parse", "walk", "dump", "unparse", "AST", "Name", "NodeVisitor", "get_docstring"] {
            assert!(module_attr(name).is_err(), "ast.{name} must refuse");
        }
    }
}
