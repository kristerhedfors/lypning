//! Builtin functions.
//!
//! The set is chosen from the corpus, not from the Python manual: `print`
//! appears in 93.5% of harvested one-liners, `open` in 42.7%, `len` in 10.4%.
//! Anything not here is `unsupported: builtin`, which is a routing signal, not
//! a failure — see `route.rs`.

use crate::args::Args;
use crate::err::*;
use crate::eval::{int_val, Interp};
use crate::fmt;
use crate::io as mio;
use crate::iter::{decode_utf8, Iter};
use crate::ops;
use crate::value::*;
use std::cell::RefCell;
use std::rc::Rc;

/// Every name resolvable as a builtin. Kept as one table so `route.rs` can ask
/// "would lypning know this name?" without executing anything.
///
/// **Ordered by how often the corpus names it, commonest first — not
/// alphabetically.** [`builtin`] walks this table from the front on every
/// builtin name a program reads, so the order is the expected length of that
/// walk and nothing else: no reader of this table depends on it, and every one
/// of the names is unique, so `find` returns the same entry whatever the order.
///
/// Counted by parsing the corpus and walking each AST for `Name` nodes in this
/// set, with a word scan as the fallback on the 47 programs that do not parse:
/// **3,688 loaded on 2026-09-07, 17,208 sightings.** The mean walk over that
/// distribution is **25.31 compares alphabetically and 4.64 here**, and the top
/// three names alone — `print` 6,888, `open` 2,733, `len` 1,720 — are 66% of
/// every sighting while sitting 28th, 26th and 19th in the alphabet. `input` is
/// last because the corpus never names it; the count is a property of this
/// corpus on this date, and CLAUDE.md invariant 3 applies to it like any other.
///
/// This is the third thing tried on this scan and the first that helped. The
/// other two changed its SHAPE and both lost: `binary_search` measured 2%
/// worse with disjoint bands (`docs/HILLCLIMB.md` iterations 4 and 42 — a
/// perfectly predicted walk of a cache-resident table beats five unpredictable
/// branches), and routing the two tables through a first-byte test made
/// `builtin-sum-len` 15% slower by moving inlining (iteration 68). Reordering
/// changes no branch, no code and no byte of the binary — only which entry is
/// found first — which is why it is the one that survives. If a name's
/// frequency changes enough to matter, re-count it; do not re-sort it.
pub const BUILTINS: &[&str] = &[
    "print", "open", "len", "isinstance", "repr", "sorted", "range", "str", "int", "dict", "set",
    "list", "sum", "type", "min", "chr", "enumerate", "any", "bool", "float", "zip", "round",
    "max", "divmod", "bytes", "hex", "iter", "next", "tuple", "format", "ord", "abs", "all",
    "bin", "map", "filter", "reversed", "oct", "input",
];

/// f64 -> i64 the way CPython converts, refusing where it cannot.
///
/// `as i64` SATURATES in Rust: `1e308 as i64` is i64::MAX and `f64::INFINITY as
/// i64` is i64::MAX, so `int(1e308)` answered 9223372036854775807 and
/// `round(1e100)` answered the same — both at exit 0, both wrong, and both in
/// exactly the place docs/LYPNING.md §3 promises an `unsupported: bigint` refusal.
/// The promise was kept for arithmetic (every op is checked) and quietly broken
/// on the two conversions. scripts/lypning-fuzz.mjs found it in its first 120
/// probes.
///
/// The three outcomes are CPython's, not an approximation of them: NaN is a
/// ValueError, an infinity is an OverflowError, and a finite value too large
/// for i64 is a value Python WOULD represent — so it is the bigint refusal, and
/// the dispatcher hands the program to an interpreter that has bignums.
pub fn float_to_int(f: f64, what: &str) -> R<i64> {
    if f.is_nan() {
        return Err(value_err(format!("cannot convert float NaN to integer")));
    }
    if f.is_infinite() {
        return Err(overflow_err("cannot convert float infinity to integer"));
    }
    // 2^63 exactly; f64 cannot represent i64::MAX, so comparing against the
    // power of two is the only correct bound.
    if f >= 9223372036854775808.0 || f < -9223372036854775808.0 {
        return Err(unsupported(
            "bigint",
            &format!("{what}() of a float beyond 64-bit range (Python would use a bignum)"),
        ));
    }
    Ok(f as i64)
}

/// The end of CPython's float `sum` loop, on both versions at once. `f` is the
/// naive running sum every CPython holds; `c` is the Neumaier correction that
/// 3.12+ adds — only when it is non-zero and finite, so an overflowed or `nan`
/// correction never turns an `inf` sum into `nan`, and a `-0.0` sum keeps its
/// sign. 3.11 returns `f` as it stands. Answer only when the two are the same
/// bits; otherwise the program's output depends on which CPython the caller
/// has, and that is a refusal, not a guess.
fn float_sum_agreed(f: f64, c12: f64, c14: f64) -> R<f64> {
    // Three eras, one loop: 3.11 returns `f`; 3.12 and 3.13 fold in a
    // Neumaier correction accumulated over the FLOAT items only (an int met in
    // the float loop is `f_result += (double)value`, uncorrected); 3.14 runs
    // every item, ints included, through the same `cs_add`. The engine answers
    // only where all three land on the same bits.
    let fold = |c: f64| if c != 0.0 && c.is_finite() { f + c } else { f };
    if fold(c12).to_bits() == f.to_bits() && fold(c14).to_bits() == f.to_bits() {
        Ok(f)
    } else {
        Err(unsupported(
            "float-sum",
            "sum() over floats where CPython 3.11, 3.12 and 3.14 round differently (3.12+ compensates floats, 3.14 compensates ints in the float loop too); the answers differ",
        ))
    }
}

/// Python's rule for underscores in a numeric literal: they may appear only
/// BETWEEN digits.
///
/// `1_0` is ten; `1_`, `_1`, `1__0` and `1_0_` are all ValueErrors. The parser
/// here stripped every underscore unconditionally before handing the digits to
/// Rust, so all four of those answered a number where CPython refuses — and
/// `int('1_')` answering `1` is the shape that reaches a caller as data.
///
/// Applied to the digit run only, after any sign and base prefix have been
/// taken off, because `int('0x_1f', 16)` is a ValueError too: the underscore
/// there sits between a prefix and a digit, not between two digits.
/// `after_prefix` allows ONE leading underscore, because an underscore may also
/// follow a base specifier: `int('0x_1f', 16)` is 31. It is only the digit run
/// that has the between-digits rule, and the prefix has already been taken off
/// by the time this sees the string.
fn underscores_are_between_digits(s: &str, radix: u32, after_prefix: bool) -> bool {
    let b = s.as_bytes();
    for (i, c) in b.iter().enumerate() {
        if *c != b'_' {
            continue;
        }
        let before = (i == 0 && after_prefix) || (i > 0 && (b[i - 1] as char).is_digit(radix));
        let after = i + 1 < b.len() && (b[i + 1] as char).is_digit(radix);
        if !(before && after) {
            return false;
        }
    }
    true
}

pub const EXCEPTIONS: &[&str] = &[
    "ArithmeticError",
    "AssertionError",
    "AttributeError",
    "BaseException",
    "Exception",
    "FileExistsError",
    "FileNotFoundError",
    "IndexError",
    "KeyError",
    "LookupError",
    "NameError",
    "NotImplementedError",
    "OverflowError",
    "OSError",
    "IOError",
    "PermissionError",
    "RuntimeError",
    "StopIteration",
    "SystemExit",
    "TypeError",
    "UnboundLocalError",
    "UnicodeDecodeError",
    "ValueError",
    "ZeroDivisionError",
];

pub fn is_exception_name(n: &str) -> bool {
    EXCEPTIONS.iter().any(|e| name_eq(e, n))
}

/// CPython's `tp_name` for a `Value::Builtin` that is a CLASS, or `None` for a
/// builtin FUNCTION.
///
/// `Value::Builtin` is one variant over two CPython types: `str`, `int` and
/// `ValueError` are `type` objects and `len`, `print` and `open` are
/// `builtin_function_or_method`, and answering the second name for the first
/// was wrong in every message that names a type — `len(str)` prints `object of
/// type 'type' has no len()` in CPython and printed
/// `builtin_function_or_method` here, at exit 0. `value::Callable` is the
/// reader; `value::attr_error` needs the NAME as well as the fact, because a
/// class is the one callable whose `AttributeError` names the object rather
/// than its type.
///
/// This is a closed set and therefore provable rather than open-ended: the two
/// tables above are the whole builtin namespace this engine has, and the three
/// classes below are every class a served module exports. The names were read
/// off CPython 3.14.5 on 2026-09-07, `tp_name` included — which is DOTTED for
/// `collections.defaultdict` and bare for everything else, and `IOError` is
/// `OSError` rather than a class of its own.
pub fn class_name(n: &str) -> Option<&'static str> {
    if let Some(t) = TYPE_OBJECTS.iter().find(|t| **t == n) {
        return Some(t);
    }
    if n == "IOError" {
        // `IOError is OSError` — one class under two names, and the message
        // CPython prints is the one class's.
        return Some("OSError");
    }
    if let Some(e) = EXCEPTIONS.iter().find(|e| **e == n) {
        return Some(e);
    }
    #[cfg(feature = "cap-pathlib")]
    if n == "Path" {
        return Some("Path");
    }
    #[cfg(feature = "cap-collections")]
    if n == "Counter" {
        return Some("Counter");
    }
    #[cfg(feature = "cap-collections")]
    if n == "defaultdict" {
        // The one dotted `tp_name` in the set: it is `_collections.defaultdict`
        // in C and CPython prints `collections.defaultdict`.
        return Some("collections.defaultdict");
    }
    None
}

/// How CPython SPELLS the class `n` names inside `repr`, or `None` when this
/// crate cannot prove which class it is holding.
///
/// Not [`class_name`], and the gap between the two is the whole of this
/// function. `class_name` answers `tp_name`, which is what an `AttributeError`
/// prints. `type.__repr__` prints `<class '{__module__}.{__qualname__}'>` and
/// elides the module only for `builtins` — so `Counter`, whose `tp_name` is
/// bare, reprs as `collections.Counter`. Read off CPython 3.14.5 on 2026-09-07
/// by running it, not by recalling it, because the two spellings differ for
/// exactly the entries a reimplementation would assume they agree on.
///
/// **Two names are deliberately absent, and both are ALIASES this crate
/// collapses onto one value.** `modules.rs` answers `Value::Builtin("Path")`
/// for `pathlib.Path` AND `pathlib.PosixPath`, and `Value::Builtin("ValueError")`
/// for `ValueError` AND `json.JSONDecodeError`. The collapse is right where it
/// was made — `isinstance` and `except` cannot tell the members of either pair
/// apart — and wrong here, because `repr` can: CPython says `pathlib.PosixPath`
/// and `json.decoder.JSONDecodeError` for the second of each pair. One value,
/// two spellings, and nothing in the value says which, so both refuse
/// (invariant 1). `IOError` is NOT such a pair: `IOError is OSError` is one
/// class under two names, and `repr(IOError)` really is `<class 'OSError'>`.
pub fn class_repr_name(n: &str) -> Option<&'static str> {
    match class_name(n)? {
        "Path" | "ValueError" => None,
        "Counter" => Some("collections.Counter"),
        other => Some(other),
    }
}

/// The names in [`BUILTINS`] that are TYPE OBJECTS rather than functions.
///
/// Sixteen of the thirty-nine, read off CPython 3.14.5 by asking
/// `type(getattr(builtins, n)).__name__` rather than by reading the manual. The
/// other twenty-three — `abs`, `len`, `open`, `print`, `sorted` and their
/// neighbours — really are `builtin_function_or_method`.
const TYPE_OBJECTS: &[&str] = &[
    "bool", "bytes", "dict", "enumerate", "filter", "float", "int", "list", "map", "range",
    "reversed", "set", "str", "tuple", "type", "zip",
];

