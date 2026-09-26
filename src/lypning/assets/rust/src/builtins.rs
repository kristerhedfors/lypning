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
            "sum() over floats, which CPython versions round differently (3.12+ compensates floats, 3.14 compensates ints in the float loop too); the answers differ",
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

/// Exception classes that exist but are NOT in the builtin namespace.
///
/// `JSONDecodeError` is a real class with its own name, its own identity and
/// its own `repr`, reachable only as `json.JSONDecodeError` — a BARE
/// `JSONDecodeError` is a `NameError` in CPython and must stay one here. That
/// is the whole reason this is a second list rather than three more entries in
/// `EXCEPTIONS`: `builtin()` walks `EXCEPTIONS` to RESOLVE a bare name, and a
/// name in it becomes visible everywhere.
///
/// It was spelled `Value::Builtin("ValueError")` until 2026-09-12, on the
/// reasoning in [`class_repr_name`] that `isinstance` and `except` cannot tell
/// the pair apart. They cannot — but `__name__` and `is` can, and both were
/// answering for the wrong class at exit 0:
///
/// ```text
/// json.JSONDecodeError.__name__      CPython JSONDecodeError, here ValueError
/// ValueError is json.JSONDecodeError CPython False,           here True
/// json.JSONDecodeError is ValueError CPython False,           here True
/// ```
///
/// Three silent wrong answers, none of which any gate could see: no corpus
/// program asks a caught exception for its class. `nt refusals --run` found
/// them, in the population that does — 1,173 programs a model wrote.
pub const MODULE_EXCEPTIONS: &[&str] = &["JSONDecodeError"];

/// The ZERO of every run of Unicode decimal digits (category Nd), read off
/// CPython 3.14.5's `unicodedata` (Unicode 16.0.0) on 2026-09-25: 75 runs of
/// exactly ten, `0`..`9` in order, which is every non-ASCII decimal there is.
const DECIMAL_ZEROS: [u32; 75] = [
    0x660, 0x6f0, 0x7c0, 0x966, 0x9e6, 0xa66, 0xae6, 0xb66, 0xbe6, 0xc66, 0xce6, 0xd66, 0xde6,
    0xe50, 0xed0, 0xf20, 0x1040, 0x1090, 0x17e0, 0x1810, 0x1946, 0x19d0, 0x1a80, 0x1a90, 0x1b50,
    0x1bb0, 0x1c40, 0x1c50, 0xa620, 0xa8d0, 0xa900, 0xa9d0, 0xa9f0, 0xaa50, 0xabf0, 0xff10,
    0x104a0, 0x10d30, 0x10d40, 0x11066, 0x110f0, 0x11136, 0x111d0, 0x112f0, 0x11450, 0x114d0,
    0x11650, 0x116c0, 0x116d0, 0x116da, 0x11730, 0x118e0, 0x11950, 0x11bf0, 0x11c50, 0x11d50,
    0x11da0, 0x11f50, 0x16130, 0x16a60, 0x16ac0, 0x16b50, 0x16d70, 0x1ccf0, 0x1d7ce, 0x1d7d8,
    0x1d7e2, 0x1d7ec, 0x1d7f6, 0x1e140, 0x1e2f0, 0x1e4f0, 0x1e5f1, 0x1e950, 0x1fbf0,
];

/// CPython's `_PyUnicode_TransformDecimalAndSpaceToASCII`, which `int()` and
/// `float()` of a str read through: a Unicode decimal digit is its ASCII
/// digit, Unicode whitespace is a space, and any other non-ASCII character
/// is `?`, which no literal accepts. `int('٣')` is 3 in CPython and was a
/// ValueError here at exit 1. Error messages still quote the ORIGINAL text.
fn ascii_digits(s: &str) -> std::borrow::Cow<'_, str> {
    if s.is_ascii() {
        return std::borrow::Cow::Borrowed(s);
    }
    s.chars()
        .map(|c| {
            let u = c as u32;
            if c.is_ascii() {
                c
            } else if c.is_whitespace() {
                ' '
            } else if let Some(z) = DECIMAL_ZEROS.iter().find(|&&z| u >= z && u < z + 10) {
                (b'0' + (u - z) as u8) as char
            } else {
                '?'
            }
        })
        .collect()
}

