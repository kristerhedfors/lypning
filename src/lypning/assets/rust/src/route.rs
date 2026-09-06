//! The classifier — the "mixture" in the mixture of Pythons.
//!
//! Routing is a STATIC analysis over lypning's own front end, not a heuristic over
//! the program text. That choice is the whole design:
//!
//!   * lypning's parser already reports the exact construct that would stop it, as
//!     `unsupported: <kind>: <detail>`. Asking the parser is therefore an exact
//!     answer to "can lypning run this", not a guess — and it costs one parse, no
//!     process spawn, no execution.
//!   * A larger sibling on the spectrum cannot be asked the same way (it is a
//!     separate binary), so its reach is a capability TABLE (`CAPS`), derived
//!     from the `cap-*` features it was built with and checked against
//!     measured conformance by `lypning conformance --mixture both`.
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
/// Python side's `engines.SPECTRUM`, in this order, pinned by test.
pub const SPECTRUM: &[Variant] = &[
    Variant { name: "lypning", caps: &[] },
    Variant {
        name: "lypning-l",
        // Alphabetical, which is the order `build.rs` emits `LYPNING_CAPS` in —
        // so the binary's own answer, this table and `engines.VARIANT_CAPS` are
        // one list and not three that happen to agree.
        caps: &[
            "cap-collections",
            "cap-csv",
            "cap-glob",
            "cap-hashlib",
            "cap-pathlib",
            "cap-re",
        ],
    },
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
///
/// `cap-pathlib` serves the `pathlib` MODULE and, like `cap-collections`,
/// answers no runtime kind: every `pathlib` refusal it raises is a shape it
/// declines BECAUSE CPython is the one that can answer it — a `ValueError`'s
/// wording, a directory order, a `.stat()` — and not because these bytes are
/// missing, so a sibling would refuse it identically and listing the kind would
/// cost a spawn to be told no twice.
///
/// `cap-re` serves the `re` module AND its matcher, and answers no runtime kind
/// either: every `re:` refusal it raises is a shape CPython owns (a construct
/// of a later slice, a version-shaped message, a step budget), there is no rung
/// above lypning-l to carry a kind to, and `chain_after` a runtime `re:`
/// refusal is `[cpython]` by construction.
///
/// `cap-csv` serves the `csv` MODULE — its two READERS, and only the
/// attributes [`MODULE_ATTRS`] names, which is the row that keeps this one
/// honest: `csv` is the first module whose surface a variant serves only PART
/// of, and a `module` claim alone sent the six corpus writers into lypning-l to
/// find that out at runtime. It answers no runtime kind, for the same reason as
/// the three above: a `csv:` refusal is a `csv.Error` message, a dialect, or a
/// file written under an open handle, which is the FILE object's divergence
/// rather than the reader's. It also serves
/// `open(newline='')`, whose refusal kind is `open-newline` and which is
/// deliberately NOT listed here: the kinds column is read by `answers`, which
/// decides STATIC routing, and no walk ever produces `open-newline`. The
/// RUNTIME chain off it already reaches lypning-l, because `chain_after` tries
/// every sibling with a strictly larger `cap-*` set.
///
/// `cap-hashlib` serves the `hashlib` MODULE — four CONSTRUCTORS, and only the
/// names [`MODULE_ATTRS`] lists, for the same reason `csv` needs a row: adding
/// the module to this table admits every hashlib program into `lypning-l`,
/// including the ones reaching for `hashlib.new`, `algorithms_guaranteed` or a
/// variable-length digest, which this capability does not serve and which would
/// otherwise be found out at RUNTIME. It answers no runtime kind: a `hashlib:`
/// refusal is a keyword CPython owns or an attribute whose answer is CPython's
/// to print, and there is no rung above `lypning-l` to carry the kind to.
///
/// `cap-glob` serves the `glob` MODULE and answers no runtime kind either. It
/// is the SECOND module served only in part, and it needs no [`MODULE_ATTRS`]
/// row to say so: the walk below carries [`GLOB_SERVED`] unconditionally, so
/// the core blocks `module-attr: glob.translate` out of its own walk exactly
/// where lypning-l would. The one thing about `glob` a router would like to
/// have seen coming — `glob-order`, a result whose ORDER the program can
/// observe — is not a runtime kind at all: [`walk_expr`] decides it
/// STATICALLY, before the program starts, and the kind is in
/// [`ONLY_CPYTHON_KINDS`] because no reimplementation can reproduce
/// `os.scandir` order, so no sibling could answer it either.
pub const CAPS: &[(&str, &[&str], &[&str])] = &[
    ("cap-collections", &["collections"], &[]),
    ("cap-csv", &["csv"], &[]),
    ("cap-glob", &["glob"], &[]),
    ("cap-hashlib", &["hashlib"], &[]),
    ("cap-pathlib", &["pathlib"], &[]),
    ("cap-re", &["re"], &[]),
];

/// The module attributes a capability answers, for the modules whose surface is
/// small enough to write down EXACTLY. Carried by every variant, like
/// [`SPECTRUM`] and [`CAPS`], because the binary that routes is the CHEAPEST
/// one — the core, which has none of these capabilities compiled in and
/// therefore no other way to know.
///
/// It exists because a `module` claim alone is too coarse in one direction that
/// costs an exit code. The core stops on `module: import csv` and [`CAPS`] says
/// lypning-l serves `csv`, so every csv program routed INTO lypning-l — the six
/// corpus WRITERS included, which `csv.rs` declines by omission. On lypning-l
/// that decline is a `module-attr` its own walk sees, but the walk only runs
/// under `route`; a `-c` run reaches it at RUNTIME, and a runtime refusal after
/// a side effect the commit barrier has let through is exit 1 for a program
/// that works. Naming the served attributes here moves the block back into the
/// core's walk, where it is static and free.
///
/// A module NOT listed here claims its whole surface, which is the behaviour
/// every module had before this table: `re` and `pathlib` are deliberately
/// absent, because their surfaces are large, partly value-dependent, and an
/// attribute wrongly left out of a list is a program sent to CPython that
/// lypning-l would have run. The rule for adding a row is that the list can be
/// held to the capability's own `module_attr` by a test on the variant that has
/// it — `csv.rs` does, in `the_route_table_names_exactly_what_is_served`.
///
/// `glob` is absent for the opposite reason: it is small enough, but the walk
/// already carries [`GLOB_SERVED`] unconditionally and decides `glob.<n>` from
/// it — with the KIND the runtime would have raised — several arms before
/// [`capability_module`] is reached. A row here would be a second table saying
/// the same thing, and the two would drift.
pub const MODULE_ATTRS: &[(&str, &[&str])] = &[
    (
        "csv",
        &["DictReader", "QUOTE_ALL", "QUOTE_MINIMAL", "QUOTE_NONE", "QUOTE_NONNUMERIC", "reader"],
    ),
    // Held to `hashlib::SERVED` by
    // `hashlib::tests::the_route_table_names_exactly_what_is_served`. Every
    // other name on the module — `new`, `algorithms_guaranteed`,
    // `algorithms_available`, `blake2b`, `blake2s`, `shake_128`, `shake_256`,
    // `sha3_*`, `sha224`, `sha384`, `pbkdf2_hmac`, `scrypt`, `file_digest` —
    // is blocked HERE, in the core's walk, and never reaches the variant.
    ("hashlib", &["md5", "sha1", "sha256", "sha512"]),
];

/// Does some variant on the spectrum answer `module.name`, as far as
/// [`MODULE_ATTRS`] can say? `true` for every module the table does not list —
/// the safe direction, since it leaves routing exactly as it was.
fn served_attr(module: &str, name: &str) -> bool {
    match MODULE_ATTRS.iter().find(|(m, _)| *m == module) {
        Some((_, attrs)) => attrs.contains(&name),
        None => true,
    }
}

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

/// Was this program handed here on `import hashlib` alone — the one import
/// that admits a program onto a rung this commit added?
///
/// The general form of the question (*any* module a capability serves) is the
/// right one and it does not work, which is measured rather than argued. Made
/// generic it refused three corpus programs that the CORE runs and matches —
/// py-a17ba3c48307 and py-a28bd1e6292d, which reach `csv.writer(...).writerow`
/// only after `sys.argv[1]` has already raised IndexError, and py-2dbc1fa3548e,
/// which reads `Fraction.numerator` in a branch a missing file stops it from
/// entering. All three die identically on both engines today; hoisting a
/// blocker they never reach into a static refusal makes the larger variant do
/// worse than the smaller one on a program both ran, which invariant 10 does
/// not allow. `cap-glob` measured the same shape over `MODULE_ATTRS` and
/// narrowed the same way.
///
/// So: `hashlib` only, behind the capability's own feature, and the identical
/// hole for `collections`, `csv`, `pathlib` and `re` is left OPEN and written
/// down rather than closed by a rule that costs three matches.
#[cfg(feature = "cap-hashlib")]
fn admitted_by_a_capability(req: &Requirements) -> bool {
    req.imports.contains("hashlib")
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
/// there anyway. CPython's row is always yes.
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

/// `stop` is the refusal every rung shares, when the walk found one — a
/// blocker that is NOT the one the blocker slot reports.
///
/// The two are different questions and this is the only place they meet.
/// `Route::kind` is what stopped THIS binary and is first-wins, because that is
/// the row `--plan` ranks; the stop is what stops EVERY binary, and in the core
/// it is never first — `import glob` is, and `lypning-l` answers that one.
/// Reporting only the blocker sent every such program to `lypning-l` for a
/// refusal (`docs/HILLCLIMB.md`, the `cap-glob` review). So the blocker slot is
/// left exactly as the walk filled it and the VERDICTS are overwritten: what
/// the stop names is a refusal no rung of the spectrum answers — `os.scandir`
/// order, a keyword only CPython serves, an attribute nothing here has, a
/// pattern nothing here compiles — and a verdict vector that says so routes to
/// CPython through [`engine_from_verdicts`] and shortens the chain through
/// [`chain_after`] with no special case in either.
fn finish_route(
    kind: String,
    detail: String,
    imports: Vec<String>,
    reads_stdin: bool,
    stop: Option<(String, String)>,
) -> Route {
    let mut verdicts = verdicts(&kind, &detail, &imports);
    if let Some((k, d)) = stop {
        for v in verdicts.iter_mut().skip(self_index()) {
            if v.engine != CPYTHON_NAME {
                *v = Verdict::no(v.engine, &k, &d);
            }
        }
    }
    let engine = engine_from_verdicts(&verdicts);
    Route { engine, kind, detail, imports, verdicts, reads_stdin }
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
        // a module only the larger variant serves: from the core, that
        // variant (the variant's own walker never raises this blocker)
        let vs = verdicts("module", "import re", &["re".to_string()]);
        if self_index() == 0 {
            assert_eq!(engine_from_verdicts(&vs), Engine::Rust(1));
        }
        // a module nobody but CPython has: CPython, because there is no tier
        // between the spectrum and CPython any more
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
            ("module", "import ctypes", vec!["ctypes".to_string()]),
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
    fn a_pattern_this_engine_cannot_compile_is_a_static_block_and_a_good_one_is_not() {
        // Every assertion below runs on EVERY variant, and that is the point of
        // #48: the walk that decides a pattern is `repat.rs`, which the core
        // carries too, so the binary that ROUTES answers the same question the
        // binary that RUNS would have. While this was `cfg(feature = "cap-re")`
        // the core answered `lypning-l` for the second list and the chain then
        // reached lypning-l with `-c`, where the refusal fired at runtime.
        //
        // `re` is the sibling's module, so a bare import routes there and so
        // does an ordinary matcher call — the pattern is decided by the walker,
        // before the program starts, in every spelling a walk can see.
        for src in [
            "import re\nprint(re.sub('a', 'b', 'a'))",
            "import re as x\nprint(x.findall(r'\\d+', 'a1'))",
            "from re import search\nprint(search('a', 'a'))",
            "from re import compile as c\nprint(c('a'))",
            "import re, os\nos.makedirs('d1/d2')\nprint(re.sub('a', 'b', 'a'))",
            "import re\nprint(re.escape('a.'), re.I | re.M, re.purge())",
            "import re\nprint('ok')",
            "re = 'a,b'\nprint(re.split(','))",
        ] {
            let r = route(src);
            let l = r.verdicts.iter().find(|v| v.engine == "lypning-l").unwrap();
            assert!(l.kind.is_empty(), "{src}: {} {}", l.kind, l.detail);
        }
        // …and a construct of a later slice, a pattern CPython itself rejects,
        // or a BYTES pattern is a route to CPython from every rung — which is
        // what keeps a runtime refusal from landing after a side effect the
        // commit barrier has let through. The last row is issue #48's own
        // reproduction: through the chain it was
        // `lypning: error: … reached after output was already flushed`, exit 1,
        // where CPython answers `[b'a', b'a']` at exit 0.
        for src in [
            "import re\nprint(re.search(r'(?P<a>x)', 'x'))",
            "import re\nprint(re.findall(r'(?<=a)b', 'ab'))",
            "import re as x\nprint(x.sub(r'(a)\\1', 'b', 'aa'))",
            "from re import compile as c\nprint(c('a{2,1}'))",
            "import re, os\nos.makedirs('d1/d2')\nprint(re.sub(r'(?=a)', 'b', 'a'))",
            "import re, os\nos.makedirs('d')\nprint(re.findall(b'a', b'aa'))",
        ] {
            let r = route(src);
            assert_eq!(r.engine, Engine::CPython, "{src}");
            // The KIND is this binary's own first blocker and differs by rung —
            // `module: import re` in the core, `re: …` where `re` is served —
            // because that is the row `--plan` ranks. The VERDICT is the
            // spectrum's, and it is the same on both.
            let l = r.verdicts.iter().find(|v| v.engine == "lypning-l").unwrap();
            assert_eq!(l.kind, "re", "{src}");
        }
    }

    #[test]
    fn reads_stdin_is_generous_and_false_for_a_program_that_cannot_read_it() {
        for src in [
            "import sys\nprint(sys.stdin.read())",
            "import sys as s\nfor l in s.stdin: print(l)",
            "from sys import stdin\nprint(stdin.read())",
            "print(input())",
            "print(open(0).read())",
            "import os\nprint(os.read(0, 10))",
            "import fileinput\nfor l in fileinput.input(): print(l)",
            "print(open('/dev/stdin').read())",
            "import sys\nprint(getattr(sys, 'stdin').read())",
            "class C: pass\nimport sys\nsys.stdin.read()",
            // A parse-time blocker stops the walk before it sees the call;
            // the text scan is what answers for these.
            "class C: pass\nprint(open(0).read())",
            "class C: pass\nimport os\nprint(os.read(0, 10))",
            // `input` bound to a name and called through it: the bare
            // identifier, not the spelling `input(`, is what reads the pipe.
            "f = input\nprint(int(f()) * 10**30)",
            "g = (input)\nprint(g())",
        ] {
            assert!(route(src).reads_stdin, "{src}");
        }
        for src in [
            "print(1)",
            // The word, not the substring: these cannot read stdin.
            "inputs = [1]\nprint(inputs[0])",
            "user_input = 'x'\nprint(user_input)",
            "import collections\nprint(collections.Counter('ab'))",
            "import sys\nprint(sys.argv[1:])",
            "import re\nprint(re.escape('a'))",
        ] {
            assert!(!route(src).reads_stdin, "{src}");
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
    /// The program can read stdin — `sys.stdin`, `input()`, `open(0)`,
    /// `fileinput`, `/dev/stdin` — as far as a walk and a text scan can tell.
    /// The dispatcher reads this before it forks an intermediate rung: a piped
    /// stdin is buffered once and replayed to every rung ONLY when the program
    /// can consume it, because the read blocks until the writer closes, and
    /// `tail -f x | lypning run -c 'print(1)'` must print rather than wait
    /// (`main.rs:exec_engine`). Generous by design — an over-match costs one
    /// read of bytes the program was going to read anyway; a miss is the
    /// exhausted-stream bug back — and the Python dispatcher applies the same
    /// bit (`engines.dispatch`), so the two agree on when to buffer.
    pub reads_stdin: bool,
}

/// Modules the oracle lypning-mp serves: its frozen `micropython/lib` shim
/// stdlib plus the MicroPython built-ins its variant enables. Not a routing
/// table — nothing routes to the oracle (`ORACLE_NAME`) — but kept: it is the
/// import surface `lypning conformance --engine lypning-mp` grades within, and
/// a build order for a larger Rust variant. A table because the oracle is a
/// separate binary that cannot be asked.
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
/// before a marker is set. The Rust core serves the seeded-integer subset
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
/// every larger Rust sibling.
///
/// Falling through assumes the next rung is at least as correct as the one that
/// refused, and for a capability gap it is — "I have no `collections`", and
/// `lypning-l` has it. These are not capability gaps. Each names a behaviour
/// CPython has that is subtle enough that the refusal exists BECAUSE a
/// reimplementation gets it wrong, so a larger build of the same
/// reimplementation gets it wrong too and would answer at exit 0 rather than
/// refusing. The oracle's measured divergences (`.github/known-mismatches.json`)
/// are where these kinds come from.
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
    // A `glob.glob()` result in a position that would show the ORDER of two or
    // more matched paths. The same fact as `set-order` about a different system
    // call: CPython's answer comes from `os.scandir`, so a second
    // reimplementation is no likelier to reproduce it than the first was.
    // `glob.rs`, and the static blocker in `walk_expr` below.
    "glob-order",
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

/// Does this refusal kind rule out every Rust variant? See [`ONLY_CPYTHON_KINDS`].
pub fn only_cpython(kind: &str) -> bool {
    ONLY_CPYTHON_KINDS.contains(&kind)
}

/// Can the oracle lypning-mp import everything this program imports? Not a
/// routing question — nothing routes to the oracle — but the reach that
/// `lypning conformance --engine lypning-mp` grades within, read on the Python
/// side through `routing.micropython_modules()`.
pub fn micropython_imports(imports: &[String]) -> bool {
    imports.iter().all(|m| MICROPYTHON_MODULES.contains(&m.as_str()))
}

/// Constructs no Rust variant has and no `cap-*` feature adds, so a program
/// using one goes straight to CPython rather than paying a larger sibling's
/// spawn to be told no. Distinct from [`ONLY_CPYTHON_KINDS`]: those are
/// behaviours a reimplementation gets WRONG; these are ones it does not have.
///
/// `decorator` and `generator` were here and should not have been: the rung
/// that then stood below the spectrum ran both, and ten corpus programs were
/// sent past it (CHANGELOG.md, 2026-08-25, #15). Today a refusal of either is
/// decided like any other capability gap — by the siblings' verdicts.
///
/// `async` stays, and for a reason that shows what this list is really for:
/// `async def` may *parse* somewhere, so the syntax is not the problem —
/// `asyncio` is what the program needs to do anything, and no Rust variant has
/// it. This list is about where a program ENDS UP, not about what a parser
/// accepts.
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
    // Textual, so it is available on every path below — a parse-time blocker
    // stops the walk before it sees anything, and the dispatcher's question
    // about stdin still has to be answered for that program.
    let reads_stdin = mentions_stdin(src);
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
            finish_route(kind, detail, imports, reads_stdin, None)
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
            finish_route(
                "syntax".into(),
                format!("line {line}: {msg}"),
                scan_imports(src),
                reads_stdin,
                None,
            )
        }
        Err(other) => finish_route("error".into(), other.to_string(), imports, reads_stdin, None),
        Ok(body) => {
            let mut req = Requirements::default();
            // Textual and computed once, before the walk, because a `def sorted`
            // BELOW a call still decides what that call meant inside a function.
            // Only for a source that mentions the module at all, so a program
            // with no glob in it pays one substring search.
            req.glob_wrappers = trusted_wrappers(src);
            walk_block(&body, &mut req);
            imports = req.imports.iter().cloned().collect();
            let reads_stdin = reads_stdin || req.reads_stdin;
            let stop = req.spectrum_stop.take();
            match req.blocker {
                None => finish_route(String::new(), String::new(), imports, reads_stdin, stop),
                Some((kind, detail)) => {
                    finish_route(kind, detail, imports, reads_stdin, stop)
                }
            }
        }
    }
}

/// The textual half of `Route::reads_stdin`: the spellings a walk cannot see
/// (`getattr(sys, "stdin")`, `sys.__dict__["stdin"]`, a program whose parse
/// stopped before the walk began — where `open(0)` and `os.read(0, …)` have
/// only this scan to be seen by). Substrings, not tokens, on purpose: `f.read(0)`
/// on a file over-matches, and that costs one read of a pipe the caller
/// filled for the program anyway.
///
/// `input` is matched as a bare identifier, not only as the call `input(`:
/// `f = input; print(int(f()) * 10**30)` reads the pipe through the alias,
/// refuses bigint AFTER it has, and a dispatcher that did not buffer stdin for
/// it handed CPython an exhausted stream — EOFError at exit 1 where CPython
/// prints the number. `stdin` is already a substring above, which is wider
/// than the bare word.
fn mentions_stdin(src: &str) -> bool {
    ["stdin", "fileinput", "input(", "/dev/fd/0", "open(0", "read(0"]
        .iter()
        .any(|s| src.contains(s))
        || mentions_word(src, "input")
}

/// `\bword\b` without a regex: `word` in `src` with no identifier character
/// (`[A-Za-z0-9_]`) on either side. `inputs`, `user_input` and `input_` do not
/// match; `f = input`, `(input)` and `input;` do.
fn mentions_word(src: &str, word: &str) -> bool {
    let is_ident = |c: char| c.is_ascii_alphanumeric() || c == '_';
    let mut from = 0;
    while let Some(i) = src[from..].find(word) {
        let start = from + i;
        let end = start + word.len();
        let before = src[..start].chars().next_back().map_or(false, is_ident);
        let after = src[end..].chars().next().map_or(false, is_ident);
        if !before && !after {
            return true;
        }
        from = start + 1;
    }
    false
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
/// `answers`. The old `match kind` arm that named the oracle's rung is gone
/// with the rung — and with it `mp_risk`, whose whole job was to keep a program
/// OFF it. The knowledge those tables held (which constructs a second
/// reimplementation gets wrong) is not lost: it is `ONLY_CPYTHON_KINDS` below,
/// which now rules out every Rust variant too, and `.github/known-mismatches.json`.
fn cpython_only(kind: &str) -> bool {
    ONLY_CPYTHON_KINDS.contains(&kind) || CPYTHON_ONLY_KINDS.contains(&kind)
}

/// Whether this binary has a reason to record what a name in a `hashlib`
/// program holds. `false` in the core, where it folds the guard in
/// [`Requirements::bind_pattern`] — and, with the `cfg` on the variant itself,
/// the whole `PatLit::HashCtor` arm — away at compile time.
#[cfg(feature = "cap-hashlib")]
const TRACKS_HASHLIB: bool = true;
#[cfg(not(feature = "cap-hashlib"))]
const TRACKS_HASHLIB: bool = false;

/// A pattern a walk could read: the text of a `str` literal, the fact that it
/// was a `bytes` one — which is all `re` needs, since a bytes pattern refuses
/// whatever its content — or the TYPE of any other literal, which is what
/// `glob` needs, because its refusal names the type it was handed.
///
/// Read by TWO capabilities now, which is why it is no longer behind
/// `cap-re`: `re.sub(P, …)` and `glob.glob(P)` ask the same question of the
/// same binding, and `glob`'s half has to be answered in the CORE. THREE with
/// `PatLit::HashCtor` below, which is not a pattern and is here anyway.
#[derive(Clone)]
enum PatLit {
    Str(std::rc::Rc<str>),
    Bytes,
    Other(&'static str),
    /// `f = hashlib.md5`, `from hashlib import sha256 as f` — the served
    /// CONSTRUCTOR a name holds, so a call through the name is decided by the
    /// same walk that decides the dotted spelling.
    ///
    /// Not a literal, and in this table anyway, because what a name holds is a
    /// question of ORDER and SCOPE and [`Requirements::bind_pattern`] is the
    /// one place that already gets both right: a rebinding above the call
    /// replaces it, a parameter of that spelling gives it up, and a binding
    /// made inside a `def` is a binding of that `def` alone. A second table
    /// beside it would have to repeat all three, and `glob_names` — which
    /// does not — is the reason `from glob import glob` inside a function
    /// still shadows the module at the top of the file.
    #[cfg(feature = "cap-hashlib")]
    HashCtor(&'static str),
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
    /// `from re import search as s` — bound name to the `re` function it
    /// names, so a bare `s(…)` is seen as the call it is and its PATTERN can
    /// be decided here, before the program starts. In EVERY variant since #48:
    /// the core has no matcher, but `repat.rs` gives it the parser, which is
    /// all a walk ever needed.
    re_names: Vec<(String, String)>,
    /// `P = r'…'` — a pattern LITERAL bound to a name, so that `re.sub(P, …)`
    /// and `glob.glob(P)` are decided by the same walk that decides
    /// `re.sub(r'…', …)` and `glob.glob('…')`. `None` is a name a walk cannot
    /// read a literal out of (a loop variable, a parameter, anything
    /// computed), and it is the value that MATTERS: a name this table does not
    /// resolve keeps the runtime refusal, which is the backstop. Only filled
    /// once `re` or `glob` is imported, so a program that never touches either
    /// module pays one set lookup per binding and no allocation — and a literal
    /// bound ABOVE that import line is therefore not in it.
    ///
    /// ONE table, read in source order and saved across a nested scope by
    /// [`enter_scope`](Requirements::enter_scope): the entry in it at the call
    /// is the binding in force at the call, and a name bound inside a `def`, a
    /// `lambda` or a comprehension is a name of that scope alone.
    pats: Vec<(String, Option<PatLit>)>,
    /// `from glob import glob [as g]` — the bound name of a glob FUNCTION, so
    /// that a bare `g(...)` is seen as the call it is. Without it the order
    /// blocker below would miss the one spelling that hides the module name.
    glob_names: Vec<(String, String)>,
    /// The call nodes the parent blessed as order-blind, by identity. The walk
    /// borrows one live AST for its whole run, so no node is freed and no
    /// address is reused; nothing is dereferenced through these.
    glob_blessed: Vec<*const Expr>,
    /// The refusal that stops EVERY rung of the spectrum, as `(kind, detail)`,
    /// recorded even when an EARLIER blocker won the `--plan` row. A program
    /// whose first blocker is something lypning-l runs anyway (the walker is
    /// deliberately pessimistic about methods) must still not reach the call it
    /// would have refused halfway through.
    ///
    /// [`route`] reads it, and has to: in the CORE the first blocker for a
    /// capability program is `module: import X`, which `lypning-l` answers — so
    /// the blocker slot alone routes the program to a sibling that refuses it.
    /// One wasted spawn when the sibling refuses cleanly; **exit 1** when it
    /// does not, because the chain reaches that sibling with `-c` and its
    /// refusal then fires at RUNTIME, which after a committed `os.makedirs` is
    /// a number invariant 2 forbids retrying. That is issue #48, and this slot
    /// is the whole of the answer to it: every variant computes the spectrum's
    /// verdict, so there is still exactly ONE routing decision.
    ///
    /// [`static_stop_check`] reads it too, for the `-c` entry that is not
    /// routed at all.
    ///
    /// It carries the KIND as well as the detail, and it is not glob's alone:
    /// `glob-order`, a glob keyword no rung serves, a glob attribute nothing
    /// here has, an `re` pattern literal no rung can compile (#48), and — since
    /// `cap-hashlib` — a `hashlib` constructor or attribute no rung has.
    /// Each keeps the kind the runtime would have raised, so a program is
    /// refused with the same line one in-process run earlier.
    spectrum_stop: Option<(String, String)>,
    /// Which order-blind wrappers are still the BUILTIN, one bit per index into
    /// [`ORDER_BLIND`]. `sorted` rebound to something that shows its argument's
    /// order would make the blessing below a lie — see [`trusted_wrappers`].
    /// Zero, the default, trusts none of them, which is right for a program
    /// that never mentions the module and has nothing to bless.
    glob_wrappers: u16,
    /// Every name some scope declared `global`, so [`Requirements::leave_scope`]
    /// can give it up again on the way out. It is the one spelling that binds
    /// OUT THERE from IN HERE, which is exactly what the save/restore below
    /// would otherwise undo. `nonlocal` cannot appear: `parse.rs` refuses it.
    pat_globals: Vec<String>,
    /// See `Route::reads_stdin`. The walk's half: `sys.stdin`, `input()`,
    /// `open(0)`, `os.read(0, …)`, `fileinput`; the text scan is the other.
    reads_stdin: bool,
}

impl Requirements {
    fn block(&mut self, kind: &str, detail: String) {
        if self.blocker.is_none() {
            self.blocker = Some((kind.to_string(), detail));
        }
    }

    /// A refusal the whole spectrum shares, for the router AND for the run.
    /// `block` is first-wins because `--plan` ranks what a program hit FIRST;
    /// the stop slot is separate because the run has to refuse whether or not
    /// something else was hit earlier.
    ///
    /// Named for the SLOT and not for `glob`, which was its only writer until
    /// `re` (#48) and `cap-hashlib`: those capabilities ask the same question
    /// of the walk — *is there a refusal here that no rung answers, and can it
    /// be raised before the interpreter exists?* — and one slot is what lets
    /// [`static_stop_check`] answer it once.
    fn stop(&mut self, kind: &str, detail: String) {
        self.block(kind, detail.clone());
        self.stop_only(kind, detail);
    }

    /// The stop without the blocker, for the places that have ALREADY blocked
    /// correctly on both variants — the `from glob import …` arm, where the
    /// core blocks `module` and lypning-l blocks `module-attr` and neither
    /// should be displaced from the `--plan` row this walk reports.
    fn stop_only(&mut self, kind: &str, detail: String) {
        if self.spectrum_stop.is_none() {
            self.spectrum_stop = Some((kind.to_string(), detail));
        }
    }

    fn block_glob_order(&mut self) {
        self.stop("glob-order", GLOB_ORDER.to_string());
    }

    /// Replace a `module: import X` blocker with a `module-attr: X.name` one.
    ///
    /// The walk keeps the FIRST blocker, and for a capability module the first
    /// is always the import — which a larger sibling answers, so the program
    /// routes there and finds out at runtime that the ATTRIBUTE is not served.
    /// This is the one case where a later blocker is strictly more informative
    /// than the one already recorded: same module, and `answers` returns false
    /// for `module-attr`, so the program goes to CPython in one step instead of
    /// two. Only over a `module` blocker naming the SAME module — a blocker on
    /// some other import is a different program's problem and stays put.
    fn escalate(&mut self, module: &str, name: &str) {
        let same = match &self.blocker {
            Some((k, d)) => k == "module" && module_of(d) == module,
            None => true,
        };
        if same {
            self.blocker = Some(("module-attr".to_string(), format!("{module}.{name}")));
        }
    }

    /// Record what `name` now holds: a pattern literal, or `None` for a
    /// binding a walk cannot read. The walk is in SOURCE ORDER, so the value
    /// in force at the call is the one the call is decided against, and a
    /// rebinding before the call replaces the literal rather than stacking on
    /// it. A name bound only AFTER its use is never resolved, which is the
    /// safe direction: the runtime refusal still catches it. Every spelling
    /// that binds arrives here — an assignment, a `for` target, a `with … as`,
    /// a parameter, an `import … as`, an `except … as`, a `def`'s own name.
    fn bind_pattern(&mut self, name: &str, lit: Option<PatLit>) {
        if !self.imports.contains("re")
            && !self.imports.contains("glob")
            && !(TRACKS_HASHLIB && self.imports.contains("hashlib"))
        {
            return;
        }
        match self.pats.iter_mut().find(|(n, _)| n == name) {
            Some(slot) => slot.1 = lit,
            None => self.pats.push((name.to_string(), lit)),
        }
    }

    fn pattern_named(&self, name: &str) -> Option<PatLit> {
        self.pats
            .iter()
            .find(|(n, _)| n == name)
            .and_then(|(_, v)| v.clone())
    }

    /// Every name a parameter list binds is a name whose value arrives at
    /// CALL time, so it is not the module-level literal that shares its
    /// spelling. Blocking on that literal would send a program this engine
    /// runs to CPython — the direction `resolve_module` was written to stop.
    ///
    /// It binds INSIDE ITS OWN FUNCTION and nowhere else, which is what the
    /// [`enter_scope`](Self::enter_scope) around the body makes true. Without
    /// that, one `def f(p)` anywhere in the file gave up a module-level
    /// `p = "[z-a]"` for every call in it, and the pattern was refused at
    /// runtime instead: past a committed barrier, exit 1.
    fn shadow_params(&mut self, params: &crate::ast::Params) {
        for n in &params.names {
            self.bind_pattern(n, None);
        }
    }

    /// Enter a nested scope — a `def` body, a `lambda` body, a comprehension —
    /// and hand back the table to put back on the way out.
    ///
    /// The table is read in SOURCE ORDER, so a binding above a call is the one
    /// the call is decided against and a binding below it is already too late
    /// to matter. Scope is the other half of that rule and was missing: a name
    /// bound in here is a name of in here, so neither direction of the leak is
    /// right. A parameter or a local escaping outward gave up a module-level
    /// literal that was live at the call (a runtime refusal past the barrier,
    /// exit 1); a local literal escaping outward answered for a module-level
    /// name it never held (a call decided against a pattern that was never in
    /// force at it).
    #[must_use]
    fn enter_scope(&self) -> Vec<(String, Option<PatLit>)> {
        self.pats.clone()
    }

    /// Leave it, restoring what the enclosing scope could read — minus every
    /// name any scope declared `global`, which the walk has no call graph to
    /// place. Giving that name up costs a CPython spawn; keeping a literal it
    /// may no longer hold would cost an answer.
    fn leave_scope(&mut self, saved: Vec<(String, Option<PatLit>)>) {
        self.pats = saved;
        if self.pat_globals.is_empty() {
            return;
        }
        let names = std::mem::take(&mut self.pat_globals);
        for n in &names {
            self.bind_pattern(n, None);
        }
        self.pat_globals = names;
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
                if path.as_ref() == "fileinput" {
                    req.reads_stdin = true;
                }
                // An alias is an `as` clause and NOTHING else. `import a.b`
                // binds the name `a` (parse.rs does this correctly, as Python
                // does), so comparing the binding against the full dotted path
                // recorded a false alias "os" -> "os.path" — and then
                // `resolve_module` turned the name `os` into the module
                // `os.path`, so `os.path.basename(...)` blocked as
                // `module-attr: os.path.path` and the program went to CPython
                // for a call this engine answers. Compare against the FIRST
                // component, which is what the name is actually bound to.
                if bound.as_ref() != path.split('.').next().unwrap_or(path.as_ref()) {
                    req.aliases.push((bound.to_string(), path.to_string()));
                }
                // The `as` name is a binding like any other, so it gives up
                // whatever literal that spelling held above it.
                req.bind_pattern(bound, None);
                if !crate::modules::MODULES.contains(&path.as_ref()) {
                    req.block("module", format!("import {path}"));
                }
            }
        }
        Stmt::FromImport { module, names } => {
            req.imports.insert(module.to_string());
            for (_, bind) in names {
                req.bind_pattern(bind, None);
            }
            match module.as_ref() {
                "fileinput" => req.reads_stdin = true,
                "sys" if names.iter().any(|(n, _)| matches!(n.as_ref(), "stdin" | "__stdin__")) => {
                    req.reads_stdin = true
                }
                "re" => {
                    for (n, bind) in names {
                        if crate::repat::is_matcher(n) {
                            req.re_names.push((bind.to_string(), n.to_string()));
                        }
                    }
                }
                // Both halves of `from hashlib import …`, for the reason the
                // `glob` arm below has both: a served name is a CONSTRUCTOR
                // whose call this walk still has to decide, and an unserved
                // one is a refusal that must be raised before the interpreter
                // exists. `stop_only` and not `stop` because the arm
                // further down already blocks it correctly on both variants —
                // `module` in the core, `module-attr` on lypning-l — and
                // neither should be displaced from the `--plan` row.
                #[cfg(feature = "cap-hashlib")]
                "hashlib" => {
                    for (n, bind) in names {
                        match crate::hashlib::SERVED.iter().copied().find(|x| *x == n.as_ref()) {
                            Some(c) => req.bind_pattern(bind, Some(PatLit::HashCtor(c))),
                            None => req.stop_only("module-attr", format!("hashlib.{n}")),
                        }
                    }
                }
                "glob" => {
                    for (n, bind) in names {
                        // EVERY served name, not just the two the order rule
                        // cares about: `from glob import escape` binds a call
                        // whose arguments this walk still has to decide.
                        if GLOB_SERVED.contains(&n.as_ref()) {
                            req.glob_names.push((bind.to_string(), n.to_string()));
                        } else {
                            // `stop_only`, because the arm below already blocks
                            // this correctly on both variants — `module` in the
                            // core, `module-attr` on lypning-l — and neither
                            // should be displaced from the `--plan` row.
                            req.stop_only("module-attr", format!("glob.{n}"));
                        }
                    }
                }
                _ => {}
            }
            if !crate::modules::MODULES.contains(&module.as_ref()) {
                req.block("module", format!("from {module} import …"));
                // `from csv import writer` on a variant that does not serve
                // `csv`: the import alone would route to the sibling that does,
                // which does not serve THIS name either. See `MODULE_ATTRS`.
                for (n, _) in names {
                    if !served_attr(module, n) {
                        req.escalate(module, n);
                        break;
                    }
                }
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
            // BEFORE `walk_target`, which gives up every name it binds: this
            // one READS the table (`f = g` after `g = hashlib.md5`), and
            // `g = g` would otherwise be decided against the slot the target
            // had just cleared.
            #[cfg(feature = "cap-hashlib")]
            let ctor = hash_ctor(value, req);
            for t in targets {
                walk_target(t, req);
            }
            #[cfg(feature = "cap-hashlib")]
            if let Some(c) = ctor {
                for t in targets {
                    if let Target::Name(n) = t {
                        req.bind_pattern(n, Some(PatLit::HashCtor(c)));
                    }
                }
            }
            // After `walk_target`, which cleared every name it bound: a
            // LITERAL is the one value a walk can read back, so it is put back.
            if let Some(lit) = pattern_literal(value) {
                for t in targets {
                    if let Target::Name(n) = t {
                        req.bind_pattern(n, Some(lit.clone()));
                    }
                }
            }
        }
        Stmt::AugAssign { target, value, .. } => {
            // Value first, because that is the order the two run in: `p += x`
            // reads `p`, evaluates `x`, then rebinds. Walking the target first
            // gave the name up before the expression that used it was decided.
            walk_expr(value, req);
            walk_target(target, req);
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
            // The ITERABLE is evaluated before the target is ever bound, in
            // Python and so here: `for p in sorted(glob.glob(p))` globs the
            // pattern `p` held on the way in. Walking the target first gave
            // that binding up before the call that read it was decided, and
            // the pattern was refused at runtime instead — past a committed
            // barrier, exit 1.
            walk_expr(iter, req);
            walk_target(target, req);
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
        Stmt::Def { name, body, params } => {
            // The defaults are evaluated OUT HERE, at definition time, so they
            // are walked before the scope is entered.
            for d in params.defaults.iter().flatten() {
                walk_expr(d, req);
            }
            // …and the function object is bound out here too, so a `def p():`
            // gives up a `p = "[z-a]"` above it exactly as any other rebinding
            // of the name would.
            req.bind_pattern(name, None);
            let saved = req.enter_scope();
            req.shadow_params(params);
            walk_block(body, req);
            req.leave_scope(saved);
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
                    // A DOTTED name is resolved the way an attribute is, not by
                    // throwing the prefix away: `except json.JSONDecodeError`
                    // reduced to `JSONDecodeError`, which is not a builtin, and
                    // blocked a program this engine runs. Resolve the module and
                    // ask it for the leaf; fall back to the old rule when the
                    // prefix is not a module we serve, so this can only remove
                    // refusals it can justify.
                    let ok = match k.rsplit_once('.') {
                        Some((prefix, leaf)) => {
                            match crate::modules::MODULES.iter().find(|m| **m == prefix) {
                                Some(m) => crate::modules::get_attr(
                                    &crate::value::Value::Module(m),
                                    leaf,
                                )
                                .is_ok(),
                                None => crate::builtins::is_exception_name(leaf),
                            }
                        }
                        None => crate::builtins::is_exception_name(k),
                    };
                    if !ok {
                        req.block("exception", format!("except {k}"));
                    }
                }
                // `except E as p` binds `p`, and Python deletes it again at
                // the end of the handler — either way the literal it used to
                // hold is not what the name reads afterwards.
                if let Some(n) = &h.name {
                    req.bind_pattern(n, None);
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
        // `global p` makes a binding in here a binding out there, and the walk
        // has no call graph to say whether it ran before the call that reads
        // `p`. Give the name up in this scope and in the one restored above it.
        Stmt::Global(names) => {
            for n in names {
                if !req.pat_globals.iter().any(|g| g == n.as_ref()) {
                    req.pat_globals.push(n.to_string());
                }
                req.bind_pattern(n, None);
            }
        }
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
        // A name this binds no longer holds whatever literal it held: a `for`
        // target, an augmented assignment, a `with … as`, a `del`, a
        // comprehension's variable and an assignment of anything computed all
        // arrive here, and all of them make the name unreadable to a walk.
        // `Stmt::Assign` puts a literal back afterwards.
        Target::Name(n) => req.bind_pattern(n, None),
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
                // Safe to admit unconditionally, and the reason is the exact
                // one the pathlib note below turns on: an unmodelled receiver
                // REFUSES rather than errors. `x = 3; x.__name__` answers
                // `unsupported: dunder-attr: int.__name__` at exit 90, which
                // the chain recovers for one spawn — where `.name` or `.errno`
                // would raise AttributeError at exit 1, which it never
                // recovers. Only names whose miss is a refusal belong here.
                | "__name__"
        )
}

/// A `pathlib` name — `.name`, `.parts`, `.with_suffix` — admitted to the
/// optimistic union above ONLY for a program that imports `pathlib`.
///
/// Unconditionally would be wrong in the direction that matters. `.name` is an
/// ordinary attribute on other objects and this engine answers it on none of
/// them, so a program that says `f.name` is blocked today and routed to
/// CPython, which answers it; admitting the name for every receiver would run
/// the program here instead and stop at an `AttributeError` — exit 1, the
/// program's own exit, which the chain never retries. The import is what makes
/// the optimism honest.
#[cfg(feature = "cap-pathlib")]
fn pathlib_method(req: &Requirements, n: &str) -> bool {
    req.imports.contains("pathlib") && crate::pathlib::known_method(n)
}
#[cfg(not(feature = "cap-pathlib"))]
fn pathlib_method(_req: &Requirements, _n: &str) -> bool {
    false
}

/// The Match/Pattern names — `.group`, `.span`, `.start`, `.string` — admitted
/// ONLY for a program that imports `re`, by the same argument as
/// [`pathlib_method`]: `.start` and `.end` are ordinary names on other objects
/// that this engine answers on none of them.
#[cfg(feature = "cap-re")]
fn re_method(req: &Requirements, n: &str) -> bool {
    req.imports.contains("re") && crate::re::known_method(n)
}
#[cfg(not(feature = "cap-re"))]
fn re_method(_req: &Requirements, _n: &str) -> bool {
    false
}

/// `.hexdigest`, `.digest` and `.digest_size`, admitted ONLY for a program that
/// imports `hashlib`, by the same argument as [`pathlib_method`].
///
/// **`.name` and `.update` are deliberately not here, for opposite reasons.**
/// `.update` needs no admitting: it is already in the union, because a `dict`
/// and a `set` have one. `.name` is the one this list must NOT be optimistic
/// about — it is an ordinary attribute on a file object, a module and an
/// exception, this engine answers it on none of them, and admitting it would
/// run a hashlib program here only to stop at an `AttributeError` at exit 1,
/// which the chain never retries, where CPython answers. So `h.name` is a
/// static `method` block that costs a CPython spawn and is never wrong, and
/// `hashlib.rs` still answers it for a run that entered as `-c`.
#[cfg(feature = "cap-hashlib")]
fn hash_method(req: &Requirements, n: &str) -> bool {
    req.imports.contains("hashlib") && matches!(n, "digest" | "digest_size" | "hexdigest")
}
#[cfg(not(feature = "cap-hashlib"))]
fn hash_method(_req: &Requirements, _n: &str) -> bool {
    false
}

/// The `re` function a call names — `re.sub`, `x.sub` after `import re as x`,
/// or a name bound by `from re import sub [as s]` — together with the
/// expression in its PATTERN position, wherever that is spelled.
///
/// Only for a program that imports `re`: the import is what makes the name mean
/// the module, exactly as for [`pathlib_method`]; `re.split(",")` on a string
/// someone called `re` is a str method and runs here.
fn re_call_of<'a>(
    func: &Expr,
    args: &'a [Expr],
    kwargs: &'a [(std::rc::Rc<str>, Expr)],
    req: &Requirements,
) -> Option<&'a Expr> {
    if !req.imports.contains("re") {
        return None;
    }
    let named = match func {
        Expr::Attr(b, n) => match &**b {
            Expr::Name(base) => {
                let name = req
                    .aliases
                    .iter()
                    .find(|(a, _)| a == base.as_ref())
                    .map(|(_, p)| p.as_str())
                    .unwrap_or(base.as_ref());
                name == "re" && crate::repat::is_matcher(n)
            }
            _ => false,
        },
        Expr::Name(n) => req.re_names.iter().any(|(bound, _)| bound == n.as_ref()),
        _ => false,
    };
    if !named {
        return None;
    }
    // The pattern is the first positional argument of every one of them, and
    // `pattern=` when there is no positional at all — the spelling CPython
    // and this engine both accept and the walk would otherwise not see.
    args.first()
        .or_else(|| kwargs.iter().find(|(k, _)| k.as_ref() == "pattern").map(|(_, v)| v))
}

/// The text of an f-string that has NO interpolations — `f"[z-a]"`, which is a
/// string literal with a prefix on it and nothing else. A join rather than one
/// part because adjacent literals concatenate: `f"[z" "-a]"` parses to two
/// [`FPart::Lit`]s, and `f""` to none.
///
/// `None` the moment one `{…}` is in it. That part is built when the program
/// runs, so its text is not a walk's to read and the call keeps the runtime
/// backstop — the same direction every other unreadable value takes.
fn fstring_text(parts: &[FPart]) -> Option<std::rc::Rc<str>> {
    let mut out = String::new();
    for part in parts {
        match part {
            FPart::Lit(s) => out.push_str(s),
            FPart::Expr { .. } => return None,
        }
    }
    Some(out.into())
}

fn pattern_literal(e: &Expr) -> Option<PatLit> {
    match e {
        Expr::Str(s) => Some(PatLit::Str(s.clone())),
        Expr::Bytes(_) => Some(PatLit::Bytes),
        // Both halves of the f-string are a `str`; only one of them is a
        // VALUE. A constant f-string is a literal and answers its text; one
        // with an interpolation is computed and answers the type alone. Before
        // this arm both fell to [`literal_type`], which answers the type and
        // never a value — so `glob.glob(f"[z-a]")` passed the type gate with no
        // text to scan, [`glob_pattern_block`] never ran, and the pattern was
        // refused at runtime instead: past a committed barrier, exit 1.
        Expr::FString(parts) => Some(match fstring_text(parts) {
            Some(s) => PatLit::Str(s),
            None => PatLit::Other("str"),
        }),
        e => literal_type(e).map(PatLit::Other),
    }
}

/// The TYPE of a literal expression, spelled the way `value::type_name` spells
/// it — which is the way the refusal that names it spells it too.
///
/// An f-string IS a `str` whatever is interpolated into it, so it answers the
/// type here; whether its TEXT can be read is [`pattern_literal`]'s question
/// and not this one's. Everything that is not a literal answers `None` and
/// keeps the runtime refusal.
fn literal_type(e: &Expr) -> Option<&'static str> {
    Some(match e {
        Expr::Str(_) | Expr::FString(_) => "str",
        Expr::Bytes(_) => "bytes",
        Expr::Int(_) => "int",
        Expr::Float(_) => "float",
        Expr::True | Expr::False => "bool",
        Expr::None => "NoneType",
        Expr::List(_) => "list",
        Expr::Tuple(_) => "tuple",
        Expr::Set(_) => "set",
        Expr::Dict(_) | Expr::DictUnpack(_) => "dict",
        _ => return None,
    })
}

/// A pattern literal this engine cannot compile, as a STATIC block.
///
/// It is the same refusal `re.compile` would raise one in-process run later,
/// moved to where a walk can see it — and the move is the point. A runtime
/// refusal that lands after a side effect the commit barrier has already let
/// through (`os.makedirs` before `re.sub`) cannot fall onward: it becomes exit
/// 1, which the chain never retries. `route.rs` learned that from the `re`
/// surface's first shape, where the whole matcher was a static row for exactly
/// this reason.
///
/// So the walk reads the pattern wherever a walk honestly can: a literal in
/// the pattern position, a literal `pattern=` keyword, a literal bound to a
/// NAME above the call, and a `bytes` literal in any of those — which refuses
/// whatever its content. A pattern BUILT at runtime, or read out of a name a
/// walk cannot follow, keeps the runtime refusal; that is what the backstop is
/// for, and taking a static route on a guess would send a program this engine
/// runs to CPython instead.
///
/// **Every variant runs this walk, and that is issue #48's fix.** It used to be
/// behind `cfg(feature = "cap-re")`, so only `lypning-l` computed it — and
/// `lypning-l` is not the binary that routes. The core stopped on `module:
/// import re`, read `cap-re` off `lypning-l`'s row and sent the program there;
/// the chain hands a rung its program with `-c`, not `run`, so `lypning-l` was
/// never asked to route it either, and the block fired at RUNTIME instead —
/// after `os.makedirs()` had committed the barrier, which is exit 1 and a
/// chain that cannot fall onward. The pattern parser is `repat.rs` for exactly
/// this reason, and the verdict goes in the SPECTRUM STOP slot rather than the
/// blocker slot, because in the core the blocker is `module: import re` and
/// `lypning-l` answers that one.
fn re_pattern_block(
    req: &mut Requirements,
    func: &Expr,
    args: &[Expr],
    kwargs: &[(std::rc::Rc<str>, Expr)],
) {
    let lit = match re_call_of(func, args, kwargs, req) {
        // `P = r'…'` above the call. `pattern_named` answers only for a name
        // whose binding a walk could read; anything else is `None` here and
        // keeps the runtime refusal, which is what the backstop is for.
        Some(Expr::Name(n)) => req.pattern_named(n),
        Some(other) => pattern_literal(other),
        None => None,
    };
    match lit {
        Some(PatLit::Str(src)) => {
            if let Err(e) = crate::repat::precompile(&src) {
                if let crate::err::ErrKind::Unsupported { kind, detail } = e.kind() {
                    req.stop(kind, detail.clone());
                }
            }
        }
        // A BYTES pattern is servable by CPython and by nothing in this
        // engine, and it refuses whatever its content and whatever the
        // subject — which makes it exactly the shape that must not wait for
        // runtime to say so. Same kind, same detail, one in-process run
        // earlier.
        Some(PatLit::Bytes) => {
            req.stop("re", "bytes pattern or subject (re over bytes)".to_string())
        }
        // A literal of any other type is `glob`'s half of this table and not
        // `re`'s: what `re.compile(5)` raises is a `TypeError` whose wording is
        // CPython's, so the runtime refusal is the one that must fire. A name
        // holding a hashlib constructor is the same case one type further out:
        // `re.sub(f, …)` after `f = hashlib.md5` is CPython's TypeError too.
        #[cfg(feature = "cap-hashlib")]
        Some(PatLit::HashCtor(_)) => {}
        Some(PatLit::Other(_)) | None => {}
    }
}


// ---- the glob order rule ---------------------------------------------------
//
// `glob.glob()` returns a list whose ORDER is the filesystem's, so the whole
// question the capability has to answer is: can this program see the order?
// It is decided HERE, in the walk, before anything runs — never at runtime,
// because a runtime refusal reached after `os.makedirs` has committed the
// barrier is exit 1 with the output discarded, and the chain never retries
// that. `glob.rs` says the rest.
//
// **None of it is behind `cfg(feature = "cap-glob")`, and that is deliberate.**
// It is pure walker logic — a position test over the AST with no glob
// implementation behind it — so it belongs in the routing table every variant
// carries whole, next to `SPECTRUM` and `CAPS`. The core is the binary
// `engines.route()` asks; while this rule was gated, the core saw only
// `module: import glob`, read `cap-glob` off `lypning-l`'s row and predicted
// `lypning-l` for programs `lypning-l` refuses with `glob-order` — one wasted
// spawn each. That is the same defect a small no-json variant had when it
// routed `import json` past its larger sibling, and the fix is the same one:
// every binary computes the whole spectrum's verdict, not just its own.

/// The order-blind wrappers: builtins whose answer is the same for every
/// permutation of the list they are handed. A `glob.glob(...)` call that is a
/// DIRECT argument of one of these cannot show its order, so it is served;
/// everywhere else the call is a `glob-order` blocker.
///
/// It is exactly the list the refusal below names, and `tests/test_glob_grid.py`
/// has a row for each: a name here that this engine does not serve would bless a
/// call and then refuse the wrapper, which is a refusal for the wrong reason.
/// `frozenset` is such a name and is deliberately absent.
///
/// **A name belongs here only if its RESULT carries no order — not merely if
/// its ANSWER is order-blind.** `set` was here and does not qualify, and the
/// difference cost a correct program: `set(glob.glob(p))` answers the same set
/// for every permutation, but the SET is then handed back to the program, and
/// this engine's `Value::Set` is insertion-ordered where CPython's is
/// hash-ordered — so `print(set(glob.glob('*.py')))` is a RUNTIME `set-order`
/// refusal. Runtime is the one place this rule may not land: after
/// `os.mkdir("D")` has committed the write barrier a refusal is exit 1 with the
/// directory left behind and no answer, where the core refused cleanly at 90
/// and the chain got the answer from CPython. Every other name here answers a
/// SCALAR (`bool`, `len`, `min`, `max`, `any`, `all`, `sum`, and `in` in
/// [`walk_expr`]) or a sorted list (`sorted`), and a scalar has no order to
/// leak. `frozenset` was already excluded for this shape; `set` is the same
/// shape and the doc comment did not say so, which is why it survived.
///
/// The flag is whether the name also admits `glob.iglob`, which answers a
/// GENERATOR in CPython. Every position that CONSUMES its argument reads a
/// generator and a list identically, so it is `true`; the two that ASK ABOUT
/// the container rather than its elements are not:
///
///   * `len` — `TypeError` on a generator, so the answer is not even the same
///     kind of thing.
///   * `bool` — a generator is ALWAYS truthy, so `bool(glob.iglob('nope*'))`
///     is `True` in CPython and was `False` here, at exit 0. The empty match
///     set is the normal case for a glob, so this was the common path.
const ORDER_BLIND: &[(&str, bool)] = &[
    ("all", true),
    ("any", true),
    ("bool", false),
    ("len", false),
    ("max", true),
    ("min", true),
    ("sorted", true),
    ("sum", true),
];

/// The detail of the static blocker, spelled once so `--plan` ranks one row for
/// it however it was reached — including the `iglob` half, which is a narrower
/// rule and not a second kind.
const GLOB_ORDER: &str = "glob() order is filesystem-defined and not \
     reproducible; served only inside sorted(), bool(), len(), min(), max(), \
     any(), all(), sum() or the right of `in` — and iglob() answers a \
     generator, which len() and bool() do not read as a list";

/// The `glob` attributes lypning-l serves, and therefore the only ones ANY rung
/// of the spectrum answers. Every other name — `translate`, `glob0`, `glob1`,
/// `_ishidden` — is a `module-attr` refusal.
///
/// **The table is here and not in `modules.rs`, and that is the whole point.**
/// `modules::MODULES` is per-variant and has no `glob` row in the CORE, so
/// [`resolve_module`] answers `None` for `glob.` in the one binary
/// `engines.route()` asks. A program that reached `glob.translate` was
/// therefore routed to `lypning-l` on the strength of `module: import glob`,
/// ran until the attribute was touched, and refused THERE — after `os.mkdir`
/// had committed the write barrier, which is exit 1 with the directory left
/// behind and no answer, where the core without `cap-glob` refused cleanly at
/// 90 and the chain got the answer from CPython. A router can only read a
/// routing table, so the attribute surface is one; `glob::SERVED` is this list
/// and not a second copy of it.
pub const GLOB_SERVED: &[&str] = &["escape", "glob", "has_magic", "iglob"];

/// The keyword arguments each served name takes. `glob`/`iglob` take
/// `recursive=` and nothing else — `root_dir=`, `dir_fd=` and `include_hidden=`
/// are CPython's and refuse (`glob.rs`) — and `escape`/`has_magic` take none.
fn glob_kw_served(name: &str, k: &str) -> bool {
    matches!(name, "glob" | "iglob") && k == "recursive"
}

/// Does this expression name the `glob` MODULE — through an alias, and only in
/// a program that imported it?
///
/// Textual, and deliberately NOT through [`resolve_module`]: that resolves
/// against `modules::MODULES`, which has no `glob` row in the core, so the core
/// would miss exactly the spelling it is being asked to route.
fn glob_module(b: &Expr, req: &Requirements) -> bool {
    if !req.imports.contains("glob") {
        return false;
    }
    let Expr::Name(base) = b else { return false };
    let m = req
        .aliases
        .iter()
        .find(|(a, _)| a == base.as_ref())
        .map(|(_, p)| p.as_str())
        .unwrap_or(base.as_ref());
    m == "glob"
}

/// Which glob FUNCTION this callee names, if any — one of [`GLOB_SERVED`].
///
/// `glob.glob(...)`, `x.iglob(...)` after `import glob as x`, and a bare
/// `g(...)` bound by `from glob import escape as g`. Only for a program that
/// imports `glob`: the import is what makes the name mean the module, exactly
/// as for [`pathlib_method`].
fn glob_func(func: &Expr, req: &Requirements) -> Option<&'static str> {
    let n: &str = match func {
        Expr::Attr(b, n) if glob_module(b, req) => n.as_ref(),
        Expr::Name(n) => req
            .glob_names
            .iter()
            .find(|(bound, _)| bound == n.as_ref())
            .map(|(_, f)| f.as_str())?,
        _ => return None,
    };
    GLOB_SERVED.iter().copied().find(|x| *x == n)
}

/// Is this call one of the two names that answer a LISTING — and is it `iglob`?
/// The order rule is about those two and no others: `escape` and `has_magic`
/// are pure string algebra and carry no order to show.
fn glob_call(func: &Expr, req: &Requirements) -> Option<bool> {
    match glob_func(func, req)? {
        "glob" => Some(false),
        "iglob" => Some(true),
        _ => None,
    }
}

/// The bracket expression starting at `p[at]`, as `(body, index after ']')`, or
/// `None` when there is no closing `]` at all — in which case the `[` is a
/// literal. The scan is CPython's: a `!` and then a `]` immediately after the
/// `[` are both part of the body, so `[]]` matches a `]` and `[!]]` matches
/// anything else.
///
/// In `route.rs` rather than `glob.rs` for the reason [`GLOB_SERVED`] is: the
/// WALKER runs it, and every variant carries the walker including the one with
/// no glob implementation behind it. `glob::fnmatch` calls this one.
pub fn glob_class(p: &[char], at: usize) -> Option<(&[char], usize)> {
    let mut k = at + 1;
    if k < p.len() && p[k] == '!' {
        k += 1;
    }
    if k < p.len() && p[k] == ']' {
        k += 1;
    }
    while k < p.len() && p[k] != ']' {
        k += 1;
    }
    if k >= p.len() {
        return None;
    }
    Some((&p[at + 1..k], k + 1))
}

const GLOB_RANGE: &str = "a [z-a] range in a pattern (CPython rewrites it)";

/// Every refusal a glob PATTERN can raise on its own, decided from the pattern
/// alone — **the one place that question is asked.** The walker runs it over a
/// literal it can read; `glob::call` runs it over the pattern it was handed,
/// before the first directory is listed. So the two cannot disagree, and the
/// runtime answer stopped depending on what happened to be on disk: a reversed
/// range only ever reached the matcher when some candidate name got far enough
/// into the pattern to test it, which made `glob.glob('[z-a]')` an answer in an
/// empty directory and a refusal in a full one.
///
/// Split on `/` first, because that is what `iglob` does before anything is
/// matched: a `[` in one component and a `]` in the next are two literals and
/// not a class.
pub fn glob_pattern_block(pat: &str) -> Option<&'static str> {
    for comp in pat.split('/') {
        let p: Vec<char> = comp.chars().collect();
        let mut i = 0;
        while i < p.len() {
            if p[i] == '[' {
                if let Some((body, next)) = glob_class(&p, i) {
                    if glob_range_reversed(body) {
                        return Some(GLOB_RANGE);
                    }
                    i = next;
                    continue;
                }
            }
            i += 1;
        }
    }
    None
}