/// Is `t` a dict SUBCLASS that `isinstance(x, dict)` must answer True for?
///
/// `Counter` and `defaultdict` are, and `isinstance(c, dict)` answering False
/// would be a wrong answer at exit 0 on the commonest guard an agent writes
/// around a mapping. Spelled twice, and not as a `cfg!` inside the comparison,
/// so the variant without the capability compiles the same bytes it always did.
#[cfg(feature = "cap-collections")]
fn dict_subclass(want: &str, have: &str) -> bool {
    want == "dict" && matches!(have, "Counter" | "defaultdict")
}
#[cfg(not(feature = "cap-collections"))]
fn dict_subclass(_want: &str, _have: &str) -> bool {
    false
}

pub fn exception_static(n: &str) -> &'static str {
    EXCEPTIONS.iter().find(|e| name_eq(e, n)).copied().unwrap_or("Exception")
}

pub fn builtin(name: &str) -> Option<Value> {
    // Reached on EVERY read of a builtin name — `len`, `print`, `str` — because
    // a builtin is not in any scope, so `Interp::lookup` misses every map first
    // and arrives here. The scan stays a scan (`docs/HILLCLIMB.md` iteration 4
    // measured binary search over this table and it bought no wall clock); what
    // changes is that a comparison is now bytes inline instead of a call out to
    // `memcmp`, which was 5.2% of self time on its own. See `value::name_eq`.
    //
    // The scan's LENGTH is the table's order, and `BUILTINS` is sorted by
    // corpus frequency for that reason — see its doc comment, and do not
    // re-alphabetise it. What the order is worth was measured as an A/B of the
    // whole engine (`docs/HILLCLIMB.md`, and the commit that reordered it): the
    // interpreter arm of `perf`'s `str-of-scalar` fell 16.3% and
    // `file-write-read` 3.7%, worst build against best build, while rows whose
    // hot loop names no builtin did not move. An earlier ablation here — timing
    // `abs` at index 0 against `len` at index 18 in the same loop — is NOT that
    // number and is left out of the estimate: the two calls differ in their
    // argument type as well as their position, so it prices `length()` on a
    // `str` along with the walk.
    //
    // `EXCEPTIONS` below is deliberately NOT reordered: a name that reaches it
    // has already walked all of `BUILTINS`, so its own order buys nothing on
    // this path, and `call_builtin`'s `is_exception_name` scans it in full for
    // every builtin that is not an exception whatever order it is in.
    if let Some(b) = BUILTINS.iter().find(|b| name_eq(b, name)) {
        return Some(Value::Builtin(b));
    }
    if let Some(e) = EXCEPTIONS.iter().find(|b| name_eq(b, name)) {
        return Some(Value::Builtin(e));
    }
    match name {
        "True" => Some(Value::Bool(true)),
        "False" => Some(Value::Bool(false)),
        "None" => Some(Value::None),
        "__name__" => Some(Value::Str("__main__".into())),
        _ => None,
    }
}

fn kwget(kw: &[(Rc<str>, Value)], name: &str) -> Option<Value> {
    kw.iter().find(|(k, _)| k.as_ref() == name).map(|(_, v)| v.clone())
}

/// The `key=` argument of `sorted`, `list.sort`, `min` and `max`.
///
/// `None` is the DEFAULT, not a callable, and passing it explicitly means "no
/// key" — which matters because `key=None` is how an optional key is spelled:
/// `sorted(xs, key=chooser)` where `chooser` may be `None`. Reading it as a
/// value to call raised `TypeError: 'NoneType' object is not callable` at
/// exit 1, and an ordinary non-zero exit is the program's own, so the
/// dispatcher does not fall through — the caller got that error for valid
/// Python instead of the answer.
/// CPython's exact complaint for a keyword a function does not take. `sorted`
/// says `sort()` in its message, which is why the reported name is a parameter.
/// `sorted([3, 1], strict_mode=True)` answered `[1, 3]` — the caller asked for
/// a stricter mode and silently got the default one.
fn reject_unknown_kw(func: &str, kw: &[(Rc<str>, Value)], allowed: &[&str]) -> R<()> {
    for (k, _) in kw {
        if !allowed.contains(&k.as_ref()) {
            return Err(type_err(format!(
                "'{k}' is an invalid keyword argument for {func}()"
            )));
        }
    }
    Ok(())
}

pub fn key_arg(kw: &[(Rc<str>, Value)], name: &str) -> Option<Value> {
    match kwget(kw, name) {
        Some(Value::None) | None => None,
        Some(v) => Some(v),
    }
}

/// The `reverse=` argument, which CPython reads through `__index__` rather than
/// for truthiness. `sorted(xs, reverse=None)` is a TypeError there and was an
/// ascending sort here — a wrong answer at exit 0, which is worse than the
/// error it should have been. Only `bool` and `int` have `__index__` in this
/// subset, so anything else is refused with CPython's own wording.
pub fn reverse_arg(kw: &[(Rc<str>, Value)]) -> R<bool> {
    match kwget(kw, "reverse") {
        None => Ok(false),
        Some(Value::Bool(b)) => Ok(b),
        Some(Value::Int(i)) => Ok(!i.is_zero()),
        Some(other) => Err(type_err(format!(
            "'{}' object cannot be interpreted as an integer",
            type_name(&other)
        ))),
    }
}

/// Builtins whose parameters are positional-only, so naming one is a TypeError.
///
/// Almost all of them, and lypning **silently ignored** the keyword instead:
/// `bool(x=1)` answered `False` — the no-argument result — at exit 0. Naming an
/// argument is an ordinary way to be wrong, and CPython says so; answering the
/// wrong thing without a word is the one outcome this project treats as always
/// a bug.
///
/// Enumerated by asking CPython 3.11, not by reading the manual. The builtins
/// deliberately absent from this list are the ones that really do take
/// keywords: `print`, `int`, `round`, `sorted`, `min`, `max`, `sum`, `open`,
/// `str`, `bytes`, `enumerate`, `zip` and `dict`.
const NO_KEYWORDS: &[&str] = &[
    "abs", "all", "any", "bin", "bool", "chr", "divmod", "filter", "float", "format", "hex",
    "isinstance", "iter", "len", "list", "map", "next", "oct", "ord", "range", "repr", "set",
    "tuple",
];

fn no_kw(name: &str, kw: &[(Rc<str>, Value)]) -> R<()> {
    match kw.first() {
        None => Ok(()),
        // CPython's exact wording for a C builtin: no "(got 'x')" tail, which
        // is the shape used for Python-level functions and not for these.
        Some(_) => Err(type_err(format!("{name}() takes no keyword arguments"))),
    }
}

/// Which of CPython's three arity WORDINGS a builtin uses. The counts alone
/// were not enough: the same `(1, 1)` is spelled three different ways depending
/// on how the function is defined in C, and a table that got the count right
/// and the text wrong still disagrees with the reference on stderr.
///
/// Read off CPython 3.14.5 on 2026-09-07 by calling each name with 0..5
/// type-correct arguments and keeping the message verbatim.
#[derive(Clone, Copy)]
enum Say {
    /// `METH_O`, which spells its count as a WORD and never varies:
    /// `len() takes exactly one argument (2 given)`, at zero arguments too.
    One,
    /// Argument Clinic's vectorcall wording, with **no parentheses** after the
    /// name: `sorted expected 1 argument, got 2`,
    /// `int expected at most 2 arguments, got 3`,
    /// `range expected at least 1 argument, got 0`.
    Bare,
    /// Argument Clinic's *parenthesised* wording, used by the names whose
    /// signature has a keyword-capable parameter:
    /// `enumerate() takes at most 2 arguments (3 given)`.
    Paren,
}

/// How many POSITIONAL arguments each builtin takes, as `(min, max, wording)`.
///
/// The same defect as the method table in `methods.rs`: extras were dropped in
/// silence, so `abs(1, 2)` answered `1`, `chr(65, 66)` answered `'A'`,
/// `len([1], [2])` answered `1`, `repr(1, 2)` answered `'1'` and
/// `divmod(1, 2, 3)` answered `(0, 1)` — every one at exit 0 where CPython
/// raises.
///
/// **The rows added on 2026-09-07 are the names that were still doing it.**
/// Seven were absent and dropped their extras in the same silence:
/// `dict({'a': 1}, 0)` answered `{'a': 1}`, `enumerate([1], 0, 0)` enumerated,
/// `filter(bool, [1], 0)` filtered, `reversed([1], 0)` reversed,
/// `input('a', 'b')` prompted, and `str` and `bytes` ignored a fourth argument
/// — every one at exit 0 where CPython raises TypeError. They belong here
/// rather than in their arms because an arm that has already begun is an arm
/// that can have written to a file first.
///
/// `print` and `zip` are variadic and absent. `min`, `max` and `map` have a
/// floor and no ceiling, and each already raises its own floor message —
/// `min expected at least 1 argument, got 0` and
/// `map() must have at least two arguments.` — so the table stays out of their
/// way. `type` is absent because neither a floor nor a ceiling can say
/// "1 or 3", and `open` because its floor message names a parameter.
fn arity(name: &str) -> Option<(usize, usize, Say)> {
    Some(match name {
        "len" | "abs" | "chr" | "ord" | "hex" | "oct" | "bin" | "repr" | "all" | "any" => {
            (1, 1, Say::One)
        }
        // `format(value, format_spec)` takes two, and the first derivation of
        // this table said one. The probe had called `format(1, 1)`, which fails
        // with "argument 2 must be str, not int" — a TYPE error whose text
        // contains the word "argument", so it was scored as an arity limit.
        // Deriving a table by asking the oracle only works if the question is
        // asked with type-correct values.
        "format" => (1, 2, Say::Bare),
        "sorted" | "reversed" => (1, 1, Say::Bare),
        "bool" | "float" | "list" | "tuple" | "set" | "dict" => (0, 1, Say::Bare),
        "divmod" | "isinstance" | "filter" => (2, 2, Say::Bare),
        "int" => (0, 2, Say::Bare),
        "next" | "iter" => (1, 2, Say::Bare),
        "input" => (0, 1, Say::Bare),
        "str" => (0, 3, Say::Bare),
        // `round` and `sum` have a CEILING and no floor HERE, because their
        // zero-argument messages are neither of the two forms below —
        // `round() missing required argument 'number' (pos 1)` and
        // `sum() takes at least 1 positional argument (0 given)`. The arms raise
        // those; the table only has to stay out of the way. Found by
        // py-7c8697bf4fa2, which greps CPython's own arity messages with a
        // regex: until `re` had a matcher the program never ran here, and the
        // divergence had nothing to expose it. `enumerate` and `bytes` are the
        // same shape.
        "round" | "sum" | "enumerate" => (0, 2, Say::Paren),
        "bytes" => (0, 3, Say::Paren),
        "range" => (1, 3, Say::Bare),
        _ => return None,
    })
}

fn plural(n: usize) -> &'static str {
    if n == 1 {
        "argument"
    } else {
        "arguments"
    }
}

fn check_arity(name: &str, args: &Args, kw: &[(Rc<str>, Value)]) -> R<()> {
    let Some((lo, hi, say)) = arity(name) else { return Ok(()) };
    let n = args.len();
    // The floor counts POSITIONAL arguments, so it only applies when nothing was
    // passed by name: `round(number=2.5)` has none and is still a complete call.
    // The ceiling always applies — a keyword never makes an extra positional
    // legal.
    if n <= hi && (n >= lo || !kw.is_empty()) {
        return Ok(());
    }
    Err(type_err(match say {
        // One text for both directions: `METH_O` counts what it got and says
        // nothing about the bound.
        Say::One => format!("{name}() takes exactly one argument ({n} given)"),
        Say::Bare if lo == hi => {
            format!("{name} expected {lo} {}, got {n}", plural(lo))
        }
        Say::Bare if n > hi => {
            format!("{name} expected at most {hi} {}, got {n}", plural(hi))
        }
        Say::Bare => format!("{name} expected at least {lo} {}, got {n}", plural(lo)),
        Say::Paren => format!("{name}() takes at most {hi} arguments ({n} given)"),
    }))
}

