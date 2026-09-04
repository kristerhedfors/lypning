//! The classifier — the "mixture" in the mixture of Pythons.
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
    /// A point on the Rust spectrum, by row of [`SPECTRUM`].
    Rust(usize),
    CPython,
}

impl Engine {
    pub fn as_str(self) -> &'static str {
        match self {
            Engine::Rust(i) => SPECTRUM[i].name,
            Engine::CPython => "cpython",
        }
    }
}

/// One point on the Rust spectrum: its engine name and the capability features
/// compiled into it. `caps` is cumulative — a larger variant lists everything a
/// smaller one has — which is the monotonicity the router relies on.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Variant {
    pub name: &'static str,
    pub caps: &'static [&'static str],
}

/// The spectrum, cheapest first. EVERY variant carries the whole table, not
/// just its own row: the prototype that carried only its own capabilities
/// routed `import json` past its larger sibling straight to lypning-mp,
/// because the router could not know a sibling existed. The names are the
/// Python side's `engines.SPECTRUM`, in this order, pinned by test. One row
/// today; `lypning-l` is the next.
pub const SPECTRUM: &[Variant] = &[
    Variant { name: "lypning", caps: &[] },
    Variant { name: "lypning-l", caps: &["cap-collections"] },
];

/// The same names, NUL-terminated for the C ABI. A test holds the two lists
/// to each other; `c""` literals are the only way to get a static C string
/// without an allocation, and the ABI promises these are never freed.
pub const SPECTRUM_C: &[&std::ffi::CStr] = &[c"lypning", c"lypning-l"];

/// `cap-*` feature → (the modules it serves, the RUNTIME refusal kinds it
/// answers). Every row here is a claim `lypning build` proves on the variant
/// that carries it.
///
/// `cap-collections` serves the `collections` MODULE and answers no runtime
/// kind: the `collections` kind it raises is a refusal a larger sibling would
/// raise identically (there is no larger sibling, and the surface it refuses —
/// error messages, multiset arithmetic, `.elements()` — is refused BECAUSE it
/// is CPython's to answer, not because these bytes are missing). An empty kind
/// list is the honest one; a kind listed here would cost a spawn to be told no
/// a second time.
pub const CAPS: &[(&str, &[&str], &[&str])] = &[("cap-collections", &["collections"], &[])];

/// This binary's own name, from `build.rs` — the same constant `err::ENGINE`
/// writes at the head of every refusal line.
pub const SELF: &str = env!("LYPNING_ENGINE");

/// The capabilities this binary was built with, from `build.rs`.
pub const SELF_CAPS: &str = env!("LYPNING_CAPS");

/// Which row of [`SPECTRUM`] this binary is. A build whose `build.rs` named a
/// variant the table does not list is a broken build, not a routing case.
pub fn self_index() -> usize {
    SPECTRUM
        .iter()
        .position(|v| v.name == SELF)
        .expect("build.rs named a variant that route::SPECTRUM does not list")
}

/// `route --spectrum`: the table and this binary's place in it, as JSON, for
/// `lypning build` to assert and the Python side to pin its copy against.
pub fn spectrum_json() -> String {
    fn q(s: &str) -> String {
        let mut out = String::from("\"");
        for c in s.chars() {
            match c {
                '"' => out.push_str("\\\""),
                '\\' => out.push_str("\\\\"),
                c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
                c => out.push(c),
            }
        }
        out.push('"');
        out
    }
    let rows: Vec<String> = SPECTRUM
        .iter()
        .map(|v| {
            let caps: Vec<String> = v.caps.iter().map(|c| q(c)).collect();
            format!("{{\"name\":{},\"caps\":[{}]}}", q(v.name), caps.join(","))
        })
        .collect();
    let caps: Vec<String> = CAPS
        .iter()
        .map(|(c, mods, kinds)| {
            let m: Vec<String> = mods.iter().map(|x| q(x)).collect();
            let k: Vec<String> = kinds.iter().map(|x| q(x)).collect();
            format!("{{\"cap\":{},\"modules\":[{}],\"kinds\":[{}]}}", q(c), m.join(","), k.join(","))
        })
        .collect();
    let self_caps: Vec<String> = SELF_CAPS.split(',').filter(|s| !s.is_empty()).map(q).collect();
    format!(
        "{{\"self\":{},\"self_caps\":[{}],\"spectrum\":[{}],\"caps\":[{}]}}",
        q(SELF), self_caps.join(","), rows.join(","), caps.join(",")
    )
}

