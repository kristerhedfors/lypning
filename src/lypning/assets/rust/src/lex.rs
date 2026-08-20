//! Tokenizer for the lypning subset.
//!
//! Layout handling (INDENT/DEDENT) is done here rather than in the parser so
//! the parser can stay a plain recursive-descent walk over a flat token slice.
//!
//! Anything the lexer cannot represent EXACTLY becomes an `Unsupported` error
//! rather than a guess. A tokenizer that quietly mis-reads a literal is the
//! silent-divergence failure mode the whole project exists to avoid (the lypning-mp
//! skill §2): the agent that typed the one-liner will not notice.

use crate::err::{unsupported, LypningError};

#[derive(Debug, Clone, PartialEq)]
pub enum Tok {
    Name(String),
    Int(i64),
    Float(f64),
    /// A string literal, already decoded. `is_bytes` distinguishes b"".
    Str { value: Vec<u8>, is_bytes: bool },
    /// An f-string, kept as its raw inner source; the parser expands it.
    FStr { raw: String, raw_prefix: bool },
    Op(&'static str),
    Newline,
    Indent,
    Dedent,
    Eof,
}

#[derive(Debug, Clone)]
pub struct Token {
    pub tok: Tok,
    pub line: u32,
}

/// Multi-character operators, longest first — the match is greedy.
const OPS: &[&str] = &[
    "**=", "//=", ">>=", "<<=", "...", "!=", "==", "<=", ">=", "->", ":=", "**", "//", "<<", ">>",
    "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "@=", "+", "-", "*", "/", "%", "@", "&", "|",
    "^", "~", "<", ">", "(", ")", "[", "]", "{", "}", ",", ":", ".", ";", "=",
];

const KEYWORDS: &[&str] = &[
    "False", "None", "True", "and", "as", "assert", "async", "await", "break", "class", "continue",
    "def", "del", "elif", "else", "except", "finally", "for", "from", "global", "if", "import",
    "in", "is", "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try", "while",
    "with", "yield",
];

pub fn is_keyword(s: &str) -> bool {
    KEYWORDS.contains(&s)
}

pub struct Lexer<'a> {
    src: &'a [u8],
    pos: usize,
    line: u32,
    /// Bracket nesting depth. Inside brackets, newlines and indentation are
    /// implicit continuations and produce no tokens at all.
    depth: i32,
    indents: Vec<u32>,
    out: Vec<Token>,
}

pub fn tokenize(src: &str) -> Result<Vec<Token>, LypningError> {
    Lexer {
        src: src.as_bytes(),
        pos: 0,
        line: 1,
        depth: 0,
        indents: vec![0],
        out: Vec::new(),
    }
    .run()
}

impl<'a> Lexer<'a> {
    fn peek(&self) -> u8 {
        *self.src.get(self.pos).unwrap_or(&0)
    }
    fn at(&self, n: usize) -> u8 {
        *self.src.get(self.pos + n).unwrap_or(&0)
    }
    fn push(&mut self, tok: Tok) {
        let line = self.line;
        self.out.push(Token { tok, line });
    }