/// The message a `SystemExit` carries, chosen so that [`system_exit_code`] can
/// read the code back out of it EXACTLY. `SystemExit(arg)` and `sys.exit(arg)`
/// both come through here, so they raise one and the same exception and a
/// handler that catches one catches the other.
///
/// `Value::Exc` is a flat `(kind, message)` pair, and for every other
/// exception that is enough: the message is what gets printed. `SystemExit`
/// is different — its argument is the process's exit status, and what it
/// MEANS depends on its type. `SystemExit(4)` exits 4 in silence;
/// `SystemExit("4")` prints `4` and exits 1. Both would store the message
/// `"4"`, so the runtime refuses the second: any str whose text the decoder
/// would read as a different code (an integer, `None`, `True`, `False`, or
/// the empty string that means "no argument"), and any argument that is not
/// an int, a bool, `None` or a str at all, since `e.code` must then be a
/// value this pair has nowhere to keep. Two or more arguments make `.code`
/// the tuple, which is the same problem.
pub fn system_exit_msg(args: &Args) -> R<String> {
    if args.len() > 1 {
        return Err(unsupported("exception", "SystemExit with more than one argument"));
    }
    match args.first() {
        None => Ok(String::new()),
        // A status past the machine word is not an exit CODE: CPython cannot
        // put one in the status word either and exits 255 with nothing printed,
        // which is a shape this engine has no way to produce. Refused, before
        // the arm below turns it into a message and an exit 1.
        Some(Value::Int(i)) if i.small().is_none() => Err(unsupported(
            "bigint",
            "sys.exit() of an integer past 64 bits, which CPython cannot put in a status word either",
        )),
        Some(v @ (Value::None | Value::Int(_) | Value::Bool(_))) => fmt::to_str(v),
        Some(Value::Str(s))
            if !s.is_empty()
                && !matches!(&**s, "None" | "True" | "False")
                && s.parse::<i64>().is_err() =>
        {
            Ok(s.to_string())
        }
        Some(other) => Err(unsupported(
            "exception",
            &format!(
                "SystemExit({}), whose code cannot be carried exactly",
                fmt::repr(other)?
            ),
        )),
    }
}

/// `SystemExit(...).code`, read back from the message [`system_exit_msg`]
/// stored. The constructor's refusals are what make every arm here exact.
/// `abs()` where the magnitude does not fit a machine word — `abs(-2**63)`, and
/// every wide value. The bignum on the variant that has one, a refusal on the
/// core, which is what this arm did before `cap-bigint`.
#[allow(unused_variables)]
fn wide_abs(i: &Int) -> R<Value> {
    #[cfg(feature = "cap-bigint")]
    return Ok(crate::bigint::abs(i));
    #[cfg(not(feature = "cap-bigint"))]
    Err(unsupported("bigint", "abs() result beyond 64-bit range"))
}

pub fn system_exit_code(msg: &str) -> Value {
    match msg {
        "" | "None" => Value::None,
        "True" => Value::Bool(true),
        "False" => Value::Bool(false),
        _ => match msg.parse::<i64>() {
            Ok(i) => ival(i),
            Err(_) => Value::Str(msg.into()),
        },
    }
}