/// One rung's verdict on a program. `kind == ""` means it can run it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Verdict {
    pub engine: &'static str,
    pub kind: String,
    pub detail: String,
}

impl Verdict {
    fn ok(engine: &'static str) -> Self {
        Verdict { engine, kind: String::new(), detail: String::new() }
    }
    fn no(engine: &'static str, kind: &str, detail: &str) -> Self {
        Verdict { engine, kind: kind.to_string(), detail: detail.to_string() }
    }
}

pub const CPYTHON_NAME: &str = "cpython";

/// The MicroPython build's name. NOT a routing destination — it left the chain
/// on 2026-09-04 and is kept as an ORACLE: a second, independent reimplementation
/// of Python whose measured divergences from CPython (`.github/known-mismatches.json`,
/// 79 entries in 34 families) are the empirical list of what a reimplementation
/// gets wrong, and therefore what a larger Rust variant must implement exactly
/// or refuse. Nothing here routes to it; `MICROPYTHON_MODULES` below is its
/// import surface, read as the oracle's reach and as `lypning-l`'s build order.
pub const ORACLE_NAME: &str = "lypning-mp";

/// Every engine name in cost order — the spectrum, then CPython. The same tuple
/// the Python side calls `ENGINE_ORDER`.
pub fn engine_order() -> Vec<&'static str> {
    let mut out: Vec<&'static str> = SPECTRUM.iter().map(|v| v.name).collect();
    out.push(CPYTHON_NAME);
    out
}

impl Engine {
    /// The rung named `name`, or `None` for a name no table lists — which the
    /// caller must treat as CPython, the safe direction, never silently.
    pub fn from_name(name: &str) -> Option<Engine> {
        if name == CPYTHON_NAME {
            Some(Engine::CPython)
        } else {
            SPECTRUM.iter().position(|v| v.name == name).map(Engine::Rust)
        }
    }
}

/// Does `v` serve module `m`? The core set every variant has, plus whatever a
/// capability it carries adds. `modules::MODULES` is THIS binary's set, which
/// is the core set until the first `cap-*` gate makes it variant-specific.
fn served_module(v: &Variant, m: &str) -> bool {
    crate::modules::MODULES.contains(&m)
        || CAPS.iter().any(|(c, mods, _)| v.caps.contains(c) && mods.contains(&m))
}

fn module_of(detail: &str) -> &str {
    // `import X` / `from X import …`, as the walker spells its blockers.
    detail
        .trim_start_matches("import ")
        .trim_start_matches("from ")
        .split_whitespace()
        .next()
        .unwrap_or("")
}

/// Would variant `v` run a program that THIS binary stopped on `(kind, detail)`?
///
/// Asked only of rungs at or above this one. A `module` blocker is answered by
/// a variant that serves the module; a runtime kind (`bigint`, `format-spec`,
/// …) by one whose capability lists it in `CAPS`. `module-attr` is never
/// claimed until the attribute surface is a table (it is a `match` in
/// `modules::get_attr` today), because claiming a module's attribute by the
/// module's name alone is exactly how a program would reach a sibling that
/// refuses it again — a spawn wasted, and the ledger already paid for that
/// lesson once. With one row in the spectrum every answer here is `false`.
pub fn answers(v: &Variant, kind: &str, detail: &str) -> bool {
    match kind {
        "module" => served_module(v, module_of(detail)),
        "module-attr" => false,
        _ => CAPS.iter().any(|(c, _, kinds)| v.caps.contains(c) && kinds.contains(&kind)),
    }
}

