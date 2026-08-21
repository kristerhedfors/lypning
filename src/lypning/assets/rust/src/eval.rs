//! The tree-walking evaluator.
//!
//! Scoping is real, not approximated: a function carries the scope chain it was
//! defined in, and the set of names it assigns anywhere in its body. That
//! second piece is what makes `UnboundLocalError` come out right instead of
//! silently finding a global of the same name — the exact shape of "plausible
//! wrong answer" this runtime exists to avoid.

use crate::args::Args;
use crate::ast::*;
use crate::err::*;
use crate::fmt;
use crate::modules;
use crate::value::*;
use std::cell::RefCell;
use crate::hash::{Map, Set as FastSet};
use std::rc::Rc;

pub type Scope = Rc<RefCell<Map<Rc<str>, Value>>>;

pub fn new_scope() -> Scope {
    Rc::new(RefCell::new(crate::hash::map()))
}

pub enum Flow {
    Normal,
    Break,
    Continue,
    Return(Value),
}

/// Recursion guard. CPython's default limit is 1000 frames; ours is lower
/// because a Rust stack frame for `eval` is fat, and a stack overflow in a
/// runtime with `panic = "abort"` is an unrecoverable crash rather than a
/// RecursionError. Hitting it is `unsupported`, so the program is retried on an
/// interpreter with a real limit instead of dying here.
const MAX_DEPTH: usize = 180;

/// How deep one expression may be evaluated. See [`Interp::eval`].
///
/// Sized against `MAX_DEPTH` above rather than against the stack alone: a legal
/// recursive function at 179 frames spends about three `eval` levels per frame,
/// so anything below ~540 would refuse programs this runtime already runs. 600
/// clears that and is comfortable on a 1 MB host thread — measured, on a
/// `threading.stack_size(1 << 20)` thread, against the deepest recursion
/// `MAX_DEPTH` allows.
const MAX_EXPR_DEPTH: u32 = 600;

pub struct Interp {
    pub globals: Scope,
    /// Innermost-last scope chain for the function currently executing.
    pub chain: Vec<Scope>,
    /// Names declared `global` in the function currently executing.
    global_decls: Vec<FastSet<Rc<str>>>,
    /// Names assigned somewhere in the current function body.
    assigned: Vec<Rc<FastSet<Rc<str>>>>,
    pub modules: Map<Rc<str>, Value>,
    depth: usize,
    /// Statements executed and loop iterations taken, against the host's
    /// budget. Two plain fields rather than a thread_local read per statement:
    /// this is the hottest branch in the interpreter, and it must cost a
    /// register compare or it is not affordable at all.
    steps: u64,
    step_limit: u64,
    /// How deep `eval` currently is, against [`MAX_EXPR_DEPTH`].
    expr_depth: u32,
    /// Spent scope-chain vectors, kept to be filled again. See
    /// `call_func_inner`; capped at [`CHAIN_POOL_MAX`] so a deep recursion
    /// cannot leave the pool holding its whole depth for the rest of the run.
    chain_pool: Vec<Vec<Scope>>,
}

/// How many scope-chain vectors to keep. Above the depth of any recursion this
/// runtime allows to finish, and small enough to be nothing.
const CHAIN_POOL_MAX: usize = 64;

impl Interp {
    pub fn new() -> Self {
        Interp {
            globals: new_scope(),
            chain: Vec::new(),
            global_decls: Vec::new(),
            assigned: Vec::new(),
            modules: crate::hash::map(),
            depth: 0,
            steps: 0,
            expr_depth: 0,
            chain_pool: Vec::new(),
            // Read once, here, rather than per statement. Zero — the CLI's
            // value, since a process can simply be killed — compiles the check
            // down to a comparison that is never true.
            step_limit: crate::host::step_limit(),
        }
    }

    // ---- names ------------------------------------------------------------

    pub fn lookup(&self, name: &str) -> R<Value> {
        for s in self.chain.iter().rev() {
            if let Some(v) = s.borrow().get(name) {
                return Ok(v.clone());
            }
        }
        if let Some(f) = self.assigned.last() {
            // Assigned somewhere in this function but not bound yet.
            if f.contains(name) && !self.global_decls.last().is_some_and(|g| g.contains(name)) {
                return Err(LypningError::exc(
                    "UnboundLocalError",
                    format!("cannot access local variable '{name}' where it is not associated with a value"),
                ));
            }
        }
        if let Some(v) = self.globals.borrow().get(name) {
            return Ok(v.clone());
        }
        if let Some(v) = crate::builtins::builtin(name) {
            return Ok(v);
        }
        Err(name_err(name))
    }

    pub fn bind(&mut self, name: &Rc<str>, v: Value) {
        if self
            .global_decls
            .last()
            .is_some_and(|g| g.contains(name.as_ref()))
        {
            self.globals.borrow_mut().insert(name.clone(), v);
            return;
        }
        match self.chain.last() {
            Some(s) => {
                s.borrow_mut().insert(name.clone(), v);
            }
            None => {
                self.globals.borrow_mut().insert(name.clone(), v);
            }
        }
    }

    // ---- statements -------------------------------------------------------

    pub fn run(&mut self, body: &[Stmt]) -> R<()> {
        match self.exec_block(body)? {
            Flow::Normal => Ok(()),
            _ => Err(LypningError::syntax(0, "'return'/'break' outside a block")),
        }
    }

    pub fn exec_block(&mut self, body: &[Stmt]) -> R<Flow> {
        for s in body {
            match self.exec(s)? {
                Flow::Normal => {}
                other => return Ok(other),
            }
        }
        Ok(Flow::Normal)
    }

