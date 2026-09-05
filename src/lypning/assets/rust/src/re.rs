//! `re` — the module and its MATCHER, which is the whole of the `cap-re`
//! capability: compiled into `lypning-l` and into nothing smaller. Every line
//! of this file, and every line that reaches it, is behind
//! `cfg(feature = "cap-re")`, so the `lypning` binary does not move a byte for
//! it.
//!
//! What is here: the module and its flag constants as a value of their own
//! (`Value::ReFlag`) with CPython's repr; `re.escape` and `re.purge`; and a
//! backtracking regular-expression engine — parser, compiler and machine —
//! serving `search`, `match`, `fullmatch`, `findall`, `finditer`, `compile`,
//! `sub`, `subn` and `split` on the module and on `re.Pattern`, over the slice
//! of the pattern language the corpus uses.
//!
//! **The slice, and why it stops where it does.** Literals, escaped
//! metacharacters, control and numeric escapes, `.`, classes with ranges and
//! negation, `\d \w \s` and their uppercase forms in and out of classes,
//! `^ $ \A \Z \b \B`, `* + ? {m,n}` greedy and lazy, capturing and `(?:)`
//! groups, alternation, the flags `I M S X A U` as arguments and as a leading
//! `(?imsxau)`, and `sub`'s template language. Named groups, `(?P=name)`,
//! `\1` backreferences, lookahead and lookbehind, scoped `(?i:…)`, atomic
//! groups, possessive quantifiers, conditionals, `\N{…}`, `\z` and bytes
//! patterns each REFUSE, by name, so that `conformance --plan` ranks them one
//! construct at a time and the next slice is measured rather than guessed.
//!
//! **The refusals are the design, not the leftovers**, and three of them are
//! the reason this engine is 65 KB rather than 200:
//!
//!   * **No Unicode tables ship.** `\w \d \s \b \B` and `re.IGNORECASE`
//!     are ASCII-exact and refuse the moment the pattern or the searched slice
//!     of the subject holds a code point above U+007F; `re.A` makes them
//!     ASCII-only, so it is exact everywhere and never refuses. Literals,
//!     ranges and negated classes compare code points and are exact on any
//!     text — `re.sub('l', 'L', 'héllo')` and `[é-ê]` ANSWER. The general
//!     categories, the simple-fold special sets (σ/ς/Σ, k/K/K, s/S/ſ) and the
//!     `\N{…}` names are exactly the bytes that would put this capability at
//!     the density `csv` and `hashlib` were rejected for, and each is a
//!     MISMATCH factory if approximated.
//!   * **Every `re.error` is CPython's to print.** Its message text and its
//!     position moved between 3.11, 3.12, 3.13 and 3.14, and the class was
//!     renamed in 3.13. So a pattern CPython rejects is a refusal naming the
//!     CATEGORY, and the program gets the real message one spawn later.
//!   * **The step budget refuses; it never answers `None`.** CPython is
//!     exponential on `(a+)+$` against 30 a's and a b — measured 3.14.5 on this
//!     host at 19 s — and so is any backtracker. A budget that answered "no
//!     match" when it ran out would be a wrong answer at exit 0; this one is an
//!     `Err` propagated out of the scan loop, so it can never surface as a
//!     `None`, a short `findall` or a half-substituted string.
//!
//! **Static beats runtime, and the router does the pattern parse.** A pattern
//! LITERAL that this engine cannot compile is the program's blocker in
//! `route::re_pattern_block`, decided by a walk before anything runs — the same
//! refusal `re.compile` would raise one in-process run later. The move is not
//! cosmetic: a runtime refusal that lands after a side effect the commit
//! barrier has already let through (`os.makedirs` before `re.sub`) cannot fall
//! onward, so it becomes exit 1, the program's own exit, which the chain never
//! retries. A pattern BUILT at runtime has nothing for a walk to compile and
//! keeps the runtime refusal, which is what the backstop is for.
//!
//! **The traps, each measured on CPython 3.11.15, 3.12.13, 3.13.13 and 3.14.5
//! before the code below was written:**
//!
//!   1. **An `IntFlag` is an int.** `re.I == 2`, `re.I + 1`, `{re.I: 1}[2]`,
//!      `sorted([re.M, re.I])`, `'ab' * re.I`, `[0, 1, 2][re.I]` and
//!      `isinstance(re.I, int)` all answer as for the int — and every
//!      arithmetic operator returns a PLAIN int (`-re.I` is `-2`, not a flag).
//!      Only `| & ^` stay in the flag type. A `Value::Int(2)` would get all of
//!      that right and then print `2` for `re.I` at exit 0, the wrong answer
//!      this capability exists to avoid — so the flag is its own variant, and
//!      one arm in `value::as_num` is what makes the int half fall out of the
//!      numeric paths that already exist rather than out of a second set.
//!   2. **`str()` of a flag is its repr.** `RegexFlag.__str__` is
//!      `object.__str__`, so `print(re.I)`, `f'{re.I}'`, `'%s' % re.I` and
//!      `format(re.I)` all print `re.IGNORECASE`, on every CPython from 3.9
//!      to 3.14. A NON-empty spec moved: `IntFlag` became a `ReprEnum` in
//!      3.11, so `f'{re.I:>5}'` is `'    2'` (the int's) from 3.11 on and
//!      `' re.IGNORECASE'`-shaped (the repr's) before, and `format(re.I, 'd')`
//!      is `'2'` from 3.11 on and a ValueError before. Refused. The `%`
//!      operator's `s` and `r` conversions never touch `__format__` — they
//!      are `str()` and `repr()`, then padding — so `'%s' % re.I`,
//!      `'%-20s' % re.I` and `'%r' % re.I` answer, exactly, on every version;
//!      its numeric conversions (`%d`, `%x`, `%c`, …) are refused with the
//!      specs.
//!   3. **The repr's member order is DECLARATION order, not bit order.**
//!      `re.I | re.A` is `re.ASCII|re.IGNORECASE`: `RegexFlag` declares ASCII
//!      (256) first, and `Flag` iterates by declaration when the values are
//!      not ascending. [`FLAG_NAMES`] is in that order and a unit test pins
//!      it. Bits above the named ones print as one hex residue after the
//!      names: `re.I | 512` is `re.IGNORECASE|0x200` — but a value with NO
//!      named member is not `0x200`: `Flag._missing_` leaves its `_name_`
//!      unset and `global_flag_repr` falls back to the constructor form,
//!      `re.RegexFlag(512)`, decimal. Every CPython from 3.11 to 3.14 agrees
//!      on both spellings; the grid caught the second one.
//!   4. **Bit 0x1 is the TEMPLATE bit** — `re.TEMPLATE` on 3.11 and 3.12,
//!      `0x1` on 3.13+. It is never produced here (`re.T` and `re.TEMPLATE`
//!      refuse as module attributes) and a value that carries it — `re.I | 1`,
//!      `re.I | True` — refuses at repr rather than pick a CPython. `~re.I`
//!      is the same story on every bit at once, and refuses too.
//!   5. **`Flag` is a container.** `len(re.I)`, `list(re.I | re.M)`,
//!      `re.I in re.I | re.M`, `re.I.name` and `re.I.value` are all answered
//!      by CPython and would be TypeErrors or AttributeErrors here — exit 1,
//!      the program's own, which the chain never retries. Each is a refusal.
//!
//! **A refusal is never a bug (invariant 1); an exit 1 where CPython answers
//! is** — and that is the defect four capabilities in a row shipped: a new
//! `Value` variant reached through a path that had no arm for it. So every path
//! a value can take — `==`, `is`, hashing, ordering, `str`/`repr`, `bool`,
//! `int`, `float`, `len`, `iter`, `bytes`, indexing, slicing, `in`,
//! `json.dumps`, a format spec, attribute access, every operator — has an arm
//! for `Value::ReFlag`, `Value::Pattern` and `Value::Match`, and each is exact
//! or a refusal, never a fall-through.

use crate::args::Args;
use crate::ast::BinOp;
use crate::err::{unsupported, LypningError, R};
use crate::eval::Interp;
use crate::value::{type_name, Value};
use std::rc::Rc;

/// The flag bits, as `sre_constants` spells them. TEMPLATE (1) is never
/// produced; see trap 4.
pub const I: u32 = 2;
pub const L: u32 = 4;
pub const M: u32 = 8;
pub const S: u32 = 16;
pub const U: u32 = 32;
pub const X: u32 = 64;
pub const DEBUG: u32 = 128;
pub const A: u32 = 256;

/// Module functions, bound as `Value::Bound(Module("re"), name)`. Sorted,
/// because [`module_attr`] binary-searches it (`methods.rs` says why, and
/// `tests/test_method_tables.py` holds it).
const MODULE_METHODS: &[&str] = &[
    "compile", "escape", "findall", "finditer", "fullmatch", "match", "purge", "search", "split",
    "sub", "subn",
];

/// Flag constants: name -> bits. Sorted by name; binary-searched.
const FLAGS: &[(&str, u32)] = &[
    ("A", A), ("ASCII", A), ("DEBUG", DEBUG), ("DOTALL", S), ("I", I), ("IGNORECASE", I),
    ("L", L), ("LOCALE", L), ("M", M), ("MULTILINE", M), ("NOFLAG", 0), ("S", S), ("U", U),
    ("UNICODE", U), ("VERBOSE", X), ("X", X),
];

/// The Match/Pattern names the router admits for a program that imports `re`
/// (`route::re_method`). Sorted; binary-searched. Exactly the names this engine
/// SERVES: `groupindex`, `scanner`, `expand`, `groupdict`, `lastindex`,
/// `lastgroup` and `regs` are deliberately absent, because a shape the engine
/// does not answer is cheaper as a static block — the program goes straight to
/// CPython — than as a runtime refusal, which costs an in-process run first and
/// can land after a side effect the commit barrier has already let through.
const ROUTED_METHODS: &[&str] = &[
    "end", "endpos", "findall", "finditer", "flags", "fullmatch", "group", "groups", "match",
    "pattern", "pos", "re", "search", "span", "split", "start", "string", "sub", "subn",
];

/// Bit -> name, in `RegexFlag`'s DECLARATION order, which is the order the
/// repr joins them in (trap 3). TEMPLATE (1) is deliberately absent.
const FLAG_NAMES: &[(u32, &str)] = &[
    (A, "ASCII"), (I, "IGNORECASE"), (L, "LOCALE"), (U, "UNICODE"), (M, "MULTILINE"),
    (S, "DOTALL"), (X, "VERBOSE"), (DEBUG, "DEBUG"),
];

/// The bits a name can spell: everything below 0x200 except the TEMPLATE bit.
const NAMED_BITS: u32 = 0x1FE;

pub fn refuse(what: &str) -> LypningError {
    unsupported("re", what)
}

/// An attribute on a flag — `.name`, `.value`, `.bit_length`, `.real` — every
/// one of which CPython answers and none of which this engine does.
pub fn attr_refused(name: &str) -> LypningError {
    refuse(&format!("RegexFlag.{name}"))
}

/// A non-empty format spec on a flag — `format()`, an f-string, or a numeric
/// `%` conversion. Trap 2: CPython 3.11+ formats the int, 3.9 and 3.10 the
/// repr, so no one answer is right on every interpreter an agent may hold.
pub fn spec_refused() -> LypningError {
    refuse("a format spec on a RegexFlag, which CPython 3.11+ applies to the int and 3.9/3.10 to the repr")
}

/// The router's optimistic method union (`route::known_method`) has to admit
/// the Match/Pattern names or a program that imports `re` and calls
/// `m.group()` is blocked before it starts — but only for a program that
/// imports `re`: `.start`, `.end`, `.span` and `.string` are ordinary names
/// elsewhere. See `route::walk_expr`.
pub fn known_method(name: &str) -> bool {
    ROUTED_METHODS.binary_search(&name).is_ok()
}

/// Is `name` one of the module functions that needs the matcher? The ROUTER
/// asks, so that it can compile the pattern of `re.<name>('…', …)` before the
/// program starts (`route::re_pattern_block`).
pub fn is_matcher(name: &str) -> bool {
    MATCHER_FNS.binary_search(&name).is_ok()
}

/// Compile a pattern literal for the WALKER, with the default flags.
///
/// A pattern is decided by its text alone in this slice — the flags an engine
/// still serves cannot make an unservable construct servable, and a flag it
/// does not serve refuses on its own at runtime — so the walk can answer from
/// the literal. `Ok(())` means "this pattern is servable, do not block".
pub fn precompile(src: &str) -> R<()> {
    build(&Rc::from(src), 0).map(|_| ())
}

/// The module functions the matcher backs, sorted.
const MATCHER_FNS: &[&str] = &[
    "compile", "findall", "finditer", "fullmatch", "match", "search", "split", "sub", "subn",
];

/// `re.<name>`: a flag, a bound module function, or — for everything else,
/// `re.error` and `re.Pattern` and `re.TEMPLATE` included — the same
/// `module-attr` refusal every other module raises, which is also what blocks
/// the name STATICALLY in the router (`from re import error` never runs here).
pub fn module_attr(name: &str) -> R<Value> {
    if let Ok(i) = FLAGS.binary_search_by(|(n, _)| (*n).cmp(name)) {
        return Ok(Value::ReFlag(FLAGS[i].1));
    }
    match MODULE_METHODS.binary_search(&name) {
        Ok(i) => Ok(Value::Bound(Rc::new(Value::Module("re")), MODULE_METHODS[i])),
        Err(_) => Err(unsupported("module-attr", &format!("re.{name}"))),
    }
}

/// Every module function. `escape` and `purge` are answered; the nine that
/// need a matcher refuse with a detail that names the function, so the plan
/// can rank them.
pub fn call(it: &mut Interp, name: &str, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    match name {
        "escape" => {
            if !kw.is_empty() {
                return Err(refuse("re.escape() with keyword arguments"));
            }
            if args.len() != 1 {
                return Err(refuse(&format!(
                    "re.escape() with {} positional arguments",
                    args.len()
                )));
            }
            match args.first() {
                Some(Value::Str(s)) => Ok(Value::Str(escape(s).into())),
                Some(Value::Bytes(_)) => Err(refuse("bytes pattern or subject (re over bytes)")),
                Some(other) => Err(refuse(&format!(
                    "re.escape() of a {}, which CPython answers with a TypeError",
                    type_name(other)
                ))),
                None => Err(refuse("re.escape() with 0 positional arguments")),
            }
        }
        "purge" => {
            if !args.is_empty() || !kw.is_empty() {
                return Err(refuse("re.purge() with arguments"));
            }
            // CPython's `re.purge()` empties `_cache`, and the next
            // `re.compile` of the same text is then a DIFFERENT object — which
            // `is` can see.
            purge_cache();
            Ok(Value::None)
        }
        other => matcher_call(it, other, args, kw),
    }
}

