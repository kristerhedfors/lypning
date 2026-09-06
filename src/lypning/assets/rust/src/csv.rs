//! `csv.reader` and `csv.DictReader` — the whole of the `cap-csv` capability,
//! compiled into `lypning-l` and into nothing smaller.
//!
//! **There is no new `Value` variant here, and that is the design.** The first
//! attempt at this capability (`docs/HILLCLIMB.md` iteration 74) added a
//! `Value::CsvWriter` and paid for it with five findings from one root cause: it
//! was wired into `type_name`, `methods`, `ops` and `fmt` but not into
//! `value::eq`, `value::is_same` or hash, so two writers compared unequal at
//! exit 0 and `list.remove(w)` died at exit 1. A variant reaches print, `==`,
//! hash, `sorted`, `in`, `bool`, `len`, iteration, indexing, slice assignment,
//! `del`, augmented assignment, `json.dumps` and `%`-formatting, and the arms it
//! must be wired into are exactly the ones nothing forces you to remember.
//!
//! So a reader here is an **[`Iter::Vec`] behind the existing
//! [`Value::IterObj`]** — the shape `re.finditer` already returns. Every one of
//! those arms is already wired for it: `next(r)` takes it as an iterator,
//! `for row in r` shares the same cursor, `list(r)` collects it, `repr(r)`
//! refuses because CPython's repr carries a heap address, and `type(r).__name__`
//! is the `reader` / `DictReader` CPython prints. Nothing new had to be
//! remembered, because nothing new exists.
//!
//! **The writers refuse, statically.** `csv.writer` and `csv.DictWriter` are the
//! only shapes that need an object with methods, which is precisely what cost
//! five findings; they are absent from [`module_attr`], so the router's walk
//! stops on `module-attr: csv.writer` before the program runs and CPython gets
//! it for the price of one static route. A corpus mine on 2026-09-06 over the
//! 23 programs `--plan` blocks on `import csv` counts 15 readers over a file, 2
//! over `sys.stdin` and 6 writers.
//!
//! **The reader is EAGER, and the file is what makes that safe.** CPython's is
//! lazy over the input iterator, so it has the rows at a different MOMENT, and
//! an adversarial grid found five directions in which the moment is observable.
//! Two are answered here: a parse error is raised at construction (a refusal,
//! and an earlier refusal is a better one — it is further from any side effect
//! that could commit the barrier), and a `float()` that a `QUOTE_NONNUMERIC`
//! field fails is a refusal for the OPPOSITE reason (it is the program's own
//! `ValueError`, not a refusal, so raising it from the wrong statement changes
//! what a `try` catches — see [`Parser::save`]).
//!
//! The other three are events on the FILE, invisible from the reader, so the
//! file tells the reader: `io::csv_register` pairs the two, `io::csv_on_close`
//! turns a close into CPython's own `ValueError: I/O operation on closed file.`
//! on the next row, and `io::csv_on_write` refuses a write a lazy reader would
//! have seen. `sys.stdin` has no second half — it cannot be reopened — so
//! `io::stdin_csv_take` marks it and every later read of it refuses.
//!
//! Every corpus shape consumes the reader immediately, which is why eagerness
//! is still what makes the plain `Iter::Vec` above possible; the bookkeeping is
//! what makes it honest.
//!
//! **The refusals are the design, not the leftovers.** Every `csv.Error`
//! message is CPython's to word, `field_size_limit` and `Sniffer` are CPython's
//! to answer, and a dialect this file cannot reproduce exactly is refused rather
//! than approximated. A refusal is never a bug (CLAUDE.md invariant 1); a row
//! this engine gets subtly wrong at exit 0 is.

use crate::args::Args;
use crate::err::{unsupported, LypningError, R};
use crate::eval::Interp;
use crate::io as mio;
use crate::iter::Iter;
use crate::value::{list, type_name, Dict, Value};
use std::cell::RefCell;
use std::rc::Rc;

pub fn refuse(what: &str) -> LypningError {
    unsupported("csv", what)
}

/// The module functions this engine answers, sorted for [`slice::binary_search`].
///
/// `writer`, `DictWriter`, `Sniffer`, `field_size_limit`, `register_dialect`,
/// `list_dialects`, `get_dialect`, `unregister_dialect`, `excel`, `unix_dialect`
/// and `Error` are all absent on purpose: an absent name is a `module-attr`
/// refusal, and `route.rs` raises that from the WALK, before a byte of the
/// program runs. That is the whole reason the writers are spelled by omission
/// rather than by a runtime `return Err(...)` — a runtime refusal reached after
/// a side effect the commit barrier has let through cannot fall onward.
const MODULE_METHODS: &[&str] = &["DictReader", "reader"];

