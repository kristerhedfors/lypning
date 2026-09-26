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

pub const SPECTRUM: &[Variant] = &[
    Variant { name: "lypning", caps: &[] },
    Variant {
        name: "lypning-l",
        // Alphabetical, which is the order `build.rs` emits `LYPNING_CAPS` in —
        // so the binary's own answer, this table and `engines.VARIANT_CAPS` are
        // one list and not three that happen to agree.
        caps: &[
            "cap-ast",
            "cap-base64",
            "cap-bigint",
            "cap-binascii",
            "cap-collections",
            "cap-csv",
            "cap-difflib",
            "cap-future",
            "cap-glob",
            "cap-hashlib",
            "cap-itertools",
            "cap-pathlib",
            "cap-random",
            "cap-re",
            "cap-statistics",
            "cap-textwrap",
            "cap-time",
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
/// `cap-bigint` is the first row that is the OTHER column: it serves no module
/// at all and answers two RUNTIME kinds, `bigint` and `int-div-precision`. Both
/// are value-dependent by nature — `r *= i` in a loop overflows on an iteration
/// no walk can pick out — so there is nothing to hoist into `walk_expr`, and the
/// kinds column is what makes the core's runtime refusal reach the variant that
/// can answer it instead of costing a CPython spawn. `int-div-precision` left
/// [`ONLY_CPYTHON_KINDS`] on the same commit, for the reason written there.
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
/// `cap-base64` serves the `base64` MODULE — four functions of it, and only the
/// attributes [`MODULE_ATTRS`] names — and answers no runtime kind. The one
/// refusal it raises at runtime (`base64`) is a value the WALK could not read:
/// an argument computed rather than spelled. There is no rung above lypning-l
/// to hand it to, and `chain_after` a runtime `base64:` refusal is `[cpython]`
/// by construction, so listing the kind would cost a spawn to be told no twice.
///
/// `cap-binascii` serves the `binascii` MODULE — six functions of it, and only
/// the names [`MODULE_ATTRS`] lists — and answers no runtime kind, for
/// `cap-base64`'s reason: its one runtime refusal (`binascii`) is a computed
/// argument whose call CPython raises on or words, and there is no rung above
/// lypning-l to carry the kind to. It also serves the `struct` MODULE —
/// `pack`, `unpack`, `unpack_from` and `calcsize` — folded into this row
/// rather than a row of its own, because the frozen core carries this table
/// and a row costs it bytes a module name does not. `struct` has no
/// [`MODULE_ATTRS`] row for the same reason, so its unserved names
/// (`error`, `Struct`, `pack_into`, `iter_unpack`) are LATE: routed into
/// lypning-l, refused there at run time, a spawn and never an answer.
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
/// `cap-statistics` serves the `statistics` MODULE — four functions, and only
/// the names [`MODULE_ATTRS`] lists — and answers no runtime kind. Its runtime
/// `statistics:` refusals (empty data, a float or non-numeric `mean`) are
/// answers CPython owns: there is no rung above `lypning-l` to carry the kind
/// to, so listing it would cost a spawn to be told no twice.
/// `cap-itertools` serves the `itertools` MODULE — two classes of it, and only
/// the names [`MODULE_ATTRS`] lists — and `cap-difflib` serves the `difflib`
/// module with NO name at all, which is why its row there is present and
/// empty: an absent row would claim the whole surface. Neither answers a
/// runtime kind, for the reason `cap-hashlib` gives: there is no rung above
/// `lypning-l` to carry one to.
///
/// `cap-glob` serves the `glob` MODULE and answers no runtime kind either. It
/// is the SECOND module served only in part, and it needs no [`MODULE_ATTRS`]
/// row to say so: the walk below carries [`GLOB_SERVED`] unconditionally, so
/// the core blocks `module-attr: glob.translate` out of its own walk exactly
/// where lypning-l would. Glob ORDER is served: `glob.rs` lists in CPython's
/// own yield order over `readdir`. What is left of `glob-order` is a lazy
/// `iglob` where its order shows, or a listing function used as a value —
/// both decided STATICALLY by [`walk_expr`] in every variant — and a listing
/// whose order shows over a directory the run has changed, which lypning-l
/// refuses at runtime. The kind is in [`ONLY_CPYTHON_KINDS`] and deliberately
/// NOT in this row: no sibling answers any of the three, so listing it would
/// have the core predict lypning-l for programs lypning-l refuses.
///
/// `cap-textwrap` serves the `textwrap` MODULE — five functions, and only the
/// names [`MODULE_ATTRS`] lists (`TextWrapper` is not one) — and answers no
/// runtime kind. Its runtime refusals (`textwrap:`) are a computed argument of
/// the wrong type or a non-ASCII character the hyphen regex would have to
/// classify, and there is no rung above `lypning-l` to carry either to.
///
/// `cap-time` serves the `time` MODULE — the clocks, a bounded `sleep` and the
/// one fused UTC stamp, and only the names [`MODULE_ATTRS`] lists — and
/// answers no runtime kind. Every `time:` refusal is a SHAPE lypning-l's own
/// walk decides before the program starts (a function used as a value, a
/// `sleep` the walk cannot bound, `strftime` outside the fused shape), and
/// there is no rung above lypning-l to carry the kind to.
///
/// `cap-future` serves `__future__`, which is not a module at all but a
/// compiler directive: `future.rs` is a pass over the parse that strips a
/// served head of future imports, so lypning-l's walk never sees one. The row
/// is the MODULE column, not a kind, because what the CORE stops on is
/// `module: from __future__ import …` and [`answers`] asks `served_module` of
/// `module_of` that detail, which is `__future__`. Its own refusal kinds
/// (`future`, `annotation`) are shapes CPython owns — a `SyntaxError`, a
/// `_Feature` value, annotations as strings — and are not listed.
///
/// The kind column carries the two PARSE-time kinds the core's parser stops
/// on and lypning-l's parser serves under the same contract — syntax the
/// core refuses before the first statement, a run held from that statement
/// (`route::arm_hold`, `parse::funcsig_used`): `decorator` (`@d` before a
/// `def`) and `kwonly` (keyword-only parameters). Folded into this row rather
/// than a row of their own because the frozen core carries this table and a
/// row costs it bytes a kind array does not. `class`, `async` and `generator`
/// are NOT here: a decorated class still refuses in lypning-l, at parse.
///
/// `cap-random` is the first row with NEITHER column: it serves no module —
/// `random` and `sys` are the core's own — and no runtime kind, because every
/// refusal it raises is the `random` kind, which is only-CPython. Its routing
/// half is [`CAP_ATTRS`], the attributes it adds to those two modules; the row
/// is here so the spectrum declares the feature like every other.
///
/// `cap-ast` serves the `ast` MODULE — `literal_eval` over a `str`, and only
/// the names [`MODULE_ATTRS`] lists ([`AST_SERVED`]; `ast.parse` is NOT one,
/// because `parse.rs` accepts programs CPython's `ast.parse` rejects) — and
/// answers no runtime kind: every `ast:` refusal is an input whose answer
/// CPython owns (a `ValueError` naming an AST node, a warning, an escape or a
/// number `lex.rs` may misread), and there is no rung above lypning-l to carry
/// it to.
pub const CAPS: &[(&str, &[&str], &[&str])] = &[
    ("cap-ast", &["ast"], &[]),
    ("cap-base64", &["base64"], &[]),
    ("cap-bigint", &[], &["bigint", "int-div-precision"]),
    ("cap-binascii", &["binascii", "struct"], &["fromhex"]),
    ("cap-collections", &["collections"], &[]),
    ("cap-csv", &["csv"], &[]),
    ("cap-difflib", &["difflib"], &[]),
    ("cap-future", &["__future__"], &["decorator", "kwonly"]),
    ("cap-glob", &["glob"], &[]),
    ("cap-hashlib", &["hashlib"], &[]),
    ("cap-itertools", &["itertools"], &[]),
    ("cap-pathlib", &["pathlib"], &[]),
    ("cap-random", &[], &[]),
    ("cap-re", &["re"], &[]),
    ("cap-statistics", &["statistics"], &[]),
    ("cap-textwrap", &["textwrap"], &[]),
    ("cap-time", &["time"], &[]),
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
/// `base64` is here for exactly the reason `csv` is, and the row IS
/// [`BASE64_SERVED`] rather than a copy of it. Four names are served —
/// `b64encode`, `b64decode`, `urlsafe_b64encode`, `urlsafe_b64decode` — and the
/// rest of the module (`b16*`, `b32*`, `b85*`, `a85*`, `encodebytes`,
/// `decodebytes`, `standard_b64*`) is not. Without the row the core would route
/// `base64.b32encode(...)` into lypning-l on the strength of `module: import
/// base64` and the attribute would refuse THERE, one statement into a program
/// that may already have written to disk.
///
/// `glob` is absent for the opposite reason: it is small enough, but the walk
/// already carries [`GLOB_SERVED`] unconditionally and decides `glob.<n>` from
/// it — with the KIND the runtime would have raised — several arms before
/// [`capability_module`] is reached. A row here would be a second table saying
/// the same thing, and the two would drift.
///
/// `__future__` is here so the CORE sends `from __future__ import braces`, a
/// misspelled feature and `barry_as_FLUFL` to CPython rather than to a variant
/// that would refuse them: the row IS [`FUTURE_SERVED`], held to `future.rs`
/// by its own `the_route_table_names_exactly_what_is_served`.
pub const MODULE_ATTRS: &[(&str, &[&str])] = &[
    ("__future__", FUTURE_SERVED),
    // Held to `pyast.rs` by its own
    // `the_route_table_names_exactly_what_is_served`. `parse`, `walk`,
    // `dump`, `unparse`, the node classes and `NodeVisitor` are blocked HERE,
    // in the core's walk, and never reach the variant. Deliberately NO
    // pre-run stop in lypning-l's own walk, as `textwrap` has: an `ast.parse`
    // that never runs (`if False:`) is the core's answer (invariant 10), and
    // one that runs refuses where it is evaluated, in a run `import ast` has
    // already held reversible ([`HINT_HELD_CAPS`]).
    ("ast", AST_SERVED),
    ("base64", BASE64_SERVED),
    // Held to `binascii.rs` by its own
    // `the_route_table_names_exactly_what_is_served`. `Error`, `crc32`,
    // `crc_hqx`, the uu/qp codecs and `Incomplete` are blocked HERE, in the
    // core's walk — `except binascii.Error` included.
    ("binascii", BINASCII_SERVED),
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
    // Held to `statistics::SERVED` by
    // `statistics::tests::the_route_table_names_exactly_what_is_served`. Every
    // other name — `stdev`, `pstdev`, `variance`, `fmean`, `mode`,
    // `quantiles`, `NormalDist`, `StatisticsError` — is blocked HERE, in the
    // core's walk, and never reaches the variant.
    ("statistics", &["mean", "median", "median_high", "median_low"]),
    // Held to `itertools::SERVED` by
    // `itertools::tests::the_route_table_names_exactly_what_is_served`. Every
    // other name — `chain`, `islice`, `permutations`, `count`, `groupby`,
    // `accumulate`, `zip_longest`, `tee`, … — is blocked HERE, in the core's
    // walk, and never reaches the variant.
    ("itertools", &["combinations", "product"]),
    // EMPTY on purpose, and present on purpose: `import difflib` is served
    // and nothing on it is, so `difflib.SequenceMatcher` and every other name
    // is a `module-attr` block in the core's walk. Held to `modules::get_attr`
    // by `the_difflib_row_is_empty_and_the_variant_serves_nothing_on_it`.
    ("difflib", &[]),
    // Held to `textwrap.rs` by
    // `textwrap::tests::the_route_table_names_exactly_what_is_served`.
    // `TextWrapper`, `__file__` and every private name are blocked HERE, in
    // the core's walk.
    ("textwrap", TEXTWRAP_SERVED),
    // Held to `time::SERVED` by
    // `time::tests::the_route_table_names_exactly_what_is_served`. Every
    // local-time name — `localtime`, `ctime`, `asctime`, `mktime`, `strptime`,
    // `timezone`, `tzname`, `altzone`, `daylight` — and the rest of the module
    // (`process_time`, `thread_time`, `get_clock_info`, `struct_time`, the
    // `clock_*` functions and constants) is blocked HERE, in the core's walk.
    // A served name used in a shape lypning-l does not serve is lypning-l's
    // own walk to refuse, before its first statement.
    ("time", TIME_SERVED),
];

/// The `ast` names lypning-l serves — `route.rs`'s own table, for the reason
/// [`BINASCII_SERVED`] is: the binary that routes has no `pyast.rs`.
/// `ast.parse` is deliberately absent — see `pyast.rs`.
pub const AST_SERVED: &[&str] = &["literal_eval"];

/// The `time` names lypning-l serves — `route.rs`'s own table, for the reason
/// [`TEXTWRAP_SERVED`] is: the CORE walks every served `time` call too
/// ([`time_call_block`]), so it sends a program lypning-l's walk would refuse
/// straight to CPython rather than into a rung that refuses it (#48), and it
/// has no `time.rs` compiled in. `time::SERVED` IS this list.
pub const TIME_SERVED: &[&str] = &[
    "gmtime", "monotonic", "monotonic_ns", "perf_counter", "perf_counter_ns", "sleep",
    "strftime", "time", "time_ns",
];

/// Why `time.strftime(f, …)` refuses `f`, or `None` when every directive in
/// it is one of `%Y %m %d %H %M %S %%` — the ones that read neither the locale
/// nor the zone. Here rather than in `time.rs` so the core's walk can ask it;
/// the runtime asks the same function.
pub fn time_format_block(f: &str) -> Option<&'static str> {
    if !f.is_ascii() {
        return Some("time.strftime() over a non-ASCII format");
    }
    let b = f.as_bytes();
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%' {
            match b.get(i + 1) {
                Some(b'Y' | b'm' | b'd' | b'H' | b'M' | b'S' | b'%') => i += 1,
                Some(_) => {
                    return Some(
                        "time.strftime() with a directive outside %Y %m %d %H %M %S %% \
                         (the rest read the locale or the zone, or are platform-defined)",
                    )
                }
                None => return Some("time.strftime() with a trailing '%'"),
            }
        }
        i += 1;
    }
    None
}

/// The `textwrap` functions lypning-l serves, and therefore the only ones ANY
/// rung answers — `route.rs`'s own table and not a copy of `textwrap.rs`'s, for
/// the reason [`BASE64_SERVED`] gives: the binary that routes has no
/// `textwrap.rs` compiled into it.
pub const TEXTWRAP_SERVED: &[&str] = &["dedent", "fill", "indent", "shorten", "wrap"];

/// The keyword arguments each served `textwrap` function takes here. `wrap` and
/// `fill` take the five the corpus uses; `shorten` takes `width` and
/// `placeholder`; `dedent` and `indent` take none (`indent`'s `predicate=` is
/// a callable this engine would have to call per line, and is refused).
/// `TextWrapper`'s other knobs — `max_lines`, `expand_tabs`, `tabsize`,
/// `replace_whitespace`, `drop_whitespace`, `fix_sentence_endings` — are not
/// served, and neither is a keyword `text=`.
fn textwrap_kw_served(name: &str, k: &str) -> bool {
    match name {
        "wrap" | "fill" => matches!(
            k,
            "width" | "initial_indent" | "subsequent_indent" | "break_long_words" | "break_on_hyphens"
        ),
        "shorten" => matches!(k, "width" | "placeholder"),
        _ => false,
    }
}

/// The `from __future__ import` names `cap-future` serves: every feature that
/// is mandatory in Python 3, and so does nothing, plus `annotations`, whose
/// effect the pass implements by never evaluating one. Not `barry_as_FLUFL`
/// (a grammar), not `braces` (a `SyntaxError`), and nothing else, because
/// CPython answers any other name with a `SyntaxError`. Sorted, as every
/// [`MODULE_ATTRS`] row is.
pub const FUTURE_SERVED: &[&str] = &[
    "absolute_import",
    "annotations",
    "division",
    "generator_stop",
    "generators",
    "nested_scopes",
    "print_function",
    "unicode_literals",
    "with_statement",
];

/// How many tokens of the program are `needle`: a name, or text inside an
/// f-string, whose expressions the lexer keeps as raw source. Over-counting
/// (an f-string's literal text) can only refuse, never serve.
pub fn future_mentions(toks: &[crate::lex::Token], needle: &str) -> usize {
    use crate::lex::Tok;
    toks.iter()
        .filter(|t| match &t.tok {
            Tok::Name(n) => n == needle,
            Tok::FStr { raw, .. } => raw.contains(needle),
            _ => false,
        })
        .count()
}

/// How many NAME tokens are `needle` — a spelling that can be a statement,
/// which an f-string's literal text never is. Asked where the answer decides
/// whether `__future__` is looked at at all: `print(f"see __future__")` is the
/// core's program, and its superset may not refuse it.
pub fn future_names(toks: &[crate::lex::Token], needle: &str) -> usize {
    toks.iter().filter(|t| matches!(&t.tok, crate::lex::Tok::Name(n) if n == needle)).count()
}

/// Does the program import the MODULE `__future__` — a `__future__` NAME right
/// after `from` or `import`? Only then is it a compiler directive at all.
/// Anywhere else the name is an ordinary one — a variable, a parameter, an
/// `as` target, a keyword argument — which the core runs as it runs any name,
/// and CPython with it; neither variant may look further (invariant 10).
/// `import os, __future__` is not caught here and needs not be: its import
/// refuses at runtime, in every variant, before anything after it runs.
pub fn future_imported(toks: &[crate::lex::Token]) -> bool {
    use crate::lex::Tok;
    toks.windows(2).any(|w| {
        matches!(&w[1].tok, Tok::Name(n) if n == "__future__")
            && matches!(&w[0].tok, Tok::Name(k) if k == "from" || k == "import")
    })
}

/// The refusal a program with a `__future__` NAME in it gets from its TOKENS
/// alone: `barry_as_FLUFL`, a grammar. (A non-ASCII identifier, which CPython
/// NFKC-folds — `ｂarry_as_FLUFL` — never reaches a token: the lexer refuses it.)
pub fn future_token_block(toks: &[crate::lex::Token]) -> Option<&'static str> {
    (future_names(toks, "barry_as_FLUFL") > 0).then_some("from __future__ import barry_as_FLUFL")
}

