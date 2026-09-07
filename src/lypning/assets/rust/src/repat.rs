//! `repat` — the `re` pattern PARSER, and the one part of the `re` capability
//! every variant of the spectrum carries.
//!
//! `re.rs` is `cap-re`: the module surface, the compiler back end and the
//! matching machine, in `lypning-l` and in nothing smaller. This file is the
//! front end of the same engine, and it is here — unconditional, next to
//! `route.rs` — for the reason `route.rs`'s glob rules are: **the binary that
//! routes is the CHEAPEST one.** `engines.route` asks `lypning`, and a static
//! blocker only `lypning-l` can compute is inert on exactly the path the
//! dispatcher uses (issue #48). The core stopped on `module: import re`, read
//! `cap-re` off `lypning-l`'s row, and predicted `lypning-l` for a program
//! `lypning-l` refuses before it starts — one wasted spawn each, or worse: the
//! chain hands the program to `lypning-l` with `-c`, so `lypning-l` refuses at
//! RUNTIME — one spawn already spent, and past an effect the barrier cannot
//! take back (an early flush, `os.rmdir`) exit 1, which the chain never
//! retries. `os.makedirs` was on that list until issue #51.
//!
//! **Only the parser, and that is the whole economy of the split.** Every
//! refusal a pattern can raise — an unservable construct, a pattern CPython
//! itself rejects, a bad escape, a flag combination — comes out of
//! [`parse_pattern`]. What follows it in `re::build` (`compile_node`,
//! `fill_peeks`, the `Pat`) cannot fail, so the walker never needs it, and
//! `re::build` calls this function on the same text with the same flags: the
//! two cannot drift, and `re.rs` has the test that says so.

use crate::err::{unsupported, LypningError, R};
use std::rc::Rc;

/// The flag bits, as `sre_constants` spells them. TEMPLATE (1) is never
/// produced; see trap 4.
pub(crate) const I: u32 = 2;
pub(crate) const L: u32 = 4;
pub(crate) const M: u32 = 8;
pub(crate) const S: u32 = 16;
pub(crate) const U: u32 = 32;
pub(crate) const X: u32 = 64;
pub(crate) const DEBUG: u32 = 128;
pub(crate) const A: u32 = 256;

pub(crate) fn refuse(what: &str) -> LypningError {
    unsupported("re", what)
}

/// Is `name` one of the module functions that needs the matcher? The ROUTER
/// asks, so that it can decide the pattern of `re.<name>('…', …)` before the
/// program starts (`route::re_pattern_block`).
pub(crate) fn is_matcher(name: &str) -> bool {
    MATCHER_FNS.binary_search(&name).is_ok()
}

/// Decide a pattern literal for the WALKER, with the default flags.
///
/// A pattern is decided by its text alone in this slice — the flags an engine
/// still serves cannot make an unservable construct servable, and a flag it
/// does not serve refuses on its own at runtime — so the walk can answer from
/// the literal. `Ok(())` means "this pattern is servable, do not block".
///
/// It is [`parse_pattern`] and not `re::build`, and the difference is the whole
/// reason this module exists: every refusal a pattern can raise comes out of
/// the PARSE, and what follows it — `compile_node`, `fill_peeks`, the `Pat` —
/// cannot fail. So the walker needs the parser and not the compiler, and the
/// core's share of `re` is the parser alone: 834,720 B to 851,264 B, both 7
/// device blocks, measured 2026-09-06 on Darwin arm64 — where the whole of
/// `cap-re` in the core measured 917,520 B on the same day, 16 B past the
/// seventh block. `re::build` calls the same function on the same text, so the
/// two can neither drift nor disagree; `re.rs` has the test.
pub(crate) fn precompile(src: &str) -> R<()> {
    parse_pattern(&Rc::from(src), 0).map(|_| ())
}

/// The module functions the matcher backs, sorted.
pub(crate) const MATCHER_FNS: &[&str] = &[
    "compile", "findall", "finditer", "fullmatch", "match", "search", "split", "sub", "subn",
];

/// `sre_constants.MAXREPEAT`. `a{4294967295}` is an OverflowError in CPython,
/// so this value is the ceiling and not a legal count.
const MAXREPEAT: u32 = 4294967295;