pub fn call_builtin(
    it: &mut Interp,
    name: &str,
    args: &mut Args,
    kw: Vec<(Rc<str>, Value)>,
) -> R<Value> {
    if !kw.is_empty() && NO_KEYWORDS.contains(&name) {
        return Err(type_err(format!("{name}() takes no keyword arguments")));
    }
    check_arity(name, args, &kw)?;
    // `raise ValueError("x")` / `except E as e` construct exception instances.
    if is_exception_name(name) {
        // `BaseException` takes no keywords, so `SystemExit(code=4)` is a
        // TypeError in CPython — and was `SystemExit()` here, exit 0.
        no_kw(name, &kw)?;
        if name == "SystemExit" {
            return Ok(Value::Exc("SystemExit", system_exit_msg(args)?.into()));
        }
        let msg = match args.first() {
            // `str(KeyError('f'))` is `"'f'"`, not `"f"`: KeyError shows the
            // REPR of its key, so that a missing `''` is distinguishable from a
            // missing `' '`. Every site that raises one from a real lookup
            // already stored `repr(key)`; only the constructor stored the plain
            // string, so the two disagreed and `repr()` then quoted the lookup
            // form a second time (`KeyError("'k'")`).
            Some(v) if name == "KeyError" => fmt::repr(v)?,
            Some(v) => fmt::to_str(v)?,
            None => String::new(),
        };
        return Ok(Value::Exc(exception_static(name), msg.into()));
    }
    // `Counter` and `defaultdict` are not builtin NAMES — they resolve only
    // through `collections`, and `builtin()` still says no to them — but they
    // arrive here as `Value::Builtin`, the shape every type object has.
    #[cfg(feature = "cap-collections")]
    if matches!(name, "Counter" | "defaultdict") {
        return crate::collections::construct(it, name, args, kw);
    }
    // `Path(...)` arrives the same way and for the same reason.
    #[cfg(feature = "cap-pathlib")]
    if name == "Path" {
        return crate::pathlib::construct(args, &kw);
    }
    Ok(match name {
        // `print(` is in nearly every corpus program, and this arm allocated
        // four times to write one line: a `String` for a separator it never
        // uses when there is one argument, a `String` for a newline, a
        // `String` to accumulate into, and one more from `to_str`. The
        // defaults are `&'static str` now and the single-argument case reuses
        // the string `to_str` already built.
        //
        // `print-lines` was 0.27x CPython before this, so the row was never the
        // reason — the reason is that `corpus-time` is spawn-bound and cannot
        // see per-call allocations at all (skill §3), while `print` is the one
        // construct nearly every program in the corpus executes.
        "print" => {
            // `sep` and `end` must be str or None, and CPython checks the TYPE
            // rather than converting. `fmt::to_str` converted: `print('a',
            // end=2)` printed `a2` and `print('a', sep=1)` printed `a` at exit
            // 0, both where CPython raises. The first is a wrong answer on
            // stdout; the second is worse in a way that is easy to miss — with
            // one argument the separator is never used, so the bad keyword was
            // accepted silently and only showed up if a second argument ever
            // appeared.
            let sep_owned;
            let sep: &str = match kwget(&kw, "sep") {
                Some(Value::None) | None => " ",
                Some(Value::Str(v)) => {
                    sep_owned = v;
                    sep_owned.as_ref()
                }
                Some(other) => {
                    return Err(type_err(format!(
                        "sep must be None or a string, not {}",
                        type_name(&other)
                    )))
                }
            };
            let end_owned;
            let end: &str = match kwget(&kw, "end") {
                Some(Value::None) | None => "\n",
                Some(Value::Str(v)) => {
                    end_owned = v;
                    end_owned.as_ref()
                }
                Some(other) => {
                    return Err(type_err(format!(
                        "end must be None or a string, not {}",
                        type_name(&other)
                    )))
                }
            };
            let to_err = match kwget(&kw, "file") {
                Some(Value::Module("sys.stderr")) => true,
                Some(Value::Module("sys.stdout")) | None => false,
                Some(other) => {
                    return Err(unsupported(
                        "print-file",
                        &format!("print(file=…) to a {}", type_name(&other)),
                    ))
                }
            };
            for (k, _) in &kw {
                if !matches!(k.as_ref(), "sep" | "end" | "file" | "flush") {
                    return Err(type_err(format!(
                        "'{k}' is an invalid keyword argument for print()"
                    )));
                }
            }
            // One argument is the shape almost every `print` has: take the
            // string `to_str` built rather than copying it into a second one
            // and dropping the first.
            let mut out = match args.first() {
                Some(a) if args.len() == 1 => fmt::to_str(a)?,
                _ => {
                    let mut acc = String::new();
                    for (i, a) in args.iter().enumerate() {
                        if i > 0 {
                            acc.push_str(sep);
                        }
                        acc.push_str(&fmt::to_str(a)?);
                    }
                    acc
                }
            };
            out.push_str(end);
            if to_err {
                mio::write_err(out.as_bytes())?;
            } else {
                mio::write_out(out.as_bytes())?;
            }
            Value::None
        }
        "len" => {
            no_kw("len", &kw)?;
            let v = arg1(name, &args)?;
            ival(length(&v)? as i64)
        }
        "repr" => Value::Str(fmt::repr_rc(&arg1(name, &args)?)?),
        "str" => match args.first() {
            None => Value::Str("".into()),
            Some(v) => {
                let (enc, errs) = text_codec_args("str", &args, &kw)?;
                match v {
                    // `str(b, encoding)` IS `b.decode(encoding)`, and it went
                    // straight to `decode_utf8` whatever the encoding said —
                    // so `str(b'h\xe9', 'latin-1')` raised UnicodeDecodeError at
                    // exit 1 for a program CPython answers `'hé'`, while
                    // `b'h\xe9'.decode('latin-1')`, the same operation spelled
                    // the other way, correctly refused. One reader for both
                    // spellings now, so the two cannot disagree again.
                    Value::Bytes(b) if enc.is_some() => {
                        check_decode_errors(errs.as_ref())?;
                        let e = fmt::to_str(enc.as_ref().unwrap())?;
                        Value::Str(crate::iter::decode_named(b, &e)?)
                    }
                    // An encoding given for something that is not bytes at all.
                    // This fell through to `to_rc` and STRINGIFIED the object:
                    // `str(1, 'utf-8')` answered `'1'` at exit 0.
                    _ => match codec_without_subject("str", v, enc.as_ref(), errs.is_some(), true) {
                        Some(e) => return Err(e),
                        None => Value::Str(fmt::to_rc(v)?),
                    },
                }
            }
        },
        "int" => {
            // `x` is positional-only in CPython, so naming it is a TypeError —
            // and this arm used to IGNORE the keyword and fall through to the
            // no-argument case, so `int(x='5')` answered **0** at exit 0.
            if let Some((k, _)) = kw.iter().find(|(k, _)| k.as_ref() != "base") {
                return Err(type_err(format!(
                    "'{k}' is an invalid keyword argument for int()"
                )));
            }
            let base_arg = crate::args::bind(&args, &kw, 1, "base", "int")?;
            let explicit_base = base_arg.is_some();
            let base = match &base_arg {
                Some(v) => int_val(v)?,
                None => 10,
            };
            // `i64::from_str_radix` PANICS outside 2..=36, and a panic is exit
            // 134 — not 0, not 90, so the dispatcher hands it straight back and
            // the caller reads a Rust abort. `int(s, 0)` is ordinary Python
            // (detect the base from the prefix) and aborted the interpreter.
            if base != 0 && !(2..=36).contains(&base) {
                return Err(value_err("int() base must be >= 2 and <= 36, or 0"));
            }
            if explicit_base && !matches!(args.first(), Some(Value::Str(_)) | Some(Value::Bytes(_)))
            {
                return Err(type_err("int() can't convert non-string with explicit base"));
            }
            match args.first() {
                None => ival(0),
                Some(Value::Str(s)) => {
                    let t = s.trim();
                    let (t, neg) = match t.strip_prefix('-') {
                        Some(r) => (r, true),
                        None => (t.strip_prefix('+').unwrap_or(t), false),
                    };
                    // Base 0 reads the prefix and then holds the literal to
                    // Python's *source* rules, where a leading zero on a decimal
                    // is not allowed: `int('010', 0)` is a ValueError while
                    // `int('00', 0)` is 0. That asymmetry only exists for base 0.
                    //
                    // `reported` stays at what the CALLER passed: CPython says
                    // "with base 0" even after it has resolved the prefix to 16,
                    // and a message naming the detected base would send a reader
                    // looking for an argument nobody wrote.
                    let reported = base;
                    let mut base = base;
                    let t2 = if base == 0 {
                        let (rest, detected) = match t.get(..2) {
                            Some("0x") | Some("0X") => (&t[2..], 16),
                            Some("0o") | Some("0O") => (&t[2..], 8),
                            Some("0b") | Some("0B") => (&t[2..], 2),
                            _ => (t, 10),
                        };
                        base = detected;
                        if detected == 10 {
                            let digits: String = rest.chars().filter(|c| *c != '_').collect();
                            if digits.starts_with('0') && digits.chars().any(|c| c != '0') {
                                return Err(value_err(format!(
                                    "invalid literal for int() with base 0: {}",
                                    fmt::str_repr(s)?
                                )));
                            }
                        }
                        rest
                    } else if base == 16 {
                        t.strip_prefix("0x").or_else(|| t.strip_prefix("0X")).unwrap_or(t)
                    } else if base == 8 {
                        t.strip_prefix("0o").or_else(|| t.strip_prefix("0O")).unwrap_or(t)
                    } else if base == 2 {
                        t.strip_prefix("0b").or_else(|| t.strip_prefix("0B")).unwrap_or(t)
                    } else {
                        t
                    };
                    if !underscores_are_between_digits(t2, base as u32, t2.len() < t.len()) {
                        return Err(value_err(format!(
                            "invalid literal for int() with base {reported}: {}",
                            fmt::str_repr(s)?
                        )));
                    }
                    let cleaned: String = t2.chars().filter(|c| *c != '_').collect();
                    // CPython'S DIGIT LIMIT IS A SCREEN ON THIS STRING, NOT ON
                    // THE RESULT. It was applied only inside `bigint::parse`,
                    // which is reached from the overflow arm below — so a
                    // literal that FITS an `i64` never met it however long it
                    // was, and `int('0' * 5000 + '1')` printed `1` at exit 0 on
                    // both variants where CPython raises `ValueError` for 5001
                    // digits. `cleaned` is exactly what CPython counts: the
                    // digit run, sign and whitespace and base prefix already
                    // off it and underscores already out of it, leading zeros
                    // kept. `base` has been resolved from a `0` argument, so
                    // the power-of-two exemption inside the rule sees the base
                    // that will actually be converted.
                    //
                    // Placed AFTER the well-formedness test, and that ordering
                    // is CPython's: `int('x' * 5000)` is "invalid literal", not
                    // "Exceeds the limit", so a screen in front of it would
                    // refuse a program CPython answers with an ordinary
                    // `ValueError`. The scan costs nothing on the hot path —
                    // the rule's own length compare is false for every literal
                    // under 641 digits and `&&` stops there.
                    if crate::host::over_str_digit_limit(cleaned.len(), base as u32)
                        && !cleaned.is_empty()
                        && cleaned.chars().all(|c| c.is_digit(base as u32))
                    {
                        return Err(unsupported(
                            "bigint",
                            "int() of a string past sys.get_int_max_str_digits(), \
                             where CPython raises ValueError",
                        ));
                    }
                    match i64::from_str_radix(&cleaned, base as u32) {
                        Ok(v) => ival(if neg { -v } else { v }),
                        Err(e) if !cleaned.is_empty()
                            && cleaned.chars().all(|c| c.is_digit(base as u32)) =>
                        {
                            let _ = e;
                            // A well-formed literal too wide for an i64 IS an
                            // integer; `cap-bigint` builds it, and the core
                            // still refuses. The digit limit has already been
                            // applied above, on the string, which is where
                            // CPython applies it.
                            //
                            // THE GUARD IS THE TWO CONDITIONS ABOVE AND NOTHING
                            // ELSE. It carried a third — `cleaned.len() > 18`,
                            // a decimal digit count — which is a cheap screen
                            // for base 10 and a WRONG one for every other base:
                            // `int('ffffffffffffffff', 16)` is 16 characters
                            // and `int('z' * 13, 36)` is 13, both past an i64,
                            // and both fell through to the ValueError arm
                            // below. That is exit 1 — the program's own, which
                            // the chain never retries — for a literal CPython
                            // converts. The screen was never needed: `cleaned`
                            // has had its sign stripped, so once it is
                            // non-empty and every character is a digit of the
                            // base, overflow is the ONLY error
                            // `from_str_radix` can have returned.
                            #[cfg(feature = "cap-bigint")]
                            match crate::bigint::parse(&cleaned, base as u32) {
                                Some(v) => {
                                    return Ok(if neg {
                                        crate::bigint::neg(&v)
                                    } else {
                                        Value::Int(v)
                                    })
                                }
                                None => {
                                    return Err(crate::bigint::refuse(
                                        "int() of a string past sys.get_int_max_str_digits(), where CPython raises ValueError",
                                    ))
                                }
                            }
                            #[cfg(not(feature = "cap-bigint"))]
                            return Err(unsupported("bigint", "int() result beyond 64-bit range"));
                        }
                        Err(_) => {
                            return Err(value_err(format!(
                                "invalid literal for int() with base {reported}: {}",
                                fmt::str_repr(s)?
                            )))
                        }
                    }
                }
                Some(Value::Float(f)) => ival(float_to_int(*f, "int")?),
                Some(Value::Int(i)) => Value::Int(i.clone()),
                Some(Value::Bool(b)) => ival(*b as i64),
                #[cfg(feature = "cap-re")]
                Some(Value::ReFlag(b)) => ival(*b as i64),
                #[cfg(feature = "cap-re")]
                Some(v @ (Value::Pattern(_) | Value::Match(_))) => {
                    return Err(crate::re::guard_one(v, "int() of").unwrap_err())
                }
                Some(Value::Bytes(b)) => {
                    let s = decode_utf8(b)?;
                    return call_builtin(it, "int", &mut Args::one(Value::Str(s.into())), kw);
                }
                Some(other) => {
                    return Err(type_err(format!(
                        "int() argument must be a string or a number, not '{}'",
                        type_name(other)
                    )))
                }
            }
        }
        "float" => match args.first() {
            None => Value::Float(0.0),
            #[cfg(feature = "cap-re")]
            Some(Value::ReFlag(b)) => Value::Float(*b as f64),
            #[cfg(feature = "cap-re")]
            Some(v @ (Value::Pattern(_) | Value::Match(_))) => {
                return Err(crate::re::guard_one(v, "float() of").unwrap_err())
            }
            Some(Value::Str(s)) => {
                let t = s.trim();
                let lower = t.to_ascii_lowercase();
                match lower.as_str() {
                    "inf" | "+inf" | "infinity" | "+infinity" => Value::Float(f64::INFINITY),
                    "-inf" | "-infinity" => Value::Float(f64::NEG_INFINITY),
                    "nan" | "+nan" | "-nan" => Value::Float(f64::NAN),
                    // Same underscore rule as `int()`: between digits only, so
                    // `float('1_')` is a ValueError and not 1.0. Checked on the
                    // sign-stripped body, since `float('-1_0')` is fine.
                    _ if !underscores_are_between_digits(
                        t.strip_prefix(['-', '+']).unwrap_or(t),
                        10,
                        false,
                    ) =>
                    {
                        return Err(value_err(format!(
                            "could not convert string to float: {}",
                            fmt::str_repr(s)?
                        )))
                    }
                    _ => match t.replace('_', "").parse::<f64>() {
                        Ok(v) => Value::Float(v),
                        Err(_) => {
                            return Err(value_err(format!(
                                "could not convert string to float: {}",
                                fmt::str_repr(s)?
                            )))
                        }
                    },
                }
            }
            // `float(2**100)` needs the round-to-nearest a wide integer does
            // not carry here; `get()` refuses rather than round through an
            // intermediate this engine cannot make exact.
            Some(Value::Int(i)) => Value::Float(i.get()? as f64),
            Some(Value::Bool(b)) => Value::Float(*b as i64 as f64),
            Some(Value::Float(f)) => Value::Float(*f),
            Some(other) => {
                return Err(type_err(format!(
                    "float() argument must be a string or a real number, not '{}'",
                    type_name(other)
                )))
            }
        },
        "bool" => Value::Bool(match args.first() {
            None => false,
            Some(v) => truthy(v)?,
        }),
        "list" => match args.first() {
            None => list(Vec::new()),
            Some(v) => list(it.iter_collect(v.clone())?),
        },
        "tuple" => match args.first() {
            None => Value::Tuple(Rc::new(Vec::new())),
            Some(v) => Value::Tuple(Rc::new(it.iter_collect(v.clone())?)),
        },
        "set" => {
            let mut s = Set::new();
            if let Some(v) = args.first() {
                for x in it.collect_unordered(v.clone())? {
                    s.add(x)?;
                }
            }
            Value::Set(Rc::new(RefCell::new(s)))
        }
        "dict" => {
            let mut d = Dict::new();
            if let Some(v) = args.first() {
                match v {
                    Value::Dict(src) => {
                        let pairs: Vec<(Value, Value)> =
                            src.borrow().iter().map(|(k, v)| (k.clone(), v.clone())).collect();
                        for (k, v) in pairs {
                            d.insert(k, v)?;
                        }
                    }
                    other => {
                        for pair in it.iter_collect(other.clone())? {
                            let kv = it.iter_collect(pair)?;
                            if kv.len() != 2 {
                                return Err(value_err(
                                    "dictionary update sequence element has length != 2",
                                ));
                            }
                            d.insert(kv[0].clone(), kv[1].clone())?;
                        }
                    }
                }
            }
            for (k, v) in kw {
                d.insert(Value::Str(k), v)?;
            }
            Value::Dict(Rc::new(RefCell::new(d)))
        }
        "range" => {
            no_kw("range", &kw)?;
            let n: Vec<i64> = args.iter().map(int_val).collect::<R<Vec<_>>>()?;
            match n.len() {
                1 => Value::Range(0, n[0], 1),
                2 => Value::Range(n[0], n[1], 1),
                3 => {
                    if n[2] == 0 {
                        return Err(value_err("range() arg 3 must not be zero"));
                    }
                    Value::Range(n[0], n[1], n[2])
                }
                _ => return Err(type_err("range expected 1 to 3 arguments")),
            }
        }
        // `sum` DRIVES the iterator rather than draining it into a `Vec` first.
        // Measured before the change: `sum()` of a 50,000-element int list spent
        // 23.6 ns per element materialising and dropping a copy against 18 ns
        // doing the additions — 57% of the call was the copy. `sum` of a
        // generator was indistinguishable in cost from `list()` of the same
        // generator, because the buffer was the whole job and the additions were
        // noise beside it.
        //
        // Streaming is also the more faithful of the two: CPython's `sum` pulls
        // one element at a time, so an exception from element three arrives
        // after two additions rather than after none. Nothing observable in this
        // subset can tell the difference today — `binop` cannot re-enter user
        // code, there being no `__add__` to call — but the eager version was
        // only ever accidentally right about it.
        "sum" => {
            // `start` is positional-OR-keyword since 3.8. Reading only the
            // positional slot did not refuse `sum(xs, start=10)` — it ignored
            // the keyword and summed from 0, so the answer was silently short
            // by the start value at exit 0. The type check below then never saw
            // a keyword start either, so `sum([], start='')` answered 0 where
            // CPython raises.
            if let Some((k, _)) = kw.iter().find(|(k, _)| k.as_ref() != "start") {
                return Err(type_err(format!(
                    "sum() takes no keyword arguments (got '{k}')"
                )));
            }
            let start = crate::args::bind(&args, &kw, 1, "start", "sum")?
                .unwrap_or(ival(0));
            // CPython refuses a str or bytes START before it looks at the
            // sequence at all — `sum([], '')` is a TypeError and so is
            // `sum([1, 2], '')`. lypning did not, and just concatenated:
            // `sum(['a', 'b'], '')` printed `ab` at exit 0 where CPython
            // raises. A wrong answer, not a refusal, and invisible to every
            // gate because no corpus program does it. Found by reading this
            // arm for speed, which is the second time the queue has turned up a
            // correctness bug on the way past (ledger, iteration 13).
            //
            // Only the start is checked, and only these two types, because that
            // is exactly what CPython checks: a list start concatenates lists
            // and must keep doing so. `sum(['a', 'b'])` needs no case here — the
            // default start is `0` and `0 + 'a'` already raises the TypeError
            // CPython raises, with CPython's message.
            match &start {
                Value::Str(_) => {
                    return Err(type_err("sum() can't sum strings [use ''.join(seq) instead]"))
                }
                Value::Bytes(_) => {
                    return Err(type_err("sum() can't sum bytes [use b''.join(seq) instead]"))
                }
                _ => {}
            }
            match args.first() {
                // A set is fine here only because `sum` over floats is
                // order-sensitive; ints are not, so refuse the risky half only.
                // This arm stays eager: it has to read every element to decide
                // whether to refuse at all, before it may add any of them.
                Some(Value::Set(s)) => {
                    let items = s.borrow().items.clone();
                    if items.iter().any(|x| matches!(x, Value::Float(_))) {
                        return Err(set_order_refused("sum() of a set of floats"));
                    }
                    // A float START puts every int through the float loop,
                    // which 3.14 compensates — and compensation is only
                    // nearly order-independent. `sum({1, 2**53, -2**53}, 0.0)`
                    // answered 0.0 here against CPython's 1.0.
                    if matches!(start, Value::Float(_)) {
                        return Err(set_order_refused("sum() of a set with a float start"));
                    }
                    let mut acc = start;
                    for x in items {
                        acc = it.binop(crate::ast::BinOp::Add, &acc, &x)?;
                    }
                    acc
                }
                Some(v) => {
                    // The whole reason this row exists in the corpus at 22%:
                    // `sum()` of a list of ints. Adding two i64s through
                    // `binop` costs ~54 instructions — `as_num` twice, the op
                    // match, the checked add, a `Value` built and dropped —
                    // where the operation itself is one `add` and one `jo`.
                    //
                    // Bailing out mid-scan is safe because the loop has no
                    // effects to undo: the general path below starts over from
                    // `start` and produces whatever the mixed types or the
                    // overflow deserve — a TypeError, or the `bigint` refusal
                    // that sends the program to an interpreter with bignums.
                    if let (Value::Int(Int::S(a0)), Value::List(l)) = (&start, v) {
                        let fast = {
                            let items = l.borrow();
                            let mut acc: i64 = *a0;
                            let mut ok = true;
                            for x in items.iter() {
                                match x {
                                    // A wide element, or an overflow, leaves the
                                    // fast loop with nothing undone; the general
                                    // loop below starts over from `start` and
                                    // `binop` promotes each addition.
                                    Value::Int(Int::S(n)) => match acc.checked_add(*n) {
                                        Some(t) => acc = t,
                                        None => {
                                            ok = false;
                                            break;
                                        }
                                    },
                                    _ => {
                                        ok = false;
                                        break;
                                    }
                                }
                            }
                            if ok {
                                Some(acc)
                            } else {
                                None
                            }
                        };
                        if let Some(n) = fast {
                            return Ok(ival(n));
                        }
                    }
                    // CPython's `sum` is three loops, not one: an exact-int
                    // loop on a C long, a float loop on a C double, and the
                    // generic `PyNumber_Add` loop for everything else. The
                    // float loop is where the two CPython versions this tree
                    // targets disagree. 3.12+ (`builtin_sum_impl`) runs
                    // Neumaier compensated summation there — it keeps the naive
                    // running sum `f` and a correction `c`, and adds `c` once,
                    // at the end or on leaving the loop. 3.11 has no `c`. So
                    // `sum([0.1] * 10)` is `1.0` on 3.14 and
                    // `0.9999999999999999` on 3.11, and the naive fold that
                    // stood here matched 3.11 silently: a MISMATCH against the
                    // reference, and a wrong answer for whichever interpreter
                    // the caller meant.
                    //
                    // The engine may only answer where both agree, so the loop
                    // carries both: `f` is the naive sum (bit-for-bit what 3.11
                    // returns, and what 3.12+ holds before the correction), `c`
                    // is the correction only 3.12+ adds. `float_sum_agreed`
                    // answers when `f + c` and `f` are the same bits and
                    // refuses otherwise. An int met in the float loop is where
                    // a THIRD era appears: `(double)value` added naively on
                    // 3.11-3.13, compensated on 3.14 — so two corrections ride
                    // along, and a `bool` is an int there on every version.
                    //
                    // Which loop CPython is in is tracked explicitly rather
                    // than read off `acc`'s type, because the transitions are
                    // one-way and depend on the *start*, not the running value:
                    // only an exact `int` start begins in the int loop, only
                    // leaving it on a float enters the float loop, and a `bool`
                    // start (`sum(xs, True)`) is generic from the first element
                    // — naive on every version, so answered naively here.
                    let mut iter = it.make_iter(v.clone())?;
                    let mut acc = start;
                    let mut int_loop = matches!(acc, Value::Int(_));
                    // `Some((f, c12, c14))` while CPython would be in its float loop.
                    let mut float_loop = match acc {
                        Value::Float(f) => Some((f, 0.0f64, 0.0f64)),
                        _ => None,
                    };
                    while let Some(x) = it.iter_next(&mut iter)? {
                        if let Some((f, c12, c14)) = float_loop.as_mut() {
                            match x {
                                Value::Float(x) => {
                                    let t = *f + x;
                                    let d = if f.abs() >= x.abs() { (*f - t) + x } else { (x - t) + *f };
                                    *c12 += d;
                                    *c14 += d;
                                    *f = t;
                                }
                                // An int here is naive on 3.11-3.13 and
                                // compensated on 3.14: it moves `c14` only.
                                Value::Int(_) | Value::Bool(_) => {
                                    let x = match x {
                                        // Adding a wide integer to a running
                                        // float sum needs the rounding this
                                        // engine refuses; `get()` says so.
                                        Value::Int(n) => n.get()? as f64,
                                        Value::Bool(b) => b as i64 as f64,
                                        _ => unreachable!(),
                                    };
                                    let t = *f + x;
                                    *c14 += if f.abs() >= x.abs() { (*f - t) + x } else { (x - t) + *f };
                                    *f = t;
                                }
                                other => {
                                    // 3.12+ folds `c` in before handing the
                                    // pair to `PyNumber_Add`. Nothing in this
                                    // subset adds to a float except the three
                                    // arms above, so what follows is CPython's
                                    // own TypeError — but the fold is checked
                                    // first, so a sum that already disagreed
                                    // cannot answer by erroring.
                                    acc = Value::Float(float_sum_agreed(*f, *c12, *c14)?);
                                    float_loop = None;
                                    acc = it.binop(crate::ast::BinOp::Add, &acc, &other)?;
                                }
                            }
                            continue;
                        }
                        let was_int_loop = int_loop;
                        int_loop = int_loop && matches!(x, Value::Int(_) | Value::Bool(_));
                        acc = it.binop(crate::ast::BinOp::Add, &acc, &x)?;
                        if was_int_loop && !int_loop {
                            if let Value::Float(f) = acc {
                                float_loop = Some((f, 0.0, 0.0));
                            }
                        }
                    }
                    match float_loop {
                        Some((f, c12, c14)) => Value::Float(float_sum_agreed(f, c12, c14)?),
                        None => acc,
                    }
                }
                // Argument Clinic's, and neither of the two forms
                // `check_arity` writes — which is why `sum`'s floor is 0 in the
                // table and lives here instead.
                None => {
                    return Err(type_err("sum() takes at least 1 positional argument (0 given)"))
                }
            }
        }
        "min" | "max" => {
            reject_unknown_kw(name, &kw, &["key", "default"])?;
            // `min()` is a TypeError about the ARGUMENT LIST, not a ValueError
            // about an empty iterable: with no positional at all there is no
            // iterable to be empty, and `default=` does not rescue it either.
            // The empty-sequence arm below answered the wrong exception class,
            // which a program that catches `ValueError` sees as a caught error
            // where CPython propagates.
            if args.is_empty() {
                return Err(type_err(format!("{name} expected at least 1 argument, got 0")));
            }
            let want_max = name == "max";
            let from_set = args.len() == 1 && matches!(args.first(), Some(Value::Set(_)));
            let items: Vec<Value> = if args.len() == 1 {
                it.collect_unordered(args.remove(0))?
            } else {
                args.to_vec()
            };
            let keyf = key_arg(&kw, "key");
            let default = kwget(&kw, "default");
            if items.is_empty() {
                return match default {
                    Some(d) => Ok(d),
                    // CPython 3.12 rewrote this message: 3.9 said
                    // "min() arg is an empty sequence" and the reference this
                    // repository grades against (3.14.5) says
                    // "min() iterable argument is empty". An empty match set is
                    // the NORMAL case for a glob and `min`/`max` are positions
                    // `route.rs` admits, so the older wording became reachable
                    // from an advertised one.
                    None => Err(value_err(format!("{name}() iterable argument is empty"))),
                };
            }
            let mut best = items[0].clone();
            let mut bestk = keyed(it, &keyf, &best)?;
            let mut tied = false;
            for x in &items[1..] {
                let k = keyed(it, &keyf, x)?;
                // `>` for `max` and `<` for `min`: the symbol reaches stderr
                // through the TypeError for a pair this engine will not order,
                // and CPython's names the operator the builtin actually uses.
                let o = ops::order_op(if want_max { ">" } else { "<" }, &k, &bestk)?;
                // Ties keep the FIRST element, matching CPython.
                if (want_max && o == std::cmp::Ordering::Greater)
                    || (!want_max && o == std::cmp::Ordering::Less)
                {
                    best = x.clone();
                    bestk = k;
                    tied = false;
                } else if o == std::cmp::Ordering::Equal {
                    tied = true;
                }
            }
            // "Ties keep the FIRST element" is only reproducible if there IS a
            // first. Over a SET there is not: iteration order is this engine's
            // own, so `max({-1, 1}, key=abs)` answered -1 where CPython answers
            // 1 — same set, same key, different order, and both at exit 0.
            //
            // The existing set-order guard covered `sum()` of float sets and
            // `reversed()`, and missed this path entirely. Refused only when a
            // tie ACTUALLY occurred, not whenever a key is present over a set:
            // `max(s, key=len)` with distinct lengths has one answer and should
            // keep giving it.
            if tied && from_set {
                return Err(set_order_refused(&format!(
                    "{name}() of a set where the key ties"
                )));
            }
            best
        }
        "sorted" => {
            reject_unknown_kw("sort", &kw, &["key", "reverse"])?;
            let v = args
                .first()
                .cloned()
                .ok_or_else(|| type_err("sorted expected 1 argument, got 0"))?;
            let from_set = matches!(v, Value::Set(_));
            let mut items = it.collect_unordered(v)?;
            let keyf = key_arg(&kw, "key");
            let rev = reverse_arg(&kw)?;
            let mut keys = Vec::with_capacity(items.len());
            for x in &items {
                keys.push(keyed(it, &keyf, x)?);
            }
            // Same leak as `min`/`max` above: a stable sort keeps tied elements
            // in the order it received them, and over a set that order is this
            // engine's. Only a real tie is refused — `sorted(s, key=len)` with
            // distinct lengths has one answer.
            //
            // A tie is a pair of ADJACENT keys once the keys are in order, so
            // the question is answered by one sort and one linear scan. It used
            // to be an all-pairs scan, which is O(n^2) `ops::order` calls: at
            // 60,000 distinct keys — the size a filesystem listing reaches, and
            // the size `docs/HILLCLIMB.md` iteration 76 measured — that was
            // **28.61 s** against CPython's 0.02 s (macOS arm64, 2026-09-06).
            // The keys are sorted here and again below because `sort_values`
            // permutes `items` alongside them and this pass must not.
            if from_set && keyf.is_some() {
                let mut probe = keys.clone();
                let mut probe_keys = keys.clone();
                ops::sort_values(&mut probe, &mut probe_keys, false)?;
                for pair in probe.windows(2) {
                    if ops::order(&pair[0], &pair[1])? == std::cmp::Ordering::Equal {
                        return Err(set_order_refused("sorted() of a set where the key ties"));
                    }
                }
            }
            ops::sort_values(&mut items, &mut keys, rev)?;
            list(items)
        }
        "abs" => match arg1(name, &args)? {
            #[cfg(feature = "cap-re")]
            Value::ReFlag(b) => ival(b as i64),
            // `abs(-2**63)` is 2**63, a bignum, and so is `abs()` of any wide
            // value; `bigint::abs` builds it and the core refuses.
            Value::Int(i) => match i.small().and_then(|v| v.checked_abs()) {
                Some(v) => ival(v),
                None => wide_abs(&i)?,
            },
            Value::Bool(b) => ival(b as i64),
            Value::Float(f) => Value::Float(f.abs()),
            other => {
                return Err(type_err(format!(
                    "bad operand type for abs(): '{}'",
                    type_name(&other)
                )))
            }
        },
        "round" => {
            if let Some((k, _)) = kw
                .iter()
                .find(|(k, _)| !matches!(k.as_ref(), "number" | "ndigits"))
            {
                return Err(type_err(format!(
                    "'{k}' is an invalid keyword argument for round()"
                )));
            }
            let v = crate::args::bind(&args, &kw, 0, "number", "round")?
                .ok_or_else(|| type_err("round() missing required argument 'number' (pos 1)"))?;
            // `ndigits=None` is the DEFAULT and means "round to an integer", the
            // same family as `key=None` (iteration 51). Passed through
            // `int_val` it raised at exit 1, so `round(x, None)` — which is what
            // an optional precision looks like — failed on valid Python.
            // `round(re.I)` and `round(re.I, -1)` are the int's: substitute
            // it and let the arms below answer.
            #[cfg(feature = "cap-re")]
            let v = crate::re::as_int(&v).unwrap_or(v);
            let nd = match crate::args::bind(&args, &kw, 1, "ndigits", "round")? {
                None | Some(Value::None) => None,
                Some(x) => Some(int_val(&x)?),
            };
            match (&v, nd) {
                (Value::Int(i), None) => Value::Int(i.clone()),
                (Value::Int(i), Some(n)) if n >= 0 => Value::Int(i.clone()),
                // `round(12345, -2)` is 12300, and `round(15, -1)` is 20 while
                // `round(25, -1)` is 20 as well — half to EVEN, like everywhere
                // else in Python. This used to refuse, which was safe but cost a
                // CPython spawn for an ordinary "round to the nearest hundred".
                //
                // Done in integer arithmetic rather than by scaling through f64:
                // an int near 2**63 has more significant digits than a double
                // carries, so the float path would answer a rounded number that
                // is not the rounded number.
                // A WIDE value rounded to a negative ndigits needs the same
                // integer arithmetic at a width the scale below does not have.
                // Refused rather than approximated.
                (Value::Int(i), Some(n)) => {
                    let i = &i.get()?;
                    let k = (-n) as u32;
                    let scale = match 10i64.checked_pow(k) {
                        Some(s) => s,
                        // Past 10**18 every i64 rounds to zero, and Python
                        // agrees — there is nothing to refuse.
                        None => return Ok(ival(0)),
                    };
                    let q = i.div_euclid(scale);
                    let rem = i.rem_euclid(scale);
                    let half = scale / 2;
                    let up = rem > half || (rem == half && q % 2 != 0);
                    let out = if up { q + 1 } else { q };
                    match out.checked_mul(scale) {
                        Some(r) => ival(r),
                        None => {
                            return Err(unsupported(
                                "bigint",
                                "round() result beyond 64-bit range",
                            ))
                        }
                    }
                }
                (Value::Float(f), None) => ival(float_to_int(round_half_even(*f, 0), "round")?),
                (Value::Float(f), Some(n)) => Value::Float(round_half_even(*f, n)),
                (Value::Bool(b), _) => ival(*b as i64),
                _ => {
                    return Err(unsupported(
                        "round",
                        "round() of this argument combination",
                    ))
                }
            }
        }
        "divmod" => {
            let (a, b) = (arg1(name, &args)?, args.get(1).cloned().unwrap_or(Value::None));
            let q = it.binop(crate::ast::BinOp::FloorDiv, &a, &b)?;
            let r = it.binop(crate::ast::BinOp::Mod, &a, &b)?;
            Value::Tuple(Rc::new(vec![q, r]))
        }
        "any" | "all" => {
            let want_all = name == "all";
            let v = arg1(name, &args)?;
            // Short-circuits, which is why the iterator is driven rather than
            // materialised: `any(1/x for x in [1,0])` must not divide by zero.
            let mut iter = match &v {
                Value::Set(s) => Iter::Vec(s.borrow().items.clone(), 0),
                other => it.make_iter(other.clone())?,
            };
            let mut result = want_all;
            while let Some(x) = it.iter_next(&mut iter)? {
                let t = truthy(&x)?;
                if t != want_all {
                    result = t;
                    break;
                }
            }
            Value::Bool(result)
        }
        "enumerate" => {
            // Same exemption, same hole as `zip` above: `start` is real, and
            // everything else was dropped rather than refused, so
            // `enumerate(xs, strict=True)` answered at exit 0 where CPython
            // raises TypeError.
            if let Some((k, _)) = kw.iter().find(|(k, _)| k.as_ref() != "start") {
                return Err(type_err(format!(
                    "'{k}' is an invalid keyword argument for enumerate()"
                )));
            }
            // Argument Clinic names the parameter; `arg1` writes the generic
            // `missing 1 required positional argument`, which is a Python-level
            // function's wording and not this one's.
            let v = match args.first() {
                Some(v) => v.clone(),
                None => {
                    return Err(type_err("enumerate() missing required argument 'iterable'"))
                }
            };
            let start = match crate::args::bind(&args, &kw, 1, "start", "enumerate")? {
                Some(x) => int_val(&x)?,
                None => 0,
            };
            let inner = it.make_iter(v)?;
            Value::IterObj(
                Rc::new(RefCell::new(Iter::Enumerate(Box::new(inner), start))),
                "enumerate",
            )
        }
        "zip" => {
            // `zip` and `enumerate` are exempt from NO_KEYWORDS because they
            // really do take keywords — and the exemption became exemption from
            // ALL validation, so any keyword was dropped in silence.
            // `zip(a, b, strict=True)` truncated to the shorter input at exit 0,
            // which is the ONE thing the caller wrote `strict=True` to prevent:
            // the guard was removed by the runtime that was asked to enforce it.
            //
            // Refused rather than implemented. `strict` needs a length check the
            // lazy `Iter::Zip` does not currently make, and per invariant 1 a
            // refusal the dispatcher can route onward beats an approximation.
            if let Some((k, _)) = kw.first() {
                if k.as_ref() == "strict" {
                    return Err(unsupported("argument", "keyword strict"));
                }
                return Err(type_err(format!(
                    "'{k}' is an invalid keyword argument for zip()"
                )));
            }
            let mut its = Vec::with_capacity(args.len());
            for i in 0..args.len() {
                let a = args.take(i);
                its.push(it.make_iter(a)?);
            }
            Value::IterObj(Rc::new(RefCell::new(Iter::Zip(its))), "zip")
        }
        "map" => {
            // TWO, not one. `map(abs)` built an `Iter::Map` over ZERO iterables,
            // which — like `Iter::Zip` over zero — has nothing to report
            // exhaustion, so it called `abs()` forever: exit 0 while the value
            // went unconsumed, and `abs() takes exactly one argument (0 given)`
            // the moment it was. CPython raises here, before the iterator
            // exists, and the trailing full stop is its own.
            if args.len() < 2 {
                return Err(type_err("map() must have at least two arguments."));
            }
            let f = args.remove(0);
            let mut its = Vec::with_capacity(args.len());
            for i in 0..args.len() {
                let a = args.take(i);
                its.push(it.make_iter(a)?);
            }
            Value::IterObj(Rc::new(RefCell::new(Iter::Map(f, its))), "map")
        }
        "filter" => {
            let f = args
                .first()
                .cloned()
                .ok_or_else(|| type_err("filter expected 2 arguments, got 0"))?;
            let v = args
                .get(1)
                .cloned()
                .ok_or_else(|| type_err("filter expected 2 arguments, got 1"))?;
            let inner = it.make_iter(v)?;
            let pred = if matches!(f, Value::None) { None } else { Some(f) };
            Value::IterObj(
                Rc::new(RefCell::new(Iter::Filter(pred, Box::new(inner)))),
                "filter",
            )
        }
        "reversed" => {
            let v = arg1(name, &args)?;
            // REVERSING AN ITERATOR IS NOT A THING, and this used to do it.
            // CPython needs `__reversed__`, or `__len__` and `__getitem__`
            // together, so a sequence reverses and a one-pass iterator raises.
            // `list(reversed(iter([1, 2])))` answered `[2, 1]` here and is a
            // TypeError there — a divergence in the rarer direction, where the
            // engine SUCCEEDS and CPython refuses, which no amount of trusting
            // the engine's own errors would have caught.
            //
            // A set is not reversible either, and this refused it as a
            // set-order exposure. That was a spawn spent on nothing: CPython
            // never gets far enough to iterate, so the TypeError below is
            // exact and the refusal is not needed.
            #[cfg(feature = "cap-pathlib")]
            crate::pathlib::guard_view(&v, "reversed() of")?;
            #[cfg(feature = "cap-re")]
            crate::re::guard_one(&v, "reversed() of")?;
            match &v {
                Value::Str(_)
                | Value::Bytes(_)
                | Value::List(_)
                | Value::Tuple(_)
                | Value::Range(..)
                | Value::Dict(_)
                | Value::DictView(..) => {}
                // The message names the type, and an iterator's type name is
                // one this engine cannot spell: CPython has a family of them
                // (`list_iterator`, `tuple_iterator`, `str_ascii_iterator` —
                // which is `str_iterator` for a non-ASCII string, an internal
                // representation detail). So this refuses for the same reason
                // `repr()` of an iterator refuses: the answer contains
                // something not reproducible.
                Value::IterObj(..) | Value::Gen(_) => {
                    return Err(unsupported(
                        "iterator-type-name",
                        "reversed() of an iterator, whose CPython TypeError names one of a \
                         family of iterator types this engine does not distinguish",
                    ))
                }
                other => {
                    return Err(type_err(format!(
                        "'{}' object is not reversible",
                        type_name(other)
                    )))
                }
            }
            let mut items = it.iter_collect(v)?;
            items.reverse();
            Value::IterObj(
                Rc::new(RefCell::new(Iter::Vec(items, 0))),
                "list_reverseiterator",
            )
        }
        "iter" => {
            // The two-argument form is `iter(callable, sentinel)` — a different
            // iterator entirely, which this engine does not build. The sentinel
            // was DROPPED, so `iter([1, 2], 0)` handed back a plain list
            // iterator at exit 0 where CPython raises. The ceiling in `arity`
            // admits two arguments; what they MEAN is decided here, and the two
            // outcomes are not the same: a first argument that is not callable
            // is the caller's own TypeError and is served exactly, while a real
            // callable is a feature this crate does not have and refuses.
            if args.len() == 2 {
                let v = arg1(name, &args)?;
                if !matches!(v, Value::Func(_) | Value::Builtin(_) | Value::Bound(..)) {
                    return Err(type_err("iter(v, w): v must be callable"));
                }
                return Err(unsupported("builtin", "iter(callable, sentinel)"));
            }
            let v = arg1(name, &args)?;
            // Before the `IterObj` shortcut below: a hash object wears that
            // shape and is not iterable, so handing it back would answer where
            // CPython raises.
            #[cfg(feature = "cap-hashlib")]
            if crate::hashlib::as_hasher(&v).is_some() {
                return Err(crate::hashlib::not_iterable());
            }
            if let Value::IterObj(..) = v {
                return Ok(v);
            }
            let inner = it.make_iter(v)?;
            Value::IterObj(Rc::new(RefCell::new(inner)), "iterator")
        }
        "next" => {
            let v = arg1(name, &args)?;
            let mut i = match &v {
                Value::IterObj(inner, _) => Iter::Shared(inner.clone()),
                Value::Gen(g) => Iter::Gen(g.clone()),
                other => {
                    return Err(type_err(format!(
                        "'{}' object is not an iterator",
                        type_name(other)
                    )))
                }
            };
            match it.iter_next(&mut i)? {
                Some(v) => v,
                None => match args.get(1) {
                    Some(d) => d.clone(),
                    None => return Err(LypningError::exc("StopIteration", "")),
                },
            }
        }
        "ord" => {
            let v = arg1(name, &args)?;
            match &v {
                Value::Str(s) => {
                    let mut c = s.chars();
                    match (c.next(), c.next()) {
                        (Some(ch), None) => ival(ch as i64),
                        _ => {
                            return Err(type_err(format!(
                                "ord() expected a character, but string of length {} found",
                                s.chars().count()
                            )))
                        }
                    }
                }
                Value::Bytes(b) if b.len() == 1 => ival(b[0] as i64),
                _ => return Err(type_err("ord() expected string of length 1")),
            }
        }
        "chr" => {
            let n = int_val(&arg1(name, &args)?)?;
            match u32::try_from(n).ok().and_then(char::from_u32) {
                Some(c) => Value::Str(crate::value::char_str(c)),
                None => return Err(value_err("chr() arg not in range(0x110000)")),
            }
        }
        "hex" | "oct" | "bin" => {
            let v = arg1(name, &args)?;
            let (radix, pfx) = match name {
                "hex" => (16, "0x"),
                "oct" => (8, "0o"),
                _ => (2, "0b"),
            };
            // The `format()` path for the same three radices goes through
            // `fmt::wide_digits`; this one is the BUILTIN, which CPython reaches
            // by a different slot and which has no digit cap of its own — so a
            // wide value is answered here rather than refused, the way it is
            // there. Splitting the sign from the digits is what lets one
            // conversion serve both: CPython puts the minus BEFORE the prefix,
            // `-0x10`.
            #[cfg(feature = "cap-bigint")]
            if let Value::Int(crate::value::Int::B(b)) = &v {
                let body = crate::bigint::to_radix(b, radix, false);
                let sign = if b.neg { "-" } else { "" };
                return Ok(Value::Str(format!("{sign}{pfx}{body}").into()));
            }
            let n = int_val(&v)?;
            let body = match radix {
                16 => format!("{:x}", n.unsigned_abs()),
                8 => format!("{:o}", n.unsigned_abs()),
                _ => format!("{:b}", n.unsigned_abs()),
            };
            Value::Str(format!("{}{pfx}{body}", if n < 0 { "-" } else { "" }).into())
        }
        "format" => {
            let v = arg1(name, &args)?;
            let spec = match args.get(1) {
                Some(s) => fmt::to_str(s)?,
                None => String::new(),
            };
            Value::Str(fmt::format_value(&v, &spec)?.into())
        }
        "type" => {
            // `type(name, bases, dict)` is the CLASS CONSTRUCTOR, a second
            // signature this engine has no class objects to answer with, and
            // `type()` with any other count is a TypeError with one wording for
            // both. Not in `arity()` because that table's floor and ceiling
            // cannot say "1 or 3": every count but those two answered
            // `<class 'int'>` for the FIRST argument at exit 0, and the
            // three-argument form answered it for a program CPython runs.
            if args.len() == 3 {
                return Err(unsupported("type", "type() with 3 arguments (class creation)"));
            }
            if args.len() != 1 {
                return Err(type_err("type() takes 1 or 3 arguments"));
            }
            let v = arg1(name, &args)?;
            Value::Builtin(match type_name(&v) {
                "int" => "int",
                "str" => "str",
                "float" => "float",
                "bool" => "bool",
                "list" => "list",
                "dict" => "dict",
                "set" => "set",
                "tuple" => "tuple",
                "bytes" => "bytes",
                other => {
                    return Err(unsupported(
                        "type",
                        &format!("type() of a {other}"),
                    ))
                }
            })
        }
        "isinstance" => {
            let v = arg1(name, &args)?;
            let cls = args
                .get(1)
                .cloned()
                .ok_or_else(|| type_err("isinstance expected 2 arguments, got 1"))?;
            // `&'static str`, not `String`: these come out of `Value::Builtin`,
            // which already interns them, and building a `String` per class was
            // an allocation for a comparison.
            let names: Vec<&'static str> = match &cls {
                Value::Tuple(t) => t
                    .iter()
                    .map(|c| match c {
                        Value::Builtin(b) => Ok(*b),
                        other => Err(type_err(format!(
                            "isinstance() arg 2 must be a type, not {}",
                            type_name(other)
                        ))),
                    })
                    .collect::<R<Vec<_>>>()?,
                Value::Builtin(b) => vec![*b],
                other => {
                    return Err(type_err(format!(
                        "isinstance() arg 2 must be a type, not {}",
                        type_name(other)
                    )))
                }
            };
            // `isinstance(x, type)` asks whether x is a CLASS. lypning has no
            // class objects of its own and `Value::Builtin` is both `int` and
            // `print`, so answering would mean guessing which builtins are
            // types. Refused instead: a refusal costs one spawn and CPython
            // answers, and this is exactly the trade invariant 1 describes.
            if names.contains(&"type") {
                return Err(unsupported("isinstance", "isinstance() against `type`"));
            }
            let t = type_name(&v);
            #[cfg(feature = "cap-pathlib")]
            if names.iter().any(|n| crate::pathlib::isinstance_hit(n, &v)) {
                return Ok(Value::Bool(true));
            }
            // `IntFlag` is an int subclass: `isinstance(re.I, int)` is True,
            // and `isinstance(re.I, bool)` is not.
            #[cfg(feature = "cap-re")]
            if matches!(v, Value::ReFlag(_)) && names.contains(&"int") {
                return Ok(Value::Bool(true));
            }
            Value::Bool(names.iter().any(|n| {
                // An exception instance is matched through the SAME hierarchy
                // table `except` uses, not by its type name. `type_name` of any
                // `Exc` is the literal string "Exception", so comparing against
                // it answered False for `isinstance(ValueError('b'),
                // ValueError)` and True for `isinstance(SystemExit(),
                // Exception)` — both at exit 0, both wrong, and neither visible
                // to `conformance` because no corpus entry did it yet. One
                // table, so this can never disagree with an `except` clause.
                if let Value::Exc(kind, _) = &v {
                    return crate::eval::exc_matches(n, kind);
                }
                *n == t
                    // bool is a subclass of int in Python; str/bytes are not
                    // related, and neither are list/tuple.
                    || (*n == "int" && t == "bool")
                    || (*n == "float" && matches!(t, "float"))
                    // …and `Counter` and `defaultdict` are subclasses of dict,
                    // which is exactly the idiom that would have answered False
                    // at exit 0: `isinstance(c, dict)` is True in CPython.
                    || dict_subclass(n, t)
            }))
        }
        "open" => {
            let path = match args.first() {
                Some(Value::Str(s)) => s.to_string(),
                // `open(Path('x'))` is `__fspath__`, and `to_str` knows it.
                #[cfg(feature = "cap-pathlib")]
                Some(p @ Value::Path(..)) => fmt::to_str(p)?,
                // `open(0)` is the descriptor: CPython reads stdin. Stringified
                // it opened a FILE named "0" — FileNotFoundError at exit 1 where
                // CPython answers. A descriptor is not something this engine
                // opens, so it refuses and the answer comes from CPython.
                Some(_) => {
                    return Err(unsupported(
                        "builtin",
                        "open() with a non-str path (a file descriptor)",
                    ))
                }
                None => return Err(type_err("open() missing required argument 'file' (pos 1)")),
            };
            let mode = match crate::args::bind(&args, &kw, 1, "mode", "open")? {
                Some(v) => fmt::to_str(&v)?,
                None => "r".to_string(),
            };
            open_value(&path, &mode, &kw)?
        }
        "input" => {
            if let Some(p) = args.first() {
                mio::write_out(fmt::to_str(p)?.as_bytes())?;
            }
            match mio::stdin_line()? {
                Some(b) => {
                    let s = crate::iter::decode_text(
                        &b,
                        "non-UTF-8 bytes on stdin (CPython decodes it with surrogateescape)",
                    )?;
                    Value::Str(s.trim_end_matches('\n').trim_end_matches('\r').into())
                }
                None => return Err(LypningError::exc("EOFError", "EOF when reading a line")),
            }
        }
        "bytes" => {
            // An `encoding` is only meaningful for a str source, and CPython says so
            // before it looks at the source at all. Every arm below but the
            // `Value::Str` one DROPPED the pair in silence, so `bytes(2, 'utf-8')`
            // answered `b'\x00\x00'` and `bytes(2, 0)` answered the same, where
            // CPython raises two different TypeErrors. The str arm reads the pair
            // itself, which is why it is excluded here rather than checked twice.
            if !matches!(args.first(), Some(Value::Str(_))) {
                let (enc, errs) = text_codec_args("bytes", &args, &kw)?;
                let subject = args.first().unwrap_or(&Value::None);
                if let Some(e) =
                    codec_without_subject("bytes", subject, enc.as_ref(), errs.is_some(), false)
                {
                    return Err(e);
                }
            }
            match args.first() {
                None => Value::Bytes(Rc::new(Vec::new())),
                // `PurePath.__bytes__` is `os.fsencode(self)`, so CPython answers
                // the path's own text as bytes. Reaching the generic arm instead
                // raised "not iterable" at exit 1 for a program CPython runs — a
                // wrong exit code, not a refusal, which the chain never retries.
                #[cfg(feature = "cap-pathlib")]
                Some(Value::Path(p, false)) => Value::Bytes(Rc::new(p.as_bytes().to_vec())),
                // `bytes('abc')` is a TypeError: a str has no bytes until an
                // ENCODING says which. Answering `b'abc'` silently picked UTF-8 on
                // the caller's behalf — right for ASCII and a wrong answer the
                // moment the text is not, which is exactly when it matters.
                // `bytes('abc', 'utf-8')` is the spelling that works and still does.
                Some(Value::Str(s)) => {
                    // The encoding argument's VALUE was never read: `bytes('a',
                    // 'bogus')` answered b'a' where CPython raises LookupError, and
                    // `bytes('héllo', 'latin-1')` answered the UTF-8 bytes — data
                    // corruption at exit 0. Same rule as `str.encode` above: UTF-8
                    // spellings pass, ASCII validates, anything else refuses.
                    let enc = crate::args::bind(&args, &kw, 1, "encoding", "bytes")?;
                    let Some(e) = enc else {
                        return Err(type_err("string argument without an encoding"));
                    };
                    let e = fmt::to_str(&e)?.to_ascii_lowercase().replace('_', "-");
                    if !matches!(e.as_str(), "utf-8" | "utf8" | "ascii") {
                        return Err(unsupported("encoding", &format!("bytes(str, '{e}')")));
                    }
                    if e == "ascii" && !s.is_ascii() {
                        return Err(LypningError::exc(
                            "UnicodeEncodeError",
                            "'ascii' codec can't encode character",
                        ));
                    }
                    Value::Bytes(Rc::new(s.as_bytes().to_vec()))
                }
                Some(Value::Bytes(b)) => Value::Bytes(b.clone()),
                // `bytes(2**100)` is a MemoryError in CPython, not a value; the
                // machine-word requirement refuses, which is one spawn and the right
                // answer.
                Some(Value::Int(n)) => {
                    // `.max(0)` CLAMPED: `bytes(-1)` answered `b''` at exit 0 where
                    // CPython raises `ValueError: negative count`. A count that is
                    // not a count is the caller's mistake, and answering the empty
                    // object hides it — the program keeps running with an object it
                    // never asked for. Served exactly rather than refused: the
                    // message is four bytes of table and CPython's own wording.
                    let n = n.get()?;
                    if n < 0 {
                        return Err(value_err("negative count"));
                    }
                    Value::Bytes(Rc::new(vec![0u8; n as usize]))
                }
                // `bytes(re.I)` is `b'\x00\x00'` — the int path.
                #[cfg(feature = "cap-re")]
                Some(Value::ReFlag(b)) => Value::Bytes(Rc::new(vec![0u8; *b as usize])),
                #[cfg(feature = "cap-re")]
                Some(v @ (Value::Pattern(_) | Value::Match(_))) => {
                    return Err(crate::re::guard_one(v, "bytes() of").unwrap_err())
                }
                Some(other) => {
                    let items = it.iter_collect(other.clone())?;
                    let mut out = Vec::with_capacity(items.len());
                    for x in items {
                        // `as u8` TRUNCATES in Rust, so bytes([2**62]) was b"\x00"
                        // and bytes([300]) was b"\x2c" — silent data corruption at
                        // exit 0 where CPython raises. Found by scripts/lypning-fuzz.mjs.
                        let n = int_val(&x)?;
                        if !(0..=255).contains(&n) {
                            return Err(value_err("bytes must be in range(0, 256)"));
                        }
                        out.push(n as u8);
                    }
                    Value::Bytes(Rc::new(out))
                }
            }
        }
        other => {
            return Err(unsupported(
                "builtin",
                &format!("builtin function {other}()"),
            ))
        }
    })
}