    /// One unit of progress against the host's budget.
    ///
    /// Called from the two places an unbounded program must pass through: every
    /// statement, and every advance of every iterator. The second is not
    /// redundant — `sum(range(10**18))` is ONE statement, and a bound that only
    /// counted statements would let it run forever inside a host that asked for
    /// a bound.
    #[inline(always)]
    pub(crate) fn tick(&mut self) -> R<()> {
        if self.step_limit == 0 {
            return Ok(());
        }
        self.steps += 1;
        if self.steps > self.step_limit {
            return Err(unsupported(
                "steps",
                &format!("still running after {} steps", self.step_limit),
            ));
        }
        Ok(())
    }

    fn exec(&mut self, s: &Stmt) -> R<Flow> {
        self.tick()?;
        match s {
            Stmt::Pass => {}
            Stmt::Expr(e) => {
                self.eval(e)?;
            }
            Stmt::Assign { targets, value } => {
                let v = self.eval(value)?;
                for t in targets {
                    self.assign(t, v.clone())?;
                }
            }
            Stmt::AugAssign { target, op, value } => {
                let rhs = self.eval(value)?;
                let cur = self.read_target(target)?;
                // `list += iterable` mutates in place, and the difference is
                // observable through any other name bound to the same list.
                if let (BinOp::Add, Value::List(l)) = (op, &cur) {
                    let extra = self.iter_collect(rhs)?;
                    l.borrow_mut().extend(extra);
                    return Ok(Flow::Normal);
                }
                let nv = self.binop(*op, &cur, &rhs)?;
                self.assign(target, nv)?;
            }
            Stmt::If { arms, els } => {
                for (c, body) in arms {
                    let cv = self.eval(c)?;
                    if truthy(&cv)? {
                        return self.exec_block(body);
                    }
                }
                return self.exec_block(els);
            }
            Stmt::While { cond, body, els } => {
                loop {
                    let cv = self.eval(cond)?;
                    if !truthy(&cv)? {
                        break;
                    }
                    match self.exec_block(body)? {
                        Flow::Break => return Ok(Flow::Normal),
                        Flow::Return(v) => return Ok(Flow::Return(v)),
                        _ => {}
                    }
                }
                return self.exec_block(els);
            }
            Stmt::For {
                target,
                iter,
                body,
                els,
            } => {
                let it = self.eval(iter)?;
                let mut it = self.make_iter(it)?;
                loop {
                    let Some(v) = self.iter_next(&mut it)? else {
                        break;
                    };
                    self.assign(target, v)?;
                    match self.exec_block(body)? {
                        Flow::Break => return Ok(Flow::Normal),
                        Flow::Return(v) => return Ok(Flow::Return(v)),
                        _ => {}
                    }
                }
                return self.exec_block(els);
            }
            Stmt::Break => return Ok(Flow::Break),
            Stmt::Continue => return Ok(Flow::Continue),
            Stmt::Return(e) => {
                let v = match e {
                    Some(e) => self.eval(e)?,
                    None => Value::None,
                };
                return Ok(Flow::Return(v));
            }
            Stmt::Assert { test, msg } => {
                let t = self.eval(test)?;
                if !truthy(&t)? {
                    let m = match msg {
                        Some(m) => fmt::to_str(&self.eval(m)?)?,
                        None => String::new(),
                    };
                    return Err(LypningError::exc("AssertionError", m));
                }
            }
            Stmt::Raise { exc } => {
                let e = match exc {
                    None => return Err(LypningError::exc("RuntimeError", "No active exception to reraise")),
                    Some(e) => self.eval(e)?,
                };
                return Err(match e {
                    Value::Exc(k, m) => LypningError::Exc(Exc {
                        kind: k,
                        msg: m.to_string(),
                    }),
                    Value::Builtin(name) if crate::builtins::is_exception_name(name) => {
                        LypningError::Exc(Exc {
                            kind: crate::builtins::exception_static(name),
                            msg: String::new(),
                        })
                    }
                    other => {
                        return Err(type_err(format!(
                            "exceptions must derive from BaseException, not {}",
                            type_name(&other)
                        )))
                    }
                });
            }
            Stmt::Def { name, params, body } => {
                let mut defaults = Vec::with_capacity(params.defaults.len());
                for d in &params.defaults {
                    defaults.push(match d {
                        Some(e) => Some(self.eval(e)?),
                        None => None,
                    });
                }
                let f = Value::Func(Rc::new(FuncObj {
                    name: name.clone(),
                    params: params.clone(),
                    body: body.clone(),
                    defaults,
                    lambda: None,
                    env: self.chain.clone(),
                    assigned: Rc::new(assigned_names(body, params)),
                }));
                self.bind(name, f);
            }
            Stmt::Global(names) => {
                if let Some(g) = self.global_decls.last_mut() {
                    for n in names {
                        g.insert(n.clone());
                    }
                }
            }
            Stmt::Del(targets) => {
                for t in targets {
                    match t {
                        Target::Name(n) => {
                            let removed = match self.chain.last() {
                                Some(s) => s.borrow_mut().remove(n.as_ref()).is_some(),
                                None => self.globals.borrow_mut().remove(n.as_ref()).is_some(),
                            };
                            if !removed {
                                return Err(name_err(n));
                            }
                        }
                        Target::Index(base, idx) => {
                            let b = self.eval(base)?;
                            let i = self.eval(idx)?;
                            self.del_item(&b, &i)?;
                        }
                        _ => return Err(unsupported("del", "del of this target form")),
                    }
                }
            }
            Stmt::Try {
                body,
                handlers,
                els,
                finally,
            } => {
                let r = self.exec_block(body);
                let out = match r {
                    Ok(flow) => {
                        let e = self.exec_block(els);
                        match e {
                            Ok(Flow::Normal) => Ok(flow),
                            other => other,
                        }
                    }
                    Err(err) => {
                        // `unsupported` is a runtime capability gap, not a
                        // Python exception: catching it with `except Exception`
                        // would turn a routing signal into a wrong answer.
                        if err.is_unsupported() || matches!(err, LypningError::Exit(_)) {
                            Err(err)
                        } else {
                            let kind = err_kind(&err);
                            let mut handled = None;
                            for h in handlers {
                                if h.kinds.is_empty()
                                    || h.kinds.iter().any(|k| exc_matches(k, kind))
                                {
                                    if let Some(n) = &h.name {
                                        let v = Value::Exc(kind, err_msg(&err).into());
                                        self.bind(n, v);
                                    }
                                    handled = Some(self.exec_block(&h.body));
                                    break;
                                }
                            }
                            match handled {
                                Some(r) => r,
                                None => Err(err),
                            }
                        }
                    }
                };
                if !finally.is_empty() {
                    match self.exec_block(finally)? {
                        Flow::Normal => {}
                        other => {
                            // A `break` or `return` in `finally` DISCARDS an
                            // in-flight exception — that is CPython's rule and
                            // it is kept. It must not discard a REFUSAL:
                            // `unsupported` is not the program's exception, it
                            // is this runtime saying it cannot run the program,
                            // and swallowing it hands the caller a wrong answer
                            // at exit 0 instead of routing onward to CPython.
                            // Same reasoning as the `except` arm above.
                            if out.as_ref().err().is_some_and(|e| e.is_unsupported()) {
                                return out;
                            }
                            return Ok(other);
                        }
                    }
                }
                return out;
            }
            Stmt::With { items, body } => {
                let mut opened = Vec::new();
                for (ctx, alias) in items {
                    let v = self.eval(ctx)?;
                    match &v {
                        Value::File(_) => {}
                        other => {
                            return Err(unsupported(
                                "with",
                                &format!("context manager of type {}", type_name(other)),
                            ))
                        }
                    }
                    if let Some(t) = alias {
                        self.assign(t, v.clone())?;
                    }
                    opened.push(v);
                }
                let r = self.exec_block(body);
                for v in opened {
                    if let Value::File(f) = v {
                        f.borrow_mut().closed = true;
                    }
                }
                return r;
            }
            Stmt::Import { names } => {
                for (path, bind) in names {
                    let m = modules::import(path)?;
                    // `import os.path` binds `os`, but `os.path` must resolve.
                    if path.contains('.') && bind.as_ref() == path.split('.').next().unwrap() {
                        modules::import(path.split('.').next().unwrap())?;
                        let root = modules::import(path.split('.').next().unwrap())?;
                        self.bind(bind, root);
                    } else {
                        self.bind(bind, m);
                    }
                }
            }
            Stmt::FromImport { module, names } => {
                let m = modules::import(module)?;
                for (n, bind) in names {
                    let v = modules::get_attr(&m, n)?;
                    self.bind(bind, v);
                }
            }
        }
        Ok(Flow::Normal)
    }