    fn run(mut self) -> Result<Vec<Token>, LypningError> {
        let mut at_line_start = true;
        loop {
            if at_line_start && self.depth == 0 {
                if !self.layout()? {
                    break;
                }
                at_line_start = false;
                continue;
            }
            match self.peek() {
                0 => break,
                b' ' | b'\t' | 0x0c => {
                    self.pos += 1;
                }
                b'\r' => {
                    self.pos += 1;
                }
                b'#' => {
                    while self.peek() != b'\n' && self.peek() != 0 {
                        self.pos += 1;
                    }
                }
                b'\\' if self.at(1) == b'\n' => {
                    self.pos += 2;
                    self.line += 1;
                }
                b'\\' if self.at(1) == b'\r' && self.at(2) == b'\n' => {
                    self.pos += 3;
                    self.line += 1;
                }
                b'\n' => {
                    self.pos += 1;
                    if self.depth == 0 {
                        self.push(Tok::Newline);
                        self.line += 1;
                        at_line_start = true;
                    } else {
                        self.line += 1;
                    }
                }
                c => {
                    if c.is_ascii_digit() || (c == b'.' && self.at(1).is_ascii_digit()) {
                        self.number()?;
                    } else if is_ident_start(c) {
                        self.name_or_string()?;
                    } else if c == b'"' || c == b'\'' {
                        let (value, is_bytes) = self.string(false, false, false)?;
                        self.push(Tok::Str { value, is_bytes });
                    } else {
                        self.operator()?;
                    }
                }
            }
        }
        // A file that does not end in a newline still terminates its last
        // statement; and every open block closes at EOF.
        if !matches!(self.out.last().map(|t| &t.tok), Some(Tok::Newline) | None) {
            self.push(Tok::Newline);
        }
        while self.indents.len() > 1 {
            self.indents.pop();
            self.push(Tok::Dedent);
        }
        self.push(Tok::Eof);
        Ok(self.out)
    }

    /// Handle start-of-line indentation. Returns false at end of input.
    fn layout(&mut self) -> Result<bool, LypningError> {
        loop {
            let mut col: u32 = 0;
            let start = self.pos;
            loop {
                match self.peek() {
                    b' ' => {
                        col += 1;
                        self.pos += 1;
                    }
                    // CPython's tokenizer expands tabs to the next multiple of
                    // 8. Mixed tabs and spaces are a real source of divergence,
                    // so match the rule rather than counting a tab as one.
                    b'\t' => {
                        col = (col / 8 + 1) * 8;
                        self.pos += 1;
                    }
                    b'\x0c' => {
                        col = 0;
                        self.pos += 1;
                    }
                    _ => break,
                }
            }
            match self.peek() {
                0 => {
                    self.pos = start;
                    return Ok(false);
                }
                // Blank and comment-only lines carry no indentation information.
                b'\n' => {
                    self.pos += 1;
                    self.line += 1;
                    continue;
                }
                b'\r' => {
                    self.pos += 1;
                    continue;
                }
                b'#' => {
                    while self.peek() != b'\n' && self.peek() != 0 {
                        self.pos += 1;
                    }
                    continue;
                }
                _ => {}
            }
            let cur = *self.indents.last().unwrap();
            if col > cur {
                self.indents.push(col);
                self.push(Tok::Indent);
            } else if col < cur {
                while *self.indents.last().unwrap() > col {
                    self.indents.pop();
                    self.push(Tok::Dedent);
                }
                if *self.indents.last().unwrap() != col {
                    return Err(LypningError::syntax(self.line, "unindent does not match any outer indentation level"));
                }
            }
            return Ok(true);
        }
    }

    fn operator(&mut self) -> Result<(), LypningError> {
        for op in OPS {
            let b = op.as_bytes();
            if self.src[self.pos..].starts_with(b) {
                self.pos += b.len();
                match *op {
                    "(" | "[" | "{" => self.depth += 1,
                    ")" | "]" | "}" => self.depth -= 1,
                    _ => {}
                }
                self.push(Tok::Op(op));
                return Ok(());
            }
        }
        if self.peek() == b'!' {
            return Err(LypningError::syntax(self.line, "invalid syntax"));
        }
        Err(unsupported(
            "token",
            &format!("byte 0x{:02x} at line {}", self.peek(), self.line),
        ))
    }

