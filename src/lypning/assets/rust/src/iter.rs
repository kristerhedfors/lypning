//! Iteration.
//!
//! Iterators hold data only; the interpreter drives them, so an iterator can
//! call back into `eval` without a borrow cycle. Four shapes are lazy on
//! purpose — `range`, file lines, generator expressions and a `csv.reader` —
//! because each has a case where materialising changes the ANSWER rather than
//! just the memory use. For the last two the case is the same one: a program
//! can look at the stream between two rows, and what it sees there is the
//! difference between reproducing CPython and inventing a position.

use crate::err::*;
use crate::eval::{Interp, Scope};
use crate::io as mio;
use crate::value::*;
use std::cell::RefCell;
use std::rc::Rc;

use crate::ast::{CompClause, Expr};

pub enum Iter {
    /// Live view of a list: Python sees appends made during the loop, so this
    /// indexes the original rather than a snapshot.
    List(Rc<RefCell<Vec<Value>>>, usize),
    Tuple(Rc<Vec<Value>>, usize),
    Chars(Vec<char>, usize),
    Bytes(Rc<Vec<u8>>, usize),
    /// A dict traversal: the elements snapshotted, plus the dict itself and the
    /// size it had, so a mutation during iteration raises the same RuntimeError
    /// CPython raises.
    ///
    /// Used for a bare dict AND for its three VIEWS. The views used to snapshot
    /// into a plain `Iter::Vec`, which threw the dict away — so
    /// `for k in d.keys(): del d[k]` walked a frozen copy, emptied the dict and
    /// answered normally where CPython raises. The guard was there; the views
    /// simply did not reach it.
    DictIter {
        d: Rc<RefCell<Dict>>,
        items: Vec<Value>,
        i: usize,
        n0: usize,
    },
    Range {
        cur: i64,
        stop: i64,
        step: i64,
    },
    Lines(Rc<RefCell<mio::FileObj>>),
    Stdin,
    Gen(Rc<RefCell<GenState>>),
    Vec(Vec<Value>, usize),
    /// `map(f, *iters)` — stops at the shortest, like Python.
    Map(Value, Vec<Iter>),
    /// `filter(pred, it)`; `None` means "keep truthy".
    Filter(Option<Value>, Box<Iter>),
    Zip(Vec<Iter>),
    Enumerate(Box<Iter>, i64),
    /// A live iterator handed out by `iter()` and consumed by `next()`.
    Shared(Rc<RefCell<Iter>>),
    /// A `csv.reader` / `csv.DictReader`, pulling its lines from the stream
    /// underneath it one row at a time — which is what CPython's does, and the
    /// whole reason this is an `Iter` variant rather than a `Vec` of rows.
    ///
    /// The eager version of this capability had the right ROWS at the wrong
    /// MOMENT: it drained the stream at construction, so a file closed under it
    /// still answered, `f.read()` after `next(r)` came back empty, `input()`
    /// and a reader over `sys.stdin` fought over one cursor, and five guards
    /// stood on the paths back to the stream to say so. The state below is the
    /// same state `_csv`'s `ReaderObj` carries between rows, and none of those
    /// paths needs telling any more.
    ///
    /// An `Iter` variant and NOT a `Value` one: `Value` reaches `eq`, `hash`,
    /// `repr`, `bool`, `len` and a dozen more arms nothing forces you to
    /// remember (`docs/HILLCLIMB.md` iteration 74); `Iter` reaches `iter_next`,
    /// and `Value::IterObj` — the shape `re.finditer` already returns — carries
    /// it through every one of the others.
    #[cfg(feature = "cap-csv")]
    Csv(Box<crate::csv::CsvIter>),
}

