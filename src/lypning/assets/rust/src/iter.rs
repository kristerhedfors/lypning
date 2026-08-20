//! Iteration.
//!
//! Iterators hold data only; the interpreter drives them, so an iterator can
//! call back into `eval` without a borrow cycle. Three shapes are lazy on
//! purpose — `range`, file lines, and generator expressions — because each has
//! a case where materialising changes the answer rather than just the memory
//! use.

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
    /// Dict keys, snapshotted, with the original size so a mutation during
    /// iteration raises the same RuntimeError CPython raises.
    DictKeys {
        d: Rc<RefCell<Dict>>,
        keys: Vec<Value>,
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
}

/// A generator expression, suspended between elements.
pub struct GenState {
    pub clauses: Vec<CompClause>,
    pub elt: Expr,
    pub env: Vec<Scope>,
    pub scope: Scope,
    pub stack: Vec<Iter>,
    pub started: bool,
    pub done: bool,
    /// Set while the generator is being advanced, so a self-referential
    /// generator is reported rather than panicking on the RefCell.
    pub running: bool,
}

impl GenState {
    pub fn new(clauses: Vec<CompClause>, elt: Expr, env: Vec<Scope>) -> Self {
        GenState {
            clauses,
            elt,
            env,
            scope: crate::eval::new_scope(),
            stack: Vec::new(),
            started: false,
            done: false,
            running: false,
        }
    }
    fn placeholder() -> Self {
        GenState {
            clauses: Vec::new(),
            elt: Expr::None,
            env: Vec::new(),
            scope: crate::eval::new_scope(),
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
                Iter::DictKeys { d, keys, i: 0, n0 }
            }
            // See value.rs: reproducing CPython's set order is impossible, so
            // iterating one is refused instead of silently reordered.
            Value::Set(_) => return Err(set_order_refused("iterating a set")),
            Value::File(f) => Iter::Lines(f),
            Value::Gen(g) => Iter::Gen(g),
            Value::IterObj(it, _) => Iter::Shared(it),
            // `for line in sys.stdin` — the largest single cluster in the
            // corpus is `stdin -> transform -> stdout`.
            Value::Module("sys.stdin") => Iter::Stdin,
            Value::DictView(d, kind) => {
                let items = {
                    let b = d.borrow();
                    match kind {
                        "keys" => b.keys(),
                        "values" => b.values(),
                        _ => b.items(),
                    }
                };
                Iter::Vec(items, 0)
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
                    let v = Value::Str(c[*i].to_string().into());
                    *i += 1;
                    Some(v)
                } else {
                    None
                }
            }
            Iter::Bytes(b, i) => {
                if *i < b.len() {
                    let v = Value::Int(b[*i] as i64);
                    *i += 1;
                    Some(v)
                } else {
                    None
                }
            }
            Iter::DictKeys { d, keys, i, n0 } => {
                if d.borrow().len() != *n0 {
                    return Err(LypningError::exc(
                        "RuntimeError",
                        "dictionary changed size during iteration",
                    ));
                }
                if *i < keys.len() {
                    let v = keys[*i].clone();
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
                    Some(Value::Int(v))
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
                    None
                } else {
                    let start = fo.pos;
                    let end = match fo.data[start..].iter().position(|c| *c == b'\n') {
                        Some(i) => start + i + 1,
                        None => fo.data.len(),
                    };
                    fo.pos = end;
                    let chunk = fo.data[start..end].to_vec();
                    Some(if fo.binary {
                        Value::Bytes(Rc::new(chunk))
                    } else {
                        Value::Str(decode_utf8(&chunk)?.into())
                    })
                }
            }
            Iter::Stdin => match mio::stdin_line()? {
                Some(b) => Some(Value::Str(decode_utf8(&b)?.into())),
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
                let mut args = Vec::with_capacity(its.len());
                for it in its.iter_mut() {
                    match self.iter_next(it)? {
                        Some(v) => args.push(v),
                        None => return Ok(None),
                    }
                }
                Some(self.call(&f, args, Vec::new())?)
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
                            let r = self.call(f, vec![v.clone()], Vec::new())?;
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
                    Some(Value::Tuple(Rc::new(vec![Value::Int(i), v])))
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
        let mut st = std::mem::replace(&mut *g.borrow_mut(), GenState::placeholder());
        st.running = true;
        let saved = std::mem::replace(&mut self.chain, {
            let mut c = st.env.clone();
            c.push(st.scope.clone());
            c
        });
        let r = self.gen_step(&mut st);
        self.chain = saved;
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
            let clause = st.clauses[level].clone();
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
            if level + 1 < st.clauses.len() {
                let nxt = st.clauses[level + 1].iter.clone();
                let v = self.eval(&nxt)?;
                let it = self.make_iter(v)?;
                st.stack.push(it);
                continue;
            }
            let elt = st.elt.clone();
            return Ok(Some(self.eval(&elt)?));
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

pub fn decode_utf8(b: &[u8]) -> R<String> {
    match std::str::from_utf8(b) {
        Ok(s) => Ok(s.to_string()),
        Err(e) => Err(LypningError::exc(
            "UnicodeDecodeError",
            format!(
                "'utf-8' codec can't decode byte 0x{:02x} in position {}: invalid start byte",
                b.get(e.valid_up_to()).copied().unwrap_or(0),
                e.valid_up_to()
            ),
        )),
    }
}
