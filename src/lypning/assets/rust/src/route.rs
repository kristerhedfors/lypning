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
use crate::err::ErrKind;
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
    /// Every module the program imports — **sorted and deduplicated**, not in
    /// source order. It is collected through a `BTreeSet` because the question
    /// it answers is "which modules does this need", which has no order, and a
    /// set cannot report `import os` twice for a program that says it twice.
    /// The doc here said "in source order" for as long as it was wrong; a host
    /// that indexed `imports[0]` expecting the first line got the alphabetically
    /// first module instead.
    pub imports: Vec<String>,
}

/// Modules lypning-mp serves: its frozen `micropython/lib` shim stdlib plus the
/// MicroPython built-ins its variant enables. Kept as a table because lypning-mp is
/// a separate binary that cannot be asked; kept HONEST by
/// `lypning conformance`, which runs the corpus through all three
/// engines and reports any program this table sends to the wrong one.
///
/// `argparse` was added 2026-08-25. `tests/test_routing.py` reported it — that
/// test asks the tier what it can import and warns about anything the corpus
/// uses that this table omits, because every such program takes a CPython spawn
/// it does not need. The test **warns and never asserts**, on purpose: a test
/// that failed until someone edited this table would demand exactly the edit
/// CLAUDE.md invariant 1 prohibits.
///
/// **`unicodedata` was reported by the same test and is deliberately NOT here.**
/// The tier imports it; it does not serve it. `unicodedata.decomposition` is
/// absent, so a corpus program that prints a version banner and then calls it
/// gets its banner onto stdout before the refusal — and lypning-mp streams, so
/// those bytes are already committed (§6). Adding the module moved routing
/// safety's fatal count from **UNSAFE 4 to 5**. That is the whole meaning of
/// "importable is not the same as complete", and it is why this table is earned
/// with `lypning conformance` rather than with `import x` returning 0.
/// `docs/HILLCLIMB.md` iteration 40 has the measurement.
const MICROPYTHON_MODULES: &[&str] = &[
    "argparse",
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

/// Refusal kinds after which the chain jumps straight to CPython, skipping
/// lypning-mp entirely.
///
/// Falling through assumes the tier below is at least as correct as the one that
/// refused, and for a capability gap it is — "I have no decorators", and
/// MicroPython has decorators. These are not capability gaps. Each names a
/// behaviour CPython has that is subtle enough that the refusal exists BECAUSE a
/// reimplementation gets it wrong, so the tier below gets it wrong too and
/// answers at exit 0 rather than refusing.
///
/// **This table is read by both dispatchers.** It was not: the rule was added to
/// `engines.dispatch` (the Python one, which `lypning conformance` measures) and
/// not to `dispatch` below (the Rust one, which is what `lypning run` actually
/// executes and what `lypning bench` times). So the correctness gate tested a
/// dispatcher users do not run, the cost gate ran a dispatcher nothing checked,
/// and three measured programs answered wrongly at exit 0 through the binary
/// while answering correctly through the battery:
///
/// ```text
/// lypning run -c 'print({3,1,2})'                 {3, 1, 2}   CPython {1, 2, 3}
/// lypning run -c 'x=float("nan")\nprint(x in [x])'  False     CPython True
/// lypning run -c 'print(9007199254740993 / 3)'    …330.5      CPython …331.0
/// ```
///
/// `engines.ONLY_CPYTHON_REFUSALS` is now held to this list by
/// `tests/test_routing.py`, which reads it out of this file the way
/// `routing.micropython_modules()` reads `MICROPYTHON_MODULES` — a copy that
/// cannot drift silently rather than a copy that already had.
pub const ONLY_CPYTHON_KINDS: &[&str] = &[
    "del",
    "dict-view",
    "exception-chaining",
    "int-div-precision",
    "json",
    "nan-identity",
    "percent-format",
    "repr-unicode",
    "set-method",
    "set-order",
];

/// Does this refusal kind rule out every tier but CPython? See [`ONLY_CPYTHON_KINDS`].
pub fn only_cpython(kind: &str) -> bool {
    ONLY_CPYTHON_KINDS.contains(&kind)
}

/// Constructs no MicroPython-derived runtime has, so a program using one goes
/// straight to CPython rather than paying a lypning-mp spawn to be told no.
///
/// `decorator` and `generator` were here and should not have been: MicroPython
/// implements both, in the language rather than a library. Ten corpus programs
/// were sent past a tier that runs them — measured, not assumed:
///
/// ```text
/// $ lypning-mp -c 'def d(f):
///       def w(*a): return f(*a)*2
///       return w
///   @d
///   def g(x): return x+1
///   print(g(3))'
/// 8
/// $ lypning-mp -c 'def g():
///       yield 1
///   gen = g(); print(next(gen)); gen.close(); print("closed")'
/// 1
/// closed
/// ```
///
/// `async` stays, and for a reason that shows what this list is really for:
/// `async def` *parses* there, so the syntax is not the problem — `asyncio` is
/// absent, and the program needs it to do anything. The refusal is clean (exit
/// 90, empty stdout), so routing an `async` program here would cost a spawn and
/// still reach CPython. This list is about where a program ENDS UP, not about
/// what a parser accepts.
///
/// A decorator that comes *from* an absent module costs nothing either, and not
/// by luck: `engine_for` checks the imports before it reaches this match, so
/// `@functools.lru_cache` is decided by `import functools` and still goes
/// straight to CPython. What is left to pay for is a decorator or generator that
/// imports nothing lypning-mp lacks and fails there for some other reason — one
/// spawn, and the chain still answers. WASTED is a budget; a program that could
/// have skipped CPython entirely is worth more than that.
const CPYTHON_ONLY_KINDS: &[&str] = &["async"];

pub fn route(src: &str) -> Route {
    let mut imports = Vec::new();
    match crate::parse::parse(src) {
        Err(ref e) if matches!(e.kind(), ErrKind::Unsupported { .. }) => {
            let (kind, detail) = match e.kind() {
                ErrKind::Unsupported { kind, detail } => (kind.clone(), detail.clone()),
                _ => unreachable!(),
            };
            // The parse stopped before the imports could be collected, so scan
            // the source for them: the import line is what usually decides the
            // tier, and it is cheap and unambiguous to find.
            imports = scan_imports(src);
            let engine = engine_for(&kind, &imports, &BTreeSet::new());
            Route {
                engine,
                kind,
                detail,
                imports,
            }
        }
        Err(ref e) if matches!(e.kind(), ErrKind::Syntax { .. }) => {
            let (line, msg) = match e.kind() {
                ErrKind::Syntax { line, msg } => (*line, msg.clone()),
                _ => unreachable!(),
            };
            // A syntax error is not a capability gap. CPython owns it, because
            // its message is the one the caller expects to read.
            Route {
                engine: Engine::CPython,
                kind: "syntax".into(),
                detail: format!("line {line}: {msg}"),
                imports: scan_imports(src),
            }
        }
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
                    let engine = engine_for(&kind, &imports, &req.mp_risk);
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

/// Constructs lypning-mp is KNOWN to answer WRONGLY, each named by its family in
/// `.github/known-mismatches.json`.
///
/// This is the inverse of `MICROPYTHON_MODULES` and it exists for one number:
/// **UNSAFE**, a program routed to a tier that gives the wrong answer. Six of
/// the seven route to lypning-mp, and lypning-mp is a third-party runtime whose
/// defects cannot be fixed here — but the CLASSIFIER can decline to send a
/// program there when the source shows it would trip on one.
///
/// Deliberately CONSTRUCT-level and not KIND-level. Iteration 48 widened a whole
/// blocker kind toward lypning-mp on the strength of a LATE improvement and
/// bought two UNSAFE; the lesson recorded there is that a population where the
/// tier is usually right is not a population the classifier may claim. A named
/// construct with a measured cost is a different claim, and each entry here
/// carries both.
///
/// Measured 2026-08-28 over a corpus of 2239 programs: `random.seed` 19,
/// `__module__` 5, `.parts` 1 — 25 in total, against 133 for routing all of
/// `pathlib` away and 15 for all of `base64`. Twenty-five extra CPython spawns
/// buys three UNSAFE, and UNSAFE is a gate where LATE is a budget.
const MICROPYTHON_UNSAFE: &[(&str, &str)] = &[
    // A seeded stream is not reproducible across implementations: MicroPython
    // has no Mersenne Twister, so `random.seed(7)` produces a different sequence
    // and the program answers, plausibly and wrongly.
    ("random.seed", "random-seeded-stream"),
    // Built-in types carry no `__module__` there, so the ordinary
    // `type(e).__module__ + '.' + type(e).__name__` idiom dies mid-program with
    // output already committed.
    ("__module__", "dunder-missing-on-builtins"),
    // `Path('/a/b').parts` drops the root component. Guarded on `pathlib` also
    // being imported, since `.parts` is an ordinary attribute name.
    ("parts", "pathlib-parts-drops-root"),
    // The rest are the COMMIT BARRIER, and they fail differently: lypning-mp
    // answers correctly right up to the construct and only then refuses, with
    // its output already streamed. A refusal is interchangeable with the next
    // tier's answer only because it leaves nothing behind, so one that arrives
    // late is not a refusal the chain can act on (docs/LYPNING.md §6). The tier
    // cannot be fixed here; the classifier can decline to start there.
    ("hashlib.algorithms_guaranteed", "commit-barrier"),
];

//: Keyword ARGUMENT names carrying the same risk. A separate table because a
//: keyword is a different node from an attribute, not a different policy: the
//: entry below is a parameter lypning-mp does not accept and discovers only at
//: the call, by which point the program has printed.
const MICROPYTHON_UNSAFE_KWARGS: &[(&str, &str)] = &[("strict_mode", "commit-barrier")];

fn engine_for(kind: &str, imports: &[String], mp_risk: &BTreeSet<&'static str>) -> Engine {
    if CPYTHON_ONLY_KINDS.contains(&kind) {
        return Engine::CPython;
    }
    // A construct lypning-mp gets wrong decides before anything else does: the
    // whole point is that this program must not reach that tier, whatever else
    // would have sent it there.
    if !mp_risk.is_empty() {
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
        // lypning-mp is MicroPython: it has arbitrary-precision integers, a
        // class system, decorators and generators. Each of these is exactly a
        // gap lypning refuses — AT PARSE TIME, which is the only time this
        // match runs. The classifier sees a kind from exactly three sources:
        // a parse-time refusal (parse.rs), a lex-time one (lex.rs), or
        // `Requirements::block` in this file. This arm listed 23 more kinds
        // that only the EVALUATOR emits (`set-order`, `del`, `json`,
        // `percent-format`, `round`, …) — names that can never reach it, six of
        // which `ONLY_CPYTHON_KINDS` says must skip lypning-mp entirely. Dead,
        // the contradiction was inert; the day someone taught the parser to
        // spot one of those constructs statically, this arm would have started
        // routing programs to the tier the other table exists to keep them off,
        // with no gate looking. The unlisted kinds fall to `_ => CPython`,
        // which is the safe direction: a spare spawn, never a wrong answer.
        // `tests/test_routing.py` now holds this arm to the kinds the
        // classifier can actually emit, read out of the source.
        "bigint" | "module" | "module-attr" | "class" | "recursion" | "fstring"
        | "setattr" | "slice-assign" | "unpack" | "kwonly" | "nonlocal"
        | "import" | "escape" | "ellipsis" | "complex"
        // Both are language features there, not library ones. See
        // CPYTHON_ONLY_KINDS, which listed them as absent until it was measured.
        | "decorator" | "generator" => Engine::MicroPython,
        // A construct nobody named yet: send it to the most capable tier rather
        // than guess, and let the conformance run reclassify it with evidence.
        _ => Engine::CPython,
    }
}

#[derive(Default)]
struct Requirements {
    imports: BTreeSet<String>,
    blocker: Option<(String, String)>,
    /// Families from `.github/known-mismatches.json` this program's SOURCE
    /// shows it would trip on lypning-mp. See [`MICROPYTHON_UNSAFE`]. The
    /// labels come from two namespaces — ledger family names for the construct
    /// table, refusal kinds for the AST markers — and only EMPTINESS is ever
    /// read; a label is a breadcrumb for the next reader, not a join key.
    mp_risk: BTreeSet<&'static str>,
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

/// The module a dotted expression names, if it names one at all.
///
/// Recursive because module paths nest and the check that used this had no way
/// to. `os.getenv` has a bare `Expr::Name` for a base and was always decided
/// against `modules::MODULES`; `os.path.basename` has an `Expr::Attr` for a
/// base, fell past that check into the method table, missed every entry there
/// and was blocked as `method: .basename()` — for a function the engine
/// implements, along with thirteen others under `os.path`.
///
/// A classifier that under-reports its own engine is worse than one spawn
/// wasted. `lypning route` is what the skill tells an agent to trust, so an
/// agent reads "cpython" and **rewrites working code to satisfy a tier the
/// original already met**; the prompting study watched two of them replace
/// `os.path.splitext` with a hand-rolled `rfind`. `docs/LYPNING.md` §4.
///
/// Only a `Value::Module` counts as a step. `os.environ` resolves to a dict and
/// stops the walk here, so `os.environ.get` is still decided by the method
/// table — which is correct, because `.get` is a method and not a module
/// attribute.
fn resolve_module(e: &Expr) -> Option<crate::value::Value> {
    match e {
        Expr::Name(n) => crate::modules::MODULES
            .iter()
            .find(|x| **x == n.as_ref())
            .map(|m| crate::value::Value::Module(m)),
        Expr::Attr(b, n) => match crate::modules::get_attr(&resolve_module(b)?, n) {
            Ok(v @ crate::value::Value::Module(_)) => Some(v),
            _ => None,
        },
        _ => None,
    }
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
            // Record any construct lypning-mp is known to answer wrongly. This
            // runs BEFORE the module/method resolution below, because the point
            // is where the program must not GO, not what stops lypning.
            //
            // `.parts` is guarded on `pathlib`, since it is an ordinary
            // attribute name and only the pathlib one is wrong; `__module__` is
            // a dunder that means nothing else; a construct spelled with a dot
            // is matched as a dotted path, so an unrelated `.seed` on some other
            // object does not fire `random.seed`.
            for (construct, family) in MICROPYTHON_UNSAFE {
                let hit = match construct.split_once('.') {
                    Some((module, attr)) => {
                        n.as_ref() == attr
                            && matches!(b.as_ref(), Expr::Name(m) if m.as_ref() == module)
                    }
                    None if *construct == "parts" => {
                        n.as_ref() == "parts" && req.imports.iter().any(|m| m == "pathlib")
                    }
                    None => n.as_ref() == *construct,
                };
                if hit {
                    req.mp_risk.insert(family);
                }
            }
            // A module attribute is decidable; anything else is a method name.
            if let Some(crate::value::Value::Module(m)) = resolve_module(b) {
                if crate::modules::get_attr(&crate::value::Value::Module(m), n).is_err() {
                    req.block("module-attr", format!("{m}.{n}"));
                }
                return;
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
            // THE TWO TABLES HAVE TO COVER THE SAME GROUND, and this is where
            // they did not. `engines.ONLY_CPYTHON_REFUSALS` is the RUNTIME half:
            // it fires on a refusal tier 1 actually emitted. But tier 1 only
            // runs when the classifier sends the program there, and a program
            // whose FIRST blocker is an ordinary capability gap goes straight to
            // lypning-mp — so tier 1 never refuses, the runtime table never
            // sees the kind, and the tier answers wrongly at exit 0:
            //
            //     import math                      (an unused import is enough)
            //     x = float("nan")
            //     print(x in [x])     CPython True   lypning-mp False
            //
            // A NaN literal is visible in the SOURCE, so the static half can
            // catch what the runtime half cannot. Measured over the corpus the
            // run loaded (2,239 programs, 2026-08-28): 8 programs contain
            // `float("nan")` and exactly ONE routes to lypning-mp today, so the
            // rule costs one spawn.
            if let Expr::Name(f) = func.as_ref() {
                if f.as_ref() == "float" && args.len() == 1 {
                    if let Expr::Str(v) = &args[0] {
                        if v.eq_ignore_ascii_case("nan") {
                            req.mp_risk.insert("nan-identity");
                        }
                    }
                }
            }
            walk_expr(func, req);
            for a in args {
                walk_expr(a, req);
            }
            for (name, v) in kwargs {
                for (construct, family) in MICROPYTHON_UNSAFE_KWARGS {
                    if name.as_ref() == *construct {
                        req.mp_risk.insert(family);
                    }
                }
                walk_expr(v, req);
            }
            for d in dstar {
                walk_expr(d, req);
            }
        }
        Expr::Bin(op, a, b) => {
            // The same hole as the NaN literal above, for the kind this session
            // split out of `bigint`. `int / int` where an operand is past 2**53
            // needs a quotient rounded from the integers themselves, and
            // lypning-mp converts both to double exactly as tier 1 would have —
            // so it answers, and it answers wrongly:
            //
            //     import math                  (an unused import is enough)
            //     print(9007199254740993 / 3)
            //     CPython 3002399751580331.0   lypning-mp 3002399751580330.5
            //
            // Narrow on purpose: the DIVISION operator with a literal operand
            // past the exactly-representable range, not "a big literal anywhere".
            // Measured over the corpus the run loaded (2,239 programs,
            // 2026-08-28): 10 programs hold a literal that large and exactly ONE
            // routes to lypning-mp, so the rule costs one spawn.
            const EXACT: i64 = 1 << 53;
            if matches!(op, BinOp::Div) {
                for side in [a.as_ref(), b.as_ref()] {
                    if let Expr::Int(n) = side {
                        if n.unsigned_abs() > EXACT as u64 {
                            req.mp_risk.insert("int-div-precision");
                        }
                    }
                }
            }
            walk_expr(a, req);
            walk_expr(b, req);
        }
        Expr::Index(a, b) => {
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