// ---- character classes ----------------------------------------------------

/// Category bits. ASCII-exact: `\d` is `0-9`, `\w` is `[0-9A-Za-z_]`, and `\s`
/// is `\t\n\v\f\r` plus the space AND `\x1c-\x1f`, which `str.isspace()`
/// includes and the `re.A` table does not — the one place the two spellings of
/// `\s` differ inside ASCII.
const C_D: u8 = 1;
const C_ND: u8 = 2;
const C_W: u8 = 4;
const C_NW: u8 = 8;
const C_S: u8 = 16;
const C_NS: u8 = 32;

pub(crate) struct Class {
    pub(crate) negate: bool,
    pub(crate) ranges: Vec<(u32, u32)>,
    pub(crate) cats: u8,
}

pub(crate) fn is_word(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

/// `\s` without `re.A`: `str.isspace()` restricted to ASCII, which is the
/// five C escapes, the space, and the four separators `\x1c-\x1f`.
fn is_space_u(c: char) -> bool {
    matches!(c, '\t' | '\n' | '\x0b' | '\x0c' | '\r' | ' ' | '\x1c'..='\x1f')
}

/// `\s` under `re.A`: `sre`'s ASCII table, which stops at `\r`.
fn is_space_a(c: char) -> bool {
    matches!(c, '\t' | '\n' | '\x0b' | '\x0c' | '\r' | ' ')
}

impl Class {
    pub(crate) fn raw(&self, c: char, ascii: bool) -> bool {
        let u = c as u32;
        for (lo, hi) in &self.ranges {
            if u >= *lo && u <= *hi {
                return true;
            }
        }
        if self.cats == 0 {
            return false;
        }
        let sp = if ascii { is_space_a(c) } else { is_space_u(c) };
        (self.cats & C_D != 0 && c.is_ascii_digit())
            || (self.cats & C_ND != 0 && !c.is_ascii_digit())
            || (self.cats & C_W != 0 && is_word(c))
            || (self.cats & C_NW != 0 && !is_word(c))
            || (self.cats & C_S != 0 && sp)
            || (self.cats & C_NS != 0 && !sp)
    }
}

/// The ASCII case twin of `c`, or `c` itself. `re.IGNORECASE` over non-ASCII
/// refuses before any of this runs, so an ASCII fold is the exact one.
pub(crate) fn swap_ascii(c: char) -> char {
    if c.is_ascii_uppercase() {
        c.to_ascii_lowercase()
    } else if c.is_ascii_lowercase() {
        c.to_ascii_uppercase()
    } else {
        c
    }
}

// ---- where the parse tree anchors ------------------------------------------

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum At {
    /// `^` without MULTILINE, and `\A`: index 0 of the REAL string, not `pos`
    /// — `re.compile(r'^b').search('ab', 1)` is None.
    Begin,
    BeginLine,
    /// `$`: at `endpos`, or one before it with a `\n` there. `\Z` is [`At::StrEnd`].
    End,
    EndLine,
    StrEnd,
    WordB,
    NotWordB,
}

// ---- refusal spelling -----------------------------------------------------

/// The pattern text inside a refusal detail, escaped and truncated.
///
/// A refusal is ONE line on stderr (invariant 2), so a pattern with a newline
/// in it cannot be pasted in raw — and `fmt::str_repr` is not usable either,
/// because it refuses on code points whose printability is CPython's table.
pub(crate) fn show(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('\'');
    for (n, c) in s.chars().enumerate() {
        if n == 60 {
            out.push('…');
            break;
        }
        // Only what would BREAK the one-line contract is escaped. A backslash
        // is left alone on purpose: `pattern '\d+'` is the text the user typed
        // and the row `--plan` groups by, and `pattern '\\d+'` is a second
        // thing to decode before reading it.
        match c {
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32))
            }
            c => out.push(c),
        }
    }
    out.push('\'');
    out
}

/// A pattern CPython rejects. Its `re.error` carries a message and a position
/// that moved between 3.11, 3.12, 3.13 and 3.14 (and the class was renamed in
/// 3.13), so the text is CPython's to print, one spawn later — this names the
/// category only.
fn bad_pattern(src: &str, category: &str) -> LypningError {
    refuse(&format!(
        "pattern {}, which CPython rejects: {category}",
        show(src)
    ))
}

