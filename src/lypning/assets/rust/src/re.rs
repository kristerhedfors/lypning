//! `re` — the module SURFACE, which is the whole of the `cap-re` capability:
//! compiled into `lypning-l` and into nothing smaller. Every line of this file,
//! and every line that reaches it, is behind `cfg(feature = "cap-re")`, so the
//! `lypning` binary does not move a byte for it.
//!
//! There is NO regular-expression matcher here. What there is: the module, so
//! the router admits a program that imports it; the flag constants as a value
//! of their own (`Value::ReFlag`) with CPython's repr; `re.escape` and
//! `re.purge`, the two functions that need no matcher; and the NAMES of the
//! nine matcher-backed functions, each of which refuses at call time with a
//! stable detail (`re: re.search() (pattern matching is not served yet)`) that
//! `lypning routes --plan` and `conformance --plan` rank per function. That is
//! the matcher's build order, measured rather than guessed.
//!
//! Why the surface alone is a variant step: the corpus mine of 2026-09-04
//! (3,525 entries loaded; 248 blocked on `module: import re` for lypning-l, 213
//! of them with no other static blocker) ran those 213 in the battery's own
//! temp cwd. 174 fail identically on both engines BEFORE their first regex call
//! (167 on a repo-relative file that is not there, 7 on `sys.argv`), 8 time out
//! on both, 6 refuse at runtime on `eval`/`dir`/`type`, and 25 ever reach a
//! matcher. Admitting the names is what the 174 need, and 94 of them never
//! call a matcher function at all. Quote the counts a conformance run prints,
//! with its date — not these.
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
//! **The refusals are the design, not the leftovers.** A refusal is never a
//! bug (invariant 1); an exit 1 where CPython answers is — and it is the
//! defect three capabilities in a row shipped: a new `Value` variant reached
//! through a path that had no arm for it. So every path a value can take —
//! `==`, `is`, hashing, ordering, `str`/`repr`, `bool`, `int`, `len`, `iter`,
//! `json.dumps`, attribute access, every operator — has an arm for
//! `Value::ReFlag`, and each is exact or a refusal, never a fall-through.

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
/// (`route::re_method`). Sorted; binary-searched. `groupindex` and `scanner`
/// are deliberately absent: a shape the engine will never serve is cheaper as a
/// static block than as a runtime refusal.
const ROUTED_METHODS: &[&str] = &[
    "end", "endpos", "expand", "findall", "finditer", "flags", "fullmatch", "group", "groupdict",
    "groups", "lastgroup", "lastindex", "match", "pattern", "pos", "re", "regs", "search", "span",
    "split", "start", "string", "sub", "subn",
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
pub fn call(_it: &mut Interp, name: &str, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
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
            Ok(Value::None)
        }
        other => Err(refuse(&format!("re.{other}() (pattern matching is not served yet)"))),
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
/// Called FIRST in `Interp::binop`, before the numeric fast path — which would
/// otherwise answer `re.I | re.M` as `10`, at exit 0.
pub fn binop(op: BinOp, a: &Value, b: &Value) -> R<Option<Value>> {
    if !matches!(a, Value::ReFlag(_)) && !matches!(b, Value::ReFlag(_)) {
        return Ok(None);
    }
    if !matches!(op, BinOp::BitOr | BinOp::BitAnd | BinOp::BitXor) {
        return Ok(None);
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
        assert_eq!(FLAG_NAMES.iter().fold(0, |acc, (b, _)| acc | b), NAMED_BITS);
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