pub fn open_value(path: &str, mode: &str, kw: &[(Rc<str>, Value)]) -> R<Value> {
    let binary = mode.contains('b');
    if let Some(enc) = kwget(kw, "encoding") {
        let e = fmt::to_str(&enc)?.to_ascii_lowercase().replace('_', "-");
        if !matches!(e.as_str(), "utf-8" | "utf8" | "ascii" | "none") {
            return Err(unsupported("encoding", &format!("text encoding '{e}'")));
        }
    }
    // CPython rejects `newline=` on a BINARY stream before it looks at the
    // value — `open(p,'rb',newline='')` is a ValueError, not a raw stream. The
    // check has to come before `newline_mode_of`, which reads the value and
    // would otherwise accept `''` in binary mode on the variant that serves it
    // and `'\n'` on the one that does not.
    if binary {
        if let Some(nl) = kwget(kw, "newline") {
            if !matches!(nl, Value::None) {
                return Err(LypningError::exc(
                    "ValueError",
                    "binary mode doesn't take a newline argument",
                ));
            }
        }
    }
    let nl = newline_mode_of(kw)?;
    let base: String = mode.chars().filter(|c| !matches!(c, 'b' | 't')).collect();
    let mut f = mio::open_file(path, if base.is_empty() { "r" } else { &base }, binary)?;
    set_newline(&mut f, nl);
    Ok(Value::File(Rc::new(RefCell::new(f))))
}