/// A program's head of `from __future__` imports, as `(start, end, names)`
/// statement indices and the features it names — or the `future` refusal
/// `cap-future` raises for it: `__debug__` anywhere, an alias, a name off
/// [`FUTURE_SERVED`], a `__future__` anywhere but the head (a misplaced
/// import, `import __future__`, an attribute), or the imported feature's own
/// name or `__annotations__` spelled anywhere else. Asked by `future.rs` and by
/// the CORE's route, so the core never sends a head lypning-l refuses into
/// lypning-l (#48). What only `future.rs` sees — a compile-time `SyntaxError`
/// the parser noted as lax — still costs that one spawn.
pub fn future_head(
    body: &[Stmt],
    toks: &[crate::lex::Token],
) -> Result<(usize, usize, Vec<std::rc::Rc<str>>), String> {
    if future_mentions(toks, "__debug__") > 0 {
        return Err("the name __debug__".into());
    }
    let start = match body.first() {
        Some(Stmt::Expr(Expr::Str(_))) => 1,
        _ => 0,
    };
    let mut end = start;
    let mut names: Vec<std::rc::Rc<str>> = Vec::new();
    while let Some(Stmt::FromImport { module, names: ns }) = body.get(end) {
        if module.as_ref() != "__future__" {
            break;
        }
        for (n, bind) in ns {
            if n != bind {
                return Err(format!("from __future__ import {n} as {bind}"));
            }
            if !FUTURE_SERVED.contains(&n.as_ref()) {
                return Err(format!("from __future__ import {n}"));
            }
            names.push(n.clone());
        }
        end += 1;
    }
    if end - start != future_mentions(toks, "__future__") {
        return Err("__future__ anywhere but the head of the program".into());
    }
    // Each consumed name is one token of its own import; any other token
    // spelling it is a use of the `_Feature` binding.
    let own = |n: &str| names.iter().filter(|m| m.as_ref() == n).count();
    for n in names.iter().map(|n| n.as_ref()).chain(["__annotations__"]) {
        if future_mentions(toks, n) != own(n) {
            return Err(format!("the name {n}"));
        }
    }
    Ok((start, end, names))
}

/// The CORE's half of [`future_head`]: a program that spells `__future__` as
/// a name is one the core blocks on (`module: from __future__ import …`) and
/// `cap-future` claims, so what `future.rs` would refuse is recorded as the
/// spectrum's stop and the program goes to CPython in one step. Not on a
/// variant with `cap-future`, whose parse has already made the decision (and
/// removed the head this would count).
#[cfg(not(feature = "cap-future"))]
fn future_route_stop(src: &str, body: &[Stmt], req: &mut Requirements) {
    if !src.contains("__future__") {
        return;
    }
    let Ok(toks) = crate::lex::tokenize(src) else { return };
    if !future_imported(&toks) {
        return;
    }
    let why = match future_token_block(&toks) {
        Some(w) => w.to_string(),
        None => match future_head(body, &toks) {
            Err(d) => d,
            Ok(_) => return,
        },
    };
    req.stop_only("future", why);
}

/// The attributes a capability adds to a module EVERY variant serves — the
/// core's own `random` and `sys` — as `(cap, module, served anywhere, served in
/// a shape)`, space-separated. Carried by every variant for the reason
/// [`MODULE_ATTRS`] is: the binary that routes is the core, whose `get_attr`
/// refuses these names and which therefore records `module-attr: random.sample`
/// as its blocker. Without this table [`answers`] could only say no, and a
/// capability on a core module would be dead from the router's side (F3).
///
/// The last column is the names served only in the SHAPES the walk blesses
/// ([`bless_cap_shapes`]): `random.Random` as the callee of a one-argument call,
/// and `sys.version_info` as `[0]`/`[1]`, `[:n]` with `n <= 2`, `.major`/
/// `.minor`, or an operand compared with a tuple literal of at most two items.
/// Anywhere else — and for every name a listed module's row does not carry — no
/// rung serves the attribute, and the walk says so in the route's stop slot, so
/// a program whose FIRST blocker lypning-l answers is still not sent there to
/// be refused one statement in.
pub const CAP_ATTRS: &[(&str, &str, &[&str], &[&str])] = &[
    ("cap-random", "random", &["sample", "shuffle"], &["Random"]),
    ("cap-random", "sys", &[], &["version_info"]),
];

/// Does `v` serve `module.name` out of [`CAP_ATTRS`] — `shaped` when the walk
/// blessed the shape it was spelled in? `sys.version_info` is answered only by
/// a build whose reference version was MEASURED ([`crate::err::REF_PY_KNOWN`]).
#[inline(never)]
fn cap_attr(v: &Variant, module: &str, name: &str, shaped: bool) -> bool {
    (module != "sys" || crate::err::REF_PY_KNOWN)
        && CAP_ATTRS.iter().any(|(c, m, any, shape)| {
            *m == module && v.caps.contains(c) && (any.contains(&name) || (shaped && shape.contains(&name)))
        })
}

/// Is `module.name` an attribute of a [`CAP_ATTRS`] module that NO rung of
/// the spectrum serves, spelled the way it is? `false` for every other module,
/// which leaves their routing exactly as it was.
/// The top row is asked alone because `caps` is cumulative
/// (`caps_are_cumulative_and_every_cap_is_declared`).
fn no_rung_serves(module: &str, name: &str, shaped: bool) -> bool {
    CAP_ATTRS.iter().any(|r| r.1 == module) && !cap_attr(&SPECTRUM[SPECTRUM.len() - 1], module, name, shaped)
}

/// Note a [`CAP_ATTRS`] name, whatever shape it is spelled in: the core
/// serves none of them, so its walk blocks on each (see
/// [`Requirements::core_attr`]).
#[cfg(feature = "cap-random")]
fn note_core_attr(req: &mut Requirements, module: &str, name: &str) {
    if CAP_ATTRS.iter().any(|(_, m, any, shape)| *m == module && (any.contains(&name) || shape.contains(&name))) {
        req.core_attr = true;
    }
}

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