    // ---- assignment -------------------------------------------------------

    fn read_target(&mut self, t: &Target) -> R<Value> {
        Ok(match t {
            Target::Name(n) => self.lookup(n)?,
            Target::Attr(b, n) => {
                let bv = self.eval(b)?;
                self.get_attr(&bv, n)?
            }
            Target::Index(b, i) => {
                let bv = self.eval(b)?;
                let iv = self.eval(i)?;
                self.index(&bv, &iv)?
            }
            _ => return Err(unsupported("augassign", "augmented assignment to a slice")),
        })
    }

    pub fn assign(&mut self, t: &Target, v: Value) -> R<()> {
        match t {
            Target::Name(n) => self.bind(n, v),
            Target::Tuple(parts) => {
                let star_at = parts.iter().position(|p| matches!(p, Target::Star(_)));
                let items = self.iter_collect(v)?;
                match star_at {
                    None => {
                        if items.len() != parts.len() {
                            return Err(value_err(if items.len() < parts.len() {
                                format!(
                                    "not enough values to unpack (expected {}, got {})",
                                    parts.len(),
                                    items.len()
                                )
                            } else {
                                format!("too many values to unpack (expected {})", parts.len())
                            }));
                        }
                        for (p, x) in parts.iter().zip(items) {
                            self.assign(p, x)?;
                        }
                    }
                    Some(k) => {
                        let after = parts.len() - k - 1;
                        if items.len() < parts.len() - 1 {
                            return Err(value_err(format!(
                                "not enough values to unpack (expected at least {}, got {})",
                                parts.len() - 1,
                                items.len()
                            )));
                        }
                        for (i, p) in parts[..k].iter().enumerate() {
                            self.assign(p, items[i].clone())?;
                        }
                        let mid = items[k..items.len() - after].to_vec();
                        if let Target::Star(inner) = &parts[k] {
                            self.assign(inner, list(mid))?;
                        }
                        for (i, p) in parts[k + 1..].iter().enumerate() {
                            self.assign(p, items[items.len() - after + i].clone())?;
                        }
                    }
                }
            }
            Target::Index(b, i) => {
                let bv = self.eval(b)?;
                let iv = self.eval(i)?;
                self.set_item(&bv, iv, v)?;
            }
            Target::Attr(b, n) => {
                let bv = self.eval(b)?;
                return Err(unsupported(
                    "setattr",
                    &format!("assignment to .{n} on a {}", type_name(&bv)),
                ));
            }
            Target::Slice { base, lo, hi } => {
                let bv = self.eval(base)?;
                let Value::List(l) = &bv else {
                    return Err(type_err(format!(
                        "'{}' object does not support slice assignment",
                        type_name(&bv)
                    )));
                };
                let n = l.borrow().len() as i64;
                let lo = match lo {
                    Some(e) => {
                        let x = self.eval(e)?;
                        clamp_index(int_val(&x)?, n)
                    }
                    None => 0,
                };
                let hi = match hi {
                    Some(e) => {
                        let x = self.eval(e)?;
                        clamp_index(int_val(&x)?, n)
                    }
                    None => n,
                };
                let repl = self.iter_collect(v)?;
                let (lo, hi) = (lo as usize, hi.max(lo) as usize);
                l.borrow_mut().splice(lo..hi, repl);
            }
            Target::Star(_) => {
                return Err(LypningError::syntax(0, "starred assignment target must be in a list or tuple"))
            }
        }
        Ok(())
    }

