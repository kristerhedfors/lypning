//! The value model.
//!
//! Two deliberate refusals live here, and both exist to keep lypning from ever
//! returning a *plausible wrong answer* — the one outcome that would make a
//! subset runtime worse than nothing:
//!
//!   * **Integers are i64.** Python's are arbitrary-precision. Every arithmetic
//!     op is checked, and an overflow is `unsupported: bigint`, not a wrap.
//!   * **Set iteration order is refused.** CPython's set order falls out of its
//!     internal hashing; no independent implementation can reproduce it. So
//!     order-INDEPENDENT operations on sets are supported (`len`, `in`, the set
//!     algebra, `sorted`, `min`, `max`, `any`, `all`) and anything that would
//!     expose an order (`repr`, iteration, `list()`, `.join`, unpacking) exits
//!     90. A dict, whose order Python *does* define as insertion order, has no
//!     such restriction.

use crate::err::{type_err, unsupported, LypningError, R};
use std::cell::RefCell;
use crate::hash::Map;
use std::rc::Rc;

use crate::ast::{Params, Stmt};

#[derive(Clone)]
pub enum Value {
    None,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(Rc<str>),
    Bytes(Rc<Vec<u8>>),
    List(Rc<RefCell<Vec<Value>>>),
    Tuple(Rc<Vec<Value>>),
    Dict(Rc<RefCell<Dict>>),
    Set(Rc<RefCell<Set>>),
    Range(i64, i64, i64),
    File(Rc<RefCell<crate::io::FileObj>>),
    /// A module object, e.g. `json` or `os.path`.
    Module(&'static str),
    /// A builtin function, identified by its canonical name.
    Builtin(&'static str),
    /// A bound method: receiver + method name.
    Bound(Rc<Value>, &'static str),
    Func(Rc<FuncObj>),
    /// A lazily-evaluated generator expression. Laziness is not a luxury here:
    /// `any(1/x for x in [1, 0])` is `True` in Python because the generator is
    /// never asked for its second element. Materialising it would raise
    /// ZeroDivisionError instead — a wrong answer, not a refusal.
    Gen(Rc<RefCell<crate::iter::GenState>>),
    /// A live iterator object (`enumerate`, `zip`, `map`, `filter`, …). Its
    /// CPython repr embeds a heap address, so `repr()` of one is refused rather
    /// than faked.
    IterObj(Rc<RefCell<crate::iter::Iter>>, &'static str),
    /// `d.keys()` / `.values()` / `.items()`. Unlike the iterators above these
    /// have a fully deterministic repr, so they are a separate value.
    DictView(Rc<RefCell<Dict>>, &'static str),
    /// An exception instance, as produced by `except E as e`.
    Exc(&'static str, Rc<str>),
}

pub struct FuncObj {
    pub name: Rc<str>,
    pub params: Rc<Params>,
    pub body: Rc<Vec<Stmt>>,
    pub defaults: Vec<Option<Value>>,
    /// Lambdas carry a body expression instead of a statement list.
    pub lambda: Option<Rc<crate::ast::Expr>>,
    /// The scope chain this function was defined in — its closure.
    pub env: Vec<crate::eval::Scope>,
    /// Every name the body assigns; see `eval::assigned_names`.
    pub assigned: Rc<crate::hash::Set<Rc<str>>>,
}

// ---- hashable keys --------------------------------------------------------

#[derive(Clone, PartialEq, Eq, Hash)]
pub enum HKey {
    None,
    Int(i64),
    /// Only non-integral floats land here; `1.0` normalizes to `Int(1)` so that
    /// `{1: 'a', 1.0: 'b'}` collapses to one entry exactly as Python does.
    Float(u64),
    Str(Rc<str>),
    Bytes(Rc<Vec<u8>>),
    Tuple(Vec<HKey>),
}

pub fn hkey(v: &Value) -> R<HKey> {
    // The recursion guard is taken on the ONE arm that descends, below, and not
    // here. It is a thread_local read-modify-write in and another out, plus an
    // `R<Nest>` built and dropped — and every dict get, every dict insert and
    // every `in` over a dict or set paid it to hash a string or an int, neither
    // of which can recurse at all.
    Ok(match v {
        Value::None => HKey::None,
        Value::Bool(b) => HKey::Int(*b as i64),
        Value::Int(i) => HKey::Int(*i),
        Value::Float(f) => {
            if f.is_finite() && f.fract() == 0.0 && *f >= -(2f64.powi(63)) && *f < 2f64.powi(63) {
                HKey::Int(*f as i64)
            } else {
                HKey::Float(f.to_bits())
            }
        }
        Value::Str(s) => HKey::Str(s.clone()),
        Value::Bytes(b) => HKey::Bytes(b.clone()),
        Value::Tuple(t) => {
            // The one arm that descends.
            let _nest = crate::err::Nest::enter("tuple key")?;
            let mut out = Vec::with_capacity(t.len());
            for x in t.iter() {
                out.push(hkey(x)?);
            }
            HKey::Tuple(out)
        }
        other => {
            return Err(type_err(format!(
                "unhashable type: '{}'",
                type_name(other)
            )))
        }
    })
}

// ---- dict -----------------------------------------------------------------

/// Insertion-ordered mapping. Python guarantees insertion order since 3.7, so
/// reproducing it is required, not optional: `print(d)` and `json.dumps(d)`
/// both expose it.
#[derive(Default)]
pub struct Dict {
    pub entries: Vec<(Value, Value)>,
    index: Map<HKey, usize>,
    /// Tombstone count; `entries` holds `Value::None` placeholders that
    /// `iter()` skips, so deletion does not disturb the order of the rest.
    holes: usize,
    dead: Vec<bool>,
}

impl Dict {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn len(&self) -> usize {
        self.entries.len() - self.holes
    }
    pub fn get(&self, k: &Value) -> R<Option<Value>> {
        let h = hkey(k)?;
        Ok(self.index.get(&h).map(|i| self.entries[*i].1.clone()))
    }
    pub fn contains(&self, k: &Value) -> R<bool> {
        Ok(self.index.contains_key(&hkey(k)?))
    }
    pub fn insert(&mut self, k: Value, v: Value) -> R<()> {
        let h = hkey(&k)?;
        match self.index.get(&h) {
            // Python keeps the ORIGINAL key object and position on overwrite.
            Some(i) => self.entries[*i].1 = v,
            None => {
                self.index.insert(h, self.entries.len());
                self.entries.push((k, v));
                self.dead.push(false);
            }
        }
        Ok(())
    }
    pub fn remove(&mut self, k: &Value) -> R<Option<Value>> {
        let h = hkey(k)?;
        match self.index.remove(&h) {
            Some(i) => {
                let old = std::mem::replace(&mut self.entries[i].1, Value::None);
                self.dead[i] = true;
                self.holes += 1;
                Ok(Some(old))
            }
            None => Ok(None),
        }
    }
    pub fn iter(&self) -> impl Iterator<Item = (&Value, &Value)> {
        self.entries
            .iter()
            .enumerate()
            .filter(move |(i, _)| !self.dead[*i])
            .map(|(_, (k, v))| (k, v))
    }
    pub fn keys(&self) -> Vec<Value> {
        self.iter().map(|(k, _)| k.clone()).collect()
    }
    pub fn values(&self) -> Vec<Value> {
        self.iter().map(|(_, v)| v.clone()).collect()
    }
    pub fn items(&self) -> Vec<Value> {
        self.iter()
            .map(|(k, v)| Value::Tuple(Rc::new(vec![k.clone(), v.clone()])))
            .collect()
    }
}

// ---- set ------------------------------------------------------------------

#[derive(Default)]
pub struct Set {
    /// Kept in insertion order so `sorted()` is stable for equal keys, but the
    /// order is NEVER observable — see the module comment.
    pub items: Vec<Value>,
    index: Map<HKey, usize>,
}

impl Set {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn len(&self) -> usize {
        self.items.len()
    }
    pub fn contains(&self, v: &Value) -> R<bool> {
        Ok(self.index.contains_key(&hkey(v)?))
    }
    pub fn add(&mut self, v: Value) -> R<()> {
        let h = hkey(&v)?;
        if !self.index.contains_key(&h) {
            self.index.insert(h, self.items.len());
            self.items.push(v);
        }
        Ok(())
    }
    pub fn discard(&mut self, v: &Value) -> R<bool> {
        let h = hkey(v)?;
        match self.index.remove(&h) {
            Some(i) => {
                self.items.remove(i);
                for (_, idx) in self.index.iter_mut() {
                    if *idx > i {
                        *idx -= 1;
                    }
                }
                Ok(true)
            }
            None => Ok(false),
        }
    }
}

/// The refusal that keeps set output honest.
pub fn set_order_refused(what: &str) -> crate::err::LypningError {
    unsupported(
        "set-order",
        &format!("{what} exposes set iteration order, which CPython defines by its own hashing"),
    )
}

// ---- helpers --------------------------------------------------------------

pub fn type_name(v: &Value) -> &'static str {
    match v {
        Value::None => "NoneType",
        Value::Bool(_) => "bool",
        Value::Int(_) => "int",
        Value::Float(_) => "float",
        Value::Str(_) => "str",
        Value::Bytes(_) => "bytes",
        Value::List(_) => "list",
        Value::Tuple(_) => "tuple",
        Value::Dict(_) => "dict",
        Value::Set(_) => "set",
        Value::Range(..) => "range",
        Value::File(_) => "TextIOWrapper",
        Value::Module(_) => "module",
        Value::Builtin(_) | Value::Bound(..) => "builtin_function_or_method",
        Value::Func(_) => "function",
        Value::Gen(_) => "generator",
        Value::IterObj(_, k) => k,
        Value::DictView(_, k) => match *k {
            "keys" => "dict_keys",
            "values" => "dict_values",
            _ => "dict_items",
        },
        Value::Exc(..) => "Exception",
    }
}

pub fn truthy(v: &Value) -> R<bool> {
    Ok(match v {
        Value::None => false,
        Value::Bool(b) => *b,
        Value::Int(i) => *i != 0,
        Value::Float(f) => *f != 0.0,
        Value::Str(s) => !s.is_empty(),
        Value::Bytes(b) => !b.is_empty(),
        Value::List(l) => !l.borrow().is_empty(),
        Value::Tuple(t) => !t.is_empty(),
        Value::Dict(d) => d.borrow().len() != 0,
        Value::Set(s) => s.borrow().len() != 0,
        Value::Range(a, b, st) => range_len(*a, *b, *st) > 0,
        Value::DictView(d, _) => d.borrow().len() != 0,
        _ => true,
    })
}

pub fn range_len(start: i64, stop: i64, step: i64) -> i64 {
    if step > 0 {
        if stop > start {
            (stop - start - 1) / step + 1
        } else {
            0
        }
    } else if start > stop {
        (start - stop - 1) / (-step) + 1
    } else {
        0
    }
}

pub fn list(v: Vec<Value>) -> Value {
    Value::List(Rc::new(RefCell::new(v)))
}
pub fn str_val(s: impl Into<Rc<str>>) -> Value {
    Value::Str(s.into())
}

/// Structural equality with Python's numeric-tower rules (`1 == 1.0 == True`).
/// CPython compares container elements with `x is y or x == y` — IDENTITY
/// first — and that shortcut is observable for exactly one value: a NaN, which
/// is not equal to itself.
///
///     n = float("nan")
///     n in [n]        # True in CPython: the same object
///     [n] == [n]      # True, element by element, for the same reason
///     float("nan") in [float("nan")]   # False: two different objects
///
/// A float here is a bare `f64` with no object identity, so those three cases
/// are indistinguishable and every answer is wrong for one of them. Refused
/// rather than guessed.
///
/// **Deliberately SHALLOW.** The first version of this recursed into nested
/// containers, which meant a second full-depth traversal running INSIDE one
/// level of `eq`'s recursion guard — so `x == y` over two 20,000-deep nested
/// lists overflowed the stack before the guard could fire, and killed the
/// process on a 1 MB host thread. `eq` already descends, guarded, and every
/// level runs this check on its own immediate elements, so a shallow test
/// covers the same ground with no recursion of its own.
fn nan_here(v: &Value) -> bool {
    matches!(v, Value::Float(f) if f.is_nan())
}

/// The immediate elements only — see [`nan_here`].
pub fn has_nan(v: &Value) -> bool {
    match v {
        Value::Float(f) => f.is_nan(),
        Value::List(l) => l.borrow().iter().any(nan_here),
        Value::Tuple(t) => t.iter().any(nan_here),
        _ => false,
    }
}

fn refuse_nan_identity(what: &str) -> LypningError {
    unsupported(
        "nan-identity",
        &format!("{what} over a NaN, which CPython decides by object identity"),
    )
}

pub fn eq(a: &Value, b: &Value) -> R<bool> {
    // The scalar cases first, and BEFORE the recursion guard: none of them can
    // descend, and this is the hottest comparison in the interpreter.
    match (a, b) {
        (Value::None, Value::None) => return Ok(true),
        (Value::Str(x), Value::Str(y)) => return Ok(x == y),
        (Value::Bytes(x), Value::Bytes(y)) => return Ok(x == y),
        (Value::Bool(x), Value::Bool(y)) => return Ok(x == y),
        _ => {}
    }
    if let (Some(x), Some(y)) = (as_num(a), as_num(b)) {
        return Ok(num_eq(x, y));
    }
    // Only the composite arms below can descend, so only they take the guard.
    // What it is for is `x == y` over two deep lists, which was a stack
    // overflow — and a stack overflow embedded is the HOST's SIGSEGV rather
    // than a refusal it can route onward. That path still takes it once per
    // level, exactly as before; what changed is that `1 == 2` no longer does.
    let _nest = crate::err::Nest::enter("comparison")?;
    Ok(match (a, b) {
        (Value::List(x), Value::List(y)) => {
            if Rc::ptr_eq(x, y) {
                return Ok(true);
            }
            let (x, y) = (x.borrow(), y.borrow());
            if x.iter().any(nan_here) || y.iter().any(nan_here) {
                return Err(refuse_nan_identity("sequence equality"));
            }
            seq_eq(&x, &y)?
        }
        (Value::Tuple(x), Value::Tuple(y)) => {
            if x.iter().any(nan_here) || y.iter().any(nan_here) {
                return Err(refuse_nan_identity("sequence equality"));
            }
            seq_eq(x, y)?
        }
        (Value::Dict(x), Value::Dict(y)) => {
            if Rc::ptr_eq(x, y) {
                return Ok(true);
            }
            let (xd, yd) = (x.borrow(), y.borrow());
            if xd.len() != yd.len() {
                return Ok(false);
            }
            for (k, v) in xd.iter() {
                match yd.get(k)? {
                    Some(other) => {
                        if !eq(v, &other)? {
                            return Ok(false);
                        }
                    }
                    None => return Ok(false),
                }
            }
            true
        }
        (Value::Set(x), Value::Set(y)) => {
            let (xs, ys) = (x.borrow(), y.borrow());
            if xs.len() != ys.len() {
                return Ok(false);
            }
            for it in xs.items.iter() {
                if !ys.contains(it)? {
                    return Ok(false);
                }
            }
            true
        }
        (Value::Range(a1, b1, c1), Value::Range(a2, b2, c2)) => {
            a1 == a2 && b1 == b2 && c1 == c2
        }
        (Value::DictView(x, kx), Value::DictView(y, ky)) => {
            // The three views do NOT compare alike, and treating them alike was
            // wrong in both directions at once: `d.values() == d.values()` said
            // True where CPython says False, and `d.keys() == other.keys()` said
            // False where CPython says True.
            //
            // `dict_values` has no `__eq__` at all, so two of them fall back to
            // identity — even two views of the SAME dict are unequal, because
            // they are different objects. `dict_keys` and `dict_items` are
            // set-like: equal when they hold the same elements, whatever the
            // order and whichever dict they came from.
            if kx != ky {
                false
            } else if *kx == "values" {
                // `dict_values` compares by OBJECT IDENTITY, and a view here is
                // just `(Rc<dict>, kind)` — it has no identity of its own, so
                // `v == v` and `d.values() == d.values()` are indistinguishable
                // while CPython answers True and False. Refused rather than
                // guessed: the chain answers it correctly one spawn later, and
                // either guess is a silent wrong answer for the other case.
                return Err(unsupported(
                    "dict-view",
                    "dict_values comparison, which CPython decides by object identity",
                ));
            } else if Rc::ptr_eq(x, y) {
                true
            } else {
                // Collected into owned vectors so both borrows are released
                // before the element comparison below, which recurses into `eq`
                // and may borrow either dict again.
                let (a, b) = {
                    let (dx, dy) = (x.borrow(), y.borrow());
                    if *kx == "keys" {
                        (dx.keys(), dy.keys())
                    } else {
                        (dx.items(), dy.items())
                    }
                };
                if a.len() != b.len() {
                    false
                } else {
                    let mut same = true;
                    for i in &a {
                        let mut found = false;
                        for j in &b {
                            if eq(i, j)? {
                                found = true;
                                break;
                            }
                        }
                        if !found {
                            same = false;
                            break;
                        }
                    }
                    same
                }
            }
        }
        (Value::Module(x), Value::Module(y)) => x == y,
        (Value::Builtin(x), Value::Builtin(y)) => x == y,
        (Value::Func(x), Value::Func(y)) => Rc::ptr_eq(x, y),
        (Value::File(x), Value::File(y)) => Rc::ptr_eq(x, y),
        _ => false,
    })
}

fn seq_eq(x: &[Value], y: &[Value]) -> R<bool> {
    if x.len() != y.len() {
        return Ok(false);
    }
    for (a, b) in x.iter().zip(y.iter()) {
        if !eq(a, b)? {
            return Ok(false);
        }
    }
    Ok(true)
}

#[derive(Clone, Copy)]
pub enum Num {
    I(i64),
    F(f64),
}

pub fn as_num(v: &Value) -> Option<Num> {
    match v {
        Value::Bool(b) => Some(Num::I(*b as i64)),
        Value::Int(i) => Some(Num::I(*i)),
        Value::Float(f) => Some(Num::F(*f)),
        _ => None,
    }
}

fn num_eq(a: Num, b: Num) -> bool {
    match (a, b) {
        (Num::I(x), Num::I(y)) => x == y,
        (Num::F(x), Num::F(y)) => x == y,
        (Num::I(x), Num::F(y)) | (Num::F(y), Num::I(x)) => (x as f64) == y && y.fract() == 0.0
            || (x as f64) == y,
    }
}

/// Identity, for `is`. Only the cases Python actually guarantees.
pub fn is_same(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::None, Value::None) => true,
        (Value::Bool(x), Value::Bool(y)) => x == y,
        (Value::List(x), Value::List(y)) => Rc::ptr_eq(x, y),
        (Value::Dict(x), Value::Dict(y)) => Rc::ptr_eq(x, y),
        (Value::Set(x), Value::Set(y)) => Rc::ptr_eq(x, y),
        (Value::Tuple(x), Value::Tuple(y)) => Rc::ptr_eq(x, y),
        (Value::Str(x), Value::Str(y)) => Rc::ptr_eq(x, y),
        (Value::File(x), Value::File(y)) => Rc::ptr_eq(x, y),
        (Value::Func(x), Value::Func(y)) => Rc::ptr_eq(x, y),
        (Value::Module(x), Value::Module(y)) => x == y,
        (Value::Builtin(x), Value::Builtin(y)) => x == y,
        // Small-int caching is an implementation detail agents should not rely
        // on and we will not reproduce; refusing beats guessing either way.
        _ => false,
    }
}

// ---- taking a deep structure apart without recursing on it ------------------

/// Drop a value iteratively, so a deeply nested one cannot overflow the stack.
///
/// Rust's derived drop is recursive: dropping a list that contains a list that
/// contains a list unwinds one stack frame per level, and a program can build
/// a hundred thousand levels with a two-line loop. The `lypning` BINARY never
/// noticed, because the structures it builds are alive when the process exits
/// and the kernel reclaims them. A library has no such exit: it hands the host
/// back its thread, so it must actually take the value apart — and doing that
/// the obvious way is a SIGSEGV in somebody else's process, which no
/// `catch_unwind` can intercept because a stack overflow is not an unwind.
///
/// So the children are pushed onto a heap worklist instead of being dropped in
/// place. Nothing here ever nests deeper than one frame, whatever the value's
/// shape. `Rc::try_unwrap` is what makes it correct as well as safe: a shared
/// child is not ours to dismantle, and dropping our handle to it merely
/// decrements, which does not recurse either.
pub fn dismantle(root: Value) {
    let mut work = vec![root];
    while let Some(v) = work.pop() {
        match v {
            Value::List(rc) => {
                if let Ok(cell) = Rc::try_unwrap(rc) {
                    work.extend(cell.into_inner());
                }
            }
            Value::Tuple(rc) => {
                if let Ok(items) = Rc::try_unwrap(rc) {
                    work.extend(items);
                }
            }
            Value::Dict(rc) => {
                if let Ok(cell) = Rc::try_unwrap(rc) {
                    for (k, val) in cell.into_inner().entries {
                        work.push(k);
                        work.push(val);
                    }
                }
            }
            Value::Set(rc) => {
                if let Ok(cell) = Rc::try_unwrap(rc) {
                    work.extend(cell.into_inner().items);
                }
            }
            Value::Bound(rc, _) => {
                if let Ok(inner) = Rc::try_unwrap(rc) {
                    work.push(inner);
                }
            }
            // Everything else is either a scalar or an `Rc` to something whose
            // own depth is bounded by the parser (`parse::MAX_PARSE_DEPTH`), so
            // its recursive drop is bounded too.
            _ => {}
        }
    }
}