/// A pattern this engine does not serve yet. Ranked one row per construct by
/// `conformance --plan`, which is the build order for the next slice.
fn no_construct(src: &str, construct: &str) -> LypningError {
    refuse(&format!("pattern {}: {construct}", show(src)))
}

// ---- the parse tree -------------------------------------------------------

pub(crate) enum Node {
    Empty,
    Lit(char),
    Class(u32),
    Any,
    At(At),
    /// `Some(n)` is the capturing group's 1-based number.
    Group(Option<u32>, Box<Node>),
    Cat(Vec<Node>),
    Alt(Vec<Node>),
    Rep {
        min: u32,
        max: u32,
        greedy: bool,
        body: Box<Node>,
    },
}

impl Node {
    fn is_at(&self) -> bool {
        matches!(self, Node::At(_))
    }
    fn is_rep(&self) -> bool {
        matches!(self, Node::Rep { .. })
    }
}

/// `(?aiLmsux)` at the head of a pattern, and nowhere else: CPython 3.11+
/// raises `global flags not at the start of the expression` for
/// `a(?i)b` and even for `()(?i)`, so a leading run is the whole of it.
/// `(?i)(?m)a` is legal and is two turns of this loop.
fn leading_flags(src: &[char], out: &mut u32) -> R<usize> {
    let mut i = 0;
    loop {
        if src.get(i) != Some(&'(') || src.get(i + 1) != Some(&'?') {
            return Ok(i);
        }
        let mut j = i + 2;
        let mut bits = 0u32;
        while let Some(c) = src.get(j) {
            let b = match c {
                'i' => I,
                'm' => M,
                's' => S,
                'x' => X,
                'a' => A,
                'u' => U,
                'L' => L,
                _ => break,
            };
            bits |= b;
            j += 1;
        }
        if j == i + 2 || src.get(j) != Some(&')') {
            return Ok(i);
        }
        *out |= bits;
        i = j + 1;
    }
}

struct P<'a> {
    s: &'a [char],
    src: &'a str,
    i: usize,
    flags: u32,
    groups: u32,
    classes: Vec<Class>,
    depth: u32,
}

impl<'a> P<'a> {
    fn peek(&self) -> Option<char> {
        self.s.get(self.i).copied()
    }
    fn at(&self, k: usize) -> Option<char> {
        self.s.get(self.i + k).copied()
    }
    fn verbose(&self) -> bool {
        self.flags & X != 0
    }
    fn bad(&self, category: &str) -> LypningError {
        bad_pattern(self.src, category)
    }
    fn no(&self, construct: &str) -> LypningError {
        no_construct(self.src, construct)
    }

    /// VERBOSE skips whitespace and `#` comments — but only HERE, at the top of
    /// the item loop. Not inside `[...]` (which has its own loop), not after a
    /// backslash (the escape reads its own character), and not inside a `{m,n}`
    /// body: `(?x)a \d {2}` quantifies and `(?x)a \d { 2 }` is five literals.
    fn skip_x(&mut self) {
        if !self.verbose() {
            return;
        }
        loop {
            match self.peek() {
                Some(' ') | Some('\t') | Some('\n') | Some('\r') | Some('\x0b')
                | Some('\x0c') => self.i += 1,
                Some('#') => {
                    while let Some(c) = self.peek() {
                        self.i += 1;
                        if c == '\n' {
                            break;
                        }
                    }
                }
                _ => return,
            }
        }
    }

    fn alt(&mut self) -> R<Node> {
        let mut branches = vec![self.cat()?];
        while self.peek() == Some('|') {
            self.i += 1;
            branches.push(self.cat()?);
        }
        Ok(if branches.len() == 1 {
            branches.pop().unwrap()
        } else {
            Node::Alt(branches)
        })
    }

