//! `base64` — the whole of the `cap-base64` capability, compiled into
//! `lypning-l` and into nothing smaller. Every line of this file, and every
//! line that reaches it, is behind `cfg(feature = "cap-base64")`.
//!
//! **The invariant this module exists to hold: `b64decode` DISCARDS what is not
//! in the alphabet, and it is the discarding that decides the padding.** A
//! naive decoder raises on `b"a!Gk="` and on `b"aG k="`; CPython answers
//! `b'hi'` for both, because `binascii.a2b_base64` in its default non-strict
//! mode drops every byte outside the 64-character alphabet before it counts
//! anything. A decoder that got that wrong would raise where CPython answers —
//! exit 1, which the chain never retries — and, worse, would ANSWER where
//! CPython raises, at exit 0.
//!
//! **`Value::Bytes` in, `Value::Bytes` out. There is no new `Value` variant**,
//! so no arm of `ops.rs`, `fmt.rs`, `value.rs`, `json.rs`, `iter.rs` or
//! `builtins.rs` has to remember this capability exists. Five capabilities in a
//! row shipped a variant that reached print, `==`, hash, `in`, `len`, indexing,
//! slice assignment and `%`-formatting through an arm nobody remembered
//! (`docs/HILLCLIMB.md` iterations 74, 76 and 77). `bytes` is what CPython's
//! own functions return, so the exact shape is also the free one.
//!
//! # The decode rule, measured rather than recalled
//!
//! **The rule is a STATE MACHINE, not a count over the whole input.** Carry
//! `quad_pos` (how many alphabet bytes of the current quad have been read, 0–3)
//! and `pads` (how many `=` since the last alphabet byte). For each input byte:
//!
//!   1. `=` — if `quad_pos >= 2`, increment `pads`; `quad_pos + pads >= 4`
//!      CLOSES the quad, and that byte is where the decode ends. A `=` while
//!      `quad_pos` is 0 or 1 does nothing at all: it is not counted and it is
//!      not an error, which is why `b"===="` is `b''`.
//!   2. a byte outside `A-Za-z0-9+/` — discarded, and it does NOT reset `pads`.
//!      Whitespace, punctuation, NUL and every byte above 127 are simply gone,
//!      so `b"aa=\n="` is a two-character quad closed by two pads: `b'i'`.
//!   3. an alphabet byte — reset `pads` to 0, fold its six bits in, emit a byte
//!      on every quad position but the first, and advance `quad_pos`.
//!
//! At the end of the input, `quad_pos == 0` succeeds; `quad_pos == 1` is the
//! `"number of data characters (N) cannot be 1 more than a multiple of 4"`
//! error; 2 or 3 with too few pads is `"Incorrect padding"`. Both errors are a
//! `binascii.Error` whose message text is CPython's and whose CLASS this engine
//! does not have, so both refuse — and [`data_block`] decides them in the WALK
//! for every literal, so the refusal lands before the program starts rather
//! than past a committed write barrier.
//!
//! # Where the CPythons disagree, and why clause 1 ends in a refusal
//!
//! **`Modules/binascii.c` changed what clause 1 does, and both answers are
//! live.** Read on 2026-09-12 from the branch tips:
//!
//! | branch | lenient `=` that closes a quad | `b64decode(b"AA==AA==")` |
//! |---|---|---|
//! | 3.11, 3.12 | `quad_pos >= 2 && quad_pos + ++pads >= 4` → `goto done` | `b'\x00'` |
//! | 3.13, 3.14, `main` | ignored like any non-alphabet byte; the verdict is taken at the end from `quad_pos != 0 && quad_pos + pads < 4` | `b'\x00\x00\x00'` |
//!
//! So `b"aGk=aGk="` is `b'hi'` on 3.11 and 3.12 and `Incorrect padding` on 3.13
//! and later, and `b"AAA=A"` is `b'\x00\x00'` on one pair and `b'\x00\x00\x00'`
//! on the other. The engine has ONE answer and cannot be right for both — so on
//! exactly these inputs it gives none, and the chain spends one spawn on the
//! interpreter that owns the question. `src/lypning/assets/micropython/lib/
//! README.md` records the same split from the other side.
//!
//! The two families agree **exactly** when no alphabet byte follows the pad
//! that closes a quad — checked by enumerating both models on 2026-09-12 over
//! 296,105 rows (exhaustive for every input of length 0–4 over `ABC=!-_+\n\0/`,
//! plus 280,000 random rows of length 5–17): zero rows where they disagreed and
//! this test did not say so, and zero rows where this engine answers and either
//! family says something else. That one-line lookahead is [`VERSION_SPLIT`],
//! and it is why `b"aGk="`, `b"aGk=\n"` and `b"aa=\n="` still answer while
//! `b"AA==AA=="` refuses.
//!
//! **What was here before was the 3.13+ rule with no stop and no refusal**, so
//! this engine answered `b'\x00\x00\x00'` for `b"AA==AA=="` and refused
//! `b"aGk=aGk="` on a 3.11 host — a MISMATCH at exit 0, which is invariant 1.
//!
//! # What is refused, and where each refusal is decided
//!
//! `altchars=`, `validate=` that is not literally falsy, a second positional
//! argument, a wrong argument count, a non-bytes literal handed to an encoder,
//! and a literal whose decode would raise are all decided STATICALLY, by
//! [`crate::route::base64_call_block`], from the walk. `route::base64_static_check`
//! runs that walk before the first statement of a `-c` run, so nothing here is
//! reached after a side effect at all — which is what kept these programs
//! answerable while `os.mkdir` still committed the barrier (`io.rs`, issue #51,
//! now fixed at the barrier itself).
//!
//! The walk decides the WHOLE program, so a base64 call the run would never
//! reach — one behind `if False:`, or the unused arm of a ternary — is decided
//! too, and such a program refuses where CPython answers. That is a coverage
//! loss and never a wrong answer: the chain hands it to CPython for one spawn.
//! `static_stop_check` has the identical property and for the identical reason,
//! and the alternative — deciding only what the run reaches — is the runtime
//! refusal, one spawn already spent, that this whole design exists to avoid.
//!
//! What is left at runtime is the residue no walk could read: an argument whose
//! VALUE is computed. That is one refusal kind (`base64`) and it is the
//! backstop, not the design — `tests/test_base64_grid.py::RUNTIME_BACKSTOP`
//! lists every shape that reaches it.
//!
//! `b16*`, `b32*`, `b32hex*`, `b85*`, `a85*`, `encodebytes`, `decodebytes`,
//! `standard_b64encode`/`standard_b64decode` and `binascii` are not served at
//! all: they refuse through `module-attr`, which `route::MODULE_ATTRS` makes a
//! static block in the CORE's walk — the binary that routes, and the one with
//! none of this file compiled into it.