/// Where a text stream's next line ends, given what `open(newline=…)` asked for.
///
/// `newline='\n'` — and every BINARY stream, which takes no `newline=` at all —
/// ends a line at `\n` and nowhere else. The other two recognise all three
/// endings: `\r\n`, `\n` and a bare `\r`. They differ only in what the line
/// then LOOKS like, which is [`line_text`]'s half.
#[cfg(feature = "cap-csv")]
fn line_end(fo: &mio::FileObj, start: usize) -> usize {
    if fo.binary || fo.newline_mode == mio::NEWLINE_KEEP_NL {
        return match fo.data[start..].iter().position(|c| *c == b'\n') {
            Some(i) => start + i + 1,
            None => fo.data.len(),
        };
    }
    match fo.data[start..].iter().position(|c| *c == b'\n' || *c == b'\r') {
        Some(i) => {
            let e = start + i;
            if fo.data[e] == b'\r' && fo.data.get(e + 1) == Some(&b'\n') {
                e + 2
            } else {
                e + 1
            }
        }
        None => fo.data.len(),
    }
}

/// The line itself. `newline=None` — the default, and the only mode that
/// TRANSLATES — hands `\r\n` and a bare `\r` to the program as `\n`.
///
/// The translation is the TERMINATOR and nothing else, which is why this is a
/// suffix test and not a scan: [`line_end`] stopped at the first `\r` or `\n`,
/// so the only carriage return a line can contain is the one it ends with. The
/// lines without one — every line of every file that has no `\r` in it — take
/// the same single allocation they always did.
#[cfg(feature = "cap-csv")]
fn line_text(fo: &mio::FileObj, start: usize, end: usize) -> R<Rc<str>> {
    let text = decode_text(&fo.data[start..end], LINE_UTF8)?;
    if fo.newline_mode == mio::NEWLINE_UNIVERSAL {
        let cut = if text.ends_with("\r\n") {
            2
        } else if text.ends_with('\r') {
            1
        } else {
            return Ok(text);
        };
        let head = &text[..text.len() - cut];
        let mut out = String::with_capacity(head.len() + 1);
        out.push_str(head);
        out.push('\n');
        return Ok(Rc::from(out.as_str()));
    }
    Ok(text)
}

/// The core's half: this engine has never translated a line ending and splits
/// at `\n`, which is exactly `newline='\n'` and — on input without a `\r` —
/// `newline=None`. `newline=''` refuses here (`builtins::newline_mode_of`), so
/// the other two modes are unreachable and cost no bytes.
#[cfg(not(feature = "cap-csv"))]
fn line_end(fo: &mio::FileObj, start: usize) -> usize {
    match fo.data[start..].iter().position(|c| *c == b'\n') {
        Some(i) => start + i + 1,
        None => fo.data.len(),
    }
}

#[cfg(not(feature = "cap-csv"))]
fn line_text(fo: &mio::FileObj, start: usize, end: usize) -> R<Rc<str>> {
    decode_text(&fo.data[start..end], LINE_UTF8)
}

const LINE_UTF8: &str = "non-UTF-8 bytes in a text-mode line read";

/// `TextIOWrapper.__next__`'s one side effect on the stream, which is not
/// `pos`: it disables `tell()` while its read-ahead is in play. A BINARY stream
/// has no decoder to lose track of and keeps telling throughout.
#[cfg(feature = "cap-csv")]
fn set_telling(fo: &mut mio::FileObj, on: bool) {
    if !fo.binary {
        fo.telling = on;
    }
}

#[cfg(not(feature = "cap-csv"))]
fn set_telling(_fo: &mut mio::FileObj, _on: bool) {}

/// A generator expression, suspended between elements.
///
/// The AST is behind `Rc` because `gen_step` has to detach it from the borrow
/// on `st` before calling back into `eval` — and it used to do that by
/// **deep-cloning the clause and the element expression on every element
/// yielded**, one allocation per AST node, per element. A refcount bump does
/// the same job.
pub struct GenState {
    pub clauses: Rc<Vec<CompClause>>,
    pub elt: Rc<Expr>,
    pub env: Vec<Scope>,
    /// `None` only in a [`GenState::placeholder_like`], which exists to fill the
    /// `RefCell` while the real state is out and whose scope is never read —
    /// it is marked `running` and `done`, and both are checked first. An
    /// `Option` rather than a shared empty scope because a `thread_local` for
    /// it measured 8% slower on recursion: adding one shifts the TLS block, and
    /// the recursion guard already lives there.
    pub scope: Option<Scope>,
    pub stack: Vec<Iter>,
    pub started: bool,
    pub done: bool,
    /// Set while the generator is being advanced, so a self-referential
    /// generator is reported rather than panicking on the RefCell.
    pub running: bool,
}