/// The `QUOTE_*` constants, sorted by name. They are plain `int`s in CPython
/// (`_csv` exports them as C constants, not as an enum), so they are plain ints
/// here and every arithmetic, comparison and `print` path is the int's own.
///
/// `QUOTE_STRINGS` (4) and `QUOTE_NOTNULL` (5) arrived in 3.12 and mean nothing
/// to a reader — `_csv`'s parser tests `quoting == QUOTE_NONNUMERIC` and
/// `quoting != QUOTE_NONE` and nothing else — so serving them would be serving a
/// name whose only visible effect is on a writer this engine refuses. They are
/// absent, which makes them a static `module-attr` refusal.
const CONSTANTS: &[(&str, i64)] = &[
    ("QUOTE_ALL", 1),
    ("QUOTE_MINIMAL", 0),
    ("QUOTE_NONE", 3),
    ("QUOTE_NONNUMERIC", 2),
];

const QUOTE_MINIMAL: i64 = 0;
const QUOTE_NONNUMERIC: i64 = 2;
const QUOTE_NONE: i64 = 3;

/// `_csv.c`'s default, and the limit its error message quotes.
const FIELD_LIMIT: usize = 131_072;

pub fn module_attr(name: &str) -> R<Value> {
    if let Ok(i) = CONSTANTS.binary_search_by(|(n, _)| (*n).cmp(name)) {
        return Ok(Value::Int(CONSTANTS[i].1));
    }
    match MODULE_METHODS.binary_search(&name) {
        Ok(i) => Ok(Value::Bound(
            Rc::new(Value::Module("csv")),
            MODULE_METHODS[i],
        )),
        Err(_) => Err(unsupported("module-attr", &format!("csv.{name}"))),
    }
}

pub fn call(it: &mut Interp, name: &str, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    match name {
        "reader" => reader(it, args, kw),
        "DictReader" => dict_reader(it, args, kw),
        // Unreachable through `module_attr`, which is the only way in; spelled
        // rather than `unreachable!()` because a panic is not a refusal.
        other => Err(refuse(&format!("csv.{other}()"))),
    }
}

// ---- the dialect -----------------------------------------------------------

/// The reader half of a `csv` dialect. `lineterminator` is not here because the
/// READER ignores it entirely (`_csv`'s parser hard-codes `\r` and `\n`) while
/// still VALIDATING it, so accepting it would mean reproducing a validation
/// whose only effect is an error message CPython owns; it refuses instead.
struct Dialect {
    delimiter: char,
    /// `None` is `quotechar=None`, which no character can equal — the same way
    /// `_csv` stores it as a NUL that its own NUL check makes unreachable.
    quotechar: Option<char>,
    escapechar: Option<char>,
    doublequote: bool,
    skipinitialspace: bool,
    quoting: i64,
    strict: bool,
}

impl Default for Dialect {
    /// `csv.excel`, which is what `csv.reader(f)` uses.
    fn default() -> Self {
        Dialect {
            delimiter: ',',
            quotechar: Some('"'),
            escapechar: None,
            doublequote: true,
            skipinitialspace: false,
            quoting: QUOTE_MINIMAL,
            strict: false,
        }
    }
}

/// One character, the way `_csv`'s dialect validation reads one — except that
/// every way of getting it wrong refuses, because each raises a TypeError whose
/// wording has moved between CPython versions.
fn one_char(what: &str, v: &Value) -> R<char> {
    match v {
        Value::Str(s) => {
            let mut cs = s.chars();
            match (cs.next(), cs.next()) {
                (Some(c), None) => Ok(c),
                _ => Err(refuse(&format!(
                    "\"{what}\" must be a 1-character string (CPython raises a TypeError this engine does not word)"
                ))),
            }
        }
        other => Err(refuse(&format!(
            "\"{what}\" as a {} (CPython raises a TypeError this engine does not word)",
            type_name(other)
        ))),
    }
}

fn truth(what: &str, v: &Value) -> R<bool> {
    match v {
        Value::Bool(b) => Ok(*b),
        // CPython takes the truth value of whatever it is given here, and so
        // does this — `crate::ops::truthy` is the one implementation of that.
        other => crate::value::truthy(other).map_err(|_| {
            refuse(&format!(
                "\"{what}\" as a {}",
                type_name(other)
            ))
        }),
    }
}

