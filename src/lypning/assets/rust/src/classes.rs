//! Classes — the whole of the `cap-class` capability, compiled into `lypning-l`
//! only. Every line here, and every line that reaches it, is behind
//! `cfg(feature = "cap-class")`, so the frozen core gains no byte of it.
//!
//! **The invariant this file exists to hold: an instance never prints itself by
//! accident.** CPython's default `object.__repr__` is
//! `<__main__.C object at 0x104a2f350>` — a heap address, which no independent
//! runtime can reproduce and which changes between two runs of CPython itself.
//! So an instance whose class defines neither `__repr__` nor `__str__` is a
//! REFUSAL wherever text is asked of it (exit 90), never a guess. Everything
//! else in this file follows from that rule applied one path at a time.
//!
//! What the subset is: a class with no base or `object`, `__init__`, plain
//! methods, instance attributes, class attributes, and `__repr__`/`__str__`.
//! What it is not is decided in `parse::class_def`, STATICALLY, so
//! `lypning route` sees the refusal without running the program: multiple
//! inheritance, any base but `object` (`Exception` included — an exception
//! hierarchy is CPython's own), a metaclass or computed base, `__slots__` and
//! every other dunder attribute, `@property` and every decorated method (the
//! `decorator` refusal already owns those), a nested class, and any class-body
//! statement that is not a method, a plain attribute, `pass` or a docstring.
//! Two more are runtime refusals because no walk can see them: a class defined
//! inside a function (its `__qualname__` is `f.<locals>.C`, which the repr of
//! the class object would have to print) and an assignment to an attribute of a
//! class object after the definition.
//!
//! `super()` needs no refusal of its own: it is not a builtin, so it refuses as
//! `builtin: super` — the one bare name in the class grammar that would
//! otherwise have been a NameError at exit 1.

use crate::args::Args;
use crate::ast::{Params, Stmt};
use crate::err::{type_err, unsupported, R};
use crate::eval::{new_scope, Interp, Scope};
use crate::value::{FuncObj, Value};
use std::cell::RefCell;
use std::rc::Rc;

/// A class object. `dict` is the same `Scope` map a function frame uses: the
/// class body executes with it pushed on the chain, so `A = 1` then `B = A + 1`
/// in a class body resolves exactly as Python's class scope does, and what the
/// body left behind IS the class dict with no copy in between.
pub struct ClassObj {
    /// Interned, because `value::type_name` returns `&'static str` and a class
    /// name is the type name of every instance of it. [`intern`] is what makes
    /// that safe; see it for the bound on what it can leak.
    pub name: &'static str,
    pub dict: Scope,
}

/// An instance: its class, and its own attribute dict.
pub struct InstObj {
    pub class: Rc<ClassObj>,
    pub dict: Scope,
}

thread_local! {
    /// Class names handed to `type_name`, leaked once each. Bounded by the
    /// number of DISTINCT class names in one program's source, which the parser
    /// has already read — a class defined a million times in a loop leaks one
    /// string, not a million, which is the whole reason this is a table and not
    /// a bare `Box::leak`.
    static NAMES: RefCell<Vec<&'static str>> = const { RefCell::new(Vec::new()) };
}

fn intern(s: &str) -> &'static str {
    NAMES.with(|t| {
        let mut t = t.borrow_mut();
        if let Some(x) = t.iter().find(|x| **x == s) {
            return *x;
        }
        // `Box::<str>::from` and NOT `String::into_boxed_str`. The second one
        // shrinks the buffer, which reaches `RawVec::shrink` and the custom
        // allocator's `realloc` — a monomorphisation nothing else in this
        // binary needed. It was measured, once, at **56,032 bytes of `__text`**
        // for one leaked class name; this spelling allocates the exact length
        // and never reallocates.
        let leaked: &'static str = Box::leak(Box::<str>::from(s));
        t.push(leaked);
        leaked
    })
}

pub fn refuse(what: &str) -> crate::err::LypningError {
    unsupported("class", what)
}

// ---- defining ---------------------------------------------------------------