    fn number(&mut self) -> Result<(), LypningError> {
        let start = self.pos;
        // 0x / 0o / 0b
        if self.peek() == b'0' && matches!(self.at(1) | 0x20, b'x' | b'o' | b'b') {
            let radix = match self.at(1) | 0x20 {
                b'x' => 16,
                b'o' => 8,
                _ => 2,
            };
            self.pos += 2;
            let ds = self.pos;
            while self.peek().is_ascii_alphanumeric() || self.peek() == b'_' {
                self.pos += 1;
            }
            let text: String = std::str::from_utf8(&self.src[ds..self.pos])
                .unwrap_or("")
                .chars()
                .filter(|c| *c != '_')
                .collect();
            let v = i64::from_str_radix(&text, radix)
                .map_err(|_| unsupported("bigint", "integer literal beyond 64-bit range"))?;
            self.push(Tok::Int(v));
            return Ok(());
        }
        let mut is_float = false;
        while self.peek().is_ascii_digit() || self.peek() == b'_' {
            self.pos += 1;
        }
        if self.peek() == b'.' && self.at(1) != b'.' {
            is_float = true;
            self.pos += 1;
            while self.peek().is_ascii_digit() || self.peek() == b'_' {
                self.pos += 1;
            }
        }
        if (self.peek() | 0x20) == b'e'
            && (self.at(1).is_ascii_digit()
                || ((self.at(1) == b'+' || self.at(1) == b'-') && self.at(2).is_ascii_digit()))
        {
            is_float = true;
            self.pos += 2;
            while self.peek().is_ascii_digit() || self.peek() == b'_' {
                self.pos += 1;
            }
        }
        if (self.peek() | 0x20) == b'j' {
            return Err(unsupported("complex", "complex literal"));
        }
        let text: String = std::str::from_utf8(&self.src[start..self.pos])
            .unwrap_or("")
            .chars()
            .filter(|c| *c != '_')
            .collect();
        if is_float {
            self.push(Tok::Float(text.parse::<f64>().unwrap_or(f64::NAN)));
        } else {
            match text.parse::<i64>() {
                Ok(v) => self.push(Tok::Int(v)),
                // Python has arbitrary-precision ints. Refusing is the only
                // honest answer; guessing would be a silent wrong number.
                Err(_) => return Err(unsupported("bigint", "integer literal beyond 64-bit range")),
            }
        }
        Ok(())
    }

    /// An identifier, a keyword, or a prefixed string literal (r/b/f/u and pairs).
    fn name_or_string(&mut self) -> Result<(), LypningError> {
        let start = self.pos;
        while is_ident_cont(self.peek()) {
            self.pos += 1;
        }
        let word = std::str::from_utf8(&self.src[start..self.pos])
            .map_err(|_| unsupported("token", "non-utf8 identifier"))?
            .to_string();
        if (self.peek() == b'"' || self.peek() == b'\'') && word.len() <= 2 {
            let lower = word.to_ascii_lowercase();
            let (raw, bytes, fstr, uni) = (
                lower.contains('r'),
                lower.contains('b'),
                lower.contains('f'),
                lower.contains('u'),
            );
            if raw || bytes || fstr || uni {
                if fstr {
                    let text = self.raw_string_body()?;
                    self.push(Tok::FStr {
                        raw: text,
                        raw_prefix: raw,
                    });
                } else {
                    let (value, _) = self.string(raw, bytes, false)?;
                    self.push(Tok::Str {
                        value,
                        is_bytes: bytes,
                    });
                }
                return Ok(());
            }
        }
        self.push(Tok::Name(word));
        Ok(())
    }

    /// Read a string literal body verbatim (used by f-strings, which the parser
    /// re-lexes after splitting on the replacement fields).
    fn raw_string_body(&mut self) -> Result<String, LypningError> {
        let quote = self.peek();
        let triple = self.at(1) == quote && self.at(2) == quote;
        let qlen = if triple { 3 } else { 1 };
        self.pos += qlen;
        let start = self.pos;
        loop {
            match self.peek() {
                0 => return Err(LypningError::syntax(self.line, "unterminated string literal")),
                b'\n' if !triple => {
                    return Err(LypningError::syntax(self.line, "unterminated string literal"))
                }
                b'\n' => {
                    self.line += 1;
                    self.pos += 1;
                }
                b'\\' => {
                    if self.at(1) == b'\n' {
                        self.line += 1;
                    }
                    self.pos += 2;
                }
                c if c == quote => {
                    let closes = if triple {
                        self.at(1) == quote && self.at(2) == quote
                    } else {
                        true
                    };
                    if closes {
                        let body = std::str::from_utf8(&self.src[start..self.pos])
                            .map_err(|_| unsupported("token", "non-utf8 string"))?
                            .to_string();
                        self.pos += qlen;
                        return Ok(body);
                    }
                    self.pos += 1;
                }
                _ => self.pos += 1,
            }
        }
    }