impl GenState {
    pub fn new(clauses: Rc<Vec<CompClause>>, elt: Rc<Expr>, env: Vec<Scope>) -> Self {
        GenState {
            clauses,
            elt,
            env,
            scope: Some(crate::eval::new_scope()),
            stack: Vec::new(),
            started: false,
            done: false,
            running: false,
        }
    }
    /// The stand-in that fills the `RefCell` while the real state is out.
    ///
    /// It borrows the real state's two `Rc`s rather than making its own, and
    /// that is the whole function: `Rc::new(Vec::new())` and
    /// `Rc::new(Expr::None)` were **two heap allocations and two frees per
    /// element yielded** — an `RcBox` around a 24-byte `Vec` header, and one
    /// sized to the largest `Expr` variant, both freed a few lines later when
    /// the real state goes back. Two refcount increments do the same job.
    ///
    /// Neither field is ever read through the placeholder: it is marked
    /// `running` and `done`, and both are checked before anything else.
    fn placeholder_like(real: &GenState) -> Self {
        GenState {
            clauses: real.clauses.clone(),
            elt: real.elt.clone(),
            env: Vec::new(),
            scope: None,
            stack: Vec::new(),
            started: true,
            done: true,
            running: true,
        }
    }
}

impl Interp {
    pub fn make_iter(&mut self, v: Value) -> R<Iter> {
        Ok(match v {
            Value::List(l) => Iter::List(l, 0),
            Value::Tuple(t) => Iter::Tuple(t, 0),
            Value::Str(s) => Iter::Chars(s.chars().collect(), 0),
            Value::Bytes(b) => Iter::Bytes(b, 0),
            Value::Range(a, b, st) => Iter::Range {
                cur: a,
                stop: b,
                step: st,
            },
            Value::Dict(d) => {
                let (keys, n0) = {
                    let b = d.borrow();
                    (b.keys(), b.len())
                };
                Iter::DictIter { d, items: keys, i: 0, n0 }
            }
            // See value.rs: reproducing CPython's set order is impossible, so
            // iterating one is refused instead of silently reordered.
            Value::Set(_) => return Err(set_order_refused("iterating a set")),
            // `for q in p.parents` — the parents are a short, already-computed
            // list, so the existing tuple iterator carries them and there is no
            // new iterator state in the binary for this.
            #[cfg(feature = "cap-pathlib")]
            Value::Path(s, true) => Iter::Tuple(Rc::new(crate::pathlib::view_items(&s)), 0),
            // `list(re.I | re.M)` and `for f in re.I` are `Flag.__iter__` —
            // the members present — which CPython answers. This one arm covers
            // `iter()`, `list()`, `tuple()`, `set()`, `sorted()`, `for`,
            // comprehensions, `map`/`filter`/`zip`, `any`/`all` and `sum`.
            #[cfg(feature = "cap-re")]
            Value::ReFlag(_) => {
                return Err(crate::re::refuse("iterating a RegexFlag (Flag.__iter__)"))
            }
            // `list(m)` and `for x in p` are TypeErrors in CPython; the
            // message names a type this engine would have to spell.
            #[cfg(feature = "cap-re")]
            v @ (Value::Pattern(_) | Value::Match(_)) => {
                return Err(crate::re::guard_one(&v, "iterating").unwrap_err())
            }
            Value::File(f) => Iter::Lines(f),
            Value::Gen(g) => Iter::Gen(g),
            Value::IterObj(it, _) => Iter::Shared(it),
            // `for line in sys.stdin` — the largest single cluster in the
            // corpus is `stdin -> transform -> stdout`. One cursor serves this,
            // `input()`, `sys.stdin.read()` and a `csv.reader` over the stream,
            // which is what makes an interleaving of them come out in CPython's
            // order; a guard here used to stand in for that and was installed
            // once per LOOP, so an iterator already in flight walked straight
            // past it.
            Value::Module("sys.stdin") => Iter::Stdin,
            Value::DictView(d, kind) => {
                let (items, n0) = {
                    let b = d.borrow();
                    let items = match kind {
                        "keys" => b.keys(),
                        "values" => b.values(),
                        _ => b.items(),
                    };
                    (items, b.len())
                };
                Iter::DictIter { d, items, i: 0, n0 }
            }
            other => {
                return Err(type_err(format!(
                    "'{}' object is not iterable",
                    type_name(&other)
                )))
            }
        })
    }