/// The dialect a `reader(...)` / `DictReader(...)` call names.
///
/// A positional dialect (`csv.reader(f, "excel")`) and a `dialect=` keyword both
/// refuse: a named dialect is a registry lookup whose failure message and whose
/// `unix` row are CPython's, and neither appears in the corpus.
fn dialect_from(args: &Args, kw: &[(Rc<str>, Value)], skip: usize) -> R<Dialect> {
    if args.len() > skip {
        return Err(refuse("a positional dialect argument"));
    }
    let mut d = Dialect::default();
    for (k, v) in kw {
        match k.as_ref() {
            "delimiter" => d.delimiter = one_char("delimiter", v)?,
            "quotechar" => {
                d.quotechar = match v {
                    Value::None => None,
                    other => Some(one_char("quotechar", other)?),
                }
            }
            "escapechar" => {
                d.escapechar = match v {
                    Value::None => None,
                    other => Some(one_char("escapechar", other)?),
                }
            }
            "doublequote" => d.doublequote = truth("doublequote", v)?,
            "skipinitialspace" => d.skipinitialspace = truth("skipinitialspace", v)?,
            "strict" => d.strict = truth("strict", v)?,
            "quoting" => match v {
                Value::Int(n) if (QUOTE_MINIMAL..=QUOTE_NONE).contains(n) => d.quoting = *n,
                _ => {
                    return Err(refuse(
                        "quoting= outside QUOTE_MINIMAL..QUOTE_NONE (CPython's message names a \
                         range this engine does not reproduce)",
                    ))
                }
            },
            other => return Err(refuse(&format!("{other}= (a dialect this engine does not serve)"))),
        }
    }
    // `quotechar=None` with any quoting but QUOTE_NONE is a TypeError in
    // CPython, worded by the version. Refused rather than parsed as though the
    // quoting had been QUOTE_NONE, which is what ignoring it would amount to.
    if d.quotechar.is_none() && d.quoting != QUOTE_NONE {
        return Err(refuse("quotechar=None with quoting other than QUOTE_NONE"));
    }
    check_chars(&d)?;
    Ok(d)
}

/// `_csv.c`'s `dialect_check_char`, which runs on every dialect character
/// BEFORE a byte is parsed and which the first cut of this file did not have:
/// each character was validated in ISOLATION, so a dialect CPython rejects
/// outright — `delimiter=quotechar`, `escapechar=delimiter`,
/// `escapechar=quotechar`, or any of the three spelled as `\r` or `\n` —
/// parsed here and printed rows at exit 0. Twenty-seven rows of a 152-row
/// dialect grid, all of this one family.
///
/// Refused rather than worded: these are `ValueError: bad delimiter or
/// quotechar value` and its four siblings, whose exact spelling has moved
/// between CPython versions and which the module's own policy leaves to
/// CPython. The refusal lands at `csv.reader(…)`, before a row exists, which
/// is as far from a committed side effect as this capability can put it.
fn check_chars(d: &Dialect) -> R<()> {
    for (what, c) in [
        ("delimiter", Some(d.delimiter)),
        ("quotechar", d.quotechar),
        ("escapechar", d.escapechar),
    ] {
        if matches!(c, Some('\r') | Some('\n')) {
            return Err(refuse(&format!(
                "{what}= as a line terminator (CPython raises `bad {what} value` before it parses a byte)"
            )));
        }
    }
    // Two of the three being the same character is the case `_csv` names in
    // one message per pair. `escapechar == quotechar` needs the `is_some`:
    // both default to a `None` that is not a character and cannot collide.
    let same = |a: Option<char>, b: Option<char>| a.is_some() && a == b;
    for (a, b, msg) in [
        (Some(d.delimiter), d.quotechar, "delimiter or quotechar"),
        (Some(d.delimiter), d.escapechar, "delimiter or escapechar"),
        (d.escapechar, d.quotechar, "escapechar or quotechar"),
    ] {
        if same(a, b) {
            return Err(refuse(&format!(
                "a dialect whose {msg} are the same character (CPython raises `bad {msg} value` before it parses a byte)"
            )));
        }
    }
    Ok(())
}

// ---- the input -------------------------------------------------------------

