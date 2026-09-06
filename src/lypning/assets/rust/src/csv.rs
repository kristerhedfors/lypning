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
//! So a reader here is an **`Iter` behind the existing [`Value::IterObj`]** —
//! the shape `re.finditer` already returns, and `Iter` is where the lazy
//! shapes already live (`Iter::Lines`, `Iter::Range`, `Iter::Gen`). Every one
//! of those arms is already wired for it: `next(r)` takes it as an iterator,
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
//! **The reader is LAZY, which is the design.** CPython's pulls one line from
//! its input iterator per record, so a program can look at the stream BETWEEN
//! two rows — and an adversarial grid found five directions in which it does.
//! The first cut of this file drained the stream at construction and answered
//! all five with guards: a flag on the file, a second flag for `sys.stdin`, a
//! registry pairing every live reader with its file, and two events (`close`,
//! `write`) the file had to push back at a reader that could not see them.
//! Each closed one row and left the next; `input()` reached the one stream
//! without a guard, and a stream iterator already in flight walked past the one
//! that was installed per loop.
//!
//! So the reader pulls lines from the FILE OBJECT, through the same
//! [`Iter::Lines`] a `for line in f` uses and the same `Iter::Stdin` `input()`
//! shares a cursor with. It has no stream of its own to leave in the wrong
//! place. `f.read()` after `next(r)` returns what is left because the reader
//! only took what it yielded; a file closed under it raises CPython's own
//! `ValueError: I/O operation on closed file.` from the line read; a
//! `QUOTE_NONNUMERIC` field `float()` cannot take raises the program's own
//! `ValueError` from the ITERATION that reaches it, which is the statement a
//! `try:` around the loop is written to catch. None of that is code here. It
//! is what is left when the guards go.
//!
//! One thing survives them, and it is not a csv divergence: this engine's
//! `FileObj` holds the bytes `open()` read, where CPython's holds a
//! descriptor. So a write to a path under an open read handle is invisible
//! here and visible there — for `f.read()` as much as for a reader — and
//! [`next_line`] refuses a row rather than adding to it (`io::write_gen`).
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
#[derive(Clone, Copy)]
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

/// The LINES a reader will see, as the stream's own iterator.
///
/// This is the whole of the laziness. `csv.reader(f)` in CPython is
/// `PyObject_GetIter(f)` and one `PyIter_Next` per record; here it is
/// `Iter::Lines` — the same iterator `for line in f` and `f.readline()` drive —
/// and one `iter_next` per record. So the stream ends up exactly where CPython
/// leaves it after every row, and none of the five guards the eager version
/// needed has anything left to guard.
///
/// The corpus mine (2026-09-06) says what this has to accept: 15 of the 17
/// corpus readers read a file object from `open(...)` and 2 read `sys.stdin`.
/// A list of strings is the other shape `csv.reader` accepts and it is NOT
/// served — it appears in the corpus only inside two capture-harness programs
/// that are unroutable for other reasons, and guessing at a shape the mine does
/// not show is how a capability grows surface nobody measured.
///
/// The three checks below are CPython's own, at CPython's own moment: `iter()`
/// of a closed file IS the `ValueError`, raised from the `csv.reader(...)` call
/// and not from the first row. The other two are refusals, and a refusal may
/// always be earlier than the error it stands for.
fn input_lines(v: &Value) -> R<Iter> {
    match v {
        Value::File(f) => {
            let fo = f.borrow();
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
            drop(fo);
            Ok(Iter::Lines(f.clone()))
        }
        // `sys.stdin` is a text stream opened with `newline="\n"`, NOT with
        // the `newline=None` a plain `open()` gets: CPython's `create_stdio`
        // names it, and this engine's own `sys.stdin.read()` already returns a
        // `\r` verbatim. Checked by experiment against this box's CPython, both
        // directions, before the constant was chosen. `Iter::Stdin` splits at
        // `\n` alone, which is that mode — and it is the SAME cursor `input()`,
        // `sys.stdin.readline()` and `for line in sys.stdin` advance, so an
        // interleaving of them comes out in CPython's order rather than in one
        // this engine invented.
        Value::Module("sys.stdin") => Ok(Iter::Stdin),
        other => Err(refuse(&format!(
            "csv.reader() over a {} (only a file object and sys.stdin are served)",
            type_name(other)
        ))),
    }
}

