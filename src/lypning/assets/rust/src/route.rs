//! The classifier — the "mixture" in Mixture of Pythons.
//!
//! Routing is a STATIC analysis over lypning's own front end, not a heuristic over
//! the program text. That choice is the whole design:
//!
//!   * lypning's parser already reports the exact construct that would stop it, as
//!     `unsupported: <kind>: <detail>`. Asking the parser is therefore an exact
//!     answer to "can lypning run this", not a guess — and it costs one parse, no
//!     process spawn, no execution.
//!   * The tiers below lypning cannot be asked the same way (they are separate
//!     binaries), so those are capability TABLES, derived from what each
//!     runtime ships and checked against measured conformance by
//!     `lypning conformance`.
//!
//! A route is a prediction, and predictions are wrong sometimes. That is why
//! the dispatcher is a fallback CHAIN rather than a jump: a wrong route costs
//! one wasted spawn (~2 ms), never a wrong answer, because every tier refuses
//! with exit 90 instead of guessing. The classifier's job is to make the first
//! guess right often enough that the chain rarely runs twice.

use crate::ast::*;
use crate::err::LypningError;
use std::collections::BTreeSet;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Engine {
    Lypning,
    MicroPython,
    CPython,
}

impl Engine {
    pub fn as_str(self) -> &'static str {
        match self {
            Engine::Lypning => "lypning",
            Engine::MicroPython => "lypning-mp",
            Engine::CPython => "cpython",
        }
    }
}

#[derive(Debug)]
pub struct Route {
    pub engine: Engine,
    /// The construct that pushed the program past lypning, if any.
    pub kind: String,
    pub detail: String,
    /// Every module the program imports, in source order.
    pub imports: Vec<String>,
}

/// Modules lypning-mp serves: its frozen `micropython/lib` shim stdlib plus the
/// MicroPython built-ins its variant enables. Kept as a table because lypning-mp is
/// a separate binary that cannot be asked; kept HONEST by
/// `lypning conformance`, which runs the corpus through all three
/// engines and reports any program this table sends to the wrong one.
const MICROPYTHON_MODULES: &[&str] = &[
    "base64",
    "binascii",
    "builtins",
    "cmath",
    "collections",
    "contextlib",
    "csv",
    "datetime",
    "errno",
    "glob",
    "hashlib",
    "io",
    "json",
    "math",
    "os",
    "os.path",
    "pathlib",
    "random",
    "re",
    "shutil",
    "statistics",
    "struct",
    "sys",
    "tempfile",
    "textwrap",
    "time",
    "urllib",
    "urllib.parse",
    "zlib",
];

/// Constructs no MicroPython-derived runtime has, so a program using one goes
/// straight to CPython rather than paying a lypning-mp spawn to be told no.
const CPYTHON_ONLY_KINDS: &[&str] = &["async", "decorator", "generator"];

pub fn route(src: &str) -> Route {
    let mut imports = Vec::new();
    match crate::parse::parse(src) {
        Err(LypningError::Unsupported { kind, detail }) => {
            // The parse stopped before the imports could be collected, so scan
            // the source for them: the import line is what usually decides the
            // tier, and it is cheap and unambiguous to find.
            imports = scan_imports(src);
            let engine = engine_for(&kind, &imports);
            Route {
                engine,
                kind,
                detail,
                imports,
            }
        }
        Err(LypningError::Syntax { line, msg }) => Route {
            // A syntax error is not a capability gap. CPython owns it, because
            // its message is the one the caller expects to read.
            engine: Engine::CPython,
            kind: "syntax".into(),
            detail: format!("line {line}: {msg}"),
            imports: scan_imports(src),
        },
        Err(other) => Route {
            engine: Engine::CPython,
            kind: "error".into(),
            detail: other.to_string(),
            imports,
        },
        Ok(body) => {
            let mut req = Requirements::default();
            walk_block(&body, &mut req);
            imports = req.imports.iter().cloned().collect();
            match req.blocker {
                None => Route {
                    engine: Engine::Lypning,
                    kind: String::new(),
                    detail: String::new(),
                    imports,
                },
                Some((kind, detail)) => {
                    let engine = engine_for(&kind, &imports);
                    Route {
                        engine,
                        kind,
                        detail,
                        imports,
                    }
                }
            }
        }
    }
}

fn engine_for(kind: &str, imports: &[String]) -> Engine {
    if CPYTHON_ONLY_KINDS.contains(&kind) {
        return Engine::CPython;
    }
    // Any import outside lypning-mp's stdlib decides for CPython regardless of what
    // else blocked lypning — the import fails first, so a closer look is wasted.
    for m in imports {
        if !MICROPYTHON_MODULES.contains(&m.as_str()) {
            return Engine::CPython;
        }
    }
    match kind {
        // lypning-mp is MicroPython: it has arbitrary-precision integers, a regex
        // engine, and a set type whose order it defines for itself. Each of
        // these is exactly a gap lypning refuses.
        "bigint" | "module" | "module-attr" | "set-order" | "builtin" | "str-method"
        | "list-method" | "dict-method" | "set-method" | "file-method" | "encoding" | "repr"
        | "repr-unicode" | "class" | "recursion" | "with" | "format" | "percent-format"
        | "os-listdir" | "type" | "json" | "open-mode" | "fstring" | "print-file" | "round"
        | "setattr" | "del" | "augassign" | "slice-assign" | "unpack" | "kwonly" | "nonlocal"
        | "import" | "file-seek" | "open-newline" | "escape" | "ellipsis" | "complex" => {
            Engine::MicroPython
        }
        // A construct nobody named yet: send it to the most capable tier rather
        // than guess, and let the conformance run reclassify it with evidence.
        _ => Engine::CPython,
    }
}

