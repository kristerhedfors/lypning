//! `collections.Counter` and `collections.defaultdict` — the whole of the
//! `cap-collections` capability, compiled into `lypning-l` and into nothing
//! smaller. Every line of this file, and every line that reaches it, is behind
//! `cfg(feature = "cap-collections")`, so the `lypning` binary does not move a
//! byte for it.
//!
//! Both types ARE dicts here: a `Value::Dict` whose `Dict::coll` tag says which
//! one. That is not a shortcut, it is the point — `Counter` and `defaultdict`
//! are dict SUBCLASSES in CPython, so `len`, `in`, iteration order, `==`
//! against a plain dict, `.keys()`/`.items()`, `dict(c)`, `{**c}`,
//! `json.dumps(c)` and key collapse (`1`, `1.0`, `True` are one key, FIRST key
//! object and LAST value) all have to be the dict's own behaviour. Reusing
//! `Dict` makes them so by construction rather than by a second implementation
//! that would have to be kept in step.
//!
//! What is left is the short list of places a subclass actually differs, and
//! every one of them is a hook into this file:
//!
//!   * `c[missing]` is `0` and inserts nothing; `d[missing]` on a defaultdict
//!     calls the factory and DOES insert. `in` inserts for neither.
//!   * `del c[missing]` is a no-op on a Counter (it overrides `__delitem__`)
//!     and a `KeyError` on a defaultdict.
//!   * `repr` — `Counter({'a': 2, 'b': 1})` in most_common order,
//!     `defaultdict(<class 'list'>, {})` with the real class repr.
//!   * `.most_common()`, and `Counter.update()` which ADDS counts where
//!     `dict.update` replaces them.
//!   * `.copy()` and `.clear()` keep the type.
//!   * every operator (`+`, `-`, `&`, `|`, `<`, unary `-`) — Counter defines
//!     multiset arithmetic and this engine does not have it.
//!
//! **The refusals are the design, not the leftovers.** Every exception MESSAGE
//! these types raise differs across CPython 3.11/3.12/3.14, so no error path is
//! reimplemented: a bad argument, a non-callable `default_factory`, a count
//! that is not an int, `.elements()`, `.subtract()`, arithmetic — each is
//! `unsupported: collections: …` and reaches CPython one spawn later.
//! A refusal is never a bug (invariant 1); a message this engine cannot pin is.

use crate::args::Args;
use crate::err::{key_err, unsupported, LypningError, R};
use crate::eval::Interp;
use crate::fmt;
use crate::value::{hkey, set_order_refused, type_name, Dict, Value};
use std::cell::RefCell;
use std::rc::Rc;

/// Which of the two a `Dict` is. `Copy`, and read through [`kind_of`], so
/// asking never needs a mutable borrow.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Counter,
    /// `defaultdict`, carrying its `default_factory` as the builtin type name
    /// it must be — `None` for `defaultdict()`, whose missing key is an
    /// ordinary `KeyError`.
    Default(Option<&'static str>),
}

/// The `default_factory` values this engine will accept. A lambda, a `def`, or
/// anything else is refused rather than called: `defaultdict(lambda: 0)` is
/// real Python, and answering it would mean calling back into the interpreter
/// from every missing-key read — worth doing, not worth guessing at.
const FACTORIES: &[&str] = &["bool", "dict", "float", "int", "list", "set", "str", "tuple"];

/// Sorted, because [`method_name`] binary-searches them (`methods.rs` says why).
/// `most_common` is the only name here that a plain dict does not have; every
/// other one is delegated to `dict_method` so that a Counter's `.get` is
/// literally `dict.get`, message for message.
const COUNTER_METHODS: &[&str] = &[
    "clear", "copy", "get", "items", "keys", "most_common", "pop", "popitem", "setdefault",
    "update", "values",
];
const DEFAULT_METHODS: &[&str] = &[
    "clear", "copy", "get", "items", "keys", "pop", "popitem", "setdefault", "update", "values",
];

pub fn kind_name(k: Kind) -> &'static str {
    match k {
        Kind::Counter => "Counter",
        Kind::Default(_) => "defaultdict",
    }
}

/// The tag on `d`, or `None` for a plain dict.
///
/// `try_borrow`, never `borrow`: this is called from `type_name`, which runs
/// inside error paths that a live `borrow_mut` may already have taken (a dict
/// used as its own key). A panic there is an abort, and an abort is the one
/// outcome the exit-90 contract cannot survive. [`self_key_guard`] closes the
/// one case where the fallback would have printed the wrong type name.
pub fn kind_of(d: &Rc<RefCell<Dict>>) -> Option<Kind> {
    d.try_borrow().ok().and_then(|b| b.coll)
}