/// Execute a class body and build the class object.
///
/// Methods are built HERE rather than by `Stmt::Def`'s own arm, and the reason
/// is a silent wrong answer: `Stmt::Def` captures `self.chain` as the function's
/// closure, and the chain during a class body holds the class scope. A method
/// built that way would resolve a bare `n` in its body to the class attribute
/// `n` — where CPython raises `NameError`, because a class body is not a
/// closure for the methods defined in it. `env: Vec::new()` is that rule: a
/// method sees its own frame and the globals, and nothing between.
pub fn define(it: &mut Interp, name: &Rc<str>, body: &Rc<Vec<Stmt>>) -> R<Value> {
    // A class inside a function has `__qualname__` `f.<locals>.C`, which is
    // what `repr` of the class object prints. The chain is empty at module
    // level and only there, so this is the exact test.
    if !it.chain.is_empty() {
        return Err(refuse("a class defined inside a function"));
    }
    let scope = new_scope();
    it.chain.push(scope.clone());
    let mut err = None;
    for st in body.iter() {
        // The four statement shapes `parse::class_def` admits, executed HERE
        // rather than through `Interp::exec_block`. That is a size decision
        // with a number: calling `exec_block` from this file let the optimiser
        // inline the whole statement interpreter into it, and the variant was
        // 62,872 bytes bigger for four statement kinds it already knew.
        let r = match st {
            Stmt::Def {
                name: m,
                params,
                body: b,
            } => method(it, &scope, m, params, b),
            Stmt::Assign { targets, value } => match it.eval(value) {
                Ok(v) => {
                    if let Some(crate::ast::Target::Name(n)) = targets.first() {
                        scope.borrow_mut().insert(n.clone(), v);
                    }
                    Ok(())
                }
                Err(e) => Err(e),
            },
            // `pass` and the docstring.
            _ => Ok(()),
        };
        if let Err(e) = r {
            err = Some(e);
            break;
        }
    }
    it.chain.pop();
    match err {
        Some(e) => Err(e),
        None => Ok(Value::Class(Rc::new(ClassObj {
            name: intern(name),
            dict: scope,
        }))),
    }
}

fn method(
    it: &mut Interp,
    scope: &Scope,
    name: &Rc<str>,
    params: &Rc<Params>,
    body: &Rc<Vec<Stmt>>,
) -> R<()> {
    let mut defaults = Vec::with_capacity(params.defaults.len());
    for d in &params.defaults {
        // Defaults ARE evaluated in the class body's scope, unlike the body.
        defaults.push(match d {
            Some(e) => Some(it.eval(e)?),
            None => None,
        });
    }
    let f = Value::Func(Rc::new(FuncObj {
        name: name.clone(),
        params: params.clone(),
        body: body.clone(),
        defaults,
        lambda: None,
        env: Vec::new(),
        assigned: Rc::new(crate::eval::assigned_names(body, params)),
    }));
    scope.borrow_mut().insert(name.clone(), f);
    Ok(())
}

// ---- instances --------------------------------------------------------------

fn class_get(c: &Rc<ClassObj>, name: &str) -> Option<Value> {
    c.dict.borrow().get(name).cloned()
}

/// `self` in front of the arguments the caller wrote. A fresh [`Args`] rather
/// than an insert: `Args` has no shift-right, and the alternative — a method
/// whose `self` is bound through a synthesised closure instead — reports every
/// arity error one argument short of CPython's, which counts `self`.
/// The ONE re-entry into `Interp::call` in this file. Three call sites let the
/// optimiser paste a copy of the dispatcher into each; one gives it nothing to
/// copy.
fn invoke(it: &mut Interp, f: &Value, recv: Value, args: &mut Args, kw: Vec<(Rc<str>, Value)>) -> R<Value> {
    let mut a = with_self(recv, args);
    it.call(f, &mut a, kw)
}

fn with_self(recv: Value, args: &mut Args) -> Args {
    let n = args.len();
    let mut a = Args::with_capacity(n + 1);
    a.push(recv);
    for i in 0..n {
        a.push(args.take(i));
    }
    a
}

pub fn instantiate(
    it: &mut Interp,
    c: Rc<ClassObj>,
    args: &mut Args,
    kw: Vec<(Rc<str>, Value)>,
) -> R<Value> {
    let inst = Rc::new(InstObj {
        class: c.clone(),
        dict: new_scope(),
    });
    match class_get(&c, "__init__") {
        Some(f @ Value::Func(_)) => {
            let r = invoke(it, &f, Value::Obj(inst.clone()), args, kw)?;
            // CPython raises for a non-None return, and the message names the
            // type it got. A refusal instead would be a refusal AFTER the
            // constructor ran, which is the shape the barrier exists to avoid.
            if !matches!(r, Value::None) {
                return Err(type_err(format!(
                    "__init__() should return None, not '{}'",
                    crate::value::type_name(&r)
                )));
            }
        }
        _ => {
            if args.len() != 0 || !kw.is_empty() {
                return Err(type_err(format!("{}() takes no arguments", c.name)));
            }
        }
    }
    Ok(Value::Obj(inst))
}

pub fn call_method(
    it: &mut Interp,
    recv: &Rc<InstObj>,
    f: &Rc<FuncObj>,
    args: &mut Args,
    kw: Vec<(Rc<str>, Value)>,
) -> R<Value> {
    invoke(it, &Value::Func(f.clone()), Value::Obj(recv.clone()), args, kw)
}