    // ---- expressions ------------------------------------------------------

    /// Evaluate one expression, one level deeper.
    ///
    /// `parse::MAX_PARSE_DEPTH` bounds how deeply the grammar NESTS, which is
    /// not the same thing: `1+1+1+…` is parsed by an iterative loop into a
    /// left-leaning tree whose spine is one node per term, so a flat chain of
    /// two thousand terms parses fine and then recurses two thousand deep here.
    /// On a host thread with a 1 MB stack that is a SIGSEGV in the caller's
    /// process — and a stack overflow is not an unwind, so nothing at the ABI
    /// boundary can catch it.
    ///
    /// A plain field rather than the thread_local `err::Nest`: this is the
    /// hottest function in the interpreter and the check has to compile to a
    /// register compare.
    pub fn eval(&mut self, e: &Expr) -> R<Value> {
        self.expr_depth += 1;
        if self.expr_depth > MAX_EXPR_DEPTH {
            self.expr_depth -= 1;
            return Err(unsupported(
                "recursion",
                &format!("expression evaluation nested deeper than {MAX_EXPR_DEPTH}"),
            ));
        }
        let r = self.eval_inner(e);
        self.expr_depth -= 1;
        r
    }

    fn eval_inner(&mut self, e: &Expr) -> R<Value> {
        Ok(match e {
            Expr::None => Value::None,
            Expr::True => Value::Bool(true),
            Expr::False => Value::Bool(false),
            Expr::Int(i) => Value::Int(*i),
            Expr::Float(f) => Value::Float(*f),
            Expr::Str(s) => Value::Str(s.clone()),
            Expr::Bytes(b) => Value::Bytes(b.clone()),
            Expr::Name(n) => self.lookup(n)?,
            // A bare `*x` in an expression position has no value; the parser
            // only produces it where a target list is possible.
            Expr::Starred(_) => {
                return Err(LypningError::syntax(
                    0,
                    "can't use starred expression here",
                ))
            }
            Expr::Tuple(items) => {
                let mut v = Vec::with_capacity(items.len());
                for x in items {
                    v.push(self.eval(x)?);
                }
                Value::Tuple(Rc::new(v))
            }
            Expr::List(items) => {
                let mut v = Vec::with_capacity(items.len());
                for x in items {
                    v.push(self.eval(x)?);
                }
                list(v)
            }
            Expr::Set(items) => {
                let mut s = Set::new();
                for x in items {
                    let v = self.eval(x)?;
                    s.add(v)?;
                }
                Value::Set(Rc::new(RefCell::new(s)))
            }
            Expr::Dict(pairs) => {
                let mut d = Dict::new();
                for (k, v) in pairs {
                    let kv = self.eval(k)?;
                    let vv = self.eval(v)?;
                    d.insert(kv, vv)?;
                }
                Value::Dict(Rc::new(RefCell::new(d)))
            }
            Expr::DictUnpack(items) => {
                let mut d = Dict::new();
                for it in items {
                    match it {
                        DictItem::Pair(k, v) => {
                            let kv = self.eval(k)?;
                            let vv = self.eval(v)?;
                            d.insert(kv, vv)?;
                        }
                        DictItem::Unpack(e) => {
                            let v = self.eval(e)?;
                            let Value::Dict(src) = &v else {
                                return Err(type_err(format!(
                                    "argument of type '{}' is not a mapping",
                                    type_name(&v)
                                )));
                            };
                            let pairs: Vec<(Value, Value)> = src
                                .borrow()
                                .iter()
                                .map(|(k, v)| (k.clone(), v.clone()))
                                .collect();
                            for (k, v) in pairs {
                                d.insert(k, v)?;
                            }
                        }
                    }
                }
                Value::Dict(Rc::new(RefCell::new(d)))
            }
            Expr::Bin(op, a, b) => {
                let av = self.eval(a)?;
                let bv = self.eval(b)?;
                self.binop(*op, &av, &bv)?
            }
            Expr::Un(op, a) => {
                let v = self.eval(a)?;
                match op {
                    UnOp::Not => Value::Bool(!truthy(&v)?),
                    UnOp::Neg => match v {
                        Value::Int(i) => Value::Int(
                            i.checked_neg()
                                .ok_or_else(|| unsupported("bigint", "integer negation overflow"))?,
                        ),
                        Value::Bool(b) => Value::Int(-(b as i64)),
                        Value::Float(f) => Value::Float(-f),
                        other => {
                            return Err(type_err(format!(
                                "bad operand type for unary -: '{}'",
                                type_name(&other)
                            )))
                        }
                    },
                    UnOp::Pos => match v {
                        Value::Bool(b) => Value::Int(b as i64),
                        v @ (Value::Int(_) | Value::Float(_)) => v,
                        other => {
                            return Err(type_err(format!(
                                "bad operand type for unary +: '{}'",
                                type_name(&other)
                            )))
                        }
                    },
                    UnOp::Invert => match v {
                        Value::Int(i) => Value::Int(!i),
                        Value::Bool(b) => Value::Int(!(b as i64)),
                        other => {
                            return Err(type_err(format!(
                                "bad operand type for unary ~: '{}'",
                                type_name(&other)
                            )))
                        }
                    },
                }
            }
            Expr::Compare { first, rest } => {
                // Each operand is evaluated at most once, and the chain
                // short-circuits — both are guaranteed by Python.
                let mut left = self.eval(first)?;
                for (op, rhs) in rest {
                    let right = self.eval(rhs)?;
                    if !self.compare(*op, &left, &right)? {
                        return Ok(Value::Bool(false));
                    }
                    left = right;
                }
                Value::Bool(true)
            }
            Expr::BoolAnd(items) => {
                let mut last = Value::Bool(true);
                for x in items {
                    last = self.eval(x)?;
                    if !truthy(&last)? {
                        return Ok(last);
                    }
                }
                last
            }
            Expr::BoolOr(items) => {
                let mut last = Value::Bool(false);
                for x in items {
                    last = self.eval(x)?;
                    if truthy(&last)? {
                        return Ok(last);
                    }
                }
                last
            }
            Expr::Cond { cond, then, els } => {
                let c = self.eval(cond)?;
                if truthy(&c)? {
                    self.eval(then)?
                } else {
                    self.eval(els)?
                }
            }
            Expr::Attr(b, n) => {
                let bv = self.eval(b)?;
                self.get_attr(&bv, n)?
            }
            Expr::Index(b, i) => {
                let bv = self.eval(b)?;
                let iv = self.eval(i)?;
                self.index(&bv, &iv)?
            }
            Expr::Slice {
                base,
                lo,
                hi,
                step,
            } => {
                let bv = self.eval(base)?;
                let lo = match lo {
                    Some(e) => Some(self.eval(e)?),
                    None => None,
                };
                let hi = match hi {
                    Some(e) => Some(self.eval(e)?),
                    None => None,
                };
                let st = match step {
                    Some(e) => Some(self.eval(e)?),
                    None => None,
                };
                self.slice(&bv, lo, hi, st)?
            }
            Expr::FString(parts) => {
                let mut out = String::new();
                for p in parts {
                    match p {
                        FPart::Lit(s) => out.push_str(s),
                        FPart::Expr { expr, conv, spec } => {
                            let v = self.eval(expr)?;
                            let spec_s = match spec {
                                Some(s) => fmt::to_str(&self.eval(s)?)?,
                                None => String::new(),
                            };
                            let s = match conv {
                                Some('r') => fmt::repr(&v)?,
                                Some('s') => fmt::to_str(&v)?,
                                Some('a') => {
                                    return Err(unsupported("fstring", "!a conversion"))
                                }
                                Some(c) => {
                                    return Err(value_err(format!(
                                        "Invalid conversion character '{c}'"
                                    )))
                                }
                                None => {
                                    return {
                                        out.push_str(&fmt::format_value(&v, &spec_s)?);
                                        continue;
                                    }
                                }
                            };
                            out.push_str(&fmt::format_value(&Value::Str(s.into()), &spec_s)?);
                        }
                    }
                }
                Value::Str(out.into())
            }
            Expr::Lambda { params, body } => {
                let mut defaults = Vec::with_capacity(params.defaults.len());
                for d in &params.defaults {
                    defaults.push(match d {
                        Some(e) => Some(self.eval(e)?),
                        None => None,
                    });
                }
                Value::Func(Rc::new(FuncObj {
                    name: "<lambda>".into(),
                    params: params.clone(),
                    body: Rc::new(Vec::new()),
                    defaults,
                    lambda: Some(Rc::new((**body).clone())),
                    env: self.chain.clone(),
                    assigned: Rc::new(crate::hash::set()),
                }))
            }
            Expr::Comp {
                kind,
                elt,
                val,
                clauses,
            } => match kind {
                CompKind::Gen => Value::Gen(Rc::new(RefCell::new(crate::iter::GenState::new(
                    clauses.clone(),
                    (**elt).clone(),
                    self.chain.clone(),
                )))),
                _ => self.eval_comp(*kind, elt, val.as_deref(), clauses)?,
            },
            Expr::Call {
                func,
                args,
                star,
                kwargs,
                dstar,
            } => {
                // A method call is the commonest call an agent types — a
                // `.foo()` is in most corpus programs — and routing one through
                // `get_attr` builds a `Value::Bound`, which heap-allocates an
                // `Rc<Value>` for the receiver purely to hand it to
                // `call_method` a few lines later and drop it again. When the
                // base really has the method, call it directly and never build
                // the bound object.
                //
                // Everything else still goes through `get_attr`, including a
                // module attribute and an UNBOUND method (`str.upper`), because
                // both mean something different and `get_attr` is where that
                // difference is decided.
                let mut method: Option<(Value, &'static str)> = None;
                let mut f = Value::None;
                match &**func {
                    Expr::Attr(b, n) => {
                        let bv = self.eval(b)?;
                        match crate::methods::method_name(&bv, n) {
                            Some(m) if !matches!(bv, Value::Module(_)) => {
                                method = Some((bv, m));
                            }
                            _ => f = self.get_attr(&bv, n)?,
                        }
                    }
                    _ => f = self.eval(func)?,
                }
                let mut a = Args::with_capacity(args.len());
                for (i, x) in args.iter().enumerate() {
                    let v = self.eval(x)?;
                    if star.contains(&i) {
                        a.extend(self.iter_collect(v)?);
                    } else {
                        a.push(v);
                    }
                }
                let mut kw: Vec<(Rc<str>, Value)> = Vec::with_capacity(kwargs.len());
                for (n, x) in kwargs {
                    kw.push((n.clone(), self.eval(x)?));
                }
                for d in dstar {
                    let v = self.eval(d)?;
                    let Value::Dict(m) = &v else {
                        return Err(type_err("argument after ** must be a mapping"));
                    };
                    let pairs: Vec<(Value, Value)> =
                        m.borrow().iter().map(|(k, v)| (k.clone(), v.clone())).collect();
                    for (k, v) in pairs {
                        let Value::Str(ks) = k else {
                            return Err(type_err("keywords must be strings"));
                        };
                        kw.push((ks, v));
                    }
                }
                match method {
                    Some((recv, m)) => crate::methods::call_method(self, &recv, m, &mut a, kw)?,
                    None => self.call(&f, &mut a, kw)?,
                }
            }
        })
    }

    fn eval_comp(
        &mut self,
        kind: CompKind,
        elt: &Expr,
        val: Option<&Expr>,
        clauses: &[CompClause],
    ) -> R<Value> {
        // Python gives a comprehension its own scope, so the loop variable does
        // not leak. Push one, and pop it on every exit path.
        self.chain.push(new_scope());
        let r = self.comp_loop(kind, elt, val, clauses, 0);
        self.chain.pop();
        r
    }

    fn comp_loop(
        &mut self,
        kind: CompKind,
        elt: &Expr,
        val: Option<&Expr>,
        clauses: &[CompClause],
        _d: usize,
    ) -> R<Value> {
        let mut items: Vec<Value> = Vec::new();
        let mut dict = Dict::new();
        let mut set = Set::new();
        let mut stack: Vec<IterState> = Vec::new();
        let it0 = self.eval(&clauses[0].iter)?;
        stack.push(IterState {
            it: self.make_iter(it0)?,
        });
        'outer: while !stack.is_empty() {
            let level = stack.len() - 1;
            let mut st = stack.pop().unwrap();
            let next = self.iter_next(&mut st.it)?;
            let Some(v) = next else {
                continue;
            };
            stack.push(st);
            self.assign(&clauses[level].target, v)?;
            let mut ok = true;
            for cond in &clauses[level].ifs {
                let c = self.eval(cond)?;
                if !truthy(&c)? {
                    ok = false;
                    break;
                }
            }
            if !ok {
                continue 'outer;
            }
            if level + 1 < clauses.len() {
                let iv = self.eval(&clauses[level + 1].iter)?;
                let it = self.make_iter(iv)?;
                stack.push(IterState { it });
                continue;
            }
            match kind {
                CompKind::List => items.push(self.eval(elt)?),
                CompKind::Set => {
                    let v = self.eval(elt)?;
                    set.add(v)?;
                }
                CompKind::Dict => {
                    let k = self.eval(elt)?;
                    let v = self.eval(val.unwrap())?;
                    dict.insert(k, v)?;
                }
                CompKind::Gen => unreachable!(),
            }
        }
        Ok(match kind {
            CompKind::List => list(items),
            CompKind::Set => Value::Set(Rc::new(RefCell::new(set))),
            CompKind::Dict => Value::Dict(Rc::new(RefCell::new(dict))),
            CompKind::Gen => unreachable!(),
        })
    }