/// One line, or `None` at end of input.
///
/// The one check that is left, and it is the FILE object's divergence rather
/// than this reader's: `FileObj::data` is the bytes `open()` read, so a write
/// to the path under the handle is invisible here and visible to CPython, whose
/// reader would go on to yield it. `f.read()` has the same hole and does not
/// refuse; this declines to widen it rather than to close it.
fn next_line(it: &mut Interp, lines: &mut Iter) -> R<Option<Rc<str>>> {
    if let Iter::Lines(f) = lines {
        let fo = f.borrow();
        if mio::write_gen(&fo.path) != fo.write_gen {
            return Err(refuse(
                "a row from a file that has been written since it was opened (this engine's file \
                 object is the bytes open() read; CPython's reader would yield what was written)",
            ));
        }
    }
    match it.iter_next(lines)? {
        // `Iter::Lines` yields `Bytes` for a binary stream, which `input_lines`
        // has already refused, and `Iter::Stdin` yields nothing else.
        Some(Value::Str(s)) => Ok(Some(s)),
        Some(other) => Err(refuse(&format!(
            "an input line that is a {} (CPython's csv.Error names a type this engine does not)",
            type_name(&other)
        ))),
        None => Ok(None),
    }
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

struct Parser {
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

impl Parser {
    fn new() -> Self {
        Parser {
            st: St::StartRecord,
            field: String::new(),
            numeric: false,
            field_chars: 0,
            row: Vec::new(),
        }
    }

    /// `parse_reset`, which `_csv` runs at the top of every `Reader_iternext`
    /// — so a record that ended mid-field at end of input cannot be yielded
    /// twice, and the state a row starts from is always the same one.
    fn reset(&mut self) {
        self.st = St::StartRecord;
        self.field.clear();
        self.field_chars = 0;
        self.numeric = false;
        self.row.clear();
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
            // `PyNumber_Float(field)`, and the ValueError it raises for a
            // non-numeric field is the PROGRAM's own exception, not a refusal —
            // so the only thing that matters is the statement it comes from.
            // The eager reader ran it during `csv.reader(…)`, where a `try:`
            // around the loop could not catch it and the rows before it had not
            // printed, and turned it into a refusal for exactly that reason.
            // A lazy reader runs it here: at the row that reaches the field,
            // after the rows before it have been yielded, which is CPython's
            // own place for it. Nothing to convert, and nothing to refuse.
            crate::builtins::call_builtin(
                it,
                "float",
                &mut Args::one(Value::Str(text)),
                Vec::new(),
            )?
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
    fn step(&mut self, it: &mut Interp, d: &Dialect, c: Ch) -> R<()> {
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

// ---- the reader ------------------------------------------------------------

/// A live `csv.reader` or `csv.DictReader`: the parser's state between rows,
/// the dialect it parses with, and the stream it pulls lines from.
///
/// It is `_csv`'s `ReaderObj` with `Lib/csv.py`'s `DictReader` folded into it
/// as an option, because the two differ only in what they do with a row that
/// has already been parsed — and a `DictReader` in CPython holds a `reader` and
/// forwards to it, which is a second object and a second set of arms for
/// nothing.
pub struct CsvIter {
    lines: Iter,
    d: Dialect,
    p: Parser,
    /// `None` for `csv.reader`. The header is `Option` inside it because
    /// `DictReader.fieldnames` is a PROPERTY: the first row is read at the
    /// first `next()`, not at construction, and not at all when `fieldnames=`
    /// was given.
    dict: Option<Box<DictState>>,
}

struct DictState {
    header: Option<Vec<Value>>,
    restkey: Value,
    restval: Value,
}

/// One row, pulled now. `iter::Iter::Csv` is the only caller.
pub fn next_row(it: &mut Interp, c: &mut CsvIter) -> R<Option<Value>> {
    if c.dict.is_some() {
        return dict_row(it, c);
    }
    Ok(row(it, c)?.map(list))
}

/// `Reader_iternext`, transcribed: reset, then pull a line and feed it until
/// the state machine lands back on `StartRecord` after the end-of-line
/// sentinel. A record spanning three lines is three pulls inside ONE call,
/// which is why the loop is here and not in the caller.
fn row(it: &mut Interp, c: &mut CsvIter) -> R<Option<Vec<Value>>> {
    c.p.reset();
    loop {
        let Some(line) = next_line(it, &mut c.lines)? else {
            // End of input with a field still open. `strict` makes it an error
            // CPython words; otherwise the partial field is saved and the
            // record yielded — once, because `reset` above runs before the next
            // pull finds the same end of input.
            if !c.p.field.is_empty() || c.p.st == St::InQuoted {
                if c.d.strict {
                    return Err(refuse("unexpected end of data (a csv.Error CPython words)"));
                }
                c.p.save(it)?;
                return Ok(Some(std::mem::take(&mut c.p.row)));
            }
            return Ok(None);
        };
        for ch in line.chars() {
            if ch == '\0' {
                return Err(refuse("line contains NUL (a csv.Error CPython words)"));
            }
            c.p.step(it, &c.d, Some(ch))?;
        }
        c.p.step(it, &c.d, None)?;
        if c.p.st == St::StartRecord {
            return Ok(Some(std::mem::take(&mut c.p.row)));
        }
    }
}

/// `DictReader.__next__`, transcribed, including the two places it reads a row
/// that never becomes one: the header, and every `[]` an empty line parses to.
fn dict_row(it: &mut Interp, c: &mut CsvIter) -> R<Option<Value>> {
    // `self.fieldnames`, the property — and its `except StopIteration: pass`,
    // which leaves `_fieldnames` None and lets the `next(self.reader)` below
    // end the iteration instead.
    if matches!(&c.dict, Some(d) if d.header.is_none()) {
        if let Some(h) = row(it, c)? {
            if let Some(d) = c.dict.as_mut() {
                d.header = Some(h);
            }
        }
    }
    let values = loop {
        match row(it, c)? {
            None => return Ok(None),
            // `while row == []: row = next(self.reader)`
            Some(r) if r.is_empty() => continue,
            Some(r) => break r,
        }
    };
    let ds = match c.dict.as_ref() {
        Some(d) => d,
        None => return Ok(None),
    };
    let header = ds.header.as_deref().unwrap_or(&[]);
    let mut dict = Dict::new();
    for (i, k) in header.iter().enumerate() {
        match values.get(i) {
            Some(v) => dict.insert(k.clone(), v.clone())?,
            None => dict.insert(k.clone(), ds.restval.clone())?,
        }
    }
    if values.len() > header.len() {
        dict.insert(ds.restkey.clone(), list(values[header.len()..].to_vec()))?;
    }
    Ok(Some(Value::Dict(Rc::new(RefCell::new(dict)))))
}

/// The reader VALUE. `Value::IterObj` over the `Iter` above is the whole of it:
/// an existing variant, on every arm the interpreter has, whose `type_name` is
/// the `reader` / `DictReader` CPython prints and whose `repr` refuses because
/// CPython's carries a heap address. There is no registration step and no
/// pairing to keep — the reader holds its stream, the way CPython's does.
fn reader_value(c: CsvIter, kind: &'static str) -> Value {
    Value::IterObj(Rc::new(RefCell::new(Iter::Csv(Box::new(c)))), kind)
}

// ---- the two module functions ----------------------------------------------

/// `csv.reader(f, **dialect)` → an iterator over lists of strings.
///
/// Nothing is read here. CPython's `csv.reader(...)` takes an iterator over the
/// input and the first `next()` is the first line off the stream, so the only
/// thing that may fail at this statement is the dialect and the input's type —
/// and both are refusals at the one point in a csv program that is furthest
/// from a committed side effect.
fn reader(_it: &mut Interp, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    let src = args
        .first()
        .cloned()
        .ok_or_else(|| refuse("csv.reader() with no argument"))?;
    let d = dialect_from(args, kw, 1)?;
    let lines = input_lines(&src)?;
    Ok(reader_value(CsvIter { lines, d, p: Parser::new(), dict: None }, "reader"))
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
fn dict_reader(_it: &mut Interp, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    let src = args
        .first()
        .cloned()
        .ok_or_else(|| refuse("csv.DictReader() with no argument"))?;
    let mut rest: Vec<(Rc<str>, Value)> = Vec::new();
    let mut header: Option<Vec<Value>> = None;
    let mut restkey = Value::None;
    let mut restval = Value::None;
    for (k, v) in kw {
        match k.as_ref() {
            "fieldnames" => match v {
                Value::None => {}
                Value::List(l) => header = Some(l.borrow().clone()),
                Value::Tuple(t) => header = Some((**t).clone()),
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
    let lines = input_lines(&src)?;
    Ok(reader_value(
        CsvIter {
            lines,
            d,
            p: Parser::new(),
            dict: Some(Box::new(DictState { header, restkey, restval })),
        },
        "DictReader",
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A reader over an in-memory `FileObj`, drained the way a program drains
    /// one. It goes through the REAL line reader (`iter::Iter::Lines`) and the
    /// real `next_row`, so what these assert about a `\r` is what a file opened
    /// under that `newline=` mode actually yields — the split used to live in
    /// this module, where it could and did disagree with the stream it shared
    /// the flag with.
    fn try_rows(text: &str, d: &Dialect, mode: u8) -> R<Vec<Vec<String>>> {
        let mut it = Interp::new();
        let f = Rc::new(RefCell::new(mio::FileObj {
            path: String::new(),
            mode: mio::Mode::Read,
            binary: false,
            closed: false,
            data: text.as_bytes().to_vec(),
            pos: 0,
            newline_mode: mode,
            write_gen: 0,
            telling: true,
        }));
        let mut c = CsvIter {
            lines: Iter::Lines(f),
            d: *d,
            p: Parser::new(),
            dict: None,
        };
        let mut out = Vec::new();
        while let Some(r) = row(&mut it, &mut c)? {
            out.push(
                r.into_iter()
                    .map(|v| match v {
                        Value::Str(s) => s.to_string(),
                        Value::Float(f) => format!("float:{f}"),
                        other => format!("?{}", type_name(&other)),
                    })
                    .collect(),
            );
        }
        Ok(out)
    }

    fn rows_mode(text: &str, d: &Dialect, mode: u8) -> Vec<Vec<String>> {
        try_rows(text, d, mode).unwrap()
    }

    fn rows(text: &str, d: &Dialect) -> Vec<Vec<String>> {
        rows_mode(text, d, mio::NEWLINE_RAW)
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
    fn the_stream_decides_where_a_record_ends_and_the_parser_does_not() {
        let d = Dialect::default();
        // The three `newline=` modes differ exactly where a `\r` falls, and a
        // reader is the thing that notices. `newline=None` TRANSLATES, so the
        // `\r` inside the quoted field arrives as a `\n`.
        assert_eq!(rows_mode("a\rb\n", &d, mio::NEWLINE_UNIVERSAL),
                   vec![vec!["a".to_string()], vec!["b".to_string()]]);
        assert_eq!(rows_mode("a\rb\n", &d, mio::NEWLINE_RAW),
                   vec![vec!["a".to_string()], vec!["b".to_string()]]);
        assert_eq!(rows_mode("\"a\rb\",c\n", &d, mio::NEWLINE_UNIVERSAL),
                   vec![vec!["a\nb".to_string(), "c".to_string()]]);
        assert_eq!(rows_mode("\"a\rb\",c\n", &d, mio::NEWLINE_RAW),
                   vec![vec!["a\rb".to_string(), "c".to_string()]]);
        // `newline='\n'` does not end a line at a `\r` at all, so the parser
        // meets it inside an unquoted field, where CPython's own reader raises
        // `new-line character seen in unquoted field` — a csv.Error, refused.
        assert!(try_rows("a\rb\n", &d, mio::NEWLINE_KEEP_NL).is_err());
        // …and it is the STREAM that decides that, not the dialect: the same
        // bytes under the other two modes are two ordinary records.
        assert_eq!(rows_mode("a\rb\n", &d, mio::NEWLINE_RAW).len(), 2);
    }

    /// The whole point of laziness: the stream is where CPython leaves it after
    /// every row, so what is left of the file is still there to be read.
    #[test]
    fn a_row_takes_only_the_lines_it_needed() {
        let mut it = Interp::new();
        let f = Rc::new(RefCell::new(mio::FileObj {
            path: String::new(),
            mode: mio::Mode::Read,
            binary: false,
            closed: false,
            data: b"a,b\nc,d\ne,f\n".to_vec(),
            pos: 0,
            newline_mode: mio::NEWLINE_RAW,
            write_gen: 0,
            telling: true,
        }));
        let d = Dialect::default();
        let mut c = CsvIter { lines: Iter::Lines(f.clone()), d, p: Parser::new(), dict: None };
        assert!(row(&mut it, &mut c).unwrap().is_some());
        // One row taken, one line consumed — `f.read()` would return the rest.
        assert_eq!(f.borrow().pos, 4);
        assert!(row(&mut it, &mut c).unwrap().is_some());
        assert_eq!(f.borrow().pos, 8);
        // A file closed under the reader is CPython's own ValueError, and it
        // comes from the line read rather than from anything this module does.
        f.borrow_mut().closed = true;
        assert!(row(&mut it, &mut c).is_err());
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