/// CPython 3.7+'s `_special_chars_map`: exactly these code points get a
/// backslash. `!"%',/:;<=>@_` and the backtick, digits, letters, `\x00` and
/// every non-ASCII character copy through — the pre-3.7 rule escaped every
/// non-alphanumeric and would write `\!` and `\é`.
const fn special_table() -> [bool; 128] {
    let specials = b"()[]{}?*+-|^$\\.&~# \t\n\r\x0b\x0c";
    let mut t = [false; 128];
    let mut i = 0;
    while i < specials.len() {
        t[specials[i] as usize] = true;
        i += 1;
    }
    t
}
const SPECIAL: [bool; 128] = special_table();

/// `re.escape(s)`. No allocation beyond the output.
pub fn escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 8);
    for c in s.chars() {
        if (c as u32) < 128 && SPECIAL[c as usize] {
            out.push('\\');
        }
        out.push(c);
    }
    out
}

/// `repr(flag)`, which is also `str(flag)`: the named members present in
/// declaration order, joined by `|`, then any residue above the named bits as
/// one `0x…`; zero is `re.NOFLAG`; residue with NO named member beside it is
/// the constructor form, `re.RegexFlag(512)`; the TEMPLATE bit refuses
/// (traps 3 and 4).
pub fn flag_repr(bits: u32) -> R<String> {
    if bits == 0 {
        return Ok("re.NOFLAG".into());
    }
    if bits & 1 != 0 {
        return Err(refuse(
            "a RegexFlag with bit 0x1 set, which CPython 3.12 prints as re.TEMPLATE and 3.13+ as 0x1",
        ));
    }
    let mut out = String::new();
    for (bit, name) in FLAG_NAMES {
        if bits & bit != 0 {
            if !out.is_empty() {
                out.push('|');
            }
            out.push_str("re.");
            out.push_str(name);
        }
    }
    let residue = bits & !NAMED_BITS;
    if residue != 0 {
        if out.is_empty() {
            // No member to hang the hex on: `Flag._missing_` sets `_name_` to
            // None and `global_flag_repr` prints `module.Class(value)`.
            return Ok(format!("re.RegexFlag({residue})"));
        }
        out.push('|');
        out.push_str(&format!("0x{residue:x}"));
    }
    Ok(out)
}

/// `| & ^` with a flag on either side, which is the ONLY arithmetic that stays
/// in the flag type. The other side may be a flag, an int in the flag range or
/// a bool (`Flag.__or__` accepts any int); a float, a str or `None` is a
/// TypeError in CPython and a refusal here. Every other operator answers
/// `Ok(None)` and falls to the numeric path through `as_num`, which is where
/// `IntFlag` arithmetic returns a plain int in every CPython.
///
/// One asymmetry, and it is CPython's dispatch rule: an INT on the left still
/// answers a flag (`2 | re.I` is `re.IGNORECASE`), because `RegexFlag` is a
/// subclass of `int` and its reflected `__ror__` is tried first — but a BOOL
/// on the left answers a plain int (`False | re.I` is `2`, `True & re.I` is
/// `0`), because `RegexFlag` is not a subclass of `bool`, so `bool.__or__`
/// runs first and delegates to `int`'s, which never looks at the subclass.
/// Measured on 3.11 through 3.14; found by a differential run.
///
/// Called FIRST in `Interp::binop`, before the numeric fast path — which would
/// otherwise answer `re.I | re.M` as `10`, at exit 0.
pub fn binop(op: BinOp, a: &Value, b: &Value) -> R<Option<Value>> {
    if !matches!(a, Value::ReFlag(_)) && !matches!(b, Value::ReFlag(_)) {
        return Ok(None);
    }
    if !matches!(op, BinOp::BitOr | BinOp::BitAnd | BinOp::BitXor) {
        return Ok(None);
    }
    if let (Value::Bool(x), Value::ReFlag(y)) = (a, b) {
        let (x, y) = (*x as i64, *y as i64);
        return Ok(Some(Value::Int(match op {
            BinOp::BitOr => x | y,
            BinOp::BitAnd => x & y,
            _ => x ^ y,
        })));
    }
    let sym = crate::ops::op_sym(op);
    let bits = |v: &Value| -> R<u32> {
        match v {
            Value::ReFlag(x) => Ok(*x),
            Value::Bool(x) => Ok(*x as u32),
            Value::Int(i) => u32::try_from(*i).map_err(|_| {
                refuse(&format!("{sym} between a RegexFlag and an int outside the flag range"))
            }),
            other => Err(refuse(&format!(
                "{sym} between a RegexFlag and a {}",
                type_name(other)
            ))),
        }
    };
    let (x, y) = (bits(a)?, bits(b)?);
    Ok(Some(Value::ReFlag(match op {
        BinOp::BitOr => x | y,
        BinOp::BitAnd => x & y,
        _ => x ^ y,
    })))
}

/// The flag as the int it is, for the paths that read an operand through
/// `__index__` — `'ab' * re.I`, `[0] * re.M` — and cannot go through
/// `as_num` because their other side is not a number. `None` for anything
/// that is not a flag, so the caller can tell whether a substitution happened.
pub fn as_int(v: &Value) -> Option<Value> {
    match v {
        Value::ReFlag(b) => Some(Value::Int(*b as i64)),
        _ => None,
    }
}


// ===========================================================================
// The matcher
// ===========================================================================
//
// Three stages, in the order CPython has them: `Lib/re/_parser.py` builds a
// tree, `Lib/re/_compiler.py` flattens it into opcodes, and
// `Modules/_sre/sre_lib.h` walks the opcodes with an explicit data stack. This
// is a port of that shape, minus the Unicode tables, with every `raise
// error(...)` turned into a refusal. Porting rather than inventing is the whole
// method: a backtracker written from first principles agrees with `sre` on
// `a+b` and disagrees on `(a*)*`, `(a?){3}`, `re.sub(r'a*', '-', 'baaac')` and
// the leftmost-first rule — one silent wrong answer per rediscovered rule.
//
// **Positions are code points.** The subject is converted to `Vec<char>` once
// per call, so `span()`, `start()`, `end()`, `pos`/`endpos` and every offset
// `finditer` reports are CPython's by construction. A byte-offset engine over
// `&str` answers `re.search('b', 'éb').span()` as `(2, 3)` where CPython says
// `(1, 2)` — at exit 0.
//
// **No Unicode tables ship.** `\w \d \s \b \B \W \D \S` and `re.IGNORECASE`
// are ASCII-exact and REFUSE when the pattern or the searched slice of the
// subject holds a code point above U+007F (`re.A` / `(?a)` makes them
// ASCII-only, so it is exact everywhere and never refuses). Literals, ranges
// and negated classes compare code points and are exact on any text:
// `re.sub('l', 'L', 'héllo')` and `[é-ê]` answer, they do not refuse. The
// tables — general categories, simple folding with its special sets (σ/ς/Σ,
// k/K/K, s/S/ſ), `\N{…}` names — are exactly the bytes that would put this
// capability at the density csv and hashlib were rejected for, and each one is
// a MISMATCH factory if approximated instead.
//
// **The step budget REFUSES; it never answers `None`.** CPython is exponential
// on `(a+)+$` against 30 a's and a b (measured 3.14.5 on this host at 19 s) and
// so is any backtracker. A budget that answered "no match" when it ran out
// would be a wrong answer at exit 0; this one is an `Err` that propagates out
// of the scan loop, so it can never surface as a `None`, a short `findall` or a
// half-substituted string. The commit barrier discards the staged stdout and
// CPython then runs the same search — which is exactly what the user would have
// got without lypning.

use std::cell::{Cell, RefCell};

/// `sre_constants.MAXREPEAT`. `a{4294967295}` is an OverflowError in CPython,
/// so this value is the ceiling and not a legal count.
const MAXREPEAT: u32 = 4294967295;
/// An unset group slot, and an unset `last_ptr`. Not `0`: position 0 is real.
const NOMARK: u32 = u32::MAX;
/// No previous repeat context — the head of the chain.
const NOREP: u32 = u32::MAX;

/// Ops executed plus frames popped, per API call. Chosen against the grid: the
/// linear rows (`(?:a|b)*c` over 400,001 characters, `(a+)+b` and `(.*)*b` and
/// `^(\w+\s?)*$` over 100,000) stay two orders of magnitude below it, and the
/// exponential ones (`(a+)+$`, `(a|aa)*$`, `.*.*.*.*.*.*.*.*x`) reach it in
/// well under a second. A shape CPython answers quickly and this budget refuses
/// is a wasted spawn, never a wrong answer — a bench item, not a correctness
/// one.
const STEP_BUDGET: u64 = 20_000_000;
/// Frames live, per attempt. `(?:a|b)*c` over 400,001 characters needs about
/// 800,000; this is the ceiling that keeps a runaway pattern from taking the
/// machine's memory instead of its time.
const FRAME_CEILING: usize = 2_500_000;

// ---- character classes ----------------------------------------------------

/// Category bits. ASCII-exact: `\d` is `0-9`, `\w` is `[0-9A-Za-z_]`, and `\s`
/// is `\t\n\v\f\r` plus the space AND `\x1c-\x1f`, which `str.isspace()`
/// includes and the `re.A` table does not — the one place the two spellings of
/// `\s` differ inside ASCII.
const C_D: u8 = 1;
const C_ND: u8 = 2;
const C_W: u8 = 4;
const C_NW: u8 = 8;
const C_S: u8 = 16;
const C_NS: u8 = 32;

struct Class {
    negate: bool,
    ranges: Vec<(u32, u32)>,
    cats: u8,
}

fn is_word(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

/// `\s` without `re.A`: `str.isspace()` restricted to ASCII, which is the
/// five C escapes, the space, and the four separators `\x1c-\x1f`.
fn is_space_u(c: char) -> bool {
    matches!(c, '\t' | '\n' | '\x0b' | '\x0c' | '\r' | ' ' | '\x1c'..='\x1f')
}

/// `\s` under `re.A`: `sre`'s ASCII table, which stops at `\r`.
fn is_space_a(c: char) -> bool {
    matches!(c, '\t' | '\n' | '\x0b' | '\x0c' | '\r' | ' ')
}

impl Class {
    fn raw(&self, c: char, ascii: bool) -> bool {
        let u = c as u32;
        for (lo, hi) in &self.ranges {
            if u >= *lo && u <= *hi {
                return true;
            }
        }
        if self.cats == 0 {
            return false;
        }
        let sp = if ascii { is_space_a(c) } else { is_space_u(c) };
        (self.cats & C_D != 0 && c.is_ascii_digit())
            || (self.cats & C_ND != 0 && !c.is_ascii_digit())
            || (self.cats & C_W != 0 && is_word(c))
            || (self.cats & C_NW != 0 && !is_word(c))
            || (self.cats & C_S != 0 && sp)
            || (self.cats & C_NS != 0 && !sp)
    }
}

/// The ASCII case twin of `c`, or `c` itself. `re.IGNORECASE` over non-ASCII
/// refuses before any of this runs, so an ASCII fold is the exact one.
fn swap_ascii(c: char) -> char {
    if c.is_ascii_uppercase() {
        c.to_ascii_lowercase()
    } else if c.is_ascii_lowercase() {
        c.to_ascii_uppercase()
    } else {
        c
    }
}

// ---- the compiled program -------------------------------------------------

#[derive(Clone, Copy, PartialEq, Eq)]
enum At {
    /// `^` without MULTILINE, and `\A`: index 0 of the REAL string, not `pos`
    /// — `re.compile(r'^b').search('ab', 1)` is None.
    Begin,
    BeginLine,
    /// `$`: at `endpos`, or one before it with a `\n` there. `\Z` is [`At::StrEnd`].
    End,
    EndLine,
    StrEnd,
    WordB,
    NotWordB,
}

#[derive(Clone, Copy)]
enum Op {
    Lit(char),
    /// The pattern character, ASCII-lowercased once at compile time.
    LitFold(char),
    /// `.` — everything but `\n`.
    Any,
    /// `.` under DOTALL.
    AnyAll,
    In(u32),
    At(At),
    Mark(u32),
    Jump(u32),
    /// Alternatives tried in order — leftmost-first, never longest. The
    /// targets live in `Pat::branches` so that an `Op` stays `Copy`, which is
    /// what lets the machine read one without holding a borrow of the program
    /// while it mutates its own state.
    Branch { at: u32, n: u32 },
    /// A repeat of a ONE-CHARACTER atom, which is the shape that makes `a*`,
    /// `\w+`, `[^x]*` and `.*` linear: the count is a number to decrement, not
    /// a frame per character. `sre`'s REPEAT_ONE / MIN_REPEAT_ONE. The atom is
    /// at `pc + 1` and the tail at `pc + 2`.
    RepOne {
        min: u32,
        max: u32,
        greedy: bool,
        /// `sre`'s next-literal peek: when the tail begins with a literal,
        /// positions where that literal is not present are skipped without
        /// running the tail at all.
        peek: Option<char>,
        fold_peek: bool,
    },
    /// The general repeat. Body at `pc + 1`, [`Op::Until`] at `until`, tail
    /// after it.
    Repeat {
        min: u32,
        max: u32,
        greedy: bool,
        until: u32,
    },
    /// `sre`'s MAX_UNTIL / MIN_UNTIL, reading the innermost repeat context.
    Until,
    Success,
}

/// A compiled pattern. `source` and `flags` are what `==` and the hash compare,
/// so two `re.compile` calls with the same text and the same effective flags
/// are equal objects — and the compile cache makes them the SAME object, which
/// is what `re.compile('a') is re.compile('a')` needs.
pub struct Pat {
    pub source: Rc<str>,
    /// The EFFECTIVE flags: what was given, plus the inline `(?imsxau)` at the
    /// head of the pattern, plus UNICODE unless ASCII was asked for. This is
    /// what `.flags` reports and what `==` and the hash compare. The flags as
    /// GIVEN are the compile cache's key instead, so `re.compile('a', re.U)`
    /// and `re.compile('a')` are two objects — which `is` can see — and still
    /// compare equal.
    pub flags: u32,
    pub groups: u32,
    code: Vec<Op>,
    branches: Vec<u32>,
    classes: Vec<Class>,
    /// The pattern uses a table class, `\b` or `\B`: exact over ASCII, a
    /// refusal over anything else.
    has_table: bool,
    /// IGNORECASE is on without ASCII: an ASCII fold, refused over anything else.
    folds: bool,
    /// `\B` is present, so an EMPTY searched range refuses — CPython 3.12 says
    /// no match there and 3.14 says a match, and neither is safe to pick.
    has_nwb: bool,
    src_ascii: bool,
}

impl Pat {
    fn ascii_flag(&self) -> bool {
        self.flags & A != 0
    }
}

// ---- refusal spelling -----------------------------------------------------

/// The pattern text inside a refusal detail, escaped and truncated.
///
/// A refusal is ONE line on stderr (invariant 2), so a pattern with a newline
/// in it cannot be pasted in raw — and `fmt::str_repr` is not usable either,
/// because it refuses on code points whose printability is CPython's table.
fn show(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('\'');
    for (n, c) in s.chars().enumerate() {
        if n == 60 {
            out.push('…');
            break;
        }
        // Only what would BREAK the one-line contract is escaped. A backslash
        // is left alone on purpose: `pattern '\d+'` is the text the user typed
        // and the row `--plan` groups by, and `pattern '\\d+'` is a second
        // thing to decode before reading it.
        match c {
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32))
            }
            c => out.push(c),
        }
    }
    out.push('\'');
    out
}