/// Does this bracket body hold a REVERSED range — `[z-a]`, the shape where
/// CPython stops emitting a character class and starts merging chunks?
///
/// The stride is the matcher's own in `glob::class_holds`, which no longer asks
/// this question at all: [`glob_pattern_block`] has already refused every
/// pattern that could have made it say yes, so there is one rule and not two.
fn glob_range_reversed(body: &[char]) -> bool {
    let body = match body.first() {
        Some('!') => &body[1..],
        _ => body,
    };
    let mut k = 0;
    while k < body.len() {
        if k + 2 < body.len() && body[k + 1] == '-' {
            if body[k] > body[k + 2] {
                return true;
            }
            k += 3;
        } else {
            k += 1;
        }
    }
    false
}

/// As much of the pattern argument as a walk can honestly read: the TYPE of a
/// literal — or of a literal bound to a name above the call — and its text when
/// that type is `str`.
///
/// `None` is everything else: a pattern built at runtime, read from `argv`,
/// returned by a call, or held by a name this walk cannot follow. Those keep
/// the runtime refusal, and taking a static route on a guess would send a
/// program this engine runs to CPython instead.
fn glob_arg(e: &Expr, req: &Requirements) -> Option<(&'static str, Option<std::rc::Rc<str>>)> {
    let lit = match e {
        Expr::Name(n) => req.pattern_named(n)?,
        e => pattern_literal(e)?,
    };
    Some(match lit {
        PatLit::Str(s) => ("str", Some(s)),
        PatLit::Bytes => ("bytes", None),
        PatLit::Other(t) => (t, None),
        // `f = hashlib.md5; glob.glob(f)` — a TypeError in CPython, named
        // after the type `value::type_name` gives the value, which is the
        // wording `glob`'s refusal is built from.
        #[cfg(feature = "cap-hashlib")]
        PatLit::HashCtor(_) => ("builtin_function_or_method", None),
    })
}