/// The lines a reader will see, taken from whatever was handed to it.
///
/// The corpus mine (2026-09-06) says what this has to accept: 15 of the 17
/// corpus readers read a file object from `open(...)` and 2 read `sys.stdin`.
/// A list of strings is the other shape `csv.reader` accepts and it is NOT
/// served — it appears in the corpus only inside two capture-harness programs
/// that are unroutable for other reasons, and guessing at a shape the mine does
/// not show is how a capability grows surface nobody measured.
fn input_lines(v: &Value) -> R<(Vec<String>, Option<Rc<RefCell<mio::FileObj>>>)> {
    match v {
        Value::File(f) => {
            let mut fo = f.borrow_mut();
            if fo.closed {
                return Err(LypningError::exc(
                    "ValueError",
                    "I/O operation on closed file.",
                ));
            }
            if fo.mode != mio::Mode::Read {
                // `io.UnsupportedOperation: not readable`, whose type is an
                // `io` name this engine has no class object for.
                return Err(refuse("csv.reader() over a file not opened for reading"));
            }
            if fo.binary {
                // `_csv.Error: iterator should return strings, not bytes …`
                return Err(refuse("csv.reader() over a file opened in binary mode"));
            }
            // A SECOND reader over the same stream. CPython's first reader is
            // lazy, so an unconsumed one left the file at the start and the
            // second reader gets every row; this one already took them, and the
            // second would silently answer `[]` at exit 0. Same guard, and the
            // same reason, as the direct reads in `methods.rs`.
            mio::csv_read_guard(&fo)?;
            // The reader CONSUMES its input in CPython too: an `f.read()` after
            // one returns "".
            let start = fo.pos;
            fo.pos = fo.data.len();
            fo.csv_consumed = true;
            let text = crate::iter::decode_utf8(&fo.data[start..])?;
            let mode = fo.newline_mode;
            drop(fo);
            Ok((split_lines(&text, mode), Some(f.clone())))
        }
        // `sys.stdin` is a text stream opened with `newline="\n"`, NOT with
        // the `newline=None` a plain `open()` gets: CPython's `create_stdio`
        // names it, and this engine's own `sys.stdin.read()` already returns a
        // `\r` verbatim. The first cut of this file split it with
        // `NEWLINE_UNIVERSAL` on a comment that said the opposite, so a `\r`
        // inside a quoted field was rewritten to `\n` and a bare `\r` between
        // records became a record break where CPython raises. Checked by
        // experiment against this box's CPython, both directions, before the
        // constant was changed.
        //
        // `stdin_rest` is the same reader `sys.stdin.read()` uses and it
        // CONSUMES — which `stdin_csv_take` records, so a later read of the one
        // stream a program cannot reopen refuses instead of answering empty.
        Value::Module("sys.stdin") => {
            let text = crate::iter::decode_utf8(&mio::stdin_rest()?)?;
            mio::stdin_csv_take();
            Ok((split_lines(&text, mio::NEWLINE_KEEP_NL), None))
        }
        other => Err(refuse(&format!(
            "csv.reader() over a {} (only a file object and sys.stdin are served)",
            type_name(other)
        ))),
    }
}

/// Split text into the lines a Python TEXT STREAM would hand the reader, which
/// is the one place `open(newline=…)` is observable to `csv`.
///
/// `newline=None` translates `\r\n` and a lone `\r` to `\n` and splits there;
/// `newline=''` translates nothing and splits at all three; `newline='\n'`
/// translates nothing and splits only at `\n`. The three agree on every input
/// without a `\r`, which is why they can be — and were, by the first cut of
/// this file — confused: they diverge exactly when a `\r` falls inside a quoted
/// field, where the reader keeps it verbatim.
fn split_lines(text: &str, mode: u8) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut cur = String::new();
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '\n' => {
                cur.push('\n');
                out.push(std::mem::take(&mut cur));
            }
            '\r' if mode != mio::NEWLINE_KEEP_NL => {
                if mode == mio::NEWLINE_UNIVERSAL {
                    if chars.peek() == Some(&'\n') {
                        chars.next();
                    }
                    cur.push('\n');
                } else {
                    cur.push('\r');
                    if chars.peek() == Some(&'\n') {
                        chars.next();
                        cur.push('\n');
                    }
                }
                out.push(std::mem::take(&mut cur));
            }
            c => cur.push(c),
        }
    }
    // A file with no trailing newline ends in a partial line, and CPython's
    // stream yields it; a file that DOES end in one yields nothing after it,
    // which is why an empty `cur` is dropped rather than pushed. An empty file
    // yields no lines at all and the reader produces no rows.
    if !cur.is_empty() {
        out.push(cur);
    }
    out
}

// ---- the parser ------------------------------------------------------------

/// `_csv.c`'s `ParserState`, name for name. The reader is a character machine
/// and not a `split`, and the states are the whole reason: `"a,b"` is one field,
/// `"a""b"` is one field containing a quote, and `"a\nb"` is one field spanning
/// two lines — none of which a splitter can see.
#[derive(Clone, Copy, PartialEq)]
enum St {
    StartRecord,
    StartField,
    EscapedChar,
    InField,
    InQuoted,
    EscapeInQuoted,
    QuoteInQuoted,
    EatCrNl,
    AfterEscapedCrNl,
}

/// The end-of-line sentinel `_csv` passes after the last character of a line.
/// It is spelled `'\0'` there and a NUL in the DATA is rejected before it can be
/// confused with it; this uses `None` so the two cannot be confused at all.
type Ch = Option<char>;