    pub fn iter_next(&mut self, it: &mut Iter) -> R<Option<Value>> {
        self.tick()?;
        Ok(match it {
            Iter::List(l, i) => {
                let b = l.borrow();
                if *i < b.len() {
                    let v = b[*i].clone();
                    *i += 1;
                    Some(v)
                } else {
                    None
                }
            }
            Iter::Tuple(t, i) => {
                if *i < t.len() {
                    let v = t[*i].clone();
                    *i += 1;
                    Some(v)
                } else {
                    None
                }
            }
            Iter::Vec(v, i) => {
                if *i < v.len() {
                    let x = v[*i].clone();
                    *i += 1;
                    Some(x)
                } else {
                    None
                }
            }
            Iter::Chars(c, i) => {
                if *i < c.len() {
                    let v = Value::Str(crate::value::char_str(c[*i]));
                    *i += 1;
                    Some(v)
                } else {
                    None
                }
            }
            Iter::Bytes(b, i) => {
                if *i < b.len() {
                    let v = ival(b[*i] as i64);
                    *i += 1;
                    Some(v)
                } else {
                    None
                }
            }
            Iter::DictIter { d, items, i, n0 } => {
                if d.borrow().len() != *n0 {
                    return Err(LypningError::exc(
                        "RuntimeError",
                        "dictionary changed size during iteration",
                    ));
                }
                if *i < items.len() {
                    let v = items[*i].clone();
                    *i += 1;
                    Some(v)
                } else {
                    None
                }
            }
            Iter::Range { cur, stop, step } => {
                let go = if *step > 0 { *cur < *stop } else { *cur > *stop };
                if go {
                    let v = *cur;
                    *cur = cur.checked_add(*step).ok_or_else(|| {
                        unsupported("bigint", "range counter beyond 64-bit range")
                    })?;
                    Some(ival(v))
                } else {
                    None
                }
            }
            Iter::Lines(f) => {
                let mut fo = f.borrow_mut();
                if fo.closed {
                    return Err(LypningError::exc(
                        "ValueError",
                        "I/O operation on closed file.",
                    ));
                }
                if fo.pos >= fo.data.len() {
                    // The EOF that ends a `for` loop is where CPython puts its
                    // read-ahead down and `tell()` starts working again.
                    set_telling(&mut fo, true);
                    None
                } else {
                    let start = fo.pos;
                    let end = line_end(&fo, start);
                    fo.pos = end;
                    set_telling(&mut fo, false);
                    Some(if fo.binary {
                        Value::Bytes(Rc::new(fo.data[start..end].to_vec()))
                    } else {
                        // Straight from the file buffer's slice into the
                        // `Rc<str>`. This used to build a `Vec<u8>` and then a
                        // `String` on the way, so every line of every file read
                        // in a `for` loop was copied three times.
                        Value::Str(line_text(&fo, start, end)?)
                    })
                }
            }
            // One row, pulled now. The lines come from the arm above this one
            // and from `Iter::Stdin` below it — the reader has no stream of its
            // own, which is exactly why the stream is where CPython leaves it
            // after every row.
            #[cfg(feature = "cap-csv")]
            Iter::Csv(c) => crate::csv::next_row(self, c)?,
            Iter::Stdin => match mio::stdin_line()? {
                Some(b) => Some(Value::Str(decode_text(
                    &b,
                    "non-UTF-8 bytes on stdin (CPython decodes it with surrogateescape)",
                )?)),
                None => None,
            },
            Iter::Gen(g) => {
                let g = g.clone();
                self.gen_next(&g)?
            }
            Iter::Shared(inner) => {
                let inner = inner.clone();
                let mut b = inner
                    .try_borrow_mut()
                    .map_err(|_| value_err("iterator already in use"))?;
                self.iter_next(&mut b)?
            }
            Iter::Map(f, its) => {
                let f = f.clone();
                let mut args = crate::args::Args::with_capacity(its.len());
                for it in its.iter_mut() {
                    match self.iter_next(it)? {
                        Some(v) => args.push(v),
                        None => return Ok(None),
                    }
                }
                Some(self.call(&f, &mut args, Vec::new())?)
            }
            Iter::Filter(pred, inner) => {
                let pred = pred.clone();
                loop {
                    let Some(v) = self.iter_next(inner)? else {
                        return Ok(None);
                    };
                    let keep = match &pred {
                        None => truthy(&v)?,
                        Some(f) => {
                            let r = self.call(f, &mut crate::args::Args::one(v.clone()), Vec::new())?;
                            truthy(&r)?
                        }
                    };
                    if keep {
                        return Ok(Some(v));
                    }
                }
            }
            Iter::Zip(its) => {
                let mut out = Vec::with_capacity(its.len());
                for it in its.iter_mut() {
                    match self.iter_next(it)? {
                        Some(v) => out.push(v),
                        None => return Ok(None),
                    }
                }
                Some(Value::Tuple(Rc::new(out)))
            }
            Iter::Enumerate(inner, n) => match self.iter_next(inner)? {
                Some(v) => {
                    let i = *n;
                    *n += 1;
                    Some(Value::Tuple(Rc::new(vec![ival(i), v])))
                }
                None => None,
            },
        })
    }