/// The verdict of every rung on a program that THIS binary's walker stopped on
/// `(kind, detail)` — empty kind: nothing stopped it.
///
/// Rungs below this binary get no verdict of their own (the walker reports
/// only THIS binary's first blocker, which says nothing about a smaller
/// sibling's capabilities) and are marked so; the floor rule never routes
/// there anyway. lypning-mp's row is the same decision `engine_for` has
/// always made; CPython's is always yes.
pub fn verdicts(kind: &str, detail: &str, imports: &[String]) -> Vec<Verdict> {
    let me = self_index();
    let mut out = Vec::with_capacity(SPECTRUM.len() + 1);
    for (i, v) in SPECTRUM.iter().enumerate() {
        let vd = if i < me {
            Verdict::no(v.name, "floor", "below the routing binary")
        } else if kind.is_empty() {
            Verdict::ok(v.name)
        } else if cpython_only(kind) || kind == "syntax" || kind == "error" {
            Verdict::no(v.name, kind, detail)
        } else if i == me || !answers(v, kind, detail) {
            Verdict::no(v.name, kind, detail)
        } else if let Some(m) = imports.iter().find(|m| !served_module(v, m)) {
            Verdict::no(v.name, "module", &format!("import {m}"))
        } else {
            Verdict::ok(v.name)
        };
        out.push(vd);
    }
    out.push(Verdict::ok(CPYTHON_NAME));
    out
}

/// `Route.engine` from the verdict vector: the first rung that can run the
/// program AT OR ABOVE this binary — the floor rule. A router never sends a
/// program to a variant smaller than itself: the running binary's blocks are
/// already paid for.
fn engine_from_verdicts(vs: &[Verdict]) -> Engine {
    let me = self_index();
    vs.iter()
        .skip(me)
        .find(|v| v.kind.is_empty())
        .and_then(|v| Engine::from_name(v.engine))
        .unwrap_or(Engine::CPython)
}

/// The chain the dispatcher walks after `after` refused AT RUNTIME with
/// `kind` — the rule both dispatchers use, so it is spelled here once and the
/// Python side is held to it by a cross-product test.
///
/// A kind in `ONLY_CPYTHON_KINDS` rules out every reimplementation. Otherwise:
/// each later Rust sibling whose STATIC verdict was "can run" (it already
/// satisfied the imports and every static kind) AND whose capabilities are a
/// strict superset of the refusing rung's — a sibling built with the same
/// `cap-*` set cannot answer at runtime what this one could not, and trying it
/// is a spawn wasted; then lypning-mp if it can import everything, then
/// CPython. There is no tier between the spectrum and CPython: lypning-mp left
/// the chain on 2026-09-04 (it is the oracle now), so a refusal a larger sibling
/// cannot answer costs a CPython spawn — which is exactly what makes
/// `conformance --plan` rank the build order by real cost.
pub fn chain_after(after: &str, kind: &str, verdicts: &[Verdict]) -> Vec<&'static str> {
    let order = engine_order();
    let start = order.iter().position(|e| *e == after).map(|i| i + 1).unwrap_or(order.len() - 1);
    let rest = &order[start..];
    if cpython_only(kind) {
        return vec![CPYTHON_NAME];
    }
    let after_caps: &[&str] = SPECTRUM.iter().find(|v| v.name == after).map(|v| v.caps).unwrap_or(&[]);
    let mut out = Vec::new();
    for e in rest {
        if *e == CPYTHON_NAME {
            continue;
        }
        let caps = SPECTRUM.iter().find(|v| v.name == *e).map(|v| v.caps).unwrap_or(&[]);
        let gains = caps.iter().any(|c| !after_caps.contains(c));
        if gains && verdicts.iter().any(|v| v.engine == *e && v.kind.is_empty()) {
            out.push(*e);
        }
    }
    out.push(CPYTHON_NAME);
    out
}

fn finish_route(kind: String, detail: String, imports: Vec<String>) -> Route {
    let verdicts = verdicts(&kind, &detail, &imports);
    let engine = engine_from_verdicts(&verdicts);
    Route { engine, kind, detail, imports, verdicts }
}

#[cfg(test)]
mod spectrum_tests {
    use super::*;

    #[test]
    fn this_binary_is_a_row_of_the_table_it_carries() {
        assert_eq!(SPECTRUM[self_index()].name, SELF);
        assert_eq!(crate::err::ENGINE, SELF);
    }

    #[test]
    fn caps_are_cumulative_and_every_cap_is_declared() {
        for w in SPECTRUM.windows(2) {
            for c in w[0].caps {
                assert!(w[1].caps.contains(c), "{} has {c} but {} does not", w[0].name, w[1].name);
            }
        }
        for v in SPECTRUM {
            for c in v.caps {
                assert!(CAPS.iter().any(|(name, _, _)| name == c), "{c} is not in CAPS");
            }
        }
    }