use crate::args::Args;
use crate::err::{unsupported, LypningError, R};
use crate::value::Value;
use std::rc::Rc;

/// The names this module serves — `route::BASE64_SERVED` itself, not a copy of
/// it. The router answers the same question BEFORE this file is reached, out of
/// a table every variant carries, and two tables that must agree are one table.
use crate::route::BASE64_SERVED as SERVED;

pub fn refuse(what: &str) -> LypningError {
    unsupported("base64", what)
}

/// `base64.<name>` as a value. A served name is a bound module method; every
/// other name refuses with the kind the router blocks on statically.
pub fn module_attr(name: &str) -> R<Value> {
    match SERVED.iter().find(|n| **n == name) {
        Some(n) => Ok(Value::Bound(Rc::new(Value::Module("base64")), n)),
        None => Err(unsupported("module-attr", &format!("base64.{name}"))),
    }
}

/// The method names a `base64` program may reach that this engine answers on
/// no other type. Empty, and deliberately: every served function takes `bytes`
/// and answers `bytes`, so the only methods a caller uses afterwards are
/// `bytes`' own — which `route::known_method` already admits for every program.
/// The pathlib/re shape (`known_method` widened by an import) buys nothing here
/// and would widen the optimistic union for no reason.
pub fn call(_it: &mut crate::eval::Interp, name: &str, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    // Everything below is asked again by `route::base64_call_block` over a
    // literal, in the walk, so a program whose arguments the source spells is
    // refused before it starts. This is the backstop for the rest.
    let urlsafe = matches!(name, "urlsafe_b64encode" | "urlsafe_b64decode");
    let decoding = matches!(name, "b64decode" | "urlsafe_b64decode");
    if args.is_empty() {
        return Err(refuse(&format!("base64.{name}() with no argument")));
    }
    if args.len() > 1 {
        // The second positional is `altchars` on the two standard functions and
        // a TypeError on the urlsafe pair. Both refuse: `altchars` is a second
        // alphabet this engine does not implement, and the TypeError's wording
        // is CPython's.
        return Err(refuse(&format!("base64.{name}() with extra positional arguments")));
    }
    for (k, v) in kw {
        match crate::route::base64_kw_block(name, k, matches!(v, Value::None), falsy(v)) {
            Some(why) => return Err(refuse(&why)),
            None => {}
        }
    }
    let arg = args.first().expect("checked non-empty above");
    if decoding {
        let data = decode_input(name, arg)?;
        match scan(&data, urlsafe) {
            Ok(out) => Ok(Value::Bytes(Rc::new(out))),
            Err(why) => Err(refuse(why)),
        }
    } else {
        let Value::Bytes(b) = arg else {
            // CPython: `TypeError: a bytes-like object is required, not 'str'`.
            // Refused rather than raised — the wording is CPython's, and a
            // `str` here is the single most common way an agent's one-liner
            // gets this call wrong.
            return Err(refuse(&format!(
                "base64.{name}() over a {} (CPython requires a bytes-like object)",
                crate::value::type_name(arg)
            )));
        };
        Ok(Value::Bytes(Rc::new(encode(b, urlsafe))))
    }
}