/// A pattern CPython rejects. Its `re.error` carries a message and a position
/// that moved between 3.11, 3.12, 3.13 and 3.14 (and the class was renamed in
/// 3.13), so the text is CPython's to print, one spawn later — this names the
/// category only.
fn bad_pattern(src: &str, category: &str) -> LypningError {
    refuse(&format!(
        "pattern {}, which CPython rejects: {category}",
        show(src)
    ))
}

/// A pattern this engine does not serve yet. Ranked one row per construct by
/// `conformance --plan`, which is the build order for the next slice.
fn no_construct(src: &str, construct: &str) -> LypningError {
    refuse(&format!("pattern {}: {construct}", show(src)))
}

// ---- the parse tree -------------------------------------------------------

enum Node {
    Empty,
    Lit(char),
    Class(u32),
    Any,
    At(At),
    /// `Some(n)` is the capturing group's 1-based number.
    Group(Option<u32>, Box<Node>),
    Cat(Vec<Node>),
    Alt(Vec<Node>),
    Rep {
        min: u32,
        max: u32,
        greedy: bool,
        body: Box<Node>,
    },
}

impl Node {
    fn is_at(&self) -> bool {
        matches!(self, Node::At(_))
    }
    fn is_rep(&self) -> bool {
        matches!(self, Node::Rep { .. })
    }
}

/// `(?aiLmsux)` at the head of a pattern, and nowhere else: CPython 3.11+
/// raises `global flags not at the start of the expression` for
/// `a(?i)b` and even for `()(?i)`, so a leading run is the whole of it.
/// `(?i)(?m)a` is legal and is two turns of this loop.
fn leading_flags(src: &[char], out: &mut u32) -> R<usize> {
    let mut i = 0;
    loop {
        if src.get(i) != Some(&'(') || src.get(i + 1) != Some(&'?') {
            return Ok(i);
        }
        let mut j = i + 2;
        let mut bits = 0u32;
        while let Some(c) = src.get(j) {
            let b = match c {
                'i' => I,
                'm' => M,
                's' => S,
                'x' => X,
                'a' => A,
                'u' => U,
                'L' => L,
                _ => break,
            };
            bits |= b;
            j += 1;
        }
        if j == i + 2 || src.get(j) != Some(&')') {
            return Ok(i);
        }
        *out |= bits;
        i = j + 1;
    }
}

struct P<'a> {
    s: &'a [char],
    src: &'a str,
    i: usize,
    flags: u32,
    groups: u32,
    classes: Vec<Class>,
    depth: u32,
}

impl<'a> P<'a> {
    fn peek(&self) -> Option<char> {
        self.s.get(self.i).copied()
    }
    fn at(&self, k: usize) -> Option<char> {
        self.s.get(self.i + k).copied()
    }
    fn verbose(&self) -> bool {
        self.flags & X != 0
    }
    fn bad(&self, category: &str) -> LypningError {
        bad_pattern(self.src, category)
    }
    fn no(&self, construct: &str) -> LypningError {
        no_construct(self.src, construct)
    }

    /// VERBOSE skips whitespace and `#` comments — but only HERE, at the top of
    /// the item loop. Not inside `[...]` (which has its own loop), not after a
    /// backslash (the escape reads its own character), and not inside a `{m,n}`
    /// body: `(?x)a \d {2}` quantifies and `(?x)a \d { 2 }` is five literals.
    fn skip_x(&mut self) {
        if !self.verbose() {
            return;
        }
        loop {
            match self.peek() {
                Some(' ') | Some('\t') | Some('\n') | Some('\r') | Some('\x0b')
                | Some('\x0c') => self.i += 1,
                Some('#') => {
                    while let Some(c) = self.peek() {
                        self.i += 1;
                        if c == '\n' {
                            break;
                        }
                    }
                }
                _ => return,
            }
        }
    }

    fn alt(&mut self) -> R<Node> {
        let mut branches = vec![self.cat()?];
        while self.peek() == Some('|') {
            self.i += 1;
            branches.push(self.cat()?);
        }
        Ok(if branches.len() == 1 {
            branches.pop().unwrap()
        } else {
            Node::Alt(branches)
        })
    }

    fn cat(&mut self) -> R<Node> {
        let mut items: Vec<Node> = Vec::new();
        loop {
            self.skip_x();
            let c = match self.peek() {
                None | Some('|') | Some(')') => break,
                Some(c) => c,
            };
            if let Some((min, max)) = self.quantifier(c)? {
                let greedy = match self.peek() {
                    Some('?') => {
                        self.i += 1;
                        false
                    }
                    // `a*+`, `a++`, `a?+`, `a{m,n}+` — 3.11's possessive forms.
                    Some('+') => return Err(self.no("possessive quantifier")),
                    _ => true,
                };
                let last = items.last();
                if last.is_none() || last.unwrap().is_at() {
                    return Err(self.bad("nothing to repeat"));
                }
                if last.unwrap().is_rep() {
                    return Err(self.bad("multiple repeat"));
                }
                let body = items.pop().unwrap();
                items.push(Node::Rep {
                    min,
                    max,
                    greedy,
                    body: Box::new(body),
                });
                continue;
            }
            if let Some(n) = self.atom()? {
                items.push(n);
            }
        }
        Ok(match items.len() {
            0 => Node::Empty,
            1 => items.pop().unwrap(),
            _ => Node::Cat(items),
        })
    }

    /// `*`, `+`, `?`, or a `{...}` whose body is digits and at most one comma.
    /// `a{`, `a{}`, `a{x}`, `a{ 1}` and `a{1,2` are literal braces — the rule
    /// that makes `(?x)a { 2 }` five literals rather than a quantifier.
    fn quantifier(&mut self, c: char) -> R<Option<(u32, u32)>> {
        match c {
            '*' => {
                self.i += 1;
                return Ok(Some((0, MAXREPEAT)));
            }
            '+' => {
                self.i += 1;
                return Ok(Some((1, MAXREPEAT)));
            }
            '?' => {
                self.i += 1;
                return Ok(Some((0, 1)));
            }
            '{' => {}
            _ => return Ok(None),
        }
        if self.at(1) == Some('}') {
            return Ok(None);
        }
        let here = self.i;
        let mut j = self.i + 1;
        let digits = |s: &[char], j: &mut usize| -> Option<u64> {
            let start = *j;
            let mut v: u64 = 0;
            while let Some(c) = s.get(*j) {
                if !c.is_ascii_digit() {
                    break;
                }
                v = v.saturating_mul(10).saturating_add(*c as u64 - '0' as u64);
                *j += 1;
            }
            (*j > start).then_some(v)
        };
        let lo = digits(self.s, &mut j);
        let (hi, comma) = if self.s.get(j) == Some(&',') {
            j += 1;
            (digits(self.s, &mut j), true)
        } else {
            (lo, false)
        };
        if self.s.get(j) != Some(&'}') {
            self.i = here;
            return Ok(None);
        }
        self.i = j + 1;
        let min = lo.unwrap_or(0);
        let max = match hi {
            Some(h) => h,
            None if comma => MAXREPEAT as u64,
            None => 0,
        };
        if min >= MAXREPEAT as u64 || max >= MAXREPEAT as u64 && hi.is_some() {
            return Err(self.bad("the repetition number is too large"));
        }
        if max < min {
            return Err(self.bad("min repeat greater than max repeat"));
        }
        Ok(Some((min as u32, max as u32)))
    }

    /// One item. `Ok(None)` is a `(?#...)` comment, which produces no node.
    fn atom(&mut self) -> R<Option<Node>> {
        let c = self.peek().unwrap();
        Ok(Some(match c {
            '.' => {
                self.i += 1;
                Node::Any
            }
            '^' => {
                self.i += 1;
                Node::At(if self.flags & M != 0 {
                    At::BeginLine
                } else {
                    At::Begin
                })
            }
            '$' => {
                self.i += 1;
                Node::At(if self.flags & M != 0 {
                    At::EndLine
                } else {
                    At::End
                })
            }
            '[' => self.class()?,
            '(' => match self.group()? {
                Some(n) => n,
                None => return Ok(None),
            },
            '\\' => self.escape()?,
            _ => {
                self.i += 1;
                Node::Lit(c)
            }
        }))
    }

    fn group(&mut self) -> R<Option<Node>> {
        self.i += 1; // '('
        if self.peek() != Some('?') {
            self.groups += 1;
            let idx = self.groups;
            let body = self.nested()?;
            if self.peek() != Some(')') {
                return Err(self.bad("missing ), unterminated subpattern"));
            }
            self.i += 1;
            return Ok(Some(Node::Group(Some(idx), Box::new(body))));
        }
        self.i += 1; // '?'
        match self.peek() {
            Some(':') => {
                self.i += 1;
                let body = self.nested()?;
                if self.peek() != Some(')') {
                    return Err(self.bad("missing ), unterminated subpattern"));
                }
                self.i += 1;
                Ok(Some(Node::Group(None, Box::new(body))))
            }
            // `(?#...)` — a comment, in every mode, not just VERBOSE.
            Some('#') => {
                while let Some(c) = self.peek() {
                    self.i += 1;
                    if c == ')' {
                        return Ok(None);
                    }
                }
                Err(self.bad("missing ), unterminated comment"))
            }
            Some('P') => Err(self.no(if self.at(1) == Some('=') {
                "named backreference (?P=name)"
            } else {
                "named group (?P<name>…)"
            })),
            Some('=') => Err(self.no("lookahead (?=…)")),
            Some('!') => Err(self.no("negative lookahead (?!…)")),
            Some('<') => Err(self.no(match self.at(1) {
                Some('=') => "lookbehind (?<=…)",
                Some('!') => "negative lookbehind (?<!…)",
                _ => "the (?<name>…) extension, which CPython rejects",
            })),
            Some('>') => Err(self.no("atomic group (?>…)")),
            Some('(') => Err(self.no("conditional group (?(id)…)")),
            Some(c) if c.is_ascii_alphabetic() || c == '-' => {
                // A scoped `(?i:…)`, a `(?-i)`, or a global flag past the
                // start of the pattern — CPython raises for the last one.
                let mut j = self.i;
                while matches!(self.s.get(j), Some(c) if c.is_ascii_alphabetic() || *c == '-') {
                    j += 1;
                }
                if self.s.get(j) == Some(&':') {
                    Err(self.no("scoped inline flags (?flags:…)"))
                } else {
                    Err(self.bad("global flags not at the start of the expression"))
                }
            }
            _ => Err(self.bad("unknown extension")),
        }
    }

    fn nested(&mut self) -> R<Node> {
        self.depth += 1;
        if self.depth > 120 {
            return Err(self.bad("too deeply nested"));
        }
        let n = self.alt()?;
        self.depth -= 1;
        Ok(n)
    }

    fn cls(&mut self, c: Class) -> Node {
        self.classes.push(c);
        Node::Class(self.classes.len() as u32 - 1)
    }

    /// A single-category shorthand outside a class: `\d` and friends.
    fn cat_node(&mut self, cats: u8) -> Node {
        self.cls(Class {
            negate: false,
            ranges: Vec::new(),
            cats,
        })
    }
}

impl<'a> P<'a> {
    /// A `\` escape outside a character class. Returns a node.
    ///
    /// `sre`'s three-way split, and every branch of it is a trap: a shorthand
    /// or an anchor; a numeric escape, where `\0` and three octal digits are a
    /// character and `\1`..`\99` are a GROUP REFERENCE (`\101` is `'A'` but
    /// `\11` is group 11); or a literal — but only when the character after the
    /// backslash is not an ASCII letter, because `\e`, `\q` and `\p` are
    /// `re.error` in CPython while `\-`, `\_`, `\ ` and `\é` are the character.
    fn escape(&mut self) -> R<Node> {
        self.i += 1;
        let Some(c) = self.peek() else {
            return Err(self.bad("bad escape (end of pattern)"));
        };
        self.i += 1;
        Ok(match c {
            'd' => self.cat_node(C_D),
            'D' => self.cat_node(C_ND),
            'w' => self.cat_node(C_W),
            'W' => self.cat_node(C_NW),
            's' => self.cat_node(C_S),
            'S' => self.cat_node(C_NS),
            'b' => Node::At(At::WordB),
            'B' => Node::At(At::NotWordB),
            'A' => Node::At(At::Begin),
            'Z' => Node::At(At::StrEnd),
            // New in 3.14 and `bad escape \z` on 3.13 and earlier: no answer
            // is right on every interpreter an agent may be holding.
            'z' => return Err(self.no("\\z, which is an anchor in CPython 3.14 and an error before it")),
            'a' => Node::Lit('\x07'),
            'f' => Node::Lit('\x0c'),
            'n' => Node::Lit('\n'),
            'r' => Node::Lit('\r'),
            't' => Node::Lit('\t'),
            'v' => Node::Lit('\x0b'),
            'N' => return Err(self.no("\\N{…}, which needs the Unicode name tables")),
            'x' => Node::Lit(self.hex(2, "incomplete escape \\x")?),
            'u' => Node::Lit(self.hex(4, "incomplete escape \\u")?),
            'U' => Node::Lit(self.hex(8, "incomplete escape \\U")?),
            '0' => {
                // `\0`, `\0d`, `\0dd` — octal, up to two more digits.
                let mut v = 0u32;
                for _ in 0..2 {
                    match self.peek() {
                        Some(d) if ('0'..'8').contains(&d) => {
                            v = v * 8 + (d as u32 - '0' as u32);
                            self.i += 1;
                        }
                        _ => break,
                    }
                }
                Node::Lit(char::from_u32(v).unwrap_or('\0'))
            }
            '1'..='9' => {
                // Octal *or* a decimal group reference, and `sre` decides by
                // looking one further: three octal digits make a character,
                // anything else is a group.
                let mut digits = String::new();
                digits.push(c);
                if matches!(self.peek(), Some(d) if d.is_ascii_digit()) {
                    let d2 = self.peek().unwrap();
                    digits.push(d2);
                    self.i += 1;
                    let oct = |x: char| ('0'..'8').contains(&x);
                    if oct(c) && oct(d2) && matches!(self.peek(), Some(d3) if oct(d3)) {
                        let d3 = self.peek().unwrap();
                        self.i += 1;
                        let v = u32::from_str_radix(&format!("{c}{d2}{d3}"), 8).unwrap_or(0);
                        if v > 0o377 {
                            return Err(self.bad("octal escape value outside of range 0-0o377"));
                        }
                        return Ok(Node::Lit(char::from_u32(v).unwrap_or('\0')));
                    }
                }
                let n: u32 = digits.parse().unwrap_or(0);
                if n > self.groups {
                    return Err(self.bad("invalid group reference"));
                }
                return Err(self.no("backreference \\1..\\99"));
            }
            c if c.is_ascii_alphabetic() => {
                return Err(self.bad(&format!("bad escape \\{c}")))
            }
            c => Node::Lit(c),
        })
    }