// ---- attributes -------------------------------------------------------------

fn attr_err(what: &str, name: &str) -> crate::err::LypningError {
    crate::err::LypningError::exc(
        "AttributeError",
        format!("'{what}' object has no attribute '{name}'"),
    )
}

pub fn get_attr(base: &Value, name: &str) -> Option<R<Value>> {
    match base {
        Value::Obj(o) => Some(inst_attr(o, name)),
        Value::Class(c) => Some(match name {
            "__name__" => Ok(Value::Str(c.name.into())),
            // `C.__dict__`, `C.__bases__`, `C.__qualname__`, `C.__mro__`:
            // answered by CPython off `type`, so a refusal and not an
            // AttributeError, for the same reason as an instance's.
            _ if is_dunder(name) => Err(refuse(&format!(
                "{}.{name} — an attribute a class inherits from `type`",
                c.name
            ))),
            _ => match class_get(c, name) {
                Some(v) => Ok(v),
                // `C.x` for a name the class does not define is CPython's
                // AttributeError too, and its message names `type object`.
                None => Err(crate::err::LypningError::exc(
                    "AttributeError",
                    format!("type object '{}' has no attribute '{name}'", c.name),
                )),
            },
        }),
        // A bound method's own attributes (`__func__`, `__self__`, `__name__`)
        // are CPython's to answer; two of the three repr an address.
        Value::Meth(..) => Some(Err(refuse("an attribute of a bound method"))),
        _ => None,
    }
}

fn inst_attr(o: &Rc<InstObj>, name: &str) -> R<Value> {
    if let Some(v) = o.dict.borrow().get(name) {
        return Ok(v.clone());
    }
    if let Some(v) = class_get(&o.class, name) {
        return Ok(match v {
            Value::Func(f) => Value::Meth(o.clone(), f),
            other => other,
        });
    }
    match name {
        "__class__" => Ok(Value::Class(o.class.clone())),
        // EVERY OTHER DUNDER IS A REFUSAL, NOT AN AttributeError. `object`
        // gives an instance two dozen attributes for free — `__dict__`,
        // `__module__`, `__doc__`, `__hash__`, `__sizeof__` — and CPython
        // ANSWERS all of them. Raising here was exit 1 where CPython prints
        // `{'n': 1}`, which the chain never retries. A plain name that is
        // missing is CPython's AttributeError too, and stays one.
        // (`__dict__` in particular is the LIVE mapping there; a copy would
        // diverge the moment the program wrote through it.)
        _ if is_dunder(name) => Err(refuse(&format!(
            "{}.{name} — an attribute {} inherits from `object`",
            o.class.name, o.class.name
        ))),
        _ => Err(attr_err(o.class.name, name)),
    }
}

/// `__x__`. The same shape `parse::class_def` refuses to DEFINE; here it is the
/// shape that must not raise AttributeError, because `object` answers it.
fn is_dunder(n: &str) -> bool {
    n.len() > 4 && n.starts_with("__") && n.ends_with("__")
}

/// `x.attr = v`. `Some(Ok(()))` when this value owns the assignment.
pub fn set_attr(base: &Value, name: &Rc<str>, v: Value) -> Option<R<()>> {
    match base {
        Value::Obj(o) => {
            o.dict.borrow_mut().insert(name.clone(), v);
            Some(Ok(()))
        }
        // Mutating a class after its definition changes what every instance
        // already built reads back, including instances a method closed over.
        // It is answerable, and it is not answered: the walk that admits an
        // attribute NAME (`route::class_attrs`) reads the class body, and a
        // name written only here was never in it.
        Value::Class(_) => Some(Err(refuse("assignment to an attribute of a class"))),
        Value::Meth(..) => Some(Err(refuse("assignment to an attribute of a bound method"))),
        _ => None,
    }
}

// ---- text -------------------------------------------------------------------