/// Every refusal an ADMITTED glob call can raise that a walk can decide,
/// hoisted out of the run and into the walk.
///
/// This is the class the position rule left open. `sorted(glob.glob(P, …))` is
/// a blessed position, so the order rule serves it and the program STARTS — and
/// then a refusal reached after `os.mkdir` has committed the write barrier is
/// exit 1 with the directory on disk and no answer, which the chain never
/// retries. `docs/HILLCLIMB.md` iteration 76 rejected the first `cap-glob`
/// attempt for exactly that shape. A static blocker costs the program nothing:
/// it was never started here.
///
/// The three that a walk can see are the three that are LITERAL in the source:
///
///   * the unsupported keyword arguments — `root_dir=`, `dir_fd=`,
///     `include_hidden=` on `glob`/`iglob`, and any keyword at all on `escape`
///     and `has_magic`. The NAME is what refuses, and a keyword's name is
///     spelled at the call site.
///   * the argument count, and the pattern's TYPE when it is a literal.
///   * the pattern itself, when it is a `str` literal (or a name bound to one),
///     through [`glob_pattern_block`] — the same scan `glob::call` runs.
///
/// The order the tests are made in is `glob::call`'s own, so a program is
/// refused with the same line it would have been refused with a run later.
///
/// **A `*`/`**` is not by itself a value the walk cannot read** — what is
/// BEHIND it is the question. A DISPLAY is spelled out in the source, so
/// `*["*.py"]`, `*("*.py",)` and `**{"root_dir": "d"}` are spliced into the
/// positional and keyword lists by [`flatten_call`] and the call is then
/// decided exactly as if the stars had never been typed. Everything else —
/// a name, a call, a comprehension, a `*` inside the display, a dict key that
/// is not a `str` literal — is where the early return still lives.
///
/// **What stays a runtime refusal, and why that is a much smaller surface.**
/// A pattern whose value is computed keeps the type and range checks at
/// runtime; so does a call whose unpacked argument list is itself computed
/// (`a = ["*.py"]; glob.glob(*a)`), where the walk can neither count the
/// positionals nor read the keyword names. Two more are the FILESYSTEM's and no
/// walk could ever hoist them: a directory entry whose name is not valid UTF-8,
/// and a `**` walk deeper than this engine follows. Each is now reachable only
/// from a program whose pattern is dynamic, or whose unpacked argument list is
/// — a far narrower door than one a string literal could walk through.
fn glob_call_block(
    req: &mut Requirements,
    func: &Expr,
    args: &[Expr],
    kwargs: &[(std::rc::Rc<str>, Expr)],
    star: &[usize],
    dstar: &[Expr],
) {
    let Some(name) = glob_func(func, req) else { return };
    let Some((pos, kws)) = flatten_call(args, kwargs, star, dstar) else { return };
    let arg = pos.first().and_then(|e| glob_arg(e, req));
    let detail = match (pos.len(), &arg) {
        (0, _) => Some(format!("glob.{name}() with no pattern")),
        (_, Some((t, _))) if *t != "str" => Some(format!(
            "glob.{name}() over a pattern that is not a str (a {t})"
        )),
        (1, _) => None,
        _ => Some(format!("glob.{name}() with extra positional arguments")),
    };
    let detail = detail
        .or_else(|| match (name, arg.as_ref().and_then(|(_, s)| s.as_ref())) {
            ("glob" | "iglob", Some(s)) => glob_pattern_block(s).map(str::to_string),
            _ => None,
        })
        .or_else(|| {
            kws.iter()
                .find(|(k, _)| !glob_kw_served(name, k))
                .map(|(k, _)| format!("glob.{name}({k}=…)"))
        });
    if let Some(d) = detail {
        req.stop("glob", d);
    }
}