    fn cat(&mut self) -> R<Node> {
        let mut items: Vec<Node> = Vec::new();
        loop {
            self.skip_x();
            let c = match self.peek() {
                None | Some('|') | Some(')') => break,
                Some(c) => c,
            };
            if let Some((min, max)) = self.quantifier(c)? {
                let greedy = match self.peek() {
                    Some('?') => {
                        self.i += 1;
                        false
                    }
                    // `a*+`, `a++`, `a?+`, `a{m,n}+` — 3.11's possessive forms.
                    Some('+') => return Err(self.no("possessive quantifier")),
                    _ => true,
                };
                let last = items.last();
                if last.is_none() || last.unwrap().is_at() {
                    return Err(self.bad("nothing to repeat"));
                }
                if last.unwrap().is_rep() {
                    return Err(self.bad("multiple repeat"));
                }
                let body = items.pop().unwrap();
                items.push(Node::Rep {
                    min,
                    max,
                    greedy,
                    body: Box::new(body),
                });
                continue;
            }
            if let Some(n) = self.atom()? {
                items.push(n);
            }
        }
        Ok(match items.len() {
            0 => Node::Empty,
            1 => items.pop().unwrap(),
            _ => Node::Cat(items),
        })
    }

    /// `*`, `+`, `?`, or a `{...}` whose body is digits and at most one comma.
    /// `a{`, `a{}`, `a{x}`, `a{ 1}` and `a{1,2` are literal braces — the rule
    /// that makes `(?x)a { 2 }` five literals rather than a quantifier.
    fn quantifier(&mut self, c: char) -> R<Option<(u32, u32)>> {
        match c {
            '*' => {
                self.i += 1;
                return Ok(Some((0, MAXREPEAT)));
            }
            '+' => {
                self.i += 1;
                return Ok(Some((1, MAXREPEAT)));
            }
            '?' => {
                self.i += 1;
                return Ok(Some((0, 1)));
            }
            '{' => {}
            _ => return Ok(None),
        }
        if self.at(1) == Some('}') {
            return Ok(None);
        }
        let here = self.i;
        let mut j = self.i + 1;
        let digits = |s: &[char], j: &mut usize| -> Option<u64> {
            let start = *j;
            let mut v: u64 = 0;
            while let Some(c) = s.get(*j) {
                if !c.is_ascii_digit() {
                    break;
                }
                v = v.saturating_mul(10).saturating_add(*c as u64 - '0' as u64);
                *j += 1;
            }
            (*j > start).then_some(v)
        };
        let lo = digits(self.s, &mut j);
        let (hi, comma) = if self.s.get(j) == Some(&',') {
            j += 1;
            (digits(self.s, &mut j), true)
        } else {
            (lo, false)
        };
        if self.s.get(j) != Some(&'}') {
            self.i = here;
            return Ok(None);
        }
        self.i = j + 1;
        let min = lo.unwrap_or(0);
        let max = match hi {
            Some(h) => h,
            None if comma => MAXREPEAT as u64,
            None => 0,
        };
        if min >= MAXREPEAT as u64 || max >= MAXREPEAT as u64 && hi.is_some() {
            return Err(self.bad("the repetition number is too large"));
        }
        if max < min {
            return Err(self.bad("min repeat greater than max repeat"));
        }
        Ok(Some((min as u32, max as u32)))
    }

    /// One item. `Ok(None)` is a `(?#...)` comment, which produces no node.
    fn atom(&mut self) -> R<Option<Node>> {
        let c = self.peek().unwrap();
        Ok(Some(match c {
            '.' => {
                self.i += 1;
                Node::Any
            }
            '^' => {
                self.i += 1;
                Node::At(if self.flags & M != 0 {
                    At::BeginLine
                } else {
                    At::Begin
                })
            }
            '$' => {
                self.i += 1;
                Node::At(if self.flags & M != 0 {
                    At::EndLine
                } else {
                    At::End
                })
            }
            '[' => self.class()?,
            '(' => match self.group()? {
                Some(n) => n,
                None => return Ok(None),
            },
            '\\' => self.escape()?,
            _ => {
                self.i += 1;
                Node::Lit(c)
            }
        }))
    }