pub fn is_coll(v: &Value) -> bool {
    matches!(v, Value::Dict(d) if kind_of(d).is_some())
}

/// Every operator refuses when either side is one of these.
///
/// `Counter` defines `+ - & |` as multiset arithmetic, `< <= > >=` as multiset
/// containment (3.10+), and unary `+`/`-` as "drop the non-positive counts".
/// Falling through would answer a plain dict's `TypeError` — or, for `|`, a
/// dict MERGE, which is a different answer at exit 0. `==` is deliberately not
/// here: it is `dict.__eq__` for both types, which is what `value::eq` already
/// does.
pub fn guard_operand(a: &Value, b: &Value) -> R<()> {
    if is_coll(a) || is_coll(b) {
        return Err(unsupported(
            "collections",
            "an operator over a Counter or defaultdict (Counter defines multiset arithmetic)",
        ));
    }
    Ok(())
}

/// An attribute that is not one of the methods above — `.default_factory`
/// most of all, whose value is a class object this engine has no repr for.
pub fn attr_refused(k: Kind, name: &str) -> LypningError {
    unsupported("collections", &format!("{}.{name}", kind_name(k)))
}

/// The router's optimistic method union (`route::known_method`) has to admit
/// the one name that is not on any other type, or `c.most_common()` blocks the
/// program that this capability exists to run.
pub fn known_method(name: &str) -> bool {
    name == "most_common"
}

pub fn method_name(k: Kind, name: &str) -> Option<&'static str> {
    let t: &[&str] = match k {
        Kind::Counter => COUNTER_METHODS,
        Kind::Default(_) => DEFAULT_METHODS,
    };
    t.binary_search(&name).ok().map(|i| t[i])
}

fn empty(k: Kind) -> Dict {
    let mut d = Dict::new();
    d.coll = Some(k);
    d
}

fn default_value(f: &'static str) -> Value {
    match f {
        "int" => Value::Int(0),
        "float" => Value::Float(0.0),
        "str" => Value::Str("".into()),
        "bool" => Value::Bool(false),
        "list" => crate::value::list(Vec::new()),
        "tuple" => Value::Tuple(Rc::new(Vec::new())),
        "set" => Value::Set(Rc::new(RefCell::new(crate::value::Set::new()))),
        _ => Value::Dict(Rc::new(RefCell::new(Dict::new()))),
    }
}

/// A count, as the `i64` every ordering and every addition here needs.
///
/// A Counter built by counting always holds ints. One built from a mapping can
/// hold anything at all (`Counter({'a': 'x'})` is legal), and CPython then
/// answers `TypeError` from inside `heapq`, or falls back to insertion order in
/// `__repr__`, depending on which operation asked. Refused rather than
/// reproduced.
fn int_count(v: &Value) -> R<i64> {
    match v {
        Value::Int(i) => Ok(*i),
        _ => Err(unsupported(
            "collections",
            "a Counter count that is not an int, which CPython orders by comparing the values",
        )),
    }
}

fn count_overflow() -> LypningError {
    unsupported("bigint", "a Counter count past 64 bits (Python would use a bignum)")
}

/// `most_common()`'s order, which is the whole reason this capability is risky.
///
/// CPython is `sorted(self.items(), key=itemgetter(1), reverse=True)`, and
/// `list.sort` is STABLE — `reverse=True` reverses the comparison, never the
/// run of equal elements — so counts that tie keep INSERTION order.
/// `Counter('abracadabra')` is `a b r c d`, not `a r b d c`. `sort_by` here is
/// stable for the same reason and must stay so.
fn ordered(d: &Dict) -> R<Vec<(Value, Value)>> {
    let mut rows: Vec<(i64, Value, Value)> = Vec::with_capacity(d.len());
    for (k, v) in d.iter() {
        rows.push((int_count(v)?, k.clone(), v.clone()));
    }
    rows.sort_by(|a, b| b.0.cmp(&a.0));
    Ok(rows.into_iter().map(|(_, k, v)| (k, v)).collect())
}