    #[test]
    fn the_floor_rule_and_the_chain_reproduce_the_three_tier_decisions() {
        // nothing blocks: this binary runs it
        let vs = verdicts("", "", &[]);
        assert_eq!(engine_from_verdicts(&vs), Engine::Rust(self_index()));
        // a module no Rust variant serves: CPython, because there is no tier
        // between the spectrum and CPython any more
        let vs = verdicts("module", "import re", &["re".to_string()]);
        assert_eq!(engine_from_verdicts(&vs), Engine::CPython);
        // a module nobody but CPython has
        let vs = verdicts("module", "import subprocess", &["subprocess".to_string()]);
        assert_eq!(engine_from_verdicts(&vs), Engine::CPython);
        // a semantic refusal skips everything
        let vs = verdicts("set-order", "x", &[]);
        assert_eq!(engine_from_verdicts(&vs), Engine::CPython);
        // Runtime chain. lypning-l is no longer capability-identical — it
        // carries `cap-collections` — so from the core a runtime refusal on a
        // program lypning-l can statically RUN now tries lypning-l before
        // CPython. That is `chain_after`'s "strictly more capable" rule doing
        // what it says, and both dispatchers apply it (the Python side is
        // `engines.chain_after_refusal`, held to this by a cross-product test).
        // It costs one spawn on a kind lypning-l cannot answer either, which is
        // the price of the rule being about capability sets rather than about
        // this one kind; the CAPS `kinds` column is where a future capability
        // says which runtime refusals it DOES answer.
        // Asked of the row that HAS a larger sibling; the top row's chain is
        // CPython and nothing else, which the assertions below cover.
        let vs_ok = verdicts("", "", &["os".to_string()]);
        let after_core: Vec<&str> = if self_index() == 0 {
            vec!["lypning-l", CPYTHON_NAME]
        } else {
            vec![CPYTHON_NAME]
        };
        assert_eq!(chain_after(SELF, "bigint", &vs_ok), after_core);
        assert_eq!(chain_after(SELF, "bigint", &vs), vec![CPYTHON_NAME]);
        assert_eq!(chain_after(SELF, "set-order", &vs), vec![CPYTHON_NAME]);
        assert_eq!(chain_after("nonesuch", "bigint", &vs), vec![CPYTHON_NAME]);
    }

    #[test]
    fn the_c_names_are_the_names() {
        assert_eq!(SPECTRUM.len(), SPECTRUM_C.len());
        for (v, c) in SPECTRUM.iter().zip(SPECTRUM_C) {
            assert_eq!(c.to_str().unwrap(), v.name);
        }
    }

    #[test]
    fn with_identical_capabilities_the_floor_rule_never_picks_the_larger_sibling() {
        // Row 1 has exactly row 0's caps, so from row 0 every program that row 0
        // refuses is refused by row 1 too, and the engine is never lypning-l.
        // This is what makes step 5 behaviour-free; it stops holding the day
        // lypning-l gains a capability, which is the point.
        if self_index() != 0 {
            return;
        }
        for (kind, detail, imports) in [
            ("module", "import re", vec!["re".to_string()]),
            ("module", "import subprocess", vec!["subprocess".to_string()]),
            ("class", "class definition", vec![]),
            ("bigint", "x", vec![]),
        ] {
            let vs = verdicts(kind, detail, &imports);
            assert_ne!(engine_from_verdicts(&vs), Engine::Rust(1), "{kind}");
            assert_eq!(vs[1].kind, vs[0].kind, "{kind}: rows 0 and 1 must agree");
        }
    }