    fn group(&mut self) -> R<Option<Node>> {
        self.i += 1; // '('
        if self.peek() != Some('?') {
            self.groups += 1;
            let idx = self.groups;
            let body = self.nested()?;
            if self.peek() != Some(')') {
                return Err(self.bad("missing ), unterminated subpattern"));
            }
            self.i += 1;
            return Ok(Some(Node::Group(Some(idx), Box::new(body))));
        }
        self.i += 1; // '?'
        match self.peek() {
            Some(':') => {
                self.i += 1;
                let body = self.nested()?;
                if self.peek() != Some(')') {
                    return Err(self.bad("missing ), unterminated subpattern"));
                }
                self.i += 1;
                Ok(Some(Node::Group(None, Box::new(body))))
            }
            // `(?#...)` — a comment, in every mode, not just VERBOSE.
            Some('#') => {
                while let Some(c) = self.peek() {
                    self.i += 1;
                    if c == ')' {
                        return Ok(None);
                    }
                }
                Err(self.bad("missing ), unterminated comment"))
            }
            Some('P') => Err(self.no(if self.at(1) == Some('=') {
                "named backreference (?P=name)"
            } else {
                "named group (?P<name>…)"
            })),
            Some('=') => Err(self.no("lookahead (?=…)")),
            Some('!') => Err(self.no("negative lookahead (?!…)")),
            Some('<') => Err(self.no(match self.at(1) {
                Some('=') => "lookbehind (?<=…)",
                Some('!') => "negative lookbehind (?<!…)",
                _ => "the (?<name>…) extension, which CPython rejects",
            })),
            Some('>') => Err(self.no("atomic group (?>…)")),
            Some('(') => Err(self.no("conditional group (?(id)…)")),
            Some(c) if c.is_ascii_alphabetic() || c == '-' => {
                // A scoped `(?i:…)`, a `(?-i)`, or a global flag past the
                // start of the pattern — CPython raises for the last one.
                let mut j = self.i;
                while matches!(self.s.get(j), Some(c) if c.is_ascii_alphabetic() || *c == '-') {
                    j += 1;
                }
                if self.s.get(j) == Some(&':') {
                    Err(self.no("scoped inline flags (?flags:…)"))
                } else {
                    Err(self.bad("global flags not at the start of the expression"))
                }
            }
            _ => Err(self.bad("unknown extension")),
        }
    }

    fn nested(&mut self) -> R<Node> {
        self.depth += 1;
        if self.depth > 120 {
            return Err(self.bad("too deeply nested"));
        }
        let n = self.alt()?;
        self.depth -= 1;
        Ok(n)
    }

    fn cls(&mut self, c: Class) -> Node {
        self.classes.push(c);
        Node::Class(self.classes.len() as u32 - 1)
    }

    /// A single-category shorthand outside a class: `\d` and friends.
    fn cat_node(&mut self, cats: u8) -> Node {
        self.cls(Class {
            negate: false,
            ranges: Vec::new(),
            cats,
        })
    }
}