/// Which `hashlib` CONSTRUCTOR this expression names, if any — one of
/// `hashlib::SERVED`.
///
/// Every spelling, because a static check that depends on how a function was
/// SPELLED is a static check with a hole in it. `hashlib.sha256`, `h.sha256`
/// after `import hashlib as h`, a bare `sha256` bound by
/// `from hashlib import sha256`, and `f` after `f = hashlib.md5` are one
/// function under four names, and the last two used to fall through to the
/// runtime backstop — which past a committed `os.mkdir` is exit 1 with the
/// output discarded, the exact shape [`hash_call_block`] exists to prevent.
///
/// The bound names are read out of the binding table rather than a list of
/// their own: `PatLit::HashCtor` is recorded by
/// [`bind_pattern`](Requirements::bind_pattern), so a rebinding, a parameter
/// of the same spelling and a `def`-local binding are all already right here.
#[cfg(feature = "cap-hashlib")]
fn hash_ctor(func: &Expr, req: &Requirements) -> Option<&'static str> {
    match func {
        Expr::Attr(b, n) if hash_module(b, req) => {
            crate::hashlib::SERVED.iter().copied().find(|x| *x == n.as_ref())
        }
        Expr::Name(n) => match req.pattern_named(n) {
            Some(PatLit::HashCtor(c)) => Some(c),
            _ => None,
        },
        _ => None,
    }
}