    #[test]
    fn the_caps_this_binary_was_built_with_are_its_row() {
        let built: Vec<&str> = SELF_CAPS.split(',').filter(|s| !s.is_empty()).collect();
        let mut declared: Vec<&str> = SPECTRUM[self_index()].caps.to_vec();
        declared.sort();
        assert_eq!(built, declared, "build.rs and route::SPECTRUM disagree about {SELF}");
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
    /// Every rung's verdict on this program, in `engine_order()` — what
    /// `engine` was derived from, and what the dispatcher walks after a
    /// RUNTIME refusal (`chain_after`). Both dispatchers read this same vector.
    pub verdicts: Vec<Verdict>,
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
///
/// **`random` left this table on 2026-09-02, and is deliberately NOT here.**
/// MicroPython's generator is not MT19937, so any *seeded* stream it answers
/// is a plausible wrong number at exit 0 — and whether a program is seeded
/// cannot be decided statically. A `random.seed` marker was tried and defeated
/// by every spelling it could not see: `from random import *`,
/// `getattr(random, "seed")`, a bound name `s = random.seed`, and any
/// parse-time blocker (`class C: pass` beside the seed), which stops the walker
/// before a marker is set. Tier 1 serves the seeded-integer subset
/// (`random.rs`) and CPython serves the rest; an unseeded stream costs one
/// CPython spawn more than it did, which is the price of never being wrong.
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
    // Built-in types on lypning-mp carry no `__module__`/`__doc__`, so the
    // getattr-with-default idiom prints the default at exit 0. `dunder-attr`
    // (the names mp answers, `__name__`/`__class__`) still falls through.
    "dunder-missing",
    // lypning-mp ignores every encoding argument that is not UTF-8: measured
    // 2026-08-30, `bytes('a', 'bogus')` answers b'a' where CPython raises
    // LookupError, and latin-1/utf-16/ascii all come back as the UTF-8 bytes.
    "encoding",
    "exception-chaining",
    // `is` between two equal immutables not provably the same object. The kind
    // only fires on that ambiguous case, and it is exactly where lypning-mp
    // answers wrongly: its small-int boxing makes `int('1000') is 1000` True
    // where CPython says False. Measured 2026-08-30 on lypning-mp-i386.
    "identity",
    "int-div-precision",
    // The message names an iterator type CPython spells from a family
    // (`list_iterator`, …) and lypning-mp spells as `iterator` — measured, so
    // its answer is the same wrong text this engine refused to print.
    "iterator-type-name",
    "json",
    "nan-identity",
    // A sort over a NaN is the sort algorithm's answer, not Python's, and
    // lypning-mp's algorithm differs from timsort: `sorted([3,1,nan,2])` is
    // `[1, nan, 2, 3]` there and `[1, 2, 3, nan]` in CPython. Measured
    // 2026-08-30.
    "nan-order",
    "percent-format",
    // Every refusal `random.rs` raises — an unseeded stream, a step, a seed
    // that is not an int, a count past 64 bits. The module is off lypning-mp's
    // table because its generator is not MT19937; a RUNTIME refusal must not
    // undo that by falling one tier instead of two.
    "random",
    "repr-unicode",
    "set-method",
    "set-order",
];

/// Does this refusal kind rule out every tier but CPython? See [`ONLY_CPYTHON_KINDS`].
pub fn only_cpython(kind: &str) -> bool {
    ONLY_CPYTHON_KINDS.contains(&kind)
}

/// Can lypning-mp import everything this program imports? The static router
/// asks this before naming the tier (`engine_for`); the dispatcher must ask it
/// again when a RUNTIME refusal falls onward, or a program routed to tier 1
/// on its imports lands on a tier those imports had already ruled out — a
/// seeded `random` stream that hits `bigint` in its own arithmetic, say.
pub fn micropython_imports(imports: &[String]) -> bool {
    imports.iter().all(|m| MICROPYTHON_MODULES.contains(&m.as_str()))
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
            finish_route(kind, detail, imports)
        }
        Err(ref e) if matches!(e.kind(), ErrKind::Syntax { .. }) => {
            let (line, msg) = match e.kind() {
                ErrKind::Syntax { line, msg } => (*line, msg.clone()),
                _ => unreachable!(),
            };
            // A syntax error is not a capability gap. CPython owns it, because
            // its message is the one the caller expects to read.
            // `syntax` is in neither kind table, so every rung but CPython
            // refuses it and the verdicts say so.
            finish_route("syntax".into(), format!("line {line}: {msg}"), scan_imports(src))
        }
        Err(other) => finish_route("error".into(), other.to_string(), imports),
        Ok(body) => {
            let mut req = Requirements::default();
            walk_block(&body, &mut req);
            imports = req.imports.iter().cloned().collect();
            match req.blocker {
                None => finish_route(String::new(), String::new(), imports),
                Some((kind, detail)) => finish_route(kind, detail, imports),
            }
        }
    }
}