    // ---- calls ------------------------------------------------------------

    pub fn call(&mut self, f: &Value, args: &mut Args, kw: Vec<(Rc<str>, Value)>) -> R<Value> {
        match f {
            Value::Builtin(name) => crate::builtins::call_builtin(self, name, args, kw),
            Value::Bound(recv, name) => crate::methods::call_method(self, recv, name, args, kw),
            Value::Func(func) => self.call_func(func.clone(), args, kw),
            other => Err(type_err(format!(
                "'{}' object is not callable",
                type_name(other)
            ))),
        }
    }

    fn call_func(&mut self, f: Rc<FuncObj>, args: &mut Args, kw: Vec<(Rc<str>, Value)>) -> R<Value> {
        self.depth += 1;
        if self.depth > MAX_DEPTH {
            self.depth -= 1;
            return Err(unsupported(
                "recursion",
                &format!("call depth beyond {MAX_DEPTH}"),
            ));
        }
        let r = self.call_func_inner(f, args, kw);
        self.depth -= 1;
        r
    }

    fn call_func_inner(
        &mut self,
        f: Rc<FuncObj>,
        args: &mut Args,
        kw: Vec<(Rc<str>, Value)>,
    ) -> R<Value> {
        let p = &f.params;
        let scope = new_scope();
        let npos = p.names.len()
            - p.star.map_or(0, |_| 1)
            - p.dstar.map_or(0, |_| 1);
        {
            let mut s = scope.borrow_mut();
            let mut used = Used::new(p.names.len());
            let mut extra = Vec::new();
            // Indexed with `Args::take` rather than `into_iter`: consuming the
            // list as an iterator would turn it into a `Vec` first, which is
            // exactly the allocation `Args` exists to remove and would put it
            // back on the hottest call in the interpreter.
            let nargs = args.len();
            for i in 0..nargs {
                let a = args.take(i);
                if i < npos {
                    s.insert(p.names[i].clone(), a);
                    used.set(i);
                } else if p.star.is_some() {
                    extra.push(a);
                } else {
                    return Err(arity_error(&f.name, npos, &f.defaults, nargs));
                }
            }
            if let Some(si) = p.star {
                s.insert(p.names[si].clone(), Value::Tuple(Rc::new(extra)));
                used.set(si);
            }
            let mut leftover = Dict::new();
            for (k, v) in kw {
                match p.names[..npos].iter().position(|n| *n == k) {
                    Some(i) => {
                        s.insert(p.names[i].clone(), v);
                        used.set(i);
                    }
                    None => {
                        if p.dstar.is_some() {
                            leftover.insert(Value::Str(k), v)?;
                        } else {
                            return Err(type_err(format!(
                                "{}() got an unexpected keyword argument '{k}'",
                                f.name
                            )));
                        }
                    }
                }
            }
            if let Some(di) = p.dstar {
                s.insert(
                    p.names[di].clone(),
                    Value::Dict(Rc::new(RefCell::new(leftover))),
                );
                used.set(di);
            }
            for i in 0..npos {
                if !used.get(i) {
                    match &f.defaults[i] {
                        Some(d) => {
                            s.insert(p.names[i].clone(), d.clone());
                        }
                        None => {
                            return Err(type_err(format!(
                                "{}() missing 1 required positional argument: '{}'",
                                f.name, p.names[i]
                            )))
                        }
                    }
                }
            }
        }
        // The scope chain is rebuilt per call — the closure's environment plus
        // this frame — and the vector that holds it used to be allocated and
        // freed per call. Recycled instead: the spent one goes back to the pool
        // at the bottom of this function, so a recursion pays for its depth once
        // rather than once per frame per call.
        let mut c = self.chain_pool.pop().unwrap_or_default();
        c.extend_from_slice(&f.env);
        c.push(scope);
        let saved_chain = std::mem::replace(&mut self.chain, c);
        self.global_decls.push(crate::hash::set());
        self.assigned.push(f.assigned.clone());
        let r = match &f.lambda {
            Some(body) => self.eval(body),
            None => match self.exec_block(&f.body) {
                Ok(Flow::Return(v)) => Ok(v),
                Ok(_) => Ok(Value::None),
                Err(e) => Err(e),
            },
        };
        self.assigned.pop();
        self.global_decls.pop();
        // Cleared here rather than on reuse, so the frame's scopes are dropped
        // when the frame ends and not whenever the vector is next taken out.
        let mut spent = std::mem::replace(&mut self.chain, saved_chain);
        spent.clear();
        if self.chain_pool.len() < CHAIN_POOL_MAX {
            self.chain_pool.push(spent);
        }
        r
    }
}