/// Does `b` name the `hashlib` module — `hashlib.…` or `h.…` after
/// `import hashlib as h`? Only for a program that imports it, exactly as
/// [`glob_module`] requires.
#[cfg(feature = "cap-hashlib")]
fn hash_module(b: &Expr, req: &Requirements) -> bool {
    if !req.imports.contains("hashlib") {
        return false;
    }
    let Expr::Name(base) = b else { return false };
    let m = req
        .aliases
        .iter()
        .find(|(a, _)| a == base.as_ref())
        .map(|(_, p)| p.as_str())
        .unwrap_or(base.as_ref());
    m == "hashlib"
}

/// Everything about a served `hashlib` constructor call that a walk can decide,
/// decided here rather than one statement into a program that has already made
/// a directory (issue #51).
///
/// A hash constructor takes ONE optional positional and no keywords this
/// capability serves. `usedforsecurity=` is the keyword that matters: CPython
/// answers it, and answers it DIFFERENTLY on a FIPS-mode build, where
/// `usedforsecurity=True` on md5 raises instead of hashing. An engine that
/// ignored the flag would be right on this host and wrong on that one, which is
/// exactly the shape invariant 1 says to refuse rather than guess.
///
/// Deliberately blunt about `*` and `**`: a splice is refused rather than
/// flattened, because the only thing this needs to be sure of is that there is
/// nothing here it has not read, and a wrongly-admitted keyword is a wrong
/// answer where a wrongly-refused one is a CPython spawn.
#[cfg(feature = "cap-hashlib")]
fn hash_call_block(
    req: &mut Requirements,
    func: &Expr,
    args: &[Expr],
    kwargs: &[(std::rc::Rc<str>, Expr)],
    star: &[usize],
    dstar: &[Expr],
) {
    let Some(name) = hash_ctor(func, req) else { return };
    let detail = if let Some((k, _)) = kwargs.first() {
        format!("hashlib.{name}({k}=…)")
    } else if !dstar.is_empty() {
        format!("hashlib.{name}(**…), whose keywords a walk cannot read")
    } else if !star.is_empty() {
        format!("hashlib.{name}(*…), whose arguments a walk cannot count")
    } else if args.len() > 1 {
        format!("hashlib.{name}() with extra positional arguments")
    } else {
        return;
    };
    req.stop("hashlib", detail);
}