#[derive(Default)]
struct Requirements {
    imports: BTreeSet<String>,
    blocker: Option<(String, String)>,
}

impl Requirements {
    fn block(&mut self, kind: &str, detail: String) {
        if self.blocker.is_none() {
            self.blocker = Some((kind.to_string(), detail));
        }
    }
}

fn walk_block(body: &[Stmt], req: &mut Requirements) {
    for s in body {
        walk_stmt(s, req);
    }
}

fn walk_stmt(s: &Stmt, req: &mut Requirements) {
    match s {
        Stmt::Import { names } => {
            for (path, _) in names {
                req.imports.insert(path.to_string());
                if !crate::modules::MODULES.contains(&path.as_ref()) {
                    req.block("module", format!("import {path}"));
                }
            }
        }
        Stmt::FromImport { module, names } => {
            req.imports.insert(module.to_string());
            if !crate::modules::MODULES.contains(&module.as_ref()) {
                req.block("module", format!("from {module} import …"));
            } else {
                let m = crate::value::Value::Module(
                    crate::modules::MODULES
                        .iter()
                        .find(|x| **x == module.as_ref())
                        .unwrap(),
                );
                for (n, _) in names {
                    if crate::modules::get_attr(&m, n).is_err() {
                        req.block("module-attr", format!("{module}.{n}"));
                    }
                }
            }
        }
        Stmt::Expr(e) => walk_expr(e, req),
        Stmt::Assign { targets, value } => {
            walk_expr(value, req);
            for t in targets {
                walk_target(t, req);
            }
        }
        Stmt::AugAssign { target, value, .. } => {
            walk_target(target, req);
            walk_expr(value, req);
        }
        Stmt::If { arms, els } => {
            for (c, b) in arms {
                walk_expr(c, req);
                walk_block(b, req);
            }
            walk_block(els, req);
        }
        Stmt::For {
            target,
            iter,
            body,
            els,
        } => {
            walk_target(target, req);
            walk_expr(iter, req);
            walk_block(body, req);
            walk_block(els, req);
        }
        Stmt::While { cond, body, els } => {
            walk_expr(cond, req);
            walk_block(body, req);
            walk_block(els, req);
        }
        Stmt::Return(Some(e)) | Stmt::Raise { exc: Some(e) } => walk_expr(e, req),
        Stmt::Assert { test, msg } => {
            walk_expr(test, req);
            if let Some(m) = msg {
                walk_expr(m, req);
            }
        }
        Stmt::Def { body, params, .. } => {
            for d in params.defaults.iter().flatten() {
                walk_expr(d, req);
            }
            walk_block(body, req);
        }
        Stmt::Try {
            body,
            handlers,
            els,
            finally,
        } => {
            walk_block(body, req);
            for h in handlers {
                for k in &h.kinds {
                    let base = k.rsplit('.').next().unwrap_or(k);
                    if !crate::builtins::is_exception_name(base) {
                        req.block("exception", format!("except {k}"));
                    }
                }
                walk_block(&h.body, req);
            }
            walk_block(els, req);
            walk_block(finally, req);
        }
        Stmt::With { items, body } => {
            for (e, t) in items {
                walk_expr(e, req);
                if let Some(t) = t {
                    walk_target(t, req);
                }
            }
            walk_block(body, req);
        }
        Stmt::Del(ts) => ts.iter().for_each(|t| walk_target(t, req)),
        _ => {}
    }
}

fn walk_target(t: &Target, req: &mut Requirements) {
    match t {
        Target::Tuple(v) => v.iter().for_each(|x| walk_target(x, req)),
        Target::Star(b) => walk_target(b, req),
        Target::Attr(e, n) => {
            walk_expr(e, req);
            req.block("setattr", format!("assignment to .{n}"));
        }
        Target::Index(a, b) => {
            walk_expr(a, req);
            walk_expr(b, req);
        }
        Target::Slice { base, lo, hi } => {
            walk_expr(base, req);
            for e in [lo, hi].into_iter().flatten() {
                walk_expr(e, req);
            }
        }
        Target::Name(_) => {}
    }
}