    fn string(&mut self, raw: bool, bytes: bool, _f: bool) -> Result<(Vec<u8>, bool), LypningError> {
        let line = self.line;
        let body = self.raw_string_body()?;
        let decoded = if raw {
            body.into_bytes()
        } else {
            decode_escapes(&body, bytes, line)?
        };
        Ok((decoded, bytes))
    }
}

fn is_ident_start(c: u8) -> bool {
    c == b'_' || c.is_ascii_alphabetic() || c >= 0x80
}
fn is_ident_cont(c: u8) -> bool {
    is_ident_start(c) || c.is_ascii_digit()
}

/// Decode Python string escapes. An escape we do not know is left verbatim
/// (backslash included), which is what CPython does for unknown escapes.
pub fn decode_escapes(s: &str, bytes: bool, line: u32) -> Result<Vec<u8>, LypningError> {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] != b'\\' {
            out.push(b[i]);
            i += 1;
            continue;
        }
        i += 1;
        if i >= b.len() {
            out.push(b'\\');
            break;
        }
        let c = b[i];
        i += 1;
        match c {
            b'n' => out.push(b'\n'),
            b't' => out.push(b'\t'),
            b'r' => out.push(b'\r'),
            b'0' | b'1' | b'2' | b'3' | b'4' | b'5' | b'6' | b'7' => {
                let mut v = (c - b'0') as u32;
                let mut n = 1;
                while n < 3 && i < b.len() && (b'0'..=b'7').contains(&b[i]) {
                    v = v * 8 + (b[i] - b'0') as u32;
                    i += 1;
                    n += 1;
                }
                if bytes || v < 0x80 {
                    out.push(v as u8);
                } else {
                    push_char(&mut out, v, line)?;
                }
            }
            b'\\' => out.push(b'\\'),
            b'\'' => out.push(b'\''),
            b'"' => out.push(b'"'),
            b'a' => out.push(0x07),
            b'b' => out.push(0x08),
            b'f' => out.push(0x0c),
            b'v' => out.push(0x0b),
            b'\n' => {}
            b'\r' => {
                if i < b.len() && b[i] == b'\n' {
                    i += 1;
                }
            }
            b'x' | b'u' | b'U' => {
                let n = match c {
                    b'x' => 2,
                    b'u' => 4,
                    _ => 8,
                };
                if c != b'x' && bytes {
                    // In a bytes literal \u is not an escape at all.
                    out.push(b'\\');
                    out.push(c);
                    continue;
                }
                if i + n > b.len() {
                    return Err(LypningError::syntax(line, "truncated \\x escape"));
                }
                let hex = std::str::from_utf8(&b[i..i + n]).unwrap_or("");
                let v = u32::from_str_radix(hex, 16)
                    .map_err(|_| LypningError::syntax(line, "invalid \\x escape"))?;
                i += n;
                if bytes || (c == b'x' && v < 0x80) {
                    out.push(v as u8);
                } else if c == b'x' {
                    push_char(&mut out, v, line)?;
                } else {
                    push_char(&mut out, v, line)?;
                }
            }
            b'N' => return Err(unsupported("escape", "\\N{...} named unicode escape")),
            _ => {
                out.push(b'\\');
                out.push(c);
            }
        }
    }
    Ok(out)
}

fn push_char(out: &mut Vec<u8>, v: u32, line: u32) -> Result<(), LypningError> {
    match char::from_u32(v) {
        Some(ch) => {
            let mut buf = [0u8; 4];
            out.extend_from_slice(ch.encode_utf8(&mut buf).as_bytes());
            Ok(())
        }
        None => Err(LypningError::syntax(line, "invalid unicode escape")),
    }
}