/// What `open(newline=…)` asked for, on the variant that can tell the three
/// modes apart.
///
/// `newline=''` is the spelling `csv`'s own documentation requires, and it is
/// what 13 of the 15 corpus readers over a file are written with — a mine on
/// 2026-09-06 finds that 16 of the 17 corpus programs passing `newline=` to
/// `open` also import `csv`, and the seventeenth only prints the word. So it is
/// served where `cap-csv` is and refused where it is not, which leaves the
/// frozen core exactly as it was. Accepting it is EXACT rather than generous:
/// this engine's file object has never translated a line ending, which is what
/// `newline=''` means. The mode is only recorded — `csv.rs` is the one reader,
/// because to a `csv.reader` a `\r` inside a quoted field is the difference
/// between one record and two.
#[cfg(feature = "cap-csv")]
fn newline_mode_of(kw: &[(Rc<str>, Value)]) -> R<u8> {
    match kwget(kw, "newline") {
        None | Some(Value::None) => Ok(mio::NEWLINE_UNIVERSAL),
        Some(v) => match fmt::to_str(&v)?.as_str() {
            "" => Ok(mio::NEWLINE_RAW),
            "\n" => Ok(mio::NEWLINE_KEEP_NL),
            _ => Err(unsupported("open-newline", "open(newline=…) translation")),
        },
    }
}