struct Parser<'a> {
    d: &'a Dialect,
    st: St,
    field: String,
    /// Set when an UNQUOTED field begins under `QUOTE_NONNUMERIC`, and cleared
    /// by the save that converts it. A quoted field is never converted, which
    /// is why this is set in one branch and not by the quoting mode alone.
    numeric: bool,
    /// `field.chars().count()`, carried rather than recomputed. Asking the
    /// `String` per character made [`Parser::add`] quadratic in the field
    /// length — a 100 KB field is what the limit exists to catch, and walking
    /// it once per character to find out is the slowest possible way to say so.
    field_chars: usize,
    row: Vec<Value>,
}

impl<'a> Parser<'a> {
    fn new(d: &'a Dialect) -> Self {
        Parser {
            d,
            st: St::StartRecord,
            field: String::new(),
            numeric: false,
            field_chars: 0,
            row: Vec::new(),
        }
    }

    fn add(&mut self, c: char) -> R<()> {
        if self.field_chars >= FIELD_LIMIT {
            return Err(refuse(&format!(
                "field larger than field limit ({FIELD_LIMIT}) — csv.field_size_limit() is CPython's"
            )));
        }
        self.field.push(c);
        self.field_chars += 1;
        Ok(())
    }

    fn save(&mut self, it: &mut Interp) -> R<()> {
        let text: Rc<str> = Rc::from(self.field.as_str());
        self.field.clear();
        self.field_chars = 0;
        let v = if self.numeric {
            self.numeric = false;
            // `PyNumber_Float(field)`. The ValueError this raises for a
            // non-numeric field is `float()`'s own — and it is the program's
            // OWN exception, not a refusal, which is exactly why it may not be
            // raised from here: this reader is eager, so `float()` runs during
            // `csv.reader(…)` and CPython runs it during the ITERATION, after
            // the rows before it have already been yielded and printed. A
            // `try:` around the loop caught it there and does not catch it
            // here. So the failure becomes a refusal at construction — no
            // stdout has been written, the run falls onward, and CPython raises
            // its own ValueError in its own statement.
            let s = text.clone();
            crate::builtins::call_builtin(
                it,
                "float",
                &mut Args::one(Value::Str(text)),
                Vec::new(),
            )
            .map_err(|_| {
                refuse(&format!(
                    "a QUOTE_NONNUMERIC field float() cannot take ({:?}) — CPython raises that \
                     ValueError from the ITERATION, after the rows before it",
                    clip(&s)
                ))
            })?
        } else {
            Value::Str(text)
        };
        self.row.push(v);
        Ok(())
    }