/// Method names lypning implements on ANY type. Attribute access on a value whose
/// type is not known statically is checked against this union — a name outside
/// it certainly fails, a name inside it probably works. The asymmetry is
/// deliberate: this pass must never claim lypning can run something it cannot, and
/// it is allowed to be optimistic in the other direction because the dispatcher
/// falls through on exit 90.
fn known_method(name: &str) -> bool {
    crate::methods::method_name(&crate::value::Value::Str("".into()), name).is_some()
        || crate::methods::method_name(&crate::value::list(Vec::new()), name).is_some()
        || crate::methods::method_name(
            &crate::value::Value::Dict(std::rc::Rc::new(std::cell::RefCell::new(
                crate::value::Dict::new(),
            ))),
            name,
        )
        .is_some()
        || crate::methods::method_name(
            &crate::value::Value::Set(std::rc::Rc::new(std::cell::RefCell::new(
                crate::value::Set::new(),
            ))),
            name,
        )
        .is_some()
        || crate::methods::method_name(&crate::value::Value::Bytes(std::rc::Rc::new(Vec::new())), name)
            .is_some()
        || matches!(
            name,
            "read" | "readline" | "readlines" | "write" | "writelines" | "close" | "seek" | "tell"
                | "flush" | "args"
        )
}

fn walk_expr(e: &Expr, req: &mut Requirements) {
    match e {
        Expr::Name(n) => {
            // Builtin names are the only ones resolvable statically; a local
            // may legitimately be defined anywhere, so unknown names pass here
            // and become a NameError at runtime exactly as in CPython.
            if crate::builtins::builtin(n).is_none()
                && crate::builtins::is_exception_name(n)
            {
                req.block("exception", format!("exception class {n}"));
            }
        }
        Expr::Attr(b, n) => {
            walk_expr(b, req);
            // A module attribute is decidable; anything else is a method name.
            if let Expr::Name(m) = b.as_ref() {
                if let Some(mm) = crate::modules::MODULES.iter().find(|x| **x == m.as_ref()) {
                    if crate::modules::get_attr(&crate::value::Value::Module(mm), n).is_err() {
                        req.block("module-attr", format!("{m}.{n}"));
                    }
                    return;
                }
            }
            if !known_method(n) {
                req.block("method", format!(".{n}()"));
            }
        }
        Expr::Call {
            func,
            args,
            kwargs,
            dstar,
            ..
        } => {
            walk_expr(func, req);
            for a in args {
                walk_expr(a, req);
            }
            for (_, v) in kwargs {
                walk_expr(v, req);
            }
            for d in dstar {
                walk_expr(d, req);
            }
        }
        Expr::Bin(_, a, b) | Expr::Index(a, b) => {
            walk_expr(a, req);
            walk_expr(b, req);
        }
        Expr::Un(_, a) => walk_expr(a, req),
        Expr::Compare { first, rest } => {
            walk_expr(first, req);
            for (_, x) in rest {
                walk_expr(x, req);
            }
        }
        Expr::BoolAnd(v) | Expr::BoolOr(v) | Expr::Tuple(v) | Expr::List(v) | Expr::Set(v) => {
            v.iter().for_each(|x| walk_expr(x, req))
        }
        Expr::Dict(pairs) => pairs.iter().for_each(|(k, v)| {
            walk_expr(k, req);
            walk_expr(v, req);
        }),
        Expr::DictUnpack(items) => items.iter().for_each(|i| match i {
            DictItem::Pair(k, v) => {
                walk_expr(k, req);
                walk_expr(v, req);
            }
            DictItem::Unpack(e) => walk_expr(e, req),
        }),
        Expr::Cond { cond, then, els } => {
            walk_expr(cond, req);
            walk_expr(then, req);
            walk_expr(els, req);
        }
        Expr::Slice {
            base,
            lo,
            hi,
            step,
        } => {
            walk_expr(base, req);
            for x in [lo, hi, step].into_iter().flatten() {
                walk_expr(x, req);
            }
        }
        Expr::Comp {
            elt, val, clauses, ..
        } => {
            walk_expr(elt, req);
            if let Some(v) = val {
                walk_expr(v, req);
            }
            for c in clauses {
                walk_target(&c.target, req);
                walk_expr(&c.iter, req);
                c.ifs.iter().for_each(|i| walk_expr(i, req));
            }
        }
        Expr::FString(parts) => parts.iter().for_each(|p| {
            if let FPart::Expr { expr, spec, .. } = p {
                walk_expr(expr, req);
                if let Some(s) = spec {
                    walk_expr(s, req);
                }
            }
        }),
        Expr::Lambda { body, params } => {
            for d in params.defaults.iter().flatten() {
                walk_expr(d, req);
            }
            walk_expr(body, req);
        }
        _ => {}
    }
}

/// Find `import X` / `from X import …` textually. Used only when the parse
/// failed early — a rough answer about the imports is better than none, and it
/// can only move a program toward a MORE capable tier.
pub fn scan_imports(src: &str) -> Vec<String> {
    let mut out = Vec::new();
    for line in src.lines() {
        let t = line.trim_start();
        let rest = if let Some(r) = t.strip_prefix("import ") {
            r
        } else if let Some(r) = t.strip_prefix("from ") {
            match r.split_once(" import") {
                Some((m, _)) => m,
                None => continue,
            }
        } else {
            continue;
        };
        for part in rest.split(',') {
            let name = part.trim().split_whitespace().next().unwrap_or("");
            if !name.is_empty() && !out.iter().any(|x| x == name) {
                out.push(name.to_string());
            }
        }
    }
    out
}