/// `repr()` of either type — the exact CPython text or a refusal, never an
/// approximation (invariant 1, and `fmt.rs`'s whole premise).
///
/// `Counter.__repr__` is `f'{name}({dict(self.most_common())!r})'`, and bare
/// `Counter()` when empty. `defaultdict_repr` is
/// `defaultdict(<factory repr>, <dict repr>)`, where the factory repr of a
/// builtin type is `<class 'list'>`. CPython's Counter falls back to insertion
/// order when the counts are not orderable; [`int_count`] refuses that case
/// instead, so nothing here guesses at an order.
pub fn repr(d: &Dict) -> R<String> {
    match d.coll {
        Some(Kind::Counter) => {
            if d.len() == 0 {
                return Ok("Counter()".into());
            }
            let mut out = String::from("Counter({");
            for (i, (k, v)) in ordered(d)?.into_iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                out.push_str(&fmt::repr(&k)?);
                out.push_str(": ");
                out.push_str(&fmt::repr(&v)?);
            }
            out.push_str("})");
            Ok(out)
        }
        Some(Kind::Default(f)) => {
            let factory = match f {
                None => "None".to_string(),
                Some(t) => format!("<class '{t}'>"),
            };
            Ok(format!("defaultdict({factory}, {})", fmt::dict_repr(d)?))
        }
        None => fmt::dict_repr(d),
    }
}

/// `c[k]` — the one place the two types disagree with each other as well as
/// with `dict`. A Counter answers `0` and inserts NOTHING; a defaultdict calls
/// its factory and DOES insert, which is observable one line later in `len()`
/// and in `repr()`.
pub fn index(cell: &Rc<RefCell<Dict>>, k: Kind, idx: &Value) -> R<Value> {
    if let Some(v) = cell.borrow().get(idx)? {
        return Ok(v);
    }
    match k {
        Kind::Counter => Ok(Value::Int(0)),
        Kind::Default(None) => Err(key_err(fmt::repr(idx)?)),
        Kind::Default(Some(f)) => {
            let v = default_value(f);
            cell.borrow_mut().insert(idx.clone(), v.clone())?;
            Ok(v)
        }
    }
}

/// `del c[k]`. `Counter.__delitem__` is documented as "does not raise KeyError
/// for missing values" and is a no-op there; a defaultdict does not override it.
pub fn del_item(cell: &Rc<RefCell<Dict>>, k: Kind, idx: &Value) -> R<()> {
    self_key_guard(idx)?;
    let gone = cell.borrow_mut().remove(idx)?.is_some();
    if !gone && !matches!(k, Kind::Counter) {
        return Err(key_err(fmt::repr(idx)?));
    }
    Ok(())
}

/// Hash an unhashable key BEFORE the mutable borrow that would make
/// `type_name` unable to read the tag.
///
/// `c[c] = 1` takes `borrow_mut` and then hashes the key, and the key is the
/// same object — so `kind_of`'s `try_borrow` fails and the `TypeError` would
/// say `'dict'` where CPython says `'Counter'`. Asking here, with nothing
/// borrowed, makes the message exact. Only a dict key can be self-referential
/// this way, so nothing else pays for it.
pub fn self_key_guard(idx: &Value) -> R<()> {
    if matches!(idx, Value::Dict(_)) {
        hkey(idx)?;
    }
    Ok(())
}

/// `Counter(x)` / `defaultdict(f)`.
pub fn construct(
    it: &mut Interp,
    name: &str,
    args: &mut Args,
    kw: Vec<(Rc<str>, Value)>,
) -> R<Value> {
    if !kw.is_empty() {
        // `Counter(a=1)` is real, and its semantics are `update(kwds)` on a
        // possibly non-empty self. Refused rather than half-implemented.
        return Err(unsupported(
            "collections",
            &format!("{name}() with keyword arguments"),
        ));
    }
    if args.len() > 1 {
        return Err(unsupported(
            "collections",
            &format!("{name}() with more than one argument"),
        ));
    }
    if name == "Counter" {
        let cell = Rc::new(RefCell::new(empty(Kind::Counter)));
        if let Some(v) = args.first().cloned() {
            update(it, &cell, &v)?;
        }
        return Ok(Value::Dict(cell));
    }
    let f = match args.first() {
        None | Some(Value::None) => None,
        Some(Value::Builtin(b)) if FACTORIES.contains(b) => Some(*b),
        Some(other) => {
            return Err(unsupported(
                "collections",
                &format!(
                    "defaultdict() with a {} default_factory (only the builtin types are served)",
                    type_name(other)
                ),
            ))
        }
    };
    Ok(Value::Dict(Rc::new(RefCell::new(empty(Kind::Default(f))))))
}