    /// `parse_process_char`, transcribed. Every branch below is one of its
    /// branches; the one liberty taken is that `c == quotechar` is false when
    /// there is no quotechar, where `_csv` compares against a NUL it has already
    /// ruled out of the data.
    fn step(&mut self, it: &mut Interp, c: Ch) -> R<()> {
        let d = self.d;
        let is_quote = |c: Ch| c.is_some() && c == d.quotechar && d.quoting != QUOTE_NONE;
        let is_escape = |c: Ch| c.is_some() && c == d.escapechar;
        let is_delim = |c: Ch| c == Some(d.delimiter);
        let is_eol = |c: Ch| matches!(c, None | Some('\n') | Some('\r'));
        loop {
            match self.st {
                St::StartRecord => {
                    match c {
                        None => return Ok(()),
                        Some('\n') | Some('\r') => {
                            self.st = St::EatCrNl;
                            return Ok(());
                        }
                        _ => {}
                    }
                    self.st = St::StartField;
                    continue; // fallthrough to StartField
                }
                St::StartField => {
                    if is_eol(c) {
                        self.save(it)?;
                        self.st = if c.is_none() { St::StartRecord } else { St::EatCrNl };
                    } else if is_quote(c) {
                        self.st = St::InQuoted;
                    } else if is_escape(c) {
                        self.st = St::EscapedChar;
                    } else if c == Some(' ') && d.skipinitialspace {
                        // ignored
                    } else if is_delim(c) {
                        self.save(it)?;
                    } else {
                        if d.quoting == QUOTE_NONNUMERIC {
                            self.numeric = true;
                        }
                        self.add(c.unwrap())?;
                        self.st = St::InField;
                    }
                    return Ok(());
                }
                St::EscapedChar => {
                    match c {
                        Some(nl @ ('\n' | '\r')) => {
                            self.add(nl)?;
                            self.st = St::AfterEscapedCrNl;
                        }
                        other => {
                            self.add(other.unwrap_or('\n'))?;
                            self.st = St::InField;
                        }
                    }
                    return Ok(());
                }
                St::AfterEscapedCrNl => {
                    if c.is_none() {
                        return Ok(());
                    }
                    self.st = St::InField;
                    continue; // fallthrough to InField
                }
                St::InField => {
                    if is_eol(c) {
                        self.save(it)?;
                        self.st = if c.is_none() { St::StartRecord } else { St::EatCrNl };
                    } else if is_escape(c) {
                        self.st = St::EscapedChar;
                    } else if is_delim(c) {
                        self.save(it)?;
                        self.st = St::StartField;
                    } else {
                        self.add(c.unwrap())?;
                    }
                    return Ok(());
                }
                St::InQuoted => {
                    if c.is_none() {
                        // End of LINE inside quotes is not end of record: the
                        // newline is already in the field and the next line
                        // continues it.
                    } else if is_escape(c) {
                        self.st = St::EscapeInQuoted;
                    } else if is_quote(c) {
                        self.st = if d.doublequote { St::QuoteInQuoted } else { St::InField };
                    } else {
                        self.add(c.unwrap())?;
                    }
                    return Ok(());
                }
                St::EscapeInQuoted => {
                    self.add(c.unwrap_or('\n'))?;
                    self.st = St::InQuoted;
                    return Ok(());
                }
                St::QuoteInQuoted => {
                    if is_quote(c) {
                        self.add(c.unwrap())?;
                        self.st = St::InQuoted;
                    } else if is_delim(c) {
                        self.save(it)?;
                        self.st = St::StartField;
                    } else if is_eol(c) {
                        self.save(it)?;
                        self.st = if c.is_none() { St::StartRecord } else { St::EatCrNl };
                    } else if !d.strict {
                        self.add(c.unwrap())?;
                        self.st = St::InField;
                    } else {
                        return Err(refuse(&format!(
                            "'{}' expected after '{}' (a csv.Error CPython words)",
                            d.delimiter,
                            d.quotechar.unwrap_or('"')
                        )));
                    }
                    return Ok(());
                }
                St::EatCrNl => {
                    match c {
                        Some('\r') | Some('\n') => {}
                        None => self.st = St::StartRecord,
                        Some(_) => {
                            return Err(refuse(
                                "new-line character seen in unquoted field (a csv.Error CPython \
                                 words)",
                            ))
                        }
                    }
                    return Ok(());
                }
            }
        }
    }
}

/// Every row, eagerly. `Reader_iternext` yields a record each time the state
/// machine lands back on `StartRecord` after the end-of-line sentinel, and
/// finishes a trailing partial record at end of input; both are here.
fn parse_rows(it: &mut Interp, lines: &[String], d: &Dialect) -> R<Vec<Vec<Value>>> {
    let mut p = Parser::new(d);
    let mut out: Vec<Vec<Value>> = Vec::new();
    for line in lines {
        for c in line.chars() {
            if c == '\0' {
                return Err(refuse("line contains NUL (a csv.Error CPython words)"));
            }
            p.step(it, Some(c))?;
        }
        p.step(it, None)?;
        if p.st == St::StartRecord {
            out.push(std::mem::take(&mut p.row));
        }
    }
    // End of input with a field still open. `strict` makes it an error CPython
    // words; otherwise the partial field is saved and the record yielded.
    if !p.field.is_empty() || p.st == St::InQuoted {
        if d.strict {
            return Err(refuse("unexpected end of data (a csv.Error CPython words)"));
        }
        p.save(it)?;
        out.push(std::mem::take(&mut p.row));
    }
    Ok(out)
}

// ---- the two module functions ----------------------------------------------

/// `csv.reader(f, **dialect)` → an iterator over lists of strings.
///
/// `Value::IterObj` over an `Iter::Vec` is the whole return value: an existing
/// variant, on every arm the interpreter has, whose `type_name` is the `reader`
/// CPython prints and whose `repr` refuses because CPython's carries a heap
/// address.
fn reader(it: &mut Interp, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    let src = args
        .first()
        .cloned()
        .ok_or_else(|| refuse("csv.reader() with no argument"))?;
    let d = dialect_from(args, kw, 1)?;
    let (lines, file) = input_lines(&src)?;
    let rows = parse_rows(it, &lines, &d)?;
    Ok(rows_iter(rows.into_iter().map(list).collect(), "reader", file))
}

/// The reader value, and the one bookkeeping step an EAGER reader owes its
/// file.
///
/// The rows are right; the MOMENT they were read is not. CPython's reader holds
/// the file and reads it row by row, so a file closed under it raises and a file
/// written under it is seen — two divergences no amount of care inside this
/// module can notice, because neither is an event on the reader. `io` keeps the
/// pairing and the file tells the reader (`io::csv_on_close`,
/// `io::csv_on_write`).
fn rows_iter(rows: Vec<Value>, kind: &'static str, file: Option<Rc<RefCell<mio::FileObj>>>) -> Value {
    let it = Rc::new(RefCell::new(Iter::Vec(rows, 0)));
    if let Some(f) = file {
        mio::csv_register(&f, &it);
    }
    Value::IterObj(it, kind)
}

