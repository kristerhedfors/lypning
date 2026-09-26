//! Tokenizer for the lypning subset.
//!
//! Layout handling (INDENT/DEDENT) is done here rather than in the parser so
//! the parser can stay a plain recursive-descent walk over a flat token slice.
//!
//! Layout is also where the ORDER of two errors is decided. CPython's tokenizer
//! reads a line's indentation before it lexes that line and stops there, so an
//! indent no suite asked for hides everything after it; this lexer runs to the
//! end of the source before the parser sees a token, so it has to raise on the
//! indent itself, from `layout`, or it names the wrong cause.
//!
//! Anything the lexer cannot represent EXACTLY becomes an `Unsupported` error
//! rather than a guess. A tokenizer that quietly mis-reads a literal is the
//! silent-divergence failure mode the whole project exists to avoid: the agent
//! that typed the one-liner will not notice.

use crate::err::{unsupported, LypningError};

#[derive(Debug, Clone, PartialEq)]
pub enum Tok {
    Name(String),
    Int(crate::value::Int),
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
    /// Each open level as `(column, column with a tab counted as 1)`: two
    /// readings of one indentation, which CPython requires to agree.
    indents: Vec<(u32, u32)>,
    out: Vec<Token>,
}

pub fn tokenize(src: &str) -> Result<Vec<Token>, LypningError> {
    Lexer {
        src: src.as_bytes(),
        pos: 0,
        line: 1,
        depth: 0,
        indents: vec![(0, 0)],
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
            let mut alt: u32 = 0;
            let start = self.pos;
            loop {
                match self.peek() {
                    b' ' => {
                        col += 1;
                        alt += 1;
                        self.pos += 1;
                    }
                    // CPython's tokenizer expands tabs to the next multiple of
                    // 8. Mixed tabs and spaces are a real source of divergence,
                    // so match the rule rather than counting a tab as one.
                    b'\t' => {
                        col = (col / 8 + 1) * 8;
                        alt += 1;
                        self.pos += 1;
                    }
                    b'\x0c' => {
                        col = 0;
                        alt = 0;
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
            // CPython's `tok_get` reads the indentation twice — a tab to the
            // next multiple of 8, and a tab as 1 — and a line whose two
            // readings order it differently against the open level is a
            // `TabError` (`if 1:` / TAB `x=1` / 8 spaces `y=2`). Counting only
            // the first reading ran it.
            let (cur, cur_alt) = *self.indents.last().unwrap();
            if col > cur {
                if alt <= cur_alt {
                    return Err(tab_error(self.line));
                }
                if !self.indent_opens_a_suite() {
                    return Err(self.unexpected_indent());
                }
                self.indents.push((col, alt));
                self.push(Tok::Indent);
            } else {
                while self.indents.last().unwrap().0 > col {
                    self.indents.pop();
                    self.push(Tok::Dedent);
                }
                let (cur, cur_alt) = *self.indents.last().unwrap();
                if cur != col {
                    return Err(unindent_error(self.line));
                }
                if cur_alt != alt {
                    return Err(tab_error(self.line));
                }
            }
            return Ok(true);
        }
    }

    /// Is the INDENT about to be pushed one a suite asked for?
    ///
    /// Python has exactly one rule that takes an INDENT — `block: NEWLINE
    /// INDENT statements DEDENT` — and `block` appears only after the `:` that
    /// opens a suite, so the two tokens already emitted answer the question
    /// without the lexer knowing any grammar beyond that. Blank and
    /// comment-only lines emit nothing, so `if x:` followed by a comment and
    /// then the body still ends `: NEWLINE` here.
    fn indent_opens_a_suite(&self) -> bool {
        let n = self.out.len();
        n >= 2
            && matches!(self.out[n - 1].tok, Tok::Newline)
            && matches!(self.out[n - 2].tok, Tok::Op(":"))
    }

    /// The indentation at the head of THIS line, when no suite asked for it.
    ///
    /// Raised from the layout pass, at the moment the indent is read, rather
    /// than left for the parser to trip over the `Indent` token later — and the
    /// timing is half the point. CPython's tokenizer reads a logical line's
    /// indentation before it lexes that line, and the parser fails on the INDENT
    /// before anything further down is tokenized at all, so a lexical problem
    /// later in the file never gets the chance to be reported. This lexer
    /// tokenizes the whole source up front, so it was reporting the later
    /// problem instead: corpus `py-771e5de335fc` is an indented paste whose last
    /// line is an unterminated string, and CPython stops at the indent on line 1
    /// while this engine stopped at the string on line 6. Same program, a
    /// different cause named — the silent-divergence shape invariant 1 exists
    /// for, and both being errors is why no exit code gave it away.
    ///
    /// It is a REFUSAL and not this engine's own `IndentationError`, because
    /// neither half of what CPython would say here is knowable from inside a
    /// binary that does not know which CPython it is paired with:
    ///
    /// * **Whether it is an error at all.** Since 3.13 `python -c` dedents the
    ///   command before compiling it, so an indent common to every line is
    ///   simply removed: `python3.14 -c " print(1)"` prints 1 and `python3.11
    ///   -c` on the same text raises `IndentationError`. The two also disagree
    ///   about which error a bad program gets — for `"  print(1)\n  print('x\n"`
    ///   3.11 answers `IndentationError` on line 1 and 3.14 an unterminated
    ///   string on line 2 (measured 2026-09-07 on 3.11.15 and 3.14.5).
    /// * **Whether the indent is even the FIRST thing wrong.** CPython's
    ///   tokenizer has lexical errors this one does not, so an indent this lexer
    ///   reaches may sit behind one CPython stopped at already. Corpus
    ///   `py-50e65eaca71f` is a commit message: CPython rejects `2026-08-21` on
    ///   line 4 (leading zeros in a decimal literal), where this lexer reads
    ///   `08` as 8 and carries on to the indent on line 14. Naming the indent
    ///   there would be as wrong as naming the string was, one line number
    ///   further on.
    ///
    /// So the program leaves by the exit-90 contract and the reference answers
    /// it. That is invariant 1: the answer arrives one spawn later and the
    /// caller reads it, where a guess at CPython's wording would not be noticed.
    fn unexpected_indent(&self) -> LypningError {
        unsupported(
            "indent",
            &format!("line {} is indented where no block opened", self.line),
        )
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
        // Bytes that cannot begin a token in ANY Python 3 program, inside or
        // outside the subset: `!` on its own (`!=` is matched by the operator
        // table above), `$`, `?` and a backtick — Python 2's repr quotes, gone
        // since 2008. CPython answers each with a SyntaxError at exit 1 and
        // empty stdout, so that is what lypning must answer too.
        //
        // A refusal here would be a lie in the other direction: `unsupported`
        // means "outside my subset, try the next interpreter", and there is no
        // interpreter for which `$p` is a program. The corpus has four entries
        // that are shell paste accidents — `$p`, `$1`, and a Rust `r#"…"#`
        // block — and every one of them was costing a spawn to be told by
        // CPython what lypning already knew.
        //
        // Deliberately NOT extended to non-ASCII bytes, which are refused
        // below: Python 3 identifiers may be Unicode (`π = 1` is a valid
        // program), and which characters CPython admits — XID_Start and
        // XID_Continue after NFKC, so `ｘ` IS `x` — takes tables this binary
        // does not carry. The rest (`€`, a no-break or zero-width space) are
        // CPython's SyntaxError. Either way the reference answers; the corpus
        // has no non-ASCII identifier (mined 2026-09-25, 8,901 entries).
        if matches!(self.peek(), b'!' | b'$' | b'?' | b'`') {
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
            // `0x_1` is legal: one underscore may follow the prefix.
            let d = &self.src[ds..self.pos];
            let d = d.strip_prefix(b"_").unwrap_or(d);
            if d.is_empty() || bad_underscores(d) {
                return Err(LypningError::syntax(self.line, "invalid number literal"));
            }
            let text: String = std::str::from_utf8(&self.src[ds..self.pos])
                .unwrap_or("")
                .chars()
                .filter(|c| *c != '_')
                .collect();
            let v = match i64::from_str_radix(&text, radix) {
                Ok(v) => crate::value::Int::S(v),
                Err(_) => wide_literal(&text, radix)?,
            };
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
        if let Some(why) = bad_decimal(&self.src[start..self.pos], is_float) {
            return Err(LypningError::syntax(self.line, why));
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
                Ok(v) => self.push(Tok::Int(crate::value::Int::S(v))),
                // Python has arbitrary-precision ints. On the core, refusing is
                // the only honest answer and guessing would be a silent wrong
                // number; `cap-bigint` reads the digits instead.
                Err(_) => {
                    let v = wide_literal(&text, 10)?;
                    self.push(Tok::Int(v));
                }
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
            // The prefix set is CLOSED, and testing it with `contains` was not
            // testing it at all: `bb"abc"` contains a 'b' and lexed as a bytes
            // literal, `rr"abc"` as a raw one, where CPython raises SyntaxError
            // for both. Accepting a literal CPython rejects is the worst
            // direction for a parser to be wrong in -- the program runs and
            // answers, at exit 0, and no chain retries it.
            //
            // What CPython admits: one of r, b, f, u alone, or the pairs rb/br
            // and rf/fr, in any case. `u` combines with nothing (it is the 2.x
            // spelling kept for source compatibility) and `b` with `f` is a
            // bytes f-string, which does not exist. (py-ab889058c3ba)
            let known = matches!(
                lower.as_str(),
                "r" | "b" | "f" | "u" | "rb" | "br" | "rf" | "fr"
            );
            let (raw, bytes, fstr, uni) = (
                known && lower.contains('r'),
                known && lower.contains('b'),
                known && lower.contains('f'),
                known && lower.contains('u'),
            );
            if raw || bytes || fstr || uni {
                if fstr {
                    let text = self.raw_string_body()?;
                    // A replacement field still open where the body ended met
                    // the f-string's own quote inside it: `f"{d["k"]}"`, valid
                    // from 3.12 (PEP 701) and a SyntaxError before. Which of
                    // the two is the reference's to say.
                    if fstring_field_open(text.as_bytes()) {
                        return Err(unsupported("fstring", "nested quote"));
                    }
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
        // A compile-time error in CPython, raw or not: `b'٣'` never runs.
        if bytes && !body.is_ascii() {
            return Err(LypningError::syntax(
                line,
                "bytes can only contain ASCII literal characters",
            ));
        }
        let decoded = if raw {
            body.into_bytes()
        } else {
            decode_escapes(&body, bytes, line)?
        };
        Ok((decoded, bytes))
    }
}

/// An integer literal whose digits do not fit an `i64`.
///
/// The digits are already stripped of their sign, prefix and underscores. On the
/// frozen core there is nothing to build one out of and this is the refusal the
/// lexer has always raised; on `cap-bigint` it is a value, refused only past
/// CPython's own `sys.get_int_max_str_digits()`, where CPython raises
/// `ValueError` rather than converting.
#[allow(unused_variables)]
fn wide_literal(digits: &str, radix: u32) -> Result<crate::value::Int, LypningError> {
    #[cfg(feature = "cap-bigint")]
    match crate::bigint::parse(digits, radix) {
        Some(v) => return Ok(v),
        None => {
            return Err(unsupported(
                "bigint",
                "an integer literal past sys.get_int_max_str_digits(), where CPython raises ValueError",
            ))
        }
    }
    #[cfg(not(feature = "cap-bigint"))]
    Err(unsupported("bigint", "integer literal beyond 64-bit range"))
}

/// Is a `{` replacement field still open at the end of an f-string body?
/// `{{` and `}}` are literal braces.
fn fstring_field_open(b: &[u8]) -> bool {
    let (mut depth, mut i) = (0usize, 0);
    while i < b.len() {
        match (b[i], b.get(i + 1)) {
            (b'{', Some(b'{')) | (b'}', Some(b'}')) if depth == 0 => i += 1,
            (b'{', _) => depth += 1,
            (b'}', _) => depth = depth.saturating_sub(1),
            _ => {}
        }
        i += 1;
    }
    depth > 0
}

fn is_ident_start(c: u8) -> bool {
    c == b'_' || c.is_ascii_alphabetic()
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
        // A lone surrogate is a legal `str` in CPython — `'\udcff'` is how
        // surrogateescape spells an undecodable byte — and no UTF-8 string can
        // hold one. That is not a syntax error, because CPython compiles it: it
        // is a program this engine cannot run. It answered SyntaxError at exit
        // 1, which the dispatcher returns unchanged as the program's own; a
        // refusal sends it on to CPython instead. Found by the Stage 0a replay
        // (ntx-590d4adaea1e). Above U+10FFFF stays a SyntaxError, as in CPython.
        None if (0xD800..=0xDFFF).contains(&v) => Err(unsupported(
            "escape",
            "\\u escape naming a lone surrogate, which no UTF-8 string can hold",
        )),
        None => Err(LypningError::syntax(line, "invalid unicode escape")),
    }
}

/// CPython raises `TabError` here, and for a dedent that matches no level
/// `IndentationError` — subclasses of `SyntaxError` whose names are the last
/// stderr line, which this engine's `SyntaxError` cannot spell. Refused, as
/// [`Lexer::unexpected_indent`] is: the reference interpreter raises its own.
fn tab_error(_line: u32) -> LypningError {
    unsupported("indent", "TabError")
}

fn unindent_error(_line: u32) -> LypningError {
    unsupported("indent", "IndentationError")
}

/// A run of digits and underscores CPython rejects: an underscore that does
/// not sit BETWEEN two digits — leading, trailing or doubled (`1_`, `1__0`).
fn bad_underscores(run: &[u8]) -> bool {
    run.first() == Some(&b'_') || run.last() == Some(&b'_') || run.windows(2).any(|w| w == b"__")
}

/// The `SyntaxError` CPython's tokenizer raises for a decimal literal this
/// lexer reads anyway: a misplaced underscore in any digit run (the integer
/// part, the fraction, the exponent), and a leading zero on a nonzero integer
/// (`0777`, which is octal in Python 2 and nothing in Python 3). `00` and
/// `0_0` are zero, and `09.5` is a float; all three are legal.
fn bad_decimal(lit: &[u8], is_float: bool) -> Option<&'static str> {
    let runs = lit.split(|c| matches!(c, b'.' | b'e' | b'E' | b'+' | b'-'));
    for run in runs {
        if bad_underscores(run) {
            return Some("invalid decimal literal");
        }
    }
    let digits: Vec<u8> = lit.iter().copied().filter(|c| *c != b'_').collect();
    if !is_float && digits.len() > 1 && digits[0] == b'0' && digits.iter().any(|c| *c != b'0') {
        return Some(
            "leading zeros in decimal integer literals are not permitted; use an 0o prefix for octal integers",
        );
    }
    None
}