    fn hex(&mut self, n: usize, what: &str) -> R<char> {
        let mut v = 0u32;
        for _ in 0..n {
            match self.peek() {
                Some(d) if d.is_ascii_hexdigit() => {
                    v = v * 16 + d.to_digit(16).unwrap();
                    self.i += 1;
                }
                _ => return Err(self.bad(what)),
            }
        }
        char::from_u32(v).ok_or_else(|| self.bad("bad escape (value out of range)"))
    }

    /// `[...]`. `]` first is a literal, `-` at either end is a literal, `\b` is
    /// a backspace rather than a boundary, `[a-c-e]` is `a-c` plus `-` plus
    /// `e`, and the set-operation shapes `[[a]`, `[a&&b]`, `[a||b]`, `[a~~b]`
    /// are literals (CPython's FutureWarning is stderr, which the harness
    /// strips). A range endpoint that is a class escape, or a range running
    /// backwards, is a `re.error`.
    fn class(&mut self) -> R<Node> {
        self.i += 1; // '['
        let mut cl = Class {
            negate: false,
            ranges: Vec::new(),
            cats: 0,
        };
        if self.peek() == Some('^') {
            cl.negate = true;
            self.i += 1;
        }
        let mut first = true;
        loop {
            let Some(c) = self.peek() else {
                return Err(self.bad("unterminated character set"));
            };
            if c == ']' && !first {
                self.i += 1;
                break;
            }
            first = false;
            let lo = self.class_item(&mut cl)?;
            // A `-` that is not the last character starts a range.
            if self.peek() == Some('-') && self.at(1) != Some(']') && self.at(1).is_some() {
                self.i += 1;
                let hi = self.class_item(&mut cl)?;
                match (lo, hi) {
                    (Some(a), Some(b)) => {
                        if (b as u32) < a as u32 {
                            return Err(self.bad("bad character range"));
                        }
                        cl.ranges.push((a as u32, b as u32));
                    }
                    _ => return Err(self.bad("bad character range")),
                }
            } else if let Some(a) = lo {
                cl.ranges.push((a as u32, a as u32));
            }
        }
        Ok(self.cls(cl))
    }

    /// One member. `None` when it was a category shorthand, which folds into
    /// `cl.cats` and can never be a range endpoint.
    fn class_item(&mut self, cl: &mut Class) -> R<Option<char>> {
        let c = self.peek().unwrap();
        if c != '\\' {
            self.i += 1;
            return Ok(Some(c));
        }
        self.i += 1;
        let Some(e) = self.peek() else {
            return Err(self.bad("bad escape (end of pattern)"));
        };
        self.i += 1;
        let cat = match e {
            'd' => C_D,
            'D' => C_ND,
            'w' => C_W,
            'W' => C_NW,
            's' => C_S,
            'S' => C_NS,
            _ => 0,
        };
        if cat != 0 {
            cl.cats |= cat;
            return Ok(None);
        }
        Ok(Some(match e {
            'a' => '\x07',
            // A BACKSPACE inside a class, not a word boundary.
            'b' => '\x08',
            'f' => '\x0c',
            'n' => '\n',
            'r' => '\r',
            't' => '\t',
            'v' => '\x0b',
            'x' => self.hex(2, "incomplete escape \\x")?,
            'u' => self.hex(4, "incomplete escape \\u")?,
            'U' => self.hex(8, "incomplete escape \\U")?,
            'N' => return Err(self.no("\\N{…}, which needs the Unicode name tables")),
            // Inside a class every `\ddd` is octal — `[\1]` is `\x01`, not a
            // group reference.
            '0'..='7' => {
                let mut v = e as u32 - '0' as u32;
                for _ in 0..2 {
                    match self.peek() {
                        Some(d) if ('0'..'8').contains(&d) => {
                            v = v * 8 + (d as u32 - '0' as u32);
                            self.i += 1;
                        }
                        _ => break,
                    }
                }
                if v > 0o377 {
                    return Err(self.bad("octal escape value outside of range 0-0o377"));
                }
                char::from_u32(v).unwrap_or('\0')
            }
            '8' | '9' => return Err(self.bad(&format!("bad escape \\{e}"))),
            c if c.is_ascii_alphabetic() => {
                return Err(self.bad(&format!("bad escape \\{c}")))
            }
            c => c,
        }))
    }
}

// ---- compilation ----------------------------------------------------------

/// `_compiler._simple`: a repeat whose body is exactly one character-wide atom
/// becomes a COUNTED repeat rather than one backtrack frame per character. This
/// is why `a*`, `\w+`, `[^x]*` and `.*` are linear over a 100,000-character
/// subject in CPython, and why they have to be linear here too.
fn simple(n: &Node) -> bool {
    match n {
        Node::Lit(_) | Node::Class(_) | Node::Any => true,
        Node::Group(None, b) => simple(b),
        _ => false,
    }
}

fn compile_node(n: &Node, out: &mut Vec<Op>, br: &mut Vec<u32>, fold: bool, dotall: bool) {
    match n {
        Node::Empty => {}
        Node::Lit(c) => out.push(if fold {
            Op::LitFold(c.to_ascii_lowercase())
        } else {
            Op::Lit(*c)
        }),
        Node::Class(i) => out.push(Op::In(*i)),
        Node::Any => out.push(if dotall { Op::AnyAll } else { Op::Any }),
        Node::At(a) => out.push(Op::At(*a)),
        Node::Group(Some(g), b) => {
            out.push(Op::Mark((g - 1) * 2));
            compile_node(b, out, br, fold, dotall);
            out.push(Op::Mark((g - 1) * 2 + 1));
        }
        Node::Group(None, b) => compile_node(b, out, br, fold, dotall),
        Node::Cat(v) => {
            for x in v {
                compile_node(x, out, br, fold, dotall);
            }
        }
        Node::Alt(v) => {
            let bpc = out.len();
            out.push(Op::Jump(0));
            let at = br.len() as u32;
            for _ in v {
                br.push(0);
            }
            let mut jumps = Vec::with_capacity(v.len());
            for (k, x) in v.iter().enumerate() {
                br[at as usize + k] = out.len() as u32;
                compile_node(x, out, br, fold, dotall);
                jumps.push(out.len());
                out.push(Op::Jump(0));
            }
            let end = out.len() as u32;
            for j in jumps {
                out[j] = Op::Jump(end);
            }
            out[bpc] = Op::Branch {
                at,
                n: v.len() as u32,
            };
        }
        Node::Rep {
            min,
            max,
            greedy,
            body,
        } => {
            if simple(body) {
                out.push(Op::RepOne {
                    min: *min,
                    max: *max,
                    greedy: *greedy,
                    peek: None,
                    fold_peek: false,
                });
                compile_node(body, out, br, fold, dotall);
            } else {
                let pc = out.len();
                out.push(Op::Repeat {
                    min: *min,
                    max: *max,
                    greedy: *greedy,
                    until: 0,
                });
                compile_node(body, out, br, fold, dotall);
                let until = out.len() as u32;
                out.push(Op::Until);
                out[pc] = Op::Repeat {
                    min: *min,
                    max: *max,
                    greedy: *greedy,
                    until,
                };
            }
        }
    }
}

/// `sre`'s next-literal peek, filled in once the tail exists: a greedy
/// one-character repeat whose tail begins with a literal skips every position
/// where that literal is absent instead of running the tail there.
fn fill_peeks(code: &mut Vec<Op>) {
    for pc in 0..code.len() {
        let greedy = match &code[pc] {
            Op::RepOne { greedy, .. } => *greedy,
            _ => continue,
        };
        if !greedy {
            continue;
        }
        let (peek, fold_peek) = match code.get(pc + 2) {
            Some(Op::Lit(c)) => (Some(*c), false),
            Some(Op::LitFold(c)) => (Some(*c), true),
            _ => continue,
        };
        if let Op::RepOne {
            peek: p,
            fold_peek: f,
            ..
        } = &mut code[pc]
        {
            *p = peek;
            *f = fold_peek;
        }
    }
}

/// Parse, compile, and record what the matcher has to check before it runs.
fn build(src: &Rc<str>, given: u32) -> R<Pat> {
    if given & L != 0 {
        return Err(refuse(
            "flags: re.LOCALE with a str pattern, which CPython answers with a ValueError",
        ));
    }
    if given & DEBUG != 0 {
        return Err(refuse(
            "flags: re.DEBUG, which prints CPython's own parse tree to stdout",
        ));
    }
    if given & !(I | M | S | X | A | U) != 0 {
        return Err(refuse(&format!(
            "flags: unknown flag bits 0x{:x}",
            given & !(I | M | S | X | A | U)
        )));
    }
    let chars: Vec<char> = src.chars().collect();
    let mut flags = given;
    let head = leading_flags(&chars, &mut flags)?;
    if flags & A != 0 && flags & U != 0 {
        return Err(refuse(
            "flags: ASCII and UNICODE together, which CPython answers with a ValueError",
        ));
    }
    if flags & L != 0 {
        return Err(refuse(
            "flags: re.LOCALE with a str pattern, which CPython answers with a ValueError",
        ));
    }
    let mut p = P {
        s: &chars,
        src,
        i: head,
        flags,
        groups: 0,
        classes: Vec::new(),
        depth: 0,
    };
    let tree = p.alt()?;
    if p.i < chars.len() {
        // Only a stray `)` can stop the top-level parse early.
        return Err(bad_pattern(src, "unbalanced parenthesis"));
    }
    let (groups, classes, flags) = (p.groups, p.classes, p.flags);
    let fold = flags & I != 0;
    let mut code = Vec::new();
    let mut branches = Vec::new();
    compile_node(&tree, &mut code, &mut branches, fold, flags & S != 0);
    code.push(Op::Success);
    fill_peeks(&mut code);
    let has_table = code.iter().any(|op| match op {
        Op::In(i) => classes[*i as usize].cats != 0,
        Op::At(At::WordB) | Op::At(At::NotWordB) => true,
        _ => false,
    });
    let has_nwb = code.iter().any(|op| matches!(op, Op::At(At::NotWordB)));
    // `.flags` is what CPython reports: the given bits, the inline bits, and
    // UNICODE unless ASCII was asked for.
    let eff = if flags & A != 0 { flags } else { flags | U };
    Ok(Pat {
        source: src.clone(),
        flags: eff,
        groups,
        code,
        branches,
        classes,
        has_table: has_table && flags & A == 0,
        folds: fold && flags & A == 0,
        has_nwb,
        src_ascii: src.is_ascii(),
    })
}

// ---- the machine ----------------------------------------------------------

/// One live repeat context, `sre`'s `SRE_REPEAT`. `prev` chains them, so a
/// nested repeat sees its own and the tail after it sees the outer one.
struct Rep {
    prev: u32,
    body: u32,
    min: u32,
    max: u32,
    greedy: bool,
    count: i64,
    /// The position the previous iteration started at. An iteration that ends
    /// where it started stops the loop — and is still RECORDED, which is what
    /// makes `re.match(r'(a*)*', 'aa').groups()` `('',)` rather than `('aa',)`.
    last: u32,
}

const F_BRANCH: u8 = 0;
const F_REPONE: u8 = 1;
const F_MINREPONE: u8 = 2;
const F_UNTIL1: u8 = 3;
const F_UNTIL2: u8 = 4;
const F_UNTIL3: u8 = 5;
const F_MIN1: u8 = 6;
const F_MIN4: u8 = 7;
const F_MIN5: u8 = 8;

struct Frame {
    kind: u8,
    pc: u32,
    ptr: u32,
    mu: u32,
    cr: u32,
    rl: u32,
    a: u32,
    b: u32,
}

struct Ex<'a> {
    code: &'a [Op],
    branches: &'a [u32],
    classes: &'a [Class],
    s: &'a [char],
    end: usize,
    fold: bool,
    ascii: bool,
    marks: Vec<u32>,
    mundo: Vec<(u32, u32)>,
    reps: Vec<Rep>,
    cur: u32,
    stack: Vec<Frame>,
    steps: u64,
    src: Rc<str>,
}

impl<'a> Ex<'a> {
    fn over(&self) -> LypningError {
        refuse(&format!(
            "backtracking budget exceeded matching {} — CPython is exponential on this \
             shape too and would answer, slowly",
            show(&self.src)
        ))
    }

    fn word_at(&self, ptr: usize) -> bool {
        if self.end == 0 {
            return false;
        }
        let before = ptr > 0 && is_word(self.s[ptr - 1]);
        let after = ptr < self.end && is_word(self.s[ptr]);
        before != after
    }

    fn at_ok(&self, a: At, ptr: usize) -> bool {
        match a {
            At::Begin => ptr == 0,
            At::BeginLine => ptr == 0 || self.s[ptr - 1] == '\n',
            At::End => ptr == self.end || (ptr + 1 == self.end && self.s[ptr] == '\n'),
            At::EndLine => ptr == self.end || self.s[ptr] == '\n',
            At::StrEnd => ptr == self.end,
            At::WordB => self.word_at(ptr),
            At::NotWordB => !self.word_at(ptr),
        }
    }

    fn in_class(&self, i: u32, c: char) -> bool {
        let cl = &self.classes[i as usize];
        let mut hit = cl.raw(c, self.ascii);
        if self.fold && !hit {
            let d = swap_ascii(c);
            if d != c {
                hit = cl.raw(d, self.ascii);
            }
        }
        cl.negate != hit
    }

    /// Does the ONE-op atom at `pc` match `c`? Only the four shapes [`simple`]
    /// admits ever reach here.
    fn atom(&self, pc: u32, c: char) -> bool {
        match self.code[pc as usize] {
            Op::Lit(x) => c == x,
            Op::LitFold(x) => c.to_ascii_lowercase() == x,
            Op::Any => c != '\n',
            Op::AnyAll => true,
            Op::In(i) => self.in_class(i, c),
            _ => false,
        }
    }

    fn setmark(&mut self, slot: u32, v: u32) {
        self.mundo.push((slot, self.marks[slot as usize]));
        self.marks[slot as usize] = v;
    }

    fn undo(&mut self, to: u32) {
        while self.mundo.len() > to as usize {
            let (slot, old) = self.mundo.pop().unwrap();
            self.marks[slot as usize] = old;
        }
    }

    fn push(&mut self, f: Frame) -> R<()> {
        if self.stack.len() >= FRAME_CEILING {
            return Err(refuse(&format!(
                "backtracking stack exceeded matching {}",
                show(&self.src)
            )));
        }
        self.stack.push(f);
        Ok(())
    }

    /// The greedy one-character repeat's peek trim: the largest `count` at or
    /// above `min` whose next character is the literal the tail starts with.
    fn trim(&self, mut count: u32, base: usize, min: u32, peek: char, fold: bool) -> Option<u32> {
        loop {
            let p = base + count as usize;
            let ok = p < self.end
                && if fold {
                    self.s[p].to_ascii_lowercase() == peek
                } else {
                    self.s[p] == peek
                };
            if ok {
                return Some(count);
            }
            if count == min {
                return None;
            }
            count -= 1;
        }
    }