/// Is this keyword's value falsy — the `validate=` question, and ONLY that one.
///
/// **`None` IS NOT AMONG THEM, and the comment that used to say so was reading
/// the wrong CPython.** On 3.9 `b64decode` tests `if validate and ...` in
/// Python, so `None` is merely falsy and the call returns. From 3.11 the body is
/// `return binascii.a2b_base64(s, strict_mode=validate)` and Argument Clinic
/// converts `strict_mode` as an `int`, not a `bool` — so `None` is
/// `TypeError: 'NoneType' object cannot be interpreted as an integer` and `0`
/// and `False` still decode. Measured on 3.11.15 this date, all five of
/// `False`, `0`, `None`, `''`, `b''`.
///
/// That is the same version split this module already declines everywhere else,
/// so it declines here too: `None` refuses and the two that every live CPython
/// agrees about are served. Answering `b'hi'` was right on 3.9 and a wrong
/// answer at exit 0 on every interpreter since.
///
/// `altchars=` asks a different question and gets a different predicate
/// (`matches!(v, Value::None)`, at the call above): its default is ABSENT, and
/// `None` is the only value that spells absent, because CPython's test is
/// `if altchars is not None`. A PRESENT falsy `altchars` is a value CPython
/// REJECTS — `TypeError` for `0` and `False`, `AssertionError` for `b""` and
/// `""` — and answering it here was a wrong answer at exit 0.
/// `route::base64_kw_block` holds both rules and the measurement.
fn falsy(v: &Value) -> bool {
    matches!(v, Value::Bool(false)) || matches!(v, Value::Int(i) if i.is_zero())
}

/// `base64._bytes_from_decode_data`, minus the two error paths it owns.
///
/// A `str` IS accepted by every decoder — that is not a coercion this engine
/// invented, it is `s.encode('ascii')` in `base64.py`, and the corpus reaches
/// it through `json.load(...)['content']`, which is always a `str`. A non-ASCII
/// `str` raises `ValueError('string argument should contain only ASCII
/// characters')` and anything that is not bytes-like raises a `TypeError`;
/// both messages are CPython's, so both refuse.
fn decode_input(name: &str, v: &Value) -> R<Vec<u8>> {
    match v {
        Value::Bytes(b) => Ok(b.as_ref().clone()),
        Value::Str(s) if s.is_ascii() => Ok(s.as_bytes().to_vec()),
        Value::Str(_) => Err(refuse(&format!(
            "base64.{name}() over a str holding a non-ASCII character (CPython raises a ValueError this engine does not word)"
        ))),
        v => Err(refuse(&format!(
            "base64.{name}() over a {} (CPython requires a bytes-like object or an ASCII str)",
            crate::value::type_name(v)
        ))),
    }
}

