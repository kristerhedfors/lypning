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
        caps: &["cap-collections", "cap-glob", "cap-pathlib", "cap-re"],
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
/// `cap-glob` serves the `glob` MODULE and answers no runtime kind either. The
/// one thing about `glob` a router would like to have seen coming —
/// `glob-order`, a result whose ORDER the program can observe — is not a
/// runtime kind at all: [`walk_expr`] decides it STATICALLY, before the program
/// starts, and the kind is in [`ONLY_CPYTHON_KINDS`] because no
/// reimplementation can reproduce `os.scandir` order, so no sibling could
/// answer it either.
pub const CAPS: &[(&str, &[&str], &[&str])] = &[
    ("cap-collections", &["collections"], &[]),
    ("cap-glob", &["glob"], &[]),
    ("cap-pathlib", &["pathlib"], &[]),
    ("cap-re", &["re"], &[]),
];

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

/// `stop` is the glob refusal every rung shares, when the walk found one — a
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
/// order, a keyword only CPython serves, an attribute nothing here has — and a
/// verdict vector that says so routes to CPython through
/// [`engine_from_verdicts`] and shortens the chain through [`chain_after`]
/// with no special case in either.
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
        // `re` is the sibling's module now, so a bare import routes there and
        // so does an ordinary matcher call — the pattern is COMPILED by the
        // walker, before the program starts, in every spelling a walk can see.
        #[cfg(feature = "cap-re")]
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
        // …and a construct of a later slice, or a pattern CPython itself
        // rejects, is the program's blocker HERE rather than a refusal one
        // in-process run later — which is what keeps a runtime refusal from
        // landing after a side effect the commit barrier has let through.
        #[cfg(feature = "cap-re")]
        for src in [
            "import re\nprint(re.search(r'(?P<a>x)', 'x'))",
            "import re\nprint(re.findall(r'(?<=a)b', 'ab'))",
            "import re as x\nprint(x.sub(r'(a)\\1', 'b', 'aa'))",
            "from re import compile as c\nprint(c('a{2,1}'))",
            "import re, os\nos.makedirs('d1/d2')\nprint(re.sub(r'(?=a)', 'b', 'a'))",
        ] {
            let r = route(src);
            assert_eq!(r.engine, Engine::CPython, "{src}");
            assert_eq!(r.kind, "re", "{src}");
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
            let stop = req.glob_stop.take();
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

/// A pattern a walk could read: the text of a `str` literal, the fact that it
/// was a `bytes` one — which is all `re` needs, since a bytes pattern refuses
/// whatever its content — or the TYPE of any other literal, which is what
/// `glob` needs, because its refusal names the type it was handed.
///
/// Read by TWO capabilities now, which is why it is no longer behind
/// `cap-re`: `re.sub(P, …)` and `glob.glob(P)` ask the same question of the
/// same binding, and `glob`'s half has to be answered in the CORE.
#[derive(Clone)]
enum PatLit {
    Str(std::rc::Rc<str>),
    Bytes,
    Other(&'static str),
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
    /// be compiled here, before the program starts. Only on a variant that
    /// serves `re`: the core has no compiler to decide with.
    #[cfg(feature = "cap-re")]
    re_names: Vec<(String, String)>,
    /// `P = r'…'` — a pattern LITERAL bound to a name, so that `re.sub(P, …)`
    /// and `glob.glob(P)` are decided by the same walk that decides
    /// `re.sub(r'…', …)` and `glob.glob('…')`. `None` is a name a walk cannot
    /// read a literal out of (a loop variable, a parameter, anything
    /// computed), and it is the value that MATTERS: a name this table does not
    /// resolve keeps the runtime refusal, which is the backstop. Only filled
    /// once `re` or `glob` is imported, so a program that never touches either
    /// module pays one set lookup per binding and no allocation.
    pats: Vec<(String, Option<PatLit>)>,
    /// `from glob import glob [as g]` — the bound name of a glob FUNCTION, so
    /// that a bare `g(...)` is seen as the call it is. Without it the order
    /// blocker below would miss the one spelling that hides the module name.
    glob_names: Vec<(String, String)>,
    /// The call nodes the parent blessed as order-blind, by identity. The walk
    /// borrows one live AST for its whole run, so no node is freed and no
    /// address is reused; nothing is dereferenced through these.
    glob_blessed: Vec<*const Expr>,
    /// The glob refusal that stops EVERY rung of the spectrum, as
    /// `(kind, detail)`, recorded even when an EARLIER blocker won the `--plan`
    /// row. [`glob_static_check`] reads this one: a program whose first blocker
    /// is something lypning-l runs anyway (the walker is deliberately
    /// pessimistic about methods) must still not reach a glob call it would
    /// have refused halfway through.
    ///
    /// [`route`] reads it too, and has to: in the CORE the FIRST blocker is
    /// `module: import glob`, which `lypning-l` answers — so the blocker slot
    /// alone would route such a program to a sibling that refuses it.
    ///
    /// It carries the KIND as well as the detail because it is no longer only
    /// `glob-order`. Every static refusal an admitted glob call can raise goes
    /// here — a keyword lypning-l does not serve, a pattern literal it cannot
    /// match, an attribute it does not have — and each keeps the kind the
    /// runtime would have raised, so a program is refused with the same line
    /// one in-process run earlier.
    glob_stop: Option<(String, String)>,
    /// Which order-blind wrappers are still the BUILTIN, one bit per index into
    /// [`ORDER_BLIND`]. `sorted` rebound to something that shows its argument's
    /// order would make the blessing below a lie — see [`trusted_wrappers`].
    /// Zero, the default, trusts none of them, which is right for a program
    /// that never mentions the module and has nothing to bless.
    glob_wrappers: u16,
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

    /// A glob refusal the whole spectrum shares, for the router AND for the
    /// run. `block` is first-wins because `--plan` ranks what a program hit
    /// FIRST; this slot is separate because the run has to refuse whether or
    /// not something else was hit earlier.
    fn stop_glob(&mut self, kind: &str, detail: String) {
        self.block(kind, detail.clone());
        self.stop_only(kind, detail);
    }

    /// The stop without the blocker, for the two places that have ALREADY
    /// blocked correctly on both variants — the `from glob import …` arm,
    /// where the core blocks `module` and lypning-l blocks `module-attr` and
    /// neither should be displaced from the `--plan` row this walk reports.
    fn stop_only(&mut self, kind: &str, detail: String) {
        if self.glob_stop.is_none() {
            self.glob_stop = Some((kind.to_string(), detail));
        }
    }

    fn block_glob_order(&mut self) {
        self.stop_glob("glob-order", GLOB_ORDER.to_string());
    }

    /// Record what `name` now holds: a pattern literal, or `None` for a
    /// binding a walk cannot read. The walk is in SOURCE ORDER, so the value
    /// in force at the call is the one the call is decided against, and a
    /// rebinding before the call replaces the literal rather than stacking on
    /// it. A name bound only AFTER its use is never resolved, which is the
    /// safe direction: the runtime refusal still catches it.
    fn bind_pattern(&mut self, name: &str, lit: Option<PatLit>) {
        if !self.imports.contains("re") && !self.imports.contains("glob") {
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
    fn shadow_params(&mut self, params: &crate::ast::Params) {
        for n in &params.names {
            self.bind_pattern(n, None);
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
                if !crate::modules::MODULES.contains(&path.as_ref()) {
                    req.block("module", format!("import {path}"));
                }
            }
        }
        Stmt::FromImport { module, names } => {
            req.imports.insert(module.to_string());
            match module.as_ref() {
                "fileinput" => req.reads_stdin = true,
                "sys" if names.iter().any(|(n, _)| matches!(n.as_ref(), "stdin" | "__stdin__")) => {
                    req.reads_stdin = true
                }
                #[cfg(feature = "cap-re")]
                "re" => {
                    for (n, bind) in names {
                        if crate::re::is_matcher(n) {
                            req.re_names.push((bind.to_string(), n.to_string()));
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
            req.shadow_params(params);
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

/// The `re` function a call names — `re.sub`, `x.sub` after `import re as x`,
/// or a name bound by `from re import sub [as s]` — together with the
/// expression in its PATTERN position, wherever that is spelled.
///
/// Only for a program that imports `re`: the import is what makes the name mean
/// the module, exactly as for [`pathlib_method`]; `re.split(",")` on a string
/// someone called `re` is a str method and runs here.
#[cfg(feature = "cap-re")]
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
                name == "re" && crate::re::is_matcher(n)
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

fn pattern_literal(e: &Expr) -> Option<PatLit> {
    match e {
        Expr::Str(s) => Some(PatLit::Str(s.clone())),
        Expr::Bytes(_) => Some(PatLit::Bytes),
        e => literal_type(e).map(PatLit::Other),
    }
}

/// The TYPE of a literal expression, spelled the way `value::type_name` spells
/// it — which is the way the refusal that names it spells it too.
///
/// An f-string IS a `str` and its text is not a walk's to read, so it answers
/// the type and no value. Everything that is not a literal answers `None` and
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
/// This is THIS binary's walk, so it decides for a chain that starts here.
/// A chain that starts at the core reaches `lypning-l` through `exec` with
/// `-c`, not `run`, so the core's own route — `module: import re` — is the one
/// that placed the program, and the runtime refusal is still what fires there.
#[cfg(feature = "cap-re")]
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
            if let Err(e) = crate::re::precompile(&src) {
                if let crate::err::ErrKind::Unsupported { kind, detail } = e.kind() {
                    req.block(kind, detail.clone());
                }
            }
        }
        // A BYTES pattern is servable by CPython and by nothing in this
        // engine, and it refuses whatever its content and whatever the
        // subject — which makes it exactly the shape that must not wait for
        // runtime to say so. Same kind, same detail, one in-process run
        // earlier.
        Some(PatLit::Bytes) => {
            req.block("re", "bytes pattern or subject (re over bytes)".to_string())
        }
        // A literal of any other type is `glob`'s half of this table and not
        // `re`'s: what `re.compile(5)` raises is a `TypeError` whose wording is
        // CPython's, so the runtime refusal is the one that must fire.
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
/// **What stays a runtime refusal, and why that is a much smaller surface.**
/// A pattern whose value is computed keeps the type and range checks at
/// runtime; so does a call spelled with `*args` or `**kwargs`, where the walk
/// can neither count the positionals nor read the keyword names. Two more are
/// the FILESYSTEM's and no walk could ever hoist them: a directory entry whose
/// name is not valid UTF-8, and a `**` walk deeper than this engine follows.
/// Each is now reachable only from a program whose pattern is dynamic or whose
/// argument list is unpacked — a far narrower door than one a string literal
/// could walk through.
fn glob_call_block(
    req: &mut Requirements,
    func: &Expr,
    args: &[Expr],
    kwargs: &[(std::rc::Rc<str>, Expr)],
    star: &[usize],
    dstar: &[Expr],
) {
    let Some(name) = glob_func(func, req) else { return };
    if !star.is_empty() || !dstar.is_empty() {
        return;
    }
    let arg = args.first().and_then(|e| glob_arg(e, req));
    let detail = match (args.len(), &arg) {
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
            kwargs
                .iter()
                .find(|(k, _)| !glob_kw_served(name, k))
                .map(|(k, _)| format!("glob.{name}({k}=…)"))
        });
    if let Some(d) = detail {
        req.stop_glob("glob", d);
    }
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

/// The static glob rules, asked of a program that is ABOUT TO RUN rather than
/// of one being routed — and it is the same walk, so the two can never
/// disagree.
///
/// `route()` is consulted by `lypning run`; `<bin> -c PROG` is not routed at
/// all, and that is how the chain reaches this binary once a smaller sibling
/// has picked it (`docs/HILLCLIMB.md` iteration 76 filed the general case as
/// #48). Without this the static blockers would be inert on exactly the path
/// the dispatcher uses, and `lypning-l -c 'import glob; print(glob.glob("*"))'`
/// would answer in the filesystem's order at exit 0.
///
/// It runs BEFORE the first statement, so the refusal is exit 90 with an empty
/// stdout and an untouched disk — never the exit 1 a refusal reached after
/// `os.makedirs()` would have been. Only for a source that mentions the module,
/// so every other program pays one substring search.
#[cfg(feature = "cap-glob")]
pub fn glob_static_check(body: &[Stmt], src: &str) -> crate::err::R<()> {
    if !src.contains("glob") {
        return Ok(());
    }
    let mut req = Requirements {
        glob_wrappers: trusted_wrappers(src),
        ..Requirements::default()
    };
    walk_block(body, &mut req);
    match req.glob_stop {
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
            // Every OTHER `glob.<n>`, decided from [`GLOB_SERVED`]: `escape`
            // and `has_magic` are served in any position and stop the walk
            // here, and the rest are a `module-attr` refusal that no rung of
            // the spectrum answers. `resolve_module` below cannot decide it —
            // it reads `modules::MODULES`, which has no `glob` row in the core
            // — so `glob.translate` was routed to lypning-l and refused THERE,
            // one statement into a program that had already made a directory.
            if glob_module(b, req) {
                if !GLOB_SERVED.contains(&n.as_ref()) {
                    req.stop_glob("module-attr", format!("glob.{n}"));
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
            if !known_method(n) && !pathlib_method(req, n) && !re_method(req, n) {
                req.block("method", format!(".{n}()"));
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
            #[cfg(feature = "cap-re")]
            re_pattern_block(req, func, args, kwargs);
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
            req.shadow_params(params);
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