/// The call's arguments with every `*`/`**` spliced in, as
/// `(positionals, keywords)` — or `None` when one of them holds a value only
/// the run can see.
///
/// A display is a literal: the walk can count `*["*.py"]` and read the keys of
/// `**{"root_dir": "d"}`, so a call spelled that way is exactly as decidable as
/// `glob.glob("*.py", root_dir="d")` and refuses in the walk rather than past a
/// committed barrier. `*a` and `**k` are NAMES, and the binding table this file
/// reads records a name bound to a list or a dict as its TYPE and never its
/// contents — so there is nothing there to read and the runtime backstop is the
/// answer.
///
/// A `**` inside the dict is the same computed value one level down, and it
/// parses as `Expr::DictUnpack` rather than `Expr::Dict`, so it is already
/// `None` here. A `*` inside the display (`*[*a]`) cannot reach this function
/// at all today — `parse.rs` refuses `* in a list display` and `* in a
/// parenthesized display` before the walk runs — and is rejected anyway, since
/// the one thing a splice may assume about a display is that its LENGTH is the
/// source's and not the run's.
///
/// The keyword list it returns is only ever asked for NAMES — [`glob_kw_served`]
/// reads `k` and never `v` — so a duplicate that CPython would reject as
/// `got multiple values` is not this function's to notice.
fn flatten_call<'a>(
    args: &'a [Expr],
    kwargs: &'a [(std::rc::Rc<str>, Expr)],
    star: &[usize],
    dstar: &'a [Expr],
) -> Option<(Vec<&'a Expr>, Vec<(&'a str, &'a Expr)>)> {
    let mut pos: Vec<&Expr> = Vec::with_capacity(args.len());
    for (i, a) in args.iter().enumerate() {
        if !star.contains(&i) {
            pos.push(a);
            continue;
        }
        match a {
            Expr::List(v) | Expr::Tuple(v) => {
                if v.iter().any(|x| matches!(x, Expr::Starred(_))) {
                    return None;
                }
                pos.extend(v.iter());
            }
            _ => return None,
        }
    }
    let mut kws: Vec<(&str, &Expr)> = kwargs.iter().map(|(k, v)| (k.as_ref(), v)).collect();
    for d in dstar {
        let Expr::Dict(pairs) = d else { return None };
        for (k, v) in pairs {
            match k {
                Expr::Str(s) => kws.push((s.as_ref(), v)),
                _ => return None,
            }
        }
    }
    Some((pos, kws))
}