// ---- the alphabet ----------------------------------------------------------

const STD: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
const URL: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

/// This byte's 6-bit value in the STANDARD alphabet, or `None`.
///
/// The urlsafe pair does NOT get an alphabet of its own here, and that is
/// CPython's shape rather than a shortcut: `urlsafe_b64decode` translates `-`
/// to `+` and `_` to `/` and then calls `b64decode`, so `+` and `/` remain
/// valid input to it — `urlsafe_b64decode(b"+/++")` answers the same three
/// bytes `b"-_--"` does. [`translate`] is that step, applied before this one.
fn sextet(c: u8) -> Option<u8> {
    match c {
        b'A'..=b'Z' => Some(c - b'A'),
        b'a'..=b'z' => Some(c - b'a' + 26),
        b'0'..=b'9' => Some(c - b'0' + 52),
        b'+' => Some(62),
        b'/' => Some(63),
        _ => None,
    }
}

/// `s.translate(bytes.maketrans(b'-_', b'+/'))`, the whole of what
/// `urlsafe_b64decode` does before delegating.
fn translate(data: &[u8]) -> std::borrow::Cow<'_, [u8]> {
    if !data.iter().any(|c| matches!(c, b'-' | b'_')) {
        return std::borrow::Cow::Borrowed(data);
    }
    std::borrow::Cow::Owned(
        data.iter()
            .map(|c| match c {
                b'-' => b'+',
                b'_' => b'/',
                c => *c,
            })
            .collect(),
    )
}

/// The refusal for the one input class the live CPythons answer DIFFERENTLY —
/// see the module docstring's "Where the CPythons disagree". Not a
/// `binascii.Error`, and the detail says so: there is no single right answer
/// here to give, so the chain hands the program to the interpreter that owns
/// the question.
const VERSION_SPLIT: &str =
    "b64decode() over data carrying an alphabet character after the padding that closes a \
     quad (CPython 3.11 and 3.12 stop at that padding and 3.13 and later read straight \
     through it, so they answer differently)";

/// The whole decode: `binascii.a2b_base64(data, strict_mode=False)`'s C loop,
/// answering either the bytes it produces or the refusal line for the
/// `binascii.Error` it raises.
///
/// One function, so the WALK (`route::base64_call_block`, over a literal) and
/// the run ([`call`] above, over a computed value) cannot disagree about
/// whether an input decodes OR about what it decodes to — the previous split
/// into a "would it raise" predicate and a separate decoder is exactly where
/// the two halves of a wrong model can hide from each other.
///
/// The stop in the `=` arm is the rule; the module docstring states it and
/// [`tests::a_closing_pad_ends_the_decode_and_a_split_answer_is_refused`] pins
/// both the inputs a count-the-pads decoder gets wrong and the ones the live
/// CPythons answer differently.
///
/// Both errors are `binascii.Error`s whose message text is CPython's and whose
/// CLASS this engine does not have — so this is a refusal and never a raise.
/// The detail says what CPython would do rather than quoting it.
pub fn scan(data: &[u8], urlsafe: bool) -> Result<Vec<u8>, &'static str> {
    let data = if urlsafe { translate(data) } else { std::borrow::Cow::Borrowed(data) };
    let mut out = Vec::with_capacity(data.len() / 4 * 3 + 2);
    let mut quad_pos = 0u8;
    let mut pads = 0usize;
    let mut leftchar = 0u8;
    for (i, c) in data.iter().enumerate() {
        if *c == b'=' {
            // A pad counts only once the quad it closes holds two or three
            // characters. The pad that CLOSES it is where the two live CPython
            // families part company, so it is where this engine stops too —
            // with an answer when they agree and a refusal when they do not.
            if quad_pos >= 2 {
                pads += 1;
                if quad_pos as usize + pads >= 4 {
                    return match data[i + 1..].iter().any(|c| sextet(*c).is_some()) {
                        true => Err(VERSION_SPLIT),
                        false => Ok(out),
                    };
                }
            }
            continue;
        }
        let Some(v) = sextet(*c) else { continue };
        pads = 0;
        match quad_pos {
            0 => {
                quad_pos = 1;
                leftchar = v;
            }
            1 => {
                quad_pos = 2;
                out.push((leftchar << 2) | (v >> 4));
                leftchar = v & 0x0F;
            }
            2 => {
                quad_pos = 3;
                out.push((leftchar << 4) | (v >> 2));
                leftchar = v & 0x03;
            }
            _ => {
                quad_pos = 0;
                out.push((leftchar << 6) | v);
                leftchar = 0;
            }
        }
    }
    match quad_pos {
        0 => Ok(out),
        // Six bits cannot make a byte, whatever came before them.
        1 => Err(
            "b64decode() over data whose alphabet characters are 1 more than a multiple of 4 \
             (CPython raises a binascii.Error this engine does not word)",
        ),
        _ => Err(
            "b64decode() over data with incorrect padding (CPython raises a binascii.Error \
             this engine does not word)",
        ),
    }
}