/// The constructs a second reimplementation is KNOWN to get wrong lived here as
/// `MICROPYTHON_UNSAFE` — a table whose only job was to keep a program off the
/// MicroPython tier. That tier left the chain on 2026-09-04, so the table
/// decided nothing and is gone; leaving a table wired into the walker that
/// changes no route is the inert contradiction this file already paid for once.
///
/// The KNOWLEDGE is not lost, and is more load-bearing than before:
/// `ONLY_CPYTHON_KINDS` (below) rules those constructs out of every Rust
/// variant, not just of the departed tier, and `.github/known-mismatches.json`
/// holds the oracle's 79 measured divergences in 34 named families — the list
/// a larger variant must implement exactly or refuse.


/// Which engine should run a program whose walker stopped on `kind`?
///
/// With lypning-mp out of the chain there is one question left: can any Rust
/// variant at or above the router answer it (`verdicts`), or is it CPython's?
/// This is now only the CPython-only check; the per-variant answer lives in
/// `answers`. The old `match kind` arm that named the MicroPython tier is gone
/// with the tier — and with it `mp_risk`, whose whole job was to keep a program
/// OFF that tier. The knowledge those tables held (which constructs a second
/// reimplementation gets wrong) is not lost: it is `ONLY_CPYTHON_KINDS` below,
/// which now rules out every Rust variant too, and `.github/known-mismatches.json`.
fn cpython_only(kind: &str) -> bool {
    ONLY_CPYTHON_KINDS.contains(&kind) || CPYTHON_ONLY_KINDS.contains(&kind)
}

#[derive(Default)]
struct Requirements {
    imports: BTreeSet<String>,
    blocker: Option<(String, String)>,
    /// Families from `.github/known-mismatches.json` this program's SOURCE
    /// `import random as r` — bound name to module, so the construct matchers
    /// below can see through the alias. `r.seed(7)` defeated both the dotted
    /// `random.seed` marker and the battery's own source regex: py-0e241643581e
    /// reached lypning-mp and printed a different stream at exit 0.
    aliases: Vec<(String, String)>,
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
            for (path, bound) in names {
                req.imports.insert(path.to_string());
                if bound.as_ref() != path.as_ref() {
                    req.aliases.push((bound.to_string(), path.to_string()));
                }
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
    // A capability's own method names are not on any of the probe types below,
    // because the probes are plain values: `Counter.most_common` lives on a
    // dict whose tag says Counter, and a probe dict has no tag. Without this
    // the variant that HAS the capability would block the very program it was
    // built to run.
    #[cfg(feature = "cap-collections")]
    if crate::collections::known_method(name) {
        return true;
    }
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
/// `aliases` is `import x as y`, so `r.seed(7)` after `import random as r`
/// resolves to the module and its attributes are decided, not guessed at as
/// method names — the third spelling in the ledger (`py-0e241643581e`).
fn resolve_module(e: &Expr, aliases: &[(String, String)]) -> Option<crate::value::Value> {
    match e {
        Expr::Name(n) => {
            let name = aliases
                .iter()
                .find(|(a, _)| a == n.as_ref())
                .map(|(_, p)| p.as_str())
                .unwrap_or(n.as_ref());
            crate::modules::MODULES
                .iter()
                .find(|x| **x == name)
                .map(|m| crate::value::Value::Module(m))
        }
        Expr::Attr(b, n) => match crate::modules::get_attr(&resolve_module(b, aliases)?, n) {
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
            // A module attribute is decidable; anything else is a method name.
            if let Some(crate::value::Value::Module(m)) = resolve_module(b, &req.aliases) {
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
            walk_expr(func, req);
            for a in args {
                walk_expr(a, req);
            }
            for (_name, v) in kwargs {
                walk_expr(v, req);
            }
            for d in dstar {
                walk_expr(d, req);
            }
        }
        Expr::Bin(_op, a, b) => {
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
    // Per STATEMENT, not per line: `x = 1; import random` is how a one-liner
    // imports, and this scan is the router's only sight of the imports when a
    // parse-time blocker has stopped the walker — missing one here sent a
    // seeded `random` program to the tier whose generator is not MT19937.
    for stmt in src.lines().flat_map(|l| l.split(';')) {
        let t = stmt.trim_start();
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