/// One line of a field, for a refusal that must stay one line on stderr.
fn clip(s: &str) -> String {
    let mut out: String = s.chars().take(40).collect();
    if s.chars().nth(40).is_some() {
        out.push('…');
    }
    out
}

/// `csv.DictReader(f, **dialect)` → an iterator over plain `dict`s.
///
/// `Lib/csv.py`'s class, transcribed: the first row is the header, a row that
/// parses to `[]` is SKIPPED rather than yielded, a short row is filled from
/// `restval` and a long one puts its tail under `restkey`. Since 3.8 it yields a
/// plain `dict`, which is why this needs no type of its own either.
///
/// `.fieldnames` and `.line_num` are attributes, not methods, and no dict has
/// them — so `route.rs`'s optimistic method union already stops a program that
/// reads one, statically, before it runs.
fn dict_reader(it: &mut Interp, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    let src = args
        .first()
        .cloned()
        .ok_or_else(|| refuse("csv.DictReader() with no argument"))?;
    let mut rest: Vec<(Rc<str>, Value)> = Vec::new();
    let mut fieldnames: Option<Vec<Value>> = None;
    let mut restkey = Value::None;
    let mut restval = Value::None;
    for (k, v) in kw {
        match k.as_ref() {
            "fieldnames" => match v {
                Value::None => {}
                Value::List(l) => fieldnames = Some(l.borrow().clone()),
                Value::Tuple(t) => fieldnames = Some((**t).clone()),
                other => {
                    return Err(refuse(&format!(
                        "csv.DictReader(fieldnames=) as a {}",
                        type_name(other)
                    )))
                }
            },
            "restkey" => restkey = v.clone(),
            "restval" => restval = v.clone(),
            _ => rest.push((k.clone(), v.clone())),
        }
    }
    let d = dialect_from(args, &rest, 1)?;
    let (lines, file) = input_lines(&src)?;
    let mut rows = parse_rows(it, &lines, &d)?.into_iter();
    let header: Vec<Value> = match fieldnames {
        Some(f) => f,
        // `next(self.reader)` on an empty input raises StopIteration inside the
        // property, which leaves `_fieldnames` None — and `__next__` then never
        // runs, because the same emptiness ends the iteration.
        None => match rows.next() {
            Some(h) => h,
            None => return Ok(rows_iter(Vec::new(), "DictReader", file)),
        },
    };
    let mut out: Vec<Value> = Vec::new();
    for row in rows {
        if row.is_empty() {
            continue; // `while row == []: row = next(self.reader)`
        }
        let mut dict = Dict::new();
        for (i, k) in header.iter().enumerate() {
            match row.get(i) {
                Some(v) => dict.insert(k.clone(), v.clone())?,
                None => dict.insert(k.clone(), restval.clone())?,
            }
        }
        if row.len() > header.len() {
            dict.insert(restkey.clone(), list(row[header.len()..].to_vec()))?;
        }
        out.push(Value::Dict(Rc::new(RefCell::new(dict))));
    }
    Ok(rows_iter(out, "DictReader", file))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rows(text: &str, d: &Dialect) -> Vec<Vec<String>> {
        let mut it = Interp::new();
        let lines = split_lines(text, mio::NEWLINE_RAW);
        parse_rows(&mut it, &lines, d)
            .unwrap()
            .into_iter()
            .map(|r| {
                r.into_iter()
                    .map(|v| match v {
                        Value::Str(s) => s.to_string(),
                        Value::Float(f) => format!("float:{f}"),
                        other => format!("?{}", type_name(&other)),
                    })
                    .collect()
            })
            .collect()
    }

    #[test]
    fn the_excel_dialect_is_the_default() {
        let d = Dialect::default();
        assert_eq!(rows("a,b\n1,2\n", &d), vec![vec!["a", "b"], vec!["1", "2"]]);
        // A doubled quote is one quote, and the field is one field.
        assert_eq!(rows("\"a\"\"b\",c\n", &d), vec![vec!["a\"b", "c"]]);
        // A newline inside quotes does not end the record.
        assert_eq!(rows("\"a\nb\",c\n", &d), vec![vec!["a\nb", "c"]]);
        // An empty line is an empty record, not a one-empty-field one.
        let empty: Vec<String> = Vec::new();
        assert_eq!(rows("a\n\nb\n", &d), vec![vec!["a".to_string()], empty, vec!["b".to_string()]]);
        // No trailing newline still yields the last record.
        assert_eq!(rows("a,b", &d), vec![vec!["a", "b"]]);
        // An empty file has no records at all.
        assert!(rows("", &d).is_empty());
        // Whitespace is a field.
        assert_eq!(rows(" , \n", &d), vec![vec![" ", " "]]);
        // CRLF ends the record and leaves nothing behind.
        assert_eq!(rows("a,b\r\nc,d\r\n", &d), vec![vec!["a", "b"], vec!["c", "d"]]);
    }

    #[test]
    fn split_lines_is_the_stream_and_not_the_parser() {
        assert_eq!(split_lines("a\r\nb", mio::NEWLINE_UNIVERSAL), vec!["a\n", "b"]);
        assert_eq!(split_lines("a\r\nb", mio::NEWLINE_RAW), vec!["a\r\n", "b"]);
        assert_eq!(split_lines("a\rb", mio::NEWLINE_UNIVERSAL), vec!["a\n", "b"]);
        assert_eq!(split_lines("a\rb", mio::NEWLINE_RAW), vec!["a\r", "b"]);
        assert_eq!(split_lines("a\rb", mio::NEWLINE_KEEP_NL), vec!["a\rb"]);
        let none: Vec<String> = Vec::new();
        assert_eq!(split_lines("", mio::NEWLINE_RAW), none);
    }

    /// `route::MODULE_ATTRS` is read by the CORE, which has none of this file
    /// compiled in — so the one thing that can keep it honest is this: the
    /// variant that DOES have `module_attr` holds the two lists to each other,
    /// in both directions. A name in the table this file does not serve routes
    /// a program into a refusal; a name this file serves and the table omits
    /// sends a program lypning-l would have run to CPython.
    #[test]
    fn the_route_table_names_exactly_what_is_served() {
        let table = crate::route::MODULE_ATTRS
            .iter()
            .find(|(m, _)| *m == "csv")
            .expect("route::MODULE_ATTRS has no csv row")
            .1;
        for name in table {
            assert!(module_attr(name).is_ok(), "route claims csv.{name} and csv.rs refuses it");
        }
        for name in MODULE_METHODS.iter().chain(CONSTANTS.iter().map(|(n, _)| n)) {
            assert!(
                table.contains(name),
                "csv.rs serves csv.{name} and route::MODULE_ATTRS omits it"
            );
        }
        // Sorted, so the table reads as one list and a duplicate is visible.
        assert!(table.windows(2).all(|w| w[0] < w[1]), "route::MODULE_ATTRS csv row is unsorted");
    }

    #[test]
    fn a_dialect_is_checked_against_itself_before_it_parses() {
        // `_csv` compares the three characters to EACH OTHER, and the first cut
        // of this file compared each only to itself.
        let bad = |kw: &[(&str, Value)]| {
            let kw: Vec<(Rc<str>, Value)> =
                kw.iter().map(|(k, v)| (Rc::from(*k), v.clone())).collect();
            dialect_from(&Args::new(), &kw, 1).is_err()
        };
        let c = |s: &str| Value::Str(Rc::from(s));
        assert!(bad(&[("delimiter", c(",")), ("quotechar", c(","))]));
        assert!(bad(&[("delimiter", c(",")), ("escapechar", c(","))]));
        assert!(bad(&[("quotechar", c("\"")), ("escapechar", c("\""))]));
        assert!(bad(&[("delimiter", c("\n"))]));
        assert!(bad(&[("quotechar", c("\r"))]));
        assert!(bad(&[("escapechar", c("\n"))]));
        // And the defaults, which collide with nothing, still pass.
        assert!(!bad(&[]));
        assert!(!bad(&[("delimiter", c(";")), ("quotechar", c("'")), ("escapechar", c("\\"))]));
        // `quotechar=None` and `escapechar=None` are both absent, not equal.
        assert!(!bad(&[("quotechar", Value::None), ("quoting", Value::Int(QUOTE_NONE))]));
    }

    #[test]
    fn the_writers_are_absent_rather_than_refusing_late() {
        for name in ["writer", "DictWriter", "Sniffer", "field_size_limit", "excel", "Error"] {
            match module_attr(name) {
                Err(e) => match e.kind() {
                    crate::err::ErrKind::Unsupported { kind, .. } => {
                        assert_eq!(kind, "module-attr")
                    }
                    _ => panic!("{name} must refuse as module-attr, which the WALK can see"),
                },
                Ok(_) => panic!("{name} must not be served"),
            }
        }
        assert!(module_attr("reader").is_ok());
        assert!(module_attr("DictReader").is_ok());
        assert!(matches!(module_attr("QUOTE_NONNUMERIC"), Ok(Value::Int(2))));
    }
}