/// One "was this parameter bound" flag per parameter, without the allocation a
/// `Vec<bool>` costs on every single call.
///
/// A call is where this interpreter allocates most (a quarter of its whole
/// instruction stream is musl's malloc and free — `docs/HILLCLIMB.md`
/// iteration 6), and a vector of flags for a function with two parameters is
/// one of them. Sixty-four covers every function anyone types; CPython's own
/// hard limit on positional parameters is 255, and past 64 this falls back to
/// the vector so the behaviour is IDENTICAL rather than merely unlikely to
/// differ.
struct Used {
    bits: u64,
    big: Vec<bool>,
}

impl Used {
    fn new(n: usize) -> Self {
        Used {
            bits: 0,
            big: if n > 64 { vec![false; n] } else { Vec::new() },
        }
    }

    #[inline]
    fn set(&mut self, i: usize) {
        if self.big.is_empty() {
            self.bits |= 1u64 << i;
        } else {
            self.big[i] = true;
        }
    }

    #[inline]
    fn get(&self, i: usize) -> bool {
        if self.big.is_empty() {
            self.bits & (1u64 << i) != 0
        } else {
            self.big[i]
        }
    }
}

/// CPython's wording for "too many positional arguments", to the letter.
///
/// Three things vary and all three were wrong here. The count reported is the
/// number GIVEN, not the index the binder stopped at — `f1(1, 2, 3)` says three,
/// not two. A function with defaults says `from R to N`, never a bare `N`. And
/// `argument` and `was` are singular only in the cases CPython makes them
/// singular in, which are not the same case: `takes 1 positional argument`, but
/// `takes from 0 to 1 positional arguments`.
///
/// This is a message, so it reaches stdout only through `except TypeError as e:
/// print(e)`. That is why `conformance` never caught it, and why it is a
/// divergence rather than a refusal. Found by reading the call path for
/// allocations — which is where the last such find came from too.
fn arity_error(name: &str, npos: usize, defaults: &[Option<Value>], given: usize) -> LypningError {
    let required = defaults
        .iter()
        .take(npos)
        .take_while(|d| d.is_none())
        .count();
    let takes = if required == npos {
        format!(
            "{npos} positional argument{}",
            if npos == 1 { "" } else { "s" }
        )
    } else {
        format!("from {required} to {npos} positional arguments")
    };
    type_err(format!(
        "{name}() takes {takes} but {given} {} given",
        if given == 1 { "was" } else { "were" }
    ))
}