/// `str(x)` / `repr(x)` for an instance, or `None` if `v` is not one.
///
/// The dispatch is here and not in `fmt`, because `fmt::to_str` and `fmt::repr`
/// are free functions and `__str__` is a method that needs the interpreter to
/// call it. Every `fmt` path that a value can reach WITHOUT passing through one
/// of this function's callers refuses instead — an instance inside a list, a
/// `%s`, a `.format()` argument — which is the conservative direction: a
/// refusal costs one CPython spawn, and the alternative is `<__main__.C object
/// at 0x…>` invented out of nothing.
pub fn text(it: &mut Interp, v: &Value, want_repr: bool) -> R<Option<Value>> {
    let Value::Obj(o) = v else { return Ok(None) };
    // `str()` falls back to `__repr__` when there is no `__str__`; `repr()`
    // never falls back to `__str__`.
    let f = class_get(&o.class, "__repr__");
    let f = if want_repr {
        f
    } else {
        class_get(&o.class, "__str__").or(f)
    };
    let Some(f @ Value::Func(_)) = f else {
        return Err(refuse(&format!(
            "{}() of an instance of {} — it defines no {}, and CPython's default \
             prints the object's address",
            if want_repr { "repr" } else { "str" },
            o.class.name,
            if want_repr { "__repr__" } else { "__str__ or __repr__" },
        )));
    };
    let out = invoke(it, &f, v.clone(), &mut Args::new(), Vec::new())?;
    match out {
        Value::Str(_) => Ok(Some(out)),
        other => Err(type_err(format!(
            "__{}__ returned non-string (type {})",
            if want_repr { "repr" } else { "str" },
            crate::value::type_name(&other)
        ))),
    }
}

/// The builtins that render their argument: `print(x)`, `str(x)`, `repr(x)`,
/// `format(x)`. Called once at the head of `call_builtin`, so the instance is
/// already a `Value::Str` by the time `fmt` sees it.
///
/// `format(x, spec)` with a NON-EMPTY spec is deliberately left alone: CPython
/// runs `object.__format__`, which raises for any spec at all, so passing the
/// rendered string here would have padded where CPython raises.
pub fn render_args(
    it: &mut Interp,
    name: &str,
    args: &mut Args,
    kw: &[(Rc<str>, Value)],
) -> R<()> {
    let n = match name {
        "print" => args.len(),
        // ONE argument only. `str(x, 'utf-8')` is a DECODE, and CPython raises
        // `decoding to str: need a bytes-like object, R found` for anything
        // that is not bytes — so rendering there answered the instance's text
        // at exit 0 where CPython exits 1. The same for `str(x, errors=…)`.
        "str" if args.len() == 1 && kw.is_empty() => 1,
        // `format(x, spec)` is `type(x).__format__(x, spec)`, which for an
        // instance is `object.__format__`: the str for an EMPTY spec, and a
        // TypeError for any other. So an empty spec renders and a non-empty
        // one is left for `fmt` to refuse.
        "format" if empty_spec(args) => 1,
        // `repr` is NOT here: the string `__repr__` returned IS the answer,
        // and handing it back as an argument would send it through
        // `fmt::repr` a second time — `repr(R())` printed `'R(7)'`, quotes
        // and all. `builtins::call_builtin`'s `"repr"` arm calls [`text`]
        // directly and returns.
        _ => return Ok(()),
    };
    for i in 0..n.min(args.len()) {
        if let Some(s) = text(it, &args[i], false)? {
            args.set(i, s);
        }
    }
    Ok(())
}

/// Is `format(x, spec)`'s spec absent or `""`?
fn empty_spec(args: &Args) -> bool {
    match args.get(1) {
        None => true,
        Some(Value::Str(s)) => s.is_empty(),
        Some(_) => false,
    }
}

/// One replacement field of an f-string or of `str.format`.
///
/// `{x!r}` and `{x!s}` convert FIRST and then apply the spec to the resulting
/// string, so `f"{x!r:>10}"` is padded and answered. `{x}` with a spec is
/// `object.__format__(x, spec)`, which raises in CPython for any non-empty
/// spec — so it is left for `fmt` to refuse.
pub fn render_field(it: &mut Interp, v: &Value, conv: Option<char>, spec: &str) -> R<Option<Value>> {
    match conv {
        Some('r') => text(it, v, true),
        Some('s') => text(it, v, false),
        None if spec.is_empty() => text(it, v, false),
        _ => Ok(None),
    }
}

// ---- the builtins that ask about a type -------------------------------------

/// `isinstance(v, cls)` when a class object is involved; `None` to leave the
/// builtin's own table to answer.
pub fn isinstance(v: &Value, cls: &Value) -> R<Option<bool>> {
    let mut any_class = false;
    let mut hit = false;
    let mut other = false;
    let mut one = |c: &Value| match c {
        Value::Class(k) => {
            any_class = true;
            if let Value::Obj(o) = v {
                hit |= Rc::ptr_eq(&o.class, k);
            }
        }
        _ => other = true,
    };
    match cls {
        Value::Tuple(t) => t.iter().for_each(&mut one),
        c => one(c),
    }
    if !any_class {
        return Ok(None);
    }
    if hit {
        return Ok(Some(true));
    }
    // An instance is of no builtin type, and no builtin value is an instance of
    // a user class, so either half alone is a complete answer.
    if matches!(v, Value::Obj(_)) || !other {
        return Ok(Some(false));
    }
    Err(refuse(
        "isinstance() against a tuple mixing a class with a builtin type",
    ))
}