/// Bless the one argument of an order-blind wrapper, if it is a glob call.
///
/// The blessing is by NODE IDENTITY, so it reaches exactly the call the wrapper
/// was handed and not a second one nested inside its arguments:
/// `sorted(f(glob.glob(p)))` blesses nothing, because `f` may show what
/// `sorted` would have hidden.
fn glob_bless(
    req: &mut Requirements,
    func: &Expr,
    args: &[Expr],
    kwargs: &[(std::rc::Rc<str>, Expr)],
    star: &[usize],
    dstar: &[Expr],
) {
    if args.len() != 1 || !star.is_empty() || !dstar.is_empty() {
        return;
    }
    let Expr::Name(w) = func else { return };
    let w = w.as_ref();
    let Some(i) = ORDER_BLIND.iter().position(|(n, _)| *n == w) else { return };
    if req.glob_wrappers & (1 << i) == 0 {
        return;
    }
    let takes_generator = ORDER_BLIND[i].1;
    // `key=` is the trap and it is why this is a table rather than a name test.
    // Python's sort is STABLE and `min`/`max` keep the FIRST extremum, so a tie
    // under a key is resolved by the INPUT order: `sorted(glob.glob('*'),
    // key=len)` is a filesystem order with extra steps. `reverse=` and
    // `default=` do not read the order and are served.
    let kw_ok = kwargs.iter().all(|(k, _)| match w {
        "sorted" => k.as_ref() == "reverse",
        "min" | "max" => k.as_ref() == "default",
        _ => false,
    });
    if !kw_ok {
        return;
    }
    if let Expr::Call { func: inner, .. } = &args[0] {
        match glob_call(inner, req) {
            // `iglob` answers a GENERATOR, and the two names above that ask
            // about the container rather than consume it read one differently
            // from a list — `len()` raises, `bool()` is always True. Decided
            // per NAME in [`ORDER_BLIND`], not by a test spelled here, because
            // the previous spelling covered `len` and silently missed `bool`.
            Some(true) if !takes_generator => {}
            Some(_) => req.glob_blessed.push(&args[0] as *const Expr),
            None => {}
        }
    }
}

/// The spectrum stop, asked of a program that is ABOUT TO RUN rather than of
/// one being routed — and it is the same walk, so the two can never disagree.
///
/// **Its reason is `-c` itself, and it survives #48.** That issue was the
/// CHAIN reaching this binary with `-c` on a route the core computed; the fix
/// is that every variant now computes the whole spectrum's verdict
/// ([`Requirements::spectrum_stop`]), so the chain no longer hands a rung a
/// program that rung statically refuses. What is left is the entry the chain
/// never touched: `<bin> -c PROG` typed by hand, and the one
/// `lypning conformance` grades every engine through. `glob` has NO runtime
/// backstop — the order rule is decided in the walk and nowhere else — so
/// without this, `lypning-l -c 'import glob; print(glob.glob("*"))'` does not
/// refuse, it ANSWERS, in whatever order the filesystem gave, at exit 0.
/// Measured 2026-09-06 by deleting this call and rebuilding: the refusal became
/// `['qqq.py', 'bbb.py', 'aaa.py', 'mmm.py', 'zzz.py']` and exit 0.
///
/// It runs BEFORE the first statement, so the refusal is exit 90 with an empty
/// stdout and an untouched disk — never the exit 1 a refusal reached after
/// `os.makedirs()` would have been. Only for a source that mentions `glob`, so
/// every other program pays one substring search: `re`'s stops are not why this
/// exists (the matcher refuses them at runtime, which is a backstop `glob` has
/// none of), and widening the guard to catch them would put a second AST walk
/// in front of every in-process run to buy an exit code on a path the chain can
/// no longer reach. A program that mentions `glob` and stops on `re` first is
/// refused here with the `re` line, which is the same line one statement later
/// and one committed write earlier.
///
/// `cap-hashlib` widened the guard to `hashlib` for the same reason `glob` is
/// in it: the core routes `import hashlib` INTO `lypning-l`, which enters the
/// program as `-c` and never walks it, so a constructor or attribute only the
/// walk could refuse would land at RUNTIME — and a runtime refusal after
/// `os.mkdir` has committed the barrier is exit 1 with the output discarded
/// (issue #51). Asked here, it is exit 90 with an untouched disk.
#[cfg(any(feature = "cap-glob", feature = "cap-hashlib"))]
pub fn static_stop_check(body: &[Stmt], src: &str) -> crate::err::R<()> {
    if !src.contains("glob") && !src.contains("hashlib") {
        return Ok(());
    }
    let mut req = Requirements {
        glob_wrappers: trusted_wrappers(src),
        ..Requirements::default()
    };
    walk_block(body, &mut req);
    match req.spectrum_stop {
        Some((k, d)) => Err(crate::err::unsupported(&k, &d)),
        None => Ok(()),
    }
}

/// Which order-blind wrapper names this source still uses as the BUILTIN, one
/// bit per index into [`ORDER_BLIND`].
///
/// The blessing above says `sorted(glob.glob(p))` cannot show the order, which
/// is true of the BUILTIN `sorted` and of nothing else. A walk that tracked
/// bindings would still be wrong in one direction — a `def sorted` BELOW the
/// call decides what the call meant inside a function — so this is textual and
/// runs once over the whole source, and it deliberately OVER-matches: any
/// occurrence that is not the head of a call gives the name up, which costs a
/// CPython spawn and never an answer. An occurrence after a `.` is somebody
/// else's attribute and is skipped.
///
/// **Per NAME, not per program**, and the corpus is why: `defaultdict(set)`
/// passes the builtin `set` around without rebinding anything, and a single
/// verdict for the whole source let that spelling give up `sorted`'s blessing
/// as well — refusing a program (py-ad25b33c55b7, mined 2026-09-06) whose glob
/// call is squarely inside `sorted()`. Giving up only the name that was
/// actually touched is strictly safer AND strictly wider.
fn trusted_wrappers(src: &str) -> u16 {
    let is_ident = |c: char| c.is_ascii_alphanumeric() || c == '_';
    let mut bits = (1u16 << ORDER_BLIND.len()) - 1;
    if !src.contains("glob") {
        return 0;
    }
    for (b, (w, _)) in ORDER_BLIND.iter().enumerate() {
        let mut from = 0;
        while let Some(i) = src[from..].find(w) {
            let start = from + i;
            let end = start + w.len();
            from = end;
            let head = &src[..start];
            if head.chars().next_back().is_some_and(|c| is_ident(c) || c == '.') {
                continue;
            }
            let tail = &src[end..];
            if tail.chars().next().is_some_and(is_ident) {
                continue;
            }
            // The head of a call is the only shape that is certainly the
            // builtin; `sorted = f`, `sorted, x = …`, `def f(sorted)`,
            // `lambda sorted:` and `map(len, xs)` are all something else.
            let is_call = tail.trim_start().starts_with('(');
            // `def sorted(`, `for sorted in`, `import x as sorted`,
            // `global sorted` — a call shape that is still a binding.
            let head = head.trim_end();
            let n = head.chars().rev().take_while(|c| is_ident(*c)).count();
            let bound = matches!(
                &head[head.len() - n..],
                "def" | "as" | "for" | "class" | "import" | "lambda" | "global" | "nonlocal"
            );
            if !is_call || bound {
                bits &= !(1 << b);
                break;
            }
        }
    }
    bits
}