    /// One match attempt, anchored at `start`. `Some(end)` with `marks` filled.
    fn run(&mut self, start: usize, must_advance: bool, match_all: bool) -> R<Option<usize>> {
        for m in self.marks.iter_mut() {
            *m = NOMARK;
        }
        self.mundo.clear();
        self.reps.clear();
        self.stack.clear();
        self.cur = NOREP;
        let mut pc: u32 = 0;
        let mut ptr: usize = start;
        'run: loop {
            // ---- forward ----
            loop {
                self.steps += 1;
                if self.steps > STEP_BUDGET {
                    return Err(self.over());
                }
                match self.code[pc as usize] {
                    Op::Lit(c) => {
                        if ptr < self.end && self.s[ptr] == c {
                            ptr += 1;
                            pc += 1;
                        } else {
                            break;
                        }
                    }
                    Op::LitFold(c) => {
                        if ptr < self.end && self.s[ptr].to_ascii_lowercase() == c {
                            ptr += 1;
                            pc += 1;
                        } else {
                            break;
                        }
                    }
                    Op::Any => {
                        if ptr < self.end && self.s[ptr] != '\n' {
                            ptr += 1;
                            pc += 1;
                        } else {
                            break;
                        }
                    }
                    Op::AnyAll => {
                        if ptr < self.end {
                            ptr += 1;
                            pc += 1;
                        } else {
                            break;
                        }
                    }
                    Op::In(i) => {
                        if ptr < self.end && self.in_class(i, self.s[ptr]) {
                            ptr += 1;
                            pc += 1;
                        } else {
                            break;
                        }
                    }
                    Op::At(a) => {
                        if self.at_ok(a, ptr) {
                            pc += 1;
                        } else {
                            break;
                        }
                    }
                    Op::Mark(slot) => {
                        self.setmark(slot, ptr as u32);
                        pc += 1;
                    }
                    Op::Jump(t) => pc = t,
                    Op::Branch { at, n } => {
                        let first = self.branches[at as usize];
                        if n > 1 {
                            let f = Frame {
                                kind: F_BRANCH,
                                pc,
                                ptr: ptr as u32,
                                mu: self.mundo.len() as u32,
                                cr: self.cur,
                                rl: self.reps.len() as u32,
                                a: 1,
                                b: 0,
                            };
                            self.push(f)?;
                        }
                        pc = first;
                    }
                    Op::RepOne {
                        min,
                        max,
                        greedy,
                        peek,
                        fold_peek,
                    } => {
                        let base = ptr;
                        let atom_pc = pc + 1;
                        if greedy {
                            let mut count = 0u32;
                            while count < max
                                && base + (count as usize) < self.end
                                && self.atom(atom_pc, self.s[base + count as usize])
                            {
                                count += 1;
                            }
                            if count < min {
                                break;
                            }
                            if let Some(c) = peek {
                                match self.trim(count, base, min, c, fold_peek) {
                                    Some(n) => count = n,
                                    None => break,
                                }
                            }
                            ptr = base + count as usize;
                            let f = Frame {
                                kind: F_REPONE,
                                pc,
                                ptr: base as u32,
                                mu: self.mundo.len() as u32,
                                cr: self.cur,
                                rl: self.reps.len() as u32,
                                a: count,
                                b: 0,
                            };
                            self.push(f)?;
                            pc += 2;
                        } else {
                            let mut count = 0u32;
                            while count < min
                                && base + (count as usize) < self.end
                                && self.atom(atom_pc, self.s[base + count as usize])
                            {
                                count += 1;
                            }
                            if count < min {
                                break;
                            }
                            ptr = base + count as usize;
                            let f = Frame {
                                kind: F_MINREPONE,
                                pc,
                                ptr: base as u32,
                                mu: self.mundo.len() as u32,
                                cr: self.cur,
                                rl: self.reps.len() as u32,
                                a: count,
                                b: 0,
                            };
                            self.push(f)?;
                            pc += 2;
                        }
                    }
                    Op::Repeat {
                        min,
                        max,
                        greedy,
                        until,
                    } => {
                        let r = Rep {
                            prev: self.cur,
                            body: pc + 1,
                            min,
                            max,
                            greedy,
                            count: -1,
                            last: NOMARK,
                        };
                        pc = until;
                        if self.reps.len() >= FRAME_CEILING {
                            return Err(self.over());
                        }
                        self.reps.push(r);
                        self.cur = self.reps.len() as u32 - 1;
                    }
                    Op::Until => {
                        // A jump table bug would index the arena out of range
                        // and PANIC, which is exit 101 — not a refusal, and not
                        // something the chain can route onward. Refuse instead.
                        if self.cur == NOREP {
                            return Err(self.over());
                        }
                        let r = self.cur as usize;
                        let (min, max, greedy, body) = {
                            let rep = &self.reps[r];
                            (rep.min, rep.max, rep.greedy, rep.body)
                        };
                        let ptr0 = ptr as u32;
                        let mu = self.mundo.len() as u32;
                        let count = self.reps[r].count + 1;
                        let iterate_kind = if greedy {
                            if count < min as i64 {
                                Some(F_UNTIL1)
                            } else if (count as u64) < max as u64 && ptr0 != self.reps[r].last {
                                Some(F_UNTIL2)
                            } else {
                                None
                            }
                        } else if count < min as i64 {
                            Some(F_MIN1)
                        } else {
                            None
                        };
                        if let Some(kind) = iterate_kind {
                            self.reps[r].count = count;
                            let saved = self.reps[r].last;
                            self.reps[r].last = ptr0;
                            let f = Frame {
                                kind,
                                pc,
                                ptr: ptr0,
                                mu,
                                cr: self.cur,
                                rl: self.reps.len() as u32,
                                a: count as u32,
                                b: saved,
                            };
                            self.push(f)?;
                            pc = body;
                        } else if greedy {
                            {
                                let saved_cur = self.cur;
                                self.cur = self.reps[r].prev;
                                let f = Frame {
                                    kind: F_UNTIL3,
                                    pc,
                                    ptr: ptr0,
                                    mu,
                                    cr: saved_cur,
                                    rl: self.reps.len() as u32,
                                    a: count as u32,
                                    b: 0,
                                };
                                self.push(f)?;
                                pc += 1;
                            }
                        } else {
                            let saved_cur = self.cur;
                            self.cur = self.reps[r].prev;
                            let f = Frame {
                                kind: F_MIN4,
                                pc,
                                ptr: ptr0,
                                mu,
                                cr: saved_cur,
                                rl: self.reps.len() as u32,
                                a: count as u32,
                                b: 0,
                            };
                            self.push(f)?;
                            pc += 1;
                        }
                    }
                    Op::Success => {
                        // `fullmatch` is an assertion INSIDE the backtracking,
                        // not a length test after it: `re.fullmatch(r'a|ab',
                        // 'ab')` is `'ab'` and `re.fullmatch(r'a*?', 'aaa')` is
                        // `'aaa'`. And `must_advance` is the 3.7 rule that an
                        // empty match may not repeat at the position the last
                        // one ended at — checked here so the engine BACKTRACKS
                        // into a longer alternative rather than giving up on
                        // the position.
                        if (match_all && ptr != self.end)
                            || (must_advance && ptr == start)
                        {
                            break;
                        }
                        return Ok(Some(ptr));
                    }
                }
            }
            // ---- backtrack ----
            loop {
                self.steps += 1;
                if self.steps > STEP_BUDGET {
                    return Err(self.over());
                }
                let Some(f) = self.stack.pop() else {
                    return Ok(None);
                };
                self.undo(f.mu);
                self.cur = f.cr;
                self.reps.truncate(f.rl as usize);
                ptr = f.ptr as usize;
                match f.kind {
                    F_BRANCH => {
                        let (at, n) = match self.code[f.pc as usize] {
                            Op::Branch { at, n } => (at as usize, n as usize),
                            _ => return Err(self.over()),
                        };
                        let i = f.a as usize;
                        let next = self.branches[at + i];
                        if i + 1 < n {
                            let mut g = f;
                            g.a += 1;
                            self.push(g)?;
                        }
                        pc = next;
                        continue 'run;
                    }
                    F_REPONE => {
                        let (min, peek, fold_peek) = match self.code[f.pc as usize] {
                            Op::RepOne {
                                min,
                                peek,
                                fold_peek,
                                ..
                            } => (min, peek, fold_peek),
                            _ => return Err(self.over()),
                        };
                        let base = f.ptr as usize;
                        if f.a == min {
                            continue;
                        }
                        let mut count = f.a - 1;
                        if let Some(c) = peek {
                            match self.trim(count, base, min, c, fold_peek) {
                                Some(n) => count = n,
                                None => continue,
                            }
                        }
                        ptr = base + count as usize;
                        let mut g = f;
                        g.a = count;
                        let npc = g.pc + 2;
                        self.push(g)?;
                        pc = npc;
                        continue 'run;
                    }
                    F_MINREPONE => {
                        let (max, atom_pc) = match self.code[f.pc as usize] {
                            Op::RepOne { max, .. } => (max, f.pc + 1),
                            _ => return Err(self.over()),
                        };
                        let base = f.ptr as usize;
                        let count = f.a;
                        let p = base + count as usize;
                        if count >= max || p >= self.end || !self.atom(atom_pc, self.s[p]) {
                            continue;
                        }
                        ptr = p + 1;
                        let mut g = f;
                        g.a = count + 1;
                        let npc = g.pc + 2;
                        self.push(g)?;
                        pc = npc;
                        continue 'run;
                    }
                    F_UNTIL1 | F_MIN1 | F_MIN5 => {
                        let r = self.cur as usize;
                        self.reps[r].count = f.a as i64 - 1;
                        self.reps[r].last = f.b;
                        continue;
                    }
                    F_UNTIL2 => {
                        let r = self.cur as usize;
                        self.reps[r].count = f.a as i64 - 1;
                        self.reps[r].last = f.b;
                        // …and then the tail, which is what the C code falls
                        // through to when one more iteration did not work out.
                        let saved_cur = self.cur;
                        self.cur = self.reps[r].prev;
                        let g = Frame {
                            kind: F_UNTIL3,
                            pc: f.pc,
                            ptr: f.ptr,
                            mu: f.mu,
                            cr: saved_cur,
                            rl: self.reps.len() as u32,
                            a: f.a,
                            b: 0,
                        };
                        self.push(g)?;
                        pc = f.pc + 1;
                        continue 'run;
                    }
                    F_UNTIL3 => continue,
                    // The lazy repeat tried the tail first; now it may try one
                    // more iteration, unless the count is spent or the last one
                    // was empty.
                    _ => {
                        let r = self.cur as usize;
                        let count = f.a;
                        let (max, body) = (self.reps[r].max, self.reps[r].body);
                        if (count as u64) >= max as u64 || f.ptr == self.reps[r].last {
                            continue;
                        }
                        self.reps[r].count = count as i64;
                        let saved = self.reps[r].last;
                        self.reps[r].last = f.ptr;
                        let g = Frame {
                            kind: F_MIN5,
                            pc: f.pc,
                            ptr: f.ptr,
                            mu: f.mu,
                            cr: self.cur,
                            rl: self.reps.len() as u32,
                            a: count,
                            b: saved,
                        };
                        self.push(g)?;
                        pc = body;
                        continue 'run;
                    }
                }
            }
        }
    }
}

// ---- subjects, matches and the scan loop ----------------------------------

/// The subject of ONE call, shared by every `Match` it produces. The original
/// `Rc<str>` is kept so that `m.string is s` holds; the `Vec<char>` is what
/// makes every offset a code-point offset.
pub struct Subject {
    text: Rc<str>,
    chars: Vec<char>,
    ascii: bool,
}

impl Subject {
    fn new(text: Rc<str>) -> Rc<Subject> {
        let ascii = text.is_ascii();
        let chars = text.chars().collect();
        Rc::new(Subject { text, chars, ascii })
    }
    /// The text between two CODE-POINT offsets.
    fn slice(&self, a: u32, b: u32) -> Rc<str> {
        if self.ascii {
            return Rc::from(&self.text[a as usize..b as usize]);
        }
        Rc::from(
            self.chars[a as usize..b as usize]
                .iter()
                .collect::<String>()
                .as_str(),
        )
    }
}

/// One match. `spans` is group 0 followed by every capturing group, each
/// `NOMARK` when the group did not participate — which is a different thing
/// from an empty one, and the difference is visible in four different ways:
/// `group()` says `None` and `''`, `groups()` takes a default, `findall`
/// renders `''`, `split` keeps `None`, and `span()` gives `(-1, -1)`.
pub struct MatchObj {
    pub pat: Rc<Pat>,
    subj: Rc<Subject>,
    spans: Vec<u32>,
    pos: u32,
    endpos: u32,
}

impl MatchObj {
    fn n(&self) -> usize {
        self.spans.len() / 2
    }
    fn span_of(&self, g: usize) -> (u32, u32) {
        (self.spans[g * 2], self.spans[g * 2 + 1])
    }
    fn group_value(&self, g: usize) -> Value {
        let (a, b) = self.span_of(g);
        if a == NOMARK || b == NOMARK {
            Value::None
        } else {
            Value::Str(self.subj.slice(a, b))
        }
    }
    /// `''` for a group that did not participate — `findall` and the `sub`
    /// template render it that way, `groups()` and `split` do not.
    fn group_text(&self, g: usize) -> Rc<str> {
        let (a, b) = self.span_of(g);
        if a == NOMARK || b == NOMARK {
            Rc::from("")
        } else {
            self.subj.slice(a, b)
        }
    }
    fn index_of(&self, v: &Value, what: &str) -> R<usize> {
        let g = match v {
            Value::Str(_) => {
                return Err(refuse(
                    "group '<name>': named groups are not served yet",
                ))
            }
            other => as_index(other, what)?,
        };
        if g < 0 || g as usize >= self.n() {
            return Err(refuse(&format!("group {g}: no such group")));
        }
        Ok(g as usize)
    }
}

/// A whole `re` API call: the pattern, the subject, the searched range, and
/// the one step budget they share.
struct Run<'a> {
    pat: &'a Rc<Pat>,
    subj: Rc<Subject>,
    pos: usize,
    endpos: usize,
    steps: u64,
}