/// The core's half: `newline=None` and `newline='\n'` are this engine's own
/// behaviour and everything else refuses, exactly as it did before `cap-csv`
/// existed.
#[cfg(not(feature = "cap-csv"))]
fn newline_mode_of(kw: &[(Rc<str>, Value)]) -> R<()> {
    match kwget(kw, "newline") {
        None | Some(Value::None) => Ok(()),
        Some(v) if fmt::to_str(&v)? == "\n" => Ok(()),
        Some(_) => Err(unsupported("open-newline", "open(newline=…) translation")),
    }
}

#[cfg(feature = "cap-csv")]
fn set_newline(f: &mut mio::FileObj, nl: u8) {
    f.newline_mode = nl;
}

#[cfg(not(feature = "cap-csv"))]
fn set_newline(_f: &mut mio::FileObj, _nl: ()) {}

/// The `errors=` argument of `bytes.decode` and `str(bytes, encoding, errors)`.
///
/// `"replace"` and `"ignore"` would need CPython's exact replacement behaviour;
/// refused rather than approximated, whichever of the two spellings asked.
pub fn check_decode_errors(errs: Option<&Value>) -> R<()> {
    if let Some(v) = errs {
        let e = fmt::to_str(v)?;
        if e != "strict" {
            return Err(unsupported("encoding", &format!("decode(errors='{e}')")));
        }
    }
    Ok(())
}