impl<'a> P<'a> {
    /// A `\` escape outside a character class. Returns a node.
    ///
    /// `sre`'s three-way split, and every branch of it is a trap: a shorthand
    /// or an anchor; a numeric escape, where `\0` and three octal digits are a
    /// character and `\1`..`\99` are a GROUP REFERENCE (`\101` is `'A'` but
    /// `\11` is group 11); or a literal — but only when the character after the
    /// backslash is not an ASCII letter, because `\e`, `\q` and `\p` are
    /// `re.error` in CPython while `\-`, `\_`, `\ ` and `\é` are the character.
    fn escape(&mut self) -> R<Node> {
        self.i += 1;
        let Some(c) = self.peek() else {
            return Err(self.bad("bad escape (end of pattern)"));
        };
        self.i += 1;
        Ok(match c {
            'd' => self.cat_node(C_D),
            'D' => self.cat_node(C_ND),
            'w' => self.cat_node(C_W),
            'W' => self.cat_node(C_NW),
            's' => self.cat_node(C_S),
            'S' => self.cat_node(C_NS),
            'b' => Node::At(At::WordB),
            'B' => Node::At(At::NotWordB),
            'A' => Node::At(At::Begin),
            'Z' => Node::At(At::StrEnd),
            // New in 3.14 and `bad escape \z` on 3.13 and earlier: no answer
            // is right on every interpreter an agent may be holding.
            'z' => return Err(self.no("\\z, which is an anchor in CPython 3.14 and an error before it")),
            'a' => Node::Lit('\x07'),
            'f' => Node::Lit('\x0c'),
            'n' => Node::Lit('\n'),
            'r' => Node::Lit('\r'),
            't' => Node::Lit('\t'),
            'v' => Node::Lit('\x0b'),
            'N' => return Err(self.no("\\N{…}, which needs the Unicode name tables")),
            'x' => Node::Lit(self.hex(2, "incomplete escape \\x")?),
            'u' => Node::Lit(self.hex(4, "incomplete escape \\u")?),
            'U' => Node::Lit(self.hex(8, "incomplete escape \\U")?),
            '0' => {
                // `\0`, `\0d`, `\0dd` — octal, up to two more digits.
                let mut v = 0u32;
                for _ in 0..2 {
                    match self.peek() {
                        Some(d) if ('0'..'8').contains(&d) => {
                            v = v * 8 + (d as u32 - '0' as u32);
                            self.i += 1;
                        }
                        _ => break,
                    }
                }
                Node::Lit(char::from_u32(v).unwrap_or('\0'))
            }
            '1'..='9' => {
                // Octal *or* a decimal group reference, and `sre` decides by
                // looking one further: three octal digits make a character,
                // anything else is a group.
                let mut digits = String::new();
                digits.push(c);
                if matches!(self.peek(), Some(d) if d.is_ascii_digit()) {
                    let d2 = self.peek().unwrap();
                    digits.push(d2);
                    self.i += 1;
                    let oct = |x: char| ('0'..'8').contains(&x);
                    if oct(c) && oct(d2) && matches!(self.peek(), Some(d3) if oct(d3)) {
                        let d3 = self.peek().unwrap();
                        self.i += 1;
                        let v = u32::from_str_radix(&format!("{c}{d2}{d3}"), 8).unwrap_or(0);
                        if v > 0o377 {
                            return Err(self.bad("octal escape value outside of range 0-0o377"));
                        }
                        return Ok(Node::Lit(char::from_u32(v).unwrap_or('\0')));
                    }
                }
                let n: u32 = digits.parse().unwrap_or(0);
                if n > self.groups {
                    return Err(self.bad("invalid group reference"));
                }
                return Err(self.no("backreference \\1..\\99"));
            }
            c if c.is_ascii_alphabetic() => {
                return Err(self.bad(&format!("bad escape \\{c}")))
            }
            c => Node::Lit(c),
        })
    }

    fn hex(&mut self, n: usize, what: &str) -> R<char> {
        let mut v = 0u32;
        for _ in 0..n {
            match self.peek() {
                Some(d) if d.is_ascii_hexdigit() => {
                    v = v * 16 + d.to_digit(16).unwrap();
                    self.i += 1;
                }
                _ => return Err(self.bad(what)),
            }
        }
        char::from_u32(v).ok_or_else(|| self.bad("bad escape (value out of range)"))
    }

    /// `[...]`. `]` first is a literal, `-` at either end is a literal, `\b` is
    /// a backspace rather than a boundary, `[a-c-e]` is `a-c` plus `-` plus
    /// `e`, and the set-operation shapes `[[a]`, `[a&&b]`, `[a||b]`, `[a~~b]`
    /// are literals (CPython's FutureWarning is stderr, which the harness
    /// strips). A range endpoint that is a class escape, or a range running
    /// backwards, is a `re.error`.
    fn class(&mut self) -> R<Node> {
        self.i += 1; // '['
        let mut cl = Class {
            negate: false,
            ranges: Vec::new(),
            cats: 0,
        };
        if self.peek() == Some('^') {
            cl.negate = true;
            self.i += 1;
        }
        let mut first = true;
        loop {
            let Some(c) = self.peek() else {
                return Err(self.bad("unterminated character set"));
            };
            if c == ']' && !first {
                self.i += 1;
                break;
            }
            first = false;
            let lo = self.class_item(&mut cl)?;
            // A `-` that is not the last character starts a range.
            if self.peek() == Some('-') && self.at(1) != Some(']') && self.at(1).is_some() {
                self.i += 1;
                let hi = self.class_item(&mut cl)?;
                match (lo, hi) {
                    (Some(a), Some(b)) => {
                        if (b as u32) < a as u32 {
                            return Err(self.bad("bad character range"));
                        }
                        cl.ranges.push((a as u32, b as u32));
                    }
                    _ => return Err(self.bad("bad character range")),
                }
            } else if let Some(a) = lo {
                cl.ranges.push((a as u32, a as u32));
            }
        }
        Ok(self.cls(cl))
    }