/// Would CPython's `b64decode` RAISE on this input, and why? The walk's half of
/// [`scan`], and nothing more than its error — a literal the walk can read is
/// decided from the same loop the run would take.
pub fn data_block(data: &[u8], urlsafe: bool) -> Option<&'static str> {
    scan(data, urlsafe).err()
}

/// `binascii.b2a_base64(data, newline=False)` — always padded to a multiple of
/// four, and never line-wrapped, which is the difference between this and
/// `base64.encodebytes` (not served).
fn encode(data: &[u8], urlsafe: bool) -> Vec<u8> {
    let a = if urlsafe { URL } else { STD };
    let mut out = Vec::with_capacity((data.len() + 2) / 3 * 4);
    for chunk in data.chunks(3) {
        let b0 = chunk[0] as u32;
        let b1 = *chunk.get(1).unwrap_or(&0) as u32;
        let b2 = *chunk.get(2).unwrap_or(&0) as u32;
        let n = (b0 << 16) | (b1 << 8) | b2;
        out.push(a[(n >> 18) as usize & 63]);
        out.push(a[(n >> 12) as usize & 63]);
        out.push(if chunk.len() > 1 { a[(n >> 6) as usize & 63] } else { b'=' });
        out.push(if chunk.len() > 2 { a[n as usize & 63] } else { b'=' });
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dec(data: &[u8], urlsafe: bool) -> Vec<u8> {
        scan(data, urlsafe).expect("this row decodes under CPython")
    }

    /// The rule, as the inputs that separate it from the decoders it is easy to
    /// write instead — and from the OTHER live CPython.
    ///
    /// **The previous version of this test pinned an answer no interpreter on
    /// this host gives.** It asserted `decode(b"AA==AA==", false) == vec![0, 0,
    /// 0]`, which is 3.13-and-later's answer, in a tree whose grid is graded
    /// against whatever `python3` is installed — so on a 3.11 host it was a
    /// MISMATCH at exit 0 with a unit test standing over it. Every literal
    /// below was run under `python3 -c` on CPython 3.11.15 on 2026-09-12, and
    /// every refusal is either an error both families raise or the split the
    /// module docstring tabulates from the 3.11, 3.12, 3.13, 3.14 and `main`
    /// sources.
    #[test]
    fn a_closing_pad_ends_the_decode_and_a_split_answer_is_refused() {
        // A pad while the quad holds fewer than two characters is not counted
        // and is not an error, so a run of them is an EMPTY input…
        assert_eq!(dec(b"====", false), Vec::<u8>::new());
        assert_eq!(dec(b"", false), Vec::<u8>::new());
        // …and `pads` is reset only by an ALPHABET byte, never by a discarded
        // one, so the newline here does not break the pad run.
        assert_eq!(dec(b"aa=\n=", false), b"i".to_vec());
        assert_eq!(dec(b"aGk==", false), b"hi".to_vec());
        assert_eq!(dec(b"aGk=\n", false), b"hi".to_vec());
        // Everything outside the alphabet is gone before anything is counted.
        assert_eq!(dec(b"a!G k=", false), b"hi".to_vec());
        assert_eq!(dec(b"-_--", false), Vec::<u8>::new());
        assert_eq!(dec(b"-_--", true), vec![0xfb, 0xff, 0xbe]);
        // The two errors, which are refusals. A quad left holding one
        // character is the length error whatever else the input carries…
        assert!(data_block(b"a", false).is_some());
        assert!(data_block(b"A===", false).is_some());
        assert!(data_block(b"aGkxx", false).is_some());
        // …and two or three characters with no pad to close them is
        // "Incorrect padding".
        assert!(data_block(b"aGk", false).is_some());
        assert!(data_block(b"AA=", false).is_some());
        assert!(data_block(b"AB=C", false).is_some());
        assert!(data_block(b"=AA=", false).is_some());
        // The split: an alphabet byte AFTER the pad that closed a quad. 3.11
        // and 3.12 stop at that pad; 3.13 and later read through it. One
        // refusal each, and not the `binascii.Error` line — there is no error
        // here on any interpreter, only two different answers.
        for input in [
            &b"AA==AA=="[..],   // b'\x00'      on 3.11/3.12, b'\x00\x00\x00' on 3.13+
            &b"AB==CD=="[..],   // b'\x00'      / b'\x00\x10\x83'
            &b"AAA=A"[..],      // b'\x00\x00'  / b'\x00\x00\x00'
            &b"ABC=D"[..],      // b'\x00\x10'  / b'\x00\x10\x83'
            &b"AA==A"[..],      // b'\x00'      / Incorrect padding
            &b"aGk=aGk="[..],   // b'hi'        / Incorrect padding
            &b"aGk=aGk"[..],    // b'hi'        / Incorrect padding
            &b"AAA=AB"[..],     // b'\x00\x00'  / Incorrect padding
        ] {
            assert_eq!(data_block(input, false), Some(VERSION_SPLIT), "{input:?}");
        }
        // …and the lookahead is for an ALPHABET byte, so these still answer.
        assert_eq!(dec(b"aGk=!!!", false), b"hi".to_vec());
        assert_eq!(dec(b"aGk=====", false), b"hi".to_vec());
    }

    #[test]
    fn a_round_trip_over_every_length_up_to_a_quad_boundary() {
        for n in 0..64usize {
            let raw: Vec<u8> = (0..n).map(|i| (i * 37 + 11) as u8).collect();
            let e = encode(&raw, false);
            assert_eq!(e.len() % 4, 0, "{n}");
            assert!(data_block(&e, false).is_none(), "{n}");
            assert_eq!(dec(&e, false), raw, "{n}");
            let u = encode(&raw, true);
            assert!(!u.contains(&b'+') && !u.contains(&b'/'), "{n}");
            assert_eq!(dec(&u, true), raw, "{n}");
        }
    }

    /// `route::BASE64_SERVED` is read by the CORE, which has none of this file
    /// compiled in — so the one thing that can keep it honest is this: the
    /// variant that DOES have `module_attr` holds the two lists to each other.
    #[test]
    fn the_route_table_names_exactly_what_is_served() {
        let table = crate::route::MODULE_ATTRS
            .iter()
            .find(|(m, _)| *m == "base64")
            .expect("route::MODULE_ATTRS has no base64 row")
            .1;
        for name in table {
            assert!(module_attr(name).is_ok(), "route claims base64.{name} and base64.rs refuses it");
        }
        for name in SERVED {
            assert!(table.contains(name), "base64.rs serves base64.{name} and route::MODULE_ATTRS omits it");
        }
        assert!(table.windows(2).all(|w| w[0] < w[1]), "route::MODULE_ATTRS base64 row is unsorted");
    }
}