/// The `(encoding, errors)` tail of `str(object, encoding, errors)` and
/// `bytes(source, encoding, errors)`.
///
/// Both take the pair positionally OR by keyword, and the arms below were
/// reading only one spelling each — so `bytes(2, encoding='utf-8')` took a
/// different path from `bytes(2, 'utf-8')` for one and the same call. Read
/// through [`crate::args::bind`], which is the crate's only correct way to read
/// a positional-or-keyword parameter and says why.
fn text_codec_args(
    who: &str,
    args: &Args,
    kw: &[(Rc<str>, Value)],
) -> R<(Option<Value>, Option<Value>)> {
    Ok((
        crate::args::bind(args, kw, 1, "encoding", who)?,
        crate::args::bind(args, kw, 2, "errors", who)?,
    ))
}

/// CPython's TypeError for a `str()`/`bytes()` call whose FIRST argument cannot
/// take an encoding at all, or `None` when no encoding was given.
///
/// Three texts, chosen by the two argument types, and all three measured on
/// CPython 3.14.5 on 2026-09-07. `who` is the constructor's own name, which
/// only the first text carries.
fn codec_without_subject(
    who: &str,
    subject: &Value,
    enc: Option<&Value>,
    errs: bool,
    decoding: bool,
) -> Option<LypningError> {
    if let Some(e) = enc {
        if !matches!(e, Value::Str(_)) {
            return Some(type_err(format!(
                "{who}() argument 'encoding' must be str, not {}",
                type_name(e)
            )));
        }
    } else if !errs {
        return None;
    }
    Some(type_err(if decoding {
        // `str(1, 'utf-8')`: the encoding is fine, the OBJECT is not bytes.
        format!(
            "decoding to str: need a bytes-like object, {} found",
            type_name(subject)
        )
    } else {
        // `bytes(2, 'utf-8')`: an encoding is only meaningful for a str.
        format!("{} without a string argument", if enc.is_some() { "encoding" } else { "errors" })
    }))
}

fn arg1(name: &str, args: &[Value]) -> R<Value> {
    args.first()
        .cloned()
        .ok_or_else(|| type_err(format!("{name}() missing 1 required positional argument")))
}

fn keyed(it: &mut Interp, keyf: &Option<Value>, v: &Value) -> R<Value> {
    match keyf {
        None => Ok(v.clone()),
        Some(f) => it.call(f, &mut Args::one(v.clone()), Vec::new()),
    }
}

pub fn length(v: &Value) -> R<usize> {
    Ok(match v {
        Value::Str(s) => s.chars().count(),
        Value::Bytes(b) => b.len(),
        Value::List(l) => l.borrow().len(),
        Value::Tuple(t) => t.len(),
        Value::Dict(d) => d.borrow().len(),
        Value::Set(s) => s.borrow().len(),
        Value::DictView(d, _) => d.borrow().len(),
        // `len(p.parents)`. A bare `Path` has no length in CPython either, so
        // it falls to the TypeError below with the type name it prints.
        #[cfg(feature = "cap-pathlib")]
        Value::Path(s, true) => crate::pathlib::view_len(s),
        Value::Range(a, b, st) => {
            // CPython's `len()` returns a C ssize_t, so a range longer than one
            // raises rather than truncating — and this answered 0, because the
            // i64 length had wrapped negative and `.max(0)` tidied it away.
            let n = range_len(*a, *b, *st);
            if n > i64::MAX as i128 {
                return Err(overflow_err("Python int too large to convert to C ssize_t"));
            }
            n.max(0) as usize
        }
        // Same argument as `reversed` above and as `repr`: the message names
        // the type, and CPython spells an iterator's type name in a way this
        // engine cannot reproduce, so it refuses rather than print a name that
        // is merely plausible.
        // `len(re.I)` is the member count in CPython; the TypeError below
        // would be exit 1 where it answers.
        #[cfg(feature = "cap-re")]
        Value::ReFlag(_) => {
            return Err(crate::re::refuse("len() of a RegexFlag (Flag.__len__)"))
        }
        Value::IterObj(..) | Value::Gen(_) => {
            return Err(unsupported(
                "iterator-type-name",
                "len() of an iterator, whose CPython TypeError names one of a family of \
                 iterator types this engine does not distinguish",
            ))
        }
        other => {
            return Err(type_err(format!(
                "object of type '{}' has no len()",
                type_name(other)
            )))
        }
    })
}

/// Python rounds half to EVEN, and does so on the exact binary value — which is
/// why `round(2.675, 2)` is 2.67 and not 2.68.
fn round_half_even(f: f64, ndigits: i64) -> f64 {
    if !f.is_finite() {
        return f;
    }
    if ndigits == 0 {
        let r = f.round();
        return if (f - f.trunc()).abs() == 0.5 && r % 2.0 != 0.0 {
            // The half-even correction can land on zero, and `-1.0 - -1.0` is
            // +0.0 in IEEE where CPython keeps `round(-0.5, 0) == -0.0`. The
            // sign of a zero is the only thing this line restores.
            let even = r - f.signum();
            if even == 0.0 {
                even.copysign(f)
            } else {
                even
            }
        } else {
            r
        };
    }
    // Formatting already rounds half-to-even on the exact value, so reuse it
    // rather than scaling by a power of ten (which introduces its own error).
    if (0..=17).contains(&ndigits) {
        let s = format!("{:.*}", ndigits as usize, f);
        return s.parse().unwrap_or(f);
    }
    if ndigits > 17 {
        return f;
    }
    // NEGATIVE ndigits rounds to tens, hundreds and so on, and Rust's `round()`
    // breaks ties AWAY FROM ZERO where Python breaks them to even. The
    // `ndigits == 0` branch above already carried that correction and the
    // positive branch gets it free from the formatter; only this one rounded
    // `round(5.0, -1)` to 10.0 where CPython answers 0.0, and `round(25.0, -1)`
    // to 30.0 where CPython answers 20.0.
    let scale = 10f64.powi(-ndigits as i32);
    let q = f / scale;
    let mut r = q.round();
    if (q - q.trunc()).abs() == 0.5 && r % 2.0 != 0.0 {
        r -= q.signum();
    }
    // And the same zero-sign restoration as above: `round(-5.0, -1)` is `-0.0`,
    // which `repr` shows, and the correction above produces a positive zero.
    if r == 0.0 {
        return (0.0f64).copysign(f) * scale;
    }
    r * scale
}