pub fn is_exception_name(n: &str) -> bool {
    EXCEPTIONS.iter().any(|e| name_eq(e, n)) || MODULE_EXCEPTIONS.iter().any(|e| name_eq(e, n))
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
    // Bare in an AttributeError — CPython says `type object 'JSONDecodeError'
    // has no attribute ...`, not the dotted spelling `repr` uses. Measured.
    if let Some(e) = MODULE_EXCEPTIONS.iter().find(|e| **e == n) {
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
        // `pathlib.Path` and `pathlib.PosixPath` are still one value here and
        // CPython reprs them differently, so that pair still refuses.
        // `ValueError` left this arm when `JSONDecodeError` stopped being
        // spelled with its name: it is one class under one name again.
        "Path" => None,
        "Counter" => Some("collections.Counter"),
        "JSONDecodeError" => Some("json.decoder.JSONDecodeError"),
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

/// The name CPython would give the class `n` — its `__qualname__`, and its
/// IDENTITY.
///
/// `IOError is EnvironmentError is OSError`: one class under three names, so
/// all three are one object and all three answer `OSError`. Everything else
/// answers itself. Two readers — `ops::get_attr`'s `__name__` and
/// `value::is_same` — because a name that decides what `__name__` prints and a
/// name that decides what `is` answers are the same name, and having them agree
/// by coincidence is how `IOError.__name__` came to say `IOError` while
/// `repr(IOError)` said `<class 'OSError'>` one line away.
pub fn canonical_class(n: &str) -> &str {
    match n {
        "IOError" | "EnvironmentError" => "OSError",
        other => other,
    }
}

pub fn exception_static(n: &str) -> &'static str {
    EXCEPTIONS
        .iter()
        .chain(MODULE_EXCEPTIONS.iter())
        .find(|e| name_eq(e, n))
        .copied()
        .unwrap_or("Exception")
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
///
/// **3.13 rewrote this sentence for every name that reaches here**, so both
/// wordings are live across the supported range. Measured with
/// `sorted([3, 1], strict_mode=True)` and eleven siblings on 2026-09-12:
///
///   3.9 … 3.12   'strict_mode' is an invalid keyword argument for sort()
///   3.13         sort() got an unexpected keyword argument 'strict_mode'
///
/// CPython changed the SENTENCE, not the names, so the branch belongs here and
/// at the four sites that spell the same sentence for themselves (`print`,
/// `int`, `round`, `zip`). **`enumerate` is the exception and must not get it**:
/// measured the same day, `enumerate([1], bogus=1)` says the older form on 3.13
/// too, so its site is left alone.
fn reject_unknown_kw(func: &str, kw: &[(Rc<str>, Value)], allowed: &[&str]) -> R<()> {
    for (k, _) in kw {
        if !allowed.contains(&k.as_ref()) {
            return Err(bad_kw(func, k));
        }
    }
    Ok(())
}

/// The sentence itself, in one place, so the five sites that raise it cannot
/// drift apart across a version boundary the way they would as five literals.
pub(crate) fn bad_kw(func: &str, k: &str) -> LypningError {
    type_err(if REF_PY_MINOR >= 13 {
        format!("{func}() got an unexpected keyword argument '{k}'")
    } else {
        format!("'{k}' is an invalid keyword argument for {func}()")
    })
}

/// How `int()` and `float()` name the numbers they accept. CPython 3.10
/// inserted "real" into both sentences at once, so both sites read it from
/// here. Measured on 2026-09-12 with `int([])` and `float([])`:
///
///   3.9            int() argument must be a string, a bytes-like object or A NUMBER
///   3.10 .. 3.13   … or A REAL NUMBER
///
/// The rest of each sentence is fixed on all five, and `int()`'s names a
/// bytes-like object this subset converts before it can reach the message —
/// CPython's wording is still CPython's wording, and the engine's job is to
/// repeat it, not to describe itself.
fn real_number() -> &'static str {
    if REF_PY_MINOR >= 10 {
        "a real number"
    } else {
        "a number"
    }
}

pub fn key_arg(kw: &[(Rc<str>, Value)], name: &str) -> Option<Value> {
    match kwget(kw, name) {
        Some(Value::None) | None => None,
        Some(v) => Some(v),
    }
}

/// CPython builds differ between index conversion and truth testing here.
/// The build-time oracle probe chooses the contract, including reverse=None.
pub fn reverse_arg(kw: &[(Rc<str>, Value)]) -> R<bool> {
    match kwget(kw, "reverse") {
        None => Ok(false),
        Some(v) if crate::err::REF_REVERSE_TRUTH => truthy(&v),
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
    // `reversed(sequence=xs)` is `reversed() takes no keyword arguments`;
    // without this entry it fell through to `arg1`'s missing-argument text.
    "reversed", "tuple",
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
        // `int` and `str` are the two names in this table whose WORDING moved
        // inside the supported range: 3.13 converted both to Argument Clinic
        // and they changed form with it. Measured with `int('10', 16, 0)` and
        // `str(b'x', 'u', 's', 'e')` on 2026-09-12:
        //
        //   3.9 … 3.12   int() takes at most 2 arguments (3 given)   Say::Paren
        //   3.13         int expected at most 2 arguments, got 3     Say::Bare
        //
        // Every other row of this table said the same thing on all five, which
        // is why only these two carry the branch. Their FLOORS are zero, so the
        // ceiling is the only text either of them can reach.
        "int" => (0, 2, if REF_PY_MINOR >= 13 { Say::Bare } else { Say::Paren }),
        "next" | "iter" => (1, 2, Say::Bare),
        "input" => (0, 1, Say::Bare),
        "str" => (0, 3, if REF_PY_MINOR >= 13 { Say::Bare } else { Say::Paren }),
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
            "sys.exit() of an integer past 64 bits",
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
        // CPython's constructor takes exactly five, and five this value cannot
        // keep: `UnicodeDecodeError('x')` is a TypeError there.
        if name == "UnicodeDecodeError" && args.len() != 5 {
            return Err(unicode_decode_arity(args.len()));
        }
        // `[Errno N] …` is how an OS error the ENGINE raised spells itself, and
        // `ops::errno_args` reads `args` back from it. One built here with that
        // text has `args == (text,)` and no errno; refusing it keeps the
        // spelling unambiguous.
        if let Some(Value::Str(s)) = args.first() {
            if s.starts_with("[Errno ") {
                return Err(unsupported(
                    "exception",
                    &format!("{name}() of a message spelled like an OS error's"),
                ));
            }
        }
        // WHAT THIS VALUE CAN CARRY, and therefore what it must refuse.
        //
        // `Value::Exc` is a class name and ONE `Rc<str>`. CPython's exception
        // carries `args`, a tuple of the objects it was constructed from, and
        // three observable things read it back: `e.args`, `repr(e)` (which
        // reprs each arg) and `str(e)` (empty for none, `str(arg)` for one, the
        // repr of the whole tuple for more). A single string is the only shape
        // that survives the round trip, and everything else was answering from
        // a message that had already lost the argument:
        //
        // ```text
        // repr(ValueError(42))     CPython ValueError(42),       here ValueError('42')
        // ValueError(42).args      CPython (42,),                here ('42',)
        // ValueError().args        CPython (),                   here ('',)
        // ValueError('a','b').args CPython ('a', 'b'),           here ('a',)
        // str(ValueError('a','b')) CPython ("a", "b") as a tuple, here a
        // OSError(2,'x')           CPython FileNotFoundError(2, 'x'), here OSError('2')
        // ```
        //
        // Nine wrong answers at exit 0, every one of them reachable without any
        // construct this engine refuses, and none in the corpus — so every gate
        // was green over them. They were found when `type()` stopped refusing
        // exceptions (this date) and two corpus programs that had been ROUTED
        // PAST the defect stopped being routed past it. The refusal that was
        // covering them was covering them by accident and about something else.
        //
        // So the shape refuses, at construction, where the loss happens — the
        // rule `SystemExit` already follows one arm up, for the same reason and
        // in the same words. An exception raised by the ENGINE is untouched:
        // those carry exactly one string by construction, so
        // `except ZeroDivisionError as e: e.args` still answers.
        //
        // `OSError` refuses at two args as well as at a non-string one, and
        // would even if the args were carried: CPython maps the errno onto a
        // SUBCLASS, so `OSError(2, 'x')` IS a `FileNotFoundError` and its
        // `str()` is `[Errno 2] x`. That is a different object, not a different
        // rendering.
        let msg = match args.first() {
            _ if args.len() > 1 => {
                return Err(unsupported(
                    "exception",
                    &format!(
                        "{name}() with {} arguments, whose `args` tuple this value cannot carry",
                        args.len()
                    ),
                ))
            }
            // `str(KeyError('f'))` is `"'f'"`, not `"f"`: KeyError shows the
            // REPR of its key, so that a missing `''` is distinguishable from a
            // missing `' '`. Every site that raises one from a real lookup
            // already stored `repr(key)`; only the constructor stored the plain
            // string, so the two disagreed and `repr()` then quoted the lookup
            // form a second time (`KeyError("'k'")`).
            Some(v) if name == "KeyError" => fmt::repr(v)?,
            // The empty MESSAGE is taken: it is how `raise ValueError`, `next()`'s
            // StopIteration and a bare `assert` — all argument-less, `args ==
            // ()` — are spelled. `ValueError('')` has `args == ('',)` and
            // `repr` `ValueError('')`, so it is the one string this value
            // cannot carry, and it refuses for the reason `ValueError()` does.
            Some(Value::Str(s)) if s.is_empty() => {
                return Err(unsupported(
                    "exception",
                    &format!("{name}(''), which this value cannot tell from {name}()"),
                ))
            }
            Some(Value::Str(s)) => s.to_string(),
            Some(other) => {
                return Err(unsupported(
                    "exception",
                    &format!(
                        "{name}({}), of a non-str argument                          from the message",
                        fmt::repr(other)?
                    ),
                ))
            }
            // `ValueError()` really is representable — `args` is `()` and
            // `str` is `""` — but not by THIS value, which spells it the same
            // way `ValueError("")` is spelled and would answer `('',)` for one
            // of the two. One shape, two meanings, so neither may answer.
            None => {
                return Err(unsupported(
                    "exception",
                    &format!(
                        "{name}() with no arguments, which this value cannot tell from {name}(\"\")"
                    ),
                ))
            }
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
                    return Err(bad_kw("print", k));
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
            let v = arg1(&args)?;
            ival(length(&v)? as i64)
        }
        "repr" => Value::Str(fmt::repr_rc(&arg1(&args)?)?),
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
                return Err(bad_kw("int", k));
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
            if explicit_base && args.first().is_none() {
                return Err(type_err("int() missing string argument"));
            }
            if explicit_base && !matches!(args.first(), Some(Value::Str(_)) | Some(Value::Bytes(_)))
            {
                return Err(type_err("int() can't convert non-string with explicit base"));
            }
            // `int(b'ff', 16)` is 255. The bytes arm used to recurse with
            // `Args::one(str)`, which DROPPED a positional base: `int(b'ff',
            // 16)` raised a base-10 ValueError and `int(hexlify(b'\x01\x02'),
            // 16)` printed 102 at exit 0 where CPython prints 258. A bytes
            // literal is now read as the same text, with the caller's base,
            // and only the message differs: CPython names the BYTES repr.
            // Only ASCII is text to CPython here — it never decodes, so a
            // non-ASCII byte (`b'\xd9\xa1'`, an Arabic-Indic digit in
            // UTF-8) is an invalid literal, not a digit.
            let text;
            let first = match args.first() {
                Some(Value::Bytes(b)) => {
                    if !b.is_ascii() {
                        return Err(value_err(format!(
                            "invalid literal for int() with base {base}: {}",
                            int_bytes_repr(b)
                        )));
                    }
                    text = Value::Str(decode_utf8(b)?.into());
                    Some(&text)
                }
                o => o,
            };
            let lit = |s: &str| -> R<String> {
                match args.first() {
                    Some(Value::Bytes(b)) => Ok(int_bytes_repr(b)),
                    _ => int_literal_repr(s),
                }
            };
            match first {
                None => ival(0),
                Some(Value::Str(s)) => {
                    let norm = ascii_digits(s);
                    let t = norm.trim();
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
                                    lit(s)?
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
                    // ONE sign, and only before the prefix. The sign has been
                    // stripped above, so a second one here is malformed — and
                    // `from_str_radix` would have read it as the sign: `int(
                    // '--12')` printed 12 and `int('0x-1', 16)` printed -1 at
                    // exit 0, where CPython raises ValueError for both.
                    if t2.starts_with(['+', '-'])
                        || !underscores_are_between_digits(t2, base as u32, t2.len() < t.len())
                    {
                        return Err(value_err(format!(
                            "invalid literal for int() with base {reported}: {}",
                            lit(s)?
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
                                lit(s)?
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
                Some(other) => {
                    return Err(type_err(format!(
                        "int() argument must be a string, a bytes-like object or {}, not '{}'",
                        real_number(),
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
            // `float('１２')` is 12.0: CPython reads every Unicode decimal digit
            // (and Unicode whitespace) as its ASCII counterpart first.
            Some(Value::Str(s)) => match parse_float(&ascii_digits(s)) {
                Some(v) => Value::Float(v),
                None => {
                    return Err(value_err(format!(
                        "could not convert string to float: {}",
                        fmt::str_repr(s)?
                    )))
                }
            },
            // `float(b'1.5')`: CPython parses ASCII bytes as it parses a str,
            // and reports a failure with the BYTES' repr.
            Some(b @ Value::Bytes(x)) => {
                match std::str::from_utf8(x).ok().filter(|t| t.is_ascii()).and_then(parse_float) {
                    Some(v) => Value::Float(v),
                    None => {
                        return Err(value_err(format!(
                            "could not convert string to float: {}",
                            fmt::repr(b)?
                        )))
                    }
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
                    "float() argument must be a string or {}, not '{}'",
                    real_number(),
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
                        for (i, pair) in it.iter_collect(other.clone())?.into_iter().enumerate() {
                            let kv = dict_pair(it, pair, i)?;
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
            //
            // Three TypeErrors, in CPython's order, measured on 3.10 through
            // 3.13 on 2026-09-15: the TOTAL count first (`sum([1], start=1,
            // x=2)` is `takes at most 2 arguments (3 given)`, keywords
            // included), then a missing iterable as `takes at least 1
            // positional argument (0 given)` — `sum(iterable=xs)` says this,
            // not a word about the name — and only then an unknown name in
            // `bad_kw`'s version-dependent sentence. The old text here —
            // `takes no keyword arguments (got 'x')` — is a sentence CPython
            // never says of `sum`.
            if args.len() + kw.len() > 2 {
                return Err(type_err(format!(
                    "sum() takes at most 2 arguments ({} given)",
                    args.len() + kw.len()
                )));
            }
            if args.is_empty() {
                return Err(type_err("sum() takes at least 1 positional argument (0 given)"));
            }
            if let Some((k, _)) = kw.iter().find(|(k, _)| k.as_ref() != "start") {
                return Err(bad_kw("sum", k));
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
            // `min()` is a TypeError about the ARGUMENT LIST, not a ValueError
            // about an empty iterable: with no positional at all there is no
            // iterable to be empty, and `default=` does not rescue it either.
            // The empty-sequence arm below answered the wrong exception class,
            // which a program that catches `ValueError` sees as a caught error
            // where CPython propagates.
            if args.is_empty() {
                return Err(type_err(format!("{name} expected at least 1 argument, got 0")));
            }
            // After the count, as `sorted` above: `min(iterable=xs)` names the
            // count, not the keyword.
            reject_unknown_kw(name, &kw, &["key", "default"])?;
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
                    // CPython 3.12 rewrote this message, and both wordings are
                    // live on hosts this package supports. Measured with
                    // `min([])` on 2026-09-12:
                    //
                    //   3.9 3.10 3.11  min() arg is an empty sequence
                    //   3.12 3.13      min() iterable argument is empty
                    //
                    // An empty match set is the NORMAL case for a glob and
                    // `min`/`max` are positions `route.rs` admits, so this text
                    // is reachable from an advertised one on every host.
                    None if REF_PY_MINOR >= 12 => {
                        Err(value_err(format!("{name}() iterable argument is empty")))
                    }
                    None => Err(value_err(format!("{name}() arg is an empty sequence"))),
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
            // CPython counts the positionals before it reads a keyword's name:
            // `sorted(iterable=xs)` is `sorted expected 1 argument, got 0`, not
            // a complaint about `iterable`. The guard ran first here.
            let v = args
                .first()
                .cloned()
                .ok_or_else(|| type_err("sorted expected 1 argument, got 0"))?;
            reject_unknown_kw("sort", &kw, &["key", "reverse"])?;
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
        "abs" => match arg1(&args)? {
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
                return Err(bad_kw("round", k));
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
                (Value::Float(f), None) => ival(float_to_int(round_half_even(*f, 0)?, "round")?),
                (Value::Float(f), Some(n)) => Value::Float(round_half_even(*f, n)?),
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
            let (a, b) = (arg1(&args)?, args.get(1).cloned().unwrap_or(Value::None));
            // `float_divmod` has its own zero-divisor message, `float divmod()`,
            // and this went through `//` first, which says `float floor
            // division by zero`: a different message at the same exit 1, and
            // the program's own exit is never rescued. The integer arm needs
            // nothing — `integer division or modulo by zero` IS divmod's
            // wording there. Found by the Stage 0a replay (ntx-cab30d587088);
            // `zero_div` owns the 3.14 respelling.
            // ...and the TypeError names `divmod()`, not the `//` it was
            // computed through: `divmod('a', 1)` is `unsupported operand
            // type(s) for divmod(): 'str' and 'int'` on 3.10 through 3.13,
            // measured 2026-09-15.
            // Not `as_num`: a WIDE integer is deliberately not a `Num`, and
            // `divmod(2**70, 3)` is exact on `cap-bigint` — `binop` takes it
            // before any `i64` is asked for.
            let numeric = |v: &Value| {
                #[cfg(feature = "cap-re")]
                if matches!(v, Value::ReFlag(_)) {
                    return true;
                }
                matches!(v, Value::Bool(_) | Value::Int(_) | Value::Float(_))
            };
            if !numeric(&a) || !numeric(&b) {
                return Err(type_err(format!(
                    "unsupported operand type(s) for divmod(): '{}' and '{}'",
                    type_name(&a),
                    type_name(&b)
                )));
            }
            let float_arm = matches!(a, Value::Float(_)) || matches!(b, Value::Float(_));
            if float_arm && !truthy(&b)? {
                return Err(zero_div("float divmod()"));
            }
            let q = it.binop(crate::ast::BinOp::FloorDiv, &a, &b)?;
            let r = it.binop(crate::ast::BinOp::Mod, &a, &b)?;
            Value::Tuple(Rc::new(vec![q, r]))
        }
        "any" | "all" => {
            let want_all = name == "all";
            let v = arg1(&args)?;
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
            // Both of Argument Clinic's names are real: `enumerate(iterable=xs,
            // start=1)` is accepted on every CPython this engine targets, and
            // the guard below knew only `start`, so the first spelling died at
            // exit 1 with a TypeError CPython never raises. Found by the Stage
            // 0a replay (ntx-b47b0b10247f).
            if let Some((k, _)) = kw
                .iter()
                .find(|(k, _)| !matches!(k.as_ref(), "start" | "iterable"))
            {
                return Err(type_err(format!(
                    "'{k}' is an invalid keyword argument for enumerate()"
                )));
            }
            // Argument Clinic names the parameter; `arg1` writes the generic
            // `missing 1 required positional argument`, which is a Python-level
            // function's wording and not this one's.
            //
            // Two TypeErrors around that name changed wording at 3.11, and
            // neither is the binder's. Measured on 3.10.20 / 3.11.15 / 3.12.3 /
            // 3.13.13 on 2026-09-15: `enumerate('ab', iterable='cd')` is the
            // ordinary given-twice sentence on 3.10 and `'iterable' is an
            // invalid keyword argument for enumerate()` from 3.11; and
            // `enumerate(start=1)` — the only keyword-only call the guard above
            // lets through without an iterable — is `missing required argument
            // 'iterable' (pos 1)` on 3.10 and `'start' is an invalid keyword
            // argument for enumerate()` from 3.11. A type's `tp_new` reads its
            // keywords through a different Clinic path than a function does,
            // which is why `round(2.5, number=9)` keeps the binder's wording.
            let named = kw.iter().any(|(k, _)| k.as_ref() == "iterable");
            if REF_PY_MINOR >= 11 && named && args.first().is_some() {
                return Err(type_err(
                    "'iterable' is an invalid keyword argument for enumerate()",
                ));
            }
            if REF_PY_MINOR >= 11 && args.is_empty() && !named && !kw.is_empty() {
                return Err(type_err(
                    "'start' is an invalid keyword argument for enumerate()",
                ));
            }
            let v = match crate::args::bind(&args, &kw, 0, "iterable", "enumerate")? {
                Some(v) => v,
                // 3.11 dropped the position from this sentence. Measured with
                // `enumerate()` on 2026-09-12: 3.9 and 3.10 say
                // `… required argument 'iterable' (pos 1)`, 3.11 … 3.13 stop at
                // the name. `round()` and `open()` below KEPT theirs on all
                // five, which is why only this one branches.
                None if REF_PY_MINOR < 11 => {
                    return Err(type_err(
                        "enumerate() missing required argument 'iterable' (pos 1)",
                    ))
                }
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
                // `zip` took no keywords at all before 3.10 (`strict` is what
                // gave it one), and says so in a sentence of its own. Measured
                // with `zip([1], bogus=1)` on 2026-09-12: 3.9 `zip() takes no
                // keyword arguments`; 3.10 … 3.12 the invalid-keyword sentence;
                // 3.13 the unexpected-keyword one.
                if REF_PY_MINOR < 10 {
                    return Err(type_err("zip() takes no keyword arguments"));
                }
                return Err(bad_kw("zip", k));
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
            let v = arg1(&args)?;
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
                let v = arg1(&args)?;
                if !matches!(v, Value::Func(_) | Value::Builtin(_) | Value::Bound(..)) {
                    return Err(type_err(if crate::err::REF_ITER_SHORT {
                        "iter(v, w): v must be callable"
                    } else {
                        "iter(object, sentinel): object must be callable"
                    }));
                }
                return Err(unsupported("builtin", "iter(callable, sentinel)"));
            }
            let v = arg1(&args)?;
            // Before the `IterObj` shortcut below: a hash object wears that
            // shape and is not iterable, so handing it back would answer where
            // CPython raises.
            #[cfg(feature = "cap-hashlib")]
            if crate::hashlib::as_hasher(&v).is_some() {
                return Err(crate::hashlib::not_iterable());
            }
            #[cfg(feature = "cap-random")]
            if crate::randobj::as_random(&v).is_some() {
                return Err(unsupported("random", "iter() of a Random instance"));
            }
            if let Value::IterObj(..) = v {
                return Ok(v);
            }
            // A generator IS its own iterator, so `iter(g)` is `g` — CPython's
            // generator type defines `__iter__` as `return self`. Wrapping it in
            // a fresh `IterObj` below preserved every value it yields and broke
            // the one thing a caller can observe about the wrapper: identity.
            // `print(iter(g) is g)` answered False where CPython says True, at
            // exit 0, which is the silent shape. Found by the corpus fold of
            // 2026-09-13 (py-7ef2ba28fe22).
            if let Value::Gen(_) = v {
                return Ok(v);
            }
            // Same rule, same reason: `iter(f) is f` for a file object.
            if let Value::File(_) = v {
                return Ok(v);
            }
            let inner = it.make_iter(v)?;
            Value::IterObj(Rc::new(RefCell::new(inner)), "iterator")
        }
        "next" => {
            let v = arg1(&args)?;
            let mut i = match &v {
                Value::IterObj(inner, _) => Iter::Shared(inner.clone()),
                Value::Gen(g) => Iter::Gen(g.clone()),
                // A file IS its own iterator in CPython -- `next(f)` is the next
                // line and `for line in f` is the same object advancing -- and
                // this arm was missing, so `next(f)` raised `'TextIOWrapper'
                // object is not an iterator` for a stream `for` iterates
                // happily one line above. The `Iter::Lines` the loop already
                // uses is the same reader. (py-9df101de3e90, py-d0f7eb84ef96)
                Value::File(f) => {
                    // CPython's file iterator reads AHEAD into a buffer, which
                    // is why `f.tell()` after `next(f)` is
                    // `OSError: telling position disabled by next() call` on a
                    // text stream until the iteration ends or the stream is
                    // seeked. This engine cannot reproduce the buffer's exact
                    // boundary, so it records that telling is no longer
                    // meaningful and `tell()` refuses -- one spawn, and never a
                    // position CPython would not have given.
                    if !f.borrow().binary {
                        f.borrow_mut().telling = false;
                    }
                    Iter::Lines(f.clone())
                }
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
            let v = arg1(&args)?;
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
            let n = int_val(&arg1(&args)?)?;
            match u32::try_from(n).ok().and_then(char::from_u32) {
                Some(c) => Value::Str(crate::value::char_str(c)),
                None => return Err(value_err("chr() arg not in range(0x110000)")),
            }
        }
        "hex" | "oct" | "bin" => {
            let v = arg1(&args)?;
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
            let v = arg1(&args)?;
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
            let v = arg1(&args)?;
            // `class_name` is the whole answer and the whole bound: it is the
            // closed set of every class this engine can NAME, so a value whose
            // type is not one of them still refuses. What it buys over the nine
            // hardcoded arms this replaces is every exception — and
            // `type(e).__name__` inside an `except` is the commonest thing a
            // model writes about an error it just caught. Measured over the
            // 1,173 programs in runs/qwen38-baseline-k16: 30 attempts refused
            // here, more than any other row of the `type` kind.
            //
            // Two things make the substitution safe rather than convenient.
            // `Value::Exc` carries the instance's OWN class name
            // (`value::type_name`), so a `JSONDecodeError` answers
            // `JSONDecodeError` and not the `ValueError` it used to be spelled
            // as — see `MODULE_EXCEPTIONS`, without which this arm would have
            // inherited that wrong answer. And `Path` is still not in
            // `class_name`'s reach from an instance: a `Value::Path` types as
            // `PosixPath` in CPython and `Path` here, so it stays refused.
            //
            // What comes back is an ordinary class object, so everything that
            // already decides what one of those does decides here too:
            // `__name__` answers, `repr` refuses for exactly the pair it
            // cannot spell, `is` compares by name, and calling it constructs.
            // No new surface, which is why this costs 96 bytes.
            // `type_name` is already the `&'static str` the tables compare
            // against, so the class object is that name and nothing has to be
            // looked up twice — `class_name` is asked only WHETHER this engine
            // can name the class, never what to call it.
            let tn = type_name(&v);
            match class_name(tn) {
                Some(_) if tn != "Path" => Value::Builtin(tn),
                _ => {
                    return Err(unsupported(
                        "type",
                        &format!("type() of a {tn}"),
                    ))
                }
            }
        }
        "isinstance" => {
            let v = arg1(&args)?;
            let cls = args
                .get(1)
                .cloned()
                .ok_or_else(|| type_err("isinstance expected 2 arguments, got 1"))?;
            // A class a capability holds as a MODULE ATTRIBUTE — `csv.DictReader`,
            // `itertools.product` — is a `Value::Bound`, not the `Value::Builtin`
            // the arms below compare, so it fell to `arg 2 must be a type, not
            // type`: a TypeError at exit 1 where CPython answers True or False.
            // The itertools classes are answered: their instances are
            // `IterObj`s whose kind IS the class's tp_name, so the name compare
            // below is exact. `csv.DictReader` still refuses.
            let class = |c: &Value| -> Option<&'static str> {
                match c {
                    Value::Builtin(b) => Some(*b),
                    #[cfg(feature = "cap-itertools")]
                    Value::Bound(m, _) if matches!(**m, Value::Module("itertools")) => {
                        match callable_kind(c) {
                            Some(crate::value::Callable::Class(cls)) => Some(cls),
                            _ => None,
                        }
                    }
                    _ => None,
                }
            };
            #[cfg(feature = "cap-csv")]
            {
                let held = |c: &Value| {
                    class(c).is_none()
                        && matches!(c, Value::Bound(..))
                        && matches!(callable_kind(c), Some(crate::value::Callable::Class(_)))
                };
                let hit = match &cls {
                    Value::Tuple(t) => t.iter().any(held),
                    c => held(c),
                };
                if hit {
                    return Err(unsupported(
                        "isinstance",
                        "isinstance() against a class a capability module holds as an attribute",
                    ));
                }
            }
            // `isinstance(1, 3)`: the wording is 3.10's (unions); 3.9 has no
            // union to name (measured on 3.9.6 and 3.11.15 through 3.14.5).
            let not_a_type = || {
                type_err(if REF_PY_MINOR >= 10 {
                    "isinstance() arg 2 must be a type, a tuple of types, or a union"
                } else {
                    "isinstance() arg 2 must be a type or tuple of types"
                })
            };
            // A tuple is tried LEFT TO RIGHT and stops at the first match, so
            // `isinstance(1, (int, 3))` is True and `(str, 3)` the TypeError:
            // the names up to the first non-class are what is compared, and
            // the non-class raises only if none of them matched.
            let mut bad = false;
            // `&'static str`, not `String`: these come out of `Value::Builtin`,
            // which already interns them, and building a `String` per class was
            // an allocation for a comparison.
            let names: Vec<&'static str> = match &cls {
                Value::Tuple(t) => {
                    let mut out = Vec::new();
                    for c in t.iter() {
                        match class(c) {
                            Some(n) => out.push(n),
                            None => {
                                // A nested tuple is legal in CPython and not
                                // walked here.
                                if matches!(c, Value::Tuple(_)) {
                                    return Err(unsupported(
                                        "isinstance",
                                        "isinstance() against a nested tuple of classes",
                                    ));
                                }
                                bad = true;
                                break;
                            }
                        }
                    }
                    out
                }
                c => match class(c) {
                    Some(n) => vec![n],
                    None => return Err(not_a_type()),
                },
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
            let hit = names.iter().any(|n| {
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
            });
            if bad && !hit {
                return Err(not_a_type());
            }
            Value::Bool(hit)
        }
        "open" => {
            // `file` is a keyword too — `open(file='f.txt', mode='w')` — and
            // this read the positional slot alone, so the call died as
            // `missing required argument 'file'` with the file right there.
            let path = match crate::args::bind(&args, &kw, 0, "file", "open")? {
                Some(Value::Str(s)) => s.to_string(),
                // `open(Path('x'))` is `__fspath__`, and `to_str` knows it.
                #[cfg(feature = "cap-pathlib")]
                Some(p @ Value::Path(..)) => fmt::to_str(&p)?,
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
            // `source` is Argument Clinic's name for the first parameter, so
            // `bytes(source='ab', encoding='utf-8')` is legal and `b'ab'`; every
            // arm below read only the positional slot and raised a TypeError
            // CPython never does. Re-seated as the first positional, so the
            // arms keep their one reading of the argument list. Measured on
            // 3.10 through 3.13, 2026-09-15, including the given-twice text.
            let mut kw = kw;
            let seated;
            let args: &Args = match kw.iter().position(|(k, _)| k.as_ref() == "source") {
                Some(i) => {
                    if !args.is_empty() {
                        return Err(type_err(
                            "argument for bytes() given by name ('source') and position (1)",
                        ));
                    }
                    let (_, v) = kw.remove(i);
                    seated = std::iter::once(v).chain(args.iter().cloned()).collect::<Args>();
                    &seated
                }
                None => args,
            };
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
                        // The THIRD argument -- `errors` -- was read by the
                        // parser and thrown away, so this arm always encoded
                        // strictly while `str.encode` on the same two arguments
                        // honoured the handler. `bytes(s, 'ascii', 'ignore')` is
                        // `b'hllo'` in CPython and raised here, at exit 1, which
                        // the dispatcher hands straight back as the program's
                        // own number. One home for the handlers now.
                        // (py-fde666bb0d42)
                        let errors = crate::args::bind(&args, &kw, 2, "errors", "bytes")?;
                        return Ok(Value::Bytes(Rc::new(
                            crate::methods::ascii_encode_errors(s, errors.as_ref())?,
                        )));
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

/// `repr(s)` as CPython prints it inside `invalid literal for int()`.
///
/// The format string is `%.200R`, so the REPR is cut to 200 characters -- which
/// for a long literal takes the closing quote with it, and CPython's message
/// really does end mid-string with no quote and no ellipsis. Printing the whole
/// repr gave a message that grew without bound: 242 characters for a 200-x
/// literal where CPython gives 240, and 252 for a 210-x one where CPython still
/// gives 240. Measured across n = 100, 200, 210, 220 and 5000 on this box's
/// CPython. (py-b00b60452eac)
/// The BYTES spelling of [`int_literal_repr`]: CPython cuts the buffer to 200
/// bytes before taking its repr, then the `%.200R` cut applies on top.
fn int_bytes_repr(b: &[u8]) -> String {
    let r = fmt::bytes_repr(&b[..b.len().min(200)]);
    match r.char_indices().nth(200) {
        Some((cut, _)) => r[..cut].to_string(),
        None => r,
    }
}

fn int_literal_repr(s: &str) -> R<String> {
    let r = fmt::str_repr(s)?;
    Ok(match r.char_indices().nth(200) {
        Some((cut, _)) => r[..cut].to_string(),
        None => r,
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
            // 3.10 started naming the parameter here. Measured with
            // `str(1, 0)` and `bytes(2, 0)` on 2026-09-12: 3.9 says
            // `str() argument 2 must be str, not int`, 3.10 … 3.13 say
            // `str() argument 'encoding' must be str, not int`.
            let which: &str = if REF_PY_MINOR >= 10 { "'encoding'" } else { "2" };
            return Some(type_err(format!(
                "{who}() argument {which} must be str, not {}",
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

/// The first argument, or [`bind_refused`]: the arity tables answer a bare
/// `len()` before this is reached, and no text built here would be CPython's.
fn arg1(args: &[Value]) -> R<Value> {
    args.first()
        .cloned()
        .ok_or_else(bind_refused)
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
fn round_half_even(f: f64, ndigits: i64) -> R<f64> {
    if !f.is_finite() {
        return Ok(f);
    }
    if ndigits == 0 {
        let r = f.round();
        return Ok(if (f - f.trunc()).abs() == 0.5 && r % 2.0 != 0.0 {
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
        });
    }
    // Formatting already rounds half-to-even on the exact value, so reuse it
    // rather than scaling by a power of ten (which introduces its own error).
    if (0..=17).contains(&ndigits) {
        let s = format!("{:.*}", ndigits as usize, f);
        return Ok(s.parse().unwrap_or(f));
    }
    if ndigits > 17 {
        return Ok(f);
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
    if (q - q.trunc()).abs() == 0.5 {
        // THE QUOTIENT IS NOT THE VALUE. CPython rounds the exact decimal value
        // of the double; this divides first, and the division is only correctly
        // ROUNDED. `2.5e25 / 1e25` is exactly 2.5 and looks like a tie, while
        // the double written `2.5e25` is 25000000000000000905969664 — above the
        // halfway point, so CPython answers 3e+25 and the tie rule applied here
        // answered 2e+25. A silent wrong answer, found on-policy.
        //
        // Below 2**53 the tie test is sound and the correction below is right:
        // the only double within half an ulp of `2.5 * 10**k` there IS
        // `2.5 * 10**k`, so an apparent tie is a real one. At or above 2**53 the
        // spacing of doubles exceeds what the test can resolve and this engine
        // cannot tell a tie from a near-miss without the exact expansion — so it
        // refuses, which is always acceptable, rather than guessing, which is
        // not (invariant 1).
        if f.abs() >= 9_007_199_254_740_992.0 {
            return Err(unsupported(
                "round",
                "round() of a float past 2**53 at an apparent halfway point, \
                 where the tie cannot be told from a near-miss without the \
                 exact decimal expansion",
            ));
        }
        if r % 2.0 != 0.0 {
            r -= q.signum();
        }
    }
    // And the same zero-sign restoration as above: `round(-5.0, -1)` is `-0.0`,
    // which `repr` shows, and the correction above produces a positive zero.
    if r == 0.0 {
        return Ok((0.0f64).copysign(f) * scale);
    }
    Ok(r * scale)
}

/// `raise UnicodeDecodeError` / `UnicodeDecodeError('x')`: CPython's arity
/// TypeError, word for word.
pub fn unicode_decode_arity(n: usize) -> LypningError {
    type_err(format!("function takes exactly 5 arguments ({n} given)"))
}

/// `float(str)`'s parse, shared with `float(bytes)`.
fn parse_float(s: &str) -> Option<f64> {
    let t = s.trim();
    let lower = t.to_ascii_lowercase();
    match lower.as_str() {
        "inf" | "+inf" | "infinity" | "+infinity" => Some(f64::INFINITY),
        "-inf" | "-infinity" => Some(f64::NEG_INFINITY),
        "nan" | "+nan" => Some(f64::NAN),
        // The sign bit survives: `math.copysign(1.0, float('-nan'))` is -1.0
        // in CPython, and was 1.0 here.
        "-nan" => Some(-f64::NAN),
        // Same underscore rule as `int()`: between digits only, so
        // `float('1_')` is a ValueError and not 1.0. Checked on the
        // sign-stripped body, since `float('-1_0')` is fine.
        _ if !underscores_are_between_digits(t.strip_prefix(['-', '+']).unwrap_or(t), 10, false) => {
            None
        }
        _ => t.replace('_', "").parse::<f64>().ok(),
    }
}

/// Element `i` of a `dict(seq)` / `d.update(seq)` sequence, as its two items,
/// with `dict_merge`'s own errors (CPython 3.9-3.14, measured on 3.9.6,
/// 3.11.15, 3.12.13, 3.13.13 and 3.14.5): `#i has length n; 2 is required`,
/// and for an element that is not iterable at all `cannot convert ... #i to a
/// sequence` — which 3.14 rewords to `object is not iterable` with that
/// sentence as a NOTE, a traceback line no exception here carries: refused
/// there.
pub fn dict_pair(it: &mut Interp, pair: Value, i: usize) -> R<Vec<Value>> {
    let kv = match &pair {
        Value::List(_) | Value::Tuple(_) => it.iter_collect(pair)?,
        _ => {
            let mut iter = match it.make_iter(pair) {
                Ok(x) => x,
                Err(e) if matches!(e.kind(), ErrKind::Exc(x) if x.kind == "TypeError") => {
                    if REF_PY_MINOR >= 14 {
                        return Err(unsupported(
                            "exception-note",
                            "a dict update element that is not a sequence, which CPython 3.14 annotates",
                        ));
                    }
                    return Err(type_err(format!(
                        "cannot convert dictionary update sequence element #{i} to a sequence"
                    )));
                }
                Err(e) => return Err(e),
            };
            let mut out = Vec::new();
            while let Some(x) = it.iter_next(&mut iter)? {
                out.push(x);
            }
            out
        }
    };
    if kv.len() != 2 {
        return Err(value_err(format!(
            "dictionary update sequence element #{i} has length {}; 2 is required",
            kv.len()
        )));
    }
    Ok(kv)
}