/// `textwrap` asks the same question for the ROUTE only: its results are
/// `str` and `list`, so a method outside `known_method` on one is a method
/// nothing on the spectrum has. Not a pre-run stop, because an import that
/// never runs (`while False: import textwrap`) leaves a program the core
/// answers; a direct run that does import it is held (`io::hold`), and its
/// uncaught `AttributeError` refuses at the exit as `name-hint`.
#[cfg(feature = "cap-textwrap")]
fn routed_past_by_textwrap(req: &Requirements) -> bool {
    req.imports.contains("textwrap")
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
/// …) by one whose capability lists it in `CAPS`. `module-attr` is claimed only
/// out of [`CAP_ATTRS`] — a TABLE of names, never a module's name alone,
/// because claiming an attribute by its module is exactly how a program would
/// reach a sibling that refuses it again — a spawn wasted, and the ledger
/// already paid for that lesson once.
pub fn answers(v: &Variant, kind: &str, detail: &str) -> bool {
    match kind {
        "module" => served_module(v, module_of(detail)),
        // Only out of [`CAP_ATTRS`], and a shape-only name is claimed here
        // because the walk has already put every UNBLESSED spelling of it in
        // the stop slot, which overrides this verdict.
        "module-attr" => detail.rsplit_once('.').is_some_and(|(m, n)| cap_attr(v, m, n, true)),
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
/// the stop names is a refusal no rung of the spectrum answers — a lazy
/// `iglob`'s order, a keyword only CPython serves, an attribute nothing here has, a
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
    fn the_floor_rule_picks_the_larger_sibling_for_exactly_what_it_serves() {
        // What row 1 does NOT serve, row 0's refusal cannot escape to: the
        // engine is CPython and the two rows carry the same kind. This test was
        // once about identical capabilities and every kind belonged in the first
        // list; `bigint` moved to the second the day `cap-bigint` landed, which
        // is the transition the old name described as the point of the test.
        if self_index() != 0 {
            return;
        }
        for (kind, detail, imports) in [
            ("module", "import ctypes", vec!["ctypes".to_string()]),
            ("module", "import subprocess", vec!["subprocess".to_string()]),
            ("class", "class definition", vec![]),
        ] {
            let vs = verdicts(kind, detail, &imports);
            assert_ne!(engine_from_verdicts(&vs), Engine::Rust(1), "{kind}");
            assert_eq!(vs[1].kind, vs[0].kind, "{kind}: rows 0 and 1 must agree");
        }
        // …and what row 1 DOES serve, it is routed. `cap-bigint`'s two kinds are
        // the first entries in the `CAPS` kinds column, so this is also the
        // first time a RUNTIME refusal from the core names a sibling.
        for kind in ["bigint", "int-div-precision"] {
            let vs = verdicts(kind, "x", &[]);
            assert_eq!(engine_from_verdicts(&vs), Engine::Rust(1), "{kind}");
            assert_eq!(chain_after(SELF, kind, &vs), vec!["lypning-l", CPYTHON_NAME], "{kind}");
        }
        // The PARSE-time kinds `cap-future` answers: routed to row 1 when
        // every import is served there, and still decided by the import when
        // one is not (`@functools.lru_cache`). `class` stays above: a
        // decorated class is refused at parse in both rows.
        for kind in ["decorator", "kwonly"] {
            let vs = verdicts(kind, "x", &[]);
            assert_eq!(engine_from_verdicts(&vs), Engine::Rust(1), "{kind}");
            let vs = verdicts(kind, "x", &["functools".to_string()]);
            assert_eq!(engine_from_verdicts(&vs), Engine::CPython, "{kind}");
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
            "import re\nprint(re.search(r'(?P<é>x)', 'x'))",
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

    #[cfg(feature = "cap-difflib")]
    #[test]
    fn the_difflib_row_is_empty_and_the_variant_serves_nothing_on_it() {
        let row = MODULE_ATTRS.iter().find(|(m, _)| *m == "difflib").expect("no difflib row");
        assert!(row.1.is_empty());
        let m = crate::value::Value::Module("difflib");
        for n in ["SequenceMatcher", "unified_diff", "ndiff", "get_close_matches", "Differ"] {
            assert!(crate::modules::get_attr(&m, n).is_err(), "difflib.{n}");
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

pub const ONLY_CPYTHON_KINDS: &[&str] = &[
    "del",
    "dict-view",
    "dunder-missing",
    "encoding",
    "exception-chaining",
    // A lazy `glob.iglob()` where its order shows, a listing function used as
    // a value, or (lypning-l, at runtime) a visible order over a directory the
    // run has changed. No larger rung lists any differently. `glob.rs`, and
    // the static blocker in `walk_expr` below.
    "glob-order",
    "identity",
    "iterator-type-name",
    "json",
    // Every refusal `math.rs` raises: a domain error, a `TypeError` on a
    // non-number, a wrong argument count. Each is a message text CPython owns,
    // and EVERY variant carries the same `math.rs` — so falling to a larger
    // sibling would spend a spawn to be told no in the same words.
    "math",
    // An uncaught NameError on a module name: CPython's import hint rests on a
    // suggestion search no variant runs (`err::forgot_import`).
    "name-hint",
    "nan-identity",
    "nan-order",
    "percent-format",
    "random",
    "repr-unicode",
    "set-method",
    "set-order",
];

/// Does this refusal kind rule out every Rust variant? See [`ONLY_CPYTHON_KINDS`].
pub fn only_cpython(kind: &str) -> bool {
    ONLY_CPYTHON_KINDS.contains(&kind)
}

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
            walk_program(&body, &mut req);
            #[cfg(not(feature = "cap-future"))]
            future_route_stop(src, &body, &mut req);
            imports = req.imports.iter().cloned().collect();
            let reads_stdin = reads_stdin || req.reads_stdin;
            // A refusal the walk spelled outright is the more specific one and
            // wins the slot; a method no rung models is the fallback. Both mark
            // the whole spectrum rather than one rung.
            let method = method_wide_stop(req.method_stop.take(), &imports);
            let stop = req.spectrum_stop.take().or(req.route_stop.take()).or(method);
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
    /// A [`CAP_ATTRS`] name was spelled — `random.sample`, `sys.version_info`
    /// — which the CORE's walk blocks as `module-attr` and a capability
    /// answers. Only where that capability is built: it is half of
    /// [`core_admits`], and the core never asks it.
    #[cfg(feature = "cap-random")]
    core_attr: bool,
    /// `.fromhex` was spelled — `cap-binascii`'s, which the core lacks.
    #[cfg(feature = "cap-binascii")]
    fromhex: bool,
    imports: BTreeSet<String>,
    blocker: Option<(String, String)>,
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
    /// `from base64 import b64decode [as d]` — the bound name of a base64
    /// FUNCTION, so that a bare `d(...)` is decided by the same walk that
    /// decides `base64.b64decode(...)`. Only on the variant that serves the
    /// module: the core has no decoder to decide with, and reaches the same
    /// programs through [`MODULE_ATTRS`] and the import blocker instead.
    #[cfg(feature = "cap-base64")]
    base64_names: Vec<(String, String)>,
    /// The base64 refusal that stops the run, as `(kind, detail)`, recorded
    /// even when an EARLIER blocker won the `--plan` row. Read only by
    /// [`base64_static_check`], which runs before the first statement of a
    /// `-c` run — the path the chain reaches this binary by, and the one
    /// `route()` never sees (#48). It is not read by [`route`]: in the CORE
    /// the first blocker is `module: import base64`, which lypning-l answers,
    /// and lypning-l then refuses statically for the cost of one parse.
    #[cfg(feature = "cap-base64")]
    base64_stop: Option<(String, String)>,
    /// `from textwrap import fill [as f]` — the bound name of a served
    /// `textwrap` function, so a bare `f(...)` is decided by the same walk
    /// that decides `textwrap.fill(...)`. Not scoped: a later rebinding of
    /// the name leaves it here, which can only over-refuse.
    textwrap_names: Vec<(String, String)>,
    /// Every name `import time [as t]` bound to the MODULE, and every name
    /// `from time import f [as g]` bound to a served FUNCTION. Deliberately
    /// NOT scoped or cleared by a rebinding, unlike the pattern table: every
    /// question the walk asks through these is "must this refuse?", so a name
    /// held too long costs a CPython spawn and a name given up too early could
    /// let a `struct_time` or a long sleep through. Only on the variant that
    /// serves the module; the core blocks the import instead.
    time_mods: Vec<String>,
    time_names: Vec<(String, &'static str)>,
    /// The `time.gmtime()` call nodes a served `strftime(<literal>, …)` above
    /// them blessed, by identity, as `glob_blessed` does for glob.
    time_blessed: Vec<*const Expr>,
    /// How many loops, `def`s, `lambda`s and comprehensions the walk is inside:
    /// a `time.sleep` below zero of them runs at most once per run.
    time_nest: u32,
    /// `from binascii import hexlify [as h]` — the bound name of a binascii
    /// FUNCTION, for the reason [`Self::base64_names`] exists. Its refusals
    /// share the `base64_stop` slot: one static check, one substring guard.
    #[cfg(feature = "cap-binascii")]
    binascii_names: Vec<(String, String)>,
    /// `from glob import glob [as g]` — the bound name of a glob FUNCTION, so
    /// that a bare `g(...)` is seen as the call it is. Without it the order
    /// blocker below would miss the one spelling that hides the module name.
    glob_names: Vec<(String, String)>,
    /// The call nodes the parent blessed as order-blind, by identity. The walk
    /// borrows one live AST for its whole run, so no node is freed and no
    /// address is reused; nothing is dereferenced through these. On lypning-l
    /// [`static_stop_check`] hands them to `glob::set_order_shown`: the run
    /// evaluates this same AST, so a blessed address is the node `eval`
    /// dispatches, and every other glob call's order counts as shown.
    glob_blessed: Vec<*const Expr>,
    /// Every name the walk met in a VALUE position. After the walk, one of
    /// them naming the `glob` module or a listing function — under any alias,
    /// including one bound BELOW the reference (`def f(): return G` above
    /// `import glob as G`) — is a module escape, and every listing's order
    /// counts as shown.
    #[cfg(feature = "cap-glob")]
    glob_values: Vec<std::rc::Rc<str>>,
    /// The `random.Random` / `sys.version_info` nodes whose PARENT is one of
    /// the shapes [`CAP_ATTRS`] serves them in, by identity like
    /// `glob_blessed`. Filled by [`bless_cap_shapes`] before the child is
    /// walked.
    cap_blessed: Vec<*const Expr>,
    /// A refusal no rung serves, for the ROUTE only: an unblessed shape or an
    /// unlisted name on a [`CAP_ATTRS`] module. Not read by
    /// [`static_stop_check`] — a direct run of either variant refuses at the
    /// attribute itself, at the same point in both, so there is no answer a
    /// pre-run refusal would protect and one the core would lose.
    route_stop: Option<(String, String)>,
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
    /// refusal then fires at RUNTIME, which past an effect the barrier cannot
    /// take back is a number invariant 2 forbids retrying. That is issue #48,
    /// and this slot
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
    /// The bare NAME of the first `method:` blocker the walk recorded, kept even
    /// when an EARLIER blocker won the `--plan` row — and read by
    /// [`method_wide_stop`], which decides whether it stops the whole spectrum.
    ///
    /// The name and not the `.name()` detail, because the decision is a lookup
    /// in [`CAP_METHODS`] and re-deriving the name from the rendered detail is
    /// how the two spellings drift apart.
    ///
    /// It is here for the one thing [`Requirements::block`] being first-wins
    /// cannot express. A program that imports a capability module blocks FIRST
    /// on the import, which a larger sibling answers — so [`verdicts`] marks
    /// that sibling "can run" and the method blocker recorded three statements
    /// later is dropped. `import base64` then routes
    /// `base64.b64encode((255).to_bytes(2, "big"))` into lypning-l, which has
    /// no `int.to_bytes` either and raises `AttributeError` at exit 1 — the
    /// program's own exit, which the chain never retries, where the same
    /// program refused cleanly at 90 before the module was served and CPython
    /// answered it one spawn later. `verdicts` re-checks the IMPORTS against a
    /// larger rung and nothing else, so this is the second thing it re-checks.
    ///
    /// `int.to_bytes` is SERVED now (`methods::INT_METHODS`), so that exact
    /// program routes and answers. The example is kept as the record of the
    /// defect — the mechanism it describes is unchanged, and every name still
    /// outside [`known_method`] reaches it the same way.
    method_stop: Option<String>,
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

    /// See [`Requirements::route_stop`].
    fn stop_route(&mut self, kind: &str, detail: String) {
        if self.route_stop.is_none() {
            self.route_stop = Some((kind.to_string(), detail));
        }
    }

    /// Does any capability in THIS binary still want the binding table filled?
    ///
    /// One question rather than a growing `||` chain at the call site, and the
    /// capability halves are behind their features so the CORE's
    /// `bind_pattern` is the bytes it always was — the frozen variant gains no
    /// capability code. `re` and `glob` are unconditional because the core
    /// decides both of those itself.
    fn tracks_literals(&self) -> bool {
        if self.imports.contains("re") || self.imports.contains("glob") {
            return true;
        }
        #[cfg(feature = "cap-base64")]
        if self.imports.contains("base64") {
            return true;
        }
        #[cfg(feature = "cap-binascii")]
        if self.imports.contains("binascii") {
            return true;
        }
        // `PatLit::HashCtor`: which constructor a name holds, which only the
        // variant that HAS the constructors has any use for.
        #[cfg(feature = "cap-hashlib")]
        if self.imports.contains("hashlib") {
            return true;
        }
        false
    }

    /// A base64 refusal, for the run. It does NOT touch [`Self::blocker`]: the
    /// `--plan` row for these programs is `module: import base64`, which is
    /// what the core sees and what the build order should keep ranking.
    #[cfg(feature = "cap-base64")]
    fn stop_base64(&mut self, kind: &str, detail: String) {
        if self.base64_stop.is_none() {
            self.base64_stop = Some((kind.to_string(), detail));
        }
    }

    /// A method no type THIS binary models has — recorded as the blocker, and
    /// kept in [`Self::method_stop`] whether or not it won that slot.
    ///
    /// `method` is the one kind whose meaning differs between variants, because
    /// [`known_method`] is compiled per variant. Every other kind a walk
    /// produces means the same thing in both, so `blocker` alone carries it.
    fn block_method(&mut self, name: &str) {
        // `bytes.fromhex` is `cap-binascii`'s, so its block names a kind that
        // capability's row lists and the router sends the program there.
        self.block(if name == "fromhex" { "fromhex" } else { "method" }, format!(".{name}()"));
        if self.method_stop.is_none() {
            self.method_stop = Some(name.to_string());
        }
    }

    fn block_glob_order(&mut self) {
        self.stop("glob-order", GLOB_ORDER.to_string());
    }

    /// Did a name bound to the `glob` module, or to one of its two listing
    /// functions, escape as a VALUE anywhere in the program? Then no call
    /// node tells the run what it will be handed, and every listing's order
    /// counts as shown. See [`Self::glob_values`].
    #[cfg(feature = "cap-glob")]
    fn glob_escaped(&self) -> bool {
        self.glob_values.iter().any(|n| {
            let n = n.as_ref();
            n == "glob"
                || self.aliases.iter().any(|(a, p)| a == n && p == "glob")
                || self
                    .glob_names
                    .iter()
                    .any(|(b, f)| b == n && matches!(f.as_str(), "glob" | "iglob"))
        })
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
    /// some other import keeps the `--plan` row.
    ///
    /// It does NOT keep the route. `import re, itertools` blocks first on `re`,
    /// which lypning-l answers, and `itertools.count` three statements later
    /// was dropped — so the core sent the program to a sibling that refuses it
    /// at RUNTIME: a spawn wasted, and past a committed barrier exit 1. No rung
    /// serves the attribute, which is what the stop slot says, so it is
    /// recorded there and [`finish_route`] routes the program to CPython.
    fn escalate(&mut self, module: &str, name: &str) {
        let same = match &self.blocker {
            Some((k, d)) => k == "module" && module_of(d) == module,
            None => true,
        };
        let detail = format!("{module}.{name}");
        if same {
            self.blocker = Some(("module-attr".to_string(), detail));
        } else {
            // The ROUTE's stop, not the run's: the attribute refuses where it
            // is evaluated, in every variant, and one that never is (an
            // `except m.X` no exception reaches) is the core's answer too.
            self.stop_route("module-attr", detail);
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
        // `ValueError = len` turns a later `except ValueError` into CPython's
        // TypeError; the runtime reads the name through the scopes and
        // refuses, and this stops the route before the first statement.
        if crate::builtins::EXCEPTIONS.contains(&name) {
            self.stop("exception", format!("rebinding of {name}"));
        }
        if !self.tracks_literals() {
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

/// A WHOLE program's walk: [`walk_block`] after [`time_prescan`], on the
/// variant that serves `time`, and exactly `walk_block` everywhere else.
fn walk_program(body: &[Stmt], req: &mut Requirements) {
    time_prescan(body, req);
    walk_block(body, req);
}

/// Every name any `import time [as t]` or `from time import f [as g]` binds,
/// ANYWHERE in the program, collected before the walk judges a single
/// `time.X`. The walk reads the source in text order and the program does not
/// run in it: a `def` or `lambda` written above `import time`, a `global time`
/// imported inside a function, and a loop whose later iteration runs an import
/// its earlier one skipped all call `time.X` with the module already bound —
/// and a walk that learned the name only at the import let each of them past
/// every rule below (a bare 9-tuple printed for a `struct_time`, a sleep in a
/// loop). Held program-wide and never given up, which only ever refuses more.
fn time_prescan(body: &[Stmt], req: &mut Requirements) {
    for s in body {
        match s {
            Stmt::Import { names } => {
                for (path, bound) in names {
                    if path.as_ref() == "time" && !req.time_mods.iter().any(|m| m == bound.as_ref()) {
                        req.time_mods.push(bound.to_string());
                    }
                }
            }
            Stmt::FromImport { module, names } if module.as_ref() == "time" => {
                for (n, bind) in names {
                    if let Some(f) = TIME_SERVED.iter().copied().find(|x| *x == n.as_ref()) {
                        req.time_names.push((bind.to_string(), f));
                    }
                }
            }
            Stmt::If { arms, els } => {
                arms.iter().for_each(|(_, b)| time_prescan(b, req));
                time_prescan(els, req);
            }
            Stmt::For { body, els, .. } | Stmt::While { body, els, .. } => {
                time_prescan(body, req);
                time_prescan(els, req);
            }
            Stmt::Def { body, .. } => time_prescan(body, req),
            Stmt::Try { body, handlers, els, finally } => {
                time_prescan(body, req);
                handlers.iter().for_each(|h| time_prescan(&h.body, req));
                time_prescan(els, req);
                time_prescan(finally, req);
            }
            Stmt::With { body, .. } => time_prescan(body, req),
            _ => {}
        }
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
                if path.as_ref() == "time" && !req.time_mods.iter().any(|m| m == bound.as_ref()) {
                    req.time_mods.push(bound.to_string());
                }
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
                // `from base64 import b64decode [as d]`. The unserved
                // names are already blocked correctly by both variants — the
                // arm below spells `module` in the core and `module-attr` on
                // lypning-l — but only the BLOCKER slot; the RUN needs its own
                // stop, or `from base64 import b32encode` refuses one spawn
                // into the chain rather than in the router that sent it there.
                #[cfg(feature = "cap-base64")]
                "base64" => {
                    for (n, bind) in names {
                        if BASE64_SERVED.contains(&n.as_ref()) {
                            req.base64_names.push((bind.to_string(), n.to_string()));
                        } else {
                            req.stop_base64("module-attr", format!("base64.{n}"));
                        }
                    }
                }
                // `from binascii import hexlify [as h]`, exactly as `base64`
                // above: a served name is a call this walk still decides, and
                // an unserved one (`Error`, `crc32`) must stop the RUN too.
                #[cfg(feature = "cap-binascii")]
                "binascii" => {
                    for (n, bind) in names {
                        if BINASCII_SERVED.contains(&n.as_ref()) {
                            req.binascii_names.push((bind.to_string(), n.to_string()));
                        } else {
                            req.stop_base64("module-attr", format!("binascii.{n}"));
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
                // `from textwrap import …`: a served name binds a function
                // whose calls this walk still decides; an unserved one is a
                // stop, for the run, beside the blocker the arm below records.
                "textwrap" => {
                    for (n, bind) in names {
                        if TEXTWRAP_SERVED.contains(&n.as_ref()) {
                            req.textwrap_names.push((bind.to_string(), n.to_string()));
                        } else {
                            req.stop_only("module-attr", format!("textwrap.{n}"));
                        }
                    }
                }
                // `from time import perf_counter [as pc]` binds a function whose
                // calls this walk still decides. `gmtime` and `strftime` are
                // served in ONE shape, spelled through the module, and a bare
                // name bound to either is refused: a later rebinding the walk
                // reads in the wrong order could hand `gmtime()`'s tuple to
                // something that prints it. An unserved name is a `module-attr`
                // stop for the run, `stop_only` for the same reason as `hashlib`.
                "time" => {
                    for (n, bind) in names {
                        match TIME_SERVED.iter().copied().find(|x| *x == n.as_ref()) {
                            Some(f @ ("gmtime" | "strftime")) => req.stop(
                                "time",
                                format!("from time import {f}: served only as time.strftime(<literal>, time.gmtime())"),
                            ),
                            Some(f) => req.time_names.push((bind.to_string(), f)),
                            None => req.stop_only("module-attr", format!("time.{n}")),
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
                    #[cfg(feature = "cap-random")]
                    note_core_attr(req, module, n);
                    if crate::modules::get_attr(&m, n).is_err() {
                        let d = format!("{module}.{n}");
                        // `from random import Random`: a shape-only name
                        // bound bare is a shape no rung serves.
                        if no_rung_serves(module, n, false) {
                            req.stop_route("module-attr", d.clone());
                        }
                        req.block("module-attr", d);
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
            // The body runs once per item, so a `time.sleep` in it is not one
            // the walk can bound (`time.rs`, the sleep policy).
            {
                req.time_nest += 1;
            }
            walk_block(body, req);
            walk_block(els, req);
            {
                req.time_nest -= 1;
            }
        }
        Stmt::While { cond, body, els } => {
            {
                req.time_nest += 1;
            }
            walk_expr(cond, req);
            walk_block(body, req);
            walk_block(els, req);
            {
                req.time_nest -= 1;
            }
        }
        Stmt::Return(Some(e)) | Stmt::Raise { exc: Some(e) } => walk_expr(e, req),
        Stmt::Assert { test, msg } => {
            walk_expr(test, req);
            if let Some(m) = msg {
                walk_expr(m, req);
            }
        }
        Stmt::Def {
            name,
            body,
            params,
            #[cfg(feature = "cap-future")]
            decos,
        } => {
            // The decorators are evaluated OUT HERE too, and first — before
            // the defaults and before the name is bound.
            #[cfg(feature = "cap-future")]
            for d in decos {
                walk_expr(d, req);
            }
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
            // A body runs once per CALL, and the walk has no call graph.
            {
                req.time_nest += 1;
            }
            walk_block(body, req);
            {
                req.time_nest -= 1;
            }
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
                    // `import binascii as b` then `except b.crc32:` names the
                    // same module, and reading the alias literally skipped
                    // both the resolution below and the binascii escalation.
                    let dotted = k.rsplit_once('.').map(|(prefix, leaf)| {
                        let module = req
                            .aliases
                            .iter()
                            .find(|(a, _)| a == prefix)
                            .map_or(prefix, |(_, p)| p.as_str());
                        (module.to_string(), leaf)
                    });
                    // SERVED is not enough: it must be a CLASS. `except
                    // binascii.hexlify:` and `except math.sqrt:` resolved, so
                    // the handler was admitted and silently never matched,
                    // where CPython raises TypeError the moment an exception
                    // reaches it.
                    let (ok, every_rung) = except_clause(
                        dotted.as_ref().map(|(m, l)| (m.as_str(), *l)),
                        k,
                    );
                    if !ok {
                        req.block("exception", format!("except {k}"));
                        // `import csv` then `except int:` blocked on the
                        // `module` line first, so the core routed the program
                        // to lypning-l on the import and this blocker was
                        // dropped: the handler ran as a non-match and printed
                        // at exit 0 where CPython raises TypeError. When the
                        // verdict cannot differ between rungs — a bare name,
                        // or a module this binary already serves — it stops
                        // the whole spectrum. A module only a larger rung
                        // serves (`except csv.Error` from the core) is that
                        // rung's to decide, and keeps the route it had.
                        if every_rung {
                            req.stop_only("exception", format!("except {k}"));
                        } else if dotted
                            .as_ref()
                            .is_some_and(|(m, _)| crate::modules::MODULES.contains(&m.as_str()))
                        {
                            // A module only a capability of THIS binary serves
                            // (`except glob.X`, `except time.error`): the verdict is
                            // the capability's, and a handler no exception
                            // reaches is never evaluated — the core, whose
                            // walk sees no such module, runs the program and
                            // answers. So the ROUTE goes past the rungs, and a
                            // direct run refuses where an exception reaches
                            // the clause (`eval.rs`), as the core's would.
                            req.stop_route("exception", format!("except {k}"));
                        }
                    }
                    // `except binascii.<anything>`: no binascii name is a class
                    // any rung serves (`Error` is not served, and the rest are
                    // functions), so the core must not route the program into
                    // lypning-l on the strength of the import, where the
                    // handler would refuse only once an exception reached it —
                    // possibly past a write — or, statically, in lypning-l's
                    // own walk (#48). The same holds for every module a
                    // capability of this branch added — `statistics`
                    // (`except statistics.StatisticsError`), `itertools`,
                    // `difflib`, `textwrap`, `time`, `ast`: none serves a class.
                    // `except csv.Error` keeps the route it had before these
                    // rows existed.
                    if let Some((m @ ("binascii" | "statistics" | "itertools" | "difflib" | "textwrap" | "time" | "ast"), leaf)) =
                        dotted.as_ref().map(|(m, l)| (m.as_str(), *l))
                    {
                        req.escalate(m, leaf);
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
        // The numeric probes. `bool` reads `int`'s table, so probing `int`
        // covers both; `.bit_length()`, `.to_bytes()`, `.from_bytes()`,
        // `.is_integer()` and `.as_integer_ratio()` are the names they add, and
        // without them here the walk blocks every program that types one — the
        // very programs the tables were added to run.
        || crate::methods::method_name(&crate::value::ival(0), name).is_some()
        || crate::methods::method_name(&crate::value::Value::Float(0.0), name).is_some()
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

/// Every method NAME that a capability adds and the type probes in
/// [`known_method`] cannot see, per module: **the routing half of a
/// capability's method surface, carried by every variant.**
///
/// The core is the binary that routes (`engines.route` asks it) and the binary
/// with none of these capabilities compiled in, so it is the one that has to
/// answer a question about a rung above it. That is the same economy as
/// [`MODULE_ATTRS`], [`GLOB_SERVED`] and `repat.rs`: the names are ROUTING
/// tokens, not implementation, and the implementation stays behind its
/// `cap-*`. The three capability `known_method` functions read this table, so
/// there is exactly one list per module and no copy to keep in step;
/// `tests/test_method_tables.py` holds it to the DISPATCH tables next to it,
/// which are a different thing and not merged into it.
///
/// `base64`, `csv` and `glob` have no row because their capabilities add no
/// method name at all. Every value they hand back is a `bytes`, a `str`, a
/// `list` or a `dict` the core already models, which `base64::call`'s docstring
/// states for `base64` and `csv.rs`/`glob.rs` state by having no
/// `known_method` to export.
pub(crate) const CAP_METHODS: &[(&str, &str)] = &[
    // The one name that is not on any other type: `Counter` is a tagged `Dict`
    // and a probe dict has no tag, so `known_method` cannot see it.
    ("collections", "most_common"),
    // `hashlib::HASH_ATTRS`, less the names a probe type already answers
    // (`copy`, `update`) and less `hashlib::ROUTER_WITHHELD`, which is where
    // the reason for the subtraction is written. It is the first row that is
    // deliberately SMALLER than what the module serves: this table is read by
    // name and cannot see the receiver, and `.name` under `import hashlib` is
    // a name the engine answers on a hash object and on nothing else.
    ("hashlib", "digest digest_size hexdigest"),
    // `pathlib::METHODS` and the property arms of `pathlib::get_attr`, plus
    // `cwd`. Every name here is answered EXACTLY; a name CPython has and this
    // row does not is a refusal, never an `AttributeError`.
    (
        "pathlib",
        "as_posix cwd exists is_absolute is_dir is_file joinpath mkdir name open parent parents \
         parts read_bytes read_text relative_to stem suffix suffixes unlink with_name with_stem \
         with_suffix write_bytes write_text",
    ),
    // The methods of a `random.Random(int)` instance, `randobj::METHODS`:
    // no probe type has any of them, so without the row every instance
    // program would stop on its first `.randint()` in the core's walk.
    ("random", "choice getrandbits randint random randrange sample seed shuffle"),
    // `re::PATTERN_METHODS`, `re::MATCH_METHODS` and the seven read-only
    // attributes. `groupindex`, `scanner`, `expand`, `lastindex`,
    // `lastgroup` and `regs` are deliberately absent: a shape the engine does
    // not answer is cheaper as a static block — the program goes straight to
    // CPython — than as a runtime refusal, which costs an in-process run first
    // and can land after a side effect the commit barrier has already let
    // through.
    (
        "re",
        "end endpos findall finditer flags fullmatch group groupdict groups match pattern pos re search \
         span split start string sub subn",
    ),
];

/// Is `name` one of the method names `module`'s capability adds?
///
/// A space-separated word list rather than a `&[&str]`: the core carries these
/// names because it must, and 45 fat pointers cost it more than the text does.
/// Read on the routing path only — never per method call — so a scan is the
/// right shape.
pub(crate) fn cap_serves(module: &str, name: &str) -> bool {
    CAP_METHODS
        .iter()
        .any(|(m, names)| *m == module && names.split_whitespace().any(|w| w == name))
}

/// Could a rung ABOVE this one answer `.name()`, given what the program
/// imported?
///
/// The question [`known_method`] and its two import-gated companions cannot
/// answer, because those three are compiled per variant and this one is asked
/// in the core, about `lypning-l`. Import-gated for the reason
/// [`pathlib_method`] states: `.name` and `.start` and `.parts` are ordinary
/// names on other objects, and the import is what makes the optimism honest.
fn cap_method(name: &str, imports: &[String]) -> bool {
    CAP_METHODS.iter().any(|(m, names)| {
        imports.iter().any(|i| i == m) && names.split_whitespace().any(|w| w == name)
    })
}

/// Does the `method:` blocker this walk recorded stop EVERY rung of the
/// spectrum, and therefore belong in `finish_route`'s `stop` slot?
///
/// Only when no capability above this rung serves the NAME. The blocker stays
/// out of `Route::kind` either way: `--plan` ranks what a program hit FIRST,
/// and that is still the import.
///
/// **This used to ask about the import and not the name**, and the difference
/// is a wrong answer. `method` is the one blocker kind whose meaning differs
/// between variants, so the first cut suppressed the whole-spectrum stop for
/// any program importing `collections`, `pathlib` or `re` — the three modules
/// whose capability brings method names with it. That is far wider than the
/// ambiguity: `import re` says nothing about `.to_bytes`, which no rung has, so
/// `import base64, re; base64.b64encode((255).to_bytes(2, "big"))` routed to
/// lypning-l and raised `AttributeError` at exit 1 — the program's own exit,
/// which the chain never retries — where CPython prints `b'AP8='`. Asking
/// [`CAP_METHODS`] for the name keeps every program the hatch was opened for
/// (`.most_common` under `import collections`, `.group` under `import re`,
/// `.with_suffix` under `import pathlib` — a name only the variant that HAS the
/// capability can answer) and lets go of every program it was not.
///
/// `.to_bytes` is in [`known_method`] now and that program routes and answers;
/// the example is the record of the defect, not a claim about today's tables.
fn method_wide_stop(method: Option<String>, imports: &[String]) -> Option<(String, String)> {
    let name = method?;
    // Its block names `cap-binascii`'s own kind, which routes it; no import
    // has to vouch for the name.
    if cap_method(&name, imports) || name == "fromhex" {
        return None;
    }
    Some(("method".to_string(), format!(".{name}()")))
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
///
/// The names are [`CAP_METHODS`] and not a `matches!` here, for the reason
/// [`pathlib_method`] and [`re_method`] give: the binary that ROUTES is the
/// core, [`method_wide_stop`] asks it the same question about the same names,
/// and two tables that must agree are one table.
#[cfg(feature = "cap-hashlib")]
fn hash_method(req: &Requirements, n: &str) -> bool {
    req.imports.contains("hashlib") && crate::hashlib::known_method(n)
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
/// refusal has already cost the spawn the router decided, and past a side
/// effect the barrier cannot take back it cannot fall onward at all: it becomes
/// exit 1, which the chain never retries. `route.rs` learned that from the `re`
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
/// one spawn already spent, and, while `os.makedirs()` still committed the
/// barrier (issue #51), exit 1 and a chain that could not fall onward at all.
/// The pattern parser is `repat.rs` for exactly
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
// `glob.glob()` returns a list in the filesystem's order, and lypning-l
// computes that order the way CPython does (`glob.rs`), so an eager call is
// served in every position. Two shapes are still decided HERE, before
// anything runs: `glob.iglob()` where its order shows — CPython's generator
// reads each directory only after the loop body before it has run, and a
// body that writes changes CPython's answer — and `glob`/`iglob` used as a
// VALUE, whose calls the walk cannot see. Both are `glob-order`.
//
// **None of it is behind `cfg(feature = "cap-glob")`, and that is deliberate.**
// It is pure walker logic — a position test over the AST with no glob
// implementation behind it — so it belongs in the routing table every variant
// carries whole, next to `SPECTRUM` and `CAPS`. The core is the binary
// `engines.route()` asks; while this rule was gated, the core saw only
// `module: import glob`, read `cap-glob` off `lypning-l`'s row and predicted
// `lypning-l` for programs `lypning-l` refuses with `glob-order` — one wasted
// spawn each. The converse holds as well: the eager `glob.glob` stop was
// REMOVED from this walk rather than gated, because the stop slot overrides
// every verdict in [`finish_route`], and a core that kept it would route every
// such program to CPython whatever lypning-l can answer.

/// The order-blind wrappers: builtins whose answer is the same for every
/// permutation of the list they are handed. A `glob.iglob(...)` call that is
/// a DIRECT argument of one of these (and whose flag below is `true`) is
/// served; everywhere else it is a `glob-order` blocker. A `glob.glob(...)`
/// call is served anywhere, and the blessing still matters for it on
/// lypning-l: a blessed call's listing is answered over a directory this run
/// has changed, and an unblessed one's refuses (`glob.rs`,
/// [`static_stop_check`]).
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
/// refusal. Runtime is the one place this rule costs a spawn — and, until issue
/// #51 made a directory reversible, `os.mkdir("D")` before it meant exit 1 with
/// the directory left behind and no answer, where the core refused cleanly at
/// 90 and the chain got the answer from CPython. Every other name here answers a
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

/// The detail of the static blocker, spelled once so `--plan` ranks one row
/// for it however it was reached.
const GLOB_ORDER: &str = "iglob() outside sorted/min/max/any/all/sum/`in`, or \
     glob/iglob as a value: CPython lists lazily or out of the walk's sight";

/// The `base64` attributes lypning-l serves, and therefore the only ones ANY
/// rung of the spectrum answers. It IS the [`MODULE_ATTRS`] row rather than a
/// copy of it, and `base64::tests::the_route_table_names_exactly_what_is_served`
/// holds it to `base64.rs` in both directions.
///
/// Four names, and the rest of the module — `b16encode`/`b16decode`,
/// `b32encode`/`b32decode`, `b32hexencode`/`b32hexdecode`, `b85encode`/
/// `b85decode`, `a85encode`/`a85decode`, `z85encode`/`z85decode`,
/// `encodebytes`/`decodebytes`, `standard_b64encode`/`standard_b64decode` —
/// is a `module-attr` refusal. `standard_b64*` looks like a synonym for the two
/// served names and is not one worth serving: nothing in the corpus (mined
/// 2026-09-06 over 3,688 entries) spells it, and every name here is bytes.
///
/// Sorted, because [`served_attr`] reads it as the [`MODULE_ATTRS`] row and
/// that row is asserted sorted.
pub const BASE64_SERVED: &[&str] =
    &["b64decode", "b64encode", "urlsafe_b64decode", "urlsafe_b64encode"];

/// The `binascii` attributes lypning-l serves — the [`MODULE_ATTRS`] row,
/// held to `binascii.rs` by `binascii::tests::the_route_table_names_exactly_what_is_served`.
/// Sorted, for the reason [`BASE64_SERVED`] is. `Error` is deliberately absent:
/// the class does not exist in this engine, so `except binascii.Error` is a
/// static `module-attr` block in the CORE's walk.
pub const BINASCII_SERVED: &[&str] =
    &["a2b_base64", "a2b_hex", "b2a_base64", "b2a_hex", "hexlify", "unhexlify"];

/// Which base64 keyword arguments are served, and the refusal line for the rest
/// — one function, so the WALK and `base64::call` refuse with the same words.
///
/// **Two predicates, because there are two questions.** One `falsy` answering
/// both of them ("was the argument omitted" and "is the argument the default")
/// answered `altchars=0` and `altchars=False` at exit 0, where CPython 3.14.5
/// raises — measured on 2026-09-06, all five spellings, in both directions:
///
/// ```text
///   b64decode(b'aGk=', altchars=None)   b'hi'   b64encode(b'hi', altchars=None)   b'aGk='
///   b64decode(b'aGk=', altchars=b'')    Assert  b64encode(b'hi', altchars=b'')    Assert
///   b64decode(b'aGk=', altchars='')     Assert  b64encode(b'hi', altchars='')     Assert
///   b64decode(b'aGk=', altchars=0)      Type    b64encode(b'hi', altchars=0)      Type
///   b64decode(b'aGk=', altchars=False)  Type    b64encode(b'hi', altchars=False)  Type
/// ```
///
/// An ABSENT `altchars` is the default and `None` is the only value that spells
/// absent — CPython's test is `if altchars is not None`, and everything past it
/// either reaches `_bytes_from_decode_data` (a `TypeError` for an `int` or a
/// `bool`) or `assert len(altchars) == 2` (an `AssertionError` for `b''` and
/// `''`). A PRESENT falsy `altchars` is a VALUE, and one CPython rejects.
/// `validate=` is the other question and keeps the other predicate: it reaches
/// C through a `bool` converter that calls `PyObject_IsTrue`, so every falsy
/// value there really is `validate=False`.
///
///   * `altchars=` is a parameter of `b64encode`/`b64decode` ONLY, and only
///     `altchars=None` is served. The urlsafe pair takes no keyword at all, so
///     `urlsafe_b64encode(s, altchars=None)` is a CPython `TypeError` and must
///     not be answered here either.
///   * `validate=` is `b64decode`'s alone — `urlsafe_b64decode` does not
///     forward it — and its falsy values are served exactly: `validate=0` and
///     `validate=None` ARE `validate=False`.
///   * `validate=True` selects `binascii`'s strict mode, whose every rejection
///     is a `binascii.Error` message this engine does not write.
///
/// A value the walk cannot read is neither `is_none` nor `falsy`, so it
/// refuses, which is the safe direction for both questions.
#[cfg(feature = "cap-base64")]
pub fn base64_kw_block(name: &str, k: &str, is_none: bool, falsy: bool) -> Option<String> {
    let standard = matches!(name, "b64encode" | "b64decode");
    match k {
        "altchars" if standard && is_none => None,
        "altchars" if standard => Some(format!(
            "base64.{name}(altchars=…) with a value other than None, which is either an \
             alternative alphabet this engine does not implement or a value CPython rejects \
             with a TypeError or an AssertionError this engine does not word"
        )),
        "validate" if falsy && name == "b64decode" => None,
        "validate" if name == "b64decode" => Some(format!(
            "base64.{name}(validate=…) that is not literally False, None or 0: a truthy \
             validate selects binascii's strict mode, whose every rejection is a \
             binascii.Error message this engine does not write"
        )),
        _ => Some(format!("base64.{name}({k}=…)")),
    }
}

/// As much of a base64 argument as a walk can honestly read: the TYPE of a
/// literal — or of a literal bound to a name above the call — and its BYTES
/// when the literal is spelled at the call site.
///
/// The bytes matter here in a way they do not for `glob`, because whether a
/// decode raises is a property of the DATA: `b"aGk="` decodes and `b"aGk"` is
/// a `binascii.Error`, and the second must be refused before the program runs
/// rather than after it has made a directory. A `bytes` literal bound to a
/// NAME answers its type and not its content — [`PatLit`] is the core's table
/// and records `bytes` without the bytes — so that spelling keeps the runtime
/// backstop, which `tests/test_base64_grid.py::RUNTIME_BACKSTOP` pins.
#[cfg(feature = "cap-base64")]
fn base64_arg(e: &Expr, req: &Requirements) -> Option<(&'static str, Option<Vec<u8>>)> {
    Some(match e {
        Expr::Bytes(b) => ("bytes", Some(b.as_ref().clone())),
        Expr::Str(s) => ("str", Some(s.as_bytes().to_vec())),
        Expr::FString(parts) => match fstring_text(parts) {
            Some(t) => ("str", Some(t.as_bytes().to_vec())),
            None => ("str", None),
        },
        Expr::Name(n) => match req.pattern_named(n)? {
            PatLit::Str(t) => ("str", Some(t.as_bytes().to_vec())),
            PatLit::Bytes => ("bytes", None),
            PatLit::Other(t) => (t, None),
            // The only place the two capabilities meet: a name holding a
            // hashlib CONSTRUCTOR is a callable, so `from hashlib import
            // sha256 as h; base64.b64encode(h)` is a TypeError CPython owns.
            // Saying so here refuses it before the barrier rather than after,
            // which is what the rest of this function is for.
            #[cfg(feature = "cap-hashlib")]
            PatLit::HashCtor(_) => ("builtin_function_or_method", None),
        },
        e => (literal_type(e)?, None),
    })
}

/// Does this expression name the `base64` MODULE, through an alias, in a
/// program that imported it? The import is what makes the name mean the module
/// — `base64 = 3; base64.b64encode(x)` is not a module attribute and the walk
/// must not say it is.
#[cfg(feature = "cap-base64")]
fn base64_module(b: &Expr, req: &Requirements) -> bool {
    req.imports.contains("base64")
        && matches!(resolve_module(b, &req.aliases), Some(crate::value::Value::Module("base64")))
}

/// Which base64 FUNCTION this callee names, if any — one of [`BASE64_SERVED`].
/// `base64.b64decode(...)`, `x.b64decode(...)` after `import base64 as x`, and
/// a bare `d(...)` bound by `from base64 import b64decode as d`.
#[cfg(feature = "cap-base64")]
fn base64_func(func: &Expr, req: &Requirements) -> Option<&'static str> {
    let n: &str = match func {
        Expr::Attr(b, n) if base64_module(b, req) => n.as_ref(),
        Expr::Name(n) => req
            .base64_names
            .iter()
            .find(|(bound, _)| bound == n.as_ref())
            .map(|(_, f)| f.as_str())?,
        _ => return None,
    };
    BASE64_SERVED.iter().copied().find(|x| *x == n)
}

/// Every refusal a `base64` call can raise that a walk can decide, hoisted out
/// of the run and into the walk.
///
/// This is issue #51 answered for this capability, and it was the mitigation
/// rather than the fix: serving the module means the program STARTS here, and
/// while `os.mkdir` committed the write barrier a refusal after one was exit 1
/// with the directory on disk and no answer. The barrier itself now takes a
/// directory back (`io::rewind`), so what this buys is what a static block
/// always buys — the refusal costs no spawn, and it still holds past the two
/// effects nothing can give back. `docs/HILLCLIMB.md` iterations 76 and 77
/// rejected two capabilities for exactly that shape.
///
/// Five things are literal in the source and therefore decidable here, in the
/// order [`crate::base64::call`] asks them, so a program is refused with the
/// same line one in-process run earlier:
///
///   1. the argument count — none, or a second positional (which is `altchars`
///      on the standard pair and a `TypeError` on the urlsafe one);
///   2. the keyword names and whether their values are the served defaults,
///      through [`base64_kw_block`];
///   3. the argument's TYPE when it is a literal — a `str` handed to an
///      encoder is CPython's `TypeError` and the commonest way this call is
///      typed wrongly;
///   4. a `str` literal holding a non-ASCII character, which is a `ValueError`
///      in `base64._bytes_from_decode_data`;
///   5. the DATA itself, through [`crate::base64::data_block`] — the same
///      function the run uses, so the walk and the run cannot disagree about
///      whether an input decodes.
///
/// **What stays a runtime refusal**, and it is one shape: an argument whose
/// VALUE the source does not spell — read from stdin, returned by a call,
/// built by a join, or held by a name bound to a `bytes` literal, which the
/// core's binding table records by type alone. Those keep the backstop in
/// `base64::call`, and a refusal there still lands past a committed barrier;
/// what the static half buys is that a literal never reaches that door.
#[cfg(feature = "cap-base64")]
fn base64_call_block(
    req: &mut Requirements,
    func: &Expr,
    args: &[Expr],
    kwargs: &[(std::rc::Rc<str>, Expr)],
    star: &[usize],
    dstar: &[Expr],
) {
    let Some(name) = base64_func(func, req) else { return };
    let Some((pos, kws)) = flatten_call(args, kwargs, star, dstar) else { return };
    let decoding = matches!(name, "b64decode" | "urlsafe_b64decode");
    let urlsafe = matches!(name, "urlsafe_b64encode" | "urlsafe_b64decode");
    let detail = match pos.len() {
        0 => Some(format!("base64.{name}() with no argument")),
        1 => None,
        _ => Some(format!("base64.{name}() with extra positional arguments")),
    }
    .or_else(|| {
        kws.iter().find_map(|(k, v)| {
            base64_kw_block(
                name,
                k,
                matches!(v, Expr::None),
                matches!(v, Expr::None | Expr::False)
                    || matches!(v, Expr::Int(n) if n.small() == Some(0)),
            )
        })
    })
    .or_else(|| {
        let (t, data) = pos.first().and_then(|e| base64_arg(e, req))?;
        if !decoding {
            return (t != "bytes").then(|| {
                format!("base64.{name}() over a {t} (CPython requires a bytes-like object)")
            });
        }
        if t != "bytes" && t != "str" {
            return Some(format!(
                "base64.{name}() over a {t} (CPython requires a bytes-like object or an ASCII str)"
            ));
        }
        let data = data?;
        if t == "str" && !data.is_ascii() {
            return Some(format!(
                "base64.{name}() over a str holding a non-ASCII character (CPython raises a ValueError this engine does not word)"
            ));
        }
        crate::base64::data_block(&data, urlsafe).map(str::to_string)
    });
    if let Some(d) = detail {
        req.stop_base64("base64", d);
    }
}

/// Which binascii FUNCTION this callee names, if any — one of
/// [`BINASCII_SERVED`], through the module (or its alias) or a name bound by
/// `from binascii import …`.
#[cfg(feature = "cap-binascii")]
fn binascii_func(func: &Expr, req: &Requirements) -> Option<&'static str> {
    let n: &str = match func {
        Expr::Attr(b, n)
            if req.imports.contains("binascii")
                && matches!(resolve_module(b, &req.aliases), Some(crate::value::Value::Module("binascii"))) =>
        {
            n.as_ref()
        }
        Expr::Name(n) => req
            .binascii_names
            .iter()
            .find(|(bound, _)| bound == n.as_ref())
            .map(|(_, f)| f.as_str())?,
        _ => return None,
    };
    BINASCII_SERVED.iter().copied().find(|x| *x == n)
}

/// Every refusal a served `binascii` call can raise that the source spells,
/// decided in the walk by [`crate::binascii::block`] — the SAME function the
/// run asks, so the two cannot disagree. A keyword's truth value is read only
/// from a literal; an argument's type and bytes through [`base64_arg`].
#[cfg(feature = "cap-binascii")]
fn binascii_call_block(
    req: &mut Requirements,
    func: &Expr,
    args: &[Expr],
    kwargs: &[(std::rc::Rc<str>, Expr)],
    star: &[usize],
    dstar: &[Expr],
) {
    let Some(name) = binascii_func(func, req) else { return };
    let Some((pos, kws)) = flatten_call(args, kwargs, star, dstar) else { return };
    let kws: Vec<(&str, Option<bool>)> = kws
        .iter()
        .map(|(k, v)| {
            // `binascii::truth`, over literals: before 3.12 `newline` is a C
            // `int`, so `None` is a TypeError and a wide int an OverflowError.
            let int = crate::err::REF_PY_MINOR < 12;
            let t = match v {
                Expr::True => Some(true),
                Expr::False => Some(false),
                Expr::None if !int => Some(false),
                Expr::Int(n) => n.small().filter(|i| !int || *i as i32 as i64 == *i).map(|i| i != 0),
                _ => None,
            };
            (*k, t)
        })
        .collect();
    let arg = pos.first().and_then(|e| base64_arg(e, req));
    let arg = arg.as_ref().map(|(t, d)| (*t, d.as_deref()));
    if let Some((k, d)) = crate::binascii::block(name, pos.len(), &kws, arg) {
        req.stop_base64(k, d);
    }
}

/// The static base64 rules, asked of a program that is ABOUT TO RUN rather than
/// of one being routed — and it is the same walk, so the two can never
/// disagree.
///
/// `<bin> -c PROG` is not routed at all, and that is exactly how the chain
/// reaches this binary once the core has picked it (#48). It runs BEFORE the
/// first statement, so the refusal is exit 90 with an empty stdout and an
/// untouched disk, and no statement of the program has run. Only for a source
/// that mentions the module, so every other program pays one substring
/// search.
#[cfg(feature = "cap-base64")]
pub fn base64_static_check(body: &[Stmt], src: &str) -> crate::err::R<()> {
    // `binascii` shares the slot; `a2b_base64` already says "base64", and
    // `hexlify` does not.
    if !src.contains("base64") && !(cfg!(feature = "cap-binascii") && src.contains("binascii")) {
        return Ok(());
    }
    let mut req = Requirements::default();
    walk_program(body, &mut req);
    match req.base64_stop {
        Some((k, d)) => Err(crate::err::unsupported(&k, &d)),
        None => Ok(()),
    }
}

/// The `glob` attributes lypning-l serves, and therefore the only ones ANY rung
/// of the spectrum answers. Every other name — `translate`, `glob0`, `glob1`,
/// `_ishidden` — is a `module-attr` refusal.
///
/// **The table is here and not in `modules.rs`, and that is the whole point.**
/// `modules::MODULES` is per-variant and has no `glob` row in the CORE, so
/// [`resolve_module`] answers `None` for `glob.` in the one binary
/// `engines.route()` asks. A program that reached `glob.translate` was
/// therefore routed to `lypning-l` on the strength of `module: import glob`,
/// ran until the attribute was touched, and refused THERE — one spawn spent,
/// and, while `os.mkdir` still committed the write barrier (issue #51), exit 1
/// with the directory left behind and no answer, where the core without
/// `cap-glob` refused cleanly at 90 and the chain got the answer from CPython.
/// A router can only read a
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
/// a refusal there has already spent a spawn, and past an effect the barrier
/// cannot take back is exit 1 with no answer, which the chain never retries.
/// `docs/HILLCLIMB.md` iteration 76 rejected the first `cap-glob` attempt for
/// exactly that shape, when `os.mkdir` was still one of those effects (#51).
/// A static blocker costs the program nothing: it was never started here.
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
/// runtime backstop — a spawn spent, and past an effect the barrier cannot take
/// back exit 1 with the output discarded, the exact shape [`hash_call_block`]
/// exists to prevent.
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

/// Does `b` name the `textwrap` module — `textwrap.…` or `t.…` after
/// `import textwrap as t`? Only for a program that imports it.
fn textwrap_module(b: &Expr, req: &Requirements) -> bool {
    if !req.imports.contains("textwrap") {
        return false;
    }
    let Expr::Name(base) = b else { return false };
    let m = req
        .aliases
        .iter()
        .find(|(a, _)| a == base.as_ref())
        .map(|(_, p)| p.as_str())
        .unwrap_or(base.as_ref());
    m == "textwrap"
}

/// Which served `textwrap` function this callee names, if any.
fn textwrap_func(func: &Expr, req: &Requirements) -> Option<&'static str> {
    let n: &str = match func {
        Expr::Attr(b, n) if textwrap_module(b, req) => n.as_ref(),
        Expr::Name(n) => req
            .textwrap_names
            .iter()
            .rev()
            .find(|(bound, _)| bound == n.as_ref())
            .map(|(_, f)| f.as_str())?,
        _ => return None,
    };
    TEXTWRAP_SERVED.iter().copied().find(|x| *x == n)
}

/// Everything about a served `textwrap` call that a walk can decide, decided
/// here rather than one statement into a program that may already have
/// written a file: a keyword outside [`textwrap_kw_served`], a `*`/`**`
/// splice, a positional count the function does not take, `width` given
/// twice, and an argument whose LITERAL type is not the one served. What is
/// left for `textwrap::call` is a value the walk cannot see.
fn textwrap_call_block(
    req: &mut Requirements,
    func: &Expr,
    args: &[Expr],
    kwargs: &[(std::rc::Rc<str>, Expr)],
    star: &[usize],
    dstar: &[Expr],
) {
    let Some(name) = textwrap_func(func, req) else { return };
    let (lo, hi) = match name {
        "dedent" => (1, 1),
        "indent" => (2, 2),
        _ => (1, 2),
    };
    let width_kw = kwargs.iter().any(|(k, _)| k.as_ref() == "width");
    let want = |i: usize| -> &'static str {
        match (name, i) {
            ("wrap" | "fill" | "shorten", 1) => "int",
            _ => "str",
        }
    };
    let bad_literal = args.iter().enumerate().find_map(|(i, e)| {
        let t = literal_type(e)?;
        (t != want(i)).then(|| format!("textwrap.{name}() with a {t} literal as argument {}", i + 1))
    });
    let bad_kw_literal = kwargs.iter().find_map(|(k, e)| {
        let t = literal_type(e)?;
        let want = match k.as_ref() {
            "width" => "int",
            "break_long_words" | "break_on_hyphens" => "bool",
            _ => "str",
        };
        (t != want).then(|| format!("textwrap.{name}({k}=…) with a {t} literal"))
    });
    let detail = if !dstar.is_empty() {
        format!("textwrap.{name}(**…), whose keywords a walk cannot read")
    } else if !star.is_empty() {
        format!("textwrap.{name}(*…), whose arguments a walk cannot count")
    } else if let Some((k, _)) = kwargs.iter().find(|(k, _)| !textwrap_kw_served(name, k)) {
        format!("textwrap.{name}({k}=…)")
    } else if args.len() < lo || args.len() > hi {
        format!("textwrap.{name}() with {} positional arguments", args.len())
    } else if width_kw && args.len() > 1 {
        format!("textwrap.{name}() with width given twice")
    } else if name == "shorten" && args.len() < 2 && !width_kw {
        "textwrap.shorten() without a width".to_string()
    } else if let Some(d) = bad_literal.or(bad_kw_literal) {
        d
    } else {
        return;
    };
    req.stop("textwrap", detail);
}

/// Does `b` name the `time` MODULE — `time.…`, or `t.…` after `import time as
/// t`? Every name `import time` ever bound, in any scope, and never given up:
/// see [`Requirements::time_mods`] for why the conservative direction is the
/// only one this may err in.
fn time_module(b: &Expr, req: &Requirements) -> bool {
    matches!(b, Expr::Name(n) if req.time_mods.iter().any(|m| m == n.as_ref()))
}

/// Which served `time` FUNCTION this callee names, if any: `time.f` through a
/// module name, or a bare name `from time import f [as g]` bound.
fn time_func(func: &Expr, req: &Requirements) -> Option<&'static str> {
    match func {
        Expr::Attr(b, n) if time_module(b, req) => {
            TIME_SERVED.iter().copied().find(|x| *x == n.as_ref())
        }
        Expr::Name(n) => req.time_names.iter().find(|(b, _)| b == n.as_ref()).map(|(_, f)| *f),
        _ => None,
    }
}

/// Is this `time.sleep` argument one the walk can bound: a literal that either
/// raises before sleeping (a negative number, `None`, a `str`, `bytes`) or
/// sleeps for at most one second (`time.rs`, the sleep policy)?
fn sleep_literal_ok(a: &Expr) -> bool {
    match a {
        Expr::Int(i) => i.small().is_some_and(|n| n <= 1),
        Expr::Float(f) => *f <= 1.0,
        Expr::Un(UnOp::Neg, x) => matches!(**x, Expr::Int(_) | Expr::Float(_)),
        Expr::True | Expr::False | Expr::None | Expr::Str(_) | Expr::Bytes(_) => true,
        _ => false,
    }
}

/// Everything about a served `time` call that a walk can decide, decided before
/// the program starts. `call` is the call node itself, which is how a
/// `gmtime()` knows whether the `strftime` above it blessed it.
///
///   * no keyword, `*` or `**` argument on any of them — CPython takes none
///     (a `TypeError` whose words this engine does not write), and a splice is
///     arguments the walk cannot count;
///   * the six clocks take no argument;
///   * `sleep` takes one, a literal [`sleep_literal_ok`] bounds, at a call
///     site outside every loop, `def`, `lambda` and comprehension;
///   * `strftime` takes exactly `(<str literal>, <time module>.gmtime())`, the
///     literal ASCII with directives from `%Y %m %d %H %M %S %%` only, and
///     blesses that `gmtime()` node;
///   * `gmtime` is served only where a `strftime` blessed it.
fn time_call_block(
    req: &mut Requirements,
    call: &Expr,
    f: &'static str,
    args: &[Expr],
    kwargs: &[(std::rc::Rc<str>, Expr)],
    star: &[usize],
    dstar: &[Expr],
) {
    let detail = if !kwargs.is_empty() || !star.is_empty() || !dstar.is_empty() {
        Some(format!("time.{f}() with a keyword, * or ** argument"))
    } else {
        match f {
            "sleep" if args.len() != 1 => Some("time.sleep() without exactly one argument".to_string()),
            "sleep" if req.time_nest > 0 => Some(
                "time.sleep() inside a loop, a def, a lambda or a comprehension: a later \
                 refusal re-runs the program on CPython, and the walk cannot bound how \
                 long it would have slept"
                    .to_string(),
            ),
            "sleep" if !sleep_literal_ok(&args[0]) => Some(
                "time.sleep() over anything but a literal of at most one second".to_string(),
            ),
            "sleep" => None,
            "strftime" => {
                let fmt_ok = match args.first() {
                    Some(Expr::Str(s)) => time_format_block(s).map(str::to_string),
                    _ => Some("time.strftime() over a format that is not a str literal".to_string()),
                };
                let t = args.get(1).filter(|_| args.len() == 2);
                let gm = match t {
                    Some(
                        g @ Expr::Call { func, args: a, kwargs: k, star: s, dstar: d, .. },
                    ) if a.is_empty()
                        && k.is_empty()
                        && s.is_empty()
                        && d.is_empty()
                        && matches!(&**func, Expr::Attr(b, n) if n.as_ref() == "gmtime" && time_module(b, req)) =>
                    {
                        Some(g as *const Expr)
                    }
                    _ => None,
                };
                match (fmt_ok, gm) {
                    (Some(why), _) => Some(why),
                    (None, None) => Some(
                        "time.strftime() other than time.strftime(<literal>, time.gmtime()): \
                         local time, and a struct_time, are CPython's"
                            .to_string(),
                    ),
                    (None, Some(p)) => {
                        req.time_blessed.push(p);
                        None
                    }
                }
            }
            "gmtime" if req.time_blessed.contains(&(call as *const Expr)) => None,
            "gmtime" => Some(
                "time.gmtime() outside time.strftime(<literal>, time.gmtime()): there is no \
                 struct_time here"
                    .to_string(),
            ),
            _ if !args.is_empty() => Some(format!("time.{f}() with an argument")),
            _ => None,
        }
    };
    if let Some(d) = detail {
        req.stop("time", d);
    }
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

/// Is an `except` clause an exception CLASS this binary can match — and would
/// every rung give the same answer? `module` is the dotted prefix, already
/// resolved through `import … as` when the caller can; `k` is the clause as
/// written. The walk above asks it statically, and `eval` asks it again when
/// an exception actually reaches the clause, so a program run directly with
/// `-c` refuses where CPython raises TypeError instead of skipping the handler.
pub fn except_clause(dotted: Option<(&str, &str)>, k: &str) -> (bool, bool) {
    match dotted {
        Some((module, leaf)) => match crate::modules::MODULES.iter().find(|m| **m == module) {
            Some(m) => (
                matches!(
                    crate::modules::get_attr(&crate::value::Value::Module(m), leaf),
                    Ok(crate::value::Value::Builtin(n)) if crate::builtins::is_exception_name(n)
                ),
                // Every rung only when the CORE serves the module: `glob` and
                // `time` are in this binary's MODULES and not in the core's,
                // whose walk therefore never stops on the clause.
                !CAPS.iter().any(|(c, mods, _)| !SPECTRUM[0].caps.contains(c) && mods.contains(&module)),
            ),
            None => (crate::builtins::is_exception_name(leaf), false),
        },
        None => (crate::builtins::is_exception_name(k), true),
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
/// `lypning conformance` grades every engine through. The static `glob-order`
/// shapes (a lazy `iglob` where its order shows, a listing function used as a
/// value) have NO runtime backstop — without this, `lypning-l -c` would
/// answer `for p in glob.iglob("*"): …` eagerly, at exit 0.
///
/// On lypning-l the same walk is also what tells `glob.rs` which calls'
/// order can show ([`Requirements::glob_blessed`], `glob::set_order_shown`):
/// the one runtime `glob-order`, a visible order over a directory the run has
/// changed, is decided per call from it, and a program the walk never cleared
/// keeps the fail-safe "shown".
///
/// It runs BEFORE the first statement, so the refusal is exit 90 with an empty
/// stdout and an untouched disk, and no statement of the program has run. Only
/// for a source that mentions `glob`, so
/// every other program pays one substring search: `re`'s stops are not why this
/// exists (the matcher refuses them at runtime, which is a backstop the static
/// `glob` shapes have none of), and widening the guard to catch them would put a second AST walk
/// in front of every in-process run to buy an exit code on a path the chain can
/// no longer reach. A program that mentions `glob` and stops on `re` first is
/// refused here with the `re` line, which is the same line one statement
/// earlier.
///
/// `cap-hashlib` widened the guard to `hashlib` for the same reason `glob` is
/// in it: the core routes `import hashlib` INTO `lypning-l`, which enters the
/// program as `-c` and never walks it, so a constructor or attribute only the
/// walk could refuse would land at RUNTIME — a spawn spent, and past an effect
/// the barrier cannot take back exit 1 with the output discarded (issue #51,
/// which took `os.mkdir` off that list). Asked here, it is exit 90 with an
/// untouched disk, and the router spent the refusal instead of a spawn.
///
/// **Compiled into every variant, including the ones with neither capability.**
/// A core that cannot serve `glob` refuses it anyway — but at the import, when
/// execution reaches it, where this refuses before anything runs. A program that
/// dies first was therefore ANSWERED by the core and REFUSED by its own
/// superset, which invariant 10 forbids. `spectrum_stop` is only set where no
/// rung can serve the call, so the core reports the same accurate kind, and the
/// substring guard above means a program that never mentions either name pays
/// nothing.
pub fn static_stop_check(body: &[Stmt], src: &str) -> crate::err::R<()> {
    // The fail-safe default, on EVERY run and before the guard below: a host
    // that runs many programs in one process must not inherit the previous
    // program's "not shown". Only a complete walk clears it.
    #[cfg(feature = "cap-glob")]
    crate::glob::set_order_shown(true, Vec::new());
    let mentioned = src.contains("glob") || src.contains("hashlib");
    // Behind the features, so the frozen core's guard is the bytes it was.
    #[cfg(feature = "cap-textwrap")]
    let mentioned = mentioned || src.contains("textwrap");
    // `cap-time` widens the guard to `time` on the variant that has it, for
    // the reason `hashlib` is in it.
    #[cfg(feature = "cap-time")]
    let mentioned = mentioned || src.contains("time");
    if !mentioned {
        return Ok(());
    }
    let mut req = Requirements {
        glob_wrappers: trusted_wrappers(src),
        ..Requirements::default()
    };
    walk_program(body, &mut req);
    #[cfg(feature = "cap-glob")]
    crate::glob::set_order_shown(
        req.glob_escaped(),
        req.glob_blessed.iter().map(|p| *p as usize).collect(),
    );
    match req.spectrum_stop {
        Some((k, d)) => Err(crate::err::unsupported(&k, &d)),
        None => Ok(()),
    }
}

/// The capabilities whose programs every Rust rung refused before this branch
/// served them — each went to CPython, and got CPython's `Did you mean`
/// suggestion, which no variant computes (`err::forgot_import`).
#[cfg(any(feature = "cap-itertools", feature = "cap-difflib", feature = "cap-time", feature = "cap-future"))]
const HINT_HELD_CAPS: &[&str] = &[
    "cap-ast",
    "cap-binascii",
    "cap-difflib",
    "cap-future",
    "cap-itertools",
    "cap-random",
    "cap-statistics",
    "cap-textwrap",
    "cap-time",
];

/// The capabilities the CORE lacks that this program needs, as the core's own
/// walk would find them — computed HERE, on a variant that has them, from the
/// tables every variant carries ([`SPECTRUM`], [`CAPS`], [`CAP_ATTRS`]):
/// an import of a module only a capability serves (the core blocks `module:
/// import X`), a [`CAP_ATTRS`] name (`module-attr: random.sample`), and a
/// served `__future__` head, which the parse has already removed and which the
/// core blocks as `module: from __future__ import …`. Empty means the core's
/// walk blocks on no capability, so as far as capabilities go the router picks
/// the core — and what the core answers, this variant must answer the same.
#[cfg(any(feature = "cap-itertools", feature = "cap-difflib", feature = "cap-time", feature = "cap-future"))]
fn core_lacks(req: &Requirements, future_head: bool) -> Vec<&'static str> {
    // The core's caps are empty, so a module some CAPS row lists is one the
    // core's walk blocks; a module no row lists is the core's own, or nobody's.
    let mut out: Vec<&'static str> = Vec::new();
    for m in &req.imports {
        if let Some((c, _, _)) = CAPS
            .iter()
            .find(|(c, mods, _)| !SPECTRUM[0].caps.contains(c) && mods.contains(&m.as_str()))
        {
            out.push(c);
        }
    }
    #[cfg(feature = "cap-random")]
    if req.core_attr {
        out.push("cap-random");
    }
    #[cfg(feature = "cap-binascii")]
    if req.fromhex {
        out.push("cap-binascii");
    }
    // A decorator or a keyword-only parameter: the core's PARSER refuses it,
    // before the first statement, and `cap-future` answers both kinds.
    #[cfg(feature = "cap-future")]
    let future_head = future_head || crate::parse::funcsig_used();
    if future_head {
        out.push("cap-future");
    }
    out
}

/// Does the core's walk admit this program, as far as capabilities go? See
/// [`core_lacks`]; `pub` for the test that holds the two variants' answers to
/// each other.
#[cfg(any(feature = "cap-itertools", feature = "cap-difflib", feature = "cap-time", feature = "cap-future"))]
pub fn core_admits(body: &[Stmt], src: &str) -> bool {
    core_lacks(&walk_for_hold(body, src), has_future_head(src)).is_empty()
}

#[cfg(any(feature = "cap-itertools", feature = "cap-difflib", feature = "cap-time", feature = "cap-future"))]
fn walk_for_hold(body: &[Stmt], src: &str) -> Requirements {
    let mut req = Requirements {
        glob_wrappers: trusted_wrappers(src),
        ..Requirements::default()
    };
    walk_program(body, &mut req);
    req
}

/// Did the parse remove a served `from __future__` head? It did exactly when a
/// NAME token spells `__future__` in a program that parsed: `future.rs`
/// refuses every other such name. A string or a comment is not a name.
#[cfg(any(feature = "cap-itertools", feature = "cap-difflib", feature = "cap-time", feature = "cap-future"))]
fn has_future_head(src: &str) -> bool {
    #[cfg(feature = "cap-future")]
    if src.contains("__future__") {
        return crate::lex::tokenize(src).is_ok_and(|t| future_imported(&t));
    }
    let _ = src;
    false
}

/// Is this run ARMED (`io::arm`) — may it become held, its output kept
/// reversible to the end and its uncaught `NameError`, `AttributeError` or
/// unexpected-keyword `TypeError` refused as `name-hint`
/// (`err::forgot_import`)?
///
/// Exactly when the spectrum router, evaluated in this binary over this
/// program, would NOT pick the core, because the core's static walk blocks on
/// a capability in [`HINT_HELD_CAPS`] ([`core_lacks`]). Those programs went to
/// CPython before the capability existed; a program the core routes to itself
/// is the core's answer, and this variant answers it identically — no hold, no
/// `name-hint`, no 8 MiB or `rmdir` refusal (invariant 10, pinned by
/// `tests/test_hold_monotone.py`). Nothing here reads the source as text —
/// a comment or a string that says `itertools`, a variable named `sample`,
/// arms nothing. The HOLD itself starts where the capability RUNS
/// (`io::hold`), which is where the core, running the same program, refuses:
/// an import that never runs (`if False: import time`) holds nothing, and the
/// program is answered as the core answers it.
#[cfg(any(feature = "cap-itertools", feature = "cap-difflib", feature = "cap-time", feature = "cap-future"))]
pub fn hint_held(body: &[Stmt], src: &str) -> bool {
    core_lacks(&walk_for_hold(body, src), has_future_head(src))
        .iter()
        .any(|c| HINT_HELD_CAPS.contains(c))
}

/// Before the first statement, for a run [`hint_held`] says the core routes
/// past itself: the run is ARMED (`io::arm`), and HELD where the capability
/// runs (`io::hold`) — a served `__future__` head, a decorator or a
/// keyword-only parameter at once, since the core refuses each before its
/// first statement. Until then it answers exactly as the
/// core answers (invariant 10). A routed run and a direct one are the same
/// run: nothing in the environment says which it is.
#[cfg(any(feature = "cap-itertools", feature = "cap-difflib", feature = "cap-time", feature = "cap-future"))]
pub fn arm_hold(body: &[Stmt], src: &str) {
    if hint_held(body, src) {
        crate::io::arm();
        #[cfg(feature = "cap-future")]
        let head = has_future_head(src) || crate::parse::funcsig_used();
        #[cfg(not(feature = "cap-future"))]
        let head = has_future_head(src);
        if head {
            crate::io::hold();
        }
    }
}

/// Does evaluating `module.name` mean running a capability the core lacks —
/// a [`CAP_ATTRS`] name, which the core's `get_attr` refuses? Asked where
/// the RUN evaluates the attribute (`ops.rs`, a `from … import`), never by
/// the walk, which reads `modules::get_attr` without running anything.
#[cfg(feature = "cap-random")]
pub fn core_refuses_attr(module: &str, name: &str) -> bool {
    CAP_ATTRS.iter().any(|(c, m, any, shape)| {
        *m == module && !SPECTRUM[0].caps.contains(c) && (any.contains(&name) || shape.contains(&name))
    })
}

/// Does running `module` mean running a capability the core lacks, one whose
/// programs [`HINT_HELD_CAPS`] says went to CPython before — the point at
/// which the core, running the same program, refuses (`io::hold`)?
#[cfg(any(feature = "cap-itertools", feature = "cap-difflib", feature = "cap-time", feature = "cap-future"))]
pub fn core_refuses_import(module: &str) -> bool {
    CAPS.iter().any(|(c, mods, _)| {
        HINT_HELD_CAPS.contains(c) && !SPECTRUM[0].caps.contains(c) && mods.contains(&module)
    })
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
        Some(Expr::Int(n)) if n.small() == Some(0) => true,
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
/// Bless the `random.Random` / `sys.version_info` child of `e` when `e` is one
/// of the shapes [`CAP_ATTRS`] serves it in, and put a `random.<f>(…)` call
/// spelled in a way no rung serves in the route's stop slot. `true` only for
/// `sys.version_info.major` / `.minor`, which the caller must not then treat
/// as a method name.
///
/// Every test is SYNTACTIC — a literal index, a literal slice bound, a tuple
/// LITERAL of at most two items — because the runtime half
/// (`randobj::version_info`) serves exactly what these spellings can reach and
/// refuses the rest, and a walk that admitted a computed index would admit a
/// program the runtime refuses one statement in.
fn bless_cap_shapes(e: &Expr, req: &mut Requirements) -> bool {
    let aliases = &req.aliases;
    let vi = |x: &Expr| module_attr_named(x, aliases, "sys", "version_info");
    let small = |x: &Expr, hi: i64| matches!(x, Expr::Int(i) if i.small().is_some_and(|v| (0..=hi).contains(&v)));
    let mut blessed: Option<&Expr> = None;
    let mut attr = false;
    match e {
        Expr::Attr(b, n) if matches!(n.as_ref(), "major" | "minor") && vi(b) => {
            blessed = Some(&**b);
            attr = true;
        }
        Expr::Index(b, i) if small(i, 1) && vi(b) => blessed = Some(&**b),
        Expr::Slice { base, lo: None, hi: Some(h), step: None } if small(h, 2) && vi(base) => {
            blessed = Some(&**base)
        }
        // Exactly one `version_info` operand, every other one a tuple literal
        // of at most two items, and no `in` / `is` anywhere in the chain.
        Expr::Compare { first, rest } => {
            let mut ok = true;
            for i in 0..=rest.len() {
                let x = if i == 0 { &**first } else { &rest[i - 1].1 };
                if i > 0 {
                    ok &= !matches!(rest[i - 1].0, CmpOp::In | CmpOp::NotIn | CmpOp::Is | CmpOp::IsNot);
                }
                if vi(x) {
                    ok &= blessed.is_none();
                    blessed = Some(x);
                } else {
                    ok &= matches!(x, Expr::Tuple(t) if t.len() <= 2
                        && !t.iter().any(|y| matches!(y, Expr::Starred(_))));
                }
            }
            if !ok {
                blessed = None;
            }
        }
        Expr::Call { func, args, star, kwargs, dstar, .. } => {
            let (arity, n) = match &**func {
                Expr::Attr(_, n) => match n.as_ref() {
                    "Random" | "shuffle" => (1, n),
                    "sample" => (2, n),
                    _ => return false,
                },
                _ => return false,
            };
            if !module_attr_named(func, aliases, "random", n) {
                return false;
            }
            // A literal seed this engine cannot hash the way CPython does.
            let bad_seed = n.as_ref() == "Random"
                && matches!(
                    args.first(),
                    Some(Expr::None | Expr::Float(_) | Expr::Str(_) | Expr::Bytes(_) | Expr::FString(_)
                        | Expr::Tuple(_) | Expr::List(_) | Expr::Set(_) | Expr::Dict(_))
                );
            if args.len() != arity || !star.is_empty() || !kwargs.is_empty() || !dstar.is_empty() || bad_seed {
                req.stop_route("random", "random.Random/sample/shuffle() with arguments no rung serves".into());
            } else if n.as_ref() == "Random" {
                blessed = Some(&**func);
            }
        }
        _ => {}
    }
    if let Some(p) = blessed {
        req.cap_blessed.push(p);
    }
    attr
}

/// Is `x` spelled `<module m>.n`, the module resolved as [`resolve_module`]
/// resolves it? Out of line: [`bless_cap_shapes`] asks it at every node.
#[inline(never)]
fn module_attr_named(x: &Expr, aliases: &[(String, String)], m: &str, n: &str) -> bool {
    matches!(x, Expr::Attr(b, a) if a.as_ref() == n
        && matches!(resolve_module(b, aliases), Some(crate::value::Value::Module(r)) if r == m))
}

/// A `random.Random` instance's method names — `.randint`, `.shuffle`, `.seed`
/// — admitted ONLY for a program that imports `random`, by the argument
/// [`pathlib_method`] makes. The names are [`CAP_METHODS`]'s row.
#[cfg(feature = "cap-random")]
fn random_method(req: &Requirements, n: &str) -> bool {
    req.imports.contains("random") && cap_serves("random", n)
}
#[cfg(not(feature = "cap-random"))]
fn random_method(_req: &Requirements, _n: &str) -> bool {
    false
}

/// Is `e` a bare name some `import` in the program bound — an `as` alias, or
/// the module's own name? Read in source order, like every binding here, so
/// a use above its import (in a `def`) is not one; that costs a stop, which
/// the run then raises where the attribute is evaluated.
#[cfg(any(feature = "cap-base64", feature = "cap-binascii"))]
fn names_an_import(e: &Expr, req: &Requirements) -> bool {
    let Expr::Name(n) = e else { return true };
    req.aliases.iter().any(|(a, _)| a == n.as_ref()) || req.imports.contains(n.as_ref())
}

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

/// The module `e` names, when it is one the program imported and THIS binary
/// does NOT serve — the mirror of `resolve_module`, which can only see modules
/// in `modules::MODULES`. Only a bare name (or its `import … as` alias) that the
/// program actually imported: `csv.reader` where `csv` is a local variable is
/// not a module attribute, and the walk must not say it is.
///
/// It no longer filters on [`MODULE_ATTRS`], and that is the half that matters.
/// A row there decides the ATTRIBUTE; the absence of one still decides that
/// this is an attribute AT ALL, which is what keeps `collections.Counter` and
/// `glob.escape` out of the method check — see the caller.
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
    Some(name.to_string())
}

fn walk_expr(e: &Expr, req: &mut Requirements) {
    let shaped_attr = bless_cap_shapes(e, req);
    match e {
        Expr::Name(n) => {
            #[cfg(feature = "cap-glob")]
            req.glob_values.push(n.clone());
            // The `time` module, or a served `time` function, anywhere but the
            // callee of a call (which the Call arm never walks down to): a
            // value this engine would have to print as `<module 'time'
            // (built-in)>` or `<built-in function time>`, or hand to code that
            // calls it where the walk cannot see.
            if req.time_mods.iter().any(|m| m == n.as_ref())
                || req.time_names.iter().any(|(b, _)| b == n.as_ref())
            {
                req.stop("time", format!("{n} used as a value: only a call to a served time function is served"));
                return;
            }
            // A name bound by `from glob import glob` that is NOT the callee of
            // a call: the walk skips the callee of every glob call, so
            // reaching here means the function is being passed or stored as a
            // value, where no call node says what it will list. `escape` and `has_magic` carry
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
            // `time.<n>` reached as a VALUE — the Call arm does not walk the
            // callee of a served call — or an unserved name in any position.
            // Before `b` is walked, which would refuse the module name itself.
            if time_module(b, req) {
                if TIME_SERVED.contains(&n.as_ref()) {
                    req.stop("time", format!("time.{n} used as a value: only a call is served"));
                } else {
                    req.escalate("time", n);
                    req.stop_only("module-attr", format!("time.{n}"));
                }
                return;
            }
            walk_expr(b, req);
            // `sys.version_info.major`: an attribute of a value, not a method
            // name for the union below to be pessimistic about.
            if shaped_attr {
                return;
            }
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
            // `textwrap.TextWrapper`, `textwrap.__file__`: the same stop, for
            // the same reason — the core routes on `MODULE_ATTRS`, the run
            // needs the stop.
            if textwrap_module(b, req) && !TEXTWRAP_SERVED.contains(&n.as_ref()) {
                // `escalate` keeps the core's `--plan` row the attribute
                // rather than the import; the stop is the run's.
                req.escalate("textwrap", n);
                req.stop_only("module-attr", format!("textwrap.{n}"));
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
            if let Some(crate::value::Value::Module(m)) = resolve_module(b, &req.aliases) {
                #[cfg(feature = "cap-random")]
                note_core_attr(req, m, n);
                if crate::modules::get_attr(&crate::value::Value::Module(m), n).is_err() {
                    // A blessed shape of a name THIS binary serves in that
                    // shape is not a blocker here; anywhere else it is, and
                    // when no rung serves it spelled this way, a stop.
                    let shaped = req.cap_blessed.contains(&(e as *const Expr));
                    let d = format!("{m}.{n}");
                    if no_rung_serves(m, n, shaped) {
                        req.stop_route("module-attr", d.clone());
                    }
                    if !(shaped && cap_attr(&SPECTRUM[self_index()], m, n, true)) {
                        req.block("module-attr", d);
                    }
                    // `base64.b32encode` is a `module-attr` blocker in both
                    // variants, so the ROUTE is already right — but a blocker
                    // is not a stop, and the run has to refuse before the
                    // barrier rather than when the attribute is touched.
                    //
                    // Only for a name the program IMPORTED: `resolve_module`
                    // reads any bare `binascii` as the module, and
                    // `binascii = "x"; binascii.upper()` is a string's method,
                    // which the core answers and a pre-run stop would refuse.
                    #[cfg(feature = "cap-base64")]
                    if m == "base64" && names_an_import(b, req) {
                        req.stop_base64("module-attr", format!("{m}.{n}"));
                    }
                    // `binascii.Error` / `binascii.crc32`, the same way.
                    #[cfg(feature = "cap-binascii")]
                    if m == "binascii" && names_an_import(b, req) {
                        req.stop_base64("module-attr", format!("{m}.{n}"));
                    }
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
                }
                // Served — or claimed whole, which is what `served_attr` says
                // for a module [`MODULE_ATTRS`] has no row for — and either way
                // this is a MODULE ATTRIBUTE and not a method name. Returning
                // is what says so. Falling through recorded `.b64decode()`,
                // `.Counter()` and `.escape()` as `method:` blockers, which was
                // invisible for as long as only the FIRST blocker was ever
                // read and became a wrong route the moment `method_wide_stop`
                // started reading the rest: every base64 program in the corpus
                // went to CPython, refused by the method name of the very
                // function the module was served to run.
                return;
            }
            // `bytes.fromhex` is `cap-binascii`'s, a capability the core
            // lacks: the run is armed for it as for an import (`core_lacks`).
            #[cfg(feature = "cap-binascii")]
            if n.as_ref() == "fromhex" {
                req.fromhex = true;
            }
            if !known_method(n)
                && !pathlib_method(req, n)
                && !re_method(req, n)
                && !hash_method(req, n)
                && !random_method(req, n)
            {
                // The iteration-74 defect class, and the reason `hashlib` was
                // rejected there: the walk keeps the FIRST blocker, and for a
                // program a capability admits the first blocker is the import.
                // The ROUTER only ever sees that one — so `import hashlib;
                // print(hashlib.md5(b"a").hexdigest()); print((5).bit_length())`
                // routed to lypning-l, printed the digest, and died at exit 1
                // on `.bit_length()`. An `AttributeError` is not a refusal, the
                // barrier only discards on 90, and the chain never retried it.
                //
                // [`method_wide_stop`] is what closes that hole for the ROUTER,
                // by name and in the core. This is the same hole seen from
                // inside the variant that HAS the capability: a program pinned
                // to `lypning-l`, or reached through the C ABI, never asks the
                // core anything, so it needs the walk's own
                // [`stop_only`](Requirements::stop_only) to refuse before
                // `Interp::new()` — a clean 90 the chain answers on CPython one
                // spawn later. The `--plan` row stays the one the program hit
                // first either way.
                //
                // The condition is [`admitted_by_a_capability`] and not "a
                // blocker is already recorded": in the CORE the blocker is
                // `module: import hashlib` and `.hexdigest()` is a `method`
                // block (`hash_method` is `false` there), so an unconditional
                // stop would refuse every hashlib program and the capability
                // would be dead. It is measured, not argued: over the corpus
                // loaded on 2026-09-06 the walk of the variant that HAS the
                // capabilities blocks `method` on 35 programs, 4 of them
                // admitted by a capability import, and exactly one of those
                // four is a program it answers today.
                #[cfg(feature = "cap-hashlib")]
                if admitted_by_a_capability(req) {
                    req.stop_only("method", format!(".{n}()"));
                }
                #[cfg(feature = "cap-textwrap")]
                if routed_past_by_textwrap(req) {
                    req.stop_route("method", format!(".{n}()"));
                }
                req.block_method(n);
            }
        }
        Expr::Call {
            func,
            args,
            kwargs,
            star,
            dstar,
            ..
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
            // Same reason, and the same place in the order: every refusal an
            // admitted base64 call can raise that the source spells, decided
            // before the program starts (#51).
            #[cfg(feature = "cap-base64")]
            base64_call_block(req, func, args, kwargs, star, dstar);
            textwrap_call_block(req, func, args, kwargs, star, dstar);
            // A served `time` call: every shape it can refuse is decided here,
            // and its callee is not walked, because the callee is the one
            // position a served `time` name may take.
            let time_call = match time_func(func, req) {
                Some(f) => {
                    time_call_block(req, e, f, args, kwargs, star, dstar);
                    true
                }
                None => false,
            };
            #[cfg(feature = "cap-binascii")]
            binascii_call_block(req, func, args, kwargs, star, dstar);
            // Is THIS a glob call? Its callee is not walked. `glob.glob` is
            // served in EVERY position: it is eager, and `glob.rs` lists in
            // CPython's own yield order. `iglob` is lazy in CPython — each
            // directory is read only after the loop body before it has run —
            // so it is served only where its parent blessed it, and is the
            // blocker anywhere else. `escape` and `has_magic` are string
            // algebra and never list a directory.
            let served_glob = match glob_func(func, req) {
                None => false,
                Some(f) => {
                    if f == "iglob" && !req.glob_blessed.contains(&(e as *const Expr)) {
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
            if !served_glob && !time_call {
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
            {
                req.time_nest += 1;
            }
            for c in clauses {
                walk_expr(&c.iter, req);
                walk_target(&c.target, req);
                c.ifs.iter().for_each(|i| walk_expr(i, req));
            }
            walk_expr(elt, req);
            if let Some(v) = val {
                walk_expr(v, req);
            }
            {
                req.time_nest -= 1;
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
            {
                req.time_nest += 1;
            }
            walk_expr(body, req);
            {
                req.time_nest -= 1;
            }
            req.leave_scope(saved);
        }
        // `return 0, *a` / `b = 0, *a`: a starred element of a bare tuple in a
        // VALUE position, which no variant unpacks (`eval` refuses it at
        // runtime). A starred TARGET is a `Target::Star` and never reaches here.
        Expr::Starred(_) => req.block("unpack", "* in a tuple display".to_string()),
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

#[cfg(all(test, feature = "cap-itertools", feature = "cap-random", feature = "cap-future", feature = "cap-time"))]
mod hold_tests {
    use super::*;

    fn held(src: &str) -> bool {
        hint_held(&crate::parse::parse(src).expect("parses"), src)
    }

    /// The hold is the router's verdict, never a word in the text: a program
    /// the core routes to itself is the core's, whatever it says.
    #[test]
    fn words_in_the_text_hold_nothing() {
        for src in [
            "# itertools\nfoo",
            "print('difflib')\nfoo",
            "\"\"\"uses __future__ semantics\"\"\"\nfoo",
            "print(f\"see __future__\")\nfoo",
            "import random\nsample = [1]\nprint(sample)",
            "import random\nprint('shuffle', 'Random')",
            "import sys\nversion_info = 3\nprint(version_info)",
            "# time statistics textwrap binascii\nfoo",
        ] {
            assert!(!held(src), "held a program the core routes to itself: {src:?}");
            assert!(core_admits(&crate::parse::parse(src).unwrap(), src), "{src:?}");
        }
    }

    #[test]
    fn a_capability_the_core_lacks_holds() {
        for src in [
            "import itertools\nfoo",
            "if False:\n    import time\nfoo",
            "import random\nprint(random.Random(1).randint(1, 2))",
            "import random\nl = [1]\nrandom.shuffle(l)",
            "import sys\nprint(sys.version_info[0])",
            "from sys import version_info\nprint(1)",
            "from __future__ import annotations\nfoo",
            "import statistics\nfoo",
        ] {
            assert!(held(src), "did not hold a program only a capability admits: {src:?}");
        }
        // A capability outside the held set is admitted past the core but not held.
        assert!(!held("import re\nfoo"));
        assert!(!core_admits(&crate::parse::parse("import re\nfoo").unwrap(), "import re\nfoo"));
    }

    /// No pre-run stop for what the core runs: a variable named after a
    /// capability module, or an `except m.X` clause no exception reaches. The
    /// core's walk sees no such module and the core answers; the ROUTE may
    /// still go past the rungs, but a direct run refuses only where the
    /// attribute is evaluated.
    #[test]
    fn what_the_core_runs_has_no_pre_run_stop() {
        for src in [
            "binascii = 'x'\nprint(binascii.upper())",
            "def f(binascii): return binascii.upper()\nprint(f('a'))",
            "base64 = 'x'\nprint(base64.upper())",
            "try:\n    print(1)\nexcept glob.X:\n    pass",
            "try:\n    print(1)\nexcept hashlib.X:\n    pass",
            "try:\n    print(1)\nexcept time.error:\n    pass",
            "try:\n    print(1)\nexcept textwrap.X:\n    pass",
            "try:\n    print(1)\nexcept binascii.Error:\n    pass",
            "time = 1\ntry:\n    print(1)\nexcept time.error:\n    pass",
            "def f():\n    try:\n        pass\n    except (ValueError, glob.X):\n        pass",
        ] {
            let body = crate::parse::parse(src).expect("parses");
            assert!(static_stop_check(&body, src).is_ok(), "{src:?}");
            #[cfg(feature = "cap-base64")]
            assert!(base64_static_check(&body, src).is_ok(), "{src:?}");
        }
        // An imported module's unserved attribute is still stopped before the run.
        let src = "import binascii\nprint(binascii.crc32(b'a'))";
        #[cfg(feature = "cap-base64")]
        assert!(base64_static_check(&crate::parse::parse(src).unwrap(), src).is_err());
        let _ = src;
    }
}