/// A call that reads stdin, for `Route::reads_stdin`: `input()`, and the
/// descriptor spellings — `open(0)`, `os.fdopen(0)`, `os.read(0, n)`,
/// `open("/dev/stdin")`. Over-matching (`f.read(0)` on a file) is the cheap
/// direction and is allowed.
fn calls_stdin(func: &Expr, args: &[Expr]) -> bool {
    let fd0 = match args.first() {
        Some(Expr::Int(0)) => true,
        Some(Expr::Str(s)) => s.as_ref() == "/dev/stdin",
        _ => false,
    };
    match func {
        Expr::Name(n) => n.as_ref() == "input" || (n.as_ref() == "open" && fd0),
        Expr::Attr(_, n) => {
            fd0 && matches!(n.as_ref(), "open" | "fdopen" | "read" | "readline" | "readlines")
        }
        _ => false,
    }
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

/// The module `e` names, when it is one this binary does NOT serve but
/// [`MODULE_ATTRS`] has a row for — the mirror of `resolve_module`, which can
/// only see modules in `modules::MODULES`. Only a bare name (or its `import … as`
/// alias) that the program actually imported: `csv.reader` where `csv` is a
/// local variable is not a module attribute, and the walk must not say it is.
fn capability_module(e: &Expr, req: &Requirements) -> Option<String> {
    let n = match e {
        Expr::Name(n) => n.as_ref(),
        _ => return None,
    };
    let name = req
        .aliases
        .iter()
        .find(|(a, _)| a == n)
        .map(|(_, p)| p.as_str())
        .unwrap_or(n);
    if crate::modules::MODULES.contains(&name) || !req.imports.contains(name) {
        return None;
    }
    MODULE_ATTRS.iter().find(|(m, _)| *m == name).map(|(m, _)| (*m).to_string())
}

fn walk_expr(e: &Expr, req: &mut Requirements) {
    match e {
        Expr::Name(n) => {
            // A name bound by `from glob import glob` that is NOT the callee of
            // a blessed call: the walk skips the callee of one it served, so
            // reaching here means the function is being passed, stored or
            // called somewhere the order shows. `escape` and `has_magic` carry
            // no order and are bound by the same arm, so the test is on the
            // FUNCTION and not merely on the binding.
            if req
                .glob_names
                .iter()
                .any(|(b, f)| b == n.as_ref() && matches!(f.as_str(), "glob" | "iglob"))
            {
                req.block_glob_order();
                return;
            }
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
            if matches!(n.as_ref(), "stdin" | "__stdin__") {
                req.reads_stdin = true;
            }
            // `glob.glob` as a VALUE — `f = glob.glob`, `map(glob.glob, ps)`.
            // The callee of a call this walk served is never walked, so
            // reaching the attribute here means the reference escaped into a
            // position where the order could be shown.
            //
            // Asked through [`glob_call`] and NOT through `resolve_module`
            // below: that resolves against `modules::MODULES`, which has no
            // `glob` row in the core, so the core would have missed exactly the
            // spelling it is being asked to route.
            if glob_call(e, req).is_some() {
                req.block_glob_order();
                return;
            }
            // `hashlib.<n>` outside `hashlib::SERVED`. The CORE already
            // routes this correctly out of [`MODULE_ATTRS`] — `escalate`
            // replaces the import blocker and `answers` refuses every
            // `module-attr` — so the ROUTE needs nothing here. What needs it is
            // the RUN: `lypning-l` is entered as `<bin> -c`, walks nothing, and
            // would meet `hashlib.new` at runtime, which after a committed side
            // effect is exit 1 with the output discarded (issue #51). Recorded
            // in the stop slot, [`static_check`] raises it before
            // `Interp::new()` and the disk is untouched.
            //
            // Under the capability's feature and not in the core, which is
            // frozen and already has the answer it needs; and NOT as a generic
            // rule over every [`MODULE_ATTRS`] module, which was tried and
            // measured: it made `lypning-l` refuse two corpus csv programs
            // (py-a17ba3c48307, py-a28bd1e6292d) that name `csv.__file__` in a
            // branch they never reach, where the core RAN them and matched —
            // a monotone violation, which invariant 10 does not allow.
            #[cfg(feature = "cap-hashlib")]
            if hash_module(b, req) && !crate::hashlib::SERVED.contains(&n.as_ref()) {
                req.stop("module-attr", format!("hashlib.{n}"));
                return;
            }
            // Every OTHER `glob.<n>`, decided from [`GLOB_SERVED`]: `escape`
            // and `has_magic` are served in any position and stop the walk
            // here, and the rest are a `module-attr` refusal that no rung of
            // the spectrum answers. `resolve_module` below cannot decide it —
            // it reads `modules::MODULES`, which has no `glob` row in the core
            // — so `glob.translate` was routed to lypning-l and refused THERE,
            // one statement into a program that had already made a directory.
            if glob_module(b, req) {
                if !GLOB_SERVED.contains(&n.as_ref()) {
                    req.stop("module-attr", format!("glob.{n}"));
                }
                return;
            }
            // Record any construct the oracle lypning-mp is known to answer
            // wrongly (a family in `.github/known-mismatches.json`). This
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
            // The same question for a module THIS binary does not serve but a
            // sibling does: `resolve_module` cannot answer it, because the name
            // is not in `modules::MODULES` here. `MODULE_ATTRS` is the table
            // that can, and it is carried by every variant precisely so the
            // cheapest one — the one that routes — can read it.
            if let Some(m) = capability_module(b, req) {
                if !served_attr(&m, n) {
                    req.escalate(&m, n);
                    return;
                }
            }
            if !known_method(n)
                && !pathlib_method(req, n)
                && !re_method(req, n)
                && !hash_method(req, n)
            {
                let detail = format!(".{n}()");
                // The iteration-74 defect class, and the reason `hashlib` was
                // rejected there: the walk keeps the FIRST blocker, and for a
                // program a capability admits the first blocker is the import.
                // The ROUTER only ever sees that one — so `import hashlib;
                // print(hashlib.md5(b"a").hexdigest()); print((5).bit_length())`
                // routed to lypning-l, printed the digest, and died at exit 1
                // on `.bit_length()`. An `AttributeError` is not a refusal, the
                // barrier only discards on 90, and the chain never retried it.
                //
                // [`stop_only`](Requirements::stop_only), so the `--plan` row
                // stays the one the program hit first, and the refusal is
                // raised by [`static_check`] before `Interp::new()` — a clean
                // 90 the chain answers on CPython one spawn later.
                //
                // The condition is [`admitted_by_a_capability`] and not "a
                // blocker is already recorded": in the CORE the blocker is
                // `module: import hashlib` and `.hexdigest()` is a `method`
                // block (`hash_method` is `false` there), so an unconditional
                // stop would route every hashlib program to CPython and the
                // capability would be dead. It is measured, not argued: over
                // the corpus loaded on 2026-09-06 the walk of the variant that
                // HAS the capabilities blocks `method` on 35 programs, 4 of
                // them admitted by a capability import, and exactly one of
                // those four is a program it answers today.
                #[cfg(feature = "cap-hashlib")]
                if admitted_by_a_capability(req) {
                    req.stop_only("method", detail.clone());
                }
                req.block("method", detail);
            }
        }
        Expr::Call {
            func,
            args,
            kwargs,
            star,
            dstar,
        } => {
            // Before the callee and the arguments are walked, so that a
            // program whose arguments hold a second blocker is still counted
            // under the pattern it cannot compile — the row `--plan` ranks.
            re_pattern_block(req, func, args, kwargs);
            // Under the capability's own feature, like `re_pattern_block` and
            // unlike `glob_call_block`: the CORE cannot resolve a hashlib
            // constructor (it has no `hashlib` row in `modules::MODULES`), and
            // the shape this catches — a keyword on a constructor — is one the
            // corpus never types, so paying for it in the frozen core's walk
            // would buy nothing. `static_check` is what makes it fire before
            // the barrier on the `-c` path the core routes into.
            #[cfg(feature = "cap-hashlib")]
            hash_call_block(req, func, args, kwargs, star, dstar);
            // Is THIS a glob call, and did its parent bless it? A blessed call
            // is served and its callee is not walked; an unblessed one is the
            // blocker, whatever it was going to be handed to. `escape` and
            // `has_magic` are served in EVERY position — they are string
            // algebra over the pattern and never list a directory — so they
            // stop the callee walk without asking the order question.
            let served_glob = match glob_func(func, req) {
                None => false,
                Some(f) => {
                    if matches!(f, "glob" | "iglob")
                        && !req.glob_blessed.contains(&(e as *const Expr))
                    {
                        req.block_glob_order();
                    }
                    true
                }
            };
            // After the order rule, which is the more specific row for a call
            // that is in the wrong position AND spelled wrongly, and before the
            // arguments are walked.
            glob_call_block(req, func, args, kwargs, star, dstar);
            // Before the arguments are walked, because the blessing has to be
            // in place by the time the walk reaches the call it blesses.
            glob_bless(req, func, args, kwargs, star, dstar);
            if calls_stdin(func, args) {
                req.reads_stdin = true;
            }
            if !served_glob {
                walk_expr(func, req);
            }
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
            // `p in glob.glob(...)` answers a bool, which every permutation of
            // the list answers the same way. Only for a comparison with ONE
            // operator: in a CHAIN the same expression is also the LEFT operand
            // of the next `in`, where `[a, b] in [[b, a]]` does read the order.
            if rest.len() == 1 && matches!(rest[0].0, CmpOp::In | CmpOp::NotIn) {
                if let Expr::Call { func, .. } = &rest[0].1 {
                    if glob_call(func, req).is_some() {
                        req.glob_blessed.push(&rest[0].1 as *const Expr);
                    }
                }
            }
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
            // A comprehension is its own scope in Python 3 and its clauses run
            // before the element expression, so this is both of those: the
            // targets are given up inside and restored outside, each iterable
            // is walked before the target it feeds, and the FIRST iterable is
            // therefore still read against the enclosing table — which is where
            // it is evaluated.
            let saved = req.enter_scope();
            for c in clauses {
                walk_expr(&c.iter, req);
                walk_target(&c.target, req);
                c.ifs.iter().for_each(|i| walk_expr(i, req));
            }
            walk_expr(elt, req);
            if let Some(v) = val {
                walk_expr(v, req);
            }
            req.leave_scope(saved);
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
            let saved = req.enter_scope();
            req.shadow_params(params);
            walk_expr(body, req);
            req.leave_scope(saved);
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