    /// One member. `None` when it was a category shorthand, which folds into
    /// `cl.cats` and can never be a range endpoint.
    fn class_item(&mut self, cl: &mut Class) -> R<Option<char>> {
        let c = self.peek().unwrap();
        if c != '\\' {
            self.i += 1;
            return Ok(Some(c));
        }
        self.i += 1;
        let Some(e) = self.peek() else {
            return Err(self.bad("bad escape (end of pattern)"));
        };
        self.i += 1;
        let cat = match e {
            'd' => C_D,
            'D' => C_ND,
            'w' => C_W,
            'W' => C_NW,
            's' => C_S,
            'S' => C_NS,
            _ => 0,
        };
        if cat != 0 {
            cl.cats |= cat;
            return Ok(None);
        }
        Ok(Some(match e {
            'a' => '\x07',
            // A BACKSPACE inside a class, not a word boundary.
            'b' => '\x08',
            'f' => '\x0c',
            'n' => '\n',
            'r' => '\r',
            't' => '\t',
            'v' => '\x0b',
            'x' => self.hex(2, "incomplete escape \\x")?,
            'u' => self.hex(4, "incomplete escape \\u")?,
            'U' => self.hex(8, "incomplete escape \\U")?,
            'N' => return Err(self.no("\\N{…}, which needs the Unicode name tables")),
            // Inside a class every `\ddd` is octal — `[\1]` is `\x01`, not a
            // group reference.
            '0'..='7' => {
                let mut v = e as u32 - '0' as u32;
                for _ in 0..2 {
                    match self.peek() {
                        Some(d) if ('0'..'8').contains(&d) => {
                            v = v * 8 + (d as u32 - '0' as u32);
                            self.i += 1;
                        }
                        _ => break,
                    }
                }
                if v > 0o377 {
                    return Err(self.bad("octal escape value outside of range 0-0o377"));
                }
                char::from_u32(v).unwrap_or('\0')
            }
            '8' | '9' => return Err(self.bad(&format!("bad escape \\{e}"))),
            c if c.is_ascii_alphabetic() => {
                return Err(self.bad(&format!("bad escape \\{c}")))
            }
            c => c,
        }))
    }
}


/// Parse a pattern, and hand back everything the compiler needs to finish it.
///
/// Split out of `re::build` so that the WALKER can ask the same question the
/// runtime asks without carrying the answer's machinery: this returns the tree,
/// the group count, the classes and the EFFECTIVE-so-far flags, and every `Err`
/// this engine can raise about a pattern is raised here.
pub(crate) fn parse_pattern(src: &Rc<str>, given: u32) -> R<(Node, u32, Vec<Class>, u32)> {
    if given & L != 0 {
        return Err(refuse(
            "flags: re.LOCALE with a str pattern, which CPython answers with a ValueError",
        ));
    }
    if given & DEBUG != 0 {
        return Err(refuse(
            "flags: re.DEBUG, which prints CPython's own parse tree to stdout",
        ));
    }
    if given & !(I | M | S | X | A | U) != 0 {
        return Err(refuse(&format!(
            "flags: unknown flag bits 0x{:x}",
            given & !(I | M | S | X | A | U)
        )));
    }
    let chars: Vec<char> = src.chars().collect();
    let mut flags = given;
    let head = leading_flags(&chars, &mut flags)?;
    if flags & A != 0 && flags & U != 0 {
        return Err(refuse(
            "flags: ASCII and UNICODE together, which CPython answers with a ValueError",
        ));
    }
    if flags & L != 0 {
        return Err(refuse(
            "flags: re.LOCALE with a str pattern, which CPython answers with a ValueError",
        ));
    }
    let mut p = P {
        s: &chars,
        src,
        i: head,
        flags,
        groups: 0,
        classes: Vec::new(),
        depth: 0,
    };
    let tree = p.alt()?;
    if p.i < chars.len() {
        // Only a stray `)` can stop the top-level parse early.
        return Err(bad_pattern(src, "unbalanced parenthesis"));
    }
    Ok((tree, p.groups, p.classes, p.flags))
}