    /// Advance a generator expression by one element.
    pub fn gen_next(&mut self, g: &Rc<RefCell<GenState>>) -> R<Option<Value>> {
        {
            let b = g.borrow();
            if b.done {
                return Ok(None);
            }
            if b.running {
                return Err(value_err("generator already executing"));
            }
        }
        // Take the state out so `eval` below can re-enter without a RefCell
        // conflict; put it back on every exit path.
        let mut st = {
            let mut b = g.borrow_mut();
            let stand_in = GenState::placeholder_like(&b);
            std::mem::replace(&mut *b, stand_in)
        };
        st.running = true;
        // The chain vector comes from the same pool `call_func_inner` uses:
        // this runs once per element yielded, so allocating one here was a
        // malloc and a free per element of every generator expression.
        let mut c = self.take_chain();
        c.extend_from_slice(&st.env);
        if let Some(sc) = &st.scope {
            c.push(sc.clone());
        }
        let saved = std::mem::replace(&mut self.chain, c);
        let r = self.gen_step(&mut st);
        let spent = std::mem::replace(&mut self.chain, saved);
        self.give_chain(spent);
        st.running = false;
        if r.is_err() || matches!(r, Ok(None)) {
            st.done = true;
        }
        *g.borrow_mut() = st;
        r
    }

    fn gen_step(&mut self, st: &mut GenState) -> R<Option<Value>> {
        if !st.started {
            st.started = true;
            let v = self.eval(&st.clauses[0].iter)?;
            let it = self.make_iter(v)?;
            st.stack.push(it);
        }
        while !st.stack.is_empty() {
            let level = st.stack.len() - 1;
            let mut cur = st.stack.pop().unwrap();
            let next = self.iter_next(&mut cur)?;
            let Some(v) = next else { continue };
            st.stack.push(cur);
            // One refcount bump, not a deep copy of the clause's expression
            // tree. It has to leave the borrow on `st` because the loop below
            // pushes onto `st.stack`.
            let clauses = st.clauses.clone();
            let clause = &clauses[level];
            self.assign(&clause.target, v)?;
            let mut ok = true;
            for cond in &clause.ifs {
                let c = self.eval(cond)?;
                if !truthy(&c)? {
                    ok = false;
                    break;
                }
            }
            if !ok {
                continue;
            }
            if level + 1 < clauses.len() {
                let v = self.eval(&clauses[level + 1].iter)?;
                let it = self.make_iter(v)?;
                st.stack.push(it);
                continue;
            }
            let elt = st.elt.clone();
            return Ok(Some(self.eval(&*elt)?));
        }
        Ok(None)
    }