pub struct IterState {
    pub it: crate::iter::Iter,
}

/// Every name assigned anywhere in a function body, including its parameters.
/// Used to make an early read raise `UnboundLocalError` rather than silently
/// finding a global of the same name.
fn assigned_names(body: &[Stmt], params: &Params) -> FastSet<Rc<str>> {
    let mut out = crate::hash::set();
    for n in &params.names {
        out.insert(n.clone());
    }
    collect_assigned(body, &mut out);
    out
}

fn collect_assigned(body: &[Stmt], out: &mut FastSet<Rc<str>>) {
    fn tgt(t: &Target, out: &mut FastSet<Rc<str>>) {
        match t {
            Target::Name(n) => {
                out.insert(n.clone());
            }
            Target::Tuple(v) => v.iter().for_each(|x| tgt(x, out)),
            Target::Star(b) => tgt(b, out),
            _ => {}
        }
    }
    for s in body {
        match s {
            Stmt::Assign { targets, .. } => targets.iter().for_each(|t| tgt(t, out)),
            Stmt::AugAssign { target, .. } => tgt(target, out),
            Stmt::For {
                target, body, els, ..
            } => {
                tgt(target, out);
                collect_assigned(body, out);
                collect_assigned(els, out);
            }
            Stmt::While { body, els, .. } => {
                collect_assigned(body, out);
                collect_assigned(els, out);
            }
            Stmt::If { arms, els } => {
                for (_, b) in arms {
                    collect_assigned(b, out);
                }
                collect_assigned(els, out);
            }
            Stmt::Try {
                body,
                handlers,
                els,
                finally,
            } => {
                collect_assigned(body, out);
                for h in handlers {
                    if let Some(n) = &h.name {
                        out.insert(n.clone());
                    }
                    collect_assigned(&h.body, out);
                }
                collect_assigned(els, out);
                collect_assigned(finally, out);
            }
            Stmt::With { items, body } => {
                for (_, a) in items {
                    if let Some(t) = a {
                        tgt(t, out);
                    }
                }
                collect_assigned(body, out);
            }
            Stmt::Def { name, .. } => {
                out.insert(name.clone());
            }
            Stmt::Import { names } => {
                for (_, b) in names {
                    out.insert(b.clone());
                }
            }
            Stmt::FromImport { names, .. } => {
                for (_, b) in names {
                    out.insert(b.clone());
                }
            }
            _ => {}
        }
    }
}