/// `Counter.update(x)` — which ADDS counts, where `dict.update` replaces them.
///
/// CPython's shape exactly: a mapping into an EMPTY counter is copied verbatim
/// (`super().update`), a mapping into a non-empty one adds
/// (`self[k] = count + self.get(k, 0)`), and anything else is counted element
/// by element. A set is refused, not counted: the counts would be right and the
/// insertion order — which `repr` and `most_common` expose — would be CPython's
/// set hashing, which `value.rs` refuses to reproduce.
fn update(it: &mut Interp, cell: &Rc<RefCell<Dict>>, v: &Value) -> R<()> {
    match v {
        Value::Dict(src) => {
            let pairs: Vec<(Value, Value)> = src
                .borrow()
                .iter()
                .map(|(k, x)| (k.clone(), x.clone()))
                .collect();
            let fresh = cell.borrow().len() == 0;
            for (k, x) in pairs {
                if fresh {
                    self_key_guard(&k)?;
                    cell.borrow_mut().insert(k, x)?;
                    continue;
                }
                let add = int_count(&x)?;
                let cur = match cell.borrow().get(&k)? {
                    Some(c) => int_count(&c)?,
                    None => 0,
                };
                let n = cur.checked_add(add).ok_or_else(count_overflow)?;
                self_key_guard(&k)?;
                cell.borrow_mut().insert(k, Value::Int(n))?;
            }
        }
        Value::Set(_) => return Err(set_order_refused("Counter() over a set")),
        Value::Str(_)
        | Value::Bytes(_)
        | Value::List(_)
        | Value::Tuple(_)
        | Value::Range(..)
        | Value::Gen(_)
        | Value::IterObj(..)
        | Value::DictView(..) => {
            for x in it.iter_collect(v.clone())? {
                let cur = match cell.borrow().get(&x)? {
                    Some(c) => int_count(&c)?,
                    None => 0,
                };
                let n = cur.checked_add(1).ok_or_else(count_overflow)?;
                self_key_guard(&x)?;
                cell.borrow_mut().insert(x, Value::Int(n))?;
            }
        }
        other => {
            // `Counter(1)` is a TypeError whose text this engine will not pin.
            return Err(unsupported(
                "collections",
                &format!("Counter() over a {}", type_name(other)),
            ));
        }
    }
    Ok(())
}

/// Every method call on either type. The ones a dict already gets right are
/// DELEGATED, so their arity checks, their keyword rejection and their error
/// messages are `dict`'s own — which is what CPython inherits too.
pub fn method(
    it: &mut Interp,
    cell: &Rc<RefCell<Dict>>,
    k: Kind,
    name: &str,
    args: &mut Args,
    kw: Vec<(Rc<str>, Value)>,
) -> R<Value> {
    match (k, name) {
        (Kind::Counter, "most_common") => {
            if !kw.is_empty() || args.len() > 1 {
                return Err(unsupported(
                    "collections",
                    "Counter.most_common() with anything but one positional argument",
                ));
            }
            // `most_common(n)` is `heapq.nlargest`, which is documented as
            // equivalent to `sorted(…, reverse=True)[:n]` and breaks ties by
            // original position — so it is this list, truncated. `None` and a
            // negative n are both CPython's own: `None` means all, and a
            // negative one yields nothing.
            let n = match args.first() {
                None | Some(Value::None) => None,
                Some(Value::Int(i)) => Some(*i),
                Some(Value::Bool(b)) => Some(*b as i64),
                Some(other) => {
                    return Err(unsupported(
                        "collections",
                        &format!("Counter.most_common() with a {} argument", type_name(other)),
                    ))
                }
            };
            let rows = ordered(&cell.borrow())?;
            let take = match n {
                None => rows.len(),
                Some(i) => i.clamp(0, rows.len() as i64) as usize,
            };
            Ok(crate::value::list(
                rows.into_iter()
                    .take(take)
                    .map(|(a, b)| Value::Tuple(Rc::new(vec![a, b])))
                    .collect(),
            ))
        }
        (Kind::Counter, "update") => {
            if !kw.is_empty() || args.len() > 1 {
                return Err(unsupported(
                    "collections",
                    "Counter.update() with anything but one positional argument",
                ));
            }
            if let Some(v) = args.first().cloned() {
                update(it, cell, &v)?;
            }
            Ok(Value::None)
        }
        // `.copy()` and `.clear()` keep the TYPE — `dict_method`'s versions
        // build a plain `Dict`, which would print as `{'a': 2}` one line later.
        (_, "copy") if args.is_empty() && kw.is_empty() => {
            let mut out = empty(k);
            for (a, b) in cell.borrow().iter() {
                out.insert(a.clone(), b.clone())?;
            }
            Ok(Value::Dict(Rc::new(RefCell::new(out))))
        }
        (_, "clear") if args.is_empty() && kw.is_empty() => {
            *cell.borrow_mut() = empty(k);
            Ok(Value::None)
        }
        _ => {
            // Only where the first argument is a KEY — `update`'s is a source
            // mapping, and hashing it would refuse `d.update({"a": [1]})`,
            // which is an ordinary program.
            if matches!(name, "get" | "pop" | "setdefault") {
                if let Some(a) = args.first() {
                    self_key_guard(a)?;
                }
            }
            crate::methods::dict_method(it, cell, name, args, kw)
        }
    }
}