    /// Materialise an iterable. Callers that need laziness (`any`, `all`,
    /// `next`, `for`) must use `make_iter`/`iter_next` instead.
    pub fn iter_collect(&mut self, v: Value) -> R<Vec<Value>> {
        // Fast paths that avoid building an iterator at all.
        match &v {
            Value::List(l) => return Ok(l.borrow().clone()),
            Value::Tuple(t) => return Ok((**t).clone()),
            _ => {}
        }
        let mut it = self.make_iter(v)?;
        let mut out = Vec::new();
        while let Some(x) = self.iter_next(&mut it)? {
            out.push(x);
        }
        Ok(out)
    }

    /// Like `iter_collect`, but allows a set — for the order-INDEPENDENT
    /// consumers (`sorted`, `min`, `max`, `any`, `all`, set algebra).
    pub fn collect_unordered(&mut self, v: Value) -> R<Vec<Value>> {
        if let Value::Set(s) = &v {
            return Ok(s.borrow().items.clone());
        }
        self.iter_collect(v)
    }
}

/// Text-mode decoding, which is where lypning and CPython genuinely disagree —
/// so it **refuses** rather than raising.
///
/// `bytes.decode()` and a whole-file `f.read()` both raise `UnicodeDecodeError`
/// on both interpreters, with the same message and the same position, and those
/// keep using [`decode_utf8_rc`]. Two text paths do not agree, and neither
/// disagreement is fixable cheaply:
///
///   * **stdin.** CPython decodes it with `surrogateescape` (PEP 383), so
///     `for l in sys.stdin` over `b"ok\n\xff\n"` yields `'\udcff\n'` and exits
///     0 where lypning raised.
///   * **A text file, a line at a time.** CPython decodes a buffered block, so
///     it raises *before* yielding any line, at a position counted in the file.
///     lypning reads line by line, so it yielded the first line and then raised
///     at a position counted within the second — different stdout, same exit
///     code, which is the shape invariant 1 exists to stop.
///
/// A refusal is the cheap correct answer: the barrier discards the staged
/// output, the dispatcher hands the program to CPython, and CPython does the
/// surrogateescape. If output has already been committed the refusal is turned
/// into a plain exit-1 error rather than a routable 90, which `main.rs` and
/// `embed.rs` already do for every refusal.
pub fn decode_text(b: &[u8], what: &str) -> R<std::rc::Rc<str>> {
    match std::str::from_utf8(b) {
        Ok(s) => Ok(std::rc::Rc::from(s)),
        Err(_) => Err(unsupported("encoding", what)),
    }
}

/// The same check, straight into the `Rc<str>` a `Value::Str` is made of.
///
/// `decode_utf8` returns a `String`, and every caller that wanted a VALUE then
/// paid `Rc<str>::from(String)` — a second allocation and a second copy of
/// bytes that had already been validated and copied once. On the line-reading
/// path that was **three** copies of every line: the slice out of the buffer,
/// the `String`, and the `Rc<str>`. This makes it two, and the validation is
/// the identical `std::str::from_utf8` with the identical error.
pub fn decode_utf8_rc(b: &[u8]) -> R<std::rc::Rc<str>> {
    match std::str::from_utf8(b) {
        Ok(s) => Ok(std::rc::Rc::from(s)),
        Err(e) => Err(utf8_error(b, &e)),
    }
}

pub fn decode_utf8(b: &[u8]) -> R<String> {
    match std::str::from_utf8(b) {
        Ok(s) => Ok(s.to_string()),
        Err(e) => Err(utf8_error(b, &e)),
    }
}

/// The one place the decode error is worded, so the two decoders above cannot
/// report the same bytes differently.
fn utf8_error(b: &[u8], e: &std::str::Utf8Error) -> LypningError {
    LypningError::exc(
        "UnicodeDecodeError",
        format!(
            "'utf-8' codec can't decode byte 0x{:02x} in position {}: invalid start byte",
            b.get(e.valid_up_to()).copied().unwrap_or(0),
            e.valid_up_to()
        ),
    )
}