pub fn err_kind(e: &LypningError) -> &'static str {
    match e {
        LypningError::Exc(x) => x.kind,
        _ => "RuntimeError",
    }
}
pub fn err_msg(e: &LypningError) -> String {
    match e {
        LypningError::Exc(x) => x.msg.clone(),
        other => other.to_string(),
    }
}

/// Does an `except NAME` clause catch an exception of this kind?
/// Only the hierarchy edges that actually occur are encoded; an unrecognised
/// name is `unsupported` at analysis time, so this never guesses.
pub fn exc_matches(clause: &str, kind: &str) -> bool {
    let clause = clause.rsplit('.').next().unwrap_or(clause);
    if clause == kind {
        return true;
    }
    match clause {
        "BaseException" => true,
        "Exception" => kind != "SystemExit" && kind != "KeyboardInterrupt",
        "ArithmeticError" => matches!(kind, "ZeroDivisionError" | "OverflowError" | "FloatingPointError"),
        "LookupError" => matches!(kind, "IndexError" | "KeyError"),
        // `IOError` and `EnvironmentError` are not subclasses of `OSError` in
        // CPython — they ARE it, two names bound to the same class. So they have
        // to appear on BOTH sides: as a clause that catches an OSError, which
        // they already did, and as a KIND that an `except OSError` catches,
        // which they did not. `raise IOError(...)` therefore escaped an
        // `except OSError` and exited 1 with a traceback where CPython printed
        // the handler's output — a wrong answer at a wrong exit code, which is
        // the one failure invariant 1 is about. It was asymmetric and so it read
        // as working from the other direction.
        "OSError" | "IOError" | "EnvironmentError" => matches!(
            kind,
            "OSError" | "IOError" | "EnvironmentError" | "FileNotFoundError" | "PermissionError"
                | "FileExistsError" | "IsADirectoryError" | "NotADirectoryError"
        ),
        "ValueError" => kind == "UnicodeDecodeError" || kind == "JSONDecodeError",
        "NameError" => kind == "UnboundLocalError",
        _ => false,
    }
}

pub fn int_val(v: &Value) -> R<i64> {
    match v {
        Value::Int(i) => Ok(*i),
        Value::Bool(b) => Ok(*b as i64),
        other => Err(type_err(format!(
            "'{}' object cannot be interpreted as an integer",
            type_name(other)
        ))),
    }
}

pub fn clamp_index(i: i64, n: i64) -> i64 {
    if i < 0 {
        (n + i).max(0)
    } else {
        i.min(n)
    }
}


/// Take an interpreter apart without recursing on the values it holds.
///
/// The counterpart of [`crate::value::dismantle`] for everything one run
/// accumulated: its globals, the scopes of any function still referenced, and
/// the module table. Called by [`crate::embed::run`] on the way out — the
/// binary does not need it, because a process that is about to exit does not
/// have to survive dropping what it built.
pub fn dismantle_interp(it: Interp) {
    let Interp {
        globals,
        chain,
        modules,
        ..
    } = it;
    for scope in std::iter::once(globals).chain(chain) {
        if let Ok(cell) = Rc::try_unwrap(scope) {
            for (_, v) in cell.into_inner() {
                crate::value::dismantle(v);
            }
        }
    }
    for (_, v) in modules {
        crate::value::dismantle(v);
    }
}