impl<'a> Run<'a> {
    fn new(pat: &'a Rc<Pat>, subj: Rc<Subject>, pos: usize, endpos: usize) -> R<Run<'a>> {
        // The two runtime rules that cannot be decided at compile time,
        // checked once per call rather than once per character.
        let ascii_slice = subj.ascii
            || subj.chars[pos..endpos.max(pos)].iter().all(|c| c.is_ascii());
        if !ascii_slice || !pat.src_ascii {
            if pat.has_table {
                return Err(refuse(
                    "\\w, \\d, \\s, \\b, \\B, \\W, \\D or \\S on a non-ASCII pattern or subject \
                     (Unicode tables)",
                ));
            }
            if pat.folds {
                return Err(refuse(
                    "re.IGNORECASE with a non-ASCII pattern or subject",
                ));
            }
        }
        if pat.has_nwb && endpos <= pos {
            return Err(refuse(
                "\\B on an empty string, which CPython 3.12 and 3.14 answer differently",
            ));
        }
        Ok(Run {
            pat,
            subj,
            pos,
            endpos,
            steps: 0,
        })
    }

    /// A machine over borrowed program and subject. Free rather than a method
    /// so that its lifetime is the two `Rc`s the caller holds and NOT `&self`,
    /// which the caller has to write its step count back into.
    fn ex<'x>(pat: &'x Pat, chars: &'x [char], end: usize, steps: u64) -> Ex<'x> {
        Ex {
            code: &pat.code,
            branches: &pat.branches,
            classes: &pat.classes,
            s: chars,
            end,
            fold: pat.flags & I != 0,
            ascii: pat.ascii_flag(),
            marks: vec![NOMARK; pat.groups as usize * 2],
            mundo: Vec::new(),
            reps: Vec::new(),
            cur: NOREP,
            stack: Vec::new(),
            steps,
            src: pat.source.clone(),
        }
    }

    fn record(&self, ex: &Ex, start: usize, end: usize) -> Vec<u32> {
        let mut spans = Vec::with_capacity(ex.marks.len() + 2);
        spans.push(start as u32);
        spans.push(end as u32);
        // A group whose two marks are not BOTH set did not participate:
        // `(a)(b)?` on `'a'` leaves slot 2 and slot 3 unset together, but
        // `(?:(a)|b)+` can leave one half of a pair behind on a backtrack.
        for pair in ex.marks.chunks(2) {
            if pair[0] == NOMARK || pair[1] == NOMARK {
                spans.push(NOMARK);
                spans.push(NOMARK);
            } else {
                spans.push(pair[0]);
                spans.push(pair[1]);
            }
        }
        spans
    }

    /// One anchored attempt at `start`.
    fn once(&mut self, start: usize, must_advance: bool, all: bool) -> R<Option<Vec<u32>>> {
        let (pat, subj) = (self.pat.clone(), self.subj.clone());
        let mut ex = Run::ex(&pat, &subj.chars, self.endpos, self.steps);
        let out = ex.run(start, must_advance, all)?;
        let rec = out.map(|e| self.record(&ex, start, e));
        self.steps = ex.steps;
        Ok(rec)
    }

    /// `sre_search`: try every start from `from` to `endpos`. `must_advance` —
    /// the 3.7 rule that an empty match may not repeat where the last one
    /// ended — applies at the FIRST position only, which is what makes
    /// `re.sub(r'|a', 'X', 'ab')` `'XXXbX'` and not an endless loop.
    fn search(&mut self, from: usize, must_advance: bool) -> R<Option<Vec<u32>>> {
        // `^` and `\A` can only hold at index 0, so a search for one is one
        // attempt, not one per character.
        // `endpos < pos` is not clamped: `_sre`'s search loop is
        // `while (ptr <= end)`, so it simply never runs —
        // `re.compile('').search('abc', 2, 1)` is None while
        // `re.compile('').match('abc', 2, 1)` is a match at (2, 2).
        if from > self.endpos {
            return Ok(None);
        }
        let anchored = matches!(self.pat.code.first(), Some(Op::At(At::Begin)));
        let (pat, subj) = (self.pat.clone(), self.subj.clone());
        let mut ex = Run::ex(&pat, &subj.chars, self.endpos, self.steps);
        let mut start = from;
        loop {
            if let Some(e) = ex.run(start, must_advance && start == from, false)? {
                let rec = self.record(&ex, start, e);
                self.steps = ex.steps;
                return Ok(Some(rec));
            }
            if start >= self.endpos || anchored {
                self.steps = ex.steps;
                return Ok(None);
            }
            start += 1;
        }
    }

    /// Every match, left to right, at most `max` of them. The scan rule is
    /// `_sre`'s: the next attempt starts where the last match ended, and an
    /// EMPTY match sets `must_advance` for that one attempt — so an empty match
    /// immediately after a non-empty one at the same position IS produced
    /// (`re.findall(r'a*', 'baaac')` is `['', 'aaa', '', '']`) while two empty
    /// matches at one position are not.
    fn scan(&mut self, max: usize) -> R<Vec<Vec<u32>>> {
        let mut out: Vec<Vec<u32>> = Vec::new();
        let mut p = self.pos;
        let mut must_advance = false;
        while out.len() < max {
            let Some(rec) = self.search(p, must_advance)? else {
                break;
            };
            let (s, e) = (rec[0] as usize, rec[1] as usize);
            out.push(rec);
            must_advance = e == s;
            p = e;
            if p > self.endpos {
                break;
            }
        }
        Ok(out)
    }

    fn make(&self, spans: Vec<u32>) -> Value {
        Value::Match(Rc::new(MatchObj {
            pat: self.pat.clone(),
            subj: self.subj.clone(),
            spans,
            pos: self.pos as u32,
            endpos: self.endpos as u32,
        }))
    }
}

// ---- the compile cache ----------------------------------------------------

// CPython's `re._cache`, 512 entries, keyed on the pattern text and the flags
// AS GIVEN. It is not a speed trick here: it is what makes
// `re.compile('a') is re.compile('a')` and `m.re is p` True, which they are in
// CPython and which no amount of value equality can reproduce.
thread_local! {
    static CACHE: RefCell<Vec<(Rc<str>, u32, Rc<Pat>)>> = const { RefCell::new(Vec::new()) };
    /// Set once the cache has thrown an entry away. After that two equal
    /// patterns may or may not be the same object, and CPython's answer depends
    /// on an eviction order that is its own — so `is` between them refuses.
    static EVICTED: Cell<bool> = const { Cell::new(false) };
}

const CACHE_MAX: usize = 512;

fn cached(src: &Rc<str>, given: u32) -> R<Rc<Pat>> {
    let hit = CACHE.with(|c| {
        c.borrow()
            .iter()
            .find(|(s, f, _)| *f == given && s.as_ref() == src.as_ref())
            .map(|(_, _, p)| p.clone())
    });
    if let Some(p) = hit {
        return Ok(p);
    }
    let p = Rc::new(build(src, given)?);
    CACHE.with(|c| {
        let mut b = c.borrow_mut();
        if b.len() >= CACHE_MAX {
            b.clear();
            EVICTED.with(|e| e.set(true));
        }
        b.push((src.clone(), given, p.clone()));
    });
    Ok(p)
}

fn purge_cache() {
    CACHE.with(|c| c.borrow_mut().clear());
    EVICTED.with(|e| e.set(false));
}

/// `p is q` for two DIFFERENT `Rc`s that compare equal: True in CPython
/// whenever the cache still holds the entry, and the eviction order that
/// decides it is version-shaped.
pub fn identity_unclear(a: &Value, b: &Value) -> bool {
    matches!((a, b), (Value::Pattern(_), Value::Pattern(_))) && EVICTED.with(|e| e.get())
}

// ---- argument shapes ------------------------------------------------------

fn as_index(v: &Value, what: &str) -> R<i64> {
    match v {
        Value::Int(i) => Ok(*i),
        Value::Bool(b) => Ok(*b as i64),
        Value::ReFlag(f) => Ok(*f as i64),
        other => Err(refuse(&format!(
            "{what} of a {}, which CPython answers with a TypeError",
            type_name(other)
        ))),
    }
}

fn as_text(v: &Value, what: &str) -> R<Rc<str>> {
    match v {
        Value::Str(s) => Ok(s.clone()),
        Value::Bytes(_) => Err(refuse("bytes pattern or subject (re over bytes)")),
        other => Err(refuse(&format!(
            "{what} of a {}, which CPython answers with a TypeError",
            type_name(other)
        ))),
    }
}

fn as_flags(v: Option<&Value>) -> R<u32> {
    match v {
        None | Some(Value::None) => Ok(0),
        Some(Value::ReFlag(f)) => Ok(*f),
        Some(Value::Bool(b)) => Ok(*b as u32),
        Some(Value::Int(i)) => u32::try_from(*i)
            .map_err(|_| refuse(&format!("flags: the value {i}, which is not a flag mask"))),
        Some(other) => Err(refuse(&format!(
            "flags: a {}, which CPython answers with a TypeError",
            type_name(other)
        ))),
    }
}

/// Positional-or-keyword binding with CPython's parameter names, which is the
/// whole of the flags-versus-count trap: `re.sub(p, r, s, re.I)` passes
/// `count=2`, not `flags=re.IGNORECASE`.
fn bind(disp: &str, args: &Args, kw: &[(Rc<str>, Value)], names: &[&str]) -> R<Vec<Option<Value>>> {
    if args.len() > names.len() {
        return Err(refuse(&format!(
            "{disp}() with {} positional arguments",
            args.len()
        )));
    }
    let mut out: Vec<Option<Value>> = vec![None; names.len()];
    for (i, v) in args.iter().enumerate() {
        out[i] = Some(v.clone());
    }
    for (k, v) in kw {
        match names.iter().position(|n| *n == k.as_ref()) {
            Some(i) if out[i].is_none() => out[i] = Some(v.clone()),
            Some(_) => {
                return Err(refuse(&format!(
                    "{disp}() with a repeated argument '{k}'"
                )))
            }
            None => {
                return Err(refuse(&format!(
                    "{disp}() with an unexpected keyword argument '{k}'"
                )))
            }
        }
    }
    Ok(out)
}

fn need(v: &Option<Value>, disp: &str, name: &str) -> R<Value> {
    v.clone()
        .ok_or_else(|| refuse(&format!("{disp}() without its '{name}' argument")))
}

/// The `pattern` argument: text to compile, or a `Pattern` to use as it is —
/// and giving flags with an already-compiled pattern is a ValueError there.
fn pattern_arg(v: &Value, flags: u32, disp: &str) -> R<Rc<Pat>> {
    match v {
        Value::Pattern(p) => {
            if flags != 0 {
                return Err(refuse(
                    "flags with an already-compiled pattern, which CPython answers with a \
                     ValueError",
                ));
            }
            Ok(p.clone())
        }
        other => cached(&as_text(other, &format!("{disp}() pattern"))?, flags),
    }
}

/// `pos` / `endpos`, clamped the way `_sre` clamps them: into `[0, len]`, and
/// `endpos` never below `pos` — never Python's negative indexing, so
/// `p.search('abc', 0, -1)` is None rather than a search of `'ab'`.
fn clamp(v: Option<&Value>, what: &str, len: usize, dflt: usize) -> R<usize> {
    let Some(v) = v else { return Ok(dflt) };
    if matches!(v, Value::None) {
        return Ok(dflt);
    }
    let n = as_index(v, what)?;
    Ok(if n < 0 {
        0
    } else if n as u128 > len as u128 {
        len
    } else {
        n as usize
    })
}

// ---- the replacement template ---------------------------------------------

enum Tpl {
    Text(String),
    Group(u32),
}

/// `sub`'s template is NOT the pattern's escape grammar, and reusing one for
/// the other is a wrong answer in both directions: `\x41` and `\N{…}` are legal
/// in a pattern and errors in a template, while `\.` and `\-` are the CHARACTER
/// in a pattern and the two-character text `\.` in a template.
fn parse_template(t: &str, groups: u32) -> R<Vec<Tpl>> {
    let cs: Vec<char> = t.chars().collect();
    let mut out: Vec<Tpl> = Vec::new();
    let mut lit = String::new();
    let mut i = 0;
    let flush = |lit: &mut String, out: &mut Vec<Tpl>| {
        if !lit.is_empty() {
            out.push(Tpl::Text(std::mem::take(lit)));
        }
    };
    let bad = |what: &str| refuse(&format!("template {}: {what}", show(t)));
    while i < cs.len() {
        let c = cs[i];
        i += 1;
        if c != '\\' {
            lit.push(c);
            continue;
        }
        let Some(&e) = cs.get(i) else {
            return Err(bad("bad escape (end of pattern)"));
        };
        i += 1;
        match e {
            'a' => lit.push('\x07'),
            'b' => lit.push('\x08'),
            'f' => lit.push('\x0c'),
            'n' => lit.push('\n'),
            'r' => lit.push('\r'),
            't' => lit.push('\t'),
            'v' => lit.push('\x0b'),
            '\\' => lit.push('\\'),
            'g' => {
                if cs.get(i) != Some(&'<') {
                    return Err(bad("missing <"));
                }
                i += 1;
                let start = i;
                while i < cs.len() && cs[i] != '>' {
                    i += 1;
                }
                if i >= cs.len() {
                    return Err(bad("missing >, unterminated name"));
                }
                let name: String = cs[start..i].iter().collect();
                i += 1;
                match name.parse::<u32>() {
                    Ok(n) if !name.is_empty() && n <= groups => {
                        flush(&mut lit, &mut out);
                        out.push(Tpl::Group(n));
                    }
                    Ok(n) => return Err(bad(&format!("invalid group reference {n}"))),
                    Err(_) => {
                        return Err(refuse(&format!(
                            "template {}: \\g<{name}>, a named group reference",
                            show(t)
                        )))
                    }
                }
            }
            '0' => {
                let mut v = 0u32;
                for _ in 0..2 {
                    match cs.get(i) {
                        Some(d) if ('0'..'8').contains(d) => {
                            v = v * 8 + (*d as u32 - '0' as u32);
                            i += 1;
                        }
                        _ => break,
                    }
                }
                lit.push(char::from_u32(v & 0xff).unwrap_or('\0'));
            }
            '1'..='9' => {
                let mut num = String::new();
                num.push(e);
                if matches!(cs.get(i), Some(d) if d.is_ascii_digit()) {
                    let d2 = cs[i];
                    num.push(d2);
                    i += 1;
                    let oct = |x: char| ('0'..'8').contains(&x);
                    if oct(e) && oct(d2) && matches!(cs.get(i), Some(d3) if oct(*d3)) {
                        let d3 = cs[i];
                        i += 1;
                        let v = u32::from_str_radix(&format!("{e}{d2}{d3}"), 8).unwrap_or(0);
                        if v > 0o377 {
                            return Err(bad("octal escape value outside of range 0-0o377"));
                        }
                        lit.push(char::from_u32(v).unwrap_or('\0'));
                        continue;
                    }
                }
                let n: u32 = num.parse().unwrap_or(0);
                if n > groups {
                    return Err(bad(&format!("invalid group reference {n}")));
                }
                flush(&mut lit, &mut out);
                out.push(Tpl::Group(n));
            }
            c if c.is_ascii_alphabetic() => return Err(bad(&format!("bad escape \\{c}"))),
            // An unknown NON-letter escape keeps BOTH characters: `\.` is a
            // backslash and a dot, where in a pattern it is just the dot.
            c => {
                lit.push('\\');
                lit.push(c);
            }
        }
    }
    flush(&mut lit, &mut out);
    Ok(out)
}

fn expand(tpl: &[Tpl], m: &MatchObj) -> String {
    let mut out = String::new();
    for part in tpl {
        match part {
            Tpl::Text(s) => out.push_str(s),
            Tpl::Group(g) => out.push_str(&m.group_text(*g as usize)),
        }
    }
    out
}

// ---- the API --------------------------------------------------------------

/// Which module function this is, once the pattern and subject are in hand.
enum Api {
    Search,
    Match,
    Full,
    FindAll,
    FindIter,
}

fn one_match(
    pat: &Rc<Pat>,
    subj: Rc<Subject>,
    pos: usize,
    endpos: usize,
    api: &Api,
) -> R<Value> {
    let mut run = Run::new(pat, subj, pos, endpos)?;
    let found = match api {
        Api::Search => run.search(pos, false)?,
        Api::Match => run.once(pos, false, false)?,
        Api::Full => run.once(pos, false, true)?,
        _ => None,
    };
    Ok(match found {
        Some(spans) => run.make(spans),
        None => Value::None,
    })
}

fn findall_value(m: &MatchObj) -> Value {
    match m.n() {
        1 => Value::Str(m.group_text(0)),
        2 => Value::Str(m.group_text(1)),
        n => Value::Tuple(Rc::new(
            (1..n).map(|g| Value::Str(m.group_text(g))).collect(),
        )),
    }
}

fn read_only(
    pat: &Rc<Pat>,
    subj: Rc<Subject>,
    pos: usize,
    endpos: usize,
    api: Api,
) -> R<Value> {
    match api {
        Api::FindAll | Api::FindIter => {}
        _ => return one_match(pat, subj, pos, endpos, &api),
    }
    let mut run = Run::new(pat, subj.clone(), pos, endpos)?;
    let recs = run.scan(usize::MAX)?;
    if matches!(api, Api::FindIter) {
        let items: Vec<Value> = recs.into_iter().map(|r| run.make(r)).collect();
        return Ok(Value::IterObj(
            Rc::new(RefCell::new(crate::iter::Iter::Vec(items, 0))),
            "callable_iterator",
        ));
    }
    let out: Vec<Value> = recs
        .into_iter()
        .map(|spans| {
            let m = MatchObj {
                pat: pat.clone(),
                subj: subj.clone(),
                spans,
                pos: pos as u32,
                endpos: endpos as u32,
            };
            findall_value(&m)
        })
        .collect();
    Ok(crate::value::list(out))
}

fn split_value(pat: &Rc<Pat>, subj: Rc<Subject>, maxsplit: i64) -> R<Value> {
    let n = subj.chars.len();
    let mut run = Run::new(pat, subj.clone(), 0, n)?;
    let max = if maxsplit < 0 {
        0
    } else if maxsplit == 0 {
        usize::MAX
    } else {
        maxsplit as usize
    };
    let recs = run.scan(max)?;
    let mut out: Vec<Value> = Vec::with_capacity(recs.len() + 1);
    let mut last = 0u32;
    for spans in &recs {
        out.push(Value::Str(subj.slice(last, spans[0])));
        // A group that did not participate keeps its `None` here — the one
        // API that does not render it as `''`.
        for g in 1..spans.len() / 2 {
            let (a, b) = (spans[g * 2], spans[g * 2 + 1]);
            out.push(if a == NOMARK || b == NOMARK {
                Value::None
            } else {
                Value::Str(subj.slice(a, b))
            });
        }
        last = spans[1];
    }
    out.push(Value::Str(subj.slice(last, n as u32)));
    Ok(crate::value::list(out))
}

fn sub_value(
    it: &mut Interp,
    pat: &Rc<Pat>,
    repl: &Value,
    subj: Rc<Subject>,
    count: i64,
    want_n: bool,
) -> R<Value> {
    let n = subj.chars.len();
    let mut run = Run::new(pat, subj.clone(), 0, n)?;
    let max = if count < 0 {
        0
    } else if count == 0 {
        usize::MAX
    } else {
        count as usize
    };
    let recs = run.scan(max)?;
    let tpl = match repl {
        Value::Str(t) => Some(parse_template(t, pat.groups)?),
        Value::Bytes(_) => return Err(refuse("bytes pattern or subject (re over bytes)")),
        _ => None,
    };
    let mut out = String::with_capacity(subj.text.len());
    let mut last = 0u32;
    let total = recs.len();
    for spans in recs {
        out.push_str(&subj.slice(last, spans[0]));
        last = spans[1];
        let m = MatchObj {
            pat: pat.clone(),
            subj: subj.clone(),
            spans,
            pos: 0,
            endpos: n as u32,
        };
        match &tpl {
            Some(t) => out.push_str(&expand(t, &m)),
            None => {
                let mut args = Args::one(Value::Match(Rc::new(m)));
                match it.call(repl, &mut args, Vec::new())? {
                    Value::Str(s) => out.push_str(&s),
                    Value::None => {}
                    other => {
                        return Err(refuse(&format!(
                            "a repl function that returned a {}, which CPython answers with a \
                             TypeError",
                            type_name(&other)
                        )))
                    }
                }
            }
        }
    }
    out.push_str(&subj.slice(last, n as u32));
    let s = Value::Str(out.into());
    Ok(if want_n {
        Value::Tuple(Rc::new(vec![s, Value::Int(total as i64)]))
    } else {
        s
    })
}

/// Every matcher-backed module function. `escape` and `purge` are answered in
/// [`call`] above; this is the half that needed an engine.
fn matcher_call(
    it: &mut Interp,
    name: &str,
    args: &mut Args,
    kw: &[(Rc<str>, Value)],
) -> R<Value> {
    let disp = format!("re.{name}");
    match name {
        "compile" => {
            let a = bind(&disp, args, kw, &["pattern", "flags"])?;
            let flags = as_flags(a[1].as_ref())?;
            let p = pattern_arg(&need(&a[0], &disp, "pattern")?, flags, &disp)?;
            Ok(Value::Pattern(p))
        }
        "search" | "match" | "fullmatch" | "findall" | "finditer" => {
            let a = bind(&disp, args, kw, &["pattern", "string", "flags"])?;
            let flags = as_flags(a[2].as_ref())?;
            let pat = pattern_arg(&need(&a[0], &disp, "pattern")?, flags, &disp)?;
            let subj = Subject::new(as_text(
                &need(&a[1], &disp, "string")?,
                &format!("{disp}() subject"),
            )?);
            let n = subj.chars.len();
            read_only(&pat, subj, 0, n, api_of(name))
        }
        "sub" | "subn" => {
            let a = bind(&disp, args, kw, &["pattern", "repl", "string", "count", "flags"])?;
            let flags = as_flags(a[4].as_ref())?;
            let pat = pattern_arg(&need(&a[0], &disp, "pattern")?, flags, &disp)?;
            let repl = need(&a[1], &disp, "repl")?;
            let subj = Subject::new(as_text(
                &need(&a[2], &disp, "string")?,
                &format!("{disp}() subject"),
            )?);
            let count = match a[3].as_ref() {
                None | Some(Value::None) => 0,
                Some(v) => as_index(v, &format!("{disp}() count"))?,
            };
            sub_value(it, &pat, &repl, subj, count, name == "subn")
        }
        "split" => {
            let a = bind(&disp, args, kw, &["pattern", "string", "maxsplit", "flags"])?;
            let flags = as_flags(a[3].as_ref())?;
            let pat = pattern_arg(&need(&a[0], &disp, "pattern")?, flags, &disp)?;
            let subj = Subject::new(as_text(
                &need(&a[1], &disp, "string")?,
                &format!("{disp}() subject"),
            )?);
            let maxsplit = match a[2].as_ref() {
                None | Some(Value::None) => 0,
                Some(v) => as_index(v, &format!("{disp}() maxsplit"))?,
            };
            split_value(&pat, subj, maxsplit)
        }
        other => Err(refuse(&format!("re.{other}()"))),
    }
}

fn api_of(name: &str) -> Api {
    match name {
        "match" => Api::Match,
        "fullmatch" => Api::Full,
        "findall" => Api::FindAll,
        "finditer" => Api::FindIter,
        _ => Api::Search,
    }
}

// ---- re.Pattern -----------------------------------------------------------

/// Methods, sorted; binary-searched by [`get_attr`].
const PATTERN_METHODS: &[&str] = &[
    "findall", "finditer", "fullmatch", "match", "search", "split", "sub", "subn",
];
/// Methods, sorted. `groupdict` and `expand` need named groups; `lastindex`,
/// `lastgroup` and `regs` are their own slice — each refuses, and the ROUTER
/// blocks them statically so the program never starts here. The computed
/// attributes (`pattern`, `flags`, `groups`; `string`, `re`, `pos`, `endpos`)
/// are named in `get_attr` itself, because they are values rather than bindings.
const MATCH_METHODS: &[&str] = &["end", "group", "groups", "span", "start"];
pub fn pattern_method(
    it: &mut Interp,
    p: &Rc<Pat>,
    name: &str,
    args: &mut Args,
    kw: &[(Rc<str>, Value)],
) -> R<Value> {
    let disp = format!("re.Pattern.{name}");
    match name {
        "search" | "match" | "fullmatch" | "findall" | "finditer" => {
            let a = bind(&disp, args, kw, &["string", "pos", "endpos"])?;
            let subj = Subject::new(as_text(
                &need(&a[0], &disp, "string")?,
                &format!("{disp}() subject"),
            )?);
            let n = subj.chars.len();
            let pos = clamp(a[1].as_ref(), &format!("{disp}() pos"), n, 0)?;
            let endpos = clamp(a[2].as_ref(), &format!("{disp}() endpos"), n, n)?;
            read_only(p, subj, pos, endpos, api_of(name))
        }
        "sub" | "subn" => {
            let a = bind(&disp, args, kw, &["repl", "string", "count"])?;
            let repl = need(&a[0], &disp, "repl")?;
            let subj = Subject::new(as_text(
                &need(&a[1], &disp, "string")?,
                &format!("{disp}() subject"),
            )?);
            let count = match a[2].as_ref() {
                None | Some(Value::None) => 0,
                Some(v) => as_index(v, &format!("{disp}() count"))?,
            };
            sub_value(it, p, &repl, subj, count, name == "subn")
        }
        "split" => {
            let a = bind(&disp, args, kw, &["string", "maxsplit"])?;
            let subj = Subject::new(as_text(
                &need(&a[0], &disp, "string")?,
                &format!("{disp}() subject"),
            )?);
            let maxsplit = match a[1].as_ref() {
                None | Some(Value::None) => 0,
                Some(v) => as_index(v, &format!("{disp}() maxsplit"))?,
            };
            split_value(p, subj, maxsplit)
        }
        other => Err(refuse(&format!("re.Pattern.{other}()"))),
    }
}

// ---- re.Match -------------------------------------------------------------

pub fn match_method(
    m: &Rc<MatchObj>,
    name: &str,
    args: &mut Args,
    kw: &[(Rc<str>, Value)],
) -> R<Value> {
    let disp = format!("re.Match.{name}");
    match name {
        "group" => {
            if !kw.is_empty() {
                return Err(refuse(&format!("{disp}() with keyword arguments")));
            }
            match args.len() {
                0 => Ok(m.group_value(0)),
                1 => {
                    let g = m.index_of(&args[0], &format!("{disp}() index"))?;
                    Ok(m.group_value(g))
                }
                _ => {
                    let mut out = Vec::with_capacity(args.len());
                    for v in args.iter() {
                        let g = m.index_of(v, &format!("{disp}() index"))?;
                        out.push(m.group_value(g));
                    }
                    Ok(Value::Tuple(Rc::new(out)))
                }
            }
        }
        "groups" => {
            let a = bind(&disp, args, kw, &["default"])?;
            let dflt = a[0].clone().unwrap_or(Value::None);
            let mut out = Vec::with_capacity(m.n().saturating_sub(1));
            for g in 1..m.n() {
                out.push(match m.group_value(g) {
                    Value::None => dflt.clone(),
                    v => v,
                });
            }
            Ok(Value::Tuple(Rc::new(out)))
        }
        "start" | "end" | "span" => {
            if !kw.is_empty() {
                return Err(refuse(&format!("{disp}() with keyword arguments")));
            }
            if args.len() > 1 {
                return Err(refuse(&format!(
                    "{disp}() with {} positional arguments",
                    args.len()
                )));
            }
            let g = match args.first() {
                None => 0,
                Some(v) => m.index_of(v, &format!("{disp}() index"))?,
            };
            let (a, b) = m.span_of(g);
            let (a, b) = if a == NOMARK || b == NOMARK {
                (-1i64, -1i64)
            } else {
                (a as i64, b as i64)
            };
            Ok(match name {
                "start" => Value::Int(a),
                "end" => Value::Int(b),
                _ => Value::Tuple(Rc::new(vec![Value::Int(a), Value::Int(b)])),
            })
        }
        other => Err(refuse(&format!("re.Match.{other}()"))),
    }
}

/// `m[0]`, `m[1]` — `Match.__getitem__`, which is `group()` and whose failures
/// are CPython's IndexError text.
pub fn match_index(m: &Rc<MatchObj>, idx: &Value) -> R<Value> {
    let g = m.index_of(idx, "re.Match[] index")?;
    Ok(m.group_value(g))
}

/// Attribute access on the two new values. Every name this engine does not
/// answer refuses rather than raising AttributeError: CPython answers
/// `.groupdict()`, `.expand()`, `.lastindex` and `.groupindex`, and an
/// AttributeError here is exit 1 — the program's own exit, which the chain
/// never retries.
pub fn get_attr(base: &Value, name: &str) -> R<Value> {
    match base {
        Value::Pattern(p) => Ok(match name {
            "pattern" => Value::Str(p.source.clone()),
            // A plain INT, not a RegexFlag: `Pattern.flags` is a C member,
            // so `print(p.flags)` is `34` where `print(re.I|re.U)` is
            // `re.IGNORECASE|re.UNICODE`. `p.flags & re.I` still answers a
            // flag, because the RIGHT operand's subclass wins there.
            "flags" => Value::Int(p.flags as i64),
            "groups" => Value::Int(p.groups as i64),
            other => match PATTERN_METHODS.binary_search(&other) {
                Ok(i) => Value::Bound(Rc::new(base.clone()), PATTERN_METHODS[i]),
                Err(_) => return Err(refuse(&format!("re.Pattern.{other}"))),
            },
        }),
        Value::Match(m) => Ok(match name {
            "string" => Value::Str(m.subj.text.clone()),
            "re" => Value::Pattern(m.pat.clone()),
            "pos" => Value::Int(m.pos as i64),
            "endpos" => Value::Int(m.endpos as i64),
            other => match MATCH_METHODS.binary_search(&other) {
                Ok(i) => Value::Bound(Rc::new(base.clone()), MATCH_METHODS[i]),
                Err(_) => return Err(refuse(&format!("re.Match.{other}"))),
            },
        }),
        _ => Err(refuse(name)),
    }
}

/// `repr(p)` and `repr(m)`, which are also `str()`, `print()` and `'%s' % …`.
///
/// Both truncate with CPython's `%.NR` conversion, and the detail that gives it
/// away is where: `%.50R` takes the first 50 CHARACTERS OF THE REPR, so a
/// 50-character match prints 49 characters and NO closing quote, and 30 tabs
/// print 24 `\t` escapes and a lone backslash. Truncating the string before
/// reprring it would keep the quote and be wrong every time it fired.
pub fn repr(v: &Value) -> R<String> {
    fn cut(s: &str, n: usize) -> String {
        s.chars().take(n).collect()
    }
    match v {
        Value::Match(m) => {
            let (a, b) = m.span_of(0);
            let inner = crate::fmt::str_repr(&m.group_text(0))?;
            Ok(format!(
                "<re.Match object; span=({a}, {b}), match={}>",
                cut(&inner, 50)
            ))
        }
        Value::Pattern(p) => {
            // UNICODE is dropped from a str pattern's repr — and only when it
            // is the ONLY one of the three character-set flags — while
            // `.flags` still reports it.
            let mut flags = p.flags;
            if flags & (L | U | A) == U {
                flags &= !U;
            }
            // BIT order here, which is NOT the declaration order `RegexFlag`'s
            // own repr uses: `re.I|re.A` prints `re.ASCII|re.IGNORECASE` as a
            // flag and `re.IGNORECASE|re.ASCII` inside `re.compile(…)`.
            let mut items = String::new();
            for (bit, name) in [
                (I, "IGNORECASE"),
                (M, "MULTILINE"),
                (S, "DOTALL"),
                (U, "UNICODE"),
                (X, "VERBOSE"),
                (A, "ASCII"),
            ] {
                if flags & bit != 0 {
                    if !items.is_empty() {
                        items.push('|');
                    }
                    items.push_str("re.");
                    items.push_str(name);
                }
            }
            let src = cut(&crate::fmt::str_repr(&p.source)?, 200);
            Ok(if items.is_empty() {
                format!("re.compile({src})")
            } else {
                format!("re.compile({src}, {items})")
            })
        }
        other => Err(refuse(&format!("repr() of a {}", type_name(other)))),
    }
}

// ---- the generic paths ----------------------------------------------------

/// `==` between the new values. A Pattern compares by its text and its
/// EFFECTIVE flags; a Match compares by identity, so two matches of the same
/// text are not equal — which is CPython's answer and the opposite of what a
/// derived `PartialEq` would give.
pub fn eq(a: &Value, b: &Value) -> Option<bool> {
    match (a, b) {
        (Value::Pattern(x), Value::Pattern(y)) => {
            Some(x.source == y.source && x.flags == y.flags)
        }
        (Value::Match(x), Value::Match(y)) => Some(Rc::ptr_eq(x, y)),
        (Value::Pattern(_) | Value::Match(_), _) | (_, Value::Pattern(_) | Value::Match(_)) => {
            Some(false)
        }
        _ => None,
    }
}

/// The guard every generic operator path takes: ordering, arithmetic, `in`,
/// slicing. CPython answers each of these with a TypeError whose text names
/// `re.Pattern` or `re.Match`; the refusal is what keeps that from being an
/// exit 1 here, which the chain never retries.
pub fn guard_operand(a: &Value, b: &Value, what: &str) -> R<()> {
    for v in [a, b] {
        if let Value::Pattern(_) | Value::Match(_) = v {
            return Err(refuse(&format!("{what} on a {}", type_name(v))));
        }
    }
    Ok(())
}

pub fn guard_one(v: &Value, what: &str) -> R<()> {
    if let Value::Pattern(_) | Value::Match(_) = v {
        return Err(refuse(&format!("{what} a {}", type_name(v))));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `[re.escape(chr(i)) for i in range(32, 127)]` on CPython 3.14.5,
    /// identical on 3.11.15, 3.12.13 and 3.13.13 — the trap-grid row, verbatim.
    const CPYTHON_ESCAPE_32_TO_127: &[&str] = &[
        "\\ ", "!", "\"", "\\#", "\\$", "%", "\\&", "'", "\\(", "\\)", "\\*", "\\+", ",",
        "\\-", "\\.", "/", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", ":", ";", "<",
        "=", ">", "\\?", "@", "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L",
        "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z", "\\[",
        "\\\\", "\\]", "\\^", "_", "`", "a", "b", "c", "d", "e", "f", "g", "h", "i", "j",
        "k", "l", "m", "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z",
        "\\{", "\\|", "\\}", "\\~",
    ];

    #[test]
    fn escape_is_cpythons_over_printable_ascii() {
        assert_eq!(CPYTHON_ESCAPE_32_TO_127.len(), 95);
        for (i, want) in (32u8..127).zip(CPYTHON_ESCAPE_32_TO_127) {
            assert_eq!(escape(&(i as char).to_string()), *want, "chr({i})");
        }
    }

    #[test]
    fn escape_copies_everything_else_through() {
        assert_eq!(escape(""), "");
        assert_eq!(escape("a1_\u{e9}\x00\x07\x7f\u{80}\u{a0}\u{65e5}"), "a1_\u{e9}\x00\x07\x7f\u{80}\u{a0}\u{65e5}");
        assert_eq!(escape("\t\n\r\x0b\x0c"), "\\\t\\\n\\\r\\\x0b\\\x0c");
        assert_eq!(escape("a.b*c?"), "a\\.b\\*c\\?");
        assert_eq!(escape("\\"), "\\\\");
    }

    #[test]
    fn flag_repr_joins_members_in_declaration_order() {
        let ok = |b: u32| flag_repr(b).ok();
        assert_eq!(ok(0).as_deref(), Some("re.NOFLAG"));
        assert_eq!(ok(I).as_deref(), Some("re.IGNORECASE"));
        assert_eq!(ok(I | M).as_deref(), Some("re.IGNORECASE|re.MULTILINE"));
        // ASCII is declared first and prints first, whatever its bit.
        assert_eq!(
            ok(I | M | S | X | A).as_deref(),
            Some("re.ASCII|re.IGNORECASE|re.MULTILINE|re.DOTALL|re.VERBOSE")
        );
        assert_eq!(
            ok(I | L | U | DEBUG).as_deref(),
            Some("re.IGNORECASE|re.LOCALE|re.UNICODE|re.DEBUG")
        );
        assert_eq!(ok(I | 512 | 1024).as_deref(), Some("re.IGNORECASE|0x600"));
        assert_eq!(ok(I | M | 1024).as_deref(), Some("re.IGNORECASE|re.MULTILINE|0x400"));
        assert_eq!(ok(I | 1 << 31).as_deref(), Some("re.IGNORECASE|0x80000000"));
        // Residue with no named member is the constructor form, in decimal —
        // `re.NOFLAG | 512` and `re.NOFLAG | 2**31` on CPython 3.11 through 3.14.
        assert_eq!(ok(512).as_deref(), Some("re.RegexFlag(512)"));
        assert_eq!(ok(1 << 31).as_deref(), Some("re.RegexFlag(2147483648)"));
        // The TEMPLATE bit is version-shaped and refuses.
        assert!(flag_repr(1).is_err());
        assert!(flag_repr(I | 1).is_err());
    }

    #[test]
    fn the_binary_searched_tables_are_sorted() {
        assert!(MODULE_METHODS.windows(2).all(|w| w[0] < w[1]));
        assert!(ROUTED_METHODS.windows(2).all(|w| w[0] < w[1]));
        assert!(FLAGS.windows(2).all(|w| w[0].0 < w[1].0));
        assert!(MATCHER_FNS.windows(2).all(|w| w[0] < w[1]));
        assert!(PATTERN_METHODS.windows(2).all(|w| w[0] < w[1]));
        assert!(MATCH_METHODS.windows(2).all(|w| w[0] < w[1]));
        assert_eq!(FLAG_NAMES.iter().fold(0, |acc, (b, _)| acc | b), NAMED_BITS);
    }

    /// The router's optimistic union has to be exactly the names the engine
    /// SERVES. A name in it that refuses is a wasted in-process run; a name
    /// missing from it is a program blocked on `.group()` before it starts.
    #[test]
    fn the_router_admits_exactly_the_names_the_engine_serves() {
        let served: Vec<&str> = PATTERN_METHODS
            .iter()
            .chain(MATCH_METHODS.iter())
            .chain(["flags", "groups", "pattern", "string", "re", "pos", "endpos"].iter())
            .copied()
            .collect();
        for n in &served {
            assert!(known_method(n), "the router does not admit {n}");
        }
        for n in ROUTED_METHODS {
            assert!(served.contains(n), "the router admits {n}, which nothing serves");
        }
        for n in ["groupdict", "expand", "lastindex", "lastgroup", "regs", "groupindex",
                  "scanner"] {
            assert!(!known_method(n), "{n} is a later slice and must block statically");
        }
        // Every matcher function is a served module name, so the walker's
        // pattern check and `module_attr` cannot drift apart.
        for f in MATCHER_FNS {
            assert!(module_attr(f).is_ok(), "{f}");
            assert!(is_matcher(f));
        }
        assert!(!is_matcher("escape") && !is_matcher("purge"));
    }

    /// The pattern text inside a refusal detail must be ONE line whatever the
    /// pattern holds — invariant 2 — and must still read as what was typed.
    #[test]
    fn a_pattern_in_a_refusal_stays_on_one_line() {
        assert_eq!(show("a"), "'a'");
        assert_eq!(show("\\d+"), "'\\d+'");
        assert_eq!(show("a\nb"), "'a\\nb'");
        assert_eq!(show("a\rb\tc"), "'a\\rb\\tc'");
        assert_eq!(show("a\x00b"), "'a\\x00b'");
        assert_eq!(show("it's"), "'it's'");
        assert_eq!(show(&"x".repeat(80)), format!("'{}…'", "x".repeat(60)));
        for src in ["a\nb", "a\rb", &"x".repeat(200), "\x1b[0m"] {
            assert!(!show(src).contains('\n') && !show(src).contains('\r'), "{src:?}");
        }
    }

    fn spans(pat: &str, s: &str) -> Vec<(u32, u32)> {
        let p = Rc::new(build(&Rc::from(pat), 0).expect(pat));
        let subj = Subject::new(Rc::from(s));
        let n = subj.chars.len();
        let mut run = Run::new(&p, subj, 0, n).expect(pat);
        run.scan(usize::MAX)
            .expect(pat)
            .into_iter()
            .map(|r| (r[0], r[1]))
            .collect()
    }

    /// The 3.7+ empty-match rule, which is the whole of the scan loop: an empty
    /// match immediately after a NON-empty one at the same position is
    /// produced, two empty ones at a position are not, and the attempt after an
    /// empty match starts a character later. Every one of these is a CPython
    /// 3.14.5 output, and the pre-3.7 rule gets three of them wrong.
    #[test]
    fn the_scan_loop_is_sres() {
        assert_eq!(spans("a*", "baaac"), [(0, 0), (1, 4), (4, 4), (5, 5)]);
        assert_eq!(spans("x*", "axxb"), [(0, 0), (1, 3), (3, 3), (4, 4)]);
        assert_eq!(spans("", "abc"), [(0, 0), (1, 1), (2, 2), (3, 3)]);
        assert_eq!(spans("a|", "ab"), [(0, 1), (1, 1), (2, 2)]);
        assert_eq!(spans("|a", "ab"), [(0, 0), (0, 1), (1, 1), (2, 2)]);
        assert_eq!(spans("ab", "abab"), [(0, 2), (2, 4)]);
        assert_eq!(spans("a", ""), []);
    }

    /// `sre`'s repeat rules, where an engine written from first principles
    /// disagrees: an iteration that ends where it started stops the loop and is
    /// still RECORDED, captures survive across iterations and are undone on
    /// backtracking, and alternation is leftmost-first rather than longest.
    #[test]
    fn the_repeat_and_capture_rules_are_sres() {
        let groups = |pat: &str, s: &str| -> Vec<Option<(u32, u32)>> {
            let p = Rc::new(build(&Rc::from(pat), 0).expect(pat));
            let subj = Subject::new(Rc::from(s));
            let n = subj.chars.len();
            let mut run = Run::new(&p, subj, 0, n).expect(pat);
            let rec = run.once(0, false, false).expect(pat).expect(pat);
            rec[2..]
                .chunks(2)
                .map(|c| (c[0] != NOMARK).then_some((c[0], c[1])))
                .collect()
        };
        // `(a*)*` on "aa" is ('',) — the empty second iteration is recorded.
        assert_eq!(groups("(a*)*", "aa"), [Some((2, 2))]);
        assert_eq!(groups("(a*)+", "b"), [Some((0, 0))]);
        assert_eq!(groups("(a?){3}", "aa"), [Some((2, 2))]);
        // A group that did not participate in the LAST iteration keeps its
        // earlier value…
        assert_eq!(groups("(a|b)*", "ab"), [Some((1, 2))]);
        assert_eq!(groups("(?:(a)|(b))+", "aba"), [Some((2, 3)), Some((1, 2))]);
        // …and a capture made on a path that failed is undone.
        assert_eq!(groups("(a)?(?:ab)", "ab"), [None]);
        assert_eq!(groups("(?:(a)b|ac)", "ac"), [None]);
        // Leftmost-first, not longest.
        assert_eq!(spans("a|ab", "ab"), [(0, 1)]);
        assert_eq!(spans("ab|a", "ab"), [(0, 2)]);
    }

    /// The budget REFUSES; it never answers `None`. And the shapes `_sre`
    /// answers in milliseconds must answer here too — a counted one-character
    /// repeat is what makes them linear.
    #[test]
    fn the_budget_refuses_the_exponential_and_answers_the_linear() {
        let m = |pat: &str, s: &str| -> R<Option<Vec<u32>>> {
            let p = Rc::new(build(&Rc::from(pat), 0)?);
            let subj = Subject::new(Rc::from(s));
            let n = subj.chars.len();
            Run::new(&p, subj, 0, n)?.once(0, false, false)
        };
        let long = "a".repeat(100_000);
        for (pat, s) in [
            ("(?:a|b)*c", format!("{}c", "ab".repeat(200_000))),
            ("(a+)+b", format!("{long}b")),
            ("(.*)*b", format!("{long}b")),
            ("(a|a)*b", format!("{long}b")),
            ("^(\\w+\\s?)*$", "a ".repeat(50_000)),
            ("(?:x+x+)+y", format!("{}y", "x".repeat(100_000))),
        ] {
            assert!(m(pat, &s).expect(pat).is_some(), "{pat} must answer");
        }
        for (pat, s) in [
            ("(a+)+$", format!("{}b", "a".repeat(30))),
            ("(a*)*$", format!("{}b", "a".repeat(28))),
            ("(x+x+)+y", "x".repeat(28)),
        ] {
            let e = m(pat, &s).expect_err(pat);
            let msg = format!("{e}");
            assert!(msg.contains("budget") || msg.contains("stack"), "{pat}: {msg}");
        }
    }

    #[test]
    fn module_attr_answers_the_surface_and_refuses_the_rest() {
        assert!(matches!(module_attr("I"), Ok(Value::ReFlag(2))));
        assert!(matches!(module_attr("IGNORECASE"), Ok(Value::ReFlag(2))));
        assert!(matches!(module_attr("NOFLAG"), Ok(Value::ReFlag(0))));
        assert!(matches!(module_attr("escape"), Ok(Value::Bound(_, "escape"))));
        assert!(matches!(module_attr("search"), Ok(Value::Bound(_, "search"))));
        for name in ["error", "PatternError", "T", "TEMPLATE", "RegexFlag", "Pattern", "Match",
                     "Scanner", "template", "__version__", "nosuchthing"] {
            assert!(module_attr(name).is_err(), "{name}");
        }
        assert!(known_method("group") && known_method("span") && !known_method("groupindex"));
    }
}
